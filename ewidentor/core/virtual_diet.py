from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ewidentor.core.milo import MiloStay

# Limity diet zagranicznych (pomocnicze, nie wypłata) — przyjęte w planie.
DIET_ZUS_EUR_PER_DAY = 60.0
DIET_PIT_EUR_PER_DAY = 20.0
# Orientacyjne przeciętne wynagrodzenie (limit kontekstu ZUS) — edytowalne w settings.
DEFAULT_AVG_WAGE_PLN = 8747.0


def diet_fraction(minutes: int) -> float:
    """Pełna ≥12 h, ½ przy 8–12 h, ⅓ poniżej 8 h (przy czasie > 0)."""
    if minutes <= 0:
        return 0.0
    if minutes >= 12 * 60:
        return 1.0
    if minutes >= 8 * 60:
        return 0.5
    return 1.0 / 3.0


@dataclass(slots=True)
class VirtualDietDay:
    day: date
    abroad_minutes: int
    fraction: float
    zus_eur: float
    pit_eur: float


@dataclass(slots=True)
class VirtualDietResult:
    days: list[VirtualDietDay] = field(default_factory=list)
    fraction_total: float = 0.0
    zus_eur: float = 0.0
    pit_eur: float = 0.0
    zus_pln: float = 0.0
    pit_pln: float = 0.0
    eur_pln: float = 0.0
    avg_wage_pln: float = DEFAULT_AVG_WAGE_PLN
    note: str = (
        "Wirtualna dieta — informacyjnie (nie wchodzi do brutto/netto wypłaty). "
        "ZUS: 60 €/dzień, PIT: 20 €/dzień; progi czasu: ≥12 h = 1, 8–12 h = ½, <8 h = ⅓."
    )


def compute_virtual_diet(
    stays: list[MiloStay],
    *,
    eur_pln: float,
    avg_wage_pln: float = DEFAULT_AVG_WAGE_PLN,
    exclude_countries: frozenset[str] = frozenset({"PL"}),
) -> VirtualDietResult:
    """
    Agreguje minuty pobytu za granicą per dzień kalendarzowy z wpisów MiLo
    (w tym auto z places / border_crossings).
    """
    by_day: dict[date, int] = {}
    for s in stays:
        code = s.country_code.upper()
        if code in exclude_countries:
            continue
        if s.minutes <= 0:
            continue
        by_day[s.day] = by_day.get(s.day, 0) + s.minutes

    days: list[VirtualDietDay] = []
    frac_sum = 0.0
    zus = 0.0
    pit = 0.0
    for day in sorted(by_day):
        mins = by_day[day]
        frac = diet_fraction(mins)
        z = round(DIET_ZUS_EUR_PER_DAY * frac, 4)
        p = round(DIET_PIT_EUR_PER_DAY * frac, 4)
        days.append(
            VirtualDietDay(
                day=day,
                abroad_minutes=mins,
                fraction=frac,
                zus_eur=z,
                pit_eur=p,
            )
        )
        frac_sum += frac
        zus += z
        pit += p

    fx = max(0.0, float(eur_pln))
    return VirtualDietResult(
        days=days,
        fraction_total=round(frac_sum, 4),
        zus_eur=round(zus, 2),
        pit_eur=round(pit, 2),
        zus_pln=round(zus * fx, 2),
        pit_pln=round(pit * fx, 2),
        eur_pln=fx,
        avg_wage_pln=float(avg_wage_pln),
    )
