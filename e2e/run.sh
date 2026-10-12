#!/usr/bin/env bash
# E2E check for pi-galley. Needs `pi` on PATH and python3.
# Creates a throwaway venv with pyte (terminal emulator) on first run.
set -euo pipefail
VENV="${TMPDIR:-/tmp}/pi-galley-e2e-venv"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import pyte" 2>/dev/null; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q pyte
fi
exec "$VENV/bin/python" "$(dirname "$0")/run.py" "$@"
