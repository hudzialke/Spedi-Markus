from datetime import datetime

from spedi.calculation_details import calculation_rows, calculation_text
from spedi.weekly_watch import calculate, WatchOptions, history_revision
from tests.test_compensation_assignment import crossing_history
from tests.test_weekly_watch import scenario, confirmed


def test_inspector_uses_selected_assignment_and_phone_exports_the_same_due_date():
    from spedi.mobile_schedule import build_driver_schedule
    source = crossing_history()
    options = WatchOptions(verified_revision=history_revision(source))
    result = calculate(source, options)
    rests, events = calculation_rows(result)
    row = next(row for row in events if '11.10.2026 13:29' in row[2])
    assert row[3:] == ('19:36', '09.11.2026 00:00', '12.10.2026')
    phone = build_driver_schedule(profile_key='synthetic', display_name='Synthetic', scenario=source,
                                  cycle_known=True, watch_options=options)
    debt = next(item for item in phone['pending_compensations'] if item['source_block_ids'] == ['crossing'])
    assert debt['compensation_due_at'] == '2026-11-08T23:00:00Z'
    text = calculation_text('Synthetic', result)
    assert '31.10.2026 16:33' in text and '09.11.2026 00:00' in text
    assert len(rests) >= 4


def test_trace_contains_creation_and_full_payment_without_changing_rule_results():
    from brudnopis_ot.core.weekly_rules import analyze_scenario
    source = scenario(24, 66, 24)
    events, pending = [], []
    plain = analyze_scenario(source)
    traced = analyze_scenario(source, pending_debts=pending, compensation_events=events)
    assert plain == traced
    created = [item for item in events if item.kind == 'created']
    paid = [item for item in events if item.kind == 'paid']
    assert created[0].minutes == paid[0].minutes == 21*60
    assert created[0].source_rest_ids == paid[0].source_rest_ids
    assert paid[0].payment_rest_ids


def test_inspector_model_is_lazy_and_does_not_construct_per_cell_widgets(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QTableView
    from spedi.calculation_ui import CalculationDialog, RowsModel
    app = QApplication.instance() or QApplication([])
    source = scenario(24, 25)
    result = calculate(source, confirmed(source))
    dialog = CalculationDialog('Synthetic', result)
    tables = dialog.findChildren(QTableView)
    assert len(tables) == 2 and all(table.model().rowCount() > 0 for table in tables)
    model = RowsModel(['value'], [('synthetic',)]*100_000)
    assert model.rowCount() == 100_000 and model.data(model.index(99_999, 0)) == 'synthetic'
    dialog.reject()
    dialog.deleteLater()
