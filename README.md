# CodeAct — Copilot CLI Plugin

Collapse multi-step tool chains into a single sandboxed Python execution. Instead of N individual tool calls (model → tool → model → tool …), the agent writes one Python program that chains all the tools together and runs it in a single turn.

## Before / After

**Before (standard):** 8 tool calls, 12 API requests, ~1,250 output tokens
```
model: "I'll search for TODOs"  → grep → model: "Found matches in 3 files"
→ view file1 → model: "File 1 has..." → view file2 → model: "File 2 has..."
→ view file3 → model: "File 3 has..." → model: "Here's the summary..."
```

**After (codeact):** 1 tool call, 4 API requests, ~450 output tokens
```
model: "I'll find all TODOs in one pass" → bash codeact.py --code '
  for f in glob(pattern="**/*.py"):
      content = view(path=f)
      for i, line in enumerate(content.split(chr(10))):
          if "TODO" in line:
              print(f"{f}:{i+1}: {line.strip()}")
' → model: "Here are all TODOs..."
```

## Install

**From marketplace** (recommended):

```bash
copilot plugin marketplace add jsturtevant/copilot-skills
copilot plugin install codeact@copilot-skills

# First time use — run inside Copilot CLI
/codeact-install

# Or run install script directly
bash ~/.copilot/installed-plugins/copilot-skills/codeact/scripts/install-instructions.sh

# Global install (applies to all repos)
bash ~/.copilot/installed-plugins/copilot-skills/codeact/scripts/install-instructions.sh --global
```

**From local checkout** (development):

```bash
copilot plugin install ./plugins/codeact
bash plugins/codeact/scripts/install-instructions.sh
```

## Backends

| Backend | Runtime | Startup | Python support | Isolation | Requires |
|---------|---------|---------|---------------|-----------|----------|
| **monty** (default) | [Pydantic Monty](https://github.com/pydantic/monty) | <1μs | Subset (no classes, limited stdlib) | Interpreter-level | Python 3.10+ |
| **hyperlight** | [Hyperlight](https://github.com/hyperlight-dev/hyperlight) | ~680ms | Full CPython (Wasm) | Micro-VM | KVM/mshv/Hyper-V, Python ≤3.13 |

Auto-detected at install. Override at install time: `--backend monty|hyperlight`.

Switch backend:
```bash
# Use the management skills
/codeact-install-monty
/codeact-install-hyperlight

# Or directly
bash plugins/codeact/scripts/install-instructions.sh --backend monty
```

## Sandbox Tools

Both backends register tools matching **Copilot CLI built-in tool names**:

| Tool | What it does | Requires |
|------|-------------|----------|
| `view` | Read files / list directories | — |
| `create` | Create new files | — |
| `edit` | Surgical string replacement | — |
| `glob` | Find files by pattern | — |
| `bash` | Run shell commands | — |
| `sql` | SQLite queries | — |
| `grep` | Search file contents | `rg` |
| `web_fetch` | Fetch URLs | `curl` |
| `github_api` | GitHub REST API | `gh` |

**Monty syntax:** `view(path="README.md")` — natural function calls
**Hyperlight syntax:** `call_tool("view", path="README.md")` — via wrapper

### Customising tools

Both backends consult the same user config (defaults to `~/.config/codeact/`,
override with `CODEACT_CONFIG_DIR`).

**Disable built-ins** (allowlist or denylist; env wins over config file):

```bash
# Denylist via env — drop bash + sql for this session
CODEACT_DISABLE=bash,sql copilot ...

# Allowlist via env — only view/glob/grep registered
CODEACT_TOOLS=view,glob,grep copilot ...
```

Or persist in `~/.config/codeact/config.json`:

```json
{
  "disabled": ["bash", "sql"],
  "enabled": []
}
```

After changing config, re-run `/codeact-install` so the instructions file
+ agent prompt reflect the new tool list.

**Add your own tools** — drop a `.py` file in `~/.config/codeact/tools/`.
File stem becomes the tool name; the file must define a callable (default:
`run`) plus an optional `TOOL` metadata dict:

```python
# ~/.config/codeact/tools/shout.py
"""Shout text back in uppercase."""

TOOL = {
    "description": "Echo input text in uppercase.",
    "parameters": {
        "text": {"type": "string", "required": True},
    },
    # "name": "shout",       # optional; defaults to filename stem
    # "function": "run",     # optional; defaults to "run"
}

def run(text: str = "") -> str:
    return text.upper()
```

After adding, re-run `/codeact-install` to refresh discovery. Custom tools
also honor allowlist/denylist filters.

> **Trust:** custom tools run on the host with full process privileges
> (same as built-in `bash`). Only install code you trust.

## Activation Layers

| Layer | Always-on? | How |
|-------|-----------|-----|
| Skill description matching | When prompt matches | Install plugin |
| Custom agent (`/agent codeact`) | Per session | Type once |
| Repo instructions (`/codeact-install`) | In that repo | Run once |
| Global instructions (`/codeact-install --global`) | All sessions | Run once |
| PreToolUse enforcement (`CODEACT_MODE=nudge\|exclusive`) | Yes | Set env var |

Layered-activation pattern borrowed from [caveman](https://github.com/JuliusBrussee/caveman) (skill + agent + always-on instructions + tool-call enforcement).

### Copilot CLI limitations (vs Claude Code)

Copilot CLI plugin [hooks](https://docs.github.com/en/copilot/reference/hooks-configuration) **cannot inject system prompts or modify user prompts** — only `PreToolUse` `permissionDecision: "deny"` reaches the model. So the always-on caveman trick (SessionStart → system context, UserPromptSubmit → per-turn reminder) isn't available, which is why this plugin leans on a self-owned `.github/instructions/codeact.instructions.md` plus a custom agent. Hook stdout-as-context (parity with Claude Code's `additionalContext`) would let plugins like this self-activate without writing files.

## Testing (developers)

End-to-end harness runs prompts through the real `copilot` CLI in a temp
workspace and compares **baseline vs codeact** arms for token / tool-call /
premium-request reduction. Unit tests for the tool-config layer (allow/deny
lists + custom tool loading) live under `tests/unit/` and run first via
`unittest discover` — fast, no `copilot` CLI needed.

**Prerequisites:** authenticated `copilot` CLI, [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (resolves Python + any script deps on demand — no manual `pip install`). Each perf prompt runs twice (baseline + codeact) so it consumes ~2× premium requests per prompt.

All commands below run from the **repo root**.

```bash
# Unit tests only (no copilot CLI required)
uv run plugins/codeact/tests/run_tests.py unit

# Functional only — auto-creates + cleans up a temp workspace
uv run plugins/codeact/tests/run_tests.py functional

# Perf only — baseline vs codeact comparison (auto workspace)
uv run plugins/codeact/tests/run_tests.py perf

# Full run — unit + preflight + functional + perf + cleanup
uv run plugins/codeact/tests/run_tests.py all

# Keep the temp workspace for inspection (works with all/functional/perf)
uv run plugins/codeact/tests/run_tests.py all --keep-workspace

# Custom token-reduction threshold (default 40%; all + perf)
uv run plugins/codeact/tests/run_tests.py perf --min-token-reduction 30
```

**Reuse an existing workspace** (skip auto-create, e.g. when iterating on a fixture):

```bash
uv run plugins/codeact/tests/run_tests.py functional \
  --workspace /tmp/my-workspace

uv run plugins/codeact/tests/run_tests.py perf \
  --workspace /tmp/my-workspace \
  --prompts plugins/codeact/tests/prompts/perf.json
```

Perf results are written to `plugins/codeact/tests/results/perf-results-<UTC-timestamp>.json`
(plus `plugins/codeact/tests/results/perf-results-latest.json` as a stable
pointer to the most recent run).

**Compare two runs** (e.g. before/after a change):

```bash
# Auto-compare: latest vs previous run (no args needed)
uv run plugins/codeact/tests/compare_results.py

# Explicit files with labels
uv run plugins/codeact/tests/compare_results.py \
  plugins/codeact/tests/results/perf-results-20260424T101500Z.json \
  plugins/codeact/tests/results/perf-results-latest.json \
  --a-label before --b-label after

# Custom plot output path
uv run plugins/codeact/tests/compare_results.py --out /tmp/diff.png
```

Without `uv`, falls back to `python3 tests/compare_results.py ...` and prints
the delta table only (plot needs `pip install matplotlib numpy`).

## Why CodeAct?

Instead of N individual tool calls (model → tool → model → tool …), the agent writes one Python program that chains all the tools together and runs it in a single turn. Fewer turns means the conversation context — system prompt, tool definitions, prior messages — is replayed fewer times. With MCP servers loaded, each server's tool catalog adds to that context, so the savings compound.

Each test runs the same prompt twice: once as a **baseline** (standard Copilot CLI, no plugin) and once with **codeact** (plugin loaded). Token counts are extracted from copilot process logs. Tests use a 30+ file Python project with handlers, services, middleware, configs, and tests.

| Task | Turns | Input Tokens | Est. Cost Savings |
|------|:-----:|:------------:|:-----------------:|
| Test coverage + 4 MCP servers | 6 → 2 | 335K → 103K | **69%** |
| Full project function index | 4 → 2 | 130K → 57K | **57%** |
| Test coverage (no MCP) | 4 → 2 | 123K → 58K | **57%** |
| Docstring coverage | 3 → 2 | 86K → 56K | **49%** |
| MCP docs cross-ref + 4 servers | 3 → 3 | 167K → 88K | **49%** |

Cost estimated at GPT-5.4 pricing ($2.50/M input, $15/M output). Run `uv run plugins/codeact/tests/run_tests.py perf --backend monty` to reproduce.

For more on the pattern, see [CodeAct with Hyperlight](https://devblogs.microsoft.com/agent-framework/codeact-with-hyperlight/) from Microsoft.

## License

MIT
