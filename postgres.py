from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, StorageKey
from psycopg import AsyncConnection, connect
from psycopg.types.json import Jsonb

from storage import SUMMARY_COLUMNS, StatsSummary

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bot_fsm (
        storage_key TEXT PRIMARY KEY,
        value JSONB NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS bot_events (
        bot_id BIGINT NOT NULL,
        ts TEXT NOT NULL,
        user_id BIGINT NOT NULL,
        event TEXT NOT NULL,
        value INTEGER,
        legacy_id BIGINT,
        UNIQUE (bot_id, legacy_id)
    )""",
    """CREATE TABLE IF NOT EXISTS bot_updates (
        bot_id BIGINT NOT NULL,
        update_id BIGINT NOT NULL,
        handled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (bot_id, update_id)
    )""",
    "CREATE INDEX IF NOT EXISTS bot_updates_expiry ON bot_updates (bot_id, handled_at)",
)


def prepare_database(dsn: str, bot_id: int, legacy_path: Path | None = None) -> int:
    with connect(dsn, connect_timeout=10) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (bot_id,))
        for statement in SCHEMA:
            conn.execute(statement)
        if legacy_path is None:
            return 0

        with closing(sqlite3.connect(f"{legacy_path.resolve().as_uri()}?mode=ro", uri=True)) as legacy:
            rows = legacy.execute("SELECT ts, user_id, event, value, rowid FROM events")
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO bot_events (bot_id, ts, user_id, event, value, legacy_id)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (bot_id, legacy_id) DO NOTHING""",
                    ((bot_id, *row) for row in rows),
                )
                return cursor.rowcount


# One connection and transaction per webhook request, shared by aiogram's FSM and stats.
request_connection: ContextVar[AsyncConnection] = ContextVar("request_connection")


async def claim_update(conn: AsyncConnection, bot_id: int, update_id: int) -> bool:
    await conn.execute("SELECT pg_advisory_xact_lock(%s)", (bot_id,))
    await conn.execute(
        "DELETE FROM bot_updates WHERE bot_id = %s AND handled_at < now() - interval '2 days'",
        (bot_id,),
    )
    cursor = await conn.execute(
        "INSERT INTO bot_updates (bot_id, update_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (bot_id, update_id),
    )
    return cursor.rowcount == 1


class PostgresStorage(BaseStorage):
    key_builder = DefaultKeyBuilder(with_bot_id=True, with_destiny=True)

    async def get(self, key: str) -> Any:
        cursor = await request_connection.get().execute("SELECT value FROM bot_fsm WHERE storage_key = %s", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set(self, key: str, value: Any) -> None:
        conn = request_connection.get()
        if value is None:
            await conn.execute("DELETE FROM bot_fsm WHERE storage_key = %s", (key,))
        else:
            await conn.execute(
                """INSERT INTO bot_fsm (storage_key, value) VALUES (%s, %s)
                   ON CONFLICT (storage_key) DO UPDATE SET value = excluded.value""",
                (key, Jsonb(value)),
            )

    async def get_state(self, key: StorageKey) -> str | None:
        return await self.get(self.key_builder.build(key, "state"))

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        await self.set(self.key_builder.build(key, "state"), state.state if isinstance(state, State) else state)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return await self.get(self.key_builder.build(key, "data")) or {}

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self.set(self.key_builder.build(key, "data"), dict(data) if data else None)

    async def close(self) -> None:
        pass


class PostgresStats:
    def __init__(self, conn: AsyncConnection, bot_id: int):
        self.conn = conn
        self.bot_id = bot_id

    async def log_event(self, user_id: int, event: str, value: int | None = None) -> None:
        await self.conn.execute(
            "INSERT INTO bot_events (bot_id, ts, user_id, event, value) VALUES (%s, %s, %s, %s, %s)",
            (self.bot_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id, event, value),
        )

    async def summary(self) -> StatsSummary:
        cursor = await self.conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM bot_events WHERE bot_id = %s",
            (self.bot_id,),
        )
        return await cursor.fetchone()
