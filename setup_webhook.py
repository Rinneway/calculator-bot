import argparse
import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout

from blob_storage import BlobStore
from bot import TOKEN, apply_branding, make_bot
from storage import read_events


async def register(url: str, secret: str, blob_token: str, stats_path: Path | None) -> None:
    events = read_events(stats_path) if stats_path else []
    async with make_bot() as bot, ClientSession(timeout=ClientTimeout(total=10)) as session:
        imported = await BlobStore(session, blob_token, bot.id).initialize(events)
        print(f"Хранилище Vercel Blob готово. Импортировано событий: {imported}.")
        await bot.set_webhook(
            url=url,
            secret_token=secret,
            allowed_updates=["message", "callback_query"],
            max_connections=1,
        )
        print(f"Telegram отправляет обновления на {url}")
        await apply_branding(bot)


def main() -> None:
    parser = argparse.ArgumentParser(description="Настроить Telegram webhook и Vercel Blob.")
    parser.add_argument("domain", help="Production-домен Vercel, например https://my-bot.vercel.app")
    parser.add_argument("--import-stats", type=Path, metavar="JSON_FILE")
    args = parser.parse_args()

    domain = urlsplit(args.domain)
    if (
        domain.scheme != "https" or not domain.hostname or domain.username is not None
        or domain.path not in ("", "/") or domain.query or domain.fragment
    ):
        parser.error("Укажите HTTPS-домен проекта без пути, параметров или учётных данных.")

    secret = os.getenv("WEBHOOK_SECRET", "")
    blob_token = os.getenv("BLOB_READ_WRITE_TOKEN", "")
    settings = {"BOT_TOKEN": TOKEN, "BLOB_READ_WRITE_TOKEN": blob_token, "WEBHOOK_SECRET": secret}
    missing = [name for name, value in settings.items() if not value.strip()]
    if missing:
        parser.error(f"Добавьте в локальный .env: {', '.join(missing)}.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        parser.error("WEBHOOK_SECRET должен содержать от 1 до 256 символов A–Z, a–z, 0–9, _ или -.")
    if args.import_stats is not None and not args.import_stats.is_file():
        parser.error(f"Файл статистики не найден: {args.import_stats}.")

    asyncio.run(register(f"https://{domain.netloc}/api/telegram", secret, blob_token, args.import_stats))


if __name__ == "__main__":
    main()
