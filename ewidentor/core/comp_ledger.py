from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ewidentor.core.holidays_pl import holiday_name_pl, is_polish_holiday, is_sunday_or_polish_holiday
from ewidentor.core.models import DayCard, MonthCard


class CompKind(str, Enum):
    """Rodzaj oddawania czasu (jak na wzorcu Informacje szczegółowe)."""

    OVERTIME_XB = "xb"  # nadgodziny oddane
    SUNDAY_HOLIDAY_WN = "wn"  # wolne za niedzielę / święto / dzień wolny
    DUTY_100_OFF = "duty100"  # wolne za dyżur 100%


COMP_LABELS_PL: dict[CompKind, str] = {
    CompKind.OVERTIME_XB: "Godziny nadliczbowe oddane (Xb)",
    CompKind.SUNDAY_HOLIDAY_WN: "Wolne za niedzielę/święto (Wn)",
    CompKind.DUTY_100_OFF: "Wolne za dyżur 100%",
}


@dataclass(slots=True)
class CompEntry:
    id: int | None
    driver_id: int
    kind: CompKind
    earn_day: date  # dzień powstania długu (praca w święto / nadgodziny)
    settle_day: date | None  # dzień oddania; None = nieoddane
    minutes: int = 0  # dla Xb/dyżur; dla Wn święta zwykle 0 (= cały dzień)
    rate_percent: int = 100  # 50 / 100 dla nadgodzin
    title: str = ""
    note: str = ""
    paid_out: bool = False  # True = wypłacono 100% zamiast oddania


@dataclass(slots=True)
class CompDebt:
    """Dług wykryty automatycznie z karty (jeszcze bez wpisu settle)."""

    earn_day: date
    kind: CompKind
    minutes: int
    rate_percent: int
    title: str


def detect_holiday_wn_debts(card: MonthCard) -> list[CompDebt]:
    """
    Święto/niedziela z czasem pracy → dług Wn (cały dzień).
    Nie tworzy wpisu w bazie — tylko propozycje.
    """
    out: list[CompDebt] = []
    for d in card.days:
        if d.work_min <= 0 and d.duty_50_min <= 0 and d.duty_100_min <= 0:
            continue
        if not is_sunday_or_polish_holiday(d.day):
            continue
        name = holiday_name_pl(d.day) or ("niedziela" if d.day.weekday() == 6 else "święto")
        out.append(
            CompDebt(
                earn_day=d.day,
                kind=CompKind.SUNDAY_HOLIDAY_WN,
                minutes=0,  # cały dzień wolny
                rate_percent=100,
                title=f"Praca w dniu: {name}",
            )
        )
    return out


def unreturned_sunday_holiday_minutes(
    card: MonthCard,
    entries: list[CompEntry],
) -> int:
    """
    Godziny nd/św do wypłaty 100% (art. 151¹¹ § 3 KP):
    praca w nd/św minus dni Wn już oddane wolnym.
    Wypłata 100% (paid_out) pozostaje w sumie — to właśnie dodatek.
    """
    returned_earn_days = {
        e.earn_day
        for e in entries
        if e.kind == CompKind.SUNDAY_HOLIDAY_WN
        and e.settle_day is not None
        and not e.paid_out
    }
    total = 0
    for d in card.days:
        if d.sunday_holiday_min <= 0:
            continue
        if d.day in returned_earn_days:
            continue
        total += d.sunday_holiday_min
    return total


def ensure_open_wn_entries(
    existing: list[CompEntry],
    debts: list[CompDebt],
    *,
    driver_id: int,
) -> list[CompEntry]:
    """
    Zwraca wpisy do utworzenia dla wykrytych długów Wn bez istniejącego rekordu.
    (nie zapisuje — caller robi upsert).
    """
    have = {
        (e.kind, e.earn_day)
        for e in existing
        if e.kind == CompKind.SUNDAY_HOLIDAY_WN
    }
    to_create: list[CompEntry] = []
    for debt in debts:
        if (debt.kind, debt.earn_day) in have:
            continue
        to_create.append(
            CompEntry(
                id=None,
                driver_id=driver_id,
                kind=debt.kind,
                earn_day=debt.earn_day,
                settle_day=None,
                minutes=debt.minutes,
                rate_percent=debt.rate_percent,
                title=debt.title,
            )
        )
    return to_create


def entry_status_pl(entry: CompEntry) -> str:
    if entry.paid_out:
        return "wypłacono 100%"
    if entry.settle_day is not None:
        return f"oddane {entry.settle_day.strftime('%d.%m.%Y')}"
    return "do oddania"


def format_comp_detail_line(entry: CompEntry) -> str:
    kind_l = COMP_LABELS_PL.get(entry.kind, entry.kind.value)
    if entry.kind == CompKind.SUNDAY_HOLIDAY_WN:
        if entry.paid_out:
            return (
                f"{entry.earn_day.strftime('%d.%m.%Y')}: {kind_l} — "
                f"wypłacono 100% (nie oddano dnia). {entry.title}"
            )
        if entry.settle_day:
            return (
                f"{entry.earn_day.strftime('%d.%m.%Y')}: Wolne za pracę w tym dniu (Wn) "
                f"zostało oddane w dniu {entry.settle_day.strftime('%d.%m.%Y')}. "
                f"{entry.title}"
            )
        return (
            f"{entry.earn_day.strftime('%d.%m.%Y')}: {kind_l} — do oddania "
            f"do końca miesiąca. {entry.title}"
        )
    if entry.kind == CompKind.OVERTIME_XB:
        settle = (
            entry.settle_day.strftime("%d.%m.%Y") if entry.settle_day else "—"
        )
        return (
            f"{entry.earn_day.strftime('%d.%m.%Y')}: Godziny nadliczbowe z tego dnia "
            f"zostały oddane (Xb) w dniu {settle}. "
            f"{entry.minutes // 60:02d}:{entry.minutes % 60:02d} ({entry.rate_percent}%)."
        )
    settle = entry.settle_day.strftime("%d.%m.%Y") if entry.settle_day else "—"
    return (
        f"{entry.settle_day and entry.settle_day.strftime('%d.%m.%Y') or entry.earn_day.strftime('%d.%m.%Y')}: "
        f"Wolne za czas dyżurów 100% z dnia {entry.earn_day.strftime('%d.%m.%Y')} "
        f"({entry.minutes // 60:02d}:{entry.minutes % 60:02d}). Oddanie: {settle}."
    )
