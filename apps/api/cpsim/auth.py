"""cpsim01 — the simulator's OWN authentication: its fake keys only.

``Authorization: Bearer cpsim_live_…``. A Hollisworks session cookie or Auth0 JWT is never consulted — main.py's auth
middleware passes /cpsim through untouched, and a JWT presented here is just a bearer value without the ``cpsim_``
prefix: refused (401) before any lookup. The comparison itself (salted sha256, ``hmac.compare_digest`` per stored
credential) lives in cpsim.registry.authenticate / cpsim.generators.matches.
"""

from __future__ import annotations

#: auth outcome → the message a refused caller sees (never echoes what was presented)
REFUSALS = {
    "missing": "Authorization: Bearer <simulator key> is required",
    "malformed": "Authorization must be 'Bearer <simulator key>'",
    "not_simulator_key": "not a simulator key (simulator keys start with cpsim_)",
    "unknown_key": "unknown key",
    "revoked": "this key has been rotated or revoked",
    "disabled": "this simulated counterparty is not active",
}


def bearer(header: str | None) -> str | None:
    """The bearer value, or None when absent. A present header that is not ``Bearer <value>`` yields '' (refused as
    not a simulator key)."""
    if not header:
        return None
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()
