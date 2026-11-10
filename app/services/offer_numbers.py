"""Erkennung von Angebotsnummern und Revisionskennzeichen."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PureWindowsPath, PurePosixPath

from app.config import get_settings


@dataclass(frozen=True)
class OfferNumberMatch:
    number: str        # normalisiert, z. B. "SE-13705.26"
    year: int | None   # z. B. 2026
    source: str        # "dateiname" | "ordner:<name>" | "inhalt"


def _compile(patterns: list[str] | None) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in (patterns or get_settings().offer_number_patterns)]


def normalize_offer_number(match: re.Match[str]) -> tuple[str, int | None]:
    groups = match.groupdict()
    if "num" in groups and "year" in groups and groups["num"] and groups["year"]:
        yy = int(groups["year"])
        return f"SE-{groups['num']}.{groups['year']}", 2000 + yy
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
    """Sucht zuerst im Dateinamen, dann in den Ordnern (vom nächsten zum entferntesten)."""
    parts = split_path_parts(path)
    if not parts:
        return None
    file_name = parts[-1]
    hits = find_offer_numbers(file_name, patterns)
    if hits:
        return OfferNumberMatch(hits[0][0], hits[0][1], "dateiname")
    for folder in reversed(parts[:-1]):
        hits = find_offer_numbers(folder, patterns)
        if hits:
            return OfferNumberMatch(hits[0][0], hits[0][1], f"ordner:{folder}")
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
