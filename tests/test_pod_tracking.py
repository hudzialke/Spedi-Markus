from contextlib import closing
from datetime import datetime, timezone, timedelta
import sqlite3
from zipfile import ZipFile

import pytest

from markus_dispatcher.data import db
from markus_dispatcher.core.models import JobData
from markus_dispatcher.data.pod_tracking import PodStore


@pytest.fixture
def pod(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db.config, 'DB_PATH', tmp_path/'dispatcher.db')
    monkeypatch.setattr(db.config, 'ARCHIVE_ROOT', tmp_path/'archive')
    monkeypatch.setattr(db.config, 'ROZPISKA_DB_CANDIDATE', tmp_path/'none.db')
    db.init_db()
    job = db.save_job(JobData(ref_numbers='SYNTHETIC', archive_path='synthetic', unload_datetime='28.10.2026 12:00',
                             pod_requirements='Synthetic: CMR and WZ'))
    store = PodStore(db.config.DB_PATH, db.config.ARCHIVE_ROOT, now=lambda: datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    directory = db.config.ARCHIVE_ROOT/job.archive_path/'dokumenty'
    directory.mkdir(parents=True)
    from reportlab.pdfgen.canvas import Canvas
    ids = []
    for kind in ('CMR', 'WZ'):
        path = directory/(kind+'.pdf')
        canvas = Canvas(str(path)); canvas.drawString(30, 100, 'SYNTHETIC '+kind); canvas.save()
        with db.get_conn() as conn:
            cursor = conn.execute('INSERT INTO attachments(job_id,kind,original_name,stored_name) VALUES (?,?,?,?)',
                                  (job.id, 'pod', path.name, path.name))
            ids.append(cursor.lastrowid)
    return store, job, ids


def test_pod_requires_complete_selected_documents_and_keeps_deadline_explicit(pod):
    store, job, ids = pod
    due = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    store.save(job.id, deadline=due, required_docs='CMR, WZ', documents={ids[0]: ['CMR']})
    state = store.get(job.id)
    assert state['missing_docs'] == ['WZ'] and state['sent_at'] is None
    with pytest.raises(ValueError):
        store.mark_sent(job.id)
    store.save(job.id, deadline=due, required_docs='CMR, WZ', documents={ids[0]: ['CMR'], ids[1]: ['WZ']})
    store.mark_sent(job.id)
    assert store.get(job.id)['sent_at'] == '2026-10-29T12:00:00+00:00'
    store.save(job.id, deadline=due, required_docs='CMR, WZ', documents={ids[0]: ['CMR'], ids[1]: ['WZ']})
    assert store.get(job.id)['sent_at']
    store.save(job.id, deadline=due+timedelta(hours=1), required_docs='CMR, WZ', documents={ids[0]: ['CMR'], ids[1]: ['WZ']})
    assert store.get(job.id)['sent_at'] is None


def test_one_combined_document_can_satisfy_multiple_document_types(pod, tmp_path):
    store, job, ids = pod
    store.save(job.id, deadline=None, required_docs='CMR,WZ', documents={ids[0]: 'CMR,WZ'})
    archive = store.package(job.id, tmp_path/'pod.zip')
    with ZipFile(archive) as zipped:
        assert zipped.namelist() == ['01-CMR-WZ.pdf']
        assert zipped.read(zipped.namelist()[0]).startswith(b'%PDF-')
    assert store.get(job.id)['sent_at'] is None  # Preparing a package is not sending it.


def test_pdf_package_contains_only_selected_pages_and_closes_sources(pod, tmp_path):
    from pypdf import PdfReader
    store, job, ids = pod
    store.save(job.id, deadline=None, required_docs='CMR,WZ', documents={ids[0]: 'CMR', ids[1]: 'WZ'})
    path = store.package(job.id, tmp_path/'pod.pdf')
    with path.open('rb') as output:
        assert len(PdfReader(output).pages) == 2
    cmr = store.archive_root/job.archive_path/'dokumenty/CMR.pdf'
    cmr.rename(cmr.with_name('released.pdf'))


def test_unload_changes_require_reconfirmation_and_missing_files_are_not_counted(pod):
    store, job, ids = pod
    store.save(job.id, deadline=datetime(2026, 10, 30, tzinfo=timezone.utc), required_docs='CMR', documents={ids[0]: 'CMR'})
    job.unload_datetime = '29.10.2026 12:00'
    db.save_job(job)
    assert store.get(job.id)['needs_review']
    with pytest.raises(ValueError):
        store.mark_sent(job.id)
    (store.archive_root/job.archive_path/'dokumenty/CMR.pdf').unlink()
    assert store.get(job.id)['missing_docs'] == ['CMR']


def test_cross_job_documents_and_paths_outside_archive_are_rejected(pod, tmp_path):
    store, job, ids = pod
    other = db.save_job(JobData(ref_numbers='OTHER'))
    with pytest.raises(ValueError, match='innego'):
        store.save(other.id, deadline=None, required_docs='CMR', documents={ids[0]: 'CMR'})
    with db.get_conn() as conn:
        conn.execute('UPDATE attachments SET stored_name=? WHERE id=?', ('../../outside.pdf', ids[0]))
    store.save(job.id, deadline=None, required_docs='CMR', documents={ids[0]: 'CMR'})
    assert store.get(job.id)['missing_files']
    with pytest.raises(ValueError):
        store.package(job.id, tmp_path/'blocked.zip')
    assert not (tmp_path/'blocked.zip').exists()


def test_naive_deadlines_are_rejected_and_tracking_stays_local(pod):
    store, job, ids = pod
    with pytest.raises(ValueError, match='strefy'):
        store.save(job.id, deadline=datetime(2026, 10, 30), required_docs='CMR', documents={})
    store.save(job.id, deadline=None, required_docs='CMR', documents={ids[0]: 'CMR'})
    saved = db.get_job(job.id)
    assert saved.pod_requirements == job.pod_requirements
    assert not hasattr(saved, 'deadline_utc')


def test_local_deadline_rejects_missing_and_ambiguous_dst_hours():
    from markus_dispatcher.ui.pod_dialog import local_deadline
    with pytest.raises(ValueError, match='nie istnieje'):
        local_deadline(datetime(2026, 3, 29, 2, 30))
    with pytest.raises(ValueError, match='dwukrotnie'):
        local_deadline(datetime(2026, 10, 25, 2, 30))
    first = local_deadline(datetime(2026, 10, 25, 2, 30), 0)
    second = local_deadline(datetime(2026, 10, 25, 2, 30), 1)
    assert second-first == timedelta(hours=1)


def test_pod_dialog_saves_only_explicitly_selected_documents(pod, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from markus_dispatcher.ui.pod_dialog import PodDialog
    app = QApplication.instance() or QApplication([])
    store, job, ids = pod
    dialog = PodDialog(store, job.id)
    try:
        assert store.get(job.id) is None
        dialog.documents.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog.documents.cellWidget(0, 2).setText('CMR, WZ')
        dialog.required.setText('CMR, WZ')
        assert dialog._save()
        assert not store.get(job.id)['missing_docs']
        assert len(store.get(job.id)['documents']) == 1
        assert store.get(job.id)['sent_at'] is None
    finally:
        dialog.reject()
        dialog.deleteLater()
