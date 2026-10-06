"""API 呼び出し前の予算予約と、応答後の実使用量記録（SQLite）。"""
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class BudgetExceeded(Exception):
    pass


def positive_int(name, default):
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} は1以上の整数で指定してください。")
    return value


class UsageLimits:
    def __init__(self):
        self.window = positive_int("SEARCH_WINDOW_SECONDS", 600)
        self.window_calls = positive_int("SEARCH_MAX_CALLS_PER_WINDOW", 3)
        self.daily_calls = positive_int("SEARCH_MAX_CALLS_PER_DAY", 20)
        self.daily_tokens = positive_int("SEARCH_MAX_TOKENS_PER_DAY", 200000)
        self.reserve_tokens = positive_int("SEARCH_TOKEN_RESERVATION", 12000)
        self.timezone = ZoneInfo(os.environ.get("SEARCH_BUDGET_TIMEZONE", "Asia/Tokyo"))
        self.path = Path(os.environ.get("SEARCH_USAGE_DB", ".usage/usage.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("""CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY, started REAL NOT NULL, day TEXT NOT NULL,
            tokens INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0
        )""")
        return db

    def reserve(self):
        now = time.time()
        day = datetime.fromtimestamp(now, self.timezone).date().isoformat()
        db = self.connect()
        try:
            # 複数リクエスト・プロセスが同時に来ても、確認と予約を一体で実行。
            db.execute("BEGIN IMMEDIATE")
            recent = db.execute("SELECT count(*) FROM requests WHERE started > ?", (now-self.window,)).fetchone()[0]
            count, tokens = db.execute(
                "SELECT count(*), coalesce(sum(tokens), 0) FROM requests WHERE day = ?", (day,)
            ).fetchone()
            if recent >= self.window_calls:
                raise BudgetExceeded(f"検索回数の上限（{self.window}秒間に{self.window_calls}回）です。")
            if count >= self.daily_calls:
                raise BudgetExceeded(f"本日の検索回数の上限（{self.daily_calls}回）です。")
            if tokens + self.reserve_tokens > self.daily_tokens:
                raise BudgetExceeded("本日のトークン予算が不足しています（処理中・使用量不明分の予約を含みます）。")
            request_id = db.execute(
                "INSERT INTO requests(started, day, tokens) VALUES (?, ?, ?)",
                (now, day, self.reserve_tokens),
            ).lastrowid
            db.commit()
            return request_id
        finally:
            db.close()

    def settle(self, request_id, tokens):
        if tokens < 0:
            raise ValueError("使用トークン数が不正です。")
        db = self.connect()
        try:
            with db:
                db.execute("UPDATE requests SET tokens = ?, settled = 1 WHERE id = ?", (tokens, request_id))
        finally:
            db.close()
