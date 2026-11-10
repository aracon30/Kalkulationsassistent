"""Angebotsübersicht, Detailansicht, Dokumentvorschau und optionale KI-Auswertung."""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.ai import AIError, DocumentPayload, get_provider
from app.database import get_db
from app.models import (
    AIRequestLog, Calculation, COST_FIELDS, Document, DocType, ExtractionValue, ManufacturingOperation, Offer, OfferItem,
    ReviewIssue, ValStatus,
)
from app.services.pdf_importer import extract_pdf, render_page_png
from app.web import redirect, templates

router = APIRouter()

ITEM_DISPLAY = ["product_type", "description", "drawing_number", "dn", "design_pressure", "design_temperature_min",
                "design_temperature_max", "material", "diameter", "wall_thickness", "height", "volume", "filter_elements",
                "filter_area", "design_code", "weight"]
CALC_DISPLAY = [f for f in COST_FIELDS] + ["calculation_date"]
STATUS_CHOICES = ["offen", "gewonnen", "verloren", "storniert"]


@router.get("/angebote")
def offer_list(request: Request, q: str | None = None, kunde: str | None = None, jahr: int | str | None = None,
               produkttyp: str | None = None, werkstoff: str | None = None, dn: str | None = None,
               druck_min: str | None = None, druck_max: str | None = None, status: str | None = None,
               db: Session = Depends(get_db)):
    stmt = select(Offer).options(selectinload(Offer.items).selectinload(OfferItem.calculations))
    if q:
        stmt = stmt.where(Offer.offer_number.ilike(f"%{q.strip()}%"))
    if kunde:
        stmt = stmt.where(Offer.customer.ilike(f"%{kunde.strip()}%"))
    if jahr not in (None, ""):
        try:
            stmt = stmt.where(Offer.year == int(jahr))
        except ValueError:
            pass
    if status:
        stmt = stmt.where(Offer.status.ilike(f"%{status}%"))
    item_filters = []
    if produkttyp:
        item_filters.append(OfferItem.product_type == produkttyp)
    if werkstoff:
        item_filters.append(or_(OfferItem.material.ilike(f"%{werkstoff}%"), OfferItem.material_raw.ilike(f"%{werkstoff}%")))
    for raw, op in ((dn, "dn"), (druck_min, "pmin"), (druck_max, "pmax")):
        if raw not in (None, ""):
            try:
                v = float(str(raw).replace(",", "."))
            except ValueError:
                continue
            item_filters.append(OfferItem.dn == v if op == "dn" else (OfferItem.design_pressure >= v if op == "pmin" else OfferItem.design_pressure <= v))
    if item_filters:
        stmt = stmt.where(Offer.id.in_(select(OfferItem.offer_id).where(*item_filters)))
    offers = list(db.scalars(stmt.order_by(Offer.year.desc(), Offer.offer_number.desc())).unique())
    years = [y for (y,) in db.execute(select(Offer.year).distinct().order_by(Offer.year.desc())) if y]
    types = [t for (t,) in db.execute(select(OfferItem.product_type).distinct().order_by(OfferItem.product_type)) if t]
    statuses = [s for (s,) in db.execute(select(Offer.status).distinct()) if s]
    f = {"q": q or "", "kunde": kunde or "", "jahr": str(jahr or ""), "produkttyp": produkttyp or "", "werkstoff": werkstoff or "",
         "dn": dn or "", "druck_min": druck_min or "", "druck_max": druck_max or "", "status": status or ""}
    return templates.TemplateResponse(request, "offers.html", {"nav": "angebote", "offers": offers, "years": years, "types": types,
                                                               "statuses": statuses, "f": f})


def _ev_dict(ev: ExtractionValue, doc: Document | None) -> dict:
    return {
        "id": ev.id, "field": ev.field_name, "value": ev.field_value, "unit": ev.unit, "document_id": ev.document_id,
        "file": doc.file_name if doc else None, "path": doc.file_path if doc else None, "doc_type": doc.document_type if doc else None,
        "sheet": ev.source_sheet, "cell": ev.source_cell, "label": ev.source_label, "formula": ev.source_formula,
        "cached": ev.cached_value, "page": ev.source_page, "text": ev.source_text, "method": ev.extraction_method,
        "confidence": ev.confidence, "status": ev.validation_status, "message": ev.validation_message,
    }


@router.get("/angebote/{offer_id}")
def offer_detail(offer_id: int, request: Request, doc: int | None = None, db: Session = Depends(get_db)):
    offer = db.get(Offer, offer_id)
    if not offer:
        raise HTTPException(404, "Angebot nicht gefunden")
    docs = list(db.scalars(select(Document).where(Document.offer_id == offer.id).order_by(Document.document_type, Document.file_name)))
    doc_by_id = {d.id: d for d in docs}
    current_doc_ids = {d.id for d in docs if d.is_current}
    evs = list(db.scalars(select(ExtractionValue).where(ExtractionValue.document_id.in_(doc_by_id.keys()))))
    sources: dict[str, list[dict]] = defaultdict(list)
    for ev in evs:
        if ev.document_id not in current_doc_ids:
            continue
        if ev.target_entity == "offer":
            key = f"offer:{offer.id}:{ev.field_name}"
        elif ev.target_entity == "calculation" and ev.calculation_id:
            key = f"calc:{ev.calculation_id}:{ev.field_name}"
        elif ev.target_entity == "operation" and ev.offer_item_id:
            key = f"op:{ev.offer_item_id}:{ev.document_id}:{ev.field_name}"
        elif ev.offer_item_id:
            key = f"item:{ev.offer_item_id}:{ev.field_name}"
        else:
            key = f"doc:{ev.document_id}:{ev.field_name}"
        sources[key].append(_ev_dict(ev, doc_by_id.get(ev.document_id)))
    items = []
    for item in offer.items:
        calcs = sorted(item.calculations, key=lambda c: (c.is_current, c.revision_index or 0, c.id), reverse=True)
        current = next((c for c in calcs if c.is_current), calcs[0] if calcs else None)
        ops = [op for op in db.scalars(select(ManufacturingOperation).where(ManufacturingOperation.offer_item_id == item.id)
                                       .order_by(ManufacturingOperation.id)) if op.document_id in current_doc_ids]
        issues = list(db.scalars(select(ReviewIssue).where(ReviewIssue.offer_item_id == item.id, ReviewIssue.status == "offen")))
        items.append({"item": item, "calc": current, "calcs": calcs, "ops": ops, "issues": issues,
                      "drawings": [d for d in docs if d.offer_item_id == item.id and d.extension == ".pdf"]})
    offer_issues = list(db.scalars(select(ReviewIssue).where(ReviewIssue.offer_id == offer.id, ReviewIssue.offer_item_id.is_(None), ReviewIssue.status == "offen")))
    pdfs = [d for d in docs if d.extension == ".pdf" and d.is_current]
    pdfs.sort(key=lambda d: (d.document_type != DocType.ZEICHNUNG, d.offer_item.position_number if d.offer_item else 999, d.file_name))
    selected = doc_by_id.get(doc) if doc else (pdfs[0] if pdfs else None)
    return templates.TemplateResponse(request, "offer_detail.html", {
        "nav": "angebote", "offer": offer, "docs": docs, "pdfs": pdfs, "selected": selected, "items": items, "sources": dict(sources),
        "offer_issues": offer_issues, "item_fields": ITEM_DISPLAY, "calc_fields": CALC_DISPLAY, "ai": get_provider(),
        "status_choices": STATUS_CHOICES,
    })


@router.post("/angebote/{offer_id}/status")
def set_status(offer_id: int, status: str = Form(...), user_name: str = Form(""), db: Session = Depends(get_db)):
    from app.models import Correction

    offer = db.get(Offer, offer_id)
    if not offer:
        raise HTTPException(404, "Angebot nicht gefunden")
    if status not in STATUS_CHOICES:
        raise HTTPException(400, "Ungültiger Status")
    db.add(Correction(entity="offer", entity_id=offer.id, field_name="status", old_value=offer.status, new_value=status,
                      action="korrektur", reason="Angebotsstatus manuell gesetzt", user_name=user_name or None))
    offer.status = status
    db.commit()
    return redirect(f"/angebote/{offer_id}")


def _document_or_404(db: Session, document_id: int) -> Document:
    """Dateien werden ausschließlich über ihre Datenbank-ID ausgeliefert (kein Pfad aus der Anfrage)."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")
    path = Path(doc.file_path)
    if not path.is_file():
        raise HTTPException(404, "Datei ist nicht mehr am ursprünglichen Ort vorhanden")
    return doc


@router.get("/dokumente/{document_id}/datei")
def document_file(document_id: int, db: Session = Depends(get_db)):
    doc = _document_or_404(db, document_id)
    media = {".pdf": "application/pdf", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12"}.get(doc.extension or "", "application/octet-stream")
    inline = doc.extension == ".pdf"
    return FileResponse(doc.file_path, media_type=media, filename=doc.file_name,
                        content_disposition_type="inline" if inline else "attachment")


@router.get("/dokumente/{document_id}/seite/{page}.png")
def document_page(document_id: int, page: int, zoom: float = 1.5, db: Session = Depends(get_db)):
    doc = _document_or_404(db, document_id)
    if doc.extension != ".pdf":
        raise HTTPException(400, "Nur PDF-Dokumente haben eine Seitenvorschau")
    try:
        png = render_page_png(doc.file_path, page, zoom)
    except IndexError:
        raise HTTPException(404, "Seite existiert nicht") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"PDF kann nicht dargestellt werden: {type(exc).__name__}") from None
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/api/dokumente/{document_id}")
def document_info(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")
    return {"id": doc.id, "file_name": doc.file_name, "page_count": doc.page_count, "type": doc.document_type,
            "has_text": doc.has_text, "exists": os.path.isfile(doc.file_path)}


# --- Optionale KI-Auswertung (mit ausdrücklicher Bestätigung) ---

AI_FIELDS = ["drawing_number", "dn", "design_pressure", "design_temperature_min", "design_temperature_max", "material",
             "design_code", "filter_area", "filter_elements"]


@router.get("/dokumente/{document_id}/ki")
def ai_preview(document_id: int, request: Request, db: Session = Depends(get_db)):
    doc = _document_or_404(db, document_id)
    provider = get_provider()
    text = ""
    if doc.extension == ".pdf":
        text = "\n".join(extract_pdf(doc.file_path).text_by_page)
    transfer = provider.describe_transfer(DocumentPayload(doc.id, doc.file_name, text))
    return templates.TemplateResponse(request, "ai_confirm.html", {"nav": "angebote", "doc": doc, "provider": provider,
                                                                   "transfer": transfer, "fields": AI_FIELDS})


@router.post("/dokumente/{document_id}/ki")
def ai_run(document_id: int, confirm: str = Form(""), db: Session = Depends(get_db)):
    doc = _document_or_404(db, document_id)
    provider = get_provider()
    if confirm != "ja":
        return redirect(f"/dokumente/{document_id}/ki", err="Übertragung nicht bestätigt")
    if not provider.available:
        return redirect(f"/dokumente/{document_id}/ki", err="Kein KI-Provider aktiv")
    text = "\n".join(extract_pdf(doc.file_path).text_by_page) if doc.extension == ".pdf" else ""
    log = AIRequestLog(provider=provider.name, purpose="extract_document", document_id=doc.id, chars_sent=len(text), status="gestartet")
    db.add(log)
    db.flush()
    try:
        result = asyncio.run(provider.extract_document(DocumentPayload(doc.id, doc.file_name, text), AI_FIELDS))
    except AIError as exc:
        log.status, log.error = "fehler", str(exc)
        db.commit()
        return redirect(f"/dokumente/{document_id}/ki", err=str(exc))
    n = 0
    for f in result.fields:
        if f.value is None or f.field_name not in AI_FIELDS:
            continue
        # Halluzinationsschutz: Beleg muss wörtlich im lokal extrahierten Text vorkommen
        evidence_ok = bool(f.evidence) and f.evidence in text
        db.add(ExtractionValue(
            document_id=doc.id, offer_item_id=doc.offer_item_id, target_entity="offer_item", field_name=f.field_name,
            field_value=str(f.value), unit=f.unit, source_page=f.page, source_text=f.evidence, extraction_method="ki",
            confidence=min(f.confidence, 0.5 if evidence_ok else 0.1), validation_status=ValStatus.PRUEFBEDARF,
            validation_message=f"KI-Vorschlag ({provider.name}) – nicht automatisch übernommen" + ("" if evidence_ok else "; Beleg nicht im Dokumenttext gefunden"),
        ))
        n += 1
    log.status = "ok"
    db.commit()
    return redirect(f"/angebote/{doc.offer_id}" if doc.offer_id else "/dokumente", msg=f"{n} KI-Vorschläge gespeichert (Prüfbedarf)")
