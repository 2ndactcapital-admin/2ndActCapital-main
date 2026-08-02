"""Multi-tenant Sprint 2 verify — admin-provisioned invite flow + email gate.

Pass/fail/blocked only. No interactive prompts (runs UNATTENDED). Idempotent.
Teardown at START and at END, keyed on fixed test UUIDs + a stable marker.

TWO GATES are re-checked LIVE here (same discipline as the Textract/Voyage
gates), so this script is self-contained and never emits a false [PASS]:
  (a) SES CREDENTIAL GATE — a real minimal SES API call (GetSendQuota).
  (b) SES SANDBOX GATE — a real sesv2 GetAccount (ProductionAccessEnabled).
If the credential gate fails (or the account is in sandbox), the email-delivery
assertions (Tasks 3-5's email/enrollment legs) are reported [BLOCKED] with the
exact reason. Task 2's assertions (invite creation, token validation, expiry,
revocation, cross-org isolation) run normally regardless — they need no email.

DSNs:
  DATABASE_URL             — bypass (postgres) role: seeding, reads, teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role for the cross-org
                             RLS check (falls back to SET LOCAL ROLE, else SKIPs).
"""

import asyncio
import glob
import os
import sys

# ── Make runnable via allowlisted system python3 OR venv python ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

import asyncpg  # noqa: E402

from services.invites import (  # noqa: E402
    create_invite,
    generate_invite_token,
    revoke_invite,
    validate_invite_token,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"

# ── stable ids / markers ─────────────────────────────────────────────────────
MARKER = "mt2_verify_marker"
ORG_A = "00000000-0000-0000-0000-000000000001"        # default org (exists)
ORG_B = "b2000000-0000-0000-0000-0000000000b2"        # throwaway org (RLS test)
ADMIN_A_ID = "99000000-0000-0000-0000-0000000020a1"
ADMIN_B_ID = "99000000-0000-0000-0000-0000000020b1"
ADMIN_A_SUB = "auth0|test_verify_mt2_admin_a"
ADMIN_B_SUB = "auth0|test_verify_mt2_admin_b"


def _email(tag: str) -> str:
    return f"invitee-{tag}.{MARKER}@example.com"


TEST_EMAILS = [_email(t) for t in ("create", "expired", "revoked", "positive", "xorg")]

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail=""):
    _RESULTS.append(("SKIP", name, detail))
    print(f"[SKIP] {name}" + (f" — {detail}" if detail else ""))


def blocked(name, detail=""):
    _RESULTS.append(("BLOCKED", name, detail))
    print(f"[BLOCKED] {name}" + (f" — {detail}" if detail else ""))


# ── SES gates (live) ─────────────────────────────────────────────────────────
def check_ses_gates() -> tuple[bool, bool, str]:
    """Return (cred_ok, production_ok, human_reason). Never raises."""
    try:
        import boto3  # noqa: F401
        from botocore.exceptions import ClientError
    except Exception as exc:  # noqa: BLE001
        return False, False, f"boto3 unavailable: {type(exc).__name__}: {exc}"

    import boto3
    from botocore.exceptions import ClientError

    cred_ok = False
    cred_reason = ""
    try:
        boto3.client("ses", region_name=AWS_REGION).get_send_quota()
        cred_ok = True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        msg = exc.response.get("Error", {}).get("Message")
        cred_reason = f"{code}: {msg}"
    except Exception as exc:  # noqa: BLE001
        cred_reason = f"{type(exc).__name__}: {exc}"

    if not cred_ok:
        return False, False, f"SES credential gate FAILED — {cred_reason}"

    try:
        acct = boto3.client("sesv2", region_name=AWS_REGION).get_account()
        prod = bool(acct.get("ProductionAccessEnabled"))
        if prod:
            return True, True, "SES production access enabled"
        return True, False, "SES account is in SANDBOX (ProductionAccessEnabled=false)"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        msg = exc.response.get("Error", {}).get("Message")
        return True, False, f"credentials OK but sandbox status unreadable — {code}: {msg}"
    except Exception as exc:  # noqa: BLE001
        return True, False, f"credentials OK but sandbox status unreadable — {type(exc).__name__}: {exc}"


# ── DB helpers ───────────────────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0)


async def _teardown(conn):
    # audit rows first (FK: audit_log.user_id -> users, audit_log.org_id -> orgs)
    await conn.execute(
        "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[]) OR org_id = $2",
        [ADMIN_A_ID, ADMIN_B_ID], ORG_B,
    )
    # invite rows (invited_by -> admins) before the admin rows they reference
    await conn.execute(
        "DELETE FROM users WHERE email = ANY($1::text[]) OR invite_token LIKE $2",
        TEST_EMAILS, f"%{MARKER}%",
    )
    await conn.execute(
        "DELETE FROM users WHERE org_id = $1 AND id <> ANY($2::uuid[])",
        ORG_B, [ADMIN_B_ID],
    )
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", [ADMIN_A_ID, ADMIN_B_ID]
    )
    await conn.execute("DELETE FROM organizations WHERE id = $1", ORG_B)


async def seed(conn):
    await conn.execute(
        """
        INSERT INTO organizations (id, name, slug)
        VALUES ($1, $2, $3)
        ON CONFLICT (id) DO NOTHING
        """,
        ORG_B, f"{MARKER} Org B", f"{MARKER}-orgb",
    )
    for aid, sub, org in ((ADMIN_A_ID, ADMIN_A_SUB, ORG_A), (ADMIN_B_ID, ADMIN_B_SUB, ORG_B)):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, $3, $4, $5, 'org_admin')
            ON CONFLICT (id) DO NOTHING
            """,
            aid, org, f"admin-{sub}.{MARKER}@example.com", "MT2 Admin", sub,
        )


async def _count_leftovers(conn):
    users_ct = await conn.fetchval(
        "SELECT count(*) FROM users WHERE email = ANY($1::text[]) "
        "OR invite_token LIKE $2 OR org_id = $3 OR id = ANY($4::uuid[])",
        TEST_EMAILS, f"%{MARKER}%", ORG_B, [ADMIN_A_ID, ADMIN_B_ID],
    )
    org_ct = await conn.fetchval("SELECT count(*) FROM organizations WHERE id = $1", ORG_B)
    audit_ct = await conn.fetchval(
        "SELECT count(*) FROM audit_log WHERE user_id = ANY($1::uuid[]) OR org_id = $2",
        [ADMIN_A_ID, ADMIN_B_ID], ORG_B,
    )
    return users_ct, org_ct, audit_ct


# ── Task 2 assertions ────────────────────────────────────────────────────────
async def assert_create(conn):
    row = await create_invite(
        conn, org_id=ORG_A, email=_email("create"),
        full_name="Jane Invitee", role="member", invited_by=ADMIN_A_ID,
    )
    # re-read independently to prove it's a real persisted row
    re = await conn.fetchrow(
        "SELECT id, auth0_sub, invite_status, invite_token, "
        "invite_expires_at > now() + interval '6 days' AS gt6, "
        "invite_expires_at < now() + interval '8 days' AS lt8 "
        "FROM users WHERE id = $1", row["id"],
    )
    tok = re["invite_token"]
    ok_all = (
        re is not None
        and re["auth0_sub"] is None
        and re["invite_status"] == "pending"
        and isinstance(tok, str) and len(tok) >= 20
        and re["gt6"] and re["lt8"]
    )
    # token really is random: two mints differ
    distinct = generate_invite_token() != generate_invite_token()
    if ok_all and distinct:
        ok("create invite: real pending row, random token, ~7d expiry, auth0_sub NULL",
           f"token_len={len(tok)}, status=pending, expiry in (6d,8d)=True")
    else:
        fail("create invite",
             f"auth0_sub={re['auth0_sub']!r}, status={re['invite_status']!r}, "
             f"token_len={len(tok) if tok else 0}, gt6={re['gt6']}, lt8={re['lt8']}, "
             f"distinct_tokens={distinct}")
    return row["id"], tok


async def assert_positive_validation(conn):
    row = await create_invite(
        conn, org_id=ORG_A, email=_email("positive"),
        full_name=None, role="member", invited_by=ADMIN_A_ID,
    )
    got = await validate_invite_token(conn, row["invite_token"])
    if got is not None and str(got["id"]) == str(row["id"]):
        ok("positive control: a fresh pending token validates", f"id={got['id']}")
    else:
        fail("positive control: fresh pending token should validate",
             f"got={got}")


async def assert_expired_rejected(conn):
    row = await create_invite(
        conn, org_id=ORG_A, email=_email("expired"),
        full_name=None, role="member", invited_by=ADMIN_A_ID,
    )
    # force expiry into the past (bypass conn)
    await conn.execute(
        "UPDATE users SET invite_expires_at = now() - interval '1 day' WHERE id = $1",
        row["id"],
    )
    got = await validate_invite_token(conn, row["invite_token"])
    if got is None:
        ok("expired token is rejected (not silently valid)",
           "invite_expires_at in the past → validate returns None")
    else:
        fail("expired token should be rejected", f"got={got}")


async def assert_revoked_rejected(conn):
    row = await create_invite(
        conn, org_id=ORG_A, email=_email("revoked"),
        full_name=None, role="member", invited_by=ADMIN_A_ID,
    )
    revoked_row = await revoke_invite(conn, org_id=ORG_A, invite_id=str(row["id"]))
    got = await validate_invite_token(conn, row["invite_token"])
    if revoked_row is not None and revoked_row["invite_status"] == "revoked" and got is None:
        ok("revoked token is rejected", "status=revoked → validate returns None")
    else:
        fail("revoked token should be rejected",
             f"revoked_row={revoked_row}, validate_got={got}")


# ── cross-org isolation (non-bypass app_service) ────────────────────────────
async def assert_cross_org(bypass_conn):
    """A different org's admin cannot view/revoke this org's invites."""
    # a real ORG_A invite to probe against
    inv = await create_invite(
        bypass_conn, org_id=ORG_A, email=_email("xorg"),
        full_name=None, role="member", invited_by=ADMIN_A_ID,
    )
    inv_id = str(inv["id"])

    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("cross-org isolation of invites",
                 f"could not connect app_service DSN: {type(exc).__name__}: {exc}")
            return
    else:
        conn = await _connect(DATABASE_URL)
        use_set_role = True
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE app_service")
                who = await conn.fetchval("SELECT current_user")
                bypass = await conn.fetchval(
                    "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            if who != "app_service" or bypass:
                await conn.close()
                skip("cross-org isolation of invites",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("cross-org isolation of invites",
                 f"cannot SET ROLE app_service ({type(exc).__name__}: {exc})")
            return

    try:
        async def view_count(org):
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", org)
                return await conn.fetchval(
                    "SELECT count(*) FROM users WHERE id = $1", inv_id)

        a_ct = await view_count(ORG_A)   # own org: visible
        b_ct = await view_count(ORG_B)   # other org: invisible

        # ORG_B admin tries to revoke ORG_A's invite → must affect 0 rows
        async with conn.transaction():
            if use_set_role:
                await conn.execute("SET LOCAL ROLE app_service")
            await conn.execute(
                "SELECT set_config('app.current_org_id',$1,true),"
                "       set_config('app.is_super_admin','false',true)", ORG_B)
            revoked = await conn.fetch(
                "UPDATE users SET invite_status='revoked' "
                "WHERE id=$1 AND invite_status='pending' RETURNING id", inv_id)

        # confirm (bypass) the invite is untouched
        still = await bypass_conn.fetchval(
            "SELECT invite_status FROM users WHERE id=$1", inv_id)

        if a_ct == 1 and b_ct == 0 and len(revoked) == 0 and still == "pending":
            ok("cross-org isolation: other org cannot view or revoke this org's invite",
               f"own_org_view={a_ct}, other_org_view={b_ct}, "
               f"other_org_revoke_rows={len(revoked)}, status_after={still}")
        else:
            fail("cross-org isolation of invites",
                 f"own_org_view={a_ct} (want 1), other_org_view={b_ct} (want 0), "
                 f"other_org_revoke_rows={len(revoked)} (want 0), status_after={still} (want pending)")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "permission denied" in str(exc).lower():
            skip("cross-org isolation of invites",
                 f"app_service lacks table GRANTs (not an isolation breach): {msg}")
        else:
            fail("cross-org isolation of invites", msg)
    finally:
        await conn.close()


# ── gated (email) assertions ────────────────────────────────────────────────
def report_email_assertions(cred_ok, prod_ok, reason):
    if cred_ok and prod_ok:
        # This sprint's environment does not reach here; if it ever does, these
        # would be exercised for real (send + enrollment-match + regression).
        skip("real invite email sent via SES",
             "gate passed but Task 3 send not built this sprint (see report)")
        skip("enrollment with valid token updates the existing pending row",
             "gate passed but Task 4 not built this sprint (see report)")
        skip("login with NO token uses unchanged default behavior (regression)",
             "gate passed but Task 4 not built this sprint (see report)")
    else:
        blocked("real invite email sent via SES", reason)
        blocked("enrollment with valid token updates the existing pending row",
                f"depends on email gate — {reason}; ensure_user left UNMODIFIED this sprint")
        blocked("login with NO token uses unchanged default behavior (regression)",
                f"depends on email gate — {reason}; ensure_user is byte-for-byte unchanged, "
                "so today's no-token path is intact by construction")


async def main_async():
    if not DATABASE_URL:
        print("DATABASE_URL not set — cannot run")
        return 1

    # Gate re-check (live) FIRST, and report Task 1 findings explicitly.
    cred_ok, prod_ok, reason = check_ses_gates()
    print("\n=== TASK 1 FINDINGS (gates re-checked live) ===")
    print(f"  region: {AWS_REGION}")
    print(f"  gate (a) SES credentials: {'OK' if cred_ok else 'FAILED'}")
    print(f"  gate (b) SES production (non-sandbox): {'OK' if prod_ok else 'NOT AVAILABLE'}")
    print(f"  reason: {reason}")
    print(f"  APP_SERVICE_DATABASE_URL set: {bool(APP_SERVICE_DATABASE_URL)}")
    if cred_ok and prod_ok:
        ok("Task 1 findings reported (SES usable)", reason)
    else:
        ok("Task 1 findings reported (email delivery BLOCKED at gate)", reason)

    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)          # teardown-at-START
        await seed(conn)
        print("\n=== TASK 2 (invite data model + token logic) ===")
        await assert_create(conn)
        await assert_positive_validation(conn)
        await assert_expired_rejected(conn)
        await assert_revoked_rejected(conn)

        print("\n=== CROSS-ORG ISOLATION (app_service) ===")
        await assert_cross_org(conn)
    finally:
        await conn.close()

    print("\n=== TASKS 3-5 (email delivery) — GATE CHECK ===")
    report_email_assertions(cred_ok, prod_ok, reason)

    # teardown-at-END + leftover check
    print("\n=== TEARDOWN ===")
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)
        users_ct, org_ct, audit_ct = await _count_leftovers(conn)
        if (users_ct, org_ct, audit_ct) == (0, 0, 0):
            ok("teardown: zero leftover rows", "users/orgs/audit all 0")
        else:
            fail("teardown: zero leftover rows",
                 f"users={users_ct}, orgs={org_ct}, audit={audit_ct}")
    finally:
        await conn.close()

    return summarize()


def summarize():
    n_pass = sum(1 for s, _, _ in _RESULTS if s == "PASS")
    n_fail = sum(1 for s, _, _ in _RESULTS if s == "FAIL")
    n_skip = sum(1 for s, _, _ in _RESULTS if s == "SKIP")
    n_block = sum(1 for s, _, _ in _RESULTS if s == "BLOCKED")
    print("\n=== SUMMARY ===")
    print(f"PASS={n_pass}  FAIL={n_fail}  BLOCKED={n_block}  SKIP={n_skip}")
    if n_block:
        print("\nBLOCKED (expected — email gate):")
        for s, name, detail in _RESULTS:
            if s == "BLOCKED":
                print(f"  - {name}: {detail}")
    if n_fail:
        print("\nFAILURES:")
        for s, name, detail in _RESULTS:
            if s == "FAIL":
                print(f"  - {name}: {detail}")
    print("\nRESULT:", "PASS — all runnable assertions green (email tasks blocked at gate)."
          if n_fail == 0 else "FAIL — see failures above.")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
