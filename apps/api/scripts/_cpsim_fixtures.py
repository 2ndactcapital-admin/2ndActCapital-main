"""cpsim02 — shared helpers for scripts that drive the simulator IN-PROCESS (the conformance runner, verify_cpsim02).

  TimeoutASGITransport   an ``httpx.ASGITransport`` that honours the request's own timeout (httpx's ASGITransport
                         ignores timeouts, so a simulator delay could never trip the client's timeout in-process):
                         the read timeout the CLIENT set becomes ``asyncio.wait_for`` → ``httpx.ReadTimeout``.
  mounted_app()          the simulator sub-app under ``/cpsim`` exactly as main.py mounts it (lighter than importing
                         main; the verify uses main.app itself).
  create_counterparty    a fixture Altruist-compatible counterparty (draft → active), its key returned ONCE in memory.
  plant_book             households / accounts / positions / transactions through the package's own accept().
  teardown(prefix)       every cpsim row of every counterparty whose slug starts with ``prefix`` (operator DSN: the
                         upload log is append-only and yields only to its owner with cpsim.allow_teardown = 'fixture').

Never prints a key, a token or a signature.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _p in (str(HERE), str(API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))

import httpx  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
#: every cpsim table that carries counterparty_id, children first (FK order for the teardown); cpsim03 + orders
CP_TABLES = ("cpsim.order_executions", "cpsim.orders", "cpsim.oauth_tokens", "cpsim.oauth_codes", "cpsim.book_transactions", "cpsim.book_positions",
             "cpsim.book_accounts", "cpsim.book_households", "cpsim.book_uploads", "cpsim.conformance_runs",
             "cpsim.request_log", "cpsim.counterparty_fix_sessions", "cpsim.counterparty_bank_details",
             "cpsim.counterparty_behaviour", "cpsim.counterparty_clock", "cpsim.counterparty_credentials")


#: cpsim03 fix 1 — the ONE place both verify_cpsim02 and verify_cpsim03 read cpsim_app's documented, deliberately
#: NARROWER grants from. Every other cpsim table: exactly SELECT / INSERT / UPDATE / DELETE. Each entry names its source.
CPSIM_APP_FULL = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
CPSIM_APP_WITHHELD = {
    "book_uploads": frozenset({"UPDATE", "DELETE"}),       # cpsim02: the upload log is append-only (SELECT + INSERT)
    "order_executions": frozenset({"UPDATE", "DELETE"}),   # docs/cpsim03_part1.sql: executions append-only
    "orders": frozenset({"DELETE"}),                       # docs/cpsim03_part1.sql: system-axis, never deleted
}


def cpsim_app_privileges(table: str) -> frozenset:
    """The EXACT relation privileges cpsim_app must hold on ``cpsim.<table>`` (no wildcard, nothing looser)."""
    return CPSIM_APP_FULL - CPSIM_APP_WITHHELD.get(table, frozenset())


class TimeoutASGITransport(httpx.ASGITransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        t = (request.extensions.get("timeout") or {}).get("read")
        if not t:
            return await super().handle_async_request(request)
        try:
            return await asyncio.wait_for(super().handle_async_request(request), timeout=t)
        except asyncio.TimeoutError as exc:
            raise httpx.ReadTimeout("in-process read timeout", request=request) from exc


def mounted_app():
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from cpsim.app import app as cpsim_app

    return Starlette(routes=[Mount("/cpsim", app=cpsim_app)])


async def create_counterparty(conn, *, name: str, slug: str) -> dict:
    """On a connection already AS cpsim_app inside a transaction. Returns {id, slug, key, client_id}."""
    from cpsim import registry as R

    made = await R.create(conn, name=name, institution_type="altruist", slug=slug, actor=None)
    await R.set_status(conn, made["id"], "active", None)
    client_id = await conn.fetchval("SELECT id::text FROM cpsim.counterparty_credentials WHERE counterparty_id = "
                                    "$1::uuid AND kind = 'api_key' AND rotated_at IS NULL AND revoked_at IS NULL",
                                    made["id"])
    return {"id": made["id"], "slug": made["slug"], "key": made["issued"]["api_key"], "client_id": client_id}


async def plant_book(conn, counterparty_id, book: dict) -> dict:
    """``book`` = {kind: [rows]} in households → accounts → positions → transactions order. Returns {kind: accept()}."""
    from cpsim import book as B

    out = {}
    for kind in B.KINDS:
        if book.get(kind):
            out[kind] = await B.accept(conn, counterparty_id, kind, book[kind], file_name=f"fixture_{kind}.json",
                                       sha256="0" * 64, fmt_="json", actor=None)
    return out


async def teardown(prefix: str) -> dict:
    """Delete every cpsim row of the counterparties whose slug starts with ``prefix``. Returns per-table counts."""
    from _db_connect import connect, resolve_dsn

    if not prefix or len(prefix) < 6 or "%" in prefix or "_" in prefix:
        raise ValueError("a teardown prefix must be a specific fixture slug prefix")
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the cpsim teardown: {how}")
    conn = await connect(dsn)
    counts: dict[str, int] = {}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('cpsim.allow_teardown', 'fixture', true)")
            ids = [r["id"] for r in await conn.fetch(
                "SELECT id FROM cpsim.counterparties WHERE slug LIKE $1 || '%'", prefix)]
            for t in CP_TABLES:
                r = await conn.execute(f"DELETE FROM {t} WHERE counterparty_id = ANY($1::uuid[])", ids)
                counts[t] = int(r.split()[-1])
            r = await conn.execute("DELETE FROM cpsim.request_log WHERE slug LIKE $1 || '%'", prefix)
            counts["cpsim.request_log(slug)"] = int(r.split()[-1])
            r = await conn.execute("DELETE FROM cpsim.counterparties WHERE archived_from_id = ANY($1::uuid[])", ids)
            counts["cpsim.counterparties(archived)"] = int(r.split()[-1])
            r = await conn.execute("DELETE FROM cpsim.counterparties WHERE id = ANY($1::uuid[])", ids)
            counts["cpsim.counterparties"] = int(r.split()[-1])
    finally:
        await conn.close()
    return counts


async def leftover(prefix: str) -> int:
    """Rows still owned by a fixture counterparty (0 after a clean teardown)."""
    from _db_connect import connect, resolve_dsn

    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(how)
    conn = await connect(dsn)
    try:
        n = await conn.fetchval("SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1 || '%'", prefix)
        n += await conn.fetchval("SELECT count(*) FROM cpsim.request_log WHERE slug LIKE $1 || '%'", prefix)
        return int(n)
    finally:
        await conn.close()
