#!/usr/bin/env python3
"""Hermes local preflight: reconcile machine/project facts without side effects.

This script is intentionally read-only. It reports grounded facts from the live
machine and flags contradictions against Hermes-local expectations so an agent
can cite evidence before making assumptions about ownership, path authority, or
runtime state.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREFERRED_PROJECT_PREFIX = Path(os.environ.get("HERMES_PROJECTS_DIR", Path.home() / "projects"))


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_fact(root: Path) -> dict[str, Any]:
    branch = _run(["git", "branch", "--show-current"], cwd=root)
    top = _run(["git", "rev-parse", "--show-toplevel"], cwd=root)
    status = _run(["git", "status", "--short"], cwd=root)
    remote = _run(["git", "remote", "get-url", "origin"], cwd=root)
    return {
        "top_level": top,
        "branch": branch,
        "dirty": bool(status),
        "status_short": status or "",
        "origin": remote,
    }


def _disk_fact(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_percent": round((usage.free / usage.total) * 100, 2) if usage.total else 0,
    }


def _memory_fact() -> dict[str, Any]:
    meminfo = Path("/proc/meminfo")
    facts: dict[str, Any] = {}
    if meminfo.exists():
        for line in meminfo.read_text(errors="ignore").splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                key, value = line.split(":", 1)
                facts[key.lower()] = value.strip()
    return facts


def collect_facts(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    hermes_home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    facts = {
        "node": "Romra",
        "scope": "Hermes local preflight; no peer-node or production authority inferred",
        "project_root": str(root),
        "preferred_project_prefix": str(PREFERRED_PROJECT_PREFIX),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "hermes_home": hermes_home,
        "cwd": os.getcwd(),
        "git": _git_fact(root),
        "disk": _disk_fact(root),
        "memory": _memory_fact(),
    }
    facts["contradictions"] = detect_contradictions(facts)
    return facts


def detect_contradictions(facts: dict[str, Any]) -> list[dict[str, str]]:
    contradictions: list[dict[str, str]] = []
    root = Path(str(facts.get("project_root", ""))).resolve()
    prefix = Path(str(facts.get("preferred_project_prefix", PREFERRED_PROJECT_PREFIX))).resolve()
    try:
        root.relative_to(prefix)
    except ValueError:
        contradictions.append({
            "id": "project_path_policy_mismatch",
            "expected": f"repo projects under {prefix}",
            "observed": str(root),
            "severity": "notice",
            "note": "Report-only: do not move/delete/archive without owner approval.",
        })

    git = facts.get("git") or {}
    branch = str(git.get("branch") or "")
    if branch in {"main", "master", "production", "prod"}:
        contradictions.append({
            "id": "protected_branch_worktree",
            "expected": "feature branch before repo mutations",
            "observed": branch,
            "severity": "warning",
            "note": "Local edits can be tested, but do not commit directly here.",
        })
    return contradictions


def render_text(facts: dict[str, Any]) -> str:
    lines = [
        "Hermes local preflight (read-only)",
        f"node: {facts['node']}",
        f"scope: {facts['scope']}",
        f"project_root: {facts['project_root']}",
        f"python: {facts['python']} ({facts['python_executable']})",
        f"platform: {facts['platform']} / {facts['machine']}",
        f"hermes_home: {facts['hermes_home']}",
        f"git_branch: {facts['git'].get('branch')}",
        f"git_dirty: {facts['git'].get('dirty')}",
        f"disk_free: {facts['disk'].get('free_bytes')} bytes ({facts['disk'].get('free_percent')}%)",
    ]
    contradictions = facts.get("contradictions") or []
    if contradictions:
        lines.append("contradictions:")
        for item in contradictions:
            lines.append(
                f"- {item['severity']} {item['id']}: expected={item['expected']} observed={item['observed']}"
            )
    else:
        lines.append("contradictions: none")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON facts")
    args = parser.parse_args(argv)
    facts = collect_facts()
    if args.json:
        print(json.dumps(facts, indent=2, sort_keys=True))
    else:
        print(render_text(facts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
