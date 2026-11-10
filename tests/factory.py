"""Erzeugt SYNTHETISCHE Testdateien (keine HETA-Originaldaten!).

Die Struktur orientiert sich an der Auftragsbeschreibung (ein Angebot, zwei Apparate,
separate Fertigungszeitkalkulation, zwei Zeichnungen). Alle Preise sind erfunden und
dienen nur der Prüfung der Importlogik. Angebotsnummer: SE-99901.26 (Testnummernkreis).
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

import openpyxl

TEST_OFFER = "SE-99901.26"

POSITIONS = [
    {"pos": 1, "product_type": "Kerzenfilter", "description": "Kerzenfilter KF-300 (TESTDATEN)", "drawing": "KF-9-301.11-005",
     "dn": 300, "ps": 10, "ts": "-10/150", "material": "1.4404", "diameter": 323.9, "wall": 4, "height": 1850, "volume": 120,
     "elements": 7, "area": 5.74, "code": "AD 2000", "weight": 410, "material_cost": 8000.0, "purchased": 3000.0,
     "hours": 84.0, "rate": 65.0, "eng_hours": 12.0, "doc": 600.0, "ndt": 900.0, "surface": 450.0, "mgk": 0.10, "profit": 0.15,
     "ops": {"Zuschnitt": 6, "Vorbereitung": 8, "Rüsten": 4, "Heften": 10, "Schweißen": 30, "Schleifen": 12, "Montage": 6, "Prüfung": 4, "Verteilzeit": 4}},
    {"pos": 2, "product_type": "Beutelfilter", "description": "Beutelfilter BF-1000 (TESTDATEN)", "drawing": "BF-9-1001.11-001",
     "dn": 1000, "ps": 6, "ts": "-10/120", "material": "1.4404", "diameter": 1016, "wall": 6, "height": 2100, "volume": 1450,
     "elements": 12, "area": 5.52, "code": "AD 2000", "weight": 980, "material_cost": 14500.0, "purchased": 5200.0,
     "hours": 145.0, "rate": 65.0, "eng_hours": 18.0, "doc": 800.0, "ndt": 1400.0, "surface": 900.0, "mgk": 0.10, "profit": 0.15,
     "ops": {"Zuschnitt": 10, "Vorbereitung": 14, "Rüsten": 6, "Heften": 18, "Schweißen": 52, "Schleifen": 20, "Montage": 12, "Prüfung": 6, "Verteilzeit": 7}},
]


def expected_calc(p: dict) -> dict:
    mgk = p["material_cost"] * p["mgk"]
    mfg = p["hours"] * p["rate"]
    eng = p["eng_hours"] * 85
    total = p["material_cost"] + mgk + p["purchased"] + mfg + eng + p["doc"] + p["ndt"] + p["surface"]
    profit = total * p["profit"]
    return {"mgk": mgk, "mfg": mfg, "eng": eng, "total": total, "profit": profit, "price": total + profit}


def set_cached_values(path: Path, cached: dict[str, dict[str, object]]) -> None:
    """Schreibt 'zuletzt gespeicherte' Formelergebnisse in eine von openpyxl erzeugte Datei (wie Excel es täte)."""
    wb = openpyxl.load_workbook(path)
    index = {name: i + 1 for i, name in enumerate(wb.sheetnames)}
    wb.close()
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            for sheet, cells in cached.items():
                if item.filename == f"xl/worksheets/sheet{index[sheet]}.xml":
                    xml = data.decode("utf-8")
                    for addr, value in cells.items():
                        pattern = re.compile(rf'(<c r="{addr}"[^>]*>)(<f>.*?</f>)<v\s*/?>(?:</v>)?')
                        xml, n = pattern.subn(lambda m: f"{m.group(1)}{m.group(2)}<v>{value}</v>", xml)
                        assert n == 1, f"Formelzelle {sheet}!{addr} nicht gefunden"
                    data = xml.encode("utf-8")
            if item.filename == "xl/workbook.xml":  # wie von Excel gespeichert: keine erzwungene Neuberechnung
                data = re.sub(rb'\s*fullCalcOnLoad="(1|true)"', b"", data)
            if item.filename == "docProps/app.xml":
                data = re.sub(rb"<Application>[^<]*</Application>", b"<Application>Microsoft Excel</Application>", data)
            zout.writestr(item, data)
    tmp.replace(path)


def make_kalkulation(path: Path, offer_number: str = TEST_OFFER, positions: list[dict] | None = None, *,
                     with_cache: bool = True, stale_price: bool = False, customer: str = "Testkunde Chemie GmbH") -> Path:
    positions = positions or POSITIONS
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Deckblatt"
    ws.append(["TESTDATEN – synthetische Kalkulation"])
    for row in (["Angebotsnummer", offer_number], ["Kunde", customer], ["Datum", datetime(2026, 3, 12)], ["Land", "Deutschland"],
                ["Projekt", "Filteranlage Testprojekt"], ["Branche", "Chemie"], ["Währung", "EUR"]):
        ws.append(row)
    cache: dict[str, dict[str, object]] = {}
    for p in positions:
        s = wb.create_sheet(f"Pos. {p['pos']}")
        rows = [
            ["Position", p["pos"]], ["Produkttyp", p["product_type"]], ["Apparatebezeichnung", p["description"]],
            ["Zeichnungsnummer", p["drawing"]], ["DN", p["dn"]], ["Auslegungsdruck PS", p["ps"], "bar"],
            ["Auslegungstemperatur TS", p["ts"], "°C"], ["Werkstoff", p["material"]], ["Behälterdurchmesser", p["diameter"], "mm"],
            ["Wandstärke", p["wall"], "mm"], ["Apparatehöhe", p["height"], "mm"], ["Behältervolumen", p["volume"], "l"],
            ["Anzahl Filterelemente", p["elements"]], ["Filterfläche", p["area"], "m²"], ["Regelwerk", p["code"]],
            ["Gewicht", p["weight"], "kg"], [None],
        ]
        for r in rows:
            s.append(r)
        # Kalkulation ab Zeile 20
        s["A19"] = "Kalkulation"
        s["A20"], s["B20"], s["C20"] = "Materialkosten", p["material_cost"], "€"
        s["A21"], s["B21"], s["C21"] = "Materialgemeinkosten", p["mgk"], "=B20*B21"
        s["B21"].number_format = "0%"
        s["A22"], s["B22"] = "Zukaufteile", p["purchased"]
        s["A23"], s["B23"], s["C23"] = "Fertigungsstunden", p["hours"], "h"
        s["A24"], s["B24"], s["C24"] = "Fertigungsstundensatz", p["rate"], "€/h"
        s["A25"], s["B25"] = "Fertigungskosten", "=B23*B24"
        s["A26"], s["B26"] = "Konstruktionsstunden", p["eng_hours"]
        s["A27"], s["B27"] = "Konstruktionskosten", "=B26*85"
        s["A28"], s["B28"] = "Dokumentationskosten", p["doc"]
        s["A29"], s["B29"] = "ZFP/TÜV", p["ndt"]
        s["A30"], s["B30"] = "Oberflächenbehandlung", p["surface"]
        s["A31"], s["B31"] = "Selbstkosten", "=SUM(B20,C21,B22,B25,B27,B28,B29,B30)"
        s["A32"], s["B32"], s["C32"] = "Risiko-/Gewinnzuschlag", p["profit"], "=B31*B32"
        s["B32"].number_format = "0%"
        s["A33"], s["B33"] = "Angebotspreis", "=B31+C32"
        e = expected_calc(p)
        cache[s.title] = {"C21": e["mgk"], "B25": e["mfg"], "B27": e["eng"], "B31": e["total"], "C32": e["profit"],
                          "B33": e["price"] + (1000 if stale_price else 0)}
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    if with_cache:
        set_cached_values(path, cache)
    return path


def make_fertigungszeit(path: Path, offer_number: str = TEST_OFFER, positions: list[dict] | None = None, *, with_cache: bool = True) -> Path:
    positions = positions or POSITIONS
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fertigungszeit"
    ws["A1"], ws["B1"] = "Angebotsnummer", offer_number
    ws["A3"] = "Arbeitsgang"
    ops = list(positions[0]["ops"].keys())
    for j, p in enumerate(positions):
        ws.cell(row=3, column=2 + j, value=f"Pos. {p['pos']}")
    for i, op in enumerate(ops):
        ws.cell(row=4 + i, column=1, value=op)
        for j, p in enumerate(positions):
            ws.cell(row=4 + i, column=2 + j, value=p["ops"][op])
    total_row = 4 + len(ops)
    ws.cell(row=total_row, column=1, value="Gesamtfertigungszeit")
    cache = {}
    for j, p in enumerate(positions):
        col = openpyxl.utils.get_column_letter(2 + j)
        ws[f"{col}{total_row}"] = f"=SUM({col}4:{col}{total_row - 1})"
        cache[f"{col}{total_row}"] = sum(p["ops"].values())
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    if with_cache:
        set_cached_values(path, {"Fertigungszeit": cache})
    return path


def make_drawing_pdf(path: Path, p: dict) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=1191, height=842)  # A3 quer
    lines = [
        f"{p['product_type']} DN {p['dn']}  (TESTZEICHNUNG – synthetisch)",
        f"Zeichnungsnummer: {p['drawing']}",
        f"PS = {p['ps']} bar   TS = {p['ts'].replace('/', ' / ')} °C",
        f"Werkstoff: {p['material']}",
        f"Filterfläche: {str(p['area']).replace('.', ',')} m²",
        f"Anzahl Filterelemente: {p['elements']}",
        f"Regelwerk: {p['code']}",
        "Maßstab 1:10   Blatt 1/1   Gezeichnet: Test   Allgemeintoleranz ISO 2768-mK",
    ]
    y = 60
    for line in lines:
        page.insert_text((60, y), line, fontsize=14)
        y += 28
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def make_offer_pdf(path: Path, offer_number: str = TEST_OFFER) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate([f"Angebot {offer_number} (TESTDATEN)", "Gesamtpreis netto: siehe Positionen",
                              "Lieferzeit: 12 Wochen", "Zahlungsbedingungen: 30 Tage netto", "Gültigkeit: 3 Monate"]):
        page.insert_text((60, 80 + i * 22), line, fontsize=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def make_offer_folder(base: Path, offer_number: str = TEST_OFFER, year: str = "2026", **kw) -> Path:
    folder = base / year / offer_number
    make_kalkulation(folder / f"{offer_number}_Kalkulation.xlsx", offer_number, **kw)
    make_fertigungszeit(folder / f"{offer_number}_Fertigungszeit.xlsx", offer_number)
    make_offer_pdf(folder / "Angebot.pdf", offer_number)
    for p in POSITIONS:
        make_drawing_pdf(folder / f"{p['drawing']}.pdf", p)
    (folder / "Notizen.txt").write_text("Testnotiz", encoding="utf-8")
    return folder
