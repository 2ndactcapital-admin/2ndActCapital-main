"""DISCLOSURES1 — load the platform default disclosures (docs/disclosures/defaults/<key>.md) into the library.

    python3 apps/api/scripts/load_disclosure_defaults.py --dry-run
    python3 apps/api/scripts/load_disclosure_defaults.py --drafter <super-admin email>

--dry-run (the default when no flag is given): parses and validates every default file and prints, per key:

  * the hash and size;
  * the R2 key it WOULD write (disclosures/platform/<key>/v<N>-<sha256>.md);
  * whether an identical platform version (same hash) is already on record, in which case it is skipped.

It only READS the database. It writes nothing, uploads nothing and builds no storage client.

The real run uploads each new or changed default as a platform DRAFT, through services.disclosures.upload_version.
The file goes to R2 through services.storage; the row, the hash and the audit row are written. The run is
attributed to --drafter, who must be a Super Admin. Nothing is published: maker-checker holds here too. A SECOND
Super Admin publishes each draft on Admin → Disclosures.

Never prints a secret. No prompts.
"""

from __future__ import annotations

import asyncio
import hashlib
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
DEFAULTS = REPO / "docs" / "disclosures" / "defaults"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SUMMARY = re.compile(r"<!-- summary -->\n(.*?)\n<!-- /summary -->", re.S)


def parse(path: pathlib.Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    m = FRONT.match(raw)
    if not m:
        raise ValueError(f"{path.name}: no front matter")
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    s = SUMMARY.search(raw)
    if not s:
        raise ValueError(f"{path.name}: no <!-- summary --> block")
    data = raw.encode("utf-8")
    return {"path": path, "key": meta.get("key"), "title": meta.get("title"), "category": meta.get("category"),
            "audience": meta.get("audience"), "status": meta.get("status", ""), "summary": s.group(1).strip(),
            "data": data, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def validate(d: dict) -> list[str]:
    from services import disclosures as DL
    from services import long_short_rules as R

    errs = []
    spec = DL.KEYS.get(d["key"] or "")
    if spec is None:
        return [f"{d['path'].name}: '{d['key']}' is not a key the platform defines (services.disclosures.KEYS)"]
    if d["path"].name != f"{d['key']}.md":
        errs.append(f"{d['path'].name}: file name must be {d['key']}.md")
    for f in ("category", "audience"):
        if d[f] != spec[f]:
            errs.append(f"{d['key']}: {f} {d[f]!r} differs from the catalog's {spec[f]!r}")
    if DL.DEFAULT_LABEL not in d["status"]:
        errs.append(f"{d['key']}: not marked '{DL.DEFAULT_LABEL}'")
    if not d["summary"] or len(d["summary"]) > 6000:
        errs.append(f"{d['key']}: the summary must be 1–6,000 characters")
    if d["size"] > DL.MAX_BYTES:
        errs.append(f"{d['key']}: file over {DL.MAX_BYTES} bytes")
    if d["key"] == "long_short.risks":
        keys = [i["key"] for i in DL.items_of(d["summary"])]
        if not set(R.RISK_KEYS) <= set(keys) or len(keys) != len(set(keys)):
            errs.append(f"long_short.risks: the summary must list rep13's six risk keys (each once) — got {keys}")
    if d["key"] == "replica.fund_affiliation" and "{ticker}" not in d["summary"]:
        errs.append("replica.fund_affiliation: the summary must carry {ticker}")
    return errs


def load_all() -> tuple[list[dict], list[str]]:
    docs, errs = [], []
    for p in sorted(DEFAULTS.glob("*.md")):
        try:
            d = parse(p)
        except ValueError as exc:
            errs.append(str(exc))
            continue
        errs += validate(d)
        docs.append(d)
    from services import disclosures as DL

    missing = sorted(set(DL.KEYS) - {d["key"] for d in docs})
    if missing:
        errs.append(f"no default file for: {missing}")
    return docs, errs


async def plan(docs: list[dict]) -> list[dict]:
    """Read-only: the next platform version number per key and whether this exact file is already on record."""
    from _db_connect import admin_dsn, connect, hydrate

    from services import disclosures as DL

    hydrate()
    dsn, how = await admin_dsn()
    if not dsn:
        raise SystemExit(f"no working DATABASE_URL: {how}")
    conn = await connect(dsn)
    out = []
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            for d in docs:
                cat = await DL.catalog_row(conn, d["key"])
                rows = await conn.fetch(f"SELECT version_no, content_sha256, status FROM {DL.T_VER} WHERE "
                                        "disclosure_id = $1 AND scope = 'platform' AND system_to IS NULL",
                                        cat["id"]) if cat else []
                same = [r for r in rows if r["content_sha256"] == d["sha256"] and r["status"] != "retired"]
                n = max([r["version_no"] for r in rows], default=0) + 1
                out.append({**d, "next_version": n, "existing": [dict(r) for r in same],
                            "r2_key": DL.object_key(key=d["key"], scope="platform", version_no=n, sha256=d["sha256"],
                                                    ext="md")})
    finally:
        await conn.close()
    return out


async def real_run(rows: list[dict], drafter_email: str) -> int:
    from services import disclosures as DL
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            u = await conn.fetchrow("SELECT id, org_id, role, status, is_active FROM public.users WHERE "
                                    "lower(email) = lower($1)", drafter_email)
    if u is None or u["role"] != "super_admin" or u["status"] != "active" or not u["is_active"]:
        print("[FAIL] --drafter must be an active Super Admin")
        return 1
    tokens = set_rls_context(str(u["org_id"]), True)    # BEFORE acquire: the pool applies the GUCs at acquire
    try:
        for r in rows:
            if r["existing"]:
                print(f"[SKIP] {r['key']}: identical file already on record (v{r['existing'][0]['version_no']}, "
                      f"{r['existing'][0]['status']})")
                continue
            row = await DL.upload_version(pool, org_id=u["org_id"], actor_id=u["id"], is_super_admin=True,
                                          key=r["key"], scope="platform", file_name=f"{r['key']}.md", data=r["data"],
                                          summary=r["summary"], note="platform default — have counsel review")
            print(f"[DRAFT] {r['key']} v{row['version_no']} → {row['r2_key']} (a second Super Admin publishes it)")
    finally:
        reset_rls_context(tokens)
        await close_pool()
    return 0


def main() -> int:
    args = sys.argv[1:]
    docs, errs = load_all()
    for e in errs:
        print(f"[FAIL] {e}")
    if errs:
        print(f"\n{len(errs)} problem(s) — nothing loaded")
        return 1
    rows = asyncio.run(plan(docs))
    for r in rows:
        state = (f"already on record as v{r['existing'][0]['version_no']} ({r['existing'][0]['status']}) — skip"
                 if r["existing"] else f"would draft v{r['next_version']}")
        print(f"[PLAN] {r['key']}: {r['size']} bytes sha256 {r['sha256'][:16]}… → {r['r2_key']} — {state}")
    if "--drafter" in args:
        i = args.index("--drafter")
        if i + 1 >= len(args):
            print("[FAIL] --drafter needs an email")
            return 2
        return asyncio.run(real_run(rows, args[i + 1]))
    print(f"\nDRY RUN — {len(rows)} default(s) validated; nothing written, nothing uploaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
