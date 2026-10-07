import asyncio
import io
import unittest
from contextlib import asynccontextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult, TextContent
from check_mcp import check, diagnose, ToolCallFailed


class CheckMcpTests(unittest.TestCase):
    def run_check(self, is_error, query="test"):
        session = SimpleNamespace(
            initialize=AsyncMock(),
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[SimpleNamespace(name="web_search")])),
            call_tool=AsyncMock(return_value=CallToolResult(
                content=[TextContent(type="text", text="test result")], isError=is_error)),
        )

        @asynccontextmanager
        async def transport(*args, **kwargs):
            yield None, None

        @asynccontextmanager
        async def client_session(*args, **kwargs):
            yield session

        args = SimpleNamespace(url="http://example.invalid/mcp", query=query, search_context_size="low")
        with patch.dict("os.environ", {"WEBSEARCH_MCP_TOKEN": "test-token"}), \
             patch("check_mcp.streamable_http_client", transport), \
             patch("check_mcp.ClientSession", client_session):
            asyncio.run(check(args))
        return session

    def test_list_only_never_calls_tool(self):
        with redirect_stdout(io.StringIO()):
            session = self.run_check(False, query=None)
        session.call_tool.assert_not_awaited()

    def test_successful_search_does_not_report_failure(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.run_check(False)
        self.assertIn("test result", output.getvalue())

    def test_tool_error_is_distinguished_from_connection_failure(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(ToolCallFailed) as caught:
            self.run_check(True)
        self.assertIn("MCP接続は成功", diagnose(caught.exception))
