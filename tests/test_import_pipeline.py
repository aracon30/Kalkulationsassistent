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
    for name in ("generic_excel.yaml", "generic_fertigungszeit.yaml"):
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


def test_heta_folder_structure_and_ae_numbers(db, env, monkeypatch):
    """Ordnerstruktur wie auf K:\\ (AE-Nummern, 03 Angebot\\02 Kalkulationen, 01 Maßblatt)."""
    from app.models import ClassStatus
    from tests.factory import make_drawing_pdf, make_kalkulation, make_offer_pdf
    from app.services import folder_scanner
    base = env / "K"
    root = base / "AE-13548.26-IYYM" / "01 Vertrieb - Administration" / "03 Angebot"
    make_kalkulation(root / "02 Kalkulationen" / "Pr209_Kalkulation.xlsx", "AE-13548.26")
    make_drawing_pdf(root / "01 Maßblatt" / "SKF-3-301.13-003.pdf", dict(POSITIONS[0], drawing="SKF-3-301.13-003"))
    make_offer_pdf(root / "AE-13548.26.pdf", "AE-13548.26")
    mail = base / "AE-13548.26-IYYM" / "01 Vertrieb - Administration" / "02 Schriftverkehr" / "Anfrage.msg"
    mail.parent.mkdir(parents=True); mail.write_bytes(b"x" * 1000)
    hashed = []
    real = import_service.sha256_file
    monkeypatch.setattr(import_service, "sha256_file", lambda p: hashed.append(str(p)) or real(p))
    summary = scan_and_inventory(db, str(base))
    assert not [h for h in hashed if h.endswith(".msg")]  # Mails werden nicht gelesen
    docs = {d.file_name: d for d in db.scalars(select(Document))}
    assert all(d.detected_offer_number == "AE-13548.26" for d in docs.values())
    assert docs["Pr209_Kalkulation.xlsx"].document_type == DocType.KALKULATION
    assert "Ordner '02 Kalkulationen'" in docs["Pr209_Kalkulation.xlsx"].classification_rule
    assert docs["AE-13548.26.pdf"].document_type == DocType.KUNDENANGEBOT
    assert docs["SKF-3-301.13-003.pdf"].document_type == DocType.ZEICHNUNG
    assert docs["Anfrage.msg"].import_status == ImportStatus.NICHT_UNTERSTUETZT and not docs["Anfrage.msg"].content_hashed
    run_import(db)
    offer = db.scalars(select(Offer)).one()
    assert offer.offer_number == "AE-13548.26" and offer.year == 2026 and len(offer.items) == 2


def test_same_file_in_two_offers_is_not_a_duplicate(db, env):
    from tests.factory import make_kalkulation
    base = env / "K2"
    a = make_kalkulation(base / "AE-10001.26-A" / "Kalkulation R05.xlsx", "AE-10001.26")
    (base / "AE-10002.26-B").mkdir()
    shutil.copy(a, base / "AE-10002.26-B" / "Kalkulation R05.xlsx")
    shutil.copy(a, base / "AE-10002.26-B" / "Kopie Kalkulation R05.xlsx")
    scan_and_inventory(db, str(base))
    docs = db.scalars(select(Document).order_by(Document.file_path)).all()
    statuses = {(d.detected_offer_number, d.file_name): d.import_status for d in docs}
    assert statuses[("AE-10001.26", "Kalkulation R05.xlsx")] == ImportStatus.NEU
    assert statuses[("AE-10002.26", "Kalkulation R05.xlsx")] == ImportStatus.NEU
    assert statuses[("AE-10002.26", "Kopie Kalkulation R05.xlsx")] == ImportStatus.IGNORIERT


def test_rescan_reclassifies_unchanged_files_after_rule_update(db, offer_base):
    scan_and_inventory(db, str(offer_base))
    for d in db.scalars(select(Document)):
        d.classifier_version, d.detected_offer_number, d.import_status = 1, None, ImportStatus.IGNORIERT
    db.commit()
    summary = scan_and_inventory(db, str(offer_base))
    assert summary.reclassified == 6 and summary.new_documents == 0
    kalk = db.scalars(select(Document).where(Document.file_name.like("%Kalkulation.xlsx"))).one()
    assert kalk.detected_offer_number == TEST_OFFER and kalk.import_status == ImportStatus.NEU


def test_heta_r05_template_end_to_end(db, env):
    """Aufbau wie SE-13705.26 (Originalvorlage R05), Werte synthetisch."""
    from tests.factory import R05_POSITIONS, make_heta_fertigungszeit, make_heta_r05_kalkulation
    folder = env / "K" / "SE-99902.26-Testkunde"
    make_heta_r05_kalkulation(folder / "SE-99902.26_Kalkulation.xlsx", extra_rows=3)
    make_heta_fertigungszeit(folder / "SE-99902.26_Fertigungszeit.xlsx")
    make_heta_r05_kalkulation(folder / "Kalkulation R06_01-2026.xlsx", positions=[dict(R05_POSITIONS[0], chosen=1)])  # Vorlagenkopie
    scan_and_inventory(db, str(env / "K"))
    run_import(db)
    offer = db.scalars(select(Offer)).one()
    assert offer.offer_number == "SE-99902.26" and offer.customer == "Testkunde AB" and str(offer.offer_date) == "2026-11-04"
    kf, bf = offer.items
    assert (kf.position_number, kf.product_type, kf.dn, kf.material, kf.filter_elements, kf.drawing_number) == (1, "Kerzenfilter", 300, "1.4404", 7, "KF-9-301.11-005")
    assert (kf.diameter, kf.wall_thickness, kf.weight, kf.quantity) == (323.9, 4.5, 250, 1)
    assert (bf.product_type, bf.dn, bf.filter_elements, bf.diameter, bf.wall_thickness) == ("Beutelfilter", 1000, 12, 1016, 8)
    c1 = kf.current_calculation
    assert c1.offer_price == 33750 and c1.manufacturing_hours == 88 and c1.manufacturing_hourly_rate == 65
    assert c1.engineering_hours == 40 and c1.calculated_price == pytest.approx(c1.total_cost * 1.2)
    assert c1.document.file_name == "SE-99902.26_Kalkulation.xlsx"
    ops = {o.operation_key: o for o in kf.operations}
    assert ops["gesamt_gerundet"].planned_hours == 88 and ops["grundzeit"].planned_hours == pytest.approx(20.5)
    assert sum(1 for k in ops if k.startswith("ag_")) == 4
    codes = {i.code for i in db.scalars(select(ReviewIssue))}
    assert not codes & {"angebotsnummer_abweichend", "widerspruch_fertigungszeit", "summe_abweichend", "widerspruch", "keine_positionen"}
    tpl = db.scalars(select(Document).where(Document.file_name == "Kalkulation R06_01-2026.xlsx")).one()
    assert tpl.import_status == ImportStatus.IGNORIERT and "vorlage" in tpl.import_message.lower()
    assert "kopie_ignoriert" in codes


def test_forced_reimport_replaces_values_from_earlier_wrong_profile(db, offer_base):
    """Erst falsch importiert (z. B. altes Profil), dann 'Alle erneut importieren' -> saubere Neuwerte, keine Widersprüche."""
    _full_import(db, offer_base)
    offer = db.scalars(select(Offer)).one()
    offer.customer = "Angebot Nr:"
    item = offer.items[0]
    item.product_type, item.material = "Material", "APZ Kosten"
    db.add(OfferItem(offer_id=offer.id, position_number=7, product_type="Geisterposition"))
    db.commit()
    run_import(db, force=True)
    db.expire_all()
    offer = db.scalars(select(Offer)).one()
    assert offer.customer == "Testkunde Chemie GmbH"
    assert [(i.position_number, i.product_type, i.material) for i in offer.items] == [(1, "Kerzenfilter", "1.4404"), (2, "Beutelfilter", "1.4404")]
    assert not db.scalars(select(ReviewIssue).where(ReviewIssue.code == "widerspruch")).all()
    assert db.scalar(select(func.count(Calculation.id))) == 2


def test_heta_r05_without_chosen_price_uses_calculated_price(db, env):
    from tests.factory import R05_POSITIONS, make_heta_r05_kalkulation
    folder = env / "K3" / "SE-99903.26-X"
    make_heta_r05_kalkulation(folder / "SE-99903.26_Kalkulation.xlsx", "SE-99903/26",
                              positions=[dict(R05_POSITIONS[0], chosen=None, quantity=2)])
    scan_and_inventory(db, str(env / "K3"))
    run_import(db)
    item = db.scalars(select(OfferItem)).one()
    calc = item.current_calculation
    assert calc.offer_price == pytest.approx(calc.calculated_price) and calc.offer_price > 0
    ev = db.scalars(select(ExtractionValue).where(ExtractionValue.field_name == "offer_price")).one()
    assert ev.extraction_method == "profil_ersatz" and "übernommen" in ev.validation_message
    codes = {i.code for i in db.scalars(select(ReviewIssue))}
    assert "stueckzahl_groesser_1" in codes and not codes & {"ersatzwert", "preis_fehlt"}
    assert item.quantity == 2


def test_heta_r05_validated_profile_gives_verified_values(db, env):
    from tests.factory import make_heta_r05_kalkulation
    folder = env / "K4" / "SE-99904.26-X"
    make_heta_r05_kalkulation(folder / "SE-99904.26_Kalkulation.xlsx", "SE-99904/26")
    scan_and_inventory(db, str(env / "K4"))
    run_import(db)
    price = db.scalars(select(ExtractionValue).where(ExtractionValue.field_name == "offer_price")).first()
    total = db.scalars(select(ExtractionValue).where(ExtractionValue.field_name == "total_cost")).first()
    assert price.validation_status == ValStatus.VERIFIZIERT and total.validation_status == ValStatus.VERIFIZIERT
    assert not db.scalars(select(ReviewIssue).where(ReviewIssue.code.in_(["formel_cache_abweichung", "profil_nicht_validiert"]))).all()


def test_heta_r05_validated_offer_is_verified_despite_info_hints(db, env):
    from tests.factory import make_heta_fertigungszeit, make_heta_r05_kalkulation, make_offer_pdf
    folder = env / "K5" / "SE-99905.26-X"
    make_heta_r05_kalkulation(folder / "SE-99905.26_Kalkulation.xlsx", "SE-99905/26")
    make_heta_fertigungszeit(folder / "SE-99905.26_Fertigungszeit.xlsx", "SE-99905/26")
    make_offer_pdf(folder / "SE-99905.26.pdf", "SE-99905.26")
    import pymupdf
    scan = pymupdf.open(); scan.new_page(); scan.save(str(folder / "KF-9-301.11-005.pdf")); scan.close()  # Zeichnung ohne Text
    scan_and_inventory(db, str(env / "K5"))
    run_import(db)
    offer = db.scalars(select(Offer)).one()
    assert offer.currency == "EUR" and offer.items[0].current_calculation.currency == "EUR"
    open_issues = db.scalars(select(ReviewIssue).where(ReviewIssue.status == "offen")).all()
    assert open_issues and all(i.severity == "info" for i in open_issues), [(i.code, i.message) for i in open_issues]
    assert [i.validation_status for i in offer.items] == [ValStatus.VERIFIZIERT] * 2
    assert all(i.current_calculation.validation_status == ValStatus.VERIFIZIERT for i in offer.items)
    assert offer.validation_status == ValStatus.VERIFIZIERT


def test_heta_r05_material_lines(db, env):
    from app.models import MaterialLine
    from tests.factory import make_heta_r05_kalkulation
    folder = env / "K6" / "SE-99906.26-X"
    make_heta_r05_kalkulation(folder / "SE-99906.26_Kalkulation.xlsx", "SE-99906/26")
    scan_and_inventory(db, str(env / "K6"))
    run_import(db)
    lines = {(l.offer_item.position_number, l.description): l for l in db.scalars(select(MaterialLine))}
    assert len(lines) == 8  # Positionstitel wird übersprungen
    mantel = lines[(1, "Behältermantel")]
    assert (mantel.category, mantel.outer_diameter, mantel.wall_thickness, mantel.material) == ("Rohr", 323.9, 4.5, "1.4404")
    assert (mantel.unit_price, mantel.price_unit, mantel.length_m, mantel.apz_cost, mantel.cost) == (220, "€/m", 1.7, 35, 409)
    assert mantel.est_weight_unit == "kg/m" and 6 < mantel.price_per_kg < 7
    assert (mantel.source_sheet, mantel.source_row) == ("KF-300", 5)
    flansch = lines[(1, "Behälterflansch")]
    assert (flansch.category, flansch.dn, flansch.pn, flansch.price_unit) == ("Flansch", 300, 16, "€/Stk")
    assert lines[(1, "Schrauben + Muttern Deckel")].category == "Verbindungselement"
    assert lines[(1, "Schrauben + Muttern Deckel")].thread == "M24"
    blech = lines[(2, "Behältermantel")]
    assert (blech.category, blech.plate_length, blech.plate_width, blech.wall_thickness) == ("Blech/Flachstahl", 3190, 1350, 8)
    assert 260 < blech.est_weight < 285 and blech.est_weight_unit == "kg/Stk"
    assert lines[(2, "Filterbeutel PTFE TEST")].category == "Filterelement"
    assert not db.scalars(select(ReviewIssue).where(ReviewIssue.code == "summe_abweichend")).all()
    # Neuimport ersetzt die Zeilen (keine Dubletten)
    run_import(db, force=True)
    assert db.scalar(select(func.count(MaterialLine.id))) == 8


def test_heta_r05_trade_items_from_options_sheet(db, env):
    from app.models import TradeItem
    from tests.factory import make_heta_r05_kalkulation
    folder = env / "K7" / "SE-99910.26-X"
    make_heta_r05_kalkulation(folder / "SE-99910.26_Kalkulation.xlsx", "SE-99910/26")
    scan_and_inventory(db, str(env / "K7"))
    run_import(db)
    items = {t.line_no: t for t in db.scalars(select(TradeItem))}
    assert set(items) == {"1.1", "2.1"}  # Hinweiszeile ohne Zahlen wird nicht übernommen
    k = items["1.1"]
    assert (k.quantity, k.purchase_price, k.purchase_total, k.category, k.outer_diameter) == (7, 620, 4340, "Filterelement", 63)
    assert k.sales_total == pytest.approx(4340 * 1.08 * 1.2 * 1.2) and k.markup_factor == pytest.approx(1.5552)
    assert k.sales_price == pytest.approx(k.sales_total / 7) and not k.is_estimate and "Lieferant" in k.source_note
    assert items["2.1"].is_estimate
    run_import(db, force=True)
    assert db.scalar(select(func.count(TradeItem.id))) == 2


def test_trade_tables_from_spare_part_calculations(db, env):
    from app.models import Document, TradeItem
    from tests.factory import make_trade_table_assembly, make_trade_table_factor, make_trade_table_simple
    base = env / "T"
    make_trade_table_simple(base / "DE-99920.25" / "DE-99920.25-R00_Kalkulation.xlsx")
    make_trade_table_assembly(base / "DE-99921.25" / "DE-99921.25_Kalkulation.xlsx")
    make_trade_table_factor(base / "DE-99922.26" / "DE-99922.26___Kalkulation.xlsx")
    scan_and_inventory(db, str(base))
    run_import(db)
    profiles = {d.file_name: d.profile_id for d in db.scalars(select(Document))}
    assert set(profiles.values()) == {"handelsware_tabelle"}
    items = {(t.offer.offer_number, t.description): t for t in db.scalars(select(TradeItem))}
    k = items[("DE-99920.25", "Filterkerze")]
    assert (k.dimensions, k.purchase_price, k.sales_price, k.outer_diameter) == ("Ø166 x 650", 200, 250, 166)
    assert k.markup_factor == pytest.approx(1.25)
    # Baugruppe: Einzelteile mit EK, VK nur in der Summenzeile
    oring = items[("DE-99921.25", "O-Ring")]
    assert (oring.quantity, oring.purchase_price, oring.purchase_total, oring.sales_price) == (3, 250, 750, None)
    assembly = [t for (o, _), t in items.items() if o == "DE-99921.25" and t.is_assembly][0]
    assert assembly.description == "Baugruppe: Drehgelenk mit Dichtungssatz TYP TEST" and assembly.quantity == 3
    assert assembly.purchase_total == 3 * (300 + 250 + 20) and assembly.sales_price == round(1710 * 1.3 / 3 + 390)
    assert ("DE-99921.25", "Gesamt") not in items
    # Faktor-Tabelle: EK / Stück vor "EK", VK3 vor "VK", Lieferzeit, Material, früherer Auftrag
    ks = items[("DE-99922.26", "Kantenspaltelement TEST")]
    assert (ks.purchase_price, ks.sales_price, ks.sales_total, ks.lead_time, ks.drawing_ref) == (1600, 4000, 4000, "12-13 Wochen", "0000-Pos. 53")
    o = items[("DE-99922.26", "O-Ring TEST")]
    assert (o.quantity, o.purchase_price, o.sales_price, o.material, o.last_order) == (2, 5, 25, "NBR 70°", "K-00001/24")
    assert db.scalar(select(func.count(OfferItem.id))) == 0  # keine Schein-Apparatepositionen
    assert not db.scalars(select(ReviewIssue).where(ReviewIssue.code.in_(["profil_nicht_validiert", "keine_positionen"]))).all()


def test_unknown_layout_is_not_imported_as_fake_positions(db, env):
    import openpyxl
    from app.models import Document
    p = env / "U" / "DE-99930.25" / "DE-99930.25_Kalkulation.xlsx"
    p.parent.mkdir(parents=True)
    wb = openpyxl.Workbook()
    wb.active["A1"], wb.active["A2"], wb.active["B2"] = "Notiz", "Typ", "Material"
    wb.save(p)
    scan_and_inventory(db, str(env / "U"))
    run_import(db)
    doc = db.scalars(select(Document)).one()
    assert doc.import_status == ImportStatus.IGNORIERT and "nicht erkannt" in doc.import_message
    assert db.scalar(select(func.count(OfferItem.id))) == 0
    assert db.scalars(select(ReviewIssue).where(ReviewIssue.code == "vorlage_unbekannt")).first()


def test_service_calculation(db, env):
    from app.models import Document, ServiceCalc
    from tests.factory import make_service_calc
    make_service_calc(env / "S" / "DE-99940.26" / "DE-99940.26_Kalkulation Service 2026.xlsx")
    scan_and_inventory(db, str(env / "S"))
    run_import(db)
    doc = db.scalars(select(Document)).one()
    assert doc.profile_id == "serviceeinsatz" and doc.import_status == ImportStatus.IMPORTIERT
    svc = db.scalars(select(ServiceCalc)).one()
    assert (svc.title, svc.total_cost, svc.offer_price, svc.abroad) == ("Wartung", 140 * 8 + 85 + 1.3 * 40, 1500, False)
    assert svc.offer.customer == "Testkunde"
    kinds = {l.description: l.kind for l in svc.lines}
    assert kinds == {"Service Experte": "personal", "Monteur": "personal", "Kosten pro Tag": "tag", "Unterkunft pro Nacht": "unterkunft",
                     "Reisekosten": "reise", "Fahrkosten pro km": "km"}
    expert = next(l for l in svc.lines if l.description == "Service Experte")
    assert (expert.rate_domestic, expert.rate_abroad, expert.quantity, expert.total) == (140, 160, 8, 1120)
    assert db.scalar(select(func.count(OfferItem.id))) == 0
    run_import(db, force=True)
    assert db.scalar(select(func.count(ServiceCalc.id))) == 1


def test_template_copies_invalid_and_foreign_copies_are_not_imported(db, env):
    """Unveränderte Vorlage in mehreren Angeboten, 'ungültig'-Dateien und Kopien fremder Kalkulationen."""
    import shutil
    from tests.factory import R05_POSITIONS, make_heta_r05_kalkulation
    root = env / "K4"
    tpl = env / "tpl.xlsx"
    make_heta_r05_kalkulation(tpl, positions=[dict(R05_POSITIONS[0], chosen=None)])
    for nr in ("SE-99911.26", "SE-99912.26", "SE-99913.26"):
        (root / nr).mkdir(parents=True)
        shutil.copy(tpl, root / nr / "Kalkulation R06_01-2026.xlsx")
    # ausgefüllte Vorlage (einzigartig) in einem vierten Angebot -> wird importiert
    make_heta_r05_kalkulation(root / "SE-99914.26" / "Kalkulation R06_01-2026.xlsx", "SE-99914/26")
    # eigene Kalkulation + Kopie davon in einem anderen Angebot + ungültige Fassung
    make_heta_r05_kalkulation(root / "SE-99915.26" / "SE-99915.26_Kalkulation.xlsx", "SE-99915/26")
    make_heta_r05_kalkulation(root / "SE-99915.26" / "UNGÜLTIG_SE-99915.26-R01_Kalkulation.xlsx", "SE-99915/26", extra_rows=2)
    (root / "SE-99916.26").mkdir()
    shutil.copy(root / "SE-99915.26" / "SE-99915.26_Kalkulation.xlsx", root / "SE-99916.26" / "SE-99915.26_Kalkulation.xlsx")
    scan_and_inventory(db, str(root))
    run_import(db)
    with_calc = {o.offer_number for o in db.scalars(select(Offer)) if any(i.current_calculation for i in o.items)}
    assert with_calc == {"SE-99914.26", "SE-99915.26"}
    ignored = {(d.detected_offer_number, d.file_name): d.import_message for d in db.scalars(select(Document))
               if d.import_status == ImportStatus.IGNORIERT}
    assert "weiteren Angeboten" in ignored[("SE-99911.26", "Kalkulation R06_01-2026.xlsx")]
    assert "ungültig" in ignored[("SE-99915.26", "UNGÜLTIG_SE-99915.26-R01_Kalkulation.xlsx")]
    assert "Kopie der Kalkulation von SE-99915.26" in ignored[("SE-99916.26", "SE-99915.26_Kalkulation.xlsx")]


def test_newer_revision_replaces_values_without_conflict(db, env):
    from tests.factory import R05_POSITIONS, make_heta_r05_kalkulation
    folder = env / "K5" / "SE-99920.26"
    make_heta_r05_kalkulation(folder / "SE-99920.26_Kalkulation-R01.xlsx", "SE-99920/26", positions=[dict(R05_POSITIONS[0], weight=400, elements=("Filterkerzen 5 µm TEST", 9))])
    make_heta_r05_kalkulation(folder / "SE-99920.26_Kalkulation.xlsx", "SE-99920/26", positions=[R05_POSITIONS[0]])
    scan_and_inventory(db, str(env / "K5"))
    run_import(db)
    item = db.scalars(select(OfferItem)).one()
    assert item.weight == 400
    assert item.current_calculation.document.file_name == "SE-99920.26_Kalkulation-R01.xlsx"
    issues = {i.code: i for i in db.scalars(select(ReviewIssue))}
    assert "widerspruch" not in issues
    assert issues["mehrere_versionen"].severity == "info"


def test_helper_rules_for_numbers_walls_codes():
    from types import SimpleNamespace as Extracted_
    Extracted = lambda n, g, p, v: Extracted_(field_name=n, value=v, method="profil")  # noqa: E731
    from app.services.import_service import HETA_DRAWING_CODE_RE, _code_family, _drop_implausible_wall, _same_number
    assert _same_number("13533/26", "PT-13533.26") and _same_number("DE.-13423/25", "DE-13423.25")
    assert not _same_number("DE-13398/25", "PL-13437.25")
    f = {"diameter": Extracted("diameter", "item", 1, 273.0), "wall_thickness": Extracted("wall_thickness", "item", 1, 1200.0)}
    _drop_implausible_wall(f)
    assert f["wall_thickness"].value is None
    f = {"diameter": Extracted("diameter", "item", 1, 323.9), "wall_thickness": Extracted("wall_thickness", "item", 1, 4.5)}
    _drop_implausible_wall(f)
    assert f["wall_thickness"].value == 4.5
    assert _code_family("ASME SEC. VIII") == _code_family("ASME SEC. VIII DIV.1")
    assert _code_family("AD2000-MERKBLATT") == _code_family("AD 2000") and _code_family("DGRL 2014/68/EU") is None
    m = HETA_DRAWING_CODE_RE.search("513-F066_KFW-3-101.11-014.pdf")
    assert m and (m.group("prefix"), m.group("size")) == ("KFW", "101")
