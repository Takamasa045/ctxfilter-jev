"""Run local tests and record exit codes. Does not apply global registration."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"
ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
ORIG = Path("/Users/takamasa/Documents/Codex/2026-09-20/codex-context-filter-grok46")


def run(name: str, argv: list[str], cwd: Path, env: dict[str, str]) -> dict:
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180)
    return {
        "name": name,
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "stdout_chars": len(proc.stdout),
        "stderr_chars": len(proc.stderr),
        "stdout_tail": proc.stdout[-1500:],
        "stderr_tail": proc.stderr[-2500:],
    }


def main() -> int:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    results = []
    results.append(
        run(
            "original_ctxfilter_37",
            [PYTHON, "-m", "unittest", "discover", "-s", str(ORIG / "outputs" / "tests"), "-v"],
            ORIG,
            {**env, "PYTHONPATH": str(ORIG / "outputs" / "src")},
        )
    )
    results.append(
        run(
            "ctxfilter_jev_tests",
            [PYTHON, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"],
            PROJECT,
            {**env, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ORIG / "outputs" / "src")},
        )
    )
    results.append(
        run(
            "comparison_mock",
            [PYTHON, str(ROOT / "comparison" / "run_comparison.py"), "--mock"],
            PROJECT,
            {**env, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ORIG / "outputs" / "src")},
        )
    )
    payload = {
        "ok": all(item["exit_code"] == 0 for item in results),
        "results": results,
    }
    out = Path(__file__).resolve().parent / "local-tests.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "wrote": str(out), "exit_codes": [item["exit_code"] for item in results]}))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
