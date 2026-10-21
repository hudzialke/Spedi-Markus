from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ddd_analyzer.models import (
    ActivitySegment,
    ClassifiedRest,
    ContinuousRestBlock,
    RestAnalysisSummary,
    RestKind,
)
from ddd_analyzer.rest_timeline import continuous_rest_blocks

H9 = 9 * 60
H11 = 11 * 60
H24 = 24 * 60
H45 = 45 * 60


def classify_rest_kind(duration_min: int) -> RestKind:
    if duration_min >= H45:
        return RestKind.WEEKLY_REGULAR
    if duration_min >= H24:
        return RestKind.WEEKLY_REDUCED
    if duration_min >= H11:
        return RestKind.DAILY_REGULAR
    if duration_min >= H9:
        return RestKind.DAILY_REDUCED
    return RestKind.OTHER


def compensation_deadline_for(rest_start: datetime) -> date:
    """Koniec 3. tygodnia ISO po tygodniu, w ktorym zaczal sie odpoczynek."""
    iso_year, iso_week, _ = rest_start.isocalendar()
    week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    end_week_monday = week_monday + timedelta(weeks=3)
    return end_week_monday + timedelta(days=6)


@dataclass
class _DebtItem:
    created_at: datetime
    remaining_min: int
    deadline: date
    eligible_after: datetime
    from_consecutive_pair: bool = False
    pair_id: int | None = None


def _apply_en_bloc(
    pool_min: int,
    debts: list[_DebtItem],
    *,
    at: datetime,
    allow_pair_debts: bool,
) -> int:
    """Spłać najstarsze długi wyłącznie w całości (art. 8 ust. 6b)."""
    used = 0
    remaining_pool = pool_min
    for debt in debts:
        if debt.remaining_min <= 0:
            continue
        if at < debt.eligible_after:
            continue
        if debt.from_consecutive_pair and not allow_pair_debts:
            continue
        if remaining_pool < debt.remaining_min:
            continue
        remaining_pool -= debt.remaining_min
        used += debt.remaining_min
        debt.remaining_min = 0
    return used


def _consecutive_reduced_pairs(
    blocks: list[ContinuousRestBlock],
) -> dict[int, tuple[int, int]]:
    """Wskaż pary skróconych OT rozpoczętych w kolejnych tygodniach ISO."""
    weekly = [
        (index, block)
        for index, block in enumerate(blocks)
        if classify_rest_kind(block.duration_min)
        in {RestKind.WEEKLY_REDUCED, RestKind.WEEKLY_REGULAR}
    ]
    pairs: dict[int, tuple[int, int]] = {}
    pair_id = 0
    pos = 0
    while pos < len(weekly) - 1:
        first_index, first = weekly[pos]
        second_index, second = weekly[pos + 1]
        first_kind = classify_rest_kind(first.duration_min)
        second_kind = classify_rest_kind(second.duration_min)
        first_monday = first.start.date() - timedelta(days=first.start.weekday())
        second_monday = second.start.date() - timedelta(days=second.start.weekday())
        if (
            first_kind == RestKind.WEEKLY_REDUCED
            and second_kind == RestKind.WEEKLY_REDUCED
            and second_monday - first_monday == timedelta(days=7)
        ):
            pairs[first_index] = (pair_id, second_index)
            pairs[second_index] = (pair_id, first_index)
            pair_id += 1
            pos += 2
            continue
        pos += 1
    return pairs


def classify_561(blocks: list[ContinuousRestBlock]) -> list[ClassifiedRest]:
    """Klasyfikuj bloki REST i rozlicz rekompensaty zgodnie z art. 8."""
    blocks = sorted(blocks, key=lambda block: (block.start, block.end))
    pair_members = _consecutive_reduced_pairs(blocks)
    debts: list[_DebtItem] = []
    pair_debts: dict[int, _DebtItem] = {}
    results: list[ClassifiedRest] = []

    for block_index, block in enumerate(blocks):
        kind = classify_rest_kind(block.duration_min)
        debt_created = 0
        compensation_min = 0
        deadline: date | None = None
        notes: list[str] = []

        if kind == RestKind.WEEKLY_REDUCED:
            debt_created = H45 - block.duration_min
            attached_in_block = max(0, block.duration_min - H24)
            deadline = compensation_deadline_for(block.start)

            # Nadwyżka ponad 24h może spłacić starszy pojedynczy dług,
            # ale tylko w całości. Dług pary 2× skrócony czeka na regularny OT.
            older = [d for d in debts if d.remaining_min > 0]
            compensation_min = (
                _apply_en_bloc(
                    attached_in_block,
                    older,
                    at=block.start,
                    allow_pair_debts=False,
                )
                if older
                else 0
            )
            if compensation_min:
                notes.append(f"Oddano jednorazowo {compensation_min} min starszej rekompensaty")

            pair = pair_members.get(block_index)
            if pair is None:
                debts.append(
                    _DebtItem(
                        created_at=block.start,
                        remaining_min=debt_created,
                        deadline=deadline,
                        eligible_after=block.end,
                    )
                )
            else:
                pair_id, other_index = pair
                existing = pair_debts.get(pair_id)
                if existing is None:
                    second = blocks[other_index]
                    item = _DebtItem(
                        created_at=block.start,
                        remaining_min=debt_created,
                        deadline=compensation_deadline_for(second.start),
                        eligible_after=second.end,
                        from_consecutive_pair=True,
                        pair_id=pair_id,
                    )
                    debts.append(item)
                    pair_debts[pair_id] = item
                    notes.append("Pierwszy z pary 2× skrócony: rekompensata oczekuje na drugi OT")
                else:
                    existing.remaining_min += debt_created
                    notes.append(
                        "Drugi z pary 2× skrócony: obie rekompensaty tylko razem "
                        "przed regularnym OT"
                    )

        elif kind == RestKind.WEEKLY_REGULAR:
            # Para 2× skrócony może być rozliczona tylko tutaj: pełna
            # rekompensata obu skróceń + co najmniej 45h regularnego OT.
            pool = max(0, block.duration_min - H45)
            if pool and debts:
                compensation_min = _apply_en_bloc(
                    pool,
                    debts,
                    at=block.start,
                    allow_pair_debts=True,
                )
                if compensation_min:
                    notes.append(f"Oddano jednorazowo {compensation_min} min przed regularnym OT")

        elif block.duration_min >= H9:
            # Pojedyncze skrócenie można dołączyć do OD >=9h, en bloc.
            # Dług z pary 2× skrócony jest tu zawsze zablokowany.
            pool = max(0, block.duration_min - H9)
            if pool and debts:
                compensation_min = _apply_en_bloc(
                    pool,
                    debts,
                    at=block.start,
                    allow_pair_debts=False,
                )
                if compensation_min:
                    notes.append(f"Oddano jednorazowo {compensation_min} min rekompensaty")

        unpaid = sum(d.remaining_min for d in debts)
        overdue = any(
            d.remaining_min > 0 and d.deadline < block.end.date() for d in debts
        )
        status = "zalegla" if overdue and unpaid > 0 else "OK"

        open_deadlines = [d.deadline for d in debts if d.remaining_min > 0]
        show_deadline = None
        if kind == RestKind.WEEKLY_REDUCED:
            show_deadline = deadline
        elif open_deadlines:
            show_deadline = min(open_deadlines)

        results.append(
            ClassifiedRest(
                start=block.start,
                end=block.end,
                duration_min=block.duration_min,
                kind=kind,
                compensation_min=compensation_min,
                debt_created_min=debt_created,
                debt_after_min=unpaid,
                deadline=show_deadline,
                status=status,
                note="; ".join(notes),
            )
        )

    return results


def analyze_rests(segments: list[ActivitySegment]) -> RestAnalysisSummary:
    blocks = continuous_rest_blocks(segments)
    classified = classify_561(blocks)
    unpaid = classified[-1].debt_after_min if classified else 0
    return RestAnalysisSummary(
        classified=classified,
        unpaid_compensation_min=unpaid,
        weekly_reduced_count=sum(1 for c in classified if c.kind == RestKind.WEEKLY_REDUCED),
        weekly_regular_count=sum(1 for c in classified if c.kind == RestKind.WEEKLY_REGULAR),
        daily_reduced_count=sum(1 for c in classified if c.kind == RestKind.DAILY_REDUCED),
        daily_regular_count=sum(1 for c in classified if c.kind == RestKind.DAILY_REGULAR),
    )


def significant_rests(classified: list[ClassifiedRest]) -> list[ClassifiedRest]:
    """Bloki >=9h do arkusza Odpoczynki_561."""
    return [c for c in classified if c.duration_min >= H9]
