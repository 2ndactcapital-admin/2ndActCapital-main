-- ─────────────────────────────────────────────────────────────────────────
-- Hollisworks Marketing — Part 1 SQL — contact-form submissions table.
-- ─────────────────────────────────────────────────────────────────────────
-- The public marketing page (bare hollisworks.com) has a "Start a conversation"
-- contact form. Submissions arrive PRE-TENANT: the writer is an anonymous
-- prospect on the platform's apex host, with no org context and no auth. So this
-- table has NO org_id — org scoping is not applicable to inbound marketing leads
-- (a prospect is not yet anyone's tenant).
--
-- RLS: the row is written pre-auth (like the tenant resolver / theme lookup),
-- under the same non-bypass window. We ENABLE RLS and add a single INSERT
-- carve-out gated to exactly that pre-auth window (no org context, not a super
-- admin) so a prospect can submit but nobody can read leads back through the
-- public surface. Reads are intentionally NOT granted to the pre-auth role —
-- lead review is a later, authenticated/admin concern, out of scope here.

CREATE TABLE IF NOT EXISTS public.marketing_contacts (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        text NOT NULL,
    firm        text NOT NULL,
    email       text NOT NULL,
    aum         text,
    note        text,
    source_host text,
    created_at  timestamp with time zone NOT NULL DEFAULT now()
);

ALTER TABLE public.marketing_contacts ENABLE ROW LEVEL SECURITY;

-- Pre-auth INSERT carve-out: a prospect submitting the marketing form has no
-- org context and is not a super admin — exactly the window the tenant resolver
-- and /theme/public already run in. Mirrors organizations_preauth_resolve.
DROP POLICY IF EXISTS marketing_contacts_preauth_insert ON public.marketing_contacts;
CREATE POLICY marketing_contacts_preauth_insert
    ON public.marketing_contacts
    FOR INSERT
    WITH CHECK (
        NULLIF(current_setting('app.current_org_id', true), '') IS NULL
        AND current_setting('app.is_super_admin', true) IS DISTINCT FROM 'true'
    );

-- Super admins may read/manage leads once authenticated (platform-ops concern).
DROP POLICY IF EXISTS marketing_contacts_super_all ON public.marketing_contacts;
CREATE POLICY marketing_contacts_super_all
    ON public.marketing_contacts
    FOR ALL
    USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

GRANT INSERT ON public.marketing_contacts TO app_service;
GRANT SELECT, UPDATE, DELETE ON public.marketing_contacts TO app_service;
