"""Local POD deadlines and explicitly selected documents; no outbound actions."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED


def document_types(values):
    if isinstance(values, str):
        values = values.split(',')
    result = sorted({str(value).strip().upper() for value in values if str(value).strip()})
    if len(result) > 20 or any(len(value) > 64 or '\n' in value for value in result):
        raise ValueError('Nieprawidłowa lista wymaganych dokumentów.')
    return result


def utc(value):
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Termin POD wymaga strefy czasowej.')
    return value.astimezone(timezone.utc).isoformat()


class PodStore:
    def __init__(self, path, archive_root, *, ensure_schema=True, now=lambda: datetime.now(timezone.utc)):
        self.path, self.archive_root, self.now = Path(path), Path(archive_root).resolve(), now
        if not self.path.is_file():
            raise ValueError('Brak bazy zleceń.')
        if ensure_schema:
            with self._connect() as conn:
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS pod_followup (
                        job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                        deadline_utc TEXT, required_docs TEXT NOT NULL,
                        unload_snapshot TEXT NOT NULL, sent_at TEXT, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS pod_document_labels (
                        attachment_id INTEGER PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
                        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                        doc_types TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_pod_documents_job ON pod_document_labels(job_id);
                ''')

    @contextmanager
    def _connect(self, *, readonly=False):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)
                     if readonly else sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            with conn:
                yield conn

    def _source(self, job, document):
        folder = (self.archive_root/job['archive_path']/'dokumenty').resolve()
        source = (folder/document['stored_name']).resolve()
        if (not job['archive_path'] or not folder.is_relative_to(self.archive_root)
                or not source.is_relative_to(folder) or not source.is_file()):
            raise ValueError('Brakuje dokumentu w archiwum zlecenia.')
        return source

    def save(self, job_id, *, deadline, required_docs, documents):
        due = utc(deadline)
        required = document_types(required_docs)
        if not required:
            raise ValueError('Wpisz przynajmniej jeden wymagany dokument.')
        selected = {int(key): document_types(value) for key, value in documents.items()}
        if len(selected) > 50 or any(not types for types in selected.values()):
            raise ValueError('Określ rodzaj każdego wybranego dokumentu POD.')
        with self._connect() as conn:
            job = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job is None:
                raise ValueError('Zlecenie nie istnieje.')
            known = {row['id'] for row in conn.execute('SELECT id FROM attachments WHERE job_id=?', (job_id,))}
            if not set(selected) <= known:
                raise ValueError('Dokument pochodzi z innego zlecenia.')
            old = conn.execute('SELECT * FROM pod_followup WHERE job_id=?', (job_id,)).fetchone()
            old_docs = {row['attachment_id']: json.loads(row['doc_types']) for row in conn.execute(
                'SELECT attachment_id,doc_types FROM pod_document_labels WHERE job_id=?', (job_id,))}
            unchanged = (old and old['deadline_utc'] == due and json.loads(old['required_docs']) == required
                         and old['unload_snapshot'] == job['unload_datetime'] and old_docs == selected)
            sent = old['sent_at'] if unchanged else None
            conn.execute('INSERT INTO pod_followup VALUES (?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET '
                'deadline_utc=excluded.deadline_utc,required_docs=excluded.required_docs,unload_snapshot=excluded.unload_snapshot,'
                'sent_at=excluded.sent_at,updated_at=excluded.updated_at',
                (job_id, due, json.dumps(required), job['unload_datetime'], sent, utc(self.now())))
            conn.execute('DELETE FROM pod_document_labels WHERE job_id=?', (job_id,))
            conn.executemany('INSERT INTO pod_document_labels VALUES (?,?,?)',
                             [(key, job_id, json.dumps(value)) for key, value in selected.items()])

    def get(self, job_id):
        with self._connect(readonly=True) as conn:
            job = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            record = conn.execute('SELECT * FROM pod_followup WHERE job_id=?', (job_id,)).fetchone()
            if job is None or record is None:
                return None
            documents = [dict(row) for row in conn.execute('SELECT a.*,p.doc_types FROM pod_document_labels p '
                'JOIN attachments a ON a.id=p.attachment_id AND a.job_id=p.job_id WHERE p.job_id=? ORDER BY a.id', (job_id,))]
        covered = set()
        missing_files = []
        for item in documents:
            item['doc_types'] = json.loads(item['doc_types'])
            try:
                self._source(job, item)
                covered.update(item['doc_types'])
            except ValueError:
                missing_files.append(item['original_name'])
        required = json.loads(record['required_docs'])
        return {**dict(record), 'required_docs': required, 'documents': documents, 'job': dict(job),
                'missing_docs': sorted(set(required)-covered), 'missing_files': missing_files,
                'needs_review': bool(record['deadline_utc'] and record['unload_snapshot'] != job['unload_datetime'])}

    def mark_sent(self, job_id):
        state = self.get(job_id)
        if not state or state['missing_docs'] or state['missing_files'] or state['needs_review']:
            raise ValueError('Sprawdź termin POD i uzupełnij wymagane dokumenty przed oznaczeniem wysyłki.')
        with self._connect() as conn:
            current = conn.execute('SELECT p.updated_at,j.unload_datetime FROM pod_followup p JOIN jobs j ON j.id=p.job_id '
                                   'WHERE p.job_id=?', (job_id,)).fetchone()
            if current is None or current['updated_at'] != state['updated_at'] or current['unload_datetime'] != state['job']['unload_datetime']:
                raise ValueError('Zlecenie zmieniło się. Odśwież dane przed oznaczeniem wysyłki.')
            sent_at = utc(self.now())
            conn.execute('UPDATE pod_followup SET sent_at=?,updated_at=? WHERE job_id=?',
                         (sent_at, sent_at, job_id))

    def package(self, job_id, output):
        state = self.get(job_id)
        if not state or state['missing_docs'] or state['missing_files'] or state['needs_review']:
            raise ValueError('Uzupełnij dokumenty i potwierdź termin POD.')
        destination = Path(output).resolve()
        sources = [(item, self._source(state['job'], item)) for item in state['documents']]
        if destination in {path for item, path in sources}:
            raise ValueError('Paczka nie może zastąpić dokumentu źródłowego.')
        if destination.exists() and destination.is_relative_to(self.archive_root):
            raise ValueError('Paczka nie może zastąpić istniejącego pliku w archiwum.')
        temporary = destination.with_name(destination.name+'.partial-'+uuid4().hex)
        try:
            if destination.suffix.lower() == '.pdf':
                if any(path.suffix.lower() != '.pdf' for item, path in sources):
                    raise ValueError('Do wspólnego PDF wybierz pliki PDF. Pozostałe dokumenty można spakować do ZIP.')
                from pypdf import PdfWriter
                with PdfWriter() as writer:
                    for item, path in sources:
                        writer.append(path)
                    with temporary.open('wb') as output_file:
                        writer.write(output_file)
            elif destination.suffix.lower() == '.zip':
                with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
                    for index, (item, path) in enumerate(sources, 1):
                        name = re.sub(r'[^A-Za-z0-9._-]', '_', '-'.join(item['doc_types']))
                        archive.write(path, f'{index:02d}-{name}{path.suffix.lower()}')
            else:
                raise ValueError('Wybierz plik PDF albo ZIP.')
            temporary.replace(destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)


def application_pod_store():
    from markus_dispatcher import config
    return PodStore(config.DB_PATH, config.ARCHIVE_ROOT)
