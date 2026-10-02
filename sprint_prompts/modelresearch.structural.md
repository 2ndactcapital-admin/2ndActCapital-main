MODEL RESEARCH PAGE — read-only grid of every model LiteLLM knows about.

YOU ARE THE SPRINT. Do this work yourself, now, in this session. Do NOT run
run_sprint.sh. Do NOT launch anything in the background. Run every command
synchronously in the foreground and read its output. Nothing will ever notify
you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it. Stop once it
is written and your other tasks are complete.

=== WHAT JOE ASKED FOR ===

A page where admins research AI models. On load it shows our standard grid
with filters, listing the models available via LiteLLM: provider, model name,
model version, cost columns, and other key characteristics — sortable and
filterable — with a Refresh button. Accessible to the super-admin and to org
admins. Read-only: this page never changes anything.

=== DECISIONS — do not re-litigate ===

1. SHOW EVERY MODEL LITELLM KNOWS ABOUT, not only the ones registered on our
   proxy. Only three are registered (claude-haiku, claude-sonnet, voyage-3.5),
   which is useless for research. Source: LiteLLM's model price list (roughly
   two thousand entries). Each row is flagged:
     - "Live on our proxy" — present in the proxy's /model/info
     - "In platform catalog" — present in public.platform_model_catalog, with
       its availability (available / deprecated / disabled)
   Also include each entry of public.ai_system_one_models (Jev) as a row,
   kind = 'System One', since LiteLLM's price list on our version won't hold it.

2. PREFER THE PROXY AS THE SOURCE. Task 1 finds out what the proxy (pinned at
   LiteLLM v1.96.2 — do NOT upgrade) can return. If it cannot return the full
   price list, the API may fetch LiteLLM's published price list
   (model_prices_and_context_window.json from the BerriAI/litellm GitHub
   repository) SERVER-SIDE — never from the browser — and label the source on
   the page. Either way the page shows a "Prices as of" date and the source
   used, so stale prices are never mistaken for current ones.

3. ACCESS: super-admin, plus holders of manage_org_settings (org admins).
   Resolve by permission using the existing async helpers
   (can_manage_org_settings with await and pool) — do not check a role
   string. Members are refused with 403 by the API, not merely hidden by the
   menu. The menu entry renders only inside the permission check, with NO
   truthy fallback; a missing permission envelope must fail CLOSED.

4. NO SECRETS LEAVE THE SERVER. /model/info returns litellm_params, which can
   hold api_base and credential references. Build every row from an explicit
   allow-list of fields; never pass a provider response through.

=== COLUMNS ===

Provider; model name; version (parse a dated suffix such as -20250807 where
one exists — leave it BLANK where none exists, never invent one); mode (chat,
embedding, …); input $ per 1M tokens; output $ per 1M tokens; batch input and
output $ per 1M where the price list has them; cached-input $ per 1M where
present; cost per typical call; max input tokens; max output tokens;
capability flags (function calling, structured output, vision, reasoning,
prompt caching, web search); deprecation date; live on our proxy; in platform
catalog plus availability; kind (LLM / embedding / System One).

"Cost per typical call" uses an editable token profile at the top of the page,
defaulting to 4,000 input and 600 output tokens, computed in the browser so
changing it is instant. Show it also as cost per 1,000,000 calls.

=== GRID ===

Reuse the existing DataGrid component, as every prior UI sprint did. Sort on
every column. Filters: provider (multi-select), mode, kind, each capability
flag, live-on-proxy, in-catalog, price ranges, and free-text search on model
name. Column show/hide. It must stay responsive at about two thousand rows —
Task 1 finds out whether DataGrid needs server-side paging for that.

REFRESH: re-fetches from the source, bypassing any server cache, and updates
"Last refreshed" and "Prices as of". A short server-side cache (say one hour)
is fine otherwise.

OUT OF SCOPE: reloading the proxy's own price list (that is the existing
litellm.reload_model_cost_map action), registering models, editing the
catalog, any write of any kind.

Light theme, existing design tokens, no hardcoded colors.

=== FACTS THAT CHANGE DECISIONS ===

- app_service cannot read the litellm schema. Use the proxy's admin API
  (master key, PROXY_ADMIN) from the server side only.
- RLS: reads without context set return None / 0 / [] SILENTLY. Set context on
  every read of platform_model_catalog and ai_system_one_models.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Which proxy endpoints on v1.96.2 return the FULL price list versus only
      registered models. Probe them; do not assume names.
  1b. How stale the proxy's price list is: compare its entry count and a few
      recent models against the published list. Report the gap plainly.
  1c. Every field /model/info returns, so the allow-list is written against
      reality.
  1d. Whether DataGrid handles ~2,000 rows client-side or needs paging.
  1e. Where org-admin pages live in the menu, and the real permission
      envelope pattern to reuse.

=== TASK 2: API === one read-only endpoint returning rows plus metadata
(source, prices-as-of, last-refreshed), with a refresh parameter.

=== TASK 3: PAGE AND MENU ENTRY === per the sections above.

=== TASK 4: STATUS === update docs/PROJECT_STATUS.md, including 1b's finding.

=== VERIFY: apps/api/scripts/verify_modelresearch.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print 'TOTAL: N PASS, M FAIL'
and exit non-zero on failure. Hydrate secrets from Doppler over HTTPS at
startup (verify_ensemblesystemone.py is the pattern).

Verify-script rules — each broke a real script:
  - check(passed, label, detail): condition FIRST; the helper asserts
    isinstance(passed, bool).
  - Never derive any unique value from a slice of a fixture UUID.
  - Set RLS context on EVERY read.
  - Clear audit_log, assistant_activities and agent_proposals BEFORE deleting
    any fixture users.

Assertions:
  [Y] Task 1's five findings reported
  [Y] super_admin gets 200; an org admin gets 200; a plain member gets 403 on
      the IDENTICAL request
  [Y] The menu entry renders for both admin types and not for a member, with
      no truthy fallback
  [Y] Row count matches the source's model count (plus System One rows)
  [Y] "Live on our proxy" flags match /model/info exactly — every registered
      model flagged, nothing else flagged
  [Y] "In platform catalog" flags and availability match
      platform_model_catalog exactly
  [Y] The Jev row is present, kind 'System One'
  [Y] Per-1M prices equal the source's per-token price x 1,000,000 exactly,
      on a sample of rows
  [Y] No row and no response contains api_key, api_base, or any credential
      field — grep the raw response text
  [Y] Refresh genuinely re-fetches: last-refreshed changes and the cache is
      bypassed
  [Y] Version is blank, not invented, for models with no dated suffix
  [Y] The endpoint performs no writes: row counts of every table it touches
      are unchanged
  [Y] npm run build exits 0
