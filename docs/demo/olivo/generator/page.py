import json, html, os
from collections import defaultdict
from decimal import Decimal as D
OUT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(f"{OUT}/data/olivo_family.json"))
T = json.load(open(f"{OUT}/data/_totals.json"))
mmd = open(f"{OUT}/data/olivo_structure.mmd").read()
E = {e["id"]: e for e in d["entities"]}; P = {p["id"]: p for p in d["people"]}
name = lambda i: (E.get(i) or P.get(i))["name"]
def m(x): x=float(x); return f"${x/1e6:,.1f}M" if abs(x) >= 1e6 else f"${x:,.0f}"
esc = html.escape

# asset-class roll-up (gross, family incl. charities, no liabilities)
ac = defaultdict(float)
for r in d["holdings"]: ac[r["asset_class"]] += float(r["value"])
for a in d["direct_assets"]: ac[a["asset_class"]] += float(a["value_usd"])
ac_rows = sorted(ac.items(), key=lambda kv: -kv[1]); ac_total = sum(ac.values())
debt_total = sum(float(l["amount_usd"]) for l in d["liabilities"] if l["kind"] != "Off-balance-sheet commitment")

acc_by_owner = defaultdict(list)
for a in d["accounts"]: acc_by_owner[a["owner_id"]].append(a)
hold_by_acc = defaultdict(list)
for r in d["holdings"]: hold_by_acc[r["account_id"] if "account_id" in r else r["account"]].append(r)
direct_by_owner = defaultdict(list)
for a in d["direct_assets"]: direct_by_owner[a["owner_id"]].append(a)
liab_by_owner = defaultdict(list)
for l in d["liabilities"]: liab_by_owner[l["owner_id"]].append(l)
owners_of = defaultdict(list); owned_by = defaultdict(list)
for o in d["ownership"]: owners_of[o["owned_id"]].append(o); owned_by[o["owner_id"]].append(o)
bene = {b["trust_or_charity_id"]: b for b in d["beneficial_interests"]}

css = """
<title>The Olivo Family</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,300;0,400;0,500;0,600;1,400&family=Hanken+Grotesk:wght@400;500;600&display=swap">
<style>
/* Layout: one reading column for the story, full-width bands for the chart and the ledgers. */
:root{--bg:#f7f4ec;--paper:#ffffff;--ink:#1f2a37;--muted:#5b6675;--rule:#e6e0d2;--navy:#1d3557;--navy-soft:#e8edf4;--gold:#8a6d2f;--green:#4b7a4b;--plum:#8a3c5e;
 --display:'Spectral',Georgia,serif;--body:'Hanken Grotesk',system-ui,sans-serif}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15181d;--paper:#1d2127;--ink:#e8e6df;--muted:#a7adb7;--rule:#2d333c;--navy:#9db4d6;--navy-soft:#242c38;--gold:#c9a95e;--green:#8fbf8f;--plum:#d48bab;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#15181d;--paper:#1d2127;--ink:#e8e6df;--muted:#a7adb7;--rule:#2d333c;--navy:#9db4d6;--navy-soft:#242c38;--gold:#c9a95e;--green:#8fbf8f;--plum:#d48bab;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font:17px/1.55 var(--body);padding-block:0 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.wrap{max-width:1100px;margin:0 auto}
.read{max-width:68ch}
h1,h2,h3{font-family:var(--display);font-weight:500;text-wrap:balance;line-height:1.15;margin:0}
h1{font-size:clamp(2.2rem,5vw,3.4rem);letter-spacing:-.01em}
h2{font-size:1.7rem;margin-top:3.2rem;padding-top:1.2rem;border-top:1px solid var(--rule)}
h3{font-size:1.2rem;margin-top:1.6rem}
.eyebrow{font:600 .72rem/1 var(--body);letter-spacing:.12em;text-transform:uppercase;color:var(--gold);margin:40px 0 12px}
.lede{font-family:var(--display);font-size:1.25rem;font-style:italic;color:var(--muted);margin:14px 0 0}
p{margin:.8em 0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:28px 0 8px}
.stat{background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:14px 16px}
.stat b{display:block;font:500 1.6rem/1.1 var(--display);font-variant-numeric:tabular-nums}
.stat span{font-size:.8rem;color:var(--muted)}
.note{font-size:.9rem;color:var(--muted)}
.chart{background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:12px;overflow-x:auto;margin-top:14px}
.chart pre.mermaid{min-width:900px;margin:0}
.legend{display:flex;flex-wrap:wrap;gap:10px 18px;font-size:.82rem;color:var(--muted);margin-top:10px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px;border:1px solid}
table{border-collapse:collapse;width:100%;font-size:.9rem;background:var(--paper)}
.tbl{overflow-x:auto;border:1px solid var(--rule);border-radius:6px;margin-top:12px}
th,td{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--rule)}
th{font:600 .72rem/1.2 var(--body);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);background:var(--navy-soft)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:0}
.ent{background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:16px 18px;margin-top:14px}
.ent header{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline}
.ent header b{font-family:var(--display);font-size:1.15rem;font-weight:500}
.kind{font-size:.75rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.nav{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:600;color:var(--navy)}
.ent .meta{font-size:.88rem;color:var(--muted);margin:6px 0 0}
.pill{display:inline-block;font-size:.72rem;padding:2px 8px;border-radius:999px;border:1px solid var(--rule);color:var(--muted);margin-right:6px}
.people{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-top:12px}
.person{background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:12px 14px;font-size:.9rem;min-width:0}
.person b{font-family:var(--display);font-size:1.05rem;font-weight:500;display:block}
.person .lt{color:var(--navy);font-variant-numeric:tabular-nums;font-weight:600}
.flags{margin-top:12px}
.flags li{margin:.35em 0}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
</style>
"""

def hold_rows(acc):
    rows = []
    for r in hold_by_acc.get(acc["id"], []):
        q = "" if r["quantity"] is None else (f"{r['quantity']:,.0f}" if float(r["quantity"]) == int(float(r["quantity"])) else f"{r['quantity']:,}")
        px = "" if r["price"] is None else f"{float(r['price']):,.2f}"
        cb = "" if r["cost_basis"] is None else m(r["cost_basis"])
        rows.append(f"<tr><td>{esc(r['security'])}<div class=note>{esc(r['ticker'])} · {esc(r['sub_class'])}{(' · '+esc(r['notes'])) if r['notes'] else ''}</div></td><td>{esc(r['asset_class'])}</td><td class=num>{q}</td><td class=num>{px}</td><td class=num>{m(r['value'])}</td><td class=num>{cb}</td></tr>")
    return "".join(rows)

def entity_card(e):
    eid = e["id"]
    own = "".join(f"<span class=pill>{esc(name(o['owner_id']))} {o['pct']:g}% ({esc(o['interest'])})</span>" for o in owners_of.get(eid, []))
    if eid in bene: own += f"<span class=pill>Beneficiaries: {esc(bene[eid]['description'])}</span>"
    holds = "".join(f"<span class=pill>owns {o['pct']:g}% of {esc(name(o['owned_id']))}</span>" for o in owned_by.get(eid, []))
    out = [f"<section class=ent id={eid}><header><b>{esc(e['name'])}</b><span class=kind>{esc(e['kind'])} · {esc(e['jurisdiction'])} · {e['formed']}</span><span class=nav>{m(e['net_worth_usd'])}</span></header>",
           f"<p class=meta>{esc(e['purpose'])} <em>{esc(e['control'])}.</em>{(' '+esc(e['notes'])) if e['notes'] else ''}</p>",
           f"<div class=meta style='margin-top:8px'>{own}{holds}</div>"]
    for a in acc_by_owner.get(eid, []):
        out.append(f"<h3>{esc(a['custodian'])} — {esc(a['type'])} <span class=kind>{esc(a['account_mask'])} · custody: {esc(a['custody'].replace('_',' '))} · authority: {esc(a['authority'].replace('_',' '))}</span></h3>")
        out.append(f"<div class=tbl><table><thead><tr><th>Holding</th><th>Asset class</th><th class=num>Qty</th><th class=num>Price</th><th class=num>Value</th><th class=num>Cost</th></tr></thead><tbody>{hold_rows(a)}</tbody></table></div>")
    if direct_by_owner.get(eid):
        out.append("<h3>Directly held assets</h3><div class=tbl><table><thead><tr><th>Asset</th><th>Class</th><th class=num>Value</th><th class=num>Cost</th></tr></thead><tbody>")
        out += [f"<tr><td>{esc(x['asset'])}{('<div class=note>'+esc(x['notes'])+'</div>') if x['notes'] else ''}</td><td>{esc(x['asset_class'])} · {esc(x['sub_class'])}</td><td class=num>{m(x['value_usd'])}</td><td class=num>{m(x['cost_basis_usd'])}</td></tr>" for x in direct_by_owner[eid]]
        out.append("</tbody></table></div>")
    if liab_by_owner.get(eid):
        out.append("<h3>Liabilities and commitments</h3><div class=tbl><table><thead><tr><th>Liability</th><th>Kind</th><th>Terms</th><th class=num>Amount</th></tr></thead><tbody>")
        out += [f"<tr><td>{esc(l['liability'])}{('<div class=note>'+esc(l['notes'])+'</div>') if l['notes'] else ''}</td><td>{esc(l['kind'])}</td><td>{esc(l['terms'])}</td><td class=num>{m(l['amount_usd'])}</td></tr>" for l in liab_by_owner[eid]]
        out.append("</tbody></table></div>")
    out.append("</section>")
    return "".join(out)

def person_card(p):
    pid = p["id"]; lt = T["person_lt"].get(pid, 0)
    accs = "".join(f"<div class=note>{esc(a['custodian'])} · {esc(a['type'])} ({esc(a['account_mask'])}): {m(sum(float(r['value']) for r in hold_by_acc.get(a['id'], [])))}</div>" for a in acc_by_owner.get(pid, []))
    dir_ = "".join(f"<div class=note>{esc(x['asset'])}: {m(x['value_usd'])}</div>" for x in direct_by_owner.get(pid, []))
    ownsx = "".join(f"<div class=note>owns {o['pct']:g}% of {esc(name(o['owned_id']))}</div>" for o in owned_by.get(pid, []))
    benes = [esc(E[b['trust_or_charity_id']]['name']) for b in d["beneficial_interests"] if pid in b["beneficiary_ids"]]
    bn = f"<div class=note>Beneficiary of: {', '.join(benes)}</div>" if benes else ""
    return f"<div class=person><b>{esc(p['name'])}</b><span class=note>b. {p['born']} · {esc(p['residence'])}</span><p style='margin:.5em 0'>{esc(p['story'])}</p><div class=lt>Owned directly and through entities: {m(lt)}</div>{ownsx}{accs}{dir_}{bn}</div>"

gens = {1:"Generation 1 — the founder and his wife", 2:"Generation 2 — the four children and their spouses", 3:"Generation 3 — six grandchildren"}
people_html = "".join(f"<h3>{gens[g]}</h3><div class=people>{''.join(person_card(p) for p in d['people'] if p['generation']==g)}</div>" for g in (1,2,3))

groups = [("The family office and the main partnership", ["E1","E2","E3"]),
          ("Dominic's trusts", ["E4","E7","E6","E8","E9","E19"]),
          ("The next generation's own structures", ["E10","E11","E12","E13","E14","E15"]),
          ("Real estate, the vineyard and the art", ["E20","E21","E22","E23","E24","E25","E26"]),
          ("Charitable", ["E5","E16","E17","E18"])]
# E5 is not charitable; put it with Dominic's trusts
groups[1][1].insert(1,"E5"); groups[4][1].remove("E5")
ent_html = "".join(f"<h2>{esc(t)}</h2>" + "".join(entity_card(E[i]) for i in ids) for t,ids in groups)

ac_html = "".join(f"<tr><td>{esc(k)}</td><td class=num>{m(v)}</td><td class=num>{v/ac_total*100:.1f}%</td></tr>" for k,v in ac_rows)

body = f"""{css}
<div class=wrap>
<div class=read>
<div class=eyebrow>Hollisworks demo family · fictional · as of November 2026</div>
<h1>The Olivo Family</h1>
<p class=lede>Four generations, twenty-six entities, thirty-four accounts, and one frozen-lasagna fortune.</p>
<p>Dominic Olivo started importing olive oil out of a Bridgeport warehouse in 1974, moved into frozen Italian specialties when a supermarket buyer asked for a lasagna that "tastes like somebody's grandmother made it", and sold Olivo Foods to Northline Foods in 2014 for $1.1 billion, mostly in Northline stock. He is 75, remarried, and still drives the Ferrari. The money sits in a Delaware partnership run by his eldest son, a dynasty trust the children cannot touch, three real-estate companies, a Napa vineyard that loses a little money every year on purpose, an art company his daughter curates, and a foundation that gives away $2.3 million a year. His youngest son runs a venture fund in San Francisco and keeps his own affairs separate. His youngest child is thirteen.</p>
<p>Every person, entity, account, holding and loan on this page is invented. Tickers are real listed securities with illustrative prices; CUSIPs are masked. The numbers reconcile: each entity's net worth includes what it owns through other entities, and the family total counts every entity once.</p>
</div>
<div class=stats>
<div class=stat><b>{m(T['family_total'])}</b><span>Family net worth, after debt, excluding charities</span></div>
<div class=stat><b>{m(T['charity_total'])}</b><span>Charitable vehicles (foundation, DAF, CRUT)</span></div>
<div class=stat><b>{m(ac_total)}</b><span>Gross assets, all vehicles</span></div>
<div class=stat><b>{m(debt_total)}</b><span>Debt (plus $12.0M of unfunded commitments)</span></div>
<div class=stat><b>{len(d['entities'])} / {len(d['accounts'])}</b><span>Entities / accounts at 9 custodians</span></div>
</div>

<div class=eyebrow>Ownership structure</div>
<div class=read><p>Solid arrows are ownership with the percentage held; dotted arrows are beneficial interests in trusts, which carry no ownership. Each box shows the entity's net worth including what it owns below it.</p></div>
<div class=chart><pre class="mermaid">
{esc(mmd)}
</pre></div>
<div class=legend><span><i style="background:#e9eef4;border-color:#4a5d73"></i>LLC</span><span><i style="background:#dfe7f0;border-color:#2f4a66"></i>Partnership</span><span><i style="background:#f3ecdf;border-color:#8a6d2f"></i>Trust</span><span><i style="background:#e6efe6;border-color:#4b7a4b"></i>Real estate, operating, collectibles</span><span><i style="background:#f1e4ea;border-color:#8a3c5e"></i>Charitable</span></div>

<div class=eyebrow>What the family owns</div>
<div class=tbl><table><thead><tr><th>Asset class</th><th class=num>Gross value</th><th class=num>Share</th></tr></thead><tbody>{ac_html}<tr><td><b>Total gross assets</b></td><td class=num><b>{m(ac_total)}</b></td><td class=num>100%</td></tr></tbody></table></div>

<div class=eyebrow>Why this family is a good test</div>
<div class=read><ul class=flags>
<li><b>A concentrated public position with a loan against it.</b> 2.2 million Northline shares across three vehicles at a $12 basis, $20M borrowed against them. Diversification, gain budgets, gifting and the GRAT all collide here.</li>
<li><b>A blended family.</b> A second spouse with a prenup and a SLAT, a thirteen-year-old half-sister to adults in their forties, and a bypass trust that excludes the second marriage entirely. Visibility rules get exercised.</li>
<li><b>Every custody and authority combination.</b> Managed accounts at Altruist, advised accounts at Goldman and Schwab, read-only IRAs and 529s at Fidelity, assets with no custodian at all.</li>
<li><b>Replication Studio material.</b> A QQQ position to replicate, a direct-indexed S&amp;P sleeve already running, a defined-outcome ETF, two structured notes (one autocallable, one buffered), and an options overlay at Interactive Brokers.</li>
<li><b>Marketplace material.</b> A 2nd Act SPV co-investment, a venture fund where a family member is the GP, angel positions, a sports-team stake, and a private company with a board seat.</li>
<li><b>Operating businesses and real assets.</b> A winery with employees and a bank loan, three industrial buildings on a lease that expires in 2029, a $31M art collection with a credit line against it, ten classic cars, and a jewellery schedule.</li>
<li><b>Charitable plumbing.</b> A private foundation with an excess-business-holdings problem, a DAF, a CRUT paying the founder, and an ILIT paying premiums through Crummey gifts.</li>
</ul></div>

<div class=eyebrow>The people</div>
{people_html}

{ent_html}

<div class=eyebrow>Loading this into Hollisworks</div>
<div class=read><p>The data pack has one CSV per table (people, entities, ownership, beneficial interests, accounts, holdings, direct assets, liabilities) and one JSON file with everything. People map to CRM entities of type person; entities to CRM entities of their kind with the ownership rows as the ownership graph; accounts to households and accounts with the custody and authority values the platform already uses; holdings to positions with cost basis; direct assets and liabilities to CRM records. Mark the whole set <code>is_test = true</code> so the schedulers skip it.</p></div>
</div>
"""
open(f"{OUT}/olivo_family.html","w").write(body)
print(len(body))
