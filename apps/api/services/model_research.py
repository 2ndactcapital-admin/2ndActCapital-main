"""modelresearch.structural — the read-only Model Research grid.

Every model LiteLLM knows about, one row each, flagged against what this
platform actually runs. Strictly READ-ONLY: nothing here writes to the
database or to the proxy. Reloading the proxy's own price list is a separate,
existing action (``litellm.reload_model_cost_map``) and is deliberately not
reachable from here.

SOURCE (Task 1a, probed live on the pinned v1.96.2 proxy):

  * ``GET /public/litellm_model_cost_map`` returns the proxy's FULL loaded
    price list — 4,455 keys on 2026-10-01, not the ~2,000 the prompt assumed.
    It is the PRIMARY source. ``/model/info``, ``/v2/model/info``,
    ``/model_group/info`` and ``/v1/models`` return only the 3 registered
    deployments; ``/public/model_hub`` returns ``[]``.
  * ``GET /model/cost_map/source`` reports where the proxy loaded that list
    from (``remote`` = GitHub main) and its key count.
  * ``GET /schedule/model_cost_map_reload/status`` reports the last scheduled
    reload. No reload has ever been scheduled (``last_run: null``), so the
    loaded list dates from the proxy's last start, which the proxy does not
    report. ``prices_as_of`` therefore says exactly that rather than
    inventing a date.

  If the proxy cannot return the list, the published
  ``model_prices_and_context_window.json`` is fetched from GitHub
  SERVER-SIDE (never the browser) and the source label says so; its
  ``prices_as_of`` is the file's last commit date when GitHub reports one.

WHAT IS NOT A MODEL: the ``sample_spec`` documentation key, and any entry
without a string ``litellm_provider``. The proxy injects one such entry per
registered deployment, keyed by the deployment's UUID and holding only
``{id, db_model, blocked}``. These are excluded and counted, never rendered.

NO SECRETS LEAVE THE SERVER (decision 4). ``/model/info`` carries
``litellm_params``, which may hold ``api_base`` and credential references.
Every row is built field-by-field from ``_PRICE_FIELDS`` / ``_FLAG_FIELDS``
and a handful of named scalars. No provider dict is ever passed through, and
from ``/model/info`` only ``model_name``, ``model_info.key``,
``model_info.mode`` and ``litellm_params.model`` (the route string, used for
matching only and never returned) are read.

FLAGS:
  * live_on_proxy — the row a registered deployment resolves to:
    ``model_info.key``, else ``litellm_params.model``, else that route with
    its ``provider/`` prefix stripped. A deployment that resolves to no
    price-list key gets a row of its own, so every registration is shown
    and the flag can never be silently dropped.
  * in_catalog — ``platform_model_catalog.model_id`` is a proxy ALIAS
    (``claude-sonnet``), not a price-list key, so it resolves through the
    deployment of that name. Failing that, a model_id equal to a price-list
    key matches directly. Failing both, the catalog entry gets a row of its
    own. Each catalog entry lands on exactly one row.
  * System One — each ``ai_system_one_models`` row is its own row, kind
    'System One' (LiteLLM's price list has no entry for Jev).

CACHE: the two proxy reads (price list + /model/info) are cached in-process
for ``CACHE_TTL_SECONDS``. ``refresh=True`` bypasses and refills it. The two
catalog tables are read fresh on every request, so their flags are never stale.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
import weakref
from datetime import datetime, timezone

from services import litellm_credentials

CACHE_TTL_SECONDS = 3600

PUBLISHED_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
PUBLISHED_COMMITS_URL = (
    "https://api.github.com/repos/BerriAI/litellm/commits"
    "?path=model_prices_and_context_window.json&per_page=1"
)

SOURCE_PROXY = "proxy"
SOURCE_PUBLISHED = "published"
SOURCE_LABELS = {
    SOURCE_PROXY: "LiteLLM proxy (v1.96.2) — its loaded price list",
    SOURCE_PUBLISHED: "LiteLLM published price list (GitHub, BerriAI/litellm main) — fetched by the server",
}

KIND_LLM = "LLM"
KIND_EMBEDDING = "Embedding"
KIND_SYSTEM_ONE = "System One"
KIND_OTHER = "Other"
_LLM_MODES = {"chat", "completion", "responses"}

# (row field, price-list field). Per-token prices become per-1M on the row.
_PRICE_FIELDS = (
    ("input_per_1m", "input_cost_per_token"),
    ("output_per_1m", "output_cost_per_token"),
    ("batch_input_per_1m", "input_cost_per_token_batches"),
    ("batch_output_per_1m", "output_cost_per_token_batches"),
    ("cached_input_per_1m", "cache_read_input_token_cost"),
)
# (row field, price-list field, label). The label travels in the envelope's
# vocabularies so the page never hardcodes it (Rule 1).
_FLAG_FIELDS = (
    ("function_calling", "supports_function_calling", "Function calling"),
    ("structured_output", "supports_response_schema", "Structured output"),
    ("vision", "supports_vision", "Vision"),
    ("reasoning", "supports_reasoning", "Reasoning"),
    ("prompt_caching", "supports_prompt_caching", "Prompt caching"),
    ("web_search", "supports_web_search", "Web search"),
)

# A dated suffix: -20250807 / -2025-08-07 / @20240620, optionally followed by
# a Bedrock-style -v1 or -v1:0. Anything else (gpt-4-0613's MMDD, a size,
# a semver) is NOT a date and leaves version blank.
_DATED_SUFFIX = re.compile(
    r"[-@](?P<d>\d{8}|\d{4}-\d{2}-\d{2})(?:-v\d+(?::\d+)?)?$"
)

_cache: dict | None = None
# One lock per event loop: an asyncio.Lock must not be shared across loops
# (each TestClient, and each worker, runs its own).
_locks: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


class ModelResearchSourceError(RuntimeError):
    """Neither the proxy nor the published list could be read."""


# ═══════════════════════════════════════════════════════════════════════════
# Pure helpers
# ═══════════════════════════════════════════════════════════════════════════
def parse_version(model_name: str) -> str | None:
    """ISO date of a dated suffix, or None. Never invents: the date must be a
    real calendar date or the result is None."""
    m = _DATED_SUFFIX.search(model_name or "")
    if not m:
        return None
    raw = m.group("d").replace("-", "")
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _num(value):
    """A real number, or None. bool is excluded (it is an int subclass)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _int(value):
    v = _num(value)
    return int(v) if v is not None else None


def _flag(value):
    return value if isinstance(value, bool) else None


def _str(value):
    return value if isinstance(value, str) and value else None


def kind_for_mode(mode: str | None) -> str:
    if mode in _LLM_MODES:
        return KIND_LLM
    if mode == "embedding":
        return KIND_EMBEDDING
    return KIND_OTHER


def is_model_entry(key: str, value) -> bool:
    """The source's own definition of "a model" — shared with the verify."""
    return (
        key != "sample_spec"
        and isinstance(value, dict)
        and isinstance(value.get("litellm_provider"), str)
        and bool(value.get("litellm_provider"))
    )


def _empty_row(row_id: str, *, model: str, provider: str | None, source: str) -> dict:
    row = {
        "id": row_id,
        "model": model,
        "display_name": None,
        "provider": provider,
        "version": None,
        "mode": None,
        "kind": KIND_OTHER,
        "max_input_tokens": None,
        "max_output_tokens": None,
        "deprecation_date": None,
        "live_on_proxy": False,
        "proxy_aliases": [],
        "in_catalog": False,
        "catalog_model_ids": [],
        "catalog_availability": None,
        "system_one_availability": None,
        "row_source": source,
    }
    for field, _ in _PRICE_FIELDS:
        row[field] = None
    for field, _, _ in _FLAG_FIELDS:
        row[field] = None
    return row


def build_price_row(key: str, entry: dict, source: str) -> dict:
    """One row from one price-list entry — explicit allow-list only."""
    row = _empty_row(f"{source}:{key}", model=key, provider=entry["litellm_provider"], source=source)
    mode = _str(entry.get("mode"))
    row["mode"] = mode
    row["kind"] = kind_for_mode(mode)
    row["version"] = parse_version(key)
    row["max_input_tokens"] = _int(entry.get("max_input_tokens"))
    row["max_output_tokens"] = _int(entry.get("max_output_tokens"))
    row["deprecation_date"] = _str(entry.get("deprecation_date"))
    for field, src in _PRICE_FIELDS:
        per_token = _num(entry.get(src))
        row[field] = per_token * 1_000_000 if per_token is not None else None
    for field, src, _ in _FLAG_FIELDS:
        row[field] = _flag(entry.get(src))
    return row


def resolve_deployment_key(deployment: dict, price_keys) -> str | None:
    """Which price-list key a registered deployment is."""
    info = deployment.get("model_info") or {}
    route = (deployment.get("litellm_params") or {}).get("model")
    candidates = [info.get("key"), route]
    if isinstance(route, str) and "/" in route:
        candidates.append(route.split("/", 1)[1])
    for c in candidates:
        if isinstance(c, str) and c in price_keys:
            return c
    return None


def assemble_rows(price_map: dict, source: str, deployments: list, catalog: list,
                  system_one: list) -> tuple[list[dict], dict]:
    """Pure: the full row set plus counts. ``catalog`` rows carry
    model_id/display_name/provider/availability; ``system_one`` rows carry
    key/display_name/provider/model_route/model_version/availability."""
    rows: dict[str, dict] = {}
    excluded = 0
    for key, entry in price_map.items():
        if is_model_entry(key, entry):
            rows[key] = build_price_row(key, entry, source)
        elif key != "sample_spec":
            excluded += 1
    source_count = len(rows)

    # Live on our proxy.
    alias_to_rowkey: dict[str, str] = {}
    unmatched_deployments = 0
    for dep in deployments:
        alias = dep.get("model_name")
        if not isinstance(alias, str) or not alias:
            continue
        rowkey = resolve_deployment_key(dep, rows)
        if rowkey is None:
            rowkey = f"proxy:{alias}"
            if rowkey not in rows:
                mode = _str((dep.get("model_info") or {}).get("mode"))
                r = _empty_row(rowkey, model=alias, provider=None, source="proxy_registration")
                r["mode"] = mode
                r["kind"] = kind_for_mode(mode)
                rows[rowkey] = r
                unmatched_deployments += 1
        row = rows[rowkey]
        row["live_on_proxy"] = True
        if alias not in row["proxy_aliases"]:
            row["proxy_aliases"].append(alias)
        alias_to_rowkey[alias] = rowkey

    # In platform catalog.
    unmatched_catalog = 0
    for c in catalog:
        mid = c["model_id"]
        rowkey = alias_to_rowkey.get(mid) or (mid if mid in rows and not mid.startswith("proxy:") else None)
        if rowkey is None:
            rowkey = f"catalog:{mid}"
            r = _empty_row(rowkey, model=mid, provider=c.get("provider"), source="platform_catalog")
            r["display_name"] = c.get("display_name")
            rows[rowkey] = r
            unmatched_catalog += 1
        row = rows[rowkey]
        row["in_catalog"] = True
        row["catalog_model_ids"].append(mid)
        avail = row["catalog_availability"]
        row["catalog_availability"] = (
            c["availability"] if avail is None or avail == c["availability"]
            else f"{avail}, {c['availability']}"
        )
        if row["display_name"] is None:
            row["display_name"] = c.get("display_name")

    # System One.
    for s in system_one:
        r = _empty_row(f"system_one:{s['key']}", model=s["key"], provider=s.get("provider"),
                       source="system_one_catalog")
        r["display_name"] = s.get("display_name")
        r["kind"] = KIND_SYSTEM_ONE
        r["version"] = _str(s.get("model_version"))
        r["system_one_availability"] = s.get("availability")
        rows[r["id"]] = r

    counts = {
        "source_models": source_count,
        "source_non_model_entries_excluded": excluded,
        "proxy_deployments": len([d for d in deployments if d.get("model_name")]),
        "proxy_deployments_without_price_entry": unmatched_deployments,
        "catalog_entries": len(catalog),
        "catalog_entries_without_price_entry": unmatched_catalog,
        "system_one_models": len(system_one),
        "rows": len(rows),
    }
    return list(rows.values()), counts


# ═══════════════════════════════════════════════════════════════════════════
# Source reads (blocking; run off the event loop)
# ═══════════════════════════════════════════════════════════════════════════
def _proxy_get(path: str, timeout: float = 60.0) -> tuple[int, str]:
    """GET on the proxy's admin API with the master key. Server-side only."""
    return litellm_credentials._http(path, timeout=timeout)


def _proxy_json(path: str):
    status, body = _proxy_get(path)
    if status != 200:
        raise ModelResearchSourceError(f"proxy GET {path} -> HTTP {status}")
    return json.loads(body)


def _published_get(url: str, timeout: float = 60.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "hollisworks-model-research"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _published_commit_date() -> str | None:
    try:
        commits = _published_get(PUBLISHED_COMMITS_URL, timeout=20)
        return commits[0]["commit"]["committer"]["date"]
    except Exception:  # noqa: BLE001 — best effort; prices_as_of falls back
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_sources() -> dict:
    """Read the price list (proxy first) and the registered deployments."""
    fetched_at = _now_iso()
    proxy_error = None
    price_map = None
    source = SOURCE_PROXY
    source_detail: dict = {}
    try:
        price_map = _proxy_json("/public/litellm_model_cost_map")
        if not isinstance(price_map, dict) or not price_map:
            raise ModelResearchSourceError("proxy returned an empty price list")
        try:
            src = _proxy_json("/model/cost_map/source")
            source_detail = {
                "loaded_from": _str(src.get("source")),
                "loaded_from_url": _str(src.get("url")),
                "fallback_reason": _str(src.get("fallback_reason")),
            }
        except Exception:  # noqa: BLE001
            source_detail = {}
        try:
            sched = _proxy_json("/schedule/model_cost_map_reload/status")
            last_reload = _str(sched.get("last_run"))
        except Exception:  # noqa: BLE001
            last_reload = None
        if last_reload:
            prices_as_of = last_reload
            prices_as_of_note = "The proxy's last scheduled price-list reload."
        else:
            prices_as_of = None
            prices_as_of_note = (
                "Loaded by the proxy when it last started. LiteLLM v1.96.2 does not "
                "report that time, and no scheduled reload has run."
            )
    except Exception as exc:  # noqa: BLE001
        proxy_error = str(exc)[:200]
        price_map = None

    if price_map is None:
        try:
            price_map = _published_get(PUBLISHED_URL)
        except Exception as exc:  # noqa: BLE001
            raise ModelResearchSourceError(
                f"Neither source could be read. Proxy: {proxy_error}. "
                f"Published list: {type(exc).__name__}."
            ) from exc
        source = SOURCE_PUBLISHED
        commit_date = _published_commit_date()
        prices_as_of = commit_date
        prices_as_of_note = (
            "Last commit to the published file." if commit_date
            else "GitHub did not report the file's last commit date."
        )
        source_detail = {"loaded_from_url": PUBLISHED_URL, "proxy_error": proxy_error}

    # Registered deployments: model_name / model_info.key / model_info.mode /
    # litellm_params.model ONLY. Nothing else is copied out of the response.
    deployments = []
    deployments_error = None
    try:
        for d in _proxy_json("/model/info").get("data", []):
            info = d.get("model_info") or {}
            params = d.get("litellm_params") or {}
            deployments.append({
                "model_name": _str(d.get("model_name")),
                "model_info": {"key": _str(info.get("key")), "mode": _str(info.get("mode"))},
                "litellm_params": {"model": _str(params.get("model"))},
            })
    except Exception as exc:  # noqa: BLE001
        deployments_error = f"/model/info unavailable: {str(exc)[:160]}"

    return {
        "price_map": price_map,
        "deployments": deployments,
        "deployments_error": deployments_error,
        "source": source,
        "source_detail": source_detail,
        "prices_as_of": prices_as_of,
        "prices_as_of_note": prices_as_of_note,
        "last_refreshed": fetched_at,
    }


async def get_sources(refresh: bool = False) -> tuple[dict, str]:
    """Cached source read. Returns (sources, cache_status) where
    cache_status is 'hit', 'miss' or 'bypassed'."""
    global _cache
    async with _lock():
        if not refresh and _cache is not None and _cache["expires"] > time.monotonic():
            return _cache["sources"], "hit"
        status = "bypassed" if refresh else "miss"
        loop = asyncio.get_running_loop()
        sources = await loop.run_in_executor(None, fetch_sources)
        _cache = {"sources": sources, "expires": time.monotonic() + CACHE_TTL_SECONDS}
        return sources, status


# ═══════════════════════════════════════════════════════════════════════════
# The one read
# ═══════════════════════════════════════════════════════════════════════════
async def read_catalogs(conn) -> tuple[list[dict], list[dict]]:
    """Both catalog tables, on a connection the caller has already put under
    RLS context (``get_pool().acquire()`` applies it). SELECT only."""
    catalog = [dict(r) for r in await conn.fetch(
        "SELECT model_id, display_name, provider, availability "
        "FROM public.platform_model_catalog ORDER BY model_id"
    )]
    system_one = [dict(r) for r in await conn.fetch(
        "SELECT key, display_name, provider, model_route, model_version, availability "
        "FROM public.ai_system_one_models ORDER BY key"
    )]
    return catalog, system_one


def vocabularies() -> dict:
    return {
        "kinds": [KIND_LLM, KIND_EMBEDDING, KIND_SYSTEM_ONE, KIND_OTHER],
        "capability_flags": [{"field": f, "label": label} for f, _, label in _FLAG_FIELDS],
        "catalog_availability": ["available", "deprecated", "disabled"],
        "default_token_profile": {"input_tokens": 4000, "output_tokens": 600},
        # A read-only page publishes empty write vocabularies, never omitted.
        "editable": [],
        "inline_editable": [],
    }


def compact_row(row: dict) -> dict:
    """Drop null and empty-list fields (4,400+ rows of mostly-null price
    columns is ~3.2 MB; compacted ~1.6 MB, under Vercel's response limit).
    ``False`` is KEPT: a capability flag of false is not the same as unknown.
    A missing key on the client therefore always means null."""
    return {k: v for k, v in row.items() if v is not None and v != []}


async def build_research(conn, *, refresh: bool = False) -> dict:
    sources, cache_status = await get_sources(refresh=refresh)
    catalog, system_one = await read_catalogs(conn)
    rows, counts = assemble_rows(
        sources["price_map"], sources["source"], sources["deployments"], catalog, system_one
    )
    return {
        "rows": [compact_row(r) for r in rows],
        "meta": {
            "source": sources["source"],
            "source_label": SOURCE_LABELS[sources["source"]],
            "source_detail": sources["source_detail"],
            "prices_as_of": sources["prices_as_of"],
            "prices_as_of_note": sources["prices_as_of_note"],
            "last_refreshed": sources["last_refreshed"],
            "cache": cache_status,
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "deployments_error": sources["deployments_error"],
            "counts": counts,
            "row_encoding": "fields that are null are omitted",
        },
        "vocabularies": vocabularies(),
    }


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None


__all__ = [
    "CACHE_TTL_SECONDS", "ModelResearchSourceError", "assemble_rows", "build_research",
    "build_price_row", "get_sources", "is_model_entry", "parse_version",
    "resolve_deployment_key", "vocabularies",
]
