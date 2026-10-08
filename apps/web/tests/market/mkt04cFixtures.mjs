/**
 * mkt04c fixtures shaped exactly like the backend's responses
 * (services/market_data/key_dates.py build_key_dates, saved_views.py
 * build_views, read_service.py build_correlations, each with the router's
 * permissions envelope). Names and messages are test data that appear nowhere
 * in the UI source, so a component can only show them by reading them.
 */
import { keyDatesBody } from "./fixtures.mjs";

const ENVELOPE = { can_read: true, can_write: true, is_super_admin: false, read_permission: "view_market_data", write_permission: null };

/** Key dates with a day entry, a month-precision range, a day range, and two personal dates. */
export function keyDates04cBody({ custom = true } = {}) {
  const b = keyDatesBody();
  b.key_dates = [
    { slug: "kd-old", name: "Key date before the data", kind: "kind_x", start_date: "1987-10-19", start_precision: "day", end_date: null, end_precision: null, source: "owner_list" },
    { slug: "kd-month-range", name: "Month range kd", kind: "kind_x", start_date: "2020-02-01", start_precision: "month", end_date: "2020-04-01", end_precision: "month", source: "owner_list" },
    { slug: "kd-one", name: "Key date one", kind: "kind_x", start_date: "2020-03-16", start_precision: "day", end_date: null, end_precision: null, source: "owner_list" },
    { slug: "kd-day-range", name: "Day range kd", kind: "kind_x", start_date: "2022-03-16", start_precision: "day", end_date: "2023-07-26", end_precision: "day", source: "owner_list" },
  ];
  b.custom_dates = custom
    ? [
        { id: "11111111-0000-0000-0000-000000000001", name: "Personal alpha", event_date: "2021-01-05", created_at: "2026-10-01T00:00:00+00:00", updated_at: "2026-10-01T00:00:00+00:00" },
        { id: "11111111-0000-0000-0000-000000000002", name: "Personal beta", event_date: "2024-07-09", created_at: "2026-10-01T00:00:00+00:00", updated_at: "2026-10-01T00:00:00+00:00" },
      ]
    : [];
  b.permissions = { ...ENVELOPE };
  return b;
}

export function viewConfig(overrides = {}) {
  return {
    v: 1,
    selection: [
      { kind: "indicator", key: "t.cat_alpha.1" },
      { kind: "indicator", key: "t.cat_beta.2" },
    ],
    anchor: { type: "date", value: "2020-02-29" },
    end: null,
    mode: "index",
    scale: "linear",
    overlays: { events: false, band: true, emphasis: false },
    ...overrides,
  };
}

/** A GET /market/views response: one preset, two user views. */
export function viewsBody({ canWrite = true } = {}) {
  const row = (id, name, config, unavailable = []) => ({
    id, name, config, created_at: "2026-10-01T00:00:00+00:00", updated_at: "2026-10-01T00:00:00+00:00", unavailable,
  });
  return {
    presets: [row("22222222-0000-0000-0000-000000000001", "Preset view zeta", viewConfig({ anchor: { type: "relative", years: 10 } }))],
    views: [
      row("33333333-0000-0000-0000-000000000001", "User view eta", viewConfig()),
      row(
        "33333333-0000-0000-0000-000000000002",
        "User view theta",
        viewConfig({
          selection: [
            { kind: "indicator", key: "t.cat_alpha.1" },
            { kind: "indicator", key: "t.retired.series" },
          ],
          mode: "level",
        }),
        ["t.retired.series"],
      ),
    ],
    vocabularies: {
      editable: [],
      inline_editable: [],
      modes: [],
      scales: [],
      anchor_types: [],
      selection_kinds: [],
    },
    limits: { views_max: 50, name_max: 77, selection_max: 40, config_max_bytes: 20000, relative_years: [1, 60] },
    permissions: { ...ENVELOPE, can_write: canWrite },
  };
}

/** A POST /market/correlations response, in the server's order (|r| desc, nulls last). */
export function correlationsBody(focus, rows) {
  return {
    focus_key: focus,
    window: { anchor: "2021-06-30", end: "2026-10-07" },
    lag_months: 0,
    effective_lag_months: 0,
    lag_convention: "Lag convention text from the server",
    min_periods: 24,
    results: rows.map(([series_key, r, n, frequency, unavailable_reason = null]) => ({
      series_key,
      r,
      n,
      frequency,
      overlap_from: "2021-07-31",
      overlap_to: "2026-09-30",
      unavailable_reason,
      change_method: { focus: "log", candidate: "log" },
      warnings: [],
    })),
    vocabularies: { editable: [], inline_editable: [] },
    permissions: { can_read: true, can_write: false, is_super_admin: false, read_permission: null, write_permission: null },
  };
}

/** Month-end chart periods from (year, month) for n months: [{date}]. */
export function monthEnds(year, month, n) {
  return Array.from({ length: n }, (_, i) => ({ date: new Date(Date.UTC(year, month - 1 + i + 1, 0)).toISOString().slice(0, 10) }));
}
