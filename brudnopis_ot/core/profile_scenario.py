"""Persistent scenario corrections, kept separately from imported DDD evidence."""
from dataclasses import replace
from hashlib import sha256
from bisect import bisect_right

from brudnopis_ot.core.ddd_blocks import scenario_from_segments
from brudnopis_ot.core.serialize import block_from_dict, scenario_from_dict


def original_scenario(profile):
    scenario = scenario_from_segments(profile.segments, title=profile.title,
                                      places=profile.places, border_crossings=profile.border_crossings)
    for block in scenario.blocks:
        block.id = stable_id(block)
    return scenario


def stable_id(block):
    identity = f'{block.kind.value}:{block.start.isoformat()}:{block.end.isoformat()}'
    return 'ddd_' + sha256(identity.encode()).hexdigest()[:20]


def exact_scenario(data):
    # General JSON loading coalesces adjacent blocks, which would lose edited IDs
    # and anchors. Profile corrections must round-trip exactly.
    scenario = scenario_from_dict({**data, 'blocks': []})
    scenario.blocks = [block_from_dict(b) for b in data.get('blocks', [])]
    return scenario


def rebase_scenario(baseline, edited, current):
    """Replay changed intervals onto new imports without freezing the history."""
    before = {b.id: b for b in baseline.blocks}
    after = {b.id: b for b in edited.blocks}
    removed = [b for bid, b in before.items() if after.get(bid) != b]
    additions = [replace(b) for bid, b in after.items() if before.get(bid) != b]
    # Subtract the union of changed source intervals. Rechecking every removed
    # block against every current block made long edited histories quadratic.
    cuts = []
    for old in sorted(removed, key=lambda b: b.start):
        if cuts and old.start <= cuts[-1][1]:
            cuts[-1] = (cuts[-1][0], max(cuts[-1][1], old.end))
        else:
            cuts.append((old.start, old.end))
    cut_ends = [end for _, end in cuts]
    blocks = []
    for block in current.blocks:
        pieces = []
        cursor = block.start
        index = bisect_right(cut_ends, cursor)
        while index < len(cuts) and cuts[index][0] < block.end:
            start, end = cuts[index]
            if cursor < start:
                pieces.append((cursor, start))
            cursor = max(cursor, end)
            if cursor >= block.end:
                break
            index += 1
        if cursor < block.end:
            pieces.append((cursor, block.end))
        for start, end in pieces:
            part = replace(block, start=start, end=end)
            if (start, end) != (block.start, block.end):
                part.id = stable_id(part)
            blocks.append(part)
    result = replace(edited, blocks=sorted([*blocks, *additions], key=lambda b: (b.start, b.end, b.id)))
    if result.anchor_block_id and not result.get_block(result.anchor_block_id):
        # An unedited anchor can grow when a new DDD extends the same activity.
        old = after.get(result.anchor_block_id)
        candidate = next((b for b in result.blocks if old and b.kind == old.kind
                          and b.start <= old.start < b.end), None)
        result.anchor_block_id = candidate.id if candidate else None
    return result


def update_full_scenario(source, previous_view, view):
    """Write only visible changes; axis clipping never deletes hidden history."""
    blocks = {b.id: replace(b) for b in source.blocks}
    current = {b.id: b for b in view.blocks}
    for identity in previous_view.keys() - current.keys():
        blocks.pop(identity, None)
    for identity, block in current.items():
        if previous_view.get(identity) != block:
            blocks[identity] = replace(block)
    return replace(view, blocks=list(blocks.values()))
