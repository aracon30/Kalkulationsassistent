"""Rekursiver Ordnerscan (lokal, Netzlaufwerk, UNC) – ausschließlich lesend."""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".pdf"}
# Hilfs-/Verknüpfungsdateien werden gar nicht erst inventarisiert
SKIPPED_EXTENSIONS = {".tmp", ".lnk", ".bak", ".~lock"}


def fs_path(path: str | Path, _nt: bool | None = None) -> str:
    r"""Pfad zum Öffnen von Dateien.

    Unter Windows wird das erweiterte Präfix ``\\?\`` verwendet. Damit lassen sich auch Pfade
    über 260 Zeichen und Namen mit Leerzeichen/Punkt am Ende öffnen, die ``os.scandir`` zwar
    auflistet, die normale Win32-Pfadauflösung aber nicht findet. Andere Systeme: unverändert.
    """
    p = str(path)
    nt = (os.name == "nt") if _nt is None else _nt
    if not nt or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):  # UNC: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + p[2:]
    if len(p) >= 3 and p[1] == ":" and p[2] in "\\/":
        return "\\\\?\\" + p.replace("/", "\\")
    return p


def is_excluded_dir(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, pat.lower()) for pat in patterns)
KNOWN_UNSUPPORTED = {".xls", ".xlsb", ".doc", ".docx", ".msg", ".dwg", ".dxf", ".step", ".stp", ".zip", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt", ".csv"}


@dataclass
class ScannedFile:
    path: str
    relative_path: str
    name: str
    extension: str
    size: int
    modified_at: datetime


@dataclass
class ScanProblem:
    path: str
    kind: str      # zugriff_verweigert | gesperrt | fehler | zu_gross | temp_datei
    message: str


@dataclass
class ScanResult:
    files: list[ScannedFile] = field(default_factory=list)
    problems: list[ScanProblem] = field(default_factory=list)
    locked_hints: set[str] = field(default_factory=set)  # Pfade, zu denen eine Excel-Sperrdatei (~$) existiert


def normalize_base_dir(raw: str) -> str:
    """Bereinigt Benutzereingaben (Anführungszeichen, abschließende Trenner). UNC bleibt erhalten."""
    value = (raw or "").strip().strip('"').strip("'")
    if len(value) > 3 and value.endswith(("\\", "/")) and not value.endswith(":\\"):
        value = value.rstrip("\\/")
    return value


def validate_base_dir(raw: str) -> Path:
    value = normalize_base_dir(raw)
    if not value:
        raise ValueError("Kein Basisverzeichnis angegeben.")
    path = Path(value)
    try:
        if not path.exists():
            raise ValueError(f"Verzeichnis nicht gefunden oder nicht erreichbar: {value}")
        if not path.is_dir():
            raise ValueError(f"Pfad ist kein Verzeichnis: {value}")
    except PermissionError as exc:
        raise ValueError(f"Keine Berechtigung für {value}: {exc}") from exc
    return path


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(fs_path(path), "rb") as fh:  # ausschließlich lesend
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(root: Path, excluded: list[str], problems: list[ScanProblem], cancel: Callable[[], bool] | None) -> Iterator[os.DirEntry]:
    stack = [str(root)]
    while stack:
        if cancel and cancel():
            return
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except PermissionError as exc:
            problems.append(ScanProblem(current, "zugriff_verweigert", f"Keine Leseberechtigung: {exc.strerror or exc}"))
            continue
        except OSError as exc:
            problems.append(ScanProblem(current, "fehler", f"Verzeichnis nicht lesbar: {exc.strerror or exc}"))
            continue
        for entry in sorted(entries, key=lambda e: e.name.lower()):
            try:
                if entry.is_dir(follow_symlinks=False):
                    if is_excluded_dir(entry.name, excluded) or entry.name.startswith("."):
                        continue
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    yield entry
            except OSError as exc:
                problems.append(ScanProblem(entry.path, "fehler", str(exc)))


def scan_directory(
    base_dir: str | Path,
    excluded_dir_names: list[str] | None = None,
    max_file_size_mb: int = 200,
    progress: Callable[[int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> ScanResult:
    root = Path(os.path.abspath(validate_base_dir(str(base_dir))))
    result = ScanResult()
    excluded = list(excluded_dir_names or [])
    max_bytes = max_file_size_mb * 1024 * 1024
    count = 0
    for entry in _walk(root, excluded, result.problems, cancel):
        name = entry.name
        if name.startswith("~$"):
            # Excel-Besitzerdatei: Originaldatei ist vermutlich gerade geöffnet
            result.locked_hints.add(os.path.join(os.path.dirname(entry.path), name[2:]).lower())
            continue
        if name.lower() in {"thumbs.db", "desktop.ini"} or name.startswith(".") or os.path.splitext(name)[1].lower() in SKIPPED_EXTENSIONS:
            continue
        try:
            st = entry.stat()
        except OSError as exc:
            result.problems.append(ScanProblem(entry.path, "fehler", f"Dateiattribute nicht lesbar: {exc}"))
            continue
        if st.st_size > max_bytes:
            result.problems.append(ScanProblem(entry.path, "zu_gross", f"Datei größer als {max_file_size_mb} MB – übersprungen"))
            continue
        try:
            rel = os.path.relpath(entry.path, root)
        except ValueError:
            rel = name
        result.files.append(
            ScannedFile(
                path=entry.path,
                relative_path=rel,
                name=name,
                extension=os.path.splitext(name)[1].lower(),
                size=st.st_size,
                modified_at=datetime.fromtimestamp(st.st_mtime).replace(microsecond=0),
            )
        )
        count += 1
        if progress:
            progress(count, entry.path)
    return result


def is_locked(path: str, scan: ScanResult | None = None) -> bool:
    """Grobe Prüfung, ob eine Datei gerade gesperrt ist (Excel-Sperrdatei oder Lesefehler)."""
    if scan and path.lower() in scan.locked_hints:
        return True
    try:
        with open(fs_path(path), "rb") as fh:
            fh.read(1)
        return False
    except PermissionError:
        return True
    except OSError:
        return False
