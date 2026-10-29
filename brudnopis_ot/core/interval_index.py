"""Candidate lookup for overlapping time intervals, including nested ranges."""
from bisect import bisect_left, bisect_right
from itertools import accumulate


class IntervalIndex:
    def __init__(self, items, bounds):
        self.entries = []
        for index, item in enumerate(items):
            start, end = bounds(item)
            self.entries.append((start, end, index, item))
        self.entries.sort(key=lambda entry: entry[0])
        self.starts = [entry[0] for entry in self.entries]
        self.maximum_ends = list(accumulate((entry[1] for entry in self.entries), max))

    def overlapping(self, start, end, *, original_order=False):
        if end <= start:
            return []
        hi = bisect_left(self.starts, end)
        lo = bisect_right(self.maximum_ends, start, 0, hi)
        candidates = [entry for entry in self.entries[lo:hi] if entry[1] > start]
        if original_order:
            candidates.sort(key=lambda entry: entry[2])
        return [entry[3] for entry in candidates]
