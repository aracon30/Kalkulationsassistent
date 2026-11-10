"""Profilbasierter Excel-Import.

Ein Importprofil (YAML in ``import_profiles/``) beschreibt, woran eine Vorlage
erkannt wird und wo welche Werte stehen (feste Zellen oder Beschriftungen).
Das Ergebnis ist eine reine Datenstruktur (``WorkbookExtraction``) – das
Speichern übernimmt ``import_service``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.services.excel_reader import CellInfo, WorkbookData, normalize_label, parse_number
from app.services.formula_check import check_formula
from app.services.normalize import parse_date, parse_temperature_range

UNIT_TOKENS = {"€", "eur", "euro", "h", "std", "std.", "stunden", "bar", "barg", "bar(g)", "°c", "mm", "m²", "m2", "kg",
               "l", "liter", "stk", "stk.", "stück", "%", "eur/h", "€/h", "m³", "m3"}


# --- Profilschema -------------------------------------------------------------

class FieldSpec(BaseModel):
    labels: list[str] = Field(default_factory=list)
    cell: str | None = None
    sheet: str | None = None
    type: Literal["number", "integer", "text", "date", "temperature_range"] = "number"
    unit: str | None = None
    direction: Literal["right", "below", "right_or_below"] = "right"
    max_distance: int = 8
    match: Literal["prefix", "exact"] = "prefix"
    name: str | None = None  # Anzeigename (für Arbeitsgänge)
    # Erweiterungen für feste Vorlagen
    inline: bool = False                 # "Kunde: Firma" – Wert steht nach dem Doppelpunkt in derselben Zelle
    value_column: str | None = None      # Wert in dieser Spalte der Beschriftungszeile (z. B. "O")
    row_offset: int = 0                  # mit value_column: Zeilenversatz zur Beschriftung (z. B. -1)
    regex: str | None = None             # aus dem gefundenen Wert Gruppe 1 übernehmen; kein Treffer -> nächste Fundstelle
    find_regex: str | None = None        # statt Beschriftung: Textzelle per Muster suchen, Gruppe 1 = Wert; {pos} = Positionsnummer
    occurrence: Literal["best", "first", "last"] = "best"  # bei mehreren Fundstellen


class OperationsTable(BaseModel):
    """Arbeitsgänge als frei benannte Tabellenzeilen (jede Zeile wird ein Arbeitsgang)."""
    header_label: str                    # Spaltenkopf der Arbeitsgang-Bezeichnungen, z. B. "Arbeitsgang"
    stop_labels: list[str] = Field(default_factory=list)
    max_rows: int = 80


class ProfileMeta(BaseModel):
    id: str
    name: str
    document_type: str
    validated: bool = False
    priority: int = 0


class Detection(BaseModel):
    filename_regex: list[str] = Field(default_factory=list)
    sheet_name_regex: list[str] = Field(default_factory=list)
    labels_any: list[str] = Field(default_factory=list)
    min_score: float = 0


class PositionSpec(BaseModel):
    mode: Literal["auto", "single", "sheet_per_position", "column_per_position"] = "auto"
    sheet_regex: str = r"(?i)^\s*pos(?:ition)?\.?\s*(\d+)"
    column_header_regex: str = r"(?i)^\s*pos(?:ition)?\.?\s*(\d+)\b"
    skip_sheet_regex: str | None = None
    sheet_must_contain: list[str] = Field(default_factory=list)  # Positionsblatt nur, wenn diese Beschriftung vorkommt


class ImportProfile(BaseModel):
    profile: ProfileMeta
    detection: Detection = Field(default_factory=Detection)
    positions: PositionSpec = Field(default_factory=PositionSpec)
    header_fields: dict[str, FieldSpec] = Field(default_factory=dict)
    item_fields: dict[str, FieldSpec] = Field(default_factory=dict)
    calculation_fields: dict[str, FieldSpec] = Field(default_factory=dict)
    operations: dict[str, FieldSpec] = Field(default_factory=dict)
    operations_table: OperationsTable | None = None
    calculation_fallbacks: dict[str, str] = Field(default_factory=dict)  # Zielfeld -> Ersatzfeld
    source_file: str | None = None

    @property
    def id(self) -> str:
        return self.profile.id


class ProfileError(Exception):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_profiles(directory: str | Path) -> dict[str, ImportProfile]:
    raw: dict[str, tuple[dict, str]] = {}
    for path in sorted(Path(directory).glob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ProfileError(f"Profil {path.name} ist kein gültiges YAML: {exc}") from exc
        pid = (data.get("profile") or {}).get("id") or path.stem
        raw[pid] = (data, path.name)

    resolved: dict[str, dict] = {}

    def resolve(pid: str, chain: tuple[str, ...] = ()) -> dict:
        if pid in resolved:
            return resolved[pid]
        if pid not in raw:
            raise ProfileError(f"Basisprofil '{pid}' nicht gefunden")
        if pid in chain:
            raise ProfileError(f"Zirkuläre Profilvererbung: {' -> '.join(chain + (pid,))}")
        data, _ = raw[pid]
        parent = data.get("extends")
        merged = _deep_merge(resolve(parent, chain + (pid,)), {k: v for k, v in data.items() if k != "extends"}) if parent else data
        resolved[pid] = merged
        return merged

    profiles: dict[str, ImportProfile] = {}
    for pid, (_, fname) in raw.items():
        try:
            prof = ImportProfile(**resolve(pid), source_file=fname)
        except ValidationError as exc:
            raise ProfileError(f"Profil {fname} ist ungültig: {exc}") from exc
        for rx in prof.detection.filename_regex + prof.detection.sheet_name_regex + [prof.positions.sheet_regex, prof.positions.column_header_regex]:
            try:
                re.compile(rx)
            except re.error as exc:
                raise ProfileError(f"Profil {fname}: ungültiger regulärer Ausdruck '{rx}': {exc}") from exc
        profiles[prof.id] = prof
    return profiles


def score_profile(profile: ImportProfile, wb: WorkbookData, file_name: str) -> tuple[float, list[str]]:
    det = profile.detection
    score, reasons = 0.0, []
    if any(re.search(rx, file_name) for rx in det.filename_regex):
        score += 2
        reasons.append("Dateiname")
    sheet_hits = sum(1 for s in wb.sheetnames if any(re.search(rx, s) for rx in det.sheet_name_regex))
    if sheet_hits:
        score += min(2, sheet_hits)
        reasons.append(f"{sheet_hits} Blattname(n)")
    wanted = {normalize_label(l) for l in det.labels_any}
    found: set[str] = set()
    for sheet in wb.sheetnames:
        for _, _, _, norm in wb.iter_text_cells(sheet, max_rows=200, max_cols=30):
            for w in wanted:
                if norm == w or norm.startswith(w + " "):
                    found.add(w)
    if found:
        score += len(found)
        reasons.append(f"{len(found)} Beschriftung(en)")
    return score, reasons


def select_profile(profiles: dict[str, ImportProfile], wb: WorkbookData, file_name: str, document_type: str) -> tuple[ImportProfile | None, str]:
    candidates = []
    for prof in profiles.values():
        if prof.profile.document_type != document_type:
            continue
        score, reasons = score_profile(prof, wb, file_name)
        if score >= prof.detection.min_score:
            candidates.append((score + prof.profile.priority / 100.0, prof, reasons))
    if not candidates:
        return None, "Kein passendes Importprofil"
    candidates.sort(key=lambda c: c[0], reverse=True)
    best = candidates[0]
    return best[1], f"Profil '{best[1].id}' gewählt (Punkte {best[0]:.2f}: {', '.join(best[2]) or '-'})"


# --- Extraktion --------------------------------------------------------------

@dataclass
class Issue:
    code: str
    message: str
    severity: str = "warnung"
    field_name: str | None = None
    position: int | None = None


@dataclass
class Extracted:
    field_name: str
    group: str                    # header | item | calculation | operation
    position: int | None
    value: Any                    # typisierter Wert oder None
    text: str | None
    sheet: str | None
    cell: str | None
    label: str | None
    formula: str | None
    cached: Any
    unit: str | None
    method: str                   # profil_zelle | profil_label
    confidence: float
    issues: list[Issue] = field(default_factory=list)
    display_name: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def source_ref(self) -> str:
        return f"{self.sheet}!{self.cell}" if self.sheet and self.cell else "-"


@dataclass
class WorkbookExtraction:
    profile_id: str
    profile_validated: bool
    profile_reason: str
    position_mode: str
    header: dict[str, Extracted] = field(default_factory=dict)
    positions: dict[int, dict[str, Extracted]] = field(default_factory=dict)
    operations: dict[int, dict[str, Extracted]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    application: str | None = None
    fallbacks: dict[str, str] = field(default_factory=dict)


@dataclass
class _Candidate:
    rank: tuple
    cell: CellInfo | None
    label_text: str
    unit: str | None
    missing_cache: bool = False
    override: Any = None          # aus Textzelle herausgelöster Wert (inline/regex/find_regex)
    order: tuple = ()             # Fundreihenfolge (Blatt, Zeile, Spalte)


def _label_matches(norm: str, label_norm: str, mode: str) -> int:
    """0 = keine, 2 = exakt, 1 = Präfix."""
    if norm == label_norm:
        return 2
    if mode == "prefix" and norm.startswith(label_norm) and len(norm) > len(label_norm) and not norm[len(label_norm)].isalnum():
        return 1
    return 0


def _is_unit(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in UNIT_TOKENS


def _convert(spec: FieldSpec, value: Any) -> tuple[Any, str | None, str | None]:
    """(typisierter Wert, Text, Fehler)."""
    if value is None:
        return None, None, None
    text = value.isoformat() if isinstance(value, (datetime, date)) else str(value).strip()
    if spec.type in {"number", "integer"}:
        num = parse_number(value)
        if num is None:
            return None, text, f"Wert '{text}' ist keine Zahl"
        if spec.type == "integer":
            if abs(num - round(num)) > 1e-9:
                return None, text, f"Wert '{text}' ist keine ganze Zahl"
            return int(round(num)), text, None
        return num, text, None
    if spec.type == "date":
        d = parse_date(value)
        return (d, text, None) if d else (None, text, f"Datum '{text}' nicht erkannt")
    if spec.type == "temperature_range":
        lo, hi = parse_temperature_range(value)
        if lo is None and hi is None:
            return None, text, f"Temperaturangabe '{text}' nicht erkannt"
        return (lo, hi), text, None
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    return (text or None), text, None


class Extractor:
    def __init__(self, wb: WorkbookData, profile: ImportProfile):
        self.wb = wb
        self.profile = profile
        skip = profile.positions.skip_sheet_regex
        self.sheets = [s for s in wb.sheetnames if not (skip and re.search(skip, s))] or wb.sheetnames
        self._text_cache: dict[str, list[tuple[int, int, str, str]]] = {s: list(wb.iter_text_cells(s)) for s in wb.sheetnames}
        # Alle bekannten Beschriftungen: ein Textwert, der selbst eine Beschriftung ist, ist kein Wert
        self._known_labels = {normalize_label(l) for group in (profile.header_fields, profile.item_fields, profile.calculation_fields, profile.operations)
                              for spec in group.values() for l in spec.labels}

        # Tabellenkopfzeilen: ≥ 3 bekannte Beschriftungen in einer Zeile -> Werte stehen darunter, nicht rechts
        self._header_rows: dict[str, set[int]] = {}
        for sheet, cells in self._text_cache.items():
            per_row: dict[int, int] = {}
            for row, _, _, norm in cells:
                if norm in self._known_labels:
                    per_row[row] = per_row.get(row, 0) + 1
            candidates = {r for r, n in per_row.items() if n >= 3}
            ws = wb.wb_formula[sheet]
            # Ein Tabellenkopf enthält keine Zahlen/Formeln (sonst ist es eine Wertezeile mit mehreren Beschriftungen)
            self._header_rows[sheet] = {r for r in candidates
                                        if not any(c.value is not None and not isinstance(c.value, str) or
                                                   (isinstance(c.value, str) and c.value.startswith("="))
                                                   for c in ws[r])}

    def _looks_like_label(self, val) -> bool:
        if not isinstance(val, str):
            return False
        text = val.strip()
        if text.endswith(":") or text.endswith("："):
            return True
        norm = normalize_label(text)
        return norm in self._known_labels or any(norm.startswith(l + " ") for l in self._known_labels if len(l) > 3)

    # Werte neben/unter einer Beschriftung suchen
    def _scan_value(self, sheet: str, row: int, col: int, spec: FieldSpec) -> tuple[CellInfo | None, str | None]:
        max_row, max_col = self.wb.dimensions(sheet)
        directions = {"right": [(0, 1)], "below": [(1, 0)], "right_or_below": [(0, 1), (1, 0)]}[spec.direction]
        for dr, dc in directions:
            unit = None
            for step in range(1, spec.max_distance + 1):
                r, c = row + dr * step, col + dc * step
                if r > max_row or c > max_col:
                    break
                info = self.wb.cell_rc(sheet, r, c)
                if info.formula is None and info.raw is None:
                    continue
                if info.missing_cache:
                    return info, unit
                val = info.value if info.formula else info.raw
                if spec.type in {"number", "integer"} and _is_unit(val):
                    unit = str(val).strip()
                    continue
                if spec.type in {"number", "integer"}:
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        if info.is_percent and spec.unit not in {"%"}:
                            continue  # Zuschlagssatz neben dem Betrag überspringen
                        return info, unit
                    if parse_number(val) is not None:
                        return info, unit
                    if isinstance(val, str) and (len(val.strip()) > 12 or self._looks_like_label(val)):
                        break  # vermutlich nächste Beschriftung
                    continue
                if self._looks_like_label(val):
                    break  # Textfeld: rechts steht die nächste Beschriftung, kein Wert
                return info, unit
        return None, None

    def _find(self, spec: FieldSpec, sheets: list[str], column: int | None = None, position: int | None = None) -> tuple[list[_Candidate], str]:
        """Sucht Kandidaten; bei ``column`` wird der Wert in dieser Spalte der Beschriftungszeile gelesen."""
        if spec.cell:
            sheet, addr = (spec.cell.split("!", 1) if "!" in spec.cell else (sheets[0] if sheets else self.wb.sheetnames[0], spec.cell))
            sheet = sheet.strip("'")
            if sheet not in self.wb.sheetnames:
                return [], "profil_zelle"
            return [_Candidate((3, 0, 0), self.wb.cell(sheet, addr), f"Zelle {addr}", spec.unit)], "profil_zelle"
        out: list[_Candidate] = []
        if spec.find_regex:
            rx = re.compile(spec.find_regex.replace("{pos}", str(position if position is not None else r"\d+")), re.IGNORECASE)
            for s_idx, sheet in enumerate(sheets):
                if spec.sheet and not re.search(spec.sheet, sheet):
                    continue
                for row, col, original, _ in self._text_cache.get(sheet, []):
                    m = rx.search(original)
                    if m:
                        val = (m.group(1) if m.groups() else m.group(0)).strip()
                        out.append(_Candidate((2, 0, -s_idx), self.wb.cell_rc(sheet, row, col), original, spec.unit,
                                              override=val, order=(s_idx, row, col)))
            return out, "profil_muster"
        label_norms = [normalize_label(l) for l in spec.labels]
        for s_idx, sheet in enumerate(sheets):
            if spec.sheet and not re.search(spec.sheet, sheet):
                continue
            for row, col, original, norm in self._text_cache.get(sheet, []):
                for prio, ln in enumerate(label_norms):
                    kind = _label_matches(norm, ln, "prefix" if spec.inline else spec.match)
                    if not kind:
                        continue
                    override = None
                    unit = None
                    if spec.inline:
                        if ":" not in original:
                            continue
                        override = original.split(":", 1)[1].strip() or None
                        if override is None:
                            break
                        info = self.wb.cell_rc(sheet, row, col)
                    elif spec.value_column:
                        from openpyxl.utils import column_index_from_string
                        r = row + spec.row_offset
                        if r < 1:
                            break
                        info = self.wb.cell_rc(sheet, r, column_index_from_string(spec.value_column))
                        if info.raw is None and info.formula is None:
                            break
                    elif column is not None:
                        if col >= column:
                            continue
                        info = self.wb.cell_rc(sheet, row, column)
                        if info.raw is None and info.formula is None:
                            break
                    else:
                        if spec.direction == "right" and row in self._header_rows.get(sheet, ()):
                            break  # Tabellenkopf: rechts stehen weitere Spaltenüberschriften
                        info, unit = self._scan_value(sheet, row, col, spec)
                        if info is None:
                            break
                    if spec.regex:
                        source = override if override is not None else (info.value if info.formula else info.raw)
                        m = re.search(spec.regex, str(source if source is not None else ""), re.IGNORECASE)
                        if not m:
                            break  # Muster passt nicht -> nächste Fundstelle
                        override = (m.group(1) if m.groups() else m.group(0)).strip()
                    out.append(_Candidate((kind, -prio, -s_idx), info, original, unit or spec.unit, info.missing_cache and override is None,
                                          override=override, order=(s_idx, row, col)))
                    break
        return out, "profil_label"

    def extract_field(self, name: str, group: str, spec: FieldSpec, sheets: list[str], position: int | None, column: int | None = None) -> Extracted | None:
        candidates, method = self._find(spec, sheets, column, position)
        if not candidates:
            return None
        if spec.occurrence in ("first", "last"):
            candidates.sort(key=lambda c: c.order)
            best = candidates[0] if spec.occurrence == "first" else candidates[-1]
            same_rank = [best]
        else:
            candidates.sort(key=lambda c: c.rank, reverse=True)
            best = candidates[0]
            same_rank = [c for c in candidates if c.rank == best.rank and c.cell is not None]
        return self._from_candidate(name, group, spec, best, position, method, same_rank)

    @staticmethod
    def _cand_value(c: _Candidate):
        return c.override if c.override is not None else (c.cell.value if c.cell.formula else c.cell.raw)

    def _from_candidate(self, name: str, group: str, spec: FieldSpec, best: _Candidate, position: int | None, method: str,
                        same_rank: list[_Candidate] | None = None) -> Extracted:
        info = best.cell
        assert info is not None
        issues: list[Issue] = []
        confidence = {3: 0.95, 2: 0.9, 1: 0.8}.get(best.rank[0], 0.8)
        same_rank = same_rank or [best]
        distinct = {repr(self._cand_value(c)) for c in same_rank}
        if len(distinct) > 1:
            confidence = 0.5
            refs = ", ".join(f"{c.cell.sheet}!{c.cell.address}" for c in same_rank[:5])
            issues.append(Issue("mehrdeutig", f"Feld '{name}': mehrere gleichwertige Fundstellen mit unterschiedlichen Werten ({refs})", "warnung", name, position))

        raw_value = self._cand_value(best)
        uses_formula = best.override is None and info.formula is not None
        if uses_formula and info.missing_cache:
            issues.append(Issue("formel_ohne_cache", f"Feld '{name}': Formel {info.formula} in {info.sheet}!{info.address} ohne gespeicherten Ergebniswert", "warnung", name, position))
            confidence = min(confidence, 0.3)
        extra: dict = {}
        if uses_formula and not info.missing_cache:
            def lookup(sh: str, addr: str):
                c = self.wb.cell(sh, addr)
                return (c.value if c.formula else c.raw), c.formula is not None
            chk = check_formula(info.formula, info.value, info.sheet, lookup)
            extra["formula_checkable"] = chk.checkable
            if chk.consistent is False:
                issues.append(Issue("formel_cache_abweichung",
                                    f"Feld '{name}': gespeicherter Wert {chk.cached} weicht von Nachrechnung {chk.computed:.2f} ab ({info.sheet}!{info.address}) – Formel-Cache möglicherweise veraltet",
                                    "warnung", name, position))
                confidence = min(confidence, 0.4)
            elif chk.consistent:
                extra["formula_verified"] = True

        value, text, err = _convert(spec, raw_value)
        if err:
            issues.append(Issue("typfehler", f"Feld '{name}': {err} ({info.sheet}!{info.address})", "warnung", name, position))
            confidence = min(confidence, 0.3)
        return Extracted(
            field_name=name, group=group, position=position, value=value, text=text, sheet=info.sheet, cell=info.address,
            label=best.label_text, formula=info.formula if uses_formula else None,
            cached=info.value if uses_formula else None, unit=best.unit,
            method=method, confidence=confidence, issues=issues, display_name=spec.name, extra=extra,
        )

    # --- Positionserkennung ---
    def detect_position_sheets(self) -> dict[int, str]:
        pos = self.profile.positions
        rx = re.compile(pos.sheet_regex)
        must = [normalize_label(l) for l in pos.sheet_must_contain]
        pn_spec = self.profile.item_fields.get("position_number")
        out: dict[int, str] = {}
        for sheet in self.sheets:
            m = rx.search(sheet)
            if not m:
                continue
            if must and not any(_label_matches(n, l, "prefix") for _, _, _, n in self._text_cache.get(sheet, []) for l in must):
                continue
            num = None
            if m.groups() and m.group(1) and m.group(1).isdigit():
                num = int(m.group(1))
            elif pn_spec:
                pn = self.extract_field("position_number", "item", pn_spec, [sheet], None)
                if pn and isinstance(pn.value, int) and pn.value > 0:
                    num = pn.value
            if num is None or num in out:
                num = max(out, default=0) + 1
            out[num] = sheet
        return out

    def detect_position_columns(self) -> tuple[str | None, int | None, dict[int, int]]:
        rx = re.compile(self.profile.positions.column_header_regex)
        best: tuple[str | None, int | None, dict[int, int]] = (None, None, {})
        for sheet in self.sheets:
            rows: dict[int, dict[int, int]] = {}
            for row, col, original, _ in self._text_cache[sheet]:
                m = rx.search(original)
                if m and m.groups() and m.group(1):
                    rows.setdefault(row, {}).setdefault(int(m.group(1)), col)
            for row, cols in rows.items():
                if len(cols) > len(best[2]):
                    best = (sheet, row, cols)
        return best

    def _operations_table(self, table: OperationsTable, sheets: list[str], position: int, column: int | None) -> dict[str, Extracted]:
        """Jede Zeile unter dem Spaltenkopf ist ein Arbeitsgang (Name aus der Zeile, Stunden aus der Positionsspalte)."""
        out: dict[str, Extracted] = {}
        if column is None:
            return out
        head = normalize_label(table.header_label)
        stops = [normalize_label(x) for x in table.stop_labels]
        for sheet in sheets:
            start = next(((r, c) for r, c, _, n in self._text_cache.get(sheet, []) if n == head), None)
            if not start:
                continue
            r0, name_col = start
            empty = 0
            for r in range(r0 + 1, r0 + 1 + table.max_rows):
                name_cell = self.wb.cell_rc(sheet, r, name_col)
                name = name_cell.raw if isinstance(name_cell.raw, str) else None
                if not name or not name.strip():
                    empty += 1
                    if empty >= 3:
                        break
                    continue
                empty = 0
                if any(_label_matches(normalize_label(name), s_, "prefix") for s_ in stops):
                    break
                spec = FieldSpec(labels=[], type="number", unit="h", name=name.strip()[:120])
                info = self.wb.cell_rc(sheet, r, column)
                if info.raw is None and info.formula is None:
                    continue
                key = f"ag_{r:03d}"
                cand = _Candidate((2, 0, 0), info, name.strip(), "h", info.missing_cache, order=(0, r, column))
                ex = self._from_candidate(key, "operation", spec, cand, position, "profil_tabelle")
                out[key] = ex
            break
        return out

    def run(self, profile_reason: str) -> WorkbookExtraction:
        prof = self.profile
        mode = prof.positions.mode
        pos_sheets = self.detect_position_sheets() if mode in {"auto", "sheet_per_position"} else {}
        col_sheet, header_row, pos_cols = self.detect_position_columns() if mode in {"auto", "column_per_position"} else (None, None, {})
        if mode == "auto":
            if len(pos_sheets) >= 2 or (len(pos_sheets) == 1 and prof.positions.sheet_must_contain):
                mode = "sheet_per_position"
            elif len(pos_cols) >= 2:
                mode = "column_per_position"
            else:
                mode = "single"

        result = WorkbookExtraction(prof.id, prof.profile.validated, profile_reason, mode, application=self.wb.application,
                                    fallbacks=dict(prof.calculation_fallbacks))
        for name, spec in prof.header_fields.items():
            ex = self.extract_field(name, "header", spec, self.wb.sheetnames, None)  # Kopf: alle Blätter (auch Deckblatt)
            if ex:
                result.header[name] = ex

        item_specs = [("item", n, s) for n, s in prof.item_fields.items()] + [("calculation", n, s) for n, s in prof.calculation_fields.items()]
        op_specs = list(prof.operations.items())

        def collect(position: int, sheets: list[str], column: int | None) -> None:
            fields: dict[str, Extracted] = {}
            for group, name, spec in item_specs:
                if name == "position_number" and column is not None:
                    continue
                ex = self.extract_field(name, group, spec, sheets, position, column)
                if ex:
                    fields[name] = ex
            ops: dict[str, Extracted] = {}
            if prof.operations_table is not None:
                ops.update(self._operations_table(prof.operations_table, sheets, position, column))
            for key, spec in op_specs:
                ex = self.extract_field(key, "operation", spec, sheets, position, column)
                if ex:
                    ops[key] = ex
            if fields or ops:
                result.positions[position] = fields
                result.operations[position] = ops

        if mode == "sheet_per_position":
            for num, sheet in sorted(pos_sheets.items()):
                collect(num, [sheet], None)
        elif mode == "column_per_position":
            assert col_sheet is not None
            for num, col in sorted(pos_cols.items()):
                collect(num, [col_sheet], col)
        else:
            pos_num = 1
            pn_spec = prof.item_fields.get("position_number")
            if pn_spec:
                pn = self.extract_field("position_number", "item", pn_spec, self.sheets, None)
                if pn and isinstance(pn.value, int) and pn.value > 0:
                    pos_num = pn.value
            collect(pos_num, self.sheets, None)

        if not result.positions:
            result.issues.append(Issue("keine_positionen", "Keine Angebotsposition mit auswertbaren Werten gefunden", "fehler"))
        if self.wb.full_calc_on_load:
            result.issues.append(Issue("neuberechnung_erzwungen", "Arbeitsmappe verlangt Neuberechnung beim Öffnen – gespeicherte Formelergebnisse können veraltet sein", "warnung"))
        if self.wb.application and "excel" not in self.wb.application.lower():
            result.issues.append(Issue("nicht_excel_gespeichert", f"Datei zuletzt mit '{self.wb.application}' gespeichert – Formelergebnisse ggf. nicht aktuell", "info"))
        for pos_fields in list(result.positions.values()) + list(result.operations.values()):
            for ex in pos_fields.values():
                result.issues.extend(ex.issues)
        for ex in result.header.values():
            result.issues.extend(ex.issues)
        return result


def extract_workbook(path: str | Path, profiles: dict[str, ImportProfile], document_type: str, profile_id: str | None = None) -> WorkbookExtraction:
    with WorkbookData(path) as wb:
        if profile_id:
            if profile_id not in profiles:
                raise ProfileError(f"Importprofil '{profile_id}' nicht vorhanden")
            profile, reason = profiles[profile_id], f"Profil '{profile_id}' manuell gewählt"
        else:
            profile, reason = select_profile(profiles, wb, Path(path).name, document_type)
        if profile is None:
            raise ProfileError(f"Kein Importprofil für Dokumenttyp '{document_type}' gefunden")
        return Extractor(wb, profile).run(reason)
