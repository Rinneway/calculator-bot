import asyncio
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler

from aiogram.types import Update
from psycopg import AsyncConnection

from bot import TOKEN, make_bot, make_dispatcher
from postgres import PostgresStats, PostgresStorage, claim_update, request_connection

logger = logging.getLogger(__name__)
dispatcher = make_dispatcher(storage=PostgresStorage())


async def process_update(dsn: str, update: dict) -> None:
    async with make_bot() as bot, await AsyncConnection.connect(dsn, connect_timeout=10) as conn:
        if not await claim_update(conn, bot.id, update["update_id"]):
            return

        context_token = request_connection.set(conn)
        try:
            event = Update.model_validate(update, context={"bot": bot})
            await dispatcher.feed_update(bot, event, stats_store=PostgresStats(conn, bot.id))
        finally:
            request_connection.reset(context_token)


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
        dsn = os.getenv("DATABASE_URL")
        if not (TOKEN and secret and dsn):
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
            asyncio.run(process_update(dsn, update), loop_factory=asyncio.SelectorEventLoop)
        except Exception as exc:
            logger.error("Telegram update %d failed (%s)", update["update_id"], type(exc).__name__)
            self.respond(500, "Update failed")
            return
        self.respond(200, "OK")
