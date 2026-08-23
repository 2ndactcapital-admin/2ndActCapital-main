"""Propose, review and confirm the target of an unresolved underlying edge.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
──────────────────────────────────────────────────────────────────────────────
:func:`propose_resolution` cannot resolve anything. :func:`confirm_resolution`
is the only function in the codebase that writes ``link_state='resolved'``, and
it is reachable only from a Super-Admin-gated endpoint.

That separation is not maintained by convention. ``propose_resolution`` writes
``link_state='ambiguous'`` and puts its guess in ``proposed_global_security_id``
— a different column from ``to_global_security_id``, whose meaning is pinned by
``sec_global_rel_resolved_has_target`` to "resolved's target". And underneath
both, ``trg_sec_global_rel_confirm_gate`` rejects ANY transition into
'resolved' unless ``app.underlying_confirm='true'`` is set LOCAL in the
transaction. Only :func:`confirm_resolution` sets it. A future refactor that
accidentally points the proposal pipeline at the wrong column gets a database
error, not a silently auto-approved corpus.

Confidence is deliberately not a number. A percentage invites a threshold, and a
threshold is an auto-approval rule wearing a disguise. There are two values:
``high`` (an exact hit in a hand-written table) and ``needs_manual_match``
(everything else). Neither one resolves anything on its own.

WHAT GETS PROPOSED, IN ORDER
──────────────────────────────────────────────────────────────────────────────
1. Exact hit in :mod:`services.underlying_index_registry` -> propose that index.
   61 of the live 97 edges.
2. 'Common Stock of X' / 'Class A Common Stock of X' -> extract X as a HINT for
   the reviewer. No ticker lookup, no proposal. NVDA is obvious to a person and
   unsafe for a matcher: share classes, reassigned tickers, foreign private
   issuers.
3. An ETF or fund reference -> hint where the name is cleanly extractable
   ('shares of X'), otherwise none. A fund tracking an index is not the index,
   and resolving it to itself is problem 2 again.
4. A decrement / risk-control index -> flagged for the reviewer. These usually
   have no public series and sometimes no ticker; a placeholder row is still
   reasonable, but it gets created by a person pressing ``create_new``, not by
   this module deciding the security exists.
5. Anything else -> flagged, no hint.

Cases 2-5 all record ``needs_manual_match`` and leave ``link_state`` at
'unresolved'. They are still written: "the matcher looked at this and declined"
is information the queue should show, and it is different from "nobody has run
the matcher yet".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services.underlying_index_registry import (
    lookup_index,
    resolve_or_create_index_security,
)
from services.underlying_normalization import normalize_underlying_text

TABLE = "portfolio.securities_global_relationships"
SECURITIES_TABLE = "portfolio.securities_global"
TERMS_TABLE = "portfolio.securities_global_note_terms"
FILINGS_TABLE = "portfolio.reference_filings"

# Matches sec_global_rel_state_chk exactly.
RESOLVED = "resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"

# Matches sec_global_rel_proposal_confidence_chk exactly.
HIGH = "high"
NEEDS_MANUAL = "needs_manual_match"

# Matches sec_global_rel_proposal_kind_chk exactly.
KIND_INDEX = "known_index"
KIND_SINGLE_NAME = "single_name"
KIND_FUND = "fund_etf"
KIND_DECREMENT = "decrement_candidate"
KIND_UNCLASSIFIED = "unclassified"

# The transaction-local token the database trigger looks for. Named as a
# constant so the one place that sets it is greppable.
CONFIRM_GUC = "app.underlying_confirm"


class UnderlyingResolutionError(ValueError):
    """A propose/confirm/reject operation could not be performed as specified."""


class UnderlyingResolutionPermissionError(PermissionError):
    """A write was attempted without Super Admin."""


# ── Classification of everything the registry does not know ──────────────────
#
# These patterns produce a LABEL and sometimes a HINT. Neither is a resolution
# and nothing downstream branches on them except the review screen.

# 'Common Stock of NVIDIA Corporation', 'Class A Common Stock of Meta Platforms,
# Inc.'. Case-insensitive because the corpus contains both 'Common Stock' and
# 'common stock' for the same issuer — normalize_underlying_text preserves
# casing by design, so the insensitivity has to live here.
_SINGLE_NAME = re.compile(
    r"^(?:the\s+)?(?:class\s+[A-Za-z]\s+)?common\s+stock\s+of\s+(?P<name>.+?)\s*$",
    re.IGNORECASE,
)

# 'shares of iShares MSCI EAFE ETF'. Same shape as above — a reference to an
# instrument by name — so the name is worth extracting even though the
# instrument is a fund.
_SHARES_OF = re.compile(r"^(?:the\s+)?shares\s+of\s+(?P<name>.+?)\s*$", re.IGNORECASE)

_FUND_MARKERS = re.compile(r"\b(ETF|Fund|SPDR|iShares|VanEck)\b", re.IGNORECASE)

# Decrement / risk-control / synthetic-strategy indices. Every one of these
# tokens appears in a live corpus name that is NOT a plain published index:
# 'MerQube US Large-Cap Vol Advantage Index', 'S&P 500 Futures 40% Intraday 4%
# Decrement VT Index', 'Goldman Sachs Momentum Builder Focus ER Index'.
#
# Checked AFTER the registry, which is why 'S&P 500 Futures Excess Return Index'
# is unaffected — it is a registry hit and never reaches this test.
_DECREMENT_MARKERS = re.compile(
    r"\b(Decrement|Vol\s+Advantage|Risk\s+Control|Momentum\s+Builder|"
    r"Intraday|VT\s+Index|ER\s+Index|Excess\s+Return)\b",
    re.IGNORECASE,
)


def classify_underlying(normalized: str) -> tuple[str, str | None]:
    """Label a non-registry name and pull a reviewer hint where one is safe.

    Returns ``(kind, hint)``. ``hint`` is a company or instrument name lifted
    verbatim out of the string — never a ticker, never a lookup result. It
    exists so a reviewer starts from 'NVIDIA Corporation' instead of re-reading
    the prospectus phrase.
    """
    if not normalized:
        return KIND_UNCLASSIFIED, None

    match = _SINGLE_NAME.match(normalized)
    if match:
        return KIND_SINGLE_NAME, match.group("name").strip()

    match = _SHARES_OF.match(normalized)
    if match:
        name = match.group("name").strip()
        # 'shares of X' where X is a fund is still a fund; the phrasing does not
        # change what it is.
        kind = KIND_FUND if _FUND_MARKERS.search(name) else KIND_SINGLE_NAME
        return kind, name

    if _FUND_MARKERS.search(normalized):
        return KIND_FUND, None

    if _DECREMENT_MARKERS.search(normalized):
        return KIND_DECREMENT, None

    return KIND_UNCLASSIFIED, None


# ── The proposal ─────────────────────────────────────────────────────────────


@dataclass
class ProposalResult:
    """What :func:`propose_resolution` decided about one edge.

    ``link_state`` is the state the edge is in AFTER the call. It is included so
    a caller — and the verify script — can assert on it without a second query,
    and it will never be 'resolved': this dataclass is produced by a function
    that cannot produce that state.
    """

    relationship_id: str
    raw_underlying_text: str
    normalized_text: str
    confidence: str
    kind: str
    link_state: str
    proposed_global_security_id: str | None = None
    hint: str | None = None
    created_security: bool = False
    skipped: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_proposal(self) -> bool:
        """True when a concrete target was proposed for a human to confirm."""
        return self.proposed_global_security_id is not None


async def propose_resolution(
    pool, relationship_id: str, *, is_super_admin: bool = True
) -> ProposalResult:
    """Normalize one edge, match it against the closed set, record the outcome.

    NEVER writes ``link_state='resolved'``. A registry hit lands on 'ambiguous'
    with the target in ``proposed_global_security_id``; everything else stays
    'unresolved' with a confidence of ``needs_manual_match``.

    Idempotent. Re-running against an edge that already carries a proposal
    recomputes the same answer and rewrites the same columns. Re-running against
    an edge a human has already RESOLVED does nothing at all and returns
    ``skipped=True`` — re-proposing over a settled decision would be this
    pipeline overruling the person it exists to serve.

    ``is_super_admin`` is an explicit parameter checked FIRST, per the platform's
    escape-hatch convention: the flag is passed by a caller that has already
    verified it and is never inferred from ambient context. RLS on the table is
    the second line.
    """
    if not is_super_admin:
        raise UnderlyingResolutionPermissionError(
            "proposing a resolution requires Super Admin"
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT id, raw_underlying_text, link_state, to_global_security_id
            FROM {TABLE}
            WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL
            """,
            str(relationship_id),
        )
    if row is None:
        raise UnderlyingResolutionError(f"relationship {relationship_id} not found")

    raw = row["raw_underlying_text"]
    normalized = normalize_underlying_text(raw)

    if row["link_state"] == RESOLVED:
        return ProposalResult(
            relationship_id=str(row["id"]),
            raw_underlying_text=raw,
            normalized_text=normalized,
            confidence=HIGH,
            kind=KIND_INDEX,
            link_state=RESOLVED,
            skipped=True,
            detail={"reason": "already resolved by a human; left alone"},
        )

    entry = lookup_index(normalized)
    created = False

    if entry is not None:
        # Row count before and after is how "created" is decided — the registry
        # helper returns an id either way and asking it to also report novelty
        # would duplicate state it does not own.
        before = await _index_row_count(pool)
        target_id = await resolve_or_create_index_security(pool, normalized)
        created = await _index_row_count(pool) > before

        confidence, kind, hint = HIGH, KIND_INDEX, None
        new_state = AMBIGUOUS
        proposed = target_id
    else:
        kind, hint = classify_underlying(normalized)
        confidence = NEEDS_MANUAL
        # No target, so nothing to confirm, so not 'ambiguous'. The edge stays
        # 'unresolved' — which is the truth — and the queue picks it up on the
        # 'unresolved' half of its filter.
        new_state = UNRESOLVED
        proposed = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
            )
            # Note what is NOT in this SET clause: to_global_security_id. The
            # proposal never touches it. Note also that app.underlying_confirm
            # is not set — so even if $2 somehow arrived as 'resolved', the
            # database trigger would reject this statement.
            await conn.execute(
                f"""
                UPDATE {TABLE}
                SET link_state = $2,
                    normalized_underlying_text = $3,
                    proposal_confidence = $4,
                    proposal_kind = $5,
                    proposal_hint = $6,
                    proposed_global_security_id = $7::uuid,
                    proposed_at = now()
                WHERE id = $1::uuid AND link_state <> '{RESOLVED}'
                """,
                str(relationship_id), new_state, normalized,
                confidence, kind, hint, proposed,
            )

    return ProposalResult(
        relationship_id=str(relationship_id),
        raw_underlying_text=raw,
        normalized_text=normalized,
        confidence=confidence,
        kind=kind,
        link_state=new_state,
        proposed_global_security_id=proposed,
        hint=hint,
        created_security=created,
    )


async def _index_row_count(pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            f"""
            SELECT count(*) FROM {SECURITIES_TABLE}
            WHERE security_type = 'index'
              AND valid_to IS NULL AND system_to IS NULL
            """
        )


async def propose_all_unresolved(
    pool, *, is_super_admin: bool = True
) -> list[ProposalResult]:
    """Run :func:`propose_resolution` over every edge awaiting a decision.

    Covers 'unresolved' AND 'ambiguous': an already-proposed edge is re-proposed
    so a registry edit reaches edges the previous run declined. Resolved edges
    are excluded by the query, not merely skipped by the loop, so a large corpus
    does not pay for rows there is nothing to do to.

    Sequential on purpose. The whole corpus is under a hundred rows, the work is
    a dictionary lookup, and running it concurrently would introduce a race on
    ``resolve_or_create_index_security`` for no measurable gain.
    """
    if not is_super_admin:
        raise UnderlyingResolutionPermissionError(
            "proposing resolutions requires Super Admin"
        )
    async with pool.acquire() as conn:
        ids = [
            str(r["id"])
            for r in await conn.fetch(
                f"""
                SELECT id FROM {TABLE}
                WHERE link_state <> '{RESOLVED}'
                  AND valid_to IS NULL AND system_to IS NULL
                ORDER BY id
                """
            )
        ]
    return [
        await propose_resolution(pool, rel_id, is_super_admin=True) for rel_id in ids
    ]


# ── The confirm — the only writer of link_state='resolved' ───────────────────

# proposal_kind -> what a reviewer's ``create_new`` should create. Derived
# rather than accepted from the request body: the body carries a boolean, and
# letting a client also choose the security_type would make the created row's
# type a caller's assertion rather than a consequence of what the string says.
_CREATE_TYPE_BY_KIND: dict[str, str] = {
    KIND_INDEX: "index",
    KIND_DECREMENT: "index",
    KIND_SINGLE_NAME: "equity",
    KIND_FUND: "fund",
    KIND_UNCLASSIFIED: "other",
}


async def confirm_resolution(
    pool,
    relationship_id: str,
    *,
    actor_id: str,
    global_security_id: str | None = None,
    create_new: bool = False,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    """Settle one edge: THE only code path that sets ``link_state='resolved'``.

    Target selection, in order:

    * an explicit ``global_security_id`` — the reviewer overriding or accepting
      by id;
    * ``create_new=True`` — no such security exists yet, so make one from the
      normalized name (see ``_CREATE_TYPE_BY_KIND``);
    * otherwise the standing ``proposed_global_security_id``, i.e. the reviewer
      accepting what was proposed.

    The proposal is not erased on the way out. After a confirm, an edge carries
    BOTH what was proposed and what was chosen, so "did the reviewer agree with
    the matcher" is answerable from the row rather than from a log nobody keeps.

    An edge with none of the three is a 'nothing to confirm' error rather than a
    no-op success, because a confirm dialog that returns 200 having done nothing
    is how a queue silently stops draining.

    ``app.underlying_confirm`` is set LOCAL here and NOWHERE else in the
    codebase. That GUC is what ``trg_sec_global_rel_confirm_gate`` checks, so
    this function's exclusivity is a database fact and not a code-review
    promise.
    """
    if not is_super_admin:
        raise UnderlyingResolutionPermissionError(
            "confirming a resolution requires Super Admin"
        )
    if global_security_id and create_new:
        raise UnderlyingResolutionError(
            "pass global_security_id or create_new, not both — "
            "they name two different targets"
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT id, raw_underlying_text, normalized_underlying_text,
                   link_state, proposed_global_security_id, proposal_kind
            FROM {TABLE}
            WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL
            """,
            str(relationship_id),
        )
        if row is None:
            raise UnderlyingResolutionError(
                f"relationship {relationship_id} not found"
            )
        if row["link_state"] == RESOLVED:
            raise UnderlyingResolutionError(
                f"relationship {relationship_id} is already resolved"
            )

        target: str | None = None
        created = False

        if global_security_id:
            exists = await conn.fetchval(
                f"""
                SELECT id FROM {SECURITIES_TABLE}
                WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL
                """,
                str(global_security_id),
            )
            if exists is None:
                raise UnderlyingResolutionError(
                    f"global_security_id {global_security_id} is not a current security"
                )
            target = str(exists)
        elif create_new:
            target, created = await _create_security_for(conn, row)
        elif row["proposed_global_security_id"]:
            target = str(row["proposed_global_security_id"])

        if target is None:
            raise UnderlyingResolutionError(
                "nothing to confirm: no global_security_id, no create_new, "
                "and no standing proposal on this edge"
            )

        note = (
            f"resolved by {actor_id} from "
            f"{row['raw_underlying_text']!r}"
            + (" (new security created)" if created else "")
        )

        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
            )
            # THE confirm token. Nothing else in the codebase sets it, which is
            # what makes the trigger below a real gate rather than a formality.
            await conn.execute(
                f"SELECT set_config('{CONFIRM_GUC}', 'true', true)"
            )
            # proposed_global_security_id is deliberately LEFT IN PLACE. Once
            # to_global_security_id is set, the pair records something neither
            # column says alone: whether the reviewer accepted what was proposed
            # or overrode it. That is the only direct measure of whether the
            # matcher is any good, and clearing the column would erase it on the
            # exact rows where it means the most. (It also has to stay for
            # sec_global_rel_high_needs_proposed_target — a 'high' confidence
            # with no proposed target is, correctly, a contradiction.)
            await conn.execute(
                f"""
                UPDATE {TABLE}
                SET link_state = 'resolved',
                    to_global_security_id = $2::uuid,
                    resolved_by = $3::uuid,
                    resolved_at = now(),
                    resolution_notes = $4
                WHERE id = $1::uuid
                """,
                str(relationship_id), target, str(actor_id), note,
            )

    return {
        "relationship_id": str(relationship_id),
        "link_state": RESOLVED,
        "to_global_security_id": target,
        "created_security": created,
        "resolved_by": str(actor_id),
    }


async def _create_security_for(conn, row) -> tuple[str, bool]:
    """Make a ``securities_global`` row for an edge the registry could not place.

    The name is the NORMALIZED text, not the raw text: 'the MerQube US Large-Cap
    Vol Advantage Index' and 'MerQube US Large-Cap Vol Advantage Index' should
    not become two securities because two prospectuses punctuated differently.

    For index-typed creations this re-uses the existing row if one is already
    there, matching ``uq_sec_global_active_index_name`` — so a reviewer pressing
    ``create_new`` on the second of two identical edges links to the first
    reviewer's security rather than colliding with the unique index.

    price_coverage is 'no_public_source' for a decrement candidate and 'unknown'
    for everything else. That is a statement of fact about bank-sponsored
    strategy indices, not an optimistic default — and it tells a future price
    sprint not to waste a lookup.
    """
    name = row["normalized_underlying_text"] or normalize_underlying_text(
        row["raw_underlying_text"]
    )
    if not name:
        raise UnderlyingResolutionError(
            "cannot create a security from an empty underlying name"
        )
    kind = row["proposal_kind"] or KIND_UNCLASSIFIED
    security_type = _CREATE_TYPE_BY_KIND.get(kind, "other")
    coverage = "no_public_source" if kind == KIND_DECREMENT else "unknown"

    if security_type == "index":
        existing = await conn.fetchval(
            f"""
            SELECT id FROM {SECURITIES_TABLE}
            WHERE lower(name) = lower($1) AND security_type = 'index'
              AND valid_to IS NULL AND system_to IS NULL
            """,
            name,
        )
        if existing:
            return str(existing), False

    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
    new_id = await conn.fetchval(
        f"""
        INSERT INTO {SECURITIES_TABLE}
            (name, short_name, security_type, price_coverage)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        name, name[:120], security_type, coverage,
    )
    return str(new_id), True


# ── The reject ───────────────────────────────────────────────────────────────


async def reject_proposal(
    pool, relationship_id: str, *, actor_id: str, is_super_admin: bool = False
) -> dict[str, Any]:
    """Throw away a proposal the reviewer disagrees with.

    Returns the edge to a clean 'unresolved': no proposed target, no confidence,
    no kind, no hint. ``normalized_underlying_text`` SURVIVES — it is the
    normalizer's output, not the matcher's opinion, and re-deriving it costs
    nothing but losing it would make the queue's grouping flicker.

    Refuses to touch a resolved edge. Undoing a human resolution is a different
    act with different consequences (a downstream comparability calculation may
    already depend on it) and it should not arrive through a button labelled
    'reject proposal'.
    """
    if not is_super_admin:
        raise UnderlyingResolutionPermissionError(
            "rejecting a proposal requires Super Admin"
        )

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            f"""
            SELECT link_state FROM {TABLE}
            WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL
            """,
            str(relationship_id),
        )
        if state is None:
            raise UnderlyingResolutionError(
                f"relationship {relationship_id} not found"
            )
        if state == RESOLVED:
            raise UnderlyingResolutionError(
                "this edge is resolved; rejecting a proposal cannot undo a "
                "human resolution"
            )

        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
            )
            await conn.execute(
                f"""
                UPDATE {TABLE}
                SET link_state = '{UNRESOLVED}',
                    proposed_global_security_id = NULL,
                    proposal_confidence = NULL,
                    proposal_kind = NULL,
                    proposal_hint = NULL,
                    proposed_at = NULL,
                    resolution_notes = $2
                WHERE id = $1::uuid
                """,
                str(relationship_id),
                f"proposal rejected by {actor_id}; awaiting manual resolution",
            )

    return {
        "relationship_id": str(relationship_id),
        "link_state": UNRESOLVED,
        "proposed_global_security_id": None,
    }


# ── The queue ────────────────────────────────────────────────────────────────


async def load_queue(conn) -> list[dict[str, Any]]:
    """Every edge a human still has to look at, with the note that references it.

    The join is what makes the screen usable. An edge on its own says
    'Nasdaq-100 ® Technology Sector Index SM' and nothing else; joined through
    ``from_global_security_id`` it says which structured note referenced it,
    which issuer filed it and under which accession — so a reviewer resolving an
    ambiguous name can go read the sentence it came from.

    Readable with no org context: every table in this query carries a global
    ``SELECT USING (true)`` policy, because none of them has an org_id. These
    are public facts derived from SEC filings.
    """
    rows = await conn.fetch(
        f"""
        SELECT rel.id,
               rel.raw_underlying_text,
               rel.normalized_underlying_text,
               rel.link_state,
               rel.relationship_type,
               rel.proposal_confidence,
               rel.proposal_kind,
               rel.proposal_hint,
               rel.proposed_at,
               rel.proposed_global_security_id,
               prop.name          AS proposed_security_name,
               prop.security_type AS proposed_security_type,
               prop.price_coverage AS proposed_price_coverage,
               rel.from_global_security_id,
               note.name          AS note_name,
               t.id               AS note_terms_id,
               t.product_archetype,
               t.terms_status,
               f.cik, f.filer_name, f.form_type, f.filing_date,
               f.accession_number, f.source_url
        FROM {TABLE} rel
        JOIN {SECURITIES_TABLE} note ON note.id = rel.from_global_security_id
        LEFT JOIN {SECURITIES_TABLE} prop
               ON prop.id = rel.proposed_global_security_id
        LEFT JOIN {TERMS_TABLE} t
               ON t.global_security_id = note.id
              AND t.valid_to IS NULL AND t.system_to IS NULL
        LEFT JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
        WHERE rel.link_state IN ('{UNRESOLVED}', '{AMBIGUOUS}')
          AND rel.valid_to IS NULL AND rel.system_to IS NULL
        -- Proposals first: the cheap decisions, batched, so a reviewer clears
        -- 61 edges of index confirmations before starting on the hard tail.
        ORDER BY (rel.proposal_confidence = '{HIGH}') DESC NULLS LAST,
                 rel.normalized_underlying_text NULLS LAST,
                 rel.raw_underlying_text
        """
    )
    return [
        {
            "id": str(r["id"]),
            "raw_underlying_text": r["raw_underlying_text"],
            "normalized_underlying_text": r["normalized_underlying_text"],
            "link_state": r["link_state"],
            "relationship_type": r["relationship_type"],
            "proposal": {
                "confidence": r["proposal_confidence"],
                "kind": r["proposal_kind"],
                "hint": r["proposal_hint"],
                "proposed_at": r["proposed_at"],
                "global_security_id": (
                    str(r["proposed_global_security_id"])
                    if r["proposed_global_security_id"] else None
                ),
                "security_name": r["proposed_security_name"],
                "security_type": r["proposed_security_type"],
                "price_coverage": r["proposed_price_coverage"],
            } if r["proposal_confidence"] else None,
            "note": {
                "global_security_id": str(r["from_global_security_id"]),
                "name": r["note_name"],
                "note_terms_id": str(r["note_terms_id"]) if r["note_terms_id"] else None,
                "product_archetype": r["product_archetype"],
                "terms_status": r["terms_status"],
                "cik": r["cik"],
                "filer_name": r["filer_name"],
                "form_type": r["form_type"],
                "filing_date": r["filing_date"],
                "accession_number": r["accession_number"],
                "source_url": r["source_url"],
            },
        }
        for r in rows
    ]
