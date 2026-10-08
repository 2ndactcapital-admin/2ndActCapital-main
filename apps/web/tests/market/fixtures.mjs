/**
 * A catalog response shaped exactly like GET /api/v1/market/catalog
 * (services/market_data/read_service.py build_catalog + the router's
 * permissions envelope). The names, colours and keys are test data: the UI
 * code under test must render whatever it is given, so the fixture uses
 * values that appear nowhere in the UI source.
 */

export const LIMIT = 40;

export function catalogBody({ indicatorsPerCategory = 15 } = {}) {
  const categories = [
    { key: "cat_alpha", label: "Alpha group", color: "#123456", sort_order: 1 },
    { key: "cat_beta", label: "Beta group", color: "#654321", sort_order: 20 },
    { key: "cat_gamma", label: "Gamma group", color: "#0A0B0C", sort_order: 40 },
  ];
  const providers = ["prov_one", "prov_two"];
  const licenses = ["lic_open", "lic_restricted", "public_domain"];
  const indicators = [];
  categories.forEach((c, ci) => {
    for (let i = 0; i < indicatorsPerCategory; i += 1) {
      indicators.push({
        series_key: `t.${c.key}.${i}`,
        name: `${c.label} series ${i}`,
        category: c.label,
        category_key: c.key,
        region: "US",
        frequency: "monthly",
        units: "Index",
        seasonal_adjustment: "NSA",
        default_transform: "rebase_100",
        color: `#${String(ci)}${String(i).padStart(5, "0")}`,
        source_provider: providers[i % 2],
        license_class: licenses[i % 3],
        cost_tier: "free",
        first_observation_date: `${1950 + ci * 10 + i}-01-01`,
        last_observation_date: "2026-09-30",
        security_global_id: null,
        ingest_status: "active",
        sort_order: c.sort_order + i,
      });
    }
  });
  const securities = [
    { id: "aaaaaaaa-0000-0000-0000-000000000001", name: "Linked Index One", short_name: "LI1", security_type: "index", price_source: "indicator_series", series_key: "t.cat_alpha.0", selectable: true, unselectable_reason: null, color: "#1B2B4B" },
    { id: "aaaaaaaa-0000-0000-0000-000000000002", name: "Unlinked Index Two", short_name: "UI2", security_type: "index", price_source: "none", series_key: null, selectable: false, unselectable_reason: "no_price_history", color: "#2B3B5B" },
    { id: "bbbbbbbb-0000-0000-0000-000000000001", name: "Some Note A", short_name: "NA", security_type: "structured_note", price_source: "none", series_key: null, selectable: false, unselectable_reason: "no_price_history", color: "#C5A880" },
    { id: "bbbbbbbb-0000-0000-0000-000000000002", name: "Some Note B", short_name: "NB", security_type: "structured_note", price_source: "none", series_key: null, selectable: false, unselectable_reason: "no_price_history", color: "#C5A881" },
  ];
  return {
    categories,
    indicators,
    securities,
    vocabularies: {
      editable: [],
      inline_editable: [],
      modes: [
        { key: "index", label: "Mode label index" },
        { key: "sigma", label: "Mode label sigma" },
        { key: "level", label: "Mode label level" },
        { key: "default", label: "Mode label default" },
      ],
      transforms: [],
      frequencies: [],
      grid_frequencies: [
        { key: "daily", label: "Freq label daily" },
        { key: "weekly", label: "Freq label weekly" },
        { key: "monthly", label: "Freq label monthly" },
        { key: "quarterly", label: "Freq label quarterly" },
      ],
      limits: { max_keys: LIMIT, max_grid_rows: 2000 },
      license_classes: ["lic_open", "lic_restricted", "public_domain"],
      source_providers: providers,
    },
    permissions: {
      can_read: true,
      can_write: false,
      is_super_admin: false,
      read_permission: null,
      write_permission: null,
    },
  };
}

/** A POST /market/grid response for the given series keys. */
export function gridBody(keys, { rows, series } = {}) {
  return {
    anchor: "2021-10-07",
    end: "2026-10-07",
    mode: "default",
    frequency: "monthly",
    row_count: rows.length,
    series: keys.map((k) => ({
      series_key: k,
      name: k,
      floating: false,
      warnings: [],
      unavailable_reason: null,
      last_observation_date: "2026-09-30",
      ...(series?.[k] ?? {}),
    })),
    rows,
    vocabularies: { editable: [], inline_editable: [] },
    permissions: { can_read: true, can_write: false, is_super_admin: false, read_permission: null, write_permission: null },
  };
}
