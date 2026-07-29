OWNERSHIP TREE GRAPH — SPRINT B (printable export). 3 tasks +
verification. Sprint A (interactive graph, both staff and
member routes) is already merged — components/graph/
OwnershipGraph.jsx and AsOfPicker.jsx exist and are live. Do
NOT rebuild anything from Sprint A — this sprint is export only.

CRITICAL FIRST STEP — DO NOT SKIP: per the design spec, the
implementation approach for export is NOT pre-decided. Task 1
below is a real stress test that determines which of two paths
this sprint builds. Do not skip the test and default to one
approach.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme matching every other screen. The export
output itself must ALSO respect the restricted-access filter
and both visibility engines exactly as the interactive view
does — nothing exportable that wasn't already visible to that
viewer in the live component.

=== TASK 1: The stress test — decide the implementation
approach from real data, not in the abstract ===
  (a) Create a genuinely large seeded test ownership tree (many
      entities — at least 25-30 nodes, several levels deep,
      with a realistic mix of ownership and beneficiary edges)
      using existing test-fixture patterns from Sprint A's
      verify script.
  (b) Render this tree through the EXISTING OwnershipGraph.jsx
      component and apply a @media print stylesheet: hide all
      interactive chrome (filter sidebar, zoom controls, buttons),
      keep just the rendered SVG tree.
  (c) Evaluate honestly: does the browser's native print/Save-
      as-PDF handle this large tree acceptably — is text legible
      at a reasonable zoom, does pagination (if the browser
      auto-paginates a tall SVG) look reasonable, or does content
      get cut off / shrunk illegibly / overlap badly?
  (d) REPORT this finding explicitly, then proceed accordingly:
      - If the print-stylesheet approach holds up cleanly on
        this large tree: proceed with Task 2 building THAT
        approach (the cheaper path).
      - If it does NOT hold up (illegible, badly cut off, poor
        pagination): proceed with Task 2 building a DEDICATED
        print-optimized renderer instead — a genuinely separate
        layout path (not just CSS over the same DOM) that
        explicitly paginates (e.g. one branch/subtree per page,
        or a tiled grid layout) and controls typography
        precisely rather than relying on the browser's default
        print pagination.
  Do not proceed to Task 2 without first completing and
  reporting this test.

=== TASK 2: Build the export, per Task 1's finding ===
Whichever approach Task 1 justifies, the export must include:
  - A header: the focal entity's name, "Ownership structure as
    of [date]" using whatever date AsOfPicker was set to at
    export time (default: today), and a generated-on timestamp
  - A legend: what an ownership edge vs. a beneficiary edge
    means (matching the interactive view's visual distinction)
  - Clean, static layout — no pan/zoom chrome, no filter
    sidebar, no interactive affordances
  - Respects the CURRENT expanded/collapsed state at export
    time by default (a user who has collapsed distant branches
    gets a smaller, focused export) — PLUS an "expand all before
    export" option for when a full picture is wanted
  - If the tree requires more than one page to render legibly,
    genuinely paginate (multiple pages) rather than silently
    shrinking text past readability

=== TASK 3: Wire export into BOTH existing routes ===
Add an "Export / Print" action to BOTH the staff route
(apps/web/app/crm/[id]/ownership-graph/page.js) and the member
route (apps/web/app/portfolio/ownership-tree/page.js) from
Sprint A. The export must pull from the SAME already-visibility-
filtered data the interactive view is already rendering — do
NOT re-query with a separate, potentially-unfiltered path. If a
restricted entity is correctly absent from a user's interactive
view, it must be absent from their export too, by construction
(same filtered data source), not by a second, separately-
implemented check.

=== VERIFICATION ===
Write verify_ownershiptreeb.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] Report Task 1's stress-test finding explicitly (which
      approach was chosen and why, based on the real test)
  [Y] Export header contains the correct focal entity name,
      as-of date matching the AsOfPicker state at export time,
      and a generated timestamp
  [Y] Export respects the current collapsed state by default —
      a collapsed branch does not appear in the export unless
      "expand all" was selected
  [Y] "Expand all before export" produces the full tree
  [Y] A restricted entity absent from a user's interactive view
      (per Sprint A's proven restricted-access filter) is ALSO
      absent from that same user's export — proves the export
      path did not bypass the filter
  [Y] The same restricted entity DOES appear in the export for
      a user on its allow-list
  [Y] A large tree (25-30+ nodes) produces a genuinely usable
      export — legible text, reasonable pagination if needed,
      not illegibly shrunk or silently truncated
  [Y] Export works identically from both the staff and member
      routes
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex in any new file
  [Y] Teardown: zero leftover test rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, same as Sprint A, given this
touches the same restricted-access-sensitive data path.
