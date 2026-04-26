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
            "description": "Fetch a URL. HTML is auto-converted to plain text "
                           "and capped at max_length chars.",
            "parameters": {
                "url": {"type": "string", "required": True},
                "method": {"type": "string", "required": False, "default": "GET"},
                "headers": {"type": "object", "required": False},
                "data": {"type": "string", "required": False},
                "max_length": {"type": "number", "required": False, "default": 20000},
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

    # -- mcp_call (bridge to MCP servers when .mcp.json exists) --
    mcp_cfg = _load_mcp_config()
    if mcp_cfg.get("servers"):
        server_names = list(mcp_cfg["servers"].keys())
        tools.append({
            "name": "mcp_call",
            "cli_equivalent": "MCP servers",
            "description": (
                "Call a tool on an MCP server. Available servers: "
                + ", ".join(server_names)
                + ". Use mcp_call(server='name', tool='tool_name', key=val, ...) "
                "to invoke. Returns the tool result as a string."
            ),
            "parameters": {
                "server": {"type": "string", "required": True,
                           "description": f"MCP server name. One of: {', '.join(server_names)}"},
                "tool": {"type": "string", "required": True,
                         "description": "Tool name on the MCP server."},
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
        except Exception as exc:
            print(f"⚠  failed to load MCP config {p}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# User config: enable/disable + custom tool loading
# ---------------------------------------------------------------------------

def _user_config_dir() -> Path:
    """Resolve the user config directory (CODEACT_CONFIG_DIR overrides)."""
    override = os.environ.get("CODEACT_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "codeact"


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def _load_user_tool(py_file: Path) -> dict[str, Any] | None:
    """Load a single user tool .py file. Returns a tool def, or None on error."""
    import importlib.util
    name = py_file.stem
    try:
        spec = importlib.util.spec_from_file_location(f"codeact_user_{name}", py_file)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:
        print(f"⚠  failed to load custom tool {py_file}: {exc}", file=sys.stderr)
        return None

    meta = getattr(mod, "TOOL", {}) or {}
    func_name = meta.get("function", "run")
    func = getattr(mod, func_name, None)
    if not callable(func):
        print(f"⚠  custom tool {py_file} has no callable '{func_name}'", file=sys.stderr)
        return None

    return {
        "name": meta.get("name", name),
        "cli_equivalent": "user",
        "description": meta.get("description", (mod.__doc__ or "User tool").strip()),
        "parameters": meta.get("parameters", {}),
        "implementation": {
            "type": "user",
            "module_path": str(py_file),
            "function": func_name,
        },
    }


def apply_user_config(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter built-in tools per allow/deny config and append custom tools."""
    cfg_dir = _user_config_dir()
    cfg_file = cfg_dir / "config.json"
    cfg: dict[str, Any] = {}
    if cfg_file.is_file():
        try:
            cfg = json.loads(cfg_file.read_text())
        except Exception as exc:
            print(f"⚠  invalid {cfg_file}: {exc}", file=sys.stderr)

    enabled = set(_split_csv(os.environ.get("CODEACT_TOOLS")) or cfg.get("enabled", []))
    disabled = set(_split_csv(os.environ.get("CODEACT_DISABLE")) or cfg.get("disabled", []))

    filtered = []
    for t in tools:
        n = t["name"]
        if disabled and n in disabled:
            continue
        if enabled and n not in enabled:
            continue
        filtered.append(t)

    tools_dir = cfg_dir / "tools"
    if tools_dir.is_dir():
        for py in sorted(tools_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            tdef = _load_user_tool(py)
            if tdef is None:
                continue
            if disabled and tdef["name"] in disabled:
                continue
            if enabled and tdef["name"] not in enabled:
                continue
            filtered.append(tdef)

    return filtered


# ---------------------------------------------------------------------------
# Built-in host-side tool handlers
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT: Path | None = None
_SQLITE_CONNECTIONS: dict[str, sqlite3.Connection] = {}


def _check_workspace(p: Path) -> Path:
    resolved = p.expanduser().resolve()
    if _WORKSPACE_ROOT is not None:
        # Use resolved path comparison with trailing sep to prevent prefix attacks
        # e.g. /workspace_evil passing check for /workspace
        ws = str(_WORKSPACE_ROOT)
        rp = str(resolved)
        if rp != ws and not rp.startswith(ws + os.sep):
            raise PermissionError(
                f"Path {resolved} is outside workspace {_WORKSPACE_ROOT}")
    return resolved


_MAX_VIEW_BYTES = 50 * 1024 * 1024  # 50 MB


def _view(path="", view_range=None):
    p = _check_workspace(Path(path))
    if p.is_dir():
        entries = sorted(p.iterdir())
        return "\n".join(e.name + ("/" if e.is_dir() else "") for e in entries)
    if p.stat().st_size > _MAX_VIEW_BYTES:
        raise ValueError(f"File too large ({p.stat().st_size} bytes, max {_MAX_VIEW_BYTES})")
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
    # Support brace expansion: {a,b} → run multiple globs and merge
    if "{" in pattern and "}" in pattern:
        prefix = pattern[:pattern.index("{")]
        rest = pattern[pattern.index("{"):]
        brace_end = rest.index("}") + 1
        alternatives = rest[1:brace_end-1].split(",")
        suffix = rest[brace_end:]
        expanded = [prefix + alt + suffix for alt in alternatives]
        all_matches: list[str] = []
        for p in expanded:
            all_matches.extend(str(m) for m in base.glob(p) if m.is_file())
        matches = sorted(set(all_matches))[:200]
    else:
        matches = sorted(str(p) for p in base.glob(pattern) if p.is_file())[:200]
    # Return workspace-relative paths so sandbox code doesn't need to strip prefixes
    if _WORKSPACE_ROOT is not None:
        root = str(_WORKSPACE_ROOT) + "/"
        matches = [m[len(root):] if m.startswith(root) else m for m in matches]
    return matches


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


def _web_fetch(url="", method="GET", headers=None, data="", max_length=20000):
    # Block non-HTTP schemes to prevent SSRF
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"Blocked URL scheme (only http/https allowed): {url[:50]}")
    cmd = ["curl", "-sS", "-L", "--max-time", "30", "--max-redirs", "5", "-X", method]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data:
        cmd += ["-d", data]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    body = r.stdout
    # Strip HTML to plain text so the sandbox doesn't waste tokens parsing tags
    if "<html" in body[:500].lower() or "<!doctype" in body[:500].lower():
        body = _html_to_text(body)
    # Cap response size
    if max_length and len(body) > int(max_length):
        body = body[:int(max_length)] + f"\n\n[truncated at {max_length} chars]"
    return body


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text using stdlib html.parser."""
    from html.parser import HTMLParser

    class _Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._parts: list[str] = []
            self._skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self._skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                self._parts.append(data)

    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:
        # Fallback: brute-force strip if parser chokes on malformed HTML
        text = re.sub(r"<[^>]*>", " ", html)
        return re.sub(r"\s+", " ", text).strip()
    text = " ".join(parser._parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


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


# ---------------------------------------------------------------------------
# MCP bridge
# ---------------------------------------------------------------------------

_MCP_CONFIG: dict[str, Any] | None = None


def _load_mcp_config() -> dict[str, Any]:
    """Lazy-load MCP config from the bridge module."""
    global _MCP_CONFIG
    if _MCP_CONFIG is not None:
        return _MCP_CONFIG
    bridge_path = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "mcp-bridge.py"
    if not bridge_path.is_file():
        _MCP_CONFIG = {"servers": {}}
        return _MCP_CONFIG
    import importlib.util
    spec = importlib.util.spec_from_file_location("mcp_bridge", bridge_path)
    if spec is None or spec.loader is None:
        _MCP_CONFIG = {"servers": {}}
        return _MCP_CONFIG
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MCP_CONFIG = mod._load_mcp_config()
    # Stash the module for call_mcp
    _MCP_CONFIG["_bridge_mod"] = mod
    return _MCP_CONFIG


def _mcp_call(server="", tool="", **kwargs):
    """Call an MCP server tool. Available when .mcp.json defines servers."""
    config = _load_mcp_config()
    bridge = config.get("_bridge_mod")
    if bridge is None:
        return json.dumps({"error": "MCP bridge not available"})
    return bridge.call_mcp(config, server, tool, kwargs)


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
    "mcp_call": _mcp_call,
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

    if impl_type == "user":
        import importlib.util
        path = Path(impl["module_path"])
        func_name = impl.get("function", "run")
        spec = importlib.util.spec_from_file_location(
            f"codeact_user_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load user tool {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, func_name)
        def _user(**kw):
            return fn(**kw)
        return _user

    raise ValueError(f"Unknown implementation type: {impl_type}")


# ---------------------------------------------------------------------------
# Instruction builder
# ---------------------------------------------------------------------------

def build_instructions(tools: list[dict[str, Any]]) -> str:
    """Generate compact tool reference for LLM prompts."""
    lines = ["### Sandbox tools (call as Python functions, keyword args only)"]
    lines.append("")
    for t in tools:
        sig_parts = []
        for pname, pdef in t.get("parameters", {}).items():
            if pdef.get("required"):
                sig_parts.append(f"{pname}=...")
            else:
                sig_parts.append(f"{pname}={pdef.get('default', '...')!r}")
        sig = ", ".join(sig_parts)
        desc = t.get("description", "").split(".")[0]  # first sentence only
        lines.append(f"- `{t['name']}({sig})` — {desc}")
    # Always document mcp_call even if no .mcp.json at install time
    tool_names = {t["name"] for t in tools}
    if "mcp_call" not in tool_names:
        lines.append('- `mcp_call(server=..., tool=..., **kwargs)` — Call an MCP server tool (available when .mcp.json is configured)')
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dependency installer
# ---------------------------------------------------------------------------

def _try_install(package: str, import_name: str):
    """Prompt the user to install a missing package. Returns the module or None."""
    import importlib

    print(f"\n⚠  {package} is not installed.", file=sys.stderr)

    # Try uv first
    if shutil.which("uv"):
        answer = input(f"Install {package} with uv? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            r = subprocess.run(
                ["uv", "pip", "install", package],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                print(f"✓ Installed {package}", file=sys.stderr)
                return importlib.import_module(import_name)
            print(f"uv install failed: {r.stderr.strip()}", file=sys.stderr)
    else:
        # Offer to install uv itself
        answer = input("uv is not installed. Install uv? (recommended) [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            r = subprocess.run(
                ["bash", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                # Refresh PATH and retry
                uv_path = Path.home() / ".local" / "bin" / "uv"
                if uv_path.exists():
                    print("✓ Installed uv", file=sys.stderr)
                    r2 = subprocess.run(
                        [str(uv_path), "pip", "install", package],
                        capture_output=True, text=True,
                    )
                    if r2.returncode == 0:
                        print(f"✓ Installed {package}", file=sys.stderr)
                        return importlib.import_module(import_name)
            print("uv install failed. Trying pip...", file=sys.stderr)

    # Fallback to pip
    answer = input(f"Install {package} with pip? [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"✓ Installed {package}", file=sys.stderr)
            return importlib.import_module(import_name)
        print(f"pip install failed: {r.stderr.strip()}", file=sys.stderr)

    print(f"\n✗ Could not install {package}. Install manually:", file=sys.stderr)
    print(f"  uv pip install {package}", file=sys.stderr)
    print(f"  pip install {package}", file=sys.stderr)
    return None


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
        tools = apply_user_config(discover_tools())
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
        pydantic_monty = _try_install("pydantic-monty", "pydantic_monty")
        if pydantic_monty is None:
            sys.exit(1)

    global _WORKSPACE_ROOT
    if args.workspace:
        _WORKSPACE_ROOT = Path(args.workspace).resolve()

    # Clear global state from any prior run
    for conn in _SQLITE_CONNECTIONS.values():
        try:
            conn.close()
        except Exception:
            pass
    _SQLITE_CONNECTIONS.clear()

    config: dict[str, Any] = {}
    if args.stdin:
        config = json.load(sys.stdin)
    elif args.manifest:
        config = json.loads(Path(args.manifest).read_text())
    elif args.auto:
        config["tools"] = apply_user_config(discover_tools())

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
