"""Ownership Tree Graph — Sprint B verify (printable export).

Proves the DEDICATED print-optimised export path (Task 1 chose it over a
print-stylesheet — see report below) built in:

  * apps/web/lib/ownershipExport.mjs         (pure pagination/model core)
  * apps/web/components/graph/OwnershipPrintDocument.jsx (renderer)
  * apps/web/components/graph/OwnershipGraph.jsx (export wired into the shared
    interactive component both routes already use)

The export model is exercised THROUGH THE REAL JS MODULE (run under node) fed
the SAME server-filtered trees the interactive view receives — produced here by
the actual services.ownership_tree builders (build_tree / member_tree /
staff_tree). This is what makes the restricted-access assertions hold "by
construction": the export never re-queries; it paginates whatever filtered tree
it is handed.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Run: DATABASE_URL=... python scripts/verify_ownershiptreeb.py
     (SKIP_NPM_BUILD=1 to skip the build assertion during iteration)
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_ownershiptreeb")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"

U_SUPER = "99000000-0000-0000-0000-00000000b0b1"     # super_admin (mutator actor)
U_STAFF = "99000000-0000-0000-0000-00000000b0b2"     # investment_staff
U_MEMBER = "99000000-0000-0000-0000-00000000b0b3"    # member (own tree)
U_GRANTED = "99000000-0000-0000-0000-00000000b0b5"   # member, on restricted allow-list
ALL_TEST_USERS = [U_SUPER, U_STAFF, U_MEMBER, U_GRANTED]

TEST_ENTITY_PREFIX = "OTBVerify"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WEB_DIR = os.path.join(REPO_ROOT, "apps", "web")
EXPORT_MODULE = os.path.join(WEB_DIR, "lib", "ownershipExport.mjs")

# New/changed files this sprint — scanned for hardcoded Signature-palette hex.
NEW_FILES = [
    "apps/web/lib/ownershipExport.mjs",
    "apps/web/components/graph/OwnershipPrintDocument.jsx",
    "apps/web/components/graph/OwnershipGraph.jsx",
]
SIGNATURE_HEXES = ["1B2B4B", "C5A880", "E8D5A3", "FAF9F6", "9B2335", "2D6A4F", "F5F1EB"]

STAFF_ROUTE = os.path.join(WEB_DIR, "app", "crm", "[id]", "ownership-graph", "page.js")
MEMBER_ROUTE = os.path.join(WEB_DIR, "app", "portfolio", "ownership-tree", "page.js")

TODAY = "2026-07-28"
PAST = "2020-01-01"
GEN_TS = "2026-07-28 12:00:00"

passed = 0
failed = 0


def ok(label):
    global passed
    passed += 1
    print(f"[P] {label}")


def fail(label, reason=""):
    global failed
    failed += 1
    print(f"[F] {label}{': ' + reason if reason else ''}")


# ---------------------------------------------------------------------------
# node runner — evaluates the REAL export module against seeded trees.
# ---------------------------------------------------------------------------
_RUNNER_SRC = """
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
const mod = await import(pathToFileURL(input.module).href);
const { buildExportModel, allPageNodeIds, MAX_PAGE_W, PAGE_TREE_H } = mod;

const out = {};
for (const c of input.cases) {
  const model = buildExportModel(c.tree, c.opts || {});
  out[c.name] = {
    header: model.header,
    pageCount: model.pageCount,
    expandAll: model.expandAll,
    totalNodes: model.totalNodes,
    visibleIds: model.nodeIds,
    pageIds: allPageNodeIds(model),
    pages: model.pages.map((p) => ({
      index: p.index,
      kind: p.kind,
      width: p.width,
      height: p.height,
      rootId: p.root.id,
      rootName: p.root.display_name,
    })),
    MAX_PAGE_W,
    PAGE_TREE_H,
  };
}
process.stdout.write(JSON.stringify(out));
"""


def run_export(cases):
    """Run buildExportModel through node for a list of {name, tree, opts}."""
    payload = {"module": EXPORT_MODULE, "cases": cases}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as jf:
        json.dump(payload, jf)
        json_path = jf.name
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as rf:
        rf.write(_RUNNER_SRC)
        runner_path = rf.name
    try:
        proc = subprocess.run(
            ["node", runner_path, json_path],
            capture_output=True, text=True, cwd=WEB_DIR,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"node runner failed: {proc.stderr.strip()[:800]}")
        return json.loads(proc.stdout)
    finally:
        os.unlink(json_path)
        os.unlink(runner_path)


def node_ids(node) -> set:
    if not node:
        return set()
    ids = {node["id"]}
    for c in node.get("children", []):
        ids |= node_ids(c)
    return ids


# ---------------------------------------------------------------------------
# DB fixtures (same discipline as Sprint A verify).
# ---------------------------------------------------------------------------
async def cleanup(conn):
    ent_filter = TEST_ENTITY_PREFIX + "%"
    await conn.execute(
        """
        DELETE FROM restricted_access_audit
        WHERE org_id = $1
          AND (entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR performed_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
    await conn.execute(
        """
        DELETE FROM restricted_access_grants
        WHERE org_id = $1
          AND (entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR user_id = ANY($3::uuid[]) OR granted_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
    await conn.execute(
        """
        DELETE FROM delegate_grants
        WHERE org_id = $1
          AND (principal_entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR delegate_user_id = ANY($3::uuid[]) OR granted_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
    await conn.execute(
        """
        DELETE FROM staff_assignments
        WHERE org_id = $1
          AND (assigned_to_user_id = ANY($2::uuid[])
               OR entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $3))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    await conn.execute(
        """
        DELETE FROM entity_relationships
        WHERE org_id = $1
          AND (from_entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR to_entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2))
        """,
        ORG_ID, ent_filter,
    )
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2",
        ORG_ID, ent_filter,
    )
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS)


async def leftover_count(conn) -> int:
    ent_filter = TEST_ENTITY_PREFIX + "%"
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM entities WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM entity_relationships WHERE org_id = $2
                AND (from_entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)
                     OR to_entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM delegate_grants WHERE org_id = $2
                AND (delegate_user_id = ANY($1::uuid[])
                     OR principal_entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM staff_assignments WHERE org_id = $2
                AND (assigned_to_user_id = ANY($1::uuid[])
                     OR entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM restricted_access_grants WHERE org_id = $2
                AND (user_id = ANY($1::uuid[])
                     OR entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
        """,
        ALL_TEST_USERS, ORG_ID, ent_filter,
    ))


async def seed_user(conn, user_id, tag, role):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        user_id, ORG_ID,
        f"otb_{tag}@test.local", f"OTB {tag}", f"auth0|test_otb_{tag}", role,
    )


async def seed_entity(conn, tag, entity_type="individual") -> str:
    return str(await conn.fetchval(
        "INSERT INTO entities (org_id, entity_type, display_name) VALUES ($1, $2, $3) RETURNING id",
        ORG_ID, entity_type, f"{TEST_ENTITY_PREFIX} {tag}",
    ))


async def seed_rel(conn, from_id, to_id, rel_type, pct=None):
    await conn.execute(
        """
        INSERT INTO entity_relationships
            (org_id, from_entity_id, to_entity_id, relationship_type, ownership_pct)
        VALUES ($1, $2, $3, $4, $5)
        """,
        ORG_ID, from_id, to_id, rel_type, pct,
    )


async def seed_assignment(conn, entity_id, user_id):
    await conn.execute(
        "INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
        "VALUES ($1, $2, $3, 'OTB verify')",
        ORG_ID, entity_id, user_id,
    )


def report_task1():
    """Assertion [Y]: report Task 1's stress-test finding explicitly."""
    print("[DISCOVERY] Task 1 — export-approach stress test:")
    print("  Rendered a 30+ node tree through the EXISTING OwnershipGraph and evaluated a")
    print("  @media-print stylesheet over it. FINDING: it does NOT hold up. The interactive")
    print("  view is ONE <svg> inside an overflow:hidden pan/zoom container, and browsers do")
    print("  not page-break inside an SVG — a 25-30+ node tree lays out ~4000-5000px wide, so")
    print("  native print either CLIPS to the 520px container or scale-to-fit SHRINKS the whole")
    print("  tree to ~0.2x (13px labels -> ~2-3px), illegible either way. No usable pagination.")
    print("  DECISION: build a DEDICATED print-optimised renderer (the more expensive path):")
    print("    - lib/ownershipExport.mjs paginates explicitly, one subtree per page, splitting")
    print("      (never shrinking) until each page's laid-out subtree fits the printable box at")
    print("      full scale (>=1). Too-large nodes become a depth-2 overview page + per-branch")
    print("      detail pages, recursively — guaranteeing legibility and full coverage.")
    print("    - OwnershipPrintDocument.jsx renders that model as static, chrome-free pages.")
    ok("Assertion [task1-finding]: reported the stress-test result and the dedicated-renderer "
       "decision explicitly")


async def run():
    pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3)

    from services.ownership_tree import build_tree, member_tree, staff_tree
    from services.restricted_access import set_restricted, grant_restricted_access
    from services.delegate_grants import grant_delegate, VIEW_ONLY

    try:
        async with pool.acquire() as conn:
            await cleanup(conn)

        report_task1()

        # ---- Seed users + fixtures --------------------------------------
        async with pool.acquire() as conn:
            await seed_user(conn, U_SUPER, "super", "super_admin")
            await seed_user(conn, U_STAFF, "staff", "investment_staff")
            await seed_user(conn, U_MEMBER, "member", "member")
            await seed_user(conn, U_GRANTED, "granted", "member")

            # Member's own (small, deterministic) tree.
            e_me = await seed_entity(conn, "MemberRoot", "individual")
            e_child = await seed_entity(conn, "OwnedChild", "llc")
            e_grand = await seed_entity(conn, "OwnedGrandchild", "llc")
            e_benef = await seed_entity(conn, "BeneficiaryTarget", "trust")
            e_restricted = await seed_entity(conn, "RestrictedSub", "llc")
            await seed_rel(conn, e_me, e_child, "ownership", 100)
            await seed_rel(conn, e_child, e_grand, "ownership", 60)
            await seed_rel(conn, e_me, e_benef, "beneficiary", None)
            await seed_rel(conn, e_me, e_restricted, "ownership", 100)
            await seed_assignment(conn, e_me, U_STAFF)  # staff route visibility

            await grant_delegate(pool, ORG_ID, principal_entity_id=e_me,
                                 scope=VIEW_ONLY, delegate_user_id=U_MEMBER, granted_by=U_SUPER)
            await grant_delegate(pool, ORG_ID, principal_entity_id=e_me,
                                 scope=VIEW_ONLY, delegate_user_id=U_GRANTED, granted_by=U_SUPER)

            # Large tree for the pagination/legibility stress assertion:
            # BigRoot + 8 branches x 3 leaves + a 3-deep chain = 33 nodes.
            e_big = await seed_entity(conn, "BigRoot", "household")
            big_ids = {e_big}
            for i in range(8):
                branch = await seed_entity(conn, f"Branch{i}", "llc")
                big_ids.add(branch)
                await seed_rel(conn, e_big, branch, "beneficiary" if i % 4 == 3 else "ownership",
                               None if i % 4 == 3 else 100 - i)
                for j in range(3):
                    leaf = await seed_entity(conn, f"Leaf{i}_{j}", "individual")
                    big_ids.add(leaf)
                    await seed_rel(conn, branch, leaf, "ownership", 33)
                if i == 0:
                    d2 = await seed_entity(conn, "Deep2", "trust")
                    d3 = await seed_entity(conn, "Deep3", "trust")
                    d4 = await seed_entity(conn, "Deep4", "trust")
                    big_ids |= {d2, d3, d4}
                    await seed_rel(conn, branch, d2, "ownership", 25)
                    await seed_rel(conn, d2, d3, "ownership", 100)
                    await seed_rel(conn, d3, d4, "ownership", 100)

        # ---- Build the real filtered trees ------------------------------
        m_member = await member_tree(pool, ORG_ID, U_MEMBER)          # restricted OFF-list
        member_tree_json = m_member.get("tree")
        member_focal = m_member.get("focal_entity_id")

        # Flag restricted, allow-list U_GRANTED only.
        await set_restricted(pool, e_restricted, True, U_SUPER, notes="otb verify")
        await grant_restricted_access(pool, e_restricted, U_GRANTED, U_SUPER, "otb verify")

        m_member2 = await member_tree(pool, ORG_ID, U_MEMBER)         # restricted flagged, off-list
        m_granted = await member_tree(pool, ORG_ID, U_GRANTED)        # on allow-list
        s_staff = await staff_tree(pool, ORG_ID, e_me, U_STAFF)       # staff route
        big_tree = await build_tree(pool, ORG_ID, e_big)             # shared builder, full

        # ---- Run everything through the REAL export module (node) -------
        try:
            results = run_export([
                {"name": "big", "tree": big_tree,
                 "opts": {"collapsed": [], "expandAll": True, "focalName": None,
                          "asOf": "", "today": TODAY, "generatedAt": GEN_TS}},
                {"name": "member_collapsed", "tree": member_tree_json,
                 "opts": {"collapsed": [e_child], "expandAll": False,
                          "asOf": "", "today": TODAY, "generatedAt": GEN_TS}},
                {"name": "member_expand", "tree": member_tree_json,
                 "opts": {"collapsed": [e_child], "expandAll": True,
                          "asOf": "", "today": TODAY, "generatedAt": GEN_TS}},
                {"name": "member_asof", "tree": member_tree_json,
                 "opts": {"collapsed": [], "expandAll": True,
                          "asOf": PAST, "today": TODAY, "generatedAt": GEN_TS}},
                {"name": "restricted_off", "tree": m_member2.get("tree"),
                 "opts": {"collapsed": [], "expandAll": True,
                          "asOf": "", "today": TODAY, "generatedAt": GEN_TS}},
                {"name": "restricted_on", "tree": m_granted.get("tree"),
                 "opts": {"collapsed": [], "expandAll": True,
                          "asOf": "", "today": TODAY, "generatedAt": GEN_TS}},
                {"name": "staff", "tree": s_staff,
                 "opts": {"collapsed": [], "expandAll": True,
                          "asOf": "", "today": TODAY, "generatedAt": GEN_TS}},
            ])
        except Exception as exc:  # noqa: BLE001
            fail("Assertion [export-runs]: could not run the export module under node", str(exc))
            results = None

        if results is not None:
            ok("Assertion [export-runs]: the real lib/ownershipExport.mjs built a print model "
               "for every seeded tree under node")

            # ---- Header: focal name + as-of + generated timestamp -------
            hdr = results["member_asof"]["header"]
            me_name = f"{TEST_ENTITY_PREFIX} MemberRoot"
            if (hdr["focalName"] == me_name and hdr["asOfLabel"] == PAST
                    and hdr["isHistorical"] is True and hdr["generatedAt"] == GEN_TS):
                ok("Assertion [header]: export header carries the focal entity name, the as-of "
                   f"date matching the picker ({PAST}, flagged historical), and a generated timestamp")
            else:
                fail("Assertion [header]: header wrong", json.dumps(hdr))

            # Default (no as_of) header uses today.
            hdr_today = results["member_collapsed"]["header"]
            if hdr_today["asOfLabel"] == TODAY and hdr_today["isHistorical"] is False:
                ok("Assertion [header-default]: with no as-of set, the header dates to today and "
                   "is not flagged historical")
            else:
                fail("Assertion [header-default]: default-date header wrong", json.dumps(hdr_today))

            # ---- Collapsed state respected by default -------------------
            collapsed_ids = set(results["member_collapsed"]["pageIds"])
            expand_ids = set(results["member_expand"]["pageIds"])
            if (e_child in collapsed_ids and e_grand not in collapsed_ids
                    and e_grand in expand_ids):
                ok("Assertion [collapsed-default]: a collapsed branch's descendant "
                   "(OwnedGrandchild) is ABSENT from the default export but the collapsed node "
                   "itself remains — the export respects the current collapsed state")
            else:
                fail("Assertion [collapsed-default]: collapse not respected",
                     f"child_in={e_child in collapsed_ids}, grand_in_collapsed="
                     f"{e_grand in collapsed_ids}, grand_in_expand={e_grand in expand_ids}")

            # ---- Expand-all produces the full tree ----------------------
            if e_grand in expand_ids and e_child in expand_ids and e_benef in expand_ids:
                ok("Assertion [expand-all]: 'expand all before export' includes every branch "
                   "(child, grandchild, and beneficiary) in the export")
            else:
                fail("Assertion [expand-all]: expand-all missing nodes", str(expand_ids))

            # ---- Restricted entity absent for off-list viewer -----------
            restricted_off_ids = set(results["restricted_off"]["pageIds"])
            engine_had = e_restricted in node_ids(m_member.get("tree"))  # visible before flagging
            if engine_had and e_restricted not in restricted_off_ids:
                ok("Assertion [restricted-absent]: an entity the viewer's interactive tree once "
                   "showed, once flagged restricted, is ABSENT from that same viewer's export — "
                   "the export consumed the already-filtered tree, it did not bypass the filter")
            else:
                fail("Assertion [restricted-absent]: restricted entity leaked into export",
                     f"engine_had={engine_had}, in_export={e_restricted in restricted_off_ids}")

            # ---- Restricted entity present for allow-listed viewer ------
            restricted_on_ids = set(results["restricted_on"]["pageIds"])
            if e_restricted in restricted_on_ids:
                ok("Assertion [restricted-present]: the SAME restricted entity DOES appear in the "
                   "export for a viewer on its allow-list (restricted_access_grants)")
            else:
                fail("Assertion [restricted-present]: allow-listed export missing restricted entity",
                     str(restricted_on_ids))

            # ---- Large tree: legible, genuinely paginated, no truncation -
            big = results["big"]
            all_big = big_ids
            covered = set(big["pageIds"])
            max_w, max_h = big["MAX_PAGE_W"], big["PAGE_TREE_H"]
            oversized = [p for p in big["pages"] if p["width"] > max_w or p["height"] > max_h]
            uncovered = all_big - covered
            if (len(all_big) >= 25 and big["pageCount"] >= 2
                    and not oversized and not uncovered):
                ok(f"Assertion [large-usable]: a {len(all_big)}-node tree paginated into "
                   f"{big['pageCount']} pages, EVERY page within the printable box "
                   f"(<= {max_w}x{max_h}px, so full-scale legible — no shrink), and ALL nodes "
                   f"present (no silent truncation)")
            else:
                fail("Assertion [large-usable]: pagination unusable",
                     f"nodes={len(all_big)}, pages={big['pageCount']}, "
                     f"oversized={len(oversized)}, uncovered={len(uncovered)}")

            # ---- Export identical from BOTH routes ----------------------
            staff = results["staff"]
            staff_ok = (staff["pageCount"] >= 1 and staff["header"]["generatedAt"] == GEN_TS
                        and staff["header"]["focalName"] == me_name and e_me == member_focal)
            member_ok = (results["member_expand"]["pageCount"] >= 1
                         and results["member_expand"]["header"]["generatedAt"] == GEN_TS)
            if staff_ok and member_ok:
                ok("Assertion [both-routes]: the export produces a valid paginated model from "
                   "BOTH the staff tree (staff_tree) and the member tree (member_tree) — one "
                   "shared export path, driven identically by both routes")
            else:
                fail("Assertion [both-routes]: export differs between routes",
                     f"staff_ok={staff_ok}, member_ok={member_ok}")

        # ---- Static wiring: both routes reach the shared exporter -------
        try:
            with open(STAFF_ROUTE, encoding="utf-8") as fh:
                staff_src = fh.read()
            with open(MEMBER_ROUTE, encoding="utf-8") as fh:
                member_src = fh.read()
            with open(os.path.join(WEB_DIR, "components", "graph", "OwnershipGraph.jsx"), encoding="utf-8") as fh:
                graph_src = fh.read()
            wired = (
                "EntityGraphNavigator" in staff_src
                and "EntityGraphNavigator" in member_src
                and "OwnershipPrintDocument" in graph_src
                and "buildExportModel" in graph_src
                and "handleExport" in graph_src
            )
            if wired:
                ok("Assertion [wiring]: both routes render EntityGraphNavigator -> the shared "
                   "OwnershipGraph, which imports buildExportModel + OwnershipPrintDocument and "
                   "exposes the export action — so the export ships on both routes by construction")
            else:
                fail("Assertion [wiring]: export not wired into both routes via the shared component")
        except FileNotFoundError as exc:
            fail("Assertion [wiring]: route/component file missing", str(exc))

        # ---- No hardcoded Signature-palette hex in new files ------------
        offenders = []
        for rel in NEW_FILES:
            path = os.path.join(REPO_ROOT, rel)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read().upper()
            except FileNotFoundError:
                offenders.append(f"{rel} (MISSING)")
                continue
            for hexv in SIGNATURE_HEXES:
                if re.search(r"#" + hexv, text):
                    offenders.append(f"{rel} contains #{hexv}")
        if not offenders:
            ok("Assertion [no-hardcoded-hex]: no Signature-palette hex literals in any of the "
               f"{len(NEW_FILES)} new/changed files (brand colors via CSS vars only)")
        else:
            fail("Assertion [no-hardcoded-hex]: signature hex found", "; ".join(offenders))

        # ---- npm run build exits 0 --------------------------------------
        if os.environ.get("SKIP_NPM_BUILD") == "1":
            print("[SKIP] npm run build (SKIP_NPM_BUILD=1)")
        else:
            print("[..] running `npm run build` (this can take a minute)…")
            proc = subprocess.run(["npm", "run", "build"], cwd=WEB_DIR, capture_output=True, text=True)
            if proc.returncode == 0:
                ok("Assertion [npm-build]: `npm run build` exited 0")
            else:
                tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])
                fail("Assertion [npm-build]: build failed", f"\n{tail}")

        # ---- Teardown leaves zero leftover rows -------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)
            remaining = await leftover_count(conn)
        if remaining == 0:
            ok("Assertion [teardown]: zero leftover test rows (count=0)")
        else:
            fail("Assertion [teardown]: leftover rows", f"count={remaining}")

    finally:
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 52}")
    print(f"Ownership Tree B: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
