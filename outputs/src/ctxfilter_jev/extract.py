"""Extract JSONL records through existing ctxfilter preview/fetch only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ctxfilter.core import (
    Limits as CtxLimits,
    ToolError,
    fetch_excerpt,
    open_regular,
    parse_identity,
    preview_file,
    resolve_regular_file,
)

from ctxfilter_jev.constants import (
    CTXFILTER_RUNTIME_MS,
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_LINES,
    LITERAL_FIELD,
    MAX_JSONL_LINE_CHARS,
    MAX_RECORD_ID_CHARS,
)
from ctxfilter_jev.support import ClassifyError, Deadline, is_strict_int


@dataclass(frozen=True)
class Record:
    id: str
    byte_offset: int
    end_byte: int
    text: str
    literal_label: str | None
    raw_ok: bool


def _page_limits(max_scan_bytes: int) -> CtxLimits:
    return CtxLimits(
        max_chars=DEFAULT_MAX_CHARS,
        max_lines=DEFAULT_MAX_LINES,
        max_scan_bytes=max_scan_bytes,
        max_runtime_ms=CTXFILTER_RUNTIME_MS,
        max_output_bytes=16384,
    ).clamp()


def valid_record_id(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > MAX_RECORD_ID_CHARS:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in value)


def _parse_line(line: str, byte_offset: int) -> Record:
    end_byte = byte_offset + len(line.encode("utf-8"))
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return Record("invalid", byte_offset, end_byte, "", None, False)
    if not isinstance(obj, dict):
        return Record("invalid", byte_offset, end_byte, "", None, False)
    rec_id = obj.get("id")
    text = obj.get("text")
    if not valid_record_id(rec_id) or not isinstance(text, str):
        return Record("invalid", byte_offset, end_byte, "", None, False)
    literal = obj.get(LITERAL_FIELD)
    if literal is not None and not isinstance(literal, str):
        literal = None
    return Record(str(rec_id), byte_offset, end_byte, text, literal, True)


def _prev_byte(path: str, offset: int) -> bytes:
    resolved = resolve_regular_file(path)
    handle, st = open_regular(resolved)
    try:
        if offset <= 0 or offset > st.st_size:
            return b""
        handle.seek(offset - 1)
        return handle.read(1)
    finally:
        handle.close()


def assert_record_cursor(path: str, from_byte: int, identity: Any, size: int) -> None:
    if not is_strict_int(from_byte) or from_byte < 0:
        raise ClassifyError("invalid_cursor", "from_byte is not a record boundary")
    if from_byte == 0:
        return
    if identity is None:
        raise ClassifyError("invalid_cursor", "continuation requires identity")
    if from_byte > size:
        raise ClassifyError("invalid_cursor", "from_byte is not a record boundary")
    if from_byte == size:
        return
    prev = _prev_byte(path, from_byte)
    if prev != b"\n":
        raise ClassifyError("invalid_cursor", "from_byte is not a record boundary")


def excerpt_of(record: Record, max_chars: int = DEFAULT_EXCERPT_CHARS) -> tuple[str, bool]:
    text = record.text
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def extract_records(
    path: str,
    *,
    from_byte: int = 0,
    identity: Any = None,
    max_records: int,
    max_scan_bytes: int,
    deadline: Deadline,
    file_size: int | None = None,
) -> dict[str, Any]:
    limits = _page_limits(max_scan_bytes)
    try:
        if file_size is None:
            resolved = resolve_regular_file(path)
            handle, st = open_regular(resolved)
            handle.close()
            file_size = int(st.st_size)
        assert_record_cursor(path, from_byte, identity, file_size)
        return _extract_loop(
            path,
            from_byte=from_byte,
            identity=identity,
            max_records=max_records,
            max_scan_bytes=max_scan_bytes,
            deadline=deadline,
            limits=limits,
        )
    except ToolError as exc:
        raise ClassifyError(exc.code, exc.message) from exc


def _extract_loop(
    path: str,
    *,
    from_byte: int,
    identity: Any,
    max_records: int,
    max_scan_bytes: int,
    deadline: Deadline,
    limits: CtxLimits,
) -> dict[str, Any]:
    records: list[Record] = []
    leftover = ""
    leftover_start: int | None = None
    cursor = from_byte
    ident = identity
    ctx_calls = 0
    bytes_scanned = 0
    last_result: dict[str, Any] | None = None
    file_eof = False
    unconsumed_at: int | None = None
    seen_ids: set[str] = set()

    while len(records) < max_records and not file_eof:
        if deadline.expired():
            break
        ctx_calls += 1
        if ident is None and cursor == 0:
            result = preview_file(path, limits)
        else:
            if ident is None:
                raise ClassifyError("invalid_cursor", "continuation requires identity")
            result = fetch_excerpt(path, ident, byte_offset=cursor, limits=limits)
        last_result = result
        if not result.get("ok"):
            return {"ok": False, "result": result, "ctx_calls": ctx_calls}
        ident = parse_identity(result["identity"])
        kind = result.get("kind")
        if kind == "binary":
            raise ClassifyError("binary", "binary files are not forwarded")
        if kind == "empty" and cursor == 0 and not leftover:
            file_eof = True
            break
        if result.get("decode_errors"):
            raise ClassifyError("invalid_utf8", "invalid UTF-8 is not forwarded")
        excerpt = result.get("excerpt") or ""
        start_byte = int(result.get("start_byte") or 0)
        page_eof = bool(result.get("scan_complete")) and not bool(result.get("truncated"))
        if leftover:
            text = leftover + excerpt
            text_start = int(leftover_start if leftover_start is not None else start_byte)
        else:
            text = excerpt
            text_start = start_byte
        bytes_scanned += len(excerpt.encode("utf-8"))
        offset = 0
        while True:
            nl = text.find("\n", offset)
            if nl < 0:
                leftover = text[offset:]
                leftover_start = text_start + len(text[:offset].encode("utf-8"))
                break
            line = text[offset:nl]
            line_start = text_start + len(text[:offset].encode("utf-8"))
            next_offset = nl + 1
            if line.strip():
                if len(records) >= max_records:
                    unconsumed_at = line_start
                    leftover = text[offset:]
                    leftover_start = line_start
                    break
                rec = _parse_line(line, line_start)
                if rec.raw_ok and rec.id in seen_ids:
                    rec = Record(rec.id, rec.byte_offset, rec.end_byte, rec.text, rec.literal_label, False)
                records.append(rec)
                if rec.raw_ok:
                    seen_ids.add(rec.id)
            offset = next_offset
            leftover = text[offset:]
            leftover_start = text_start + len(text[:offset].encode("utf-8"))
            if unconsumed_at is not None:
                break
        cursor = int(result.get("next_byte") or result.get("end_byte") or cursor)
        if unconsumed_at is not None:
            file_eof = False
            break
        if page_eof:
            file_eof = True
            if leftover.strip():
                line_start = int(leftover_start if leftover_start is not None else cursor)
                if len(records) >= max_records:
                    unconsumed_at = line_start
                    file_eof = False
                else:
                    rec = _parse_line(leftover, line_start)
                    if rec.raw_ok and rec.id in seen_ids:
                        rec = Record(rec.id, rec.byte_offset, rec.end_byte, rec.text, rec.literal_label, False)
                    records.append(rec)
                    if rec.raw_ok:
                        seen_ids.add(rec.id)
                    leftover = ""
                    leftover_start = None
            else:
                leftover = ""
                leftover_start = None
            break
        if leftover and len(leftover) > MAX_JSONL_LINE_CHARS:
            line_start = int(leftover_start if leftover_start is not None else cursor)
            rec = Record("invalid", line_start, line_start + len(leftover.encode("utf-8")), leftover[:MAX_JSONL_LINE_CHARS], None, False)
            records.append(rec)
            leftover = ""
            leftover_start = None
            unconsumed_at = cursor
            break
        if bytes_scanned >= max_scan_bytes:
            break
        if not excerpt:
            break
        # Keep leftover in memory and continue from this page's next_byte.
        # Do not rewind cursor to leftover_start (that duplicates the prefix).

    consumed_all = file_eof and not leftover.strip() and unconsumed_at is None
    if unconsumed_at is not None:
        next_byte = unconsumed_at
    elif leftover_start is not None and leftover.strip():
        next_byte = leftover_start
        consumed_all = False
    elif records and not consumed_all:
        next_byte = records[-1].end_byte + 1
    else:
        next_byte = cursor if not consumed_all else (records[-1].end_byte + 1 if records else from_byte)
        if consumed_all and last_result:
            next_byte = int(last_result.get("end_byte") or last_result.get("next_byte") or next_byte)
    return {
        "ok": True,
        "records": records,
        "identity": ident,
        "scan_complete": bool(consumed_all),
        "further_fetch_needed": not consumed_all,
        "next_byte": next_byte,
        "ctx_calls": ctx_calls,
        "bytes_scanned": bytes_scanned,
        "last_result": last_result,
    }
