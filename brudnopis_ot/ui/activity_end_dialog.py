"""Confirm a terminal activity's end without changing the original DDD record."""
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QDateTimeEdit, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from spedi.i18n import tr as _tr
from brudnopis_ot.core.display_tz import format_dt, utc_naive_to_zone, zone_label, zone_naive_to_utc
from ddd_analyzer.models import ACTIVITY_LABELS_PL


class ActivityEndDialog(QDialog):
    def __init__(self, activity, *, zone, confirmed_end=None, parent=None):
        super().__init__(parent)
        self.activity = activity
        self.zone = zone
        self.end_utc = None
        self.setWindowTitle(_tr('Koniec ostatniej aktywności'))
        layout = QVBoxLayout(self)
        label = QLabel(f'{ACTIVITY_LABELS_PL[activity.activity]} · {format_dt(activity.start, zone)}\n{zone_label(zone)}')
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
        note = QLabel(_tr('DDD podaje początek tej czynności. Wpisz potwierdzony koniec; uzupełnienie zostanie zapisane oddzielnie od oryginału DDD.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.end_edit = QDateTimeEdit(QDateTime(utc_naive_to_zone(confirmed_end or activity.start, zone)))
        self.end_edit.setDisplayFormat('dd.MM.yyyy HH:mm')
        self.end_edit.setCalendarPopup(True)
        layout.addWidget(self.end_edit)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.resize(470, 220)

    def accept(self):
        try:
            end = zone_naive_to_utc(self.end_edit.dateTime().toPython().replace(second=0, microsecond=0), self.zone)
            if end <= self.activity.start:
                raise ValueError(_tr('Koniec musi być późniejszy niż początek aktywności.'))
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.end_utc = end
        super().accept()
