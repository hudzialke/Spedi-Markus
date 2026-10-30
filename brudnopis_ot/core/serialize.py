from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from brudnopis_ot.core.ddd_blocks import merge_contiguous_blocks
from brudnopis_ot.core.models import ActivityKind, Block, Scenario


def _dt_to_str(dt: datetime) -> str:
    # Preserve the legacy wall time and second precision without invoking
    # strftime for every endpoint in a large scenario.
    if dt.year < 1000:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.isoformat(timespec='seconds')


def _dt_from_str(value: str) -> datetime:
    return datetime.fromisoformat(value)


def block_to_dict(block: Block) -> dict[str, Any]:
    return {
        "id": block.id,
        "kind": block.kind.value,
        "start": _dt_to_str(block.start),
        "end": _dt_to_str(block.end),
        "country_start": block.country_start,
        "started_abroad": block.started_abroad,
        "label": block.label,
    }


def block_from_dict(data: dict[str, Any]) -> Block:
    return Block(
        id=str(data["id"]) if data.get("id") else uuid4().hex[:12],
        kind=ActivityKind(data["kind"]),
        start=_dt_from_str(data["start"]),
        end=_dt_from_str(data["end"]),
        country_start=data.get("country_start"),
        started_abroad=bool(data.get("started_abroad", False)),
        label=data.get("label"),
    )


def scenario_to_dict(scenario: Scenario) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "title": scenario.title,
        "week_start": scenario.week_start.isoformat(),
        "weeks": scenario.weeks,
        "employer_country": scenario.employer_country,
        "blocks": [block_to_dict(b) for b in scenario.sorted_blocks()],
    }
    if scenario.specific_conditions:
        payload['specific_conditions'] = [
            {'occurred_at': c.occurred_at.isoformat(), 'type_code': c.type_code}
            for c in scenario.specific_conditions]
    if scenario.anchor_block_id:
        payload["anchor_block_id"] = scenario.anchor_block_id
    return payload


def scenario_from_dict(data: dict[str, Any]) -> Scenario:
    from ddd_analyzer.specific_conditions import SpecificCondition
    blocks = merge_contiguous_blocks(
        [block_from_dict(b) for b in data.get("blocks", [])]
    )
    return Scenario(
        week_start=date.fromisoformat(data["week_start"]),
        weeks=int(data.get("weeks", 5)),
        employer_country=str(data.get("employer_country", "PL")),
        title=str(data.get("title", "Scenariusz")),
        blocks=blocks,
        anchor_block_id=data.get("anchor_block_id"),
        specific_conditions=tuple(SpecificCondition(datetime.fromisoformat(c['occurred_at']), int(c['type_code']))
                                  for c in data.get('specific_conditions', [])),
    )


def save_scenario(scenario: Scenario, path: str | Path) -> Path:
    """Zapisz scenariusz JSON. Tworzy katalogi nadrzędne; zwraca ścieżkę pliku."""
    path = Path(path)
    if path.suffix.lower() != ".json":
        path = path.with_suffix(".json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(scenario_to_dict(scenario), ensure_ascii=False, indent=2) + "\n"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except OSError as exc:
        raise OSError(f"Nie udało się zapisać scenariusza:\n{path.resolve()}\n{exc}") from exc
    return path


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OSError(f"Nie udało się otworzyć scenariusza:\n{path.resolve()}\n{exc}") from exc
    return scenario_from_dict(data)
