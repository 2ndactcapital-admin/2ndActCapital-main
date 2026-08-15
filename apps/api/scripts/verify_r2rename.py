"""verify_r2rename.py — R2 bucket migration verifier (2ndactcapital-docs → hollisworks-docs).

Pass/fail output only. No interactive prompts. Idempotent. Read-only against R2
and Postgres — it NEVER writes to either bucket, so the source bucket is not
modified by running this. Teardown runs at start AND end (defensive; the script
seeds nothing, so teardown is a no-op guard).

The core assertion is a REAL FETCH through the application's own retrieval path
(services.storage.get_signed_url → HTTP GET), not an existence check: a copy that
silently missed objects passes "does the bucket exist" and FAILS this.

Honest gate: if R2 credentials are absent (they live only in Render, sync:false),
or boto3 is unavailable, this reports [BLOCKED] and exits non-zero. It does NOT
mock, simulate, or emit a false PASS.

Config (from environment, or apps/api/.env):
  R2_BUCKET_NAME     — the CONFIGURED (new) bucket. Expected 'hollisworks-docs'.
  R2_SOURCE_BUCKET   — the OLD source bucket to compare against. Required for the
                       count/size/content-parity assertions. Deliberately NOT
                       hardcoded here so this file contains no stale bucket literal.
  R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY — R2 credentials.
  DATABASE_URL       — Postgres (PgBouncer; statement_cache_size=0).
"""

import asyncio
import hashlib
import os
import subprocess
import sys

# Make the venv's site-packages importable (boto3, asyncpg live there).
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _py in ("python3.14", "python3.13", "python3.12", "python3.11"):
    _sp = os.path.join(_API_ROOT, "venv", "lib", _py, "site-packages")
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.insert(0, _sp)
sys.path.insert(0, _API_ROOT)  # so `services.storage` imports resolve

EXPECTED_NEW_BUCKET = "hollisworks-docs"
FALLBACK_LITERAL = "2ndactcapital" + "-docs"  # assembled, so this file holds no literal
MIN_DOCS = 10


def _load_env_from_dotenv():
    """Populate os.environ from apps/api/.env for keys not already set."""
    envp = os.path.join(_API_ROOT, ".env")
    if not os.path.exists(envp):
        return
    with open(envp) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _fail(msg):
    print(f"[FAIL] {msg}")


def _ok(msg):
    print(f"[PASS] {msg}")


def _blocked(msg):
    print(f"[BLOCKED] {msg}")


def _bucket_stats(client, bucket):
    """Return (object_count, total_bytes) for a bucket via paginated ListObjectsV2."""
    count = 0
    total = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            count += 1
            total += obj["Size"]
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return count, total


def _list_keys(client, bucket, limit=None):
    keys = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            keys.append(obj["Key"])
            if limit and len(keys) >= limit:
                return keys
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return keys


def _grep_no_hardcoded_literal():
    """Assert no remaining hardcoded source-bucket literal in apps/api or apps/web.

    Excludes docs/PROJECT_STATUS.md, the sprint prompt, historical migration
    files, and this verifier itself.
    """
    repo_root = os.path.abspath(os.path.join(_API_ROOT, "..", ".."))
    try:
        out = subprocess.run(
            ["grep", "-rIln", FALLBACK_LITERAL,
             os.path.join(repo_root, "apps", "api"),
             os.path.join(repo_root, "apps", "web")],
            capture_output=True, text=True,
        ).stdout
    except FileNotFoundError:
        return None, ["grep not available"]
    hits = []
    for line in out.splitlines():
        p = line.strip()
        if not p:
            continue
        base = os.path.basename(p)
        if base == "verify_r2rename.py":
            continue
        if "PROJECT_STATUS.md" in p:
            continue
        if "/migrations/" in p or "/migration/" in p:
            continue
        if p.endswith(".sprint.log") or "/sprint_prompts/" in p:
            continue
        hits.append(p)
    return (len(hits) == 0), hits


async def _fetch_document_rows(conn, source_client, source_bucket):
    """Fetch >= MIN_DOCS documents through the app retrieval path; assert parity.

    Uses services.storage.get_signed_url (the presigned-URL path Chancery/the
    routers use), then HTTP GETs it and compares content length against the
    source bucket's HEAD. Returns (checked, passed, details).
    """
    import urllib.request
    import urllib.error
    from services import storage  # noqa: E402  (app retrieval path)

    # Collect (key, bucket) pairs across the storage tables actually deployed.
    rows = []
    # deal_documents carries its own r2_bucket column.
    dd = await conn.fetch(
        "SELECT r2_key AS key, r2_bucket AS bucket FROM deal_documents "
        "WHERE r2_key IS NOT NULL")
    rows += [(r["key"], r["bucket"]) for r in dd]
    for tbl in ("documents", "entity_documents", "spv_documents"):
        try:
            r = await conn.fetch(
                f"SELECT storage_key AS key FROM {tbl} WHERE storage_key IS NOT NULL")
            rows += [(x["key"], None) for x in r]
        except Exception:
            pass

    checked = passed = 0
    details = []
    for key, bucket in rows:
        if checked >= max(MIN_DOCS, len(rows)):
            break
        checked += 1
        # Retrieval goes through the configured (new) bucket by default.
        url = storage.get_signed_url(key)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            details.append(f"  MISS key={key!r} via app path → HTTP {e.code}")
            continue
        # Source-of-truth length from the source bucket HEAD.
        try:
            head = source_client.head_object(Bucket=source_bucket, Key=key)
            src_len = head["ContentLength"]
        except Exception as e:
            details.append(f"  key={key!r} source HEAD failed: {e}")
            continue
        if body and len(body) == src_len:
            passed += 1
            details.append(f"  OK  key={key!r} len={len(body)}")
        else:
            details.append(
                f"  LEN-MISMATCH key={key!r} app={len(body)} src={src_len}")
    return checked, passed, details


async def main():
    _load_env_from_dotenv()
    failures = 0
    print("=== verify_r2rename.py — R2 migration verifier ===")

    # --- Teardown (start): read-only verifier seeds nothing; guard is a no-op. ---
    print("[setup] read-only verifier — no test rows to tear down")

    # --- Config / honest gate ---
    new_bucket = os.environ.get("R2_BUCKET_NAME")
    source_bucket = os.environ.get("R2_SOURCE_BUCKET")
    have_creds = all(os.environ.get(k) for k in
                     ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"))

    if not new_bucket:
        _blocked("R2_BUCKET_NAME is not set — cannot verify the configured bucket.")
        return 2
    if not have_creds:
        _blocked("R2 credentials absent (R2_ACCOUNT_ID/ACCESS_KEY_ID/SECRET_ACCESS_KEY). "
                 "They live only in Render (sync:false). Cannot reach R2 — migration "
                 "not verifiable in this environment.")
        return 2
    try:
        import boto3  # noqa: F401
    except Exception as e:
        _blocked(f"boto3 unavailable ({e}) — cannot reach R2.")
        return 2

    # [CHECK] bucket name read from env, equals expected new bucket
    if new_bucket == EXPECTED_NEW_BUCKET:
        _ok(f"configured R2_BUCKET_NAME = {new_bucket!r} (read from env, not a literal)")
    else:
        _fail(f"R2_BUCKET_NAME = {new_bucket!r}, expected {EXPECTED_NEW_BUCKET!r}")
        failures += 1

    from services import storage  # noqa: E402
    client = storage.get_s3_client()

    # [CHECK] new bucket reachable
    try:
        client.head_bucket(Bucket=new_bucket)
        _ok(f"new bucket {new_bucket!r} reachable")
    except Exception as e:
        _fail(f"new bucket {new_bucket!r} not reachable: {e}")
        return 1  # nothing else is meaningful

    if not source_bucket:
        _blocked("R2_SOURCE_BUCKET not set — cannot compare counts/sizes/content "
                 "against the old bucket. Set it to the pre-migration bucket name.")
        return 2

    # [CHECK] old bucket still exists (this sprint must not have deleted it)
    try:
        client.head_bucket(Bucket=source_bucket)
        _ok(f"source bucket {source_bucket!r} still exists")
    except Exception as e:
        _fail(f"source bucket {source_bucket!r} missing — did the sprint delete it? {e}")
        failures += 1

    # [CHECK] object count + total bytes parity
    src_count, src_bytes = _bucket_stats(client, source_bucket)
    new_count, new_bytes = _bucket_stats(client, new_bucket)
    print(f"[info] source {source_bucket}: {src_count} objects, {src_bytes} bytes")
    print(f"[info] dest   {new_bucket}: {new_count} objects, {new_bytes} bytes")
    if src_count == new_count:
        _ok(f"object count matches: {src_count} == {new_count}")
    else:
        _fail(f"object count mismatch: source {src_count} != dest {new_count}")
        failures += 1
    if src_bytes == new_bytes:
        _ok(f"total byte size matches: {src_bytes} == {new_bytes}")
    else:
        _fail(f"byte size mismatch: source {src_bytes} != dest {new_bytes}")
        failures += 1

    # [CHECK] spot content parity for up to 5 objects spanning prefixes
    spot_keys = _list_keys(client, source_bucket, limit=25)
    # Diversify by top-level prefix.
    seen_prefix = {}
    chosen = []
    for k in spot_keys:
        pref = k.split("/", 1)[0]
        if seen_prefix.get(pref, 0) < 2:
            seen_prefix[pref] = seen_prefix.get(pref, 0) + 1
            chosen.append(k)
        if len(chosen) >= 5:
            break
    spot_pass = 0
    for k in chosen:
        try:
            s = client.get_object(Bucket=source_bucket, Key=k)["Body"].read()
            d = client.get_object(Bucket=new_bucket, Key=k)["Body"].read()
            sh, dh = hashlib.sha256(s).hexdigest(), hashlib.sha256(d).hexdigest()
            if len(s) == len(d) and sh == dh:
                spot_pass += 1
                print(f"  [spot OK] {k} len={len(s)} sha256={sh[:12]}…")
            else:
                print(f"  [spot MISMATCH] {k} src_len={len(s)} dst_len={len(d)}")
        except Exception as e:
            print(f"  [spot ERR] {k}: {e}")
    if chosen and spot_pass == len(chosen):
        _ok(f"spot content parity: {spot_pass}/{len(chosen)} objects byte-identical")
    elif not chosen:
        print("  [info] no objects to spot-check (empty source bucket)")
    else:
        _fail(f"spot content parity failed: {spot_pass}/{len(chosen)}")
        failures += 1

    # --- DB-backed retrieval-path checks ---
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _blocked("DATABASE_URL not set — cannot run app-retrieval-path assertions.")
        return 2
    import asyncpg  # noqa: E402
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        # [CHECK] fetch >= MIN_DOCS rows through the app retrieval path
        checked, passed, details = await _fetch_document_rows(conn, client, source_bucket)
        for line in details:
            print(line)
        if checked < MIN_DOCS:
            print(f"[info] only {checked} documents rows with stored keys exist "
                  f"(fewer than {MIN_DOCS}); asserting over the actual {checked}.")
        if checked and passed == checked:
            _ok(f"app retrieval path: {passed}/{checked} objects fetched non-empty "
                f"with matching content length")
        elif not checked:
            _fail("no documents rows with stored keys to fetch — cannot prove "
                  "retrieval works")
            failures += 1
        else:
            _fail(f"app retrieval path: only {passed}/{checked} objects verified")
            failures += 1

        # [CHECK] negative case: nonexistent key → clean not-found, not 500/empty
        import urllib.request
        import urllib.error
        bad_url = storage.get_signed_url("chancery/__nonexistent__/does-not-exist.bin")
        try:
            with urllib.request.urlopen(bad_url, timeout=30) as r:
                r.read()
            _fail("negative case: nonexistent key returned success (expected 404)")
            failures += 1
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                _ok(f"negative case: nonexistent key → clean HTTP {e.code} not-found")
            else:
                _fail(f"negative case: nonexistent key → HTTP {e.code} (expected 403/404)")
                failures += 1
    finally:
        await conn.close()

    # [CHECK] no remaining hardcoded source-bucket literal
    clean, hits = _grep_no_hardcoded_literal()
    if clean is None:
        print(f"  [info] literal grep skipped: {hits}")
    elif clean:
        _ok("no hardcoded source-bucket literal remains in apps/api or apps/web")
    else:
        _fail("hardcoded source-bucket literal still present in: " + ", ".join(hits))
        failures += 1

    # --- Teardown (end): nothing seeded; no-op guard. ---
    print("[teardown] read-only verifier — nothing to clean up")

    print("=== " + ("ALL CHECKS PASSED" if failures == 0
                     else f"{failures} CHECK(S) FAILED") + " ===")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
