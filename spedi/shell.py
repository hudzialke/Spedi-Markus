from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt, QTimer, QThread
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
    QMenu,
    QApplication,
)

from brudnopis_ot.ui.main_window import MainWindow as BrudnopisWindow

from spedi.embed import as_embedded_widget
from spedi.paths import brudnopis_db
from spedi.mobile_sync_ui import MobileSyncWorker
from spedi.i18n import LANGUAGES, language, save_language, tr
from spedi.about import show_about
from spedi import __version__


def _solid_toolbar_buttons(root: QWidget) -> None:
    """Toolbar QAction ma autoRaise — bez tego Fusion maluje sam tekst."""
    for tb in root.findChildren(QToolBar):
        for btn in tb.findChildren(QToolButton):
            btn.setAutoRaise(False)


class ShellWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Spedi {__version__}")
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
        sub = QLabel(f"{__version__} · "+_tr("Brudnopis · Ewidentor · Dispatcher"))
        sub.setObjectName("BrandSub")
        brand.addWidget(title)
        brand.addWidget(sub)
        bar.addLayout(brand)
        bar.addStretch(1)
        today = QPushButton(_tr('Dzisiaj'))
        today.clicked.connect(self._open_daily)
        bar.addWidget(today)
        self._language = QComboBox()
        self._language.setAccessibleName("Language / Język")
        for code, label in LANGUAGES.items():
            self._language.addItem(label, code)
        self._language.setCurrentIndex(self._language.findData(language()))
        self._language.activated.connect(self._change_language)
        bar.addWidget(self._language)
        tools_button = QToolButton()
        tools_button.setText(_tr('Narzędzia'))
        tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tools_menu = QMenu(tools_button)
        tools_menu.addAction(_tr('Kopie danych…'), self._open_backups)
        tools_menu.addAction(_tr('Historia korekt profilu…'), self._open_profile_history)
        tools_menu.addAction(_tr('Pokaż wyliczenie terminów…'), self._open_calculation)
        tools_menu.addAction(_tr('Sprawdź aktualizacje…'), lambda: self._check_updates(manual=True))
        from spedi.updates import UpdateService
        from spedi.paths import data_dir
        self._update_service = UpdateService(data_dir()/'update-state.json')
        self._automatic_update_action = tools_menu.addAction(_tr('Sprawdzaj aktualizacje automatycznie'))
        self._automatic_update_action.setCheckable(True)
        self._automatic_update_action.setChecked(self._update_service.automatic)
        self._automatic_update_action.toggled.connect(self._change_auto_updates)
        tools_menu.addSeparator()
        tools_menu.addAction(_tr('O programie'), lambda: show_about(self))
        tools_button.setMenu(tools_menu)
        bar.addWidget(tools_button)
        self._mobile_worker = None
        self._closing_requested = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setInterval(100)
        self._shutdown_timer.timeout.connect(self.close)
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
        self.stack.addWidget(as_embedded_widget(brudnopis))
        # Create secondary modules on their first visit. Their stable stack
        # positions keep navigation and embedded-widget callers unchanged.
        self.stack.addWidget(QWidget())
        self.stack.addWidget(QWidget())
        self._loaded_pages = {0}
        layout.addWidget(self.stack, stretch=1)

        self.setCentralWidget(root)
        _solid_toolbar_buttons(self)
        self._show_page(0)
        self._mobile_timer = QTimer(self)
        self._mobile_timer.setInterval(60_000)
        self._mobile_timer.timeout.connect(self._auto_sync_mobile)
        self._mobile_timer.start()
        QTimer.singleShot(5000, self._auto_sync_mobile)
        self._backup_worker = None
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(3_600_000)
        self._backup_timer.timeout.connect(self._auto_backup)
        self._initial_backup_timer = QTimer(self)
        self._initial_backup_timer.setSingleShot(True)
        self._initial_backup_timer.timeout.connect(self._auto_backup)
        if QApplication.platformName() != 'offscreen':
            self._backup_timer.start()
            self._initial_backup_timer.start(1500)
        self._update_worker = None
        self._show_update_when_ready = False
        self._latest_release = self._update_service.cached()
        self._release_status = QPushButton(_tr('Sprawdź aktualizacje'))
        self._release_status.clicked.connect(lambda: self._check_updates(manual=True))
        self.statusBar().addPermanentWidget(self._release_status)
        self._set_release_status()
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(3_600_000)
        self._update_timer.timeout.connect(self._check_updates)
        self._initial_update_timer = QTimer(self)
        self._initial_update_timer.setSingleShot(True)
        self._initial_update_timer.timeout.connect(self._check_updates)
        if QApplication.platformName() != 'offscreen':
            self._update_timer.start()
            self._initial_update_timer.start(4000)

    def _set_release_status(self):
        text = (_tr('Dostępna wersja: ')+self._latest_release.version
                if self._latest_release and self._latest_release.newer_than() else _tr('Sprawdź aktualizacje'))
        self._release_status.setText(text)

    def _change_auto_updates(self, enabled):
        try:
            self._update_service.set_automatic(enabled)
        except OSError as exc:
            self._automatic_update_action.blockSignals(True)
            self._automatic_update_action.setChecked(not enabled)
            self._automatic_update_action.blockSignals(False)
            QMessageBox.warning(self, _tr('Aktualizacje Spedi'), str(exc))

    def _check_updates(self, *, manual=False):
        if self._closing_requested:
            return
        self._show_update_when_ready |= manual
        if self._update_worker is not None:
            return
        if not manual and not self._update_service.due():
            return
        from spedi.background import BackgroundTask
        worker = BackgroundTask(lambda task: self._update_service.check(), self)
        self._update_worker = worker
        self._release_status.setText(_tr('Sprawdzanie aktualizacji…'))
        def finished():
            self._update_worker = None
            show = self._show_update_when_ready
            self._show_update_when_ready = False
            if worker.error is None:
                self._latest_release = worker.result
            self._set_release_status()
            if show and not self._closing_requested:
                if worker.error is not None:
                    QMessageBox.warning(self, _tr('Nie sprawdzono aktualizacji'), str(worker.error))
                else:
                    self._show_updates()
            worker.deleteLater()
        worker.finished.connect(finished)
        worker.start()

    def _show_updates(self):
        from spedi.update_ui import UpdateDialog
        from spedi.backups import application_backups
        dialog = UpdateDialog(self._latest_release, self._update_service, application_backups(), self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
            self._automatic_update_action.blockSignals(True)
            self._automatic_update_action.setChecked(self._update_service.automatic)
            self._automatic_update_action.blockSignals(False)

    def _auto_backup(self):
        if self._closing_requested or self._backup_worker is not None:
            return
        from spedi.backups import application_backups
        from spedi.background import BackgroundTask
        worker = BackgroundTask(lambda task: application_backups().ensure_daily(cancelled=task.isInterruptionRequested), self)
        self._backup_worker = worker
        def finished():
            self._backup_worker = None
            if worker.error and not isinstance(worker.error, InterruptedError):
                self.statusBar().showMessage(_tr('Nie wykonano kopii danych. Otwórz Narzędzia → Kopie danych.'), 30_000)
            worker.deleteLater()
        worker.finished.connect(finished)
        worker.start()

    def _open_backups(self):
        from spedi.backups import application_backups
        from spedi.backup_ui import BackupDialog
        dialog = BackupDialog(application_backups(), self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _open_profile_history(self):
        window = self._brudnopis
        if not window._ddd_profile:
            QMessageBox.information(self, _tr('Historia korekt profilu'), _tr('Najpierw wybierz profil kierowcy.'))
            return
        if window._dirty and not (window._persist_template() if window._is_template() else window._persist_ddd()):
            return
        from spedi.background import run_background
        from spedi.backup_ui import ProfileHistoryDialog
        path, key, store_type = window._ddd_store.path, window._ddd_profile.profile_key, type(window._ddd_store)
        def restore(revision):
            loaded = run_background(self, _tr('Przywracanie profilu…'),
                                    lambda task: store_type(path).restore_edit_revision(key, revision))
            window._apply_ddd_profile(loaded)
        dialog = ProfileHistoryDialog(window._ddd_store.list_edit_history(key), restore, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _open_calculation(self):
        from spedi.background import run_background
        from spedi.calculation_ui import CalculationDialog
        from spedi.weekly_watch import WatchStore, calculate, load_scenario
        from brudnopis_ot.core.models import copy_scenario
        window = self._brudnopis
        if window._ddd_profile:
            if window._dirty and not (window._persist_template() if window._is_template() else window._persist_ddd()):
                return
            path, key, store_type = window._ddd_store.path, window._ddd_profile.profile_key, type(window._ddd_store)
            def compute(task):
                store = store_type(path)
                profile = store.get(key)
                return profile.profile_label, calculate(load_scenario(store, profile), WatchStore(path).get(key))
        else:
            scenario = copy_scenario(window._scenario)
            def compute(task):
                return scenario.title, calculate(scenario)
        try:
            name, result = run_background(self, _tr('Wyliczanie terminów…'), compute)
            dialog = CalculationDialog(name, result, self)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
        except Exception as exc:
            QMessageBox.warning(self, _tr('Wyliczenie terminów'), str(exc))

    def _open_daily(self):
        from markus_dispatcher import config
        from spedi.daily_ui import DailyOverviewDialog
        dialog = DailyOverviewDialog(self._brudnopis._ddd_store.path, config.DB_PATH, config.ARCHIVE_ROOT, self)
        chosen = None
        try:
            dialog.exec()
            chosen = dialog.chosen
        finally:
            dialog.deleteLater()
        if chosen is None:
            return
        try:
            if chosen.target == 'watchlist':
                from spedi.weekly_watch_ui import open_watchlist
                open_watchlist(self._brudnopis)
            elif chosen.target == 'pod':
                from markus_dispatcher.ui.pod_dialog import PodDialog
                from markus_dispatcher.data.pod_tracking import application_pod_store
                detail = PodDialog(application_pod_store(), chosen.job_id, self)
                try:
                    detail.exec()
                finally:
                    detail.deleteLater()
            elif chosen.profile_key:
                from spedi.background import run_background
                from spedi.calculation_ui import CalculationDialog
                from spedi.weekly_watch import WatchStore, calculate, load_scenario
                path, store_type = self._brudnopis._ddd_store.path, type(self._brudnopis._ddd_store)
                def compute(task):
                    store = store_type(path)
                    profile = store.get(chosen.profile_key)
                    return profile.profile_label, calculate(load_scenario(store, profile), WatchStore(path).get(chosen.profile_key))
                name, result = run_background(self, _tr('Wyliczanie terminów…'), compute)
                detail = CalculationDialog(name, result, self)
                try:
                    detail.exec()
                finally:
                    detail.deleteLater()
        except Exception as exc:
            QMessageBox.warning(self, _tr('Nie otwarto sprawy'), str(exc))

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
        if self._closing_requested:
            return
        from markus_dispatcher import config
        # No implicit configuration creation or network retries without credentials.
        if config.FIREBASE_SERVICE_ACCOUNT.exists() and config.FIREBASE_CONFIG.exists():
            self._sync_mobile()

    def _sync_mobile(self):
        if self._closing_requested:
            return
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
        worker = self._mobile_worker
        worker.finished.connect(lambda: self._mobile_finished(worker))
        self._mobile_worker.start()

    def _mobile_finished(self, worker):
        if self._mobile_worker is worker:
            self._mobile_worker = None
        worker.deleteLater()

    def _mobile_done(self, result):
        self._mobile_sync_button.setEnabled(True)
        self.statusBar().showMessage(result.message, 30_000)

    def _mobile_failed(self, message):
        self._mobile_sync_button.setEnabled(True)
        self.statusBar().showMessage(_tr("Monity: ") + message, 30_000)

    def closeEvent(self, event):
        self._backup_timer.stop()
        self._initial_backup_timer.stop()
        self._update_timer.stop()
        self._initial_update_timer.stop()
        running = [worker for worker in self.findChildren(QThread) if worker.isRunning()]
        if running:
            if not self._closing_requested:
                self._closing_requested = True
                self._mobile_timer.stop()
                self.centralWidget().setEnabled(False)
                if 2 in self._loaded_pages:
                    self.stack.widget(2).job._cancel_geocode()
                for worker in running:
                    worker.requestInterruption()
                self.statusBar().showMessage(_tr('Kończenie zadań w tle przed zamknięciem…'))
                self._shutdown_timer.start()
            event.ignore()
            return
        self._shutdown_timer.stop()
        self._mobile_timer.stop()
        self._closing_requested = True
        super().closeEvent(event)

    def _show_page(self, index: int) -> None:
        if self._closing_requested:
            return
        if index not in self._loaded_pages:
            from importlib import import_module
            from spedi.background import run_background
            if index == 1:
                module = run_background(self, _tr('Ładowanie Ewidentora…'),
                    lambda task: import_module('ewidentor.ui.main_window'))
                page = as_embedded_widget(module.MainWindow(brudnopis_db=brudnopis_db()))
            elif index == 2:
                def load_dispatcher(task):
                    from markus_dispatcher.data.db import init_db
                    module = import_module('markus_dispatcher.ui.main_window')
                    init_db()
                    return module
                module = run_background(self, _tr('Ładowanie Dispatchera…'), load_dispatcher)
                page = module.MainPanel()
            else:
                return
            placeholder = self.stack.widget(index)
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.stack.insertWidget(index, page)
            self._loaded_pages.add(index)
            _solid_toolbar_buttons(page)
        self.stack.setCurrentIndex(index)
        for i, chip in enumerate(self._chips):
            chip.setProperty("active", i == index)
            chip.style().unpolish(chip)
            chip.style().polish(chip)
