from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.models import Scenario, Block, ActivityKind, copy_scenario
from tests.test_performance_regressions import card
from brudnopis_ot.core.hudonizator_reader import RawSegment


def test_history_restore_is_itself_reversible_and_repeated_saves_are_ignored(tmp_path):
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    profile = store.create_template('Synthetic', date(2026, 10, 5))
    initial = store.load_template_scenario(profile.profile_key)
    first = copy_scenario(initial)
    first.blocks.append(Block(ActivityKind.WORK, datetime(2026, 10, 5), datetime(2026, 10, 5, 8)))
    store.save_template_scenario(profile.profile_key, first)
    store.save_template_scenario(profile.profile_key, first)
    assert len(store.list_edit_history(profile.profile_key)) == 1
    second = copy_scenario(first)
    second.blocks[0].end += timedelta(hours=1)
    store.save_template_scenario(profile.profile_key, second)
    history = store.list_edit_history(profile.profile_key)
    assert history[0]['summary']['changed'] == 1
    store.restore_edit_revision(profile.profile_key, history[0]['id'])
    assert store.load_template_scenario(profile.profile_key).blocks[0].end.hour == 8
    undo_restore = store.list_edit_history(profile.profile_key)[0]
    assert undo_restore['reason'] == 'restore'
    store.restore_edit_revision(profile.profile_key, undo_restore['id'])
    assert store.load_template_scenario(profile.profile_key).blocks[0].end.hour == 9


def test_history_is_bounded_per_profile_and_corruption_never_changes_current_data(tmp_path):
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    profile = store.create_template('Synthetic', date(2026, 10, 5))
    scenario = store.load_template_scenario(profile.profile_key)
    for i in range(35):
        scenario.title = f'Synthetic {i}'
        store.save_template_scenario(profile.profile_key, scenario)
    history = store.list_edit_history(profile.profile_key)
    assert len(history) == 25
    with store._connect() as conn:
        conn.execute("UPDATE profile_edit_history SET payload=x'0001' WHERE id=?", (history[0]['id'],))
    with pytest.raises(ValueError, match='uszkodzona'):
        store.restore_edit_revision(profile.profile_key, history[0]['id'])
    assert store.load_template_scenario(profile.profile_key).title == 'Synthetic 34'


def test_restoring_old_ddd_edits_keeps_newly_imported_source_history(tmp_path):
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    profile = store.upsert(card([RawSegment(date(2026, 10, 5), 'drive', 0, 60)]))
    original = store.load_ddd_scenario(profile.profile_key)
    modified = copy_scenario(original)
    modified.blocks[0].end -= timedelta(minutes=10)
    store.save_ddd_scenario(profile.profile_key, modified)
    modified.blocks[0].end -= timedelta(minutes=10)
    store.save_ddd_scenario(profile.profile_key, modified)
    chosen = store.list_edit_history(profile.profile_key)[0]['id']
    incoming = replace(profile, segments=[*profile.segments, RawSegment(date(2026, 10, 6), 'work', 0, 120)],
                       date_to=date(2026, 10, 6))
    store.merge_incoming(incoming)
    store.restore_edit_revision(profile.profile_key, chosen)
    restored = store.load_ddd_scenario(profile.profile_key)
    assert any(b.start.date() == date(2026, 10, 6) for b in restored.blocks)
    assert max(b.end for b in restored.blocks if b.start.date() == date(2026, 10, 5)).minute == 50


def test_history_cannot_be_restored_into_another_profile(tmp_path):
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    first = store.create_template('One', date(2026, 10, 5))
    second = store.create_template('Two', date(2026, 10, 5))
    scenario = store.load_template_scenario(first.profile_key)
    scenario.title = 'Changed'
    store.save_template_scenario(first.profile_key, scenario)
    revision = store.list_edit_history(first.profile_key)[0]['id']
    with pytest.raises(ValueError):
        store.restore_edit_revision(second.profile_key, revision)
    assert store.load_template_scenario(second.profile_key).title == 'Two'
