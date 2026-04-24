---
description: 'Implement the CodeAct pattern with Pydantic Monty — a minimal, secure Python interpreter written in Rust. Collapse multi-step tool chains into a single sandboxed execution. Unlike hyperlight-codeact, tools are called as regular Python functions (no call_tool wrapper). Tool names match Copilot CLI built-ins (view, create, edit, glob, grep, bash, sql, web_fetch, github_api). Sub-microsecond startup. Use when chaining 3+ tool calls. Trigger phrases: "monty codeact", "monty sandbox", "chain tools", "codeact", "run in monty", "pydantic monty".'
name: monty-codeact
---
# Monty CodeAct

Collapse multi-step tool chains into a single sandboxed Python execution using
[Pydantic Monty](https://github.com/pydantic/monty) — a minimal, secure Python
interpreter written in Rust with sub-microsecond startup.

**Key difference from hyperlight-codeact:** tools are called as regular Python
functions, not through `call_tool()`:

```python
# Monty — natural function calls
content = view(path="README.md")
files = glob(pattern="**/*.py")

# Hyperlight — requires call_tool wrapper
content = call_tool("view", path="README.md")
```

Tool names match Copilot CLI built-in tools:

| Copilot CLI tool     | Monty function   | What it does               |
|----------------------|------------------|----------------------------|
| `view`               | `view()`         | Read files / list dirs     |
| `create`             | `create()`       | Create new files           |
| `edit`               | `edit()`         | Surgical string replace    |
| `glob`               | `glob()`         | Find files by pattern      |
| `grep` / `rg`        | `grep()`         | Search file contents       |
| `bash`               | `bash()`         | Run shell commands         |
| `sql`                | `sql()`          | SQLite queries             |
| `web_fetch`          | `web_fetch()`    | Fetch URLs                 |
| `github-mcp-server-*`| `github_api()`   | GitHub REST API via `gh`   |

## Trust model

Monty completely blocks direct access to the host filesystem, network, and
environment. The only way sandboxed code can interact with the outside world
is through registered external functions (the tools above).

Tool callbacks run on the host with full process access. Use `--workspace`
to restrict file tools to a directory tree.

## Monty limitations

Monty runs a subset of Python. It **cannot**:
- Define classes (coming soon)
- Use match statements (coming soon)
- Import third-party libraries
- Use most of the standard library (supported: json, re, datetime, sys, os, typing, asyncio)

It **can**: loops, functions, f-strings, list/dict comprehensions, try/except,
type hints, and calling external functions with keyword arguments.

## When to use CodeAct vs direct tool calls

**Reach for CodeAct when:** chaining 3+ tool calls, doing computation on
intermediate results, or optimizing for latency/tokens.

**Stay with direct tool calls when:** 1-2 calls, need per-call approval,
or need full Python (classes, third-party libs).

## Quick start

```bash
python3 scripts/codeact.py --auto --workspace . --code '
files = glob(pattern="**/*.py", paths="src")
print("Found " + str(len(files)) + " Python files")
for f in files[:3]:
    content = view(path=f)
    print(f + ": " + str(len(content.split(chr(10)))) + " lines")
'
```

## Workflow

### Step 1 -- Discover tools

```bash
python3 scripts/codeact.py --discover           # JSON manifest
python3 scripts/codeact.py --instructions        # LLM-ready reference
```

### Step 2 -- Write sandboxed code

Call tools as regular Python functions with keyword arguments:

```python
content = view(path="src/main.py")
files = glob(pattern="**/*.py")
hits = grep(pattern="TODO", paths="src")
result = bash(command="git log --oneline -5")
edit(path="config.json", old_str='"debug": false', new_str='"debug": true')
rows = sql(query="SELECT * FROM users", db_path="app.db")
data = github_api(endpoint="/repos/owner/repo/issues")
```

Chain by sequencing or nesting:

```python
for f in glob(pattern="**/*.py", paths="src"):
    content = view(path=f)
    if "TODO" in content:
        print(f + ": " + str(content.count("TODO")) + " TODOs")
```

**Note:** Use `chr(10)` for newlines in split/join, string concatenation
with `+` instead of f-strings with backslashes, and `import json` before
using `json.loads()` / `json.dumps()` (Monty supports the json stdlib module
but it must be imported explicitly).

### Step 3 -- Execute

```bash
python3 scripts/codeact.py --auto --workspace . --code '...'
python3 scripts/codeact.py --manifest tools.json --code-file script.py
echo '{"tools": [...], "code": "..."}' | python3 scripts/codeact.py --stdin
```

Output is JSON:
```json
{"stdout": "...", "stderr": "...", "return_value": null, "success": true}
```

## Available scripts

### `scripts/codeact.py`
Key flags:
- `--discover` / `--instructions` -- tool discovery
- `--auto` -- auto-discover tools before running
- `--workspace <dir>` -- restrict file tools to a directory tree
- `--max-steps N` / `--max-memory N` -- Monty execution limits

## References

- **Tool patterns**: See [references/tool-patterns.md](references/tool-patterns.md)

## Prerequisites

- Python 3.10+
- `pip install pydantic-monty` (~4.5MB, no other dependencies)
