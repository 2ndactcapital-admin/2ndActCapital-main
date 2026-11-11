-- cpsim01 part 1 — the COUNTERPARTY SIMULATOR's own schema, role and registry.
-- Design: docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md. Notes: docs/cpsim/CPSIM01_NOTES.md.
--
-- 1. Schema `cpsim` — the simulator's own book. Nothing here has org_id: a simulated counterparty is a
--    PLATFORM object (Super Admin), never a tenant row. No table in `public` is created by this file.
-- 2. Role `cpsim_app` — NOLOGIN, NOINHERIT. Granted to the app's login role `app_service` WITH INHERIT FALSE,
--    SET TRUE (PostgreSQL 16+): app_service holds NONE of cpsim_app's privileges until a transaction says
--    `SET LOCAL ROLE cpsim_app`. No new login, no new secret.
--    Privileges (the complete, documented set — verify_cpsim01 reads them back from the catalog):
--      USAGE on schema cpsim; SELECT, INSERT, UPDATE, DELETE on every cpsim table; EXECUTE on the cpsim functions;
--      USAGE on schema portfolio; SELECT on portfolio.securities_global, portfolio.securities_global_identifiers,
--      portfolio.securities_global_prices (the security master and prices — read-only reference data).
--      NOTHING else: no privilege on any public table (audit_log is written by the admin router as app_service,
--      after RESET ROLE, in the same transaction).
-- 3. Registry: institution_types (seeded 4), counterparties (is_simulated CHECK, SYSTEM-axis bi-temporal: the id
--    is referenced by every child table, so an edit archives a copy with archived_from_id and keeps the id),
--    credentials (salted hash + last four, prefix CHECK cpsim_), FIX sessions, bank details (FAKE by construction
--    — CHECKs + a trigger refuse anything that could be real), behaviour, clock (VALID-axis restatement: one live
--    row per counterparty, history kept), request_log (no bodies, no keys).
-- 4. cpsim.may_connect(org, household) — the principle-3 rule; reads public.org_settings
--    `counterparty_sim.allowed` (default false = no row), organizations.is_test and
--    public.crm_is_demo_household (CRMDETAIL1's demo rule, reused).
-- 5. public.org_settings guard: `counterparty_sim.allowed` may be written only with app.is_super_admin = 'true'
--    (the Super Admin admin router); the generic settings path refuses it in code too.
--
-- Nothing is removed by this file (re-runnable through CREATE OR REPLACE / IF NOT EXISTS).

BEGIN;

CREATE SCHEMA IF NOT EXISTS cpsim;
REVOKE ALL ON SCHEMA cpsim FROM PUBLIC;
COMMENT ON SCHEMA cpsim IS 'cpsim01 — the Counterparty Simulator''s own book (SIMULATED data only). Reached only '
  'through role cpsim_app (SET LOCAL ROLE) and the Super Admin admin router.';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cpsim_app') THEN
    CREATE ROLE cpsim_app NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;
COMMENT ON ROLE cpsim_app IS 'cpsim01 — the Counterparty Simulator. NOLOGIN; entered with SET LOCAL ROLE.';

GRANT cpsim_app TO app_service WITH INHERIT FALSE, SET TRUE;

-- ── fake-value checks (pure) ─────────────────────────────────────────────────────────────────────────────────
-- The real ABA check-digit rule: 3·(d1+d4+d7) + 7·(d2+d5+d8) + (d3+d6+d9) ≡ 0 (mod 10).
CREATE OR REPLACE FUNCTION cpsim.aba_checksum_ok(r text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
  SELECT CASE WHEN r ~ '^[0-9]{9}$' THEN
    (3 * (substr(r, 1, 1)::int + substr(r, 4, 1)::int + substr(r, 7, 1)::int)
   + 7 * (substr(r, 2, 1)::int + substr(r, 5, 1)::int + substr(r, 8, 1)::int)
   +     (substr(r, 3, 1)::int + substr(r, 6, 1)::int + substr(r, 9, 1)::int)) % 10 = 0
  ELSE false END
$$;

-- The real IBAN rule (ISO 13616): move the first four characters to the end, letters → 10..35, mod 97 = 1.
CREATE OR REPLACE FUNCTION cpsim.iban_ok(iban text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog AS $$
DECLARE
  s text;
  ch text;
  d text;
  rem int := 0;
BEGIN
  IF iban IS NULL OR iban !~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$' THEN
    RETURN false;
  END IF;
  s := substr(iban, 5) || substr(iban, 1, 4);
  FOR i IN 1..length(s) LOOP
    ch := substr(s, i, 1);
    IF ch ~ '[A-Z]' THEN d := (ascii(ch) - 55)::text; ELSE d := ch; END IF;
    FOR j IN 1..length(d) LOOP
      rem := (rem * 10 + substr(d, j, 1)::int) % 97;
    END LOOP;
  END LOOP;
  RETURN rem = 1;
END $$;

-- ── institution types ────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.institution_types (
  key              text PRIMARY KEY,
  label            text NOT NULL,
  protocol         text NOT NULL,
  field_groups     text[] NOT NULL,
  envelope_schema  text NOT NULL DEFAULT 'generic_envelope_v1',
  description      text NOT NULL,
  sort_order       integer NOT NULL DEFAULT 100,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT institution_types_key_chk CHECK (key ~ '^[a-z][a-z0-9_]{1,40}$'),
  CONSTRAINT institution_types_label_chk CHECK (length(btrim(label)) BETWEEN 1 AND 120),
  CONSTRAINT institution_types_protocol_chk CHECK (protocol IN ('rest_oauth', 'fix', 'bank_wire_ach', 'bank_swift')),
  CONSTRAINT institution_types_groups_chk CHECK (
    field_groups <@ ARRAY['connection', 'fix', 'bank_domestic', 'bank_international', 'behaviour', 'clock']::text[]
    AND 'connection' = ANY (field_groups)),
  CONSTRAINT institution_types_schema_chk CHECK (envelope_schema ~ '^[a-z][a-z0-9_]{1,60}$')
);

INSERT INTO cpsim.institution_types (key, label, protocol, field_groups, envelope_schema, description, sort_order) VALUES
  ('altruist', 'Altruist-compatible custodian', 'rest_oauth', ARRAY['connection', 'behaviour', 'clock'],
   'generic_envelope_v1', 'A custodian answering the REST + OAuth paths the existing Altruist-compatible client calls '
   '(households, accounts, positions, transactions; webhooks). API arrives in cpsim02.', 10),
  ('schwab', 'Schwab-compatible custodian', 'fix', ARRAY['connection', 'fix', 'behaviour', 'clock'],
   'generic_envelope_v1', 'A custodian reached over a FIX session (stored only — no FIX engine in v1) and files.', 20),
  ('domestic_bank', 'Domestic bank', 'bank_wire_ach', ARRAY['connection', 'bank_domestic', 'behaviour', 'clock'],
   'generic_envelope_v1', 'A U.S. bank: wire and ACH with fake routing and account numbers.', 30),
  ('international_bank', 'International bank', 'bank_swift',
   ARRAY['connection', 'bank_international', 'behaviour', 'clock'],
   'generic_envelope_v1', 'A non-U.S. bank: SWIFT / BIC, IBAN and an intermediary bank — all fake by construction.', 40)
ON CONFLICT (key) DO NOTHING;

-- ── counterparties (SYSTEM-axis bi-temporal: id kept, archived copies carry archived_from_id) ─────────────
CREATE TABLE IF NOT EXISTS cpsim.counterparties (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              text NOT NULL,
  name              text NOT NULL,
  institution_type  text NOT NULL REFERENCES cpsim.institution_types (key),
  is_simulated      boolean NOT NULL DEFAULT true,
  status            text NOT NULL DEFAULT 'draft',
  destination_path  text NOT NULL,
  notes             text,
  created_by        uuid,
  updated_by        uuid,
  created_at        timestamptz NOT NULL DEFAULT now(),
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  archived_from_id  uuid REFERENCES cpsim.counterparties (id),
  CONSTRAINT counterparties_simulated_chk CHECK (is_simulated = true),
  CONSTRAINT counterparties_slug_chk CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  CONSTRAINT counterparties_name_chk CHECK (length(btrim(name)) BETWEEN 1 AND 200),
  CONSTRAINT counterparties_status_chk CHECK (status IN ('draft', 'active', 'disabled')),
  CONSTRAINT counterparties_destination_chk CHECK (destination_path = '/cpsim/c/' || slug),
  CONSTRAINT counterparties_archive_chk CHECK ((system_to IS NULL) = (archived_from_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS counterparties_slug_live_uq ON cpsim.counterparties (slug) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS counterparties_archived_from_idx ON cpsim.counterparties (archived_from_id);

-- ── credentials (the fake key and the FIX password: salted hash + last four; shown once, never stored) ────
CREATE TABLE IF NOT EXISTS cpsim.counterparty_credentials (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  kind             text NOT NULL,
  key_prefix       text NOT NULL,
  key_hash         text NOT NULL,
  salt             text NOT NULL,
  last4            text NOT NULL,
  created_by       uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  rotated_at       timestamptz,
  revoked_at       timestamptz,
  revoked_by       uuid,
  replaced_by      uuid REFERENCES cpsim.counterparty_credentials (id),
  CONSTRAINT counterparty_credentials_kind_chk CHECK (kind IN ('api_key', 'fix_password')),
  CONSTRAINT counterparty_credentials_prefix_chk CHECK (key_prefix ~ '^cpsim_[a-z]+_$'),
  CONSTRAINT counterparty_credentials_hash_chk CHECK (key_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT counterparty_credentials_salt_chk CHECK (salt ~ '^[0-9a-f]{32}$'),
  CONSTRAINT counterparty_credentials_last4_chk CHECK (last4 ~ '^[0-9a-f]{4}$'),
  CONSTRAINT counterparty_credentials_close_chk CHECK (rotated_at IS NULL OR revoked_at IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS counterparty_credentials_live_uq ON cpsim.counterparty_credentials
  (counterparty_id, kind) WHERE rotated_at IS NULL AND revoked_at IS NULL;

-- ── FIX session (stored only — there is no FIX engine) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.counterparty_fix_sessions (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id         uuid NOT NULL REFERENCES cpsim.counterparties (id),
  begin_string            text NOT NULL,
  sender_comp_id          text NOT NULL,
  target_comp_id          text NOT NULL,
  host                    text NOT NULL,
  port                    integer NOT NULL,
  heartbt_int             integer NOT NULL DEFAULT 30,
  reset_on_logon          boolean NOT NULL DEFAULT true,
  username                text,
  password_credential_id  uuid REFERENCES cpsim.counterparty_credentials (id),
  created_by              uuid,
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  CONSTRAINT counterparty_fix_sessions_begin_chk CHECK (begin_string IN ('FIX.4.2', 'FIX.4.4', 'FIXT.1.1')),
  CONSTRAINT counterparty_fix_sessions_comp_chk CHECK (sender_comp_id ~ '^[A-Za-z0-9_.-]{1,64}$'
                                                       AND target_comp_id ~ '^[A-Za-z0-9_.-]{1,64}$'),
  CONSTRAINT counterparty_fix_sessions_host_chk CHECK (host ~ '^[A-Za-z0-9.-]{1,253}$'),
  CONSTRAINT counterparty_fix_sessions_port_chk CHECK (port BETWEEN 1 AND 65535),
  CONSTRAINT counterparty_fix_sessions_hb_chk CHECK (heartbt_int BETWEEN 1 AND 3600),
  CONSTRAINT counterparty_fix_sessions_user_chk CHECK (username IS NULL OR username ~ '^[A-Za-z0-9_.@-]{1,64}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS counterparty_fix_sessions_live_uq ON cpsim.counterparty_fix_sessions
  (counterparty_id) WHERE valid_to IS NULL;

-- ── bank details (FAKE by construction) ───────────────────────────────────────────────────────────────────
-- ABA: 9 digits beginning 99 (the Federal Reserve assigns 00–12, 21–32, 61–72 and 80; 99 is never assigned)
--      AND failing the real check digit — two independent reasons it can never be a real routing number.
-- Account: 'CPSIM' + digits — not a number shape any bank issues.
-- BIC: country code 'XX' (ISO 3166 user-assigned — no country, so no real BIC) and a location code ending '0'
--      (SWIFT's test-and-training marker); a seeded list of REAL example BICs is refused by trigger.
-- IBAN: check digits 00, 01 or 99 — never produced by the ISO 13616 algorithm (valid range 02–98) — and the
--      mod-97 test must FAIL.
CREATE TABLE IF NOT EXISTS cpsim.real_bic_examples (
  bic   text PRIMARY KEY,
  note  text NOT NULL,
  CONSTRAINT real_bic_examples_bic_chk CHECK (bic ~ '^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$')
);
INSERT INTO cpsim.real_bic_examples (bic, note) VALUES
  ('CHASUS33', 'published example'), ('BOFAUS3N', 'published example'), ('CITIUS33', 'published example'),
  ('WFBIUS6S', 'published example'), ('DEUTDEFF', 'published example'), ('HSBCGB2L', 'published example'),
  ('BARCGB22', 'published example'), ('BNPAFRPP', 'published example'), ('UBSWCHZH', 'published example'),
  ('NWBKGB2L', 'published example'), ('COBADEFF', 'published example'), ('SCBLSGSG', 'published example')
ON CONFLICT (bic) DO NOTHING;

CREATE TABLE IF NOT EXISTS cpsim.counterparty_bank_details (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id         uuid NOT NULL REFERENCES cpsim.counterparties (id),
  kind                    text NOT NULL,
  bank_name               text NOT NULL,
  aba_wire                text,
  aba_ach                 text,
  account_number          text,
  bic                     text,
  iban                    text,
  intermediary_bank_name  text,
  intermediary_bic        text,
  intermediary_aba        text,
  beneficiary_name        text,
  beneficiary_address     text,
  created_by              uuid,
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  CONSTRAINT counterparty_bank_details_kind_chk CHECK (kind IN ('domestic', 'international')),
  CONSTRAINT counterparty_bank_details_name_chk CHECK (bank_name LIKE 'CPSIM %'),
  CONSTRAINT counterparty_bank_details_aba_wire_chk CHECK (
    aba_wire IS NULL OR (aba_wire ~ '^99[0-9]{7}$' AND NOT cpsim.aba_checksum_ok(aba_wire))),
  CONSTRAINT counterparty_bank_details_aba_ach_chk CHECK (
    aba_ach IS NULL OR (aba_ach ~ '^99[0-9]{7}$' AND NOT cpsim.aba_checksum_ok(aba_ach))),
  CONSTRAINT counterparty_bank_details_aba_int_chk CHECK (
    intermediary_aba IS NULL OR (intermediary_aba ~ '^99[0-9]{7}$' AND NOT cpsim.aba_checksum_ok(intermediary_aba))),
  CONSTRAINT counterparty_bank_details_account_chk CHECK (account_number IS NULL OR account_number ~ '^CPSIM[0-9]{6,17}$'),
  CONSTRAINT counterparty_bank_details_bic_chk CHECK (bic IS NULL OR bic ~ '^[A-Z]{4}XX[A-Z0-9]0([A-Z0-9]{3})?$'),
  CONSTRAINT counterparty_bank_details_int_bic_chk CHECK (
    intermediary_bic IS NULL OR intermediary_bic ~ '^[A-Z]{4}XX[A-Z0-9]0([A-Z0-9]{3})?$'),
  CONSTRAINT counterparty_bank_details_iban_chk CHECK (
    iban IS NULL OR (iban ~ '^[A-Z]{2}(00|01|99)[A-Z0-9]{11,30}$' AND NOT cpsim.iban_ok(iban))),
  CONSTRAINT counterparty_bank_details_shape_chk CHECK (
    (kind = 'domestic' AND aba_wire IS NOT NULL AND aba_ach IS NOT NULL AND account_number IS NOT NULL)
    OR (kind = 'international' AND bic IS NOT NULL AND iban IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS counterparty_bank_details_live_uq ON cpsim.counterparty_bank_details
  (counterparty_id, kind) WHERE valid_to IS NULL;

CREATE OR REPLACE FUNCTION cpsim.bank_details_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, cpsim AS $$
BEGIN
  IF NEW.bic IS NOT NULL AND EXISTS (SELECT 1 FROM cpsim.real_bic_examples r WHERE left(NEW.bic, 8) = left(r.bic, 8)) THEN
    RAISE EXCEPTION 'cpsim01: % is a real BIC — the simulator holds fake bank details only', NEW.bic
      USING ERRCODE = '23514';
  END IF;
  IF NEW.intermediary_bic IS NOT NULL AND EXISTS (
      SELECT 1 FROM cpsim.real_bic_examples r WHERE left(NEW.intermediary_bic, 8) = left(r.bic, 8)) THEN
    RAISE EXCEPTION 'cpsim01: % is a real BIC — the simulator holds fake bank details only', NEW.intermediary_bic
      USING ERRCODE = '23514';
  END IF;
  IF cpsim.aba_checksum_ok(NEW.aba_wire) OR cpsim.aba_checksum_ok(NEW.aba_ach)
     OR cpsim.aba_checksum_ok(NEW.intermediary_aba) THEN
    RAISE EXCEPTION 'cpsim01: a routing number passing the real ABA check digit is refused — fake values only'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER counterparty_bank_details_guard_trg BEFORE INSERT OR UPDATE ON cpsim.counterparty_bank_details
  FOR EACH ROW EXECUTE FUNCTION cpsim.bank_details_guard();

-- ── behaviour (stored now; drives outcomes from cpsim03 / cpsim04) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.counterparty_behaviour (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id       uuid NOT NULL REFERENCES cpsim.counterparties (id),
  default_outcome       text NOT NULL DEFAULT 'accept',
  reject_reason         text,
  partial_pct           numeric,
  delay_seconds         integer,
  ach_return_code       text,
  ach_return_after_days integer,
  webhooks_enabled      boolean NOT NULL DEFAULT false,
  created_by            uuid,
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  CONSTRAINT counterparty_behaviour_outcome_chk CHECK (default_outcome IN ('accept', 'reject', 'partial', 'delay')),
  CONSTRAINT counterparty_behaviour_reject_chk CHECK (
    default_outcome <> 'reject' OR length(btrim(COALESCE(reject_reason, ''))) > 0),
  CONSTRAINT counterparty_behaviour_partial_chk CHECK (
    (default_outcome <> 'partial' OR partial_pct IS NOT NULL) AND (partial_pct IS NULL OR (partial_pct > 0 AND partial_pct < 100))),
  CONSTRAINT counterparty_behaviour_delay_chk CHECK (
    (default_outcome <> 'delay' OR delay_seconds IS NOT NULL) AND (delay_seconds IS NULL OR delay_seconds BETWEEN 0 AND 86400)),
  CONSTRAINT counterparty_behaviour_ach_chk CHECK (
    (ach_return_code IS NULL) = (ach_return_after_days IS NULL)
    AND (ach_return_code IS NULL OR ach_return_code ~ '^R[0-9]{2}$')
    AND (ach_return_after_days IS NULL OR ach_return_after_days BETWEEN 0 AND 60))
);
CREATE UNIQUE INDEX IF NOT EXISTS counterparty_behaviour_live_uq ON cpsim.counterparty_behaviour (counterparty_id)
  WHERE valid_to IS NULL;

-- ── clock (the counterparty's business date; VALID-axis restatement keeps every move) ─────────────────────
CREATE TABLE IF NOT EXISTS cpsim.counterparty_clock (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  business_date    date NOT NULL,
  reason           text NOT NULL,
  set_by           uuid,
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  CONSTRAINT counterparty_clock_reason_chk CHECK (reason IN ('initial', 'advance', 'set')),
  CONSTRAINT counterparty_clock_weekday_chk CHECK (extract(isodow FROM business_date) < 6)
);
CREATE UNIQUE INDEX IF NOT EXISTS counterparty_clock_live_uq ON cpsim.counterparty_clock (counterparty_id)
  WHERE valid_to IS NULL;

-- ── request log (every simulator request; never a body, never a key) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.request_log (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid REFERENCES cpsim.counterparties (id),
  slug             text,
  method           text NOT NULL,
  path             text NOT NULL,
  status           integer NOT NULL,
  auth_outcome     text NOT NULL,
  shape_valid      boolean,
  error_count      integer NOT NULL DEFAULT 0,
  message_type     text,
  at               timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT request_log_method_chk CHECK (method IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),
  CONSTRAINT request_log_status_chk CHECK (status BETWEEN 100 AND 599),
  CONSTRAINT request_log_auth_chk CHECK (auth_outcome IN ('not_required', 'ok', 'missing', 'malformed',
                                                          'not_simulator_key', 'unknown_key', 'revoked',
                                                          'disabled', 'unknown_counterparty')),
  CONSTRAINT request_log_no_key_chk CHECK (path NOT LIKE '%cpsim\_%' AND COALESCE(message_type, '') NOT LIKE '%cpsim\_%'),
  CONSTRAINT request_log_path_chk CHECK (length(path) <= 300 AND COALESCE(length(message_type), 0) <= 80)
);
CREATE INDEX IF NOT EXISTS request_log_counterparty_at_idx ON cpsim.request_log (counterparty_id, at DESC);
CREATE INDEX IF NOT EXISTS request_log_slug_idx ON cpsim.request_log (slug);

-- ── may_connect (principle 3; cpsim02 calls it) ───────────────────────────────────────────────────────────
-- SECURITY DEFINER (owner postgres): it must read org_settings / organizations / households whatever RLS context
-- the caller carries — the same reason CRMDETAIL1's crm_is_demo_household is a definer. Arguments are uuids only.
CREATE OR REPLACE FUNCTION cpsim.may_connect(p_org uuid, p_household uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
  v_allowed boolean;
  v_is_test boolean;
BEGIN
  IF p_org IS NULL THEN
    RETURN false;
  END IF;
  SELECT COALESCE(bool_or(s.setting_value = 'true'::jsonb), false) INTO v_allowed
    FROM public.org_settings s WHERE s.org_id = p_org AND s.setting_key = 'counterparty_sim.allowed';
  IF NOT v_allowed THEN
    RETURN false;
  END IF;
  SELECT o.is_test INTO v_is_test FROM public.organizations o WHERE o.id = p_org;
  IF v_is_test IS NULL THEN
    RETURN false;
  END IF;
  IF p_household IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM public.households h WHERE h.id = p_household AND h.org_id = p_org) THEN
    RETURN false;            -- another org's household (or none): never
  END IF;
  IF v_is_test THEN
    RETURN true;             -- a test org: freely, once the setting is on
  END IF;
  RETURN p_household IS NOT NULL AND public.crm_is_demo_household(p_household);   -- a real org: demo households only
END $$;

-- ── privileges: exactly the documented set ────────────────────────────────────────────────────────────────
REVOKE ALL ON ALL TABLES IN SCHEMA cpsim FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cpsim FROM PUBLIC;
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'app_service', 'litellm_service'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA cpsim FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cpsim FROM %I', r);
      EXECUTE format('REVOKE ALL ON SCHEMA cpsim FROM %I', r);
    END IF;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA cpsim TO cpsim_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cpsim TO cpsim_app;
GRANT EXECUTE ON FUNCTION cpsim.aba_checksum_ok(text), cpsim.iban_ok(text), cpsim.may_connect(uuid, uuid),
  cpsim.bank_details_guard() TO cpsim_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA cpsim REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA cpsim REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA cpsim GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cpsim_app;

GRANT USAGE ON SCHEMA portfolio TO cpsim_app;
GRANT SELECT ON portfolio.securities_global, portfolio.securities_global_identifiers,
  portfolio.securities_global_prices TO cpsim_app;

-- ── the org setting: Super Admin only (generic settings path refuses it in code; this is the floor) ────────
CREATE OR REPLACE FUNCTION public.cpsim_org_setting_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.setting_key = 'counterparty_sim.allowed'
     OR (TG_OP = 'UPDATE' AND OLD.setting_key = 'counterparty_sim.allowed') THEN
    IF COALESCE(NULLIF(current_setting('app.is_super_admin', true), ''), 'false') <> 'true' THEN
      RAISE EXCEPTION 'cpsim01: counterparty_sim.allowed is set only by a Super Admin (Admin → Counterparties)'
        USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(NEW.setting_value) <> 'boolean' THEN
      RAISE EXCEPTION 'cpsim01: counterparty_sim.allowed must be true or false' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER cpsim_org_setting_guard_trg BEFORE INSERT OR UPDATE ON public.org_settings
  FOR EACH ROW EXECUTE FUNCTION public.cpsim_org_setting_guard();

COMMIT;
