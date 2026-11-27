import argparse
import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from bot import TOKEN, apply_branding, make_bot
from postgres import prepare_database


async def register(url: str, secret: str, dsn: str, legacy_path: Path | None) -> None:
    async with make_bot() as bot:
        imported = await asyncio.to_thread(prepare_database, dsn, bot.id, legacy_path)
        print(f"PostgreSQL готова. Импортировано событий: {imported}.")
        await bot.set_webhook(
            url=url,
            secret_token=secret,
            allowed_updates=["message", "callback_query"],
            max_connections=1,
        )
        print(f"Telegram принимает обновления через {url}")
        await apply_branding(bot)


def main() -> None:
    parser = argparse.ArgumentParser(description="Настроить Telegram webhook и создать таблицы PostgreSQL.")
    parser.add_argument("domain", help="Production-домен Vercel, например https://my-bot.vercel.app")
    parser.add_argument("--import-stats", type=Path, metavar="SQLITE_FILE")
    args = parser.parse_args()

    domain = urlsplit(args.domain)
    if (
        domain.scheme != "https" or not domain.hostname or domain.username is not None
        or domain.path not in ("", "/") or domain.query or domain.fragment
    ):
        parser.error("Укажите HTTPS-домен проекта без пути, параметров или учётных данных.")

    secret = os.getenv("WEBHOOK_SECRET", "")
    dsn = os.getenv("DATABASE_URL", "")
    if not TOKEN or not dsn or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        parser.error("Проверьте BOT_TOKEN, DATABASE_URL и WEBHOOK_SECRET в .env.")

    asyncio.run(register(f"https://{domain.netloc}/api/telegram", secret, dsn, args.import_stats))


if __name__ == "__main__":
    main()
