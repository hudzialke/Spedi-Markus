from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass

import httpx

from markus_dispatcher.config import (
    GEOCODE_MAX_REQUESTS,
    GEOCODE_MIN_INTERVAL_S,
    GEOCODE_TIMEOUT_S,
    GEOCODE_USER_AGENT,
)
from markus_dispatcher.core.models import Coordinates

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_rate_lock = threading.Lock()
_last_request_monotonic = 0.0
_cache_lock = threading.Lock()
_geocode_cache: dict[str, Coordinates] = {}

# Litery EU (w tym ž, ą, ł) — zakres À-ÿ nie obejmuje ž (U+017E).
_LETTER = r"A-Za-zÀ-ÿĀ-ž"

# PL 83-010 / IT-21010 / SK 060 01 / 21010 + miasto
POSTAL_CITY_RE = re.compile(
    rf"\b(?P<cc>[A-Z]{{2}})?[-\s]?(?P<code>\d{{3}}\s\d{{2}}|\d{{2}}[-\s]?\d{{3}}|\d{{4,5}})\s+"
    rf"(?P<city>[{_LETTER}][{_LETTER}']*(?:\s+[{_LETTER}][{_LETTER}']*){{0,3}})\b",
    re.IGNORECASE,
)

# Agrestowa 3 / Weinova 2538/1 / Via Roma 12
STREET_RE = re.compile(
    rf"(?ix)\b(?:"
    rf"(?:ul\.?|ulica|al\.?|aleja|os\.?|plac|pl\.?|via|viale|strasse|str\.)\s+"
    rf")?"
    rf"(?P<street>[{_LETTER}][{_LETTER}'\-]+(?:\s+[{_LETTER}][{_LETTER}'\-]+){{0,4}})"
    rf"\s+(?P<number>\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?)\b"
)

# Wklejka z gotowym GPS (np. "GPS: 49.1639627, 20.4461481")
PASTED_GPS_RE = re.compile(
    r"(?i)(?:\bgps\b[:\s]*)?(-?\d{1,3}[.,]\d{3,})\s*[,;]\s*(-?\d{1,3}[.,]\d{3,})"
)

PHONE_RE = re.compile(
    r"(?ix)(?:,?\s*)?(?:tel\.?|telefon|phone|mob\.?|kom\.?)[:\s]*"
    r"[\d\s\-/\+]{5,}"
)

NOISE_LINE_RE = re.compile(
    r"(?ix)^(?:"
    r"\d+\s*[°º]?\s*piano.*"
    r"|piano\s+\d+.*"
    r"|floor\s+\d+.*"
    r"|pi[eę]tro\s*\d*.*"
    r"|ingresso\s*[a-z0-9]*.*"
    r"|palazzina\b.*"
    r")$"
)

COUNTRY_HINT = {
    "IT": "Italy",
    "PL": "Poland",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "NL": "Netherlands",
    "BE": "Belgium",
    "AT": "Austria",
    "CZ": "Czechia",
    "SK": "Slovakia",
}

# Klasa/typ Nominatim — im wyżej, tym lepiej dla punktów załadunku/rozładunku.
PREFERRED_TYPES = {
    ("building", "warehouse"),
    ("building", "industrial"),
    ("building", "commercial"),
    ("building", "yes"),
    ("place", "house"),
    ("place", "isolated_dwelling"),
    ("highway", "unclassified"),
    ("highway", "residential"),
    ("highway", "service"),
    ("amenity", "cargo"),
    ("aeroway", "gate"),
}

PENALIZED_TYPES = {
    ("aeroway", "tower"),
    ("amenity", "fire_station"),
    ("boundary", "administrative"),
    ("place", "village"),
    ("place", "town"),
    ("place", "city"),
    ("place", "municipality"),
    ("place", "suburb"),
}


class GeocodeError(Exception):
    """Nie udało się znaleźć współrzędnych dla adresu."""


@dataclass(frozen=True)
class AddressParts:
    raw_clean: str
    company: str
    street: str
    house_number: str
    postal_code: str
    city: str
    country_code: str
    place_name: str  # np. Malpensa Cargo City (bez ulicy)


@dataclass(frozen=True)
class NominatimHit:
    lat: float
    lon: float
    display_name: str
    osm_class: str
    osm_type: str
    importance: float
    query: str

    @property
    def coords(self) -> Coordinates:
        return Coordinates(lat=self.lat, lon=self.lon)


def _normalize_chars(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for ch in ("–", "—", "−", "‐"):
        text = text.replace(ch, "-")
    text = text.replace("°", " ")
    return text


def _clean_line(line: str) -> str:
    line = _normalize_chars(line)
    line = PHONE_RE.sub("", line)
    line = re.sub(r"[,\s\-]+$", "", line)
    line = re.sub(r"\s+", " ", line).strip(" ,;-")
    return line


def _is_noise_line(line: str) -> bool:
    return bool(NOISE_LINE_RE.match(line.strip()))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = re.sub(r"\s+", " ", item.strip().lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def extract_pasted_gps(address: str) -> Coordinates | None:
    """Jeśli w tekście są gotowe współrzędne — użyj ich zamiast Nominatim."""
    match = PASTED_GPS_RE.search(address or "")
    if match is None:
        return None
    try:
        lat = float(match.group(1).replace(",", "."))
        lon = float(match.group(2).replace(",", "."))
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return Coordinates(lat=lat, lon=lon)


def parse_address(address: str) -> AddressParts:
    """Wyciąga ulicę, numer, kod, miasto — ignoruje tel. i piętro."""
    raw = address.strip()
    lines = [_clean_line(ln) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln and not _is_noise_line(ln)]
    if not lines:
        cleaned = _clean_line(raw)
        lines = [cleaned] if cleaned else []

    blob = _clean_line(", ".join(lines))
    postal_match = POSTAL_CITY_RE.search(blob)
    cc = (postal_match.group("cc") or "").upper() if postal_match else ""
    postal_code = (
        postal_match.group("code").replace(" ", "") if postal_match else ""
    )
    city = _clean_line(postal_match.group("city")) if postal_match else ""

    # Ulica: szukaj w całym tekście (nie w samym kodzie pocztowym).
    search_blob = blob
    if postal_match:
        search_blob = (
            blob[: postal_match.start()] + " " + blob[postal_match.end() :]
        ).strip(" ,;")
    street_match = STREET_RE.search(search_blob)
    street = ""
    house_number = ""
    company = ""
    if street_match:
        street = street_match.group("street").strip()
        house_number = street_match.group("number").strip()
        before = search_blob[: street_match.start()].strip(" ,;-")
        # Pierwszy segment przed ulicą często = nazwa firmy (Ratherm).
        if before:
            company = before.split(",")[0].strip()
            # Odrzuć jeśli wygląda jak resztki adresu wieloliniowego lotniska.
            if len(company) > 48:
                company = ""

    place_lines = []
    for ln in lines:
        if POSTAL_CITY_RE.search(ln):
            # Z linii z kodem wytnij samą część przed kodem.
            m = POSTAL_CITY_RE.search(ln)
            assert m is not None
            head = ln[: m.start()].strip(" ,;-")
            if head and not STREET_RE.search(head):
                place_lines.append(head)
            continue
        if STREET_RE.search(ln):
            continue
        place_lines.append(ln)

    place_name = _clean_line(", ".join(place_lines))
    place_name = re.sub(r"(?i)\baeroporto\b", " ", place_name)
    place_name = re.sub(r"(?i)\bairport\b", " ", place_name)
    place_name = re.sub(r"\s*-\s*", " ", place_name)
    place_name = re.sub(r"\s+", " ", place_name).strip(" ,;-")

    # Gdy nie ma osobnego place, a jest cargo/malpensa w blobie:
    if not place_name and re.search(r"(?i)malpensa|cargo\s*city", blob):
        place_name = "Malpensa Cargo City"

    return AddressParts(
        raw_clean=blob,
        company=company,
        street=street,
        house_number=house_number,
        postal_code=postal_code,
        city=city,
        country_code=cc,
        place_name=place_name,
    )


def build_geocode_queries(address: str) -> list[str]:
    """Buduje zapytania: najpierw ulica+numer, potem place/cargo, na końcu miasto."""
    parts = parse_address(address)
    if not parts.raw_clean:
        return []

    country = COUNTRY_HINT.get(parts.country_code, "")
    street_full = (
        f"{parts.street} {parts.house_number}".strip()
        if parts.street and parts.house_number
        else parts.street
    )

    candidates: list[str] = []

    # 1) Precyzyjny adres uliczny — priorytet absolutny.
    if street_full and parts.city:
        candidates.append(f"{street_full}, {parts.postal_code} {parts.city}".strip(" ,"))
        candidates.append(f"{street_full}, {parts.city}")
        if parts.postal_code:
            candidates.append(f"{street_full}, {parts.postal_code}")
        if country:
            candidates.append(f"{street_full}, {parts.city}, {country}")
    elif street_full:
        candidates.append(street_full)

    # 2) Firma + ulica (czasem pomaga, czasem szkodzi — po ulicy samej).
    if parts.company and street_full and parts.city:
        candidates.append(f"{parts.company}, {street_full}, {parts.city}")

    # 3) Place / cargo (lotniska, strefy) — zanim spadniemy do samego miasta.
    if parts.place_name:
        if re.search(r"(?i)malpensa", parts.place_name) and re.search(
            r"(?i)cargo", parts.place_name + parts.raw_clean
        ):
            candidates.append("Milano Malpensa Cargo")
            candidates.append("Cargo City Sud, Ferno")
            candidates.append("Malpensa Cargo, Ferno")
        if parts.city:
            candidates.append(f"{parts.place_name}, {parts.city}")
            if country:
                candidates.append(f"{parts.place_name}, {parts.city}, {country}")
        candidates.append(parts.place_name)

    # 4) Oryginał wyczyszczony (bez tel.) — niski priorytet względem ulicy.
    candidates.append(parts.raw_clean)

    # 5) Sam kod + miasto — tylko fallback (nieprecyzyjny środek miejscowości).
    if parts.postal_code and parts.city:
        prefix = f"{parts.country_code}-" if parts.country_code else ""
        candidates.append(f"{prefix}{parts.postal_code} {parts.city}")
        candidates.append(f"{parts.postal_code} {parts.city}")
        if country:
            candidates.append(f"{parts.postal_code} {parts.city}, {country}")
    if parts.city and country:
        candidates.append(f"{parts.city}, {country}")
    elif parts.city:
        candidates.append(parts.city)

    return _dedupe(candidates)


def _score_hit(hit: NominatimHit, parts: AddressParts) -> float:
    """Im wyższy wynik, tym lepszy kandydat."""
    score = float(hit.importance or 0.0)
    key = (hit.osm_class, hit.osm_type)
    display = hit.display_name.lower()

    if key in PREFERRED_TYPES:
        score += 5.0
    if key in PENALIZED_TYPES:
        score -= 8.0
    if hit.osm_class == "boundary":
        score -= 10.0
    if hit.osm_type in {"tower", "fire_station"}:
        score -= 12.0

    if parts.street and parts.street.lower() in display:
        score += 6.0
    if parts.house_number and re.search(
        rf"\b{re.escape(parts.house_number)}\b", display
    ):
        score += 4.0
    if parts.city and parts.city.lower() in display:
        score += 1.5

    # Malpensa cargo: preferuj warehouse / cargo w nazwie, unikaj control tower.
    if re.search(r"(?i)cargo", parts.raw_clean + " " + parts.place_name):
        if "cargo" in display:
            score += 4.0
        if "warehouse" in display or hit.osm_type == "warehouse":
            score += 3.0
        if "control tower" in display or hit.osm_type == "tower":
            score -= 15.0
        if "fire" in display:
            score -= 10.0

    # Kara za wynik będący tylko miastem, gdy mieliśmy ulicę.
    if parts.street and hit.osm_class in {"boundary", "place"} and hit.osm_type in {
        "village",
        "town",
        "city",
        "municipality",
        "administrative",
    }:
        score -= 20.0

    return score


def _hits_from_response(data: object, query: str) -> list[NominatimHit]:
    if not isinstance(data, list):
        return []
    hits: list[NominatimHit] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            hits.append(
                NominatimHit(
                    lat=float(item["lat"]),
                    lon=float(item["lon"]),
                    display_name=str(item.get("display_name") or ""),
                    osm_class=str(item.get("class") or ""),
                    osm_type=str(item.get("type") or ""),
                    importance=float(item.get("importance") or 0.0),
                    query=query,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return hits


def _throttle(*, enabled: bool) -> None:
    """Globalny limit Nominatim — wszystkie wątki dzielą jeden odstęp."""
    global _last_request_monotonic
    if not enabled:
        return
    with _rate_lock:
        now = time.monotonic()
        wait = GEOCODE_MIN_INTERVAL_S - (now - _last_request_monotonic)
        if wait > 0:
            time.sleep(wait)
        _last_request_monotonic = time.monotonic()


def _cache_key(address: str) -> str:
    return re.sub(r"\s+", " ", address.strip().lower())


def clear_geocode_cache() -> None:
    with _cache_lock:
        _geocode_cache.clear()


def _nominatim_get(
    client: httpx.Client,
    *,
    params: dict[str, str | int],
    rate_limit: bool,
) -> httpx.Response:
    headers = {"User-Agent": GEOCODE_USER_AGENT, "Accept-Language": "pl,en,it"}
    last_exc: Exception | None = None
    for attempt in range(3):
        _throttle(enabled=rate_limit)
        try:
            response = client.get(NOMINATIM_URL, params=params, headers=headers)
        except httpx.HTTPError as exc:
            last_exc = exc
            if rate_limit and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        if response.status_code == 429:
            last_exc = httpx.HTTPStatusError(
                "Too Many Requests",
                request=response.request,
                response=response,
            )
            if attempt < 2:
                # Nominatim: odczekaj dłużej przy 429.
                backoff = 2.5 * (attempt + 1) if rate_limit else 0.0
                if backoff:
                    time.sleep(backoff)
                continue
            response.raise_for_status()
        response.raise_for_status()
        return response
    assert last_exc is not None
    raise last_exc


def _nominatim_search(
    query: str,
    *,
    client: httpx.Client,
    country_code: str | None = None,
    limit: int = 5,
    rate_limit: bool = True,
) -> list[NominatimHit]:
    params: dict[str, str | int] = {
        "q": query,
        "format": "json",
        "limit": limit,
        "addressdetails": 1,
    }
    if country_code:
        params["countrycodes"] = country_code.lower()

    response = _nominatim_get(client, params=params, rate_limit=rate_limit)
    return _hits_from_response(response.json(), query)


def _nominatim_structured(
    parts: AddressParts,
    *,
    client: httpx.Client,
    rate_limit: bool = True,
) -> list[NominatimHit]:
    if not (parts.street and parts.house_number):
        return []
    params: dict[str, str | int] = {
        "street": f"{parts.street} {parts.house_number}",
        "format": "json",
        "limit": 5,
        "addressdetails": 1,
    }
    if parts.city:
        params["city"] = parts.city
    if parts.postal_code:
        params["postalcode"] = parts.postal_code
    if parts.country_code:
        params["country"] = COUNTRY_HINT.get(
            parts.country_code, parts.country_code
        )
        params["countrycodes"] = parts.country_code.lower()

    response = _nominatim_get(client, params=params, rate_limit=rate_limit)
    label = f"{parts.street} {parts.house_number}, {parts.city}".strip(" ,")
    return _hits_from_response(response.json(), label)


def geocode_address(
    address: str,
    *,
    client: httpx.Client | None = None,
    pause_s: float = 1.05,
    max_requests: int = GEOCODE_MAX_REQUESTS,
    use_cache: bool = True,
) -> Coordinates:
    """Geokoduje adres: ulica > cargo/place > miasto, z rankingiem wyników.

    ``pause_s <= 0`` wyłącza globalny rate-limit (testy z mockiem).
    """
    pasted = extract_pasted_gps(address)
    if pasted is not None:
        return pasted

    parts = parse_address(address)
    queries = build_geocode_queries(address)
    if not queries:
        raise GeocodeError("Pusty adres.")

    cache_key = _cache_key(parts.raw_clean or address)
    if use_cache:
        with _cache_lock:
            cached = _geocode_cache.get(cache_key)
            if cached is not None:
                return cached

    rate_limit = pause_s > 0
    headers = {"User-Agent": GEOCODE_USER_AGENT, "Accept-Language": "pl,en,it"}
    owns_client = client is None
    http = client or httpx.Client(timeout=GEOCODE_TIMEOUT_S, headers=headers)

    best: NominatimHit | None = None
    best_score = float("-inf")
    last_network_error: Exception | None = None
    request_index = 0

    def consider(hits: list[NominatimHit]) -> None:
        nonlocal best, best_score
        for hit in hits:
            score = _score_hit(hit, parts)
            if score > best_score:
                best = hit
                best_score = score

    def should_stop() -> bool:
        """Mamy już precyzyjny hit (budynek/ulica) — nie schodź do centrum miasta."""
        if best is None:
            return False
        key = (best.osm_class, best.osm_type)
        if key in PREFERRED_TYPES:
            return True
        if parts.street and parts.street.lower() in best.display_name.lower():
            return True
        if re.search(r"(?i)cargo", parts.raw_clean) and "cargo" in best.display_name.lower():
            if best.osm_type not in {"tower", "fire_station"}:
                return True
        return False

    def remember(coords: Coordinates) -> Coordinates:
        if use_cache:
            with _cache_lock:
                _geocode_cache[cache_key] = coords
        return coords

    try:
        # Structured search first when street known.
        if parts.street and parts.house_number and request_index < max_requests:
            try:
                consider(
                    _nominatim_structured(
                        parts, client=http, rate_limit=rate_limit
                    )
                )
                request_index += 1
            except httpx.HTTPError as exc:
                last_network_error = exc
                request_index += 1
            if should_stop():
                assert best is not None
                return remember(best.coords)

        for query in queries:
            if request_index >= max_requests:
                break
            try:
                hits = _nominatim_search(
                    query,
                    client=http,
                    country_code=parts.country_code or None,
                    rate_limit=rate_limit,
                )
                # Bez drugiego strzału „bez countrycodes” — oszczędza limit 1 req/s.
                consider(hits)
                request_index += 1
            except httpx.HTTPError as exc:
                last_network_error = exc
                request_index += 1
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                    if exc.response.status_code == 429:
                        raise GeocodeError(
                            "Nominatim: zbyt wiele zapytań (429). "
                            "Poczekaj chwilę i spróbuj ponownie."
                        ) from exc
                continue

            if should_stop():
                assert best is not None
                return remember(best.coords)

            if best is not None and parts.street:
                if (best.osm_class, best.osm_type) in PENALIZED_TYPES:
                    continue
    finally:
        if owns_client:
            http.close()

    if best is not None and best_score > -15:
        return remember(best.coords)

    if last_network_error is not None and request_index <= 1:
        raise GeocodeError(
            f"Błąd sieci przy geokodowaniu: {last_network_error}"
        ) from last_network_error
    if last_network_error is not None:
        raise GeocodeError(
            f"Nie znaleziono współrzędnych (ostatni błąd sieci: {last_network_error})."
        ) from last_network_error
    raise GeocodeError("Nie znaleziono współrzędnych dla tego adresu.")
