"""REP11 Task 1 discovery — read-only. Note terms, readings, underlying links,
issuers, holdings by CUSIP. Never prints a secret."""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()


async def cols(conn, schema, t):
    return [f"{r[0]}:{r[1]}" for r in await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=$1 AND table_name=$2 "
        "ORDER BY ordinal_position", schema, t)]


async def main() -> None:
    dsn, how = await resolve_dsn("DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP")
    conn = await connect(dsn)
    print("[INFO] as", await conn.fetchval("SELECT current_user"), "via", how)
    for s, t in (("portfolio", "securities_global_note_terms"), ("portfolio", "note_terms_field_registry"),
                 ("portfolio", "note_term_readings"), ("portfolio", "structured_note_issuers"),
                 ("portfolio", "reference_filings")):
        print(f"[COLS] {s}.{t}:", await cols(conn, s, t))
    tabs = await conn.fetch("SELECT table_schema, table_name FROM information_schema.tables WHERE table_name ILIKE "
                            "ANY(ARRAY['%underlying%','%note%']) AND table_schema IN ('public','portfolio') ORDER BY 1,2")
    print("[INFO] note/underlying tables:", [f"{r[0]}.{r[1]}" for r in tabs])
    for r in tabs:
        if 'underlying' in r[1]:
            print(f"[COLS] {r[0]}.{r[1]}:", await cols(conn, r[0], r[1]))
            print("   count", await conn.fetchval(f"SELECT count(*) FROM {r[0]}.{r[1]}"))
    T = "portfolio.securities_global_note_terms"
    print("[INFO] all rows / live:", await conn.fetchval(f"SELECT count(*) FROM {T}"),
          await conn.fetchval(f"SELECT count(*) FROM {T} WHERE valid_to IS NULL AND system_to IS NULL"))
    print("[INFO] live per security >1:", await conn.fetchval(
        f"SELECT count(*) FROM (SELECT global_security_id FROM {T} WHERE valid_to IS NULL AND system_to IS NULL "
        "GROUP BY 1 HAVING count(*)>1) x"))
    for c in ("terms_status", "routing_decision", "extraction_confidence", "protection_type", "basket_type",
              "return_basis", "is_decrement_index", "autocall_frequency", "notional_currency", "has_no_call_period"):
        print(f"[DIST] {c}:", [tuple(r) for r in await conn.fetch(
            f"SELECT {c}::text, count(*) FROM {T} WHERE valid_to IS NULL AND system_to IS NULL GROUP BY 1 ORDER BY 2 DESC")])
    print("[DIST] archetype x basket x protection:", [tuple(r) for r in await conn.fetch(
        f"SELECT product_archetype, basket_type, protection_type, terms_status, count(*) FROM {T} "
        "WHERE valid_to IS NULL AND system_to IS NULL GROUP BY 1,2,3,4 ORDER BY 1,2,3,4")])
    print("[SAMPLE] field_status example:", await conn.fetchval(
        f"SELECT field_status::text FROM {T} WHERE valid_to IS NULL AND system_to IS NULL LIMIT 1"))
    for r in await conn.fetch(
            f"SELECT product_archetype, basket_type, protection_type, protection_pct, cap_pct, participation_rate, "
            f"coupon_rate, coupon_barrier_pct, autocall_barrier_pct, tenor_years, initial_valuation_date, "
            f"final_valuation_date, return_basis, is_decrement_index, routing_decision, terms_status "
            f"FROM {T} WHERE valid_to IS NULL AND system_to IS NULL AND product_archetype <> 'autocallable' "
            "ORDER BY product_archetype, basket_type"):
        print("  ROW", dict(r))
    print("[CHECKS]", [tuple(r) for r in await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass", T)])
    print("[IDX]", [r[0] for r in await conn.fetch(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='portfolio' AND tablename='securities_global_note_terms'")])
    print("[POL]", [tuple(r) for r in await conn.fetch(
        "SELECT policyname, cmd FROM pg_policies WHERE schemaname='portfolio' AND tablename='securities_global_note_terms'")])
    print("[TRG]", [r[0] for r in await conn.fetch(
        "SELECT tgname FROM pg_trigger WHERE tgrelid=$1::regclass AND NOT tgisinternal", T)])
    print("[REG] keys:", [(r[0], r[1]) for r in await conn.fetch(
        "SELECT field_key, applies_to_archetypes FROM portfolio.note_terms_field_registry WHERE retired_at IS NULL ORDER BY sort_order")])
    print("[REG] distrib/fee-ish:", [tuple(r) for r in await conn.fetch(
        "SELECT field_key, display_label, value_shape FROM portfolio.note_terms_field_registry WHERE field_key ~* "
        "'(agent|fee|commission|distrib|price|estimated|value|underlying|cusip|issuer|observ|memory|barrier|initial_level|strike|day)'")])
    print("[FS] field_status values:", [tuple(r) for r in await conn.fetch(
        f"SELECT v, count(*) FROM {T}, jsonb_each_text(field_status) AS e(k, v) WHERE valid_to IS NULL GROUP BY 1")])
    print("[COLS] rel:", await cols(conn, "portfolio", "securities_global_relationships"))
    print("[REL] by state/kind:", [tuple(r) for r in await conn.fetch(
        "SELECT r.relationship_type, r.link_state, count(*), count(DISTINCT r.from_global_security_id) FROM "
        f"portfolio.securities_global_relationships r WHERE r.from_global_security_id IN (SELECT global_security_id FROM {T}) GROUP BY 1,2")])
    print("[REL] notes w/ all underlyings resolved:", await conn.fetchval(
        f"SELECT count(*) FROM {T} t WHERE EXISTS (SELECT 1 FROM portfolio.securities_global_relationships r WHERE "
        "r.from_global_security_id=t.global_security_id) AND NOT EXISTS (SELECT 1 FROM portfolio.securities_global_relationships r "
        "WHERE r.from_global_security_id=t.global_security_id AND r.link_state<>'resolved')"))
    print("[REL] resolved target types:", [tuple(r) for r in await conn.fetch(
        "SELECT g.security_type, count(*) FROM portfolio.securities_global_relationships r JOIN portfolio.securities_global g "
        "ON g.id=r.to_global_security_id WHERE r.link_state='resolved' GROUP BY 1")])
    print("[REL] checks:", [tuple(r) for r in await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='portfolio.securities_global_relationships'::regclass AND contype='c'")])
    print("[SEC] note securities:", [tuple(r) for r in await conn.fetch(
        f"SELECT g.security_type, count(*) FROM {T} t JOIN portfolio.securities_global g ON g.id=t.global_security_id GROUP BY 1")])
    print("[COLS] securities_global:", await cols(conn, "portfolio", "securities_global"))
    print("[ID types on note secs]", [tuple(r) for r in await conn.fetch(
        f"SELECT i.id_type, count(*) FROM {T} t JOIN portfolio.securities_global_identifiers i ON "
        "i.global_security_id=t.global_security_id GROUP BY 1")])
    pc = await cols(conn, "portfolio", "positions")
    print("[COLS] positions:", pc)
    print("[HOLD] positions on note-terms securities by test org:", [tuple(r) for r in await conn.fetch(
        f"SELECT o.is_test, count(*), count(DISTINCT p.org_id) FROM portfolio.positions p JOIN organizations o ON o.id=p.org_id "
        f"WHERE p.global_security_id IN (SELECT global_security_id FROM {T}) GROUP BY 1")])
    print("[HOLD] structured_note positions overall:", [tuple(r) for r in await conn.fetch(
        "SELECT o.is_test, count(*) FROM portfolio.positions p JOIN portfolio.securities_global g ON g.id=p.global_security_id "
        "JOIN organizations o ON o.id=p.org_id WHERE g.security_type='structured_note' GROUP BY 1")])
    print("[RF] sample:", {k: v for k, v in dict(await conn.fetchrow(
        f"SELECT r.id, r.cik, r.filer_name, r.form_type, r.accession_number, r.filing_date, r.source_url FROM portfolio.reference_filings r JOIN {T} t ON t.reference_filing_id = r.id LIMIT 1")).items()})
    print("[ISS] cols ok; sample:", dict(await conn.fetchrow("SELECT filer_cik, issuer_group, filer_role, credit_entity FROM portfolio.structured_note_issuers LIMIT 1")))
    print("[ISS] notes with issuer match:", await conn.fetchval(
        f"SELECT count(*) FROM {T} t JOIN portfolio.reference_filings r ON r.id=t.reference_filing_id JOIN portfolio.structured_note_issuers i ON ltrim(i.filer_cik,'0')=ltrim(r.cik,'0')"))
    print("[QUOTE kinds/rows]", await conn.fetchval("SELECT count(*) FROM portfolio.adviser_quotes"),
          await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='adviser_quotes_kind_chk'"))
    await conn.close()

asyncio.run(main())
