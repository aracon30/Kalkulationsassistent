"""Neue Anfrage: Eckdaten -> ähnliche Apparate -> Materialauszug der Vorlage -> Vorkalkulation (HETA R05) -> speichern."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Inquiry, OfferItem
from app.services import heta_calc
from app.services.excel_reader import parse_number
from app.services.inquiry import (load_item, material_price_stats, prefill_inputs, reference_summary, similar_items,
                                  template_lines)
from app.services.normalize import normalize_material, normalize_product_type
from app.web import redirect, templates

router = APIRouter()
STATUSES = ["in Arbeit", "angeboten", "verworfen"]
CALC_FIELDS = [  # (Schlüssel, Beschriftung, Gruppe)
    ("material_cost", "Materialkosten je Gehäuse [€]", "Kosten"),
    ("manufacturing_hours", "Fertigungsstunden [h]", "Kosten"),
    ("manufacturing_hourly_rate", "Stundensatz Fertigung [€/h]", "Sätze"),
    ("engineering_hours", "Konstruktion [h]", "Kosten"),
    ("engineering_hourly_rate", "Stundensatz Konstruktion [€/h]", "Sätze"),
    ("documentation_hours", "Dokumentation [h]", "Kosten"),
    ("documentation_hourly_rate", "Stundensatz Dokumentation [€/h]", "Sätze"),
    ("third_party_cost", "Third Party (Vorprüfung, Berechnung, Druckprobe) [€]", "Kosten"),
    ("ndt_cost", "ZFP + TÜV [€]", "Kosten"),
    ("weight", "Gewicht für Beizen [kg]", "Kosten"),
    ("pickling_price_per_kg", "Beizen / Passivieren [€/kg]", "Sätze"),
    ("coating_cost", "Lackieren / Innenauskleidung [€]", "Kosten"),
    ("packaging_cost", "Verpackung [€]", "Kosten"),
    ("shipping_cost", "Versand [€]", "Kosten"),
    ("material_overhead_rate", "Materialgemeinkosten [%]", "Hebesätze"),
    ("procurement_rate", "Beschaffung extern [%]", "Hebesätze"),
    ("sales_overhead_rate", "Vertriebsgemeinkosten [%]", "Hebesätze"),
    ("risk_profit_rate", "Risiko + Gewinn [%]", "Hebesätze"),
    ("commission_rate", "Provision [%]", "Hebesätze"),
]
RATE_FIELDS = {k for k, _, g in CALC_FIELDS if g == "Hebesätze"}


def _defaults() -> dict[str, float]:
    return heta_calc.load_defaults(get_settings().heta_calc_file)


def _num(v: str | None) -> float | None:
    return parse_number(v) if v not in (None, "") else None


def _apply_basics(inq: Inquiry, title: str, customer: str, product_type: str, dn: str, design_pressure: str, material: str,
                  filter_elements: str, filter_area: str, weight: str, quantity: str) -> None:
    inq.title = title.strip()[:255] or "Neue Anfrage"
    inq.customer = customer.strip() or None
    inq.product_type = normalize_product_type(product_type)[0] if product_type.strip() else None
    inq.dn = _num(dn)
    inq.design_pressure = _num(design_pressure)
    inq.material = normalize_material(material)[0] if material.strip() else None
    fe = _num(filter_elements)
    inq.filter_elements = int(fe) if fe is not None else None
    inq.filter_area = _num(filter_area)
    inq.weight = _num(weight)
    q = _num(quantity)
    inq.quantity = int(q) if q else None


@router.get("/anfragen")
def inquiry_list(request: Request, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Inquiry).order_by(Inquiry.updated_at.desc(), Inquiry.id.desc())))
    types = [t for (t,) in db.execute(select(OfferItem.product_type).distinct()) if t]
    return templates.TemplateResponse(request, "inquiries.html", {"nav": "anfragen", "rows": rows, "types": types, "pre": {}})


@router.get("/anfragen/neu")
def inquiry_new(request: Request, ref: int | None = None, db: Session = Depends(get_db)):
    item = load_item(db, ref)
    pre = {}
    if item:
        pre = {"product_type": item.product_type, "dn": item.dn, "material": item.material, "filter_elements": item.filter_elements,
               "weight": item.weight, "ref": item.id, "title": f"Anfrage ähnlich {item.offer.offer_number} Pos. {item.position_number}"}
    rows = list(db.scalars(select(Inquiry).order_by(Inquiry.updated_at.desc())))
    types = [t for (t,) in db.execute(select(OfferItem.product_type).distinct()) if t]
    return templates.TemplateResponse(request, "inquiries.html", {"nav": "anfragen", "rows": rows, "types": types, "pre": pre})


@router.post("/anfragen")
def inquiry_create(title: str = Form(""), customer: str = Form(""), product_type: str = Form(""), dn: str = Form(""),
                   design_pressure: str = Form(""), material: str = Form(""), filter_elements: str = Form(""),
                   filter_area: str = Form(""), weight: str = Form(""), quantity: str = Form(""), ref: str = Form(""),
                   db: Session = Depends(get_db)):
    inq = Inquiry(title="")
    _apply_basics(inq, title, customer, product_type, dn, design_pressure, material, filter_elements, filter_area, weight, quantity)
    if not any([inq.product_type, inq.dn, inq.material, inq.filter_elements, inq.weight]):
        return redirect("/anfragen", err="Bitte mindestens Produkttyp, DN, Werkstoff, Elemente oder Gewicht angeben")
    if ref.isdigit():
        inq.reference_item_id = int(ref)
    else:
        matches = similar_items(db, inq, get_settings().similarity_file, top_n=1)
        inq.reference_item_id = matches[0].item_id if matches else None
    db.add(inq)
    db.commit()
    return redirect(f"/anfragen/{inq.id}")


@router.get("/anfragen/{inq_id}")
def inquiry_workspace(inq_id: int, request: Request, ref: int | None = None, db: Session = Depends(get_db)):
    inq = db.get(Inquiry, inq_id)
    if not inq:
        raise HTTPException(404, "Anfrage nicht gefunden")
    settings = get_settings()
    matches = similar_items(db, inq, settings.similarity_file)
    ref_id = ref or inq.reference_item_id or (matches[0].item_id if matches else None)
    item = load_item(db, ref_id)
    defaults = _defaults()
    same_ref = ref_id == inq.reference_item_id
    stats = material_price_stats(db)
    lines = template_lines(db, item, stats)
    inputs = prefill_inputs(item, inq, defaults)
    if same_ref and inq.calc_inputs_json:
        inputs.update(json.loads(inq.calc_inputs_json))
    if same_ref and inq.material_json:
        saved = {int(x["i"]): x for x in json.loads(inq.material_json)}
        for l in lines:
            if l["i"] in saved:
                l.update({k: saved[l["i"]][k] for k in ("qty", "length", "price", "apz", "mfg", "include") if k in saved[l["i"]]})
    form_inputs = {k: (round(v * 100, 3) if (k in RATE_FIELDS and v is not None) else v) for k, v in inputs.items()}
    return templates.TemplateResponse(request, "inquiry.html", {
        "nav": "anfragen", "inq": inq, "matches": matches, "item": item, "lines": lines, "inputs": form_inputs,
        "fields": CALC_FIELDS, "rate_fields": RATE_FIELDS, "summary": reference_summary(matches), "statuses": STATUSES,
        "use_material_sum": bool(lines) and not (same_ref and inq.calc_inputs_json and json.loads(inq.calc_inputs_json).get("_manual_material")),
    })


class CalcRequest(BaseModel):
    inputs: dict[str, float | str | None]


@router.post("/api/anfragen/berechnen")
def inquiry_calculate(data: CalcRequest):
    return heta_calc.calculate(data.inputs, _defaults()).to_dict()


@router.post("/anfragen/{inq_id}/speichern")
def inquiry_save(inq_id: int, calc_inputs_json: str = Form("{}"), material_json: str = Form("[]"), ref: str = Form(""),
                 notes: str = Form(""), status: str = Form("in Arbeit"), db: Session = Depends(get_db)):
    inq = db.get(Inquiry, inq_id)
    if not inq:
        raise HTTPException(404, "Anfrage nicht gefunden")
    try:
        inputs = json.loads(calc_inputs_json or "{}")
        mats = json.loads(material_json or "[]")
        if not isinstance(inputs, dict) or not isinstance(mats, list):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Ungültige Eingaben") from None
    result = heta_calc.calculate(inputs, _defaults())
    inq.calc_inputs_json = json.dumps(inputs, ensure_ascii=False)
    inq.material_json = json.dumps(mats, ensure_ascii=False)
    inq.result_price = result.price_fca if result.complete else None
    inq.reference_item_id = int(ref) if ref.isdigit() else inq.reference_item_id
    inq.notes = notes.strip() or None
    inq.status = status if status in STATUSES else inq.status
    db.commit()
    return redirect(f"/anfragen/{inq.id}", msg="Anfrage gespeichert")


@router.post("/anfragen/{inq_id}/eckdaten")
def inquiry_update_basics(inq_id: int, title: str = Form(""), customer: str = Form(""), product_type: str = Form(""), dn: str = Form(""),
                          design_pressure: str = Form(""), material: str = Form(""), filter_elements: str = Form(""),
                          filter_area: str = Form(""), weight: str = Form(""), quantity: str = Form(""), db: Session = Depends(get_db)):
    inq = db.get(Inquiry, inq_id)
    if not inq:
        raise HTTPException(404, "Anfrage nicht gefunden")
    _apply_basics(inq, title, customer, product_type, dn, design_pressure, material, filter_elements, filter_area, weight, quantity)
    db.commit()
    return redirect(f"/anfragen/{inq.id}", msg="Eckdaten aktualisiert")


@router.post("/anfragen/{inq_id}/loeschen")
def inquiry_delete(inq_id: int, db: Session = Depends(get_db)):
    inq = db.get(Inquiry, inq_id)
    if inq:
        db.delete(inq)
        db.commit()
    return redirect("/anfragen", msg="Anfrage gelöscht")
