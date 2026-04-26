#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "matplotlib>=3.8",
#   "numpy>=1.26",
# ]
# ///
"""compare_results.py — Compare two perf-results JSON files from run_tests.py.

Usage:
    # Auto-compare: latest vs previous (no args needed)
    uv run compare_results.py

    # Explicit files
    uv run compare_results.py <baseline.json> <candidate.json> [--out plot.png]

    # With labels
    uv run compare_results.py before.json after.json --a-label before --b-label after

With no arguments, auto-discovers the two most recent perf-results-*.json files
in plugins/codeact/tests/results/ and compares them (older = A, newer = B).

Both files are produced by `run_tests.py perf` (or `all`) and live in
plugins/codeact/tests/results/. Prints a side-by-side delta table and, if
matplotlib is available, writes a grouped bar chart to the path given by
--out (default: tests/results/compare-<timestamp>.png).

Renders ASCII-only output if matplotlib isn't installed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load(path: Path) -> dict:
    """Load a perf-results file. Tolerates the old (bare list) format."""
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        return {"timestamp": "(unknown)", "results": raw}
    return raw


def index_by_id(results: list[dict]) -> dict[str, dict]:
    return {r["prompt_id"]: r for r in results}


def fmt_pct(v: float) -> str:
    return f"{v:+.1f}%"


def print_table(a_label: str, b_label: str, a: dict, b: dict) -> list[dict]:
    """Print delta table and return per-prompt comparison records."""
    a_idx = index_by_id(a["results"])
    b_idx = index_by_id(b["results"])
    common = sorted(set(a_idx) & set(b_idx))
    a_only = sorted(set(a_idx) - set(b_idx))
    b_only = sorted(set(b_idx) - set(a_idx))

    print(f"\n{a_label}: {a.get('timestamp', '?')}  ({len(a['results'])} prompts)")
    print(f"{b_label}: {b.get('timestamp', '?')}  ({len(b['results'])} prompts)")
    if a_only:
        print(f"  only in {a_label}: {', '.join(a_only)}")
    if b_only:
        print(f"  only in {b_label}: {', '.join(b_only)}")

    if not common:
        print("\nNo overlapping prompt IDs to compare.")
        return []

    metrics = [
        ("token_reduction_pct", "tok red %"),
        ("context_reduction_pct", "ctx red %"),
        ("cost_reduction_pct", "cost red %"),
        ("tool_reduction_pct",  "tool red %"),
        ("turn_reduction_pct",  "turn red %"),
        ("request_reduction_pct", "req red %"),
        ("codeact_tokens",      "codeact tok"),
        ("codeact_tool_calls",  "codeact tools"),
        ("codeact_turns",       "codeact turns"),
    ]
    header = f"{'prompt':<20} " + " ".join(f"{lbl:>14}" for _, lbl in metrics) * 1
    print("\n" + "=" * (20 + 16 * len(metrics) * 2))
    print(f"{'prompt':<20} " + "  ".join(f"{lbl + ' (A)':>14} {lbl + ' (B)':>14} {'Δ':>10}" for _, lbl in metrics))
    print("-" * (20 + 42 * len(metrics)))

    records = []
    for pid in common:
        ra, rb = a_idx[pid], b_idx[pid]
        row = [f"{pid:<20}"]
        rec = {"prompt_id": pid}
        for key, _ in metrics:
            va = ra.get(key, 0) or 0
            vb = rb.get(key, 0) or 0
            delta = vb - va
            rec[f"{key}_a"] = va
            rec[f"{key}_b"] = vb
            rec[f"{key}_delta"] = delta
            if isinstance(va, float) or isinstance(vb, float) or "pct" in key:
                row.append(f"{va:>14.1f} {vb:>14.1f} {delta:>+10.1f}")
            else:
                row.append(f"{va:>14d} {vb:>14d} {delta:>+10d}")
        print("  ".join(row))
        records.append(rec)
    print("=" * (20 + 42 * len(metrics)))
    return records


def plot(records: list[dict], a_label: str, b_label: str, out_path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\nmatplotlib not installed — skipping plot. "
              "Install with: pip install matplotlib", file=sys.stderr)
        return False

    if not records:
        return False

    pids = [r["prompt_id"] for r in records]
    metrics = [
        ("token_reduction_pct", "Output token reduction %"),
        ("cost_reduction_pct", "Estimated cost reduction %"),
        ("tool_reduction_pct", "Tool-call reduction %"),
        ("turn_reduction_pct", "API turn reduction %"),
    ]

    fig, axes = plt.subplots(len(metrics), 1, figsize=(max(8, len(pids) * 1.2), 9), sharex=True)
    if len(metrics) == 1:
        axes = [axes]

    width = 0.38
    import numpy as np
    x = np.arange(len(pids))

    for ax, (key, title) in zip(axes, metrics):
        a_vals = [r[f"{key}_a"] for r in records]
        b_vals = [r[f"{key}_b"] for r in records]
        ax.bar(x - width / 2, a_vals, width, label=a_label)
        ax.bar(x + width / 2, b_vals, width, label=b_label)
        ax.set_ylabel(title)
        ax.axhline(0, color="black", linewidth=0.5)
        ax.legend(loc="best")
        ax.grid(True, axis="y", alpha=0.3)

    axes[-1].set_xticks(x)
    axes[-1].set_xticklabels(pids, rotation=30, ha="right")
    fig.suptitle(f"CodeAct perf comparison: {a_label} vs {b_label}")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    print(f"\nPlot written to {out_path}")
    return True


def _find_recent_results(n: int = 2) -> list[Path]:
    """Find the N most recent perf-results-*.json files by filename timestamp."""
    results_dir = Path(__file__).parent / "results"
    files = sorted(results_dir.glob("perf-results-2*.json"), reverse=True)
    return files[:n]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compare two perf-results files. "
                    "With no args, auto-compares the two most recent results.")
    ap.add_argument("baseline", nargs="?", type=Path, default=None,
                    help="Older / reference results JSON (arm A). "
                         "Omit to auto-discover.")
    ap.add_argument("candidate", nargs="?", type=Path, default=None,
                    help="Newer / candidate results JSON (arm B). "
                         "Omit to auto-discover.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Plot output path (default: tests/results/compare-<ts>.png)")
    ap.add_argument("--a-label", default=None, help="Label for baseline arm")
    ap.add_argument("--b-label", default=None, help="Label for candidate arm")
    args = ap.parse_args()

    # Auto-discover if no files specified
    if args.baseline is None and args.candidate is None:
        recent = _find_recent_results(2)
        if len(recent) < 2:
            ap.error("Need at least 2 perf-results-*.json files in "
                     "tests/results/ for auto-compare. Run `run_tests.py perf` "
                     "at least twice, or specify files explicitly.")
        args.candidate = recent[0]  # newest
        args.baseline = recent[1]   # previous
        print(f"Auto-discovered:")
        print(f"  previous: {args.baseline.name}")
        print(f"  latest:   {args.candidate.name}")
    elif args.baseline is not None and args.candidate is None:
        # One file given — compare it against latest
        recent = _find_recent_results(1)
        if not recent:
            ap.error("No perf-results-*.json found for auto-compare.")
        args.candidate = recent[0]
        print(f"Comparing against latest: {args.candidate.name}")

    # Default labels from filenames
    if args.a_label is None:
        args.a_label = args.baseline.stem.replace("perf-results-", "")
    if args.b_label is None:
        args.b_label = args.candidate.stem.replace("perf-results-", "")

    for p in (args.baseline, args.candidate):
        if not p.is_file():
            ap.error(f"file not found: {p}")

    a = load(args.baseline)
    b = load(args.candidate)
    records = print_table(args.a_label, args.b_label, a, b)

    if not records:
        sys.exit(0)

    out = args.out
    if out is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = Path(__file__).parent / "results" / f"compare-{ts}.png"
    plot(records, args.a_label, args.b_label, out)


if __name__ == "__main__":
    main()
