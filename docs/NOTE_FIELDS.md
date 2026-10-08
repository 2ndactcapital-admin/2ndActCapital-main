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
| ★ `protection_type` | enum | 10 | none / buffer / barrier / floor / full. **Never a yes/no.** |
| ★ `buffer_pct` | numeric | 4 | Losses start only after the underlying falls more than X%. |
| ★ `barrier_pct` | numeric | 11+4 | Below it, the full decline applies from the first dollar. Synonyms: downside threshold (4 docs, grouped separately), trigger value, knock-in level, threshold value. |
| ★ `barrier_observation` | enum | 5 | at maturity only / daily / continuous. **Trap: daily is far riskier.** |
| `downside_leverage` | numeric | — | E.g. 1:1 below the barrier, or geared loss below a buffer. |
| ★ `principal_at_risk` | boolean | 13 | True unless repayment at maturity does not depend on the underlying. |
| `participation_rate` | numeric | 5 | |
| `cap_pct` | numeric | — | Maximum return. |
| `fixed_payout_amount` / `fixed_payout_threshold_pct` | numeric | — | **New.** Digital / "redemption barrier" payouts like the BofA $1,330 note. **Trap: a "barrier" here protects nothing.** |
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
| ★ `price_to_public_pct` | numeric | 15 | Usually 100%. |
| ★ `estimated_value_per_1000` | numeric | 16+4+3 | **Decision B.** Banks state it as a % (16 docs) or a $ per note (7). Store a range when stated ("not less than $X"). |
| ★ `agent_commission_pct` | numeric range | 7 | Min/max: "up to" is common. |
| `structuring_fee_pct` / `other_fees_pct` | numeric | — | Platform, marketing, structuring fees. |
| ★ `total_fees_pct` | numeric | 6 | |
| ★ `fee_based_account_price` | numeric | 5 | The price an RIA's advisory client pays. Usually a range ("may pay as little as $977.50"). |
| `proceeds_to_issuer_pct` | numeric | 4 | Self-check: price − fees ≈ proceeds. |

## 8 · Distribution — child table, one row per participant
| Field | Type | Seen | Notes |
|---|---|---|---|
| ★ `name` | text → participants table | 10 | E.g. issuer's own broker-dealer, iCapital Markets, dealers. |
| `role` | enum | — | issuer-affiliated agent / distribution agent / dealer / placement agent. |
| `fee_pct` | numeric range | — | |
| `calculation_agent` (on the note) | text | 9 | Flag when it is an issuer affiliate (a conflict). |

## 9 · Later, rules only (not extracted by models)
Registration statement number (14 docs), listing status (11), prospectus and
prospectus-supplement dates, form of notes, "filed pursuant to" rule, credit-risk
boilerplate, business-day conventions, settlement lags. All are labeled lines that
rules extract exactly.

## Self-checks (free accuracy checks)
- price − total fees ≈ proceeds to issuer
- estimated value < price
- autocall schedule dates fall between pricing and maturity
- a recomputed payoff matches the filing's hypothetical example table (mismatch → review, since filings contain errors too)

---

## Decisions (Joe, 2026-10-08)
- **A.** `initial_valuation_date` is NOT a separate field: it is `pricing_date`. A rule
  flags any note whose filing states a different initial valuation date (review, not merge).
- **B.** Estimated value is stored BOTH as $ per $1,000 and as % of principal; whichever the
  filing states is extracted, the other is derived and marked derived.
- **C.** "Up to" / "as low as" / "not less than" amounts are stored as a MIN and MAX with the
  bound's wording kept as evidence. A single stated amount sets min = max.
- **D.** No further fields for now.
