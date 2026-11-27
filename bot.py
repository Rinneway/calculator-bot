"""Telegram-бот «Калькулятор самозарплаты» для кейса «Свой резерв» (команда SND).

Запуск:  BOT_TOKEN=... SURVEY_URL=... ADMIN_IDS=123,456 python bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import branding
from calc import make_plan, parse_days, parse_money, render_plan

try:  # .env рядом с ботом (необязательно)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("BOT_TOKEN", "")
SURVEY_URL = os.getenv("SURVEY_URL", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.getenv("DB_PATH", "stats.db")

router = Router()


# ---------- статистика (для слайда и защиты) ----------
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS events (ts TEXT, user_id INTEGER, event TEXT, value INTEGER)")
    return con


def log_event(user_id: int, event: str, value: int | None = None) -> None:
    with db() as con:
        con.execute("INSERT INTO events VALUES (?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), user_id, event, value))


def stats_text() -> str:
    with db() as con:
        def users(ev: str) -> int:
            return con.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE event=?", (ev,)).fetchone()[0]
        started, done, want, survey = users("start"), users("plan"), users("want_card"), users("survey")
        avg = con.execute("SELECT AVG(value) FROM events WHERE event='plan' AND value>0").fetchone()[0]
    conv = lambda a, b: f"{a / b * 100:.0f}%" if b else "—"
    return (
        "<b>Статистика бота</b>\n"
        f"Открыли бота: {started}\n"
        f"Получили расчёт: {done} ({conv(done, started)})\n"
        f"Нажали «Хочу такую карту»: {want} ({conv(want, done)} от получивших расчёт)\n"
        f"Перешли к опросу: {survey}\n"
        f"Средняя самозарплата: {int(avg or 0):,} ₽".replace(",", " ")
    )


# ---------- диалог ----------
class Calc(StatesGroup):
    balance = State()
    days = State()
    mandatory = State()
    save = State()


def kb(*rows: list[tuple[str, str]]):
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*[InlineKeyboardButton(text=t, callback_data=d) for t, d in row])
    return b.as_markup()


START_TEXT = (
    "Привет! Я калькулятор <b>самозарплаты</b>.\n\n"
    "Когда деньги приходят неровно — стипендия, аванс, переводы, оплата за заказ, — "
    "сложно понять, сколько можно тратить. Я посчитаю одинаковую сумму на каждую неделю, "
    "чтобы денег хватило до следующего поступления.\n\n"
    "4 вопроса, меньше минуты."
)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    log_event(m.from_user.id, "start")
    await m.answer(START_TEXT, reply_markup=kb([("Посчитать", "go")]))


@router.callback_query(F.data == "go")
@router.callback_query(F.data == "again")
async def ask_balance(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Calc.balance)
    await c.message.answer("1/4. <b>Сколько у тебя сейчас денег?</b>\nВсе карты и наличные. Например: 18 000 или 18к")
    await c.answer()


@router.message(Calc.balance)
async def got_balance(m: Message, state: FSMContext):
    v = parse_money(m.text or "")
    if v is None or v <= 0:
        await m.answer("Не понял сумму. Напиши числом, например 18 000 или 18к")
        return
    await state.update_data(balance=v)
    await state.set_state(Calc.days)
    await m.answer(
        "2/4. <b>Через сколько дней следующее крупное поступление?</b>\n"
        "Стипендия, зарплата или аванс. Выбери или напиши дату (например 25.12)",
        reply_markup=kb([("1 неделя", "d:7"), ("2 недели", "d:14")], [("3 недели", "d:21"), ("месяц", "d:30")]),
    )


async def set_days(target: Message, state: FSMContext, days: int):
    await state.update_data(days=days)
    await state.set_state(Calc.mandatory)
    await target.answer(
        "3/4. <b>Сколько обязательных платежей нужно заплатить до этого дня?</b>\n"
        "Аренда или общежитие, связь, проезд. Если нет — жми «0»",
        reply_markup=kb([("0", "m:0")]),
    )


@router.callback_query(Calc.days, F.data.startswith("d:"))
async def got_days_btn(c: CallbackQuery, state: FSMContext):
    await set_days(c.message, state, int(c.data[2:]))
    await c.answer()


@router.message(Calc.days)
async def got_days(m: Message, state: FSMContext):
    d = parse_days(m.text or "", date.today())
    if d is None:
        await m.answer("Не понял. Напиши число дней (например 14) или дату (например 25.12)")
        return
    await set_days(m, state, d)


async def set_mandatory(target: Message, state: FSMContext, v: int):
    await state.update_data(mandatory=v)
    await state.set_state(Calc.save)
    await target.answer(
        "4/4. <b>Сколько хочешь отложить до следующего поступления?</b>\nВ процентах от денег или суммой",
        reply_markup=kb([("Ничего", "s:0"), ("10%", "s:p10"), ("20%", "s:p20")]),
    )


@router.callback_query(Calc.mandatory, F.data.startswith("m:"))
async def got_mand_btn(c: CallbackQuery, state: FSMContext):
    await set_mandatory(c.message, state, int(c.data[2:]))
    await c.answer()


@router.message(Calc.mandatory)
async def got_mand(m: Message, state: FSMContext):
    v = parse_money(m.text or "")
    if v is None:
        await m.answer("Напиши сумму числом, например 2 500. Если платежей нет — 0")
        return
    await set_mandatory(m, state, v)


async def finish(target: Message, state: FSMContext, user_id: int, save: int):
    data = await state.get_data()
    today = date.today()
    plan = make_plan(data["balance"], data["days"], data["mandatory"], save, today)
    await state.clear()
    log_event(user_id, "plan", plan.weekly)
    await target.answer(render_plan(plan, today), reply_markup=kb(
        [("Хочу такую карту", "want")],
        [("Пройти опрос — 2 минуты", "survey")],
        [("Посчитать заново", "again")],
    ))


@router.callback_query(Calc.save, F.data.startswith("s:"))
async def got_save_btn(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    raw = c.data[2:]
    save = int(data["balance"] * int(raw[1:]) / 100) if raw.startswith("p") else int(raw)
    await finish(c.message, state, c.from_user.id, save)
    await c.answer()


@router.message(Calc.save)
async def got_save(m: Message, state: FSMContext):
    t = (m.text or "").strip()
    data = await state.get_data()
    if t.endswith("%") and t[:-1].strip().isdigit():
        save = int(data["balance"] * int(t[:-1]) / 100)
    else:
        save = parse_money(t)
        if save is None:
            await m.answer("Напиши сумму (например 3 000) или процент (например 15%)")
            return
    await finish(m, state, m.from_user.id, save)


@router.callback_query(F.data == "want")
async def want(c: CallbackQuery):
    log_event(c.from_user.id, "want_card")
    await c.message.answer(
        "Спасибо! «Свой резерв» — пока концепт команды SND для кейс-чемпионата ВТБ, оформить карту нельзя. "
        "Но твой голос мы посчитали — он пойдёт в защиту проекта."
    )
    await c.answer("Голос учтён")


@router.callback_query(F.data == "survey")
async def survey(c: CallbackQuery):
    log_event(c.from_user.id, "survey")
    if SURVEY_URL:
        b = InlineKeyboardBuilder()
        b.button(text="Открыть опрос", url=SURVEY_URL)
        await c.message.answer("Опрос анонимный, 11 вопросов. Честное «не нужно» тоже помогает!", reply_markup=b.as_markup())
    else:
        await c.message.answer("Ссылка на опрос скоро появится.")
    await c.answer()


@router.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id in ADMIN_IDS:
        await m.answer(stats_text())


@router.message(Command("help"))
async def help_(m: Message):
    await m.answer(branding.HELP)


async def apply_branding(bot: Bot) -> None:
    """Название, описания и меню команд — через Bot API, чтобы не вводить вручную."""
    steps = [
        bot.set_my_commands([BotCommand(command=c, description=d) for c, d in branding.COMMANDS]),
        bot.set_my_short_description(short_description=branding.SHORT_DESCRIPTION),
        bot.set_my_description(description=branding.DESCRIPTION),
        bot.set_my_name(name=branding.NAME),
    ]
    for step in steps:
        try:
            await step
        except Exception as e:  # например, лимит на частую смену имени — не мешает работе
            logging.warning("Оформление: %s", e)


async def main():
    if not TOKEN:
        raise SystemExit("Нет BOT_TOKEN. Получите токен у @BotFather и запишите в .env")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await apply_branding(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
