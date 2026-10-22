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
    migrate_legacy_databases()
    _windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    configure_qt(app)
    apply_theme(app)
    from spedi.shell import ShellWindow

    window = ShellWindow()
    icon = app_icon()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
