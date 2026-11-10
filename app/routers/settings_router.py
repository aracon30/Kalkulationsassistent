"""Einstellungen, Importprofile, KI-Status und Datenbank-Backup."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse

from app.ai import get_provider
from app.config import get_settings, update_settings
from app.database import backup_database
from app.services.excel_importer import ProfileError, load_profiles
from app.services.excel_reader import find_soffice
from app.services.folder_scanner import normalize_base_dir
from app.web import redirect, templates

router = APIRouter()


@router.get("/einstellungen")
def settings_page(request: Request, msg: str | None = None, err: str | None = None):
    s = get_settings()
    try:
        profiles, profile_error = load_profiles(s.profiles_dir), None
    except ProfileError as exc:
        profiles, profile_error = {}, str(exc)
    backups = sorted(s.backup_dir.glob("*.db"), reverse=True)[:10] if s.backup_dir.exists() else []
    return templates.TemplateResponse(request, "settings.html", {
        "nav": "einstellungen", "s": s, "profiles": profiles, "profile_error": profile_error, "provider": get_provider(),
        "soffice": find_soffice(), "backups": backups, "msg": msg, "err": err,
    })


@router.post("/einstellungen")
def save_settings(base_dir: str = Form(""), offer_number_patterns: str = Form(""), max_file_size_mb: int = Form(200)):
    import re

    patterns = [p.strip() for p in offer_number_patterns.splitlines() if p.strip()]
    for p in patterns:
        try:
            re.compile(p)
        except re.error as exc:
            return redirect("/einstellungen", err=f"Ungültiger regulärer Ausdruck '{p}': {exc}")
    update_settings(base_dir=normalize_base_dir(base_dir) or None, offer_number_patterns=patterns or get_settings().offer_number_patterns,
                    max_file_size_mb=max(1, min(max_file_size_mb, 2000)))
    return redirect("/einstellungen", msg="Einstellungen gespeichert")


@router.post("/einstellungen/backup")
def create_backup():
    path = backup_database()
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)
