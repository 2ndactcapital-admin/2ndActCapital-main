-- Sprint 16: Reference Data + CRM Entity Completeness
-- Run in Supabase SQL editor (Step 1 of standard sprint steps).

-- ============================================================
-- entities: name components + completeness fields
-- ============================================================
ALTER TABLE entities
  ADD COLUMN IF NOT EXISTS name_prefix text,
  ADD COLUMN IF NOT EXISTS first_name text,
  ADD COLUMN IF NOT EXISTS middle_name text,
  ADD COLUMN IF NOT EXISTS surname text,
  ADD COLUMN IF NOT EXISTS name_suffix text,
  ADD COLUMN IF NOT EXISTS legal_name_overridden boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS inception_date date,
  ADD COLUMN IF NOT EXISTS end_date date,
  ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS url text,
  ADD COLUMN IF NOT EXISTS country_code char(2),
  ADD COLUMN IF NOT EXISTS region_code text;

-- Migrate legacy date_of_birth → inception_date
UPDATE entities
SET inception_date = date_of_birth
WHERE date_of_birth IS NOT NULL AND inception_date IS NULL;

-- ============================================================
-- entity_addresses: phone, country_code, region_code, seasonal
-- ============================================================
ALTER TABLE entity_addresses
  ADD COLUMN IF NOT EXISTS phone text,
  ADD COLUMN IF NOT EXISTS country_code char(2),
  ADD COLUMN IF NOT EXISTS region_code text,
  ADD COLUMN IF NOT EXISTS is_seasonal boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS season_from_month integer,
  ADD COLUMN IF NOT EXISTS season_to_month integer;

-- ============================================================
-- reference_items
-- ============================================================
CREATE TABLE IF NOT EXISTS reference_items (
  id           uuid        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  list_key     text        NOT NULL,
  code         text        NOT NULL,
  label        text        NOT NULL,
  parent_code  text,
  display_order integer    NOT NULL DEFAULT 0,
  extra        jsonb,
  is_active    boolean     NOT NULL DEFAULT true,
  UNIQUE (list_key, code)
);

CREATE INDEX IF NOT EXISTS reference_items_list_key_idx
  ON reference_items (list_key, display_order, code)
  WHERE is_active = true;

-- ============================================================
-- Seed: months
-- ============================================================
INSERT INTO reference_items (list_key, code, label, display_order) VALUES
  ('month', '1',  'January',   1),
  ('month', '2',  'February',  2),
  ('month', '3',  'March',     3),
  ('month', '4',  'April',     4),
  ('month', '5',  'May',       5),
  ('month', '6',  'June',      6),
  ('month', '7',  'July',      7),
  ('month', '8',  'August',    8),
  ('month', '9',  'September', 9),
  ('month', '10', 'October',  10),
  ('month', '11', 'November', 11),
  ('month', '12', 'December', 12)
ON CONFLICT (list_key, code) DO NOTHING;

-- ============================================================
-- Seed: currencies
-- ============================================================
INSERT INTO reference_items (list_key, code, label, display_order) VALUES
  ('currency', 'USD', 'US Dollar',        1),
  ('currency', 'EUR', 'Euro',             2),
  ('currency', 'GBP', 'British Pound',    3),
  ('currency', 'CAD', 'Canadian Dollar',  4),
  ('currency', 'AUD', 'Australian Dollar',5),
  ('currency', 'CHF', 'Swiss Franc',      6),
  ('currency', 'JPY', 'Japanese Yen',     7)
ON CONFLICT (list_key, code) DO NOTHING;

-- ============================================================
-- Seed: countries  (extra.subdivision = label for region input)
-- ============================================================
INSERT INTO reference_items (list_key, code, label, display_order, extra) VALUES
  ('country', 'US', 'United States',    1,  '{"subdivision":"State"}'),
  ('country', 'CA', 'Canada',           2,  '{"subdivision":"Province"}'),
  ('country', 'GB', 'United Kingdom',   3,  '{"subdivision":"Region"}'),
  ('country', 'AU', 'Australia',        4,  '{"subdivision":"State"}'),
  ('country', 'NZ', 'New Zealand',      5,  NULL),
  ('country', 'IE', 'Ireland',          6,  NULL),
  ('country', 'DE', 'Germany',          7,  NULL),
  ('country', 'FR', 'France',           8,  NULL),
  ('country', 'CH', 'Switzerland',      9,  '{"subdivision":"Canton"}'),
  ('country', 'AT', 'Austria',         10,  NULL),
  ('country', 'NL', 'Netherlands',     11,  NULL),
  ('country', 'SE', 'Sweden',          12,  NULL),
  ('country', 'NO', 'Norway',          13,  NULL),
  ('country', 'DK', 'Denmark',         14,  NULL),
  ('country', 'SG', 'Singapore',       15,  NULL),
  ('country', 'HK', 'Hong Kong',       16,  NULL),
  ('country', 'JP', 'Japan',           17,  NULL),
  ('country', 'MX', 'Mexico',          18,  '{"subdivision":"State"}'),
  ('country', 'BR', 'Brazil',          19,  '{"subdivision":"State"}'),
  ('country', 'IN', 'India',           20,  '{"subdivision":"State"}')
ON CONFLICT (list_key, code) DO NOTHING;

-- ============================================================
-- Seed: US States (51 incl. DC)
-- ============================================================
INSERT INTO reference_items (list_key, code, label, parent_code, display_order) VALUES
  ('us_state', 'AL', 'Alabama',              'US',  1),
  ('us_state', 'AK', 'Alaska',               'US',  2),
  ('us_state', 'AZ', 'Arizona',              'US',  3),
  ('us_state', 'AR', 'Arkansas',             'US',  4),
  ('us_state', 'CA', 'California',           'US',  5),
  ('us_state', 'CO', 'Colorado',             'US',  6),
  ('us_state', 'CT', 'Connecticut',          'US',  7),
  ('us_state', 'DE', 'Delaware',             'US',  8),
  ('us_state', 'DC', 'District of Columbia', 'US',  9),
  ('us_state', 'FL', 'Florida',              'US', 10),
  ('us_state', 'GA', 'Georgia',              'US', 11),
  ('us_state', 'HI', 'Hawaii',               'US', 12),
  ('us_state', 'ID', 'Idaho',                'US', 13),
  ('us_state', 'IL', 'Illinois',             'US', 14),
  ('us_state', 'IN', 'Indiana',              'US', 15),
  ('us_state', 'IA', 'Iowa',                 'US', 16),
  ('us_state', 'KS', 'Kansas',               'US', 17),
  ('us_state', 'KY', 'Kentucky',             'US', 18),
  ('us_state', 'LA', 'Louisiana',            'US', 19),
  ('us_state', 'ME', 'Maine',                'US', 20),
  ('us_state', 'MD', 'Maryland',             'US', 21),
  ('us_state', 'MA', 'Massachusetts',        'US', 22),
  ('us_state', 'MI', 'Michigan',             'US', 23),
  ('us_state', 'MN', 'Minnesota',            'US', 24),
  ('us_state', 'MS', 'Mississippi',          'US', 25),
  ('us_state', 'MO', 'Missouri',             'US', 26),
  ('us_state', 'MT', 'Montana',              'US', 27),
  ('us_state', 'NE', 'Nebraska',             'US', 28),
  ('us_state', 'NV', 'Nevada',               'US', 29),
  ('us_state', 'NH', 'New Hampshire',        'US', 30),
  ('us_state', 'NJ', 'New Jersey',           'US', 31),
  ('us_state', 'NM', 'New Mexico',           'US', 32),
  ('us_state', 'NY', 'New York',             'US', 33),
  ('us_state', 'NC', 'North Carolina',       'US', 34),
  ('us_state', 'ND', 'North Dakota',         'US', 35),
  ('us_state', 'OH', 'Ohio',                 'US', 36),
  ('us_state', 'OK', 'Oklahoma',             'US', 37),
  ('us_state', 'OR', 'Oregon',               'US', 38),
  ('us_state', 'PA', 'Pennsylvania',         'US', 39),
  ('us_state', 'RI', 'Rhode Island',         'US', 40),
  ('us_state', 'SC', 'South Carolina',       'US', 41),
  ('us_state', 'SD', 'South Dakota',         'US', 42),
  ('us_state', 'TN', 'Tennessee',            'US', 43),
  ('us_state', 'TX', 'Texas',                'US', 44),
  ('us_state', 'UT', 'Utah',                 'US', 45),
  ('us_state', 'VT', 'Vermont',              'US', 46),
  ('us_state', 'VA', 'Virginia',             'US', 47),
  ('us_state', 'WA', 'Washington',           'US', 48),
  ('us_state', 'WV', 'West Virginia',        'US', 49),
  ('us_state', 'WI', 'Wisconsin',            'US', 50),
  ('us_state', 'WY', 'Wyoming',              'US', 51)
ON CONFLICT (list_key, code) DO NOTHING;

-- ============================================================
-- Seed: Canadian Provinces + Territories (13)
-- ============================================================
INSERT INTO reference_items (list_key, code, label, parent_code, display_order) VALUES
  ('ca_province', 'AB', 'Alberta',                   'CA',  1),
  ('ca_province', 'BC', 'British Columbia',          'CA',  2),
  ('ca_province', 'MB', 'Manitoba',                  'CA',  3),
  ('ca_province', 'NB', 'New Brunswick',             'CA',  4),
  ('ca_province', 'NL', 'Newfoundland and Labrador', 'CA',  5),
  ('ca_province', 'NS', 'Nova Scotia',               'CA',  6),
  ('ca_province', 'NT', 'Northwest Territories',     'CA',  7),
  ('ca_province', 'NU', 'Nunavut',                   'CA',  8),
  ('ca_province', 'ON', 'Ontario',                   'CA',  9),
  ('ca_province', 'PE', 'Prince Edward Island',      'CA', 10),
  ('ca_province', 'QC', 'Quebec',                    'CA', 11),
  ('ca_province', 'SK', 'Saskatchewan',              'CA', 12),
  ('ca_province', 'YT', 'Yukon',                     'CA', 13)
ON CONFLICT (list_key, code) DO NOTHING;
