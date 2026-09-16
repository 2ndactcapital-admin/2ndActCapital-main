-- LiteLLM Phase E — per-task effort. Two nullable columns on ai_decision_log
-- so the fallback-with-effort decision (§4 of docs/LITELLM_D2_E_SPEC.md) is
-- visible after the fact, not just implemented in code:
--
--   effort_requested — the org's ai.effort.<key> value for this task, if any
--                       (independent of whether the model actually used
--                       supports it). NULL when no effort is assigned.
--   effort_used      — the effort level ACTUALLY sent to the provider on
--                       this attempt. NULL when effort_requested was NULL,
--                       OR when it was requested but silently dropped
--                       because model_used does not report
--                       supports_reasoning: true (the Task 3 decision:
--                       drop, don't fail). effort_requested IS NOT NULL AND
--                       effort_used IS NULL is exactly that dropped case,
--                       queryable directly — no separate flag needed.
ALTER TABLE ai_decision_log ADD COLUMN effort_requested text;
ALTER TABLE ai_decision_log ADD COLUMN effort_used text;
