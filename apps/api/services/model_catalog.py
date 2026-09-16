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

Task 1b (GET /model/info probe, live): the proxy's model catalogue only lists
REGISTERED DEPLOYMENTS (2 today — the platform's own claude-sonnet and
voyage-3.5 mirrors), not a broad provider catalogue. Where a curated model's
``model_id`` happens to match a registered deployment's real upstream model
string, ``enrich_with_live_info`` opportunistically attaches real
``max_input_tokens``/``max_output_tokens``/``input_cost_per_token``/
``output_cost_per_token`` from LiteLLM's own admin API for display. There is
no "provider" field on a model_info entry — it is derived here from the
``litellm_params.model`` "provider/model" prefix, never guessed from the
model_id string alone. A curated model with no matching live deployment
(true for most of the catalog, since Phase D1's deployments are per-PROVIDER,
not per-model) simply gets no enrichment — a real, honestly-reported gap, not
a bug to paper over with invented numbers.
"""
from __future__ import annotations


class ModelCatalogError(RuntimeError):
    """A catalog write was rejected — bad input or a real DB conflict."""


async def list_catalog(conn) -> list[dict]:
    rows = await conn.fetch(
        "SELECT model_id, display_name, provider, created_at "
        "FROM platform_model_catalog ORDER BY provider, display_name"
    )
    return [dict(r) for r in rows]


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
        RETURNING model_id, display_name, provider, created_at
        """,
        model_id, display_name, provider, created_by,
    )
    return dict(row)


async def remove_catalog_model(conn, model_id: str) -> bool:
    """True if a row was actually removed. CASCADEs into every org's
    org_model_selections for this model — an org cannot keep authorising a
    model Hollisworks has withdrawn from the curated list."""
    result = await conn.execute(
        "DELETE FROM platform_model_catalog WHERE model_id = $1", model_id
    )
    return result != "DELETE 0"


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
    """
    model_ids = sorted({m.strip() for m in (model_ids or []) if m and m.strip()})
    if model_ids:
        valid = await conn.fetch(
            "SELECT model_id FROM platform_model_catalog WHERE model_id = ANY($1::text[])",
            model_ids,
        )
        valid_ids = {r["model_id"] for r in valid}
        unknown = [m for m in model_ids if m not in valid_ids]
        if unknown:
            raise ModelCatalogError(
                f"Not on the platform catalog: {', '.join(unknown)}"
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

    by_upstream: dict[str, dict] = {}
    for entry in deployments:
        upstream = (entry.get("litellm_params") or {}).get("model")
        if upstream:
            by_upstream[upstream] = entry

    out = []
    for row in catalog_rows:
        row = dict(row)
        match = None
        for upstream, entry in by_upstream.items():
            # The upstream string is "provider/real-model-id" (e.g.
            # "anthropic/claude-sonnet-4-6"); a curated model_id matches when
            # it IS that real model id — never matched on the LiteLLM
            # deployment's own model_name, which is internal (Task 4's proof
            # in litellm_credentials.py applies here too).
            if upstream.endswith(f"/{row['model_id']}") or upstream == row["model_id"]:
                match = entry
                break
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
