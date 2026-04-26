#!/usr/bin/env bash
# run.sh — codeact-install skill entry point
# Forwards all args to shared install-instructions.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/../../scripts/install-instructions.sh" "$@"
