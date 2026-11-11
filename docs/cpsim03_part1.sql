-- cpsim03 part 1 — the simulated custodian's ORDERS + EXECUTIONS, and the minimal public changes the person actions
-- "Send to custodian" / "Import fills" need. Design: docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md; notes:
-- docs/cpsim/CPSIM03_NOTES.md (D1–D11); contract: docs/cpsim/ALTRUIST_CONTRACT.md §8 (every field ASSUMED).
--
-- cpsim schema (no org_id — a simulated counterparty is a PLATFORM object; cpsim_app only):
--   orders            one row per order a counterparty received. SYSTEM axis (Rule 3): executions reference the row's
--                     id, so a status change archives a copy (services.bitemporal.archive_live_row — the shared
--                     helper) and keeps the id. client_order_id is UNIQUE per counterparty among live rows (a repeat
--                     POST returns the same order, never a second one); order_id (sim-o-…) likewise.
--   order_executions  append-only: cpsim_app holds SELECT + INSERT only, and a trigger refuses UPDATE / DELETE except
--                     the table owner with cpsim.allow_teardown = 'fixture' (cpsim02's convention). exec_id (sim-e-…)
--                     UNIQUE per counterparty.
-- public schema — exactly what D4 / D5 / D8 / D11 need, RLS unchanged:
--   execution_instructions.custodian_order_id   the custodian's order id, set when "Send to custodian" is accepted
--                                                (+ a CHECK tying it to sent_channel / routed_via 'custodian_api';
--                                                + the guard's lifecycle list, so it may be written at approved → sent)
--   execution_fills.custodian_exec_id            the execution an imported fill came from (+ a partial UNIQUE index:
--                                                one fill per execution per instruction — a re-import books nothing)
--   execution_instruction_events_action_chk      + 'custodian_reject', 'custodian_cancel'
-- sent_channel and routed_via carry NO CHECK (catalog, 2026-11-11): the new value 'custodian_api' needs no DDL there.
-- Blocks (trade_blocks_route_chk), the FIX router and every RLS policy are untouched.
-- Re-runnable: CREATE … IF NOT EXISTS / OR REPLACE; constraints dropped and re-added by name.

BEGIN;

-- ── the orders a simulated counterparty received ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.orders (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id      uuid NOT NULL REFERENCES cpsim.counterparties (id),
  order_id             text NOT NULL,
  client_order_id      text NOT NULL,
  account_external_id  text NOT NULL,
  security_id          uuid NOT NULL,
  symbol               text,
  cusip                text,
  side                 text NOT NULL,
  quantity             numeric,
  notional             numeric,
  order_type           text NOT NULL,
  limit_price          numeric,
  time_in_force        text NOT NULL DEFAULT 'gtc',
  outcome              text NOT NULL,
  partial_pct          numeric,
  status               text NOT NULL,
  reject_reason        text,
  trade_date           date NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  archived_from_id     uuid REFERENCES cpsim.orders (id),
  CONSTRAINT orders_order_id_chk CHECK (order_id ~ '^sim-o-[0-9a-f]{16}$'),
  CONSTRAINT orders_client_order_id_chk CHECK (client_order_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT orders_side_chk CHECK (side IN ('buy', 'sell')),
  CONSTRAINT orders_amount_chk CHECK ((quantity IS NOT NULL AND notional IS NULL AND quantity > 0)
                                      OR (quantity IS NULL AND notional IS NOT NULL AND notional > 0)),
  CONSTRAINT orders_type_chk CHECK ((order_type = 'market' AND limit_price IS NULL)
                                    OR (order_type = 'limit' AND limit_price > 0)),
  CONSTRAINT orders_tif_chk CHECK (time_in_force IN ('day', 'gtc')),
  CONSTRAINT orders_outcome_chk CHECK (outcome IN ('accept', 'reject', 'partial', 'delay')),
  CONSTRAINT orders_status_chk CHECK (status IN ('accepted', 'working', 'partially_filled', 'filled', 'rejected',
                                                 'cancelled')),
  CONSTRAINT orders_rejected_chk CHECK ((status = 'rejected') = (reject_reason IS NOT NULL)),
  CONSTRAINT orders_archive_chk CHECK ((system_to IS NULL) = (archived_from_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS orders_client_order_live_uq ON cpsim.orders (counterparty_id, client_order_id)
  WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS orders_order_id_live_uq ON cpsim.orders (counterparty_id, order_id)
  WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS orders_account_idx ON cpsim.orders (counterparty_id, account_external_id, created_at DESC)
  WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS orders_working_idx ON cpsim.orders (counterparty_id, status)
  WHERE system_to IS NULL AND status IN ('accepted', 'working', 'partially_filled');

-- ── executions (append-only) ─────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.order_executions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id       uuid NOT NULL REFERENCES cpsim.counterparties (id),
  order_row_id          uuid NOT NULL REFERENCES cpsim.orders (id),
  exec_id               text NOT NULL,
  quantity              numeric NOT NULL,
  price                 numeric NOT NULL,
  executed_at           timestamptz NOT NULL,
  trade_date            date NOT NULL,
  settle_date           date NOT NULL,
  book_transaction_id   text NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT order_executions_exec_id_chk CHECK (exec_id ~ '^sim-e-[0-9a-f]{16}$'),
  CONSTRAINT order_executions_quantity_chk CHECK (quantity > 0),
  CONSTRAINT order_executions_price_chk CHECK (price > 0),
  CONSTRAINT order_executions_dates_chk CHECK (settle_date >= trade_date),
  CONSTRAINT order_executions_txn_chk CHECK (book_transaction_id = 'sim-x-' || exec_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS order_executions_exec_uq ON cpsim.order_executions (counterparty_id, exec_id);
CREATE INDEX IF NOT EXISTS order_executions_order_idx ON cpsim.order_executions (order_row_id, executed_at);

-- executions are append-only; an archived order state is immutable; deletes only for the owner's fixture teardown
CREATE OR REPLACE FUNCTION cpsim.orders_guard() RETURNS trigger
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
  IF TG_TABLE_NAME = 'order_executions' THEN
    RAISE EXCEPTION 'cpsim: order executions are append-only' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'cpsim: an archived order state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.id <> OLD.id OR NEW.order_id <> OLD.order_id OR NEW.client_order_id <> OLD.client_order_id
     OR NEW.counterparty_id <> OLD.counterparty_id THEN
    RAISE EXCEPTION 'cpsim: an order keeps its ids' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS orders_guard_trg ON cpsim.orders;
CREATE TRIGGER orders_guard_trg BEFORE UPDATE OR DELETE ON cpsim.orders
  FOR EACH ROW EXECUTE FUNCTION cpsim.orders_guard();
DROP TRIGGER IF EXISTS order_executions_guard_trg ON cpsim.order_executions;
CREATE TRIGGER order_executions_guard_trg BEFORE UPDATE OR DELETE ON cpsim.order_executions
  FOR EACH ROW EXECUTE FUNCTION cpsim.orders_guard();

-- ── privileges: the new cpsim tables to cpsim_app only (the documented set) ─────────────────────────────────
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'app_service', 'litellm_service'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON cpsim.orders, cpsim.order_executions FROM %I', r);
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON cpsim.orders, cpsim.order_executions FROM PUBLIC;
REVOKE ALL ON cpsim.orders, cpsim.order_executions FROM cpsim_app;
GRANT SELECT, INSERT, UPDATE ON cpsim.orders TO cpsim_app;
GRANT SELECT, INSERT ON cpsim.order_executions TO cpsim_app;
REVOKE ALL ON FUNCTION cpsim.orders_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cpsim.orders_guard() TO cpsim_app;

-- ── public: the custodian order id on an instruction (D5) ───────────────────────────────────────────────────
ALTER TABLE public.execution_instructions ADD COLUMN IF NOT EXISTS custodian_order_id text;
ALTER TABLE public.execution_instructions DROP CONSTRAINT IF EXISTS execution_instructions_custodian_order_chk;
ALTER TABLE public.execution_instructions ADD CONSTRAINT execution_instructions_custodian_order_chk CHECK (
  (custodian_order_id IS NULL AND sent_channel IS DISTINCT FROM 'custodian_api')
  OR (custodian_order_id IS NOT NULL AND length(btrim(custodian_order_id)) BETWEEN 1 AND 80
      AND sent_channel = 'custodian_api' AND routed_via = 'custodian_api'));

-- the guard: identical to rep2's, with custodian_order_id added to the lifecycle columns (written at approved → sent)
CREATE OR REPLACE FUNCTION public.execution_instructions_guard()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  lifecycle text[] := ARRAY['status', 'maker_ids', 'approval_task_id', 'approved_by', 'approved_at', 'sent_by',
    'sent_at', 'sent_channel', 'sent_note', 'reconciled_by', 'reconciled_at', 'reconcile_method',
    'reconcile_reference', 'reconcile_note', 'closed_by', 'closed_at', 'cancelled_by', 'cancelled_at',
    'cancel_reason', 'expired_at', 'expiry_reason', 'execute_by', 'updated_at', 'system_from',
    'custodian_order_id'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'execution instructions are never deleted (cancel one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived execution instruction state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('closed', 'cancelled', 'expired') THEN
    RAISE EXCEPTION 'a % execution instruction is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live instruction row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'draft' AND (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'execution instruction terms are frozen once submitted (status %)', OLD.status
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>cancelled', 'draft>expired',
      'pending_approval>approved', 'pending_approval>draft', 'pending_approval>cancelled', 'pending_approval>expired',
      'approved>sent', 'approved>cancelled', 'approved>expired',
      'sent>partially_filled', 'sent>filled', 'sent>cancelled', 'sent>expired',
      'partially_filled>filled', 'partially_filled>cancelled', 'partially_filled>expired',
      'filled>reconciled', 'reconciled>closed'])) THEN
    RAISE EXCEPTION 'illegal execution instruction transition % -> %', OLD.status, NEW.status
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $function$;

-- ── public: the execution an imported fill came from (D8) ───────────────────────────────────────────────────
ALTER TABLE public.execution_fills ADD COLUMN IF NOT EXISTS custodian_exec_id text;
ALTER TABLE public.execution_fills DROP CONSTRAINT IF EXISTS execution_fills_custodian_exec_chk;
ALTER TABLE public.execution_fills ADD CONSTRAINT execution_fills_custodian_exec_chk CHECK (
  custodian_exec_id IS NULL OR length(btrim(custodian_exec_id)) BETWEEN 1 AND 80);
CREATE UNIQUE INDEX IF NOT EXISTS execution_fills_custodian_exec_uq ON public.execution_fills
  (instruction_id, custodian_exec_id) WHERE custodian_exec_id IS NOT NULL AND system_to IS NULL;

-- ── public: the two custodian events (a refused order; a custodian-side cancel that leaves fills to import) ──
ALTER TABLE public.execution_instruction_events DROP CONSTRAINT IF EXISTS execution_instruction_events_action_chk;
ALTER TABLE public.execution_instruction_events ADD CONSTRAINT execution_instruction_events_action_chk CHECK (
  action = ANY (ARRAY['create'::text, 'edit'::text, 'submit'::text, 'approve'::text, 'reject'::text,
    'mark_sent'::text, 'fill'::text, 'reconcile'::text, 'close'::text, 'cancel'::text, 'expire'::text, 'carry'::text,
    'ask'::text, 'letter'::text, 'custodian_reject'::text, 'custodian_cancel'::text]));

COMMIT;
