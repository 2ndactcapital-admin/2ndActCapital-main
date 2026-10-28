"""DEALROOM3 — the Deal Room's control and insight layer (Phase 3).

One rule each:

  document_policy   what a caller may do with ONE data-room document: managers
                    get the original; investors and advisors get, by the
                    room's settings, a signed URL (watermark off, downloads
                    allowed — today's behaviour), a watermarked rendition
                    streamed by the API (watermark on), page images only
                    (view-only), or a refusal with a plain reason
                    (a type that cannot be watermarked, unless the manager
                    marked it "share unwatermarked").
  record_watermark  one deal_room_watermarks row per rendition: its id is
                    printed on every page, so a leaked copy traces to a person.
  log_activity      the append-only activity log (deal_room_activity): every
                    room open, folder open, page view, download, question and
                    answer read by an investor or advisor. Never deleted —
                    not when access ends, not when the deal closes.
  org_access_sweep  per org: granted → expired after the access end date (the
                    end date itself is still open), the "expiring" notice N days
                    before, granted → paused on a lapsed eligibility, paused →
                    granted once the record passes again. ``access_tick``
                    runs it for every non-test org from the scheduler cron.
  recheck_entity    the same pause / restore for one entity, called in the
                    transaction that changes its compliance record — at once.
  Q&A               ask (an investor with access, about a folder they can open
                    or generally), answer / withdraw / merge (managers). An
                    answer is visible to exactly the investors whose access
                    reaches the question's folder (general → after_agreements);
                    the asker is never shown to anyone but managers.
  advisors          an investor invites an advisor (an existing user of the
                    deal's org is linked; otherwise invited via the usermgmt1
                    path); the advisor acknowledges (typed name + consent)
                    before seeing anything; the folder function then evaluates
                    them AS the investor. Revoking the investor ends them.
  return notices    on revoke, optionally: the joinder's return-or-destroy
                    notice, recorded, sent in-app, acknowledged by the investor.

Notifications go through services.notifications.NotificationBus (in-app now,
email recorded pending for the existing worker) AFTER the deciding
transaction commits — a notification never blocks or outlives a rolled-back
decision. The custody cliff: nothing here is agent-reachable.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime, timezone
from uuid import UUID

from services import deal_room as R
from services import deal_room_watermark as W

ACTIVITY_NOTICE = ("Opens, page views and downloads in this data room are recorded, and the publisher can see "
                   "them.")
VIEW_ONLY_NOTICE = ("This room is view-only: documents open in the viewer, one page at a time, and cannot be "
                    "downloaded. View-only deters copying but cannot stop screenshots or photographs.")
WATERMARK_NOTICE = "Every copy and page you open is stamped with your name and a traceable watermark id."
OFFERING_DRAFT_NOTE = ("The Rule 506(b) and 506(c) settings apply DRAFT rules that are pending counsel review.")
ADVISOR_REFUSED = ("Advisors read the investor's open folders only: they cannot sign, request access, ask "
                   "questions or invite others.")
ADVISOR_ACK_TEXT = (
    "Advisor acknowledgement — {deal}\n\n"
    "I, {advisor}, have been asked by {investor} ({entity}) to review confidential information about {deal}, "
    "as their {role}.\n\n"
    "1. I am bound by the same confidentiality obligations as {investor} under the agreements they signed for "
    "this deal, and I will use the information only to advise them on it.\n"
    "2. {investor} remains responsible for my compliance with those obligations.\n"
    "3. I will not share the information with anyone else, and I will return or destroy it if asked.\n"
    "4. My access ends when {investor}'s access ends, and my opens, page views and downloads are recorded.\n"
)
RETURN_NOTICE_TEXT = (
    "Return-or-destroy notice — {deal}\n\n"
    "{investor} ({entity}): your access to the data room for {deal} was withdrawn on {on}.\n\n"
    "Under the confidentiality agreement you signed for this deal (the platform joinder's return-or-destroy "
    "clause), please return or destroy every copy of the confidential information you received — including "
    "downloaded or printed documents and any copies held by your advisors — and confirm that you have done so "
    "by acknowledging this notice.\n"
)


def _uuid(v) -> UUID | None:
    if v is None:
        return None
    return v if isinstance(v, UUID) else UUID(str(v))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_stamp(at: datetime | None = None) -> str:
    at = at or datetime.now(timezone.utc)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Who the reader is ──────────────────────────────────────────────────────
async def reader_context(conn, deal, principal, rights) -> dict:
    """The investor a non-manager reads as: themselves, or (an advisor) the
    investor they advise. Carries the names a watermark prints."""
    acting_for = rights.advisor_of if rights.role == "advisor" else None
    subject = acting_for or principal.user_id
    access = await R.live_access(conn, deal["id"], subject)
    entity_id = access["entity_id"] if access else None
    user = await conn.fetchrow("SELECT full_name, email FROM users WHERE id = $1", _uuid(principal.user_id))
    entity = await conn.fetchval("SELECT COALESCE(legal_name, display_name) FROM entities WHERE id = $1 "
                                 "AND valid_to IS NULL AND system_to IS NULL", entity_id) if entity_id else None
    investor_name = None
    if acting_for:
        investor_name = await conn.fetchval("SELECT COALESCE(full_name, email) FROM users WHERE id = $1",
                                            _uuid(acting_for))
    name = (user["full_name"] or user["email"]) if user else str(principal.user_id)
    return {"user_id": _uuid(principal.user_id), "on_behalf_of": _uuid(acting_for), "entity_id": entity_id,
            "name": name if not acting_for else f"{name}, advisor to {investor_name}", "entity_name": entity,
            "access": access}


# ── Documents: the ONE policy ──────────────────────────────────────────────
def document_policy(settings: dict, doc: dict, *, manage: bool, advisor_pending: bool = False) -> dict:
    """{download: 'original_url' | 'watermarked' | None, pages: bool,
    watermark: bool, reason: str | None}. Pure: the caller has already
    established that the document's folder is open to the caller."""
    kind = W.kind_of(doc.get("file_name"), doc.get("file_type"))
    if manage:
        return {"download": "original_url", "pages": kind is not None, "watermark": False, "reason": None,
                "kind": kind}
    if advisor_pending:
        return {"download": None, "pages": False, "watermark": False, "kind": kind,
                "reason": "Sign the advisor acknowledgement to open the investor's folders."}
    wm = settings.get("watermark") == "on"
    view_only = settings.get("downloads") == "view_only"
    shared = bool(doc.get("share_unwatermarked"))
    if kind is None:
        if view_only:
            return {"download": None, "pages": False, "watermark": False, "kind": None,
                    "reason": W.NOT_VIEWABLE}
        if wm and not shared:
            return {"download": None, "pages": False, "watermark": False, "kind": None,
                    "reason": W.NOT_WATERMARKABLE}
        return {"download": "original_url", "pages": False, "watermark": False, "kind": None, "reason": None}
    if view_only:
        return {"download": None, "pages": True, "watermark": wm, "kind": kind, "reason": None}
    if wm:
        return {"download": "watermarked", "pages": True, "watermark": True, "kind": kind, "reason": None}
    return {"download": "original_url", "pages": True, "watermark": False, "kind": kind, "reason": None}


async def record_watermark(conn, deal, doc, reader: dict, *, kind: str, page: int | None, source_sha256: str,
                           at: datetime) -> tuple[UUID, list[str]]:
    """Insert the register row FIRST (its id is what the page prints), return
    (watermark id, stamp lines)."""
    wid = await conn.fetchval("SELECT gen_random_uuid()")
    deal_name = deal["name"]
    lines = W.stamp_lines(investor=reader["name"], entity=reader["entity_name"], deal=deal_name,
                          at_utc=utc_stamp(at), watermark_id=str(wid))
    await conn.execute(
        """
        INSERT INTO deal_room_watermarks (id, org_id, deal_id, document_id, user_id, entity_id, on_behalf_of_user_id,
                                          kind, page, source_sha256, stamp_text, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """, wid, _uuid(deal["org_id"]), _uuid(deal["id"]), _uuid(doc["id"]), reader["user_id"],
        reader["entity_id"], reader["on_behalf_of"], kind, page, source_sha256, "\n".join(lines), at)
    return wid, lines


async def lookup_watermark(conn, watermark_id) -> dict | None:
    """A watermark id (from a leaked page) → who it was made for."""
    r = await conn.fetchrow(
        """
        SELECT w.id, w.deal_id, w.document_id, w.user_id, w.entity_id, w.on_behalf_of_user_id, w.kind, w.page,
               w.created_at, w.source_sha256, w.stamp_text, COALESCE(u.full_name, u.email) AS user_name, u.email,
               e.display_name AS entity_name, d.file_name, dl.name AS deal_name,
               COALESCE(ob.full_name, ob.email) AS on_behalf_of_name
        FROM deal_room_watermarks w
        JOIN users u ON u.id = w.user_id
        LEFT JOIN users ob ON ob.id = w.on_behalf_of_user_id
        LEFT JOIN entities e ON e.id = w.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
        LEFT JOIN deal_documents d ON d.id = w.document_id
        LEFT JOIN deals dl ON dl.id = w.deal_id AND dl.valid_to IS NULL AND dl.system_to IS NULL
        WHERE w.id = $1
        """, _uuid(watermark_id))
    return dict(r) if r else None


# ── Activity (append-only) ─────────────────────────────────────────────────
async def log_activity(conn, deal, reader: dict, action: str, *, folder_id=None, document_id=None, page=None,
                       watermark_id=None, detail: dict | None = None) -> UUID:
    if action not in R.ACTIVITY_ACTIONS:
        raise ValueError(action)
    return await conn.fetchval(
        """
        INSERT INTO deal_room_activity (org_id, deal_id, user_id, entity_id, on_behalf_of_user_id, folder_id,
                                        document_id, action, page, watermark_id, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb) RETURNING id
        """, _uuid(deal["org_id"]), _uuid(deal["id"]), reader["user_id"], reader["entity_id"],
        reader["on_behalf_of"], _uuid(folder_id), _uuid(document_id), action, page, _uuid(watermark_id),
        json.dumps(detail, default=str) if detail else None)


async def first_folder_open(conn, deal_id, user_id, folder_id) -> bool:
    """True when this is the reader's first open of the folder (the just-logged row is the only one)."""
    n = await conn.fetchval("SELECT count(*) FROM deal_room_activity WHERE deal_id = $1 AND user_id = $2 "
                            "AND folder_id = $3 AND action = 'folder_open'", _uuid(deal_id), _uuid(user_id),
                            _uuid(folder_id))
    return int(n) == 1


ACTIVITY_INVESTOR_SQL = """
    SELECT a.user_id, COALESCE(u.full_name, u.email) AS user_name,
           (array_agg(a.on_behalf_of_user_id) FILTER (WHERE a.on_behalf_of_user_id IS NOT NULL))[1]
             AS on_behalf_of_user_id,
           max(COALESCE(ob.full_name, ob.email)) AS on_behalf_of_name, max(e.display_name) AS entity_name,
           min(a.at) FILTER (WHERE a.action IN ('room_open', 'folder_open', 'view_page', 'download')) AS first_open,
           max(a.at) FILTER (WHERE a.action IN ('room_open', 'folder_open', 'view_page', 'download')) AS last_open,
           count(DISTINCT a.document_id) FILTER (WHERE a.action = 'view_page') AS documents_viewed,
           count(DISTINCT a.document_id) FILTER (WHERE a.action = 'download') AS documents_downloaded,
           count(*) FILTER (WHERE a.action = 'view_page') AS pages_viewed,
           count(*) FILTER (WHERE a.action = 'download') AS downloads,
           count(*) FILTER (WHERE a.action = 'question') AS questions
    FROM deal_room_activity a
    JOIN users u ON u.id = a.user_id
    LEFT JOIN users ob ON ob.id = a.on_behalf_of_user_id
    LEFT JOIN entities e ON e.id = a.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
    WHERE a.deal_id = $1
    GROUP BY a.user_id, u.full_name, u.email
    ORDER BY max(a.at) DESC NULLS LAST, a.user_id"""

ACTIVITY_DOCUMENT_SQL = """
    SELECT a.document_id, d.file_name, f.key AS folder_key, f.label AS folder_label,
           count(DISTINCT a.user_id) AS readers,
           count(*) FILTER (WHERE a.action = 'view_page') AS pages_viewed,
           count(*) FILTER (WHERE a.action = 'download') AS downloads,
           min(a.at) AS first_at, max(a.at) AS last_at
    FROM deal_room_activity a
    JOIN deal_documents d ON d.id = a.document_id
    LEFT JOIN deal_folders f ON f.id = d.folder_id
    WHERE a.deal_id = $1 AND a.document_id IS NOT NULL
    GROUP BY a.document_id, d.file_name, f.key, f.label
    ORDER BY max(a.at) DESC, a.document_id"""

ACTIVITY_EVENTS_SQL = """
    SELECT a.at, a.action, a.user_id, COALESCE(u.full_name, u.email) AS user_name,
           COALESCE(ob.full_name, ob.email) AS on_behalf_of_name, e.display_name AS entity_name,
           f.key AS folder_key, d.file_name, a.page, a.watermark_id
    FROM deal_room_activity a
    JOIN users u ON u.id = a.user_id
    LEFT JOIN users ob ON ob.id = a.on_behalf_of_user_id
    LEFT JOIN entities e ON e.id = a.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
    LEFT JOIN deal_folders f ON f.id = a.folder_id
    LEFT JOIN deal_documents d ON d.id = a.document_id
    WHERE a.deal_id = $1
    ORDER BY a.at, a.id"""

ACTIVITY_VIEWS = {"investor": ACTIVITY_INVESTOR_SQL, "document": ACTIVITY_DOCUMENT_SQL, "events": ACTIVITY_EVENTS_SQL}


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


async def activity_rows(conn, deal_id, view: str) -> list[dict]:
    rows = await conn.fetch(ACTIVITY_VIEWS[view], _uuid(deal_id))
    return [{k: (int(v) if isinstance(v, int) else v) for k, v in dict(r).items()} for r in rows]


def activity_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    if not rows:
        return ""
    w = csv.writer(buf, lineterminator="\n")
    cols = list(rows[0].keys())
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(r[c]) for c in cols])
    return buf.getvalue()


# ── Notifications (after commit) ───────────────────────────────────────────
# In-app now; email recorded 'pending' in notification_delivery_log for the existing worker.
CHANNELS = ["in_app", "email"]


class Outbox:
    """Notifications collected inside a transaction and published after it."""

    def __init__(self):
        self.items: list[tuple] = []

    def add(self, deal, user_ids, event, title, body, payload=None):
        ids = [u for u in dict.fromkeys(_uuid(x) for x in user_ids if x)]
        if ids:
            self.items.append((deal, ids, event, title, body, payload or {}))

    async def publish(self, pool) -> int:
        from services.notifications import notification_bus

        n = 0
        for deal, ids, event, title, body, payload in self.items:
            try:
                await notification_bus.publish(pool, deal["org_id"], event, title, body, [str(u) for u in ids],
                                               resource_type="deal", resource_id=deal["id"],
                                               payload={"deal_id": str(deal["id"]), **payload}, channels=CHANNELS)
                n += 1
            except Exception as exc:  # noqa: BLE001 — best effort, never blocks a decision
                print(f"[deal_room] notify failed ({event}): {type(exc).__name__}: {exc}")
        self.items.clear()
        return n


async def publisher_notify(conn, outbox: Outbox, deal, event_key: str, event: str, title: str, body: str,
                           payload=None) -> None:
    """Publisher users + delegates, when the deal's settings ask for this event."""
    settings = await R.room_settings(conn, deal["id"])
    if event_key not in (settings.get("notify_publisher_on") or []):
        return
    outbox.add(deal, await R.publisher_and_delegate_user_ids(conn, deal), event, title, body, payload)


async def folder_readers(conn, deal_id, folder_id) -> list[UUID]:
    """Every investor (and active advisor) the folder is open to right now."""
    rows = await conn.fetch(
        """
        SELECT user_id FROM deal_access WHERE deal_id = $1 AND valid_to IS NULL AND status = 'granted'
          AND deal_room_folder_open($2, user_id)
        UNION
        SELECT advisor_user_id FROM deal_room_advisors WHERE deal_id = $1 AND valid_to IS NULL AND status = 'active'
          AND deal_room_folder_open($2, advisor_user_id)
        """, _uuid(deal_id), _uuid(folder_id))
    return [r["user_id"] for r in rows]


# ── Expiry and pause ───────────────────────────────────────────────────────
async def _deal(conn, deal_id) -> dict | None:
    d = await R.fetch_deal(conn, deal_id)
    return dict(d) if d else None


async def pause_access(conn, deal, acc, *, actor, reason: str, outbox: Outbox) -> dict:
    _, row = await R.set_access(conn, deal["org_id"], deal["id"], acc["user_id"], status="paused", actor=actor,
                                prior_level=acc["level_granted"], decided_by=actor, reason="eligibility lapsed",
                                paused_reason=reason)
    await R.audit(conn, org_id=deal["org_id"], actor=actor, action="deal_access.pause", resource_id=row["id"],
                  payload={"deal_id": deal["id"], "user_id": acc["user_id"], "prior_level": acc["level_granted"],
                           "reason": reason, "previous_row": acc["id"]})
    outbox.add(deal, [acc["user_id"]], "deal_access_paused", "Deal room access paused",
               f"Your access to {deal['name']} is paused: {reason} It reopens once the record is updated.",
               {"access_id": str(row["id"])})
    outbox.add(deal, await R.staff_user_ids(conn, deal["org_id"]), "deal_access_paused_staff",
               "Investor access paused", f"Access to {deal['name']} was paused for an investor: {reason}",
               {"access_id": str(row["id"])})
    return dict(row)


async def restore_access(conn, deal, acc, *, actor, today: date, outbox: Outbox) -> dict:
    ends = acc["access_ends_on"]
    if ends is not None and ends < today:
        _, row = await R.set_access(conn, deal["org_id"], deal["id"], acc["user_id"], status="expired", actor=actor,
                                    decided_by=actor, reason="access period ended while paused")
        action = "deal_access.restore_expired"
    else:
        _, row = await R.set_access(conn, deal["org_id"], deal["id"], acc["user_id"], status="granted",
                                    level=acc["prior_level"] or "after_agreements", actor=actor, decided_by=actor,
                                    reason="eligibility restored")
        action = "deal_access.restore"
        outbox.add(deal, [acc["user_id"]], "deal_access_restored", "Deal room access restored",
                   f"Your access to {deal['name']} is open again.", {"access_id": str(row["id"])})
    await R.audit(conn, org_id=deal["org_id"], actor=actor, action=action, resource_id=row["id"],
                  payload={"deal_id": deal["id"], "user_id": acc["user_id"], "level": acc["prior_level"],
                           "previous_row": acc["id"]})
    return dict(row)


async def _compliance_check(conn, deal, acc, today: date) -> dict:
    settings = await R.room_settings(conn, deal["id"])
    return await R.evaluate_eligibility(conn, acc["entity_id"], settings["eligibility_rule"], today=today,
                                        offering_type=settings.get("offering_type"))


async def recheck_rows(conn, rows, *, actor, today: date, outbox: Outbox) -> list[dict]:
    """Pause each granted row whose entity no longer passes; restore each
    paused row whose entity passes again."""
    changed = []
    for acc in rows:
        if acc["entity_id"] is None or acc["status"] not in ("granted", "paused"):
            continue
        deal = await _deal(conn, acc["deal_id"])
        if deal is None:
            continue
        elig = await _compliance_check(conn, deal, acc, today)
        if acc["status"] == "granted" and not elig["eligible"]:
            row = await pause_access(conn, deal, acc, actor=actor, reason=" ".join(elig["missing"]), outbox=outbox)
            changed.append({"deal_id": acc["deal_id"], "user_id": acc["user_id"], "to": "paused", "row": row})
        elif acc["status"] == "paused" and elig["eligible"]:
            row = await restore_access(conn, deal, acc, actor=actor, today=today, outbox=outbox)
            changed.append({"deal_id": acc["deal_id"], "user_id": acc["user_id"], "to": row["status"], "row": row})
    return changed


async def recheck_entity(conn, org_id, entity_id, *, actor, today: date | None = None,
                         outbox: Outbox | None = None) -> list[dict]:
    """Called in the transaction that changes an entity's compliance record
    (routers/entities.update_compliance): pause / restore at once."""
    today = today or date.today()
    rows = await conn.fetch(f"SELECT {R.ACCESS_COLS} FROM deal_access WHERE org_id = $1 AND entity_id = $2 "
                            "AND valid_to IS NULL AND status IN ('granted', 'paused')", _uuid(org_id),
                            _uuid(entity_id))
    return await recheck_rows(conn, rows, actor=actor, today=today, outbox=outbox if outbox is not None else Outbox())


async def org_access_sweep(pool, *, org_id, today: date | None = None, include_test_orgs: bool = False) -> dict:
    """One org's sweep, in that org's RLS context (the caller set it). Order:
    pause/restore, then expire (granted rows whose end date is BEFORE today —
    the end date itself is still open), then the expiring notice."""
    from services import orgs as ORGS

    today = today or date.today()
    out = {"expired": [], "paused": [], "restored": [], "notified": [], "skipped": None}
    outbox = Outbox()
    async with pool.acquire() as conn:
        if not include_test_orgs and await ORGS.is_test_org(conn, org_id):
            out["skipped"] = "test org"
            return out
        async with conn.transaction():
            rows = await conn.fetch(f"SELECT {R.ACCESS_COLS} FROM deal_access WHERE org_id = $1 AND valid_to IS NULL "
                                    "AND status IN ('granted', 'paused')", _uuid(org_id))
            for ch in await recheck_rows(conn, rows, actor=None, today=today, outbox=outbox):
                out["paused" if ch["to"] == "paused" else "restored"].append(
                    {"deal_id": str(ch["deal_id"]), "user_id": str(ch["user_id"]), "status": ch["to"]})
            due = await conn.fetch(f"SELECT {R.ACCESS_COLS} FROM deal_access WHERE org_id = $1 AND valid_to IS NULL "
                                   "AND status = 'granted' AND access_ends_on IS NOT NULL AND access_ends_on < $2",
                                   _uuid(org_id), today)
            for acc in due:
                deal = await _deal(conn, acc["deal_id"])
                if deal is None:
                    continue
                _, row = await R.set_access(conn, deal["org_id"], deal["id"], acc["user_id"], status="expired",
                                            actor=None, decided_by=None, reason="access period ended")
                await R.audit(conn, org_id=deal["org_id"], actor=None, action="deal_access.expire",
                              resource_id=row["id"], payload={"deal_id": deal["id"], "user_id": acc["user_id"],
                                                              "access_ends_on": acc["access_ends_on"],
                                                              "level": acc["level_granted"], "previous_row": acc["id"]})
                outbox.add(deal, [acc["user_id"]], "deal_access_expired", "Deal room access ended",
                           f"Your access period for {deal['name']} ended on {acc['access_ends_on'].isoformat()}.",
                           {"access_id": str(row["id"])})
                outbox.add(deal, await R.publisher_and_delegate_user_ids(conn, deal), "deal_access_expired_publisher",
                           "Investor access expired", f"An investor's access period for {deal['name']} ended.",
                           {"access_id": str(row["id"])})
                out["expired"].append({"deal_id": str(deal["id"]), "user_id": str(acc["user_id"])})
            soon = await conn.fetch(
                f"""
                SELECT {', '.join('a.' + c.strip() for c in R.ACCESS_COLS.split(','))}, s.expiry_notice_days
                FROM deal_access a JOIN deal_room_settings s ON s.deal_id = a.deal_id AND s.valid_to IS NULL
                WHERE a.org_id = $1 AND a.valid_to IS NULL AND a.status = 'granted' AND a.access_ends_on IS NOT NULL
                  AND a.access_ends_on >= $2 AND a.access_ends_on - s.expiry_notice_days <= $2
                  AND s.expiry_notice_days > 0
                  AND NOT EXISTS (SELECT 1 FROM notifications n JOIN notification_recipients nr
                                    ON nr.notification_id = n.id AND nr.user_id = a.user_id
                                  WHERE n.org_id = a.org_id AND n.event_type = 'deal_access_expiring'
                                    AND n.resource_id = a.deal_id
                                    AND n.payload->>'access_ends_on' = a.access_ends_on::text)
                """, _uuid(org_id), today)
        for acc in soon:
            deal = await R.fetch_deal(conn, acc["deal_id"])
            if deal is None:
                continue
            outbox.add(dict(deal), [acc["user_id"]], "deal_access_expiring", "Deal room access ending soon",
                       f"Your access to {deal['name']} ends on {acc['access_ends_on'].isoformat()} "
                       f"({(acc['access_ends_on'] - today).days} day(s)).",
                       {"access_ends_on": acc["access_ends_on"].isoformat()})
            out["notified"].append({"deal_id": str(acc["deal_id"]), "user_id": str(acc["user_id"])})
    await outbox.publish(pool)
    return out


async def access_tick(pool, *, today: date | None = None) -> dict:
    """The scheduler entrypoint's step: every NON-test org with live access
    rows, each in its own RLS context (services.orgs — a verify's fixture org
    is never swept by the live cron)."""
    from services import orgs as ORGS
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            orgs = [r["org_id"] for r in await conn.fetch(
                "SELECT DISTINCT org_id FROM deal_access x WHERE valid_to IS NULL AND status IN ('granted', 'paused') "
                "AND " + ORGS.not_test_org_sql("x.org_id"))]
    finally:
        reset_rls_context(tokens)
    out = {}
    for org in orgs:
        tokens = set_rls_context(str(org), False)
        try:
            out[str(org)] = await org_access_sweep(pool, org_id=org, today=today)
        except Exception as exc:  # noqa: BLE001 — one org never stops the others
            out[str(org)] = {"errors": [f"{type(exc).__name__}: {exc}"]}
        finally:
            reset_rls_context(tokens)
    return out


# ── Q&A ────────────────────────────────────────────────────────────────────
QUESTION_COLS = ("id, org_id, deal_id, folder_id, asked_by, entity_id, body, status, merged_into, status_reason, "
                 "status_by, created_at, system_from")


async def live_question(conn, deal_id, question_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT {QUESTION_COLS} FROM deal_room_questions WHERE id = $1 AND deal_id = $2 "
                            "AND system_to IS NULL", _uuid(question_id), _uuid(deal_id))
    return dict(r) if r else None


async def archive_question(conn, question_id) -> None:
    """System-axis history copy (id kept on the live row — answers point at it)."""
    await conn.execute(
        """
        INSERT INTO deal_room_questions (org_id, deal_id, folder_id, asked_by, entity_id, body, status, merged_into,
                                         status_reason, status_by, created_at, system_from, system_to, history_of)
        SELECT org_id, deal_id, folder_id, asked_by, entity_id, body, status, merged_into, status_reason, status_by,
               created_at, system_from, clock_timestamp(), id
        FROM deal_room_questions WHERE id = $1 AND system_to IS NULL
        """, _uuid(question_id))


async def set_question_status(conn, question_id, *, status, actor, reason=None, merged_into=None) -> None:
    await archive_question(conn, question_id)
    await conn.execute("UPDATE deal_room_questions SET status = $2, status_reason = $3, status_by = $4, "
                       "merged_into = $5, system_from = clock_timestamp() WHERE id = $1 AND system_to IS NULL",
                       _uuid(question_id), status, reason, _uuid(actor), _uuid(merged_into))


async def has_granted(conn, deal_id, user_id) -> bool:
    """The caller (or, for an active advisor, their investor) holds granted
    access — the after_agreements reach of a general question."""
    return bool(await conn.fetchval(
        """
        SELECT EXISTS (SELECT 1 FROM deal_access a WHERE a.deal_id = $1 AND a.valid_to IS NULL
                       AND a.status = 'granted' AND (a.user_id = $2 OR a.user_id IN (
                         SELECT access_user_id FROM deal_room_advisors WHERE deal_id = $1 AND advisor_user_id = $2
                           AND status = 'active' AND valid_to IS NULL)))
        """, _uuid(deal_id), _uuid(user_id)))


async def question_visible(conn, q: dict, user_id) -> bool:
    if q["folder_id"] is None:
        return await has_granted(conn, q["deal_id"], user_id)
    return bool(await conn.fetchval("SELECT deal_room_folder_open($1, $2)", q["folder_id"], _uuid(user_id)))


async def ask(conn, deal, reader: dict, *, body: str, folder_id=None) -> dict:
    if reader["on_behalf_of"] is not None:
        raise R.DealRoomError(403, ADVISOR_REFUSED)
    if len((body or "").strip()) < 3:
        raise R.DealRoomError(422, "Write your question.")
    access = reader["access"]
    if access is None or access["status"] != "granted":
        raise R.DealRoomError(409, "Questions are open to investors whose access has been granted.")
    if folder_id is not None:
        ok = await conn.fetchval("SELECT deal_room_folder_open($1, $2) AND EXISTS (SELECT 1 FROM deal_folders "
                                 "WHERE id = $1 AND deal_id = $3 AND system_to IS NULL)", _uuid(folder_id),
                                 reader["user_id"], _uuid(deal["id"]))
        if not ok:
            raise R.DealRoomError(404, "Folder not found")
    qid = await conn.fetchval(
        "INSERT INTO deal_room_questions (org_id, deal_id, folder_id, asked_by, entity_id, body) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id", _uuid(deal["org_id"]), _uuid(deal["id"]), _uuid(folder_id),
        reader["user_id"], reader["entity_id"], body.strip())
    await log_activity(conn, deal, reader, "question", folder_id=folder_id, detail={"question_id": str(qid)})
    return await live_question(conn, deal["id"], qid)


async def live_answer(conn, question_id) -> dict | None:
    r = await conn.fetchrow("SELECT id, question_id, body, answered_by, created_at, system_from FROM deal_room_answers "
                            "WHERE question_id = $1 AND system_to IS NULL", _uuid(question_id))
    return dict(r) if r else None


async def answer(conn, deal, question_id, *, body: str, actor) -> tuple[dict, list[UUID]]:
    """Answer (or re-answer: the previous answer is archived on the system
    axis). Returns (answer, recipients) — every investor / active advisor
    the question's level reaches, the asker included."""
    if len((body or "").strip()) < 2:
        raise R.DealRoomError(422, "Write the answer.")
    q = await live_question(conn, deal["id"], question_id)
    if q is None:
        raise R.DealRoomError(404, "Question not found")
    if q["status"] in ("withdrawn", "merged"):
        raise R.DealRoomError(409, f"This question was {q['status']}; answer the question it was merged into.")
    prev = await live_answer(conn, q["id"])
    if prev is not None:
        await conn.execute(
            "INSERT INTO deal_room_answers (org_id, deal_id, question_id, body, answered_by, created_at, system_from, "
            "system_to, history_of) SELECT org_id, deal_id, question_id, body, answered_by, created_at, system_from, "
            "clock_timestamp(), id FROM deal_room_answers WHERE id = $1", prev["id"])
        await conn.execute("UPDATE deal_room_answers SET body = $2, answered_by = $3, system_from = clock_timestamp() "
                           "WHERE id = $1", prev["id"], body.strip(), _uuid(actor))
    else:
        await conn.execute("INSERT INTO deal_room_answers (org_id, deal_id, question_id, body, answered_by) "
                           "VALUES ($1, $2, $3, $4, $5)", _uuid(deal["org_id"]), _uuid(deal["id"]), q["id"],
                           body.strip(), _uuid(actor))
    if q["status"] != "answered":
        await set_question_status(conn, q["id"], status="answered", actor=actor)
    await R.audit(conn, org_id=deal["org_id"], actor=actor, action="deal_room.question_answer", resource_id=q["id"],
                  payload={"deal_id": deal["id"], "reanswer": prev is not None})
    return await live_answer(conn, q["id"]), await question_audience(conn, deal["id"], q)


async def question_audience(conn, deal_id, q: dict) -> list[UUID]:
    """Everyone an answered question reaches (and every asker merged into it)."""
    if q["folder_id"] is None:
        rows = await conn.fetch(
            "SELECT user_id FROM deal_access WHERE deal_id = $1 AND valid_to IS NULL AND status = 'granted' "
            "UNION SELECT ad.advisor_user_id FROM deal_room_advisors ad JOIN deal_access a ON a.deal_id = ad.deal_id "
            "AND a.user_id = ad.access_user_id AND a.valid_to IS NULL AND a.status = 'granted' "
            "WHERE ad.deal_id = $1 AND ad.status = 'active' AND ad.valid_to IS NULL", _uuid(deal_id))
        ids = [r["user_id"] for r in rows]
    else:
        ids = await folder_readers(conn, deal_id, q["folder_id"])
    askers = [r["asked_by"] for r in await conn.fetch(
        "SELECT asked_by FROM deal_room_questions WHERE system_to IS NULL AND (id = $1 OR merged_into = $1)", q["id"])]
    return list(dict.fromkeys([*ids, *askers]))


async def withdraw(conn, deal, question_id, *, reason: str, actor) -> dict:
    if not (reason or "").strip():
        raise R.DealRoomError(422, "Give a reason to withdraw the question.")
    q = await live_question(conn, deal["id"], question_id)
    if q is None:
        raise R.DealRoomError(404, "Question not found")
    if q["status"] in ("withdrawn", "merged"):
        raise R.DealRoomError(409, f"Already {q['status']}.")
    await set_question_status(conn, q["id"], status="withdrawn", actor=actor, reason=reason.strip())
    await R.audit(conn, org_id=deal["org_id"], actor=actor, action="deal_room.question_withdraw", resource_id=q["id"],
                  payload={"reason": reason.strip()})
    return await live_question(conn, deal["id"], q["id"])


async def merge(conn, deal, question_id, *, into_id, reason: str, actor) -> dict:
    if not (reason or "").strip():
        raise R.DealRoomError(422, "Give a reason to merge the question.")
    q = await live_question(conn, deal["id"], question_id)
    target = await live_question(conn, deal["id"], into_id)
    if q is None or target is None:
        raise R.DealRoomError(404, "Question not found")
    if q["id"] == target["id"] or target["status"] in ("withdrawn", "merged") or q["status"] in ("withdrawn", "merged"):
        raise R.DealRoomError(409, "Merge an open or answered question into another live question.")
    if (target["folder_id"] is None) != (q["folder_id"] is None) or (
            q["folder_id"] is not None and q["folder_id"] != target["folder_id"]):
        # the asker must still be able to read the target's answer
        if not await question_visible(conn, target, q["asked_by"]):
            raise R.DealRoomError(409, "The asker could not read the target question's answer — merge into a "
                                       "question at the same level.")
    await set_question_status(conn, q["id"], status="merged", actor=actor, reason=reason.strip(),
                              merged_into=target["id"])
    await R.audit(conn, org_id=deal["org_id"], actor=actor, action="deal_room.question_merge", resource_id=q["id"],
                  payload={"into": target["id"], "reason": reason.strip()})
    return await live_question(conn, deal["id"], q["id"])


async def questions_for(conn, deal, *, manage: bool, user_id, lab: dict) -> list[dict]:
    """Managers: every live question with its asker. Anyone else: their own
    questions (any status) plus every ANSWERED question their level reaches —
    never an asker's identity (``mine`` says only whether it is theirs)."""
    rows = await conn.fetch(
        f"""
        SELECT q.id, q.folder_id, q.asked_by, q.body, q.status, q.merged_into, q.status_reason, q.created_at,
               f.key AS folder_key, f.label AS folder_label, a.body AS answer, a.system_from AS answered_at,
               COALESCE(u.full_name, u.email) AS asker_name, e.display_name AS asker_entity
        FROM deal_room_questions q
        LEFT JOIN deal_folders f ON f.id = q.folder_id
        LEFT JOIN deal_room_answers a ON a.question_id = q.id AND a.system_to IS NULL
        LEFT JOIN users u ON u.id = q.asked_by
        LEFT JOIN entities e ON e.id = q.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
        WHERE q.deal_id = $1 AND q.system_to IS NULL
        ORDER BY q.created_at, q.id
        """, _uuid(deal["id"]))
    out = []
    uid = _uuid(user_id)
    for r in rows:
        mine = r["asked_by"] == uid
        folder_label = r["folder_label"] or (lab.get(f"deal_room_folder_{r['folder_key']}") if r["folder_key"] else None)
        base = {"id": str(r["id"]), "folder_id": str(r["folder_id"]) if r["folder_id"] else None,
                "folder_label": folder_label, "body": r["body"], "status": r["status"],
                "status_label": lab.get(f"deal_room_question_status_{r['status']}"),
                "merged_into": str(r["merged_into"]) if r["merged_into"] else None,
                "answer": r["answer"], "answered_at": r["answered_at"].isoformat() if r["answered_at"] else None,
                "created_at": r["created_at"].isoformat(), "mine": mine}
        if manage:
            out.append({**base, "asker_name": r["asker_name"], "asker_entity": r["asker_entity"],
                        "status_reason": r["status_reason"]})
            continue
        if mine:
            out.append({**base, "status_reason": r["status_reason"] if r["status"] in ("withdrawn", "merged")
                        else None})
            continue
        if r["status"] == "answered" and await question_visible(conn, dict(r) | {"deal_id": deal["id"]}, uid):
            out.append(base)
    return out


# ── Advisors ───────────────────────────────────────────────────────────────
ADVISOR_COLS = ("id, org_id, deal_id, access_user_id, investor_entity_id, advisor_user_id, name, firm, role, email, "
                "status, ack_typed_name, ack_consent, ack_text_sha256, acknowledged_at, invited_by, revoked_by, "
                "revoked_reason, valid_from")


async def restate_advisor(conn, cur: dict, *, actor, **changes) -> dict:
    await conn.execute("UPDATE deal_room_advisors SET valid_to = clock_timestamp() WHERE id = $1 AND valid_to IS NULL",
                       cur["id"])
    new = {**cur, **changes}
    cols = ["org_id", "deal_id", "access_user_id", "investor_entity_id", "advisor_user_id", "name", "firm", "role",
            "email", "status", "ack_typed_name", "ack_consent", "ack_text_sha256", "acknowledged_at", "invited_by",
            "revoked_by", "revoked_reason"]
    row = await conn.fetchrow(
        f"INSERT INTO deal_room_advisors ({', '.join(cols)}, created_by, valid_from) VALUES "
        f"({', '.join(f'${i + 1}' for i in range(len(cols)))}, ${len(cols) + 1}, clock_timestamp()) "
        f"RETURNING {ADVISOR_COLS}", *[new.get(c) for c in cols], _uuid(actor))
    return dict(row)


async def invite_advisor(conn, deal, reader: dict, *, name: str, firm: str | None, role: str, email: str,
                         send_email: bool = True) -> dict:
    """An investor with granted access names an advisor. An existing user of
    the deal's org (exact email) is linked; an unknown email is invited
    through the usermgmt1 path (services.invites.create_invite) into the deal's
    org; an email that belongs to ANOTHER org is refused."""
    from services.invites import create_invite

    access = reader["access"]
    if reader["on_behalf_of"] is not None:
        raise R.DealRoomError(403, ADVISOR_REFUSED)
    if access is None or access["status"] != "granted":
        raise R.DealRoomError(409, "You can add advisors once your access to this deal is granted.")
    if role not in R.ADVISOR_ROLES:
        raise R.DealRoomError(422, f"role must be one of {', '.join(R.ADVISOR_ROLES)}")
    email = (email or "").strip().lower()
    if "@" not in email or len((name or "").strip()) < 2:
        raise R.DealRoomError(422, "Give the advisor's name and email address.")
    user = await conn.fetchrow("SELECT id, org_id, status FROM users WHERE lower(email) = $1", email)
    if user is None:
        # users RLS shows only this org's rows: an address held in another org
        # surfaces as the users.email UNIQUE violation below → the same 409.
        try:
            inv = await create_invite(conn, org_id=str(deal["org_id"]), email=email, full_name=name.strip(),
                                      role="member", invited_by=str(reader["user_id"]), send_email=send_email)
        except Exception as exc:  # noqa: BLE001 — a duplicate email in another org (RLS hid it)
            if "unique" in type(exc).__name__.lower() or "duplicate" in str(exc).lower():
                raise R.DealRoomError(409, "That email belongs to an account outside this firm; use another "
                                           "address.") from exc
            raise
        advisor_user = _uuid(inv["id"])
        how = "invited"
    else:
        if user["org_id"] != _uuid(deal["org_id"]):
            raise R.DealRoomError(409, "That email belongs to an account outside this firm; use another address.")
        if user["status"] == "retired":
            raise R.DealRoomError(409, "That account is closed.")
        advisor_user = user["id"]
        how = "linked"
    if advisor_user == reader["user_id"]:
        raise R.DealRoomError(422, "You cannot name yourself as your advisor.")
    if await conn.fetchval("SELECT 1 FROM deal_access WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL",
                           _uuid(deal["id"]), advisor_user):
        raise R.DealRoomError(409, "That person has their own access to this deal.")
    cur = await conn.fetchrow(f"SELECT {ADVISOR_COLS} FROM deal_room_advisors WHERE deal_id = $1 AND advisor_user_id "
                              "= $2 AND valid_to IS NULL", _uuid(deal["id"]), advisor_user)
    if cur is not None and cur["status"] != "revoked":
        raise R.DealRoomError(409, "That advisor is already on this deal.")
    base = {"org_id": _uuid(deal["org_id"]), "deal_id": _uuid(deal["id"]), "access_user_id": reader["user_id"],
            "investor_entity_id": reader["entity_id"], "advisor_user_id": advisor_user, "name": name.strip(),
            "firm": (firm or "").strip() or None, "role": role, "email": email, "status": "invited",
            "ack_typed_name": None, "ack_consent": None, "ack_text_sha256": None, "acknowledged_at": None,
            "invited_by": reader["user_id"], "revoked_by": None, "revoked_reason": None}
    if cur is not None:
        row = await restate_advisor(conn, dict(cur), actor=reader["user_id"], **base)
    else:
        row = dict(await conn.fetchrow(
            f"""
            INSERT INTO deal_room_advisors (org_id, deal_id, access_user_id, investor_entity_id, advisor_user_id, name,
                                            firm, role, email, status, invited_by, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'invited', $10, $10) RETURNING {ADVISOR_COLS}
            """, base["org_id"], base["deal_id"], base["access_user_id"], base["investor_entity_id"], advisor_user,
            base["name"], base["firm"], role, email, reader["user_id"]))
    await R.audit(conn, org_id=deal["org_id"], actor=reader["user_id"], action="deal_room.advisor_invite",
                  resource_id=row["id"], payload={"deal_id": deal["id"], "advisor_user_id": advisor_user,
                                                  "role": role, "how": how})
    return {**row, "how": how}


async def advisor_ack_text(conn, deal, adv) -> str:
    investor = await conn.fetchval("SELECT COALESCE(full_name, email) FROM users WHERE id = $1", adv["access_user_id"])
    entity = await conn.fetchval("SELECT COALESCE(legal_name, display_name) FROM entities WHERE id = $1 AND valid_to "
                                 "IS NULL AND system_to IS NULL", adv["investor_entity_id"]) \
        if adv["investor_entity_id"] else None
    return ADVISOR_ACK_TEXT.format(deal=deal["name"], advisor=adv["name"], investor=investor,
                                   entity=entity or "their investing entity", role=adv["role"].replace("_", " "))


async def acknowledge(conn, deal, principal, *, typed_name: str, consent: bool, text_sha256: str) -> dict:
    cur = await conn.fetchrow(f"SELECT {ADVISOR_COLS} FROM deal_room_advisors WHERE deal_id = $1 AND advisor_user_id "
                              "= $2 AND valid_to IS NULL AND status IN ('invited', 'active')", _uuid(deal["id"]),
                              _uuid(principal.user_id))
    if cur is None:
        raise R.DealRoomError(404, "No advisor invitation for you on this deal.")
    if cur["status"] == "active":
        raise R.DealRoomError(409, "Already acknowledged.")
    if consent is not True:
        raise R.DealRoomError(422, "Tick the consent box to acknowledge.")
    text = await advisor_ack_text(conn, deal, cur)
    if text_sha256 != sha256_text(text):
        raise R.DealRoomError(409, {"message": "The acknowledgement changed since you opened it. Read it again.",
                                    "text_sha256": sha256_text(text)})
    me = await conn.fetchval("SELECT full_name FROM users WHERE id = $1", _uuid(principal.user_id)) or cur["name"]
    if R._norm_name(typed_name) not in (R._norm_name(me), R._norm_name(cur["name"])):
        raise R.DealRoomError(422, "Type your full name exactly as it appears on your account.")
    row = await restate_advisor(conn, dict(cur), actor=principal.user_id, status="active",
                                ack_typed_name=typed_name.strip(), ack_consent=True, ack_text_sha256=sha256_text(text),
                                acknowledged_at=datetime.now(timezone.utc))
    await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.advisor_acknowledge",
                  resource_id=row["id"], payload={"deal_id": deal["id"], "text_sha256": sha256_text(text)})
    return row


async def remove_advisor(conn, deal, advisor_user_id, *, actor, reason: str, by_investor) -> dict:
    """The investor removes their own advisor, or a manager removes any
    advisor on the deal (reason required either way)."""
    if not (reason or "").strip():
        raise R.DealRoomError(422, "Give a reason to remove the advisor.")
    cur = await conn.fetchrow(f"SELECT {ADVISOR_COLS} FROM deal_room_advisors WHERE deal_id = $1 AND advisor_user_id "
                              "= $2 AND valid_to IS NULL AND status <> 'revoked'", _uuid(deal["id"]),
                              _uuid(advisor_user_id))
    if cur is None or (by_investor is not None and cur["access_user_id"] != _uuid(by_investor)):
        raise R.DealRoomError(404, "Advisor not found")
    row = await restate_advisor(conn, dict(cur), actor=actor, status="revoked", revoked_by=_uuid(actor),
                                revoked_reason=reason.strip())
    await R.audit(conn, org_id=deal["org_id"], actor=actor, action="deal_room.advisor_remove", resource_id=row["id"],
                  payload={"deal_id": deal["id"], "advisor_user_id": advisor_user_id, "reason": reason.strip(),
                           "by": "investor" if by_investor else "manager"})
    return row


async def end_advisors(conn, deal_id, investor_user_id, *, actor, reason: str) -> int:
    """Revoking / declining the investor ends every advisor of theirs on the deal."""
    rows = await conn.fetch(f"SELECT {ADVISOR_COLS} FROM deal_room_advisors WHERE deal_id = $1 AND access_user_id = $2 "
                            "AND valid_to IS NULL AND status <> 'revoked'", _uuid(deal_id), _uuid(investor_user_id))
    for cur in rows:
        await restate_advisor(conn, dict(cur), actor=actor, status="revoked", revoked_by=_uuid(actor),
                              revoked_reason=reason)
    return len(rows)


async def advisors_of(conn, deal_id, investor_user_id=None, lab: dict | None = None) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT {ADVISOR_COLS} FROM deal_room_advisors WHERE deal_id = $1 AND valid_to IS NULL "
        "AND ($2::uuid IS NULL OR access_user_id = $2) ORDER BY valid_from", _uuid(deal_id), _uuid(investor_user_id))
    lab = lab or {}
    return [{"advisor_user_id": str(r["advisor_user_id"]), "investor_user_id": str(r["access_user_id"]),
             "name": r["name"], "firm": r["firm"], "role": r["role"],
             "role_label": lab.get(f"deal_room_advisor_role_{r['role']}"), "status": r["status"],
             "status_label": lab.get(f"deal_room_advisor_status_{r['status']}"),
             "acknowledged_at": r["acknowledged_at"].isoformat() if r["acknowledged_at"] else None,
             "revoked_reason": r["revoked_reason"]} for r in rows]


# ── Return-or-destroy notices ──────────────────────────────────────────────
async def send_return_notice(conn, deal, access_row: dict, *, actor) -> dict:
    """Recorded on the revoked access row; the caller sends it in-app."""
    investor = await conn.fetchval("SELECT COALESCE(full_name, email) FROM users WHERE id = $1", access_row["user_id"])
    entity = await conn.fetchval("SELECT COALESCE(legal_name, display_name) FROM entities WHERE id = $1 AND valid_to "
                                 "IS NULL AND system_to IS NULL", access_row["entity_id"]) \
        if access_row.get("entity_id") else None
    text = RETURN_NOTICE_TEXT.format(deal=deal["name"], investor=investor, entity=entity or "your investing entity",
                                     on=date.today().isoformat())
    row = await conn.fetchrow(
        "INSERT INTO deal_room_return_notices (org_id, deal_id, access_id, user_id, entity_id, notice_text, "
        "notice_sha256, sent_by) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id, sent_at",
        _uuid(deal["org_id"]), _uuid(deal["id"]), _uuid(access_row["id"]), _uuid(access_row["user_id"]),
        _uuid(access_row.get("entity_id")), text, sha256_text(text), _uuid(actor))
    await R.audit(conn, org_id=deal["org_id"], actor=actor, action="deal_room.return_notice", resource_id=row["id"],
                  payload={"deal_id": deal["id"], "user_id": access_row["user_id"], "access_id": access_row["id"],
                           "notice_sha256": sha256_text(text)})
    return {"id": row["id"], "sent_at": row["sent_at"], "text": text}


async def live_notice(conn, deal_id, user_id) -> dict | None:
    r = await conn.fetchrow(
        "SELECT id, access_id, notice_text, notice_sha256, sent_at, acknowledged_at, ack_typed_name FROM "
        "deal_room_return_notices WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL ORDER BY sent_at DESC "
        "LIMIT 1", _uuid(deal_id), _uuid(user_id))
    return dict(r) if r else None


async def acknowledge_notice(conn, deal, principal, *, typed_name: str) -> dict:
    n = await live_notice(conn, deal["id"], principal.user_id)
    if n is None:
        raise R.DealRoomError(404, "No return-or-destroy notice for you on this deal.")
    if n["acknowledged_at"] is not None:
        raise R.DealRoomError(409, "Already acknowledged.")
    me = await conn.fetchval("SELECT full_name FROM users WHERE id = $1", _uuid(principal.user_id))
    if not me or R._norm_name(typed_name) != R._norm_name(me):
        raise R.DealRoomError(422, "Type your full name exactly as it appears on your account.")
    await conn.execute("UPDATE deal_room_return_notices SET valid_to = clock_timestamp() WHERE id = $1", n["id"])
    row = await conn.fetchrow(
        "INSERT INTO deal_room_return_notices (org_id, deal_id, access_id, user_id, entity_id, notice_text, "
        "notice_sha256, sent_by, sent_at, acknowledged_at, ack_typed_name, valid_from) "
        "SELECT org_id, deal_id, access_id, user_id, entity_id, notice_text, notice_sha256, sent_by, sent_at, "
        "clock_timestamp(), $2, clock_timestamp() FROM deal_room_return_notices WHERE id = $1 "
        "RETURNING id, acknowledged_at", n["id"], typed_name.strip())
    await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.return_notice_ack",
                  resource_id=row["id"], payload={"deal_id": deal["id"], "notice_sha256": n["notice_sha256"]})
    return dict(row)


# ── CRM relationship date (506(b) input) ───────────────────────────────────
async def set_relationship_date(conn, org_id, entity_id, *, established_on: date, basis: str | None, actor) -> dict:
    await conn.execute("UPDATE crm_relationship_dates SET valid_to = clock_timestamp() WHERE entity_id = $1 "
                       "AND valid_to IS NULL", _uuid(entity_id))
    row = await conn.fetchrow(
        "INSERT INTO crm_relationship_dates (org_id, entity_id, established_on, basis, recorded_by, valid_from) "
        "VALUES ($1, $2, $3, $4, $5, clock_timestamp()) RETURNING id, entity_id, established_on, basis, valid_from",
        _uuid(org_id), _uuid(entity_id), established_on, basis, _uuid(actor))
    return dict(row)

