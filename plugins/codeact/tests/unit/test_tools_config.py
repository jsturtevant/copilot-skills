#!/usr/bin/env python3
"""test_tools_config.py — Unit tests for tool enable/disable + custom tool loading.

Exercises both backend codeact.py modules (monty + hyperlight) without needing
the actual sandbox runtimes — only the tool-discovery + filtering layer.

Run:
    python3 plugins/codeact/tests/test_tools_config.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "user-tools"


def _load_backend(name: str):
    """Load a backend's codeact.py as a module, isolated from sys.modules."""
    path = PLUGIN_DIR / "skills" / f"{name}-codeact" / "scripts" / "codeact.py"
    spec = importlib.util.spec_from_file_location(f"_codeact_{name}", path)
    assert spec and spec.loader, f"could not load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ToolConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="codeact-test-"))
        self.cfg_dir = self.tmp / "codeact"
        self.cfg_dir.mkdir()
        os.environ["CODEACT_CONFIG_DIR"] = str(self.cfg_dir)
        # Clean any leftover env from other tests
        for var in ("CODEACT_TOOLS", "CODEACT_DISABLE"):
            os.environ.pop(var, None)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        for var in ("CODEACT_CONFIG_DIR", "CODEACT_TOOLS", "CODEACT_DISABLE"):
            os.environ.pop(var, None)

    # ---- shared assertions, parameterised over backend ----

    def _check_baseline(self, backend_name: str) -> None:
        be = _load_backend(backend_name)
        tools = be.apply_user_config(be.discover_tools())
        names = {t["name"] for t in tools}
        # Built-ins always present (no filesystem deps required)
        for required in ("view", "create", "edit", "glob", "bash", "sql"):
            self.assertIn(required, names,
                          f"{backend_name}: missing builtin {required}")

    def _check_denylist_env(self, backend_name: str) -> None:
        os.environ["CODEACT_DISABLE"] = "bash, sql"
        be = _load_backend(backend_name)
        names = {t["name"] for t in be.apply_user_config(be.discover_tools())}
        self.assertNotIn("bash", names, f"{backend_name}: bash should be disabled")
        self.assertNotIn("sql", names, f"{backend_name}: sql should be disabled")
        self.assertIn("view", names)

    def _check_allowlist_env(self, backend_name: str) -> None:
        os.environ["CODEACT_TOOLS"] = "view,glob"
        be = _load_backend(backend_name)
        names = {t["name"] for t in be.apply_user_config(be.discover_tools())}
        self.assertEqual(names, {"view", "glob"},
                         f"{backend_name}: allowlist not honored, got {names}")

    def _check_config_file(self, backend_name: str) -> None:
        (self.cfg_dir / "config.json").write_text(
            '{"disabled": ["bash", "sql", "github_api", "web_fetch", "grep"]}')
        be = _load_backend(backend_name)
        names = {t["name"] for t in be.apply_user_config(be.discover_tools())}
        self.assertNotIn("bash", names)
        self.assertNotIn("sql", names)
        self.assertIn("view", names)

    def _check_custom_tool(self, backend_name: str) -> None:
        tools_dir = self.cfg_dir / "tools"
        tools_dir.mkdir()
        shutil.copy(FIXTURES / "shout.py", tools_dir / "shout.py")
        be = _load_backend(backend_name)
        tools = be.apply_user_config(be.discover_tools())
        shout = next((t for t in tools if t["name"] == "shout"), None)
        self.assertIsNotNone(shout, f"{backend_name}: custom tool not registered")
        self.assertEqual(shout["implementation"]["type"], "user")
        # Build handler and call it — must work for both backends
        handler = be._make_handler(shout)
        self.assertEqual(handler(text="hello"), "HELLO",
                         f"{backend_name}: handler returned wrong value")

    def _check_custom_tool_denylist(self, backend_name: str) -> None:
        tools_dir = self.cfg_dir / "tools"
        tools_dir.mkdir()
        shutil.copy(FIXTURES / "shout.py", tools_dir / "shout.py")
        os.environ["CODEACT_DISABLE"] = "shout"
        be = _load_backend(backend_name)
        names = {t["name"] for t in be.apply_user_config(be.discover_tools())}
        self.assertNotIn("shout", names,
                         f"{backend_name}: custom tool not filtered by denylist")

    def _check_bad_custom_tool_skipped(self, backend_name: str) -> None:
        tools_dir = self.cfg_dir / "tools"
        tools_dir.mkdir()
        (tools_dir / "broken.py").write_text("raise RuntimeError('boom')\n")
        be = _load_backend(backend_name)
        # Should not raise — bad tools are logged + skipped
        tools = be.apply_user_config(be.discover_tools())
        names = {t["name"] for t in tools}
        self.assertNotIn("broken", names)
        self.assertIn("view", names)  # baseline still works

    # ---- per-backend test methods (so unittest reports them separately) ----

    def test_monty_baseline(self):           self._check_baseline("monty")
    def test_hyperlight_baseline(self):      self._check_baseline("hyperlight")

    def test_monty_denylist_env(self):       self._check_denylist_env("monty")
    def test_hyperlight_denylist_env(self):  self._check_denylist_env("hyperlight")

    def test_monty_allowlist_env(self):      self._check_allowlist_env("monty")
    def test_hyperlight_allowlist_env(self): self._check_allowlist_env("hyperlight")

    def test_monty_config_file(self):        self._check_config_file("monty")
    def test_hyperlight_config_file(self):   self._check_config_file("hyperlight")

    def test_monty_custom_tool(self):        self._check_custom_tool("monty")
    def test_hyperlight_custom_tool(self):   self._check_custom_tool("hyperlight")

    def test_monty_custom_denylist(self):    self._check_custom_tool_denylist("monty")
    def test_hyperlight_custom_denylist(self): self._check_custom_tool_denylist("hyperlight")

    def test_monty_bad_tool_skipped(self):   self._check_bad_custom_tool_skipped("monty")
    def test_hyperlight_bad_tool_skipped(self): self._check_bad_custom_tool_skipped("hyperlight")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
