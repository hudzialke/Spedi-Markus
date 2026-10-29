"""Compatibility imports for the single authoritative weekly-rest engine.

New consumers should import brudnopis_ot.core.weekly_rules directly. Existing
phone/watchlist imports continue to resolve the very same functions and types.
"""
from brudnopis_ot.core import weekly_rules as _engine
from brudnopis_ot.core.weekly_rules import (
    analyze_scenario, build_weekly_rests, detect_mobility_pairs,
)


def __getattr__(name):
    return getattr(_engine, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_engine)))
