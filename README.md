# HETA Kalkulationsassistent (MVP)

Lokale Web-Anwendung zum Einlesen, Strukturieren und Auswerten historischer Angebotsprojekte
(Kalkulationen, Fertigungszeiten, Zeichnungen). Läuft vollständig lokal unter
`http://127.0.0.1:8000` – ohne Docker, ohne Node.js, ohne Cloud-Datenbank, ohne KI-Zwang.

> **Status:** lauffähiges MVP (Phase 1–3 vollständig, Phase 4 in erster Ausbaustufe).
> Die Importprofile sind **noch nicht gegen die HETA-Originaldateien validiert**, weil die
> Referenzdateien bei der Entwicklung nicht vorlagen. Details: [`docs/STATUS.md`](docs/STATUS.md).

## Installation und Start unter Windows 11

Voraussetzung: Python 3.12 oder neuer ([python.org](https://www.python.org/downloads/), bei der
Installation „Add python.exe to PATH“ bzw. den `py`-Launcher aktivieren).

1. Ordner `heta-kalkulationsassistent` an einen lokalen Ort kopieren (z. B. `C:\HETA\kalkulationsassistent`).
2. `start.bat` doppelklicken.
   - Beim ersten Start wird `.venv` angelegt und `requirements.txt` installiert (Internet nötig).
   - `.env` wird aus `.env.example` erzeugt, falls nicht vorhanden.
   - Der Browser öffnet `http://127.0.0.1:8000`.
3. Im **Import-Center** das Basisverzeichnis eintragen (z. B. `\\SERVER\Freigabe\Angebote`
   oder `D:\Angebote`), **Scan starten**, danach **Import starten**.

Beenden: Konsolenfenster schließen oder `Strg+C`.

Manueller Start (alle Plattformen):

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Demo ohne Originaldaten

```bash
python scripts/create_demo_data.py
```

erzeugt ein **synthetisches** Angebot `SE-99901.26` (2 Positionen, Kalkulation, Fertigungszeit,
2 Zeichnungen) unter `data/demo_angebote`. Dieses Verzeichnis im Import-Center scannen.

### Abgleich mit den Referenzdateien

Die vier Referenzdateien (`SE-13705.26_Kalkulation.xlsx`, `SE-13705.26_Fertigungszeit.xlsx`,
`KF-3-301.11-005.pdf`, `BF-3-1001.11-001.pdf`) nach `samples/` kopieren, dann:

```bash
python scripts/verify_samples.py
```

Der Abgleich läuft in einer temporären Datenbank auf Kopien, vergleicht mit den Kontrollwerten
aus dem Auftrag, prüft per SHA-256, dass die Originale unverändert bleiben, und schreibt
`samples/pruefbericht.md`. `samples/` ist von Git ausgeschlossen (vertrauliche Kundendaten).

## Tests

```bash
python -m pytest
```

75 automatisierte Tests: Ordnerscan, Angebotsnummern, UNC-Pfade, Klassifizierung, Excel-Import,
Mehrpositionsangebote, Formel-Caches (fehlend/veraltet), Dubletten, Importwiederholung,
Transaktionen/Abbruch, Kalkulationslogik, Ähnlichkeitssuche, KI-Provider-Auswahl, Weboberfläche,
Sicherheit (Host-/Origin-Prüfung, Dateizugriff nur per ID), Backup.
Die Tests verwenden ausschließlich synthetische Dateien aus `tests/factory.py`.

## Projektstruktur

```text
heta-kalkulationsassistent/
├── app/
│   ├── main.py                 FastAPI-App, Sicherheits-Middleware, Logging
│   ├── config.py               Einstellungen (.env, data/settings.json)
│   ├── web.py                  Templates, deutsche Zahlen-/Datumsformate
│   ├── database/               SQLite-Engine, Alembic-Migration beim Start, Backup
│   ├── models/                 SQLAlchemy-Modelle
│   ├── routers/                Dashboard, Import-Center, Angebote, Prüfcenter, Analyse, Einstellungen
│   ├── services/
│   │   ├── folder_scanner.py   rekursiver Scan, SHA-256, Sperrdateien, Zugriffsfehler
│   │   ├── offer_numbers.py    Angebotsnummern (Regex), Revisionen
│   │   ├── document_classifier.py  regelbasierte Klassifizierung mit Begründung
│   │   ├── excel_reader.py     Lesen auf temporärer Kopie, Formeln + gespeicherte Werte
│   │   ├── formula_check.py    sichere Nachrechnung einfacher Formeln (Cache-Prüfung)
│   │   ├── excel_importer.py   profilbasierte Extraktion (YAML)
│   │   ├── pdf_importer.py     Textextraktion, Schlüsselbegriffe, Seitenvorschau
│   │   ├── import_service.py   Scan/Import-Ablauf, Transaktionen, Prüfbedarf
│   │   ├── jobs.py             Hintergrundjobs mit Abbruch
│   │   ├── validation.py       Plausibilitätsregeln, Validierungsstatus
│   │   ├── normalize.py        Werkstoffe, Produkttypen, Temperaturen, Datum
│   │   ├── calculation_engine.py  konfigurierbare Zuschlagskalkulation
│   │   └── similarity.py       gewichtete, erklärbare Ähnlichkeitssuche
│   ├── ai/                     Provider-Schnittstelle (None, Mock, Ollama, Azure Foundry, M365 vorbereitet)
│   ├── templates/              Jinja2-Oberfläche (deutsch)
│   └── static/                 CSS, Vanilla JS
├── import_profiles/            YAML-Importprofile
├── config/                     Zuschlagsschema, Ähnlichkeitsgewichte
├── alembic/                    Datenbankmigrationen
├── scripts/                    Demo-Daten, Abgleich mit Referenzdateien
├── tests/                      pytest
├── docs/                       Benutzerhandbuch, Status/offene Punkte
├── samples/  data/  logs/      (nicht versioniert)
├── requirements.txt  .env.example  start.bat
```

## Sicherheit (Kurzfassung)

- Server lauscht nur auf `127.0.0.1`; fremde `Host`-Header werden abgewiesen (DNS-Rebinding-Schutz),
  schreibende Anfragen von fremder Herkunft (`Origin`/`Referer`) werden mit 403 abgelehnt.
- Originaldateien werden nur lesend geöffnet; Excel wird auf einer temporären Kopie ausgewertet.
  Keine Makroausführung, keine Ausführung von Inhalten aus PDF/Excel.
- Dateien werden nur über ihre Datenbank-ID ausgeliefert (kein Pfad aus der Anfrage → kein Path Traversal).
- Alle Datenbankzugriffe über SQLAlchemy (parametrisiert).
- API-Schlüssel nur über Umgebungsvariablen; Log-Filter maskiert Schlüsselwerte. Keine Telemetrie,
  kein Cloud-Upload. KI ist standardmäßig aus; externe Provider brauchen zusätzlich
  `HETA_AI_EXTERNAL_ENABLED=true` und eine Bestätigung pro Dokument.
- Datenbank-Backup über *Einstellungen → Backup* (SQLite-Backup-API, konsistent im laufenden Betrieb).

Weitere Dokumentation: [`docs/BENUTZERHANDBUCH.md`](docs/BENUTZERHANDBUCH.md), [`docs/STATUS.md`](docs/STATUS.md).
