import asyncio

import pytest

from app.config import get_settings
from app.services.calculation_engine import Scheme, calculate, load_scheme
from app.services.similarity import load_config, rank


@pytest.fixture()
def scheme(env):
    return load_scheme(get_settings().simulation_scheme_file)


def test_scheme_is_marked_unvalidated(scheme):
    assert scheme.validated is False


def test_calculation_distinguishes_base_rate_amount(scheme):
    r = calculate(scheme, {"material_cost": 10000, "manufacturing_hours": 100, "manufacturing_hourly_rate": 60,
                           "engineering_hours": 10, "engineering_hourly_rate": 80, "material_overhead_rate": 0.1,
                           "sales_overhead_rate": 0.05, "risk_profit_rate": 0.1, "weight": 500})
    lines = {l.key: l for l in r.lines}
    assert lines["material_overhead"].base == 10000 and lines["material_overhead"].rate == 0.1 and lines["material_overhead"].amount == 1000
    production = 10000 + 1000 + 0 + 6000 + 0 + 800
    assert lines["production_cost"].amount == pytest.approx(production)
    assert r.total_cost == pytest.approx(production * 1.05)
    assert r.profit == pytest.approx(production * 1.05 * 0.1)
    assert r.sales_price == pytest.approx(production * 1.05 * 1.1)
    assert r.margin == pytest.approx(r.profit / r.sales_price)          # Marge bezogen auf Verkaufspreis
    assert r.markup_on_cost == pytest.approx(0.1)                       # Zuschlag bezogen auf Selbstkosten
    assert r.price_per_kg == pytest.approx(r.sales_price / 500)


def test_missing_inputs_are_not_zero(scheme):
    r = calculate(scheme, {"manufacturing_hours": 80})  # Materialkosten fehlen
    assert r.sales_price is None and r.total_cost is None
    assert "material_cost" in r.missing_inputs
    assert not r.complete


def test_scheme_validation_rejects_forward_reference():
    with pytest.raises(ValueError):
        Scheme(id="x", name="x", lines=[{"key": "a", "label": "A", "type": "sum", "of": ["b"]},
                                        {"key": "b", "label": "B", "type": "input", "input": "b"}])


def test_simulation_does_not_touch_original(db, offer_base):
    from sqlalchemy import select
    from app.models import Calculation
    from app.services.calculation_engine import inputs_from_calculation
    from app.services.import_service import run_import, scan_and_inventory
    scan_and_inventory(db, str(offer_base)); run_import(db)
    calc = db.scalars(select(Calculation)).first()
    before = calc.offer_price
    inputs = inputs_from_calculation(calc, calc.offer_item)
    inputs["material_cost"] = 1.0
    calculate(load_scheme(get_settings().simulation_scheme_file), inputs)
    db.refresh(calc)
    assert calc.offer_price == before
    assert inputs_from_calculation(calc)["material_overhead_rate"] == pytest.approx(0.10)


CANDIDATES = [
    {"item_id": 1, "product_type": "Kerzenfilter", "dn": 400, "design_pressure": 16, "material": "1.4571", "filter_elements": 24, "filter_area": None, "weight": None},
    {"item_id": 2, "product_type": "Kerzenfilter", "dn": 300, "design_pressure": 10, "material": "1.4404", "filter_elements": 7, "filter_area": 5.74, "weight": 410},
    {"item_id": 3, "product_type": "Beutelfilter", "dn": 400, "design_pressure": 16, "material": "1.4571", "filter_elements": 24, "filter_area": None, "weight": None},
    {"item_id": 4, "product_type": "Kerzenfilter", "dn": None, "design_pressure": None, "material": None, "filter_elements": None, "filter_area": None, "weight": None},
]


@pytest.fixture()
def sim_cfg(env):
    return load_config(get_settings().similarity_file)


def test_similarity_ranking(sim_cfg):
    q = {"product_type": "Kerzenfilter", "dn": 400, "design_pressure": 16, "material": "1.4571", "filter_elements": 24}
    res = rank(q, CANDIDATES, sim_cfg)
    assert [m.item_id for m in res][:3] == [1, 2, 3]
    assert res[0].similarity == 1.0 and res[0].coverage == 1.0
    # Werkstoffgruppe: 1.4404 ~ 1.4571 (Austenit) -> Teilähnlichkeit
    mat = next(d for d in res[1].details if d.feature == "material")
    assert 0 < mat.score < 1


def test_missing_features_are_excluded_not_zero(sim_cfg):
    q = {"product_type": "Kerzenfilter", "dn": 400}
    res = {m.item_id: m for m in rank(q, CANDIDATES, sim_cfg, top_n=10)}
    # Kandidat 4 hat nur den Produkttyp: Ähnlichkeit 1.0 auf vorhandenen Merkmalen, aber geringe Abdeckung
    assert res[4].similarity == 1.0 and res[4].coverage < 1.0
    assert res[1].rank_score > res[4].rank_score
    dn = next(d for d in res[4].details if d.feature == "dn")
    assert dn.score is None and "fehlt" in dn.note


def test_ai_defaults_to_no_provider(env, monkeypatch):
    from app import ai, config
    assert ai.get_provider().name == "none"
    monkeypatch.setenv("HETA_AI_PROVIDER", "azure_foundry")
    config.reset_settings()
    assert ai.get_provider().name == "none"  # unvollständig konfiguriert
    monkeypatch.setenv("HETA_AZURE_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("HETA_AZURE_DEPLOYMENT", "x")
    monkeypatch.setenv("HETA_AZURE_API_KEY", "geheim-123456")
    config.reset_settings()
    assert ai.get_provider().name == "none"  # extern nicht freigegeben
    monkeypatch.setenv("HETA_AI_EXTERNAL_ENABLED", "true")
    config.reset_settings()
    assert ai.get_provider().name == "azure_foundry"


def test_mock_provider_returns_validated_schema(env):
    from app.ai import DocumentPayload, MockAIProvider
    res = asyncio.run(MockAIProvider().extract_document(DocumentPayload(1, "x.pdf", "Kerzenfilter DN 300, PS = 10 bar, 1.4404"), ["dn", "material", "weight"]))
    vals = {f.field_name: f.value for f in res.fields}
    assert vals == {"dn": "300", "material": "1.4404", "weight": None}
