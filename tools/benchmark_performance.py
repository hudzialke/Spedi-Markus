"""Repeatable, offline performance probes; never reads the user's application data.

Run with the project Python: python tools/benchmark_performance.py --output result.json
All widgets use Qt's offscreen platform and all databases contain generated records.
"""
from __future__ import annotations

import argparse
import cProfile
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def measure(function, repeats=5):
    times = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = function()
        times.append((time.perf_counter() - start) * 1000)
    return {'median_ms': round(statistics.median(times), 3),
            'max_ms': round(max(times), 3), 'samples_ms': [round(t, 3) for t in times]}, result


def run(root, profile_path=None):
    for variable, folder in [('SPEDI_DATA_DIR', 'data'), ('SPEDI_DISPATCHER_DATA_DIR', 'dispatcher'),
                             ('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming')]:
        os.environ[variable] = str(root / folder)
    os.environ['SPEDI_SETTINGS_FILE'] = str(root / 'settings.json')
    os.environ['SPEDI_LANGUAGE'] = 'pl'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter, QRegion
    from PySide6.QtWidgets import QApplication
    from brudnopis_ot.core.ddd_file_loader import DddLoadResult
    from brudnopis_ot.core.ddd_store import DddProfileStore
    from brudnopis_ot.core.hudonizator_reader import RawSegment
    from brudnopis_ot.core.models import Scenario, ActivityKind, Block
    from brudnopis_ot.core.validator import validate_scenario
    from brudnopis_ot.ui.week_grid import WeekGridWidget
    from ewidentor.core.brudnopis_import import load_month_segments

    app = QApplication.instance() or QApplication([])
    font_path = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts' / 'segoeui.ttf'
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 9))
    start_day = date(2023, 1, 2)
    days = 1095
    segments = [RawSegment(start_day + timedelta(days=d), 'drive' if i % 2 else 'rest',
                           i * 15, (i + 1) * 15) for d in range(days) for i in range(96)]
    store = DddProfileStore(root / 'history.sqlite')
    profile = store.upsert(DddLoadResult(segments, 'Synthetic', 'Synthetic', 'BENCH', None, 'card', (),
                                       start_day, start_day + timedelta(days=days-1)))
    report = {'python': sys.version.split()[0], 'fixture': {'days': days, 'segments': len(segments)}, 'probes': {}}
    report['probes']['load_month'], rows = measure(lambda: load_month_segments(store.path, profile.profile_key, 2025, 10))
    assert len(rows) == 31 * 96
    report['probes']['list_profiles'], _ = measure(store.list_profiles, repeats=3)
    if hasattr(store, 'list_profile_summaries'):
        report['probes']['profile_picker'], _ = measure(store.list_profile_summaries)

    from ddd_analyzer.models import ActivitySegment, ActivityType
    from ddd_analyzer.night_work import analyze_night_work
    night_segments = []
    for offset in range(365):
        day = start_day + timedelta(days=offset)
        for i in range(36):
            night_segments.append(ActivitySegment(day, ActivityType.DRIVE if i % 2 else ActivityType.WORK,
                                                   i*15, (i+1)*15))
        night_segments.append(ActivitySegment(day, ActivityType.REST, 540, 1440))
    report['probes']['night_year'], _ = measure(lambda: analyze_night_work(night_segments), repeats=3)

    roster = []
    roster_start = datetime(2026, 1, 5)
    for week in range(52):
        for day in range(5):
            base = roster_start + timedelta(weeks=week, days=day)
            roster.extend(Block(ActivityKind.DRIVE if i % 2 else ActivityKind.WORK,
                                base+timedelta(minutes=i*15), base+timedelta(minutes=(i+1)*15),
                                id=f'roster-{week}-{day}-{i}') for i in range(36))
            roster.append(Block(ActivityKind.REST, base+timedelta(hours=9), base+timedelta(days=1)))
        roster.append(Block(ActivityKind.REST, roster_start+timedelta(weeks=week, days=5),
                            roster_start+timedelta(weeks=week+1)))
    roster_scenario = Scenario(week_start=roster_start.date(), weeks=16, blocks=roster)
    report['probes']['validate_roster_year'], _ = measure(lambda: validate_scenario(roster_scenario), repeats=3)

    monday = date(2026, 6, 1)
    start = datetime.combine(monday, datetime.min.time())
    blocks = [Block(ActivityKind.DRIVE if i % 2 else ActivityKind.REST,
                    start + timedelta(minutes=i*15), start + timedelta(minutes=(i+1)*15), id=f'b{i}')
              for i in range(16*7*96)]
    scenario = Scenario(week_start=monday, weeks=16, blocks=blocks)
    report['probes']['validate_16_weeks'], result = measure(lambda: validate_scenario(scenario), repeats=3)
    report['result'] = {'findings': len(result.findings), 'weekly_rests': len(result.weekly_rests)}
    grid = WeekGridWidget()
    grid.set_scenario(scenario)
    grid.set_validation(result)
    grid.resize(grid.minimumSize())
    image = QImage(grid.size(), QImage.Format.Format_ARGB32_Premultiplied)

    def paint(region=None):
        painter = QPainter(image)
        try:
            if region is None:
                grid.render(painter, QPoint())
            else:
                grid.render(painter, QPoint(), QRegion(region))
        finally:
            painter.end()

    report['probes']['timeline_full'], _ = measure(paint, repeats=3)
    report['probes']['timeline_viewport'], _ = measure(lambda: paint(QRect(0, 0, grid.width(), 300)), repeats=3)
    if profile_path:
        profiler = cProfile.Profile()
        profiler.runcall(lambda: (validate_scenario(scenario), paint()))
        profiler.dump_stats(str(profile_path))
    grid.close()
    app.processEvents()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--source-root', type=Path, help='Optional reference checkout to compare')
    args = parser.parse_args()
    if args.source_root:
        sys.path.insert(0, str(args.source_root.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent.resolve(), prefix='spedi-benchmark-', ignore_cleanup_errors=True) as temporary:
        result = run(Path(temporary), args.profile)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
