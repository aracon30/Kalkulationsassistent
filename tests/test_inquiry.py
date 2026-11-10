import json

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.services import heta_calc


@pytest.fixture()
def defaults(env):
    return heta_calc.load_defaults(get_settings().heta_calc_file)


@pytest.mark.parametrize("inputs,total,price", [
    # Werte aus der Originaldatei SE-13705.26 (Blätter KF-300 und BF-1000)
    ({"material_cost": 10758.5, "manufacturing_hours": 88, "engineering_hours": 40, "documentation_hours": 20,
      "third_party_cost": 2800, "ndt_cost": 500, "weight": 250}, 27890.516, 33468.6192),
    ({"material_cost": 22476.5, "manufacturing_hours": 156, "engineering_hours": 50, "documentation_hours": 20,
      "third_party_cost": 2800, "ndt_cost": 720, "weight": 1200}, 49475.544, 59370.6528),
])
def test_heta_calc_matches_original_template(defaults, inputs, total, price):
    r = heta_calc.calculate(inputs, defaults)
    assert r.total_cost == pytest.approx(total, abs=0.005)
    assert r.price_exw == pytest.approx(price, abs=0.005) and r.price_fca == pytest.approx(price, abs=0.005)
    assert r.complete and r.price_per_kg == pytest.approx(price / inputs["weight"])


def test_heta_calc_commission_and_missing(defaults):
    r = heta_calc.calculate({"material_cost": 1000, "manufacturing_hours": 10, "commission_rate": 0.1, "packaging_cost": 100}, defaults)
    lines = {l.key: l.amount for l in r.lines}
    assert lines["commission"] == pytest.approx(r.price_exw / 0.9 - r.price_exw)
    assert r.price_fca == pytest.approx(r.price_exw + 100 + lines["commission"])
    r = heta_calc.calculate({"manufacturing_hours": 10}, defaults)
    assert not r.complete and "material_cost" in r.missing


def test_inquiry_flow(client_with_data):
    client, db_session = client_with_data
    from app.models import Inquiry
    r = client.post("/anfragen", data={"title": "Test-Anfrage", "product_type": "Kerzenfilter", "dn": "300", "material": "316L",
                                       "filter_elements": "7", "quantity": "1"}, follow_redirects=False)
    assert r.status_code == 303
    url = r.headers["location"]
    page = client.get(url)
    assert page.status_code == 200
    t = page.text
    assert "Ähnliche Apparate" in t and "✓ gewählt" in t and "SE-99909.26" in t
    assert "Behältermantel" in t and "Ø323,9 x 4,5" in t           # Materialauszug der Vorlage
    assert 'name="manufacturing_hours"' in t and 'value="88"' in t  # Stunden aus der Vorlage
    inq = db_session.scalars(select(Inquiry)).one()
    assert inq.material == "1.4404" and inq.reference_item_id is not None
    # Speichern
    inputs = {"material_cost": 10000, "manufacturing_hours": 90, "engineering_hours": 40, "documentation_hours": 20, "weight": 250}
    r = client.post(f"/anfragen/{inq.id}/speichern", data={"calc_inputs_json": json.dumps(inputs),
                                                           "material_json": json.dumps([{"i": 0, "qty": 1, "price": 230, "length": 1.7, "apz": 35, "include": True}]),
                                                           "notes": "Lieferant anfragen", "status": "angeboten"}, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    inq = db_session.get(Inquiry, inq.id)
    assert inq.status == "angeboten" and inq.notes == "Lieferant anfragen" and inq.result_price > 0
    page = client.get(f"/anfragen/{inq.id}").text
    assert 'value="230,00"' in page and 'value="90"' in page   # gespeicherte Werte werden wieder geladen
    # Originaldaten unverändert
    from app.models import Calculation
    assert db_session.scalars(select(Calculation)).first().manufacturing_hours == 88
    assert client.get("/anfragen").status_code == 200
    r = client.post("/api/anfragen/berechnen", json={"inputs": inputs})
    assert r.status_code == 200 and r.json()["complete"] is True


def test_inquiry_requires_some_data(client_with_data):
    client, _ = client_with_data
    r = client.post("/anfragen", data={"title": "leer"}, follow_redirects=True)
    assert "mindestens" in r.text


@pytest.fixture()
def client_with_data(env):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services.jobs import manager
    from app.database import new_session
    from tests.factory import make_heta_r05_kalkulation
    make_heta_r05_kalkulation(env / "A" / "SE-99909.26-X" / "SE-99909.26_Kalkulation.xlsx", "SE-99909/26")
    with TestClient(create_app()) as c:
        manager.start("scan", str(env / "A"), wait=True)
        manager.start("import", None, wait=True)
        s = new_session()
        yield c, s
        s.close()


def test_template_lines_reproduce_original_material_cost(client_with_data):
    """Mit den Preisen der Vorlage ergibt der Materialauszug exakt die Materialkosten der Originalkalkulation."""
    _, db = client_with_data
    from app.models import OfferItem
    from app.services.inquiry import load_item, material_price_stats, template_lines
    stats = material_price_stats(db)
    for item in db.scalars(select(OfferItem)):
        item = load_item(db, item.id)
        lines = template_lines(db, item, stats)
        total = sum((l["length"] or 1) * l["qty"] * l["price_then"] + l["apz"] + l["mfg"] for l in lines)
        assert total == pytest.approx(item.current_calculation.material_cost)


def test_trade_calc_matches_options_sheet(defaults):
    from app.services import trade_calc
    r = trade_calc.calculate([{"qty": 7, "ek": 620}, {"qty": 12, "ek": 120}, {"qty": 1, "ek": 1200}, {"qty": 5}],
                             {"packaging_cost": 50}, defaults)
    # Originalwerte Blatt "Optionen" SE-13705.26: 6.749,568 / 2.239,488 / 1.866,24
    assert [round(l["vk_total"], 3) for l in r["lines"][:3]] == [6749.568, 2239.488, 1866.24]
    assert r["factor"] == pytest.approx(1.5552) and r["open"] == ["4"]
    assert r["total"] == pytest.approx(6749.568 + 2239.488 + 1866.24 + 50)


def test_trade_inquiry_flow(client_with_data):
    client, db = client_with_data
    from app.models import Inquiry
    r = client.post("/anfragen/handelsware", data={"title": "Ersatzkerzen", "customer": "Raffinerie", "reference_no": "BAKN26115231",
                                                   "due_date": "2026-11-17"}, follow_redirects=False)
    url = r.headers["location"]
    page = client.get(url).text
    assert "Ersatzteile / Handelsware" in page and "BAKN26115231" in page and "Frist 17.11.2026" in page
    # Suche findet Handelsware aus dem Blatt "Optionen" der importierten Kalkulation
    res = client.get("/api/handelsware/suche?q=ersatzkerzen").json()
    assert res["trade"] and res["trade"][0]["ek"] == 620
    res = client.get("/api/handelsware/suche?q=&d_min=60&d_max=70").json()
    assert any("Filterkerzen" in m["description"] or "Ersatzkerzen" in (m.get("description") or "") for m in res["material"] + res["trade"])
    calc = client.post("/api/handelsware/berechnen", json={"lines": [{"qty": "25", "ek": "18,50"}, {"qty": 96, "ek": None}], "rates": {}}).json()
    assert calc["lines"][0]["vk_unit"] == pytest.approx(18.5 * 1.5552) and calc["open"] == ["2"]
    data = {"lines": [{"pos": "1", "description": "Wickelkerze 2 µm Nylon/316, 61x27,5x762", "qty": 25, "unit": "Stk", "supplier": "Lieferant A", "ek": 18.5, "lead_time": "4 Wo"},
                      {"pos": "2+3", "description": "Wickelkerze 10 µm", "qty": 96, "unit": "Stk", "supplier": "", "ek": None, "lead_time": ""}],
            "rates": {"material_overhead_rate": 0.08, "sales_overhead_rate": 0.2, "risk_profit_rate": 0.2, "shipping_cost": 150}}
    inq_id = int(url.rsplit("/", 1)[1])
    r = client.post(f"/anfragen/{inq_id}/handelsware/speichern", data={"trade_json": json.dumps(data), "notes": "Beständigkeit 143 °C bestätigen lassen",
                                                                       "status": "in Arbeit", "title": "Ersatzkerzen", "customer": "Raffinerie",
                                                                       "reference_no": "BAKN26115231", "due_date": "17.11.2026"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    inq = db.get(Inquiry, inq_id)
    assert inq.kind == "handelsware" and inq.result_price == pytest.approx(25 * 18.5 * 1.5552 + 150)
    page = client.get(url).text
    assert "Wickelkerze 2" in page and "Beständigkeit" in page
    lst = client.get("/anfragen").text
    assert "Handelsware" in lst and "BAKN26115231" in lst
