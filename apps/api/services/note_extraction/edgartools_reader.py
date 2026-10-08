"""Cascade step (b): EdgarTools' 424B parser on the STORED HTML.

EdgarTools (``edgartools``, PINNED in requirements.txt) recognises structured-
note pricing supplements and reads their key-terms table
(``Prospectus424B.structured_note_terms``). Its normal entry point,
``Prospectus424B.from_filing(filing)``, wants an EdgarTools ``Filing`` and will
fetch from the SEC. We never give it one: the stored HTML goes straight into
``edgar.documents.parse_html`` and the same two functions ``structured_note_
terms`` and ``pricing`` call internally (``classify_tables_in_document`` +
``extract_structured_note_terms`` / ``extract_pricing_data``).

ZERO SEC REQUESTS is enforced, not hoped for: the whole call — including the
library import — runs inside ``network_blocked()``: ``socket.socket.connect``,
``connect_ex``, ``socket.getaddrinfo`` and ``socket.create_connection`` record
and refuse any attempt from that thread or from EdgarTools code anywhere. Every reading records how many attempts were blocked;
a non-zero count is reported, and the parse still cannot reach the network.

Its field mapping is NOISY (measured in Task 1d: ``issue_date`` picked up a
call-settlement sentence on a real Barclays filing). It is one more reading
with provenance — the harness measures its per-field accuracy — never truth.
"""
from __future__ import annotations

import contextlib
import re
import socket
import threading
from dataclasses import dataclass, field

from services.note_extraction.schema import dates_in

EDGARTOOLS_DIST = "edgartools"
EDGARTOOLS_PINNED_VERSION = "5.59.1"
EDGARTOOLS_LICENSE = "MIT"


class NetworkBlocked(OSError):
    pass


# Blocking is SCOPED, not process-wide: the cascade runs Model 1 / Model 2 /
# R2 traffic concurrently on other threads and the event loop, and a global
# block would break them. A connection attempt is refused when it comes from a
# thread inside ``network_blocked()`` OR from any frame running EdgarTools
# code (``edgar`` package) — so a worker thread EdgarTools might spawn is
# caught too.
_STATE_LOCK = threading.Lock()
_BLOCKED_THREADS: dict[int, list] = {}
_PATCHED = False
_ORIG: dict = {}


def _edgar_on_stack() -> bool:
    import sys
    f = sys._getframe(2)
    while f is not None:
        mod = f.f_globals.get("__name__", "")
        if mod == "edgar" or mod.startswith("edgar."):
            return True
        f = f.f_back
    return False


def _guard(kind: str, target) -> None:
    tid = threading.get_ident()
    attempts = _BLOCKED_THREADS.get(tid)
    if attempts is None and _edgar_on_stack():
        attempts = next(iter(_BLOCKED_THREADS.values()), None)
        if attempts is None:
            attempts = _ORPHAN_ATTEMPTS
    if attempts is not None:
        attempts.append((kind, str(target)))
        raise NetworkBlocked(f"network blocked during EdgarTools parse: {kind} {target}")


_ORPHAN_ATTEMPTS: list = []


def _install() -> None:
    global _PATCHED
    with _STATE_LOCK:
        if _PATCHED:
            return
        _ORIG["connect"] = socket.socket.connect
        _ORIG["connect_ex"] = socket.socket.connect_ex
        _ORIG["getaddrinfo"] = socket.getaddrinfo
        _ORIG["create_connection"] = socket.create_connection

        def _connect(self, address, *a, **k):
            _guard("connect", address)
            return _ORIG["connect"](self, address, *a, **k)

        def _connect_ex(self, address, *a, **k):
            _guard("connect_ex", address)
            return _ORIG["connect_ex"](self, address, *a, **k)

        def _gai(host, *a, **k):
            _guard("dns", host)
            return _ORIG["getaddrinfo"](host, *a, **k)

        def _cc(address, *a, **k):
            _guard("create_connection", address)
            return _ORIG["create_connection"](address, *a, **k)

        socket.socket.connect = _connect
        socket.socket.connect_ex = _connect_ex
        socket.getaddrinfo = _gai
        socket.create_connection = _cc
        _PATCHED = True


@contextlib.contextmanager
def network_blocked(attempts: list):
    """Refuse every outbound connection and DNS lookup made by THIS thread (or
    by EdgarTools code anywhere) for the duration; record each attempt."""
    _install()
    tid = threading.get_ident()
    _BLOCKED_THREADS[tid] = attempts
    try:
        yield attempts
    finally:
        _BLOCKED_THREADS.pop(tid, None)


def installed_version() -> str | None:
    try:
        from importlib.metadata import version
        return version(EDGARTOOLS_DIST)
    except Exception:  # noqa: BLE001
        return None


def installed_license() -> str | None:
    try:
        from importlib.metadata import metadata
        md = metadata(EDGARTOOLS_DIST)
        return md.get("License-Expression") or md.get("License")
    except Exception:  # noqa: BLE001
        return None


@dataclass
class EdgarToolsResult:
    ok: bool
    fields: dict[str, dict] = field(default_factory=dict)   # key -> {value, quote}
    raw_terms: dict | None = None
    tables_found: dict[str, int] = field(default_factory=dict)
    network_attempts: list = field(default_factory=list)
    version: str | None = None
    license: str | None = None
    error: str | None = None


_PCT_OF_INITIAL = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%\s+of\s+the\s+(?:initial|starting|strike)", re.IGNORECASE)
_CUSIP = re.compile(r"(?<![0-9A-Z])([0-9][0-9A-Z]{7}[0-9])(?![0-9A-Z])")
_MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")

# additional_terms label -> our field, for percent-of-initial values.
_ADDITIONAL_PCT = (
    (re.compile(r"^coupon\s+barrier", re.I), "coupon_barrier_pct"),
    (re.compile(r"^(?:call|autocall|automatic\s+call)\s+(?:value|level|barrier|threshold)", re.I), "autocall_level_pct"),
    (re.compile(r"^(?:barrier|downside\s+threshold|trigger|knock-?in)\s*(?:value|level)?", re.I), "barrier_pct"),
    (re.compile(r"^buffer\s*(?:amount|percentage|level)?", re.I), "buffer_pct"),
)


def _num(s: str | None):
    if not s:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        f = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def map_terms(terms: dict) -> dict[str, dict]:
    """StructuredNoteTerms (as a dict) -> {field_key: {value, quote}}. Only
    values that parse cleanly are mapped; the quote is the cell text."""
    out: dict[str, dict] = {}

    def put(key, value, quote):
        if value is not None and key not in out:
            out[key] = {"value": value, "quote": (quote or "")[:500]}

    cusip = terms.get("cusip")
    if cusip:
        m = _CUSIP.search(cusip)
        if m:
            from services.edgar_pipeline import is_valid_cusip
            if is_valid_cusip(m.group(1)):
                put("cusip", m.group(1), cusip)
    for src, key in (("pricing_date", "pricing_date"), ("maturity_date", "maturity_date")):
        v = terms.get(src)
        found = sorted(dates_in(v or ""))
        if len(found) == 1:
            put(key, found[0], v)
    denoms = terms.get("denominations")
    if denoms:
        m = _MONEY.search(denoms)
        if m:
            put("denomination", _num(m.group(1)), denoms)
    und = terms.get("underlying")
    if und and len(und) < 300:
        put("underlyings", [{"name": und.strip()}], und)
    buf = terms.get("buffer_amount")
    if buf and "%" in buf:
        put("buffer_pct", _num(buf), buf)
    thr = terms.get("threshold_value")
    if thr:
        m = _PCT_OF_INITIAL.search(thr)
        if m:
            put("barrier_pct", _num(m.group(1)), thr)
    cap = terms.get("max_return")
    if cap and "%" in cap:
        put("cap_pct", _num(cap), cap)
    part = terms.get("upside_participation_rate")
    if part and "%" in part:
        put("participation_rate", _num(part), part)
    for label, value in (terms.get("additional_terms") or {}).items():
        if not isinstance(value, str):
            continue
        clean = label.strip().rstrip(":*").strip()
        for rx, key in _ADDITIONAL_PCT:
            if rx.match(clean):
                m = _PCT_OF_INITIAL.search(value)
                if m:
                    put(key, _num(m.group(1)), value)
                break
    return out


def read_html(html: str) -> EdgarToolsResult:
    """Parse the stored HTML with EdgarTools, network blocked throughout."""
    attempts: list = []
    res = EdgarToolsResult(ok=False, version=installed_version(), license=installed_license())
    try:
        with network_blocked(attempts):
            from edgar.documents import parse_html
            from edgar.offerings.prospectus._424b_tables import (
                classify_tables_in_document,
                extract_structured_note_terms,
            )
            doc = parse_html(html)
            tables = classify_tables_in_document(doc) if doc else {}
            res.tables_found = {k: len(v) for k, v in tables.items()}
            key_tables = tables.get("key_terms", [])
            merged: dict = {}
            for t in key_tables:
                terms = extract_structured_note_terms(t).model_dump()
                for k, v in terms.items():
                    if k == "additional_terms":
                        merged.setdefault("additional_terms", {})
                        for ak, av in (v or {}).items():
                            merged["additional_terms"].setdefault(ak, av)
                    elif merged.get(k) is None and v is not None:
                        merged[k] = v
            res.raw_terms = merged or None
            res.fields = map_terms(merged) if merged else {}
            res.ok = True
    except NetworkBlocked as exc:
        res.error = f"EdgarTools attempted network access: {exc}"
    except Exception as exc:  # noqa: BLE001 — a library failure is a failed reading
        res.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    res.network_attempts = attempts
    return res
