"""Task 5 — register the candidate reader models on the LiteLLM proxy and add
each to platform_model_catalog as 'available' ONLY after a real call through
the proxy succeeds (the rule Jev followed).

Keys live ONLY in Doppler's prd_lite_llm config and are referenced as
``os.environ/NAME`` — no key value is read, written or printed here. A model
whose key NAME is missing from prd_lite_llm is reported BLOCKED and skipped;
the operator adds the key (and confirms prd_lite_llm syncs to the proxy's
Render service) and re-runs.

No OpenRouter. No litellm SDK. One deployment per name (a second deployment
under the same name would load-balance), so an existing name is reused, never
duplicated.

    python3 apps/api/scripts/register_note_extraction_models.py            # dry run (default)
    python3 apps/api/scripts/register_note_extraction_models.py --apply    # register + verify
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request

import _note_extraction_common as common

# deployment name -> (upstream litellm model, key NAME in prd_lite_llm, provider, display)
CANDIDATES = (
    ("gpt-oss-120b", "deepinfra/openai/gpt-oss-120b", "DEEPINFRA_API_KEY", "deepinfra", "gpt-oss-120b (DeepInfra)"),
    ("gemini-2.5-flash-lite", "gemini/gemini-2.5-flash-lite", "GEMINI_API_KEY", "gemini", "Gemini 2.5 Flash-Lite"),
    ("mistral-small-24b", "deepinfra/mistralai/Mistral-Small-3.2-24B-Instruct-2506", "DEEPINFRA_API_KEY",
     "deepinfra", "Mistral Small 3.2 24B (DeepInfra)"),
    ("gpt-5-nano", "openai/gpt-5-nano", "OPENAI_API_KEY", "openai", "GPT-5 nano"),
    ("gpt-5-mini", "openai/gpt-5-mini", "OPENAI_API_KEY", "openai", "GPT-5 mini"),
    ("deepseek-v3.2", "deepinfra/deepseek-ai/DeepSeek-V3.2", "DEEPINFRA_API_KEY", "deepinfra",
     "DeepSeek V3.2 (DeepInfra)"),
    ("qwen2.5-7b", "deepinfra/Qwen/Qwen2.5-7B-Instruct", "DEEPINFRA_API_KEY", "deepinfra",
     "Qwen2.5 7B Instruct (DeepInfra) — long shot"),
)
LITE_CONFIG = "prd_lite_llm"


def lite_llm_key_names() -> set[str]:
    from _doppler_env import _token_from_cli_config

    tok = os.environ.get("DOPPLER_TOKEN") or _token_from_cli_config()[0]
    req = urllib.request.Request(
        f"https://api.doppler.com/v3/configs/config/secrets/names?project=hollisworks&config={LITE_CONFIG}",
        headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return set(json.load(r).get("names", []))


async def real_call(name: str) -> tuple[bool, str, str | None]:
    from services.note_extraction import proxy

    resp = await proxy.chat({"model": name, "max_tokens": 16,
                             "messages": [{"role": "user", "content": "Reply with the single word: ready"}]})
    if resp.status != 200 or not resp.body:
        return False, f"HTTP {resp.status}: {resp.error or resp.text[:200]}", None
    return True, "ok", resp.body.get("model")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from services.database import platform_scope
    from services.litellm_credentials import _http
    from services.note_extraction import proxy

    conn = await common.connect()          # hydrates Doppler first
    names = lite_llm_key_names()
    catalog = proxy.deployment_catalog()
    results = []
    try:
        for name, upstream, key_name, provider, display in CANDIDATES:
            if key_name not in names:
                results.append((name, "BLOCKED", f"{key_name} is not in Doppler {LITE_CONFIG}"))
                continue
            dep = catalog.get(name)
            if dep is not None and dep.duplicate:
                results.append((name, "BLOCKED", "more than one deployment under this name (would load-balance)"))
                continue
            if not args.apply:
                results.append((name, "READY", f"would {'reuse' if dep else 'register'} {upstream} "
                                               f"with api_key os.environ/{key_name}, then call it"))
                continue
            if dep is None:
                status, body = _http("/model/new", method="POST", body={
                    "model_name": name,
                    "litellm_params": {"model": upstream, "api_key": f"os.environ/{key_name}"},
                    "model_info": {"description": f"noteextractb1 candidate reader — {display}"},
                })
                if status != 200:
                    results.append((name, "FAILED", f"POST /model/new -> HTTP {status}: {body[:200]}"))
                    continue
                catalog = proxy.deployment_catalog()
            ok, detail, reported = await real_call(name)
            if ok and not proxy.reported_model_matches(upstream, reported):
                ok, detail = False, f"provider reported '{reported}', not {upstream}"
            async with platform_scope(conn):
                await conn.execute(
                    """INSERT INTO platform_model_catalog (model_id, display_name, provider, availability)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (model_id) DO UPDATE SET availability = EXCLUDED.availability""",
                    name, display, provider, "available" if ok else "disabled")
            results.append((name, "AVAILABLE" if ok else "FAILED",
                            f"real call: {detail}; provider reported {reported!r}"))
    finally:
        await conn.close()
    for name, status, detail in results:
        print(f"[{status}] {name}: {detail}")
    if not args.apply:
        print("dry run — nothing registered, no provider called. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
