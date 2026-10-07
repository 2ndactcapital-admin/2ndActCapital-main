MKT03B — KEY DATES, REGIMES AND SAVED VIEWS: API AND SEEDS. 6 tasks + verification.

mkt01 to mkt03 (merged, verified) built the market data registry, adapters,
nightly refresh and the read API (catalog, series, grid, correlations). This
sprint adds what the chart UI needs around the data:
  - Reference data every user sees: 32 key dates (crises and turning points),
    16 regimes (recessions and Fed tightening cycles) and 4 starter views.
  - Per-user data: personal key dates ("My dates") and named saved views.
  - The API for all of it, plus a loader for the reference data.

OUT OF SCOPE (do not build): any UI or Next.js route (mkt04); organization-
shared views or dates; an admin screen for editing reference data; structured-
note price history; changes to the read API's endpoints or transforms.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- Part 1 is already applied and verified live (migration
  mkt03b_key_dates_regimes_views). Four new tables in schema market_data, all
  empty, all with RLS enabled, app_service has SELECT/INSERT/UPDATE/DELETE and
  anon/authenticated have nothing:
    key_dates      id, slug (unique), name, kind, start_date, start_precision
                   ('day'|'month'), end_date, end_precision, source
                   ('owner_list'|'suggested'), is_active, notes, created_at,
                   updated_at. kind is one of equity_crash, market_peak_trough,
                   banking_credit, sovereign_currency, rates_monetary,
                   geopolitical_trade, policy_regime. CHECKs: end_date >=
                   start_date; end_date and end_precision are both null or both
                   set; a month-precision start_date is the first of the month.
    regimes        id, regime_type ('recession'|'fed_tightening'), name,
                   start_date, end_date, source ('nber'|'fomc_curated'),
                   is_active, timestamps. UNIQUE (regime_type, start_date);
                   CHECK end_date >= start_date.
    user_key_dates id, org_id (FK organizations), user_id (FK users), name,
                   event_date, timestamps. CHECK name = btrim(name) and 1 to 60
                   characters. UNIQUE (org_id, user_id, lower(name),
                   event_date).
    saved_views    id, owner_scope ('platform'|'user'), org_id, user_id, name,
                   config jsonb, timestamps. CHECK: a platform row has NULL
                   org_id and user_id, a user row has both set. CHECK name =
                   btrim(name), 1 to 80 characters. CHECK config is a JSON
                   object of at most 20,000 bytes. UNIQUE (org_id, user_id,
                   lower(name)) for user rows; UNIQUE (lower(name)) for
                   platform rows.
- Policies: key_dates and regimes = global read, writes only when
  app.is_super_admin = 'true' (same as the other market_data reference tables).
  user_key_dates = ONE org-isolation policy for all commands, identical in
  shape to member_todos and user_notification_preferences. saved_views = read:
  platform rows for everyone plus the caller's org rows; insert/update/delete:
  only owner_scope 'user' rows of the caller's org (or super-admin).
- IMPORTANT, differs from docs/market_data/KEY_DATES_SPEC_V1.md section 3.4:
  the platform has exactly two RLS session settings, app.current_org_id and
  app.is_super_admin. There is NO current-user setting. The existing user-
  scoped tables enforce org isolation in RLS and filter by user_id in the
  service layer. This sprint follows that established pattern: every query on
  user_key_dates and saved_views filters by BOTH org_id and user_id taken from
  the verified session, with RLS as the org-level backstop. The spec's
  `visibility` column is deliberately not built; it can be added later.
  Do not add a new RLS setting; that is a platform-wide change.
- The mkt03 API conventions to mirror (confirm in Task 1): router
  apps/api/routers/market_data.py registered under /api/v1; the one gate
  require_market_data_read in services/market_data/access.py (valid session
  only, read_permission null in the envelope); the permissions and
  vocabularies envelope; error responses that never echo the request body.
- Baselines from the last live runs, do NOT chain them inside your verify
  script: verify_mkt01 57, verify_mkt02 52, verify_mkt02c 39, verify_mkt02c2
  16, verify_mkt03 68, all 0 failed. 69 active series. Data runs from 1970-01-02
  (S&P 500) to the latest trading day.

DECISIONS ALREADY MADE, do not relitigate:
- Custom dates and saved views are gated by the same single session-only gate.
  A caller manages only their own rows. No new permission is invented.
- Saved views store the RESOLVED anchor (a date, or "N years ago"), never a
  reference to a key date, so deleting a personal date never breaks a view.
- A view references its series by stable key (series_key for indicators, the
  securities_global id for securities). If a referenced series later stops
  being active, the view is returned unchanged with that selection flagged
  unavailable; nothing is silently removed.
- Platform preset views are seed-only: the API never edits or deletes them.
- Hard delete for both personal tables (they are bookmarks, not records).
- The Fed tightening dates in the seed are approximate and are to be verified
  against the FOMC record by the owner; the sprint seeds them as given and says
  so in the docs.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. If you have no database access in this
environment, say so explicitly and STOP.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- statement_cache_size=0 on EVERY asyncpg connection and pool. Schema-qualify
  everything (market_data.*, public.*).
- org_id and the user id come ONLY from the verified session, never from a body
  or path parameter. Request models use extra='forbid'; a body field named
  org_id or user_id is rejected with 422 and never echoed back.
- Personal-data endpoints run on the request connection with the caller's RLS
  context set the way existing user-scoped endpoints do. They must NOT use
  platform_scope(). Only the reference loader script uses platform_scope().
- No DDL. No writes to portfolio.*. No new RLS settings. No UI, no Next.js
  routes. Do not edit CLAUDE.md. Do not edit mkt01 to mkt03 modules except to
  register the new routes and to import helpers.
- Do NOT run the loader script or the verify script, and make no live external
  calls from this session. Write them and STOP; the operator runs them.
  Read-only database discovery is fine.
- NEVER print or log a secret. Errors returned to callers never echo the
  request body.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Query the live database and confirm CONFIRMED REAL FACTS: the four
      tables, columns, CHECK bodies, indexes, policies, grants, and that all
      four are empty. Report any difference and STOP if the live DDL differs.
  1b. Find how a route obtains the verified org id AND the verified user id from
      the session (the sibling of get_org_id), and how an existing user-scoped
      endpoint (member_todos or notification preferences) filters by user and
      sets the RLS context on the request connection. Mirror it exactly.
  1c. Find how earlier verify scripts create fixture organizations and users and
      how they prove RLS isolation under a non-bypassing connection. Reuse the
      existing helper if there is one; otherwise create tagged fixture rows and
      delete them in strict FK order.
  1d. Read the mkt03 router, access.py and envelope code and mirror them for the
      new routes. Report the real function names.
  1e. Find the repository helper that gives each series' first and last
      observation dates. Key-date and custom-date validation uses the earliest
      first date and the latest last date across ACTIVE series as "the available
      data range", read per request, never hardcoded.

=== TASK 2: REFERENCE LOADER AND SEED FILES ===
  2a. Write the three seed files below EXACTLY as given (valid JSON; the wrapper
      objects are described in each heading). Do not reorder, rename or "fix"
      anything.
  2b. Write apps/api/scripts/market_data_seed_reference.py with a `load`
      subcommand. It reads the three files and upserts inside platform_scope():
        - key_dates on slug, regimes on (regime_type, start_date), platform
          views on lower(name) with owner_scope 'platform' and NULL org_id and
          user_id.
        - On conflict update only the data fields and updated_at, and only when
          a value actually changed. Never delete a row absent from the seed.
          Never touch is_active or notes on an existing row.
        - Before loading a preset, check that every indicator key in its
          selection is an ACTIVE registry series and every security key is an
          existing selectable security. A preset with an unresolvable key is
          SKIPPED with a [FIND] naming the key; the rest still load.
        - Print counts inserted / updated / unchanged per table and any [FIND].
      Put the upsert logic in a service function that takes rows, so tests can
      call it with fixture rows without reading the real files.

SEED DATA (copy exactly):

docs/market_data/market_key_dates_v1.json  — {"version": 1, "key_dates": [ ...these 32 objects... ]}
  {"slug": "volcker-shock-1980", "name": "Volcker rate shock and bear market", "kind": "rates_monetary", "start_date": "1980-11-01", "start_precision": "month", "end_date": "1982-08-12", "end_precision": "day", "source": "owner_list"}
  {"slug": "latam-debt-crisis-1982", "name": "Latin American debt crisis", "kind": "sovereign_currency", "start_date": "1982-08-01", "start_precision": "month", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "black-monday-1987", "name": "Black Monday", "kind": "equity_crash", "start_date": "1987-10-19", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "sl-crisis-1989", "name": "Savings & Loan crisis (peak and bailout)", "kind": "banking_credit", "start_date": "1989-08-01", "start_precision": "month", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "japan-bubble-peak-1989", "name": "Japan bubble peak", "kind": "market_peak_trough", "start_date": "1989-12-29", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "iraq-kuwait-1990", "name": "Iraq invades Kuwait (Gulf War selloff)", "kind": "geopolitical_trade", "start_date": "1990-08-02", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "bond-massacre-1994", "name": "Bond market massacre", "kind": "rates_monetary", "start_date": "1994-02-04", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "mexican-peso-1994", "name": "Mexican peso crisis", "kind": "sovereign_currency", "start_date": "1994-12-20", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "asian-crisis-1997", "name": "Asian Financial Crisis", "kind": "sovereign_currency", "start_date": "1997-07-02", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "russia-ltcm-1998", "name": "Russian default and LTCM collapse", "kind": "sovereign_currency", "start_date": "1998-08-17", "start_precision": "day", "end_date": "1998-09-23", "end_precision": "day", "source": "owner_list"}
  {"slug": "dotcom-peak-2000", "name": "Dot-com peak", "kind": "market_peak_trough", "start_date": "2000-03-10", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "sept-11-2001", "name": "September 11 attacks", "kind": "geopolitical_trade", "start_date": "2001-09-11", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "enron-worldcom-2001", "name": "Enron and WorldCom collapses", "kind": "banking_credit", "start_date": "2001-12-02", "start_precision": "day", "end_date": "2002-07-21", "end_precision": "day", "source": "owner_list"}
  {"slug": "bear-stearns-2008", "name": "Bear Stearns rescue", "kind": "banking_credit", "start_date": "2008-03-16", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "lehman-2008", "name": "Lehman Brothers collapse", "kind": "banking_credit", "start_date": "2008-09-15", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "flash-crash-2010", "name": "Flash Crash", "kind": "equity_crash", "start_date": "2010-05-06", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "us-downgrade-eurozone-2011", "name": "U.S. credit downgrade and Eurozone crisis", "kind": "sovereign_currency", "start_date": "2011-08-05", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "china-crash-2015", "name": "China stock market crash", "kind": "equity_crash", "start_date": "2015-06-12", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "covid-crash-2020", "name": "COVID crash", "kind": "equity_crash", "start_date": "2020-02-19", "start_precision": "day", "end_date": "2020-03-23", "end_precision": "day", "source": "owner_list"}
  {"slug": "bear-market-2022", "name": "2022 bear market and Fed rate hikes", "kind": "rates_monetary", "start_date": "2022-01-03", "start_precision": "day", "end_date": "2022-10-12", "end_precision": "day", "source": "owner_list"}
  {"slug": "svb-2023", "name": "Silicon Valley Bank collapse", "kind": "banking_credit", "start_date": "2023-03-10", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "liberation-day-2025", "name": "\"Liberation Day\" tariff shock", "kind": "geopolitical_trade", "start_date": "2025-04-02", "start_precision": "day", "end_date": null, "end_precision": null, "source": "owner_list"}
  {"slug": "plaza-accord-1985", "name": "Plaza Accord (dollar devaluation agreed)", "kind": "policy_regime", "start_date": "1985-09-22", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "black-wednesday-1992", "name": "Black Wednesday (UK exits the ERM)", "kind": "sovereign_currency", "start_date": "1992-09-16", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "euro-launch-1999", "name": "Euro launch", "kind": "policy_regime", "start_date": "1999-01-01", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "dotcom-low-2002", "name": "Dot-com bear market low", "kind": "market_peak_trough", "start_date": "2002-10-09", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "bnp-paribas-2007", "name": "Subprime contagion (BNP Paribas freezes funds)", "kind": "banking_credit", "start_date": "2007-08-09", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "equity-peak-2007", "name": "Pre-crisis equity peak", "kind": "market_peak_trough", "start_date": "2007-10-09", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "qe1-2008", "name": "Fed announces QE1", "kind": "rates_monetary", "start_date": "2008-11-25", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "gfc-low-2009", "name": "Global financial crisis equity low", "kind": "market_peak_trough", "start_date": "2009-03-09", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "taper-tantrum-2013", "name": "Taper tantrum", "kind": "rates_monetary", "start_date": "2013-05-22", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}
  {"slug": "brexit-2016", "name": "Brexit vote", "kind": "geopolitical_trade", "start_date": "2016-06-23", "start_precision": "day", "end_date": null, "end_precision": null, "source": "suggested"}

docs/market_data/market_regimes_v1.json  — {"version": 1, "regimes": [ ...these 16 objects... ]}
  {"regime_type": "recession", "name": "US recession 1969-70", "start_date": "1969-12-01", "end_date": "1970-11-30", "source": "nber"}
  {"regime_type": "recession", "name": "US recession 1973-75", "start_date": "1973-11-01", "end_date": "1975-03-31", "source": "nber"}
  {"regime_type": "recession", "name": "US recession 1980", "start_date": "1980-01-01", "end_date": "1980-07-31", "source": "nber"}
  {"regime_type": "recession", "name": "US recession 1981-82", "start_date": "1981-07-01", "end_date": "1982-11-30", "source": "nber"}
  {"regime_type": "recession", "name": "US recession 1990-91", "start_date": "1990-07-01", "end_date": "1991-03-31", "source": "nber"}
  {"regime_type": "recession", "name": "US recession 2001", "start_date": "2001-03-01", "end_date": "2001-11-30", "source": "nber"}
  {"regime_type": "recession", "name": "US recession 2007-09", "start_date": "2007-12-01", "end_date": "2009-06-30", "source": "nber"}
  {"regime_type": "recession", "name": "US recession 2020", "start_date": "2020-02-01", "end_date": "2020-04-30", "source": "nber"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 1980-81", "start_date": "1980-01-01", "end_date": "1981-06-30", "source": "fomc_curated"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 1983-84", "start_date": "1983-03-01", "end_date": "1984-08-31", "source": "fomc_curated"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 1988-89", "start_date": "1988-03-01", "end_date": "1989-02-28", "source": "fomc_curated"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 1994-95", "start_date": "1994-02-01", "end_date": "1995-02-28", "source": "fomc_curated"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 1999-00", "start_date": "1999-06-01", "end_date": "2000-05-31", "source": "fomc_curated"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 2004-06", "start_date": "2004-06-01", "end_date": "2006-06-30", "source": "fomc_curated"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 2015-18", "start_date": "2015-12-01", "end_date": "2018-12-31", "source": "fomc_curated"}
  {"regime_type": "fed_tightening", "name": "Fed tightening 2022-23", "start_date": "2022-03-01", "end_date": "2023-07-31", "source": "fomc_curated"}

docs/market_data/market_view_presets_v1.json  — {"version": 1, "views": [ ...these 4 objects... ]}
  {"name": "Cross-asset overview", "config": {"v": 1, "selection": [{"kind": "indicator", "key": "fred.sp500"}, {"kind": "indicator", "key": "fred.nasdaq100"}, {"kind": "indicator", "key": "fred.payems"}, {"kind": "indicator", "key": "fred.cpiaucsl"}, {"kind": "indicator", "key": "fred.dcoilwtico"}, {"kind": "indicator", "key": "yahoo.gc_f"}, {"kind": "indicator", "key": "fred.csushpinsa"}, {"kind": "indicator", "key": "fred.dtwexbgs"}, {"kind": "indicator", "key": "fred.dgs10"}, {"kind": "indicator", "key": "fred.baa10y"}], "anchor": {"type": "date", "value": "1990-01-01"}, "end": null, "mode": "index", "scale": "log", "overlays": {"events": true, "band": true, "emphasis": true}}}
  {"name": "Inflation and rates since 2020", "config": {"v": 1, "selection": [{"kind": "indicator", "key": "fred.cpiaucsl"}, {"kind": "indicator", "key": "fred.pcepilfe"}, {"kind": "indicator", "key": "fred.dgs10"}, {"kind": "indicator", "key": "fred.t10y2y"}, {"kind": "indicator", "key": "fred.baa10y"}, {"kind": "indicator", "key": "yahoo.gc_f"}, {"kind": "indicator", "key": "fred.dcoilwtico"}, {"kind": "indicator", "key": "fred.dtwexbgs"}, {"kind": "indicator", "key": "fred.sp500"}], "anchor": {"type": "date", "value": "2020-01-01"}, "end": null, "mode": "sigma", "scale": "linear", "overlays": {"events": true, "band": true, "emphasis": true}}}
  {"name": "Growth vs equities", "config": {"v": 1, "selection": [{"kind": "indicator", "key": "fred.gdpc1"}, {"kind": "indicator", "key": "fred.payems"}, {"kind": "indicator", "key": "fred.unrate"}, {"kind": "indicator", "key": "fred.sp500"}, {"kind": "indicator", "key": "fred.nasdaq100"}, {"kind": "indicator", "key": "yahoo.dji"}], "anchor": {"type": "date", "value": "2007-10-01"}, "end": null, "mode": "index", "scale": "log", "overlays": {"events": true, "band": true, "emphasis": true}}}
  {"name": "Housing cycle", "config": {"v": 1, "selection": [{"kind": "indicator", "key": "fred.csushpinsa"}, {"kind": "indicator", "key": "fred.houst"}, {"kind": "indicator", "key": "fred.dgs10"}, {"kind": "indicator", "key": "fred.unrate"}, {"kind": "indicator", "key": "fred.cpiaucsl"}, {"kind": "indicator", "key": "fred.sp500"}], "anchor": {"type": "date", "value": "2003-01-01"}, "end": null, "mode": "sigma", "scale": "linear", "overlays": {"events": true, "band": true, "emphasis": true}}}

=== TASK 3: KEY DATES API ===
Routes (under the same /api/v1 prefix as the mkt03 routes):
  GET    /market/key-dates
  POST   /market/key-dates/custom      body {name, event_date}
  DELETE /market/key-dates/custom/{id}
  3a. GET returns: key_dates (active rows, sorted chronologically by start_date,
      each with slug, name, kind, start_date, start_precision, end_date,
      end_precision, source), custom_dates (ONLY the caller's own, org_id and
      user_id both matched, sorted by event_date), regimes (active, sorted by
      start_date), the data range (first and last date), permissions
      (can_read true, can_write true meaning "own rows", is_super_admin,
      read_permission null, write_permission null), and vocabularies: kinds and
      regime types with labels from the server (equity_crash "Equity crash",
      market_peak_trough "Market peak or trough", banking_credit "Banking or
      credit crisis", sovereign_currency "Sovereign or currency crisis",
      rates_monetary "Rates and monetary policy", geopolitical_trade
      "Geopolitical or trade", policy_regime "Policy regime"; recession
      "Recession", fed_tightening "Fed tightening"), and the limits
      (custom_dates_max 50, name_max 60). custom_dates is an empty array, never
      omitted, for a caller with none.
  3b. POST validates server-side and answers with these exact messages (422
      unless stated): name trimmed; empty or whitespace-only -> "Enter a name
      for this date."; longer than 60 -> "Names can be up to 60 characters.";
      any control character -> "Names cannot contain control characters.";
      missing or malformed date -> "Pick a date."; a date before the first or
      after the last available data month -> "That date is outside the
      available data (<Mon YYYY> to <Mon YYYY>)." using the real range; same
      name (case-insensitive) on the same date for this user -> 409 "You
      already saved that name on that date."; the 51st date -> 422 "You can save
      up to 50 dates. Delete one first." The saved row stores the trimmed name.
      A body containing org_id or user_id is rejected (422) and nothing is
      stored or echoed.
  3c. DELETE removes the caller's own row only. A row that does not exist, or
      belongs to another user in the same org, or to another org, answers 404
      with no hint which, and leaves the row untouched. Success answers 200
      with {"deleted": true}.

=== TASK 4: SAVED VIEWS API ===
Routes:
  GET    /market/views
  POST   /market/views                 body {name, config}
  PUT    /market/views/{id}            body {name?, config?} (at least one)
  DELETE /market/views/{id}
  4a. CONFIG SCHEMA (canonical, extra='forbid' at every level):
        {"v": 1,
         "selection": [{"kind": "indicator"|"security", "key": "<series_key or
                        securities_global id>"}, ...],
         "anchor": {"type": "date", "value": "YYYY-MM-DD"}
                   | {"type": "relative", "years": N},
         "end": null | the same shape as anchor,
         "mode": "index"|"sigma"|"default"|"level",
         "scale": "log"|"linear",
         "overlays": {"events": bool, "band": bool, "emphasis": bool}}
      Rules: 1 to 40 selections, no duplicates; every indicator key must be an
      ACTIVE registry series and every security key a securities_global row the
      catalog marks selectable, otherwise 422 naming ONLY the offending keys;
      relative years 1 to 60; dates must parse; when both are dates the anchor
      must not be after the end; unknown fields anywhere are rejected.
  4b. GET returns {presets, views, permissions, vocabularies, limits}: presets
      are the platform rows, views are ONLY the caller's own rows (org_id and
      user_id both matched), each as {id, name, config, created_at, updated_at,
      unavailable: [keys]}. A selection whose series is no longer active is
      listed in unavailable and left in the config unchanged; the view itself is
      never modified by a read. Vocabularies: modes and scales with labels from
      the server. Limits: views_max 50, name_max 80.
  4c. POST: name trimmed, 1 to 80 characters ("Enter a name for this view." /
      "Names can be up to 80 characters."); same name for this user,
      case-insensitive -> 409 "You already have a view with that name."; the
      51st -> 422 "You can save up to 50 views. Delete one first."; config
      validated per 4a; returns the saved row.
  4d. PUT updates the caller's own view only; renaming onto another of the
      caller's names is 409; an id that is a platform preset, another user's, or
      another org's, or does not exist, answers 404 and changes nothing.
  4e. DELETE removes the caller's own view only, same 404 rule, 200
      {"deleted": true}. Platform presets can never be changed through the API.

=== TASK 5: REAL PROOF (written into the verify script, NOT executed by you) ===
Phase A is self-contained and always runs. It creates fixture orgs and users
(per 1c), tagged so teardown can find them; loader tests use fixture rows with
slugs and names starting 'verify.mkt03b.' and never the seed files. Views in
tests reference real ACTIVE series keys read-only; the one "no longer active"
case uses a fixture series created for the test and then set to 'deferred'.
  - Schema behavior: each CHECK refuses what it should and admits what it
    should: key_dates (month precision not on the 1st; end before start;
    end_date without end_precision; bad kind), regimes (end before start;
    duplicate type and start), user_key_dates (untrimmed, empty, 61 characters;
    a duplicate that differs only in letter case), saved_views (platform row
    with an org_id; user row without a user_id; 81-character name; config that
    is not an object; config over 20,000 bytes; case-insensitive duplicate name
    per user; case-insensitive duplicate platform name).
  - Gates on the IDENTICAL request, both ways, under a connection with
    rolbypassrls = false (asserted as its own check): key_dates and regimes
    inserts are refused without platform scope and admitted with it; a platform
    preset insert is refused under an org context and admitted under platform
    scope; an org context cannot update or delete a preset (zero rows, re-read
    unchanged).
  - Org isolation, both directions on the same test: org A cannot read, insert
    on behalf of, update or delete org B's custom dates and views, and org B
    cannot touch org A's. Insert with a mismatched org_id is refused by the RLS
    check.
  - User isolation inside one org (service layer): user A's custom dates and
    views never appear in user B's GET; B's DELETE of A's row answers 404 and
    leaves it unchanged; B's PUT of A's view answers 404 and leaves it
    unchanged; A's own DELETE succeeds, re-read independently.
  - Key-date POST: every message in 3b exactly, including the real data range
    text; the duplicate with different letter case; the 51st; the trimmed name
    stored; org_id or user_id in the body is rejected and not echoed.
  - Key-date GET: sorted chronologically; custom_dates empty array for a user
    with none; the envelope, vocabularies and limits are present; a caller with
    no valid session is refused by the gate.
  - Views: every invalid-config variant is refused with 422 naming the field
    or the offending keys only: unknown field at each level, inactive series
    key (use a real 'deferred' series), unselectable security (use a real note
    security), duplicate selection, empty selection, 41 selections, bad mode,
    bad scale, bad anchor type, relative years 0 and 61, anchor after end,
    config over 20,000 bytes. A valid config round-trips through POST then GET
    unchanged.
  - Views, names and limits: messages in 4c exactly; case-insensitive duplicate
    409; the 51st view; PUT overwrite by the owner; PUT rename onto an existing
    name 409; PUT or DELETE on a preset id answers 404 and the preset is
    unchanged.
  - Stale selections: after the fixture series is set to 'deferred', GET lists
    its key in unavailable, the other selections are intact, and the stored
    config is byte-for-byte unchanged.
  - Loader (fixture rows): inserts, a second identical call writes nothing and
    leaves updated_at untouched, a changed value updates only that row, is_active
    and notes on an existing row are never overwritten, a row absent from the
    input is never deleted, a preset with an unresolvable key is skipped with a
    [FIND] while another preset in the same call still loads.
  - Seed files: version fields, 32 key dates (22 'owner_list', 10 'suggested'),
    16 regimes (8 each type), 4 views; unique slugs; every kind, precision and
    source value passes the live CHECKs; every month-precision start is the
    first of its month; the file contents equal the data in this prompt
    (spot-check slugs black-monday-1987, covid-crash-2020, latam-debt-crisis-
    1982, one regime of each type, and one preset).
  - Least privilege: the key-date and view modules never import platform_scope
    (static check), and the endpoints succeed on a non-bypassing connection
    without super-admin.
  - Existing data untouched: row counts of market_data.indicator_series,
    indicator_observations, indicator_ingest_runs, portfolio.securities_global,
    portfolio.securities_global_prices, public.fx_rates, public.users and
    public.organizations are equal before and after, excluding fixtures.
  - Teardown deletes fixtures in strict FK order (personal rows, then fixture
    users, then fixture organizations, then fixture series and their
    observations), confirmed against information_schema, and re-reads every
    table to confirm non-fixture counts are exactly as before. Fail loudly with
    SystemExit(2) if any fixture row remains. Never TRUNCATE.
Phase B runs ONLY with `--live`, after the operator has run the loader.
  - key_dates has 32 rows, 22 'owner_list' and 10 'suggested'; regimes has 16;
    platform views has 4; every indicator key in every preset resolves to an
    ACTIVE series; the earliest key date and the earliest regime are within the
    available data range (report any that fall before it as [FIND]).
  - Spot checks read back exactly: black-monday-1987 (day precision,
    1987-10-19, no end); covid-crash-2020 (2020-02-19 to 2020-03-23, both day precision);
    latam-debt-crisis-1982 (month precision, 1982-08-01).
  - GET /market/key-dates through the production function as a fixture caller
    returns the 32 key dates chronologically, the 16 regimes, an empty
    custom_dates, and the data range.
  - Running the loader's upsert logic over the real seed rows in DRY mode (a
    function that classifies without writing) reports 0 inserted, 0 updated.

=== TASK 6: DOCS ===
Update docs/PROJECT_STATUS.md with an mkt03b entry, and under the mkt03 entry
append `UPDATE 2026-10-07` saying the remaining plan is mkt04 (UI). Update
docs/MARKET_DATA_DESIGN_V1.md with: the key-dates, regimes, views and custom-
dates design (tables, policies, API, validation messages, config schema); the
decision that per-user privacy is enforced in the service layer because the
platform has no current-user RLS setting, with a one-paragraph "next candidate"
noting a current-user setting would let the database enforce it; the stale-
selection behavior; that platform presets are seed-only; that the Fed
tightening dates are approximate and must be verified against the FOMC record;
that views may reference Yahoo-sourced series so the Yahoo launch blocker and
the per-tenant gating in mkt04 still apply; and that docs/market_data/
KEY_DATES_SPEC_V1.md is the original spec, superseded where this section says
so. Do not create or edit that spec file. Do not edit CLAUDE.md; list any lines
you think it should gain at the end of PROJECT_STATUS.

=== VERIFICATION: apps/api/scripts/verify_mkt03b.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. Hydrate secrets internally using the same helper the earlier verify
scripts use. Output lines begin with [PASS], [FAIL], [FIND], or [SKIP]; the last
line is `TOTAL: <passed> passed, <failed> failed`. Exit non-zero on any [FAIL].
Every assertion states why it matters, not just what it checked. One assertion
per bullet in Task 5, Phase A always, Phase B behind --live. Do not chain the
other verify scripts; the operator re-runs them standalone.

When finished, STOP and print exactly these operator commands, in this order:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_seed_reference.py load
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03b.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
