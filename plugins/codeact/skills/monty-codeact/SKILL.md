---
description: |
  CodeAct via Monty. Use when looping over many files (8+), cross-referencing
  results from multiple sources, aggregating data across directories, or
  chaining 5+ dependent tool calls. Collapses N round-trips into one sandboxed
  Python run via scripts/codeact.py. NOT beneficial for <5 files or simple
  grep-then-view — direct tool calls have less overhead at small scale.
  ESPECIALLY valuable when MCP servers are loaded — fewer turns means the
  MCP tool catalog context is replayed fewer times.
  Run `scripts/codeact.py --discover` to see available sandbox tools.
  Sub-microsecond startup.
  Trigger: "codeact", "chain tools", "sandbox", "batch", "for each",
  "monty codeact", "monty sandbox", "run in monty", "pydantic monty".
name: monty-codeact
---
# Monty CodeAct

Collapse multi-step tool chains into a single sandboxed Python execution using
[Pydantic Monty](https://github.com/pydantic/monty) — a minimal, secure Python
interpreter written in Rust with sub-microsecond startup.

## Syntax

Tools are called as regular Python functions with keyword arguments:

```python
content = view(path="src/main.py")
files = glob(pattern="**/*.py")
hits = grep(pattern="TODO", paths="src")
result = bash(command="git log --oneline -5")
edit(path="config.json", old_str='"debug": false', new_str='"debug": true')
```

Chain by sequencing:

```python
for f in glob(pattern="**/*.py", paths="src"):
    content = view(path=f)
    if "TODO" in content:
        print(f + ": " + str(content.count("TODO")) + " TODOs")
```

## Discover tools

```bash
python3 scripts/codeact.py --discover       # JSON manifest
python3 scripts/codeact.py --instructions   # LLM-ready reference
```

Tools are auto-detected based on what's installed on the host.

## Execute

```bash
uv run --with pydantic-monty python3 scripts/codeact.py --auto --workspace . --code '...'
```

Output: `{"stdout": "...", "stderr": "...", "return_value": null, "success": true}`

## Monty limitations

Monty runs a subset of Python. **Will error on:**
- **Classes** — no `class` keyword at all
- **Match statements** — no `match`/`case`
- **f-string format specs** — `f"{x:<10}"`, `f"{x:>5}"`, `f"{x:.2f}"` all fail
- **`str.format()`** — `"{:<10}".format(x)` fails
- **`str.startswith()` with tuple** — use `or` instead
- **Set comprehensions** — build with list + `in` checks
- **Third-party imports** — only stdlib subset
- **Most stdlib** — only: json, re, datetime, sys, os.environ (no os.path, no os.walk)
- **Brace expansion in glob** — `glob(pattern="src/{db,services}/**/*.py")` fails. Use two separate `glob()` calls.

**Sandbox tool return types** (getting these wrong causes retries):
- `glob(pattern=...)` → **list of strings** like `["src/app.py", "src/utils.py"]`
- `view(path=...)` → **string** (file content)
- `mcp_call(server=..., tool=..., ...)` → **string**
- `bash(command=...)` → **dict** with keys `stdout`, `stderr`, `returncode`

**Key usage patterns:**
- **Do NOT scout first.** `glob()` and `view()` are in the sandbox — discover
  files inside your codeact program, not with separate tool calls before it.
- **One program, one bash call.** Do not run multiple codeact invocations.
  If the first one fails, fix the bug in the program, don't add a scouting step.
- **Wrap file reads in try/except** so one bad file doesn't abort the run.
- Use `for f in glob(pattern="**/*.py"):` to iterate files. No os.walk or os.path.

**Output formatting workaround** (use instead of format specs):
```python
def pad(s, w):
    s = str(s)
    return s + " " * max(0, w - len(s))
```

**Tips:** Use `chr(10)` for newlines. Use `import json` explicitly.
Use string concatenation (`+`) or simple f-strings (`f"count: {n}"`).

## Trust model

Sandboxed code can only reach the outside world through registered tool
functions. Use `--workspace` to restrict file tools to a directory tree.

## Prerequisites

- Python 3.10+
- `uv` (recommended) or `pip install pydantic-monty`

## References

- [references/tool-patterns.md](references/tool-patterns.md)
