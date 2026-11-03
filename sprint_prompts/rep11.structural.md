REP11 — Replication Studio, sprint 11 of 15 (phase 3 starts: structured
notes): note replication from the 424B2 terms — for every structured note
whose payoff terms are extracted (portfolio.securities_global_note_terms),
a replicability verdict with the plain reason, and for the replicable ones a
replica built from rep8's options-sleeve machinery (underlying + listed /
FLEX options, plus a Treasury leg where the note promises principal), its
at-expiry payoff drawn over the note's, the differences that matter (issuer
credit, liquidity, tax, path features the replica cannot copy), and — only
when quotes exist — the replica's cost against the note's price (the
embedded fee estimate). A household that holds a note sees "replicable
alternative" in the X-ray. Nothing trades; every leg is a manual
instruction. 9 tasks + verification.

Context: spec roadmap phase 3 — "rep11 note replication from the 424B2
schema; rep12 note-versus-replica check across new issues". Instrument order
(Joe): index ETFs → defined-outcome options sleeve → structured notes →
long / short. The structured-note thesis (Joe): public EDGAR filings show
what a note really is; Hollisworks shows a member "here's what you own, what
it's really worth, and what a transparent replica would look like".
Distribution agent and fee split are important to show when the terms carry
them. No paid data vendor (marketdata1 rule): prices / premiums / vols come
from rep8's adviser quotes or the security-data layer; anything that needs a
number we do not have reads "not priced — enter a quote".

Counsel / Joe items (notes on screen and in the design doc, never decided in
code): tax treatment of the note (prepaid forward / contingent payment debt
instrument) vs the replica (Section 1256 index options; straddle question
pending counsel from rep8); whether showing "embedded fee" estimates to
members is hypothetical performance / marketing (rep10's policy gate); the
issuer-credit disclosure wording.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after rep10 + aibudget1 + vhk2;
Task 1 confirms exact signatures):
- portfolio.securities_global_note_terms: 54 live rows — columns id,
  global_security_id, reference_filing_id, terms_status (final |
  preliminary), product_archetype (autocallable, buffered_note, digital,
  principal_protected, reverse_convertible, other), protection_type
  (buffer | floor | none), basket_type (single | worst_of | basket),
  return_basis, is_decrement_index, notional_currency, protection_pct,
  cap_pct, participation_rate, coupon_rate, coupon_barrier_pct,
  autocall_barrier_pct, autocall_frequency, has_no_call_period,
  no_call_months, initial_valuation_date, final_valuation_date, tenor_years,
  field_status, extraction_confidence, routing_decision, routed_at, bi-
  temporal columns. Live mix: autocallable 31 (single 15, worst_of 14,
  basket 1), buffered_note 15 (single 10, worst_of 5), digital 6,
  principal_protected 2, reverse_convertible 1, other 1; final 47,
  preliminary 7.
- portfolio.reference_filings 11,836; note_term_readings 6,673;
  note_terms_field_registry 75 fields; structured_note_issuers 33;
  services/note_terms_extraction.py, note_terms_routing.py,
  note_terms_corrections.py, underlying_resolution.py (underlying links).
- rep8 (services/options_sleeve.py): option_contracts (platform, terms_key
  identity), option_packages / option_package_legs on a model version,
  find_or_create_contract, adviser quotes (portfolio.adviser_quotes,
  insert-only, as-of + source), latest_quote, legs_from_rows, intrinsic,
  package_value, package_return, stated_outcome, computed_terms, payoff_grid,
  payoff_points; buffer package = underlying + long ATM put + short put at
  the buffer end + short call at the cap; FLEX European cash-settled index
  options by default; options_tax_gate.py; options_proposals.py (proposal
  kind 'options'; legs → manual instructions); Altruist options level
  'unknown' → manual only. All four options tables hold 0 rows today.
- services/pricing/ssvi_surface.py exists (SSVI surface fit) — usable ONLY
  with implied-vol quotes; none exist.
- rep7 X-ray (services/xray.py) lists every household position with its
  security; rep10's hypothetical-performance policy gate
  (hypothetical_policy.py) governs member visibility of hypothetical
  figures.
- vhk1 / vhk2: apps/api/scripts/_verify_self.py (own_routes, still_gated,
  when_empty); docs/VERIFY_RULES.md (no frozen counts / lists of live
  state); every verify supports --self-checks-only and --cleanup-only;
  teardown via scripts/_verify_cleanup.py.
- Regression: rep11 builds on the options sleeve → re-run verify_rep8.py
  ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- The live Render scheduler skips is_test orgs; every fixture org MUST be
  is_test = true.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any full verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. Write the verify script but DO NOT RUN IT, and do not run npm run
build. No network calls; no model calls (this sprint reads terms already
extracted). Never print a secret. Column names from the live DB. Bi-temporal
rule 3 for every new table; RLS for every operation. Decimal everywhere. A
number that needs data the platform does not have reads "not priced — enter
a quote" — never a placeholder. Every estimate labelled "estimate" with its
quotes and as-of. Note terms are PUBLIC platform data; replicas, verdicts
and analyses that touch a household are org-scoped. Every human action is a
human task; maker-checker on a replica becoming a model. Light/cream UI with
the existing tokens; labels and vocabularies from the API envelope. SQL
parameters inside CASE / COALESCE get explicit casts. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The note-terms model: which row is "the" current terms per note (live
      bi-temporal row; final vs preliminary; field_status / routing_decision
      meaning "approved" vs "needs review"); the underlying(s) per note
      (underlying_resolution links: index / ETF / single stock; resolved or
      not); distribution agent and fees — which fields carry them.
  1b. Payoff mapping per archetype, from the registry fields: buffered_note
      (single) → buffer package (rep8); principal_protected → Treasury
      zero / bill at the protected % + call spread at participation and cap;
      digital (single) → call-spread approximation (stated tolerance);
      reverse_convertible (single) → short put + Treasury + coupon;
      autocallable, worst_of, basket, decrement indices, path features
      (barriers observed daily, memory coupons) → NOT statically replicable —
      say exactly why per feature. Report how many of the 54 fall in each
      verdict.
  1c. What rep8's builder needs (strike % of start level, expiry, ratio,
      underlying security id) and how a note's dates map (initial / final
      valuation dates → outcome period; tenor).
  1d. Where a household holds a note today (positions by CUSIP →
      securities_global → note terms) — counts only.
  1e. Whether rep8's package can hang off something other than a model
      version (a note analysis) or needs a new parent.

=== TASK 2: SCHEMA ===
Design from Task 1 (names indicative):
  - note_replicability (platform, per note-terms version): verdict
    static | approximate | not_replicable, reasons (codes + text), the
    mapping used, computed_at; recomputed when the note's live terms change.
  - note_replicas (org-scoped): note, verdict snapshot, package (rep8
    option_packages) and a Treasury / cash leg (amount, maturity,
    security when known), status draft → pending_approval → approved →
    archived; maker-checker; becomes a model / registry entry only on
    approval.
  - note_replica_comparisons: note price (quote) and replica cost (quotes)
    with as-of; embedded-fee estimate = note price − replica cost (when
    both priced); differences list (credit, liquidity, tax, path).
Apply with scripts/_apply_rep11.py through Doppler, synchronously; VERIFY it
landed; save docs/rep11_part1.sql.

=== TASK 3: VERDICTS ===
A platform job computes the verdict for every live note-terms row (all 54),
with reasons a member can read ("worst-of three underlyings — a replica
would need correlation, which listed options cannot provide"); preliminary
terms are marked "preliminary — final terms may differ"; terms needing
review are marked and never used to build a replica.

=== TASK 4: BUILD A REPLICA ===
For static / approximate notes: the legs from the note's terms through rep8's
builder (contracts via find_or_create_contract; Treasury leg as a cash /
bill line), computed cap / buffer / floor next to the note's stated values
(exact Decimal; any difference shown), the at-expiry payoff curve of note vs
replica on one grid (strikes only — always available), and an approximation
error statement for digital / approximate cases.

=== TASK 5: PRICE, WHEN QUOTES EXIST ===
Replica cost from adviser quotes (premiums, underlying level, Treasury
yield) and the note's price (quote); embedded-fee estimate with every quote
and its as-of; with any quote missing: "not priced — enter a quote" naming
what is missing. Implied vols: only if entered; ssvi_surface is used only
when enough vol quotes exist (state the minimum), never extrapolated
silently.

=== TASK 6: THE X-RAY AND THE MEMBER VIEW ===
A household's X-ray marks each held note: verdict, "replicable alternative"
(link to the replica analysis), stated terms, distribution agent / fees when
known. Member visibility of any embedded-fee or replica-cost figure follows
rep10's hypothetical-performance policy (staff only without an approved
policy); the verdict and stated terms are factual and always visible to the
household's own members.

=== TASK 7: API, PERMISSIONS, SCREENS ===
Reuse view_replication / manage_replication / approve_replication; routes
under /api/v1/replication/notes/…, gated, in AUTHZ1_ROUTES; envelopes with
vocabularies. Screens: Replication → Notes (all notes with terms, filter by
verdict / archetype / issuer / underlying), a note's page (terms with the
filing link, verdict and reasons, payoff overlay, replica builder, pricing,
differences), the X-ray marker.

=== TASK 8: REAL PROOF ===
  - Verdicts: one fixture note per archetype / basket type → the expected
    verdict and reason; preliminary marked; needs-review terms refused for
    building; the job is idempotent and recomputes on a terms change.
  - Buffered single-index note: legs exact (strikes = 100%, 100% − buffer,
    100% + cap); computed cap / buffer / floor equal the note's stated
    values; payoff grid identical to the note's stated payoff at every
    grid point.
  - Principal-protected: Treasury leg + call spread reproduces protection
    and participation / cap exactly at expiry.
  - Digital: approximation error within the stated tolerance, stated.
  - Worst-of / autocallable: refused with the reason; nothing written.
  - Pricing: all quotes present → embedded fee exact; one missing → "not
    priced", naming it; as-of carried.
  - X-ray: a fixture household holding a fixture note sees the marker;
    member visibility of fee figures follows the policy both ways.
  - Maker-checker on a replica becoming a model; org isolation both
    directions (rolbypassrls = false); gates both ways on identical
    requests; node tests show no approve / build controls without the flags.
  - Real note-terms rows untouched (count + checksum) — fixture notes only
    in a reserved prefix, platform rows removed by id at teardown.
  - Routes: own routes via _verify_self.own_routes(BEFORE_REF, "rep11") +
    still_gated; authz1_routes.py --check CURRENT.
  - Regression: verify_rep8.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (phase 3 begins: verdict
rules per archetype and feature, replica construction, pricing rule, member
visibility, open counsel items, the verdict counts over today's 54 notes);
add verify_rep11.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep11.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
or model calls. Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (platform fixture
notes / contracts included); re-read every touched table for its exact
pre-test count. Compute every expected date at the call. Fixture orgs and
users is_test = true (assert it). Read UNIQUE / CHECK / FK constraints from
the catalog before seeding and check every seed literal against its CHECK
(archetype, protection, basket, verdict, statuses). Reserved fixture
prefixes that match no real row (assert it). Support --self-checks-only and
--cleanup-only. Real-data checks (the verdict job over the real 54) use
_verify_self.when_empty and assert the rule per row, never a frozen count.
The regression subprocess gets VERIFY_NESTED=1, runs after teardown, 2700 s
timeout; with VERIFY_NESTED=1 this script skips its regression section with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a fingerprint of a real table by its real key (read it from the
    catalog), never an assumed `id`.
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real notes, terms, readings, filings, positions or users:
    count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e (verdict counts over today's notes)
  [Y] Schema landed, RLS for every operation
  [Y] Verdicts per archetype / feature with reasons; preliminary and
      needs-review handling; idempotent recompute
  [Y] Replicas exact for buffered / principal-protected; digital within
      tolerance; refusals write nothing
  [Y] Pricing exact with quotes; "not priced" naming the gap
  [Y] X-ray marker; member visibility per policy both ways
  [Y] Maker-checker; org isolation both directions; gates both ways; node
      tests
  [Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep8.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; real rows unchanged
