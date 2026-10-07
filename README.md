# websearch-mcp

Strataの「低コストな検索窓口」。検索文字列だけをOpenAI公式Responses APIの`web_search`へ送り、最大5件のタイトル・出典URL・主要情報・短い要約をJSONで返します。比較・考察・最終回答はStrataで行います。

```text
Strata: 検索文字列 → HTTP MCP /mcp (Bearer認証) → websearch-mcp (NAS Docker)
                                                   ↓ 入力検証・予算予約
                                               OpenAI Responses API + web_search（1回）
                                                   ↓ 構造化JSON（最大5件）
Strata: 結果を使って比較・最終回答
```

MCPツールは `web_search` だけです。画像解析は別サーバーの `vision-mcp` を使います。

- 通信方式: Streamable HTTP（Docker）／stdio（ローカル開発）
- HTTPエンドポイント: `/mcp`、コンテナ内ポート`8000`固定
- 公開ツール: `web_search(query: str, search_context_size="low")`
- 既定モデル: `gpt-5.6-luna`

## NASでの起動

1. NAS上にこのリポジトリを配置し、NASのSSHシェルでリポジトリのディレクトリから実行します。UGREENのDockerプロジェクト機能でも同じComposeと`.env`を指定できます。
2. `.env`を設定します。新規cloneでは`cp .env.example .env`で作成します。既存の`.env`を上書きしないでください。

```dotenv
OPENAI_API_KEY=自分のOpenAI_APIキー
OPENAI_MODEL=gpt-5.6-luna
WEBSEARCH_MCP_TOKEN=別途生成した32文字以上のランダム文字列
MCP_BIND_IP=192.168.0.49
SERVER_IP=192.168.0.49
MCP_PORT=8101
MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*
```

`MCP_BIND_IP`はNASホストの公開先インターフェース、`SERVER_IP`はHost検証に追加するNASのIPです。両方設定してください。`MCP_PORT`はホスト側の公開ポートだけに反映し、コンテナ内は8000番固定です。未指定時のホスト側ポートは8101です。NAS名で接続する場合は`MCP_ALLOWED_HOSTS`へ`nas-name:8101`等を追加します。allowed hostsは接続元IP制限ではありません。

認証トークンの生成例（Pythonのある端末で実行）:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

OpenAIキーはNASだけに置き、Strataには**別の**`WEBSEARCH_MCP_TOKEN`を設定します。`vision-mcp`とは別の値にしてください。

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=30 websearch-mcp
```

`config --quiet`は展開した秘密情報を表示せずに設定を検証します。`.env`にキー・認証トークンが空のままだと起動しません。可能なら`chmod 600 .env`にします。`.env`はPythonに自動では読み込まれず、Composeが環境変数に展開します。

既定値の`MCP_BIND_IP=127.0.0.1`ではPhantom（Windows）から接続できません。LAN利用時だけNASのLAN IPへ変更します。NASのファイアウォールでも必要なLAN端末に限定し、ルーターのポート転送は設定しません。HTTPは暗号化されないため、信頼できないネットワークを通す場合はTLSのリバースプロキシやVPNを使用してください。

## Strataへの登録

既存設定の`mcp_servers`の`websearch-mcp`エントリに`headers`を追加してStrataを再起動します。

```json
{
  "mcp_servers": {
    "websearch-mcp": {
      "url": "http://192.168.0.49:8101/mcp",
      "headers": {
        "Authorization": "Bearer .envのWEBSEARCH_MCP_TOKENと同じ値"
      }
    }
  }
}
```

設定断片なので、既存のモデル設定JSON全体を置き換えないでください。ツール定義が変わった場合はStrataを再接続してツール一覧を再取得します。

### 検索の使い方

```python
web_search(
    query="DDR5 32GB 価格",
    search_context_size="low"  # low / medium / high
) -> str  # JSON: results[], limitations, usage
```

| `search_context_size` | 用途 |
|---|---|
| `low` | 既定。価格・日付・単純な事実確認 |
| `medium` | 検索情報の詳細が必要な場合のみ |
| `high` | さらに多い情報が必要な場合のみ。費用が増える可能性がある |

件数を増やすためにmedium/highへ変更しません。網羅調査・最安値保証は行いません。選択値は戻り値の`usage.search_context_size`と使用量ログに含めます。

## コストと出力

| 設定 | 既定値・動作 |
|---|---|
| `OPENAI_MODEL` | `gpt-5.6-luna`。`reasoning.effort=none`と`web_search`に対応するモデルを指定 |
| `SEARCH_MAX_OUTPUT_TOKENS` | 1200。設定範囲256〜4000 |
| 検索回数 | 1リクエストにつき`max_tool_calls=1`、結果は最大5件 |
| APIタイムアウト | HTTP timeout 50秒、呼び出し全体55秒。Strata既定60秒以内を目安 |
| 再試行 | SDKでは0回。失敗時も自動再検索しない |

モデルを変更しても`reasoning.effort=none`は固定で送ります。非対応のモデルではAPIエラーになります。

### サーバー全体の使用量上限

全クライアントで予算を共有し、OpenAI APIを呼ぶ前に確認します。上限に達した後はAPIを呼ばずエラーを返します。

| 環境変数 | Compose既定値 | 意味 |
|---|---|---|
| `SEARCH_MAX_CALLS_PER_WINDOW` | 50 | 直近の時間枠で許可するAPI呼び出し試行数 |
| `SEARCH_WINDOW_SECONDS` | 60 | 時間枠の長さ（秒） |
| `SEARCH_MAX_CALLS_PER_DAY` | 500 | 1日のAPI呼び出し試行数 |
| `SEARCH_MAX_TOKENS_PER_DAY` | 500000 | 入力＋出力tokensの日次予算（予約分を含む） |
| `SEARCH_TOKEN_RESERVATION` | 12000 | 呼び出し前に予約する推定tokens |
| `SEARCH_BUDGET_TIMEZONE` | `Asia/Tokyo` | 日付を区切るタイムゾーン |

数値は設定範囲内の正の整数のみです。**0は無制限ではなく設定エラー**で、サーバーは起動しません。

- API呼び出し前に回数と推定tokensを原子的に予約し、並列の呼び出しにも同じ上限を適用します。
- 応答後に予約tokensを実使用量へ置き換えます。失敗・タイムアウト・使用量不明の場合は予約を保持します。
- 入力検証で失敗した場合は予約前なので消費しません。
- 使用量DBにアクセスできない場合はAPIを呼びません。

**トークン予算は厳密な課金上限ではありません。** 実使用量が予約値を超えると日次予算を超過する場合があります。

使用量はComposeの名前付きボリューム`websearch-mcp-usage`（`/data`）にSQLiteで保存し、コンテナ再作成後も引き継ぎます。記録するのは開始時刻・日付・予約／実使用tokensだけです。通常運用で`docker compose down -v`を使わないでください。

## データと秘密情報

OpenAIへ送るのは検索文字列と固定指示です。Strataの会話履歴は送りません。ただし**検索文字列内の個人情報は外部へ送信されます**。

Responses APIには`store=false`を指定します。これはレスポンス保存を無効化する指定であり、API全体の保持をゼロにする保証ではありません。

- APIキー・`.env`・使用量DBはGitに含めません。Docker build contextにも含めません（`.dockerignore`は許可リスト方式）。
- SDKエラー本文は外へ返さず、キー・検索文字列・検索結果本文をログに出しません。token数などの集計値だけを記録します。
- `OPENAI_BASE_URL`やプロキシ環境変数を使わず、公式`https://api.openai.com/v1`に接続します。
- HTTP MCPは専用Bearer tokenを必須にします。allowed hostsはDNS rebinding対策であり、認証やファイアウォールの代わりではありません。
- コンテナは非root（UID 10001）、ルートFS読み取り専用、追加capabilityなし、`no-new-privileges`。

## 確認・開発

Python 3.12以上、Linux/macOS向けです。

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

テストは実SDK + MockTransportでOpenAI通信を置き換え、外部通信・課金なしでリクエスト形状・入力検証・予算・HTTP認証・Host/Origin・本文サイズ制限を確認します。

接続確認は環境変数`WEBSEARCH_MCP_TOKEN`を設定したシェルで:

```bash
.venv/bin/python check_mcp.py http://192.168.0.49:8101/mcp
```

この確認はinitializeとtools/listだけで、OpenAIを呼びません。実検索で1回だけ確認する場合（課金・使用量上限の対象）:

```bash
.venv/bin/python check_mcp.py http://192.168.0.49:8101/mcp --query "Pythonの公式サイト" --search-context-size low
```

ローカル開発でHTTPを起動する場合、`.env`の値をシェル環境に設定し、`SEARCH_USAGE_DB`を存在するローカル保存先へ設定してから`python server.py --http`を実行します。`--http`なしはstdioです。stdioの標準出力はMCP通信専用なので、デバッグ出力を追加しないでください。

公開Strataクライアントとの任意の互換性テスト:

```bash
STRATA_SOURCE=/path/to/Strata .venv/bin/python -m unittest discover -s tests -v
```

### 1.0.0-alpha からの移行

- HTTP接続にBearer認証が必須になりました。`.env`に`WEBSEARCH_MCP_TOKEN`を追加し、Strata側に`headers`を追加してください。
- `MCP_BIND_IP`を追加しました。未設定だとループバックのみで公開されます。
- `OPENAI_MODEL`を`.env`で変更できるようになりました（以前は`gpt-5.6-luna`固定）。
- API失敗時の例外はSDKのメッセージを含まないToolErrorに統一しました。
- 使用量DBのスキーマとボリューム名は変更していないため、履歴は引き継がれます。
- `check_mcp.py`はHTTP専用になり、検索は`--query`指定時だけ行います。

### フォルダー構成と役割

```text
websearch-mcp/
├─ docker-compose.yaml   ← コンテナの起動・ポート・ハードニング設定
├─ Dockerfile            ← コンテナイメージのビルド定義
├─ .env / .env.example   ← 運用設定（.envはGit管理外）／ひな形
├─ .dockerignore         ← ビルドに含めるファイルの許可リスト
├─ requirements.txt      ← Pythonライブラリの依存関係
├─ server.py             ← サーバーの入口：起動・認証・MCPツール登録
├─ websearch_mcp/        ← 検索のコア処理
│  ├─ config.py          ← 環境変数から設定を読み込む
│  ├─ search.py          ← OpenAIへの検索依頼・結果の検証
│  └─ budget.py          ← 回数・トークン予算の予約と記録
├─ check_mcp.py          ← MCP接続の確認
└─ tests/                ← 自動テスト
```

外側の`websearch-mcp`（ハイフン）はプロジェクト全体、内側の`websearch_mcp`（アンダースコア）はPythonパッケージです。`vision-mcp`と同じ構成です。
