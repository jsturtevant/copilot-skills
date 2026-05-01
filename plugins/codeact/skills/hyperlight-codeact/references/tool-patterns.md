# Tool Patterns & Chaining Reference

## CLI Tool Mapping

Sandbox tools mirror Copilot CLI built-in tools:

| Copilot CLI | Sandbox call_tool() | Host implementation |
|---|---|---|
| `view` | `call_tool('view', path=..., view_range=...)` | `Path.read_text()`, line slicing |
| `create` | `call_tool('create', path=..., file_text=...)` | `Path.write_text()` (fails if exists) |
| `edit` | `call_tool('edit', path=..., old_str=..., new_str=...)` | String replace (exactly 1 match) |
| `glob` | `call_tool('glob', pattern=..., paths=...)` | `Path.glob()` |
| `grep` / `rg` | `call_tool('grep', pattern=..., paths=..., glob=...)` | `rg` subprocess (requires ripgrep) |
| `bash` | `call_tool('bash', command=..., timeout=...)` | `subprocess.run(shell=True)` |
| `sql` | `call_tool('sql', query=..., db_path=...)` | `sqlite3` module |
| `web_fetch` | `call_tool('web_fetch', url=..., method=...)` | `curl` subprocess |
| `github-mcp-server-*` | `call_tool('github_api', endpoint=..., method=..., body=...)` | `gh api` subprocess |

## Always available (no dependencies)

| tool | description |
|---|---|
| `view` | Read file contents or list directory |
| `create` | Create a new file (fails if exists) |
| `edit` | Surgical string replacement in a file |
| `glob` | Find files by glob pattern |
| `bash` | Run shell commands (**high risk**) |
| `sql` | Execute SQLite queries |

## Conditionally available

| tool | requires | description |
|---|---|---|
| `grep` | `rg` (ripgrep) | Search file contents |
| `web_fetch` | `curl` | Fetch URLs |
| `github_api` | `gh` CLI | GitHub REST API |

## Chaining Patterns

### Sequential: search -> read -> analyze

```python
for f in call_tool('glob', pattern='**/*.py', paths='src'):
    content = call_tool('view', path=f)
    lines = content.splitlines()
    todos = [l for l in lines if 'TODO' in l]
    if todos:
        print(f"{f}: {len(todos)} TODOs")
        for t in todos:
            print(f"  {t.strip()}")
```

### Nested composition

```python
# Read config found by glob
config = call_tool('view',
    path=call_tool('glob', pattern='**/config.json')[0])
```

### Fan-out / fan-in (multi-repo)

```python
import json as _json
repos = ['repo-a', 'repo-b', 'repo-c']
all_issues = []
for repo in repos:
    raw = call_tool('github_api', endpoint=f'/repos/myorg/{repo}/issues')
    all_issues.extend(_json.loads(raw))
print(f"Total open issues: {len(all_issues)}")
```

### Data pipeline with SQL

```python
import json as _json

# Create and populate a table from API data
call_tool('sql', query='CREATE TABLE IF NOT EXISTS issues (id INT, title TEXT, state TEXT)')

raw = call_tool('github_api', endpoint='/repos/owner/repo/issues?per_page=50')
issues = _json.loads(raw)
for issue in issues:
    call_tool('sql', query=f"INSERT INTO issues VALUES ({issue['number']}, '{issue['title']}', '{issue['state']}')")

# Query the data
open_count = call_tool('sql', query='SELECT COUNT(*) as cnt FROM issues WHERE state="open"')
print(f"Open issues: {open_count[0]['cnt']}")
```

### Search and edit

```python
# Find and fix a pattern across files
hits = call_tool('grep', pattern='http://', paths='src', glob='*.py')
for line in hits.strip().splitlines():
    filepath = line.split(':')[0]
    content = call_tool('view', path=filepath)
    if 'http://' in content and 'https://' not in content:
        call_tool('edit', path=filepath,
                  old_str='http://', new_str='https://')
        print(f"Fixed: {filepath}")
```

### Error-tolerant batch processing

```python
import json as _json
files = call_tool('glob', pattern='*.json', paths='config')
for f in files:
    try:
        raw = call_tool('view', path=f)
        data = _json.loads(raw)
        print(f"{f}: OK ({len(data)} keys)")
    except Exception as e:
        print(f"{f}: ERROR - {e}")
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

```json
{
  "name": "calculate",
  "description": "Evaluate a math expression",
  "parameters": {"expression": {"type": "string", "required": true}},
  "implementation": {"type": "python", "code": "result = eval(expression, {'__builtins__': {}}, {'abs': abs, 'min': min, 'max': max, 'round': round})"}
}
```
