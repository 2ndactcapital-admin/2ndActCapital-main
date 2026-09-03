FEE MODULE — SPRINT fee43 (invoices, reconciliation, GL posting —
open question #3, finally resolved). 3 tasks + verification. Part 1
SQL (ledger_books, journal_entries.vehicle_kind, fee_invoices,
fee_receipts) is already applied by Joe directly via Supabase MCP —
confirm it live before writing any code.

THE CENTRAL DECISION THIS SPRINT MAKES: journal_entries.vehicle_id has
NO foreign key (confirmed live) and has, in the single journal_entries
row that has ever existed, only ever pointed at an SPV — by
convention, never by schema enforcement. This sprint generalizes that
convention explicitly via the new vehicle_kind column: vehicle_id may
now point at an spvs.id (vehicle_kind='SPV', unchanged) OR a
ledger_books.id (vehicle_kind='LEDGER_BOOK', new). ledger_books is
seeded with exactly two rows this sprint must create:
RIA_OPERATING (Access's own advisory-fee revenue) and CLUB_DUES (the
501(c)(6) club's membership revenue) — kept as separate books because
they are legally distinct businesses sharing one org_id, and conflating
their revenue would misstate both entities' own financials.

WHAT THIS RESOLVES, concretely:
  - fee36's F4o (GL posting stub) — fee_run_lines can now post for
    real: ASSET_MANAGEMENT/PLANNING/TRANSACTION product_types post to
    RIA_OPERATING; CLUB_DUES posts to CLUB_DUES. SPV/STRUCTURED_
    INVESTMENT revenue types keep posting within their own SPV's
    existing vehicle_id (vehicle_kind='SPV', unchanged behavior).
  - fee42b's 6l (carry runs don't post to GL) — a POSTED spv_carry_run
    can now post carry_to_gp. Confirm in Task 1 whether this posts
    within the SPV's own book (a carried-interest-payable line) or to
    a GP-entity book — no GP legal entity exists in this schema today
    (confirmed: no entities row for a management-company/GP entity),
    so if that turns out to be needed, REPORT it as a real gap rather
    than inventing a GP entity silently. Posting within the SPV's own
    existing vehicle_id is very likely the correct, achievable answer
    for this sprint; do not force a GP-entity model into existence.
  - v_capital_accounts' structural break (found by fee42b) is NOT
    fixed by this sprint directly — that view's brokenness is about a
    different dimension (journal_lines.dim_member_series_id) than what
    this sprint touches. Confirm in Task 1 whether this sprint's new
    postings happen to populate that dimension as a side effect; if
    not, say so plainly rather than implying it's fixed.

CONTEXT, settled, do not re-derive:
- chart_of_accounts has no advisory-fee or club-dues revenue account.
  This sprint must add real accounts (e.g. a 4000-series revenue
  account for RIA advisory fees, a distinct one for club dues),
  org-scoped, matching the existing chart's hierarchy conventions
  (parent_code, account_type, normal_balance) — read the existing
  chart's actual shape in Task 1 before inventing account codes that
  don't fit its numbering convention.
- The omnibus custodian statement is ingested via a Chancery document
  upload, per the original design doc, NOT an API integration. Confirm
  Chancery's real document-upload/extraction mechanism (used elsewhere
  in the platform) and reuse it rather than building a second upload
  path.
- fee_receipts.variance is billed minus received; NULL until computed.
  Reconciliation status starts UNRECONCILED, becomes MATCHED when
  variance is within a defensible tolerance (define and justify the
  tolerance, do not pick an arbitrary number silently) or EXCEPTION
  otherwise, requiring reviewed_by/reviewed_at together (already a
  paired CHECK) before an exception can be closed.

OUT OF SCOPE: any Altruist-API-shaped integration for the omnibus
statement (Chancery upload only, per the design doc). Building a GP
legal entity model (report as a gap if Task 1 finds it's genuinely
needed). Retroactively posting fee31-fee42b's already-POSTED runs to
the GL — this sprint's posting logic applies going forward; backfilling
history is a separate, explicit decision Joe would need to make.

STANDING RULES: org_id never from request bodies. Decimal everywhere.
No interactive prompts. Additive-first — the existing single
journal_entries row and its SPV-only posting behavior must be provably
unchanged by this sprint (same standard as fee42's spvs.mgmt_fee_pct
proof).

=== TASK 1: Discover, don't assume ===
Confirm live: all four new/altered objects exactly as deployed. Read
chart_of_accounts' real existing rows to match its numbering/hierarchy
convention before adding new accounts. Read posting_templates and
posting_template_lines' real shape (fee_runs' current GL-posting stub
presumably already knows how to call these, even though it currently
writes nothing) to reuse the existing posting mechanism rather than
inventing a parallel one. Confirm Chancery's real document-upload
mechanism. Confirm whether an SPV entity model has anything resembling
a GP/manager reference (spvs table columns, or elsewhere) before
concluding one needs to be invented or deferred. Report all findings,
especially the account-numbering convention and the GP-entity
question, before writing code.

=== TASK 2: GL posting — fee_run_lines and spv_carry_run_lines ===
Wire the actual posting: when a fee_run reaches POSTED (fee36) or a
spv_carry_run reaches POSTED (fee42b), generate real journal_entries/
journal_lines via the existing posting_templates mechanism, crediting
the correct revenue account (RIA_OPERATING or CLUB_DUES book per
product_type, or the SPV's own book for SPV-type revenue and for
carry) and debiting the correct receivable/payable account. This is
additive to both tables' existing immutable-once-posted discipline —
do not reopen a POSTED run to attach a journal_entry; the posting
happens as part of the SAME transaction that moves status to POSTED,
or is refused if it cannot complete cleanly (a POSTED fee_run with no
resulting journal_entry, because the posting failed silently, would be
a real revenue-recognition bug). Emit revenue_events (fee39) unchanged
— this sprint adds a GL-side effect, it does not touch fee39's
existing emission logic.

=== TASK 3: Invoices + reconciliation ===
fee_invoices: generate a real invoice for a POSTED fee_run's
household-scoped lines, with the disclosure language fee41's narrative
system already knows how to produce (reuse it, do not write a second
disclosure text generator). fee_receipts + reconciliation: accept an
omnibus statement via Chancery upload, extract per-account/per-
household received amounts, and write fee_receipts rows computing
variance against the corresponding fee_run_lines. A statement whose
allocated total doesn't tie to the omnibus total raises a real,
reviewable exception — never silently posts. Build the exception
queue (fee_receipts filtered to reconciliation_status='EXCEPTION')
sufficient to review and close exceptions with reviewed_by/reviewed_at.

=== VERIFICATION ===
Write scripts/verify_fee43.py — pass/fail only, app_service for RLS,
teardown discipline.
Assert:
  1. All new/altered objects deployed exactly as expected; the single
     pre-existing journal_entries row is BYTE-IDENTICAL after this
     sprint, including vehicle_kind backfilled to 'SPV' — same
     additive-first proof standard as fee42's spvs columns.
  2. Both ledger_books rows (RIA_OPERATING, CLUB_DUES) exist and are
     distinguishable; the new chart_of_accounts revenue accounts exist
     and follow the existing numbering convention (not an invented
     scheme that clashes with it).
  3. Posting a real fee_run with ASSET_MANAGEMENT lines produces
     journal_entries pointed at the RIA_OPERATING ledger_book; a
     fee_run with CLUB_DUES lines posts to the CLUB_DUES book — prove
     both directions on the SAME run if it has mixed product_types,
     not just two separate single-product runs.
  4. Posting a POSTED spv_carry_run's carry_to_gp produces a real
     journal_entry, correctly scoped per Task 1's finding (SPV's own
     book, or a reported gap if a GP entity is genuinely required).
  5. A fee_run or spv_carry_run whose GL posting fails partway does
     NOT end up POSTED with no journal_entry — the whole transition
     is atomic, proven by forcing a failure and checking the run's
     final state.
  6. An invoice's disclosure text is generated via fee41's existing
     mechanism, not a second one — prove by comparing output, not by
     assuming based on the code path taken.
  7. A reconciliation exception is raised when the omnibus total
     doesn't tie, and is NOT silently absorbed into a posted state;
     closing it requires both reviewed_by and reviewed_at.
  8. Cross-org isolation on all four new/altered objects via
     app_service.
  9. No table's row count differs from its pre-test count after the
     script exits, EXCEPT journal_entries/journal_lines which the
     script's own posting checks legitimately create — teardown must
     still remove those, not just report they existed.
Report actual results, including the honest GP-entity finding from
Task 1, then stop.
