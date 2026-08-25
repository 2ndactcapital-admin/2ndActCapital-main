"""ensureuseruuidfix verify — uuid_generate_v4() schema qualification.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent.
Teardown at START and at END, keyed on a stable marker.

THE PRODUCTION BUG THIS GATES
-----------------------------
Live Render log:

    ERROR in ensure_user (sub='auth0|6a7c8b473069946d5a6d5400'):
    function uuid_generate_v4() does not exist

``ensure_user`` never re-raises — it swallows the error and returns the
uuid5-derived ``get_user_id`` fallback. So the visible symptom was NOT a 500;
it was a caller silently running under an id that matches no ``users`` row,
i.e. "limited access" for every brand-new identity.

WHAT IS REAL HERE, AND WHAT IS NOT — stated up front so no assertion reads as
stronger than it is:

  * Every write runs against the LIVE database named by DATABASE_URL and every
    assertion reads the row back out of that database. Nothing is mocked.
  * THE SEARCH_PATH IS PINNED TO ``public`` for every probe connection. This is
    the whole point. Locally, DATABASE_URL authenticates as ``postgres``, whose
    rolconfig is ``search_path="$user", public, extensions`` — under that
    search_path a BARE uuid_generate_v4() resolves fine and every assertion
    below would pass vacuously, fix or no fix. Pinning ``search_path = public``
    reproduces the production role's resolution environment exactly.
  * Non-vacuity is asserted, not assumed: [4] executes the PRE-FIX bare SQL
    under the same pinned search_path and REQUIRES it to raise. If that probe
    ever passes, this script is not testing anything and says so.
  * The SQL executed in [8]/[9] is not retyped here — it is extracted verbatim
    from the real source files at runtime, so the thing proven is the shipped
    statement text.
  * ``fetch_auth0_identity`` is stubbed to return (None, None). That is the real
    behaviour when /userinfo is unreachable (an access token minted for a custom
    API audience carries no email claim), and it keeps the run offline and
    deterministic. The /userinfo network leg is NOT the subject of this sprint.

DSN:
  DATABASE_URL — bypass (postgres) role: seeding, probes, reads, teardown.
"""

import ast
import asyncio
import glob
import os
import re
import subprocess
import sys
from uuid import UUID

# ── Make runnable via allowlisted system python3 OR venv python ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

import asyncpg  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── stable ids / markers ────────────────────────────────────────────────────
MARKER = "ensureuseruuidfix_verify"
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

# The brand-new identity. Shaped like a real Auth0 sub; torn down at start so
# it is genuinely never-seen at the moment ensure_user is called.
NEW_SUB = f"auth0|{MARKER}_brandnew"
ADMIN_ID = "99000000-0000-0000-0000-0000ee1d0001"
ADMIN_SUB = f"auth0|{MARKER}_admin"
ADMIN_EMAIL = f"admin.{MARKER}@example.com"
INVITEE_EMAIL = f"invitee.{MARKER}@example.com"
S19_SUB = f"auth0|{MARKER}_s19"
S19_EMAIL = f"{S19_SUB}@placeholder.local"
SAM_SUB = f"auth0|{MARKER}_sam"
SAM_EMAIL = f"{SAM_SUB}@placeholder.local"
RAW_SUB = f"auth0|{MARKER}_rawsql"
RAW_EMAIL = f"raw.{MARKER}@example.com"
DEFAULTS_SUB = f"auth0|{MARKER}_defaults"
DEFAULTS_EMAIL = f"defaults.{MARKER}@example.com"
ASSET_NAME = f"asset.{MARKER}"

TEST_SUBS = [NEW_SUB, ADMIN_SUB, S19_SUB, SAM_SUB, RAW_SUB, DEFAULTS_SUB]
TEST_EMAILS = [
    ADMIN_EMAIL, INVITEE_EMAIL, S19_EMAIL, SAM_EMAIL, RAW_EMAIL, DEFAULTS_EMAIL,
    f"{NEW_SUB}@placeholder.local",
]

HW_ISSUER_DOMAIN = "dev-gy85vzuf6mruzv3j.us.auth0.com"
HW_ISSUER = f"https://{HW_ISSUER_DOMAIN}/"

# The four literal call sites Task 1b found. Every one must be proven, not just
# ensure_user's.
CALL_SITES = [
    ("services/users.py", "the production break — ensure_user's INSERT"),
    ("services/invites.py", "admin invite creation — same latent break"),
    ("scripts/verify_sprint19.py", "verify-script INSERT"),
    ("scripts/verify_superadminmenu.py", "verify-script INSERT"),
]

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def check(name, condition, detail=""):
    (ok if condition else fail)(name, detail)
    return bool(condition)


def report(line):
    print(f"       {line}")


def section(title):
    print(f"\n── {title} " + "─" * max(0, 72 - len(title)))


# ── request shim ────────────────────────────────────────────────────────────
class _State:
    def __init__(self, user):
        self.user = user


class FakeRequest:
    """The minimal Request surface ensure_user / get_org_id actually touch."""

    def __init__(self, claims, token="fake-access-token"):
        self.state = _State(claims)
        self.headers = {"Authorization": f"Bearer {token}"}


def hollisworks_claims(sub, **extra):
    claims = {"sub": sub, "iss": HW_ISSUER, "aud": "https://api.hollisworks.com"}
    claims.update(extra)
    return claims


# ── source extraction ───────────────────────────────────────────────────────
def _source_path(rel):
    return os.path.join(_API_ROOT, rel)


def read_source(rel):
    with open(_source_path(rel), "r", encoding="utf-8") as fh:
        return fh.read()


def extract_sql_block(rel, needle="extensions.uuid_generate_v4()"):
    """Return the triple-quoted SQL literal in `rel` that contains `needle`.

    Extracted verbatim from the real file so [4]/[8] execute the SHIPPED
    statement text, not a retyped copy that could drift from it. The block must
    contain INSERT INTO — several of these files also *describe* the function in
    prose (module docstrings), and matching the first occurrence would grab the
    docstring instead of the statement.
    """
    for match in re.finditer(r'"""(.*?)"""', read_source(rel), re.S):
        block = match.group(1)
        if needle in block and "INSERT INTO" in block.upper():
            return block
    raise LookupError(f"no INSERT block containing {needle!r} in {rel}")


def scan_literal_calls():
    """Every uuid_generate_v4 reference in apps/api *SQL string literals*.

    Uses AST rather than grep so that prose — module/function docstrings that
    describe the bug, including this fix's own explanatory comments — is not
    counted as a call site. Returns (qualified, bare) as (rel, lineno[, text]).
    """
    qualified, bare = [], []
    self_rel = os.path.join("scripts", os.path.basename(__file__))
    for root, dirs, files in os.walk(_API_ROOT):
        dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", "node_modules")]
        for name in files:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, name), _API_ROOT)
            if rel == self_rel:
                continue  # this file quotes the pre-fix bug text on purpose
            source = read_source(rel)
            if "uuid_generate_v4" not in source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            docstrings = set()
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if not isinstance(node, (ast.Module, ast.FunctionDef,
                                         ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstrings.add(id(body[0].value))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings or "uuid_generate_v4" not in node.value:
                    continue
                stripped = node.value.replace("extensions.uuid_generate_v4", "")
                if "uuid_generate_v4" in stripped:
                    bare.append((rel.replace(os.sep, "/"), node.lineno,
                                 node.value.strip().splitlines()[0][:80]))
                else:
                    qualified.append((rel.replace(os.sep, "/"), node.lineno))
    return qualified, bare


async def _connect(dsn):
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    # DATABASE_URL goes through PgBouncer. A session-level SET leaks onto the
    # pooled SERVER connection and outlives the client that issued it, so a
    # previous run could hand us a session already pinned. Start from the role
    # default, and never issue a session-level SET below — only SET LOCAL.
    await conn.execute("RESET search_path")
    return conn


class pinned:
    """Run a block with `search_path` pinned, TRANSACTION-SCOPED.

    ``SET LOCAL`` reverts at COMMIT/ROLLBACK, so nothing leaks onto the pooled
    server connection. ``public`` is the production role's search_path; it is
    what makes every probe below non-vacuous.
    """

    def __init__(self, conn, path="public", commit=True):
        self._conn = conn
        self._path = path
        self._commit = commit
        self._tr = None

    async def __aenter__(self):
        self._tr = self._conn.transaction()
        await self._tr.start()
        await self._conn.execute(f"SET LOCAL search_path = {self._path}")
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None or not self._commit:
            await self._tr.rollback()
        else:
            await self._tr.commit()
        return False


async def _probe(conn, coro_fn):
    """Run one statement inside a savepoint. Returns (value, exception)."""
    await conn.execute("SAVEPOINT sp")
    try:
        value = await coro_fn()
        await conn.execute("RELEASE SAVEPOINT sp")
        return value, None
    except Exception as exc:  # noqa: BLE001 — the exception IS the assertion
        await conn.execute("ROLLBACK TO SAVEPOINT sp")
        return None, exc


def _is_undefined_function(exc):
    return isinstance(exc, asyncpg.exceptions.UndefinedFunctionError) and (
        "uuid_generate_v4" in str(exc)
    )


# ── teardown ────────────────────────────────────────────────────────────────
async def _teardown(conn):
    await conn.execute(
        "DELETE FROM user_roles WHERE user_id IN "
        "(SELECT id FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[]))",
        TEST_SUBS, TEST_EMAILS,
    )
    await conn.execute(
        "UPDATE users SET invited_by = NULL WHERE invited_by IN "
        "(SELECT id FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[]))",
        TEST_SUBS, TEST_EMAILS,
    )
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[]) OR id = $3",
        TEST_SUBS, TEST_EMAILS, ADMIN_ID,
    )
    await conn.execute("DELETE FROM portfolio.assets WHERE name = $1", ASSET_NAME)


async def _leftover_count(conn):
    users = await conn.fetchval(
        "SELECT count(*) FROM users WHERE auth0_sub = ANY($1::text[]) "
        "OR email = ANY($2::text[]) OR id = $3",
        TEST_SUBS, TEST_EMAILS, ADMIN_ID,
    )
    assets = await conn.fetchval(
        "SELECT count(*) FROM portfolio.assets WHERE name = $1", ASSET_NAME
    )
    return users, assets


# ════════════════════════════════════════════════════════════════════════════
async def main():
    if not DATABASE_URL:
        fail("env", "DATABASE_URL is not set — cannot run against a real database")
        return 1

    conn = await _connect(DATABASE_URL)
    probe = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)

        # ── [1] TASK 1a — how ensure_user generates the id ──────────────────
        section("[1] Task 1a — how ensure_user's INSERT generates the new row's id")
        insert_sql = extract_sql_block("services/users.py")
        report("services/users.py :: ensure_user, step 3 INSERT (verbatim from source):")
        for line in insert_sql.strip().splitlines():
            report("  | " + line.strip())
        supplies_id_literally = bool(
            re.search(r"INSERT INTO users \(\s*id\s*,", insert_sql)
            and re.search(r"VALUES\s*\(\s*extensions\.uuid_generate_v4\(\)", insert_sql)
        )
        report("")
        report("FINDING 1a: ensure_user calls the function as LITERAL SQL TEXT. It")
        report("  names `id` in the column list and supplies uuid_generate_v4()")
        report("  explicitly in VALUES. It does NOT omit `id` and fall back to the")
        report("  users.id column DEFAULT. Literal SQL text is name-resolved at")
        report("  PARSE time against the SESSION's search_path — which is why it")
        report("  broke while 107 DEFAULT-driven tables kept working.")
        check(
            "[1] Task 1a reported: ensure_user supplies id as literal SQL, now schema-qualified",
            supplies_id_literally,
            "INSERT INTO users (id, ...) VALUES (extensions.uuid_generate_v4(), ...)",
        )

        # ── [2] TASK 1b — every literal call site ───────────────────────────
        section("[2] Task 1b — every literal uuid_generate_v4() call site in apps/api")
        qualified, bare = scan_literal_calls()
        report("FINDING 1b: NOT isolated to ensure_user. Four live literal call sites:")
        for rel, why in CALL_SITES:
            hits = sorted(ln for r, ln in qualified if r == rel)
            report(f"  {rel}:{hits} — {why}")
        others = sorted({r for r, _ in qualified} - {rel for rel, _ in CALL_SITES})
        report(f"  also updated for accuracy (non-executing text): {others or 'none'}")
        report("  prose-only references (docstrings) are excluded by the AST scan:")
        report("    services/users.py module docstring, services/securities_global.py:565")
        report(f"  ({len(qualified)} qualified SQL literals now; {len(bare)} bare remaining)")
        for rel, lineno, line in bare:
            report(f"  BARE STILL PRESENT -> {rel}:{lineno}: {line}")
        check(
            "[2] Task 1b reported: all four live call sites enumerated",
            len(CALL_SITES) == 4
            and all(any(r == rel for r, _ in qualified) for rel, _ in CALL_SITES),
            f"{len(CALL_SITES)} sites, all now schema-qualified",
        )
        check(
            "[2] Task 2 fix: zero bare uuid_generate_v4() calls in apps/api SQL literals",
            not bare,
            "AST scan of every string literal (docstrings excluded) finds none",
        )

        # ── [3] TASK 1c — why the DEFAULT works and the literal does not ────
        section("[3] Task 1c — why table DEFAULTs survive and literal SQL does not")
        locations = [
            r["nspname"] for r in await conn.fetch(
                "SELECT n.nspname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.proname = 'uuid_generate_v4'"
            )
        ]
        GET_USERS_DEFAULT = (
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='users' AND column_name='id'"
        )
        GET_DEFAULT_EXPR = """
            SELECT pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attrdef d JOIN pg_attribute a
              ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE d.adrelid = 'public.users'::regclass AND a.attname = 'id'
        """
        # Read the SAME stored default under two explicitly-set search_paths.
        # pg_get_expr DEPARSES the stored OID, so what it prints is a function
        # of the READER's search_path — which is itself the proof that the
        # stored form carries no schema name at all.
        async with pinned(probe, "public, extensions", commit=False):
            users_default = await probe.fetchval(GET_USERS_DEFAULT)
            pretty_visible = await probe.fetchval(GET_DEFAULT_EXPR)
        async with pinned(probe, "public", commit=False):
            pretty_hidden = await probe.fetchval(GET_DEFAULT_EXPR)
        raw_default = await conn.fetchval("""
            SELECT d.adbin::text FROM pg_attrdef d JOIN pg_attribute a
              ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE d.adrelid = 'public.users'::regclass AND a.attname = 'id'
        """)
        func_oid = await conn.fetchval(
            "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE p.proname='uuid_generate_v4' AND n.nspname='extensions'"
        )
        default_count = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE column_name='id' AND column_default LIKE '%uuid_generate_v4%'"
        )
        report(f"uuid_generate_v4 exists in schema(s)        : {locations}")
        report(f"users.id column_default (as text)           : {users_default}")
        report(f"raw stored parse tree                       : {raw_default}")
        report(f"pg_get_expr, search_path=public,extensions  : {pretty_visible}")
        report(f"pg_get_expr, search_path=public (production): {pretty_hidden}")
        report(f"extensions.uuid_generate_v4 OID     : {func_oid}")
        report(f"tables whose id DEFAULTs to it      : {default_count}")
        report("")
        report("FINDING 1c: users.id DOES default to uuid_generate_v4() — and that")
        report("  default works fine. THE EXACT REASON the two behave differently:")
        report("  a column DEFAULT is parsed and NAME-RESOLVED ONCE at DDL time and")
        report("  stored as a parse tree carrying the function's OID")
        report(f"  (:funcid {func_oid}) — no schema name, no lookup, so it never")
        report("  consults search_path at runtime. Literal SQL text in a statement")
        report("  is re-resolved on EVERY parse against the SESSION's search_path.")
        report("  The app role's search_path is \"$user\", public — which does not")
        report("  contain `extensions`, the only schema holding uuid_generate_v4.")
        report("  Hence: 107 DEFAULT-driven tables insert fine, ensure_user's")
        report("  literal call raises `function uuid_generate_v4() does not exist`.")
        report("  (It never reproduced in dev because DATABASE_URL authenticates as")
        report("  `postgres`, whose rolconfig ADDS extensions to search_path.)")
        check(
            "[3] Task 1c reported: uuid_generate_v4 exists ONLY in `extensions`",
            locations == ["extensions"],
            f"pg_proc locations = {locations}",
        )
        check(
            "[3] Task 1c reported: users.id DEFAULT is stored OID-resolved, not as text",
            raw_default is not None and f":funcid {func_oid}" in raw_default,
            f"adbin holds :funcid {func_oid} — no schema name, so the DEFAULT never "
            f"consults search_path",
        )
        check(
            "[3] Task 1c reported: the same stored DEFAULT deparses differently per reader",
            pretty_visible == "uuid_generate_v4()"
            and pretty_hidden == "extensions.uuid_generate_v4()",
            f"extensions visible -> {pretty_visible!r}; hidden -> {pretty_hidden!r} "
            f"— the schema name is the reader's rendering, never the stored value",
        )

        # ── [4] NON-VACUITY: pin search_path, prove the old SQL still fails ──
        section("[4] Non-vacuity — pin search_path to production's, replay the pre-fix SQL")
        report(f"role default search_path (dev, `postgres`): "
               f"{await conn.fetchval('show search_path')} — extensions PRESENT, "
               f"which is why this never reproduced locally")
        pre_fix_sql = insert_sql.replace(
            "extensions.uuid_generate_v4()", "uuid_generate_v4()"
        )
        async with pinned(probe, "public", commit=False):
            report(f"probe search_path pinned to: "
                   f"{await probe.fetchval('show search_path')} (production shape)")
            bare_val, bare_exc = await _probe(
                probe, lambda: probe.fetchval("SELECT uuid_generate_v4()"))
            check(
                "[4] pre-fix bare uuid_generate_v4() STILL raises under the pinned search_path",
                _is_undefined_function(bare_exc),
                f"{type(bare_exc).__name__}: {str(bare_exc).splitlines()[0]}" if bare_exc
                else f"NO ERROR (got {bare_val}) — this script would be vacuous",
            )
            _, pre_exc = await _probe(probe, lambda: probe.fetchrow(
                pre_fix_sql, DEFAULT_ORG, RAW_EMAIL, "Pre-fix", RAW_SUB, "member"))
            check(
                "[4] pre-fix ensure_user INSERT reproduces the exact production error",
                _is_undefined_function(pre_exc),
                f"{type(pre_exc).__name__}: {str(pre_exc).splitlines()[0]}" if pre_exc
                else "NO ERROR — the bug is not reproduced, assertions below are vacuous",
            )
            qual_val, qual_exc = await _probe(
                probe, lambda: probe.fetchval("SELECT extensions.uuid_generate_v4()"))
            check(
                "[4] schema-qualified call succeeds under the same pinned search_path",
                qual_exc is None and _as_uuid(qual_val) is not None,
                f"-> {qual_val}",
            )

        # ── [5] TASK 3 — a real ensure_user for a brand-new sub ─────────────
        section("[5] Task 3 — real ensure_user, brand-new auth0_sub, production search_path")
        import services.users as su  # noqa: E402

        su.fetch_auth0_identity = _stub_identity

        pre_existing = await conn.fetchval(
            "SELECT count(*) FROM users WHERE auth0_sub = $1", NEW_SUB)
        check(
            "[5] the sub is genuinely never-seen before the call",
            pre_existing == 0,
            f"rows for {NEW_SUB} = {pre_existing}",
        )

        request = FakeRequest(hollisworks_claims(NEW_SUB))
        fallback_id = su.get_user_id(request)

        live = await _connect(DATABASE_URL)
        try:
            # Committed, so the independent `conn` below can read the row back —
            # a same-connection read would not prove durability.
            async with pinned(live, "public"):
                report(f"ensure_user connection search_path = "
                       f"{await live.fetchval('show search_path')} (production shape)")
                returned = await su.ensure_user(live, request)
            report(f"ensure_user returned         : {returned}")
            report(f"get_user_id fallback would be: {fallback_id}")

            returned_uuid = _as_uuid(returned)
            check(
                "[5] ensure_user returns a real, valid UUID",
                returned_uuid is not None,
                f"{returned}",
            )
            # ensure_user swallows every exception and returns the uuid5
            # fallback. Asserting "it returned something" would therefore pass
            # against the BROKEN code too. The id must not BE that fallback.
            check(
                "[5] the id is DB-minted, not the swallowed-error uuid5 fallback",
                returned_uuid is not None and returned_uuid != fallback_id,
                f"returned {returned} != fallback {fallback_id}",
            )
            check(
                "[5] the DB-minted id is a v4 UUID (uuid_generate_v4 output)",
                returned_uuid is not None and UUID(returned_uuid).version == 4,
                f"version = {UUID(returned_uuid).version if returned_uuid else 'n/a'}",
            )

            # ── [6] TASK 3 — the row is findable by that same auth0_sub ─────
            section("[6] Task 3 — the created row is findable afterward by the same auth0_sub")
            row = await conn.fetchrow(
                "SELECT id, org_id, email, full_name, role, auth0_sub "
                "FROM users WHERE auth0_sub = $1", NEW_SUB)
            check(
                "[6] a row is findable by that auth0_sub, on an INDEPENDENT connection",
                row is not None,
                f"auth0_sub = {NEW_SUB}",
            )
            if row:
                report(f"  id        = {row['id']}")
                report(f"  org_id    = {row['org_id']}")
                report(f"  email     = {row['email']}")
                report(f"  role      = {row['role']}")
                check(
                    "[6] the persisted row's id is exactly what ensure_user returned",
                    str(row["id"]) == returned_uuid,
                    f"{row['id']} == {returned}",
                )
                async with pinned(live, "public"):
                    again = await su.ensure_user(
                        live, FakeRequest(hollisworks_claims(NEW_SUB)))
                check(
                    "[6] re-resolving the same sub returns the same id (idempotent)",
                    again == returned,
                    f"second call -> {again}",
                )
                dupes = await conn.fetchval(
                    "SELECT count(*) FROM users WHERE auth0_sub = $1", NEW_SUB)
                check("[6] exactly one row for the sub", dupes == 1, f"count = {dupes}")
        finally:
            await live.close()

        # ── [7] Task 1b site 2 — invites.create_invite, proven individually ──
        section("[7] Task 1b site 2 — services/invites.py::create_invite (real call)")
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, $3, 'Invite Admin', $4, 'admin')
            ON CONFLICT (id) DO NOTHING
            """,
            ADMIN_ID, DEFAULT_ORG, ADMIN_EMAIL, ADMIN_SUB,
        )
        from services.invites import create_invite  # noqa: E402

        inv_conn = await _connect(DATABASE_URL)
        try:
            async with pinned(inv_conn, "public"):
                invite = await create_invite(
                    inv_conn,
                    org_id=DEFAULT_ORG,
                    email=INVITEE_EMAIL,
                    full_name="Invitee",
                    role="member",
                    invited_by=ADMIN_ID,
                )
            invite_id = _as_uuid(invite["id"]) if invite else None
            report(f"create_invite returned id = {invite_id}")
            check(
                "[7] create_invite succeeds under the production search_path",
                invite_id is not None and UUID(invite_id).version == 4,
                f"v4 id {invite_id}",
            )
            found = await conn.fetchval(
                "SELECT id FROM users WHERE email = $1 AND invite_status = 'pending'",
                INVITEE_EMAIL,
            )
            check(
                "[7] the invite row is persisted and findable",
                found is not None and str(found) == invite_id,
                f"{found}",
            )
        finally:
            await inv_conn.close()

        # ── [8] Task 1b sites 3 & 4 — verify-script SQL, proven individually ─
        section("[8] Task 1b sites 3 & 4 — verify-script INSERTs (shipped text, executed)")
        script_sites = [
            ("scripts/verify_sprint19.py", (DEFAULT_ORG, S19_EMAIL, S19_SUB)),
            ("scripts/verify_superadminmenu.py", (DEFAULT_ORG, SAM_EMAIL, SAM_SUB)),
        ]
        for rel, params in script_sites:
            sql = extract_sql_block(rel)
            site_conn = await _connect(DATABASE_URL)
            try:
                async with pinned(site_conn, "public"):
                    if "RETURNING" in sql.upper():
                        value = await site_conn.fetchval(sql, *params)
                    else:
                        await site_conn.execute(sql, *params)
                        value = await site_conn.fetchval(
                            "SELECT id FROM users WHERE auth0_sub = $1", params[2])
                minted = _as_uuid(value)
                check(
                    f"[8] {rel} INSERT succeeds under the production search_path",
                    minted is not None and UUID(minted).version == 4,
                    f"minted {minted}",
                )
                # And the pre-fix form of the SAME statement must still fail.
                pf_conn = await _connect(DATABASE_URL)
                try:
                    async with pinned(pf_conn, "public", commit=False):
                        _, exc = await _probe(pf_conn, lambda: pf_conn.execute(
                            sql.replace(
                                "extensions.uuid_generate_v4()", "uuid_generate_v4()"),
                            *params))
                finally:
                    await pf_conn.close()
                check(
                    f"[8] {rel} pre-fix form still fails (assertion is not vacuous)",
                    _is_undefined_function(exc),
                    f"{type(exc).__name__}" if exc else "NO ERROR",
                )
            finally:
                await site_conn.close()

        # ── [9] No regression: table-level DEFAULTs untouched and still work ─
        section("[9] No regression — table-level DEFAULTs untouched and still working")
        reg_conn = await _connect(DATABASE_URL)
        try:
            async with pinned(reg_conn, "public"):
                default_user = await reg_conn.fetchval(
                    """
                    INSERT INTO users (org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, 'Defaults Probe', $3, 'member')
                    RETURNING id
                    """,
                    DEFAULT_ORG, DEFAULTS_EMAIL, DEFAULTS_SUB,
                )
                asset_id = await reg_conn.fetchval(
                    """
                    INSERT INTO portfolio.assets (org_id, name, asset_type)
                    VALUES ($1, $2, 'equity')
                    RETURNING id
                    """,
                    DEFAULT_ORG, ASSET_NAME,
                )
            check(
                "[9] users INSERT relying on the column DEFAULT still mints an id",
                _as_uuid(default_user) is not None,
                f"id = {default_user} (no literal call in this statement)",
            )
            check(
                "[9] portfolio.assets INSERT relying on its DEFAULT still mints an id",
                _as_uuid(asset_id) is not None,
                f"id = {asset_id}",
            )
        finally:
            await reg_conn.close()

        still_defaulting = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE column_name='id' AND column_default LIKE '%uuid_generate_v4%'"
        )
        check(
            "[9] all table-level DEFAULTs are unchanged by this fix",
            still_defaulting == default_count and default_count > 0,
            f"{still_defaulting} columns still default to uuid_generate_v4() — no DDL was run",
        )
        sql_diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", "apps/api/migrations"],
            cwd=_REPO_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        check(
            "[9] no migration / DDL file was modified (DEFAULT clauses left alone)",
            sql_diff == "",
            "git diff over apps/api/migrations is empty" if sql_diff == ""
            else f"modified: {sql_diff}",
        )

        # ── [10] Teardown ───────────────────────────────────────────────────
        section("[10] Teardown")
        await _teardown(conn)
        users_left, assets_left = await _leftover_count(conn)
        check(
            "[10] teardown leaves zero rows",
            users_left == 0 and assets_left == 0,
            f"users={users_left}, portfolio.assets={assets_left}",
        )

    finally:
        try:
            await _teardown(conn)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] final teardown: {exc}")
        await probe.close()
        await conn.close()

    # ── summary ─────────────────────────────────────────────────────────────
    passed = sum(1 for s, _, _ in _RESULTS if s == "PASS")
    failed = sum(1 for s, _, _ in _RESULTS if s == "FAIL")
    print("\n" + "=" * 78)
    print(f"ensureuseruuidfix verify: {passed} passed, {failed} failed "
          f"({passed}/{passed + failed})")
    if failed:
        for status, name, detail in _RESULTS:
            if status == "FAIL":
                print(f"  FAIL {name} — {detail}")
    print("=" * 78)
    return 1 if failed else 0


def _as_uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


async def _stub_identity(request, claims):
    """Stand in for the /userinfo call — offline, deterministic, no email claim."""
    return None, None


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
