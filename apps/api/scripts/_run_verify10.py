"""Load the user's login-shell env (DATABASE_URL / APP_SERVICE_DATABASE_URL /
ANTHROPIC_API_KEY live in ~/.bashrc) then run verify_chancery10 in-process."""
import os, runpy, subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    out = subprocess.run(
        ["bash", "-c", "source \"$HOME/.bashrc\" >/dev/null 2>&1; env -0"],
        capture_output=True)
    for kv in out.stdout.split(b"\0"):
        if b"=" in kv:
            k, v = kv.split(b"=", 1)
            os.environ[k.decode(errors="replace")] = v.decode(errors="replace")
except Exception as exc:  # noqa: BLE001
    print(f"[warn] could not load login-shell env: {exc}")

# Also load apps/api/.env (holds APP_SERVICE_DATABASE_URL for the RLS check).
_ENV_FILE = os.path.join(os.path.dirname(_HERE), ".env")
if os.path.exists(_ENV_FILE):
    for line in open(_ENV_FILE):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v

runpy.run_path(os.path.join(_HERE, "verify_chancery10.py"), run_name="__main__")
