-- cpsim06 part 1 — ACCOUNT OPENING at the simulated custodian (Realtime-API-shaped; every path / field ASSUMED).
-- Notes: docs/cpsim/CPSIM06_NOTES.md (D1–D6). Design: docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md.
--
-- cpsim schema ONLY (platform objects, no org_id; cpsim_app only). NO public DDL: CPSIM06_NOTES §1d finding 7 —
-- Hollisworks writes nothing new (add_account, the crosswalk and audit_log are existing tables).
--
--   realtime_users           simulator users for the password grant; password = salted sha256 only (shown once at
--                            creation). Revoked, never deleted (cpsim_app: SELECT / INSERT / UPDATE).
--   realtime_tokens          access (5 min) / refresh tokens (FIXED 10 h session window); sha256 + last four only.
--   realtime_contacts        VALID axis (Rule 3) per (counterparty, contact_id). Tax id = last four + salted sha256
--                            (bytea) ONLY; no date of birth (age_years at age_as_of). (SELECT / INSERT / UPDATE.)
--   realtime_drafts          VALID axis per (counterparty, draft_id); opening closes the draft row and inserts the
--                            opened row. (SELECT / INSERT / UPDATE.)
--   realtime_beneficiaries   append-only, per draft ROW (a restated draft gets its own rows). (SELECT / INSERT.)
--   realtime_validation_log  append-only: every validate / submit with its problems. (SELECT / INSERT.)
--   book_accounts            + date_open, account_number (an OPENED account's; NULL for uploaded rows → unchanged).
--
-- D3: a CHECK refuses more than 4 digits in every tax-id text column (tax_id_last4), the digest is a 32-byte bytea, and
-- every free-text / jsonb column a request can reach refuses a run of 9 digits (no full SSN / ITIN / EIN can land in
-- one even by mistake; ZIP+4 is stored 12345-6789, phones 555-555-0100).
-- Append-only tables reuse cpsim.fix_append_only_guard() (cpsim05): UPDATE / DELETE refused except the table owner with
-- cpsim.allow_teardown = 'fixture'.
-- Re-runnable: IF NOT EXISTS / OR REPLACE; constraints dropped and re-added by name.

BEGIN;

-- ── book: an opened account carries its opening date and its number ─────────────────────────────────────────
ALTER TABLE cpsim.book_accounts ADD COLUMN IF NOT EXISTS date_open date;
ALTER TABLE cpsim.book_accounts ADD COLUMN IF NOT EXISTS account_number text;
ALTER TABLE cpsim.book_accounts DROP CONSTRAINT IF EXISTS book_accounts_number_chk;
ALTER TABLE cpsim.book_accounts ADD CONSTRAINT book_accounts_number_chk
  CHECK (account_number IS NULL OR account_number ~ '^CPSIM[0-9]{9}$');

-- ── simulator users (password grant) ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.realtime_users (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  username         text NOT NULL,
  password_salt    bytea NOT NULL,
  password_hash    bytea NOT NULL,
  created_by       uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  revoked_at       timestamptz,
  CONSTRAINT realtime_users_username_chk CHECK (username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
  CONSTRAINT realtime_users_salt_chk CHECK (octet_length(password_salt) = 16),
  CONSTRAINT realtime_users_hash_chk CHECK (octet_length(password_hash) = 32)
);
CREATE UNIQUE INDEX IF NOT EXISTS realtime_users_live_uq ON cpsim.realtime_users (counterparty_id, username)
  WHERE revoked_at IS NULL;

-- ── tokens: access 5 min; refresh on a FIXED 10 h window from the session's first issue ─────────────────────
CREATE TABLE IF NOT EXISTS cpsim.realtime_tokens (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id     uuid NOT NULL REFERENCES cpsim.counterparties (id),
  user_id             uuid NOT NULL REFERENCES cpsim.realtime_users (id),
  session_id          uuid NOT NULL,
  kind                text NOT NULL,
  token_hash          text NOT NULL,
  last4               text NOT NULL,
  created_at          timestamptz NOT NULL,
  expires_at          timestamptz NOT NULL,
  session_expires_at  timestamptz NOT NULL,
  revoked_at          timestamptz,
  CONSTRAINT realtime_tokens_kind_chk CHECK (kind IN ('access', 'refresh')),
  CONSTRAINT realtime_tokens_hash_chk CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT realtime_tokens_last4_chk CHECK (last4 ~ '^[0-9a-f]{4}$'),
  CONSTRAINT realtime_tokens_window_chk CHECK (expires_at > created_at AND expires_at <= session_expires_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS realtime_tokens_hash_uq ON cpsim.realtime_tokens (token_hash);
CREATE INDEX IF NOT EXISTS realtime_tokens_session_idx ON cpsim.realtime_tokens (session_id);

-- ── contacts (VALID axis; tax id last four + salted digest only; no date of birth) ──────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.realtime_contacts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id      uuid NOT NULL REFERENCES cpsim.counterparties (id),
  contact_id           text NOT NULL,
  client_reference     text,
  kind                 text NOT NULL,
  legal_name           text,
  email                text,
  phone                text,
  residential_address  jsonb,
  mailing_address      jsonb,
  citizenship          text,
  visa_type            text,
  visa_expiration      date,
  employment           jsonb,
  age_years            integer,
  age_as_of            date,
  tax_id_kind          text,
  tax_id_last4         text,
  tax_id_salt          bytea,
  tax_id_digest        bytea,
  problems             jsonb NOT NULL DEFAULT '[]'::jsonb,
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  CONSTRAINT realtime_contacts_id_chk CHECK (contact_id ~ '^rtc-[0-9a-f]{16}$'),
  CONSTRAINT realtime_contacts_ref_chk CHECK (client_reference IS NULL OR client_reference ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT realtime_contacts_kind_chk CHECK (kind IN ('person', 'entity')),
  CONSTRAINT realtime_contacts_tax_kind_chk CHECK (tax_id_kind IS NULL OR tax_id_kind IN ('ssn', 'itin', 'ein')),
  -- D3: never more than four digits of a tax id
  CONSTRAINT realtime_contacts_tax_last4_chk CHECK (tax_id_last4 IS NULL OR tax_id_last4 ~ '^[0-9]{0,4}$'),
  CONSTRAINT realtime_contacts_tax_digest_chk CHECK ((tax_id_digest IS NULL AND tax_id_salt IS NULL)
    OR (octet_length(tax_id_digest) = 32 AND octet_length(tax_id_salt) = 16)),
  CONSTRAINT realtime_contacts_age_chk CHECK ((age_years IS NULL) = (age_as_of IS NULL) AND (age_years IS NULL OR age_years BETWEEN 0 AND 150)),
  CONSTRAINT realtime_contacts_no_taxid_text_chk CHECK (
    coalesce(legal_name, '') !~ '[0-9]{9}' AND coalesce(email, '') !~ '[0-9]{9}' AND coalesce(phone, '') !~ '[0-9]{5}'
    AND coalesce(residential_address::text, '') !~ '[0-9]{9}' AND coalesce(mailing_address::text, '') !~ '[0-9]{9}'
    AND coalesce(employment::text, '') !~ '[0-9]{9}' AND problems::text !~ '[0-9]{9}'
    AND coalesce(visa_type, '') !~ '[0-9]{5}' AND coalesce(citizenship, '') !~ '[0-9]{5}'),
  CONSTRAINT realtime_contacts_problems_chk CHECK (jsonb_typeof(problems) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS realtime_contacts_live_uq ON cpsim.realtime_contacts (counterparty_id, contact_id)
  WHERE valid_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS realtime_contacts_ref_uq ON cpsim.realtime_contacts (counterparty_id, client_reference)
  WHERE valid_to IS NULL AND client_reference IS NOT NULL;
CREATE INDEX IF NOT EXISTS realtime_contacts_email_idx ON cpsim.realtime_contacts (counterparty_id, lower(email))
  WHERE valid_to IS NULL;

-- ── account drafts (VALID axis); opening = close the draft row, insert the opened row ───────────────────────
CREATE TABLE IF NOT EXISTS cpsim.realtime_drafts (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id        uuid NOT NULL REFERENCES cpsim.counterparties (id),
  draft_id               text NOT NULL,
  client_reference       text,
  household_external_id  text,
  account_type           text,
  account_group          text,
  owners                 jsonb NOT NULL DEFAULT '[]'::jsonb,
  trusted_contact        jsonb,
  status                 text NOT NULL DEFAULT 'draft',
  account_external_id    text,
  account_number         text,
  date_open              date,
  opened_at              timestamptz,
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz,
  CONSTRAINT realtime_drafts_id_chk CHECK (draft_id ~ '^rtd-[0-9a-f]{16}$'),
  CONSTRAINT realtime_drafts_ref_chk CHECK (client_reference IS NULL OR client_reference ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT realtime_drafts_status_chk CHECK (status IN ('draft', 'opened')),
  CONSTRAINT realtime_drafts_opened_chk CHECK ((status = 'opened') = (account_external_id IS NOT NULL
    AND account_number IS NOT NULL AND date_open IS NOT NULL AND opened_at IS NOT NULL)),
  CONSTRAINT realtime_drafts_number_chk CHECK (account_number IS NULL OR account_number ~ '^CPSIM[0-9]{9}$'),
  CONSTRAINT realtime_drafts_type_chk CHECK (account_type IS NULL OR account_type ~ '^[a-z0-9_]{1,60}$'),
  CONSTRAINT realtime_drafts_no_taxid_text_chk CHECK (owners::text !~ '[0-9]{9}'
    AND coalesce(trusted_contact::text, '') !~ '[0-9]{9}' AND coalesce(account_group, '') !~ '[0-9]{9}')
);
CREATE UNIQUE INDEX IF NOT EXISTS realtime_drafts_live_uq ON cpsim.realtime_drafts (counterparty_id, draft_id)
  WHERE valid_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS realtime_drafts_ref_uq ON cpsim.realtime_drafts (counterparty_id, client_reference)
  WHERE valid_to IS NULL AND client_reference IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS realtime_drafts_account_uq ON cpsim.realtime_drafts (counterparty_id, account_external_id)
  WHERE valid_to IS NULL AND account_external_id IS NOT NULL;

-- ── beneficiaries (append-only, per draft row) ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.realtime_beneficiaries (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  draft_row_id     uuid NOT NULL REFERENCES cpsim.realtime_drafts (id),
  position         integer NOT NULL,
  tier             text,
  name             text,
  kind             text,
  contact_id       text,
  relationship     text,
  percentage       numeric,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT realtime_beneficiaries_position_chk CHECK (position >= 0),
  CONSTRAINT realtime_beneficiaries_no_taxid_text_chk CHECK (coalesce(name, '') !~ '[0-9]{9}'
    AND coalesce(relationship, '') !~ '[0-9]{9}' AND coalesce(tier, '') !~ '[0-9]{5}' AND coalesce(kind, '') !~ '[0-9]{5}')
);
CREATE UNIQUE INDEX IF NOT EXISTS realtime_beneficiaries_row_uq ON cpsim.realtime_beneficiaries (draft_row_id, position);

-- ── validation log (append-only) ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.realtime_validation_log (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  draft_id         text NOT NULL,
  action           text NOT NULL,
  outcome          text NOT NULL,
  problems         jsonb NOT NULL DEFAULT '[]'::jsonb,
  problem_count    integer NOT NULL DEFAULT 0,
  clock_date       date NOT NULL,
  account_external_id text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT realtime_validation_log_action_chk CHECK (action IN ('validate', 'submit')),
  CONSTRAINT realtime_validation_log_outcome_chk CHECK (outcome IN ('ok', 'refused', 'opened', 'already_open')),
  CONSTRAINT realtime_validation_log_count_chk CHECK (problem_count = jsonb_array_length(problems)),
  CONSTRAINT realtime_validation_log_no_taxid_text_chk CHECK (problems::text !~ '[0-9]{9}')
);
CREATE INDEX IF NOT EXISTS realtime_validation_log_recent_idx ON cpsim.realtime_validation_log (counterparty_id, created_at DESC);

DROP TRIGGER IF EXISTS realtime_validation_log_guard_trg ON cpsim.realtime_validation_log;
CREATE TRIGGER realtime_validation_log_guard_trg BEFORE UPDATE OR DELETE ON cpsim.realtime_validation_log
  FOR EACH ROW EXECUTE FUNCTION cpsim.fix_append_only_guard();
DROP TRIGGER IF EXISTS realtime_beneficiaries_guard_trg ON cpsim.realtime_beneficiaries;
CREATE TRIGGER realtime_beneficiaries_guard_trg BEFORE UPDATE OR DELETE ON cpsim.realtime_beneficiaries
  FOR EACH ROW EXECUTE FUNCTION cpsim.fix_append_only_guard();

-- ── privileges: cpsim_app only, the documented set ──────────────────────────────────────────────────────────
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'app_service', 'litellm_service'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON cpsim.realtime_users, cpsim.realtime_tokens, cpsim.realtime_contacts, '
                     'cpsim.realtime_drafts, cpsim.realtime_beneficiaries, cpsim.realtime_validation_log FROM %I', r);
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON cpsim.realtime_users, cpsim.realtime_tokens, cpsim.realtime_contacts, cpsim.realtime_drafts,
  cpsim.realtime_beneficiaries, cpsim.realtime_validation_log FROM PUBLIC;
REVOKE ALL ON cpsim.realtime_users, cpsim.realtime_tokens, cpsim.realtime_contacts, cpsim.realtime_drafts,
  cpsim.realtime_beneficiaries, cpsim.realtime_validation_log FROM cpsim_app;
GRANT SELECT, INSERT, UPDATE ON cpsim.realtime_users, cpsim.realtime_contacts, cpsim.realtime_drafts TO cpsim_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON cpsim.realtime_tokens TO cpsim_app;
GRANT SELECT, INSERT ON cpsim.realtime_beneficiaries, cpsim.realtime_validation_log TO cpsim_app;

COMMIT;
