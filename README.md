# ctxfilter-jev

**JSONLの各記録が「調べたいことに関係するか」を、Jevで判定するツールです。** [ctxfilter](https://github.com/Takamasa045/ctxfilter)でローカルファイルから必要な範囲を取り出し、限定した抜粋を既存のJev MCPへ渡します。CLIとMCPサーバーとして使えます。

Opt-in relevance classification for local JSONL records, using bounded ctxfilter excerpts and an existing Jev MCP.

## どんなときに使う？

たとえば「ログイン時のタイムアウトの原因を調べたい」という目的に対して、ログの各記録が関連するかを判定します。JSONLは、1行に1つのJSONを記録するテキスト形式です。

- **ctxfilter**：ファイルを短く読む、決まった文字列を探す。
- **ctxfilter-jev**：取り出した記録の意味を見て、目的との関連を判定する。

結果には関連判定と根拠となる抜粋などが含まれます。文脈不足・低信頼・読み取り失敗などは保留し、人や呼び出し元のエージェントに確認を戻します。情報が足りないことを「無関係」とは扱いません。

## 導入前に知っておくこと

このリポジトリは、**macOS上の特定環境で動作確認した実装と検証資料**を公開しています。汎用インストーラー付きの配布パッケージではなく、**cloneするだけでは動きません**。

実Jev判定では、抜粋・判定目的（`goal`）・追加ルール（`criteria`）が外部サービスへ送られます。通常の読み取り・文字列検索だけならctxfilterを使ってください。

## 現在の構成と依存

- 実装・CLI・テストは `outputs/src`、`outputs/bin`、`outputs/tests` にあります。
- 開発時の配置は `/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration` です。
- 既存ctxfilterのコードを外部の `../codex-context-filter-grok46/outputs/src` から利用します。このリポジトリには同梱していません。
- Python 3.14、既存 `jev-mcp@0.4.0` のローカルキャッシュ、既存Codex MCP設定・Jev認証を利用します。実装には開発環境の絶対パスが残っています。

別の環境で試すときは、次の順で準備してください。

1. このリポジトリとctxfilterを取得する。
2. [constants.py](outputs/src/ctxfilter_jev/constants.py)のプロジェクト・ctxfilter・Python・Jevキャッシュのパスを合わせる。登録・テスト・検証スクリプト内の固定パスも確認する。
3. 既存Jev MCPの設定と認証を確認し、外部送信してよい非機密データ・目的・ルールを決める。
4. 許可リストを準備してから、CLIまたはMCPで実行する。

登録スクリプトは既存のCodex設定を変更します。移設先でそのまま適用せず、[登録案](outputs/registration/config-diff-proposal.toml)と[解除手順](outputs/registration/rollback.md)を確認してください。

## CLIの入口

上記の依存先・パスを設定した環境で、リポジトリのルートから実行します。

```bash
# 利用できるコマンドを確認する
python3 outputs/bin/ctxfilter-jev --help

# ファイルのパス・ハッシュと、許可リスト登録用の候補を確認する
python3 outputs/bin/ctxfilter-jev fingerprint --path /path/to/records.jsonl

# MCPサーバーを起動する（標準入出力で要求を待ちます）
python3 outputs/bin/ctxfilter-jev mcp
```

`fingerprint` は送信許可を付与しません。対象データの送信許可を確認したうえで、`allowlist-add` で許可リストへ登録します。許可リストは、承認したファイルのパスとハッシュを照合する仕組みです。

登録済みの非機密ファイルを実Jevで判定する例（**外部送信が発生します**）：

```bash
python3 outputs/bin/ctxfilter-jev classify \
  --path /path/to/records.jsonl \
  --allowlist /path/to/allowlist.json \
  --goal "ログイン時のタイムアウト原因の調査に関係する記録を探す"
```

`--deterministic` を付けた場合はJevを呼ばず、既存の `literal_label` を読み取ります。これは接続前の動作確認などに使うモードで、意味に基づく分類ではありません。

## 使用と検証

詳しい操作・比較結果は [使用説明](outputs/README.md)、[完了報告](outputs/completion-report.md)、[独立レビュー](outputs/codex-review.md) を参照してください。これらの実測・登録状態は2026-09-20の元のMacでの記録です。Codexネイティブツール呼び出しは当該記録では未確認です。

既存依存が揃った元の配置でのローカル検査:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.14/bin/python3.14 -m unittest discover -s outputs/tests -v
```

保存済みの連携スイート記録は41件成功し、内部で当時のctxfilterの37件も再実行しています。これは開発時の結果であり、移設先での動作を保証するものではありません。

テストは `work/fixtures/build_fixtures.py` から自作の非機密fixtureを生成します。この生成処理はローカルの `outputs/allowlist.json` をfixture用に書き換えるため、独自の許可リストを使っている場合は事前に退避してください。テストはモックを利用し、実Jev推論の追加実行は不要です。

## 外部送信と保存対象

通常の文字列検索はctxfilterだけを利用します。実Jev判定は明示的に許可されたファイル・ハッシュのみ対象で、goalや追加ルールの送信許可は別に必要です。曖昧・不完全・変更済み・失敗の証拠は保留します。決定論モードは `literal_label` の抽出であり、意味判定ではありません。

認証情報、Codex設定のバックアップ、Grok実行ログ、ローカルの許可リストはGit管理から除外しています。保存した実測証拠は自作fixtureのものです。登録解除の手順は [rollback](outputs/registration/rollback.md) を参照してください。
