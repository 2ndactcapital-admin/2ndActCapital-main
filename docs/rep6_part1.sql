-- REP6 — Replication Studio, sprint 6: the tax engine. Member tax profiles and
-- loss carryforwards (adviser-entered, approved by a second person), the
-- substitution table (a versioned, approved compliance artifact) and its
-- denials (pairs that are substantially identical and may never pair),
-- declared outside trades, the loss bank, and daily loss-harvest runs and
-- their candidates. Harvest proposals are rep5 proposals (trigger 'harvest');
-- every trade line now carries its household wash-sale control result.
--
-- Applied by apps/api/scripts/_apply_rep6.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- SCHEMA. Every new table lives in `public` and is ORG-scoped: NULLIF org
-- isolation (or the super-admin carve-out) on SELECT, INSERT, UPDATE and
-- DELETE — a policy for every operation each table takes.
--
-- TAX SUBJECT (Task 1b). A profile, a carryforward, a loss-bank entry and a
-- declared outside trade hang on a SUBJECT: the account's household
-- (public.households — a married couple filing jointly is one subject covering
-- both spouses' accounts), or, for an account with no household, its primary
-- entity. subject_type IN ('household', 'entity'); no FK (polymorphic), checked
-- by the services against the caller's org.
--
-- BI-TEMPORAL (Rule 3)
--   tax_profiles, loss_carryforwards  — BOTH axes. A correction or a new year's
--                        figures is a NEW version row (its own id, version_no
--                        + 1 in the subject + year); approving it closes the
--                        approved version it replaces on the VALID axis. A
--                        version's own decision (pending → approved /
--                        rejected, approved → superseded) is a SYSTEM-axis
--                        archive — harvest candidates and loss-bank entries
--                        point at the profile / carryforward id, so it is kept.
--   substitution_sets  — BOTH axes, the same way (every version kept); its
--                        candidates are immutable rows of the version.
--   substitution_denials — SYSTEM axis (status pending → active / rejected,
--                        active → retired).
--   declared_outside_trades — VALID axis: a correction closes the row and
--                        inserts its successor; a withdrawal closes it.
--   loss_bank_entries  — VALID axis: an entry is never edited; a provisional
--                        'fill' entry is CLOSED (valid_to) when the
--                        custodian's closed lot for the same tax lot arrives,
--                        so nothing is counted twice. Unique per source key on
--                        every live-system row (closed ones included), so a
--                        re-run never re-adds what was superseded.
--   harvest_runs, harvest_candidates — VALID axis, written once, immutable.
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(): execution.teardown = 'fixture', never
--   as app_service) — the same switch rep2 and rep5 use.
--
-- REP5 CHANGES
--   * rebalance_proposals.trigger gains 'harvest'.
--   * rebalance_proposal_trades gains `wash_sale` (jsonb: the control result —
--     outcome clear | replaced | deferred | blocked, with its matches). A line
--     whose outcome is deferred or blocked is RECORDED on the proposal and never
--     routed to an instruction. `wash_sale_flag` (rep5's flag) stays for the
--     proposals that carry it; rep6 never writes it.
--   * account_model_links gains the harvest settings (NULL = the org default):
--     harvest_loss_pct, harvest_loss_min, harvest_to_cash, harvest_swap_back.
--
-- THE CUSTODY CLIFF. Nothing here trades. A harvest is a proposal a person
-- approves; an approved proposal becomes execution instructions a person
-- carries out.

-- ── 0. Permissions (org_admin by default; rbac.ensure_org_admin_role too) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_tax', 'tax', 'view'),
  ('manage_tax', 'tax', 'manage'),
  ('approve_tax', 'tax', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_tax', 'manage_tax', 'approve_tax')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 1. Tax profiles (adviser-entered rates, versioned, approved) ───────────
CREATE TABLE IF NOT EXISTS public.tax_profiles (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 uuid NOT NULL REFERENCES public.organizations(id),
  subject_type           text NOT NULL,
  subject_id             uuid NOT NULL,
  tax_year               integer NOT NULL,
  version_no             integer NOT NULL DEFAULT 1,
  filing_status          text NOT NULL,
  federal_ordinary_rate  numeric(8, 6) NOT NULL,
  federal_lt_rate        numeric(8, 6) NOT NULL,
  niit_applies           boolean NOT NULL,
  niit_rate              numeric(8, 6) NOT NULL DEFAULT 0.038,
  state_code             text,
  state_rate             numeric(8, 6) NOT NULL DEFAULT 0,
  state_lt_rate          numeric(8, 6),
  city                   text,
  city_rate              numeric(8, 6) NOT NULL DEFAULT 0,
  city_lt_rate           numeric(8, 6),
  as_of                  date NOT NULL,
  source                 text NOT NULL DEFAULT 'adviser',
  source_note            text NOT NULL,
  notes                  text,
  status                 text NOT NULL DEFAULT 'pending_approval',
  entered_by             uuid NOT NULL REFERENCES public.users(id),
  checker_id             uuid REFERENCES public.users(id),
  approval_task_id       uuid REFERENCES public.workflow_run_steps(id),
  decided_at             timestamptz,
  decision_note          text,
  replaces_id            uuid REFERENCES public.tax_profiles(id),
  archived_from_id       uuid REFERENCES public.tax_profiles(id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz,
  system_from            timestamptz NOT NULL DEFAULT now(),
  system_to              timestamptz,
  CONSTRAINT tax_profiles_subject_chk CHECK (subject_type IN ('household', 'entity')),
  CONSTRAINT tax_profiles_year_chk CHECK (tax_year BETWEEN 2000 AND 2100),
  CONSTRAINT tax_profiles_version_chk CHECK (version_no >= 1),
  CONSTRAINT tax_profiles_filing_chk CHECK (filing_status IN ('single', 'married_filing_jointly',
    'married_filing_separately', 'head_of_household', 'qualifying_surviving_spouse')),
  CONSTRAINT tax_profiles_rates_chk CHECK (
    federal_ordinary_rate >= 0 AND federal_ordinary_rate < 1 AND federal_lt_rate >= 0 AND federal_lt_rate < 1
    AND niit_rate >= 0 AND niit_rate < 1 AND state_rate >= 0 AND state_rate < 1 AND city_rate >= 0 AND city_rate < 1
    AND (state_lt_rate IS NULL OR (state_lt_rate >= 0 AND state_lt_rate < 1))
    AND (city_lt_rate IS NULL OR (city_lt_rate >= 0 AND city_lt_rate < 1))),
  -- tax inputs come from the adviser, with a date and a source — never guessed
  CONSTRAINT tax_profiles_source_chk CHECK (source = 'adviser' AND length(btrim(source_note)) > 0),
  CONSTRAINT tax_profiles_status_chk CHECK (status IN ('pending_approval', 'approved', 'rejected', 'superseded')),
  CONSTRAINT tax_profiles_maker_checker_chk CHECK (checker_id IS NULL OR checker_id <> entered_by),
  CONSTRAINT tax_profiles_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL
                                             OR system_to IS NOT NULL),
  CONSTRAINT tax_profiles_decided_chk CHECK (status NOT IN ('approved', 'rejected', 'superseded')
                                             OR (checker_id IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT tax_profiles_rejected_chk CHECK (status <> 'rejected' OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT tax_profiles_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one LIVE approved profile and one pending change per subject and tax year
CREATE UNIQUE INDEX IF NOT EXISTS tax_profiles_approved_uq
  ON public.tax_profiles (org_id, subject_type, subject_id, tax_year)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tax_profiles_pending_uq
  ON public.tax_profiles (org_id, subject_type, subject_id, tax_year)
  WHERE status = 'pending_approval' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tax_profiles_version_uq
  ON public.tax_profiles (org_id, subject_type, subject_id, tax_year, version_no) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tax_profiles_task_uq
  ON public.tax_profiles (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

-- ── 2. Loss carryforwards (from the prior return, approved) ────────────────
CREATE TABLE IF NOT EXISTS public.loss_carryforwards (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  subject_type       text NOT NULL,
  subject_id         uuid NOT NULL,
  tax_year           integer NOT NULL,
  version_no         integer NOT NULL DEFAULT 1,
  st_amount          numeric(20, 2) NOT NULL DEFAULT 0,
  lt_amount          numeric(20, 2) NOT NULL DEFAULT 0,
  source_document    text NOT NULL,
  notes              text,
  status             text NOT NULL DEFAULT 'pending_approval',
  entered_by         uuid NOT NULL REFERENCES public.users(id),
  checker_id         uuid REFERENCES public.users(id),
  approval_task_id   uuid REFERENCES public.workflow_run_steps(id),
  decided_at         timestamptz,
  decision_note      text,
  replaces_id        uuid REFERENCES public.loss_carryforwards(id),
  archived_from_id   uuid REFERENCES public.loss_carryforwards(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT loss_carryforwards_subject_chk CHECK (subject_type IN ('household', 'entity')),
  CONSTRAINT loss_carryforwards_year_chk CHECK (tax_year BETWEEN 2000 AND 2100),
  CONSTRAINT loss_carryforwards_version_chk CHECK (version_no >= 1),
  -- an opening carryforward is a LOSS carried in, stated as a non-negative amount per character
  CONSTRAINT loss_carryforwards_amount_chk CHECK (st_amount >= 0 AND lt_amount >= 0),
  CONSTRAINT loss_carryforwards_source_chk CHECK (length(btrim(source_document)) > 0),
  CONSTRAINT loss_carryforwards_status_chk CHECK (status IN ('pending_approval', 'approved', 'rejected', 'superseded')),
  CONSTRAINT loss_carryforwards_maker_checker_chk CHECK (checker_id IS NULL OR checker_id <> entered_by),
  CONSTRAINT loss_carryforwards_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL
                                                   OR system_to IS NOT NULL),
  CONSTRAINT loss_carryforwards_decided_chk CHECK (status NOT IN ('approved', 'rejected', 'superseded')
                                                   OR (checker_id IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT loss_carryforwards_rejected_chk CHECK (status <> 'rejected'
                                                    OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT loss_carryforwards_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS loss_carryforwards_approved_uq
  ON public.loss_carryforwards (org_id, subject_type, subject_id, tax_year)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS loss_carryforwards_pending_uq
  ON public.loss_carryforwards (org_id, subject_type, subject_id, tax_year)
  WHERE status = 'pending_approval' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS loss_carryforwards_version_uq
  ON public.loss_carryforwards (org_id, subject_type, subject_id, tax_year, version_no) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS loss_carryforwards_task_uq
  ON public.loss_carryforwards (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

-- one guard for both versioned, approved tax inputs
CREATE OR REPLACE FUNCTION public.tax_inputs_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  decision text[] := ARRAY['status', 'checker_id', 'approval_task_id', 'decided_at', 'decision_note', 'valid_to',
    'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'tax inputs are never deleted (a correction is a new version)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived tax input state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live tax input row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NOT NULL THEN
    RAISE EXCEPTION 'a closed tax input version is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('rejected', 'superseded') THEN
    RAISE EXCEPTION 'a % tax input is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  -- what the adviser entered never changes: a correction is a new version
  IF (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
    RAISE EXCEPTION 'tax inputs are versioned: a correction is a new version, not an edit' USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'pending_approval>approved', 'pending_approval>rejected', 'approved>superseded'])) THEN
    RAISE EXCEPTION 'illegal tax input transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tax_profiles_guard_trg ON public.tax_profiles;
CREATE TRIGGER tax_profiles_guard_trg BEFORE UPDATE OR DELETE ON public.tax_profiles
  FOR EACH ROW EXECUTE FUNCTION public.tax_inputs_guard();
DROP TRIGGER IF EXISTS loss_carryforwards_guard_trg ON public.loss_carryforwards;
CREATE TRIGGER loss_carryforwards_guard_trg BEFORE UPDATE OR DELETE ON public.loss_carryforwards
  FOR EACH ROW EXECUTE FUNCTION public.tax_inputs_guard();

-- ── 3. Substitution sets (versioned, approved compliance artifact) ─────────
CREATE TABLE IF NOT EXISTS public.substitution_sets (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  version_no          integer NOT NULL DEFAULT 1,
  status              text NOT NULL DEFAULT 'pending_approval',
  notes               text,
  maker_id            uuid NOT NULL REFERENCES public.users(id),
  checker_id          uuid REFERENCES public.users(id),
  approval_task_id    uuid REFERENCES public.workflow_run_steps(id),
  decided_at          timestamptz,
  decision_note       text,
  replaces_id         uuid REFERENCES public.substitution_sets(id),
  archived_from_id    uuid REFERENCES public.substitution_sets(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT substitution_sets_version_chk CHECK (version_no >= 1),
  CONSTRAINT substitution_sets_status_chk CHECK (status IN ('pending_approval', 'approved', 'rejected', 'superseded',
                                                            'retired')),
  CONSTRAINT substitution_sets_maker_checker_chk CHECK (checker_id IS NULL OR checker_id <> maker_id),
  CONSTRAINT substitution_sets_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL
                                                  OR system_to IS NOT NULL),
  CONSTRAINT substitution_sets_decided_chk CHECK (status NOT IN ('approved', 'rejected', 'superseded', 'retired')
                                                  OR (checker_id IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT substitution_sets_rejected_chk CHECK (status <> 'rejected'
                                                   OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT substitution_sets_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS substitution_sets_approved_uq
  ON public.substitution_sets (org_id, global_security_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS substitution_sets_pending_uq
  ON public.substitution_sets (org_id, global_security_id)
  WHERE status = 'pending_approval' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS substitution_sets_version_uq
  ON public.substitution_sets (org_id, global_security_id, version_no) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS substitution_sets_task_uq
  ON public.substitution_sets (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.substitution_sets_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  decision text[] := ARRAY['status', 'checker_id', 'approval_task_id', 'decided_at', 'decision_note', 'valid_to',
    'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'substitution sets are never deleted (every version is kept)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived substitution set state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live substitution set row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NOT NULL THEN
    RAISE EXCEPTION 'a closed substitution set version is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('rejected', 'superseded', 'retired') THEN
    RAISE EXCEPTION 'a % substitution set is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
    RAISE EXCEPTION 'substitution sets are versioned: a change is a new version, not an edit' USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'pending_approval>approved', 'pending_approval>rejected', 'approved>superseded', 'approved>retired'])) THEN
    RAISE EXCEPTION 'illegal substitution set transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS substitution_sets_guard_trg ON public.substitution_sets;
CREATE TRIGGER substitution_sets_guard_trg BEFORE UPDATE OR DELETE ON public.substitution_sets
  FOR EACH ROW EXECUTE FUNCTION public.substitution_sets_guard();

-- the replacement candidates of ONE set version, in order — immutable rows
CREATE TABLE IF NOT EXISTS public.substitution_set_candidates (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  uuid NOT NULL REFERENCES public.organizations(id),
  set_id                  uuid NOT NULL REFERENCES public.substitution_sets(id),
  rank                    integer NOT NULL,
  replacement_security_id uuid NOT NULL REFERENCES portfolio.securities_global(id),
  reason                  text NOT NULL,
  evidence                text NOT NULL,
  source                  text NOT NULL DEFAULT 'adviser',
  created_at              timestamptz NOT NULL DEFAULT now(),
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  system_from             timestamptz NOT NULL DEFAULT now(),
  system_to               timestamptz,
  CONSTRAINT substitution_set_candidates_rank_chk CHECK (rank >= 1),
  CONSTRAINT substitution_set_candidates_text_chk CHECK (length(btrim(reason)) > 0 AND length(btrim(evidence)) > 0),
  -- a model alternate is only a STARTING POINT: it becomes a candidate with a reason and an approval
  CONSTRAINT substitution_set_candidates_source_chk CHECK (source IN ('adviser', 'model_alternate'))
);
CREATE UNIQUE INDEX IF NOT EXISTS substitution_set_candidates_rank_uq
  ON public.substitution_set_candidates (set_id, rank) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS substitution_set_candidates_security_uq
  ON public.substitution_set_candidates (set_id, replacement_security_id) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.rep6_immutable_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows are never deleted', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  -- the only change ever allowed: closing a live row on the valid axis
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% rows are only ever closed (valid_to), never edited', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS substitution_set_candidates_guard_trg ON public.substitution_set_candidates;
CREATE TRIGGER substitution_set_candidates_guard_trg BEFORE UPDATE OR DELETE ON public.substitution_set_candidates
  FOR EACH ROW EXECUTE FUNCTION public.rep6_immutable_guard();

-- ── 4. Substitution denials (substantially identical — NEVER pair) ─────────
-- A security and itself is the rule in code (always identical), never a row.
CREATE TABLE IF NOT EXISTS public.substitution_denials (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  security_a_id      uuid NOT NULL REFERENCES portfolio.securities_global(id),
  security_b_id      uuid NOT NULL REFERENCES portfolio.securities_global(id),
  rule               text NOT NULL,
  reason             text NOT NULL,
  evidence           text NOT NULL,
  status             text NOT NULL,
  maker_kind         text NOT NULL,
  maker_id           uuid REFERENCES public.users(id),
  checker_id         uuid REFERENCES public.users(id),
  approval_task_id   uuid REFERENCES public.workflow_run_steps(id),
  decided_at         timestamptz,
  decision_note      text,
  retire_reason      text,
  archived_from_id   uuid REFERENCES public.substitution_denials(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  -- stored once per pair, in id order: (a, b) and (b, a) are the same denial
  CONSTRAINT substitution_denials_pair_chk CHECK (security_a_id < security_b_id),
  CONSTRAINT substitution_denials_rule_chk CHECK (rule IN ('same_index', 'share_class', 'adviser')),
  CONSTRAINT substitution_denials_text_chk CHECK (length(btrim(reason)) > 0 AND length(btrim(evidence)) > 0),
  CONSTRAINT substitution_denials_status_chk CHECK (status IN ('pending_approval', 'active', 'rejected', 'retired')),
  -- a rule-seeded denial is the system's (protective, active at once); an adviser's needs a second person
  CONSTRAINT substitution_denials_maker_chk CHECK ((maker_kind = 'system' AND maker_id IS NULL AND rule <> 'adviser')
                                                   OR (maker_kind = 'user' AND maker_id IS NOT NULL)),
  CONSTRAINT substitution_denials_maker_checker_chk CHECK (checker_id IS NULL OR maker_id IS NULL
                                                           OR checker_id <> maker_id),
  CONSTRAINT substitution_denials_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL
                                                     OR system_to IS NOT NULL),
  CONSTRAINT substitution_denials_user_active_chk CHECK (status <> 'active' OR maker_kind = 'system'
                                                         OR checker_id IS NOT NULL),
  CONSTRAINT substitution_denials_rejected_chk CHECK (status <> 'rejected'
                                                      OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT substitution_denials_retired_chk CHECK (status <> 'retired'
                                                     OR (checker_id IS NOT NULL
                                                         AND length(btrim(COALESCE(retire_reason, ''))) > 0)),
  CONSTRAINT substitution_denials_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS substitution_denials_live_uq
  ON public.substitution_denials (org_id, security_a_id, security_b_id)
  WHERE status IN ('pending_approval', 'active') AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS substitution_denials_task_uq
  ON public.substitution_denials (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.substitution_denials_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  decision text[] := ARRAY['status', 'checker_id', 'approval_task_id', 'decided_at', 'decision_note',
    'retire_reason', 'valid_to', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'substitution denials are never deleted (retire one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived denial state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live denial row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('rejected', 'retired') THEN
    RAISE EXCEPTION 'a % denial is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
    RAISE EXCEPTION 'a denial pair and its reason never change' USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'pending_approval>active', 'pending_approval>rejected', 'active>retired'])) THEN
    RAISE EXCEPTION 'illegal denial transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS substitution_denials_guard_trg ON public.substitution_denials;
CREATE TRIGGER substitution_denials_guard_trg BEFORE UPDATE OR DELETE ON public.substitution_denials
  FOR EACH ROW EXECUTE FUNCTION public.substitution_denials_guard();

-- ── 5. Declared outside trades (accounts outside the platform) ─────────────
CREATE TABLE IF NOT EXISTS public.declared_outside_trades (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  subject_type         text NOT NULL,
  subject_id           uuid NOT NULL,
  account_description  text NOT NULL,
  account_tax_class    text NOT NULL DEFAULT 'unknown',
  global_security_id   uuid NOT NULL REFERENCES portfolio.securities_global(id),
  side                 text NOT NULL,
  trade_date           date NOT NULL,
  quantity             numeric(28, 8),
  loss                 text,
  realized_amount      numeric(20, 2),
  term                 text,
  declared_by          uuid NOT NULL REFERENCES public.users(id),
  source_note          text NOT NULL,
  replaces_id          uuid REFERENCES public.declared_outside_trades(id),
  withdraw_reason      text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT declared_outside_trades_subject_chk CHECK (subject_type IN ('household', 'entity')),
  CONSTRAINT declared_outside_trades_class_chk CHECK (account_tax_class IN ('taxable', 'tax_advantaged', 'unknown')),
  CONSTRAINT declared_outside_trades_side_chk CHECK (side IN ('buy', 'sell')),
  CONSTRAINT declared_outside_trades_qty_chk CHECK (quantity IS NULL OR quantity > 0),
  -- loss yes / no / unknown is a SALE's; a buy has none
  CONSTRAINT declared_outside_trades_loss_chk CHECK ((side = 'buy' AND loss IS NULL AND realized_amount IS NULL
                                                      AND term IS NULL)
                                                     OR (side = 'sell' AND loss IN ('yes', 'no', 'unknown'))),
  CONSTRAINT declared_outside_trades_amount_chk CHECK (realized_amount IS NULL
                                                       OR (term IN ('short', 'long')
                                                           AND ((loss = 'yes' AND realized_amount < 0)
                                                                OR (loss = 'no' AND realized_amount >= 0)))),
  CONSTRAINT declared_outside_trades_term_chk CHECK (term IS NULL OR term IN ('short', 'long')),
  CONSTRAINT declared_outside_trades_text_chk CHECK (length(btrim(account_description)) > 0
                                                     AND length(btrim(source_note)) > 0)
);
CREATE INDEX IF NOT EXISTS declared_outside_trades_subject_idx
  ON public.declared_outside_trades (org_id, subject_type, subject_id, trade_date) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.declared_outside_trades_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'declared outside trades are never deleted (withdraw one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to', 'withdraw_reason']) = (to_jsonb(OLD) - ARRAY['valid_to', 'withdraw_reason']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a declared outside trade is only ever closed (corrected or withdrawn), never edited'
    USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS declared_outside_trades_guard_trg ON public.declared_outside_trades;
CREATE TRIGGER declared_outside_trades_guard_trg BEFORE UPDATE OR DELETE ON public.declared_outside_trades
  FOR EACH ROW EXECUTE FUNCTION public.declared_outside_trades_guard();

-- ── 6. Loss bank (realized gains and losses, one row per source) ───────────
CREATE TABLE IF NOT EXISTS public.loss_bank_entries (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  subject_type        text NOT NULL,
  subject_id          uuid NOT NULL,
  tax_year            integer NOT NULL,
  account_id          uuid REFERENCES public.accounts(id),
  trade_date          date NOT NULL,
  character           text NOT NULL,
  amount              numeric(20, 2) NOT NULL,
  source              text NOT NULL,
  source_id           uuid NOT NULL,
  source_key          text NOT NULL,
  tax_lot_id          uuid,
  global_security_id  uuid REFERENCES portfolio.securities_global(id),
  detail              jsonb NOT NULL DEFAULT '{}'::jsonb,
  superseded_reason   text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT loss_bank_entries_subject_chk CHECK (subject_type IN ('household', 'entity')),
  CONSTRAINT loss_bank_entries_year_chk CHECK (tax_year BETWEEN 2000 AND 2100),
  CONSTRAINT loss_bank_entries_character_chk CHECK (character IN ('short', 'long')),
  CONSTRAINT loss_bank_entries_source_chk CHECK (source IN ('lot_close', 'fill', 'carryforward', 'declared_outside')),
  -- a carryforward is a LOSS carried in; its sign is fixed
  CONSTRAINT loss_bank_entries_cf_sign_chk CHECK (source <> 'carryforward' OR amount <= 0),
  CONSTRAINT loss_bank_entries_detail_chk CHECK (jsonb_typeof(detail) = 'object'),
  CONSTRAINT loss_bank_entries_closed_chk CHECK (valid_to IS NULL OR length(btrim(COALESCE(superseded_reason, ''))) > 0)
);
-- ONE row per source key, closed rows included: a re-run never re-adds a superseded entry
CREATE UNIQUE INDEX IF NOT EXISTS loss_bank_entries_source_uq
  ON public.loss_bank_entries (org_id, source, source_key) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS loss_bank_entries_subject_idx
  ON public.loss_bank_entries (org_id, subject_type, subject_id, tax_year) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS loss_bank_entries_lot_idx
  ON public.loss_bank_entries (tax_lot_id) WHERE tax_lot_id IS NOT NULL AND valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.loss_bank_entries_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'loss bank entries are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to', 'superseded_reason'])
         = (to_jsonb(OLD) - ARRAY['valid_to', 'superseded_reason']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a loss bank entry is only ever closed (superseded), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS loss_bank_entries_guard_trg ON public.loss_bank_entries;
CREATE TRIGGER loss_bank_entries_guard_trg BEFORE UPDATE OR DELETE ON public.loss_bank_entries
  FOR EACH ROW EXECUTE FUNCTION public.loss_bank_entries_guard();

-- ── 7. Harvest runs and candidates ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.harvest_runs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  account_id       uuid NOT NULL REFERENCES public.accounts(id),
  run_date         date NOT NULL,
  engine           text NOT NULL,
  outcome          text NOT NULL,
  reason           text NOT NULL,
  settings         jsonb NOT NULL DEFAULT '{}'::jsonb,
  tax_profile_id   uuid REFERENCES public.tax_profiles(id),
  rates            jsonb NOT NULL DEFAULT '{}'::jsonb,
  valuation_as_of  date,
  proposal_id      uuid REFERENCES public.rebalance_proposals(id),
  candidates       integer NOT NULL DEFAULT 0,
  taken            integer NOT NULL DEFAULT 0,
  created_by       text NOT NULL DEFAULT 'scheduler',
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT harvest_runs_engine_chk CHECK (engine IN ('hollisworks', 'custodian')),
  CONSTRAINT harvest_runs_outcome_chk CHECK (outcome IN ('proposed', 'nothing_to_harvest', 'skipped', 'failed')),
  CONSTRAINT harvest_runs_proposed_chk CHECK ((outcome = 'proposed') = (proposal_id IS NOT NULL)),
  CONSTRAINT harvest_runs_custodian_chk CHECK (engine = 'hollisworks' OR proposal_id IS NULL),
  CONSTRAINT harvest_runs_counts_chk CHECK (candidates >= 0 AND taken >= 0 AND taken <= candidates),
  CONSTRAINT harvest_runs_json_chk CHECK (jsonb_typeof(settings) = 'object' AND jsonb_typeof(rates) = 'object'),
  CONSTRAINT harvest_runs_reason_chk CHECK (length(btrim(reason)) > 0)
);
-- ONE run (so at most one harvest proposal) per account per day
CREATE UNIQUE INDEX IF NOT EXISTS harvest_runs_day_uq
  ON public.harvest_runs (account_id, run_date) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE TABLE IF NOT EXISTS public.harvest_candidates (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES public.organizations(id),
  run_id                   uuid NOT NULL REFERENCES public.harvest_runs(id),
  account_id               uuid NOT NULL REFERENCES public.accounts(id),
  kind                     text NOT NULL DEFAULT 'harvest',
  tax_lot_id               uuid,
  global_security_id       uuid NOT NULL REFERENCES portfolio.securities_global(id),
  quantity                 numeric(28, 8),
  unit_cost                numeric(28, 8),
  price                    numeric(28, 8),
  price_as_of              date,
  price_source             text NOT NULL,
  cost_basis               numeric(28, 8),
  market_value             numeric(28, 8),
  unrealized               numeric(28, 8),
  trading_cost             numeric(28, 8),
  net_loss                 numeric(28, 8),
  loss_pct                 numeric(14, 10),
  term                     text,
  threshold_pct            numeric(14, 10),
  threshold_amount         numeric(20, 2),
  passes_pct               boolean,
  passes_amount            boolean,
  rate_used                numeric(8, 6),
  est_tax_value            numeric(20, 2),
  value_note               text NOT NULL,
  replacement_security_id  uuid REFERENCES portfolio.securities_global(id),
  replacement_note         text,
  wash_sale                jsonb NOT NULL DEFAULT '{}'::jsonb,
  outcome                  text NOT NULL,
  skip_reason              text,
  skip_detail              text,
  proposal_id              uuid REFERENCES public.rebalance_proposals(id),
  original_candidate_id    uuid REFERENCES public.harvest_candidates(id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT harvest_candidates_kind_chk CHECK (kind IN ('harvest', 'swap_back')),
  CONSTRAINT harvest_candidates_term_chk CHECK (term IS NULL OR term IN ('short', 'long', 'unknown')),
  CONSTRAINT harvest_candidates_price_chk CHECK (price_source IN ('account_position', 'none')
                                                 AND ((price_source = 'none') = (price IS NULL))),
  CONSTRAINT harvest_candidates_outcome_chk CHECK (outcome IN ('proposed', 'skipped')),
  CONSTRAINT harvest_candidates_skip_chk CHECK ((outcome = 'skipped') = (skip_reason IS NOT NULL)),
  CONSTRAINT harvest_candidates_proposed_chk CHECK (outcome <> 'proposed' OR proposal_id IS NOT NULL),
  CONSTRAINT harvest_candidates_swap_chk CHECK (kind = 'harvest' OR original_candidate_id IS NOT NULL),
  -- every tax figure is an estimate, and says so (or says why there is none)
  CONSTRAINT harvest_candidates_value_chk CHECK (length(btrim(value_note)) > 0
                                                 AND (est_tax_value IS NULL OR rate_used IS NOT NULL)),
  CONSTRAINT harvest_candidates_wash_chk CHECK (jsonb_typeof(wash_sale) = 'object')
);
ALTER TABLE public.harvest_candidates DROP CONSTRAINT IF EXISTS harvest_candidates_reason_chk;
ALTER TABLE public.harvest_candidates ADD CONSTRAINT harvest_candidates_reason_chk CHECK (skip_reason IS NULL OR skip_reason IN (
  'below_threshold', 'needs_price', 'no_cost_basis', 'recent_household_buy', 'no_clear_replacement',
  'open_proposal', 'proposal_failed', 'swap_back_not_clear', 'swap_back_gain_budget', 'nothing_to_swap'));
CREATE INDEX IF NOT EXISTS harvest_candidates_run_idx ON public.harvest_candidates (run_id);
CREATE INDEX IF NOT EXISTS harvest_candidates_account_idx
  ON public.harvest_candidates (account_id, created_at DESC) WHERE valid_to IS NULL AND system_to IS NULL;

DROP TRIGGER IF EXISTS harvest_runs_guard_trg ON public.harvest_runs;
CREATE TRIGGER harvest_runs_guard_trg BEFORE UPDATE OR DELETE ON public.harvest_runs
  FOR EACH ROW EXECUTE FUNCTION public.rep6_immutable_guard();
DROP TRIGGER IF EXISTS harvest_candidates_guard_trg ON public.harvest_candidates;
CREATE TRIGGER harvest_candidates_guard_trg BEFORE UPDATE OR DELETE ON public.harvest_candidates
  FOR EACH ROW EXECUTE FUNCTION public.rep6_immutable_guard();

-- ── 8. rep5: the harvest trigger, wash-sale results on trades, harvest settings ──
ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_trigger_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_trigger_chk
  CHECK (trigger IN ('join', 'model_change', 'drift', 'cash_flow', 'manual', 'harvest'));

ALTER TABLE public.rebalance_proposal_trades ADD COLUMN IF NOT EXISTS wash_sale jsonb;
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_wash_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_wash_chk
  CHECK (wash_sale IS NULL OR (jsonb_typeof(wash_sale) = 'object'
                               AND wash_sale->>'outcome' IN ('clear', 'replaced', 'deferred', 'blocked')));
-- a deferred or blocked line never reaches an instruction
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_wash_route_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_wash_route_chk
  CHECK (instruction_id IS NULL OR wash_sale IS NULL OR wash_sale->>'outcome' IN ('clear', 'replaced'));

ALTER TABLE public.account_model_links ADD COLUMN IF NOT EXISTS harvest_loss_pct numeric(14, 10);
ALTER TABLE public.account_model_links ADD COLUMN IF NOT EXISTS harvest_loss_min numeric(20, 2);
ALTER TABLE public.account_model_links ADD COLUMN IF NOT EXISTS harvest_to_cash boolean;
ALTER TABLE public.account_model_links ADD COLUMN IF NOT EXISTS harvest_swap_back boolean;
ALTER TABLE public.account_model_links DROP CONSTRAINT IF EXISTS account_model_links_harvest_chk;
ALTER TABLE public.account_model_links ADD CONSTRAINT account_model_links_harvest_chk
  CHECK ((harvest_loss_pct IS NULL OR (harvest_loss_pct > 0 AND harvest_loss_pct < 1))
         AND (harvest_loss_min IS NULL OR harvest_loss_min >= 0));

-- ── 9. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['tax_profiles', 'loss_carryforwards', 'substitution_sets', 'substitution_set_candidates',
                           'substitution_denials', 'declared_outside_trades', 'loss_bank_entries', 'harvest_runs',
                           'harvest_candidates'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t,
                   pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;
