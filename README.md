# websearch-mcp

MCP 対応の AI アプリケーションから Web 検索を利用するための Python サーバーです。
Docker 上で動作し、OpenAI Responses API の `web_search` を使って、短い検索結果と出典 URL を返します。
検索・事実抽出はサーバー側で行い、比較・考察・最終回答は呼び出し側の AI に任せます。

## 構成

```text
MCP クライアント
  ↓ Streamable HTTP
Docker コンテナ（websearch-mcp）
  ↓ Responses API
OpenAI の Web 検索
```

クライアントは処理を依頼する側、サーバーは依頼を受ける側です。
このサーバーは MCP クライアントから検索文字列を受け取り、OpenAI API に検索を依頼して結果を返します。

- 通信方式：Streamable HTTP（Docker）／stdio（ローカル開発）
- HTTP エンドポイント：`/mcp`
- コンテナ内の待ち受けポート：`8000`
- 公開ツール：`web_search(query: str, search_context_size="low")`
- 使用モデル：`gpt-5.6-luna`

認証は実装していません。信頼できるネットワーク内で利用し、そのままインターネットへ公開しないでください。
検索の実行には OpenAI API の利用料金がかかります。

## Docker Compose で起動する

Docker Engine と Docker Compose が利用できる環境で、プロジェクトのルートディレクトリから実行します。
ホスト側に Python をインストールする必要はありません。

### 1. 環境変数を設定する

`docker-compose.yaml` と同じディレクトリに `.env` を作成します。

```dotenv
OPENAI_API_KEY=your-api-key
SERVER_IP=
MCP_PORT=8000

SEARCH_MAX_CALLS_PER_WINDOW=3
SEARCH_WINDOW_SECONDS=600
SEARCH_MAX_CALLS_PER_DAY=20
SEARCH_MAX_TOKENS_PER_DAY=200000
SEARCH_TOKEN_RESERVATION=12000
SEARCH_BUDGET_TIMEZONE=Asia/Tokyo
```

`your-api-key` を実際の API キーに置き換えてください。
同じホストから localhost で接続する場合、`SERVER_IP` は空のままで利用できます。
別のホストから接続する場合は、クライアントが接続先に使うサーバーの IP を指定します。
`.env` は Git と Docker のビルド対象から除外しています。キーをソースコードへ直接書かないでください。

### 2. 設定を確認して起動する

```sh
docker compose -f docker-compose.yaml config --quiet
docker compose -f docker-compose.yaml up -d --build
```

`config --quiet` は展開した API キーを表示せずに設定を検証します。
検証が成功したことを確認してから起動してください。
Docker の操作に追加の権限が必要な環境では、それぞれの環境の運用方法に従って実行します。

### 3. 起動状態を確認する

```sh
docker compose -f docker-compose.yaml ps
docker compose -f docker-compose.yaml logs --tail 30 websearch-mcp
```

コンテナが起動状態になり、ログに `Application startup complete` が表示されればサーバーの起動は完了です。
API キーやモデルの利用権限は、実際に検索する際に確認されます。

## MCP クライアントから接続する

クライアントに次の情報を設定します。

| 項目 | 値 |
| --- | --- |
| 名前 | `websearch-mcp`（任意） |
| 通信方式 | Streamable HTTP |
| URL | `http://<server-host>:<published-port>/mcp` |
| 認証 | なし |

`<server-host>` は接続先のホスト、`<published-port>` は `.env` の `MCP_PORT` に置き換えます。
同じホストから既定ポートで接続する場合は `http://localhost:8000/mcp` です。
OpenAI API キーはサーバー側に設定するため、クライアントへ渡す必要はありません。

設定ファイルの形式はクライアントによって異なります。
`mcp_servers` を使うクライアントでは、次のような設定になります。

```json
{
  "mcp_servers": {
    "websearch-mcp": {
      "url": "http://<server-host>:<published-port>/mcp"
    }
  }
}
```

上記は置き換え用のテンプレートです。既存の設定に統合し、クライアントの仕様に合わせてください。
ブラウザで URL を開くだけでは MCP の接続確認にはなりません。

## 検索結果とコスト制限

`web_search(query, search_context_size="low")` は、次のフィールドを含む JSON テキストを返します。

| フィールド | 内容 |
| --- | --- |
| `results` | 最大5件の結果。各結果は `title`・`url`・`facts`・`summary` |
| `limitations` | 限定検索であり、網羅性や最安値順位を保証しない旨 |
| `usage` | 入力・出力 tokens と検索ツール呼び出し数 |

価格などの主要情報は確認できた範囲で返し、不明な条件は未確認とします。
「トップ10」の依頼でも最大5件に制限し、件数を埋めるための追加調査は行わないよう指示します。

| 制限 | 設定値 |
| --- | --- |
| モデル | `gpt-5.6-luna` |
| 推論 | `reasoning.effort="none"` |
| API 応答内の検索ツール呼び出し | 最大1回 |
| 検索コンテキスト | `low`（既定）／`medium`／`high` を呼び出し側で選択 |
| 生成 tokens | 最大1,200（推論分を含む） |
| 検索文字列 | 500文字以内 |
| 各結果の長さ | タイトル120・URL1,000・主要情報200・要約120文字以内 |
| SDK の自動リトライ | なし |

返却件数は検索サービス内部の取得ページ数とは異なります。
`low` は入力 tokens の厳密な上限ではなく、内部のページ数も厳密には指定できません。
出力が上限に達した場合や形式が不正な場合、上限を増やして自動再試行することはありません。

使用量は戻り値の `usage` と、ログの `search_usage` 行で確認できます。
このログには応答 ID・モデル・完了状態・使用量を記録し、API キー・検索語・結果本文は記録しません。
Web 検索にはモデルの token 料金に加え、ツールの利用料金がかかります。最新の料金は公式資料を参照してください。

- [モデル仕様](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Web 検索の仕様](https://developers.openai.com/api/docs/guides/tools-web-search)
- [API 料金](https://developers.openai.com/api/docs/pricing)

### 検索コンテキストの選択

MCP クライアントは `search_context_size` 引数に `low`・`medium`・`high` を指定できます。
省略時は `low` なので、従来の `query` だけの呼び出しも利用できます。
ツールのスキーマと説明に選択肢を公開し、クライアントの AI が内容に応じて選べるようにしています。

```json
{"query": "DDR4メモリの販売価格を検索", "search_context_size": "low"}
```

価格・日付・単純な事実確認は `low` を基本とします。検索結果の詳細が必要な場合のみ
`medium`、さらに多くの情報が必要な場合のみ `high` を使います。
これはモデルに渡す検索情報量の指定であり、検索件数・ページ数や厳密な token 数の指定ではありません。
`low` にすれば各依頼の総料金が必ず下がるわけではなく、繰り返し呼び出せば費用は増えます。
結果5件・検索1回・出力1,200 tokens・サーバー全体の予算は、どの値でも維持します。
トークン予約値も自動増加しないため、情報量を増やす際は予約値と実使用量の差に注意してください。
選択値は戻り値の `usage.search_context_size` と使用量ログに含めます。

確認用クライアントでは次のように指定できます。

```sh
python check_mcp.py --url "http://<server-host>:<published-port>/mcp" --search-context-size low "DDR4メモリの販売価格を検索"
```

更新後はコンテナを再ビルド・再作成し、MCP クライアントを再接続してツール定義を再取得してください。

## サーバー全体の使用量上限

全クライアントで予算を共有し、OpenAI API を呼ぶ前に確認します。
質問の境界は判別しないため、依頼単位ではなく時間枠と日付で制限します。
クライアントが繰り返しツールを呼んでも、上限に達した後は API を呼ばずエラーを返します。

| 環境変数 | 初期値 | 意味 |
| --- | --- | --- |
| `SEARCH_MAX_CALLS_PER_WINDOW` | `3` | 直近の時間枠で許可する API 呼び出し試行数 |
| `SEARCH_WINDOW_SECONDS` | `600` | 時間枠の長さ（秒） |
| `SEARCH_MAX_CALLS_PER_DAY` | `20` | 1日の API 呼び出し試行数 |
| `SEARCH_MAX_TOKENS_PER_DAY` | `200000` | 入力＋出力 tokens の日次予算（予約分を含む） |
| `SEARCH_TOKEN_RESERVATION` | `12000` | 呼び出し前に予約する推定 tokens |
| `SEARCH_BUDGET_TIMEZONE` | `Asia/Tokyo` | 日付を区切るタイムゾーン |
| `SEARCH_USAGE_DB` | Docker：`/data/usage.sqlite3`、ローカル：`.usage/usage.sqlite3` | 使用量の保存先 |

数値の設定は正の整数のみです。**0 は無制限ではなく設定エラー**です。
Compose では上限値を省略すると初期値を使用します。
`SEARCH_USAGE_DB` は付属の Compose ファイルで固定しているため、変更する場合はファイルとボリュームの設定を合わせてください。

### 予算の数え方

- API 呼び出し前に回数と推定 tokens を予約し、並列の呼び出しにも同じ上限を適用します。
- 応答後に予約 tokens を実使用量へ置き換えます。予算に予約分の空きがなければ開始しません。
- 失敗・タイムアウトも呼び出し回数に含めます。使用量不明やプロセス中断の場合は予約を保持します。
- 使用量は呼び出し開始日の予算に計上します。日付変更後も直近の時間枠による回数制限は維持します。
- 設定不正や保存先の破損などで予算を確認できない場合は、API を呼ばず停止します。

**トークン予算は厳密な課金上限ではありません。** 実使用量が予約値を超えると、日次予算を超過する場合があります。
その後の新しい呼び出しは停止しますが、既に実行中の処理にも超過の可能性があります。

### 使用量の永続化

Compose は名前付きボリューム `websearch-mcp-usage` を `/data` に接続します。
同じボリュームを使用すれば、コンテナの再起動・再作成後も使用量を引き継ぎます。
記録するのは開始時刻・日付・予約／実使用 tokens で、キー・検索語・結果本文は保存しません。

ボリュームの削除・変更は使用量をリセットします。通常の更新で `docker compose down -v` を実行しないでください。
初回導入時は0から開始し、過去の API 使用量を自動取得しません。
保存先は1つの Docker ホストのローカルボリュームを想定しています。複数ホストやネットワーク共有上での SQLite 共有は対象外です。

## ネットワーク設定

| 設定 | 用途 |
| --- | --- |
| `SERVER_IP` | 接続先として許可するサーバーの IP。IPv4／IPv6 に対応 |
| `MCP_PORT` | Compose で指定するホスト側の公開ポート。コンテナ内部は `8000` 固定 |
| `MCP_HOST` | サーバーの待ち受けアドレス。既定は `0.0.0.0` |
| `MCP_ALLOWED_HOSTS` | 接続先として許可するホスト名など。カンマ区切り。既定は localhost とループバック |

`SERVER_IP` はコンテナの待ち受けアドレスではなく、接続先の許可リストへ追加する値です。
Docker では `MCP_HOST` は通常変更しません。
`MCP_ALLOWED_HOSTS` は接続元クライアントの許可リストではありません。
ホスト名で接続する場合は、Compose の `environment` に `MCP_ALLOWED_HOSTS` を追加してください。
例：`localhost:*,127.0.0.1:*,mcp.example.internal:*`。`SERVER_IP` はこのリストへ別途追加されます。

付属 Compose のポート指定は `${MCP_PORT}:8000` です。
ホスト側のポートを変更した場合は、クライアントの接続 URL も変更します。
`EXPOSE 8000` はイメージのポート情報であり、それだけではホストへ公開されません。

## 更新・停止

コードや `.env` を変更した後は、次のコマンドで再ビルド・再作成します。

```sh
docker compose -f docker-compose.yaml config --quiet
docker compose -f docker-compose.yaml up -d --build
```

停止のみ行う場合：

```sh
docker compose -f docker-compose.yaml stop
```

コンテナとネットワークを削除し、使用量ボリュームを残す場合：

```sh
docker compose -f docker-compose.yaml down
```

以前 `docker run` で作った同名コンテナがある場合は、設定の検証後に旧コンテナを停止・削除してから Compose で起動します。
使用量を引き継ぐ場合は、同じ名前のボリュームを使用してください。

## 開発・接続確認・テスト

Docker での運用とは別に、Python 3.10 以上でローカルの確認用クライアントとテストを実行できます。
以下のコマンドはプロジェクトのルートで、仮想環境を有効にした状態で実行します。

```sh
python -m pip install -r requirements.txt
python check_mcp.py --url "http://<server-host>:<published-port>/mcp" --list-only
```

プレースホルダーを接続先に置き換えてください。
`公開ツール: web_search` と表示されれば MCP 接続は成功です。一覧取得では OpenAI API を呼びません。
実検索は次で確認します。API 利用料金と使用量上限の対象になります。

```sh
python check_mcp.py --url "http://<server-host>:<published-port>/mcp" "Pythonの公式サイトを検索してください"
```

HTTP 接続では接続先サーバーの API キーと予算を使います。確認クライアントを終了してもサーバーは停止しません。

### stdio での開発

```sh
python check_mcp.py --list-only
python check_mcp.py
```

`--url` を省略すると確認スクリプトがサーバーを子プロセスとして起動し、終了時に停止します。
検索時に `OPENAI_API_KEY` が未設定なら、対話入力を求めます。
サーバーを直接起動する場合は `python server.py`、HTTP で起動する場合は `python server.py --http` を使います。
stdio の標準出力は MCP 通信専用なので、デバッグ出力を追加しないでください。

`.env` の読み込みは Compose が行います。Python コード自体は `.env` を自動では読み込みません。
ローカル実行では必要な環境変数をプロセスへ渡してください。使用量の保存先も Docker とは別です。
`OPENAI_MODEL` は未設定または `gpt-5.6-luna` のみ受け付けます。

### 回帰テスト

```sh
python -m unittest discover -s tests -v
```

API 応答をダミーに置き換え、MCP の返却・入力検証・回数制限・日次予算・並列予約などを確認します。
実際の API 呼び出しは行わず、検索品質・モデル利用権限・課金額は検証しません。

## ファイルと依存関係

| ファイル | 役割 |
| --- | --- |
| `server.py` | MCP ツールと OpenAI API 呼び出し |
| `usage_limits.py` | 使用量の予約・記録・上限判定 |
| `check_mcp.py` | stdio／HTTP の接続確認用クライアント |
| `tests/` | API を使わない回帰テスト |
| `Dockerfile` | Python 3.12 ベースのイメージ作成 |
| `docker-compose.yaml` | ポート・環境変数・永続化ボリュームの設定 |
| `requirements.txt` | Python の依存パッケージ |
| `.dockerignore` | 秘密情報や開発用ファイルをビルド対象から除外 |

コンテナは UID 10001 の一般ユーザーで動作します。
コピーするコードは `COPY --chmod=644` で読み取り可能にし、使用量保存用の `/data` は実行ユーザーが書き込めるようにします。

SDK は外部サービスや通信機能を扱うためのライブラリです。

| パッケージ | 用途 |
| --- | --- |
| `mcp` | [公式 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。クライアントへツールを公開 |
| `openai` | [公式 OpenAI Python SDK](https://developers.openai.com/api/reference/python)。Responses API を呼び出す |
| `tzdata` | 日次予算の区切りに使うタイムゾーンデータ |

`@mcp.tool()` が Python 関数を MCP ツールとして公開し、`AsyncOpenAI()` が OpenAI API を呼び出します。
自作 MCP ツールの `web_search` と、OpenAI の組み込みツール `web_search` は別のもので、このサーバーが両者をつなぎます。
