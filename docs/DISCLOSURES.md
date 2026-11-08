# Disclosures — one library for the whole platform (DISCLOSURES1)

**Platform rule for every later sprint: Every new disclosure goes through the library.** Do not put risk text,
hypothetical-performance text, tax labels, licence notices or product disclosures in code or in a feature's own
table. Add a key to `services/disclosures.KEYS`, write its default in `docs/disclosures/defaults/<key>.md`, read it
at the screen with `services.disclosures.resolve_safe` / `text_for`, and gate any sensitive action on
`services.disclosures.require_current`. If the key replaces text that already exists, keep that exact text as the
key's fallback (`FALLBACK_SOURCES`, read from the original constant).

## The library

| Table | What it holds |
|---|---|
| `public.disclosures` | One row per **key** (`long_short.risks`, `hypothetical.performance`, …): title, category, audience (staff / member / both). Platform data: every org reads it; only a Super Admin writes it. Rows for keys the platform defines are created on first use (`ensure_catalog`). |
| `public.disclosure_versions` | Each **version** is a FILE in R2 (PDF, DOCX, Markdown or HTML, at most 5 MB). The row holds its R2 key, its SHA-256, size and type, its plain-text **summary** (what screens show inline), its effective date, its status (`draft` → `published` → `retired`; a draft can be withdrawn = retired), who drafted it and who published it. It also holds the scope target (platform / org / household / account). The id is stable: a status change copies the prior state on the system axis (`archived_from_id`). The file, the summary and the dates never change; a correction is a new version. |
| `public.disclosure_acknowledgements` | Who acknowledged which **version** (its id **and** its hash; a composite FK keeps the pair honest), for what (a long/short proposal, a 351 plan, an account, a backtest), from which screen, and when. Insert-only. |

RLS on every operation of every table:

* Platform rows (`org_id` NULL) are read by everyone and written only under `app.is_super_admin`.
* Org rows are read and written only by their own org (NULLIF on the org GUC).
* Deletes pass the guards only on the fixture-teardown path.

## Scopes and the resolver rule

    platform default  →  org  →  household  →  account

**The most specific PUBLISHED version in force on the as-of date wins.** In force means
`effective_from <= as_of` and (`effective_to` is NULL or `as_of < effective_to`).

* The resolver (`services.disclosures.resolve_many`) is one function and one query. An account's household is read
  from the account in the same statement.
* On a tie at one scope, the latest effective date wins, then the highest version number.
* A draft never shows.
* A retired version stops on its retire date.
* With no version in force, the key's **fallback** applies: the exact text the code carried before the library.
  The fallback also applies whenever the library cannot be read; `resolve_safe` never raises and keeps the caller's
  transaction. So nothing goes blank.

## R2 layout

    disclosures/platform/<key>/v<N>-<sha256>.<ext>
    disclosures/orgs/<org_id>/<key>/v<N>-<sha256>.<ext>
    disclosures/orgs/<org_id>/households/<household_id>/<key>/v<N>-<sha256>.<ext>
    disclosures/orgs/<org_id>/accounts/<account_id>/<key>/v<N>-<sha256>.<ext>

* Uploads go through `services.storage.upload_bytes`; the bucket is `R2_BUCKET_NAME`.
* An object is never overwritten. A new version is a new object (its number and hash are in the key), and the
  database refuses a second live row for one key.
* If the store fails, nothing is recorded.
* Reads are **proxied**: `GET /api/v1/disclosures/versions/{id}/file` reads the object with
  `storage.download_bytes` under the caller's RLS context.
  * The read is refused if the bytes no longer match the recorded hash.
  * Another org's version is a 404 before any storage call.
  * A member reads only a published, member-audience version in force that applies to them (their org, their
    households, their accounts: `GET /api/v1/disclosures/mine`).

## Who does what

* **Super Admin** manages platform defaults.
* **org admin** (`manage_disclosures`, granted by default with `view_disclosures`) manages the org's versions, and
  its households' and accounts' (from the record's page: household → Disclosures tab, the account page's
  Disclosures panel).
* **Publishing is maker-checker.** The uploader cannot publish their own version: the service refuses it and the
  CHECK `disclosure_versions_maker_checker_chk` backs that up. A second holder publishes it (a second Super Admin
  for a platform default).
* **Every action is audited**: `disclosure.version_drafted`, `_published`, `_retired`, `disclosure.acknowledged`.

Admin → Settings → **Disclosures** (`/admin/disclosures`) shows:

* every key, with what is in force for this org and why (platform default / org override / built-in text);
* each key's versions: status, dates, maker / checker, the file, the summary;
* Upload (draft), Publish and Retire, each only where the server lists the action.

## The acknowledgement rule

A gated action calls `require_current(key, subject)`. **The version in force NOW must have been acknowledged — by
id and hash — for that subject.** If a newer version was published since, the old acknowledgement no longer
suffices and the person acknowledges again. Both acknowledgements are kept.

* **rep13 long/short.** The proposal's risks come from `long_short.risks`, in force for the account. Acknowledging
  records that version. Submit and approval refuse a stale acknowledgement. With no library version in force,
  rep13's own acknowledgement is the whole rule, as before.
* **Later:** the 351 plan (`section351.exit`) and hypothetical performance shown to a member
  (`hypothetical.performance`) call the same gate.

## Keys and where they show

| Key | Shown on | Fallback (exact old text) |
|---|---|---|
| `long_short.risks` | long/short proposal → Disclosures (and its acknowledgement) | rep13's six `RISKS` |
| `hypothetical.performance` | every backtest surface, the PDF, the member envelope, note estimates | `NO_POLICY` with no approved policy. With an approved policy, an ORG-or-lower version replaces the policy's text; the platform default never replaces a firm's approved text. The Marketing Rule gate (audience) is unchanged. |
| `tax.estimate_label` | tax overview (`estimate_disclosure`), long/short tax estimate | `"estimate"` |
| `prices.internal_use_notice` | Super Admin → Security master → Jobs | `PRICE_GAP_NOTE` |
| `replica.fund_affiliation` | every fund-replica model / registry entry (`{ticker}` replaced) | `FUND_DISCLOSURE_TEMPLATE` (= the old `disclosure_for`) |
| `direct_indexing.tax_overlay`, `section351.exit`, `options.overlay`, `notes.replica` | resolvable now (`GET /disclosures/resolve`); no screen yet | none (new defaults) |

## Operator step — load the platform defaults

The defaults are Markdown files in `docs/disclosures/defaults/<key>.md`. Each is our own plain-English text, marked
**"default — have counsel review"**, and none is any firm's text.

    doppler run -- python3 apps/api/scripts/load_disclosure_defaults.py --dry-run
    doppler run -- python3 apps/api/scripts/load_disclosure_defaults.py --drafter <super-admin email>

`--dry-run` validates every file and prints the R2 key each would get. It writes nothing.

The real run uploads each new or changed default as a platform **draft**. Then a **second** Super Admin publishes
each one on Admin → Disclosures. Until a default is published, screens show the built-in text.
