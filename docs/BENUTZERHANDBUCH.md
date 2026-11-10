# Benutzerhandbuch – HETA Kalkulationsassistent

## 1. Grundprinzip

Der Kalkulationsassistent liest Angebotsordner **nur lesend** ein, speichert die erkannten Werte
mit **Quellenangabe** (Datei, Tabellenblatt, Zelle bzw. PDF-Seite) in einer lokalen
SQLite-Datenbank und markiert alles, was nicht eindeutig ist, als **Prüfbedarf**.
Fehlende Werte bleiben leer („–“) und werden nie als 0 gespeichert.

Validierungsstatus jedes Wertes:

| Status | Bedeutung |
|---|---|
| **Verifiziert** | Aus einem validierten Importprofil, eindeutig, Formel-Cache konsistent, Plausibilitätsregeln erfüllt – oder manuell freigegeben |
| **Prüfbedarf** | Fehlend, widersprüchlich, mehrdeutig, aus Zeichnungstext, aus KI, oder Profil noch nicht validiert |
| **Nicht importierbar** | Datei beschädigt, gesperrt, nicht unterstütztes Format |

## 2. Import-Center

1. **Basisverzeichnis** eintragen. Unterstützt: lokale Pfade (`D:\Angebote`), Netzlaufwerke (`N:\Angebote`)
   und UNC-Pfade (`\\SERVER\Freigabe\Angebote`). Unterordner werden rekursiv durchsucht.
2. **Scan starten**: Dateien werden inventarisiert (SHA-256-Hash), klassifiziert, Angebotsnummer
   und Revision erkannt. Unveränderte Dateien (gleicher Pfad, Größe, Änderungsdatum) werden nicht
   erneut gelesen. Geänderte Dateien erhalten eine neue Version; inhaltsgleiche Kopien werden als
   Duplikat erkannt und übersprungen.
3. **Import starten**:
   - *Nur neue/geänderte Dateien* (Standard)
   - *Neue + fehlgeschlagene* – z. B. nachdem eine gesperrte Excel-Datei geschlossen wurde
   - *Alle erneut importieren* – ersetzt die abgeleiteten Daten der Dateien (keine Dubletten)
4. Fortschritt wird live angezeigt; der Browser bleibt bedienbar. **Abbrechen** stoppt nach der
   aktuellen Datei; das gerade bearbeitete Angebot wird vollständig zurückgerollt.
5. Je Job: **Protokoll** (mit Filter „Nur Fehler“) und **Importbericht als CSV** (Excel-kompatibel, `;`).

Angebotsnummern werden per regulärem Ausdruck erkannt (Standard: `SE-12345.26`), zuerst im
Dateinamen, sonst im nächstgelegenen Ordnernamen. Das Muster ist unter *Einstellungen* änderbar.

Geöffnete Excel-Dateien: Der Assistent liest eine Kopie. Ist die Datei so gesperrt, dass auch Lesen
scheitert, erscheint sie als „fehler“ und kann später mit *Neue + fehlgeschlagene* nachgeholt werden.

## 3. Dateiinventar

Alle gefundenen Dateien mit Dokumenttyp, Erkennungsstatus (*Eindeutig / Wahrscheinlich /
Manuelle Prüfung / Nicht unterstützt*), **angewendeten Erkennungsregeln** (aufklappen),
Version/Revision und Importstatus. Mit „ältere Versionen“ werden auch ersetzte Dateistände gezeigt.

## 4. Angebote

**Übersicht** mit Filtern: Angebotsnummer, Kunde, Jahr, Produkttyp, Werkstoff, DN, Druckbereich, Status.

**Detailansicht** (zweigeteilt):

- **Links**: Zeichnungs-/PDF-Vorschau mit Seitenwechsel, Zoom (−/+/Einpassen), „PDF öffnen“ und
  Dokumentauswahl; darunter alle Dokumente des Angebots mit Positionszuordnung.
- **Rechts**: Angebotskopf, je Position technische Daten, Kalkulation, Fertigungszeiten.
  - **Gestrichelt unterstrichene Werte anklicken** → Quellenangabe: Datei, Pfad, Tabellenblatt,
    Zelle, Beschriftung, Formel, gespeichertes Ergebnis, Methode, Sicherheit, Status, Hinweise.
    Bei Werten aus Zeichnungen: Seite und Textstelle, mit Link „in Vorschau zeigen“.
  - Gelb hinterlegt = Prüfbedarf. Die hochgestellte Zahl zeigt, wie viele Quellen den Wert belegen
    (z. B. Kalkulation **und** Zeichnung).
  - Mehrere Kalkulationsversionen sind aufklappbar; die aktuelle ist markiert.
- Angebotsstatus (offen/gewonnen/verloren/storniert) kann gesetzt werden – wird protokolliert.

## 5. Prüfcenter

Listet alle offenen Prüfpunkte, z. B.:

- Angebotspreis nicht gefunden (`preis_fehlt`)
- Excel-Formel ohne gespeicherten Ergebniswert (`formel_ohne_cache`)
- gespeicherter Formelwert weicht von Nachrechnung ab (`formel_cache_abweichung`)
- unterschiedliche Angaben in Zeichnung und Kalkulation (`widerspruch`)
- Fertigungsstunden Kalkulation ≠ Fertigungszeitkalkulation (`widerspruch_fertigungszeit`)
- mehrere mögliche Kalkulationsversionen (`mehrere_versionen`)
- Zeichnung keiner Position zuordenbar / Zuordnung unsicher
- Importprofil noch nicht validiert (`profil_nicht_validiert`)

Aktionen (immer mit Namen):

- **Freigeben** – Wert ist geprüft und korrekt → Status „Verifiziert“.
- **Verwerfen** – Hinweis ist nicht relevant.
- **Korrigieren** – Feld wählen, neuen Wert und Begründung eingeben. Der Wert wird im Datensatz
  ersetzt, ein Quelleneintrag „Manuelle Korrektur“ angelegt. Leerer Wert = „keine Angabe“.

Jede Aktion steht im **Prüfprotokoll** (Zeit, Benutzer, Objekt, Feld, alter/neuer Wert, Begründung).
Originaldateien werden nie verändert. Achtung bei „Alle erneut importieren“: Kalkulationswerte werden
dabei aus der Datei neu angelegt (Korrekturen an Kalkulationswerten müssten erneut erfolgen; sie
bleiben im Protokoll nachvollziehbar). Korrigierte technische Daten bleiben erhalten; weicht die Datei
davon ab, entsteht ein Prüfpunkt „widerspruch“.

## 6. Ähnlichkeitssuche

Technische Parameter einer Anfrage eingeben (Produkttyp, DN, PS, Werkstoff, Filterfläche, Gewicht,
Elementanzahl – alle optional). Ergebnis: die fünf ähnlichsten historischen Positionen mit
Ähnlichkeit, **Abdeckung** (Anteil der vergleichbaren Merkmale), Angebotspreis, €/kg,
Fertigungsstunden und Status. „Bewertung nachvollziehen“ zeigt je Merkmal die Teilähnlichkeit.

- Fehlende Merkmale werden nicht als 0 gewertet, sondern ausgelassen (geringere Abdeckung).
- Zahlen: logarithmischer Abstand mit Toleranzfaktor; Werkstoffe: gleiche Nummer = 100 %,
  gleiche Gruppe (z. B. Austenit 1.4404/1.4571) = 60 %.
- Gewichte und Toleranzen: `config/similarity.yaml`.

## 7. Kalkulationssimulation

Position wählen (übernimmt Werte der Originalkalkulation) oder leer beginnen; Eingaben ändern –
die Ergebnisse werden sofort neu berechnet. Angezeigt werden je Zeile **Kostenbasis, Zuschlagssatz,
Zuschlagsbetrag**, sowie **Selbstkosten, Gewinnzuschlag, Verkaufspreis, Gewinnmarge** (bezogen auf
den Verkaufspreis) und €/kg. Fehlende Eingaben werden genannt und nicht als 0 angenommen.

Das Zuschlagsschema (`config/calculation_scheme.yaml`) ist ein **Beispiel und nicht validiert** –
die Oberfläche weist darauf hin. Simulationen werden separat gespeichert und verändern nie die
importierte Originalkalkulation.

## 8. Einstellungen

Basisverzeichnis, Angebotsnummern-Muster, maximale Dateigröße; Übersicht der Importprofile
(validiert ja/nein), KI-Status, Datenbank-Backup (Download + Kopie in `data/backups/`).

## 9. Importprofile anpassen

Profile sind YAML-Dateien in `import_profiles/` (Beschreibung der Optionen im Kopf von
`generic_excel.yaml`). Ein Profil kann mit `extends:` ein anderes erweitern. Werte werden über
Beschriftungen gesucht (rechts daneben bzw. darunter) oder über feste Zellen (`cell: "Kalkulation!F42"`).
Mehrere Positionen werden automatisch erkannt: ein Blatt je Position (`Pos. 1`, `Pos. 2`, …) oder
eine Spalte je Position (Kopfzeile `Pos. 1 | Pos. 2`).

Vorgehen zur Validierung einer Vorlage:
1. Referenzdatei nach `samples/` legen, `python scripts/verify_samples.py` ausführen.
2. Abweichungen im Bericht prüfen, Beschriftungen/Zellen im Profil anpassen.
3. Wenn alle Kontrollwerte stimmen: `validated: true` setzen. Erst dann können Werte automatisch
   den Status „Verifiziert“ erhalten.

## 10. Optionale KI

Standardmäßig aus. Aktivierung über `.env` (`HETA_AI_PROVIDER`, siehe `.env.example`). Externe
Anbieter benötigen zusätzlich `HETA_AI_EXTERNAL_ENABLED=true`. In der Angebotsdetailansicht erscheint
dann „KI-Auswertung…“: Vor der Übertragung werden Anbieter, Dokument, Umfang und Textvorschau
angezeigt; die Übertragung muss bestätigt werden. Übertragen wird nur der lokal extrahierte Text.
KI-Ergebnisse werden als Vorschläge mit „Prüfbedarf“ gespeichert; ein Vorschlag, dessen Beleg nicht
wörtlich im Dokumenttext steht, wird zusätzlich als unbelegt markiert.

## 11. Protokolle und Datenablage

- Datenbank: `data/heta_kalkulation.db`, Backups: `data/backups/`
- Anwendungsprotokoll: `logs/app.log` (rotierend, ohne Zugangsdaten)
- Einstellungen: `data/settings.json`
