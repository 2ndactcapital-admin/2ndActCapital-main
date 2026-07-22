#!/usr/bin/env bash
# Sprint 24 brand-sweep grep. Shared by the human-run inventory (Pass A) and
# the verify_sprint24.py hard gate, so both count exactly the same thing.
cd "$(dirname "$0")/.." || exit 1

INC=(--include=*.py --include=*.js --include=*.jsx --include=*.ts --include=*.tsx
     --include=*.css --include=*.json --include=*.html --include=*.svg --include=*.mjs)

NAME_RE='2nd ?Act|2ndAct'
HEX_RE='#?(1B2B4B|C5A880|E8D5A3|9AA6BF|FAF9F6|F5F1EB|FFFFFF|0F172A|334155|64748B|E2E8F0)'

scan() {
  grep -rInE "$1" apps/ scripts/ "${INC[@]}" 2>/dev/null \
    | grep -v node_modules | grep -v '/\.next/' | grep -v '/venv/'
}

case "${1:-all}" in
  name) scan "$NAME_RE" ;;
  hex)  scan "$HEX_RE" ;;
  *)    scan "$NAME_RE"; scan "$HEX_RE" ;;
esac
