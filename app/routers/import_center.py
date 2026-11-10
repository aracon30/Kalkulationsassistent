"""Import-Center: Basisverzeichnis, Scan, Import, Fortschritt, Fehlerprotokoll, CSV-Bericht."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings, update_settings
from app.database import get_db
from app.models import Document, ImportJob, ImportJobEntry
from app.services.folder_scanner import normalize_base_dir, validate_base_dir
from app.services.jobs import JobBusyError, manager
from app.web import redirect, templates

router = APIRouter()


def _job_dict(job: ImportJob | None) -> dict | None:
    if job is None:
        return None
    live = manager.progress.get(job.id, {}) if job.status == "laeuft" else {}
    return {
        "id": job.id, "type": job.job_type, "status": job.status, "base_dir": job.base_dir,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "progress_done": live.get("progress_done", job.progress_done), "progress_total": live.get("progress_total", job.progress_total),
        "current_item": live.get("current_item", job.current_item),
        "files_scanned": job.files_scanned, "files_imported": job.files_imported, "files_skipped": job.files_skipped,
        "files_failed": job.files_failed, "error_log": job.error_log, "running": job.status == "laeuft",
    }


@router.get("/import")
def import_center(request: Request, msg: str | None = None, err: str | None = None, db: Session = Depends(get_db)):
    jobs = list(db.scalars(select(ImportJob).order_by(ImportJob.id.desc()).limit(15)))
    counts = db.execute(select(Document.document_type, Document.import_status, func.count(Document.id))
                        .where(Document.is_current.is_(True)).group_by(Document.document_type, Document.import_status)).all()
    return templates.TemplateResponse(request, "import.html", {
        "nav": "import", "settings": get_settings(), "jobs": jobs, "counts": counts, "busy": manager.busy,
        "msg": msg, "err": err,
    })


@router.post("/import/scan")
def start_scan(base_dir: str = Form(...)):
    value = normalize_base_dir(base_dir)
    try:
        validate_base_dir(value)
        update_settings(base_dir=value)
        manager.start("scan", value)
    except (ValueError, JobBusyError) as exc:
        return redirect("/import", err=str(exc))
    return redirect("/import", msg="Scan gestartet")


@router.post("/import/start")
def start_import(mode: str = Form("neu")):
    try:
        manager.start("import", None, force=(mode == "alle"), retry_failed=(mode == "fehler"))
    except JobBusyError as exc:
        return redirect("/import", err=str(exc))
    return redirect("/import", msg="Import gestartet")


@router.post("/import/cancel")
def cancel_job():
    ok = manager.cancel()
    return redirect("/import", msg="Abbruch angefordert – laufendes Angebot wird zurückgerollt" if ok else "Kein laufender Job")


@router.get("/api/jobs/current")
def current_job(db: Session = Depends(get_db)):
    job = db.get(ImportJob, manager.current_job_id) if manager.current_job_id else db.scalars(select(ImportJob).order_by(ImportJob.id.desc())).first()
    return {"busy": manager.busy, "job": _job_dict(job)}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden")
    return _job_dict(job)


@router.get("/import/jobs/{job_id}")
def job_detail(job_id: int, request: Request, only_errors: bool = False, db: Session = Depends(get_db)):
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden")
    q = select(ImportJobEntry).where(ImportJobEntry.job_id == job_id)
    if only_errors:
        q = q.where(ImportJobEntry.status.in_(["fehler", "abgebrochen"]))
    entries = list(db.scalars(q.order_by(ImportJobEntry.id)))
    return templates.TemplateResponse(request, "job_detail.html", {"nav": "import", "job": job, "entries": entries, "only_errors": only_errors})


@router.get("/import/jobs/{job_id}/bericht.csv")
def job_report_csv(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden")
    buf = io.StringIO()
    buf.write("﻿")  # BOM für Excel
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Job", "Typ", "Zeitpunkt", "Angebotsnummer", "Datei", "Aktion", "Status", "Meldung"])
    for e in db.scalars(select(ImportJobEntry).where(ImportJobEntry.job_id == job_id).order_by(ImportJobEntry.id)):
        w.writerow([job.id, job.job_type, e.created_at, e.offer_number or "", e.file_path or "", e.action, e.status, e.message or ""])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="importbericht_{job_id}.csv"'})


@router.get("/dokumente")
def documents(request: Request, q: str | None = None, typ: str | None = None, status: str | None = None,
              alle_versionen: bool = False, page: int = 1, db: Session = Depends(get_db)):
    stmt = select(Document)
    if not alle_versionen:
        stmt = stmt.where(Document.is_current.is_(True))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Document.file_path.ilike(like), Document.detected_offer_number.ilike(like)))
    if typ:
        stmt = stmt.where(Document.document_type == typ)
    if status:
        stmt = stmt.where(Document.import_status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    docs = list(db.scalars(stmt.order_by(Document.detected_offer_number, Document.file_path).offset((page - 1) * 200).limit(200)))
    return templates.TemplateResponse(request, "documents.html", {
        "nav": "dokumente", "docs": docs, "q": q or "", "typ": typ or "", "status": status or "", "alle_versionen": alle_versionen,
        "total": total, "page": page, "pages": max(1, (total + 199) // 200),
    })
