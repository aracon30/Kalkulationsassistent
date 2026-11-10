"""Erkennung von Angebotsnummern und Revisionskennzeichen."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PureWindowsPath, PurePosixPath

from app.config import get_settings


@dataclass(frozen=True)
class OfferNumberMatch:
    number: str        # normalisiert, z. B. "AE-13548.26"
    year: int | None   # z. B. 2026
    source: str        # "ordner:<name>" | "dateiname" (ggf. mit Hinweis auf abweichende Nummer)


def _compile(patterns: list[str] | None) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in (patterns or get_settings().offer_number_patterns)]


def normalize_offer_number(match: re.Match[str]) -> tuple[str, int | None]:
    groups = match.groupdict()
    if groups.get("num") and groups.get("year"):
        prefix = (groups.get("prefix") or "SE").upper()
        return f"{prefix}-{groups['num']}.{groups['year']}", 2000 + int(groups["year"])
    text = re.sub(r"\s+", "", match.group(0)).upper().replace("_", "-")
    return text, None


def find_offer_numbers(text: str, patterns: list[str] | None = None) -> list[tuple[str, int | None]]:
    found: list[tuple[str, int | None]] = []
    for rx in _compile(patterns):
        for m in rx.finditer(text or ""):
            item = normalize_offer_number(m)
            if item not in found:
                found.append(item)
    return found


def split_path_parts(path: str) -> list[str]:
    """Pfadbestandteile für Windows- (inkl. UNC) und POSIX-Pfade."""
    if "\\" in path or re.match(r"^[A-Za-z]:", path):
        return list(PureWindowsPath(path).parts)
    return list(PurePosixPath(path).parts)


def detect_offer_number(path: str, patterns: list[str] | None = None) -> OfferNumberMatch | None:
    """Ordner vor Dateiname: Der Angebotsordner (z. B. ``AE-13548.26-Kunde``) ist maßgeblich.

    Dateinamen enthalten oft Nummern anderer Angebote (kopierte Vorlagen, Referenzrechnungen),
    sie werden nur verwendet, wenn kein Ordner eine Nummer trägt.
    """
    parts = split_path_parts(path)
    if not parts:
        return None
    file_hits = find_offer_numbers(parts[-1], patterns)
    for folder in reversed(parts[:-1]):
        hits = find_offer_numbers(folder, patterns)
        if hits:
            source = f"ordner:{folder}"
            other = [n for n, _ in file_hits if n != hits[0][0]]
            if other:
                source += f" (Dateiname nennt {other[0]})"
            return OfferNumberMatch(hits[0][0], hits[0][1], source)
    if file_hits:
        return OfferNumberMatch(file_hits[0][0], file_hits[0][1], "dateiname")
    return None


_REVISION_PATTERNS = [
    re.compile(r"(?:^|[_\-\s.(])rev(?:ision)?\.?[_\-\s]?(?P<rev>[0-9]{1,3}|[A-Z])(?=$|[_\-\s.)])", re.IGNORECASE),
    re.compile(r"(?:^|[_\-\s.(])v(?P<rev>[0-9]{1,3})(?=$|[_\-\s.)])", re.IGNORECASE),
    re.compile(r"(?:^|[_\-\s.(])(?:index|idx)[_\-\s]?(?P<rev>[0-9]{1,3}|[A-Z])(?=$|[_\-\s.)])", re.IGNORECASE),
]


def detect_revision(file_name: str) -> tuple[str | None, int | None]:
    """Revisionskennzeichen aus dem Dateinamen (z. B. ``_Rev2``, ``_V3``, ``Index B``)."""
    stem = file_name.rsplit(".", 1)[0]
    for rx in _REVISION_PATTERNS:
        m = rx.search(stem)
        if m:
            rev = m.group("rev").upper()
            index = int(rev) if rev.isdigit() else (ord(rev) - ord("A") + 1)
            return rev, index
    return None, None
