MINI-BEDROCK MODEL CONFIG. Small, focused sprint — 2 tasks +
verification. Do NOT expand scope beyond what's listed here.

CONTEXT: org_settings already exists (S24) — extensible
key/value table (org_id, setting_key, setting_value jsonb,
category, is_public, updated_at, updated_by). The goal: make
which AI model gets called a CONFIG value per org, not a
hardcoded string scattered through the codebase — so switching
a client, or the whole platform, to a different model or a
different provider (e.g. AWS Bedrock in the future) becomes a
settings change, not a code change.

STANDING RULES: light theme (whites/creams) if any UI is
touched; no interactive prompts; org_id never from request body.

=== TASK 1: Discover current model usage — DO NOT GUESS ===
Grep the ENTIRE codebase (apps/api primarily) for:
  - Every hardcoded Anthropic model string (e.g. anything
    matching "claude-" followed by a version/name)
  - The central AI-calling helper(s) if they exist — likely
    named something like call_claude_text / call_claude_json
    (search for these exact names first; if they don't exist
    under these names, find whatever function(s) actually wrap
    the Anthropic API client calls across the codebase)
Report: the exact current model string(s) found, and every file/
call site that either (a) uses the central helper, or (b) calls
the Anthropic client directly with its own hardcoded model
string (this second category is exactly what needs fixing).

=== TASK 2: Config-driven model resolution ===
Using WHATEVER current model string(s) Task 1 discovered
(preserve exact current behavior — do not change which model
actually gets called for existing functionality):
  - Add three new org_settings keys, seeded onto the real
    default org (00000000-0000-0000-0000-000000000001) with
    category 'ai':
      ai.model.default   -> the current hardcoded model string
                             found in Task 1 (as-is, preserving
                             behavior)
      ai.model.provider  -> "anthropic"
      ai.model.fallback  -> same as ai.model.default UNLESS
                             Task 1 finds an existing fallback/
                             retry pattern already in the code —
                             if so, use whatever that already
                             falls back to. Do NOT invent a new
                             fallback choice.
  - Add these three keys to the DEFAULT_SETTINGS map in
    apps/api/services/org_settings.py (same fallback-for-orgs-
    without-explicit-settings pattern used for the S24 brand
    keys), using the same current values as the seed.
  - Ensure the central AI-calling helper (found/confirmed in
    Task 1) resolves its model from org_settings (via the
    existing get_setting service) rather than a hardcoded
    constant. If MULTIPLE call sites bypass the central helper
    entirely (calling the Anthropic client directly), refactor
    them to go through the central helper instead — this is the
    real fix, not just adding config keys nobody reads.
  - Do NOT build a UI for editing these settings in this sprint
    (that can reuse the existing OrgSettingsEditor.jsx pattern
    later if wanted) — backend config-resolution only.

=== VERIFICATION ===
Write apps/api/scripts/verify_minibedrock.py, same pattern as
prior verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] ai.model.default / ai.model.provider / ai.model.fallback
      exist in org_settings for the default org, with the
      EXACT values discovered in Task 1 (not a guessed value)
  [Y] The same three keys exist in DEFAULT_SETTINGS with
      matching values
  [Y] get_setting(org_id, 'ai.model.default') returns the
      correct value
  [Y] Create a second test org with NO explicit ai.model.*
      settings — confirm the central helper (or get_setting)
      falls back to DEFAULT_SETTINGS correctly for that org
  [Y] Grep-based check: confirm zero remaining hardcoded model
      strings outside (a) the DEFAULT_SETTINGS fallback map
      itself and (b) this seed/migration — every other call site
      must resolve through org_settings now
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). Push when 100%
pass.
