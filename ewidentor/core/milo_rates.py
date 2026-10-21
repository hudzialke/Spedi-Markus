from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OpType(str, Enum):
    BILATERAL = "bilateral"
    TRANSIT = "transit"
    CABOTAGE = "cabotage"
    CROSS_TRADE = "cross_trade"


OP_LABELS_PL: dict[OpType, str] = {
    OpType.BILATERAL: "Bilateralny",
    OpType.TRANSIT: "Tranzyt",
    OpType.CABOTAGE: "Kabotaż",
    OpType.CROSS_TRADE: "Cross-trade",
}

MILO_OP_GLOSSARY_PL = (
    "Bilateralny — trasa związana z PL (typowy wyjazd/powrót). Bez wyrównania MiLo.\n"
    "Tranzyt — krótki przejazd przez kraj. Bez wyrównania.\n"
    "Kabotaż — załadunek i rozładunek w tym samym kraju obcym. Jest top-up.\n"
    "Cross-trade — załadunek w kraju A, rozładunek w B, oba ≠ PL. Jest top-up."
)

POSTING_OP_TYPES = frozenset({OpType.CABOTAGE, OpType.CROSS_TRADE})


def coerce_op_type(value: OpType | str | None) -> OpType:
    """QComboBox często zwraca zwykły str zamiast enuma."""
    if isinstance(value, OpType):
        return value
    raw = str(value or OpType.BILATERAL.value).strip().lower()
    try:
        return OpType(raw)
    except ValueError:
        return OpType.BILATERAL


def op_type_code(value: OpType | str | None) -> str:
    return coerce_op_type(value).value


class AtSeniority(str, Enum):
    """Progi stażu AT (KV Güterbeförderung, LKW ≤3,5 t) — a = ≤5 lat."""

    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"


def coerce_at_seniority(value: AtSeniority | str | None) -> AtSeniority:
    """QComboBox/SQLite często zwracają zwykły str zamiast enuma."""
    if isinstance(value, AtSeniority):
        return value
    raw = str(value or "a").strip().lower()
    try:
        return AtSeniority(raw)
    except ValueError:
        return AtSeniority.A


def at_seniority_code(value: AtSeniority | str | None) -> str:
    return coerce_at_seniority(value).value


AT_SENIORITY_LABELS: dict[AtSeniority, str] = {
    AtSeniority.A: "a — ≤5 lat (12,34 €)",
    AtSeniority.B: "b — kolejny próg (12,63 €)",
    AtSeniority.C: "c — kolejny próg (12,92 €)",
    AtSeniority.D: "d — kolejny próg (13,26 €)",
    AtSeniority.E: "e — najwyższy (13,60 €)",
}

AT_SENIORITY_RATES_EUR: dict[AtSeniority, float] = {
    AtSeniority.A: 12.34,
    AtSeniority.B: 12.63,
    AtSeniority.C: 12.92,
    AtSeniority.D: 13.26,
    AtSeniority.E: 13.60,
}


@dataclass(slots=True)
class CountryRate:
    country_code: str
    name_pl: str
    currency: str
    hourly_rate: float
    enabled: bool = True
    note: str = ""
    source: str = "seed_2026"


# Seed 2026 — bus ≤3,5 t / kat. B (zaakceptowany w planie). MC/SM wyłączone.
SEED_COUNTRY_RATES: tuple[CountryRate, ...] = (
    CountryRate("DE", "Niemcy", "EUR", 13.90, True, "Mindestlohn 01.01.2026"),
    CountryRate("FR", "Francja", "EUR", 12.31, True, "CCN 3 bis ≤3,5 t"),
    CountryRate("AT", "Austria", "EUR", 12.34, True, "KV ≤3,5 t, staż ≤5 lat (override a–e)"),
    CountryRate("NO", "Norwegia", "NOK", 229.0, True, "Allmenngjøring >2,5 t"),
    CountryRate("CZ", "Czechy", "CZK", 134.40, True, "Min. krajowa 2026"),
    CountryRate("SK", "Słowacja", "EUR", 5.28, True, "915 €/mies. ≈ /173 h"),
    CountryRate("HU", "Węgry", "HUF", 2145.0, True, "Min. krajowa"),
    CountryRate("HR", "Chorwacja", "EUR", 6.07, True, "1050 €/mies. ≈ /173 h"),
    CountryRate("SI", "Słowenia", "EUR", 8.57, True, "1482 €/mies. ≈ /173 h"),
    CountryRate("LT", "Litwa", "EUR", 7.05, True, "Min. krajowa"),
    CountryRate("LV", "Łotwa", "EUR", 4.51, True, "780 €/mies. ≈ /173 h"),
    CountryRate("EE", "Estonia", "EUR", 5.31, True, "886 €/mies."),
    CountryRate("ES", "Hiszpania", "EUR", 8.50, True, "SMI orientacyjnie"),
    CountryRate("PT", "Portugalia", "EUR", 5.32, True, "920 €/mies. ≈ /173 h"),
    CountryRate("DK", "Dania", "DKK", 196.0, True, "CBA orientacyjny — edytuj"),
    CountryRate("SE", "Szwecja", "SEK", 178.0, True, "CBA orientacyjny — edytuj"),
    CountryRate("FI", "Finlandia", "EUR", 17.00, True, "CBA orientacyjny — edytuj"),
    CountryRate("IT", "Włochy", "EUR", 9.50, True, "CCNL orientacyjny — edytuj"),
    CountryRate("NL", "Holandia", "EUR", 14.06, True, "Min. krajowa orientacyjnie"),
    CountryRate("BE", "Belgia", "EUR", 12.50, True, "Orientacyjnie — edytuj"),
    CountryRate("LU", "Luksemburg", "EUR", 15.25, True, "Orientacyjnie — edytuj"),
    CountryRate("MC", "Monako", "EUR", 12.02, False, "Wyłączone na start"),
    CountryRate("SM", "San Marino", "EUR", 10.64, False, "Wyłączone na start"),
)

# Kursy: ile PLN za 1 jednostkę waluty (ręczne ustawienia).
DEFAULT_FX_PLN: dict[str, float] = {
    "EUR": 4.30,
    "NOK": 0.37,
    "CZK": 0.17,
    "HUF": 0.011,
    "DKK": 0.58,
    "SEK": 0.38,
    "PLN": 1.0,
}


def country_rate_map(rates: list[CountryRate]) -> dict[str, CountryRate]:
    return {r.country_code.upper(): r for r in rates}


def resolve_hourly_rate(
    rate: CountryRate,
    *,
    at_band: AtSeniority = AtSeniority.A,
) -> float:
    if rate.country_code.upper() == "AT":
        return AT_SENIORITY_RATES_EUR.get(at_band, rate.hourly_rate)
    return rate.hourly_rate
