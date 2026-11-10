import pytest

from app.config import get_settings
from app.services.excel_importer import ProfileError, extract_workbook, load_profiles
from app.services.excel_reader import ExcelReadError, WorkbookData, parse_number
from app.services.formula_check import check_formula, evaluate
from tests.factory import POSITIONS, expected_calc, make_fertigungszeit, make_kalkulation


@pytest.fixture()
def profiles(env):
    return load_profiles(get_settings().profiles_dir)


def test_profiles_load_with_inheritance(profiles):
    assert {"generic_excel", "generic_fertigungszeit", "heta_2025", "heta_2026", "heta_fertigungszeit"} <= set(profiles)
    p = profiles["heta_2026"]
    assert p.calculation_fields["offer_price"].labels[0] == "Angebotspreis"  # geerbt
    assert p.profile.validated is False


def test_profile_errors(tmp_path):
    (tmp_path / "a.yaml").write_text("extends: fehlt\nprofile: {id: a, name: A, document_type: x}\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profiles(tmp_path)


def test_multi_position_workbook(profiles, tmp_path):
    path = make_kalkulation(tmp_path / "SE-99901.26_Kalkulation.xlsx")
    ex = extract_workbook(path, profiles, "angebotskalkulation")
    assert ex.profile_id == "heta_2026"
    assert ex.position_mode == "sheet_per_position"
    assert sorted(ex.positions) == [1, 2]
    assert ex.header["customer"].value == "Testkunde Chemie GmbH"
    for p in POSITIONS:
        f = ex.positions[p["pos"]]
        e = expected_calc(p)
        assert f["dn"].value == p["dn"]
        assert f["material"].value == p["material"]
        assert f["filter_elements"].value == p["elements"]
        assert f["filter_area"].value == pytest.approx(p["area"])
        assert f["design_temperature"].value == (-10.0, float(p["ts"].split("/")[1]))
        assert f["manufacturing_hours"].value == p["hours"]
        assert f["offer_price"].value == pytest.approx(e["price"])
        # Zuschlagssatz (Prozentzelle) wird übersprungen, Betrag gelesen
        assert f["material_overhead"].value == pytest.approx(e["mgk"])
        assert f["material_overhead"].cell == "C21"
        # Quellenreferenz und Formel
        price = f["offer_price"]
        assert (price.sheet, price.cell, price.formula) == (f"Pos. {p['pos']}", "B33", "=B31+C32")
        assert price.extra.get("formula_verified") is True
        assert not price.issues


def test_missing_formula_cache_is_flagged_not_zero(profiles, tmp_path):
    path = make_kalkulation(tmp_path / "SE-99901.26_Kalkulation.xlsx", with_cache=False)
    ex = extract_workbook(path, profiles, "angebotskalkulation")
    price = ex.positions[1]["offer_price"]
    assert price.value is None  # niemals 0
    assert price.formula == "=B31+C32"
    assert any(i.code == "formel_ohne_cache" for i in price.issues)
    assert ex.positions[1]["material_cost"].value == 8000  # einfache Werte bleiben lesbar


def test_stale_formula_cache_detected(profiles, tmp_path):
    path = make_kalkulation(tmp_path / "SE-99901.26_Kalkulation.xlsx", stale_price=True)
    ex = extract_workbook(path, profiles, "angebotskalkulation")
    price = ex.positions[1]["offer_price"]
    assert any(i.code == "formel_cache_abweichung" for i in price.issues)
    assert price.confidence <= 0.4


def test_column_per_position_fertigungszeit(profiles, tmp_path):
    path = make_fertigungszeit(tmp_path / "SE-99901.26_Fertigungszeit.xlsx")
    ex = extract_workbook(path, profiles, "fertigungszeitkalkulation")
    assert ex.position_mode == "column_per_position"
    assert ex.operations[1]["gesamt"].value == 84
    assert ex.operations[2]["gesamt"].value == 145
    assert ex.operations[2]["schweissen"].value == 52
    assert ex.operations[1]["ruesten"].display_name == "Rüsten"


def test_corrupt_workbook_raises_readable_error(tmp_path):
    bad = tmp_path / "kaputt.xlsx"
    bad.write_bytes(b"PK\x03\x04 kaputt")
    with pytest.raises(ExcelReadError) as exc:
        WorkbookData(bad)
    assert exc.value.kind == "beschaedigt"


def test_original_file_unchanged(profiles, tmp_path):
    import hashlib
    path = make_kalkulation(tmp_path / "SE-99901.26_Kalkulation.xlsx")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    mtime = path.stat().st_mtime_ns
    extract_workbook(path, profiles, "angebotskalkulation")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert path.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("raw,expected", [
    ("33.629,33", 33629.33), ("5,74 m²", 5.74), ("84 h", 84.0), ("1.4404", 1.4404), (" 1 234,5 € ", 1234.5),
    ("60916.66", 60916.66), ("abc", None), (None, None), (12, 12.0), ("1.000", 1000.0),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_formula_evaluator():
    cells = {("S", "A1"): (10, False), ("S", "A2"): (5, False), ("S", "A3"): (None, False), ("T 1", "B1"): (2, False), ("S", "A4"): (None, True)}
    look = lambda sh, a: cells.get((sh, a), (None, False))
    assert evaluate("=SUM(A1:A3)*'T 1'!B1", "S", look) == 30
    assert evaluate("=ROUND(A1/3,2)+10%", "S", look) == pytest.approx(3.43)
    assert evaluate("=VLOOKUP(A1,B:C,2)", "S", look) is None
    assert evaluate("=A4+1", "S", look) is None  # referenzierte Formel ohne Cache
    assert check_formula("=A1+A2", 15, "S", look).consistent is True
    assert check_formula("=A1+A2", 16, "S", look).consistent is False


def test_optional_recalculation_only_on_copy(tmp_path):
    import hashlib
    import shutil
    from app.services.excel_reader import find_soffice, recalculate_copy
    if not find_soffice():
        pytest.skip("LibreOffice nicht installiert – optionale Neuberechnung nicht verfügbar")
    path = make_kalkulation(tmp_path / "SE-99901.26_Kalkulation.xlsx", with_cache=False)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    out = recalculate_copy(path)
    try:
        assert out is not None and out.parent != path.parent
        with WorkbookData(out) as wb:
            assert wb.cell("Pos. 1", "B33").value == pytest.approx(expected_calc(POSITIONS[0])["price"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    finally:
        if out:
            shutil.rmtree(out.parent.parent, ignore_errors=True)
