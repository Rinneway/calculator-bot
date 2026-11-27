import asyncio
import hmac
import json
import logging
import os
from contextlib import suppress
from copy import deepcopy
from http.server import BaseHTTPRequestHandler
from time import time

from aiohttp import ClientSession, ClientTimeout

from aiogram.types import Update

from bot import TOKEN, make_bot, make_dispatcher
from blob_storage import BlobConflict, BlobDialogStorage, BlobStore, request_dialogs
from storage import EventStats

logger = logging.getLogger(__name__)
dispatcher = make_dispatcher(storage=BlobDialogStorage())


async def process_update(blob_token: str, update: dict) -> None:
    async with make_bot() as bot, ClientSession(timeout=ClientTimeout(total=6)) as session:
        store = BlobStore(session, blob_token, bot.id)
        saved, etag = await store.load()
        now = time()
        update_id = str(update["update_id"])
        if saved["updates"].get(update_id, 0) > now - 172800:
            return
        if saved["lease_until"] > now:
            raise BlobConflict("A Telegram update is in progress")

        # Reserve the document before making Telegram calls. The lease outlasts maxDuration.
        lease_etag = await store.save({**saved, "lease_until": now + 60}, etag)
        working = deepcopy(saved)
        working["lease_until"] = 0
        working["updates"] = {key: ts for key, ts in saved["updates"].items() if ts > now - 172800}
        context_token = request_dialogs.set(working["dialogs"])
        try:
            event = Update.model_validate(update, context={"bot": bot})
            await dispatcher.feed_update(bot, event, stats_store=EventStats(working["events"]))
        except Exception:
            with suppress(Exception):
                await store.save({**saved, "lease_until": 0}, lease_etag)
            raise
        finally:
            request_dialogs.reset(context_token)

        working["updates"][update_id] = time()
        await store.save(working, lease_etag)


class handler(BaseHTTPRequestHandler):
    def respond(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def do_GET(self) -> None:
        self.respond(200, "Telegram webhook")

    def do_POST(self) -> None:
        secret = os.getenv("WEBHOOK_SECRET")
        blob_token = os.getenv("BLOB_READ_WRITE_TOKEN")
        if not (TOKEN and secret and blob_token):
            self.respond(503, "Webhook is not configured")
            return

        header = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(header.encode(), secret.encode()):
            self.respond(403, "Forbidden")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValueError
            update = json.loads(self.rfile.read(length))
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                raise ValueError
        except ValueError:
            self.respond(400, "Invalid Telegram update")
            return

        try:
            asyncio.run(process_update(blob_token, update))
        except BlobConflict:
            self.respond(503, "Update in progress")
            return
        except Exception as exc:
            logger.error("Telegram update %d failed (%s)", update["update_id"], type(exc).__name__)
            self.respond(500, "Update failed")
            return
        self.respond(200, "OK")
