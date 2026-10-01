"""ensemblesystemone.structural — the ensemble picker and versioned ensemble
configs (``public.ai_ensemble_configs``).

An ensemble for a task is exactly three slots:

  * Model 1, Model 2 — LLMs from the EXISTING ``platform_model_catalog``
    (services.model_catalog). An option is selectable only when its catalog
    availability is 'available' AND the live proxy reports it as mode 'chat'
    (``litellm_credentials.chat_capable_models`` — Phase E's check, reused,
    never a second one). That rule alone keeps voyage-3.5 (an embedding
    model) out. The stored value is the catalog ``model_id`` — the proxy
    deployment name, e.g. 'claude-haiku'. The proxy REJECTS raw upstream ids
    with HTTP 400, so an upstream id is only ever the version SNAPSHOT.
  * System One — an entry from ``ai_system_one_models`` (services.system_one),
    preselected to the catalog default. Never an LLM: the column carries an
    FK to the System One catalog, and validation refuses anything else first.

The note-term corpus is GLOBAL (one corpus, every org), so the active
ensemble is platform-wide: one active config per task_key, no org axis.

``activate_ensemble`` is the only write. Configs are immutable (the
ai_ensemble_configs_retire_only trigger permits exactly one UPDATE: retiring
the active row), so a change is a new row; the previous active row is retired
in the SAME transaction. Each slot's exact version is snapshotted into the
*_version columns at creation — permanent provenance.

NOTE: extraction does NOT read the active ensemble yet — next sprint.
"""
from __future__ import annotations

import asyncio

import asyncpg

from services.system_one import list_system_one_models

# The only ensemble task that exists today. A key, not a display label.
KNOWN_TASK_KEYS = frozenset({"note_terms_hazard"})
AVAILABLE = "available"


class EnsembleValidationError(ValueError):
    """The requested selection is invalid (bad input, unavailable model)."""


class EnsembleConflictError(RuntimeError):
    """A concurrent activation won the race, or the retire matched no row."""


def _upstream_versions() -> dict[str, str]:
    """{deployment model_name: upstream model id} from the existing
    /model/info wrapper — the source of each LLM's version snapshot."""
    from services.litellm_credentials import list_deployments

    out: dict[str, set] = {}
    for d in list_deployments():
        name = d.get("model_name")
        upstream = (d.get("litellm_params") or {}).get("model")
        if name and upstream:
            out.setdefault(name, set()).add(
                upstream.split("/", 1)[1] if "/" in upstream else upstream
            )
    # A group whose deployments disagree has no single exact version.
    return {name: next(iter(v)) for name, v in out.items() if len(v) == 1}


async def llm_options(conn) -> list[dict]:
    from services.litellm_credentials import chat_capable_models
    from services.model_catalog import list_catalog

    catalog = await list_catalog(conn)
    chat = await asyncio.to_thread(chat_capable_models)
    try:
        versions = await asyncio.to_thread(_upstream_versions)
        version_error = None
    except Exception as exc:  # noqa: BLE001
        versions, version_error = {}, f"live proxy /model/info unreadable ({type(exc).__name__})"

    out = []
    for m in catalog:
        model_id = m["model_id"]
        reason = None
        if m["availability"] != AVAILABLE:
            reason = f"platform catalog availability is '{m['availability']}'"
        elif not chat:
            reason = "the live proxy reports no chat models (it may be unreachable)"
        elif model_id not in chat:
            reason = "not a chat model on the live proxy (mode is not 'chat')"
        elif model_id not in versions:
            reason = version_error or (
                "no single exact version on the live proxy — cannot snapshot it"
            )
        out.append({
            "model_id": model_id,
            "display_name": m["display_name"],
            "provider": m["provider"],
            "availability": m["availability"],
            "is_chat": model_id in chat,
            "model_version": versions.get(model_id),
            "selectable": reason is None,
            "unavailable_reason": reason,
        })
    return out


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
    rows = await conn.fetch(
        "SELECT * FROM ai_ensemble_configs WHERE task_key = $1 "
        "ORDER BY created_at DESC, id",
        task_key,
    )
    return [_row_out(r) for r in rows]


def blocker_for(llms: list[dict], system_ones: list[dict]) -> str | None:
    """Exactly why no valid ensemble can be activated right now, or None."""
    problems = []
    usable_llms = [o for o in llms if o["selectable"]]
    if len(usable_llms) < 2:
        detail = "; ".join(
            f"{o['model_id']}: {o['unavailable_reason']}" for o in llms if not o["selectable"]
        )
        problems.append(
            f"Model 1 and Model 2 need two different available chat models; "
            f"{len(usable_llms)} available"
            + (f" ({detail})" if detail else "")
        )
    if not any(o["selectable"] for o in system_ones):
        if not system_ones:
            problems.append("The System One catalog is empty")
        else:
            detail = "; ".join(
                f"{o['display_name']}: {o['unavailable_reason']}" for o in system_ones
            )
            problems.append(f"No System One model is available ({detail})")
    return ". ".join(problems) + "." if problems else None


async def get_picker(conn, task_key: str) -> dict:
    if task_key not in KNOWN_TASK_KEYS:
        raise EnsembleValidationError(f"unknown task_key '{task_key}'")
    llms = await llm_options(conn)
    system_ones = await list_system_one_models(conn)
    default = next((o for o in system_ones if o["is_default"]), None)
    history = await list_ensembles(conn, task_key)
    return {
        "task_key": task_key,
        "llm_options": llms,
        "system_one_options": system_ones,
        "default_system_one_model": default["key"] if default else None,
        # The server decides the preselection; the client only renders it.
        "preselected": {"system_one_model": default["key"] if default else None},
        "blocker": blocker_for(llms, system_ones),
        "active": next((r for r in history if r["is_active"]), None),
        "history": history,
    }


def validate_selection(
    llms: list[dict], system_ones: list[dict], task_key: str,
    model_1: str, model_2: str, system_one_model: str,
) -> tuple[dict, dict, dict]:
    if task_key not in KNOWN_TASK_KEYS:
        raise EnsembleValidationError(f"unknown task_key '{task_key}'")
    if not all(isinstance(x, str) and x.strip() for x in (model_1, model_2, system_one_model)):
        raise EnsembleValidationError("Model 1, Model 2 and the System One model are all required")
    # Mirrors ai_ensemble_configs_models_distinct_chk.
    if model_1 == model_2:
        raise EnsembleValidationError("Model 1 and Model 2 must be different models")

    llm_by_id = {o["model_id"]: o for o in llms}
    s1_by_key = {o["key"]: o for o in system_ones}
    picked = []
    for slot, value in (("Model 1", model_1), ("Model 2", model_2)):
        opt = llm_by_id.get(value)
        if opt is None:
            hint = (" — it is a System One model; LLM slots take chat models only"
                    if value in s1_by_key else "")
            raise EnsembleValidationError(
                f"{slot}: '{value}' is not on the platform model catalog{hint}"
            )
        if not opt["selectable"]:
            raise EnsembleValidationError(f"{slot}: '{value}' is not available — {opt['unavailable_reason']}")
        picked.append(opt)

    s1 = s1_by_key.get(system_one_model)
    if s1 is None:
        hint = (" — it is an LLM; the System One slot takes System One models only"
                if system_one_model in llm_by_id else "")
        raise EnsembleValidationError(
            f"System One: '{system_one_model}' is not on the System One catalog{hint}"
        )
    if not s1["selectable"]:
        raise EnsembleValidationError(
            f"System One: '{system_one_model}' is not available — {s1['unavailable_reason']}"
        )
    return picked[0], picked[1], s1


async def activate_ensemble(
    conn, task_key: str, model_1: str, model_2: str, system_one_model: str,
    created_by, notes: str | None = None,
) -> dict:
    """Validate, then retire the current active config and insert the new one
    in ONE transaction. Returns {"ensemble", "retired_id"}.

    The System One row is re-read FOR SHARE inside the transaction, so an
    entry disabled between validation and insert is still refused, and its
    version snapshot is the one current at commit. A race between two
    activations is settled by the one-active-per-task partial unique index.
    """
    llms = await llm_options(conn)
    system_ones = await list_system_one_models(conn)
    e1, e2, s1 = validate_selection(llms, system_ones, task_key, model_1, model_2, system_one_model)

    try:
        async with conn.transaction():
            s1_now = await conn.fetchrow(
                "SELECT availability, model_version FROM ai_system_one_models "
                "WHERE key = $1 FOR SHARE",
                s1["key"],
            )
            if s1_now is None or s1_now["availability"] != AVAILABLE or not s1_now["model_version"]:
                raise EnsembleValidationError(
                    f"System One: '{s1['key']}' stopped being available before activation"
                )
            current = await conn.fetchrow(
                "SELECT id FROM ai_ensemble_configs WHERE task_key = $1 AND is_active FOR UPDATE",
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
                    raise EnsembleConflictError(
                        "retiring the active ensemble matched zero rows — EITHER this "
                        "connection lacks super-admin RLS context (the UPDATE policy "
                        "silently matches nothing), OR another activation retired it "
                        "concurrently. This code cannot tell which."
                    )
                retired_id = str(retired["id"])
            row = await conn.fetchrow(
                """
                INSERT INTO ai_ensemble_configs (
                    task_key, model_1, model_1_version, model_2, model_2_version,
                    system_one_model, system_one_model_version,
                    is_active, created_by, activated_at, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, $8, now(), $9)
                RETURNING *
                """,
                task_key,
                e1["model_id"], e1["model_version"],
                e2["model_id"], e2["model_version"],
                s1["key"], s1_now["model_version"],
                created_by, notes,
            )
    except asyncpg.UniqueViolationError as exc:
        raise EnsembleConflictError(
            f"another ensemble for '{task_key}' was activated concurrently — reload and retry"
        ) from exc

    return {"ensemble": _row_out(row), "retired_id": retired_id}
