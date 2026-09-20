from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
WORK = ROOT.parent / "work"
sys.path.insert(0, str(SRC))

from ctxfilter_jev.extract import extract_records  # noqa: E402
from ctxfilter_jev.support import Deadline  # noqa: E402


def fixtures() -> dict[str, str]:
    sys.path.insert(0, str(WORK / "fixtures"))
    from build_fixtures import build

    return build()


class ExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fx = fixtures()

    def test_long_jsonl_spans_pages_without_duplicate(self) -> None:
        result = extract_records(
            self.fx["longline"],
            max_records=8,
            max_scan_bytes=1_048_576,
            deadline=Deadline(5000),
        )
        self.assertTrue(result["ok"], result)
        ids = [rec.id for rec in result["records"]]
        self.assertEqual(ids, ["long1", "after1"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(result["scan_complete"])
        long = result["records"][0]
        self.assertTrue(long.raw_ok)
        self.assertIn("AuthTimeout", long.text)
        self.assertIn("日本語", long.text)
        self.assertGreater(len(long.text), 2000)
        after = result["records"][1]
        self.assertGreater(after.byte_offset, long.end_byte)
        self.assertGreaterEqual(result["ctx_calls"], 2)
        rec0 = json.loads(Path(self.fx["longline"]).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(long.text, rec0["text"])
