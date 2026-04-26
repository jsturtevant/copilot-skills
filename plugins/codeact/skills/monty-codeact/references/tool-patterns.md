# Tool Patterns & Chaining Reference (Monty)

## Key difference from Hyperlight

Monty calls tools as **regular Python functions** — no `call_tool()` wrapper:

```python
# Monty (this skill)
content = view(path="README.md")
files = glob(pattern="**/*.py")

# Hyperlight (hyperlight-codeact skill)
content = call_tool("view", path="README.md")
files = call_tool("glob", pattern="**/*.py")
```

## CLI Tool Mapping

| Copilot CLI | Monty function | Host implementation |
|---|---|---|
| `view` | `view(path=..., view_range=...)` | `Path.read_text()`, line slicing |
| `create` | `create(path=..., file_text=...)` | `Path.write_text()` (fails if exists) |
| `edit` | `edit(path=..., old_str=..., new_str=...)` | String replace (exactly 1 match) |
| `glob` | `glob(pattern=..., paths=...)` | `Path.glob()` |
| `grep` | `grep(pattern=..., paths=..., glob=...)` | `rg` subprocess (needs ripgrep) |
| `bash` | `bash(command=..., timeout=...)` | `subprocess.run(shell=True)` |
| `sql` | `sql(query=..., db_path=...)` | `sqlite3` module |
| `web_fetch` | `web_fetch(url=..., method=...)` | `curl` subprocess |
| `github_api` | `github_api(endpoint=..., method=..., body=...)` | `gh api` subprocess |

## Always available

| tool | description |
|---|---|
| `view` | Read file contents or list directory |
| `create` | Create a new file (fails if exists) |
| `edit` | Surgical string replacement |
| `glob` | Find files by glob pattern |
| `bash` | Run shell commands (**high risk**) |
| `sql` | Execute SQLite queries |

## Conditionally available

| tool | requires | description |
|---|---|---|
| `grep` | `rg` (ripgrep) | Search file contents |
| `web_fetch` | `curl` | Fetch URLs |
| `github_api` | `gh` CLI | GitHub REST API |

## Monty-specific notes

- Use `chr(10)` for newline character (backslash escapes in some contexts differ)
- Use string concatenation `+` or simple f-strings: `f"count: {n}"`
- **No f-string format specs** — `f"{x:<10}"`, `f"{x:>5}"`, `f"{x:.2f}"` all error
- **No `str.format()`** — `"{:<10}".format(x)` errors
- **No `os.path` or `os.walk`** — use `glob()` to find files, `view()` to read them
- For tabular output, use manual padding:
  ```python
  def pad(s, w):
      s = str(s)
      return s + " " * max(0, w - len(s))
  ```
- No classes, no match statements, no third-party imports
- Supported stdlib: `json`, `re`, `datetime`, `sys`, `os.environ` (no os.path), `typing`, `asyncio`
- Sub-microsecond startup vs ~680ms for Hyperlight

### Return types (critical — wrong assumptions cause retries)
- `glob(pattern=...)` → `list[str]` e.g. `["src/app.py", "src/utils.py"]`
- `view(path=...)` → `str` (full file content)
- `bash(command=...)` → `dict` with `stdout`, `stderr`, `returncode`
- `mcp_call(server=..., tool=..., ...)` → `str`

## Chaining Patterns

### Sequential: search -> read -> analyze

```python
# Do everything in one program — no scouting needed
for f in glob(pattern="**/*.py", paths="src"):
    try:
        content = view(path=f)
    except Exception as e:
        print(f + ": ERROR - " + str(e))
        continue
    lines = content.split(chr(10))
    todos = [l for l in lines if "TODO" in l]
    if todos:
        clean = f.replace("./", "")
        print(clean + ": " + str(len(todos)) + " TODOs")
```

### Cross-file import analysis

```python
import re
files = glob(pattern="src/**/*.py")
deps = {}
for f in files:
    clean = f.replace("./", "")
    try:
        content = view(path=f)
    except Exception:
        continue
    imports = []
    for line in content.split(chr(10)):
        if line.startswith("from src.") or line.startswith("import src."):
            m = re.match(r"(?:from|import)\s+(src\.\S+)", line)
            if m:
                imports.append(m.group(1))
    if imports:
        deps[clean] = imports
for mod, imps in deps.items():
    print(mod + " -> " + ", ".join(imps))
```

### Fan-out / fan-in

```python
repos = ["repo-a", "repo-b", "repo-c"]
all_issues = []
for repo in repos:
    raw = github_api(endpoint="/repos/myorg/" + repo + "/issues")
    all_issues.extend(json.loads(raw))
print("Total issues: " + str(len(all_issues)))
```

### Data pipeline with SQL

```python
sql(query="CREATE TABLE IF NOT EXISTS files (name TEXT, lines INT)")

for f in glob(pattern="**/*.py", paths="src"):
    content = view(path=f)
    n = len(content.split(chr(10)))
    sql(query="INSERT INTO files VALUES ('" + f + "', " + str(n) + ")")

top = sql(query="SELECT name, lines FROM files ORDER BY lines DESC LIMIT 5")
for row in top:
    print(row["name"] + ": " + str(row["lines"]) + " lines")
```

### Error-tolerant batch

```python
files = glob(pattern="*.json", paths="config")
for f in files:
    try:
        raw = view(path=f)
        data = json.loads(raw)
        print(f + ": OK")
    except Exception as e:
        print(f + ": ERROR - " + str(e))
```

### MCP server calls

When `.mcp.json` is present, `mcp_call` bridges to MCP servers:

```python
# Search Microsoft docs
result = mcp_call(server="microsoft-docs", tool="microsoft_docs_search",
                  query="Azure Functions")
print(result[:200])

# Chain: search docs then fetch a page
import json as _json
hits = _json.loads(mcp_call(server="microsoft-docs",
                           tool="microsoft_docs_search",
                           query="Azure Functions"))
url = hits["results"][0]["url"]
page = mcp_call(server="microsoft-docs", tool="microsoft_docs_fetch", url=url)
print(page[:500])
```

## Custom Tool Definitions

Add to the manifest JSON:

```json
{
  "name": "count_lines",
  "description": "Count lines in a file",
  "parameters": {"path": {"type": "string", "required": true}},
  "implementation": {"type": "shell", "command_template": "wc -l < {path}"}
}
```
