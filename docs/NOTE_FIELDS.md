# Structured-note field list — v3, APPROVED (2026-10-08)

v2: confirmed against the regrouped inventory (572 concepts, model grouping, 170 recovered quotes). Adds final-value method, one observation schedule table, payoff-formula evidence, and synonyms the grouping left unmerged.

For Joe's review. Once approved, this freezes the extraction schema.
Evidence: inventory run `aa1c8c7c` (22 pricing supplements, 21 issuers, one per issuer)
plus the existing `note_terms_field_registry` (19 fields) and B1's extensions.
"Seen" = how many of the 22 documents stated the item. ★ = critical: a
disagreement sends the note to review.

Principle: the core fields are what's needed to (a) price a note against market
data and (b) compare its cost to investors. Everything else is later, and mostly
rules-only.

---

## 1 · Identity and program
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `cusip` | text | 16 | Rule-extracted, check digit validated. Model is cross-check only. |
| `isin` | text | 4 | Rules only. |
| ★ `issuer_entity` | text → issuer table | 21 | Merges `issuer` / `issuer_name`. Normalised to `structured_note_issuers`. |
| ★ `guarantor_entity` | text → issuer table | 5 | The credit actually standing behind the note. Confirm against the issuer table's mapping; mismatch → review. |
| `product_supplement_ref` | text | 4 | Name and date of the program document (e.g. "EQUITY-1, Dec 8 2025"). Links the note to its generic terms. |
| `issue_size` | numeric (USD) | 12+5+5 | Aggregate principal. Synonyms the grouping split: aggregate principal amount, total offering amount, total price to public. |
| `denomination` | numeric | 10 | Minimum / per-note amount (usually $1,000 or $10). |
| `currency` | text | 9 | Default USD. |

## 2 · Dates
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `pricing_date` | date | 20 | Synonyms: Trade Date, Strike Date. **Decision A below.** |
| `issue_date` | date | 15 | Settlement / original issue date. |
| ★ `final_valuation_date` | date | 16 | Synonyms: final determination / observation / calculation day. |
| ★ `maturity_date` | date | 19 | |
| `tenor_months` | derived | — | Computed from pricing → maturity, never extracted. |

## 3 · Underlyings — child table, one row per underlying
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `name` | text | 10+ | |
| `ticker` | text | — | Bloomberg ticker where given (e.g. "SPX", "ASML UW"). |
| `kind` | enum | — | index / ETF / single stock / rate / other. |
| `weight_pct` | numeric | — | Null for single and worst-of. |
| `initial_level` | numeric | — | |
| ★ `return_basis` | enum | 9 | price return / total return / decrement. **Trap: price return without dividends is worth less.** |
| `fx_treatment` | enum | — | none / quanto / composite. |
| ★ `basket_type` (on the note) | enum | 6 | single / worst-of / weighted / average. **Trap: worst-of is much riskier than average.** |

## 4 · Payoff and protection
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `product_archetype` | enum | 7 | autocallable / buffered / barrier / digital / leveraged / principal-protected / reverse convertible / other. |
| ★ `protection_type` | enum | 10 | none / buffer / barrier / floor / full. **Never a yes/no.** Buffer only when losses start AFTER the buffer amount; barrier when crossing the level exposes the FULL decline. |
| ★ `buffer_pct` | numeric | 4 | The size of the decline the investor is protected from — losses equal only the decline BEYOND this amount. Never the buffer RATE / downside gearing (e.g. 111.11%, 117.65%) — that is a leverage multiplier, not a level. ONLY for `protection_type` 'buffer'. |
| ★ `barrier_pct` | numeric | 11+4 | Level below which the FULL decline applies, from the first dollar. **notefix2 F3: use this even when the filing calls it a "threshold value" (BofA), a "trigger buffer level" (Goldman), a "trigger value", "knock-in level" or "downside threshold"** — the word "buffer" in a filing's own label is not evidence this is `buffer_pct`. Test: at a final level just below it, does the investor lose (initial − final)/initial? Then it is a barrier. ONLY for `protection_type` 'barrier'. |
| ★ `barrier_observation` | enum | 5 | at maturity only / daily / continuous. **Trap: daily is far riskier.** |
| `downside_leverage` | numeric | — | E.g. 1:1 below the barrier, or geared loss below a buffer. |
| ★ `principal_at_risk` | boolean | 13 | True unless repayment at maturity does not depend on the underlying. |
| `participation_rate` | numeric | 5 | Upside participation in the underlying's gain only. **notefix2 F4: never a digital or fixed percentage** (e.g. a "Digital Percentage: 40.00%" belongs in `fixed_payout_amount`/`fixed_payout_threshold_pct`, not here). |
| `cap_pct` | numeric | — | Maximum return when the upside is participation-based. **notefix2 F4: not a fixed final-date premium or a digital return** — those are `fixed_payout_amount`. |
| `fixed_payout_amount` / `fixed_payout_threshold_pct` | numeric | — | **New.** Digital / "redemption barrier" payouts like the BofA $1,330 note: a fixed amount per $1,000 paid when the underlying ends at or above a threshold (digital, "contingent digital return", "step", fixed final-date premium), and that threshold as % of initial. **Trap: a "barrier" here protects nothing**, and a stated "Digital Percentage" or fixed final-date premium must NOT land in `participation_rate` or `cap_pct` (notefix2 F4). |
| ★ `final_value_method` | enum | 12 | **New in v2.** How the final level is set: single closing level / average of several dates / lowest of several dates. Changes the note's value materially. |
| `payoff_formula_quotes` | text (evidence) | 10 | **New in v2.** The filing's own payment-at-maturity formulas (above and below the threshold), stored as quotes for the payoff self-check, not as fields to fill. |
| `max_principal_loss_pct` | derived | 3 | Computed from protection type and level, never extracted. |

## 5 · Income
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `coupon_type` | enum | 13 | none / fixed / contingent. |
| ★ `coupon_rate_pa` | numeric | 8 | Always annualised; record frequency separately. |
| `coupon_frequency` | enum | — | monthly / quarterly / semi-annual / annual. |
| ★ `coupon_barrier_pct` | numeric | 9 | |
| ★ `coupon_memory` | boolean | 7 | |

## 6 · Calls
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `call_type` | enum | 11 | none / automatic / issuer. **Trap: issuer calls favour the bank.** |
| ★ `autocall_level_pct` | numeric | 10+6 | Synonyms: call value, call threshold (6 docs, grouped separately). |
| `autocall_frequency` | enum | 6 | |
| `no_call_months` | numeric | 5 | |
| Observation schedule — child table | rows | 7+ | **Changed in v2: one table for coupons and calls.** Per observation date: payment date, coupon barrier, coupon amount, call level, call amount. Most autocallables check both on the same dates. |

## 7 · Economics — the value signal
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `price_to_public_pct` | numeric | 15 | Usually 100%. Stays scalar: the only "up to" wording near it in the 138-final corpus describes a dealer resale discount, not this field's own value (notefix1 1d). |
| ★ `estimated_value_per_1000` | numeric | 16+4+3 | **Decision B.** Banks state it as a % (16 docs) or a $ per note (7). Store a range when stated ("not less than $X"). |
| ★ `agent_commission_pct` | numeric range | 7 | Display label: "Underwriting discount / agent commission" (bulkfetch — the key is unchanged). Most bank covers call it the underwriting discount. Min/max: "up to" is common. |
| `structuring_fee_pct` / `other_fees_pct` | numeric range | — | **notefix1.** Platform, marketing, structuring fees. "Up to $X" / "up to X%" wording confirmed in the 138-final corpus. |
| ★ `total_fees_pct` | numeric range | 6 | **notefix1.** Widened with the other fee fields. |
| ★ `fee_based_account_price` | numeric | 5 | The price an RIA's advisory client pays. Usually a range ("may pay as little as $977.50"). |
| `proceeds_to_issuer_pct` | numeric range | 4 | **notefix1.** Self-check: price − fees ≈ proceeds, using the range ends that make the check meaningful (see Self-checks below). |
| `total_fees_usd` | derived range | — | **bulkfetch. Calculated, never extracted or edited:** `issue_size` × `total_fees_pct` / 100, each end of the range separately. Self-check `dollar_totals_match`: compared with the dollar total the cover table states (e.g. "$27,950"), ±$1 or ±0.01%; a mismatch is needs_review naming both numbers. |
| `proceeds_to_issuer_usd` | derived range | — | **bulkfetch. Calculated, never extracted or edited:** `issue_size` × `proceeds_to_issuer_pct` / 100, range ends separately; same self-check against the stated total (e.g. "$832,050"). |

## 8 · Distribution — child table, one row per participant
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `name` | text → participants table | 10 | E.g. issuer's own broker-dealer, iCapital Markets, dealers. |
| `role` | enum | — | issuer-affiliated agent / distribution agent / dealer / placement agent. **notefix2 F1: `issuer_affiliated_agent` is resolved by RULE from `portfolio.distribution_participants.affiliated_issuer_group`, not left to the reader** — a member matching a seeded affiliate (BofAS/MLPF&S, JPMS, MS & Co./MSSB, GS&Co., Barclays Capital Inc., UBS Securities LLC, UBS Financial Services Inc., CGMI) of the note's OWN issuer group is this role regardless of what any reader answered. An unaffiliated or unknown name is still reported, never dropped. |
| `fee_pct` | numeric range | — | |
| `calculation_agent` (on the note) | text | 9 | Flag when it is an issuer affiliate (a conflict) — same affiliate-table match as `distribution`'s role rule (`participants.calculation_agent_affiliation`). |

## 9 · Later, rules only (not extracted by models)
Registration statement number (14 docs), listing status (11), prospectus and
prospectus-supplement dates, form of notes, "filed pursuant to" rule, credit-risk
boilerplate, business-day conventions, settlement lags. All are labeled lines that
rules extract exactly.

## Self-checks (free accuracy checks)
- price − total fees ≈ proceeds to issuer. **notefix1:** `total_fees_pct` and `proceeds_to_issuer_pct` are
  both ranges now. The check computes the IMPLIED proceeds interval `[price − fees.max, price − fees.min]`
  (price is still scalar) and fires only when that interval and the stated proceeds range do not overlap,
  within tolerance. A single stated amount is a range with `min = max`, so a wholly scalar note is checked
  exactly as before. The check never fires on a range whose bounds are both individually consistent with
  the other side — it fires only when EVERY combination of a feasible fee and a feasible proceeds value
  disagrees with price.
- estimated value < price
- autocall schedule dates fall between pricing and maturity
- a recomputed payoff matches the filing's hypothetical example table (mismatch → review, since filings contain errors too)
- **notefix2 F6:** when only an underwriting discount / agent's commission is stated and
  `total_fees_pct` is blank, the commission IS the total (derived, named `default_total_fees`);
  `fees_reconcile`'s finding now also proposes the corrected total (`detail.proposed_total_fees_pct`)
  when the stated total fails the price − fees ≈ proceeds check.
- **notefix2 F7 (`coupon_cross_check`):** when the observation schedule states a fixed per-period
  coupon amount and `coupon_frequency` is known, `coupon_rate_pa` must equal
  amount × periods/yr ÷ denomination × 100 (±0.01%); a mismatch is `needs_review` naming both numbers.
- **notefix2 F7 (`maturity_after_pricing`):** `maturity_date` / `final_valuation_date` must be
  strictly after `pricing_date`. A date that LITERALLY equals `pricing_date` is already rejected
  earlier, at the evidence level, before it can even become a candidate.
- **notefix2 F3 (`payoff_matches_at_protection_levels`):** extends the payoff self-check with
  SYNTHETIC rows at the stated barrier/buffer level ± 0.01% — catching a barrier read as a buffer
  (or vice versa) even on a filing whose own table happens not to tabulate a row exactly at that
  level, by comparing against the nearest real filing row within that tolerance.
- **notefix2 F2/F7 (hypothetical-section rejection):** for `coupon_rate_pa`, `pricing_date`,
  `issue_date`, `maturity_date`, `barrier_pct`, `buffer_pct` and `underlyings.initial_level`, a
  reading whose quote lies ENTIRELY inside a detected "hypothetical example" section is rejected
  for that field (reason `hypothetical_section`) before it can become a candidate — it may still be
  used by the payoff check, which reads the section's own rows directly, not this reading.
- **notefix2 Task 3 (list hygiene):** before `underlyings` / `distribution` list members become
  readings, a table header ("CUSIP", "Barrier Value"), a definition sentence ("The closing level of
  the underlying...", "For each underlying...") or a single entry naming more than one real member
  is dropped with its reason (`services.note_extraction.rules.filter_list_members`) — recorded, never
  silently discarded.
- **notefix2 Task 4 (defaults):** `currency` defaults to USD, `call_type` to 'none' (no call/
  redemption wording anywhere in the filing), `coupon_memory` to false (a contingent coupon with no
  memory/accrual wording), and `no_call_months` from the first observation-schedule date that checks
  a call minus `issue_date` — each written as a `derived` reading naming its own rule, and NEVER
  applied over a value the filing actually states (a EUR note stays EUR).

---

## Decisions (Joe, 2026-10-08)
- **A.** `initial_valuation_date` is NOT a separate field: it is `pricing_date`. A rule
  flags any note whose filing states a different initial valuation date (review, not merge).
- **B.** Estimated value is stored BOTH as $ per $1,000 and as % of principal; whichever the
  filing states is extracted, the other is derived and marked derived.
- **C.** "Up to" / "as low as" / "not less than" amounts are stored as a MIN and MAX with the
  bound's wording kept as evidence. A single stated amount sets min = max.
- **D.** No further fields for now (superseded by notefix2 — see below).
- **E. (notefix2, 2026-10-14)** Every derivation reads the RESOLVED value of its inputs, never a
  single unverified reading — including inside `gold_prefill`, which has only one reader: there,
  "resolved" means the model's answer stands when an independent rules reading agrees or is silent,
  and the field is left unresolved (not merely wrong) when they disagree.
