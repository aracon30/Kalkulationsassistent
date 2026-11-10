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
- Kalkulationsdatei bewusst nicht importiert (`kopie_ignoriert`, Hinweis): Dateiname enthält „ungültig“;
  inhaltsgleiche Datei liegt in mehreren Angeboten (unveränderte Vorlage); inhaltsgleiche Kopie der
  Kalkulation eines anderen Angebots; oder Vorlagen-/Fremdkalkulation neben der eigenen Kalkulation.
  Fehlt „gewählt:“, wird der kalkulierte Preis übernommen – ohne Prüfpunkt, vermerkt in der Quellenangabe.

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

## 7. Neue Anfrage (Vorkalkulation)

Menü *Angebotserstellung → Neue Anfrage*, oder in einer Angebotsansicht „Neue Anfrage mit diesem
Apparat als Vorlage“.

1. **Eckdaten** eingeben (alles optional, mindestens ein Merkmal). Die Anfrage wird angelegt und der
   ähnlichste frühere Apparat als Vorlage gewählt.
2. **Ähnliche Apparate:** mit „als Vorlage“ eine andere Position wählen.
3. **Materialauszug der Vorlage** (je Gehäuse): je Bauteil Preis der Vorlage, Median (Anzahl Werte) und
   zuletzt angesetzter Preis aus allen Kalkulationen. Der „Ansatz“ ist mit dem Median vorbelegt, wenn
   mindestens zwei Werte vorliegen. Anzahl, Länge, Ansatz, APZ und Bearbeitung sind änderbar; Zeilen per
   Häkchen abwählbar. Schalter: „Median aller Angebote“, „Preise der Vorlage“, „zuletzt angesetzte Preise“.
   Die Summe wird als Materialkosten übernommen (abschaltbar, dann Materialkosten frei eintragen).
4. **Vorkalkulation** nach der HETA-Vorlage R05: Fertigung, Konstruktion, Dokumentation, Third Party,
   ZFP/TÜV, Beizen (Gewicht × €/kg), Lackieren, Hebesätze (MGK 8 %, Beschaffung 5 %, VGK 20 %,
   Risiko + Gewinn 20 %), Verpackung, Versand, Provision. Ergebnis sofort: Selbstkosten, Preis FCA je
   Gehäuse, €/kg, bei Stückzahl > 1 der Gesamtpreis. Daneben die Spanne der ähnlichen Apparate.
5. **Speichern** mit Notizen und Status (in Arbeit / angeboten / verworfen), **Drucken** für die Akte.

Die eigentliche Kalkulation erfolgt weiterhin in der Excel-Vorlage; die Anfrage ist Vorbereitung und
Plausibilitätsrahmen. Originaldaten werden nicht verändert. Sätze: `config/heta_kalkulation.yaml`.

## 7b. Neue Anfrage: Ersatzteile / Handelsware

Für Anfragen ohne Apparat (Filterkerzen, Beutel, Dichtungen, Armaturen): *Neue Anfrage → Ersatzteile /
Handelsware*. Kunde, RFQ-Nummer und Frist eintragen, dann je Artikel eine Position (gleiche Artikel aus
mehreren Kundenpositionen zusammenfassen, z. B. „2+3“). Sobald der Einkaufspreis vom Lieferanten vorliegt,
erscheint der Verkaufspreis je Stück und gesamt (Zuschläge wie im Blatt „Optionen“: 8 % / 20 % / 20 % →
Faktor 1,5552; änderbar). Unter „Frühere Preise suchen“ finden sich Handelsware aus früheren Angeboten
(mit EK, VK und Lieferant) und Bauteile aus Materialauszügen; „übernehmen“ setzt den EK in die zuletzt
bearbeitete Position. Notizen für Lieferantenanfragen, Beständigkeitsnachweise und Abweichungen;
„Drucken“ erzeugt eine Übersicht ohne Bedienelemente.

## 7a. Materialpreise

Alle Bauteilzeilen aus den Materialauszügen: Ansicht „Handelsware (EK/VK)“ für die Optionen-Blätter, „Preisspannen“ (gleiches Bauteil = Kategorie +
Form + Abmaße + Werkstoff + Einheit, ohne Abmaße zusätzlich Benennung) oder „Einzelzeilen“, Filter,
CSV-Export. €/kg ist für Rohre und Bleche aus Abmaßen geschätzt.

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
