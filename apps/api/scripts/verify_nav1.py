"""verify_nav1.py — NAV1: the new menu (phase 1 of the navigation redesign). STATIC: no database, no network, no
model call. Menu presentation only — every page keeps its URL and its gate.

    timeout 600 python3 apps/api/scripts/verify_nav1.py                      # self-checks + node tests + matrices
    timeout 600 python3 apps/api/scripts/verify_nav1.py --self-checks-only    # git-pinned checks only

What it proves
  * BASELINE IS REAL — apps/api/scripts/nav1_baseline.json equals a fresh snapshot of the menu module as it was
    BEFORE NAV1 (the parent of NAV1's first commit), so every "unchanged" check below compares against the real
    pre-NAV1 menu, not against a file the sprint could have edited to agree with itself.
  * NOTHING LOST, NOTHING DOUBLED — every pre-NAV1 href is still in MENU_ITEMS exactly once; the ONLY new href is
    /treasury; Approvals is listed once (the one shared inbox).
  * GATES UNCHANGED — old vs new gate per href: empty diff; every pre-NAV1 GATE_* constant byte-identical.
  * PERSONAS — member, next-gen, support staff, investment staff, member manager, org admin (two shapes), Super
    Admin (two shapes), role-less and a lost envelope: the same set of offered hrefs as before (regrouped), plus
    /treasury exactly when its API's read gate admits them; the /admin area and every area gate unchanged.
  * THE NEW STRUCTURE — sections, groups, labels and tab lists exactly as Joe decided (written out by hand here).
  * MEMBER LABELS — "My accounts" / "My family" for a member, "Cross Book" / "Entities" otherwise.
  * ASSISTANT — no role-based default left; one top-bar toggle; the existing users.assistant_panel_posture store,
    unchanged on the API side.
  * NO ROUTE CHANGED — no file under apps/web/app or apps/api/routers changed in NAV1; (full run) authz1_routes.py
    and visibility1_routes.py --check CURRENT.
  * (full run) the node tests: tests/nav1 (NAV1's own, incl. sidebar render per persona and the assistant
    toggle / remember / render) and every other web test importing the menu module.

Self-checks read every file at NAV1's OWN end point (HEAD until merged, then its branch tip), so a later sprint that
edits the menu cannot turn them red (lesson: verify discovery checks self-contaminate).
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parent.parent
WEB = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402  (stdlib only)

SPRINT_KEY = "nav1"
TREASURY = "/treasury"
MENU = "apps/web/lib/menuVisibility.mjs"

# ── Joe's decisions, by hand (sprint_prompts/nav1.structural.md) ───────────────
WANT_SECTIONS = [["main", None], ["org_admin", "Organization admin"], ["platform", "Platform · Super Admin"]]
WANT_GROUPS = {
    "main": [["portfolio", "Cross Book"], ["model_portfolios", "Model Portfolios"], ["marketplace", "Marketplace"],
             ["notes", "Structured Notes"], ["documents", "Documents"]],
    "org_admin": [["settings", "Firm settings"], ["people", "People & Access"],
                  ["model_admin", "Model Portfolio Admin"], ["data_pricing", "Data & pricing"],
                  ["billing", "Custody & Billing"], ["workflows", "Automation"], ["deal_intake", "Deal Intake"]],
    "platform": [["controls", "Tenants & controls"], ["security_data", "Security data"], ["ai_models", "AI"],
                 ["structured_notes", "Structured notes pipeline"], ["publishing", "Publishing"]],
}
WANT_TABS = {
    "portfolio": ["/portfolio", "/portfolio/positions", "/portfolio/transactions", "/portfolio/securities",
                  "/portfolio/commitments", "/portfolio/households", "/portfolio/allocation",
                  "/portfolio/ownership-tree", "/portfolio/approvals"],
    "model_portfolios": ["/portfolio/models", "/portfolio/replication/catalog", "/portfolio/replication",
                         "/portfolio/replication/tilts", "/portfolio/replication/backtest", "/portfolio/accounts",
                         "/portfolio/tax", "/portfolio/replication/section351", "/portfolio/blocks",
                         "/portfolio/instructions"],
    "notes": ["/portfolio/replication/notes", "/portfolio/replication/notes/new-issues"],
    "marketplace": ["/marketplace", "/marketplace/my-access", "/marketplace/access"],  # unchanged
    "documents": ["/documents", "/documents/semantic"],  # unchanged
    "settings":["/admin/settings", "/admin/disclosures", "/admin/hypothetical-policy", "/admin/modeling/ta"],
    "people": ["/admin/users", "/admin/staff-visibility", "/admin/profiles", "/admin/permission-sets", "/admin/sod"],
    "model_admin": ["/admin/allocation-policy", "/admin/executing-accounts"],
    "data_pricing": ["/admin/security-data/sources", "/admin/security-data/quality"],
    "billing": ["/admin/custodian-capabilities", "/admin/counterparties", "/admin/custody-import",
                "/fee-schedules/chat", "/admin/billing-groups", "/profitability"],
    "workflows": ["/admin/workflows", "/admin/workflows/runs", "/admin/workflows/triggers",
                  "/admin/workflows/templates", "/admin/workflows/autonomy"],
    "deal_intake": ["/admin/vdr-proposals"],
    "controls": ["/admin/platform", "/admin/restricted-access", "/admin/trading-authority",
                 "/admin/integrated-import"],
    "security_data": ["/admin/security-data", "/admin/security-data/master"],
    "ai_models": ["/admin/model-catalog", "/admin/ai-jobs", "/admin/model-research", "/admin/ai-drift"],
    "structured_notes": ["/admin/edgar-pipeline", "/admin/pricing/note-terms-queue", "/admin/note-extraction/gold",
                         "/admin/note-extraction/results", "/admin/pricing/surface",
                         "/portfolio/replication/notes/underlyings"],
    "publishing": ["/admin/model-provider", "/admin/platform/workflow-templates"],
}
WANT_MAIN_ITEMS = [["/dashboard", "Dashboard"], ["/crm", "Entities"], [TREASURY, "Treasury"],
                   ["/spvs", "SPV Manager"], ["/market", "Market Indicators"], ["/notifications", "Notifications"]]
# personas holding view_portfolio / manage_portfolio, or Super Admin (GET /cash/households' read gate)
WANT_TREASURY = sorted(["member", "plain_member", "staff_member", "super_admin", "super_admin_bare", "support_staff"])
WANT_LABELS = {  # persona → (Cross Book group label, entities entry label or None when not visible)
    "member": ("My accounts", None), "plain_member": ("My accounts", None), "next_gen": ("My accounts", None),
    "support_staff": ("Cross Book", "Entities"), "org_admin": ("Cross Book", "Entities"),
    "staff_member": ("Cross Book", "Entities"), "super_admin": ("Cross Book", "Entities"),
}

# The JS that snapshots a module tree (mirrors nav1_snapshot.mjs, plus structure + labels).
PROBE = r"""
import { pathToFileURL } from "node:url";
const root = process.argv[process.argv.length - 1];
const m = await import(pathToFileURL(root + "/apps/web/lib/menuVisibility.mjs").href);
const { snapshot } = await import(pathToFileURL(root + "/apps/api/scripts/nav1_snapshot.mjs").href);
const { PERSONAS } = await import(pathToFileURL(root + "/apps/api/scripts/nav1_personas.mjs").href);
const snap = await snapshot(root + "/apps/web/lib/menuVisibility.mjs");
const out = { snap, sections: m.SECTIONS.map((s) => [s.id, s.header]), groups: {}, tabs: {}, main_items: [],
  labels: {}, display: null };
for (const s of ["main", "org_admin", "platform"]) {
  out.groups[s] = m.GROUPS.filter((g) => g.section === s).sort((a, b) => a.order - b.order).map((g) => [g.id, g.label]);
}
for (const g of m.GROUPS) {
  out.tabs[g.id] = m.MENU_ITEMS.filter((i) => i.group === g.id && !i.comingSoon)
    .sort((a, b) => a.tab_order - b.tab_order).map((i) => i.href);
}
out.main_items = m.MENU_ITEMS.filter((i) => i.section === "main" && !i.group && !i.comingSoon)
  .sort((a, b) => a.order - b.order).map((i) => [i.href, i.label]);
for (const [n, me] of Object.entries(PERSONAS)) {
  const e = m.visibleSectionEntries(me, "main");
  out.labels[n] = [e.find((x) => x.key === "group:portfolio")?.label ?? null, e.find((x) => x.key === "/crm")?.label ?? null];
  out.labels[n + "#empty_groups"] = m.GROUPS.filter((g) => {
    const shown = m.visibleSections(me).some((s) => s.entries.some((x) => x.groupId === g.id));
    return shown !== (m.visibleTabs(me, g.id).length > 0);
  }).map((g) => g.id);
}
if (m.displayLabel) {
  const crm = m.MENU_ITEMS.find((i) => i.href === "/crm");
  out.display = [m.displayLabel(PERSONAS.member, crm), m.displayLabel(PERSONAS.org_admin, crm), m.displayLabel(null, crm)];
}
process.stdout.write(JSON.stringify(out));
"""

NODE_FILES = ["apps/web/lib/menuVisibility.mjs", "apps/api/scripts/nav1_snapshot.mjs",
              "apps/api/scripts/nav1_personas.mjs", "apps/api/scripts/navregroup_personas.mjs"]


def _show(ref: str, path: str) -> str | None:
    code, out, _ = VS.git("show", f"{ref}:{path}")
    return out if code == 0 else None


def _tree_at(ref: str, files: list[str], overrides: dict | None = None) -> pathlib.Path:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="nav1_"))
    for f in files:
        text = (overrides or {}).get(f)
        if text is None:
            text = _show(ref, f)
        if text is None:
            raise RuntimeError(f"{f} not found at {ref}")
        dest = tmp / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    return tmp


def _probe(root: pathlib.Path) -> dict:
    p = subprocess.run(["node", "--input-type=module", "-e", PROBE, str(root)], capture_output=True, text=True,
                       timeout=180, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError(f"node probe failed: {p.stderr[-600:]}")
    return json.loads(p.stdout)


def _nav1_base(end: str) -> str:
    """The parent of NAV1's FIRST own commit reachable from its end point."""
    code, out, _ = VS.git("log", "--reverse", "--format=%h%x00%s", end)
    for line in out.splitlines():
        h, _, s = line.partition("\x00")
        if re.match(r"^nav1(?![\w-])", s):
            return VS.git("rev-parse", "--short", f"{h}^")[1].strip()
    raise RuntimeError("no nav1 commit found")


def self_checks(check, find):
    end, _how = VS.sprint_end_ref(SPRINT_KEY)
    base = _nav1_base(end)
    print(f"[INFO] NAV1 base (pre-NAV1) = {base}; end = {end}")

    # ── baseline is the real pre-NAV1 menu ──
    baseline_text = _show(end, "apps/api/scripts/nav1_baseline.json")
    baseline = json.loads(baseline_text or "{}")
    old_root = _tree_at(end, NODE_FILES, {"apps/web/lib/menuVisibility.mjs": _show(base, MENU)})
    old = _probe(old_root)["snap"]
    check(old == baseline and len(baseline.get("items", [])) == 78,
          "the frozen baseline (nav1_baseline.json) equals a fresh snapshot of the PRE-NAV1 menu module "
          f"({base}:{MENU}; 78 items)", f"items {len(baseline.get('items', []))}")

    new = _probe(_tree_at(end, NODE_FILES))
    snap = new["snap"]
    OLD = {i["href"]: i for i in old["items"]}
    NEW = {i["href"]: i for i in snap["items"]}
    counts: dict = {}
    for i in snap["items"]:
        counts[i["href"]] = counts.get(i["href"], 0) + 1
    check(all(n == 1 for n in counts.values()), "every href appears in the new menu EXACTLY once",
          f"{[h for h, n in counts.items() if n != 1]}")
    lost = sorted(set(OLD) - set(NEW))
    added = sorted(set(NEW) - set(OLD))
    check(not lost, f"nothing lost: all {len(OLD)} pre-NAV1 hrefs are still in the menu", f"lost {lost}")
    check(added == [TREASURY], "the ONLY new href is /treasury", f"added {added}")
    appr = [i for i in snap["items"] if i["href"] == "/portfolio/approvals"]
    check(len(appr) == 1 and appr[0]["group"] == "portfolio", "Approvals: the one shared inbox, listed once (Cross Book)")
    gate_diff = sorted(h for h in OLD if json.dumps(OLD[h]["gate"], sort_keys=True)
                       != json.dumps(NEW.get(h, {}).get("gate"), sort_keys=True))
    check(not gate_diff, "every item keeps its exact gate — old vs new gate per href: EMPTY diff", f"{gate_diff}")
    cs = sorted(h for h in OLD if OLD[h]["comingSoon"] != NEW.get(h, {}).get("comingSoon"))
    check(not cs, "coming-soon flags unchanged (Reporting, Insurance, Community still never listed)", f"{cs}")
    check(NEW.get(TREASURY, {}).get("gate") == {"anyOf": ["view_portfolio", "manage_portfolio"], "strict": True}
          and NEW.get(TREASURY, {}).get("section") == "main",
          "Treasury: a main entry gated on its API's read gate any(view_portfolio | manage_portfolio), strict")

    # every pre-NAV1 GATE_* constant byte-identical
    def gate_defs(text: str) -> dict:
        out = {}
        for m in re.finditer(r"^export const (GATE_\w+) = (.*?);\s*$", text or "", re.M | re.S):
            out[m.group(1)] = re.sub(r"\s+", " ", m.group(2))
        return out
    g_old, g_new = gate_defs(_show(base, MENU)), gate_defs(_show(end, MENU))
    changed = sorted(k for k in g_old if g_old[k] != g_new.get(k))
    check(len(g_old) >= 30 and not changed and sorted(set(g_new) - set(g_old)) == ["GATE_TREASURY"],
          f"all {len(g_old)} pre-NAV1 GATE_* constants byte-identical; the only new one is GATE_TREASURY",
          f"changed {changed}, new {sorted(set(g_new) - set(g_old))}")
    area_old = re.search(r"export const AREA_GATES = \{.*?\};", _show(base, MENU) or "", re.S)
    area_new = re.search(r"export const AREA_GATES = \{.*?\};", _show(end, MENU) or "", re.S)
    check(bool(area_old and area_new and area_old.group(0) == area_new.group(0)), "AREA_GATES unchanged")
    fn_old = re.search(r"export function canAccess\(.*?\n}\n", _show(base, MENU) or "", re.S)
    fn_new = re.search(r"export function canAccess\(.*?\n}\n", _show(end, MENU) or "", re.S)
    check(bool(fn_old and fn_new and fn_old.group(0) == fn_new.group(0)), "canAccess (the gate rule) unchanged")

    # ── personas ──
    bad = []
    for name, before in old["personas"].items():
        after = snap["personas"].get(name)
        if after is None:
            bad.append((name, "missing"))
            continue
        if [h for h in after["hrefs"] if h != TREASURY] != before["hrefs"]:
            bad.append((name, "hrefs", sorted(set(after["hrefs"]) ^ set(before["hrefs"]))))
        if after["admin_area"] != before["admin_area"] or after["areas"] != before["areas"]:
            bad.append((name, "areas"))
    check(not bad and len(old["personas"]) == 12,
          "12 personas (member, next-gen, support staff, investment staff, member manager, org admin x2, Super Admin "
          "x2, role-less, lost envelope): the SAME offered hrefs as before NAV1 (regrouped), /admin area and every "
          "area gate unchanged", f"{bad}")
    tr = sorted(n for n, p in snap["personas"].items() if TREASURY in p["hrefs"])
    check(tr == WANT_TREASURY, "Treasury visible exactly to the personas its API admits (view_portfolio / "
          "manage_portfolio / Super Admin); never the org admin without them, never a lost envelope", f"{tr}")
    empties = {k: v for k, v in new["labels"].items() if k.endswith("#empty_groups") and v}
    check(not empties, "a group is shown iff it has a visible tab for that persona (empty groups hidden)", f"{empties}")
    member_sections = {n: sorted({h.split("/")[1] for h in snap["personas"][n]["hrefs"]})
                       for n in ("member", "plain_member", "next_gen", "lost_envelope")}
    check(all("admin" not in v for v in member_sections.values()),
          "a member (and a lost envelope) sees no admin page at all", f"{member_sections}")

    # ── the new structure, by hand ──
    check(new["sections"] == WANT_SECTIONS, "sections: Main / Organization admin / Platform · Super Admin",
          f"{new['sections']}")
    check(new["groups"] == WANT_GROUPS, "groups, labels, sections and order exactly as decided", f"{new['groups']}")
    tab_bad = {g: new["tabs"].get(g) for g, want in WANT_TABS.items() if new["tabs"].get(g) != want}
    check(not tab_bad and set(new["tabs"]) == set(WANT_TABS), "every group holds exactly its decided tabs, in order",
          f"{tab_bad}")
    check(new["main_items"] == WANT_MAIN_ITEMS, "main standalone entries: Dashboard, Entities, Treasury, SPV Manager, "
          "Market Indicators, Notifications", f"{new['main_items']}")
    lab_bad = {n: new["labels"].get(n) for n, want in WANT_LABELS.items() if tuple(new["labels"].get(n)) != want}
    check(not lab_bad, "labels: a member reads 'My accounts'; staff / org admin / Super Admin read 'Cross Book' and "
          "'Entities' (the CRM gate unchanged, so no member sees the entities entry)", f"{lab_bad}")
    check(new["display"] == ["My family", "Entities", "My family"],
          "the member label rule on the entities entry: member → 'My family', org admin → 'Entities', lost envelope "
          "→ the member label (fail closed)", f"{new['display']}")
    find("the entities entry's 'My family' label is unreachable for a member today: /crm is GATE_CRM (internal roles "
         "only) and NAV1 widens no gate — it shows once a member-visible entities page exists (nav3)")
    find("Treasury's Coverage / Ladder / Payments / Rules / Payees are sections of the one /treasury page, not "
         "routes — one sidebar entry; Security master's Review queue / Tracked view likewise sections of one page")

    # ── assistant ──
    panel = _show(end, "apps/web/components/assistant/AssistantPanel.jsx") or ""
    topbar = _show(end, "apps/web/components/TopBar.jsx") or ""
    shell = _show(end, "apps/web/components/AppShell.jsx") or ""
    pref = _show(end, "apps/web/lib/assistantPanelPref.mjs") or ""
    old_panel = _show(base, "apps/web/components/assistant/AssistantPanel.jsx") or ""
    check("ROLE_POSTURE" in old_panel and "user.assistant_panel_posture" in old_panel,
          "reproduced: before NAV1 the panel defaulted by ROLE (member → expanded) and read the posture off the "
          "session user")
    check("ROLE_POSTURE" not in panel and "user.role" not in panel and "useAssistantPanel" in panel
          and "if (!open) return null" in panel, "after: no role default; the panel renders only while open")
    check(topbar.count("<AssistantToggle />") == 1 and "<AssistantPanel user={user} />" in shell,
          "ONE toggle in the top bar; the shell still mounts the panel")
    check('export const STORAGE_KEY = "assistant-panel-open"' in pref and "try {" in pref and "return false;" in pref,
          "remembered per browser (storage wrapped in try/catch) and closed by default")
    users_old = _show(base, "apps/api/routers/users.py")
    users_new = _show(end, "apps/api/routers/users.py")
    check(users_old == users_new and users_new and "assistant_panel_posture: str | None = None" in users_new,
          "the account preference reuses the EXISTING store (users.assistant_panel_posture via PATCH /users/me); "
          "routers/users.py byte-identical")

    # ── no route changed ──
    code, out, _ = VS.git("diff", "--name-only", f"{base}..{end}", "--", "apps/web/app", "apps/api/routers",
                          "apps/web/proxy.js", "apps/api/main.py")
    check(code == 0 and out.strip() == "", "no route / page / API router file changed in NAV1 (menu presentation only)",
          out.strip()[:300])


def _node_tests() -> tuple[int, int, str]:
    files = sorted({str(p.relative_to(WEB)) for p in (WEB / "tests").rglob("*.test.mjs")
                    if "menuVisibility" in p.read_text(errors="ignore")} | {"tests/nav1/nav1.test.mjs"})
    p = subprocess.run(["node", "--test", *files], cwd=str(WEB), capture_output=True, text=True, timeout=540,
                       stdin=subprocess.DEVNULL)
    mp = re.search(r"^# pass (\d+)", p.stdout, re.M)
    mf = re.search(r"^# fail (\d+)", p.stdout, re.M)
    fails = "\n".join(re.findall(r"^not ok .*$", p.stdout, re.M))[:800]
    return (int(mp.group(1)) if mp else -1, int(mf.group(1)) if mf else -1, f"{len(files)} files; {fails}")


def main() -> int:
    n = {"pass": 0, "fail": 0}

    def check(passed, label, detail=""):
        ok = bool(passed)
        n["pass" if ok else "fail"] += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok or not detail else f" — {detail}"), flush=True)
        return ok

    def find(label, detail=""):
        print(f"[FIND] {label}" + (f" — {detail}" if detail else ""), flush=True)

    print("\nA. self-checks (pinned to NAV1's own end point)")
    try:
        self_checks(check, find)
    except Exception as exc:  # noqa: BLE001
        check(False, "self-checks raised", f"{type(exc).__name__}: {exc}")

    print("\nB. node tests (tests/nav1 + every web test importing the menu module)")
    try:
        p, f, detail = _node_tests()
        check(p > 0 and f == 0, f"node --test: {p} pass, {f} fail", detail)
    except Exception as exc:  # noqa: BLE001
        check(False, "node tests raised", f"{type(exc).__name__}: {exc}")

    print("\nC. route matrices CURRENT (no route changed)")
    py = API_DIR / "venv" / "bin" / "python"
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        try:
            r = subprocess.run([str(py if py.exists() else sys.executable), str(HERE / script), "--check"],
                               cwd=str(REPO), capture_output=True, text=True, timeout=540, stdin=subprocess.DEVNULL)
            check(r.returncode == 0 and "CURRENT" in r.stdout, f"{script} --check: CURRENT",
                  (r.stdout + r.stderr)[-300:])
        except Exception as exc:  # noqa: BLE001
            check(False, f"{script} --check raised", f"{type(exc).__name__}: {exc}")

    print(f"\nTOTAL: {n['pass']} PASS, {n['fail']} FAIL")
    return 0 if n["fail"] == 0 else 1


if __name__ == "__main__":
    if VS.requested():
        sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))
    sys.exit(main())
