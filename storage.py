from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from typing import Protocol

StatsSummary = tuple[int, int, int, int, float | Decimal | None]

SUMMARY_COLUMNS = """
    COUNT(DISTINCT user_id) FILTER (WHERE event = 'start'),
    COUNT(DISTINCT user_id) FILTER (WHERE event = 'plan'),
    COUNT(DISTINCT user_id) FILTER (WHERE event = 'want_card'),
    COUNT(DISTINCT user_id) FILTER (WHERE event = 'survey'),
    AVG(value) FILTER (WHERE event = 'plan' AND value > 0)
"""


class StatsStore(Protocol):
    async def log_event(self, user_id: int, event: str, value: int | None = None) -> None: ...
    async def summary(self) -> StatsSummary: ...


class SQLiteStats:
    def __init__(self, path: str):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE IF NOT EXISTS events (ts TEXT, user_id INTEGER, event TEXT, value INTEGER)")
        return conn

    async def log_event(self, user_id: int, event: str, value: int | None = None) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), user_id, event, value),
            )

    async def summary(self) -> StatsSummary:
        with closing(self.connect()) as conn:
            return conn.execute(f"SELECT {SUMMARY_COLUMNS} FROM events").fetchone()
