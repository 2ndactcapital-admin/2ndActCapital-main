"""PORTENTRY2 (MUSE-OLIVO-PERS-01) — what the caller may change on ONE CRM record, as an envelope the page renders
from, each key computed by the SAME rule as the route that would accept the write (never a second opinion).

The CRM record page used to render Edit / Remove / Add on the Ownership tab (and the Details edit, tax ids …) for
any internal role; support staff (internal, holding edit_crm_contact but NOT manage_members) could open the
ownership-% dialog with Save enabled while the API refuses it. Two gate shapes exist on the CRM write routes and
each is mirrored here exactly:

  * ``services.authz.require_permission(p, k)``           — Super Admin first, then exact (NO staff bypass):
    PUT /entities/{id} (details, status, tags, active), tax ids, employment, social profiles, attributes, notes apply.
  * ``services.authz.require_internal_permission(p, *ks)`` — Super Admin, the STAFF_ROLE_NAMES bypass, else an
    internal role holding one of ``ks``: ownership add / edit / remove, relationships, typed detail fields,
    liabilities (manage_members); addresses, contact, notes (manage_members OR edit_crm_contact).

The household's accounts follow routers.households._require_household_admin (internal + manage_members).
"""
from __future__ import annotations

from services.authz import Principal

MANAGE = "manage_members"
CONTACT = "edit_crm_contact"

#: envelope key → (gate shape, permissions) — the route each key stands for is named in the module docstring.
GATES: dict[str, tuple[str, tuple[str, ...]]] = {
    "record": ("exact", (MANAGE,)),            # PUT /entities/{id}: details, status, tags, active
    "tax_ids": ("exact", (MANAGE,)),           # POST /entities/{id}/tax-ids
    "employment": ("exact", (MANAGE,)),        # POST/PUT /entities/{id}/employment
    "social": ("exact", (MANAGE,)),            # POST /entities/{id}/social-profiles
    "attributes": ("exact", (MANAGE,)),        # POST /entities/{id}/attributes
    "ownership": ("internal", (MANAGE,)),      # POST /entities/{id}/ownership, PATCH/DELETE /entity-relationships/…
    "relationships": ("internal", (MANAGE,)),  # POST /entities/{id}/crm-relationships, …/end
    "details": ("internal", (MANAGE,)),        # PUT /entities/{id}/details
    "liabilities": ("internal", (MANAGE,)),    # POST /entities/{id}/liabilities …
    "addresses": ("internal", (MANAGE, CONTACT)),   # POST/PUT /entities/{id}/addresses
    "contact": ("internal", (MANAGE, CONTACT)),     # PUT /entities/{id}/contact
    "notes": ("internal", (MANAGE, CONTACT)),       # POST /entities/{id}/notes
    "accounts": ("internal", (MANAGE,)),       # household accounts (routers.households)
}


def allowed(p: Principal | None, key: str) -> bool:
    """True iff the route behind ``key`` would admit ``p`` — the exact rule of its gate."""
    if p is None or not p.admitted:
        return False
    shape, perms = GATES[key]
    if p.is_super_admin:
        return True
    if shape == "exact":
        return any(k in p.permissions for k in perms)
    if p.is_staff:
        return True
    return bool(p.is_internal and any(k in p.permissions for k in perms))


def envelope(p: Principal | None) -> dict:
    """``permissions`` + ``vocabularies.editable`` for a record page; every key present, False / absent when not
    allowed (a lost envelope fails closed in the page: lib/portentry2.mjs canEdit)."""
    flags = {k: allowed(p, k) for k in GATES}
    return {"permissions": {"can_read": bool(p is not None and p.admitted and (p.is_super_admin or p.is_internal)),
                            "can_write": any(flags.values()),
                            "is_super_admin": bool(p is not None and p.is_super_admin),
                            "read_permission": "internal", "write_permission": MANAGE, "can": flags},
            "vocabularies": {"editable": sorted(k for k, v in flags.items() if v), "inline_editable": []}}
