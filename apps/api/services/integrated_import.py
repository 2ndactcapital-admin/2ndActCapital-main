"""PORTENTRY1 Task 6 (GAP-PORT-02) — the one-time position import for INTEGRATED custodian accounts awaiting a feed.

Joe's decision (2026-11-04): the demo's integrated (Altruist) accounts are loaded ONCE from a position file and
superseded by the custodian feed when it arrives.

FLOW (a Super Admin screen, /admin/integrated-import):
  1. upload a CSV (format below) → a DRAFT ``portfolio.provisional_imports`` row holding the PREVIEW: every row with
     the account it resolved to (by MASK only), the security it resolved to (the platform master — read only — else
     an org-scoped manual asset), its value, and totals per account and overall. Nothing else is written.
  2. a SECOND Super Admin approves (human task ``integrated_import_approval``, maker-checker: the uploader never
     approves; the table's own CHECK backs it) → positions are written on each account's primary entity with
     ``account_id`` set, ``authority = 'stated'`` and ``source_system = 'manual'``, each recorded in
     ``portfolio.provisional_positions``; the families' net worth is refreshed (portfolio_refresh).
  3. the CUSTODIAN FEED supersedes them. ``'manual'`` ranks BELOW ``'altruist'`` in the default precedence order
     (org_settings ``portfolio.precedence.source_order``) — the existing import's ``reporting_tool_import`` ranks
     ABOVE it, which is why that token is not used here. When the Altruist sync writes an account's positions,
     :func:`supersede_for_account` runs ``portfolio_precedence.resolve_holding`` on every provisional holding key
     (same owner / asset / date → the provisional row is annotated ``superseded_by_source = 'altruist'``) and then
     closes every provisional row of that account (valid axis; ``provisional_positions`` records by whom and why) —
     a holding the feed no longer reports must not linger as the account's latest statement.

CSV FORMAT (header row, UTF-8; documented in docs/demo/olivo/README.md):
  account_mask, as_of_date, name, ticker, cusip, asset_class, sub_class, taxonomy_key, quantity, price,
  market_value, cost_basis
  * account_mask — the account's mask ("****2201"); matched on its last four digits to exactly ONE non-held-away
    account of the org. A full account number is refused (more than four digits).
  * market_value — required when quantity × price is not given (a value-only line, e.g. a direct-index SMA).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from services import human_tasks as HT
from services.portfolio_assets import TABLE_POSITIONS, _OrgWrite

T_IMP = "portfolio.provisional_imports"
T_PROV = "portfolio.provisional_positions"
TASK_KIND = "integrated_import_approval"
SUBJECT = "provisional_import"
ELIGIBLE = "manage_portfolio"
AUTHORITY = "stated"
SOURCE = "manual"
COLUMNS = ("account_mask", "as_of_date", "name", "ticker", "cusip", "asset_class", "sub_class", "taxonomy_key",
           "quantity", "price", "market_value", "cost_basis")
REQUIRED = ("account_mask", "name")


class ImportError_(ValueError):
    pass


class ImportNotFound(LookupError):
    pass


def _d(v, label):
    v = (v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise ImportError_(f"{label} '{v}' is not a number") from None
    if d < 0:
        raise ImportError_(f"{label} is negative")
    return d


def mask_digits(mask: str) -> str | None:
    digits = re.sub(r"\D", "", mask or "")
    return digits[-4:] if 1 <= len(digits) <= 4 else None


async def _accounts(c, org_id) -> list[dict]:
    return [dict(r) for r in await c.fetch(
        "SELECT id, account_number_masked, primary_entity_id, is_held_away, custodian_code FROM public.accounts "
        "WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL", str(org_id))]


async def build_preview(conn, org_id, data: bytes, *, default_as_of: date | None = None) -> dict:
    from services import securities_global as SG

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportError_("the file is not UTF-8 text (CSV)") from None
    reader = csv.DictReader(io.StringIO(text))
    hdr = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED + ("quantity", "market_value") if c not in hdr]
    if missing:
        raise ImportError_(f"the header lacks {', '.join(missing)} (columns: {', '.join(COLUMNS)})")
    async with _OrgWrite(conn, str(org_id)) as c:
        accounts = await _accounts(c, org_id)
    rows, rejects = [], []
    raw_rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in reader]
    looks = {t: await SG.get_by_identifiers_bulk(conn, t, [r.get(t) for r in raw_rows if r.get(t)])
             for t in ("cusip", "ticker")}
    for i, r in enumerate(raw_rows, start=2):
        try:
            digits = mask_digits(r.get("account_mask"))
            if digits is None:
                raise ImportError_("account_mask must be a MASK (at most the last four digits), never a full number")
            hits = [a for a in accounts if mask_digits(a["account_number_masked"]) == digits and not a["is_held_away"]]
            if len(hits) != 1:
                raise ImportError_(f"{len(hits)} integrated accounts end in {digits} — need exactly one")
            acct = hits[0]
            q, px, mv = _d(r.get("quantity"), "quantity"), _d(r.get("price"), "price"), _d(r.get("market_value"),
                                                                                           "market value")
            if mv is None and q is not None and px is not None:
                mv = q * px
            if mv is None:
                raise ImportError_("a line needs a market value (or quantity and price)")
            as_of = date.fromisoformat(r["as_of_date"]) if r.get("as_of_date") else default_as_of
            if as_of is None:
                raise ImportError_("no as_of_date on the line and none for the file")
            sec, how = None, "org-scoped manual asset (not in the master)"
            for t in ("cusip", "ticker"):
                got = looks[t].get(SG.normalize_identifier_value(t, r[t])) if r.get(t) else None
                if got:
                    sec, how = str(got.get("matched_global_security_id") or got["id"]), f"master by {t.upper()}"
                    break
            rows.append({"line": i, "account_id": str(acct["id"]), "account_mask": acct["account_number_masked"],
                         "owner_entity_id": str(acct["primary_entity_id"]), "name": r.get("name"),
                         "ticker": r.get("ticker") or None, "cusip": r.get("cusip") or None,
                         "asset_class": r.get("asset_class") or None, "sub_class": r.get("sub_class") or None,
                         "taxonomy_key": r.get("taxonomy_key") or None, "as_of": as_of.isoformat(),
                         "quantity": None if q is None else str(q), "price": None if px is None else str(px),
                         "market_value": str(mv), "cost_basis": None if _d(r.get("cost_basis"), "cost basis") is None
                         else str(_d(r.get("cost_basis"), "cost basis")),
                         "security_id": sec, "resolution": how})
        except (ImportError_, ValueError) as exc:
            rejects.append({"line": i, "reason": str(exc), "account_mask": r.get("account_mask"),
                            "name": r.get("name")})
    per: dict[str, dict] = {}
    for x in rows:
        t = per.setdefault(x["account_mask"], {"account_mask": x["account_mask"], "rows": 0, "value": Decimal(0)})
        t["rows"] += 1
        t["value"] += Decimal(x["market_value"])
    total = sum((t["value"] for t in per.values()), Decimal(0))
    return {"rows": rows, "rejects": rejects,
            "totals": {"accounts": [{**t, "value": str(t["value"])} for t in per.values()], "value": str(total),
                       "rows": len(rows), "rejected": len(rejects),
                       "resolved_in_master": sum(1 for x in rows if x["security_id"]),
                       "manual_assets": sum(1 for x in rows if not x["security_id"])}}


async def create_draft(pool, *, org_id, actor_id, data: bytes, filename: str | None, as_of: date | None,
                       notes: str | None = None) -> dict:
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        preview = await build_preview(conn, org_id, data, default_as_of=as_of)
    if not preview["rows"]:
        raise ImportError_("no line could be read: " + "; ".join(f"line {r['line']}: {r['reason']}"
                                                                 for r in preview["rejects"][:5]))
    file_as_of = max(date.fromisoformat(r["as_of"]) for r in preview["rows"])
    iid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=iid, maker_id=actor["id"],
        eligible_permission=ELIGIBLE, title=f"Integrated-account position import ({preview['totals']['rows']} lines, "
                                            f"{len(preview['totals']['accounts'])} accounts) as of {file_as_of}",
        payload={"rows": preview["totals"]["rows"], "value": preview["totals"]["value"]},
        action_path="/admin/integrated-import")
    try:
        async with pool.acquire() as conn:
            async with _OrgWrite(conn, str(org_id)) as c:
                row = await c.fetchrow(
                    f"""INSERT INTO {T_IMP} (id, org_id, file_name, file_sha256, as_of, rows_read, rows_accepted,
                            rows_rejected, preview, totals, entered_by, approval_task_id, notes)
                        VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11::uuid, $12::uuid, $13)
                        RETURNING *""",
                    iid, str(org_id), (filename or "")[:300] or None, hashlib.sha256(data).hexdigest(), file_as_of,
                    len(preview["rows"]) + len(preview["rejects"]), len(preview["rows"]), len(preview["rejects"]),
                    json.dumps({"rows": preview["rows"], "rejects": preview["rejects"]}),
                    json.dumps(preview["totals"]), str(actor["id"]), task["id"], (notes or "").strip() or None)
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the import could not be recorded")
        raise
    return import_out(dict(row))


def import_out(r: dict) -> dict:
    def j(v):
        return json.loads(v) if isinstance(v, str) else (v or {})
    return {"id": str(r["id"]), "status": r["status"], "file_name": r["file_name"], "as_of": r["as_of"].isoformat(),
            "rows_read": r["rows_read"], "rows_accepted": r["rows_accepted"], "rows_rejected": r["rows_rejected"],
            "preview": j(r["preview"]), "totals": j(r["totals"]), "entered_by": str(r["entered_by"]),
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
            "decision_note": r["decision_note"]}


async def get_import(conn, org_id, import_id) -> dict:
    async with _OrgWrite(conn, str(org_id)) as c:
        r = await c.fetchrow(f"SELECT * FROM {T_IMP} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                             str(import_id), str(org_id))
    if r is None:
        raise ImportNotFound("import not found")
    return dict(r)


async def list_imports(conn, org_id) -> list[dict]:
    async with _OrgWrite(conn, str(org_id)) as c:
        rows = await c.fetch(f"SELECT * FROM {T_IMP} WHERE org_id = $1::uuid AND system_to IS NULL "
                             "ORDER BY created_at DESC LIMIT 100", str(org_id))
    return [import_out(dict(r)) for r in rows]


async def _decide(c, imp: dict, status: str, checker, note) -> dict:
    """System axis: archive a copy, decide the live row (its id is kept — provisional_positions point at it)."""
    await c.execute(
        f"""INSERT INTO {T_IMP} (org_id, file_name, file_sha256, as_of, status, rows_read, rows_accepted,
                rows_rejected, preview, totals, entered_by, approved_by, approval_task_id, decided_at, decision_note,
                notes, archived_from_id, created_at, valid_from, valid_to, system_from, system_to)
            SELECT org_id, file_name, file_sha256, as_of, status, rows_read, rows_accepted, rows_rejected, preview,
                   totals, entered_by, approved_by, approval_task_id, decided_at, decision_note, notes, id, created_at,
                   valid_from, valid_to, system_from, now()
            FROM {T_IMP} WHERE id = $1""", imp["id"])
    return dict(await c.fetchrow(
        f"UPDATE {T_IMP} SET status = $2, approved_by = $3::uuid, decided_at = now(), decision_note = $4, "
        "system_from = now() WHERE id = $1 RETURNING *", imp["id"], status, str(checker), note))


async def _apply(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    from services.held_away import _asset_for
    from services.portfolio_manual import manual_asset_for
    from services.portfolio_precedence import resolve_holding

    owners: set[str] = set()
    async with pool.acquire() as conn:
        async with _OrgWrite(conn, str(org_id)) as c:
            imp = await c.fetchrow(f"SELECT * FROM {T_IMP} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                   "AND system_to IS NULL FOR UPDATE", str(task["id"]), str(org_id))
            if imp is None or imp["status"] != "draft":
                return import_out(dict(imp)) if imp else None
            imp = dict(imp)
            if str(imp["entered_by"]) == str(actor_id):
                raise PermissionError("maker-checker: the person who uploaded an import never approves it")
            if outcome != "approve":
                return import_out(await _decide(c, imp, "rejected", actor_id, note))
            preview = json.loads(imp["preview"]) if isinstance(imp["preview"], str) else imp["preview"]
            keys = []
            for x in preview.get("rows", []):
                if x["security_id"]:
                    aid = await _asset_for(c, org_id, x["security_id"])
                else:
                    kind = "mutual_fund" if "fund" in (x.get("sub_class") or "").lower() else "private"
                    aid = await manual_asset_for(c, org_id, {
                        "asset_kind": kind, "name": x["name"], "taxonomy_key": x.get("taxonomy_key"),
                        "identifiers": {k: x[k] for k in ("ticker", "cusip") if x.get(k)},
                        "terms": {"sub_class": x.get("sub_class")} if x.get("sub_class") else {}})
                q = Decimal(x["quantity"]) if x.get("quantity") else None
                basis = "units" if q is not None else "value"
                as_of = date.fromisoformat(x["as_of"])
                pid = await c.fetchval(
                    f"""INSERT INTO {TABLE_POSITIONS} (org_id, owner_entity_id, asset_id, as_of_date, ownership_basis,
                            quantity, cost_basis, market_value, authority, source_system, account_id, taxonomy_key)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11::uuid, $12)
                        RETURNING id::text""",
                    str(org_id), x["owner_entity_id"], aid, as_of, basis, q,
                    Decimal(x["cost_basis"]) if x.get("cost_basis") else None, Decimal(x["market_value"]),
                    AUTHORITY, SOURCE, x["account_id"], x.get("taxonomy_key"))
                await c.execute(f"INSERT INTO {T_PROV} (org_id, import_id, account_id, position_id, as_of) "
                                "VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5)",
                                str(org_id), str(imp["id"]), x["account_id"], pid, as_of)
                keys.append((x["owner_entity_id"], aid, as_of))
                owners.add(x["owner_entity_id"])
            out = import_out(await _decide(c, imp, "approved", actor_id, note))
        for owner, aid, as_of in sorted(set(keys)):
            await resolve_holding(conn, str(org_id), owner_entity_id=owner, asset_id=aid, as_of_date=as_of)
    from services import portfolio_refresh as PR

    out["rollup"] = await PR.after_write(pool, org_id, sorted(owners), reason=f"integrated import {imp['id']}",
                                         actor_id=actor_id)
    return out


async def _on_task(pool, task, outcome, actor_id, note):
    return await _apply(pool, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_KIND, label="Integrated-account import approval", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=_on_task)


async def decide(pool, *, org_id, actor_id, import_id, outcome: str, note: str | None = None) -> dict:
    async with pool.acquire() as conn:
        imp = await get_import(conn, org_id, import_id)
    if imp["status"] != "draft":
        raise ImportError_(f"this import is {imp['status']}, not a draft")
    if str(imp["entered_by"]) == str(actor_id):
        from services.maker_names import maker_refusal  # BUGFIX4 — names who entered it (and the shared sign-ins)

        async with pool.acquire() as conn:
            who = await maker_refusal(conn, maker_id=imp["entered_by"], checker_id=actor_id, approver="Super Admin",
                                      item="import")
        raise PermissionError(f"maker-checker: {who}")
    done = await HT.complete_task(pool, org_id=org_id, task_id=imp["approval_task_id"], actor_id=actor_id,
                                  outcome=outcome, note=note)
    return done.get("subject_result") or {}


async def supersede_for_account(conn, org_id, account_id, *, by_source: str = "altruist") -> dict:
    """The custodian feed arrived for ``account_id``: resolve precedence on every provisional holding key, then close
    every live provisional row of the account (valid axis; nothing deleted). Returns what it did."""
    from services.portfolio_precedence import resolve_holding

    org = str(org_id)
    async with _OrgWrite(conn, org) as c:
        prov = await c.fetch(
            f"SELECT pp.id::text, pp.position_id::text, p.owner_entity_id::text AS owner, p.asset_id::text AS asset, "
            f"p.as_of_date FROM {T_PROV} pp JOIN {TABLE_POSITIONS} p ON p.id = pp.position_id "
            f"WHERE pp.org_id = $1::uuid AND pp.account_id = $2::uuid AND pp.valid_to IS NULL AND pp.system_to IS NULL",
            org, str(account_id))
    resolved = 0
    for k in sorted({(r["owner"], r["asset"], r["as_of_date"]) for r in prov}):
        if await resolve_holding(conn, org, owner_entity_id=k[0], asset_id=k[1], as_of_date=k[2]) is not None:
            resolved += 1
    why = f"superseded by the {by_source} custodian feed ({datetime.now(timezone.utc).date().isoformat()})"
    async with _OrgWrite(conn, org) as c:
        ids = [r["position_id"] for r in prov]
        if ids:
            await c.execute(f"UPDATE {TABLE_POSITIONS} SET valid_to = now() WHERE id = ANY($1::uuid[]) "
                            "AND valid_to IS NULL AND system_to IS NULL", ids)
            await c.execute(f"UPDATE {T_PROV} SET valid_to = now(), superseded_by_source = $2, superseded_reason = $3 "
                            "WHERE id = ANY($1::uuid[])", [r["id"] for r in prov], by_source, why)
    return {"account_id": str(account_id), "provisional_rows": len(prov), "keys_resolved": resolved,
            "superseded_by": by_source}


def vocab() -> dict:
    return {"columns": list(COLUMNS), "required": list(REQUIRED), "authority": AUTHORITY, "source_system": SOURCE,
            "statuses": [{"value": "draft", "label": "Draft — awaiting a second person's approval"},
                         {"value": "approved", "label": "Approved — provisional until the custodian feed"},
                         {"value": "rejected", "label": "Rejected"}]}
