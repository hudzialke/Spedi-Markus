from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ewidentor.ui.main_window import MainWindow
from ewidentor.ui.styles import apply_app_style


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Ewidentor")
    app.setOrganizationName("Ewidentor")
    apply_app_style(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
