"""verify_readerfix1 — both AI readers answer within their token allowances (fund terms AND note terms).

gpt-5-nano (Model 2) spent its whole 3,000-token output allowance on hidden reasoning on IVV's prospectus and returned
no text, so every fund field showed "only one reader found a value". Fixed: ONE per-deployment reader setting
(readers.reader_settings — low reasoning effort + a 12,000-token reasoning headroom for gpt-5 / o-series models,
unchanged otherwise) used by both readers; an empty answer caused by the allowance is retried ONCE with double the
allowance (recorded, budgeted), a second empty answer is invalid "the model used its whole output allowance"; a fund
whose latest run had a failed reader is re-read once (both readers); a note with Model 1 ok and Model 2 empty gets
ONE Model-2-only re-read (Model 1's stored call reused). The agreement rule is unchanged.

    timeout 600 python3 apps/api/scripts/verify_readerfix1.py --self-checks-only   # pure, no DB
    timeout 600 python3 apps/api/scripts/verify_readerfix1.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_readerfix1.py --section r          # one DB section (b | r | n | l)
    timeout 600 python3 apps/api/scripts/verify_readerfix1.py                      # everything + fast regressions

Sections (each self-contained): S self-checks (settings, same setting for both readers, retry, second empty, no
retry when not the allowance, field-level failure, agreement rule) · B the REAL daily-budget ledger with the larger
reservation (fixture deployments) · R re-read eligibility once (funds: both readers; notes: Model 2 only) inside a
ROLLED-BACK transaction · N the real note cascade's Model-2-only re-read (stub transport) · L READ-ONLY live checks
(ensemble configs fingerprint, IVV due, notes affected, IVV prompt measured) · X fast regressions.

NO MODEL CALL, NO NETWORK: ``services.note_extraction.proxy._post`` is a scripted fake returning the real response
shape (content + finish_reason + usage incl. reasoning tokens). FIXTURES: deployments 'readerfix1-fx-*' (ledger rows
deleted at the end, by name), prompt version 'readerfix1.fx.*' (inside a transaction that is always rolled back).
Never writes a real reading; never changes an ensemble config, budget or model (fingerprinted before and after).
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import json
import os
import pathlib
import sys
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_total as VT  # noqa: E402
from _db_connect import admin_dsn, connect, operator_dsn  # noqa: E402

import asyncpg  # noqa: E402

from services import ai_budget as AB  # noqa: E402
from services import ai_daily_runs as DR  # noqa: E402
from services import replica_terms as RT  # noqa: E402
from services.note_extraction import cascade as CS  # noqa: E402
from services.note_extraction import proxy as PX  # noqa: E402
from services.note_extraction import readers as NR  # noqa: E402
from services.note_extraction import runner as RN  # noqa: E402
from services.note_extraction import schema as NS  # noqa: E402
from services.note_extraction import store as NSTORE  # noqa: E402
from services.note_extraction.spend import SpendTracker  # noqa: E402

FX = "readerfix1-fx-"
FX_PROMPT = "readerfix1.fx.v1"
REGRESSIONS = ["verify_dailyops1.py", "verify_aibudget1.py", "verify_rep1.py", "verify_rep3.py"]
# The live configs as the sprint prompt states them (Joe's — never changed by this sprint).
EXPECTED_CONFIGS = {
    "note_terms_extraction": ("gpt-5-mini", "tokens_per_day", Decimal("1500000"), "gpt-5-nano", "tokens_per_day",
                              Decimal("500000")),
    "replica_terms_extraction": ("gpt-5-mini", "tokens_per_day", Decimal("250000"), "gpt-5-nano", "tokens_per_day",
                                 Decimal("250000")),
}

_n_pass = _n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""), flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


# ═══ the scripted fake transport (the real response shape) ══════════════════
CALLS: list[dict] = []
SCRIPT: dict[str, list] = {}           # deployment -> queued responses (dicts from answer()/empty())
PROVIDER = {"mini": "gpt-5-mini-2025-08-07", "nano": "gpt-5-nano-2025-08-07", "plain": "plain-model"}


def answer(content: str, out: int = 900, reasoning: int = 400) -> dict:
    return {"content": content, "finish_reason": "stop", "out": out, "reasoning": reasoning}


def empty(finish: str = "length", out: int | None = None) -> dict:
    """The IVV shape: no text, every output token spent on reasoning (out None = the whole allowance)."""
    return {"content": "", "finish_reason": finish, "out": out, "reasoning": out}


def _provider_for(dep: str) -> str:
    for k, v in PROVIDER.items():
        if dep.endswith(k) or f"-{k}-" in dep or dep.endswith(f"-{k}"):
            return v
    return "plain-model"


async def fake_post(path: str, body: dict, timeout: float):
    dep = body.get("model")
    queue = SCRIPT.get(dep) or []
    step = queue.pop(0) if queue else answer("{}")
    mt = AB.body_max_tokens(body)
    out = step["out"] if step["out"] is not None else mt
    tin = (AB.prompt_chars_of(body.get("messages")) + 3) // 4
    CALLS.append({"model": dep, "max_tokens": mt, "reasoning_effort": body.get("reasoning_effort"), "out": out,
                  "tags": (body.get("metadata") or {}).get("tags")})
    resp = {"model": _provider_for(dep),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": step["content"]},
                         "finish_reason": step["finish_reason"]}],
            "usage": {"prompt_tokens": tin, "completion_tokens": out, "total_tokens": tin + out,
                      "completion_tokens_details": {"reasoning_tokens": step["reasoning"] if step["reasoning"]
                                                    is not None else out}}}
    return PX.ProxyResponse(200, resp, json.dumps(resp), {"x-litellm-attempted-fallbacks": "0"}, 5)


def dep(name: str, upstream: str) -> PX.Deployment:
    return PX.Deployment(name=name, deployment_id=None, upstream=upstream, price_key=name,
                         input_cost_per_token=1e-7, output_cost_per_token=4e-7, cache_read_cost_per_token=None,
                         supports_response_schema=False, max_input_tokens=None)


def catalog(prefix: str = FX) -> dict:
    return {f"{prefix}mini": dep(f"{prefix}mini", "openai/gpt-5-mini"),
            f"{prefix}nano": dep(f"{prefix}nano", "openai/gpt-5-nano"),
            f"{prefix}plain": dep(f"{prefix}plain", "fixture/plain-model")}


def fund_specs() -> list:
    return [NS.FieldSpec("expense_ratio_net_pct", "Net expense ratio", NS.KIND_NUMBER, "net expense ratio, %"),
            NS.FieldSpec("index_name", "Index", "text", "the index tracked"),
            NS.FieldSpec("inception_date", "Inception", NS.KIND_DATE, "inception date")]


def fund_answer(**over) -> str:
    base = {"expense_ratio_net_pct": {"value": 0.03, "quote": "Total annual fund operating expenses 0.03%"},
            "index_name": {"value": "S&P 500", "quote": "seeks to track the S&P 500"},
            "inception_date": {"value": "2000-05-15", "quote": "commenced operations on May 15, 2000"}}
    base.update(over)
    return json.dumps(base)


def reset():
    CALLS.clear()
    SCRIPT.clear()


# ═══ S — self-checks (pure: no DB) ══════════════════════════════════════════
async def self_checks():
    PX._post = fake_post
    cat = catalog()
    mini, nano, plain = f"{FX}mini", f"{FX}nano", f"{FX}plain"

    section("[S1] the ONE per-deployment setting: gpt-5 family → low effort + headroom; others unchanged")
    for name, d in (("gpt-5-mini", None), ("gpt-5-nano", None), (nano, cat[nano]), ("openai/gpt-5-mini", None)):
        f = NR.reader_settings("model_2", name, answer_tokens=RT.READER_MAX_TOKENS, catalog={name: d} if d else None)
        n = NR.reader_settings("model_2", name, answer_tokens=NR.NOTE_ANSWER_TOKENS, catalog={name: d} if d else None)
        check(f.reasoning_effort == "low" and f.max_tokens == 15_000 and f.retry_max_tokens == 30_000
              and n.reasoning_effort == "low" and n.max_tokens == 16_000 and n.retry_max_tokens == 32_000,
              f"{name}: effort low; fund 15,000 → retry 30,000; note 16,000 → retry 32,000",
              f"fund {f.max_tokens}/{f.retry_max_tokens} note {n.max_tokens}/{n.retry_max_tokens}")
    for name, d in (("gpt-4o-mini", None), (plain, cat[plain]), ("deepseek-chat", None), ("claude-sonnet", None)):
        f = NR.reader_settings("model_1", name, answer_tokens=RT.READER_MAX_TOKENS, catalog={name: d} if d else None)
        n = RN.reader_config("model_1", name, catalog={name: d} if d else None)
        check(f.reasoning_effort is None and f.max_tokens == 3000 and n.reasoning_effort is None
              and n.max_tokens == 4000, f"{name}: not a reasoning model — no effort, allowance unchanged (3,000 / 4,000)",
              f"{f} | {n}")
    o = RN.reader_config("model_1", "gpt-5-mini", {"gpt-5-mini": "medium"}, 9000)
    check(o.reasoning_effort == "medium" and o.max_tokens == 9000 and o.retry_max_tokens == 18000,
          "an explicit operator override (pilot / eval CLI) wins over the shared default", f"{o}")
    flagged = dep("odd-name", "fixture/odd")
    flagged.supports_reasoning = True
    check(NR.is_reasoning_model("odd-name", flagged) and not NR.is_reasoning_model("odd-name", dep("odd-name", "x/y")),
          "the proxy's own supports_reasoning flag also marks a reasoning model")

    section("[S2] the fund reader and the note reader use the SAME setting (one function, one request shape)")
    seen = []
    real = NR.reader_settings

    def spy(*a, **k):
        seen.append((a[1], k.get("answer_tokens")))
        return real(*a, **k)
    NR.reader_settings = spy
    try:
        RT.reader_cfg("model_2", nano, cat)
        RN.reader_config("model_2", nano, catalog=cat)
    finally:
        NR.reader_settings = real
    check(seen == [(nano, RT.READER_MAX_TOKENS), (nano, NR.NOTE_ANSWER_TOKENS)],
          "both readers' settings come from readers.reader_settings (fund answer 3,000; note answer 4,000)", f"{seen}")
    reset()
    specs = fund_specs()
    msgs, ph = RT.build_messages(specs, "x" * 29_000, fund_label="IVV", form_type="485BPOS")
    SCRIPT[nano] = [answer(fund_answer())]
    SCRIPT[plain] = [answer(fund_answer())]
    await RT.read_one("model_2", nano, specs, msgs, ph, cat)
    await RT.read_one("model_1", plain, specs, msgs, ph, cat)
    note_specs = fund_specs()
    SCRIPT[nano] = [answer(fund_answer())]
    SCRIPT[plain] = [answer(fund_answer())]
    await NR.read(RN.reader_config("model_2", nano, catalog=cat), note_specs, "y" * 20_000, filer="F", form_type="424B2",
                  catalog=cat, spend=None)
    await NR.read(RN.reader_config("model_1", plain, catalog=cat), note_specs, "y" * 20_000, filer="F",
                  form_type="424B2", catalog=cat, spend=None)
    got = [(c["model"], c["reasoning_effort"], c["max_tokens"]) for c in CALLS]
    check(got == [(nano, "low", 15_000), (plain, None, 3000), (nano, "low", 16_000), (plain, None, 4000)],
          "request bodies: gpt-5 deployments carry reasoning_effort 'low' + the larger allowance; the other model "
          "neither (fund, then note)", f"{got}")

    section("[S3] an empty answer from the allowance → ONE retry with the larger allowance → a value")
    reset()
    SCRIPT[nano] = [empty("length"), answer(fund_answer(), out=2100, reasoning=1600)]
    c = await RT.read_one("model_2", nano, specs, msgs, ph, cat)
    check(c.status == "ok" and len(c.fields) == 3 and c.fields["index_name"]["value"] == "S&P 500",
          "fund reader: the retry's answer is used — every field has its value", f"{c.status} {c.error} {list(c.fields)}")
    check([x["max_tokens"] for x in CALLS] == [15_000, 30_000] and len(CALLS) == 2,
          "exactly two calls: 15,000 then 30,000", f"{[x['max_tokens'] for x in CALLS]}")
    check(bool(c.retry) and c.retry.get("made") is True and c.retry.get("first_finish_reason") == "length"
          and c.retry.get("retry_max_tokens") == 30_000, "the retry is recorded on the call (→ reading metadata)",
          f"{c.retry}")
    tin = (sum(len(m["content"]) for m in msgs) + 3) // 4
    check(c.output_tokens == 15_000 + 2100 and c.input_tokens == 2 * tin,
          "both attempts' tokens are counted (input ×2, output summed)", f"in {c.input_tokens} out {c.output_tokens}")
    reset()
    SCRIPT[nano] = [empty("length"), answer(fund_answer())]
    nc = await NR.read(RN.reader_config("model_2", nano, catalog=cat), note_specs, "y" * 20_000, filer="F",
                       form_type="424B2", catalog=cat, spend=SpendTracker(cap_usd=None))
    check(nc.status == "ok" and [x["max_tokens"] for x in CALLS] == [16_000, 32_000] and nc.retry["made"] is True,
          "note reader: the same retry (16,000 → 32,000) through the same function", f"{nc.status} {nc.retry}")
    row = CS._call_row(NSDOC, nc, run_id=None, origin="cascade", ensemble_config_id=None)
    check(row.metadata.get("retry", {}).get("made") is True and row.reasoning_effort == "low",
          "the note call row records the retry and the effort", f"{row.metadata.get('retry')}")

    section("[S4] a second empty answer → invalid 'the model used its whole output allowance' — never a third call")
    reset()
    SCRIPT[nano] = [empty("length"), empty("length"), answer(fund_answer())]
    c = await RT.read_one("model_2", nano, specs, msgs, ph, cat)
    check(c.status == "invalid" and c.error.startswith(NR.ALLOWANCE_EXHAUSTED) and len(CALLS) == 2,
          "fund reader: invalid with the reason; exactly 2 calls", f"{c.status} {c.error!r} calls={len(CALLS)}")
    reset()
    SCRIPT[nano] = [empty("length", out=None), empty(None, out=None)]
    nc = await NR.read(RN.reader_config("model_2", nano, catalog=cat), note_specs, "y" * 20_000, filer="F",
                       form_type="424B2", catalog=cat, spend=None)
    check(nc.status == "invalid" and nc.error.startswith(NR.ALLOWANCE_EXHAUSTED) and len(CALLS) == 2,
          "note reader: invalid with the reason (no finish_reason, output == allowance also counts); 2 calls",
          f"{nc.error!r}")
    reset()
    SCRIPT[nano] = [empty("stop", out=120)]
    c = await RT.read_one("model_2", nano, specs, msgs, ph, cat)
    check(c.status == "invalid" and c.error == "empty response" and len(CALLS) == 1 and c.retry is None,
          "an empty answer NOT caused by the allowance (finish 'stop', 120 of 15,000) is not retried", f"{c.error}")
    reset()
    SCRIPT[plain] = [empty("length", out=None), answer(fund_answer())]
    c = await RT.read_one("model_1", plain, specs, msgs, ph, cat)
    check(c.status == "ok" and [x["max_tokens"] for x in CALLS] == [3000, 6000],
          "a non-reasoning model's allowance is unchanged; its rare empty-from-length answer gets the same one retry",
          f"{[x['max_tokens'] for x in CALLS]}")

    section("[S5] one field failing never stops the other fields (one call reads every field)")
    reset()
    SCRIPT[nano] = [answer(fund_answer(expense_ratio_net_pct={"value": "not a number at all", "quote": "q"}))]
    c = await RT.read_one("model_2", nano, specs, msgs, ph, cat)
    check(c.status == "ok" and "expense_ratio_net_pct" not in c.fields and {"index_name", "inception_date"}
          <= set(c.fields), "field 1 fails its shape → dropped; fields 2..n kept, the call is ok", f"{list(c.fields)}")
    find("nano did not 'stop after one reading': a reader answers ALL fields in ONE call; a failed call is stored as "
         "ONE '__call__' row (IVV: gpt-5-mini 13 field rows of one call, gpt-5-nano one '__call__' row)")

    section("[S6] the agreement rule is unchanged: agree → accepted; single / disagree → needs review")
    reg = [{"field_key": k, "extraction_method": "model", "is_critical": False}
           for k in ("agree", "single", "disagree")]

    def rd(src, key, norm):
        return {"id": str(uuid.uuid4()), "status": "ok", "field_key": key, "source": src, "value": json.dumps(norm),
                "value_normalized": norm, "quote_verified": True, "value_in_quote": True, "source_quote": "q"}
    run = RT.ExtractionRun("r", "extracted", readings=[rd("model_1", "agree", "a"), rd("model_2", "agree", "a"),
                                                         rd("model_1", "single", "s"),
                                                         rd("model_1", "disagree", "x"), rd("model_2", "disagree", "y")])
    v = RT.compose_values(reg, run, [], {"instrument_class": "etf"})
    check(v["agree"]["confidence"] == "agreed" and not v["agree"]["needs_review"], "both readers agree → accepted")
    check(v["single"]["needs_review"] and "only one reader found a value" in v["single"]["review_reasons"],
          "one reader only → still needs review ('only one reader found a value')")
    check(v["disagree"]["needs_review"] and v["disagree"]["confidence"] == "disagreed", "disagree → needs review")


NSDOC = type("Doc", (), {"reference_filing_id": "00000000-0000-0000-0000-000000000000"})()


# ═══ DB helpers ═════════════════════════════════════════════════════════════
@asynccontextmanager
async def platform_tx(conn):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        yield conn


async def config_fingerprint(conn) -> tuple[str, dict]:
    async with platform_tx(conn):
        rows = await conn.fetch("SELECT * FROM public.ai_ensemble_configs WHERE task_key = ANY($1::text[]) "
                                "ORDER BY id", list(EXPECTED_CONFIGS))
    blob = json.dumps([dict(r) for r in rows], default=str, sort_keys=True)
    active = {r["task_key"]: (r["model_1"], r["model_1_budget_kind"], Decimal(r["model_1_budget_value"]),
                              r["model_2"], r["model_2_budget_kind"], Decimal(r["model_2_budget_value"]))
              for r in rows if r["is_active"]}
    return hashlib.sha256(blob.encode()).hexdigest(), active


async def teardown(conn) -> int:
    """By fixture name only: the ledger rows of the 'readerfix1-fx-*' deployments."""
    async with platform_tx(conn):
        st = await conn.execute("DELETE FROM public.ai_daily_usage WHERE deployment LIKE $1", FX + "%")
    return int(st.rsplit(" ", 1)[-1])


async def leftovers(conn) -> int:
    async with platform_tx(conn):
        n = await conn.fetchval("SELECT count(*) FROM public.ai_daily_usage WHERE deployment LIKE $1", FX + "%")
        n += await conn.fetchval("SELECT count(*) FROM portfolio.replica_term_readings WHERE prompt_version LIKE "
                                 "'readerfix1.fx%'")
        n += await conn.fetchval("SELECT count(*) FROM portfolio.note_term_readings WHERE deployment_name LIKE $1",
                                 FX + "%")
    return int(n)


# ═══ B — the REAL budget ledger with the larger reservation ══════════════════
async def y_budget(conn, pool):
    section("[B] the daily budget is never exceeded with the larger reservation (real ai_daily_usage, fixture models)")
    specs = fund_specs()
    msgs, ph = RT.build_messages(specs, "z" * 29_000, fund_label="FX", form_type="485BPOS")
    chars = sum(len(m["content"]) for m in msgs)
    tin = (chars + 3) // 4
    first, retry = tin + 15_000, tin + 30_000
    for tag, budget, retry_fits in (("a", first + retry, True), ("b", first + retry - 1, False)):
        p = f"{FX}{tag}-"
        cat = catalog(p)
        ens = {"id": None, "model_1": f"{p}mini", "model_2": f"{p}nano",
               "model_1_budget_kind": AB.TOKENS_PER_DAY, "model_1_budget_value": Decimal(10_000_000),
               "model_2_budget_kind": AB.TOKENS_PER_DAY, "model_2_budget_value": Decimal(budget)}
        reset()
        SCRIPT[f"{p}nano"] = [empty("length"), answer(fund_answer(), out=2000, reasoning=1500)]
        SCRIPT[f"{p}mini"] = [answer(fund_answer(), out=2400, reasoning=1900)]
        async with AB.job_scope(RT.TASK_KEY, ens, catalog=cat, pool=pool):
            calls = await RT.read_pair(ens, [f"{p}mini", f"{p}nano"], specs, msgs, ph, cat)
        async with platform_tx(conn):
            u = await conn.fetchrow("SELECT * FROM public.ai_daily_usage WHERE deployment = $1 AND budget_day = "
                                    f"{AB.BUDGET_DAY_SQL}", f"{p}nano")
        used = int(u["input_tokens"]) + int(u["output_tokens"]) + int(u["reserved_tokens"])
        nano_call = calls[1]
        if retry_fits:
            check(nano_call.status == "ok" and nano_call.retry["made"] is True,
                  f"budget {budget:,} = first {first:,} + retry {retry:,}: the retry is made and answers")
            check(int(u["input_tokens"]) == 2 * tin and int(u["output_tokens"]) == 15_000 + 2000
                  and int(u["calls"]) == 2, "the ledger records BOTH attempts (input ×2, output 15,000 + 2,000)",
                  f"{dict(u)}")
        else:
            check(nano_call.status == "invalid" and nano_call.error.startswith(NR.ALLOWANCE_EXHAUSTED)
                  and "BudgetReached" in nano_call.error and nano_call.retry["made"] is False,
                  f"budget {budget:,} (one token short): the retry is NOT made — invalid with the reason",
                  f"{nano_call.error[:140]}")
            check(int(u["calls"]) == 1 and len([c for c in CALLS if c["model"] == f"{p}nano"]) == 1,
                  "exactly one nano call reached the (fake) provider", f"{dict(u)}")
        check(used <= budget and int(u["reserved_tokens"]) == 0,
              f"used {used:,} ≤ budget {budget:,}; nothing left reserved")
        check(calls[0].status == "ok", "Model 1 unaffected by Model 2's retry")
    find("reservation per fund call at the new allowance: ceil(chars/4) + 15,000 (gpt-5); the retry reserves "
         "ceil(chars/4) + 30,000 on its own — settled to the real usage afterwards")


# ═══ R — re-read eligibility, once (ROLLED-BACK transaction) ══════════════════
class _Rollback(Exception):
    pass


async def y_reread(conn):
    section("[R] re-read eligibility — funds once (both readers), notes once (Model 2 only); all rolled back")
    async with platform_tx(conn):
        ivv = await conn.fetchrow(
            "SELECT r.fund_global_security_id, r.source_document_id, r.ensemble_config_id, e.model_1, e.model_2 "
            "FROM portfolio.replica_term_readings r JOIN public.ai_ensemble_configs e ON e.id = r.ensemble_config_id "
            "WHERE r.source = 'model_1' AND r.system_to IS NULL ORDER BY r.created_at DESC LIMIT 1")
        rf = await conn.fetchval("SELECT id FROM portfolio.reference_filings WHERE retention_classification = "
                                 "'public_reference' ORDER BY id LIMIT 1")
        ncfg = await conn.fetchval("SELECT id FROM public.ai_ensemble_configs WHERE task_key = "
                                   "'note_terms_extraction' AND is_active")
    if ivv is None or rf is None:
        find("no live fund reading / reference filing to anchor the fixtures — section skipped")
        return
    fund, m1, m2 = ivv["fund_global_security_id"], ivv["model_1"], ivv["model_2"]
    t0 = datetime.now(timezone.utc)
    due_sql = f"SELECT {RT.reread_due_sql('$1::uuid', '$2', '$3', '$4')}"

    async def fund_row(c, run_id, src, key, status, at, meta=None, error=None):
        await c.execute(
            "INSERT INTO portfolio.replica_term_readings (fund_global_security_id, source_document_id, "
            "extraction_run_id, field_key, source, status, error, deployment_name, ensemble_config_id, "
            "prompt_version, metadata, created_at) VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, "
            "$12)", fund, ivv["source_document_id"], run_id, key, src, status, error, m1 if src == "model_1" else m2,
            ivv["ensemble_config_id"], FX_PROMPT, json.dumps(meta or {}), at)

    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            due = lambda: conn.fetchval(due_sql, fund, FX_PROMPT, m1, m2)  # noqa: E731
            check(await due() is False, "a fund with no reading under this prompt is not 're-read due'")
            a = str(uuid.uuid4())
            await fund_row(conn, a, "model_1", "index_name", "ok", t0)
            await fund_row(conn, a, "model_1", "fund_name", "ok", t0)
            sp = conn.transaction()          # a savepoint, rolled back after the negative case
            await sp.start()
            await fund_row(conn, a, "model_2", "index_name", "ok", t0)
            check(await due() is False, "both readers ok → NOT due (no re-read of a good run)")
            await sp.rollback()
            await fund_row(conn, a, "model_2", "__call__", "invalid", t0, error="empty response")
            check(await due() is True, "Model 2's call invalid (the IVV shape) → due for ONE re-read")
            # the queue the nightly uses selects it (same SQL fragment), with its real prompt patched to the fixture's
            orig_tx, orig_pv = AB._platform_tx, RT.PROMPT_VERSION

            @asynccontextmanager
            async def same_conn(pool):
                async with conn.transaction():
                    yield conn
            AB._platform_tx, RT.PROMPT_VERSION = same_conn, FX_PROMPT
            try:
                q1 = {i["id"] for i in await DR._replica_queue(None, limit=10_000, include_test_orgs=True,
                                                              cfg={"model_1": m1, "model_2": m2})}
                b = str(uuid.uuid4())
                for src in ("model_1", "model_2"):
                    await fund_row(conn, b, src, "__call__" if src == "model_2" else "index_name",
                                   "invalid" if src == "model_2" else "ok", t0 + timedelta(seconds=5),
                                   meta={"reread": True}, error="the model used its whole output allowance")
                q2 = {i["id"] for i in await DR._replica_queue(None, limit=10_000, include_test_orgs=True,
                                                              cfg={"model_1": m1, "model_2": m2})}
            finally:
                AB._platform_tx, RT.PROMPT_VERSION = orig_tx, orig_pv
            check(str(fund) in q1, "the nightly replica queue includes the due fund (no manual step)")
            check(await due() is False and str(fund) not in q2,
                  "after its re-read (metadata.reread) — even if Model 2 failed AGAIN — never due again (ONCE)")

            # notes — Model 2 only
            run_id = await NSTORE.create_run(conn, run_kind="cascade", spend_cap_usd=None, config={"fx": True},
                                             ensemble_config_id=ncfg)
            n1, n2 = f"{FX}mini", f"{FX}nano"
            call1 = str(uuid.uuid4())
            base = dict(reference_filing_id=str(rf), run_id=run_id, ensemble_config_id=str(ncfg))
            await NSTORE.insert_readings(conn, [
                NSTORE.ReadingRow(field_key=NSTORE.CALL_FIELD, source="model_1", status="ok", deployment_name=n1,
                                  call_id=call1, input_tokens=17000, output_tokens=5000, **base),
                NSTORE.ReadingRow(field_key="cap_pct", source="model_1", value=12.5, value_normalized="12.5",
                                  source_quote="Cap: 12.50%", deployment_name=n1, call_id=call1, **base),
                NSTORE.ReadingRow(field_key=NSTORE.CALL_FIELD, source="model_2", status="invalid",
                                  error="empty response", deployment_name=n2, **base)])
            due_n = await DR.note_reread_due(conn, n1, n2)
            check(due_n == [str(rf)], "a note with Model 1 ok + Model 2 empty in the same run → due (Model 2 only)",
                  f"{due_n}")
            c1 = await CS.prior_model_1_call(conn, str(rf), n1)
            check(c1 is not None and c1.call_id == call1 and c1.fields == {"cap_pct": {"value": 12.5,
                  "quote": "Cap: 12.50%"}} and c1.cost_usd == 0.0,
                  "Model 1's call is rebuilt from its stored readings (no Model 1 tokens; cost 0 here)", f"{c1}")
            AB._platform_tx = same_conn
            try:
                q = await DR._note_queue(None, limit=5, cfg={"model_1": n1, "model_2": n2})
            finally:
                AB._platform_tx = orig_tx
            check(bool(q) and q[0] == {"id": str(rf), "issuer": None, "reread_model_2": True},
                  "the nightly note queue puts the Model-2-only re-read FIRST (ahead of new notes)", f"{q[:2]}")
            await NSTORE.insert_readings(conn, [NSTORE.ReadingRow(
                field_key=NSTORE.CALL_FIELD, source="model_2", status="invalid", error="x", deployment_name=n2,
                metadata={CS.REREAD_MODEL_2: True}, **base)])
            check(await DR.note_reread_due(conn, n1, n2) == [], "after its re-read (marker) — never due again (ONCE)")
            raise _Rollback()
    except _Rollback:
        pass
    check(await leftovers(conn) == 0, "the transaction was rolled back — zero fixture readings remain")


# ═══ N — the real cascade's Model-2-only re-read ═════════════════════════════
async def y_note_cascade(conn):
    section("[N] the real note cascade: a Model-2-only re-read calls Model 2 only and rewrites no Model 1 reading")
    from services.note_extraction import documents as DOCS
    async with platform_tx(conn):
        specs = NS.build_field_specs(await NS.load_registry_rows(conn))
    rspecs = CS.reader_specs(specs)
    html = ("<html><body><p>Pricing supplement. The notes are linked to the S&amp;P 500 Index. Pricing date: October "
            "1, 2026. Maturity date: October 1, 2027. Principal amount: $1,000 per note. Cap: 12.50%.</p></body></html>")
    doc = DOCS.document_from_html(html, reference_filing_id=str(uuid.uuid4()), form_type="424B2", filer_name="FX")
    cat = catalog()
    n1, n2 = f"{FX}mini", f"{FX}nano"
    slots = CS.EnsembleSlots(model_1=RN.reader_config("model_1", n1, catalog=cat),
                             model_2=RN.reader_config("model_2", n2, catalog=cat), jev_route=None, escalation=None)
    keys = [s.key for s in rspecs]
    m1_fields = {k: {"value": None, "quote": None} for k in keys}
    prior = NR.ReaderCall(slot="model_1", deployment=n1, status="ok", fields=m1_fields, cost_usd=0.0,
                          call_id="fx-model-1-call")
    reset()
    SCRIPT[n2] = [empty("length"), answer(json.dumps(m1_fields))]
    out = await CS.run_note(doc, specs, slots, catalog=cat, spend=None, run_id=None, prior_model_1=prior)
    models = [c["model"] for c in CALLS]
    check(models and set(models) == {n2}, "only Model 2 was called (the empty answer + its one retry)", f"{models}")
    check(not [r for r in out.readings if r.source == "model_1"], "no Model 1 reading is written again (kept as-is)")
    call_rows = [r for r in out.readings if r.field_key == NSTORE.CALL_FIELD and r.source == "model_2"]
    check(len(call_rows) == 1 and call_rows[0].metadata.get(CS.REREAD_MODEL_2) is True
          and call_rows[0].metadata.get("model_1_call_id") == "fx-model-1-call"
          and call_rows[0].metadata.get("retry", {}).get("made") is True,
          "Model 2's call row records the re-read, the Model 1 call it pairs with, and the retry",
          f"{call_rows[0].metadata if call_rows else None}")
    check(out.status != "failed", "the note is compared and staged from the pair (status not 'failed')",
          f"{out.status} {out.status_reason}")


# ═══ L — READ-ONLY live checks ══════════════════════════════════════════════
async def y_live(conn, fp_before):
    section("[L] READ-ONLY live: ensemble configs unchanged; IVV due; notes affected; IVV prompt measured")
    fp, active = await config_fingerprint(conn)
    check(active == EXPECTED_CONFIGS, "the active configs are exactly the stated models + budgets", f"{active}")
    check(fp == fp_before, "ensemble configs fingerprint unchanged across this run", fp[:12])
    async with platform_tx(conn):
        ivv = await conn.fetchrow(
            "SELECT s.global_security_id, d.extracted_text, d.form_type FROM portfolio.replica_securities s "
            "JOIN portfolio.replica_source_documents d ON d.fund_global_security_id = s.global_security_id "
            "WHERE upper(s.ticker) = 'IVV' AND s.system_to IS NULL ORDER BY d.created_at DESC LIMIT 1")
        notes = await DR.note_reread_due(conn, "gpt-5-mini", "gpt-5-nano")
        due = await conn.fetchval(f"SELECT {RT.reread_due_sql('$1::uuid', '$2', '$3', '$4')}",
                                  ivv["global_security_id"], RT.PROMPT_VERSION, "gpt-5-mini",
                                  "gpt-5-nano") if ivv else None
    find(f"notes with Model 1 ok + Model 2 empty/invalid (live, read-only): {len(notes)}")
    if ivv is None:
        find("IVV has no stored prospectus here — not measured")
        return
    check(due is True, "IVV is due for its ONE re-read on the next nightly / extraction (no manual step)")
    async with platform_tx(conn):
        reg = RT.build_specs(await RT.load_registry(conn, "etf"))
    window, _ = RT.doc_window(ivv["extracted_text"] or "", "IVV")
    msgs, _ = RT.build_messages(reg, window, fund_label="IVV", form_type=ivv["form_type"])
    chars = sum(len(m["content"]) for m in msgs)
    est = (chars + 3) // 4
    find(f"IVV's stored prompt: {chars:,} chars ≈ {est:,} tokens (provider counted 7,408); per-call reservation "
         f"now {est + 15_000:,} (was {est + 3000:,}); retry {est + 30_000:,}; 250,000/day ⇒ ≥ "
         f"{250_000 // (est + 15_000)} funds/day by reservation, ≥ {250_000 // (2 * est + 45_000)} if every call retried")


# ═══ driver ═════════════════════════════════════════════════════════════════
def regression():
    section("[X] fast regressions (each once, nested, its own TOTAL line)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=600, env_extra={"SKIP_BUILD": "1"})
        check(bool(t.ok), f"{script}: 0 FAIL (its own final TOTAL line and exit code)", f"{t.detail()}")


async def main_async() -> int:
    await self_checks()
    if "--self-checks-only" in sys.argv:
        return 0
    dsn, how = await admin_dsn()
    if not dsn:
        check(False, "a working DATABASE_URL", how)
        return 1
    os.environ["DATABASE_URL"] = dsn
    conn = await connect(dsn)
    op_dsn, _ = await operator_dsn()
    op = await connect(op_dsn) if op_dsn else conn
    try:
        if "--cleanup-only" in sys.argv:
            n = await teardown(op)
            check(await leftovers(conn) == 0, "cleanup-only: zero fixture rows remain", f"removed {n}")
            return 0
        only = sys.argv[sys.argv.index("--section") + 1].lower() if "--section" in sys.argv else None
        want = lambda k: only is None or only == k  # noqa: E731
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the app connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
        await teardown(op)
        check(await leftovers(conn) == 0, "zero fixture rows before the run")
        fp_before, _ = await config_fingerprint(conn)
        pool = await asyncpg.create_pool(dsn, statement_cache_size=0, min_size=1, max_size=4, ssl="require")
        try:
            for k, fn, args in (("b", y_budget, (conn, pool)), ("r", y_reread, (conn,)), ("n", y_note_cascade, (conn,))):
                if want(k):
                    try:
                        await fn(*args)
                    except Exception as exc:  # noqa: BLE001
                        check(False, f"section {k} raised", f"{type(exc).__name__}: {exc}")
                        traceback.print_exc()
        finally:
            await pool.close()
            section("Teardown — by fixture name")
            print(f"[INFO] removed {await teardown(op)} fixture ledger rows")
            check(await leftovers(conn) == 0, "zero fixture rows remain")
        if want("l"):
            await y_live(conn, fp_before)
    finally:
        await conn.close()
        if op is not conn:
            await op.close()
    if only is None and os.environ.get("VERIFY_NESTED") != "1":
        regression()
    return 0


def main() -> int:
    try:
        code = asyncio.run(main_async())
    except Exception as exc:  # noqa: BLE001
        check(False, "the verify ran to completion", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        code = 1
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
