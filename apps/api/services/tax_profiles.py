"""Tax inputs (rep6) — member tax profiles, loss carryforwards and declared
outside trades. Every tax input comes from the ADVISER, with a date and a
source; nothing here ever guesses a rate.

THE SUBJECT (Task 1b)
──────────────────────────────────────────────────────────────────────────────
A profile hangs on the account's HOUSEHOLD (public.households — a married
couple filing jointly is one profile covering both spouses' accounts) or, for
an account with no household, on its PRIMARY ENTITY. :func:`subject_of`.

PROFILES — one live approved profile per subject and tax year
──────────────────────────────────────────────────────────────────────────────
Entered by a holder of manage_tax (rates as decimal fractions, the as-of date
and a source note); approved by a DIFFERENT holder of approve_tax on a
maker-checker human task (``tax_profile_approval``; the CHECK
tax_profiles_maker_checker_chk backs it). A correction is a new version that
replaces the approved one when approved (valid axis); every version is kept.

COMBINED RATES — computed, never stored (:func:`combined_rates`)
  ST = federal ordinary + NIIT (3.8% when it applies) + state + city
  LT = federal long-term + NIIT + state LT + city LT, where a state / city that
       taxes long-term gains as ordinary income (no separate LT rate entered)
       uses its ordinary rate.
No approved profile → every tax value reads "not estimated — no tax profile"
(:data:`NO_PROFILE`), never a default rate.

CARRYFORWARDS — from the prior year's return (ST and LT loss carried in, with
the source document), approved the same way (``loss_carryforward_approval``).

DECLARED OUTSIDE TRADES — trades in accounts OUTSIDE the platform that the
adviser declares (security, side, date, loss yes / no / unknown, a source
note) so they count in the household's wash-sale windows; a sale with a
realized amount also enters the loss bank. Protective input: it takes effect
on entry; a correction or a withdrawal closes the row (valid axis).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from services import human_tasks as HT

log = logging.getLogger(__name__)

VIEW = "view_tax"
MANAGE = "manage_tax"
APPROVE = "approve_tax"
T_PROFILE = "public.tax_profiles"
T_CF = "public.loss_carryforwards"
T_DECL = "public.declared_outside_trades"
TASK_PROFILE = "tax_profile_approval"
TASK_CF = "loss_carryforward_approval"
SUBJECT_PROFILE = "tax_profile"
SUBJECT_CF = "loss_carryforward"
NIIT_RATE = Decimal("0.038")
ESTIMATE = "estimate"
NO_PROFILE = "not estimated — no tax profile"
NEEDS_PRICES = "not estimated — needs prices"

FILING_STATUS_LABELS = {"single": "Single", "married_filing_jointly": "Married filing jointly",
                        "married_filing_separately": "Married filing separately",
                        "head_of_household": "Head of household",
                        "qualifying_surviving_spouse": "Qualifying surviving spouse"}
STATUS_LABELS = {"pending_approval": "Awaiting approval", "approved": "Approved", "rejected": "Rejected",
                 "superseded": "Replaced by a newer version"}
SUBJECT_TYPE_LABELS = {"household": "Household", "entity": "Member (no household)"}
LOSS_LABELS = {"yes": "At a loss", "no": "Not at a loss", "unknown": "Unknown"}
TERM_LABELS = {"short": "Short-term", "long": "Long-term"}
RATE_FIELDS = ("federal_ordinary_rate", "federal_lt_rate", "state_rate", "state_lt_rate", "city_rate", "city_lt_rate")


class TaxError(ValueError):
    """A request the rules refuse (422)."""


class TaxNotFound(LookupError):
    pass


class TaxConflict(RuntimeError):
    """The state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


MAKER_CHECKER_RULE = ("Maker-checker: you entered this, so a different holder of approve_tax must approve or "
                      "reject it")


def _dumps(v):
    from services.account_models import _dumps as d

    return d(v)


def _loads(v):
    from services.account_models import _loads as l_

    return l_(v)


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise TaxNotFound(f"{what} not found") from None


def _num(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v else None


# ── Subject ─────────────────────────────────────────────────────────────────
def subject_of(account: dict) -> tuple[str, str]:
    """The tax subject of an account: its household, else its primary entity."""
    if account.get("household_id"):
        return "household", str(account["household_id"])
    return "entity", str(account["primary_entity_id"])


async def check_subject(conn, org_id, subject_type: str, subject_id) -> dict:
    """The subject must be one of this org's households / entities."""
    sid = _uuid(subject_id, "subject")
    if subject_type == "household":
        r = await conn.fetchrow("SELECT id, name FROM public.households WHERE id = $1::uuid AND org_id = $2::uuid",
                                sid, str(org_id))
        if r is None:
            raise TaxNotFound("household not found")
        return {"subject_type": "household", "subject_id": str(r["id"]), "name": r["name"]}
    if subject_type == "entity":
        r = await conn.fetchrow("SELECT id, display_name FROM public.entities WHERE id = $1::uuid AND org_id = $2::uuid "
                                "AND system_to IS NULL ORDER BY valid_to NULLS FIRST LIMIT 1", sid, str(org_id))
        if r is None:
            raise TaxNotFound("member not found")
        return {"subject_type": "entity", "subject_id": str(r["id"]), "name": r["display_name"]}
    raise TaxError(f"subject type is one of {list(SUBJECT_TYPE_LABELS)}")


async def subjects(conn, org_id) -> list[dict]:
    """Every subject that has an open account: its households, and the primary
    entities of accounts with no household — with their accounts."""
    rows = await conn.fetch(
        """SELECT a.id, a.account_number_masked, a.registration_type, a.tax_status, a.household_id,
                  a.primary_entity_id, h.name AS household_name, e.display_name AS entity_name
           FROM public.accounts a
           LEFT JOIN public.households h ON h.id = a.household_id
           LEFT JOIN LATERAL (SELECT display_name FROM public.entities x WHERE x.id = a.primary_entity_id
                              AND x.system_to IS NULL ORDER BY x.valid_to NULLS FIRST LIMIT 1) e ON true
           WHERE a.org_id = $1::uuid AND a.system_to IS NULL AND a.valid_to IS NULL AND a.closed_on IS NULL
           ORDER BY 7, 8, a.account_number_masked""", str(org_id))
    out: dict[tuple, dict] = {}
    for r in rows:
        st, sid = subject_of(dict(r))
        s = out.setdefault((st, sid), {"subject_type": st, "subject_id": sid,
                                       "name": r["household_name"] if st == "household" else r["entity_name"],
                                       "accounts": []})
        s["accounts"].append({"id": str(r["id"]), "account_number_masked": r["account_number_masked"],
                              "registration_type": r["registration_type"], "tax_status": r["tax_status"]})
    return list(out.values())


# ── Combined rates ──────────────────────────────────────────────────────────
def combined_rates(p: dict | None) -> dict | None:
    """{st, lt, parts, as_of, label} from an approved profile — never stored,
    never defaulted. None without a profile."""
    if not p:
        return None

    def d(k):
        v = p.get(k)
        return None if v is None else Decimal(str(v))

    niit = d("niit_rate") if p.get("niit_applies") else Decimal(0)
    state, city = d("state_rate") or Decimal(0), d("city_rate") or Decimal(0)
    state_lt = d("state_lt_rate") if d("state_lt_rate") is not None else state
    city_lt = d("city_lt_rate") if d("city_lt_rate") is not None else city
    st = d("federal_ordinary_rate") + niit + state + city
    lt = d("federal_lt_rate") + niit + state_lt + city_lt
    return {"st": st, "lt": lt, "label": ESTIMATE, "profile_id": str(p["id"]), "tax_year": p["tax_year"],
            "as_of": _iso(p["as_of"]), "filing_status": p["filing_status"],
            "parts": {"federal_ordinary": str(d("federal_ordinary_rate")), "federal_lt": str(d("federal_lt_rate")),
                      "niit": str(niit), "state": str(state), "state_lt": str(state_lt), "city": str(city),
                      "city_lt": str(city_lt),
                      "state_lt_source": "entered" if d("state_lt_rate") is not None else "ordinary (no LT rate entered)",
                      "city_lt_source": "entered" if d("city_lt_rate") is not None else "ordinary (no LT rate entered)"}}


def rates_out(r: dict | None) -> dict:
    if r is None:
        return {"estimated": False, "label": NO_PROFILE}
    return {"estimated": True, "label": ESTIMATE, "st": str(r["st"]), "lt": str(r["lt"]), "parts": r["parts"],
            "as_of": r["as_of"], "tax_year": r["tax_year"], "profile_id": r["profile_id"],
            "filing_status": r["filing_status"]}


async def approved_profile(conn, org_id, subject_type: str, subject_id, tax_year: int) -> dict | None:
    r = await conn.fetchrow(
        f"SELECT * FROM {T_PROFILE} WHERE org_id = $1::uuid AND subject_type = $2 AND subject_id = $3::uuid "
        "AND tax_year = $4 AND status = 'approved' AND valid_to IS NULL AND system_to IS NULL",
        str(org_id), subject_type, str(subject_id), int(tax_year))
    return dict(r) if r else None


async def profile_for_account(conn, org_id, account: dict, tax_year: int) -> dict | None:
    st, sid = subject_of(account)
    return await approved_profile(conn, org_id, st, sid, tax_year)


# ── Writing primitives (system-axis archive keeps the id) ───────────────────
async def _archive(conn, table: str, row_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(row_id))
    if got is None:
        raise TaxConflict("the row is not live — refusing to change it")


async def _update(conn, table: str, row_id, fields: dict) -> dict:
    await _archive(conn, table, row_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    row = await conn.fetchrow(f"UPDATE {table} SET {sets}, system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(row_id), *[fields[c] for c in cols])
    if row is None:
        raise TaxConflict("the row changed underneath this request — reload and retry")
    return dict(row)


async def _audit(conn, org_id, user_id, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"tax.{action}", resource_type, str(resource_id),
        _dumps(payload))


def _rate(v, name: str, *, nullable: bool = False) -> Decimal | None:
    if v is None:
        if nullable:
            return None
        raise TaxError(f"{name} is required (as a decimal fraction, e.g. 0.37) — a rate is never assumed")
    if not isinstance(v, Decimal):
        raise TaxError(f"{name} must be a decimal")
    if v < 0 or v >= 1:
        raise TaxError(f"{name} is a fraction from 0 up to (not including) 1")
    return v.quantize(Decimal("0.000001"))


# ── Profiles ────────────────────────────────────────────────────────────────
async def propose_profile(pool, *, org_id, actor_id, subject_type: str, subject_id, tax_year: int, data: dict) -> dict:
    """A new profile version (pending) + its maker-checker task."""
    year = int(tax_year)
    if not 2000 <= year <= 2100:
        raise TaxError("tax year is from 2000 to 2100")
    fs = data.get("filing_status")
    if fs not in FILING_STATUS_LABELS:
        raise TaxError(f"filing status is one of {list(FILING_STATUS_LABELS)}")
    rates = {"federal_ordinary_rate": _rate(data.get("federal_ordinary_rate"), "federal ordinary / short-term rate"),
             "federal_lt_rate": _rate(data.get("federal_lt_rate"), "federal long-term rate"),
             "state_rate": _rate(data.get("state_rate") if data.get("state_rate") is not None else Decimal(0),
                                 "state rate"),
             "state_lt_rate": _rate(data.get("state_lt_rate"), "state long-term rate", nullable=True),
             "city_rate": _rate(data.get("city_rate") if data.get("city_rate") is not None else Decimal(0),
                                "city rate"),
             "city_lt_rate": _rate(data.get("city_lt_rate"), "city long-term rate", nullable=True)}
    if not isinstance(data.get("niit_applies"), bool):
        raise TaxError("say whether the 3.8% net investment income tax applies (true or false)")
    as_of = data.get("as_of")
    if not isinstance(as_of, date):
        raise TaxError("the as-of date of these rates is required")
    note = (data.get("source_note") or "").strip()
    if not note:
        raise TaxError("a source note is required (where the adviser took these rates from)")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        subj = await check_subject(conn, org_id, subject_type, subject_id)
        pending = await conn.fetchval(
            f"SELECT id FROM {T_PROFILE} WHERE org_id = $1::uuid AND subject_type = $2 AND subject_id = $3::uuid "
            "AND tax_year = $4 AND status = 'pending_approval' AND valid_to IS NULL AND system_to IS NULL",
            str(org_id), subj["subject_type"], subj["subject_id"], year)
        if pending:
            raise TaxConflict("a profile for this subject and year is already awaiting approval — decide it first")
        current = await approved_profile(conn, org_id, subj["subject_type"], subj["subject_id"], year)
        vno = int(await conn.fetchval(
            f"SELECT COALESCE(max(version_no), 0) FROM {T_PROFILE} WHERE org_id = $1::uuid AND subject_type = $2 "
            "AND subject_id = $3::uuid AND tax_year = $4", str(org_id), subj["subject_type"], subj["subject_id"],
            year)) + 1
    new_id = uuid.uuid4()
    preview = combined_rates({**rates, "id": new_id, "niit_applies": data["niit_applies"], "niit_rate": NIIT_RATE,
                              "tax_year": year, "as_of": as_of, "filing_status": fs})
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_PROFILE, subject_type=SUBJECT_PROFILE, subject_id=new_id, maker_id=actor["id"],
        eligible_permission=APPROVE, title=f"Approve tax profile: {subj['name']} — {year} (v{vno})",
        payload={"subject": subj, "tax_year": year, "filing_status": fs, "as_of": as_of.isoformat(),
                 "combined": rates_out(preview), "source_note": note},
        action_path="/portfolio/tax?view=profiles")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_PROFILE} (id, org_id, subject_type, subject_id, tax_year, version_no,
                            filing_status, federal_ordinary_rate, federal_lt_rate, niit_applies, niit_rate, state_code,
                            state_rate, state_lt_rate, city, city_rate, city_lt_rate, as_of, source, source_note, notes,
                            status, entered_by, approval_task_id, replaces_id)
                        VALUES ($1, $2::uuid, $3, $4::uuid, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17,
                                $18, 'adviser', $19, $20, 'pending_approval', $21::uuid, $22::uuid, $23::uuid)
                        RETURNING *""",
                    new_id, str(org_id), subj["subject_type"], subj["subject_id"], year, vno, fs,
                    rates["federal_ordinary_rate"], rates["federal_lt_rate"], data["niit_applies"], NIIT_RATE,
                    (data.get("state_code") or "").strip().upper() or None, rates["state_rate"],
                    rates["state_lt_rate"], (data.get("city") or "").strip() or None, rates["city_rate"],
                    rates["city_lt_rate"], as_of, note, (data.get("notes") or "").strip() or None, str(actor["id"]),
                    task["id"], str(current["id"]) if current else None)
                await _audit(conn, org_id, actor["id"], "profile_proposed", SUBJECT_PROFILE, new_id,
                             {"subject": subj, "tax_year": year, "version_no": vno, "task_id": task["id"]})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the tax profile could not be recorded")
        raise
    return {**dict(row), "task": task}


async def propose_carryforward(pool, *, org_id, actor_id, subject_type: str, subject_id, tax_year: int,
                               st_amount: Decimal, lt_amount: Decimal, source_document: str,
                               notes: str | None = None) -> dict:
    year = int(tax_year)
    if not 2000 <= year <= 2100:
        raise TaxError("tax year is from 2000 to 2100")
    for n, v in (("short-term carryforward", st_amount), ("long-term carryforward", lt_amount)):
        if not isinstance(v, Decimal) or v < 0:
            raise TaxError(f"{n} is a decimal amount of loss carried in, zero or more")
    if not (source_document or "").strip():
        raise TaxError("the source document (the prior year's return / Schedule D) is required")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        subj = await check_subject(conn, org_id, subject_type, subject_id)
        if await conn.fetchval(
                f"SELECT id FROM {T_CF} WHERE org_id = $1::uuid AND subject_type = $2 AND subject_id = $3::uuid "
                "AND tax_year = $4 AND status = 'pending_approval' AND valid_to IS NULL AND system_to IS NULL",
                str(org_id), subj["subject_type"], subj["subject_id"], year):
            raise TaxConflict("a carryforward for this subject and year is already awaiting approval")
        cur = await conn.fetchval(
            f"SELECT id FROM {T_CF} WHERE org_id = $1::uuid AND subject_type = $2 AND subject_id = $3::uuid "
            "AND tax_year = $4 AND status = 'approved' AND valid_to IS NULL AND system_to IS NULL",
            str(org_id), subj["subject_type"], subj["subject_id"], year)
        vno = int(await conn.fetchval(
            f"SELECT COALESCE(max(version_no), 0) FROM {T_CF} WHERE org_id = $1::uuid AND subject_type = $2 "
            "AND subject_id = $3::uuid AND tax_year = $4", str(org_id), subj["subject_type"], subj["subject_id"],
            year)) + 1
    new_id = uuid.uuid4()
    st_q, lt_q = st_amount.quantize(Decimal("0.01")), lt_amount.quantize(Decimal("0.01"))
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_CF, subject_type=SUBJECT_CF, subject_id=new_id, maker_id=actor["id"],
        eligible_permission=APPROVE, title=f"Approve loss carryforward: {subj['name']} — into {year} (v{vno})",
        payload={"subject": subj, "tax_year": year, "st_amount": str(st_q), "lt_amount": str(lt_q),
                 "source_document": source_document.strip()}, action_path="/portfolio/tax?view=profiles")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_CF} (id, org_id, subject_type, subject_id, tax_year, version_no, st_amount,
                            lt_amount, source_document, notes, status, entered_by, approval_task_id, replaces_id)
                        VALUES ($1, $2::uuid, $3, $4::uuid, $5, $6, $7, $8, $9, $10, 'pending_approval', $11::uuid,
                                $12::uuid, $13::uuid) RETURNING *""",
                    new_id, str(org_id), subj["subject_type"], subj["subject_id"], year, vno, st_q, lt_q,
                    source_document.strip(), (notes or "").strip() or None, str(actor["id"]), task["id"],
                    str(cur) if cur else None)
                await _audit(conn, org_id, actor["id"], "carryforward_proposed", SUBJECT_CF, new_id,
                             {"subject": subj, "tax_year": year, "task_id": task["id"]})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the carryforward could not be recorded")
        raise
    return {**dict(row), "task": task}


async def _apply_outcome(pool, table: str, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    """Apply a decided profile / carryforward task. Idempotent."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {table} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "pending_approval":
                return dict(row) if row else None
            row = dict(row)
            checker = uuid.UUID(str(actor_id))
            if checker == row["entered_by"]:
                raise MakerCheckerViolation(MAKER_CHECKER_RULE)
            now = datetime.now(timezone.utc)
            kind = "profile" if table == T_PROFILE else "carryforward"
            if outcome == "approve":
                prior = await conn.fetchrow(
                    f"SELECT id FROM {table} WHERE org_id = $1::uuid AND subject_type = $2 AND subject_id = $3 "
                    "AND tax_year = $4 AND status = 'approved' AND valid_to IS NULL AND system_to IS NULL FOR UPDATE",
                    str(org_id), row["subject_type"], row["subject_id"], row["tax_year"])
                if prior is not None:
                    await _update(conn, table, prior["id"], {"status": "superseded", "valid_to": now})
                new = await _update(conn, table, row["id"], {"status": "approved", "checker_id": checker,
                                                             "decided_at": now, "decision_note": note})
            else:
                new = await _update(conn, table, row["id"], {"status": "rejected", "checker_id": checker,
                                                             "decided_at": now, "decision_note": note})
            await _audit(conn, org_id, checker, f"{kind}_{'approved' if outcome == 'approve' else 'rejected'}",
                         SUBJECT_PROFILE if table == T_PROFILE else SUBJECT_CF, row["id"],
                         {"task_id": task["id"], "note": note})
    return new


async def _on_profile(pool, task, outcome, actor_id, note):
    return await _apply_outcome(pool, T_PROFILE, task["org_id"], task, outcome, actor_id, note)


async def _on_cf(pool, task, outcome, actor_id, note):
    return await _apply_outcome(pool, T_CF, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_PROFILE, label="Tax profile approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_profile)
HT.register_kind(TASK_CF, label="Loss carryforward approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_cf)


async def decide(pool, *, org_id, actor_id, kind: str, row_id, outcome: str, note: str | None = None) -> dict:
    """Approve / reject a pending profile or carryforward through its task.
    Refused (each leaving it unchanged): 403 its maker; 409 not pending; 422 a
    rejection without a note."""
    table = {"profile": T_PROFILE, "carryforward": T_CF}.get(kind)
    if table is None:
        raise TaxError("kind is profile or carryforward")
    if outcome not in ("approve", "reject"):
        raise TaxError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise TaxError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid "
                                  "AND system_to IS NULL", _uuid(row_id, kind), str(org_id))
        if row is None:
            raise TaxNotFound(f"{kind} not found")
        if row["status"] == "pending_approval" and row["approval_task_id"]:
            task = await HT.get_task(conn, org_id, row["approval_task_id"])
        else:
            task = None
    if row["status"] != "pending_approval":
        raise TaxConflict(f"this {kind} is {STATUS_LABELS[row['status']].lower()}, not awaiting approval")
    if actor["id"] == row["entered_by"]:
        raise MakerCheckerViolation(MAKER_CHECKER_RULE)
    if task and task["status"] == "completed" and task["outcome"] in ("approve", "reject"):
        await _apply_outcome(pool, table, org_id, task, task["outcome"], task["completed_by"], task["note"])
        raise TaxConflict("this was already decided — reload")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise TaxConflict(str(exc)) from exc
    return done.get("subject_result") or dict(row)


def profile_out(r: dict) -> dict:
    return {"id": str(r["id"]), "subject_type": r["subject_type"], "subject_id": str(r["subject_id"]),
            "tax_year": r["tax_year"], "version_no": r["version_no"], "filing_status": r["filing_status"],
            **{k: _num(r[k]) for k in RATE_FIELDS}, "niit_applies": r["niit_applies"], "niit_rate": _num(r["niit_rate"]),
            "state_code": r["state_code"], "city": r["city"], "as_of": _iso(r["as_of"]), "source": r["source"],
            "source_note": r["source_note"], "notes": r["notes"], "status": r["status"],
            "entered_by": str(r["entered_by"]), "checker_id": str(r["checker_id"]) if r["checker_id"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"],
            "current": r["valid_to"] is None, "created_at": _iso(r["created_at"]),
            "combined": rates_out(combined_rates(r) if r["status"] == "approved" else None)
            if r["status"] == "approved" else {"estimated": False, "label": "not in use until approved",
                                               "preview": rates_out(combined_rates(r))}}


def carryforward_out(r: dict) -> dict:
    return {"id": str(r["id"]), "subject_type": r["subject_type"], "subject_id": str(r["subject_id"]),
            "tax_year": r["tax_year"], "version_no": r["version_no"], "st_amount": _num(r["st_amount"]),
            "lt_amount": _num(r["lt_amount"]), "source_document": r["source_document"], "notes": r["notes"],
            "status": r["status"], "entered_by": str(r["entered_by"]),
            "checker_id": str(r["checker_id"]) if r["checker_id"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"],
            "current": r["valid_to"] is None, "created_at": _iso(r["created_at"])}


async def list_profiles(conn, org_id, *, subject_type=None, subject_id=None, tax_year=None) -> list[dict]:
    args: list = [str(org_id)]
    sql = f"SELECT * FROM {T_PROFILE} WHERE org_id = $1::uuid AND system_to IS NULL"
    if subject_type and subject_id:
        args += [subject_type, _uuid(subject_id, "subject")]
        sql += f" AND subject_type = ${len(args) - 1} AND subject_id = ${len(args)}::uuid"
    if tax_year:
        args.append(int(tax_year))
        sql += f" AND tax_year = ${len(args)}"
    sql += " ORDER BY subject_type, subject_id, tax_year DESC, version_no DESC"
    return [profile_out(dict(r)) for r in await conn.fetch(sql, *args)]


async def list_carryforwards(conn, org_id, *, subject_type=None, subject_id=None) -> list[dict]:
    args: list = [str(org_id)]
    sql = f"SELECT * FROM {T_CF} WHERE org_id = $1::uuid AND system_to IS NULL"
    if subject_type and subject_id:
        args += [subject_type, _uuid(subject_id, "subject")]
        sql += f" AND subject_type = ${len(args) - 1} AND subject_id = ${len(args)}::uuid"
    sql += " ORDER BY subject_type, subject_id, tax_year DESC, version_no DESC"
    return [carryforward_out(dict(r)) for r in await conn.fetch(sql, *args)]


def actions_for(row: dict, *, can_approve: bool, user_id) -> list[str]:
    """The SERVER's list of actions on one profile / carryforward."""
    if can_approve and row["status"] == "pending_approval" and row["entered_by"] != str(user_id):
        return ["approve", "reject"]
    return []


# ── Declared outside trades ─────────────────────────────────────────────────
async def declare_trade(pool, *, org_id, actor_id, subject_type: str, subject_id, data: dict) -> dict:
    side = data.get("side")
    if side not in ("buy", "sell"):
        raise TaxError("side is buy or sell")
    td = data.get("trade_date")
    if not isinstance(td, date):
        raise TaxError("the trade date is required")
    if td > date.today():
        raise TaxError("a declared trade has already happened — the trade date is not in the future")
    loss = data.get("loss")
    amount = data.get("realized_amount")
    term = data.get("term")
    if side == "buy":
        if loss is not None or amount is not None or term is not None:
            raise TaxError("a buy has no loss, realized amount or term")
    else:
        if loss not in LOSS_LABELS:
            raise TaxError("say whether the sale was at a loss: yes, no or unknown")
        if amount is not None:
            if not isinstance(amount, Decimal):
                raise TaxError("the realized amount is a decimal")
            if term not in TERM_LABELS:
                raise TaxError("a realized amount needs its term (short or long)")
            if (loss == "yes" and amount >= 0) or (loss == "no" and amount < 0) or loss == "unknown":
                raise TaxError("the realized amount must agree with 'at a loss' (negative for a loss)")
            amount = amount.quantize(Decimal("0.01"))
    qty = data.get("quantity")
    if qty is not None and (not isinstance(qty, Decimal) or qty <= 0):
        raise TaxError("quantity is a decimal above zero")
    cls = data.get("account_tax_class") or "unknown"
    if cls not in ("taxable", "tax_advantaged", "unknown"):
        raise TaxError("the outside account is taxable, tax_advantaged (an IRA / Roth) or unknown")
    desc = (data.get("account_description") or "").strip()
    note = (data.get("source_note") or "").strip()
    if not desc or not note:
        raise TaxError("describe the outside account and give a source note")
    gid = _uuid(data.get("global_security_id"), "security")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        subj = await check_subject(conn, org_id, subject_type, subject_id)
        if not await conn.fetchval("SELECT 1 FROM portfolio.securities_global WHERE id = $1::uuid AND system_to IS NULL",
                                   gid):
            raise TaxError("the security is not in the security master")
        replaces = data.get("replaces_id")
        async with conn.transaction():
            if replaces:
                old = await conn.fetchrow(f"SELECT * FROM {T_DECL} WHERE id = $1::uuid AND org_id = $2::uuid "
                                          "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE",
                                          _uuid(replaces, "declared trade"), str(org_id))
                if old is None:
                    raise TaxNotFound("the declared trade to correct is not live")
                await conn.execute(f"UPDATE {T_DECL} SET valid_to = now(), withdraw_reason = 'corrected' WHERE id = $1",
                                   old["id"])
            row = await conn.fetchrow(
                f"""INSERT INTO {T_DECL} (org_id, subject_type, subject_id, account_description, account_tax_class,
                        global_security_id, side, trade_date, quantity, loss, realized_amount, term, declared_by,
                        source_note, replaces_id)
                    VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6::uuid, $7, $8, $9, $10, $11, $12, $13::uuid, $14,
                            $15::uuid) RETURNING *""",
                str(org_id), subj["subject_type"], subj["subject_id"], desc[:500], cls, gid, side, td, qty,
                loss if side == "sell" else None, amount, term if amount is not None else None, str(actor["id"]),
                note[:2000], str(replaces) if replaces else None)
            await _audit(conn, org_id, actor["id"], "outside_trade_declared", "declared_outside_trade", row["id"],
                         {"subject": subj, "side": side, "trade_date": td.isoformat(), "security_id": gid})
    return dict(row)


async def withdraw_trade(pool, *, org_id, actor_id, trade_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise TaxError("withdrawing a declared trade needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await conn.fetchrow(f"UPDATE {T_DECL} SET valid_to = now(), withdraw_reason = $3 WHERE id = $1::uuid "
                                      "AND org_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL RETURNING *",
                                      _uuid(trade_id, "declared trade"), str(org_id), reason.strip()[:2000])
            if row is None:
                raise TaxNotFound("declared trade not found (or already withdrawn)")
            await _audit(conn, org_id, actor["id"], "outside_trade_withdrawn", "declared_outside_trade", row["id"],
                         {"reason": reason.strip()})
    return dict(row)


def declared_out(r: dict) -> dict:
    return {"id": str(r["id"]), "subject_type": r["subject_type"], "subject_id": str(r["subject_id"]),
            "account_description": r["account_description"], "account_tax_class": r["account_tax_class"],
            "global_security_id": str(r["global_security_id"]), "security": r.get("security"),
            "side": r["side"], "trade_date": _iso(r["trade_date"]), "quantity": _num(r["quantity"]),
            "loss": r["loss"], "realized_amount": _num(r["realized_amount"]), "term": r["term"],
            "declared_by": str(r["declared_by"]), "source_note": r["source_note"],
            "live": r["valid_to"] is None, "withdraw_reason": r["withdraw_reason"], "created_at": _iso(r["created_at"])}


async def list_declared(conn, org_id, *, include_closed: bool = False) -> list[dict]:
    from services.wash_sale import security_labels

    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_DECL} WHERE org_id = $1::uuid AND system_to IS NULL"
        + ("" if include_closed else " AND valid_to IS NULL") + " ORDER BY trade_date DESC, created_at DESC",
        str(org_id))]
    labels = await security_labels(conn, {r["global_security_id"] for r in rows})
    return [declared_out({**r, "security": labels.get(str(r["global_security_id"]))}) for r in rows]


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"filing_statuses": pairs(FILING_STATUS_LABELS), "tax_input_statuses": pairs(STATUS_LABELS),
            "subject_types": pairs(SUBJECT_TYPE_LABELS), "loss_values": pairs(LOSS_LABELS),
            "terms": pairs(TERM_LABELS), "sides": [{"key": "buy", "label": "Buy"}, {"key": "sell", "label": "Sell"}],
            "estimate_label": ESTIMATE, "no_profile_label": NO_PROFILE, "needs_prices_label": NEEDS_PRICES}
