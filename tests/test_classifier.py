from app.models import ClassStatus, DocType
from app.services.document_classifier import classify_file
from tests.factory import POSITIONS, make_drawing_pdf, make_fertigungszeit, make_kalkulation, make_offer_pdf


def test_kalkulation_confirmed_by_content(tmp_path):
    c = classify_file(make_kalkulation(tmp_path / "SE-99901.26_Kalkulation.xlsx"))
    assert c.document_type == DocType.KALKULATION and c.status == ClassStatus.EINDEUTIG
    assert "Dateiname" in c.rule_text and "Kalkulationsbegriffe" in c.rule_text


def test_content_overrides_misleading_name(tmp_path):
    # Dateiname ohne Hinweis, Inhalt eindeutig Fertigungszeit
    c = classify_file(make_fertigungszeit(tmp_path / "Tabelle1.xlsx"))
    assert c.document_type == DocType.FERTIGUNGSZEIT
    assert c.status in (ClassStatus.WAHRSCHEINLICH, ClassStatus.EINDEUTIG)


def test_filename_alone_is_not_unique(tmp_path):
    p = tmp_path / "Kalkulation.xlsx"
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active["A1"] = "Hallo"
    wb.save(p)
    c = classify_file(p)
    assert c.document_type == DocType.KALKULATION and c.status == ClassStatus.WAHRSCHEINLICH


def test_drawing_and_offer_pdf(tmp_path):
    d = classify_file(make_drawing_pdf(tmp_path / "KF-9-301.11-005.pdf", POSITIONS[0]))
    assert d.document_type == DocType.ZEICHNUNG and d.status == ClassStatus.EINDEUTIG and d.product_hint == "Kerzenfilter"
    o = classify_file(make_offer_pdf(tmp_path / "Angebot.pdf"))
    assert o.document_type == DocType.KUNDENANGEBOT


def test_unsupported_and_corrupt(tmp_path):
    xls = tmp_path / "alt.xls"
    xls.write_bytes(b"\xd0\xcf\x11\xe0")
    c = classify_file(xls)
    assert c.status == ClassStatus.NICHT_UNTERSTUETZT
    bad = tmp_path / "SE-99901.26_Kalkulation.xlsx"
    bad.write_bytes(b"kein zip")
    c = classify_file(bad)
    assert c.status == ClassStatus.MANUELL and "nicht lesbar" in c.rule_text
