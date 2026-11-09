# Cash Coverage & Treasury — design spec v1 (2026-11-04)

Status: **design, not scheduled.** Roadmap position: after S28 (drift monitor),
i.e. after the current sprint queue (crmdetail1 → visibility1 → secmaster2 →
prices → regression / HollisMuse v4 → rep13–15 → S28). Replaces and expands
the roadmap's "Cash management module (acct00–acct02, cash00–cash12)" entry.

Inspiration: Mercury Bank's Treasury product (functional requirements captured
from its public demo, 2026-11-04). We adopt its two best ideas — **cash
coverage** and **three sleeves (Near-term / Reserve / Long-term)** — and
rebuild them around what a UHNW family actually has: lumpy, dated obligations
spread across many legal entities, taxed differently per account.

---

## 1 · Why this, and why it beats a business treasury tool

Mercury sizes cash as *months of a business's average outgoings*. A family's
outflows are not a steady burn. The Olivo demo family, for example, has:

- capital calls — $12M unfunded (Harbor Point IV $8M, Caldera II $4M), with
  timing projected by the Takahashi-Alexander model already in Hollisworks;
- estimated taxes — April, June, September, January;
- debt service — $52.8M of mortgages, a securities-backed line, an art line, a
  farm loan (crmdetail1 liabilities);
- property carrying costs — Greenwich, Naples, Aspen, Miami, the vineyard;
- an ILIT premium, GRAT annuity payments through 2027, the foundation's 5%
  payout, tuition, lifestyle spending.

So the product is **obligation-matched cash management**: know every dated
obligation, show how far the liquid cash covers them, hold that cash in the
right sleeve and the right instrument *after tax*, and keep it topped up.

---

## 2 · Core concepts

### 2.1 Obligations ledger
Every known future outflow, per entity, with a date (or recurrence), amount,
certainty and source.

| Source type | Comes from | Certainty |
|---|---|---|
| capital_call_projected | TA model on portfolio.commitments (projected at read time, never stored as obligation rows — keep that rule) | estimated |
| capital_call_noticed | an actual call notice (Chancery / manual) | firm |
| debt_service | liabilities (crmdetail1): payment schedule or interest-only estimate | firm / estimated |
| tax_estimate | quarterly estimates (manual first; later from the tax engine) | estimated |
| insurance_premium | ILIT / policy schedule | firm |
| trust_payment | GRAT annuity, CRUT unitrust, required distributions | firm |
| charitable_payout | foundation minimum distribution, pledges | firm / estimated |
| property_carrying | taxes, insurance, maintenance per property | estimated |
| tuition, lifestyle_recurring | manual recurring | estimated |
| one_off | manual | firm / estimated |

Rules: an obligation belongs to an **entity** (the payer), rolls up to the
household; projected items are recomputed, never frozen; every manual item has
an owner and an as-of; bitemporal like everything else. **Resolve the overlap
with SPV commitment tracking before building** (one commitments source of
truth; obligations *read* it).

### 2.2 Cash coverage
Two numbers, family-level and per entity:
- **Months of coverage** = liquid balance ÷ average monthly gross outflow
  (Mercury's definition — kept for familiarity).
- **Covered through <date>** = walk the dated obligation schedule forward,
  drawing down liquid balance, until it runs out. Show the first uncovered
  obligation ("next gap: Caldera II call, ~$4M, est. Mar 2027").

Per-entity coverage is the differentiator: a family can be well covered
overall while the ILIT cannot pay its premium or Round Hill Road LLC cannot pay
property tax. Flag any entity whose coverage falls below its policy minimum.

"Liquid balance" = cash + the three sleeves (+ optionally an approved
securities-backed line's undrawn amount, shown separately, never mixed in).

### 2.3 The three sleeves
Each sleeve is defined in **months of obligations**, convertible to % and $.

| Sleeve | Covers | Typical instruments | Liquidity | Yield basis |
|---|---|---|---|---|
| **Near-term** | next ~3 months of obligations + buffer | government or muni money-market fund | same day (cut-off) | 7-day SEC / effective yield |
| **Reserve** | months 4–12 | ultra-short bond fund, T-bills | next day / at maturity | 30-day SEC yield / YTM |
| **Long-term** | months 12–24+ | Treasury ladder (bills / notes); munis where after-tax better | at maturity; 1–2 days early sale (may lose principal) | yield to maturity |

**Obligation-matched ladder:** rungs are placed on the dates of known large
obligations (tax dates, called capital, GRAT payments), with monthly filler
rungs between — not just "one rung a month".

**Boundaries:** two controls (Near/Reserve boundary, Reserve/Long boundary, in
months); sleeve sizes are derived, not typed. Rung count control (default 8).
Changes are drafts until saved.

**Presets:** Stay liquid (all Near-term), Balanced (all Reserve), Recommended
(questionnaire), Custom. Each shows estimated net and after-tax yield.

### 2.4 Monthly maturity waterfall
When a rung matures: (1) top up any sleeve below target, Near-term first;
(2) reinvest the remainder as a new rung at the end of the ladder — or on the
next uncovered obligation date. Earnings (dividends, interest) are reinvested
per the target allocation. Runs as a **scheduled job on the workflow engine**
(like secmaster1's jobs): it produces a **proposal**, never a trade.

### 2.5 Withdrawal order
Near-term first, then Reserve, then Long-term — selling the rung closest to
maturity first, with the "may lose principal" warning on early sale.

### 2.6 After-tax instrument selection
Per account's tax treatment and the owner's state: Treasuries and Treasury
funds are state-tax-exempt (CT / NY residents); muni money-market and muni
ladders for high-bracket taxable accounts; taxable instruments in IRAs / trusts
where tax is irrelevant or different. Show net yield **and** after-tax yield
per sleeve. Roadmap fact kept: T-bills beat CDs after tax for NY-domiciled
taxable members (~133 bp hurdle). Uses the lot-level tax engine's member tax
profiles (rep6).

### 2.7 Recommendation questionnaire (3 steps)
1. **Outflows** — prefilled from the obligations ledger (not just trailing
   spending); show trailing 3-month actual outflows as a cross-check and warn
   if the entered figure is well below them.
2. **Objective** — stability vs. yield; positions the liquid sleeves.
3. **Recommendation** — same presentation as the editor, with estimated net
   and after-tax yield. Applying it creates a **policy draft that needs a
   second person** (no skip-confirmation for real money — deliberate departure
   from Mercury).

---

## 3 · Screens

1. **Treasury overview** (per household, filter per entity): balance-over-time
   chart (30d / 90d / 6m / YTD / 12m / all) with transfer markers and a
   tooltip (value, pending in, pending out); blended net yield with method
   note; **coverage card** (months + covered-through + next gap); per-entity
   coverage table with flags; earnings dialog (dividends per fund + ladder
   interest + cash interest − advisory fee = total; fee accrued daily, charged
   monthly); actions: documents, transfer instruction, edit policy, rules.
2. **Obligations** — calendar and list, by entity, certainty, source; add /
   edit manual items; projected items marked and explained.
3. **Activity ledger** — date, description, change, pending marker with
   settlement guidance; entry types: transfers, dividends, interest,
   maturities, fees, credits; clear empty state; a detail drawer per entry
   (Mercury's demo had dead links — we spec the drawer).
4. **Holdings** — per sleeve: instrument, plain-language description, yield
   with basis and as-of, liquidity SLA with cut-off wording, shares, value,
   months of cash, prospectus link; ladder rows expand to every rung (maturity,
   type, par vs market, YTM).
5. **Policy editor** — boundaries, rung count, presets, live estimated yield,
   ladder explainer, draft until saved, maker-checker on save.
6. **Questionnaire** — §2.7.
7. **Rules** — §4.
8. **Documents** — statements, trade confirmations, 1099s through Chancery;
   dates in **Eastern time** (Mercury uses UTC — rejected).

Member view: their family's coverage and sleeves, read-only, under the
hypothetical / estimate labelling rules; never other families (bugfix2,
visibility1).

---

## 4 · Money movement and rules — governed

Mercury lets rules move money automatically. Hollisworks does it in stages:

| Stage | Behaviour | Gate |
|---|---|---|
| A (build first) | Transfers and rules produce **transfer instructions** — printed / manual, approved by a second person, same path as execution instructions | none — manual always offered |
| B | Instructions sent to the custodian through the adapter | Altruist write access |
| C | Rules execute without a human per occurrence | S30 SOC + S29b + **counsel** on custody / standing letters of authorization |

Rule types (from Mercury): distribute across accounts (% of balance, fixed
amount, % of incoming); maintain a target balance (condition + supporting
account); scheduled recurring transfer; redirect incoming funds (sweep %).
Frequencies: daily, business days, weekly, monthly. Rules are workflow
triggers on the engine — run history, holds and alerts come free. Same-owner
moves first; third-party destinations only after counsel.

---

## 5 · Calculations (summary)

- Coverage months = liquid balance ÷ monthly gross outflow.
- Covered-through = forward walk of dated obligations against liquid balance.
- Blended net yield = Σ(sleeve value × sleeve yield) ÷ total − advisory fee;
  each holding on its own basis (7-day MMF, 30-day SEC bond fund, YTM ladder).
- After-tax yield = net yield × (1 − applicable federal / state / local rate,
  by instrument exemption).
- Earnings = dividends + ladder interest + cash interest − fee; reinvested.
- All money in Decimal; every figure carries source and as-of; anything
  without data reads "not estimated — needs <x>".

---

## 6 · Data sources

| Need | Free / existing | Paid option |
|---|---|---|
| Treasury yield curve | **FRED** (already a provider) | FMP treasury rates |
| T-bill / note CUSIPs, maturities, auction results | **Treasury Fiscal Data API / TreasuryDirect** (free) — new provider | — |
| Money-market fund 7-day yields, holdings | **SEC N-MFP filings** (free, monthly; EDGAR pipeline exists) | FMP fund data |
| Bond-fund 30-day SEC yields | issuer files | FMP |
| Fund / ETF prices and history | (the "prices" sprint) | **FMP EOD history** |
| Custodian cash rate, balances | Altruist (read-only integration) | — |

All through the marketdata1 provider registry with licence facts; no vendor
lock-in (Joe, 2026-10-28).

---

## 7 · Data model (indicative — sprints confirm against the live DB)

- `cash_obligations` — entity, source_type, amount, currency, due_date or
  recurrence (RRULE), certainty, source_ref, notes, bitemporal.
- `cash_policies` — household / entity scope, near_reserve_boundary_months,
  reserve_long_boundary_months, rung_count, objective, min_coverage_months,
  status (draft / approved), maker / checker, bitemporal.
- `cash_sleeve_targets` — derived per policy version (months, %, $).
- `cash_ladder_rungs` — policy, maturity, instrument (security master id),
  par, target obligation link.
- `cash_rules` — type, source, destinations (% / $), basis, frequency, stage
  (A / B / C), status; fires via workflow triggers.
- `cash_activity` / earnings attribution — from positions, transactions and
  fee accrual.
Reuse: portfolio.commitments (+ TA projections), liabilities, positions,
models / proposals / execution instructions (Replication Studio), workflow
scheduler, human tasks, Chancery.

---

## 8 · Phasing (proposed sprint names)

| Sprint | Scope | Depends on |
|---|---|---|
| cash00 | Discovery + design confirmation; resolve commitments overlap; Altruist cash questions answered | — |
| cash01 | Obligations ledger + coverage (months, covered-through, per entity, flags) | crmdetail1, TA model |
| cash02 | Policies, sleeves, presets, questionnaire (maker-checker) | cash01 |
| cash03 | Ladder construction + after-tax instrument selection; Treasury Fiscal Data + N-MFP providers | prices / yields, rep6 tax profiles |
| cash04 | Proposals → execution instructions; maturity waterfall job | cash03, rep execution path |
| cash05 | Treasury overview, holdings, activity, earnings, documents | cash04 |
| cash06 | Rules, stage A (instructions) | workflow engine |
| later | Stages B / C | Altruist write, S29b, S30, counsel |

The earlier acct00–acct02 (read-only Altruist integration) remain
prerequisites for live balances; cash01–cash03 can run on held-away / manual
data and the Olivo demo before that.

---

## 9 · Open questions

1. Altruist: cash rate (negotiated vs scheduled), any spread on cash (ADV
   Item 5 disclosure either way), brokered CDs, money-market fund menu,
   Treasury purchase support, write-endpoint sandbox.
2. Fee: does the RIA charge on cash sleeves; at what rate; minimum balance?
3. Entity-level cash accounts: does each trust / LLC have its own sweep, or a
   family cash account with sub-ledgers?
4. Tax estimates: manual input first — when does the tax engine produce them?
5. Member experience: how much of the treasury view members see.
6. Custody / SLOA: counsel review before any stage C rule or third-party move.

## 10 · Mercury features deliberately NOT adopted
- Applying a recommendation without confirmation → maker-checker instead.
- UTC dates in documents → Eastern time.
- Fully automatic money movement on day one → staged A / B / C.
- Mercury's fee / yield tier table → their pricing, not a requirement.
- Bookmarking accounts → trivial; only if it falls out of a general UI pattern.

## 11 · Demo
The Olivo family (docs/demo/olivo) is the reference case: per-entity
obligations (capital calls, mortgages and lines, GRAT annuity, ILIT premium,
foundation payout, property costs, tuition) make coverage and the
obligation-matched ladder visible on day one.
