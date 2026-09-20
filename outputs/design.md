# Design

## Slice

1 OS (macOS) × existing Jev MCP × this workspace × relevance classification of bounded JSONL issue/log records against one goal.

Extraction and the Jev call happen inside this local Python process. Codex sees a compact evidence-linked result. Deterministic literal tasks skip Jev.

## Reuse

ctxfilter core is imported from `/Users/takamasa/Documents/Codex/2026-09-20/codex-context-filter-grok46/outputs/src`. This package does not copy or replace that core.

Jev is the inspected `jev-mcp@0.4.0` cache. Configured launcher remains `npx -y --ignore-scripts jev-mcp@0.4.0`. After that provenance check, the child argv is pinned to the sibling `node` plus `dist/index.js` so the bytes executed match the inspected cache. Auth stays in `~/.config/typesafe/key`; this process never reads the key.

## Bounds

Records, excerpts, state JSON, output JSON, scan bytes, runtime, Jev request/response bytes, and RPC ids are capped. Output space is reserved before Jev. If the budget cannot hold telemetry plus items, the call fails or defers with a cursor. Usage/call counts survive post-Jev errors, including `changed_during_read`.

## Cursors

`from_byte` must be 0, EOF, or immediately after a newline, with matching identity when `from_byte > 0`. Completion is derived from consumed records, not from a ctxfilter page hitting EOF while unread lines remain.

## Criteria

Options are the fixed set `relevant` / `irrelevant` / `needs_context`. Mandatory untrusted-evidence rules are immutable. Extra `question_rules` are length-limited and appended.

## Comparison

Semantic inputs have no labels. Ground truth is a sidecar. Full-direct counts a realistic envelope that contains the exact source; inspector simulation is separate and is not a Codex API. ctxfilter-only judges only preview/fetch returned text and counts those envelopes. Jev counts compact dumps plus JSON-RPC envelopes, including continuations. Mock and live files are separate. A proof is reused only if path hash, size, goal, and complete id coverage match.

Display excerpts may be shorter than the bounded text sent to Jev. That is not semantic truncation.

Installer recovery restores config only if it still equals the bytes just written. Cooperative flock is used; a non-cooperating writer can still race after the last preimage check.

## Out of scope

Global Codex registration apply, other providers, free-form Jev design questions, native Codex MCP until a reloaded session after an approved install.
