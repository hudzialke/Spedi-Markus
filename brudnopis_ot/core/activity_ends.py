"""Confirmed terminal activity intervals; newer raw evidence always takes priority."""
from datetime import datetime, timedelta

from brudnopis_ot.core.hudonizator_reader import RawSegment


def confirmed_segments(segments, confirmations, open_activity=None, *, range_start=None, range_end=None):
    if not confirmations:
        return []
    bounds = [(datetime.combine(s.day, datetime.min.time()) + timedelta(minutes=s.start_minute),
               datetime.combine(s.day, datetime.min.time()) + timedelta(minutes=s.end_minute)) for s in segments]
    additions = []
    for row in confirmations:
        start, end = datetime.fromisoformat(row['start']), datetime.fromisoformat(row['end'])
        if any(a <= start < b for a, b in bounds):
            continue
        boundaries = [a for a, _ in bounds if a > start]
        if open_activity and open_activity.start > start:
            boundaries.append(open_activity.start)
        end = min([end, *boundaries])
        if range_end is not None:
            end = min(end, range_end)
        current = max(start, range_start) if range_start is not None else start
        while current < end:
            midnight = datetime.combine(current.date(), datetime.min.time())
            piece_end = min(end, midnight + timedelta(days=1))
            additions.append(RawSegment(current.date(), row['activity'],
                                        int((current - midnight).total_seconds() // 60),
                                        int((piece_end - midnight).total_seconds() // 60)))
            current = piece_end
    return additions
