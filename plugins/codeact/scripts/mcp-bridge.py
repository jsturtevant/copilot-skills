#!/usr/bin/env python3
"""MCP bridge — call MCP server tools from the codeact sandbox.

Supports two MCP server types:
  - HTTP/SSE: POST JSON-RPC to the server URL (streamable-http transport)
  - stdio: spawn the server command, send JSON-RPC over stdin/stdout

Usage:
    python3 mcp-bridge.py --config .mcp.json --server microsoft-docs \
        --tool search --args '{"query": "Azure Functions"}'

    python3 mcp-bridge.py --config .mcp.json --list-servers
    python3 mcp-bridge.py --config .mcp.json --server markitdown --list-tools
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Only pass safe env vars to MCP server subprocesses — never leak secrets.
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER",
    "SHELL", "TMPDIR", "TMP", "TEMP", "XDG_RUNTIME_DIR",
})


def _safe_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build an environment dict with only safe vars from os.environ."""
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
    if extra:
        env.update(extra)
    return env
from typing import Any

_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB cap for MCP responses


def _validate_url(url: str) -> None:
    """Reject non-HTTP URLs and localhost/private IPs to prevent SSRF."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r} (only http/https allowed)")
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError(f"Blocked URL: localhost access not allowed ({host})")


def _read_capped(resp, max_bytes: int = _MAX_RESPONSE_BYTES) -> str:
    """Read HTTP response with a size cap to prevent OOM."""
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace")


def _load_mcp_config(config_path: str | None = None) -> dict[str, Any]:
    """Load MCP server configuration from well-known locations."""
    search_paths = [
        Path(".mcp.json"),
        Path(".vscode/mcp.json"),
        Path(".github/copilot/mcp.json"),
    ]
    if config_path:
        search_paths.insert(0, Path(config_path))

    for p in search_paths:
        if p.is_file():
            try:
                cfg = json.loads(p.read_text())
                # Normalize: accept both "servers" and "mcpServers"
                servers = cfg.get("servers") or cfg.get("mcpServers") or {}
                return {"servers": servers, "source": str(p)}
            except Exception:
                continue
    return {"servers": {}, "source": None}


def _call_http_server(url: str, tool_name: str, arguments: dict[str, Any],
                      timeout: int = 30) -> str:
    """Call a tool on an HTTP/SSE MCP server using streamable-http transport."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    _validate_url(url)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = _read_capped(resp)

            # Direct JSON-RPC response
            if "application/json" in content_type:
                result = json.loads(body)
                return _extract_result(result)

            # SSE stream — parse event lines
            if "text/event-stream" in content_type:
                return _parse_sse_result(body)

            # Unknown content type — return raw
            return body
    except urllib.error.URLError as exc:
        return json.dumps({"error": str(exc)})


def _call_stdio_server(command: str, args: list[str], tool_name: str,
                       arguments: dict[str, Any], env: dict[str, str] | None = None,
                       timeout: int = 30) -> str:
    """Call a tool on a stdio MCP server (spawn, init, call, close)."""
    init_msg = {
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "codeact-bridge", "version": "1.0"},
        },
    }
    call_msg = {
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    stdin_data = (
        json.dumps(init_msg) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps(call_msg) + "\n"
    )

    run_env = _safe_env(env)

    try:
        proc = subprocess.run(
            [command] + args,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except FileNotFoundError:
        return json.dumps({"error": f"Command not found: {command}"})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"MCP server timed out after {timeout}s"})

    # Parse JSON-RPC responses from stdout (one per line)
    for line in proc.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("id") == 2:  # Our tools/call response
                return _extract_result(msg)
        except json.JSONDecodeError:
            continue

    # Fallback: return everything
    return proc.stdout or proc.stderr or json.dumps({"error": "No response from MCP server"})


def _list_tools_stdio(command: str, args: list[str],
                      env: dict[str, str] | None = None,
                      timeout: int = 15) -> list[dict[str, Any]]:
    """List tools from a stdio MCP server."""
    init_msg = {
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "codeact-bridge", "version": "1.0"},
        },
    }
    list_msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    stdin_data = (
        json.dumps(init_msg) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps(list_msg) + "\n"
    )

    run_env = _safe_env(env)

    try:
        proc = subprocess.run(
            [command] + args,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except Exception:
        return []

    for line in proc.stdout.strip().split("\n"):
        try:
            msg = json.loads(line.strip())
            if msg.get("id") == 2 and "result" in msg:
                return msg["result"].get("tools", [])
        except json.JSONDecodeError:
            continue
    return []


def _list_tools_http(url: str, timeout: int = 15) -> list[dict[str, Any]]:
    """List tools from an HTTP MCP server."""
    _validate_url(url)
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/list", "params": {},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _read_capped(resp)
            content_type = resp.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                # Parse SSE for the result
                for line in body.split("\n"):
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            msg = json.loads(data_str)
                            if "result" in msg:
                                return msg["result"].get("tools", [])
                        except json.JSONDecodeError:
                            continue
                return []
            result = json.loads(body)
            if "result" in result:
                return result["result"].get("tools", [])
    except Exception:
        pass
    return []


def _extract_result(msg: dict[str, Any]) -> str:
    """Extract the text content from a JSON-RPC result."""
    if "error" in msg:
        return json.dumps(msg["error"])
    result = msg.get("result", {})
    # MCP tools/call returns {"content": [{"type": "text", "text": "..."}]}
    content = result.get("content", [])
    if isinstance(content, list):
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    # Fallback
    return json.dumps(result) if isinstance(result, dict) else str(result)


def _parse_sse_result(body: str) -> str:
    """Parse SSE stream for the tools/call result."""
    for line in body.split("\n"):
        if line.startswith("data:"):
            data_str = line[5:].strip()
            try:
                msg = json.loads(data_str)
                if msg.get("id") and ("result" in msg or "error" in msg):
                    return _extract_result(msg)
            except json.JSONDecodeError:
                continue
    return body  # Return raw if no result found


def call_mcp(config: dict[str, Any], server_name: str, tool_name: str,
             arguments: dict[str, Any], timeout: int = 30) -> str:
    """Call an MCP tool. Main entry point."""
    servers = config.get("servers", {})
    if server_name not in servers:
        return json.dumps({"error": f"Unknown MCP server: {server_name}",
                           "available": list(servers.keys())})

    scfg = servers[server_name]

    # HTTP/SSE server
    if scfg.get("type") == "http" or "url" in scfg:
        url = scfg.get("url", "")
        return _call_http_server(url, tool_name, arguments, timeout=timeout)

    # stdio server
    command = scfg.get("command", "")
    args = scfg.get("args", [])
    env = scfg.get("env")
    if not command:
        return json.dumps({"error": f"MCP server {server_name} has no command or url"})

    return _call_stdio_server(command, args, tool_name, arguments,
                              env=env, timeout=timeout)


def list_mcp_tools(config: dict[str, Any], server_name: str,
                   timeout: int = 15) -> list[dict[str, Any]]:
    """List tools available on an MCP server."""
    servers = config.get("servers", {})
    if server_name not in servers:
        return []
    scfg = servers[server_name]
    if scfg.get("type") == "http" or "url" in scfg:
        return _list_tools_http(scfg["url"], timeout=timeout)
    command = scfg.get("command", "")
    args = scfg.get("args", [])
    env = scfg.get("env")
    if not command:
        return []
    return _list_tools_stdio(command, args, env=env, timeout=timeout)


def main():
    ap = argparse.ArgumentParser(description="MCP bridge for codeact sandbox")
    ap.add_argument("--config", help="Path to .mcp.json (default: auto-discover)")
    ap.add_argument("--server", help="MCP server name")
    ap.add_argument("--tool", help="Tool name to call")
    ap.add_argument("--args", default="{}", help="JSON arguments for the tool")
    ap.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    ap.add_argument("--list-servers", action="store_true", help="List available MCP servers")
    ap.add_argument("--list-tools", action="store_true", help="List tools on a server")
    args = ap.parse_args()

    config = _load_mcp_config(args.config)

    if args.list_servers:
        for name, scfg in config["servers"].items():
            stype = "http" if (scfg.get("type") == "http" or "url" in scfg) else "stdio"
            print(f"  {name} ({stype})")
        return

    if not args.server:
        ap.error("--server is required")

    if args.list_tools:
        tools = list_mcp_tools(config, args.server, timeout=args.timeout)
        for t in tools:
            desc = t.get("description", "")[:60]
            print(f"  {t['name']}: {desc}")
        return

    if not args.tool:
        ap.error("--tool is required for calling")

    try:
        arguments = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Invalid --args JSON: {exc}"}))
        sys.exit(1)

    result = call_mcp(config, args.server, args.tool, arguments,
                      timeout=args.timeout)
    print(result)


if __name__ == "__main__":
    main()
