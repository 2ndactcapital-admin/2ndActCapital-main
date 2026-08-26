"""TASK 2 — add LITELLM_BASE_URL to Doppler hollisworks/prd. Prints no secret.

The URL itself is not a secret (it is a public Render hostname, already written
in render.yaml), so it is safe to echo. Nothing else is echoed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts._doppler_env import hydrate_from_doppler  # noqa: E402

TARGET = "https://hollisworks-litellm.onrender.com"

hydrate_from_doppler()
tok = os.environ["DOPPLER_TOKEN"]
proj = os.environ.get("DOPPLER_PROJECT", "hollisworks")
conf = os.environ.get("DOPPLER_CONFIG", "prd")

body = {"project": proj, "config": conf, "secrets": {"LITELLM_BASE_URL": TARGET}}
req = urllib.request.Request(
    "https://api.doppler.com/v3/configs/config/secrets",
    data=json.dumps(body).encode(), method="POST")
req.add_header("Authorization", f"Bearer {tok}")
req.add_header("Content-Type", "application/json")
req.add_header("Accept", "application/json")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"SET LITELLM_BASE_URL -> HTTP {r.status} (value: {TARGET})")
except urllib.error.HTTPError as e:
    # The error body can echo secret names but not values; print the status and
    # the message field only.
    try:
        msg = json.loads(e.read().decode()).get("messages") or "(no message)"
    except Exception:  # noqa: BLE001
        msg = "(unparseable body)"
    print(f"SET LITELLM_BASE_URL -> HTTP {e.code} {msg}")
    sys.exit(1)

# Re-read to prove it landed.
names, err = hydrate_from_doppler()
print("re-read: LITELLM_BASE_URL is "
      f"{'PRESENT' if 'LITELLM_BASE_URL' in names else 'STILL ABSENT'}")
print(f"re-read value matches target: {os.environ.get('LITELLM_BASE_URL') == TARGET}")
