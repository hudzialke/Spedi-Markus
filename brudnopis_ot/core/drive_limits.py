from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from brudnopis_ot.core.calendar_weeks import format_duration_hm, week_bounds, week_index
from brudnopis_ot.core.daily_summary import build_daily_totals
from brudnopis_ot.core.models import (
    ActivityKind,
    Block,
    Finding,
    Scenario,
    TimeRange,
    WeeklyRestPeriod,
)

# 561/2006 art. 6
DAILY_DRIVE_LIMIT_H = 9.0
DAILY_DRIVE_EXTENDED_H = 10.0
MAX_EXTENDED_DAYS_PER_WEEK = 2
WEEKLY_DRIVE_LIMIT_H = 56.0
BIWEEKLY_DRIVE_LIMIT_H = 90.0


@dataclass(frozen=True, slots=True)
class DriveLimitsResult:
    findings: tuple[Finding, ...]
    hints: tuple[str, ...]
    period_summaries: tuple[str, ...]


def _overlap_hours(block: Block, start: datetime, end: datetime) -> float:
    s = max(block.start, start)
    e = min(block.end, end)
    if e <= s:
        return 0.0
    return (e - s).total_seconds() / 3600.0


def _sum_kind_hours(
    blocks: list[Block],
    kind: ActivityKind | tuple[ActivityKind, ...],
    start: datetime,
    end: datetime,
) -> float:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    total = 0.0
    for block in blocks:
        if block.kind in kinds:
            total += _overlap_hours(block, start, end)
    return total


def _period_summaries(
    weekly: list[WeeklyRestPeriod],
    blocks: list[Block],
    *,
    now_end: datetime | None = None,
) -> list[str]:
    """Sumy jazda/praca/odpoczynek między kolejnymi OT (i po ostatnim)."""
    lines: list[str] = []
    rest_kinds = (ActivityKind.REST, ActivityKind.CARD_OUT)

    def _line(label: str, a: datetime, b: datetime) -> str:
        drive = _sum_kind_hours(blocks, ActivityKind.DRIVE, a, b)
        work = _sum_kind_hours(blocks, ActivityKind.WORK, a, b)
        avail = _sum_kind_hours(blocks, ActivityKind.AVAILABILITY, a, b)
        rest = _sum_kind_hours(blocks, rest_kinds, a, b)
        return (
            f"Między OT {label}: jazda {format_duration_hm(drive)}, "
            f"praca {format_duration_hm(work)}, dysp. {format_duration_hm(avail)}, "
            f"odpoczynek {format_duration_hm(rest)}"
        )

    for prev, curr in zip(weekly, weekly[1:], strict=False):
        lines.append(
            _line(
                f"{prev.end.strftime('%d.%m %H:%M')}→{curr.start.strftime('%d.%m %H:%M')}",
                prev.end,
                curr.start,
            )
        )
    if weekly:
        last = weekly[-1]
        end = now_end or (
            max((b.end for b in blocks), default=last.end)
        )
        if end > last.end:
            lines.append(
                _line(
                    f"{last.end.strftime('%d.%m %H:%M')}→teraz",
                    last.end,
                    end,
                )
            )
    return lines


def _calendar_week_drive_hours(
    blocks: list[Block],
    week_monday: date,
) -> float:
    start, end = week_bounds(week_monday)
    return _sum_kind_hours(blocks, ActivityKind.DRIVE, start, end)


def _extended_drive_days_in_week(
    blocks: list[Block],
    week_monday: date,
) -> list[date]:
    """Dni kalendarzowe z jazdą > 9h w danym tygodniu Pn–Nd."""
    start, end = week_bounds(week_monday)
    days = build_daily_totals(
        blocks,
        range_start=week_monday,
        range_end=week_monday + timedelta(days=6),
    )
    extended: list[date] = []
    for day in days:
        hours = day.drive_min / 60.0
        if hours > DAILY_DRIVE_LIMIT_H + 1e-6:
            extended.append(day.day)
    # also flag single-day > 10h later via findings
    del start, end
    return extended


def analyze_drive_limits(
    scenario: Scenario,
    weekly_rests: list[WeeklyRestPeriod],
) -> DriveLimitsResult:
    """Limity jazdy art. 6 WE/561/2006 względem osi scenariusza."""
    findings: list[Finding] = []
    hints: list[str] = []
    blocks = scenario.blocks
    weekly = [w for w in weekly_rests if w.is_weekly]

    summaries = _period_summaries(weekly, blocks)
    hints.extend(summaries)

    for widx in range(scenario.weeks):
        monday = scenario.week_start + timedelta(days=7 * widx)
        drive_h = _calendar_week_drive_hours(blocks, monday)
        extended_days = _extended_drive_days_in_week(blocks, monday)
        remaining_10 = max(0, MAX_EXTENDED_DAYS_PER_WEEK - len(extended_days))

        # Dni z jazdą > 10h
        for day in extended_days:
            day_totals = build_daily_totals(blocks, range_start=day, range_end=day)
            if not day_totals:
                continue
            hours = day_totals[0].drive_min / 60.0
            if hours > DAILY_DRIVE_EXTENDED_H + 1e-6:
                day_start = datetime.combine(day, datetime.min.time())
                findings.append(
                    Finding(
                        severity="violation",
                        code="drive_10h_day",
                        message_pl=(
                            f"Tydzień {widx + 1}: jazda {format_duration_hm(hours)} "
                            f"dnia {day.strftime('%d.%m.%Y')} (max 10h)."
                        ),
                        underline_range=TimeRange(
                            start=day_start,
                            end=day_start + timedelta(days=1),
                        ),
                    )
                )

        if len(extended_days) > MAX_EXTENDED_DAYS_PER_WEEK:
            findings.append(
                Finding(
                    severity="violation",
                    code="drive_10h",
                    message_pl=(
                        f"Tydzień {widx + 1} ({monday.strftime('%d.%m')}–"
                        f"{(monday + timedelta(days=6)).strftime('%d.%m')}): "
                        f"{len(extended_days)}× jazda >9h (dozwolone max 2×10h)."
                    ),
                )
            )
        elif drive_h > 0.5:
            used = len(extended_days)
            hints.append(
                f"Tydzień {widx + 1}: jazda {format_duration_hm(drive_h)}/56h · "
                f"dni 10h: {used}/2 · "
                + (
                    f"wolno jeszcze {remaining_10}× jazdę 10h"
                    if remaining_10
                    else "brak kolejnych 10h w tym tygodniu"
                )
            )

        if drive_h > WEEKLY_DRIVE_LIMIT_H + 1e-6:
            start, end = week_bounds(monday)
            findings.append(
                Finding(
                    severity="violation",
                    code="drive_56h",
                    message_pl=(
                        f"Tydzień {widx + 1}: jazda {format_duration_hm(drive_h)} "
                        f"(limit 56h/tydzień)."
                    ),
                    underline_range=TimeRange(start=start, end=end),
                )
            )

    for widx in range(max(0, scenario.weeks - 1)):
        m1 = scenario.week_start + timedelta(days=7 * widx)
        m2 = m1 + timedelta(days=7)
        h1 = _calendar_week_drive_hours(blocks, m1)
        h2 = _calendar_week_drive_hours(blocks, m2)
        total = h1 + h2
        if total > BIWEEKLY_DRIVE_LIMIT_H + 1e-6:
            start, _ = week_bounds(m1)
            _, end = week_bounds(m2)
            findings.append(
                Finding(
                    severity="violation",
                    code="drive_90h",
                    message_pl=(
                        f"Tygodnie {widx + 1}–{widx + 2}: jazda "
                        f"{format_duration_hm(total)} (limit 90h/2 tygodnie)."
                    ),
                    underline_range=TimeRange(start=start, end=end),
                )
            )

    return DriveLimitsResult(
        findings=tuple(findings),
        hints=tuple(hints),
        period_summaries=tuple(summaries),
    )


def analyze_ot_planning_hints(
    scenario: Scenario,
    weekly_rests: list[WeeklyRestPeriod],
    pm_pairs: list[tuple[WeeklyRestPeriod, WeeklyRestPeriod]],
    week_classes: dict[int, list[str]],
) -> tuple[list[Finding], list[str]]:
    """
    Proaktywne ostrzeżenia: wymuszony OT regularny / cooldown przed kolejną parą PM.
    """
    findings: list[Finding] = []
    hints: list[str] = []
    weekly = [w for w in weekly_rests if w.is_weekly]
    if not weekly:
        return findings, hints

    # --- Cooldown po parze PM: ≥2 regularne w 4 tygodniach od pierwszego skróconego ---
    latest_pm_ready = True
    for a, b in pm_pairs:
        window_start = a.start
        window_end = window_start + timedelta(weeks=4)
        in_window = [w for w in weekly if window_start <= w.start < window_end]
        regulars = [w for w in in_window if w.classification == "regular"]
        if len(regulars) < 2:
            latest_pm_ready = False
            findings.append(
                Finding(
                    severity="warning",
                    code="pm_pair_cooldown",
                    message_pl=(
                        "Nie można jeszcze zaplanować kolejnych 2× skróconych OT (PM): "
                        f"w oknie 4 tygodni od {a.start.strftime('%d.%m')} wymagane są "
                        f"co najmniej 2 regularne OT 45h (jest {len(regulars)})."
                    ),
                    related_block_ids=a.block_ids + b.block_ids,
                )
            )
        else:
            hints.append(
                f"Po parze PM ({a.start.strftime('%d.%m')}+{b.start.strftime('%d.%m')}): "
                "warunki 4 tygodni spełnione — można ponownie planować 2× skrócone OT."
            )

    if not pm_pairs:
        hints.append(
            "Można zaplanować 2 kolejne skrócone OT (PM), jeśli oba rozpoczną się za granicą."
        )
    elif latest_pm_ready:
        hints.append(
            "Można ponownie zaplanować 2 kolejne skrócone OT (pakiet mobilności)."
        )

    # --- Wymuszony regularny (art. 8(6)) ---
    last = weekly[-1]
    open_week_idx = week_index(scenario.week_start, last.end)

    for widx in range(max(0, open_week_idx - 1), scenario.weeks):
        prev_classes = week_classes.get(widx - 1, []) if widx > 0 else []
        curr_classes = week_classes.get(widx, [])
        if "reduced" not in prev_classes:
            continue
        if "regular" in curr_classes:
            continue
        pair_covers = False
        for a, b in pm_pairs:
            a_idx = week_index(scenario.week_start, a.start)
            b_idx = week_index(scenario.week_start, b.start)
            if widx in range(a_idx, b_idx + 1):
                pair_covers = True
                break
        if pair_covers:
            continue
        if not curr_classes or ("reduced" in curr_classes and "regular" not in curr_classes):
            monday = scenario.week_start + timedelta(days=7 * widx)
            findings.append(
                Finding(
                    severity="warning",
                    code="ot_regular_forced",
                    message_pl=(
                        f"W tygodniu {widx + 1} ({monday.strftime('%d.%m')}–"
                        f"{(monday + timedelta(days=6)).strftime('%d.%m')}) "
                        "wymuszony jest odpoczynek tygodniowy regularny (45h) — "
                        "poprzedni tydzień miał skrócony OT (poza legalną parą PM)."
                    ),
                )
            )
            break

    return findings, hints
