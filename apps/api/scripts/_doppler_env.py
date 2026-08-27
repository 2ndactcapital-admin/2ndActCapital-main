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
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

DOPPLER_DOWNLOAD_URL = (
    "https://api.doppler.com/v3/configs/config/secrets/download?format=json"
)

# The Doppler CLI persists a token here even when DOPPLER_TOKEN is absent from
# the environment. It is a CLI token (``dp.ct.…``), which is account-scoped
# rather than bound to one config — so the download URL must name the project
# and config explicitly or the request 404s.
DOPPLER_CLI_CONFIG = pathlib.Path.home() / ".doppler" / ".doppler.yaml"
DOPPLER_CLI_SCOPE = "/mnt/c/Users/Joe/2ndActCapital/apps/api"


def _token_from_cli_config() -> tuple[str, str | None, str | None]:
    """Read (token, project, config) out of the Doppler CLI's on-disk YAML.

    Hand-parsed: the two-space-indented ``scoped:`` map is simple enough that
    pulling in a YAML dependency for it would be the larger cost. Returns empty
    strings rather than raising when the file is missing or shaped differently.
    """
    try:
        text = DOPPLER_CLI_CONFIG.read_text()
    except OSError:
        return "", None, None

    token = ""
    project = config = None
    current_scope = None
    for line in text.splitlines():
        scope = re.match(r"^\s{4}(\S.*?):\s*$", line)
        if scope:
            current_scope = scope.group(1)
            continue
        field = re.match(r"^\s{8}([\w.\-]+):\s*(\S+)\s*$", line)
        if not field or current_scope is None:
            continue
        key, value = field.group(1), field.group(2)
        if key == "token" and not token:
            token = value
        elif current_scope == DOPPLER_CLI_SCOPE and key == "enclave.project":
            project = value
        elif current_scope == DOPPLER_CLI_SCOPE and key == "enclave.config":
            config = value
    return token, project, config


def hydrate_from_doppler(*, overwrite: bool = True, timeout: float = 20.0) -> tuple[list[str], str | None]:
    """Load Doppler's secrets into ``os.environ``.

    Returns ``(names_loaded, error)``. ``overwrite`` defaults to True on
    purpose: the whole point is that the ambient value may be the stale one, so
    "don't clobber what's already set" would preserve exactly the bug.
    """
    url = DOPPLER_DOWNLOAD_URL
    token = (os.environ.get("DOPPLER_TOKEN") or "").strip()
    if not token:
        token, project, config = _token_from_cli_config()
        if not token:
            return [], "DOPPLER_TOKEN is not set and no CLI token is on disk"
        if project and config:
            url += "&" + urllib.parse.urlencode({"project": project, "config": config})

    request = urllib.request.Request(
        url,
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
