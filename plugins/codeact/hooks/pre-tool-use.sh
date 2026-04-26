#!/usr/bin/env bash
# pre-tool-use.sh — PreToolUse hook for codeact enforcement
# Driven by CODEACT_MODE env var:
#   unset / "off"  → pass through (exit 0)
#   "nudge"        → count sequential read-only tool calls, deny after ≥3
#   "exclusive"    → allow only bash calls invoking codeact, deny all else
#
# Input: JSON on stdin with tool call details
# Output: JSON on stdout with permissionDecision + reason (or empty for allow)

set -uo pipefail

MODE="${CODEACT_MODE:-off}"

# Fast path: no enforcement
if [[ "$MODE" == "off" ]] || [[ -z "$MODE" ]]; then
  cat > /dev/null  # consume stdin
  exit 0
fi

# Read tool call from stdin
INPUT=$(cat)

# Extract tool name and arguments using jq (per Copilot docs best practice)
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // .tool_name // ""' 2>/dev/null || echo "")
TOOL_ARGS=$(echo "$INPUT" | jq -c '.toolInput // .input // {}' 2>/dev/null || echo "{}")

# Check if this is a codeact bash call
is_codeact_call() {
  if [[ "$TOOL_NAME" != "bash" ]] && [[ "$TOOL_NAME" != "shell" ]]; then
    return 1
  fi
  echo "$TOOL_ARGS" | grep -qE 'codeact\.py|scripts/codeact'
}

# Counter file for nudge mode (per-PPID to avoid session collisions)
COUNTER_DIR="${XDG_RUNTIME_DIR:-/tmp}"
COUNTER_FILE="$COUNTER_DIR/codeact-$PPID.count"

# Read discovered tool list from install-time manifest
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLS_FILE="$PLUGIN_DIR/.codeact-tools.json"
if [[ -f "$TOOLS_FILE" ]]; then
  INSTALLED_TOOLS=$(jq -r '[.tools[].name] | join(", ")' "$TOOLS_FILE" 2>/dev/null || echo "view, create, edit, glob, bash, sql")
else
  INSTALLED_TOOLS="view, create, edit, glob, bash, sql"
fi

DENY_REASON="CodeAct enforcement active (CODEACT_MODE=${MODE}). Collapse this work into one sandboxed Python run:

  bash plugins/codeact/scripts/codeact --code '<your python>'

Sandbox tools: ${INSTALLED_TOOLS}.
Disable enforcement: unset CODEACT_MODE."

deny() {
  jq -n --arg reason "$DENY_REASON" '{permissionDecision: "deny", permissionDecisionReason: $reason}'
  exit 0
}

allow() {
  echo '{}'
  exit 0
}

case "$MODE" in
  nudge)
    # Reset counter on codeact invocation
    if is_codeact_call; then
      echo "0" > "$COUNTER_FILE" 2>/dev/null || true
      allow
    fi

    # Read-only tools increment counter
    READ_ONLY_TOOLS="view glob grep rg read_file file_search"
    IS_READ_ONLY=false
    for t in $READ_ONLY_TOOLS; do
      if [[ "$TOOL_NAME" == "$t" ]]; then
        IS_READ_ONLY=true
        break
      fi
    done

    if [[ "$IS_READ_ONLY" == "true" ]]; then
      COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")
      COUNT=$((COUNT + 1))
      echo "$COUNT" > "$COUNTER_FILE" 2>/dev/null || true

      if (( COUNT >= 3 )); then
        echo "0" > "$COUNTER_FILE" 2>/dev/null || true
        deny
      fi
    else
      # Non-read-only, non-codeact tool: reset counter
      echo "0" > "$COUNTER_FILE" 2>/dev/null || true
    fi

    allow
    ;;

  exclusive)
    if is_codeact_call; then
      allow
    fi
    deny
    ;;

  *)
    # Unknown mode, pass through
    allow
    ;;
esac
