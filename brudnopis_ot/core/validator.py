from __future__ import annotations

from brudnopis_ot.core.models import Scenario, ValidationResult
from brudnopis_ot.core.display_tz import DEFAULT_ZONE, DisplayZone
from brudnopis_ot.core.weekly_rules import analyze_scenario


def validate_scenario(
    scenario: Scenario,
    *,
    zone: DisplayZone = DEFAULT_ZONE,
) -> ValidationResult:
    """Public entry: validate weekly-rest rules for a scratchpad scenario."""
    return analyze_scenario(scenario, zone=zone)
