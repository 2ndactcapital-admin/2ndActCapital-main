"""The Olivo family — a fictional four-generation family for Hollisworks demo data.
Builds people / entities / ownership / accounts / holdings / liabilities, reconciles
every entity's net worth bottom-up, then writes JSON + CSVs + a Mermaid chart + HTML."""
import csv, json, html, os
from collections import defaultdict
from decimal import Decimal as D

OUT = os.path.dirname(os.path.abspath(__file__))
M = lambda x: D(str(x)) * 1_000_000  # millions → dollars

# ───────────────────────────── People ─────────────────────────────
people = [
 # id, name, born, generation, role/story, residence
 ("P1","Dominic J. Olivo",1951,1,"Founder of Olivo Foods (frozen Italian specialties, sold to Northline Foods in 2014). Widowed 2008; remarried 2011. Still chairs the foundation; drives the cars.","Greenwich, CT / Naples, FL"),
 ("P2","Marguerite Meloni Olivo",1968,1,"Dominic's second wife; former gallery director. Prenuptial agreement; provided for through the 2019 SLAT.","Greenwich, CT"),
 ("P3","Anthony D. Olivo",1976,2,"Eldest. Ran Olivo Foods' operations through the sale; chairs the family office and sits on the Dynasty Trust distribution committee.","Darien, CT"),
 ("P4","Dr. Elena Olivo",1978,2,"Anthony's wife; interventional cardiologist at a Stamford hospital.","Darien, CT"),
 ("P5","Francesca Olivo-Cipolla",1979,2,"Middle child. Contemporary-art dealer with a gallery in Miami; advises the family's art holdings.","Miami, FL"),
 ("P6","Mateo Cipolla",1977,2,"Francesca's husband; architect (Cipolla Studio).","Miami, FL"),
 ("P7","Michael T. Olivo",1983,2,"Youngest of the first marriage. Venture investor in San Francisco (GP of Caldera Ventures). Divorced 2021.","San Francisco, CA / Aspen, CO"),
 ("P8","Giulia Olivo",2013,2,"Dominic and Marguerite's daughter. Minor; assets held in a 2503(c) trust.","Greenwich, CT"),
 ("P9","Sofia Olivo",2004,3,"Anthony & Elena's daughter; college senior (economics).","Providence, RI"),
 ("P10","Luca Olivo",2007,3,"Anthony & Elena's son; college freshman.","Hanover, NH"),
 ("P11","Isabel Cipolla",2009,3,"Francesca & Mateo's daughter.","Miami, FL"),
 ("P12","Nico Cipolla",2013,3,"Francesca & Mateo's son (twin).","Miami, FL"),
 ("P13","Gabriel Cipolla",2013,3,"Francesca & Mateo's son (twin).","Miami, FL"),
 ("P14","Theo Olivo",2012,3,"Michael's son; shared custody.","San Francisco, CA"),
]
PEOPLE = {p[0]: p for p in people}

# ───────────────────────────── Entities ─────────────────────────────
# id, name, kind, jurisdiction, formed, purpose, control (trustee/manager), notes
entities = [
 ("E1","Olivo Family Office LLC","LLC","Delaware",2015,"Family office: manager of Olivo Holdings, employs 6 staff; also the GP's parent.","Managing member: Anthony Olivo",""),
 ("E2","OH GP LLC","LLC (general partner)","Delaware",2015,"General partner of Olivo Holdings LP.","Sole member: Olivo Family Office LLC",""),
 ("E3","Olivo Holdings LP","LP","Delaware",2015,"The main family investment partnership: managed liquid portfolio, private funds, direct deals, the vineyard and the art company.","GP: OH GP LLC; investment committee of Dominic, Anthony, Francesca, Michael","Valuation policy: funds at latest reported NAV, directs at last round, real assets at appraisal (Stag's Hollow 2025, art 2026)."),
 ("E4","Dominic J. Olivo Revocable Trust","Revocable trust","Connecticut",1998,"Dominic's personal holdings; pour-over on death.","Trustee: Dominic; successor: Anthony",""),
 ("E5","Olivo 2014 Dynasty Trust","Irrevocable dynasty trust","Delaware",2014,"Funded with Northline Foods stock at the sale; for descendants of Dominic and Theresa. GST-exempt.","Trustee: Wilmington Fiduciary Co.; distribution committee chaired by Anthony","Concentrated Northline position; a diversification plan runs through Olivo Holdings."),
 ("E6","Marguerite Olivo 2019 SLAT","Spousal lifetime access trust","Connecticut",2019,"For Marguerite and Giulia; holds the Naples residence and a managed portfolio.","Trustee: Coastal Bank Trust Department",""),
 ("E7","DJO 2021 GRAT","Grantor retained annuity trust","Connecticut",2021,"Two-year rolling GRAT on Northline stock; remainder to the Dynasty Trust.","Trustee: Anthony Olivo","Annuity payments to Dominic through 2027."),
 ("E8","Theresa Olivo Family Trust","Bypass (credit-shelter) trust","Connecticut",2008,"Established at Theresa's death for the three children equally; holds the commercial real estate.","Trustee: Coastal Bank Trust Department",""),
 ("E9","Giulia Olivo 2503(c) Trust","Minor's trust","Connecticut",2014,"Annual-exclusion gifts for Giulia; vests at 21.","Trustee: Marguerite Olivo",""),
 ("E10","AOF Investments LLC","LLC","Connecticut",2016,"Anthony and Elena's family investment LLC.","Managers: Anthony and Elena",""),
 ("E11","AO Descendants Trust 2018","Irrevocable trust","Delaware",2018,"Anthony's trust for Sofia and Luca.","Trustee: Wilmington Fiduciary Co.",""),
 ("E12","Cipolla Olivo Holdings LLC","LLC","Florida",2017,"Francesca and Mateo's family LLC: Miami real estate, art, LP units.","Managers: Francesca and Mateo",""),
 ("E13","Cipolla 2020 Family Trust","Irrevocable trust","Florida",2020,"For Isabel, Nico and Gabriel.","Trustee: Mateo Cipolla",""),
 ("E14","MTO Capital LLC","LLC","Delaware",2019,"Michael's personal investment company: public tech, venture fund interest, angel positions, digital assets, the Aspen house.","Manager: Michael Olivo","Michael's divorce settlement (2021) carved out the pre-2016 assets; everything here is post-separation."),
 ("E15","Theo Olivo 2020 Trust","Irrevocable trust","California",2020,"Michael's trust for Theo.","Trustee: Anthony Olivo",""),
 ("E16","Olivo Family Foundation","Private foundation (501(c)(3))","Connecticut",2014,"Funded at the sale; grants about $2.3M a year to food security and culinary education.","Board: Dominic (chair), Anthony, Francesca, Michael, Marguerite","5% minimum distribution; excess business holdings rule limits Northline stock."),
 ("E17","Olivo Giving Fund","Donor-advised fund","Harbor Charitable Trust (sponsor)",2016,"Family DAF; advisers are the four adult Olivos.","Sponsor: Harbor Charitable Trust",""),
 ("E18","Olivo 2016 CRUT","Charitable remainder unitrust","Connecticut",2016,"5% unitrust to Dominic for life; remainder to the foundation.","Trustee: Coastal Bank Trust Department",""),
 ("E19","Olivo 2012 ILIT","Irrevocable life insurance trust","Connecticut",2012,"Owns a $25M survivorship policy on Dominic and Marguerite.","Trustee: Anthony Olivo","Crummey notices annually to the three adult children."),
 ("E20","Round Hill Road LLC","LLC (real estate)","Connecticut",2005,"The Greenwich residence (9 acres).","Managed by the family office",""),
 ("E21","Port Royal Residence LLC","LLC (real estate)","Florida",2019,"The Naples winter residence.","Managed by the family office",""),
 ("E22","Stag's Hollow Vineyard LLC","LLC (operating)","California",2017,"62-acre Napa vineyard and winery; ~5,000 cases a year; 14 employees.","Manager: a hired GM; Francesca chairs","Operating business; K-1s to members."),
 ("E23","Olivo Commercial RE LLC","LLC (real estate)","Connecticut",2008,"Three industrial properties (Bridgeport, Edison, Allentown) leased to Northline Foods on a 15-year NNN lease.","Managed by the family office","Lease expires 2029 — renewal risk."),
 ("E24","Ajax Peak LLC","LLC (real estate)","Colorado",2020,"Michael's Aspen house.","Manager: Michael",""),
 ("E25","Brickell Bay 41 LLC","LLC (real estate)","Florida",2017,"Francesca's Miami condo and the gallery space on NE 41st Street.","Managers: Francesca and Mateo",""),
 ("E26","Olivo Art Holdings LLC","LLC (collectibles)","Delaware",2018,"41 postwar and contemporary works; loaned to museums; insured through AXA Art.","Manager: Francesca Olivo-Cipolla","Art-secured credit line at a private bank."),
]
ENT = {e[0]: e for e in entities}

# ───────────────────────────── Ownership (owner → owned, %) ─────────────────────────────
# owner ids may be people or entities; "kind" describes the interest.
owns = [
 ("P1","E1",40,"Membership interest"),("P3","E1",20,"Membership interest"),("P5","E1",20,"Membership interest"),("P7","E1",20,"Membership interest"),
 ("E1","E2",100,"Sole member"),
 ("E2","E3",0.5,"GP interest"),("E4","E3",34.5,"LP units"),("E5","E3",30,"LP units"),("E10","E3",10,"LP units"),("E12","E3",10,"LP units"),("E14","E3",10,"LP units"),("E6","E3",5,"LP units"),
 ("P1","E4",100,"Grantor / trustee (revocable)"),
 ("E4","E20",100,"Sole member"),
 ("E6","E21",100,"Sole member"),
 ("E3","E22",80,"Membership interest"),("E10","E22",10,"Membership interest"),("E12","E22",10,"Membership interest"),
 ("E8","E23",100,"Sole member"),
 ("E14","E24",100,"Sole member"),
 ("E12","E25",100,"Sole member"),
 ("E3","E26",60,"Membership interest"),("E12","E26",25,"Membership interest"),("E4","E26",15,"Membership interest"),
 ("P3","E10",50,"Membership interest"),("P4","E10",50,"Membership interest"),
 ("P5","E12",60,"Membership interest"),("P6","E12",40,"Membership interest"),
 ("P7","E14",100,"Sole member"),
]
# Beneficial interests (trusts, charities) — not ownership, shown dashed
benefits = [
 ("E5",["P3","P5","P7","P9","P10","P11","P12","P13","P14"],"Descendants of Dominic & Theresa"),
 ("E6",["P2","P8"],"Marguerite; Giulia"),
 ("E7",["P1","E5"],"Annuity to Dominic; remainder to Dynasty Trust"),
 ("E8",["P3","P5","P7"],"Equal shares"),
 ("E9",["P8"],"Giulia"),
 ("E11",["P9","P10"],"Sofia & Luca"),
 ("E13",["P11","P12","P13"],"Isabel, Nico & Gabriel"),
 ("E15",["P14"],"Theo"),
 ("E18",["P1","E16"],"5% unitrust to Dominic; remainder to the foundation"),
 ("E19",["P3","P5","P7","P8"],"Children"),
]

# ───────────────────────────── Accounts ─────────────────────────────
# id, owner (entity or person), custodian, type, custody, authority, account mask
accounts = [
 ("A1","E4","Goldman Sachs PWM","Taxable brokerage","other_custodian","advise","****4471"),
 ("A2","E4","Schwab","Municipal bond ladder","other_custodian","advise","****8830"),
 ("A3","E4","Coastal Bank","Checking + money market","outside","read_only","****1102"),
 ("A4","E4","Goldman Sachs PWM","Structured notes","other_custodian","advise","****4472"),
 ("A5","P1","Fidelity","Rollover IRA","outside","read_only","****6619"),
 ("A6","E5","Goldman Sachs PWM","Taxable brokerage (Northline)","other_custodian","advise","****5120"),
 ("A7","E5","Altruist","Managed account (2nd Act)","integrated","managed","****2201"),
 ("A8","E7","Goldman Sachs PWM","GRAT brokerage","other_custodian","read_only","****5121"),
 ("A9","E3","Altruist","Managed account (2nd Act)","integrated","managed","****2202"),
 ("A10","E3","Olivo Holdings (books)","Private investments ledger","outside","read_only","—"),
 ("A11","E3","Coastal Bank","Operating cash","outside","read_only","****1140"),
 ("A12","E6","Altruist","Managed account (2nd Act)","integrated","managed","****2203"),
 ("A13","E8","Schwab","Taxable brokerage","other_custodian","advise","****8831"),
 ("A14","E9","Altruist","Managed account (2nd Act)","integrated","managed","****2204"),
 ("A15","E10","Altruist","Managed account (2nd Act)","integrated","managed","****2205"),
 ("A16","P3","Fidelity","401(k) rollover IRA","outside","read_only","****6620"),
 ("A17","P4","Fidelity","IRA","outside","read_only","****6621"),
 ("A18","P3","CHET (Fidelity)","529 — Sofia","outside","read_only","****7701"),
 ("A19","P3","CHET (Fidelity)","529 — Luca","outside","read_only","****7702"),
 ("A20","E11","Altruist","Managed account (2nd Act)","integrated","managed","****2206"),
 ("A21","E12","Schwab","Taxable brokerage","other_custodian","advise","****8832"),
 ("A22","P5","Schwab","Roth IRA","outside","read_only","****8833"),
 ("A23","P5","Florida Prepaid / Vanguard 529","529 — Isabel, Nico, Gabriel (3 plans)","outside","read_only","****7710"),
 ("A24","E13","Altruist","Managed account (2nd Act)","integrated","managed","****2207"),
 ("A25","E14","Interactive Brokers","Margin account (options overlay)","other_custodian","advise","****3390"),
 ("A26","E14","Coinbase Prime","Digital assets","outside","read_only","****0042"),
 ("A27","E14","MTO Capital (books)","Venture & angel ledger","outside","read_only","—"),
 ("A28","P7","Schwab","Roth IRA","outside","read_only","****8834"),
 ("A29","E15","Altruist","Managed account (2nd Act)","integrated","managed","****2208"),
 ("A30","E16","Goldman Sachs PWM","Foundation endowment","other_custodian","advise","****5130"),
 ("A31","E17","Harbor Charitable Trust","DAF","outside","read_only","****9001"),
 ("A32","E18","Schwab","CRUT brokerage","other_custodian","advise","****8835"),
 ("A33","E19","Lincoln Benefit Life","Survivorship UL policy","outside","read_only","POL-****77"),
 ("A34","P2","Schwab","Taxable brokerage","other_custodian","advise","****8836"),
]
ACC = {a[0]: a for a in accounts}

# ───────────────────────────── Holdings ─────────────────────────────
# account, security, ticker/id, asset class, sub-class, qty, price, value override (None=qty*price), cost basis, notes
H = []
def h(acct, name, tick, ac, sub, qty=None, px=None, val=None, cost=None, note=""):
    v = D(str(val)) if val is not None else (D(str(qty)) * D(str(px)))
    H.append(dict(account=acct, security=name, ticker=tick, asset_class=ac, sub_class=sub,
                  quantity=qty, price=px, value=v, cost_basis=(D(str(cost)) if cost is not None else None), notes=note))

# A1 Dominic revocable — Goldman taxable ($62M)
h("A1","SPDR S&P 500 ETF","SPY","Public equity","US large cap ETF",38_000,642.10,cost=14_800_000)
h("A1","Vanguard Total International Stock ETF","VXUS","Public equity","International ETF",160_000,71.40,cost=9_100_000)
h("A1","iShares Russell 2000 ETF","IWM","Public equity","US small cap ETF",22_000,241.30,cost=4_600_000)
h("A1","Northline Foods Inc.","NLF","Public equity","Single stock (concentrated)",120_000,61.05,cost=1_440_000,note="Residual from the 2014 sale; basis $12/sh")
h("A1","Apple Inc.","AAPL","Public equity","Single stock",18_000,262.40,cost=1_900_000)
h("A1","Berkshire Hathaway Class B","BRK.B","Public equity","Single stock",9_000,498.20,cost=2_100_000)
h("A1","Vanguard Total Bond Market ETF","BND","Fixed income","Core bond ETF",95_000,74.90,cost=7_400_000)
h("A1","Goldman Sachs Financial Square Govt MMF","FGTXX","Cash","Money market",3_250_000,1.00,cost=3_250_000)
# A2 muni ladder ($18M)
for yr,cpn,face in [(2027,"4.00",2_000_000),(2028,"5.00",2_500_000),(2029,"4.00",2_500_000),(2030,"5.00",3_000_000),(2031,"4.00",3_000_000),(2033,"5.00",2_500_000),(2036,"4.00",2_500_000)]:
    h("A2",f"Connecticut / NY GO & essential-service munis {cpn}% due {yr}",f"MUNI-{yr}","Fixed income","Tax-exempt municipal bond",face,0.985 if yr<2030 else 1.01,cost=face)
# A3 cash
h("A3","Coastal Bank checking","CASH","Cash","Operating cash",val=1_150_000,cost=1_150_000)
h("A3","Coastal Bank money market","CASH-MM","Cash","Money market",val=3_050_000,cost=3_050_000)
# A4 structured notes
h("A4","JPM autocallable contingent-coupon note on S&P 500 (CUSIP 48133X***) due 2028","NOTE-JPM-SPX","Structured note","Autocallable, 70% barrier, 9.25% coupon",val=5_000_000,cost=5_000_000,note="Issued Mar 2026; worst-of? no — single index")
h("A4","Goldman buffered note on Nasdaq-100 (15% buffer, 1.3x to 24% cap) due 2027","NOTE-GS-NDX","Structured note","Buffered growth note",val=3_120_000,cost=3_000_000)
# A5 Dominic IRA (held away)
h("A5","Fidelity 500 Index Fund","FXAIX","Public equity","US large cap fund",28_000,228.10,cost=3_100_000)
h("A5","Fidelity U.S. Bond Index Fund","FXNAX","Fixed income","Core bond fund",250_000,10.52,cost=2_700_000)
h("A5","Fidelity Government Cash Reserves","FDRXX","Cash","Money market",450_000,1.00,cost=450_000)
# A6 Dynasty trust — Northline
h("A6","Northline Foods Inc.","NLF","Public equity","Single stock (concentrated)",1_800_000,61.05,cost=21_600_000,note="Received at the 2014 sale; basis $12/sh. Collateral for the $20M SBL.")
h("A6","Goldman Sachs Financial Square Govt MMF","FGTXX","Cash","Money market",1_800_000,1.00,cost=1_800_000)
# A7 Dynasty trust — Altruist managed ($40M: diversification sleeve)
h("A7","Vanguard S&P 500 ETF","VOO","Public equity","US large cap ETF",24_000,590.20,cost=12_400_000)
h("A7","Vanguard FTSE Developed Markets ETF","VEA","Public equity","Developed ex-US ETF",150_000,58.90,cost=7_900_000)
h("A7","Vanguard FTSE Emerging Markets ETF","VWO","Public equity","Emerging markets ETF",70_000,52.10,cost=3_400_000)
h("A7","iShares Core U.S. Aggregate Bond ETF","AGG","Fixed income","Core bond ETF",95_000,101.30,cost=9_700_000)
h("A7","iShares 0-3 Month Treasury Bond ETF","SGOV","Cash","T-bill ETF",48_000,100.60,cost=4_800_000)
h("A7","Invesco QQQ Trust","QQQ","Public equity","Nasdaq-100 ETF",6_500,598.40,cost=3_100_000,note="Candidate for a Replication Studio direct-index replica")
# A8 GRAT
h("A8","Northline Foods Inc.","NLF","Public equity","Single stock (concentrated)",400_000,61.05,cost=4_800_000)
# A9 Holdings LP — Altruist managed ($55M)
h("A9","Olivo Core Equity (direct-indexed S&P 500 replica)","SMA-CORE","Public equity","Direct-index SMA (503 positions)",val=24_600_000,cost=19_200_000,note="Replication Studio replica of SPY; tax-loss harvesting on")
h("A9","iShares MSCI EAFE ETF","EFA","Public equity","Developed ex-US ETF",110_000,88.70,cost=8_100_000)
h("A9","iShares Core U.S. Aggregate Bond ETF","AGG","Fixed income","Core bond ETF",70_000,101.30,cost=7_000_000)
h("A9","Vanguard Short-Term Inflation-Protected Securities ETF","VTIP","Fixed income","TIPS ETF",80_000,50.10,cost=3_900_000)
h("A9","iShares 0-3 Month Treasury Bond ETF","SGOV","Cash","T-bill ETF",60_000,100.60,cost=6_000_000)
h("A9","Innovator U.S. Equity Power Buffer ETF (Jan)","PJAN","Public equity","Defined-outcome ETF",60_000,42.80,cost=2_400_000,note="Options-sleeve candidate (rep8)")
# A10 Holdings LP — privates
h("A10","Harbor Point Partners IV, L.P.","PE-HPP4","Private equity","Buyout fund (2019 vintage)",val=22_300_000,cost=18_000_000,note="$30M commitment, $8M unfunded; Q2 2026 NAV")
h("A10","Thornfield Credit Opportunities II","PC-TCO2","Private credit","Opportunistic credit fund",val=15_100_000,cost=15_000_000,note="$15M commitment, fully drawn; 11.2% net IRR")
h("A10","Stillwater Macro Fund Ltd.","HF-STILL","Hedge fund","Global macro (quarterly liquidity)",val=12_400_000,cost=10_000_000)
h("A10","Brightwater Aquaculture, Inc. — Series C Preferred","PRIV-BWA","Private company","Direct investment (land-based salmon)",420_000,15.48,cost=6_500_000,note="Last round Nov 2025; board observer seat (Anthony)")
h("A10","New England Harbor FC — 2.5% LLC interest","PRIV-NEHFC","Private company","Sports franchise minority stake",val=4_000_000,cost=2_500_000,note="USL Championship club; capital call rights")
h("A10","2nd Act Marketplace SPV: Meridian Logistics Class A","SPV-MERIDIAN","Private company","Co-investment SPV (2nd Act Capital)",val=3_050_000,cost=3_000_000,note="Invested through the Hollisworks marketplace, 2026")
# A11 operating cash
h("A11","Coastal Bank operating account","CASH","Cash","Operating cash",val=7_000_000,cost=7_000_000)
# A12 SLAT Altruist ($14M)
h("A12","Vanguard Total Stock Market ETF","VTI","Public equity","US total market ETF",21_000,318.50,cost=5_400_000)
h("A12","Vanguard Total International Stock ETF","VXUS","Public equity","International ETF",45_000,71.40,cost=2_900_000)
h("A12","Vanguard Tax-Exempt Bond ETF","VTEB","Fixed income","Municipal bond ETF",80_000,50.80,cost=4_000_000)
# A13 Theresa trust Schwab ($6M)
h("A13","Schwab U.S. Dividend Equity ETF","SCHD","Public equity","Dividend ETF",110_000,29.40,cost=2_600_000)
h("A13","Schwab U.S. TIPS ETF","SCHP","Fixed income","TIPS ETF",50_000,27.10,cost=1_350_000)
h("A13","Schwab Treasury Obligations MMF","SNOXX","Cash","Money market",1_420_000,1.00,cost=1_420_000)
# A14 Giulia 2503c
h("A14","Vanguard Total Stock Market ETF","VTI","Public equity","US total market ETF",5_000,318.50,cost=1_100_000)
h("A14","iShares Core U.S. Aggregate Bond ETF","AGG","Fixed income","Core bond ETF",5_000,101.30,cost=500_000)
# A15 AOF Investments Altruist ($8.5M)
h("A15","Vanguard S&P 500 ETF","VOO","Public equity","US large cap ETF",7_200,590.20,cost=3_100_000)
h("A15","Vanguard FTSE All-World ex-US ETF","VEU","Public equity","International ETF",28_000,68.30,cost=1_600_000)
h("A15","Vanguard Intermediate-Term Tax-Exempt Fund","VWIUX","Fixed income","Municipal bond fund",160_000,13.90,cost=2_200_000)
# A16/17 personal IRAs
h("A16","Fidelity Total Market Index Fund","FSKAX","Public equity","US total market fund",19_000,166.20,cost=1_900_000)
h("A17","Fidelity Freedom 2045 Fund","FFFGX","Multi-asset","Target-date fund",85_000,14.10,cost=900_000)
h("A18","CHET Age-Based Portfolio (college)","529-SOFIA","Multi-asset","529 plan",val=310_000,cost=200_000)
h("A19","CHET Age-Based Portfolio (2025)","529-LUCA","Multi-asset","529 plan",val=290_000,cost=200_000)
# A20 AO Descendants
h("A20","Vanguard Total Stock Market ETF","VTI","Public equity","US total market ETF",12_000,318.50,cost=2_900_000)
h("A20","Vanguard Total International Stock ETF","VXUS","Public equity","International ETF",12_000,71.40,cost=700_000)
h("A20","iShares Core U.S. Aggregate Bond ETF","AGG","Fixed income","Core bond ETF",7_000,101.30,cost=700_000)
# A21 Cipolla Olivo Schwab ($4.8M)
h("A21","Schwab U.S. Broad Market ETF","SCHB","Public equity","US total market ETF",90_000,24.10,cost=1_500_000)
h("A21","Schwab International Equity ETF","SCHF","Public equity","International ETF",40_000,43.20,cost=1_300_000)
h("A21","Schwab Municipal Bond ETF","SCMB","Fixed income","Municipal bond ETF",35_000,25.20,cost=880_000)
# A22 Francesca Roth
h("A22","Schwab S&P 500 Index Fund","SWPPX","Public equity","US large cap fund",9_500,94.60,cost=600_000)
h("A23","Florida Prepaid + Vanguard 529 (3 plans)","529-CIPOLLA","Multi-asset","529 plans",val=540_000,cost=400_000)
# A24 Cipolla trust
h("A24","Vanguard Total Stock Market ETF","VTI","Public equity","US total market ETF",8_000,318.50,cost=1_900_000)
h("A24","iShares Core U.S. Aggregate Bond ETF","AGG","Fixed income","Core bond ETF",10_000,101.30,cost=1_000_000)
# A25 MTO Capital — Interactive Brokers ($11M)
h("A25","NVIDIA Corp.","NVDA","Public equity","Single stock",14_000,196.30,cost=900_000)
h("A25","Microsoft Corp.","MSFT","Public equity","Single stock",5_500,512.40,cost=1_400_000)
h("A25","Alphabet Inc. Class A","GOOGL","Public equity","Single stock",8,251.60,val=2_012_800,cost=600_000,note="qty 8,000")
h("A25","Invesco QQQ Trust","QQQ","Public equity","Nasdaq-100 ETF",5_000,598.40,cost=2_100_000)
h("A25","Written SPY calls (covered-call overlay), Dec 2026 660","OPT-SPY-C660","Options","Short call overlay",-50,None,val=-187_500,cost=-240_000,note="50 contracts; premium received $240k")
h("A25","Cash / sweep","CASH","Cash","Brokerage cash",val=1_050_000,cost=1_050_000)
# A26 crypto
h("A26","Bitcoin","BTC","Digital assets","Cryptocurrency",18.5,97_400,cost=620_000)
h("A26","Ether","ETH","Digital assets","Cryptocurrency",120,3_280,cost=210_000)
# A27 venture ledger
h("A27","Caldera Ventures Fund II, L.P. — LP interest","VC-CALDERA2","Venture capital","Venture fund (Michael is a GP)",val=9_000_000,cost=6_000_000,note="$10M commitment, $6M called; plus GP carry (unvalued)")
h("A27","Luma Robotics — seed SAFE","ANGEL-LUMA","Private company","Angel investment",val=750_000,cost=250_000,note="Marked at the Series A price")
h("A27","Halcyon Bio — seed preferred","ANGEL-HALCYON","Private company","Angel investment",val=500_000,cost=500_000)
h("A27","Pressbox Media — convertible note","ANGEL-PRESSBOX","Private company","Angel investment",val=0,cost=150_000,note="Written off 2025")
# A28 Michael Roth
h("A28","Schwab Total Stock Market Index Fund","SWTSX","Public equity","US total market fund",6_000,101.30,cost=350_000)
# A29 Theo trust
h("A29","Vanguard Total Stock Market ETF","VTI","Public equity","US total market ETF",4_200,318.50,cost=1_000_000)
h("A29","iShares Core U.S. Aggregate Bond ETF","AGG","Fixed income","Core bond ETF",5_500,101.30,cost=550_000)
# A30 Foundation ($45M)
h("A30","Vanguard Total World Stock ETF","VT","Public equity","Global equity ETF",220_000,136.20,cost=22_000_000)
h("A30","Vanguard Total Bond Market ETF","BND","Fixed income","Core bond ETF",140_000,74.90,cost=10_800_000)
h("A30","Harbor Point Partners IV, L.P. (foundation sleeve)","PE-HPP4","Private equity","Buyout fund",val=3_200_000,cost=2_500_000)
h("A30","Goldman Sachs Financial Square Govt MMF","FGTXX","Cash","Money market",1_300_000,1.00,cost=1_300_000)
# A31 DAF
h("A31","Harbor Charitable balanced pool (60/40)","DAF-POOL","Multi-asset","DAF investment pool",val=6_100_000,cost=5_000_000)
# A32 CRUT
h("A32","Schwab U.S. Dividend Equity ETF","SCHD","Public equity","Dividend ETF",200_000,29.40,cost=4_800_000)
h("A32","Schwab Intermediate-Term U.S. Treasury ETF","SCHR","Fixed income","Treasury ETF",200_000,26.10,cost=5_200_000)
# A33 ILIT policy
h("A33","Survivorship UL policy, $25,000,000 face (Dominic & Marguerite)","POL-SUL","Life insurance","Survivorship universal life — cash value",val=4_100_000,cost=3_600_000,note="Premium $185k/yr via Crummey gifts")
# A34 Marguerite Schwab
h("A34","Schwab U.S. Broad Market ETF","SCHB","Public equity","US total market ETF",70_000,24.10,cost=1_100_000)
h("A34","Schwab Municipal Bond ETF","SCMB","Fixed income","Municipal bond ETF",45_000,25.20,cost=1_130_000)
h("A34","Schwab Treasury Obligations MMF","SNOXX","Cash","Money market",270_000,1.00,cost=270_000)

# ───────────────────────────── Directly held real assets (no custodian) ─────────────────────────────
# owner, name, asset class, sub-class, value, cost, note
direct = [
 ("E20","Round Hill Road, Greenwich — residence, 9 acres","Real estate","Primary residence",14_500_000,6_200_000,"Appraised 2025"),
 ("E21","Port Royal, Naples FL — winter residence","Real estate","Secondary residence",9_200_000,7_100_000,"Purchased 2019"),
 ("E22","Stag's Hollow — 62 acres, Napa (vines, winery, tasting room)","Real estate","Agricultural / operating property",28_000_000,19_500_000,"Appraised 2025"),
 ("E22","Winery equipment and vehicles","Operating business","Plant & equipment",2_500_000,3_100_000,"Net book"),
 ("E22","Wine library (back vintages, ~9,000 bottles)","Collectibles","Wine",1_800_000,900_000,"Insured at $2.1M"),
 ("E23","Bridgeport CT cold-storage facility","Real estate","Industrial (NNN to Northline)",15_000_000,9_000_000,""),
 ("E23","Edison NJ distribution center","Real estate","Industrial (NNN to Northline)",14_000_000,8_500_000,""),
 ("E23","Allentown PA plant","Real estate","Industrial (NNN to Northline)",9_000_000,6_000_000,""),
 ("E24","Ajax Peak, Aspen — ski house","Real estate","Secondary residence",7_800_000,6_900_000,"Purchased 2020"),
 ("E25","Brickell Bay 41 — condo","Real estate","Primary residence",4_600_000,3_400_000,""),
 ("E25","NE 41st Street — gallery space","Real estate","Commercial (gallery)",3_200_000,2_700_000,"Leased to Francesca's gallery at market"),
 ("E26","Postwar abstraction — 9 works (Mitchell, Frankenthaler, Twombly school)","Collectibles","Fine art",12_800_000,5_100_000,"Three on museum loan"),
 ("E26","Contemporary — 23 works (Ruscha, Kusama, Doig, Marshall, others)","Collectibles","Fine art",15_400_000,9_800_000,""),
 ("E26","Photography and works on paper — 9 works","Collectibles","Fine art",3_200_000,2_200_000,""),
 ("E4","1963 Ferrari 250 GT Lusso","Collectibles","Classic car",2_900_000,1_100_000,"Pebble Beach 2019"),
 ("E4","1957 Mercedes-Benz 300SL Roadster","Collectibles","Classic car",1_800_000,950_000,""),
 ("E4","1967 Shelby GT500, 1972 Lancia Stratos, 1989 Porsche 959 and five others","Collectibles","Classic cars (8)",7_700_000,4_900_000,"Stored in Greenwich; Hagerty"),
 ("P2","Jewelry (Cartier, Graff; the Meloni sapphire)","Collectibles","Jewelry",2_300_000,1_900_000,"Scheduled on the homeowners policy"),
 ("E1","Family office working capital","Cash","Operating cash",1_400_000,1_400_000,""),
]

# ───────────────────────────── Liabilities ─────────────────────────────
liabilities = [
 ("E5","Goldman Sachs — securities-backed line of credit","Secured credit line",20_000_000,"SOFR + 1.10%","Collateral: Northline shares in A6; used for Holdings LP capital calls and the Naples purchase."),
 ("E21","Coastal Bank — mortgage (Naples)","Mortgage",3_500_000,"5.35% fixed, 2049",""),
 ("E22","Farm Credit West — agricultural term loan","Term loan",6_000_000,"6.10%, 2032","Secured by the vineyard"),
 ("E23","Coastal Bank — commercial mortgage (3 properties)","Mortgage",14_000_000,"4.80% fixed, 2033","Cross-collateralised"),
 ("E24","First Western — mortgage (Aspen)","Mortgage",2_400_000,"6.25% fixed, 2050",""),
 ("E25","Coastal Bank — mortgage (Miami condo)","Mortgage",1_900_000,"5.90% fixed, 2047",""),
 ("E26","Private bank — art-secured credit line","Secured credit line",5_000_000,"SOFR + 1.75%","Secured by 11 works; drawn for the 2025 Marshall acquisition"),
 ("E3","Harbor Point Partners IV — unfunded commitment","Off-balance-sheet commitment",8_000_000,"","Callable through 2027"),
 ("E14","Caldera Ventures Fund II — unfunded commitment","Off-balance-sheet commitment",4_000_000,"","Callable through 2028"),
]

# ───────────────────────────── Reconcile ─────────────────────────────
owner_of_account = {a[0]: a[1] for a in accounts}
gross = defaultdict(D)       # direct gross assets per owner (entity or person)
for r in H: gross[owner_of_account[r["account"]]] += r["value"]
for o,_,_,_,v,_,_ in direct: gross[o] += D(str(v))
debt = defaultdict(D)
for o,_,kind,amt,_,_ in liabilities:
    if kind != "Off-balance-sheet commitment": debt[o] += D(str(amt))

# ownership of entity → list of (owner, pct)
owners_of = defaultdict(list)
for o,e,pct,_ in owns: owners_of[e].append((o, D(str(pct))))

nav = {}   # entity NAV including look-through of entities it owns
def entity_nav(e, seen=()):
    if e in nav: return nav[e]
    if e in seen: raise RuntimeError("cycle " + e)
    total = gross[e] - debt[e]
    for owned, lst in owners_of.items():
        for o,pct in lst:
            if o == e: total += entity_nav(owned, seen + (e,)) * pct / 100
    nav[e] = total
    return total
for e in ENT: entity_nav(e)

# look-through to people: owned entities (not trusts/charities — those listed separately as beneficial)
person_lt = defaultdict(D)
for p in PEOPLE: person_lt[p] += gross[p] - debt[p]
def allocate(e, pct, seen=()):
    for o,share in owners_of.get(e, []):
        if o in PEOPLE: person_lt[o] += nav[e] * pct * share / 10000
        else: allocate(e, pct * share / 100, seen + (e,))
# distribute every entity that has owners (top-down): start from entities whose owners include people
roots = [e for e in ENT if any(o in PEOPLE for o,_ in owners_of.get(e, []))]
# Only "ownership" edges carry value to people; P1→E4 (revocable) is 100% so Dominic gets E4's NAV.
for e in roots:
    for o,share in owners_of[e]:
        if o in PEOPLE: person_lt[o] += nav[e] * share / 100

unowned = [e for e in ENT if not owners_of.get(e)]   # trusts and charities with no "owner"
charitable = {"E16","E17","E18"}
family_total = sum(nav[e] for e in unowned if e not in charitable) + sum(v for p,v in person_lt.items())
charity_total = sum(nav[e] for e in charitable)

# ───────────────────────────── Write data pack ─────────────────────────────
def money(x): return f"${x/1_000_000:,.1f}M"
os.makedirs(f"{OUT}/data", exist_ok=True)
def wcsv(name, header, rows):
    with open(f"{OUT}/data/{name}", "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
wcsv("people.csv", ["id","name","born","generation","story","residence"], people)
wcsv("entities.csv", ["id","name","kind","jurisdiction","formed","purpose","control","notes","net_worth_usd"], [list(e)+[int(nav[e[0]])] for e in entities])
wcsv("ownership.csv", ["owner_id","owned_id","pct","interest"], owns)
wcsv("beneficial_interests.csv", ["trust_or_charity_id","beneficiary_ids","description"], [(t," ".join(b),d) for t,b,d in benefits])
wcsv("accounts.csv", ["id","owner_id","custodian","type","custody","authority","account_mask"], accounts)
wcsv("holdings.csv", ["account_id","security","ticker","asset_class","sub_class","quantity","price","value_usd","cost_basis_usd","notes"],
     [(r["account"],r["security"],r["ticker"],r["asset_class"],r["sub_class"],r["quantity"],r["price"],int(r["value"]),(int(r["cost_basis"]) if r["cost_basis"] is not None else ""),r["notes"]) for r in H])
wcsv("direct_assets.csv", ["owner_id","asset","asset_class","sub_class","value_usd","cost_basis_usd","notes"], direct)
wcsv("liabilities.csv", ["owner_id","liability","kind","amount_usd","terms","notes"], liabilities)
json.dump({"family":"Olivo","as_of":"2026-11-04","people":[dict(zip(["id","name","born","generation","story","residence"],p)) for p in people],
           "entities":[dict(zip(["id","name","kind","jurisdiction","formed","purpose","control","notes"],e), net_worth_usd=int(nav[e[0]])) for e in entities],
           "ownership":[dict(zip(["owner_id","owned_id","pct","interest"],o)) for o in owns],
           "beneficial_interests":[dict(trust_or_charity_id=t,beneficiary_ids=b,description=d) for t,b,d in benefits],
           "accounts":[dict(zip(["id","owner_id","custodian","type","custody","authority","account_mask"],a)) for a in accounts],
           "holdings":[{**r,"value":int(r["value"]),"cost_basis":(int(r["cost_basis"]) if r["cost_basis"] is not None else None)} for r in H],
           "direct_assets":[dict(zip(["owner_id","asset","asset_class","sub_class","value_usd","cost_basis_usd","notes"],d)) for d in direct],
           "liabilities":[dict(zip(["owner_id","liability","kind","amount_usd","terms","notes"],l)) for l in liabilities],
           "totals":{"family_net_worth_usd":int(family_total),"charitable_usd":int(charity_total)}},
          open(f"{OUT}/data/olivo_family.json","w"), indent=1, default=str)

# ───────────────────────────── Mermaid ─────────────────────────────
short = {"E1":"Family Office LLC","E2":"OH GP LLC","E3":"Olivo Holdings LP","E4":"Dominic Revocable Trust","E5":"2014 Dynasty Trust","E6":"Marguerite 2019 SLAT","E7":"DJO 2021 GRAT","E8":"Theresa Family Trust","E9":"Giulia 2503(c) Trust","E10":"AOF Investments LLC","E11":"AO Descendants Trust","E12":"Cipolla Olivo Holdings","E13":"Cipolla 2020 Family Trust","E14":"MTO Capital LLC","E15":"Theo 2020 Trust","E16":"Family Foundation","E17":"Giving Fund (DAF)","E18":"2016 CRUT","E19":"2012 ILIT","E20":"Round Hill Road LLC","E21":"Port Royal Residence LLC","E22":"Stag's Hollow Vineyard","E23":"Commercial RE LLC","E24":"Ajax Peak LLC","E25":"Brickell Bay 41 LLC","E26":"Art Holdings LLC"}
cls = {"LLC":"llc","LLC (general partner)":"llc","LP":"lp","LLC (real estate)":"re","LLC (operating)":"re","LLC (collectibles)":"re"}
def ecls(e):
    k = ENT[e][2]
    if "trust" in k.lower() and "Charitable" not in k: return "trust"
    if e in charitable or "ILIT" in ENT[e][1]: return "charity" if e in charitable else "trust"
    return cls.get(k, "llc")
mm = ["flowchart TB"]
mm.append("  subgraph G1[Generation 1]\n    P1([Dominic 1951])\n    P2([Marguerite 1968])\n  end")
mm.append("  subgraph G2[Generation 2]\n    P3([Anthony 1976])\n    P4([Elena 1978])\n    P5([Francesca 1979])\n    P6([Mateo 1977])\n    P7([Michael 1983])\n    P8([Giulia 2013])\n  end")
mm.append("  subgraph G3[Generation 3]\n    P9([Sofia 2004])\n    P10([Luca 2007])\n    P11([Isabel 2009])\n    P12([Nico 2013])\n    P13([Gabriel 2013])\n    P14([Theo 2012])\n  end")
for e in ENT:
    shape = ("[[%s]]" if ecls(e)=="lp" else "{{%s}}" if ecls(e)=="trust" else "(%s)" if ecls(e)=="charity" else "[%s]")
    mm.append(f"  {e}{shape % (short[e] + '<br/>' + money(nav[e]))}")
for o,e,pct,_ in owns:
    lab = f"{pct:g}%"
    mm.append(f"  {o} -->|{lab}| {e}")
for t,b,d in benefits:
    for x in b: mm.append(f"  {t} -.->|benefits| {x}")
for e in ENT: mm.append(f"  class {e} {ecls(e)}")
mm.append("  classDef llc fill:#e9eef4,stroke:#4a5d73,color:#1f2a37\n  classDef lp fill:#dfe7f0,stroke:#2f4a66,color:#1f2a37,stroke-width:2px\n  classDef trust fill:#f3ecdf,stroke:#8a6d2f,color:#3a2e14\n  classDef re fill:#e6efe6,stroke:#4b7a4b,color:#1f3320\n  classDef charity fill:#f1e4ea,stroke:#8a3c5e,color:#3a1a28")
open(f"{OUT}/data/olivo_structure.mmd","w").write("\n".join(mm))

json.dump({"nav":{k:int(v) for k,v in nav.items()},"gross":{k:int(v) for k,v in gross.items()},"debt":{k:int(v) for k,v in debt.items()},
           "person_lt":{k:int(v) for k,v in person_lt.items()},"family_total":int(family_total),"charity_total":int(charity_total),
           "unowned":unowned}, open(f"{OUT}/data/_totals.json","w"), indent=1)
print("family", money(family_total), "charity", money(charity_total))
for e in ENT: print(e, short[e], money(nav[e]))
for p in PEOPLE: print(p, PEOPLE[p][1], money(person_lt[p]))
