"""Generates HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md from data/olivo_crm.json."""
import json, os
OUT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(f"{OUT}/data/olivo_crm.json"))
T = json.load(open(f"{OUT}/data/_totals.json"))
# ── v3 overrides: sub-types from the CRM's lists, extra detail fields, the insurer ──
d["organisations"].append({"id": "O9", "name": "Lincoln Benefit Life Company", "entity_type": "corporation",
                           "note": "Insurer — the ILIT's $25M survivorship universal life policy"})
SUBTYPE = {"E1": "", "E2": "", "E3": "", "E10": "investment", "E12": "investment", "E14": "investment",
           "E20": "real_estate", "E21": "real_estate", "E22": "operating", "E23": "real_estate", "E24": "real_estate",
           "E25": "real_estate", "E26": "collectibles"}
EXTRA = {"E3": {"fund_role": "family_partnership"},
         "E16": {"ein_status": "501c3"},
         "E19": {"insurer": "O9", "policy_type": "survivorship universal life", "face_amount": 25000000,
                 "insureds": "P1 + P2", "policy_number_masked": "POL-****77"}}
for e in d["entities"]:
    if e["id"] in SUBTYPE: e["sub_type"] = SUBTYPE[e["id"]] or None
    e["details"] = {k: v for k, v in e["details"].items() if k not in ("ein_status", "fund_role", "policy")}
    e["details"].update(EXTRA.get(e["id"], {}))
names = {p["id"]: p["name"] for p in d["people"]}
names.update({e["id"]: e["name"] for e in d["entities"]})
names.update({o["id"]: o["name"] for o in d["organisations"]})
M = lambda v: f"${v:,.0f}"
MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
L = []
w = L.append

def acct_number(a):
    last = a["account_mask"].replace("*", "").replace("POL-", "")
    if a["account_mask"] == "—":
        return f"LEDGER-{a['id']}-0001"
    if a["account_mask"].startswith("POL"):
        return f"LBL-SUL-00000{last}"
    seed = sum(ord(c) for c in a["id"]) * 7919
    return f"{(seed % 900000) + 100000}{last}"

w("""# HollisMuse — the Olivo family: permanent demo data entry, v3 (2026-11-05)

You are **HollisMuse**, driving a real browser against the **live production**
Hollisworks app (`https://2ndactcapital.com`, tenant **2nd Act Capital**). This
pass is different from a test pass: you are **building a permanent demo
family** through the front end, exactly as a firm's staff would onboard a new
multigenerational client. Everything you enter stays. The family is fictional;
every name, address, email (`@example.com`) and account number below is
invented.

You have two jobs at once:
1. **Enter the data** in this file, completely and exactly.
2. **Report every place the app makes that hard or impossible** — a missing
   field, a missing screen, a confusing flow, a wrong total. That report is as
   valuable as the data.

Every step has an ID (e.g. `C.4`). Cite it in every finding. Work in order;
later phases depend on earlier ones.

---

## 0 · Rules — they override everything below

1. **Production data is real.** Only create and edit the Olivo records below.
   Never edit, merge or delete a record you did not create in this pass.
2. **No `MUSE –` prefix this time.** That prefix marks throwaway test rows and
   they are removed by clean-up scripts. Use the names exactly as written here.
   Instead, **add the tag `demo-olivo`** to every CRM record you create (people,
   organisations, entities) wherever the CRM offers tags. If a record type has
   no tags, note it once per type.
3. **Before creating anything, search for it.** If an Olivo record already
   exists (a re-run, or a partial earlier pass), open it and complete it — do
   not create a duplicate. A duplicate you created by mistake: report it, do
   not delete it.
4. **Approvals:** the default maker is the **org admin**; the checker is the
   **super admin** (§1). Approve only Olivo items, only as the second person.
   Try one approval as support staff — it should be refused.
5. **Never:** approve or post a fee run; route, send, fill or settle anything;
   start a trade; enter an API key; change a real job, model or budget. The
   Olivo accounts are **not billable** — if an account screen asks, choose
   not billable.
6. **No screen for it?** Record `BLOCKED — <what was missing>` against the
   step ID and continue with the next step. Do not invent a workaround (e.g.
   do not put a liability in a notes field unless the step says to).
7. **Preview only, never download.** Same as the test plan.
8. **Stop and report** if you see another family's data where you should not,
   a stack trace, or a control that acts without confirming.
9. Full account numbers are typed once, on the account form; afterwards the app
   must show only the mask (e.g. `****4471`). A full number displayed anywhere
   after saving is a **High** finding.

### Preconditions (check first; if one is not met, stop and report)
- **P.1** perms1, crmdetail1 and secmaster1 are merged and the security-master
  initial load has run: searching the security master for `AAPL`, `MSFT` and
  `VOO` finds each one. (If not, do phases A–H and J–L, skip I and M.)
- **P.2** The CRM shows relationship types beyond "ownership" (trustee,
  grantor, beneficiary, spouse, parent of …) and a Liabilities area — both come
  from crmdetail1. If either is missing, do the rest and mark those steps
  BLOCKED.

---

## v3 — one complete pass (read this)

crmdetail1 and portentry1 are merged: relationship types, trust / entity detail fields, client / contact
statuses, marital status, sub-type lists, insurance-policy fields, ownership by org admins, the narrow
`edit_crm_contact` permission for support staff, manual positions of every kind, **Assets & Investments**
(Add asset, Add private investment), liabilities, and net worth that recalculates itself. **No workarounds
this time** — anything that does not work as written below is a finding.

**Before Muse starts (Joe does this, not Muse):** the eight integrated Altruist accounts' positions are loaded
once through **Super Admin → Integrated import** from `docs/demo/olivo/data/import/integrated_positions.csv`,
approved by a second super admin. That file is keyed by account mask, so it is loaded **after phase H has
created the accounts** — Muse stops after H.check and reports; Joe loads the file; Muse continues with I.

## 1 · Personas — who does what

| Persona | Login | Role in this pass |
|---|---|---|
| **Org admin** | `jpl991723@gmail.com` | **Maker.** Creates every CRM record, entity, relationship, ownership %, household, account, position, asset, private investment, liability. |
| **Super admin** | `jlarizza@culmina.io` | **Checker.** Approves maker-checker items (non-default authority, held-away manual entries). The integrated import is Joe's step (§v3). |
| **Support staff** | `jpl991722@gmail.com` | Enters the **notes** (B.3) and the **Cipolla branch addresses** (C, marked) with `edit_crm_contact`; tries once to edit an ownership % and a trust detail field — both must be refused. |
| **Member A** | `jpl991721@gmail.com` | Read-only checks in phase O. Do **not** link this login to an Olivo person unless the developer has said to (open decision). |

---
""")

# ── A: organisations ──
w("## A · Outside organisations (non-client CRM records)\n")
w("Create each as an organisation / corporation record (not a client), tag `demo-olivo`, and put the note in the record's notes.\n")
w("| Step | Name | Type | Note |\n|---|---|---|---|")
for i, o in enumerate(d["organisations"], 1):
    w(f"| A.{i} | {o['name']} | {o['entity_type']} | {o['note']} |")
w("")

# ── B: people ──
w("## B · People\n")
w("**B.1** Create each person as an individual. Use the name parts exactly (prefix, first, middle, surname). Date of birth as given. "
  "Status: **client** for family members, **contact** for Claire Porcari, **deceased** follows from Theresa's date of death; marital status as given. Theresa Zucchini Olivo is **deceased** — enter her date of death "
  "if the form offers it (crmdetail1), otherwise mark her inactive and report.\n")
w("| Step | Person | Prefix | First | Middle | Surname | Born | Died | Maiden | Marital | Email | Phone | Status |")
w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for i, p in enumerate(d["people"], 1):
    w(f"| B.1.{i} | {p['name']} | {p.get('prefix','')} | {p['first']} | {p.get('middle','')} | {p['surname']} | {p['dob']} | {p.get('dod','')} | "
      f"{p.get('maiden','')} | {p['marital']} | {p.get('email','')} | {p.get('phone','')} | {p['status']}{' (minor)' if p.get('minor') else ''} |")
w("\n**B.2** For each minor (born after 2008-11-04), check the record shows them as a minor (or age under 18). Record what the app shows.\n")
w("**B.3** (as **support staff**) Add one note per person with the text below (note type: general / other).\n")
w("| Step | Person | Note |\n|---|---|---|")
for i, p in enumerate(d["people"], 1):
    w(f"| B.3.{i} | {p['name']} | {p['story']} |")
w("")

# ── C: addresses ──
w("## C · Addresses (including seasonal homes)\n")
w("Where a row says **seasonal**, use the CRM's seasonal-address feature with the months shown. Exactly one primary address per person or entity. "
  "Rows for Francesca, Mateo, Isabel, Nico and Gabriel (the Cipolla branch) are entered by **support staff**; the rest by the org admin.\n")
w("| Step | Record | Type | Address | Primary | Seasonal | Note |\n|---|---|---|---|---|---|---|")
for i, a in enumerate(d["addresses"], 1):
    s = f"{MON[a['season_from_month']-1]}–{MON[a['season_to_month']-1]}" if a["is_seasonal"] else ""
    w(f"| C.{i} | {names[a['entity_id']]} | {a['address_type']} | {a['street1']}, {a['city']}, {a['state']} {a['postal_code']} | "
      f"{'yes' if a['is_primary'] else ''} | {s} | {a['note']}{' **(support staff)**' if a['entity_id'] in ('P5','P6','P11','P12','P13') else ''} |")
w("\n**C.check** Open Dominic's record in December–April terms: does the CRM show Naples as the current address in those months and Greenwich otherwise? "
  "Record how seasonal addresses are displayed (and whether a seasonal address can also be the mailing address).\n")

# ── D: entities ──
w("## D · Legal entities\n")
w("Create each with the entity type, sub-type, state of formation, inception date and detail fields shown. "
  "Put **Purpose**, **Control** and **Notes** into the record's notes (one note each, labelled). Tag `demo-olivo`.\n")
w("| Step | Entity | Type | Sub-type | State | Inception | Detail fields |\n|---|---|---|---|---|---|---|")
for i, e in enumerate(d["entities"], 1):
    det = "; ".join(f"{k}={(' + '.join(names.get(x, x) for x in v.split(' + '))) if isinstance(v, str) else v}" for k, v in e["details"].items())
    w(f"| D.{i} | {e['name']} | {e['entity_type']} | {e['sub_type'] or ''} | {e['state'] or e['jurisdiction']} | {e['inception_date']} | {det} |")
w("\n| Step | Entity | Purpose | Control | Notes |\n|---|---|---|---|---|")
for i, e in enumerate(d["entities"], 1):
    w(f"| D.{i}.n | {e['name']} | {e['purpose']} | {e['control']} | {e['notes']} |")
w("")

# ── E: relationships ──
w("## E · Relationships — family and roles\n")
w("Add each as a relationship of the type shown, from the first record to the second, with the note. Then open three records and check the "
  "reverse reads correctly (e.g. on the Dynasty Trust: \"Trustee: Wilmington Fiduciary Co.\").\n")
w("| Step | From | Relationship | To | Note |\n|---|---|---|---|---|")
for i, r in enumerate(d["relationships"], 1):
    w(f"| E.{i} | {names[r['from_id']]} | {r['relationship_type']} | {names[r['to_id']]} | {r['note']} |")
w("")

# ── F: ownership ──
w("## F · Ownership (percentages)\n")
w("As **org admin**. Enter each as ownership with the percentage and the interest type in the note. The percentages of each owned entity add to 100.\n")
w("| Step | Owner | Owns | % | Interest |\n|---|---|---|---|---|")
for i, o in enumerate(d["ownership"], 1):
    w(f"| F.{i} | {names[o['owner_id']]} | {names[o['owned_id']]} | {o['pct']} | {o['interest']} |")
w("""
**F.check** Open the hierarchy / ownership graph for **Olivo Holdings LP**: owners above it (OH GP LLC 0.5%, the Revocable Trust 34.5%,
the Dynasty Trust 30%, AOF / Cipolla Olivo / MTO 10% each, the SLAT 5%) and its holdings below (Stag's Hollow 80%, Art Holdings 60%).
Switch look-through on and off. Record anything missing or wrong.
""")

# ── G: household ──
w("## G · Household\n")
w("**G.1** Portfolio → Households → **New household** `Olivo Family`. Members: all 14 family members (B.1.1–B.1.14; not Theresa, not Claire). "
  "**G.2** Add the 26 legal entities to the household if the screen allows entities as members; record what it allows.\n")

# ── H: accounts ──
w("## H · Accounts\n")
w("Household → **Add account** for each. Type the full number once; afterwards check only the mask shows. Owner = the CRM record. "
  "Authority and custody as given — a non-default authority opens a maker-checker draft: the **super admin** approves it. Not billable.\n")
w("| Step | Acct | Owner | Custodian | Type | Full number (type once) | Mask | Custody | Authority |\n|---|---|---|---|---|---|---|---|---|")
for i, a in enumerate(d["accounts"], 1):
    w(f"| H.{i} | {a['id']} | {names[a['owner_id']]} | {a['custodian']} | {a['type']} | `{acct_number(a)}` | {a['account_mask']} | {a['custody']} | {a['authority']} |")
w("\n**H.check** The account list shows 34 accounts, masks only. Any full number visible = High. **Then stop and report — Joe loads the integrated import (§v3) before phase I.**\n")

# ── I: holdings ──
INTEGRATED = {"A7", "A9", "A12", "A14", "A15", "A20", "A24", "A29"}
J_TICKERS = {"PE-HPP4", "PC-TCO2", "HF-STILL", "PRIV-BWA", "PRIV-NEHFC", "SPV-MERIDIAN", "VC-CALDERA2",
             "ANGEL-LUMA", "ANGEL-HALCYON", "ANGEL-PRESSBOX"}
MMF = {"FGTXX", "FDRXX", "SNOXX"}
MF = {"FXAIX", "FXNAX", "FSKAX", "FFFGX", "VWIUX", "SWPPX", "SWTSX"}
def kind(h):
    t = h["ticker"]
    if t in MMF: return "money_market"
    if t in MF: return "mutual_fund"
    if t.startswith("MUNI-"): return "municipal_bond (coupon %s%%, matures %s-07-01)" % (h["security"].split()[-1].replace("%", ""), t[5:])
    if t.startswith("CASH"): return "cash"
    if t.startswith("OPT-"): return "listed_option (only if a matching contract exists — else finding)"
    if t in ("BTC", "ETH"): return "digital_asset"
    if t.startswith("NOTE-"): return "structured note — master first (I.NOTE), else private"
    if t == "NLF": return "private — name it \"Northline Foods Inc. (fictional)\""
    if t.startswith(("529", "SMA-", "DAF-", "POL-")): return "private"
    return "listed — resolves in the master by ticker"
w("## I · Holdings (as of 2026-11-04)\n")
w("""**I.0 — stop here first.** Report that phase H is done and **wait until Joe confirms the integrated import is loaded** (§v3).
Then check: the eight integrated accounts (A7, A9, A12, A14, A15, A20, A24, A29) each show their positions (from the import, marked provisional /
"stated"), with the totals in the tables below. Do **not** enter positions on those accounts by hand.

**Every other account:** the held-away **manual entry**, one draft per account, as-of 2026-11-04, quantity + price (or value) + cost basis, with
the **Entry kind** shown. Listed tickers must resolve to the existing security — never create a security; a listed ticker that does not
resolve is a finding. The super admin approves each draft as the second person. Lines marked **→ J** are not entered here — they are private
investments, entered in phase J.
""")
cur = None
for h in d["holdings"]:
    if h["account"] != cur:
        cur = h["account"]
        a = next(x for x in d["accounts"] if x["id"] == cur)
        tag = " — **loaded by the integrated import (I.0): check only**" if cur in INTEGRATED else ""
        w(f"\n**I.{cur}** — {names[a['owner_id']]} · {a['custodian']} · {a['type']} ({a['custody']}){tag}\n")
        w("| Ticker / id | Security | Qty | Price | Value | Cost basis | Entry kind | Note |\n|---|---|---|---|---|---|---|---|")
    q = f"{h['quantity']:,}" if h["quantity"] not in (None, "") else ""
    pr = f"{h['price']:,}" if h["price"] not in (None, "") else ""
    k = "→ J (private investment)" if h["ticker"] in J_TICKERS else kind(h)
    w(f"| {h['ticker']} | {h['security']} | {q} | {pr} | {M(h['value'])} | {M(h['cost_basis']) if h['cost_basis'] not in (None,'') else ''} | {k} | {h['notes']} |")
w("""
**I.NLF** *Northline Foods (NLF)* is fictional — never map it to a real security. Enter it as a **private** manual asset named
"Northline Foods Inc. (fictional)" in all three accounts (A1, A6, A8); the three lines should share one org-level asset.

**I.NOTE** The two structured notes: search Replication → Notes for a JPMorgan autocallable on the S&P 500 and a Goldman Sachs buffered note on
the Nasdaq-100. If one exists, map the line to it and record its CUSIP; otherwise enter it as a private manual asset and say so.

**I.check** Each account's total equals the table total. Mutual funds and money-market funds show as org manual assets (the platform master does
not have them yet — expected) and the screen lists them as waiting to relink.
""")

# ── J: private investments ──
PI = [
 ("J.1", "E3", "Harbor Point Partners IV, L.P.", "fund", "2019-06-30", 2019, 30000000, 22000000, 4000000, 22300000, "NAV Q2 2026 (statement)", "O.n/a"),
 ("J.2", "E3", "Thornfield Credit Opportunities II", "fund", "2021-03-31", 2021, 15000000, 15000000, 0, 15100000, "NAV (statement); fully drawn; 11.2% net IRR", ""),
 ("J.3", "E3", "Stillwater Macro Fund Ltd.", "fund", "2022-01-01", 2022, None, 10000000, 0, 12400000, "hedge fund, quarterly liquidity; no commitment", ""),
 ("J.4", "E3", "Brightwater Aquaculture, Inc. — Series C Preferred", "direct deal", "2025-11-15", 2025, None, 6500000, 0, 6501600, "420,000 sh at $15.48 (last round Nov 2025); board observer: Anthony", ""),
 ("J.5", "E3", "New England Harbor FC — 2.5% LLC interest", "direct deal", "2023-05-01", 2023, None, 2500000, 0, 4000000, "estimate; capital call rights", ""),
 ("J.6", "E3", "2nd Act Marketplace SPV: Meridian Logistics Class A", "SPV", "2026-02-01", 2026, None, 3000000, 0, 3050000, "statement; do NOT create a marketplace deal", ""),
 ("J.7", "E14", "Caldera Ventures Fund II, L.P. — LP interest", "fund", "2022-09-30", 2022, 10000000, 6000000, 0, 9000000, "NAV (statement); Michael is a GP — GP carry unvalued", ""),
 ("J.8", "E14", "Luma Robotics — seed SAFE", "SAFE", "2024-04-01", 2024, None, 250000, 0, 750000, "estimate at the Series A price", ""),
 ("J.9", "E14", "Halcyon Bio — seed preferred", "direct deal", "2024-10-01", 2024, None, 500000, 0, 500000, "at cost", ""),
 ("J.10", "E14", "Pressbox Media — convertible note", "note", "2023-08-01", 2023, None, 150000, 0, 0, "written off 2025 — value 0", ""),
 ("J.11", "E16", "Harbor Point Partners IV, L.P. (foundation sleeve)", "fund", "2019-06-30", 2019, 2500000, 2500000, 0, 3200000, "NAV (statement); fully called", ""),
]
w("## J · Private investments and commitments\n")
w("On the owner's CRM record → **Assets & Investments → Add private investment**. Opening called and distributed amounts as given; the screen "
  "computes unfunded — it must match the last column. Latest value as a NAV / estimate valuation as of 2026-06-30 (funds) or 2026-11-04. "
  "Afterwards open the commitment and check the capital-call projection shows it.\n")
w("| Step | Owner | Investment | Type | Date | Vintage | Commitment | Called | Distributed | Latest value | Unfunded (expected) | Note |")
w("|---|---|---|---|---|---|---|---|---|---|---|---|")
for st, own, nm, ty, dt, vin, com, called, dist, val, note, _ in PI:
    unf = M(com - called) if com else "—"
    w(f"| {st} | {names[own]} | {nm} | {ty} | {dt} | {vin} | {M(com) if com else '—'} | {M(called)} | {M(dist)} | {M(val)} | {unf} | {note} |")
w("\n**J.check** Unfunded totals: Harbor Point IV $8,000,000 + Caldera II $4,000,000 = **$12,000,000**. These are commitments, not liabilities.\n")

# ── K: direct assets ──
METHOD = lambda n: "appraisal" if "apprais" in n.lower() else ("statement" if "statement" in n.lower() else "estimate")
w("## K · Direct assets (real estate, collectibles, operating business, cash)\n")
w("On the owner's record → **Assets & Investments → Add asset**: kind, name, owner and %, cost basis, valuation (value, date 2026-11-04 unless "
  "the note gives one, method as shown, source = the note). For the insurance policy use kind *insurance cash value* (it is entered on the "
  "ILIT in phase I as account A33's line, so skip it here if it already shows).\n")
w("| Step | Owner | Asset | Kind | Value | Cost basis | Method | Note |\n|---|---|---|---|---|---|---|---|")
KIND = {"Real estate": "real_estate", "Collectibles": "collectible", "Operating business": "operating_business", "Cash": "other (cash)"}
for i, x in enumerate(d["direct_assets"], 1):
    w(f"| K.{i} | {names[x['owner_id']]} | {x['asset']} | {KIND.get(x['asset_class'], x['asset_class'])} | {M(x['value_usd'])} | "
      f"{M(x['cost_basis_usd']) if x['cost_basis_usd'] not in (None,'') else ''} | {METHOD(x['notes'])} | {x['notes']} |")
w("")

# ── L: liabilities ──
w("## L · Liabilities\n")
w("Enter each under the borrower entity, with the lender (create the lender as an organisation if it is not in phase A), the kind, balance "
  "and terms. Off-balance-sheet commitments are NOT liabilities here — they were entered in J.\n")
w("| Step | Borrower | Liability | Kind | Balance | Terms | Note |\n|---|---|---|---|---|---|---|")
for i, l in enumerate(d["liabilities"], 1):
    if "commitment" in l["kind"].lower(): continue
    w(f"| L.{i} | {names[l['owner_id']]} | {l['liability']} | {l['kind']} | {M(l['amount_usd'])} | {l['terms']} | {l['notes']} |")
w("")

# ── M/N/O checks ──
w("## M · Security-master check\n")
w("**M.1** Search the security master for every distinct *listed* ticker in phase I; record found / not found. "
  "**M.2** List every line you could not map, with its account and the reason the screen gave.\n")
w("## N · Reconciliation — do the numbers add up?\n")
w(f"Expected (bottom-up, from this file): family net worth **{M(d['totals']['family_net_worth_usd'])}**; charitable entities "
  f"**{M(d['totals']['charitable_usd'])}**. Record what the app shows for each entity below (net of liabilities, after look-through) and the "
  "difference. First press **Recalculate** on the household. Every difference is a finding unless a BLOCKED step explains it.\n")
w("| Entity | Expected net value | App shows | Difference |\n|---|---|---|---|")
for eid, v in sorted(T["nav"].items(), key=lambda kv: int(kv[0][1:])):
    w(f"| {names[eid]} | {M(v)} | | |")
w("""
## O · Views per persona (read-only)

- **O.1** Org admin: the Olivo household, the hierarchy graph, the X-ray (if it runs on held-away data), net worth. Screenshots.
- **O.2** Support staff: can they see the Olivo family? Everything, or only what they are assigned? Record.
- **O.3** Member A: must **not** see the Olivo family anywhere (search, CRM URL from a staff session, household URL). Any Olivo name visible
  to Member A is **Critical**.
- **O.4** Super admin: platform security master shows the mapped tickers; nothing Olivo-specific leaks into platform screens.

## Report

Use the test plan's finding format (ID `MUSE-OLIVO-<step>-<nn>`). End with:
1. A table: phase · steps done · BLOCKED · findings by severity.
2. **Every record you created**, by exact name (they stay — this list is the demo inventory).
3. Every BLOCKED step with what was missing — this becomes the next sprint.
4. The reconciliation table (N) filled in.
5. Your top 5 "an adviser onboarding a family like this would trip over this" observations.
""")
open(f"{OUT}/HOLLISMUSE_OLIVO_DATA_ENTRY_v3.md", "w").write("\n".join(L))
print(len(L), "lines")
