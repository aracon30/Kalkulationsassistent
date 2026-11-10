"""Prüfcenter: offene Prüfungen, manuelle Korrektur, Freigabe, Prüfprotokoll."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import COST_FIELDS, Calculation, Correction, ExtractionValue, Offer, OfferItem, ReviewIssue, ValStatus
from app.services.import_service import ITEM_FIELDS, OFFER_FIELDS, _update_validation_status
from app.services.normalize import normalize_material, normalize_product_type, parse_date
from app.services.excel_reader import parse_number
from app.web import redirect, templates

router = APIRouter()

INT_FIELDS = {"filter_elements", "position_number"}
TEXT_FIELDS = {"product_type", "description", "drawing_number", "material", "design_code", "customer", "project_name",
               "country", "industry", "status", "currency", "revision"}
DATE_FIELDS = {"offer_date", "calculation_date"}


def convert_value(field: str, raw: str):
    raw = (raw or "").strip()
    if raw == "":
        return None
    if field in DATE_FIELDS:
        d = parse_date(raw)
        if d is None:
            raise ValueError("Datum im Format TT.MM.JJJJ angeben")
        return d
    if field in TEXT_FIELDS:
        if field == "material":
            return normalize_material(raw)[0]
        if field == "product_type":
            return normalize_product_type(raw)[0]
        return raw
    num = parse_number(raw)
    if num is None:
        raise ValueError(f"'{raw}' ist keine Zahl")
    if field in INT_FIELDS:
        if num != int(num):
            raise ValueError("Ganze Zahl erwartet")
        return int(num)
    return num


def _target(db: Session, issue: ReviewIssue, ev: ExtractionValue | None, field: str):
    """Ermittelt das zu ändernde Objekt (Angebot, Position oder aktuelle Kalkulation)."""
    if field in COST_FIELDS or field == "calculation_date":
        if ev and ev.calculation_id:
            return db.get(Calculation, ev.calculation_id), "calculation"
        item = db.get(OfferItem, issue.offer_item_id) if issue.offer_item_id else None
        calc = item.current_calculation if item else None
        return calc, "calculation"
    if field in ITEM_FIELDS:
        item_id = (ev.offer_item_id if ev else None) or issue.offer_item_id
        return (db.get(OfferItem, item_id) if item_id else None), "offer_item"
    if field in OFFER_FIELDS:
        return (db.get(Offer, issue.offer_id) if issue.offer_id else None), "offer"
    return None, None


@router.get("/pruefung")
def review_list(request: Request, status: str = "offen", code: str | None = None, angebot: str | None = None,
                msg: str | None = None, err: str | None = None, db: Session = Depends(get_db)):
    stmt = select(ReviewIssue).where(ReviewIssue.status == status)
    if code:
        stmt = stmt.where(ReviewIssue.code == code)
    if angebot:
        stmt = stmt.join(Offer, ReviewIssue.offer_id == Offer.id).where(Offer.offer_number.ilike(f"%{angebot}%"))
    issues = list(db.scalars(stmt.order_by(ReviewIssue.severity.desc(), ReviewIssue.id.desc()).limit(500)))
    codes = db.execute(select(ReviewIssue.code, func.count(ReviewIssue.id)).where(ReviewIssue.status == status).group_by(ReviewIssue.code)).all()
    editable = sorted(set(ITEM_FIELDS + COST_FIELDS + OFFER_FIELDS + ["calculation_date"]) - {"position_number"})
    return templates.TemplateResponse(request, "review.html", {"nav": "pruefung", "issues": issues, "codes": codes, "status": status,
                                                               "code": code or "", "angebot": angebot or "", "msg": msg, "err": err,
                                                               "editable": editable})


@router.post("/pruefung/{issue_id}")
def review_action(issue_id: int, action: str = Form(...), field_name: str = Form(""), new_value: str = Form(""),
                  reason: str = Form(""), user_name: str = Form(""), db: Session = Depends(get_db)):
    issue = db.get(ReviewIssue, issue_id)
    if not issue:
        raise HTTPException(404, "Prüfpunkt nicht gefunden")
    if not user_name.strip():
        return redirect("/pruefung", err="Bitte Namen für das Prüfprotokoll angeben")
    ev = db.get(ExtractionValue, issue.extraction_value_id) if issue.extraction_value_id else None
    now = datetime.now().replace(microsecond=0)

    if action == "freigeben":
        if ev:
            db.add(Correction(entity="extraction_value", entity_id=ev.id, field_name=ev.field_name, old_value=ev.validation_status,
                              new_value=ValStatus.VERIFIZIERT, action="freigabe", reason=reason or "Wert geprüft und freigegeben",
                              user_name=user_name, extraction_value_id=ev.id, review_issue_id=issue.id))
            ev.validation_status = ValStatus.VERIFIZIERT
            ev.validation_message = f"{ev.validation_message or ''}; manuell freigegeben von {user_name} am {now:%d.%m.%Y}".strip("; ")
        else:
            db.add(Correction(entity="review_issue", entity_id=issue.id, action="freigabe", reason=reason or "Hinweis geprüft",
                              user_name=user_name, review_issue_id=issue.id))
    elif action == "korrigieren":
        field = field_name or (ev.field_name if ev else "")
        if not field:
            return redirect("/pruefung", err="Feld für die Korrektur auswählen")
        try:
            value = convert_value(field, new_value)
        except ValueError as exc:
            return redirect("/pruefung", err=str(exc))
        target, entity = _target(db, issue, ev, field)
        if target is None or not hasattr(target, field):
            return redirect("/pruefung", err=f"Kein Ziel für Feld '{field}' gefunden")
        old = getattr(target, field)
        db.add(Correction(entity=entity, entity_id=target.id, field_name=field, old_value=None if old is None else str(old),
                          new_value=None if value is None else str(value), action="korrektur", reason=reason or None,
                          user_name=user_name, extraction_value_id=ev.id if ev else None, review_issue_id=issue.id))
        setattr(target, field, value)
        if field == "material" and entity == "offer_item":
            target.material_raw = new_value.strip() or None
        if issue.document_id:
            db.add(ExtractionValue(
                document_id=issue.document_id, offer_item_id=getattr(target, "offer_item_id", None) if entity == "calculation" else (target.id if entity == "offer_item" else None),
                calculation_id=target.id if entity == "calculation" else None, target_entity=entity, field_name=field,
                field_value=None if value is None else str(value), numeric_value=float(value) if isinstance(value, (int, float)) else None,
                extraction_method="manuell", confidence=1.0, validation_status=ValStatus.VERIFIZIERT,
                validation_message=f"Manuell korrigiert von {user_name} am {now:%d.%m.%Y}" + (f": {reason}" if reason else ""),
            ))
        if ev and ev.field_name == field:
            ev.validation_message = f"{ev.validation_message or ''}; ersetzt durch manuelle Korrektur".strip("; ")
    elif action == "verwerfen":
        db.add(Correction(entity="review_issue", entity_id=issue.id, action="verworfen", reason=reason or "Hinweis nicht relevant",
                          user_name=user_name, review_issue_id=issue.id))
    else:
        raise HTTPException(400, "Unbekannte Aktion")

    issue.status, issue.resolved_at = "erledigt", now
    issue.resolution_note = f"{action} durch {user_name}" + (f": {reason}" if reason else "")
    if issue.offer_id:
        offer = db.get(Offer, issue.offer_id)
        if offer:
            _update_validation_status(db, offer)
    db.commit()
    return redirect("/pruefung", msg=f"Prüfpunkt #{issue.id} erledigt ({action})")


@router.get("/pruefung/protokoll")
def correction_log(request: Request, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Correction).order_by(Correction.id.desc()).limit(1000)))
    return templates.TemplateResponse(request, "corrections.html", {"nav": "pruefung", "rows": rows})
