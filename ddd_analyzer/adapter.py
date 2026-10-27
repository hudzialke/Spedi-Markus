from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from ddd_analyzer.gnss_crossings import derive_crossings_from_gnss
from ddd_analyzer.models import (
    ActivitySegment,
    ActivityType,
    BorderCrossing,
    DddAnalysis,
    DddFileKind,
    DriverInfo,
    GnssPoint,
    OpenActivity,
    DailySummary,
    PlaceRecord,
    VehicleRef,
)
from ddd_analyzer.nations import to_iso2_country
from ddd_analyzer.summarize import build_daily_summaries

_RAW_ACTIVITY_MAP: dict[str, ActivityType] = {
    "REST": ActivityType.REST,
    "AVAILABLE": ActivityType.AVAILABILITY,
    "WORK": ActivityType.WORK,
    "DRIVE": ActivityType.DRIVE,
}

_NA = {"N/A", "", None}


def _clean(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return default if text in _NA else text


def _parse_day(value: str) -> date:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Nierozpoznany format daty: {value!r}")


def _parse_time_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value or value in _NA:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _map_activity(raw: str) -> ActivityType:
    key = str(raw).strip().upper()
    key = {"AVAILABILITY": "AVAILABLE", "POA": "AVAILABLE"}.get(key, key)
    if key not in _RAW_ACTIVITY_MAP:
        raise ValueError(f"Nieznana aktywność DDD: {raw!r}")
    return _RAW_ACTIVITY_MAP[key]


def _slot_rank(change: dict[str, Any]) -> int:
    """Lower rank = preferred driver timeline.

    VU downloads often mix First (kierowca) and Second (pusty / drugi kierowca).
    Mixing them into one day timeline falsely turns DRIVE into AVAILABLE.
    """
    slot = str(change.get("slot") or "").strip().lower()
    if slot in {"first", "driver", "0", "1"}:
        return 0
    if slot in {"second", "co-driver", "codriver", "2"}:
        return 2
    if change.get("card_inserted") is True:
        return 0
    if change.get("card_inserted") is False:
        return 2
    return 1


def _driver_slot_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not changes:
        return []
    return [c for c in changes if str(c.get("slot", "")).lower() in {"first", "driver"}]


def _segments_from_day(day: date, changes: list[dict[str, Any]], *, vu: bool = False) -> list[ActivitySegment]:
    if not changes:
        return []
    # Keep only one slot (kierowca). Dual-slot VU records must not interleave.
    ordered = sorted(
        _driver_slot_changes(changes) if vu else changes,
        key=lambda c: (_parse_time_minutes(c["time"]), _slot_rank(c)),
    )
    segments: list[ActivitySegment] = []
    for idx, change in enumerate(ordered):
        start = _parse_time_minutes(change["time"])
        end = _parse_time_minutes(ordered[idx + 1]["time"]) if idx + 1 < len(ordered) else 24 * 60
        if end <= start:
            continue
        if vu and change.get("card_inserted") is not True:
            continue
        segments.append(
            ActivitySegment(
                day=day,
                activity=_map_activity(change.get("activity", "UNKNOWN")),
                start_minute=start,
                end_minute=end,
            )
        )
    return segments


def _latest_card_download(raw: dict[str, Any]) -> datetime | None:
    """LastCardDownload (EF); may describe an older download, not this file."""
    best: datetime | None = None
    for item in raw.get("card_downloads") or []:
        if not isinstance(item, dict):
            continue
        dt = _parse_iso_dt(item.get("download_time"))
        if dt is None:
            continue
        dt = _as_utc_naive(dt)
        if best is None or dt > best:
            best = dt
    return best


def clip_segments_to_cutoff(
    segments: list[ActivitySegment],
    cutoff: datetime | None,
) -> list[ActivitySegment]:
    """Keep only complete minutes before a validated recording boundary."""
    if not segments or cutoff is None:
        return segments
    cutoff_utc = _as_utc_naive(cutoff)
    clip_min = cutoff_utc.hour * 60 + cutoff_utc.minute

    clipped: list[ActivitySegment] = []
    for seg in segments:
        if seg.day < cutoff_utc.date():
            clipped.append(seg)
            continue
        if seg.day > cutoff_utc.date():
            continue
        if seg.end_minute <= clip_min:
            clipped.append(seg)
            continue
        if seg.start_minute >= clip_min:
            continue
        clipped.append(
            ActivitySegment(
                day=seg.day,
                activity=seg.activity,
                start_minute=seg.start_minute,
                end_minute=clip_min,
            )
        )
    return clipped


def activity_recording_end(raw: dict, file_kind: DddFileKind) -> datetime | None:
    """Bound coverage using file contents, never its filename or the PC clock.

    A change records the start of an activity, not its duration. Without a
    current source timestamp, only intervals up to the last change are known.
    LastCardDownload can refer to a previous download and must not truncate
    newer activity records.
    """
    changes = [datetime.combine(_parse_day(d['date']), datetime.min.time())
               + timedelta(minutes=_parse_time_minutes(c['time']))
               for d in raw.get('activities') or [] for c in d.get('changes') or []]
    if not changes:
        return None
    last_change = max(changes)
    if file_kind != DddFileKind.VU:
        downloaded = _latest_card_download(raw)
        return downloaded if downloaded is not None and downloaded >= last_change else last_change
    values = [(raw.get('metadata') or {}).get('current_datetime'),
              ((raw.get('vu_overview') or {}).get('downloadable_period') or {}).get('max')]
    values.extend(r.get('max_downloadable') for r in raw.get('downloadable_periods') or [])
    bounds = [_as_utc_naive(dt) for value in values if (dt := _parse_iso_dt(value)) is not None]
    # Contradictory VU bounds must never extend coverage beyond either bound.
    return min(bounds) if bounds else last_change


def open_card_activity(raw: dict, cutoff: datetime | None, file_kind: DddFileKind) -> OpenActivity | None:
    """Keep the known last card change separately from measurable intervals."""
    if file_kind != DddFileKind.CARD or cutoff is None:
        return None
    changes = [(datetime.combine(_parse_day(day['date']), datetime.min.time())
                + timedelta(minutes=_parse_time_minutes(change['time'])), change)
               for day in raw.get('activities') or [] for change in day.get('changes') or []]
    if not changes:
        return None
    last = max(instant for instant, _ in changes)
    if _as_utc_naive(cutoff) > last:
        return None
    kinds = {_map_activity(change.get('activity', 'UNKNOWN')) for instant, change in changes if instant == last}
    if len(kinds) != 1:
        raise ValueError('Sprzeczne ostatnie aktywności karty DDD.')
    return OpenActivity(kinds.pop(), last)


def apply_activity_cutoff(
    analysis: DddAnalysis,
    cutoff: datetime | None,
) -> DddAnalysis:
    """Przytnij segmenty i przelicz podsumowania dnia (np. po ustawieniu read_at)."""
    if cutoff is None or not analysis.segments:
        return analysis
    new_segments = clip_segments_to_cutoff(analysis.segments, cutoff)
    if new_segments is analysis.segments or new_segments == analysis.segments:
        return analysis
    odo = {s.day: s.odometer_km for s in analysis.daily_summaries}
    analysis.segments = new_segments
    analysis.daily_summaries = build_daily_summaries(new_segments, odo)
    days = [s.day for s in new_segments]
    analysis.date_range = (min(days), max(days)) if days else (None, None)
    return analysis


def _driver_from_raw(raw: dict[str, Any]) -> DriverInfo:
    driver = raw.get("driver") or {}
    return DriverInfo(
        card_number=_clean(driver.get("card_number")),
        surname=_clean(driver.get("surname")),
        firstname=_clean(driver.get("firstname")),
        birth_date=_clean(driver.get("birth_date")),
        expiry_date=_clean(driver.get("expiry_date")),
        issuing_nation=_clean(driver.get("issuing_nation")),
        licence_number=_clean(driver.get("licence_number")),
    )


def _vehicle_from_raw(raw: dict[str, Any], source: str) -> VehicleRef:
    vehicle = raw.get("vehicle") or {}
    return VehicleRef(
        plate=_clean(vehicle.get("plate")),
        registration_nation=_clean(vehicle.get("registration_nation")),
        vin=_clean(vehicle.get("vin")),
        source=source,
    )


def _vehicles_from_sessions(raw: dict[str, Any]) -> list[VehicleRef]:
    sessions = raw.get("vehicle_sessions") or []
    vehicles: list[VehicleRef] = []
    seen: set[tuple[str, str, str]] = set()
    for session in sessions:
        plate = _clean(session.get("vehicle_plate"))
        nation = _clean(session.get("vehicle_nation"))
        vin = _clean(session.get("vin"))
        key = (plate, nation, vin)
        if key in seen:
            continue
        seen.add(key)
        vehicles.append(
            VehicleRef(
                plate=plate,
                registration_nation=nation,
                vin=vin,
                odometer_begin=session.get("odometer_begin"),
                odometer_end=session.get("odometer_end"),
                first_use=_parse_iso_dt(session.get("start")),
                last_use=_parse_iso_dt(session.get("end")),
                source="card_session",
            )
        )
    return vehicles


def _odometer_from_crossing(item: dict[str, Any]) -> int | None:
    for key in ("odometer_km", "vehicle_odometer_value", "odometer"):
        val = item.get(key)
        if val is None or val == "" or val == "—":
            continue
        try:
            return int(val)
        except (TypeError, ValueError):
            continue
    return None


def _geo_from_crossing(item: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = item.get("latitude")
    lon = item.get("longitude")
    if lat is None or lon is None:
        place = item.get("gnss_place") or {}
        if isinstance(place, dict):
            lat = place.get("latitude", place.get("lat"))
            lon = place.get("longitude", place.get("lon"))
            geo = place.get("geo")
            if (lat is None or lon is None) and isinstance(geo, dict):
                lat = geo.get("latitude", geo.get("lat", geo.get("latitude_deg")))
                lon = geo.get("longitude", geo.get("lon", geo.get("longitude_deg")))
    try:
        lat_f = float(lat) if lat is not None and lat != "—" else None
    except (TypeError, ValueError):
        lat_f = None
    try:
        lon_f = float(lon) if lon is not None and lon != "—" else None
    except (TypeError, ValueError):
        lon_f = None
    return lat_f, lon_f


def _timestamp_from_crossing(item: dict[str, Any]) -> datetime | None:
    dt = _parse_iso_dt(item.get("timestamp"))
    if dt is not None:
        return _as_utc_naive(dt)
    place = item.get("gnss_place")
    if isinstance(place, dict):
        dt = _parse_iso_dt(place.get("timestamp"))
        if dt is not None:
            return _as_utc_naive(dt)
    return None


def _border_crossings_from_raw(raw: dict[str, Any]) -> list[BorderCrossing]:
    out: list[BorderCrossing] = []
    for item in raw.get("border_crossings") or []:
        if not isinstance(item, dict):
            continue
        ts = _timestamp_from_crossing(item)
        if ts is None:
            continue
        left = to_iso2_country(
            item.get("country_left") or item.get("nation_from")
        )
        entered = to_iso2_country(
            item.get("country_entered") or item.get("nation_to")
        )
        if not left and not entered:
            continue
        lat, lon = _geo_from_crossing(item)
        out.append(
            BorderCrossing(
                timestamp=ts,
                country_left=left,
                country_entered=entered,
                odometer_km=_odometer_from_crossing(item),
                latitude=lat,
                longitude=lon,
                source="card",
            )
        )
    out.sort(key=lambda c: c.timestamp)
    return out


def _gnss_points_from_raw(raw: dict[str, Any]) -> list[GnssPoint]:
    """Punkty z gnss_ad_records (karta flat lat/lon albo VU gnss_place)."""
    out: list[GnssPoint] = []
    for item in raw.get("gnss_ad_records") or []:
        if not isinstance(item, dict):
            continue
        ts = _parse_iso_dt(item.get("timestamp")) or _parse_iso_dt(
            item.get("gnss_timestamp")
        )
        if ts is None:
            place = item.get("gnss_place")
            if isinstance(place, dict):
                ts = _parse_iso_dt(place.get("timestamp"))
        if ts is None:
            continue
        ts = _as_utc_naive(ts)
        lat, lon = _geo_from_crossing(item)
        if lat is None or lon is None:
            continue
        out.append(
            GnssPoint(
                occurred_at=ts,
                latitude=lat,
                longitude=lon,
                odometer_km=_odometer_from_crossing(item),
            )
        )
    out.sort(key=lambda p: p.occurred_at)
    return out


def _entry_type_from_place(item: dict[str, Any]) -> str:
    raw = str(item.get("entry_type") or "").strip().upper()
    if raw in {"START", "END"}:
        return raw
    code = item.get("type_code")
    try:
        code_i = int(code) if code is not None else -1
    except (TypeError, ValueError):
        code_i = -1
    if code_i == 0:
        return "START"
    if code_i == 1:
        return "END"
    return raw or ""


def _places_from_raw(raw: dict[str, Any]) -> list[PlaceRecord]:
    out: list[PlaceRecord] = []
    for item in raw.get("places") or []:
        if not isinstance(item, dict):
            continue
        ts = _parse_iso_dt(item.get("timestamp"))
        if ts is None:
            continue
        ts = _as_utc_naive(ts)
        country = to_iso2_country(item.get("nation") or item.get("country_code"))
        if not country:
            continue
        entry_type = _entry_type_from_place(item)
        lat, lon = _geo_from_crossing(item)
        out.append(
            PlaceRecord(
                timestamp=ts,
                entry_type=entry_type or "START",
                country_code=country,
                odometer_km=_odometer_from_crossing(item),
                latitude=lat,
                longitude=lon,
            )
        )
    out.sort(key=lambda p: (p.timestamp, p.entry_type, p.country_code))
    return out


def parse_tacho_result(raw: dict[str, Any], source_path: str, file_kind: DddFileKind) -> DddAnalysis:
    meta = raw.get("metadata") or {}
    activities = raw.get("activities") or []

    segments: list[ActivitySegment] = []
    odometer_by_day: dict[date, int | None] = {}
    for day_record in activities:
        day = _parse_day(day_record["date"])
        odometer_by_day[day] = day_record.get("odometer_km")
        segments.extend(_segments_from_day(day, day_record.get("changes") or [], vu=file_kind == DddFileKind.VU))

    cutoff = activity_recording_end(raw, file_kind)
    segments = clip_segments_to_cutoff(segments, cutoff)
    open_activity = open_card_activity(raw, cutoff, file_kind)
    daily_summaries = build_daily_summaries(segments, odometer_by_day)
    if open_activity and not any(s.day == open_activity.start.date() for s in daily_summaries):
        daily_summaries.append(DailySummary(day=open_activity.start.date()))
        daily_summaries.sort(key=lambda s: s.day)
    days = [s.day for s in segments]
    if open_activity:
        days.append(open_activity.start.date())
    date_range = (min(days), max(days)) if days else (None, None)
    border_crossings = _border_crossings_from_raw(raw)
    places = _places_from_raw(raw)
    gnss_points = _gnss_points_from_raw(raw)
    if not border_crossings and gnss_points:
        border_crossings = derive_crossings_from_gnss(gnss_points)

    primary_vehicle = _vehicle_from_raw(raw, "vu" if file_kind == DddFileKind.VU else "card")
    vehicles = _vehicles_from_sessions(raw)
    if primary_vehicle.plate and not any(v.plate == primary_vehicle.plate for v in vehicles):
        vehicles.insert(0, primary_vehicle)

    analysis = DddAnalysis(
        source_path=source_path,
        file_kind=file_kind,
        generation=_clean(meta.get("generation"), "Nieznana"),
        is_vu=bool(meta.get("is_vu")),
        driver=_driver_from_raw(raw),
        vehicle=primary_vehicle,
        vehicles=vehicles,
        segments=segments,
        daily_summaries=daily_summaries,
        date_range=date_range,
        filename=_clean(meta.get("filename"), Path(source_path).name),
        border_crossings=border_crossings,
        places=places,
        gnss_points=gnss_points,
        open_activity=open_activity,
    )

    if file_kind == DddFileKind.VU:
        analysis.driver_analyses = _vu_driver_analyses(raw, analysis)
        for driver in analysis.driver_analyses:
            apply_activity_cutoff(driver, cutoff)
        analysis.driver_analyses = [driver for driver in analysis.driver_analyses if driver.segments]
    return analysis


def _vu_driver_analyses(raw: dict, vehicle: DddAnalysis) -> list[DddAnalysis]:
    """Attribute only occupied slot intervals covered by unambiguous card IW records.

    A vehicle timeline is not a driver's timeline. Missing withdrawal times,
    unrecognised slots and overlapping ownership are deliberately not inferred.
    """
    sessions = []
    drivers = {}
    for record in raw.get("card_iw_records") or []:
        card = record.get("card") or {}
        if card.get("present") is False or card.get("card_type") != 1:
            continue
        number = _clean(card.get("card_number"))
        # G1 FullCardNumber decoder prefixes the nation; G2 returns 16 chars.
        nation = _clean(card.get("nation"))
        if nation and len(number) == len(nation) + 16 and number.startswith(nation):
            number = number[len(nation):]
        if len(number) != 16 or not number.isascii() or not number.isalnum():
            continue
        start = _parse_iso_dt(record.get("insertion_time"))
        end = _parse_iso_dt(record.get("withdrawal_time"))
        slot = record.get("card_slot")
        if start is None or end is None or slot not in (0, 1):
            continue
        start, end = _as_utc_naive(start), _as_utc_naive(end)
        if end <= start:
            continue
        sessions.append((start, end, "First" if slot == 0 else "Second", number))
        drivers[number] = DriverInfo(card_number=number,
            surname=_clean(record.get("holder_surname")),
            firstname=_clean(record.get("holder_first_names")))
    by_driver = {}
    for record in raw.get("activities") or []:
        day = _parse_day(record["date"])
        midnight = datetime.combine(day, datetime.min.time())
        for slot in ("First", "Second"):
            changes = sorted((c for c in record.get("changes", []) if c.get("slot") == slot),
                             key=lambda c: _parse_time_minutes(c["time"]))
            for i, change in enumerate(changes):
                if change.get("card_inserted") is not True:
                    continue
                lo = midnight + timedelta(minutes=_parse_time_minutes(change["time"]))
                hi = midnight + timedelta(minutes=_parse_time_minutes(changes[i+1]["time"]) if i+1 < len(changes) else 1440)
                relevant = [s for s in sessions if s[2] == slot and s[0] < hi and s[1] > lo]
                boundaries = sorted({lo, hi, *(max(lo, s[0]) for s in relevant), *(min(hi, s[1]) for s in relevant)})
                for a, b in zip(boundaries, boundaries[1:]):
                    owners = {s[3] for s in relevant if s[0] <= a and s[1] >= b}
                    if len(owners) != 1:
                        continue
                    number = next(iter(owners))
                    # Minute model: retain only complete minutes inside ownership.
                    start_min = int(((a-midnight).total_seconds()+59)//60)
                    end_min = int((b-midnight).total_seconds()//60)
                    if start_min < end_min:
                        by_driver.setdefault(number, []).append(ActivitySegment(day=day,
                            activity=_map_activity(change.get("activity", "UNKNOWN")),
                            start_minute=start_min, end_minute=end_min))
    out = []
    for number, segments in sorted(by_driver.items()):
        # Exact duplicated daily records must not double the driver's minutes.
        unique = {(s.day, s.start_minute, s.end_minute, s.activity): s for s in segments}
        segments = sorted(unique.values(), key=lambda s: (s.day, s.start_minute, s.end_minute))
        if any(a.day == b.day and a.end_minute > b.start_minute for a,b in zip(segments, segments[1:])):
            continue  # conflicting records, never assign an ambiguous timeline
        days = [s.day for s in segments]
        out.append(replace(vehicle, driver=drivers[number], segments=segments,
            daily_summaries=build_daily_summaries(segments, {}), date_range=(min(days), max(days)),
            driver_analyses=[], border_crossings=[], places=[], gnss_points=[]))
    return out
