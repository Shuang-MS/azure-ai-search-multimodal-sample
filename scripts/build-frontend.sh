#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
FRONTEND_DIR="$REPO_ROOT/src/frontend"

if ! command -v npm >/dev/null 2>&1; then
  echo "Error: npm is required to build the frontend. Install Node.js (>= 18)." >&2
  exit 127
fi

(
  cd "$FRONTEND_DIR"
  npm install
  npm run build
)
