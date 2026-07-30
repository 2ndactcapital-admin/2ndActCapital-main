import os, sys, pathlib
BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "venv/lib/python3.14/site-packages"))
# Load DATABASE_URL from apps/api/.env if not already in env
if not os.environ.get("DATABASE_URL"):
    env = BASE / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()
            break
import runpy
runpy.run_path(str(pathlib.Path(__file__).resolve().parent / "refresh_schema.py"), run_name="__main__")
