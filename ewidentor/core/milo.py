from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ewidentor.core.milo_rates import (
    DEFAULT_FX_PLN,
    POSTING_OP_TYPES,
    AtSeniority,
    CountryRate,
    OpType,
    country_rate_map,
    resolve_hourly_rate,
)


@dataclass(slots=True)
class MiloStay:
    """Ręczny wpis: czas MiLo (jazda+praca+dyżur) w kraju w danym dniu."""

    id: int | None
    driver_id: int
    day: date
    country_code: str
    minutes: int
    op_type: OpType
    note: str = ""


def sorted_milo_stays(
    stays: list[MiloStay],
    *,
    first_seen: dict[tuple[date, str], datetime] | None = None,
) -> list[MiloStay]:
    """
    Earliest first by day.
    When ``first_seen`` is provided (from places / border crossings), same-day
    rows follow actual presence start — not insert id or alphabetical country.
    Without hints: day, then id (legacy insert order).
    """
    if not first_seen:
        return sorted(
            stays,
            key=lambda s: (s.day, s.id if s.id is not None else 0),
        )

    def key(s: MiloStay) -> tuple:
        seen = first_seen.get((s.day, s.country_code.upper()))
        return (
            s.day,
            seen if seen is not None else datetime.max,
            s.id if s.id is not None else 0,
        )

    return sorted(stays, key=key)


@dataclass(slots=True)
class MiloTopUpLine:
    country_code: str
    country_name: str
    op_type: OpType
    minutes: int
    currency: str
    min_hourly: float
    pl_hourly_in_currency: float
    topup_currency: float
    topup_pln: float
    fx: float
    note: str = ""


@dataclass(slots=True)
class MiloResult:
    lines: list[MiloTopUpLine] = field(default_factory=list)
    topup_total_pln: float = 0.0
    posting_minutes: int = 0
    skipped_minutes: int = 0  # bilateral/transit / disabled / brak stawki


def compute_milo_topup(
    stays: list[MiloStay],
    *,
    pl_hourly_pln: float,
    rates: list[CountryRate],
    fx_pln: dict[str, float] | None = None,
    at_band: AtSeniority = AtSeniority.A,
    posting_only: bool = True,
) -> MiloResult:
    """
    Top-up: cabotage/cross-trade (domyślnie) albo cały czas pracy w kraju
    (``posting_only=False`` — jak wyrównanie na karcie 4Trans).
    Kwota = godziny × max(0, min_kraj − stawka_PL w walucie kraju), potem × kurs → PLN.
    """
    fx = {**DEFAULT_FX_PLN, **(fx_pln or {})}
    by_code = country_rate_map(rates)
    # agreguj po (kraj, typ) — czytelniej w raporcie
    buckets: dict[tuple[str, OpType], int] = {}
    skipped = 0
    for s in stays:
        if s.minutes <= 0:
            continue
        key = (s.country_code.upper(), s.op_type)
        if s.country_code.upper() == "PL":
            skipped += s.minutes
            continue
        if posting_only and s.op_type not in POSTING_OP_TYPES:
            skipped += s.minutes
            continue
        buckets[key] = buckets.get(key, 0) + s.minutes

    lines: list[MiloTopUpLine] = []
    total = 0.0
    posting = 0
    for (code, op), minutes in sorted(buckets.items()):
        rate = by_code.get(code)
        if rate is None or not rate.enabled:
            skipped += minutes
            continue
        currency = rate.currency.upper()
        unit_pln = float(fx.get(currency, 0.0) or 0.0)
        if unit_pln <= 0:
            skipped += minutes
            continue
        min_h = resolve_hourly_rate(rate, at_band=at_band)
        pl_in_cur = pl_hourly_pln / unit_pln
        hours = minutes / 60.0
        diff = max(0.0, min_h - pl_in_cur)
        top_cur = round(hours * diff, 4)
        top_pln = round(top_cur * unit_pln, 2)
        posting += minutes
        total += top_pln
        lines.append(
            MiloTopUpLine(
                country_code=code,
                country_name=rate.name_pl,
                op_type=op,
                minutes=minutes,
                currency=currency,
                min_hourly=min_h,
                pl_hourly_in_currency=round(pl_in_cur, 4),
                topup_currency=top_cur,
                topup_pln=top_pln,
                fx=unit_pln,
                note=rate.note,
            )
        )
    return MiloResult(
        lines=lines,
        topup_total_pln=round(total, 2),
        posting_minutes=posting,
        skipped_minutes=skipped,
    )
