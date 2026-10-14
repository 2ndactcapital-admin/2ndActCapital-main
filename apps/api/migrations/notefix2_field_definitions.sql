-- notefix2.structural — Task 6: field definitions (F3/F4) the reader prompt
-- uses verbatim (schema.json_schema embeds portfolio.note_terms_field_registry
-- .description directly in the system message's JSON schema). DML only — no
-- DDL, so this runs as app_service (confirmed live: UPDATE privilege present),
-- unlike the two notefix2 migrations that needed the owner role.
--
-- F3 (buffer vs barrier): both descriptions now give a TEST the reader can
-- apply to filing wording it has never seen phrased as "threshold value" /
-- "trigger buffer level", rather than relying on the word "barrier" or
-- "buffer" literally appearing.
-- F4 (digital vs participation): both descriptions now say explicitly what
-- they are NOT, since the confusion observed live was a digital/fixed amount
-- landing in participation_rate or cap_pct.

UPDATE portfolio.note_terms_field_registry SET description =
    'Level (as % of initial) below which the investor bears the full decline from the initial level, '
    'from the first dollar. Use this even when the filing calls it a ''threshold value'', ''trigger '
    'value'', ''trigger buffer level'' or ''downside threshold''. Test: at a final level just below it, '
    'does the investor lose (initial - final)/initial? Then it is a barrier. ONLY for protection_type '
    '''barrier''. Never a buffer.'
WHERE field_key = 'barrier_pct';

UPDATE portfolio.note_terms_field_registry SET description =
    'The size of the decline (as %) the investor is protected from: losses equal only the decline '
    'BEYOND this amount. ''Buffer amount: 10%'' -> 10. Never the buffer rate or downside gearing '
    '(e.g. 111.11%). ONLY for protection_type ''buffer''. Never a barrier level.'
WHERE field_key = 'buffer_pct';

UPDATE portfolio.note_terms_field_registry SET description =
    'How principal is exposed at maturity. ''full'': repayment of principal at maturity does NOT '
    'depend on the underlying. ''floor'': a minimum repayment below 100% of principal. ''buffer'': '
    'losses start only AFTER the buffer amount - only the decline beyond it is lost (the amount goes '
    'in buffer_pct). ''barrier'': crossing (breaching) the level exposes the FULL decline from the '
    'initial level (the level goes in barrier_pct). ''none'': every percent of decline is lost. Buffer '
    'only when losses start after the buffer amount; barrier when crossing the level exposes the full '
    'decline.'
WHERE field_key = 'protection_type';

UPDATE portfolio.note_terms_field_registry SET description =
    'Upside participation in the underlying''s gain only. Never a digital or fixed percentage.'
WHERE field_key = 'participation_rate';

UPDATE portfolio.note_terms_field_registry SET description =
    'Maximum return when the upside is participation-based. Not a fixed premium or digital return.'
WHERE field_key = 'cap_pct';

UPDATE portfolio.note_terms_field_registry SET description =
    'A fixed amount per $1,000 paid when the underlying ends at or above a threshold (digital, '
    '''contingent digital return'', ''step'', fixed final-date premium). Never a participation or cap '
    'percentage.'
WHERE field_key = 'fixed_payout_amount';

UPDATE portfolio.note_terms_field_registry SET description =
    'That threshold, as a percent of the initial level, at or above which fixed_payout_amount is paid '
    '(digital / ''contingent digital return'' / ''step'' / fixed final-date premium).'
WHERE field_key = 'fixed_payout_threshold_pct';

UPDATE portfolio.note_terms_field_registry SET description =
    'EVERY selling agent, distributor, dealer or placement agent named in the (supplemental) plan of '
    'distribution, one entry each: the name exactly as stated, its role, and the fee it receives as a '
    'MIN and MAX percent of principal. Role ''issuer_affiliated_agent'' = the issuer''s own '
    'broker-dealer affiliate (the filing says ''an affiliate of ...'' or ''wholly owned subsidiary'') - '
    'use this even when the filing itself calls it the ''selling agent'' or ''distribution agent''; a '
    'party with no stated affiliation to the issuer is ''distribution_agent'' or ''dealer'' instead.'
WHERE field_key = 'distribution';
