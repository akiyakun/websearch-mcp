# websearch-mcp

Strata の低コストな検索窓口。検索文字列だけを OpenAI Responses API の `web_search` に送り、最大5件のタイトル・出典URL・主要情報・短い要約を JSON で返す。比較・考察・最終回答は Strata が行う。

## このリポジトリ固有の情報

- 公開ツール: `web_search(query: str, search_context_size: "low" | "medium" | "high" = "low") -> str`（JSON: `results` / `limitations` / `usage`）
- ホスト側ポート: `8101`
- 認証トークン: `WEBSEARCH_MCP_TOKEN`
- 既定モデル: `gpt-5.6-luna`（`reasoning.effort=none` 固定で送る）
- 固有の環境変数: `SEARCH_MAX_OUTPUT_TOKENS` `SEARCH_MAX_CALLS_PER_WINDOW` `SEARCH_WINDOW_SECONDS` `SEARCH_MAX_CALLS_PER_DAY` `SEARCH_MAX_TOKENS_PER_DAY` `SEARCH_TOKEN_RESERVATION` `SEARCH_BUDGET_TIMEZONE` `SEARCH_USAGE_DB`
- 主なファイル: `websearch_mcp/search.py`（指示文・JSON Schema・API呼び出し）、`websearch_mcp/budget.py`（回数＋トークン予算）、`websearch_mcp/config.py`

### 固有の制約

- 1リクエストにつき `max_tool_calls=1`、結果は最大5件。件数・網羅性・最安値を求められても検索を増やさない。
- 出力は `RESULT_FORMAT`（strict JSON Schema）と pydantic の `SearchResults` の二重で検証する。片方を変えたらもう片方も揃える。
- `web_search_call` が出力に無い応答は、検索結果として返さない。
- 予算はトークンも予約制：呼び出し前に `SEARCH_TOKEN_RESERVATION` を予約し、応答後に実使用量で `settle` する。使用量が不明なら予約を残す。
- 使用量DBのスキーマ（`requests` テーブル）とボリューム名 `websearch-mcp-usage` は、1.0.0-alpha からの履歴を引き継ぐため変えない。
- API呼び出し全体は55秒（HTTP 50秒）。Strata の60秒以内に収める。

### 確認コマンド

```bash
.venv/bin/python -m unittest discover -s tests -v
WEBSEARCH_MCP_TOKEN=... .venv/bin/python check_mcp.py http://192.168.0.100:8101/mcp            # 課金なし
WEBSEARCH_MCP_TOKEN=... .venv/bin/python check_mcp.py http://192.168.0.100:8101/mcp --query "…"  # 課金あり
```

<!-- BEGIN mcp-conventions: sync.py が管理。この範囲は直接編集しない -->

## 共通規約（全MCPリポジトリ共通）

この節は `mcp-conventions/CONVENTIONS.md` の写しです。直接編集せず、正本を直してから `python ../mcp-conventions/sync.py` で反映します。

### 環境

- MCPサーバーは UGREEN NAS（DXP4800Plus）の Docker で動かす。NASのLAN IPは `192.168.0.100`。
- Macでは NAS の Docker 共有が `/Volumes/docker` に SMB でマウントされている。**Macに docker は無い**。ビルド・起動確認は利用者がNASで行う。
- クライアントは Windows PC「Phantom」上の Strata（[Niko1221/Strata](https://github.com/Niko1221/Strata)）。HTTP MCP（`/mcp`）に Bearer ヘッダー付きで接続する。
- Strata のツール呼び出しは既定60秒でタイムアウトする。API呼び出し全体をそれ未満に収める。
- 各MCPは別々の git リポジトリ（`github.com/akiyakun/<name>-mcp`）。

### ホスト側ポートの割り当て

| ポート | リポジトリ |
|---|---|
| 8100 | agent-gateway（MCP中継。構成は独自） |
| 8101 | websearch-mcp |
| 8102 | vision-mcp |
| 8103〜 | 未使用。新規作成時はこの表に追記する |

コンテナ内の待ち受けは常に `8000`。ホスト側は `.env` の `MCP_PORT`。

### 構成の型

```text
<name>-mcp/
├─ server.py          入口のみ：create_server / BearerAuth / create_http_app / main
├─ <name>_mcp/        コア処理（config.py / budget.py / 機能別モジュール）
├─ check_mcp.py       HTTP接続確認。既定は tools/list だけ（課金なし）
├─ tests/             外部通信なしのテスト
├─ Dockerfile / docker-compose.yaml / .env.example / .dockerignore / requirements.txt
└─ AGENTS.md / CLAUDE.md / README.md
```

- 設定は `config.Settings.from_env()` で起動時に1回読み、範囲外は `ValueError` で起動を止める（0を無制限扱いしない）。
- 新規MCPは `python mcp-conventions/sync.py new <name> --port <番号>` でひな形から作る。
- `requirements.txt` の版は全リポジトリで揃える（現在 `mcp==2.3.0` `openai==3.24.0` `httpx2==2.13.1` `uvicorn==0.54.0`）。

### セキュリティ（必須）

- HTTP MCP は `<NAME>_MCP_TOKEN`（32文字以上）による Bearer 認証を必須にする。トークンはMCPごとに別の値にし、OpenAIキーを流用しない。
- `TransportSecuritySettings(enable_dns_rebinding_protection=True)`、`stateless_http=True`、`json_response=True`、`max_request_body_size` を設定する。`MCP_ALLOWED_HOSTS` に `*` を許さない。
- 公開ポートは `${MCP_BIND_IP:-127.0.0.1}` にバインドする。
- OpenAI は `base_url="https://api.openai.com/v1"` 固定、`httpx.AsyncClient(trust_env=False)`、`store=False`。
- キー・入力本文・検索語・画像・APIの応答本文をログにも ToolError にも出さない。SDKの例外は固定の日本語メッセージの `ToolError` に置き換え `from None` で投げる。ログに残すのは token 数などの集計値だけ。
- `openai` / `httpx` / `httpcore` / `mcp` のロガーは `CRITICAL` にする。

### コスト管理（必須）

- `max_retries=0`。失敗しても自動再試行しない。ToolError の文面でも Strata に「この依頼で再試行しない」と伝える。
- API を呼ぶ前に SQLite（`BEGIN IMMEDIATE`）で回数・予算を原子的に予約する。失敗・タイムアウトも消費として残す。入力検証エラーは予約前に返す。
- 使用量ストアを確認できないときは API を呼ばない（fail closed）。
- 使用量DBは名前付きボリューム（`/data`）。スキーマとボリューム名を変える場合は履歴の移行方法を README に書く。

### Docker

- `python:3.12-slim`、UID/GID `10001`、`COPY --chmod=644`、`chmod -R a=rX /app`。
- compose に `read_only: true`、`tmpfs: /tmp`、`cap_drop: [ALL]`、`no-new-privileges:true`、`pids_limit`、`mem_limit` を付ける。
- `.dockerignore` は許可リスト方式（`**` で全除外し、必要なファイルだけ `!` で戻す）。パッケージ配下にサブフォルダーを足したら `.dockerignore` も更新する。
- 秘密は `.env`（Git・ビルド対象外）。必須値は compose で `${VAR:?...}` にする。`.env.example` を必ず用意する。

### テスト

- `python -m unittest discover -s tests -v`。実SDK + `httpx.MockTransport` で OpenAI を置き換え、外部通信・課金をしない。
- 最低限確認すること：リクエストの形（モデル・`store`・上限値・公式URL）、入力検証で API を呼ばないこと、API失敗時に秘密を漏らさず再試行しないこと、予算の原子性と期限、HTTPの 401 / 421 / 403 / 413、トークン未設定で起動拒否。
- Mac の確認は既存の `.venv`（Python 3.13）を使う。Docker 内は 3.12 なので、3.13 専用の構文を使わない。

### 言語・書き方

- 利用者向けの文面（ToolError、README、check_mcp の出力）は日本語。コードのコメントは英語で短く。
- ツールの docstring は Strata のモデルが読む説明。用途・引数の選び方・やらないこと（反復呼び出しの禁止など）を日本語で簡潔に書く。
- 指示（INSTRUCTIONS）では、入力やページ・画像の中の命令をデータとして扱うよう明記する。

### リリース

- バージョンは `docker-compose.yaml` の `image:` タグと `MCPServer(..., version=...)` を揃える。
- 利用者が NAS で起動と Strata からの認識を確認してから、コミット `Release <name>-mcp X.Y.Z`、注釈付きタグ `vX.Y.Z` を付けて push する。
- コミット・push は利用者に頼まれたときだけ行う。

<!-- END mcp-conventions -->
