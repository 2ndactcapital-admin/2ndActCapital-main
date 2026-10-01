"""configure_typesafe_passthrough.py — register the /typesafe generic
pass-through on the LiteLLM proxy (ensemblesystemone.structural, Task 4).

HOW THIS DEPLOYMENT'S PROXY CONFIG IS STORED AND CHANGED (Task 1a, probed
live): ``hollisworks-litellm`` runs LiteLLM v1.96.2 (``/openapi.json``
info.version) with ``STORE_MODEL_IN_DB`` — there is no config.yaml the app
edits. Models AND pass-through endpoints live in the proxy's own database and
are changed through its admin API with the master key:

    GET    /config/pass_through_endpoint                 list
    POST   /config/pass_through_endpoint                 create
    DELETE /config/pass_through_endpoint?endpoint_id=…   delete

v1.96.2 has generic pass-through (``PassThroughGenericEndpoint``: path,
target, headers, include_subpath, auth — auth defaults to True). Native Jev
support (``/typesafe/{endpoint}`` with registry pricing) only exists from
v1.102.1; this is the generic route, so spend is NOT token-priced.

The TypeSafe key is NEVER sent from here. The header value is the literal
reference ``Bearer os.environ/TYPESAFE_API_KEY``; the PROXY resolves it from
its own environment (Doppler ``prd_lite_llm``). This script never reads,
prints or forwards the key, and never prints a header value it reads back.

Idempotent: if an endpoint with path /typesafe already exists with the right
shape, nothing is changed. A /typesafe endpoint with the WRONG shape is
reported, not silently replaced — pass ``--replace`` to delete and recreate.

Run:  python3 apps/api/scripts/configure_typesafe_passthrough.py [--replace]
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _doppler_env import hydrate_from_doppler  # noqa: E402

PATH = "/typesafe"
TARGET = "https://api.typesafe.ai"
# A reference the proxy resolves from ITS environment — not a secret value.
AUTH_HEADER_REF = "Bearer os.environ/TYPESAFE_API_KEY"

DESIRED = {
    "path": PATH,
    "target": TARGET,
    "headers": {"Authorization": AUTH_HEADER_REF},
    "include_subpath": True,
    "auth": True,
}


def _http(path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, str]:
    base = os.environ["LITELLM_BASE_URL"].rstrip("/")
    key = os.environ["LITELLM_MASTER_KEY"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def list_endpoints() -> list[dict]:
    status, body = _http("/config/pass_through_endpoint")
    if status != 200:
        raise RuntimeError(f"GET /config/pass_through_endpoint -> HTTP {status}")
    return json.loads(body).get("endpoints", [])


def describe(ep: dict) -> dict:
    """A printable summary. Header VALUES are never printed — only whether
    each one is an os.environ/ reference (a literal secret would not be)."""
    return {
        "id": ep.get("id"),
        "path": ep.get("path"),
        "target": ep.get("target"),
        "include_subpath": ep.get("include_subpath"),
        "auth": ep.get("auth"),
        "headers": {
            k: ("os.environ/ reference" if isinstance(v, str) and "os.environ/" in v
                else "LITERAL VALUE (not a reference)")
            for k, v in (ep.get("headers") or {}).items()
        },
    }


def matches(ep: dict) -> bool:
    return (
        ep.get("path") == PATH
        and (ep.get("target") or "").rstrip("/") == TARGET
        and bool(ep.get("include_subpath")) is True
        and ep.get("auth") is not False
        and (ep.get("headers") or {}).get("Authorization") == AUTH_HEADER_REF
    )


def main() -> int:
    names, err = hydrate_from_doppler()
    if err:
        print(f"[doppler] {err}")
        return 1
    replace = "--replace" in sys.argv[1:]

    existing = [ep for ep in list_endpoints() if ep.get("path") == PATH]
    for ep in existing:
        print("existing:", json.dumps(describe(ep)))
    if any(matches(ep) for ep in existing):
        print("OK: /typesafe pass-through already configured correctly — no change.")
        return 0
    if existing and not replace:
        print("REFUSING: a /typesafe endpoint exists with a different shape. "
              "Re-run with --replace to delete and recreate it.")
        return 2
    for ep in existing:
        status, _ = _http(
            f"/config/pass_through_endpoint?endpoint_id={ep.get('id') or PATH}",
            method="DELETE",
        )
        print(f"deleted existing /typesafe endpoint -> HTTP {status}")

    status, body = _http("/config/pass_through_endpoint", method="POST", body=DESIRED)
    print(f"POST /config/pass_through_endpoint -> HTTP {status}")
    if status not in (200, 201):
        print(body[:300])
        return 1
    after = [ep for ep in list_endpoints() if ep.get("path") == PATH]
    for ep in after:
        print("now:", json.dumps(describe(ep)))
    return 0 if any(matches(ep) for ep in after) else 1


if __name__ == "__main__":
    sys.exit(main())
