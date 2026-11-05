# The Olivo family — permanent demo data

A fictional four-generation family used as **permanent demo / test data** for
Hollisworks. Every name, address, email (`@example.com`) and account number is
invented. It is entered through the **front end** by HollisMuse (the browser
QA agent), the way a firm's staff would onboard a new family — so loading it
also tests the onboarding screens.

| | |
|---|---|
| People | 16 (14 family across 3 generations, Theresa — deceased, Claire Porcari — contact) |
| Legal entities | 26 (family office, GP, family LP, 12 trusts, foundation, DAF, real-estate / operating / art LLCs) |
| Outside organisations | 8 (corporate trustees, DAF sponsor, Northline Foods, Caldera Ventures, lenders) |
| Addresses | 62, incl. seasonal homes (Greenwich / Naples, San Francisco / Aspen, college addresses) |
| Relationships | 117 (spouse, parent of, grantor, trustee, beneficiary, managing member, board, donor advisor …) |
| Ownership links | 29 (percentages; every owned entity sums to 100) |
| Accounts | 34 at 9 custodians (integrated / other custodian / outside; read-only / advise / managed) |
| Holdings | 95 (listed ETFs and stocks, mutual funds, munis, structured notes, options, crypto, private funds, directs, 529s, a life policy) |
| Direct assets | 19 (real estate, vineyard, art, cars, jewelry, cash) |
| Liabilities | 7 (+2 unfunded commitments, entered as commitments) |
| Family net worth | $601.8M; charitable entities $62.2M (bottom-up, reconciled) |

Surnames: Olivo (main family), Meloni, Broccoli, Cipolla, Zucchini, Porcari.

## Files

```
docs/demo/olivo/
  README.md                              this file
  HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md      the step-by-step entry plan HollisMuse follows (phases A–O)
  olivo_family.html                      one-page view of the family (structure, net worth, accounts)
  data/
    olivo_family.json                    base dataset (people, entities, ownership, accounts, holdings, assets, liabilities)
    olivo_crm.json                       base + CRM detail (DOBs, name parts, addresses, relationships, detail fields, security-master mapping)
    crm_people.csv, crm_entities.csv, crm_organisations.csv, crm_addresses.csv, crm_relationships.csv
    people.csv, entities.csv, ownership.csv, beneficial_interests.csv, accounts.csv,
    holdings.csv, holdings_secmaster.csv, direct_assets.csv, liabilities.csv
    olivo_structure.mmd                  Mermaid ownership chart
    _totals.json                         expected net value per entity (the reconciliation targets)
  generator/
    build.py      → data/olivo_family.json, base CSVs, _totals.json, olivo_structure.mmd (reconciles net worth bottom-up)
    enrich.py     → data/olivo_crm.json, crm_*.csv, holdings_secmaster.csv
    muse_plan.py  → HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md
    page.py       → olivo_family.html
```

## Regenerate

From `docs/demo/olivo/generator/` (plain Python 3, no packages):

```bash
mkdir -p data && python3 build.py && python3 enrich.py && python3 muse_plan.py && python3 page.py
```

The scripts write next to themselves (`data/`, the .md, the .html); move the
outputs up one level if you regenerate in place. Change the family in
`build.py` (people, entities, accounts, holdings, assets, liabilities) and
`enrich.py` (DOBs, addresses, relationships, detail fields); the totals and the
Muse plan follow automatically.

## Load into an instance (production, a new tenant, or a re-load)

**Prerequisites** (sprints merged): perms1 (households + add account),
secmaster1 + its initial load (listed tickers resolve), crmdetail1
(relationship types, trust / entity detail fields, people fields,
liabilities, demo protection). Known gaps at the time of writing: mutual /
money-market funds are not in the security master (secmaster2), the
integrated Altruist accounts have no positions path without a feed, Northline
Foods (NLF) is fictional, no price history.

**Steps**
1. Confirm the four personas exist in the target tenant (org admin = maker,
   super admin = checker, support staff, member) — §1 of the Muse plan. For
   another tenant, change the logins and the app URL at the top of the plan.
2. Give HollisMuse `HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md`. It searches before
   creating, so a **re-load completes a partial load** instead of duplicating.
3. Every record is tagged **`demo-olivo`**. Clean-up scripts and verify
   teardowns never delete `demo-*` rows (crmdetail1). Throwaway test rows use
   the `MUSE –` prefix instead — never mix the two.
4. After the pass, compare Muse's reconciliation table (phase N) with
   `data/_totals.json`; every difference should be explained by a BLOCKED step.
5. Record the run (date, tenant, Muse report, BLOCKED steps) in
   `docs/PROJECT_STATUS.md` under "Olivo demo data".

**Removing it** (only if ever needed): there is deliberately no script. Find
the records by the `demo-olivo` tag and the `Olivo Family` household, and
remove them by id with the documented privileged path — the demo protection
guard must be lifted explicitly for that run.

## Protection rule (crmdetail1)
Every Olivo record is tagged `demo-olivo`. A record tagged `demo-*`, or a household whose members are all
demo-tagged, can never be deleted by a script: the database's BEFORE DELETE guard refuses it in a real org (the
fixture path `crm.demo_teardown = 'fixture'` works for `is_test` orgs only), `_verify_cleanup` and the MUSE removal
scripts refuse any plan that touches it, and the app refuses to delete the demo household. "Removing it" therefore
means retiring / editing in the app; there is no privileged path for a real org's demo rows. See
`docs/demo/README.md`.

## Decisions still open (2026-11-04)
- Which tenant hosts the demo (2nd Act's book, tagged and non-billable, vs a
  separate demo firm).
- Integrated Altruist accounts: enter as held-away, or a demo positions upload.
- NLF: replace with a real ticker, or a private security.
- Whether member persona `jpl991721` is linked to Anthony Olivo for
  member-portal demos.
