from __future__ import annotations

from spedi.i18n import tr as _tr

from dataclasses import dataclass, field
from typing import Literal

DriverStatus = Literal["", "not_ready", "working", "empty", "planned", "reload"]
JobStatus = Literal[
    "draft",
    "assigned",
    "loading",
    "en_route",
    "unloading",
    "done",
    "cancelled",
]
ContactChannel = Literal["whatsapp", "email", "both"]
AttachmentKind = Literal["pdf", "excel", "sheets", "file", "pod"]

GLYPH_LOAD = "▼"
GLYPH_DETAILS = "●"
GLYPH_UNLOAD = "▲"


@dataclass
class Coordinates:
    lat: float
    lon: float

    def format(self, *, precision: int = 6) -> str:
        return f"{self.lat:.{precision}f}, {self.lon:.{precision}f}"


@dataclass(slots=True)
class Driver:
    id: int
    display_name: str
    is_active: bool
    status: DriverStatus
    note: str
    note_fixed: str

    @property
    def sort_key(self) -> str:
        return self.display_name.casefold()


@dataclass(slots=True)
class Client:
    id: int
    name: str
    email: str = ""
    phone: str = ""
    preferred_channel: ContactChannel = "both"
    notes: str = ""


@dataclass(slots=True)
class Attachment:
    id: int
    job_id: int
    kind: AttachmentKind
    original_name: str
    stored_name: str
    source_url: str = ""
    created_at: str = ""


@dataclass
class JobData:
    """Dane edytora / instrukcji (kompatybilne z build_instruction)."""

    load_address: str = ""
    load_datetime: str = ""
    load_coords: Coordinates | None = None
    ref_numbers: str = ""
    pallets: str = ""
    notes: str = ""
    unload_address: str = ""
    unload_datetime: str = ""
    unload_coords: Coordinates | None = None
    maps_url: str = ""
    geocode_errors: dict[str, str] = field(default_factory=dict)
    client_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""
    client_message: str = ""
    status: JobStatus = "draft"
    driver_id: int | None = None
    client_id: int | None = None
    id: int | None = None
    archive_path: str = ""
    updated_at: str = ""


# Alias wsteczny dla portu z Zlecenie.
ZlecenieData = JobData


def parse_driver_input(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Podaj imię, nazwisko i wymiary auta.")
    if len(cleaned) < 2:
        raise ValueError("Wpis jest za krótki.")
    return cleaned


STATUS_LABELS_PL: dict[DriverStatus, str] = {
    "": _tr("Brak statusu"),
    "not_ready": _tr("Niegotowy"),
    "working": _tr("W pracy"),
    "empty": _tr("Pusty"),
    "planned": _tr("Zaplanowane"),
    "reload": _tr("Doładuj"),
}

JOB_STATUS_LABELS_PL: dict[JobStatus, str] = {
    "draft": "Szkic",
    "assigned": "Przypisane",
    "loading": _tr("Załadunek"),
    "en_route": "W drodze",
    "unloading": _tr("Rozładunek"),
    "done": "Zakończone",
    "cancelled": "Anulowane",
}
