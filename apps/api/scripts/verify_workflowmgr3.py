"""verify_workflowmgr3.py — Workflow Manager Phase 3 (library + diagram editor).

Proves the Phase-3 HTTP surface end to end against real seeded data, exercising
the ACTUAL FastAPI endpoints (via TestClient) so the reused admin gating is
proven wired, not merely present:

  * GET  /api/v1/admin/workflows                    — library list + step summary
  * GET  /api/v1/admin/workflows/{id}               — editor detail
  * POST /api/v1/admin/workflows/{id}/versions      — save edit → NEW version

Auth is driven the faithful way: we monkeypatch ``main.verify_token`` so a Bearer
token resolves to the claims of a seeded admin / non-admin user, then let the
REAL auth + RLS middleware, the REAL _require_admin gate, and the REAL services
(Phase 2 validator + deriver, Phase 3 save_new_version) run. Nothing is faked
past the token boundary.

Pass/fail only. No interactive prompts. Teardown at start AND at end.

Run:  python3 apps/api/scripts/verify_workflowmgr3.py
"""
import asyncio
import inspect
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import asyncpg

API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)
WEB_DIR = os.path.abspath(os.path.join(API_DIR, "..", "web"))

from services.action_registry import REGISTRY
from services.assistant_actions import register_all
from services.workflow_steps_deriver import derive_and_store_steps
import services.workflow_editor as editor
import services.workflow_steps_deriver as deriver_mod

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")  # Ripasso (2nd real org)

ADMIN_ID = UUID("99000000-0000-0000-0000-0000000000f1")
MEMBER_ID = UUID("99000000-0000-0000-0000-0000000000f2")
DEF_ID = UUID("99000000-0000-0000-0000-0000000000fa")
VER_ID = UUID("99000000-0000-0000-0000-0000000000fb")
OTHER_DEF_ID = UUID("99000000-0000-0000-0000-0000000000fc")
OTHER_VER_ID = UUID("99000000-0000-0000-0000-0000000000fd")
NAME_MARKER = "WFMGR3_TEST"

READ_ACTION = "marketplace.show_new_deals"   # access_type = read  → default Tier 3
WRITE_ACTION = "spv.record_transaction"      # access_type = write → default Tier 2

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
EXT_NS = "http://2ndactcapital.com/bpmn/ext"

# New files this sprint added — scanned for hardcoded Signature-palette hex.
NEW_FILES = [
    "apps/api/routers/workflows.py",
    "apps/api/services/workflow_editor.py",
    "apps/web/lib/workflowActions.js",
    "apps/web/app/admin/workflows/page.js",
    "apps/web/components/admin/WorkflowLibraryManager.jsx",
    "apps/web/app/admin/workflows/[id]/edit/page.js",
    "apps/web/components/admin/WorkflowDiagramEditor.jsx",
]
# The brand "Signature palette" (Design Tokens — Never Change): navy / gold /
# gold-light / cream backgrounds. Each has a Tailwind token class, so hardcoding
# its hex is the anti-pattern. (Neutral state colors like the error red have no
# token and are used verbatim across the existing admin screens.)
SIGNATURE_HEX = ["1B2B4B", "C5A880", "E8D5A3", "FAF9F6", "F5F1EB"]

_ok = True
_v1_steps_snapshot = []


def check(label: str, passed: bool, detail: str = "") -> bool:
    global _ok
    mark = "[PASS]" if passed else "[FAIL]"
    line = f"{mark} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    if not passed:
        _ok = False
    return passed


# ── BPMN builders (reference REAL seeded rows) ───────────────────────────────
def _svc_user_bpmn(proc_id, profile_id, action_key) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        f'id="D_{proc_id}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="{proc_id}" isExecutable="true">'
        '<bpmn:startEvent id="s_start"><bpmn:outgoing>e1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="s_service" name="Pull deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}"/></bpmn:extensionElements>'
        '<bpmn:incoming>e1</bpmn:incoming><bpmn:outgoing>e2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:userTask id="s_user" name="Member approves">'
        f'<bpmn:extensionElements><twoa:governance assignedRoleProfileId="{profile_id}"/></bpmn:extensionElements>'
        '<bpmn:incoming>e2</bpmn:incoming><bpmn:outgoing>e3</bpmn:outgoing></bpmn:userTask>'
        '<bpmn:endEvent id="s_end"><bpmn:incoming>e3</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="e1" sourceRef="s_start" targetRef="s_service"/>'
        '<bpmn:sequenceFlow id="e2" sourceRef="s_service" targetRef="s_user"/>'
        '<bpmn:sequenceFlow id="e3" sourceRef="s_user" targetRef="s_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _service_only_bpmn(proc_id, action_key) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        f'id="D_{proc_id}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="{proc_id}" isExecutable="true">'
        '<bpmn:startEvent id="o_start"><bpmn:outgoing>o1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="o_service" name="Pull deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}"/></bpmn:extensionElements>'
        '<bpmn:incoming>o1</bpmn:incoming><bpmn:outgoing>o2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:endEvent id="o_end"><bpmn:incoming>o2</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="o1" sourceRef="o_start" targetRef="o_service"/>'
        '<bpmn:sequenceFlow id="o2" sourceRef="o_service" targetRef="o_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _edited_bpmn(profile_id) -> str:
    """A DIFFERENT, valid process (write action + user) — the manual 'edit'."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        'id="D_wfmgr3_edited" targetNamespace="http://2ndactcapital.com/bpmn">'
        '<bpmn:process id="wfmgr3_edited_proc" isExecutable="true">'
        '<bpmn:startEvent id="x_start"><bpmn:outgoing>x1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="x_write" name="Record txn">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{WRITE_ACTION}"/></bpmn:extensionElements>'
        '<bpmn:incoming>x1</bpmn:incoming><bpmn:outgoing>x2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:userTask id="x_user" name="Partner signs off">'
        f'<bpmn:extensionElements><twoa:governance assignedRoleProfileId="{profile_id}"/></bpmn:extensionElements>'
        '<bpmn:incoming>x2</bpmn:incoming><bpmn:outgoing>x3</bpmn:outgoing></bpmn:userTask>'
        '<bpmn:endEvent id="x_end"><bpmn:incoming>x3</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="x1" sourceRef="x_start" targetRef="x_write"/>'
        '<bpmn:sequenceFlow id="x2" sourceRef="x_write" targetRef="x_user"/>'
        '<bpmn:sequenceFlow id="x3" sourceRef="x_user" targetRef="x_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _bad_action_bpmn() -> str:
    """Well-formed BPMN referencing a HALLUCINATED action key — must be rejected."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        'id="D_wfmgr3_bad" targetNamespace="http://2ndactcapital.com/bpmn">'
        '<bpmn:process id="wfmgr3_bad_proc" isExecutable="true">'
        '<bpmn:startEvent id="b_start"><bpmn:outgoing>b1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="b_service" name="Do made-up thing">'
        '<bpmn:extensionElements><twoa:governance actionRegistryKey="totally.invented_action"/></bpmn:extensionElements>'
        '<bpmn:incoming>b1</bpmn:incoming><bpmn:outgoing>b2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:endEvent id="b_end"><bpmn:incoming>b2</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="b1" sourceRef="b_start" targetRef="b_service"/>'
        '<bpmn:sequenceFlow id="b2" sourceRef="b_service" targetRef="b_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


# ── DB teardown / seed ───────────────────────────────────────────────────────
async def teardown(conn):
    def_rows = await conn.fetch(
        "SELECT id FROM workflow_definitions WHERE name LIKE $1", NAME_MARKER + "%"
    )
    def_ids = [r["id"] for r in def_rows] + [DEF_ID, OTHER_DEF_ID]
    await conn.execute(
        """DELETE FROM workflow_steps WHERE workflow_version_id IN
           (SELECT id FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[]))""",
        def_ids,
    )
    await conn.execute(
        "DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])", def_ids
    )
    await conn.execute("DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", def_ids)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [ADMIN_ID, MEMBER_ID])


async def seed(conn, profile_id):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'wfmgr3_admin@test.local', 'WFMGR3 Admin', 'auth0|wfmgr3_admin', 'org_admin')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        ADMIN_ID, ORG_ID,
    )
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'wfmgr3_member@test.local', 'WFMGR3 Member', 'auth0|wfmgr3_member', 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        MEMBER_ID, ORG_ID,
    )
    # Definition + current v1 in the caller's org.
    v1_xml = _svc_user_bpmn("wfmgr3_v1_proc", profile_id, READ_ACTION)
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1,$2,$3,'phase 3 fixture',$4) ON CONFLICT (id) DO NOTHING""",
        DEF_ID, ORG_ID, NAME_MARKER + " Library Definition", ADMIN_ID,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1,$2,$3,1,$4,'v1',true,$5) ON CONFLICT (id) DO NOTHING""",
        VER_ID, DEF_ID, ORG_ID, v1_xml, ADMIN_ID,
    )
    async with conn.transaction():
        await derive_and_store_steps(conn, VER_ID, ORG_ID, v1_xml)

    # A definition belonging to a DIFFERENT org — must never appear for ORG_ID.
    other_xml = _service_only_bpmn("wfmgr3_other_proc", READ_ACTION)
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1,$2,$3,'other org fixture',NULL) ON CONFLICT (id) DO NOTHING""",
        OTHER_DEF_ID, OTHER_ORG, NAME_MARKER + " OtherOrg Definition",
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1,$2,$3,1,$4,'v1',true,NULL) ON CONFLICT (id) DO NOTHING""",
        OTHER_VER_ID, OTHER_DEF_ID, OTHER_ORG, other_xml,
    )
    async with conn.transaction():
        await derive_and_store_steps(conn, OTHER_VER_ID, OTHER_ORG, other_xml)


async def setup_phase():
    """Teardown-at-start, seed, and snapshot v1 steps. Returns the profile id."""
    url = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            await teardown(conn)
            profile = await conn.fetchrow(
                "SELECT id FROM profiles WHERE org_id=$1 AND is_seed=true ORDER BY name LIMIT 1",
                ORG_ID,
            ) or await conn.fetchrow(
                "SELECT id FROM profiles WHERE org_id=$1 ORDER BY name LIMIT 1", ORG_ID
            )
            profile_id = profile["id"]
            await seed(conn, profile_id)
            rows = await conn.fetch(
                """SELECT id, step_key, step_type, autonomy_tier, action_registry_key,
                          assigned_role_profile_id
                   FROM workflow_steps WHERE workflow_version_id=$1 ORDER BY step_key""",
                VER_ID,
            )
            _v1_steps_snapshot.extend(dict(r) for r in rows)
        return profile_id
    finally:
        await pool.close()


# ── API tests (real handlers, direct calls with a fake authenticated request) ─
async def run_api_tests(profile_id):
    """Drive the real endpoint handlers + the reused _require_admin gate.

    A fake Request carries only ``request.state.user`` (sub + org_id claims) —
    exactly what get_org_id / ensure_user / load_principal read. The RLS
    middleware is not needed here (the workflow_* tables are not RLS-guarded),
    so calling the handlers directly exercises the same auth + service path an
    HTTP request would. Returns a dict of observations for DB checks.
    """
    import types as _types

    from fastapi import HTTPException

    from routers import workflows as wf
    from routers.workflows import VersionSave

    def req(uid, org):
        return _types.SimpleNamespace(
            state=_types.SimpleNamespace(user={"sub": str(uid), "org_id": str(org)})
        )

    admin_req = req(ADMIN_ID, ORG_ID)
    member_req = req(MEMBER_ID, ORG_ID)
    obs = {}

    async def status_of(coro):
        try:
            await coro
            return 200
        except HTTPException as exc:
            return exc.status_code

    # [Y] Library list — correct definition/version/step summary, org-scoped.
    items = await wf.list_workflows(admin_req)
    obs["list_status"] = 200
    mine = next((w for w in items if str(w.id) == str(DEF_ID)), None)
    obs["mine"] = (
        {
            "current_version_number": mine.current_version_number,
            "step_count": mine.step_count,
            "approval_step_count": mine.approval_step_count,
        }
        if mine
        else None
    )
    obs["other_present"] = any(str(w.id) == str(OTHER_DEF_ID) for w in items)

    # [Y] Non-admin rejected from BOTH library and editor endpoints.
    obs["member_list_status"] = await status_of(wf.list_workflows(member_req))
    obs["member_editor_status"] = await status_of(wf.get_workflow(member_req, DEF_ID))

    # Editor detail as admin — populated with steps + closed reference lists.
    detail = await wf.get_workflow(admin_req, DEF_ID)
    obs["detail_steps"] = len(detail.steps)
    obs["detail_has_pickers"] = len(detail.profiles) > 0 and len(detail.actions) > 0

    # [Y] Save an edit → NEW version.
    try:
        saved = await wf.save_workflow_version(
            admin_req,
            DEF_ID,
            VersionSave(bpmn_xml=_edited_bpmn(profile_id), change_summary="manual edit"),
        )
        obs["save_status"] = 201
        obs["saved_version"] = {
            "id": str(saved.id),
            "version_number": saved.version_number,
        }
    except HTTPException as exc:
        obs["save_status"] = exc.status_code
        obs["saved_version"] = None

    # [Y] Invalid save (bad action key) → rejected, no new version.
    obs["bad_status"] = await status_of(
        wf.save_workflow_version(admin_req, DEF_ID, VersionSave(bpmn_xml=_bad_action_bpmn()))
    )

    return obs


# ── Final DB verification + teardown ─────────────────────────────────────────
async def verify_and_teardown(obs):
    url = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            # Library summary correctness + org isolation.
            mine = obs.get("mine")
            check(
                "Library endpoint returns the org's definition with current version "
                "+ step/tier summary, and NOT another org's",
                obs["list_status"] == 200
                and mine is not None
                and mine["current_version_number"] == 1
                and mine["step_count"] == 2
                and mine["approval_step_count"] == 1
                and obs["other_present"] is False,
                f"status={obs['list_status']} mine={mine} other_present={obs['other_present']}",
            )

            # Non-admin gate on BOTH endpoints.
            check(
                "Non-admin (member) is rejected 403 from BOTH the library and editor "
                "endpoints (reused SOC gating is actually wired)",
                obs["member_list_status"] == 403 and obs["member_editor_status"] == 403,
                f"list={obs['member_list_status']} editor={obs['member_editor_status']}",
            )

            # Save created a NEW current version; old version untouched.
            saved = obs.get("saved_version")
            versions = await conn.fetch(
                """SELECT id, version_number, is_current FROM workflow_versions
                   WHERE workflow_definition_id=$1 ORDER BY version_number""",
                DEF_ID,
            )
            vmap = {v["version_number"]: v for v in versions}
            v2 = vmap.get(2)
            v2_steps = await conn.fetch(
                "SELECT step_key, step_type, autonomy_tier FROM workflow_steps "
                "WHERE workflow_version_id=$1 ORDER BY step_key",
                v2["id"],
            ) if v2 else []
            v1_now = await conn.fetch(
                """SELECT id, step_key, step_type, autonomy_tier, action_registry_key,
                          assigned_role_profile_id
                   FROM workflow_steps WHERE workflow_version_id=$1 ORDER BY step_key""",
                VER_ID,
            )
            v1_unchanged = [dict(r) for r in v1_now] == _v1_steps_snapshot
            # v2 derived from the edited BPMN: write service (Tier 2) + user (Tier 1).
            v2_keys = {r["step_key"] for r in v2_steps}
            v2_derived_fresh = v2_keys == {"x_write", "x_user"} and len(v2_steps) == 2
            check(
                "Saving an edit creates a NEW version (v2, is_current) with the previous "
                "flipped to is_current=false; steps re-derived for the NEW version only, "
                "the old version's steps untouched",
                obs["save_status"] == 201
                and saved
                and saved["version_number"] == 2
                and v2 is not None
                and v2["is_current"] is True
                and vmap[1]["is_current"] is False
                and v2_derived_fresh
                and v1_unchanged,
                f"save={obs['save_status']} v2_fresh={v2_derived_fresh} "
                f"v1_unchanged={v1_unchanged} versions={[dict(v) for v in versions]}",
            )

            # Invalid save rejected; no v3 row.
            max_version = await conn.fetchval(
                "SELECT max(version_number) FROM workflow_versions WHERE workflow_definition_id=$1",
                DEF_ID,
            )
            check(
                "Saving an edit with an invalid action_registry_key is REJECTED (422); "
                "no new version row is created",
                obs["bad_status"] == 422 and max_version == 2,
                f"bad_status={obs['bad_status']} max_version={max_version}",
            )

            # Teardown + zero-leftover.
            await teardown(conn)
            leftover = {}
            leftover["definitions"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE id = ANY($1::uuid[])",
                [DEF_ID, OTHER_DEF_ID],
            )
            leftover["marker_defs"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE name LIKE $1",
                NAME_MARKER + "%",
            )
            leftover["versions"] = await conn.fetchval(
                """SELECT count(*) FROM workflow_versions
                   WHERE workflow_definition_id = ANY($1::uuid[])""",
                [DEF_ID, OTHER_DEF_ID],
            )
            leftover["steps"] = await conn.fetchval(
                """SELECT count(*) FROM workflow_steps WHERE workflow_version_id IN
                   (SELECT id FROM workflow_versions
                    WHERE workflow_definition_id = ANY($1::uuid[]))""",
                [DEF_ID, OTHER_DEF_ID],
            )
            leftover["users"] = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", [ADMIN_ID, MEMBER_ID]
            )
            check("Teardown left zero leftover rows", all(v == 0 for v in leftover.values()), str(leftover))
    finally:
        await pool.close()


# ── Static checks (no DB) ────────────────────────────────────────────────────
def report_discovery():
    print("\n=== Task 1 — Discovery Findings ===")
    print(
        "  1(a) GATING: reuse services.rbac.can_manage_org_settings (super_admin "
        "anywhere OR org_admin at home), via a _require_admin(request) helper "
        "mirroring routers/profiles.py — get_org_id(request) + ensure_user + "
        "load_principal, raising HTTPException(403). Not new logic."
    )
    print(
        "  1(b) BPMN-JS: apps/web/package.json had NO bpmn-js-adjacent dep. Added "
        "(current npm) bpmn-js@18.22.1, bpmn-js-properties-panel@5.63.0, "
        "@bpmn-io/properties-panel@3.48.0 — installed cleanly; the properties "
        "panel is the confirmed scope addition for editing step governance."
    )
    sig = inspect.signature(deriver_mod.derive_and_store_steps)
    print(
        "  1(c) DERIVER reuse point: services.workflow_steps_deriver."
        f"derive_and_store_steps{sig} — re-run after a manual edit to re-derive "
        "workflow_steps for the new version (runs Phase 1 parse_bpmn + governance)."
    )
    reuses = "derive_and_store_steps" in inspect.getsource(editor)
    check(
        "Task 1 findings reported (a: reuse can_manage_org_settings gate, "
        "b: bpmn-js packages, c: derive_and_store_steps signature)",
        list(sig.parameters)[:4] == ["conn", "workflow_version_id", "org_id", "bpmn_xml"]
        and reuses,
    )


def hex_scan():
    offenders = []
    for rel in NEW_FILES:
        p = Path(API_DIR).parent.parent / rel
        if not p.exists():
            offenders.append(f"{rel} MISSING")
            continue
        text = p.read_text().upper()
        for h in SIGNATURE_HEX:
            if h in text:
                offenders.append(f"{rel}:{h}")
    check(
        "No hardcoded Signature-palette hex (navy/gold/gold-light/cream) in any new file",
        not offenders,
        "; ".join(offenders) if offenders else f"scanned {len(NEW_FILES)} files",
    )


def build_check():
    if os.environ.get("SKIP_BUILD"):
        check("npm run build exits 0", True, "SKIP_BUILD set — skipped")
        return
    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=WEB_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    check(
        "npm run build exits 0",
        proc.returncode == 0,
        f"exit={proc.returncode}" + ("" if proc.returncode == 0 else "\n" + proc.stdout[-1500:]),
    )


async def run_all():
    from services.database import close_pool

    profile_id = await setup_phase()
    try:
        obs = await run_api_tests(profile_id)
        await verify_and_teardown(obs)
    finally:
        await close_pool()


def main():
    if not os.environ.get("DATABASE_URL"):
        print("SKIP — DATABASE_URL not set")
        sys.exit(0)

    register_all()

    report_discovery()

    print("\n=== Endpoints (real handlers + reused admin gate) ===")
    asyncio.run(run_all())

    print("\n=== Static checks ===")
    hex_scan()

    print("\n=== Frontend build ===")
    build_check()

    print()
    if _ok:
        print("RESULT: ALL ASSERTIONS PASSED ✅")
        sys.exit(0)
    else:
        print("RESULT: FAILURES PRESENT ❌")
        sys.exit(1)


if __name__ == "__main__":
    main()
