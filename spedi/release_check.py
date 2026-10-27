"""Frozen-package smoke check using generated documents and isolated storage only."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile


def run(report_path: str) -> int:
    from spedi import __version__

    report = {'version': __version__, 'ok': False, 'checks': []}
    try:
        with tempfile.TemporaryDirectory(prefix='spedi-release-check-', ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            for variable, folder in (
                ('SPEDI_DATA_DIR', 'data'), ('SPEDI_DISPATCHER_DATA_DIR', 'dispatcher'),
                ('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'),
            ):
                os.environ[variable] = str(root / folder)
            os.environ['SPEDI_SETTINGS_FILE'] = str(root / 'settings.json')
            os.environ['QT_QPA_PLATFORM'] = 'offscreen'
            os.environ['SPEDI_LANGUAGE'] = 'pl'

            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QRectF, QThread
            import time
            from PySide6.QtPdf import QPdfDocument
            from reportlab.pdfgen.canvas import Canvas
            from markus_dispatcher.ui.document_import_dialog import (
                DocumentImportDialog, pdf_text_in_rect,
            )
            from markus_dispatcher.core.document_import import (
                OCR_LANGUAGES, read_spreadsheet, run_local_ocr, tesseract_path,
            )
            from markus_dispatcher.core.limited_process import run_limited
            from openpyxl import Workbook
            import xlrd
            from PIL import Image, ImageDraw, ImageFont

            app = QApplication.instance() or QApplication([])
            pdf_path = root / 'synthetic.pdf'
            canvas = Canvas(str(pdf_path), pagesize=(600, 800))
            for x, y, text in ((50, 700, 'LOAD DEMO'), (340, 700, 'UNLOAD DEMO'),
                               (50, 680, 'Test Street 12'), (340, 680, 'Other Street 45')):
                canvas.drawString(x, y, text)
            canvas.save()
            pdf = QPdfDocument()
            assert pdf.load(str(pdf_path)) == QPdfDocument.Error.None_
            assert pdf_text_in_rect(pdf, 0, QRectF(40, 80, 220, 80)) == 'LOAD DEMO\nTest Street 12'
            pdf.close()
            dialog = DocumentImportDialog()
            dialog.open_path(pdf_path)
            assert dialog.pdf.pageCount() == 1
            deadline = time.monotonic() + 15
            while dialog._pdf_worker is not None:
                assert time.monotonic() < deadline
                app.processEvents()
                QThread.msleep(5)
            assert dialog._page_index is not None
            dialog._region(QRectF(40/600, 80/800, 220/600, 80/800))
            assert dialog.selection.toPlainText() == 'LOAD DEMO\nTest Street 12'
            dialog.reject()
            app.processEvents()
            report['checks'].append('pdf-columns-and-import-dialog')

            from datetime import date, datetime, timedelta
            from dataclasses import replace
            from brudnopis_ot.core.models import Scenario, Block, ActivityKind
            from brudnopis_ot.core.ddd_store import DddProfileStore
            from spedi.weekly_watch import WatchStore, WatchOptions
            from spedi.weekly_watch_ui import WeeklyWatchDialog
            store = DddProfileStore(root/'synthetic.sqlite')
            driver = store.create_template('SYNTHETIC', date(2026, 8, 3))
            start = datetime(2026, 8, 7, 12)
            scenario = Scenario(week_start=date(2026, 8, 3), title='SYNTHETIC',
                                blocks=[Block(ActivityKind.REST, start, start+timedelta(hours=45))])
            store.save_template_scenario(driver.profile_key, scenario)
            WatchStore(store.path).save(driver.profile_key, WatchOptions(selected=True))
            watch = WeeklyWatchDialog(store)
            deadline = time.monotonic()+15
            while watch._worker is not None or watch._pending:
                assert time.monotonic() < deadline
                app.processEvents()
                QThread.msleep(5)
            assert driver.profile_key in watch.results
            watch.reject()
            report['checks'].append('background-weekly-watch')

            workbook = Workbook()
            workbook.active['A1'] = 'SYNTHETIC POD'
            sheet_path = root / 'synthetic.xlsx'
            workbook.save(sheet_path)
            assert 'SYNTHETIC POD' in read_spreadsheet(sheet_path)[0].html
            assert xlrd.__version__
            report['checks'].append('excel-readers')

            engine = tesseract_path()
            assert engine, 'missing packaged OCR'
            languages = run_limited([engine, '--list-langs'], timeout=15)
            assert languages.returncode == 0
            assert set(OCR_LANGUAGES).issubset(languages.stdout.decode().splitlines())
            report['checks'].append('ocr-five-languages-and-kernel-memory-limit')
            image = Image.new('RGB', (1100, 160), 'white')
            font_path = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts' / 'arial.ttf'
            font = ImageFont.truetype(str(font_path), 48)
            ImageDraw.Draw(image).text((30, 40), 'SPEDI POD TEST 12345', fill='black', font=font)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            assert '12345' in run_local_ocr(buffer.getvalue())
            report['checks'].append('offline-ocr-recognition')
            # Check lazily loaded application modules are present in the bundle.
            import spedi.shell
            import ddd_analyzer.ui.main_window
            import brudnopis_ot.ui.cycle_start_dialog
            import ewidentor
            import google_auth_oauthlib.flow
            report['checks'].append('application-modules')
            # Exercise this release's DDD fix in the frozen binary as well.
            from ddd_analyzer.adapter import parse_tacho_result
            from ddd_analyzer.models import DddFileKind
            from brudnopis_ot.core.ddd_file_loader import load_ddd_file
            from brudnopis_ot.ui.activity_end_dialog import ActivityEndDialog
            from ewidentor.core.brudnopis_import import load_month_segments
            card_path = root / 'C_synthetic.ddd'
            card_path.write_bytes(b'synthetic-self-test')
            raw = {'driver': {'card_number': '1111222233334444', 'surname': 'SYNTHETIC'},
                   'activities': [{'date': '2026-10-26', 'changes': [
                       {'time': '00:00', 'activity': 'REST'}, {'time': '09:59', 'activity': 'AVAILABLE'}]}]}
            card = parse_tacho_result(raw, str(card_path), DddFileKind.CARD)
            profile = store.upsert(load_ddd_file(card_path, _analysis=card))
            assert profile.open_activity.start == datetime(2026, 10, 26, 9, 59)
            end_dialog = ActivityEndDialog(profile.open_activity, zone='UTC', confirmed_end=datetime(2026, 10, 26, 14, 3))
            end_dialog.accept()
            store.confirm_activity_end(profile.profile_key, profile.open_activity, end_dialog.end_utc)
            assert max(s.end_minute for s in store.get(profile.profile_key).segments) == 599
            rows = load_month_segments(store.path, profile.profile_key, 2026, 10)
            assert sum(s.end_minute-s.start_minute for s in rows if s.activity == 'availability') == 244
            assert not any(b.kind == ActivityKind.AVAILABILITY for b in store.load_ddd_scenario(profile.profile_key, original=True).blocks)
            report['checks'].append('ddd-open-activity-and-confirmed-end')
            report['ok'] = True
    except Exception as error:
        # Exception text can contain paths; keep the report free of user data.
        report['ok'] = False
        report['error_type'] = type(error).__name__
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1
