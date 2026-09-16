# LiteLLM Phases D2 & E — Model Catalog and Task Assignment Specification

**Status**: specification, agreed. D2 partially in flight (see §6 for what the running sprint does and does not cover). Supersedes the looser "model pick-list UI" line in the phasing table.

---

## 1 · The shape, end to end

1. **Hollisworks super-admin curates** which models the platform offers.
2. **Org admin selects** which of those its org may use, and supplies provider credentials (already built — D1a).
3. **Org admin assigns** a model per AI task, and an effort level where the model supports it.
4. **New AI tasks appear automatically** in the assignment list as they are added to the platform.
5. **Hollisworks grants platform-key access per provider per org** — not per task (see §5).

---

## 2 · Store policy, not metadata

**The catalog stores the model identifier and Hollisworks' own policy about it. It does NOT store the model's metadata.**

Pricing changes, context windows grow, LiteLLM updates its cost map. A catalog that copies `input_cost_per_token` at curation time shows a stale price forever. Fetch live from `GET /model_group/info` at read time.

This follows the precedent set in Phase C, where the re-indexing confirmation dialog pulls live pricing from the proxy rather than a local table — deliberately, so the cost shown to an admin cannot silently drift from the cost actually incurred.

**What `/model_group/info` genuinely returns** (probed live, confirmed):

| Field | Example | Use |
|---|---|---|
| `max_input_tokens` | 1000000 | Context-fit display |
| `input_cost_per_token` / `output_cost_per_token` | 3e-06 / 1.5e-05 | Live pricing |
| `mode` | `chat` / `embedding` | Separates text from embedding models |
| `supports_reasoning` | true / false | **Gates the effort control** |
| `supported_openai_params` | includes `thinking`, `reasoning_effort` | Which effort parameter to send |
| `supports_vision`, `supports_function_calling` | bool | Capability display |
| `providers` | `["anthropic"]` | Which credential applies |

**What it does NOT return**: valid *values* for `reasoning_effort`, and no deprecation dates. See §4 and §3.

---

## 3 · Availability: three states, not a boolean

"Turning a model off" has two genuinely different meanings, and a boolean forces a bad choice between them. Three states give a real off-ramp:

| State | In the picker? | Existing selections | Orgs notified |
|---|---|---|---|
| `available` | Yes | Work | — |
| `deprecated` | No | Still work | Yes — orgs using it are alerted |
| `disabled` | No | Fall back to the org safe model | Yes |

**Why three.** `deprecated` lets Hollisworks stop offering a model while giving orgs already on it time to migrate, with a real alert rather than silent removal from a screen. `disabled` is the decisive stop. A boolean would mean either a switch that does nothing to existing users, or one that breaks them without warning.

**The alert path already exists** — the `create_credential_failure_alerts` sibling pattern from D1c, routed to `manage_org_settings` holders. Reuse it; do not build a third notification mechanism.

**Interaction with the safe-model hierarchy**: a `disabled` model falls back to the org safe model, consistent with how model-unavailability behaves today (D1c confirmed this path is structurally distinct from credential failure, which fails loudly instead).

---

## 4 · Effort — gated by metadata, valued locally

**Gating is metadata-driven.** Show an effort control only where `supports_reasoning` is true. `voyage-3.5` correctly reports `false`; `claude-sonnet` reports `true`. No hardcoded provider table needed for this.

**Which parameter to send is metadata-driven.** `supported_openai_params` names it — `thinking` and `reasoning_effort` both appear for `claude-sonnet`.

**The valid VALUES are not.** LiteLLM tells you the parameter exists, not its legal range. `reasoning_effort` follows a small enum in the OpenAI convention; Anthropic's `thinking` takes a token budget. A small local mapping supplies the options. This is a real but bounded maintenance item — much smaller than a full per-provider capability table, and it only grows when a genuinely new effort mechanism appears.

**Effort is per task, not per org.** A document classification wants low effort; a diligence memo wants high. An org-wide effort setting would mostly be a way to spend more without knowing which tasks benefit. Hollisworks sets a sensible per-task default; the org may override per task.

**Open question, to settle in Phase E**: when a high-effort task falls back to the org safe model, does the effort setting carry? The fallback model may not support it at all. Recommend: drop effort silently on fallback and record it in `ai_decision_log`, rather than failing the call.

---

## 5 · Credentials stay per-provider — deliberately

Credential source is `ai.credential_source.<provider>` per org: `'org'` or `'platform'` (built, D1a). An org enters its Anthropic key once and it covers every Anthropic model it selects.

**Per-task key selection was considered and rejected.** It would add a third dimension (org × task × key) and reintroduce exactly the silent bill-shifting D1c was built to prevent — where an org's usage quietly lands on the Hollisworks bill without anyone choosing it. Hollisworks granting an org platform-key access for a *provider* already covers the real need.

---

## 6 · What the in-flight D2 sprint covers, and what it does not

The running `litellmphased2.structural` sprint was scoped before this specification was settled. It builds:

- The curated platform list (Hollisworks-only editing, org admin refused)
- The org picker (org admin editing, plain member refused)
- Real enforcement at the call path — an unauthorised model is genuinely refused, not merely hidden

**It does NOT build**: the three-state availability model (§3) — its prompt says "add or remove," a boolean. Nor effort (§4), which was always Phase E.

**So §3 is a D2 follow-up**, either as its own small sprint or absorbed into Phase E. Worth deciding once D2's output is reviewed — if its catalog schema already has a status column, extending it is trivial; if it stored a boolean, it needs a migration.

**Also worth noting**: the running sprint created a real table (`apps/api/migrations/litellmphased2_model_catalog.sql`) rather than using a platform-scoped `org_settings` key. That was its own Task 1a finding and may well be the right call for something with this much structure — but it means the catalog has a real schema that §3's states must fit into.

---

## 7 · Phase E scope, consolidated

- Per-task model assignment from the org's authorised list
- Per-task effort, gated on `supports_reasoning`, valued from the local mapping (§4)
- New AI tasks appear automatically as they are added
- The two-tier safe-model hierarchy (task model → org safe → Hollis safe) stays as built
- Settle the effort-on-fallback question (§4)
