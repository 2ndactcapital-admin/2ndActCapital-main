"""PRICES3 — the lean price of record, its restatement history, and the compatibility view (docs/PRICES3_NOTES.md).

    <schema>.price_series            one row per (security, price_type, provider_key, org_id) — org_id NULL = platform
    <schema>.price_provenance        one row per (provider, source, currency, licence, upload, org) — the licence ONCE
    <schema>.price_quality_flags     bit ⇄ name for quality_flags
    <schema>.security_prices         THE LEAN ROW: one per (series, date) — always the current value
    <schema>.security_price_history  every superseded value, with its valid / system times exactly as they were
    <schema>.price_bars_pending      OHLCV bars written since the last archive append (drained by the archive step)
    <schema>.price_archive_state     the archive's watermark + last compaction month
    <schema>.prices3_migration       ONE row: store_mode ('legacy' | 'lean' — THE switch) + the operator migration
    <schema>.prices3_dirty           securities the old table saw writes for while the operator copy ran
    <schema>.securities_global_prices_v3
                                     the compatibility view (security_invoker): the old 19 columns from lean ∪
                                     history. The operator script renames it to securities_global_prices at the swap.

Every DDL statement is parameterised by schema (the verify builds the same objects in a fixture schema). The writers
read ``store_mode`` at run time: 'legacy' writes the old table exactly as before; 'lean' writes the lean table (+
history on a restatement). The operator script flips it inside the swap transaction."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

#: the schema every runtime helper uses (a verify points it at its fixture schema)
SCHEMA = "portfolio"
OLD = "securities_global_prices"
VIEW_V3 = "securities_global_prices_v3"
PRE = "securities_global_prices_pre_prices3"
MODES = ("legacy", "lean")
LIVE = "valid_to IS NULL AND system_to IS NULL"
#: the old table's 19 columns, in order — the compatibility view publishes exactly these
OLD_COLUMNS = ("id", "global_security_id", "price_date", "price", "currency_code", "price_type", "source", "valid_from",
               "valid_to", "system_from", "system_to", "provider_key", "as_of", "quality_flags", "licence", "org_id",
               "upload_id", "adjustment_factor", "bar")
NEW_TABLES = ("price_series", "price_provenance", "price_quality_flags", "security_prices", "security_price_history",
              "price_bars_pending", "price_archive_state", "prices3_migration", "prices3_dirty")
KNOWN_FLAGS = ("outlier",)
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def old_name(schema: str | None = None) -> str:
    """The name readers and legacy writers use (the old table before the swap, the view after)."""
    return f"{schema or SCHEMA}.{OLD}"


def t(name: str, schema: str | None = None) -> str:
    return f"{schema or SCHEMA}.{name}"


# ── policies (the old table's, verbatim; lean rows inherit them through their series) ──
_SA = "NULLIF(current_setting('app.is_super_admin', true), '') = 'true'"
_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"
_READ = f"(org_id IS NULL OR org_id = {_ORG} OR {_SA})"
_WRITE = f"({_SA} OR (org_id IS NOT NULL AND org_id = {_ORG}))"


def _org_policies(s: str, table: str) -> str:
    return f"""
ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS {table}_read ON {s}.{table};
CREATE POLICY {table}_read ON {s}.{table} FOR SELECT USING {_READ};
DROP POLICY IF EXISTS {table}_insert ON {s}.{table};
CREATE POLICY {table}_insert ON {s}.{table} FOR INSERT WITH CHECK {_WRITE};
DROP POLICY IF EXISTS {table}_update ON {s}.{table};
CREATE POLICY {table}_update ON {s}.{table} FOR UPDATE USING {_WRITE} WITH CHECK {_WRITE};
DROP POLICY IF EXISTS {table}_delete ON {s}.{table};
CREATE POLICY {table}_delete ON {s}.{table} FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');
"""


def _series_policies(s: str, table: str) -> str:
    """A lean / history / bar row is visible exactly when its series is (price_series carries the old policies)."""
    vis = f"(series_id IN (SELECT ps.id FROM {s}.price_series ps))"
    wr = (f"(series_id IN (SELECT ps.id FROM {s}.price_series ps WHERE {_SA} OR (ps.org_id IS NOT NULL AND "
          f"ps.org_id = {_ORG})))")
    return f"""
ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS {table}_read ON {s}.{table};
CREATE POLICY {table}_read ON {s}.{table} FOR SELECT USING {vis};
DROP POLICY IF EXISTS {table}_insert ON {s}.{table};
CREATE POLICY {table}_insert ON {s}.{table} FOR INSERT WITH CHECK {wr};
DROP POLICY IF EXISTS {table}_update ON {s}.{table};
CREATE POLICY {table}_update ON {s}.{table} FOR UPDATE USING {wr} WITH CHECK {wr};
DROP POLICY IF EXISTS {table}_delete ON {s}.{table};
CREATE POLICY {table}_delete ON {s}.{table} FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');
"""


def _platform_policies(s: str, table: str) -> str:
    """Readable by every role (the switch, the archive state); written by a platform transaction only."""
    return f"""
ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS {table}_read ON {s}.{table};
CREATE POLICY {table}_read ON {s}.{table} FOR SELECT USING (true);
DROP POLICY IF EXISTS {table}_write ON {s}.{table};
CREATE POLICY {table}_write ON {s}.{table} FOR ALL USING ({_SA}) WITH CHECK ({_SA});
"""


def schema_sql(schema: str = "portfolio", *, fks: bool = True) -> str:
    """Every PRICES3 object, idempotent (CREATE … IF NOT EXISTS / OR REPLACE). ``fks`` = the FKs onto
    securities_global / organizations (a fixture schema leaves them off: its securities are not in the master)."""
    s = schema
    fk_sec = "REFERENCES portfolio.securities_global(id)" if fks else ""
    fk_org = "REFERENCES public.organizations(id)" if fks else ""
    cols_sel = ", ".join(OLD_COLUMNS)
    return f"""
CREATE TABLE IF NOT EXISTS {s}.price_series (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    global_security_id uuid NOT NULL {fk_sec},
    price_type text NOT NULL DEFAULT 'close',
    provider_key text,
    org_id uuid {fk_org},
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sec_global_price_type_chk CHECK (price_type = ANY (ARRAY['close'::text, 'intraday_indicative'::text,
                                                                          'model'::text])),
    CONSTRAINT price_series_key_uq UNIQUE NULLS NOT DISTINCT (global_security_id, price_type, provider_key, org_id)
);
COMMENT ON TABLE {s}.price_series IS 'PRICES3: one row per (security, price_type, provider_key, org_id; NULL org = platform). Carries the old price table''s RLS; lean / history rows are visible through their series.';

CREATE TABLE IF NOT EXISTS {s}.price_provenance (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    provider_key text,
    source text,
    currency_code text,
    licence jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    upload_id uuid,
    org_id uuid {fk_org},
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sec_global_price_upload_chk CHECK (upload_id IS NULL OR (org_id IS NOT NULL AND
                                                                         provider_key = 'csv_upload'::text)),
    CONSTRAINT price_provenance_uq UNIQUE NULLS NOT DISTINCT (provider_key, source, currency_code, licence, upload_id,
                                                              org_id)
);
COMMENT ON TABLE {s}.price_provenance IS 'PRICES3: provenance dictionary (provider, source, currency, licence, upload, org) — stored once, referenced by every lean / history row. Insert-only.';

CREATE TABLE IF NOT EXISTS {s}.price_quality_flags (
    bit smallint PRIMARY KEY CHECK (bit BETWEEN 0 AND 14),
    name text NOT NULL UNIQUE
);
INSERT INTO {s}.price_quality_flags (bit, name) VALUES (0, 'outlier') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS {s}.security_prices (
    as_of timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    series_id integer NOT NULL REFERENCES {s}.price_series(id),
    price_date date NOT NULL,
    provenance_id integer NOT NULL REFERENCES {s}.price_provenance(id),
    quality_bits smallint NOT NULL DEFAULT 0,
    price numeric NOT NULL,
    adjustment_factor numeric,
    CONSTRAINT security_prices_pkey PRIMARY KEY (series_id, price_date),
    CONSTRAINT sec_global_price_positive_chk CHECK (price > (0)::numeric),
    CONSTRAINT sec_global_price_adj_factor_chk CHECK (adjustment_factor IS NULL OR adjustment_factor > (0)::numeric)
);
COMMENT ON TABLE {s}.security_prices IS 'PRICES3: THE price of record — one lean row per (series, date), always the current value. recorded_at = the old system_from = valid_from. A superseded value lives in security_price_history.';
CREATE INDEX IF NOT EXISTS security_prices_date_ix ON {s}.security_prices (price_date);
CREATE INDEX IF NOT EXISTS security_prices_recorded_brin ON {s}.security_prices USING brin (recorded_at);

CREATE TABLE IF NOT EXISTS {s}.security_price_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    series_id integer NOT NULL REFERENCES {s}.price_series(id),
    price_date date NOT NULL,
    price numeric NOT NULL,
    adjustment_factor numeric,
    provenance_id integer NOT NULL REFERENCES {s}.price_provenance(id),
    quality_bits smallint NOT NULL DEFAULT 0,
    as_of timestamptz,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    system_from timestamptz NOT NULL,
    system_to timestamptz,
    run_id uuid,
    superseded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT security_price_history_closed_chk CHECK (valid_to IS NOT NULL OR system_to IS NOT NULL),
    CONSTRAINT security_price_history_positive_chk CHECK (price > (0)::numeric)
);
COMMENT ON TABLE {s}.security_price_history IS 'PRICES3: every superseded price value with its valid / system times exactly as they were (system axis = a receptor restatement, valid axis = an org upload restatement). Never live.';
CREATE INDEX IF NOT EXISTS security_price_history_key_ix ON {s}.security_price_history (series_id, price_date);
CREATE INDEX IF NOT EXISTS security_price_history_superseded_ix ON {s}.security_price_history (superseded_at);

CREATE TABLE IF NOT EXISTS {s}.price_bars_pending (
    series_id integer NOT NULL REFERENCES {s}.price_series(id),
    price_date date NOT NULL,
    recorded_at timestamptz NOT NULL,
    bar jsonb NOT NULL,
    CONSTRAINT price_bars_pending_pkey PRIMARY KEY (series_id, price_date, recorded_at)
);
COMMENT ON TABLE {s}.price_bars_pending IS 'PRICES3: OHLCV bars since the last archive append. The bar lives in the R2 archive only; the archive step drains this table.';

CREATE TABLE IF NOT EXISTS {s}.price_archive_state (
    prefix text PRIMARY KEY,
    watermark timestamptz,
    compacted_month text,
    last_run_at timestamptz,
    last_result jsonb NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE TABLE IF NOT EXISTS {s}.prices3_migration (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    store_mode text NOT NULL DEFAULT 'legacy' CHECK (store_mode IN ('legacy', 'lean')),
    phase text NOT NULL DEFAULT 'not_started' CHECK (phase IN ('not_started', 'copying', 'copied', 'verified',
                                                               'swapped', 'rolled_back', 'dropped')),
    checkpoint_gsid uuid,
    copied_rows bigint NOT NULL DEFAULT 0,
    copied_securities integer NOT NULL DEFAULT 0,
    batches integer NOT NULL DEFAULT 0,
    copy_started_at timestamptz,
    copy_finished_at timestamptz,
    verified_ok boolean,
    verified_at timestamptz,
    verify_detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    swapped_at timestamptz,
    rolled_back_at timestamptz,
    archive_ok boolean,
    archive_at timestamptz,
    archive_detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    dropped_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE {s}.prices3_migration IS 'PRICES3: ONE row. store_mode is THE writers'' switch (legacy = old table, lean = lean table + history), flipped only by scripts/prices3_migrate.py inside the swap transaction. The rest is the operator migration''s checkpoint and verification record.';
INSERT INTO {s}.prices3_migration (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS {s}.prices3_dirty (
    global_security_id uuid PRIMARY KEY,
    touched_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE {s}.prices3_dirty ADD COLUMN IF NOT EXISTS resynced_at timestamptz;
COMMENT ON TABLE {s}.prices3_dirty IS 'PRICES3: securities the OLD table saw a write for while the operator copy ran (trigger installed by scripts/prices3_migrate.py). Kept, never cleared, until the swap: the swap re-verifies every one of them under the write lock.';

-- ── functions ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION {s}.prices3_row_id(p_series integer, p_date date, p_recorded timestamptz)
RETURNS uuid LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$f$ SELECT md5('prices3:' || p_series || ':' || p_date || ':' || (extract(epoch FROM p_recorded) * 1000000)::bigint)::uuid $f$;

CREATE OR REPLACE FUNCTION {s}.price_flag_names(p_bits smallint)
RETURNS text[] LANGUAGE sql STABLE PARALLEL SAFE AS
$f$ SELECT CASE WHEN p_bits = 0 THEN '{{}}'::text[]
               ELSE ARRAY(SELECT f.name FROM {s}.price_quality_flags f WHERE (p_bits::int & (1 << f.bit)) <> 0
                          ORDER BY f.bit) END $f$;

CREATE OR REPLACE FUNCTION {s}.price_flag_bits(p_names text[])
RETURNS smallint LANGUAGE plpgsql AS
$f$
DECLARE n text; b smallint; acc integer := 0;
BEGIN
    IF p_names IS NULL OR cardinality(p_names) = 0 THEN RETURN 0; END IF;
    FOREACH n IN ARRAY p_names LOOP
        SELECT bit INTO b FROM {s}.price_quality_flags WHERE name = n;
        IF b IS NULL THEN
            INSERT INTO {s}.price_quality_flags (bit, name)
            SELECT COALESCE(max(bit) + 1, 0), n FROM {s}.price_quality_flags
            ON CONFLICT (name) DO NOTHING;
            SELECT bit INTO b FROM {s}.price_quality_flags WHERE name = n;
            IF b IS NULL THEN RAISE EXCEPTION 'prices3: quality flag % could not be registered', n; END IF;
        END IF;
        acc := acc | (1 << b);
    END LOOP;
    RETURN acc::smallint;
END $f$;

CREATE OR REPLACE FUNCTION {s}.price_series_id(p_gsid uuid, p_type text, p_provider text, p_org uuid)
RETURNS integer LANGUAGE plpgsql AS
$f$
DECLARE r integer;
BEGIN
    SELECT id INTO r FROM {s}.price_series WHERE global_security_id = p_gsid AND price_type = p_type
       AND provider_key IS NOT DISTINCT FROM p_provider AND org_id IS NOT DISTINCT FROM p_org;
    IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO {s}.price_series (global_security_id, price_type, provider_key, org_id)
    VALUES (p_gsid, p_type, p_provider, p_org) ON CONFLICT ON CONSTRAINT price_series_key_uq DO NOTHING
    RETURNING id INTO r;
    IF r IS NULL THEN
        SELECT id INTO r FROM {s}.price_series WHERE global_security_id = p_gsid AND price_type = p_type
           AND provider_key IS NOT DISTINCT FROM p_provider AND org_id IS NOT DISTINCT FROM p_org;
    END IF;
    RETURN r;
END $f$;

CREATE OR REPLACE FUNCTION {s}.price_provenance_id(p_provider text, p_source text, p_currency text, p_licence jsonb,
                                                   p_upload uuid, p_org uuid)
RETURNS integer LANGUAGE plpgsql AS
$f$
DECLARE r integer; lic jsonb := COALESCE(p_licence, '{{}}'::jsonb);
BEGIN
    SELECT id INTO r FROM {s}.price_provenance WHERE provider_key IS NOT DISTINCT FROM p_provider
       AND source IS NOT DISTINCT FROM p_source AND currency_code IS NOT DISTINCT FROM p_currency AND licence = lic
       AND upload_id IS NOT DISTINCT FROM p_upload AND org_id IS NOT DISTINCT FROM p_org;
    IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO {s}.price_provenance (provider_key, source, currency_code, licence, upload_id, org_id)
    VALUES (p_provider, p_source, p_currency, lic, p_upload, p_org)
    ON CONFLICT ON CONSTRAINT price_provenance_uq DO NOTHING RETURNING id INTO r;
    IF r IS NULL THEN
        SELECT id INTO r FROM {s}.price_provenance WHERE provider_key IS NOT DISTINCT FROM p_provider
           AND source IS NOT DISTINCT FROM p_source AND currency_code IS NOT DISTINCT FROM p_currency AND licence = lic
           AND upload_id IS NOT DISTINCT FROM p_upload AND org_id IS NOT DISTINCT FROM p_org;
    END IF;
    RETURN r;
END $f$;

-- ── the compatibility view: the old 19 columns, from lean ∪ history ─────────
CREATE OR REPLACE VIEW {s}.{VIEW_V3} WITH (security_invoker = true) AS
SELECT {s}.prices3_row_id(l.series_id, l.price_date, l.recorded_at) AS id, se.global_security_id, l.price_date,
       l.price, pv.currency_code, se.price_type, pv.source, l.recorded_at AS valid_from, NULL::timestamptz AS valid_to,
       l.recorded_at AS system_from, NULL::timestamptz AS system_to, se.provider_key, l.as_of,
       {s}.price_flag_names(l.quality_bits) AS quality_flags, pv.licence, se.org_id, pv.upload_id,
       l.adjustment_factor, '{{}}'::jsonb AS bar
  FROM {s}.security_prices l
  JOIN {s}.price_series se ON se.id = l.series_id
  JOIN {s}.price_provenance pv ON pv.id = l.provenance_id
UNION ALL
SELECT h.id, se.global_security_id, h.price_date, h.price, pv.currency_code, se.price_type, pv.source, h.valid_from,
       h.valid_to, h.system_from, h.system_to, se.provider_key, h.as_of, {s}.price_flag_names(h.quality_bits),
       pv.licence, se.org_id, pv.upload_id, h.adjustment_factor, '{{}}'::jsonb
  FROM {s}.security_price_history h
  JOIN {s}.price_series se ON se.id = h.series_id
  JOIN {s}.price_provenance pv ON pv.id = h.provenance_id;
COMMENT ON VIEW {s}.{VIEW_V3} IS 'PRICES3 compatibility view (security_invoker): the old securities_global_prices columns from security_prices (live) UNION ALL security_price_history (superseded). Renamed to securities_global_prices by scripts/prices3_migrate.py at the swap. bar is always {{}} — OHLCV lives in the R2 archive.';
ALTER VIEW {s}.{VIEW_V3} ALTER COLUMN price_type SET DEFAULT 'close';
ALTER VIEW {s}.{VIEW_V3} ALTER COLUMN valid_from SET DEFAULT now();
ALTER VIEW {s}.{VIEW_V3} ALTER COLUMN system_from SET DEFAULT now();
ALTER VIEW {s}.{VIEW_V3} ALTER COLUMN quality_flags SET DEFAULT '{{}}'::text[];
ALTER VIEW {s}.{VIEW_V3} ALTER COLUMN licence SET DEFAULT '{{}}'::jsonb;
ALTER VIEW {s}.{VIEW_V3} ALTER COLUMN bar SET DEFAULT '{{}}'::jsonb;

-- old-shaped writes through the view (verify fixtures, legacy-mode writers after the swap)
CREATE OR REPLACE FUNCTION {s}.prices3_view_write()
RETURNS trigger LANGUAGE plpgsql AS
$f$
DECLARE
    v_series integer; v_old_series integer; v_prov integer; v_bits smallint; v_sf timestamptz; v_id uuid;
    was_live boolean; now_live boolean;
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        v_series := {s}.price_series_id(NEW.global_security_id, COALESCE(NEW.price_type, 'close'), NEW.provider_key,
                                        NEW.org_id);
        v_prov := {s}.price_provenance_id(NEW.provider_key, NEW.source, NEW.currency_code, NEW.licence, NEW.upload_id,
                                          NEW.org_id);
        v_bits := {s}.price_flag_bits(NEW.quality_flags);
        v_sf := COALESCE(NEW.system_from, now());
        now_live := NEW.valid_to IS NULL AND NEW.system_to IS NULL;
        IF now_live AND COALESCE(NEW.valid_from, v_sf) <> v_sf THEN
            RAISE EXCEPTION 'prices3: a live price row keeps valid_from = system_from (got % / %)', NEW.valid_from, v_sf;
        END IF;
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT id INTO v_old_series FROM {s}.price_series WHERE global_security_id = OLD.global_security_id
           AND price_type = OLD.price_type AND provider_key IS NOT DISTINCT FROM OLD.provider_key
           AND org_id IS NOT DISTINCT FROM OLD.org_id;
        was_live := OLD.valid_to IS NULL AND OLD.system_to IS NULL;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF was_live THEN
            DELETE FROM {s}.security_prices WHERE series_id = v_old_series AND price_date = OLD.price_date
               AND recorded_at = OLD.system_from;
            DELETE FROM {s}.price_bars_pending WHERE series_id = v_old_series AND price_date = OLD.price_date
               AND recorded_at = OLD.system_from;
        ELSE
            DELETE FROM {s}.security_price_history WHERE id = OLD.id;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF now_live THEN
            INSERT INTO {s}.security_prices (as_of, recorded_at, series_id, price_date, provenance_id, quality_bits,
                                             price, adjustment_factor)
            VALUES (NEW.as_of, v_sf, v_series, NEW.price_date, v_prov, v_bits, NEW.price, NEW.adjustment_factor);
            NEW.id := {s}.prices3_row_id(v_series, NEW.price_date, v_sf);
            IF NEW.bar IS NOT NULL AND NEW.bar <> '{{}}'::jsonb THEN
                INSERT INTO {s}.price_bars_pending (series_id, price_date, recorded_at, bar)
                VALUES (v_series, NEW.price_date, v_sf, NEW.bar) ON CONFLICT DO NOTHING;
            END IF;
        ELSE
            INSERT INTO {s}.security_price_history (id, series_id, price_date, price, adjustment_factor, provenance_id,
                                                    quality_bits, as_of, valid_from, valid_to, system_from, system_to)
            VALUES (COALESCE(NEW.id, gen_random_uuid()), v_series, NEW.price_date, NEW.price, NEW.adjustment_factor,
                    v_prov, v_bits, NEW.as_of, COALESCE(NEW.valid_from, v_sf), NEW.valid_to, v_sf, NEW.system_to)
            RETURNING id INTO v_id;
            NEW.id := v_id;
        END IF;
    ELSE  -- UPDATE
        IF was_live AND now_live THEN
            UPDATE {s}.security_prices SET series_id = v_series, price_date = NEW.price_date, price = NEW.price,
                   adjustment_factor = NEW.adjustment_factor, as_of = NEW.as_of, provenance_id = v_prov,
                   quality_bits = v_bits, recorded_at = v_sf
             WHERE series_id = v_old_series AND price_date = OLD.price_date AND recorded_at = OLD.system_from;
            NEW.id := {s}.prices3_row_id(v_series, NEW.price_date, v_sf);
        ELSIF was_live THEN  -- the live row is closed: it becomes history, keeping its id
            INSERT INTO {s}.security_price_history (id, series_id, price_date, price, adjustment_factor, provenance_id,
                                                    quality_bits, as_of, valid_from, valid_to, system_from, system_to)
            VALUES (OLD.id, v_series, NEW.price_date, NEW.price, NEW.adjustment_factor, v_prov, v_bits, NEW.as_of,
                    COALESCE(NEW.valid_from, v_sf), NEW.valid_to, v_sf, NEW.system_to);
            DELETE FROM {s}.security_prices WHERE series_id = v_old_series AND price_date = OLD.price_date
               AND recorded_at = OLD.system_from;
            NEW.id := OLD.id;
        ELSIF now_live THEN  -- a closed row re-opened
            DELETE FROM {s}.security_price_history WHERE id = OLD.id;
            INSERT INTO {s}.security_prices (as_of, recorded_at, series_id, price_date, provenance_id, quality_bits,
                                             price, adjustment_factor)
            VALUES (NEW.as_of, v_sf, v_series, NEW.price_date, v_prov, v_bits, NEW.price, NEW.adjustment_factor);
            NEW.id := {s}.prices3_row_id(v_series, NEW.price_date, v_sf);
        ELSE
            UPDATE {s}.security_price_history SET series_id = v_series, price_date = NEW.price_date, price = NEW.price,
                   adjustment_factor = NEW.adjustment_factor, provenance_id = v_prov, quality_bits = v_bits,
                   as_of = NEW.as_of, valid_from = COALESCE(NEW.valid_from, v_sf), valid_to = NEW.valid_to,
                   system_from = v_sf, system_to = NEW.system_to
             WHERE id = OLD.id;
            NEW.id := OLD.id;
        END IF;
    END IF;
    NEW.price_type := COALESCE(NEW.price_type, 'close');
    NEW.system_from := v_sf;
    NEW.valid_from := COALESCE(NEW.valid_from, v_sf);
    NEW.quality_flags := {s}.price_flag_names(v_bits);
    NEW.licence := COALESCE(NEW.licence, '{{}}'::jsonb);
    NEW.bar := '{{}}'::jsonb;
    RETURN NEW;
END $f$;

DROP TRIGGER IF EXISTS prices3_view_write ON {s}.{VIEW_V3};
CREATE TRIGGER prices3_view_write INSTEAD OF INSERT OR UPDATE OR DELETE ON {s}.{VIEW_V3}
    FOR EACH ROW EXECUTE FUNCTION {s}.prices3_view_write();

-- "what was the price as known at time T" — over the compatibility view (old rows and new rows alike)
CREATE OR REPLACE FUNCTION {s}.price_as_known(p_at timestamptz, p_gsids uuid[] DEFAULT NULL,
                                              p_price_type text DEFAULT 'close')
RETURNS SETOF {s}.{VIEW_V3} LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT v.* FROM {s}.{VIEW_V3} v
     WHERE (p_gsids IS NULL OR v.global_security_id = ANY (p_gsids)) AND v.price_type = p_price_type
       AND v.system_from <= p_at AND (v.system_to IS NULL OR v.system_to > p_at)
       AND v.valid_from <= p_at AND (v.valid_to IS NULL OR v.valid_to > p_at);
END;

-- the operator copy's change capture on the OLD table (installed on it by scripts/prices3_migrate.py only)
CREATE OR REPLACE FUNCTION {s}.prices3_mark_dirty()
RETURNS trigger LANGUAGE plpgsql AS
$f$
BEGIN
    INSERT INTO {s}.prices3_dirty (global_security_id, touched_at)
    VALUES (CASE WHEN TG_OP = 'DELETE' THEN OLD.global_security_id ELSE NEW.global_security_id END, clock_timestamp())
    ON CONFLICT (global_security_id) DO UPDATE SET touched_at = clock_timestamp();
    IF TG_OP = 'UPDATE' AND OLD.global_security_id <> NEW.global_security_id THEN
        INSERT INTO {s}.prices3_dirty (global_security_id, touched_at) VALUES (OLD.global_security_id, clock_timestamp())
        ON CONFLICT (global_security_id) DO UPDATE SET touched_at = clock_timestamp();
    END IF;
    RETURN NULL;
END $f$;

{_org_policies(s, "price_series")}
{_org_policies(s, "price_provenance")}
{_series_policies(s, "security_prices")}
{_series_policies(s, "security_price_history")}
{_series_policies(s, "price_bars_pending")}
{_platform_policies(s, "price_quality_flags")}
{_platform_policies(s, "price_archive_state")}
{_platform_policies(s, "prices3_migration")}
{_platform_policies(s, "prices3_dirty")}

GRANT SELECT, INSERT, UPDATE, DELETE ON {s}.price_series, {s}.price_provenance, {s}.price_quality_flags,
    {s}.security_prices, {s}.security_price_history, {s}.price_bars_pending, {s}.price_archive_state,
    {s}.prices3_migration, {s}.prices3_dirty, {s}.{VIEW_V3} TO app_service;
GRANT SELECT ON {s}.price_series, {s}.price_provenance, {s}.price_quality_flags, {s}.security_prices,
    {s}.security_price_history, {s}.prices3_migration, {s}.{VIEW_V3} TO cpsim_app;
GRANT USAGE ON SEQUENCE {s}.price_series_id_seq, {s}.price_provenance_id_seq TO app_service;
-- select list kept in step with OLD_COLUMNS: {cols_sel}
"""


# ── the switch ──────────────────────────────────────────────────────────────
async def store_mode(conn) -> str:
    """'legacy' | 'lean' — read at run time on every write call. No migration table ⇒ legacy."""
    if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t("prices3_migration")):
        return "legacy"
    m = await conn.fetchval(f"SELECT store_mode FROM {t('prices3_migration')} WHERE id = 1")
    return m if m in MODES else "legacy"


# ── lean writers (the same semantics as writes.py's legacy writers) ─────────
async def _series(conn, gsid, price_type: str, provider_key, org_id) -> int:
    return int(await conn.fetchval(f"SELECT {t('price_series_id')}($1::uuid, $2, $3, $4::uuid)", str(gsid), price_type,
                                   provider_key, org_id))


async def _provenance(conn, provider_key, source, currency, licence_json: str, upload_id, org_id) -> int:
    return int(await conn.fetchval(f"SELECT {t('price_provenance_id')}($1, $2, $3, $4::jsonb, $5::uuid, $6::uuid)",
                                   provider_key, source, currency, licence_json, upload_id, org_id))


async def _bits(conn, flags) -> int:
    if not flags:
        return 0
    return int(await conn.fetchval(f"SELECT {t('price_flag_bits')}($1::text[])", list(flags)))


async def lean_upsert_eod_bars(conn, gsid: str, bars, *, provider_key: str, licence: dict, currency: str | None,
                               flags: dict | None = None, as_of=None, run_id=None) -> dict:
    """writes.upsert_eod_bars on the lean table: platform rows for ONE security from ONE provider, inside the caller's
    platform transaction. An equal close writes nothing; a CHANGED close is a SYSTEM-axis restatement — the current
    value goes to security_price_history with system_to = now() and the lean row takes the new value; the bar goes
    to price_bars_pending for the archive."""
    out = {"inserted": 0, "restated": 0, "unchanged": 0}
    if not bars:
        return out
    sid = await _series(conn, gsid, "close", provider_key, None)
    days = [b.day for b in bars]
    cur = {r["price_date"]: r for r in await conn.fetch(
        f"SELECT price_date, price, recorded_at, as_of, provenance_id, quality_bits, adjustment_factor FROM "
        f"{t('security_prices')} WHERE series_id = $1 AND price_date >= $2 AND price_date <= $3 FOR UPDATE",
        sid, min(days), max(days))}
    stamp = as_of or datetime.now(timezone.utc)
    lic = json.dumps(licence or {}, sort_keys=True)
    prov_cache: dict = {}
    hist, upd, ins, bar_rows = [], [], [], []
    for b in bars:
        c = cur.get(b.day)
        if c is not None and Decimal(str(c["price"])) == b.close:
            out["unchanged"] += 1
            continue
        ccy = b.currency or currency
        if ccy not in prov_cache:
            prov_cache[ccy] = await _provenance(conn, provider_key, provider_key, ccy, lic, None, None)
        bits = await _bits(conn, (flags or {}).get(b.day) or [])
        row = (stamp, sid, b.day, prov_cache[ccy], bits, b.close, b.adjustment_factor)
        if c is not None:
            out["restated"] += 1
            hist.append((sid, b.day, c["price"], c["adjustment_factor"], c["provenance_id"], c["quality_bits"],
                         c["as_of"], c["recorded_at"], run_id))
            upd.append(row)
        else:
            out["inserted"] += 1
            ins.append(row)
        bar_rows.append((sid, b.day, json.dumps(b.bar_json())))
    if hist:
        await conn.executemany(
            f"INSERT INTO {t('security_price_history')} (id, series_id, price_date, price, adjustment_factor, "
            f"provenance_id, quality_bits, as_of, valid_from, valid_to, system_from, system_to, run_id) VALUES "
            f"({t('prices3_row_id')}($1, $2, $8), $1, $2, $3, $4, $5, $6, $7, $8, NULL, $8, now(), $9::uuid)", hist)
        await conn.executemany(
            f"UPDATE {t('security_prices')} SET as_of = $1, recorded_at = now(), provenance_id = $4, quality_bits = $5, "
            f"price = $6, adjustment_factor = $7 WHERE series_id = $2 AND price_date = $3", upd)
    if ins:
        await conn.executemany(
            f"INSERT INTO {t('security_prices')} (as_of, recorded_at, series_id, price_date, provenance_id, quality_bits, "
            f"price, adjustment_factor) VALUES ($1, now(), $2, $3, $4, $5, $6, $7)", ins)
    if bar_rows:
        await conn.executemany(
            f"INSERT INTO {t('price_bars_pending')} (series_id, price_date, recorded_at, bar) VALUES ($1, $2, now(), "
            f"$3::jsonb) ON CONFLICT (series_id, price_date, recorded_at) DO UPDATE SET bar = EXCLUDED.bar", bar_rows)
    return out


async def lean_restate_point(conn, *, gsid: str, price_type: str, provider_key, org_id, day: date, value: Decimal,
                             currency, source, upload_id, licence_json: str, as_of, run_id=None) -> str:
    """writes._restate on the lean table (VALID axis — org uploads): 'inserted' | 'revised' | 'unchanged'."""
    sid = await _series(conn, gsid, price_type, provider_key, org_id)
    cur = await conn.fetchrow(
        f"SELECT price, recorded_at, as_of, provenance_id, quality_bits, adjustment_factor FROM {t('security_prices')} "
        f"WHERE series_id = $1 AND price_date = $2 FOR UPDATE", sid, day)
    if cur is not None and Decimal(str(cur["price"])) == value:
        return "unchanged"
    prov = await _provenance(conn, provider_key, source, currency, licence_json, upload_id, org_id)
    if cur is not None:
        await conn.execute(
            f"INSERT INTO {t('security_price_history')} (id, series_id, price_date, price, adjustment_factor, "
            f"provenance_id, quality_bits, as_of, valid_from, valid_to, system_from, system_to, run_id) VALUES "
            f"({t('prices3_row_id')}($1, $2, $7), $1, $2, $3, $4, $5, $6, $8, $7, now(), $7, NULL, $9::uuid)",
            sid, day, cur["price"], cur["adjustment_factor"], cur["provenance_id"], cur["quality_bits"],
            cur["recorded_at"], cur["as_of"], run_id)
        await conn.execute(
            f"UPDATE {t('security_prices')} SET as_of = $3, recorded_at = now(), provenance_id = $4, quality_bits = 0, "
            f"price = $5, adjustment_factor = NULL WHERE series_id = $1 AND price_date = $2",
            sid, day, as_of, prov, value)
        return "revised"
    await conn.execute(
        f"INSERT INTO {t('security_prices')} (as_of, recorded_at, series_id, price_date, provenance_id, quality_bits, "
        f"price, adjustment_factor) VALUES ($1, now(), $2, $3, $4, 0, $5, NULL)", as_of, sid, day, prov, value)
    return "inserted"


async def lean_add_price(conn, *, gsid: str, price_date: date, amount, currency_code, price_type: str,
                         source) -> str:
    """securities_global.add_price on the lean table (provider platform_entry, as_of now()). Returns the row id the
    compatibility view shows for it. A second live point for the same key raises UniqueViolation, as before."""
    sid = await _series(conn, gsid, price_type, "platform_entry", None)
    prov = await _provenance(conn, "platform_entry", source, currency_code, "{}", None, None)
    return str(await conn.fetchval(
        f"INSERT INTO {t('security_prices')} (as_of, recorded_at, series_id, price_date, provenance_id, quality_bits, "
        f"price, adjustment_factor) VALUES (now(), now(), $1, $2, $3, 0, $4, NULL) "
        f"RETURNING {t('prices3_row_id')}(series_id, price_date, recorded_at)", sid, price_date, prov, amount))
