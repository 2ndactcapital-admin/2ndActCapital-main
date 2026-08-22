R2 BUCKET MIGRATION — 2ndactcapital-docs to hollisworks-docs.
4 tasks + verification. This is a real object-copy migration with a
LIVE RETRIEVAL DEPENDENCY, not a config change. R2, like S3, has no
rename operation.

WHY NOW: the structured-investments reference corpus will write
~250k objects. Doing this after that point is permanently expensive.
This is the last cheap moment, and "cheap" here still means a real
migration.

STANDING RULES: org_id never from request body; Decimal for money;
no interactive prompts; light theme if any UI is touched (none
expected). Every new table gets its RLS policy in the same migration
(no new tables expected here).

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue. The two
exceptions are the explicit STOP conditions in Task 1 and Task 2,
which are honest gates: if they trip, stop and report BLOCKED rather
than working around them.

DELIBERATELY OUT OF SCOPE — DO NOT DO THESE:
  - Deleting the old bucket. That is the irreversible step and it is
    a SEPARATE follow-up sprint after a soak period. The old bucket
    must survive this sprint intact.
  - Changing the R2 key structure or prefix conventions.
  - Adding any reference/ or non-org-scoped prefix (that belongs to
    the corpus sprint).
  - Any Chancery behavioural change.


=== TASK 1: DISCOVER — do not assume ===

Read the real, current code and report findings, THEN CONTINUE
IMMEDIATELY in the same response.

Establish and report all of the following:

  1a. Every reference to the bucket name. Grep the whole repo for
      '2ndactcapital-docs' and for the env var name that holds it.
      Report: env var name(s), every file that reads it, and any
      HARDCODED occurrence of the literal bucket string.

  2a. HOW KEYS ARE STORED. Inspect the documents table and any
      related storage columns. Determine whether stored values are
      bucket-RELATIVE keys (e.g. 'chancery/{org_id}/...') or FULL
      URLs / URIs with the bucket name embedded.

      *** STOP CONDITION ***
      If the bucket name is embedded in stored row data, this sprint
      changes shape — it becomes a data migration as well as an
      object copy. STOP and report BLOCKED with the exact column(s)
      and a row count. Do not attempt the data rewrite in this
      sprint.

  3a. HOW VERSIONING WORKS. PROJECT_STATUS records that Chancery
      Phase 2 proved "real R2 versioning (re-upload creates v2, v1
      retained)". Determine whether that is:
        (i)  R2 native object versioning, or
        (ii) application-level versioning (distinct keys per version,
             tracked in Postgres)
      This matters: a plain object copy does NOT preserve native
      version history. If (i), report it explicitly and report
      whether prior versions would be lost by a copy.

  4a. Whether any R2 object is served through a PUBLIC URL
      (pub-*.r2.dev, a custom domain, or a public bucket binding),
      versus everything going through backend-generated presigned
      URLs. A public hostname change has a different blast radius.

  5a. Whether the frontend references R2 at all. Per the standing
      environment-variable gotcha, a variable used by frontend code
      must exist in Vercel even if it is already in Render. Report
      whether any apps/web code touches the bucket name.

  6a. Object count and total size in the current bucket, and whether
      any bucket other than 2ndactcapital-docs is in use.

  7a. What copy tooling is actually available in this environment —
      rclone, aws-cli with --endpoint-url (R2 is S3-compatible), or
      the Cloudflare API. Report which, and prefer whichever is
      already used elsewhere in the repo.


=== TASK 2: CREATE AND COPY — old bucket untouched ===

  - Create the new bucket 'hollisworks-docs' in the same Cloudflare
    account and jurisdiction/region as the existing one. Match the
    existing bucket's settings, including versioning configuration
    if Task 1 found native versioning enabled.

    *** HONEST GATE ***
    If R2 credentials do not permit bucket creation, or the copy
    tooling cannot authenticate, STOP and report BLOCKED. Do not
    mock, simulate, or proceed past a real credential failure.

  - Copy ALL objects from 2ndactcapital-docs to hollisworks-docs,
    preserving keys byte-for-byte. Do not transform, re-prefix, or
    normalise any key.

  - The OLD BUCKET IS NOT MODIFIED. No deletes, no moves.

  - After the copy, prove completeness with real numbers, not a
    tool exit code:
      * object count in source == object count in destination
      * total byte size source == destination
      * report both figures explicitly

  - Spot-verify content, not just listings: for at least 5 objects
    spanning different key prefixes, fetch from BOTH buckets and
    compare content length and a hash of the bytes. Report the
    per-object comparison.

  Per the standing convention: VERIFY EFFECTS, NOT EXIT CODES. A
  sync tool reporting success while silently skipping objects has
  precedent elsewhere in this project.


=== TASK 3: CUTOVER — flip the read path ===

  - Update the R2 bucket env var (name discovered in Task 1a) in
    apps/api/.env for local, and report the EXACT variable name and
    new value that must be set in Render.

    Note: the sprint runner cannot set Render env vars. Report them
    clearly as a required manual step, and state plainly in the
    final summary that the production cutover is NOT complete until
    Render is updated and redeployed.

  - If Task 1a found any HARDCODED occurrence of the literal bucket
    string, replace it with a read of the env var. Do not leave a
    hardcoded bucket name behind — that is the same class of defect
    as a hardcoded brand string.

  - If Task 5a found frontend references, report the corresponding
    Vercel variable explicitly and separately.

  - ROLLBACK IS: revert the env var to the old bucket name and
    redeploy. The old bucket still holds every object, so rollback
    is complete and instant. State this in the summary.


=== TASK 4: UPDATE PROJECT STATUS ===

Update docs/PROJECT_STATUS.md in the same commit:
  - §10 Known Gaps: remove or amend the "R2 bucket name" item to
    reflect actual state.
  - Record the migration, the date, that the OLD BUCKET IS RETAINED
    pending a separate deletion sprint, and any finding from Task 1
    worth keeping (especially the versioning answer from 3a).
  - If anything came back BLOCKED, record that honestly rather than
    only recording success.


=== VERIFICATION: apps/api/scripts/verify_r2rename.py ===

Pass/fail output only. No interactive prompts. Idempotent. Teardown
at start AND end. Runs against the real environment.

The core assertion is a REAL FETCH, not an existence check — a copy
that silently missed objects passes "does the bucket exist" and
fails this:

  [ ] Reads the bucket name from the env var (NOT a hardcoded
      literal in the verify script itself)
  [ ] The new bucket is reachable and the configured bucket name is
      'hollisworks-docs'
  [ ] Object count in hollisworks-docs == object count in
      2ndactcapital-docs (report BOTH numbers)
  [ ] Total byte size matches between the two buckets (report both)
  [ ] For at least 10 documents rows with stored R2 keys: fetch each
      object THROUGH THE APPLICATION'S OWN RETRIEVAL PATH (the same
      service function Chancery uses, not a raw client) and assert
      non-empty bytes come back and content length matches the
      source bucket's object
  [ ] NEGATIVE CASE: a deliberately non-existent key returns a clean
      not-found through that same path, not a 500 or a silent empty
      success
  [ ] The OLD BUCKET STILL EXISTS and still contains its original
      object count — this sprint must not have deleted anything
  [ ] grep proves no remaining hardcoded '2ndactcapital-docs'
      literal anywhere in apps/api or apps/web (excluding
      docs/PROJECT_STATUS.md, this sprint prompt, and any historical
      migration file)

If fewer than 10 documents rows exist with stored keys, use however
many exist and REPORT THE ACTUAL COUNT rather than silently
weakening the assertion.
