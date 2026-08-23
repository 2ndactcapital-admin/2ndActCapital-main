"""Verification — underlying normalization, the closed-set index matcher, the
proposal pipeline and the human-gated confirm.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback. If that
credential does not connect, this script FAILS loudly rather than quietly
"verifying" a permission gate under a role that bypasses it.

THIS SCRIPT ASSERTS AGAINST THE REAL CORPUS, NOT A FIXTURE
──────────────────────────────────────────────────────────────────────────────
Every normalization pair below is a verbatim string pulled from
portfolio.securities_global_relationships. Every proposal count is measured by
running the real pipeline over the real 97 edges. A synthetic fixture would
prove the normalizer handles the noise somebody imagined; these strings are the
noise that is actually there, including two patterns the sprint brief did not
list (trailing ticker glosses, and spaces stranded in front of punctuation by
the mark characters).

WHAT TEARDOWN DOES AND DELIBERATELY DOES NOT DO
──────────────────────────────────────────────────────────────────────────────
Restores, at start and at end:
  * the two seeded test users;
  * the FULL prior state of every edge this script mutated in the confirm and
    reject tests — snapshotted before the test, written back after, so a real
    production edge is not left resolved by a test run;
  * any securities_global row the create_new test made.

Does NOT undo:
  * the index rows the registry creates, or the proposals the full-corpus pass
    writes. Those are the sprint's intended output, they are idempotent, and
    they are re-derivable from a single function call. Deleting them so the
    script "leaves no trace" would mean the verified pipeline had never actually
    run against production — which is the one thing this sprint needed to prove.

Run:
    python3 scripts/verify_underlyingresolution.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.append(
    "/mnt/c/Users/Joe/2ndActCapital/apps/api/venv/lib/python3.12/site-packages"
)

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    override=False,
)

from services.underlying_index_registry import (  # noqa: E402
    KNOWN_INDICES,
    lookup_index,
    resolve_or_create_index_security,
)
from services.underlying_normalization import (  # noqa: E402
    normalization_key,
    normalize_underlying_text,
)
from services.underlying_resolution import (  # noqa: E402
    UnderlyingResolutionError,
    UnderlyingResolutionPermissionError,
    confirm_resolution,
    load_queue,
    propose_all_unresolved,
    propose_resolution,
    reject_proposal,
)

REL_TABLE = "portfolio.securities_global_relationships"
SEC_TABLE = "portfolio.securities_global"
IDENT_TABLE = "portfolio.securities_global_identifiers"

ADMIN_USER_ID = "99000000-0000-0000-0000-000000000021"
ADMIN_SUB = "auth0|verify_underlying_super_admin"
MEMBER_USER_ID = "99000000-0000-0000-0000-000000000022"
MEMBER_SUB = "auth0|verify_underlying_member"
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

# A securities_global name no prospectus will ever produce, used for the
# create_new path so teardown can delete it by exact match.
FIXTURE_SECURITY_NAME = "VERIFY underlyingresolution fixture security"

ROOT = "/mnt/c/Users/Joe/2ndActCapital"

# The five index families the sprint brief names. Used for the coverage
# assertion — matched on the NORMALIZED name, so the assertion is about
# securities and not about spellings.
INDEX_FAMILY_NAMES = {
    "S&P 500 Index",
    "Russell 2000 Index",
    "Nasdaq-100 Index",
    "Dow Jones Industrial Average",
    "EURO STOXX 50 Index",
}

# ── Real duplicate pairs, verbatim from the database ─────────────────────────
#
# Each tuple must normalize to one string. Chosen to exercise a different noise
# pattern each: leading article + spaced ®; embedded ® with no spaces; ® before
# vs. after the word 'Index'; a service mark written 'SM' where a sibling writes
# '®'; and a trailing parenthetical ticker gloss.
REAL_DUPLICATE_PAIRS = [
    ("S&P 500 ® Index", "the S&P 500 ® Index"),
    ("S&P 500 ® Index", "the S&P 500® Index"),
    ("Russell 2000 ® Index", "the Russell 2000 ® Index"),
    ("Russell 2000 ® Index", "Russell 2000® Index"),
    ("Nasdaq-100 ® Index", "Nasdaq-100 Index ®"),
    ("Nasdaq-100 ® Index", "the Nasdaq-100 Index®"),
    ("Dow Jones Industrial Average SM", "the Dow Jones Industrial Average®"),
    ("Dow Jones Industrial Average ®", "Dow Jones Industrial Average®"),
    ("EURO STOXX 50 ® Index", "the EURO STOXX 50 ® Index"),
    # Trailing ticker gloss — the pattern the brief did not list.
    ('the Nasdaq-100 Index ® (the "NDX Index")', "the Nasdaq-100 ® Index"),
    ('the Russell 2000 ® Index (the "RTY Index")', "Russell 2000 ® Index"),
    ('The Nasdaq-100 Index ® (ticker: "NDX")', "Nasdaq-100 ® Index"),
    ("S&P 500 ® Futures Excess Return Index (SPXFP)",
     "the S&P 500 ® Futures Excess Return Index"),
]

# Pairs that must NOT collapse. Same branding, different securities.
REAL_DISTINCT_PAIRS = [
    ("S&P 500 ® Index", "the S&P 500 ® Futures Excess Return Index"),
    ("Nasdaq-100 ® Index", "Nasdaq-100 ® Equal Weighted Index"),
    ("Nasdaq-100 ® Index", "Nasdaq-100 ® Technology Sector Index SM"),
    ("Russell 2000 ® Index", "iShares ® Russell 2000 ® ETF"),
    ("S&P 500 ® Index", "S&P ® 500 Futures 40% Intraday 4% Decrement VT Index"),
]

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ── Setup / teardown ─────────────────────────────────────────────────────────


async def teardown(conn) -> None:
    """Remove fixture users and the fixture security. Edge state is restored
    separately, by the checks that mutate it, from a snapshot they take."""
    await conn.execute(
        f"DELETE FROM {IDENT_TABLE} WHERE global_security_id IN "
        f"(SELECT id FROM {SEC_TABLE} WHERE name = $1)",
        FIXTURE_SECURITY_NAME,
    )
    # Nothing can reference it: it is created and confirmed inside one check,
    # and that check restores the edge that pointed at it before this runs.
    await conn.execute(
        f"UPDATE {REL_TABLE} SET to_global_security_id = NULL, "
        f"proposed_global_security_id = NULL "
        f"WHERE to_global_security_id IN (SELECT id FROM {SEC_TABLE} WHERE name = $1) "
        f"   OR proposed_global_security_id IN (SELECT id FROM {SEC_TABLE} WHERE name = $1)",
        FIXTURE_SECURITY_NAME,
    )
    await conn.execute(f"DELETE FROM {SEC_TABLE} WHERE name = $1", FIXTURE_SECURITY_NAME)
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[])",
        [ADMIN_SUB, MEMBER_SUB],
    )


async def seed_users(conn) -> None:
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_underlying_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_underlying_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify Underlying', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


EDGE_COLUMNS = (
    "link_state", "to_global_security_id", "proposed_global_security_id",
    "proposal_confidence", "proposal_kind", "proposal_hint", "proposed_at",
    "normalized_underlying_text", "resolved_by", "resolved_at", "resolution_notes",
)


async def snapshot_edge(conn, rel_id: str) -> dict:
    row = await conn.fetchrow(
        f"SELECT {', '.join(EDGE_COLUMNS)} FROM {REL_TABLE} WHERE id = $1::uuid",
        rel_id,
    )
    return dict(row)


async def restore_edge(conn, rel_id: str, snap: dict) -> None:
    """Write a snapshot back verbatim.

    Runs on the DATABASE_URL connection (table owner, RLS not forced) so it does
    not need the super-admin GUC. It deliberately does NOT set
    app.underlying_confirm: if a snapshot ever carried link_state='resolved' the
    trigger would reject this and the run would fail loudly, which is the right
    outcome — this script has no business restoring a resolution it did not make.
    """
    sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(EDGE_COLUMNS))
    await conn.execute(
        f"UPDATE {REL_TABLE} SET {sets} WHERE id = $1::uuid",
        rel_id, *[snap[col] for col in EDGE_COLUMNS],
    )


async def app_service_connection():
    url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "APP_SERVICE_DATABASE_URL is unset — the permission gates cannot be "
            "verified honestly. There is no SET ROLE fallback here by design."
        )
    conn = await asyncpg.connect(url, statement_cache_size=0)
    who = await conn.fetchval("SELECT current_user")
    bypass = await conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    if bypass:
        await conn.close()
        raise RuntimeError(f"APP_SERVICE_DATABASE_URL role {who!r} bypasses RLS")
    return conn, who


# ── 1. Normalization, against the real strings ───────────────────────────────


def check_normalization() -> None:
    failures = []
    for left, right in REAL_DUPLICATE_PAIRS:
        if normalize_underlying_text(left) != normalize_underlying_text(right):
            failures.append(
                f"{left!r} -> {normalize_underlying_text(left)!r} != "
                f"{right!r} -> {normalize_underlying_text(right)!r}"
            )
    check(
        f"normalize collapses {len(REAL_DUPLICATE_PAIRS)} REAL duplicate pairs "
        "from the live corpus",
        not failures,
        "; ".join(failures[:3]),
    )

    failures = []
    for left, right in REAL_DISTINCT_PAIRS:
        if normalize_underlying_text(left) == normalize_underlying_text(right):
            failures.append(f"{left!r} and {right!r} both -> "
                            f"{normalize_underlying_text(left)!r}")
    check(
        "normalize keeps genuinely different securities apart "
        "(S&P 500 vs Futures ER, NDX vs NDXE/NDXT, index vs ETF, vs decrement)",
        not failures,
        "; ".join(failures),
    )

    # The specific assertion the brief calls out by name.
    spot = normalize_underlying_text("S&P 500 ® Index")
    futs = normalize_underlying_text("the S&P 500 ® Futures Excess Return Index")
    check(
        "'S&P 500 Index' and 'S&P 500 Futures Excess Return Index' normalize "
        "to DIFFERENT strings",
        spot != futs and spot == "S&P 500 Index"
        and futs == "S&P 500 Futures Excess Return Index",
        f"{spot!r} vs {futs!r}",
    )


async def check_normalization_over_corpus(conn) -> None:
    """Measure the collapse on the whole live population, not a sample."""
    rows = await conn.fetch(
        f"SELECT DISTINCT raw_underlying_text FROM {REL_TABLE} "
        f"WHERE valid_to IS NULL AND system_to IS NULL"
    )
    raws = [r["raw_underlying_text"] for r in rows]
    norms = {normalize_underlying_text(r) for r in raws}
    check(
        "normalization measurably reduces the live corpus",
        len(norms) < len(raws),
        f"{len(raws)} distinct raw -> {len(norms)} distinct normalized",
    )
    blanks = [r for r in raws if not normalize_underlying_text(r)]
    check(
        "no live string normalizes to empty",
        not blanks,
        f"{len(blanks)} blanked: {blanks[:2]}",
    )


# ── 2. The registry — creating an index at most once ─────────────────────────


async def check_registry_idempotent(pool, conn) -> None:
    name = "S&P 500 Index"
    entry = lookup_index(name)
    check(
        "the five brief-named index families are all in KNOWN_INDICES",
        all(lookup_index(n) is not None for n in INDEX_FAMILY_NAMES),
        f"{len(KNOWN_INDICES)} lookup keys over "
        f"{len({v['name'] for v in KNOWN_INDICES.values()})} securities",
    )
    check(
        "'S&P 500 Futures Excess Return Index' is a SEPARATE registry entry "
        "from 'S&P 500 Index'",
        lookup_index("S&P 500 Futures Excess Return Index") is not None
        and lookup_index("S&P 500 Futures Excess Return Index")["name"]
        != entry["name"],
    )
    check(
        "the TOPIX alias resolves to the same entry as 'Tokyo Stock Price Index'",
        lookup_index("TOPIX Index") is not None
        and lookup_index("TOPIX Index") is lookup_index("Tokyo Stock Price Index"),
    )

    async def index_count() -> int:
        return await conn.fetchval(
            f"SELECT count(*) FROM {SEC_TABLE} WHERE security_type = 'index' "
            f"AND valid_to IS NULL AND system_to IS NULL"
        )

    first_id = await resolve_or_create_index_security(pool, name)
    after_first = await index_count()
    second_id = await resolve_or_create_index_security(pool, name)
    after_second = await index_count()

    check(
        "resolve_or_create_index_security returns the SAME id on a second call",
        first_id == second_id,
        f"{first_id} vs {second_id}",
    )
    check(
        "the second call creates NO additional securities_global row",
        after_first == after_second,
        f"index rows {after_first} -> {after_second}",
    )
    row = await conn.fetchrow(
        f"SELECT name, security_type, price_coverage FROM {SEC_TABLE} WHERE id = $1::uuid",
        first_id,
    )
    check(
        "the created row is security_type='index' with price_coverage='unknown'",
        row is not None and row["security_type"] == "index"
        and row["price_coverage"] == "unknown",
        f"{dict(row) if row else None}",
    )

    # The 1e gap this sprint closed: the table had NO unique constraint at all.
    dupes = await conn.fetch(
        f"SELECT lower(name) n, count(*) c FROM {SEC_TABLE} "
        f"WHERE security_type = 'index' AND valid_to IS NULL AND system_to IS NULL "
        f"GROUP BY 1 HAVING count(*) > 1"
    )
    check(
        "no duplicate index rows exist (uq_sec_global_active_index_name holds)",
        not dupes,
        f"{[(d['n'], d['c']) for d in dupes]}",
    )
    check(
        "resolve_or_create_index_security REFUSES a name outside the registry",
        await _raises_keyerror(pool, "Totally Made Up Strategy Index"),
    )


async def _raises_keyerror(pool, name: str) -> bool:
    try:
        await resolve_or_create_index_security(pool, name)
    except KeyError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


# ── 3. THE core governance assertion: a proposal cannot resolve ──────────────


async def check_proposal_never_resolves(pool, conn) -> str | None:
    """Run the real pipeline on a real high-confidence index edge."""
    row = await conn.fetchrow(
        f"""
        SELECT id, raw_underlying_text FROM {REL_TABLE}
        WHERE raw_underlying_text = 'S&P 500 ® Index'
          AND valid_to IS NULL AND system_to IS NULL
        LIMIT 1
        """
    )
    if row is None:
        check("propose_resolution never sets link_state='resolved'", False,
              "no live 'S&P 500 ® Index' edge to test against")
        return None

    rel_id = str(row["id"])
    result = await propose_resolution(pool, rel_id, is_super_admin=True)
    after = await conn.fetchrow(
        f"SELECT link_state, to_global_security_id, proposed_global_security_id, "
        f"proposal_confidence, resolved_by FROM {REL_TABLE} WHERE id = $1::uuid",
        rel_id,
    )

    check(
        "PROPOSAL NEVER RESOLVES: a high-confidence index match leaves "
        "link_state NOT 'resolved'",
        after["link_state"] != "resolved",
        f"link_state={after['link_state']!r} confidence={after['proposal_confidence']!r}",
    )
    check(
        "the high-confidence proposal lands on 'ambiguous' with a proposed target",
        after["link_state"] == "ambiguous"
        and after["proposed_global_security_id"] is not None
        and result.confidence == "high",
        f"state={after['link_state']!r} "
        f"proposed={after['proposed_global_security_id']} "
        f"confidence={result.confidence!r}",
    )
    check(
        "to_global_security_id is NOT overloaded with the unconfirmed guess",
        after["to_global_security_id"] is None and after["resolved_by"] is None,
        f"to_global_security_id={after['to_global_security_id']} "
        f"resolved_by={after['resolved_by']}",
    )
    return rel_id


async def check_database_level_gate(app_conn, conn) -> None:
    """The gate is in the DATABASE, not only in Python.

    Two distinct proofs, because they fail differently:
      * RLS blocks the UPDATE outright for a caller with no super-admin GUC —
        zero rows touched, no error;
      * with the super-admin GUC set but WITHOUT app.underlying_confirm, the
        trigger RAISES. That second one is the maker-checker: a Super Admin can
        write to this table all day and still cannot resolve an edge except
        through the confirm path.
    """
    row = await conn.fetchrow(
        f"SELECT id, link_state FROM {REL_TABLE} "
        f"WHERE link_state <> 'resolved' AND valid_to IS NULL AND system_to IS NULL "
        f"LIMIT 1"
    )
    if row is None:
        check("database-level confirm gate", False, "no non-resolved edge to test")
        return
    rel_id = str(row["id"])
    target = await conn.fetchval(
        f"SELECT id FROM {SEC_TABLE} WHERE security_type = 'index' "
        f"AND valid_to IS NULL AND system_to IS NULL LIMIT 1"
    )

    # (a) no GUCs at all -> RLS refuses; the row must be untouched.
    tag = await app_conn.execute(
        f"UPDATE {REL_TABLE} SET link_state = 'resolved', "
        f"to_global_security_id = $2::uuid, resolved_by = $3::uuid, "
        f"resolved_at = now() WHERE id = $1::uuid",
        rel_id, str(target), ADMIN_USER_ID,
    )
    still = await conn.fetchval(
        f"SELECT link_state FROM {REL_TABLE} WHERE id = $1::uuid", rel_id
    )
    check(
        "app_service with NO super-admin context cannot set link_state='resolved' "
        "(RLS)",
        tag.endswith(" 0") and still != "resolved",
        f"tag={tag!r} link_state now {still!r}",
    )

    # (b) super-admin GUC set, confirm token absent -> the TRIGGER raises.
    raised = ""
    try:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
            )
            await app_conn.execute(
                f"UPDATE {REL_TABLE} SET link_state = 'resolved', "
                f"to_global_security_id = $2::uuid, resolved_by = $3::uuid, "
                f"resolved_at = now() WHERE id = $1::uuid",
                rel_id, str(target), ADMIN_USER_ID,
            )
    except asyncpg.PostgresError as exc:
        raised = f"{getattr(exc, 'sqlstate', '')}: {exc}"
    still = await conn.fetchval(
        f"SELECT link_state FROM {REL_TABLE} WHERE id = $1::uuid", rel_id
    )
    check(
        "MAKER-CHECKER IS IN THE DATABASE: even a Super Admin cannot set "
        "link_state='resolved' without the confirm token",
        raised.startswith("42501") and still != "resolved",
        raised[:140] or "no exception raised",
    )


# ── 4. Exactly one write site sets link_state='resolved' ─────────────────────


_UPDATE_KEYWORD = re.compile(r"\bUPDATE\b")
_SET_RESOLVED = re.compile(r"link_state\s*=\s*'resolved'")


def _scan_files() -> list[str]:
    paths = []
    for base in ("apps/api/services", "apps/api/routers", "apps/api/scripts",
                 "apps/api/models", "apps/api/migrations", "docs"):
        root = os.path.join(ROOT, base)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "venv", "node_modules")]
            for fname in filenames:
                if fname.endswith((".py", ".sql")):
                    paths.append(os.path.join(dirpath, fname))
    return sorted(paths)


def check_single_write_site() -> None:
    """Find every UPDATE statement in the codebase whose SET clause writes
    link_state='resolved', and assert there is exactly one.

    Scoped to UPDATE statements on purpose. The Part-1 migration's trigger body
    contains ``NEW.link_state = 'resolved'`` and the services contain
    ``link_state <> 'resolved'`` guards — those are comparisons, and a naive
    grep for the literal would report five sites and prove nothing.
    """
    hits: list[str] = []
    for path in _scan_files():
        if os.path.basename(path) == os.path.basename(__file__):
            continue  # this file's own restore/attack SQL is not a write site
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        for match in _UPDATE_KEYWORD.finditer(text):
            # The statement runs to the next ';' or the end of the enclosing
            # Python string literal, whichever comes first.
            tail = text[match.end():]
            end = min(
                (i for i in (tail.find(";"), tail.find('"""'), tail.find("'''"))
                 if i != -1),
                default=len(tail),
            )
            if _SET_RESOLVED.search(tail[:end]):
                line = text[: match.start()].count("\n") + 1
                hits.append(f"{os.path.relpath(path, ROOT)}:{line}")

    check(
        "EXACTLY ONE code site writes link_state='resolved'",
        len(hits) == 1,
        f"{len(hits)} site(s): {hits}",
    )
    check(
        "that site is confirm_resolution in services/underlying_resolution.py",
        len(hits) == 1 and hits[0].startswith("apps/api/services/underlying_resolution.py"),
        f"{hits}",
    )


# ── 5. Permission gates ──────────────────────────────────────────────────────


async def check_permission_gates(pool, conn) -> None:
    row = await conn.fetchrow(
        f"SELECT id FROM {REL_TABLE} WHERE link_state <> 'resolved' "
        f"AND valid_to IS NULL AND system_to IS NULL LIMIT 1"
    )
    rel_id = str(row["id"])

    for label, coro in (
        ("confirm_resolution", confirm_resolution(
            pool, rel_id, actor_id=ADMIN_USER_ID, is_super_admin=False)),
        ("reject_proposal", reject_proposal(
            pool, rel_id, actor_id=ADMIN_USER_ID, is_super_admin=False)),
        ("propose_resolution", propose_resolution(
            pool, rel_id, is_super_admin=False)),
    ):
        raised = False
        try:
            await coro
        except UnderlyingResolutionPermissionError:
            raised = True
        except Exception as exc:  # noqa: BLE001
            raised = False
            print(f"      ({label} raised {type(exc).__name__} instead)")
        check(f"{label} refuses without is_super_admin", raised)

    both = False
    try:
        await confirm_resolution(
            pool, rel_id, actor_id=ADMIN_USER_ID,
            global_security_id=ADMIN_USER_ID, create_new=True, is_super_admin=True,
        )
    except UnderlyingResolutionError:
        both = True
    except Exception:  # noqa: BLE001
        both = False
    check("confirm refuses global_security_id AND create_new together", both)


# ── 6. The full corpus run ───────────────────────────────────────────────────


async def check_full_corpus(pool, conn) -> dict:
    total_before = await conn.fetchval(
        f"SELECT count(*) FROM {REL_TABLE} "
        f"WHERE link_state <> 'resolved' AND valid_to IS NULL AND system_to IS NULL"
    )
    results_all = await propose_all_unresolved(pool, is_super_admin=True)

    high = [r for r in results_all if r.confidence == "high"]
    manual = [r for r in results_all if r.confidence == "needs_manual_match"]
    by_kind: dict[str, int] = {}
    for r in manual:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    with_hint = [r for r in manual if r.hint]

    print(f"\n      ── full-corpus pass over {len(results_all)} edges ──")
    print(f"      high-confidence index proposal : {len(high)}")
    print(f"      needs_manual_match             : {len(manual)}")
    for kind in sorted(by_kind):
        print(f"        {kind:<22}       : {by_kind[kind]}")
    print(f"      of which carry a reviewer hint : {len(with_hint)}")

    check(
        "propose_resolution ran against every non-resolved edge",
        len(results_all) == total_before,
        f"{len(results_all)} of {total_before}",
    )
    check(
        "no edge came back 'resolved' from the full-corpus pass",
        all(r.link_state != "resolved" for r in results_all),
        f"{sum(1 for r in results_all if r.link_state == 'resolved')} did",
    )
    check(
        "every high-confidence result carries a proposed target",
        all(r.proposed_global_security_id for r in high),
    )
    check(
        "every needs_manual_match result carries NO proposed target",
        all(r.proposed_global_security_id is None for r in manual),
    )

    # The coverage assertion, computed from the FIVE named families rather than
    # from a hand-counted number, so it stays true if the corpus grows.
    family_edges = [
        r for r in results_all
        if lookup_index(r.normalized_text) is not None
        and lookup_index(r.normalized_text)["name"] in INDEX_FAMILY_NAMES
    ]
    proposed_family = [r for r in family_edges if r.is_proposal]
    check(
        "the five brief-named index families ALL receive a proposal "
        "(brief expects at least 30 edges)",
        len(family_edges) >= 30 and len(proposed_family) == len(family_edges),
        f"{len(proposed_family)}/{len(family_edges)} family edges proposed",
    )

    # Single names get a hint but never a target — the explicit non-goal.
    single = [r for r in manual if r.kind == "single_name"]
    check(
        "single-name equities get a reviewer HINT and never an auto-match",
        single and all(r.hint and r.proposed_global_security_id is None
                       for r in single),
        f"{len(single)} single-name edges, "
        f"e.g. {single[0].hint!r}" if single else "none found",
    )
    nvidia = [r for r in single if r.hint and "NVIDIA" in r.hint]
    check(
        "'the Common Stock of NVIDIA Corporation' -> hint 'NVIDIA Corporation', "
        "not NVDA",
        len(nvidia) >= 4 and all(r.hint == "NVIDIA Corporation" for r in nvidia),
        f"{len(nvidia)} NVIDIA edges, hints={sorted({r.hint for r in nvidia})}",
    )

    # Decrement indices flagged, never proposed.
    decrement = [r for r in manual if r.kind == "decrement_candidate"]
    check(
        "decrement / risk-control indices are flagged and never auto-matched",
        decrement and all(r.proposed_global_security_id is None for r in decrement),
        f"{len(decrement)} flagged: "
        f"{sorted({r.normalized_text for r in decrement})[:2]}",
    )

    return {
        "total": len(results_all),
        "high": len(high),
        "manual": len(manual),
        "by_kind": by_kind,
        "with_hint": len(with_hint),
        "family_edges": len(family_edges),
    }


# ── 7. Confirm and reject, end to end on real data ───────────────────────────


async def check_confirm_end_to_end(pool, conn) -> None:
    row = await conn.fetchrow(
        f"""
        SELECT id FROM {REL_TABLE}
        WHERE link_state = 'ambiguous' AND proposal_confidence = 'high'
          AND valid_to IS NULL AND system_to IS NULL
        ORDER BY id LIMIT 1
        """
    )
    if row is None:
        check("confirm one proposed index resolution end to end", False,
              "no standing high-confidence proposal to confirm")
        return
    rel_id = str(row["id"])
    snap = await snapshot_edge(conn, rel_id)
    try:
        result = await confirm_resolution(
            pool, rel_id, actor_id=ADMIN_USER_ID, is_super_admin=True
        )
        after = await conn.fetchrow(
            f"""
            SELECT rel.link_state, rel.to_global_security_id,
                   rel.proposed_global_security_id, rel.resolved_by, rel.resolved_at,
                   s.security_type, s.name
            FROM {REL_TABLE} rel
            LEFT JOIN {SEC_TABLE} s ON s.id = rel.to_global_security_id
            WHERE rel.id = $1::uuid
            """,
            rel_id,
        )
        check(
            "confirm sets link_state='resolved' with a non-null "
            "to_global_security_id",
            after["link_state"] == "resolved"
            and after["to_global_security_id"] is not None,
            f"state={after['link_state']!r} target={after['to_global_security_id']}",
        )
        check(
            "the confirmed target IS a securities_global row with "
            "security_type='index'",
            after["security_type"] == "index",
            f"{after['name']!r} type={after['security_type']!r}",
        )
        check(
            "confirm records who resolved it and when",
            str(after["resolved_by"]) == ADMIN_USER_ID
            and after["resolved_at"] is not None,
            f"by={after['resolved_by']} at={after['resolved_at']}",
        )
        check(
            "confirm KEEPS the proposal alongside the confirmed target, so "
            "'reviewer agreed with the matcher' stays answerable from the row",
            after["proposed_global_security_id"] is not None
            and str(after["proposed_global_security_id"])
            == str(after["to_global_security_id"]),
            f"proposed={after['proposed_global_security_id']} "
            f"confirmed={after['to_global_security_id']}",
        )
        again = False
        try:
            await confirm_resolution(
                pool, rel_id, actor_id=ADMIN_USER_ID, is_super_admin=True
            )
        except UnderlyingResolutionError:
            again = True
        check("confirming an already-resolved edge is refused, not silently re-done",
              again)
        check("confirm reports the target it wrote",
              result["to_global_security_id"] == str(after["to_global_security_id"]))
    finally:
        await restore_edge(conn, rel_id, snap)
    restored = await conn.fetchval(
        f"SELECT link_state FROM {REL_TABLE} WHERE id = $1::uuid", rel_id
    )
    check("the confirmed test edge was restored to its pre-test state",
          restored == snap["link_state"], f"{restored!r} vs {snap['link_state']!r}")


async def check_create_new(pool, conn) -> None:
    """The reviewer's escape hatch for a security the registry cannot place."""
    row = await conn.fetchrow(
        f"""
        SELECT id FROM {REL_TABLE}
        WHERE proposal_kind = 'decrement_candidate'
          AND link_state <> 'resolved' AND valid_to IS NULL AND system_to IS NULL
        ORDER BY id LIMIT 1
        """
    )
    if row is None:
        check("create_new builds a placeholder for a decrement index", False,
              "no decrement candidate in the corpus")
        return
    rel_id = str(row["id"])
    snap = await snapshot_edge(conn, rel_id)
    created_id = None
    try:
        result = await confirm_resolution(
            pool, rel_id, actor_id=ADMIN_USER_ID, create_new=True, is_super_admin=True
        )
        created_id = result["to_global_security_id"]
        sec = await conn.fetchrow(
            f"SELECT name, security_type, price_coverage FROM {SEC_TABLE} "
            f"WHERE id = $1::uuid",
            created_id,
        )
        check(
            "create_new makes an index row with price_coverage='no_public_source' "
            "for a decrement index",
            sec is not None and sec["security_type"] == "index"
            and sec["price_coverage"] == "no_public_source",
            f"{dict(sec) if sec else None}",
        )
        check(
            "the created row is named from the NORMALIZED text, not the raw text",
            sec is not None and "®" not in sec["name"],
            f"{sec['name']!r}" if sec else "",
        )
    finally:
        await restore_edge(conn, rel_id, snap)
        if created_id:
            # Only delete if nothing else points at it — a second reviewer may
            # legitimately have linked to the same placeholder.
            refs = await conn.fetchval(
                f"SELECT count(*) FROM {REL_TABLE} "
                f"WHERE to_global_security_id = $1::uuid "
                f"   OR proposed_global_security_id = $1::uuid",
                created_id,
            )
            if refs == 0:
                await conn.execute(
                    f"DELETE FROM {IDENT_TABLE} WHERE global_security_id = $1::uuid",
                    created_id,
                )
                await conn.execute(
                    f"DELETE FROM {SEC_TABLE} WHERE id = $1::uuid", created_id
                )


async def check_reject(pool, conn) -> None:
    row = await conn.fetchrow(
        f"""
        SELECT id FROM {REL_TABLE}
        WHERE link_state = 'ambiguous' AND proposed_global_security_id IS NOT NULL
          AND valid_to IS NULL AND system_to IS NULL
        ORDER BY id DESC LIMIT 1
        """
    )
    if row is None:
        check("reject clears a proposal back to 'unresolved'", False,
              "no standing proposal to reject")
        return
    rel_id = str(row["id"])
    snap = await snapshot_edge(conn, rel_id)
    try:
        await reject_proposal(pool, rel_id, actor_id=ADMIN_USER_ID, is_super_admin=True)
        after = await conn.fetchrow(
            f"SELECT link_state, proposed_global_security_id, proposal_confidence, "
            f"proposal_kind, proposal_hint, proposed_at, normalized_underlying_text "
            f"FROM {REL_TABLE} WHERE id = $1::uuid",
            rel_id,
        )
        check(
            "reject returns the edge to 'unresolved' with the proposal CLEARED",
            after["link_state"] == "unresolved"
            and after["proposed_global_security_id"] is None
            and after["proposal_confidence"] is None
            and after["proposal_kind"] is None
            and after["proposed_at"] is None,
            f"{dict(after)}",
        )
        check(
            "reject keeps normalized_underlying_text (the normalizer's output is "
            "not the matcher's opinion)",
            after["normalized_underlying_text"] is not None,
            f"{after['normalized_underlying_text']!r}",
        )
    finally:
        await restore_edge(conn, rel_id, snap)


# ── 8. The queue ─────────────────────────────────────────────────────────────


async def check_queue(conn, app_conn) -> None:
    items = await load_queue(conn)
    check("the queue returns the edges awaiting a decision", bool(items),
          f"{len(items)} rows")

    with_note = [
        i for i in items
        if i["note"]["note_terms_id"] and i["note"]["cik"] and i["note"]["accession_number"]
    ]
    check(
        "queue rows carry REAL note context (note_terms row + filer + accession), "
        "not just the bare relationship",
        bool(with_note),
        f"{len(with_note)}/{len(items)} joined; "
        f"e.g. {with_note[0]['note']['filer_name']!r} "
        f"{with_note[0]['note']['accession_number']!r} "
        f"archetype={with_note[0]['note']['product_archetype']!r}"
        if with_note else "none joined",
    )
    check(
        "no resolved edge leaks into the queue",
        all(i["link_state"] in ("unresolved", "ambiguous") for i in items),
    )
    proposed_first = [i["proposal"] is not None
                      and i["proposal"]["confidence"] == "high" for i in items]
    check(
        "the queue sorts confirmable proposals to the top",
        proposed_first == sorted(proposed_first, reverse=True),
    )

    # Global read with NO org context at all.
    await app_conn.execute("SELECT set_config('app.current_org_id', '', true)")
    org_ctx = await app_conn.fetchval("SELECT current_setting('app.current_org_id', true)")
    app_items = await load_queue(app_conn)
    check(
        "global read on the queue works under app_service with NO org context set",
        len(app_items) == len(items),
        f"app_service saw {len(app_items)}, owner saw {len(items)}, "
        f"app.current_org_id={org_ctx!r}",
    )


# ── 9. The HTTP surface ──────────────────────────────────────────────────────


async def check_endpoints(conn) -> None:
    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        check("underlying-queue endpoint", False,
              f"could not import app/TestClient: {type(exc).__name__}: {exc}")
        return

    subs = {"admin": ADMIN_SUB, "member": MEMBER_SUB}
    current = {"who": "admin"}
    main.verify_token = lambda _t: {
        "sub": subs[current["who"]],
        "email": "verify_underlying@test.local",
        "org_id": DEFAULT_ORG_ID,
    }
    hdr = {"Authorization": "Bearer stub"}

    def get_queue():
        with TestClient(main.app, raise_server_exceptions=False) as c:
            return c.get("/api/v1/admin/pricing/underlying-queue", headers=hdr)

    resp = await asyncio.to_thread(get_queue)
    body = resp.json() if resp.status_code == 200 else {}
    expected = len(await load_queue(conn))
    check(
        "GET /admin/pricing/underlying-queue returns the queue for a Super Admin",
        resp.status_code == 200 and len(body.get("queue", [])) == expected,
        f"HTTP {resp.status_code}; {len(body.get('queue', []))} of {expected}; "
        f"counts={body.get('counts')}",
    )

    rel_id = body["queue"][0]["id"] if body.get("queue") else None

    current["who"] = "member"

    def post_confirm():
        with TestClient(main.app, raise_server_exceptions=False) as c:
            return c.post(
                f"/api/v1/admin/pricing/underlying-queue/{rel_id}/confirm",
                json={"global_security_id": None, "create_new": False},
                headers=hdr,
            )

    def post_reject():
        with TestClient(main.app, raise_server_exceptions=False) as c:
            return c.post(
                f"/api/v1/admin/pricing/underlying-queue/{rel_id}/reject",
                headers=hdr,
            )

    def get_as_member():
        with TestClient(main.app, raise_server_exceptions=False) as c:
            return c.get("/api/v1/admin/pricing/underlying-queue", headers=hdr)

    if rel_id:
        confirm_resp = await asyncio.to_thread(post_confirm)
        check(
            "POST .../confirm is 403 for a non-Super-Admin",
            confirm_resp.status_code == 403,
            f"HTTP {confirm_resp.status_code}",
        )
        reject_resp = await asyncio.to_thread(post_reject)
        check(
            "POST .../reject is 403 for a non-Super-Admin",
            reject_resp.status_code == 403,
            f"HTTP {reject_resp.status_code}",
        )
        state = await conn.fetchval(
            f"SELECT link_state FROM {REL_TABLE} WHERE id = $1::uuid", rel_id
        )
        check(
            "the refused confirm changed nothing",
            state != "resolved",
            f"link_state={state!r}",
        )
    member_get = await asyncio.to_thread(get_as_member)
    check("GET .../underlying-queue is 403 for a non-Super-Admin",
          member_get.status_code == 403, f"HTTP {member_get.status_code}")
    current["who"] = "admin"


# ── main ─────────────────────────────────────────────────────────────────────


async def main_async() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("FATAL: DATABASE_URL is not set")
        return 1

    app_conn, app_role = await app_service_connection()
    print(f"app_service role: {app_role}")

    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"], statement_cache_size=0, min_size=1, max_size=4
    )
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    summary: dict = {}
    try:
        await teardown(conn)  # START
        await seed_users(conn)

        check_normalization()
        await check_normalization_over_corpus(conn)
        await check_registry_idempotent(pool, conn)
        await check_proposal_never_resolves(pool, conn)
        check_single_write_site()
        await check_permission_gates(pool, conn)
        await check_database_level_gate(app_conn, conn)
        summary = await check_full_corpus(pool, conn)
        await check_confirm_end_to_end(pool, conn)
        await check_create_new(pool, conn)
        await check_reject(pool, conn)
        await check_queue(conn, app_conn)
        await check_endpoints(conn)
    finally:
        try:
            await teardown(conn)  # END
        finally:
            await conn.close()
            await pool.close()
            await app_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    if summary:
        print(f"\nCORPUS: {summary['total']} edges — "
              f"{summary['high']} high-confidence index proposals, "
              f"{summary['manual']} routed to manual review "
              f"({summary['by_kind']})")
    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} "
          f"({len(results)} checks, {passed} passed, {failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
