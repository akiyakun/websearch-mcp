"""Persistent atomic call/token reservation. Failures still count; no automatic retry."""
import sqlite3
import time
from contextlib import closing
from datetime import datetime

from .config import Settings


class BudgetError(ValueError):
    pass


def connect(settings: Settings) -> sqlite3.Connection:
    db = sqlite3.connect(settings.usage_db, timeout=10)
    try:
        # Same schema as 1.0.0-alpha so an existing usage volume keeps its history.
        db.execute("""CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY, started REAL NOT NULL, day TEXT NOT NULL,
            tokens INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0
        )""")
    except Exception:
        db.close()
        raise
    return db


def reserve(settings: Settings) -> int:
    now = time.time()
    day = datetime.fromtimestamp(now, settings.timezone).date().isoformat()
    with closing(connect(settings)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        recent = db.execute("SELECT count(*) FROM requests WHERE started > ?",
                            (now - settings.window_seconds,)).fetchone()[0]
        count, tokens = db.execute(
            "SELECT count(*), coalesce(sum(tokens), 0) FROM requests WHERE day = ?", (day,)
        ).fetchone()
        if recent >= settings.calls_per_window:
            raise BudgetError(f"検索回数の上限（{settings.window_seconds}秒間に{settings.calls_per_window}回）です。")
        if count >= settings.calls_per_day:
            raise BudgetError(f"本日の検索回数の上限（{settings.calls_per_day}回）です。")
        if tokens + settings.token_reservation > settings.tokens_per_day:
            raise BudgetError("本日のトークン予算が不足しています（処理中・使用量不明分の予約を含みます）。")
        return db.execute("INSERT INTO requests(started, day, tokens) VALUES (?, ?, ?)",
                          (now, day, settings.token_reservation)).lastrowid


def settle(settings: Settings, request_id: int, tokens: int) -> None:
    if tokens < 0:
        raise ValueError("使用トークン数が不正です。")
    with closing(connect(settings)) as db, db:
        db.execute("UPDATE requests SET tokens = ?, settled = 1 WHERE id = ?", (tokens, request_id))
