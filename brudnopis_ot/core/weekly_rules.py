from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from brudnopis_ot.core.calendar_weeks import (
    format_duration_hm,
    third_week_end_after,
    week_index,
)
from brudnopis_ot.core.display_tz import DEFAULT_ZONE, DisplayZone, format_dt
from brudnopis_ot.core.models import (
    ActivityKind,
    Block,
    Finding,
    Scenario,
    TimeRange,
    ValidationResult,
    WeeklyRestClass,
    WeeklyRestPeriod,
)

REGULAR_HOURS = 45.0
REDUCED_MIN_HOURS = 24.0
COMPENSATION_ATTACH_MIN_HOURS = 9.0
SIX_DAYS = timedelta(days=6)
SPLIT_WEEK_MIN_HOURS = 69.0  # 45 + 24


@dataclass(slots=True)
class _Debt:
    source_rest_ids: tuple[str, ...]
    remaining: float
    deadline: datetime
    source_end: datetime
    from_pm_pair: bool
    latest_start: datetime | None
    label: str


@dataclass(frozen=True, slots=True)
class CompensationEvent:
    kind: str
    source_rest_ids: tuple[str, ...]
    at: datetime
    minutes: int
    deadline: datetime
    payment_rest_ids: tuple[str, ...] = ()


def compensation_hours(duration_hours: float) -> float:
    """Hours owed after a reduced weekly rest (art. 8(6b))."""
    if duration_hours >= REGULAR_HOURS:
        return 0.0
    if duration_hours < REDUCED_MIN_HOURS:
        return 0.0
    return REGULAR_HOURS - duration_hours


def classify_rest_hours(duration_hours: float) -> WeeklyRestClass:
    if duration_hours >= REGULAR_HOURS:
        return "regular"
    if duration_hours >= REDUCED_MIN_HOURS:
        return "reduced"
    if duration_hours > 0:
        return "too_short"
    return "none"


def rest_label(classification: WeeklyRestClass, duration_hours: float) -> str:
    hm = format_duration_hm(duration_hours)
    if classification == "regular":
        return f"h(45) {hm}"
    if classification == "reduced":
        return f"h(24) {hm}"
    if classification == "too_short":
        return f"odpoczynek {hm}"
    return hm


def merge_rest_blocks(
    blocks: list[Block],
) -> list[tuple[tuple[str, ...], datetime, datetime, bool, str | None]]:
    """Merge contiguous REST blocks. MVP: only pure REST counts toward OT."""
    rests = sorted(
        [b for b in blocks if b.kind == ActivityKind.REST],
        key=lambda b: (b.start, b.end),
    )
    if not rests:
        return []

    segments: list[tuple[list[str], datetime, datetime, bool, str | None]] = []
    cur_ids = [rests[0].id]
    cur_start = rests[0].start
    cur_end = rests[0].end
    cur_abroad = rests[0].started_abroad
    cur_country = rests[0].country_start

    for block in rests[1:]:
        if block.start <= cur_end:
            cur_ids.append(block.id)
            if block.end > cur_end:
                cur_end = block.end
            cur_abroad = cur_abroad or block.started_abroad
            if cur_country is None:
                cur_country = block.country_start
        else:
            segments.append((cur_ids, cur_start, cur_end, cur_abroad, cur_country))
            cur_ids = [block.id]
            cur_start = block.start
            cur_end = block.end
            cur_abroad = block.started_abroad
            cur_country = block.country_start
    segments.append((cur_ids, cur_start, cur_end, cur_abroad, cur_country))
    return [(tuple(ids), s, e, a, c) for ids, s, e, a, c in segments]


def build_weekly_rests(scenario: Scenario) -> list[WeeklyRestPeriod]:
    periods: list[WeeklyRestPeriod] = []
    for ids, start, end, abroad, country in merge_rest_blocks(scenario.blocks):
        hours = (end - start).total_seconds() / 3600.0
        classification = classify_rest_hours(hours)
        debt = compensation_hours(hours) if classification == "reduced" else 0.0
        comp_attached = max(0.0, hours - REGULAR_HOURS) if classification == "regular" else 0.0
        periods.append(
            WeeklyRestPeriod(
                block_ids=ids,
                start=start,
                end=end,
                classification=classification,
                started_abroad=abroad,
                country_start=country,
                debt_hours=debt,
                compensation_hours=comp_attached,
                compensation_precedes_regular=False,
                label=rest_label(classification, hours),
            )
        )
    return periods


def _week_assignments(
    period: WeeklyRestPeriod,
    scenario_monday: date,
) -> list[tuple[int, WeeklyRestClass]]:
    """
    Map a weekly rest onto calendar week index(es).
    Rests >= 69h spanning a week boundary can count as 24+45 or 45+24
    in two weeks.
    """
    if period.classification not in ("reduced", "regular"):
        return []

    start_idx = week_index(scenario_monday, period.start)
    end_idx = week_index(scenario_monday, period.end - timedelta(seconds=1))
    hours = period.duration_hours

    if start_idx == end_idx or hours < SPLIT_WEEK_MIN_HOURS:
        return [(start_idx, period.classification)]

    if hours >= 90.0:
        return [(start_idx, "regular"), (end_idx, "regular")]

    boundary = datetime.combine(
        scenario_monday + timedelta(weeks=end_idx),
        datetime.min.time(),
    )
    hours_before = max(0.0, (boundary - period.start).total_seconds() / 3600.0)
    if hours_before >= REGULAR_HOURS:
        return [(start_idx, "regular"), (end_idx, "reduced")]
    if hours_before >= REDUCED_MIN_HOURS:
        return [(start_idx, "reduced"), (end_idx, "regular")]
    return [(start_idx, period.classification)]


def detect_mobility_pairs(
    weekly: list[WeeklyRestPeriod],
) -> list[tuple[WeeklyRestPeriod, WeeklyRestPeriod]]:
    """Two consecutive reduced weekly rests both started abroad → PM pair."""
    reduced = [w for w in weekly if w.classification == "reduced"]
    pairs: list[tuple[WeeklyRestPeriod, WeeklyRestPeriod]] = []
    i = 0
    while i < len(reduced) - 1:
        a, b = reduced[i], reduced[i + 1]
        # Consecutive among all weekly rests (no regular between them)
        between = [
            w
            for w in weekly
            if w.is_weekly and a.end <= w.start < b.start
        ]
        if between:
            i += 1
            continue
        if a.started_abroad and b.started_abroad:
            pairs.append((a, b))
            i += 2
            continue
        i += 1
    return pairs


def _attached_compensation_capacity(
    period: WeeklyRestPeriod,
    *,
    older_debt_remaining: float = 0.0,
) -> float:
    """
    Nadwyżka odpoczynku do doklejenia jako rekompensata (art. 8).

    - regularny OT: R − 45 h
    - skrócony OT (≥24h, <45h): R − 24 h (tylko ponad minimum skróconego)
    - wyjątek OD+rek.: gdy w tym bloku da się oddać cały dług z R−9 i zostaje
      typowy OD 9–11h, traktuj jako OD ≥9h + doklejona rekompensata (R − 9)
    - pozostałe REST ≥9h (dobowy / too_short): R − 9 h

    Spłata długu jest zawsze w całości w jednym bloku (bez rat).
    """
    hours = period.duration_hours
    if hours < COMPENSATION_ATTACH_MIN_HOURS:
        return 0.0
    if period.classification == "regular":
        return max(0.0, hours - REGULAR_HOURS)
    if period.classification == "reduced":
        r9_cap = max(0.0, hours - COMPENSATION_ATTACH_MIN_HOURS)
        if older_debt_remaining > 1e-6 and r9_cap + 1e-6 >= older_debt_remaining:
            residual = hours - older_debt_remaining
            if (
                COMPENSATION_ATTACH_MIN_HOURS - 1e-6
                <= residual
                <= 11.0 + 1e-6
            ):
                return r9_cap
        return max(0.0, hours - REDUCED_MIN_HOURS)
    return max(0.0, hours - COMPENSATION_ATTACH_MIN_HOURS)


def _eligible_debt_remaining(
    period: WeeklyRestPeriod,
    open_debts: list[_Debt],
) -> float:
    total = 0.0
    for debt in open_debts:
        if period.start < debt.source_end or period.start > debt.deadline:
            continue
        total += debt.remaining
    return total


def _rest_anchor_label(period: WeeklyRestPeriod) -> str:
    if period.classification == "regular":
        return "OT"
    if period.classification == "reduced":
        return "odpoczynku"
    return "OD (≥9h)"


def analyze_scenario(
    scenario: Scenario,
    *,
    zone: DisplayZone = DEFAULT_ZONE,
    pending_debts: list[_Debt] | None = None,
    compensation_events: list[CompensationEvent] | None = None,
) -> ValidationResult:
    findings: list[Finding] = []
    from ddd_analyzer.specific_conditions import ferry_intervals, FERRY_REVIEW
    for interval in ferry_intervals(scenario.specific_conditions):
        if not interval.overlaps(scenario.range_start, scenario.range_end):
            continue
        related = tuple(b.id for b in scenario.blocks if interval.overlaps(b.start, b.end))
        findings.append(Finding(severity='info', code='FERRY_TRAIN_REVIEW',
            message_pl=f'{format_dt(interval.start, zone)} · {FERRY_REVIEW}',
            related_block_ids=related))
    hints: list[str] = []
    compensation_windows: list[TimeRange] = []
    weekly_all = build_weekly_rests(scenario)
    weekly = [w for w in weekly_all if w.is_weekly]
    six_windows: list[TimeRange] = []

    def fmt(dt: datetime, short: bool = False) -> str:
        return format_dt(dt, zone, "%d.%m %H:%M" if short else "%d.%m.%Y %H:%M")

    def fmt_day(dt: datetime) -> str:
        return format_dt(dt, zone, "%d.%m")

    # --- 6 × 24h ---
    if weekly:
        for prev, curr in zip(weekly, weekly[1:], strict=False):
            deadline = prev.end + SIX_DAYS
            six_windows.append(TimeRange(start=prev.end, end=min(curr.start, deadline)))
            if curr.start > deadline:
                findings.append(
                    Finding(
                        severity="violation",
                        code="six_x_24h",
                        message_pl=(
                            f"OT musi rozpocząć się najpóźniej "
                            f"{fmt(deadline)} "
                            f"(6×24h od końca poprzedniego OT "
                            f"{fmt(prev.end)})."
                        ),
                        related_block_ids=curr.block_ids + prev.block_ids,
                        underline_range=TimeRange(start=deadline, end=curr.start),
                    )
                )
            else:
                hints.append(
                    f"Max. 6×24h: {fmt(prev.end, short=True)} → "
                    f"deadline {fmt(deadline, short=True)}"
                )

        # Open window after the last weekly rest (even if next OT has not started yet)
        last = weekly[-1]
        open_deadline = last.end + SIX_DAYS
        six_windows.append(TimeRange(start=last.end, end=open_deadline))
        hints.append(
            f"Max. 6×24h (otwarte): {fmt(last.end, short=True)} → "
            f"deadline {fmt(open_deadline, short=True)}"
        )
        hints.append(
            f"Kolejny OT najpóźniej do {fmt(open_deadline, short=True)} "
            f"(koniec poprzedniego OT {fmt(last.end, short=True)})."
        )

    # Kotwica „start”: gdy brak OT w danych, okno 6×24h liczymy od startu bloku-kotwicy
    # (typowe PPM: przejście rest→aktywność = koniec poprzedniego OT).
    if scenario.anchor_block_id:
        anchor = scenario.get_block(scenario.anchor_block_id)
        if anchor is not None:
            prior = [w for w in weekly if w.end <= anchor.start + timedelta(minutes=1)]
            ref = prior[-1].end if prior else anchor.start
            already = any(abs((w.start - ref).total_seconds()) < 60 for w in six_windows)
            if not already:
                open_deadline = ref + SIX_DAYS
                six_windows.append(TimeRange(start=ref, end=open_deadline))
                hints.append(
                    f"Max. 6×24h (od startu): {fmt(ref, short=True)} → "
                    f"deadline {fmt(open_deadline, short=True)}"
                )
                hints.append(
                    f"Kolejny OT najpóźniej do {fmt(open_deadline, short=True)} "
                    f"(kotwica start {fmt(ref, short=True)})."
                )

    # Too-short rests flagged only if >= 24 expected context — skip daily rests.

    # --- Mobility package pairs ---
    pm_pairs = detect_mobility_pairs(weekly)
    pm_rest_ids: set[str] = set()
    pm_timing: dict[int, tuple[datetime, datetime, float]] = {}
    for a, b in pm_pairs:
        pm_rest_ids.update(a.block_ids)
        pm_rest_ids.update(b.block_ids)
        total_compensation = a.debt_hours + b.debt_hours
        regular_start_deadline = b.end + SIX_DAYS
        latest_compensation_start = regular_start_deadline - timedelta(
            hours=total_compensation
        )
        pm_timing[id(b)] = (
            latest_compensation_start,
            regular_start_deadline,
            total_compensation,
        )
        compensation_windows.append(
            TimeRange(
                start=latest_compensation_start,
                end=regular_start_deadline,
            )
        )
        hints.append(
            f"Pakiet mobilności: dwa skrócone OT "
            f"({fmt_day(a.start)} + {fmt_day(b.start)}) rozpoczęte za granicą."
        )
        # In any four consecutive weeks covering the pair: >=4 OT and >=2 regular
        window_start = a.start
        window_end = window_start + timedelta(weeks=4)
        in_window = [w for w in weekly if window_start <= w.start < window_end]
        regulars = [w for w in in_window if w.classification == "regular"]
        if len(in_window) < 4:
            findings.append(
                Finding(
                    severity="warning",
                    code="pm_four_rests",
                    message_pl=(
                        "Pakiet mobilności: w ciągu 4 tygodni od pierwszego z dwóch "
                        f"skróconych OT powinno być co najmniej 4 OT (jest {len(in_window)})."
                    ),
                    related_block_ids=a.block_ids + b.block_ids,
                )
            )
        if len(regulars) < 2:
            findings.append(
                Finding(
                    severity="violation",
                    code="pm_two_regular",
                    message_pl=(
                        "Pakiet mobilności: w oknie 4 tygodni wymagane są co najmniej "
                        f"dwa regularne OT 45h (jest {len(regulars)})."
                    ),
                    related_block_ids=a.block_ids + b.block_ids,
                )
            )

    # --- Compensation debts (art. 8) ---
    # 1) przy REST ≥9h: spłać starsze długi z nadwyżki — TYLKO W CAŁOŚCI (bez rat)
    # 2) skrócony OT tworzy nowy dług tylko gdy po oddanej rek. baza ≥24h
    #    (inaczej cały blok to OD + doklejona rekompensata)
    open_debts: list[_Debt] = []
    consumed_pm: set[int] = set()
    paid_by_blocks: dict[tuple[str, ...], float] = {}
    pm_paid_by_blocks: set[tuple[str, ...]] = set()

    # PM: łączny dług po drugim skróconym — oznacz pary, twórz dług przy końcu b
    pm_second_ids = {id(b): (a, b) for a, b in pm_pairs}
    for a, b in pm_pairs:
        consumed_pm.add(id(a))
        consumed_pm.add(id(b))
        latest_start, regular_start_deadline, _ = pm_timing[id(b)]
        hints.append(
            f"Rekompensata PM: {format_duration_hm(a.debt_hours)}+"
            f"{format_duration_hm(b.debt_hours)}. Najpóźniej zakończ jazdę i rozpocznij "
            f"rekompensaty {fmt(latest_start)}; regularne 45h musi rozpocząć się "
            f"najpóźniej {fmt(regular_start_deadline)}."
        )

    for period in weekly_all:
        eligible = _eligible_debt_remaining(period, open_debts)
        capacity = _attached_compensation_capacity(
            period, older_debt_remaining=eligible
        )
        paid_here = 0.0
        if capacity > 0 and open_debts:
            still: list[_Debt] = []
            for debt in open_debts:
                if period.start < debt.source_end or period.start > debt.deadline:
                    still.append(debt)
                    continue
                # Art. 8 ust. 6b: po dwóch kolejnych skróconych OT obie
                # rekompensaty muszą bezpośrednio poprzedzać regularny OT 45h.
                if debt.from_pm_pair and period.classification != "regular":
                    still.append(debt)
                    continue
                if (
                    debt.from_pm_pair
                    and debt.latest_start is not None
                    and period.start > debt.latest_start
                ):
                    still.append(debt)
                    continue
                # Całość albo nic — nie wolno oddawać rekompensaty w ratach
                if capacity + 1e-6 < debt.remaining:
                    still.append(debt)
                    continue
                take = debt.remaining
                if compensation_events is not None:
                    compensation_events.append(CompensationEvent('paid', debt.source_rest_ids,
                        period.start, round(take*60), debt.deadline, period.block_ids))
                debt.remaining = 0.0
                capacity -= take
                paid_here += take
                if debt.from_pm_pair:
                    pm_paid_by_blocks.add(period.block_ids)
                base_note = (
                    f" (łącznie z bazą 45h → ok. {format_duration_hm(45 + take)})"
                    if debt.from_pm_pair
                    else ""
                )
                hints.append(
                    f"Oddano rekompensatę {format_duration_hm(take)} przy "
                    f"{_rest_anchor_label(period)} {fmt(period.start)}{base_note}."
                )
            open_debts = still
        if paid_here > 1e-6:
            paid_by_blocks[period.block_ids] = (
                paid_by_blocks.get(period.block_ids, 0.0) + paid_here
            )

        # Nowy dług ze skróconego OT (po odjęciu rek. z tego bloku)
        if period.classification != "reduced":
            continue
        residual = period.duration_hours - paid_here
        if residual + 1e-6 < REDUCED_MIN_HOURS:
            # Blok zużyty jako OD (≥9h) + rekompensata — bez nowego długu OT
            continue

        if id(period) in pm_second_ids:
            a, b = pm_second_ids[id(period)]
            total = a.debt_hours + b.debt_hours
            latest_start, regular_start_deadline, _ = pm_timing[id(b)]
            open_debts.append(
                _Debt(
                    source_rest_ids=a.block_ids + b.block_ids,
                    remaining=total,
                    deadline=regular_start_deadline,
                    source_end=b.end,
                    from_pm_pair=True,
                    latest_start=latest_start,
                    label=f"{format_duration_hm(a.debt_hours)}+{format_duration_hm(b.debt_hours)}",
                )
            )
            if compensation_events is not None:
                debt = open_debts[-1]
                compensation_events.append(CompensationEvent('created', debt.source_rest_ids,
                    debt.source_end, round(debt.remaining*60), debt.deadline))
            continue
        if id(period) in consumed_pm:
            # Pierwszy z pary PM — dług powstanie dopiero przy drugim
            continue
        if period.debt_hours <= 0:
            continue
        open_debts.append(
            _Debt(
                source_rest_ids=period.block_ids,
                remaining=period.debt_hours,
                deadline=third_week_end_after(period.start, scenario.week_start),
                source_end=period.end,
                from_pm_pair=False,
                latest_start=None,
                label=format_duration_hm(period.debt_hours),
            )
        )
        if compensation_events is not None:
            debt = open_debts[-1]
            compensation_events.append(CompensationEvent('created', debt.source_rest_ids,
                debt.source_end, round(debt.remaining*60), debt.deadline))

    remaining = open_debts
    if pending_debts is not None:
        pending_debts.extend(remaining)

    # UI: zielony pasek pokazuje tylko rekompensatę faktycznie zaliczoną
    # przez analizator, a nie każdą nadwyżkę ponad 45h.
    weekly_all = [
        WeeklyRestPeriod(
            block_ids=p.block_ids,
            start=p.start,
            end=p.end,
            classification=p.classification,
            started_abroad=p.started_abroad,
            country_start=p.country_start,
            debt_hours=(
                0.0
                if (
                    p.classification == "reduced"
                    and (p.duration_hours - paid_by_blocks.get(p.block_ids, 0.0))
                    + 1e-6
                    < REDUCED_MIN_HOURS
                )
                else p.debt_hours
            ),
            compensation_hours=paid_by_blocks.get(p.block_ids, 0.0),
            compensation_precedes_regular=p.block_ids in pm_paid_by_blocks,
            label=p.label,
        )
        for p in weekly_all
    ]
    weekly = [w for w in weekly_all if w.is_weekly]

    # Deadline passed only if known activity continues past the deadline.
    # Do NOT use scenario.range_end — the display window often ends exactly on
    # the deadline (end of 3rd following week), which wrongly looked like
    # "only 2 weeks" of grace while still inside week W+3.
    last_known = max((b.end for b in scenario.blocks), default=None)
    for debt in remaining:
        left = format_duration_hm(debt.remaining)
        if debt.from_pm_pair and debt.latest_start is not None:
            if last_known is not None and last_known > debt.latest_start:
                findings.append(
                    Finding(
                        severity="violation",
                        code="pm_compensation_start_deadline",
                        message_pl=(
                            f"Za późno na rozpoczęcie łącznych rekompensat {left}: "
                            f"jazdę trzeba było zakończyć najpóźniej {fmt(debt.latest_start)}, "
                            f"aby regularne 45h rozpoczęło się do {fmt(debt.deadline)}."
                        ),
                        related_block_ids=debt.source_rest_ids,
                        underline_range=TimeRange(
                            start=debt.latest_start,
                            end=max(debt.latest_start + timedelta(hours=1), last_known),
                        ),
                    )
                )
            else:
                findings.append(
                    Finding(
                        severity="warning",
                        code="pm_compensation_start_pending",
                        message_pl=(
                            f"Najpóźniej zakończ jazdę i rozpocznij łączne rekompensaty "
                            f"{left} dnia {fmt(debt.latest_start)}; regularne 45h musi "
                            f"rozpocząć się do {fmt(debt.deadline)}."
                        ),
                        related_block_ids=debt.source_rest_ids,
                    )
                )
            continue
        if last_known is not None and last_known >= debt.deadline:
            findings.append(
                Finding(
                    severity="violation",
                    code="compensation_deadline",
                    message_pl=(
                        f"Brak rekompensaty {left} do końca 3. tygodnia "
                        f"(termin {fmt(debt.deadline)})."
                    ),
                    related_block_ids=debt.source_rest_ids,
                    underline_range=TimeRange(
                        start=debt.deadline - timedelta(hours=1),
                        end=debt.deadline,
                    ),
                )
            )
        else:
            findings.append(
                Finding(
                    severity="warning",
                    code="compensation_pending",
                    message_pl=(
                        f"Do oddania rekompensata {left} "
                        f"najpóźniej do {fmt(debt.deadline)}."
                    ),
                    related_block_ids=debt.source_rest_ids,
                )
            )

    # --- Two-week pattern without PM ---
    # In any two consecutive calendar weeks: at least one regular (or two reduced
    # only allowed under PM which is handled above).
    week_classes: dict[int, list[WeeklyRestClass]] = {}
    for w in weekly:
        for widx, cls in _week_assignments(w, scenario.week_start):
            week_classes.setdefault(widx, []).append(cls)

    for widx in range(scenario.weeks - 1):
        left = week_classes.get(widx, [])
        right = week_classes.get(widx + 1, [])
        if not left and not right:
            continue
        # Skip pair-check entirely inside active PM window covering these weeks
        pair_covers = False
        for a, b in pm_pairs:
            a_idx = week_index(scenario.week_start, a.start)
            b_idx = week_index(scenario.week_start, b.start)
            if {widx, widx + 1} <= set(range(a_idx, b_idx + 2)):
                pair_covers = True
                break
        if pair_covers:
            continue
        combined = left + right
        has_regular = "regular" in combined
        has_reduced = "reduced" in combined
        if has_reduced and not has_regular and combined:
            # Two reduced in two weeks without PM is a violation of art. 8(6)
            findings.append(
                Finding(
                    severity="violation",
                    code="two_week_pattern",
                    message_pl=(
                        f"W tygodniach {widx + 1}–{widx + 2} brak regularnego OT 45h "
                        f"(wymagany co najmniej jeden regularny w każdych dwóch "
                        f"kolejnych tygodniach, poza pakietem mobilności)."
                    ),
                )
            )

    # --- Drive limits (art. 6) + planning hints (forced regular / PM cooldown) ---
    from brudnopis_ot.core.drive_limits import analyze_drive_limits, analyze_ot_planning_hints

    drive = analyze_drive_limits(scenario, weekly_all)
    findings.extend(drive.findings)
    hints.extend(drive.hints)

    week_classes_str: dict[int, list[str]] = {
        k: list(v) for k, v in week_classes.items()
    }
    plan_findings, plan_hints = analyze_ot_planning_hints(
        scenario, weekly_all, pm_pairs, week_classes_str
    )
    findings.extend(plan_findings)
    hints.extend(plan_hints)

    # --- Dobowy odpoczynek skrócony do 9h (art. 8 dla OD/daily rest) ---
    from brudnopis_ot.core.daily_rest_limits import analyze_daily_reduced_9h_limits

    daily_findings = analyze_daily_reduced_9h_limits(
        scenario=scenario,
        weekly_all=weekly_all,
        weekly=weekly,
    )
    findings.extend(daily_findings.findings)
    hints.extend(daily_findings.hints)

    if not findings:
        findings.append(
            Finding(
                severity="ok",
                code="ok",
                message_pl="Brak naruszeń odpoczynków tygodniowych w scenariuszu.",
            )
        )

    return ValidationResult(
        findings=tuple(findings),
        weekly_rests=tuple(weekly_all),
        six_day_windows=tuple(six_windows),
        compensation_windows=tuple(compensation_windows),
        compensation_hints=tuple(hints),
    )


def _source_end(periods: list[WeeklyRestPeriod], block_ids: tuple[str, ...]) -> datetime:
    ends: list[datetime] = []
    for w in periods:
        if any(rid in w.block_ids for rid in block_ids):
            ends.append(w.end)
    return max(ends) if ends else datetime.min
