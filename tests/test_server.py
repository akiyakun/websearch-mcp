"""API 料金を発生させず、コスト制限・HTTP ガード・MCP の返却を確認するテスト。"""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx2 as httpx
from mcp.server.mcpserver.exceptions import ToolError
from openai import AsyncOpenAI
from starlette.testclient import TestClient

from server import create_http_app, create_server
from websearch_mcp.budget import BudgetError, reserve, settle
from websearch_mcp.config import Settings
from websearch_mcp.search import search

TOKEN = "test-token-" + "a" * 32
RESULT = {"title": "DDR5 32GB", "url": "https://example.com/item",
          "facts": "税込12,000円。送料未確認。", "summary": "32GBキットの販売情報。"}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings("gpt-5.6-luna", 1200, self.root / "usage.db",
                                 600, 100, 100, 10_000_000, 12000, ZoneInfo("Asia/Tokyo"))
        env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret-never-log", "WEBSEARCH_MCP_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)

    def api(self, status=200, response_status="completed", text=None, searched=True, usage=True):
        self.requests = []

        def handler(request):
            self.requests.append(request)
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "test-secret-never-log query", "type": "server_error"}})
            output = [{"id": "ws_test", "type": "web_search_call", "status": "completed",
                       "action": {"type": "search", "query": "q"}}] if searched else []
            output.append({"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "annotations": [],
                                        "text": json.dumps({"results": [RESULT]}) if text is None else text}]})
            body = {"id": "resp_test", "object": "response", "created_at": 1,
                    "status": response_status, "model": "gpt-5.6-luna", "output": output}
            if usage:
                body["usage"] = {"input_tokens": 2000, "output_tokens": 300, "total_tokens": 2300,
                                 "input_tokens_details": {"cached_tokens": 0},
                                 "output_tokens_details": {"reasoning_tokens": 0}}
            return httpx.Response(200, json=body)

        def factory(**kwargs):
            # Exercise the real SDK serializer and HTTP error handling without external calls.
            supplied = kwargs.pop("http_client")
            asyncio.get_running_loop().create_task(supplied.aclose())
            return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return patch("websearch_mcp.search.AsyncOpenAI", side_effect=factory)

    def run_search(self, query="test", size="low", settings=None):
        return asyncio.run(search(settings or self.settings, query, size))

    def rows(self):
        with closing(sqlite3.connect(self.settings.usage_db)) as db:
            return db.execute("SELECT tokens, settled FROM requests ORDER BY id").fetchall()


class ApiTests(Fixture):
    def test_request_and_output(self):
        with self.api():
            data = json.loads(self.run_search("DDRメモリ 最安値トップ10"))
        self.assertEqual(data["results"], [RESULT])
        self.assertEqual(data["usage"]["input_tokens"], 2000)
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual(str(req.url), "https://api.openai.com/v1/responses")
        body = json.loads(req.content)
        self.assertEqual(body["model"], "gpt-5.6-luna")
        self.assertFalse(body["store"])
        self.assertEqual(body["max_tool_calls"], 1)
        self.assertEqual(body["max_output_tokens"], 1200)
        self.assertEqual(body["reasoning"], {"effort": "none"})
        self.assertEqual(body["tools"][0]["search_context_size"], "low")
        self.assertEqual(body["text"]["format"]["schema"]["properties"]["results"]["maxItems"], 5)
        self.assertEqual(self.rows(), [(2300, 1)])

    def test_context_sizes(self):
        for size in ("low", "medium", "high"):
            with self.subTest(size=size), self.api():
                data = json.loads(self.run_search(size=size))
                self.assertEqual(json.loads(self.requests[0].content)["tools"][0]["search_context_size"], size)
                self.assertEqual(data["usage"]["search_context_size"], size)

    def test_invalid_inputs_never_call_api(self):
        with self.api():
            for query, size in ((" ", "low"), ("a" * 501, "low"), ("test", "unlimited")):
                with self.assertRaises(ToolError):
                    self.run_search(query, size)
            self.assertFalse(self.requests)
        self.assertFalse(self.settings.usage_db.exists())

    def test_api_error_no_secret_or_retry_and_still_counted(self):
        with self.api(status=500), self.assertRaises(ToolError) as exc:
            self.run_search()
        self.assertNotIn("test-secret", str(exc.exception))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.rows(), [(12000, 0)])

    def test_incomplete_not_retried(self):
        with self.api(response_status="incomplete"), self.assertRaises(ToolError):
            self.run_search()
        self.assertEqual(len(self.requests), 1)

    def test_bad_or_oversized_results_not_retried(self):
        for text in ('{"results":', json.dumps({"results": [RESULT] * 6}),
                     json.dumps({"results": [{**RESULT, "facts": "x" * 201}]}),
                     json.dumps({"results": [{**RESULT, "url": "file:///secret"}]})):
            with self.subTest(text=text[:30]), self.api(text=text), self.assertRaises(ToolError):
                self.run_search()
            self.assertEqual(len(self.requests), 1)

    def test_no_search_not_presented_as_search(self):
        with self.api(searched=False), self.assertRaises(ToolError):
            self.run_search()

    def test_unknown_usage_keeps_reservation(self):
        with self.api(usage=False):
            self.run_search()
        self.assertEqual(self.rows(), [(12000, 0)])

    def test_empty_results_are_valid(self):
        with self.api(text='{"results": []}'):
            self.assertEqual(json.loads(self.run_search())["results"], [])

    def test_budget_blocks_before_api(self):
        settings = replace(self.settings, calls_per_day=1)
        with self.api():
            self.run_search(settings=settings)
            with self.assertRaises(ToolError):
                self.run_search(settings=settings)
        self.assertEqual(len(self.requests), 1)

    def test_budget_store_failure_closed(self):
        with self.api(), self.assertRaises(ToolError):
            self.run_search(settings=replace(self.settings, usage_db=self.root / "missing" / "db"))
        self.assertFalse(self.requests)


class BudgetTests(Fixture):
    def setUp(self):
        super().setUp()
        self.settings = replace(self.settings, calls_per_window=3, calls_per_day=20, tokens_per_day=200000)

    def test_parallel_reservations_stop_at_three(self):
        def attempt(_):
            try:
                reserve(self.settings)
                return 1
            except BudgetError:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(12))), 3)

    def test_persistent_window_and_expiry(self):
        with patch("websearch_mcp.budget.time.time", return_value=100000):
            for _ in range(3):
                reserve(self.settings)
            with self.assertRaises(BudgetError):
                reserve(self.settings)
        with patch("websearch_mcp.budget.time.time", return_value=100600):
            reserve(self.settings)

    def test_daily_calls_and_tokyo_midnight(self):
        settings = replace(self.settings, calls_per_day=1)
        start = datetime(2026, 10, 7, 23, 0, tzinfo=ZoneInfo("Asia/Tokyo")).timestamp()
        with patch("websearch_mcp.budget.time.time", return_value=start):
            reserve(settings)
        with patch("websearch_mcp.budget.time.time", return_value=start + 700), self.assertRaises(BudgetError):
            reserve(settings)
        with patch("websearch_mcp.budget.time.time", return_value=start + 3600):
            reserve(settings)

    def test_tokens_reserve_settle_and_overshoot(self):
        settings = replace(self.settings, tokens_per_day=20000)
        first = reserve(settings)
        with self.assertRaises(BudgetError):
            reserve(settings)
        settle(settings, first, 2000)
        second = reserve(settings)
        settle(settings, second, 30000)
        with self.assertRaises(BudgetError):
            reserve(settings)

    def test_invalid_config_and_corrupt_store(self):
        with patch.dict(os.environ, {"SEARCH_MAX_CALLS_PER_DAY": "0"}), self.assertRaises(ValueError):
            Settings.from_env()
        self.settings.usage_db.write_text("broken database")
        with self.assertRaises(Exception):
            reserve(self.settings)


class HttpTests(Fixture):
    def test_mcp_roundtrip_and_guards(self):
        headers = {"Authorization": "Bearer " + TOKEN, "Accept": "application/json, text/event-stream"}
        with patch.dict(os.environ, {"MCP_ALLOWED_HOSTS": "testserver"}):
            app = create_http_app(create_server(self.settings))
        with self.api(), TestClient(app) as client:
            self.assertEqual(client.post("/mcp", json={}).status_code, 401)
            self.assertEqual(client.post("/mcp", headers={**headers, "Host": "evil.example"}, json={}).status_code, 421)
            self.assertEqual(client.post("/mcp", headers={**headers, "Origin": "https://evil.example"}, json={}).status_code, 403)
            init = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
            self.assertEqual(init.status_code, 200, init.text)
            headers["MCP-Protocol-Version"] = init.json()["result"]["protocolVersion"]
            listing = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
            tools = listing["result"]["tools"]
            self.assertEqual([t["name"] for t in tools], ["web_search"])
            size = tools[0]["inputSchema"]["properties"]["search_context_size"]
            self.assertEqual((size["enum"], size["default"]), (["low", "medium", "high"], "low"))
            result = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "web_search", "arguments": {"query": "test"}}}).json()["result"]
            self.assertFalse(result.get("isError"), result)
            self.assertEqual(json.loads(result["content"][0]["text"])["results"], [RESULT])
            oversized = client.post("/mcp", headers=headers, json={"data": "x" * 17000})
            self.assertEqual(oversized.status_code, 413)

    def test_missing_auth_configuration_rejected(self):
        with patch.dict(os.environ, {"WEBSEARCH_MCP_TOKEN": ""}), self.assertRaises(ValueError):
            create_http_app(create_server(self.settings))


if __name__ == "__main__":
    unittest.main()
