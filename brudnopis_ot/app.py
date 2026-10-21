from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from brudnopis_ot.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Brudnopis OT")
    app.setOrganizationName("BrudnopisOT")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
