"""Heurystyki sugestii typu operacji MiLo (bez auto-aplikacji)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ewidentor.core.dispatcher_milo import (
    DispatcherLeg,
    classify_stay_from_dispatcher,
    matching_leg,
)
from ewidentor.core.milo import MiloStay
from ewidentor.core.milo_rates import OP_LABELS_PL, OpType


# Krótki pobyt → kandydat na tranzyt (minuty).
TRANSIT_MAX_MIN = 3 * 60
HOME_COUNTRY = "PL"


@dataclass(slots=True)
class MiloOpSuggestion:
    stay_id: int
    day: date
    country_code: str
    current: OpType
    suggested: OpType
    reason: str
    source: str = "DDD"


def suggest_milo_op_types(
    stays: list[MiloStay],
    *,
    dispatcher_legs: Sequence[DispatcherLeg] | None = None,
) -> list[MiloOpSuggestion]:
    """
    Sugestie typów: DDD (obecność) + Dispatcher (zał./rozł.) gdy jest zlecenie.

    DDD (gdy brak dopasowanego zlecenia):
    - PL → bilateral (bez top-upu)
    - wiele krajów ≠ PL + krótki pobyt (< 3 h) → transit
    - wiele krajów ≠ PL + dłuższy pobyt → cross_trade
    - jeden kraj zagraniczny → bilateral

    Dispatcher nadpisuje DDD: załadunek w kraju pobytu przy rozładunku
    gdzie indziej → cross-trade (nie tranzyt); zał.+rozł. w tym samym
    kraju obcym → kabotaż.
    """
    if not stays:
        return []

    legs = tuple(dispatcher_legs or ())
    foreign_codes = {
        s.country_code.upper()
        for s in stays
        if s.country_code.upper() != HOME_COUNTRY and s.minutes > 0
    }
    multi_foreign = len(foreign_codes) >= 2

    by_day: dict[date, set[str]] = {}
    for s in stays:
        code = s.country_code.upper()
        if code == HOME_COUNTRY or s.minutes <= 0:
            continue
        by_day.setdefault(s.day, set()).add(code)

    out: list[MiloOpSuggestion] = []
    for s in stays:
        if s.id is None or s.minutes <= 0:
            continue
        code = s.country_code.upper()
        current = s.op_type
        source = "DDD"

        disp = classify_stay_from_dispatcher(s.day, code, legs)
        if disp is not None:
            suggested = disp.op
            reason = disp.reason
            source = "DISP."
        elif code == HOME_COUNTRY:
            suggested = OpType.BILATERAL
            reason = "Polska — bez posting (bilateral)"
        elif multi_foreign and s.minutes < TRANSIT_MAX_MIN:
            suggested = OpType.TRANSIT
            reason = f"Krótki pobyt ({s.minutes} min) przy wielu krajach — tranzyt"
        elif multi_foreign:
            day_n = len(by_day.get(s.day, set()))
            if day_n >= 2 or len(foreign_codes) >= 3:
                suggested = OpType.CROSS_TRADE
                reason = "Wiele krajów w trasie — cross-trade (nie prosty bilateral)"
            else:
                suggested = OpType.CROSS_TRADE
                reason = "Multi-country w miesiącu — cross-trade zamiast auto-bilateral"
        else:
            suggested = OpType.BILATERAL
            reason = "Jeden kraj zagraniczny — bilateral"

        if suggested == current:
            continue

        out.append(
            MiloOpSuggestion(
                stay_id=int(s.id),
                day=s.day,
                country_code=code,
                current=current,
                suggested=suggested,
                reason=reason,
                source=source,
            )
        )
    return out


def suggestion_for_stay(
    stays: list[MiloStay],
    stay_id: int | None,
    *,
    dispatcher_legs: Sequence[DispatcherLeg] | None = None,
) -> MiloOpSuggestion | None:
    if stay_id is None:
        return None
    for item in suggest_milo_op_types(stays, dispatcher_legs=dispatcher_legs):
        if item.stay_id == stay_id:
            return item
    return None


def format_suggestion_hint(sug: MiloOpSuggestion, *, source: str | None = None) -> str:
    label = OP_LABELS_PL.get(sug.suggested, sug.suggested.value)
    src = source or sug.source or "DDD"
    return f"wg {src}: {label} — {sug.reason}"


def format_dispatcher_hint(
    *,
    load_cc: str,
    unload_cc: str,
    load_day: date | None = None,
    unload_day: date | None = None,
) -> str:
    """Podgląd zał./rozł. z Dispatchera (ten sam sygnał co nadpisanie typu)."""
    load = (load_cc or "?").strip().upper() or "?"
    unload = (unload_cc or "?").strip().upper() or "?"
    extra_l = f" {load_day.strftime('%d.%m')}" if load_day else ""
    extra_u = f" {unload_day.strftime('%d.%m')}" if unload_day else ""
    return f"wg DISP.: zał. {load}{extra_l}, rozł. {unload}{extra_u}"


def dispatcher_hint_for_stay(
    day: date,
    country_code: str,
    legs: Sequence[DispatcherLeg] | None,
) -> str:
    if not legs:
        return ""
    leg = matching_leg(day, country_code, list(legs))
    if leg is None:
        return ""
    unload = "/".join(leg.unload_ccs) or "?"
    return format_dispatcher_hint(
        load_cc=leg.load_cc or "?",
        unload_cc=unload,
        load_day=leg.load_day,
        unload_day=leg.unload_day,
    )


def mark_note_manual(note: str) -> str:
    """Po ręcznej zmianie typu — odejście od czystego „auto…”."""
    raw = (note or "").strip()
    low = raw.lower()
    if not raw:
        return "ręcznie"
    if "ręcznie" in low:
        return raw
    if low.startswith("auto"):
        return f"{raw} · ręcznie"
    return f"{raw} · ręcznie"
