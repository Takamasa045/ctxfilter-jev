# 完了レポート

## 最終段

review-final の 256 バイト上限とストリーム改行の数え方を直し、fingerprint の hash/candidate 断言を足した。新規 Jev 推論はしていない。ローカル 41 件 OK のあと、所有ブロックだけグローバル登録した。

## テスト

```
/opt/homebrew/opt/python@3.14/bin/python3.14 -m unittest discover -s /Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/tests -v
```

exit 0、**41 tests**、3.291s。元 ctxfilter **37** を含む。

## 比較の数え方

再計測は直列化だけ。推論は `proof.json`（履歴）と `proof-live-02.json`（比較）。usage は各回 **1797/302**。最新 API **604 ms**、経過 **708 ms**。

| 面 | envelope_body | file（+改行） | 呼び出し |
| --- | --- | --- | --- |
| 全文 | 1315 | 1316 | 1 |
| ctxfilter | 3419 | 3421 | 2 |
| ctxfilter+Jev | 2440（内容 2108） | 2441 | 1 |
| schema | 1972 | 1973 | 別 |

全文の wall は直列化 **約 0.05 ms**。Jev の 708 ms は保存済み推論であり、再計測の壁時計ではない。

## 登録

apply exit 0。before config sha `52d1cb62…`、after `7a434f54…`。Jev と ctxfilter は前後ともあり。rollback dry-run は所有ブロックだけ外し、Jev/ctxfilter は残る。rollback 後 sha は before と一致しない（挿入時の改行差）。本文は出していない。

`codex mcp get ctxfilter_jev`: enabled, stdio, 登録した command/args。これは設定読込。ネイティブツール観測ではない。

ローカル MCP スモーク: 成功。最初のスモークは notification の応答待ちでハングし kill。Jev ではない。修正後 exit 0。

## 未確認

Codex ネイティブのツール一覧に ctxfilter_jev が出るか。新セッションの再読込が必要。
