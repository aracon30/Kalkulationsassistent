"""Prüft den Import der realen Referenzdateien in samples/ gegen die Kontrollwerte aus dem Auftrag.

Aufruf:  python scripts/verify_samples.py
- Verwendet eine temporäre Datenbank (die Arbeitsdatenbank bleibt unberührt).
- Originaldateien werden nur gelesen; ihr SHA-256 wird vorher und nachher verglichen.
- Fehlende Dateien werden ausdrücklich gemeldet; es werden keine Werte erfunden.
Ergebnis: Konsolenbericht + samples/pruefbericht.md
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXPECTED_FILES = ["SE-13705.26_Kalkulation.xlsx", "SE-13705.26_Fertigungszeit.xlsx", "KF-3-301.11-005.pdf", "BF-3-1001.11-001.pdf"]
# Kontrollwerte laut Auftrag ("bisherige Auswertung") – NICHT als Originaldaten verwenden
CONTROL = {
    1: {"product_type": "Kerzenfilter", "dn": 300, "material": "1.4404", "filter_elements": 7, "filter_area": 5.74, "hours": 84, "offer_price": 33629.33},
    2: {"product_type": "Beutelfilter", "dn": 1000, "material": "1.4404", "filter_elements": 12, "filter_area": 5.52, "hours": 145, "offer_price": 60916.66},
}


def main() -> int:
    samples = ROOT / "samples"
    lines = ["# Prüfbericht Referenzdateien", ""]
    missing = [f for f in EXPECTED_FILES if not (samples / f).is_file()]
    for f in EXPECTED_FILES:
        lines.append(f"- {f}: {'FEHLT' if f in missing else 'vorhanden'}")
    if missing:
        lines += ["", "**Nicht alle Referenzdateien vorhanden – Abgleich nur für vorhandene Dateien möglich.**"]

    tmp = Path(tempfile.mkdtemp(prefix="heta_verify_"))
    os.environ["HETA_DATA_DIR"] = str(tmp)
    os.environ["HETA_DB_PATH"] = str(tmp / "verify.db")
    from app.config import reset_settings
    reset_settings()
    from sqlalchemy import select
    from app.database import init_db, new_session
    from app.models import Offer, ReviewIssue
    from app.services.folder_scanner import sha256_file
    from app.services.import_service import run_import, scan_and_inventory

    present = [samples / f for f in EXPECTED_FILES if f not in missing]
    if not present:
        print("\n".join(lines))
        print("\nKeine Referenzdateien gefunden. Bitte nach samples/ kopieren.")
        return 2
    before = {p: sha256_file(p) for p in present}
    # Kopie in einen Angebotsordner, damit auch Zeichnungen ohne Angebotsnummer im Namen zugeordnet werden
    import shutil
    work = tmp / "Angebote" / "2026" / "SE-13705.26"
    work.mkdir(parents=True)
    for p in present:
        shutil.copy2(p, work / p.name)
    init_db()
    s = new_session()
    scan_and_inventory(s, str(tmp / "Angebote"))
    summary = run_import(s)
    lines += ["", f"Import: {summary.imported} importiert, {summary.failed} fehlerhaft, Fehler: {summary.errors or 'keine'}", ""]
    offer = s.scalars(select(Offer).where(Offer.offer_number == "SE-13705.26")).first()
    if not offer:
        lines.append("Angebot SE-13705.26 wurde nicht angelegt.")
    else:
        lines += [f"Positionen erkannt: {len(offer.items)} (erwartet 2)", "", "| Pos | Feld | Kontrollwert | Import | Bewertung |", "|---|---|---|---|---|"]
        for pos, ctrl in CONTROL.items():
            item = next((i for i in offer.items if i.position_number == pos), None)
            calc = item.current_calculation if item else None
            ops_total = None
            if item:
                tot = [o.planned_hours for o in item.operations if o.operation_key == "gesamt"]
                ops_total = tot[0] if tot else None
            actual = {
                "product_type": item.product_type if item else None, "dn": item.dn if item else None,
                "material": item.material if item else None, "filter_elements": item.filter_elements if item else None,
                "filter_area": item.filter_area if item else None,
                "hours": ops_total if ops_total is not None else (calc.manufacturing_hours if calc else None),
                "offer_price": calc.offer_price if calc else None,
            }
            for k, exp in ctrl.items():
                act = actual[k]
                if act is None:
                    verdict = "nicht gefunden"
                elif isinstance(exp, (int, float)):
                    verdict = "OK" if abs(float(act) - exp) <= max(0.01, abs(exp) * 1e-6) else f"ABWEICHUNG ({float(act) - exp:+.2f})"
                else:
                    verdict = "OK" if str(act).lower() == str(exp).lower() else "ABWEICHUNG"
                lines.append(f"| {pos} | {k} | {exp} | {act if act is not None else '–'} | {verdict} |")
        issues = s.scalars(select(ReviewIssue).where(ReviewIssue.offer_id == offer.id, ReviewIssue.status == "offen")).all()
        lines += ["", f"Offene Prüfpunkte: {len(issues)}"] + [f"- [{i.code}] {i.message}" for i in issues[:60]]
    after = {p: sha256_file(p) for p in present}
    lines += ["", "Originaldateien unverändert: " + ("JA" if before == after else "NEIN – BITTE PRÜFEN")]
    s.close()
    report = "\n".join(lines)
    (samples / "pruefbericht.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
