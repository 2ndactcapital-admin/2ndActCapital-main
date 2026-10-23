"""Custodian capabilities — what each custodian route can actually do (rep2).

PLATFORM data (``public.custodian_capabilities``, owner_scope 'platform',
org_id NULL): every org reads the same record; only Super Admin writes it.
VERSIONED on the valid axis: an edit closes the live version (valid_to) and
records version_no + 1 with its own evidence, as-of date and recorder — the
old version is kept, and a trigger refuses any other change.

THE ONE RULE EVERY CALLER FOLLOWS: ``'unknown'`` IS NOT AVAILABLE. A
capability nobody has evidenced is treated exactly like a 'no'. :func:`available`
is the single place that decides it; callers never compare strings themselves.
A missing capability DOWNGRADES a plan (core holdings only, or a manual
instruction) — it never blocks it.

Custodian keys are lower-case route keys ('altruist', 'manual'), the same keys
the trade-adapter registry uses (services.custody.registry.trade_custodian_key
maps an account's custodian_code onto one).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

T_CAP = "public.custodian_capabilities"

#: Ordered — the matrix renders in this order. Labels are the server's
#: vocabulary (published in every envelope); nothing on the client holds them.
CAPABILITY_LABELS: dict[str, str] = {
    "fractional_shares": "Fractional shares",
    "equity_trading_api": "Equity trading API",
    "model_api": "Model API",
    "trade_list_upload": "Trade-list upload",
    "options_level": "Options level (0–4)",
    "flex_options": "FLEX options",
    "index_options": "Index options",
    "margin": "Margin",
    "short_selling": "Short selling",
    "in_kind_transfers": "In-kind transfers",
    "custodian_tax_loss_harvesting": "Custodian tax-loss harvesting",
    "lot_level_cost_basis_feed": "Lot-level cost-basis feed",
    "held_away_aggregation": "Held-away aggregation",
}
CAPABILITY_KEYS = tuple(CAPABILITY_LABELS)

CUSTODIAN_LABELS: dict[str, str] = {
    "altruist": "Altruist",
    "manual": "Manual — execution instruction",
}

VALUE_LABELS: dict[str, str] = {
    "yes": "Yes", "no": "No", "unknown": "Unknown",
    "0": "Level 0", "1": "Level 1", "2": "Level 2", "3": "Level 3", "4": "Level 4",
}
YES_NO_VALUES = ("yes", "no", "unknown")
OPTIONS_LEVEL_VALUES = ("0", "1", "2", "3", "4", "unknown")

EVIDENCE_KIND_LABELS: dict[str, str] = {
    "document": "Document", "url": "URL", "assumed": "Assumed",
}


class CapabilityError(Exception):
    pass


class CapabilityPermissionError(CapabilityError):
    pass


@dataclass(frozen=True)
class Capability:
    value: str
    evidence: str
    as_of: date | None
    evidence_kind: str = "assumed"
    version_no: int | None = None

    def as_tuple(self) -> tuple[str, str, date | None]:
        return (self.value, self.evidence, self.as_of)


def values_for(key: str) -> tuple[str, ...]:
    if key not in CAPABILITY_LABELS:
        raise CapabilityError(f"unknown capability {key!r}")
    return OPTIONS_LEVEL_VALUES if key == "options_level" else YES_NO_VALUES


def available(value: str | Capability | None, *, at_least: int = 1) -> bool:
    """THE rule: only an evidenced positive is available. 'unknown' → False.

    For ``options_level`` a value is available when it is a level >= ``at_least``.
    """
    v = value.value if isinstance(value, Capability) else value
    if v is None or v == "unknown":
        return False
    if v == "yes":
        return True
    if v.isdigit():
        return int(v) >= at_least
    return False


async def capability(conn, custodian_code: str, key: str) -> Capability:
    """``(value, evidence, as_of)`` of the live version. A custodian or key with
    no row is 'unknown' with that stated as its evidence — never a guess."""
    values_for(key)
    row = await conn.fetchrow(
        f"SELECT value, evidence, evidence_kind, as_of, version_no FROM {T_CAP} WHERE custodian_code = $1 "
        "AND capability_key = $2 AND valid_to IS NULL AND system_to IS NULL", custodian_code, key)
    if row is None:
        return Capability("unknown", f"no capability record for {custodian_code!r}", None)
    return Capability(row["value"], row["evidence"], row["as_of"], row["evidence_kind"], row["version_no"])


def _row_out(r) -> dict:
    return {
        "id": str(r["id"]), "custodian_code": r["custodian_code"], "capability_key": r["capability_key"],
        "value": r["value"], "available": available(r["value"]), "evidence_kind": r["evidence_kind"],
        "evidence": r["evidence"], "as_of": r["as_of"].isoformat() if r["as_of"] else None,
        "version_no": r["version_no"], "recorded_by": str(r["recorded_by"]) if r["recorded_by"] else None,
        "note": r["note"], "valid_from": r["valid_from"].isoformat() if r["valid_from"] else None,
        "valid_to": r["valid_to"].isoformat() if r["valid_to"] else None,
    }


async def matrix(conn) -> dict:
    """Every custodian × every capability (live versions). A missing cell is
    reported as 'unknown' with ``recorded: false`` rather than skipped."""
    rows = await conn.fetch(f"SELECT * FROM {T_CAP} WHERE valid_to IS NULL AND system_to IS NULL "
                            "ORDER BY custodian_code, capability_key")
    by: dict[str, dict] = {}
    for r in rows:
        by.setdefault(r["custodian_code"], {})[r["capability_key"]] = {**_row_out(r), "recorded": True}
    codes = sorted(set(by) | set(CUSTODIAN_LABELS), key=lambda c: (c not in CUSTODIAN_LABELS, c))
    out = []
    for code in codes:
        cells = []
        for key in CAPABILITY_KEYS:
            cell = by.get(code, {}).get(key)
            cells.append(cell or {"custodian_code": code, "capability_key": key, "value": "unknown",
                                  "available": False, "evidence_kind": "assumed", "evidence": "no record",
                                  "as_of": None, "version_no": None, "recorded": False})
        out.append({"custodian_code": code, "label": CUSTODIAN_LABELS.get(code, code), "cells": cells})
    return {"custodians": out}


async def history(conn, custodian_code: str, key: str) -> list[dict]:
    values_for(key)
    rows = await conn.fetch(f"SELECT * FROM {T_CAP} WHERE custodian_code = $1 AND capability_key = $2 "
                            "AND system_to IS NULL ORDER BY version_no DESC", custodian_code, key)
    return [_row_out(r) for r in rows]


async def set_capability(conn, *, custodian_code: str, key: str, value: str, evidence_kind: str, evidence: str,
                         as_of: date, actor_id, actor_org_id, is_super_admin: bool, note: str | None = None) -> dict:
    """Record a new version (Super Admin only). Closes the live version and
    inserts version_no + 1 in ONE transaction; writes an audit_log row in the
    editor's org. The caller's connection must carry the super-admin RLS
    context (the platform write policy), which the API's middleware sets."""
    if not is_super_admin:
        raise CapabilityPermissionError("Super Admin required")
    if value not in values_for(key):
        raise CapabilityError(f"{CAPABILITY_LABELS[key]} takes one of {list(values_for(key))}, not {value!r}")
    if evidence_kind not in EVIDENCE_KIND_LABELS:
        raise CapabilityError(f"evidence kind must be one of {list(EVIDENCE_KIND_LABELS)}")
    if not (evidence or "").strip():
        raise CapabilityError("evidence is required — a document, a URL, or the word 'assumed' with why")
    if evidence_kind == "url" and not evidence.strip().lower().startswith(("http://", "https://")):
        raise CapabilityError("a URL evidence must start with http:// or https://")
    if not custodian_code or not custodian_code.replace("_", "").isalnum() or custodian_code != custodian_code.lower():
        raise CapabilityError("custodian code must be lower-case letters, digits and underscores")
    async with conn.transaction():
        live = await conn.fetchrow(
            f"SELECT id, version_no FROM {T_CAP} WHERE custodian_code = $1 AND capability_key = $2 "
            "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", custodian_code, key)
        version = 1
        if live is not None:
            await conn.execute(f"UPDATE {T_CAP} SET valid_to = now() WHERE id = $1", live["id"])
            version = live["version_no"] + 1
        row = await conn.fetchrow(
            f"""INSERT INTO {T_CAP} (custodian_code, capability_key, value, evidence_kind, evidence, as_of,
                    version_no, recorded_by, note)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::uuid, $9) RETURNING *""",
            custodian_code, key, value, evidence_kind, evidence.strip(), as_of, version, str(actor_id), note)
        await conn.execute(
            "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
            "VALUES ($1::uuid, $2::uuid, 'custodian_capability.set', 'custodian_capability', $3::uuid, $4::jsonb)",
            str(actor_org_id), str(actor_id), str(row["id"]),
            json.dumps({"custodian_code": custodian_code, "capability_key": key, "value": value,
                        "evidence_kind": evidence_kind, "version_no": version,
                        "previous_version_id": str(live["id"]) if live else None}))
    return _row_out(row)


def vocabularies() -> dict:
    return {
        "capabilities": [{"key": k, "label": v, "values": list(values_for(k))} for k, v in CAPABILITY_LABELS.items()],
        "custodians": [{"key": k, "label": v} for k, v in CUSTODIAN_LABELS.items()],
        "values": [{"key": k, "label": v} for k, v in VALUE_LABELS.items()],
        "evidence_kinds": [{"key": k, "label": v} for k, v in EVIDENCE_KIND_LABELS.items()],
        "unknown_rule": "Unknown is treated as not available.",
    }
