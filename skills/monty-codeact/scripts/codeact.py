#!/usr/bin/env python3
"""CodeAct executor using Pydantic Monty.

Discovers available host tools, registers them as external functions in
Monty (a minimal secure Python interpreter), and executes code that calls
tools as regular Python functions.

Unlike hyperlight-codeact which uses call_tool(), Monty lets the sandbox
call tools directly as functions:  view(path="README.md")

Tool names match the Copilot CLI built-in tools:

    Copilot CLI tool   |  Monty function name
    -------------------|---------------------
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
    --instructions      Print tool reference for LLM prompts.
    --code / --code-file  Execute sandboxed code with registered tools.

Examples:
    python3 codeact.py --discover
    python3 codeact.py --instructions
    python3 codeact.py --auto --code 'print(view(path="README.md")[:100])'
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

    tools.append({
        "name": "view",
        "cli_equivalent": "view",
        "description": "Read file contents or list a directory. "
                       "Pass view_range=[start,end] to read specific lines.",
        "parameters": {
            "path": {"type": "string", "required": True,
                     "description": "File or directory path."},
            "view_range": {"type": "array", "required": False,
                           "description": "[start_line, end_line] 1-indexed."},
        },
        "implementation": {"type": "builtin"},
    })

    tools.append({
        "name": "create",
        "cli_equivalent": "create",
        "description": "Create a new file with the given content. "
                       "Fails if the file already exists.",
        "parameters": {
            "path": {"type": "string", "required": True},
            "file_text": {"type": "string", "required": True},
        },
        "implementation": {"type": "builtin"},
    })

    tools.append({
        "name": "edit",
        "cli_equivalent": "edit",
        "description": "Replace exactly one occurrence of old_str with new_str.",
        "parameters": {
            "path": {"type": "string", "required": True},
            "old_str": {"type": "string", "required": True},
            "new_str": {"type": "string", "required": True},
        },
        "implementation": {"type": "builtin"},
    })

    tools.append({
        "name": "glob",
        "cli_equivalent": "glob",
        "description": "Find files matching a glob pattern (max 200).",
        "parameters": {
            "pattern": {"type": "string", "required": True,
                        "description": "Glob pattern, e.g. '**/*.py'."},
            "paths": {"type": "string", "required": False, "default": "."},
        },
        "implementation": {"type": "builtin"},
    })

    tools.append({
        "name": "bash",
        "cli_equivalent": "bash",
        "description": "Execute a shell command. Returns dict with stdout, "
                       "stderr, returncode. HIGH RISK.",
        "parameters": {
            "command": {"type": "string", "required": True},
            "timeout": {"type": "number", "required": False, "default": 30},
        },
        "implementation": {"type": "builtin"},
        "risk": "high",
    })

    tools.append({
        "name": "sql",
        "cli_equivalent": "sql",
        "description": "Execute a SQL query against a SQLite database. "
                       "Returns rows as list of dicts.",
        "parameters": {
            "query": {"type": "string", "required": True},
            "db_path": {"type": "string", "required": False, "default": ":memory:"},
        },
        "implementation": {"type": "builtin"},
    })

    if shutil.which("rg"):
        tools.append({
            "name": "grep",
            "cli_equivalent": "grep / rg",
            "description": "Search file contents with ripgrep.",
            "parameters": {
                "pattern": {"type": "string", "required": True},
                "paths": {"type": "string", "required": False, "default": "."},
                "glob": {"type": "string", "required": False},
                "context_lines": {"type": "number", "required": False, "default": 0},
            },
            "implementation": {"type": "builtin"},
        })

    if shutil.which("curl"):
        tools.append({
            "name": "web_fetch",
            "cli_equivalent": "web_fetch",
            "description": "Fetch a URL and return its content.",
            "parameters": {
                "url": {"type": "string", "required": True},
                "method": {"type": "string", "required": False, "default": "GET"},
                "headers": {"type": "object", "required": False},
                "data": {"type": "string", "required": False},
            },
            "implementation": {"type": "builtin"},
        })

    if shutil.which("gh"):
        tools.append({
            "name": "github_api",
            "cli_equivalent": "github-mcp-server-*",
            "description": "Call the GitHub REST API via the gh CLI.",
            "parameters": {
                "endpoint": {"type": "string", "required": True},
                "method": {"type": "string", "required": False, "default": "GET"},
                "body": {"type": "string", "required": False},
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
    resolved = p.expanduser().resolve()
    if _WORKSPACE_ROOT is not None:
        if not str(resolved).startswith(str(_WORKSPACE_ROOT)):
            raise PermissionError(
                f"Path {resolved} is outside workspace {_WORKSPACE_ROOT}")
    return resolved


def _view(path="", view_range=None):
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


def _create(path="", file_text=""):
    p = _check_workspace(Path(path))
    if p.exists():
        raise FileExistsError(f"{path} already exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(file_text)
    return f"Created {path} ({len(file_text)} bytes)"


def _edit(path="", old_str="", new_str=""):
    p = _check_workspace(Path(path))
    text = p.read_text()
    count = text.count(old_str)
    if count == 0:
        raise ValueError("old_str not found in file.")
    if count > 1:
        raise ValueError(f"old_str matches {count} times — must be unique.")
    p.write_text(text.replace(old_str, new_str, 1))
    return f"Edited {path}"


def _glob(pattern="**/*", paths="."):
    base = _check_workspace(Path(paths))
    return sorted(str(p) for p in base.glob(pattern) if p.is_file())[:200]


def _bash(command="", timeout=30):
    r = subprocess.run(command, shell=True, capture_output=True, text=True,
                       timeout=int(timeout))
    return {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}


def _sql(query="", db_path=":memory:"):
    if db_path != ":memory:":
        _check_workspace(Path(db_path))
    if db_path not in _SQLITE_CONNECTIONS:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _SQLITE_CONNECTIONS[db_path] = conn
    conn = _SQLITE_CONNECTIONS[db_path]
    cur = conn.execute(query)
    conn.commit()
    if cur.description:
        return [dict(row) for row in cur.fetchall()]
    return [{"rows_affected": cur.rowcount}]


def _grep(pattern="", paths=".", glob="", context_lines=0):
    cmd = ["rg", "--no-heading", "--line-number", pattern, paths]
    if glob:
        cmd += ["--glob", glob]
    if context_lines:
        cmd += ["-C", str(int(context_lines))]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.stdout


def _web_fetch(url="", method="GET", headers=None, data=""):
    cmd = ["curl", "-sS", "-X", method]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data:
        cmd += ["-d", data]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.stdout


def _github_api(endpoint="", method="GET", body=""):
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
    impl = tool_def.get("implementation", {})
    impl_type = impl.get("type", "builtin")

    if impl_type == "builtin":
        name = tool_def["name"]
        if name in _BUILTIN_HANDLERS:
            return _BUILTIN_HANDLERS[name]
        raise ValueError(f"No built-in handler for '{name}'")

    if impl_type == "shell":
        tpl = impl["command_template"]
        def _shell(**kw):
            cmd = tpl.format(**kw)
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=impl.get("timeout", 30))
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or f"exit {r.returncode}")
            return r.stdout
        return _shell

    if impl_type == "python":
        code = impl["code"]
        def _py(**kw):
            ns = dict(kw)
            exec(code, {"__builtins__": __builtins__, "Path": Path,
                        "json": json, "re": re}, ns)
            return ns.get("result")
        return _py

    raise ValueError(f"Unknown implementation type: {impl_type}")


# ---------------------------------------------------------------------------
# Instruction builder
# ---------------------------------------------------------------------------

def build_instructions(tools: list[dict[str, Any]]) -> str:
    """Generate tool reference for LLM prompts."""
    lines = [
        "## Sandbox Tool Reference (Monty)",
        "",
        "Inside the sandbox, call tools directly as Python functions.",
        "No call_tool() wrapper needed — just call them by name.",
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
        label = f" (= CLI {cli_eq})" if cli_eq else ""
        lines.append(f"### `{t['name']}({sig})`{label}")
        lines.append(f"{t.get('description', '')}")
        lines.append("")

    tool_names = {t["name"] for t in tools}
    lines.append("### Chaining example")
    lines.append("```python")
    if "glob" in tool_names and "view" in tool_names:
        lines.append("# List Python files, read the first one")
        lines.append('files = glob(pattern="**/*.py")')
        lines.append("if files:")
        lines.append("    content = view(path=files[0])")
        lines.append("    print(content[:200])")
    else:
        lines.append('content = view(path="README.md")')
        lines.append("print(content[:200])")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="CodeAct executor using Pydantic Monty — discover "
                    "Copilot CLI-equivalent tools and run chained code.")
    ap.add_argument("--discover", action="store_true",
                    help="Print JSON manifest of available tools.")
    ap.add_argument("--instructions", action="store_true",
                    help="Print tool reference for LLM prompts.")
    ap.add_argument("--manifest",
                    help="Path to a JSON tool manifest.")
    ap.add_argument("--code", help="Inline Python code to execute.")
    ap.add_argument("--code-file", help="Path to a .py file to execute.")
    ap.add_argument("--auto", action="store_true",
                    help="Auto-discover tools before running code.")
    ap.add_argument("--stdin", action="store_true",
                    help="Read JSON config (tools + code) from stdin.")
    ap.add_argument("--output", help="Write discovery output to this file.")
    ap.add_argument("--workspace",
                    help="Restrict file tools to this directory tree.")
    ap.add_argument("--max-steps", type=int, default=None,
                    help="Max execution steps (Monty limit).")
    ap.add_argument("--max-memory", type=int, default=None,
                    help="Max memory in bytes (Monty limit).")
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
        import pydantic_monty
    except ImportError:
        print(json.dumps({
            "stdout": "",
            "stderr": (
                "pydantic-monty is not installed.\n"
                "Run this script with: uv run --with pydantic-monty python3 scripts/codeact.py ...\n"
                "Or install manually: pip install pydantic-monty"
            ),
            "return_value": None,
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

    # Build external_functions dict
    ext_funcs: dict[str, Any] = {}
    for tdef in tools:
        ext_funcs[tdef["name"]] = _make_handler(tdef)

    # Capture stdout/stderr
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def print_callback(stream: str, text: str) -> None:
        if stream == "stderr":
            stderr_parts.append(text)
        else:
            stdout_parts.append(text)

    # Build limits
    limits: dict[str, int] = {}
    if args.max_steps:
        limits["max_execution_steps"] = args.max_steps
    if args.max_memory:
        limits["max_memory_bytes"] = args.max_memory

    try:
        m = pydantic_monty.Monty(code)
        run_kwargs: dict[str, Any] = {
            "external_functions": ext_funcs,
            "print_callback": print_callback,
        }
        if limits:
            run_kwargs["limits"] = limits

        return_value = m.run(**run_kwargs)

        output: dict[str, Any] = {
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
            "return_value": return_value,
            "success": True,
        }
    except Exception as exc:
        output = {
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts) + f"\n{type(exc).__name__}: {exc}",
            "return_value": None,
            "success": False,
        }

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
