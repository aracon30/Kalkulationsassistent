"""Materialpreis-Datenbank: Bauteilzeilen aus allen Materialauszügen, mit Filtern, Preisspannen und CSV-Export."""
from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Calculation, Document, MaterialLine, Offer, OfferItem
from app.services.excel_reader import parse_number
from app.services.normalize import price_group_key
from app.web import templates

router = APIRouter()


def _query(db: Session, q: str | None, kategorie: str | None, werkstoff: str | None, d_min: str | None, d_max: str | None,
           dn: str | None, jahr: str | None, nur_aktuell: bool):
    stmt = (select(MaterialLine).join(Calculation, MaterialLine.calculation_id == Calculation.id)
            .join(OfferItem, MaterialLine.offer_item_id == OfferItem.id).join(Offer, OfferItem.offer_id == Offer.id)
            .options(selectinload(MaterialLine.offer_item).selectinload(OfferItem.offer), selectinload(MaterialLine.calculation)))
    if nur_aktuell:
        stmt = stmt.where(Calculation.is_current.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(MaterialLine.description.ilike(like), MaterialLine.form.ilike(like), MaterialLine.dimensions.ilike(like),
                              MaterialLine.standard.ilike(like)))
    if kategorie:
        stmt = stmt.where(MaterialLine.category == kategorie)
    if werkstoff:
        stmt = stmt.where(or_(MaterialLine.material.ilike(f"%{werkstoff}%"), MaterialLine.material_raw.ilike(f"%{werkstoff}%")))
    lo, hi = parse_number(d_min), parse_number(d_max)
    if lo is not None:
        stmt = stmt.where(MaterialLine.outer_diameter >= lo)
    if hi is not None:
        stmt = stmt.where(MaterialLine.outer_diameter <= hi)
    dn_v = parse_number(dn)
    if dn_v is not None:
        stmt = stmt.where(MaterialLine.dn == dn_v)
    if jahr and str(jahr).isdigit():
        stmt = stmt.where(Offer.year == int(jahr))
    return stmt.order_by(MaterialLine.category, MaterialLine.description, Offer.offer_number.desc())


def _group(lines: list[MaterialLine]) -> list[dict]:
    """Preisspannen je gleichartigem Bauteil: Kategorie + Abmaße + Werkstoff + Preiseinheit."""
    groups: dict[tuple, list[MaterialLine]] = defaultdict(list)
    for l in lines:
        if l.unit_price is None:
            continue
        groups[price_group_key(l.category, l.form, l.dimensions, l.material or l.material_raw, l.price_unit, l.description)].append(l)
    out = []
    for key, items in groups.items():
        cat, unit, mat = key[0], key[4], items[0].material or items[0].material_raw or ""
        prices = sorted(x.unit_price for x in items)
        latest = max(items, key=lambda x: (x.offer_item.offer.offer_date or x.offer_item.offer.created_at.date(), x.id))
        out.append({"category": cat, "form": items[0].form, "dimensions": items[0].dimensions, "material": mat, "unit": unit, "n": len(items),
                    "min": prices[0], "median": statistics.median(prices), "max": prices[-1],
                    "latest_price": latest.unit_price, "latest_offer": latest.offer_item.offer, "example": items[0].description,
                    "price_per_kg": statistics.median([x.price_per_kg for x in items if x.price_per_kg]) if any(x.price_per_kg for x in items) else None})
    out.sort(key=lambda g: (g["category"] or "", -g["n"], g["dimensions"] or ""))
    return out


@router.get("/material")
def material_page(request: Request, q: str | None = None, kategorie: str | None = None, werkstoff: str | None = None,
                  d_min: str | None = None, d_max: str | None = None, dn: str | None = None, jahr: str | None = None,
                  ansicht: str = "gruppen", alle_versionen: bool = False, db: Session = Depends(get_db)):
    lines = list(db.scalars(_query(db, q, kategorie, werkstoff, d_min, d_max, dn, jahr, not alle_versionen).limit(5000)))
    cats = [c for (c,) in db.execute(select(MaterialLine.category).distinct().order_by(MaterialLine.category)) if c]
    years = [y for (y,) in db.execute(select(Offer.year).distinct().order_by(Offer.year.desc())) if y]
    f = {"q": q or "", "kategorie": kategorie or "", "werkstoff": werkstoff or "", "d_min": d_min or "", "d_max": d_max or "",
         "dn": dn or "", "jahr": jahr or "", "ansicht": ansicht, "alle_versionen": alle_versionen}
    return templates.TemplateResponse(request, "materials.html", {"nav": "material", "lines": lines, "groups": _group(lines) if ansicht == "gruppen" else [],
                                                                  "cats": cats, "years": years, "f": f, "query": request.url.query})


@router.get("/material/export.csv")
def material_csv(q: str | None = None, kategorie: str | None = None, werkstoff: str | None = None, d_min: str | None = None,
                 d_max: str | None = None, dn: str | None = None, jahr: str | None = None, alle_versionen: bool = False,
                 db: Session = Depends(get_db)):
    lines = db.scalars(_query(db, q, kategorie, werkstoff, d_min, d_max, dn, jahr, not alle_versionen))
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Angebot", "Angebotsdatum", "Pos.", "Kategorie", "Benennung", "Form", "Abmaße", "DN", "PN", "Außen-Ø [mm]", "Wand/Dicke [mm]",
                "Werkstoff", "Werkstoff (Quelle)", "Anzahl", "Länge [m]", "Einzelpreis", "Einheit", "APZ-Kosten", "Kosten", "Gesamtkosten",
                "Gewicht geschätzt", "Einheit Gewicht", "€/kg geschätzt", "Datei", "Blatt", "Zeile"])
    num = lambda v: "" if v is None else f"{v:.4f}".rstrip("0").rstrip(".").replace(".", ",")
    for l in lines:
        o = l.offer_item.offer
        w.writerow([o.offer_number, o.offer_date or "", l.offer_item.position_number, l.category, l.description, l.form or "", l.dimensions or "",
                    num(l.dn), num(l.pn), num(l.outer_diameter), num(l.wall_thickness), l.material or "", l.material_raw or "", num(l.quantity),
                    num(l.length_m), num(l.unit_price), l.price_unit or "", num(l.apz_cost), num(l.cost), num(l.total_cost), num(l.est_weight),
                    l.est_weight_unit or "", num(l.price_per_kg), l.document.file_name if l.document else "", l.source_sheet or "", l.source_row or ""])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="materialpreise.csv"'})
