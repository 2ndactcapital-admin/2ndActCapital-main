REP15 — Replication Studio phase 4 ends: PLATFORM-PUBLISHED MODELS AND
TENANT SUBSCRIPTIONS. The Hollisworks platform org (admin.hollisworks) can
publish models as a sub-adviser; each tenant org can subscribe, use a
subscribed model's versions in its accounts, and be billed a model-provider
fee — all behind a regulatory gate that stays CLOSED until a registered
adviser entity and the agreements are on file. 10 tasks + verification.
Branch rep15-work.

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22):
- "Models for other tenants: every tenant runs its own instance with its own
  models and registry; the Hollisworks platform org (admin.hollisworks) can
  also act as a sub-adviser, publishing models that tenants subscribe to."
- "The platform org publishing models as a sub-adviser needs a registered
  adviser entity behind it, plus ADV disclosure and sub-advisory agreements
  with each subscribing tenant. Hollisworks the software company is not an
  RIA today, so this is a structuring decision before phase 4."
- "Models used by other tenants: a model-provider fee (bps of model assets)
  owed to 2nd Act, as a separate revenue line posted to the general ledger.
  Phase 4, after the regulatory decision."
- Naming: every model name starts with "Holdings of"; descriptions say
  "replicates the holdings of …"; never the index name as the model's name.
- Every human action is a task in the workflow engine and is logged.
DECIDED for this sprint: build the whole mechanism now; the PUBLISHING GATE
is a platform setting that is OFF and can only be opened by Super Admin after
recording the registered adviser entity (legal name, SEC / CRD number, ADV
Part 2A as a disclosure-library file) — until then nothing can be published
to, or subscribed by, a REAL org (fixture orgs only, in the verify).

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (Task 1 confirms signatures):
- rep3: portfolio.models one registry, versions, upload + upload log,
  library, composite sleeves, maker-checker approval. rep5: accounts join
  model versions; proposals. rep13: long/short models (kind long_short).
- Platform jobs / platform data live in the Hollisworks org; tenants are
  orgs; RLS isolates every org (a tenant never reads another tenant's rows).
- disclosures1 (merged before this sprint): the disclosure library — files in
  R2 per org / household / account, versions, maker-checker publish,
  acknowledgement by version. The sub-advisory agreement and the ADV are
  library disclosures (keys model_provider.subadvisory_agreement,
  model_provider.adv_2a) — never text in code.
- Fees / GL: fee schedules and fee calc (fee42b and later), general ledger
  postings with immutability triggers (teardown via the documented
  privileged path) — Task 1 finds the posting API.
- bugfix4: roles from user_roles only. Count live-written tables BY FIXTURE
  IDS ONLY (nightly ~07:00 UTC).
- Regression: this changes the model registry and library every account
  reads → re-run verify_rep13.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret. Decimal everywhere. Bi-temporal
rule 3; RLS for every operation; maker-checker on every publish, subscribe
and adopt. Never open the publishing gate; never publish to or subscribe a
real org. No model call. Light / cream UI. Commit ALL sprint files (excluding
sprint_prompts/logs) on the current branch at the end with message "rep15:
<one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. The model registry: tables, version lifecycle, who owns a model (org),
      how the library lists models, how an account joins a version.
  1b. How a platform-org row could be read by a tenant without breaking RLS
      (existing patterns: the security master, platform providers) — the
      safe way for a tenant to read PUBLISHED platform model versions only.
  1c. Fee schedule / fee calc and the GL posting API (accounts, journal
      lines, immutability, reversal).
  1d. The disclosure library's resolver and acknowledgement calls.

=== TASK 2: THE GATE AND THE PROVIDER ===
A platform "model provider" record: legal entity name, SEC file / CRD
number, registration status, ADV Part 2A (library key model_provider.adv_2a),
effective date; and the publishing gate setting (OFF). Opening it requires
Super Admin, a complete provider record and a published ADV; every change
audited. While OFF: publish, subscribe and adopt routes refuse with "model
publishing is not enabled — a registered adviser entity is required".

=== TASK 3: PUBLISHING ===
The platform org's models (rep3 registry) can be marked publishable; a
version is PUBLISHED to the catalog by maker-checker (approve → publish),
with its kind, construction notes, holdings as-of, model fee (bps) and the
required naming. A published version is immutable; a retired version stays
readable for accounts already on it. The fund's holdings file is never
exposed — only the model's target weights.

=== TASK 4: THE CATALOG AND SUBSCRIPTIONS ===
Tenants see Replication → Model catalog: published platform models (name,
kind, versions, fee). An org admin requests a subscription; it becomes
active only when (a) the tenant has acknowledged the in-force
model_provider.subadvisory_agreement version (resolved for THAT tenant — a
tenant-specific agreement overrides the platform default) and (b) a Super
Admin on the platform side approves it (maker-checker across the two orgs,
both audited). Subscriptions: active / suspended / ended, with dates.

=== TASK 5: USING A SUBSCRIBED MODEL ===
A subscribed model appears in the tenant's library as read-only ("Provided
by <provider>"), its versions selectable for accounts like the tenant's own;
the tenant may COPY it into its own registry (a new tenant-owned model,
"Holdings of …", no longer provided — the copy breaks the link and the fee).
New version published → the tenant gets a task "new version available" with
the diff (weights changed); adopting it for the tenant's accounts is the
tenant's maker-checker decision (an org setting may pre-approve adoption;
default off). Suspending or ending a subscription stops new adoptions; accounts
already on it keep their version and get a task to move.

=== TASK 6: ISOLATION ACROSS ORGS ===
The platform org sees, per subscribing tenant, only: subscription status,
the count of accounts and the total model assets per model version (for the
fee) — never members, accounts, positions or names. A tenant never sees
another tenant's subscription. Prove both with RLS (rolbypassrls = false).

=== TASK 7: THE MODEL-PROVIDER FEE ===
Per tenant per model: bps × average daily model assets over the billing
period (accounts on a provided version; state the asset definition — market
value of the account's positions under the model), accrued daily, invoiced
per period as a separate revenue line "Model-provider fee" and posted to the
general ledger through the existing posting API (receivable at the tenant /
revenue at the provider), reversal on correction, never edited. Behind the
same gate (OFF for real orgs).

=== TASK 8: DISCLOSURES ===
Every subscribed-model proposal in a tenant shows the provider and that the
model is provided by a sub-adviser (library key model_provider.member_notice
— a new platform default, "have counsel review"); the tenant's ADV
disclosure obligation is listed as an operator item, not automated.

=== TASK 9: REAL PROOF ===
  - Gate: OFF refuses publish / subscribe / adopt with the reason; opening
    without a complete provider or ADV refused; Super Admin opens it on the
    FIXTURE platform org only.
  - Publish: maker cannot publish own version; published version immutable;
    naming enforced; holdings file never in any tenant response.
  - Subscribe: refused without the tenant's acknowledgement of the in-force
    agreement version; a tenant-specific agreement overrides the default;
    platform Super Admin approval required; statuses and dates.
  - Use: the subscribed version joins a fixture account; a proposal shows
    the provider notice; copy creates a tenant-owned model with no link and
    no fee; a new version creates the "new version available" task with the
    exact weight diff; adoption needs the tenant's checker; suspension stops
    adoption and tasks existing accounts.
  - Isolation: the platform org reads only status, counts and model assets
    per tenant per version; tenant B never sees tenant A's subscription or
    accounts; members of a tenant see only their own household.
  - Fee: hand-computed bps × average daily assets over a fixture period ==
    the accrual == the invoice line == the GL posting; a correction posts a
    reversal; nothing edited in place.
  - No model / network call; real and demo rows unchanged (counted by
    fixture ids where real activity writes); the gate is OFF on the real
    platform org after the run.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_rep13.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md and docs/REPLICATION_STUDIO.md (rep15: the gate, the
provider record, publish / subscribe / adopt flows, cross-org isolation, the
fee and GL lines); operator / Joe items: the registered adviser entity
decision, the ADV, the sub-advisory agreement text (library), the fee bps;
add verify_rep15.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep15.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path). Set AUTHZ_TEST_MODE=1 before importing the app. Hydrate secrets
from Doppler over HTTPS. No network (assert it; R2 through the fake storage
transport disclosures1's verify uses). A FIXTURE platform org and two
fixture tenant orgs (privileged insert path, is_test = true — assert it); the
real platform org and its gate are only READ (assert unchanged). Wrap every
section so an exception is a [FAIL]; teardown via scripts/_verify_cleanup.py
at start and end, by fixture id, GL rows through the documented privileged
path; re-read every touched table for its exact pre-test count, counting
audit_log and any table real activity writes to BY FIXTURE IDS ONLY. Roles
via user_roles (assert the app decides each). Read UNIQUE / CHECK / FK
constraints and unique indexes from the catalog before seeding (FAIL if none
are read). Reserved fixture prefixes that match no real row (assert it).
Support --self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context set ON the connection inside its transaction (a contextvar
    alone is not an RLS context); rolbypassrls = false asserted; an empty
    list never passes a check.
  - Compare Decimals as Decimals, never as text.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - Each fixture upload has distinct content; tear down EVERY row a service
    creates on your behalf (e.g. master securities), by the id it returns.
  - Count your own node tests by their file, not by title prefixes.
  - A test stub must match the CURRENT signature of what it replaces.
  - Never seed a state the database forbids (read the unique indexes first).
  - No check frozen against live state; static markers match by name, not by
    an exact signature line.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1d
  [Y] Gate OFF refuses everything; opening needs the provider + ADV
  [Y] Publish maker-checker, immutable, naming, no holdings file exposed
  [Y] Subscribe: agreement acknowledgement by version + platform approval
  [Y] Use: read-only, copy, new-version task + diff, adoption maker-checker,
      suspension
  [Y] Cross-org isolation both ways; members unchanged
  [Y] Fee accrual == invoice == GL; reversal on correction
  [Y] Provider notice on proposals (library)
  [Y] No model / network call; real gate OFF and unchanged; own routes;
      --check CURRENT; real + demo rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep13.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
