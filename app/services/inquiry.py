"""Arbeitsbereich "Neue Anfrage": ähnliche Apparate, Materialauszug der Vorlage mit Preisstatistik,
Vorbelegung der Vorkalkulation nach HETA-Logik. Liest nur – importierte Originaldaten bleiben unverändert."""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Calculation, Inquiry, MaterialLine, Offer, OfferItem
from app.services.normalize import price_group_key
from app.services.similarity import Match, load_config, rank


def _dims_key(dims: str | None) -> str:
    return (dims or "").replace(" ", "").lower()


@dataclass
class PriceStat:
    n: int
    median: float
    minimum: float
    maximum: float
    latest: float
    latest_offer: str


def material_price_stats(db: Session) -> dict[tuple, PriceStat]:
    """Preisstatistik je Kategorie + Abmaße + Werkstoff + Einheit über alle aktuellen Kalkulationen."""
    rows = db.execute(
        select(MaterialLine.category, MaterialLine.form, MaterialLine.dimensions, MaterialLine.material, MaterialLine.material_raw,
               MaterialLine.price_unit, MaterialLine.description, MaterialLine.unit_price, Offer.offer_number, Offer.offer_date, MaterialLine.id)
        .join(Calculation, MaterialLine.calculation_id == Calculation.id).join(OfferItem, MaterialLine.offer_item_id == OfferItem.id)
        .join(Offer, OfferItem.offer_id == Offer.id)
        .where(Calculation.is_current.is_(True), MaterialLine.unit_price.is_not(None))
    ).all()
    groups: dict[tuple, list] = defaultdict(list)
    for cat, form, dims, mat, mat_raw, unit, desc, price, offer_no, offer_date, lid in rows:
        groups[price_group_key(cat, form, dims, mat or mat_raw, unit, desc)].append((price, offer_date, lid, offer_no))
    out = {}
    for key, vals in groups.items():
        prices = [p for p, *_ in vals]
        latest = max(vals, key=lambda x: (x[1] is not None, x[1], x[2]))
        out[key] = PriceStat(len(prices), statistics.median(prices), min(prices), max(prices), latest[0], latest[3])
    return out


def line_key(l: MaterialLine) -> tuple:
    return price_group_key(l.category, l.form, l.dimensions, l.material or l.material_raw, l.price_unit, l.description)


def query_from_inquiry(inq: Inquiry) -> dict:
    return {"product_type": inq.product_type, "dn": inq.dn, "design_pressure": inq.design_pressure, "material": inq.material,
            "filter_area": inq.filter_area, "weight": inq.weight, "filter_elements": inq.filter_elements}


def similar_items(db: Session, inq: Inquiry, similarity_file, top_n: int = 5) -> list[Match]:
    from app.routers.analysis import candidate_rows

    query = {k: v for k, v in query_from_inquiry(inq).items() if v not in (None, "")}
    if not query:
        return []
    return rank(query, candidate_rows(db), load_config(similarity_file), top_n=top_n)


def template_lines(db: Session, item: OfferItem | None, stats: dict[tuple, PriceStat]) -> list[dict]:
    if item is None or item.current_calculation is None:
        return []
    lines = db.scalars(select(MaterialLine).where(MaterialLine.calculation_id == item.current_calculation.id).order_by(MaterialLine.source_row))
    out = []
    for i, l in enumerate(lines):
        st = stats.get(line_key(l))
        housings = item.quantity or 1
        qty = (l.quantity / housings) if l.quantity is not None else 1.0  # Anzahl je Gehäuse
        out.append({
            "i": i, "description": l.description, "category": l.category, "dimensions": l.dimensions, "material": l.material or l.material_raw,
            "qty": qty, "length": l.length_m, "apz": l.apz_cost or 0.0, "price_then": l.unit_price, "unit": l.price_unit,
            "mfg": (l.manufacturing_cost or 0.0) / housings,  # Bearbeitungskosten der Zeile (Spalte "Fertigungskosten")
            "median": st.median if st else None, "n": st.n if st else 0, "latest": st.latest if st else None,
            "min": st.minimum if st else None, "max": st.maximum if st else None,
            "price": st.median if (st and st.n > 1) else l.unit_price,  # Ansatz: Median, wenn mehrere Werte vorhanden
            "include": True,
        })
    return out


def prefill_inputs(item: OfferItem | None, inq: Inquiry, defaults: dict[str, float]) -> dict[str, float | None]:
    """Startwerte der Vorkalkulation aus der Vorlage (Stunden, Fremdleistungen, Sätze) – alles änderbar."""
    v: dict[str, float | None] = dict(defaults)
    calc = item.current_calculation if item else None
    v["weight"] = inq.weight or (item.weight if item else None)
    if calc is None:
        return v
    v.update({
        "manufacturing_hours": calc.manufacturing_hours,
        "engineering_hours": calc.engineering_hours,
        "third_party_cost": calc.inspection_cost,
        "ndt_cost": calc.ndt_tuev_cost,
        "material_cost": calc.material_cost,
    })
    if calc.manufacturing_hourly_rate:
        v["manufacturing_hourly_rate"] = calc.manufacturing_hourly_rate
    if calc.documentation_cost is not None and defaults.get("documentation_hourly_rate"):
        v["documentation_hours"] = round(calc.documentation_cost / defaults["documentation_hourly_rate"], 2)
    if calc.material_cost and calc.material_overhead is not None:
        v["material_overhead_rate"] = round(calc.material_overhead / calc.material_cost, 4)
    if calc.material_cost and calc.material_overhead is not None and calc.sales_overhead is not None:
        v["sales_overhead_rate"] = round(calc.sales_overhead / (calc.material_cost + calc.material_overhead), 4)
    if calc.total_cost and calc.risk_profit is not None:
        v["risk_profit_rate"] = round(calc.risk_profit / calc.total_cost, 4)
    if calc.surface_treatment_cost is not None and v.get("weight"):
        ref_weight = item.weight or v["weight"]
        if ref_weight:
            v["pickling_price_per_kg"] = round(calc.surface_treatment_cost / ref_weight, 4)
    return {k: val for k, val in v.items()}


def reference_summary(matches: list[Match]) -> dict:
    """Spannen der ähnlichen Apparate als Plausibilitätsrahmen."""
    def span(key):
        vals = [m.data.get(key) for m in matches if m.data.get(key) is not None]
        return (min(vals), statistics.median(vals), max(vals)) if vals else None
    return {"price": span("offer_price"), "price_per_kg": span("price_per_kg"), "hours": span("manufacturing_hours")}


def load_item(db: Session, item_id: int | None) -> OfferItem | None:
    if not item_id:
        return None
    return db.scalars(select(OfferItem).where(OfferItem.id == item_id)
                      .options(selectinload(OfferItem.offer), selectinload(OfferItem.calculations))).first()
