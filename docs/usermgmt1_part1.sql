-- USERMGMT1 part 1 — test-account flag + unverified contact email.
-- Additive only. Applied live via the Supabase MCP (2026-10-21).
--
-- users.is_test        verify fixtures; hidden from People & Access by default.
--                      Set by trigger for every '@test.local' address, so every
--                      verify script that seeds a fixture user (they all use
--                      that domain) is flagged with no per-script change, and
--                      by services.test_accounts.mark_test_user for any other.
-- users.contact_email  an address a PENDING person typed on the Access pending
--                      page. UNVERIFIED: shown to admins, never used for
--                      linking or provisioning (services.users never reads it).

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS is_test boolean NOT NULL DEFAULT false;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS contact_email text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS contact_email_at timestamptz;

CREATE OR REPLACE FUNCTION public.users_mark_test_fixture()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF NEW.email ILIKE '%@test.local' THEN
    NEW.is_test := true;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER users_mark_test_fixture
  BEFORE INSERT OR UPDATE OF email ON public.users
  FOR EACH ROW EXECUTE FUNCTION public.users_mark_test_fixture();

UPDATE public.users SET is_test = true WHERE email ILIKE '%@test.local' AND NOT is_test;
