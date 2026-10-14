-- notefix2.structural — Task 2: affiliated distribution agents.
--
-- distribution_participants has NO affiliation column today (confirmed live,
-- notefix2 Task 1b: the table has 0 rows) and participants.match_distribution
-- only annotates whatever role the MODEL already picked — it never corrects
-- a mislabeled issuer-affiliated agent (BofAS / MLPF&S / JPMS / MS & Co. /
-- GS&Co. / CGMI etc. read as a plain 'distribution_agent'). This adds the
-- column the rule in services/note_extraction/participants.py needs, and
-- seeds the real participants named in the F1 finding as REFERENCE DATA
-- (status 'active', not 'proposed' — a human has already confirmed these).
--
-- affiliated_issuer_group matches portfolio.structured_note_issuers.issuer_group
-- (confirmed live values: Bank of America, Barclays, Citigroup, Goldman Sachs,
-- JPMorgan, Morgan Stanley, UBS, ... ) — never the default-org id, never a CIK.

ALTER TABLE portfolio.distribution_participants
    ADD COLUMN IF NOT EXISTS affiliated_issuer_group text;

COMMENT ON COLUMN portfolio.distribution_participants.affiliated_issuer_group IS
    'Set only for participant_type = issuer_affiliate: the structured_note_issuers.issuer_group '
    'this broker-dealer is an affiliate of. A distribution member matching this participant on a '
    'note from the SAME issuer group resolves to role issuer_affiliated_agent regardless of the '
    'model''s own answer (services.note_extraction.participants.resolve_distribution_roles).';

INSERT INTO portfolio.distribution_participants
    (canonical_name, participant_type, aliases, affiliated_issuer_group, status, notes)
VALUES
    ('BofA Securities, Inc.', 'issuer_affiliate', ARRAY['BofAS', 'BofA Securities'],
     'Bank of America', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('Merrill Lynch, Pierce, Fenner & Smith Incorporated', 'issuer_affiliate', ARRAY['MLPF&S'],
     'Bank of America', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('J.P. Morgan Securities LLC', 'issuer_affiliate', ARRAY['JPMS', 'J.P. Morgan Securities'],
     'JPMorgan', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('Morgan Stanley & Co. LLC', 'issuer_affiliate', ARRAY['MS & Co.', 'Morgan Stanley & Co.'],
     'Morgan Stanley', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('Morgan Stanley Smith Barney LLC', 'issuer_affiliate',
     ARRAY['Morgan Stanley Wealth Management', 'MSSB'],
     'Morgan Stanley', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('Goldman Sachs & Co. LLC', 'issuer_affiliate', ARRAY['GS&Co.', 'Goldman Sachs & Co.'],
     'Goldman Sachs', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('Barclays Capital Inc.', 'issuer_affiliate', ARRAY[]::text[],
     'Barclays', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('UBS Securities LLC', 'issuer_affiliate', ARRAY[]::text[],
     'UBS', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('UBS Financial Services Inc.', 'issuer_affiliate', ARRAY[]::text[],
     'UBS', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer'),
    ('Citigroup Global Markets Inc.', 'issuer_affiliate', ARRAY['CGMI', 'Citigroup Global Markets'],
     'Citigroup', 'active', 'notefix2 F1 seed: confirmed issuer-affiliated broker-dealer')
ON CONFLICT (lower(canonical_name)) DO NOTHING;
