"""Custodian-profile registry — resolve an adapter from a ``custodian_code``.

TWO LOOKUPS, NOT ONE, AND THE SPLIT IS THE POINT
──────────────────────────────────────────────────────────────────────────────
``custodian_code`` is *data*. An org adds "Schwab's new institutional export"
or "Fidelity, but their operations team renamed three columns last quarter" by
editing a row in ``org_settings``. There is no deploy, no migration and no code
change, because a custodian code does not map to a class — it maps to a
**profile**::

    org_settings['custody.profiles'] = {
      "SCHWAB_CSV": {"adapter": "csv", "label": "…", "column_map": {…}},
      "FIDELITY_CSV": {"adapter": "csv", "label": "…", "column_map": {…}}
    }

``adapter`` is *code*. A genuinely new mechanism — an SFTP puller, a fixed-width
parser, a live REST client — registers a class under a new adapter key with
:func:`register_adapter`, and this file does not change either. The sprint's
requirement ("a second custodian profile must be addable later without touching
this sprint's code") is satisfied at both layers, and the hardcoded ``if
custodian_code == …`` chain has nowhere to grow.

WHY THE DEFAULTS ARE HERE AND NOT IN A MIGRATION
──────────────────────────────────────────────────────────────────────────────
A brand-new org has no ``custody.profiles`` row. Falling back to
:data:`DEFAULT_PROFILES` means the import screen works on day one instead of
presenting an empty custodian dropdown that looks like a bug. An org that saves
its own profiles shadows the defaults per-code, so a tenant can override
GENERIC_CSV's column map without losing the other built-ins.

Two errors, not one, because they have different fixes: an unregistered
custodian code is an org-settings problem the operator can fix from the admin
screen; a profile naming an adapter that no longer exists is a deployment
problem only an engineer can fix.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from services.custody.base import (
    PROFILES_SETTING_KEY,
    SALT_SETTING_KEY,
    CustodyAdapter,
    CustodyError,
)

# ── The code-side registry ───────────────────────────────────────────────
_ADAPTERS: dict[str, type[CustodyAdapter]] = {}


class UnknownCustodianError(CustodyError):
    """No profile is configured for this ``custodian_code``.

    Its own type so a caller (and the verify script) can distinguish "the org
    has not configured this custodian" — fixable in settings — from a genuine
    parse failure. Carries the codes that DO resolve, because the single most
    useful thing to say to someone who mistyped ``SCWHAB_CSV`` is the list.
    """

    def __init__(self, custodian_code: str, known: list[str]):
        self.custodian_code = custodian_code
        self.known = known
        super().__init__(
            f"no custodian profile is configured for {custodian_code!r}. "
            f"Configured codes: {known or '(none)'}. Add one under the "
            f"{PROFILES_SETTING_KEY!r} org setting — no code change is needed "
            f"for a new custodian that exports a CSV."
        )


class UnknownAdapterError(CustodyError):
    """The profile resolved, but names an adapter that is not registered.

    Distinct from UnknownCustodianError: the org's settings are reachable and
    well-formed, and the missing piece is code. Telling an operator to "check
    your custodian code" here would send them to fix something that is correct.
    """

    def __init__(self, custodian_code: str, adapter_key: str, known: list[str]):
        self.custodian_code = custodian_code
        self.adapter_key = adapter_key
        self.known = known
        super().__init__(
            f"custodian profile {custodian_code!r} names adapter "
            f"{adapter_key!r}, which is not registered. Registered adapters: "
            f"{known or '(none)'}. This is a deployment problem, not a "
            f"settings problem — the profile is fine."
        )


def register_adapter(key: str, adapter_class: type[CustodyAdapter]) -> None:
    """Register an adapter class under ``key``.

    Re-registering the same key with the same class is a no-op so that a module
    re-imported under two names (``services.custody.csv_adapter`` and a test's
    direct path import) does not trip the guard. Re-registering with a
    *different* class raises: two adapters silently fighting over one key would
    make which parser ran depend on import order.
    """
    existing = _ADAPTERS.get(key)
    if existing is not None and existing is not adapter_class:
        raise CustodyError(
            f"adapter key {key!r} is already registered to "
            f"{existing.__name__}; refusing to rebind it to "
            f"{adapter_class.__name__}"
        )
    adapter_class.adapter_key = key
    _ADAPTERS[key] = adapter_class


def registered_adapters() -> list[str]:
    return sorted(_ADAPTERS)


def get_adapter_class(adapter_key: str) -> type[CustodyAdapter]:
    try:
        return _ADAPTERS[adapter_key]
    except KeyError:
        raise UnknownAdapterError(
            "(direct lookup)", adapter_key, registered_adapters()
        ) from None


# ── The settings-side profiles ───────────────────────────────────────────
#
# Field names on the right of a column map are the RECORD field names in
# base.py, never database column names. The mapping the user edits is
# "which of my columns is the market value", not "which of my columns is
# account_balances_daily.total_market_value" — the storage shape is not
# something an operations person should have to know, and coupling the two
# would make a future column rename a data-migration of everyone's settings.

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "GENERIC_CSV": {
        "adapter": "csv",
        "label": "Generic CSV",
        "source_system": "CSV",
        "column_map": {
            "account": {
                "account_number": "account_number",
                "primary_entity_ref": "entity",
                "household_ref": "household",
                "registration_type": "registration_type",
                "tax_status": "tax_status",
                "service_model": "service_model",
                "is_billable": "is_billable",
                "is_discretionary": "is_discretionary",
                "is_held_away": "is_held_away",
                "opened_on": "opened_on",
                "base_currency": "base_currency",
            },
            "balance": {
                "account_number": "account_number",
                "as_of_date": "as_of_date",
                "total_market_value": "total_market_value",
                "cash_value": "cash_value",
                "margin_balance": "margin_balance",
                "accrued_income": "accrued_income",
            },
            "flow": {
                "account_number": "account_number",
                "flow_date": "flow_date",
                "amount": "amount",
                "flow_type": "flow_type",
                "is_billable_flow": "is_billable_flow",
            },
        },
    },
}


@dataclass(frozen=True)
class CustodyProfile:
    """A resolved custodian profile: which parser, and how its columns map."""

    custodian_code: str
    adapter_key: str
    label: str
    source_system: str
    column_map: dict[str, dict[str, str]]
    is_default: bool

    def adapter_class(self) -> type[CustodyAdapter]:
        adapter_class = _ADAPTERS.get(self.adapter_key)
        if adapter_class is None:
            raise UnknownAdapterError(
                self.custodian_code, self.adapter_key, registered_adapters()
            )
        return adapter_class


async def load_profiles(conn, org_id) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """``(profiles, codes_the_org_configured_itself)``.

    Read directly rather than through ``org_settings.get_setting`` so the
    defaults stay in this module. ``get_setting`` would fold "the org stored
    nothing" and "the org stored something identical to the default" into one
    answer, and the import screen wants to show which is which.
    """
    row = await conn.fetchrow(
        "SELECT setting_value FROM org_settings "
        "WHERE org_id = $1 AND setting_key = $2",
        str(org_id), PROFILES_SETTING_KEY,
    )
    profiles = dict(DEFAULT_PROFILES)
    org_codes: set[str] = set()
    if row is not None:
        import json

        value = row["setting_value"]
        stored = json.loads(value) if isinstance(value, str) else value
        if isinstance(stored, dict):
            for code, profile in stored.items():
                if isinstance(profile, dict):
                    profiles[code] = profile
                    org_codes.add(code)
    return profiles, org_codes


async def resolve_profile(conn, org_id, custodian_code: str) -> CustodyProfile:
    """Find the profile for ``custodian_code`` in this org, or raise.

    Raises :class:`UnknownCustodianError` for a code with no profile and
    :class:`UnknownAdapterError` for a profile whose adapter is not registered —
    both checked HERE, so a caller that only wants to validate a code does not
    have to construct an adapter (and read a file) to find out.
    """
    profiles, org_codes = await load_profiles(conn, org_id)
    profile = profiles.get(custodian_code)
    if profile is None:
        raise UnknownCustodianError(custodian_code, sorted(profiles))

    adapter_key = profile.get("adapter")
    if not adapter_key or adapter_key not in _ADAPTERS:
        raise UnknownAdapterError(
            custodian_code, adapter_key or "(unset)", registered_adapters()
        )

    column_map = profile.get("column_map") or {}
    return CustodyProfile(
        custodian_code=custodian_code,
        adapter_key=adapter_key,
        label=profile.get("label") or custodian_code,
        source_system=profile.get("source_system") or custodian_code,
        column_map={
            kind: dict(column_map.get(kind) or {})
            for kind in ("account", "balance", "flow")
        },
        is_default=custodian_code not in org_codes,
    )


async def build_adapter(
    conn,
    org_id,
    custodian_code: str,
    *,
    file_bytes: bytes,
    filename: str | None = None,
    column_map_override: dict[str, dict[str, str]] | None = None,
) -> tuple[CustodyAdapter, CustodyProfile]:
    """Resolve the profile and construct its adapter over ``file_bytes``.

    ``column_map_override`` is what the import UI's mapping step submits. It
    overrides per RECORD KIND, not per field: a partially-overridden map would
    silently mix the operator's new account mapping with the profile's stale one
    and produce a file that half-imports.
    """
    profile = await resolve_profile(conn, org_id, custodian_code)
    column_map = dict(profile.column_map)
    strict_kinds: set[str] = set()
    for kind, mapping in (column_map_override or {}).items():
        if mapping:
            column_map[kind] = dict(mapping)
            # Only the kinds the CALLER mapped are validated strictly. The
            # default profile maps more columns than any one custodian emits, on
            # purpose; the operator's own mapping was picked from this file's
            # real headers, so a missing column there is a genuine mistake.
            strict_kinds.add(kind)

    adapter = profile.adapter_class()(
        custodian_code=custodian_code,
        source_system=profile.source_system,
        file_bytes=file_bytes,
        filename=filename,
        column_map=column_map,
        strict_kinds=frozenset(strict_kinds),
    )
    return adapter, profile


# ── The per-org account-number salt ──────────────────────────────────────


async def get_or_create_salt(conn, org_id) -> str:
    """The org's account-number hash salt, minted on first use.

    NOT written through ``org_settings.set_setting``: that function enforces
    "caller must be org_admin", and minting a salt is a system action taken on
    behalf of whoever happens to run the first import. Requiring an admin to
    pre-create a random string before anyone can import would be a setup step
    with no security value — the salt is not a decision, it is entropy.

    ``is_public = false`` is set EXPLICITLY and is the load-bearing line in this
    function. The column defaults to **true**, and ``get_public_settings`` — the
    unauthenticated ``/theme/public`` endpoint that brands the login screen —
    serves every ``is_public`` row. Taking the default here would publish the
    salt to the internet, and the hash it protects is over account numbers short
    enough to brute-force once the salt is known.

    ``ON CONFLICT DO NOTHING`` + re-read rather than upsert: two concurrent
    first imports must converge on ONE salt. Whoever loses the race must adopt
    the winner's value, because a salt that changed after rows were written
    would re-hash every account number and detach every existing account from
    its own future imports.
    """
    row = await conn.fetchrow(
        "SELECT setting_value FROM org_settings "
        "WHERE org_id = $1 AND setting_key = $2",
        str(org_id), SALT_SETTING_KEY,
    )
    if row is not None:
        import json

        value = row["setting_value"]
        salt = json.loads(value) if isinstance(value, str) else value
        if isinstance(salt, str) and salt:
            return salt

    minted = secrets.token_hex(32)
    await conn.execute(
        """
        INSERT INTO org_settings
            (org_id, setting_key, setting_value, category, is_public, updated_at)
        VALUES ($1, $2, $3::jsonb, 'custody', false, now())
        ON CONFLICT (org_id, setting_key) DO NOTHING
        """,
        str(org_id), SALT_SETTING_KEY, f'"{minted}"',
    )
    stored = await conn.fetchval(
        "SELECT setting_value FROM org_settings "
        "WHERE org_id = $1 AND setting_key = $2",
        str(org_id), SALT_SETTING_KEY,
    )
    import json

    salt = json.loads(stored) if isinstance(stored, str) else stored
    if not isinstance(salt, str) or not salt:
        raise CustodyError(
            f"could not establish an account-number salt for org {org_id}"
        )
    return salt
