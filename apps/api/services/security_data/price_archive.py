"""PRICES3 — the price HISTORY ARCHIVE: Parquet files in R2, read with DuckDB (pyarrow when DuckDB is absent).

LAYOUT  one prefix (``PREFIX``), partitioned by year:
            <PREFIX>/year=YYYY/compact.parquet                 one per year (monthly compaction / the migration export)
            <PREFIX>/year=YYYY/append-<bound>-<id>.parquet     the daily appends since the last compaction
        A row = one platform close as the lean table holds it (+ its OHLCV bar when known), keyed by (security,
        price_type, provider, date); a later ``recorded_at`` supersedes an earlier one (readers and compaction keep
        the latest). PLATFORM ROWS ONLY — org-uploaded prices never leave Postgres (an R2 prefix has no RLS).

WRITE   ``archive_step(pool)`` — a step of the existing nightly prices job (security_master_jobs._prices): appends
        lean rows whose recorded_at is past the watermark (portfolio.price_archive_state) with their bars
        (portfolio.price_bars_pending, drained once archived); on the first run of a calendar month it compacts every
        year that has append parts. Never raises (returns its outcome). Nothing happens before the operator swap
        (store_mode 'legacy') or before the migration's archive export.

READ    ``history_frame(gsids, start, end, field=…)`` → pandas DataFrame, index = price_date, one column per
        security. Objects are fetched over the S3 API with the existing R2 credentials (boto3,
        services.storage.get_s3_client) into a local cache keyed by ETag, then scanned in-process by DuckDB.
        Exact: prices come back as Decimal.

Every function takes a ``store`` (``R2Store`` / ``LocalStore`` — a verify uses a filesystem store, no network)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from services.security_data import price_store as PS

PREFIX = "prices/archive/v1/eod"
COMPACT = "compact.parquet"
SETTLE_MINUTES = 15          # rows newer than this are left for the next run (a slow transaction's recorded_at)
DECIMAL_PRECISION, DECIMAL_SCALE = 38, 16
FIELDS = ("price", "adjustment_factor", "currency_code", "source", "as_of", "recorded_at", "quality_flags", "licence",
          "bar")
KEY = ("global_security_id", "price_type", "provider_key", "price_date")


# ── stores ──────────────────────────────────────────────────────────────────
class LocalStore:
    """A filesystem stand-in for R2 (same API). ``root`` / key = the object."""

    def __init__(self, root):
        self.root = pathlib.Path(root)

    def list(self, prefix: str) -> list[tuple[str, str]]:
        base = self.root / prefix
        if not base.exists():
            return []
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                st = p.stat()
                out.append((p.relative_to(self.root).as_posix(), f"{st.st_size}-{st.st_mtime_ns}"))
        return out

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def put(self, key: str, data: bytes) -> None:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)

    def local_path(self, key: str) -> str:
        return str(self.root / key)


class R2Store:
    """R2 over the S3 API with the existing credentials (services.storage)."""

    def __init__(self, bucket: str | None = None):
        from services import storage

        self.bucket = bucket or os.environ.get("PRICES_ARCHIVE_BUCKET") or storage.DEFAULT_BUCKET
        self._client = storage.get_s3_client()

    def list(self, prefix: str) -> list[tuple[str, str]]:
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix.rstrip("/") + "/"}
            if token:
                kw["ContinuationToken"] = token
            r = self._client.list_objects_v2(**kw)
            out += [(o["Key"], str(o.get("ETag", "")).strip('"')) for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return sorted(out)
            token = r.get("NextContinuationToken")

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="application/vnd.apache.parquet")

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def r2_configured() -> bool:
    return all(os.environ.get(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"))


def default_store():
    return R2Store() if r2_configured() else None


# ── Parquet ─────────────────────────────────────────────────────────────────
def arrow_schema():
    import pyarrow as pa

    dec = pa.decimal128(DECIMAL_PRECISION, DECIMAL_SCALE)
    ts = pa.timestamp("us", tz="UTC")
    return pa.schema([("global_security_id", pa.string()), ("price_type", pa.string()), ("provider_key", pa.string()),
                      ("price_date", pa.date32()), ("price", dec), ("adjustment_factor", dec),
                      ("currency_code", pa.string()), ("source", pa.string()), ("as_of", ts), ("recorded_at", ts),
                      ("quality_flags", pa.list_(pa.string())), ("licence", pa.string()), ("bar", pa.string())])


def _jsontext(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = json.loads(v)
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def normalise(r) -> dict:
    """One archive row from a DB record (view / lean join / old table) — the archive's own canonical shape."""
    g = r.get if hasattr(r, "get") else (lambda k, d=None: r[k])
    return {"global_security_id": str(g("global_security_id")), "price_type": g("price_type") or "close",
            "provider_key": g("provider_key"), "price_date": g("price_date"),
            "price": Decimal(str(g("price"))), "adjustment_factor": (Decimal(str(g("adjustment_factor")))
                                                                     if g("adjustment_factor") is not None else None),
            "currency_code": g("currency_code"), "source": g("source"), "as_of": g("as_of"),
            "recorded_at": g("recorded_at"), "quality_flags": list(g("quality_flags") or []),
            "licence": _jsontext(g("licence")) or "{}",
            "bar": _jsontext(g("bar")) if g("bar") not in (None, {}, "{}") else None}


def to_parquet(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    sch = arrow_schema()
    cols = {f.name: [r[f.name] for r in rows] for f in sch}
    tbl = pa.Table.from_pydict(cols, schema=sch)
    buf = io.BytesIO()
    pq.write_table(tbl, buf, compression="zstd", row_group_size=100_000)
    return buf.getvalue()


class YearWriter:
    """Streams one year's rows into ONE Parquet object (row groups as they come; one PUT at close)."""

    def __init__(self, store, key: str):
        import pyarrow.parquet as pq

        self.store, self.key, self.rows = store, key, 0
        self._buf = io.BytesIO()
        self._w = pq.ParquetWriter(self._buf, arrow_schema(), compression="zstd")

    def add(self, rows: list[dict]) -> None:
        import pyarrow as pa

        if not rows:
            return
        sch = arrow_schema()
        self._w.write_table(pa.Table.from_pydict({f.name: [r[f.name] for r in rows] for f in sch}, schema=sch))
        self.rows += len(rows)

    def close(self) -> int:
        self._w.close()
        self.store.put(self.key, self._buf.getvalue())
        return self.rows


def year_prefix(year: int, prefix: str = PREFIX) -> str:
    return f"{prefix}/year={int(year)}"


def compact_key(year: int, prefix: str = PREFIX) -> str:
    return f"{year_prefix(year, prefix)}/{COMPACT}"


def objects(store, prefix: str = PREFIX, years: set[int] | None = None) -> list[tuple[str, str]]:
    out = []
    for key, etag in store.list(prefix):
        part = key[len(prefix):].lstrip("/").split("/", 1)[0]
        if not part.startswith("year=") or not key.endswith(".parquet"):
            continue
        if years is None or int(part[5:]) in years:
            out.append((key, etag))
    return out


def _local(store, key: str, etag: str, cache_dir: str | None) -> str:
    if hasattr(store, "local_path"):
        return store.local_path(key)
    d = pathlib.Path(cache_dir or os.path.join(tempfile.gettempdir(), "prices3_archive_cache"))
    d.mkdir(parents=True, exist_ok=True)
    p = d / (hashlib.sha1(f"{key}|{etag}".encode()).hexdigest() + ".parquet")
    if not p.exists():
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(store.get(key))
        tmp.replace(p)
    return str(p)


# ── read ────────────────────────────────────────────────────────────────────
def engine_name() -> str:
    try:
        import duckdb  # noqa: F401
        return "duckdb"
    except ImportError:
        return "pyarrow"


def read_rows(store, gsids: list[str] | None, start: date | None, end: date | None, *, price_type: str = "close",
              provider_key: str | None = None, prefix: str = PREFIX, cache_dir: str | None = None,
              engine: str | None = None):
    """The deduplicated archive rows (latest recorded_at per key) as a pandas DataFrame."""
    import pandas as pd

    years = set(range(start.year, end.year + 1)) if (start and end) else None
    files = [_local(store, k, e, cache_dir) for k, e in objects(store, prefix, years)]
    cols = list(arrow_schema().names)
    if not files:
        return pd.DataFrame(columns=cols)
    engine = engine or engine_name()
    ids = [str(g) for g in gsids] if gsids else None
    if engine == "duckdb":
        import duckdb

        con = duckdb.connect()
        try:
            where, args = ["price_type = ?"], [price_type]
            if ids is not None:
                where.append("global_security_id IN (SELECT unnest(?::VARCHAR[]))")
                args.append(ids)
            if start:
                where.append("price_date >= ?")
                args.append(start)
            if end:
                where.append("price_date <= ?")
                args.append(end)
            if provider_key:
                where.append("provider_key = ?")
                args.append(provider_key)
            sql = (f"SELECT {', '.join(cols)} FROM read_parquet(?) WHERE {' AND '.join(where)} QUALIFY row_number() "
                   "OVER (PARTITION BY global_security_id, price_type, provider_key, price_date ORDER BY recorded_at "
                   "DESC) = 1")
            return con.execute(sql, [files] + args).df()
        finally:
            con.close()
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    dset = ds.dataset(files, format="parquet", schema=arrow_schema())
    f = pc.field("price_type") == price_type
    if ids is not None:
        f = f & pc.field("global_security_id").isin(ids)
    if start:
        f = f & (pc.field("price_date") >= start)
    if end:
        f = f & (pc.field("price_date") <= end)
    if provider_key:
        f = f & (pc.field("provider_key") == provider_key)
    df = dset.to_table(filter=f).to_pandas()
    if df.empty:
        return df
    df = df.sort_values(["global_security_id", "price_type", "provider_key", "price_date", "recorded_at"],
                        na_position="first")
    return df.drop_duplicates(subset=list(KEY), keep="last").reset_index(drop=True)


def choose_provider(df):
    """provider_key None ⇒ per security, the provider with the most points in the window (ties: lowest key) —
    the same rule prices._coverage uses for 'the' series."""
    if df.empty:
        return df
    n = df.groupby(["global_security_id", "provider_key"], dropna=False).size().reset_index(name="n")
    n["pk"] = n["provider_key"].fillna("")
    n = n.sort_values(["global_security_id", "n", "pk"], ascending=[True, False, True])
    best = n.drop_duplicates("global_security_id")[["global_security_id", "provider_key"]]
    return df.merge(best, on=["global_security_id", "provider_key"], how="inner")


def history_frame(gsids: list[str], start: date, end: date, *, field: str = "price", price_type: str = "close",
                  provider_key: str | None = None, store=None, prefix: str = PREFIX, cache_dir: str | None = None,
                  engine: str | None = None):
    """A date × security frame of ``field`` (price / adjustment_factor / bar / …) from the archive."""
    import pandas as pd

    if field not in FIELDS:
        raise ValueError(f"field is one of {FIELDS}")
    store = store or default_store()
    if store is None:
        raise RuntimeError("no archive store: R2 credentials are not configured")
    df = read_rows(store, gsids, start, end, price_type=price_type, provider_key=provider_key, prefix=prefix,
                   cache_dir=cache_dir, engine=engine)
    if df.empty:
        return pd.DataFrame(index=pd.Index([], name="price_date"))
    if provider_key is None:
        df = choose_provider(df)
    df["price_date"] = [d if isinstance(d, date) and not isinstance(d, datetime) else pd.Timestamp(d).date()
                        for d in df["price_date"]]
    if field in ("price", "adjustment_factor"):
        df[field] = [None if v is None or (isinstance(v, float) and v != v) else Decimal(str(v)) for v in df[field]]
    wide = df.pivot(index="price_date", columns="global_security_id", values=field).sort_index()
    order = [str(g) for g in gsids if str(g) in wide.columns]
    return wide[order]


# ── write: the nightly step ─────────────────────────────────────────────────
_LEAN_ROWS = """
SELECT se.global_security_id, se.price_type, se.provider_key, l.price_date, l.price, l.adjustment_factor,
       pv.currency_code, pv.source, l.as_of, l.recorded_at, {s}.price_flag_names(l.quality_bits) AS quality_flags,
       pv.licence, b.bar
  FROM {s}.security_prices l
  JOIN {s}.price_series se ON se.id = l.series_id AND se.org_id IS NULL
  JOIN {s}.price_provenance pv ON pv.id = l.provenance_id
  LEFT JOIN {s}.price_bars_pending b ON b.series_id = l.series_id AND b.price_date = l.price_date
                                    AND b.recorded_at = l.recorded_at
 WHERE l.recorded_at > $1 AND l.recorded_at <= $2
 ORDER BY se.global_security_id, l.price_date"""


def write_append(store, rows: list[dict], bound: datetime, prefix: str = PREFIX) -> dict:
    by_year: dict[int, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["price_date"].year, []).append(r)
    out = {}
    for y, rs in sorted(by_year.items()):
        key = f"{year_prefix(y, prefix)}/append-{bound.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.parquet"
        store.put(key, to_parquet(rs))
        out[str(y)] = {"key": key, "rows": len(rs)}
    return out


def compact_year(store, year: int, prefix: str = PREFIX, cache_dir: str | None = None) -> dict:
    """Rewrite year=YYYY as ONE compact.parquet (latest recorded_at per key), then delete the append parts."""
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    objs = objects(store, prefix, {year})
    parts = [k for k, _ in objs if not k.endswith("/" + COMPACT)]
    if not parts:
        return {"year": year, "parts": 0, "action": "nothing"}
    files = [_local(store, k, e, cache_dir) for k, e in objs]
    df = ds.dataset(files, format="parquet", schema=arrow_schema()).to_table().to_pandas()
    df = df.sort_values(list(KEY) + ["recorded_at"], na_position="first").drop_duplicates(subset=list(KEY),
                                                                                          keep="last")
    tbl = pa.Table.from_pandas(df.reset_index(drop=True), schema=arrow_schema(), preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(tbl, buf, compression="zstd", row_group_size=100_000)
    store.put(compact_key(year, prefix), buf.getvalue())
    for k in parts:
        store.delete(k)
    return {"year": year, "parts": len(parts), "rows": int(tbl.num_rows), "action": "compacted"}


async def archive_step(pool, *, store=None, now: datetime | None = None, prefix: str = PREFIX,
                       settle_minutes: int = SETTLE_MINUTES, compact: bool | None = None) -> dict:
    """The nightly job's archive step: daily append + monthly compaction. Never raises."""
    from services.replica_holdings import platform_tx

    s = PS.SCHEMA
    try:
        async with platform_tx(pool) as c:
            if await PS.store_mode(c) != "lean":
                return {"action": "skipped", "reason": "store_mode is legacy — the archive is written from the lean "
                                                       "table after the operator swap"}
            mig = await c.fetchrow(f"SELECT swapped_at, archive_ok FROM {s}.prices3_migration WHERE id = 1")
            st = await c.fetchrow(f"SELECT watermark, compacted_month FROM {s}.price_archive_state WHERE prefix = $1",
                                  prefix)
            db_now = await c.fetchval("SELECT now()")
        if st is None or st["watermark"] is None:
            return {"action": "skipped", "reason": "waiting for the migration's archive export "
                                                   "(scripts/prices3_migrate.py --export-archive sets the watermark)"}
        store = store or default_store()
        if store is None:
            return {"action": "skipped", "reason": "no R2 credentials (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
                                                   "R2_SECRET_ACCESS_KEY) on this service"}
        now = now or db_now
        bound = now - timedelta(minutes=settle_minutes)
        wm = st["watermark"]
        out: dict = {"action": "appended", "watermark_from": wm.isoformat(), "watermark_to": bound.isoformat(),
                     "files": {}, "rows": 0}
        if bound > wm:
            async with platform_tx(pool) as c:
                rows = [normalise(r) for r in await c.fetch(_LEAN_ROWS.format(s=s), wm, bound)]
            out["files"] = write_append(store, rows, bound, prefix) if rows else {}
            out["rows"] = len(rows)
            async with platform_tx(pool) as c:
                await c.execute(f"UPDATE {s}.price_archive_state SET watermark = $2, last_run_at = now(), "
                                f"last_result = $3::jsonb WHERE prefix = $1", prefix, bound,
                                json.dumps({k: v for k, v in out.items() if k != "files"}))
                out["bars_drained"] = int((await c.execute(
                    f"DELETE FROM {s}.price_bars_pending WHERE recorded_at <= $1", bound)).split()[-1])
        else:
            out["action"] = "nothing_new"
        month = now.strftime("%Y-%m")
        if compact is True or (compact is None and st["compacted_month"] != month):
            years = sorted({int(k[len(prefix):].lstrip("/").split("/", 1)[0][5:]) for k, _ in objects(store, prefix)})
            out["compaction"] = [compact_year(store, y, prefix) for y in years]
            async with platform_tx(pool) as c:
                await c.execute(f"UPDATE {s}.price_archive_state SET compacted_month = $2 WHERE prefix = $1", prefix,
                                month)
        return out
    except Exception as exc:  # noqa: BLE001 — a job step that never breaks the price job
        return {"action": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
