from __future__ import annotations

from markus_dispatcher.core.maps_link import build_maps_dir_url
from markus_dispatcher.core.models import JobData

DEFAULT_FOOTER = (
    "Pamiętaj o foto towaru i numerze identyfikacyjnym CMR "
    "w prawym górnym rogu dokumentu."
)

CMR_REMINDER = DEFAULT_FOOTER


def _block_lines(title: str, rows: list[tuple[str, str]]) -> list[str]:
    lines = [title]
    for label, value in rows:
        text = value.strip()
        if not text:
            continue
        if "\n" in text:
            lines.append(f"{label}:")
            for part in text.splitlines():
                lines.append(f"  {part}")
        else:
            lines.append(f"{label}: {text}")
    return lines


def _headed_title(base: str, when: str) -> str:
    when = when.strip()
    return f"{base} {when}" if when else base


def build_instruction(
    data: JobData,
    *,
    footer: str | None = None,
) -> str:
    """Składa gotowy tekst instrukcji dla kierowcy (do schowka / WhatsApp)."""
    maps_url = data.maps_url.strip()
    if not maps_url and data.load_coords and data.unload_coords:
        maps_url = build_maps_dir_url(data.load_coords, data.unload_coords)

    sections: list[str] = []

    load = _block_lines(
        _headed_title("ZAŁADUNEK", data.load_datetime),
        [
            ("Adres", data.load_address),
            ("GPS", data.load_coords.format() if data.load_coords else ""),
        ],
    )
    if (
        data.load_address.strip()
        or data.load_datetime.strip()
        or data.load_coords is not None
    ):
        sections.append("\n".join(load))

    details_rows: list[tuple[str, str]] = [
        ("Ref", data.ref_numbers),
        ("Palety", data.pallets),
        ("Uwagi", data.notes),
    ]
    if data.client_name.strip():
        details_rows.insert(0, ("Klient", data.client_name))
    details = _block_lines("SZCZEGÓŁY", details_rows)
    if len(details) > 1:
        sections.append("\n".join(details))

    unload = _block_lines(
        _headed_title("ROZŁADUNEK", data.unload_datetime),
        [
            ("Adres", data.unload_address),
            ("GPS", data.unload_coords.format() if data.unload_coords else ""),
        ],
    )
    if (
        data.unload_address.strip()
        or data.unload_datetime.strip()
        or data.unload_coords is not None
    ):
        sections.append("\n".join(unload))

    if maps_url:
        sections.append(f"TRASA:\n{maps_url}")

    text = "\n\n".join(sections)
    foot = DEFAULT_FOOTER if footer is None else footer
    foot = foot.strip()
    if foot:
        text = f"{text}\n\n{foot}" if text else foot
    return text
