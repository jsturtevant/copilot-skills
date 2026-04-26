#!/usr/bin/env bash
# run.sh — codeact-install-hyperlight skill entry point
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/../../scripts/install-instructions.sh" --backend hyperlight "$@"
