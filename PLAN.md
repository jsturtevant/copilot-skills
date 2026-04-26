# Plan: `codeact` Copilot CLI plugin

## Scope locked

- Plugin name: `codeact`
- Marketplace repo: `jsturtevant/copilot-skills`, manifest at `.github/plugin/marketplace.json`
- Backends: `monty` + `hyperlight`, default `auto` (KVM/mshv/Hyper-V detection, fallback to monty, override via `CODEACT_BACKEND`)
- Skills layout: two peer skills (`hyperlight-codeact`, `monty-codeact`), no umbrella
- Top-level `skills/` removed; everything under `plugins/codeact/`
- Reminder strategy: skill description + custom instructions snippet + PreToolUse deny (in enforcement mode) + custom agent
- Future submission target: `awesome-copilot` marketplace (not now)

## Constraints discovered (Copilot CLI vs Claude Code)

Copilot CLI hooks **cannot inject system prompts or modify user prompts**. Per https://docs.github.com/en/copilot/reference/hooks-configuration:

| Hook | Output behavior |
|------|-----------------|
| SessionStart | Ignored |
| SessionEnd | Ignored |
| UserPromptSubmitted | Ignored (prompt modification not supported) |
| PreToolUse | Only `permissionDecision: "deny"` is processed |
| PostToolUse | Ignored |

So the caveman trick (SessionStart stdout → system context, UserPromptSubmit `hookSpecificOutput.additionalContext` → per-turn reminder) is **not available** in Copilot CLI today.

The only model-facing text channel a plugin controls is the **PreToolUse `permissionDecisionReason`** when denying.

Custom instructions surfaces (per https://docs.github.com/en/copilot/reference/custom-instructions-support and https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions) for Copilot CLI:
1. `.github/copilot-instructions.md` (repo-wide)
2. `.github/instructions/**/*.instructions.md` (path-specific, frontmatter `applyTo`, glob — e.g. `applyTo: "**"` matches everything)
3. `AGENTS.md` (agent instructions)
4. `$HOME/.copilot/copilot-instructions.md` (personal, applies globally)
5. `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` env var — comma-separated directories scanned for `AGENTS.md` and `.github/instructions/**/*.instructions.md`

**Install strategy:** drop a single self-owned file at `.github/instructions/codeact.instructions.md` with `applyTo: "**"`. Never modifies user's `copilot-instructions.md` or `AGENTS.md`. Easy to remove (one file). Global variant writes to `$HOME/.copilot/codeact.instructions.md` (or sets `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` to plugin install dir).

## Workaround layers (stack of activation surfaces)

| Layer | Always-on? | User action | Strength |
|-------|-----------|-------------|----------|
| 1 Skill description matching | When prompt matches triggers | Install plugin | Low passive |
| 2 Custom agent (`/agent codeact`) | Per session, sticky | Invoke once per session | High but opt-in |
| 3 Repo `.github/instructions/codeact.instructions.md` (`applyTo: "**"`) | Yes, in that repo | Run `/codeact-install` once per repo | Medium, owned-file (no merge with user's instructions) |
| 4 Global `$HOME/.copilot/codeact.instructions.md` | Yes, all sessions | Run `/codeact-install --global` | Medium, set-and-forget |
| 5 PreToolUse `deny` reason | Yes, on tool calls | Set `CODEACT_MODE=nudge\|exclusive` | High self-correcting |
| 6 (future) MCP-in-sandbox | Yes when configured | Migrate MCP config | Highest for context savings |

Default install wires layers 1+2+5(off). Recommended user step: run `/codeact-install` for layer 3 (or `--global` for layer 4). Power users opt into layer 5.

## Config model: instructions+agent ARE the config

Since Copilot CLI plugins can't inject runtime context, there is no in-memory "current backend" state to track. Instead:

- **A self-owned path-specific instructions file IS the persisted configuration.** Switching backends = rewriting that one file.
- File location: `.github/instructions/codeact.instructions.md` (repo) or `$HOME/.copilot/codeact.instructions.md` (global). Frontmatter `applyTo: "**"` so it activates for every prompt.
- The file is owned wholly by codeact — the install/switch skills overwrite it atomically. No sentinel-bracketed merges needed because we never share a file with the user.
- The custom agent file `agents/codeact.agent.md` ships pre-templated; switch skills regenerate it with the chosen backend.
- `CODEACT_BACKEND` env var still wins at runtime for power users; skills set the *default* baked into the instructions file.

## Discovery + preflight (always run on install/switch)

Every management skill (`/codeact-install`, `/codeact-monty-backend`, `/codeact-hyperlight-backend`) runs the same pipeline before writing any config:

1. **`scripts/preflight.sh <backend>`** — verify the chosen backend's runtime is actually usable. Exits non-zero with a diagnostic if any check fails. Checks per backend:
   - **monty:** `python3 --version` ≥ 3.10, `uv --version` available (or fallback path documented), `pydantic-monty` installable from PyPI.
   - **hyperlight:** `/dev/kvm` readable (Linux), or `mshv` / Hyper-V available (Windows). On macOS, fail with clear message "hyperlight unsupported on macOS, use monty". `python3 --version` ≤ 3.13 (Wasm guest constraint), `uv --version` available, `hyperlight-sandbox[wasm,python_guest]>=0.3.0` resolvable.
   - **shared:** `bash` present, write access to target instructions path.
2. **`scripts/codeact --discover --backend <backend>`** — invokes the backend's `codeact.py --discover` (already exists) to enumerate registered tools. Output = JSON manifest of host tools + any MCP tools the backend would proxy in.
3. **`scripts/codeact --instructions --backend <backend>`** — LLM-ready Markdown reference for the same tool list (shape suitable for direct paste into instructions / agent).
4. **Template substitution** — `install-instructions.sh` reads the discovered manifest and substitutes:
   - `{{BACKEND}}` → chosen backend name
   - `{{TOOL_LIST}}` → comma-separated tool names from discovery
   - `{{TOOL_REFERENCE}}` → full Markdown reference block
   - `{{SYNTAX}}` → backend-specific syntax example (monty: plain calls; hyperlight: `call_tool(...)`)
   - `{{CODEACT_DIR}}` → absolute plugin install path
5. **Atomic write** of `.github/instructions/codeact.instructions.md` and `agents/codeact.agent.md`. Both files now name the actual available tools, including any MCP servers configured at install time.

If preflight fails, the skill aborts and prints the diagnostic. No partial config is written.

Discovery is **re-runnable**: if user adds an MCP server later, they run `/codeact-install` again to refresh the tool list in the config files.

## Management skills (slash-invoked)

Three purpose-built skills, each a folder under `skills/` with `SKILL.md` + script. User types the slash prefix to invoke.

| Slash invocation | Skill folder | Action |
|------------------|--------------|--------|
| `/codeact-install` | `skills/codeact-install/` | Run `detect-backend.sh` to pick best backend, then write `.github/instructions/codeact.instructions.md` (or `$HOME/.copilot/codeact.instructions.md` with `--global`). Regenerates `agents/codeact.agent.md`. Idempotent overwrite. |
| `/codeact-install-monty` | `skills/codeact-install-monty/` | Rewrite the instructions file + agent file with backend pinned to `monty`. |
| `/codeact-install-hyperlight` | `skills/codeact-install-hyperlight/` | Rewrite the instructions file + agent file with backend pinned to `hyperlight`. |

Each `SKILL.md`:
- `name`: matches folder (e.g. `codeact-install`)
- `description`: short, includes "Use when user asks to install/configure codeact" so the matcher also fires on natural language
- `allowed-tools: shell` (so script runs without per-call confirmation — noted as security trade-off in skill body)
- Body: "Run `bash $SKILL_DIR/run.sh [--global]` from this skill's base directory." Skill scripts shell out to shared `scripts/install-instructions.sh` + `scripts/detect-backend.sh` in the plugin root.

User flow:
```
$ copilot
> /codeact-install
  CodeAct: detected hyperlight (KVM available). Wrote
  .github/instructions/codeact.instructions.md (applyTo: "**").
  Restart session to load.

> /codeact-install-monty

```
copilot-skills/
├── README.md
├── PLAN.md                                        # this file
├── .github/plugin/marketplace.json
└── plugins/codeact/
    ├── plugin.json
    ├── agents/
    │   ├── codeact.agent.md.tmpl                   # source template (with {{BACKEND}}, {{TOOL_LIST}}, etc.)
    │   └── codeact.agent.md                        # rendered by switch skills (pre-rendered fallback committed)
    ├── skills/
    │   ├── hyperlight-codeact/                    # backend skill
    │   │   ├── SKILL.md          # description rewritten for trigger breadth
    │   │   ├── references/tool-patterns.md
    │   │   └── scripts/codeact.py
    │   ├── monty-codeact/                         # backend skill
    │   │   ├── SKILL.md          # description rewritten for trigger breadth
    │   │   ├── references/tool-patterns.md
    │   │   └── scripts/codeact.py
    │   ├── codeact-install/                       # /codeact-install management skill
    │   │   ├── SKILL.md
    │   │   └── run.sh
    │   ├── codeact-install-monty/                 # /codeact-install-monty
    │   │   ├── SKILL.md
    │   │   └── run.sh
    │   └── codeact-install-hyperlight/            # /codeact-install-hyperlight
    │       ├── SKILL.md
    │       └── run.sh
    ├── hooks.json
    ├── hooks/
    │   ├── pre-tool-use.sh
    │   └── pre-tool-use.ps1
    ├── instructions/
    │   └── codeact.instructions.md.tmpl           # path-specific template, applyTo: "**", {{BACKEND}} substituted
    └── scripts/
        ├── install-instructions.sh                # accepts --backend <name> [--global]; runs preflight + discovery
        ├── install-instructions.ps1
        ├── detect-backend.sh                      # auto-pick backend
        ├── preflight.sh                           # verify backend runtime usable, fail-fast diagnostics
        ├── preflight.ps1
        └── codeact                                # thin dispatcher → backend script (also: --discover, --instructions)
```

## Component specs

### `plugin.json`
```json
{
  "name": "codeact",
  "description": "Collapse multi-step tool chains into one sandboxed Python run. Hyperlight + Monty backends.",
  "version": "0.1.0",
  "author": { "name": "jsturtevant" },
  "license": "MIT",
  "repository": "https://github.com/jsturtevant/copilot-skills",
  "keywords": ["codeact", "codemode", "sandbox", "tool-chaining", "mcp", "python"],
  "agents": "agents/",
  "skills": "skills/",
  "hooks": "hooks.json"
}
```

No `commands` field — Copilot CLI uses skills as the slash-command surface.

### Skill descriptions (discovery surface)

Both `hyperlight-codeact/SKILL.md` and `monty-codeact/SKILL.md`:
```yaml
description: |
  CodeAct via <backend>. Use when chaining 3+ tool calls, looping over files,
  filtering/aggregating tool results, calling MCP tools in sequence, batch
  operations, "for each", "find all then", "process all". Collapses N
  round-trips into one sandboxed Python run via scripts/codeact.py.
  Tools inside sandbox: view, create, edit, glob, grep, bash, sql, web_fetch,
  github_api + any registered MCP tool. Trigger: "codeact", "chain tools",
  "sandbox", "batch", "for each".
```
Body content unchanged (current is solid).

### Custom agent — `agents/codeact.agent.md`

The whole point: agent has **no direct host tools**. Only `bash` to run the
codeact dispatcher. All file reads, edits, searches, MCP calls happen
*inside* the sandbox via the Python program. This forces codeact-or-nothing
without needing the PreToolUse exclusive-mode hook.

```yaml
---
name: codeact
description: Sandbox-only agent. All work happens inside one Python run via codeact dispatcher.
tools: ["bash"]
---

You have exactly one tool: `bash`. Use it only to invoke the codeact
dispatcher:

  bash {{CODEACT_DIR}}/scripts/codeact --auto --workspace . --code '<python>'

All file reads, edits, searches, shell commands, MCP calls happen *inside*
that Python program. Available sandbox functions:

{{TOOL_LIST}}

Backend: **{{BACKEND}}** (auto-detected at install; override with
`CODEACT_BACKEND=monty|hyperlight`).

Hyperlight syntax: `call_tool("name", **kwargs)`.
Monty syntax: plain function calls — `view(path="...")`, `glob(pattern="...")`.

If a task is genuinely a single read or single edit and writing Python
would be more verbose than the work itself, say so explicitly and ask the
user to switch agents. Do not try to work around the lack of direct tools.
```

Note: `{{TOOL_LIST}}` and `{{BACKEND}}` are substituted by `install-instructions.sh`
during install/switch, so the agent prompt always names the actually-discovered
sandbox tools (incl. MCP tools).

### Custom instructions file (path-specific, owned by codeact)

`instructions/codeact.instructions.md.tmpl` (template):
```markdown
---
applyTo: "**"
---

## CodeAct (installed via `codeact` plugin, backend: {{BACKEND}})

When a task needs ≥3 tool calls, a loop over files, filtering/aggregation
of tool results, or chaining MCP tools, prefer one sandboxed Python run via
`bash {{CODEACT_DIR}}/scripts/codeact --code '...'` instead of serial direct
calls.

Current backend: **{{BACKEND}}** (override with `CODEACT_BACKEND=monty|hyperlight`).

### Available sandbox tools (discovered at install time)

{{TOOL_LIST}}

{{TOOL_REFERENCE}}

See `{{CODEACT_DIR}}/skills/{{BACKEND}}-codeact/SKILL.md` for invocation syntax.
```

`scripts/install-instructions.sh`:
- `--backend <name>` (or auto via `detect-backend.sh` if omitted)
- `--global` → write to `$HOME/.copilot/codeact.instructions.md`
- default → write to `./.github/instructions/codeact.instructions.md`
- Pipeline: `preflight.sh <backend>` → abort on failure → `codeact --discover --backend <backend>` → `codeact --instructions --backend <backend>` → substitute placeholders → atomic tmp+rename, 0644.
- Same pipeline regenerates `agents/codeact.agent.md` so the agent prompt names the same tool list.
- File is fully owned: overwrite without merge. Removal = `rm` the one file.

### PreToolUse enforcement

`hooks.json`:
```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      { "type": "command", "bash": "./hooks/pre-tool-use.sh", "timeoutSec": 5 }
    ]
  }
}
```

`hooks/pre-tool-use.sh` driven by `CODEACT_MODE`:

| `CODEACT_MODE` | Behavior |
|---|---|
| unset / `off` | Read input, exit 0. No interference. |
| `nudge` | Counter file `${XDG_RUNTIME_DIR:-/tmp}/codeact-$PPID.count`. Increment on each read-only tool (`view`, `glob`, `grep`, `rg`, `read_file`, `file_search`). After ≥3 in a row, deny next call with reason text. Reset counter on deny or on `bash` invoking `scripts/codeact.py`. |
| `exclusive` | Allow only `bash` calls whose args contain `scripts/codeact.py` (or `codeact `). Deny everything else with reason text. |

Reason text (model-facing channel):
```
CodeAct enforcement active (CODEACT_MODE=<mode>). Collapse this work into
one sandboxed Python run:

  bash plugins/codeact/skills/<backend>-codeact/scripts/codeact.py \
    --auto --workspace . --code '<your python>'

Sandbox tools: view, create, edit, glob, grep, bash, sql, web_fetch,
github_api + MCP. Override backend with CODEACT_BACKEND=monty|hyperlight.
Disable enforcement: unset CODEACT_MODE.
```

Counter state per-PPID so concurrent sessions don't collide. Silent-fail on FS errors. PowerShell variant mirrors logic.

### Backend auto-detect — `scripts/detect-backend.sh`

Honors `CODEACT_BACKEND` if set. Otherwise:
- macOS → `monty`
- Linux → `/dev/kvm` or `/dev/mshv` readable → `hyperlight`, else `monty`
- Windows → Hyper-V available → `hyperlight`, else `monty`

`scripts/codeact` = thin wrapper. Subcommands:
- (no flag) `--code '...'` → run sandboxed code via detected backend's `codeact.py`
- `--discover [--backend X]` → emit tools JSON manifest
- `--instructions [--backend X]` → emit LLM-ready tool reference Markdown

### Preflight — `scripts/preflight.sh`

Usage: `preflight.sh <backend>` → exit 0 if usable, non-zero with human-readable diagnostic otherwise. Called automatically by install/switch skills before any config write. Can also be invoked directly by users to debug install issues.

### Marketplace — `.github/plugin/marketplace.json`

```json
{
  "name": "copilot-skills",
  "owner": { "name": "jsturtevant" },
  "metadata": {
    "description": "Personal marketplace — codeact and friends",
    "version": "0.1.0"
  },
  "plugins": [
    {
      "name": "codeact",
      "description": "Collapse multi-step tool chains into sandboxed Python runs.",
      "version": "0.1.0",
      "source": "./plugins/codeact",
      "license": "MIT",
      "keywords": ["codeact", "sandbox", "mcp"]
    }
  ]
}
```

User install:
```bash
copilot plugin marketplace add jsturtevant/copilot-skills
copilot plugin install codeact@copilot-skills
bash ~/.copilot/installed-plugins/copilot-skills/codeact/scripts/install-instructions.sh
```

## Build order

1. Move `skills/hyperlight-codeact/` → `plugins/codeact/skills/hyperlight-codeact/`. Same for monty. Delete top-level `skills/`.
2. Rewrite both backend `SKILL.md` descriptions with expanded triggers.
3. Write `plugins/codeact/plugin.json`.
4. Write `agents/codeact.agent.md` template (with `{{BACKEND}}`, `{{TOOL_LIST}}` placeholders).
5. Write `instructions/codeact.instructions.md.tmpl` (path-specific, `applyTo: "**"`, all `{{...}}` placeholders).
6. Write `scripts/detect-backend.sh` + `scripts/codeact` dispatcher (with `--discover` and `--instructions` subcommands wrapping existing backend `codeact.py`).
7. Write `scripts/preflight.sh` + `scripts/preflight.ps1` (per-backend runtime checks).
8. Write `scripts/install-instructions.sh` (`--backend` + `--global` flags; pipeline = preflight → discover → instructions → substitute → atomic write of both instructions file and agent file).
9. Write management skills:
   - `skills/codeact-install/{SKILL.md,run.sh}` (auto-detect backend + preflight + discover + write)
   - `skills/codeact-install-monty/{SKILL.md,run.sh}` (preflight monty + discover + write)
   - `skills/codeact-install-hyperlight/{SKILL.md,run.sh}` (preflight hyperlight + discover + write)
   Each `run.sh` shells out to shared `../../scripts/install-instructions.sh`.
10. Write `hooks.json` + `hooks/pre-tool-use.sh` + `.ps1`.
11. Write `.github/plugin/marketplace.json`.
12. Rewrite `README.md`.
13. Local test: `copilot plugin install ./plugins/codeact`, verify `/skills list`, `/agent`, `/codeact-install` (preflight + discovery), inspect generated instructions file for actual tool list, switch skills, exercise PreToolUse counter, verify deny path.

## Test harness

### Design

End-to-end tests using the real `copilot` CLI. Creates a temp workspace with known files, runs prompts in multiple arms, captures JSONL output, and compares metrics.

### File layout

```
plugins/codeact/tests/
├── run_tests.py              # Main test runner: `all` | `functional` | `perf` subcommands
├── prompts/
│   ├── functional.json       # Functional test prompts + expected assertions
│   └── perf.json             # Perf test prompts (multi-step tasks)
└── fixtures/
    └── setup-workspace.sh    # Creates temp workspace with known file structure
```

### Temp workspace structure (created by `setup-workspace.sh`)

```
/tmp/codeact-test-XXXX/
├── src/
│   ├── app.py               # 100-line file with 5 TODOs
│   ├── utils.py              # Helper functions, some without docstrings
│   ├── models.py             # Data models
│   ├── api.py                # API endpoints with HTTP references
│   └── config.py             # Configuration
├── config/
│   ├── settings.json         # Valid JSON
│   ├── database.json         # Valid JSON
│   └── broken.json           # Invalid JSON (for error-tolerant test)
├── tests/
│   ├── test_app.py           # Test file
│   └── test_utils.py         # Test file
└── README.md
```

### Test arms (3-way comparison)

| Arm | CLI flags | Purpose |
|-----|-----------|---------|
| `baseline` | `--no-custom-instructions` (no plugin) | Standard multi-tool behavior |
| `codeact` | `--plugin-dir ./plugins/codeact` | CodeAct with skill matching |
| `codeact-instruct` | `--plugin-dir ./plugins/codeact` + instructions file installed | CodeAct with always-on instructions |

All arms run with: `--output-format json --yolo -s -p "<prompt>"`

### Functional test prompts (`prompts/functional.json`)

Each prompt has assertions about what should appear in the output:

```json
[
  {
    "id": "multi-file-search",
    "prompt": "Find all TODO comments across all Python files in src/ and list each with its file and line number. Use codeact.",
    "assertions": {
      "codeact_invoked": true,
      "output_contains": ["TODO", "app.py"],
      "min_todos_found": 3
    }
  },
  {
    "id": "batch-count",
    "prompt": "Count lines of code in each Python file under src/ and show the top 3 largest. Use codeact to do this in one pass.",
    "assertions": {
      "codeact_invoked": true,
      "output_contains": ["lines", "app.py"]
    }
  },
  {
    "id": "json-validate",
    "prompt": "Check all JSON files in config/ for valid syntax. Report which are valid and which have errors. Use codeact.",
    "assertions": {
      "codeact_invoked": true,
      "output_contains": ["broken.json", "ERROR"]
    }
  },
  {
    "id": "single-file-no-codeact",
    "prompt": "Read README.md and tell me what it says.",
    "assertions": {
      "codeact_invoked": false,
      "output_contains": ["README"]
    }
  }
]
```

### Performance test prompts (`prompts/perf.json`)

Multi-step tasks where CodeAct should show significant token/request reduction:

```json
[
  {
    "id": "find-todos",
    "prompt": "Find all TODO comments in every Python file under src/, show each with file path and line number, then count the total.",
    "expected_baseline_tool_calls": ">=5",
    "expected_codeact_tool_calls": "<=2"
  },
  {
    "id": "code-stats",
    "prompt": "For each Python file in src/, count the number of functions, classes, and lines. Show a summary table.",
    "expected_baseline_tool_calls": ">=6",
    "expected_codeact_tool_calls": "<=2"
  },
  {
    "id": "batch-edit-check",
    "prompt": "Find all files in src/ that import 'os' and list them with the line numbers where the import appears.",
    "expected_baseline_tool_calls": ">=4",
    "expected_codeact_tool_calls": "<=2"
  }
]
```

### Metrics extracted by `run_tests.py`

From JSONL output (`--output-format json`):

| Metric | Source event | Field |
|--------|-------------|-------|
| Output tokens | `assistant.message` | `data.outputTokens` |
| Premium requests | `result` | `usage.premiumRequests` |
| API duration (ms) | `result` | `usage.totalApiDurationMs` |
| Session duration (ms) | `result` | `usage.sessionDurationMs` |
| Tool call count | `assistant.message` | `data.toolRequests[]` (length) |
| Tool names used | `assistant.message` | `data.toolRequests[].toolName` |
| CodeAct invoked? | tool calls | Any `bash` call with `codeact` in args |

### `run_tests.py` output

```
┌─────────────────┬──────────┬─────────┬──────────────┬─────────┐
│ Prompt          │ Arm      │ Tokens  │ Tool Calls   │ Requests│
├─────────────────┼──────────┼─────────┼──────────────┼─────────┤
│ find-todos      │ baseline │ 1,250   │ 8            │ 12      │
│ find-todos      │ codeact  │ 450     │ 1            │ 4       │
│ find-todos      │ Δ        │ -64%    │ -87%         │ -67%    │
├─────────────────┼──────────┼─────────┼──────────────┼─────────┤
│ code-stats      │ baseline │ 2,100   │ 12           │ 18      │
│ code-stats      │ codeact  │ 600     │ 1            │ 5       │
│ code-stats      │ Δ        │ -71%    │ -92%         │ -72%    │
└─────────────────┴──────────┴─────────┴──────────────┴─────────┘

PASS: codeact arm shows ≥40% token reduction on all multi-step prompts.
PASS: codeact_invoked=true for all multi-step prompts.
PASS: codeact_invoked=false for single-file prompt.
```

### `run_tests.py all` workflow

```bash
cd plugins/codeact
python3 tests/run_tests.py all          # full run, auto-creates + cleans temp workspace
python3 tests/run_tests.py functional --prompts ... --workspace ... --plugin-dir ...
python3 tests/run_tests.py perf       --prompts ... --workspace ... --plugin-dir ...
```

`all` does: preflight (copilot CLI, python3, plugin.json) → create temp workspace via `fixtures/setup-workspace.sh` → verify plugin loads → functional → perf → cleanup (skip with `--keep-workspace`).

### Running

```bash
cd plugins/codeact
python3 tests/run_tests.py all
```

Requires: `copilot` CLI authenticated, `python3`, `uv` (for backend deps).
Each perf prompt runs twice (baseline + codeact) so costs ~2x premium requests per prompt.

## Future / parking lot

- **Compression mode** (caveman-style output compression) gated by `config.compression.enabled`. Out of scope for v0.1.
- **MCP-in-sandbox** registry (`~/.config/codeact/mcp.json`) so MCP schemas don't bloat host context — separate proxy mode in `codeact.py --mcp <server>`.
- **Submit to `awesome-copilot` marketplace** once stable.
- **File feature request** with GitHub for hook stdout context-injection (parity with Claude Code's `additionalContext`).

## Tool customization (shipped)

Both backends honour the same user-config layer (resolved from `CODEACT_CONFIG_DIR`, else `$XDG_CONFIG_HOME/codeact/`, else `~/.config/codeact/`):

- **Allowlist / denylist** via env (`CODEACT_TOOLS`, `CODEACT_DISABLE`) or `config.json` (`enabled`, `disabled`). Env wins.
- **Custom tools** via drop-in Python files in `<config-dir>/tools/*.py`. Each file defines a callable (default `run`) plus an optional `TOOL` dict (`name`, `description`, `parameters`, `function`). Loaded as `implementation.type = "user"`. Trust = host process.
- Custom tools also subject to allow/deny filtering.
- After config changes, re-run `/codeact-install` to regenerate the discovered tool list baked into the instructions file + agent prompt.
- Tested by `tests/unit/test_tools_config.py` (14 cases covering both backends, run via `python3 tests/run_tests.py unit` or as part of `all`). Functional prompt `custom-tool-shout` exercises the full path through the real `copilot` CLI: workspace fixture drops `shout.py` into `<workspace>/.codeact-config/tools/`, prompt sets `CODEACT_CONFIG_DIR` so the agent can call it via codeact.
