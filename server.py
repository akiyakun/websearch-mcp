import argparse
import hmac
import logging
import os
from ipaddress import ip_address
from typing import Literal

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

from websearch_mcp.config import Settings, integer
from websearch_mcp.search import search


def create_server(settings: Settings) -> MCPServer:
    mcp = MCPServer("websearch-mcp", version="1.0.0")

    @mcp.tool()
    async def web_search(query: str, search_context_size: Literal["low", "medium", "high"] = "low") -> str:
        """低コストの限定 Web 検索。最大5件のタイトル・URL・主要情報・短い要約を返す。
        search_context_size は原則 low（価格・日付・単純な事実確認）。
        検索情報の詳細が必要な場合のみ medium、さらに多い情報が必要な場合のみ high。
        medium/high は費用が増える可能性がある。件数を増やすためには変更しない。
        網羅調査・最安値保証は行わない。比較・考察は呼び出し側で行い、件数を埋めるための反復呼び出しを避ける。
        """
        return await search(settings, query, search_context_size)

    return mcp


class BearerAuth:
    def __init__(self, app, token: str):
        self.app, self.expected = app, ("Bearer " + token).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_http_app(mcp: MCPServer):
    token = os.environ.get("WEBSEARCH_MCP_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("WEBSEARCH_MCP_TOKENに空白を含まない32文字以上のASCII文字列を設定してください。")
    hosts = [x.strip() for x in os.environ.get(
        "MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,[::1]:*"
    ).split(",") if x.strip()]
    if "*" in hosts:
        raise ValueError("MCP_ALLOWED_HOSTSで全ホスト許可は指定できません。")
    server_ip = os.environ.get("SERVER_IP", "").strip()
    if server_ip:
        addr = ip_address(server_ip)
        hosts.append(f"[{addr}]:*" if addr.version == 6 else f"{addr}:*")
    app = mcp.streamable_http_app(
        stateless_http=True, json_response=True, max_request_body_size=16 * 1024,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts),
    )
    return BearerAuth(app, token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # SDK diagnostics may contain request data. Only our aggregate usage log is needed.
    for name in ("openai", "httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    mcp = create_server(Settings.from_env())
    if args.http:
        uvicorn.run(create_http_app(mcp), host=os.environ.get("MCP_HOST", "0.0.0.0"),
                    port=integer("MCP_PORT", 8000, 1, 65535), access_log=False)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
