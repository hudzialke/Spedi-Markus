"""Wyliczanie przekroczeń granic z punktów GNSS (offline)."""

from __future__ import annotations

from ddd_analyzer.geo_country import country_at
from ddd_analyzer.models import BorderCrossing, GnssPoint


def derive_crossings_from_gnss(points: list[GnssPoint]) -> list[BorderCrossing]:
    """
    Moment granicy = czas pierwszego punktu GNSS w nowym kraju.
    Pomija punkty bez rozpoznanego kraju.
    """
    ordered = sorted(points, key=lambda p: p.occurred_at)
    out: list[BorderCrossing] = []
    prev_country = ""
    for p in ordered:
        country = country_at(p.latitude, p.longitude)
        if not country:
            continue
        if prev_country and country != prev_country:
            out.append(
                BorderCrossing(
                    timestamp=p.occurred_at,
                    country_left=prev_country,
                    country_entered=country,
                    odometer_km=p.odometer_km,
                    latitude=p.latitude,
                    longitude=p.longitude,
                    source="gnss",
                )
            )
        prev_country = country
    return out
