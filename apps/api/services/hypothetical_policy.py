"""REP10 — the firm's hypothetical-performance policy (SEC Marketing Rule —
counsel's item), versioned and approved by a second person (rep9's allocation
policy pattern, rep7b's authority_change_guard on the table).

The policy is the firm's OWN words; the code enforces only its mechanics:
  audience   staff_only | staff_and_members — who may see a backtest;
  disclosure the text every backtest screen, PDF and envelope carries.
With NO approved policy: backtests are staff-only and carry
"no hypothetical-performance policy approved — staff use only". A member
envelope NEVER carries a backtest unless the approved policy's audience is
staff_and_members (services.backtest.member_view).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import human_tasks as HT
from services import rep10_common as C
from services import sod  # S30 — the ONE maker-checker rule

T_POL = "public.hypothetical_performance_policies"
MANAGE = "manage_hypothetical_policy"
VIEW = "view_replication"
TASK_KIND = "hypothetical_policy_approval"
SUBJECT = "hypothetical_policy"
# BUGFIX2 (BT-02): a standalone sentence wherever it is shown ("Hypothetical performance. No …"), so it is
# capitalised here, at its one source, rather than patched per screen.
NO_POLICY = "No hypothetical-performance policy approved — staff use only"
AUDIENCE_LABELS = {"staff_only": "Staff only", "staff_and_members": "Staff, and members with the disclosure"}
STATUS_LABELS = {"draft": "Awaiting a second person's approval", "approved": "Approved", "rejected": "Rejected"}
OPEN_ITEMS = (
    "The hypothetical-performance policy itself (SEC Marketing Rule, 206(4)-1) and who may see a backtest are for "
    "the firm and its counsel — this screen records the firm's decision; it does not make it.",
    "Factor-score data licences: a factor tilt reads scores only from a file the firm uploads under its own licence "
    "(or a contracted vendor) — counsel confirms the licence allows it.",
)


async def current(conn, org_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE org_id = $1::uuid AND status = 'approved' "
                              "AND valid_to IS NULL AND system_to IS NULL", str(org_id))
    return dict(row) if row else None


def policy_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "version_no": r["version_no"], "policy_text": r["policy_text"],
            "audience": r["audience"], "audience_label": AUDIENCE_LABELS[r["audience"]],
            "disclosure_text": r["disclosure_text"], "reason": r["reason"], "status": r["status"],
            "maker_id": str(r["maker_id"]), "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": C.iso(r["decided_at"]), "decision_note": r["decision_note"],
            "valid_to": C.iso(r["valid_to"]), "superseded_reason": r["superseded_reason"],
            "created_at": C.iso(r["created_at"]), "in_effect": r["status"] == "approved" and r["valid_to"] is None}


LIBRARY_KEY = "hypothetical.performance"


async def library_disclosure(conn, org_id, household_id=None) -> dict | None:
    """DISCLOSURES1 — an ORG-or-lower published library version of hypothetical.performance, or None. A platform
    default never replaces a firm's approved policy text (the firm's own words, approved by a second person)."""
    from services import disclosures as DL

    res = await DL.resolve_safe(conn, LIBRARY_KEY, org_id=org_id, household_id=household_id)
    return res if res["source"] == "library" and res["scope"] != "platform" else None


async def gate_for(conn, org_id, policy: dict | None = None, *, household_id=None, loaded: bool = False) -> dict:
    """:func:`gate` with the disclosure text through the library (the Marketing Rule gate itself is unchanged):
    no approved policy → staff only, NO_POLICY; an approved policy → the org's (or household's) library version when
    one is in force, else the policy's own approved disclosure_text."""
    if not loaded and policy is None:
        policy = await current(conn, org_id)
    g = gate(policy)
    if policy is None:
        return {**g, "disclosure_source": "policy_gate"}
    lib = await library_disclosure(conn, org_id, household_id)
    if lib is None:
        return {**g, "disclosure_source": "policy"}
    return {**g, "disclosure": lib["text"], "disclosure_source": "library", "disclosure_version_id": lib["version_id"],
            "disclosure_version_no": lib["version_no"], "disclosure_file_href": lib["file_href"]}


def gate(policy: dict | None) -> dict:
    """What every backtest carries: the audience, whether members may see it, and the disclosure text."""
    if policy is None:
        return {"policy_id": None, "audience": "staff_only", "members_allowed": False, "disclosure": NO_POLICY,
                "label": C.HYPOTHETICAL}
    return {"policy_id": str(policy["id"]), "version_no": policy["version_no"], "audience": policy["audience"],
            "members_allowed": policy["audience"] == "staff_and_members", "disclosure": policy["disclosure_text"],
            "label": C.HYPOTHETICAL}


async def list_policies(conn, org_id) -> list[dict]:
    return [policy_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_POL} WHERE org_id = $1::uuid AND system_to IS NULL ORDER BY version_no DESC", str(org_id))]


async def request(pool, *, org_id, actor_id, policy_text: str, audience: str, disclosure_text: str,
                  reason: str) -> dict:
    """A new policy VERSION, a draft until a second holder of manage_hypothetical_policy approves it."""
    if audience not in AUDIENCE_LABELS:
        raise C.Rep10Error(f"audience is one of {list(AUDIENCE_LABELS)}")
    if not (policy_text or "").strip():
        raise C.Rep10Error("the policy text is the firm's own words — it cannot be empty")
    if not (disclosure_text or "").strip():
        raise C.Rep10Error("the disclosure every backtest carries cannot be empty")
    if not (reason or "").strip():
        raise C.Rep10Error("a policy version needs a reason")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        if await conn.fetchval(f"SELECT 1 FROM {T_POL} WHERE org_id = $1::uuid AND status = 'draft' "
                               "AND system_to IS NULL", str(org_id)):
            raise C.Rep10Conflict("a policy version is already awaiting approval — decide it first")
        version = int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T_POL} WHERE "
                                          "org_id = $1::uuid AND system_to IS NULL", str(org_id)))
    pid = uuid.uuid4()
    task = await HT.open_task(pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=pid,
                              maker_id=actor["id"], eligible_permission=MANAGE,
                              title=f"Approve hypothetical-performance policy version {version}",
                              payload={"version_no": version, "audience": audience},
                              action_path="/admin/hypothetical-policy")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_POL} (id, org_id, version_no, policy_text, audience, disclosure_text, reason,
                            status, maker_id, approval_task_id)
                        VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, 'draft', $8::uuid, $9::uuid) RETURNING *""",
                    pid, str(org_id), version, policy_text.strip(), audience, disclosure_text.strip(), reason.strip(),
                    str(actor["id"]), task["id"])
                await C.audit(conn, org_id, actor["id"], "hypothetical_policy_requested", SUBJECT, pid,
                              {"version_no": version, "audience": audience, "task_id": task["id"]})
    except Exception:
        await C.withdraw_task(pool, task["id"], "the policy version could not be recorded")
        raise
    return {**policy_out(dict(row)), "task": task}


async def decide(pool, *, org_id, actor_id, policy_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise C.Rep10Error("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise C.Rep10Error("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        row = await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                  "system_to IS NULL", C.uid(policy_id, "policy"), str(org_id))
    if row is None:
        raise C.Rep10NotFound("policy not found")
    if row["status"] != "draft":
        raise C.Rep10Conflict(f"the policy version is {row['status']}, not awaiting approval")
    async with pool.acquire() as conn:
        await sod.gate(conn, org_id=org_id, item_type=sod.HYPOTHETICAL_POLICY, checker_id=actor["id"],
                       maker_ids=[row["maker_id"]], item_id=row["id"], site="hypothetical_policy.decide",
                       refuse=lambda d: C.MakerCheckerViolation(f"maker-checker: you wrote this policy version, so a different holder of "
                                                              f"{MANAGE} must {outcome} it"))
    await C.complete(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"], outcome=outcome,
                     note=note)
    async with pool.acquire() as conn:
        return policy_out(dict(await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE id = $1 AND system_to IS NULL",
                                                   row["id"])))


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if row is None or row["status"] != "draft":
                return policy_out(dict(row)) if row else None
            checker = uuid.UUID(str(actor_id))
            await sod.gate(conn, org_id=task["org_id"], item_type=sod.HYPOTHETICAL_POLICY, checker_id=checker,
                           maker_ids=[row["maker_id"]], item_id=row["id"], site="hypothetical_policy._apply",
                           refuse=lambda d: C.MakerCheckerViolation("maker-checker: the writer of a policy version cannot approve it"))
            status = "approved" if outcome == "approve" else "rejected"
            if status == "approved":
                prev = await conn.fetchrow(f"SELECT id FROM {T_POL} WHERE org_id = $1::uuid AND status = 'approved' "
                                           "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", str(org_id))
                if prev:
                    await conn.execute(f"UPDATE {T_POL} SET valid_to = now(), superseded_reason = $2 WHERE id = $1",
                                       prev["id"], f"superseded by version {row['version_no']}")
            await C.archive(conn, T_POL, row["id"])
            new = dict(await conn.fetchrow(
                f"UPDATE {T_POL} SET status = $2, approved_by = $3, decided_at = $4, decision_note = $5, "
                "system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING *", row["id"], status, checker,
                datetime.now(timezone.utc), note))
            await C.audit(conn, org_id, checker, f"hypothetical_policy_{status}", SUBJECT, row["id"],
                          {"task_id": task["id"], "note": note, "version_no": row["version_no"]})
    return policy_out(new)


async def _on_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_KIND, label="Hypothetical-performance policy approval", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=_on_complete)


def vocabularies() -> dict:
    return {"audiences": [{"key": k, "label": v} for k, v in AUDIENCE_LABELS.items()],
            "policy_statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "no_policy_label": NO_POLICY, "open_items": list(OPEN_ITEMS)}
