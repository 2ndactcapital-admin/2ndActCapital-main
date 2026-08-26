"""Run an older verify script under Doppler-hydrated credentials.

The pre-Doppler verify scripts read DATABASE_URL straight from the ambient
environment, where the copy is stale and its password is rejected. This wrapper
hydrates from Doppler first, then execs the target script in-process so it sees
a working DSN. No secret is printed.

    python3 apps/api/scripts/_run_with_doppler.py verify_workflowmgr4.py
"""
import pathlib
import runpy
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _db_bootstrap import bootstrap  # noqa: E402

if len(sys.argv) < 2:
    sys.exit("usage: _run_with_doppler.py <script.py> [args...]")

dsn = bootstrap(quiet=True)
if not dsn:
    sys.exit("no working DATABASE_URL from Doppler")

import os  # noqa: E402
os.environ["DATABASE_URL"] = dsn

target = HERE / sys.argv[1]
sys.argv = [str(target)] + sys.argv[2:]
runpy.run_path(str(target), run_name="__main__")
