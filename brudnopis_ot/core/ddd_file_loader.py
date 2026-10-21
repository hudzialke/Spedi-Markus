from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from brudnopis_ot.core.hudonizator_reader import RawSegment

SourceKind = Literal["card", "vu", "template"]


class DddLoadError(Exception):
    """Błąd wczytania pliku .ddd (komunikat po polsku)."""


@dataclass(frozen=True, slots=True)
class BorderCrossingRecord:
    occurred_at: datetime
    country_left: str
    country_entered: str
    odometer_km: int | None = None
    source: str = "card"  # card | gnss

    @property
    def day(self) -> date:
        return self.occurred_at.date()


@dataclass(frozen=True, slots=True)
class GnssPointRecord:
    occurred_at: datetime
    latitude: float
    longitude: float
    odometer_km: int | None = None

    @property
    def day(self) -> date:
        return self.occurred_at.date()


@dataclass(frozen=True, slots=True)
class PlaceRecord:
    """START/END miejsca pracy dziennej z DDD (nation → ISO-2)."""

    occurred_at: datetime
    entry_type: str  # START | END
    country_code: str
    odometer_km: int | None = None

    @property
    def day(self) -> date:
        return self.occurred_at.date()


@dataclass(frozen=True, slots=True)
class DddLoadResult:
    segments: list[RawSegment]
    title: str
    driver_name: str
    card_number: str | None
    vehicle_plate: str | None
    source_kind: SourceKind
    filenames: tuple[str, ...]
    date_from: date | None
    date_to: date | None
    border_crossings: tuple[BorderCrossingRecord, ...] = ()
    places: tuple[PlaceRecord, ...] = ()
    gnss_points: tuple[GnssPointRecord, ...] = ()

    template_id: str | None = None

    @property
    def filename(self) -> str:
        if not self.filenames:
            return ""
        if len(self.filenames) == 1:
            return self.filenames[0]
        return f"{len(self.filenames)} plików"

    @property
    def profile_key(self) -> str:
        """Klucz profilu: karta kierowcy albo rejestracja pojazdu (VU)."""
        if self.source_kind == "template":
            if not self.template_id:
                raise ValueError("Ręczny profil wymaga trwałego identyfikatora")
            return f"template:{self.template_id}"
        if self.source_kind == "vu" and not _clean_text(self.card_number):
            plate = _normalize_plate(self.vehicle_plate)
            if plate:
                return f"vu:{plate}"
            return f"vu-file:{self.filenames[0].casefold() if self.filenames else '?'}"
        card = (self.card_number or "").strip()
        if card and card.upper() not in {"", "N/A"}:
            return f"card:{card}"
        return f"name:{(self.driver_name or '').strip().casefold()}"

    # Compat alias used earlier in UI/tests
    @property
    def driver_key(self) -> str:
        return self.profile_key

    @property
    def profile_label(self) -> str:
        if self.source_kind == "template":
            return f"{self.driver_name} · Ręczny profil"
        if self.source_kind == "vu" and not _clean_text(self.card_number):
            plate = self.vehicle_plate or "brak rejestracji"
            return f"VU {plate}"
        card = self.card_number or "brak karty"
        return f"{self.driver_name} ({card})"


def _clean_text(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.upper() in {"N/A", "NONE", "-"}:
        return None
    return text


def _normalize_plate(plate: str | None) -> str:
    if not plate:
        return ""
    return "".join(str(plate).split()).upper()


def replace_range(
    existing: list[RawSegment],
    new_segments: list[RawSegment],
    date_from: date | None,
    date_to: date | None,
) -> list[RawSegment]:
    """Merge actual intervals, retaining gaps; reject conflicting raw evidence."""
    out: list[RawSegment] = []
    for seg in sorted([*existing, *new_segments], key=lambda s: (s.day, s.start_minute, s.end_minute)):
        if seg.activity not in {"drive", "work", "rest", "availability"} or not 0 <= seg.start_minute < seg.end_minute <= 1440:
            raise DddLoadError("Niepoprawny segment DDD")
        if out and out[-1].day == seg.day and out[-1].end_minute > seg.start_minute and out[-1].activity != seg.activity:
            raise DddLoadError(f"Sprzeczne aktywności DDD: {seg.day}, minuta {seg.start_minute}. Zachowano wcześniejsze dane.")
        if out and out[-1].day == seg.day and out[-1].activity == seg.activity and out[-1].end_minute >= seg.start_minute:
            old = out.pop()
            out.append(RawSegment(old.day, old.activity, old.start_minute, max(old.end_minute, seg.end_minute)))
        else:
            out.append(seg)
    return out


def replace_crossings_range(
    existing: tuple[BorderCrossingRecord, ...] | list[BorderCrossingRecord],
    new_crossings: tuple[BorderCrossingRecord, ...] | list[BorderCrossingRecord],
    date_from: date | None,
    date_to: date | None,
) -> tuple[BorderCrossingRecord, ...]:
    if date_from is not None and date_to is not None:
        kept = [c for c in existing if c.day < date_from or c.day > date_to]
    else:
        kept = list(existing)
    merged = kept + list(new_crossings)
    merged.sort(key=lambda c: (c.occurred_at, c.country_left, c.country_entered))
    return tuple(merged)


def replace_places_range(
    existing: tuple[PlaceRecord, ...] | list[PlaceRecord],
    new_places: tuple[PlaceRecord, ...] | list[PlaceRecord],
    date_from: date | None,
    date_to: date | None,
) -> tuple[PlaceRecord, ...]:
    if date_from is not None and date_to is not None:
        kept = [p for p in existing if p.day < date_from or p.day > date_to]
    else:
        kept = list(existing)
    merged = kept + list(new_places)
    merged.sort(key=lambda p: (p.occurred_at, p.entry_type, p.country_code))
    return tuple(merged)


def replace_gnss_points_range(
    existing: tuple[GnssPointRecord, ...] | list[GnssPointRecord],
    new_points: tuple[GnssPointRecord, ...] | list[GnssPointRecord],
    date_from: date | None,
    date_to: date | None,
) -> tuple[GnssPointRecord, ...]:
    if date_from is not None and date_to is not None:
        kept = [p for p in existing if p.day < date_from or p.day > date_to]
    else:
        kept = list(existing)
    merged = kept + list(new_points)
    merged.sort(key=lambda p: (p.occurred_at, p.latitude, p.longitude))
    return tuple(merged)


def _title_for(result_fields: dict) -> str:
    kind: SourceKind = result_fields["source_kind"]
    filenames: tuple[str, ...] = result_fields["filenames"]
    n = len(filenames)
    if kind == "vu":
        plate = result_fields["vehicle_plate"] or "pojazd"
        base = f"DDD VU: {plate}"
    else:
        base = f"DDD: {result_fields['driver_name']}"
    if n > 1:
        return f"{base} ({n} plików)"
    if n == 1:
        return f"{base} ({filenames[0]})"
    return base


def load_ddd_file(path: str | Path, *, _analysis=None) -> DddLoadResult:
    """
    Parsuj kartę kierowcy (C_*.ddd) albo pamięć VU / tachograf (M_*.ddd).
    Zwraca segmenty w formacie RawSegment.
    """
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise DddLoadError(f"Plik nie istnieje:\n{file_path}")

    try:
        from ddd_analyzer.models import DddFileKind
        from ddd_analyzer.parser import parse_ddd_file
    except ImportError as exc:
        raise DddLoadError(
            "Brak modułu ddd-analyzer.\n"
            "Zainstaluj zależność (pip install -e ../ddd-analyzer) "
            "albo użyj pełnego EXE Brudnopisu."
        ) from exc

    try:
        analysis = _analysis if _analysis is not None else parse_ddd_file(file_path)
    except FileNotFoundError as exc:
        raise DddLoadError(str(exc)) from exc
    except ValueError as exc:
        raise DddLoadError(f"Nie udało się odczytać pliku .ddd:\n{exc}") from exc
    except Exception as exc:  # noqa: BLE001 — parser tacho bywa kapryśny
        raise DddLoadError(f"Błąd parsera DDD:\n{exc}") from exc

    if analysis.file_kind == DddFileKind.VU and _analysis is None:
        parts = getattr(analysis, "driver_analyses", None)
        if not isinstance(parts, list) or len(parts) != 1:
            raise DddLoadError("VU: użyj importu wielu profili; brak jednoznacznie przypisanego pojedynczego kierowcy.")
        analysis = parts[0]
    if analysis.file_kind == DddFileKind.CARD:
        source_kind: SourceKind = "card"
    elif analysis.file_kind == DddFileKind.VU:
        source_kind = "vu"
    else:
        raise DddLoadError(
            "Nierozpoznany typ pliku .ddd.\n"
            "Oczekiwano karty (C_*.ddd) albo tachografu VU (M_*.ddd).\n"
            f"Plik: {file_path.name}"
        )

    segments = [
        RawSegment(
            day=seg.day,
            activity=seg.activity.value,
            start_minute=seg.start_minute,
            end_minute=seg.end_minute,
        )
        for seg in analysis.segments
    ]
    crossings = tuple(
        BorderCrossingRecord(
            occurred_at=bc.timestamp,
            country_left=bc.country_left,
            country_entered=bc.country_entered,
            odometer_km=bc.odometer_km,
            source=str(getattr(bc, "source", "") or "card"),
        )
        for bc in getattr(analysis, "border_crossings", None) or []
        if getattr(bc, "timestamp", None) is not None
    )
    places = tuple(
        PlaceRecord(
            occurred_at=pl.timestamp,
            entry_type=str(getattr(pl, "entry_type", "") or "START"),
            country_code=str(getattr(pl, "country_code", "") or ""),
            odometer_km=getattr(pl, "odometer_km", None),
        )
        for pl in getattr(analysis, "places", None) or []
        if getattr(pl, "timestamp", None) is not None
        and str(getattr(pl, "country_code", "") or "").strip()
    )
    gnss_points = tuple(
        GnssPointRecord(
            occurred_at=gp.occurred_at,
            latitude=float(gp.latitude),
            longitude=float(gp.longitude),
            odometer_km=getattr(gp, "odometer_km", None),
        )
        for gp in getattr(analysis, "gnss_points", None) or []
        if getattr(gp, "occurred_at", None) is not None
    )
    date_from, date_to = analysis.date_range
    driver_name = (analysis.driver.display_name or "").strip() or "Kierowca"
    card = _clean_text(analysis.driver.card_number)
    plate = _clean_text(getattr(analysis.vehicle, "plate", None))
    if not plate:
        for veh in getattr(analysis, "vehicles", None) or []:
            plate = _clean_text(getattr(veh, "plate", None))
            if plate:
                break

    if source_kind == "card" and not card:
        raise DddLoadError("Brak pewnego numeru karty kierowcy; profil nie został utworzony.")
    if not segments:
        raise DddLoadError("Brak poprawnych segmentów aktywności w pliku.")
    if source_kind == "vu" and not plate:
        raise DddLoadError(
            "Plik VU nie zawiera numeru rejestracyjnego pojazdu.\n"
            f"Plik: {file_path.name}"
        )

    fields = {
        "driver_name": driver_name,
        "vehicle_plate": plate,
        "source_kind": source_kind,
        "filenames": (file_path.name,),
    }
    return DddLoadResult(
        segments=segments,
        title=_title_for(fields),
        driver_name=driver_name,
        card_number=card,
        vehicle_plate=plate,
        source_kind=source_kind,
        filenames=(file_path.name,),
        date_from=date_from,
        date_to=date_to,
        border_crossings=crossings,
        places=places,
        gnss_points=gnss_points,
    )


def _merge_parts(parts: list[DddLoadResult]) -> DddLoadResult:
    if not parts:
        raise DddLoadError("Brak plików do scalenia.")
    ordered = sorted(
        parts,
        key=lambda r: (r.date_from or date.min, r.date_to or date.min, r.filenames[0]),
    )
    segments: list[RawSegment] = []
    crossings: tuple[BorderCrossingRecord, ...] = ()
    places: tuple[PlaceRecord, ...] = ()
    gnss_points: tuple[GnssPointRecord, ...] = ()
    for part in ordered:
        segments = replace_range(segments, part.segments, part.date_from, part.date_to)
        crossings = replace_crossings_range(
            crossings, part.border_crossings, part.date_from, part.date_to
        )
        places = replace_places_range(
            places, part.places, part.date_from, part.date_to
        )
        gnss_points = replace_gnss_points_range(
            gnss_points, part.gnss_points, part.date_from, part.date_to
        )

    first = ordered[0]
    dates = [d for r in ordered for d in (r.date_from, r.date_to) if d is not None]
    filenames: list[str] = []
    for r in ordered:
        filenames.extend(r.filenames)
    # Prefer non-empty plate/name from any part
    plate = first.vehicle_plate
    name = first.driver_name
    card = first.card_number
    for r in ordered:
        if not plate and r.vehicle_plate:
            plate = r.vehicle_plate
        if r.driver_name and r.driver_name != "Kierowca":
            name = r.driver_name
        if not card and r.card_number:
            card = r.card_number
    fn_tuple = tuple(filenames)
    fields = {
        "driver_name": name,
        "vehicle_plate": plate,
        "source_kind": first.source_kind,
        "filenames": fn_tuple,
    }
    return DddLoadResult(
        segments=segments,
        title=_title_for(fields),
        driver_name=name,
        card_number=card,
        vehicle_plate=plate,
        source_kind=first.source_kind,
        filenames=fn_tuple,
        date_from=min(dates) if dates else None,
        date_to=max(dates) if dates else None,
        border_crossings=crossings,
        places=places,
        gnss_points=gnss_points,
    )


def load_ddd_files(paths: list[str | Path]) -> DddLoadResult:
    """
    Wczytaj jeden lub wiele plików .ddd tego samego profilu i zsumuj segmenty.
    Profil = ta sama karta (C_*) albo ta sama rejestracja (M_*).
    """
    if not paths:
        raise DddLoadError("Nie wybrano żadnego pliku .ddd.")

    loaded = [load_ddd_file(p) for p in paths]
    keys = {r.profile_key for r in loaded}
    if len(keys) > 1:
        labels = sorted({r.profile_label for r in loaded})
        raise DddLoadError(
            "Wybrane pliki należą do różnych profili.\n"
            "Użyj importu wsadowego (wiele kart i VU naraz) — program pogrupuje je sam.\n\n"
            + "\n".join(f"• {n}" for n in labels)
        )
    return _merge_parts(loaded)


@dataclass(frozen=True, slots=True)
class BatchImportResult:
    profiles: tuple[DddLoadResult, ...]
    errors: tuple[str, ...]
    link_hints: tuple[str, ...]


def load_ddd_batch(paths: list[str | Path]) -> BatchImportResult:
    """
    Wczytaj wiele plików C_* i M_* naraz, pogrupuj po karcie / rejestracji
    i scal segmenty w obrębie każdego profilu.
    """
    if not paths:
        raise DddLoadError("Nie wybrano żadnego pliku .ddd.")

    loaded: list[DddLoadResult] = []
    errors: list[str] = []
    for path in paths:
        try:
            loaded.extend(load_ddd_profiles(path))
        except DddLoadError as exc:
            errors.append(f"{Path(path).name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{Path(path).name}: {exc}")

    if not loaded:
        raise DddLoadError(
            "Nie udało się wczytać żadnego pliku.\n\n" + "\n".join(errors)
        )

    by_key: dict[str, list[DddLoadResult]] = {}
    for item in loaded:
        by_key.setdefault(item.profile_key, []).append(item)

    profiles = tuple(
        sorted(
            (_merge_parts(group) for group in by_key.values()),
            key=lambda p: (p.source_kind, p.profile_label.casefold()),
        )
    )

    # Podpowiedzi: karta z rejestracją pojazdu ↔ osobny profil VU tej samej rejestracji
    vu_plates = {
        _normalize_plate(p.vehicle_plate): p
        for p in profiles
        if p.source_kind == "vu" and p.vehicle_plate
    }
    hints: list[str] = []
    for p in profiles:
        if p.source_kind != "card" or not p.vehicle_plate:
            continue
        key = _normalize_plate(p.vehicle_plate)
        vu = vu_plates.get(key)
        if vu:
            hints.append(
                f"Karta „{p.driver_name}” → pojazd {p.vehicle_plate} "
                f"(w batchu jest też VU tej rejestracji: {vu.filename})."
            )

    return BatchImportResult(
        profiles=profiles,
        errors=tuple(errors),
        link_hints=tuple(hints),
    )


def merge_profile_into_session(
    session: list[DddLoadResult],
    incoming: DddLoadResult,
) -> list[DddLoadResult]:
    """Doklej profil do sesji: ten sam klucz → scal, inaczej dodaj."""
    out: list[DddLoadResult] = []
    found = False
    for existing in session:
        if existing.profile_key == incoming.profile_key:
            out.append(merge_load_into_profile(existing, incoming))
            found = True
        else:
            out.append(existing)
    if not found:
        out.append(incoming)
    return sorted(out, key=lambda p: (p.source_kind, p.profile_label.casefold()))


def merge_batch_into_session(
    session: list[DddLoadResult],
    batch: BatchImportResult,
) -> list[DddLoadResult]:
    result = list(session)
    for profile in batch.profiles:
        result = merge_profile_into_session(result, profile)
    return result


def merge_load_into_profile(
    existing: DddLoadResult | None,
    incoming: DddLoadResult,
) -> DddLoadResult:
    """Doklej kolejny import do otwartego profilu (ta sama karta / rejestracja)."""
    if existing is None:
        return incoming
    if existing.profile_key != incoming.profile_key:
        raise DddLoadError(
            "Ten plik należy do innego profilu niż otwarty brudnopis.\n\n"
            f"Otwarty: {existing.profile_label}\n"
            f"Plik: {incoming.profile_label}\n\n"
            "Wybierz „Nowy” albo wczytaj pliki tego samego profilu."
        )
    segments = replace_range(
        existing.segments,
        incoming.segments,
        incoming.date_from,
        incoming.date_to,
    )
    crossings = replace_crossings_range(
        existing.border_crossings,
        incoming.border_crossings,
        incoming.date_from,
        incoming.date_to,
    )
    places = replace_places_range(
        existing.places,
        incoming.places,
        incoming.date_from,
        incoming.date_to,
    )
    gnss_points = replace_gnss_points_range(
        existing.gnss_points,
        incoming.gnss_points,
        incoming.date_from,
        incoming.date_to,
    )
    dates = [
        d
        for d in (
            existing.date_from,
            existing.date_to,
            incoming.date_from,
            incoming.date_to,
        )
        if d is not None
    ]
    filenames = tuple(dict.fromkeys((*existing.filenames, *incoming.filenames)))
    fields = {
        "driver_name": existing.driver_name,
        "vehicle_plate": existing.vehicle_plate,
        "source_kind": existing.source_kind,
        "filenames": filenames,
    }
    return replace(
        existing,
        segments=segments,
        border_crossings=crossings,
        places=places,
        gnss_points=gnss_points,
        title=_title_for(fields),
        filenames=filenames,
        date_from=min(dates) if dates else None,
        date_to=max(dates) if dates else None,
    )



def load_ddd_profiles(path: str | Path) -> list[DddLoadResult]:
    """Parse once; a VU file may contain several independently identified drivers."""
    from ddd_analyzer.parser import parse_ddd_file
    from ddd_analyzer.models import DddFileKind
    analysis = parse_ddd_file(path)
    if analysis.file_kind == DddFileKind.VU:
        parts = analysis.driver_analyses
        if not isinstance(parts, list) or not parts:
            raise DddLoadError("VU: brak aktywności z pewnym przypisaniem do karty i slotu kierowcy (włożenie/wyjęcie). Nie przypisano do profilu.")
    else:
        parts = [analysis]
    return [load_ddd_file(path, _analysis=part) for part in parts]
