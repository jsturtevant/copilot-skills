#!/usr/bin/env bash
# install-instructions.sh — Install codeact instructions + agent files
# Pipeline: preflight → discover → instructions → substitute → atomic write
#
# Usage:
#   install-instructions.sh [--backend <name>] [--global]
#
# Options:
#   --backend <name>   Force backend (monty|hyperlight). Default: auto-detect.
#   --global           Write to $HOME/.copilot/ instead of .github/instructions/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKEND=""
GLOBAL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)   BACKEND="$2"; shift 2 ;;
    --backend=*) BACKEND="${1#--backend=}"; shift ;;
    --global)    GLOBAL=true; shift ;;
    *)           echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# --- 1. Auto-detect backend if not specified ---
if [[ -z "$BACKEND" ]]; then
  BACKEND=$(bash "$SCRIPT_DIR/detect-backend.sh")
fi
echo "Backend: $BACKEND" >&2

# --- 2. Preflight ---
bash "$SCRIPT_DIR/preflight.sh" "$BACKEND"

# --- 3. Discover tools ---
TOOLS_FILE="$PLUGIN_DIR/.codeact-tools.json"
python3 "$PLUGIN_DIR/skills/${BACKEND}-codeact/scripts/codeact.py" --discover --output "$TOOLS_FILE"
echo "Wrote: $TOOLS_FILE" >&2

# --- 3a. Persist backend choice so runtime dispatch matches install ---
BACKEND_MARKER="$PLUGIN_DIR/.codeact-backend"
echo "$BACKEND" > "$BACKEND_MARKER"
echo "Wrote: $BACKEND_MARKER" >&2

TOOL_LIST=$(jq -r '[.tools[].name] | join(", ")' "$TOOLS_FILE")
# Always include mcp_call in the list so the model knows it exists
if ! echo "$TOOL_LIST" | grep -q "mcp_call"; then
  TOOL_LIST="$TOOL_LIST, mcp_call"
fi

# --- 4. Generate instructions reference ---
TOOL_REFERENCE=$(python3 "$PLUGIN_DIR/skills/${BACKEND}-codeact/scripts/codeact.py" --instructions)

# --- 4b. Backend-specific syntax guide ---
case "$BACKEND" in
  monty)
    SYNTAX="Tools are called as regular Python functions: \`view(path=\"f.py\")\`, \`glob(pattern=\"**/*.py\")\`, etc."
    BACKEND_LIMITATIONS="### Monty limitations (avoid retries)

Monty runs a Python subset. These **will error**:
- \`f\"{x:<10}\"\` or any f-string format spec → use \`+\` with manual padding
- \`\"{:<10}\".format(x)\` → no \`str.format()\`
- \`class Foo:\` → no classes
- \`match x:\` → no match/case
- \`str.startswith()\` with tuple → use \`or\`
- Set comprehensions → use \`list\` + \`in\`
- \`os.path\`, \`os.walk\` → use \`glob()\` and \`view()\` instead

**MCP from inside sandbox:** Use \`mcp_call(server=\"name\", tool=\"tool\", ...)\` to call MCP servers. Both \`mcp_call()\` and \`web_fetch()\` work inside the sandbox."
    ;;
  hyperlight)
    SYNTAX="Tools are called via \`call_tool(name, **kwargs)\` — no import needed."
    BACKEND_LIMITATIONS="**MCP from inside sandbox:** Use \`call_tool(\"mcp_call\", server=\"name\", tool=\"tool\", ...)\` to call MCP servers. Both \`mcp_call\` and \`web_fetch\` work inside the sandbox."
    ;;
esac

# --- 5. Determine output paths ---
if [[ "$GLOBAL" == "true" ]]; then
  INSTRUCTIONS_DIR="$HOME/.copilot"
  INSTRUCTIONS_FILE="$INSTRUCTIONS_DIR/codeact.instructions.md"
else
  INSTRUCTIONS_DIR=".github/instructions"
  INSTRUCTIONS_FILE="$INSTRUCTIONS_DIR/codeact.instructions.md"
fi
AGENT_FILE="$PLUGIN_DIR/agents/codeact.agent.md"

# --- 6. Template substitution ---
CODEACT_DIR="$PLUGIN_DIR"

substitute() {
  local template="$1"
  local content
  content=$(cat "$template")
  content="${content//\{\{BACKEND\}\}/$BACKEND}"
  content="${content//\{\{CODEACT_DIR\}\}/$CODEACT_DIR}"
  content="${content//\{\{TOOL_LIST\}\}/$TOOL_LIST}"
  # Pass multiline values via env vars to avoid shell/Python quoting issues
  TOOL_REF_B64=$(echo "$TOOL_REFERENCE" | base64 -w0)
  SYNTAX_B64=$(echo "$SYNTAX" | base64 -w0)
  LIMITS_B64=$(echo "$BACKEND_LIMITATIONS" | base64 -w0)
  echo "$content" | TOOL_REF_B64="$TOOL_REF_B64" SYNTAX_B64="$SYNTAX_B64" LIMITS_B64="$LIMITS_B64" python3 -c "
import sys, os, base64
content = sys.stdin.read()
ref = base64.b64decode(os.environ['TOOL_REF_B64']).decode()
syntax = base64.b64decode(os.environ['SYNTAX_B64']).decode()
limits = base64.b64decode(os.environ['LIMITS_B64']).decode()
content = content.replace('{{TOOL_REFERENCE}}', ref)
content = content.replace('{{SYNTAX}}', syntax)
content = content.replace('{{BACKEND_LIMITATIONS}}', limits)
print(content, end='')
"
}

# --- 7. Atomic write ---
mkdir -p "$INSTRUCTIONS_DIR"

# Instructions file
TMPFILE=$(mktemp "${INSTRUCTIONS_FILE}.XXXXXX")
substitute "$PLUGIN_DIR/instructions/codeact.instructions.md.tmpl" > "$TMPFILE"
mv "$TMPFILE" "$INSTRUCTIONS_FILE"
chmod 644 "$INSTRUCTIONS_FILE"
echo "Wrote: $INSTRUCTIONS_FILE" >&2

# Agent file (template is .tmpl, output strips .tmpl suffix)
AGENT_TMPL="$PLUGIN_DIR/agents/codeact.agent.md.tmpl"
if [[ ! -f "$AGENT_TMPL" ]]; then
  echo "Error: agent template not found: $AGENT_TMPL" >&2
  exit 1
fi
TMPFILE=$(mktemp "${AGENT_FILE}.XXXXXX")
substitute "$AGENT_TMPL" > "$TMPFILE"
mv "$TMPFILE" "$AGENT_FILE"
chmod 644 "$AGENT_FILE"
echo "Wrote: $AGENT_FILE" >&2

echo "" >&2
echo "CodeAct installed (backend=$BACKEND). Restart session to load." >&2
