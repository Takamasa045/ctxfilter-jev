# ctxfilter-jev

既存 ctxfilter で抜粋し、既存 Jev MCP を子プロセスで呼ぶ、許可リスト付きの関連判定です。元の ctxfilter は変更していません。

対象は macOS × 既存 Jev × この作業ディレクトリ × JSONL 1ファイルの関連分類です。

## 使い方

```bash
/opt/homebrew/opt/python@3.14/bin/python3.14 /Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/bin/ctxfilter-jev classify --path /Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/work/fixtures/synthetic-literal.jsonl --deterministic
/opt/homebrew/opt/python@3.14/bin/python3.14 /Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/bin/ctxfilter-jev fingerprint --path FILE
/opt/homebrew/opt/python@3.14/bin/python3.14 /Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/bin/ctxfilter-jev mcp
```

`--deterministic` は `literal_label` 抽出だけで Jev を呼びません。ファイル allowlist は goal/criteria の機密許可ではありません。`max_output_bytes` はツール本文 JSON の上限です。MCP JSON-RPC 封筒は別に数えます。256 バイト指定でも本文はその上限に収まり、Jev を呼んだかと usage は残します。

## 比較（ラベルなし 6 行、825 B）

文字/バイトはトークンでも枠でもありません。制御されたローカル直列化の再計測であり、Codex ネイティブ回線のキャプチャではありません。Jev 推論は保存済み 2 回を再利用し、この最終段では新規推論していません。

NDJSON ファイルは本文 + 末尾改行 1 バイトです。`envelope_body_bytes` は改行なし、`file_bytes` は改行込みです。

| 面 | 本文封筒 | ファイル | 呼び出し | 時間 |
| --- | --- | --- | --- | --- |
| 全文 read | 1315 B | 1316 B | 1 | 直列化 約 0.05 ms |
| ctxfilter preview+fetch | 3419 B | 3421 B | 2 | 直列化 0.42 ms |
| ctxfilter+Jev | 2440 B（内容 2108 B） | 2441 B | Codex 1 / 内部 Jev 1 | 推論 604 ms API / 708 ms 経過（保存済み） |
| tools/list | 1972 B | 1973 B | 別計上 | 直列化 |

この小さな fixture では Jev 封筒 2440 B が全文 1315 B より大きい。節約とは言わない。

Jev 実測は 2 回とも usage **1797/302**、モデル `jev-1.13.0`。最新は latency **604 ms**、経過 **708 ms**。明確 5 件一致、r3 は review で deferred。

古い `comparison/results.json` は方法が違うため superseded。正本は `results-live.json`。

## 登録

`~/.codex/config.toml` と `~/.codex/AGENTS.md` に所有ブロックを適用済み。Jev / ctxfilter / 他 MCP は残しています。`codex mcp get ctxfilter_jev` は設定読込です。ローカル stdio スモークは initialize/list/ping/deterministic。**Codex ネイティブツール一覧での出現は未確認**（mcp get や子プロセススモークと等価ではありません）。

戻しは dry-run のみ確認。backup は `work/backup/20260920T032258313437Z-config.toml` と `…314093Z-AGENTS.md`。
