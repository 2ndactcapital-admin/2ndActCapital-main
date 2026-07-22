GRID UX — MINI-SPRINT A: build the reusable DataGrid component.
Small, focused sprint — 2 tasks + verification. Do NOT expand
scope beyond what's listed here (mini-sprint B, migrating all
OTHER screens onto this component, is separate and comes later).

CONTEXT: the component library already includes an AG-Grid
implementation used for the SPV Ledger (find it — likely
SPVLedgerClient.jsx or similar under apps/web/components/spv/).
Read its current config before building anything, so the new
shared component generalizes real, working patterns rather than
inventing new ones. S24 (white-label) just landed — a
ThemeProvider and lib/theme.js now expose the org's brand
colors/fonts; the new grid component must consume theme values
through that provider, not hardcode Signature palette colors
directly (the whole point of S24 was eliminating exactly that).

STANDING RULES: light theme (whites/creams) always; no
interactive prompts; Decimal for any money columns rendered.

=== TASK 1: Build the reusable DataGrid component ===
Create a shared component (e.g.
apps/web/components/ui/DataGrid.jsx) wrapping AG-Grid with:
  - Column picker — show/hide columns via a toggle menu
  - Column reorder — drag to reorder
  - Sort — click column header, standard AG-Grid sort
  - Filter — per-column filter inputs + a global quick-search box
  - Prop-driven column definitions (columnDefs, rowData, and an
    optional gridId used later for saving layout preferences —
    but do NOT build persistence yet, that's out of scope for
    this mini-sprint; just accept the prop so the API is stable
    when persistence is added later)
  - Styling pulled from the ThemeProvider/theme context (S24) —
    navy/gold/cream tokens, not hardcoded hex
  - Sensible, compact default row height and header style
    matching the existing SPV Ledger's current look, so the
    visual result doesn't regress

=== TASK 2: Pilot it on the SPV Ledger ===
Refactor the EXISTING SPV Ledger screen (wherever AG-Grid is
currently used) to consume the new shared DataGrid component
instead of its own bespoke AG-Grid setup. This is the proof
that the generalization actually works on real data before any
other screen adopts it. Confirm all existing SPV Ledger
functionality (whatever columns/sorting/filtering it already
has) still works identically through the new component — no
regressions.

Do NOT touch any other screen in this sprint (entity lists,
marketplace, member lists, etc.) — those are mini-sprint B.

=== VERIFICATION ===
This sprint has no database changes, so the usual SQL-assertion
verify pattern doesn't fully apply. Write
apps/api/scripts/verify_gridux_a.py that instead:
  [Y] The new component file exists at the expected path and
      exports the expected props (columnDefs, rowData, gridId
      at minimum — check via a simple file-content assertion,
      not a DB query)
  [Y] The SPV Ledger screen file now imports/uses the new
      shared component (grep-based check — confirm it's not
      still using a separate, unmigrated AG-Grid setup)
  [Y] Run the frontend build (cd apps/web && npm run build,
      or the project's actual build command — check
      package.json first) and assert it completes with exit
      code 0 — this is the real correctness gate for a
      frontend-only sprint
  [Y] No hardcoded Signature-palette hex values in the new
      DataGrid component file itself (reuse the same hex
      pattern from brand_sweep_grep.sh, applied just to this
      one new file — the whole point of building it AFTER S24
      is that it should be theme-driven from day one)

Report each assertion explicitly (pass/fail), same style as
prior verify scripts. Push when 100% pass.
