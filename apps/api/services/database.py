"""Database access layer — RLS-aware connection pool (Sprint: RLS Phase 1).

Manages a lazily-initialized asyncpg connection pool sourced from the
``DATABASE_URL`` environment variable.

────────────────────────────────────────────────────────────────────────────
ROW-LEVEL SECURITY MECHANISM — PORTABLE, HOST-AGNOSTIC
────────────────────────────────────────────────────────────────────────────
Every connection handed out by this module runs inside an explicit
transaction whose FIRST statements are::

    SELECT set_config('app.current_org_id', <org uuid>, true),   -- SET LOCAL
           set_config('app.is_super_admin', 'true'|'false', true),
           set_config('app.current_auth0_sub', <raw sub>, true); -- RLS Phase 2

The third GUC, ``app.current_auth0_sub`` (RLS Phase 2), carries the raw Auth0
``sub`` claim so the ``users`` table's bootstrap policy can let a request read
and INSERT its OWN row (``auth0_sub = app.current_auth0_sub``) with NO org
context yet — a brand-new user's very first request. It is set FIRST in the
request lifecycle (see main.py), before org_id/role are resolved.

``set_config(..., is_local => true)`` is exactly ``SET LOCAL`` — the value is
scoped to the current transaction and is reset on COMMIT/ROLLBACK. Postgres
Row-Level Security policies read those values back with
``current_setting('app.current_org_id', true)`` (the pilot policy on
``trusted_contacts`` does exactly this). This whole scheme is **standard
Postgres** — it is portable to ANY Postgres host (RDS, Cloud SQL, self-hosted,
a different pooler) with no change to the policies or to the ContextVar
mechanism below.

Default-deny by construction: when no org context is set (e.g. the middleware
did not run), we set ``app.current_org_id`` to the empty string ``''``. RLS
policies must read it as ``NULLIF(current_setting('app.current_org_id', true),
'')::uuid`` (the pilot policy on ``trusted_contacts`` does), which maps ``''``
→ NULL → matches nothing → zero rows.

Why '' and NULLIF rather than "just leave it unset"? A custom "placeholder"
GUC (``app.*``) does NOT revert to NULL after a ``SET LOCAL`` commits — on a
pooled connection that is later reused it reverts to ``''`` (empty string).
A naive policy ``org_id = current_setting(...)::uuid`` would then raise
``invalid input syntax for type uuid: ""`` on that reused connection instead of
default-denying. Setting ``''`` explicitly every transaction (below) plus the
NULLIF guard in the policy makes the default-deny deterministic and error-free
regardless of connection reuse. We never set it to a real-but-fake UUID, so no
sentinel org id is ever assumed to be unused.

SET LOCAL (not plain SET) is also what makes this correct under a
transaction-mode pooler (pgBouncer): the setting cannot leak to the next
transaction that reuses the same backend, because it is torn down at
transaction end.

────────────────────────────────────────────────────────────────────────────
SUPABASE-SPECIFIC BITS — ISOLATED TO create_pool() BELOW
────────────────────────────────────────────────────────────────────────────
The ONLY Supabase-specific configuration lives in ``_create_asyncpg_pool()``:

  * ``ssl="require"``            — Supabase requires TLS.
  * ``statement_cache_size=0``  — required for Supabase's transaction-mode
                                  pooler (pgBouncer); prepared statements are
                                  not supported there.
  * the project-ref-qualified username in ``DATABASE_URL`` (e.g.
    ``postgres.<project-ref>``) — a Supabase pooler convention.

Migrating to a different Postgres host later should require changing ONLY that
function (SSL mode, cache setting, DSN). The RLS policies, the ContextVars, and
the acquire()/transaction wrapping in this file are host-agnostic and stay put.

────────────────────────────────────────────────────────────────────────────
NOTE — this sprint does NOT change the live connection. DATABASE_URL still
points at the original (RLS-bypassing) ``postgres`` role in every deployed
environment. The wrapping below sets the GUCs on every connection, but they
are inert while connected as a bypass role. Switching the app to the
non-bypass ``app_service`` role is a deliberate, separate, manual step.
────────────────────────────────────────────────────────────────────────────
"""

import os
from contextvars import ContextVar

import asyncpg

# ── Per-async-task RLS context ─────────────────────────────────────────────
# FastAPI runs each request in its own task, and the request middleware
# populates these before the route handler runs (see main.py). They are read
# at connection-acquire time and pushed to Postgres as SET LOCAL values.
#
# Defaults are the safe ones: no org (→ default-deny) and not a super admin.
_org_id_var: ContextVar[str | None] = ContextVar("rls_org_id", default=None)
_is_super_admin_var: ContextVar[bool] = ContextVar("rls_is_super_admin", default=False)

# The raw Auth0 ``sub`` claim (RLS Phase 2). This is set FIRST in the request
# lifecycle — before org_id/is_super_admin are resolved — so the self-lookup leg
# of the ``users`` RLS policy (``auth0_sub = app.current_auth0_sub``) lets a
# request read/insert its OWN users row during bootstrap, when the caller's
# org_id and role are not yet known. Kept independent of org context on purpose:
# the bootstrap read must succeed with NO org context at all.
_auth0_sub_var: ContextVar[str | None] = ContextVar("rls_auth0_sub", default=None)


def set_rls_context(org_id, is_super_admin: bool):
    """Set the current task's RLS context; returns tokens for :func:`reset_rls_context`.

    ``org_id`` may be None/empty (→ left unset → default-deny). Never pass an
    empty string through to Postgres — an unset GUC is what yields zero rows,
    whereas ``''::uuid`` would raise.
    """
    org_token = _org_id_var.set(str(org_id) if org_id else None)
    super_token = _is_super_admin_var.set(bool(is_super_admin))
    return (org_token, super_token)


def reset_rls_context(tokens) -> None:
    """Restore the RLS context to its prior value (call in a ``finally``)."""
    org_token, super_token = tokens
    _org_id_var.reset(org_token)
    _is_super_admin_var.reset(super_token)


def set_auth0_sub_context(auth0_sub):
    """Set the current task's raw Auth0 ``sub`` for the bootstrap RLS leg.

    Returns a token for :func:`reset_auth0_sub_context`. Set this FIRST in the
    request lifecycle (before org_id/is_super_admin are resolved) — see the
    ``_auth0_sub_var`` note above. A falsy ``auth0_sub`` is left unset (→ '' at
    apply time → the policy's NULLIF maps it to NULL → matches nothing).
    """
    return _auth0_sub_var.set(str(auth0_sub) if auth0_sub else None)


def reset_auth0_sub_context(token) -> None:
    """Restore the auth0_sub context to its prior value (call in a ``finally``)."""
    _auth0_sub_var.reset(token)


def current_rls_context() -> tuple[str | None, bool, str | None]:
    """Return ``(org_id, is_super_admin, auth0_sub)`` in effect — for diagnostics."""
    return (_org_id_var.get(), _is_super_admin_var.get(), _auth0_sub_var.get())


async def _apply_rls_settings(conn) -> None:
    """Emit the SET LOCAL statements for the connection's transaction.

    Called as the first thing inside every wrapped transaction. Reads the
    ContextVars, not any request object, so it is agnostic to how the context
    was established.
    """
    # Always set ALL THREE GUCs, deterministically, every transaction — so
    # context never leaks from a prior transaction on the same pooled backend.
    # No org in context → '' (the policy's NULLIF maps it to NULL → default-deny),
    # never a real-or-fake UUID; likewise no auth0_sub → '' → NULLIF → NULL, so
    # the bootstrap leg matches nothing rather than every unauthenticated row.
    # See the module docstring for why '' + NULLIF is correct.
    org_id = _org_id_var.get() or ""
    is_super = "true" if _is_super_admin_var.get() else "false"
    auth0_sub = _auth0_sub_var.get() or ""
    await conn.execute(
        "SELECT set_config('app.current_org_id', $1, true),"
        "       set_config('app.is_super_admin', $2, true),"
        "       set_config('app.current_auth0_sub', $3, true)",
        org_id,
        is_super,
        auth0_sub,
    )


# ── Wrapped acquire() — same call-site syntax, now transaction-scoped ───────
class _RLSAcquireContext:
    """Async context manager returned by ``pool.acquire()``.

    Enters an explicit transaction, applies the RLS SET LOCAL settings, and
    yields the raw asyncpg connection so existing call sites
    (``async with pool.acquire() as conn:``) are unchanged. Commits on clean
    exit, rolls back on exception. Any ``async with conn.transaction():`` a
    caller opens inside this block nests as a SAVEPOINT (asyncpg does this
    automatically), preserving that block's semantics.
    """

    __slots__ = ("_wrapper", "_conn", "_tr")

    def __init__(self, wrapper: "_RLSPool"):
        self._wrapper = wrapper
        self._conn = None
        self._tr = None

    async def __aenter__(self):
        conn = await self._wrapper._pool.acquire()
        try:
            tr = conn.transaction()
            await tr.start()
            await _apply_rls_settings(conn)
        except BaseException:
            # Release rolls back any started transaction (pool reset), and
            # guarantees we never leak the connection if start/settings fail.
            await self._wrapper._pool.release(conn)
            raise
        self._conn = conn
        self._tr = tr
        return conn

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                await self._tr.commit()
            else:
                await self._tr.rollback()
        finally:
            await self._wrapper._pool.release(self._conn)
            self._conn = None
            self._tr = None
        return False


class _RLSPool:
    """Thin wrapper over an asyncpg pool that makes every unit of work
    RLS-context-aware.

    * ``acquire()`` returns the transaction-scoped context manager above.
    * Pool-level shortcuts (``fetch``/``fetchrow``/``fetchval``/``fetchmany``/
      ``execute``/``executemany``) are also wrapped — they run inside their own
      RLS transaction — because several call sites use ``await pool.fetch(...)``
      directly instead of ``pool.acquire()``.
    * Everything else (``close``, ``terminate``, sizing, etc.) delegates to the
      underlying pool.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def acquire(self) -> _RLSAcquireContext:
        return _RLSAcquireContext(self)

    async def execute(self, *args, **kwargs):
        async with self.acquire() as conn:
            return await conn.execute(*args, **kwargs)

    async def executemany(self, *args, **kwargs):
        async with self.acquire() as conn:
            return await conn.executemany(*args, **kwargs)

    async def fetch(self, *args, **kwargs):
        async with self.acquire() as conn:
            return await conn.fetch(*args, **kwargs)

    async def fetchrow(self, *args, **kwargs):
        async with self.acquire() as conn:
            return await conn.fetchrow(*args, **kwargs)

    async def fetchval(self, *args, **kwargs):
        async with self.acquire() as conn:
            return await conn.fetchval(*args, **kwargs)

    async def fetchmany(self, *args, **kwargs):
        async with self.acquire() as conn:
            return await conn.fetchmany(*args, **kwargs)

    async def close(self) -> None:
        await self._pool.close()

    def __getattr__(self, name):
        # Delegate anything we did not override to the real pool.
        return getattr(self._pool, name)


_pool: _RLSPool | None = None


async def _create_asyncpg_pool(database_url: str) -> asyncpg.Pool:
    """Create the raw asyncpg pool.

    *** This is the ONLY Supabase-specific function. *** To move to a different
    Postgres host, change SSL mode / statement_cache_size / DSN here — nothing
    else in this module (or in the RLS policies) needs to change.
    """
    return await asyncpg.create_pool(
        dsn=database_url,
        ssl="require",              # Supabase requires TLS
        statement_cache_size=0,     # required for Supabase's pgBouncer pooler
        min_size=1,
        max_size=10,
    )


async def get_pool() -> _RLSPool:
    """Return the shared RLS-aware connection pool, creating it on first use.

    The returned object supports the same interface existing call sites use —
    ``async with pool.acquire() as conn:`` and the ``pool.fetch(...)`` shortcuts
    — but every unit of work now runs inside a transaction that first applies
    the current RLS context (org_id + is_super_admin) via SET LOCAL.
    """
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _pool = _RLSPool(await _create_asyncpg_pool(database_url))
    return _pool


async def close_pool() -> None:
    """Close the connection pool, if one was created."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
