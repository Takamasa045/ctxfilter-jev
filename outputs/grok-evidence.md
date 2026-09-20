# Evidence

Session: `01a0bc78-7bb2-7051-8344-8875febfed4d`

Model: grok-4.6
First-end metadata: grok-4.6-build

## 実 Jev（新規なし。2 回とも保存済み）

Call 1 `outputs/verification/proof.json`: usage 1797/302, latency 585 ms, 応答 3748 B, ファイル当時 998 B。明確 5 件一致、r3 review。

Call 2 `outputs/verification/proof-live-02.json`: usage 1797/302, latency 604 ms, 経過 708 ms, コンパクト 2108 B / 封筒 2440 B, ファイル 825 B。明確 5 件一致、r3 review/deferred。

最終段の比較は call 2 の直列化再計測。`inference_rerun: false`。

## 直列化（Codex ネイティブ回線ではない）

`outputs/comparison/results-live.json` と `streams-live/`。本文とファイル（+1 改行/本）を分けて再計算済み。

- 全文 1315 B 本文 / 1316 B ファイル、wall 約 0.05 ms
- ctxfilter 3419 / 3421、2 呼び出し、wall 約 0.42 ms
- Jev 2440 / 2441、内容 2108
- schema 1972 / 1973

`comparison/results.json` は superseded（`SUPERSEDED.md`）。

## テスト

unittest discover: exit **0**, ran **41**, 3.291s, original 37 included.

## 登録

apply: exit 0。backup:

- `work/backup/20260920T032258313437Z-config.toml`
- `work/backup/20260920T032258314093Z-AGENTS.md`

Jev/ctxfilter テーブルは apply 前後とも存在。rollback dry-run のみ。`codex mcp get ctxfilter_jev` enabled stdio。ローカル MCP スモーク ok、jev_called false。

ネイティブ Codex ツール: **未確認**。
