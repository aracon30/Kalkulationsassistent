"""Gemeinsame Hilfen für die Weboberfläche (Templates, Formatierung)."""
from __future__ import annotations

from datetime import date, datetime

from fastapi.templating import Jinja2Templates

from app.config import APP_ROOT, APP_VERSION
from app.models import ClassStatus, DocType, ValStatus

templates = Jinja2Templates(directory=str(APP_ROOT / "app" / "templates"))


def de_num(value, decimals: int = 2, empty: str = "–") -> str:
    if value is None or value == "":
        return empty
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    s = f"{v:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def de_auto(value, empty: str = "–") -> str:
    """Zahl ohne überflüssige Nachkommastellen."""
    if value is None or value == "":
        return empty
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)) and float(value).is_integer():
        # technische Kennwerte (DN 1000, 1450 kg) ohne Tausenderpunkt
        return str(int(value)) if abs(value) < 10000 else de_num(value, 0)
    if isinstance(value, float):
        return de_num(value, 2)
    return str(value)


def de_date(value, empty: str = "–") -> str:
    if value is None:
        return empty
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def pct(value, empty: str = "–") -> str:
    return empty if value is None else de_num(value * 100, 1) + " %"


templates.env.filters["de_num"] = de_num
templates.env.filters["de_auto"] = de_auto
templates.env.filters["de_date"] = de_date
templates.env.filters["pct"] = pct
templates.env.globals.update(
    APP_VERSION=APP_VERSION,
    DOC_LABELS=DocType.LABELS,
    CLASS_LABELS=ClassStatus.LABELS,
    VAL_LABELS=ValStatus.LABELS,
)

FIELD_LABELS = {
    "offer_number": "Angebotsnummer", "customer": "Kunde", "offer_date": "Angebotsdatum", "country": "Land",
    "project_name": "Projekt", "industry": "Branche", "status": "Status", "currency": "Währung", "revision": "Revision",
    "position_number": "Position", "product_type": "Produkttyp", "description": "Bezeichnung", "drawing_number": "Zeichnungsnummer",
    "dn": "DN", "design_pressure": "Auslegungsdruck PS [bar]", "design_temperature": "Auslegungstemperatur TS",
    "design_temperature_min": "TS min [°C]", "design_temperature_max": "TS max [°C]", "material": "Werkstoff",
    "diameter": "Behälterdurchmesser [mm]", "wall_thickness": "Wandstärke [mm]", "height": "Apparatehöhe [mm]",
    "volume": "Behältervolumen [l]", "filter_elements": "Anzahl Filterelemente", "filter_area": "Filterfläche [m²]",
    "design_code": "Regelwerk", "weight": "Gewicht [kg]",
    "material_cost": "Materialkosten", "material_overhead": "Materialgemeinkosten", "purchased_parts_cost": "Zukaufteile",
    "manufacturing_hours": "Fertigungsstunden [h]", "manufacturing_hourly_rate": "Fertigungsstundensatz [€/h]",
    "manufacturing_cost": "Fertigungskosten", "engineering_hours": "Konstruktionsstunden [h]", "engineering_cost": "Konstruktionskosten",
    "documentation_cost": "Dokumentationskosten", "inspection_cost": "Externe Prüfkosten", "ndt_tuev_cost": "ZFP/TÜV",
    "surface_treatment_cost": "Oberflächenbehandlung", "procurement_cost": "Beschaffungskosten", "sales_overhead": "Vertriebsgemeinkosten",
    "risk_profit": "Risiko-/Gewinnzuschlag", "total_cost": "Selbstkosten", "offer_price": "Angebotspreis", "calculation_date": "Kalkulationsdatum",
}
templates.env.globals["FIELD_LABELS"] = FIELD_LABELS


def redirect(path: str, **params):
    """Weiterleitung mit sauber kodierten Meldungen (Post/Redirect/Get)."""
    from urllib.parse import urlencode

    from fastapi.responses import RedirectResponse

    query = urlencode({k: v for k, v in params.items() if v is not None})
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)
