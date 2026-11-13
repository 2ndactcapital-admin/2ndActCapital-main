"""verify_prices3 — the lean price of record + restatement history + compatibility view; the R2 Parquet archive +
reader; the paced, resumable operator migration — proven on FIXTURE SCHEMAS shaped like the real table.

    timeout 600 python3 apps/api/scripts/verify_prices3.py --self-checks-only
    timeout 600 python3 apps/api/scripts/verify_prices3.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_prices3.py --section m      # ONE section (own fixture + teardown)
    python3 apps/api/scripts/verify_prices3.py                              # everything + fast regressions

Sections (each builds its OWN fixture schema, reads its OWN clock, and tears it down):
  s  schema   the real portfolio objects read back (applied EMPTY; the real table is still the table, untouched)
  m  migrate  a fixture copy of the real table (LIKE … INCLUDING ALL + the same RLS policies + grants) holding
              ≥ 50k rows incl. org-uploaded rows (2 orgs) and restated rows (system AND valid axis, a double
              restatement, NULL provider, NULL as_of); dry-run numbers; paced copy with a SIMULATED KILL inside a
              batch + --resume; writes DURING the copy caught by the capture trigger (verify red before the catch-up,
              green after); swap; 14 platform reader queries IDENTICAL before vs after (the real functions where
              they are pure reads, through a connection that only renames the table); as-of identical; restatements
              after the swap land in history and the view shows both versions exactly as the legacy writer does
              (a legacy control schema runs the same write); RLS both ways under app_service (rolbypassrls false);
              the nightly writer writes lean after the switch; old-shaped writes through the view; rollback writes
              every post-swap change back; --drop-old refuses (no --i-confirm / red verification / no archive) and
              then drops; bytes/row measured
  a  archive  its own swapped fixture; export → a LOCAL filesystem store (no network); parity with Postgres
              (price, adjustment, bar; several windows; provider rule); org rows never archived; the nightly step
              (skipped in legacy, appends after the switch, restated value wins, bars drained); compaction keeps
              the frame identical
  j  fast regressions: verify_prices1, verify_prices2, verify_track1, verify_secmaster2, verify_dailyops1, verify_rep10
              (the backtest), verify_marketdata1 (the layer's readers), verify_rep4 (fund build price reads) —
              --self-checks-only, VERIFY_REGRESSIONS=fast

NO NETWORK, NO MODEL CALL: services.storage.get_s3_client is replaced by a recorder that refuses; the archive store is
a filesystem fixture. NEVER a real price row: every write lands in a fixture schema 'zzp3_*' (price_store.SCHEMA is
pointed at it for the section and restored); the real portfolio PRICES3 tables stay empty and the real table stays
the table. TEARDOWN: DROP SCHEMA of the section's own fixture schema (created by this script, name 'zzp3_*', carrying
its marker comment) — never a real table.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import dataclasses
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "prices3"
REGRESSIONS = ["verify_prices1.py", "verify_prices2.py", "verify_track1.py", "verify_secmaster2.py",
               "verify_dailyops1.py", "verify_rep10.py", "verify_marketdata1.py", "verify_rep4.py"]
MARKER = "prices3 verify fixture — dropped by verify_prices3"
D = Decimal


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


#: every platform reader of the price table: (name, file, a fragment that must still be in that file)
READERS = (
    ("R1 store.prices — latest close per security (every store-backed provider)", "apps/api/services/security_data/providers/store.py",
     "AND (provider_key = $2 OR ($2 = 'platform_entry' AND provider_key IS NULL)) "),
    ("R2 store.prices — a date range (backtest / get_series)", "apps/api/services/security_data/providers/store.py",
     "AND price_type = 'close' AND price_date <= $3 AND ($4::date IS NULL OR price_date >= $4::date) "),
    ("R3 store.prices — an org's uploaded file (csv_upload, org RLS)", "apps/api/services/security_data/providers/store.py",
     "AND (org_id IS NULL OR org_id = $5::uuid) AND "),
    ("R4 prices.latest_dates", "apps/api/services/security_data/prices.py",
     "\"SELECT global_security_id::text AS g, max(price_date) AS d FROM portfolio.securities_global_prices WHERE \""),
    ("R5 prices._context (last 40 closes before a day)", "apps/api/services/security_data/prices.py",
     "(PARTITION BY global_security_id ORDER BY price_date DESC) AS rn FROM portfolio.securities_global_prices "),
    ("R6 prices._coverage (provider, count, first, last)", "apps/api/services/security_data/prices.py",
     "\"SELECT provider_key, count(*) AS n, min(price_date) AS a, max(price_date) AS b FROM \""),
    ("R7 prices.refresh_status (latest date per covered security)", "apps/api/services/security_data/prices.py",
     "f\"global_security_id = ANY($1::uuid[]) AND org_id IS NULL AND price_type = 'close' AND {LIVE} GROUP BY 1\","),
    ("R8 nightly.platform_quality (a date window, every platform series)", "apps/api/services/security_data/nightly.py",
     "(\"portfolio.securities_global_prices\", \"global_security_id::text\", \"price_date\", \"price\","),
    ("R9 tracking.estimate DISTINCT priced + tracking.progress EXISTS", "apps/api/services/security_data/tracking.py",
     "\"SELECT DISTINCT global_security_id::text FROM portfolio.securities_global_prices WHERE \""),
    ("R10 security_master_summary has_price EXISTS", "apps/api/services/security_master_summary.py",
     "NULL AND p.valid_to IS NULL AND p.system_to IS NULL) AS has_price "),
    ("R11 portfolio_securities LATERAL latest platform price (asset grid)", "apps/api/services/portfolio_securities.py",
     "SELECT p.price, p.price_date, p.currency_code, p.price_type, p.source"),
    ("R12 portfolio_securities security detail: last 30 prices", "apps/api/services/portfolio_securities.py",
     "SELECT p.id::text AS id, p.price_date, p.price, p.currency_code,"),
    ("R13 fix_sim_pl.last_platform_closes", "apps/api/services/fix_sim_pl.py",
     "\"SELECT DISTINCT ON (global_security_id) global_security_id, price, price_date \""),
    ("R14 cpsim book.platform_price", "apps/api/cpsim/book.py",
     "\"SELECT price FROM portfolio.securities_global_prices WHERE global_security_id = $1::uuid AND org_id IS NULL \""),
)


# ═══ self-checks (no database, no network) ══════════════════════════════════
def self_checks(check, find):
    reg = _read("scripts/regression_set.txt")
    check(bool(re.search(r"(?m)^verify_prices3\.py\s*$", reg)), "verify_prices3.py is in scripts/regression_set.txt")
    w = _read("apps/api/services/security_data/writes.py")
    check(w.count('await PS.store_mode(') == 2 and "PS.lean_upsert_eod_bars(" in w and "PS.lean_restate_point(" in w,
          "writes.upsert_eod_bars and writes.upsert_prices route on the ONE runtime switch (store_mode)")
    check("portfolio.securities_global_prices" not in w,
          "writes.py names no price table literally (PS.old_name() = the table before the swap, the view after)")
    sg = _read("apps/api/services/securities_global.py")
    check("PS.store_mode(c) == \"lean\"" in sg and "PS.lean_add_price(" in sg,
          "securities_global.add_price routes on the same switch")
    from services.security_data import price_store as PS

    sql = PS.schema_sql("zzp3_selfcheck", fks=False)
    check("WITH (security_invoker = true)" in sql, "the compatibility view is security_invoker = true")
    m = re.search(r"CREATE OR REPLACE VIEW .*? AS\s+SELECT (.*?)\s+FROM zzp3_selfcheck\.security_prices l", sql, re.S)
    got = [re.split(r"\s+AS\s+|\s*\)\s*$", c.strip())[-1].split(".")[-1] for c in
           re.split(r",\s*(?![^()]*\))", m.group(1))] if m else []
    check(tuple(got) == PS.OLD_COLUMNS, "its lean branch publishes the old 19 columns in order", f"{got}")
    check(sql.count("CREATE POLICY") == 4 * 5 + 2 * 4, "28 policies: 4 × 5 org/series tables + 2 × 4 platform tables",
          f"{sql.count('CREATE POLICY')}")
    check("org_id" not in re.search(r"CREATE TABLE IF NOT EXISTS zzp3_selfcheck\.security_prices \((.*?)\n\);", sql,
                                    re.S).group(1),
          "the lean row has NO org_id column (8 columns → a 24-byte header); org scope comes from its series")
    # the bar has no reader
    allowed = {"services/security_data/providers/eod.py", "services/security_data/writes.py",
               "services/security_data/price_store.py", "services/security_data/price_archive.py"}
    rx = re.compile(r"""[\"']bar[\"']\]|->>?\s*'bar'|\bp\.bar\b|\.bar_json\(""")
    hits = []
    for base in ("services", "routers", "cpsim"):
        for p in (API_DIR / base).rglob("*.py"):
            rel = p.relative_to(API_DIR).as_posix()
            if rel not in allowed and rx.search(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(rel)
    check(not hits, "no service / router / cpsim file reads the OHLCV bar (it moves to the archive only)", f"{hits}")
    web = []
    for root, dirs, files in os.walk(REPO / "apps" / "web"):
        dirs[:] = [d_ for d_ in dirs if d_ not in ("node_modules", ".next", ".git", ".vercel")]
        for fn in files:
            if fn.endswith((".js", ".jsx", ".mjs", ".ts", ".tsx")):
                fp = pathlib.Path(root) / fn
                if re.search(r"\badjusted_close\b|\bbar\.(open|high|low|volume)\b",
                             fp.read_text(encoding="utf-8", errors="ignore")):
                    web.append(str(fp))
    check(not web, "no front-end file reads an OHLCV field", f"{web[:3]}")
    # readers still contain the predicates the DB section runs
    missing = [n for n, f, frag in READERS if frag not in _read(f)]
    check(not missing, f"all {len(READERS)} platform readers still carry the query text the migrate section runs",
          f"{missing}")
    jobs = _read("apps/api/services/security_master_jobs.py")
    check("PA.archive_step(ctx.pool)" in jobs and "PRICES_ARCHIVE" in jobs,
          "the archive step is a step of the existing nightly prices job (PRICES_ARCHIVE=0 turns it off)")
    req = _read("apps/api/requirements.txt")
    check(bool(re.search(r"(?m)^duckdb", req) and re.search(r"(?m)^pyarrow", req)),
          "duckdb + pyarrow declared in requirements.txt")
    mig = _read("apps/api/scripts/prices3_migrate.py")
    drops = re.findall(r"(DROP TABLE|TRUNCATE|DROP TRIGGER|DROP VIEW|DROP SCHEMA)[^\"']*", mig)
    check(sorted(set(drops)) == ["DROP TABLE", "DROP TRIGGER", "TRUNCATE"]
          and mig.count("DROP TABLE {n['pre']}") == 1 and "TRUNCATE {n['lean']}, {n['hist']}, {n['bars']}, "
          "{n['dirty']}" in mig,
          "the migration drops ONLY the renamed old table (in --drop-old), TRUNCATEs only the derived tables "
          "(--reset-copy) and drops only its capture trigger", f"{sorted(set(drops))}")
    import prices3_migrate as PM

    per = [(f"{i:08d}-0000-0000-0000-000000000000", n) for i, n in enumerate([30_000, 30_000, 10_000, 70_000, 5, 5])]
    bs = list(PM.batches_of(per, 50_000))
    check(sum(b[2] for b in bs) == 140_010 and [b[3] for b in bs] == [2, 2, 2] and bs[0][0] is None
          and bs[1][0] == bs[0][1] and all(b[2] >= 50_000 for b in bs[:-1]),
          "batches_of: whole securities per batch, every batch but the last ≥ --batch rows, contiguous ranges", f"{bs}")
    # the archive round trip, offline
    from services.security_data import price_archive as PA

    root = tempfile.mkdtemp(prefix="zzp3_selfcheck_")
    try:
        st = PA.LocalStore(root)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [PA.normalise({"global_security_id": g, "price_type": "close", "provider_key": "p",
                              "price_date": date(2025, 12, 29) + timedelta(days=i), "price": D("4.5000") + i,
                              "adjustment_factor": D("1.000123"), "currency_code": "USD", "source": "p", "as_of": t0,
                              "recorded_at": t0, "quality_flags": [], "licence": {"a": 1}, "bar": {"open": "1"}})
                for g in ("A", "B") for i in range(6)]
        PA.write_append(st, rows, t0)
        PA.write_append(st, [dict(rows[0], price=D("9.99"), recorded_at=t0 + timedelta(hours=1))],
                        t0 + timedelta(hours=1))
        f1 = PA.history_frame(["A", "B"], date(2025, 1, 1), date(2026, 12, 31), store=st)
        check(f1.loc[date(2025, 12, 29), "A"] == D("9.99") and f1.loc[date(2025, 12, 29), "B"] == D("4.5000")
              and f1.shape == (6, 2), "archive (offline): the later recorded_at wins; a date × security frame",
              f"{f1.shape}")
        [PA.compact_year(st, y) for y in (2025, 2026)]
        f2 = PA.history_frame(["A", "B"], date(2025, 1, 1), date(2026, 12, 31), store=st)
        check(bool(f2.equals(f1)) and all(k.endswith("/compact.parquet") for k, _ in st.list(PA.PREFIX)),
              "archive (offline): compaction leaves one file per year and an IDENTICAL frame")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    find(f"archive scan engine in this venv: {PA.engine_name()}",
         "duckdb is not installed (no network in the sprint); it is declared in requirements.txt — every archive "
         "proof here ran on pyarrow" if PA.engine_name() == "pyarrow" else "duckdb present")


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))


# ═══ the DB run ═════════════════════════════════════════════════════════════
import asyncpg  # noqa: E402

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

import _verify_total as VT  # noqa: E402
import prices3_migrate as PM  # noqa: E402
from services import storage as STORAGE  # noqa: E402
from services.security_data import price_archive as PA  # noqa: E402
from services.security_data import price_store as PS  # noqa: E402
from services.security_data import writes as W  # noqa: E402
from services.security_data.providers.eod import EodBar  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
NETWORK_CALLS: list = []
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    passed = bool(passed)
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""), flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def _no_network(*a, **k):
    NETWORK_CALLS.append("services.storage.get_s3_client")
    raise RuntimeError("verify_prices3: no network — the archive store is a filesystem fixture")


STORAGE.get_s3_client = _no_network
PA.default_store = lambda: None


def u(name: str) -> str:
    """md5('zzp3:' || name)::uuid — the same id the fixture SQL computes."""
    import hashlib

    return str(uuid.UUID(hashlib.md5(f"zzp3:{name}".encode()).hexdigest()))


ORG_A, ORG_B = u("org-a"), u("org-b")
UPL_A, UPL_B, UPL_A2 = u("upload-a"), u("upload-b"), u("upload-a2")
SRC, ALT = "zzp3_src", "zzp3_alt"
LIC = {"attribution": "ZZP3 fixture", "internal_use": True, "member_display": "not_allowed"}


def gs(fx: str, i: int) -> str:
    return u(f"{fx}:sec:{i}")


def org_gs(fx: str, org: str, i: int) -> str:
    return u(f"{fx}:{org}:sec:{i}")


async def operator():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    return dsn, how


class Rewrite:
    """A connection that renames the real price table to the fixture's — every other byte of SQL is the reader's."""

    def __init__(self, conn, fx: str):
        self._c, self._fx = conn, fx

    def _s(self, q: str) -> str:
        return q.replace("portfolio.securities_global_prices", f"{self._fx}.securities_global_prices")

    async def fetch(self, q, *a, **k):
        return await self._c.fetch(self._s(q), *a, **k)

    async def fetchrow(self, q, *a, **k):
        return await self._c.fetchrow(self._s(q), *a, **k)

    async def fetchval(self, q, *a, **k):
        return await self._c.fetchval(self._s(q), *a, **k)

    async def execute(self, q, *a, **k):
        return await self._c.execute(self._s(q), *a, **k)

    def transaction(self, *a, **k):
        return self._c.transaction(*a, **k)


def norm(v):
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return norm(dataclasses.asdict(v))
    if isinstance(v, dict):
        return {str(k): norm(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [norm(x) for x in v]
    if hasattr(v, "items") and hasattr(v, "keys"):
        return {k: norm(v[k]) for k in v.keys()}
    if isinstance(v, (Decimal, date, datetime, uuid.UUID)):
        return f"{type(v).__name__}:{v.isoformat() if hasattr(v, 'isoformat') else v}"
    return v


@dataclasses.dataclass
class Ctx:
    fx: str
    conn: asyncpg.Connection
    dsn: str


async def as_role(conn, *, org: str | None = None, super_admin: bool = False):
    await conn.execute("SET LOCAL ROLE app_service")
    await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org or "")
    await conn.execute("SELECT set_config('app.is_super_admin', $1, true)", "true" if super_admin else "")


# ── fixture ─────────────────────────────────────────────────────────────────
async def drop_fixture(conn, fx: str) -> bool:
    assert fx.startswith("zzp3_"), fx
    cm = await conn.fetchval("SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = $1", fx)
    if cm is None and not await conn.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", fx):
        return False
    if cm != MARKER:
        raise RuntimeError(f"schema {fx} exists without this script's marker — not dropping it")
    await conn.execute(f"DROP SCHEMA {fx} CASCADE")
    return True


async def build_fixture(conn, fx: str, *, n_sec: int, lean_schema: bool = True) -> dict:
    """An old-shaped table in schema ``fx`` (LIKE the real one, its RLS policies and grants) + the PRICES3 objects."""
    await drop_fixture(conn, fx)
    await conn.execute(f"CREATE SCHEMA {fx}")
    await conn.execute(f"COMMENT ON SCHEMA {fx} IS '{MARKER}'")
    await conn.execute(f"GRANT USAGE ON SCHEMA {fx} TO app_service, cpsim_app")
    await conn.execute(f"CREATE TABLE {fx}.{PS.OLD} (LIKE portfolio.{PS.OLD} INCLUDING DEFAULTS INCLUDING CONSTRAINTS "
                       f"INCLUDING INDEXES INCLUDING COMMENTS)")
    await conn.execute(f"ALTER TABLE {fx}.{PS.OLD} ENABLE ROW LEVEL SECURITY")
    for p in await conn.fetch("SELECT policyname, cmd, qual, with_check FROM pg_policies WHERE schemaname = "
                              "'portfolio' AND tablename = $1", PS.OLD):
        using = f" USING ({p['qual']})" if p["qual"] else ""
        wc = f" WITH CHECK ({p['with_check']})" if p["with_check"] else ""
        await conn.execute(f"CREATE POLICY {p['policyname']} ON {fx}.{PS.OLD} FOR {p['cmd']}{using}{wc}")
    await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {fx}.{PS.OLD} TO app_service")
    await conn.execute(f"GRANT SELECT ON {fx}.{PS.OLD} TO cpsim_app")
    if lean_schema:
        await conn.execute(PS.schema_sql(fx, fks=False))
    t = f"{fx}.{PS.OLD}"
    days = (f"(SELECT d::date AS d, row_number() OVER (ORDER BY d) AS di FROM generate_series('2021-11-08'::date, "
            f"'2026-11-06'::date, '1 day') d WHERE extract(isodow FROM d) < 6) days")
    secs = f"(SELECT i FROM generate_series(0, {n_sec - 1}) i) s"
    gid = "md5('zzp3:' || $1 || ':sec:' || s.i)::uuid"
    price = "round((5 + (s.i % 37) + 3 * sin(days.di / 9.0 + s.i))::numeric, 4)"
    sf = "('2026-11-01 02:00:00+00'::timestamptz + (s.i || ' minutes')::interval + (days.di || ' ms')::interval)"
    await conn.execute(
        f"INSERT INTO {t} (global_security_id, price_date, price, currency_code, price_type, source, valid_from, "
        f"system_from, provider_key, as_of, quality_flags, licence, adjustment_factor, bar) "
        f"SELECT {gid}, days.d, {price}, 'USD', 'close', $2, {sf}, {sf}, $2, {sf} + ((s.i % 5) * 17 || ' s')::interval, "
        f"CASE WHEN days.di % 97 = 0 THEN '{{outlier}}'::text[] ELSE '{{}}'::text[] END, $3::jsonb, "
        f"CASE WHEN s.i % 7 = 0 THEN NULL ELSE round((1 + (s.i % 5) * 0.000731 + days.di * 0.0000013)::numeric, 6) END, "
        f"jsonb_build_object('open', {price}::text, 'high', ({price} * 1.01)::text, 'low', ({price} * 0.99)::text, "
        f"'volume', (1000 + days.di)::text) FROM {secs}, {days}", fx, SRC, json.dumps(LIC))
    # a second provider for three securities (the last 200 days)
    await conn.execute(
        f"INSERT INTO {t} (global_security_id, price_date, price, currency_code, source, valid_from, system_from, "
        f"provider_key, as_of, licence, bar) SELECT {gid}, days.d, {price} + 0.01, 'USD', $2, {sf} + interval '1 hour', "
        f"{sf} + interval '1 hour', $2, {sf}, "
        f"'{{\"attribution\": \"alt\"}}'::jsonb, '{{}}'::jsonb FROM {secs}, {days} WHERE s.i < 3 AND days.di > 1104",
        fx, ALT)
    # provider-less rows (read as platform_entry), NULL as_of, NULL adjustment
    await conn.execute(
        f"INSERT INTO {t} (global_security_id, price_date, price, currency_code, source, valid_from, system_from) "
        f"SELECT {gid}, days.d, {price}, 'USD', 'manual', {sf} + interval '2 hours', {sf} + interval '2 hours' "
        f"FROM {secs}, {days} WHERE s.i IN (4, 5) "
        f"AND days.di > 1264", fx)
    # org uploads: org A 3 securities, org B 2 (one of them shares a platform security)
    for org, upl, k in ((ORG_A, UPL_A, 3), (ORG_B, UPL_B, 2)):
        await conn.execute(
            f"INSERT INTO {t} (global_security_id, price_date, price, currency_code, source, valid_from, system_from, "
            f"provider_key, as_of, licence, org_id, upload_id) SELECT CASE WHEN $2::uuid = $6::uuid AND s.i = 0 THEN "
            f"md5('zzp3:' || $1 || ':sec:0')::uuid ELSE md5('zzp3:' || $1 || ':' || $5 || ':sec:' || s.i)::uuid END, "
            f"days.d, {price} * 2, 'USD', 'csv_upload', {sf}, {sf}, 'csv_upload', {sf}, "
            f"'{{\"attribution\": \"org file\"}}'::jsonb, $2::uuid, $3::uuid FROM {secs}, {days} WHERE s.i < $4 AND "
            f"days.di > 1004", fx, org, upl, k, "A" if org == ORG_A else "B", ORG_B)
    # SYSTEM-axis restatements: 5 securities × 60 dates (the old value closed at the live row's system_from)
    await conn.execute(
        f"INSERT INTO {t} (global_security_id, price_date, price, currency_code, price_type, source, valid_from, "
        f"valid_to, system_from, system_to, provider_key, as_of, quality_flags, licence, adjustment_factor, bar) "
        f"SELECT global_security_id, price_date, price * 0.99, currency_code, price_type, source, "
        f"system_from - interval '3 days', NULL, system_from - interval '3 days', system_from, provider_key, "
        f"as_of - interval '3 days', '{{}}', licence, adjustment_factor, bar FROM {t} WHERE provider_key = $1 AND "
        f"global_security_id = ANY($2::uuid[]) AND price_date > '2026-08-10'", SRC, [gs(fx, i) for i in range(6, 11)])
    # …and a DOUBLE restatement on one security / date
    await conn.execute(
        f"INSERT INTO {t} (global_security_id, price_date, price, currency_code, source, valid_from, system_from, "
        f"system_to, provider_key, as_of, licence) SELECT global_security_id, price_date, price * 0.98, currency_code, "
        f"source, system_from - interval '6 days', system_from - interval '6 days', system_from - interval '3 days', "
        f"provider_key, as_of - interval '6 days', licence FROM {t} WHERE provider_key = $1 AND global_security_id = "
        f"$2::uuid AND price_date > '2026-10-01' AND valid_to IS NULL AND system_to IS NULL", SRC, gs(fx, 6))
    # VALID-axis restatements on org A's file: 30 dates
    await conn.execute(
        f"INSERT INTO {t} (global_security_id, price_date, price, currency_code, source, valid_from, valid_to, "
        f"system_from, provider_key, as_of, licence, org_id, upload_id) SELECT global_security_id, price_date, "
        f"price * 1.05, currency_code, source, valid_from - interval '1 day', valid_from, valid_from - interval '1 day', "
        f"provider_key, as_of, licence, org_id, upload_id FROM {t} WHERE org_id = $1::uuid AND global_security_id = "
        f"$2::uuid AND price_date > '2026-09-20' AND valid_to IS NULL AND system_to IS NULL", ORG_A,
        org_gs(fx, "A", 0))
    st = await conn.fetchrow(
        f"SELECT count(*) AS n, count(*) FILTER (WHERE org_id IS NOT NULL) AS org, count(*) FILTER (WHERE system_to IS "
        f"NOT NULL) AS sys, count(*) FILTER (WHERE valid_to IS NOT NULL) AS val, count(*) FILTER (WHERE provider_key IS "
        f"NULL) AS nullp, count(DISTINCT global_security_id) AS secs FROM {t}")
    return dict(st)


# ── readers (before / after) ────────────────────────────────────────────────
async def readers(conn, fx: str) -> dict:
    """{reader: normalised result}: the platform's own reader functions (through Rewrite) and the reader SQL."""
    from cpsim import book as CB
    from services import fix_sim_pl as FS
    from services.security_data import prices as P
    from services.security_data.providers import store

    T = f"{fx}.{PS.OLD}"
    plat = [gs(fx, i) for i in range(0, 14)]
    live = "valid_to IS NULL AND system_to IS NULL"
    out: dict = {}
    rw = Rewrite(conn, fx)
    async with conn.transaction():
        await as_role(conn, org=ORG_A)
        out["R1"] = norm(await store.prices(rw, ORG_A, SRC, plat, end=date(2026, 11, 6), start=None))
        out["R1b"] = norm(await store.prices(rw, ORG_A, "platform_entry", plat, end=date(2026, 11, 6), start=None))
        out["R2"] = norm(await store.prices(rw, ORG_A, SRC, plat[:4], end=date(2026, 6, 30), start=date(2025, 1, 2)))
        out["R3"] = norm(await store.prices(rw, ORG_A, "csv_upload", [org_gs(fx, "A", i) for i in range(3)] +
                                            [gs(fx, 0)], end=date(2026, 11, 6), start=date(2026, 9, 1)))
    async with conn.transaction():
        await as_role(conn, org=ORG_B)
        out["R3b"] = norm(await store.prices(rw, ORG_B, "csv_upload", [org_gs(fx, "A", i) for i in range(3)] +
                                             [gs(fx, 0)], end=date(2026, 11, 6), start=date(2026, 9, 1)))
    async with conn.transaction():
        await as_role(conn)
        out["R4"] = norm(await P.latest_dates(rw, SRC, plat))
        out["R5"] = norm(await P._context(rw, SRC, plat, date(2026, 9, 1)))
    async with conn.transaction():
        await as_role(conn, super_admin=True)
        out["R6"] = norm([await conn.fetchrow(
            f"SELECT provider_key, count(*) AS n, min(price_date) AS a, max(price_date) AS b FROM {T} WHERE "
            f"global_security_id = $1::uuid AND org_id IS NULL AND price_type = 'close' AND {live} GROUP BY "
            f"provider_key ORDER BY count(*) DESC, provider_key LIMIT 1", g) for g in plat])
        out["R7"] = norm(await conn.fetch(
            f"SELECT global_security_id::text AS g, max(price_date) AS d FROM {T} WHERE global_security_id = "
            f"ANY($1::uuid[]) AND org_id IS NULL AND price_type = 'close' AND {live} GROUP BY 1 ORDER BY 1", plat))
        out["R8"] = norm(await conn.fetch(
            f"SELECT global_security_id::text AS k, provider_key, price_date AS d, price AS v FROM {T} WHERE org_id "
            f"IS NULL AND price_date >= $1 AND price_date <= $2 AND valid_to IS NULL AND system_to IS NULL "
            f"AND ($3::text[] IS NULL OR global_security_id::text = ANY($3::text[])) ORDER BY 1, 2, 3",
            date(2026, 8, 1), date(2026, 11, 6), None))
        out["R9"] = norm(sorted(r[0] for r in await conn.fetch(
            f"SELECT DISTINCT global_security_id::text FROM {T} WHERE global_security_id = ANY($1::uuid[]) AND org_id "
            f"IS NULL AND price_type = 'close' AND {live}", plat + [org_gs(fx, "A", 0), u("nobody")])))
        probe = plat + [org_gs(fx, "A", 0), u("nobody")]
        out["R9b"] = norm(await conn.fetch(
            f"SELECT g::text, EXISTS (SELECT 1 FROM {T} p WHERE p.global_security_id = g AND p.org_id IS NULL AND "
            f"p.price_type = 'close' AND p.valid_to IS NULL AND p.system_to IS NULL) AS loaded FROM unnest($1::uuid[]) g "
            f"ORDER BY 1", probe))
        out["R10"] = norm(await conn.fetch(
            f"SELECT g::text, EXISTS (SELECT 1 FROM {T} p WHERE p.global_security_id = g AND p.org_id IS NULL AND "
            f"p.valid_to IS NULL AND p.system_to IS NULL) AS has_price FROM unnest($1::uuid[]) g ORDER BY 1", probe))
        out["R11"] = norm(await conn.fetch(
            f"SELECT g::text, gp.* FROM unnest($1::uuid[]) g LEFT JOIN LATERAL (SELECT p.price, p.price_date, "
            f"p.currency_code, p.price_type, p.source FROM {T} p WHERE p.global_security_id = g AND p.org_id IS NULL "
            f"AND p.valid_to IS NULL AND p.system_to IS NULL ORDER BY p.price_date DESC, p.system_from DESC LIMIT 1) gp "
            f"ON true ORDER BY 1", probe))
        det = [await conn.fetch(
            f"SELECT p.id::text AS id, p.price_date, p.price, p.currency_code, p.price_type, p.source FROM {T} p WHERE "
            f"p.global_security_id = $1::uuid AND p.org_id IS NULL AND p.valid_to IS NULL AND p.system_to IS NULL "
            f"ORDER BY p.price_date DESC, p.system_from DESC LIMIT 30", g) for g in plat[:6]]
        out["R12"] = norm([[{k: r[k] for k in r.keys() if k != "id"} for r in rows] for rows in det])
        out["_R12_ids"] = [[r["id"] for r in rows] for rows in det]
    async with conn.transaction():
        await as_role(conn, org=ORG_A)
        # R13 / R14 order by date only: with two platform providers closing the same day their pick is arbitrary —
        # on the old table too (a FIND below) — so they are compared on single-provider securities
        single = plat[3:]
        out["R13"] = norm(await FS.last_platform_closes(rw, single, date(2026, 6, 30)))
        out["R14"] = norm([await CB.platform_price(rw, g, date(2026, 7, 3)) for g in single[:8]])
    return out


AS_OF_SQL = ("SELECT {cols} FROM {rel} v WHERE v.global_security_id = ANY($2::uuid[]) AND v.price_type = 'close' AND "
             "v.system_from <= $1 AND (v.system_to IS NULL OR v.system_to > $1) AND v.valid_from <= $1 AND "
             "(v.valid_to IS NULL OR v.valid_to > $1)")
AS_OF_COLS = ", ".join(c for c in PS.OLD_COLUMNS if c not in ("id", "bar")) + \
    ", CASE WHEN valid_to IS NULL AND system_to IS NULL THEN NULL ELSE id END AS closed_id"
AS_OF_ORDER = " ORDER BY global_security_id, provider_key NULLS FIRST, org_id NULLS FIRST, price_date, system_from"


async def as_of_answers(conn, fx: str, rel: str | None, stamps: list, ids: list) -> list:
    out = []
    async with conn.transaction():
        await as_role(conn, super_admin=True)
        for t_ in stamps:
            if rel:
                q = AS_OF_SQL.format(cols=AS_OF_COLS, rel=rel) + AS_OF_ORDER
            else:
                q = (f"SELECT {AS_OF_COLS} FROM {fx}.price_as_known($1, $2::uuid[]) v" + AS_OF_ORDER)
            out.append(norm(await conn.fetch(q, t_, ids)))
    return out


async def view_rows(conn, rel: str, gsid: str, day: date, *, org: str | None = None) -> list:
    async with conn.transaction():
        await as_role(conn, org=org, super_admin=org is None)
        return [dict(r) for r in await conn.fetch(
            f"SELECT id, price, valid_from, valid_to, system_from, system_to, provider_key, org_id, upload_id FROM {rel} "
            f"WHERE global_security_id = $1::uuid AND price_date = $2 ORDER BY system_from, valid_from", gsid, day)]


def shape(rows: list) -> list:
    """The bitemporal shape of one (security, date): per row (price, live?, axis) + 'the closure = the successor's
    start' — what the legacy writer produces, independent of ids and wall-clock times."""
    rows = sorted(rows, key=lambda r: (r["system_from"], r["valid_from"]))
    out = []
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        axis = "live" if r["valid_to"] is None and r["system_to"] is None else ("system" if r["system_to"] else "valid")
        joined = None if nxt is None else ((r["system_to"] or r["valid_to"]) == nxt["system_from"])
        out.append((str(r["price"]), axis, joined))
    return out


def first_diff(a, b):
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return (i, first_diff(x, y))
        return ("len", len(a), len(b))
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                return (k, str(a.get(k))[:300], str(b.get(k))[:300])
    return (str(a)[:300], str(b)[:300])


def bars(day: date, close: str, *, open_: str = "1.0") -> list:
    return [EodBar(day=day, close=D(close), currency="USD", open=D(open_), high=D(close) + 1, low=D(close) - 1,
                   volume=D(1000), adjusted_close=D(close), adjustment_factor=D("1"))]


async def measure(conn, fx: str) -> dict:
    """bytes / row of the lean table (main fork + its indexes) vs the fixture old table."""
    r = await conn.fetchrow(
        f"SELECT (SELECT count(*) FROM {fx}.security_prices) AS n, pg_relation_size('{fx}.security_prices') AS heap, "
        f"pg_indexes_size('{fx}.security_prices') AS idx, pg_total_relation_size('{fx}.security_prices') AS total, "
        f"(SELECT avg(pg_column_size(l.*)) FROM {fx}.security_prices l) AS tuple")
    return dict(r)


# ═══ section s — the real schema, read back ═════════════════════════════════
async def y_schema(conn):
    section("[S] The real PRICES3 objects: applied EMPTY; the real table is still the table")
    import _apply_prices3 as AP

    ok, bad, lines = await AP.read_back(conn, "portfolio")
    for ln in lines:
        print("   " + ln)
    check(bad == 0 and ok >= 19, f"portfolio catalog read-back: {ok} PASS, {bad} FAIL (_apply_prices3.read_back)")
    mig = await PM.migration_row(conn, "portfolio")
    check(mig.get("phase") == "not_started" and mig.get("store_mode") == "legacy" and mig.get("copied_rows") == 0,
          "the real migration row: not_started / legacy / 0 rows copied (the sprint never migrates real data)",
          f"{ {k: mig.get(k) for k in ('phase', 'store_mode', 'copied_rows')} }")


# ═══ section m — the migration end to end ═══════════════════════════════════
async def y_migrate(conn, dsn):
    fx, ctl = "zzp3_m", "zzp3_c"
    section(f"[M] Migration on a fixture copy ({fx}) — dry run, paced copy, kill + resume, catch-up, verify, swap, "
            "readers identical, restatements, RLS, rollback, guarded drop, bytes/row")
    clock = await conn.fetchval("SELECT now()")
    print(f"[INFO] section clock: DB now() = {clock.isoformat()}")
    PS.SCHEMA = fx
    try:
        st = await build_fixture(conn, fx, n_sec=40)
        check(st["n"] >= 50_000 and st["org"] > 0 and st["sys"] > 0 and st["val"] > 0 and st["nullp"] > 0,
              f"fixture: {st['n']:,} rows over {st['secs']} securities — {st['org']:,} org-uploaded, {st['sys']} "
              f"system-axis + {st['val']} valid-axis restated, {st['nullp']} provider-less", f"{st}")
        role = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_service'")
        check(role is not None and role["rolbypassrls"] is False,
              "app_service (every RLS proof below runs SET LOCAL ROLE app_service) has rolbypassrls = false")
        # the dry run
        p = await PM.plan(conn, fx, batch=10_000, sleep=0.2)
        check(p["rows"] == st["n"] and p["closed"] == st["sys"] + st["val"] and p["org_rows"] == st["org"]
              and p["batches"] >= 5 and p["estimated_new_bytes"] < p["old_total_bytes"]
              and p["live_valid_from_ne_system_from"] == 0 and p["flags"] == ["outlier"],
              f"dry run: {p['rows']:,} rows, {p['closed']} closed, {p['batches']} batches of 10k, estimated new "
              f"{p['estimated_new_bytes']:,} B vs old {p['old_total_bytes']:,} B, WAL ~{p['estimated_wal_bytes']:,} B",
              f"{p}")
        mig0 = await PM.migration_row(conn, fx)
        check(mig0["phase"] == "not_started" and mig0["copied_rows"] == 0, "the dry run wrote nothing")
        # copy: 1 batch, then writes during the copy, then a simulated kill inside batch 3, then --resume
        r1 = await PM.copy(conn, fx, batch=10_000, sleep=0.2, stop_after=1)
        m1 = await PM.migration_row(conn, fx)
        trig = await conn.fetchval(f"SELECT count(*) FROM pg_trigger WHERE tgrelid = '{fx}.{PS.OLD}'::regclass AND "
                                   "tgname = 'prices3_dirty'")
        check(r1["action"] == "stopped" and m1["phase"] == "copying" and m1["checkpoint_gsid"] is not None
              and trig == 1, f"copy: batch 1 committed ({r1['rows']:,} rows), checkpoint set, capture trigger on")
        first = sorted([gs(fx, i) for i in range(40)])[0]
        late = sorted([gs(fx, i) for i in range(40)])[-1]
        async with PM.ptx(conn):
            got_a = await W.upsert_eod_bars(conn, first, bars(date(2026, 11, 6), "123.4567"), provider_key=SRC,
                                            licence=LIC, currency="USD")
            got_b = await W.upsert_eod_bars(conn, late, bars(date(2026, 11, 9), "77.7700"), provider_key=SRC,
                                            licence=LIC, currency="USD")
        dirty = {str(r[0]) for r in await conn.fetch(f"SELECT global_security_id FROM {fx}.prices3_dirty")}
        check(got_a["restated"] == 1 and got_b["inserted"] == 1 and {first, late} <= dirty,
              "legacy writer DURING the copy (a restatement on an already-copied security + a new close on a later "
              "one) wrote the OLD table and both securities are marked dirty", f"{got_a} {got_b} {dirty}")
        try:
            await PM.copy(conn, fx, batch=10_000, sleep=0.2)
            check(False, "a copy without --resume while one is in progress is refused")
        except PM.Refused:
            check(True, "a copy without --resume while one is in progress is refused")
        cp_before = (await PM.migration_row(conn, fx))["checkpoint_gsid"]
        killed = None
        try:
            await PM.copy(conn, fx, batch=10_000, sleep=0.2, resume=True, fail_in_batch=1)
        except RuntimeError as exc:
            killed = str(exc)
        m2 = await PM.migration_row(conn, fx)
        check(bool(killed) and m2["phase"] == "copying" and m2["batches"] == 2,
              "simulated kill INSIDE a batch: the batch's rows rolled back with it; the checkpoint stands at the last "
              "committed batch", f"{killed} batches={m2['batches']}")
        lean_mid = await conn.fetchval(f"SELECT count(*) FROM {fx}.{PS.VIEW_V3}")
        check(lean_mid == m2["copied_rows"], "…the new tables hold exactly the committed batches' rows",
              f"{lean_mid} vs {m2['copied_rows']}")
        r3 = await PM.copy(conn, fx, batch=10_000, sleep=0.2, resume=True)
        m3 = await PM.migration_row(conn, fx)
        check(r3["action"] == "copied" and m3["phase"] == "copied" and r3["resumed_from"] == str(m2["checkpoint_gsid"])
              and str(cp_before) <= str(m2["checkpoint_gsid"]),
              f"--resume continued after the last committed batch and finished ({m3['batches']} batches)", f"{r3}")
        # verify before the catch-up: the write on an already-copied security makes it RED
        red = await PM.verify_all(conn, fx, batch=10_000, sleep=0)
        check(red["ok"] is False and any(m["gsid"] == first for m in red["first_mismatches"]),
              "verify BEFORE the catch-up is RED — it names the security written after its batch was copied",
              f"{red['mismatches']}")
        cu = await PM.catch_up(conn, fx)
        green = await PM.verify_all(conn, fx, batch=10_000, sleep=0)
        check(cu["resynced_securities"] >= 2 and green["ok"] is True and green["rows_old"] == green["rows_new"]
              and green["securities"] == st["secs"],
              f"catch-up re-copied {cu['resynced_securities']} dirty securities; verify GREEN: {green['securities']} "
              f"securities, {green['rows_old']:,} rows, per-security count + md5 equal", f"{green}")
        mig_v = await PM.migration_row(conn, fx)
        check(mig_v["phase"] == "verified" and mig_v["verified_ok"] is True and mig_v["verify_detail"],
              "the verification is recorded on the migration row (phase verified)")
        # readers + as-of BEFORE
        before = await readers(conn, fx)
        ids_probe = [gs(fx, i) for i in range(6, 12)] + [org_gs(fx, "A", 0)]
        stamps = [datetime(2026, 10, 28, 12, tzinfo=timezone.utc), datetime(2026, 10, 31, 3, tzinfo=timezone.utc),
                  datetime(2026, 11, 1, 2, 10, tzinfo=timezone.utc), datetime(2026, 12, 1, tzinfo=timezone.utc),
                  datetime.now(timezone.utc)]
        asof_old = await as_of_answers(conn, fx, f"{fx}.{PS.OLD}", stamps, ids_probe)
        n_view_before = await conn.fetchval(f"SELECT count(*) FROM {fx}.{PS.VIEW_V3}")
        # SWAP
        sw = await PM.swap(conn, fx)
        kinds = (await PM.relkind(conn, f"{fx}.{PS.OLD}"), await PM.relkind(conn, f"{fx}.{PS.PRE}"),
                 await PM.relkind(conn, f"{fx}.{PS.VIEW_V3}"))
        m_sw = await PM.migration_row(conn, fx)
        check(sw["action"] == "swapped" and kinds == ("v", "r", None) and m_sw["store_mode"] == "lean"
              and m_sw["phase"] == "swapped" and sw["dirty_checked"] >= 2,
              f"swap in ONE transaction ({sw['lock_held_s']} s under the write lock, {sw['dirty_checked']} dirty "
              f"securities re-verified): table → {PS.PRE}, view → {PS.OLD}, store_mode → lean", f"{sw} {kinds}")
        trig2 = await conn.fetchval(f"SELECT count(*) FROM pg_trigger WHERE tgrelid = '{fx}.{PS.PRE}'::regclass AND "
                                    "tgname = 'prices3_dirty'")
        vopt = await conn.fetchval(f"SELECT reloptions FROM pg_class WHERE oid = '{fx}.{PS.OLD}'::regclass")
        check(trig2 == 0 and "security_invoker=true" in (vopt or []),
              "the capture trigger is gone; the name now resolves to the security_invoker view")
        after = await readers(conn, fx)
        for name, f_, _ in READERS:
            k = name.split()[0]
            pairs = [x for x in (k, k + "b") if x in before]
            same = all(before[x] == after[x] for x in pairs)
            nonempty = all(bool(before[x]) and before[x] not in ({}, []) for x in pairs)
            check(same and nonempty, f"{name}: IDENTICAL before vs after (non-empty)",
                  f"{[(x, first_diff(before[x], after[x])) for x in pairs if before[x] != after[x]]}")
        check(bool(before["R3"]) and bool(before["R3b"]) and before["R3b"] != before["R3"],
              "…the org reads differ between org A and org B (RLS is in the results)")
        async with conn.transaction():
            await as_role(conn, super_admin=True)
            amb = await conn.fetchval(
                f"SELECT count(*) FROM (SELECT global_security_id, price_date FROM {fx}.{PS.OLD} WHERE org_id IS NULL "
                f"AND valid_to IS NULL AND system_to IS NULL AND price_type = 'close' GROUP BY 1, 2 HAVING "
                f"count(DISTINCT COALESCE(provider_key, '')) > 1) x")
        fs_src = _read("apps/api/services/fix_sim_pl.py") + _read("apps/api/cpsim/book.py")
        find("R13 fix_sim_pl.last_platform_closes / R14 cpsim platform_price order by price_date ONLY (no provider / "
             "system_from tie-break)",
             f"when two platform providers hold a close for the same day ({amb:,} such (security, day) pairs in this "
             "fixture) the row they return is arbitrary — on the OLD table as much as on the view; pre-existing, not "
             "changed here (no reader changes behaviour). Compared on single-provider securities; R11/R12 tie-break "
             "on system_from and are compared on all" if "ORDER BY global_security_id, price_date DESC\"" in fs_src
             and "ORDER BY price_date DESC LIMIT 1\"" in fs_src else "source changed — re-check")
        ids_b, ids_a = before["_R12_ids"], after["_R12_ids"]
        flat_a = [i for rows in ids_a for i in rows]
        find("R12 the security detail's price `id`: live rows get a NEW id after the swap",
             f"before {ids_b[0][:1]} → after {ids_a[0][:1]}; nothing references a price id (no FK, no stored copy — "
             "discovery 1a); history rows keep their real ids; every other column is identical")
        again = await readers(conn, fx)
        check(len(set(flat_a)) == len(flat_a) and again["_R12_ids"] == ids_a,
              "…the new ids are unique and STABLE (a second read returns the same ids)")
        asof_new = await as_of_answers(conn, fx, None, stamps, ids_probe)
        asof_view = await as_of_answers(conn, fx, f"{fx}.{PS.OLD}", stamps, ids_probe)
        check(asof_old == asof_new == asof_view and all(asof_old),
              f"as-of: price_as_known(T) = the same predicate on the old table, at {len(stamps)} instants (closed "
              "rows keep their ids)", f"{[len(x) for x in asof_old]} vs {[len(x) for x in asof_new]}")
        check(await conn.fetchval(f"SELECT count(*) FROM {fx}.{PS.OLD}") == n_view_before,
              "the view under the old name holds every row it held before the swap")
        # restatement after the swap (system axis) vs the legacy writer on a control copy
        sec, day = gs(fx, 11), date(2026, 10, 15)
        b0 = await view_rows(conn, f"{fx}.{PS.OLD}", sec, day)
        await drop_fixture(conn, ctl)
        await conn.execute(f"CREATE SCHEMA {ctl}")
        await conn.execute(f"COMMENT ON SCHEMA {ctl} IS '{MARKER}'")
        await conn.execute(f"CREATE TABLE {ctl}.{PS.OLD} (LIKE portfolio.{PS.OLD} INCLUDING DEFAULTS INCLUDING "
                           f"CONSTRAINTS INCLUDING INDEXES)")
        await conn.execute(f"INSERT INTO {ctl}.{PS.OLD} SELECT * FROM {fx}.{PS.PRE} WHERE global_security_id = $1",
                           sec)
        await conn.execute(f"GRANT USAGE ON SCHEMA {ctl} TO app_service")
        await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {ctl}.{PS.OLD} TO app_service")
        PS.SCHEMA = ctl
        mode_ctl = None
        async with PM.ptx(conn):
            mode_ctl = await PS.store_mode(conn)
            r_ctl = await W.upsert_eod_bars(conn, sec, bars(day, "55.5555"), provider_key=SRC, licence=LIC,
                                            currency="USD")
        PS.SCHEMA = fx
        async with PM.ptx(conn):
            r_new = await W.upsert_eod_bars(conn, sec, bars(day, "55.5555"), provider_key=SRC, licence=LIC,
                                            currency="USD")
        a_new = await view_rows(conn, f"{fx}.{PS.OLD}", sec, day)
        a_ctl = await view_rows(conn, f"{ctl}.{PS.OLD}", sec, day)
        hist = await conn.fetch(f"SELECT h.* FROM {fx}.security_price_history h JOIN {fx}.price_series se ON se.id = "
                                f"h.series_id WHERE se.global_security_id = $1 AND h.price_date = $2", sec, day)
        check(mode_ctl == "legacy" and r_ctl == r_new == {"inserted": 0, "restated": 1, "unchanged": 0}
              and shape(a_new) == shape(a_ctl) == [(str(b0[0]["price"]), "system", True), ("55.5555", "live", None)]
              and len(hist) == 1 and hist[0]["id"] == b0[0]["id"],
              "restatement AFTER the swap: the superseded close lands in security_price_history (keeping the id the "
              "view showed) and the view shows both versions with EXACTLY the legacy writer's shape",
              f"{shape(a_new)} vs {shape(a_ctl)} hist={len(hist)}")
        bar_n = await conn.fetchval(f"SELECT count(*) FROM {fx}.price_bars_pending b JOIN {fx}.price_series se ON "
                                    f"se.id = b.series_id WHERE se.global_security_id = $1 AND b.price_date = $2",
                                    sec, day)
        check(bar_n == 1, "…its OHLCV bar waits in price_bars_pending for the archive (the view's bar is {})")
        # nightly writes lean after the switch
        pre_n = await conn.fetchval(f"SELECT count(*) FROM {fx}.{PS.PRE}")
        lean_n = await conn.fetchval(f"SELECT count(*) FROM {fx}.security_prices")
        async with PM.ptx(conn):
            r_n = await W.upsert_eod_bars(conn, gs(fx, 12), bars(date(2026, 11, 9), "42.4200"), provider_key=SRC,
                                          licence=LIC, currency="USD")
            r_eq = await W.upsert_eod_bars(conn, gs(fx, 12), bars(date(2026, 11, 9), "42.4200"), provider_key=SRC,
                                           licence=LIC, currency="USD")
        check(r_n["inserted"] == 1 and r_eq["unchanged"] == 1
              and await conn.fetchval(f"SELECT count(*) FROM {fx}.security_prices") == lean_n + 1
              and await conn.fetchval(f"SELECT count(*) FROM {fx}.{PS.PRE}") == pre_n,
              "the nightly writer (writes.upsert_eod_bars — receptor / backfill / nightly / tick) writes the LEAN "
              "table after the switch; an equal close writes nothing; the old table is untouched")
        # org upload after the switch (valid axis) under app_service + org A
        oday = date(2026, 10, 20)
        osec = org_gs(fx, "A", 1)
        b_org = await view_rows(conn, f"{fx}.{PS.OLD}", osec, oday, org=ORG_A)
        async with conn.transaction():
            await as_role(conn, org=ORG_A)
            n_up = await W.upsert_prices(None, osec, [(oday, D("999.5"))], provider_key="csv_upload", org_id=ORG_A,
                                         upload_id=UPL_A2, licence={"attribution": "org file 2"}, conn=conn)
        a_org = await view_rows(conn, f"{fx}.{PS.OLD}", osec, oday, org=ORG_A)
        check(n_up == 1 and shape(a_org) == [(str(b_org[0]["price"]), "valid", True), ("999.5", "live", None)]
              and str(a_org[-1]["upload_id"]) == UPL_A2,
              "an org upload AFTER the switch (writes.upsert_prices, org A's own RLS context): the old value closes on "
              "the VALID axis in history, the new one is live with the new upload", f"{shape(a_org)}")
        # RLS both ways on the view
        async def visible(org):
            async with conn.transaction():
                await as_role(conn, org=org)
                return {(str(r[0]) if r[0] else None) for r in await conn.fetch(
                    f"SELECT DISTINCT org_id FROM {fx}.{PS.OLD}")}
        va, vb, vn = await visible(ORG_A), await visible(ORG_B), await visible(None)
        check(va == {None, ORG_A} and vb == {None, ORG_B} and vn == {None},
              "RLS (app_service): org A sees platform + its own prices, org B platform + its own, no org platform only",
              f"A={va} B={vb} none={vn}")
        refused = []
        for label, coro_fn in (
                ("org A writes an org-B price through the writer", lambda: W.upsert_prices(
                    None, org_gs(fx, "B", 0), [(oday, D("1.5"))], provider_key="csv_upload", org_id=ORG_B,
                    upload_id=UPL_B, licence={}, conn=conn)),
                ("org A inserts an org-B row through the view", lambda: conn.execute(
                    f"INSERT INTO {fx}.{PS.OLD} (global_security_id, price_date, price, provider_key, org_id, "
                    f"upload_id) VALUES ($1, $2, 1.5, 'csv_upload', $3, $4)", org_gs(fx, "B", 0), oday, ORG_B, UPL_B)),
                ("org A writes a PLATFORM price", lambda: conn.execute(
                    f"INSERT INTO {fx}.{PS.OLD} (global_security_id, price_date, price, provider_key) VALUES ($1, $2, "
                    f"1.5, $3)", gs(fx, 1), date(2026, 11, 10), SRC))):
            try:
                async with conn.transaction():
                    await as_role(conn, org=ORG_A)
                    await coro_fn()
                refused.append((label, False))
            except (asyncpg.InsufficientPrivilegeError, asyncpg.exceptions.PostgresError) as exc:
                refused.append((label, isinstance(exc, asyncpg.InsufficientPrivilegeError) or
                                "row-level security" in str(exc) or "not visible" in str(exc)))
        check(all(ok for _, ok in refused), "RLS refuses: " + "; ".join(lbl for lbl, _ in refused), f"{refused}")
        async with conn.transaction():
            await as_role(conn, org=ORG_A)
            upd = await conn.execute(f"UPDATE {fx}.{PS.OLD} SET price = 1 WHERE org_id = $1", ORG_B)
        check(upd == "UPDATE 0", "…and org A updating org B's rows touches zero rows", upd)
        # old-shaped writes through the view (a stale legacy process / a verify fixture)
        async with PM.ptx(conn):
            vid = await conn.fetchval(
                f"INSERT INTO {fx}.{PS.OLD} (global_security_id, price_date, price, currency_code, source, provider_key, "
                f"as_of) VALUES ($1, $2, 3.25, 'USD', 'manual', 'platform_entry', now()) RETURNING id", gs(fx, 13),
                date(2026, 11, 10))
            got = await conn.fetchrow(f"SELECT price, provider_key FROM {fx}.{PS.OLD} WHERE id = $1", vid)
            lid = await PS.lean_add_price(conn, gsid=gs(fx, 13), price_date=date(2026, 11, 11), amount=D("3.5"),
                                          currency_code="USD", price_type="close", source="manual")
            got2 = await conn.fetchval(f"SELECT price FROM {fx}.{PS.OLD} WHERE id = $1::uuid", lid)
            dele = await conn.execute(f"DELETE FROM {fx}.{PS.OLD} WHERE id = $1", vid)
        check(got is not None and got["price"] == D("3.25") and got2 == D("3.5") and dele == "DELETE 1",
              "an old-shaped INSERT … RETURNING id through the view lands in the lean table and reads back by that "
              "id; add_price's lean path returns the view's id; DELETE through the view removes it")
        # ROLLBACK (post-swap changes written back)
        post_view = await conn.fetchval(f"SELECT count(*) FROM {fx}.{PS.OLD}")
        rb = await PM.rollback(conn, fx)
        kinds_rb = (await PM.relkind(conn, f"{fx}.{PS.OLD}"), await PM.relkind(conn, f"{fx}.{PS.VIEW_V3}"),
                    await PM.relkind(conn, f"{fx}.{PS.PRE}"))
        m_rb = await PM.migration_row(conn, fx)
        async with PM.ptx(conn):
            allg = [str(r[0]) for r in await conn.fetch(f"SELECT DISTINCT global_security_id FROM {fx}.{PS.OLD}")]
            c_tab = await PM.checksums(conn, f"{fx}.{PS.OLD}", "ids", [allg])
            c_view = await PM.checksums(conn, f"{fx}.{PS.VIEW_V3}", "ids", [allg])
        check(kinds_rb == ("r", "v", None) and m_rb["store_mode"] == "legacy" and m_rb["phase"] == "rolled_back"
              and rb["rows"] == post_view and c_tab == c_view and rb["written_back"]["added"] >= 3
              and rb["written_back"]["closed"] >= 2,
              f"--rollback: post-swap changes written back ({rb['written_back']}), names restored, store_mode legacy; "
              "the restored table = the view, security by security (count + md5)", f"{rb} {kinds_rb}")
        async with PM.ptx(conn):
            r_leg = await W.upsert_eod_bars(conn, gs(fx, 12), bars(date(2026, 11, 10), "43.0000"), provider_key=SRC,
                                            licence=LIC, currency="USD")
        check(r_leg["inserted"] == 1 and await conn.fetchval(
            f"SELECT count(*) FROM {fx}.{PS.OLD} WHERE price_date = '2026-11-10' AND global_security_id = $1",
            gs(fx, 12)) == 1, "…and the writers write the old table again (the switch read 'legacy')")
        # guarded drop
        for fn, why in ((lambda: PM.rollback(conn, fx), "--rollback when not swapped"),
                        (lambda: PM.copy(conn, fx), "a copy after a rollback without --reset-copy")):
            try:
                await fn()
                check(False, f"{why} is refused")
            except PM.Refused:
                check(True, f"{why} is refused")
        await PM.reset_copy(conn, fx)
        await PM.copy(conn, fx, batch=20_000, sleep=0)
        await PM.catch_up(conn, fx)
        g2 = await PM.verify_all(conn, fx, batch=20_000, sleep=0)
        await PM.swap(conn, fx)
        check(g2["ok"] is True, "--reset-copy → copy → verify GREEN → swap again (for the drop proofs)", f"{g2}")
        # bytes / row — measured on the CLEAN copy (the first copy carries the killed batch's dead tuples)
        mz = await measure(conn, fx)
        old_t = await conn.fetchval(f"SELECT pg_total_relation_size('{fx}.{PS.PRE}')::float / count(*) FROM "
                                    f"{fx}.{PS.PRE}")
        per = (mz["heap"] + mz["idx"]) / mz["n"]
        print(f"[INFO] bytes/row: lean heap {mz['heap'] / mz['n']:.1f} + indexes {mz['idx'] / mz['n']:.1f} = {per:.1f} "
              f"(total incl. FSM/VM {mz['total'] / mz['n']:.1f}; avg tuple {float(mz['tuple']):.1f}); fixture old "
              f"table {old_t:.1f}")
        check(per <= 125 and per < old_t / 4, f"MEASURED lean bytes/row incl. indexes = {per:.1f} (fixture old table "
              f"{old_t:.1f}); target ≤ ~100", f"{mz}")
        SECTION_NOTES["bytes_per_row"] = {"lean_heap": round(mz["heap"] / mz["n"], 1),
                                          "lean_indexes": round(mz["idx"] / mz["n"], 1), "lean": round(per, 1),
                                          "old_fixture": round(old_t, 1), "avg_tuple": round(float(mz["tuple"]), 1)}
        try:
            await PM.reset_copy(conn, fx)
            check(False, "--reset-copy is refused once swapped")
        except PM.Refused:
            check(True, "--reset-copy is refused once swapped (the lean tables are the record)")
        code = await PM.run(["--drop-old", "--schema", fx])
        msgs = []
        for kw, prep in ((dict(confirm=False), None), (dict(confirm=True), "red"), (dict(confirm=True), "no_archive")):
            if prep == "red":
                async with PM.ptx(conn):
                    await conn.execute(f"UPDATE {fx}.prices3_migration SET verified_ok = false WHERE id = 1")
            try:
                await PM.drop_old(conn, fx, **kw)
                msgs.append((prep, "DROPPED"))
            except PM.Refused as exc:
                msgs.append((prep, str(exc)))
            if prep == "red":
                async with PM.ptx(conn):
                    await conn.execute(f"UPDATE {fx}.prices3_migration SET verified_ok = true WHERE id = 1")
        check(code == 3 and "--i-confirm was not passed" in msgs[0][1] and "verification is not green" in msgs[1][1]
              and "archive export" in msgs[2][1] and await PM.relkind(conn, f"{fx}.{PS.PRE}") == "r",
              "--drop-old REFUSES: without --i-confirm (CLI exit 3), with a red verification, without a green "
              "archive export — the old table is still there", f"{msgs}")
        root = tempfile.mkdtemp(prefix="zzp3_m_store_")
        try:
            ex = await PM.export_archive(conn, fx, store=PA.LocalStore(root), sleep=0)
            dr = await PM.drop_old(conn, fx, confirm=True)
            m_d = await PM.migration_row(conn, fx)
            n_after = await conn.fetchval(f"SELECT count(*) FROM {fx}.{PS.OLD}")
            check(ex["ok"] and dr["action"] == "dropped" and await PM.relkind(conn, f"{fx}.{PS.PRE}") is None
                  and m_d["phase"] == "dropped" and n_after > 50_000,
                  f"with the export green + --i-confirm: dropped ({dr['freed_bytes']:,} B freed); the view still reads "
                  f"{n_after:,} rows", f"{ex} {dr}")
        finally:
            shutil.rmtree(root, ignore_errors=True)
    finally:
        PS.SCHEMA = "portfolio"
        for s_ in (fx, ctl):
            await drop_fixture(conn, s_)


# ═══ section a — the archive ════════════════════════════════════════════════
async def pg_frame(conn, fx: str, gsids, start, end, *, field="price", provider_key=None) -> dict:
    """The Postgres side of parity: {(date, gsid): value} from the view (platform, live), the same provider rule."""
    async with conn.transaction():
        await as_role(conn, super_admin=True)
        rows = await conn.fetch(
            f"SELECT global_security_id::text AS g, provider_key, price_date, price, adjustment_factor FROM "
            f"{fx}.{PS.OLD} WHERE org_id IS NULL AND valid_to IS NULL AND system_to IS NULL AND price_type = 'close' "
            f"AND global_security_id = ANY($1::uuid[]) AND price_date BETWEEN $2 AND $3 AND ($4::text IS NULL OR "
            f"provider_key = $4)", gsids, start, end, provider_key)
    if provider_key is None:
        cnt: dict = {}
        for r in rows:
            cnt[(r["g"], r["provider_key"])] = cnt.get((r["g"], r["provider_key"]), 0) + 1
        best = {}
        for (g, pk), n in sorted(cnt.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1] or "")):
            best.setdefault(g, pk)
        rows = [r for r in rows if best.get(r["g"]) == r["provider_key"]]
    return {(r["price_date"], r["g"]): r[field] for r in rows}


def frame_dict(df) -> dict:
    out = {}
    for d_, row in df.iterrows():
        for g, v in row.items():
            if v is not None and not (isinstance(v, float) and v != v):
                out[(d_, g)] = v
    return out


async def y_archive(conn, dsn):
    fx = "zzp3_a"
    section(f"[A] Archive on its own swapped fixture ({fx}) — export, parity with Postgres, org rows excluded, the "
            "nightly step, compaction")
    clock = await conn.fetchval("SELECT now()")
    print(f"[INFO] section clock: DB now() = {clock.isoformat()}")
    PS.SCHEMA = fx
    root = tempfile.mkdtemp(prefix="zzp3_a_store_")
    store = PA.LocalStore(root)
    pool = await asyncpg.create_pool(dsn, statement_cache_size=0, min_size=1, max_size=2, ssl="require")
    try:
        st = await build_fixture(conn, fx, n_sec=12)
        skip = await PA.archive_step(pool, store=store)
        check(skip["action"] == "skipped" and "legacy" in skip["reason"],
              "the nightly archive step does nothing before the swap (store_mode legacy)", f"{skip}")
        await PM.copy(conn, fx, batch=20_000, sleep=0)
        v = await PM.verify_all(conn, fx, batch=20_000, sleep=0)
        await PM.swap(conn, fx)
        wait = await PA.archive_step(pool, store=store)
        check(v["ok"] and wait["action"] == "skipped" and "export" in wait["reason"],
              "after the swap and before the export: skipped, waiting for the migration's export", f"{wait}")
        ex = await PM.export_archive(conn, fx, store=store, sleep=0)
        check(ex["ok"] and len(ex["years"]) == 6 and all(y["ok"] for y in ex["years"].values()),
              f"export: {sum(y['rows'] for y in ex['years'].values()):,} live platform rows → one compact Parquet "
              "per year (6 years), read back by count + exact sum of price", f"{ex}")
        wm = await conn.fetchval(f"SELECT watermark FROM {fx}.price_archive_state WHERE prefix = $1", PA.PREFIX)
        sw = (await PM.migration_row(conn, fx))["swapped_at"]
        check(wm == sw, "…and the archive watermark = swapped_at (later writes are the daily append's)")
        plat = [gs(fx, i) for i in range(12)]
        windows = (("all 12, five years", plat, date(2021, 11, 1), date(2026, 12, 31), None),
                   ("4 securities, the last year", plat[:4], date(2025, 11, 7), date(2026, 11, 6), None),
                   ("one security across a year boundary", plat[6:7], date(2023, 12, 1), date(2024, 1, 31), None),
                   ("explicit provider (the second source)", plat[:3], date(2026, 1, 1), date(2026, 12, 31), ALT))
        for label, ids, a, b, pk in windows:
            pgd = await pg_frame(conn, fx, ids, a, b, provider_key=pk)
            ard = frame_dict(PA.history_frame(ids, a, b, store=store, provider_key=pk))
            check(bool(pgd) and set(pgd) == set(ard) and all(pgd[k] == ard[k] for k in pgd),
                  f"parity (price) — {label}: {len(pgd):,} cells IDENTICAL archive vs Postgres",
                  f"pg {len(pgd)} archive {len(ard)}; diff {[k for k in pgd if pgd.get(k) != ard.get(k)][:3]}")
        pga = await pg_frame(conn, fx, plat, date(2021, 11, 1), date(2026, 12, 31), field="adjustment_factor")
        ara = frame_dict(PA.history_frame(plat, date(2021, 11, 1), date(2026, 12, 31), store=store,
                                          field="adjustment_factor"))
        check(set(k for k, v_ in pga.items() if v_ is not None) == set(ara) and all(pga[k] == ara[k] for k in ara),
              f"parity (adjustment_factor, NULLs included): {len(ara):,} cells identical")
        async with conn.transaction():
            await as_role(conn, super_admin=True)
            old_bars = {(r["price_date"], str(r["global_security_id"])): r["bar"] for r in await conn.fetch(
                f"SELECT global_security_id, price_date, bar FROM {fx}.{PS.PRE} WHERE global_security_id = ANY($1::uuid[]) "
                f"AND provider_key = $2 AND org_id IS NULL AND valid_to IS NULL AND system_to IS NULL AND price_date >= "
                f"'2026-01-01'", plat[:5], SRC)}
        arb = frame_dict(PA.history_frame(plat[:5], date(2026, 1, 1), date(2026, 12, 31), store=store, field="bar",
                                          provider_key=SRC))
        check(bool(old_bars) and all(json.loads(arb[k]) == json.loads(old_bars[k]) for k in old_bars)
              and set(arb) == set(old_bars),
              f"the OHLCV bar moved to the archive intact: {len(old_bars):,} bars identical to the old table's")
        allrows = PA.read_rows(store, None, None, None)
        org_ids = {org_gs(fx, "A", i) for i in range(3)} | {org_gs(fx, "B", i) for i in range(2)}
        check(len(allrows) > 0 and "csv_upload" not in set(allrows["provider_key"].dropna())
              and not (set(allrows["global_security_id"]) & org_ids),
              "org-uploaded prices are NEVER in the archive (no csv_upload row, no org security)")
        # the daily append after the switch
        async with PM.ptx(conn):
            await W.upsert_eod_bars(conn, plat[0], bars(date(2026, 11, 9), "11.1100", open_="11"), provider_key=SRC,
                                    licence=LIC, currency="USD")
            await W.upsert_eod_bars(conn, plat[1], bars(date(2026, 11, 9), "22.2200", open_="22"), provider_key=SRC,
                                    licence=LIC, currency="USD")
            await W.upsert_eod_bars(conn, plat[2], bars(date(2026, 10, 1), "33.3300", open_="33"), provider_key=SRC,
                                    licence=LIC, currency="USD")
        now2 = await conn.fetchval("SELECT now() + interval '1 second'")
        ap = await PA.archive_step(pool, store=store, now=now2, settle_minutes=0, compact=False)
        pend = await conn.fetchval(f"SELECT count(*) FROM {fx}.price_bars_pending")
        check(ap["action"] == "appended" and ap["rows"] == 3 and set(ap["files"]) == {"2026"} and pend == 0,
              "daily append after the switch: 3 rows (2 new closes + 1 restatement) → one append part for 2026; "
              "pending bars drained", f"{ap} pending={pend}")
        # (in this window both sources hold equal point counts, so the default rule picks the lower key — name it)
        pgd = await pg_frame(conn, fx, plat[:3], date(2026, 9, 1), date(2026, 12, 31), provider_key=SRC)
        ard = frame_dict(PA.history_frame(plat[:3], date(2026, 9, 1), date(2026, 12, 31), store=store,
                                          provider_key=SRC))
        check(set(pgd) == set(ard) and all(pgd[k] == ard[k] for k in pgd) and ard[(date(2026, 10, 1), plat[2])] ==
              D("33.33") and (date(2026, 11, 9), plat[0]) in ard,
              "parity after the append: the new closes are there and the RESTATED value wins over the exported one")
        nb = frame_dict(PA.history_frame(plat[:1], date(2026, 11, 9), date(2026, 11, 9), store=store, field="bar",
                                         provider_key=SRC))
        check(json.loads(nb[(date(2026, 11, 9), plat[0])]).get("open") == "11",
              "…with the new bar (from price_bars_pending)")
        again = await PA.archive_step(pool, store=store, now=now2, settle_minutes=0, compact=False)
        check(again["action"] == "nothing_new", "a second run with nothing new appends nothing", f"{again}")
        before_c = frame_dict(PA.history_frame(plat, date(2021, 11, 1), date(2026, 12, 31), store=store))
        cp = await PA.archive_step(pool, store=store, now=now2, settle_minutes=0, compact=True)
        keys = [k for k, _ in store.list(PA.PREFIX)]
        after_c = frame_dict(PA.history_frame(plat, date(2021, 11, 1), date(2026, 12, 31), store=store))
        month = await conn.fetchval(f"SELECT compacted_month FROM {fx}.price_archive_state WHERE prefix = $1",
                                    PA.PREFIX)
        check(any(c["action"] == "compacted" for c in cp.get("compaction", [])) and all(k.endswith("/compact.parquet")
                                                                                         for k in keys)
              and len(keys) == 6 and before_c == after_c and month == now2.strftime("%Y-%m"),
              f"monthly compaction: parts merged into one file per year ({len(keys)} files), the frame IDENTICAL "
              f"({len(after_c):,} cells), compacted_month recorded", f"{cp} {keys}")
        nodb = await PA.archive_step(pool, store=None, now=now2, settle_minutes=0, compact=False)
        check(nodb["action"] == "skipped" and "R2 credentials" in nodb["reason"],
              "without R2 credentials the step says so and does nothing (never raises)", f"{nodb}")
        find("backtest history read left on Postgres",
             "measured 2026-11-13 on the live table: 30 securities × 5 y = 36,778 rows via an index scan touching 4,612 "
             "pages — an indexed range read, not a long scan; it also needs org-uploaded prices + the rank walk, which "
             "the platform-only archive cannot give (docs/PRICES3_NOTES.md 2f)")
    finally:
        PS.SCHEMA = "portfolio"
        await pool.close()
        shutil.rmtree(root, ignore_errors=True)
        await drop_fixture(conn, fx)


# ═══ regressions / driver ═══════════════════════════════════════════════════
def regression():
    section("[J] Regressions — each ONCE, flat, VERIFY_NESTED=1, its OWN final TOTAL line (fast)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


SECTIONS = {"s": "schema", "m": "migrate", "a": "archive", "j": "regressions"}
SECTION_NOTES: dict = {}


async def run(name, fn, *args):
    t0 = time.monotonic()
    try:
        await fn(*args)
    except Exception as exc:  # noqa: BLE001
        check(False, f"section '{name}' raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"[INFO] {name}: {time.monotonic() - t0:.1f}s", flush=True)


async def real_fingerprint(conn) -> dict:
    async with PM.ptx(conn):
        return {"relkind": await PM.relkind(conn, f"portfolio.{PS.OLD}"),
                "new_tables": {t_: int(await conn.fetchval(f"SELECT count(*) FROM portfolio.{t_}"))
                               for t_ in ("price_series", "price_provenance", "security_prices",
                                          "security_price_history", "price_bars_pending", "price_archive_state",
                                          "prices3_dirty")},
                "migration": {k: v for k, v in (await conn.fetchrow(
                    "SELECT phase, store_mode, copied_rows FROM portfolio.prices3_migration WHERE id = 1")).items()},
                "fixture_rows_in_real": int(await conn.fetchval(
                    f"SELECT count(*) FROM portfolio.{PS.OLD} WHERE provider_key LIKE 'zzp3%'"))}


async def leftovers(conn) -> list:
    return [r[0] for r in await conn.fetch("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'zzp3\\_%'")]


async def main_async() -> int:
    dsn, how = await operator()
    conn = await connect(dsn)
    print(f"[INFO] database via {how} as {await conn.fetchval('SELECT current_user')}; fixture schemas zzp3_*")
    if "--cleanup-only" in sys.argv:
        for s_ in await leftovers(conn):
            await drop_fixture(conn, s_)
        check(not await leftovers(conn), "--cleanup-only: zero fixture schemas remain")
        await conn.close()
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 0 if _n_fail == 0 else 1
    only = None
    if "--section" in sys.argv:
        only = {x.strip() for x in sys.argv[sys.argv.index("--section") + 1].lower().split(",") if x.strip()}
        if not only or not only <= set(SECTIONS):
            print(f"[FAIL] unknown section(s) {sorted(only - set(SECTIONS))}; one of {sorted(SECTIONS)}")
            print("TOTAL: 0 PASS, 1 FAIL")
            return 1
    want = (lambda k: True) if only is None else (lambda k: k in only)
    real0 = None
    try:
        for s_ in await leftovers(conn):
            await drop_fixture(conn, s_)
        check(not await leftovers(conn), "pre-run cleanup: zero fixture schemas")
        real0 = await real_fingerprint(conn)
        if want("s"):
            await run("schema", y_schema, conn)
        if want("m"):
            await run("migrate", y_migrate, conn, dsn)
        if want("a"):
            await run("archive", y_archive, conn, dsn)
        check(NETWORK_CALLS == [], "no network call was attempted (R2 client refused if asked)", f"{NETWORK_CALLS}")
    finally:
        section("Teardown — zero fixture schemas; the real PRICES3 tables still empty; the real table still the table")
        PS.SCHEMA = "portfolio"
        try:
            for s_ in await leftovers(conn):
                await drop_fixture(conn, s_)
            check(not await leftovers(conn), "zero fixture schemas remain")
            if real0 is not None:
                real1 = await real_fingerprint(conn)
                check(real1 == real0 and real1["relkind"] == "r" and not any(real1["new_tables"].values())
                      and real1["fixture_rows_in_real"] == 0,
                      "real objects unchanged: portfolio.securities_global_prices is the table, the PRICES3 tables are "
                      "empty, the migration row untouched, no fixture row in the real table", f"{real0} → {real1}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
        await conn.close()
    if SECTION_NOTES:
        print(f"[INFO] measured: {json.dumps(SECTION_NOTES)}")
    if only is None:
        if os.environ.get("VERIFY_NESTED") == "1":
            print("[SKIP] nested run")
        else:
            regression()
    elif "j" in only:
        regression()
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
