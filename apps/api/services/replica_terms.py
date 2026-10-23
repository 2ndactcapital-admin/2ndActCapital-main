"""Replication Studio (rep1) — the registry, fund terms extraction, and the
fix/edit flow over terms versions.

THE TWO KINDS OF EDIT — kept apart on purpose
──────────────────────────────────────────────────────────────────────────────
* A CORRECTION says the read was wrong: the fund's real value is X. It writes
  ``document_field_corrections`` (target_type 'replica_terms', target_id = the
  terms version), sets the field's origin to 'corrected', and creates NO
  departure — we still replicate the fund, we just read it right now.
* A DELIBERATE CHANGE says we choose to differ from the fund. It needs a
  reason, sets origin 'deliberate', and adds a DEPARTURE {field, fund value,
  our value, reason} — shown at the top of the Terms tab in plain words.
Neither ever edits an approved version (409): both edit only the live draft. A
change with no draft open starts the next version from the approved one.

MAKER-CHECKER. Everyone who corrects or changes a field of a draft is recorded
in ``maker_ids``. Approve / reject is refused (403, naming the rule) for any of
them; the CHECK ``replica_terms_versions_maker_checker_chk`` refuses it in the
database as well. Approving supersedes the previous approved version — one
approved version is live per entry (partial unique index), and an approved row
is never edited (trigger ``replica_terms_version_guard``).

BI-TEMPORAL. A registry entry and a terms version are FK targets, so they are
archived on the SYSTEM axis before every change (:func:`_archive`): the
outgoing state is copied to a fresh id with ``archived_from_id`` and
``system_to = now()``; the live row keeps its id.

WORKFLOW + AUDIT (Task 1e). The workflow engine creates User Tasks only from a
published BPMN version; there is no service to open an ad-hoc task for a human
action. So, for every human action (add, correct, change, approve, reject,
refresh): one append-only ``portfolio.replica_terms_actions`` row + one
``audit_log`` row, in the SAME transaction as the change; then a
``domain_events`` row (``services.domain_events.publish_event``, event_type
``replication.<action>``, source_type ``replica_terms_action``, source_id =
the action id) — the platform's workflow event, which also starts any
workflow an org has subscribed to that event type.

APPROVAL IS A GOVERNED HUMAN TASK (rep3). "Approval needed" is one
``services.human_tasks`` task (kind ``replica_terms_approval``, eligible
permission ``approve_replication``, subject = the terms version), opened when a
draft is created or edited (if none is open for it) — the engine notifies every
holder through its own member_todos. Approve / reject completes that task
through the engine (``complete_task``); the version change itself runs in the
kind's ``on_complete`` callback, idempotently, and :func:`sync_terms_task_outcome`
re-applies a decision the engine recorded if the subject update ever failed
after it. ``replica_terms_actions`` stays the domain log. The task kind is NOT
engine-level maker-checker: rep1's makers are everyone in ``maker_ids`` (who
corrected or changed a field), which the engine's single ``started_by`` cannot
express — so the refusal stays rep1's own (same text, same 403) and the CHECK
``replica_terms_versions_maker_checker_chk`` still backs it.

NO CLAUDE. Extraction calls the platform's LiteLLM proxy through
``services.note_extraction.proxy.chat`` (one attempt per named deployment, no
fallback, provider-reported model recorded) with the slots of the active
``ai_ensemble_configs`` row for task 'replica_terms_extraction' (else the
note-extraction ensemble). Any slot whose name, upstream or reported model is
a Claude/Anthropic model is refused before — and after — the call.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from services import edgar_fetch, html_text, storage
from services import human_tasks as HT
from services import replica_holdings as H
from services.note_extraction import proxy
from services.note_extraction import schema as NS
from services.note_extraction.quote_match import TextIndex

T_ENTRY = "portfolio.replica_securities"
T_VER = "portfolio.replica_terms_versions"
T_ACT = "portfolio.replica_terms_actions"
T_REG = "portfolio.replica_terms_field_registry"
T_READ = "portfolio.replica_term_readings"
T_DOC = "portfolio.replica_source_documents"
T_CLASS = "portfolio.replica_instrument_classes"

TASK_KEY = "replica_terms_extraction"
FALLBACK_TASK_KEY = "note_terms_extraction"
PROMPT_VERSION = "replica.terms.reader.v1"
DOC_WINDOW_CHARS = 60_000

ORIGINS = ("extracted", "corrected", "deliberate")
ORIGIN_LABELS = {"extracted": "Extracted", "corrected": "Corrected", "deliberate": "Deliberate change"}
VERSION_STATUSES = ("draft", "approved", "superseded", "rejected")
VERSION_STATUS_LABELS = {"draft": "Draft", "approved": "Approved", "superseded": "Superseded",
                         "rejected": "Rejected"}
ENTRY_STATUSES = ("draft", "active", "retired")
ENTRY_STATUS_LABELS = {"draft": "Draft", "active": "Active", "retired": "Retired"}
CONFIDENCE_LABELS = {"agreed": "Both readers agree", "single": "One reader only",
                     "disagreed": "Readers disagree", "not_stated": "Not stated", "data": "From data",
                     "human": "Set by staff", "not_run": "Not extracted"}

TODO_SOURCE_APPROVAL = "replica_terms_approval"   # pre-rep3 drafts' todos (closed on decide)
TERMS_TASK_KIND = "replica_terms_approval"
TERMS_TASK_SUBJECT = "replica_terms_version"
REJECT_WITHOUT_NOTE = "(rejected without a note)"
TODO_SOURCE_HOLDINGS = "replica_holdings_review"
TODO_CATEGORY = "replication"
APPROVE_PERMISSION = "approve_replication"
MANAGE_PERMISSION = "manage_replication"
MAKER_CHECKER_RULE = ("Maker-checker: you corrected or changed a field in this draft, so you cannot approve or "
                      "reject it — another approver must.")

_CLAUDE_RE = re.compile(r"claude|anthropic", re.I)


class ReplicaTermsError(ValueError):
    """A request the caller can fix (422)."""


class ReplicaNotFound(LookupError):
    """Not visible to this org (404) — never says whether it exists elsewhere."""


class ReplicaConflict(RuntimeError):
    """The target is not in a state that takes this action (409)."""


class MakerCheckerViolation(PermissionError):
    """The approver is a maker of the draft (403)."""


class ExtractionBlocked(RuntimeError):
    """Extraction cannot run (no ensemble / a Claude slot / no document)."""


def display_name_for(ticker: str) -> str:
    return f"Holdings of {ticker.upper()}"


def disclosure_for(ticker: str) -> str:
    return (f"Replicates the published holdings of {ticker.upper()}; not affiliated with or endorsed by the "
            f"fund's sponsor or index provider.")


def _dumps(obj) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


# ── archive (system axis) ───────────────────────────────────────────────────
async def _archive(conn, table: str, row_id) -> str:
    archived = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL
            RETURNING id""", str(row_id))
    if archived is None:
        raise ReplicaConflict(f"{table} row {row_id} is not live — refusing to edit it")
    return str(archived)


# ── registry ────────────────────────────────────────────────────────────────
async def instrument_classes(conn) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT key, label, description, enabled FROM {T_CLASS} WHERE valid_to IS NULL AND system_to IS NULL "
        "ORDER BY sort_order, key")]


async def load_entry(conn, org_id, entry_id) -> dict:
    try:
        uuid.UUID(str(entry_id))
    except ValueError as exc:
        raise ReplicaNotFound("registry entry not found") from exc
    row = await conn.fetchrow(
        f"SELECT * FROM {T_ENTRY} WHERE id = $1::uuid AND org_id = $2::uuid AND valid_to IS NULL "
        "AND system_to IS NULL", str(entry_id), str(org_id))
    if row is None:
        raise ReplicaNotFound("registry entry not found")
    return dict(row)


@dataclass
class ActionRef:
    id: str
    action: str
    org_id: str
    payload: dict = field(default_factory=dict)
    actor_id: str | None = None


async def _record_action(conn, *, org_id, entry_id, version_id, action: str, actor_id, field_key=None,
                         before=None, after=None, reason=None, correction_id=None, detail=None) -> ActionRef:
    """The action row + its audit_log row, inside the caller's transaction."""
    aid = await conn.fetchval(
        f"""INSERT INTO {T_ACT} (org_id, replica_security_id, terms_version_id, action, field_key, before_value,
                after_value, reason, actor_id, correction_id, detail)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7::jsonb, $8, $9::uuid, $10::uuid, $11::jsonb)
            RETURNING id""",
        str(org_id), str(entry_id), str(version_id) if version_id else None, action, field_key,
        _dumps(before) if before is not None else None, _dumps(after) if after is not None else None, reason,
        str(actor_id) if actor_id else None, str(correction_id) if correction_id else None, _dumps(detail or {}))
    payload = {"replica_security_id": str(entry_id), "terms_version_id": str(version_id) if version_id else None,
               "field_key": field_key, "reason": reason, **(detail or {})}
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(actor_id) if actor_id else None, f"replication.{action}", "replica_terms_action",
        str(aid), _dumps({**payload, "before": before, "after": after}))
    return ActionRef(str(aid), action, str(org_id), payload, str(actor_id) if actor_id else None)


async def publish_actions(pool, refs: list[ActionRef]) -> list[dict]:
    """After commit: one domain event per action (never raises)."""
    from services.domain_events import publish_event

    out = []
    for ref in refs:
        out.append(await publish_event(pool, ref.org_id, f"replication.{ref.action}", "replica_terms_action",
                                       ref.id, ref.payload, created_by=ref.actor_id))
    return out


async def _approvers(conn, org_id, permission: str = APPROVE_PERMISSION) -> list[str]:
    rows = await conn.fetch(
        """SELECT DISTINCT u.id FROM users u
           JOIN user_roles ur ON ur.user_id = u.id
           JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
           JOIN role_permissions rp ON rp.role_id = ur.role_id
           JOIN permissions p ON p.id = rp.permission_id
           WHERE u.org_id = $1::uuid AND p.name = $2 AND u.is_active
             AND COALESCE(u.status, 'active') = 'active'""", str(org_id), permission)
    return [str(r["id"]) for r in rows]


async def open_todos(conn, *, org_id, recipients: list[str], source: str, related_type: str, related_id,
                     title: str, detail: str, action_key: str, priority: int = 20) -> list[str]:
    """Open (or re-open) one todo per recipient — idempotent per
    (user, org, source, related)."""
    ids = []
    for uid in recipients:
        existing = await conn.fetchval(
            "SELECT id FROM member_todos WHERE user_id = $1::uuid AND org_id = $2::uuid AND source = $3 "
            "AND related_type = $4 AND related_id = $5::uuid LIMIT 1", uid, str(org_id), source, related_type,
            str(related_id))
        if existing:
            await conn.execute("UPDATE member_todos SET title = $2, detail = $3, status = 'open', updated_at = now() "
                               "WHERE id = $1", existing, title, detail)
            ids.append(str(existing))
        else:
            ids.append(str(await conn.fetchval(
                """INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, related_id,
                       title, detail, action_key, priority, status)
                   VALUES ($1::uuid, $2::uuid, 'actual', $3, $4, $5, $6::uuid, $7, $8, $9, $10, 'open') RETURNING id""",
                str(org_id), uid, TODO_CATEGORY, source, related_type, str(related_id), title, detail, action_key,
                priority)))
    return ids


async def close_todos(conn, *, org_id, source: str, related_id) -> int:
    status = await conn.execute(
        "UPDATE member_todos SET status = 'done', updated_at = now() WHERE org_id = $1::uuid AND source = $2 "
        "AND related_id = $3::uuid AND status = 'open'", str(org_id), source, str(related_id))
    return int(status.rsplit(" ", 1)[-1])


async def open_terms_task(conn, org_id, version_id) -> dict | None:
    rows = await HT.list_tasks(conn, org_id, status="open", kind=TERMS_TASK_KIND, subject_type=TERMS_TASK_SUBJECT,
                               subject_id=version_id, limit=1)
    return rows[0] if rows else None


async def ensure_approval_task(pool, *, org_id, entry: dict, version: dict, actor_id) -> dict | None:
    """The version's open approval task, opened now if there is none (the
    engine notifies every approve_replication holder). After the caller's
    transaction has committed — the engine uses its own connections."""
    async with pool.acquire() as conn:
        task = await open_terms_task(conn, org_id, version["id"])
    if task is not None:
        return task
    if actor_id is None:
        return None
    try:
        return await HT.open_task(
            pool, org_id=org_id, kind=TERMS_TASK_KIND, subject_type=TERMS_TASK_SUBJECT, subject_id=version["id"],
            maker_id=actor_id, eligible_permission=APPROVE_PERMISSION,
            title=f"Approve terms: {entry['display_name']} v{version['version_no']}",
            payload={"replica_security_id": str(entry["id"]), "version_no": version["version_no"],
                     "ticker": entry.get("ticker")},
            action_path=f"/portfolio/replication/{entry['id']}")
    except HT.HumanTaskConflict:
        async with pool.acquire() as conn:
            return await open_terms_task(conn, org_id, version["id"])


async def create_entry(pool, *, org_id, actor_id, ticker: str | None, cusip: str | None, instrument_class: str,
                       http: httpx.AsyncClient, holdings_source_key: str | None = None,
                       fund_name: str | None = None, issuer: str | None = None) -> tuple[dict, list[ActionRef]]:
    async with pool.acquire() as conn:
        classes = {c["key"]: c for c in await instrument_classes(conn)}
    cls = classes.get(instrument_class)
    if cls is None:
        raise ReplicaTermsError(f"unknown instrument class {instrument_class!r}")
    if not cls["enabled"]:
        raise ReplicaTermsError(f"instrument class {cls['label']!r} is not supported yet")
    fund = await H.resolve_fund(pool, ticker=ticker, cusip=cusip, http=http, fund_name=fund_name)
    sec = fund.sec or {}
    async with pool.acquire() as conn:
        source = await H.choose_source(conn, ticker=fund.ticker, series_id=sec.get("series_id"),
                                       override_key=holdings_source_key)
        dup = await conn.fetchval(
            f"SELECT id FROM {T_ENTRY} WHERE org_id = $1::uuid AND global_security_id = $2::uuid "
            "AND valid_to IS NULL AND system_to IS NULL", str(org_id), fund.global_security_id)
        if dup:
            raise ReplicaConflict(f"{display_name_for(fund.ticker)} is already in the registry")
        entry_id = await conn.fetchval(
            f"""INSERT INTO {T_ENTRY} (org_id, global_security_id, instrument_class, ticker, cusip, fund_name,
                    issuer, sec_cik, sec_series_id, sec_class_id, holdings_source_key, status, display_name,
                    created_by)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12, $13::uuid)
                RETURNING id""",
            str(org_id), fund.global_security_id, instrument_class, fund.ticker, fund.cusip or cusip,
            fund_name or (fund.name if fund.name != fund.ticker else None),
            issuer or (source["issuer"] if source["parser"] != "sec_nport" else None),
            sec.get("cik"), sec.get("series_id"), sec.get("class_id"), source["key"],
            display_name_for(fund.ticker), str(actor_id) if actor_id else None)
        ref = await _record_action(conn, org_id=org_id, entry_id=entry_id, version_id=None, action="add_security",
                                   actor_id=actor_id, after={"ticker": fund.ticker, "source": source["key"]},
                                   detail={"created_global_security": fund.created})
        entry = await load_entry(conn, org_id, entry_id)
    return entry, [ref]


# ── registry field specs ────────────────────────────────────────────────────
async def load_registry(conn) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_REG} WHERE valid_to IS NULL AND system_to IS NULL AND retired_at IS NULL "
        "ORDER BY sort_order, field_key")]


def build_specs(registry: list[dict], *, model_only: bool = True) -> list[NS.FieldSpec]:
    rows = []
    for r in registry:
        if model_only and r["extraction_method"] != "model":
            continue
        d = dict(r)
        if d.get("trap_rule"):
            d["description"] = f"{d['description']} {d['trap_rule']}"
        rows.append(d)
    return NS.build_field_specs(rows)


# ── prospectus ──────────────────────────────────────────────────────────────
async def pull_prospectus(pool, *, fund_id: str, ticker: str, series_id: str | None,
                          http: httpx.AsyncClient) -> dict:
    """The fund's latest summary prospectus (497K), else its prospectus
    (485BPOS), stored once as a platform source document (+ gzipped bytes in
    R2). Same bytes again = the existing row."""
    if not series_id:
        raise ExtractionBlocked(f"{ticker} has no SEC series id — its prospectus cannot be located on EDGAR")
    filing = await H.latest_series_filing(http, series_id, ("497K",))
    kind = "summary_prospectus"
    if filing is None:
        filing = await H.latest_series_filing(http, series_id, ("485BPOS",))
        kind = "prospectus"
    if filing is None or not filing.get("cik"):
        raise ExtractionBlocked(f"no 497K or 485BPOS found on EDGAR for series {series_id}")
    folder = H.filing_folder(filing["cik"], filing["accession"])
    listing = await edgar_fetch._get(http, f"{folder}/index.json")
    items = (listing.json().get("directory") or {}).get("item") or []
    primary = edgar_fetch._pick_primary_document(items, filing["form"])
    if not primary:
        raise ExtractionBlocked(f"filing {filing['accession']} has no primary document")
    url = f"{folder}/{primary}"
    raw = (await edgar_fetch._get(http, url)).content
    sha = hashlib.sha256(raw).hexdigest()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(f"SELECT * FROM {T_DOC} WHERE fund_global_security_id = $1::uuid "
                                       "AND sha256 = $2 AND system_to IS NULL", str(fund_id), sha)
    if existing:
        return dict(existing)
    text = html_text.extract_bytes(raw).text
    key = f"replica/documents/{ticker.upper()}/{filing['accession']}/{primary}.gz"
    await run_in_threadpool(storage.upload_bytes, key, gzip.compress(raw, mtime=0), "application/gzip")
    async with H.platform_tx(pool) as conn:
        row = await conn.fetchrow(
            f"""INSERT INTO {T_DOC} (fund_global_security_id, doc_kind, form_type, accession_number, filing_date,
                    primary_document, source_url, r2_key, sha256, byte_size, extracted_text)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT ON CONSTRAINT replica_source_documents_file_uq DO NOTHING RETURNING *""",
            str(fund_id), kind, filing["form"].replace("/A", ""), filing["accession"], filing["filing_date"],
            primary, url, key, sha, len(raw), text)
        if row is None:
            row = await conn.fetchrow(f"SELECT * FROM {T_DOC} WHERE fund_global_security_id = $1::uuid "
                                      "AND sha256 = $2 AND system_to IS NULL", str(fund_id), sha)
    return dict(row)


def doc_window(text: str, ticker: str) -> tuple[str, int]:
    """(window, offset). A summary prospectus fits whole; a trust-wide 485BPOS
    is cut to the section that names this ticker, so the readers see this
    fund's terms and the offset keeps every quote span absolute."""
    if len(text) <= DOC_WINDOW_CHARS:
        return text, 0
    m = re.search(rf"\b{re.escape(ticker.upper())}\b", text)
    start = max(0, (m.start() if m else 0) - 2_000)
    return text[start:start + DOC_WINDOW_CHARS], start


# ── the ensemble (no Claude) ────────────────────────────────────────────────
INSTRUCTIONS = """You read ONE US fund prospectus or summary prospectus (SEC 497K / 485BPOS) of an index ETF \
or index mutual fund and report its terms as ONE JSON object that matches the JSON schema below exactly.

Rules:
- Answer only from the document text you are given. If a term is not stated, its value is null.
- For EVERY field give a SHORT EXACT quote copied verbatim from the document (one sentence or table row, \
under 300 characters) that states the value; quote null when the value is null.
- If the document covers several funds, answer ONLY for the fund named on the "Fund:" line.
- Percentages as numbers like 0.20 for 0.20% (never 0.002). Dates as YYYY-MM-DD.
- Expense ratio: gross = total annual fund operating expenses BEFORE any fee waiver or expense \
reimbursement; net = AFTER it; when there is no waiver, net equals gross.
- The index name is reported verbatim; it is a disclosed term only.
- Output JSON only — no prose, no markdown."""


def is_claude(*names) -> bool:
    return any(bool(n) and bool(_CLAUDE_RE.search(str(n))) for n in names)


async def active_ensemble(conn) -> dict | None:
    for key in (TASK_KEY, FALLBACK_TASK_KEY):
        row = await conn.fetchrow("SELECT * FROM ai_ensemble_configs WHERE task_key = $1 AND is_active "
                                  "ORDER BY activated_at DESC NULLS LAST LIMIT 1", key)
        if row:
            return dict(row)
    return None


def assert_slots_not_claude(ensemble: dict, catalog: dict) -> list[str]:
    """The two reader deployments, refused if either is a Claude model by its
    proxy name or its upstream — checked BEFORE any call."""
    slots = [ensemble["model_1"], ensemble["model_2"]]
    for name in slots:
        dep = catalog.get(name)
        if dep is None:
            raise ExtractionBlocked(f"deployment '{name}' is not registered on the LiteLLM proxy")
        if dep.duplicate:
            raise ExtractionBlocked(f"deployment '{name}' has two deployments behind it and would load-balance")
        if is_claude(name, dep.upstream, dep.price_key):
            raise ExtractionBlocked(f"deployment '{name}' ({dep.upstream}) is a Claude model — fund terms "
                                    "extraction never uses Claude")
    return slots


@dataclass
class Call:
    slot: str
    deployment: str
    status: str = "failed"
    error: str | None = None
    fields: dict = field(default_factory=dict)
    provider_model: str | None = None
    proxy_model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    prefix_hash: str | None = None


def build_messages(specs, window: str, *, fund_label: str, form_type: str) -> tuple[list[dict], str]:
    system = INSTRUCTIONS + "\n\nJSON schema:\n" + json.dumps(NS.json_schema(specs), sort_keys=True,
                                                               separators=(",", ":"))
    prefix = "Read the document below and answer with the JSON object only.\n\n"
    user = prefix + f"Fund: {fund_label}\nForm type: {form_type}\n\nDOCUMENT TEXT:\n" + window
    return ([{"role": "system", "content": system}, {"role": "user", "content": user}],
            hashlib.sha256((system + "\x00" + prefix).encode()).hexdigest())


async def read_one(slot: str, deployment: str, specs, messages, prefix_hash: str, catalog: dict) -> Call:
    dep = catalog.get(deployment)
    call = Call(slot, deployment, prefix_hash=prefix_hash)
    body = {"model": deployment, "messages": messages, "max_tokens": 3000,
            "metadata": {"tags": ["usage:replica_terms_extraction", f"slot:{slot}"]}}
    if dep is not None and dep.supports_response_schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "fund_terms", "schema": NS.json_schema(specs),
                                                   "strict": False}}
    else:
        body["response_format"] = {"type": "json_object"}
    resp = await proxy.chat(body)
    call.latency_ms = resp.latency_ms
    call.proxy_model_id = resp.headers.get("x-litellm-model-id")
    call.input_tokens, call.output_tokens, _ = proxy.response_usage(resp.body)
    call.cost_usd = proxy.header_cost(resp)
    if resp.status != 200 or resp.body is None:
        call.error = f"HTTP {resp.status}: {resp.error or (resp.text or '')[:300]}"
        return call
    call.provider_model = resp.body.get("model")
    fallbacks = resp.headers.get("x-litellm-attempted-fallbacks")
    if fallbacks not in (None, "0"):
        call.status, call.error = "model_mismatch", f"the proxy attempted {fallbacks} fallback(s)"
        return call
    if is_claude(call.provider_model):
        call.status, call.error = "model_mismatch", f"a Claude model answered ({call.provider_model}) — discarded"
        return call
    if not proxy.reported_model_matches(dep.upstream if dep else None, call.provider_model):
        call.status = "model_mismatch"
        call.error = f"asked for '{deployment}' but the provider reported '{call.provider_model}'"
        return call
    try:
        content = resp.body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        call.status, call.error = "invalid", "response carries no message content"
        return call
    parsed, fatal, _issues = NS.parse_reader_output(specs, content)
    if fatal:
        call.status, call.error = "invalid", fatal
        return call
    call.fields, call.status = parsed, "ok"
    return call


@dataclass
class ExtractionRun:
    run_id: str
    status: str                      # 'extracted' | 'reused' | 'blocked' | 'failed'
    reason: str | None = None
    document_id: str | None = None
    ensemble_config_id: str | None = None
    models: list = field(default_factory=list)
    readings: list = field(default_factory=list)      # dicts as stored
    cost_usd: float = 0.0


async def run_extraction(pool, *, fund_id: str, ticker: str, series_id: str | None, http: httpx.AsyncClient,
                         catalog_loader=None) -> ExtractionRun:
    """Pull the prospectus, run both readers, write readings (platform).
    A run for the same document + ensemble + prompt already on record is
    REUSED — the documents and readings are shared, fetched and read once."""
    run = ExtractionRun(str(uuid.uuid4()), "blocked")
    async with pool.acquire() as conn:
        ensemble = await active_ensemble(conn)
        registry = await load_registry(conn)
    if ensemble is None:
        run.reason = ("no active extraction ensemble: ai_ensemble_configs has no active row for "
                      f"'{TASK_KEY}' or '{FALLBACK_TASK_KEY}'")
        return run
    run.ensemble_config_id = str(ensemble["id"])
    catalog = await run_in_threadpool(catalog_loader or proxy.deployment_catalog)
    try:
        slots = assert_slots_not_claude(ensemble, catalog)
    except ExtractionBlocked as exc:
        run.reason = str(exc)
        return run
    run.models = slots
    try:
        doc = await pull_prospectus(pool, fund_id=fund_id, ticker=ticker, series_id=series_id, http=http)
    except (ExtractionBlocked, H.ReplicaHoldingsError, httpx.HTTPError, RuntimeError) as exc:
        run.reason = f"{type(exc).__name__}: {exc}"
        return run
    run.document_id = str(doc["id"])
    async with pool.acquire() as conn:
        prior = await conn.fetchval(
            f"SELECT extraction_run_id FROM {T_READ} WHERE source_document_id = $1::uuid AND ensemble_config_id = "
            "$2::uuid AND prompt_version = $3 AND system_to IS NULL ORDER BY created_at DESC LIMIT 1",
            run.document_id, run.ensemble_config_id, PROMPT_VERSION)
        if prior:
            run.run_id, run.status = str(prior), "reused"
            run.readings = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM {T_READ} WHERE extraction_run_id = $1::uuid AND system_to IS NULL", str(prior))]
            return run
    specs = build_specs(registry)
    window, offset = doc_window(doc["extracted_text"] or "", ticker)
    messages, prefix_hash = build_messages(specs, window, fund_label=ticker.upper(), form_type=doc["form_type"])
    calls = await asyncio.gather(*(read_one(s, d, specs, messages, prefix_hash, catalog)
                                   for s, d in (("model_1", slots[0]), ("model_2", slots[1]))))
    index = TextIndex(doc["extracted_text"] or "")
    smap = NS.spec_map(specs)
    records = []
    for call in calls:
        run.cost_usd += float(call.cost_usd or 0)
        base = (fund_id, run.document_id, run.run_id, call.slot, call.deployment, call.provider_model,
                call.proxy_model_id, run.ensemble_config_id, PROMPT_VERSION, call.prefix_hash, call.input_tokens,
                call.output_tokens, Decimal(repr(call.cost_usd)) if call.cost_usd is not None else None,
                call.latency_ms, call.call_id)
        if call.status != "ok":
            records.append(base + ("__call__", None, None, call.status, call.error, None, None, None, None, None))
            continue
        for key, item in call.fields.items():
            spec = smap[key]
            value = NS.to_jsonable(item.get("value"))
            quote = item.get("quote")
            span = index.locate(quote) if quote else None
            records.append(base + (key, _dumps(value) if value is not None else None, NS.normalize(spec, value),
                                   "ok", None, quote, span is not None if quote else None,
                                   NS.value_in_quote(spec, value, quote), span[0] if span else None,
                                   span[1] if span else None))
    async with H.platform_tx(pool) as conn:
        rows = await conn.fetch(
            f"""INSERT INTO {T_READ} (fund_global_security_id, source_document_id, extraction_run_id, source,
                    deployment_name, provider_model, proxy_model_id, ensemble_config_id, prompt_version,
                    prompt_prefix_hash, input_tokens, output_tokens, cost_usd, latency_ms, call_id, field_key, value,
                    value_normalized, status, error, source_quote, quote_verified, value_in_quote, raw_char_start,
                    raw_char_end)
                SELECT * FROM unnest($1::uuid[], $2::uuid[], $3::uuid[], $4::text[], $5::text[], $6::text[],
                    $7::text[], $8::uuid[], $9::text[], $10::text[], $11::int[], $12::int[], $13::numeric[],
                    $14::int[], $15::text[], $16::text[], $17::jsonb[], $18::text[], $19::text[], $20::text[],
                    $21::text[], $22::bool[], $23::bool[], $24::int[], $25::int[])
                RETURNING *""",
            *[list(col) for col in zip(*records)])
    run.readings = [dict(r) for r in rows]
    ok_calls = [c for c in calls if c.status == "ok"]
    run.status = "extracted" if ok_calls else "failed"
    if not ok_calls:
        run.reason = "; ".join(f"{c.slot}: {c.error}" for c in calls)
    return run


# ── drafts ──────────────────────────────────────────────────────────────────
async def _data_readings(pool, *, fund_id: str, entry: dict) -> list[dict]:
    """Deterministic fields from data, written as platform readings
    (source 'data'): ticker, SEC ids, holdings count of the latest live
    snapshot. Instrument class and issuer come from the ORG's registry entry,
    so they are NOT written to the shared readings table."""
    run_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        snap = await H.latest_snapshot(conn, fund_id)
        n = None
        if snap:
            n = await conn.fetchval("SELECT count(*) FROM portfolio.replica_holdings_rows WHERE snapshot_id = $1 "
                                    "AND asset_type <> 'cash'", snap["id"])
    facts = [("ticker", entry["ticker"], "security_master"),
             ("sec_cik", entry.get("sec_cik"), "sec_company_tickers_mf"),
             ("sec_series_id", entry.get("sec_series_id"), "sec_company_tickers_mf"),
             ("sec_class_id", entry.get("sec_class_id"), "sec_company_tickers_mf")]
    if snap is not None:
        facts.append(("holdings_count_latest", int(n), f"snapshot:{snap['id']}"))
    facts = [f for f in facts if f[1] is not None]
    if not facts:
        return []
    async with H.platform_tx(pool) as conn:
        rows = await conn.fetch(
            f"""INSERT INTO {T_READ} (fund_global_security_id, extraction_run_id, field_key, value, value_normalized,
                    source, data_source)
                SELECT $1::uuid, $2::uuid, k, v, n, 'data', s
                FROM unnest($3::text[], $4::jsonb[], $5::text[], $6::text[]) AS t(k, v, n, s)
                RETURNING *""",
            str(fund_id), run_id, [f[0] for f in facts], [_dumps(f[1]) for f in facts],
            [str(f[1]) for f in facts], [f[2] for f in facts])
    return [dict(r) for r in rows]


def compose_values(registry: list[dict], run: ExtractionRun, data_readings: list[dict], entry: dict) -> dict:
    """Per-field draft values (origin 'extracted') with confidence and the
    needs-review flag: no verified quote, a value not in its own quote, the two
    readers disagreeing or only one answering, or extraction not run."""
    by_key: dict[str, dict[str, dict]] = {}
    for r in run.readings:
        if r["status"] == "ok" and r["field_key"] != "__call__":
            by_key.setdefault(r["field_key"], {})[r["source"]] = r
    data = {r["field_key"]: r for r in data_readings}
    values = {}
    for reg in registry:
        key = reg["field_key"]
        v: dict[str, Any] = {"origin": "extracted", "value": None, "fund_value": None, "reading_id": None,
                             "quote": None, "quote_verified": None, "confidence": "not_run", "needs_review": False,
                             "review_reasons": [], "alternatives": []}
        if reg["extraction_method"] == "data":
            v["source"] = "data"
            if key in data:
                v.update(value=_loads(data[key]["value"]), reading_id=str(data[key]["id"]), confidence="data",
                         data_source=data[key]["data_source"])
            elif key == "instrument_class":
                v.update(value=entry["instrument_class"], confidence="data", data_source="registry")
            elif key == "issuer":
                v.update(value=entry.get("issuer"), confidence="data" if entry.get("issuer") else "not_stated",
                         data_source="registry")
            v["fund_value"] = v["value"]
            values[key] = v
            continue
        v["source"] = "model"
        pair = by_key.get(key, {})
        r1, r2 = pair.get("model_1"), pair.get("model_2")
        if run.status not in ("extracted", "reused"):
            v["needs_review"], v["review_reasons"] = True, [f"extraction not run: {run.reason or run.status}"]
        else:
            n1 = r1["value_normalized"] if r1 else None
            n2 = r2["value_normalized"] if r2 else None
            chosen = None
            if n1 is not None and n1 == n2:
                chosen = r1 if r1["quote_verified"] or not r2["quote_verified"] else r2
                v["confidence"] = "agreed"
            elif n1 is None and n2 is None:
                v["confidence"] = "not_stated"
                if reg["is_critical"]:
                    v["needs_review"] = True
                    v["review_reasons"].append("critical term not found by either reader")
            elif n1 is None or n2 is None:
                chosen = r1 if n1 is not None else r2
                v["confidence"] = "single"
                v["needs_review"] = True
                v["review_reasons"].append("only one reader found a value")
            else:
                chosen = r1
                v["confidence"] = "disagreed"
                v["needs_review"] = True
                v["review_reasons"].append("the two readers disagree")
                v["alternatives"] = [{"source": r["source"], "value": _loads(r["value"]), "reading_id": str(r["id"])}
                                     for r in (r1, r2)]
            if chosen is not None:
                v.update(value=_loads(chosen["value"]), reading_id=str(chosen["id"]), quote=chosen["source_quote"],
                         quote_verified=chosen["quote_verified"],
                         readings=[str(r["id"]) for r in (r1, r2) if r is not None])
                if not chosen["quote_verified"]:
                    v["needs_review"] = True
                    v["review_reasons"].append("its quote was not found in the document")
                elif chosen["value_in_quote"] is False:
                    v["needs_review"] = True
                    v["review_reasons"].append("the value does not appear in its quote")
        v["fund_value"] = v["value"]
        values[key] = v
    return values


async def _next_version_no(conn, entry_id) -> int:
    return int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T_VER} "
                                   "WHERE replica_security_id = $1::uuid AND system_to IS NULL", str(entry_id)))


async def live_draft(conn, entry_id, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT * FROM {T_VER} WHERE replica_security_id = $1::uuid AND status = 'draft' AND valid_to IS NULL "
        f"AND system_to IS NULL {'FOR UPDATE' if lock else ''}", str(entry_id))
    return dict(row) if row else None


async def live_approved(conn, entry_id, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT * FROM {T_VER} WHERE replica_security_id = $1::uuid AND status = 'approved' AND valid_to IS NULL "
        f"AND system_to IS NULL {'FOR UPDATE' if lock else ''}", str(entry_id))
    return dict(row) if row else None


def _needs(values: dict) -> list[str]:
    return sorted(k for k, v in values.items() if v.get("needs_review"))


async def build_draft(pool, *, org_id, entry: dict, actor_id, http: httpx.AsyncClient,
                      catalog_loader=None) -> tuple[dict, ExtractionRun, list[ActionRef]]:
    """Extract (or reuse the shared extraction) and write the draft. An open
    draft keeps every field a person corrected or changed; only fields still
    'extracted' are refreshed."""
    run = await run_extraction(pool, fund_id=str(entry["global_security_id"]), ticker=entry["ticker"],
                               series_id=entry.get("sec_series_id"), http=http, catalog_loader=catalog_loader)
    data = await _data_readings(pool, fund_id=str(entry["global_security_id"]), entry=entry)
    async with pool.acquire() as conn:
        registry = await load_registry(conn)
    values = compose_values(registry, run, data, entry)
    extraction = {"run_id": run.run_id, "status": run.status, "reason": run.reason, "document_id": run.document_id,
                  "ensemble_config_id": run.ensemble_config_id, "models": run.models, "cost_usd": run.cost_usd,
                  "at": datetime.now(timezone.utc).isoformat()}
    async with pool.acquire() as conn:
        await load_entry(conn, org_id, entry["id"])
        draft = await live_draft(conn, entry["id"], lock=True)
        if draft is None:
            approved = await live_approved(conn, entry["id"])
            vid = await conn.fetchval(
                f"""INSERT INTO {T_VER} (org_id, replica_security_id, version_no, status, term_values, departures,
                        needs_review_fields, extraction, created_by, supersedes_version_id)
                    VALUES ($1::uuid, $2::uuid, $3, 'draft', $4::jsonb, '[]'::jsonb, $5, $6::jsonb, $7::uuid, $8::uuid)
                    RETURNING id""",
                str(org_id), str(entry["id"]), await _next_version_no(conn, entry["id"]), _dumps(values),
                _needs(values), _dumps(extraction), str(actor_id) if actor_id else None,
                str(approved["id"]) if approved else None)
        else:
            kept = {k: v for k, v in (_loads(draft["term_values"]) or {}).items() if v.get("origin") != "extracted"}
            merged = {**values, **kept}
            await _archive(conn, T_VER, draft["id"])
            await conn.execute(f"UPDATE {T_VER} SET term_values = $2::jsonb, needs_review_fields = $3, "
                               "extraction = $4::jsonb, system_from = now() WHERE id = $1::uuid",
                               draft["id"], _dumps(merged), _needs(merged), _dumps(extraction))
            vid = draft["id"]
        version = dict(await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid", str(vid)))
        ref = await _record_action(conn, org_id=org_id, entry_id=entry["id"], version_id=vid, action="extract",
                                   actor_id=actor_id, detail={"extraction_status": run.status,
                                                              "needs_review": len(_needs(_loads(version["term_values"])))})
    await ensure_approval_task(pool, org_id=org_id, entry=entry, version=version, actor_id=actor_id)
    return version, run, [ref]


# ── correction / deliberate change ──────────────────────────────────────────
def _coerce_value(reg: dict, value):
    """The value, checked against the field's own type — a correction is data
    entry and gets the same shape rules as a reading."""
    if value is None:
        return None
    t = reg["data_type"]
    if t == "numeric":
        if isinstance(value, bool):
            raise ReplicaTermsError(f"{reg['display_label']} must be a number")
        try:
            d = Decimal(str(value).strip())
        except Exception as exc:  # noqa: BLE001
            raise ReplicaTermsError(f"{reg['display_label']} must be a number") from exc
        return float(d) if d != d.to_integral_value() else int(d)
    if t == "boolean":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "yes"):
            return True
        if s in ("false", "no"):
            return False
        raise ReplicaTermsError(f"{reg['display_label']} must be yes or no")
    if t == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError as exc:
            raise ReplicaTermsError(f"{reg['display_label']} must be a date (YYYY-MM-DD)") from exc
    s = str(value).strip()
    if not s:
        raise ReplicaTermsError(f"{reg['display_label']} cannot be blank")
    if reg.get("enum_values") and s not in reg["enum_values"]:
        raise ReplicaTermsError(f"{reg['display_label']} must be one of {list(reg['enum_values'])}")
    return s


async def _registry_field(conn, field_key: str) -> dict:
    reg = await conn.fetchrow(f"SELECT * FROM {T_REG} WHERE field_key = $1 AND valid_to IS NULL "
                              "AND system_to IS NULL AND retired_at IS NULL", field_key)
    if reg is None:
        raise ReplicaTermsError(f"unknown terms field {field_key!r}")
    return dict(reg)


async def _version_for_edit(conn, entry_id, version_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid AND replica_security_id = $2::uuid "
                              "AND system_to IS NULL FOR UPDATE", str(version_id), str(entry_id))
    if row is None:
        raise ReplicaNotFound("terms version not found")
    if row["status"] != "draft":
        raise ReplicaConflict(f"version {row['version_no']} is {row['status']} — only the open draft is edited; "
                              "approved versions are never edited")
    return dict(row)


async def correct_field(pool, *, org_id, actor_id, entry_id, version_id, field_key: str, value,
                        notes: str | None = None) -> tuple[dict, list[ActionRef]]:
    async with pool.acquire() as conn:
        entry = await load_entry(conn, org_id, entry_id)
        reg = await _registry_field(conn, field_key)
        draft = await _version_for_edit(conn, entry_id, version_id)
        new_value = _coerce_value(reg, value)
        values = _loads(draft["term_values"]) or {}
        cur = dict(values.get(field_key) or {"source": "model"})
        before = cur.get("value")
        corr_id = await conn.fetchval(
            """INSERT INTO document_field_corrections (org_id, field_name, original_value, corrected_value, notes,
                   corrected_by, target_type, target_id)
               VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, 'replica_terms', $7::uuid) RETURNING id""",
            str(org_id), field_key, None if before is None else _dumps(before), _dumps(new_value), notes,
            str(actor_id), str(draft["id"]))
        cur.update(value=new_value, fund_value=new_value, origin="corrected", needs_review=False, review_reasons=[],
                   confidence="human", corrected_by=str(actor_id), correction_id=str(corr_id),
                   extracted_value=cur.get("extracted_value", before))
        values[field_key] = cur
        departures = [d for d in (_loads(draft["departures"]) or []) if d.get("field_key") != field_key]
        makers = sorted({*(str(m) for m in draft["maker_ids"] or []), str(actor_id)})
        await _archive(conn, T_VER, draft["id"])
        await conn.execute(f"UPDATE {T_VER} SET term_values = $2::jsonb, departures = $3::jsonb, "
                           "needs_review_fields = $4, maker_ids = $5::uuid[], system_from = now() WHERE id = $1::uuid",
                           draft["id"], _dumps(values), _dumps(departures), _needs(values), makers)
        version = dict(await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid", str(draft["id"])))
        ref = await _record_action(conn, org_id=org_id, entry_id=entry_id, version_id=draft["id"], action="correct",
                                   actor_id=actor_id, field_key=field_key, before=before, after=new_value,
                                   reason=notes, correction_id=corr_id)
    await ensure_approval_task(pool, org_id=org_id, entry=entry, version=version, actor_id=actor_id)
    return version, [ref]


async def change_field(pool, *, org_id, actor_id, entry_id, field_key: str, value, reason: str,
                       version_id=None) -> tuple[dict, list[ActionRef]]:
    if not (reason or "").strip():
        raise ReplicaTermsError("a deliberate change needs a reason")
    async with pool.acquire() as conn:
        entry = await load_entry(conn, org_id, entry_id)
        reg = await _registry_field(conn, field_key)
        if reg["disclosure_only"]:
            raise ReplicaTermsError(f"{reg['display_label']} is a disclosed term of the fund — it can be corrected, "
                                    "never changed")
        if version_id is not None:
            draft = await _version_for_edit(conn, entry_id, version_id)
        else:
            draft = await live_draft(conn, entry_id, lock=True)
            if draft is None:
                approved = await live_approved(conn, entry_id, lock=True)
                if approved is None:
                    raise ReplicaConflict("there is no draft or approved version to change — extract terms first")
                vid = await conn.fetchval(
                    f"""INSERT INTO {T_VER} (org_id, replica_security_id, version_no, status, term_values, departures,
                            needs_review_fields, extraction, created_by, supersedes_version_id)
                        VALUES ($1::uuid, $2::uuid, $3, 'draft', $4::jsonb, $5::jsonb, $6, $7::jsonb, $8::uuid,
                                $9::uuid) RETURNING id""",
                    str(org_id), str(entry_id), await _next_version_no(conn, entry_id),
                    _dumps(_loads(approved["term_values"])), _dumps(_loads(approved["departures"])),
                    list(approved["needs_review_fields"] or []), _dumps(_loads(approved["extraction"])),
                    str(actor_id), str(approved["id"]))
                draft = dict(await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid FOR UPDATE", str(vid)))
        new_value = _coerce_value(reg, value)
        values = _loads(draft["term_values"]) or {}
        cur = dict(values.get(field_key) or {"source": "model"})
        fund_value = cur.get("fund_value", cur.get("value"))
        if new_value == fund_value:
            raise ReplicaTermsError("that is the fund's own value — a deliberate change must differ from the fund")
        before = cur.get("value")
        cur.update(value=new_value, origin="deliberate", needs_review=False, review_reasons=[], confidence="human",
                   changed_by=str(actor_id), reason=reason.strip(), fund_value=fund_value)
        values[field_key] = cur
        departures = [d for d in (_loads(draft["departures"]) or []) if d.get("field_key") != field_key]
        departures.append({"field_key": field_key, "label": reg["display_label"], "fund_value": fund_value,
                           "our_value": new_value, "reason": reason.strip(), "by": str(actor_id),
                           "at": datetime.now(timezone.utc).isoformat()})
        makers = sorted({*(str(m) for m in draft["maker_ids"] or []), str(actor_id)})
        await _archive(conn, T_VER, draft["id"])
        await conn.execute(f"UPDATE {T_VER} SET term_values = $2::jsonb, departures = $3::jsonb, "
                           "needs_review_fields = $4, maker_ids = $5::uuid[], system_from = now() WHERE id = $1::uuid",
                           draft["id"], _dumps(values), _dumps(departures), _needs(values), makers)
        version = dict(await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid", str(draft["id"])))
        ref = await _record_action(conn, org_id=org_id, entry_id=entry_id, version_id=draft["id"], action="change",
                                   actor_id=actor_id, field_key=field_key, before=before, after=new_value,
                                   reason=reason.strip(), detail={"fund_value": fund_value})
    await ensure_approval_task(pool, org_id=org_id, entry=entry, version=version, actor_id=actor_id)
    return version, [ref]


# ── approve / reject (a governed human task) ────────────────────────────────
async def _apply_terms_decision(pool, *, org_id, version_id, approve: bool, actor_id, note,
                                task_id=None) -> tuple[dict, list[ActionRef]]:
    """Apply a decided approval task to its terms version, then publish the
    action. Idempotent: a version that is no longer a draft is left alone and
    returned with no action."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid AND org_id = $2::uuid "
                                  "AND system_to IS NULL FOR UPDATE", str(version_id), str(org_id))
        if row is None:
            raise ReplicaNotFound("terms version not found")
        if row["status"] != "draft":
            return dict(row), []
        draft = dict(row)
        entry_id = draft["replica_security_id"]
        entry = await load_entry(conn, org_id, entry_id)
        if str(actor_id) in {str(m) for m in draft["maker_ids"] or []}:
            raise MakerCheckerViolation(MAKER_CHECKER_RULE)
        await _archive(conn, T_VER, draft["id"])
        prior = None
        if approve:
            prior = await live_approved(conn, entry_id, lock=True)
            if prior is not None:
                await _archive(conn, T_VER, prior["id"])
                await conn.execute(f"UPDATE {T_VER} SET status = 'superseded' WHERE id = $1::uuid", prior["id"])
            await conn.execute(
                f"UPDATE {T_VER} SET status = 'approved', checker_id = $2::uuid, approved_at = now(), "
                "decision_note = $3, supersedes_version_id = $4::uuid, system_from = now() WHERE id = $1::uuid",
                draft["id"], str(actor_id), note, str(prior["id"]) if prior else draft["supersedes_version_id"])
            if entry["status"] == "draft":
                await _archive(conn, T_ENTRY, entry["id"])
                await conn.execute(f"UPDATE {T_ENTRY} SET status = 'active', system_from = now() WHERE id = $1::uuid",
                                   entry["id"])
        else:
            await conn.execute(
                f"UPDATE {T_VER} SET status = 'rejected', checker_id = $2::uuid, rejected_at = now(), "
                "decision_note = $3, system_from = now() WHERE id = $1::uuid", draft["id"], str(actor_id), note)
        # drafts opened before rep3 carried member_todos of their own
        closed = await close_todos(conn, org_id=org_id, source=TODO_SOURCE_APPROVAL, related_id=draft["id"])
        version = dict(await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid", str(draft["id"])))
        ref = await _record_action(conn, org_id=org_id, entry_id=entry_id, version_id=draft["id"],
                                   action="approve" if approve else "reject", actor_id=actor_id, reason=note,
                                   detail={"version_no": draft["version_no"], "legacy_todos_closed": closed,
                                           "task_id": str(task_id) if task_id else None,
                                           "superseded_version_id": str(prior["id"]) if prior else None})
    await publish_actions(pool, [ref])
    if approve and prior is not None:
        # rep4: a critical terms change raises a review task on every model built from this fund (no automatic
        # version). Never undoes the approval — a failure is logged.
        try:
            from services import replica_models as RM

            await RM.monitor_terms(pool, org_id=org_id, entry_id=entry_id, version_id=draft["id"],
                                   prior_version_id=prior["id"])
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("terms monitor failed for %s", draft["id"])
    return version, [ref]


async def _on_terms_task_complete(pool, task, outcome, actor_id, note):
    stored = None if note == REJECT_WITHOUT_NOTE else note
    return await _apply_terms_decision(pool, org_id=task["org_id"], version_id=task["subject_id"],
                                       approve=outcome == "approve", actor_id=actor_id, note=stored,
                                       task_id=task["id"])


HT.register_kind(TERMS_TASK_KIND, label="Terms approval", outcomes=("approve", "reject"), maker_checker=False,
                 on_complete=_on_terms_task_complete)


async def sync_terms_task_outcome(pool, *, org_id, version_id) -> None:
    """Recovery: a draft whose approval task the engine already decided (the
    subject update failed after the engine committed) takes that outcome now."""
    async with pool.acquire() as conn:
        status = await conn.fetchval(f"SELECT status FROM {T_VER} WHERE id = $1::uuid AND org_id = $2::uuid "
                                     "AND system_to IS NULL", str(version_id), str(org_id))
        if status != "draft":
            return
        done = await HT.list_tasks(conn, org_id, status="completed", kind=TERMS_TASK_KIND,
                                   subject_type=TERMS_TASK_SUBJECT, subject_id=version_id, limit=1)
    if done and done[0]["outcome"] in ("approve", "reject") and done[0]["completed_by"]:
        t = done[0]
        await _on_terms_task_complete(pool, t, t["outcome"], t["completed_by"], t["note"])


async def decide(pool, *, org_id, actor_id, entry_id, version_id, approve: bool,
                 note: str | None = None) -> tuple[dict, list[ActionRef]]:
    """Approve / reject the draft through its human task. Refusals are rep1's,
    unchanged and in the same order: 404 not visible, 409 not the open draft,
    403 a maker of the draft (MAKER_CHECKER_RULE). The action is published by
    the task callback — callers must not publish the returned refs again."""
    try:
        vid = uuid.UUID(str(version_id))
    except ValueError as exc:
        raise ReplicaNotFound("terms version not found") from exc
    await sync_terms_task_outcome(pool, org_id=org_id, version_id=vid)
    async with pool.acquire() as conn:
        entry = await load_entry(conn, org_id, entry_id)
        draft = await _version_for_edit(conn, entry_id, version_id)
        if str(actor_id) in {str(m) for m in draft["maker_ids"] or []}:
            raise MakerCheckerViolation(MAKER_CHECKER_RULE)
    task = await ensure_approval_task(pool, org_id=org_id, entry=entry, version=draft, actor_id=actor_id)
    if task is None:
        raise ReplicaConflict("the approval task for this draft could not be opened")
    task_note = note if (note or "").strip() else (None if approve else REJECT_WITHOUT_NOTE)
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor_id,
                                      outcome="approve" if approve else "reject", note=task_note)
    except HT.HumanTaskPermissionError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise ReplicaConflict(str(exc)) from exc
    except HT.AgentActorRefused as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskError as exc:
        raise ReplicaTermsError(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1::uuid", str(draft["id"]))
        return dict(row), []
    return result


# ── reads for the API ───────────────────────────────────────────────────────
async def versions_of(conn, entry_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT id, version_no, status, departures, needs_review_fields, maker_ids, created_by, checker_id, "
        f"approved_at, rejected_at, decision_note, created_at, system_from FROM {T_VER} "
        "WHERE replica_security_id = $1::uuid AND system_to IS NULL ORDER BY version_no DESC", str(entry_id))]


async def readings_for(conn, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = await conn.fetch(
        f"SELECT r.id, r.field_key, r.value, r.source, r.status, r.provider_model, r.deployment_name, r.source_quote, "
        f"r.quote_verified, r.value_in_quote, r.raw_char_start, r.raw_char_end, r.data_source, r.source_document_id, "
        f"d.source_url AS document_url, d.form_type FROM {T_READ} r LEFT JOIN {T_DOC} d ON d.id = r.source_document_id "
        "WHERE r.id = ANY($1::uuid[])", ids)
    return {str(r["id"]): dict(r) for r in rows}


async def actions_of(conn, entry_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT a.id, a.action, a.field_key, a.before_value, a.after_value, a.reason, a.actor_id, a.created_at, "
        f"a.terms_version_id, COALESCE(u.full_name, u.email) AS actor_name FROM {T_ACT} a "
        "LEFT JOIN users u ON u.id = a.actor_id WHERE a.replica_security_id = $1::uuid ORDER BY a.created_at DESC "
        "LIMIT 200", str(entry_id))]
