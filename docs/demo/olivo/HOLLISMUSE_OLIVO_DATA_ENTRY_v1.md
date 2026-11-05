# HollisMuse — the Olivo family: permanent demo data entry, v1 (2026-11-04)

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

## 1 · Personas — who does what

| Persona | Login | Role in this pass |
|---|---|---|
| **Org admin** | `jpl991723@gmail.com` | **Maker.** Creates every CRM record, household, account, asset, liability. |
| **Super admin** | `jlarizza@culmina.io` | **Checker.** Approves maker-checker items (non-default authority, held-away imports, valuations if gated). Also maps securities if the master needs a Super Admin. |
| **Support staff** | `jpl991722@gmail.com` | Enters the **notes** in phase B.3 and the **addresses** for the Cipolla branch (C.3). Record anything this role is refused. |
| **Member A** | `jpl991721@gmail.com` | Read-only checks in phase O. Do **not** link this login to an Olivo person unless the developer has said to (open decision). |

---

## A · Outside organisations (non-client CRM records)

Create each as an organisation / corporation record (not a client), tag `demo-olivo`, and put the note in the record's notes.

| Step | Name | Type | Note |
|---|---|---|---|
| A.1 | Wilmington Fiduciary Co. | corporation | Corporate trustee (Delaware) — Dynasty Trust and AO Descendants Trust |
| A.2 | Coastal Bank, N.A. — Trust Department | corporation | Corporate trustee — SLAT, Theresa trust, CRUT; also operating bank and lender |
| A.3 | Harbor Charitable Trust | foundation | Donor-advised fund sponsor (public charity) for the Olivo Giving Fund |
| A.4 | Northline Foods Inc. | corporation | Acquirer of Olivo Foods (2014); tenant of the three industrial properties; the family's concentrated stock |
| A.5 | Caldera Ventures | lp | Michael's venture firm (GP); the family is an LP in Fund II |
| A.6 | Cipolla Studio | llc | Mateo's architecture practice |
| A.7 | Farm Credit West | corporation | Lender — Stag's Hollow agricultural term loan |
| A.8 | First Western Trust | corporation | Lender — Aspen mortgage |

## B · People

**B.1** Create each person as an individual. Use the name parts exactly (prefix, first, middle, surname). Date of birth as given. Status: `client` for family members, `contact` for Claire Porcari. Theresa Zucchini Olivo is **deceased** — enter her date of death if the form offers it (crmdetail1), otherwise mark her inactive and report.

| Step | Person | Prefix | First | Middle | Surname | Born | Died | Maiden | Marital | Email | Phone | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B.1.1 | Dominic J. Olivo |  | Dominic | James | Olivo | 1951-03-19 |  |  | married | dominic.olivo@example.com | +1 203 555 0101 | client |
| B.1.2 | Marguerite Meloni Olivo |  | Marguerite | Meloni | Olivo | 1968-09-02 |  | Meloni | married | marguerite.olivo@example.com | +1 203 555 0102 | client |
| B.1.3 | Anthony D. Olivo |  | Anthony | Dominic | Olivo | 1976-06-13 |  |  | married | anthony.olivo@example.com | +1 203 555 0103 | client |
| B.1.4 | Dr. Elena Olivo | Dr. | Elena |  | Olivo | 1978-11-27 |  | Broccoli | married | elena.olivo@example.com | +1 203 555 0104 | client |
| B.1.5 | Francesca Olivo-Cipolla |  | Francesca |  | Olivo-Cipolla | 1979-04-08 |  |  | married | francesca@cipollastudio.example.com | +1 305 555 0105 | client |
| B.1.6 | Mateo Cipolla |  | Mateo |  | Cipolla | 1977-01-22 |  |  | married | mateo@cipollastudio.example.com | +1 305 555 0106 | client |
| B.1.7 | Michael T. Olivo |  | Michael | Thomas | Olivo | 1983-08-30 |  |  | divorced | michael@calderavc.example.com | +1 415 555 0107 | client |
| B.1.8 | Giulia Olivo |  | Giulia |  | Olivo | 2013-05-16 |  |  | single |  |  | client (minor) |
| B.1.9 | Sofia Olivo |  | Sofia |  | Olivo | 2004-02-11 |  |  | single | sofia.olivo@example.com | +1 203 555 0109 | client |
| B.1.10 | Luca Olivo |  | Luca |  | Olivo | 2007-10-05 |  |  | single | luca.olivo@example.com | +1 203 555 0110 | client |
| B.1.11 | Isabel Cipolla |  | Isabel |  | Cipolla | 2009-07-24 |  |  | single |  |  | client (minor) |
| B.1.12 | Nico Cipolla |  | Nico |  | Cipolla | 2013-12-03 |  |  | single |  |  | client (minor) |
| B.1.13 | Gabriel Cipolla |  | Gabriel |  | Cipolla | 2013-12-03 |  |  | single |  |  | client (minor) |
| B.1.14 | Theo Olivo |  | Theo |  | Olivo | 2012-03-28 |  |  | single |  |  | client (minor) |
| B.1.15 | Theresa Zucchini Olivo |  | Theresa | Zucchini | Olivo | 1953-10-14 | 2008-02-09 | Zucchini | married |  |  | deceased |
| B.1.16 | Claire Porcari |  | Claire |  | Porcari | 1985-06-07 |  |  | divorced | claire.porcari@example.com | +1 650 555 0116 | contact |

**B.2** For each minor (born after 2008-11-04), check the record shows them as a minor (or age under 18). Record what the app shows.

**B.3** (as **support staff**) Add one note per person with the text below (note type: general / other).

| Step | Person | Note |
|---|---|---|
| B.3.1 | Dominic J. Olivo | Founder of Olivo Foods (frozen Italian specialties, sold to Northline Foods in 2014). Widowed 2008; remarried 2011. Still chairs the foundation; drives the cars. |
| B.3.2 | Marguerite Meloni Olivo | Dominic's second wife; former gallery director. Prenuptial agreement; provided for through the 2019 SLAT. |
| B.3.3 | Anthony D. Olivo | Eldest. Ran Olivo Foods' operations through the sale; chairs the family office and sits on the Dynasty Trust distribution committee. |
| B.3.4 | Dr. Elena Olivo | Anthony's wife; interventional cardiologist at a Stamford hospital. |
| B.3.5 | Francesca Olivo-Cipolla | Middle child. Contemporary-art dealer with a gallery in Miami; advises the family's art holdings. |
| B.3.6 | Mateo Cipolla | Francesca's husband; architect (Cipolla Studio). |
| B.3.7 | Michael T. Olivo | Youngest of the first marriage. Venture investor in San Francisco (GP of Caldera Ventures). Divorced 2021. |
| B.3.8 | Giulia Olivo | Dominic and Marguerite's daughter. Minor; assets held in a 2503(c) trust. |
| B.3.9 | Sofia Olivo | Anthony & Elena's daughter; college senior (economics). |
| B.3.10 | Luca Olivo | Anthony & Elena's son; college freshman. |
| B.3.11 | Isabel Cipolla | Francesca & Mateo's daughter. |
| B.3.12 | Nico Cipolla | Francesca & Mateo's son (twin). |
| B.3.13 | Gabriel Cipolla | Francesca & Mateo's son (twin). |
| B.3.14 | Theo Olivo | Michael's son; shared custody. |
| B.3.15 | Theresa Zucchini Olivo | Dominic's first wife, mother of Anthony, Francesca and Michael. Died 2008; her estate funded the Theresa Olivo Family Trust. |
| B.3.16 | Claire Porcari | Michael's former wife (divorced 2021), Theo's mother; shared custody. Not a client — contact for Theo's trust and school matters only. |

## C · Addresses (including seasonal homes)

Where a row says **seasonal**, use the CRM's seasonal-address feature with the months shown. Exactly one primary address per person or entity. C.3 (the Cipolla branch: Francesca, Mateo, Isabel, Nico, Gabriel) is done by **support staff**; the rest by the org admin.

| Step | Record | Type | Address | Primary | Seasonal | Note |
|---|---|---|---|---|---|---|
| C.1 | Dominic J. Olivo | primary_residence | 38 Round Hill Road, Greenwich, CT 06831 | yes | May–Nov | Greenwich May–November |
| C.2 | Dominic J. Olivo | primary_residence | 2950 Galleon Drive, Naples, FL 34102 |  | Dec–Apr | Naples December–April (Port Royal, owned by Port Royal Residence LLC) |
| C.3 | Marguerite Meloni Olivo | primary_residence | 38 Round Hill Road, Greenwich, CT 06831 | yes | May–Nov | Greenwich May–November |
| C.4 | Marguerite Meloni Olivo | primary_residence | 2950 Galleon Drive, Naples, FL 34102 |  | Dec–Apr | Naples December–April (Port Royal, owned by Port Royal Residence LLC) |
| C.5 | Giulia Olivo | primary_residence | 38 Round Hill Road, Greenwich, CT 06831 | yes |  |  |
| C.6 | Anthony D. Olivo | primary_residence | 41 Pear Tree Point Road, Darien, CT 06820 | yes |  |  |
| C.7 | Dr. Elena Olivo | primary_residence | 41 Pear Tree Point Road, Darien, CT 06820 | yes |  |  |
| C.8 | Dr. Elena Olivo | business | 30 Shelburne Road (cardiology practice), Stamford, CT 06902 |  |  |  |
| C.9 | Sofia Olivo | primary_residence | 41 Pear Tree Point Road, Darien, CT 06820 | yes | Jun–Aug | Home for the summer |
| C.10 | Sofia Olivo | mailing | Box 4127, 69 Brown Street, Providence, RI 02912 |  | Sep–May | College, September–May |
| C.11 | Luca Olivo | primary_residence | 41 Pear Tree Point Road, Darien, CT 06820 | yes | Jun–Aug | Home for the summer |
| C.12 | Luca Olivo | mailing | HB 3311, 6 North Main Street, Hanover, NH 03755 |  | Sep–May | College, September–May |
| C.13 | Francesca Olivo-Cipolla | primary_residence | 1450 Brickell Bay Drive, Unit 4101, Miami, FL 33131 | yes |  |  |
| C.14 | Mateo Cipolla | primary_residence | 1450 Brickell Bay Drive, Unit 4101, Miami, FL 33131 | yes |  |  |
| C.15 | Isabel Cipolla | primary_residence | 1450 Brickell Bay Drive, Unit 4101, Miami, FL 33131 | yes |  |  |
| C.16 | Nico Cipolla | primary_residence | 1450 Brickell Bay Drive, Unit 4101, Miami, FL 33131 | yes |  |  |
| C.17 | Gabriel Cipolla | primary_residence | 1450 Brickell Bay Drive, Unit 4101, Miami, FL 33131 | yes |  |  |
| C.18 | Francesca Olivo-Cipolla | business | 180 NE 41st Street (gallery), Miami, FL 33137 |  |  |  |
| C.19 | Mateo Cipolla | business | Cipolla Studio, 3250 NE 1st Avenue, Miami, FL 33137 |  |  |  |
| C.20 | Michael T. Olivo | primary_residence | 2840 Broadway, San Francisco, CA 94115 | yes | Apr–Nov | San Francisco April–November |
| C.21 | Michael T. Olivo | primary_residence | 120 Ajax Avenue, Aspen, CO 81611 |  | Dec–Mar | Aspen December–March (owned by Ajax Peak LLC) |
| C.22 | Michael T. Olivo | business | Caldera Ventures, 550 Jackson Street, San Francisco, CA 94133 |  |  |  |
| C.23 | Theo Olivo | primary_residence | 2840 Broadway, San Francisco, CA 94115 | yes |  | Shared custody — alternate weeks with his mother in Palo Alto |
| C.24 | Claire Porcari | primary_residence | 1120 Hamilton Avenue, Palo Alto, CA 94301 | yes |  |  |
| C.25 | Olivo Family Office LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.26 | Olivo Family Office LLC | registered | c/o registered agent, 1209 Orange Street, Wilmington, DE 19801 |  |  |  |
| C.27 | OH GP LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.28 | OH GP LLC | registered | c/o registered agent, 1209 Orange Street, Wilmington, DE 19801 |  |  |  |
| C.29 | Olivo Holdings LP | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.30 | Olivo Holdings LP | registered | c/o registered agent, 1209 Orange Street, Wilmington, DE 19801 |  |  |  |
| C.31 | Dominic J. Olivo Revocable Trust | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.32 | Olivo 2014 Dynasty Trust | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.33 | Olivo 2014 Dynasty Trust | registered | c/o registered agent, 1209 Orange Street, Wilmington, DE 19801 |  |  |  |
| C.34 | Marguerite Olivo 2019 SLAT | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.35 | DJO 2021 GRAT | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.36 | Theresa Olivo Family Trust | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.37 | Giulia Olivo 2503(c) Trust | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.38 | AOF Investments LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.39 | AO Descendants Trust 2018 | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.40 | AO Descendants Trust 2018 | registered | c/o registered agent, 1209 Orange Street, Wilmington, DE 19801 |  |  |  |
| C.41 | Cipolla Olivo Holdings LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.42 | Cipolla 2020 Family Trust | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.43 | MTO Capital LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.44 | MTO Capital LLC | registered | c/o registered agent, 1209 Orange Street, Wilmington, DE 19801 |  |  |  |
| C.45 | Theo Olivo 2020 Trust | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.46 | Olivo Family Foundation | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.47 | Olivo Giving Fund | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.48 | Olivo 2016 CRUT | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.49 | Olivo 2012 ILIT | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.50 | Round Hill Road LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.51 | Port Royal Residence LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.52 | Stag's Hollow Vineyard LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.53 | Olivo Commercial RE LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.54 | Ajax Peak LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.55 | Brickell Bay 41 LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.56 | Olivo Art Holdings LLC | business | 2 Greenwich Office Park, Suite 300, Greenwich, CT 06831 | yes |  | Administered by the family office |
| C.57 | Olivo Art Holdings LLC | registered | c/o registered agent, 1209 Orange Street, Wilmington, DE 19801 |  |  |  |
| C.58 | Round Hill Road LLC | business | 38 Round Hill Road, Greenwich, CT 06831 |  |  | Property address |
| C.59 | Port Royal Residence LLC | business | 2950 Galleon Drive, Naples, FL 34102 |  |  | Property address |
| C.60 | Stag's Hollow Vineyard LLC | business | 4100 Silverado Trail, Napa, CA 94558 |  |  | Vineyard and winery |
| C.61 | Ajax Peak LLC | business | 120 Ajax Avenue, Aspen, CO 81611 |  |  | Property address |
| C.62 | Brickell Bay 41 LLC | business | 1450 Brickell Bay Drive, Unit 4101, Miami, FL 33131 |  |  | Condo; gallery space at 180 NE 41st Street |

**C.check** Open Dominic's record in December–April terms: does the CRM show Naples as the current address in those months and Greenwich otherwise? Record how seasonal addresses are displayed (and whether a seasonal address can also be the mailing address).

## D · Legal entities

Create each with the entity type, sub-type, state of formation, inception date and detail fields shown. Put **Purpose**, **Control** and **Notes** into the record's notes (one note each, labelled). Tag `demo-olivo`.

| Step | Entity | Type | Sub-type | State | Inception | Detail fields |
|---|---|---|---|---|---|---|
| D.1 | Olivo Family Office LLC | family_office |  | DE | 2015-02-15 |  |
| D.2 | OH GP LLC | gp |  | DE | 2015-03-15 |  |
| D.3 | Olivo Holdings LP | lp |  | DE | 2015-04-15 | fund_role=family_partnership; valuation_policy=funds at latest reported NAV; directs at last round; real assets at appraisal |
| D.4 | Dominic J. Olivo Revocable Trust | trust | revocable | CT | 1998-05-15 | trust_type=revocable; revocable=True; situs=CT; gst_exempt=False; termination=pour-over at grantor's death |
| D.5 | Olivo 2014 Dynasty Trust | trust | dynasty | DE | 2014-06-15 | trust_type=dynasty; revocable=False; situs=DE; gst_exempt=True; termination=perpetual (Delaware); funded=Northline Foods stock at the 2014 sale |
| D.6 | Marguerite Olivo 2019 SLAT | trust | slat | CT | 2019-07-15 | trust_type=slat; revocable=False; situs=CT; gst_exempt=False; beneficiaries_note=spouse and Giulia |
| D.7 | DJO 2021 GRAT | trust | grat | CT | 2021-08-15 | trust_type=grat; revocable=False; situs=CT; annuity_term_years=2; annuity_end=2027-12-31; remainder_to=Olivo 2014 Dynasty Trust |
| D.8 | Theresa Olivo Family Trust | trust | bypass | CT | 2008-09-15 | trust_type=bypass; revocable=False; situs=CT; gst_exempt=False; funded=Theresa's estate, 2008; shares=equal thirds |
| D.9 | Giulia Olivo 2503(c) Trust | trust | 2503c | CT | 2014-10-15 | trust_type=2503c; revocable=False; situs=CT; vesting_age=21 |
| D.10 | AOF Investments LLC | llc |  | CT | 2016-11-15 |  |
| D.11 | AO Descendants Trust 2018 | trust | irrevocable | DE | 2018-12-15 | trust_type=irrevocable; revocable=False; situs=DE; gst_exempt=True |
| D.12 | Cipolla Olivo Holdings LLC | llc |  | FL | 2017-01-15 |  |
| D.13 | Cipolla 2020 Family Trust | trust | irrevocable | FL | 2020-02-15 | trust_type=irrevocable; revocable=False; situs=FL; gst_exempt=False |
| D.14 | MTO Capital LLC | llc |  | DE | 2019-03-15 |  |
| D.15 | Theo Olivo 2020 Trust | trust | irrevocable | CA | 2020-04-15 | trust_type=irrevocable; revocable=False; situs=CA; gst_exempt=False |
| D.16 | Olivo Family Foundation | foundation | private_foundation | CT | 2014-05-15 | charity_type=private_foundation; ein_status=501(c)(3); min_distribution_pct=5 |
| D.17 | Olivo Giving Fund | foundation | daf | Harbor Charitable Trust (sponsor) | 2016-06-15 | charity_type=daf; sponsor=Harbor Charitable Trust |
| D.18 | Olivo 2016 CRUT | trust | crut | CT | 2016-07-15 | trust_type=crut; revocable=False; situs=CT; unitrust_rate_pct=5; remainder_to=Olivo Family Foundation |
| D.19 | Olivo 2012 ILIT | trust | ilit | CT | 2012-08-15 | trust_type=ilit; revocable=False; situs=CT; crummey_notices=True; policy=$25M survivorship UL, Lincoln Benefit Life |
| D.20 | Round Hill Road LLC | llc | real estate | CT | 2005-09-15 |  |
| D.21 | Port Royal Residence LLC | llc | real estate | FL | 2019-10-15 |  |
| D.22 | Stag's Hollow Vineyard LLC | llc | operating | CA | 2017-11-15 |  |
| D.23 | Olivo Commercial RE LLC | llc | real estate | CT | 2008-12-15 |  |
| D.24 | Ajax Peak LLC | llc | real estate | CO | 2020-01-15 |  |
| D.25 | Brickell Bay 41 LLC | llc | real estate | FL | 2017-02-15 |  |
| D.26 | Olivo Art Holdings LLC | llc | collectibles | DE | 2018-03-15 |  |

| Step | Entity | Purpose | Control | Notes |
|---|---|---|---|---|
| D.1.n | Olivo Family Office LLC | Family office: manager of Olivo Holdings, employs 6 staff; also the GP's parent. | Managing member: Anthony Olivo |  |
| D.2.n | OH GP LLC | General partner of Olivo Holdings LP. | Sole member: Olivo Family Office LLC |  |
| D.3.n | Olivo Holdings LP | The main family investment partnership: managed liquid portfolio, private funds, direct deals, the vineyard and the art company. | GP: OH GP LLC; investment committee of Dominic, Anthony, Francesca, Michael | Valuation policy: funds at latest reported NAV, directs at last round, real assets at appraisal (Stag's Hollow 2025, art 2026). |
| D.4.n | Dominic J. Olivo Revocable Trust | Dominic's personal holdings; pour-over on death. | Trustee: Dominic; successor: Anthony |  |
| D.5.n | Olivo 2014 Dynasty Trust | Funded with Northline Foods stock at the sale; for descendants of Dominic and Theresa. GST-exempt. | Trustee: Wilmington Fiduciary Co.; distribution committee chaired by Anthony | Concentrated Northline position; a diversification plan runs through Olivo Holdings. |
| D.6.n | Marguerite Olivo 2019 SLAT | For Marguerite and Giulia; holds the Naples residence and a managed portfolio. | Trustee: Coastal Bank Trust Department |  |
| D.7.n | DJO 2021 GRAT | Two-year rolling GRAT on Northline stock; remainder to the Dynasty Trust. | Trustee: Anthony Olivo | Annuity payments to Dominic through 2027. |
| D.8.n | Theresa Olivo Family Trust | Established at Theresa's death for the three children equally; holds the commercial real estate. | Trustee: Coastal Bank Trust Department |  |
| D.9.n | Giulia Olivo 2503(c) Trust | Annual-exclusion gifts for Giulia; vests at 21. | Trustee: Marguerite Olivo |  |
| D.10.n | AOF Investments LLC | Anthony and Elena's family investment LLC. | Managers: Anthony and Elena |  |
| D.11.n | AO Descendants Trust 2018 | Anthony's trust for Sofia and Luca. | Trustee: Wilmington Fiduciary Co. |  |
| D.12.n | Cipolla Olivo Holdings LLC | Francesca and Mateo's family LLC: Miami real estate, art, LP units. | Managers: Francesca and Mateo |  |
| D.13.n | Cipolla 2020 Family Trust | For Isabel, Nico and Gabriel. | Trustee: Mateo Cipolla |  |
| D.14.n | MTO Capital LLC | Michael's personal investment company: public tech, venture fund interest, angel positions, digital assets, the Aspen house. | Manager: Michael Olivo | Michael's divorce settlement (2021) carved out the pre-2016 assets; everything here is post-separation. |
| D.15.n | Theo Olivo 2020 Trust | Michael's trust for Theo. | Trustee: Anthony Olivo |  |
| D.16.n | Olivo Family Foundation | Funded at the sale; grants about $2.3M a year to food security and culinary education. | Board: Dominic (chair), Anthony, Francesca, Michael, Marguerite | 5% minimum distribution; excess business holdings rule limits Northline stock. |
| D.17.n | Olivo Giving Fund | Family DAF; advisers are the four adult Olivos. | Sponsor: Harbor Charitable Trust |  |
| D.18.n | Olivo 2016 CRUT | 5% unitrust to Dominic for life; remainder to the foundation. | Trustee: Coastal Bank Trust Department |  |
| D.19.n | Olivo 2012 ILIT | Owns a $25M survivorship policy on Dominic and Marguerite. | Trustee: Anthony Olivo | Crummey notices annually to the three adult children. |
| D.20.n | Round Hill Road LLC | The Greenwich residence (9 acres). | Managed by the family office |  |
| D.21.n | Port Royal Residence LLC | The Naples winter residence. | Managed by the family office |  |
| D.22.n | Stag's Hollow Vineyard LLC | 62-acre Napa vineyard and winery; ~5,000 cases a year; 14 employees. | Manager: a hired GM; Francesca chairs | Operating business; K-1s to members. |
| D.23.n | Olivo Commercial RE LLC | Three industrial properties (Bridgeport, Edison, Allentown) leased to Northline Foods on a 15-year NNN lease. | Managed by the family office | Lease expires 2029 — renewal risk. |
| D.24.n | Ajax Peak LLC | Michael's Aspen house. | Manager: Michael |  |
| D.25.n | Brickell Bay 41 LLC | Francesca's Miami condo and the gallery space on NE 41st Street. | Managers: Francesca and Mateo |  |
| D.26.n | Olivo Art Holdings LLC | 41 postwar and contemporary works; loaned to museums; insured through AXA Art. | Manager: Francesca Olivo-Cipolla | Art-secured credit line at a private bank. |

## E · Relationships — family and roles

Add each as a relationship of the type shown, from the first record to the second, with the note. Then open three records and check the reverse reads correctly (e.g. on the Dynasty Trust: "Trustee: Wilmington Fiduciary Co.").

| Step | From | Relationship | To | Note |
|---|---|---|---|---|
| E.1 | Dominic J. Olivo | spouse | Marguerite Meloni Olivo | married 2011; prenuptial agreement |
| E.2 | Anthony D. Olivo | spouse | Dr. Elena Olivo |  |
| E.3 | Francesca Olivo-Cipolla | spouse | Mateo Cipolla |  |
| E.4 | Dominic J. Olivo | former_spouse | Theresa Zucchini Olivo | Theresa died 2008 |
| E.5 | Michael T. Olivo | former_spouse | Claire Porcari | divorced 2021; shared custody of Theo |
| E.6 | Dominic J. Olivo | parent_of | Anthony D. Olivo |  |
| E.7 | Dominic J. Olivo | parent_of | Francesca Olivo-Cipolla |  |
| E.8 | Dominic J. Olivo | parent_of | Michael T. Olivo |  |
| E.9 | Dominic J. Olivo | parent_of | Giulia Olivo |  |
| E.10 | Theresa Zucchini Olivo | parent_of | Anthony D. Olivo |  |
| E.11 | Theresa Zucchini Olivo | parent_of | Francesca Olivo-Cipolla |  |
| E.12 | Theresa Zucchini Olivo | parent_of | Michael T. Olivo |  |
| E.13 | Marguerite Meloni Olivo | parent_of | Giulia Olivo |  |
| E.14 | Anthony D. Olivo | parent_of | Sofia Olivo |  |
| E.15 | Anthony D. Olivo | parent_of | Luca Olivo |  |
| E.16 | Dr. Elena Olivo | parent_of | Sofia Olivo |  |
| E.17 | Dr. Elena Olivo | parent_of | Luca Olivo |  |
| E.18 | Francesca Olivo-Cipolla | parent_of | Isabel Cipolla |  |
| E.19 | Francesca Olivo-Cipolla | parent_of | Nico Cipolla |  |
| E.20 | Francesca Olivo-Cipolla | parent_of | Gabriel Cipolla |  |
| E.21 | Mateo Cipolla | parent_of | Isabel Cipolla |  |
| E.22 | Mateo Cipolla | parent_of | Nico Cipolla |  |
| E.23 | Mateo Cipolla | parent_of | Gabriel Cipolla |  |
| E.24 | Michael T. Olivo | parent_of | Theo Olivo |  |
| E.25 | Claire Porcari | parent_of | Theo Olivo |  |
| E.26 | Nico Cipolla | sibling | Gabriel Cipolla | twins |
| E.27 | Anthony D. Olivo | managing_member | Olivo Family Office LLC |  |
| E.28 | Olivo Family Office LLC | sole_member | OH GP LLC |  |
| E.29 | OH GP LLC | general_partner | Olivo Holdings LP |  |
| E.30 | Dominic J. Olivo | investment_committee | Olivo Holdings LP |  |
| E.31 | Anthony D. Olivo | investment_committee | Olivo Holdings LP |  |
| E.32 | Francesca Olivo-Cipolla | investment_committee | Olivo Holdings LP |  |
| E.33 | Michael T. Olivo | investment_committee | Olivo Holdings LP |  |
| E.34 | Dominic J. Olivo | grantor | Dominic J. Olivo Revocable Trust |  |
| E.35 | Dominic J. Olivo | trustee | Dominic J. Olivo Revocable Trust |  |
| E.36 | Anthony D. Olivo | successor_trustee | Dominic J. Olivo Revocable Trust |  |
| E.37 | Dominic J. Olivo | grantor | Olivo 2014 Dynasty Trust |  |
| E.38 | Wilmington Fiduciary Co. | trustee | Olivo 2014 Dynasty Trust |  |
| E.39 | Anthony D. Olivo | distribution_committee | Olivo 2014 Dynasty Trust |  |
| E.40 | Dominic J. Olivo | grantor | Marguerite Olivo 2019 SLAT |  |
| E.41 | Coastal Bank, N.A. — Trust Department | trustee | Marguerite Olivo 2019 SLAT |  |
| E.42 | Dominic J. Olivo | grantor | DJO 2021 GRAT |  |
| E.43 | Anthony D. Olivo | trustee | DJO 2021 GRAT |  |
| E.44 | Olivo 2014 Dynasty Trust | remainder_beneficiary | DJO 2021 GRAT |  |
| E.45 | Theresa Zucchini Olivo | grantor | Theresa Olivo Family Trust |  |
| E.46 | Coastal Bank, N.A. — Trust Department | trustee | Theresa Olivo Family Trust |  |
| E.47 | Dominic J. Olivo | grantor | Giulia Olivo 2503(c) Trust |  |
| E.48 | Marguerite Meloni Olivo | grantor | Giulia Olivo 2503(c) Trust |  |
| E.49 | Marguerite Meloni Olivo | trustee | Giulia Olivo 2503(c) Trust |  |
| E.50 | Anthony D. Olivo | manager | AOF Investments LLC |  |
| E.51 | Dr. Elena Olivo | manager | AOF Investments LLC |  |
| E.52 | Anthony D. Olivo | grantor | AO Descendants Trust 2018 |  |
| E.53 | Dr. Elena Olivo | grantor | AO Descendants Trust 2018 |  |
| E.54 | Wilmington Fiduciary Co. | trustee | AO Descendants Trust 2018 |  |
| E.55 | Francesca Olivo-Cipolla | manager | Cipolla Olivo Holdings LLC |  |
| E.56 | Mateo Cipolla | manager | Cipolla Olivo Holdings LLC |  |
| E.57 | Francesca Olivo-Cipolla | grantor | Cipolla 2020 Family Trust |  |
| E.58 | Mateo Cipolla | grantor | Cipolla 2020 Family Trust |  |
| E.59 | Mateo Cipolla | trustee | Cipolla 2020 Family Trust |  |
| E.60 | Michael T. Olivo | manager | MTO Capital LLC |  |
| E.61 | Michael T. Olivo | grantor | Theo Olivo 2020 Trust |  |
| E.62 | Anthony D. Olivo | trustee | Theo Olivo 2020 Trust |  |
| E.63 | Dominic J. Olivo | board_chair | Olivo Family Foundation |  |
| E.64 | Anthony D. Olivo | board_member | Olivo Family Foundation |  |
| E.65 | Francesca Olivo-Cipolla | board_member | Olivo Family Foundation |  |
| E.66 | Michael T. Olivo | board_member | Olivo Family Foundation |  |
| E.67 | Marguerite Meloni Olivo | board_member | Olivo Family Foundation |  |
| E.68 | Harbor Charitable Trust | sponsor | Olivo Giving Fund |  |
| E.69 | Dominic J. Olivo | donor_advisor | Olivo Giving Fund |  |
| E.70 | Anthony D. Olivo | donor_advisor | Olivo Giving Fund |  |
| E.71 | Francesca Olivo-Cipolla | donor_advisor | Olivo Giving Fund |  |
| E.72 | Michael T. Olivo | donor_advisor | Olivo Giving Fund |  |
| E.73 | Dominic J. Olivo | grantor | Olivo 2016 CRUT |  |
| E.74 | Coastal Bank, N.A. — Trust Department | trustee | Olivo 2016 CRUT |  |
| E.75 | Olivo Family Foundation | remainder_beneficiary | Olivo 2016 CRUT |  |
| E.76 | Dominic J. Olivo | grantor | Olivo 2012 ILIT |  |
| E.77 | Marguerite Meloni Olivo | grantor | Olivo 2012 ILIT |  |
| E.78 | Anthony D. Olivo | trustee | Olivo 2012 ILIT |  |
| E.79 | Olivo Family Office LLC | manager | Round Hill Road LLC |  |
| E.80 | Olivo Family Office LLC | manager | Port Royal Residence LLC |  |
| E.81 | Francesca Olivo-Cipolla | board_chair | Stag's Hollow Vineyard LLC |  |
| E.82 | Olivo Family Office LLC | manager | Olivo Commercial RE LLC |  |
| E.83 | Michael T. Olivo | manager | Ajax Peak LLC |  |
| E.84 | Francesca Olivo-Cipolla | manager | Brickell Bay 41 LLC |  |
| E.85 | Mateo Cipolla | manager | Brickell Bay 41 LLC |  |
| E.86 | Francesca Olivo-Cipolla | manager | Olivo Art Holdings LLC |  |
| E.87 | Michael T. Olivo | general_partner | Caldera Ventures |  |
| E.88 | Mateo Cipolla | owner | Cipolla Studio |  |
| E.89 | Anthony D. Olivo | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.90 | Francesca Olivo-Cipolla | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.91 | Michael T. Olivo | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.92 | Sofia Olivo | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.93 | Luca Olivo | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.94 | Isabel Cipolla | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.95 | Nico Cipolla | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.96 | Gabriel Cipolla | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.97 | Theo Olivo | beneficiary | Olivo 2014 Dynasty Trust | Descendants of Dominic & Theresa |
| E.98 | Marguerite Meloni Olivo | beneficiary | Marguerite Olivo 2019 SLAT | Marguerite; Giulia |
| E.99 | Giulia Olivo | beneficiary | Marguerite Olivo 2019 SLAT | Marguerite; Giulia |
| E.100 | Dominic J. Olivo | beneficiary | DJO 2021 GRAT | Annuity to Dominic; remainder to Dynasty Trust |
| E.101 | Anthony D. Olivo | beneficiary | Theresa Olivo Family Trust | Equal shares |
| E.102 | Francesca Olivo-Cipolla | beneficiary | Theresa Olivo Family Trust | Equal shares |
| E.103 | Michael T. Olivo | beneficiary | Theresa Olivo Family Trust | Equal shares |
| E.104 | Giulia Olivo | beneficiary | Giulia Olivo 2503(c) Trust | Giulia |
| E.105 | Sofia Olivo | beneficiary | AO Descendants Trust 2018 | Sofia & Luca |
| E.106 | Luca Olivo | beneficiary | AO Descendants Trust 2018 | Sofia & Luca |
| E.107 | Isabel Cipolla | beneficiary | Cipolla 2020 Family Trust | Isabel, Nico & Gabriel |
| E.108 | Nico Cipolla | beneficiary | Cipolla 2020 Family Trust | Isabel, Nico & Gabriel |
| E.109 | Gabriel Cipolla | beneficiary | Cipolla 2020 Family Trust | Isabel, Nico & Gabriel |
| E.110 | Theo Olivo | beneficiary | Theo Olivo 2020 Trust | Theo |
| E.111 | Dominic J. Olivo | beneficiary | Olivo 2016 CRUT | 5% unitrust to Dominic; remainder to the foundation |
| E.112 | Anthony D. Olivo | beneficiary | Olivo 2012 ILIT | Children |
| E.113 | Francesca Olivo-Cipolla | beneficiary | Olivo 2012 ILIT | Children |
| E.114 | Michael T. Olivo | beneficiary | Olivo 2012 ILIT | Children |
| E.115 | Giulia Olivo | beneficiary | Olivo 2012 ILIT | Children |
| E.116 | Dominic J. Olivo | annuitant | DJO 2021 GRAT | annuity payments through 2027 |
| E.117 | Dominic J. Olivo | income_beneficiary | Olivo 2016 CRUT | 5% unitrust for life |

## F · Ownership (percentages)

Enter each as ownership with the percentage and the interest type in the note. The percentages of each owned entity add to 100.

| Step | Owner | Owns | % | Interest |
|---|---|---|---|---|
| F.1 | Dominic J. Olivo | Olivo Family Office LLC | 40 | Membership interest |
| F.2 | Anthony D. Olivo | Olivo Family Office LLC | 20 | Membership interest |
| F.3 | Francesca Olivo-Cipolla | Olivo Family Office LLC | 20 | Membership interest |
| F.4 | Michael T. Olivo | Olivo Family Office LLC | 20 | Membership interest |
| F.5 | Olivo Family Office LLC | OH GP LLC | 100 | Sole member |
| F.6 | OH GP LLC | Olivo Holdings LP | 0.5 | GP interest |
| F.7 | Dominic J. Olivo Revocable Trust | Olivo Holdings LP | 34.5 | LP units |
| F.8 | Olivo 2014 Dynasty Trust | Olivo Holdings LP | 30 | LP units |
| F.9 | AOF Investments LLC | Olivo Holdings LP | 10 | LP units |
| F.10 | Cipolla Olivo Holdings LLC | Olivo Holdings LP | 10 | LP units |
| F.11 | MTO Capital LLC | Olivo Holdings LP | 10 | LP units |
| F.12 | Marguerite Olivo 2019 SLAT | Olivo Holdings LP | 5 | LP units |
| F.13 | Dominic J. Olivo | Dominic J. Olivo Revocable Trust | 100 | Grantor / trustee (revocable) |
| F.14 | Dominic J. Olivo Revocable Trust | Round Hill Road LLC | 100 | Sole member |
| F.15 | Marguerite Olivo 2019 SLAT | Port Royal Residence LLC | 100 | Sole member |
| F.16 | Olivo Holdings LP | Stag's Hollow Vineyard LLC | 80 | Membership interest |
| F.17 | AOF Investments LLC | Stag's Hollow Vineyard LLC | 10 | Membership interest |
| F.18 | Cipolla Olivo Holdings LLC | Stag's Hollow Vineyard LLC | 10 | Membership interest |
| F.19 | Theresa Olivo Family Trust | Olivo Commercial RE LLC | 100 | Sole member |
| F.20 | MTO Capital LLC | Ajax Peak LLC | 100 | Sole member |
| F.21 | Cipolla Olivo Holdings LLC | Brickell Bay 41 LLC | 100 | Sole member |
| F.22 | Olivo Holdings LP | Olivo Art Holdings LLC | 60 | Membership interest |
| F.23 | Cipolla Olivo Holdings LLC | Olivo Art Holdings LLC | 25 | Membership interest |
| F.24 | Dominic J. Olivo Revocable Trust | Olivo Art Holdings LLC | 15 | Membership interest |
| F.25 | Anthony D. Olivo | AOF Investments LLC | 50 | Membership interest |
| F.26 | Dr. Elena Olivo | AOF Investments LLC | 50 | Membership interest |
| F.27 | Francesca Olivo-Cipolla | Cipolla Olivo Holdings LLC | 60 | Membership interest |
| F.28 | Mateo Cipolla | Cipolla Olivo Holdings LLC | 40 | Membership interest |
| F.29 | Michael T. Olivo | MTO Capital LLC | 100 | Sole member |

**F.check** Open the hierarchy / ownership graph for **Olivo Holdings LP**: owners above it (OH GP LLC 0.5%, the Revocable Trust 34.5%,
the Dynasty Trust 30%, AOF / Cipolla Olivo / MTO 10% each, the SLAT 5%) and its holdings below (Stag's Hollow 80%, Art Holdings 60%).
Switch look-through on and off. Record anything missing or wrong.

## G · Household

**G.1** Portfolio → Households → **New household** `Olivo Family`. Members: all 14 family members (B.1.1–B.1.14; not Theresa, not Claire). **G.2** Add the 26 legal entities to the household if the screen allows entities as members; record what it allows.

## H · Accounts

Household → **Add account** for each. Type the full number once; afterwards check only the mask shows. Owner = the CRM record. Authority and custody as given — a non-default authority opens a maker-checker draft: the **super admin** approves it. Not billable.

| Step | Acct | Owner | Custodian | Type | Full number (type once) | Mask | Custody | Authority |
|---|---|---|---|---|---|---|---|---|
| H.1 | A1 | Dominic J. Olivo Revocable Trust | Goldman Sachs PWM | Taxable brokerage | `1027664471` | ****4471 | other_custodian | advise |
| H.2 | A2 | Dominic J. Olivo Revocable Trust | Schwab | Municipal bond ladder | `1106858830` | ****8830 | other_custodian | advise |
| H.3 | A3 | Dominic J. Olivo Revocable Trust | Coastal Bank | Checking + money market | `1186041102` | ****1102 | outside | read_only |
| H.4 | A4 | Dominic J. Olivo Revocable Trust | Goldman Sachs PWM | Structured notes | `1265234472` | ****4472 | other_custodian | advise |
| H.5 | A5 | Dominic J. Olivo | Fidelity | Rollover IRA | `1344426619` | ****6619 | outside | read_only |
| H.6 | A6 | Olivo 2014 Dynasty Trust | Goldman Sachs PWM | Taxable brokerage (Northline) | `1423615120` | ****5120 | other_custodian | advise |
| H.7 | A7 | Olivo 2014 Dynasty Trust | Altruist | Managed account (2nd Act) | `1502802201` | ****2201 | integrated | managed |
| H.8 | A8 | DJO 2021 GRAT | Goldman Sachs PWM | GRAT brokerage | `1581995121` | ****5121 | other_custodian | read_only |
| H.9 | A9 | Olivo Holdings LP | Altruist | Managed account (2nd Act) | `1661182202` | ****2202 | integrated | managed |
| H.10 | A10 | Olivo Holdings LP | Olivo Holdings (books) | Private investments ledger | `LEDGER-A10-0001` | — | outside | read_only |
| H.11 | A11 | Olivo Holdings LP | Coastal Bank | Operating cash | `4907971140` | ****1140 | outside | read_only |
| H.12 | A12 | Marguerite Olivo 2019 SLAT | Altruist | Managed account (2nd Act) | `4987162203` | ****2203 | integrated | managed |
| H.13 | A13 | Theresa Olivo Family Trust | Schwab | Taxable brokerage | `5066358831` | ****8831 | other_custodian | advise |
| H.14 | A14 | Giulia Olivo 2503(c) Trust | Altruist | Managed account (2nd Act) | `5145542204` | ****2204 | integrated | managed |
| H.15 | A15 | AOF Investments LLC | Altruist | Managed account (2nd Act) | `5224732205` | ****2205 | integrated | managed |
| H.16 | A16 | Anthony D. Olivo | Fidelity | 401(k) rollover IRA | `5303926620` | ****6620 | outside | read_only |
| H.17 | A17 | Dr. Elena Olivo | Fidelity | IRA | `5383116621` | ****6621 | outside | read_only |
| H.18 | A18 | Anthony D. Olivo | CHET (Fidelity) | 529 — Sofia | `5462307701` | ****7701 | outside | read_only |
| H.19 | A19 | Anthony D. Olivo | CHET (Fidelity) | 529 — Luca | `5541497702` | ****7702 | outside | read_only |
| H.20 | A20 | AO Descendants Trust 2018 | Altruist | Managed account (2nd Act) | `4907972206` | ****2206 | integrated | managed |
| H.21 | A21 | Cipolla Olivo Holdings LLC | Schwab | Taxable brokerage | `4987168832` | ****8832 | other_custodian | advise |
| H.22 | A22 | Francesca Olivo-Cipolla | Schwab | Roth IRA | `5066358833` | ****8833 | outside | read_only |
| H.23 | A23 | Francesca Olivo-Cipolla | Florida Prepaid / Vanguard 529 | 529 — Isabel, Nico, Gabriel (3 plans) | `5145547710` | ****7710 | outside | read_only |
| H.24 | A24 | Cipolla 2020 Family Trust | Altruist | Managed account (2nd Act) | `5224732207` | ****2207 | integrated | managed |
| H.25 | A25 | MTO Capital LLC | Interactive Brokers | Margin account (options overlay) | `5303923390` | ****3390 | other_custodian | advise |
| H.26 | A26 | MTO Capital LLC | Coinbase Prime | Digital assets | `5383110042` | ****0042 | outside | read_only |
| H.27 | A27 | MTO Capital LLC | MTO Capital (books) | Venture & angel ledger | `LEDGER-A27-0001` | — | outside | read_only |
| H.28 | A28 | Michael T. Olivo | Schwab | Roth IRA | `5541498834` | ****8834 | outside | read_only |
| H.29 | A29 | Theo Olivo 2020 Trust | Altruist | Managed account (2nd Act) | `5620682208` | ****2208 | integrated | managed |
| H.30 | A30 | Olivo Family Foundation | Goldman Sachs PWM | Foundation endowment | `4987165130` | ****5130 | other_custodian | advise |
| H.31 | A31 | Olivo Giving Fund | Harbor Charitable Trust | DAF | `5066359001` | ****9001 | outside | read_only |
| H.32 | A32 | Olivo 2016 CRUT | Schwab | CRUT brokerage | `5145548835` | ****8835 | other_custodian | advise |
| H.33 | A33 | Olivo 2012 ILIT | Lincoln Benefit Life | Survivorship UL policy | `LBL-SUL-0000077` | POL-****77 | outside | read_only |
| H.34 | A34 | Marguerite Meloni Olivo | Schwab | Taxable brokerage | `5303928836` | ****8836 | other_custodian | advise |

**H.check** The account list shows 34 accounts, masks only. Any full number visible = High.

## I · Holdings (as of 2026-11-04)

For each account, enter its positions. Route:
- **other_custodian / outside** accounts: the held-away **manual entry** (positions with quantity, price or value, cost basis, as-of 2026-11-04).
  Held-away entries may need the super admin's approval — approve as checker.
- **integrated (Altruist)** accounts: positions normally arrive from the custodian feed. Record what the screen offers; if it offers no
  manual path, mark `BLOCKED — integrated account, no feed` and continue (open decision for the developer).
- The **Security master** column says how the line maps. "listed" lines must resolve to an existing security by ticker — never create a
  new security for them; a listed ticker that does not resolve is a finding. "mutual fund" lines are expected NOT to resolve yet (known gap)
  — enter them as unmapped/manual if the screen allows, and list them in the report.


**I.A1** — Dominic J. Olivo Revocable Trust · Goldman Sachs PWM · Taxable brokerage (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SPY | SPDR S&P 500 ETF | 38,000 | 642.1 | $24,399,800 | $14,800,000 | listed — expected in the security master after secmaster1's load |  |
| VXUS | Vanguard Total International Stock ETF | 160,000 | 71.4 | $11,424,000 | $9,100,000 | listed — expected in the security master after secmaster1's load |  |
| IWM | iShares Russell 2000 ETF | 22,000 | 241.3 | $5,308,600 | $4,600,000 | listed — expected in the security master after secmaster1's load |  |
| NLF | Northline Foods Inc. | 120,000 | 61.05 | $7,326,000 | $1,440,000 | FICTIONAL company — decide: replace with a real ticker, or a private-asset record | Residual from the 2014 sale; basis $12/sh |
| AAPL | Apple Inc. | 18,000 | 262.4 | $4,723,200 | $1,900,000 | listed — expected in the security master after secmaster1's load |  |
| BRK.B | Berkshire Hathaway Class B | 9,000 | 498.2 | $4,483,800 | $2,100,000 | listed — expected in the security master after secmaster1's load |  |
| BND | Vanguard Total Bond Market ETF | 95,000 | 74.9 | $7,115,500 | $7,400,000 | listed — expected in the security master after secmaster1's load |  |
| FGTXX | Goldman Sachs Financial Square Govt MMF | 3,250,000 | 1.0 | $3,250,000 | $3,250,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A2** — Dominic J. Olivo Revocable Trust · Schwab · Municipal bond ladder (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| MUNI-2027 | Connecticut / NY GO & essential-service munis 4.00% due 2027 | 2,000,000 | 0.985 | $1,970,000 | $2,000,000 | municipal bond — no free CUSIP source; enter as a manual fixed-income asset |  |
| MUNI-2028 | Connecticut / NY GO & essential-service munis 5.00% due 2028 | 2,500,000 | 0.985 | $2,462,500 | $2,500,000 | municipal bond — no free CUSIP source; enter as a manual fixed-income asset |  |
| MUNI-2029 | Connecticut / NY GO & essential-service munis 4.00% due 2029 | 2,500,000 | 0.985 | $2,462,500 | $2,500,000 | municipal bond — no free CUSIP source; enter as a manual fixed-income asset |  |
| MUNI-2030 | Connecticut / NY GO & essential-service munis 5.00% due 2030 | 3,000,000 | 1.01 | $3,030,000 | $3,000,000 | municipal bond — no free CUSIP source; enter as a manual fixed-income asset |  |
| MUNI-2031 | Connecticut / NY GO & essential-service munis 4.00% due 2031 | 3,000,000 | 1.01 | $3,030,000 | $3,000,000 | municipal bond — no free CUSIP source; enter as a manual fixed-income asset |  |
| MUNI-2033 | Connecticut / NY GO & essential-service munis 5.00% due 2033 | 2,500,000 | 1.01 | $2,525,000 | $2,500,000 | municipal bond — no free CUSIP source; enter as a manual fixed-income asset |  |
| MUNI-2036 | Connecticut / NY GO & essential-service munis 4.00% due 2036 | 2,500,000 | 1.01 | $2,525,000 | $2,500,000 | municipal bond — no free CUSIP source; enter as a manual fixed-income asset |  |

**I.A3** — Dominic J. Olivo Revocable Trust · Coastal Bank · Checking + money market (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| CASH | Coastal Bank checking |  |  | $1,150,000 | $1,150,000 | cash — cash position |  |
| CASH-MM | Coastal Bank money market |  |  | $3,050,000 | $3,050,000 | cash — cash position |  |

**I.A4** — Dominic J. Olivo Revocable Trust · Goldman Sachs PWM · Structured notes (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| NOTE-JPM-SPX | JPM autocallable contingent-coupon note on S&P 500 (CUSIP 48133X***) due 2028 |  |  | $5,000,000 | $5,000,000 | structured note — map to one of the 54 real notes already in the master | Issued Mar 2026; worst-of? no — single index |
| NOTE-GS-NDX | Goldman buffered note on Nasdaq-100 (15% buffer, 1.3x to 24% cap) due 2027 |  |  | $3,120,000 | $3,000,000 | structured note — map to one of the 54 real notes already in the master |  |

**I.A5** — Dominic J. Olivo · Fidelity · Rollover IRA (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| FXAIX | Fidelity 500 Index Fund | 28,000 | 228.1 | $6,386,800 | $3,100,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |
| FXNAX | Fidelity U.S. Bond Index Fund | 250,000 | 10.52 | $2,630,000 | $2,700,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |
| FDRXX | Fidelity Government Cash Reserves | 450,000 | 1.0 | $450,000 | $450,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A6** — Olivo 2014 Dynasty Trust · Goldman Sachs PWM · Taxable brokerage (Northline) (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| NLF | Northline Foods Inc. | 1,800,000 | 61.05 | $109,890,000 | $21,600,000 | FICTIONAL company — decide: replace with a real ticker, or a private-asset record | Received at the 2014 sale; basis $12/sh. Collateral for the $20M SBL. |
| FGTXX | Goldman Sachs Financial Square Govt MMF | 1,800,000 | 1.0 | $1,800,000 | $1,800,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A7** — Olivo 2014 Dynasty Trust · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VOO | Vanguard S&P 500 ETF | 24,000 | 590.2 | $14,164,800 | $12,400,000 | listed — expected in the security master after secmaster1's load |  |
| VEA | Vanguard FTSE Developed Markets ETF | 150,000 | 58.9 | $8,835,000 | $7,900,000 | listed — expected in the security master after secmaster1's load |  |
| VWO | Vanguard FTSE Emerging Markets ETF | 70,000 | 52.1 | $3,647,000 | $3,400,000 | listed — expected in the security master after secmaster1's load |  |
| AGG | iShares Core U.S. Aggregate Bond ETF | 95,000 | 101.3 | $9,623,500 | $9,700,000 | listed — expected in the security master after secmaster1's load |  |
| SGOV | iShares 0-3 Month Treasury Bond ETF | 48,000 | 100.6 | $4,828,800 | $4,800,000 | listed — expected in the security master after secmaster1's load |  |
| QQQ | Invesco QQQ Trust | 6,500 | 598.4 | $3,889,600 | $3,100,000 | listed — expected in the security master after secmaster1's load | Candidate for a Replication Studio direct-index replica |

**I.A8** — DJO 2021 GRAT · Goldman Sachs PWM · GRAT brokerage (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| NLF | Northline Foods Inc. | 400,000 | 61.05 | $24,420,000 | $4,800,000 | FICTIONAL company — decide: replace with a real ticker, or a private-asset record |  |

**I.A9** — Olivo Holdings LP · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SMA-CORE | Olivo Core Equity (direct-indexed S&P 500 replica) |  |  | $24,600,000 | $19,200,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | Replication Studio replica of SPY; tax-loss harvesting on |
| EFA | iShares MSCI EAFE ETF | 110,000 | 88.7 | $9,757,000 | $8,100,000 | listed — expected in the security master after secmaster1's load |  |
| AGG | iShares Core U.S. Aggregate Bond ETF | 70,000 | 101.3 | $7,091,000 | $7,000,000 | listed — expected in the security master after secmaster1's load |  |
| VTIP | Vanguard Short-Term Inflation-Protected Securities ETF | 80,000 | 50.1 | $4,008,000 | $3,900,000 | listed — expected in the security master after secmaster1's load |  |
| SGOV | iShares 0-3 Month Treasury Bond ETF | 60,000 | 100.6 | $6,036,000 | $6,000,000 | listed — expected in the security master after secmaster1's load |  |
| PJAN | Innovator U.S. Equity Power Buffer ETF (Jan) | 60,000 | 42.8 | $2,568,000 | $2,400,000 | listed — expected in the security master after secmaster1's load | Options-sleeve candidate (rep8) |

**I.A10** — Olivo Holdings LP · Olivo Holdings (books) · Private investments ledger (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| PE-HPP4 | Harbor Point Partners IV, L.P. |  |  | $22,300,000 | $18,000,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | $30M commitment, $8M unfunded; Q2 2026 NAV |
| PC-TCO2 | Thornfield Credit Opportunities II |  |  | $15,100,000 | $15,000,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | $15M commitment, fully drawn; 11.2% net IRR |
| HF-STILL | Stillwater Macro Fund Ltd. |  |  | $12,400,000 | $10,000,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) |  |
| PRIV-BWA | Brightwater Aquaculture, Inc. — Series C Preferred | 420,000 | 15.48 | $6,501,600 | $6,500,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | Last round Nov 2025; board observer seat (Anthony) |
| PRIV-NEHFC | New England Harbor FC — 2.5% LLC interest |  |  | $4,000,000 | $2,500,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | USL Championship club; capital call rights |
| SPV-MERIDIAN | 2nd Act Marketplace SPV: Meridian Logistics Class A |  |  | $3,050,000 | $3,000,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | Invested through the Hollisworks marketplace, 2026 |

**I.A11** — Olivo Holdings LP · Coastal Bank · Operating cash (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| CASH | Coastal Bank operating account |  |  | $7,000,000 | $7,000,000 | cash — cash position |  |

**I.A12** — Marguerite Olivo 2019 SLAT · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VTI | Vanguard Total Stock Market ETF | 21,000 | 318.5 | $6,688,500 | $5,400,000 | listed — expected in the security master after secmaster1's load |  |
| VXUS | Vanguard Total International Stock ETF | 45,000 | 71.4 | $3,213,000 | $2,900,000 | listed — expected in the security master after secmaster1's load |  |
| VTEB | Vanguard Tax-Exempt Bond ETF | 80,000 | 50.8 | $4,064,000 | $4,000,000 | listed — expected in the security master after secmaster1's load |  |

**I.A13** — Theresa Olivo Family Trust · Schwab · Taxable brokerage (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SCHD | Schwab U.S. Dividend Equity ETF | 110,000 | 29.4 | $3,234,000 | $2,600,000 | listed — expected in the security master after secmaster1's load |  |
| SCHP | Schwab U.S. TIPS ETF | 50,000 | 27.1 | $1,355,000 | $1,350,000 | listed — expected in the security master after secmaster1's load |  |
| SNOXX | Schwab Treasury Obligations MMF | 1,420,000 | 1.0 | $1,420,000 | $1,420,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A14** — Giulia Olivo 2503(c) Trust · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VTI | Vanguard Total Stock Market ETF | 5,000 | 318.5 | $1,592,500 | $1,100,000 | listed — expected in the security master after secmaster1's load |  |
| AGG | iShares Core U.S. Aggregate Bond ETF | 5,000 | 101.3 | $506,500 | $500,000 | listed — expected in the security master after secmaster1's load |  |

**I.A15** — AOF Investments LLC · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VOO | Vanguard S&P 500 ETF | 7,200 | 590.2 | $4,249,440 | $3,100,000 | listed — expected in the security master after secmaster1's load |  |
| VEU | Vanguard FTSE All-World ex-US ETF | 28,000 | 68.3 | $1,912,400 | $1,600,000 | listed — expected in the security master after secmaster1's load |  |
| VWIUX | Vanguard Intermediate-Term Tax-Exempt Fund | 160,000 | 13.9 | $2,224,000 | $2,200,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A16** — Anthony D. Olivo · Fidelity · 401(k) rollover IRA (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| FSKAX | Fidelity Total Market Index Fund | 19,000 | 166.2 | $3,157,800 | $1,900,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A17** — Dr. Elena Olivo · Fidelity · IRA (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| FFFGX | Fidelity Freedom 2045 Fund | 85,000 | 14.1 | $1,198,500 | $900,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A18** — Anthony D. Olivo · CHET (Fidelity) · 529 — Sofia (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| 529-SOFIA | CHET Age-Based Portfolio (college) |  |  | $310,000 | $200,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) |  |

**I.A19** — Anthony D. Olivo · CHET (Fidelity) · 529 — Luca (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| 529-LUCA | CHET Age-Based Portfolio (2025) |  |  | $290,000 | $200,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) |  |

**I.A20** — AO Descendants Trust 2018 · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VTI | Vanguard Total Stock Market ETF | 12,000 | 318.5 | $3,822,000 | $2,900,000 | listed — expected in the security master after secmaster1's load |  |
| VXUS | Vanguard Total International Stock ETF | 12,000 | 71.4 | $856,800 | $700,000 | listed — expected in the security master after secmaster1's load |  |
| AGG | iShares Core U.S. Aggregate Bond ETF | 7,000 | 101.3 | $709,100 | $700,000 | listed — expected in the security master after secmaster1's load |  |

**I.A21** — Cipolla Olivo Holdings LLC · Schwab · Taxable brokerage (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SCHB | Schwab U.S. Broad Market ETF | 90,000 | 24.1 | $2,169,000 | $1,500,000 | listed — expected in the security master after secmaster1's load |  |
| SCHF | Schwab International Equity ETF | 40,000 | 43.2 | $1,728,000 | $1,300,000 | listed — expected in the security master after secmaster1's load |  |
| SCMB | Schwab Municipal Bond ETF | 35,000 | 25.2 | $882,000 | $880,000 | listed — expected in the security master after secmaster1's load |  |

**I.A22** — Francesca Olivo-Cipolla · Schwab · Roth IRA (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SWPPX | Schwab S&P 500 Index Fund | 9,500 | 94.6 | $898,700 | $600,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A23** — Francesca Olivo-Cipolla · Florida Prepaid / Vanguard 529 · 529 — Isabel, Nico, Gabriel (3 plans) (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| 529-CIPOLLA | Florida Prepaid + Vanguard 529 (3 plans) |  |  | $540,000 | $400,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) |  |

**I.A24** — Cipolla 2020 Family Trust · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VTI | Vanguard Total Stock Market ETF | 8,000 | 318.5 | $2,548,000 | $1,900,000 | listed — expected in the security master after secmaster1's load |  |
| AGG | iShares Core U.S. Aggregate Bond ETF | 10,000 | 101.3 | $1,013,000 | $1,000,000 | listed — expected in the security master after secmaster1's load |  |

**I.A25** — MTO Capital LLC · Interactive Brokers · Margin account (options overlay) (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| NVDA | NVIDIA Corp. | 14,000 | 196.3 | $2,748,200 | $900,000 | listed — expected in the security master after secmaster1's load |  |
| MSFT | Microsoft Corp. | 5,500 | 512.4 | $2,818,200 | $1,400,000 | listed — expected in the security master after secmaster1's load |  |
| GOOGL | Alphabet Inc. Class A | 8 | 251.6 | $2,012,800 | $600,000 | listed — expected in the security master after secmaster1's load | qty 8,000 |
| QQQ | Invesco QQQ Trust | 5,000 | 598.4 | $2,992,000 | $2,100,000 | listed — expected in the security master after secmaster1's load |  |
| OPT-SPY-C660 | Written SPY calls (covered-call overlay), Dec 2026 660 | -50 |  | $-187,500 | $-240,000 | listed option — create the contract in the options sleeve (rep8) | 50 contracts; premium received $240k |
| CASH | Cash / sweep |  |  | $1,050,000 | $1,050,000 | cash — cash position |  |

**I.A26** — MTO Capital LLC · Coinbase Prime · Digital assets (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| BTC | Bitcoin | 18.5 | 97,400 | $1,801,900 | $620,000 | digital asset — manual asset |  |
| ETH | Ether | 120 | 3,280 | $393,600 | $210,000 | digital asset — manual asset |  |

**I.A27** — MTO Capital LLC · MTO Capital (books) · Venture & angel ledger (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VC-CALDERA2 | Caldera Ventures Fund II, L.P. — LP interest |  |  | $9,000,000 | $6,000,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | $10M commitment, $6M called; plus GP carry (unvalued) |
| ANGEL-LUMA | Luma Robotics — seed SAFE |  |  | $750,000 | $250,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | Marked at the Series A price |
| ANGEL-HALCYON | Halcyon Bio — seed preferred |  |  | $500,000 | $500,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) |  |
| ANGEL-PRESSBOX | Pressbox Media — convertible note |  |  | $0 | $150,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | Written off 2025 |

**I.A28** — Michael T. Olivo · Schwab · Roth IRA (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SWTSX | Schwab Total Stock Market Index Fund | 6,000 | 101.3 | $607,800 | $350,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A29** — Theo Olivo 2020 Trust · Altruist · Managed account (2nd Act) (integrated)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VTI | Vanguard Total Stock Market ETF | 4,200 | 318.5 | $1,337,700 | $1,000,000 | listed — expected in the security master after secmaster1's load |  |
| AGG | iShares Core U.S. Aggregate Bond ETF | 5,500 | 101.3 | $557,150 | $550,000 | listed — expected in the security master after secmaster1's load |  |

**I.A30** — Olivo Family Foundation · Goldman Sachs PWM · Foundation endowment (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| VT | Vanguard Total World Stock ETF | 220,000 | 136.2 | $29,964,000 | $22,000,000 | listed — expected in the security master after secmaster1's load |  |
| BND | Vanguard Total Bond Market ETF | 140,000 | 74.9 | $10,486,000 | $10,800,000 | listed — expected in the security master after secmaster1's load |  |
| PE-HPP4 | Harbor Point Partners IV, L.P. (foundation sleeve) |  |  | $3,200,000 | $2,500,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) |  |
| FGTXX | Goldman Sachs Financial Square Govt MMF | 1,300,000 | 1.0 | $1,300,000 | $1,300,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.A31** — Olivo Giving Fund · Harbor Charitable Trust · DAF (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| DAF-POOL | Harbor Charitable balanced pool (60/40) |  |  | $6,100,000 | $5,000,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) |  |

**I.A32** — Olivo 2016 CRUT · Schwab · CRUT brokerage (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SCHD | Schwab U.S. Dividend Equity ETF | 200,000 | 29.4 | $5,880,000 | $4,800,000 | listed — expected in the security master after secmaster1's load |  |
| SCHR | Schwab Intermediate-Term U.S. Treasury ETF | 200,000 | 26.1 | $5,220,000 | $5,200,000 | listed — expected in the security master after secmaster1's load |  |

**I.A33** — Olivo 2012 ILIT · Lincoln Benefit Life · Survivorship UL policy (outside)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| POL-SUL | Survivorship UL policy, $25,000,000 face (Dominic & Marguerite) |  |  | $4,100,000 | $3,600,000 | private / manual asset (fund interest, direct deal, 529, policy, SMA) | Premium $185k/yr via Crummey gifts |

**I.A34** — Marguerite Meloni Olivo · Schwab · Taxable brokerage (other_custodian)

| Ticker / id | Security | Qty | Price | Value | Cost basis | Security master | Note |
|---|---|---|---|---|---|---|---|
| SCHB | Schwab U.S. Broad Market ETF | 70,000 | 24.1 | $1,687,000 | $1,100,000 | listed — expected in the security master after secmaster1's load |  |
| SCMB | Schwab Municipal Bond ETF | 45,000 | 25.2 | $1,134,000 | $1,130,000 | listed — expected in the security master after secmaster1's load |  |
| SNOXX | Schwab Treasury Obligations MMF | 270,000 | 1.0 | $270,000 | $270,000 | mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers) |  |

**I.NLF** *Northline Foods (NLF)* is fictional. Do not map it to any real security. If the platform offers a "security not in the master"
path (a private / manual security), use it and note the flow; otherwise mark BLOCKED.

**I.NOTE** The two structured notes: search Replication → Notes for a JPMorgan autocallable on the S&P 500 and a Goldman Sachs buffered note on
the Nasdaq-100. If one exists, map the position to it and record which (its CUSIP); if not, enter as manual and report.

## J · Private investments and commitments

Lines in phase I marked *private / manual asset* that are funds or direct deals (Harbor Point IV, Thornfield II, Stillwater, Brightwater, New England Harbor FC, the 2nd Act Marketplace SPV, Caldera Fund II, Luma, Halcyon, Pressbox) are entered as **private investments** with their value as the latest valuation. Commitments: **Harbor Point Partners IV** — $8,000,000 unfunded (Olivo Holdings LP); **Caldera Ventures Fund II** — $4,000,000 unfunded (MTO Capital LLC). Enter those on the commitments screen if it exists.

**J.SPV** The SPV line names a 2nd Act Marketplace deal (Meridian Logistics). If the marketplace has no such deal, enter it as a private investment and say so — do not create a marketplace deal.

## K · Direct assets (real estate, collectibles, operating business, cash)

Enter each as an asset owned by the entity, with its value as a valuation (method: appraisal / estimate as the note says) and the cost basis.

| Step | Owner | Asset | Class | Sub-class | Value | Cost basis | Note |
|---|---|---|---|---|---|---|---|
| K.1 | Round Hill Road LLC | Round Hill Road, Greenwich — residence, 9 acres | Real estate | Primary residence | $14,500,000 | $6,200,000 | Appraised 2025 |
| K.2 | Port Royal Residence LLC | Port Royal, Naples FL — winter residence | Real estate | Secondary residence | $9,200,000 | $7,100,000 | Purchased 2019 |
| K.3 | Stag's Hollow Vineyard LLC | Stag's Hollow — 62 acres, Napa (vines, winery, tasting room) | Real estate | Agricultural / operating property | $28,000,000 | $19,500,000 | Appraised 2025 |
| K.4 | Stag's Hollow Vineyard LLC | Winery equipment and vehicles | Operating business | Plant & equipment | $2,500,000 | $3,100,000 | Net book |
| K.5 | Stag's Hollow Vineyard LLC | Wine library (back vintages, ~9,000 bottles) | Collectibles | Wine | $1,800,000 | $900,000 | Insured at $2.1M |
| K.6 | Olivo Commercial RE LLC | Bridgeport CT cold-storage facility | Real estate | Industrial (NNN to Northline) | $15,000,000 | $9,000,000 |  |
| K.7 | Olivo Commercial RE LLC | Edison NJ distribution center | Real estate | Industrial (NNN to Northline) | $14,000,000 | $8,500,000 |  |
| K.8 | Olivo Commercial RE LLC | Allentown PA plant | Real estate | Industrial (NNN to Northline) | $9,000,000 | $6,000,000 |  |
| K.9 | Ajax Peak LLC | Ajax Peak, Aspen — ski house | Real estate | Secondary residence | $7,800,000 | $6,900,000 | Purchased 2020 |
| K.10 | Brickell Bay 41 LLC | Brickell Bay 41 — condo | Real estate | Primary residence | $4,600,000 | $3,400,000 |  |
| K.11 | Brickell Bay 41 LLC | NE 41st Street — gallery space | Real estate | Commercial (gallery) | $3,200,000 | $2,700,000 | Leased to Francesca's gallery at market |
| K.12 | Olivo Art Holdings LLC | Postwar abstraction — 9 works (Mitchell, Frankenthaler, Twombly school) | Collectibles | Fine art | $12,800,000 | $5,100,000 | Three on museum loan |
| K.13 | Olivo Art Holdings LLC | Contemporary — 23 works (Ruscha, Kusama, Doig, Marshall, others) | Collectibles | Fine art | $15,400,000 | $9,800,000 |  |
| K.14 | Olivo Art Holdings LLC | Photography and works on paper — 9 works | Collectibles | Fine art | $3,200,000 | $2,200,000 |  |
| K.15 | Dominic J. Olivo Revocable Trust | 1963 Ferrari 250 GT Lusso | Collectibles | Classic car | $2,900,000 | $1,100,000 | Pebble Beach 2019 |
| K.16 | Dominic J. Olivo Revocable Trust | 1957 Mercedes-Benz 300SL Roadster | Collectibles | Classic car | $1,800,000 | $950,000 |  |
| K.17 | Dominic J. Olivo Revocable Trust | 1967 Shelby GT500, 1972 Lancia Stratos, 1989 Porsche 959 and five others | Collectibles | Classic cars (8) | $7,700,000 | $4,900,000 | Stored in Greenwich; Hagerty |
| K.18 | Marguerite Meloni Olivo | Jewelry (Cartier, Graff; the Meloni sapphire) | Collectibles | Jewelry | $2,300,000 | $1,900,000 | Scheduled on the homeowners policy |
| K.19 | Olivo Family Office LLC | Family office working capital | Cash | Operating cash | $1,400,000 | $1,400,000 |  |

## L · Liabilities (crmdetail1)

Enter each under the borrower entity, with the lender (create the lender as an organisation if it is not in phase A), the kind, balance and terms. Off-balance-sheet commitments are NOT liabilities here — they were entered in J.

| Step | Borrower | Liability | Kind | Balance | Terms | Note |
|---|---|---|---|---|---|---|
| L.1 | Olivo 2014 Dynasty Trust | Goldman Sachs — securities-backed line of credit | Secured credit line | $20,000,000 | SOFR + 1.10% | Collateral: Northline shares in A6; used for Holdings LP capital calls and the Naples purchase. |
| L.2 | Port Royal Residence LLC | Coastal Bank — mortgage (Naples) | Mortgage | $3,500,000 | 5.35% fixed, 2049 |  |
| L.3 | Stag's Hollow Vineyard LLC | Farm Credit West — agricultural term loan | Term loan | $6,000,000 | 6.10%, 2032 | Secured by the vineyard |
| L.4 | Olivo Commercial RE LLC | Coastal Bank — commercial mortgage (3 properties) | Mortgage | $14,000,000 | 4.80% fixed, 2033 | Cross-collateralised |
| L.5 | Ajax Peak LLC | First Western — mortgage (Aspen) | Mortgage | $2,400,000 | 6.25% fixed, 2050 |  |
| L.6 | Brickell Bay 41 LLC | Coastal Bank — mortgage (Miami condo) | Mortgage | $1,900,000 | 5.90% fixed, 2047 |  |
| L.7 | Olivo Art Holdings LLC | Private bank — art-secured credit line | Secured credit line | $5,000,000 | SOFR + 1.75% | Secured by 11 works; drawn for the 2025 Marshall acquisition |

## M · Security-master check

**M.1** Search the security master for every distinct *listed* ticker in phase I; record found / not found. **M.2** List every line you could not map, with its account and the reason the screen gave.

## N · Reconciliation — do the numbers add up?

Expected (bottom-up, from this file): family net worth **$601,788,090**; charitable entities **$62,150,000**. Record what the app shows for each entity below (net of liabilities, after look-through) and the difference. A difference is not automatically a bug — say which data you could not enter (BLOCKED steps) that explains it.

| Entity | Expected net value | App shows | Difference |
|---|---|---|---|
| Olivo Family Office LLC | $2,206,458 | | |
| OH GP LLC | $806,458 | | |
| Olivo Holdings LP | $161,291,600 | | |
| Dominic J. Olivo Revocable Trust | $184,861,502 | | |
| Olivo 2014 Dynasty Trust | $185,066,180 | | |
| Marguerite Olivo 2019 SLAT | $27,730,080 | | |
| DJO 2021 GRAT | $24,420,000 | | |
| Theresa Olivo Family Trust | $30,009,000 | | |
| Giulia Olivo 2503(c) Trust | $2,099,000 | | |
| AOF Investments LLC | $27,145,000 | | |
| AO Descendants Trust 2018 | $5,387,900 | | |
| Cipolla Olivo Holdings LLC | $36,038,160 | | |
| Cipolla 2020 Family Trust | $3,561,000 | | |
| MTO Capital LLC | $45,408,360 | | |
| Theo Olivo 2020 Trust | $1,894,850 | | |
| Olivo Family Foundation | $44,950,000 | | |
| Olivo Giving Fund | $6,100,000 | | |
| Olivo 2016 CRUT | $11,100,000 | | |
| Olivo 2012 ILIT | $4,100,000 | | |
| Round Hill Road LLC | $14,500,000 | | |
| Port Royal Residence LLC | $5,700,000 | | |
| Stag's Hollow Vineyard LLC | $26,300,000 | | |
| Olivo Commercial RE LLC | $24,000,000 | | |
| Ajax Peak LLC | $5,400,000 | | |
| Brickell Bay 41 LLC | $5,900,000 | | |
| Olivo Art Holdings LLC | $26,400,000 | | |

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
