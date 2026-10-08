"""verify_notefields.py — NOTE FIELDS v3: the registry as the schema, the three
structured lists, rules / derivations / self-checks, and the two model guardrails.

WRITTEN BY THE SPRINT, RUN BY THE OPERATOR:

    python3 apps/api/scripts/verify_notefields.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on
any failure. Hydrates secrets from Doppler over HTTPS at startup
(_db_bootstrap). EVERY model call is mocked: the readers' transport
(proxy.chat) and the AI router's client (extraction._build_text_ai_client) are
replaced before any section runs, so real spend is $0. The one network read is
the proxy's GET /model/info (Task 1d: which catalog entries have no price) —
an admin read, not a model call.

FIXTURES (all tagged, all torn down at start AND end, counted to zero):
  * note_extraction_runs with config->>'verify_tag' = TAG (run_kind 'verify')
    and their readings + staging rows (two cascade outcomes persisted against
    two REAL reference filings — the rows carry the verify run id, the filings
    are only FK targets and are never written);
  * one platform_model_catalog row, model_id FIXTURE_MODEL (the unpriced model);
  * one distribution_participants row, canonical_name FIXTURE_PARTICIPANT.
Every fixture value is checked against the table's live CHECK constraints
(pg_constraint, contype read ::text) before it is written. No unique value is
derived from a slice of a UUID.

RLS: the connection is app_service (rolbypassrls asserted false). Every read of
an RLS table goes through platform_scope (SET LOCAL app.is_super_admin inside a
transaction) — a read without it would return zero rows silently.

Each [Y] section is wrapped: an exception is a [FAIL] naming the section, never
a crash; teardown always runs.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
DOC = REPO / "docs" / "NOTE_FIELDS.md"

TAG = "notefields-verify"
FIXTURE_MODEL = "nfverify-unpriced-model"
FIXTURE_PROVIDER = "nfverify"
FIXTURE_PARTICIPANT = "NOTEFIELDS VERIFY FIXTURE DEALER LLC"
UNKNOWN_PARTICIPANT = "NOTEFIELDS VERIFY UNLISTED PLACEMENT AGENT LLC"
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"
M1, M2 = "nfverify-reader-one", "nfverify-reader-two"

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} ({type(passed).__name__}), not a bool, for {label!r}")
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ═══ RLS-scoped DB helpers ═════════════════════════════════════════════════
async def q(conn, sql, *args):
    from services.database import platform_scope
    async with platform_scope(conn):
        return await conn.fetch(sql, *args)


async def qval(conn, sql, *args):
    from services.database import platform_scope
    async with platform_scope(conn):
        return await conn.fetchval(sql, *args)


async def check_values(conn, table: str) -> dict[str, str]:
    """{constraint name: definition} of the table's CHECK constraints, live."""
    rows = await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = $1::regclass AND contype::text = 'c'", table)
    return {r["conname"]: r["def"] for r in rows}


def allowed(defs: dict[str, str], column: str) -> set[str]:
    """The literal values a `column = ANY (ARRAY[...])` CHECK allows."""
    out: set[str] = set()
    for d in defs.values():
        if re.search(rf"\(\(?{column}\b", d) and "ARRAY[" in d:
            out |= set(re.findall(r"'([^']+)'::text", d))
    return out


# ═══ Teardown — by fixture tag, with a before/after count as the backstop ══
async def fixture_counts(conn) -> dict[str, int]:
    runs = "SELECT id FROM portfolio.note_extraction_runs WHERE config->>'verify_tag' = $1"
    return {
        "runs": await qval(conn, f"SELECT count(*) FROM ({runs}) r", TAG),
        "readings": await qval(conn, f"SELECT count(*) FROM portfolio.note_term_readings WHERE run_id IN ({runs})", TAG),
        "staging": await qval(conn, f"SELECT count(*) FROM portfolio.note_extraction_staging WHERE run_id IN ({runs})", TAG),
        "catalog": await qval(conn, "SELECT count(*) FROM platform_model_catalog WHERE model_id = $1", FIXTURE_MODEL),
        "participants": await qval(conn, "SELECT count(*) FROM portfolio.distribution_participants "
                                         "WHERE lower(canonical_name) = lower($1)", FIXTURE_PARTICIPANT),
    }


async def teardown(conn) -> None:
    from services.database import platform_scope
    runs = "SELECT id FROM portfolio.note_extraction_runs WHERE config->>'verify_tag' = $1"
    async with platform_scope(conn):
        await conn.execute(f"DELETE FROM portfolio.note_extraction_staging WHERE run_id IN ({runs})", TAG)
        await conn.execute(f"DELETE FROM portfolio.note_term_readings WHERE run_id IN ({runs})", TAG)
        await conn.execute(f"DELETE FROM portfolio.note_extraction_runs WHERE config->>'verify_tag' = $1", TAG)
    async with platform_scope(conn):
        await conn.execute("DELETE FROM platform_model_catalog WHERE model_id = $1", FIXTURE_MODEL)
        await conn.execute("DELETE FROM portfolio.distribution_participants WHERE lower(canonical_name) = lower($1)",
                           FIXTURE_PARTICIPANT)


# ═══ Mocks — installed before any section; real spend stays $0 ═════════════
CHAT_BODIES: list[dict] = []
READER_ANSWER: dict = {}


def install_mocks():
    from services import extraction
    from services.note_extraction import proxy

    async def fake_chat(body, timeout=None):
        CHAT_BODIES.append(body)
        content = json.dumps(READER_ANSWER)
        return proxy.ProxyResponse(
            200, {"model": body["model"], "choices": [{"message": {"content": content}}],
                  "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            content, {"x-litellm-attempted-fallbacks": "0", "x-litellm-response-cost": "0.0"})

    async def fake_jev(body, timeout=None):
        raise AssertionError("Jev must not be called by this verify")

    proxy.chat = fake_chat
    proxy.jev = fake_jev

    class _Usage:
        input_tokens, output_tokens = 10, 5

    class _Msg:
        usage = _Usage()
        content = []

    class _Messages:
        def __init__(self):
            self.models: list[str] = []

        async def create(self, **kw):
            self.models.append(kw.get("model"))
            return _Msg()

    class _Client:
        def __init__(self):
            self.messages = _Messages()

    client = _Client()

    async def fake_build():
        return client, extraction.TRANSPORT_ANTHROPIC, "", None, False

    logged: list[dict] = []

    async def fake_log(**kw):
        logged.append(kw)

    extraction._build_text_ai_client = fake_build
    extraction._safe_log = fake_log
    return client, logged


def fake_catalog(names_priced: dict[str, bool]):
    from services.note_extraction import proxy
    out = {}
    for n, priced in names_priced.items():
        out[n] = proxy.Deployment(n, None, n, None, 1e-7 if priced else None, 4e-7 if priced else None,
                                  None, True, None)
    return out


# ═══ Fixture filing (rendered by documents.document_from_html) ═════════════
def fixture_html(consistent: bool) -> str:
    proceeds = "$975.00" if consistent else "$960.00"
    ev = "$961.46" if consistent else "$1,010.00"
    initial = "March 27, 2026" if consistent else "March 25, 2026"
    row30 = "$850.00" if consistent else "$700.00"
    obs = "September 28, 2026" if consistent else "June 30, 2027"
    return f"""<html><body>
<p>Observation Date: {obs}</p>
<p>Pricing Date: March 27, 2026</p>
<p>Initial Valuation Date: {initial}</p>
<p>Maturity Date: March 29, 2027</p>
<p>Buffer Amount: 15%</p>
<p>The estimated value of the notes on the pricing date is {ev} per $1,000 principal amount.</p>
<p>Per Note</p><p>$1,000.00</p><p>$25.00</p><p>{proceeds}</p>
<h2>Hypothetical Payments at Maturity</h2>
<p>-10.00% $1,000.00</p>
<p>-30.00% {row30}</p>
<p>20.00% $1,200.00</p>
<h2>Supplemental Plan of Distribution</h2>
<p>{FIXTURE_PARTICIPANT} will act as dealer. {UNKNOWN_PARTICIPANT} will act as placement agent.</p>
</body></html>"""


def reader_answer(rspecs, consistent: bool) -> dict:
    a = {s.key: {"value": None, "quote": None} for s in rspecs}
    a["pricing_date"] = {"value": "2026-03-27", "quote": "Pricing Date: March 27, 2026"}
    a["maturity_date"] = {"value": "2027-03-29", "quote": "Maturity Date: March 29, 2027"}
    a["protection_type"] = {"value": "buffer", "quote": "Buffer Amount: 15%"}
    a["buffer_pct"] = {"value": 15, "quote": "Buffer Amount: 15%"}
    a["coupon_type"] = {"value": "none", "quote": "Buffer Amount: 15%"}
    a["call_type"] = {"value": "none", "quote": "Buffer Amount: 15%"}
    a["price_to_public_pct"] = {"value": 100, "quote": "$1,000.00"}
    a["total_fees_pct"] = {"value": 2.5, "quote": "$25.00"}
    a["proceeds_to_issuer_pct"] = {"value": 97.5 if consistent else 96.0,
                                   "quote": "$975.00" if consistent else "$960.00"}
    ev = 961.46 if consistent else 1010.0
    a["estimated_value_per_1000"] = {"value": {"min": ev, "max": ev, "bound": "exact"},
                                     "quote": f"is ${ev:,.2f} per $1,000 principal amount"}
    sched_date = "2026-09-28" if consistent else "2027-06-30"
    a["observation_schedule"] = {"value": [{"observation_date": sched_date, "payment_date": None,
                                            "coupon_barrier_pct": None, "coupon_amount": None,
                                            "call_level_pct": None, "call_amount": None}],
                                 "quote": "Observation Date: " + ("September 28, 2026" if consistent else "June 30, 2027")}
    a["distribution"] = {"value": [
        {"name": FIXTURE_PARTICIPANT, "role": "dealer", "fee_min_pct": None, "fee_max_pct": None},
        {"name": UNKNOWN_PARTICIPANT, "role": "placement_agent", "fee_min_pct": None, "fee_max_pct": None},
    ], "quote": f"{FIXTURE_PARTICIPANT} will act as dealer. {UNKNOWN_PARTICIPANT} will act as placement agent."}
    return a


# ═══ Doc parsing (the approved field list) ═════════════════════════════════
def parse_doc() -> dict:
    """{'top': {key: starred}, 'members': {list_key: {member: starred}}, 'rules_only_phrases': n}."""
    text = DOC.read_text(encoding="utf-8")
    top: dict[str, bool] = {}
    members: dict[str, dict[str, bool]] = {"underlyings": {}, "distribution": {}}
    sec = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\d)\s", line)
        if m:
            sec = int(m.group(1))
            continue
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| Field"):
            continue
        cell = line.split("|")[1]
        starred = "★" in cell
        keys = re.findall(r"`([a-z0-9_]+)`", cell)
        if not keys:
            if "observation schedule" in cell.lower():
                top["observation_schedule"] = starred
            continue
        on_note = "(on the note)" in cell
        if sec in (3, 8) and not on_note:
            lk = "underlyings" if sec == 3 else "distribution"
            for k in keys:
                members[lk][k] = starred
            continue
        for k in keys:
            top[k] = starred
    for lk, mem in members.items():
        top[lk] = any(mem.values())
    s9 = text.split("## 9", 1)[1].split("##", 1)[0] if "## 9" in text else ""
    return {"top": top, "members": members, "section9": s9}


# ═══ Sections ══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — the four discovery findings")
    import subprocess
    from services.note_extraction import proxy

    # 1a — every reader of the registry / B1's extension list
    proc = subprocess.run(
        ["grep", "-rlE", r"load_registry_rows\(|build_field_specs\(|load_specs\(|note_terms_field_registry",
         "--include=*.py", "services", "routers", "scripts"], cwd=API_DIR, capture_output=True, text=True)
    users = sorted(p for p in proc.stdout.split() if "verify_" not in p)
    find("1a registry consumers", ", ".join(users))
    must = {"services/note_extraction/schema.py", "routers/note_extraction_admin.py",
            "scripts/_note_extraction_common.py", "services/edgar_inventory.py"}
    check(must <= set(users), "1a: the schema builder, gold screen, harness and inventory all read the registry",
          f"missing {sorted(must - set(users))}")
    schema_src = (API_DIR / "services/note_extraction/schema.py").read_text()
    check("_EXTENSIONS" not in schema_src and "_REGISTRY_OVERRIDES" not in schema_src,
          "1a: B1's hard-coded extension list and override table are gone from schema.py")

    # 1b — how values are stored
    rows = await conn.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='portfolio' "
        "AND ((table_name='note_term_readings' AND column_name='value') "
        "OR (table_name='note_extraction_staged_fields' AND column_name='resolved_value') "
        "OR (table_name='note_gold_values' AND column_name='value'))")
    types = {f"{r['table_name']}.{r['column_name']}": r["data_type"] for r in rows}
    find("1b value storage", json.dumps(types))
    check(len(types) == 3 and set(types.values()) == {"jsonb"},
          "1b: readings / staged / gold values are already jsonb — lists and ranges fit with no new column")

    # 1c — rows on renamed / retired keys
    retired = await q(conn, "SELECT field_key, replaced_by, replacement_rule FROM portfolio.note_terms_field_registry "
                            "WHERE retired_at IS NOT NULL ORDER BY field_key")
    rkeys = [r["field_key"] for r in retired]
    per = {}
    for t, col in (("note_term_readings", "field_key"), ("note_extraction_staged_fields", "field_key"),
                   ("note_gold_values", "field_key")):
        for r in await q(conn, f"SELECT {col} AS k, count(*) AS n FROM portfolio.{t} WHERE {col} = ANY($1::text[]) "
                               f"GROUP BY 1 ORDER BY 1", rkeys):
            per[f"{t}.{r['k']}"] = r["n"]
    legacy = await qval(conn, "SELECT count(*) FROM portfolio.note_term_readings WHERE origin = 'migrated_correction'")
    find("1c rows on retired keys (kept, mapped)", json.dumps(per) + f"; migrated legacy readings in total: {legacy}")
    check(len(rkeys) > 0, "1c: retired keys exist and were counted", f"{len(rkeys)} retired keys")

    # 1d — catalog entries with no known price on the proxy
    try:
        cat = await asyncio.to_thread(proxy.deployment_catalog)
    except Exception as exc:  # noqa: BLE001
        check(False, "1d: the proxy's /model/info could be read", f"{type(exc).__name__}: {exc}")
        return
    from services.note_extraction.spend import has_price
    models = [r["model_id"] for r in await q(conn, "SELECT model_id FROM platform_model_catalog "
                                                   "WHERE model_id <> $1 ORDER BY model_id", FIXTURE_MODEL)]
    unpriced = [m for m in models if not has_price(cat.get(m))]
    find("1d catalog entries with no proxy price", ", ".join(unpriced) or "none")
    check(len(models) > 0, "1d: every catalog entry was checked against the proxy's price list",
          f"{len(models)} entries, {len(unpriced)} without a price")


async def y_registry(conn, specs):
    section("[Y] Every field in docs/NOTE_FIELDS.md is in the registry; critical = ★")
    from services.note_extraction import schema
    d = parse_doc()
    top = d["top"]
    check(len(top) >= 40, "the document parses into its field list", f"{len(top)} fields")
    rows = {r["field_key"]: r for r in await q(conn, schema.REGISTRY_SELECT + " WHERE retired_at IS NULL")}
    missing = sorted(k for k in top if k not in rows)
    check(not missing, "every document field is a LIVE registry row", f"missing {missing}")
    incomplete = sorted(k for k in top if k in rows and not (rows[k]["section"] and rows[k]["description"]
                                                             and rows[k]["value_shape"]
                                                             and rows[k]["is_critical"] is not None))
    check(not incomplete, "each carries section, is_critical, value_shape and description", f"{incomplete}")
    starred = {k for k, s in top.items() if s}
    reg_crit = {k for k, r in rows.items() if r["is_critical"]}
    check(len(starred) > 0 and starred == reg_crit, "the registry's critical set equals the ★ set",
          f"★ only {sorted(starred - reg_crit)}; registry only {sorted(reg_crit - starred)}")
    for lk, mem in d["members"].items():
        model_fields = set(schema.LIST_MEMBER_MODELS[lk].model_fields)
        want = set()
        for m in mem:
            want |= {"fee_min_pct", "fee_max_pct"} if m == "fee_pct" else {m}
        check(len(want) > 0 and want <= model_fields, f"every '{lk}' member in the document is a field of its "
              f"Pydantic model", f"missing {sorted(want - model_fields)}")
    shapes = {k: rows[k]["value_shape"] for k in ("underlyings", "observation_schedule", "distribution")}
    check(set(shapes.values()) == {"list"}, "the three child tables are value_shape 'list'", json.dumps(shapes))
    rules_only = [k for k, r in rows.items() if r["section"] == "rules_only"]
    check(len(rules_only) >= 9 and all(rows[k]["extraction_method"] == "rules" for k in rules_only),
          "section 9 fields are extraction_method 'rules'", f"{sorted(rules_only)}")
    check(rows["tenor_months"]["extraction_method"] == "derived"
          and rows["max_principal_loss_pct"]["extraction_method"] == "derived",
          "tenor and max loss are extraction_method 'derived'")
    ranges = sorted(k for k, r in rows.items() if r["value_shape"] == "range")
    check({"agent_commission_pct", "fee_based_account_price", "estimated_value_per_1000"} <= set(ranges),
          "the range-valued economics are value_shape 'range' (decision C)", f"{ranges}")


async def y_orphans(conn):
    section("[Y] No field_key is orphaned; migrated counts")
    keys = {r["field_key"]: r for r in await q(
        conn, "SELECT field_key, retired_at, replaced_by, replacement_rule FROM portfolio.note_terms_field_registry")}
    total = 0
    for t in ("note_term_readings", "note_extraction_staged_fields", "note_gold_values"):
        used = await q(conn, f"SELECT field_key, count(*) AS n FROM portfolio.{t} WHERE field_key <> '__call__' "
                             f"GROUP BY 1")
        total += sum(r["n"] for r in used)
        orphans = sorted(r["field_key"] for r in used if r["field_key"] not in keys)
        unmapped = sorted(r["field_key"] for r in used if r["field_key"] in keys
                          and keys[r["field_key"]]["retired_at"] is not None
                          and not keys[r["field_key"]]["replacement_rule"])
        check(not orphans and not unmapped, f"{t}: every field_key is a live key or a retired key with a mapping",
              f"orphans {orphans}, retired-without-mapping {unmapped}")
    check(total > 0, "the orphan check saw real rows (not vacuous)", f"{total} rows")
    renames = [k for k, r in keys.items() if r["retired_at"] is not None
               and (r["replacement_rule"] or "").startswith("renamed")]
    left = await qval(conn, "SELECT count(*) FROM portfolio.note_term_readings WHERE field_key = ANY($1::text[])",
                      renames)
    left += await qval(conn, "SELECT count(*) FROM portfolio.note_extraction_staged_fields "
                             "WHERE field_key = ANY($1::text[])", renames)
    left += await qval(conn, "SELECT count(*) FROM portfolio.note_gold_values WHERE field_key = ANY($1::text[])",
                       renames)
    check(len(renames) > 0 and left == 0, "no row remains on a renamed key (the data migration is complete)",
          f"{len(renames)} renamed keys, {left} rows still on one")
    moved = {}
    for t in ("note_term_readings", "note_extraction_staged_fields", "note_gold_values"):
        moved[t] = await qval(conn, f"SELECT count(*) FROM portfolio.{t} WHERE metadata ? 'renamed_from'")
    find("rows migrated to a new key (old key in metadata.renamed_from)", json.dumps(moved))
    trig = {r["tgname"]: r["e"] for r in await conn.fetch(
        "SELECT tgname, tgenabled::text AS e FROM pg_trigger WHERE tgname = ANY($1::text[])",
        ["note_term_readings_no_update", "note_gold_values_human_guard_ins"])}
    check(len(trig) == 2 and set(trig.values()) == {"O"},
          "the readings-immutable and gold human-guard triggers are re-enabled after the migration", json.dumps(trig))


async def y_reader_schema(conn, specs):
    section("[Y] The generated reader schema equals the registry; buffer and barrier are distinct")
    from services.note_extraction import schema
    rows = await q(conn, "SELECT field_key, description FROM portfolio.note_terms_field_registry "
                         "WHERE retired_at IS NULL AND extraction_method = 'model'")
    reg = {r["field_key"]: r["description"] for r in rows}
    js = schema.json_schema(schema.reader_specs(specs))
    props = js["properties"]
    check(len(reg) > 0 and set(props) == set(reg), "reader JSON schema keys == the registry's live model fields",
          f"schema-only {sorted(set(props) - set(reg))}, registry-only {sorted(set(reg) - set(props))}")
    verbatim = [k for k in reg if not props.get(k, {}).get("description", "").startswith(reg[k])]
    check(not verbatim, "every field's description in the prompt schema is the registry's, verbatim", f"{verbatim}")
    live = {r["field_key"] for r in await q(conn, "SELECT field_key FROM portfolio.note_terms_field_registry "
                                                  "WHERE retired_at IS NULL")}
    check({s.key for s in specs} == live, "build_field_specs returns exactly the live registry rows")
    try:
        schema.assert_no_shared_protection_field(specs)
        ok = True
    except ValueError:
        ok = False
    by = {s.key: s for s in specs}
    check(ok and "buffer_pct" in by and "barrier_pct" in by and "protection_pct" not in by,
          "buffer_pct and barrier_pct are separate live fields; protection_pct is retired")
    import dataclasses
    bad = [dataclasses.replace(s, description="the buffer or barrier level") if s.key == "barrier_pct" else s
           for s in specs]
    try:
        schema.assert_no_shared_protection_field(bad)
        refused = False
    except ValueError:
        refused = True
    check(refused, "the separation check REFUSES a spec where barrier_pct admits a buffer level")


def y_lists():
    section("[Y] List fields: validate good, reject malformed; reordered equal members agree")
    from services.note_extraction import compare as cmp
    from services.note_extraction import schema
    good = {
        "underlyings": [
            {"name": "S&P 500® Index", "ticker": "SPX", "kind": "index", "weight_pct": None, "initial_level": 5000.5,
             "return_basis": "price_return", "fx_treatment": "none"},
            {"name": "Russell 2000® Index", "ticker": "RTY", "kind": "index", "weight_pct": None,
             "initial_level": 2100, "return_basis": "price_return", "fx_treatment": "none"}],
        "observation_schedule": [
            {"observation_date": "2026-06-26", "payment_date": "2026-07-01", "coupon_barrier_pct": 70,
             "coupon_amount": 22.5, "call_level_pct": 100, "call_amount": 1022.5},
            {"observation_date": "2026-09-28", "payment_date": "2026-10-01", "coupon_barrier_pct": 70,
             "coupon_amount": 22.5, "call_level_pct": 100, "call_amount": 1022.5}],
        "distribution": [
            {"name": "Alpha Securities LLC", "role": "issuer_affiliated_agent", "fee_min_pct": 1.5, "fee_max_pct": 2.0},
            {"name": "Zeta Markets LLC", "role": "dealer", "fee_min_pct": None, "fee_max_pct": 0.5}],
    }
    bad = {
        "underlyings": [{"ticker": "SPX", "kind": "index"}],                                  # no name
        "observation_schedule": [{"observation_date": "2026-09-28", "payment_date": "2026-09-01"}],  # pays before
        "distribution": [{"name": "Alpha Securities LLC", "role": "dealer", "fee_min_pct": 3, "fee_max_pct": 1}],
    }
    bad2 = {
        "underlyings": [{"name": "SPX", "weight_pct": 150}],
        "observation_schedule": [{"observation_date": "not a date"}],
        "distribution": [{"name": "Alpha Securities LLC", "role": "bogus_role"}],
    }
    for k in good:
        members, err = schema.validate_list(k, good[k])
        check(err is None and isinstance(members, list) and len(members) == len(good[k]),
              f"{k}: a good fixture validates", err or "")
        for label, b in (("malformed", bad[k]), ("malformed (second shape)", bad2[k])):
            _m, err = schema.validate_list(k, b)
            check(err is not None, f"{k}: a {label} fixture is REJECTED", (err or "accepted")[:120])
        rev = list(reversed(good[k]))
        check(rev != good[k] and schema.lists_agree(k, good[k], rev),
              f"{k}: the same members in reverse order AGREE")
        changed = json.loads(json.dumps(good[k]))
        first_num = next(f for f, kind in schema._member_fields(k) if kind == schema.KIND_NUMBER
                         and changed[0].get(f) is not None)
        changed[0][first_num] = float(changed[0][first_num]) + 1
        check(not schema.lists_agree(k, good[k], changed),
              f"{k}: a changed member field ({first_num}) DISAGREES")
    # same normalisation as scalars inside members: 2 vs "2.00" vs 2.0
    a = [{"name": "Alpha Securities LLC", "role": "dealer", "fee_min_pct": 2, "fee_max_pct": "2.00"}]
    b = [{"name": "ALPHA SECURITIES, L.L.C.".replace("L.L.C.", "LLC"), "role": "dealer", "fee_min_pct": 2.0,
          "fee_max_pct": 2}]
    check(schema.lists_agree("distribution", a, b),
          "member fields compare with scalar normalisation (2 == '2.00'; name case/punctuation ignored)")
    # end to end through compare_field: two readers, reordered lists -> verified agreement
    spec = schema.FieldSpec("underlyings", "Underlyings", schema.KIND_LIST, "d", value_shape="list")
    names = "S&P 500® Index and the Russell 2000® Index"
    e1 = cmp.Evidence("model_1", good["underlyings"], names, schema.normalize(spec, good["underlyings"]),
                      None, True, schema.value_in_quote(spec, good["underlyings"], names))
    rev = list(reversed(good["underlyings"]))
    e2 = cmp.Evidence("model_2", rev, names, schema.normalize(spec, rev), None, True,
                      schema.value_in_quote(spec, rev, names))
    c = cmp.compare_field(spec, e1, e2)
    check(c.outcome == "verified_agreement", "compare_field: two readers with reordered equal lists -> "
          "verified_agreement", c.reason)


async def y_participants(conn):
    section("[Y] An unknown distribution participant is reported, not dropped")
    from services.database import platform_scope
    from services.note_extraction import participants
    defs = await check_values(conn, "portfolio.distribution_participants")
    types, statuses = allowed(defs, "participant_type"), allowed(defs, "status")
    check("other" in types and "proposed" in statuses, "fixture participant values satisfy the live CHECKs",
          f"types {sorted(types)}, statuses {sorted(statuses)}")
    pre = await qval(conn, "SELECT count(*) FROM portfolio.distribution_participants "
                           "WHERE lower(canonical_name) = lower($1) OR lower($1) = ANY(SELECT lower(a) FROM unnest(aliases) a)",
                     UNKNOWN_PARTICIPANT)
    check(pre == 0, "the 'unknown' participant really is absent from the table")
    async with platform_scope(conn):
        await conn.execute(
            "INSERT INTO portfolio.distribution_participants (canonical_name, participant_type, aliases, status, notes) "
            "VALUES ($1, 'other', $2, 'proposed', $3)", FIXTURE_PARTICIPANT, ["NFVerify Fixture Dealer"], TAG)
    rows = [dict(r) for r in await q(
        conn, "SELECT id, canonical_name, participant_type, aliases, status, observed_count "
              "FROM portfolio.distribution_participants WHERE status <> 'retired'")]
    check(any(r["canonical_name"] == FIXTURE_PARTICIPANT for r in rows), "the fixture participant is readable")
    members_in = [{"name": FIXTURE_PARTICIPANT.title(), "role": "dealer"},
                  {"name": "NFVerify Fixture Dealer", "role": "dealer"},
                  {"name": UNKNOWN_PARTICIPANT, "role": "placement_agent"}]
    members, unmatched = participants.match_distribution(members_in, rows)
    check(len(members) == len(members_in), "every member is still in the list (none dropped)",
          f"{len(members)} of {len(members_in)}")
    check(members[0]["matched"] is True and members[1]["matched"] is True,
          "the known participant matches by canonical name AND by alias")
    check(members[2]["matched"] is False and members[2]["participant_id"] is None
          and [u["name"] for u in unmatched] == [UNKNOWN_PARTICIPANT],
          "the unknown participant is kept with no id AND reported in unmatched")
    return rows


def y_rules(dictionary):
    section("[Y] Rules extract each labeled field; dictionary labels from >= 3 banks")
    from services.edgar_pipeline import is_valid_cusip
    from services.note_extraction import rules

    def cusip_with_check(base8: str) -> str:
        total = 0
        for i, ch in enumerate(base8):
            v = int(ch) if ch.isdigit() else ord(ch) - 55
            if i % 2 == 1:
                v *= 2
            total += v // 10 + v % 10
        return base8 + str((10 - total % 10) % 10)

    def isin_for(cusip: str) -> str:
        body = "US" + cusip
        digits = "".join(str(int(c, 36)) for c in body)
        total = 0
        for i, ch in enumerate(reversed(digits)):
            n = int(ch)
            if i % 2 == 0:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        return body + str((10 - total % 10) % 10)

    cusip = cusip_with_check("06745QAB")
    isin = isin_for(cusip)
    check(is_valid_cusip(cusip) and rules._isin_valid(isin), "fixture CUSIP and ISIN pass their check digits",
          f"{cusip} {isin}")
    text = "\n".join([
        "Pricing Date: March 27, 2026", "Original Issue Date: April 1, 2026",
        "Final Valuation Date: March 24, 2028", "Maturity Date: March 29, 2028",
        f"CUSIP No.: {cusip}", f"ISIN: {isin}", "Aggregate Principal Amount: $2,892,000",
        "Minimum Denominations: $1,000",
        "The estimated value of the notes on the pricing date is $961.46 per $1,000 principal amount.",
        "Per Note", "100.00%", "2.50%", "97.50%",
        "The agent will receive a commission of $25.00 per $1,000 principal amount.",
        "The public offering price for investors purchasing the notes in fee-based advisory accounts will be $980.00 per note.",
        "Registration Statement No. 333-275898",
    ])
    r = rules.run_rules(text, dictionary=dictionary)
    want = {
        "pricing_date": "2026-03-27", "issue_date": "2026-04-01", "final_valuation_date": "2028-03-24",
        "maturity_date": "2028-03-29", "cusip": cusip, "isin": isin, "issue_size": 2892000, "denomination": 1000,
        "estimated_value_per_1000": {"min": 961.46, "max": 961.46, "bound": "exact"},
        "price_to_public_pct": 100, "total_fees_pct": 2.5, "proceeds_to_issuer_pct": 97.5,
        "agent_commission_pct": {"min": 2.5, "max": 2.5, "bound": "exact"},
        "fee_based_account_price": {"min": 980, "max": 980, "bound": "exact"},
        "registration_number": "333-275898",
    }
    for k, v in want.items():
        got = r.hits.get(k)
        check(got is not None and got.value == v, f"rules extract {k}", f"got {got.value if got else None!r}")
        if got is not None:
            check(bool(got.quote) and got.quote in text, f"{k}: the hit's quote is an exact span of the text")
    # dictionary labels: three different banks, each with its own label
    by = (dictionary.get("fields", {}).get("pricing_date") or {}).get("by_issuer") or {}
    tried, banks_ok = set(), []
    for bank, labels in sorted(by.items()):
        for lab in labels:
            if lab.lower() in tried:
                continue
            snippet = f"{lab}: March 27, 2026\nMaturity Date: March 29, 2028"
            hit = rules.run_rules(snippet, dictionary=dictionary).hits.get("pricing_date")
            if (hit is not None and hit.value == "2026-03-27" and hit.detail.get("label", "").lower() == lab.lower()
                    and hit.detail.get("label_source") == "dictionary" and bank in hit.detail.get("label_issuers", [])):
                banks_ok.append((bank, lab))
                tried.add(lab.lower())
                break
    check(len({b for b, _ in banks_ok}) >= 3,
          "pricing_date is extracted with a GENERATED dictionary label for >= 3 different banks",
          "; ".join(f"{b}: '{l}'" for b, l in banks_ok))
    src = (API_DIR / "services/note_extraction/rules.py").read_text()
    check("_DATE_LABELS" not in src and "label_dictionary" in src,
          "the rules' hand-written date-label list is gone; labels come from the dictionary")
    check(dictionary.get("source_run_id") == "aa1c8c7c-239a-4057-9fb3-2c911c9c2bc5"
          and bool(dictionary.get("version")), "the dictionary file is versioned and generated from inventory run aa1c8c7c",
          f"{dictionary.get('version')} {dictionary.get('source_run_id')}")


def y_inventory_synonyms(specs):
    section("[Y] The inventory mapping check's synonyms come from the registry")
    from services import edgar_inventory as inv
    src = (API_DIR / "services/edgar_inventory.py").read_text()
    check("_SYNONYMS: dict" not in src and "_with_synonyms" not in src, "the hard-coded _SYNONYMS table is gone")
    table = inv.synonym_table(specs)
    by = {s.key: s for s in specs}
    check("trigger" in table.get("barrier_pct", frozenset()), "the generated table carries barrier_pct's registry "
          "synonym 'trigger'", f"{sorted(table.get('barrier_pct', []))[:8]}")
    check(inv.plausible_mapping("Trigger Value", by["barrier_pct"]) is True
          and inv.plausible_mapping("Denominations", by["maturity_date"]) is False,
          "plausible_mapping: 'Trigger Value' -> barrier_pct accepted, 'Denominations' -> maturity_date refused")


def y_derive():
    section("[Y] Derivations: tenor, max loss, the missing estimated-value unit — computed and marked derived")
    from services.note_extraction import derive
    t = derive.tenor_months("2026-03-27", "2028-03-27")
    check(t is not None and t.value == 24 and t.derived is True and t.derived_from == ["pricing_date", "maturity_date"],
          "tenor_months 2026-03-27 -> 2028-03-27 = 24, marked derived", f"{t}")
    check(derive.tenor_months("2026-03-27", None) is None, "no maturity date -> no tenor (never a guess)")
    m = derive.max_principal_loss_pct("buffer", buffer_pct=15)
    check(m is not None and m.value == 85 and m.derived is True, "max loss, 15% buffer = 85, marked derived", f"{m}")
    check(derive.max_principal_loss_pct("barrier", barrier_pct=70).value == 100
          and derive.max_principal_loss_pct("full").value == 0
          and derive.max_principal_loss_pct("buffer", buffer_pct=20, downside_leverage=1.25).value == 100,
          "max loss: barrier 100, full 0, geared buffer capped at 100")
    e = derive.estimated_value_counterpart(per_1000={"min": 965.5, "max": 965.5, "bound": "exact"})
    check(e is not None and e.field_key == "estimated_value_pct" and e.value["min"] == 96.55 and e.derived is True,
          "estimated value stated per $1,000 -> % derived (decision B)", f"{e}")
    e2 = derive.estimated_value_counterpart(pct={"min": 96.55, "max": None, "bound": "not_less_than"})
    check(e2 is not None and e2.field_key == "estimated_value_per_1000" and e2.value["min"] == 965.5
          and e2.value["max"] is None and e2.derived is True,
          "estimated value stated as % (a range) -> per $1,000 derived, bound kept")
    check(derive.estimated_value_counterpart(per_1000=965.5, pct=96.55) is None,
          "both units stated -> nothing derived (the stated values stand)")


def y_ranges(dictionary):
    section("[Y] Ranges (decision C)")
    from services.note_extraction import rules
    r1 = rules.run_rules("The agent will receive a commission of up to 2.50% of the principal amount.",
                         dictionary=dictionary).hits.get("agent_commission_pct")
    check(r1 is not None and r1.value == {"min": None, "max": 2.5, "bound": "up_to"} and "up to 2.50%" in r1.quote,
          "'up to 2.50%' -> min NULL, max 2.50, the bound wording kept in the quote",
          f"{r1.value if r1 else None} | {r1.quote if r1 else ''}")
    r2 = rules.run_rules("Investors in fee-based advisory accounts may pay a purchase price as low as $977.50 per note.",
                         dictionary=dictionary).hits.get("fee_based_account_price")
    check(r2 is not None and r2.value == {"min": 977.5, "max": None, "bound": "as_low_as"}
          and "as low as $977.50" in r2.quote, "'as low as $977.50' -> min 977.50, max NULL, wording kept",
          f"{r2.value if r2 else None}")
    r3 = rules.run_rules("The public offering price for investors purchasing the notes in fee-based advisory "
                         "accounts will be $977.50 per note.", dictionary=dictionary).hits.get("fee_based_account_price")
    check(r3 is not None and r3.value == {"min": 977.5, "max": 977.5, "bound": "exact"},
          "'$977.50' alone -> min = max = 977.50", f"{r3.value if r3 else None}")
    r4 = rules.run_rules("The estimated value of the notes is not less than $940.00 per $1,000 principal amount.",
                         dictionary=dictionary).hits.get("estimated_value_per_1000")
    check(r4 is not None and r4.value == {"min": 940, "max": None, "bound": "not_less_than"}
          and "not less than" in r4.quote, "'not less than $940.00' -> min 940, max NULL",
          f"{r4.value if r4 else None}")


def y_checks():
    section("[Y] Self-checks fire on an inconsistency, stay quiet when consistent")
    from services.note_extraction import checks
    good = {"price_to_public_pct": 100, "total_fees_pct": 2.5, "proceeds_to_issuer_pct": 97.5,
            "estimated_value_pct": {"min": 96.1, "max": 96.1}, "pricing_date": "2026-03-27",
            "maturity_date": "2027-03-29", "observation_schedule": [{"observation_date": "2026-09-28"}],
            "protection_type": "buffer", "buffer_pct": 15, "coupon_type": "none", "call_type": "none"}
    rows_ok = [{"underlying_return_pct": -10, "payment_per_1000": 1000},
               {"underlying_return_pct": -30, "payment_per_1000": 850},
               {"underlying_return_pct": 20, "payment_per_1000": 1200}]
    cases = [
        ("fees_reconcile", lambda v: checks.fees_reconcile(v), {"proceeds_to_issuer_pct": 96.0}),
        ("estimated_value_below_price", lambda v: checks.estimated_value_below_price(v),
         {"estimated_value_pct": {"min": 101, "max": 101}}),
        ("observation_dates_in_term", lambda v: checks.observation_dates_in_term(v),
         {"observation_schedule": [{"observation_date": "2027-06-30"}]}),
        ("initial_valuation_date", lambda v: checks.initial_valuation_date(v, "2026-03-25"), None),
        ("payoff_matches_examples", lambda v: checks.payoff_matches_examples(v, [
            *rows_ok[:1], {"underlying_return_pct": -30, "payment_per_1000": 700}]), None),
    ]
    quiet = {
        "fees_reconcile": checks.fees_reconcile(good),
        "estimated_value_below_price": checks.estimated_value_below_price(good),
        "observation_dates_in_term": checks.observation_dates_in_term(good),
        "initial_valuation_date": checks.initial_valuation_date(good, "2026-03-27"),
        "payoff_matches_examples": checks.payoff_matches_examples(good, rows_ok),
    }
    for name, fn, patch in cases:
        v = {**good, **(patch or {})}
        f = fn(v)
        check(f is not None and f.check == name and f.needs_review is True and bool(f.reason),
              f"{name}: FIRES on a crafted inconsistency with needs_review and a reason",
              f.reason if f else "quiet")
        check(quiet[name] is None, f"{name}: stays QUIET on the consistent fixture")
    check(checks.run_self_checks(good, stated_initial_valuation_date="2026-03-27", hypothetical_rows=rows_ok) == [],
          "run_self_checks on the consistent fixture returns no finding")
    check(checks.payoff_matches_examples({**good, "coupon_type": "contingent"}, [
        {"underlying_return_pct": -30, "payment_per_1000": 1}]) is None,
        "the payoff check stays out of notes whose payoff it cannot recompute (coupons)")


async def y_cascade(conn, specs, catalog, participant_rows, filings):
    section("[Y] End to end (mocked readers): derived + self-check needs_review, persisted and re-read")
    from services.database import platform_scope
    from services.note_extraction import cascade, documents, readers, runner, schema, store
    rspecs = schema.reader_specs(specs)
    slots = cascade.EnsembleSlots(readers.ReaderConfig("model_1", M1), readers.ReaderConfig("model_2", M2), None, None)
    defs = await check_values(conn, "portfolio.note_extraction_runs")
    check("verify" in allowed(defs, "run_kind") and "running" in allowed(defs, "status"),
          "fixture run values satisfy the live CHECKs")
    results = {}
    for consistent, fid in ((True, filings[0]), (False, filings[1])):
        READER_ANSWER.clear()
        READER_ANSWER.update(reader_answer(rspecs, consistent))
        doc = documents.document_from_html(fixture_html(consistent), reference_filing_id=fid,
                                           filer_name="NOTEFIELDS VERIFY", form_type="424B2")
        before = len(CHAT_BODIES)
        out = await cascade.run_note(doc, specs, slots, catalog=catalog, spend=None, participant_rows=participant_rows)
        check(len(CHAT_BODIES) - before == 2, f"{'consistent' if consistent else 'inconsistent'} note: exactly the "
              f"two MOCKED reader calls were made")
        sent = set(json.loads(CHAT_BODIES[-1]["messages"][0]["content"].split("JSON schema:\n", 1)[1])["properties"])
        check(sent == {s.key for s in rspecs}, "the readers were asked exactly the registry's model fields")
        run_id = await store.create_run(conn, run_kind="verify", spend_cap_usd=0, config={"verify_tag": TAG})
        staging_id = await runner.persist_outcome(conn, out, run_id=run_id, ensemble_config_id=None)
        results[consistent] = (out, run_id, staging_id)
    # re-read from the database
    for consistent, (out, run_id, staging_id) in results.items():
        name = "consistent" if consistent else "inconsistent"
        st = (await q(conn, "SELECT status, status_reason, detail, unmatched_participants "
                            "FROM portfolio.note_extraction_staging WHERE id = $1", staging_id))[0]
        fields = {r["field_key"]: r for r in await q(
            conn, "SELECT field_key, resolved_value, resolution, needs_review, metadata "
                  "FROM portfolio.note_extraction_staged_fields WHERE staging_id = $1", staging_id)}
        check(len(fields) == len(specs), f"{name}: one staged row per live registry field (re-read)",
              f"{len(fields)} vs {len(specs)}")
        derived_reads = {r["field_key"]: r for r in await q(
            conn, "SELECT field_key, value, metadata FROM portfolio.note_term_readings "
                  "WHERE run_id = $1 AND source = 'derived'", run_id)}
        for k in ("tenor_months", "max_principal_loss_pct", "estimated_value_pct"):
            f = fields.get(k)
            meta = json.loads(f["metadata"]) if f and isinstance(f["metadata"], str) else (f["metadata"] if f else {})
            rmeta = derived_reads.get(k, {}).get("metadata")
            rmeta = json.loads(rmeta) if isinstance(rmeta, str) else (rmeta or {})
            check(f is not None and f["resolution"] == "derived" and meta.get("derived") is True
                  and k in derived_reads and rmeta.get("derived") is True,
                  f"{name}: {k} is staged 'derived' AND has a 'derived' reading, both marked")
        detail = st["detail"] if isinstance(st["detail"], dict) else json.loads(st["detail"])
        fired = sorted(c["check"] for c in detail.get("self_checks", []))
        unmatched = st["unmatched_participants"]
        unmatched = unmatched if isinstance(unmatched, list) else json.loads(unmatched)
        check(UNKNOWN_PARTICIPANT in [u.get("name") for u in unmatched],
              f"{name}: the unknown participant is in the staged unmatched_participants")
        dist = json.loads(fields["distribution"]["resolved_value"]) if fields["distribution"]["resolved_value"] else []
        check(len(dist) == 2, f"{name}: the staged distribution list still holds both members")
        if consistent:
            check(fired == [] and st["status"] == "verified",
                  "consistent: no self-check fires and the note is verified", f"{st['status']} {fired}")
        else:
            want = {"fees_reconcile", "estimated_value_below_price", "observation_dates_in_term",
                    "initial_valuation_date", "payoff_matches_examples"}
            check(set(fired) == want, "inconsistent: all five self-checks fire", f"{fired}")
            check(st["status"] == "needs_review" and all(f"self-check {c}" in (st["status_reason"] or "") for c in want),
                  "inconsistent: the note is needs_review and its reason names every check")
            pd = fields["pricing_date"]
            pmeta = pd["metadata"] if isinstance(pd["metadata"], dict) else json.loads(pd["metadata"])
            check(pd["needs_review"] is True and any("initial_valuation_date" in r for r in pmeta.get("review_reasons", [])),
                  "inconsistent: pricing_date is flagged needs_review with the decision-A reason")
            check(json.loads(pd["resolved_value"]) == "2026-03-27",
                  "inconsistent: the flagged pricing date is NOT changed (never silently fixed)")


async def y_public_data_only(conn, client, logged, catalog_names):
    section("[Y] public_data_only: org-scoped call and org picker refuse; the note ensemble accepts")
    from services import extraction, model_catalog
    from services.note_extraction import cascade
    flagged = sorted(await model_catalog.public_data_only_model_ids(conn))
    openai = sorted(r["model_id"] for r in await q(conn, "SELECT model_id FROM platform_model_catalog "
                                                         "WHERE provider = 'openai'"))
    check(len(flagged) > 0 and flagged == openai, "public_data_only is set exactly on the OpenAI models",
          f"{flagged}")
    model = flagged[0]
    # org-scoped call through the real chain executor (client mocked)
    orig_chain = extraction.resolve_fallback_chain

    async def no_chain(org_id=None, *, primary_key=extraction.DEFAULT_MODEL_KEY):
        return []
    extraction.resolve_fallback_chain = no_chain
    try:
        client.messages.models.clear()
        try:
            await extraction._execute_chain(task_type="text_generation", org_id=DEFAULT_ORG,
                                            model_key=extraction.DEFAULT_MODEL_KEY, model_override=model,
                                            make_call=lambda c, m, a, e: c.messages.create(model=m),
                                            extract=lambda m: m)
            refused = False
        except extraction.AIModelNotAuthorizedError:
            refused = True
        check(refused and model not in client.messages.models,
              f"an ORG-scoped call naming {model} is refused before any provider call",
              f"provider saw {client.messages.models}")
        client.messages.models.clear()
        try:
            await extraction._execute_chain(task_type=cascade.TASK_KEY, org_id=None,
                                            model_key=extraction.DEFAULT_MODEL_KEY, model_override=model,
                                            make_call=lambda c, m, a, e: c.messages.create(model=m),
                                            extract=lambda m: m)
            ran = True
        except extraction.AIModelNotAuthorizedError:
            ran = False
        check(ran and client.messages.models == [model],
              f"the note-extraction task with no org reaches {model} (mocked provider)", f"{client.messages.models}")
        client.messages.models.clear()
        try:
            await extraction._execute_chain(task_type=cascade.TASK_KEY, org_id=DEFAULT_ORG,
                                            model_key=extraction.DEFAULT_MODEL_KEY, model_override=model,
                                            make_call=lambda c, m, a, e: c.messages.create(model=m),
                                            extract=lambda m: m)
            refused2 = False
        except extraction.AIModelNotAuthorizedError:
            refused2 = True
        check(refused2 and client.messages.models == [],
              "even the note task is refused when an org is in scope")
    finally:
        extraction.resolve_fallback_chain = orig_chain
    # org model picker
    picker = await model_catalog.list_catalog_for_org(conn, DEFAULT_ORG)
    ids = {m["model_id"] for m in picker}
    check(len(ids) > 0 and not (ids & set(flagged)), "the org picker vocabulary hides every public-data-only model",
          f"{sorted(ids)}")
    before = sorted(await model_catalog.list_org_selections(conn, DEFAULT_ORG))
    tr = conn.transaction()
    await tr.start()
    try:
        try:
            await model_catalog.set_org_selections(conn, DEFAULT_ORG, [model])
            pick_refused = False
        except model_catalog.PublicDataOnlyError:
            pick_refused = True
    finally:
        await tr.rollback()
    after = sorted(await model_catalog.list_org_selections(conn, DEFAULT_ORG))
    check(pick_refused and before == after, "selecting it for an org is REFUSED and the org's selections are unchanged",
          f"before {before} after {after}")
    try:
        await model_catalog.validate_assignable_model(conn, DEFAULT_ORG, model)
        assign_refused = False
    except model_catalog.PublicDataOnlyError:
        assign_refused = True
    check(assign_refused, "assigning it to an org task is REFUSED")
    # the ensemble / inventory accept it
    try:
        await model_catalog.assert_model_allowed_for_task(conn, model, task_key=cascade.TASK_KEY, org_id=None)
        ens_ok = True
    except model_catalog.PublicDataOnlyError:
        ens_ok = False
    check(ens_ok, f"the note extraction ensemble task ({cascade.TASK_KEY}) accepts {model}")
    from services import edgar_inventory as inv
    ok, report = await inv.eligible_models(conn, fake_catalog({n: True for n in catalog_names}))
    check(model in ok, f"the EDGAR inventory's model choice accepts {model}",
          next((r["why_not"] for r in report if r["model_id"] == model), ""))


async def y_bulk_price(conn, specs):
    section("[Y] Bulk runs refuse an unpriced model; a manual price is used by the cap")
    from services.database import platform_scope
    from services.note_extraction import cascade, readers, runner, spend
    from services.note_extraction.trim import estimate_tokens_chars
    defs = await check_values(conn, "platform_model_catalog")
    check("available" in allowed(defs, "availability"), "fixture catalog value satisfies the live CHECK")
    async with platform_scope(conn):
        await conn.execute("INSERT INTO platform_model_catalog (model_id, display_name, provider, availability) "
                           "VALUES ($1, 'NOTEFIELDS VERIFY unpriced', $2, 'available')", FIXTURE_MODEL, FIXTURE_PROVIDER)
    cat = fake_catalog({FIXTURE_MODEL: False, M1: True})
    try:
        await spend.priced_catalog_for_bulk(conn, cat, [M1, FIXTURE_MODEL])
        refused = False
    except spend.UnpricedModelError as exc:
        refused = exc.models == [FIXTURE_MODEL]
    check(refused, "no proxy price + no manual price -> the bulk guard REFUSES, naming the model")
    runs_before = await qval(conn, "SELECT count(*) FROM portfolio.note_extraction_runs")
    slots = cascade.EnsembleSlots(readers.ReaderConfig("model_1", M1), readers.ReaderConfig("model_2", FIXTURE_MODEL),
                                  None, None)
    try:
        await runner.run_notes(conn, [], specs, slots, catalog=cat, spend_cap_usd=1.0, run_kind="pilot",
                               config={"verify_tag": TAG}, participant_rows=[])
        run_refused = False
    except spend.UnpricedModelError:
        run_refused = True
    runs_after = await qval(conn, "SELECT count(*) FROM portfolio.note_extraction_runs")
    check(run_refused and runs_after == runs_before, "a pilot run with it is refused before any run row exists",
          f"runs {runs_before} -> {runs_after}")
    from services import edgar_inventory as inv
    _ok, report = await inv.eligible_models(conn, cat)
    why = next((r["why_not"] for r in report if r["model_id"] == FIXTURE_MODEL), None)
    check(why is not None and "price" in why, "the inventory marks it not eligible for lack of a price", f"{why}")
    async with platform_scope(conn):
        await conn.execute("UPDATE platform_model_catalog SET manual_input_cost_per_mtok = 1.0, "
                           "manual_output_cost_per_mtok = 2.0 WHERE model_id = $1", FIXTURE_MODEL)
    priced = await spend.priced_catalog_for_bulk(conn, cat, [M1, FIXTURE_MODEL])
    dep = priced[FIXTURE_MODEL]
    check(dep.price_source == "manual" and abs(dep.input_cost_per_token - 1e-6) < 1e-15
          and abs(dep.output_cost_per_token - 2e-6) < 1e-15,
          "with a manual price the guard accepts it, priced from the catalog entry")
    check(priced[M1].price_source == "proxy", "a proxy price is never overridden by a manual one")
    chars, out_tokens = 40000, 1000
    est = spend.estimate_call_cost(dep, chars, out_tokens)
    expect = estimate_tokens_chars(chars) * 1e-6 + out_tokens * 2e-6
    check(abs(est - expect) < 1e-12 and est > 0, "the spend-cap estimate uses the manual price",
          f"est ${est:.6f} expected ${expect:.6f}")
    tracker = spend.SpendTracker(cap_usd=expect * 0.5)
    try:
        await tracker.reserve(est, "verify")
        capped = False
    except spend.SpendCapReached:
        capped = True
    check(capped, "a cap below the manual-priced estimate stops the call before it is made")
    rec = spend.recorded_cost(dep, 0.0, 1000, 500, 0)
    check(rec is not None and abs(rec - (1000 * 1e-6 + 500 * 2e-6)) < 1e-12,
          "a manual-priced call records the manual cost even when the proxy's header says $0", f"{rec}")


async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    conn = await asyncpg.connect(url, statement_cache_size=0)
    client, logged = install_mocks()
    sgnt_before = None
    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the verify connection's role does NOT bypass RLS",
              f"{await conn.fetchval('SELECT current_user')}")
        try:
            await teardown(conn)
        except Exception as exc:  # noqa: BLE001
            check(False, "start-of-run teardown", f"{type(exc).__name__}: {exc}")
        sgnt_before = await qval(conn, "SELECT count(*) FROM portfolio.securities_global_note_terms")
        from services.note_extraction import label_dictionary as ld
        from services.note_extraction import schema
        specs = await schema.load_specs(conn)
        dictionary = ld.load_dictionary()
        catalog_names = [r["model_id"] for r in await q(conn, "SELECT model_id FROM platform_model_catalog")]
        filings = [str(r["id"]) for r in await q(conn, "SELECT id FROM portfolio.reference_filings ORDER BY id LIMIT 2")]
        check(len(filings) == 2, "two real reference filings exist to serve as FK targets")

        async def run(name, fn, *a):
            try:
                res = fn(*a)
                if asyncio.iscoroutine(res):
                    res = await res
                return res
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")
                return None

        await run("task1", y_task1, conn)
        await run("registry", y_registry, conn, specs)
        await run("orphans", y_orphans, conn)
        await run("reader schema", y_reader_schema, conn, specs)
        await run("lists", y_lists)
        participant_rows = await run("participants", y_participants, conn) or []
        await run("rules", y_rules, dictionary)
        await run("inventory synonyms", y_inventory_synonyms, specs)
        await run("derive", y_derive)
        await run("ranges", y_ranges, dictionary)
        await run("checks", y_checks)
        await run("cascade", y_cascade, conn, specs, fake_catalog({M1: True, M2: True}), participant_rows, filings)
        await run("public_data_only", y_public_data_only, conn, client, logged, catalog_names)
        await run("bulk price", y_bulk_price, conn, specs)
        check(len(logged) >= 0 and all(not str(b.get("model", "")).startswith(("gpt", "claude")) for b in CHAT_BODIES),
              "every reader call went to a mocked fixture deployment (no real model named)")

        section("[Y] securities_global_note_terms untouched")
        sgnt_after = await qval(conn, "SELECT count(*) FROM portfolio.securities_global_note_terms")
        check(sgnt_before is not None and sgnt_after == sgnt_before,
              "securities_global_note_terms row count unchanged", f"{sgnt_before} -> {sgnt_after}")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        section("[Y] Teardown leaves zero fixture rows")
        try:
            await teardown(conn)
            left = await fixture_counts(conn)
            check(sum(left.values()) == 0, "zero fixture rows remain", json.dumps(left))
        except Exception as exc:  # noqa: BLE001
            check(False, "end-of-run teardown", f"{type(exc).__name__}: {exc}")
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        await conn.close()
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
