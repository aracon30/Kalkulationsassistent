"""Lesender Zugriff auf Excel-Arbeitsmappen.

- Die Originaldatei wird nur gelesen und in eine temporäre Kopie kopiert.
- Die Mappe wird zweimal geladen: ``data_only=False`` (Formeln) und ``data_only=True``
  (zuletzt von Excel gespeicherte Ergebniswerte). openpyxl berechnet keine Formeln.
- Makros werden nie ausgeführt (openpyxl führt keinen VBA-Code aus; ``keep_vba=False``).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException


class ExcelReadError(Exception):
    def __init__(self, message: str, kind: str = "fehler"):
        super().__init__(message)
        self.kind = kind  # gesperrt | beschaedigt | zugriff_verweigert | fehler


def normalize_label(text: Any) -> str:
    """Vereinheitlicht Zellbeschriftungen für den Vergleich."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower().strip()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", s)  # Einheiten in Klammern entfernen
    s = re.sub(r"[:;=]+$", "", s.strip())
    s = re.sub(r"[\s_]+", " ", s)
    return s.strip(" .:-")


_NUMBER_RE = re.compile(r"^[-+]?(\d{1,3}([.\s']\d{3})+|\d+)([.,]\d+)?$")


def parse_number(value: Any) -> float | None:
    """Zahl aus Zellwert; versteht deutsche Schreibweise (``33.629,33``, ``5,74 m²``)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (datetime, date)):
        return None
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"(?i)(€|eur|euro|chf|usd|\$|m²|m2|m³|m3|kg|mm|bar\(?g?\)?|°c|h|std\.?|stk\.?|stück|%|l)$", "", s).strip()
    s = s.replace("\u00a0", " ")
    if not _NUMBER_RE.match(s):
        m = re.match(r"^[-+]?\d+(?:[.,]\d+)?", s)
        if not m or len(m.group(0)) < len(s) - 0:
            return None
        s = m.group(0)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.match(r"^[-+]?\d{1,3}(\.\d{3})+$", s):
        s = s.replace(".", "")  # Tausenderpunkte
    s = s.replace(" ", "").replace("'", "")
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class CellInfo:
    sheet: str
    address: str
    raw: Any              # Inhalt mit data_only=False (Formel oder Wert)
    value: Any            # gespeicherter Ergebniswert (data_only=True)
    formula: str | None
    number_format: str = "General"

    @property
    def is_percent(self) -> bool:
        return "%" in (self.number_format or "")

    @property
    def missing_cache(self) -> bool:
        return self.formula is not None and self.value is None


class WorkbookData:
    def __init__(self, path: str | Path):
        self.original_path = Path(path)
        self._tmpdir = tempfile.TemporaryDirectory(prefix="heta_xlsx_")
        suffix = self.original_path.suffix.lower() or ".xlsx"
        self.tmp_path = Path(self._tmpdir.name) / f"kopie{suffix}"
        try:
            shutil.copyfile(self.original_path, self.tmp_path)
        except PermissionError as exc:
            self.close()
            raise ExcelReadError(f"Datei gesperrt oder keine Leseberechtigung: {exc.strerror or exc}", "gesperrt") from exc
        except OSError as exc:
            self.close()
            raise ExcelReadError(f"Datei nicht lesbar: {exc.strerror or exc}") from exc
        try:
            self.wb_formula = openpyxl.load_workbook(self.tmp_path, data_only=False, keep_vba=False, keep_links=False)
            self.wb_values = openpyxl.load_workbook(self.tmp_path, data_only=True, keep_vba=False, keep_links=False)
        except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, TypeError, OSError) as exc:
            self.close()
            raise ExcelReadError(f"Excel-Datei beschädigt oder kein gültiges XLSX-Format: {exc}", "beschaedigt") from exc
        self.application, self.full_calc_on_load = self._read_package_info()

    # --- Paketinformationen ---
    def _read_package_info(self) -> tuple[str | None, bool]:
        app, full_calc = None, False
        try:
            with zipfile.ZipFile(self.tmp_path) as z:
                names = set(z.namelist())
                if "docProps/app.xml" in names:
                    m = re.search(rb"<Application>([^<]*)</Application>", z.read("docProps/app.xml"))
                    app = m.group(1).decode("utf-8", "replace") if m else None
                if "xl/workbook.xml" in names:
                    full_calc = bool(re.search(rb'fullCalcOnLoad="(1|true)"', z.read("xl/workbook.xml")))
        except (zipfile.BadZipFile, OSError, KeyError):
            pass
        return app, full_calc

    @property
    def sheetnames(self) -> list[str]:
        return list(self.wb_formula.sheetnames)

    def cell(self, sheet: str, address: str) -> CellInfo:
        address = address.replace("$", "").upper()
        fcell = self.wb_formula[sheet][address]
        raw = fcell.value
        value = self.wb_values[sheet][address].value
        formula = None
        if isinstance(raw, str) and raw.startswith("="):
            formula = raw
        elif raw is not None and raw.__class__.__name__ in {"ArrayFormula", "DataTableFormula"}:
            formula = str(getattr(raw, "text", raw))
        return CellInfo(sheet, address, raw, value, formula, fcell.number_format or "General")

    def cell_rc(self, sheet: str, row: int, col: int) -> CellInfo:
        return self.cell(sheet, f"{get_column_letter(col)}{row}")

    def iter_text_cells(self, sheet: str, max_rows: int = 500, max_cols: int = 40) -> Iterator[tuple[int, int, str, str]]:
        """(row, col, Originaltext, normalisierter Text) aller Textzellen."""
        ws = self.wb_formula[sheet]
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, max_rows), max_col=min(ws.max_column, max_cols)):
            for c in row:
                v = c.value
                if isinstance(v, str) and v and not v.startswith("="):
                    yield c.row, c.column, v, normalize_label(v)

    def dimensions(self, sheet: str) -> tuple[int, int]:
        ws = self.wb_formula[sheet]
        return ws.max_row, ws.max_column

    def close(self) -> None:
        for attr in ("wb_formula", "wb_values"):
            wb = getattr(self, attr, None)
            if wb is not None:
                try:
                    wb.close()
                except Exception:  # noqa: BLE001 - Aufräumen darf nicht scheitern
                    pass
        self._tmpdir.cleanup()

    def __enter__(self) -> "WorkbookData":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def peek_workbook(path: str | Path, max_cells: int = 400) -> tuple[list[str], list[str]]:
    """Schneller Blick in eine Mappe für die Klassifizierung: Blattnamen und erste Textzellen."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_vba=False, keep_links=False)
    try:
        labels: list[str] = []
        for ws in wb.worksheets:
            count = 0
            for row in ws.iter_rows(max_row=120, max_col=30, values_only=True):
                for v in row:
                    if isinstance(v, str) and v.strip():
                        labels.append(normalize_label(v))
                        count += 1
                if count >= max_cells:
                    break
        return list(wb.sheetnames), labels
    finally:
        wb.close()


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if os.path.isfile(candidate):
            return candidate
    return None


def recalculate_copy(path: str | Path, timeout: int = 120) -> Path | None:
    """Optionale Neuberechnung mit LibreOffice – ausschließlich auf einer temporären Kopie.

    Gibt den Pfad der neu berechneten Kopie zurück (der Aufrufer löscht das Verzeichnis)
    oder ``None``, wenn LibreOffice nicht verfügbar ist bzw. die Berechnung scheitert.
    Makros werden nicht ausgeführt (LibreOffice-Standard: Makros deaktiviert im headless-Modus
    ohne vertrauenswürdige Quelle).
    """
    soffice = find_soffice()
    if not soffice:
        return None
    workdir = Path(tempfile.mkdtemp(prefix="heta_recalc_"))
    src = workdir / ("quelle" + Path(path).suffix.lower())
    shutil.copyfile(path, src)
    outdir = workdir / "out"
    outdir.mkdir()
    profile = (workdir / "lo_profile").as_uri()
    try:
        subprocess.run(
            [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--convert-to", "xlsx", "--outdir", str(outdir), str(src)],
            check=True, timeout=timeout, capture_output=True,
        )
    except (subprocess.SubprocessError, OSError):
        shutil.rmtree(workdir, ignore_errors=True)
        return None
    out = outdir / "quelle.xlsx"
    return out if out.exists() else None
