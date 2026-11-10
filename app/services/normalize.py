"""Vereinheitlichung technischer Bezeichnungen (Werkstoffe, Produkttypen, Temperaturbereiche)."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta

# Werkstoffnummer -> Synonyme (Kurzname, AISI). Erweiterbar.
MATERIAL_SYNONYMS: dict[str, list[str]] = {
    "1.4404": ["x2crnimo17-12-2", "316l", "aisi 316l", "v4a"],
    "1.4571": ["x6crnimoti17-12-2", "316ti", "aisi 316ti"],
    "1.4401": ["x5crnimo17-12-2", "316", "aisi 316"],
    "1.4301": ["x5crni18-10", "304", "aisi 304", "v2a"],
    "1.4307": ["x2crni18-9", "304l", "aisi 304l"],
    "1.4462": ["x2crninmon22-5-3", "duplex", "2205"],
    "1.4539": ["x1nicrmocu25-20-5", "904l"],
    "1.0425": ["p265gh", "h ii", "hii"],
    "1.0345": ["p235gh", "h i", "hi"],
    "1.0566": ["p355nl1"],
    "1.0038": ["s235jr", "s235jrg2", "st 37-2", "st37"],
    "1.0570": ["s355j2g3", "s355j2", "st 52-3", "st52"],
}
MATERIAL_GROUPS = {
    "austenit": {"1.4404", "1.4571", "1.4401", "1.4301", "1.4307", "1.4539"},
    "duplex": {"1.4462"},
    "kesselstahl": {"1.0425", "1.0345", "1.0566"},
    "baustahl": {"1.0038", "1.0570"},
}
_MAT_LOOKUP = {syn: num for num, syns in MATERIAL_SYNONYMS.items() for syn in syns}

PRODUCT_TYPES = {
    "Kerzenfilter": ["kerzenfilter", "kf", "candle filter", "filterkerzen", "kerzen-filter"],
    "Beutelfilter": ["beutelfilter", "bf", "bag filter", "filterbeutel", "beutel-filter"],
    "Siebfilter": ["siebfilter", "sf", "strainer", "korbfilter"],
    "Rückspülfilter": ["rückspülfilter", "rueckspuelfilter", "backflush filter"],
    "Druckbehälter": ["druckbehälter", "druckbehaelter", "behälter", "pressure vessel"],
    "Wärmetauscher": ["wärmetauscher", "waermetauscher", "heat exchanger"],
}
_PT_LOOKUP = {syn: canon for canon, syns in PRODUCT_TYPES.items() for syn in syns}

DN_SERIES = [10, 15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700,
             750, 800, 850, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1800, 2000, 2200, 2400, 2600, 2800, 3000,
             3200, 3400, 3600, 3800, 4000]


def normalize_material(raw: str | None) -> tuple[str | None, bool]:
    """Liefert (normalisierter Werkstoff, sicher erkannt?). Unbekannte Bezeichnungen bleiben unverändert."""
    if raw is None:
        return None, False
    text = str(raw).strip()
    if not text:
        return None, False
    m = re.search(r"\b1[.,]\s?(\d{4})\b", text)
    if m:
        return f"1.{m.group(1)}", True
    key = re.sub(r"\s+", " ", text.lower()).strip()
    if key in _MAT_LOOKUP:
        return _MAT_LOOKUP[key], True
    compact = key.replace(" ", "")
    for syn, num in _MAT_LOOKUP.items():
        if syn.replace(" ", "") == compact:
            return num, True
    return text, False


def material_group(material: str | None) -> str | None:
    if not material:
        return None
    for group, members in MATERIAL_GROUPS.items():
        if material in members:
            return group
    return None


def normalize_product_type(raw: str | None) -> tuple[str | None, bool]:
    if raw is None:
        return None, False
    text = str(raw).strip()
    if not text:
        return None, False
    key = text.lower()
    if key in _PT_LOOKUP:
        return _PT_LOOKUP[key], True
    for syn, canon in sorted(_PT_LOOKUP.items(), key=lambda kv: -len(kv[0])):
        if len(syn) > 3 and syn in key:
            return canon, True
    return text, False


def parse_temperature_range(raw) -> tuple[float | None, float | None]:
    """'-10/200 °C', '-10 ... +200', '200' -> (min, max). Ein Einzelwert gilt als Maximum."""
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return None, float(raw)
    nums = re.findall(r"[-+−–]?\s?\d+(?:[.,]\d+)?", str(raw))
    vals = []
    for n in nums:
        n = n.replace("−", "-").replace("–", "-").replace(" ", "").replace(",", ".")
        try:
            vals.append(float(n))
        except ValueError:
            pass
    if not vals:
        return None, None
    if len(vals) == 1:
        return None, vals[0]
    return min(vals[0], vals[1]), max(vals[0], vals[1])


def parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:  # Excel-Seriennummer
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    text = str(value).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_standard_dn(dn: float | None) -> bool:
    return dn is not None and int(dn) == dn and int(dn) in DN_SERIES


# --- Materialauszug: Abmaße, Kategorien, Gewichtsschätzung ---------------------------------------

DENSITY_KG_M3 = {"edelstahl": 7900.0, "stahl": 7850.0}

MATERIAL_CATEGORIES: list[tuple[str, str]] = [
    # (Muster in Form/Benennung, Kategorie) – Reihenfolge = Priorität
    (r"filterkerze|filterbeutel|filterkorb|siebkorb|erstbest(ü|ue)ckung", "Filterelement"),
    (r"dichtung", "Dichtung"),
    (r"schraube|mutter|bolzen|gewindestange|scheibe", "Verbindungselement"),
    (r"blindflansch|flansch", "Flansch"),
    (r"kl(ö|oe)pper|korbbogen|boden", "Boden"),
    (r"muffe|bogen|t-st(ü|ue)ck|reduzier|kappe|nippel", "Formteil"),
    (r"rohr", "Rohr"),
    (r"blech|flachstahl|platte|lasche", "Blech/Flachstahl"),
    (r"rund(stahl|material)|vollmaterial|welle", "Rundmaterial"),
    (r"e-teil|kaufteil|zukauf|davit|typenschild", "Kauf-/Einbauteil"),
]


_SPECIFIC = {"Filterelement", "Dichtung", "Verbindungselement"}


def material_category(description: str | None, form: str | None) -> str:
    """Eindeutige Bauteilarten (Filterelement, Dichtung, Schrauben) aus Benennung oder Form zuerst,
    danach die Form ("Rohr, geschweißt", "V-Flansch"), zuletzt die Benennung."""
    both = f"{form or ''} {description or ''}".lower()
    for rx, cat in MATERIAL_CATEGORIES:
        if cat in _SPECIFIC and re.search(rx, both):
            return cat
    for source in ((form or "").lower(), (description or "").lower()):
        for rx, cat in MATERIAL_CATEGORIES:
            if source and re.search(rx, source):
                return cat
    return "Sonstiges"


def _num_de(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_dimensions(text: str | None) -> dict:
    """Zerlegt Abmaßangaben: 'Ø323,9 x 4,5', 'DN300 PN16', '3190 x 1350 x 8', 'Ø314 x 15', 'M24', 'G1/2"'."""
    out: dict = {}
    if not text:
        return out
    t = str(text).replace("×", "x").replace("X", "x")
    m = re.search(r"\bDN\s*(\d+)", t, re.IGNORECASE)
    if m:
        out["dn"] = float(m.group(1))
    m = re.search(r"\bPN\s*(\d+)", t, re.IGNORECASE)
    if m:
        out["pn"] = float(m.group(1))
    m = re.search(r"\b(M\d+|G\s?\d+(?:/\d+)?\"?|R\s?\d+(?:/\d+)?\"?)", t)
    if m and "dn" not in out:
        out["thread"] = m.group(1).replace(" ", "")
    m = re.search(r"[ØøΦ⌀]\s*(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)(?:\s*x\s*(\d+(?:[.,]\d+)?))?", t)
    if m:
        out["outer_diameter"] = _num_de(m.group(1))
        out["second"] = _num_de(m.group(2))  # Wand (Rohr) bzw. Dicke/Länge – Bedeutung abhängig von Kategorie
        if m.group(3):
            out["third"] = _num_de(m.group(3))
        return out
    m = re.search(r"[ØøΦ⌀]\s*(\d+(?:[.,]\d+)?)", t)
    if m:
        out["outer_diameter"] = _num_de(m.group(1))
        return out
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)", t)
    if m:
        out["length"], out["width"], out["thickness"] = (_num_de(g) for g in m.groups())
        return out
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)", t)
    if m:
        out["width"], out["thickness"] = _num_de(m.group(1)), _num_de(m.group(2))
    return out


def _density(material: str | None) -> float | None:
    if not material:
        return None
    if re.match(r"1\.4\d{3}", material) or material.lower() in {"316l", "304", "304l", "316ti", "v4a", "v2a"}:
        return DENSITY_KG_M3["edelstahl"]
    if re.match(r"1\.0\d{3}", material) or material.lower().startswith(("p2", "p3", "s2", "s3")):
        return DENSITY_KG_M3["stahl"]
    return None


def estimate_weight(category: str, dims: dict, material: str | None, length_m: float | None) -> tuple[float | None, str | None]:
    """Geschätztes Gewicht je Preiseinheit (kg/m bei Rohren mit Länge, sonst kg/Stück). Nur Rohr/Blech."""
    rho = _density(material)
    if rho is None:
        return None, None
    if category == "Rohr" and dims.get("outer_diameter") and dims.get("second"):
        d, s = dims["outer_diameter"], dims["second"]
        if s * 2 >= d:
            return None, None
        kg_per_m = math.pi * (d - s) * s * 1e-6 * rho
        return (kg_per_m, "kg/m") if length_m else (None, None)
    if category == "Blech/Flachstahl" or category == "Boden":
        if dims.get("length") and dims.get("width") and dims.get("thickness"):
            return dims["length"] * dims["width"] * dims["thickness"] * 1e-9 * rho, "kg/Stk"
        if category == "Blech/Flachstahl" and dims.get("outer_diameter") and dims.get("second"):  # Ronde Ø x t
            return math.pi / 4 * dims["outer_diameter"] ** 2 * dims["second"] * 1e-9 * rho, "kg/Stk"
    return None, None



def price_group_key(category: str | None, form: str | None, dimensions: str | None, material: str | None,
                    price_unit: str | None, description: str | None) -> tuple:
    """Schlüssel für "gleiches Bauteil" in der Preisstatistik.

    Kategorie + Form + Abmaße + Werkstoff + Preiseinheit. Ohne Abmaße wäre das zu grob
    (Prallblech, Standfuß und Erdungslasche wären "gleich") – dann zählt zusätzlich die Benennung
    (ohne Stutzenbezeichnungen wie N1/N2 und ohne Zahlen).
    """
    norm = lambda x: re.sub(r"\s+", " ", (x or "").strip().lower())  # noqa: E731
    dims = (dimensions or "").replace(" ", "").lower()
    desc = ""
    if not dims:
        desc = re.sub(r"\bn\d+([/+]n?\d+)*\b|\d+([.,]\d+)?", "", norm(description))
        desc = re.sub(r"\s+", " ", desc).strip()
    return (category or "", norm(form), dims, (material or "").lower(), price_unit or "", desc)
