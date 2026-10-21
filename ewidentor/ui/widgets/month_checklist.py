from __future__ import annotations

from spedi.i18n import tr as _tr

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

@dataclass(slots=True)
class ChecklistState:
    sync_ok: bool = False
    days_ok: bool = False
    wn_ok: bool = False
    milo_ok: bool = False
    attachments_ok: bool = False
    pdf_done: bool = False

_STEPS: list[tuple[str, str]] = [
    ("sync", "Sync"),
    ("days", _tr("Dni")),
    ("wn", "Wn"),
    ("milo", "MiLo"),
    ("attachments", _tr("Zał.")),
    ("pdf", "PDF"),
]

class MonthChecklist(QFrame):
    """Step bar zamknięcia miesiąca (1→6) + CTA."""

    jump_requested = Signal(str)  # sync|days|wn|milo|attachments|pdf
    close_month_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("checklistFrame")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel(_tr("Zamknięcie miesiąca"))
        title.setStyleSheet("font-weight: 600;")
        head.addWidget(title)
        self._progress = QLabel("0/6")
        self._progress.setObjectName("sectionHint")
        head.addWidget(self._progress)
        head.addStretch(1)
        self._close_btn = QPushButton(_tr("Zamknij miesiąc…"))
        self._close_btn.setObjectName("primaryButton")
        self._close_btn.clicked.connect(self.close_month_requested.emit)
        head.addWidget(self._close_btn)
        lay.addLayout(head)

        steps_row = QHBoxLayout()
        steps_row.setSpacing(4)
        self._steps: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(_STEPS, start=1):
            if i > 1:
                connector = QLabel("—")
                connector.setObjectName("sectionHint")
                connector.setAlignment(Qt.AlignmentFlag.AlignCenter)
                steps_row.addWidget(connector)
            btn = QPushButton(f"{i} {label}")
            btn.setObjectName("stepButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            btn.setProperty("stepDone", False)
            btn.clicked.connect(lambda checked=False, k=key: self.jump_requested.emit(k))
            self._steps[key] = btn
            steps_row.addWidget(btn)
        steps_row.addStretch(1)
        lay.addLayout(steps_row)

        hint = QLabel(
            _tr("Kliknij krok, aby przejść do sekcji. Status aktualizuje się po wczytaniu miesiąca.")
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

    def set_state(self, state: ChecklistState) -> None:
        mapping = {
            "sync": state.sync_ok,
            "days": state.days_ok,
            "wn": state.wn_ok,
            "milo": state.milo_ok,
            "attachments": state.attachments_ok,
            "pdf": state.pdf_done,
        }
        done = sum(1 for ok in mapping.values() if ok)
        for key, ok in mapping.items():
            btn = self._steps[key]
            btn.setProperty("stepDone", ok)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        self._progress.setText(f"{done}/6")
