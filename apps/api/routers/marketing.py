"""Hollisworks marketing endpoints — pre-auth, public by design.

    GET  /marketing/firm-search   fuzzy-match a firm name -> login/enroll redirect
    POST /marketing/contact       store a contact-form submission

Both run on the platform's own apex marketing site BEFORE anyone is
authenticated (like /theme/public and /tenant/resolve), so they are listed in
main.PUBLIC_PATHS and rely on the pre-auth RLS carve-outs
(organizations_preauth_resolve, marketing_contacts_preauth_insert).
"""

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from services.database import get_pool
from services.marketing import match_firm, store_contact

router = APIRouter(tags=["marketing"])

# Minimal email shape check — the project has no email-validator dependency, and
# this endpoint only needs to reject obvious garbage before storing a lead.
import re as _re

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/marketing/firm-search")
async def firm_search(
    q: str = Query(..., description="Typed firm name"),
    intent: str = Query("login", description="login | enroll — which button was clicked"),
):
    """Match a typed firm name and resolve the correct login/enroll redirect.

    Returns ``{status, redirect_url, org_name, message}``. The frontend redirects
    only on ``status == "matched"``; ``ambiguous`` / ``none`` render the message
    and let the user retry. Intent is preserved end-to-end from the button click.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await match_firm(conn, q, intent)
    return result


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    firm: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    aum: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=5000)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("A valid work email is required")
        return v


@router.post("/marketing/contact")
async def contact(payload: ContactIn, request: Request):
    """Persist a marketing contact-form submission (pre-tenant, no org_id)."""
    source_host = request.headers.get("host")
    pool = await get_pool()
    async with pool.acquire() as conn:
        contact_id = await store_contact(
            conn,
            name=payload.name.strip(),
            firm=payload.firm.strip(),
            email=str(payload.email).strip(),
            aum=payload.aum,
            note=payload.note,
            source_host=source_host,
        )
    return {"status": "received", "id": contact_id}
