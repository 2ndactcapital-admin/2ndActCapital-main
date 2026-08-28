-- Sprint fee33 — billing groups + membership. THE PART 1 SQL ITSELF.
--
-- ⚠ READ THIS BEFORE MERGING ⚠
-- ─────────────────────────────────────────────────────────────────────────────
-- The fee33 prompt states that Part 1 "is already applied by Joe directly via
-- Supabase MCP — confirm it live before writing any code, do not re-create it."
--
-- It was NOT applied. scripts/discover_fee33.py measures this directly: neither
-- table existed in any schema, no enum backed group_type, no table anywhere
-- matched %billing% or %fee%, and `grep -rn billing_group` over the repo hit
-- nothing but the prompt itself. The prompt does not carry the DDL text either,
-- so there was nothing to confirm and nothing to re-create.
--
-- This file is therefore SPRINT-AUTHORED, not Joe-authored. Every other Part 1
-- in this project was reviewed by a human before the sprint ran; this one was
-- not. Review the column shape here as a design decision, not as a transcript
-- of one already made.
--
-- The shape is derived from (a) fee33's own stated functional requirements and
-- (b) the deployed fee31/fee32 conventions introspected in Task 1 — never from
-- memory of what a billing group "usually" looks like.
--
--
-- WHY group_type IS A CHECK AND NOT AN ENUM
-- ─────────────────────────────────────────────────────────────────────────────
-- accounts.registration_type, accounts.tax_status, accounts.service_model and
-- account_owners.role are all `text`, and portfolio.positions' vocabularies are
-- CHECK-constrained text too. There is not one enum in the fee31/fee32 surface.
-- Matching that is worth more than the marginal type safety: fee34 will add
-- schedule scopes against this vocabulary, and widening a CHECK is a one-line
-- migration where adding an enum label is irreversible.
--
--
-- WHY household_id IS NULLABLE, AND WHY NOTHING AUTO-CREATES A GROUP
-- ─────────────────────────────────────────────────────────────────────────────
-- A billing group is the breakpoint aggregation unit; a household is a CRM
-- relationship. They diverge in exactly the cases that matter — a trust
-- reported with the family but billed standalone, two households sharing one
-- combined breakpoint. household_id here is an ADVISORY link for the admin UI's
-- convenience, never the source of membership. Membership lives in
-- billing_group_members and only there.
--
-- Task 1 also looked for an existing structure that would imply a sensible
-- auto-created default, and found the opposite. There are two household
-- groupings and services/households.py documents them as never-to-be-conflated:
-- household_memberships is many-to-many and OVERLAPS by design, while
-- entities.primary_household_id is at-most-one. Deriving a BREAKPOINT group
-- from the overlapping one would double-count an entity across two groups,
-- which is precisely the corruption a breakpoint tier cannot survive. So no
-- trigger, no cascade from create_household(), no backfill. A default group is
-- something an operator opts into per household, not something the schema does
-- behind their back.
--
--
-- WHY THE BREAKPOINT UNIQUENESS RULE IS *NOT* IN THIS FILE
-- ─────────────────────────────────────────────────────────────────────────────
-- "An account belongs to at most one ACTIVE BREAKPOINT group" cannot be a
-- partial unique index: the predicate depends on billing_groups.group_type,
-- a column on the OTHER table, and Postgres index predicates may only reference
-- the indexed table's own columns.
--
-- The three alternatives were considered and rejected:
--   * Denormalising group_type onto the member row would let the two copies
--     drift the moment a group's type is corrected, and the drifted copy is the
--     one the index would trust.
--   * A CHECK with a subquery is not supported.
--   * A constraint trigger would put a hard refusal on an ingestion path, which
--     fee32's RFC already settled against for exactly this table family.
-- It lives in services/billing_groups.py, raised as BreakpointOverlapError.
-- See that module's docstring for the concurrency caveat and its FOR UPDATE
-- lock, which is what makes the application-level rule hold under races.

BEGIN;

-- ── billing_groups ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.billing_groups (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES public.organizations (id),

    name            text NOT NULL,

    -- BREAKPOINT — the accounts whose values SUM to determine a fee tier. The
    --              one type an account may be in only once at a time, because
    --              being in two means its value is counted twice toward a tier.
    -- STATEMENT  — which accounts print on one statement. Deliberately
    --              unrestricted: a joint account legitimately appears on both
    --              spouses' statement groupings.
    -- PAYER      — which accounts a single payer settles. Also unrestricted;
    --              split-billing arrangements are real.
    group_type      text NOT NULL,

    -- ADVISORY ONLY. Never read to derive membership. NULL is a first-class,
    -- fully supported state — a billing group that spans two households, or
    -- belongs to none, is the case this table exists to represent.
    household_id    uuid REFERENCES public.households (id),

    notes           text,

    created_by      uuid REFERENCES public.users (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    -- Bi-temporal on both axes, matching public.accounts exactly. The VALID
    -- axis restates (a group renamed or retyped); the SYSTEM axis archives.
    valid_from      timestamptz NOT NULL DEFAULT now(),
    valid_to        timestamptz,
    system_from     timestamptz NOT NULL DEFAULT now(),
    system_to       timestamptz,

    CONSTRAINT billing_groups_group_type_check
        CHECK (group_type IN ('BREAKPOINT', 'STATEMENT', 'PAYER')),

    CONSTRAINT billing_groups_name_not_blank_check
        CHECK (btrim(name) <> '')
);

-- One active group per (org, type, name). Partial on system_to so archived
-- generations may repeat the pair freely — same shape as
-- accounts_active_identity_uq.
CREATE UNIQUE INDEX IF NOT EXISTS billing_groups_active_name_uq
    ON public.billing_groups (org_id, group_type, lower(btrim(name)))
    WHERE valid_to IS NULL AND system_to IS NULL;

CREATE INDEX IF NOT EXISTS billing_groups_org_idx
    ON public.billing_groups (org_id, group_type)
    WHERE valid_to IS NULL AND system_to IS NULL;

CREATE INDEX IF NOT EXISTS billing_groups_household_idx
    ON public.billing_groups (household_id)
    WHERE valid_to IS NULL AND system_to IS NULL;


-- ── billing_group_members ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.billing_group_members (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL REFERENCES public.organizations (id),

    billing_group_id   uuid NOT NULL REFERENCES public.billing_groups (id),
    account_id         uuid NOT NULL REFERENCES public.accounts (id),

    added_by           uuid REFERENCES public.users (id),
    created_at         timestamptz NOT NULL DEFAULT now(),

    -- Membership ends by CLOSING a row, never by deleting one. "This account
    -- was in the Smith breakpoint group for Q1 and moved out in Q2" is a fee
    -- input, not history nobody needs — a hard delete makes a past invoice
    -- unreproducible.
    valid_from         timestamptz NOT NULL DEFAULT now(),
    valid_to           timestamptz,
    system_from        timestamptz NOT NULL DEFAULT now(),
    system_to          timestamptz,

    -- The FKs are org-blind (they reference id alone), exactly as
    -- portfolio.positions.account_id is. A caller-supplied id from another
    -- tenant satisfies them. RLS's WITH CHECK on org_id is the real gate, and
    -- services/billing_groups.py additionally verifies BOTH referenced rows are
    -- this org's before inserting — fee32 learned that lesson on positions.
    CONSTRAINT billing_group_members_temporal_check
        CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- An account may appear in one group only once ACTIVELY. This is the part that
-- IS expressible as an index, because it references only this table's columns.
-- It does NOT implement the BREAKPOINT rule — that spans groups and depends on
-- billing_groups.group_type; see the header and services/billing_groups.py.
CREATE UNIQUE INDEX IF NOT EXISTS billing_group_members_active_uq
    ON public.billing_group_members (billing_group_id, account_id)
    WHERE valid_to IS NULL AND system_to IS NULL;

-- The BREAKPOINT overlap check's own query: "which active groups is this
-- account in?", answered without scanning closed memberships.
CREATE INDEX IF NOT EXISTS billing_group_members_account_active_idx
    ON public.billing_group_members (org_id, account_id)
    WHERE valid_to IS NULL AND system_to IS NULL;

CREATE INDEX IF NOT EXISTS billing_group_members_group_active_idx
    ON public.billing_group_members (billing_group_id)
    WHERE valid_to IS NULL AND system_to IS NULL;


-- ── RLS ──────────────────────────────────────────────────────────────────────
-- Introspected from the deployed accounts / account_owners / households
-- policies rather than written from memory. The NULLIF is load-bearing: on a
-- pooled backend a custom GUC reverts to '' rather than NULL, and a bare
-- ''::uuid cast raises instead of default-denying.

ALTER TABLE public.billing_groups ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS billing_groups_org_isolation ON public.billing_groups;

CREATE POLICY billing_groups_org_isolation
    ON public.billing_groups
    FOR ALL
    USING (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

ALTER TABLE public.billing_group_members ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS billing_group_members_org_isolation
    ON public.billing_group_members;

-- billing_group_members carries its OWN org_id rather than inheriting the
-- group's via a join, unlike household_memberships which carries none and has
-- no RLS at all. A join-based policy would make every membership read depend on
-- billing_groups also being visible, and "the parent row was filtered out" and
-- "there is no membership" would become the same observation.
CREATE POLICY billing_group_members_org_isolation
    ON public.billing_group_members
    FOR ALL
    USING (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

-- Without these the app connects as app_service and gets a bare
-- permission-denied that looks exactly like an RLS denial.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.billing_groups        TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.billing_group_members TO app_service;

COMMIT;
