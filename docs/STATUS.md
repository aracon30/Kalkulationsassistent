# Implementierungsstand und offene Punkte (MVP 0.1.0)

Stand: 10.11.2026 · Tests: `python -m pytest` → 85 bestanden (Linux, Python 3.13; LibreOffice-Test
wird ohne LibreOffice übersprungen).

## 0. Wichtigster offener Punkt: Referenzdateien fehlten

Die im Auftrag genannten Dateien (`samples/SE-13705.26_Kalkulation.xlsx`,
`SE-13705.26_Fertigungszeit.xlsx`, `KF-3-301.11-005.pdf`, `BF-3-1001.11-001.pdf`) waren im
Projektverzeichnis **nicht vorhanden**. Folgen:

- Der Abgleich mit den Kontrollwerten (Pos. 1: DN 300, 1.4404, 7 Kerzen, 5,74 m², 84 h,
  33.629,33 €; Pos. 2: DN 1000, 1.4404, 12 Beutel, 5,52 m², 145 h, 60.916,66 €) konnte **nicht**
  durchgeführt werden. Es wurden **keine Originalwerte erfunden**.
- (Stand 0.1.0) Die Profile `heta_2025`, `heta_2026`, `heta_fertigungszeit` beruhten auf den Begriffen aus dem
  Auftrag, nicht auf den echten Vorlagen. Sie sind als `validated: false` markiert; deshalb erhalten
  importierte Werte derzeit **immer** den Status „Prüfbedarf“.
- Getestet wurde mit **synthetischen** Dateien (`tests/factory.py`, Angebotsnummer `SE-99901.26`,
  frei erfundene Preise), die die beschriebene Struktur nachbilden: ein Angebot, zwei Apparate,
  ein Tabellenblatt je Position, Fertigungszeit mit einer Spalte je Position, zwei Zeichnungen.
- Nächster Schritt: Dateien nach `samples/` kopieren, `python scripts/verify_samples.py` ausführen,
  Abweichungen im Profil korrigieren, dann `validated: true` setzen.

## 1. Tatsächlich implementiert und getestet

| Bereich | Umfang |
|---|---|
| Grundsystem | FastAPI/Uvicorn auf 127.0.0.1:8000, SQLite + SQLAlchemy, Alembic-Migration beim Start, `start.bat`, Logging mit Rotation |
| Ordnerscan | rekursiv, lokale/Netz-/UNC-Pfade, SHA-256, Erkennung unveränderter Dateien (Pfad+Größe+Datum), Versionen bei Änderungen, Duplikate per Hash, Excel-Sperrdateien `~$`, Zugriffsfehler protokolliert, Größenlimit |
| Angebotsnummern | konfigurierbare Regex, Dateiname vor Ordner, Jahr aus Suffix; Revision aus `Rev2`, `_V3`, `Index B` |
| Klassifizierung | 7 Dokumenttypen, Punkte-Regeln aus Dateiname, Blattnamen, Zellbeschriftungen, PDF-Text, PDF-Metadaten, Seitenformat; 4 Status; Regeln werden gespeichert; Dateiname allein → höchstens „wahrscheinlich“ |
| Excel-Import | YAML-Profile mit Vererbung, Profilauswahl per Punktwertung, Beschriftungs- und Zellsuche, Einheiten-/Prozentzellen-Erkennung, Mehrpositionsmodus (Blatt je Position, Spalte je Position, Einzelposition), Typkonvertierung (deutsche Zahlen, Datum, Temperaturbereich) |
| Formeln | Laden mit `data_only=False` und `True`; Zelle, Formel, gespeicherter Wert, Blatt, Datei gespeichert; fehlender Cache → NULL + Prüfbedarf; veralteter Cache: sichere Nachrechnung einfacher Formeln (Grundrechenarten, SUM/SUMME, ROUND/RUNDEN, MIN, MAX, ABS, blattübergreifend) und Hinweis bei `fullCalcOnLoad` bzw. Nicht-Excel-Speicherung |
| Neuberechnung | optional mit LibreOffice, ausschließlich auf temporärer Kopie (`recalculate_copy`) |
| PDF | Textextraktion, Erkennung Zeichnungsnr., DN, PS, TS, Werkstoff, Regelwerk, Filterfläche, Elementanzahl, Produkttyp; Hinweis bei Scans ohne Text; Seitenvorschau (PNG) mit Zoom und Seitenwechsel |
| Zuordnung | Zeichnung → Position über Zeichnungsnummer, sonst Produkttyp-Präfix (KF/BF), sonst einzige Position; sonst Prüfpunkt |
| Datenqualität | Regeln: nicht negativ, plausible Bereiche (Stunden, Druck, DN-Reihe, Preis > 0), Preis < Selbstkosten, Fertigungskosten ≠ Stunden×Satz, Summe Arbeitsgänge ≠ Gesamt, Fertigungsstunden Kalkulation ≠ Fertigungszeit, Widersprüche zwischen Dokumenten, mehrdeutige Fundstellen, Werkstoff-Vereinheitlichung (1.4404 = 316L …) |
| Transaktionen | je Angebot eine Transaktion, je Datei ein Savepoint; Abbruch und Fehler rollen das Angebot vollständig zurück; Wiederholung idempotent (auch erzwungen) |
| Oberfläche | Dashboard, Import-Center (Hintergrundjob, Fortschritt, Abbruch, Protokoll, CSV), Dateiinventar, Angebotsübersicht mit Filtern, zweigeteilte Detailansicht mit Quellenanzeige per Klick, Prüfcenter mit Freigabe/Korrektur/Protokoll, Einstellungen, Backup |
| Phase 4 | Ähnlichkeitssuche (gewichtet, erklärbar, fehlende Merkmale ausgelassen), Kalkulationssimulation (konfigurierbares Schema, Live-Berechnung, getrennte Speicherung, Vergleich mit Original), KI-Schnittstelle mit `NoAIProvider` und `MockAIProvider` |
| Sicherheit | nur 127.0.0.1, Host-Prüfung, Origin-Prüfung für POST, Dateien nur per ID, nur lesende Dateizugriffe, keine Makros, parametrisierte SQL, Log-Maskierung von Schlüsseln, Backup |

## 2. Nur vorbereitet (nicht produktiv geprüft)

- **OllamaProvider** und **AzureFoundryProvider**: implementiert (HTTP, Timeout, 429-Retry, JSON-Schema-
  Validierung, Protokoll ohne Inhalte), aber **ohne echten Endpunkt getestet**. Aktiv nur bei
  vollständiger Konfiguration; extern zusätzlich nur mit `HETA_AI_EXTERNAL_ENABLED=true`.
  Endpunkt-/API-Version von Azure Foundry ist gegen die eigene Ressource zu prüfen.
- **M365CopilotProvider**: nur Platzhalter. Copilot-Lizenz, Entra-ID-Anmeldung (delegiert),
  Graph-Berechtigungen und API-Grenzen sind vorher mit der IT zu klären; eine Copilot-Lizenz
  ermöglicht nicht automatisch beliebige Modellinferenz.
- **KI-Kostenerfassung**: Tabelle `ai_requests` mit Feld `cost_estimate` vorhanden, Kosten werden
  noch nicht berechnet.
- **OCR für gescannte Zeichnungen**: nicht implementiert; nur Hinweis + optionale KI.
- **LibreOffice-Neuberechnung**: Funktion vorhanden und getestet, aber nicht in der Oberfläche
  angebunden (z. B. als Aktion im Prüfcenter für `formel_ohne_cache`).
- **Lieferantenangebote / Stücklisten**: werden klassifiziert und dem Angebot zugeordnet, aber
  inhaltlich nicht ausgewertet.

## 3. Welche Excel-Felder zuverlässig erkannt werden

Zuverlässig **in der synthetischen Testvorlage** (Beschriftung links, Wert rechts daneben):
alle Kopf-, Technik- und Kalkulationsfelder aus Abschnitt 5.2 des Auftrags sowie alle Arbeitsgänge
der Fertigungszeit. Für die **echten HETA-Vorlagen ist das unbestätigt**.

Robust gegenüber Layoutvarianten:
- Einheitenzellen zwischen Beschriftung und Wert (`€`, `h`, `bar`, `mm` …)
- Prozent-formatierte Zuschlagssätze vor dem Betrag (Satz wird übersprungen, Betrag gelesen)
- Beschriftungen mit Einheit in Klammern, Umlaute/ß, Groß-/Kleinschreibung
- Positionen als Blätter („Pos. 1“) oder Spalten („Pos. 1 | Pos. 2“)

Bekannte Grenzen:
- Beschriftung und Wert in verbundenen Zellen über größere Distanz (> 8 Spalten) – `max_distance` erhöhen.
- Positionen, deren Blätter nicht mit „Pos“/„Position“ + Nummer beginnen – `sheet_regex` anpassen.
- Text-Zahlen wie „33.629“ (ohne Komma) werden als Tausenderzahl 33629 gelesen.
- Formeln mit Funktionen außerhalb der unterstützten Liste (z. B. SVERWEIS, WENN) werden nicht
  nachgerechnet („nicht automatisch nachrechenbar“, kein Fehler).

## 4. Was manuell geprüft werden muss

- Alle Werte, solange das Profil nicht validiert ist (`profil_nicht_validiert`).
- Werte aus Zeichnungstext (DN, PS, TS, Werkstoff …): nie automatisch freigegeben, außer sie
  werden durch einen verifizierten Kalkulationswert bestätigt. Zeichnungsmaße sind keine
  Fertigungs- oder drucktechnisch freigegebenen Werte.
- Formeln ohne gespeicherten Wert und abweichende Formel-Caches.
- Zuordnungen „wahrscheinlich“ (Zeichnung über Produkttyp/Einzelposition zugeordnet).
- Mehrere Kalkulationsversionen in einem Angebot (gewählt wird die höchste Revision bzw. die neueste Datei).
- Unbekannte Werkstoff- oder Produkttypbezeichnungen.
- Alle KI-Vorschläge.

## 5. Noch nicht produktionsreif

- **Profile nicht validiert** (siehe Abschnitt 0) – Kernvoraussetzung für den produktiven Einsatz.
- **Windows-Lauf nicht real getestet**: Entwicklung und Tests liefen unter Linux. Pfadbehandlung
  ist plattformneutral (UNC-Zerlegung per Test abgedeckt), `start.bat` ist erstellt, aber ein Test
  auf Windows 11 mit echtem SMB-Laufwerk steht aus.
- **Zuschlagsschema der Simulation** ist ein Beispiel und nicht gegen HETA-Kalkulationen validiert.
- Geldbeträge als Gleitkommazahlen (`Float`) gespeichert – für Auswertungen ausreichend, für
  buchhalterische Genauigkeit wäre `Decimal` vorzuziehen.
- Kein Benutzer-/Rechtekonzept (Einzelplatzbetrieb; Name wird im Prüfprotokoll frei eingegeben).
- Keine Statushistorie für Angebote (Statusänderungen stehen im Prüfprotokoll).
- Bei sehr großen Beständen (> 10.000 Dateien) ist die Performance nicht gemessen;
  der Scan liest bei jedem Lauf alle Verzeichniseinträge, hasht aber nur geänderte Dateien.
- Erzwungener Neuimport legt Kalkulationswerte neu an; manuelle Korrekturen an
  Kalkulationswerten werden dabei nicht automatisch erneut angewendet.

## 6. Änderungen nach dem ersten Scan auf K:\ (0.1.1)

Erkenntnisse aus dem ersten Echtlauf (5.216 Dateien, „K:\01 HETA\01 ANGEBOTE\01 ab 2026“):

- **Angebotsnummern `AE-12345.26`** werden jetzt erkannt (Muster: zwei Buchstaben, 5 Ziffern, Punkt, Jahr).
  Ein unverändert gespeichertes altes Standardmuster (nur `SE-`) wird automatisch ersetzt.
- **Ordner vor Dateiname:** Die Nummer des Angebotsordners ist maßgeblich; nennt der Dateiname
  ein anderes Angebot (kopierte Referenz), wird das nur als Hinweis gespeichert.
- **Ordnerstruktur als Erkennungsmerkmal:** `…\02 Kalkulationen` → Kalkulation, `…\01 Maßblatt` /
  `Zeichnungen` → Zeichnung, `…\03 Angebot\AE-xxxxx.xx.pdf` → Kundenangebot u. a.
- **`*MUSTERORDNER*`** wird standardmäßig übersprungen (Ausschlussliste in den Einstellungen bearbeitbar).
- **Duplikate nur innerhalb eines Angebots:** dieselbe Datei in Ordnern verschiedener Angebote wird
  jedem Angebot zugeordnet.
- **Schnellerer Scan:** nicht auswertbare Dateien (Mails, Word, CAD, Bilder) werden nicht mehr gelesen
  (nur Größe/Datum); `.tmp` und `.lnk` werden ignoriert.
- **Windows-Pfade:** Dateien werden über das erweiterte Präfix `\\?\` geöffnet (lange Pfade, Namen mit
  Leerzeichen/Punkt am Ende).
- **Neu-Bewertung:** Bei geänderten Erkennungsregeln werden unveränderte, noch nicht importierte Dateien
  beim nächsten Scan neu klassifiziert, ohne sie erneut zu hashen (Datenbankmigration 0002).

## 7. Abgleich mit den Originaldateien SE-13705.26 (0.2.0)

Am 10.11.2026 lagen `SE-13705.26_Kalkulation.xlsx` und `SE-13705.26_Fertigungszeit.xlsx` vor
(die Zeichnungs-PDFs nicht). Daraus entstanden die Profile `heta_kalkulation_r05` (Vorlage
„Materialauszug / Kalkulation Behälterbau“, Stand R05_2023-05) und `heta_fertigungszeit`.
Ergebnis des vollständigen Imports: alle Felder an der richtigen Zelle, keine Fehlzuordnung.

| | Pos. 1 KF DN300 | Pos. 2 BF DN1000 |
|---|---|---|
| Material / Elemente / Mantel | 1.4404, 7 Kerzen, Ø323,9 × 4,5 | 1.4404, 12 Beutel, Ø1016 (Boden), 8 mm |
| Fertigungsstunden Kalkulation (= FZ gerundet) | 88 h | 156 h |
| Fertigungszeit je Behälter (FZ, ungerundet) | 83,06 h | 144,95 h |
| Selbstkosten (R über „Risiko + Gewinn“) | 27.890,52 € | 49.475,54 € |
| Kalkulierter Preis („Summe bei Gesamtabnahme“, FCA) | 33.468,62 € | 59.370,65 € |
| Angebotspreis („gewählt:“) | 33.750,00 € | 59.750,00 € |

**Abweichungen zu den Kontrollwerten des Auftrags** (33.629,33 € / 84 h bzw. 60.916,66 € / 145 h):
Kein Wert der Dateien entspricht den Kontrollpreisen; die Kontrollstunden passen nur zu Pos. 2
(145 h ≈ 144,95 h ungerundet), Pos. 1 liegt bei 83,06 h ungerundet bzw. 88 h gerundet. Die
Kontrollwerte stammen vermutlich aus einem älteren Dateistand. Filterfläche (5,74 / 5,52 m²) steht in
keiner der beiden Dateien. Fachlich zu bestätigen: Gilt „gewählt:“ als Angebotspreis?

Festlegungen im Profil (bitte bestätigen):
- Angebotspreis = „gewählt:“; zusätzlich wird der rechnerische Preis als „Kalkulierter Preis“ gespeichert.
- Kostenbeträge aus Spalte O (alle Gehäuse der Position), Selbstkosten aus Spalte R (je Stück).
- Gewicht = „Gewicht in Kg“ aus der Zeile Beizen/Passivieren.
- Arbeitsgänge werden mit ihrem Originaltext übernommen; zum Vergleich mit der Kalkulation
  dient „Fertigungszeit gerundet“.
- Dateien „Kalkulation R0x_…“ ohne Angebotsnummer gelten als Vorlagenkopie und werden ignoriert,
  wenn das Angebot eine Kalkulation mit Angebotsnummer im Namen hat.
