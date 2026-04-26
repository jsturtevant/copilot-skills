#!/usr/bin/env bash
# preflight.sh — verify a codeact backend runtime is usable
# Usage: preflight.sh <backend>
# Exit 0 if usable, non-zero with diagnostic on failure.
set -euo pipefail

BACKEND="${1:?Usage: preflight.sh <backend>}"

fail() { echo "PREFLIGHT FAIL ($BACKEND): $*" >&2; exit 1; }
warn() { echo "PREFLIGHT WARN ($BACKEND): $*" >&2; }

# --- shared checks ---
command -v bash >/dev/null 2>&1 || fail "bash not found"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 10) )); then
  fail "Python $PY_VERSION too old. Need >=3.10."
fi

HAS_UV=false
if command -v uv >/dev/null 2>&1; then
  HAS_UV=true
fi

case "$BACKEND" in
  monty)
    if [[ "$HAS_UV" != "true" ]]; then
      warn "uv not found. Will try pip fallback for pydantic-monty install."
    fi
    # Check if pydantic-monty is importable or installable
    if ! python3 -c "import pydantic_monty" 2>/dev/null; then
      if [[ "$HAS_UV" == "true" ]]; then
        echo "pydantic-monty not installed. Will auto-install via uv run --with." >&2
      else
        warn "pydantic-monty not installed. Install with: pip install pydantic-monty"
      fi
    fi
    ;;

  hyperlight)
    # macOS check
    if [[ "$(uname -s)" == "Darwin" ]]; then
      fail "Hyperlight not supported on macOS. Use monty backend."
    fi

    # Python version ceiling
    if (( PY_MAJOR > 3 || (PY_MAJOR == 3 && PY_MINOR > 13) )); then
      fail "Python $PY_VERSION too new for hyperlight Wasm backend. Need <=3.13. Use: uv run --python 3.13 ..."
    fi

    # KVM/mshv check on Linux
    if [[ "$(uname -s)" == "Linux" ]]; then
      if [[ ! -r /dev/kvm ]] && [[ ! -r /dev/mshv ]]; then
        fail "Neither /dev/kvm nor /dev/mshv readable. Hyperlight needs hardware virtualization."
      fi
    fi

    if [[ "$HAS_UV" != "true" ]]; then
      warn "uv not found. Will try pip fallback for hyperlight-sandbox install."
    fi

    if ! python3 -c "from hyperlight_sandbox import Sandbox" 2>/dev/null; then
      if [[ "$HAS_UV" == "true" ]]; then
        echo "hyperlight-sandbox not installed. Will auto-install via uv run --with." >&2
      else
        warn "hyperlight-sandbox not installed. Install with: pip install 'hyperlight-sandbox[wasm,python_guest]>=0.3.0'"
      fi
    fi
    ;;

  *)
    fail "Unknown backend: $BACKEND. Expected 'monty' or 'hyperlight'."
    ;;
esac

echo "Preflight OK: $BACKEND (Python $PY_VERSION, uv=$HAS_UV)" >&2
exit 0
