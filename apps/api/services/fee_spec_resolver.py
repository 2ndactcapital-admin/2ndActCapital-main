"""FeeSpec references -> real database ids. Deterministic, org-scoped. fee40.

The model produces NAMES because it cannot see the database. This module turns
them into ids, and it is the only thing that does — the model is never shown a
list of candidate ids to pick from, because a model choosing between two
similarly-named households is a silent wrong-client bill with no trace.

THREE OUTCOMES, NEVER TWO
──────────────────────────────────────────────────────────────────────────────
  * RESOLVED       — exactly one row matches the name exactly (case-folded).
  * AMBIGUOUS      — more than one exact match, or only near matches. Every
                     candidate is returned for a human to choose from. Nothing
                     is picked.
  * UNRESOLVED     — nothing matched at all.

There is deliberately no "best guess" branch and no similarity threshold above
which one candidate wins. A trigram score of 0.9 between "Harrison Family
Trust" and "Harrison Family Trust II" is high confidence in the WRONG account,
and the two are exactly the pair a real advisory firm has.

CROSS-ORG
──────────────────────────────────────────────────────────────────────────────
Every query below carries ``org_id = $1`` in its WHERE clause, and ``org_id``
reaches this module only from ``get_org_id(request)`` — the caller's own
verified session — never from a request body. The RLS policies on these tables
are the second line, not the first: the application connects as a role whose
GUC is set per-request, and a resolver that leaned on RLS alone would resolve
whatever the GUC happened to hold.

[FIND] ``portfolio.securities_global`` HAS NO ``org_id``
──────────────────────────────────────────────────────────────────────────────
It is a global reference table by design (portfolio A1): a CUSIP is a public
fact, not a tenant's data. So a SECURITY reference resolves against two
different things with two different isolation stories, and they are reported
separately rather than merged into one candidate list:

  * ``portfolio.assets``            — org-scoped. Isolated like everything else.
  * ``portfolio.securities_global`` — global. Every org legitimately sees the
                                      same rows, and "cross-org isolation" is
                                      not a property it can have or violate.

Merging them would produce a candidate list where some entries are isolated and
some are not, with nothing on the row saying which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Mapping, Sequence

from services.fee_spec import NormalisedSpec, REFERENCE_KINDS

#: Rows returned per kind before the resolver stops offering candidates. A
#: disambiguation list longer than this is not a choice, it is a search result —
#: the advisor is told to narrow the name instead of being handed 200 rows.
MAX_CANDIDATES = 10


class FeeSpecResolutionError(ValueError):
    """A reference could not be processed at all (bad kind, empty name)."""

    code = "reference_unprocessable"


@dataclass
class Resolution:
    """What happened to one named reference."""

    ref: str
    kind: str
    name: str
    status: str                                   # resolved | ambiguous | unresolved
    id: str | None = None
    matched_name: str | None = None
    scope: str = "org"                            # org | global
    candidates: list[dict[str, Any]] = dc_field(default_factory=list)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref, "kind": self.kind, "name": self.name,
            "status": self.status, "id": self.id,
            "matched_name": self.matched_name, "scope": self.scope,
            "candidates": self.candidates, "reason": self.reason,
        }


@dataclass
class ResolutionReport:
    resolutions: list[Resolution] = dc_field(default_factory=list)

    @property
    def by_ref(self) -> dict[str, Resolution]:
        return {r.ref: r for r in self.resolutions}

    @property
    def resolved_ids(self) -> dict[str, str]:
        return {r.ref: r.id for r in self.resolutions if r.status == "resolved" and r.id}

    @property
    def disambiguation(self) -> list[dict[str, Any]]:
        """Every reference a human still has to choose for. Never auto-picked."""
        return [r.as_dict() for r in self.resolutions if r.status == "ambiguous"]

    @property
    def unresolved(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.resolutions if r.status == "unresolved"]

    @property
    def is_complete(self) -> bool:
        return all(r.status == "resolved" for r in self.resolutions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolutions": [r.as_dict() for r in self.resolutions],
            "disambiguation": self.disambiguation,
            "unresolved": self.unresolved,
            "is_complete": self.is_complete,
        }


# ── One query per kind. Each returns (id, label, secondary_label). ───────────
#
# ``$1`` is org_id and ``$2`` is the search term in EVERY org-scoped query
# below, without exception. The uniformity is the point: a reader checking that
# tenant isolation holds does not have to reason about five different parameter
# orders.

_ENTITY_SQL = """
    SELECT e.id::text AS id, e.display_name AS label, e.legal_name AS alt
    FROM entities e
    WHERE e.org_id = $1::uuid
      AND e.valid_to IS NULL AND e.system_to IS NULL
      AND (lower(e.display_name) LIKE $2 OR lower(coalesce(e.legal_name, '')) LIKE $2)
    ORDER BY e.display_name
    LIMIT $3
"""

_HOUSEHOLD_SQL = """
    SELECT h.id::text AS id, h.name AS label, NULL::text AS alt
    FROM households h
    WHERE h.org_id = $1::uuid AND lower(h.name) LIKE $2
    ORDER BY h.name
    LIMIT $3
"""

# Account numbers are stored masked and hashed — there is no plaintext column to
# match, so an advisor's "the Fidelity account ending 4417" matches against the
# mask itself and against the custodian's own id.
_ACCOUNT_SQL = """
    SELECT a.id::text AS id, a.account_number_masked AS label,
           a.custodian_code AS alt
    FROM accounts a
    WHERE a.org_id = $1::uuid
      AND a.valid_to IS NULL AND a.system_to IS NULL
      AND (lower(a.account_number_masked) LIKE $2
           OR lower(coalesce(a.custodian_account_id, '')) LIKE $2)
    ORDER BY a.account_number_masked
    LIMIT $3
"""

_BILLING_GROUP_SQL = """
    SELECT g.id::text AS id, g.name AS label, g.group_type AS alt
    FROM billing_groups g
    WHERE g.org_id = $1::uuid
      AND g.valid_to IS NULL AND g.system_to IS NULL
      AND lower(g.name) LIKE $2
    ORDER BY g.name
    LIMIT $3
"""

_ASSET_SQL = """
    SELECT s.id::text AS id, s.name AS label, s.short_name AS alt
    FROM portfolio.assets s
    WHERE s.org_id = $1::uuid
      AND s.valid_to IS NULL AND s.system_to IS NULL
      AND (lower(s.name) LIKE $2 OR lower(coalesce(s.short_name, '')) LIKE $2)
    ORDER BY s.name
    LIMIT $3
"""

# No org_id — see the module docstring. Its parameters are ($1 term, $2 limit),
# a DIFFERENT shape from every query above, which is deliberate: the missing
# org_id should look wrong at a glance rather than blend in.
_SECURITY_GLOBAL_SQL = """
    SELECT g.id::text AS id, g.name AS label, g.short_name AS alt
    FROM portfolio.securities_global g
    WHERE g.valid_to IS NULL AND g.system_to IS NULL
      AND g.merged_into_id IS NULL
      AND (lower(g.name) LIKE $1 OR lower(coalesce(g.short_name, '')) LIKE $1)
    ORDER BY g.name
    LIMIT $2
"""

_ORG_SCOPED_SQL: Mapping[str, str] = {
    "ENTITY": _ENTITY_SQL,
    "HOUSEHOLD": _HOUSEHOLD_SQL,
    "ACCOUNT": _ACCOUNT_SQL,
    "BILLING_GROUP": _BILLING_GROUP_SQL,
    "SECURITY": _ASSET_SQL,
}


def _like_term(name: str) -> str:
    """A LIKE pattern that is a CONTAINS, with the wildcards the user typed
    neutralised.

    ``%`` and ``_`` are escaped: an advisor writing "Smith_Trust" means an
    underscore, and leaving it as LIKE's single-character wildcard would widen
    the search silently — the widening direction, which produces extra
    candidates rather than none, so it would never be noticed as a bug.
    """
    escaped = re.sub(r"([%_\\])", r"\\\1", name.strip().lower())
    return f"%{escaped}%"


def _exact(rows: Sequence[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    target = name.strip().lower()
    return [
        r for r in rows
        if (r["label"] or "").strip().lower() == target
        or (r["alt"] or "").strip().lower() == target
    ]


async def resolve_reference(
    conn, org_id: str, *, ref: str, kind: str, name: str
) -> Resolution:
    """One name -> one Resolution. Never picks between candidates.

    An EXACT (case-folded) match on the primary or alternate label wins outright
    when there is exactly one of them, even if the same term also appears inside
    other names — "Smith Trust" resolving to "Smith Trust" rather than being
    called ambiguous against "Smith Trust II" is the behaviour an advisor
    expects and is safe precisely because it is exact.
    """
    kind = (kind or "").upper()
    if kind not in REFERENCE_KINDS:
        raise FeeSpecResolutionError(
            f"reference kind {kind!r} is not one of {list(REFERENCE_KINDS)}"
        )
    if not (name or "").strip():
        raise FeeSpecResolutionError(f"reference {ref!r} has no name to resolve")

    term = _like_term(name)
    rows = [dict(r) for r in await conn.fetch(
        _ORG_SCOPED_SQL[kind], org_id, term, MAX_CANDIDATES + 1
    )]
    scope = "org"

    # A SECURITY that is not one of this org's own assets may still be a global
    # instrument. Tried only after the org-scoped lookup comes back empty, so an
    # org's own asset always outranks the global row of the same name.
    if kind == "SECURITY" and not rows:
        rows = [dict(r) for r in await conn.fetch(
            _SECURITY_GLOBAL_SQL, term, MAX_CANDIDATES + 1
        )]
        scope = "global"

    truncated = len(rows) > MAX_CANDIDATES
    rows = rows[:MAX_CANDIDATES]

    if not rows:
        return Resolution(
            ref=ref, kind=kind, name=name, status="unresolved", scope=scope,
            reason=(
                f"no {kind.lower().replace('_', ' ')} in this organisation matches "
                f"{name!r}. It may not exist yet, or the name may differ from how "
                f"it is recorded."
            ),
        )

    exact = _exact(rows, name)
    if len(exact) == 1:
        row = exact[0]
        return Resolution(
            ref=ref, kind=kind, name=name, status="resolved", id=row["id"],
            matched_name=row["label"], scope=scope,
        )

    candidates = [
        {"id": r["id"], "label": r["label"], "alt": r["alt"], "scope": scope}
        for r in rows
    ]
    if len(exact) > 1:
        reason = (
            f"{len(exact)} records are named exactly {name!r}. Choose which one "
            f"this arrangement covers — nothing has been assumed."
        )
    else:
        reason = (
            f"{len(candidates)} record(s) resemble {name!r} but none matches it "
            f"exactly. Choose one, or correct the name."
            + (
                f" More than {MAX_CANDIDATES} matched; only the first "
                f"{MAX_CANDIDATES} are shown — narrow the name."
                if truncated else ""
            )
        )
    return Resolution(
        ref=ref, kind=kind, name=name, status="ambiguous", scope=scope,
        candidates=candidates, reason=reason,
    )


async def resolve_spec_references(
    conn, org_id: str, spec: NormalisedSpec
) -> ResolutionReport:
    """Resolve every reference the spec carries, in the order it declared them."""
    if not org_id:
        raise FeeSpecResolutionError(
            "org_id is required to resolve references — it comes from the "
            "caller's session, never from the request body"
        )
    report = ResolutionReport()
    for reference in spec.references:
        report.resolutions.append(await resolve_reference(
            conn, org_id,
            ref=reference["ref"], kind=reference["kind"], name=reference["name"],
        ))
    return report


def apply_resolutions(spec: NormalisedSpec, report: ResolutionReport) -> NormalisedSpec:
    """Write resolved ids onto the bundles that pointed at them, in place.

    An unresolved or ambiguous ``scope_ref`` leaves ``scope_id`` ABSENT rather
    than null-filled, and the row is marked ``needs_reference``. fee34's
    ``ScopeIdRequiredError`` then refuses it by name at save time — the same
    refusal an operator would get from the manual screen, rather than a second
    message invented here.
    """
    ids = report.resolved_ids
    for bundle in (spec.exclusions, spec.discounts, spec.credits):
        for row in bundle:
            ref = row.get("scope_ref")
            if not ref:
                continue
            if ref in ids:
                row["scope_id"] = ids[ref]
                row.pop("needs_reference", None)
            else:
                row.pop("scope_id", None)
                row["needs_reference"] = ref
    return spec


__all__ = [
    "MAX_CANDIDATES",
    "FeeSpecResolutionError",
    "Resolution",
    "ResolutionReport",
    "apply_resolutions",
    "resolve_reference",
    "resolve_spec_references",
]
