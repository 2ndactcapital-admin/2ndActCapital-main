-- cpsim05 part 1 — a SIMULATED FIX counterparty (FIX 4.4 tag=value over HTTP), automatic for the simulated route only.
-- Notes: docs/cpsim/CPSIM05_NOTES.md (D1–D8). Design: docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md.
--
-- cpsim schema (platform objects, no org_id; cpsim_app only):
--   institution_types 'altruist'      + the 'fix' field group (the simulated custodian had no FIX session fields —
--                                      CPSIM05_NOTES §5 finding 1). Reference data, not a fixture.
--   orders.protocol                    'rest' (cpsim03) | 'fix' — a FIX order's partial remainder is re-evaluated on
--                                      clock advance (D2); REST orders keep cpsim03 D6's rule.
--   orders.is_block                    a block NewOrderSingle (591 present): executes WITHOUT a book move; the
--                                      AllocationInstruction moves each account (D6).
--   fix_messages                       append-only: every FIX message in and out (D1). Passwords are redacted before
--                                      insert (application) and refused here (CHECK).
--   fix_allocations                    append-only: each account leg of an accepted AllocationInstruction (D6) —
--                                      UNIQUE per (counterparty, alloc id, account): an allocation books once.
-- Append-only tables: cpsim_app holds SELECT + INSERT only; a trigger refuses UPDATE / DELETE except the table owner
-- with cpsim.allow_teardown = 'fixture' (cpsim02–04's convention).
--
-- public schema — the minimum D4 / D5 / D6 need (catalog read 2026-11-11, CPSIM05_NOTES §1c):
--   execution_instructions_custodian_order_chk  the custodian order id may also be a FIX OrderID on a 'fix_sim'
--                                               instruction; a fix_sim BLOCK member is sent with no order id of its own
--   execution_instructions_fix_sim_chk          (D5 backstop) sent_channel 'fix_sim' only when routed_via 'fix_sim'
--   execution_instruction_events_action_chk     + 'approve_upstream' (the approval of record via the proposal's
--                                               maker-checker), 'automatic_failed' (an automatic step that failed)
--   execution_instruction_events_fix_sim_guard  (D5 backstop) BEFORE INSERT: an event with detail.channel 'fix_sim',
--                                               detail.on_behalf 'automatic' or one of the two new actions is refused
--                                               unless the instruction's live routed_via is 'fix_sim'. INSERT only —
--                                               teardown deletes are unaffected.
--   trade_blocks_route_chk                      route IN ('manual', 'fix_sim') — 'fix' stays refused (not connected)
-- RLS unchanged everywhere (no table added in public; no policy touched).
-- Re-runnable: IF NOT EXISTS / OR REPLACE; constraints dropped and re-added by name.

BEGIN;

-- ── cpsim: the simulated custodian type carries a FIX session ───────────────────────────────────────────────
UPDATE cpsim.institution_types SET field_groups = array_append(field_groups, 'fix')
 WHERE key = 'altruist' AND NOT ('fix' = ANY (field_groups));

-- ── cpsim: orders placed over FIX; block orders ─────────────────────────────────────────────────────────────
ALTER TABLE cpsim.orders ADD COLUMN IF NOT EXISTS protocol text NOT NULL DEFAULT 'rest';
ALTER TABLE cpsim.orders ADD COLUMN IF NOT EXISTS is_block boolean NOT NULL DEFAULT false;
ALTER TABLE cpsim.orders DROP CONSTRAINT IF EXISTS orders_protocol_chk;
ALTER TABLE cpsim.orders ADD CONSTRAINT orders_protocol_chk CHECK (protocol IN ('rest', 'fix') AND (NOT is_block OR protocol = 'fix'));

-- ── cpsim: the FIX message log (append-only) ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.fix_messages (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid REFERENCES cpsim.counterparties (id),
  exchange_id      uuid NOT NULL,
  seq              integer NOT NULL,
  direction        text NOT NULL,
  msg_type         text,
  msg_seq_num      integer,
  sender_comp_id   text,
  target_comp_id   text,
  cl_ord_id        text,
  order_id         text,
  exec_id          text,
  alloc_id         text,
  valid            boolean NOT NULL,
  reject_reason    text,
  raw              text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fix_messages_direction_chk CHECK (direction IN ('in', 'out')),
  CONSTRAINT fix_messages_seq_chk CHECK (seq >= 0),
  CONSTRAINT fix_messages_raw_chk CHECK (length(raw) BETWEEN 1 AND 8000),
  -- a password (554) or raw data (96) value is NEVER stored: the application writes 554=[redacted]
  CONSTRAINT fix_messages_no_secret_chk CHECK (raw !~ '(^|[^0-9])(554|96)=(?!\[redacted\])'),
  CONSTRAINT fix_messages_msg_type_chk CHECK (msg_type IS NULL OR msg_type ~ '^[0-9A-Za-z]{1,2}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS fix_messages_exchange_uq ON cpsim.fix_messages (exchange_id, direction, seq);
CREATE INDEX IF NOT EXISTS fix_messages_recent_idx ON cpsim.fix_messages (counterparty_id, created_at DESC);
CREATE INDEX IF NOT EXISTS fix_messages_clordid_idx ON cpsim.fix_messages (counterparty_id, cl_ord_id)
  WHERE cl_ord_id IS NOT NULL;

-- ── cpsim: allocation legs of an accepted AllocationInstruction (append-only) ───────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.fix_allocations (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id      uuid NOT NULL REFERENCES cpsim.counterparties (id),
  order_row_id         uuid NOT NULL REFERENCES cpsim.orders (id),
  alloc_id             text NOT NULL,
  account_external_id  text NOT NULL,
  quantity             numeric NOT NULL,
  price                numeric NOT NULL,
  trade_date           date NOT NULL,
  settle_date          date NOT NULL,
  book_transaction_id  text NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fix_allocations_alloc_id_chk CHECK (alloc_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT fix_allocations_quantity_chk CHECK (quantity > 0),
  CONSTRAINT fix_allocations_price_chk CHECK (price > 0),
  CONSTRAINT fix_allocations_dates_chk CHECK (settle_date >= trade_date),
  CONSTRAINT fix_allocations_txn_chk CHECK (book_transaction_id ~ '^sim-a-[0-9a-f]{16}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS fix_allocations_leg_uq ON cpsim.fix_allocations
  (counterparty_id, alloc_id, account_external_id);
CREATE INDEX IF NOT EXISTS fix_allocations_order_idx ON cpsim.fix_allocations (order_row_id);

CREATE OR REPLACE FUNCTION cpsim.fix_append_only_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, cpsim AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND COALESCE(current_setting('cpsim.allow_teardown', true), '') = 'fixture'
     AND current_user = (SELECT r.rolname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE c.oid = TG_RELID)
  THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'cpsim: % is append-only (fixture teardown by the owner only)', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS fix_messages_guard_trg ON cpsim.fix_messages;
CREATE TRIGGER fix_messages_guard_trg BEFORE UPDATE OR DELETE ON cpsim.fix_messages
  FOR EACH ROW EXECUTE FUNCTION cpsim.fix_append_only_guard();
DROP TRIGGER IF EXISTS fix_allocations_guard_trg ON cpsim.fix_allocations;
CREATE TRIGGER fix_allocations_guard_trg BEFORE UPDATE OR DELETE ON cpsim.fix_allocations
  FOR EACH ROW EXECUTE FUNCTION cpsim.fix_append_only_guard();

-- privileges: the two new cpsim tables to cpsim_app only, SELECT + INSERT (the documented set)
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'app_service', 'litellm_service'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON cpsim.fix_messages, cpsim.fix_allocations FROM %I', r);
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON cpsim.fix_messages, cpsim.fix_allocations FROM PUBLIC;
REVOKE ALL ON cpsim.fix_messages, cpsim.fix_allocations FROM cpsim_app;
GRANT SELECT, INSERT ON cpsim.fix_messages, cpsim.fix_allocations TO cpsim_app;
REVOKE ALL ON FUNCTION cpsim.fix_append_only_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cpsim.fix_append_only_guard() TO cpsim_app;

-- ── public: a FIX OrderID on a fix_sim instruction (D4); fix_sim block members carry none of their own (D6) ─
ALTER TABLE public.execution_instructions DROP CONSTRAINT IF EXISTS execution_instructions_custodian_order_chk;
ALTER TABLE public.execution_instructions ADD CONSTRAINT execution_instructions_custodian_order_chk CHECK (
  (custodian_order_id IS NULL AND sent_channel IS DISTINCT FROM 'custodian_api')
  OR (custodian_order_id IS NOT NULL AND length(btrim(custodian_order_id)) BETWEEN 1 AND 80
      AND ((sent_channel = 'custodian_api' AND routed_via = 'custodian_api')
           OR (sent_channel = 'fix_sim' AND routed_via = 'fix_sim'))));

-- ── public: D5 backstop on the instruction — the fix_sim channel only on a fix_sim-routed instruction ───────
ALTER TABLE public.execution_instructions DROP CONSTRAINT IF EXISTS execution_instructions_fix_sim_chk;
ALTER TABLE public.execution_instructions ADD CONSTRAINT execution_instructions_fix_sim_chk CHECK (
  sent_channel IS DISTINCT FROM 'fix_sim' OR routed_via = 'fix_sim');

-- ── public: the two new event actions ───────────────────────────────────────────────────────────────────────
ALTER TABLE public.execution_instruction_events DROP CONSTRAINT IF EXISTS execution_instruction_events_action_chk;
ALTER TABLE public.execution_instruction_events ADD CONSTRAINT execution_instruction_events_action_chk CHECK (
  action = ANY (ARRAY['create'::text, 'edit'::text, 'submit'::text, 'approve'::text, 'reject'::text,
    'mark_sent'::text, 'fill'::text, 'reconcile'::text, 'close'::text, 'cancel'::text, 'expire'::text, 'carry'::text,
    'ask'::text, 'letter'::text, 'custodian_reject'::text, 'custodian_cancel'::text,
    'approve_upstream'::text, 'automatic_failed'::text]));

-- ── public: D5 backstop on the event log — automatic / fix_sim events only on a fix_sim-routed instruction ──
CREATE OR REPLACE FUNCTION public.execution_instruction_events_fix_sim_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE rv text;
BEGIN
  IF NEW.action IN ('approve_upstream', 'automatic_failed')
     OR COALESCE(NEW.detail ->> 'channel', '') = 'fix_sim'
     OR COALESCE(NEW.detail ->> 'on_behalf', '') = 'automatic' THEN
    SELECT routed_via INTO rv FROM public.execution_instructions
     WHERE id = NEW.instruction_id AND system_to IS NULL;
    IF rv IS DISTINCT FROM 'fix_sim' THEN
      RAISE EXCEPTION 'cpsim05: an automatic / fix_sim event is recorded only on an instruction routed to the simulated FIX counterparty (this one is routed %)',
        COALESCE(rv, 'not visible') USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS execution_instruction_events_fix_sim_guard_trg ON public.execution_instruction_events;
CREATE TRIGGER execution_instruction_events_fix_sim_guard_trg BEFORE INSERT ON public.execution_instruction_events
  FOR EACH ROW EXECUTE FUNCTION public.execution_instruction_events_fix_sim_guard();

-- ── public: a block may take the simulated FIX route (D6); 'fix' stays refused ─────────────────────────────
ALTER TABLE public.trade_blocks DROP CONSTRAINT IF EXISTS trade_blocks_route_chk;
ALTER TABLE public.trade_blocks ADD CONSTRAINT trade_blocks_route_chk CHECK (route IN ('manual', 'fix_sim'));

COMMIT;
