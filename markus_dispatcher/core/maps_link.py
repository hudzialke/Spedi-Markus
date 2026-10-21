from __future__ import annotations

from urllib.parse import quote

from markus_dispatcher.core.models import Coordinates


def build_maps_dir_url(origin: Coordinates, destination: Coordinates) -> str:
    """Buduje klikalny link trasy Google Maps (origin → destination)."""
    start = f"{origin.lat},{origin.lon}"
    end = f"{destination.lat},{destination.lon}"
    return (
        f"https://www.google.com/maps/dir/"
        f"{quote(start, safe=',')}/{quote(end, safe=',')}"
    )
