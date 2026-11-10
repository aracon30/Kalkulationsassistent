"""Regelbasierte, nachvollziehbare Ähnlichkeitssuche über historische Angebotspositionen.

Gesamtähnlichkeit = Σ(w_i · s_i) / Σ(w_i) über alle Merkmale, die in Anfrage UND
historischer Position vorhanden sind. Fehlende Merkmale fließen nicht als 0 ein.
Abdeckung = Σ(w_i verglichen) / Σ(w_i angefragt). Rangwert = Ähnlichkeit × Abdeckung –
so rangiert eine Position mit wenigen bekannten Merkmalen nicht vor einer gut vergleichbaren.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from app.services.normalize import material_group, normalize_material, normalize_product_type


class FeatureConfig(BaseModel):
    weight: float = 1.0
    kind: Literal["numeric", "category", "material"] = "numeric"
    tolerance_factor: float = 2.0


class SimilarityConfig(BaseModel):
    features: dict[str, FeatureConfig]
    min_coverage: float = 0.4
    top_n: int = 5


def load_config(path: str | Path) -> SimilarityConfig:
    return SimilarityConfig(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@dataclass
class FeatureScore:
    feature: str
    query: Any
    candidate: Any
    weight: float
    score: float | None   # None = nicht vergleichbar (fehlt)
    note: str = ""


@dataclass
class Match:
    item_id: int
    similarity: float
    coverage: float
    rank_score: float
    details: list[FeatureScore] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def _numeric(a: float, b: float, tol: float) -> tuple[float, str]:
    if a == b:
        return 1.0, "gleich"
    if a <= 0 or b <= 0:
        return (1.0 if a == b else 0.0), "Wert ≤ 0"
    s = max(0.0, 1.0 - abs(math.log(a / b)) / math.log(tol))
    return s, f"Abweichung {((b - a) / a) * 100:+.0f} %"


def score_feature(name: str, cfg: FeatureConfig, q: Any, c: Any) -> FeatureScore:
    if q is None or q == "" or c is None or c == "":
        return FeatureScore(name, q, c, cfg.weight, None, "fehlt – nicht bewertet")
    if cfg.kind == "category":
        qn, _ = normalize_product_type(str(q))
        cn, _ = normalize_product_type(str(c))
        same = (qn or "").lower() == (cn or "").lower()
        return FeatureScore(name, q, c, cfg.weight, 1.0 if same else 0.0, "gleich" if same else "anderer Typ")
    if cfg.kind == "material":
        qn, _ = normalize_material(str(q))
        cn, _ = normalize_material(str(c))
        if qn == cn:
            return FeatureScore(name, q, c, cfg.weight, 1.0, "gleicher Werkstoff")
        gq, gc = material_group(qn), material_group(cn)
        if gq and gq == gc:
            return FeatureScore(name, q, c, cfg.weight, 0.6, f"gleiche Werkstoffgruppe ({gq})")
        return FeatureScore(name, q, c, cfg.weight, 0.0, "anderer Werkstoff")
    try:
        s, note = _numeric(float(q), float(c), cfg.tolerance_factor)
    except (TypeError, ValueError):
        return FeatureScore(name, q, c, cfg.weight, None, "nicht numerisch")
    return FeatureScore(name, q, c, cfg.weight, s, note)


def rank(query: dict[str, Any], candidates: list[dict[str, Any]], config: SimilarityConfig, top_n: int | None = None) -> list[Match]:
    requested = {k: v for k, v in query.items() if k in config.features and v not in (None, "")}
    if not requested:
        return []
    req_weight = sum(config.features[k].weight for k in requested)
    matches: list[Match] = []
    for cand in candidates:
        details = [score_feature(k, config.features[k], requested.get(k), cand.get(k)) for k in config.features]
        used = [d for d in details if d.score is not None and d.feature in requested]
        if not used:
            continue
        w = sum(d.weight for d in used)
        sim = sum(d.weight * d.score for d in used) / w
        coverage = w / req_weight
        m = Match(cand["item_id"], round(sim, 4), round(coverage, 4), round(sim * coverage, 4), details, dict(cand))
        m.data["low_coverage"] = coverage < config.min_coverage
        matches.append(m)
    matches.sort(key=lambda m: (m.rank_score, m.similarity), reverse=True)
    return matches[: (top_n or config.top_n)]
