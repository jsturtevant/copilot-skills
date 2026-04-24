# Copilot Skills

Agent skills for GitHub Copilot CLI that implement the **CodeAct pattern** — collapse multi-step tool chains into a single sandboxed Python execution.

## Install

```bash
# Install a specific skill
gh skill install jsturtevant/copilot-skills monty-codeact
gh skill install jsturtevant/copilot-skills hyperlight-codeact

# Or browse and choose interactively
gh skill install jsturtevant/copilot-skills
```

Then `/skills reload` in Copilot CLI to pick them up.

## Available Skills

### monty-codeact

**CodeAct with [Pydantic Monty](https://github.com/pydantic/monty)** — a minimal, secure Python interpreter written in Rust.

- Sub-microsecond startup (<1μs)
- Tools called as natural Python functions: `view(path="README.md")`
- Lightweight: `pip install pydantic-monty` (~4.5MB)
- Auto-installs dependencies via `uv` if missing

Best for: fast, lightweight tool chaining where full Python isn't needed.

### hyperlight-codeact

**CodeAct with [Hyperlight](https://github.com/hyperlight-dev/hyperlight)** — micro-VM sandbox using WebAssembly.

- Full CPython runtime inside a Wasm sandbox
- Tools called via `call_tool("view", path="README.md")`
- Stronger isolation (separate micro-VM per execution)
- Auto-installs dependencies via `uv` if missing

Best for: when you need full Python support or stronger sandbox isolation.

### Shared features

Both skills discover and register tools that match **Copilot CLI built-in tool names**:

| Copilot CLI tool | Sandbox function | What it does |
|---|---|---|
| `view` | `view()` / `call_tool("view")` | Read files / list directories |
| `create` | `create()` / `call_tool("create")` | Create new files |
| `edit` | `edit()` / `call_tool("edit")` | Surgical string replacement |
| `glob` | `glob()` / `call_tool("glob")` | Find files by pattern |
| `grep` | `grep()` / `call_tool("grep")` | Search file contents (needs `rg`) |
| `bash` | `bash()` / `call_tool("bash")` | Run shell commands |
| `sql` | `sql()` / `call_tool("sql")` | SQLite queries |
| `web_fetch` | `web_fetch()` / `call_tool("web_fetch")` | Fetch URLs (needs `curl`) |
| `github_api` | `github_api()` / `call_tool("github_api")` | GitHub REST API (needs `gh`) |

## Why CodeAct?

Instead of N individual tool calls (model → tool → model → tool …), the agent writes one Python program that chains all the tools together and runs it in a single turn. This cuts latency by ~50% and token usage by 85%+ for multi-step tasks.

For more on the pattern, see [CodeAct with Hyperlight](https://devblogs.microsoft.com/agent-framework/codeact-with-hyperlight/) from Microsoft.

## License

MIT
