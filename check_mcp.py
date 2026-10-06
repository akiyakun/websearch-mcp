"""MCP 経由でツール一覧の取得と Web 検索を確認します。"""

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="Model Context Protocolとは何ですか？公式サイトを検索し、日本語で短く説明してください。")
    parser.add_argument("--list-only", action="store_true", help="ツール一覧だけ取得する（API 呼び出しなし）")
    parser.add_argument("--url", help="起動済みの HTTP MCP サーバーへ接続する URL")
    args = parser.parse_args()

    # MCP の子プロセスには、必要な環境変数を明示的に渡します。
    env = {name: os.environ[name] for name in ("OPENAI_API_KEY", "OPENAI_MODEL") if os.environ.get(name)}
    if not args.list_only:
        if not args.query.strip():
            parser.error("検索文字列を入力してください。")
        if not args.url and "OPENAI_API_KEY" not in env:
            if not sys.stdin.isatty():
                parser.error("環境変数 OPENAI_API_KEY を設定してください。")
            env["OPENAI_API_KEY"] = getpass.getpass("OpenAI API キー（画面には表示されません）: ").strip()
            if not env["OPENAI_API_KEY"]:
                parser.error("API キーが空です。")

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py").resolve())],
        env=env,
    )
    # HTTP 接続ではキーは送信せず、接続先サーバーに設定されたキーを使います。
    async with Client(args.url or server) as client:
        tools = await client.list_tools()
        print("公開ツール:", ", ".join(tool.name for tool in tools.tools), flush=True)
        if args.list_only:
            return 0
        print("MCP 経由で検索中です（OpenAI API の利用料金が発生します）…", flush=True)
        result = await client.call_tool("web_search", {"query": args.query})
        for content in result.content:
            if content.type == "text":
                print(content.text)
        return 1 if result.is_error else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
