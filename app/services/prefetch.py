"""Paralleles Vorauslesen von Excel- und PDF-Dateien für den Import.

Das Lesen (Kopie vom Netzlaufwerk, openpyxl zweimal, PDF-Text) ist der langsame Teil des Imports und
hängt nicht von der Datenbank ab. Es läuft deshalb in mehreren Prozessen voraus; geschrieben wird
weiterhin nacheinander in einer Transaktion je Angebot. Schlägt das Vorauslesen fehl, liest der
Import die Datei wie bisher selbst (gleiche Fehlermeldungen wie ohne Vorauslesen).
"""
from __future__ import annotations

import logging
import os
from collections import deque
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Callable

log = logging.getLogger(__name__)



def task_for(document_type: str, extension: str) -> str | None:
    from app.models import DocType
    if document_type in (DocType.KALKULATION, DocType.FERTIGUNGSZEIT) and extension in (".xlsx", ".xlsm"):
        return "excel"
    if document_type == DocType.ZEICHNUNG and extension == ".pdf":
        return "pdf"
    return None


def _work(kind: str, path: str, document_type: str, profiles_dir: str):
    """Läuft im Worker-Prozess. Gibt (True, Ergebnis) oder (False, Fehlertext) zurück."""
    try:
        if kind == "excel":
            from app.services.excel_importer import extract_workbook
            return True, extract_workbook(path, _profiles(profiles_dir), document_type)
        from app.services.pdf_importer import extract_pdf
        return True, extract_pdf(path)
    except Exception as exc:  # noqa: BLE001 - wird im Hauptprozess erneut ausgeführt
        return False, f"{type(exc).__name__}: {exc}"


_PROFILE_CACHE: dict[str, dict] = {}


def _profiles(profiles_dir: str) -> dict:
    if profiles_dir not in _PROFILE_CACHE:
        from app.services.excel_importer import load_profiles
        _PROFILE_CACHE[profiles_dir] = load_profiles(profiles_dir)
    return _PROFILE_CACHE[profiles_dir]


def default_workers() -> int:
    env = os.environ.get("HETA_IMPORT_WORKERS")
    if env and env.isdigit():
        return int(env)
    return max(1, min(4, (os.cpu_count() or 2) - 1))


class Prefetcher:
    """Liest Dateien in Bearbeitungsreihenfolge voraus (begrenztes Fenster, damit der Speicher klein bleibt)."""

    def __init__(self, items: list[tuple[int, str, str, str]], profiles_dir: str, workers: int | None = None, window: int | None = None):
        # items: (doc_id, kind, path, document_type) in der Reihenfolge, in der der Import sie anfordert
        self.workers = default_workers() if workers is None else workers
        self.window = window or self.workers * 3
        self.profiles_dir = profiles_dir
        self._queue = deque(items)
        self._futures: dict[int, Future] = {}
        self._pool: ProcessPoolExecutor | None = None
        if self.workers > 1 and items:
            try:
                self._pool = ProcessPoolExecutor(max_workers=self.workers)
            except (OSError, NotImplementedError) as exc:
                log.warning("Paralleles Lesen nicht verfügbar (%s) – lese nacheinander", exc)
        self._fill()

    def _fill(self) -> None:
        if self._pool is None:
            return
        while self._queue and len(self._futures) < self.window:
            doc_id, kind, path, dtype = self._queue.popleft()
            self._futures[doc_id] = self._pool.submit(_work, kind, path, dtype, self.profiles_dir)

    def get(self, doc_id: int, fallback: Callable[[], object]):
        """Ergebnis für ``doc_id``; ohne Vorauslesen oder bei Fehler wird ``fallback`` im Hauptprozess ausgeführt."""
        fut = self._futures.pop(doc_id, None)
        if fut is None:
            # nicht (mehr) vorgesehen – z. B. Dokument aussortiert; aus der Warteschlange entfernen
            self._queue = deque(i for i in self._queue if i[0] != doc_id)
            self._fill()
            return fallback()
        try:
            ok, result = fut.result()
        except Exception as exc:  # noqa: BLE001 - z. B. abgestürzter Worker
            ok, result = False, str(exc)
        self._fill()
        if ok:
            return result
        log.debug("Vorauslesen von Dokument %s fehlgeschlagen (%s) – lese erneut", doc_id, result)
        return fallback()

    def skip(self, doc_id: int) -> None:
        fut = self._futures.pop(doc_id, None)
        if fut is not None:
            fut.cancel()
        else:
            self._queue = deque(i for i in self._queue if i[0] != doc_id)
        self._fill()

    def close(self) -> None:
        if self._pool is not None:
            for f in self._futures.values():
                f.cancel()
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
