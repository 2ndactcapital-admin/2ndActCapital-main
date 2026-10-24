"""The substitution table (rep6) — a versioned, APPROVED compliance artifact.

SETS — per security, its replacement candidates in order
──────────────────────────────────────────────────────────────────────────────
A holder of manage_tax proposes a set version for a security: candidates in
rank order, each with a reason and evidence ("same industry", "different index
provider"). The model's own alternates (rep3's model_version_positions.
alternates) are offered as a STARTING POINT (:func:`suggestions`) — a
suggestion is never a pair by itself: it becomes a candidate only with a reason,
evidence and the set's approval. A DIFFERENT holder of approve_tax approves it
on a maker-checker human task (``substitution_set_approval``); the approved
version replaces the one before it (valid axis); every version is kept. ONLY an
approved, live set is read by the engine (services.wash_sale.approved_substitutes).

DENIALS — pairs that are substantially identical and may NEVER pair
──────────────────────────────────────────────────────────────────────────────
  same security      always (the rule in code, never a row)
  same_index         two funds whose APPROVED rep1 terms carry the same index_name,
                     both in this org's registry (seeded by :func:`seed_rule_denials`)
  share_class        share classes the security master links: two securities with
                     the same securities_global.canonical_id, or two registry
                     entries with the same SEC series id (seeded the same way)
  adviser            a pair an adviser enters — active once a second person
                     approves it (``substitution_denial_approval``)
A rule-seeded denial is the system's and takes effect at once (it only ever
makes the engine more careful); retiring ANY denial needs approve_tax and is
never done by its maker. A denial OVERRIDES a set: a candidate denied with its
security is refused when the set is proposed and skipped by the engine if the
denial comes later.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from services import human_tasks as HT
from services.tax_profiles import (APPROVE, MakerCheckerViolation, TaxConflict, TaxError, TaxNotFound, _audit,
                                   _iso, _update, _uuid)

log = logging.getLogger(__name__)

T_SET = "public.substitution_sets"
T_CAND = "public.substitution_set_candidates"
T_DENY = "public.substitution_denials"
TASK_SET = "substitution_set_approval"
TASK_DENY = "substitution_denial_approval"
SUBJECT_SET = "substitution_set"
SUBJECT_DENY = "substitution_denial"
SET_STATUS_LABELS = {"pending_approval": "Awaiting approval", "approved": "Approved (in use)", "rejected": "Rejected",
                     "superseded": "Replaced by a newer version", "retired": "Retired"}
DENIAL_STATUS_LABELS = {"pending_approval": "Awaiting approval", "active": "Active", "rejected": "Rejected",
                        "retired": "Retired"}
DENIAL_RULE_LABELS = {"same_index": "Two funds tracking the same index",
                      "share_class": "Share classes the security master links",
                      "adviser": "Entered by an adviser"}
CANDIDATE_SOURCE_LABELS = {"adviser": "Entered by an adviser", "model_alternate": "From the model's alternates"}
MAKER_CHECKER_RULE = ("Maker-checker: you proposed this, so a different holder of approve_tax must approve or "
                      "reject it")


def _pair(a, b) -> tuple[str, str]:
    a, b = str(a), str(b)
    return (a, b) if a < b else (b, a)


async def _security_ok(conn, gid: str) -> None:
    if not await conn.fetchval("SELECT 1 FROM portfolio.securities_global WHERE id = $1::uuid AND system_to IS NULL",
                               gid):
        raise TaxError(f"security {gid} is not in the security master")


# ── Sets ────────────────────────────────────────────────────────────────────
async def propose_set(pool, *, org_id, actor_id, security_id, candidates: list[dict], notes: str | None = None) -> dict:
    """A new set version (pending) with its candidates + the maker-checker task."""
    from services.wash_sale import active_denials

    sid = _uuid(security_id, "security")
    if not candidates:
        raise TaxError("a substitution set needs at least one candidate")
    if len(candidates) > 20:
        raise TaxError("at most 20 candidates")
    clean, seen = [], set()
    for i, c in enumerate(candidates, start=1):
        rid = _uuid(c.get("replacement_security_id"), "replacement security")
        reason, evidence = (c.get("reason") or "").strip(), (c.get("evidence") or "").strip()
        if not reason or not evidence:
            raise TaxError("every candidate needs a reason and evidence")
        if rid == sid:
            raise TaxError("a security is never its own substitute (it is substantially identical to itself)")
        if rid in seen:
            raise TaxError("a candidate is listed twice")
        src = c.get("source") or "adviser"
        if src not in CANDIDATE_SOURCE_LABELS:
            raise TaxError(f"candidate source is one of {list(CANDIDATE_SOURCE_LABELS)}")
        seen.add(rid)
        clean.append({"rank": i, "replacement_security_id": rid, "reason": reason[:1000], "evidence": evidence[:2000],
                      "source": src})
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        await _security_ok(conn, sid)
        denials = await active_denials(conn, org_id)
        for c in clean:
            await _security_ok(conn, c["replacement_security_id"])
            if frozenset((sid, c["replacement_security_id"])) in denials:
                raise TaxError("a denial pairs this candidate with the security as substantially identical — a "
                               "denial overrides a set; retire the denial first if it is wrong")
        if await conn.fetchval(f"SELECT id FROM {T_SET} WHERE org_id = $1::uuid AND global_security_id = $2::uuid "
                               "AND status = 'pending_approval' AND valid_to IS NULL AND system_to IS NULL",
                               str(org_id), sid):
            raise TaxConflict("a set for this security is already awaiting approval — decide it first")
        cur = await conn.fetchval(f"SELECT id FROM {T_SET} WHERE org_id = $1::uuid AND global_security_id = $2::uuid "
                                  "AND status = 'approved' AND valid_to IS NULL AND system_to IS NULL", str(org_id), sid)
        vno = int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) FROM {T_SET} WHERE org_id = $1::uuid "
                                      "AND global_security_id = $2::uuid", str(org_id), sid)) + 1
        name = await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = $1::uuid", sid)
    new_id = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_SET, subject_type=SUBJECT_SET, subject_id=new_id, maker_id=actor["id"],
        eligible_permission=APPROVE, title=f"Approve substitution set: {name} (v{vno})",
        payload={"security_id": sid, "security": name, "version_no": vno, "candidates": clean},
        action_path="/portfolio/tax?view=substitutions")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_SET} (id, org_id, global_security_id, version_no, status, notes, maker_id,
                            approval_task_id, replaces_id)
                        VALUES ($1, $2::uuid, $3::uuid, $4, 'pending_approval', $5, $6::uuid, $7::uuid, $8::uuid)
                        RETURNING *""",
                    new_id, str(org_id), sid, vno, (notes or "").strip() or None, str(actor["id"]), task["id"],
                    str(cur) if cur else None)
                for c in clean:
                    await conn.execute(
                        f"INSERT INTO {T_CAND} (org_id, set_id, rank, replacement_security_id, reason, evidence, source) "
                        "VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7)", str(org_id), new_id, c["rank"],
                        c["replacement_security_id"], c["reason"], c["evidence"], c["source"])
                await _audit(conn, org_id, actor["id"], "substitution_set_proposed", SUBJECT_SET, new_id,
                             {"security_id": sid, "version_no": vno, "candidates": len(clean), "task_id": task["id"]})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the substitution set could not be recorded")
        raise
    return {**dict(row), "task": task}


async def _set_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "pending_approval":
                return dict(row) if row else None
            row = dict(row)
            checker = uuid.UUID(str(actor_id))
            if checker == row["maker_id"]:
                raise MakerCheckerViolation(MAKER_CHECKER_RULE)
            now = datetime.now(timezone.utc)
            if outcome == "approve":
                prior = await conn.fetchrow(
                    f"SELECT id FROM {T_SET} WHERE org_id = $1::uuid AND global_security_id = $2 AND status = 'approved' "
                    "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", str(org_id), row["global_security_id"])
                if prior is not None:
                    await _update(conn, T_SET, prior["id"], {"status": "superseded", "valid_to": now})
                new = await _update(conn, T_SET, row["id"], {"status": "approved", "checker_id": checker,
                                                             "decided_at": now, "decision_note": note})
            else:
                new = await _update(conn, T_SET, row["id"], {"status": "rejected", "checker_id": checker,
                                                             "decided_at": now, "decision_note": note})
            await _audit(conn, org_id, checker, f"substitution_set_{'approved' if outcome == 'approve' else 'rejected'}",
                         SUBJECT_SET, row["id"], {"task_id": task["id"], "note": note})
    return new


async def _on_set(pool, task, outcome, actor_id, note):
    return await _set_outcome(pool, task["org_id"], task, outcome, actor_id, note)


async def _deny_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_DENY} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "pending_approval":
                return dict(row) if row else None
            row = dict(row)
            checker = uuid.UUID(str(actor_id))
            if checker == row["maker_id"]:
                raise MakerCheckerViolation(MAKER_CHECKER_RULE)
            now = datetime.now(timezone.utc)
            new = await _update(conn, T_DENY, row["id"], {
                "status": "active" if outcome == "approve" else "rejected", "checker_id": checker, "decided_at": now,
                "decision_note": note})
            await _audit(conn, org_id, checker, f"denial_{'approved' if outcome == 'approve' else 'rejected'}",
                         SUBJECT_DENY, row["id"], {"task_id": task["id"], "note": note})
    return new


async def _on_deny(pool, task, outcome, actor_id, note):
    return await _deny_outcome(pool, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_SET, label="Substitution set approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_set)
HT.register_kind(TASK_DENY, label="Substitution denial approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_deny)


async def decide(pool, *, org_id, actor_id, kind: str, row_id, outcome: str, note: str | None = None) -> dict:
    table, maker_col = {"set": (T_SET, "maker_id"), "denial": (T_DENY, "maker_id")}.get(kind, (None, None))
    if table is None:
        raise TaxError("kind is set or denial")
    if outcome not in ("approve", "reject"):
        raise TaxError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise TaxError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                                  _uuid(row_id, kind), str(org_id))
    if row is None:
        raise TaxNotFound(f"{kind} not found")
    if row["status"] != "pending_approval":
        raise TaxConflict(f"this {kind} is {row['status'].replace('_', ' ')}, not awaiting approval")
    if row[maker_col] is not None and actor["id"] == row[maker_col]:
        raise MakerCheckerViolation(MAKER_CHECKER_RULE)
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


async def retire_set(pool, *, org_id, actor_id, set_id, reason: str) -> dict:
    """approved → retired (the engine stops using it). approve_tax, never its maker."""
    if not (reason or "").strip():
        raise TaxError("retiring a set needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", _uuid(set_id, "set"), str(org_id))
            if row is None:
                raise TaxNotFound("set not found")
            if row["status"] != "approved" or row["valid_to"] is not None:
                raise TaxConflict("only the approved, current version of a set is retired")
            if actor["id"] == row["maker_id"]:
                raise MakerCheckerViolation("Maker-checker: a set is retired by someone other than its maker")
            now = datetime.now(timezone.utc)
            new = await _update(conn, T_SET, row["id"], {"status": "retired", "valid_to": now,
                                                         "decision_note": reason.strip()[:2000]})
            await _audit(conn, org_id, actor["id"], "substitution_set_retired", SUBJECT_SET, row["id"],
                         {"reason": reason.strip()})
    return new


# ── Denials ─────────────────────────────────────────────────────────────────
async def propose_denial(pool, *, org_id, actor_id, security_a_id, security_b_id, reason: str, evidence: str) -> dict:
    a, b = _pair(_uuid(security_a_id, "security"), _uuid(security_b_id, "security"))
    if a == b:
        raise TaxError("a security and itself are always substantially identical — that needs no denial")
    if not (reason or "").strip() or not (evidence or "").strip():
        raise TaxError("a denial needs a reason and evidence")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        await _security_ok(conn, a)
        await _security_ok(conn, b)
        if await conn.fetchval(f"SELECT id FROM {T_DENY} WHERE org_id = $1::uuid AND security_a_id = $2::uuid AND "
                               "security_b_id = $3::uuid AND status IN ('pending_approval', 'active') "
                               "AND valid_to IS NULL AND system_to IS NULL", str(org_id), a, b):
            raise TaxConflict("this pair is already denied (or awaiting approval)")
    new_id = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_DENY, subject_type=SUBJECT_DENY, subject_id=new_id, maker_id=actor["id"],
        eligible_permission=APPROVE, title="Approve a substitution denial (substantially identical pair)",
        payload={"security_a_id": a, "security_b_id": b, "reason": reason.strip(), "evidence": evidence.strip()},
        action_path="/portfolio/tax?view=substitutions")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_DENY} (id, org_id, security_a_id, security_b_id, rule, reason, evidence, status,
                            maker_kind, maker_id, approval_task_id)
                        VALUES ($1, $2::uuid, $3::uuid, $4::uuid, 'adviser', $5, $6, 'pending_approval', 'user', $7::uuid,
                                $8::uuid) RETURNING *""",
                    new_id, str(org_id), a, b, reason.strip()[:1000], evidence.strip()[:2000], str(actor["id"]),
                    task["id"])
                await _audit(conn, org_id, actor["id"], "denial_proposed", SUBJECT_DENY, new_id,
                             {"pair": [a, b], "task_id": task["id"]})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the denial could not be recorded")
        raise
    return {**dict(row), "task": task}


async def retire_denial(pool, *, org_id, actor_id, denial_id, reason: str) -> dict:
    """active → retired (the pair may pair again). approve_tax, never its maker."""
    if not (reason or "").strip():
        raise TaxError("retiring a denial needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_DENY} WHERE id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", _uuid(denial_id, "denial"), str(org_id))
            if row is None:
                raise TaxNotFound("denial not found")
            if row["status"] != "active":
                raise TaxConflict("only an active denial is retired")
            if row["maker_id"] is not None and actor["id"] == row["maker_id"]:
                raise MakerCheckerViolation("Maker-checker: a denial is retired by someone other than its maker")
            now = datetime.now(timezone.utc)
            new = await _update(conn, T_DENY, row["id"], {"status": "retired", "checker_id": actor["id"],
                                                          "decided_at": now, "retire_reason": reason.strip()[:2000]})
            await _audit(conn, org_id, actor["id"], "denial_retired", SUBJECT_DENY, row["id"], {"reason": reason.strip()})
    return new


async def rule_pairs(conn, org_id) -> list[dict]:
    """The denials the rules imply (nothing inferred beyond the stated rules)."""
    out: dict[tuple, dict] = {}
    # same index: approved rep1 terms with the same index_name, both in this org's registry
    rows = await conn.fetch(
        """SELECT rs.global_security_id, lower(btrim(v.term_values->>'index_name')) AS ix, v.term_values->>'index_name' AS raw
           FROM portfolio.replica_terms_versions v
           JOIN portfolio.replica_securities rs ON rs.id = v.replica_security_id AND rs.valid_to IS NULL
                AND rs.system_to IS NULL AND rs.org_id = $1::uuid
           WHERE v.org_id = $1::uuid AND v.status = 'approved' AND v.valid_to IS NULL AND v.system_to IS NULL
             AND COALESCE(btrim(v.term_values->>'index_name'), '') <> ''""", str(org_id))
    by_ix: dict[str, list] = {}
    for r in rows:
        by_ix.setdefault(r["ix"], []).append(r)
    for ix, group in by_ix.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = _pair(group[i]["global_security_id"], group[j]["global_security_id"])
                if a != b:
                    out.setdefault((a, b), {"a": a, "b": b, "rule": "same_index",
                                            "reason": f"both funds track the same index ({group[i]['raw']})",
                                            "evidence": "approved rep1 terms: index_name"})
    # share classes: the same SEC series in this org's registry
    rows = await conn.fetch(
        """SELECT global_security_id, sec_series_id FROM portfolio.replica_securities
           WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL AND COALESCE(sec_series_id, '') <> ''""",
        str(org_id))
    by_series: dict[str, list] = {}
    for r in rows:
        by_series.setdefault(r["sec_series_id"], []).append(str(r["global_security_id"]))
    for series, ids in by_series.items():
        ids = sorted(set(ids))
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                out.setdefault((ids[i], ids[j]), {"a": ids[i], "b": ids[j], "rule": "share_class",
                                                  "reason": f"share classes of the same fund series ({series})",
                                                  "evidence": "registry: sec_series_id"})
    # share classes: the security master's canonical link (two ids, one canonical security), for held securities
    rows = await conn.fetch(
        """SELECT g.id, g.canonical_id FROM portfolio.securities_global g
           WHERE g.system_to IS NULL AND g.valid_to IS NULL AND g.canonical_id IS NOT NULL AND g.canonical_id IN (
             SELECT canonical_id FROM portfolio.securities_global WHERE system_to IS NULL AND valid_to IS NULL
             GROUP BY canonical_id HAVING count(*) > 1)""")
    by_canon: dict[str, list] = {}
    for r in rows:
        by_canon.setdefault(str(r["canonical_id"]), []).append(str(r["id"]))
    for canon, ids in by_canon.items():
        ids = sorted(set(ids))
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                out.setdefault((ids[i], ids[j]), {"a": ids[i], "b": ids[j], "rule": "share_class",
                                                  "reason": "the security master links them as one security",
                                                  "evidence": f"securities_global.canonical_id {canon}"})
    return list(out.values())


async def seed_rule_denials(pool, *, org_id, actor_id=None) -> list[dict]:
    """Insert every rule pair that is not yet denied (idempotent). Active at once."""
    made = []
    async with pool.acquire() as conn:
        pairs = await rule_pairs(conn, org_id)
        async with conn.transaction():
            for p in pairs:
                exists = await conn.fetchval(
                    f"SELECT id FROM {T_DENY} WHERE org_id = $1::uuid AND security_a_id = $2::uuid AND security_b_id = "
                    "$3::uuid AND status IN ('pending_approval', 'active') AND valid_to IS NULL AND system_to IS NULL",
                    str(org_id), p["a"], p["b"])
                if exists:
                    continue
                rid = await conn.fetchval(
                    f"""INSERT INTO {T_DENY} (org_id, security_a_id, security_b_id, rule, reason, evidence, status,
                            maker_kind) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'active', 'system') RETURNING id""",
                    str(org_id), p["a"], p["b"], p["rule"], p["reason"], p["evidence"])
                await _audit(conn, org_id, actor_id, "denial_seeded", SUBJECT_DENY, rid, p)
                made.append({**p, "id": str(rid)})
    return made


# ── Reading ─────────────────────────────────────────────────────────────────
async def suggestions(conn, org_id, security_id) -> list[dict]:
    """The model's alternates for a security — a starting point, never a pair."""
    from services.wash_sale import security_labels

    sid = _uuid(security_id, "security")
    rows = await conn.fetch(
        """SELECT DISTINCT upper(a.value) AS t FROM portfolio.model_version_positions p
           CROSS JOIN LATERAL jsonb_array_elements_text(p.alternates) a
           WHERE p.org_id = $1::uuid AND p.global_security_id = $2::uuid AND p.valid_to IS NULL AND p.system_to IS NULL
             AND jsonb_typeof(p.alternates) = 'array'""", str(org_id), sid)
    tickers = [r["t"] for r in rows]
    if not tickers:
        return []
    ids = await conn.fetch(
        "SELECT upper(id_value) AS t, global_security_id FROM portfolio.securities_global_identifiers WHERE id_type = "
        "'ticker' AND upper(id_value) = ANY($1::text[]) AND valid_to IS NULL AND system_to IS NULL", tickers)
    labels = await security_labels(conn, {r["global_security_id"] for r in ids})
    return [{"replacement_security_id": str(r["global_security_id"]), "label": labels.get(str(r["global_security_id"])),
             "ticker": r["t"], "source": "model_alternate",
             "note": "the model's alternate — a starting point only: it needs a reason, evidence and approval"}
            for r in ids if str(r["global_security_id"]) != sid]


async def list_sets(conn, org_id) -> list[dict]:
    from services.wash_sale import security_labels

    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_SET} WHERE org_id = $1::uuid AND system_to IS NULL "
        "ORDER BY global_security_id, version_no DESC", str(org_id))]
    cands = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_CAND} WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL ORDER BY set_id, rank",
        str(org_id))]
    labels = await security_labels(conn, {r["global_security_id"] for r in rows}
                                   | {c["replacement_security_id"] for c in cands})
    by_set: dict = {}
    for c in cands:
        by_set.setdefault(c["set_id"], []).append({
            "rank": c["rank"], "replacement_security_id": str(c["replacement_security_id"]),
            "label": labels.get(str(c["replacement_security_id"])), "reason": c["reason"], "evidence": c["evidence"],
            "source": c["source"]})
    return [{"id": str(r["id"]), "global_security_id": str(r["global_security_id"]),
             "security": labels.get(str(r["global_security_id"])), "version_no": r["version_no"],
             "status": r["status"], "current": r["valid_to"] is None, "notes": r["notes"],
             "maker_id": str(r["maker_id"]), "checker_id": str(r["checker_id"]) if r["checker_id"] else None,
             "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
             "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"],
             "created_at": _iso(r["created_at"]), "candidates": by_set.get(r["id"], [])} for r in rows]


async def list_denials(conn, org_id, *, include_closed: bool = True) -> list[dict]:
    from services.wash_sale import security_labels

    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_DENY} WHERE org_id = $1::uuid AND system_to IS NULL"
        + ("" if include_closed else " AND status IN ('pending_approval', 'active')") + " ORDER BY created_at DESC",
        str(org_id))]
    labels = await security_labels(conn, {r["security_a_id"] for r in rows} | {r["security_b_id"] for r in rows})
    return [{"id": str(r["id"]), "security_a_id": str(r["security_a_id"]), "security_b_id": str(r["security_b_id"]),
             "security_a": labels.get(str(r["security_a_id"])), "security_b": labels.get(str(r["security_b_id"])),
             "rule": r["rule"], "reason": r["reason"], "evidence": r["evidence"], "status": r["status"],
             "maker_kind": r["maker_kind"], "maker_id": str(r["maker_id"]) if r["maker_id"] else None,
             "checker_id": str(r["checker_id"]) if r["checker_id"] else None,
             "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
             "decision_note": r["decision_note"], "retire_reason": r["retire_reason"],
             "created_at": _iso(r["created_at"])} for r in rows]


def set_actions(row: dict, *, can_approve: bool, user_id) -> list[str]:
    acts = []
    if can_approve and row["status"] == "pending_approval" and row["maker_id"] != str(user_id):
        acts += ["approve", "reject"]
    if can_approve and row["status"] == "approved" and row["current"] and row["maker_id"] != str(user_id):
        acts.append("retire")
    return acts


def denial_actions(row: dict, *, can_approve: bool, user_id) -> list[str]:
    acts = []
    if can_approve and row["status"] == "pending_approval" and row["maker_id"] != str(user_id):
        acts += ["approve", "reject"]
    if can_approve and row["status"] == "active" and row["maker_id"] != str(user_id):
        acts.append("retire")
    return acts


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"set_statuses": pairs(SET_STATUS_LABELS), "denial_statuses": pairs(DENIAL_STATUS_LABELS),
            "denial_rules": pairs(DENIAL_RULE_LABELS), "candidate_sources": pairs(CANDIDATE_SOURCE_LABELS)}
