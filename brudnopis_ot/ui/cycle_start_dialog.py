from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from brudnopis_ot.core.calendar_weeks import ensure_monday


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


class CycleStartDialog(QDialog):
    """Wybór daty, od której oś Brudnopisu pokazuje cykl pracy."""

    def __init__(
        self,
        parent=None,
        *,
        suggested: date,
        earliest: date | None = None,
        latest: date | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Początek cyklu"))
        hint = QLabel(
            _tr("Oś pokaże aktywność od wybranego poniedziałku. "
            "Pełna historia zostaje w bazie — datę możesz później cofnąć.")
        )
        hint.setWordWrap(True)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        monday = ensure_monday(suggested)
        self.date_edit.setDate(_qdate(monday))
        if earliest:
            self.date_edit.setMinimumDate(_qdate(ensure_monday(earliest)))
        if latest:
            self.date_edit.setMaximumDate(_qdate(latest))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow(_tr("Od dnia (poniedziałek)"), self.date_edit)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def selected_monday(self) -> date:
        qday = self.date_edit.date()
        chosen = date(qday.year(), qday.month(), qday.day())
        return ensure_monday(chosen)
