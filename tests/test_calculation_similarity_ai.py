import asyncio

import pytest

from app.config import get_settings
from app.services.similarity import load_config, rank


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
