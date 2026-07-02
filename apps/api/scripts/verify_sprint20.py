"""verify_sprint20.py — Sprint 20: Public marketing site + /login redirect.

Checks (file-based; no DB writes, no interactive prompt):
  1. app/page.js exists and contains the hero headline text (public marketing page).
  2. proxy.js / auth0 middleware does NOT block unauthenticated requests to "/".
  3. app/login/page.js exists and redirects to /auth/login (Auth0 flow unchanged).
  4. Marketing page contains the three founding rules and the launch-version
     discretion statement; contains NO "nothing leaves" / zero-retention strings.
  5. ReferenceSelect.jsx: country 'CA' maps to 'ca_province'; 'US' maps to 'us_state'.
"""

import os
import sys

_ok = True

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
WEB = os.path.join(REPO_ROOT, "apps", "web")


def check(label: str, passed: bool) -> bool:
    global _ok
    mark = "[P]" if passed else "[F]"
    print(f"{mark} {label}")
    if not passed:
        _ok = False
    return passed


def read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


# ── Check 1: Public marketing page exists with hero headline ────────────────
page_path = os.path.join(WEB, "app", "page.js")
page_src = read_file(page_path)

check("Check 1: app/page.js exists", bool(page_src))
check(
    "Check 1: page.js contains hero headline ('post-liquidity')",
    "post-liquidity" in page_src,
)
check(
    "Check 1: authenticated users redirected to /dashboard",
    'redirect("/dashboard")' in page_src,
)
check(
    "Check 1: unauthenticated path renders marketing content (no blanket redirect when no session)",
    # Only one call-site redirect (guarded by 'if session') — page renders for unauthed visitors.
    # Count redirect(" to exclude the import statement.
    page_src.count('redirect("') == 1,
)

# ── Check 2: "/" is publicly accessible (not blocked in proxy/middleware) ───
proxy_path = os.path.join(WEB, "proxy.js")
proxy_src = read_file(proxy_path)

check("Check 2: proxy.js exists", bool(proxy_src))
# auth0.middleware without a custom block means all paths pass through;
# protection is opt-in per-page via getSession().
check(
    "Check 2: proxy.js uses auth0.middleware (passes through by default)",
    "auth0.middleware" in proxy_src,
)
check(
    "Check 2: proxy.js does not hard-block unauthenticated requests",
    # A blocking proxy would call auth0.protect() or throw/return 401 explicitly
    "auth0.protect" not in proxy_src and "return new Response" not in proxy_src,
)

# ── Check 3: /login redirects into Auth0 flow ───────────────────────────────
login_path = os.path.join(WEB, "app", "login", "page.js")
login_src = read_file(login_path)

check("Check 3: app/login/page.js exists", bool(login_src))
check(
    "Check 3: /login redirects to /auth/login (Auth0 flow)",
    "/auth/login" in login_src,
)
check(
    "Check 3: authenticated visitors at /login redirect to /dashboard",
    'redirect("/dashboard")' in login_src,
)

# In @auth0/nextjs-auth0 v4, /auth/callback and /auth/logout are handled
# automatically by auth0.middleware() in proxy.js — no filesystem routes needed.
check(
    "Check 3: Auth0 routes handled by auth0.middleware (no custom /auth/* files needed)",
    "auth0.middleware" in proxy_src,
)

# ── Check 4: Marketing page copy ────────────────────────────────────────────
# Founding rules
check(
    "Check 4: founding rule 01 (Sponsored entry only)",
    "Sponsored entry only" in page_src,
)
check(
    "Check 4: founding rule 02 (Bring value beyond capital)",
    "Bring value beyond capital" in page_src,
)
check(
    "Check 4: founding rule 03 (No assholes)",
    "No assholes" in page_src,
)

# Launch-version discretion statement (neutral — no gated claims)
check(
    "Check 4: launch-version discretion statement present",
    "Discretion is the foundation" in page_src,
)

# Prohibited strings — zero-retention / "nothing leaves" copy must not appear
PROHIBITED = [
    "nothing leaves",
    "zero data retention",
    "data never leaves",
    "fully private",
    "ZDR",
    "zero retention",
    "no data leaves",
]
for phrase in PROHIBITED:
    check(
        f"Check 4: prohibited phrase absent — '{phrase}'",
        phrase.lower() not in page_src.lower(),
    )

# ── Check 5: ReferenceSelect region mappings ─────────────────────────────────
ref_path = os.path.join(WEB, "components", "ReferenceSelect.jsx")
ref_src = read_file(ref_path)

check("Check 5: ReferenceSelect.jsx exists", bool(ref_src))
check(
    "Check 5: US -> us_state mapping present",
    "US" in ref_src and "us_state" in ref_src,
)
check(
    "Check 5: CA -> ca_province mapping present",
    "CA" in ref_src and "ca_province" in ref_src,
)
# Confirm they are on the same REGION_LIST object, not scattered strings
check(
    "Check 5: US and CA mappings are both in the REGION_LIST constant",
    'US: "us_state"' in ref_src and 'CA: "ca_province"' in ref_src,
)


# ── Result ───────────────────────────────────────────────────────────────────
if _ok:
    print("\nAll Sprint 20 checks passed.")
else:
    print("\nSome checks FAILED — see above.")
    sys.exit(1)
