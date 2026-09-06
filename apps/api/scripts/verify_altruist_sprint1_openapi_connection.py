"""Altruist Sprint 1 verification — OAuth2 authorization-code scaffold +
per-tenant credential storage (altruist_connections / altruist_oauth_states).

Pass/fail only, no prompts. Run:

    python3 scripts/verify_altruist_sprint1_openapi_connection.py

Every table this script writes to is counted before the first insert and
again after the last delete; a difference of even one row fails the run.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Task 3 (sandbox smoke test) is BLOCKED, not skipped or faked.** No
  Altruist sandbox/production credentials exist anywhere in this project's
  Doppler config (confirmed live via ``doppler secrets --only-names`` below,
  not assumed from the sprint prompt). No live HTTP call to Altruist is ever
  attempted by this script.

* **The pre-connection guard is proven to FAIL first.** ``require_active_
  connection``/``call_households`` are called against an org+environment
  with NO ``altruist_connections`` row before the connected case is ever
  shown — the negative is not inferred from the positive.

* **Cross-org isolation runs on ``app_service``, whose ``rolbypassrls`` is
  asserted False FIRST.** Without that assertion every isolation check below
  it proves nothing. Both tables (``altruist_connections`` and
  ``altruist_oauth_states``) are proven in BOTH directions — org A cannot
  read or write org B's row, and org A's own row IS readable on the same
  connection, same table, same script.

* **Token refresh persistence is proven by an INDEPENDENT re-read.**
  ``persist_refresh`` is forced with a synthetic ``TokenResponse`` (no live
  call), then a brand new connection — not the one that wrote it — re-reads
  the row and confirms the new (decrypted) token and expiry actually landed.

* **The four CSRF-state rejection cases share one request shape.** Missing,
  expired, already-used, and cross-org states are each consumed with the
  identical ``consume_oauth_state(conn, org_id=ORG, state=...)`` call,
  differing only in which state string is passed — plus one control case
  where a genuinely valid state succeeds, so the four failures are shown
  against a call shape that is known to work. All four failures are
  confirmed to raise the SAME exception class (``AltruistOAuthError``) with
  no structured discriminator a caller could branch on — which is the real
  CSRF property ("must not leak *which* reason"). The exact message text
  does differ between expired/used/invalid, which is recorded as a [FIND],
  not silently treated as a pass.

* Teardown is by fixture id, in FK-safe order, never a TRUNCATE.
"""

from __future__ import annotations

import asyncio
import glob
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

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"
TAG = "altr1verify"

# ── fixture ids ─────────────────────────────────────────────────────────────
CONN_ORG_SANDBOX = "99000000-0000-0000-0000-0000a17f0001"       # ORG, connected
CONN_OTHERORG_SANDBOX = "99000000-0000-0000-0000-0000a17f0002"  # OTHER_ORG, connected
CONNECTIONS = [CONN_ORG_SANDBOX, CONN_OTHERORG_SANDBOX]

ST_VALID_ID = "99000000-0000-0000-0000-0000a17f0011"
ST_EXPIRED_ID = "99000000-0000-0000-0000-0000a17f0012"
ST_USED_ID = "99000000-0000-0000-0000-0000a17f0013"
ST_XORG_ID = "99000000-0000-0000-0000-0000a17f0014"
STATES = [ST_VALID_ID, ST_EXPIRED_ID, ST_USED_ID, ST_XORG_ID]

TOK_VALID = f"{TAG}_valid"
TOK_EXPIRED = f"{TAG}_expired"
TOK_USED = f"{TAG}_used"
TOK_XORG = f"{TAG}_xorg"
TOK_MISSING = f"{TAG}_never_inserted"
TOKENS = [TOK_VALID, TOK_EXPIRED, TOK_USED, TOK_XORG]

# No real key exists in Doppler for this interim application-layer encryption
# layer (see altruist_oauth.py module docstring, [FIND]) — a synthetic
# session-local key is sufficient to exercise the encrypt/decrypt plumbing.
import base64  # noqa: E402
import os  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

COUNTED = (
    "public.altruist_connections",
    "public.altruist_oauth_states",
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
        print(f"altruist_sprint1_openapi_connection: {counts.get('PASS', 0)}/{total} PASS"
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
        "DELETE FROM public.altruist_oauth_states WHERE id = ANY($1::uuid[])", STATES)
    await conn.execute(
        "DELETE FROM public.altruist_connections WHERE id = ANY($1::uuid[])", CONNECTIONS)


async def build_fixtures(conn) -> None:
    from services.altruist_oauth import encrypt_secret, last4

    now = datetime.now(timezone.utc)

    for cid, org, client_id, access_tok, refresh_tok in (
        (CONN_ORG_SANDBOX, ORG, f"{TAG}-client-org", f"{TAG}-access-org", f"{TAG}-refresh-org"),
        (CONN_OTHERORG_SANDBOX, OTHER_ORG, f"{TAG}-client-other", f"{TAG}-access-other", f"{TAG}-refresh-other"),
    ):
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
            cid, org, client_id,
            encrypt_secret(f"{TAG}-secret"), last4(f"{TAG}-secret"),
            encrypt_secret(access_tok), last4(access_tok),
            encrypt_secret(refresh_tok), last4(refresh_tok),
            now + timedelta(hours=1), now,
        )

    # Valid, unexpired, unused — the control case and the [3] target row's
    # sibling for [4]'s single-use flow.
    await conn.execute(
        """INSERT INTO public.altruist_oauth_states
             (id, org_id, environment, state, initiated_by, expires_at)
           VALUES ($1::uuid, $2::uuid, 'sandbox', $3, NULL, $4)""",
        ST_VALID_ID, ORG, TOK_VALID, now + timedelta(minutes=10))

    # Expired: unused, but expires_at is in the past.
    await conn.execute(
        """INSERT INTO public.altruist_oauth_states
             (id, org_id, environment, state, initiated_by, expires_at)
           VALUES ($1::uuid, $2::uuid, 'sandbox', $3, NULL, $4)""",
        ST_EXPIRED_ID, ORG, TOK_EXPIRED, now - timedelta(minutes=1))

    # Already used: used_at is set.
    await conn.execute(
        """INSERT INTO public.altruist_oauth_states
             (id, org_id, environment, state, initiated_by, expires_at, used_at)
           VALUES ($1::uuid, $2::uuid, 'sandbox', $3, NULL, $4, $5)""",
        ST_USED_ID, ORG, TOK_USED, now + timedelta(minutes=10), now - timedelta(minutes=1))

    # Cross-org: valid and unused, but scoped to OTHER_ORG.
    await conn.execute(
        """INSERT INTO public.altruist_oauth_states
             (id, org_id, environment, state, initiated_by, expires_at)
           VALUES ($1::uuid, $2::uuid, 'sandbox', $3, NULL, $4)""",
        ST_XORG_ID, OTHER_ORG, TOK_XORG, now + timedelta(minutes=10))


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
    names = out.stdout
    altruist_names = [ln.strip() for ln in names.splitlines() if "ALTRUIST" in ln.upper()]
    if altruist_names:
        R.find("T3", f"ALTRUIST-named secrets DO exist in Doppler: {altruist_names} — "
                      "this contradicts the sprint's stated assumption; a live smoke "
                      "test may now be possible but was NOT attempted by this script "
                      "(would require knowing which of these map to CLIENT_ID/SECRET)")
        return
    R.blocked("T3", "no ALTRUIST_* secrets exist in this project's Doppler config "
                     "(`doppler secrets --only-names`, project hollisworks) — sandbox "
                     "smoke test is genuinely blocked pending Sprint 0 credential "
                     "provisioning; no live call was attempted")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4a — the pre-connection guard fails BEFORE the connected case
# ═══════════════════════════════════════════════════════════════════════════
async def check_task4a_guard(admin) -> None:
    from services.altruist_oauth import AltruistNotConnected, call_households, require_active_connection

    # No altruist_connections row exists for ORG/production (fixtures only
    # cover ORG/sandbox) — the negative case, proven FIRST.
    raised = None
    try:
        await require_active_connection(admin, org_id=ORG, environment="production")
    except AltruistNotConnected as exc:
        raised = exc
    R.expect("4a-1", raised is not None,
              "require_active_connection raises AltruistNotConnected for an "
              "org+environment with no altruist_connections row",
              detail=f"raised={raised}")

    raised2 = None
    try:
        await call_households(admin, org_id=ORG, environment="production")
    except AltruistNotConnected as exc:
        raised2 = exc
    except Exception as exc:  # noqa: BLE001
        raised2 = exc
    R.expect("4a-2", isinstance(raised2, AltruistNotConnected),
              "call_households raises AltruistNotConnected (not NotImplementedError, "
              "not any other error) when no connection exists — the guard runs "
              "before any network call is even attempted",
              detail=f"raised={raised2!r}")

    # THEN the connected case, on ORG/sandbox (the fixture row).
    row = await require_active_connection(admin, org_id=ORG, environment="sandbox")
    R.expect("4a-3", row is not None and row.get("status") == "connected"
              and str(row.get("id")) == CONN_ORG_SANDBOX,
              "require_active_connection returns the real connection row once "
              "one exists",
              detail=str(row))

    # call_households on the connected org+environment must reach PAST the
    # guard — the ONLY thing failing now is the un-implemented HTTP call.
    guard_passed = False
    try:
        await call_households(admin, org_id=ORG, environment="sandbox")
    except AltruistNotConnected:
        guard_passed = False
    except NotImplementedError:
        guard_passed = True
    R.expect("4a-4", guard_passed,
              "call_households on a genuinely connected org+environment gets PAST "
              "the guard (raises NotImplementedError, not AltruistNotConnected) — "
              "proving [4a-2]'s rejection was the guard, not an unconditional "
              "failure")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4b — RLS isolation, both directions, both tables, on app_service
# ═══════════════════════════════════════════════════════════════════════════
async def check_task4b_rls(admin, app) -> None:
    bypass = await app.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    who = await app.fetchval("SELECT current_user")
    if not R.expect("4b-0", bypass is False,
                     f"the isolation connection is '{who}' with rolbypassrls=False "
                     f"— without this every check below proves nothing",
                     detail=f"rolbypassrls={bypass}"):
        return

    # ── altruist_connections, scoped to ORG.
    async with app.transaction():
        await scoped(app, ORG)
        own = await app.fetchval(
            "SELECT count(*) FROM public.altruist_connections WHERE id = $1::uuid",
            CONN_ORG_SANDBOX)
        other = await app.fetchval(
            "SELECT count(*) FROM public.altruist_connections WHERE id = $1::uuid",
            CONN_OTHERORG_SANDBOX)
    R.expect("4b-1", own == 1 and other == 0,
              "app_service scoped to ORG sees its own altruist_connections row and "
              "NOT the other org's, on the identical query with only the org GUC "
              "changed", detail=f"own={own} other={other}")

    # ── altruist_connections, scoped to OTHER_ORG — the reverse direction.
    async with app.transaction():
        await scoped(app, OTHER_ORG)
        own2 = await app.fetchval(
            "SELECT count(*) FROM public.altruist_connections WHERE id = $1::uuid",
            CONN_OTHERORG_SANDBOX)
        other2 = await app.fetchval(
            "SELECT count(*) FROM public.altruist_connections WHERE id = $1::uuid",
            CONN_ORG_SANDBOX)
    R.expect("4b-2", own2 == 1 and other2 == 0,
              "reversed: app_service scoped to OTHER_ORG sees its own row and not "
              "ORG's — isolation holds in both directions, not just one",
              detail=f"own={own2} other={other2}")

    # ── altruist_connections write refusal.
    from services.altruist_oauth import encrypt_secret, last4
    refused = False
    try:
        async with app.transaction():
            await scoped(app, ORG)
            await app.execute(
                """INSERT INTO public.altruist_connections (
                       org_id, environment, status, client_id,
                       client_secret_encrypted, client_secret_last4,
                       refresh_token_encrypted, refresh_token_last4
                   ) VALUES ($1::uuid, 'production', 'connected', 'x', $2, $3, $4, $5)""",
                OTHER_ORG, encrypt_secret("x"), last4("x"),
                encrypt_secret("x"), last4("x"))
    except asyncpg.InsufficientPrivilegeError:
        refused = True
    R.expect("4b-3", refused,
              "app_service scoped to ORG cannot INSERT an altruist_connections row "
              "with org_id=OTHER_ORG — the policy's WITH CHECK is real, not just USING")

    # ── altruist_oauth_states, scoped to ORG.
    async with app.transaction():
        await scoped(app, ORG)
        own3 = await app.fetchval(
            "SELECT count(*) FROM public.altruist_oauth_states WHERE id = $1::uuid",
            ST_VALID_ID)
        other3 = await app.fetchval(
            "SELECT count(*) FROM public.altruist_oauth_states WHERE id = $1::uuid",
            ST_XORG_ID)
    R.expect("4b-4", own3 == 1 and other3 == 0,
              "app_service scoped to ORG sees its own altruist_oauth_states row and "
              "NOT the OTHER org's", detail=f"own={own3} other={other3}")

    # ── altruist_oauth_states, scoped to OTHER_ORG — the reverse direction.
    async with app.transaction():
        await scoped(app, OTHER_ORG)
        own4 = await app.fetchval(
            "SELECT count(*) FROM public.altruist_oauth_states WHERE id = $1::uuid",
            ST_XORG_ID)
        other4 = await app.fetchval(
            "SELECT count(*) FROM public.altruist_oauth_states WHERE id = $1::uuid",
            ST_VALID_ID)
    R.expect("4b-5", own4 == 1 and other4 == 0,
              "reversed: app_service scoped to OTHER_ORG sees its own state row and "
              "not ORG's", detail=f"own={own4} other={other4}")

    # ── altruist_oauth_states write refusal.
    refused2 = False
    try:
        async with app.transaction():
            await scoped(app, ORG)
            await app.execute(
                """INSERT INTO public.altruist_oauth_states
                     (org_id, environment, state, expires_at)
                   VALUES ($1::uuid, 'sandbox', $2, now() + interval '10 minutes')""",
                OTHER_ORG, f"{TAG}_xorg_write_attempt")
    except asyncpg.InsufficientPrivilegeError:
        refused2 = True
    R.expect("4b-6", refused2,
              "app_service scoped to ORG cannot INSERT an altruist_oauth_states row "
              "with org_id=OTHER_ORG")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4c — token refresh persistence, proven by an independent re-read
# ═══════════════════════════════════════════════════════════════════════════
async def check_task4c_refresh(admin, admin_url: str) -> None:
    from services.altruist_oauth import TokenResponse, decrypt_secret, last4, persist_refresh

    before = await admin.fetchrow(
        """SELECT access_token_last4, refresh_token_last4, token_expires_at,
                  last_refreshed_at
           FROM public.altruist_connections WHERE id = $1::uuid""",
        CONN_ORG_SANDBOX)

    new_access = f"{TAG}-refreshed-access-9f3"
    new_refresh = f"{TAG}-refreshed-refresh-9f3"
    synthetic = TokenResponse(access_token=new_access, refresh_token=new_refresh,
                               expires_in=3600, scope="accounts:read")

    await persist_refresh(admin, connection_id=CONN_ORG_SANDBOX, tokens=synthetic)

    # A GENUINELY independent connection — not `admin`, not `app` — proves the
    # write committed rather than living only in the writer's own session.
    independent = await connect(admin_url)
    try:
        after = await independent.fetchrow(
            """SELECT access_token_encrypted, access_token_last4,
                      refresh_token_encrypted, refresh_token_last4,
                      token_expires_at, last_refreshed_at, status
               FROM public.altruist_connections WHERE id = $1::uuid""",
            CONN_ORG_SANDBOX)
    finally:
        await independent.close()

    R.expect("4c-1", after is not None
              and after["access_token_last4"] == last4(new_access)
              and after["refresh_token_last4"] == last4(new_refresh),
              "an independent connection reads back the new last4 markers for "
              "both tokens", detail=str(dict(after) if after else None))
    R.expect("4c-2",
              after is not None
              and decrypt_secret(after["access_token_encrypted"]) == new_access
              and decrypt_secret(after["refresh_token_encrypted"]) == new_refresh,
              "the FULL encrypted value decrypts back to the exact synthetic token "
              "text — not just the redaction-safe last4 marker")
    R.expect("4c-3",
              after is not None and before is not None
              and after["token_expires_at"] > before["token_expires_at"]
              and after["last_refreshed_at"] > before["last_refreshed_at"],
              "token_expires_at and last_refreshed_at both advanced past their "
              "pre-refresh values",
              detail=f"before={dict(before) if before else None} "
                     f"after={dict(after) if after else None}")
    R.expect("4c-4", after is not None and after["status"] == "connected",
              "status remains 'connected' after a routine refresh")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4d — consume_oauth_state: four rejections + the control that succeeds
# ═══════════════════════════════════════════════════════════════════════════
async def check_task4d_state(admin) -> None:
    from services.altruist_oauth import AltruistOAuthError, consume_oauth_state

    exceptions: dict[str, Exception] = {}

    async def expect_rejected(ref, label, token, msg):
        exc = None
        try:
            await consume_oauth_state(admin, org_id=ORG, state=token)
        except AltruistOAuthError as e:
            exc = e
        R.expect(ref, exc is not None, msg, detail=f"raised={exc!r}")
        if exc is not None:
            exceptions[label] = exc
        return exc

    await expect_rejected("4d-1", "missing", TOK_MISSING,
                           "consume_oauth_state rejects a state that was never "
                           "inserted (missing)")
    await expect_rejected("4d-2", "expired", TOK_EXPIRED,
                           "consume_oauth_state rejects a state past its expires_at")
    await expect_rejected("4d-3", "used", TOK_USED,
                           "consume_oauth_state rejects a state whose used_at is "
                           "already set")
    await expect_rejected("4d-4", "xorg", TOK_XORG,
                           "consume_oauth_state rejects a state that belongs to a "
                           "DIFFERENT org than the one requesting consumption, on "
                           "the identical request shape")

    # ── Same exception CLASS for all four — the real CSRF property: a caller
    #    (a future callback endpoint) catching AltruistOAuthError cannot branch
    #    on exception TYPE to learn which of the four reasons applied.
    classes = {label: type(exc) for label, exc in exceptions.items()}
    R.expect("4d-5", len(exceptions) == 4 and len(set(classes.values())) == 1
              and next(iter(set(classes.values()))) is AltruistOAuthError,
              "all four rejection cases raise the exact same exception class "
              "(AltruistOAuthError), not four distinguishable subclasses a "
              "handler could branch on", detail=str(classes))

    # [FIND] the message TEXT does differ by reason even though the class does
    # not — worth recording plainly rather than silently treated as identical.
    messages = {label: str(exc) for label, exc in exceptions.items()}
    distinct_msgs = len(set(messages.values()))
    if distinct_msgs > 1:
        R.find("4d-note",
               f"the exception CLASS is uniform (AltruistOAuthError) as designed, "
               f"but the message TEXT is not identical across all four reasons: "
               f"{messages}. missing/xorg happen to share the same generic "
               f"message ('Invalid or unrecognized...') because both hit the "
               f"same 'row is None' branch, but expired/used each carry their own "
               f"distinct text. A caller that logs or surfaces this message "
               f"verbatim (rather than catching the class and emitting one fixed "
               f"string) would leak more than the class-level design intends.")

    # ── The control: an identical call shape, but with a genuinely valid,
    #    unexpired, unused state — succeeds.
    result = await consume_oauth_state(admin, org_id=ORG, state=TOK_VALID)
    R.expect("4d-6", result == {"environment": "sandbox"},
              "the same call shape succeeds and returns the state's environment "
              "when the state is genuinely valid", detail=str(result))

    persisted = await admin.fetchval(
        "SELECT used_at FROM public.altruist_oauth_states WHERE id = $1::uuid",
        ST_VALID_ID)
    R.expect("4d-7", persisted is not None,
              "consuming the valid state persisted used_at — re-read on the same "
              "connection confirms the single-use mark actually wrote")

    # Reusing the now-consumed valid state must fail exactly like the
    # already-used case above (single-use is real, not merely first-call-only).
    reused = None
    try:
        await consume_oauth_state(admin, org_id=ORG, state=TOK_VALID)
    except AltruistOAuthError as e:
        reused = e
    R.expect("4d-8", reused is not None,
              "consuming the SAME state a second time is rejected — the control "
              "case's own state cannot be replayed", detail=f"raised={reused!r}")


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

    pre: dict[str, int] = {}
    try:
        await teardown(admin)
        pre = await counts(admin)
        await build_fixtures(admin)

        await check_task4a_guard(admin)
        await check_task4b_rls(admin, app)
        await check_task4c_refresh(admin, admin_url)
        await check_task4d_state(admin)
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
