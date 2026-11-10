"""Plausibilitätsregeln und Validierungsstatus.

- "verifiziert": Wert stammt aus einem validierten Profil, ist eindeutig, besitzt ggf. einen
  konsistenten Formel-Cache und besteht alle Plausibilitätsregeln – oder wurde manuell freigegeben.
- "pruefbedarf": alles andere (fehlend, widersprüchlich, unsicher, unvalidiertes Profil, KI).
- "nicht_importierbar": Datei/Struktur nicht verarbeitbar.
KI-Ergebnisse erhalten nie automatisch "verifiziert".
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import ValStatus
from app.services.normalize import is_standard_dn

NON_NEGATIVE = {
    "material_cost", "material_overhead", "purchased_parts_cost", "manufacturing_hours", "manufacturing_hourly_rate",
    "manufacturing_cost", "engineering_hours", "engineering_cost", "documentation_cost", "inspection_cost",
    "ndt_tuev_cost", "surface_treatment_cost", "procurement_cost", "sales_overhead", "total_cost", "offer_price",
    "dn", "diameter", "wall_thickness", "height", "volume", "filter_elements", "filter_area", "weight",
}

# (min, max) – grobe Plausibilitätsgrenzen; Abweichungen erzeugen Prüfbedarf, keine Ablehnung.
RANGES = {
    "manufacturing_hours": (0.5, 20000),
    "engineering_hours": (0, 5000),
    "manufacturing_hourly_rate": (20, 300),
    "design_pressure": (-1, 400),
    "design_temperature_min": (-273, 1000),
    "design_temperature_max": (-273, 1000),
    "dn": (10, 6000),
    "wall_thickness": (0.5, 200),
    "filter_elements": (1, 2000),
    "filter_area": (0.01, 5000),
    "weight": (0.5, 500000),
    "offer_price": (1, 50_000_000),
    "total_cost": (1, 50_000_000),
}
OPERATION_RANGE = (0, 5000)


@dataclass
class RuleResult:
    ok: bool
    code: str | None = None
    message: str | None = None


def check_value(field: str, value, group: str = "") -> list[RuleResult]:
    out: list[RuleResult] = []
    if value is None:
        return out
    if isinstance(value, tuple):
        return out
    if isinstance(value, (int, float)):
        if field in NON_NEGATIVE and value < 0:
            out.append(RuleResult(False, "negativ", f"{field} ist negativ ({value})"))
        rng = OPERATION_RANGE if group == "operation" else RANGES.get(field)
        if rng and not (rng[0] <= value <= rng[1]):
            out.append(RuleResult(False, "unplausibel", f"{field} = {value:g} liegt außerhalb des plausiblen Bereichs {rng[0]:g}–{rng[1]:g}"))
        if field == "offer_price" and value <= 0:
            out.append(RuleResult(False, "preis_null", "Angebotspreis ist nicht größer als 0"))
        if field == "dn" and not is_standard_dn(value):
            out.append(RuleResult(False, "dn_nicht_genormt", f"DN {value:g} ist keine übliche Nennweite"))
    return out


def decide_status(*, profile_validated: bool, confidence: float, has_issues: bool, method: str) -> tuple[str, str | None]:
    if method == "ki":
        return ValStatus.PRUEFBEDARF, "KI-Ergebnis – manuelle Bestätigung erforderlich"
    if has_issues:
        return ValStatus.PRUEFBEDARF, "Regelverletzung oder Unsicherheit bei der Extraktion"
    if method.startswith("pdf"):
        return ValStatus.PRUEFBEDARF, "Wert aus Zeichnungstext – Bestätigung durch Kalkulation oder manuell erforderlich"
    if not profile_validated:
        return ValStatus.PRUEFBEDARF, "Importprofil noch nicht gegen Originaldateien validiert"
    if confidence < 0.85:
        return ValStatus.PRUEFBEDARF, f"Erkennungssicherheit {confidence:.2f} unter 0,85"
    return ValStatus.VERIFIZIERT, None


def values_conflict(a, b, rel_tol: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) > max(rel_tol * max(abs(float(a)), abs(float(b))), 1e-6)
    return str(a).strip().lower() != str(b).strip().lower()
