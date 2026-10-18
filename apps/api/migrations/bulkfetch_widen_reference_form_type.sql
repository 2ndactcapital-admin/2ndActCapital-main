-- bulkfetch.structural — Task 5: a product supplement is often NOT a 424B2.
-- Programs file their product / underlying supplements under 424B2, 424B3 or
-- 424B5; the pricing supplements that reference them are 424B2. The
-- reference_filings form_type CHECK only allowed 424B2 / FWP, so a supplement
-- filed as 424B3/424B5 could not be stored at all. Widened, never narrowed.
-- Kept in its OWN file because it contains a DROP CONSTRAINT (the Supabase MCP
-- apply path cancels any migration containing DROP; split so the additive file
-- is never blocked by this one).
ALTER TABLE portfolio.reference_filings DROP CONSTRAINT IF EXISTS reference_filings_form_type_check;
ALTER TABLE portfolio.reference_filings ADD CONSTRAINT reference_filings_form_type_check
    CHECK (form_type = ANY (ARRAY['424B2'::text, 'FWP'::text, '424B3'::text, '424B5'::text]));
