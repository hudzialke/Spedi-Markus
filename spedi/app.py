from __future__ import annotations

import ctypes
import sys

from PySide6.QtWidgets import QApplication

from spedi.migrate import migrate_legacy_databases
from spedi.paths import APP_NAME, configure_module_paths
from spedi.ui import apply_theme, app_icon
from spedi.i18n import configure_qt


def _windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Spedi.Spedi")
    except (AttributeError, OSError):
        pass


def main() -> int:
    # Packaging check must precede legacy migration and all user storage access.
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test-report":
        from spedi.release_check import run
        return run(sys.argv[2])
    configure_module_paths()
    _windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    configure_qt(app)
    apply_theme(app)
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QMessageBox
    from spedi.paths import data_dir
    from spedi.i18n import tr
    instance_lock = QLockFile(str(data_dir()/'spedi-instance.lock'))
    if not instance_lock.tryLock(0):
        QMessageBox.information(None, APP_NAME, tr('Spedi jest już uruchomione dla tego katalogu danych.'))
        return 0
    app._instance_lock = instance_lock
    from spedi.backups import application_backups
    from spedi.background import run_background
    backups = application_backups()
    if backups.pending.exists() or backups.journal.exists():
        try:
            state = run_background(None, tr('Przywracanie danych przed uruchomieniem…'), lambda task: backups.apply_pending())
            if state == 'recovered':
                QMessageBox.information(None, APP_NAME, tr('Odzyskano dane sprzed przerwanego przywracania.'))
        except Exception as exc:
            if backups.journal.exists():
                QMessageBox.critical(None, tr('Nie przywrócono danych'), str(exc))
                return 1
            box = QMessageBox(QMessageBox.Icon.Warning, tr('Nie przywrócono danych'), str(exc))
            keep_current = box.addButton(tr('Anuluj przywracanie i użyj bieżących danych'), QMessageBox.ButtonRole.AcceptRole)
            quit_button = box.addButton(tr('Zakończ'), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(quit_button)
            box.exec()
            if box.clickedButton() is not keep_current:
                return 1
            backups.pending.unlink(missing_ok=True)
    migrate_legacy_databases()
    from spedi.shell import ShellWindow

    window = ShellWindow()
    icon = app_icon()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
