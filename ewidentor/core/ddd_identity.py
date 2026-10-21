from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class DddIdentityError(Exception):
    """Nie udało się odczytać tożsamości z pliku .ddd."""


@dataclass(frozen=True, slots=True)
class DddIdentity:
    driver_name: str
    card_number: str | None
    vehicle_plate: str | None
    source_kind: str  # card | vu
    filename: str

    @property
    def profile_key(self) -> str:
        if self.source_kind == "vu":
            plate = "".join((self.vehicle_plate or "").split()).upper()
            if plate:
                return f"vu:{plate}"
            return f"vu-file:{self.filename.casefold()}"
        card = (self.card_number or "").strip()
        if card:
            return f"card:{card}"
        return f"name:{(self.driver_name or '').strip().casefold()}"


def ddd_analyzer_available() -> bool:
    try:
        import ddd_analyzer  # noqa: F401
        return True
    except ImportError:
        return False


def read_ddd_identity(path: str | Path) -> DddIdentity:
    """
    Odczytaj z pliku .ddd dane potrzebne Ewidentorowi (nazwa, nr karty, rejestracja).
    Wymaga zainstalowanego ddd-analyzer (ten sam stack co Brudnopis).
    """
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise DddIdentityError(f"Plik nie istnieje:\n{file_path}")

    try:
        from ddd_analyzer.models import DddFileKind
        from ddd_analyzer.parser import parse_ddd_file
    except ImportError as exc:
        raise DddIdentityError(
            "Brak pakietu ddd-analyzer.\n"
            "Zainstaluj: pip install -e ..\\ddd-analyzer\n"
            "(parser kart używany też przez Brudnopis OT)."
        ) from exc

    try:
        analysis = parse_ddd_file(file_path)
    except Exception as exc:  # noqa: BLE001 — komunikat dla UI
        raise DddIdentityError(f"Nie udało się sparsować DDD:\n{exc}") from exc

    kind = analysis.file_kind
    if kind == DddFileKind.CARD:
        source_kind = "card"
    elif kind == DddFileKind.VU:
        source_kind = "vu"
    else:
        raise DddIdentityError(f"Nierozpoznany typ pliku DDD: {file_path.name}")

    def _clean(value: str | None) -> str | None:
        text = (value or "").strip()
        if not text or text.upper() in {"N/A", "NONE", "-"}:
            return None
        return text

    name = (analysis.driver.display_name or "").strip() or "Kierowca"
    card = _clean(analysis.driver.card_number)
    plate = _clean(getattr(analysis.vehicle, "plate", None))
    if not plate:
        for veh in getattr(analysis, "vehicles", None) or []:
            plate = _clean(getattr(veh, "plate", None))
            if plate:
                break

    if source_kind == "card" and not card:
        raise DddIdentityError(
            f"Plik karty nie zawiera numeru karty kierowcy:\n{file_path.name}"
        )

    return DddIdentity(
        driver_name=name,
        card_number=card,
        vehicle_plate=plate,
        source_kind=source_kind,
        filename=file_path.name,
    )
