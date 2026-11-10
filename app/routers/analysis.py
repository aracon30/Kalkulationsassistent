"""Ähnlichkeitssuche."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.models import OfferItem
from app.services.excel_reader import parse_number
from app.services.similarity import load_config, rank
from app.web import templates

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
