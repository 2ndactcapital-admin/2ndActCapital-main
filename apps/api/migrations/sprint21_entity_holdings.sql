-- Sprint 21: Portfolio allocation lens — entity holdings snapshot table
-- Run in Supabase SQL editor before deploying Sprint 21.
--
-- org_id is a plain uuid (no FK to organizations) consistent with the rest
-- of the schema where existing tables omit the FK to organizations.

CREATE TABLE IF NOT EXISTS entity_holdings (
    id uuid NOT NULL DEFAULT uuid_generate_v4(),
    org_id uuid NOT NULL,
    entity_id uuid NOT NULL REFERENCES entities(id),
    taxonomy_key text NOT NULL,
    market_value numeric NOT NULL DEFAULT 0,
    currency_code text NOT NULL DEFAULT 'USD',
    as_of_date date NOT NULL,
    source text NOT NULL DEFAULT 'manual',
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS entity_holdings_entity_date
    ON entity_holdings (entity_id, taxonomy_key, as_of_date DESC);

CREATE INDEX IF NOT EXISTS entity_holdings_org_date
    ON entity_holdings (org_id, as_of_date DESC);

-- ── Demo seed data (idempotent) ──────────────────────────────────────────────
-- Seeds realistic holdings + targets for James Hargrove and the Hargrove
-- Family Trust using the live taxonomy keys already seeded in the config table.
-- Holdings cover 4 super-classes to produce on/under/over/off_plan states.

DO $$
DECLARE
    v_org  uuid  := '00000000-0000-0000-0000-000000000001';
    v_ent1 uuid  := '10000000-0000-0000-0000-000000000003'; -- James Hargrove
    v_ent2 uuid  := '10000000-0000-0000-0000-000000000001'; -- Hargrove Family Trust
    v_date date  := CURRENT_DATE;

    -- super-class keys (ordered by display_order, ascending)
    v_sc   text[];
    -- first major-class key under each super-class
    v_mc   text[];

    _sc    text;
    _mc    text;
    _idx   int;
BEGIN
    -- Fetch first 5 super-class keys
    SELECT array_agg(config_key ORDER BY display_order NULLS LAST, config_key)
    INTO v_sc
    FROM (
        SELECT config_key, display_order FROM config
        WHERE org_id = v_org AND category = 'asset_taxonomy'
          AND config_key ~ '^taxonomy_sc_\d+$'
          AND (is_active IS NULL OR is_active = true)
        ORDER BY display_order NULLS LAST, config_key
        LIMIT 5
    ) t;

    IF v_sc IS NULL OR array_length(v_sc, 1) = 0 THEN
        RAISE NOTICE 'taxonomy not seeded — skipping demo holdings';
        RETURN;
    END IF;

    -- For each super-class fetch its first major-class key
    v_mc := ARRAY[]::text[];
    FOR _idx IN 1 .. array_length(v_sc, 1) LOOP
        _sc := v_sc[_idx];
        -- Extract the sc number: taxonomy_sc_N → mc prefix taxonomy_mc_N_
        SELECT config_key INTO _mc
        FROM config
        WHERE org_id = v_org AND category = 'asset_taxonomy'
          AND config_key ~ ('^taxonomy_mc_' ||
              split_part(split_part(_sc, '_sc_', 2), '_', 1) || '_\d+$')
          AND (is_active IS NULL OR is_active = true)
        ORDER BY display_order NULLS LAST, config_key
        LIMIT 1;
        v_mc := v_mc || COALESCE(_mc, '');
    END LOOP;

    -- ── James Hargrove: $5.2 M portfolio spread across 3 super-classes ──────
    -- sc[1]: $2,000,000  → "on target" if target = 38.5%
    -- sc[2]: $1,500,000  → "under target" if target = 40% (actual ≈ 28.8%)
    -- sc[3]: $1,200,000  → "over target" if target = 10% (actual ≈ 23.1%)
    -- sc[4]: $500,000    → "off_plan" (no target)
    -- Total: $5,200,000

    -- Clear prior demo rows for this entity to make the block idempotent.
    DELETE FROM entity_holdings
    WHERE org_id = v_org AND entity_id = v_ent1 AND source = 'manual';

    DELETE FROM member_target_allocations
    WHERE org_id = v_org AND entity_id = v_ent1;

    -- Holdings at major-class level (most realistic source)
    IF array_length(v_sc, 1) >= 1 AND v_mc[1] <> '' THEN
        INSERT INTO entity_holdings
            (org_id, entity_id, taxonomy_key, market_value, currency_code, as_of_date, source)
        VALUES (v_org, v_ent1, v_mc[1], 2000000, 'USD', v_date, 'manual');

        INSERT INTO member_target_allocations
            (org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from, system_from)
        VALUES (v_org, v_ent1, v_sc[1], 'super', 38.5, now(), now());
    END IF;

    IF array_length(v_sc, 1) >= 2 AND v_mc[2] <> '' THEN
        INSERT INTO entity_holdings
            (org_id, entity_id, taxonomy_key, market_value, currency_code, as_of_date, source)
        VALUES (v_org, v_ent1, v_mc[2], 1500000, 'USD', v_date, 'manual');

        INSERT INTO member_target_allocations
            (org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from, system_from)
        VALUES (v_org, v_ent1, v_sc[2], 'super', 40.0, now(), now());
    END IF;

    IF array_length(v_sc, 1) >= 3 AND v_mc[3] <> '' THEN
        INSERT INTO entity_holdings
            (org_id, entity_id, taxonomy_key, market_value, currency_code, as_of_date, source)
        VALUES (v_org, v_ent1, v_mc[3], 1200000, 'USD', v_date, 'manual');

        INSERT INTO member_target_allocations
            (org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from, system_from)
        VALUES (v_org, v_ent1, v_sc[3], 'super', 10.0, now(), now());
    END IF;

    IF array_length(v_sc, 1) >= 4 AND v_mc[4] <> '' THEN
        -- Held but no target → off_plan state
        INSERT INTO entity_holdings
            (org_id, entity_id, taxonomy_key, market_value, currency_code, as_of_date, source)
        VALUES (v_org, v_ent1, v_mc[4], 500000, 'USD', v_date, 'manual');
    END IF;

    -- ── Hargrove Family Trust: $8 M, two super-classes with major-class targets ──
    DELETE FROM entity_holdings
    WHERE org_id = v_org AND entity_id = v_ent2 AND source = 'manual';

    DELETE FROM member_target_allocations
    WHERE org_id = v_org AND entity_id = v_ent2;

    IF array_length(v_sc, 1) >= 1 AND v_mc[1] <> '' THEN
        INSERT INTO entity_holdings
            (org_id, entity_id, taxonomy_key, market_value, currency_code, as_of_date, source)
        VALUES (v_org, v_ent2, v_mc[1], 5000000, 'USD', v_date, 'manual');

        INSERT INTO member_target_allocations
            (org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from, system_from)
        VALUES
            (v_org, v_ent2, v_sc[1], 'super',  62.5, now(), now()),
            (v_org, v_ent2, v_mc[1], 'major',  62.5, now(), now());
    END IF;

    IF array_length(v_sc, 1) >= 2 AND v_mc[2] <> '' THEN
        INSERT INTO entity_holdings
            (org_id, entity_id, taxonomy_key, market_value, currency_code, as_of_date, source)
        VALUES (v_org, v_ent2, v_mc[2], 3000000, 'USD', v_date, 'manual');

        INSERT INTO member_target_allocations
            (org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from, system_from)
        VALUES
            (v_org, v_ent2, v_sc[2], 'super',  50.0, now(), now()),
            (v_org, v_ent2, v_mc[2], 'major',  50.0, now(), now());
    END IF;

    RAISE NOTICE 'Sprint 21 demo data seeded successfully.';
END $$;
