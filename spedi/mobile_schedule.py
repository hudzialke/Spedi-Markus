"""Draft mobile data contract; no Firebase writes or UI changes on import.

The modified scenario is authoritative. Planned return is an independent plan,
never a replacement for an analyzer deadline. All instants sent to Android are
UTC ISO strings; week labels are calendar dates in Europe/Warsaw.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from brudnopis_ot.core.models import Scenario
from brudnopis_ot.core.serialize import scenario_to_dict

from spedi.mobile_rules import analyze_scenario

WARSAW = ZoneInfo("Europe/Warsaw")


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # Brudnopis blocks use naive UTC.
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _planned_instant(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Planowany powrót wymaga jawnej strefy czasowej.")
    return utc_iso(parsed)


def driver_document_id(profile_key: str) -> str:
    """Identity is based on the profile key, never on a mutable display name."""
    return sha256(profile_key.encode("utf-8")).hexdigest()


def build_driver_schedule(
    *, profile_key: str, display_name: str, scenario: Scenario,
    cycle_known: bool, planned_return_at: str | None = None,
    departure_at: str | None = None,
) -> dict[str, Any]:
    from brudnopis_ot.core.models import copy_scenario
    full = copy_scenario(scenario)
    view = copy_scenario(full)
    view.set_axis_monday(view.week_start)  # Same projection as 'Zmodyfikowany'.
    pending = []
    result = analyze_scenario(view, pending_debts=pending)
    anchor = full.get_block(full.anchor_block_id) if full.anchor_block_id else None
    week4 = full.week_start + timedelta(weeks=3)
    week4_start = datetime.combine(week4, time.min, tzinfo=WARSAW)
    # The open 6×24h window belongs to the last weekly rest, not a historical
    # anchor window appended by the engine. A planned future rest remains a plan.
    weekly = [rest for rest in result.weekly_rests if rest.is_weekly]
    rest_ref = weekly[-1].end if weekly else (anchor.start if anchor else None)
    next_rest = rest_ref + timedelta(days=6) if rest_ref else None
    debts = [{
        "source_block_ids": list(debt.source_rest_ids),
        "remaining_minutes": round(debt.remaining * 60),
        "compensation_due_at": utc_iso(debt.deadline),
        "latest_compensation_start_at": utc_iso(debt.latest_start),
        "mobility_pair": debt.from_pm_pair,
    } for debt in pending]
    latest_starts = [debt.latest_start for debt in pending if debt.latest_start]
    rest_candidates = [value for value in [next_rest, *latest_starts] if value]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "profile_key": profile_key,
        "display_name": display_name,
        "source_view": "modified",
        "display_zone": "Europe/Warsaw",
        "cycle_known": cycle_known,
        "week1_monday": full.week_start.isoformat() if cycle_known else None,
        "week4_monday": week4.isoformat() if cycle_known else None,
        "week4_sunday": (week4 + timedelta(days=6)).isoformat() if cycle_known else None,
        "week4_start_at": utc_iso(week4_start) if cycle_known else None,
        "anchor_at": utc_iso(anchor.start) if anchor else None,
        "planned_return_at": _planned_instant(planned_return_at),
        "departure_at": _planned_instant(departure_at),
        "next_weekly_rest_start_by": utc_iso(next_rest),
        "rest_start_by": utc_iso(min(rest_candidates)) if rest_candidates else None,
        "pending_compensations": debts,
        "findings": [{"code": f.code, "severity": f.severity,
                      "message": f.message_pl, "block_ids": list(f.related_block_ids)}
                     for f in result.findings],
        "hints": list(result.compensation_hints),
        "scenario": scenario_to_dict(full),
        "last_activity_at": utc_iso(max((b.end for b in full.blocks), default=None)),
        "calculation_basis": "modified_scenario_including_planned_blocks",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["revision"] = sha256(encoded.encode("utf-8")).hexdigest()
    return payload


def build_driver_documents(store, plans_by_profile=None, *, include_scenario=True) -> dict[str, dict[str, Any]]:
    """Export every stored profile including manual profiles and missing starts.

    The caller provides the store, so merely importing this module never opens
    or creates a user's database. Dispatcher driver IDs are a separate namespace.
    """
    documents = {}
    for profile in store.list_profile_summaries():
        plan = (plans_by_profile or {}).get(profile.profile_key, {})
        scenario = (store.load_template_scenario(profile.profile_key)
                    if profile.source_kind == "template"
                    else store.load_ddd_scenario(profile.profile_key, original=False))
        document = build_driver_schedule(
            profile_key=profile.profile_key,
            display_name=profile.driver_name or profile.title,
            scenario=scenario,
            cycle_known=store.get_cycle_start(profile.profile_key) is not None,
            planned_return_at=plan.get("planned_return_at"),
            departure_at=plan.get("departure_at"),
        )
        if not include_scenario:
            # The revision still covers the full scenario, but one-way phone
            # publication should not retain every driver's full timeline.
            document.pop('scenario')
        documents[driver_document_id(profile.profile_key)] = document
    return documents
