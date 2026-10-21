from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from markus_dispatcher.core.models import STATUS_LABELS_PL, Driver, DriverStatus
from markus_dispatcher.data import db
from markus_dispatcher.ui.icons import tool_button


class FleetPane(QWidget):
    """Lewa szpalta Split Lane — lista kierowców."""

    driver_selected = Signal(int)
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drivers: list[Driver] = []
        self._selected_id: int | None = None
        self._buttons: dict[int, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        title = QLabel(_tr("Flota"))
        title.setObjectName("SectionLabel")
        root.addWidget(title)

        tools = QHBoxLayout()
        add_btn = tool_button(_tr("Dodaj kierowcę"), "plus")
        add_btn.clicked.connect(self._add_driver)
        del_btn = tool_button(_tr("Usuń wybranego kierowcę"), "trash")
        del_btn.clicked.connect(self._delete_selected)
        tools.addWidget(add_btn)
        tools.addWidget(del_btn)
        tools.addStretch(1)
        root.addLayout(tools)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll, stretch=1)

        status_row = QHBoxLayout()
        for status, label in (
            ("working", _tr("W pracy")),
            ("empty", _tr("Pusty")),
            ("planned", _tr("Zaplan.")),
            ("not_ready", _tr("Niegotowy")),
        ):
            btn = QPushButton(label)
            btn.setObjectName("GhostButton")
            btn.setToolTip(f"{_tr('Status: ')}{label}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _=False, s=status: self._set_status(s)  # type: ignore[arg-type]
            )
            status_row.addWidget(btn)
        root.addLayout(status_row)

    def reload(self) -> None:
        self._drivers = db.list_drivers()
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._buttons.clear()
        for driver in self._drivers:
            btn = QPushButton()
            btn.setObjectName("DriverRow")
            btn.setCursor(btn.cursor())
            hint = STATUS_LABELS_PL.get(driver.status, _tr("Brak statusu"))
            if not driver.is_active:
                hint = f"{hint} · nieaktywny"
            btn.setText(f"{driver.display_name}\n{hint}")
            btn.clicked.connect(
                lambda _=False, d=driver.id: self._select(d)
            )
            self._list_layout.insertWidget(self._list_layout.count() - 1, btn)
            self._buttons[driver.id] = btn
        if self._selected_id is not None:
            self._apply_selected_style()

    def selected_driver_id(self) -> int | None:
        return self._selected_id

    def _select(self, driver_id: int) -> None:
        self._selected_id = driver_id
        self._apply_selected_style()
        self.driver_selected.emit(driver_id)

    def _apply_selected_style(self) -> None:
        for did, btn in self._buttons.items():
            btn.setProperty("selected", did == self._selected_id)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _add_driver(self) -> None:
        text, ok = QInputDialog.getText(
            self, _tr("Nowy kierowca"), _tr("Imię, nazwisko i wymiary auta:")
        )
        if not ok:
            return
        try:
            from markus_dispatcher.core.models import parse_driver_input

            name = parse_driver_input(text)
        except ValueError as exc:
            QMessageBox.warning(self, "MarkusDispatcher", str(exc))
            return
        driver = db.add_driver(name)
        self.reload()
        self._select(driver.id)
        self.changed.emit()

    def _delete_selected(self) -> None:
        if self._selected_id is None:
            return
        db.delete_driver(self._selected_id)
        self._selected_id = None
        self.reload()
        self.changed.emit()

    def _set_status(self, status: DriverStatus) -> None:
        if self._selected_id is None:
            return
        current = db.get_driver(self._selected_id)
        if current is None:
            return
        new_status: DriverStatus = "" if current.status == status else status
        db.set_driver_status(self._selected_id, new_status)
        self.reload()
        self.changed.emit()
