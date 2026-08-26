"""Independent cross-check: live information_schema vs docs/schema_snapshot.sql.

Deliberately re-queries the database rather than trusting refresh_schema.py's
own output, so a stale/buggy generator shows up as a mismatch here.
"""
import asyncio
import os
import pathlib
import re
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve()
API_DIR = HERE.parents[1]
REPO_ROOT = API_DIR.parent.parent
sys.path.insert(0, str(HERE.parent))
for sp in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(sp))

import asyncpg  # noqa: E402

from _doppler_env import hydrate_from_doppler  # noqa: E402

hydrate_from_doppler()
parts = urllib.parse.urlparse(os.environ["DATABASE_URL"])
pw = urllib.parse.quote(os.environ["DB_PASSWORD"], safe="")
URL = urllib.parse.urlunparse(
    (parts.scheme, f"{parts.username}:{pw}@{parts.hostname}:{parts.port}", parts.path, "", "", "")
)

SNAPSHOT = REPO_ROOT / "docs" / "schema_snapshot.sql"
snap: dict[str, set[str]] = {}
cur = None
for line in SNAPSHOT.read_text().splitlines():
    m = re.match(r"^-- ===== (.+?) =====", line)
    if m:
        cur = m.group(1)
        snap[cur] = set()
        continue
    if cur:
        m = re.match(r"^--   ([A-Za-z_][A-Za-z0-9_]*)\s{2,}", line)
        if m:
            snap[cur].add(m.group(1))

SCHEMAS = ("public", "litellm", "portfolio")

# Views count too — v_trial_balance / v_capital_accounts are in the snapshot.
QUERY = """
SELECT c.table_schema, c.table_name, c.column_name
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = ANY($1::text[])
"""


async def main() -> None:
    conn = await asyncpg.connect(URL, statement_cache_size=0, ssl="require")
    rows = await conn.fetch(QUERY, list(SCHEMAS))
    live: dict[str, set[str]] = {}
    for r in rows:
        key = (
            r["table_name"]
            if r["table_schema"] == "public"
            else f"{r['table_schema']}.{r['table_name']}"
        )
        live.setdefault(key, set()).add(r["column_name"])

    for s in SCHEMAS:
        n = sum(1 for k in live if (("." not in k) if s == "public" else k.startswith(f"{s}.")))
        print(f"live relations in {s}: {n}")
    print(f"snapshot sections: {len(snap)}")
    print("in LIVE but not snapshot:", sorted(set(live) - set(snap)) or "none")
    print("in SNAPSHOT but not live:", sorted(set(snap) - set(live)) or "none")

    mismatch = [
        (k, sorted(live[k] - snap[k]), sorted(snap[k] - live[k]))
        for k in sorted(set(live) & set(snap))
        if live[k] != snap[k]
    ]
    print("column-set mismatches:", len(mismatch))
    for k, live_only, snap_only in mismatch[:20]:
        print(f"   {k}: live-only={live_only} snap-only={snap_only}")

    for t in ("journal_entries", "journal_lines", "reference_data"):
        print(f"{t}: live cols = {sorted(live.get(t, []))}")

    await conn.close()


asyncio.run(main())
