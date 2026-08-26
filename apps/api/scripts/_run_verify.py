"""Run a legacy verify script with Doppler-repaired credentials in os.environ.

Several verify scripts predate _db_bootstrap and read DATABASE_URL at import
time from a stale shell copy. This hydrates first, then runs them unmodified.

Usage: python3 apps/api/scripts/_run_verify.py verify_s27.py
"""
from __future__ import annotations

import asyncio
import pathlib
import runpy
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

target = sys.argv[1]
asyncio.run(bootstrap_async(quiet=True))
sys.argv = [target] + sys.argv[2:]
runpy.run_path(str(pathlib.Path(__file__).resolve().parent / target),
               run_name="__main__")
