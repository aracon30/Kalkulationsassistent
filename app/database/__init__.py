"""Datenbankanbindung (SQLite, SQLAlchemy, Alembic-Migrationen)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import APP_ROOT, get_settings

_lock = threading.Lock()
_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


def run_migrations(db_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(APP_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def init_db(db_path: Path | None = None) -> Engine:
    """Initialisiert Engine und Schema (Alembic ``upgrade head``)."""
    global _engine, _factory
    with _lock:
        settings = get_settings()
        path = Path(db_path) if db_path else settings.database_path
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path.as_posix()}"
        if _engine is not None:
            _engine.dispose()
        run_migrations(url)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        event.listen(engine, "connect", _sqlite_pragmas)
        _engine = engine
        _factory = sessionmaker(bind=engine, expire_on_commit=False)
        return engine


def get_engine() -> Engine:
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


def new_session() -> Session:
    if _factory is None:
        init_db()
    assert _factory is not None
    return _factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI-Dependency."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def backup_database() -> Path:
    """Konsistentes Backup über die SQLite-Backup-API (auch während des Betriebs)."""
    settings = get_settings()
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    target = settings.backup_dir / f"heta_kalkulation_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(str(settings.database_path))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target
