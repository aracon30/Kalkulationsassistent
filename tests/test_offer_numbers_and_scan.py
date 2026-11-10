import os
from pathlib import Path

import pytest

from app.services.folder_scanner import normalize_base_dir, scan_directory, sha256_file, validate_base_dir
from app.services.offer_numbers import detect_offer_number, detect_revision, find_offer_numbers, split_path_parts


@pytest.mark.parametrize("text,expected", [
    ("SE-13705.26_Kalkulation.xlsx", ("SE-13705.26", 2026)),
    ("Angebot SE 12501.25 Rev2.pdf", ("SE-12501.25", 2025)),
    ("se_12501.25", ("SE-12501.25", 2025)),
    ("XSE-13705.26", None),
    ("SE-13705.264", None),
    ("Kalkulation.xlsx", None),
])
def test_find_offer_numbers(env, text, expected):
    hits = find_offer_numbers(text)
    assert (hits[0] if hits else None) == expected


def test_detect_offer_number_prefers_filename_then_folder(env):
    m = detect_offer_number(r"\\SERVER\Freigabe\Angebote\2026\SE-13705.26\Zeichnungen\KF-3-301.11-005.pdf")
    assert m.number == "SE-13705.26" and m.year == 2026 and m.source.startswith("ordner:")
    m = detect_offer_number("/daten/2025/SE-12501.25/SE-12502.25_Kalkulation.xlsx")
    assert m.number == "SE-12502.25" and m.source == "dateiname"
    assert detect_offer_number("/daten/sonstiges/liste.xlsx") is None


def test_unc_and_windows_paths_are_split_correctly():
    parts = split_path_parts(r"\\SERVER\Freigabe\Angebote\2026\SE-13705.26\Angebot.pdf")
    assert parts[0].startswith("\\\\SERVER\\Freigabe")
    assert parts[-2:] == ["SE-13705.26", "Angebot.pdf"]
    assert normalize_base_dir(' "\\\\SERVER\\Freigabe\\Angebote\\" ') == r"\\SERVER\Freigabe\Angebote"
    assert normalize_base_dir("D:\\") == "D:\\"


@pytest.mark.parametrize("name,expected", [
    ("SE-13705.26_Kalkulation_Rev2.xlsx", ("2", 2)), ("Kalkulation_V3.xlsx", ("3", 3)),
    ("Kalkulation Index B.xlsx", ("B", 2)), ("Kalkulation.xlsx", (None, None)), ("Revisionsliste.xlsx", (None, None)),
])
def test_detect_revision(name, expected):
    assert detect_revision(name) == expected


def test_scan_recursive_and_lockfiles(env, offer_base):
    folder = offer_base / "2026" / "SE-99901.26"
    (folder / "~$SE-99901.26_Kalkulation.xlsx").write_bytes(b"lock")
    (offer_base / ".versteckt").mkdir()
    (offer_base / ".versteckt" / "x.pdf").write_bytes(b"x")
    res = scan_directory(offer_base)
    names = sorted(f.name for f in res.files)
    assert "~$SE-99901.26_Kalkulation.xlsx" not in names and "x.pdf" not in names
    assert len(names) == 6
    assert str(folder / "SE-99901.26_Kalkulation.xlsx").lower() in res.locked_hints
    rel = {f.relative_path for f in res.files}
    assert os.path.join("2026", "SE-99901.26", "Angebot.pdf") in rel


def test_scan_reports_permission_errors(env, tmp_path, monkeypatch):
    base = tmp_path / "base"
    (base / "geheim").mkdir(parents=True)
    (base / "a.pdf").write_bytes(b"%PDF")
    real = os.scandir

    def fake(path):
        if str(path).endswith("geheim"):
            raise PermissionError(13, "Zugriff verweigert")
        return real(path)

    monkeypatch.setattr(os, "scandir", fake)
    res = scan_directory(base)
    assert [f.name for f in res.files] == ["a.pdf"]
    assert res.problems and res.problems[0].kind == "zugriff_verweigert"


def test_validate_base_dir_errors(tmp_path):
    with pytest.raises(ValueError):
        validate_base_dir("")
    with pytest.raises(ValueError):
        validate_base_dir(str(tmp_path / "gibtsnicht"))
    f = tmp_path / "datei.txt"
    f.write_text("x")
    with pytest.raises(ValueError):
        validate_base_dir(str(f))


def test_sha256(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert sha256_file(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
