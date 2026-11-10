"""Scan- und Importablauf (idempotent, transaktional je Angebot).

1. Verzeichnis scannen, Dateien inventarisieren, SHA-256 berechnen
2. Bekannte Dateien erkennen (Pfad + Größe + Änderungsdatum bzw. Hash)
3. Dokumenttyp bestimmen, Angebotsnummer und Revision erkennen
4. Import: je Angebotsnummer eine Transaktion – Excel-Profil wählen, Werte extrahieren,
   Positionen zuordnen, Plausibilität prüfen, speichern, Prüfbedarf erzeugen.
"""
from __future__ import annotations

import hashlib
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
from app.services.document_classifier import CLASSIFIER_VERSION, DRAWING_NAME_RE, PRODUCT_PREFIXES, classify_file
from app.services.excel_importer import Extracted, ImportProfile, Issue, ProfileError, WorkbookExtraction, extract_workbook, load_profiles
from app.services.excel_reader import ExcelReadError
from app.services.folder_scanner import SUPPORTED_EXTENSIONS, ScanResult, scan_directory, sha256_file
from app.services.normalize import normalize_material, normalize_product_type
from app.services.offer_numbers import detect_offer_number, detect_revision, find_offer_numbers
from app.services.pdf_importer import PdfExtraction, extract_pdf

log = logging.getLogger(__name__)

IMPORTABLE_TYPES = {DocType.KALKULATION, DocType.FERTIGUNGSZEIT, DocType.ZEICHNUNG, DocType.KUNDENANGEBOT,
                    DocType.STUECKLISTE, DocType.LIEFERANTENANGEBOT}
ITEM_FIELDS = ["position_number", "product_type", "description", "drawing_number", "dn", "design_pressure",
               "design_temperature_min", "design_temperature_max", "material", "diameter", "wall_thickness", "height",
               "volume", "filter_elements", "filter_area", "design_code", "weight", "quantity"]
OFFER_FIELDS = ["customer", "project_name", "offer_date", "country", "industry", "status", "currency", "revision"]
PDF_CROSSCHECK = ["drawing_number", "product_type", "dn", "design_pressure", "design_temperature_min", "design_temperature_max",
                  "material", "design_code", "filter_area", "filter_elements"]


TEMPLATE_NAME_RE = re.compile(r"(?i)^kalkulation[\s_-]*r\d{2}(?!\d)")


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
    reclassified: int = 0
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
    reporter.log(None, None, None, "scan", "hinweis",
                 f"Zusammenfassung: {summary.files_found} Dateien, {summary.new_documents} neu, {summary.changed_documents} geändert, "
                 f"{summary.unchanged} unverändert, davon {summary.reclassified} neu bewertet, {summary.duplicates} Duplikate, "
                 f"{len(summary.problems)} Probleme")
    reporter.commit_point()
    return summary


def _meta_hash(path: str, size: int, modified_at) -> str:
    """Kennung für nicht auswertbare Dateitypen (Mails, Word, CAD …): kein Lesen des Inhalts."""
    return hashlib.sha256(f"meta|{path}|{size}|{modified_at}".encode("utf-8")).hexdigest()


RECLASSIFY_STATUSES = {ImportStatus.NEU, ImportStatus.IGNORIERT, ImportStatus.FEHLER, ImportStatus.NICHT_UNTERSTUETZT}


def _classify_into(session: Session, doc: Document, summary: ScanSummary) -> None:
    """Klassifizierung, Angebotsnummer, Revision und Duplikatprüfung (setzt auch den Importstatus)."""
    cls = classify_file(doc.file_path)
    offer = detect_offer_number(doc.file_path)
    doc.document_type, doc.classification_status = cls.document_type, cls.status
    doc.classification_rule, doc.classification_confidence = cls.rule_text, cls.confidence
    doc.detected_offer_number = offer.number if offer else None
    doc.offer_number_source = offer.source if offer else None
    doc.revision_label = detect_revision(doc.file_name)[0]
    doc.page_count, doc.has_text = cls.page_count, cls.has_text
    doc.classifier_version = CLASSIFIER_VERSION
    doc.import_status, doc.import_message, doc.duplicate_of_id = ImportStatus.NEU, None, None
    dup = None
    if doc.content_hashed:
        # Duplikat nur innerhalb desselben Angebots: dieselbe Datei in einem anderen Angebotsordner
        # (z. B. kopierte Kalkulation) gehört zu jenem Angebot.
        q = select(Document).where(Document.file_hash == doc.file_hash, Document.file_path != doc.file_path,
                                   Document.is_current.is_(True), Document.duplicate_of_id.is_(None))
        q = q.where(Document.detected_offer_number == doc.detected_offer_number) if doc.detected_offer_number else q.where(Document.detected_offer_number.is_(None))
        if doc.id:
            q = q.where(Document.id < doc.id)
        dup = session.scalars(q.order_by(Document.id)).first()
    if cls.status == ClassStatus.NICHT_UNTERSTUETZT:
        doc.import_status, doc.import_message = ImportStatus.NICHT_UNTERSTUETZT, cls.rule_text
    elif dup:
        doc.import_status, doc.import_message, doc.duplicate_of_id = ImportStatus.IGNORIERT, f"Inhaltsgleiches Duplikat von {dup.file_path}", dup.id
        summary.duplicates += 1
    elif cls.document_type not in IMPORTABLE_TYPES:
        doc.import_status, doc.import_message = ImportStatus.IGNORIERT, "Dokumenttyp ohne Datenimport"


def _inventory_file(session: Session, f, found: ScanResult, summary: ScanSummary, reporter: Reporter) -> None:
    current = session.scalars(select(Document).where(Document.file_path == f.path, Document.is_current.is_(True))).first()
    now = datetime.now().replace(microsecond=0)
    if current and current.file_size == f.size and current.modified_at == f.modified_at:
        current.scanned_at = now
        summary.unchanged += 1
        if current.classifier_version != CLASSIFIER_VERSION and current.import_status in RECLASSIFY_STATUSES:
            _classify_into(session, current, summary)  # Regeln geändert -> neu bewerten (Datei unverändert)
            summary.reclassified += 1
        return
    if f.path.lower() in found.locked_hints:
        reporter.log(None, None, f.path, "scan", "hinweis", "Excel-Sperrdatei gefunden – Datei ist evtl. geöffnet; es wird eine Kopie gelesen")
    content_hashed = f.extension in SUPPORTED_EXTENSIONS
    if content_hashed:
        try:
            file_hash = sha256_file(f.path)
        except PermissionError as exc:
            raise PermissionError(f"Datei gesperrt oder keine Leseberechtigung ({exc.strerror or exc})") from exc
        except FileNotFoundError as exc:
            raise FileNotFoundError("Datei beim Lesen nicht mehr vorhanden (während des Scans verschoben/umbenannt?) "
                                    "oder Name von Windows nicht auflösbar") from exc
    else:
        file_hash = _meta_hash(f.path, f.size, f.modified_at)

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
    doc = Document(
        file_path=f.path, relative_path=f.relative_path, file_name=f.name, extension=f.extension, file_hash=file_hash,
        content_hashed=content_hashed, file_size=f.size, modified_at=f.modified_at, scanned_at=now,
        version=(max((d.version for d in versions), default=0) + 1), is_current=True,
    )
    session.add(doc)
    session.flush()
    _classify_into(session, doc, summary)
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
    from app.models import MaterialLine, ServiceCalc, TradeItem
    for model in (ExtractionValue, MaterialLine, TradeItem, ServiceCalc, ManufacturingOperation, Calculation):
        for row in session.scalars(select(model).where(model.document_id == doc.id)):
            session.delete(row)
    for row in session.scalars(select(ReviewIssue).where(ReviewIssue.document_id == doc.id, ReviewIssue.status == "offen")):
        session.delete(row)
    session.flush()


def _reset_offer(session: Session, offer: Offer) -> None:
    """Erzwungener Neuimport: alle aus Dateien abgeleiteten Daten des Angebots verwerfen und neu aufbauen.

    Sonst blieben Werte eines früheren (falschen) Profils in Positionen und Angebotskopf stehen und die
    korrekten neuen Werte würden nur als Widerspruch gemeldet. Das Prüfprotokoll (Korrekturen) bleibt erhalten,
    ebenso der manuell gesetzte Angebotsstatus.
    """
    doc_ids = list(session.scalars(select(Document.id).where(Document.offer_id == offer.id)))
    if doc_ids:
        for row in session.scalars(select(ExtractionValue).where(ExtractionValue.document_id.in_(doc_ids))):
            session.delete(row)
    for row in session.scalars(select(ReviewIssue).where(ReviewIssue.offer_id == offer.id, ReviewIssue.status == "offen")):
        session.delete(row)
    for d in session.scalars(select(Document).where(Document.offer_id == offer.id)):
        d.offer_item_id = None
    from app.models import MaterialLine, ServiceCalc, TradeItem
    for model in (TradeItem, ServiceCalc):
        for row in session.scalars(select(model).where(model.offer_id == offer.id)):
            session.delete(row)
    item_ids = list(session.scalars(select(OfferItem.id).where(OfferItem.offer_id == offer.id)))
    if item_ids:
        for row in session.scalars(select(MaterialLine).where(MaterialLine.offer_item_id.in_(item_ids))):
            session.delete(row)
        session.flush()
    for item in session.scalars(select(OfferItem).where(OfferItem.offer_id == offer.id)).all():
        session.delete(item)  # mit Kalkulationen und Arbeitsgängen
    for f in OFFER_FIELDS:
        if f != "status":
            setattr(offer, f, None)
    offer.validation_status = ValStatus.PRUEFBEDARF
    session.flush()
    session.expire(offer, ["items"])


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


def _nothing_usable(ex: WorkbookExtraction) -> bool:
    """Unvalidiertes (generisches) Profil ohne einen einzigen Kalkulationswert und ohne Handelsware:
    Aufbau unbekannt – keine Schein-Positionen anlegen."""
    if ex.profile_validated or ex.trade_lines or ex.service_lines:
        return False
    for fields in ex.positions.values():
        if any(e.group == "calculation" and e.value is not None for e in fields.values()):
            return False
    return not any(ops for ops in ex.operations.values())


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
            found = [n for n, _ in find_offer_numbers(str(e.value or ""))]
            if e.value and offer.offer_number not in found:
                ev.validation_status = ValStatus.PRUEFBEDARF
                _issue(session, "angebotsnummer_abweichend", f"{doc.file_name}: Angebotsnummer in der Datei ('{e.value}') weicht von Ordner/Dateiname ({offer.offer_number}) ab", "warnung", offer=offer, document=doc, value=ev)
            continue
        if name in OFFER_FIELDS and e.value is not None:
            current = getattr(offer, name)
            if current is None:
                setattr(offer, name, e.value)
            elif validation.values_conflict(current, e.value):
                _issue(session, "widerspruch", f"Angebotskopf: {name} widersprüchlich – '{current}' vs. '{e.value}' ({doc.file_name})", "warnung", offer=offer, document=doc, value=ev)

    if ex.trade_lines:
        _store_trade_items(session, offer, doc, ex.trade_lines)
    if ex.service_lines:
        _store_service(session, offer, doc, ex)
    for name, value in ex.constants.items():
        if name in OFFER_FIELDS and getattr(offer, name) is None:
            setattr(offer, name, value)  # Vorgabe der Vorlage (z. B. Währung), im Profil dokumentiert
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
        if calc is not None and ex.material_lines.get(pos):
            _store_material_lines(session, item, calc, doc, ex.material_lines[pos])
        if calc is not None:
            for target, source in ex.fallbacks.items():
                src = fields.get(source)
                if getattr(calc, target, None) is None and src is not None and src.value is not None:
                    setattr(calc, target, src.value)
                    ev = _store_value(session, doc, src, profile_validated=False, item=item, calc=calc, target="calculation", field_name=target)
                    ev.extraction_method = "profil_ersatz"
                    ev.validation_status = ValStatus.PRUEFBEDARF
                    ev.validation_message = f"Kein Wert für '{target}' in der Datei – '{source}' übernommen"
                    _issue(session, "ersatzwert", f"Pos. {item.position_number}: {target} fehlt in {doc.file_name} – Wert aus {source} ({src.source_ref}) übernommen",
                           "info", item=item, document=doc, value=ev)
            _check_calculation(session, item, calc, doc)

        ops = ex.operations.get(pos, {})
        if ops:
            _store_operations(session, offer, item, doc, ops, ex.profile_validated)
    return notes


def _to_float(v) -> float | None:
    from app.services.excel_reader import parse_number
    return parse_number(v)


def _store_material_lines(session: Session, item: OfferItem, calc: Calculation, doc: Document, rows: list[dict]) -> None:
    from app.models import MaterialLine
    from app.services.normalize import estimate_weight, material_category, normalize_material, parse_dimensions

    total = 0.0
    n_total = 0
    for r in rows:
        if all(_to_float(r.get(k)) is None for k in ("quantity", "unit_price", "cost", "total_cost", "apz_cost")):
            continue  # reine Textzeile (Hinweis, Zwischenüberschrift) – kein Bauteil
        desc = str(r.get("description") or "").strip()[:255]
        form = str(r["form"]).strip()[:128] if r.get("form") not in (None, "") else None
        dims_raw = str(r["dimensions"]).strip()[:128] if r.get("dimensions") not in (None, "") else None
        mat_raw = str(r["material"]).strip()[:128] if r.get("material") not in (None, "") else None
        mat_norm, _ = normalize_material(mat_raw)
        category = material_category(desc, form)
        dims = parse_dimensions(dims_raw)
        length = _to_float(r.get("length"))
        length = length if length else None
        # "Länge" ist in der Vorlage ein Multiplikator (Preis × Länge): €/m bei Rohr/Rundmaterial oder Länge ≠ 1,
        # eine 1 bei Blechen/Teilen ist neutral -> Preis je Stück
        if length == 1 and category not in ("Rohr", "Rundmaterial"):
            length = None
        unit_price = _to_float(r.get("unit_price"))
        wall = dims.get("second") if category in ("Rohr", "Blech/Flachstahl", "Boden") else None
        if category == "Blech/Flachstahl" and dims.get("thickness"):
            wall = dims["thickness"]
        w, w_unit = estimate_weight(category, dims, mat_norm, length)
        line_total = _to_float(r.get("total_cost"))
        if line_total is not None:
            total += line_total
            n_total += 1
        session.add(MaterialLine(
            calculation_id=calc.id, offer_item_id=item.id, document_id=doc.id, source_sheet=r["_sheet"], source_row=r["_row"],
            line_no=None if r.get("line_no") is None else str(r["line_no"])[:16], description=desc or None, form=form,
            dimensions=dims_raw, standard=None if r.get("standard") in (None, "") else str(r["standard"])[:128],
            material_raw=mat_raw, material=mat_norm, category=category, quantity=_to_float(r.get("quantity")),
            length_m=length, unit_price=unit_price, price_unit="€/m" if length else "€/Stk",
            apz_cost=_to_float(r.get("apz_cost")), cost=_to_float(r.get("cost")),
            manufacturing_cost=_to_float(r.get("manufacturing_cost")), total_cost=line_total,
            dn=dims.get("dn"), pn=dims.get("pn"), outer_diameter=dims.get("outer_diameter"), wall_thickness=wall,
            plate_length=dims.get("length"), plate_width=dims.get("width"), thread=dims.get("thread"),
            est_weight=w, est_weight_unit=w_unit, price_per_kg=(unit_price / w) if (unit_price and w) else None,
        ))
        if r.get("_missing_cache"):
            _issue(session, "formel_ohne_cache", f"{doc.file_name}: Materialauszug Zeile {r['_row']} ({desc}): Formel ohne gespeicherten Wert ({', '.join(r['_missing_cache'])})",
                   "warnung", item=item, document=doc)
    session.flush()
    expected = calc.material_cost * (item.quantity or 1) if calc.material_cost is not None else None
    if expected is not None and n_total and abs(total - expected) > max(1.0, 0.01 * expected):
        _issue(session, "summe_abweichend", f"Pos. {item.position_number}: Summe Materialauszug {total:,.2f} € ≠ Material Gesamtkosten {expected:,.2f} € ({doc.file_name})",
               "warnung", item=item, document=doc)


def _store_trade_items(session: Session, offer: Offer, doc: Document, rows: list[dict]) -> None:
    from app.models import TradeItem
    from app.services.normalize import material_category, parse_dimensions

    txt = lambda v, n: (str(v).strip()[:n] or None) if v not in (None, "") else None  # noqa: E731
    assembly: dict | None = None
    for r in rows:
        nums = {k: _to_float(r.get(k)) for k in ("quantity", "purchase_price", "purchase_total", "material_overhead",
                                                 "sales_overhead", "risk_profit", "sales_total", "sales_price", "factor")}
        if all(nums[k] is None for k in ("quantity", "purchase_price", "purchase_total", "sales_total", "sales_price")):
            continue
        desc = str(r.get("description") or "").strip()
        dims = txt(r.get("dimensions"), 128)
        note = txt(r.get("source_note"), 500)
        qty = nums["quantity"]
        ek = nums["purchase_price"]
        ek_total = nums["purchase_total"] if nums["purchase_total"] is not None else (ek * qty if (ek is not None and qty) else None)
        if ek is None and ek_total is not None and qty:
            ek = ek_total / qty
        merged = r.get("_merged") or {}
        vk, vk_total = nums["sales_price"], nums["sales_total"]
        if any(k in merged for k in ("sales_price", "sales_total")):
            # Verkaufspreis gilt für mehrere Zeilen (Baugruppe): nicht der Einzelzeile zuordnen
            if assembly is None:
                assembly = {"vk": vk, "vk_total": vk_total, "qty": qty, "ek_total": 0.0, "row": r["_row"], "sheet": r["_sheet"],
                            "title": r.get("_title") or desc, "factor": nums["factor"]}
            vk = vk_total = None
        if assembly is not None and ek_total is not None:
            assembly["ek_total"] += ek_total
        if vk is None and vk_total is not None and qty:
            vk = vk_total / qty
        if vk_total is None and vk is not None and qty:
            vk_total = vk * qty
        if vk_total is None and ek_total is not None and None not in (nums["material_overhead"], nums["sales_overhead"], nums["risk_profit"]):
            vk_total = ek_total + nums["material_overhead"] + nums["sales_overhead"] + nums["risk_profit"]
            vk = vk_total / qty if qty else None
        factor = (vk / ek) if (vk and ek) else nums["factor"]
        session.add(TradeItem(
            offer_id=offer.id, document_id=doc.id, source_sheet=r["_sheet"], source_row=r["_row"],
            line_no=txt(r.get("line_no"), 16), description=desc[:500] or None,
            category=material_category(desc, None), outer_diameter=parse_dimensions(dims or desc).get("outer_diameter"),
            quantity=qty, purchase_price=ek, purchase_total=ek_total,
            material_overhead=nums["material_overhead"], sales_overhead=nums["sales_overhead"], risk_profit=nums["risk_profit"],
            sales_total=vk_total, sales_price=vk, markup_factor=factor, source_note=note,
            is_estimate=bool(note and re.search(r"(?i)sch(ä|ae)tz|annahme|anfragen", note)),
            dimensions=dims, material=txt(r.get("material"), 128), lead_time=txt(r.get("lead_time"), 64),
            drawing_ref=txt(r.get("drawing_ref"), 128), last_order=txt(r.get("last_order"), 64),
        ))
    if assembly is not None and (assembly["vk"] is not None or assembly["vk_total"] is not None):
        qty = assembly["qty"] or 1
        vk = assembly["vk"] if assembly["vk"] is not None else assembly["vk_total"] / qty
        ek_unit = assembly["ek_total"] / qty if assembly["ek_total"] else None
        session.add(TradeItem(
            offer_id=offer.id, document_id=doc.id, source_sheet=assembly["sheet"], source_row=assembly["row"],
            description=f"Baugruppe: {assembly['title']}"[:500], category="Baugruppe", quantity=qty,
            purchase_price=ek_unit, purchase_total=assembly["ek_total"] or None, sales_price=vk, sales_total=vk * qty,
            markup_factor=(vk / ek_unit) if (vk and ek_unit) else assembly["factor"], is_assembly=True,
            source_note="Verkaufspreis gilt für die Baugruppe (verbundene Zellen); EK = Summe der Einzelteile",
        ))
    session.flush()


SERVICE_KINDS = [
    (r"experte|monteur|techniker|ingenieur|meister|helfer|stunde|personal|arbeitszeit", "personal"),
    (r"unterkunft|hotel|(ü|ue)bernachtung|nacht", "unterkunft"),
    (r"pro km|kilometer|\bkm\b|fahrkosten", "km"),
    (r"reise|flug|bahn|anreise|fahrt", "reise"),
    (r"pro tag|tagessatz|spesen|verpflegung|ausl(ö|oe)se", "tag"),
]


def service_kind(description: str | None) -> str:
    t = (description or "").lower()
    return next((k for rx, k in SERVICE_KINDS if re.search(rx, t)), "sonstiges")


def _store_service(session: Session, offer: Offer, doc: Document, ex: WorkbookExtraction) -> None:
    from app.models import ServiceCalc, ServiceLine

    svc = ServiceCalc(offer_id=offer.id, document_id=doc.id,
                      title=ex.header["project_name"].value if "project_name" in ex.header else None,
                      total_cost=ex.service["total_cost"].value if "total_cost" in ex.service else None,
                      offer_price=ex.service["offer_price"].value if "offer_price" in ex.service else None)
    session.add(svc)
    session.flush()
    total = 0.0
    abroad = False
    for r in ex.service_lines:
        nums = {k: _to_float(r.get(k)) for k in ("rate_domestic", "rate_abroad", "quantity", "total")}
        desc = str(r.get("description") or "").strip()
        if not desc or all(v is None for v in nums.values()):
            continue
        q, t = nums["quantity"], nums["total"]
        if q and t is not None and nums["rate_abroad"] is not None:
            # Ausland nur, wenn der Betrag zum Auslandssatz passt (nicht zum Inlandssatz)
            fits_abroad = abs(nums["rate_abroad"] * q - t) < 0.01
            fits_domestic = nums["rate_domestic"] is not None and abs(nums["rate_domestic"] * q - t) < 0.01
            abroad = abroad or (fits_abroad and not fits_domestic)
        session.add(ServiceLine(service_calc_id=svc.id, source_sheet=r["_sheet"], source_row=r["_row"],
                                line_no=None if r.get("line_no") is None else str(r["line_no"])[:16], description=desc[:255],
                                kind=service_kind(desc), rate_domestic=nums["rate_domestic"], rate_abroad=nums["rate_abroad"],
                                quantity=nums["quantity"], total=nums["total"]))
        total += nums["total"] or 0.0
    svc.abroad = abroad
    if svc.total_cost is None:
        svc.total_cost = total
    elif abs(svc.total_cost - total) > 0.5:
        _issue(session, "summe_abweichend", f"{doc.file_name}: Summe der Servicezeilen {total:,.2f} € ≠ Gesamt {svc.total_cost:,.2f} €", "warnung", offer=offer, document=doc)
    if svc.offer_price is None:
        _issue(session, "preis_fehlt", f"{doc.file_name}: kein Angebotspreis unter „Gesamt“ gefunden – Summe {svc.total_cost:,.2f} € verwenden?", "info", offer=offer, document=doc)
    session.flush()


def _check_calculation(session: Session, item: OfferItem, calc: Calculation, doc: Document) -> None:
    if item.quantity is not None and item.quantity > 1:
        _issue(session, "stueckzahl_groesser_1", f"Pos. {item.position_number}: Stückzahl {item.quantity} – Kostenwerte gelten je Gehäuse; "
               f"prüfen, ob der Angebotspreis je Stück oder gesamt gilt ({doc.file_name})", "info", item=item, document=doc)
    if calc.offer_price is None:
        _issue(session, "preis_fehlt", f"Pos. {item.position_number}: Angebotspreis konnte in {doc.file_name} nicht gefunden werden", "warnung", item=item, document=doc)
    if calc.total_cost is not None and calc.offer_price is not None and calc.offer_price < calc.total_cost:
        _issue(session, "preis_unter_selbstkosten", f"Pos. {item.position_number}: Angebotspreis {calc.offer_price:,.2f} liegt unter den Selbstkosten {calc.total_cost:,.2f}", "warnung", item=item, document=doc)
    h, r, c = calc.manufacturing_hours, calc.manufacturing_hourly_rate, calc.manufacturing_cost
    if None not in (h, r, c) and abs(h * r - c) > max(1.0, 0.01 * c):
        _issue(session, "summe_abweichend", f"Pos. {item.position_number}: Fertigungskosten {c:,.2f} ≠ Stunden × Satz ({h:g} × {r:g} = {h * r:,.2f})", "info", item=item, document=doc)


SPECIAL_OPERATIONS = {"grundzeit", "verteilzeit", "gesamt", "gesamt_gerundet"}


def _store_operations(session: Session, offer: Offer, item: OfferItem, doc: Document, ops: dict[str, Extracted], profile_validated: bool) -> None:
    calc = item.current_calculation
    hours = {k: e.value for k, e in ops.items() if isinstance(e.value, (int, float))}
    steps = {k: v for k, v in hours.items() if k not in SPECIAL_OPERATIONS}
    for key, e in ops.items():
        _store_value(session, doc, e, profile_validated=profile_validated, item=item, calc=None, target="operation")
        session.add(ManufacturingOperation(
            calculation_id=calc.id if calc else None, offer_item_id=item.id, document_id=doc.id, operation_key=key[:64],
            operation_name=(e.display_name or key)[:128], planned_hours=hours.get(key),
            source_reference=f"{doc.file_name} – {e.source_ref}",
        ))
    session.flush()
    step_sum = sum(steps.values()) if steps else None
    if step_sum is not None:
        if "grundzeit" in hours:
            check, base, label = step_sum, hours["grundzeit"], "Grundzeit"
        elif "gesamt" in hours:
            check, base, label = step_sum + hours.get("verteilzeit", 0.0), hours["gesamt"], "Gesamtfertigungszeit"
        else:
            check = base = None
        if base is not None and abs(check - base) > max(0.05, 0.01 * base):
            _issue(session, "summe_abweichend", f"Pos. {item.position_number}: Summe der Arbeitsgänge {check:g} h ≠ {label} {base:g} h ({doc.file_name})", "warnung", item=item, document=doc)
    # Für den Vergleich mit der Kalkulation: gerundeter Wert (wird dort übernommen), sonst Gesamt, sonst Summe
    effective = hours.get("gesamt_gerundet", hours.get("gesamt", step_sum))
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
    if force:
        _reset_offer(session, offer)
    order = {DocType.KALKULATION: 0, DocType.FERTIGUNGSZEIT: 1, DocType.ZEICHNUNG: 2}
    docs = sorted(docs, key=lambda d: (order.get(d.document_type, 9), d.modified_at or datetime.min, d.file_path))
    kalk_docs: list[Document] = []
    # Unausgefüllte Vorlagenkopien ("Kalkulation R06_01-2026.xlsx") nicht als Kalkulation werten, wenn das
    # Angebot eine Kalkulation mit Angebotsnummer im Dateinamen besitzt.
    named = [d for d in docs if d.document_type == DocType.KALKULATION and find_offer_numbers(d.file_name)]
    if named:
        for d in [d for d in docs if d.document_type == DocType.KALKULATION and d not in named and TEMPLATE_NAME_RE.search(d.file_name)]:
            docs.remove(d)
            d.offer_id = offer.id
            d.import_status = ImportStatus.IGNORIERT
            d.import_message = f"Vermutlich Kalkulationsvorlage (ohne Angebotsnummer im Namen); maßgeblich: {named[0].file_name}"
            _issue(session, "vorlage_ignoriert", f"{d.file_name}: als Vorlagenkopie nicht importiert – bei Bedarf umbenennen oder mit „Alle erneut importieren“ prüfen", "info", offer=offer, document=d)
            stats["log"].append((d.id, d.file_path, "import", "ignoriert", d.import_message))
    if not named:
        for d in docs:
            if d.document_type == DocType.KALKULATION and TEMPLATE_NAME_RE.search(d.file_name):
                _issue(session, "vorlage_pruefen", f"{d.file_name}: Dateiname wie Kalkulationsvorlage und keine Kalkulation mit Angebotsnummer im Angebot – prüfen, ob ausgefüllt",
                       "info", offer=offer, document=d)
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
                    if _nothing_usable(ex):
                        doc.import_status = ImportStatus.IGNORIERT
                        doc.import_message = f"Vorlage nicht erkannt – keine Kalkulationswerte gefunden (Profil {ex.profile_id})"
                        doc.profile_id = ex.profile_id
                        _issue(session, "vorlage_unbekannt", f"{doc.file_name}: Aufbau nicht erkannt – nicht importiert. "
                               "Bei Bedarf Struktur-Export an den Support (neue Vorlage).", "info", offer=offer, document=doc)
                        stats["log"].append((doc.id, doc.file_path, "import", "ignoriert", doc.import_message))
                        continue
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
        open_issues = session.scalars(select(ReviewIssue.id).where(ReviewIssue.offer_item_id == item.id, ReviewIssue.status == "offen",
                                                                    ReviewIssue.severity != "info")).first()
        vals = list(session.scalars(select(ExtractionValue.validation_status).where(ExtractionValue.offer_item_id == item.id, ExtractionValue.target_entity == "offer_item")))
        item.validation_status = ValStatus.VERIFIZIERT if vals and not open_issues and all(v == ValStatus.VERIFIZIERT for v in vals) else ValStatus.PRUEFBEDARF
        for calc in item.calculations:
            cvals = list(session.scalars(select(ExtractionValue.validation_status).where(ExtractionValue.calculation_id == calc.id)))
            calc.validation_status = ValStatus.VERIFIZIERT if cvals and not open_issues and all(v == ValStatus.VERIFIZIERT for v in cvals) else ValStatus.PRUEFBEDARF
        any_open = any_open or bool(open_issues) or item.validation_status != ValStatus.VERIFIZIERT
    offer_open = session.scalars(select(ReviewIssue.id).where(ReviewIssue.offer_id == offer.id, ReviewIssue.status == "offen",
                                                              ReviewIssue.severity != "info")).first()
    offer.validation_status = ValStatus.PRUEFBEDARF if (any_open or offer_open) else ValStatus.VERIFIZIERT
