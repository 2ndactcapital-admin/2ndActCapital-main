"""ensemblesystemone.structural — the System One model catalog
(``public.ai_system_one_models``).

A System One model answers TYPED questions (choice / score / yes-no) with a
probability distribution, not chat text. Jev (TypeSafe) is the only one today.
An ensemble is Model 1 + Model 2 (LLMs, from ``platform_model_catalog``) plus
exactly one System One model from this catalog — see services.ai_ensembles.

Super-admin curates this catalog the same way it curates the LLM catalog
(services.model_catalog): add / remove entries, set availability, mark exactly
one entry as the DEFAULT (a partial unique index makes a second default a
database refusal), and "verify now".

AVAILABILITY IS EARNED, NEVER SET. ``'available'`` is reachable ONLY through
``verify_system_one_model``: a real call through the LiteLLM proxy's /typesafe
pass-through that (1) lists models and (2) returns one decision whose
probabilities arrive intact. A credential existing is not availability. A
super-admin may move an entry to 'deprecated' or 'disabled' by hand, never to
'available'. The DB CHECK ``ai_system_one_models_available_requires_
verification_chk`` backs this: 'available' needs last_verified_at AND the
model_version TypeSafe reported.

HOW JEV IS CALLED: ``POST {LITELLM_BASE_URL}/typesafe/v1/systemone`` with the
app's own LiteLLM key. The proxy forwards to https://api.typesafe.ai and
injects ``TYPESAFE_API_KEY`` from ITS environment (Doppler prd_lite_llm). The
app never holds that key (scripts/configure_typesafe_passthrough.py).

The caller supplies the RLS context: through a real request the pool sets
app.is_super_admin from the principal; a script sets it itself. Every write
policy here checks it, and a missing context shows up as ZERO rows, not a
permission error — so a zero-row write is reported as "no such entry OR no
super-admin context", never as one specific cause.
"""
from __future__ import annotations

import asyncio
import json
import math

import asyncpg

AVAILABLE = "available"
MANUAL_AVAILABILITY = ("deprecated", "disabled")

# provider -> the proxy pass-through prefix that reaches it. Only TypeSafe
# exists; an entry with any other provider cannot be verified (and so can
# never become available) until a route for it is configured on the proxy.
PROVIDER_PROXY_PREFIX = {"typesafe": "/typesafe"}

# The minimal decision used by the live check. A two-option choice: the
# response must carry a probability for exactly these two keys, summing to 1.
_PROBE_QUESTION_KEY = "is_structured_note"
_PROBE_OPTIONS = {
    "yes": "The text describes a structured note",
    "no": "The text does not describe a structured note",
}
_PROBE_BODY_STATE = (
    "Autocallable contingent income note linked to the S&P 500 index, "
    "with a 70% downside barrier."
)

_COLUMNS = (
    "id, key, display_name, provider, model_route, model_version, "
    "credential_name, availability, is_default, last_verified_at, "
    "last_check_detail, notes, created_at"
)


class SystemOneCatalogError(RuntimeError):
    """A catalog write was refused — bad input, or the row was not writable."""


def _zero_rows(key: str) -> SystemOneCatalogError:
    return SystemOneCatalogError(
        f"'{key}' matched zero rows — EITHER it is not on the System One "
        f"catalog, OR this connection lacks super-admin RLS context (the write "
        f"policy then silently matches nothing). This code cannot tell which."
    )


def _out(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    for k in ("last_verified_at", "created_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def unavailable_reason(row) -> str | None:
    """Why an entry cannot fill the System One slot, or None if it can."""
    if row["availability"] == AVAILABLE and row["model_version"]:
        return None
    reason = f"availability is '{row['availability']}'"
    if row["last_check_detail"]:
        reason += f" — {row['last_check_detail']}"
    elif row["last_verified_at"] is None:
        reason += " — never verified by a real call"
    return reason


async def list_system_one_models(conn) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM ai_system_one_models ORDER BY is_default DESC, key"
    )
    out = []
    for r in rows:
        d = _out(r)
        d["unavailable_reason"] = unavailable_reason(r)
        d["selectable"] = d["unavailable_reason"] is None
        out.append(d)
    return out


async def get_system_one_model(conn, key: str):
    return await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM ai_system_one_models WHERE key = $1", key
    )


async def add_system_one_model(
    conn, *, key: str, display_name: str, provider: str, model_route: str,
    notes: str | None = None, credential_name: str | None = None,
) -> dict:
    """A new entry always starts 'disabled' and never default — it has not
    been called yet."""
    key = (key or "").strip()
    display_name = (display_name or "").strip()
    provider = (provider or "").strip().lower()
    model_route = (model_route or "").strip()
    if not key or not display_name or not provider or not model_route:
        raise SystemOneCatalogError(
            "key, display_name, provider and model_route are all required"
        )
    # A System One key must never collide with an LLM catalog id: the two
    # slots would become ambiguous in any list that shows both.
    if await conn.fetchval(
        "SELECT 1 FROM platform_model_catalog WHERE model_id = $1", key
    ):
        raise SystemOneCatalogError(
            f"'{key}' is an LLM on the platform model catalog — System One keys "
            f"must be distinct from LLM model ids"
        )
    try:
        row = await conn.fetchrow(
            f"""
            INSERT INTO ai_system_one_models
                (key, display_name, provider, model_route, credential_name,
                 availability, is_default, last_check_detail, notes)
            VALUES ($1, $2, $3, $4, $5, 'disabled', false,
                    'Never verified. Run "Verify now".', $6)
            RETURNING {_COLUMNS}
            """,
            key, display_name, provider, model_route, credential_name, notes,
        )
    except asyncpg.UniqueViolationError as exc:
        raise SystemOneCatalogError(f"'{key}' is already on the System One catalog") from exc
    return _out(row)


async def remove_system_one_model(conn, key: str) -> None:
    current = await get_system_one_model(conn, key)
    if current is None:
        raise SystemOneCatalogError(f"'{key}' is not on the System One catalog")
    if current["is_default"]:
        raise SystemOneCatalogError(
            f"'{key}' is the default System One model — make another entry the "
            f"default before removing it"
        )
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                "DELETE FROM ai_system_one_models WHERE key = $1 RETURNING key", key
            )
    except asyncpg.ForeignKeyViolationError as exc:
        raise SystemOneCatalogError(
            f"'{key}' is recorded in ensemble history and cannot be removed — "
            f"set it to 'disabled' instead"
        ) from exc
    if row is None:
        raise _zero_rows(key)


async def set_system_one_availability(conn, key: str, availability: str) -> dict:
    if availability == AVAILABLE:
        raise SystemOneCatalogError(
            "'available' cannot be set by hand — run Verify now; a real call "
            "through the proxy is the only way an entry becomes available"
        )
    if availability not in MANUAL_AVAILABILITY:
        raise SystemOneCatalogError(
            f"'{availability}' is not a valid availability (allowed by hand: "
            f"{', '.join(MANUAL_AVAILABILITY)})"
        )
    row = await conn.fetchrow(
        f"""
        UPDATE ai_system_one_models
        SET availability = $2,
            last_check_detail = 'Set to ' || $2 || ' by a super admin.'
        WHERE key = $1
        RETURNING {_COLUMNS}
        """,
        key, availability,
    )
    if row is None:
        raise _zero_rows(key)
    return _out(row)


async def set_system_one_default(conn, key: str) -> dict:
    """Move the default to ``key`` in ONE transaction. The partial unique
    index ai_system_one_models_one_default refuses two defaults outright, so
    the old default is cleared first."""
    async with conn.transaction():
        target = await conn.fetchrow(
            "SELECT key FROM ai_system_one_models WHERE key = $1 FOR UPDATE", key
        )
        if target is None:
            raise SystemOneCatalogError(f"'{key}' is not on the System One catalog")
        await conn.execute(
            "UPDATE ai_system_one_models SET is_default = false "
            "WHERE is_default AND key <> $1",
            key,
        )
        row = await conn.fetchrow(
            f"UPDATE ai_system_one_models SET is_default = true WHERE key = $1 "
            f"RETURNING {_COLUMNS}",
            key,
        )
        if row is None:
            raise _zero_rows(key)
    return _out(row)


# ── The live availability check ────────────────────────────────────────────

def _describe_http_failure(step: str, status: int, prefix: str) -> str:
    """Name what the status code can actually tell us, and no more."""
    if status == 404:
        return (
            f"{step}: HTTP 404 — the proxy has no {prefix} route, or the upstream "
            f"path does not exist (scripts/configure_typesafe_passthrough.py "
            f"configures the route)"
        )
    if status in (401, 403):
        return (
            f"{step}: HTTP {status} — refused for authentication. Either the "
            f"app's LiteLLM key was rejected by the proxy, or the upstream "
            f"rejected the provider key the proxy injected (TYPESAFE_API_KEY "
            f"missing, wrong, or not yet loaded by the proxy). The status alone "
            f"does not say which."
        )
    return f"{step}: HTTP {status}"


def check_decision(payload: dict, route: str, listed: set[str]) -> tuple[str | None, str | None]:
    """Validate one /v1/systemone response. Returns (reported_version, error)."""
    reported = payload.get("model")
    if not isinstance(reported, str) or not reported:
        return None, "decision response carries no 'model' version"
    # A pinned route that is not a listed alias must come back as exactly
    # itself — otherwise the version recorded would not be the one asked for.
    if route not in listed and reported != route:
        return None, (
            f"asked for pinned '{route}' but TypeSafe reported '{reported}' — "
            f"a pinned route must run exactly that version"
        )
    answer = (payload.get("answers") or {}).get(_PROBE_QUESTION_KEY)
    if not isinstance(answer, dict):
        return None, "decision response has no answer for the probe question"
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or set(probs) != set(_PROBE_OPTIONS):
        return None, (
            f"probabilities missing or not keyed by the probe options "
            f"(got {sorted(probs) if isinstance(probs, dict) else type(probs).__name__})"
        )
    values = list(probs.values())
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return None, "probabilities are not all numbers"
    if not all(0.0 <= float(v) <= 1.0 for v in values) or not math.isclose(
        sum(float(v) for v in values), 1.0, abs_tol=1e-3
    ):
        return None, f"probabilities do not form a distribution (sum={sum(values)})"
    if answer.get("choice") not in _PROBE_OPTIONS:
        return None, f"choice {answer.get('choice')!r} is not one of the probe options"
    return reported, None


def probe_proxy(provider: str, route: str) -> dict:
    """The two real calls, synchronously. Never raises; never touches the DB.

    Returns {"ok", "detail", "listed_models", "reported_version", "decision"}.
    ``decision`` is the parsed response body, unchanged — the caller may
    inspect it, nothing here rewrites it.
    """
    from services.litellm_credentials import proxy_request

    prefix = PROVIDER_PROXY_PREFIX.get(provider)
    out = {"ok": False, "detail": None, "listed_models": [], "reported_version": None,
           "decision": None}
    if prefix is None:
        out["detail"] = f"no proxy route is configured for provider '{provider}'"
        return out
    try:
        status, body = proxy_request(f"{prefix}/v1/models", timeout=30)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"models listing: proxy unreachable ({type(exc).__name__})"
        return out
    if status != 200:
        out["detail"] = _describe_http_failure("models listing", status, prefix)
        return out
    try:
        models = json.loads(body).get("models") or []
        listed = {m.get("name") for m in models if isinstance(m, dict) and m.get("name")}
    except (ValueError, AttributeError):
        out["detail"] = "models listing: response was not the expected JSON"
        return out
    out["listed_models"] = sorted(listed)
    if not listed:
        out["detail"] = "models listing: TypeSafe returned no models"
        return out

    request = {
        "model": route,
        "state": _PROBE_BODY_STATE,
        "questions": {
            _PROBE_QUESTION_KEY: {
                "type": "choice",
                "instructions": "Does this text describe a structured note?",
                "criteria": _PROBE_OPTIONS,
            }
        },
    }
    try:
        status, body = proxy_request(
            f"{prefix}/v1/systemone", method="POST", body=request, timeout=60
        )
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"decision: proxy unreachable ({type(exc).__name__})"
        return out
    if status != 200:
        out["detail"] = _describe_http_failure("decision", status, prefix)
        return out
    try:
        payload = json.loads(body)
    except ValueError:
        out["detail"] = "decision: response was not JSON"
        return out
    out["decision"] = payload
    reported, error = check_decision(payload, route, listed)
    if error:
        out["detail"] = f"decision: {error}"
        return out
    out["ok"] = True
    out["reported_version"] = reported
    out["detail"] = (
        f"Verified: models listing returned {len(listed)} model(s); one decision "
        f"on '{route}' ran as '{reported}' with probabilities intact."
    )
    return out


async def verify_system_one_model(conn, key: str) -> dict:
    """Run the real check and record the outcome. On success the entry
    becomes 'available' with last_verified_at = now() and model_version =
    what TypeSafe reported. On failure an 'available' entry drops to
    'disabled'; a 'deprecated' or 'disabled' one keeps its state. Either way
    last_check_detail says what happened, and last_verified_at keeps the last
    SUCCESS (never overwritten by a failure)."""
    row = await get_system_one_model(conn, key)
    if row is None:
        raise SystemOneCatalogError(f"'{key}' is not on the System One catalog")

    # HTTP outside any DB transaction.
    result = await asyncio.to_thread(probe_proxy, row["provider"], row["model_route"])

    if result["ok"]:
        updated = await conn.fetchrow(
            f"""
            UPDATE ai_system_one_models
            SET availability = 'available', last_verified_at = now(),
                model_version = $2, last_check_detail = $3
            WHERE key = $1
            RETURNING {_COLUMNS}
            """,
            key, result["reported_version"], result["detail"],
        )
    else:
        updated = await conn.fetchrow(
            f"""
            UPDATE ai_system_one_models
            SET availability = CASE WHEN availability = 'available'
                                    THEN 'disabled' ELSE availability END,
                last_check_detail = $2
            WHERE key = $1
            RETURNING {_COLUMNS}
            """,
            key, f"Last check failed: {result['detail']}",
        )
    if updated is None:
        raise _zero_rows(key)
    return {
        "ok": result["ok"],
        "detail": result["detail"],
        "listed_models": result["listed_models"],
        "reported_version": result["reported_version"],
        "entry": _out(updated),
    }
