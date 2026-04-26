#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""run_tests.py — Test harness for codeact plugin.

Runs prompts through copilot CLI, captures JSONL output, extracts metrics,
and compares baseline vs codeact arms.

Usage:
    # Full end-to-end run (creates temp workspace, runs all tests, cleans up)
    python3 run_tests.py all

    # Sub-runs (caller supplies workspace + plugin dir)
    python3 run_tests.py functional --prompts prompts/functional.json \\
        --workspace /tmp/test --plugin-dir ./plugins/codeact

    python3 run_tests.py perf --prompts prompts/perf.json \\
        --workspace /tmp/test --plugin-dir ./plugins/codeact \\
        --min-token-reduction 40
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunMetrics:
    """Metrics extracted from a copilot CLI JSONL run."""

    prompt_id: str = ""
    arm: str = ""
    output_tokens: int = 0
    input_tokens: int = 0
    api_turns: int = 0
    premium_requests: int = 0
    api_duration_ms: int = 0
    session_duration_ms: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    assistant_text: str = ""
    codeact_invoked: bool = False
    codeact_evidence: list[str] = field(default_factory=list)
    success: bool = False
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""
    # Context bloat: total bytes of tool results returned to the conversation.
    # Each turn re-sends prior results as context, so this directly correlates
    # with input token cost. Lower = less context replay per turn.
    tool_result_bytes: int = 0


# Module-level switch flipped by --verbose.
VERBOSE = False

# Default cap for evidence / tool-call printout. Long enough to show the
# command up to the start of inline `--code` payloads, short enough to keep
# normal-mode output scannable. --verbose disables truncation entirely.
EVIDENCE_TRUNCATE = 300


def _truncate(s: str, limit: int = EVIDENCE_TRUNCATE) -> str:
    if VERBOSE or len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def run_copilot(
    prompt: str,
    workspace: str,
    plugin_dir: str | None = None,
    no_custom_instructions: bool = False,
    timeout: int = 180,
    log_label: str | None = None,
    agent: str | None = None,
    disable_mcp_servers: list[str] | None = None,
    allow_tools: list[str] | None = None,
    deny_tools: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    """Run a prompt through copilot CLI. Returns (parsed JSONL events, stdout, stderr).

    Stdout + stderr are also written to ``<workspace>/.copilot-logs/<label>.{out,err}``
    so verbose mode can cat them cleanly instead of inlining huge JSONL blobs.
    """
    # Snapshot process logs before run so we can find the new one after
    log_glob = str(Path.home() / ".copilot" / "logs" / "process-*.log")
    import glob as _glob_mod
    logs_before = set(_glob_mod.glob(log_glob))
    cmd = [
        "copilot",
        "-p", prompt,
        "--output-format", "json",
        "--yolo",
        "-s",
    ]
    if plugin_dir:
        cmd += ["--plugin-dir", str(Path(plugin_dir).resolve())]
    if no_custom_instructions:
        cmd += ["--no-custom-instructions"]
    if disable_mcp_servers:
        for server in disable_mcp_servers:
            cmd += ["--disable-mcp-server", server]
    if allow_tools:
        for tool in allow_tools:
            cmd += ["--allow-tool", tool]
    if deny_tools:
        for tool in deny_tools:
            cmd += ["--deny-tool", tool]
    if agent:
        cmd += ["--agent", agent]

    env = os.environ.copy()
    env["CODEACT_MODE"] = "off"  # Don't interfere with baseline
    # Scope custom-tool config to the workspace so prompts can drop tools
    # into <workspace>/.codeact-config/tools/ without touching the user's
    # real ~/.config/codeact. No-op for prompts that don't use custom tools.
    env["CODEACT_CONFIG_DIR"] = str(Path(workspace) / ".codeact-config")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace,
            env=env,
        )
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT after {timeout}s", file=sys.stderr)
        return [], "", ""

    # Persist raw output to the workspace so verbose mode (or post-mortem
    # debugging with --keep-workspace) can inspect them with `cat`/`jq`
    # rather than scrolling through inlined logs.
    log_dir = Path(workspace) / ".copilot-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    label = log_label or f"run-{int(time.time() * 1000)}"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)
    out_path = log_dir / f"{safe}.out.jsonl"
    err_path = log_dir / f"{safe}.err.txt"
    out_path.write_text(result.stdout)
    err_path.write_text(result.stderr)

    if VERBOSE:
        print(f"\n--- copilot stdout: {out_path} ---", file=sys.stderr)
        print(result.stdout.rstrip(), file=sys.stderr)
        if result.stderr.strip():
            print(f"--- copilot stderr: {err_path} ---", file=sys.stderr)
            print(result.stderr.rstrip(), file=sys.stderr)
        print("--- end copilot output ---\n", file=sys.stderr)

    events = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Extract prompt_tokens from process log (total input context per turn)
    prompt_tokens_per_turn = _extract_prompt_tokens(logs_before, log_glob)
    if prompt_tokens_per_turn:
        # Stash on events so extract_metrics can use them
        events.append({
            "type": "_prompt_tokens",
            "data": {
                "per_turn": prompt_tokens_per_turn,
                "total": sum(prompt_tokens_per_turn),
            },
        })

    return events, result.stdout, result.stderr


def _extract_prompt_tokens(logs_before: set, log_glob: str) -> list[int]:
    """Find the new process log and extract prompt_tokens per turn."""
    import glob as _glob_mod
    logs_after = set(_glob_mod.glob(log_glob))
    new_logs = logs_after - logs_before
    if not new_logs:
        return []
    # Pick the newest
    log_path = max(new_logs, key=os.path.getmtime)
    try:
        content = Path(log_path).read_text(errors="replace")
    except OSError:
        return []
    # Extract prompt_tokens values (filter small values from metadata calls)
    tokens = [int(m) for m in re.findall(r'"prompt_tokens":\s*(\d+)', content)
              if int(m) > 1000]
    return tokens


# Match a real codeact dispatch invocation (the `scripts/codeact` shell script
# or the `codeact` CLI symlinked on PATH), not arbitrary mentions of the word.
_CODEACT_CMD_RE = re.compile(
    r"(?:^|[\s/&;|`])(?:bash\s+\S*scripts/codeact|\.?/?\S*scripts/codeact|(?<![\w.-])codeact)\b",
)


def _is_codeact_command(cmd: str) -> bool:
    if not cmd:
        return False
    return bool(_CODEACT_CMD_RE.search(cmd))


def extract_metrics(events: list[dict[str, Any]], prompt_id: str, arm: str) -> RunMetrics:
    """Extract metrics from JSONL events."""
    m = RunMetrics(prompt_id=prompt_id, arm=arm, raw_events=events)

    for ev in events:
        ev_type = ev.get("type", "")
        data = ev.get("data", {})

        if ev_type == "assistant.message":
            m.output_tokens += data.get("outputTokens", 0)
            m.input_tokens += data.get("inputTokens", 0)
            m.api_turns += 1
            m.assistant_text += data.get("content", "")

            tool_requests = data.get("toolRequests", [])
            for tr in tool_requests:
                tool_name = tr.get("name", tr.get("toolName", ""))
                m.tool_calls.append(tr)
                m.tool_names.append(tool_name)

                # Check if codeact was invoked via skill tool or bash
                if tool_name == "skill":
                    args = tr.get("arguments", {})
                    skill_name = args.get("skill", "") if isinstance(args, dict) else str(args)
                    if "codeact" in skill_name:
                        m.codeact_invoked = True
                        m.codeact_evidence.append(f"skill tool: {skill_name!r}")
                elif tool_name in ("bash", "shell"):
                    args = tr.get("arguments", tr.get("toolInput", {}))
                    cmd = args.get("command", "") if isinstance(args, dict) else str(args)
                    if _is_codeact_command(cmd):
                        m.codeact_invoked = True
                        m.codeact_evidence.append(f"{tool_name} args: {cmd}")

        elif ev_type == "tool.execution_start":
            # Also track tool names from execution events
            tool_name = data.get("toolName", "")
            args = data.get("arguments", {})
            if tool_name in ("bash", "shell"):
                cmd = args.get("command", "") if isinstance(args, dict) else str(args)
                if _is_codeact_command(cmd):
                    m.codeact_invoked = True
                    m.codeact_evidence.append(f"{tool_name} exec: {cmd}")

        elif ev_type == "tool.execution_complete":
            # Measure tool result content — this is context that gets replayed
            # on subsequent turns, so it directly drives input token cost.
            result = data.get("result", {})
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            m.tool_result_bytes += len(content.encode("utf-8", errors="replace"))

        elif ev_type == "tool.execution_partial_result":
            # Partial results also contribute to context if replayed
            content = data.get("partialOutput", "")
            m.tool_result_bytes += len(content.encode("utf-8", errors="replace"))

        elif ev_type == "result":
            usage = data.get("usage", ev.get("usage", {}))
            m.premium_requests = usage.get("premiumRequests", 0)
            m.api_duration_ms = usage.get("totalApiDurationMs", 0)
            m.session_duration_ms = usage.get("sessionDurationMs", 0)
            m.success = True

        elif ev_type == "_prompt_tokens":
            # Injected by run_copilot from process log
            m.input_tokens = data.get("total", 0)

    return m


def materialise_files(specs: list[dict[str, Any]], workspace: Path,
                      fixtures_dir: Path) -> list[Path]:
    """Drop per-prompt files into the workspace. Returns paths created."""
    created: list[Path] = []
    for spec in specs:
        raw = spec["path"].replace("${WORKSPACE}", str(workspace))
        dest = Path(raw)
        if not dest.is_absolute():
            dest = workspace / dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "from" in spec:
            src = (fixtures_dir / spec["from"]).resolve()
            dest.write_bytes(src.read_bytes())
        else:
            dest.write_text(spec.get("content", ""))
        created.append(dest)
    return created


def cleanup_files(paths: list[Path]) -> None:
    """Remove files (and empty parents) created by materialise_files."""
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    # Best-effort prune empty parent dirs
    for p in paths:
        for parent in p.parents:
            try:
                parent.rmdir()
            except OSError:
                break


def run_functional_tests(
    prompts_file: str,
    workspace: str,
    plugin_dir: str,
    agent: str | None = None,
    label: str = "functional",
) -> bool:
    """Run functional tests: verify codeact is/isn't invoked as expected.

    `agent`: passed through to `copilot --agent <name>` (e.g. "codeact").
    `label`: used in the section header + log filenames so multiple suites
             in one run don't overwrite each other's logs.
    """
    prompts = json.loads(Path(prompts_file).read_text())
    all_passed = True

    suffix = f" (agent={agent})" if agent else ""
    print(f"\n=== {label.upper()} TESTS{suffix} ===\n")
    print(f"  prompts: {prompts_file}")

    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    workspace_path = Path(workspace)

    for p in prompts:
        pid = p["id"]
        prompt_text = p["prompt"]
        assertions = p.get("assertions", {})

        # Materialise per-prompt files into the workspace before the run.
        # Schema: "files": [{"path": "...", "content": "..."} or {"path": "...", "from": "rel/to/fixtures"}]
        created = materialise_files(p.get("files", []), workspace_path, fixtures_dir)

        print(f"  [{pid}] Running...", end="", flush=True)

        try:
            events, stdout, stderr = run_copilot(
                prompt=prompt_text,
                workspace=workspace,
                plugin_dir=plugin_dir,
                log_label=f"{label}-{pid}",
                agent=agent,
            )
        finally:
            cleanup_files(created)

        metrics = extract_metrics(events, pid, "codeact")

        # Check assertions
        passed = True
        failures = []

        # codeact_invoked assertion
        if "codeact_invoked" in assertions:
            expected = assertions["codeact_invoked"]
            if metrics.codeact_invoked != expected:
                passed = False
                failures.append(
                    f"codeact_invoked: expected={expected}, got={metrics.codeact_invoked}"
                )

        # output_contains assertions
        for needle in assertions.get("output_contains", []):
            # Check in assistant text AND all event content
            combined = metrics.assistant_text.lower()
            for ev in events:
                data = ev.get("data", {})
                combined += str(data.get("content", "")).lower()
                combined += str(data.get("result", "")).lower()
                combined += str(data.get("partialOutput", "")).lower()
            if needle.lower() not in combined:
                passed = False
                failures.append(f"output missing: '{needle}'")

        if passed:
            print(f" PASS (tokens={metrics.output_tokens}, "
                  f"tools={len(metrics.tool_calls)}, "
                  f"codeact={'yes' if metrics.codeact_invoked else 'no'})")
        else:
            print(f" FAIL")
            for f in failures:
                print(f"    - {f}")
            all_passed = False

        # Always show evidence when codeact_invoked was checked OR when the
        # heuristic fired, so reviewers can audit. In verbose mode, also
        # dump tool calls with their arguments so it's clear *what* the
        # agent ran.
        show_evidence = (
            "codeact_invoked" in assertions
            or metrics.codeact_invoked
            or VERBOSE
        )
        if show_evidence:
            if metrics.codeact_evidence:
                print(f"    codeact evidence:")
                for e in metrics.codeact_evidence:
                    print(f"      \u2022 {_truncate(e)}")
            elif metrics.codeact_invoked:
                print(f"    codeact evidence: (none recorded)")
        if VERBOSE and metrics.tool_calls:
            print(f"    tools called ({len(metrics.tool_calls)}):")
            for tc in metrics.tool_calls:
                name = tc.get("name", tc.get("toolName", "?"))
                args = tc.get("arguments", tc.get("toolInput", {}))
                if isinstance(args, dict):
                    # Prefer common single-field summaries when present.
                    summary = (
                        args.get("command")
                        or args.get("path")
                        or args.get("pattern")
                        or args.get("code")
                        or json.dumps(args, ensure_ascii=False)
                    )
                else:
                    summary = str(args)
                summary = _truncate(summary)
                # Indent multi-line commands so they read cleanly under the bullet.
                lines = summary.splitlines() or [""]
                print(f"      \u2022 {name}: {lines[0]}")
                for ln in lines[1:]:
                    print(f"        {ln}")

    return all_passed


def run_perf_tests(
    prompts_file: str,
    workspace: str,
    plugin_dir: str,
    min_token_reduction: int = 40,
    agent: str | None = None,
) -> bool:
    """Run perf tests: compare baseline vs codeact token usage."""
    prompts = json.loads(Path(prompts_file).read_text())
    all_passed = True
    results = []

    print("\n=== PERFORMANCE TESTS ===\n")

    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    workspace_path = Path(workspace)

    for p in prompts:
        pid = p["id"]
        prompt_text = p["prompt"]

        # Per-prompt fixture files (e.g. .mcp.json to load extra MCP servers).
        # Same schema as functional tests; cleaned up after the prompt runs.
        created = materialise_files(p.get("files", []), workspace_path, fixtures_dir)

        try:
            # --- Baseline arm ---
            print(f"  [{pid}] baseline...", end="", flush=True)
            baseline_events, _, _ = run_copilot(
                prompt=prompt_text,
                workspace=workspace,
                plugin_dir=None,
                no_custom_instructions=True,
                log_label=f"perf-{pid}-baseline",
            )
            baseline = extract_metrics(baseline_events, pid, "baseline")
            print(f" out={baseline.output_tokens}, in={baseline.input_tokens}, "
                  f"tools={len(baseline.tool_calls)}, "
                  f"turns={baseline.api_turns}")

            # Small delay between runs
            time.sleep(2)

            # --- CodeAct arm (add hint to trigger codeact) ---
            codeact_prompt = prompt_text + " Use codeact to do this in a single sandbox run."
            print(f"  [{pid}] codeact...", end="", flush=True)

            # If prompt opts into MCP disabling, extract server names from the
            # materialised .mcp.json so the codeact arm can't use external MCP
            # tools — it must go through the sandbox's mcp_call() bridge.
            disable_mcp: list[str] | None = None
            deny_external: list[str] | None = None
            if p.get("disable_mcp_in_codeact"):
                for spec in p.get("files", []):
                    if spec.get("path", "").endswith(".mcp.json"):
                        try:
                            cfg = json.loads(spec.get("content", "{}"))
                            servers = cfg.get("mcpServers", cfg.get("servers", {}))
                            disable_mcp = list(servers.keys())
                        except Exception:
                            pass

            codeact_events, _, _ = run_copilot(
                prompt=codeact_prompt,
                workspace=workspace,
                plugin_dir=plugin_dir,
                log_label=f"perf-{pid}-codeact",
                agent=agent,
                disable_mcp_servers=disable_mcp,
                deny_tools=deny_external,
            )
            codeact = extract_metrics(codeact_events, pid, "codeact")
            print(f" out={codeact.output_tokens}, in={codeact.input_tokens}, "
                  f"tools={len(codeact.tool_calls)}, "
                  f"turns={codeact.api_turns}")

            # --- Check for failed arms ---
            # A run with 0 output tokens AND 0 turns means it timed out or
            # never produced a response. Mark it rather than computing
            # misleading 100% reductions.
            baseline_failed = baseline.output_tokens == 0 and baseline.api_turns == 0
            codeact_failed = codeact.output_tokens == 0 and codeact.api_turns == 0

            # --- Compare ---
            # Cost estimate for comparison only. Pricing based on GPT-5.4
            # ($2.50/M input, $15/M output) — actual costs vary by model.
            INPUT_COST_PER_M = 2.50
            OUTPUT_COST_PER_M = 15.0

            def _est_cost(m: RunMetrics) -> float:
                return (m.input_tokens * INPUT_COST_PER_M
                        + m.output_tokens * OUTPUT_COST_PER_M) / 1_000_000

            baseline_cost = _est_cost(baseline)
            codeact_cost = _est_cost(codeact)

            if baseline.output_tokens > 0:
                token_reduction = (
                    (baseline.output_tokens - codeact.output_tokens)
                    / baseline.output_tokens
                    * 100
                )
            else:
                token_reduction = 0

            if baseline.input_tokens > 0:
                input_token_reduction = (
                    (baseline.input_tokens - codeact.input_tokens)
                    / baseline.input_tokens
                    * 100
                )
            else:
                input_token_reduction = 0

            cost_reduction = 0.0
            if baseline_cost > 0:
                cost_reduction = (
                    (baseline_cost - codeact_cost) / baseline_cost * 100
                )

            if baseline.premium_requests > 0:
                request_reduction = (
                    (baseline.premium_requests - codeact.premium_requests)
                    / baseline.premium_requests
                    * 100
                )
            else:
                request_reduction = 0

            tool_reduction = 0
            if len(baseline.tool_calls) > 0:
                tool_reduction = (
                    (len(baseline.tool_calls) - len(codeact.tool_calls))
                    / len(baseline.tool_calls)
                    * 100
                )

            turn_reduction = 0
            if baseline.api_turns > 0:
                turn_reduction = (
                    (baseline.api_turns - codeact.api_turns)
                    / baseline.api_turns
                    * 100
                )

            context_reduction = 0
            if baseline.tool_result_bytes > 0:
                context_reduction = (
                    (baseline.tool_result_bytes - codeact.tool_result_bytes)
                    / baseline.tool_result_bytes
                    * 100
                )

            # If either arm failed, null out percentages to avoid misleading data
            comparison_valid = not baseline_failed and not codeact_failed
            if not comparison_valid:
                token_reduction = input_token_reduction = cost_reduction = 0
                request_reduction = tool_reduction = turn_reduction = context_reduction = 0

            result = {
                "prompt_id": pid,
                "status": "valid" if comparison_valid else (
                    "baseline_failed" if baseline_failed else "codeact_failed"),
                "baseline_tokens": baseline.output_tokens,
                "codeact_tokens": codeact.output_tokens,
                "token_reduction_pct": round(token_reduction, 1),
                "baseline_input_tokens": baseline.input_tokens,
                "codeact_input_tokens": codeact.input_tokens,
                "input_token_reduction_pct": round(input_token_reduction, 1),
                "baseline_cost_est": round(baseline_cost, 6),
                "codeact_cost_est": round(codeact_cost, 6),
                "cost_reduction_pct": round(cost_reduction, 1),
                "baseline_requests": baseline.premium_requests,
                "codeact_requests": codeact.premium_requests,
                "request_reduction_pct": round(request_reduction, 1),
                "baseline_tool_calls": len(baseline.tool_calls),
                "codeact_tool_calls": len(codeact.tool_calls),
                "tool_reduction_pct": round(tool_reduction, 1),
                "baseline_turns": baseline.api_turns,
                "codeact_turns": codeact.api_turns,
                "turn_reduction_pct": round(turn_reduction, 1),
                "baseline_context_bytes": baseline.tool_result_bytes,
                "codeact_context_bytes": codeact.tool_result_bytes,
                "context_reduction_pct": round(context_reduction, 1),
                "codeact_invoked": codeact.codeact_invoked,
                "baseline_tools": baseline.tool_names,
                "codeact_tools": codeact.tool_names,
                "min_token_reduction": p.get("min_token_reduction"),
            }
            results.append(result)

            if not comparison_valid:
                label = "BASELINE FAILED" if baseline_failed else "CODEACT FAILED"
                print(f"  [{pid}] {label} — skipping comparison")
            else:
                print(f"  [{pid}] Δ tokens={token_reduction:+.1f}%, "
                      f"tools={tool_reduction:+.1f}%, "
                      f"turns={turn_reduction:+.1f}%, "
                      f"context={context_reduction:+.1f}%")

            prompt_min = p.get("min_token_reduction", min_token_reduction)
            if token_reduction < prompt_min - 0.05:
                print(f"  [{pid}] WARN: token reduction {token_reduction:.1f}% "
                      f"< threshold {prompt_min}%")

            print()
        finally:
            cleanup_files(created)

    # --- Summary table ---
    print("\n" + "=" * 130)
    print(f"{'Prompt':<25} {'Arm':<10} {'OutTok':>8} {'InTok':>8} {'Tools':>7} {'Turns':>7} {'CtxKB':>7} {'Cost$':>10}")
    print("-" * 130)

    for r in results:
        if r.get("status") != "valid":
            print(f"{r['prompt_id']:<25} {'SKIPPED':<10} {r['status']}")
            print()
            continue
        bl_ctx = r['baseline_context_bytes'] / 1024
        ca_ctx = r['codeact_context_bytes'] / 1024
        print(f"{r['prompt_id']:<25} {'baseline':<10} "
              f"{r['baseline_tokens']:>8} "
              f"{r['baseline_input_tokens']:>8} "
              f"{r['baseline_tool_calls']:>7} "
              f"{r['baseline_turns']:>7} "
              f"{bl_ctx:>6.1f}K "
              f"{r['baseline_cost_est']:>10.6f}")
        print(f"{'':<25} {'codeact':<10} "
              f"{r['codeact_tokens']:>8} "
              f"{r['codeact_input_tokens']:>8} "
              f"{r['codeact_tool_calls']:>7} "
              f"{r['codeact_turns']:>7} "
              f"{ca_ctx:>6.1f}K "
              f"{r['codeact_cost_est']:>10.6f}")
        print(f"{'':<25} {'Δ':<10} "
              f"{r['token_reduction_pct']:>+7.1f}% "
              f"{r['input_token_reduction_pct']:>+7.1f}% "
              f"{r['tool_reduction_pct']:>+6.1f}% "
              f"{r['turn_reduction_pct']:>+6.1f}% "
              f"{r['context_reduction_pct']:>+6.1f}% "
              f"{r['cost_reduction_pct']:>+9.1f}%")
        print()

    print("=" * 80)

    # --- Pass/fail ---
    passing = True
    valid_results = [r for r in results if r.get("status") == "valid"]
    failed_results = [r for r in results if r.get("status") != "valid"]
    if failed_results:
        print(f"SKIPPED: {len(failed_results)} prompt(s) had failed arms "
              f"({', '.join(r['prompt_id'] for r in failed_results)})")
    for r in valid_results:
        # Per-prompt threshold overrides the global min_token_reduction.
        # File-analysis prompts can't beat bash+python3 one-liners, so they
        # get a relaxed threshold (or None to skip). MCP prompts keep the
        # global threshold.
        prompt_threshold = r.get("min_token_reduction")
        effective_threshold = (
            prompt_threshold if prompt_threshold is not None
            else min_token_reduction
        )
        if r["token_reduction_pct"] < effective_threshold:
            print(f"FAIL: {r['prompt_id']} token reduction "
                  f"{r['token_reduction_pct']}% < {effective_threshold}%")
            passing = False
        if not r["codeact_invoked"]:
            print(f"WARN: {r['prompt_id']} codeact was not invoked in codeact arm")

    if passing:
        print(f"\nPASS: All prompts show ≥{min_token_reduction}% token reduction.")
    else:
        all_passed = False

    # Save results: timestamped file + `latest.json` symlink/copy for convenience
    from datetime import datetime, timezone
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "timestamp": stamp,
        "min_token_reduction": min_token_reduction,
        "results": results,
    }
    results_file = results_dir / f"perf-results-{stamp}.json"
    results_file.write_text(json.dumps(payload, indent=2))
    latest = results_dir / "perf-results-latest.json"
    latest.write_text(json.dumps(payload, indent=2))
    print(f"\nResults saved to {results_file}")
    print(f"Latest pointer:   {latest}")

    return all_passed


def preflight(plugin_dir: Path) -> None:
    """Verify copilot CLI, python3, and plugin manifest exist."""
    print("=== CODEACT TEST HARNESS ===\n")
    import shutil

    if not shutil.which("copilot"):
        print("ERROR: copilot CLI not found. Install from https://docs.github.com/copilot",
              file=sys.stderr)
        sys.exit(1)
    try:
        ver = subprocess.run(["copilot", "--version"], capture_output=True, text=True, timeout=5)
        print(f"copilot: {ver.stdout.strip() or 'installed'}")
    except Exception:
        print("copilot: installed")

    print(f"python3: {sys.version.split()[0]}")

    if not (plugin_dir / "plugin.json").is_file():
        print(f"ERROR: plugin.json not found at {plugin_dir}", file=sys.stderr)
        sys.exit(1)
    print(f"plugin: {plugin_dir}")


def setup_workspace(tests_dir: Path) -> Path:
    """Run setup-workspace.sh fixture, return temp workspace path."""
    print("\nCreating test workspace...")
    result = subprocess.run(
        ["bash", str(tests_dir / "fixtures" / "setup-workspace.sh")],
        capture_output=True, text=True, check=True,
    )
    workspace = Path(result.stdout.strip())
    print(f"Workspace: {workspace}")
    return workspace


def clone_plugin(plugin_dir: Path) -> Path:
    """Copy the plugin tree to a temp dir so /codeact-install can rewrite
    its agent file + .codeact-tools.json without dirtying the dev's tree."""
    import shutil as _shutil
    import tempfile
    dst = Path(tempfile.mkdtemp(prefix="codeact-plugin-"))
    # copytree needs the leaf to not exist
    _shutil.rmtree(dst)
    _shutil.copytree(plugin_dir, dst, symlinks=True)
    print(f"Plugin clone: {dst}")
    return dst


def prime_workspace(workspace: Path, plugin_dir: Path,
                    backend: str | None = None) -> None:
    """Invoke the appropriate codeact-install skill in the workspace via
    copilot CLI so the agent writes .github/instructions/codeact.instructions.md
    (and updates the agent file + .codeact-backend marker) — same path a real
    user takes. Without this the workspace has no codeact instructions layer
    and prompts under-fire.

    `backend` selects which install skill to run:
      None       -> /codeact-install            (auto-detect)
      "monty"    -> /codeact-install-monty
      "hyperlight" -> /codeact-install-hyperlight
    """
    skill_map = {
        None: "/codeact-install",
        "monty": "/codeact-install-monty",
        "hyperlight": "/codeact-install-hyperlight",
    }
    skill = skill_map.get(backend)
    if skill is None:
        raise ValueError(f"Unknown backend for prime_workspace: {backend!r}")
    label = backend or "auto"
    print(f"\nPriming workspace with {skill} (backend={label}) ...")
    cmd = [
        "copilot",
        "-p", skill,
        "--output-format", "json",
        "--yolo",
        "-s",
        "--plugin-dir", str(plugin_dir.resolve()),
    ]
    env = os.environ.copy()
    env["CODEACT_CONFIG_DIR"] = str(workspace / ".codeact-config")
    try:
        r = subprocess.run(
            cmd, cwd=str(workspace), capture_output=True, text=True, timeout=180, env=env,
        )
    except subprocess.TimeoutExpired:
        print("  WARNING: /codeact-install timed out after 180s")
        return

    instructions = workspace / ".github" / "instructions" / "codeact.instructions.md"
    agent_file = plugin_dir / "agents" / "codeact.agent.md"
    if instructions.is_file():
        print(f"  OK: wrote {instructions.relative_to(workspace)}")
    else:
        print(f"  WARNING: /codeact-install did not produce {instructions}")
        if VERBOSE:
            print("--- /codeact-install stdout ---", file=sys.stderr)
            print(r.stdout, file=sys.stderr)
            print("--- /codeact-install stderr ---", file=sys.stderr)
            print(r.stderr, file=sys.stderr)
    if agent_file.is_file():
        print(f"  OK: agent file present at {agent_file}")
    else:
        print(f"  WARNING: agent file missing at {agent_file}")


def verify_plugin_loads(plugin_dir: Path) -> None:
    """Smoke-test that copilot CLI sees the plugin's skills."""
    print("\nVerifying plugin loads in copilot...")
    try:
        result = subprocess.run(
            ["copilot",
             "-p", "List your available skills. Just list the skill names, nothing else.",
             "--plugin-dir", str(plugin_dir),
             "--output-format", "json", "--yolo", "-s"],
            capture_output=True, text=True, timeout=60,
        )
        if "codeact" in result.stdout.lower():
            print("Plugin loaded successfully (codeact skills detected)")
        else:
            print("WARNING: Could not verify codeact skills in plugin output.")
            print("Continuing anyway — skill matching may still work.")
    except Exception as e:
        print(f"WARNING: plugin verify failed ({e}); continuing.")


def main() -> None:
    ap = argparse.ArgumentParser(description="CodeAct test harness")
    sub = ap.add_subparsers(dest="command", required=True)

    # all (default end-to-end runner — no required args)
    all_p = sub.add_parser("all", help="Run preflight + functional + perf with auto workspace")
    all_p.add_argument("--plugin-dir", default=None,
                       help="Plugin directory (default: parent of tests/)")
    all_p.add_argument("--min-token-reduction", type=int, default=40)
    all_p.add_argument("--keep-workspace", action="store_true",
                       help="Don't delete temp workspace on exit")
    all_p.add_argument("--suite",
                       choices=["hinted", "natural", "both"],
                       default="both",
                       help="Functional suite(s) to run (default: both).")
    all_p.add_argument("--agent", default=None,
                       help="Pass through as `copilot --agent <name>` for all "
                            "test runs (perf + functional).")

    # Functional tests
    func_p = sub.add_parser("functional", help="Run functional tests only")
    func_p.add_argument("--prompts", default=None,
                        help="Path to prompts JSON (overrides --suite). "
                             "Default: tests/prompts/functional.json")
    func_p.add_argument("--suite",
                        choices=["hinted", "natural", "both"],
                        default="hinted",
                        help="Which prompt suite to run. 'hinted' uses "
                             "functional.json (prompts include 'use codeact'). "
                             "'natural' uses functional-natural.json (no hint; "
                             "tests whether the agent picks codeact on its own). "
                             "'both' runs hinted then natural.")
    func_p.add_argument("--agent", default=None,
                        help="Pass through as `copilot --agent <name>`. "
                             "Use 'codeact' to force the plugin's custom agent.")
    func_p.add_argument("--workspace", default=None,
                        help="Test workspace path (default: auto-create + cleanup)")
    func_p.add_argument("--plugin-dir", default=None,
                        help="Plugin directory (default: parent of tests/)")
    func_p.add_argument("--keep-workspace", action="store_true",
                        help="Don't delete auto-created workspace on exit")

    # Perf tests
    perf_p = sub.add_parser("perf", help="Run performance tests only")
    perf_p.add_argument("--prompts", default=None,
                        help="Path to prompts JSON (default: tests/prompts/perf.json)")
    perf_p.add_argument("--workspace", default=None,
                        help="Test workspace path (default: auto-create + cleanup)")
    perf_p.add_argument("--plugin-dir", default=None,
                        help="Plugin directory (default: parent of tests/)")
    perf_p.add_argument("--min-token-reduction", type=int, default=40,
                        help="Minimum token reduction %% to pass (default: 40)")
    perf_p.add_argument("--keep-workspace", action="store_true",
                        help="Don't delete auto-created workspace on exit")
    perf_p.add_argument("--agent", default=None,
                        help="Pass through as `copilot --agent <name>` for the "
                             "codeact arm (baseline arm always runs without).")

    # Unit tests (no copilot CLI required)
    sub.add_parser("unit", help="Run unit tests only (tests/unit/, no copilot CLI)")

    for p in (all_p, func_p, perf_p):
        p.add_argument("-v", "--verbose", action="store_true",
                       help="Print copilot stdout/stderr and codeact-evidence per prompt")
        p.add_argument("--backend",
                       choices=["auto", "monty", "hyperlight", "all"],
                       default="auto",
                       help="Backend to test. 'all' loops monty + hyperlight "
                            "(fresh workspace + plugin clone per backend). "
                            "'auto' uses detect-backend.sh (default).")

    args = ap.parse_args()
    if getattr(args, "verbose", False):
        globals()["VERBOSE"] = True

    # Resolve the list of backends to iterate (None = auto-detect).
    backend_arg = getattr(args, "backend", "auto")
    if backend_arg == "all":
        backends: list[str | None] = ["monty", "hyperlight"]
    elif backend_arg == "auto":
        backends = [None]
    else:
        backends = [backend_arg]

    # Resolve functional suites: each suite is (label, prompts_file).
    def resolve_suites(tests_dir: Path) -> list[tuple[str, str]]:
        suite = getattr(args, "suite", "hinted")
        explicit = getattr(args, "prompts", None)
        if explicit:
            return [("functional", explicit)]
        suites: list[tuple[str, str]] = []
        if suite in ("hinted", "both"):
            suites.append(("functional",
                           str(tests_dir / "prompts" / "functional.json")))
        if suite in ("natural", "both"):
            suites.append(("functional-natural",
                           str(tests_dir / "prompts" / "functional-natural.json")))
        return suites

    agent = getattr(args, "agent", None)

    if args.command == "all":
        import shutil as _shutil
        tests_dir = Path(__file__).resolve().parent
        plugin_dir = Path(args.plugin_dir).resolve() if args.plugin_dir else tests_dir.parent
        preflight(plugin_dir)

        # Unit tests first — fast, no copilot CLI needed
        print("\n=== UNIT TESTS ===")
        unit_rc = subprocess.call(
            [sys.executable, "-m", "unittest", "discover",
             "-s", str(tests_dir / "unit"), "-v"],
        )
        unit_ok = unit_rc == 0

        per_backend: dict[str, bool] = {}
        for be in backends:
            label = be or "auto"
            print(f"\n############ BACKEND: {label} ############")
            workspace = setup_workspace(tests_dir)
            plugin_clone = clone_plugin(plugin_dir)
            try:
                verify_plugin_loads(plugin_clone)
                prime_workspace(workspace, plugin_clone, backend=be)
                suite_results = []
                for suite_label, suite_path in resolve_suites(tests_dir):
                    suite_results.append(run_functional_tests(
                        suite_path,
                        str(workspace), str(plugin_clone),
                        agent=agent, label=suite_label,
                    ))
                func_ok = all(suite_results)
                perf_ok = run_perf_tests(
                    str(tests_dir / "prompts" / "perf.json"),
                    str(workspace), str(plugin_clone),
                    args.min_token_reduction,
                    agent=agent,
                )
                per_backend[label] = func_ok and perf_ok
            finally:
                _shutil.rmtree(plugin_clone, ignore_errors=True)
                if not args.keep_workspace:
                    print(f"\nCleaning up {workspace}...")
                    _shutil.rmtree(workspace, ignore_errors=True)

        ok = unit_ok and all(per_backend.values())
        print("\n=== BACKEND SUMMARY ===")
        print(f"  unit: {'PASS' if unit_ok else 'FAIL'}")
        for label, passed in per_backend.items():
            print(f"  {label}: {'PASS' if passed else 'FAIL'}")
        print("\n" + ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    elif args.command == "functional":
        import shutil as _shutil
        tests_dir = Path(__file__).resolve().parent
        plugin_dir = Path(args.plugin_dir).resolve() if args.plugin_dir else tests_dir.parent
        suites = resolve_suites(tests_dir)
        preflight(plugin_dir)
        if args.workspace and len(backends) > 1:
            ap.error("--workspace cannot be combined with --backend all "
                     "(supplied workspace is already primed for one backend).")

        per_backend = {}
        for be in backends:
            label = be or "auto"
            print(f"\n############ BACKEND: {label} ############")
            if args.workspace:
                workspace = Path(args.workspace)
                owns_workspace = False
            else:
                workspace = setup_workspace(tests_dir)
                owns_workspace = True
            plugin_clone = clone_plugin(plugin_dir)
            try:
                verify_plugin_loads(plugin_clone)
                if owns_workspace:
                    prime_workspace(workspace, plugin_clone, backend=be)
                suite_results = []
                for suite_label, suite_path in suites:
                    suite_results.append(run_functional_tests(
                        suite_path, str(workspace), str(plugin_clone),
                        agent=agent, label=suite_label,
                    ))
                per_backend[label] = all(suite_results)
            finally:
                _shutil.rmtree(plugin_clone, ignore_errors=True)
                if owns_workspace and not args.keep_workspace:
                    print(f"\nCleaning up {workspace}...")
                    _shutil.rmtree(workspace, ignore_errors=True)
        if len(per_backend) > 1:
            print("\n=== BACKEND SUMMARY ===")
            for label, passed in per_backend.items():
                print(f"  {label}: {'PASS' if passed else 'FAIL'}")
        ok = all(per_backend.values())
    elif args.command == "perf":
        import shutil as _shutil
        tests_dir = Path(__file__).resolve().parent
        plugin_dir = Path(args.plugin_dir).resolve() if args.plugin_dir else tests_dir.parent
        prompts = args.prompts or str(tests_dir / "prompts" / "perf.json")
        preflight(plugin_dir)
        if args.workspace and len(backends) > 1:
            ap.error("--workspace cannot be combined with --backend all "
                     "(supplied workspace is already primed for one backend).")

        per_backend = {}
        for be in backends:
            label = be or "auto"
            print(f"\n############ BACKEND: {label} ############")
            if args.workspace:
                workspace = Path(args.workspace)
                owns_workspace = False
            else:
                workspace = setup_workspace(tests_dir)
                owns_workspace = True
            plugin_clone = clone_plugin(plugin_dir)
            try:
                verify_plugin_loads(plugin_clone)
                if owns_workspace:
                    prime_workspace(workspace, plugin_clone, backend=be)
                per_backend[label] = run_perf_tests(
                    prompts, str(workspace), str(plugin_clone),
                    args.min_token_reduction,
                    agent=agent)
            finally:
                _shutil.rmtree(plugin_clone, ignore_errors=True)
                if owns_workspace and not args.keep_workspace:
                    print(f"\nCleaning up {workspace}...")
                    _shutil.rmtree(workspace, ignore_errors=True)
        if len(per_backend) > 1:
            print("\n=== BACKEND SUMMARY ===")
            for label, passed in per_backend.items():
                print(f"  {label}: {'PASS' if passed else 'FAIL'}")
        ok = all(per_backend.values())
    elif args.command == "unit":
        tests_dir = Path(__file__).resolve().parent
        rc = subprocess.call(
            [sys.executable, "-m", "unittest", "discover",
             "-s", str(tests_dir / "unit"), "-v"],
        )
        ok = rc == 0
    else:
        ap.error(f"Unknown command: {args.command}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
