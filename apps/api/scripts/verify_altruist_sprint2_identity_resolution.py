"""Altruist Sprint 2 verification — household/account identity resolution
against ``portfolio.external_references`` (services/altruist_identity.py).

Pass/fail only, no prompts. Run:

    python3 scripts/verify_altruist_sprint2_identity_resolution.py

Every table this script writes to is counted before the first insert and
again after the last delete; a difference of even one row fails the run.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Task 3 (sandbox smoke test) is BLOCKED, not skipped or faked** — same
  live check as Sprint 1, re-run rather than assumed still true.

* **The Sprint 1 guard is proven to fail FIRST, from Sprint 2's own entry
  point.** ``resolve_identity`` is called against an org+environment with no
  ``altruist_connections`` row before the connected case is ever shown — it
  raises ``AltruistNotConnected`` (not a network error, not a KeyError)
  because ``call_households`` calls ``require_active_connection`` before
  building any request.

* **The HTTP layer is genuinely exercised, not bypassed.** Unlike Sprint 1's
  own verify script (which tested ``persist_refresh`` directly with a
  hand-built ``TokenResponse`` and never actually drove
  ``exchange_code_for_tokens`` through a transport), this script injects an
  ``httpx.MockTransport`` into ``call_households``/``fetch_accounts_for_
  household`` so the real request-building and response-parsing code in
  ``services/altruist_oauth.py`` runs end to end against a synthetic,
  documented-shape payload.

* **Idempotency is proven by literally re-running the same resolution pass
  twice and diffing row counts** — not by inspecting the code and asserting
  it "should" be idempotent.

* **Cross-org isolation runs on ``app_service``, whose ``rolbypassrls`` is
  asserted False first**, on both tables this sprint writes
  (``portfolio.external_references``, ``public.account_import_exceptions``),
  in both directions.

* **[FIND], not silently worked around:** neither
  ``portfolio.external_references.record_type`` nor
  ``public.account_import_exceptions.record_kind`` admits ``'household'`` —
  both deployed CHECK constraints only allow the values enumerated in
  ``services/altruist_identity.py``'s module docstring. Household identity is
  therefore never itself written to either table; this is proven below by
  confirming every written row's discriminator is ``'account'``.

* Teardown is by fixture id, in FK-safe order (exceptions/batch depend on
  households/accounts existing only as JSON, but ``account_import_batches``
  is FK-referenced BY exceptions, so it is deleted last), never a TRUNCATE.
"""

from __future__ import annotations

import asyncio
import glob
import json
import pathlib
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"
TAG = "altr2verify"

CONN_ORG_SANDBOX = "99000000-0000-0000-0000-0000a17f1001"
CONNECTIONS = [CONN_ORG_SANDBOX]

ACCOUNT_ORG_MATCHED = "99000000-0000-0000-0000-0000a17f1101"   # an existing public.accounts row
ACCOUNTS = [ACCOUNT_ORG_MATCHED]
ENTITY_ORG = "99000000-0000-0000-0000-0000a17f1201"
ENTITIES = [ENTITY_ORG]

ALTRUIST_HOUSEHOLD_ID = f"{TAG}-hh-1"
ALTRUIST_ACCOUNT_MATCHED_ID = f"{TAG}-acct-matched"
ALTRUIST_ACCOUNT_UNMATCHED_ID = f"{TAG}-acct-unmatched"

# Pre-seeded crosswalk row for the "already resolved" case.
EXTREF_MATCHED_ID = "99000000-0000-0000-0000-0000a17f1301"

COUNTED = (
    "public.altruist_connections",
    "portfolio.external_references",
    "public.account_import_exceptions",
    "public.account_import_batches",
    "public.accounts",
    "public.entities",
)


# ═══════════════════════════════════════════════════════════════════════════
# Harness
# ═══════════════════════════════════════════════════════════════════════════
class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def ok(self, ref, msg):
        self.rows.append(("PASS", ref, msg))
        print(f"[PASS] {ref}  {msg}")

    def bad(self, ref, msg, detail=""):
        self.rows.append(("FAIL", ref, f"{msg} — {detail}" if detail else msg))
        print(f"[FAIL] {ref}  {msg}" + (f"\n         {detail}" if detail else ""))

    def find(self, ref, msg):
        self.rows.append(("FIND", ref, msg))
        print(f"[FIND] {ref}  {msg}")

    def blocked(self, ref, msg):
        self.rows.append(("BLOCKED", ref, msg))
        print(f"[BLOCKED] {ref}  {msg}")

    def expect(self, ref, condition, msg, detail=""):
        if condition:
            self.ok(ref, msg)
        else:
            self.bad(ref, msg, detail)
        return bool(condition)

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    def summary(self):
        counts: dict[str, int] = {}
        for kind, _, _ in self.rows:
            counts[kind] = counts.get(kind, 0) + 1
        total = len(self.rows)
        print("\n" + "=" * 78)
        print(f"altruist_sprint2_identity_resolution: {counts.get('PASS', 0)}/{total} PASS"
              + "".join(f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


async def scoped(conn, org_id: str) -> None:
    await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org_id)
    await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
async def teardown(conn) -> None:
    await conn.execute(
        "DELETE FROM portfolio.external_references WHERE id = $1::uuid", EXTREF_MATCHED_ID)
    await conn.execute(
        "DELETE FROM public.account_import_exceptions "
        "WHERE raw_row ->> 'external_id' = ANY($1::text[])",
        [ALTRUIST_ACCOUNT_MATCHED_ID, ALTRUIST_ACCOUNT_UNMATCHED_ID],
    )
    await conn.execute(
        "DELETE FROM public.account_import_batches "
        "WHERE org_id = ANY($1::uuid[]) AND custodian_code = 'ALTRUIST'",
        [ORG, OTHER_ORG],
    )
    await conn.execute(
        "DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES)
    await conn.execute(
        "DELETE FROM public.altruist_connections WHERE id = ANY($1::uuid[])", CONNECTIONS)


async def build_fixtures(conn) -> None:
    from services.altruist_oauth import encrypt_secret, last4

    now = datetime.now(timezone.utc)

    await conn.execute(
        """
        INSERT INTO public.altruist_connections (
            id, org_id, environment, status, client_id,
            client_secret_encrypted, client_secret_last4,
            access_token_encrypted, access_token_last4,
            refresh_token_encrypted, refresh_token_last4,
            scope, token_expires_at, last_refreshed_at, connected_by
        ) VALUES (
            $1::uuid, $2::uuid, 'sandbox', 'connected', $3,
            $4, $5, $6, $7, $8, $9,
            'accounts:read', $10, $11, NULL
        )
        """,
        CONN_ORG_SANDBOX, ORG, f"{TAG}-client",
        encrypt_secret(f"{TAG}-secret"), last4(f"{TAG}-secret"),
        encrypt_secret(f"{TAG}-access"), last4(f"{TAG}-access"),
        encrypt_secret(f"{TAG}-refresh"), last4(f"{TAG}-refresh"),
        now + timedelta(hours=1), now,
    )

    await conn.execute(
        """
        INSERT INTO public.entities (id, org_id, entity_type, display_name, status)
        VALUES ($1::uuid, $2::uuid, 'individual', $3, 'active')
        """,
        ENTITY_ORG, ORG, f"{TAG} owner",
    )

    await conn.execute(
        """
        INSERT INTO public.accounts (
            id, org_id, account_number_masked, account_number_hash,
            custodian_code, registration_type, tax_status, primary_entity_id
        ) VALUES ($1::uuid, $2::uuid, $3, $4, 'ALTRUIST', 'INDIVIDUAL', 'TAXABLE', $5::uuid)
        """,
        ACCOUNT_ORG_MATCHED, ORG, f"{TAG}-****1234", f"{TAG}-hash-1234", ENTITY_ORG,
    )

    # Pre-existing crosswalk row: the "already resolved" case.
    await conn.execute(
        """
        INSERT INTO portfolio.external_references
            (id, org_id, source_system, external_id, record_type, record_id)
        VALUES ($1::uuid, $2::uuid, 'ALTRUIST', $3, 'account', $4::uuid)
        """,
        EXTREF_MATCHED_ID, ORG, ALTRUIST_ACCOUNT_MATCHED_ID, ACCOUNT_ORG_MATCHED,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Synthetic Altruist transport — documented-shape /households + /accounts
# ═══════════════════════════════════════════════════════════════════════════
def make_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/households"):
            return httpx.Response(200, json={
                "households": [
                    {"id": ALTRUIST_HOUSEHOLD_ID, "name": f"{TAG} Household"},
                ],
            })
        if request.url.path.endswith("/accounts"):
            household_id = request.url.params.get("household_id")
            assert household_id == ALTRUIST_HOUSEHOLD_ID, (
                f"accounts call must be scoped to the household id from the "
                f"households call, got {household_id!r}"
            )
            return httpx.Response(200, json={
                "accounts": [
                    {"id": ALTRUIST_ACCOUNT_MATCHED_ID, "name": f"{TAG} Matched"},
                    {"id": ALTRUIST_ACCOUNT_UNMATCHED_ID, "name": f"{TAG} Unmatched"},
                ],
            })
        return httpx.Response(404, json={"error": "unexpected path in verify mock"})

    return httpx.MockTransport(handler)


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 (last bullet) — the Sprint 1 guard still fires, from THIS entry point
# ═══════════════════════════════════════════════════════════════════════════
async def check_guard_still_respected(admin) -> None:
    from services.altruist_oauth import AltruistNotConnected
    from services.altruist_identity import resolve_identity

    raised = None
    try:
        # OTHER_ORG has no altruist_connections row at all (fixtures only
        # cover ORG). No mock transport is even passed — if the guard did
        # NOT fire first, this would attempt a real network call and hang
        # or fail with a transport error, not AltruistNotConnected.
        await resolve_identity(admin, org_id=OTHER_ORG, environment="sandbox")
    except AltruistNotConnected as exc:
        raised = exc
    except Exception as exc:  # noqa: BLE001
        raised = exc
    R.expect("G-1", isinstance(raised, AltruistNotConnected),
              "resolve_identity raises AltruistNotConnected (not a network "
              "error) for an org with no altruist_connections row — the "
              "Sprint 1 guard fires before any HTTP call is attempted",
              detail=f"raised={raised!r}")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4a/4b — resolution + idempotency, run twice, diffed
# ═══════════════════════════════════════════════════════════════════════════
async def check_resolution_and_idempotency(admin) -> None:
    from services.altruist_identity import resolve_identity

    transport = make_mock_transport()

    last_seen_0 = await admin.fetchval(
        "SELECT last_seen FROM portfolio.external_references WHERE id = $1::uuid",
        EXTREF_MATCHED_ID)

    before_refs = await admin.fetchval(
        "SELECT count(*) FROM portfolio.external_references WHERE org_id = $1::uuid",
        ORG)
    before_exc = await admin.fetchval(
        "SELECT count(*) FROM public.account_import_exceptions WHERE org_id = $1::uuid",
        ORG)

    result1 = await resolve_identity(admin, org_id=ORG, environment="sandbox", transport=transport)

    R.expect("A-1", result1["households_seen"] == 1,
              "exactly one synthetic household was traversed",
              detail=str(result1))
    R.expect("A-2",
              len(result1["resolved"]) == 1
              and result1["resolved"][0]["altruist_account_id"] == ALTRUIST_ACCOUNT_MATCHED_ID
              and result1["resolved"][0]["hollisworks_account_id"] == ACCOUNT_ORG_MATCHED,
              "the account with a pre-existing external_references row resolves "
              "to the correct EXISTING Hollisworks account uuid",
              detail=str(result1["resolved"]))
    R.expect("A-3",
              len(result1["exceptions"]) == 1
              and result1["exceptions"][0]["altruist_account_id"] == ALTRUIST_ACCOUNT_UNMATCHED_ID,
              "the account with NO existing external_references row lands "
              "exactly one account_import_exceptions row",
              detail=str(result1["exceptions"]))

    exc_row = await admin.fetchrow(
        "SELECT record_kind, reason_code, raw_row FROM public.account_import_exceptions "
        "WHERE id = $1::uuid",
        result1["exceptions"][0]["exception_id"],
    )
    raw = json.loads(exc_row["raw_row"]) if isinstance(exc_row["raw_row"], str) else exc_row["raw_row"]
    R.expect("A-4",
              exc_row["record_kind"] == "account"
              and raw.get("external_id") == ALTRUIST_ACCOUNT_UNMATCHED_ID
              and raw.get("name")
              and raw.get("org_id") == ORG,
              "the exception row's record_kind is 'account' (never 'household' "
              "— the CHECK constraint would refuse it) and raw_row carries "
              "enough for a human reviewer: Altruist id, name, org_id",
              detail=str(dict(exc_row)))

    after_refs_1 = await admin.fetchval(
        "SELECT count(*) FROM portfolio.external_references WHERE org_id = $1::uuid",
        ORG)
    after_exc_1 = await admin.fetchval(
        "SELECT count(*) FROM public.account_import_exceptions WHERE org_id = $1::uuid",
        ORG)
    R.expect("A-5", after_refs_1 == before_refs and after_exc_1 == before_exc + 1,
              "run 1: external_references count unchanged (matched case never "
              "inserts, only updates last_seen), exactly one new exception row",
              detail=f"refs before={before_refs} after={after_refs_1}; "
                     f"exc before={before_exc} after={after_exc_1}")

    # ── Run 2: identical inputs, same mock transport, fresh instance.
    result2 = await resolve_identity(
        admin, org_id=ORG, environment="sandbox", transport=make_mock_transport()
    )

    after_refs_2 = await admin.fetchval(
        "SELECT count(*) FROM portfolio.external_references WHERE org_id = $1::uuid",
        ORG)
    after_exc_2 = await admin.fetchval(
        "SELECT count(*) FROM public.account_import_exceptions WHERE org_id = $1::uuid",
        ORG)
    R.expect("A-6", after_refs_2 == after_refs_1,
              "run 2: external_references row count IDENTICAL to after run 1 — "
              "no duplicate crosswalk row for the same (org, source, external_id, "
              "record_type)", detail=f"after1={after_refs_1} after2={after_refs_2}")
    R.expect("A-7", after_exc_2 == after_exc_1,
              "run 2: account_import_exceptions row count IDENTICAL to after "
              "run 1 — re-running does not create a second exception row for "
              "the same unmatched account",
              detail=f"after1={after_exc_1} after2={after_exc_2}")
    R.expect("A-8",
              result2["exceptions"][0]["exception_id"] == result1["exceptions"][0]["exception_id"],
              "run 2 returns the SAME exception id as run 1 (found, not "
              "re-created)",
              detail=f"run1={result1['exceptions']} run2={result2['exceptions']}")

    last_seen_2 = await admin.fetchval(
        "SELECT last_seen FROM portfolio.external_references WHERE id = $1::uuid",
        EXTREF_MATCHED_ID)
    R.expect("A-9", last_seen_0 is not None and last_seen_2 is not None
              and last_seen_2 > last_seen_0,
              "the matched crosswalk row's last_seen strictly ADVANCED from its "
              "fixture-insert value across the two resolution runs — a real "
              "UPDATE landed, not merely a column that happens to be non-null",
              detail=f"last_seen at fixture insert={last_seen_0}, "
                     f"after both runs={last_seen_2}")

    only_batch = await admin.fetchval(
        "SELECT count(*) FROM public.account_import_batches "
        "WHERE org_id = $1::uuid AND custodian_code = 'ALTRUIST'", ORG)
    R.expect("A-10", only_batch == 1,
              "exactly one account_import_batches row backs BOTH runs — the "
              "batch is reused, not recreated per run",
              detail=f"batch rows={only_batch}")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4c — RLS isolation both directions, both tables this sprint writes
# ═══════════════════════════════════════════════════════════════════════════
async def check_rls(admin, app) -> None:
    bypass = await app.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    who = await app.fetchval("SELECT current_user")
    if not R.expect("R-0", bypass is False,
                     f"the isolation connection is '{who}' with rolbypassrls=False "
                     f"— without this every check below proves nothing",
                     detail=f"rolbypassrls={bypass}"):
        return

    # external_references, scoped to ORG.
    async with app.transaction():
        await scoped(app, ORG)
        own = await app.fetchval(
            "SELECT count(*) FROM portfolio.external_references WHERE id = $1::uuid",
            EXTREF_MATCHED_ID)
    R.expect("R-1", own == 1,
              "app_service scoped to ORG sees its own external_references row",
              detail=f"own={own}")

    async with app.transaction():
        await scoped(app, OTHER_ORG)
        other = await app.fetchval(
            "SELECT count(*) FROM portfolio.external_references WHERE id = $1::uuid",
            EXTREF_MATCHED_ID)
    R.expect("R-2", other == 0,
              "reversed: app_service scoped to OTHER_ORG canNOT see ORG's "
              "external_references row, on the identical query with only the "
              "org GUC changed", detail=f"other={other}")

    refused = False
    try:
        async with app.transaction():
            await scoped(app, OTHER_ORG)
            await app.execute(
                """INSERT INTO portfolio.external_references
                     (org_id, source_system, external_id, record_type, record_id)
                   VALUES ($1::uuid, 'ALTRUIST', $2, 'account', $3::uuid)""",
                ORG, f"{TAG}-xorg-write-attempt", ACCOUNT_ORG_MATCHED)
    except asyncpg.InsufficientPrivilegeError:
        refused = True
    R.expect("R-3", refused,
              "app_service scoped to OTHER_ORG cannot INSERT an "
              "external_references row with org_id=ORG — WITH CHECK is real")

    # account_import_exceptions — seed one OTHER_ORG row directly for the read side.
    other_batch = await admin.fetchval(
        """INSERT INTO public.account_import_batches
               (org_id, custodian_code, source_filename, status)
           VALUES ($1::uuid, 'ALTRUIST', 'rls-check', 'DRY_RUN')
           RETURNING id::text""",
        OTHER_ORG)
    other_exc = await admin.fetchval(
        """INSERT INTO public.account_import_exceptions
               (org_id, batch_id, source_row, record_kind, reason_code, reason, raw_row)
           VALUES ($1::uuid, $2::uuid, 0, 'account', 'altruist_account_unmatched',
                   'rls check fixture', $3::jsonb)
           RETURNING id::text""",
        OTHER_ORG, other_batch, json.dumps({"external_id": f"{TAG}-rls-other"}))

    async with app.transaction():
        await scoped(app, ORG)
        cannot_see = await app.fetchval(
            "SELECT count(*) FROM public.account_import_exceptions WHERE id = $1::uuid",
            other_exc)
    R.expect("R-4", cannot_see == 0,
              "app_service scoped to ORG cannot see OTHER_ORG's "
              "account_import_exceptions row", detail=f"visible={cannot_see}")

    async with app.transaction():
        await scoped(app, OTHER_ORG)
        can_see = await app.fetchval(
            "SELECT count(*) FROM public.account_import_exceptions WHERE id = $1::uuid",
            other_exc)
    R.expect("R-5", can_see == 1,
              "reversed: app_service scoped to OTHER_ORG DOES see its own "
              "account_import_exceptions row on the same query",
              detail=f"visible={can_see}")

    refused2 = False
    try:
        async with app.transaction():
            await scoped(app, ORG)
            await app.execute(
                """INSERT INTO public.account_import_exceptions
                       (org_id, batch_id, source_row, record_kind, reason_code, reason, raw_row)
                   VALUES ($1::uuid, $2::uuid, 0, 'account', 'altruist_account_unmatched',
                           'xorg write attempt', '{}'::jsonb)""",
                OTHER_ORG, other_batch)
    except asyncpg.InsufficientPrivilegeError:
        refused2 = True
    R.expect("R-6", refused2,
              "app_service scoped to ORG cannot INSERT an "
              "account_import_exceptions row with org_id=OTHER_ORG")

    await admin.execute(
        "DELETE FROM public.account_import_exceptions WHERE id = $1::uuid", other_exc)
    await admin.execute(
        "DELETE FROM public.account_import_batches WHERE id = $1::uuid", other_batch)


# ═══════════════════════════════════════════════════════════════════════════
# Task 3 — sandbox smoke test: confirm no credentials, do not attempt a call
# ═══════════════════════════════════════════════════════════════════════════
def check_task3_credentials() -> None:
    try:
        out = subprocess.run(
            ["doppler", "secrets", "--only-names"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        R.blocked("T3", f"could not run `doppler secrets --only-names`: {exc!r} — "
                         "sandbox smoke test cannot be attempted or refuted")
        return
    if out.returncode != 0:
        R.blocked("T3", f"`doppler secrets --only-names` exited {out.returncode}: "
                         f"{out.stderr.strip()} — sandbox smoke test cannot be attempted")
        return
    altruist_names = [ln.strip() for ln in out.stdout.splitlines() if "ALTRUIST" in ln.upper()]
    if altruist_names:
        R.find("T3", f"ALTRUIST-named secrets DO exist in Doppler: {altruist_names} — "
                      "contradicts Sprint 1/2's stated assumption; a live smoke test "
                      "may now be possible but was NOT attempted by this script")
        return
    R.blocked("T3", "no ALTRUIST_* secrets exist in this project's Doppler config "
                     "(`doppler secrets --only-names`, project hollisworks, re-confirmed "
                     "live for Sprint 2) — sandbox smoke test is genuinely blocked; "
                     "no live call was attempted")


# ═══════════════════════════════════════════════════════════════════════════
async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    if admin_url is None:
        print(f"FATAL: cannot reach the database as postgres — {admin_prov}")
        return 2
    if app_url is None:
        print(f"FATAL: cannot reach the database as app_service — {app_prov}. "
              f"Every RLS check would pass vacuously on the postgres DSN")
        return 2
    print(f"admin       : {admin_prov}")
    print(f"app_service : {app_prov}\n")

    check_task3_credentials()

    admin = await connect(admin_url)
    app = await connect(app_url)

    import os
    from cryptography.fernet import Fernet
    os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    pre: dict[str, int] = {}
    try:
        await teardown(admin)
        pre = await counts(admin)
        await build_fixtures(admin)

        await check_guard_still_respected(admin)
        await check_resolution_and_idempotency(admin)
        await check_rls(admin, app)
    except Exception:  # noqa: BLE001
        R.bad("driver", "the run aborted", traceback.format_exc())
    finally:
        try:
            await teardown(admin)
        except Exception:  # noqa: BLE001
            R.bad("teardown", "teardown failed", traceback.format_exc())
        post = await counts(admin)
        drift = {t: (pre.get(t), post.get(t)) for t in COUNTED
                 if pre.get(t) != post.get(t)}
        R.expect("teardown-check", not drift,
                 f"every one of the {len(COUNTED)} tables this script writes to is "
                 f"back at its pre-test row count", detail=str(drift))
        await admin.close()
        await app.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
