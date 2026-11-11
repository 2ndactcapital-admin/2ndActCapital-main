-- cpsim04 part 1 — the simulated custodian's TRANSFERS, their events, the CASH LEDGER and the consent flag.
-- Design: docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md; notes: docs/cpsim/CPSIM04_NOTES.md (D1–D11); contract:
-- docs/cpsim/ALTRUIST_CONTRACT.md §9 (every field ASSUMED).
--
-- cpsim schema (no org_id — a simulated counterparty is a PLATFORM object; cpsim_app only):
--   transfers         one row per transfer a counterparty received. SYSTEM axis (Rule 3): events and cash movements
--                     reference the row's id, so a status change archives a copy (services.bitemporal.archive_live_row)
--                     and keeps the id. client_transfer_id UNIQUE per counterparty among live rows (a repeat POST
--                     returns the same transfer); transfer_id (sim-t-…) likewise. A bank destination is stored as
--                     REFERENCES only (last four digits of the routing number / IBAN, the fake BIC) — never a number.
--   transfer_events   append-only: every status a transfer took, with the counterparty's business date.
--   cash_movements    append-only: the cash LEDGER — one row per leg moved (a transfer's debit / credit, a return's two
--                     reversals, a settled fill's cash). UNIQUE (counterparty, kind, source ref, account): a leg moves
--                     once, whatever re-runs. balance_after = the account's cash row after the move.
--   counterparty_behaviour.consent_required   D8: a received transfer waits for "Record member consent".
--   book_transactions_type_chk                + the D1 cash types (transfer_in / out, ach_in / out, wire_in / out,
--                                             ach_return) — the book serves them through the existing feed.
-- Append-only tables: cpsim_app holds SELECT + INSERT only, and a trigger refuses UPDATE / DELETE except the table owner
-- with cpsim.allow_teardown = 'fixture' (cpsim02 / cpsim03's convention).
--
-- public schema — NOTHING. Catalog read 2026-11-11 (CPSIM04_NOTES §1b–1d): payment_instructions.sent_channel and
-- .adapter carry no CHECK (the channel 'custodian_api' and the adapter 'altruist_simulator' need no DDL);
-- external_ref exists and is a lifecycle column; payment_events_action_chk already admits every action D7 applies;
-- portfolio.external_references.source_system has no CHECK (the source 'CPSIM_BANK' needs no DDL); D2 maps to the
-- existing codes buy / sell / adjustment (no vocabulary row). RLS unchanged everywhere.
-- Re-runnable: CREATE … IF NOT EXISTS / OR REPLACE; constraints dropped and re-added by name.

BEGIN;

-- ── D8: the consent flag ────────────────────────────────────────────────────────────────────────────────────
ALTER TABLE cpsim.counterparty_behaviour ADD COLUMN IF NOT EXISTS consent_required boolean NOT NULL DEFAULT false;

-- ── the book serves the cash types (D1 §9.4) ────────────────────────────────────────────────────────────────
ALTER TABLE cpsim.book_transactions DROP CONSTRAINT IF EXISTS book_transactions_type_chk;
ALTER TABLE cpsim.book_transactions ADD CONSTRAINT book_transactions_type_chk CHECK (type IN (
  'buy', 'sell', 'dividend', 'interest', 'fee', 'fee_expense', 'adjustment',
  'transfer_in', 'transfer_out', 'ach_in', 'ach_out', 'wire_in', 'wire_out', 'ach_return'));

-- ── transfers ───────────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.transfers (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id          uuid NOT NULL REFERENCES cpsim.counterparties (id),
  transfer_id              text NOT NULL,
  client_transfer_id       text NOT NULL,
  from_account_external_id text NOT NULL,
  to_account_external_id   text,
  to_bank_slug             text,
  to_bank_account_id       text,
  to_bank_routing_last4    text,
  to_bank_bic              text,
  to_bank_iban_last4       text,
  amount                   numeric(20, 2) NOT NULL,
  currency_code            text NOT NULL DEFAULT 'USD',
  rail                     text NOT NULL,
  value_date               date NOT NULL,
  status                   text NOT NULL,
  reject_reason            text,
  return_code              text,
  return_reason            text,
  return_source            text,
  return_after_days        integer,
  received_on              date NOT NULL,
  acknowledged_on          date,
  settle_on                date,
  settled_on               date,
  return_due_on            date,
  returned_on              date,
  consent_recorded_at      timestamptz,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  archived_from_id         uuid REFERENCES cpsim.transfers (id),
  CONSTRAINT transfers_transfer_id_chk CHECK (transfer_id ~ '^sim-t-[0-9a-f]{16}$'),
  CONSTRAINT transfers_client_id_chk CHECK (client_transfer_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT transfers_amount_chk CHECK (amount > 0),
  CONSTRAINT transfers_currency_chk CHECK (currency_code = 'USD'),
  CONSTRAINT transfers_rail_chk CHECK (rail IN ('ach', 'wire', 'journal')),
  CONSTRAINT transfers_destination_chk CHECK (
    (rail = 'journal' AND to_account_external_id IS NOT NULL AND to_bank_slug IS NULL AND to_bank_account_id IS NULL)
    OR (rail IN ('ach', 'wire') AND to_account_external_id IS NULL AND to_bank_slug IS NOT NULL
        AND to_bank_account_id IS NOT NULL)),
  -- references only: never more than four digits of a routing number or an IBAN
  CONSTRAINT transfers_bank_refs_chk CHECK (
    (to_bank_routing_last4 IS NULL OR to_bank_routing_last4 ~ '^[0-9]{4}$')
    AND (to_bank_iban_last4 IS NULL OR to_bank_iban_last4 ~ '^[A-Z0-9]{4}$')
    AND (to_bank_bic IS NULL OR to_bank_bic ~ '^[A-Z0-9]{8}([A-Z0-9]{3})?$')),
  CONSTRAINT transfers_status_chk CHECK (status IN ('received', 'pending_consent', 'acknowledged', 'settled',
                                                    'rejected', 'returned', 'cancelled')),
  CONSTRAINT transfers_rejected_chk CHECK ((status = 'rejected') = (reject_reason IS NOT NULL)),
  CONSTRAINT transfers_returned_chk CHECK (status <> 'returned' OR (rail = 'ach' AND return_code ~ '^R[0-9]{2}$'
                                                                   AND settled_on IS NOT NULL AND returned_on IS NOT NULL)),
  CONSTRAINT transfers_return_plan_chk CHECK ((return_code IS NULL) = (return_source IS NULL)
                                              AND (return_code IS NULL OR return_code ~ '^R[0-9]{2}$')
                                              AND (return_source IS NULL OR return_source IN ('sender', 'receiver'))
                                              AND (return_after_days IS NULL OR return_after_days BETWEEN 0 AND 60)),
  CONSTRAINT transfers_settled_chk CHECK (status NOT IN ('settled', 'returned') OR settled_on IS NOT NULL),
  CONSTRAINT transfers_archive_chk CHECK ((system_to IS NULL) = (archived_from_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS transfers_client_live_uq ON cpsim.transfers (counterparty_id, client_transfer_id)
  WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS transfers_transfer_id_live_uq ON cpsim.transfers (counterparty_id, transfer_id)
  WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS transfers_from_idx ON cpsim.transfers (counterparty_id, from_account_external_id,
  created_at DESC) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS transfers_to_idx ON cpsim.transfers (counterparty_id, to_account_external_id)
  WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS transfers_bank_idx ON cpsim.transfers (to_bank_slug, to_bank_account_id)
  WHERE system_to IS NULL AND to_bank_slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS transfers_open_idx ON cpsim.transfers (counterparty_id, status)
  WHERE system_to IS NULL AND status IN ('received', 'pending_consent', 'acknowledged', 'settled');

-- ── transfer events (append-only) ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.transfer_events (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id   uuid NOT NULL REFERENCES cpsim.counterparties (id),
  transfer_row_id   uuid NOT NULL REFERENCES cpsim.transfers (id),
  seq               integer NOT NULL,
  status            text NOT NULL,
  business_date     date NOT NULL,
  detail            jsonb NOT NULL DEFAULT '{}'::jsonb,
  at                timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT transfer_events_status_chk CHECK (status IN ('received', 'pending_consent', 'acknowledged', 'settled',
                                                          'rejected', 'returned', 'cancelled')),
  CONSTRAINT transfer_events_seq_chk CHECK (seq >= 1),
  CONSTRAINT transfer_events_detail_chk CHECK (jsonb_typeof(detail) = 'object')
);
CREATE UNIQUE INDEX IF NOT EXISTS transfer_events_seq_uq ON cpsim.transfer_events (transfer_row_id, seq);

-- ── the cash ledger (append-only) ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.cash_movements (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id       uuid NOT NULL REFERENCES cpsim.counterparties (id),
  account_external_id   text NOT NULL,
  movement_kind         text NOT NULL,
  source_ref            text NOT NULL,
  amount                numeric(20, 2) NOT NULL,
  balance_after         numeric(20, 2) NOT NULL,
  effective_date        date NOT NULL,
  book_transaction_id   text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT cash_movements_kind_chk CHECK (movement_kind IN ('transfer_debit', 'transfer_credit', 'return_credit',
                                                              'return_debit', 'fill_buy', 'fill_sell')),
  CONSTRAINT cash_movements_sign_chk CHECK (
    (movement_kind IN ('transfer_debit', 'return_debit', 'fill_buy') AND amount < 0)
    OR (movement_kind IN ('transfer_credit', 'return_credit', 'fill_sell') AND amount > 0)),
  CONSTRAINT cash_movements_ref_chk CHECK (source_ref ~ '^sim-[a-z]-[0-9a-f]{16}$'),
  CONSTRAINT cash_movements_txn_chk CHECK (book_transaction_id IS NULL OR book_transaction_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS cash_movements_leg_uq ON cpsim.cash_movements (counterparty_id, movement_kind,
  source_ref, account_external_id);
CREATE INDEX IF NOT EXISTS cash_movements_account_idx ON cpsim.cash_movements (counterparty_id, account_external_id,
  effective_date);

-- the guard: transfers keep their ids and archived states are immutable; events and movements are append-only;
-- deletes only for the owner's fixture teardown
CREATE OR REPLACE FUNCTION cpsim.transfers_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, cpsim AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF COALESCE(current_setting('cpsim.allow_teardown', true), '') = 'fixture'
       AND current_user = (SELECT r.rolname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE c.oid = TG_RELID)
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'cpsim: % rows are never deleted (fixture teardown by the owner only)', TG_TABLE_NAME
      USING ERRCODE = '42501';
  END IF;
  IF TG_TABLE_NAME IN ('transfer_events', 'cash_movements') THEN
    RAISE EXCEPTION 'cpsim: % are append-only', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'cpsim: an archived transfer state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.id <> OLD.id OR NEW.transfer_id <> OLD.transfer_id OR NEW.client_transfer_id <> OLD.client_transfer_id
     OR NEW.counterparty_id <> OLD.counterparty_id OR NEW.amount <> OLD.amount OR NEW.rail <> OLD.rail
     OR NEW.from_account_external_id <> OLD.from_account_external_id THEN
    RAISE EXCEPTION 'cpsim: a transfer keeps its ids and terms' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS transfers_guard_trg ON cpsim.transfers;
CREATE TRIGGER transfers_guard_trg BEFORE UPDATE OR DELETE ON cpsim.transfers
  FOR EACH ROW EXECUTE FUNCTION cpsim.transfers_guard();
DROP TRIGGER IF EXISTS transfer_events_guard_trg ON cpsim.transfer_events;
CREATE TRIGGER transfer_events_guard_trg BEFORE UPDATE OR DELETE ON cpsim.transfer_events
  FOR EACH ROW EXECUTE FUNCTION cpsim.transfers_guard();
DROP TRIGGER IF EXISTS cash_movements_guard_trg ON cpsim.cash_movements;
CREATE TRIGGER cash_movements_guard_trg BEFORE UPDATE OR DELETE ON cpsim.cash_movements
  FOR EACH ROW EXECUTE FUNCTION cpsim.transfers_guard();

-- ── privileges: the new cpsim tables to cpsim_app only (the documented set) ─────────────────────────────────
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'app_service', 'litellm_service'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON cpsim.transfers, cpsim.transfer_events, cpsim.cash_movements FROM %I', r);
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON cpsim.transfers, cpsim.transfer_events, cpsim.cash_movements FROM PUBLIC;
REVOKE ALL ON cpsim.transfers, cpsim.transfer_events, cpsim.cash_movements FROM cpsim_app;
GRANT SELECT, INSERT, UPDATE ON cpsim.transfers TO cpsim_app;
GRANT SELECT, INSERT ON cpsim.transfer_events TO cpsim_app;
GRANT SELECT, INSERT ON cpsim.cash_movements TO cpsim_app;
REVOKE ALL ON FUNCTION cpsim.transfers_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cpsim.transfers_guard() TO cpsim_app;

COMMIT;
