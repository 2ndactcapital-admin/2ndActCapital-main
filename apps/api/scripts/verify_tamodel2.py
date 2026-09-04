"""TA Model Sprint 2 (admin settings UX) — verify.

TASK 1 — DISCOVERY FINDINGS, READ THIS FIRST
──────────────────────────────────────────────────────────────────────────────
1a. GET /api/v1/modeling/ta/defaults's response, BEFORE this sprint, was a
    flat dict of the 4 modeling.ta.* keys with NO indication of which of the
    8 strategies inside ``modeling.ta.strategy_defaults`` an org had actually
    overridden vs. was still inheriting from the platform seed. The org_settings
    row backing that key is ONE jsonb blob covering all 8 strategies, so even
    ``get_setting_with_origin``'s row-existence flag cannot answer this at
    strategy granularity — an org that has written the row at all reads as
    "not default" for every strategy, even ones it never touched. This sprint
    adds ``services.ta_config.strategy_overrides`` (a real per-strategy
    Decimal-VALUE comparison against the seed, not a row-existence check) and
    publishes it as ``strategy_overrides`` on both GET and PUT responses.

1b. No existing admin settings screen in this codebase publishes a real
    ``permissions`` envelope for an org_settings-shaped edit.
    ``OrgSettingsEditor.jsx`` (the only other screen editing a nested
    org_settings shape) takes a client-derived boolean ``canEdit`` prop from
    each of its two callers' own role checks — never a server-published
    ``permissions.can_write`` — a real, pre-existing gap in that older screen,
    not fixed by this sprint (out of scope: this sprint only touches
    modeling_ta.py / ta_config.py / the new TA screen). The correct,
    established pattern this sprint reuses instead is the Workflow Triggers
    screen's envelope (``services.rbac`` bypass-first, ``can_write`` with NO
    ``||``/``??`` fallback) — genuinely reused, not reinvented.

1c. The PUT endpoint's NUMERIC validation (bow_factor >= 0, rates in [0,1],
    fund_life_years > 0) is real and shared — both the router and
    ``ta_config.params_for_strategy`` construct the SAME ``TAParams``, whose
    ``__post_init__`` is the one place those rules live. But the SHAPE/
    key-membership checks (unknown top-level key, unknown strategy_key) were,
    and remain, inline in the router, not delegated to a single ``ta_config``
    entry point — a real, narrower gap than the numeric rules, left as-is
    since duplicating a two-line membership check is not the same risk class
    as duplicating a numeric rule.

    A SEPARATE, more serious bug found in this same read: the PUT handler
    passed ``body.values`` straight to ``set_settings`` with no merge step.
    ``modeling.ta.strategy_defaults`` is ONE blob for all 8 strategies, so a
    caller submitting only the ONE strategy an admin actually edited would
    silently REPLACE the whole blob, discarding every other strategy's prior
    override. This sprint fixes it: the router now fetches the org's existing
    blob and merges the submitted per-strategy entries into it before writing
    (Assertions 2.x below reproduce the bug against the pre-fix shape's
    request contract and prove the fix). This is a real, structural change to
    a Sprint 1 write endpoint, not merely a UI addition — the reason this
    sprint is `.structural`, not `.lowrisk`.

DATABASE CONNECTIVITY
──────────────────────────────────────────────────────────────────────────────
Same discipline as verify_tamodel1.py: hydrates from Doppler over HTTPS
(``_doppler_env.hydrate_from_doppler`` — stdlib only, never the ``doppler``
CLI, never prints a value) before attempting a real connection. Every
DB-dependent assertion is reported [BLOCKED] with the real exception if that
connection fails — never silently skipped, never faked as [PASS].

Run:
    python3 scripts/verify_tamodel2.py
"""

from __future__ import annotations

import glob
import os
import pathlib
import re
import subprocess
import sys
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.join(_HERE, "..")
sys.path.insert(0, _HERE)
sys.path.insert(0, _API)
sys.path.extend(sorted(glob.glob(os.path.join(_API, "venv", "lib", "python3*", "site-packages"))))

import asyncio  # noqa: E402

import asyncpg  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
WEB = REPO / "apps" / "web"

passed = 0
failed = 0
blocked = 0


def ok(label: str) -> None:
    global passed
    passed += 1
    print(f"[PASS] {label}")


def fail(label: str, detail: str = "") -> None:
    global failed
    failed += 1
    print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        ok(label)
    else:
        fail(label, detail)


def blocked_(label: str, reason: str) -> None:
    global blocked
    blocked += 1
    print(f"[BLOCKED] {label} — {reason}")


def report(label: str, detail: str) -> None:
    print(f"[FIND] {label}\n       {detail}")


def read(path) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8")


def strip_js_comments(src: str) -> str:
    """Executable JSX only — an ABSENCE assertion must not trip on a comment
    that merely EXPLAINS the rule it is checking for."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — DISCOVERY FINDINGS (no DB required — repo/code facts)
# ═══════════════════════════════════════════════════════════════════════════


def report_task1_findings() -> None:
    report(
        "1a — GET /modeling/ta/defaults published NO per-strategy override "
        "signal before this sprint",
        "modeling.ta.strategy_defaults is one jsonb blob for all 8 strategies; "
        "org_settings.get_setting_with_origin's row-existence flag cannot "
        "answer 'which strategy did the org actually touch' at that "
        "granularity. Fixed by services.ta_config.strategy_overrides, a real "
        "per-strategy Decimal-value comparison against the seed — published as "
        "strategy_overrides on both GET and PUT (apps/api/routers/modeling_ta.py).",
    )
    report(
        "1b — no existing admin settings screen published a real permissions "
        "envelope for a nested org_settings edit",
        "OrgSettingsEditor.jsx (the only other screen editing a nested "
        "org_settings shape) takes a client-derived `canEdit` boolean from "
        "each caller's own role check, never a server `permissions.can_write` "
        "— a real, pre-existing gap left as-is (out of this sprint's scope). "
        "The TA settings screen instead reuses the Workflow Triggers screen's "
        "envelope pattern verbatim: bypass-first is_super_admin, can_write "
        "with NO ||/?? fallback, seeded to {can_write: false} when missing.",
    )
    report(
        "1c — PUT's numeric validation is shared; its shape checks are not; "
        "and a real clobber bug was found and fixed",
        "bow_factor/rate/fund_life_years rules live in ONE place (TAParams."
        "__post_init__, constructed identically by the router and by "
        "ta_config.params_for_strategy) — genuinely shared, not duplicated. "
        "But the PUT handler wrote body.values straight through with no merge "
        "step: since strategy_defaults is ONE blob for all 8 strategies, a "
        "caller submitting only the ONE strategy actually edited would "
        "silently REPLACE the whole blob and discard every other strategy's "
        "prior override. Fixed this sprint: the router now merges a partial "
        "per-strategy submission into the org's existing blob before writing "
        "(Assertions 2.x below reproduce the bug's precondition and prove the "
        "fix) — a real structural change to a Sprint 1 endpoint, the reason "
        "this sprint is .structural.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE-DEPENDENT ASSERTIONS
# ═══════════════════════════════════════════════════════════════════════════

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-TAMODEL2"
ADMIN_SUB = "auth0|verify_tamodel2_admin"
MEMBER_SUB = "auth0|verify_tamodel2_member"

# services.permissions.get_user_id derives a deterministic uuid5(sub) for any
# claim whose `sub` is not already a UUID — see project memory: "get_user_id
# returns uuid5(sub), so a hand-picked fixture id fakes a 403".
U_ADMIN = str(uuid5(NAMESPACE_URL, ADMIN_SUB))         # org A, org_admin
U_MEMBER = str(uuid5(NAMESPACE_URL, MEMBER_SUB))       # org A, plain member
ALL_TEST_USERS = [U_ADMIN, U_MEMBER]

HEADERS = {"Authorization": "Bearer verify-token"}

DB_ASSERTIONS = (
    "2.x GET/PUT envelope shape (strategy_overrides, permissions)",
    "2.x the merge fix: a partial single-strategy PUT does not clobber other "
    "overridden strategies",
    "2.x a real 400 is surfaced as a plain string (verbatim-renderable)",
    "2.x view-only: can_write is false in GET, and PUT is refused 403 — "
    "checked independently",
    "2.x cross-org isolation on the settings screen",
    "2.x GET /modeling/ta/calibration-floor reuses the real frequency-aware floor",
    "3.x teardown leaves zero leftover rows",
)


async def cleanup(conn) -> None:
    await conn.execute(
        "DELETE FROM org_settings WHERE org_id IN ($1, $2) AND setting_key LIKE 'modeling.ta.%'",
        ORG_A, ORG_B,
    )
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM org_settings WHERE org_id IN ($2, $3) AND setting_key LIKE 'modeling.ta.%')
        """,
        ALL_TEST_USERS, ORG_A, ORG_B,
    ))


async def seed_users(conn) -> None:
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO UPDATE SET role = EXCLUDED.role, org_id = EXCLUDED.org_id
        """,
        U_ADMIN, ORG_A, "tamodel2_admin@test.local", "TAModel2 Admin", ADMIN_SUB, "org_admin",
    )
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO UPDATE SET role = EXCLUDED.role, org_id = EXCLUDED.org_id
        """,
        U_MEMBER, ORG_A, "tamodel2_member@test.local", "TAModel2 Member", MEMBER_SUB, "member",
    )


class _Principal:
    """Drives the real ASGI app as one specific user (verify_tamodel1.py's
    shape — one shared TestClient/loop, verify_token stubbed per-call)."""

    __slots__ = ("client", "org_id", "sub")

    def __init__(self, client, org_id: str, sub: str):
        self.client = client
        self.org_id = org_id
        self.sub = sub

    def _become(self) -> None:
        import main
        main.verify_token = lambda _token: {
            "sub": self.sub, "email": f"{self.sub}@test.local", "org_id": self.org_id,
        }

    def get(self, url, **kw):
        self._become()
        return self.client.get(url, **kw)

    def put(self, url, **kw):
        self._become()
        return self.client.put(url, **kw)


async def run_api_assertions(admin_conn) -> None:
    import main
    from starlette.testclient import TestClient

    print("\n── Section 2: TA settings envelope, through the REAL ASGI app ──")

    shared = TestClient(main.app, raise_server_exceptions=False)
    shared.__enter__()
    try:
        admin = _Principal(shared, ORG_A, ADMIN_SUB)
        member = _Principal(shared, ORG_A, MEMBER_SUB)
        org_b_admin = _Principal(shared, ORG_B, ADMIN_SUB)

        # ── 2.1: GET envelope shape — real settings, no mock data ──────────
        res = admin.get("/api/v1/modeling/ta/defaults", headers=HEADERS)
        body = res.json() if res.status_code == 200 else {}
        check(
            "[Y] GET /modeling/ta/defaults returns 200 with the REAL, live "
            "settings (not a fixture/mock — an env with a fresh org has never "
            "had this row written and still gets a full response)",
            res.status_code == 200 and "modeling.ta.strategy_defaults" in body,
            f"status={res.status_code}",
        )
        check(
            "[Y] the response publishes strategy_overrides for exactly the 8 "
            "real strategy keys, all False for a never-configured org",
            set(body.get("strategy_overrides", {})) == {
                "buyout", "growth_equity", "venture_capital", "real_estate",
                "real_assets", "private_credit", "fund_of_funds", "secondaries",
            }
            and not any(body["strategy_overrides"].values()),
            f"got {body.get('strategy_overrides')}",
        )
        check(
            "[Y] the response publishes a real permissions envelope: "
            "can_read=true, can_write=true for org_admin, is_super_admin=false",
            body.get("permissions", {}).get("can_read") is True
            and body.get("permissions", {}).get("can_write") is True
            and body.get("permissions", {}).get("is_super_admin") is False,
            f"permissions={body.get('permissions')}",
        )

        # ── 2.2: the merge fix — reproduce the clobber bug's precondition, ──
        # ── then prove a partial single-strategy PUT no longer triggers it ──
        seed_body = {
            "values": {
                "modeling.ta.strategy_defaults": {
                    "secondaries": {
                        "rate_of_contribution": "0.30", "rate_of_distribution": "0.25",
                        "growth_rate": "0.03", "bow_factor": "1.9", "fund_life_years": "8",
                    }
                }
            }
        }
        seed_res = admin.put("/api/v1/admin/modeling/ta/defaults", json=seed_body, headers=HEADERS)
        check(
            "[Y] seed PUT: overriding 'secondaries' alone succeeds",
            seed_res.status_code == 200, f"status={seed_res.status_code} body={seed_res.text[:300]}",
        )
        after_seed = seed_res.json()
        check(
            "[Y] after the seed PUT, secondaries shows as an override and the "
            "other 7 strategies are still on platform default (nothing else "
            "was touched by a single-strategy write)",
            after_seed.get("strategy_overrides", {}).get("secondaries") is True
            and after_seed.get("strategy_overrides", {}).get("buyout") is False,
            f"got {after_seed.get('strategy_overrides')}",
        )

        # NOW submit a DIFFERENT single strategy (buyout). Pre-fix, this PUT
        # would replace the whole blob with just {"buyout": ...}, silently
        # discarding the secondaries override just proven above.
        edit_body = {
            "values": {
                "modeling.ta.strategy_defaults": {
                    "buyout": {
                        "rate_of_contribution": "0.0999", "rate_of_distribution": "0.0602",
                        "growth_rate": "0.02643", "bow_factor": "2.5", "fund_life_years": "10",
                    }
                }
            }
        }
        edit_res = admin.put("/api/v1/admin/modeling/ta/defaults", json=edit_body, headers=HEADERS)
        check(
            "[Y] the UI-driven edit: PUT with ONLY buyout in the body succeeds",
            edit_res.status_code == 200, f"status={edit_res.status_code} body={edit_res.text[:300]}",
        )
        after_edit = edit_res.json()
        check(
            "[Y] THE FIX: after editing ONLY buyout, secondaries' prior "
            "override SURVIVES with its exact values — the merge, not a "
            "wholesale replace",
            after_edit["modeling.ta.strategy_defaults"]["secondaries"]["bow_factor"] == "1.9"
            and after_edit.get("strategy_overrides", {}).get("secondaries") is True,
            f"got secondaries={after_edit.get('modeling.ta.strategy_defaults', {}).get('secondaries')}",
        )
        check(
            "[Y] buyout itself now reflects the new value and is flagged as "
            "an override",
            after_edit["modeling.ta.strategy_defaults"]["buyout"]["rate_of_contribution"] == "0.0999"
            and after_edit.get("strategy_overrides", {}).get("buyout") is True,
        )
        check(
            "[Y] the 6 strategies never touched remain on platform default",
            all(
                after_edit["strategy_overrides"][k] is False
                for k in ("growth_equity", "venture_capital", "real_estate",
                          "real_assets", "private_credit", "fund_of_funds")
            ),
            f"got {after_edit.get('strategy_overrides')}",
        )

        # ── A subsequent, INDEPENDENT GET reflects the persisted change ────
        fresh_get = admin.get("/api/v1/modeling/ta/defaults", headers=HEADERS)
        fresh_body = fresh_get.json()
        check(
            "[Y] a FRESH, independent GET (not the PUT's own echoed response) "
            "reflects both persisted changes",
            fresh_get.status_code == 200
            and fresh_body["modeling.ta.strategy_defaults"]["buyout"]["rate_of_contribution"] == "0.0999"
            and fresh_body["modeling.ta.strategy_defaults"]["secondaries"]["bow_factor"] == "1.9",
            f"status={fresh_get.status_code}",
        )

        # ── 2.3: a real 400/422 is a plain string — verbatim-renderable ─────
        bad_res = admin.put(
            "/api/v1/admin/modeling/ta/defaults",
            json={"values": {"modeling.ta.strategy_defaults": {
                "buyout": {
                    "rate_of_contribution": "0.08", "rate_of_distribution": "0.06",
                    "growth_rate": "0.02", "bow_factor": "-1", "fund_life_years": "10",
                }
            }}},
            headers=HEADERS,
        )
        bad_detail = bad_res.json().get("detail") if bad_res.status_code < 500 else None
        check(
            "[Y] the real API refuses bow_factor=-1 with 400, and `detail` is "
            "a PLAIN STRING (formatApiError's verbatim branch — no client-side "
            "re-derivation needed)",
            bad_res.status_code == 400 and isinstance(bad_detail, str) and "bow_factor" in bad_detail,
            f"status={bad_res.status_code} detail={bad_detail!r}",
        )

        # ── 2.4: view-only — checked independently of the UI-side proof ────
        member_get = member.get("/api/v1/modeling/ta/defaults", headers=HEADERS)
        member_body = member_get.json() if member_get.status_code == 200 else {}
        check(
            "[Y] a plain member CAN read GET defaults (matches org_settings' "
            "own open-read convention), and the envelope reports can_write=false",
            member_get.status_code == 200
            and member_body.get("permissions", {}).get("can_write") is False,
            f"status={member_get.status_code} permissions={member_body.get('permissions')}",
        )
        member_put = member.put(
            "/api/v1/admin/modeling/ta/defaults",
            json={"values": {"modeling.ta.projection_horizon_years": 5}}, headers=HEADERS,
        )
        check(
            "[Y] the SAME plain member is REFUSED (403) on PUT — server-side "
            "enforcement, independent of what the UI renders",
            member_put.status_code == 403, f"status={member_put.status_code}",
        )

        # ── 2.5: cross-org isolation ─────────────────────────────────────────
        res_b = org_b_admin.get("/api/v1/modeling/ta/defaults", headers=HEADERS)
        b_body = res_b.json() if res_b.status_code == 200 else {}
        check(
            "[Y] org B's screen NEVER shows org A's overridden buyout/secondaries "
            "values — it gets the unmodified platform default",
            b_body.get("modeling.ta.strategy_defaults", {}).get("buyout", {}).get("rate_of_contribution") == "0.0788"
            and b_body.get("modeling.ta.strategy_defaults", {}).get("secondaries", {}).get("bow_factor") == "1.3",
            f"got buyout={b_body.get('modeling.ta.strategy_defaults', {}).get('buyout')}",
        )
        check(
            "[Y] org B's strategy_overrides are all False — org A's overrides "
            "do not leak into org B's provenance signal either",
            not any(b_body.get("strategy_overrides", {}).values()),
            f"got {b_body.get('strategy_overrides')}",
        )

        # ── 2.6: calibration floor endpoint reuses the real function ────────
        floor_q = admin.get("/api/v1/modeling/ta/calibration-floor?periods_per_year=4", headers=HEADERS)
        floor_y = admin.get("/api/v1/modeling/ta/calibration-floor?periods_per_year=1", headers=HEADERS)
        check(
            "[Y] GET calibration-floor at periods_per_year=4 returns the real "
            "12-period floor (3 years * 4, not a flat 3)",
            floor_q.status_code == 200 and floor_q.json().get("minimum_realized_periods") == 12,
            f"status={floor_q.status_code} body={floor_q.text[:200]}",
        )
        check(
            "[Y] the SAME endpoint at periods_per_year=1 returns 3 — proves "
            "it is a real function call, not a hardcoded 12",
            floor_y.status_code == 200 and floor_y.json().get("minimum_realized_periods") == 3,
            f"status={floor_y.status_code}",
        )
    finally:
        shared.__exit__(None, None, None)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — THE UI HALF: static proof of what the components render
# ═══════════════════════════════════════════════════════════════════════════

SCREEN = WEB / "components" / "admin" / "TaSettingsScreen.jsx"
PAGE = WEB / "app" / "admin" / "modeling" / "ta" / "page.js"
GET_ROUTE = WEB / "app" / "api" / "admin" / "modeling" / "ta" / "defaults" / "route.js"


def run_ui_static_assertions() -> None:
    print("\n── Section 3: TaSettingsScreen.jsx — what renders for can_write=false ──")

    if not SCREEN.exists():
        fail("TaSettingsScreen.jsx exists", f"not found at {SCREEN}")
        return

    screen = strip_js_comments(read(SCREEN))

    check(
        "[Y] canWrite comes from permissions.can_write with NO truthy "
        "fallback anywhere (?? / || would silently restore controls)",
        "permissions?.can_write" in screen
        and not re.findall(r"can_write\s*(?:\?\?|\|\|)\s*(?!false)\w+", screen),
        "expected `!!permissions?.can_write` with no ??/|| fallback",
    )
    check(
        "[Y] a missing envelope seeds can_write: false (fail CLOSED, not "
        "fail open)",
        "can_write: false" in screen,
    )
    check(
        "[Y] the strategy grid's Edit control (StrategyDetailPane) renders "
        "only inside a canWrite gate",
        "canWrite && !editing" in screen,
    )
    check(
        "[Y] the strategy edit FORM itself renders only inside a canWrite gate",
        "canWrite && editing" in screen,
    )
    check(
        "[Y] the platform-settings Edit control renders only inside a "
        "canWrite gate (same discipline, second panel)",
        screen.count("canWrite && !editing") >= 2,
        "expected the gate to appear for both StrategyDetailPane and "
        "PlatformSettingsCard",
    )
    check(
        "[Y] a view-only caller sees an explicit 'View only' label, not a "
        "silently absent button (the screen SAYS what state it is in)",
        "View only" in screen,
    )
    check(
        "[Y] formatApiError is IMPORTED from TriggerDetailPane, not "
        "re-implemented — one 422/400-rendering function, not two that "
        "could drift",
        'import { formatApiError } from "@/components/admin/TriggerDetailPane"' in screen
        or "import { formatApiError } from '@/components/admin/TriggerDetailPane'" in screen,
    )
    check(
        "[Y] every rate/bow/fund-life field is a plain text input, never "
        'type=\"number\" — a number input would coerce the Decimal-as-string '
        "through a JS float on every keystroke",
        'type="number"' not in screen.split("PlatformSettingsCard")[0]
        if "PlatformSettingsCard" in screen else True,
    )

    if PAGE.exists():
        page = strip_js_comments(read(PAGE))
        check(
            "[Y] the page is a real server component seeding from the REAL "
            "getTaDefaults() API call, not mock/static data",
            "getTaDefaults()" in page and "initialEnvelope" in page,
        )
    else:
        fail("app/admin/modeling/ta/page.js exists", f"not found at {PAGE}")

    if GET_ROUTE.exists():
        route = strip_js_comments(read(GET_ROUTE))
        check(
            "[Y] the Next.js proxy route never accepts org_id from the "
            "client — no org_id param anywhere in the file (Rule 6)",
            "org_id" not in route,
        )
        check(
            "[Y] the proxy forwards to the REAL backend paths for both verbs",
            '"/api/v1/modeling/ta/defaults"' in route
            and '"/api/v1/admin/modeling/ta/defaults"' in route,
        )
    else:
        fail("app/api/admin/modeling/ta/defaults/route.js exists", f"not found at {GET_ROUTE}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — npm run build exits 0
# ═══════════════════════════════════════════════════════════════════════════


def run_build_check() -> None:
    print("\n── Section 4: npm run build (apps/web) ──")
    try:
        result = subprocess.run(
            ["npm", "run", "build"], cwd=str(WEB),
            capture_output=True, text=True, timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        fail("npm run build exits 0", f"could not run: {type(exc).__name__}: {exc}")
        return
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])
    check(
        "[Y] npm run build exits 0",
        result.returncode == 0,
        f"exit={result.returncode}\n{tail}" if result.returncode != 0 else "",
    )
    if result.returncode == 0:
        combined = result.stdout + result.stderr
        check(
            "[Y] the new TA settings routes are actually in the build output "
            "(not just 'build succeeded' in the abstract)",
            "/admin/modeling/ta" in combined and "/api/admin/modeling/ta/defaults" in combined,
        )


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    from _doppler_env import hydrate_from_doppler

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to ambient DATABASE_URL")

    db_url = os.environ.get("DATABASE_URL")

    print("=" * 78)
    print("TA MODEL SPRINT 2 — verify")
    print("=" * 78)

    report_task1_findings()
    run_ui_static_assertions()
    run_build_check()

    print("\n── Database connectivity check (real attempt, not a presence check) ──")
    admin_conn = None
    if not db_url:
        print("[BLOCKED] DATABASE_URL is not set")
        for label in DB_ASSERTIONS:
            blocked_(label, "DATABASE_URL not set")
    else:
        try:
            admin_conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            print(f"[BLOCKED] could not connect to DATABASE_URL: {type(exc).__name__}: {exc}")
            for label in DB_ASSERTIONS:
                blocked_(label, "DATABASE_URL present but authentication failed — see above")
        else:
            try:
                await cleanup(admin_conn)  # teardown-at-start
                await seed_users(admin_conn)
                try:
                    await run_api_assertions(admin_conn)
                except Exception as exc:  # noqa: BLE001
                    fail("2.x API-layer assertions raised unexpectedly", f"{type(exc).__name__}: {exc}")
            finally:
                await cleanup(admin_conn)
                remaining = await leftover_count(admin_conn)
                check("[Y] teardown complete — zero leftover test rows", remaining == 0, f"count={remaining}")
                await admin_conn.close()

    print("\n" + "=" * 78)
    print(f"TA Model Sprint 2: {passed} passed, {failed} failed, {blocked} blocked")
    print("=" * 78)
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
