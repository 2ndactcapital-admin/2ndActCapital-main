"""BUGFIX4 — maker-checker refusals that NAME the people, in one place.

Joe signed in as jlarizza@gmail.com and as jlarizza@culmina.io, and both were refused "A different Super Admin must
approve this import." — correct (both sign-ins resolve to ONE user, the one who entered it), but the message said
neither who entered the item nor that the two logins are the same person, so it read as a bug. Every maker-checker
refusal that has the maker's id now says: "Entered by <maker> — a different <approver> must approve …", and when the
checker IS the maker it adds which sign-ins resolve to that one person.

Reads ``users`` / ``user_identities`` on the caller's connection (the request's org context).
"""

from __future__ import annotations

PLACEHOLDER = "@placeholder.local"


def display_name(row) -> str:
    if row is None:
        return "an unknown user"
    name = (row["full_name"] or "").strip()
    email = row["email"] or ""
    if email.endswith(PLACEHOLDER):
        email = row["contact_email"] or "a sign-in with no verified email"
    return f"{name} ({email})" if name and email else (name or email or "an unknown user")


async def person(conn, user_id) -> dict | None:
    if user_id is None:
        return None
    row = await conn.fetchrow("SELECT id, full_name, email, contact_email, auth0_sub FROM users WHERE id = $1::uuid",
                              str(user_id))
    if row is None:
        return None
    subs = [row["auth0_sub"]] if row["auth0_sub"] else []
    subs += [r["auth0_sub"] for r in await conn.fetch(
        "SELECT auth0_sub FROM user_identities WHERE user_id = $1::uuid ORDER BY created_at", str(user_id))]
    emails = [e for e in [row["email"]] if e and not e.endswith(PLACEHOLDER)]
    emails += [r["email"] for r in await conn.fetch(
        "SELECT email FROM user_identities WHERE user_id = $1::uuid AND email IS NOT NULL ORDER BY created_at",
        str(user_id)) if r["email"] not in emails]
    return {"id": str(row["id"]), "name": display_name(row), "sign_ins": len(subs), "emails": emails}


def refusal_text(maker: dict | None, checker: dict | None, *, approver: str, item: str) -> str:
    """Pure: the refusal sentence from two ``person`` dicts."""
    who = maker["name"] if maker else "another user"
    text = f"Entered by {who} — a different {approver} must approve this {item}."
    if maker and checker and maker["id"] == checker["id"]:
        text += " That is you"
        if checker["sign_ins"] > 1 or len(checker["emails"]) > 1:
            logins = ", ".join(checker["emails"]) or f"{checker['sign_ins']} sign-ins"
            text += (f": your sign-ins ({logins}) all resolve to the same person, so none of them can approve "
                     "what any of them entered")
        text += "."
    return text


async def maker_refusal(conn, *, maker_id, checker_id, approver: str, item: str) -> str:
    return refusal_text(await person(conn, maker_id), await person(conn, checker_id), approver=approver, item=item)


async def entered_by_note(conn, *, maker_id, viewer_id, approver: str, item: str) -> str | None:
    """The line a draft shows its own maker in place of the Approve button (None for anyone else)."""
    if maker_id is None or viewer_id is None or str(maker_id) != str(viewer_id):
        return None
    return await maker_refusal(conn, maker_id=maker_id, checker_id=viewer_id, approver=approver, item=item)
