"""Generates HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md from data/olivo_crm.json."""
import json, os
OUT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(f"{OUT}/data/olivo_crm.json"))
T = json.load(open(f"{OUT}/data/_totals.json"))
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

w("""# HollisMuse — the Olivo family: permanent demo data entry, v2 (2026-11-04)

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

## v2 — what changed after crmdetail1 (read this)

crmdetail1 added relationship types, trust / entity detail fields, maiden name, date of death, the minor flag,
liabilities and demo protection. Its step-by-step check found these **known gaps** — do not report them as new
findings; follow the workaround and move on:

| Gap | Affects | Workaround in this pass |
|---|---|---|
| GAP-CRM-01 no "client / contact" status | B.1 status | family → **active**; Claire Porcari → **active** + tag `contact` |
| GAP-CRM-02 no marital-status field | B.1 | skip the field; note once |
| GAP-CRM-03 fixed LLC sub-type list | D.20–D.26 | pick the closest sub-type (or none); put the intended one in the record's notes |
| GAP-CRM-04 no fund_role / ein_status / policy fields | D.3, D.16, D.19 | put those values in the record's notes |
| GAP-CRM-05 org admin cannot write ownership | F | **super admin** enters phase F |
| GAP-CRM-06 support staff cannot edit CRM notes / addresses | B.3, C (Cipolla) | support staff tries **B.3.1 once** (expect refused, record it); the **org admin** enters the rest |
| GAP-PORT-01 manual positions only for securities in the master | I | enter only **listed** tickers; skip mutual funds, munis, cash, private, options, crypto (⏭ GAP-PORT-01) |
| GAP-PORT-02 integrated (Altruist) accounts have no positions path | I (A7, A9, A12, A14, A15, A20, A24, A29) | ⏭ GAP-PORT-02 |
| GAP-PORT-03 no direct-asset entry | K | ⏭ GAP-PORT-03 |
| GAP-PORT-04 no private-fund / commitment entry | J | ⏭ GAP-PORT-04 |
| GAP-PORT-05 net worth needs a rollup run | N | if no "recalculate" control exists, record what shows and stop |

So this pass loads the **CRM side completely** (A–H, L) and the listed holdings on non-integrated accounts; J, K
and most of I wait for a follow-up sprint. Because Muse searches before creating, the next pass completes the
same family without duplicates.

## 1 · Personas — who does what

| Persona | Login | Role in this pass |
|---|---|---|
| **Org admin** | `jpl991723@gmail.com` | **Maker.** Creates every CRM record, household, account, liability, notes and addresses (GAP-CRM-06). |
| **Super admin** | `jlarizza@culmina.io` | **Checker.** Approves maker-checker items (non-default authority, held-away imports, valuations if gated). Also maps securities if the master needs a Super Admin. |
| **Support staff** | `jpl991722@gmail.com` | Tries B.3.1 once (expected refused — GAP-CRM-06) and does the read-only checks in O.2. |
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
  "Status: **active** for everyone (GAP-CRM-01); add tag `contact` to Claire Porcari; skip marital status (GAP-CRM-02). Theresa Zucchini Olivo is **deceased** — enter her date of death "
  "if the form offers it (crmdetail1), otherwise mark her inactive and report.\n")
w("| Step | Person | Prefix | First | Middle | Surname | Born | Died | Maiden | Marital | Email | Phone | Status |")
w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for i, p in enumerate(d["people"], 1):
    w(f"| B.1.{i} | {p['name']} | {p.get('prefix','')} | {p['first']} | {p.get('middle','')} | {p['surname']} | {p['dob']} | {p.get('dod','')} | "
      f"{p.get('maiden','')} | {p['marital']} | {p.get('email','')} | {p.get('phone','')} | {p['status']}{' (minor)' if p.get('minor') else ''} |")
w("\n**B.2** For each minor (born after 2008-11-04), check the record shows them as a minor (or age under 18). Record what the app shows.\n")
w("**B.3** (as **org admin**; support staff tries B.3.1 first — GAP-CRM-06) Add one note per person with the text below (note type: general / other).\n")
w("| Step | Person | Note |\n|---|---|---|")
for i, p in enumerate(d["people"], 1):
    w(f"| B.3.{i} | {p['name']} | {p['story']} |")
w("")

# ── C: addresses ──
w("## C · Addresses (including seasonal homes)\n")
w("Where a row says **seasonal**, use the CRM's seasonal-address feature with the months shown. Exactly one primary address per person or entity. "
  "All by the org admin (GAP-CRM-06).\n")
w("| Step | Record | Type | Address | Primary | Seasonal | Note |\n|---|---|---|---|---|---|---|")
for i, a in enumerate(d["addresses"], 1):
    s = f"{MON[a['season_from_month']-1]}–{MON[a['season_to_month']-1]}" if a["is_seasonal"] else ""
    w(f"| C.{i} | {names[a['entity_id']]} | {a['address_type']} | {a['street1']}, {a['city']}, {a['state']} {a['postal_code']} | "
      f"{'yes' if a['is_primary'] else ''} | {s} | {a['note']} |")
w("\n**C.check** Open Dominic's record in December–April terms: does the CRM show Naples as the current address in those months and Greenwich otherwise? "
  "Record how seasonal addresses are displayed (and whether a seasonal address can also be the mailing address).\n")

# ── D: entities ──
w("## D · Legal entities\n")
w("Create each with the entity type, sub-type, state of formation, inception date and detail fields shown. "
  "Put **Purpose**, **Control** and **Notes** into the record's notes (one note each, labelled). Tag `demo-olivo`.\n")
w("| Step | Entity | Type | Sub-type | State | Inception | Detail fields |\n|---|---|---|---|---|---|---|")
for i, e in enumerate(d["entities"], 1):
    det = "; ".join(f"{k}={names.get(v, v) if isinstance(v, str) else v}" for k, v in e["details"].items())
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
w("**As super admin (GAP-CRM-05).** Enter each as ownership with the percentage and the interest type in the note. The percentages of each owned entity add to 100.\n")
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
w("\n**H.check** The account list shows 34 accounts, masks only. Any full number visible = High.\n")

# ── I: holdings ──
w("## I · Holdings (as of 2026-11-04)\n")
w("""For each account, enter its positions. Route:
- **other_custodian / outside** accounts: the held-away **manual entry** (positions with quantity, price or value, cost basis, as-of 2026-11-04).
  Held-away entries may need the super admin's approval — approve as checker.
- **integrated (Altruist)** accounts: positions normally arrive from the custodian feed. Record what the screen offers; if it offers no
  manual path, mark `BLOCKED — integrated account, no feed` and continue (open decision for the developer).
- The **Security master** column says how the line maps. "listed" lines must resolve to an existing security by ticker — never create a
  new security for them; a listed ticker that does not resolve is a finding. "mutual fund" lines are expected NOT to resolve yet (known gap)
  — enter them as unmapped/manual if the screen allows, and list them in the report.
""")
cur = None
for h in d["holdings"]:
    if h["account"] != cur:
        cur = h["account"]
        a = next(x for x in d["accounts"] if x["id"] == cur)
        w(f"\n**I.{cur}** — {names[a['owner_id']]} · {a['custodian']} · {a['type']} ({a['custody']})\n")
        w("| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |\n|---|---|---|---|---|---|---|---|")
    q = f"{h['quantity']:,}" if h["quantity"] not in (None, "") else ""
    pr = f"{h['price']:,}" if h["price"] not in (None, "") else ""
    w(f"| {h['ticker']} | {h['security']} | {q} | {pr} | {M(h['value'])} | {M(h['cost_basis']) if h['cost_basis'] not in (None,'') else ''} | {h['secmaster']} | {h['notes']} |")
w("""
**I.NLF** *Northline Foods (NLF)* is fictional. Do not map it to any real security. If the platform offers a "security not in the master"
path (a private / manual security), use it and note the flow; otherwise mark BLOCKED.

**I.NOTE** The two structured notes: search Replication → Notes for a JPMorgan autocallable on the S&P 500 and a Goldman Sachs buffered note on
the Nasdaq-100. If one exists, map the position to it and record which (its CUSIP); if not, enter as manual and report.
""")

# ── J/K: private investments & direct assets ──
w("## J · Private investments and commitments — ⏭ GAP-PORT-04 in this pass (kept for the next pass)\n")
w("Lines in phase I marked *private / manual asset* that are funds or direct deals (Harbor Point IV, Thornfield II, Stillwater, Brightwater, "
  "New England Harbor FC, the 2nd Act Marketplace SPV, Caldera Fund II, Luma, Halcyon, Pressbox) are entered as **private investments** with "
  "their value as the latest valuation. Commitments: **Harbor Point Partners IV** — $8,000,000 unfunded (Olivo Holdings LP); "
  "**Caldera Ventures Fund II** — $4,000,000 unfunded (MTO Capital LLC). Enter those on the commitments screen if it exists.\n")
w("**J.SPV** The SPV line names a 2nd Act Marketplace deal (Meridian Logistics). If the marketplace has no such deal, enter it as a private "
  "investment and say so — do not create a marketplace deal.\n")
w("## K · Direct assets — ⏭ GAP-PORT-03 in this pass (kept for the next pass)\n")
w("Enter each as an asset owned by the entity, with its value as a valuation (method: appraisal / estimate as the note says) and the cost basis.\n")
w("| Step | Owner | Asset | Class | Sub-class | Value | Cost basis | Note |\n|---|---|---|---|---|---|---|---|")
for i, x in enumerate(d["direct_assets"], 1):
    w(f"| K.{i} | {names[x['owner_id']]} | {x['asset']} | {x['asset_class']} | {x['sub_class']} | {M(x['value_usd'])} | "
      f"{M(x['cost_basis_usd']) if x['cost_basis_usd'] not in (None,'') else ''} | {x['notes']} |")
w("")

# ── L: liabilities ──
w("## L · Liabilities (crmdetail1)\n")
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
  "difference. A difference is not automatically a bug — say which data you could not enter (BLOCKED steps) that explains it.\n")
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
open(f"{OUT}/HOLLISMUSE_OLIVO_DATA_ENTRY_v2.md", "w").write("\n".join(L))
print(len(L), "lines")
