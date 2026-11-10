# Implementierungsstand und offene Punkte (MVP 0.1.0)

Stand: 10.11.2026 · Tests: `python -m pytest` → 102 bestanden (Linux, Python 3.13; LibreOffice-Test
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

Festlegungen im Profil (Stand 10.11.2026):
- Angebotspreis = „gewählt:“ (vom Fachbereich bestätigt). Fehlt „gewählt:“, wird der kalkulierte Preis
  übernommen und als Ersatzwert mit Prüfbedarf markiert.
- Alle Kostenwerte aus Spalte R („Preis bei Abnahme 1 Stück“ = ein Gehäuse inkl. einmaliger Kosten wie
  Konstruktion/Dokumentation). Passt zu Stunden und Gewicht je Gehäuse. Bei Stückzahl > 1 entsteht ein
  Prüfhinweis, weil der Bezug von „gewählt:“ (je Stück oder gesamt) dann nicht eindeutig ist.
- Gewicht = „Gewicht in Kg“ aus der Zeile Beizen/Passivieren.
- Arbeitsgänge mit Originaltext; Vergleich mit der Kalkulation über „Fertigungszeit gerundet“.
- „Kalkulation R0x_…“-Dateien sind Vorlagenkopien (bestätigt): ignoriert, wenn eine Kalkulation mit
  Angebotsnummer existiert, sonst Prüfhinweis.
- Die Profile `heta_kalkulation_r05` und `heta_fertigungszeit` sind auf `validated: true` gesetzt.
  Werte können damit „Verifiziert“ werden, wenn sie eindeutig sind und alle Regeln bestehen;
  Mehrdeutiges, Ersatzwerte und Widersprüche bleiben im Prüfcenter. Zurücksetzen: `validated: false`.
- Info-Hinweise (z. B. „Zeichnung ohne Textebene“, „Vorlagenkopie ignoriert“) bleiben im Prüfcenter
  sichtbar, verhindern aber nicht die Freigabe; nur Warnungen und Fehler halten den Status auf „Prüfbedarf“.
- Währung der Vorlage R05: EUR (alle Beträge sind dort als € formatiert).

## 8. Materialpreis-Datenbank (0.3.0)

Jede Zeile des Materialauszugs (Vorlage R05) wird als Bauteil gespeichert (Tabelle `material_lines`):
Benennung, Form, Abmaße, Norm, Werkstoff (Quelle + vereinheitlicht, z. B. 316L → 1.4404), Anzahl,
Länge, Einzelpreis mit Einheit (€/m bei Länge, sonst €/Stk), APZ-Kosten, Kosten, Fertigungs- und
Gesamtkosten der Zeile sowie die Quellzeile. Abmaße werden zerlegt (Ø × Wand, DN/PN, L × B × t,
Gewinde), jede Zeile erhält eine Kategorie (Rohr, Blech/Flachstahl, Flansch, Boden, Formteil,
Dichtung, Verbindungselement, Filterelement, Kauf-/Einbauteil, Sonstiges – Regeln in
`app/services/normalize.py`). Für Rohre und Bleche wird ein Gewicht und €/kg **geschätzt**
(Edelstahl 7,9 / Stahl 7,85 kg/dm³). Prüfung: Summe der Zeilen = Material Gesamtkosten.

Seite „Materialpreise“: Filter (Text, Kategorie, Werkstoff, Ø-Bereich, DN, Jahr), Ansicht
„Preisspannen“ (Anzahl, min/Median/max, zuletzt gezahlter Preis je Kategorie + Abmaß + Werkstoff)
oder „Einzelzeilen“, CSV-Export. In der Angebotsansicht je Position aufklappbar.

Getestet an SE-13705.26: 48 Bauteilzeilen, Summen stimmen, keine Prüfhinweise.
Offen: Bedeutung der Spalte „APZ Kosten“ (vermutlich Abnahmeprüfzeugnis) bestätigen; Einzelpreise
sind Kalkulationsansätze (Schätz-/Erfahrungswerte), keine Einkaufspreise.

## 9. Neue Anfrage / Vorkalkulation (0.4.0)

Die Kalkulationssimulation (Beispielschema) wurde entfernt und durch „Neue Anfrage“ ersetzt
(Tabelle `inquiries`, Migration 0005; Tabelle `simulations` entfällt):

1. Eckdaten (Produkttyp, DN, PS, Werkstoff, Elemente, Filterfläche, Gewicht, Stückzahl).
2. Fünf ähnliche Apparate; einer wird als Vorlage gewählt (Standard: der ähnlichste).
3. Materialauszug der Vorlage je Gehäuse: Preis der Vorlage, Median/Anzahl und zuletzt angesetzter
   Preis aus der Materialpreis-Datenbank; Ansatz (Standard: Median, wenn ≥ 2 Werte), Anzahl, Länge,
   APZ und Bearbeitungskosten änderbar; Umschalter Median / Vorlage / zuletzt.
   Mit „Preise der Vorlage“ ergibt sich exakt „Material Gesamtkosten“ des Originals (geprüft an SE-13705.26).
4. Vorkalkulation nach der HETA-Vorlage R05 (`app/services/heta_calc.py`, Sätze in
   `config/heta_kalkulation.yaml`): reproduziert Selbstkosten und Preis von SE-13705.26 auf den Cent.
   Vorbelegung aus der Vorlage (Stunden, Konstruktion, Dokumentation, Third Party, ZFP, Hebesätze).
5. Vergleichsrahmen (min/Median/max von Preis, €/kg, Stunden der ähnlichen Apparate), Notizen,
   Status (in Arbeit / angeboten / verworfen), Druckansicht.

Preisstatistik „gleiches Bauteil“ = Kategorie + Form + Abmaße + Werkstoff + Preiseinheit; ohne
Abmaße zusätzlich die Benennung (sonst würden z. B. Prallblech und Standfuß vermischt).
Grenzen: Teile mit gleicher Benennung, aber anderer Größe ohne Abmaßangabe (z. B. Davit für DN300
und DN1000) werden weiterhin zusammengefasst – Median bei solchen Teilen prüfen.

## 10. Handelsware / Ersatzteilanfragen (0.5.0)

**A) Handelsware aus Kalkulationen:** Blätter „Optionen“ (bzw. „Handelsware“, „Ersatzteile“) werden beim
Import gelesen (Tabelle `trade_items`, Migration 0006): Benennung, Menge, EK je Stück und gesamt, MGK,
VGK, Risiko + Gewinn, VK, Faktor VK/EK, „Quelle / Annahme“ (Lieferant, Datum). Einträge mit
„Schätzwert/anfragen“ sind als Schätzwert markiert. Geprüft an SE-13705.26 (3 Optionen, Faktor 1,5552).
Ansicht: *Materialpreise → Ansicht „Handelsware (EK/VK)“*, CSV-Export.

**B) Anfrage-Art „Ersatzteile / Handelsware“:** Kopf mit Kunde, RFQ-Nummer und Frist; Positionen
(Bezeichnung/Spezifikation, Menge, Einheit, Lieferant, EK, Lieferzeit); VK = EK × (1 + MGK) × (1 + VGK) ×
(1 + Risiko/Gewinn) wie im Blatt „Optionen“ (`app/services/trade_calc.py`, reproduziert die Originalwerte);
Pauschalen für Verpackung, Versand, Zertifikate; Positionen ohne EK bleiben „offen“. Suche nach früheren
Preisen in Handelsware und Materialauszügen mit „übernehmen“. Speichern, Status, Druckansicht.

Weitere Änderungen: Kategorie „Armatur“; „Siebkorbhalterung“/„Korbaufnahme“ nicht mehr als Filterelement.

## 11. Ersatzteil-Preistabellen und unbekannte Vorlagen (0.6.0)

Befund nach dem Import von „01 ab 2025“: 212 von ~635 Kalkulationsdateien passten nicht zur Vorlage R05.
Stichprobe (DE-13212.25-R00, DE-13457.25, DE-13704.26): keine Apparatekalkulationen, sondern
**Preistabellen für Ersatzteile/Handelsware** („Tabelle1“: Bezeichnung, Abmessung, Stückzahl, EK, Faktor,
VK, Lieferzeit … in wechselnder Spaltenfolge). Die leere Vorlage R06_01-2026 wird vom R05-Profil erkannt.

- Neues Profil `handelsware_tabelle`: Spalten werden über die Kopfzeile erkannt (Priorität der
  Beschriftungen, exakt vor Präfix, jede Spalte einmal – z. B. „EK / Stück“ vor „EK“, „VK3“ vor „VK“).
  Zeilen landen in der Handelswaren-Datenbank (inkl. Abmessung, Material, Lieferzeit, Zeichnung/StüLi,
  früherer Auftrag). Über mehrere Zeilen verbundene VK-Zellen = Baugruppenpreis → eigene Summenzeile
  „Baugruppe: …“ (EK = Summe der Einzelteile). Summen-/Erlöszeilen werden nicht als Artikel übernommen.
- Dateien, aus denen das allgemeine Ersatzprofil keinen einzigen Kalkulationswert liest, werden nicht mehr
  als Schein-Positionen angelegt, sondern „ignoriert“ mit Prüfhinweis `vorlage_unbekannt`.
- Migration 0007 (zusätzliche Spalten in `trade_items`). Geprüft an den drei Originaldateien.

## 12. Serviceeinsätze (0.7.0)

Ergebnis nach Neuimport „01 ab 2025“: profil_nicht_validiert 212 → 4, vorlage_unbekannt 40
(davon 8 × Nachkalkulation NK-2905.11, ~13 Listen/Hilfsdateien, 7 Service/Montage, ~11 weitere Kalkulationen).

Neues Profil `serviceeinsatz` (Vorlage „Serviceeinsatz / Kalkulation“, geprüft an DE-13511.26):
Kunde, Benennung, Zeilen (Pos., Benennung, Satz Deutschland/Ausland, Anzahl, Gesamtkosten), „Gesamt“ und
der angebotene Preis zwei Zeilen darunter. Tabellen `service_calcs`/`service_lines` (Migration 0008).
Zeilenart automatisch: Personal, Tagessatz/Spesen, Unterkunft, Reise, Fahrkosten/km. „Ausland“, wenn der
Betrag zum Auslandssatz passt. Anzeige: Angebotsansicht und *Materialpreise → Ansicht „Service“*
(Sätze je Leistung mit min/Median/max/zuletzt, Einsätze mit Stunden, Tagen, Nächten, km, Preis).
Offen: Montage-/Software-Kalkulationen (anderer Aufbau?) und ~11 Kalkulationen mit unbekanntem Aufbau.

## 13. Importgeschwindigkeit

Excel- und PDF-Dateien werden in mehreren Prozessen vorausgelesen (`app/services/prefetch.py`,
Standard: CPU-Kerne − 1, höchstens 4; `HETA_IMPORT_WORKERS=1` schaltet es ab). Die Datenbank wird
weiterhin nacheinander mit einer Transaktion je Angebot geschrieben; schlägt das Vorauslesen fehl,
liest der Import die Datei selbst (gleiche Fehlermeldungen). Außerdem wird nicht mehr nach jedem
einzelnen Wert in die Datenbank geschrieben (flush). Messung mit 80 synthetischen Dateien lokal:
4,6 s → 2,0 s. Auf Netzlaufwerken ist der Gewinn voraussichtlich größer (Kopieren läuft parallel),
aber nicht gemessen.
