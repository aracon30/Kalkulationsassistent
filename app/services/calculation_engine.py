"""Konfigurierbare Zuschlagskalkulation für Simulationen.

Unterscheidet ausdrücklich Kostenbasis, Zuschlagssatz, Zuschlagsbetrag, Selbstkosten,
Gewinnzuschlag, Verkaufspreis und Gewinnmarge. Das Schema kommt aus
``config/calculation_scheme.yaml`` und ist ausdrücklich als nicht validiert gekennzeichnet.
Simulationen überschreiben niemals importierte Originalwerte.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class SchemeLine(BaseModel):
    key: str
    label: str
    type: Literal["input", "product", "surcharge", "sum"]
    input: str | None = None
    a: str | None = None
    b: str | None = None
    base: list[str] = Field(default_factory=list)
    rate: str | None = None
    of: list[str] = Field(default_factory=list)
    subtotal: bool = False

    @model_validator(mode="after")
    def _check(self) -> "SchemeLine":
        need = {"input": [self.input], "product": [self.a, self.b], "surcharge": [self.rate], "sum": [True]}[self.type]
        if any(x is None for x in need) or (self.type == "surcharge" and not self.base) or (self.type == "sum" and not self.of):
            raise ValueError(f"Zeile '{self.key}' unvollständig für Typ {self.type}")
        return self


class Scheme(BaseModel):
    id: str
    name: str
    validated: bool = False
    defaults: dict[str, float | None] = Field(default_factory=dict)
    lines: list[SchemeLine]
    result_keys: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _refs(self) -> "Scheme":
        seen: set[str] = set()
        for line in self.lines:
            for ref in line.base + line.of:
                if ref not in seen:
                    raise ValueError(f"Zeile '{line.key}' bezieht sich auf '{ref}', das vorher nicht berechnet wird")
            seen.add(line.key)
        return self


def load_scheme(path: str | Path) -> Scheme:
    return Scheme(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@dataclass
class Line:
    key: str
    label: str
    kind: str
    base: float | None = None        # Kostenbasis (bei Zuschlägen)
    rate: float | None = None        # Zuschlagssatz
    amount: float | None = None      # Betrag
    formula: str = ""
    subtotal: bool = False
    missing: list[str] = field(default_factory=list)


@dataclass
class CalcResult:
    scheme_id: str
    scheme_validated: bool
    lines: list[Line]
    total_cost: float | None
    profit: float | None
    sales_price: float | None
    margin: float | None              # Gewinn / Verkaufspreis
    markup_on_cost: float | None      # Gewinn / Selbstkosten
    price_per_kg: float | None
    complete: bool
    missing_inputs: list[str]
    inputs: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def calculate(scheme: Scheme, inputs: dict[str, Any]) -> CalcResult:
    """Berechnet alle Zeilen. Fehlende Eingaben werden NICHT als 0 angenommen: abhängige Beträge bleiben leer."""
    values: dict[str, float | None] = {k: _num(v) for k, v in scheme.defaults.items()}
    for k, v in inputs.items():
        if k in values or k == "weight":
            values[k] = _num(v)
    amounts: dict[str, float | None] = {}
    lines: list[Line] = []
    missing_all: list[str] = []

    def fmt(x: float | None) -> str:
        return "?" if x is None else f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    labels = {sl.key: sl.label for sl in scheme.lines}

    for sl in scheme.lines:
        line = Line(sl.key, sl.label, sl.type, subtotal=sl.subtotal)
        if sl.type == "input":
            line.amount = values.get(sl.input)
            if line.amount is None:
                line.missing.append(sl.input)
            line.formula = f"Eingabe {sl.input}"
        elif sl.type == "product":
            a, b = values.get(sl.a), values.get(sl.b)
            line.missing = [n for n, v in ((sl.a, a), (sl.b, b)) if v is None]
            line.amount = None if line.missing else a * b
            line.formula = f"{fmt(a)} × {fmt(b)}"
        elif sl.type == "surcharge":
            bases = [amounts.get(k) for k in sl.base]
            line.base = None if any(x is None for x in bases) else sum(bases)
            line.rate = values.get(sl.rate)
            if line.rate is None:
                line.missing.append(sl.rate)
            line.amount = None if (line.base is None or line.rate is None) else line.base * line.rate
            line.formula = f"{fmt(line.base)} × {fmt(line.rate * 100)} %" if line.rate is not None else f"{fmt(line.base)} × ?"
        else:
            parts = [amounts.get(k) for k in sl.of]
            line.amount = None if any(x is None for x in parts) else sum(parts)
            line.missing = [k for k, v in zip(sl.of, parts) if v is None]
            line.formula = " + ".join(labels.get(k, k) for k in sl.of)
        amounts[sl.key] = line.amount
        missing_all.extend(m for m in line.missing if m not in missing_all and m in values)
        lines.append(line)

    rk = scheme.result_keys
    total = amounts.get(rk.get("total_cost", "total_cost"))
    profit = amounts.get(rk.get("profit", "risk_profit"))
    price = amounts.get(rk.get("sales_price", "sales_price"))
    weight = values.get("weight")
    return CalcResult(
        scheme_id=scheme.id, scheme_validated=scheme.validated, lines=lines, total_cost=total, profit=profit,
        sales_price=price,
        margin=(profit / price) if (profit is not None and price) else None,
        markup_on_cost=(profit / total) if (profit is not None and total) else None,
        price_per_kg=(price / weight) if (price is not None and weight) else None,
        complete=price is not None, missing_inputs=missing_all, inputs=values,
    )


def inputs_from_calculation(calc, item=None) -> dict[str, float | None]:
    """Vorbelegung einer Simulation aus einer importierten Kalkulation (nur lesend)."""
    out: dict[str, float | None] = {}
    if calc is not None:
        out.update({
            "material_cost": calc.material_cost,
            "purchased_parts_cost": calc.purchased_parts_cost,
            "manufacturing_hours": calc.manufacturing_hours,
            "manufacturing_hourly_rate": calc.manufacturing_hourly_rate,
            "engineering_hours": calc.engineering_hours,
            "documentation_cost": calc.documentation_cost,
            "inspection_cost": (calc.inspection_cost or 0) + (calc.ndt_tuev_cost or 0) if (calc.inspection_cost is not None or calc.ndt_tuev_cost is not None) else None,
            "surface_treatment_cost": calc.surface_treatment_cost,
            "procurement_cost": calc.procurement_cost,
        })
        if calc.engineering_hours and calc.engineering_cost:
            out["engineering_hourly_rate"] = calc.engineering_cost / calc.engineering_hours
        if calc.material_cost and calc.material_overhead is not None:
            out["material_overhead_rate"] = calc.material_overhead / calc.material_cost
        if calc.total_cost and calc.risk_profit is not None:
            out["risk_profit_rate"] = calc.risk_profit / calc.total_cost
    if item is not None:
        out["weight"] = item.weight
    return {k: v for k, v in out.items() if v is not None}
