from __future__ import annotations

from spedi.i18n import tr as _tr

import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot

from markus_dispatcher.core.geocode import GeocodeError, geocode_address

log = logging.getLogger(__name__)


class GeocodeWorker(QObject):
    """Geokoduje adres w osobnym wątku (nie blokuje UI)."""

    # Tylko typy Qt-safe przez QueuedConnection — dataclass crashuje proces.
    finished = Signal(str, float, float)  # side, lat, lon
    failed = Signal(str, str)  # side, error message

    def __init__(self, side: str, address: str) -> None:
        super().__init__()
        self._side = side
        self._address = address

    @Slot()
    def run(self) -> None:
        try:
            coords = geocode_address(self._address)
            self.finished.emit(self._side, float(coords.lat), float(coords.lon))
        except GeocodeError as exc:
            self.failed.emit(self._side, str(exc))
        except Exception as exc:  # noqa: BLE001 — wątek nie może wywalić procesu
            log.exception("geocode worker crashed for %s", self._side)
            self.failed.emit(self._side, f"{_tr('Błąd geokodowania: ')}{exc}")


def start_geocode(parent: QObject, side: str, address: str) -> QThread:
    """Uruchamia geokodowanie; zwraca wątek (caller trzyma referencję)."""
    thread = QThread(parent)
    worker = GeocodeWorker(side, address)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    # JobPane clears its reference and deletes the thread when finished.
    thread._worker = worker  # type: ignore[attr-defined]
    return thread
