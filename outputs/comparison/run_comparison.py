"""Compare full-read, ctxfilter-only, and ctxfilter+Jev serialized streams.

Equal scoped evidence. No hidden full-file oracle for ctxfilter-only.
Ground truth stays in a sidecar and is not copied into counted streams.
Mock and live results use separate files. Stale proofs are not reused.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from ctxfilter_jev.classify import classify_records, dumps_classify  # noqa: E402
from ctxfilter.core import Limits, dumps_result, fetch_excerpt, parse_identity, preview_file  # noqa: E402
from ctxfilter_jev.mcp_server import _tool_schemas  # noqa: E402
from ctxfilter_jev.support import dumps, encoded_size  # noqa: E402

WORK = PROJECT / "work"
OUT = ROOT / "comparison"
HISTORICAL_PROOF = ROOT / "verification" / "proof.json"


def _fixtures() -> dict[str, str]:
    sys.path.insert(0, str(WORK / "fixtures"))
    from build_fixtures import build

    return build()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_contract(path: Path, goal: str) -> dict:
    text = path.read_text(encoding="utf-8")
    ids = []
    for line in text.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        ids.append(obj["id"])
        if "literal_label" in obj:
            raise RuntimeError("semantic comparison input must not embed ground-truth labels")
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
        "goal": goal,
        "record_ids": ids,
        "n_records": len(ids),
    }


def tool_envelope(msg_id: int, payload_text: str) -> str:
    ok = True
    try:
        parsed = json.loads(payload_text)
        if isinstance(parsed, dict) and parsed.get("ok") is False:
            ok = False
    except json.JSONDecodeError:
        ok = False
    return dumps(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": payload_text}], "isError": not ok},
        }
    )


def list_envelope() -> str:
    return dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": _tool_schemas()}})


def heuristic(text: str) -> str:
    lowered = text.lower()
    has_auth = any(token in lowered for token in ("auth", "login", "idp"))
    has_timeout = "timeout" in lowered
    if has_auth and has_timeout:
        return "relevant"
    if has_timeout and not has_auth:
        return "needs_context"
    if not has_auth and not has_timeout:
        return "irrelevant"
    return "needs_context"


def parse_records_from_text(text: str) -> list[dict]:
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("id"), str) and isinstance(obj.get("text"), str):
            records.append({"id": obj["id"], "text": obj["text"]})
    return records


def view_full(path: Path) -> dict:
    started = time.monotonic()
    source = path.read_text(encoding="utf-8")
    payload_text = dumps(
        {
            "ok": True,
            "tool": "read_file",
            "path": str(path),
            "content": source,
        }
    )
    envelope = tool_envelope(10, payload_text)
    inspector = {}
    for rec in parse_records_from_text(source):
        inspector[rec["id"]] = heuristic(rec["text"])
    wall_ms = (time.monotonic() - started) * 1000.0
    return {
        "streams": [envelope],
        "content_json": payload_text,
        "counts": {
            "content_bytes": encoded_size(payload_text),
            "envelope_body_bytes": encoded_size(envelope),
            "codex_facing_calls": 1,
        },
        "inspector_simulation": {
            "note": "Heuristic over the counted full-file content. Not a Codex API inference.",
            "classifications": inspector,
        },
        "wall_ms": wall_ms,
        "measurement_kind": "controlled_local_serialization",
    }


def view_ctxfilter(path: Path) -> dict:
    started = time.monotonic()
    streams = []
    msg_id = 20
    limits = Limits(max_chars=400, max_lines=12, max_output_bytes=16384)
    preview = preview_file(str(path), limits)
    preview_text = dumps_result(preview, 16384)
    streams.append(tool_envelope(msg_id, preview_text))
    msg_id += 1
    pieces = [preview.get("excerpt") or ""]
    ident = parse_identity(preview["identity"]) if preview.get("ok") else None
    cursor = int(preview.get("next_byte") or 0)
    complete = bool(preview.get("scan_complete")) and not bool(preview.get("truncated"))
    fetch_count = 0
    while ident is not None and not complete:
        fetched = fetch_excerpt(str(path), ident, byte_offset=cursor, limits=Limits(max_chars=2000, max_lines=200))
        fetch_text = dumps_result(fetched, 16384)
        streams.append(tool_envelope(msg_id, fetch_text))
        msg_id += 1
        fetch_count += 1
        if not fetched.get("ok"):
            break
        pieces.append(fetched.get("excerpt") or "")
        cursor = int(fetched.get("next_byte") or fetched.get("end_byte") or cursor)
        complete = bool(fetched.get("scan_complete")) and not bool(fetched.get("truncated"))
        if fetch_count > 64:
            break
    returned = "".join(pieces)
    records = parse_records_from_text(returned)
    inspector = {rec["id"]: heuristic(rec["text"]) for rec in records}
    return {
        "streams": streams,
        "counts": {
            "envelope_body_bytes": sum(encoded_size(item) for item in streams),
            "codex_facing_calls": len(streams),
            "preview_calls": 1,
            "fetch_calls": fetch_count,
        },
        "measurement_kind": "controlled_local_serialization",
        "returned_chars": len(returned),
        "inspector_simulation": {
            "note": "Heuristic only on excerpts actually returned by preview/fetch. Not a hidden full-file oracle. Not a Codex API inference.",
            "classifications": inspector,
            "record_ids_from_stream": [rec["id"] for rec in records],
        },
        "wall_ms": (time.monotonic() - started) * 1000.0,
        "measurement_kind": "controlled_local_serialization",
    }


def proof_matches(proof: dict, contract: dict) -> bool:
    ident = (proof.get("classify_result") or {}).get("identity") or {}
    if ident.get("sha256") != contract["sha256"]:
        return False
    if int(ident.get("size") or -1) != contract["size"] and str(ident.get("size")) != str(contract["size"]):
        return False
    result = proof.get("classify_result") or {}
    if result.get("goal") != contract["goal"]:
        return False
    ids = [item.get("id") for item in result.get("items") or []]
    if ids != contract["record_ids"]:
        return False
    if not result.get("scan_complete"):
        return False
    return True


def view_jev(path: Path, allowlist: str, contract: dict, mock: bool, reuse_path: Path | None = None) -> dict:
    started = time.monotonic()
    attempts = []
    streams = []
    results = []
    source = "fresh_call"
    from_byte = 0
    identity = None
    call_index = 0
    historical_ok = False
    if HISTORICAL_PROOF.is_file():
        historical = json.loads(HISTORICAL_PROOF.read_text(encoding="utf-8"))
        historical_ok = proof_matches(historical, contract)
        attempts.append({"source": "verification/proof.json", "matched_contract": historical_ok, "reused": False, "superseded": True})
    if reuse_path and reuse_path.is_file() and not mock:
        captured = json.loads(reuse_path.read_text(encoding="utf-8"))
        result = captured.get("classify_result") or {}
        if not proof_matches(captured, contract):
            raise RuntimeError("captured live proof does not match current evidence contract")
        results = [result]
        payload = dumps_classify(result, int((result.get("limits") or {}).get("max_output_bytes") or 16384))
        streams.append(tool_envelope(31, payload))
        source = "reused_captured_inference_serialization_only"
        attempts.append(
            {
                "source": str(reuse_path),
                "ok": bool(result.get("ok")),
                "jev_called": (result.get("jev") or {}).get("called"),
                "elapsed_ms": captured.get("elapsed_ms") or result.get("elapsed_ms"),
                "inference_rerun": False,
            }
        )
    else:
        if not mock:
            raise RuntimeError("live Jev inference is disabled; reuse captured proof-live-02.json")
        kwargs = {"allowlist_path": allowlist, "goal": contract["goal"], "test_transport": str(ROOT / "tests" / "mock_jev.py")}
        while True:
            call_index += 1
            result = classify_records(str(path), from_byte=from_byte, identity=identity, **kwargs)
            results.append(result)
            payload = dumps_classify(result, int((result.get("limits") or {}).get("max_output_bytes") or 16384))
            streams.append(tool_envelope(30 + call_index, payload))
            attempts.append(
                {
                    "source": source,
                    "ok": bool(result.get("ok")),
                    "jev_called": (result.get("jev") or {}).get("called"),
                    "elapsed_ms": result.get("elapsed_ms"),
                    "from_byte": from_byte,
                    "mock": True,
                }
            )
            if result.get("scan_complete") or not result.get("further_fetch_needed"):
                break
            cursor = result.get("cursor") or {}
            from_byte = int(cursor.get("next_byte") or result.get("next_byte") or 0)
            identity = cursor.get("identity") or result.get("identity")
            if call_index > 16:
                break
    compact = json.loads(dumps_classify(results[0], 16384)) if results else {}
    labels = {}
    for result in results:
        for item in result.get("items") or []:
            labels[item["id"]] = item["decision"]
    elapsed = sum(int(result.get("elapsed_ms") or 0) for result in results)
    usage = None
    model = None
    latency = 0
    jev_calls = 0
    ctx_calls = 0
    for result in results:
        jev = result.get("jev") or {}
        if jev.get("usage"):
            if usage is None:
                usage = {"input_tokens": 0, "output_tokens": 0}
            usage["input_tokens"] += int(jev["usage"].get("input_tokens") or 0)
            usage["output_tokens"] += int(jev["usage"].get("output_tokens") or 0)
        if jev.get("model"):
            model = jev.get("model")
        latency += int(jev.get("latency_ms") or 0)
        jev_calls += int((result.get("call_counts") or {}).get("jev_internal") or 0)
        ctx_calls += int((result.get("call_counts") or {}).get("ctxfilter_internal") or 0)
    return {
        "streams": streams,
        "compact_payload": compact,
        "full_internal_results": results,
        "source": source,
        "historical_proof_reused": False,
        "historical_proof_matched": historical_ok,
        "attempts": attempts,
        "counts": {
            "content_bytes": sum(encoded_size(dumps_classify(result, 16384)) for result in results),
            "envelope_body_bytes": sum(encoded_size(item) for item in streams),
            "codex_facing_calls": len(streams),
        },
        "measurement_kind": "controlled_local_serialization_plus_reused_inference",
        "internal": {
            "jev_internal_calls": jev_calls,
            "ctxfilter_internal_calls": ctx_calls,
            "jev_usage": usage,
            "jev_model": model,
            "jev_latency_ms": latency,
            "classify_elapsed_ms": elapsed,
            "jev_mock": mock,
        },
        "classifications": labels,
        "wall_ms": (time.monotonic() - started) * 1000.0,
        "elapsed_ms": elapsed,
    }


def write_streams(folder: Path, streams: list[str]) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    body_bytes = 0
    file_bytes = 0
    for idx, body in enumerate(streams):
        name = folder / f"{idx:02d}.ndjson"
        encoded = body.encode("utf-8")
        name.write_bytes(encoded + b"\n")
        names.append(str(name))
        body_bytes += len(encoded)
        file_bytes += name.stat().st_size
    return {
        "files": names,
        "envelope_body_bytes": body_bytes,
        "file_bytes": file_bytes,
        "framing_bytes": file_bytes - body_bytes,
        "note": "Each stream file is envelope body plus one trailing newline. Totals from files include framing_bytes.",
    }


def recount_files(paths: list[str]) -> dict:
    body_bytes = 0
    file_bytes = 0
    for path in paths:
        raw = Path(path).read_bytes()
        file_bytes += len(raw)
        body = raw[:-1] if raw.endswith(b"\n") else raw
        body_bytes += len(body)
    return {
        "envelope_body_bytes": body_bytes,
        "file_bytes": file_bytes,
        "framing_bytes": file_bytes - body_bytes,
        "recomputed_from_files": True,
    }


def main() -> int:
    fx = _fixtures()
    path = Path(fx["jsonl"])
    truth = json.loads(Path(fx["truth"]).read_text(encoding="utf-8"))["labels"]
    goal = fx["goal"]
    contract = evidence_contract(path, goal)
    mock = "--mock" in sys.argv
    captured = ROOT / "verification" / "proof-live-02.json"
    reuse = (not mock) and captured.is_file()
    schema = list_envelope()
    full = view_full(path)
    ctx = view_ctxfilter(path)
    jev = view_jev(path, fx["allowlist"], contract, mock=mock, reuse_path=captured if reuse else None)
    stream_dir = OUT / ("streams-mock" if mock else "streams-live")
    saved = {
        "schema": write_streams(stream_dir / "schema", [schema]),
        "full_direct": write_streams(stream_dir / "full_direct", full["streams"]),
        "ctxfilter": write_streams(stream_dir / "ctxfilter", ctx["streams"]),
        "ctxfilter_jev": write_streams(stream_dir / "ctxfilter_jev", jev["streams"]),
    }
    for key, info in saved.items():
        saved[key] = {**info, "recount": recount_files(info["files"])}

    def mismatch(view_labels: dict) -> dict:
        return {key: {"truth": truth[key], "view": view_labels.get(key)} for key in truth if truth[key] != view_labels.get(key)}

    missing_ctx = [rid for rid in contract["record_ids"] if rid not in ctx["inspector_simulation"]["classifications"]]
    report = {
        "goal": goal,
        "fixture": str(path),
        "contract": contract,
        "ground_truth_file": fx["truth"],
        "ground_truth_note": "Sidecar only. Not embedded in inputs or counted streams. Not a Codex API experiment.",
        "schema_overhead": {
            "bytes": encoded_size(schema),
            "chars": len(schema),
            "note": "Actual tools/list JSON-RPC envelope.",
        },
        "views": {
            "full_direct_read": {
                "counts": full["counts"],
                "inspector_simulation": full["inspector_simulation"],
                "mismatches_vs_truth": mismatch(full["inspector_simulation"]["classifications"]),
                "wall_ms": full["wall_ms"],
                "measurement_kind": full["measurement_kind"],
            },
            "ctxfilter_only": {
                "counts": ctx["counts"],
                "inspector_simulation": ctx["inspector_simulation"],
                "missing_from_returned_evidence": missing_ctx,
                "mismatches_vs_truth": mismatch(ctx["inspector_simulation"]["classifications"]),
                "wall_ms": ctx["wall_ms"],
            },
            "ctxfilter_jev": {
                "counts": jev["counts"],
                "internal": jev["internal"],
                "attempts": jev["attempts"],
                "classifications": jev["classifications"],
                "mismatches_vs_truth": mismatch(jev["classifications"]),
                "elapsed_ms": jev["elapsed_ms"],
                "wall_ms": jev["wall_ms"],
                "source": jev["source"],
                "historical_proof_reused": jev["historical_proof_reused"],
                "compact_payload": jev["compact_payload"],
            },
        },
        "stream_files": saved,
        "byte_proxy_is_not_tokens_or_quota": True,
        "codex_native_mcp_verified": False,
        "totals_recomputable_from_stream_files": True,
        "framing_note": "NDJSON files store envelope body plus one trailing newline. envelope_body_bytes excludes that delimiter; file_bytes includes it.",
        "measurement_kind": "controlled_local_serialization; Jev inference reused from captured proof, not rerun",
        "inference_reused": reuse,
        "superseded_labeled_only": ["outputs/comparison/results.json"] if (OUT / "results.json").is_file() else [],
    }
    out_name = OUT / ("results-mock.json" if mock else "results-live.json")
    OUT.mkdir(parents=True, exist_ok=True)
    out_name.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(dumps({"ok": True, "wrote": str(out_name), "mock": mock, "jev_source": jev["source"], "inference_reused": reuse, "historical_reused": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
