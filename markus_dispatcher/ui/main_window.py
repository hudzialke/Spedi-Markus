from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from markus_dispatcher.integrations.trans_eu import TransEuClient
from markus_dispatcher.services.history_export import export_history
from markus_dispatcher.ui.clients_pane import ClientsPane
from markus_dispatcher.ui.firebase_dialog import FirebaseSyncDialog
from markus_dispatcher.ui.fleet_pane import FleetPane
from markus_dispatcher.ui.icons import set_tool_icon, tool_button
from markus_dispatcher.ui.job_pane import JobPane


class MainPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fleet_open = False
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        bar = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("MarkusDispatcher")
        title.setObjectName("BrandTitle")
        sub = QLabel(_tr("Kafelki · flota w szufladzie"))
        sub.setObjectName("BrandSub")
        brand.addWidget(title)
        brand.addWidget(sub)
        bar.addLayout(brand)

        self.chip_ops = tool_button(_tr("Flota / zlecenia"), "truck")
        self.chip_ops.setProperty("active", True)
        self.chip_clients = tool_button(_tr("Klienci"), "people")
        self.chip_ops.clicked.connect(lambda: self._show_page(0))
        self.chip_clients.clicked.connect(lambda: self._show_page(1))
        self.fleet_btn = tool_button(
            _tr("Pokaż listę kierowców"),
            "chevron-right",
            label=_tr("Flota"),
        )
        self.fleet_btn.clicked.connect(self._toggle_fleet)
        self.driver_chip = QPushButton(_tr("Wybierz kierowcę"))
        self.driver_chip.setObjectName("DriverChip")
        self.driver_chip.setFlat(True)
        self.driver_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.driver_chip.setToolTip(_tr("Kliknij, żeby otworzyć listę kierowców"))
        self.driver_chip.clicked.connect(self._toggle_fleet)
        sync_fb = tool_button(_tr("Sync Firebase"), "sync")
        sync_fb.clicked.connect(self._sync_firebase)
        export_history_btn = tool_button(_tr("Eksportuj historię zleceń do Excel"), "file")
        export_history_btn.clicked.connect(self._export_history)
        sync_btn = tool_button(_tr("Sync Trans.eu (jeszcze atrapa)"), "grid")
        sync_btn.clicked.connect(self._sync_trans)
        new_btn = tool_button(_tr("Nowe zlecenie"), "plus", kind="primary")
        new_btn.clicked.connect(self._new_job)
        bar.addWidget(self.fleet_btn)
        bar.addWidget(self.driver_chip, stretch=1)
        bar.addWidget(self.chip_ops)
        bar.addWidget(self.chip_clients)
        bar.addWidget(sync_fb)
        bar.addWidget(export_history_btn)
        bar.addWidget(export_history_btn)
        bar.addWidget(sync_btn)
        bar.addWidget(new_btn)
        root.addLayout(bar)

        self.stack = QStackedWidget()
        ops = QWidget()
        ops_layout = QVBoxLayout(ops)
        ops_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)

        left = QFrame()
        left.setObjectName("Lane")
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        self.fleet = FleetPane()
        left_l.addWidget(self.fleet)
        left.setMinimumWidth(0)
        left.setMaximumWidth(320)
        left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.fleet.setMinimumWidth(0)

        right = QFrame()
        right.setObjectName("Lane")
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        self.job = JobPane()
        right_l.addWidget(self.job)

        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setCollapsible(0, True)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([0, 1200])
        ops_layout.addWidget(self.splitter)

        self.clients = ClientsPane()
        self.stack.addWidget(ops)
        self.stack.addWidget(self.clients)
        root.addWidget(self.stack, stretch=1)

        self.fleet.driver_selected.connect(self._on_driver)
        self.job.saved.connect(self._on_job_saved)
        self.fleet.reload()

    def _toggle_fleet(self) -> None:
        self._fleet_open = not self._fleet_open
        self._apply_fleet_open()

    def _apply_fleet_open(self) -> None:
        self.fleet_btn.setProperty("active", self._fleet_open)
        if self._fleet_open:
            set_tool_icon(self.fleet_btn, "chevron-left")
            self.fleet_btn.setToolTip(_tr("Ukryj listę kierowców"))
            self.splitter.setSizes([280, 1000])
        else:
            set_tool_icon(self.fleet_btn, "chevron-right")
            self.fleet_btn.setToolTip(_tr("Pokaż listę kierowców"))
            self.splitter.setSizes([0, 1280])
        self.fleet_btn.style().unpolish(self.fleet_btn)
        self.fleet_btn.style().polish(self.fleet_btn)

    def _on_driver(self, driver_id: int) -> None:
        self.job.bind_driver(driver_id)
        self._refresh_driver_chip()

    def _refresh_driver_chip(self) -> None:
        did = self.fleet.selected_driver_id()
        if did is None:
            self.driver_chip.setText(_tr("Wybierz kierowcę"))
            return
        from markus_dispatcher.data import db

        driver = db.get_driver(did)
        if driver is None:
            self.driver_chip.setText(_tr("Wybierz kierowcę"))
            return
        self.driver_chip.setText(driver.display_name)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.chip_ops.setProperty("active", index == 0)
        self.chip_clients.setProperty("active", index == 1)
        for w in (self.chip_ops, self.chip_clients):
            w.style().unpolish(w)
            w.style().polish(w)
        if index == 1:
            self.clients.reload()

    def _new_job(self) -> None:
        self._show_page(0)
        self.job.new_job(driver_id=self.fleet.selected_driver_id())

    def _on_job_saved(self) -> None:
        self.fleet.reload()
        self.clients.reload()
        self._refresh_driver_chip()

    def _sync_firebase(self) -> None:
        dlg = FirebaseSyncDialog(self)
        dlg.exec()
        self.fleet.reload()
        self.clients.reload()
        if self.fleet.selected_driver_id() is not None:
            self.job.bind_driver(self.fleet.selected_driver_id())
        self._refresh_driver_chip()

    def _export_history(self) -> None:
        try:
            result = export_history()
        except Exception as exc:
            QMessageBox.critical(
                self,
                _tr("Eksport historii zleceń"),
                f"{_tr('Nie udało się utworzyć arkusza Excel.\n\n')}{exc}",
            )
            return
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle(_tr("Eksport historii zleceń"))
        message.setText(
            f"{_tr('Arkusz został zaktualizowany.\n\nZlecenia: ')}{result.job_count}\nDo weryfikacji: {result.verification_count}\n\nPlik: {result.path}"
        )
        open_folder = message.addButton(
            _tr("Otwórz folder"), QMessageBox.ButtonRole.ActionRole
        )
        message.addButton(QMessageBox.StandardButton.Close)
        message.exec()
        if message.clickedButton() is open_folder:
            folder_url = QUrl.fromLocalFile(str(result.path.parent))
            if not QDesktopServices.openUrl(folder_url):
                QMessageBox.warning(
                    self,
                    _tr("Eksport historii zleceń"),
                    _tr("Nie udało się otworzyć folderu eksportu."),
                )

    def _sync_trans(self) -> None:
        client = TransEuClient()
        QMessageBox.information(
            self,
            "MarkusDispatcher",
            _tr("Sync Trans.eu: stub. Po rejestracji API podłączymy "
            "transporty w realizacji i auto-update’y do klientów.")
            if not client.configured
            else _tr("Sync jeszcze niezaimplementowany."),
        )
