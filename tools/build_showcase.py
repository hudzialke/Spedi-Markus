"""Capture real widgets with generated fixtures in an isolated, offline sandbox.

The output is a local publication draft. Never reads the installed app's data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
from datetime import date, datetime, time, timedelta
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'work' / 'showcase-review'
RUN = OUT / 'private-fixtures' / datetime.now().strftime('%Y%m%d-%H%M%S')
ASSETS = OUT / 'assets'
for path in (OUT, RUN, ASSETS):
    path.mkdir(parents=True, exist_ok=True)
for variable, folder in {'SPEDI_DATA_DIR':'data', 'SPEDI_DISPATCHER_DATA_DIR':'dispatcher',
                         'LOCALAPPDATA':'local', 'APPDATA':'roaming', 'TEMP':'tmp', 'TMP':'tmp'}.items():
    target = RUN / folder
    target.mkdir(exist_ok=True)
    os.environ[variable] = str(target)
os.environ['SPEDI_SETTINGS_FILE'] = str(RUN / 'settings.json')
os.environ['SPEDI_LANGUAGE'] = 'pl'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

def deny_network(*args, **kwargs):
    raise RuntimeError('Showcase sandbox: network disabled')

socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
socket.getaddrinfo = deny_network

from PySide6.QtCore import QSettings, QThread, Qt, QRectF
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QScrollArea

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(RUN / 'qt-settings'))

def pump(seconds=.15, until=None):
    end = monotonic() + (30 if until else seconds)
    while monotonic() < end:
        app.processEvents()
        if until and until():
            return
        QThread.msleep(5)
    if until:
        raise RuntimeError('Timed out waiting for showcase widget')

class CaptureFrame(QWidget):
    def __init__(self, widget, title, size=(1560, 1040)):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.banner = QLabel()
        self.banner.setStyleSheet('background:#123b48;color:white;padding:12px 18px;font-size:15px;font-weight:600;')
        layout.addWidget(self.banner)
        widget.setWindowFlags(Qt.WindowType.Widget)
        layout.addWidget(widget, 1)
        self.resize(*size)
        self.title(title)
        self.show()
        pump()

    def title(self, text):
        self.banner.setText('DEMO · wszystkie dane fikcyjne · Spedi 0.2.1     |     ' + text)

    def save(self, name):
        pump()
        path = ASSETS / name
        assert self.grab().save(str(path))
        return path

def order_pdf():
    from reportlab.pdfgen.canvas import Canvas
    path = RUN / 'DEMO-order.pdf'
    c = Canvas(str(path), pagesize=(600, 800))
    c.setTitle('DEMO - synthetic transport order')
    c.setAuthor('Spedi demo generator')
    c.setFont('Helvetica-Bold', 22)
    c.drawString(40, 755, 'DEMO LOGISTICS')
    c.setFont('Helvetica', 11)
    c.drawString(40, 725, 'TRANSPORT ORDER: DEMO/1042/2026')
    c.drawString(40, 695, 'Client: Demo Logistics')
    for x, label, street, city, when in ((40, 'LOADING', 'Demo Street 12', '00-001 Warsaw, PL', '26.10.2026 08:00'),
                                        (320, 'UNLOADING', 'Example Street 45', '10115 Berlin, DE', '27.10.2026 10:00')):
        c.setStrokeColorRGB(.75, .8, .83)
        c.rect(x-10, 485, 250, 170)
        c.setFont('Helvetica-Bold', 12)
        c.drawString(x, 630, label)
        c.setFont('Helvetica', 11)
        for y, text in ((600, street), (580, city), (540, when)):
            c.drawString(x, y, text)
    c.drawString(40, 445, 'Cargo: 8 pallets, 2400 kg')
    c.drawString(40, 425, 'Freight: 1250.00 EUR')
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, 370, 'PROOF OF DELIVERY')
    c.setFont('Helvetica', 11)
    c.drawString(40, 340, 'Signed CMR and WZ in one PDF within 24h after unloading.')
    c.drawString(40, 320, 'Send to pod@example.invalid. Late submission: 25 EUR.')
    c.setFont('Helvetica', 9)
    c.drawString(40, 60, 'DEMO - generated fictional data. No real customer or driver information.')
    c.save()
    return path

def seed():
    from brudnopis_ot.core.ddd_store import DddProfileStore
    from brudnopis_ot.core.ddd_file_loader import DddLoadResult
    from brudnopis_ot.core.hudonizator_reader import RawSegment
    from brudnopis_ot.core.models import Scenario, Block, ActivityKind as K
    from spedi.weekly_watch import WatchStore, WatchOptions, history_revision
    from ewidentor.core.store import EwidentorStore
    from ewidentor.core.models import Driver, PaySettings, Absence, AbsenceKind
    from markus_dispatcher import config
    config.ROZPISKA_DB_CANDIDATE = RUN / 'absent-legacy.sqlite'
    from markus_dispatcher.data import db, settlement_store
    from markus_dispatcher.core.models import JobData
    store = DddProfileStore(RUN / 'data' / 'ddd_profiles.sqlite')
    watch = WatchStore(store.path)
    templates = []
    for index, name in enumerate(('Kierowca A · DEMO', 'Kierowca B · DEMO', 'Kierowca C · DEMO')):
        profile = store.create_template(name, date(2026, 10, 5))
        scenario = Scenario(date(2026, 10, 5), weeks=3, title=name)
        for offset in range(21 - index*3):
            day = date(2026, 10, 5) + timedelta(days=offset)
            base = datetime.combine(day, time.min)
            if day.weekday() >= (6 if index < 2 else 5):
                scenario.blocks.append(Block(K.REST, base, base+timedelta(days=1), country_start='PL'))
                continue
            for kind, start, end in ((K.REST,0,360),(K.WORK,360,390),(K.DRIVE,390,660),
                                     (K.REST,660,705),(K.DRIVE,705,945),(K.WORK,945,975),(K.REST,975,1440)):
                scenario.blocks.append(Block(kind,base+timedelta(minutes=start),base+timedelta(minutes=end),
                                             country_start='DE' if day.weekday() == 5 else 'PL'))
        # Leave some history unconfirmed: the actual warning logic stays visible.
        store.save_template_scenario(profile.profile_key, scenario)
        watch.save(profile.profile_key, WatchOptions(True, 'PL', True,
                   history_revision(scenario) if index < 2 else ''))
        templates.append(store.get(profile.profile_key))
    segments = []
    for n in range(31):
        day = date(2026,10,1)+timedelta(days=n)
        spans = [('rest',0,1440)] if day.weekday()>4 or day.day in (19,20) else [
            ('rest',0,360),('work',360,390),('drive',390,660),('rest',660,705),
            ('drive',705,900),('availability',900,945),('work',945,975),('rest',975,1440)]
        segments.extend(RawSegment(day,kind,a,b) for kind,a,b in spans)
    card = DddLoadResult(segments,'Syntetyczne aktywności · DEMO','Kierowca D · DEMO',
                         'DEMO-CARD-004','DEMO-004','card',('synthetic-demo.ddd',),date(2026,10,1),date(2026,10,31))
    store.upsert(card)
    ew = EwidentorStore()
    driver = ew.upsert_driver(Driver(None,card.driver_name,card.card_number,card.profile_key,
                              employer_name='Firma demonstracyjna · DEMO',pay=PaySettings(monthly_salary=6000)))
    for day in (19,20):
        ew.upsert_absence(Absence(None,driver.id,date(2026,10,day),AbsenceKind.PAID_LEAVE,title='Urlop · DEMO'))
    ew.set_month_fx_eur(2026,10,4.30)
    db.init_db()
    dd = db.add_driver('Kierowca A · DEMO')
    db.add_driver('Kierowca B · DEMO')
    job = db.save_job(JobData(driver_id=dd.id, ref_numbers='DEMO/1042/2026',local_only=True,
        client_name='Demo Logistics',contact_email='pod@example.invalid',
        load_address='Demo Street 12\n00-001 Warsaw, PL',load_datetime='26.10.2026 08:00',
        unload_address='Example Street 45\n10115 Berlin, DE',unload_datetime='27.10.2026 10:00',
        pallets='8 palet · 2400 kg',status='assigned',notes='Wymiana palet: nie. Awizacja: DEMO-1042.',
        pod_requirements='CMR + WZ z podpisem w jednym PDF.\nW ciągu 24 h od rozładunku: pod@example.invalid.\nOpłata za spóźnienie według fikcyjnego zlecenia: 25 EUR.',
        import_sources=json.dumps({'ref_numbers':{'document':'DEMO-order.pdf','section':'strona 1'}},ensure_ascii=False)))
    settlement_store.save_draft(job.id,dict(load_place='00-001 PL',unload_place='10115 DE',empty_km='40',
                                loaded_km='610',price='1250',costs='25',notes='Parking · kwota przykładowa'))
    for p in (store.path,config.DATA_DIR,config.DB_PATH,config.ARCHIVE_ROOT):
        assert Path(p).resolve().is_relative_to(RUN.resolve()), p
    return store,templates,card,job

def capture_import(document):
    from markus_dispatcher.ui.document_import_dialog import DocumentImportDialog, FIELDS
    dialog = DocumentImportDialog()
    frame = CaptureFrame(dialog,'Import dokumentu · 1/4: otwórz PDF',(1480,1000))
    dialog.open_path(document)
    pump(until=lambda:dialog._pdf_worker is None)
    dialog.zoom_combo.setCurrentIndex(0)
    frames = [frame.save('import-step-01.png')]
    for step,key,rect in ((2,'load_address',QRectF(30/600,180/800,250/600,55/800)),
                          (3,'unload_address',QRectF(310/600,180/800,250/600,55/800)),
                          (4,'pod_requirements',QRectF(30/600,445/800,540/600,50/800))):
        dialog._activate(next(i for i,(k,_) in enumerate(FIELDS) if k==key))
        dialog.field_scroll.ensureWidgetVisible(dialog.edits[key], 0, 15)
        dialog._region(rect)
        pump()
        dialog.canvas.area = QRectF(rect.x()*dialog.canvas.width(),rect.y()*dialog.canvas.height(),
                                   rect.width()*dialog.canvas.width(),rect.height()*dialog.canvas.height())
        dialog.canvas.update()
        assert dialog.selection.toPlainText().strip(), (key,dialog.status.text())
        label = dict(FIELDS)[key]
        frame.title(f'Import dokumentu · {step}/4: zaznacz obszar → {label}')
        frames.append(frame.save(f'import-step-{step*2-2:02}.png'))
        dialog.assign_selection()
        dialog._activate(next(i for i,(k,_) in enumerate(FIELDS) if k==key))
        dialog.field_scroll.ensureWidgetVisible(dialog.edits[key], 0, 15)
        frame.title(f'Import dokumentu · {step}/4: przypisano → {label}')
        frames.append(frame.save(f'import-step-{step*2-1:02}.png'))
    # Remaining demo values are entered just as editable form values.
    for key,value in {'ref_numbers':'DEMO/1042/2026','client_name':'Demo Logistics','load_datetime':'26.10.2026 08:00',
                      'unload_datetime':'27.10.2026 10:00','pallets':'8 palet · 2400 kg','price':'1250 EUR'}.items():
        dialog.edits[key].setPlainText(value)
    frame.title('Import dokumentu · wymagania POD obok dokumentu źródłowego')
    dialog.field_scroll.verticalScrollBar().setValue(dialog.field_scroll.verticalScrollBar().maximum())
    frames.append(frame.save('07-import.png'))
    from PIL import Image
    images = [Image.open(p).convert('RGB') for p in frames]
    # Only encode genuine captures; do not draw or replace any UI pixels.
    images[0].save(ASSETS / 'import-demo.gif',save_all=True,append_images=images[1:],
                   duration=[1800,2200,1600,2200,1600,2600,1800,3800],loop=0,optimize=False)
    extracted = {key:dialog.edits[key].toPlainText() for key in ('load_address','unload_address','pod_requirements')}
    assert 'Demo Street 12' in extracted['load_address'] and 'Example Street 45' not in extracted['load_address']
    assert 'Example Street 45' in extracted['unload_address'] and 'Demo Street 12' not in extracted['unload_address']
    assert 'pod@example.invalid' in extracted['pod_requirements'] and '24h' in extracted['pod_requirements']
    dialog.reject()
    frame.hide()
    return extracted

def main():
    global app
    app = QApplication([])
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    for name in ('seguisym.ttf','seguiemj.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
    app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0],10))
    from spedi.ui import apply_theme
    apply_theme(app)
    store,templates,card,job = seed()
    from spedi.shell import ShellWindow
    shell = ShellWindow()
    shell._mobile_timer.stop()
    shell._brudnopis._apply_ddd_profile(templates[0],ask_cycle=False)
    frame = CaptureFrame(shell,'Brudnopis OT · planowanie i kontrola odpoczynków',(1760,1040))
    shell._brudnopis.grid.zoom_in()
    shell._brudnopis.grid.zoom_in()
    shell._brudnopis.findings.parentWidget().setMinimumWidth(420)
    frame.save('01-brudnopis.png')
    from spedi.weekly_watch_ui import WeeklyWatchDialog
    watch = WeeklyWatchDialog(store)
    wf = CaptureFrame(watch,'Lista OT · terminy, rekompensaty i ostrzeżenia',(1760,760))
    pump(.4)
    pump(until=lambda:watch._worker is None and not watch._pending)
    pump(until=lambda:len(watch.results) == 3)
    watch.table.setColumnWidth(4,145)
    watch.table.setColumnWidth(5,210)
    watch.table.setCurrentCell(watch._row(templates[2].profile_key),1)
    wf.save('02-lista-ot.png')
    watch.reject()
    pump(until=lambda:watch._worker is None)
    wf.hide()
    shell._brudnopis._apply_ddd_profile(card,ask_cycle=False)
    shell._brudnopis._tabs.setCurrentIndex(1)
    shell._brudnopis._analyzer.summary.table.selectRow(25)
    frame.title('Analiza DDD · wygenerowane aktywności, bez rzeczywistego pliku DDD')
    frame.save('03-ddd.png')
    shell._show_page(1)
    ew = shell.stack.widget(1)
    ew._autoload_timer.stop()
    ew.year_spin.setValue(2026)
    ew.month_spin.setValue(10)
    ew.load_month(silent=True)
    ew._autoload_timer.stop()
    assert ew._card is not None
    ew._set_view(1)
    ew._select_day(date(2026,10,19))
    frame.title('Ewidentor · miesiąc, aktywności i urlopy · stawki demonstracyjne')
    frame.save('04-ewidentor.png')
    ew._set_view(0)
    frame.title('Ewidentor · szczegółowa lista dni i wyliczenia miesiąca')
    frame.save('04b-ewidentor-lista.png')
    shell._show_page(2)
    dispatcher = shell.stack.widget(2)
    dispatcher.fleet._select(job.driver_id)
    dispatcher.job.load_job(job)
    frame.title('Dispatcher · zlecenie, załadunek i rozładunek')
    frame.save('05-dispatcher.png')
    for scroll in dispatcher.job.findChildren(QScrollArea):
        if scroll.widget() and scroll.widget().isAncestorOf(dispatcher.job.pod_requirements_edit):
            scroll.ensureWidgetVisible(dispatcher.job.pod_requirements_edit,10,10)
            pump()
            scroll.verticalScrollBar().setValue(min(scroll.verticalScrollBar().maximum(),scroll.verticalScrollBar().value()+320))
    frame.title('Dispatcher · warunki przekazania POD zapisane przy zleceniu')
    frame.save('06-pod.png')
    # Capture the application's real settlement panel as a separate detail view.
    settlement_frame = CaptureFrame(dispatcher.job.settlement,'Rozliczenie frachtu · dane prywatne, zapis lokalny',(1340,700))
    settlement_frame.save('08-rozliczenie.png')
    settlement_frame.hide()
    frame.hide()
    extracted = capture_import(order_pdf())
    manifest = {'app_version':'0.2.1','status':'local review draft; not published',
        'data_origin':'Generated fictional fixtures only; no real databases, documents, names or screenshots.',
        'network':'Socket connections and DNS blocked in capture process; integrations unconfigured.',
        'captures':'Actual PySide6 application widgets; demo banner is a separate Qt widget.',
        'import_checks':extracted,
        'publish_files':sorted(str(p.relative_to(OUT)).replace('\\','/') for p in ASSETS.iterdir()
                               if p.is_file() and not p.name.startswith('import-step-'))}
    (OUT/'capture-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'result':'ok','assets':len(manifest['publish_files']),'output':str(OUT)},ensure_ascii=False))
    # No normal app startup/migration, no real user databases, and no outbound calls.

if __name__ == '__main__':
    main()
