"""Zentrale Konfiguration.

Quellen (in dieser Reihenfolge, spätere überschreiben frühere):
1. Standardwerte in ``Settings``
2. ``data/settings.json`` (in der Oberfläche bearbeitbar)
3. Umgebungsvariablen ``HETA_*`` bzw. ``.env``

API-Schlüssel werden ausschließlich aus Umgebungsvariablen gelesen und niemals
in ``settings.json`` gespeichert oder protokolliert.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel, Field

APP_ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = "0.1.0"
HOST = "127.0.0.1"
PORT = 8000

# Felder, die über die Oberfläche geändert und in settings.json gespeichert werden dürfen.
EDITABLE_FIELDS = {
    "base_dir",
    "offer_number_patterns",
    "max_file_size_mb",
    "ai_provider",
    "ai_external_enabled",
    "excluded_dir_names",
}


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Leser (keine zusätzliche Abhängigkeit). Bestehende Variablen gewinnen."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class Settings(BaseModel):
    data_dir: Path = APP_ROOT / "data"
    db_path: Path | None = None
    logs_dir: Path = APP_ROOT / "logs"
    profiles_dir: Path = APP_ROOT / "import_profiles"
    config_dir: Path = APP_ROOT / "config"

    base_dir: str | None = None
    # Angebotsnummern, z. B. SE-13705.26 (Gruppe "year" = zweistelliges Jahr)
    offer_number_patterns: list[str] = Field(
        default_factory=lambda: [r"(?<![A-Za-z0-9])SE[-_ ]?(?P<num>\d{5})\.(?P<year>\d{2})(?!\d)"]
    )
    excluded_dir_names: list[str] = Field(default_factory=lambda: ["$RECYCLE.BIN", "System Volume Information", ".git"])
    max_file_size_mb: int = 200

    ai_provider: str = "none"
    ai_external_enabled: bool = False
    ai_timeout_seconds: float = 60.0

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.data_dir / "heta_kalkulation.db")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def simulation_scheme_file(self) -> Path:
        return self.config_dir / "calculation_scheme.yaml"

    @property
    def similarity_file(self) -> Path:
        return self.config_dir / "similarity.yaml"

    def save_editable(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {k: getattr(self, k) for k in sorted(EDITABLE_FIELDS)}
        tmp = self.settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.settings_file)


_lock = threading.Lock()
_settings: Settings | None = None


def _env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}


def load_settings() -> Settings:
    _load_dotenv(APP_ROOT / ".env")
    data: dict = {}
    data_dir = Path(os.environ.get("HETA_DATA_DIR", APP_ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = (APP_ROOT / data_dir).resolve()
    data["data_dir"] = data_dir
    settings_file = data_dir / "settings.json"
    if settings_file.is_file():
        try:
            stored = json.loads(settings_file.read_text(encoding="utf-8"))
            data.update({k: v for k, v in stored.items() if k in EDITABLE_FIELDS})
        except (OSError, ValueError):
            pass  # defekte Einstellungsdatei -> Standardwerte
    if os.environ.get("HETA_DB_PATH"):
        data["db_path"] = Path(os.environ["HETA_DB_PATH"])
    if os.environ.get("HETA_LOGS_DIR"):
        data["logs_dir"] = Path(os.environ["HETA_LOGS_DIR"])
    if os.environ.get("HETA_BASE_DIR"):
        data.setdefault("base_dir", os.environ["HETA_BASE_DIR"])
    if os.environ.get("HETA_AI_PROVIDER"):
        data["ai_provider"] = os.environ["HETA_AI_PROVIDER"].strip().lower()
    if os.environ.get("HETA_AI_EXTERNAL_ENABLED"):
        data["ai_external_enabled"] = _env_bool(os.environ["HETA_AI_EXTERNAL_ENABLED"])
    if os.environ.get("HETA_AI_TIMEOUT_SECONDS"):
        data["ai_timeout_seconds"] = float(os.environ["HETA_AI_TIMEOUT_SECONDS"])
    return Settings(**data)


def get_settings() -> Settings:
    global _settings
    with _lock:
        if _settings is None:
            _settings = load_settings()
        return _settings


def reset_settings() -> None:
    """Für Tests und nach dem Speichern der Einstellungen."""
    global _settings
    with _lock:
        _settings = None


def update_settings(**changes) -> Settings:
    settings = get_settings()
    for key, value in changes.items():
        if key not in EDITABLE_FIELDS:
            raise ValueError(f"Einstellung '{key}' ist nicht änderbar")
        setattr(settings, key, value)
    settings.save_editable()
    return settings
