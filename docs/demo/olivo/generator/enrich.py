"""Adds CRM detail to the Olivo family dataset: dates of birth, name parts,
addresses (with seasonal homes), family relationships, roles on each legal
entity, outside organisations, trust / LLC detail fields, notes, and the
security-master mapping for every holding. Writes data/olivo_crm.json + CSVs."""
import csv, json, os

OUT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(f"{OUT}/data/olivo_family.json"))

# ── People: name parts, DOB, contact, status ─────────────────────────────────
P = {
 "P1": dict(prefix="", first="Dominic", middle="James", surname="Olivo", dob="1951-03-19", marital="married", email="dominic.olivo@example.com", phone="+1 203 555 0101", status="client"),
 "P2": dict(prefix="", first="Marguerite", middle="Meloni", surname="Olivo", dob="1968-09-02", marital="married", email="marguerite.olivo@example.com", phone="+1 203 555 0102", status="client", maiden="Meloni"),
 "P3": dict(prefix="", first="Anthony", middle="Dominic", surname="Olivo", dob="1976-06-13", marital="married", email="anthony.olivo@example.com", phone="+1 203 555 0103", status="client"),
 "P4": dict(prefix="Dr.", first="Elena", middle="", surname="Olivo", dob="1978-11-27", marital="married", email="elena.olivo@example.com", phone="+1 203 555 0104", status="client", maiden="Broccoli"),
 "P5": dict(prefix="", first="Francesca", middle="", surname="Olivo-Cipolla", dob="1979-04-08", marital="married", email="francesca@cipollastudio.example.com", phone="+1 305 555 0105", status="client"),
 "P6": dict(prefix="", first="Mateo", middle="", surname="Cipolla", dob="1977-01-22", marital="married", email="mateo@cipollastudio.example.com", phone="+1 305 555 0106", status="client"),
 "P7": dict(prefix="", first="Michael", middle="Thomas", surname="Olivo", dob="1983-08-30", marital="divorced", email="michael@calderavc.example.com", phone="+1 415 555 0107", status="client"),
 "P8": dict(prefix="", first="Giulia", middle="", surname="Olivo", dob="2013-05-16", marital="single", email="", phone="", status="client", minor=True),
 "P9": dict(prefix="", first="Sofia", middle="", surname="Olivo", dob="2004-02-11", marital="single", email="sofia.olivo@example.com", phone="+1 203 555 0109", status="client"),
 "P10": dict(prefix="", first="Luca", middle="", surname="Olivo", dob="2007-10-05", marital="single", email="luca.olivo@example.com", phone="+1 203 555 0110", status="client"),
 "P11": dict(prefix="", first="Isabel", middle="", surname="Cipolla", dob="2009-07-24", marital="single", email="", phone="", status="client", minor=True),
 "P12": dict(prefix="", first="Nico", middle="", surname="Cipolla", dob="2013-12-03", marital="single", email="", phone="", status="client", minor=True),
 "P13": dict(prefix="", first="Gabriel", middle="", surname="Cipolla", dob="2013-12-03", marital="single", email="", phone="", status="client", minor=True),
 "P14": dict(prefix="", first="Theo", middle="", surname="Olivo", dob="2012-03-28", marital="single", email="", phone="", status="client", minor=True),
}
extra_people = [
 dict(id="P15", name="Theresa Zucchini Olivo", born=1953, generation=1, residence="Greenwich, CT",
      story="Dominic's first wife, mother of Anthony, Francesca and Michael. Died 2008; her estate funded the Theresa Olivo Family Trust.",
      prefix="", first="Theresa", middle="Zucchini", surname="Olivo", dob="1953-10-14", dod="2008-02-09", marital="married", email="", phone="", status="deceased", maiden="Zucchini"),
 dict(id="P16", name="Claire Porcari", born=1985, generation=2, residence="Palo Alto, CA",
      story="Michael's former wife (divorced 2021), Theo's mother; shared custody. Not a client — contact for Theo's trust and school matters only.",
      prefix="", first="Claire", middle="", surname="Porcari", dob="1985-06-07", marital="divorced", email="claire.porcari@example.com", phone="+1 650 555 0116", status="contact"),
]
people = []
for p in d["people"]:
    people.append({**p, **P[p["id"]]})
people += extra_people

# ── Addresses (is_seasonal + months; the CRM's seasonal-address feature) ─────
A = []
def addr(eid, typ, street, city, state, zip_, primary=False, season=None, note=""):
    A.append(dict(entity_id=eid, address_type=typ, street1=street, city=city, state=state, postal_code=zip_,
                  country="US", is_primary=primary, is_seasonal=bool(season),
                  season_from_month=season[0] if season else None, season_to_month=season[1] if season else None, note=note))
GRN = ("38 Round Hill Road", "Greenwich", "CT", "06831")
NAP = ("2950 Galleon Drive", "Naples", "FL", "34102")
DAR = ("41 Pear Tree Point Road", "Darien", "CT", "06820")
MIA = ("1450 Brickell Bay Drive, Unit 4101", "Miami", "FL", "33131")
SFO = ("2840 Broadway", "San Francisco", "CA", "94115")
ASP = ("120 Ajax Avenue", "Aspen", "CO", "81611")
for pid in ("P1", "P2"):
    addr(pid, "primary_residence", *GRN, primary=True, season=(5, 11), note="Greenwich May–November")
    addr(pid, "primary_residence", *NAP, season=(12, 4), note="Naples December–April (Port Royal, owned by Port Royal Residence LLC)")
addr("P8", "primary_residence", *GRN, primary=True)
for pid in ("P3", "P4"): addr(pid, "primary_residence", *DAR, primary=True)
addr("P4", "business", "30 Shelburne Road (cardiology practice)", "Stamford", "CT", "06902")
addr("P9", "primary_residence", *DAR, primary=True, season=(6, 8), note="Home for the summer")
addr("P9", "mailing", "Box 4127, 69 Brown Street", "Providence", "RI", "02912", season=(9, 5), note="College, September–May")
addr("P10", "primary_residence", *DAR, primary=True, season=(6, 8), note="Home for the summer")
addr("P10", "mailing", "HB 3311, 6 North Main Street", "Hanover", "NH", "03755", season=(9, 5), note="College, September–May")
for pid in ("P5", "P6", "P11", "P12", "P13"): addr(pid, "primary_residence", *MIA, primary=True)
addr("P5", "business", "180 NE 41st Street (gallery)", "Miami", "FL", "33137")
addr("P6", "business", "Cipolla Studio, 3250 NE 1st Avenue", "Miami", "FL", "33137")
addr("P7", "primary_residence", *SFO, primary=True, season=(4, 11), note="San Francisco April–November")
addr("P7", "primary_residence", *ASP, season=(12, 3), note="Aspen December–March (owned by Ajax Peak LLC)")
addr("P7", "business", "Caldera Ventures, 550 Jackson Street", "San Francisco", "CA", "94133")
addr("P14", "primary_residence", *SFO, primary=True, note="Shared custody — alternate weeks with his mother in Palo Alto")
addr("P16", "primary_residence", "1120 Hamilton Avenue", "Palo Alto", "CA", "94301", primary=True)
FO = ("2 Greenwich Office Park, Suite 300", "Greenwich", "CT", "06831")
RA_DE = ("c/o registered agent, 1209 Orange Street", "Wilmington", "DE", "19801")
juris_state = {"Delaware": "DE", "Connecticut": "CT", "Florida": "FL", "California": "CA", "Colorado": "CO"}
for e in d["entities"]:
    addr(e["id"], "business", *FO, primary=True, note="Administered by the family office")
    if e["jurisdiction"] == "Delaware":
        addr(e["id"], "registered", *RA_DE)
addr("E20", "business", *GRN, note="Property address")
addr("E21", "business", *NAP, note="Property address")
addr("E22", "business", "4100 Silverado Trail", "Napa", "CA", "94558", note="Vineyard and winery")
addr("E24", "business", *ASP, note="Property address")
addr("E25", "business", *MIA, note="Condo; gallery space at 180 NE 41st Street")

# ── Outside organisations (CRM entities, non-client) ─────────────────────────
orgs = [
 ("O1", "Wilmington Fiduciary Co.", "corporation", "Corporate trustee (Delaware) — Dynasty Trust and AO Descendants Trust"),
 ("O2", "Coastal Bank, N.A. — Trust Department", "corporation", "Corporate trustee — SLAT, Theresa trust, CRUT; also operating bank and lender"),
 ("O3", "Harbor Charitable Trust", "foundation", "Donor-advised fund sponsor (public charity) for the Olivo Giving Fund"),
 ("O4", "Northline Foods Inc.", "corporation", "Acquirer of Olivo Foods (2014); tenant of the three industrial properties; the family's concentrated stock"),
 ("O5", "Caldera Ventures", "lp", "Michael's venture firm (GP); the family is an LP in Fund II"),
 ("O6", "Cipolla Studio", "llc", "Mateo's architecture practice"),
 ("O7", "Farm Credit West", "corporation", "Lender — Stag's Hollow agricultural term loan"),
 ("O8", "First Western Trust", "corporation", "Lender — Aspen mortgage"),
]

# ── Relationships: family + roles ────────────────────────────────────────────
R = []
def rel(a, b, typ, note=""):
    R.append(dict(from_id=a, to_id=b, relationship_type=typ, note=note))
for a, b, n in [("P1", "P2", "married 2011; prenuptial agreement"), ("P3", "P4", ""), ("P5", "P6", "")]:
    rel(a, b, "spouse", n)
rel("P1", "P15", "former_spouse", "Theresa died 2008")
rel("P7", "P16", "former_spouse", "divorced 2021; shared custody of Theo")
for par, kids in {"P1": ["P3", "P5", "P7", "P8"], "P15": ["P3", "P5", "P7"], "P2": ["P8"], "P3": ["P9", "P10"],
                  "P4": ["P9", "P10"], "P5": ["P11", "P12", "P13"], "P6": ["P11", "P12", "P13"], "P7": ["P14"], "P16": ["P14"]}.items():
    for k in kids: rel(par, k, "parent_of")
rel("P12", "P13", "sibling", "twins")
roles = [
 ("P3", "E1", "managing_member"), ("E1", "E2", "sole_member"), ("E2", "E3", "general_partner"),
 ("P1", "E3", "investment_committee"), ("P3", "E3", "investment_committee"), ("P5", "E3", "investment_committee"), ("P7", "E3", "investment_committee"),
 ("P1", "E4", "grantor"), ("P1", "E4", "trustee"), ("P3", "E4", "successor_trustee"),
 ("P1", "E5", "grantor"), ("O1", "E5", "trustee"), ("P3", "E5", "distribution_committee"),
 ("P1", "E6", "grantor"), ("O2", "E6", "trustee"),
 ("P1", "E7", "grantor"), ("P3", "E7", "trustee"), ("E5", "E7", "remainder_beneficiary"),
 ("P15", "E8", "grantor"), ("O2", "E8", "trustee"),
 ("P1", "E9", "grantor"), ("P2", "E9", "grantor"), ("P2", "E9", "trustee"),
 ("P3", "E10", "manager"), ("P4", "E10", "manager"),
 ("P3", "E11", "grantor"), ("P4", "E11", "grantor"), ("O1", "E11", "trustee"),
 ("P5", "E12", "manager"), ("P6", "E12", "manager"),
 ("P5", "E13", "grantor"), ("P6", "E13", "grantor"), ("P6", "E13", "trustee"),
 ("P7", "E14", "manager"),
 ("P7", "E15", "grantor"), ("P3", "E15", "trustee"),
 ("P1", "E16", "board_chair"), ("P3", "E16", "board_member"), ("P5", "E16", "board_member"), ("P7", "E16", "board_member"), ("P2", "E16", "board_member"),
 ("O3", "E17", "sponsor"), ("P1", "E17", "donor_advisor"), ("P3", "E17", "donor_advisor"), ("P5", "E17", "donor_advisor"), ("P7", "E17", "donor_advisor"),
 ("P1", "E18", "grantor"), ("O2", "E18", "trustee"), ("E16", "E18", "remainder_beneficiary"),
 ("P1", "E19", "grantor"), ("P2", "E19", "grantor"), ("P3", "E19", "trustee"),
 ("E1", "E20", "manager"), ("E1", "E21", "manager"), ("P5", "E22", "board_chair"), ("E1", "E23", "manager"),
 ("P7", "E24", "manager"), ("P5", "E25", "manager"), ("P6", "E25", "manager"), ("P5", "E26", "manager"),
 ("P7", "O5", "general_partner"), ("P6", "O6", "owner"),
]
for a, b, t in roles: rel(a, b, t)
for b in d["beneficial_interests"]:
    for bid in b["beneficiary_ids"]:
        if not any(r["from_id"] == bid and r["to_id"] == b["trust_or_charity_id"] and r["relationship_type"] == "remainder_beneficiary" for r in R):
            rel(bid, b["trust_or_charity_id"], "beneficiary", b["description"])
rel("P1", "E7", "annuitant", "annuity payments through 2027")
rel("P1", "E18", "income_beneficiary", "5% unitrust for life")

# ── Entity detail fields (trust / LLC / LP / charity) ────────────────────────
TD = {
 "E4": dict(trust_type="revocable", revocable=True, situs="CT", gst_exempt=False, termination="pour-over at grantor's death"),
 "E5": dict(trust_type="dynasty", revocable=False, situs="DE", gst_exempt=True, termination="perpetual (Delaware)", funded="Northline Foods stock at the 2014 sale"),
 "E6": dict(trust_type="slat", revocable=False, situs="CT", gst_exempt=False, beneficiaries_note="spouse and Giulia"),
 "E7": dict(trust_type="grat", revocable=False, situs="CT", annuity_term_years=2, annuity_end="2027-12-31", remainder_to="E5"),
 "E8": dict(trust_type="bypass", revocable=False, situs="CT", gst_exempt=False, funded="Theresa's estate, 2008", shares="equal thirds"),
 "E9": dict(trust_type="2503c", revocable=False, situs="CT", vesting_age=21),
 "E11": dict(trust_type="irrevocable", revocable=False, situs="DE", gst_exempt=True),
 "E13": dict(trust_type="irrevocable", revocable=False, situs="FL", gst_exempt=False),
 "E15": dict(trust_type="irrevocable", revocable=False, situs="CA", gst_exempt=False),
 "E18": dict(trust_type="crut", revocable=False, situs="CT", unitrust_rate_pct=5, remainder_to="E16"),
 "E19": dict(trust_type="ilit", revocable=False, situs="CT", crummey_notices=True, policy="$25M survivorship UL, Lincoln Benefit Life"),
 "E16": dict(charity_type="private_foundation", ein_status="501(c)(3)", min_distribution_pct=5),
 "E17": dict(charity_type="daf", sponsor="O3"),
 "E3": dict(fund_role="family_partnership", valuation_policy="funds at latest reported NAV; directs at last round; real assets at appraisal"),
}
type_map = {"LLC": ("llc", None), "LLC (general partner)": ("gp", None), "LP": ("lp", None),
            "Private foundation (501(c)(3))": ("foundation", "private_foundation"), "Donor-advised fund": ("foundation", "daf")}
entities = []
for e in d["entities"]:
    k = e["kind"]
    if k in type_map: et, sub = type_map[k]
    elif "trust" in k.lower() or "GRAT" in k or "SLAT" in k: et, sub = "trust", TD.get(e["id"], {}).get("trust_type")
    elif k.startswith("LLC ("): et, sub = "llc", k[5:-1]
    else: et, sub = "other", k
    if e["id"] == "E1": et = "family_office"
    entities.append({**e, "entity_type": et, "sub_type": sub, "inception_date": f"{e['formed']}-{(int(e['id'][1:]) % 12) + 1:02d}-15",
                     "state": juris_state.get(e["jurisdiction"]), "details": TD.get(e["id"], {})})

# ── Security-master mapping for every holding ────────────────────────────────
LISTED = {"SPY","VXUS","IWM","AAPL","BRK.B","BND","VOO","VEA","VWO","AGG","SGOV","QQQ","EFA","VTIP","PJAN","VTI","VTEB",
          "SCHD","SCHP","VEU","SCHB","SCHF","SCMB","NVDA","MSFT","GOOGL","VT","SCHR"}
MUTUAL = {"FXAIX","FXNAX","FSKAX","FFFGX","VWIUX","SWPPX","SWTSX","FGTXX","FDRXX","SNOXX"}
def mapping(h):
    t = h["ticker"]
    if t in LISTED: return "listed — expected in the security master after secmaster1's load"
    if t in MUTUAL: return "mutual fund / money market — NOT in secmaster1's sources (needs SEC mutual-fund tickers)"
    if t == "NLF": return "FICTIONAL company — decide: replace with a real ticker, or a private-asset record"
    if t.startswith("NOTE-"): return "structured note — map to one of the 54 real notes already in the master"
    if t.startswith("MUNI-"): return "municipal bond — no free CUSIP source; enter as a manual fixed-income asset"
    if t.startswith("OPT-"): return "listed option — create the contract in the options sleeve (rep8)"
    if t in ("BTC", "ETH"): return "digital asset — manual asset"
    if t.startswith(("CASH",)): return "cash — cash position"
    return "private / manual asset (fund interest, direct deal, 529, policy, SMA)"
holdings = [{**h, "secmaster": mapping(h)} for h in d["holdings"]]

out = dict(family="Olivo", as_of=d["as_of"], people=people, organisations=[dict(id=i, name=n, entity_type=t, note=x) for i, n, t, x in orgs],
           entities=entities, addresses=A, relationships=R, ownership=d["ownership"], accounts=d["accounts"],
           holdings=holdings, direct_assets=d["direct_assets"], liabilities=d["liabilities"], totals=d["totals"])
json.dump(out, open(f"{OUT}/data/olivo_crm.json", "w"), indent=1, default=str)
def w(name, rows):
    if not rows: return
    keys = sorted({k for r in rows for k in r})
    with open(f"{OUT}/data/{name}", "w", newline="") as f:
        cw = csv.DictWriter(f, fieldnames=keys); cw.writeheader()
        for r in rows: cw.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
w("crm_people.csv", people); w("crm_entities.csv", entities); w("crm_addresses.csv", A); w("crm_relationships.csv", R)
w("crm_organisations.csv", out["organisations"]); w("holdings_secmaster.csv", holdings)
from collections import Counter
print(len(people), "people;", len(entities), "entities;", len(orgs), "orgs;", len(A), "addresses;", len(R), "relationships")
print(Counter(r["relationship_type"] for r in R))
print(Counter(h["secmaster"].split(" —")[0] for h in holdings))
