from datetime import date

from calc import make_plan, parse_days, parse_money, paydays, render_plan

TODAY = date(2026, 11, 20)  # пятница


def test_parse_money():
    assert parse_money("25 000") == 25000
    assert parse_money("25000₽") == 25000
    assert parse_money("25к") == 25000
    assert parse_money("1,5 тыс") == 1500
    assert parse_money("15k") == 15000
    assert parse_money("0") == 0
    assert parse_money("много") is None


def test_parse_days():
    assert parse_days("14", TODAY) == 14
    assert parse_days("2 недели", TODAY) == 14
    assert parse_days("25.11", TODAY) == 5
    assert parse_days("10.12.2026", TODAY) == 20
    assert parse_days("31.02", TODAY) is None
    assert parse_days("завтра", TODAY) is None


def test_paydays_fridays():
    d = paydays(TODAY, 14)  # с 21.11 по 03.12: пятница 27.11; до неё 7 дней -> первая выплата сегодня
    assert d == [TODAY, date(2026, 11, 27)]


def test_plan_regular():
    p = make_plan(balance=20000, days=14, mandatory=4000, save=0, today=TODAY)
    assert p.ok and p.payouts == 2 and p.weekly == 8000
    assert "8 000 ₽" in render_plan(p, TODAY)


def test_plan_not_enough():
    p = make_plan(balance=3000, days=14, mandatory=2500, save=1000, today=TODAY)
    assert not p.ok
    assert "Не хватает" in render_plan(p, TODAY)


def test_portrait_case():
    # аванс 25 000 на 25-е, до зарплаты 10-го — 15 дней, аренда половиной 11 000, отложить 6 000
    p = make_plan(balance=25000, days=15, mandatory=11000, save=6000, today=date(2026, 11, 25))
    assert p.ok and p.payouts == 2 and p.weekly == 4000
