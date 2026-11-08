# DISCLOSURES1 — Task 1 discovery (2026-11-08)

Read from the code at `42c5c57` (disclosures1-work's base: "merge catchall1"). Nothing here was taken from the
sprint prompt without checking the code.

## 1a. Where disclosure text is shown to a person today

"Disclosure" means text that tells a person a risk, a limit or a licence condition. Notes written for the firm and
its counsel (`OPEN_ITEMS`, `COUNSEL_ITEMS`) are not disclosures. They tell the firm what it still has to decide, and
nobody acknowledges them. They are listed at the end, with their own verdict.

| # | File (source of the text) | The text, or where it comes from | Where it renders | Audience | Library key (Task 6) |
|---|---|---|---|---|---|
| 1 | `services/long_short_rules.py` `RISKS` (six `(key, text)` pairs) | rep13's six AQR risks: pre-tax underperformance, varying tax benefit, lower future rates, unused losses, larger tax at redemption, rule changes / IRS challenge | Long/short proposal → "Disclosures" (`LongShortPanel.jsx`). The text is stored in the proposal's `long_short_proposals.disclosures` JSON at build time, and the maker ticks each risk (`ACK_ITEMS`) before submitting | staff (the adviser). The member sees `member_summary`, which points to "the risks listed with this proposal" | `long_short.risks` |
| 2 | `services/hypothetical_policy.py` `NO_POLICY` + the approved policy's `disclosure_text` (`gate()`) | With no approved policy: "No hypothetical-performance policy approved — staff use only". With one: the firm's own approved disclosure text | Every backtest surface: `BacktestWorkspace.jsx`, the backtest **PDF** (`backtest.build_pdf`, "Disclosure: …"), the member envelope (`backtest.member_view`), note replication's withheld estimate (`note_replication` → `gate["disclosure"]`) | staff, and members when the policy's audience is `staff_and_members` | `hypothetical.performance` |
| 3 | `services/security_master_jobs.py` `PRICE_GAP_NOTE` (+ the `security_data_prices` job description) | "End-of-day prices come from the prices job (internal use only for the free source). Until the operator's backfill has run …" | Super Admin → Security master → Jobs (`SecurityMaster.jsx`, `price_gap_note`) | staff (Super Admin) | `prices.internal_use_notice` |
| 4 | `services/tax_profiles.py` `ESTIMATE` / `services/rep10_common.py` `ESTIMATE` (`"estimate"`), and `apps/web/lib/tax.mjs` `"$X (estimate)"` | The bare label **"estimate"**. **[FIND] The prompt's "estimate — confirm with your tax adviser" text exists nowhere** in the API or the web app (grep across `apps/`). The only tax-estimate text is the one-word label | Every tax figure (tax overview, proposals, gifts, long/short tax estimate) | staff | `tax.estimate_label`. The fallback is the exact current label `"estimate"`; the default sentence is new (Task 7) |
| 5 | `services/replica_terms.py` `disclosure_for(ticker)` | "Replicates the published holdings of {TICKER}; not affiliated with or endorsed by the fund's sponsor or index provider." Written into `portfolio.models.disclosure` when a fund-built model is created (`replica_models.build_model`, `options_sleeve`) | Model tab / registry entry (`ModelTab.jsx`, `EntryView.jsx`, `RegistryList.jsx`) | staff | `replica.fund_affiliation` (a template with `{ticker}`) |
| 6 | `services/long_short_rules.py` `disclosures()` → `borrow_estimate.text`, `structure` | The borrow-cost estimate sentence and "a margin account is required; short positions can lose more than their initial value" | Long/short proposal → Disclosures | staff | These are computed per proposal (amounts). They stay in code. The margin and short-sale risks now sit in `long_short.risks` (Task 7) |
| 7 | `services/fee_invoices.py` → fee41 `FEE_DISCLOSURE` template | The fee invoice's disclosure, rendered by fee41's narrative templates (`DEFAULT_DISCLOSURE_TEMPLATE_CODE`) | Fee invoice (PDF / record) | member (the billed household) | **[FIND] Not moved.** It is already a managed, per-org template rendered from the database (fee41), and it is money-adjacent (invoices). A later sprint can point the template at the library. Moving it here would add a second generator, which `fee_invoices`' own docstring forbids |
| 8 | `apps/web/components/Footer.jsx`, `HollisworksMarketing.jsx` | A "Disclosures" / "Regulatory disclosures" link to `/disclosures` | Public marketing footer | public | **[FIND] `/disclosures` has no page** (`apps/web/app/disclosures` does not exist), so the link is dead today. Not changed here: it is a public page with no session, and it needs firm-written regulatory text (Form ADV / CRS) that counsel provides |

Not disclosures (checked, kept out of the library):

* `blocks.py`, `hypothetical_policy.py` `OPEN_ITEMS`; `note_replication.py` `COUNSEL_ITEMS` ("The issuer-credit
  disclosure wording on the note page — counsel's item"). These are notes to the firm about decisions that are
  still open. `notes.replica` gets a library default (Task 7), so counsel now has a place to put that wording.
* `cost_model.py`: the MARKUP "disclosure" is an acknowledgement flag (`disclosure_acknowledged_by/_at`). It holds
  no text.
* `agent_proposals.py` / `workflow_engine.py`: "disclosed self-approval" is a recorded reason, not text shown to a
  person.
* `assistant_transcript.py` / `AssistantPanel.jsx`: "disclosures" there are the tool narration lines.
* `models.py` `create_fund_model(... disclosure ...)` is the writer of row 5's text, not a second source.
* `rbac.py` and `authz.py` mention the word only in comments.
* `security_data/types.py` `PURPOSE_LABELS` ("Internal use (adviser analysis)") is a vocabulary label of the data
  layer's purpose. The licence rule itself is enforced in code (`registry.py`: member display is refused for a
  `not_allowed` licence), so it is not text a person must read.

## 1b. Storage and the R2 key layouts

* **`services/storage.py`** (current signatures, the ones the verify's fake transport matches):
  * `get_s3_client()` builds the boto3 R2 client. It raises when `R2_ACCOUNT_ID` is unset.
  * `upload_bytes(key, data, content_type=None, bucket=None) -> key`
  * `download_bytes(key, bucket=None) -> bytes`
  * `delete_object(key, bucket=None)`
  * `get_signed_url(key, expires=3600, bucket=None)`

  All of them are synchronous and called through `run_in_threadpool` / `asyncio.to_thread`. The bucket defaults
  to `R2_BUCKET_NAME` (else `"2ndactcapital-docs"`).
* **Key layouts already in the bucket.** None of them uses a `disclosures/` prefix, so the new prefix collides
  with nothing.

  | Prefix | Writer | Layout |
  |---|---|---|
  | `chancery/` | `chancery_intake.store_document` | `chancery/<org>/<entity or unfiled>/<slug>/v<N>/<document_id><ext>` |
  | (models) | `models.upload` | `<R2_PREFIX>/<org>/<sha256>` |
  | deal room | `routers/deal_room.py` | its own per-deal keys |
  | spv | `routers/spv.py` | its own keys |
  | marketplace | `routers/marketplace.py` | its own keys |
  | entity documents | `routers/entity_documents.py` | its own keys |
  | EDGAR | `edgar_pipeline`, `edgar_fetch` | global, gzip |
  | replica holdings / terms | `replica_holdings`, `replica_terms` | gzip |

  Chancery's layout is the model to follow: the key embeds a version and an id, so a re-upload can never
  overwrite a stored file.
* **Read paths for showing a stored file in the app.** Both kinds already exist:
  * **Short-lived signed URLs:** deal room downloads (300 s), document review, entity documents, SPV documents.
  * **Proxied bytes:** the deal room's watermarked rendition streams through the API, never a URL; `models`
    reads its upload back with `download_bytes`.

  DISCLOSURES1 uses the **proxied** read (`GET /api/v1/disclosures/versions/{id}/file`). The API reads the object
  with `storage.download_bytes` under the caller's RLS context, and the Next route passes the bytes through.
  There is no URL that outlives the request, and a version another org owns is a 404 before any storage call.

## 1c. Existing acknowledgement records

* **rep13 long/short.** The acknowledgement lives on `public.long_short_proposals` in three columns:
  `acknowledged_by` (uuid), `acknowledged_at` and `acknowledgement` (jsonb `{items, texts, member_summary}`).
  * `LS.acknowledge` writes it once. Only the proposal's maker may acknowledge, only while the proposal is a
    draft, and the maker must tick every key of `ACK_ITEMS` (the six risk keys + `borrow_estimate`,
    `margin_requirement` and `member_summary`).
  * `LS.presubmit` refuses a submit without it; `approval_refusal` re-checks it.
  * It records **which items** were ticked and the texts at the time, but **no version and no hash**. The texts
    are copied, so "acknowledged the CURRENT text" cannot be asked once the text changes.
* **rep10 hypothetical policy.** There is no acknowledgement by a reader. The policy VERSION is approved by a
  second holder of `manage_hypothetical_policy` (`hypothetical_performance_policies`: `maker_id`, `approved_by`,
  `approval_task_id`, CHECK `approved_by <> maker_id`). Members are never asked to acknowledge; the audience gate
  decides whether they see a backtest at all.
* **cost_model MARKUP.** `disclosure_acknowledged_by` / `_at` on the cost policy is a firm-side flag, not a
  per-person record.

There is no shared acknowledgement table. DISCLOSURES1 adds one (`public.disclosure_acknowledgements`). Each row
holds the version id **and** its content hash (a composite FK to the version), the subject (a proposal, plan,
account or backtest), the screen and the time. rep13's long/short acknowledge also writes a row for the
`long_short.risks` version in force for the proposal's account. Its submit then requires that the row names the
version **currently** in force, so a newer published version means the maker must acknowledge again.

## Decisions taken from the findings

* **Schema `public`, not `portfolio`.** The library spans backtests, prices, tax, models and proposals, not only
  portfolio data. Every comparable shared platform + org table (`custodian_capabilities`, `borrow_rates`,
  `hypothetical_performance_policies`) lives in `public` with the NULLIF org policy. `portfolio` is also not on
  `app_service`'s search_path (CLAUDE.md).
* **Versions archive on the SYSTEM axis.** `disclosure_acknowledgements` holds a live FK to a version's id, so a
  version's id must stay stable (rule 3, system-time archival). Publishing and retiring archive the prior state
  first (`archived_from_id`), then change the live row.
* **Maker-checker without a human task.** Publish is refused for the drafter, both in the service and by a CHECK
  (`published_by <> drafted_by`). No `human_tasks` kind is opened. The publisher acts from the Disclosures page;
  an approvals-inbox entry is a later nicety.
* **hypothetical.performance precedence.** With no approved policy, the staff-only line `NO_POLICY` is unchanged.
  It is the Marketing Rule gate's own status, so the gate is unchanged too. With an approved policy, an
  org-or-lower library version wins; otherwise the policy's own approved `disclosure_text`. The **platform
  default never overrides a firm's approved text.** `[FIND]` The firm's approved policy text is already an
  org-level disclosure, approved by a second person.
