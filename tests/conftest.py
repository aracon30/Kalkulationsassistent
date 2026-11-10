from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolierte Umgebung: eigene Datenbank, eigenes Datenverzeichnis, keine KI."""
    from app import config

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("HETA_DATA_DIR", str(data))
    monkeypatch.setenv("HETA_DB_PATH", str(data / "test.db"))
    monkeypatch.setenv("HETA_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("HETA_AI_PROVIDER", "none")
    monkeypatch.delenv("HETA_BASE_DIR", raising=False)
    config.reset_settings()
    from app.database import init_db

    init_db()
    yield tmp_path
    config.reset_settings()


@pytest.fixture()
def db(env):
    from app.database import new_session

    s = new_session()
    yield s
    s.close()


@pytest.fixture()
def offer_base(env):
    from tests.factory import make_offer_folder

    base = env / "Angebote"
    make_offer_folder(base)
    return base


def hash_tree(path: Path) -> dict[str, str]:
    import hashlib

    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob("*")) if p.is_file()}
