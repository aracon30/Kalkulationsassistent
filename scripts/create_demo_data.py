"""Erzeugt einen SYNTHETISCHEN Demo-Angebotsordner (keine HETA-Originaldaten).

Aufruf:  python scripts/create_demo_data.py [Zielordner]
Standard-Ziel: data/demo_angebote  -> danach im Import-Center als Basisverzeichnis angeben.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.factory import make_offer_folder  # noqa: E402

if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "demo_angebote"
    folder = make_offer_folder(target)
    print(f"Demo-Angebot (synthetisch) erzeugt: {folder}")
    print(f"Im Import-Center als Basisverzeichnis eintragen: {target}")
