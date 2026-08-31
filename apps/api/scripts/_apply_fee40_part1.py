"""fee40 Part 1 — make document_field_corrections usable for fee-schedule specs.

Idempotent. Run:

    python3 scripts/_apply_fee40_part1.py

WHY THIS FILE EXISTS AT ALL
──────────────────────────────────────────────────────────────────────────────
``docs/schema_snapshot.sql`` records columns, primary keys and unique indexes.
It records neither CHECK constraint bodies nor RLS policies. Both of the things
this migration changes are therefore invisible to the snapshot, which means a
fresh environment rebuilt from the snapshot would silently lack them and the
fee-chat correction path would fail on its first write with a constraint
violation naming a constraint nobody could find the definition of.

WHAT IT CHANGES, AND WHY EACH PART IS NEEDED
──────────────────────────────────────────────────────────────────────────────
fee40's Task 1 measured that reusing ``document_field_corrections`` for
fee-schedule corrections — as the sprint brief asked — was blocked by two
deployed constraints. The brief anticipated a NOT NULL ``document_id``; that
was NOT the problem (``document_id`` was already nullable). The real blockers:

1. ``document_field_corrections_target_type_chk`` was a CLOSED allow-list of
   ``('document','note_terms','template_proposal')``. ``'FEE_SCHEDULE_SPEC'``
   was rejected outright.

2. ``document_field_corrections_document_pairing_chk`` forced ``org_id IS NULL``
   for EVERY non-document target. Correct for ``note_terms`` — a 424B2's terms
   are a public fact belonging to no tenant — and wrong for a firm's own fee
   arrangements. Writing them org-NULL would have made them invisible to
   ``services/correction_retrieval.py`` (which filters ``org_id = $1``),
   defeating the entire purpose of recording them.

3. THE TENANT RISK, which was not in the brief. Three RLS policies were written
   as ``target_type <> 'document'`` — an OPEN-ENDED predicate that
   automatically globalises every target type added afterwards. Adding
   FEE_SCHEDULE_SPEC under it would have made one firm's fee negotiations
   readable by every org, with no code change to blame it on. All four global
   policies are narrowed to an explicit allow-list of the genuinely global
   target types, so a new org-scoped type falls under
   ``document_field_corrections_org_isolation`` and nothing else.

Every statement is written to be re-runnable. Verified by
``scripts/verify_fee40.py`` checks [6d], [6g], [8g], [8h] and [8i].
"""

from __future__ import annotations

import asyncio
import glob
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, connect  # noqa: E402

TARGET_TYPE = "FEE_SCHEDULE_SPEC"

#: The target types that are genuinely GLOBAL — no tenant owns them, so they
#: are readable org-blind. Anything not in this list is org-scoped and covered
#: by org_isolation alone.
GLOBAL_TARGET_TYPES = ("note_terms", "template_proposal")

STATEMENTS = [
    # ── 1. admit the new target type ────────────────────────────────────
    """
    ALTER TABLE public.document_field_corrections
      DROP CONSTRAINT IF EXISTS document_field_corrections_target_type_chk
    """,
    """
    ALTER TABLE public.document_field_corrections
      ADD CONSTRAINT document_field_corrections_target_type_chk
      CHECK (target_type = ANY (ARRAY[
        'document'::text, 'note_terms'::text, 'template_proposal'::text,
        'FEE_SCHEDULE_SPEC'::text]))
    """,

    # ── 2. let an org-scoped, non-document target carry its org_id ──────
    #
    # Spelled out per target type rather than as "not a document", so the next
    # type added has to state which side it is on instead of inheriting a
    # default that happens to be wrong for it.
    """
    ALTER TABLE public.document_field_corrections
      DROP CONSTRAINT IF EXISTS document_field_corrections_document_pairing_chk
    """,
    """
    ALTER TABLE public.document_field_corrections
      ADD CONSTRAINT document_field_corrections_document_pairing_chk
      CHECK (
           (target_type = 'document'::text
            AND document_id IS NOT NULL AND org_id IS NOT NULL)
        OR (target_type = ANY (ARRAY['note_terms'::text, 'template_proposal'::text])
            AND org_id IS NULL)
        OR (target_type = 'FEE_SCHEDULE_SPEC'::text
            AND org_id IS NOT NULL AND document_id IS NULL)
      )
    """,

    # ── 3. close the open-ended global carve-out ────────────────────────
    "DROP POLICY IF EXISTS document_field_corrections_global_read "
    "ON public.document_field_corrections",
    """
    CREATE POLICY document_field_corrections_global_read
      ON public.document_field_corrections FOR SELECT
      USING (target_type = ANY (ARRAY['note_terms'::text, 'template_proposal'::text]))
    """,
    "DROP POLICY IF EXISTS document_field_corrections_global_super_admin_insert "
    "ON public.document_field_corrections",
    """
    CREATE POLICY document_field_corrections_global_super_admin_insert
      ON public.document_field_corrections FOR INSERT
      WITH CHECK (target_type = ANY (ARRAY['note_terms'::text, 'template_proposal'::text])
                  AND current_setting('app.is_super_admin', true) = 'true')
    """,
    "DROP POLICY IF EXISTS document_field_corrections_global_super_admin_update "
    "ON public.document_field_corrections",
    """
    CREATE POLICY document_field_corrections_global_super_admin_update
      ON public.document_field_corrections FOR UPDATE
      USING (target_type = ANY (ARRAY['note_terms'::text, 'template_proposal'::text])
             AND current_setting('app.is_super_admin', true) = 'true')
      WITH CHECK (target_type = ANY (ARRAY['note_terms'::text, 'template_proposal'::text])
                  AND current_setting('app.is_super_admin', true) = 'true')
    """,
    "DROP POLICY IF EXISTS document_field_corrections_global_super_admin_delete "
    "ON public.document_field_corrections",
    """
    CREATE POLICY document_field_corrections_global_super_admin_delete
      ON public.document_field_corrections FOR DELETE
      USING (target_type = ANY (ARRAY['note_terms'::text, 'template_proposal'::text])
             AND current_setting('app.is_super_admin', true) = 'true')
    """,
]


async def main() -> int:
    dsn, provenance = await admin_dsn()
    if not dsn:
        print(f"FAIL: no working admin DSN: {provenance}")
        return 1
    print(f"admin: {provenance}")

    conn = await connect(dsn)
    try:
        for statement in STATEMENTS:
            await conn.execute(statement)
        print(f"applied {len(STATEMENTS)} statement(s)")

        # VERIFY it landed. A "success" response is not evidence.
        checks = await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'public.document_field_corrections'::regclass "
            "AND conname LIKE '%chk'")
        defs = {r["conname"]: r["def"] for r in checks}
        ok = all(TARGET_TYPE in d for d in defs.values())
        for name, definition in sorted(defs.items()):
            print(f"  {name}: {definition}")

        policies = await conn.fetch(
            "SELECT polname, pg_get_expr(polqual, polrelid) AS q, "
            "       pg_get_expr(polwithcheck, polrelid) AS w "
            "FROM pg_policy "
            "WHERE polrelid = 'public.document_field_corrections'::regclass "
            "AND polname LIKE '%global%'")
        leaky = [
            p["polname"] for p in policies
            if "<> 'document'" in ((p["q"] or "") + (p["w"] or ""))
        ]
        for p in policies:
            print(f"  {p['polname']}: USING {p['q']} WITH CHECK {p['w']}")

        if not ok:
            print(f"FAIL: {TARGET_TYPE} is missing from a CHECK constraint")
            return 1
        if leaky:
            print(f"FAIL: policies still carry the open-ended carve-out: {leaky}")
            return 1
        print("OK: constraints and policies verified against the live catalog")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
