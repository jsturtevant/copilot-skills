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
- Use string concatenation `+` or f-strings: `f"count: {n}"`
- No classes, no match statements, no third-party imports
- Supported stdlib: `json`, `re`, `datetime`, `sys`, `os`, `typing`, `asyncio`
- Sub-microsecond startup vs ~680ms for Hyperlight

## Chaining Patterns

### Sequential: search -> read -> analyze

```python
for f in glob(pattern="**/*.py", paths="src"):
    content = view(path=f)
    lines = content.split(chr(10))
    todos = [l for l in lines if "TODO" in l]
    if todos:
        print(f + ": " + str(len(todos)) + " TODOs")
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
