"""_verify_cleanup — one shared fixture teardown for the verify_rep* scripts.

WHY
---
The rep2 / rep5 / rep6 / rep7 / rep7b teardowns used to finish with
``delete_tree(op, "public.users" | "public.roles" | "public.organizations", ...)``
and (rep6 / rep7 / rep7b) ``sweep_fixture_orgs``. ``delete_tree`` walks EVERY FK
child table recursively from pg_constraint — deals, deal_signatures,
spv_carry_run_lines, cost_events, ... — issuing nested
``DELETE ... WHERE (cols) IN (SELECT ... FROM parent WHERE (cols) IN (SELECT ...))``
whether or not the child holds a single fixture row, and ``sweep_fixture_orgs``
fell back to that walk for every table whenever ``fk_order`` met a cycle. One
teardown took ~25 minutes, so a script that runs another as its regression
timed out.

DESIGN
------
Everything happens in ONE transaction on the operator (postgres) connection,
with the caller's teardown GUCs set ``LOCAL`` (``set_config(name, 'fixture',
true)``) — the only path past the immutability triggers.

1. SEEDS — keyed only by THIS script's fixture ids: every table with an
   ``org_id`` column (``org_id = ANY(orgs)``), ``public.organizations``
   (``id = ANY(orgs)``), ``public.users`` (``id = ANY(users) OR auth0_sub LIKE
   pattern``) and every entry of the caller's ``plan`` (table -> (where_sql,
   args)). A seed enters the delete set only if ``EXISTS`` finds a matching row.
2. CLOSURE — pg_constraint (contype 'f') is read once. For every table T in the
   delete set and every FK whose PARENT is T, the DISTINCT referenced values of
   T's delete-set rows are MATERIALISED (one SELECT), and the child is probed
   with ``EXISTS (... cc = ANY($1::<parent col type>[]) AND NOT <already
   covered>)``. Only a child that really holds such rows gets the predicate
   (and is re-queued); the values already sent per (child, FK) are remembered,
   so the walk terminates. A child of a table with no fixture rows is never
   queried. No statement ever nests ``IN (SELECT ...)`` over a live table —
   every closure predicate is ``= ANY(materialised array)`` (multi-column FKs:
   ``(c1, c2) IN (SELECT * FROM unnest($1::t1[], $2::t2[]))``).
3. SAFETY — a predicate the closure derived (not a seed) must never reach a row
   outside the fixture orgs: on a table with ``org_id``, any newly reached row
   whose ``org_id`` is not a fixture org (NULL included) raises RuntimeError
   naming the table, the FK and the count. Nothing is deleted in that case (the
   transaction rolls back). Seeds are exempt — they are the script's own
   explicit fixture keys.
4. ORDER — child-before-parent among the delete-set tables (Kahn over
   pg_constraint, self-references ignored: one DELETE handles a self-FK). A
   cycle takes the next table from the caller's explicit ``cycle_order``; with
   none applicable it raises — there is no fallback to a recursive walk.
5. DELETE — each table exactly once: ``DELETE FROM t WHERE (p1) OR (p2) ...``.

``leftover_count`` is the matching read-only count, for ``--cleanup-only``.
Self-contained: asyncpg + stdlib only.
"""

from __future__ import annotations

import re
import time
from collections import deque

_ORG_TABLES_SQL = (
    "SELECT n.nspname AS s, c.relname AS r FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
    "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
    "'information_schema') ORDER BY 1, 2")

_FK_SQL = """
    SELECT c.conname,
           cn.nspname AS child_schema, cc.relname AS child_table,
           pn.nspname AS parent_schema, pc.relname AS parent_table,
           array_agg(ca.attname::text ORDER BY k.ord) AS child_cols,
           array_agg(pa.attname::text ORDER BY k.ord) AS parent_cols,
           array_agg(format_type(pa.atttypid, pa.atttypmod) ORDER BY k.ord) AS parent_types
    FROM pg_constraint c
    JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, ord) ON true
    JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
    JOIN pg_class pc ON pc.oid = c.confrelid JOIN pg_namespace pn ON pn.oid = pc.relnamespace
    JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = k.ck
    JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
    WHERE c.contype::text = 'f' AND c.conparentid = 0 AND NOT cc.relispartition
    GROUP BY c.oid, c.conname, cn.nspname, cc.relname, pn.nspname, pc.relname"""

_FK_CACHE: dict[str, list[dict]] = {}       # parent "schema.table" -> FKs (cached for the process)
_FK_LOADED = False
_PARAM = re.compile(r"\$(\d+)\b")


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qtable(name: str) -> str:
    """'schema.table' (catalog names) -> '"schema"."table"'."""
    schema, _, rel = name.partition(".")
    return f"{qident(schema)}.{qident(rel)}"


def _renumber(sql: str, offset: int) -> str:
    if not offset:
        return sql
    return _PARAM.sub(lambda m: f"${int(m.group(1)) + offset}", sql)


def _combine(preds: list[tuple[str, list]], offset: int = 0) -> tuple[str, list]:
    """[(sql, args), ...] -> '(p1) OR (p2) ...' with renumbered params, starting after ``offset``."""
    parts, args = [], []
    for sql, a in preds:
        parts.append(f"({_renumber(sql, offset + len(args))})")
        args.extend(a)
    return " OR ".join(parts), args


class _Counter:
    def __init__(self, op):
        self.op, self.n = op, 0

    async def fetch(self, sql, *a):
        self.n += 1
        return await self.op.fetch(sql, *a)

    async def fetchval(self, sql, *a):
        self.n += 1
        return await self.op.fetchval(sql, *a)

    async def execute(self, sql, *a):
        self.n += 1
        return await self.op.execute(sql, *a)


async def _load_fks(q: _Counter) -> None:
    global _FK_LOADED
    if _FK_LOADED:
        return
    for r in await q.fetch(_FK_SQL):
        fk = {"name": r["conname"],
              "child": f"{r['child_schema']}.{r['child_table']}",
              "parent": f"{r['parent_schema']}.{r['parent_table']}",
              "child_cols": list(r["child_cols"]), "parent_cols": list(r["parent_cols"]),
              "types": list(r["parent_types"])}
        _FK_CACHE.setdefault(fk["parent"], []).append(fk)
    _FK_LOADED = True


async def _org_tables(q: _Counter) -> list[str]:
    return [f"{r['s']}.{r['r']}" for r in await q.fetch(_ORG_TABLES_SQL)]


async def _resolve(q: _Counter, name: str) -> str | None:
    """A caller's 'schema.table' -> the catalog's own 'schema.table', or None if it does not exist."""
    r = await q.fetch("SELECT n.nspname AS s, c.relname AS r FROM pg_class c JOIN pg_namespace n ON "
                      "n.oid = c.relnamespace WHERE c.oid = to_regclass($1) AND c.relkind IN ('r', 'p')", name)
    return f"{r[0]['s']}.{r[0]['r']}" if r else None


def _users_pred(users, user_sub_like) -> tuple[str, list]:
    if user_sub_like:
        return "id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(users), user_sub_like]
    return "id = ANY($1::uuid[])", [list(users)]


def _order(tables: list[str], cycle_order) -> list[str]:
    """Child-before-parent among ``tables`` (Kahn; self-references ignored)."""
    tset = set(tables)
    parents = {t: set() for t in tables}               # t -> its parents inside the set
    for p, fks in _FK_CACHE.items():
        if p not in tset:
            continue
        for fk in fks:
            if fk["child"] in tset and fk["child"] != p:
                parents[fk["child"]].add(p)
    children = {t: {c for c, ps in parents.items() if t in ps} for t in tables}
    out, done = [], set()
    while len(out) < len(tables):
        ready = [t for t in tables if t not in done and children[t] <= done]
        if not ready:
            blocked = sorted(tset - done)
            pick = next((t for t in cycle_order if t in blocked), None)
            if pick is None:
                raise RuntimeError(f"FK cycle among {blocked} — give an explicit cycle_order")
            ready = [pick]
        for t in ready:
            out.append(t)
            done.add(t)
    return out


async def cleanup(op, *, orgs, users=(), user_sub_like=None, plan=None, gucs=(), cycle_order=(),
                  label="") -> dict:
    """Delete this script's fixture rows (see the module docstring). ``op`` is an asyncpg connection on the
    operator DSN. Returns {"log": ["table: n", ...], "seconds", "tables", "queries"}."""
    t0 = time.monotonic()
    q = _Counter(op)
    orgs = list(orgs)
    users = list(users)
    log: list[str] = []
    rows_total = 0
    async with op.transaction():
        for g in gucs:
            await q.execute("SELECT set_config($1, 'fixture', true)", g)
        await _load_fks(q)
        org_tables = await _org_tables(q)
        org_table_set = set(org_tables)

        preds: dict[str, list[tuple[str, list]]] = {}   # table -> [(sql, args)]

        async def seed(table: str, sql: str, args: list) -> None:
            if await q.fetchval(f"SELECT EXISTS (SELECT 1 FROM {qtable(table)} WHERE {sql})", *args):
                preds.setdefault(table, []).append((sql, list(args)))

        # (1) every org_id table, (2) organizations, (3) users, (4) the caller's plan
        for t in org_tables:
            await seed(t, "org_id = ANY($1::uuid[])", [orgs])
        await seed("public.organizations", "id = ANY($1::uuid[])", [orgs])
        if users or user_sub_like:
            await seed("public.users", *_users_pred(users, user_sub_like))
        for name, (where, args) in (plan or {}).items():
            t = await _resolve(q, name)
            if t is None:
                continue
            await seed(t, where, list(args))

        # closure: parent -> child over pg_constraint, values materialised, only children that hold rows
        sent: dict[tuple[str, str], set] = {}            # (child, fk name) -> values already added
        values: dict[tuple, list] = {}                   # (parent, cols, n preds) -> distinct referenced values
        queue = deque(sorted(preds))
        queued = set(queue)
        while queue:
            parent = queue.popleft()
            queued.discard(parent)
            for fk in _FK_CACHE.get(parent, ()):
                child = fk["child"]
                if (parent == "public.organizations" and fk["child_cols"] == ["org_id"]
                        and fk["parent_cols"] == ["id"] and child in org_table_set):
                    continue        # exactly the org_id seed, already probed for this child
                pcols = ", ".join(qident(c) for c in fk["parent_cols"])
                nn = " AND ".join(f"{qident(c)} IS NOT NULL" for c in fk["parent_cols"])
                vkey = (parent, tuple(fk["parent_cols"]), len(preds[parent]))   # stale once preds grow
                if vkey not in values:
                    where, args = _combine(preds[parent])
                    values[vkey] = await q.fetch(
                        f"SELECT DISTINCT {pcols} FROM {qtable(parent)} WHERE ({where}) AND {nn}", *args)
                vals = values[vkey]
                key = (child, fk["name"])
                seen = sent.setdefault(key, set())
                new = [tuple(r) for r in vals if tuple(r) not in seen]
                if not new:
                    continue
                if len(fk["child_cols"]) == 1:
                    pred = (f"{qident(fk['child_cols'][0])} = ANY($1::{fk['types'][0]}[])", [[v[0] for v in new]])
                else:
                    cols = ", ".join(qident(c) for c in fk["child_cols"])
                    arrs = ", ".join(f"${i + 1}::{ty}[]" for i, ty in enumerate(fk["types"]))
                    pred = (f"({cols}) IN (SELECT * FROM unnest({arrs}))",
                            [[v[i] for v in new] for i in range(len(fk["types"]))])
                seen.update(new)
                # only rows NOT already in the delete set count as "new"
                if child in preds:
                    cov, cov_args = _combine(preds[child], len(pred[1]))
                    probe = f"({pred[0]}) AND NOT COALESCE(({cov}), false)"
                    probe_args = pred[1] + cov_args
                else:
                    probe, probe_args = pred[0], pred[1]
                if not await q.fetchval(f"SELECT EXISTS (SELECT 1 FROM {qtable(child)} WHERE {probe})", *probe_args):
                    continue
                if child in org_table_set:
                    off = len(probe_args)
                    bad = await q.fetchval(
                        f"SELECT count(*) FROM {qtable(child)} WHERE ({probe}) AND "
                        f"COALESCE(org_id = ANY(${off + 1}::uuid[]), false) = false", *probe_args, orgs)
                    if bad:
                        raise RuntimeError(
                            f"cleanup{(' ' + label) if label else ''}: refusing to delete {bad} row(s) of {child} "
                            f"outside the fixture orgs, reached through FK {fk['name']} "
                            f"({', '.join(fk['child_cols'])}) -> {parent} ({', '.join(fk['parent_cols'])})")
                preds.setdefault(child, []).append(pred)
                if child not in queued:
                    queue.append(child)
                    queued.add(child)

        for t in _order(sorted(preds), tuple(cycle_order)):
            where, args = _combine(preds[t])
            status = await q.execute(f"DELETE FROM {qtable(t)} WHERE {where}", *args)
            n = int(status.rsplit(" ", 1)[-1])
            rows_total += n
            if n:
                log.append(f"{t}: {n}")
    seconds = time.monotonic() - t0
    print(f"[INFO] cleanup{(' ' + label) if label else ''}: {len(preds)} table(s), {rows_total} row(s), "
          f"{q.n} queries in {seconds:.1f} s", flush=True)
    return {"log": log, "seconds": seconds, "tables": len(preds), "queries": q.n}


async def leftover_count(op, *, orgs, users=(), user_sub_like=None, extra=()) -> int:
    """Read-only: rows under the fixture orgs in EVERY org_id table + the fixture organizations + matching users
    + each ``extra`` (sql, args) count query."""
    q = _Counter(op)
    orgs = list(orgs)
    n = 0
    for t in await _org_tables(q):
        n += int(await q.fetchval(f"SELECT count(*) FROM {qtable(t)} WHERE org_id = ANY($1::uuid[])", orgs))
    n += int(await q.fetchval("SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])", orgs))
    if users or user_sub_like:
        sql, args = _users_pred(users, user_sub_like)
        # users carries org_id too — count only the rows the org-table pass did not already count
        n += int(await q.fetchval(f"SELECT count(*) FROM public.users WHERE ({sql}) AND "
                                  f"COALESCE(org_id = ANY(${len(args) + 1}::uuid[]), false) = false", *args, orgs))
    for sql, args in extra:
        n += int(await q.fetchval(sql, *args))
    return n


# ── The chain checks (lotmethod) ────────────────────────────────────────────
#: main @ "merge rep7b" — the base the lotmethod sprint (BUGFIX2 B1 / B2) changed the verify scripts from
LOTMETHOD_BASE = "a5ffe9a"
#: the functions B1 rewrote or removed (the cleanup itself, --cleanup-only, the leftover reads)
B1_FUNCTIONS = frozenset({"fk_children", "delete_tree", "fk_order", "sweep_fixture_orgs", "org_tables", "teardown",
                          "teardown_retrying", "pre_teardown", "leftovers", "cleanup_only"})


def _fn_at(lines: list[str], i: int) -> str | None:
    for k in range(min(i, len(lines) - 1), -1, -1):
        m = re.match(r"(?:async )?def (\w+)", lines[k])
        if m:
            return m.group(1)
    return None


def _docstring_end(lines: list[str]) -> int:
    """Index of the line that closes the module docstring (-1 when none)."""
    opened = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not opened:
            if s.startswith(('"""', 'r"""')):
                if s.count('"""') >= 2 and len(s) > 3:
                    return i
                opened = True
            elif s and not s.startswith("#"):
                return -1
        elif '"""' in s:
            return i
    return -1


def b1_line_ok(lines: list[str], i: int) -> bool:
    """A changed line BUGFIX2 B1 / B2 accounts for: inside a cleanup function, the module docstring, the
    helper's import / cache, main_async's --cleanup-only hook, a nested regression's 1200 → 2700 s timeout."""
    s = lines[i].strip()
    fn = _fn_at(lines, i)
    if fn in B1_FUNCTIONS or "_FK_CACHE" in s:
        return True
    if fn is None and (i <= _docstring_end(lines) or s == "" or "_verify_cleanup" in s or "_FK_CACHE" in s):
        return True
    if fn == "main_async" and ("cleanup" in s or s == ""):
        return True
    if fn == "regression" and re.search(r"timeout=(1200|2700)\b", s):
        return True
    return s == ""


def b1_diff_bad(old_text: str, new_text: str) -> list[str]:
    """Every changed line between two versions of a verify script that B1 / B2 does NOT account for."""
    import difflib

    old, new = old_text.splitlines(), new_text.splitlines()
    bad = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        bad += [f"-{i + 1} {_fn_at(old, i)}: {old[i].strip()[:60]}" for i in range(i1, i2) if not b1_line_ok(old, i)]
        bad += [f"+{j + 1} {_fn_at(new, j)}: {new[j].strip()[:60]}" for j in range(j1, j2) if not b1_line_ok(new, j)]
    return bad
