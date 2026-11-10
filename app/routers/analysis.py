"""Ähnlichkeitssuche und Kalkulationssimulation."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.models import Calculation, Offer, OfferItem, Simulation
from app.services.calculation_engine import calculate, inputs_from_calculation, load_scheme
from app.services.excel_reader import parse_number
from app.services.similarity import load_config, rank
from app.web import redirect, templates

router = APIRouter()

QUERY_FIELDS = ["product_type", "dn", "design_pressure", "material", "filter_area", "weight", "filter_elements"]


def candidate_rows(db: Session) -> list[dict]:
    rows = []
    items = db.scalars(select(OfferItem).options(selectinload(OfferItem.offer), selectinload(OfferItem.calculations)))
    for it in items:
        calc = it.current_calculation
        price = calc.offer_price if calc else None
        rows.append({
            "item_id": it.id, "offer_id": it.offer_id, "offer_number": it.offer.offer_number, "position": it.position_number,
            "status": it.offer.status, "validation_status": it.validation_status, "product_type": it.product_type, "dn": it.dn,
            "design_pressure": it.design_pressure, "material": it.material, "filter_area": it.filter_area, "weight": it.weight,
            "filter_elements": it.filter_elements, "offer_price": price, "manufacturing_hours": calc.manufacturing_hours if calc else None,
            "price_per_kg": (price / it.weight) if (price and it.weight) else None, "year": it.offer.year,
        })
    return rows


@router.get("/aehnlichkeit")
def similarity_page(request: Request, db: Session = Depends(get_db), product_type: str = "", dn: str = "", design_pressure: str = "",
                    material: str = "", filter_area: str = "", weight: str = "", filter_elements: str = ""):
    raw = {"product_type": product_type, "dn": dn, "design_pressure": design_pressure, "material": material,
           "filter_area": filter_area, "weight": weight, "filter_elements": filter_elements}
    query = {}
    for k, v in raw.items():
        if not v.strip():
            continue
        query[k] = v.strip() if k in ("product_type", "material") else parse_number(v)
    config = load_config(get_settings().similarity_file)
    matches = rank(query, candidate_rows(db), config) if query else []
    types = [t for (t,) in db.execute(select(OfferItem.product_type).distinct()) if t]
    return templates.TemplateResponse(request, "similarity.html", {"nav": "aehnlichkeit", "raw": raw, "matches": matches, "config": config,
                                                                   "types": types, "searched": bool(query)})


class SimInput(BaseModel):
    values: dict[str, float | str | None]


@router.get("/simulation")
def simulation_page(request: Request, position: int | None = None, db: Session = Depends(get_db)):
    scheme = load_scheme(get_settings().simulation_scheme_file)
    item = db.get(OfferItem, position) if position else None
    calc = item.current_calculation if item else None
    prefill = dict(scheme.defaults)
    prefill.update(inputs_from_calculation(calc, item))
    result = calculate(scheme, prefill)
    sims = list(db.scalars(select(Simulation).order_by(Simulation.id.desc()).limit(20)))
    items = list(db.scalars(select(OfferItem).join(Offer).options(selectinload(OfferItem.offer)).order_by(Offer.offer_number.desc(), OfferItem.position_number)))
    return templates.TemplateResponse(request, "simulation.html", {"nav": "simulation", "scheme": scheme, "prefill": prefill,
                                                                   "result": result, "item": item, "calc": calc, "sims": sims,
                                                                   "items": items})


@router.post("/api/simulation/berechnen")
def simulate(data: SimInput):
    scheme = load_scheme(get_settings().simulation_scheme_file)
    return calculate(scheme, data.values).to_dict()


@router.post("/simulation/speichern")
def save_simulation(name: str = Form(...), inputs_json: str = Form(...), position: str = Form(""), db: Session = Depends(get_db)):
    scheme = load_scheme(get_settings().simulation_scheme_file)
    try:
        inputs = json.loads(inputs_json)
        if not isinstance(inputs, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Ungültige Eingaben") from None
    result = calculate(scheme, inputs)
    item = db.get(OfferItem, int(position)) if position.isdigit() else None
    calc = item.current_calculation if item else None
    # Simulationen werden separat gespeichert – Originalkalkulationen bleiben unverändert.
    db.add(Simulation(name=name.strip()[:255] or "Simulation", offer_item_id=item.id if item else None,
                      base_calculation_id=calc.id if calc else None, scheme_id=scheme.id,
                      inputs_json=json.dumps(result.inputs, ensure_ascii=False), results_json=json.dumps(result.to_dict(), ensure_ascii=False, default=str)))
    db.commit()
    return redirect("/simulation", position=item.id if item else None)


@router.get("/simulation/{sim_id}")
def show_simulation(sim_id: int, request: Request, db: Session = Depends(get_db)):
    sim = db.get(Simulation, sim_id)
    if not sim:
        raise HTTPException(404, "Simulation nicht gefunden")
    base = db.get(Calculation, sim.base_calculation_id) if sim.base_calculation_id else None
    return templates.TemplateResponse(request, "simulation_detail.html", {"nav": "simulation", "sim": sim, "result": json.loads(sim.results_json),
                                                                          "base": base})
