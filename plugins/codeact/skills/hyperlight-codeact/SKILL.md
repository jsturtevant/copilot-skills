---
description: |
  CodeAct via Hyperlight. Use when looping over many files (8+), cross-referencing
  results from multiple sources, aggregating data across directories, or
  chaining 5+ dependent tool calls. Collapses N round-trips into one sandboxed
  Python run via scripts/codeact.py. NOT beneficial for <5 files or simple
  grep-then-view — direct tool calls have less overhead at small scale.
  ESPECIALLY valuable when MCP servers are loaded — fewer turns means the
  MCP tool catalog context is replayed fewer times.
  Run `scripts/codeact.py --discover` to see available sandbox tools.
  Trigger: "codeact", "chain tools", "sandbox", "batch", "for each",
  "hyperlight sandbox", "collapse tool calls", "run in sandbox",
  "sandbox execution".
name: hyperlight-codeact
---
# Hyperlight CodeAct

Collapse multi-step tool chains into a single sandboxed Python execution
inside an isolated Hyperlight micro-VM (WebAssembly).

## Syntax

Use `call_tool(name, **kwargs)` — built-in global, no import needed:

```python
content = call_tool('view', path='src/main.py')
files = call_tool('glob', pattern='**/*.py')
hits = call_tool('grep', pattern='TODO', paths='src')
result = call_tool('bash', command='git log --oneline -5')
call_tool('edit', path='config.json', old_str='"debug": false', new_str='"debug": true')
```

## Return types (critical — wrong assumptions cause retries)

- `call_tool('glob', ...)` → **list of strings** like `["src/app.py", ...]`
- `call_tool('view', ...)` → **string** (file content)
- `call_tool('bash', ...)` → **dict** with `stdout`, `stderr`, `returncode`
- `call_tool('mcp_call', ...)` → **string**

## Pattern

```python
for f in call_tool('glob', pattern='src/**/*.py'):
    try:
        content = call_tool('view', path=f)
    except Exception:
        continue
    # analyze content...
    print(f"{f}: {result}")
```

## Rules

- **One bash call, one program.** Do not scout with separate tool calls first.
- **Wrap file reads in try/except.**
- `glob()` returns workspace-relative paths — pass directly to `view()`.
- Brace expansion works: `call_tool('glob', pattern='src/{db,services}/**/*.py')`

## Discover tools

```bash
python3 scripts/codeact.py --discover       # JSON manifest
python3 scripts/codeact.py --instructions   # LLM-ready reference
```

Tools are auto-detected based on what's installed on the host.

## Execute

```bash
uv run --python 3.13 --with 'hyperlight-sandbox[wasm,python_guest]>=0.3.0' \
  python3 scripts/codeact.py --auto --workspace . --code '...'
```

Output: `{"stdout": "...", "stderr": "...", "exit_code": 0, "success": true}`

## Trust model

Sandboxed code can only reach the outside world through `call_tool()` bridges.
Tools run on the host with full process access. Use `--workspace` to restrict
file tools to a directory tree.

## Prerequisites

- Python 3.10–3.13 (Wasm backend has no wheels for 3.14+)
- `uv` (recommended) or `pip install 'hyperlight-sandbox[wasm,python_guest]>=0.3.0'`

## References

- [references/tool-patterns.md](references/tool-patterns.md)
