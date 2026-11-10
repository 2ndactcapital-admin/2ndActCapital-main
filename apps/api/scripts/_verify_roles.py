"""_verify_roles — ONE check that every fixture role is a user_roles grant the APP decides as meant (VERIFYFIX1).

WHY
---
BUGFIX4 made ``public.user_roles`` the ONE role source: ``services.authz.load_principal`` decides Super Admin, staff,
internal and every permission from the user's grants; ``users.role`` is only a mirror the write paths keep in step.
A verify that seeds a fixture user with ``users.role = 'super_admin'`` and no ``super_admin`` grant no longer has a
Super Admin — every check that leans on it then fails (or, worse, passes for the wrong reason: a 403 the verify
expected from a "member" that is really a 403 from a non-super "super admin").

The cash-track verifies each assert, right after seeding, that "every fixture role is a user_roles grant the app
decides as meant". This module is that assertion, shared, so every older verify in the regression set states the
same thing the same way instead of re-implementing it.

WHAT IT ASSERTS, PER FIXTURE USER
---------------------------------
``expect`` maps a user id to ``(org_id, role_names)`` or ``(org_id, role_names, admitted)`` or a dict with the keys
``org``, ``roles``, ``admitted`` (default True), ``has`` (permissions it must hold) and ``lacks`` (permissions it must
not hold).

1. Its ``user_roles`` grants in ``org`` are EXACTLY ``roles`` (read from the table, not from the seed's variables).
2. The app's own Principal (``services.authz.load_principal`` — the one the middleware builds) exists and
   ``admitted`` is as meant.
3. Admitted: Super Admin ⇔ ``super_admin`` is granted; staff ⇔ Super Admin or a ``STAFF_ROLE_NAMES`` grant; every
   permission the granted roles carry (role_permissions, re-read) is held; a Super Admin holds the whole catalog.
   Not admitted: no roles, no permissions, not super, not staff — whatever is granted.
4. ``has`` ⊆ permissions; ``lacks`` ∩ permissions = ∅ (a Super Admin "holds" everything, so ``lacks`` is only
   meaningful for a non-super user and is checked with ``Principal.has``, the gate the routes use).

The reads run in ONE transaction on the caller's connection with ``app.is_super_admin`` set LOCAL (the verify's own
platform read), so RLS context is on the connection inside its transaction (cash-track rule).
"""
from __future__ import annotations

from typing import Any, Callable

ROLE_NAMES_SQL = ("SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                  "WHERE ur.user_id = $1::uuid AND r.org_id = $2::uuid")
ROLE_PERMS_SQL = ("SELECT DISTINCT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                  "JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id "
                  "WHERE ur.user_id = $1::uuid AND r.org_id = $2::uuid")
CATALOG_SQL = "SELECT name FROM permissions"

LABEL = "every fixture role is a user_roles grant the app decides as meant"


def _spec(raw: Any) -> dict:
    if isinstance(raw, dict):
        d = dict(raw)
    else:
        t = tuple(raw)
        d = {"org": t[0], "roles": t[1]}
        if len(t) > 2:
            d["admitted"] = t[2]
    d["roles"] = frozenset(x for x in (d.get("roles") or ()) if x)
    d.setdefault("admitted", True)
    d["has"] = frozenset(d.get("has") or ())
    d["lacks"] = frozenset(d.get("lacks") or ())
    return d


async def roles_as_meant(conn, expect: dict) -> list[tuple]:
    """Every problem found, as (user id, [reasons]); empty when the app decides every fixture user as meant."""
    from services import authz as AZ  # imported late: the caller hydrates Doppler and sets AUTHZ_TEST_MODE first
    bad: list[tuple] = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        catalog = {r["name"] for r in await conn.fetch(CATALOG_SQL)}
        for uid, raw in expect.items():
            s = _spec(raw)
            org = str(s["org"])
            why: list[str] = []
            have = {r["name"] for r in await conn.fetch(ROLE_NAMES_SQL, str(uid), org)}
            if have != set(s["roles"]):
                why.append(f"user_roles {sorted(have)} != meant {sorted(s['roles'])}")
            carried = {r["name"] for r in await conn.fetch(ROLE_PERMS_SQL, str(uid), org)}
            pr = await AZ.load_principal(conn, uid)
            if pr is None:
                why.append("no Principal (no users row)")
                bad.append((str(uid), why))
                continue
            perms = set(pr.permissions or ())
            if bool(pr.admitted) is not bool(s["admitted"]):
                why.append(f"admitted {pr.admitted} != meant {s['admitted']}")
            if s["admitted"]:
                want_super = "super_admin" in s["roles"]
                want_staff = want_super or bool(AZ.STAFF_ROLE_NAMES & set(s["roles"]))
                if bool(pr.is_super_admin) is not want_super:
                    why.append(f"is_super_admin {pr.is_super_admin} != meant {want_super}")
                if bool(pr.is_staff) is not want_staff:
                    why.append(f"is_staff {pr.is_staff} != meant {want_staff}")
                if not carried <= perms:
                    why.append(f"role permissions not held: {sorted(carried - perms)[:6]}")
                if want_super and not catalog <= perms:
                    why.append(f"Super Admin lacks catalog keys: {sorted(catalog - perms)[:6]}")
                missing = sorted(p for p in s["has"] if not pr.has(p))
                if missing:
                    why.append(f"lacks meant permissions {missing}")
                extra = sorted(p for p in s["lacks"] if pr.has(p))
                if extra:
                    why.append(f"holds permissions it must not: {extra}")
            else:
                if pr.is_super_admin or pr.is_staff or perms or pr.roles:
                    why.append(f"not admitted yet decided super={pr.is_super_admin} staff={pr.is_staff} "
                               f"roles={list(pr.roles)} perms={len(perms)}")
            if why:
                bad.append((str(uid), why))
    return bad


async def check_roles_as_meant(conn, check: Callable[..., bool], expect: dict, note: str = "") -> bool:
    """``check(...)`` the shared assertion over ``expect`` (see the module doc). Returns the check's bool.

    A raise while reading is itself a [FAIL] of this check, never a skip."""
    try:
        bad = await roles_as_meant(conn, expect)
        detail = f"{len(expect)} users; {bad[:4]}" if bad else f"{len(expect)} users"
    except Exception as exc:  # noqa: BLE001 — the check fails, it does not vanish
        bad, detail = [("*", [f"{type(exc).__name__}: {exc}"])], f"raised {type(exc).__name__}: {exc}"
    label = LABEL + (f" ({note})" if note else "")
    return check(not bad and bool(expect), label, detail)
