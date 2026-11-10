"""Scan- und Importablauf (idempotent, transaktional je Angebot).

1. Verzeichnis scannen, Dateien inventarisieren, SHA-256 berechnen
2. Bekannte Dateien erkennen (Pfad + Größe + Änderungsdatum bzw. Hash)
3. Dokumenttyp bestimmen, Angebotsnummer und Revision erkennen
4. Import: je Angebotsnummer eine Transaktion – Excel-Profil wählen, Werte extrahieren,
   Positionen zuordnen, Plausibilität prüfen, speichern, Prüfbedarf erzeugen.
"""
from __future__ import annotations

import logging
import re
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Calculation, ClassStatus, Document, DocType, ExtractionValue, ImportStatus, ManufacturingOperation, Offer,
    OfferItem, ReviewIssue, ValStatus,
)
from app.services import validation
from app.services.document_classifier import DRAWING_NAME_RE, PRODUCT_PREFIXES, classify_file
from app.services.excel_importer import Extracted, ImportProfile, Issue, ProfileError, WorkbookExtraction, extract_workbook, load_profiles
from app.services.excel_reader import ExcelReadError
from app.services.folder_scanner import ScanResult, scan_directory, sha256_file
from app.services.normalize import normalize_material, normalize_product_type
from app.services.offer_numbers import detect_offer_number, detect_revision
from app.services.pdf_importer import PdfExtraction, extract_pdf

log = logging.getLogger(__name__)

IMPORTABLE_TYPES = {DocType.KALKULATION, DocType.FERTIGUNGSZEIT, DocType.ZEICHNUNG, DocType.KUNDENANGEBOT,
                    DocType.STUECKLISTE, DocType.LIEFERANTENANGEBOT}
ITEM_FIELDS = ["position_number", "product_type", "description", "drawing_number", "dn", "design_pressure",
               "design_temperature_min", "design_temperature_max", "material", "diameter", "wall_thickness", "height",
               "volume", "filter_elements", "filter_area", "design_code", "weight"]
OFFER_FIELDS = ["customer", "project_name", "offer_date", "country", "industry", "status", "currency", "revision"]
PDF_CROSSCHECK = ["drawing_number", "product_type", "dn", "design_pressure", "design_temperature_min", "design_temperature_max",
                  "material", "design_code", "filter_area", "filter_elements"]


class ImportCancelled(Exception):
    pass


@dataclass
class Reporter:
    """Rückmeldungen an den Job (eigene Session, damit keine Teil-Transaktion festgeschrieben wird)."""
    progress: Callable[[int, int, str], None] | None = None
    entry: Callable[[str | None, int | None, str | None, str, str, str | None], None] | None = None
    cancelled: Callable[[], bool] | None = None
    checkpoint: Callable[[], None] | None = None  # nach jedem Commit: gepufferte Protokolle schreiben

    def commit_point(self) -> None:
        if self.checkpoint:
            self.checkpoint()

    def tick(self, done: int, total: int, current: str) -> None:
        if self.progress:
            self.progress(done, total, current)

    def log(self, offer_number: str | None, document_id: int | None, path: str | None, action: str, status: str, message: str | None = None) -> None:
        if self.entry:
            self.entry(offer_number, document_id, path, action, status, message)

    def check_cancel(self) -> None:
        if self.cancelled and self.cancelled():
            raise ImportCancelled()


# --- Scan ------------------------------------------------------------------------

@dataclass
class ScanSummary:
    files_found: int = 0
    new_documents: int = 0
    changed_documents: int = 0
    unchanged: int = 0
    duplicates: int = 0
    problems: list[str] = field(default_factory=list)
    cancelled: bool = False


def scan_and_inventory(session: Session, base_dir: str, reporter: Reporter | None = None) -> ScanSummary:
    reporter = reporter or Reporter()
    settings = get_settings()
    summary = ScanSummary()
    found: ScanResult = scan_directory(
        base_dir, settings.excluded_dir_names, settings.max_file_size_mb,
        progress=lambda n, p: reporter.tick(0, 0, f"Suche Dateien … {n} gefunden"),
        cancel=reporter.cancelled,
    )
    summary.files_found = len(found.files)
    for prob in found.problems:
        summary.problems.append(f"{prob.kind}: {prob.path} – {prob.message}")
        reporter.log(None, None, prob.path, "scan", "fehler" if prob.kind != "zu_gross" else "uebersprungen", prob.message)

    total = len(found.files)
    for idx, f in enumerate(found.files, start=1):
        if reporter.cancelled and reporter.cancelled():
            summary.cancelled = True
            break
        reporter.tick(idx, total, f.path)
        try:
            _inventory_file(session, f, found, summary, reporter)
        except Exception as exc:  # noqa: BLE001 - eine defekte Datei darf den Scan nicht abbrechen
            session.rollback()
            msg = f"{type(exc).__name__}: {exc}"
            summary.problems.append(f"fehler: {f.path} – {msg}")
            reporter.log(None, None, f.path, "scan", "fehler", msg)
            continue
        if idx % 25 == 0:
            session.commit()
            reporter.commit_point()
    session.commit()
    reporter.commit_point()
    return summary


def _inventory_file(session: Session, f, found: ScanResult, summary: ScanSummary, reporter: Reporter) -> None:
    current = session.scalars(select(Document).where(Document.file_path == f.path, Document.is_current.is_(True))).first()
    now = datetime.now().replace(microsecond=0)
    if current and current.file_size == f.size and current.modified_at == f.modified_at:
        current.scanned_at = now
        summary.unchanged += 1
        return
    if f.path.lower() in found.locked_hints:
        reporter.log(None, None, f.path, "scan", "hinweis", "Excel-Sperrdatei gefunden – Datei ist evtl. geöffnet; es wird eine Kopie gelesen")
    try:
        file_hash = sha256_file(f.path)
    except PermissionError as exc:
        raise PermissionError(f"Datei gesperrt oder keine Leseberechtigung ({exc.strerror or exc})") from exc

    existing = session.scalars(select(Document).where(Document.file_path == f.path, Document.file_hash == file_hash)).first()
    if existing:
        # gleicher Inhalt, nur Zeitstempel geändert
        existing.file_size, existing.modified_at, existing.scanned_at = f.size, f.modified_at, now
        if not existing.is_current:
            for d in session.scalars(select(Document).where(Document.file_path == f.path, Document.id != existing.id)):
                d.is_current = False
            existing.is_current = True
        summary.unchanged += 1
        return

    versions = session.scalars(select(Document).where(Document.file_path == f.path)).all()
    for d in versions:
        if d.is_current:
            d.is_current = False
            if d.import_status == ImportStatus.NEU:
                d.import_status = ImportStatus.VERALTET
    cls = classify_file(f.path)
    offer = detect_offer_number(f.path)
    rev_label, _ = detect_revision(f.name)
    dup = session.scalars(select(Document).where(Document.file_hash == file_hash, Document.file_path != f.path, Document.is_current.is_(True))).first()
    doc = Document(
        file_path=f.path, relative_path=f.relative_path, file_name=f.name, extension=f.extension,
        document_type=cls.document_type, classification_status=cls.status, classification_rule=cls.rule_text,
        classification_confidence=cls.confidence, detected_offer_number=offer.number if offer else None,
        offer_number_source=offer.source if offer else None, file_hash=file_hash, file_size=f.size,
        modified_at=f.modified_at, scanned_at=now, version=(max((d.version for d in versions), default=0) + 1),
        is_current=True, revision_label=rev_label, page_count=cls.page_count, has_text=cls.has_text,
        duplicate_of_id=dup.id if dup else None,
    )
    if cls.status == ClassStatus.NICHT_UNTERSTUETZT:
        doc.import_status, doc.import_message = ImportStatus.NICHT_UNTERSTUETZT, cls.rule_text
    elif dup:
        doc.import_status, doc.import_message = ImportStatus.IGNORIERT, f"Inhaltsgleiches Duplikat von {dup.file_path}"
        summary.duplicates += 1
    elif cls.document_type not in IMPORTABLE_TYPES:
        doc.import_status, doc.import_message = ImportStatus.IGNORIERT, "Dokumenttyp ohne Datenimport"
    session.add(doc)
    session.flush()
    if versions:
        summary.changed_documents += 1
    else:
        summary.new_documents += 1


# --- Import ----------------------------------------------------------------------

@dataclass
class ImportSummary:
    offers: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


def pending_documents(session: Session, force: bool = False, base_dir: str | None = None, retry_failed: bool = False) -> list[Document]:
    """Neue Dokumente; mit ``retry_failed`` auch fehlgeschlagene (z. B. zuvor gesperrte), mit ``force`` alle."""
    q = select(Document).where(Document.is_current.is_(True), Document.document_type.in_(IMPORTABLE_TYPES))
    if force:
        q = q.where(Document.import_status.in_([ImportStatus.NEU, ImportStatus.IMPORTIERT, ImportStatus.FEHLER]))
    elif retry_failed:
        q = q.where(Document.import_status.in_([ImportStatus.NEU, ImportStatus.FEHLER]))
    else:
        q = q.where(Document.import_status == ImportStatus.NEU)
    docs = list(session.scalars(q.order_by(Document.file_path)))
    if base_dir:
        prefix = str(Path(base_dir)).lower()
        docs = [d for d in docs if d.file_path.lower().startswith(prefix)]
    return docs


def run_import(session: Session, reporter: Reporter | None = None, force: bool = False, base_dir: str | None = None,
               profiles: dict[str, ImportProfile] | None = None, retry_failed: bool = False) -> ImportSummary:
    reporter = reporter or Reporter()
    summary = ImportSummary()
    profiles = profiles or load_profiles(get_settings().profiles_dir)
    docs = pending_documents(session, force, base_dir, retry_failed)
    groups: dict[str | None, list[Document]] = defaultdict(list)
    for d in docs:
        groups[d.detected_offer_number].append(d)

    # Dokumente ohne Angebotsnummer -> Prüfbedarf
    for d in groups.pop(None, []):
        d.import_status, d.import_message = ImportStatus.FEHLER, "Keine Angebotsnummer erkannt – bitte Ordner/Dateiname prüfen"
        _issue(session, "keine_angebotsnummer", d.import_message, "fehler", document=d)
        summary.failed += 1
        reporter.log(None, d.id, d.file_path, "import", "fehler", d.import_message)
    session.commit()
    reporter.commit_point()

    total = sum(len(v) for v in groups.values())
    done = 0
    for offer_number in sorted(groups):
        group = groups[offer_number]
        if reporter.cancelled and reporter.cancelled():
            summary.cancelled = True
            break
        doc_ids = [d.id for d in group]
        try:
            stats = import_offer_group(session, offer_number, group, profiles, force, reporter,
                                       lambda name: reporter.tick(done, total, name))
            session.commit()
            summary.offers += 1
            summary.imported += stats["imported"]
            summary.failed += stats["failed"]
            for line in stats["log"]:
                reporter.log(offer_number, *line)
            reporter.commit_point()
        except ImportCancelled:
            session.rollback()
            summary.cancelled = True
            reporter.log(offer_number, None, None, "import", "abgebrochen", "Import abgebrochen – Angebot vollständig zurückgerollt")
            reporter.commit_point()
            break
        except Exception as exc:  # noqa: BLE001 - Fehler eines Angebots darf andere nicht stoppen
            session.rollback()
            msg = f"{type(exc).__name__}: {exc}"
            log.error("Import von %s fehlgeschlagen: %s\n%s", offer_number, msg, traceback.format_exc())
            summary.errors.append(f"{offer_number}: {msg}")
            for d in session.scalars(select(Document).where(Document.id.in_(doc_ids))):
                d.import_status, d.import_message = ImportStatus.FEHLER, f"Import zurückgerollt: {msg}"
                summary.failed += 1
                reporter.log(offer_number, d.id, d.file_path, "import", "fehler", d.import_message)
            session.commit()
            reporter.commit_point()
        done += len(group)
        reporter.tick(done, total, offer_number)
    return summary


def _issue(session: Session, code: str, message: str, severity: str = "warnung", *, offer: Offer | None = None,
           item: OfferItem | None = None, document: Document | None = None, value: ExtractionValue | None = None) -> None:
    session.flush()
    exists = session.scalars(select(ReviewIssue).where(
        ReviewIssue.code == code, ReviewIssue.message == message, ReviewIssue.status == "offen",
        ReviewIssue.document_id == (document.id if document else None),
        ReviewIssue.offer_item_id == (item.id if item else None),
    )).first()
    if exists:
        return
    session.add(ReviewIssue(code=code, message=message, severity=severity,
                            offer_id=offer.id if offer else (item.offer_id if item else (document.offer_id if document else None)),
                            offer_item_id=item.id if item else None, document_id=document.id if document else None,
                            extraction_value_id=value.id if value else None))


def _clear_document_data(session: Session, doc: Document) -> None:
    """Für erneuten (erzwungenen) Import: abgeleitete Daten dieses Dokuments entfernen."""
    for model in (ExtractionValue, ManufacturingOperation, Calculation):
        for row in session.scalars(select(model).where(model.document_id == doc.id)):
            session.delete(row)
    for row in session.scalars(select(ReviewIssue).where(ReviewIssue.document_id == doc.id, ReviewIssue.status == "offen")):
        session.delete(row)
    session.flush()


def _get_or_create_offer(session: Session, offer_number: str) -> Offer:
    offer = session.scalars(select(Offer).where(Offer.offer_number == offer_number)).first()
    if offer is None:
        m = re.search(r"\.(\d{2})$", offer_number)
        offer = Offer(offer_number=offer_number, year=2000 + int(m.group(1)) if m else None)
        session.add(offer)
        session.flush()
    return offer


def _get_or_create_item(session: Session, offer: Offer, position: int) -> OfferItem:
    item = session.scalars(select(OfferItem).where(OfferItem.offer_id == offer.id, OfferItem.position_number == position)).first()
    if item is None:
        item = OfferItem(offer_id=offer.id, position_number=position)
        session.add(item)
        session.flush()
    return item


def _store_value(session: Session, doc: Document, ex: Extracted, *, profile_validated: bool, item: OfferItem | None = None,
                 calc: Calculation | None = None, target: str, field_name: str | None = None, value=None) -> ExtractionValue:
    name = field_name or ex.field_name
    val = ex.value if value is None else value
    rule_issues = validation.check_value(name, val, ex.group)
    status, msg = validation.decide_status(profile_validated=profile_validated, confidence=ex.confidence,
                                           has_issues=bool(ex.issues or rule_issues), method=ex.method)
    messages = [i.message for i in ex.issues] + [r.message for r in rule_issues]
    if ex.extra.get("formula_verified"):
        messages.append("Formel nachgerechnet: gespeicherter Wert konsistent")
    ev = ExtractionValue(
        document_id=doc.id, offer_item_id=item.id if item else None, calculation_id=calc.id if calc else None,
        target_entity=target, field_name=name, field_value=None if val is None else str(val),
        numeric_value=float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None,
        unit=ex.unit, source_sheet=ex.sheet, source_cell=ex.cell, source_label=ex.label, source_formula=ex.formula,
        cached_value=None if ex.cached is None else str(ex.cached), extraction_method=ex.method,
        confidence=ex.confidence, validation_status=status, validation_message="; ".join(filter(None, [msg] + messages)) or None,
    )
    session.add(ev)
    session.flush()
    for r in rule_issues:
        _issue(session, r.code or "regel", f"{doc.file_name}: {r.message}", "warnung", item=item, document=doc, value=ev)
    for iss in ex.issues:
        _issue(session, iss.code, f"{doc.file_name}: {iss.message}", iss.severity, item=item, document=doc, value=ev)
    return ev


def _apply_item_value(session: Session, offer: Offer, item: OfferItem, doc: Document, field: str, value, ev: ExtractionValue) -> None:
    if field == "material" and value is not None:
        norm, ok = normalize_material(value)
        item.material_raw = str(value)
        if not ok:
            ev.validation_status = ValStatus.PRUEFBEDARF
            _issue(session, "werkstoff_unbekannt", f"Werkstoff '{value}' konnte nicht vereinheitlicht werden ({doc.file_name})", "info", item=item, document=doc, value=ev)
        value = norm
    if field == "product_type" and value is not None:
        norm, ok = normalize_product_type(value)
        if not ok:
            _issue(session, "produkttyp_unbekannt", f"Produkttyp '{value}' ist nicht in der Typenliste ({doc.file_name})", "info", item=item, document=doc, value=ev)
        value = norm
    current = getattr(item, field)
    if current is not None and validation.values_conflict(current, value):
        ev.validation_status = ValStatus.PRUEFBEDARF
        _issue(session, "widerspruch", f"Pos. {item.position_number}: {field} widersprüchlich – bisher '{current}', {doc.file_name} liefert '{value}'",
               "warnung", item=item, document=doc, value=ev)
        return
    if current is None:
        setattr(item, field, value)


def _reset_from_old_versions(session: Session, doc: Document) -> None:
    """Neue Version einer Datei: Werte älterer Versionen derselben Datei werden ersetzt statt als Widerspruch gemeldet."""
    old_ids = list(session.scalars(select(Document.id).where(Document.file_path == doc.file_path, Document.id != doc.id)))
    if not old_ids:
        return
    for ev in session.scalars(select(ExtractionValue).where(ExtractionValue.document_id.in_(old_ids), ExtractionValue.target_entity == "offer_item",
                                                           ExtractionValue.offer_item_id.is_not(None))):
        item = session.get(OfferItem, ev.offer_item_id)
        if item is not None and hasattr(item, ev.field_name):
            setattr(item, ev.field_name, None)
            if ev.field_name == "material":
                item.material_raw = None
    for op in session.scalars(select(ManufacturingOperation).where(ManufacturingOperation.document_id.in_(old_ids))):
        session.delete(op)
    session.flush()


def _import_workbook(session: Session, offer: Offer, doc: Document, ex: WorkbookExtraction, kind: str) -> list[str]:
    notes: list[str] = []
    _reset_from_old_versions(session, doc)
    doc.profile_id = ex.profile_id
    if not ex.profile_validated:
        _issue(session, "profil_nicht_validiert", f"{doc.file_name}: Importprofil '{ex.profile_id}' ist noch nicht gegen Originaldateien validiert – Werte prüfen", "info", offer=offer, document=doc)
    for iss in ex.issues:
        if iss.field_name is None:
            _issue(session, iss.code, f"{doc.file_name}: {iss.message}", iss.severity, offer=offer, document=doc)

    # Kopf
    for name, e in ex.header.items():
        ev = _store_value(session, doc, e, profile_validated=ex.profile_validated, target="offer")
        if name == "offer_number":
            if e.value and offer.offer_number.lower() not in str(e.value).lower().replace(" ", ""):
                ev.validation_status = ValStatus.PRUEFBEDARF
                _issue(session, "angebotsnummer_abweichend", f"{doc.file_name}: Angebotsnummer in der Datei ('{e.value}') weicht von Ordner/Dateiname ({offer.offer_number}) ab", "warnung", offer=offer, document=doc, value=ev)
            continue
        if name in OFFER_FIELDS and e.value is not None:
            current = getattr(offer, name)
            if current is None:
                setattr(offer, name, e.value)
            elif validation.values_conflict(current, e.value):
                _issue(session, "widerspruch", f"Angebotskopf: {name} widersprüchlich – '{current}' vs. '{e.value}' ({doc.file_name})", "warnung", offer=offer, document=doc, value=ev)

    if len(ex.positions) > 1:
        notes.append(f"{len(ex.positions)} Positionen erkannt ({ex.position_mode})")

    rev_label, rev_index = detect_revision(doc.file_name)
    if not rev_label and "revision" in ex.header and ex.header["revision"].value:
        rev_label = str(ex.header["revision"].value)
        rev_index = int(rev_label) if rev_label.isdigit() else None

    for pos, fields in sorted(ex.positions.items()):
        item = _get_or_create_item(session, offer, pos)
        if kind == DocType.KALKULATION and doc.offer_item_id is None and len(ex.positions) == 1:
            doc.offer_item_id = item.id
        calc = None
        if kind == DocType.KALKULATION:
            calc = Calculation(offer_item_id=item.id, document_id=doc.id, revision=rev_label, revision_index=rev_index,
                               profile_id=ex.profile_id, currency=offer.currency, is_current=True)
            session.add(calc)
            session.flush()
        for name, e in fields.items():
            if e.group == "item":
                if name == "position_number":
                    continue
                if name == "design_temperature" and isinstance(e.value, tuple):
                    lo, hi = e.value
                    for fname, v in (("design_temperature_min", lo), ("design_temperature_max", hi)):
                        if v is not None:
                            ev = _store_value(session, doc, e, profile_validated=ex.profile_validated, item=item, target="offer_item", field_name=fname, value=v)
                            _apply_item_value(session, offer, item, doc, fname, v, ev)
                    continue
                ev = _store_value(session, doc, e, profile_validated=ex.profile_validated, item=item, target="offer_item")
                if e.value is not None:
                    _apply_item_value(session, offer, item, doc, name, e.value, ev)
            elif e.group == "calculation" and calc is not None:
                ev = _store_value(session, doc, e, profile_validated=ex.profile_validated, item=item, calc=calc, target="calculation")
                if e.value is not None:
                    setattr(calc, name, e.value)
        if calc is not None:
            _check_calculation(session, item, calc, doc)

        ops = ex.operations.get(pos, {})
        if ops:
            _store_operations(session, offer, item, doc, ops, ex.profile_validated)
    return notes


def _check_calculation(session: Session, item: OfferItem, calc: Calculation, doc: Document) -> None:
    if calc.offer_price is None:
        _issue(session, "preis_fehlt", f"Pos. {item.position_number}: Angebotspreis konnte in {doc.file_name} nicht gefunden werden", "warnung", item=item, document=doc)
    if calc.total_cost is not None and calc.offer_price is not None and calc.offer_price < calc.total_cost:
        _issue(session, "preis_unter_selbstkosten", f"Pos. {item.position_number}: Angebotspreis {calc.offer_price:,.2f} liegt unter den Selbstkosten {calc.total_cost:,.2f}", "warnung", item=item, document=doc)
    h, r, c = calc.manufacturing_hours, calc.manufacturing_hourly_rate, calc.manufacturing_cost
    if None not in (h, r, c) and abs(h * r - c) > max(1.0, 0.01 * c):
        _issue(session, "summe_abweichend", f"Pos. {item.position_number}: Fertigungskosten {c:,.2f} ≠ Stunden × Satz ({h:g} × {r:g} = {h * r:,.2f})", "info", item=item, document=doc)


def _store_operations(session: Session, offer: Offer, item: OfferItem, doc: Document, ops: dict[str, Extracted], profile_validated: bool) -> None:
    calc = item.current_calculation
    total_ex = ops.get("gesamt")
    op_sum = 0.0
    n = 0
    for key, e in ops.items():
        ev = _store_value(session, doc, e, profile_validated=profile_validated, item=item, calc=None, target="operation")
        session.add(ManufacturingOperation(
            calculation_id=calc.id if calc else None, offer_item_id=item.id, document_id=doc.id, operation_key=key,
            operation_name=e.display_name or key, planned_hours=e.value if isinstance(e.value, (int, float)) else None,
            source_reference=f"{doc.file_name} – {e.source_ref}",
        ))
        if key != "gesamt" and isinstance(e.value, (int, float)):
            op_sum += e.value
            n += 1
        _ = ev
    session.flush()
    total = total_ex.value if total_ex and isinstance(total_ex.value, (int, float)) else None
    if total is not None and n and abs(op_sum - total) > max(0.05, 0.01 * total):
        _issue(session, "summe_abweichend", f"Pos. {item.position_number}: Summe der Arbeitsgänge {op_sum:g} h ≠ Gesamtfertigungszeit {total:g} h ({doc.file_name})", "warnung", item=item, document=doc)
    effective = total if total is not None else (op_sum if n else None)
    if calc and calc.manufacturing_hours is not None and effective is not None and validation.values_conflict(calc.manufacturing_hours, effective):
        _issue(session, "widerspruch_fertigungszeit", f"Pos. {item.position_number}: Fertigungsstunden in Kalkulation ({calc.manufacturing_hours:g} h) ≠ Fertigungszeitkalkulation ({effective:g} h)", "warnung", item=item, document=doc)


def _norm_drawing(s: str | None) -> str:
    return re.sub(r"[\s_]", "", (s or "")).upper()


def _assign_drawing(session: Session, offer: Offer, doc: Document, pdf: PdfExtraction) -> tuple[OfferItem | None, str]:
    items = list(session.scalars(select(OfferItem).where(OfferItem.offer_id == offer.id).order_by(OfferItem.position_number)))
    candidates = {_norm_drawing(doc.file_name.rsplit(".", 1)[0])}
    m = DRAWING_NAME_RE.match(doc.file_name)
    if m:
        candidates.add(_norm_drawing(m.group(0)))
    dn_val = pdf.first("drawing_number")
    if dn_val:
        candidates.add(_norm_drawing(str(dn_val.value)))
    for it in items:
        if it.drawing_number and _norm_drawing(it.drawing_number) in candidates:
            return it, "Zeichnungsnummer stimmt mit Kalkulation überein"
        if it.drawing_number and any(c.startswith(_norm_drawing(it.drawing_number)) for c in candidates if len(_norm_drawing(it.drawing_number)) >= 6):
            return it, "Zeichnungsnummer (ohne Index) stimmt mit Kalkulation überein"
    hint = PRODUCT_PREFIXES.get(m.group("prefix").upper()) if m else None
    if not hint and pdf.first("product_type"):
        hint = str(pdf.first("product_type").value)
    if hint:
        matching = [it for it in items if it.product_type == hint]
        if len(matching) == 1:
            return matching[0], f"Zuordnung über Produkttyp '{hint}' (wahrscheinlich)"
    if len(items) == 1:
        return items[0], "Einzige Position des Angebots (wahrscheinlich)"
    return None, "Keine eindeutige Position gefunden"


def _import_drawing(session: Session, offer: Offer, doc: Document) -> list[str]:
    pdf = extract_pdf(doc.file_path)
    doc.page_count, doc.has_text = pdf.page_count, pdf.has_text
    for msg in pdf.issues:
        _issue(session, "pdf_ohne_text", f"{doc.file_name}: {msg}", "info", offer=offer, document=doc)
    item, reason = _assign_drawing(session, offer, doc, pdf)
    if item is None:
        _issue(session, "zeichnung_nicht_zugeordnet", f"{doc.file_name}: Zeichnung keiner Angebotsposition eindeutig zuordenbar", "warnung", offer=offer, document=doc)
    else:
        doc.offer_item_id = item.id
        if "wahrscheinlich" in reason:
            _issue(session, "zuordnung_unsicher", f"{doc.file_name} → Pos. {item.position_number}: {reason}", "info", item=item, document=doc)
    for v in pdf.values:
        ev = ExtractionValue(
            document_id=doc.id, offer_item_id=item.id if item else None, target_entity="offer_item", field_name=v.field_name,
            field_value=str(v.value), numeric_value=float(v.value) if isinstance(v.value, (int, float)) else None,
            source_page=v.page, source_text=v.snippet, extraction_method="pdf_text", confidence=v.confidence,
            validation_status=ValStatus.PRUEFBEDARF,
            validation_message="; ".join(filter(None, ["Wert aus Zeichnungstext – nicht als freigegebener Auslegungs-/Fertigungswert zu verstehen", v.note])),
        )
        session.add(ev)
        session.flush()
        if item is None or v.field_name not in PDF_CROSSCHECK:
            continue
        current = getattr(item, v.field_name)
        if current is None:
            setattr(item, v.field_name, v.value)
            ev.validation_message += "; in Position übernommen (keine Angabe in Kalkulation)"
        elif validation.values_conflict(current, v.value):
            _issue(session, "widerspruch", f"Pos. {item.position_number}: {v.field_name} in Zeichnung {doc.file_name} ('{v.value}', S. {v.page}) ≠ Kalkulation ('{current}')",
                   "warnung", item=item, document=doc, value=ev)
        else:
            ev.validation_message += "; bestätigt durch Kalkulation"
            confirmed = session.scalars(select(ExtractionValue).where(
                ExtractionValue.offer_item_id == item.id, ExtractionValue.field_name == v.field_name,
                ExtractionValue.extraction_method.like("profil%"), ExtractionValue.validation_status == ValStatus.VERIFIZIERT)).first()
            if confirmed:
                ev.validation_status = ValStatus.VERIFIZIERT
    return [reason]


def import_offer_group(session: Session, offer_number: str, docs: list[Document], profiles: dict[str, ImportProfile],
                       force: bool, reporter: Reporter, on_file: Callable[[str], None]) -> dict:
    """Importiert alle Dokumente eines Angebots. Der Aufrufer committet oder rollt zurück."""
    stats = {"imported": 0, "failed": 0, "log": []}
    offer = _get_or_create_offer(session, offer_number)
    order = {DocType.KALKULATION: 0, DocType.FERTIGUNGSZEIT: 1, DocType.ZEICHNUNG: 2}
    docs = sorted(docs, key=lambda d: (order.get(d.document_type, 9), d.modified_at or datetime.min, d.file_path))
    kalk_docs: list[Document] = []
    for doc in docs:
        reporter.check_cancel()
        on_file(doc.file_path)
        if force:
            _clear_document_data(session, doc)
        doc.offer_id = offer.id
        try:
            notes: list[str] = []
            if doc.document_type in (DocType.KALKULATION, DocType.FERTIGUNGSZEIT) and doc.extension in (".xlsx", ".xlsm"):
                with session.begin_nested():
                    ex = extract_workbook(doc.file_path, profiles, doc.document_type)
                    notes = _import_workbook(session, offer, doc, ex, doc.document_type)
                if doc.document_type == DocType.KALKULATION:
                    kalk_docs.append(doc)
            elif doc.document_type == DocType.ZEICHNUNG and doc.extension == ".pdf":
                with session.begin_nested():
                    notes = _import_drawing(session, offer, doc)
            else:
                notes = ["Dokument dem Angebot zugeordnet (keine Datenextraktion)"]
            if doc.classification_status in (ClassStatus.MANUELL,):
                _issue(session, "klassifizierung_unsicher", f"{doc.file_name}: Dokumenttyp unsicher ({doc.classification_rule})", "info", offer=offer, document=doc)
            doc.import_status, doc.imported_at = ImportStatus.IMPORTIERT, datetime.now().replace(microsecond=0)
            doc.import_message = "; ".join(notes) or None
            stats["imported"] += 1
            stats["log"].append((doc.id, doc.file_path, "import", "importiert", doc.import_message))
        except (ExcelReadError, ProfileError, ValueError, RuntimeError, OSError) as exc:
            kind = getattr(exc, "kind", "fehler")
            doc.import_status = ImportStatus.FEHLER
            doc.import_message = f"Nicht importierbar ({kind}): {exc}"
            _issue(session, "nicht_importierbar", f"{doc.file_name}: {doc.import_message}", "fehler", offer=offer, document=doc)
            stats["failed"] += 1
            stats["log"].append((doc.id, doc.file_path, "import", "fehler", doc.import_message))

    _update_current_calculations(session, offer)
    _update_validation_status(session, offer)
    return stats


def _update_current_calculations(session: Session, offer: Offer) -> None:
    session.flush()
    for item in session.scalars(select(OfferItem).where(OfferItem.offer_id == offer.id)):
        calcs = list(session.scalars(select(Calculation).join(Document, Calculation.document_id == Document.id, isouter=True)
                                     .where(Calculation.offer_item_id == item.id)))
        if not calcs:
            continue

        def key(c: Calculation):
            d = c.document
            return (1 if (d is None or d.is_current) else 0, c.revision_index or 0, d.modified_at if d and d.modified_at else datetime.min, c.id)

        calcs.sort(key=key)
        for c in calcs:
            c.is_current = False
        calcs[-1].is_current = True
        distinct_paths = {c.document.file_path for c in calcs if c.document and c.document.is_current}
        if len(distinct_paths) > 1:
            _issue(session, "mehrere_versionen", f"Pos. {item.position_number}: mehrere mögliche Kalkulationsversionen ({len(distinct_paths)} Dateien) – aktuell gewählt: {calcs[-1].document.file_name if calcs[-1].document else '?'}",
                   "warnung", item=item)
        for op in session.scalars(select(ManufacturingOperation).where(ManufacturingOperation.offer_item_id == item.id, ManufacturingOperation.calculation_id.is_(None))):
            op.calculation_id = calcs[-1].id


def _update_validation_status(session: Session, offer: Offer) -> None:
    session.flush()
    any_open = False
    for item in session.scalars(select(OfferItem).where(OfferItem.offer_id == offer.id)):
        open_issues = session.scalars(select(ReviewIssue.id).where(ReviewIssue.offer_item_id == item.id, ReviewIssue.status == "offen")).first()
        vals = list(session.scalars(select(ExtractionValue.validation_status).where(ExtractionValue.offer_item_id == item.id, ExtractionValue.target_entity == "offer_item")))
        item.validation_status = ValStatus.VERIFIZIERT if vals and not open_issues and all(v == ValStatus.VERIFIZIERT for v in vals) else ValStatus.PRUEFBEDARF
        for calc in item.calculations:
            cvals = list(session.scalars(select(ExtractionValue.validation_status).where(ExtractionValue.calculation_id == calc.id)))
            calc.validation_status = ValStatus.VERIFIZIERT if cvals and not open_issues and all(v == ValStatus.VERIFIZIERT for v in cvals) else ValStatus.PRUEFBEDARF
        any_open = any_open or bool(open_issues) or item.validation_status != ValStatus.VERIFIZIERT
    offer_open = session.scalars(select(ReviewIssue.id).where(ReviewIssue.offer_id == offer.id, ReviewIssue.status == "offen")).first()
    offer.validation_status = ValStatus.PRUEFBEDARF if (any_open or offer_open) else ValStatus.VERIFIZIERT
