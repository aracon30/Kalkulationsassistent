"""Exportiert den AUFBAU von Excel-Dateien als Textdatei – zur Anpassung der Importprofile.

Aufruf (im Programmordner, mit der .venv-Python):
    .venv\\Scripts\\python scripts\\struktur_export.py "K:\\...\\SE-13705.26_Kalkulation.xlsx" [weitere Dateien] [--ohne-werte]

Ausgabe: je Datei eine Textdatei in samples\\ (nicht in Git) mit
  - Tabellenblättern, Größe, verbundenen Zellen
  - jeder belegten Zelle: Adresse, Art (Text/Zahl/Formel/Datum), Inhalt, Formel, gespeichertes Ergebnis, Zahlenformat
  - dem, was das aktuelle Importprofil daraus liest (Feld -> Zelle -> Wert)
Mit --ohne-werte werden Zahlen und Formelergebnisse durch <Zahl> ersetzt (Beschriftungen bleiben sichtbar;
Kundennamen in Textzellen bitte bei Bedarf vor dem Weitergeben entfernen).
Die Originaldateien werden nur gelesen (Auswertung auf einer temporären Kopie).
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl.utils import get_column_letter  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.document_classifier import classify_file  # noqa: E402
from app.services.excel_importer import Extractor, load_profiles, select_profile  # noqa: E402
from app.services.excel_reader import WorkbookData  # noqa: E402

MAX_CELLS_PER_SHEET = 6000


def fmt(v, hide: bool) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return "<Zahl>" if hide else repr(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return repr(str(v))


def export(path: Path, hide: bool) -> Path:
    lines = [f"# Struktur: {path.name}", f"# Werte ausgeblendet: {'ja' if hide else 'nein'}", ""]
    cls = classify_file(path)
    lines += [f"Klassifizierung: {cls.document_type} ({cls.status}) – {cls.rule_text}", ""]
    with WorkbookData(path) as wb:
        lines.append(f"Gespeichert mit: {wb.application or '?'}; Neuberechnung beim Öffnen erzwungen: {wb.full_calc_on_load}")
        for sheet in wb.sheetnames:
            ws = wb.wb_formula[sheet]
            merged = ", ".join(str(r) for r in list(ws.merged_cells.ranges)[:80])
            lines += ["", f"## Blatt '{sheet}'  ({ws.max_row} Zeilen × {ws.max_column} Spalten, Status: {ws.sheet_state})"]
            if merged:
                lines.append(f"Verbundene Zellen: {merged}")
            n = 0
            for row in ws.iter_rows():
                for c in row:
                    if c.value is None:
                        continue
                    n += 1
                    if n > MAX_CELLS_PER_SHEET:
                        break
                    info = wb.cell(sheet, c.coordinate)
                    nf = f"  [Format {info.number_format}]" if info.number_format not in ("General", None) else ""
                    if info.formula:
                        lines.append(f"{c.coordinate:>7}  Formel  {info.formula}  = {fmt(info.value, hide)}{nf}")
                    elif isinstance(info.raw, str):
                        lines.append(f"{c.coordinate:>7}  Text    {info.raw!r}")
                    elif isinstance(info.raw, (datetime, date)):
                        lines.append(f"{c.coordinate:>7}  Datum   {fmt(info.raw, hide)}")
                    else:
                        lines.append(f"{c.coordinate:>7}  Zahl    {fmt(info.raw, hide)}{nf}")
                if n > MAX_CELLS_PER_SHEET:
                    lines.append(f"... (mehr als {MAX_CELLS_PER_SHEET} Zellen, abgeschnitten)")
                    break

        lines += ["", "## Ergebnis des aktuellen Importprofils"]
        profiles = load_profiles(get_settings().profiles_dir)
        profile, reason = select_profile(profiles, wb, path.name, cls.document_type)
        if profile is None:
            lines.append(reason)
        else:
            ex = Extractor(wb, profile).run(reason)
            lines.append(f"{reason}; Positionsmodus: {ex.position_mode}")
            for name, e in ex.header.items():
                lines.append(f"  Kopf  {name:28} {e.source_ref:16} {fmt(e.value, hide and not isinstance(e.value, str))}  (Beschriftung {e.label!r})")
            for pos in sorted(set(ex.positions) | set(ex.operations)):
                for name, e in {**ex.positions.get(pos, {}), **ex.operations.get(pos, {})}.items():
                    lines.append(f"  Pos {pos} {name:28} {e.source_ref:16} {fmt(e.value, hide and not isinstance(e.value, str))}  (Beschriftung {e.label!r})")
            for i in ex.issues:
                lines.append(f"  Hinweis [{i.code}] {i.message}")

    out_dir = ROOT / "samples"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"struktur_{path.stem}.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    hide = "--ohne-werte" in argv
    files = [Path(a) for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    for f in files:
        try:
            print(f"Geschrieben: {export(f, hide)}")
        except Exception as exc:  # noqa: BLE001
            print(f"FEHLER bei {f}: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
