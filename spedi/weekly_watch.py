"""Local weekly-rest watchlist. Calendar windows never use the week-1 anchor.

Scope: Regulation 561/2006, goods transport, art. 8(6), (6b), (7), (9).
The result describes the next rest at its latest start, including planned blocks.
Unknown history/geography is deliberately not a permission to use a derogation.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from brudnopis_ot.core.models import ActivityKind, Scenario, WeeklyRestPeriod, copy_scenario
from brudnopis_ot.core.serialize import scenario_to_dict
from spedi.mobile_rules import analyze_scenario, build_weekly_rests, detect_mobility_pairs

WARSAW = ZoneInfo("Europe/Warsaw")
COUNTRIES = frozenset("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split())


def country_code(value: str | None) -> str:
    value = (value or "").strip().upper()
    return {"D": "DE", "A": "AT", "B": "BE", "F": "FR", "E": "ES", "P": "PT",
            "I": "IT", "N": "NO", "S": "SE", "FIN": "FI", "UK": "GB"}.get(value, value)


def monday(value: datetime) -> date:
    local = value.replace(tzinfo=timezone.utc).astimezone(WARSAW).date()
    return local - timedelta(days=local.weekday())


def midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, WARSAW).astimezone(timezone.utc).replace(tzinfo=None)


def history_revision(scenario: Scenario) -> str:
    # Moving the axis/anchor must not discard or invalidate the rest history.
    data = scenario_to_dict(scenario)
    return sha256(json.dumps({"blocks": sorted(data["blocks"], key=lambda b: b["id"]),
                              "employer_country": scenario.employer_country},
                             sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class WatchOptions:
    selected: bool = False
    residence_country: str = ""
    international_goods: bool = False
    verified_revision: str = ""


class WatchStore:
    """Independent additive table in the profile DB; keys survive driver renames."""
    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS weekly_watch (
                profile_key TEXT PRIMARY KEY, selected INTEGER NOT NULL DEFAULT 0,
                residence_country TEXT NOT NULL DEFAULT '', international_goods INTEGER NOT NULL DEFAULT 0,
                verified_revision TEXT NOT NULL DEFAULT '')""")

    def get(self, key: str) -> WatchOptions:
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute("SELECT selected, residence_country, international_goods, verified_revision "
                               "FROM weekly_watch WHERE profile_key=?", (key,)).fetchone()
        return WatchOptions(bool(row[0]), row[1], bool(row[2]), row[3]) if row else WatchOptions()

    def save(self, key: str, options: WatchOptions) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO weekly_watch VALUES (?, ?, ?, ?, ?)",
                         (key, options.selected, options.residence_country.strip().upper(),
                          options.international_goods, options.verified_revision))


@dataclass(frozen=True)
class WatchResult:
    start_by: datetime | None
    weekly_start_by: datetime | None
    compensation_minutes: int
    reduced: str  # yes / conditional / no / unknown
    reason: str
    last_activity: datetime | None
    history_from: date | None = None
    return_by: datetime | None = None
    return_compensation_minutes: int = 0
    return_rest_minutes: int = 0
    return_reason: str = ""


def load_scenario(store, profile) -> Scenario:
    return (store.load_template_scenario(profile.profile_key) if profile.source_kind == "template"
            else store.load_ddd_scenario(profile.profile_key, original=False))


def _complete(scenario: Scenario, start: datetime, end: datetime) -> bool:
    cursor = start
    for block in scenario.sorted_blocks():
        if block.end <= cursor:
            continue
        if block.start > cursor or block.kind == ActivityKind.CARD_OUT:
            return False
        cursor = max(cursor, block.end)
        if cursor >= end:
            return True
    return cursor >= end


def _conflicts(scenario: Scenario, start: datetime) -> bool:
    ends = {}
    for block in scenario.sorted_blocks():
        if block.end <= start:
            continue
        if any(kind != block.kind and end > block.start for kind, end in ends.items()):
            return True
        ends[block.kind] = max(ends.get(block.kind, block.end), block.end)
    return False


def _abroad(rest: WeeklyRestPeriod, employer: str, residence: str) -> bool | None:
    country = country_code(rest.country_start)
    employer, residence = country_code(employer), country_code(residence)
    # The old checkbox only certifies "outside employer country".
    if country and country in (employer, residence):
        return False
    if any(c not in COUNTRIES for c in (country, employer, residence)):
        return None
    return country not in (employer, residence)


def _assignment_choices(rest: WeeklyRestPeriod):
    def weeks(period):
        a, b = monday(period.start), monday(period.end - timedelta(microseconds=1))
        return [a + timedelta(weeks=n) for n in range((b-a).days // 7 + 1)]

    # TRACE 2, section B: >=69h may consist of two consecutive weekly rests
    # assigned to separate fixed weeks. Compensation hours cannot also fund
    # a second weekly rest. Do not simply duplicate a 45h boundary rest.
    base_start = rest.start + timedelta(hours=rest.compensation_hours)
    hours = (rest.end - base_start).total_seconds() / 3600
    base = replace(rest, start=base_start, compensation_hours=0)
    choices = [((day, base),) for day in weeks(base)]
    if hours < 69 or monday(base_start) == monday(rest.end - timedelta(microseconds=1)):
        return choices
    splits = [(45, "regular", "regular")] if hours >= 90 else [(45, "regular", "reduced"), (24, "reduced", "regular")]
    for length, first_class, second_class in splits:
        boundary = base_start + timedelta(hours=length)
        first = replace(rest, start=base_start, end=boundary, classification=first_class, compensation_hours=0)
        second = replace(rest, start=boundary, classification=second_class, compensation_hours=0)
        for left, right in product(weeks(first), weeks(second)):
            if right == left + timedelta(weeks=1):
                choices.append(((left, first), (right, second)))
    return choices


def _permissions(weekly: list[WeeklyRestPeriod], deadline: datetime, scenario: Scenario,
                 options: WatchOptions) -> tuple[str, str]:
    """Try each art. 8(9) assignment once, never credit a rest to two weeks.

    The hypothetical next 24h rest is abroad only conditionally. Additional rests
    count towards the minimum totals, so there is no invented 'max 2 reductions'.
    """
    week = monday(deadline)
    first = week - timedelta(weeks=3)
    relevant = [r for r in weekly if r.end > midnight(first) and r.start <= deadline]
    choices = [_assignment_choices(rest) for rest in relevant]
    combinations = 1
    for choice in choices:
        combinations *= len(choice)
    if combinations > 4096:
        return "unknown", "Zbyt wiele niejednoznacznych przypisań OT do tygodni; sprawdź historię."
    possible_unknown = False
    for assignment in product(*choices):
        assigned = [entry for rest_entries in assignment for entry in rest_entries]
        window = [(day, rest) for day, rest in assigned if first <= day <= week]
        # Include the proposed rest once in its start week. At this deadline it
        # cannot end in the following week for assignment purposes without changing
        # the stated evaluation week; earlier/later plans require a new calculation.
        if len(window) + 1 < 4 or sum(r.classification == "regular" for _, r in window) < 2:
            continue
        previous = week - timedelta(weeks=1)
        two = [(day, r) for day, r in assigned if previous <= day <= week]
        if len(two) + 1 < 2:
            continue
        if any(r.classification == "regular" for _, r in two):
            return "yes", "Można skrócić do co najmniej 24 h na wskazany termin; zachowane minima 2 i 4 tygodni."
        # Second reduced weekly rest under the international-goods derogation.
        prior = [r for day, r in two if day == previous and r.classification == "reduced"]
        if not prior:
            continue
        # After two required reductions a regular rest must follow. An extra
        # reduction within a week is not automatically a new mobility pair.
        before = previous - timedelta(weeks=1)
        earlier = [r for day, r in assigned if day == before]
        if earlier and not any(r.classification == "regular" for r in earlier):
            continue
        rest = max(prior, key=lambda r: r.end)
        geography = _abroad(rest, scenario.employer_country, options.residence_country)
        if geography is False:
            continue
        if geography is None or not options.international_goods:
            possible_unknown = True
            continue
        return "conditional", ("Można jako drugi skrócony wyłącznie w międzynarodowym przewozie rzeczy, "
                                "ze startem poza krajem siedziby i zamieszkania. Następny OT: obie rekompensaty + 45 h.")
    if possible_unknown:
        return "unknown", "Drugi skrócony wymaga potwierdzenia przewozu międzynarodowego i krajów rozpoczęcia/zamieszkania."
    return "no", "Na ten termin wymagany regularny OT: minima w ruchomych 2/4 tygodniach albo obowiązek po dwóch skróconych."


def _compensation_assignments(weekly, week1, regular_limit):
    """Feasible art. 8(9) assignments for an ordinary four-week return plan.

    One crossing rest counts once. A proposed regular rest counts in week 4.
    Never postpone a debt independently of the other rests' assignments. A
    bounded search falls back to the existing conservative start-week dates.
    Mobility-pair obligations are handled separately by the caller.
    """
    week4 = week1 + timedelta(weeks=3)
    relevant = [r for r in weekly if r.end > midnight(week1)
                and r.start < midnight(week4) and r.end <= regular_limit]
    if any(right.start > left.end + timedelta(hours=144)
           for left, right in zip(relevant, relevant[1:])):
        yield {}
        return
    choices = []
    combinations = 1
    for rest in relevant:
        alternatives = set()
        retained = []
        for entries in _assignment_choices(rest):
            # Splitting 69–89h into 24h+45h would create a new compensation
            # debt absent from pending. Do not use that artificial reduction
            # to relax the arrival plan. Two regular rests (>=90h) are safe.
            if len(entries) > 1 and any(item.classification == 'reduced' for _, item in entries):
                continue
            entries = tuple((day, item) for day, item in entries if week1 <= day <= week4)
            key = tuple((day, item.classification, item.start, item.end) for day, item in entries)
            if key not in alternatives:
                alternatives.add(key)
                retained.append(entries)
        choices.append(retained)
        combinations *= len(retained)
        if combinations > 4096:
            yield {}
            return
    found = False
    for assignment in product(*choices):
        assigned = [entry for entries in assignment for entry in entries]
        valid = True
        for offset in range(3):
            left = week1 + timedelta(weeks=offset)
            right = left + timedelta(weeks=1)
            window = [item for day, item in assigned if left <= day <= right]
            proposed = left <= week4 <= right
            if len(window) + proposed < 2 or not (proposed or any(r.classification == 'regular' for r in window)):
                valid = False
                break
        if valid:
            found = True
            yield {item.block_ids: day for day, item in assigned}
    if not found:
        yield {}


def _home_return(scenario, weekly, debt_scenario, pending, options):
    """Arrival at home/base, with a feasible uninterrupted compensation/45h plan.

    Week 1 is the user-declared departure after a home/base regular rest. An OT
    abroad never resets it. Calendar deadlines use Warsaw; durations use UTC.
    Single compensations may precede OR follow 45h. Only a mobility pair must
    precede it. Do not require repayment of a later single reduction prematurely.
    """
    anchor = scenario.get_block(scenario.anchor_block_id) if scenario.anchor_block_id else None
    week1 = monday(anchor.start) if anchor else scenario.week_start
    departure = anchor.start if anchor else midnight(week1)
    if not weekly and anchor is None:
        return None, 0, 0, "Powrót: brak odpoczynków i oznaczonego startu trasy."
    end = midnight(week1 + timedelta(weeks=4))
    week4 = midnight(week1 + timedelta(weeks=3))
    # Data have minute precision; Monday 00:00 already belongs to week 5.
    regular_limit = end - timedelta(minutes=1)
    before = [r for r in weekly if r.start < week4 and r.end > departure]
    if before:
        next_limit = before[-1].end + timedelta(hours=144)
        if next_limit >= week4:
            regular_limit = min(regular_limit, next_limit)

    # Keep an earlier return obligation visible even if an OT was subsequently
    # entered abroad. Repayment alone does not prove return to the home/base.
    pairs = [(a, b) for a, b in detect_mobility_pairs(
        [r for r in build_weekly_rests(debt_scenario) if r.is_weekly])
        if a.start >= departure]
    pair = min(pairs, key=lambda p: p[1].end) if pairs else None
    pair_minutes = 0
    if pair:
        a, b = pair
        pair_minutes = round((a.debt_hours + b.debt_hours) * 60)
        regular_limit = min(regular_limit, b.end + timedelta(hours=144))

    known_end = max((b.end for b in scenario.blocks), default=departure)
    complete = (options.verified_revision == history_revision(scenario)
                or _complete(scenario, departure, known_end))
    if _conflicts(scenario, departure):
        return None, 0, 0, "Powrót: sprzeczne aktywności — popraw historię kierowcy."
    if not pair and not complete:
        return None, 0, 0, "Powrót: brak pełnej historii od startu tygodnia 1. Uzupełnij ją lub potwierdź kompletność."

    # Correct single-debt calendar deadlines to the display jurisdiction. The
    # legacy analyzer uses naive UTC calendar dates for these deadlines.
    plans = []
    sources = {rest.block_ids: rest for rest in weekly}
    ambiguous = any(not debt.from_pm_pair
                    and (source := sources.get(debt.source_rest_ids)) is not None
                    and monday(source.start) != monday(source.end - timedelta(microseconds=1))
                    for debt in pending)
    assignments = (_compensation_assignments(weekly, week1, regular_limit)
                   if ambiguous and not pair else ({},))
    for assigned in assignments:
        singles = []
        shifted = []
        for debt in pending:
            if debt.from_pm_pair:
                continue
            source = sources.get(debt.source_rest_ids)
            if source is None:
                continue
            source_week = assigned.get(source.block_ids, monday(source.start))
            due = midnight(source_week + timedelta(weeks=4))
            if source_week != monday(source.start):
                shifted.append((source_week, due))
            if due <= end:
                singles.append((due, round(debt.remaining * 60)))
        singles.sort()
        # Earliest deadline first; each compensation stays whole and is used
        # once. Compare complete feasible plans, not per-debt maxima.
        for split in range(len(singles) + 1):
            prefix = pair_minutes + sum(minutes for _, minutes in singles[:split])
            latest = regular_limit - timedelta(minutes=prefix)
            elapsed = pair_minutes
            for index, (due, minutes) in enumerate(singles):
                if index == split:
                    elapsed += 45 * 60
                elapsed += minutes
                latest = min(latest, due - timedelta(minutes=elapsed))
            plans.append((latest, prefix, singles, shifted))
    arrival, prefix, singles, shifted = max(plans, key=lambda p: p[0])
    compensation = pair_minutes + sum(minutes for _, minutes in singles)
    if pair:
        reason = "Powrót przed regularnym OT po dwóch kolejnych skróconych: obie rekompensaty bez przerwy przed 45 h."
        if not any(d.from_pm_pair and set(d.source_rest_ids) == set(pair[0].block_ids + pair[1].block_ids) for d in pending):
            reason += " Rekompensata jest już zapisana; termin pokazuje obowiązek powrotu tej trasy, nie nowy dług. Po powrocie ustaw nowy start tygodnia 1."
    else:
        reason = "Powrót na regularny odpoczynek w 4. tygodniu trasy. Regularny OT w trasie nie zeruje tego okresu."
        if not before or before[-1].end + timedelta(hours=144) < week4:
            reason += " To granica 4. tygodnia; kolejne OT mogą przyspieszyć termin."
    reason += f" Start tygodnia 1: {week1:%d.%m.%Y}."
    for source_week, due in sorted(set(shifted)):
        local_due = due.replace(tzinfo=timezone.utc).astimezone(WARSAW)
        reason += (f" OT obejmujący granicę tygodni przypisano do tygodnia od {source_week:%d.%m.%Y};"
                   f" rekompensata do {local_due:%d.%m.%Y %H:%M}.")
    if compensation:
        after = compensation - prefix
        reason += f" Plan od powrotu: {prefix // 60}:{prefix % 60:02d} rekompensaty + 45:00 OT + {after // 60}:{after % 60:02d} rekompensaty."
        reason += " Pojedyncze skrócenia z późniejszym terminem pozostają do oddania osobno."
    reason += " Termin dotyczy przyjazdu; dojazd nie jest odpoczynkiem."
    return arrival, compensation, 45 * 60 + compensation, reason


def calculate(scenario: Scenario, options: WatchOptions = WatchOptions()) -> WatchResult:
    full = copy_scenario(scenario)
    last_activity = max((b.end for b in full.blocks), default=None)
    # Analyze all stored history, including the part hidden before 'week 1'.
    if full.blocks:
        first_day = min(b.start.date() for b in full.blocks)
        full.week_start = first_day - timedelta(days=first_day.weekday())
        full.weeks = max(1, ((last_activity.date() - full.week_start).days // 7) + 1)
    weekly = [r for r in build_weekly_rests(full) if r.is_weekly]
    # Only the first block can establish where a continuous rest STARTED.
    for index, rest in enumerate(weekly):
        first_block = min((b for b in full.blocks if b.id in rest.block_ids), key=lambda b: b.start)
        weekly[index] = replace(rest, country_start=first_block.country_start)
    # Additional reduced rests in the same calendar week do not form a new
    # mobility pair. Use the last reduction (closest to the 6x24h deadline), as
    # explained in the Commission's Q&A, while keeping ALL rests for the quota
    # calculation and the last physical rest as the deadline reference.
    groups = {}
    for rest in weekly:
        groups.setdefault(monday(rest.start), []).append(rest)
    extras = set()
    for rests in groups.values():
        reduced = [r for r in rests if r.classification == "reduced"]
        retained = None if any(r.classification == "regular" for r in rests) else (reduced[-1] if reduced else None)
        extras.update(bid for r in reduced if r is not retained for bid in r.block_ids)
    debt_scenario = copy_scenario(full)
    debt_scenario.blocks = [b for b in debt_scenario.blocks if b.id not in extras]
    # Timing obligations after two reductions must not disappear merely because
    # a manually entered block lacks the old 'abroad' checkbox. Geography is
    # checked separately before permitting use of the derogation.
    for block in debt_scenario.blocks:
        if block.kind == ActivityKind.REST:
            block.started_abroad = True
    pending = []
    analyzed = analyze_scenario(debt_scenario, pending_debts=pending)
    # A compensation actually taken late is a historical violation, not another
    # future demand for the same 42h. Allocate capacity only once per later rest.
    late_paid = False
    allocations = {p.block_ids: p.compensation_hours for p in analyzed.weekly_rests}
    for period in analyzed.weekly_rests:
        capacity = max(0, period.duration_hours - 45 - period.compensation_hours)
        for debt in list(pending):
            if debt.from_pm_pair and period.start >= debt.source_end and capacity >= debt.remaining:
                capacity -= debt.remaining
                pending.remove(debt)
                late_paid = True
                allocations[period.block_ids] += debt.remaining
    weekly = [replace(r, compensation_hours=allocations.get(r.block_ids, 0)) for r in weekly
              if r.classification == "regular" or r.duration_hours - allocations.get(r.block_ids, 0) >= 24]
    home = _home_return(scenario, weekly, debt_scenario, pending, options)
    def result(*args):
        return WatchResult(*args, return_by=home[0], return_compensation_minutes=home[1],
                           return_rest_minutes=home[2], return_reason=home[3])
    anchor = full.get_block(full.anchor_block_id) if full.anchor_block_id else None
    reference = weekly[-1].end if weekly else (anchor.start if anchor else None)
    if reference is None:
        return result(None, None, 0, "unknown", "Brak OT ≥24 h lub oznaczonego końca poprzedniego OT (start tygodnia 1).", last_activity)
    deadline = reference + timedelta(hours=144)
    pairs = [d for d in pending if d.from_pm_pair]
    comp = round(sum(d.remaining * 60 for d in pairs))
    starts = [d.latest_start for d in pairs if d.latest_start is not None]
    start = min([deadline, *starts])
    first = monday(deadline) - timedelta(weeks=3)
    if _conflicts(full, midnight(first)):
        return result(start, deadline, comp, "unknown", "Sprzeczne, nakładające się aktywności. Najpierw popraw bloki kierowcy.", last_activity, first)
    if pairs:
        return result(start, deadline, comp, "no",
                           "Po dwóch kolejnych skróconych: obie rekompensaty bez przerwy przed regularnym OT 45 h. "
                           "Wcześniejszy zaległy termin pozostaje widoczny do wyjaśnienia historii.", last_activity, first)
    # Single-reduction deadlines remain separate from the next weekly deadline.
    # Never move the next OT merely because an unrelated old debt is overdue.
    verified = options.verified_revision == history_revision(scenario)
    if not verified and not _complete(full, midnight(first), last_activity or reference):
        return result(start, deadline, 0, "unknown", "Brak pełnej historii odpoczynków w ruchomym oknie 4 tygodni. Uzupełnij ją lub potwierdź kompletność.", last_activity, first)
    status, reason = _permissions(weekly, deadline, full, options)
    if late_paid:
        reason += " W historii rekompensata pary została oddana po terminie."
    overdue = [d for d in pending if not d.from_pm_pair and d.deadline <= deadline]
    if overdue:
        status = "unknown" if status != "no" else status
        reason += " Do wyjaśnienia pozostaje termin wcześniejszej rekompensaty."
    return result(start, deadline, 0, status, reason, last_activity, first)
