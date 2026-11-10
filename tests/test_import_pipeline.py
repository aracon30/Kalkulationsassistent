import shutil

import pytest
from sqlalchemy import func, select

from app.models import (Calculation, Document, DocType, ExtractionValue, ImportStatus, ManufacturingOperation, Offer, OfferItem,
                        ReviewIssue, ValStatus)
from app.services import import_service
from app.services.import_service import run_import, scan_and_inventory
from tests.conftest import hash_tree
from tests.factory import POSITIONS, TEST_OFFER, expected_calc, make_kalkulation


def _full_import(db, base):
    scan_and_inventory(db, str(base))
    return run_import(db)


def test_offer_with_two_positions(db, offer_base):
    before = hash_tree(offer_base)
    summary = _full_import(db, offer_base)
    assert summary.failed == 0 and summary.offers == 1
    offers = db.scalars(select(Offer)).all()
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_number == TEST_OFFER and offer.year == 2026 and offer.customer == "Testkunde Chemie GmbH"
    assert offer.currency == "EUR" and offer.country == "Deutschland" and str(offer.offer_date) == "2026-03-12"
    assert [i.position_number for i in offer.items] == [1, 2]
    for item, p in zip(offer.items, POSITIONS):
        assert item.product_type == p["product_type"] and item.dn == p["dn"] and item.material == "1.4404"
        calc = item.current_calculation
        assert calc.offer_price == pytest.approx(expected_calc(p)["price"])
        assert calc.manufacturing_hours == p["hours"]
        ops = {o.operation_key: o.planned_hours for o in item.operations}
        assert ops["gesamt"] == p["hours"]
        assert all(o.calculation_id == calc.id for o in item.operations)
    # Zeichnungen der richtigen Position zugeordnet
    docs = {d.file_name: d for d in db.scalars(select(Document))}
    assert docs["KF-9-301.11-005.pdf"].offer_item.position_number == 1
    assert docs["BF-9-1001.11-001.pdf"].offer_item.position_number == 2
    assert docs["Angebot.pdf"].offer_id == offer.id
    # Originaldateien unverändert
    assert hash_tree(offer_base) == before


def test_every_value_has_source_reference(db, offer_base):
    _full_import(db, offer_base)
    evs = db.scalars(select(ExtractionValue).where(ExtractionValue.extraction_method.like("profil%"))).all()
    assert evs
    assert all(ev.source_sheet and ev.source_cell and ev.document_id for ev in evs)
    price = db.scalars(select(ExtractionValue).where(ExtractionValue.field_name == "offer_price")).first()
    assert price.source_formula == "=B31+C32" and price.cached_value is not None
    pdf_vals = db.scalars(select(ExtractionValue).where(ExtractionValue.extraction_method == "pdf_text")).all()
    assert pdf_vals and all(v.source_page == 1 and v.source_text for v in pdf_vals)


def test_unvalidated_profile_never_verified(db, offer_base):
    _full_import(db, offer_base)
    statuses = set(db.scalars(select(ExtractionValue.validation_status).where(ExtractionValue.extraction_method.like("profil%"))))
    assert statuses == {ValStatus.PRUEFBEDARF}
    assert db.scalar(select(func.count(ReviewIssue.id)).where(ReviewIssue.code == "profil_nicht_validiert")) == 2


def test_validated_profile_gives_verified_values(db, offer_base, tmp_path, monkeypatch):
    from app.config import get_settings
    prof_dir = tmp_path / "profiles"
    shutil.copytree(get_settings().profiles_dir, prof_dir)
    for name in ("heta_2026.yaml", "heta_fertigungszeit.yaml"):
        p = prof_dir / name
        p.write_text(p.read_text(encoding="utf-8").replace("validated: false", "validated: true"), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "profiles_dir", prof_dir)
    _full_import(db, offer_base)
    price = db.scalars(select(ExtractionValue).where(ExtractionValue.field_name == "offer_price")).first()
    assert price.validation_status == ValStatus.VERIFIZIERT
    dn_pdf = db.scalars(select(ExtractionValue).where(ExtractionValue.field_name == "dn", ExtractionValue.extraction_method == "pdf_text")).first()
    assert dn_pdf.validation_status == ValStatus.VERIFIZIERT and "bestätigt" in dn_pdf.validation_message


def test_reimport_is_idempotent(db, offer_base):
    _full_import(db, offer_base)
    counts = lambda: tuple(db.scalar(select(func.count(m.id))) for m in (Offer, OfferItem, Calculation, ManufacturingOperation, ExtractionValue, Document, ReviewIssue))
    first = counts()
    s2 = _full_import(db, offer_base)
    assert s2.imported == 0
    assert counts() == first
    # Auch erzwungener Neuimport erzeugt keine Dubletten
    run_import(db, force=True)
    assert counts()[:5] == first[:5]


def test_changed_file_creates_new_revision(db, offer_base):
    _full_import(db, offer_base)
    path = offer_base / "2026" / TEST_OFFER / f"{TEST_OFFER}_Kalkulation.xlsx"
    changed = [dict(p) for p in POSITIONS]
    changed[0]["material_cost"] = 9000.0
    import os, time
    make_kalkulation(path, positions=changed)
    os.utime(path, (time.time() + 5, time.time() + 5))
    summary = scan_and_inventory(db, str(offer_base))
    assert summary.changed_documents == 1
    run_import(db)
    item = db.scalars(select(OfferItem).where(OfferItem.position_number == 1)).one()
    assert len(item.calculations) == 2
    assert item.current_calculation.material_cost == 9000.0
    assert db.scalar(select(func.count(Offer.id))) == 1
    assert db.scalar(select(func.count(OfferItem.id))) == 2
    versions = db.scalars(select(Document).where(Document.file_path == str(path)).order_by(Document.version)).all()
    assert [v.is_current for v in versions] == [False, True]
    # Werte der alten Version werden ersetzt, nicht als Widerspruch gemeldet
    assert not db.scalars(select(ReviewIssue).where(ReviewIssue.code == "widerspruch")).all()


def test_duplicate_content_is_detected(db, offer_base):
    folder = offer_base / "2026" / TEST_OFFER
    shutil.copy(folder / "KF-9-301.11-005.pdf", folder / "Kopie_KF-9-301.11-005.pdf")
    _full_import(db, offer_base)
    dup = db.scalars(select(Document).where(Document.file_name == "Kopie_KF-9-301.11-005.pdf")).one()
    assert dup.duplicate_of_id is not None and dup.import_status == ImportStatus.IGNORIERT


def test_missing_cache_creates_review_issue_and_null(db, env):
    from tests.factory import make_offer_folder
    base = env / "A2"
    make_offer_folder(base, with_cache=False)
    _full_import(db, base)
    calc = db.scalars(select(Calculation)).first()
    assert calc.offer_price is None and calc.total_cost is None
    assert db.scalars(select(ReviewIssue).where(ReviewIssue.code == "formel_ohne_cache")).first()
    assert db.scalars(select(ReviewIssue).where(ReviewIssue.code == "preis_fehlt")).first()


def test_corrupt_file_is_isolated(db, offer_base):
    folder = offer_base / "2026" / TEST_OFFER
    (folder / f"{TEST_OFFER}_Kalkulation_alt.xlsx").write_bytes(b"PK\x03\x04 defekt")
    summary = _full_import(db, offer_base)
    assert summary.failed == 1
    bad = db.scalars(select(Document).where(Document.file_name.like("%_alt.xlsx"))).one()
    assert bad.import_status == ImportStatus.FEHLER
    assert db.scalar(select(func.count(OfferItem.id))) == 2  # Rest des Angebots importiert
    assert db.scalars(select(ReviewIssue).where(ReviewIssue.code == "nicht_importierbar")).first()


def test_failure_rolls_back_whole_offer(db, offer_base, monkeypatch):
    scan_and_inventory(db, str(offer_base))

    def boom(*a, **kw):
        raise KeyError("simulierter Absturz")

    monkeypatch.setattr(import_service, "_update_current_calculations", boom)
    summary = run_import(db)
    assert summary.errors
    assert db.scalar(select(func.count(Offer.id))) == 0
    assert db.scalar(select(func.count(OfferItem.id))) == 0
    assert db.scalar(select(func.count(ExtractionValue.id))) == 0
    assert all(d.import_status in (ImportStatus.FEHLER, ImportStatus.NICHT_UNTERSTUETZT) for d in db.scalars(select(Document)))


def test_cancel_leaves_no_partial_offer(db, offer_base):
    scan_and_inventory(db, str(offer_base))
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 2  # Abbruch mitten im Angebot

    summary = run_import(db, import_service.Reporter(cancelled=cancelled))
    assert summary.cancelled
    assert db.scalar(select(func.count(Offer.id))) == 0
    assert db.scalar(select(func.count(Calculation.id))) == 0
    # danach vollständiger Import möglich
    s2 = run_import(db)
    assert s2.offers == 1 and db.scalar(select(func.count(OfferItem.id))) == 2


def test_conflicting_drawing_value_is_flagged(db, env):
    from tests.factory import make_offer_folder
    base = env / "A3"
    folder = make_offer_folder(base)
    from tests.factory import make_drawing_pdf
    p = dict(POSITIONS[0]); p["dn"] = 350
    make_drawing_pdf(folder / f"{p['drawing']}.pdf", p)
    _full_import(db, base)
    issue = db.scalars(select(ReviewIssue).where(ReviewIssue.code == "widerspruch")).first()
    assert issue and "dn" in issue.message and "350" in issue.message
    item = db.scalars(select(OfferItem).where(OfferItem.position_number == 1)).one()
    assert item.dn == 300  # Kalkulationswert wird nicht stillschweigend überschrieben


def test_document_without_offer_number(db, env):
    base = env / "B"
    from tests.factory import make_kalkulation
    make_kalkulation(base / "Diverses" / "Kalkulation.xlsx")
    _full_import(db, base)
    doc = db.scalars(select(Document)).one()
    assert doc.import_status == ImportStatus.FEHLER
    assert db.scalars(select(ReviewIssue).where(ReviewIssue.code == "keine_angebotsnummer")).first()


def test_background_job_runs_scan_and_import(env, offer_base):
    from app.database import new_session
    from app.models import ImportJob, ImportJobEntry
    from app.services.jobs import manager
    j1 = manager.start("scan", str(offer_base), wait=True)
    j2 = manager.start("import", None, wait=True)
    s = new_session()
    try:
        assert s.get(ImportJob, j1).status == "abgeschlossen" and s.get(ImportJob, j1).files_scanned == 6
        job = s.get(ImportJob, j2)
        assert job.status == "abgeschlossen" and job.files_imported == 5
        assert s.scalar(select(func.count(ImportJobEntry.id)).where(ImportJobEntry.job_id == j2)) >= 5
    finally:
        s.close()
