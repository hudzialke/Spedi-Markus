"""Offline point-in-polygon: lat/lon → ISO-2 (UE + sąsiedzi)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data" / "eu_countries.geojson"


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray casting; ring = [[lon, lat], ...]."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, geom: dict[str, Any]) -> bool:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return False
    if gtype == "Polygon":
        # exterior + holes
        if not _point_in_ring(lon, lat, coords[0]):
            return False
        for hole in coords[1:]:
            if _point_in_ring(lon, lat, hole):
                return False
        return True
    if gtype == "MultiPolygon":
        for poly in coords:
            if not poly:
                continue
            if not _point_in_ring(lon, lat, poly[0]):
                continue
            in_hole = any(_point_in_ring(lon, lat, hole) for hole in poly[1:])
            if not in_hole:
                return True
        return False
    return False


@lru_cache(maxsize=1)
def _load_features() -> tuple[tuple[str, dict[str, Any]], ...]:
    if not _DATA.is_file():
        return ()
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    out: list[tuple[str, dict[str, Any]]] = []
    for feat in raw.get("features") or []:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        iso = str(props.get("iso2") or "").strip().upper()
        geom = feat.get("geometry")
        if not iso or not isinstance(geom, dict):
            continue
        out.append((iso, geom))
    return tuple(out)


def country_at(latitude: float, longitude: float) -> str:
    """Zwróć ISO-2 kraju zawierającego punkt, albo ''."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return ""
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return ""
    for iso, geom in _load_features():
        if _point_in_polygon(lon, lat, geom):
            return iso
    return ""


def clear_geo_cache() -> None:
    _load_features.cache_clear()
