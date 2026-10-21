from __future__ import annotations

import ctypes
import logging
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMainWindow

from markus_dispatcher.config import APP_NAME, DATA_DIR
from markus_dispatcher.data.db import init_db
from markus_dispatcher.ui.main_window import MainPanel
from markus_dispatcher.ui.theme import apply_theme, app_icon

_LOG_PATH = DATA_DIR / "markus_dispatcher.log"


def _install_crash_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import faulthandler

        fault_log = open(_LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=fault_log)
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(_LOG_PATH, encoding="utf-8"),
        ],
    )

    def _excepthook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        logging.critical(
            "Nieobsłużony wyjątek:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook


def _windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Spedi.MarkusDispatcher"
        )
    except (AttributeError, OSError):
        pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1280, 800)
        self.resize(1440, 900)
        self.setCentralWidget(MainPanel())


def main() -> None:
    _install_crash_logging()
    _windows_app_id()
    init_db()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    apply_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
