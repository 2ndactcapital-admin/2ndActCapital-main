"""Hollisworks marketing surface — firm search + contact-lead storage.

Two pre-auth concerns for the platform's own apex marketing site:

  * :func:`match_firm` — the shared Login/Enroll interstitial fuzzy-matches a
    typed firm name against ``organizations.name`` (NOT slug) and, on a confident
    single match, returns the org's REAL stored ``login_url`` / ``enroll_url``
    (per the caller's intent). Ambiguous or no-match returns a "clarify/retry"
    status — never a pick-list, never a guessed closest match.

  * :func:`store_contact` — persists a marketing contact-form submission.

Fuzzy matching is done in pure Python (:mod:`difflib`) — ``pg_trgm`` is not
installed on this database, and Python matching keeps the confident/ambiguous
decision explicit and deterministic. The org list read is the same pre-auth
``SELECT`` window the tenant resolver uses (organizations_preauth_resolve).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# ── Hollisworks self-match special case ─────────────────────────────────────
# "Hollisworks" is the platform itself, NOT a tenant org (deliberately no seed
# row in `organizations`). Typing it into the interstitial routes to the
# platform admin host's login / enroll path. This is an explicit, narrow branch
# in the matcher — it is checked before any org fuzzy-match.
HOLLISWORKS_ADMIN_HOST = "admin.hollisworks.com"
HOLLISWORKS_LOGIN_URL = f"https://{HOLLISWORKS_ADMIN_HOST}/login"
HOLLISWORKS_ENROLL_URL = f"https://{HOLLISWORKS_ADMIN_HOST}/enroll"
# The apex the tenant subdomains hang off — used to synthesize a fallback
# destination for a matched org that has no explicit login_url/enroll_url yet.
PLATFORM_APEX = "hollisworks.com"

VALID_INTENTS = ("login", "enroll")

# Confidence thresholds for the confident/ambiguous/none decision.
STRONG_MATCH = 0.82   # top candidate must be at least this similar to be "confident"
WEAK_FLOOR = 0.60     # below this a candidate is ignored entirely
AMBIGUOUS_MARGIN = 0.10  # if the runner-up is within this of the top, it's ambiguous


def _normalize(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for robust compare.

    Deliberately drops common corporate suffixes' punctuation but keeps the
    words, so "Acme Advisors, LLC" and "Acme Advisors LLC" normalize alike.
    """
    if not isinstance(name, str):
        return ""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)  # punctuation -> space
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _is_hollisworks(normalized_query: str) -> bool:
    """True when the typed name is the platform itself (narrow special case)."""
    # Collapse spaces so "hollis works" == "hollisworks"; require an exact hit on
    # the collapsed token so a real firm named e.g. "Hollis Wealth" does NOT trip
    # this branch.
    collapsed = normalized_query.replace(" ", "")
    return collapsed == "hollisworks"


def _destination(org: dict, intent: str) -> str:
    """Resolve a matched org's real redirect target for the intent.

    Prefers the org's stored ``login_url`` / ``enroll_url``. Falls back to the
    firm's own ``<slug>.hollisworks.com`` path when the column is unset, so a
    real tenant (e.g. 2nd Act, whose URLs are currently NULL) still lands
    somewhere correct rather than dead-ending.
    """
    stored = org.get("login_url") if intent == "login" else org.get("enroll_url")
    if stored:
        return stored
    slug = org.get("slug")
    path = "login" if intent == "login" else "enroll"
    return f"https://{slug}.{PLATFORM_APEX}/{path}"


async def match_firm(conn, query: str, intent: str) -> dict:
    """Fuzzy-match ``query`` to an org and resolve a redirect for ``intent``.

    Returns a dict with ``status`` in ``{"matched", "ambiguous", "none",
    "invalid"}``:
      * ``matched``   → ``redirect_url`` (+ ``org_name``) is populated.
      * ``ambiguous`` → several firms are plausible; ask the user to be specific.
      * ``none``      → no firm is close enough; ask them to retry.
      * ``invalid``   → empty query or unknown intent.

    NEVER returns a pick-list and NEVER guesses the closest firm on an
    ambiguous/weak match. ``conn`` must be a pre-auth pool connection.
    """
    if intent not in VALID_INTENTS:
        return {"status": "invalid", "message": "Unknown intent.", "redirect_url": None}

    q = _normalize(query or "")
    if not q:
        return {
            "status": "invalid",
            "message": "Enter your firm's name to continue.",
            "redirect_url": None,
        }

    # Special case FIRST: the platform itself → admin host (per intent).
    if _is_hollisworks(q):
        return {
            "status": "matched",
            "org_name": "Hollisworks",
            "redirect_url": (
                HOLLISWORKS_LOGIN_URL if intent == "login" else HOLLISWORKS_ENROLL_URL
            ),
            "message": None,
            "is_platform": True,
        }

    rows = await conn.fetch("SELECT id, name, slug, login_url, enroll_url FROM organizations")
    scored = []
    for r in rows:
        org = dict(r)
        score = _ratio(q, _normalize(org["name"]))
        if score >= WEAK_FLOOR:
            scored.append((score, org))
    scored.sort(key=lambda t: t[0], reverse=True)

    if not scored:
        return {
            "status": "none",
            "message": (
                "We couldn't find a firm by that name. Check the spelling and try "
                "again, or reach us at hello@hollisworks.com."
            ),
            "redirect_url": None,
        }

    top_score, top_org = scored[0]

    # An exact (normalized) name is always confident, regardless of neighbours.
    exact = top_score >= 0.999

    if not exact and top_score < STRONG_MATCH:
        # Best candidate is only weakly similar — do not guess.
        return {
            "status": "none",
            "message": (
                "We couldn't confidently match that firm name. Try entering it as "
                "it appears on your account, or reach us at hello@hollisworks.com."
            ),
            "redirect_url": None,
        }

    # Confident tier: check the runner-up. If a second firm is nearly as close,
    # it's genuinely ambiguous — ask the user to disambiguate (no pick-list).
    if not exact and len(scored) > 1:
        second_score = scored[1][0]
        if second_score >= STRONG_MATCH and (top_score - second_score) < AMBIGUOUS_MARGIN:
            return {
                "status": "ambiguous",
                "message": (
                    "More than one firm matches that name. Please enter your firm's "
                    "full, exact name to continue."
                ),
                "redirect_url": None,
            }

    return {
        "status": "matched",
        "org_name": top_org["name"],
        "redirect_url": _destination(top_org, intent),
        "message": None,
        "is_platform": False,
    }


async def store_contact(conn, *, name, firm, email, aum, note, source_host) -> str:
    """Persist a marketing contact-form submission; return the new row id.

    org_id is intentionally absent — a prospect is pre-tenant. The INSERT runs
    in the pre-auth RLS window (marketing_contacts_preauth_insert carve-out).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO marketing_contacts (name, firm, email, aum, note, source_host)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        name, firm, email, aum or None, note or None, source_host or None,
    )
    return str(row["id"])
