import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
        if minimum <= value <= maximum:
            return value
    except ValueError:
        pass
    raise ValueError(f"{name} の設定範囲は {minimum}〜{maximum} です。")


@dataclass(frozen=True)
class Settings:
    model: str
    output_tokens: int
    usage_db: Path
    window_seconds: int
    calls_per_window: int
    calls_per_day: int
    tokens_per_day: int
    token_reservation: int
    timezone: ZoneInfo

    @classmethod
    def from_env(cls):
        return cls(
            os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
            integer("SEARCH_MAX_OUTPUT_TOKENS", 1200, 256, 4000),
            Path(os.environ.get("SEARCH_USAGE_DB", "/data/usage.sqlite3")),
            integer("SEARCH_WINDOW_SECONDS", 600, 1, 86400),
            integer("SEARCH_MAX_CALLS_PER_WINDOW", 3, 1, 1000),
            integer("SEARCH_MAX_CALLS_PER_DAY", 20, 1, 10000),
            integer("SEARCH_MAX_TOKENS_PER_DAY", 200000, 1000, 100_000_000),
            integer("SEARCH_TOKEN_RESERVATION", 12000, 1, 1_000_000),
            ZoneInfo(os.environ.get("SEARCH_BUDGET_TIMEZONE", "Asia/Tokyo")),
        )
