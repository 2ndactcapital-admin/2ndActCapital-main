"""Load apps/api/.env, then run scripts/refresh_schema.py in-process.

Local convenience shim only — refresh_schema.py itself reads DATABASE_URL from
the environment and this session's shell does not export it.
"""
import os
import runpy
import sys

ROOT = "/mnt/c/Users/Joe/2ndActCapital"
sys.path.insert(0, os.path.join(ROOT, "apps/api/venv/lib/python3.12/site-packages"))
sys.path.insert(0, os.path.join(ROOT, "apps/api"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, "apps/api/.env"), override=False)

runpy.run_path(os.path.join(ROOT, "apps/api/scripts/refresh_schema.py"), run_name="__main__")
