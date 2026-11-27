from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar
from time import time
from typing import Any, TypedDict
from uuid import uuid4

from aiohttp import ClientSession
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, StorageKey

from storage import Event

# API version and conditional upload headers follow Vercel's @vercel/blob 2.6.1.
BLOB_API = "https://vercel.com/api/blob/"


class BlobConflict(Exception):
    pass


class BotDocument(TypedDict):
    dialogs: dict[str, Any]
    events: list[Event]
    updates: dict[str, float]
    lease_until: float
    imported_stats: bool


class BlobStore:
    def __init__(self, session: ClientSession, token: str, bot_id: int):
        match = re.fullmatch(r"vercel_blob_rw_([A-Za-z0-9]+)_\S+", token)
        if match is None:
            raise ValueError("Invalid BLOB_READ_WRITE_TOKEN format")
        self.session = session
        self.store_id = match[1]
        self.path = f"telegram/{bot_id}/state.json"
        self.url = f"https://{self.store_id}.private.blob.vercel-storage.com/{self.path}"
        self.auth = {"Authorization": f"Bearer {token}"}

    async def load(self) -> tuple[BotDocument, str | None]:
        # Bypass the CDN and keep the origin ETag suitable for a conditional write.
        headers = {**self.auth, "Accept-Encoding": "identity"}
        async with self.session.get(self.url, params={"cache": "0"}, headers=headers, allow_redirects=False) as response:
            if response.status == 404:
                return BotDocument(dialogs={}, events=[], updates={}, lease_until=0, imported_stats=False), None
            response.raise_for_status()
            etag = response.headers["ETag"]
            if etag.startswith("W/"):
                raise BlobConflict("Blob read returned a weak ETag")
            return await response.json(content_type=None), etag

    async def save(self, document: BotDocument, etag: str | None) -> str:
        headers = {
            **self.auth,
            "x-api-version": "12",
            "x-api-blob-request-id": f"{self.store_id}:{uuid4().hex}",
            "x-vercel-blob-store-id": self.store_id,
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1" if etag else "0",
            "x-content-type": "application/json",
            "x-cache-control-max-age": "60",
        }
        if etag is not None:
            headers["x-if-match"] = etag
        async with self.session.put(
            BLOB_API, params={"pathname": self.path}, json=document, headers=headers, allow_redirects=False,
        ) as response:
            if response.status in (409, 412):
                raise BlobConflict(f"Blob write rejected its precondition (HTTP {response.status})")
            response.raise_for_status()
            return (await response.json(content_type=None))["etag"]

    async def initialize(self, events: list[Event]) -> int:
        document, etag = await self.load()
        if etag is not None and (not events or document["imported_stats"]):
            return 0
        if document["lease_until"] > time():
            raise BlobConflict("A Telegram update is in progress")
        document["events"].extend(events)
        document["imported_stats"] = bool(events)
        await self.save(document, etag)
        return len(events)


request_dialogs: ContextVar[dict[str, Any]] = ContextVar("request_dialogs")


class BlobDialogStorage(BaseStorage):
    key_builder = DefaultKeyBuilder(with_bot_id=True, with_destiny=True)

    async def get_state(self, key: StorageKey) -> str | None:
        return request_dialogs.get().get(self.key_builder.build(key, "state"))

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        name = self.key_builder.build(key, "state")
        dialogs = request_dialogs.get()
        if state is None:
            dialogs.pop(name, None)
        else:
            dialogs[name] = state.state if isinstance(state, State) else state

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return dict(request_dialogs.get().get(self.key_builder.build(key, "data"), {}))

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        name = self.key_builder.build(key, "data")
        dialogs = request_dialogs.get()
        if data:
            dialogs[name] = dict(data)
        else:
            dialogs.pop(name, None)

    async def close(self) -> None:
        pass
