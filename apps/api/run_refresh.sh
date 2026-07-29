#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${DATABASE_URL:-}" ]; then
  DATABASE_URL="$(grep -h '^DATABASE_URL' .env | head -1 | cut -d= -f2-)"
  export DATABASE_URL
fi
python scripts/refresh_schema.py
