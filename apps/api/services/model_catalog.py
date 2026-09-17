"""LiteLLM Phase D2 — the model pick-list. TWO TIERS, per the sprint scope:

  1. ``platform_model_catalog`` — Hollisworks curates which models are
     supportable at all. No org_id column: every row is unconditionally
     platform-wide (CLAUDE.md Rule 6's owner_scope convention, mirrored here
     as "the table has no org axis" since there is no per-org variant of it).
     Writes are super_admin only, enforced BOTH at the RLS layer (migration
     litellmphased2_model_catalog.sql) and here in the app layer.

  2. ``org_model_selections`` — which of the curated models one org has
     authorised. Row PRESENCE = authorised (no separate active flag — the
     same plain-grant-table precedent as user_roles/role_permissions, not a
     valued-history table Rule 3 bi-temporal restatement would apply to).
     Reads open to any org member; writes gated on ``manage_org_settings`` —
     the identical envelope the ai-credentials endpoints (Phase D1a) already
     established. Never a second, bespoke permission gate for this area.

``resolve_authorized_models`` is the one function ``services.extraction``
calls at the real call path. It returns ``None`` for "unrestricted" (no
explicit selection — every org's real state today) rather than an empty set,
so an org that has never touched this screen is byte-for-byte unaffected —
Phase D2's own explicit no-regression requirement.

LiteLLM D2 §3 follow-up (litellmavailability.structural) — THREE-STATE
AVAILABILITY on ``platform_model_catalog.availability``
(``migrations/litellmavailability_catalog_states.sql``, already live):
``available`` / ``deprecated`` / ``disabled``, enforced by a CHECK
constraint (never pre-validated in Python — a bad value is a genuine DB
refusal, so the app-layer enum can never drift from what the database
actually accepts). ``resolve_authorized_models`` above is UNCHANGED and
deliberately does not look at availability at all: it answers "did this org
choose this model", which stays true forever regardless of what Hollisworks
later does to the model — that is exactly what lets a 'deprecated'
selection keep resolving. Two new, narrower surfaces layer on top instead:

  * ``list_catalog_for_org`` — the real picker vocabulary for one org:
    every 'available' row, plus any non-'available' row this org has
    ALREADY selected. A model this org has never selected simply is not in
    the list once it stops being 'available' — hidden from the picker,
    per spec, without touching ``list_catalog`` itself (the Hollisworks
    curation screen, ``GET /admin/model-catalog``, must keep showing every
    row in every state, since managing those states IS that screen's job).
  * ``services.extraction._execute_chain`` additionally drops any
    'disabled' model_id from the attempt list, unconditionally (regardless
    of org authorization) — never merely hidden from a screen. 'deprecated'
    is deliberately NOT filtered there: an existing selection must still
    make a real call.

Write-side gates (``set_org_selections`` refusing a NEW, non-'available'
pick; ``validate_assignable_model`` refusing a NEW task assignment onto a
'disabled' model outright, or onto a 'deprecated' one the org has not
already authorized) are the same defense-in-depth precedent Rule 1's
permission-envelope section states directly: a hidden picker option behind
an unprotected write endpoint is still a real bug.

Task 1b (GET /model/info probe, live): the proxy's model catalogue only lists
REGISTERED DEPLOYMENTS (3 today — the platform's claude-sonnet, claude-haiku
and voyage-3.5), not a broad provider catalogue. A curated model's
``model_id`` IS that deployment's ``model_name`` (litellmseedfix's naming
convention — see org_settings.py DEFAULT_SETTINGS), so
``enrich_with_live_info`` attaches real
``max_input_tokens``/``max_output_tokens``/``input_cost_per_token``/
``output_cost_per_token`` from LiteLLM's own admin API for display. There is
no "provider" field on a model_info entry — the catalog's own ``provider``
column is the source of truth for that, never derived from LiteLLM's
response. A curated model_id with no matching registered deployment (a
future curated entry with no live proxy deployment behind it yet) simply
gets no enrichment — a real, honestly-reported gap, not a bug to paper over
with invented numbers.
"""
from __future__ import annotations


class ModelCatalogError(RuntimeError):
    """A catalog write was rejected — bad input or a real DB conflict."""


async def list_catalog(conn) -> list[dict]:
    """The FULL curated list, every availability state included — this is
    the Hollisworks curation screen's own read, which must see 'deprecated'
    and 'disabled' rows to manage them. Never use this for an org-facing
    picker; see ``list_catalog_for_org``."""
    rows = await conn.fetch(
        "SELECT model_id, display_name, provider, availability, created_at "
        "FROM platform_model_catalog ORDER BY provider, display_name"
    )
    return [dict(r) for r in rows]


async def list_catalog_for_org(conn, org_id) -> list[dict]:
    """The real picker vocabulary for one org (Task 3): every 'available'
    row, plus any non-'available' row this org has ALREADY selected.

    A model this org never selected simply disappears from this list once
    Hollisworks moves it off 'available' — hidden from the picker, exactly
    per spec — while a model the org already picked stays visible (and thus
    editable/removable) regardless of its current state, since the org's
    existing choice must remain something the UI can show and act on, not
    just something ``resolve_authorized_models`` silently keeps honouring
    server-side.
    """
    selected = set(await list_org_selections(conn, org_id))
    catalog = await list_catalog(conn)
    return [
        m for m in catalog
        if m["availability"] == "available" or m["model_id"] in selected
    ]


async def add_catalog_model(
    conn, *, model_id: str, display_name: str, provider: str, created_by=None
) -> dict:
    model_id = (model_id or "").strip()
    display_name = (display_name or "").strip()
    provider = (provider or "").strip()
    if not model_id or not display_name or not provider:
        raise ModelCatalogError("model_id, display_name and provider are all required")

    existing = await conn.fetchval(
        "SELECT 1 FROM platform_model_catalog WHERE model_id = $1", model_id
    )
    if existing:
        raise ModelCatalogError(f"'{model_id}' is already on the platform catalog")

    row = await conn.fetchrow(
        """
        INSERT INTO platform_model_catalog (model_id, display_name, provider, created_by)
        VALUES ($1, $2, $3, $4)
        RETURNING model_id, display_name, provider, availability, created_at
        """,
        model_id, display_name, provider, created_by,
    )
    return dict(row)


async def remove_catalog_model(conn, model_id: str) -> bool:
    """True if a row was actually removed. CASCADEs into every org's
    org_model_selections for this model — an org cannot keep authorising a
    model Hollisworks has withdrawn from the curated list.

    Refuses to delete a model any org has ever selected (litellmavailability
    follow-up) — that is precisely the "too blunt" gap the three-state
    availability model exists to fix: an in-use model must be moved to
    'deprecated'/'disabled' with a real alert, never deleted out from under
    an org with no warning. A model no org has ever selected stays freely
    deletable, per the sprint's own explicit carve-out.
    """
    in_use = await conn.fetchval(
        "SELECT 1 FROM org_model_selections WHERE model_id = $1 LIMIT 1", model_id
    )
    if in_use:
        raise ModelCatalogError(
            f"'{model_id}' is selected by at least one organization — set its "
            f"availability to 'deprecated' or 'disabled' instead of deleting it."
        )
    result = await conn.execute(
        "DELETE FROM platform_model_catalog WHERE model_id = $1", model_id
    )
    return result != "DELETE 0"


async def set_catalog_availability(conn, *, model_id: str, availability: str) -> dict:
    """Hollisworks super-admin transitions one catalog model's availability.

    Deliberately does NOT pre-validate ``availability`` against the three
    known values — the CHECK constraint
    (``platform_model_catalog_availability_chk``) is the one real source of
    truth for what is legal, so a bad value surfaces as a genuine
    ``asyncpg.CheckViolationError`` the router translates to 400, never a
    second, drift-prone Python copy of the same enum.
    """
    row = await conn.fetchrow(
        """
        UPDATE platform_model_catalog SET availability = $2
        WHERE model_id = $1
        RETURNING model_id, display_name, provider, availability, created_at
        """,
        model_id, availability,
    )
    if row is None:
        raise ModelCatalogError(f"'{model_id}' is not on the platform catalog")
    return dict(row)


async def get_orgs_selecting(conn, model_id: str) -> list:
    """Every distinct org_id with a live ``org_model_selections`` row for
    ``model_id`` — Task 2's "affected orgs only" alert audience, never every
    org on the platform."""
    rows = await conn.fetch(
        "SELECT DISTINCT org_id FROM org_model_selections WHERE model_id = $1",
        model_id,
    )
    return [r["org_id"] for r in rows]


async def list_org_selections(conn, org_id) -> list[str]:
    rows = await conn.fetch(
        "SELECT model_id FROM org_model_selections WHERE org_id = $1", org_id
    )
    return [r["model_id"] for r in rows]


async def set_org_selections(conn, org_id, model_ids: list[str], *, updated_by=None) -> list[str]:
    """Replace org_id's authorised set with exactly ``model_ids``.

    Every id must already be on the platform catalog — the FK enforces this
    too, but a clear ModelCatalogError beats a raw asyncpg FK-violation
    message reaching the API caller. A plain DELETE-then-INSERT inside the
    caller's transaction (not bi-temporal — see module docstring): this is a
    grant set, not a history.

    litellmavailability follow-up: a model_id not already in this org's
    CURRENT selection may only be added while 'available' — the write-side
    half of "hidden from the picker" (Rule 1's permission-envelope
    precedent: a hidden option behind an unprotected write is still a real
    bug). A model_id the org ALREADY has selected may always stay in the
    replacement set regardless of its current state — re-submitting an
    unchanged selection (e.g. adding one more model elsewhere in the same
    picker) must never accidentally become impossible just because
    Hollisworks deprecated or disabled something this org picked earlier.
    """
    model_ids = sorted({m.strip() for m in (model_ids or []) if m and m.strip()})
    if model_ids:
        valid = await conn.fetch(
            "SELECT model_id, availability FROM platform_model_catalog "
            "WHERE model_id = ANY($1::text[])",
            model_ids,
        )
        valid_rows = {r["model_id"]: r["availability"] for r in valid}
        unknown = [m for m in model_ids if m not in valid_rows]
        if unknown:
            raise ModelCatalogError(
                f"Not on the platform catalog: {', '.join(unknown)}"
            )

        current = set(await list_org_selections(conn, org_id))
        blocked = [
            m for m in model_ids
            if valid_rows[m] != "available" and m not in current
        ]
        if blocked:
            raise ModelCatalogError(
                f"Not available for new selection (availability != "
                f"'available'): {', '.join(blocked)}"
            )

    async with conn.transaction():
        await conn.execute(
            "DELETE FROM org_model_selections WHERE org_id = $1", org_id
        )
        for model_id in model_ids:
            await conn.execute(
                """
                INSERT INTO org_model_selections (org_id, model_id, created_by)
                VALUES ($1, $2, $3)
                """,
                org_id, model_id, updated_by,
            )
    return model_ids


async def resolve_authorized_models(org_id) -> set[str] | None:
    """This org's authorised model_id set, or ``None`` for unrestricted.

    ``None`` — not an empty set — is "no explicit selection has ever been
    made," which resolves to the pre-D2 status quo (services.extraction's
    fallback chain runs unfiltered). Any lookup failure ALSO returns ``None``
    rather than raising, mirroring services.extraction.resolve_model's own
    fail-to-default discipline: a broken lookup must never turn into every AI
    call in the org failing closed.
    """
    if org_id is None:
        return None
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model_id FROM org_model_selections WHERE org_id = $1", org_id
            )
        if not rows:
            return None
        return {r["model_id"] for r in rows}
    except Exception as exc:  # noqa: BLE001
        print(f"resolve_authorized_models failed for org {org_id}, unrestricted: {exc}")
        return None


async def disabled_model_ids() -> set[str]:
    """model_id set with ``availability = 'disabled'`` — the ONE call-path
    filter ``services.extraction._execute_chain`` applies unconditionally,
    regardless of org authorization (a 'disabled' model must never resolve,
    even for an org that authorised or is even currently assigned it).

    Fails to an EMPTY set on any lookup error, mirroring
    ``resolve_authorized_models``'s own fail-open discipline: a broken
    lookup here must never turn into every AI call on the platform failing
    closed. Deliberately excludes 'deprecated' — an existing selection of a
    deprecated model must keep resolving.
    """
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model_id FROM platform_model_catalog "
                "WHERE availability = 'disabled'"
            )
        return {r["model_id"] for r in rows}
    except Exception as exc:  # noqa: BLE001
        print(f"disabled_model_ids failed, treating as none disabled: {exc}")
        return set()


async def validate_assignable_model(conn, org_id, model_id: str) -> None:
    """Raise ModelCatalogError unless ``model_id`` is a real platform-catalog
    entry this org is actually authorised to use (LiteLLM Phase E task
    assignment). Shared by services.org_settings._validate_setting (the
    generic settings PUT) and the dedicated task-assignment endpoint — ONE
    check, not two independently-maintained copies of "is this allowed."

    An org with no explicit ``org_model_selections`` row is unrestricted
    (``resolve_authorized_models`` returns ``None``, the pre-D2 status quo),
    so any real catalog entry is assignable — matching exactly what
    ``_execute_chain`` would actually let this attempt run as.

    litellmavailability follow-up: a 'disabled' model can never be freshly
    ASSIGNED to a task (it would never actually resolve — see
    ``services.extraction._execute_chain``'s unconditional disabled filter).
    A 'deprecated' model may only be assigned while it is already in this
    org's authorised set — the same "existing selection keeps working, new
    selection is hidden" rule the picker enforces, applied to the
    task-assignment write path too.
    """
    row = await conn.fetchrow(
        "SELECT availability FROM platform_model_catalog WHERE model_id = $1",
        model_id,
    )
    if row is None:
        raise ModelCatalogError(f"'{model_id}' is not on the platform catalog")
    availability = row["availability"]

    from services.litellm_credentials import chat_capable_models

    if model_id not in chat_capable_models():
        raise ModelCatalogError(
            f"'{model_id}' does not report mode: 'chat' on the live proxy — "
            f"not assignable to a chat task (this dial calls /v1/messages; "
            f"an embedding model like 'voyage-3.5' cannot serve it)."
        )

    authorized = await resolve_authorized_models(org_id)
    if authorized is not None and model_id not in authorized:
        raise ModelCatalogError(
            f"'{model_id}' is not authorised for this organization — "
            f"authorise it first under the org's model selections."
        )

    if availability == "disabled":
        raise ModelCatalogError(
            f"'{model_id}' is disabled on the platform catalog — it will "
            f"never resolve, so it cannot be assigned to a task."
        )
    if availability == "deprecated" and not (authorized is not None and model_id in authorized):
        raise ModelCatalogError(
            f"'{model_id}' is deprecated and not authorised for this "
            f"organization — it cannot be newly assigned."
        )


async def get_task_assignments(conn, org_id) -> list[dict]:
    """LiteLLM Phase E — one row per services.extraction.MODEL_TASK_REGISTRY
    entry: which model this org has assigned (or the platform default, when
    unset) plus its assigned effort, and the live ``supports_reasoning`` for
    whichever model is actually in effect right now. This is what gates the
    UI's effort control — never a client-side guess."""
    from services.extraction import MODEL_TASK_REGISTRY
    from services.litellm_credentials import reasoning_support_by_model
    from services.org_settings import get_setting_with_origin

    reasoning_map = reasoning_support_by_model()
    rows = []
    for entry in MODEL_TASK_REGISTRY:
        model_value, model_is_default = await get_setting_with_origin(
            conn, org_id, entry["key"]
        )
        effort_value, effort_is_default = await get_setting_with_origin(
            conn, org_id, entry["effort_key"]
        )
        rows.append({
            "key": entry["key"],
            "effort_key": entry["effort_key"],
            "label": entry["label"],
            "description": entry["description"],
            "task_types": entry["task_types"],
            "assigned_model": model_value,
            "is_default_model": model_is_default,
            "assigned_effort": effort_value,
            "is_default_effort": effort_is_default,
            "supports_reasoning": bool(reasoning_map.get(model_value)) if model_value else False,
        })
    return rows


async def set_task_assignment(
    conn, pool, org_id, task_key: str, *, model_id, effort, updated_by, principal
) -> dict:
    """Assign a model (and optionally an effort level) to one
    MODEL_TASK_REGISTRY dial. ``model_id``/``effort`` of ``None`` resets that
    half back to the platform default.

    Reuses ``services.org_settings.set_setting`` for BOTH writes rather than
    inserting directly — that is the one function that already enforces
    ``manage_org_settings`` and runs ``_validate_setting`` (which calls this
    module's own ``validate_assignable_model`` for the model, and checks the
    effort enum), so this endpoint and the generic settings PUT can never
    disagree about what is allowed. Both writes share one transaction: a
    rejected effort must not leave the model half written alone.
    """
    from services.extraction import EFFORT_KEY_BY_MODEL_KEY, MODEL_TASK_REGISTRY
    from services.org_settings import set_setting

    registry_keys = {e["key"] for e in MODEL_TASK_REGISTRY}
    if task_key not in registry_keys:
        raise ModelCatalogError(f"'{task_key}' is not an assignable AI task")
    effort_key = EFFORT_KEY_BY_MODEL_KEY[task_key]

    async with conn.transaction():
        await set_setting(
            conn, org_id, task_key, model_id, updated_by,
            principal=principal, pool=pool,
        )
        await set_setting(
            conn, org_id, effort_key, effort, updated_by,
            principal=principal, pool=pool,
        )
    return {"key": task_key, "model_id": model_id, "effort_key": effort_key, "effort": effort}


def _provider_from_upstream(upstream_model: str | None) -> str | None:
    if not upstream_model or "/" not in upstream_model:
        return None
    return upstream_model.split("/", 1)[0]


async def enrich_with_live_info(catalog_rows: list[dict]) -> list[dict]:
    """Best-effort context-window/pricing enrichment from the live LiteLLM
    admin API (Task 1b). Never raises — a proxy that is unreachable just means
    every row stays unenriched, since this is display metadata, not something
    any write path depends on."""
    try:
        from services.litellm_credentials import list_deployments

        deployments = list_deployments()
    except Exception as exc:  # noqa: BLE001
        print(f"model_catalog: live /model/info enrichment unavailable: {exc}")
        deployments = []

    # litellmseedfix's naming convention (org_settings.py DEFAULT_SETTINGS'
    # own comment) makes a curated model_id THE SAME string as the proxy's
    # registered deployment `model_name` — 'claude-sonnet', 'claude-haiku',
    # 'voyage-3.5'. Matching on model_name directly (rather than parsing
    # litellm_params.model's "provider/real-model-id" upstream string) is
    # what a same-identifier-space design makes possible, and is exact where
    # the old upstream-substring match was only a heuristic. This is
    # unrelated to litellm_credentials.py's own "never match/leak an org's
    # own BYOK deployment name" rule — that rule protects the PER-ORG
    # synthetic `org-<provider>-<org_id>` name, a different, genuinely
    # internal identifier space; the platform's shared deployment names are
    # already the catalog's own public identifier.
    by_model_name: dict[str, dict] = {
        entry["model_name"]: entry for entry in deployments if entry.get("model_name")
    }

    out = []
    for row in catalog_rows:
        row = dict(row)
        match = by_model_name.get(row["model_id"])
        if match:
            info = match.get("model_info") or {}
            row["context_window"] = info.get("max_input_tokens")
            row["max_output_tokens"] = info.get("max_output_tokens")
            row["input_cost_per_token"] = info.get("input_cost_per_token")
            row["output_cost_per_token"] = info.get("output_cost_per_token")
        else:
            row["context_window"] = None
            row["max_output_tokens"] = None
            row["input_cost_per_token"] = None
            row["output_cost_per_token"] = None
        out.append(row)
    return out
