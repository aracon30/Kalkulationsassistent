"""Vorkalkulation nach der HETA-Vorlage R05 (Formeln siehe config/heta_kalkulation.yaml).

Eingaben je Gehäuse. Leere Kostenbausteine zählen als 0 (wie in der Vorlage), werden aber als
"nicht ausgefüllt" gemeldet; ohne Materialkosten oder Fertigungsstunden ist das Ergebnis unvollständig.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.services.excel_reader import parse_number

REQUIRED = ("material_cost", "manufacturing_hours")
OPTIONAL = ("engineering_hours", "documentation_hours", "third_party_cost", "ndt_cost", "weight", "coating_cost",
            "packaging_cost", "shipping_cost")


def load_defaults(path: str | Path) -> dict[str, float]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {k: float(v) for k, v in (data.get("defaults") or {}).items()}


@dataclass
class Line:
    key: str
    label: str
    amount: float
    detail: str = ""
    subtotal: bool = False


@dataclass
class Result:
    lines: list[Line]
    total_cost: float
    risk_profit: float
    price_exw: float
    price_fca: float
    price_per_kg: float | None
    margin_on_price: float | None
    complete: bool
    missing: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    inputs: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _de(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def calculate(inputs: dict[str, Any], defaults: dict[str, float]) -> Result:
    v: dict[str, float | None] = {k: parse_number(inputs.get(k)) if inputs.get(k) not in (None, "") else None
                                  for k in set(REQUIRED) | set(OPTIONAL) | set(defaults)}
    for k, d in defaults.items():
        if v.get(k) is None:
            v[k] = d
    missing = [k for k in REQUIRED if v.get(k) is None]
    empty = [k for k in OPTIONAL if v.get(k) is None]
    g = lambda k: v.get(k) or 0.0  # noqa: E731

    material = g("material_cost")
    manufacturing = g("manufacturing_hours") * g("manufacturing_hourly_rate")
    engineering = g("engineering_hours") * g("engineering_hourly_rate")
    documentation = g("documentation_hours") * g("documentation_hourly_rate")
    third_party, ndt, coating = g("third_party_cost"), g("ndt_cost"), g("coating_cost")
    pickling = g("weight") * g("pickling_price_per_kg")
    mgk = material * g("material_overhead_rate")
    procurement = (third_party + ndt + pickling + coating) * g("procurement_rate")
    vgk = (material + mgk) * g("sales_overhead_rate")
    total = material + manufacturing + engineering + documentation + third_party + ndt + pickling + coating + mgk + procurement + vgk
    risk = total * g("risk_profit_rate")
    exw = total + risk
    rate = g("commission_rate")
    commission = exw / (1 - rate) - exw if rate < 1 else 0.0
    fca = exw + g("packaging_cost") + g("shipping_cost") + commission
    pct = lambda k: f"{g(k) * 100:.1f} %".replace(".", ",")  # noqa: E731

    lines = [
        Line("material", "Material Gesamtkosten", material),
        Line("manufacturing", "Fertigungskosten Schweißen", manufacturing, f"{g('manufacturing_hours'):g} h × {_de(g('manufacturing_hourly_rate'))} €/h"),
        Line("engineering", "Konstruktion", engineering, f"{g('engineering_hours'):g} h × {_de(g('engineering_hourly_rate'))} €/h"),
        Line("documentation", "Dokumentation", documentation, f"{g('documentation_hours'):g} h × {_de(g('documentation_hourly_rate'))} €/h"),
        Line("third_party", "Third Party (Vorprüfung, Berechnung, Druckprobe)", third_party),
        Line("ndt", "ZFP + TÜV", ndt),
        Line("pickling", "Beizen / Passivieren", pickling, f"{g('weight'):g} kg × {_de(g('pickling_price_per_kg'))} €/kg"),
        Line("coating", "Lackieren / Innenauskleidung", coating),
        Line("mgk", "Materialgemeinkosten", mgk, f"{_de(material)} × {pct('material_overhead_rate')}"),
        Line("procurement", "Beschaffungskosten Externer", procurement, f"{_de(third_party + ndt + pickling + coating)} × {pct('procurement_rate')}"),
        Line("vgk", "Vertriebsgemeinkosten", vgk, f"{_de(material + mgk)} × {pct('sales_overhead_rate')}"),
        Line("total", "Selbstkosten", total, subtotal=True),
        Line("risk", "Risiko + Gewinn", risk, f"{_de(total)} × {pct('risk_profit_rate')}"),
        Line("exw", "Preis EXW (Summe bei Gesamtabnahme)", exw, subtotal=True),
        Line("packaging", "Verpackung", g("packaging_cost")),
        Line("shipping", "Versand", g("shipping_cost")),
        Line("commission", "Provision", commission, pct("commission_rate")),
        Line("fca", "Preis FCA inkl. Provision", fca, subtotal=True),
    ]
    weight = v.get("weight")
    return Result(lines=lines, total_cost=total, risk_profit=risk, price_exw=exw, price_fca=fca,
                  price_per_kg=(fca / weight) if weight else None, margin_on_price=(risk / fca) if fca else None,
                  complete=not missing, missing=missing, empty=empty, inputs=v)
