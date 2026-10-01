"""ensemblemodels.structural — the model catalog and versioned ensemble
selections behind the structured-note hazard ensemble.

The ensemble has three slots: Review model 1, Review model 2 (two independent
reads of the hazard fields) and the Comparison model (which judges them).
Review models must be chat LLMs; the comparison model may be a chat LLM or a
judgment model (a scorer that returns probabilities, not chat text — Jev).

``get_catalog`` is ONE list, the union of:

  * every LiteLLM model group from ``GET /model/info`` (via
    ``services.litellm_credentials.list_deployments`` — never a second
    /model/info caller) whose ``model_info.mode`` is ``'chat'``, tagged
    ``kind='llm'`` with the exact upstream model version from
    ``litellm_params.model``. Non-chat groups (embedding models such as
    voyage-3.5) are excluded outright: they can fill neither a review nor a
    comparison slot, so they are not catalog entries at all.
    Availability is ``platform_model_catalog.availability`` — the platform's
    existing three-state vocabulary (available / deprecated / disabled), never
    a second one. A group with no catalog row has NO availability (None):
    Hollisworks has not curated it, so it is not selectable.
  * every ``ai_judgment_models`` row, tagged ``kind='judgment'``.

Per-org BYOK deployments (``org-<provider>-<org_id>``, see
litellm_credentials) are excluded: they are an internal routing detail of one
org's own key, never a platform-selectable model.

``activate_ensemble`` is the only write. It mirrors the table's CHECKs
server-side, refuses anything not 'available', and retires the current active
row + inserts the new active row in ONE transaction. Rows are immutable after
creation (a BEFORE UPDATE trigger permits only retirement), so a new selection
is always a new row and history is never rewritten.

The caller supplies the RLS context: through a real request,
rls_context_middleware sets app.is_super_admin from the caller's principal; a
script must set it itself. Nothing here elevates privilege.

NOTE: services.note_terms_extraction does NOT read these selections yet — it
still resolves ai.model.default / ai.model.assistant. Wiring it is the next
sprint's job.
"""
from __future__ import annotations

import asyncio
import re

import asyncpg

# The only ensemble task that exists today. A key, not a display label.
KNOWN_TASK_KEYS = frozenset({"note_terms_hazard"})

KIND_LLM = "llm"
KIND_JUDGMENT = "judgment"
AVAILABLE = "available"

# litellm_credentials._org_deployment_name's shape — one org's own BYOK
# deployment, never a platform model.
_ORG_DEPLOYMENT_RE = re.compile(r"^org-[a-z0-9_]+-[0-9a-f-]{36}$")
_DATED_VERSION_RE = re.compile(r"-\d{8}$")


class EnsembleValidationError(ValueError):
    """The requested selection is invalid (bad input, unavailable model)."""


class EnsembleConflictError(RuntimeError):
    """A concurrent activation won the race for this task_key."""


class CatalogUnavailableError(RuntimeError):
    """LiteLLM's /model/info could not be read — no catalog can be built.
    Never papered over with a hardcoded list."""


def _upstream_version(upstream: str | None) -> str | None:
    if not upstream:
        return None
    return upstream.split("/", 1)[1] if "/" in upstream else upstream


def _llm_entries(deployments: list[dict], curated: dict[str, dict]) -> list[dict]:
    """One entry per platform model group (a group can hold several
    deployments; every deployment of a group must agree on the upstream or the
    group has no single exact version and is not selectable)."""
    groups: dict[str, list[dict]] = {}
    for d in deployments:
        name = d.get("model_name")
        if not name or _ORG_DEPLOYMENT_RE.match(name):
            continue
        groups.setdefault(name, []).append(d)

    out = []
    for name, members in sorted(groups.items()):
        # Chat models only. Every deployment of the group must say 'chat' — a
        # group mixing modes is not reliably a chat model.
        if {(m.get("model_info") or {}).get("mode") for m in members} != {"chat"}:
            continue
        versions = sorted({
            _upstream_version((m.get("litellm_params") or {}).get("model")) or ""
            for m in members
        })
        info = members[0].get("model_info") or {}
        provider = info.get("litellm_provider") or (
            ((members[0].get("litellm_params") or {}).get("model") or "").split("/", 1)[0] or None
        )
        mode = info.get("mode")
        cat = curated.get(name)
        availability = cat["availability"] if cat else None

        reason = None
        if len(versions) != 1 or not versions[0]:
            exact = None
            reason = (
                f"model group has {len(members)} deployments resolving to "
                f"different upstream versions ({versions}) — no single exact version"
            )
        else:
            exact = versions[0]
        if reason is None and cat is None:
            reason = "not on platform_model_catalog — Hollisworks has not curated it"
        if reason is None and availability != AVAILABLE:
            reason = f"platform_model_catalog availability is '{availability}'"

        out.append({
            "key": name,
            "kind": KIND_LLM,
            "display_name": cat["display_name"] if cat else name,
            "provider": provider,
            "model_version": exact,
            "version_is_dated": bool(exact and _DATED_VERSION_RE.search(exact)),
            "mode": mode,
            "availability": availability,
            "selectable": reason is None,
            "unavailable_reason": reason,
            "last_verified_at": None,
        })
    return out


def _judgment_entries(rows) -> list[dict]:
    out = []
    for r in rows:
        reason = None
        if r["availability"] != AVAILABLE:
            reason = f"availability is '{r['availability']}'"
            if r["last_verified_at"] is None:
                reason += " — no call to this model has ever succeeded"
        out.append({
            "key": r["key"],
            "kind": KIND_JUDGMENT,
            "display_name": r["display_name"],
            "provider": r["provider"],
            "model_version": r["model_version"],
            "version_is_dated": False,
            "mode": "judgment",
            "availability": r["availability"],
            "selectable": reason is None,
            "unavailable_reason": reason,
            "last_verified_at": (
                r["last_verified_at"].isoformat() if r["last_verified_at"] else None
            ),
            "credential_name": r["credential_name"],
            "notes": r["notes"],
        })
    return out


async def get_catalog(conn) -> list[dict]:
    """The union catalog. Raises CatalogUnavailableError when /model/info
    cannot be read — callers surface that, never substitute a fixed list."""
    from services.litellm_credentials import list_deployments

    try:
        deployments = await asyncio.to_thread(list_deployments)
    except Exception as exc:  # noqa: BLE001 — any failure means no catalog
        raise CatalogUnavailableError(f"LiteLLM /model/info unreadable: {exc}") from exc

    curated = {
        r["model_id"]: dict(r)
        for r in await conn.fetch(
            "SELECT model_id, display_name, provider, availability FROM platform_model_catalog"
        )
    }
    judgment_rows = await conn.fetch(
        "SELECT key, display_name, provider, model_version, credential_name, "
        "availability, last_verified_at, notes FROM ai_judgment_models ORDER BY key"
    )

    catalog = _llm_entries(deployments, curated) + _judgment_entries(judgment_rows)
    seen: set[str] = set()
    for entry in catalog:
        if entry["key"] in seen:
            # A judgment key shadowing a LiteLLM group (or vice versa) would
            # make a stored selection ambiguous. Refuse to build the catalog.
            raise CatalogUnavailableError(
                f"catalog key '{entry['key']}' is both a LiteLLM model group and "
                f"an ai_judgment_models key — rename one before selecting"
            )
        seen.add(entry["key"])
    return catalog


def _row_out(r) -> dict:
    d = dict(r)
    for k in ("id", "created_by"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("created_at", "activated_at", "retired_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def list_ensembles(conn, task_key: str) -> list[dict]:
    """Every selection for one task, newest first (the active one included)."""
    rows = await conn.fetch(
        "SELECT * FROM ai_ensemble_configs WHERE task_key = $1 "
        "ORDER BY created_at DESC, id",
        task_key,
    )
    return [_row_out(r) for r in rows]


def validate_selection(
    catalog: list[dict], task_key: str, m1: str, m2: str, comparison: str
) -> tuple[dict, dict, dict, list[str]]:
    """Server-side mirror of the table CHECKs plus the availability/kind
    rules. Returns the three catalog entries and any non-blocking warnings."""
    if task_key not in KNOWN_TASK_KEYS:
        raise EnsembleValidationError(
            f"unknown task_key '{task_key}' (known: {sorted(KNOWN_TASK_KEYS)})"
        )
    if not all(isinstance(x, str) and x for x in (m1, m2, comparison)):
        raise EnsembleValidationError("all three models are required")
    # Mirrors ai_ensemble_configs_review_models_distinct_chk.
    if m1 == m2:
        raise EnsembleValidationError(
            "Review model 1 and Review model 2 must be different models"
        )
    # Mirrors ai_ensemble_configs_comparison_distinct_chk.
    if comparison in (m1, m2):
        raise EnsembleValidationError(
            "The comparison model must differ from both review models"
        )

    by_key = {e["key"]: e for e in catalog}
    picked = []
    for slot, key in (("Review model 1", m1), ("Review model 2", m2), ("Comparison model", comparison)):
        entry = by_key.get(key)
        if entry is None:
            raise EnsembleValidationError(f"{slot}: '{key}' is not in the model catalog")
        if slot.startswith("Review") and entry["kind"] != KIND_LLM:
            raise EnsembleValidationError(
                f"{slot}: '{key}' is a {entry['kind']} model — review models must be chat LLMs"
            )
        if entry["availability"] != AVAILABLE or not entry["selectable"]:
            raise EnsembleValidationError(
                f"{slot}: '{key}' is not available — {entry['unavailable_reason']}"
            )
        picked.append(entry)

    e1, e2, ec = picked
    warnings = []
    if e1["provider"] and e1["provider"] == e2["provider"]:
        warnings.append(
            f"Both review models come from the same provider family "
            f"('{e1['provider']}'). Their errors may be correlated, which weakens "
            f"the independence the ensemble relies on."
        )
    return e1, e2, ec, warnings


async def activate_ensemble(
    conn, task_key: str, m1: str, m2: str, comparison: str, created_by, notes: str | None = None
) -> dict:
    """Validate, then retire the current active row and insert the new active
    row in ONE transaction. Returns {"ensemble": row, "retired_id", "warnings"}.

    Concurrency: the active row is locked FOR UPDATE; if two activations still
    race (e.g. no active row existed to lock), the partial unique index
    ai_ensemble_configs_one_active_per_task refuses the loser, surfaced as
    EnsembleConflictError — never two active rows.
    """
    catalog = await get_catalog(conn)
    e1, e2, ec, warnings = validate_selection(catalog, task_key, m1, m2, comparison)

    try:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT id FROM ai_ensemble_configs WHERE task_key = $1 AND is_active "
                "FOR UPDATE",
                task_key,
            )
            retired_id = None
            if current is not None:
                retired = await conn.fetchrow(
                    "UPDATE ai_ensemble_configs SET is_active = false, retired_at = now() "
                    "WHERE id = $1 RETURNING id",
                    current["id"],
                )
                if retired is None:
                    # Zero rows on a row this connection just SELECTed: RLS
                    # (no super-admin context) or a concurrent retire. This
                    # code cannot tell which, so it says both.
                    raise EnsembleConflictError(
                        "retiring the active ensemble matched zero rows — EITHER "
                        "this connection lacks app.is_super_admin (the UPDATE "
                        "policy silently matches nothing), OR another activation "
                        "retired it concurrently"
                    )
                retired_id = str(retired["id"])
            row = await conn.fetchrow(
                """
                INSERT INTO ai_ensemble_configs (
                    task_key, review_model_1, review_model_1_version,
                    review_model_2, review_model_2_version,
                    comparison_model, comparison_model_version, comparison_kind,
                    is_active, created_by, activated_at, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, true, $9, now(), $10)
                RETURNING *
                """,
                task_key,
                e1["key"], e1["model_version"],
                e2["key"], e2["model_version"],
                ec["key"], ec["model_version"], ec["kind"],
                created_by, notes,
            )
    except asyncpg.UniqueViolationError as exc:
        raise EnsembleConflictError(
            f"another ensemble for '{task_key}' was activated concurrently — reload and retry"
        ) from exc

    return {"ensemble": _row_out(row), "retired_id": retired_id, "warnings": warnings}
