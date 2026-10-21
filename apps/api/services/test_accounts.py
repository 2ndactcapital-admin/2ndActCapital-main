"""Test-account hygiene (USERMGMT1 Task 5).

``users.is_test`` marks verify fixtures. A trigger sets it for every
``@test.local`` address (docs/usermgmt1_part1.sql), which is the domain every
verify script seeds with; :func:`mark_test_user` sets it for any other fixture.
People & Access hides ``is_test`` rows unless asked.

Removing an orphaned fixture user means removing every row that depends on it
first. The dependency graph is READ, never hand-listed: every single-column
foreign key in ``pg_constraint`` (contype ``f``) is followed from ``users``
outward, transitively (a fixture's workflow definition → its versions → their
steps / runs → run steps …). The plan then refuses outright if any row in it
references a user who is NOT one of the fixtures being removed — a fixture's
row that a real person also touched is not a fixture's row to delete.

Deletion runs in ONE transaction, children before parents (topological order
over the FK graph restricted to the planned tables), and checks that every
DELETE removed exactly the planned number of rows. RLS can make a DELETE match
zero rows silently (CLAUDE.md, "row not found is the symptom of TWO different
bugs"), so a short count is an error that rolls the whole thing back, never a
partial purge.
"""

from __future__ import annotations

from collections import defaultdict

USERS_TABLE = "public.users"


async def mark_test_user(conn, user_id) -> None:
    """Flag a fixture user created with a non-``@test.local`` address."""
    await conn.execute("UPDATE users SET is_test = true WHERE id = $1", user_id)


async def fk_edges(conn) -> list[dict]:
    """Every single-column FK: child table/column → parent table/column."""
    rows = await conn.fetch(
        """
        SELECT c.conname,
               c.conrelid::regclass::text  AS child,
               ca.attname                  AS child_col,
               c.confrelid::regclass::text AS parent,
               pa.attname                  AS parent_col,
               c.confdeltype::text         AS on_delete
        FROM pg_constraint c
        JOIN pg_attribute ca ON ca.attrelid = c.conrelid  AND ca.attnum = c.conkey[1]
        JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = c.confkey[1]
        WHERE c.contype::text = 'f' AND array_length(c.conkey, 1) = 1
        ORDER BY 2, 3
        """
    )
    return [{**dict(r), "child": _qualify(r["child"]), "parent": _qualify(r["parent"])} for r in rows]


def _qualify(name: str) -> str:
    return name if "." in name else f"public.{name}"


def _tids(ctids) -> list[tuple[int, int]]:
    """asyncpg binds ``tid`` as a (block, offset) tuple, not its text form."""
    out = []
    for c in ctids:
        block, _, offset = c.strip("()").partition(",")
        out.append((int(block), int(offset)))
    return out


def _ident(table: str) -> str:
    schema, _, rel = table.partition(".")
    return f'"{schema}"."{rel}"'


async def plan_purge(conn, user_ids) -> dict[str, list[str]]:
    """``{table: [ctid, …]}`` for the users and every row depending on them.

    ctids are stable for the life of the transaction the purge runs in (rows
    are only ever deleted, never updated, between plan and delete), so the
    caller MUST plan and purge inside one transaction.
    """
    edges = await fk_edges(conn)
    by_parent: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_parent[e["parent"]].append(e)

    plan: dict[str, set[str]] = defaultdict(set)
    seed = await conn.fetch(
        "SELECT ctid::text AS t FROM public.users WHERE id = ANY($1::uuid[])", list(user_ids)
    )
    plan[USERS_TABLE] = {r["t"] for r in seed}
    frontier = {USERS_TABLE: set(plan[USERS_TABLE])}
    while frontier:
        nxt: dict[str, set[str]] = defaultdict(set)
        for parent, ctids in frontier.items():
            for e in by_parent.get(parent, []):
                keys = await conn.fetch(
                    f'SELECT DISTINCT "{e["parent_col"]}"::text AS k FROM {_ident(parent)} '
                    f'WHERE ctid = ANY($1::tid[]) AND "{e["parent_col"]}" IS NOT NULL',
                    _tids(ctids),
                )
                if not keys:
                    continue
                child_rows = await conn.fetch(
                    f'SELECT ctid::text AS t FROM {_ident(e["child"])} '
                    f'WHERE "{e["child_col"]}"::text = ANY($1::text[])',
                    [k["k"] for k in keys],
                )
                new = {r["t"] for r in child_rows} - plan[e["child"]]
                if new:
                    plan[e["child"]] |= new
                    nxt[e["child"]] |= new
        frontier = nxt
    return {t: sorted(c) for t, c in plan.items() if c}


async def foreign_user_references(conn, plan: dict[str, list[str]], user_ids) -> list[dict]:
    """Rows in the plan that reference a user OUTSIDE ``user_ids`` — each one
    is a reason not to purge. Includes users rows reached through a
    self-reference (``invited_by`` / ``manager_id`` / ``deactivated_by``)."""
    edges = [e for e in await fk_edges(conn) if e["parent"] == USERS_TABLE]
    ids = [str(u) for u in user_ids]
    found = []
    for e in edges:
        ctids = plan.get(e["child"])
        if not ctids:
            continue
        n = await conn.fetchval(
            f'SELECT count(*) FROM {_ident(e["child"])} WHERE ctid = ANY($1::tid[]) '
            f'AND "{e["child_col"]}" IS NOT NULL AND NOT ("{e["child_col"]}"::text = ANY($2::text[]))',
            _tids(ctids), ids,
        )
        if n:
            found.append({"table": e["child"], "column": e["child_col"], "rows": n})
    extra_users = await conn.fetchval(
        "SELECT count(*) FROM public.users WHERE ctid = ANY($1::tid[]) "
        "AND NOT (id::text = ANY($2::text[]))",
        _tids(plan.get(USERS_TABLE, [])), ids,
    )
    if extra_users:
        found.append({"table": USERS_TABLE, "column": "id", "rows": extra_users})
    return found


def delete_order(plan: dict[str, list[str]], edges: list[dict]) -> list[str]:
    """Children before parents over the planned tables (self-edges ignored;
    a residual cycle falls back to plan order, and the count check catches it)."""
    tables = set(plan)
    parents_of: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        if e["child"] in tables and e["parent"] in tables and e["child"] != e["parent"]:
            parents_of[e["child"]].add(e["parent"])
    children_of: dict[str, set[str]] = defaultdict(set)
    for c, ps in parents_of.items():
        for p in ps:
            children_of[p].add(c)
    order, remaining = [], set(tables)
    while remaining:
        leaves = sorted(t for t in remaining if not (children_of[t] & remaining))
        if not leaves:
            leaves = sorted(remaining)[:1]
        for t in leaves:
            order.append(t)
            remaining.discard(t)
    return order


class PurgeMismatch(RuntimeError):
    pass


async def purge(conn, plan: dict[str, list[str]]) -> dict[str, int]:
    """Delete the plan child-before-parent. Caller holds the transaction.
    Returns ``{table: rows deleted}``; raises :class:`PurgeMismatch` (caller
    rolls back) if any table deleted other than its planned count."""
    edges = await fk_edges(conn)
    counts: dict[str, int] = {}
    for table in delete_order(plan, edges):
        ctids = plan[table]
        status = await conn.execute(
            f"DELETE FROM {_ident(table)} WHERE ctid = ANY($1::tid[])", _tids(ctids)
        )
        n = int(status.rsplit(" ", 1)[-1])
        if n != len(ctids):
            raise PurgeMismatch(f"{table}: planned {len(ctids)}, deleted {n}")
        counts[table] = n
    return counts
