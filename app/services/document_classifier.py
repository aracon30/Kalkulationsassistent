"""Regelbasierte Dokumentenklassifizierung.

Jede Regel vergibt Punkte für einen Dokumenttyp und wird protokolliert.
Dateinamensregeln allein führen höchstens zu "Wahrscheinlich erkannt";
für "Eindeutig erkannt" muss der Inhalt (Blattnamen, Zellbeschriftungen,
PDF-Text/Metadaten) die Einordnung bestätigen.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.models import ClassStatus, DocType
from app.services.excel_reader import normalize_label, peek_workbook
from app.services.folder_scanner import KNOWN_UNSUPPORTED, SUPPORTED_EXTENSIONS

DRAWING_NAME_RE = re.compile(r"^(?P<prefix>[A-Z]{1,4})-\d+-\d+(?:\.\d+)*-\d{2,4}", re.IGNORECASE)
PRODUCT_PREFIXES = {"KF": "Kerzenfilter", "BF": "Beutelfilter"}

CALC_LABELS = ["materialkosten", "selbstkosten", "angebotspreis", "verkaufspreis", "fertigungskosten", "materialgemeinkosten",
               "gemeinkosten", "herstellkosten", "zukaufteile", "konstruktionskosten", "deckungsbeitrag"]
TIME_LABELS = ["zuschnitt", "vorbereitung", "ruesten", "heften", "schweissen", "schleifen", "montage", "pruefung",
               "verteilzeit", "fertigungszeit", "gesamtfertigungszeit", "arbeitsgang"]
BOM_LABELS = ["benennung", "menge", "stueckliste", "artikelnummer", "teilenummer", "werkstoff", "einheit"]
DRAWING_WORDS = ["massstab", "maßstab", "zeichnungsnummer", "zeichnungs-nr", "zeichn.-nr", "gezeichnet", "gez.", "geprüft",
                 "allgemeintoleranz", "iso 2768", "blatt", "werkstoff", "projektionsmethode", "gewicht"]
OFFER_WORDS = ["angebot", "gesamtpreis", "zahlungsbedingungen", "lieferzeit", "gültigkeit", "lieferbedingungen", "incoterms", "netto"]
CAD_CREATORS = ["solidworks", "inventor", "autocad", "creo", "catia", "solid edge", "nx", "hicad", "medusa"]


@dataclass
class Classification:
    document_type: str
    status: str
    confidence: float
    rules: list[str] = field(default_factory=list)
    product_hint: str | None = None
    page_count: int | None = None
    has_text: bool | None = None
    pdf_text: str | None = None

    @property
    def rule_text(self) -> str:
        return "; ".join(self.rules)


def _count(labels: list[str], words: list[str]) -> int:
    joined = set(labels)
    hits = 0
    for w in words:
        if any(l == w or l.startswith(w + " ") or l.startswith(w) for l in joined):
            hits += 1
    return hits


def read_pdf_basics(path: str | Path, max_pages: int = 3) -> tuple[int, str, dict, tuple[float, float] | None]:
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        if doc.needs_pass:
            raise ValueError("PDF ist passwortgeschützt")
        text = "\n".join(doc[i].get_text("text") for i in range(min(max_pages, doc.page_count)))
        size = (doc[0].rect.width, doc[0].rect.height) if doc.page_count else None
        return doc.page_count, text, dict(doc.metadata or {}), size


def classify_file(path: str | Path, read_content: bool = True) -> Classification:
    p = Path(path)
    name = p.name
    stem = normalize_label(p.stem)
    ext = p.suffix.lower()
    scores: dict[str, float] = defaultdict(float)
    content_scores: dict[str, float] = defaultdict(float)
    rules: list[str] = []
    product_hint = None

    def add(doc_type: str, points: float, rule: str, content: bool = False) -> None:
        scores[doc_type] += points
        if content:
            content_scores[doc_type] += points
        rules.append(f"{rule} → {DocType.LABELS[doc_type]} (+{points:g})")

    if ext not in SUPPORTED_EXTENSIONS:
        reason = "Altes Excel-Format (.xls) wird nicht unterstützt" if ext == ".xls" else f"Dateityp '{ext or 'ohne Endung'}' wird nicht ausgewertet"
        status = ClassStatus.NICHT_UNTERSTUETZT
        return Classification(DocType.SONSTIGES, status, 0.0, [reason if ext in KNOWN_UNSUPPORTED or ext == ".xls" else reason])

    # --- Dateinamensregeln ---
    if "fertigungszeit" in stem or "fertigungsstunden" in stem or "arbeitsplan" in stem or "fertigungsplan" in stem:
        add(DocType.FERTIGUNGSZEIT, 4, "Dateiname enthält 'Fertigungszeit'")
    if "kalkulation" in stem or re.search(r"(^|[ _-])kalk([ _.-]|$)", stem):
        add(DocType.KALKULATION, 3, "Dateiname enthält 'Kalkulation'")
    if re.search(r"st(ue|ü)ckliste|(^|[ _-])bom([ _-]|$)|teileliste", stem):
        add(DocType.STUECKLISTE, 3, "Dateiname enthält 'Stückliste'")
    if re.search(r"lieferant|zukauf|quotation|(^|[ _-])la[ _-]", stem) or any(normalize_label(x) in {"lieferanten", "lieferantenangebote", "zukauf"} for x in p.parts[:-1]):
        add(DocType.LIEFERANTENANGEBOT, 3, "Dateiname/Ordner deutet auf Lieferantenangebot")
    elif ext == ".pdf" and re.search(r"(^|[ _-])angebot", stem) and "kalkulation" not in stem:
        add(DocType.KUNDENANGEBOT, 3, "PDF-Dateiname enthält 'Angebot'")
    m = DRAWING_NAME_RE.match(name)
    if m and ext == ".pdf":
        add(DocType.ZEICHNUNG, 3, f"Dateiname entspricht Zeichnungsnummernmuster ({m.group(0)})")
        product_hint = PRODUCT_PREFIXES.get(m.group("prefix").upper())
        if product_hint:
            rules.append(f"Präfix '{m.group('prefix').upper()}' → möglicher {product_hint}")
    if ext == ".pdf" and re.search(r"zeichnung|drawing|(^|[ _-])zng([ _-]|$)", stem):
        add(DocType.ZEICHNUNG, 2, "Dateiname enthält 'Zeichnung'")

    page_count = None
    has_text = None
    pdf_text = None
    content_error = None

    # --- Inhaltsregeln ---
    if read_content and ext in {".xlsx", ".xlsm"}:
        try:
            sheets, labels = peek_workbook(p)
            sheet_norm = [normalize_label(s) for s in sheets]
            if any("kalkulation" in s or s.startswith("kalk") for s in sheet_norm):
                add(DocType.KALKULATION, 2, "Tabellenblattname enthält 'Kalkulation'", True)
            if any("fertigung" in s or "arbeitsg" in s or "zeiten" in s for s in sheet_norm):
                add(DocType.FERTIGUNGSZEIT, 2, "Tabellenblattname enthält 'Fertigung/Zeiten'", True)
            if any("stueckliste" in s or s == "bom" for s in sheet_norm):
                add(DocType.STUECKLISTE, 2, "Tabellenblattname enthält 'Stückliste'", True)
            calc_hits, time_hits, bom_hits = _count(labels, CALC_LABELS), _count(labels, TIME_LABELS), _count(labels, BOM_LABELS)
            if calc_hits >= 2:
                add(DocType.KALKULATION, min(4, calc_hits), f"{calc_hits} typische Kalkulationsbegriffe in Zellen", True)
            if time_hits >= 3:
                add(DocType.FERTIGUNGSZEIT, min(5, time_hits), f"{time_hits} typische Arbeitsgänge in Zellen", True)
            if bom_hits >= 3 and calc_hits < 2:
                add(DocType.STUECKLISTE, 2, f"{bom_hits} typische Stücklistenspalten", True)
            if ext == ".xlsm":
                rules.append("Makro-Arbeitsmappe: Makros werden nicht ausgeführt")
        except Exception as exc:  # noqa: BLE001 - jeder Lesefehler führt zu manueller Prüfung
            content_error = f"Inhalt nicht lesbar: {type(exc).__name__}: {exc}"
    elif read_content and ext == ".pdf":
        try:
            page_count, text, meta, size = read_pdf_basics(p)
            pdf_text = text
            has_text = len(text.strip()) > 20
            low = text.lower()
            if not has_text:
                rules.append("PDF ohne Textebene (vermutlich Scan) – OCR/KI-Auswertung optional")
            drawing_hits = sum(1 for w in DRAWING_WORDS if w in low)
            if drawing_hits >= 2:
                add(DocType.ZEICHNUNG, min(4, drawing_hits), f"{drawing_hits} Schriftfeldbegriffe im PDF-Text", True)
            creator = f"{meta.get('creator', '')} {meta.get('producer', '')}".lower()
            if any(c in creator for c in CAD_CREATORS):
                add(DocType.ZEICHNUNG, 2, "PDF-Metadaten: CAD-Programm als Ersteller", True)
            if size and max(size) >= 1100 and drawing_hits:
                add(DocType.ZEICHNUNG, 1, "Seitenformat ≥ A3", True)
            offer_hits = sum(1 for w in OFFER_WORDS if w in low)
            if offer_hits >= 3:
                target = DocType.LIEFERANTENANGEBOT if scores.get(DocType.LIEFERANTENANGEBOT) else DocType.KUNDENANGEBOT
                add(target, min(4, offer_hits), f"{offer_hits} typische Angebotsbegriffe im PDF-Text", True)
            if "stückliste" in low or "stueckliste" in low:
                add(DocType.STUECKLISTE, 2, "PDF-Text enthält 'Stückliste'", True)
            if not product_hint:
                if "kerzenfilter" in low:
                    product_hint = "Kerzenfilter"
                elif "beutelfilter" in low:
                    product_hint = "Beutelfilter"
        except Exception as exc:  # noqa: BLE001
            content_error = f"PDF nicht lesbar: {type(exc).__name__}: {exc}"

    if content_error:
        rules.append(content_error)

    if not scores:
        return Classification(DocType.SONSTIGES, ClassStatus.MANUELL if content_error else ClassStatus.WAHRSCHEINLICH,
                              0.2, rules or ["Keine Erkennungsregel zutreffend"], product_hint, page_count, has_text, pdf_text)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    confirmed_by_content = content_scores.get(best_type, 0) >= 2
    if content_error:
        status = ClassStatus.MANUELL
    elif best >= 5 and best - second >= 3 and confirmed_by_content:
        status = ClassStatus.EINDEUTIG
    elif best >= 3 and best - second >= 1:
        status = ClassStatus.WAHRSCHEINLICH
    else:
        status = ClassStatus.MANUELL
    confidence = round(min(1.0, best / 8.0) * (1.0 if best - second >= 2 else 0.7), 2)
    return Classification(best_type, status, confidence, rules, product_hint, page_count, has_text, pdf_text)
