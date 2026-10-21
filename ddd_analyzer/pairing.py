from __future__ import annotations

import re

from ddd_analyzer.models import DddAnalysis, PairedAnalysis, VehicleMatch


def _normalize_plate(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def _normalize_vin(value: str) -> str:
    return value.strip().upper()


def compare_vehicle(card: DddAnalysis, vu: DddAnalysis) -> VehicleMatch:
    card_plate = _normalize_plate(card.vehicle.plate)
    vu_plate = _normalize_plate(vu.vehicle.plate)
    card_vin = _normalize_vin(card.vehicle.vin)
    vu_vin = _normalize_vin(vu.vehicle.vin)

    plate_match = bool(card_plate and vu_plate and card_plate == vu_plate)

    vin_match: bool | None
    if card_vin in {"", "N/A"} or vu_vin in {"", "N/A"}:
        vin_match = None
    else:
        vin_match = card_vin == vu_vin

    return VehicleMatch(
        plate_match=plate_match,
        vin_match=vin_match,
        card_plate=card.vehicle.display_plate,
        vu_plate=vu.vehicle.display_plate,
        card_vin=card_vin or "brak",
        vu_vin=vu_vin or "brak",
    )


def pair_analyses(card: DddAnalysis | None, vu: DddAnalysis | None) -> PairedAnalysis:
    match = compare_vehicle(card, vu) if card and vu else None
    return PairedAnalysis(card=card, vu=vu, vehicle_match=match)


def data_range_text(analysis: DddAnalysis) -> str:
    start, end = analysis.date_range
    if not start or not end:
        return "Brak danych aktywnosci"
    if start == end:
        return start.strftime("%d.%m.%Y")
    return f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}"
