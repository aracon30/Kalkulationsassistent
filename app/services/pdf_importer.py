"""PDF-Auswertung: Textextraktion, technische Schlüsselbegriffe, Seitenvorschau.

Alle Werte aus Zeichnungen erhalten höchstens den Status "Prüfbedarf", solange sie
nicht durch eine zweite Quelle (Kalkulation) bestätigt werden. Zeichnungsmaße
werden nicht als Fertigungs- oder drucktechnisch freigegebene Werte interpretiert.
Eingebettete Skripte/Aktionen in PDFs werden nie ausgeführt (nur Text und Bild).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.normalize import normalize_material, normalize_product_type

DRAWING_NO_RE = re.compile(r"\b([A-Z]{1,4}-\d+-\d+(?:\.\d+)*-\d{2,4})\b")
DN_RE = re.compile(r"\bDN\s*[:=]?\s*(\d{2,4})\b")
PS_RE = re.compile(r"\bPS\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)\s*(?:bar|barg|bar\s*\(?g\)?)", re.IGNORECASE)
TS_RE = re.compile(r"\bTS\s*[:=]?\s*([-+−]?\s?\d+(?:[.,]\d+)?)\s*(?:°\s*C)?\s*(?:/|bis|\.\.\.|…|-(?=\s*[+\d]))\s*([-+−]?\s?\d+(?:[.,]\d+)?)\s*°\s*C", re.IGNORECASE)
TS_SINGLE_RE = re.compile(r"\bTS\s*[:=]?\s*([-+−]?\d+(?:[.,]\d+)?)\s*°\s*C", re.IGNORECASE)
MATERIAL_RE = re.compile(r"\b(1\.\d{4})\b")
MATERIAL_LABEL_RE = re.compile(r"(?:Werkstoff|Material)\s*[:=]?\s*([A-Za-z0-9.\-/ ]{3,30})", re.IGNORECASE)
CODE_RE = re.compile(r"\b(AD[\s-]?2000(?:[\s-]?Merkblatt)?|EN\s?13445(?:-\d)?|ASME\s(?:VIII|Sec\.?\s?VIII)(?:\s?Div\.?\s?\d)?|PED\s?2014/68/EU|DGRL\s?2014/68/EU)", re.IGNORECASE)
AREA_RE = re.compile(r"(?:Filterfl(?:ä|ae)che|Filtrationsfl(?:ä|ae)che)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*m(?:²|2)", re.IGNORECASE)
AREA_LOOSE_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*m²")
ELEMENTS_RE = re.compile(r"\b(\d{1,3})\s*(?:x|×|Stk\.?|Stück)?\s*(Filterkerzen|Kerzen|Filterbeutel|Beutel|Filterelemente|Elemente)\b", re.IGNORECASE)
ELEMENTS_LABEL_RE = re.compile(r"Anzahl\s+(?:Filter)?(?:kerzen|beutel|elemente)\s*[:=]?\s*(\d{1,3})", re.IGNORECASE)


@dataclass
class PdfValue:
    field_name: str
    value: object
    text: str
    page: int
    snippet: str
    confidence: float
    note: str | None = None


@dataclass
class PdfExtraction:
    page_count: int
    has_text: bool
    values: list[PdfValue] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    text_by_page: list[str] = field(default_factory=list)

    def first(self, name: str) -> PdfValue | None:
        return next((v for v in self.values if v.field_name == name), None)


def _num(s: str) -> float:
    return float(s.replace("−", "-").replace(" ", "").replace(",", "."))


def _snippet(text: str, start: int, end: int, width: int = 40) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - width): end + width]).strip()


def extract_pdf(path: str | Path, max_pages: int = 20) -> PdfExtraction:
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        if doc.needs_pass:
            raise ValueError("PDF ist passwortgeschützt")
        pages = [doc[i].get_text("text") for i in range(min(doc.page_count, max_pages))]
        result = PdfExtraction(page_count=doc.page_count, has_text=sum(len(p.strip()) for p in pages) > 20, text_by_page=pages)
    if not result.has_text:
        result.issues.append("Keine Textebene gefunden (gescannte Zeichnung?) – Werte nur manuell oder per optionaler KI-Auswertung erfassbar")
        return result

    def add(name: str, value, page: int, m: re.Match, text: str, conf: float, note: str | None = None, unique: bool = True) -> None:
        if unique and any(v.field_name == name for v in result.values):
            existing = [v for v in result.values if v.field_name == name]
            if all(v.value != value for v in existing):
                result.values.append(PdfValue(name, value, m.group(0), page, _snippet(text, m.start(), m.end()), conf * 0.6, "Weitere abweichende Fundstelle im Dokument"))
            return
        result.values.append(PdfValue(name, value, m.group(0), page, _snippet(text, m.start(), m.end()), conf, note))

    for pno, text in enumerate(pages, start=1):
        for m in DRAWING_NO_RE.finditer(text):
            add("drawing_number", m.group(1).upper(), pno, m, text, 0.8)
        for m in DN_RE.finditer(text):
            add("dn", float(m.group(1)), pno, m, text, 0.7, "DN aus Zeichnungstext – Bezug (Behälter/Stutzen) prüfen")
        for m in PS_RE.finditer(text):
            add("design_pressure", _num(m.group(1)), pno, m, text, 0.75)
        ts_found = False
        for m in TS_RE.finditer(text):
            a, b = _num(m.group(1)), _num(m.group(2))
            add("design_temperature_min", min(a, b), pno, m, text, 0.7)
            add("design_temperature_max", max(a, b), pno, m, text, 0.7)
            ts_found = True
        if not ts_found:
            for m in TS_SINGLE_RE.finditer(text):
                add("design_temperature_max", _num(m.group(1)), pno, m, text, 0.6)
        for m in MATERIAL_LABEL_RE.finditer(text):
            mat, ok = normalize_material(m.group(1))
            if ok:
                add("material", mat, pno, m, text, 0.7)
        if not any(v.field_name == "material" for v in result.values):
            for m in MATERIAL_RE.finditer(text):
                add("material", m.group(1), pno, m, text, 0.5, "Werkstoffnummer ohne Beschriftung gefunden")
        for m in CODE_RE.finditer(text):
            add("design_code", re.sub(r"\s+", " ", m.group(1)).upper().replace("AD-2000", "AD 2000"), pno, m, text, 0.7)
        for m in AREA_RE.finditer(text):
            add("filter_area", _num(m.group(1)), pno, m, text, 0.75)
        if not any(v.field_name == "filter_area" for v in result.values):
            for m in AREA_LOOSE_RE.finditer(text):
                add("filter_area", _num(m.group(1)), pno, m, text, 0.4, "Fläche ohne Beschriftung – Bezug prüfen")
        for m in ELEMENTS_LABEL_RE.finditer(text):
            add("filter_elements", int(m.group(1)), pno, m, text, 0.75)
        if not any(v.field_name == "filter_elements" for v in result.values):
            for m in ELEMENTS_RE.finditer(text):
                add("filter_elements", int(m.group(1)), pno, m, text, 0.6)
        for word in ("Kerzenfilter", "Beutelfilter", "Siebfilter", "Rückspülfilter"):
            idx = text.lower().find(word.lower())
            if idx >= 0 and not any(v.field_name == "product_type" for v in result.values):
                canon, _ = normalize_product_type(word)
                m = re.search(re.escape(text[idx: idx + len(word)]), text)
                if m:
                    add("product_type", canon, pno, m, text, 0.6)
    return result


def render_page_png(path: str | Path, page: int, zoom: float = 1.5) -> bytes:
    import pymupdf

    zoom = max(0.25, min(zoom, 4.0))
    with pymupdf.open(str(path)) as doc:
        if page < 1 or page > doc.page_count:
            raise IndexError("Seite existiert nicht")
        pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")
