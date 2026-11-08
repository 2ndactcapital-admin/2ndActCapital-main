"""WFTEMPLATES1 — load the shipped platform workflow templates (docs/workflow_templates/) as DRAFTS.

    python3 apps/api/scripts/load_workflow_templates.py --dry-run
        Offline: no database, no network. Reads every <key>.bpmn + <key>.md, validates each through THE shared
        validator (services.workflow_nl_generator._validate, every placeholder at its default) and the Wave-2
        policy, and prints each step with its computed tier. Exit 1 if any template fails.

    doppler run -- python3 apps/api/scripts/load_workflow_templates.py --real --drafter <super-admin users.id>
        Drafts each template as a NEW draft version in the REAL Hollisworks platform org, as that Super Admin
        (services.workflow_templates.draft_platform_template — the same path as the platform screen). Idempotent:
        a template whose latest live version (draft or published) already has the same BPMN checksum is skipped.
        A DIFFERENT Super Admin then publishes each draft on Platform → Workflow templates.

The sprint runs only --dry-run. Never prints a secret.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(1, str(_site))

TEMPLATE_DIR = REPO / "docs" / "workflow_templates"
PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"


def _registry():
    from services.assistant_actions import register_all

    register_all()


def dry_run(directory=TEMPLATE_DIR) -> tuple[int, int, list[dict]]:
    """Validate every shipped template offline. Returns (ok, failed, results)."""
    from services import workflow_templates as W

    _registry()
    ok = failed = 0
    results = []
    for key in W.shipped_keys(directory):
        try:
            t = W.read_template_file(directory, key)
            W._content_checks(t["key"], t["title"], t["category"], t["description"])
            params = W.parse_parameters(t["parameters"])
            steps = W.validate_template(t["bpmn_xml"], params, profile_ids=set(), platform=True)
            ok += 1
            results.append({"key": key, "ok": True, "steps": steps, "parameters": params, "category": t["category"]})
            print(f"[OK]   {key}  ({W.category_label(t['category'])}; {len(steps)} steps, {len(params)} parameters)")
            for s in steps:
                verb = s["action_registry_key"] or "—"
                print(f"         {s['step_key']:<10} {s['step_type']:<8} {verb:<38} {s['label']}")
        except Exception as exc:  # noqa: BLE001 — every failure is reported, the run continues
            failed += 1
            errs = getattr(exc, "errors", None) or [str(exc)]
            results.append({"key": key, "ok": False, "errors": errs})
            print(f"[FAIL] {key}: " + "; ".join(errs))
    print(f"\nDRY RUN: {ok} template(s) validated, {failed} failed (nothing was written)")
    return ok, failed, results


async def load_real(drafter: str) -> int:
    from _db_connect import admin_dsn, hydrate

    hydrate()
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no DATABASE_URL: {how}")
        return 1
    os.environ["DATABASE_URL"] = dsn
    from services import workflow_templates as W
    from services.database import get_pool, reset_rls_context, set_rls_context

    _registry()
    tokens = set_rls_context(PLATFORM_ORG_ID, True)
    try:
        pool = await get_pool()
        drafted = skipped = failed = 0
        for key in W.shipped_keys(TEMPLATE_DIR):
            t = W.read_template_file(TEMPLATE_DIR, key)
            async with pool.acquire() as conn:
                latest = await conn.fetchrow(
                    f"SELECT v.bpmn_sha256, v.status, v.version_no FROM {W.T_VERSIONS} v JOIN {W.T_TEMPLATES} tt ON "
                    "tt.id = v.template_id WHERE tt.owner_org_id = $1::uuid AND tt.template_key = $2 AND "
                    "v.system_to IS NULL AND v.status IN ('draft', 'published') ORDER BY v.version_no DESC LIMIT 1",
                    PLATFORM_ORG_ID, key)
            if latest is not None and latest["bpmn_sha256"] == W.sha256(t["bpmn_xml"]):
                skipped += 1
                print(f"[SKIP] {key}: version {latest['version_no']} ({latest['status']}) already has this BPMN")
                continue
            try:
                v = await W.draft_platform_template(pool, org_id=PLATFORM_ORG_ID, actor_id=drafter, key=key,
                                                    title=t["title"], category=t["category"],
                                                    description=t["description"], bpmn_xml=t["bpmn_xml"],
                                                    parameters=t["parameters"])
                drafted += 1
                print(f"[DRAFT] {key}: version {v['version_no']} drafted — a second Super Admin publishes it")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[FAIL] {key}: {exc} {getattr(exc, 'errors', '')}")
        print(f"\nLOADED: {drafted} drafted, {skipped} unchanged, {failed} failed")
        return 1 if failed else 0
    finally:
        reset_rls_context(tokens)


def main() -> int:
    args = sys.argv[1:]
    if "--real" in args:
        if "--drafter" not in args or args.index("--drafter") + 1 >= len(args):
            print("--real needs --drafter <the drafting Super Admin's users.id>")
            return 2
        return asyncio.run(load_real(args[args.index("--drafter") + 1]))
    if "--dry-run" in args or not args:
        os.environ.setdefault("DATABASE_URL", "postgresql://dry-run.invalid/none")  # imported, never connected
        _, failed, _ = dry_run()
        return 1 if failed else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
