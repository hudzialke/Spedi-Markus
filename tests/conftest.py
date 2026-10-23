import time

import pytest


@pytest.fixture
def wait_for():
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    def wait(predicate, timeout=10000):
        end = time.monotonic() + timeout/1000
        while not predicate():
            assert time.monotonic() < end, 'Background operation did not complete'
            QApplication.processEvents()
            QThread.msleep(5)
        QApplication.processEvents()
    yield wait
    # Always retire synthetic test workers, including when an assertion fails.
    app = QApplication.instance()
    if app:
        for dialog in app.topLevelWidgets():
            if hasattr(dialog, '_pdf_worker') or hasattr(dialog, '_refresh_timer'):
                dialog.close()
                wait(lambda: getattr(dialog, '_pdf_worker', None) is None
                     and getattr(dialog, '_worker', None) is None)
