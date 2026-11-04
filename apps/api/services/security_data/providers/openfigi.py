"""openfigi — FIGI, security type and ticker for a CUSIP or a ticker (api.openfigi.com/v3/mapping).

LIMITS (response headers 2026-11-04: ``ratelimit-policy: 25;w=60``): without OPENFIGI_API_KEY 10 jobs per request
and 25 requests per minute; with it (header ``X-OPENFIGI-APIKEY``) 100 jobs per request and 25 requests per 6 s.
This module throttles to stay under the limit, waits out a 429 once, and counts every request against the
provider's daily call budget.

A job is {"idType": "ID_CUSIP" | "TICKER", "idValue": …, "exchCode": "US"}: the US composite, so one FIGI per
security (the composite FIGI), not one per venue. An answer is a list aligned with the jobs: {"data": [hits…]}
or {"warning": "No identifier found."} or {"error": …}.

"Only identifiers not mapped in the last 30 days" — ``due()`` reads portfolio.openfigi_mappings (every answer,
hit or miss, is recorded there by ``record()``)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from services import security_master_symbols as SYM
from services.security_data.providers.reference_common import (ReferenceProvider, SourceRecord, SourceTransport,
                                                              sleep)

KEY = "openfigi"
URL = "https://api.openfigi.com/v3/mapping"
REMAP_AFTER_DAYS = 30
KEYLESS = {"jobs": 10, "interval": 2.5}     # 25 requests / 60 s
KEYED = {"jobs": 100, "interval": 0.25}     # 25 requests / 6 s


class OpenFigiProvider(ReferenceProvider):
    def __init__(self) -> None:
        super().__init__(KEY)


@dataclass
class Answer:
    job: dict
    hit: dict | None
    hits: int
    warning: str | None
    ok: bool


def api_key(environ: Mapping[str, str] | None = None) -> str | None:
    v = ((environ if environ is not None else os.environ).get("OPENFIGI_API_KEY") or "").strip()
    return v or None


def limits(key: str | None) -> dict:
    return KEYED if key else KEYLESS


def cusip_job(cusip: str) -> dict:
    return {"idType": "ID_CUSIP", "idValue": cusip, "exchCode": "US"}


def ticker_job(canonical: str) -> dict | None:
    t = SYM.to_openfigi_ticker(canonical)
    return {"idType": "TICKER", "idValue": t, "exchCode": "US"} if t else None


def pick(hits: list[dict]) -> dict | None:
    """The composite listing: a hit whose figi IS its compositeFIGI, else the first."""
    if not hits:
        return None
    for h in hits:
        if h.get("figi") and h.get("figi") == h.get("compositeFIGI"):
            return h
    return hits[0]


def parse_response(jobs: list[dict], body) -> list[Answer]:
    if not isinstance(body, list) or len(body) != len(jobs):
        raise ValueError(f"OpenFIGI answered {type(body).__name__} of length "
                         f"{len(body) if isinstance(body, list) else '?'} for {len(jobs)} jobs")
    out = []
    for job, item in zip(jobs, body):
        item = item if isinstance(item, dict) else {}
        hits = item.get("data") or []
        if hits:
            out.append(Answer(job, pick(hits), len(hits), None, True))
        else:
            out.append(Answer(job, None, 0, str(item.get("warning") or item.get("error") or "no answer"),
                              "error" not in item))
    return out


def to_record(ans: Answer, *, cusip: str | None = None, as_of=None) -> SourceRecord | None:
    """A hit as a source record: FIGI (composite), canonical ticker, the US composite as exchange, the type."""
    h = ans.hit
    if not h:
        return None
    return SourceRecord(KEY, ticker=SYM.normalize_symbol(h.get("ticker")), name=(h.get("name") or "").strip() or None,
                        exchange="US", security_type=SYM.type_from_figi(h), cusip=cusip,
                        figi=h.get("compositeFIGI") or h.get("figi"), as_of=as_of, raw_symbol=h.get("ticker"))


async def map_jobs(transport: SourceTransport, jobs: list[dict], *, key: str | None = None, budget=None,
                   max_requests: int | None = None, throttle: bool = True) -> tuple[list[Answer], int]:
    """Answers for as many jobs as the budget and ``max_requests`` allow, in order. Returns (answers, requests).
    Jobs not reached are simply absent from the answers (the caller leaves them pending)."""
    lim = limits(key)
    headers = {"X-OPENFIGI-APIKEY": key} if key else {}
    answers: list[Answer] = []
    requests = 0
    for i in range(0, len(jobs), lim["jobs"]):
        if max_requests is not None and requests >= max_requests:
            break
        chunk = jobs[i:i + lim["jobs"]]
        if budget is not None:
            await budget.take()
        if throttle and requests:
            await sleep(lim["interval"])
        status, hdrs, body = await transport.post_json(URL, chunk, headers)
        requests += 1
        if status == 429:
            wait = 60.0
            try:
                wait = float((hdrs or {}).get("ratelimit-reset") or (hdrs or {}).get("Retry-After") or 60)
            except (TypeError, ValueError):
                pass
            if throttle:
                await sleep(min(wait, 60.0))
            if budget is not None:
                await budget.take()
            status, hdrs, body = await transport.post_json(URL, chunk, headers)
            requests += 1
        if status != 200:
            raise RuntimeError(f"OpenFIGI HTTP {status}: {str(body)[:200]}")
        answers.extend(parse_response(chunk, body))
    return answers, requests


# ── the 30-day memory ───────────────────────────────────────────────────────
async def due(conn, id_type: str, values: list[str], *, days: int = REMAP_AFTER_DAYS) -> list[str]:
    """The values NOT answered (hit or miss) in the last ``days`` days."""
    if not values:
        return []
    recent = {r["id_value"] for r in await conn.fetch(
        "SELECT DISTINCT id_value FROM portfolio.openfigi_mappings WHERE id_type = $1 AND id_value = ANY($2::text[]) "
        "AND mapped_at > now() - make_interval(days => $3::int) AND valid_to IS NULL AND system_to IS NULL",
        id_type, list(values), int(days))}
    return [v for v in values if v not in recent]


async def record(conn, answers: list[Answer], *, run_id=None) -> int:
    """One insert-only row per answer (on a connection already in the platform write context)."""
    import json

    if not answers:
        return 0
    await conn.executemany(
        "INSERT INTO portfolio.openfigi_mappings (id_type, id_value, exch_code, ok, hits, result, warning, run_id) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::uuid)",
        [(a.job["idType"], a.job["idValue"], a.job.get("exchCode") or "", a.ok, a.hits,
          json.dumps(a.hit) if a.hit else None, a.warning, run_id) for a in answers])
    return len(answers)
