"""Preemptive memory guard for the SSVI surface endpoint (Sprint 31).

WHY THIS EXISTS
    Render SIGKILLs a container that exceeds its memory limit. SIGKILL is not
    catchable — by the time the process would "handle" an OOM it is already
    gone, and the caller sees a dropped connection rather than a typed error.
    So the guard has to be *preemptive*: refuse the request before the heavy
    imports and the option-chain fetch, while we still control the response.

    Second line of defence: ``apply_address_space_limit`` sets RLIMIT_AS below
    the container limit, which converts an overrun into a catchable
    ``MemoryError`` instead of a SIGKILL. The endpoint turns that into a 503.

LIMIT DISCOVERY (first tier that yields a number wins)
    1. cgroup v2   /sys/fs/cgroup/memory.max, memory.current
    2. cgroup v1   /sys/fs/cgroup/memory/memory.limit_in_bytes, .usage_in_bytes
    3. psutil      (only if installed — it is an optional dependency)
    4. /proc/meminfo

    Tier 4 is an addition to the sprint's stated chain. It earns its place:
    neither cgroup hierarchy is mounted under WSL2 (the dev environment) and
    psutil is not guaranteed present, so without it the guard is unmeasurable
    on the very machine the verify script runs on — and an unmeasurable guard
    silently degrades to no guard at all.

    Every reader is total: unreadable, absent, or malformed paths yield None
    rather than an exception. A guard that crashes the request it was meant to
    protect is worse than no guard.

FAIL-OPEN, DELIBERATELY
    When no tier yields a limit we cannot prove there is *insufficient* memory,
    only that we do not know. ``assert_headroom`` therefore does not raise on an
    unknown limit — it proceeds and leaves the RLIMIT_AS/``MemoryError`` path to
    catch a genuine overrun. Failing closed here would take the endpoint down
    permanently on any host whose accounting we cannot read.
"""
from __future__ import annotations

import os
import resource
from dataclasses import dataclass
from typing import Optional, Sequence

__all__ = [
    "InsufficientMemoryError",
    "MemorySnapshot",
    "DEFAULT_REQUIRED_MB",
    "read_cgroup_limit",
    "read_cgroup_usage",
    "memory_snapshot",
    "assert_headroom",
    "apply_address_space_limit",
    "peak_rss_mb",
]

# The surface calibration's own working set: numpy + scipy + pandas + yfinance
# imports, then up to `max_expiries` option chains handled one at a time.
DEFAULT_REQUIRED_MB = 400

# Fraction of the container limit at which RLIMIT_AS is pinned.
RLIMIT_AS_FRACTION = 0.85

CGROUP_V2_LIMIT_PATHS = ("/sys/fs/cgroup/memory.max",)
CGROUP_V2_USAGE_PATHS = ("/sys/fs/cgroup/memory.current",)
CGROUP_V1_LIMIT_PATHS = ("/sys/fs/cgroup/memory/memory.limit_in_bytes",)
CGROUP_V1_USAGE_PATHS = ("/sys/fs/cgroup/memory/memory.usage_in_bytes",)

PROC_MEMINFO = "/proc/meminfo"

# cgroup v1 reports "no limit" as a page-counter saturation value rather than a
# sentinel string. Anything at or above this is "unlimited", not a real cap.
_V1_UNLIMITED_FLOOR = 1 << 62

_MB = 1024 * 1024


class InsufficientMemoryError(RuntimeError):
    """Not enough free memory to safely attempt a surface calibration."""


@dataclass(frozen=True)
class MemorySnapshot:
    """A best-effort view of the container's memory position.

    ``limit_bytes`` is None when the host imposes no discoverable cap (an
    uncontainerised dev box). ``source`` names the tier that answered, which is
    what makes the completion log useful for sizing the Render instance.
    """

    limit_bytes: Optional[int]
    used_bytes: Optional[int]
    available_bytes: Optional[int]
    source: str

    @property
    def available_mb(self) -> Optional[float]:
        if self.available_bytes is None:
            return None
        return self.available_bytes / _MB

    @property
    def limit_mb(self) -> Optional[float]:
        if self.limit_bytes is None:
            return None
        return self.limit_bytes / _MB

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "limit_mb": round(self.limit_mb, 1) if self.limit_mb is not None else None,
            "used_mb": (
                round(self.used_bytes / _MB, 1) if self.used_bytes is not None else None
            ),
            "available_mb": (
                round(self.available_mb, 1) if self.available_mb is not None else None
            ),
        }


# ---------------------------------------------------------------------------
# Total readers — these never raise
# ---------------------------------------------------------------------------
def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return None


def _read_int(path: str) -> Optional[int]:
    raw = _read_text(path)
    if raw is None:
        return None
    # cgroup v2 writes the literal "max" when the group is uncapped.
    if raw == "max":
        return None
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        return None


def read_cgroup_limit(paths: Optional[Sequence[str]] = None) -> Optional[int]:
    """Container memory limit in bytes, or None if not discoverable.

    Tries cgroup v2 then v1. Returns None — never raises — when the paths are
    absent, unreadable, or report "unlimited".
    """
    candidates = (
        tuple(paths)
        if paths is not None
        else CGROUP_V2_LIMIT_PATHS + CGROUP_V1_LIMIT_PATHS
    )
    for path in candidates:
        value = _read_int(path)
        if value is None or value <= 0 or value >= _V1_UNLIMITED_FLOOR:
            continue
        return value
    return None


def read_cgroup_usage(paths: Optional[Sequence[str]] = None) -> Optional[int]:
    """Current cgroup memory usage in bytes, or None if not discoverable."""
    candidates = (
        tuple(paths)
        if paths is not None
        else CGROUP_V2_USAGE_PATHS + CGROUP_V1_USAGE_PATHS
    )
    for path in candidates:
        value = _read_int(path)
        if value is None or value < 0:
            continue
        return value
    return None


def _psutil_snapshot() -> Optional[MemorySnapshot]:
    try:
        import psutil  # optional dependency
    except Exception:
        return None
    try:
        vm = psutil.virtual_memory()
    except Exception:
        return None
    return MemorySnapshot(
        limit_bytes=int(vm.total),
        used_bytes=int(vm.total - vm.available),
        available_bytes=int(vm.available),
        source="psutil",
    )


def _meminfo_snapshot(path: str = PROC_MEMINFO) -> Optional[MemorySnapshot]:
    raw = _read_text(path)
    if not raw:
        return None
    fields = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            fields[key.strip()] = int(parts[0]) * 1024  # values are in kB
        except ValueError:
            continue
    total = fields.get("MemTotal")
    # MemAvailable already accounts for reclaimable page cache; MemFree alone
    # badly understates what a new allocation can actually get.
    avail = fields.get("MemAvailable", fields.get("MemFree"))
    if total is None or avail is None:
        return None
    return MemorySnapshot(
        limit_bytes=total,
        used_bytes=max(total - avail, 0),
        available_bytes=avail,
        source="meminfo",
    )


def memory_snapshot() -> Optional[MemorySnapshot]:
    """Best-effort memory position, or None when no tier can answer.

    Returning None is a real outcome, not an error: it means "not measured".
    Callers must not read it as "plenty of memory".
    """
    limit = read_cgroup_limit()
    if limit is not None:
        used = read_cgroup_usage()
        available = max(limit - used, 0) if used is not None else None
        source = (
            "cgroup_v2"
            if _read_int(CGROUP_V2_LIMIT_PATHS[0]) is not None
            else "cgroup_v1"
        )
        return MemorySnapshot(
            limit_bytes=limit,
            used_bytes=used,
            available_bytes=available,
            source=source,
        )

    return _psutil_snapshot() or _meminfo_snapshot()


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def assert_headroom(required_mb: int = DEFAULT_REQUIRED_MB) -> Optional[MemorySnapshot]:
    """Raise ``InsufficientMemoryError`` if free memory is below ``required_mb``.

    Call this BEFORE importing numpy/scipy/pandas/yfinance and before any
    network fetch — that ordering is the whole point of the guard.

    Returns the snapshot it judged on (None when memory could not be measured,
    in which case it deliberately does not raise — see the module docstring).
    """
    snapshot = memory_snapshot()
    if snapshot is None or snapshot.available_bytes is None:
        return snapshot

    available_mb = snapshot.available_bytes / _MB
    if available_mb < required_mb:
        where = (
            f"limit {snapshot.limit_mb:.0f}MB via {snapshot.source}"
            if snapshot.limit_mb is not None
            else f"via {snapshot.source}"
        )
        raise InsufficientMemoryError(
            f"insufficient memory headroom: {available_mb:.0f}MB available, "
            f"{required_mb}MB required ({where})"
        )
    return snapshot


def apply_address_space_limit(
    fraction: float = RLIMIT_AS_FRACTION,
) -> Optional[int]:
    """Pin RLIMIT_AS at ``fraction`` of the container limit.

    Converts an overrun into a catchable ``MemoryError`` rather than a SIGKILL.
    Returns the byte value applied, or None if no limit was applied.

    Only applied when a genuine CONTAINER limit is discoverable. On a host with
    no cgroup cap the "limit" is the whole machine's RAM, and capping our
    address space at 85% of that is both meaningless and risky: RLIMIT_AS bounds
    *virtual* address space, and numpy/BLAS reserve large arenas they never
    fault in, so an over-eager cap makes `import scipy` fail rather than making
    the service safe. Never lowers an existing tighter limit.
    """
    limit = read_cgroup_limit()
    if limit is None:
        return None

    target = int(limit * fraction)
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    except (OSError, ValueError):
        return None

    if soft != resource.RLIM_INFINITY and soft <= target:
        return None  # already at least as strict
    if hard != resource.RLIM_INFINITY and target > hard:
        target = hard

    try:
        resource.setrlimit(resource.RLIMIT_AS, (target, hard))
    except (OSError, ValueError):
        return None
    return target


def peak_rss_mb() -> Optional[float]:
    """Peak resident set size for this process, in MB.

    Logged at request completion so the Render instance gets sized from
    measurement rather than guesswork (sprint Part 4, step 3).
    """
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except (OSError, ValueError):
        return None
    # ru_maxrss is kB on Linux, bytes on macOS.
    raw = float(usage.ru_maxrss)
    return raw / 1024.0 if os.uname().sysname == "Linux" else raw / _MB
