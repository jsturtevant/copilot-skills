---
description: 'Implement the CodeAct pattern with Hyperlight sandbox. Collapse multi-step tool chains into a single sandboxed Python execution that calls host tools via call_tool(). Tool names match Copilot CLI built-ins (view, create, edit, glob, grep, bash, sql, web_fetch, github_api) so the agent uses familiar names inside the sandbox. Use when a task requires chaining 3+ tool calls (data lookups, code search, computation, file manipulation, API calls). Trigger phrases: "codeact", "hyperlight sandbox", "chain tools together", "sandbox execution", "collapse tool calls", "run in sandbox".'
name: hyperlight-codeact
---
# Hyperlight CodeAct

Collapse multi-step tool chains into a single sandboxed Python execution.
Instead of N individual tool calls (model -> tool -> model -> tool ...), write
one Python program that chains `call_tool()` for each step inside an isolated
Hyperlight micro-VM.

Tool names inside the sandbox **match Copilot CLI built-in tools**:

| Copilot CLI tool     | Sandbox `call_tool()` | What it does               |
|----------------------|-----------------------|----------------------------|
| `view`               | `view`                | Read files / list dirs     |
| `create`             | `create`              | Create new files           |
| `edit`               | `edit`                | Surgical string replace    |
| `glob`               | `glob`                | Find files by pattern      |
| `grep` / `rg`        | `grep`                | Search file contents       |
| `bash`               | `bash`                | Run shell commands         |
| `sql`                | `sql`                 | SQLite queries             |
| `web_fetch`          | `web_fetch`           | Fetch URLs                 |
| `github-mcp-server-*`| `github_api`          | GitHub REST API via `gh`   |

## Trust model

The sandbox isolates model-generated **glue code** (the Python program), not
the tool implementations. Tools run on the **host** with full process access.
Sandboxed code can only reach the outside world through `call_tool()` bridges.

**Sandboxed:** The Python program. Cannot touch host FS, network, or processes.
**Host-side:** Tool callbacks. They have whatever access the process has.
**Implication:** Only register tools appropriate for the trust level. Use
`--workspace` to restrict file tools to a directory tree.

## When to use CodeAct vs direct tool calls

**Reach for CodeAct when:**
- Chaining 3+ tool calls (search -> read -> transform -> write).
- Intermediate results need computation (filtering, aggregation, formatting).
- Reducing latency and token usage matters.

**Stay with direct tool calls when:**
- Only 1-2 tool calls needed.
- Each call needs individual approval.
- Tool outputs are large and need streaming.

## Quick start

```bash
# 1. Discover tools (works without any packages installed)
python3 scripts/codeact.py --discover

# 2. Run code (uv auto-installs hyperlight-sandbox into an ephemeral env)
#    NOTE: The Wasm backend requires Python ≤3.13. Use --python 3.13 if your
#    system default is newer. Pin >=0.3.0 to avoid the empty stub package.
uv run --python 3.13 --with 'hyperlight-sandbox[wasm,python_guest]>=0.3.0' \
  python3 scripts/codeact.py --auto --workspace . --code '
content = call_tool("view", path="README.md")
print(f"README has {len(content.splitlines())} lines")
'
```

## Workflow

### Step 1 -- Discover tools

```bash
python3 scripts/codeact.py --discover           # JSON manifest
python3 scripts/codeact.py --instructions        # LLM-ready reference
python3 scripts/codeact.py --discover --output tools.json  # save manifest
```

### Step 2 -- Write sandboxed code

Use `call_tool(name, **kwargs)` -- built-in global, no import needed.
**All arguments must be keyword arguments.**

```python
# Same names as Copilot CLI tools
content = call_tool('view', path='src/main.py')
files = call_tool('glob', pattern='**/*.py')
hits = call_tool('grep', pattern='TODO', paths='src')
result = call_tool('bash', command='git log --oneline -5')
call_tool('edit', path='config.json', old_str='"debug": false', new_str='"debug": true')
rows = call_tool('sql', query='SELECT * FROM users', db_path='app.db')
data = call_tool('github_api', endpoint='/repos/owner/repo/issues')
```

Chain by sequencing or nesting:

```python
# Sequential: find files, read them, analyze
for f in call_tool('glob', pattern='**/*.py', paths='src'):
    content = call_tool('view', path=f)
    if 'TODO' in content:
        print(f"{f}: {content.count('TODO')} TODOs")

# Nested: read a file found by glob
content = call_tool('view', path=call_tool('glob', pattern='config.json')[0])
```

### Step 3 -- Execute

Use `uv run --with` to auto-install the dependency.

**Important:** The Wasm backend only has wheels for Python ≤3.13. If your
system Python is 3.14+, add `--python 3.13` to the `uv run` command. Always
pin `>=0.3.0` — earlier versions are stub packages without the `Sandbox` class.

```bash
# Auto-discover + workspace scoping (recommended)
uv run --python 3.13 --with 'hyperlight-sandbox[wasm,python_guest]>=0.3.0' \
  python3 scripts/codeact.py --auto --workspace . --code '...'

# With saved manifest
uv run --python 3.13 --with 'hyperlight-sandbox[wasm,python_guest]>=0.3.0' \
  python3 scripts/codeact.py --manifest tools.json --code-file script.py
```

If `hyperlight-sandbox` is already installed, plain `python3` works too:

```bash
python3 scripts/codeact.py --auto --workspace . --code '...'
```

Output is always JSON: `{"stdout": "...", "stderr": "...", "exit_code": 0, "success": true}`

## Available scripts

### `scripts/codeact.py`
Main executor. Key flags:
- `--discover` / `--instructions` -- tool discovery
- `--auto` -- auto-discover tools before running
- `--workspace <dir>` -- restrict file tools to a directory tree
- `--allowed-domains` -- allow sandbox-native HTTP
- `--manifest <file>` -- load tools from JSON

## References

- **Tool patterns and chaining examples**: See [references/tool-patterns.md](references/tool-patterns.md)

## Prerequisites

- Python 3.10–3.13 (the Wasm backend does **not** have wheels for 3.14+)
- `uv` (recommended — auto-installs `hyperlight-sandbox` with no side effects)
- Or: `pip install 'hyperlight-sandbox[wasm,python_guest]>=0.3.0'`

> **Tip:** If your system Python is 3.14+, use `uv run --python 3.13 ...` to
> automatically fetch and use a compatible interpreter.
