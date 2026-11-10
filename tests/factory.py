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


# --- Nachbildung der HETA-Vorlage "Materialauszug / Kalkulation Behälterbau" (R05) -----------------
# Nur der AUFBAU entspricht der Originalvorlage; Kunde, Mengen und Preise sind erfunden.

R05_POSITIONS = [
    {"pos": 1, "sheet": "KF-300", "title": "Pos. 1 – Kerzenfilter KF-301.11.200 (TEST), DN300", "drawing": "KF-9-301.11-005",
     "mantel": ("Rohr, geschweißt", "Ø323,9 x 4,5", "1.4404"), "elements": ("Filterkerzen 5 µm TEST", 7), "hours": 88, "weight": 250,
     "material_rows": [("Behältermantel", "Rohr, geschweißt", "Ø323,9 x 4,5", 1, 1.7, "1.4404", 35, 220),
                       ("Kerzenboden", "Blech", "Ø314 x 15", 1, None, "1.4404", 35, 250),
                       ("Behälterflansch", "V-Flansch", "DN300 PN16", 1, None, "1.4404", 35, 450),
                       ("Filterkerzen 5 µm TEST", "Erstbestückung", "Ø63 x 1005", 7, None, "316L", 0, 720),
                       ("Schrauben + Muttern Deckel", "E-Teil +2", "M24", 14, None, "25CrMo4, verzinkt", 35, 8)], "chosen": 33750},
    {"pos": 2, "sheet": "BF-1000", "title": "Pos. 2 – Beutelfilter BF-1001.11.200 (TEST), DN1000", "drawing": "BF-9-1001.11-001",
     "mantel": ("Blech", "3190 x 1350 x 8", "1.4404"), "elements": ("Filterbeutel PTFE TEST", 12), "hours": 156, "weight": 1200,
     "material_rows": [("Behältermantel", "Blech", "3190 x 1350 x 8", 1, None, "1.4404", 35, 2065),
                       ("Behälterboden", "Klöpper", "Ø1016 x 8", 1, None, "1.4404", 35, 865),
                       ("Filterbeutel PTFE TEST", "E-Teil", "Ø180 x 810", 12, None, "PTFE", 0, 120)], "chosen": 59750,
     "boden": "Ø1016 x 8"},
]


def make_heta_r05_kalkulation(path: Path, offer_slash: str = "SE-99902/26", positions: list[dict] | None = None, *, extra_rows: int = 0) -> Path:
    positions = positions or R05_POSITIONS
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    cache: dict[str, dict[str, object]] = {}
    drawings = " / ".join(f"{p['drawing']} (Pos. {p['pos']})" for p in positions)
    for p in positions:
        ws = wb.create_sheet(p["sheet"])
        c: dict[str, object] = {}
        ws["A1"] = "Materialauszug / Kalkulation Behälterbau"
        ws["A2"], ws["C2"], ws["E2"] = "Kunde: Testkunde AB", "Benennung: Filtergehäuse TEST, DN200", f"Angebot Nr: {offer_slash}"
        ws["G2"], ws["O2"], ws["Q2"] = f"Zeichnungsnummer: {drawings}", "Datum: 04.11.2026", "Name: XX"
        for col, txt in zip("ABCDEFGHIJOQR", ["Pos. ", "Benennung", "Form", "Abmaße", "Anzahl", "Länge", "Norm", "Material",
                                                "APZ Kosten", "Einzelpreis Ø-Preis ( Stck / m)", "Kosten", "Fertigungskosten", "Gesamtkosten"]):
            ws[f"{col}3"] = txt
        ws["B4"] = p["title"]
        r = 5
        mat_total = 0.0
        rows = list(p["material_rows"]) + [(f"Zusatzteil {i}", "Blech", None, 1, None, "1.4404", 0, 10) for i in range(extra_rows)]
        for name, form, dims, qty, length, mat, apz, unit in rows:
            cost = (length or 1) * qty * unit + apz
            ws[f"A{r}"], ws[f"B{r}"], ws[f"C{r}"], ws[f"D{r}"], ws[f"E{r}"] = r - 4, name, form, dims, qty
            ws[f"F{r}"], ws[f"H{r}"], ws[f"I{r}"], ws[f"J{r}"] = length, mat, apz, unit
            ws[f"O{r}"], ws[f"R{r}"] = f"=((IF(F{r}=0,1,F{r}))*E{r}*J{r})+I{r}", f"=O{r}+Q{r}"
            bearb = 300 if name == "Kerzenboden" else 0  # Bearbeitungskosten in Spalte Q wie im Original
            if bearb:
                ws[f"Q{r}"] = bearb
            c[f"O{r}"], c[f"R{r}"] = cost, cost + bearb
            mat_total += cost + bearb
            r += 1
        last = r - 1
        r += 1
        m = r                                              # Material Gesamtkosten
        ws[f"A{m}"], ws[f"B{m}"], ws[f"O{m}"] = "HETA", "Material Gesamtkosten", f"=SUM(R5:R{last})"
        c[f"O{m}"] = mat_total
        f = m + 2                                          # Fertigung
        ws[f"B{f}"], ws[f"D{f}"], ws[f"E{f}"], ws[f"F{f}"], ws[f"G{f}"] = "Fertigungskosten Schweißen (R04)", "Stundensatz:", 65, "Anzahl Stunden", p["hours"]
        ws[f"H{f}"], ws[f"I{f}"], ws[f"O{f}"] = "Anzahl Gehäuse", 1, f"=I{f}*G{f}*E{f}"
        c[f"O{f}"] = p["hours"] * 65
        k = f + 2
        ws[f"B{k}"], ws[f"D{k}"], ws[f"E{k}"], ws[f"F{k}"], ws[f"G{k}"], ws[f"O{k}"] = "Konstruktion (R04)", "Stundensatz:", 75, 40, "[h]", f"=E{k}*F{k}"
        c[f"O{k}"] = 3000
        d = k + 2
        ws[f"B{d}"], ws[f"E{d}"], ws[f"F{d}"], ws[f"O{d}"] = "Dokumentation (R04)", 75, 20, f"=F{d}*E{d}"
        c[f"O{d}"] = 1500
        t = d + 2
        ws[f"B{t}"], ws[f"O{t}"] = "Third Party, Berechnung (Ab 2022 erhöhte Kosten) (R04)", 2800
        z = t + 2
        ws[f"B{z}"], ws[f"O{z}"] = "ZFP + TÜV (5€/Film) (R04)", 500
        b = z + 2
        ws[f"B{b}"], ws[f"D{b}"], ws[f"E{b}"], ws[f"F{b}"], ws[f"G{b}"] = "Beizen / Passivieren (R04)", "Preis pro Kilo", 1, "Gewicht in Kg", p["weight"]
        ws[f"H{b}"], ws[f"I{b}"], ws[f"O{b}"] = "Anzahl Gehäuse", f"=I{f}", f"=E{b}*G{b}*I{b}"
        c[f"I{b}"], c[f"O{b}"] = 1, p["weight"]
        h = b + 2
        ws[f"A{h}"], ws[f"B{h}"], ws[f"D{h}"], ws[f"F{h}"], ws[f"O{h}"] = "Hebesätze", "Materialgemeinkosten ", "Hebesatz", 0.08, f"=O{m}*F{h}"
        ws[f"F{h}"].number_format = "0%"
        mgk = mat_total * 0.08
        c[f"O{h}"] = mgk
        rg = h + 2
        pre = mat_total + mgk + p["hours"] * 65 + 3000 + 1500 + 2800 + 500 + p["weight"]
        ws[f"R{rg - 1}"] = f"=SUM(R{m}:R{rg - 2})"
        c[f"R{rg - 1}"] = pre
        ws[f"B{rg}"], ws[f"D{rg}"], ws[f"F{rg}"], ws[f"O{rg}"] = "Risiko + Gewinn", "Hebesatz", 0.2, f"=R{rg - 1}*F{rg}"
        ws[f"F{rg}"].number_format = "0%"
        c[f"O{rg}"] = pre * 0.2
        s1 = rg + 2
        ws[f"A{s1}"], ws[f"B{s1}"], ws[f"D{s1}"], ws[f"O{s1}"] = "Endpreise", "Summe bei Gesamtabnahme", "EX Works", f"=R{rg - 1}+O{rg}"
        c[f"O{s1}"] = pre * 1.2
        s2 = s1 + 4
        ws[f"B{s2}"], ws[f"D{s2}"], ws[f"O{s2}"] = "Summe bei Gesamtabnahme", "FCA Lich, inkl. Provision", f"=O{s1}"
        c[f"O{s2}"] = pre * 1.2
        if p.get("chosen") is not None:
            ws[f"N{s2 + 2}"], ws[f"O{s2 + 2}"] = "gewählt:", p["chosen"]
        ws[f"B{s2 + 3}"] = "R05_2023-05"
        # Spalte R ("Preis bei Abnahme 1 Stück") – bei Stückzahl 1 identisch mit Spalte O
        for row in (m, f, k, d, t, z, b, h, rg, s1, s2):
            ws[f"R{row}"] = f"=O{row}"
            c[f"R{row}"] = c.get(f"O{row}", ws[f"O{row}"].value)
        if p.get("quantity"):
            ws[f"I{f}"] = p["quantity"]
        cache[p["sheet"]] = c
    v = wb.create_sheet("Vergleich")
    v["B2"] = "Vergleich (TEST)"
    o = wb.create_sheet("Optionen")  # Handelsware wie im Original (Hebesätze 8 % / 20 % / 20 %)
    o["A1"] = f"{offer_slash} – Optionen (Handelsware, TEST)"
    for col, txt in zip("ABCDEFGHIJ", ["Pos.", "Benennung", "Anzahl", "EK je Stk [€]", "EK gesamt [€]", "MGK", "VGK", "Risiko + Gewinn", "VK [€]", "Quelle / Annahme"]):
        o[f"{col}3"] = txt
    for r, (pos, name, qty, ek, note) in enumerate([("1.1", "Ersatzkerzen 100 µm TEST, Ø63 x 1005", 7, 620, "Lieferant TEST Mail 30.10.2026"),
                                                    ("2.1", "Ersatzbeutel PTFE TEST", 12, 120, "Schätzwert – anfragen")], start=4):
        e = qty * ek
        f, g = e * 0.08, (e * 1.08) * 0.2
        h = (e + f + g) * 0.2
        for col, val in zip("ABCDEFGHIJ", [pos, name, qty, ek, e, f, g, h, e + f + g + h, note]):
            o[f"{col}{r}"] = val
    o["B7"] = "Gelb = Schätzwert, vor Angebotsabgabe beim Lieferanten anfragen."
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    set_cached_values(path, cache)
    return path


def make_heta_fertigungszeit(path: Path, offer_slash: str = "SE-99902/26", hours: tuple[float, float] = (88, 156)) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fertigungszeit"
    ws["A1"] = f"{offer_slash} – Fertigungszeit je Behälter"
    ws["A2"] = "TEST – Zeichnungen KF-9-301.11-005 (Pos. 1) / BF-9-1001.11-001 (Pos. 2). Werkstoff 1.4404."
    for col, txt in zip("ABCDE", ["Nr.", "Arbeitsgang", "Pos. 1 KF DN300 [h]", "Pos. 2 BF DN1000 [h]", "Bemerkung"]):
        ws[f"{col}4"] = txt
    steps = [("Schweißen (WIG-Wurzel + MAG), inkl. Nebenzeiten", 9.5, 25.0), ("Zuschnitt Mantel, Fasen", 2, 4),
             ("Wasserdruckprobe (Blindflansche setzen, füllen)", 6, 7), ("Endmontage (Kerzen bzw. Körbe/Beutel, Deckel)", 3, 4)]
    for i, (name, a, b) in enumerate(steps, start=5):
        ws[f"A{i}"], ws[f"B{i}"], ws[f"C{i}"], ws[f"D{i}"] = i - 4, name, a, b
    g = 5 + len(steps)
    ws[f"B{g}"], ws[f"C{g}"], ws[f"D{g}"] = "Grundzeit je Behälter", f"=SUM(C5:C{g - 1})", f"=SUM(D5:D{g - 1})"
    ws[f"B{g + 1}"], ws[f"C{g + 1}"], ws[f"D{g + 1}"] = "+ Verteilzeit (REFA)", f"=C{g}*0.15", f"=D{g}*0.15"
    ws[f"B{g + 2}"], ws[f"C{g + 2}"], ws[f"D{g + 2}"] = "Fertigungszeit je Behälter", f"=C{g}+C{g + 1}", f"=D{g}+D{g + 1}"
    ws[f"B{g + 3}"], ws[f"C{g + 3}"], ws[f"D{g + 3}"] = "Fertigungszeit gerundet [h]", hours[0], hours[1]
    ws[f"B{g + 5}"], ws[f"C{g + 5}"], ws[f"D{g + 5}"] = "Kosten Fertigung", "Pos. 1 KF", "Pos. 2 BF"
    p = wb.create_sheet("Parameter")
    p["A4"], p["C4"], p["D4"] = "Geometrie je Behälter", "Pos. 1 KF DN300", "Pos. 2 BF DN1000"
    sa, sb = sum(x[1] for x in steps), sum(x[2] for x in steps)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    set_cached_values(path, {"Fertigungszeit": {f"C{g}": sa, f"D{g}": sb, f"C{g + 1}": sa * 0.15, f"D{g + 1}": sb * 0.15,
                                                f"C{g + 2}": sa * 1.15, f"D{g + 2}": sb * 1.15}})
    return path


# --- Ersatzteil-/Handelswaren-Tabellen (Aufbau wie DE-13212.25 / DE-13457.25 / DE-13704.26, Werte erfunden) ---

def make_trade_table_simple(path: Path, offer_slash: str = "DE-99920/25") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tabelle1"
    ws["B2"], ws["A3"] = offer_slash, offer_slash
    for col, txt in zip("ABCDEFG", ["Bezeichnung", "Abmessung", "Feinheit", "EK", "VK", "HETA", "Bode"]):
        ws[f"{col}5"] = txt
    ws["A6"], ws["B6"], ws["C6"], ws["D6"], ws["E6"], ws["F6"], ws["G6"] = "Filterkerze", "Ø166 x 650", "1 µm", 200, 250, "=D6*1.2", "=F6/0.9"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    set_cached_values(path, {"Tabelle1": {"F6": 240, "G6": 240 / 0.9}})
    return path


def make_trade_table_assembly(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tabelle1"
    ws["A3"] = "Drehgelenk mit Dichtungssatz TYP TEST"
    ws.merge_cells("A3:I3")
    for col, txt in zip("ABCDEGHI", ["Bezeichnung", "Stückzahl", "Abmessungen", "EK", "EK Gesamt", "Stückpreis", "Fertigung", "VK Stück"]):
        ws[f"{col}5"] = txt
    ws["F5"] = 1.3
    parts = [("Drehgelenk Material", 3, "DN100", 300), ("O-Ring", 3, "Ø 104,14 x 5,33", 250), ("Stützring", 3, "Ø 105/113,8 x 2", 20)]
    cache = {}
    for i, (name, qty, dims, ek) in enumerate(parts, start=6):
        ws[f"A{i}"], ws[f"B{i}"], ws[f"C{i}"], ws[f"D{i}"], ws[f"E{i}"] = name, qty, dims, ek, f"=D{i}*B{i}"
        cache[f"E{i}"] = ek * qty
    last = 5 + len(parts)
    total = sum(p[1] * p[3] for p in parts)
    ws[f"A{last + 1}"], ws[f"E{last + 1}"] = "Gesamt", f"=SUM(E6:E{last})"
    cache[f"E{last + 1}"] = total
    ws["F6"], ws["G6"], ws["H6"], ws["I6"] = f"=E{last + 1}*F5", "=F6/B6", "=6*65", "=ROUND(G6+H6,0)"
    vk = round(total * 1.3 / 3 + 390)
    cache.update({"F6": total * 1.3, "G6": total * 1.3 / 3, "H6": 390, "I6": vk})
    for col in "FGHI":
        ws.merge_cells(f"{col}6:{col}{last}")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    set_cached_values(path, {"Tabelle1": cache})
    return path


def make_trade_table_factor(path: Path, offer_slash: str = "DE-99922/26") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tabelle1"
    ws["A2"] = offer_slash
    hdr = ["Bezeichnung", "Zeichnung / StüLi", "Stückzahl", "Abmessung", "Material", "zuletzt", "Stückzahl2", "EK", "VK",
           "EK / Stück", "EK Gesamt", "Faktor", "VK3", "Gesamt", "Lieferzeit"]
    for i, txt in enumerate(hdr, start=1):
        ws.cell(row=4, column=i, value=txt)
    rows = [("Kantenspaltelement TEST", "0000-Pos. 53", 1, "Ø180 x 500, 50 µm", None, None, None, None, None, 1600, 2.5, "12-13 Wochen"),
            ("O-Ring TEST", "0000-Pos. 3.11", 2, "Ø308 x 6", "NBR 70°", "K-00001/24", 100, None, 24, 5, 5, None)]
    cache = {}
    for r, (name, ref, qty, dims, mat, last, qty2, ek_old, vk_old, ek, fac, lt) in enumerate(rows, start=5):
        for c, v in zip(range(1, 10), [name, ref, qty, dims, mat, last, qty2, ek_old, vk_old]):
            ws.cell(row=r, column=c, value=v)
        ws[f"J{r}"], ws[f"K{r}"], ws[f"L{r}"], ws[f"M{r}"], ws[f"N{r}"], ws[f"O{r}"] = ek, f"=J{r}*C{r}", fac, f"=ROUND(J{r}*L{r},0)", f"=M{r}*C{r}", lt
        cache.update({f"K{r}": ek * qty, f"M{r}": round(ek * fac), f"N{r}": round(ek * fac) * qty})
    ws["K8"], ws["N8"] = "=SUM(K5:K6)", "=SUM(N5:N6)"
    cache.update({"K8": sum(r[9] * r[2] for r in rows), "N8": sum(round(r[9] * r[10]) * r[2] for r in rows)})
    ws["K10"], ws["L10"] = "Erlös", "=N8-K8"
    cache["L10"] = cache["N8"] - cache["K8"]
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    set_cached_values(path, {"Tabelle1": cache})
    return path


def make_service_calc(path: Path, offer_slash: str = "DE-99940/26", abroad: bool = False) -> Path:
    """Aufbau wie "Serviceeinsatz / Kalkulation" (DE-13511.26), Werte erfunden."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Serviceeinsatz"
    ws["A1"] = "Serviceeinsatz / Kalkulation "
    ws["A2"], ws["C2"], ws["F2"] = "Kunde: Testkunde", "Benennung: Wartung", f"Angebot Nr. {offer_slash}"
    for col, txt in zip("ABCDEG", ["Pos. ", "Benennung", "Deutschland", "Ausland", "Anzahl", "Gesamtkosten"]):
        ws[f"{col}3"] = txt
    rows = [("Service Experte", 140, 160, 8), ("Monteur", 112, 130, None), ("Kosten pro Tag", 85, 120, 1),
            ("Unterkunft pro Nacht", 120, 150, None), ("Reisekosten", 80, 300, None), ("Fahrkosten pro km", 1.3, 1.3, 40)]
    cache, total = {}, 0.0
    for i, (name, de, aus, qty) in enumerate(rows, start=4):
        ws[f"A{i}"], ws[f"B{i}"], ws[f"C{i}"], ws[f"D{i}"], ws[f"E{i}"] = i - 3, name, de, aus, qty
        rate = aus if abroad else de
        ws[f"G{i}"] = f"={'D' if abroad else 'C'}{i}*E{i}"
        cache[f"G{i}"] = rate * (qty or 0)
        total += cache[f"G{i}"]
    ws["B11"], ws["G11"], ws["G13"] = "Gesamt:", "=SUM(G4:G9)", 1500
    cache["G11"] = total
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    set_cached_values(path, {"Serviceeinsatz": cache})
    return path
