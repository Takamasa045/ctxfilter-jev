"""Smallest real vertical slice: synthetic file -> ctxfilter -> child Jev ask.

Does not print source bodies, Jev request payloads, key material, or stderr bodies.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from ctxfilter_jev.classify import classify_records, dumps_classify  # noqa: E402
from ctxfilter.core import Limits, preview_file, search_file  # noqa: E402
from ctxfilter_jev.constants import DEFAULT_GOAL  # noqa: E402
from ctxfilter_jev.provenance import confirm_jev  # noqa: E402
from ctxfilter_jev.support import dumps, encoded_size  # noqa: E402


def main() -> int:
    sys.path.insert(0, str(PROJECT / "work" / "fixtures"))
    from build_fixtures import build

    fx = build()
    started = time.monotonic()
    provenance = confirm_jev()
    preview = preview_file(fx["jsonl"], Limits(max_chars=400, max_lines=12))
    search = search_file(fx["jsonl"], "AuthTimeout", Limits(max_matches=4, chunk_size=1024))
    result = classify_records(
        fx["jsonl"],
        goal=DEFAULT_GOAL,
        allowlist_path=fx["allowlist"],
        max_records=6,
    )
    payload = dumps_classify(result, 16384)
    parsed = json.loads(payload)
    safe = {
        "ok": bool(parsed.get("ok")),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "invocation": {
            "argv": provenance["argv"],
            "configured_argv": provenance.get("configured_argv"),
            "pin": provenance.get("pin"),
            "package": f"{provenance['package_name']}@{provenance['package_version']}",
            "package_path": provenance["package_path"],
            "dist_sha256": provenance.get("dist_sha256"),
            "key_file_present": provenance["key_file_present"],
            "key_file_mode": provenance["key_file_mode"],
        },
        "ctxfilter": {
            "preview_ok": preview.get("ok"),
            "preview_truncated": preview.get("truncated"),
            "preview_returned_chars": preview.get("returned_chars"),
            "search_ok": search.get("ok"),
            "search_matches": search.get("matches_returned"),
            "identity_sample_sha256": (preview.get("identity") or {}).get("sample_sha256"),
        },
        "classify_result": parsed,
        "response_bytes": encoded_size(payload),
        "response_chars": len(payload),
        "note": (
            "Jev request state and stderr are not stored. Character/byte counts are not tokens. "
            "This is a local subprocess MCP call, not Codex-native MCP."
        ),
    }
    out = Path(__file__).resolve().parent / "proof-live-02.json"
    historical = Path(__file__).resolve().parent / "proof.json"
    safe["historical_proof_preserved"] = historical.is_file()
    safe["out"] = str(out)
    out.write_text(json.dumps(safe, indent=2) + "\n", encoding="utf-8")
    print(dumps({"ok": safe["ok"], "wrote": str(out), "jev_called": parsed.get("jev", {}).get("called"), "model": parsed.get("jev", {}).get("model"), "status": parsed.get("status")}))
    return 0 if parsed.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
