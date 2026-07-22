"""Class labelling rules for SPVs under one investment (Sprint 23).

A deal (the Investment) may be subdivided into economic Classes — Class A,
Class B — each a separate `spvs` row with its own carry / mgmt fee / close
date.  "Series" is reserved for the Delaware legal compartment and is
deliberately not used here.

Rules enforced here, and enforced in the API layer via these helpers rather
than in the frontend:
  * First SPV under a deal: class_label optional (single-class investment).
  * Second and later SPVs: class_label required.
  * (deal_id, class_label) is unique — spvs_deal_class_label_uniq.
"""
from uuid import UUID

# Excel-style column names: A..Z, then AA, AB, …  Enough for any real deal;
# the sequence is unbounded so the suggestion never runs out.
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class ClassLabelError(ValueError):
    """Raised when a class_label violates the labelling rules."""


def normalize_class_label(value):
    """Trim and upper-case a label; blank/whitespace-only becomes None."""
    if value is None:
        return None
    trimmed = str(value).strip()
    return trimmed.upper() if trimmed else None


def _label_at(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', 27 -> 'AB', …"""
    out = ""
    n = index
    while True:
        out = _ALPHABET[n % 26] + out
        n = n // 26 - 1
        if n < 0:
            return out


def suggest_next_label(existing_labels) -> str:
    """First label in the A, B, C… sequence not already taken under the deal."""
    taken = {normalize_class_label(x) for x in existing_labels if x is not None}
    i = 0
    while True:
        candidate = _label_at(i)
        if candidate not in taken:
            return candidate
        i += 1


async def deal_class_state(conn, org_id, deal_id) -> dict:
    """Current class picture for a deal — drives both the form and the guard.

    org_id comes from the authenticated request, never from a request body.
    """
    rows = await conn.fetch(
        """
        SELECT id, name, class_label
        FROM spvs
        WHERE deal_id = $1 AND org_id = $2
        ORDER BY class_label ASC NULLS FIRST, created_at ASC
        """,
        deal_id if not isinstance(deal_id, str) else UUID(deal_id),
        org_id if not isinstance(org_id, str) else UUID(org_id),
    )
    labels = [r["class_label"] for r in rows]
    return {
        "deal_id": deal_id,
        "spv_count": len(rows),
        "existing_labels": [x for x in labels if x is not None],
        # Required only once the deal already holds at least one SPV: adding a
        # second vehicle is what turns a single-class deal into a multi-class one.
        "class_label_required": len(rows) >= 1,
        "suggested_class_label": suggest_next_label(labels),
    }


async def resolve_class_label(conn, org_id, deal_id, class_label):
    """Validate a proposed class_label for a new SPV under `deal_id`.

    Returns the normalized label (or None for a legitimately unlabelled first
    class).  Raises ClassLabelError with a member-readable message otherwise.
    """
    label = normalize_class_label(class_label)
    state = await deal_class_state(conn, org_id, deal_id)

    if state["class_label_required"] and label is None:
        raise ClassLabelError(
            "This deal already has an SPV, so the new one needs a class label "
            f"(e.g. Class {state['suggested_class_label']}). "
            "Classes let one investment carry different fee, carry, or close terms."
        )

    if label is not None and label in {
        normalize_class_label(x) for x in state["existing_labels"]
    }:
        raise ClassLabelError(
            f"Class {label} already exists on this deal. "
            f"Try Class {state['suggested_class_label']}."
        )

    return label
