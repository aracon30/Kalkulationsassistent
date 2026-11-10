"""Hintergrundjobs für Scan und Import (Thread, ein Job gleichzeitig).

Fortschritt und Protokoll werden über eigene, kurze Sessions geschrieben, damit
nie eine halbe Angebots-Transaktion festgeschrieben wird. Abbruch erfolgt
kooperativ zwischen Dateien/Angeboten; das laufende Angebot wird zurückgerollt.
"""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime

from sqlalchemy import select

from app.database import new_session, session_scope
from app.models import ImportJob, ImportJobEntry
from app.services.import_service import Reporter, run_import, scan_and_inventory

log = logging.getLogger(__name__)


class JobBusyError(Exception):
    pass


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.current_job_id: int | None = None
        self.progress: dict[int, dict] = {}
        self._flush = lambda: None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> bool:
        if self.busy:
            self._cancel.set()
            return True
        return False

    def recover_stale_jobs(self) -> None:
        """Nach Absturz/Neustart: als laufend markierte Jobs als abgebrochen kennzeichnen."""
        with session_scope() as s:
            for job in s.scalars(select(ImportJob).where(ImportJob.status == "laeuft")):
                job.status = "abgebrochen"
                job.finished_at = datetime.now()
                job.error_log = (job.error_log or "") + "\nAnwendung wurde während des Jobs beendet."

    def start(self, job_type: str, base_dir: str | None, *, force: bool = False, retry_failed: bool = False, wait: bool = False) -> int:
        with self._lock:
            if self.busy:
                raise JobBusyError("Es läuft bereits ein Scan oder Import.")
            self._cancel.clear()
            with session_scope() as s:
                job = ImportJob(job_type=job_type, base_dir=base_dir, status="laeuft", started_at=datetime.now())
                s.add(job)
                s.flush()
                job_id = job.id
            self.current_job_id = job_id
            self._thread = threading.Thread(target=self._run, args=(job_id, job_type, base_dir, force, retry_failed), daemon=True, name=f"heta-job-{job_id}")
            self._thread.start()
        if wait:
            self._thread.join()
        return job_id

    def _reporter(self, job_id: int) -> Reporter:
        """Fortschritt nur im Speicher; Protokolleinträge werden gepuffert und erst an
        Commit-Punkten des Imports geschrieben (SQLite erlaubt nur einen Schreiber)."""
        buffer: list[ImportJobEntry] = []

        def progress(done: int, total: int, current: str) -> None:
            self.progress[job_id] = {"progress_done": done, "progress_total": total, "current_item": current[-1000:]}

        def entry(offer_number, document_id, path, action, status, message) -> None:
            buffer.append(ImportJobEntry(job_id=job_id, offer_number=offer_number, document_id=document_id, file_path=path,
                                         action=action, status=status, message=message))

        def checkpoint() -> None:
            if not buffer:
                return
            items = buffer[:]
            buffer.clear()
            with session_scope() as s:
                s.add_all(items)

        self._flush = checkpoint
        return Reporter(progress=progress, entry=entry, cancelled=self._cancel.is_set, checkpoint=checkpoint)

    def _run(self, job_id: int, job_type: str, base_dir: str | None, force: bool, retry_failed: bool) -> None:
        reporter = self._reporter(job_id)
        session = new_session()
        status, errors = "abgeschlossen", []
        counts = {}
        try:
            if job_type == "scan":
                res = scan_and_inventory(session, base_dir or "", reporter)
                counts = {"files_scanned": res.files_found, "files_imported": 0, "files_skipped": res.unchanged + res.duplicates,
                          "files_failed": len(res.problems)}
                errors = res.problems
                status = "abgebrochen" if res.cancelled else "abgeschlossen"
            else:
                res = run_import(session, reporter, force=force, base_dir=base_dir, retry_failed=retry_failed)
                counts = {"files_imported": res.imported, "files_failed": res.failed, "files_skipped": res.skipped}
                errors = res.errors
                status = "abgebrochen" if res.cancelled else "abgeschlossen"
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            status = "fehler"
            errors.append(f"{type(exc).__name__}: {exc}")
            log.error("Job %s fehlgeschlagen: %s", job_id, traceback.format_exc())
        finally:
            session.close()
            try:
                self._flush()
            except Exception:  # noqa: BLE001
                log.error("Protokolleinträge konnten nicht gespeichert werden: %s", traceback.format_exc())
            prog = self.progress.pop(job_id, {})
            with session_scope() as s:
                job = s.get(ImportJob, job_id)
                if job:
                    for k, v in {**prog, **counts}.items():
                        setattr(job, k, v)
                    job.status = status
                    job.finished_at = datetime.now()
                    job.error_log = "\n".join(errors) or None
                    if status == "abgeschlossen":
                        job.progress_done = job.progress_total
            self.current_job_id = None


manager = JobManager()
