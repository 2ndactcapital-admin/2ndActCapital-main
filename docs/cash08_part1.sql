-- CASH08 part 1 — PAYEES, BANK-DETAIL CONTROLS, SLOAs (and the S30 catalog for vendor invoices' payees and the
-- third-party switch). Applied by apps/api/scripts/_apply_cash08.py --schema (operator DSN: DDL needs the table owner),
-- in ONE transaction (the apply script wraps it — no BEGIN / COMMIT here). Idempotent (IF NOT EXISTS / DROP … IF
-- EXISTS + CREATE / ON CONFLICT / NOT EXISTS). Design: docs/cash/CASH08_NOTES.md.
--
-- WHY ONE NEW TABLE (decided, v1.2 header; CASH08_NOTES §2): public.cash_payees — a household's payee registry. A
-- payee is neither a CRM entity nor a platform account. SYSTEM axis (services.bitemporal.archive_live_row): its id is
-- pointed at by approval tasks, sod_decisions and document links, so the live row keeps it and every earlier state
-- (the bank-change history included) is an archived copy.
--
-- NO BANK NUMBERS, IN THE DATABASE: cash_payees_no_numbers_trg refuses a run of 5+ digits in any free-text column, a
-- 9+ digit run once separators are removed in every non-phone column, and a phone that is not digits-with-separators;
-- last_four is exactly four digits (CHECK). Only the custodian's reference + the last four are ever stored.
--
-- TEARDOWN GUC (documented): payees.teardown = 'fixture', set LOCAL by a role that is NOT app_service
-- (public.payees_teardown_allowed()) — the ONLY path past the delete guard. Verify scripts pass it to
-- scripts/_verify_cleanup.cleanup.
--
-- S30 FLOOR: item types payee, payee_bank_change, first_payment_to_new_payee, third_party_payments_setting (floor
-- class 'transfer'), their duties, the platform FLOOR rule + policy (never; enforce_floor true) for each.
--
-- RLS: a policy for every operation, each reading the org GUC with NULLIF.
-- Writes no org row. Platform catalog rows only (permissions, role_permissions for org_admin, the SoD catalog).

-- ── 0. The teardown guard ──────────────────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.payees_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('payees.teardown', true), '') = 'fixture' AND current_user <> 'app_service'
$$;

-- ── 1. Permissions (org_admin by default; services/rbac.ORG_ADMIN_DEFAULT_PERMISSIONS keeps new orgs in step) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('manage_payees', 'payees', 'manage'),
  ('approve_payees', 'payees', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM public.roles r
  JOIN public.permissions p ON p.name IN ('manage_payees', 'approve_payees')
 WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 2. SoD catalog: four FLOOR item types ─────────────────────────────────────────────────────────────────────────
INSERT INTO public.sod_duties (duty_key, label, description, kind, permissions, sort_order) VALUES
  ('create_payee', 'Create a payee', 'Creates, edits or submits a household payee (cash_payees, CASH08).', 'make', '{manage_payees}', 150),
  ('approve_payee', 'Approve a payee', 'Approves a submitted payee; never its creator, editor or submitter.', 'check', '{approve_payees}', 151),
  ('change_bank_details', 'Change a payee''s bank details', 'Requests a change of a payee''s bank details (suspends the payee until approved).', 'make', '{manage_payees}', 152),
  ('approve_bank_change', 'Approve a bank-detail change', 'Approves a bank-detail change after a call-back to the number on file; never the requester.', 'check', '{approve_payees}', 153),
  ('approve_first_payment', 'Approve the first payment to a new payee', 'Approves the first payment instruction to a payee; never the payee''s creator or approver.', 'check', '{approve_payment}', 154),
  ('request_third_party_switch', 'Request third-party payments', 'Asks to turn on third-party payments for an organization (Super Admin, with a counsel sign-off document).', 'make', '{manage_org_settings}', 155),
  ('approve_third_party_switch', 'Approve third-party payments', 'A second Super Admin decides a request to turn on third-party payments.', 'check', '{manage_org_settings}', 156)
ON CONFLICT (duty_key) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description, kind = EXCLUDED.kind,
  permissions = EXCLUDED.permissions, sort_order = EXCLUDED.sort_order;

INSERT INTO public.sod_item_types (item_type, label, description, maker_duty, checker_duty, floor_class,
                                   self_approval_capable, pre_s30_rule, site, sort_order) VALUES
  ('payee', 'Payee', 'A household payee (vendor, charity, person, outside entity) — CASH08. A FLOOR item: the creator never approves it, Super Admin included.', 'create_payee', 'approve_payee', 'transfer', false, 'maker_ne_checker', 'services/cash_payees.py', 150),
  ('payee_bank_change', 'Bank-detail change', 'A change of a payee''s bank details — the classic fraud vector (CASH08). FLOOR: the requester never approves; a call-back to the number on file is recorded.', 'change_bank_details', 'approve_bank_change', 'transfer', false, 'maker_ne_checker', 'services/cash_payees.py', 151),
  ('first_payment_to_new_payee', 'First payment to a new payee', 'The first payment instruction to a payee (CASH08, dormant until third-party payments are enabled): the checker is neither the payee''s creator nor its approver.', 'create_payee', 'approve_first_payment', 'transfer', false, 'maker_ne_checker', 'services/cash_payees.py', 152),
  ('third_party_payments_setting', 'Third-party payments switch', 'Turning on an organization''s third-party payments (CASH08): two different Super Admins and a counsel sign-off document.', 'request_third_party_switch', 'approve_third_party_switch', 'transfer', false, 'maker_ne_checker', 'services/cash_payees.py', 153)
ON CONFLICT (item_type) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description,
  maker_duty = EXCLUDED.maker_duty, checker_duty = EXCLUDED.checker_duty, floor_class = EXCLUDED.floor_class,
  self_approval_capable = EXCLUDED.self_approval_capable, pre_s30_rule = EXCLUDED.pre_s30_rule,
  site = EXCLUDED.site, sort_order = EXCLUDED.sort_order;

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, maker_duty, checker_duty, scope,
                              severity, description, status, version_no)
SELECT 'floor:' || t.item_type, 'platform', NULL, 'floor', t.item_type, t.maker_duty, t.checker_duty,
       'same_item', 'block',
       'The same person may not ' || lower(m.label) || ' and ' || lower(c.label) || ' on the same '
         || lower(t.label) || '.', 'active', 1
  FROM public.sod_item_types t
  JOIN public.sod_duties m ON m.duty_key = t.maker_duty
  JOIN public.sod_duties c ON c.duty_key = t.checker_duty
 WHERE t.item_type IN ('payee', 'payee_bank_change', 'first_payment_to_new_payee', 'third_party_payments_setting')
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind IN ('floor', 'default'));

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, self_approval,
                              enforce_floor, description, status, version_no)
SELECT 'policy:' || t.item_type, 'platform', NULL, 'policy', t.item_type, 'same_item', 'block', 'never', true,
       'Platform policy for ' || lower(t.label) || ': a different person approves, always (CASH08 floor).', 'active', 1
  FROM public.sod_item_types t
 WHERE t.item_type IN ('payee', 'payee_bank_change', 'first_payment_to_new_payee', 'third_party_payments_setting')
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind = 'policy');

-- ── 3. Payees (ORG, system-axis) ───────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.cash_payees (
  id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                        uuid NOT NULL REFERENCES public.organizations(id),
  household_id                  uuid NOT NULL REFERENCES public.households(id),
  kind                          text NOT NULL,
  display_name                  text NOT NULL,
  legal_name                    text,
  contact_name                  text,
  contact_email                 text,
  contact_phone                 text,
  bank_name                     text,
  account_type                  text,
  last_four                     text,
  payee_reference               text,
  notes                         text,
  status                        text NOT NULL DEFAULT 'draft',
  created_by                    uuid NOT NULL REFERENCES public.users(id),
  maker_ids                     uuid[] NOT NULL,
  approval_task_id              uuid REFERENCES public.workflow_run_steps(id),
  approved_by                   uuid REFERENCES public.users(id),
  approved_at                   timestamptz,
  -- the governed bank-detail change (pending_* = the REQUESTED details; the current ones stand until approved)
  pending_bank_name             text,
  pending_account_type          text,
  pending_last_four             text,
  pending_payee_reference       text,
  pending_contact_phone         text,
  pending_contact_email         text,
  bank_change_requested_by      uuid REFERENCES public.users(id),
  bank_change_requested_at      timestamptz,
  bank_change_request_contact   text,
  bank_change_request_phone     text,
  bank_change_reason            text,
  bank_change_task_id           uuid REFERENCES public.workflow_run_steps(id),
  bank_change_approved_by       uuid REFERENCES public.users(id),
  bank_change_approved_at       timestamptz,
  callback_person               text,
  callback_number               text,
  callback_at                   timestamptz,
  -- the Standing Letter of Authorization (a Chancery document; also linked 'cash_payee_sloa' in document_record_links)
  sloa_document_id              uuid REFERENCES public.documents(id),
  sloa_account_ids              uuid[] NOT NULL DEFAULT '{}',
  sloa_signed_on                date,
  sloa_expires_on               date,
  sloa_attached_by              uuid REFERENCES public.users(id),
  sloa_attached_at              timestamptz,
  sloa_expired_at               timestamptz,
  sloa_expired_by               uuid REFERENCES public.users(id),
  sloa_revoked_at               timestamptz,
  sloa_revoked_by               uuid REFERENCES public.users(id),
  sloa_revoke_reason            text,
  suspended_by                  uuid REFERENCES public.users(id),
  suspended_at                  timestamptz,
  suspend_reason                text,
  retired_by                    uuid REFERENCES public.users(id),
  retired_at                    timestamptz,
  retire_reason                 text,
  archived_from_id              uuid REFERENCES public.cash_payees(id),
  created_at                    timestamptz NOT NULL DEFAULT now(),
  updated_at                    timestamptz NOT NULL DEFAULT now(),
  valid_from                    timestamptz NOT NULL DEFAULT now(),
  valid_to                      timestamptz,
  system_from                   timestamptz NOT NULL DEFAULT now(),
  system_to                     timestamptz,
  CONSTRAINT cash_payees_kind_chk CHECK (kind IN ('vendor', 'charity', 'person', 'outside_entity')),
  CONSTRAINT cash_payees_status_chk CHECK (status IN ('draft', 'pending_approval', 'active', 'suspended', 'retired')),
  CONSTRAINT cash_payees_name_chk CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
  CONSTRAINT cash_payees_account_type_chk CHECK (account_type IS NULL OR account_type IN ('checking', 'savings', 'other')),
  CONSTRAINT cash_payees_pending_account_type_chk CHECK (
    pending_account_type IS NULL OR pending_account_type IN ('checking', 'savings', 'other')),
  -- the LAST FOUR only — exactly four digits, never more
  CONSTRAINT cash_payees_last_four_chk CHECK (last_four IS NULL OR last_four ~ '^[0-9]{4}$'),
  CONSTRAINT cash_payees_pending_last_four_chk CHECK (pending_last_four IS NULL OR pending_last_four ~ '^[0-9]{4}$'),
  CONSTRAINT cash_payees_makers_chk CHECK (created_by = ANY (maker_ids)),
  -- maker-checker in the database: the approver is never a maker (the S30 floor's backstop)
  CONSTRAINT cash_payees_maker_checker_chk CHECK (approved_by IS NULL OR NOT (approved_by = ANY (maker_ids))),
  CONSTRAINT cash_payees_active_chk CHECK (
    status IN ('draft', 'pending_approval', 'retired') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT cash_payees_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  -- an active payee carries its bank reference + last four
  CONSTRAINT cash_payees_bank_chk CHECK (
    status NOT IN ('pending_approval', 'active')
    OR (length(btrim(COALESCE(payee_reference, ''))) > 0 AND last_four IS NOT NULL)),
  -- a pending bank change is open ⇔ the payee is suspended for it, with its requester, task and contact on record
  CONSTRAINT cash_payees_bank_change_open_chk CHECK (
    bank_change_task_id IS NULL
    OR (status = 'suspended' AND bank_change_requested_by IS NOT NULL AND bank_change_requested_at IS NOT NULL
        AND pending_last_four IS NOT NULL AND length(btrim(COALESCE(pending_payee_reference, ''))) > 0
        AND length(btrim(COALESCE(bank_change_request_contact, ''))) > 0)),
  -- an approved bank change: a DIFFERENT person, and the full call-back attestation (who, which number, when)
  CONSTRAINT cash_payees_bank_change_checker_chk CHECK (
    bank_change_approved_by IS NULL
    OR (bank_change_approved_by IS DISTINCT FROM bank_change_requested_by AND bank_change_approved_at IS NOT NULL
        AND length(btrim(COALESCE(callback_person, ''))) > 0 AND length(btrim(COALESCE(callback_number, ''))) > 0
        AND callback_at IS NOT NULL)),
  CONSTRAINT cash_payees_suspended_chk CHECK (
    status <> 'suspended' OR bank_change_task_id IS NOT NULL
    OR (suspended_by IS NOT NULL AND suspended_at IS NOT NULL AND length(btrim(COALESCE(suspend_reason, ''))) > 0)),
  CONSTRAINT cash_payees_retired_chk CHECK (
    status <> 'retired' OR (retired_by IS NOT NULL AND retired_at IS NOT NULL
                            AND length(btrim(COALESCE(retire_reason, ''))) > 0)),
  CONSTRAINT cash_payees_sloa_chk CHECK (
    sloa_document_id IS NOT NULL OR (sloa_expired_at IS NULL AND sloa_revoked_at IS NULL AND sloa_attached_at IS NULL)),
  CONSTRAINT cash_payees_sloa_dates_chk CHECK (
    sloa_expires_on IS NULL OR sloa_signed_on IS NULL OR sloa_expires_on > sloa_signed_on),
  CONSTRAINT cash_payees_sloa_revoked_chk CHECK (
    sloa_revoked_at IS NULL OR (sloa_revoked_by IS NOT NULL AND length(btrim(COALESCE(sloa_revoke_reason, ''))) > 0)),
  CONSTRAINT cash_payees_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- converge a table created by an earlier run of this file (CREATE TABLE IF NOT EXISTS does not change a CHECK)
ALTER TABLE public.cash_payees DROP CONSTRAINT IF EXISTS cash_payees_active_chk;
ALTER TABLE public.cash_payees ADD CONSTRAINT cash_payees_active_chk CHECK (
  status IN ('draft', 'pending_approval', 'retired') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL));
CREATE INDEX IF NOT EXISTS cash_payees_household_idx ON public.cash_payees (org_id, household_id) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS cash_payees_archive_idx ON public.cash_payees (archived_from_id) WHERE archived_from_id IS NOT NULL;
-- one live payee per (household, display name) that is not retired
CREATE UNIQUE INDEX IF NOT EXISTS cash_payees_name_live_uq
  ON public.cash_payees (household_id, lower(btrim(display_name))) WHERE system_to IS NULL AND status <> 'retired';
CREATE UNIQUE INDEX IF NOT EXISTS cash_payees_task_live_uq
  ON public.cash_payees (approval_task_id) WHERE system_to IS NULL AND approval_task_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cash_payees_bank_task_live_uq
  ON public.cash_payees (bank_change_task_id) WHERE system_to IS NULL AND bank_change_task_id IS NOT NULL;

-- PHONE SHAPE (cash08 fix 1; mirrors services/cash_payees.py is_phone — keep the two identical): only digits, '+',
-- spaces, '.', '-', '(' and ')'; no run of five or more digits; and either NANP — optional +1 / 1, then 3-3-4 digits with
-- separators ("212-555-0100", "(212) 555-0100", "+1 212.555.0100"; 10–11 digits) — or international E.164 — a leading
-- '+', a 1–3 digit country code, then groups of 1–4 digits (10–15 digits). Not a phone: "1234 5678 9012" (no phone
-- shape), "2125550100" (unseparated), a 7-digit local number.
CREATE OR REPLACE FUNCTION public.cash_payees_is_phone(v text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT v IS NOT NULL AND btrim(v, ' ') ~ '^[+0-9 ().-]+$' AND btrim(v, ' ') !~ '[0-9]{5,}'
     AND ((btrim(v, ' ') ~ '^([+]?1[ .-]?)?([(][0-9]{3}[)] ?|[0-9]{3}[ .-])[0-9]{3}[ .-][0-9]{4}$'
           AND length(regexp_replace(v, '[^0-9]', '', 'g')) IN (10, 11))
       OR (btrim(v, ' ') ~ '^[+][0-9]{1,3}([ .-]+[(]?[0-9]{1,4}[)]?)+$'
           AND length(regexp_replace(v, '[^0-9]', '', 'g')) BETWEEN 10 AND 15))
$$;

-- CONTACT TEXT (cash08 fix 1; mirrors contact_text_without_phones): the text with every phone-shaped token removed —
-- what is left is held to the bank-number rule; a token that is NOT phone-shaped stays.
CREATE OR REPLACE FUNCTION public.cash_payees_contact_without_phones(v text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  rest text := v;
  out text := '';
  m text;
  p int;
BEGIN
  IF v IS NULL THEN
    RETURN NULL;
  END IF;
  LOOP
    m := substring(rest from '[+]?[0-9(][0-9 ().-]{6,24}[0-9]');
    EXIT WHEN m IS NULL;
    p := strpos(rest, m);   -- the leftmost match's own position (an earlier copy would itself be the leftmost match)
    out := out || left(rest, p - 1) || CASE WHEN public.cash_payees_is_phone(m) THEN ' ' ELSE m END;
    rest := substr(rest, p + length(m));
  END LOOP;
  RETURN out || rest;
END $$;

-- NO BANK NUMBERS: the last four exactly four digits (ahead of the CHECK: a CHECK's error DETAIL echoes the row); a
-- run of 5+ digits anywhere; a 9+ digit run once separators go (non-phone columns); phone columns hold a PHONE SHAPE
-- (above) and nothing else; the contact-text column (how a bank-change request arrived) may quote phone-shaped numbers
-- but never one labelled as an account / routing number, and everything else in it is held to the bank rule. Bank
-- fields (bank name, reference, last four, notes) never get the phone exemption. The error names the column; never the
-- value.
CREATE OR REPLACE FUNCTION public.cash_payees_no_numbers() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  j jsonb := to_jsonb(NEW);
  c text;
  v text;
  free_cols text[] := ARRAY['display_name', 'legal_name', 'contact_name', 'contact_email', 'bank_name',
    'payee_reference', 'notes', 'pending_bank_name', 'pending_payee_reference', 'pending_contact_email',
    'bank_change_request_contact', 'bank_change_reason', 'callback_person', 'sloa_revoke_reason', 'suspend_reason',
    'retire_reason'];
  phone_cols text[] := ARRAY['contact_phone', 'pending_contact_phone', 'bank_change_request_phone', 'callback_number'];
  contact_cols text[] := ARRAY['bank_change_request_contact'];
BEGIN
  IF NEW.system_to IS NOT NULL THEN
    RETURN NEW;   -- an archived copy of a row that already passed
  END IF;
  -- the last four first (a BEFORE trigger fires ahead of the CHECK, whose error DETAIL would echo the whole row)
  IF NEW.last_four IS NOT NULL AND NEW.last_four !~ '^[0-9]{4}$' THEN
    RAISE EXCEPTION 'cash_payees.last_four: exactly the last four digits, never more' USING ERRCODE = '23514';
  END IF;
  IF NEW.pending_last_four IS NOT NULL AND NEW.pending_last_four !~ '^[0-9]{4}$' THEN
    RAISE EXCEPTION 'cash_payees.pending_last_four: exactly the last four digits, never more' USING ERRCODE = '23514';
  END IF;
  FOREACH c IN ARRAY free_cols LOOP
    v := j ->> c;
    CONTINUE WHEN v IS NULL;
    IF c = ANY (contact_cols) THEN
      IF v ~* '(^|[^a-z])(acct|account|routing|aba|iban|a/c)s?[^a-z0-9]{0,4}(no|number|num|#)?[^a-z0-9]{0,4}[+(]?[0-9]' THEN
        RAISE EXCEPTION 'cash_payees.%: a number labelled as an account or routing number — never stored', c
          USING ERRCODE = '23514';
      END IF;
      v := public.cash_payees_contact_without_phones(v);
    END IF;
    IF v ~ '[0-9]{5,}' THEN
      RAISE EXCEPTION 'cash_payees.%: a run of more than four digits looks like an account or routing number — store the custodian''s reference and the last four only', c
        USING ERRCODE = '23514';
    END IF;
    IF regexp_replace(v, '[[:space:]./-]', '', 'g') ~ '[0-9]{9,}' THEN
      RAISE EXCEPTION 'cash_payees.%: nine or more digits (separators ignored) look like a routing or account number — never stored', c
        USING ERRCODE = '23514';
    END IF;
  END LOOP;
  FOREACH c IN ARRAY phone_cols LOOP
    v := j ->> c;
    CONTINUE WHEN v IS NULL;
    IF NOT public.cash_payees_is_phone(v) THEN
      RAISE EXCEPTION 'cash_payees.%: a phone number is 212-555-0100 / (212) 555-0100 / +1 212 555 0100, or +<country> in groups of up to four digits (10–15 digits) — never an account or routing number', c
        USING ERRCODE = '23514';
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cash_payees_no_numbers_trg ON public.cash_payees;
CREATE TRIGGER cash_payees_no_numbers_trg BEFORE INSERT OR UPDATE ON public.cash_payees
  FOR EACH ROW EXECUTE FUNCTION public.cash_payees_no_numbers();

-- Status graph + archived states immutable + never deleted (but by the teardown guard).
CREATE OR REPLACE FUNCTION public.cash_payees_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.payees_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'payees are never deleted (retire one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived payee state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'retired' THEN
    RAISE EXCEPTION 'a retired payee is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live payee keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF NEW.org_id <> OLD.org_id OR NEW.household_id <> OLD.household_id OR NEW.created_by <> OLD.created_by THEN
    RAISE EXCEPTION 'a payee''s organization, household and creator never change' USING ERRCODE = '42501';
  END IF;
  -- outside the governed change, the bank details of a submitted payee never change in place: the ONLY path is the
  -- approval of an OPEN change (its task closes, an approver is named, the new details are exactly the pending ones)
  IF OLD.status <> 'draft'
     AND (NEW.last_four IS DISTINCT FROM OLD.last_four OR NEW.payee_reference IS DISTINCT FROM OLD.payee_reference
          OR NEW.bank_name IS DISTINCT FROM OLD.bank_name OR NEW.account_type IS DISTINCT FROM OLD.account_type
          OR NEW.contact_phone IS DISTINCT FROM OLD.contact_phone)
     AND NOT (OLD.bank_change_task_id IS NOT NULL AND NEW.bank_change_task_id IS NULL
              AND NEW.bank_change_approved_by IS NOT NULL
              AND NEW.bank_change_approved_at IS DISTINCT FROM OLD.bank_change_approved_at
              AND NEW.last_four IS NOT DISTINCT FROM OLD.pending_last_four
              AND NEW.payee_reference IS NOT DISTINCT FROM OLD.pending_payee_reference) THEN
    RAISE EXCEPTION 'a payee''s bank details and call-back number change only through an approved bank-detail change'
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>retired',
      'pending_approval>active', 'pending_approval>draft', 'pending_approval>retired',
      'active>suspended', 'active>retired',
      'suspended>active', 'suspended>pending_approval', 'suspended>retired'])) THEN
    RAISE EXCEPTION 'illegal payee transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cash_payees_guard_trg ON public.cash_payees;
CREATE TRIGGER cash_payees_guard_trg BEFORE UPDATE OR DELETE ON public.cash_payees
  FOR EACH ROW EXECUTE FUNCTION public.cash_payees_guard();

-- ── 4. RLS — a policy for every operation ──────────────────────────────────────────────────────────────────────────
DO $$
DECLARE pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  EXECUTE 'ALTER TABLE public.cash_payees ENABLE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS cash_payees_org_select ON public.cash_payees';
  EXECUTE format('CREATE POLICY cash_payees_org_select ON public.cash_payees FOR SELECT USING %s', pred);
  EXECUTE 'DROP POLICY IF EXISTS cash_payees_org_insert ON public.cash_payees';
  EXECUTE format('CREATE POLICY cash_payees_org_insert ON public.cash_payees FOR INSERT WITH CHECK %s', pred);
  EXECUTE 'DROP POLICY IF EXISTS cash_payees_org_update ON public.cash_payees';
  EXECUTE format('CREATE POLICY cash_payees_org_update ON public.cash_payees FOR UPDATE USING %s WITH CHECK %s', pred, pred);
  EXECUTE 'DROP POLICY IF EXISTS cash_payees_org_delete ON public.cash_payees';
  EXECUTE format('CREATE POLICY cash_payees_org_delete ON public.cash_payees FOR DELETE USING %s', pred);
  EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.cash_payees TO app_service';
END $$;
