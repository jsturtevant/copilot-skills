#!/usr/bin/env python3
"""CodeAct executor for Hyperlight Sandbox.

Discovers available host tools (mirroring Copilot CLI built-in tools),
registers them in a Hyperlight micro-VM, and executes Python code that
chains tools via call_tool().

Tool names match the Copilot CLI built-in tools so the agent can use
familiar names inside the sandbox:

    Copilot CLI tool   |  Sandbox call_tool() name
    -------------------|---------------------------
    view               |  view
    create             |  create
    edit               |  edit
    glob               |  glob
    grep / rg          |  grep
    bash               |  bash
    web_fetch          |  web_fetch
    sql                |  sql
    github-mcp-server  |  github_api

Modes:
    --discover          List available tools as a JSON manifest.
    --instructions      Print call_tool() reference for discovered tools.
    --code / --code-file  Execute sandboxed code with registered tools.

Examples:
    python3 codeact.py --discover
    python3 codeact.py --instructions
    python3 codeact.py --auto --code 'print(call_tool("view", path="README.md")[:100])'
    python3 codeact.py --auto --workspace . --code-file analysis.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------

def discover_tools() -> list[dict[str, Any]]:
    """Discover tools available on the host, named to match Copilot CLI."""
    tools: list[dict[str, Any]] = []

    # -- view (equivalent to Copilot CLI "view" tool) --
    tools.append({
        "name": "view",
        "cli_equivalent": "view",
        "description": "Read file contents or list a directory. "
                       "Pass view_range=[start,end] to read specific lines.",
        "parameters": {
            "path": {"type": "string", "required": True,
                     "description": "File or directory path."},
            "view_range": {"type": "array", "required": False,
                           "description": "[start_line, end_line] 1-indexed. "
                                          "Omit to read the full file."},
        },
        "implementation": {"type": "builtin"},
    })

    # -- create (equivalent to Copilot CLI "create" tool) --
    tools.append({
        "name": "create",
        "cli_equivalent": "create",
        "description": "Create a new file with the given content. "
                       "Fails if the file already exists.",
        "parameters": {
            "path": {"type": "string", "required": True,
                     "description": "Path for the new file."},
            "file_text": {"type": "string", "required": True,
                          "description": "Content to write."},
        },
        "implementation": {"type": "builtin"},
    })

    # -- edit (equivalent to Copilot CLI "edit" tool) --
    tools.append({
        "name": "edit",
        "cli_equivalent": "edit",
        "description": "Replace exactly one occurrence of old_str with new_str "
                       "in a file. Use for surgical edits.",
        "parameters": {
            "path": {"type": "string", "required": True,
                     "description": "File to edit."},
            "old_str": {"type": "string", "required": True,
                        "description": "Exact text to find (must match once)."},
            "new_str": {"type": "string", "required": True,
                        "description": "Replacement text."},
        },
        "implementation": {"type": "builtin"},
    })

    # -- glob (equivalent to Copilot CLI "glob" tool) --
    tools.append({
        "name": "glob",
        "cli_equivalent": "glob",
        "description": "Find files matching a glob pattern (max 200 results).",
        "parameters": {
            "pattern": {"type": "string", "required": True,
                        "description": "Glob pattern, e.g. '**/*.py'."},
            "paths": {"type": "string", "required": False, "default": ".",
                      "description": "Base directory for the search."},
        },
        "implementation": {"type": "builtin"},
    })

    # -- bash (equivalent to Copilot CLI "bash" tool) --
    tools.append({
        "name": "bash",
        "cli_equivalent": "bash",
        "description": "Execute a shell command. Returns dict with stdout, "
                       "stderr, returncode. HIGH RISK: allows arbitrary commands.",
        "parameters": {
            "command": {"type": "string", "required": True,
                        "description": "Shell command to run."},
            "timeout": {"type": "number", "required": False, "default": 30,
                        "description": "Max seconds before kill."},
        },
        "implementation": {"type": "builtin"},
        "risk": "high",
    })

    # -- sql (equivalent to Copilot CLI "sql" tool) --
    tools.append({
        "name": "sql",
        "cli_equivalent": "sql",
        "description": "Execute a SQL query against a SQLite database. "
                       "Returns rows as list of dicts.",
        "parameters": {
            "query": {"type": "string", "required": True,
                      "description": "SQL query to execute."},
            "db_path": {"type": "string", "required": False, "default": ":memory:",
                        "description": "Path to SQLite database file."},
        },
        "implementation": {"type": "builtin"},
    })

    # -- grep (equivalent to Copilot CLI "grep" / "rg" tool) --
    if shutil.which("rg"):
        tools.append({
            "name": "grep",
            "cli_equivalent": "grep / rg",
            "description": "Search file contents with ripgrep. Returns matching lines.",
            "parameters": {
                "pattern": {"type": "string", "required": True,
                            "description": "Regex search pattern."},
                "paths": {"type": "string", "required": False, "default": ".",
                          "description": "Directory to search."},
                "glob": {"type": "string", "required": False,
                         "description": "File-type glob filter, e.g. '*.py'."},
                "context_lines": {"type": "number", "required": False, "default": 0,
                                  "description": "Lines of context around each match."},
            },
            "implementation": {"type": "builtin"},
        })

    # -- web_fetch (equivalent to Copilot CLI "web_fetch" tool) --
    if shutil.which("curl"):
        tools.append({
            "name": "web_fetch",
            "cli_equivalent": "web_fetch",
            "description": "Fetch a URL and return its content.",
            "parameters": {
                "url": {"type": "string", "required": True,
                        "description": "URL to fetch."},
                "method": {"type": "string", "required": False, "default": "GET",
                           "description": "HTTP method."},
                "headers": {"type": "object", "required": False,
                            "description": "Dict of HTTP headers."},
                "data": {"type": "string", "required": False,
                         "description": "Request body."},
            },
            "implementation": {"type": "builtin"},
        })

    # -- github_api (bridges to github-mcp-server tools via gh CLI) --
    if shutil.which("gh"):
        tools.append({
            "name": "github_api",
            "cli_equivalent": "github-mcp-server-*",
            "description": "Call the GitHub REST API via the gh CLI. "
                           "Covers repos, issues, PRs, commits, actions, search.",
            "parameters": {
                "endpoint": {"type": "string", "required": True,
                             "description": "API path, e.g. '/repos/owner/repo/issues'."},
                "method": {"type": "string", "required": False, "default": "GET",
                           "description": "HTTP method (GET, POST, PATCH, DELETE)."},
                "body": {"type": "string", "required": False,
                         "description": "JSON request body for POST/PATCH."},
            },
            "implementation": {"type": "builtin"},
        })

    return tools


def discover_mcp_servers() -> list[dict[str, Any]]:
    """Scan well-known locations for MCP server configs."""
    servers: list[dict[str, Any]] = []
    search_paths = [
        Path(".mcp.json"),
        Path(".vscode/mcp.json"),
        Path(".github/copilot/mcp.json"),
        Path(os.environ.get("XDG_CONFIG_HOME",
                            Path.home() / ".config")) / "mcp/mcp.json",
    ]
    for p in search_paths:
        if not p.exists():
            continue
        try:
            cfg = json.loads(p.read_text())
            for name, scfg in cfg.get("servers",
                                       cfg.get("mcpServers", {})).items():
                servers.append({"name": name, "source": str(p), "config": scfg})
        except Exception:
            pass
    return servers


# ---------------------------------------------------------------------------
# Built-in host-side tool handlers
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT: Path | None = None
_SQLITE_CONNECTIONS: dict[str, sqlite3.Connection] = {}


def _check_workspace(p: Path) -> Path:
    """Resolve a path and verify it falls inside the workspace root."""
    resolved = p.expanduser().resolve()
    if _WORKSPACE_ROOT is not None:
        if not str(resolved).startswith(str(_WORKSPACE_ROOT)):
            raise PermissionError(
                f"Path {resolved} is outside workspace {_WORKSPACE_ROOT}")
    return resolved


def _view(path: str = "", view_range: list[int] | None = None) -> str:
    p = _check_workspace(Path(path))
    if p.is_dir():
        entries = sorted(p.iterdir())
        return "\n".join(e.name + ("/" if e.is_dir() else "") for e in entries)
    text = p.read_text()
    if view_range and len(view_range) == 2:
        lines = text.splitlines(keepends=True)
        start = max(view_range[0] - 1, 0)
        end = view_range[1] if view_range[1] != -1 else len(lines)
        return "".join(lines[start:end])
    return text


def _create(path: str = "", file_text: str = "") -> str:
    p = _check_workspace(Path(path))
    if p.exists():
        raise FileExistsError(f"{path} already exists — use edit to modify.")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(file_text)
    return f"Created {path} ({len(file_text)} bytes)"


def _edit(path: str = "", old_str: str = "", new_str: str = "") -> str:
    p = _check_workspace(Path(path))
    text = p.read_text()
    count = text.count(old_str)
    if count == 0:
        raise ValueError("old_str not found in file.")
    if count > 1:
        raise ValueError(f"old_str matches {count} times — must be unique.")
    p.write_text(text.replace(old_str, new_str, 1))
    return f"Edited {path}"


def _glob(pattern: str = "**/*", paths: str = ".") -> list[str]:
    base = _check_workspace(Path(paths))
    return sorted(str(p) for p in base.glob(pattern) if p.is_file())[:200]


def _bash(command: str = "", timeout: int = 30) -> dict[str, Any]:
    r = subprocess.run(command, shell=True, capture_output=True, text=True,
                       timeout=int(timeout))
    return {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}


def _sql(query: str = "", db_path: str = ":memory:") -> list[dict[str, Any]]:
    if db_path != ":memory:":
        _check_workspace(Path(db_path))
    key = db_path
    if key not in _SQLITE_CONNECTIONS:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _SQLITE_CONNECTIONS[key] = conn
    conn = _SQLITE_CONNECTIONS[key]
    cur = conn.execute(query)
    conn.commit()
    if cur.description:
        return [dict(row) for row in cur.fetchall()]
    return [{"rows_affected": cur.rowcount}]


def _grep(pattern: str = "", paths: str = ".", glob: str = "",
          context_lines: int = 0) -> str:
    cmd = ["rg", "--no-heading", "--line-number", pattern, paths]
    if glob:
        cmd += ["--glob", glob]
    if context_lines:
        cmd += ["-C", str(int(context_lines))]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.stdout


def _web_fetch(url: str = "", method: str = "GET",
               headers: dict[str, str] | None = None,
               data: str = "") -> str:
    cmd = ["curl", "-sS", "-X", method]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data:
        cmd += ["-d", data]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.stdout


def _github_api(endpoint: str = "", method: str = "GET",
                body: str = "") -> str:
    cmd = ["gh", "api", "-X", method, endpoint]
    if body and method.upper() in ("POST", "PATCH", "PUT"):
        cmd += ["--input", "-"]
        r = subprocess.run(cmd, input=body, capture_output=True,
                           text=True, timeout=30)
    else:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"gh api failed: {r.stderr.strip()}")
    return r.stdout


_BUILTIN_HANDLERS: dict[str, Any] = {
    "view": _view,
    "create": _create,
    "edit": _edit,
    "glob": _glob,
    "bash": _bash,
    "sql": _sql,
    "grep": _grep,
    "web_fetch": _web_fetch,
    "github_api": _github_api,
}


# ---------------------------------------------------------------------------
# Handler factory for custom tool definitions
# ---------------------------------------------------------------------------

def _make_handler(tool_def: dict[str, Any]):
    """Return a host-side callback for a tool definition."""
    impl = tool_def.get("implementation", {})
    impl_type = impl.get("type", "builtin")

    if impl_type == "builtin":
        name = tool_def["name"]
        if name in _BUILTIN_HANDLERS:
            return _BUILTIN_HANDLERS[name]
        raise ValueError(f"No built-in handler for '{name}'")

    if impl_type == "shell":
        tpl = impl["command_template"]
        def _shell(**kw: Any) -> str:
            cmd = tpl.format(**kw)
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=impl.get("timeout", 30))
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or f"exit {r.returncode}")
            return r.stdout
        return _shell

    if impl_type == "python":
        code = impl["code"]
        def _py(**kw: Any) -> Any:
            ns: dict[str, Any] = dict(kw)
            exec(code, {"__builtins__": __builtins__, "Path": Path,
                        "json": json, "re": re}, ns)
            return ns.get("result")
        return _py

    raise ValueError(f"Unknown implementation type: {impl_type}")


# ---------------------------------------------------------------------------
# Instruction builder
# ---------------------------------------------------------------------------

def build_instructions(tools: list[dict[str, Any]]) -> str:
    """Generate a call_tool() reference block for LLM system prompts."""
    lines = [
        "## Sandbox Tool Reference",
        "",
        "Inside the sandbox, use `call_tool(name, **kwargs)` to invoke host tools.",
        "It is a built-in global — no import needed.",
        "All arguments must be keyword arguments.",
        "",
        "Tool names match Copilot CLI built-in tools.",
        "",
    ]
    for t in tools:
        sig_parts = []
        for pname, pdef in t.get("parameters", {}).items():
            if pdef.get("required"):
                sig_parts.append(f"{pname}=<{pdef['type']}>")
            else:
                sig_parts.append(f"{pname}={pdef.get('default', '...')!r}")
        sig = ", ".join(sig_parts)
        cli_eq = t.get("cli_equivalent", "")
        label = f" (≈ CLI {cli_eq})" if cli_eq else ""
        lines.append(f"### `call_tool(\"{t['name']}\", {sig})`{label}")
        lines.append(f"{t.get('description', '')}")
        lines.append("")

    tool_names = {t["name"] for t in tools}
    lines.append("### Chaining example")
    lines.append("```python")
    if "grep" in tool_names and "view" in tool_names:
        lines.append("# Find TODOs, then read the first matching file")
        lines.append("hits = call_tool('grep', pattern='TODO', paths='src', glob='*.py')")
        lines.append("first_file = hits.strip().split('\\n')[0].split(':')[0]")
        lines.append("content = call_tool('view', path=first_file)")
        lines.append("print(content[:200])")
    elif "glob" in tool_names and "view" in tool_names:
        lines.append("# List Python files, then read the first one")
        lines.append("files = call_tool('glob', pattern='**/*.py')")
        lines.append("if files:")
        lines.append("    content = call_tool('view', path=files[0])")
        lines.append("    print(content[:200])")
    else:
        lines.append("result1 = call_tool('view', path='README.md')")
        lines.append("print(result1[:200])")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="CodeAct executor — discover Copilot CLI-equivalent tools, "
                    "register them in a Hyperlight sandbox, and run chained code.")
    ap.add_argument("--discover", action="store_true",
                    help="Print a JSON manifest of available host tools.")
    ap.add_argument("--instructions", action="store_true",
                    help="Print call_tool() reference for LLM prompts.")
    ap.add_argument("--manifest",
                    help="Path to a JSON tool manifest (from --discover).")
    ap.add_argument("--code", help="Inline Python code to execute.")
    ap.add_argument("--code-file", help="Path to a .py file to execute.")
    ap.add_argument("--auto", action="store_true",
                    help="Auto-discover tools before running code.")
    ap.add_argument("--stdin", action="store_true",
                    help="Read JSON config (tools + code) from stdin.")
    ap.add_argument("--output", help="Write discovery output to this file.")
    ap.add_argument("--module-path",
                    help="Path to the Hyperlight guest .aot / .wasm module.")
    ap.add_argument("--heap-size", help="Sandbox heap (e.g. '25Mi').")
    ap.add_argument("--stack-size", help="Sandbox stack (e.g. '35Mi').")
    ap.add_argument("--allowed-domains", nargs="*", default=[],
                    help="Domains reachable via sandbox http_get/http_post.")
    ap.add_argument("--workspace",
                    help="Restrict file/sql tools to this directory tree.")
    args = ap.parse_args()

    # ---- discovery / instructions ----
    if args.discover or args.instructions:
        tools = discover_tools()
        mcp = discover_mcp_servers()
        if args.instructions:
            print(build_instructions(tools))
            return
        manifest: dict[str, Any] = {"tools": tools}
        if mcp:
            manifest["mcp_servers"] = mcp
        blob = json.dumps(manifest, indent=2)
        if args.output:
            Path(args.output).write_text(blob)
            print(f"Manifest written to {args.output}", file=sys.stderr)
        else:
            print(blob)
        return

    # ---- execution ----
    try:
        from hyperlight_sandbox import Sandbox
    except ImportError:
        print(json.dumps({
            "stdout": "",
            "stderr": (
                "hyperlight-sandbox is not installed.\n"
                "Run this script with: uv run --with 'hyperlight-sandbox[wasm,python_guest]' python3 scripts/codeact.py ...\n"
                "Or install manually: pip install 'hyperlight-sandbox[wasm,python_guest]'"
            ),
            "exit_code": 1,
            "success": False,
        }, indent=2))
        sys.exit(1)

    global _WORKSPACE_ROOT
    if args.workspace:
        _WORKSPACE_ROOT = Path(args.workspace).resolve()

    config: dict[str, Any] = {}
    if args.stdin:
        config = json.load(sys.stdin)
    elif args.manifest:
        config = json.loads(Path(args.manifest).read_text())
    elif args.auto:
        config["tools"] = discover_tools()

    code = args.code
    if args.code_file:
        code = Path(args.code_file).read_text()
    elif not code and "code" in config:
        code = config["code"]

    if not code:
        ap.error("No code provided. Use --code, --code-file, or 'code' in JSON.")

    tools = config.get("tools", [])

    try:
        sb_kw: dict[str, Any] = {"backend": "wasm"}
        if args.module_path:
            sb_kw["module_path"] = args.module_path
        else:
            sb_kw["module"] = "python_guest.path"
        if args.heap_size:
            sb_kw["heap_size"] = args.heap_size
        if args.stack_size:
            sb_kw["stack_size"] = args.stack_size

        sandbox = Sandbox(**sb_kw)

        for tdef in tools:
            handler = _make_handler(tdef)
            sandbox.register_tool(tdef["name"], handler)

        for domain in args.allowed_domains + config.get("allowed_domains", []):
            if isinstance(domain, list):
                sandbox.allow_domain(domain[0], methods=domain[1:])
            else:
                sandbox.allow_domain(domain)

        result = sandbox.run(code)
        output = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "success": result.success,
        }
    except Exception as exc:
        output = {
            "stdout": "",
            "stderr": f"Executor error: {exc}",
            "exit_code": 1,
            "success": False,
        }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
