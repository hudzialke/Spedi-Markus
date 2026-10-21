from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QMessageBox,
)

from markus_dispatcher.data.db import init_db
from markus_dispatcher.ui.main_window import MainPanel

from brudnopis_ot.ui.main_window import MainWindow as BrudnopisWindow
from ewidentor.ui.main_window import MainWindow as EwidentorWindow

from spedi.embed import as_embedded_widget
from spedi.paths import brudnopis_db
from spedi.mobile_sync_ui import MobileSyncWorker
from spedi.i18n import LANGUAGES, language, save_language, tr
from spedi.about import show_about


def _solid_toolbar_buttons(root: QWidget) -> None:
    """Toolbar QAction ma autoRaise — bez tego Fusion maluje sam tekst."""
    for tb in root.findChildren(QToolBar):
        for btn in tb.findChildren(QToolButton):
            btn.setAutoRaise(False)


class ShellWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Spedi")
        self.setMinimumSize(1280, 800)
        self.resize(1480, 920)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("Spedi")
        title.setObjectName("BrandTitle")
        sub = QLabel(_tr("Brudnopis · Ewidentor · Dispatcher"))
        sub.setObjectName("BrandSub")
        brand.addWidget(title)
        brand.addWidget(sub)
        bar.addLayout(brand)
        bar.addStretch(1)
        self._language = QComboBox()
        self._language.setAccessibleName("Language / Język")
        for code, label in LANGUAGES.items():
            self._language.addItem(label, code)
        self._language.setCurrentIndex(self._language.findData(language()))
        self._language.activated.connect(self._change_language)
        bar.addWidget(self._language)
        about = QPushButton(tr(_tr("O programie")))
        about.clicked.connect(lambda: show_about(self))
        bar.addWidget(about)
        self._mobile_worker = None
        self._mobile_sync_button = QPushButton(_tr("Sync monity"))
        self._mobile_sync_button.clicked.connect(self._sync_mobile)
        bar.addWidget(self._mobile_sync_button)

        self._chips: list[QPushButton] = []
        for index, label in enumerate((_tr("Brudnopis OT"), _tr("Ewidentor"), "Dispatcher")):
            chip = QPushButton(label)
            chip.setObjectName("ChipButton")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda checked=False, i=index: self._show_page(i))
            self._chips.append(chip)
            bar.addWidget(chip)
        layout.addLayout(bar)

        self.stack = QStackedWidget()
        brudnopis = BrudnopisWindow()
        self._brudnopis = brudnopis
        from spedi.weekly_watch_ui import install_watchlist
        install_watchlist(brudnopis)
        ewidentor = EwidentorWindow(brudnopis_db=brudnopis_db())
        init_db()
        dispatcher = MainPanel()
        self.stack.addWidget(as_embedded_widget(brudnopis))
        self.stack.addWidget(as_embedded_widget(ewidentor))
        self.stack.addWidget(dispatcher)
        layout.addWidget(self.stack, stretch=1)

        self.setCentralWidget(root)
        _solid_toolbar_buttons(self)
        self._show_page(0)
        self._mobile_timer = QTimer(self)
        self._mobile_timer.setInterval(60_000)
        self._mobile_timer.timeout.connect(self._auto_sync_mobile)
        self._mobile_timer.start()
        QTimer.singleShot(5000, self._auto_sync_mobile)

    def _change_language(self, index):
        selected = self._language.itemData(index)
        try:
            save_language(selected)
        except OSError:
            self._language.setCurrentIndex(self._language.findData(language()))
            QMessageBox.warning(self, "Spedi", tr(_tr("Nie udało się zapisać ustawienia języka.")))
            return
        QMessageBox.information(
            self, "Spedi",
            "Language saved. Save your work and restart Spedi to use English."
            if selected == "en" else "Język zapisany. Zapisz swoją pracę i uruchom Spedi ponownie, aby używać języka polskiego.",
        )

    def _auto_sync_mobile(self):
        from markus_dispatcher import config
        # No implicit configuration creation or network retries without credentials.
        if config.FIREBASE_SERVICE_ACCOUNT.exists() and config.FIREBASE_CONFIG.exists():
            self._sync_mobile()

    def _sync_mobile(self):
        if self._mobile_worker is not None and self._mobile_worker.isRunning():
            return
        if getattr(self._brudnopis, "_dirty", False):
            self.statusBar().showMessage(_tr("Zapisz korekty Brudnopisu przed synchronizacją monitów."), 10_000)
            return
        self._mobile_sync_button.setEnabled(False)
        self.statusBar().showMessage(_tr("Synchronizacja monitów kierowców…"))
        self._mobile_worker = MobileSyncWorker(brudnopis_db(), self)
        self._mobile_worker.completed.connect(self._mobile_done)
        self._mobile_worker.failed.connect(self._mobile_failed)
        self._mobile_worker.start()

    def _mobile_done(self, result):
        self._mobile_sync_button.setEnabled(True)
        self.statusBar().showMessage(result.message, 30_000)

    def _mobile_failed(self, message):
        self._mobile_sync_button.setEnabled(True)
        self.statusBar().showMessage(_tr("Monity: ") + message, 30_000)

    def closeEvent(self, event):
        if self._mobile_worker is not None and self._mobile_worker.isRunning():
            self.statusBar().showMessage(_tr("Trwa synchronizacja. Zamknij program po jej zakończeniu."), 10_000)
            event.ignore()
            return
        self._mobile_timer.stop()
        super().closeEvent(event)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, chip in enumerate(self._chips):
            chip.setProperty("active", i == index)
            chip.style().unpolish(chip)
            chip.style().polish(chip)
