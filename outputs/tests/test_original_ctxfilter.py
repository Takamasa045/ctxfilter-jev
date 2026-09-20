from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ORIG = Path("/Users/takamasa/Documents/Codex/2026-09-20/codex-context-filter-grok46")
PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"


class OriginalCtxfilterTests(unittest.TestCase):
    def test_original_37_unchanged(self) -> None:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(ORIG / "outputs" / "src")
        proc = subprocess.run(
            [PYTHON, "-m", "unittest", "discover", "-s", str(ORIG / "outputs" / "tests"), "-v"],
            cwd=str(ORIG),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        self.assertIn("Ran 37 tests", proc.stderr)
        self.assertIn("OK", proc.stderr.splitlines()[-1])
