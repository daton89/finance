#!/usr/bin/env python3
"""
daily_digest.py — Morning Digest Agent.

Runs the four deterministic finance scripts (risk_agent, research_agent,
portfolio_manager default, portfolio_manager transition) as subprocesses in their
default report mode (no args) and concatenates their stdout into one combined,
clearly delimited output. Intended to be consumed by an LLM synthesis
step (Hermes cron agent) that turns this into a short Telegram verdict.

A single failing/timing-out script does not abort the digest — its
section is replaced with a "SCRIPT FAILED" notice containing the error.
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

PACKAGES_DIR = Path(__file__).resolve().parent.parent

TIMEOUT_SECONDS = 120

SCRIPTS = [
    ("RISK", ["scripts/risk_agent.py"]),
    ("RESEARCH", ["scripts/research_agent.py"]),
    ("PORTFOLIO", ["scripts/portfolio_manager.py"]),
    # Progresso transizione ETF-only (ADR-0001/0002)
    ("TRANSITION", ["scripts/portfolio_manager.py", "transition"]),
]


def run_script(cmd: list[str]) -> str:
    """Run a script as a subprocess and return its combined output.

    Never raises — any failure (nonzero exit, timeout, exception) is
    captured and returned as a "SCRIPT FAILED" section instead.
    """
    script_path = " ".join(cmd)
    try:
        result = subprocess.run(
            [sys.executable, *cmd],
            cwd=PACKAGES_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"SCRIPT FAILED: {script_path}: timed out after {TIMEOUT_SECONDS}s"
    except Exception as exc:
        return f"SCRIPT FAILED: {script_path}: {exc}"

    if result.returncode != 0:
        err = (result.stderr or "").strip() or "no stderr output"
        # Include any partial stdout too, since it may still contain useful info.
        out = (result.stdout or "").strip()
        section = f"SCRIPT FAILED: {script_path}: exit code {result.returncode}: {err}"
        if out:
            section += f"\n\n--- partial stdout ---\n{out}"
        return section

    return (result.stdout or "").strip() or "(no output)"


def main() -> None:
    today = date.today().isoformat()
    lines = [f"DAILY DIGEST — {today}", ""]

    for label, cmd in SCRIPTS:
        lines.append(f"=== {label} ===")
        lines.append(run_script(cmd))
        lines.append("")

    print("\n".join(lines).rstrip() + "\n")


if __name__ == "__main__":
    main()
