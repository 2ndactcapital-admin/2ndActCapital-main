DISCLOSURES1 — one disclosure library for the whole platform. Every
disclosure (risk statements, hypothetical-performance text, tax-estimate
labels, licence notices, product disclosures) is managed on an admin page
and points to a FILE stored in Cloudflare R2 under a folder for the org — or
a lower level (household, account) that overrides it — with versions, a
platform default, and a recorded acknowledgement of the exact version a
person saw. The disclosures hard-coded today move into it. 9 tasks +
verification. Branch disclosures1-work.

Context — Joe (2026-11-07): "Find sample disclosures and use as best
practices. In all cases of disclosures they should be managed on an admin
page and should refer to files stored in an r2 folder for each org or lower
level." Today disclosure text lives in code and per-feature tables (e.g.
services/long_short_rules.py RISKS — rep13's six long/short risks;
services/hypothetical_policy.py disclosure_text — the backtest /
hypothetical-performance policy; others found in Task 1).

BEST-PRACTICE SOURCES (read for structure and topics; do NOT copy any
firm's text — write our own plain-English defaults, marked "default — have
counsel review"):
- Titan, "Direct Indexing Risks and Disclosures" — structure: product
  overview; scope and limitations (eligible accounts, minimums, fees,
  who trades); investment risks (market, tracking error, wash sale incl.
  outside accounts, tax-benefit uncertainty — varies, front-loaded, defers
  not eliminates, $3,000 ordinary-income cap; tax-law change; complexity /
  cost-basis reporting; liquidation / exit taxes); hypothetical / backtested
  estimates; no tax, legal or estate advice.
- AQR, "Improving Direct Indexing: 130/30 and 150/50 Strategies" — the six
  long/short risks already in rep13 (pre-tax underperformance, varying tax
  benefit, lower future rates, unused losses, larger tax at redemption,
  rule changes or IRS challenge); add margin / short-sale risks (unlimited
  loss on a short, margin calls, borrow recall, borrow cost changes).
- Alpha Architect / ETF Architect "351 Education Center" and Cambria
  "Section 351 ETF Conversion" — the 25 % / 50 % test (with ETF holdings
  looked through), the 80 % control test, eligible owners (U.S. persons;
  individual / joint / trust / S-corp; partnerships and LLCs after review;
  C-corps and non-U.S. not eligible), ineligible assets (restricted /
  illiquid securities, derivatives, crypto, physical commodities, commodity
  pools, grantor trusts, partnerships), lots keep basis and holding period
  (custodians differ), in-kind contribution risk (a failed contribution
  loses carryover basis), new-fund risk (no operating history; may not reach
  viable size), complexity, consult a tax adviser, not tax / legal advice,
  read the prospectus.
- SEC Marketing Rule (Rule 206(4)-1): hypothetical performance only with
  the policies and the disclosure text it requires — keep the existing
  hypothetical_policy gate.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-07; Task 1 confirms):
- services/storage.py: boto3 S3 client for Cloudflare R2 (R2_ACCOUNT_ID,
  R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, bucket R2_BUCKET_NAME default
  "2ndactcapital-docs"), upload_bytes(...). Chancery (S26) stores documents
  in R2 with its own key layout — reuse the storage helper, keep a separate
  disclosures prefix.
- Files that mention disclosure today include services/long_short.py,
  long_short_rules.py, hypothetical_policy.py, backtest.py, blocks.py,
  cost_model.py, fee_invoices.py, agent_proposals.py, models.py,
  note_replication.py, options_sleeve.py, rebalance_proposals.py,
  replica_models.py, rbac.py, assistant_transcript.py — Task 1 sorts which
  hold disclosure TEXT shown to people.
- prices1: Yahoo prices are internal use only (member display not allowed)
  — its "internal" label is a licence notice and belongs in the library.
- bugfix4: roles from user_roles only. Count live-written tables BY FIXTURE
  IDS ONLY (the nightly runs ~07:00 UTC).
- Regression: this replaces the text rep13 proposals carry → re-run
  verify_rep13.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: no npm run build, no full verify, no network
  except R2 through the storage helper — and the VERIFY uses a fake storage
  transport (no network).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. Never print a secret. Bi-temporal rule 3; RLS for every
operation; maker-checker on publishing a disclosure version. Light / cream
UI. Commit ALL sprint files (excluding sprint_prompts/logs) on the current
branch at the end with message "disclosures1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Every place disclosure text is shown to a person (staff or member):
      file, the text or its source, where it renders (screen / PDF /
      envelope / email), audience. A table in docs/DISCLOSURES1_DISCOVERY.md.
  1b. storage.py and Chancery's R2 key layout; whether a signed / proxied
      read path exists for showing a stored file in the app.
  1c. Any existing acknowledgement records (rep13's long/short
      acknowledgement; hypothetical policy acceptance) — shape.

=== TASK 2: THE LIBRARY ===
portfolio.disclosures (or public — state why): key (e.g. long_short.risks,
section351.exit, direct_indexing.tax_overlay, hypothetical.performance,
tax.estimate_label, prices.internal_use_notice, options.overlay), title,
category, audience (staff / member / both), and versions: each version is a
FILE in R2 plus its plain-text summary (what the screen shows inline), its
content hash, effective date, status (draft / published / retired), who
drafted and who published (maker-checker). Scope: platform default →
org → household → account; the most specific PUBLISHED version in force on
the as-of date wins (resolver: one function, one query).

=== TASK 3: R2 FOLDERS ===
Key layout (state it in the doc): disclosures/platform/<key>/v<N>-<hash>.<ext>,
disclosures/orgs/<org_id>/<key>/…, disclosures/orgs/<org_id>/households/<id>/…,
…/accounts/<id>/…. Upload through storage.py (PDF, DOCX, MD, HTML; size
limit), hash stored, never overwritten (a new version is a new object).
Reads through the app (proxied or short-lived signed URL), org-scoped by
RLS: an org never reads another org's folder; members read only published
member-audience disclosures that apply to them.

=== TASK 4: ADMIN PAGE ===
Admin → Disclosures: the list (key, title, audience, in force for this org
and why — platform default or org override), per key the versions (status,
dates, maker / checker, file link, summary), Upload new version (draft) →
publish by a second person; override at household / account from that
record's page; Retire. Super Admin manages platform defaults; org admin
manages the org's and lower levels'. Every action audited.

=== TASK 5: ACKNOWLEDGEMENTS ===
One table: who acknowledged which disclosure VERSION (id + hash), for what
(proposal id, plan id, account), when, from which screen. A gated action
(rep13 long/short submit; later the 351 plan; hypothetical performance shown
to a member) checks that the CURRENT in-force version was acknowledged — if
a newer version was published since, re-acknowledgement is required.

=== TASK 6: MIGRATE TODAY'S TEXT ===
Every disclosure from 1a becomes a library key with a platform default
version: the current text written to an R2 file (via a one-time operator
script, --dry-run first — the sprint runs only --dry-run), and the code
reads it through the resolver (with the exact current text as the offline
fallback so nothing goes blank). rep13's six risks + margin / short-sale
risks → long_short.risks; hypothetical_policy's text → hypothetical.performance
(the Marketing Rule gate unchanged); the prices "internal" label →
prices.internal_use_notice; tax "estimate — confirm with your tax adviser"
→ tax.estimate_label.

=== TASK 7: DEFAULT TEXTS (BEST PRACTICE) ===
Write our own plain-English platform defaults, each marked "default — have
counsel review", for: direct_indexing.tax_overlay (Titan's structure),
long_short.risks (AQR six + margin / short-sale risks), section351.exit (the
Alpha Architect / Cambria topics above), options.overlay, notes.replica
(structured-note replica vs note), hypothetical.performance (existing),
tax.estimate_label, prices.internal_use_notice. Stored as Markdown source
files in the repo (docs/disclosures/defaults/<key>.md) and loaded to R2 by
the operator script.

=== TASK 8: REAL PROOF ===
  - Resolver: platform default; an org override wins; a household override
    wins over the org; an account override over the household; a draft never
    shows; a retired version stops on its date; the as-of date picks the
    version in force.
  - R2: upload writes the hashed key under the right folder (fake storage
    transport records the keys); a second upload is a new object; org B
    cannot read org A's file (404); a member reads only a published member
    disclosure that applies.
  - Maker-checker: the uploader cannot publish; a second person can.
  - Acknowledgement: rep13 long/short submit requires the in-force
    long_short.risks version; after a new version is published the old
    acknowledgement no longer suffices.
  - Migration: every 1a disclosure resolves to its key; with storage
    unavailable the fallback text is exactly the old text (nothing blank).
  - Admin page node tests: list, versions, upload → draft, publish by
    another user, override from a household page.
  - Org isolation both directions (rolbypassrls = false); real rows
    unchanged (counted by fixture ids where real activity writes).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_rep13.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md + docs/DISCLOSURES.md (the library, scopes, R2
layout, resolver rule, acknowledgement rule, operator step: load defaults
--dry-run then real; "every new disclosure goes through the library" as a
platform rule for later sprints); add verify_disclosures1.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_disclosures1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path). Set AUTHZ_TEST_MODE=1 before importing the app. Hydrate secrets
from Doppler over HTTPS. No network: R2 through a fake storage transport at
the CURRENT storage.py signatures (assert no real client is built). Wrap
every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count, counting audit_log and any table
real activity writes to BY FIXTURE IDS ONLY. Fixture orgs (privileged insert
path) and users is_test = true (assert it); roles via user_roles (assert the
app decides each). Read UNIQUE / CHECK / FK constraints and unique indexes
from the catalog before seeding (FAIL if none are read). Reserved fixture
prefixes that match no real row (assert it). Support --self-checks-only and
--cleanup-only. The regression subprocess gets VERIFY_NESTED=1, after
teardown, 2700 s; with VERIFY_NESTED=1 skip it with one '[SKIP] nested run'
line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context set ON the connection inside its transaction (a contextvar
    alone is not an RLS context); rolbypassrls = false asserted; an empty
    list never passes a check.
  - Compare Decimals as Decimals, hashes / keys exactly.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - Each fixture upload has distinct content.
  - Count your own node tests by their file, not by title prefixes.
  - A test stub must match the CURRENT signature of what it replaces.
  - Never seed a state the database forbids (read the unique indexes first).
  - No check frozen against live state.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1c
  [Y] Library + resolver: platform → org → household → account; as-of
  [Y] R2 layout, hashed immutable objects, org-isolated reads
  [Y] Admin page; maker-checker publish; audit
  [Y] Acknowledgements by version; re-ack after a new version
  [Y] Migration complete with exact-text fallback; defaults written
  [Y] Isolation; own routes; --check CURRENT; real rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep13.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
