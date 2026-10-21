"""USERMGMT1 Task 5 — classify every test fixture user; remove the orphans.

    python3 apps/api/scripts/_usermgmt1_hygiene.py            # report only (dry run)
    python3 apps/api/scripts/_usermgmt1_hygiene.py --apply    # remove proven orphans

A fixture user is one with ``users.is_test`` (every ``@test.local`` address,
set by trigger). For each, the verify script that seeds it is FOUND, not
assumed: the scripts directory is searched for the user's id, its email, its
local part, then the local part's prefix before a trailing ``-xxxx`` / digits.

  KEPT    — a live script seeds this user and never deletes its users in
            teardown, so the row is a persistent seed that script expects.
  ORPHAN  — no live script references it, or every script that does deletes
            its own users in teardown (the row survived a crashed teardown;
            the script re-creates it on its next run).

An orphan is removed together with every row depending on it, the plan read
from pg_constraint by ``services.test_accounts`` — and ONLY if no row in that
plan references a user outside the orphan set. One transaction; a count
mismatch on any table rolls everything back. Prints per-table counts and a
count + checksum of the real (non-test) users before and after.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect  # noqa: E402

from services import test_accounts as T  # noqa: E402

SCRIPTS_DIR = HERE
_DELETES_USERS = re.compile(r"DELETE\s+FROM\s+(?:public\.)?users\b", re.I)
REAL_USERS_CHECKSUM_SQL = (
    "SELECT count(*) AS n, md5(COALESCE(string_agg("
    "id::text || '|' || email || '|' || COALESCE(auth0_sub, '') || '|' || status || '|' || "
    "role || '|' || is_active::text || '|' || is_test::text, ',' ORDER BY id), '')) AS sum "
    "FROM users WHERE NOT is_test"
)


def _script_texts() -> dict[str, str]:
    return {
        p.name: p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(SCRIPTS_DIR.glob("*.py"))
        if p.name != pathlib.Path(__file__).name
    }


def _needles(user: dict) -> list[str]:
    email = user["email"].lower()
    local = email.split("@", 1)[0]
    stem = re.sub(r"[-_]?[0-9a-f]{4}$|\d+[a-z]?$", "", local)
    out = [str(user["id"]), email, local]
    if stem and stem != local and len(stem) >= 6:
        out.append(stem)
    return out


def classify(users: list[dict], texts: dict[str, str] | None = None) -> list[dict]:
    texts = texts if texts is not None else _script_texts()
    out = []
    for u in users:
        scripts: list[str] = []
        matched_by = None
        for needle in _needles(u):
            scripts = sorted(name for name, src in texts.items() if needle in src.lower())
            if scripts:
                matched_by = needle
                break
        deleting = [s for s in scripts if _DELETES_USERS.search(texts[s])]
        if not scripts:
            verdict, reason = "orphan", "no script in apps/api/scripts references it"
        elif len(deleting) == len(scripts):
            verdict, reason = "orphan", "its script(s) delete their users in teardown — a crashed-teardown leftover"
        else:
            verdict, reason = "kept", "seeded by a live script that keeps its user between runs"
        out.append({**u, "scripts": scripts, "matched_by": matched_by,
                    "scripts_deleting_users": deleting, "verdict": verdict, "reason": reason})
    return out


async def fixture_users(conn) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, email, org_id, status FROM users WHERE is_test ORDER BY email"
    )
    return [{**dict(r), "id": str(r["id"]), "org_id": str(r["org_id"])} for r in rows]


async def real_users_fingerprint(conn) -> tuple[int, str]:
    row = await conn.fetchrow(REAL_USERS_CHECKSUM_SQL)
    return int(row["n"]), row["sum"]


async def run(apply: bool) -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"no working DATABASE_URL: {how}")
        return 1
    conn = await connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            before = await real_users_fingerprint(conn)
            users = classify(await fixture_users(conn))
            for u in users:
                print(f"{u['verdict'].upper():6} {u['email']:42} {','.join(u['scripts']) or '—'}  ({u['reason']})")
            orphans = [u["id"] for u in users if u["verdict"] == "orphan"]
            print(f"\n{len(users)} fixture users: {len(orphans)} orphan, {len(users) - len(orphans)} kept")
            if not orphans:
                return 0
            plan = await T.plan_purge(conn, orphans)
            print("plan (rows per table):", json.dumps({t: len(c) for t, c in sorted(plan.items())}))
            blocking = await T.foreign_user_references(conn, plan, orphans)
            if blocking:
                print("REFUSED — rows in the plan reference a non-orphan user:", blocking)
                return 2
            if not apply:
                print("dry run — nothing deleted (pass --apply)")
                return 0
            counts = await T.purge(conn, plan)
            print("deleted:", json.dumps(counts))
            left = await conn.fetchval("SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", orphans)
            after = await real_users_fingerprint(conn)
            print(f"orphans left: {left}; real users {before[0]} → {after[0]}, checksum "
                  f"{'unchanged' if before == after else 'CHANGED'}")
            if left or before != after:
                raise RuntimeError("post-purge check failed — rolled back")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run("--apply" in sys.argv)))
