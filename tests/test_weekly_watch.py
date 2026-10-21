from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.models import ActivityKind, Block, Scenario
from brudnopis_ot.core.serialize import scenario_to_dict
from spedi.weekly_watch import WatchOptions, WatchStore, calculate, history_revision, load_scenario, monday


def rest(week, hours, *, country="DE", abroad=True, offset=0):
    start = datetime(2026, 8, 7, 12) + timedelta(weeks=week-1, hours=offset)
    return Block(ActivityKind.REST, start, start + timedelta(hours=hours),
                 country_start=country, started_abroad=abroad)


def scenario(*hours):
    return Scenario(week_start=date(2026, 8, 3), blocks=[rest(i+1, h) for i, h in enumerate(hours)])


def confirmed(sc, **kwargs):
    return WatchOptions(verified_revision=history_revision(sc), residence_country="PL",
                        international_goods=True, **kwargs)


def test_pair_compensation_completed_before_third_weekly_deadline():
    sc = scenario(24, 25)
    result = calculate(sc, confirmed(sc))
    assert result.weekly_start_by == sc.blocks[-1].end + timedelta(hours=144)
    assert result.start_by == result.weekly_start_by - timedelta(hours=41)
    assert result.compensation_minutes == 41 * 60
    assert result.reduced == "no"


def test_pair_does_not_require_week_one_anchor_or_old_abroad_checkbox():
    sc = scenario(24, 24)
    for block in sc.blocks:
        block.started_abroad = False
    result = calculate(sc, confirmed(sc))
    assert result.compensation_minutes == 42 * 60
    assert result.start_by == result.weekly_start_by - timedelta(hours=42)
    assert result.reduced == "no"


def test_axis_does_not_erase_compensation_or_four_week_history():
    sc = scenario(24, 24)
    expected = calculate(sc, confirmed(sc))
    sc.week_start += timedelta(weeks=2)
    sc.anchor_block_id = None
    moved = calculate(sc, confirmed(sc))
    assert (moved.start_by, moved.weekly_start_by, moved.compensation_minutes, moved.reduced) == (
        expected.start_by, expected.weekly_start_by, expected.compensation_minutes, expected.reduced)
    # The weekly calculation keeps its history; a newly declared departure
    # deliberately changes the separate return-home cycle.
    assert moved.return_by != expected.return_by


def test_regular_then_first_reduction_allowed():
    sc = scenario(45, 24, 45)
    assert calculate(sc, confirmed(sc)).reduced == "yes"


def test_second_reduction_conditional_when_two_regulars_in_rolling_four():
    sc = scenario(45, 45, 24)
    assert calculate(sc, confirmed(sc)).reduced == "conditional"


def test_second_reduction_cannot_start_in_residence_country():
    sc = scenario(45, 45, 24)
    sc.blocks[-1].country_start = "UA"
    options = replace(confirmed(sc), residence_country="UA")
    assert calculate(sc, options).reduced == "no"


@pytest.mark.parametrize("country,international", [("", True), ("PL", False)])
def test_missing_derogation_conditions_not_green(country, international):
    sc = scenario(45, 45, 24)
    options = replace(confirmed(sc), residence_country=country, international_goods=international)
    assert calculate(sc, options).reduced == "unknown"


def test_legacy_abroad_checkbox_is_not_proof_of_outside_residence():
    sc = scenario(45, 45, 24)
    sc.blocks[-1].country_start = None
    assert calculate(sc, confirmed(sc)).reduced == "unknown"


def test_next_reduction_after_pair_and_one_regular_fails_rolling_window():
    sc = scenario(24, 24, 87)
    sc.blocks[-1] = rest(3, 87, offset=-48)
    result = calculate(sc, confirmed(sc))
    assert result.compensation_minutes == 0
    assert result.reduced == "no"


def test_second_pair_after_two_regulars_can_be_planned():
    sc = scenario(24, 24, 87, 45, 24)
    sc.blocks[2] = rest(3, 87, offset=-48)
    assert calculate(sc, confirmed(sc)).reduced == "conditional"


def test_extra_reductions_are_not_a_blanket_two_per_four_week_ban():
    sc = scenario(45, 24, 45)
    # Additional 24h in a week already containing a regular rest.
    sc.blocks.append(rest(1, 24, offset=-72))
    result = calculate(sc, confirmed(sc))
    assert result.reduced == "yes"


def test_pair_uses_last_required_reduction_not_extra_midweek_rest():
    sc = scenario(24, 27)
    sc.blocks.append(rest(2, 24, offset=-72))
    result = calculate(sc, confirmed(sc))
    assert result.compensation_minutes == 39 * 60
    assert result.start_by == result.weekly_start_by - timedelta(hours=39)


def test_country_tachograph_alias_is_not_mistaken_for_another_country():
    sc = scenario(45, 45, 24)
    sc.blocks[-1].country_start = "D"
    assert calculate(sc, replace(confirmed(sc), residence_country="DE")).reduced == "no"


def test_conflicting_blocks_cannot_be_overridden_by_history_confirmation():
    sc = scenario(45, 24, 45)
    sc.blocks.append(Block(ActivityKind.DRIVE, sc.blocks[-1].start, sc.blocks[-1].start + timedelta(hours=1)))
    result = calculate(sc, confirmed(sc))
    assert result.reduced == "unknown"
    assert "Sprzeczne" in result.reason


def test_country_only_on_second_piece_is_not_proof_of_rest_start_country():
    sc = scenario(45, 45, 24)
    block = sc.blocks.pop()
    sc.blocks.append(replace(block, end=block.start + timedelta(hours=12), country_start=None))
    sc.blocks.append(replace(block, id="second-piece", start=block.start + timedelta(hours=12)))
    assert calculate(sc, confirmed(sc)).reduced == "unknown"


def test_boundary_rest_not_credited_as_two_regular_rests():
    sc = scenario(24, 24)
    # 90h minus 42h compensation leaves only one regular rest, not two.
    sc.blocks.append(rest(3, 90))
    assert calculate(sc, confirmed(sc)).reduced == "no"


def test_two_regulars_in_90h_can_be_credited_to_separate_weeks():
    sc = scenario(45, 24, 90)
    # 21h repays week 2, leaving 69h: regular + reduced, not 2 regulars.
    assert calculate(sc, confirmed(sc)).reduced == "no"
    sc = scenario(45, 45, 90)
    assert calculate(sc, confirmed(sc)).reduced == "yes"


def test_compensation_is_not_counted_as_regular_rest_before_monday():
    from spedi.weekly_watch import _assignment_choices
    from spedi.mobile_rules import build_weekly_rests
    sc = scenario(87)
    block = sc.blocks[0]
    # Sunday 02:00 UTC + 42h = Monday 20:00 UTC: regular part is week 2.
    block.start = datetime(2026, 8, 9, 2)
    block.end = block.start + timedelta(hours=87)
    period = replace(build_weekly_rests(sc)[0], compensation_hours=42)
    choices = _assignment_choices(period)
    assert all(day == date(2026, 8, 10) for choice in choices for day, _ in choice)


def test_missing_history_unknown_until_explicitly_confirmed():
    sc = scenario(45, 45, 24)
    assert calculate(sc).reduced == "unknown"
    assert calculate(sc, confirmed(sc)).reduced == "conditional"


def test_history_confirmation_invalidated_by_edit_but_not_axis_change():
    sc = scenario(45, 45, 24)
    options = confirmed(sc)
    sc.week_start += timedelta(weeks=2)
    assert calculate(sc, options).reduced == "conditional"
    sc.blocks[0].country_start = "FR"
    assert calculate(sc, options).reduced == "unknown"


def test_continuous_four_week_data_needs_no_manual_confirmation():
    sc = scenario(45, 24, 45)
    cursor = datetime(2026, 8, 2, 22)  # local Mon 00:00
    filled = []
    for block in sc.sorted_blocks():
        if cursor < block.start:
            filled.append(Block(ActivityKind.WORK, cursor, block.start))
        filled.append(block)
        cursor = block.end
    sc.blocks = filled
    assert calculate(sc).reduced == "yes"


def test_unknown_card_out_is_not_automatic_complete_history():
    sc = scenario(45, 24, 45)
    sc.blocks.append(Block(ActivityKind.CARD_OUT, datetime(2026, 8, 2, 22), sc.blocks[0].start))
    assert calculate(sc).reduced == "unknown"


def test_missing_reference_and_anchor_fallback():
    sc = Scenario(week_start=date(2026, 8, 3))
    assert calculate(sc).start_by is None
    block = Block(ActivityKind.WORK, datetime(2026, 8, 3, 6), datetime(2026, 8, 3, 7))
    sc.blocks.append(block)
    sc.anchor_block_id = block.id
    assert calculate(sc).start_by == block.start + timedelta(hours=144)
    assert calculate(sc).reduced == "unknown"


def test_naive_times_are_utc_and_calendar_weeks_are_warsaw():
    assert monday(datetime(2026, 10, 25, 23, 30)) == date(2026, 10, 26)
    assert monday(datetime(2026, 10, 25, 22, 30)) == date(2026, 10, 19)


def test_does_not_mutate_source_or_shift_actual_144_hours_across_dst():
    sc = Scenario(week_start=date(2026, 10, 19), blocks=[
        Block(ActivityKind.REST, datetime(2026, 10, 23, 6), datetime(2026, 10, 25, 3))])
    before = scenario_to_dict(deepcopy(sc))
    assert calculate(sc).weekly_start_by == datetime(2026, 10, 31, 3)
    assert scenario_to_dict(sc) == before


def test_manual_profiles_selection_and_options_persist_by_identity(tmp_path):
    store = DddProfileStore(tmp_path / "profiles.sqlite")
    first = store.create_template("Jan", date(2026, 8, 3))
    second = store.create_template("Jan", date(2026, 8, 3))
    sc = scenario(45, 45, 24)
    sc.title = "Jan"
    store.save_template_scenario(first.profile_key, sc)
    settings = WatchStore(store.path)
    options = replace(confirmed(sc), selected=True)
    settings.save(first.profile_key, options)
    assert WatchStore(store.path).get(first.profile_key) == options
    assert not settings.get(second.profile_key).selected
    reopened = load_scenario(store, first)
    assert calculate(reopened, options).reduced == "conditional"
    reopened.title = "Jan Kowalski"
    store.save_template_scenario(first.profile_key, reopened)
    assert settings.get(first.profile_key).selected


def test_ddd_modified_history_is_loaded_even_before_axis_start(tmp_path):
    from brudnopis_ot.core.ddd_file_loader import DddLoadResult
    from brudnopis_ot.core.hudonizator_reader import RawSegment
    store = DddProfileStore(tmp_path / "profiles.sqlite")
    day = date(2026, 8, 3)
    profile = DddLoadResult([RawSegment(day, "drive", 60, 120)], "Jan", "Jan", "ABC", None,
                            "card", ("sample.ddd",), day, day)
    store.upsert(profile)
    changed = scenario(24, 24)
    changed.week_start = date(2026, 8, 17)
    store.save_ddd_scenario(profile.profile_key, changed)
    full = load_scenario(store, profile)
    assert len(full.blocks) == 2
    assert min(b.start.date() for b in full.blocks) < full.week_start
    assert calculate(full, confirmed(full)).compensation_minutes == 42 * 60
    original = store.load_ddd_scenario(profile.profile_key, original=True)
    assert original.blocks[0].kind == ActivityKind.DRIVE


def test_late_pair_compensation_is_not_demanded_again():
    sc = scenario(24, 24, 87)
    result = calculate(sc, confirmed(sc))
    assert result.compensation_minutes == 0
    assert result.start_by == result.weekly_start_by


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_selection_filter_sorting_and_reopen(app, tmp_path):
    from PySide6.QtCore import Qt
    from spedi.weekly_watch_ui import WeeklyWatchDialog
    store = DddProfileStore(tmp_path / "profiles.sqlite")
    first = store.create_template("Jan", date(2026, 8, 3))
    store.create_template("Jan", date(2026, 8, 3))
    sc = scenario(24, 24)
    sc.title = "Jan"
    store.save_template_scenario(first.profile_key, sc)
    dialog = WeeklyWatchDialog(store)
    assert all(dialog.table.isRowHidden(r) for r in range(2))
    dialog._choose()
    for row in range(2):
        if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == first.profile_key:
            dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    dialog.only_selected.setChecked(True)
    assert sum(not dialog.table.isRowHidden(r) for r in range(2)) == 1
    dialog.reload()
    assert sum(not dialog.table.isRowHidden(r) for r in range(2)) == 1
    assert dialog.settings.get(first.profile_key).selected
    assert dialog.table.item(0, 2).text() != "—"
    dialog.close()


def test_toolbar_action_belongs_to_brudnopis_not_nested_analyzer(app, monkeypatch):
    from PySide6.QtWidgets import QMainWindow, QToolBar
    from spedi import weekly_watch_ui as ui
    window = QMainWindow()
    nested = QMainWindow(window)
    nested.addToolBar(QToolBar("Analyzer", nested))
    toolbar = QToolBar("Plik", window)
    toolbar.addAction("Nowy")
    window.addToolBar(toolbar)
    called = []
    monkeypatch.setattr(ui, "open_watchlist", lambda win: called.append(win))
    action = ui.install_watchlist(window)
    assert toolbar.actions()[0] is action
    action.trigger()
    assert called == [window]
    window.close()


def test_failed_autosave_does_not_display_stale_schedule(monkeypatch):
    from types import SimpleNamespace
    from spedi import weekly_watch_ui as ui
    opened = []
    monkeypatch.setattr(ui, "WeeklyWatchDialog", lambda *a: opened.append(a))
    window = SimpleNamespace(_dirty=True, _ddd_profile=object(), _is_template=lambda: True,
                             _persist_template=lambda: False)
    ui.open_watchlist(window)
    assert not opened


def test_import_conflicts_cannot_show_green_permission(app, tmp_path):
    from spedi.weekly_watch_ui import WeeklyWatchDialog
    store = DddProfileStore(tmp_path / "profiles.sqlite")
    profile = store.create_template("Jan", date(2026, 8, 3))
    sc = scenario(45, 24, 45)
    store.save_template_scenario(profile.profile_key, sc)
    WatchStore(store.path).save(profile.profile_key, confirmed(sc))
    store.add_data_issue(profile.profile_key, sc.blocks[-1].start, sc.blocks[-1].end, "conflict")
    dialog = WeeklyWatchDialog(store)
    assert dialog.results[profile.profile_key].reduced == "unknown"
    assert dialog.results[profile.profile_key].return_by is None
    dialog.close()


def test_return_after_two_reductions_is_before_third_rest_and_includes_both_debts():
    sc = scenario(24, 25)
    result = calculate(sc, confirmed(sc))
    assert result.return_by == sc.blocks[-1].end + timedelta(hours=144 - 41)
    assert result.return_compensation_minutes == 41 * 60
    assert result.return_rest_minutes == 86 * 60
    assert "przed 45 h" in result.return_reason


def test_return_alternating_rests_week4_accounts_for_first_compensation_due():
    sc = scenario(24, 45, 24)
    result = calculate(sc, confirmed(sc))
    # 45h regular + 21h compensation must finish by Sunday 24:00 Warsaw.
    assert result.return_by == datetime(2026, 8, 28, 4)
    assert result.return_by < result.weekly_start_by
    assert result.return_rest_minutes == 66 * 60
    assert result.return_compensation_minutes == 21 * 60
    # Week 3's single reduction is due later, and is not forced into this rest.
    assert "0:00 rekompensaty + 45:00 OT + 21:00 rekompensaty" in result.return_reason


def test_return_does_not_demand_single_compensation_already_paid_in_week2():
    sc = scenario(24, 66, 24)
    result = calculate(sc, confirmed(sc))
    assert result.return_by == sc.blocks[-1].end + timedelta(hours=144)
    assert result.return_compensation_minutes == 0
    assert result.return_rest_minutes == 45 * 60


def test_single_compensation_can_precede_regular_if_that_gives_latest_arrival():
    sc = scenario(24, 45, 24)
    sc.blocks[-1] = rest(3, 24, offset=24)
    result = calculate(sc, confirmed(sc))
    assert result.return_by == datetime(2026, 8, 28, 15)
    assert "21:00 rekompensaty + 45:00 OT + 0:00 rekompensaty" in result.return_reason


def test_foreign_regular_rest_does_not_reset_return_cycle_or_erase_pair_return():
    sc = scenario(24, 24, 87)
    original = calculate(scenario(24, 24), confirmed(scenario(24, 24)))
    result = calculate(sc, confirmed(sc))
    assert result.return_by == original.return_by
    assert "nie nowy dług" in result.return_reason
    assert result.compensation_minutes == 0


def test_new_week1_after_home_rest_resets_return_obligation():
    sc = scenario(24, 24, 87, 45)
    old = calculate(sc, confirmed(sc))
    sc.week_start = date(2026, 8, 24)
    new = calculate(sc, confirmed(sc))
    assert new.return_by > old.return_by
    assert "dwóch kolejnych skróconych" not in new.return_reason


def test_marked_departure_is_independent_of_axis():
    sc = scenario(24, 45, 24)
    anchor = Block(ActivityKind.WORK, datetime(2026, 8, 3, 6), datetime(2026, 8, 3, 7))
    sc.blocks.append(anchor)
    sc.anchor_block_id = anchor.id
    before = calculate(sc, confirmed(sc))
    sc.week_start += timedelta(weeks=2)
    assert calculate(sc, confirmed(sc)).return_by == before.return_by


def test_missing_history_cannot_give_an_ordinary_return_deadline():
    sc = scenario(24, 45, 24)
    assert calculate(sc).return_by is None
    assert calculate(Scenario(week_start=date(2026, 8, 3))).return_by is None


def test_return_calendar_deadline_uses_warsaw_across_dst():
    sc = Scenario(week_start=date(2026, 10, 5), blocks=[
        Block(ActivityKind.REST, datetime(2026, 10, 9, 12), datetime(2026, 10, 10, 12)),
        Block(ActivityKind.REST, datetime(2026, 10, 16, 12), datetime(2026, 10, 18, 9)),
        Block(ActivityKind.REST, datetime(2026, 10, 23, 12), datetime(2026, 10, 24, 12)),
    ])
    result = calculate(sc, confirmed(sc))
    # Sunday Nov 1 24:00 is 23:00 UTC after the clock change.
    assert result.return_by == datetime(2026, 10, 30, 5)
    assert result.return_by + timedelta(hours=66) == datetime(2026, 11, 1, 23)


def test_dialog_puts_return_and_duration_first_and_marks_overdue(app, tmp_path):
    from PySide6.QtCore import Qt
    from spedi.weekly_watch_ui import WeeklyWatchDialog, COLORS, instant
    store = DddProfileStore(tmp_path / "return.sqlite")
    profile = store.create_template("Powrót", date(2026, 8, 3))
    sc = scenario(24, 45, 24)
    store.save_template_scenario(profile.profile_key, sc)
    WatchStore(store.path).save(profile.profile_key, confirmed(sc))
    dialog = WeeklyWatchDialog(store)
    result = dialog.results[profile.profile_key]
    assert dialog.table.horizontalHeader().visualIndex(8) == 2
    assert dialog.table.item(0, 8).text() == instant(result.return_by)
    assert dialog.table.item(0, 8).foreground().color().name() == COLORS["no"]
    assert dialog.table.item(0, 9).text() == "66:00 (45:00 + 21:00)"
    dialog.table.selectRow(0)
    assert result.return_reason in dialog.detail.text()
    dialog.table.sortItems(8, Qt.SortOrder.DescendingOrder)
    dialog.reload()
    assert dialog.results[profile.profile_key].return_by == result.return_by
    dialog.close()
