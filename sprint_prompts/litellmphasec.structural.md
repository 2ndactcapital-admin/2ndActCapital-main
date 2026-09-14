LITELLM PHASE C — VOYAGE EMBEDDINGS THROUGH THE PROXY. 6 tasks +
verification. Phases A and B are complete and proven (25/25) —
the proxy is deployed, authenticated, has a registered model, and
all production TEXT calls route through it. This sprint brings
EMBEDDINGS onto the same path.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- Voyage currently BYPASSES the router entirely: raw httpx, its own
  ai.embedding.* org_settings namespace, no fallback chain, and it
  writes NO ai_decision_log row. This is the gap Phase C closes —
  confirmed by the original LiteLLM discovery sprint.
- Text calls already route correctly through
  services/extraction.py's single chokepoint via LiteLLM's
  Anthropic-shaped /v1/messages route. Embeddings will need
  LiteLLM's OpenAI-shaped /v1/embeddings route instead — CONFIRM
  the real path against the live proxy, do not assume.
- The live proxy has exactly one registered model today:
  'claude-sonnet' -> anthropic/claude-sonnet-4-6. A Voyage model
  must be registered as its own deployment for this to work —
  registering it is part of this sprint, via POST /model/new with
  the master key.
- LITELLM_ROUTING_DISABLED=1 is the real, proven rollback for text
  calls. Embeddings need an equivalent, and it must be proven the
  same way — by genuine ABSENCE from LiteLLM's spend log after the
  full flush window, not just by a result coming back.
- app_service CANNOT read the litellm schema (permission denied —
  correct least-privilege behavior). Read LiteLLM's own spend data
  via its admin API (GET /spend/logs), not by querying the schema.
- ai_decision_log reads/writes REQUIRE set_rls_context — without
  it you get SILENT RLS denials (zero rows, no error), which looks
  like an empty table rather than refused access.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO
background-process notification mechanism in this tool — nothing
will ever notify you that a script finished. Never wait for one.
Run every script SYNCHRONOUSLY in the foreground and read its
output directly. Never print VOYAGE_API_KEY, LITELLM_MASTER_KEY,
or ANTHROPIC_API_KEY, or any value that could contain one.

*** CRITICAL — THE EMBEDDING COMPATIBILITY RULE: embeddings from
different models are NOT comparable. Changing the embedding model
without re-indexing the corpus silently degrades search quality
with no error and no obvious symptom. Per the design doc, this
sprint must build FRICTION, not a lock: a real confirmation
dialog showing actual corpus size and actual re-indexing cost
before any embedding-model change is accepted. ***

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. The REAL current Voyage call path: exact file, exact
      function, exact httpx call, and every real ai.embedding.*
      org_settings key it reads. Report the real current model
      name in use.
  1b. Confirm LiteLLM's real embeddings endpoint path against the
      LIVE proxy (/v1/embeddings vs /embeddings — probe both,
      report which actually answers).
  1c. Where are embeddings actually STORED, and how many rows
      exist right now? Find the real table/column (a pgvector
      column, a jsonb array, something else) and report the real
      corpus size — this number is what the Task 4 dialog must
      show, so it must be real, not estimated.
  1d. Confirm whether ANY re-indexing mechanism exists today —
      a script, an endpoint, a job. Report honestly if nothing
      exists; that is a real finding, not a failure to search.

=== TASK 2: REGISTER THE VOYAGE MODEL ON THE PROXY ===
Register Voyage as a real model deployment via POST /model/new
using the master key, with api_key as os.environ/VOYAGE_API_KEY
(the same os.environ/ indirection the Anthropic deployment uses —
never a literal key). Use the real model name Task 1a found.
Confirm it persisted by re-reading GET /v1/models AND by the
admin API, not by trusting the POST's own response.

=== TASK 3: ROUTE EMBEDDINGS THROUGH LITELLM ===
Change the real Voyage call path (Task 1a) to call LiteLLM's
embeddings endpoint instead of Voyage directly. Bring it onto the
SAME disciplines text calls already have:
  - a real fallback chain (reuse the ai.model.fallback_chain
    pattern; confirm whether ai.embedding.* needs its own
    equivalent key and report the decision)
  - a real ai_decision_log row per call, same shape as text calls
    (set_rls_context required — see CONFIRMED REAL FACTS)
  - a real rollback switch, equivalent to
    LITELLM_ROUTING_DISABLED for text

=== TASK 4: THE RE-INDEXING FRICTION DIALOG ===
Build the real confirmation flow for an embedding-model change:
before accepting the change, show the REAL corpus size (Task 1c's
actual number) and a REAL estimated re-indexing cost computed
from that count and the new model's real per-token price. Require
explicit confirmation. This is friction, NOT a hard lock — an
admin who confirms may proceed. If Task 1d found no re-indexing
mechanism exists, say so plainly in the dialog rather than
implying one will run automatically.

=== TASK 5: REAL PROOF ===
  - Report Task 1's four findings explicitly.
  - A real embedding call succeeds end-to-end through LiteLLM and
    returns a real vector of the expected dimensionality.
  - LiteLLM's own spend log (via GET /spend/logs) records that
    call with non-zero spend.
  - ai_decision_log records it too, in the same shape text calls
    use — proving embeddings are no longer invisible to the
    decision log.
  - The fallback chain genuinely walks on a forced first-model
    failure.
  - The rollback switch genuinely bypasses LiteLLM — proven by
    ABSENCE from the spend log after the full flush window.
  - The friction dialog shows the REAL corpus count from Task 1c,
    not a hardcoded or estimated number — proven by comparing
    against a direct count.
  - An existing stored embedding is still readable and still
    matches its original dimensionality — proving this sprint did
    not silently invalidate the existing corpus.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md: Phase C complete,
embeddings now routed and logged, the real re-indexing gap
recorded honestly if Task 1d found none. Phase D (model
pick-list UI) next.

=== VERIFICATION: apps/api/scripts/verify_litellmphasec.py ===
Pass/fail only. MUST hydrate its own secrets from Doppler over
HTTPS at startup (the verify_rlscutover.py / verify_tamodel1.py
pattern — run_sprint.sh's Step 3 does NOT use doppler run --).
Never print any secret.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Voyage is registered as a real, persisted proxy deployment
  [Y] A real embedding call succeeds through LiteLLM, correct
      dimensionality
  [Y] LiteLLM's spend log records it with non-zero spend
  [Y] ai_decision_log records it in the same shape as text calls
  [Y] The fallback chain walks on a forced failure
  [Y] The rollback switch is proven by absence from the spend log
  [Y] The friction dialog shows the REAL corpus count
  [Y] Existing stored embeddings remain readable at their original
      dimensionality
  [Y] Teardown: zero leftover rows
