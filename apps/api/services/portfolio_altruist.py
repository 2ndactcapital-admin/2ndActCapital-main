"""Altruist custodial ingestion — Portfolio Phase B, Task 3.

STATUS: BLOCKED. No credentials exist, no call has ever been made, and no
mapping from an Altruist response is written below. That is the honest state of
this integration and this module exists to say so in code rather than in a
comment somebody deletes.

WHAT PHASE B'S DISCOVERY FOUND (Task 1d)
──────────────────────────────────────────────────────────────────────────────
Altruist is genuinely greenfield. Searched: every ``.py``, ``.md``, ``.sql``,
``.js``/``.jsx`` and ``.json`` in the repository, plus the process environment
and ``apps/api/.env``. The complete set of pre-existing references is:

* ``schemas/entities.py`` — a comment naming Altruist as an example of what an
  ``account`` entity is a custodial account AT;
* the string ``'altruist'`` as one member of ``positions_source_chk`` /
  ``portfolio_assets.SOURCE_SYSTEMS`` — a vocabulary slot, not an integration;
* a fixture constant in ``scripts/verify_portfolioa2.py``;
* ``services/trading_authority.py``, whose Task 1 note explicitly records that
  the design brief's assumed Altruist/custodian money-movement subsystem does
  not exist;
* design-doc and PROJECT_STATUS prose describing it as planned.

No client. No stub. No env var. No partner credentials.

WHY THERE IS NO MAPPING FUNCTION HERE
──────────────────────────────────────────────────────────────────────────────
The obvious "helpful" move is to write ``_map_altruist_position()`` against a
guessed response shape so that the day credentials arrive there is less to do.
That is worth nothing and costs something real. Altruist's actual field names,
nesting, pagination, quantity-vs-market-value conventions, cost-basis lot
handling and account-identifier semantics are unknown here; a mapping written
against a guess is a mapping that will be rewritten, and in the meantime it
reads to everyone downstream — and to the verification script — as though this
integration is built and merely unconfigured. Phase B's whole point is that the
file importer works WITHOUT this, so there is no pressure to pretend.

What is real below: the credential gate, and a probe that makes an actual
authenticated HTTP request if and only if credentials are present. Everything
past the gate raises :class:`AltruistBlocked` with the exact reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Every credential this integration would need, and which are required together.
# ALL of client id / secret / base url must be present for the probe to even be
# attempted — a half-configured integration is BLOCKED, not "try it and see",
# because a 401 from a missing secret and a 401 from a revoked one are the same
# response and only one of them is a configuration problem the operator can fix
# from the message.
ALTRUIST_ENV_VARS: tuple[str, ...] = (
    "ALTRUIST_CLIENT_ID",
    "ALTRUIST_CLIENT_SECRET",
    "ALTRUIST_BASE_URL",
)

#: The `source_system` an Altruist-sourced position would carry. Already a legal
#: value in `positions_source_chk` — the vocabulary slot has existed since A2.
ALTRUIST_SOURCE_SYSTEM = "altruist"


class AltruistBlocked(RuntimeError):
    """The Altruist integration cannot run. Carries the exact reason.

    A distinct exception type rather than a bool so that a caller cannot ignore
    it by accident, and so the reason travels with the failure instead of being
    reconstructed by whatever catches it.
    """


@dataclass(frozen=True)
class AltruistGate:
    """The result of checking whether Altruist can be used at all.

    ``attempted`` distinguishes the two blocked cases, which are NOT the same
    finding: "no credentials, so nothing was tried" is a provisioning gap, and
    "credentials present, real call made, refused" is a partner-access or
    validity problem. Collapsing them into one boolean loses the only
    information an operator needs to know who to go and ask.
    """

    ok: bool
    attempted: bool
    reason: str
    missing_vars: tuple[str, ...] = ()
    status_code: int | None = None
    detail: str | None = None


def credential_state() -> tuple[bool, tuple[str, ...]]:
    """``(all_present, missing_var_names)`` — reads the environment, nothing else."""
    missing = tuple(v for v in ALTRUIST_ENV_VARS if not os.environ.get(v))
    return (not missing), missing


async def probe(*, timeout: float = 10.0) -> AltruistGate:
    """Check credentials and, if present, make ONE real authenticated request.

    The request is the lightest thing that still proves authentication: a GET of
    the accounts collection with a page size of 1. A "health" endpoint would be
    cheaper and would prove less — an unauthenticated liveness check returns 200
    whether or not our credentials work, which is precisely the question.

    Never raises for a network or HTTP failure: a probe exists to REPORT the
    state, and a probe that throws forces every caller to re-implement the
    reporting. It returns ``ok=False`` with the real status code and the
    response body's opening characters, so that a "partner access not yet
    approved" body is visible verbatim rather than flattened to "403".
    """
    present, missing = credential_state()
    if not present:
        return AltruistGate(
            ok=False,
            attempted=False,
            reason=(
                "Altruist credentials are not configured. Missing environment "
                f"variable(s): {', '.join(missing)}. No call was attempted — "
                "there is nothing to authenticate with."
            ),
            missing_vars=missing,
        )

    base_url = os.environ["ALTRUIST_BASE_URL"].rstrip("/")
    client_id = os.environ["ALTRUIST_CLIENT_ID"]
    client_secret = os.environ["ALTRUIST_CLIENT_SECRET"]

    import httpx  # local import — only needed when credentials actually exist

    url = f"{base_url}/accounts"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                params={"limit": 1},
                headers={
                    "Accept": "application/json",
                    "X-Client-Id": client_id,
                    "Authorization": f"Bearer {client_secret}",
                },
            )
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
        return AltruistGate(
            ok=False,
            attempted=True,
            reason=(
                f"Altruist credentials are present, but the real call to {url} "
                f"failed at the transport layer: {type(exc).__name__}: {exc}"
            ),
            detail=str(exc),
        )

    body = (response.text or "")[:500]
    if response.status_code >= 400:
        return AltruistGate(
            ok=False,
            attempted=True,
            reason=(
                f"Altruist credentials are present and a real call to {url} was "
                f"made, but it was refused: HTTP {response.status_code}. "
                f"Response body (first 500 chars): {body!r}"
            ),
            status_code=response.status_code,
            detail=body,
        )

    return AltruistGate(
        ok=True,
        attempted=True,
        reason=f"Altruist authenticated: HTTP {response.status_code} from {url}",
        status_code=response.status_code,
        detail=body,
    )


async def ingest_positions(conn, org_id: str, **_kwargs):
    """Not implemented, on purpose. Raises :class:`AltruistBlocked`.

    See the module docstring for why this is not a stub written against a
    guessed response shape. When credentials arrive, the work is: run
    :func:`probe` to confirm access, capture the REAL response shape, then map
    it into ``portfolio_assets.create_position`` plus a
    ``portfolio_assets.upsert_external_reference`` row keyed on Altruist's own
    position identifier — the same idempotency mechanism
    ``services.portfolio_import`` already uses and proves, so the pattern is
    settled even though this path is not.
    """
    gate = await probe()
    raise AltruistBlocked(
        gate.reason if not gate.ok else (
            "Altruist now authenticates, but no ingestion mapping has been "
            "written: the real response shape has never been observed. Capture "
            "a live response and map it explicitly — do not guess."
        )
    )
