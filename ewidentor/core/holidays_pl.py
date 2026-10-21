from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta


def easter_sunday(year: int) -> date:
    """Algorytm Meeusa/Jonesa/Butchera (kalendarz gregoriański)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def polish_public_holidays(year: int) -> set[date]:
    """Dni ustawowo wolne od pracy (PL), w tym ruchome."""
    easter = easter_sunday(year)
    holidays = {
        date(year, 1, 1),  # Nowy Rok
        date(year, 1, 6),  # Trzech Króli
        easter,  # Niedziela Wielkanocna
        easter + timedelta(days=1),  # Poniedziałek Wielkanocny
        date(year, 5, 1),  # Święto Pracy
        date(year, 5, 3),  # 3 Maja
        easter + timedelta(days=49),  # Zielone Świątki (niedziela)
        easter + timedelta(days=60),  # Boże Ciało
        date(year, 8, 15),  # Wniebowzięcie NMP / Wojska Polskiego
        date(year, 11, 1),  # Wszystkich Świętych
        date(year, 11, 11),  # Niepodległość
        date(year, 12, 25),  # Boże Narodzenie
        date(year, 12, 26),  # 2. dzień BN
    }
    # Wigilia jako dzień wolny od 2025
    if year >= 2025:
        holidays.add(date(year, 12, 24))
    return holidays


def is_polish_holiday(day: date) -> bool:
    return day in polish_public_holidays(day.year)


def is_sunday_or_polish_holiday(day: date) -> bool:
    return day.weekday() == 6 or is_polish_holiday(day)


def holiday_name_pl(day: date) -> str | None:
    if not is_polish_holiday(day):
        if day.weekday() == 6:
            return "niedziela"
        return None
    easter = easter_sunday(day.year)
    fixed = {
        (1, 1): "Nowy Rok",
        (1, 6): "Święto Trzech Króli",
        (5, 1): "Święto Pracy",
        (5, 3): "Święto Konstytucji 3 Maja",
        (8, 15): "Wniebowzięcie NMP",
        (11, 1): "Wszystkich Świętych",
        (11, 11): "Święto Niepodległości",
        (12, 24): "Wigilia",
        (12, 25): "Boże Narodzenie",
        (12, 26): "2. dzień Bożego Narodzenia",
    }
    name = fixed.get((day.month, day.day))
    if name:
        return name
    if day == easter:
        return "Wielkanoc"
    if day == easter + timedelta(days=1):
        return "Poniedziałek Wielkanocny"
    if day == easter + timedelta(days=49):
        return "Zielone Świątki"
    if day == easter + timedelta(days=60):
        return "Boże Ciało"
    return "Święto"


def norma_kp_minutes(
    year: int,
    month: int,
    *,
    employment_fraction: float = 1.0,
) -> tuple[int, dict]:
    """
    Wymiar czasu pracy wg art. 130 § 1 KP dla miesiąca kalendarzowego.
    Zwraca (minuty, meta do raportu: tygodnie, dni_reszta, święta).
    """
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    days_count = (last - first).days + 1
    weeks = days_count // 7
    rem_start = first + timedelta(days=weeks * 7)
    weekday_rem = 0
    d = rem_start
    while d <= last:
        if d.weekday() < 5:
            weekday_rem += 1
        d += timedelta(days=1)

    holidays = polish_public_holidays(year)
    holiday_cuts = 0
    d = first
    while d <= last:
        # święto w dniu innym niż niedziela obniża wymiar o 8 h
        if d in holidays and d.weekday() != 6:
            holiday_cuts += 1
        d += timedelta(days=1)

    hours = weeks * 40 + weekday_rem * 8 - holiday_cuts * 8
    hours = max(0, hours) * employment_fraction
    meta = {
        "weeks": weeks,
        "weekday_remainder": weekday_rem,
        "holiday_cuts": holiday_cuts,
        "hours": hours,
        "formula": (
            f"({weeks} tyg. × 40,00h) + ({weekday_rem} dni × 8,00h) "
            f"- ({holiday_cuts} świąt × 8,00h) = {hours:.2f}h"
        ),
    }
    return int(round(hours * 60)), meta
