# ctxfilter-jev

ctxfilterでローカルJSONLから必要範囲を取り出し、既存のJev MCPへ限定した抜粋を渡して、根拠付きの関連判定を返すMCPサーバーです。プライベートリポジトリとして、現在の実装と検証資料を保存しています。

## 現在の構成と依存

- 実装・CLI・テストは `outputs/src`、`outputs/bin`、`outputs/tests` にあります。
- 現在の稼働場所は `/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration` です。Git管理の追加で既存MCPの登録先は変更していません。
- 既存ctxfilterのコードを外部の `../codex-context-filter-grok46/outputs/src` から利用します。このリポジトリには同梱していません。
- Python 3.14、既存 `jev-mcp@0.4.0` のローカルキャッシュ、既存Codex MCP設定・Jev認証を利用します。実装にはこのMacの絶対パスが残っています。
- **別の場所へcloneするだけでは動きません。** 移設時は `outputs/src/ctxfilter_jev/constants.py`、登録スクリプト、テスト・検証スクリプトの固定パスと依存先を確認してください。登録スクリプトは既存設定を変更するため、移設先でそのまま適用しないでください。

## 使用と検証

詳しい操作・比較結果は [使用説明](outputs/README.md)、[完了報告](outputs/completion-report.md)、[独立レビュー](outputs/codex-review.md) を参照してください。これらの実測・登録状態は2026-09-20の元のMacでの記録です。Codexネイティブツール呼び出しは当該記録では未確認です。

既存依存が揃った元の配置でのローカル検査:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.14/bin/python3.14 -m unittest discover -s outputs/tests -v
```

前回の連携スイートは41件成功し、内部で既存ctxfilterの37件も再実行しています。このGit保存作業では実装を変更していません。

テストは `work/fixtures/build_fixtures.py` から自作の非機密fixtureを生成します。この生成処理はローカルの `outputs/allowlist.json` をfixture用に書き換えるため、独自の許可リストを使っている場合は事前に退避してください。テストはモックを利用し、実Jev推論の追加実行は不要です。

## 外部送信と保存対象

通常の文字列検索はctxfilterだけを利用します。実Jev判定は明示的に許可されたファイル・ハッシュのみ対象で、goalや追加ルールの送信許可は別に必要です。曖昧・不完全・変更済み・失敗の証拠は保留します。決定論モードは `literal_label` の抽出であり、意味判定ではありません。

認証情報、Codex設定のバックアップ、Grok実行ログ、ローカルの許可リストはGit管理から除外しています。保存した実測証拠は自作fixtureのものです。登録解除の手順は [rollback](outputs/registration/rollback.md) を参照してください。
