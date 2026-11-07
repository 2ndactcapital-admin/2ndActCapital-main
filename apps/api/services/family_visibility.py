"""VISIBILITY1 — which staff see which families, enforced. THE one rule (never re-implemented per route).

THE UNIT IS A FAMILY, not an entity.
  * A HOUSEHOLD family = the household's member entities (``household_memberships`` ∪ entities whose
    ``primary_household_id`` is the household) plus everything they own or benefit from, transitively, through
    BOTH ownership tables (``entity_relationships`` ownership / beneficiary edges and ``entity_ownership``) — the
    same edges the member engine's ``resolve_entity_set`` subtree and ``ownership_read.ownership_edges`` read.
    Accounts, positions, documents, notes and liabilities belong to the family of the entity that owns them
    (``accounts.primary_entity_id`` / ``household_id``, ``positions.owner_entity_id``, ``documents.entity_id``, …).
  * A STANDALONE family = a live entity that no household family reaches and that no other standalone entity owns
    (a "root"), plus its own downward closure. An entity in a cycle with no root becomes a root itself.

THE CLOSURE RULE: an assignment (or a relationship-manager / creator link) on ANY entity of a family grants the whole
family —
  * an entity that is a MEMBER of one or more households grants each of those households (a person in two
    households: both);
  * otherwise, an entity that belongs to exactly ONE family grants that family;
  * an entity that belongs to TWO OR MORE families (a shared vehicle / SPV owned by several families) grants only
    its own downward closure — assigning a shared vehicle must never hand anyone every investor family. A shared
    entity is SEEN by staff of any family that contains it.

THE RULE (visible_family_ids):
  super admin                → every family (unscoped)
  org admin of the org       → every family of the org (unscoped) — ``manage_org_settings`` by role (services.rbac)
  member / client role       → their own family (BUGFIX2, unchanged — services.authz member path)
  other internal staff       → families assigned to them, to a team they are on, or to anyone who reports to them
                               (users.manager_id, transitively, cycle-safe)
                               ∪ families whose RELATIONSHIP MANAGER they are (entities.relationship_manager_id) —
                                 kept after the family is assigned to someone else
                               ∪ families they CREATED (households.created_by / entities.created_by) that have NO
                                 assignment — lost as soon as anyone is assigned (owner decision, PROJECT_STATUS)
  then the restricted-access filter (``entities.access_restricted`` without a ``restricted_access_grants`` row).

THE SWITCH: org_settings ``staff_visibility.enforced`` (default false, written only by PUT
/admin/staff-visibility/enforcement — audited, org admin only). When false every caller is exactly as before this
sprint (internal staff unscoped; the legacy resolver for its legacy callers); the preview still works.

COST: the whole org graph is read in ONE statement (``GRAPH_SQL``), at most once per request (cached on
``request.state``), and only for a restricted caller of an enforcing org — read on the Principal's OWN transaction
(``preload_scope``, FIX 1), so it costs the request exactly that one statement and no second connection. The switch
itself rides on the Principal's one statement (``services.authz.PRINCIPAL_COLUMNS``). Lists filter with one
``= ANY($n::uuid[])``.

FAIL CLOSED: if the graph cannot be read the request raises (refused), it is never answered unscoped.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from uuid import UUID

SETTING_KEY = "staff_visibility.enforced"
ROLE_LABELS_KEY = "staff_visibility.role_labels"
DEFAULT_ROLE_LABELS = ("Lead adviser", "Associate", "Service")

#: Why a family is visible — published to the preview (labels come from the API, never the client).
REASON_LABELS = {
    "all_super_admin": "Super Admin — every family",
    "all_org_admin": "Org admin — every family in the org",
    "assignment": "Assigned to them",
    "team": "Assigned to their team",
    "report": "Assigned to someone who reports to them",
    "relationship_manager": "Relationship manager",
    "creator": "Created it (no one is assigned)",
}

NOT_FOUND = {
    "entity_id": "Entity not found",
    "household_id": "Household not found",
    "account_id": "Account not found",
    "position_id": "Position not found",
    "transaction_id": "Transaction not found",
    "liability_id": "Liability not found",
    "document_id": "Document not found",
    "rel_id": "Relationship not found",
    "proposal_id": "Proposal not found",
    "instruction_id": "Instruction not found",
    "gift_id": "Gift not found",
    "commitment_id": "Commitment not found",
}

# ONE statement: the org's whole family graph. $1 = org id. Every leg is org-scoped (and RLS-scoped).
GRAPH_SQL = """
SELECT
  COALESCE((SELECT json_agg(json_build_object(
        'id', e.id, 'name', e.display_name, 'type', e.entity_type::text, 'cb', e.created_by,
        'rm', e.relationship_manager_id, 'ph', e.primary_household_id, 'r', e.access_restricted,
        'live', (e.valid_to IS NULL AND e.system_to IS NULL)))
     FROM entities e WHERE e.org_id = $1), '[]'::json) AS entities,
  COALESCE((SELECT json_agg(json_build_object('id', h.id, 'name', h.name, 'cb', h.created_by))
     FROM households h WHERE h.org_id = $1), '[]'::json) AS households,
  COALESCE((SELECT json_agg(json_build_object('h', hm.household_id, 'e', hm.entity_id, 'at', hm.added_at)
                            ORDER BY hm.added_at, hm.entity_id)
     FROM household_memberships hm JOIN households h ON h.id = hm.household_id WHERE h.org_id = $1),
     '[]'::json) AS memberships,
  COALESCE((SELECT json_agg(json_build_object('p', x.p, 'c', x.c)) FROM (
       SELECT r.from_entity_id AS p, r.to_entity_id AS c FROM entity_relationships r
        WHERE r.org_id = $1 AND r.relationship_type IN ('ownership', 'beneficiary')
          AND r.valid_to IS NULL AND r.system_to IS NULL
       UNION
       SELECT o.parent_id, o.child_id FROM entity_ownership o
        WHERE o.org_id = $1 AND o.valid_to IS NULL AND o.system_to IS NULL) x), '[]'::json) AS edges,
  COALESCE((SELECT json_agg(json_build_object('id', sa.id, 'e', sa.entity_id, 'u', sa.assigned_to_user_id,
                            't', sa.assigned_to_team_id, 'l', sa.role_label, 'at', sa.assigned_at,
                            'by', sa.assigned_by) ORDER BY sa.assigned_at, sa.id)
     FROM staff_assignments sa WHERE sa.org_id = $1), '[]'::json) AS assignments,
  COALESCE((SELECT json_agg(json_build_object('t', tm.team_id, 'u', tm.user_id))
     FROM team_members tm JOIN teams t ON t.id = tm.team_id WHERE t.org_id = $1), '[]'::json) AS team_members,
  COALESCE((SELECT json_agg(json_build_object('id', t.id, 'name', t.name))
     FROM teams t WHERE t.org_id = $1), '[]'::json) AS teams,
  COALESCE((SELECT json_agg(json_build_object('id', u.id, 'm', u.manager_id, 'name', u.full_name, 'email', u.email))
     FROM users u WHERE u.org_id = $1), '[]'::json) AS users,
  COALESCE((SELECT json_agg(json_build_object('e', g.entity_id, 'u', g.user_id))
     FROM restricted_access_grants g WHERE g.org_id = $1), '[]'::json) AS restricted_grants,
  COALESCE((SELECT json_agg(json_build_object('id', a.id, 'e', a.primary_entity_id, 'h', a.household_id))
     FROM (SELECT DISTINCT ON (id) id, primary_entity_id, household_id FROM accounts WHERE org_id = $1
           ORDER BY id, system_to IS NULL DESC, valid_to IS NULL DESC) a), '[]'::json) AS accounts
"""

ENFORCED_SQL = "SELECT setting_value FROM org_settings WHERE org_id = $1 AND setting_key = $2"


def _s(v) -> str | None:
    return None if v is None else str(v)


def _j(v):
    if v is None:
        return []
    return json.loads(v) if isinstance(v, (str, bytes)) else v


def setting_truthy(value) -> bool:
    """org_settings.setting_value (jsonb, decoded or text) → bool. Only a real ``true`` enforces."""
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    return value is True


# ── The graph ──────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Family:
    key: str                     # "household:<id>" | "entity:<root id>"
    kind: str                    # "household" | "entity"
    id: str
    name: str
    members: set = field(default_factory=set)      # anchor entities (household members, or the root)
    entities: set = field(default_factory=set)     # the closure
    anchor_entity_id: str | None = None            # where a new assignment for the whole family is recorded


class FamilyGraph:
    """The org's families, built in memory from one ``GRAPH_SQL`` row. Pure — no I/O after construction."""

    def __init__(self, row):
        ents = _j(row["entities"])
        self.entity = {str(e["id"]): e for e in ents}
        self.live = {k for k, e in self.entity.items() if e.get("live")}
        self.households = {str(h["id"]): h for h in _j(row["households"])}
        self.assignments = [a for a in _j(row["assignments"])]
        self.teams = {str(t["id"]): t["name"] for t in _j(row["teams"])}
        self.users = {str(u["id"]): u for u in _j(row["users"])}
        self.team_members = defaultdict(set)     # user → teams
        for tm in _j(row["team_members"]):
            self.team_members[str(tm["u"])].add(str(tm["t"]))
        self.reports_of = defaultdict(set)       # manager → direct reports
        for u in self.users.values():
            if u.get("m"):
                self.reports_of[str(u["m"])].add(str(u["id"]))
        self.restricted_grants = {(str(g["e"]), str(g["u"])) for g in _j(row["restricted_grants"])}
        try:
            accts = _j(row["accounts"])
        except (KeyError, IndexError):
            accts = []
        self.accounts = {str(a["id"]): (_s(a.get("e")), _s(a.get("h"))) for a in accts}

        self.children = defaultdict(set)
        self.parents = defaultdict(set)
        for ed in _j(row["edges"]):
            p, c = str(ed["p"]), str(ed["c"])
            if p != c:
                self.children[p].add(c)
                self.parents[c].add(p)

        self.household_members = defaultdict(list)
        self.member_of = defaultdict(set)        # entity → households it is a MEMBER of
        for m in _j(row["memberships"]):
            h, e = str(m["h"]), str(m["e"])
            if e not in self.household_members[h]:
                self.household_members[h].append(e)
            self.member_of[e].add(h)
        for eid, e in self.entity.items():
            ph = _s(e.get("ph"))
            if ph and ph in self.households:
                if eid not in self.household_members[ph]:
                    self.household_members[ph].append(eid)
                self.member_of[eid].add(ph)

        self._closure: dict[str, frozenset] = {}
        self._owners: dict | None = None
        self.families: dict[str, Family] = {}
        self.families_of = defaultdict(set)      # entity → family keys containing it
        self._build()

    def closure(self, eid: str) -> frozenset:
        """``eid`` plus everything below it (cycle-safe)."""
        hit = self._closure.get(eid)
        if hit is not None:
            return hit
        seen = {eid}
        q = deque([eid])
        while q:
            cur = q.popleft()
            for c in self.children.get(cur, ()):
                if c not in seen:
                    seen.add(c)
                    q.append(c)
        out = frozenset(seen)
        self._closure[eid] = out
        return out

    def _add(self, fam: Family):
        self.families[fam.key] = fam
        for e in fam.entities:
            self.families_of[e].add(fam.key)

    def _build(self):
        for hid, h in self.households.items():
            members = list(self.household_members.get(hid, []))
            ents: set = set()
            for m in members:
                ents |= self.closure(m)
            self._add(Family(key=f"household:{hid}", kind="household", id=hid, name=h.get("name") or "Household",
                             members=set(members), entities=ents, anchor_entity_id=members[0] if members else None))
        covered = set(self.families_of)
        standalone = sorted(self.live - covered)
        stand_set = set(standalone)
        roots = [e for e in standalone if not (self.parents.get(e, set()) & stand_set)]
        for r in roots:
            self._add_root(r)
        for e in standalone:                       # a cycle with no root: each uncovered entity roots itself
            if e not in self.families_of:
                self._add_root(e)

    def _add_root(self, r: str):
        self._add(Family(key=f"entity:{r}", kind="entity", id=r, name=self.entity[r].get("name") or "Record",
                         members={r}, entities=set(self.closure(r)), anchor_entity_id=r))

    # The closure rule: what one anchor entity grants.
    def grant(self, eid) -> tuple[set, set]:
        """(family keys, extra entity ids) granted by an assignment / ownership link on ``eid``."""
        eid = _s(eid)
        if not eid or eid not in self.entity:
            return set(), set()
        households = self.member_of.get(eid)
        if households:
            return {f"household:{h}" for h in households}, set()
        keys = self.families_of.get(eid, set())
        if len(keys) == 1:
            return set(keys), set()
        return set(), set(self.closure(eid))       # shared (or orphaned non-live) — its own subtree only

    def assigned_keys(self) -> set:
        out: set = set()
        for a in self.assignments:
            out |= self.grant(a["e"])[0]
        return out

    def report_ids(self, user_id: str) -> set:
        """``user_id`` and everyone below them (users.manager_id), cycle-safe."""
        seen = {user_id}
        q = deque([user_id])
        while q:
            cur = q.popleft()
            for r in self.reports_of.get(cur, ()):
                if r not in seen:
                    seen.add(r)
                    q.append(r)
        return seen

    def owners(self, key: str) -> dict:
        """Who owns a family: its creator(s) (households.created_by, and created_by of any entity whose grant is
        this family) and relationship manager(s) (relationship_manager_id, same entities). Built once per graph."""
        if self._owners is None:
            own = defaultdict(lambda: (set(), set()))
            for fam in self.families.values():
                if fam.kind == "household" and self.households[fam.id].get("cb"):
                    own[fam.key][0].add(str(self.households[fam.id]["cb"]))
            for eid, e in self.entity.items():
                if not (e.get("cb") or e.get("rm")):
                    continue
                for k in self.grant(eid)[0]:
                    if e.get("cb"):
                        own[k][0].add(str(e["cb"]))
                    if e.get("rm"):
                        own[k][1].add(str(e["rm"]))
            self._owners = {k: {"created_by": sorted(v[0]), "relationship_managers": sorted(v[1])}
                            for k, v in own.items()}
        return self._owners.get(key) or {"created_by": [], "relationship_managers": []}

    def visible_for(self, user_id: str) -> tuple[dict, set]:
        """(reasons by family key, extra entity ids) for a restricted staff user. Reasons are lists of dicts."""
        user_id = str(user_id)
        reasons: dict = defaultdict(list)
        extra: set = set()
        relevant = self.report_ids(user_id)
        own_teams = self.team_members.get(user_id, set())
        teams = set()
        for u in relevant:
            teams |= self.team_members.get(u, set())

        def _grant(eid, reason):
            keys, ex = self.grant(eid)
            for k in keys:
                if reason not in reasons[k]:
                    reasons[k].append(reason)
            extra.update(ex)

        for a in self.assignments:
            au, at = _s(a.get("u")), _s(a.get("t"))
            if au == user_id:
                _grant(a["e"], {"kind": "assignment", "role_label": a.get("l")})
            elif at and at in own_teams:
                _grant(a["e"], {"kind": "team", "team": self.teams.get(at), "role_label": a.get("l")})
            elif (au and au in relevant) or (at and at in teams):
                via = au if au in relevant else next((u for u in relevant if at in self.team_members.get(u, ())), None)
                name = (self.users.get(via) or {}).get("name") or (self.users.get(via) or {}).get("email")
                _grant(a["e"], {"kind": "report", "via": name, "role_label": a.get("l")})

        for eid, e in self.entity.items():
            if _s(e.get("rm")) == user_id:
                _grant(eid, {"kind": "relationship_manager"})

        assigned = self.assigned_keys()
        for key, fam in self.families.items():
            if key in assigned:
                continue
            if user_id in self.owners(key)["created_by"]:
                if {"kind": "creator"} not in reasons[key]:
                    reasons[key].append({"kind": "creator"})
        return {k: v for k, v in reasons.items() if v}, extra

    def restricted_filter(self, entity_ids: set, user_id: str) -> set:
        user_id = str(user_id)
        return {e for e in entity_ids
                if not (self.entity.get(e) or {}).get("r") or (e, user_id) in self.restricted_grants}


@dataclass(frozen=True)
class StaffScope:
    """A restricted staff caller's visible set (never None-means-all: an empty set means nothing)."""
    entity_ids: frozenset
    household_ids: frozenset
    family_keys: frozenset
    account_ids: frozenset = frozenset()
    #: every id the org KNOWS (entities, households, accounts) — the scrubber only removes a row naming one of
    #: these that is not visible; an id outside the org's family universe (a global security) is left alone.
    universe: frozenset = frozenset()


async def load_graph(conn, org_id) -> FamilyGraph:
    row = await conn.fetchrow(GRAPH_SQL, UUID(str(org_id)))
    return FamilyGraph(row)


def scope_from_graph(graph: FamilyGraph, user_id: str) -> StaffScope:
    reasons, extra = graph.visible_for(user_id)
    ents: set = set(extra)
    for k in reasons:
        ents |= graph.families[k].entities
    ents = graph.restricted_filter(ents, user_id)
    hh = frozenset(graph.families[k].id for k in reasons if graph.families[k].kind == "household")
    accts = frozenset(a for a, (e, h) in graph.accounts.items() if (e and e in ents) or (h and h in hh))
    universe = frozenset(graph.entity) | frozenset(graph.households) | frozenset(graph.accounts)
    return StaffScope(entity_ids=frozenset(ents), household_ids=hh, family_keys=frozenset(reasons),
                      account_ids=accts, universe=universe)


# ── Principal → scope ──────────────────────────────────────────────────────────────────────────────────────────
def is_org_admin(p) -> bool:
    from services import rbac
    return bool(p and p.admitted and rbac.ORG_ADMIN_PERMISSION in p.permissions)


def sees_all(p) -> str | None:
    """'all_super_admin' / 'all_org_admin' when the rule gives this principal every family, else None."""
    if p is None or not p.admitted:
        return None
    if p.is_super_admin:
        return "all_super_admin"
    if is_org_admin(p):
        return "all_org_admin"
    return None


def restricted_principal(p) -> bool:
    """True when the rule restricts this principal: an enforcing org, an internal caller, not super / org admin.
    Client roles are NOT handled here — their own-family path (BUGFIX2) is unchanged."""
    return bool(p and p.admitted and getattr(p, "visibility_enforced", False)
                and (p.is_internal or p.is_staff) and sees_all(p) is None)


async def scope_for_request(request, pool) -> StaffScope | None:
    """None = unscoped (switch off, super admin, org admin, or not an internal caller); otherwise the caller's
    StaffScope — computed at most ONCE per request (cached on request.state)."""
    from services.authz import principal_of

    p = principal_of(request)
    if not restricted_principal(p):
        return None
    cached = getattr(request.state, "visibility_scope", None)
    if cached is not None:
        return cached
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(p.org_id, False)
    try:
        async with pool.acquire() as conn:
            graph = await load_graph(conn, p.org_id)
    finally:
        reset_rls_context(tokens)
    scope = scope_from_graph(graph, p.user_id)
    request.state.visibility_scope = scope
    request.state.visibility_graph = graph
    return scope


async def preload_scope(conn, request, p) -> None:
    """VISIBILITY1 FIX 1 — read the visible set on the Principal's OWN connection and transaction
    (``services.users.resolve_request_principal``), so enforcement costs the request exactly ONE statement (the
    graph) instead of a second acquire (BEGIN + set_config + graph + COMMIT + the pool's release reset = 5).

    Only when that transaction's RLS org IS the principal's org and not super-admin scoped — the same context
    ``scope_for_request`` would set itself. Otherwise (or if the read fails) nothing is cached and
    ``scope_for_request`` reads it on its own connection, failing closed exactly as before."""
    if not restricted_principal(p) or getattr(request.state, "visibility_scope", None) is not None:
        return
    from services.database import current_rls_context

    guc_org, guc_super, _sub = current_rls_context()
    if guc_super or not guc_org or str(guc_org) != str(p.org_id):
        return
    try:
        graph = await load_graph(conn, p.org_id)
    except Exception as exc:  # noqa: BLE001 — never cache a partial scope; scope_for_request re-reads and refuses
        print(f"[visibility] graph preload failed, re-read on its own connection: {type(exc).__name__}")
        return
    request.state.visibility_graph = graph
    request.state.visibility_scope = scope_from_graph(graph, p.user_id)


async def enforced(conn, org_id) -> bool:
    return setting_truthy(await conn.fetchval(ENFORCED_SQL, UUID(str(org_id)), SETTING_KEY))


async def staff_visible_entity_ids(pool, user_id, org_id) -> set:
    """For the legacy callers of ``staff_visibility.get_staff_visible_entity_ids`` (assistant counts, document
    search, ownership tree): with the switch ON, the rule (every org entity for super / org admin); OFF → None,
    meaning "use the legacy resolver unchanged"."""
    from services.authz import load_principal
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(str(org_id), False)
    try:
        async with pool.acquire() as conn:
            if not await enforced(conn, org_id):
                return None
            p = await load_principal(conn, user_id)
            if p is None or not p.admitted:
                return set()
            if not p.is_super_admin and str(p.org_id) != str(org_id):
                return set()                       # org admin / staff of ANOTHER org: nothing here
            graph = await load_graph(conn, org_id)
    finally:
        reset_rls_context(tokens)
    if sees_all(p):
        return set(graph.entity)
    return set(scope_from_graph(graph, p.user_id).entity_ids)


# ── Preview ────────────────────────────────────────────────────────────────────────────────────────────────────
PREVIEW_USERS_SQL = """
SELECT u.id FROM users u WHERE u.org_id = $1 AND u.status = 'active' AND u.is_active ORDER BY u.full_name NULLS LAST, u.email
"""


def family_row(graph: FamilyGraph, key: str, reasons=None) -> dict:
    fam = graph.families[key]
    out = {"key": key, "kind": fam.kind, "id": fam.id, "name": fam.name,
           "anchor_entity_id": fam.anchor_entity_id, "entity_count": len(fam.entities)}
    if reasons is not None:
        out["reasons"] = [{**r, "label": REASON_LABELS[r["kind"]]} for r in reasons]
    return out


async def preview(conn, org_id) -> dict:
    """For every admitted internal user of the org: what they would see with the switch ON, and why."""
    from services.authz import PRINCIPAL_BY_ID_SQL, principal_from_row
    from services import rbac

    graph = await load_graph(conn, org_id)
    ids = [r["id"] for r in await conn.fetch(PREVIEW_USERS_SQL, UUID(str(org_id)))]
    people = []
    for uid in ids:
        p = principal_from_row(await conn.fetchrow(PRINCIPAL_BY_ID_SQL, uid, rbac.SUPER_ADMIN_ROLE))
        if p is None or not (p.is_internal or p.is_staff or p.is_super_admin):
            continue
        u = graph.users.get(str(uid)) or {}
        allk = sees_all(p)
        if allk:
            fams = [family_row(graph, k, [{"kind": allk}]) for k in sorted(graph.families,
                                                                           key=lambda k: graph.families[k].name)]
            extra = 0
        else:
            reasons, ex = graph.visible_for(p.user_id)
            fams = [family_row(graph, k, reasons[k]) for k in sorted(reasons, key=lambda k: graph.families[k].name)]
            covered = set().union(*(graph.families[k].entities for k in reasons))
            extra = len(set(ex) - covered)
        people.append({"user_id": p.user_id, "name": u.get("name"), "email": p.email, "roles": list(p.roles),
                       "sees_all": allk is not None, "family_count": len(fams), "families": fams,
                       "extra_entity_count": extra,
                       "manager_id": _s(u.get("m"))})
    none = [{"user_id": x["user_id"], "name": x["name"], "email": x["email"]}
            for x in people if not x["sees_all"] and x["family_count"] == 0]
    assigned = graph.assigned_keys()
    return {"families_total": len(graph.families),
            "unassigned_count": sum(1 for k in graph.families if k not in assigned),
            "people": people, "sees_none": none}


def unassigned(graph: FamilyGraph, scope: StaffScope | None) -> list[dict]:
    assigned = graph.assigned_keys()
    out = []
    for key in sorted(graph.families, key=lambda k: graph.families[k].name):
        if key in assigned or (scope is not None and key not in scope.family_keys):
            continue
        own = graph.owners(key)
        name_of = lambda uid: (graph.users.get(uid) or {}).get("name") or (graph.users.get(uid) or {}).get("email")
        out.append({**family_row(graph, key),
                    "created_by": [{"user_id": u, "name": name_of(u)} for u in own["created_by"]],
                    "relationship_managers": [{"user_id": u, "name": name_of(u)}
                                              for u in own["relationship_managers"]]})
    return out


def family_assignments(graph: FamilyGraph, key: str) -> list[dict]:
    """Every staff_assignments row whose anchor grants this family (the Team panel)."""
    out = []
    for a in graph.assignments:
        if key not in graph.grant(a["e"])[0]:
            continue
        uid, tid = _s(a.get("u")), _s(a.get("t"))
        u = graph.users.get(uid) or {}
        out.append({"id": str(a["id"]), "entity_id": str(a["e"]),
                    "entity_name": (graph.entity.get(str(a["e"])) or {}).get("name"),
                    "assigned_to_user_id": uid, "assigned_to_user_name": u.get("name") or u.get("email"),
                    "assigned_to_team_id": tid, "assigned_to_team_name": graph.teams.get(tid) if tid else None,
                    "role_label": a.get("l"), "assigned_at": a.get("at")})
    return out


__all__ = [
    "DEFAULT_ROLE_LABELS", "FamilyGraph", "GRAPH_SQL", "NOT_FOUND", "REASON_LABELS", "ROLE_LABELS_KEY",
    "SETTING_KEY", "StaffScope", "enforced", "family_assignments", "family_row", "is_org_admin", "load_graph",
    "preview", "restricted_principal", "scope_for_request", "scope_from_graph", "sees_all", "setting_truthy",
    "staff_visible_entity_ids", "unassigned",
]


# ── The ONE guard for every family-scoped route (registered app-wide in main.py) ───────────────────────────────
# Path / query parameters that name a family record, and how each resolves to (entity ids, household ids). A name
# whose meaning depends on the route (document_id, rel_id, proposal_id, …) is resolved only under the listed URL
# prefixes; anywhere else it is not a family id and is left to the route.
_ACCOUNT_SQL = ("SELECT primary_entity_id, household_id FROM accounts WHERE id = $1 AND org_id = $2 "
                "ORDER BY system_to IS NULL DESC, valid_to IS NULL DESC LIMIT 1")
_RESOLVERS = {
    # name: (url prefixes or None = everywhere, SQL returning entity / household columns)
    "account_id": (None, _ACCOUNT_SQL),
    "position_id": (("/api/v1/portfolio/positions/",),
                    "SELECT owner_entity_id AS e FROM portfolio.positions WHERE id = $1 AND org_id = $2 LIMIT 1"),
    "transaction_id": (("/api/v1/portfolio/transactions/",),
                       "SELECT p.owner_entity_id AS e FROM portfolio.transactions t JOIN portfolio.positions p "
                       "ON p.id = t.position_id WHERE t.id = $1 AND t.org_id = $2 LIMIT 1"),
    "liability_id": (("/api/v1/liabilities/",),
                     "SELECT owner_entity_id AS e FROM portfolio.liabilities WHERE id = $1 AND org_id = $2 LIMIT 1"),
    "document_id": (("/api/v1/documents/",),
                    "SELECT entity_id AS e, true AS org_general_ok FROM documents WHERE id = $1 AND org_id = $2"),
    "rel_id": (("/api/v1/crm-relationships/", "/api/v1/entity-relationships/"),
               "SELECT from_entity_id AS e, to_entity_id AS e2 FROM entity_relationships WHERE id = $1 AND org_id = $2 "
               "LIMIT 1"),
    "proposal_id": (("/api/v1/proposals/",),
                    "SELECT a.primary_entity_id AS e, a.household_id AS h FROM rebalance_proposals rp "
                    "JOIN accounts a ON a.id = rp.account_id WHERE rp.id = $1 AND rp.org_id = $2 LIMIT 1"),
    "instruction_id": (("/api/v1/execution/instructions/",),
                       "SELECT a.primary_entity_id AS e, a.household_id AS h FROM execution_instructions ei "
                       "JOIN accounts a ON a.id = ei.account_id WHERE ei.id = $1 AND ei.org_id = $2 LIMIT 1"),
    "gift_id": (("/api/v1/tax/gifts/",),
                "SELECT member_entity_id AS e, household_id AS h FROM gift_proposals WHERE id = $1 AND org_id = $2"),
    "commitment_id": (("/api/v1/modeling/ta/", "/api/v1/private-investments/"),
                      "SELECT p.owner_entity_id AS e FROM portfolio.commitments c JOIN portfolio.positions p "
                      "ON p.id = c.position_id WHERE c.id = $1 AND c.org_id = $2 LIMIT 1"),
}
#: URL prefixes where a parameter of that name is NOT a family record (a GL chart account; the firm's executing
#: accounts settings) — the guard leaves those routes alone.
_NOT_FAMILY = {"account_id": ("/api/v1/ledger/", "/api/v1/execution/blocks/settings/")}

#: Query-string filters that name a family record (a list filtered to an invisible family is refused the same way).
QUERY_PARAMS = ("entity_id", "household_id", "account_id", "owner_entity_id")


def _uuid(v):
    try:
        return UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return None


def _not_found(name: str):
    from fastapi import HTTPException

    return HTTPException(status_code=404, detail=NOT_FOUND.get(name, "Not found"))


async def _check(request, pool, scope: StaffScope, name: str, value, path: str) -> None:
    from services.authz import principal_of
    from services.database import reset_rls_context, set_rls_context

    if name in ("entity_id", "owner_entity_id"):
        u = _uuid(value)
        if u is None or str(u) not in scope.entity_ids:
            raise _not_found("entity_id")
        return
    if name == "household_id":
        u = _uuid(value)
        if u is None or str(u) not in scope.household_ids:
            raise _not_found(name)
        return
    if name == "account_id" and not any(path.startswith(px) for px in _NOT_FAMILY["account_id"]):
        u = _uuid(value)
        if u is None or str(u) not in scope.account_ids:
            raise _not_found(name)
        return
    spec = _RESOLVERS.get(name)
    if spec is None or any(path.startswith(px) for px in _NOT_FAMILY.get(name, ())):
        return
    prefixes, sql = spec
    if prefixes is not None and not any(path.startswith(px) for px in prefixes):
        return
    uid = _uuid(value)
    if uid is None:
        raise _not_found(name)
    p = principal_of(request)
    tokens = set_rls_context(p.org_id, False)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, uid, UUID(str(p.org_id)))
    finally:
        reset_rls_context(tokens)
    if row is None:
        raise _not_found(name)
    keys = set(row.keys())
    ents = [str(row[k]) for k in ("primary_entity_id", "e", "e2") if k in keys and row[k] is not None]
    hhs = [str(row[k]) for k in ("household_id", "h") if k in keys and row[k] is not None]
    if not ents and not hhs:
        # an org-general document (no entity): visible to staff, the document_visible rule
        if "org_general_ok" in keys:
            return
        raise _not_found(name)
    if name == "rel_id":
        ok = all(e in scope.entity_ids for e in ents)          # a relationship is changed only inside one's families
    else:
        ok = any(e in scope.entity_ids for e in ents) or any(h in scope.household_ids for h in hhs)
    if not ok:
        raise _not_found(name)


async def family_path_guard(request) -> None:
    """App-wide dependency (main.py). A restricted staff caller of an enforcing org gets the SAME 404 for a family
    record they cannot see as for a missing id — on every method, so writes are refused too. Everyone else: no-op,
    no statement."""
    from services.authz import principal_of

    p = principal_of(request)
    if not restricted_principal(p):
        return
    names = [(k, v) for k, v in (request.path_params or {}).items()]
    for q in QUERY_PARAMS:
        v = request.query_params.get(q)
        if v:
            names.append((q, v))
    names = [(k, v) for k, v in names if k in ("entity_id", "owner_entity_id", "household_id") or k in _RESOLVERS]
    if not names:
        return
    from services.database import get_pool

    pool = await get_pool()
    scope = await scope_for_request(request, pool)
    if scope is None:
        return
    for name, value in names:
        await _check(request, pool, scope, name, value, request.url.path)


async def require_visible(request, pool, *, entity_ids=(), household_ids=()) -> None:
    """For a WRITE naming family records in its BODY (the path guard sees only path / query): 404 unless every
    named record is visible to a restricted caller. No-op for everyone else."""
    scope = await scope_for_request(request, pool)
    if scope is None:
        return
    for e in entity_ids:
        if e is not None and str(e) not in scope.entity_ids:
            raise _not_found("entity_id")
    for h in household_ids:
        if h is not None and str(h) not in scope.household_ids:
            raise _not_found("household_id")


async def visible_household_ids(request, pool) -> set | None:
    """None (unscoped) or the restricted caller's visible household ids."""
    scope = await scope_for_request(request, pool)
    return None if scope is None else set(scope.household_ids)


# ── The response scrubber (restricted callers only; main.rls_context_middleware) ──────────────────────────────
#: Row keys that name the family a row belongs to. A list item carrying one of these with an id the org knows but
#: the caller may not see is removed — so a list, a search, an export rendered as JSON or a nested panel never shows
#: another family's row, whichever route produced it.
ENTITY_KEYS = ("entity_id", "owner_entity_id", "primary_entity_id", "member_entity_id", "principal_entity_id",
               "subject_entity_id", "parent_id", "child_id", "from_entity_id", "to_entity_id")
HOUSEHOLD_KEYS = ("household_id", "primary_household_id")
ACCOUNT_KEYS = ("account_id", "collateral_account_id")


def _row_visible(item: dict, scope: StaffScope) -> bool:
    own = item.get("id")
    if own is not None and not isinstance(own, (dict, list)):
        so = str(own).lower()       # the row IS a family record (an entity / household / account row)
        if so in scope.universe and not (so in scope.entity_ids or so in scope.household_ids
                                         or so in scope.account_ids):
            return False
    for keys, allowed in ((ENTITY_KEYS, scope.entity_ids), (HOUSEHOLD_KEYS, scope.household_ids),
                          (ACCOUNT_KEYS, scope.account_ids)):
        for k in keys:
            v = item.get(k)
            if v is None or isinstance(v, (dict, list)):
                continue
            sv = str(v).lower()
            if sv in scope.universe and sv not in allowed:
                return False
    return True


def row_visible(item: dict, scope: StaffScope | None) -> bool:
    """Public form of the scrubber's row rule (None scope → everything visible)."""
    return True if scope is None else _row_visible(item, scope)


def scrub(payload, scope: StaffScope):
    """``payload`` with every list item that names an invisible family removed (recursively)."""
    if isinstance(payload, list):
        return [scrub(x, scope) for x in payload if not (isinstance(x, dict) and not _row_visible(x, scope))]
    if isinstance(payload, dict):
        return {k: scrub(v, scope) for k, v in payload.items()}
    return payload


async def scope_for_principal(pool, p) -> StaffScope | None:
    """Uncached form of :func:`scope_for_request` for code that holds a Principal but no request."""
    if not restricted_principal(p):
        return None
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(p.org_id, False)
    try:
        async with pool.acquire() as conn:
            graph = await load_graph(conn, p.org_id)
    finally:
        reset_rls_context(tokens)
    return scope_from_graph(graph, p.user_id)


#: notifications.resource_type values that name a family record (resource_id is then its id).
NOTIFICATION_RESOURCE_KINDS = {"entity": "entity_ids", "household": "household_ids", "account": "account_ids"}


def notification_visible(item: dict, scope: StaffScope | None) -> bool:
    """A notification about a family reaches only people who can see that family (read-side, every channel that
    lists notifications goes through GET /notifications)."""
    if scope is None:
        return True
    attr = NOTIFICATION_RESOURCE_KINDS.get((item.get("resource_type") or "").lower())
    rid = item.get("resource_id")
    if attr and rid is not None and str(rid) in scope.universe and str(rid) not in getattr(scope, attr):
        return False
    payload = item.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    if isinstance(payload, dict) and not _row_visible(payload, scope):
        return False
    return True
