"""verify_sprint24.py — Sprint 24 white-label config.

Column names are taken directly from docs/schema_snapshot.sql:

  org_settings: id, org_id, setting_key, setting_value (jsonb NOT NULL),
    category, is_public, updated_at, updated_by, created_at
    UNIQUE org_settings_org_id_setting_key_key: (org_id, setting_key)
    NOT bi-temporal — plain upsert, no valid_from / valid_to.
  organizations: id, name, slug (UNIQUE), created_at
  users: id, org_id, email, full_name, auth0_sub, role (free text, no CHECK)

Assertions:
   1. org_settings table + unique constraint match the snapshot.
   2. get_setting returns the stored value for an existing key.
   3. get_setting falls back to DEFAULT_SETTINGS on a fresh org.
   4. set_setting upserts (create then update the same key).
   5. A 'member' CANNOT call set_setting (permission denied).
   6. An 'org_admin' CAN set their own org's settings.
   7. An 'org_admin' CANNOT set a different org's settings.
   8. A 'super_admin' CAN set settings on ANY org.
   9. THE SWEEP — zero hardcoded brand literals outside the allowed files.
  10. Teardown: zero leftover rows; constraint intact.

No fixtures touch posted ledger data, so no triggers are disabled here (the
Sprint 22 disable/re-enable dance is unnecessary and would be a needless
privilege escalation). Assertion 10 re-checks the unique index regardless.
"""
import asyncio
import os
import re
import subprocess
import sys
import uuid

import asyncpg

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

REPO_ROOT = os.path.dirname(os.path.dirname(API_DIR))

from services.org_settings import (  # noqa: E402
    DEFAULT_SETTINGS,
    SettingsPermissionError,
    get_all_settings,
    get_setting,
    set_setting,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
ORG_ID = "00000000-0000-0000-0000-000000000001"

PASS = "\033[32m[Y]\033[0m"
FAIL = "\033[31m[N]\033[0m"

results = []


def record(label, ok, note=""):
    results.append((label, ok, note))
    icon = PASS if ok else FAIL
    suffix = f"  ({note})" if note else ""
    print(f"  {icon} {label}{suffix}")


# ── The sweep gate ────────────────────────────────────────────────────────
#
# Scope: application source that makes rendering decisions. Static tenant
# artwork under apps/web/public/**  is image DATA (the client's own logo files,
# equivalent to an R2 upload) and is excluded — see ALLOWED below, which names
# every exclusion explicitly so nothing is hidden.

NAME_RE = r"2nd ?Act|2ndAct"
HEX_RE = (
    r"#?(1B2B4B|C5A880|E8D5A3|9AA6BF|FAF9F6|F5F1EB|FFFFFF|"
    r"0F172A|334155|64748B|E2E8F0)"
)

SWEEP_INCLUDES = [
    "--include=*.py", "--include=*.js", "--include=*.jsx",
    "--include=*.ts", "--include=*.tsx", "--include=*.css",
    "--include=*.json", "--include=*.html", "--include=*.mjs",
]

# The only files permitted to contain literal brand values.
ALLOWED = (
    # 1. DEFAULT_SETTINGS — this IS the default data, not app logic.
    "apps/api/services/org_settings.py",
    # 2. This sprint's seed SQL — it IS the seed data.
    "docs/sprint24_seed.sql",
    # 3. This verify script — it contains the patterns it greps for.
    "apps/api/scripts/verify_sprint24.py",
    "scripts/brand_sweep_grep.sh",
)

# Tenant artwork: static SVGs served by URL, so CSS variables cannot apply.
# Reported separately so the exclusion is visible, never silent.
ASSET_PREFIX = "apps/web/public/"


def _grep(pattern, extra_flags=()):
    cmd = [
        "grep", "-rInE", *extra_flags, pattern,
        "apps/", "scripts/", *SWEEP_INCLUDES,
    ]
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True
    )
    lines = []
    for line in proc.stdout.splitlines():
        if "node_modules" in line or "/.next/" in line or "/venv/" in line:
            continue
        lines.append(line)
    return lines


def run_sweep():
    """Return (violations, allowed_hits, asset_hits)."""
    hits = _grep(NAME_RE) + _grep(HEX_RE, extra_flags=("-i",))

    violations, allowed_hits, asset_hits = [], [], []
    for line in hits:
        path = line.split(":", 1)[0]
        if path in ALLOWED:
            allowed_hits.append(line)
        elif path.startswith(ASSET_PREFIX):
            asset_hits.append(line)
        else:
            violations.append(line)
    return violations, allowed_hits, asset_hits


async def main():
    if not DATABASE_URL:
        print("[N] SKIP — DATABASE_URL not set")
        return

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    # Fresh orgs/users per run; ids recorded so teardown is exhaustive.
    org_a = str(uuid.uuid4())      # tenant under test
    org_b = str(uuid.uuid4())      # a *different* tenant
    org_platform = str(uuid.uuid4())
    org_ids = [org_a, org_b, org_platform]

    member_id = str(uuid.uuid4())
    org_admin_id = str(uuid.uuid4())
    super_admin_id = str(uuid.uuid4())
    user_ids = [member_id, org_admin_id, super_admin_id, TEST_USER_ID]

    teardown_failed = False

    async def teardown():
        nonlocal teardown_failed
        try:
            await conn.execute(
                "DELETE FROM org_settings WHERE org_id = ANY($1::uuid[])", org_ids
            )
            await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::uuid[])", user_ids
            )
            await conn.execute(
                "DELETE FROM organizations WHERE id = ANY($1::uuid[])", org_ids
            )
        except Exception as exc:
            teardown_failed = True
            print(f"  [teardown error] {exc}", file=sys.stderr)

    # Teardown-at-start: a previous crashed run must not colour this one.
    await teardown()
    teardown_failed = False

    try:
        # ── Fixtures ──────────────────────────────────────────────────────
        for oid, name, slug in (
            (org_a, "Verify Tenant A", f"verify-a-{org_a[:8]}"),
            (org_b, "Verify Tenant B", f"verify-b-{org_b[:8]}"),
            (org_platform, "Verify Platform", f"verify-p-{org_platform[:8]}"),
        ):
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3) "
                "ON CONFLICT (id) DO NOTHING",
                oid, name, slug,
            )

        for uid, oid, role, sub in (
            (member_id, org_a, "member", f"auth0|verify24_member_{member_id[:8]}"),
            (org_admin_id, org_a, "org_admin", f"auth0|verify24_orgadmin_{org_admin_id[:8]}"),
            (super_admin_id, org_platform, "super_admin", f"auth0|verify24_super_{super_admin_id[:8]}"),
        ):
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role) "
                "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (auth0_sub) DO NOTHING",
                uid, oid, f"{uid[:8]}@verify.local", "Verify User", sub, role,
            )

        member = {"id": member_id, "org_id": org_a, "role": "member"}
        org_admin = {"id": org_admin_id, "org_id": org_a, "role": "org_admin"}
        super_admin = {
            "id": super_admin_id, "org_id": org_platform, "role": "super_admin"
        }

        # ── 1. Schema matches the snapshot ────────────────────────────────
        try:
            cols = {
                r["column_name"]: (r["data_type"], r["is_nullable"])
                for r in await conn.fetch(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'org_settings' AND table_schema = 'public'"
                )
            }
            uniq = await conn.fetchval(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'org_settings_org_id_setting_key_key'"
            )
            schema_ok = (
                cols.get("setting_value") == ("jsonb", "NO")
                and "setting_key" in cols
                and "category" in cols
                and "is_public" in cols
                and uniq == 1
            )
            record(
                "org_settings table + unique constraint match the snapshot",
                schema_ok,
                f"setting_value={cols.get('setting_value')}, unique={uniq}",
            )
        except Exception as e:
            record("org_settings table + unique constraint match the snapshot", False, str(e))

        # ── 2. get_setting returns a stored value ─────────────────────────
        try:
            await set_setting(
                conn, org_a, "brand.name", "Verify Tenant A",
                super_admin_id, principal=super_admin,
            )
            got = await get_setting(conn, org_a, "brand.name")
            record(
                "get_setting returns the stored value for an existing key",
                got == "Verify Tenant A", f"got={got!r}",
            )
        except Exception as e:
            record("get_setting returns the stored value for an existing key", False, str(e))

        # ── 3. Fallback to DEFAULT_SETTINGS on a fresh org ────────────────
        try:
            # org_b has no rows at all — every key must fall back.
            got = await get_setting(conn, org_b, "brand.color.navy")
            expected = DEFAULT_SETTINGS["brand.color.navy"]
            count_b = await conn.fetchval(
                "SELECT count(*) FROM org_settings WHERE org_id = $1", org_b
            )
            all_b = await get_all_settings(conn, org_b)
            record(
                "get_setting falls back to DEFAULT_SETTINGS for a missing key",
                got == expected and count_b == 0 and len(all_b) == len(DEFAULT_SETTINGS),
                f"got={got!r}, rows={count_b}, bulk_keys={len(all_b)}",
            )
        except Exception as e:
            record("get_setting falls back to DEFAULT_SETTINGS for a missing key", False, str(e))

        # ── 4. Upsert: create then update the same key ────────────────────
        try:
            await set_setting(
                conn, org_a, "locale.base_currency", "USD",
                super_admin_id, principal=super_admin,
            )
            first = await get_setting(conn, org_a, "locale.base_currency")
            await set_setting(
                conn, org_a, "locale.base_currency", "EUR",
                super_admin_id, principal=super_admin,
            )
            second = await get_setting(conn, org_a, "locale.base_currency")
            rows = await conn.fetchval(
                "SELECT count(*) FROM org_settings "
                "WHERE org_id = $1 AND setting_key = 'locale.base_currency'",
                org_a,
            )
            record(
                "set_setting upserts correctly (create then update, one row)",
                first == "USD" and second == "EUR" and rows == 1,
                f"{first!r} -> {second!r}, rows={rows}",
            )
        except Exception as e:
            record("set_setting upserts correctly (create then update, one row)", False, str(e))

        # ── 5. member is denied ───────────────────────────────────────────
        try:
            await set_setting(
                conn, org_a, "brand.name", "Hacked",
                member_id, principal=member,
            )
            record("A 'member' CANNOT call set_setting", False, "no exception raised")
        except SettingsPermissionError as e:
            unchanged = await get_setting(conn, org_a, "brand.name")
            record(
                "A 'member' CANNOT call set_setting",
                unchanged == "Verify Tenant A",
                f"denied; value still {unchanged!r}",
            )
        except Exception as e:
            record("A 'member' CANNOT call set_setting", False, f"wrong error: {e}")

        # ── 6. org_admin may write its OWN org ────────────────────────────
        try:
            await set_setting(
                conn, org_a, "naming.member_label", "Partner",
                org_admin_id, principal=org_admin,
            )
            got = await get_setting(conn, org_a, "naming.member_label")
            record(
                "An 'org_admin' CAN set their own org's settings",
                got == "Partner", f"got={got!r}",
            )
        except Exception as e:
            record("An 'org_admin' CAN set their own org's settings", False, str(e))

        # ── 7. org_admin may NOT write a different org ────────────────────
        try:
            await set_setting(
                conn, org_b, "naming.member_label", "Trespass",
                org_admin_id, principal=org_admin,
            )
            record("An 'org_admin' CANNOT set a DIFFERENT org's settings", False,
                   "no exception raised")
        except SettingsPermissionError:
            leaked = await conn.fetchval(
                "SELECT count(*) FROM org_settings WHERE org_id = $1", org_b
            )
            record(
                "An 'org_admin' CANNOT set a DIFFERENT org's settings",
                leaked == 0, f"denied; org_b rows={leaked}",
            )
        except Exception as e:
            record("An 'org_admin' CANNOT set a DIFFERENT org's settings", False,
                   f"wrong error: {e}")

        # ── 8. super_admin may write ANY org ──────────────────────────────
        try:
            # Note: super_admin's own org is org_platform, not org_a or org_b.
            await set_setting(
                conn, org_a, "brand.short_name", "TenantA",
                super_admin_id, principal=super_admin,
            )
            await set_setting(
                conn, org_b, "brand.short_name", "TenantB",
                super_admin_id, principal=super_admin,
            )
            a = await get_setting(conn, org_a, "brand.short_name")
            b = await get_setting(conn, org_b, "brand.short_name")
            record(
                "A 'super_admin' CAN set settings on ANY org",
                a == "TenantA" and b == "TenantB",
                f"org_a={a!r}, org_b={b!r} (super_admin home org is neither)",
            )
        except Exception as e:
            record("A 'super_admin' CAN set settings on ANY org", False, str(e))

        # ── 9. THE SWEEP ─────────────────────────────────────────────────
        try:
            violations, allowed_hits, asset_hits = run_sweep()
            print(f"      sweep: {len(violations)} violation(s), "
                  f"{len(allowed_hits)} in allowed files, "
                  f"{len(asset_hits)} in static tenant artwork "
                  f"({ASSET_PREFIX}**)")
            for v in violations[:25]:
                print(f"        VIOLATION  {v}")
            if len(violations) > 25:
                print(f"        … and {len(violations) - 25} more")
            record(
                "THE SWEEP: zero hardcoded brand literals in application code",
                len(violations) == 0,
                f"found {len(violations)}",
            )
        except Exception as e:
            record("THE SWEEP: zero hardcoded brand literals in application code",
                   False, str(e))

    finally:
        # ── 10. Teardown ─────────────────────────────────────────────────
        await teardown()
        try:
            counts = {
                "org_settings": await conn.fetchval(
                    "SELECT count(*) FROM org_settings WHERE org_id = ANY($1::uuid[])",
                    org_ids,
                ),
                "users": await conn.fetchval(
                    "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", user_ids
                ),
                "organizations": await conn.fetchval(
                    "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
                    org_ids,
                ),
            }
            uniq_intact = await conn.fetchval(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'org_settings_org_id_setting_key_key'"
            )
            clean_ok = (
                all(c == 0 for c in counts.values())
                and uniq_intact == 1
                and not teardown_failed
            )
            record(
                "Teardown: zero leftover rows; constraint intact",
                clean_ok,
                ", ".join(f"{k}={v}" for k, v in counts.items())
                + f", unique={uniq_intact}",
            )
        except Exception as te:
            record("Teardown: zero leftover rows; constraint intact", False, str(te))

        await conn.close()

        if teardown_failed:
            print(
                "\n  [FATAL] Teardown incomplete — test data left in database. "
                "Fix manually before re-running.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # ── Summary ───────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n  {passed}/{total} assertions passed")

    sweep_result = next(
        (ok for label, ok, _ in results if label.startswith("THE SWEEP")), None
    )
    if sweep_result is False:
        print(
            "  *** THE SWEEP ASSERTION FAILED — Task 6 is incomplete. ***",
            file=sys.stderr,
        )

    if passed < total:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
