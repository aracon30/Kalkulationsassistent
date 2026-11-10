"""Verkaufspreis für Handelsware nach dem HETA-Blatt "Optionen" (SE-13705.26):

    EK gesamt = Menge × EK je Stück
    MGK       = EK gesamt × MGK-Satz                    (8 %)
    VGK       = (EK gesamt + MGK) × VGK-Satz            (20 %)
    Risiko + Gewinn = (EK gesamt + MGK + VGK) × Satz    (20 %)
    VK gesamt = EK gesamt + MGK + VGK + Risiko/Gewinn   -> Faktor 1,08 × 1,2 × 1,2 = 1,5552

Pauschalen (Verpackung, Versand, Zertifikate/Dokumente) werden wie in der Apparatekalkulation
ohne Zuschlag auf die Summe addiert. Positionen ohne EK sind "offen" und fehlen in der Summe.
"""
from __future__ import annotations

from typing import Any

from app.services.excel_reader import parse_number


def _n(v) -> float | None:
    return parse_number(v) if v not in (None, "") else None


def calculate(lines: list[dict[str, Any]], rates: dict[str, Any], defaults: dict[str, float]) -> dict:
    mgk_r = _n(rates.get("material_overhead_rate"))
    vgk_r = _n(rates.get("sales_overhead_rate"))
    rg_r = _n(rates.get("risk_profit_rate"))
    mgk_r = defaults.get("material_overhead_rate", 0.08) if mgk_r is None else mgk_r
    vgk_r = defaults.get("sales_overhead_rate", 0.20) if vgk_r is None else vgk_r
    rg_r = defaults.get("risk_profit_rate", 0.20) if rg_r is None else rg_r
    factor = (1 + mgk_r) * (1 + vgk_r) * (1 + rg_r)
    out_lines, open_lines = [], []
    sum_ek = sum_vk = 0.0
    for idx, l in enumerate(lines):
        qty, ek = _n(l.get("qty")), _n(l.get("ek"))
        r = {"i": idx, "pos": l.get("pos") or str(idx + 1), "qty": qty, "ek": ek, "ek_total": None, "mgk": None, "vgk": None,
             "risk": None, "vk_total": None, "vk_unit": None}
        if qty is None or ek is None:
            open_lines.append(r["pos"])
        else:
            ek_total = qty * ek
            mgk = ek_total * mgk_r
            vgk = (ek_total + mgk) * vgk_r
            risk = (ek_total + mgk + vgk) * rg_r
            vk_total = ek_total + mgk + vgk + risk
            r.update(ek_total=ek_total, mgk=mgk, vgk=vgk, risk=risk, vk_total=vk_total, vk_unit=vk_total / qty if qty else None)
            sum_ek += ek_total
            sum_vk += vk_total
        out_lines.append(r)
    extras = {k: (_n(rates.get(k)) or 0.0) for k in ("packaging_cost", "shipping_cost", "certificate_cost")}
    total = sum_vk + sum(extras.values())
    return {"lines": out_lines, "factor": factor, "sum_ek": sum_ek, "sum_vk": sum_vk, "extras": extras, "total": total,
            "margin": ((sum_vk - sum_ek) / sum_vk) if sum_vk else None, "open": open_lines,
            "rates": {"material_overhead_rate": mgk_r, "sales_overhead_rate": vgk_r, "risk_profit_rate": rg_r}}
