LITELLM INTEGRATION — DISCOVERY ONLY. Read-only investigation, NO
CODE CHANGES. 7 tasks, findings-only. This establishes real facts
the eventual design document and sprints will be built from —
mirroring the earlier tenant-boundary-security discovery sprint's
own pattern (report, do not modify).

THERE IS NO HUMAN AVAILABLE. Report each task's findings, THEN
CONTINUE IMMEDIATELY to the next task in the same response —
never stop and wait.

DO NOT: write any code, create any file other than the findings
document specified in Task 8, modify any schema, install
anything. This is pure discovery.

=== TASK 1: every real AI call site ===
Grep the entire apps/api codebase for every call to
call_claude_text, call_claude_json, or any direct Anthropic/
OpenAI/other-provider SDK usage. For each site found, report: the
file/function, what real task it represents (in plain language),
whether it currently reads from an org_settings ai.model.* key or
is hardcoded, and if hardcoded, to what model. Cross-reference
against the four KNOWN keys (ai.model.assistant, ai.model.default,
ai.model.document_classifier, ai.model.fallback) — report every
call site that does NOT map to one of these as a genuine gap.

=== TASK 2: the "teams" naming collision ===
Confirm whether public.teams/team_members (the staff-grouping
concept already used by portfolio.udf_definitions'
owner_scope='team') is a real, distinct concept from what a
LiteLLM "Team" would represent (a whole client org, per open-
source tier constraints). Report the real, current usage of
public.teams throughout the codebase — every place it's
referenced — so the eventual design can resolve this naming
collision deliberately rather than accidentally.

=== TASK 3: Voyage/embedding call sites ===
Read the REAL current Chancery Phase 11b embedding-provider
selection mechanism (the Mini-Bedrock pattern for embeddings).
Report exactly how it works today: what determines Voyage is
used, how the "other providers listed but rejected" behavior is
implemented, and whether this could plausibly be absorbed into a
LiteLLM-routed system or has real, structural reasons it's
separate.

=== TASK 4: voice call sites ===
Find every real, current usage of AWS Polly and AWS Transcribe in
the codebase. Report exactly what each call site does, and
whether the integration shape (SDK calls, credential handling,
any Polly/Transcribe-specific features relied upon) would make
swapping to a LiteLLM-routed voice provider (e.g. Grok, OpenAI
realtime) a simple config change or a genuinely larger
undertaking. Do not guess — report only what the real code shows.

=== TASK 5: real Render service topology ===
Report the REAL current services deployed on Render for this
project (the API service confirmed throughout tonight; any
others). This informs where a new LiteLLM proxy service would
actually be added — report enough real detail (service count,
general shape) to inform that decision later, without designing
it now.

=== TASK 6: real current secrets-handling pattern ===
Report exactly how existing third-party credentials (AWS
Textract/SES, Voyage) are currently stored and injected into the
running application — which env vars, on which Render service,
confirmed live pattern. This informs where LITELLM_MASTER_KEY /
LITELLM_SALT_KEY / provider credentials should eventually live.

=== TASK 7: S27 TaskRouter's real current shape ===
Read the REAL current ai_decision_log table and the per-org
ordered fallback chain mechanism (services/task_router.py or
equivalent) — confirm its exact real columns, exact real
invocation points, and exactly how the fallback chain is
currently read from org_settings. This is the system the eventual
design must decide how to relate to LiteLLM's own routing/
fallback/spend-logging.

=== TASK 8: WRITE FINDINGS ONLY ===
Write docs/LITELLM_DISCOVERY_FINDINGS.md — a real, structured
report of everything found in Tasks 1-7. This is a discovery
record, not a design document — report facts, not
recommendations. Commit this file. No other file should be
created or modified.

=== VERIFICATION ===
There is no code to verify — this is discovery-only. Confirm
docs/LITELLM_DISCOVERY_FINDINGS.md was created and contains real,
specific findings (not placeholders) for all 7 tasks. Confirm via
git diff that NO file other than this one was created or changed.
