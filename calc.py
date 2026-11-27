"""Расчёт самозарплаты — без Telegram, чтобы логику можно было проверить тестами."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta

PAYDAY_WEEKDAY = 4  # пятница (понедельник = 0)


def parse_money(text: str) -> int | None:
    """'25 000', '25000₽', '25к', '1,5 тыс', '15k' -> рубли. None, если не число."""
    t = text.strip().lower().replace(" ", " ").replace("₽", "").replace("руб", "").replace("р.", "")
    mult = 1
    m = re.search(r"(к|k|тыс\.?|тысяч[аи]?)\s*$", t)
    if m:
        mult = 1000
        t = t[: m.start()]
    t = t.replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    value = float(t) * mult
    if value > 10_000_000:
        return None
    return int(round(value))


def parse_days(text: str, today: date) -> int | None:
    """'14' -> 14 дней; '25.12' или '25.12.2026' -> дней до даты; '2 недели' -> 14."""
    t = text.strip().lower()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        if y < 100:
            y += 2000
        try:
            target = date(y, mo, d)
        except ValueError:
            return None
        if target <= today and not m.group(3):
            target = date(y + 1, mo, d)
        days = (target - today).days
        return days if 1 <= days <= 120 else None
    m = re.fullmatch(r"(\d{1,3})\s*(д|дн|дней|дня|день)?", t)
    if m:
        days = int(m.group(1))
        return days if 1 <= days <= 120 else None
    m = re.fullmatch(r"(\d{1,2})\s*(нед|недел[ьяи]|w)\w*", t)
    if m:
        days = int(m.group(1)) * 7
        return days if 1 <= days <= 120 else None
    return None


def paydays(today: date, days: int) -> list[date]:
    """Пятницы с завтрашнего дня до дня поступления (не включая его).
    Первая выплата — сразу при включении, если до пятницы больше 2 дней."""
    end = today + timedelta(days=days)
    out = []
    d = today + timedelta(days=1)
    while d < end:
        if d.weekday() == PAYDAY_WEEKDAY:
            out.append(d)
        d += timedelta(days=1)
    first_friday = out[0] if out else end
    if (first_friday - today).days > 2:
        out.insert(0, today)
    return out or [today]


@dataclass
class Plan:
    balance: int
    days: int
    mandatory: int
    save: int
    free: int            # остаётся на жизнь
    payouts: int         # число выплат
    weekly: int          # самозарплата за одну выплату
    daily: int           # ориентир в день
    dates: list[date]
    ok: bool             # хватает ли денег на обязательное и отложенное


def make_plan(balance: int, days: int, mandatory: int, save: int, today: date) -> Plan:
    free = balance - mandatory - save
    dates = paydays(today, days)
    payouts = len(dates)
    if free <= 0:
        return Plan(balance, days, mandatory, save, free, payouts, 0, 0, dates, False)
    weekly = int(math.floor(free / payouts / 50) * 50)   # округляем вниз до 50 ₽
    daily = int(math.floor(free / days / 10) * 10)
    return Plan(balance, days, mandatory, save, free, payouts, weekly, daily, dates, True)


def rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


RU_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
             "сентября", "октября", "ноября", "декабря"]


def human_date(d: date, today: date) -> str:
    if d == today:
        return "сегодня"
    return f"{d.day} {RU_MONTHS[d.month - 1]}"


def render_plan(p: Plan, today: date) -> str:
    if not p.ok:
        short = -p.free
        lines = [
            "<b>Пока не сходится.</b>",
            f"На обязательное и накопления нужно {rub(p.mandatory + p.save)}, а у тебя {rub(p.balance)}.",
            f"Не хватает {rub(short)}.",
            "",
            "Что можно сделать:",
            "• уменьшить сумму, которую хочешь отложить;",
            "• проверить, все ли обязательные платежи нужно закрыть до следующего поступления.",
        ]
        return "\n".join(lines)
    sched = ", ".join(human_date(d, today) for d in p.dates[:6])
    if len(p.dates) > 6:
        sched += " и далее"
    left = p.free - p.weekly * p.payouts
    lines = [
        f"<b>Твоя самозарплата: {rub(p.weekly)}</b> {'в неделю' if p.payouts > 1 else 'до поступления'}",
        f"Это примерно {rub(p.daily)} в день.",
        "",
        f"Выплат до следующего поступления: {p.payouts} ({sched}).",
        f"Сначала обязательное: {rub(p.mandatory)}.",
    ]
    if p.save:
        lines.append(f"Отложено в резерв: {rub(p.save)}.")
    if left > 0:
        lines.append(f"Остаток после округления: {rub(left)} — пусть лежит в резерве.")
    lines += [
        "",
        "Именно так будет работать карта «Свой резерв»: все поступления идут в Резерв "
        "с процентами каждый день, а на карту каждую пятницу приходит одинаковая сумма.",
    ]
    return "\n".join(lines)
