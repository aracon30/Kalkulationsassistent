"""SQLAlchemy-Datenmodelle.

Grundsätze:
- Fehlende Werte sind NULL, niemals 0.
- Originalwerte (Import) und Simulationen sind strikt getrennt.
- Jeder importierte Wert besitzt über ``extraction_values`` eine Quellenreferenz.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- Statuskonstanten -------------------------------------------------------

class DocType:
    KALKULATION = "angebotskalkulation"
    FERTIGUNGSZEIT = "fertigungszeitkalkulation"
    KUNDENANGEBOT = "kundenangebot"
    ZEICHNUNG = "technische_zeichnung"
    STUECKLISTE = "stueckliste"
    LIEFERANTENANGEBOT = "lieferantenangebot"
    SONSTIGES = "sonstiges"

    LABELS = {
        KALKULATION: "Angebotskalkulation",
        FERTIGUNGSZEIT: "Fertigungszeitkalkulation",
        KUNDENANGEBOT: "Kundenangebot",
        ZEICHNUNG: "Technische Zeichnung",
        STUECKLISTE: "Stückliste",
        LIEFERANTENANGEBOT: "Lieferantenangebot",
        SONSTIGES: "Sonstiges Dokument",
    }


class ClassStatus:
    EINDEUTIG = "eindeutig"
    WAHRSCHEINLICH = "wahrscheinlich"
    MANUELL = "manuelle_pruefung"
    NICHT_UNTERSTUETZT = "nicht_unterstuetzt"

    LABELS = {
        EINDEUTIG: "Eindeutig erkannt",
        WAHRSCHEINLICH: "Wahrscheinlich erkannt",
        MANUELL: "Manuelle Prüfung erforderlich",
        NICHT_UNTERSTUETZT: "Nicht unterstützt",
    }


class ValStatus:
    VERIFIZIERT = "verifiziert"
    PRUEFBEDARF = "pruefbedarf"
    NICHT_IMPORTIERBAR = "nicht_importierbar"

    LABELS = {
        VERIFIZIERT: "Verifiziert",
        PRUEFBEDARF: "Prüfbedarf",
        NICHT_IMPORTIERBAR: "Nicht importierbar",
    }


class ImportStatus:
    NEU = "neu"
    IMPORTIERT = "importiert"
    FEHLER = "fehler"
    IGNORIERT = "ignoriert"          # Dokumenttyp ohne Datenimport (z. B. Sonstiges)
    NICHT_UNTERSTUETZT = "nicht_unterstuetzt"
    VERALTET = "veraltet"            # ältere Version derselben Datei


# --- Tabellen ----------------------------------------------------------------

class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer: Mapped[str | None] = mapped_column(String(255), index=True)
    project_name: Mapped[str | None] = mapped_column(String(255))
    offer_date: Mapped[date | None] = mapped_column(Date)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    country: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(8))
    revision: Mapped[str | None] = mapped_column(String(32))
    validation_status: Mapped[str] = mapped_column(String(32), default=ValStatus.PRUEFBEDARF)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    items: Mapped[list[OfferItem]] = relationship(back_populates="offer", cascade="all, delete-orphan", order_by="OfferItem.position_number")
    documents: Mapped[list[Document]] = relationship(back_populates="offer")


class OfferItem(Base):
    __tablename__ = "offer_items"
    __table_args__ = (UniqueConstraint("offer_id", "position_number", name="uq_offer_item_position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), index=True)
    position_number: Mapped[int] = mapped_column(Integer)
    product_type: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    drawing_number: Mapped[str | None] = mapped_column(String(64), index=True)
    dn: Mapped[float | None] = mapped_column(Float, index=True)
    design_pressure: Mapped[float | None] = mapped_column(Float)          # PS in bar(g)
    design_temperature_min: Mapped[float | None] = mapped_column(Float)   # TS min in °C
    design_temperature_max: Mapped[float | None] = mapped_column(Float)   # TS max in °C
    material: Mapped[str | None] = mapped_column(String(64), index=True)  # normalisiert
    material_raw: Mapped[str | None] = mapped_column(String(128))         # wie in der Quelle
    diameter: Mapped[float | None] = mapped_column(Float)                 # mm
    wall_thickness: Mapped[float | None] = mapped_column(Float)           # mm
    height: Mapped[float | None] = mapped_column(Float)                   # mm
    volume: Mapped[float | None] = mapped_column(Float)                   # l
    filter_elements: Mapped[int | None] = mapped_column(Integer)
    filter_area: Mapped[float | None] = mapped_column(Float)              # m²
    weight: Mapped[float | None] = mapped_column(Float)                   # kg
    design_code: Mapped[str | None] = mapped_column(String(64))
    validation_status: Mapped[str] = mapped_column(String(32), default=ValStatus.PRUEFBEDARF)

    offer: Mapped[Offer] = relationship(back_populates="items")
    calculations: Mapped[list[Calculation]] = relationship(back_populates="offer_item", cascade="all, delete-orphan")
    documents: Mapped[list[Document]] = relationship(back_populates="offer_item")
    operations: Mapped[list[ManufacturingOperation]] = relationship(back_populates="offer_item", cascade="all, delete-orphan")

    @property
    def current_calculation(self) -> Calculation | None:
        current = [c for c in self.calculations if c.is_current]
        if current:
            return current[0]
        return max(self.calculations, key=lambda c: (c.revision_index or 0, c.id), default=None)


COST_FIELDS = [
    "material_cost", "material_overhead", "purchased_parts_cost", "manufacturing_hours",
    "manufacturing_hourly_rate", "manufacturing_cost", "engineering_hours", "engineering_cost",
    "documentation_cost", "inspection_cost", "ndt_tuev_cost", "surface_treatment_cost",
    "procurement_cost", "sales_overhead", "risk_profit", "total_cost", "offer_price",
]


class Calculation(Base):
    __tablename__ = "calculations"
    __table_args__ = (UniqueConstraint("offer_item_id", "document_id", name="uq_calc_item_document"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_item_id: Mapped[int] = mapped_column(ForeignKey("offer_items.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    revision: Mapped[str | None] = mapped_column(String(32))
    revision_index: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_id: Mapped[str | None] = mapped_column(String(64))

    material_cost: Mapped[float | None] = mapped_column(Float)
    material_overhead: Mapped[float | None] = mapped_column(Float)
    purchased_parts_cost: Mapped[float | None] = mapped_column(Float)
    manufacturing_hours: Mapped[float | None] = mapped_column(Float)
    manufacturing_hourly_rate: Mapped[float | None] = mapped_column(Float)
    manufacturing_cost: Mapped[float | None] = mapped_column(Float)
    engineering_hours: Mapped[float | None] = mapped_column(Float)
    engineering_cost: Mapped[float | None] = mapped_column(Float)
    documentation_cost: Mapped[float | None] = mapped_column(Float)
    inspection_cost: Mapped[float | None] = mapped_column(Float)
    ndt_tuev_cost: Mapped[float | None] = mapped_column(Float)
    surface_treatment_cost: Mapped[float | None] = mapped_column(Float)
    procurement_cost: Mapped[float | None] = mapped_column(Float)
    sales_overhead: Mapped[float | None] = mapped_column(Float)
    risk_profit: Mapped[float | None] = mapped_column(Float)
    total_cost: Mapped[float | None] = mapped_column(Float)
    offer_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    calculation_date: Mapped[date | None] = mapped_column(Date)
    validation_status: Mapped[str] = mapped_column(String(32), default=ValStatus.PRUEFBEDARF)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    offer_item: Mapped[OfferItem] = relationship(back_populates="calculations")
    document: Mapped[Document | None] = relationship()
    operations: Mapped[list[ManufacturingOperation]] = relationship(back_populates="calculation")


class ManufacturingOperation(Base):
    __tablename__ = "manufacturing_operations"
    __table_args__ = (UniqueConstraint("offer_item_id", "document_id", "operation_key", name="uq_operation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    calculation_id: Mapped[int | None] = mapped_column(ForeignKey("calculations.id", ondelete="SET NULL"), index=True)
    offer_item_id: Mapped[int] = mapped_column(ForeignKey("offer_items.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    operation_key: Mapped[str] = mapped_column(String(64))
    operation_name: Mapped[str] = mapped_column(String(128))
    planned_hours: Mapped[float | None] = mapped_column(Float)
    actual_hours: Mapped[float | None] = mapped_column(Float)
    hourly_rate: Mapped[float | None] = mapped_column(Float)
    source_reference: Mapped[str | None] = mapped_column(String(255))

    calculation: Mapped[Calculation | None] = relationship(back_populates="operations")
    offer_item: Mapped[OfferItem] = relationship(back_populates="operations")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("file_path", "file_hash", name="uq_document_path_hash"),
        Index("ix_documents_path_current", "file_path", "is_current"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"), index=True)
    offer_item_id: Mapped[int | None] = mapped_column(ForeignKey("offer_items.id", ondelete="SET NULL"), index=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    relative_path: Mapped[str | None] = mapped_column(String(1024))
    file_name: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str | None] = mapped_column(String(16))
    document_type: Mapped[str | None] = mapped_column(String(64), index=True)
    classification_status: Mapped[str | None] = mapped_column(String(32))
    classification_rule: Mapped[str | None] = mapped_column(Text)
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    detected_offer_number: Mapped[str | None] = mapped_column(String(32), index=True)
    offer_number_source: Mapped[str | None] = mapped_column(String(64))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int | None] = mapped_column(Integer)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    revision_label: Mapped[str | None] = mapped_column(String(32))
    import_status: Mapped[str] = mapped_column(String(32), default=ImportStatus.NEU, index=True)
    import_message: Mapped[str | None] = mapped_column(Text)
    profile_id: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    has_text: Mapped[bool | None] = mapped_column(Boolean)

    offer: Mapped[Offer | None] = relationship(back_populates="documents")
    offer_item: Mapped[OfferItem | None] = relationship(back_populates="documents")
    values: Mapped[list[ExtractionValue]] = relationship(back_populates="document", cascade="all, delete-orphan")


class ExtractionValue(Base):
    __tablename__ = "extraction_values"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    offer_item_id: Mapped[int | None] = mapped_column(ForeignKey("offer_items.id", ondelete="CASCADE"), index=True)
    calculation_id: Mapped[int | None] = mapped_column(ForeignKey("calculations.id", ondelete="CASCADE"), index=True)
    target_entity: Mapped[str | None] = mapped_column(String(32))  # offer | offer_item | calculation | operation
    field_name: Mapped[str] = mapped_column(String(64), index=True)
    field_value: Mapped[str | None] = mapped_column(Text)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))
    source_sheet: Mapped[str | None] = mapped_column(String(128))
    source_cell: Mapped[str | None] = mapped_column(String(16))
    source_label: Mapped[str | None] = mapped_column(String(255))
    source_formula: Mapped[str | None] = mapped_column(Text)
    cached_value: Mapped[str | None] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_text: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    validation_status: Mapped[str] = mapped_column(String(32), default=ValStatus.PRUEFBEDARF, index=True)
    validation_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="values")


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(16))  # scan | import
    base_dir: Mapped[str | None] = mapped_column(String(1024))
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="laeuft")
    files_scanned: Mapped[int] = mapped_column(Integer, default=0)
    files_imported: Mapped[int] = mapped_column(Integer, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    current_item: Mapped[str | None] = mapped_column(String(1024))
    error_log: Mapped[str | None] = mapped_column(Text)

    entries: Mapped[list[ImportJobEntry]] = relationship(back_populates="job", cascade="all, delete-orphan")


class ImportJobEntry(Base):
    __tablename__ = "import_job_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    offer_number: Mapped[str | None] = mapped_column(String(32))
    file_path: Mapped[str | None] = mapped_column(String(1024))
    action: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    job: Mapped[ImportJob] = relationship(back_populates="entries")


class ReviewIssue(Base):
    __tablename__ = "review_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), index=True)
    offer_item_id: Mapped[int | None] = mapped_column(ForeignKey("offer_items.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    extraction_value_id: Mapped[int | None] = mapped_column(ForeignKey("extraction_values.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warnung")  # info | warnung | fehler
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="offen", index=True)  # offen | erledigt
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_note: Mapped[str | None] = mapped_column(Text)

    offer: Mapped[Offer | None] = relationship()
    offer_item: Mapped[OfferItem | None] = relationship()
    document: Mapped[Document | None] = relationship()
    extraction_value: Mapped[ExtractionValue | None] = relationship()


class Correction(Base):
    """Prüfprotokoll: jede manuelle Korrektur oder Freigabe."""
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    field_name: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(32))  # korrektur | freigabe | zuordnung
    reason: Mapped[str | None] = mapped_column(Text)
    user_name: Mapped[str | None] = mapped_column(String(128))
    extraction_value_id: Mapped[int | None] = mapped_column(ForeignKey("extraction_values.id", ondelete="SET NULL"))
    review_issue_id: Mapped[int | None] = mapped_column(ForeignKey("review_issues.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Simulation(Base):
    """Kalkulationssimulationen – strikt getrennt von importierten Originalwerten."""
    __tablename__ = "simulations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    offer_item_id: Mapped[int | None] = mapped_column(ForeignKey("offer_items.id", ondelete="SET NULL"))
    base_calculation_id: Mapped[int | None] = mapped_column(ForeignKey("calculations.id", ondelete="SET NULL"))
    scheme_id: Mapped[str | None] = mapped_column(String(64))
    inputs_json: Mapped[str] = mapped_column(Text)
    results_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AIRequestLog(Base):
    """Protokoll externer/lokaler KI-Aufrufe (ohne Inhalte und ohne Zugangsdaten)."""
    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    chars_sent: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    cost_estimate: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
