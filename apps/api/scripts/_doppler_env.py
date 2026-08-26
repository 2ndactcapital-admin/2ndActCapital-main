"""Hydrate os.environ from Doppler over its HTTPS API. Never prints a value.

WHY THIS EXISTS: the working database credentials live in Doppler. The copies in
``apps/api/.env`` and ``~/.bashrc`` are STALE and their passwords are rejected
by Postgres — that stale copy is what produced four sprints of false-green
"blocked on credentials" results. The Doppler CLI is not always invocable in an
unattended run, but ``DOPPLER_TOKEN`` is present in the environment, and the
same secrets are readable over the REST API with nothing but stdlib.

Values fetched here are written into ``os.environ`` and returned only as NAMES.
No value is ever printed, logged or returned as a string by this module.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DOPPLER_DOWNLOAD_URL = (
    "https://api.doppler.com/v3/configs/config/secrets/download?format=json"
)


def hydrate_from_doppler(*, overwrite: bool = True, timeout: float = 20.0) -> tuple[list[str], str | None]:
    """Load Doppler's secrets into ``os.environ``.

    Returns ``(names_loaded, error)``. ``overwrite`` defaults to True on
    purpose: the whole point is that the ambient value may be the stale one, so
    "don't clobber what's already set" would preserve exactly the bug.
    """
    token = (os.environ.get("DOPPLER_TOKEN") or "").strip()
    if not token:
        return [], "DOPPLER_TOKEN is not set"

    request = urllib.request.Request(
        DOPPLER_DOWNLOAD_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The body can echo the token back; report the status only.
        return [], f"Doppler API returned HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return [], f"Doppler API unreachable: {type(exc).__name__}"

    if not isinstance(payload, dict):
        return [], "Doppler API returned an unexpected payload shape"

    loaded: list[str] = []
    for key, value in payload.items():
        if value is None:
            continue
        if overwrite or not os.environ.get(key):
            os.environ[key] = str(value)
            loaded.append(key)
    return sorted(loaded), None
