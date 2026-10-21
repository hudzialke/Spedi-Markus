from datetime import date, datetime
import importlib

import pytest


DISPLAY_MODULES = (
    "ddd_analyzer.display_tz",
    "brudnopis_ot.core.display_tz",
    "ewidentor.core.display_tz",
)


@pytest.mark.parametrize("module_name", DISPLAY_MODULES)
def test_warsaw_utc_conversion_across_both_transitions(module_name):
    tz = importlib.import_module(module_name)
    assert tz.utc_naive_to_zone(datetime(2026, 3, 29, 0, 30), "Europe/Warsaw") == datetime(2026, 3, 29, 1, 30)
    assert tz.utc_naive_to_zone(datetime(2026, 3, 29, 1, 30), "Europe/Warsaw") == datetime(2026, 3, 29, 3, 30)
    assert tz.utc_naive_to_zone(datetime(2026, 10, 25, 0, 30), "Europe/Warsaw") == datetime(2026, 10, 25, 2, 30)
    assert tz.utc_naive_to_zone(datetime(2026, 10, 25, 1, 30), "Europe/Warsaw") == datetime(2026, 10, 25, 2, 30)


@pytest.mark.parametrize("module_name", DISPLAY_MODULES)
def test_spring_gap_rejected_and_autumn_fold_requires_choice(module_name):
    tz = importlib.import_module(module_name)
    spring = datetime(2026, 3, 29, 2, 30)
    assert tz.local_time_candidates(spring, "Europe/Warsaw") == ()
    with pytest.raises(tz.NonexistentLocalTimeError):
        tz.zone_naive_to_utc(spring, "Europe/Warsaw")

    autumn = datetime(2026, 10, 25, 2, 30)
    choices = tz.local_time_candidates(autumn, "Europe/Warsaw")
    assert [(c.utc, c.offset_label) for c in choices] == [
        (datetime(2026, 10, 25, 0, 30), "UTC+02:00"),
        (datetime(2026, 10, 25, 1, 30), "UTC+01:00"),
    ]
    with pytest.raises(tz.AmbiguousLocalTimeError):
        tz.zone_naive_to_utc(autumn, "Europe/Warsaw")
    assert tz.zone_naive_to_utc(autumn, "Europe/Warsaw", fold=0) == choices[0].utc
    assert tz.zone_naive_to_utc(autumn, "Europe/Warsaw", fold=1) == choices[1].utc


@pytest.mark.parametrize(
    "local,expected",
    [(datetime(2026, 1, 15, 12), datetime(2026, 1, 15, 11)),
     (datetime(2026, 7, 15, 12), datetime(2026, 7, 15, 10))],
)
def test_regular_winter_and_summer_regression(local, expected):
    from ddd_analyzer.display_tz import zone_naive_to_utc
    assert zone_naive_to_utc(local, "Europe/Warsaw") == expected
    assert zone_naive_to_utc(local, "UTC") == local


def test_brudnopis_editor_explicit_second_occurrence_and_reopen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QInputDialog
    from brudnopis_ot.core.models import ActivityKind, Block
    from brudnopis_ot.ui.block_editor import BlockEditorDialog, _to_qdt

    app = QApplication.instance() or QApplication([])
    dialog = BlockEditorDialog(
        block=Block(kind=ActivityKind.WORK, start=datetime(2026, 10, 25, 0), end=datetime(2026, 10, 25, 2)),
        zone="Europe/Warsaw",
    )
    dialog.start.setDateTime(_to_qdt(datetime(2026, 10, 25, 2, 15)))
    dialog.end.setDateTime(_to_qdt(datetime(2026, 10, 25, 3, 15)))
    prompts = []
    def choose_second(*args):
        prompts.append(args[2])
        return args[3][1], True
    monkeypatch.setattr(QInputDialog, "getItem", choose_second)
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    saved = dialog.result_block()
    assert saved.start == datetime(2026, 10, 25, 1, 15)
    assert saved.end == datetime(2026, 10, 25, 2, 15)
    assert prompts and "Start" in prompts[0]

    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: pytest.fail("unchanged UTC must be preserved"))
    reopened = BlockEditorDialog(block=saved, zone="Europe/Warsaw")
    reopened._accept()
    assert reopened.result_block().start == saved.start
    dialog.deleteLater(); reopened.deleteLater(); app.processEvents()


def test_brudnopis_editor_rejects_nonexistent_time(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from brudnopis_ot.ui.block_editor import BlockEditorDialog, _to_qdt

    app = QApplication.instance() or QApplication([])
    dialog = BlockEditorDialog(default_start=datetime(2026, 3, 29, 0), zone="Europe/Warsaw")
    dialog.start.setDateTime(_to_qdt(datetime(2026, 3, 29, 2, 30)))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog._accept()
    assert not dialog.result()
    assert warnings and "nie istnieje" in warnings[0]
    dialog.deleteLater(); app.processEvents()


def test_brudnopis_duration_uses_elapsed_time_across_autumn_fold(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog
    from brudnopis_ot.ui.block_editor import BlockEditorDialog, _to_qdt

    app = QApplication.instance() or QApplication([])
    dialog = BlockEditorDialog(default_start=datetime(2026, 10, 25, 0), zone="Europe/Warsaw")
    dialog.start.setDateTime(_to_qdt(datetime(2026, 10, 25, 2, 15)))
    dialog.duration_hours.setText("1")
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (args[3][0], True))
    dialog._apply_duration()
    dialog._accept()
    block = dialog.result_block()
    assert block.start == datetime(2026, 10, 25, 0, 15)
    assert block.end == datetime(2026, 10, 25, 1, 15)
    assert int((block.end - block.start).total_seconds()) == 3600
    dialog.deleteLater(); app.processEvents()


def test_timeline_click_handles_autumn_choice_and_spring_gap(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from brudnopis_ot.ui.week_grid import WeekGridWidget

    app = QApplication.instance() or QApplication([])
    grid = WeekGridWidget()
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (args[3][1], True))
    assert grid._clicked_local_to_utc(datetime(2026, 10, 25, 2, 30)) == datetime(2026, 10, 25, 1, 30)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    assert grid._clicked_local_to_utc(datetime(2026, 3, 29, 2, 30)) is None
    assert warnings and "nie istnieje" in warnings[0]
    grid.deleteLater(); app.processEvents()


def test_ewidentor_second_occurrence_round_trip_and_spring_gap(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog
    from ewidentor.core.day_overrides import segments_from_override_dicts
    from ewidentor.ui.day_editor_dialog import DayEditorDialog

    app = QApplication.instance() or QApplication([])
    day = date(2026, 10, 25)
    dialog = DayEditorDialog(day=day, segments=[], zone="Europe/Warsaw")
    dialog._append_row(180, 240, "work")
    dialog.table.item(0, 0).setText("02:15")
    dialog.table.item(0, 1).setText("03:15")
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (args[3][1], True))
    dialog._on_save()
    saved = dialog.result_segments()
    assert [(s.start_minute, s.end_minute) for s in saved] == [(75, 135)]
    restored = segments_from_override_dicts(day, dialog.result_override_dicts())
    assert restored == saved

    spring = DayEditorDialog(day=date(2026, 3, 29), segments=[], zone="Europe/Warsaw")
    spring._append_row(60, 180, "work")
    spring.table.item(0, 0).setText("02:30")
    with pytest.raises(ValueError, match="nie istnieje"):
        spring._read_segments(strict=True)
    dialog.deleteLater(); spring.deleteLater(); app.processEvents()
