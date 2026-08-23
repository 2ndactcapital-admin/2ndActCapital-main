STP POLICY + NOTE-TERMS REVIEW QUEUE.
5 tasks + verification.

WHAT THIS SPRINT DOES: routes newly-extracted note_terms rows either
straight through (auto-confirmed) or into a human review queue, based
on a (cik, form_type) trust policy — NOT a per-row or per-run checkbox.
Also builds the review queue screen itself, since a policy with nothing
to review against is unverifiable.

CURRENT STATE (confirmed live, do not re-derive):
  54 note_terms rows exist from the extraction sprint.
  28 are extraction_confidence='needs_review' (hazard disagreement).
  26 are extraction_confidence='high'.
  Of the 26 high-confidence rows, 48/54 total rows came from CIK
  associated with JPMorgan Chase Financial Company LLC / 424B2 —
  the single largest issuer/form pairing in the corpus so far.
  reference_filings.cik is the stable issuer identifier (text).
  reference_filings.filer_name is display-only, not a key.

THE ROUTING RULE — implement exactly this, do not simplify it:
  1. If the hazard ensemble disagreed on ANY field -> ALWAYS queue.
     No STP policy overrides this. This is non-negotiable: STP is a
     statement of trust in AGREEMENT, never a bypass of disagreement
     detection. The ensemble still runs on every row regardless of
     STP status — STP only affects what happens to an AGREEING row.
  2. Else if an ACTIVE stp_policy row exists for
     (reference_filings.cik, reference_filings.form_type) -> route
     straight through, no human touch, but the ensemble comparison
     result is still recorded on the row exactly as it would be for
     a queued row. STP changes visibility to a human, not what gets
     computed or stored.
  3. Else -> queue. This is the default for any issuer/form pairing
     that has never been explicitly trusted — "mixed batch or newer
     type" stays in the queue until someone grants STP for it.

WHAT THIS IS NOT — DO NOT BUILD THESE:
  - Underlying resolution. Still out of scope, still the next sprint.
  - Any change to the extraction or hazard-ensemble logic itself.
    This sprint only adds a ROUTING decision on top of confidence
    that already exists on each row.
  - A global "STP on/off" toggle. It must be scoped to (cik, form_type)
    pairs, per the design decision above — a single global switch
    defeats the purpose of "trust this issuer, not everything."
  - Auto-granting STP based on a clean run. STP is granted by a human
    action, explicitly, from the queue screen. Do not have the system
    infer trust from accuracy statistics alone.

STANDING RULES: org_id never from request body (N/A — global data);
Decimal for money; RLS policy in the same migration; light theme,
Signature palette from org_settings; no hardcoded hex; verify scripts
pass/fail only.

THERE IS NO HUMAN AVAILABLE. Report discovery, then continue
immediately in the same response. Exceptions are explicit STOP/BLOCKED
gates below.


=== TASK 1: DISCOVER — do not assume ===

  1a. Confirm reference_filings.cik and form_type are populated on all
      54 note_terms-linked filings (join via reference_filing_id).
      Report the actual distinct (cik, form_type) pairings present and
      their row counts — this is real data, use it, don't invent
      example issuers.

  1b. Confirm log_note_terms_correction (built in the extraction
      sprint) and the four-policy global RLS shape are both usable as
      reference patterns for this sprint's new table and endpoints.

  1c. Report whether any admin route convention already exists for a
      list+detail review screen (Chancery's DocumentReviewManager.jsx
      is the closest precedent per earlier discovery in this project —
      confirm it's still there and report its actual list/detail
      pattern so this screen matches conventions rather than inventing
      a new one).

  1d. Confirm super_admin gating pattern used for other staff-only
      admin surfaces (e.g. the S31 pricing viewer, if built) and reuse
      it exactly.


=== TASK 2: SCHEMA ===

Create `portfolio.note_terms_stp_policy`:
  id           uuid pk default uuid_generate_v4()
  cik          text not null
  form_type    text not null
  enabled      boolean not null default true
  granted_by   text            -- staff identifier, however the
                                -- platform currently records "who did
                                -- this" for admin actions; use the
                                -- existing convention, don't invent one
  granted_at   timestamptz not null default now()
  revoked_by   text
  revoked_at   timestamptz
  notes        text            -- free text, e.g. why this was granted

CHECK: form_type IN ('424B2','FWP')
UNIQUE (cik, form_type) WHERE enabled = true
  -- exactly one ACTIVE policy per issuer/form pairing; a revoked one
  -- can coexist with history, a new grant creates a new row rather
  -- than mutating the audit trail of the old one

RLS: four-policy global shape, matching every other table in this
schema (global read, super-admin write). Copy verbatim from an
existing table — do not re-derive.

Add a column to securities_global_note_terms (additive migration):
  routing_decision  text     -- 'queued' | 'stp' | NULL for rows that
                              -- predate this sprint
  routed_at         timestamptz
CHECK: routing_decision IN ('queued','stp') OR routing_decision IS NULL

Do NOT backfill routing_decision on the 54 existing rows — they were
created before this policy existed and should stay NULL, honestly
representing that no routing decision was made for them. Document this
explicitly rather than guessing a value.


=== TASK 3: ROUTING LOGIC ===

apps/api/services/note_terms_routing.py

  def route_note_terms_row(pool, note_terms_row) -> Literal['queued','stp']

Implements the exact three-step rule above. Called at the end of the
extraction pipeline (find the right insertion point in
note_terms_extraction.py from the prior sprint — do not duplicate the
extraction logic, call into this as a final step).

  async def grant_stp(pool, cik, form_type, granted_by, notes) -> uuid
  async def revoke_stp(pool, cik, form_type, revoked_by) -> None

Both are super-admin-gated actions, not called from unauthenticated
or org-scoped contexts.


=== TASK 4: REVIEW QUEUE — API + UI ===

Endpoint: GET /api/v1/admin/pricing/note-terms/queue
  Returns note_terms rows where extraction_confidence='needs_review'
  OR routing_decision='queued', joined to reference_filings for
  filer_name/cik/form_type/extracted_text, ordered by filing_date desc.
  super_admin only.

Endpoint: POST /api/v1/admin/pricing/note-terms/{id}/resolve
  body: { field: str, chosen_value: str, source: 'primary'|'secondary'|'manual' }
  Writes through log_note_terms_correction (target_type='note_terms').
  Updates the row's field value and field_status for that field to
  'extracted'. super_admin only, org_id not applicable.

Endpoint: POST /api/v1/admin/pricing/stp-policy
  body: { cik: str, form_type: str, notes: str }
  Calls grant_stp. super_admin only.

Endpoint: DELETE /api/v1/admin/pricing/stp-policy/{id}
  Calls revoke_stp.

Frontend: apps/web/app/admin/pricing/note-terms-queue/page.tsx
  Nav entry gated on super_admin, matching Task 1d's pattern.

  List view: queued rows, showing issuer (filer_name), form_type,
  filing_date, and which hazard field(s) disagreed. Signature palette,
  light theme, TanStack DataGrid per house convention.

  Detail view (per row): primary vs. secondary answer side by side for
  each disagreed field. Below it, render extracted_text sliced at
  [source_char_start:source_char_end] — the actual source sentence,
  not a page reference. Resolve action writes via the endpoint above.

  From the detail view, when the LAST queued row for a given
  (cik, form_type) pairing is resolved, offer: "No more queued items
  for {filer_name} {form_type}s — grant straight-through processing
  for this issuer/form going forward?" with a text field for notes and
  a confirm button. This is the natural grant moment per the design
  discussion — do not build a separate settings page for this.

  A small STP POLICY panel (list of active policies, with a revoke
  action) — simple table, not a separate screen.


=== TASK 5: UPDATE PROJECT STATUS ===

Update docs/PROJECT_STATUS.md: routing logic added, STP policy table,
review queue screen, and explicitly note the 54 pre-existing rows have
routing_decision=NULL by design.


=== VERIFICATION: apps/api/scripts/verify_notetermsrouting.py ===

Pass/fail only. No prompts. Idempotent. Teardown at start AND end.
Use APP_SERVICE_DATABASE_URL, fail loudly if it cannot connect.

  [ ] note_terms_stp_policy exists, RLS enabled, 4 policies, no org_id
  [ ] UNIQUE constraint allows a revoked + a new active policy for the
      same (cik, form_type) to coexist, but rejects two ACTIVE ones
  [ ] ROUTING RULE PROOF 1: a fixture row with a hazard disagreement
      routes to 'queued' even when an active STP policy exists for its
      (cik, form_type) — this is the core non-negotiable assertion
  [ ] ROUTING RULE PROOF 2: a fixture row with agreement AND an active
      STP policy routes to 'stp'
  [ ] ROUTING RULE PROOF 3: a fixture row with agreement and NO policy
      routes to 'queued' (safe default)
  [ ] An STP'd row still has its ensemble comparison result stored
      identically to a queued row — STP does not skip computation
  [ ] The 54 pre-existing rows all have routing_decision IS NULL,
      unchanged by this sprint (assert this explicitly, not just that
      the migration ran)
  [ ] grant_stp / revoke_stp are rejected under app_service without
      is_super_admin — assert the rejection
  [ ] Resolve endpoint: resolving a queued row's disagreed field logs
      via log_note_terms_correction with target_type='note_terms' and
      updates field_status for that field to 'extracted'
  [ ] Queue endpoint returns exactly the rows matching the query
      definition above — construct a fixture with a known mix of
      queued/stp/high-no-policy rows and assert the exact set returned
  [ ] Global read on note_terms_stp_policy works under app_service
      with no org context set
