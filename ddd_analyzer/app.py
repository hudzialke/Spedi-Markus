from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ddd_analyzer.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("DDD Analyzer")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
