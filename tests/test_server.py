"""API 料金を発生させず、コスト制限と MCP の返却を確認するテスト。"""
import json
import os
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

import server


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "OPENAI_MODEL": server.MODEL, "SEARCH_USAGE_DB": folder.name + "/usage.db", "SEARCH_MAX_CALLS_PER_WINDOW": "100", "SEARCH_MAX_CALLS_PER_DAY": "100", "SEARCH_MAX_TOKENS_PER_DAY": "10000000"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.result = {"title": "DDR5 32GB", "url": "https://example.com/item", "facts": "税込12,000円。送料未確認。", "summary": "32GBキットの販売情報。"}
        self.response = SimpleNamespace(
            id="test-response", status="completed",
            usage=SimpleNamespace(input_tokens=2000, output_tokens=300),
            output=[SimpleNamespace(type="web_search_call")],
            output_text=json.dumps({"results": [self.result]}),
        )
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.client.responses.create.return_value = self.response
        self.factory = patch.object(server, "AsyncOpenAI", return_value=self.client)
        self.factory_mock = self.factory.start()
        self.addCleanup(self.factory.stop)

    async def test_mcp_result_and_cost_controls(self):
        async with Client(server.mcp) as client:
            result = await client.call_tool("web_search", {"query": "DDRメモリ 最安値トップ10"})
        self.assertFalse(result.is_error)
        data = json.loads(result.content[0].text)
        self.assertEqual(data["results"], [self.result])
        self.assertEqual(data["usage"]["input_tokens"], 2000)
        self.factory_mock.assert_called_once_with(max_retries=0, timeout=60.0)
        self.client.responses.create.assert_awaited_once()
        args = self.client.responses.create.call_args.kwargs
        self.assertEqual(args["model"], "gpt-5.6-luna")
        self.assertEqual(args["max_tool_calls"], 1)
        self.assertEqual(args["max_output_tokens"], 1200)
        self.assertEqual(args["reasoning"], {"effort": "none"})
        self.assertEqual(args["tools"][0]["search_context_size"], "low")
        self.assertEqual(args["text"]["format"]["schema"]["properties"]["results"]["maxItems"], 5)

    async def test_budget_blocks_before_api(self):
        os.environ["SEARCH_MAX_CALLS_PER_WINDOW"] = "1"
        await server.web_search("test")
        with self.assertRaises(ToolError):
            await server.web_search("test again")
        self.client.responses.create.assert_awaited_once()

    async def test_api_failure_still_uses_call_budget(self):
        os.environ["SEARCH_MAX_CALLS_PER_DAY"] = "1"
        self.client.responses.create.side_effect = TimeoutError("test timeout")
        with self.assertRaises(TimeoutError):
            await server.web_search("test")
        with self.assertRaises(ToolError):
            await server.web_search("retry")
        self.client.responses.create.assert_awaited_once()

    async def test_rejects_costly_environment_before_api(self):
        os.environ["OPENAI_MODEL"] = "gpt-5.6-sol"
        with self.assertRaises(ToolError):
            await server.web_search("test")
        self.factory_mock.assert_not_called()

    async def test_query_limits_before_api(self):
        for query in (" ", "a" * 501):
            with self.assertRaises(ToolError):
                await server.web_search(query)
        self.factory_mock.assert_not_called()

    async def test_incomplete_not_retried(self):
        self.response.status = "incomplete"
        with self.assertRaises(ToolError):
            await server.web_search("test")
        self.client.responses.create.assert_awaited_once()

    async def test_bad_or_oversized_results_not_retried(self):
        for text in ('{"results":', json.dumps({"results": [self.result] * 6}),
                     json.dumps({"results": [{**self.result, "facts": "x" * 201}]}),
                     json.dumps({"results": [{**self.result, "url": "file:///secret"}]})):
            self.client.responses.create.reset_mock()
            self.response.output_text = text
            with self.assertRaises(ToolError):
                await server.web_search("test")
            self.client.responses.create.assert_awaited_once()

    async def test_no_search_not_presented_as_search(self):
        self.response.output = []
        with self.assertRaises(ToolError):
            await server.web_search("test")

    async def test_empty_results_are_valid(self):
        self.response.output_text = '{"results": []}'
        self.assertEqual(json.loads(await server.web_search("test"))["results"], [])


if __name__ == "__main__":
    unittest.main()
