#!/usr/bin/env python3
"""
LiteLLM PROXY_ADMIN diagnostic — run with: doppler run -- python3 litellm_diagnose.py

Pulls LITELLM_MASTER_KEY and LITELLM_BASE_URL fresh from the real environment
(via doppler run --) at execution time — never hardcode a key into this file,
never paste output containing a real key value back into chat.

Runs a sequence of targeted tests to triangulate WHY the master key is being
recognized as role=internal_user instead of PROXY_ADMIN, and prints a clear,
human-readable report at the end.
"""
import os
import sys
import json
import urllib.request
import urllib.error

BASE_URL = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

if not BASE_URL or not MASTER_KEY:
    print("FATAL: LITELLM_BASE_URL or LITELLM_MASTER_KEY not set in this environment.")
    print("Run this via: doppler run -- python3 litellm_diagnose.py")
    sys.exit(1)

print(f"Base URL: {BASE_URL}")
print(f"Master key length: {len(MASTER_KEY)} (value not printed)")
print("=" * 70)

results = []


def call(method, path, headers=None, body=None, label=""):
    """Make a real HTTP call, capture status + body, never raise."""
    url = f"{BASE_URL}{path}"
    hdrs = headers or {}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            text = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        text = e.read().decode(errors="replace")
    except Exception as e:
        status = None
        text = f"TRANSPORT ERROR: {e}"
    results.append((label, method, path, status, text[:300]))
    return status, text


print("\n[1] Health check — confirms the service is reachable at all")
call("GET", "/health/liveliness", label="health")

print("\n[2] Self-identity check — /key/info with NO key param, using master key as auth")
print("    Many LiteLLM-style proxies treat this as 'tell me about myself'")
call("GET", "/key/info", headers={"Authorization": f"Bearer {MASTER_KEY}"}, label="self-info")

print("\n[3] /model/info — a READ admin route (lower stakes than /model/new)")
call("GET", "/model/info", headers={"Authorization": f"Bearer {MASTER_KEY}"}, label="model-info-read")

print("\n[4] /user/list — a DIFFERENT admin route, to see if the failure is uniform")
call("GET", "/user/list", headers={"Authorization": f"Bearer {MASTER_KEY}"}, label="user-list")

print("\n[5] /model/new with a COMPLETE, valid body — isolates auth failure from validation failure")
call("POST", "/model/new", headers={"Authorization": f"Bearer {MASTER_KEY}"},
     body={"model_name": "diagnose-test", "litellm_params": {"model": "anthropic/claude-haiku-4-5-20251001", "api_key": "os.environ/ANTHROPIC_API_KEY"}},
     label="model-new-complete")

print("\n[6] Same call, NO auth header at all — does 'no key' differ from 'wrong-role key'?")
call("POST", "/model/new", headers={},
     body={"model_name": "diagnose-test-noauth", "litellm_params": {"model": "anthropic/claude-haiku-4-5-20251001"}},
     label="model-new-noauth")

print("\n[7] Same call, master key in lowercase 'authorization' header (case sensitivity check)")
call("POST", "/model/new", headers={"authorization": f"Bearer {MASTER_KEY}"},
     body={"model_name": "diagnose-test-lc", "litellm_params": {"model": "anthropic/claude-haiku-4-5-20251001"}},
     label="model-new-lowercase-header")

print("\n[8] Master key with a trailing/leading whitespace stripped explicitly (defensive)")
call("POST", "/model/new", headers={"Authorization": f"Bearer {MASTER_KEY.strip()}"},
     body={"model_name": "diagnose-test-stripped", "litellm_params": {"model": "anthropic/claude-haiku-4-5-20251001"}},
     label="model-new-stripped-key")

print("\n" + "=" * 70)
print("REPORT")
print("=" * 70)
for label, method, path, status, text in results:
    print(f"\n--- {label} ({method} {path}) ---")
    print(f"status: {status}")
    print(f"body:   {text}")

print("\n" + "=" * 70)
print("INTERPRETATION GUIDE")
print("=" * 70)
print("""
- If [2] (self-info) returns real key data with a role field: read that role
  directly — it's the server's own self-report, the most direct signal.
- If [3] succeeds but [4]/[5] fail: the bug is route-specific, not a blanket
  master-key recognition failure — worth naming which routes are affected.
- If [6] (no auth) gives a DIFFERENT error than [5] (master key): the key IS
  being recognized as SOME valid credential, just not elevated — points at
  a role-resolution bug, not a key-matching failure.
- If [6] and [5] give the IDENTICAL error: the master key isn't being
  recognized as anything special at all — points at a key-matching failure
  (the running process may not have the value we think it has).
- If [7] or [8] succeed where the original failed: header casing or
  whitespace was the actual cause all along.
""")
