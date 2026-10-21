from __future__ import annotations

from datetime import datetime

from markus_dispatcher.core.models import (
    STATUS_LABELS_PL,
    Driver,
    DriverStatus,
    JobData,
    JOB_STATUS_LABELS_PL,
)


def _append_note_block(lines: list[str], label: str, note: str) -> None:
    text = note.strip()
    if not text:
        return
    if "\n" in text:
        lines.append(f"  {label}:")
        for note_line in text.splitlines():
            lines.append(f"    {note_line}")
    else:
        lines.append(f"  {label}: {text}")


def format_driver_line(driver: Driver) -> str:
    lines = [
        driver.display_name,
        f"  Status: {STATUS_LABELS_PL.get(driver.status, 'Brak statusu')}",
        f"  Dostępny: {'tak' if driver.is_active else 'nie'}",
    ]
    _append_note_block(lines, "Notatka bieżąca", driver.note)
    _append_note_block(lines, "Notatka stała", driver.note_fixed)
    return "\n".join(lines)


def build_summary(
    drivers: list[Driver],
    *,
    generated_at: datetime | None = None,
) -> str:
    when = generated_at or datetime.now()
    header = when.strftime("MARKUSDISPATCHER - %d.%m.%Y %H:%M")
    if not drivers:
        return f"{header}\n\nBrak kierowców na liście."
    blocks = [format_driver_line(driver) for driver in drivers]
    return f"{header}\n\n" + "\n\n".join(blocks)


def build_client_status_message(
    job: JobData,
    *,
    driver_name: str = "",
) -> str:
    """Szablon update’u dla klienta (WA / e-mail) — do ręcznego dopisania treści."""
    status = JOB_STATUS_LABELS_PL.get(job.status, job.status)
    route = " → ".join(
        part
        for part in (
            _human_place(job.load_address),
            _human_place(job.unload_address),
        )
        if part
    )
    lines = ["Update:"]
    if driver_name.strip():
        lines.append(f"Kierowca: {driver_name.strip()}")
    if route:
        lines.append(f"Trasa: {route}")
    lines.append(f"Status: {status}")
    return "\n".join(lines)


def _short_place(address: str) -> str:
    text = address.strip()
    if not text:
        return ""
    first = text.splitlines()[0].strip()
    if len(first) > 48:
        return first[:45] + "…"
    return first


def _human_place(address: str) -> str:
    """Skrót miejsca w czytelnej formie (bez KRZYKU WIELKIMI LITERAMI)."""
    place = _short_place(address)
    if not place:
        return ""
    letters = [c for c in place if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return place.title()
    return place


def driver_status_badge(status: DriverStatus) -> str:
    return STATUS_LABELS_PL.get(status, "Brak")
