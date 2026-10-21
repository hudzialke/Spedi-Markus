from __future__ import annotations

from datetime import datetime, timedelta

from ddd_analyzer.models import ActivitySegment, ActivityType, ContinuousRestBlock


def _segment_bounds(segment: ActivitySegment) -> tuple[datetime, datetime]:
    start = datetime.combine(segment.day, datetime.min.time()) + timedelta(minutes=segment.start_minute)
    end = datetime.combine(segment.day, datetime.min.time()) + timedelta(minutes=segment.end_minute)
    return start, end


def continuous_rest_blocks(segments: list[ActivitySegment]) -> list[ContinuousRestBlock]:
    """Scal kolejne segmenty REST w ciagle bloki (takze przez polnoc)."""
    rest_segments = sorted(
        [s for s in segments if s.activity == ActivityType.REST],
        key=lambda s: (s.day, s.start_minute, s.end_minute),
    )
    if not rest_segments:
        return []

    blocks: list[ContinuousRestBlock] = []
    current_start, current_end = _segment_bounds(rest_segments[0])

    for segment in rest_segments[1:]:
        start, end = _segment_bounds(segment)
        if start == current_end:
            current_end = end
            continue
        if start < current_end:
            # Nakładanie — rozciągnij koniec
            current_end = max(current_end, end)
            continue
        blocks.append(ContinuousRestBlock(start=current_start, end=current_end))
        current_start, current_end = start, end

    blocks.append(ContinuousRestBlock(start=current_start, end=current_end))
    return blocks
