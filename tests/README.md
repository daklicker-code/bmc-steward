# BMC-Steward – Prüfsuite

Headless-Test- und Sicherheitsprüfung. Braucht keinen echten BMC, kein Display, keine zusätzlichen
Python-Pakete außer dem ohnehin benötigten **PyQt6**. Ausführen auf dem Entwicklungs-PC im Repo-Ordner.

## Starten

```bash
python3 tests/run.py              # Unit-Tests + statisches Audit + kurzes Fuzzing
python3 tests/run.py --quick      # ohne Fuzzing (schnell)
python3 tests/run.py --audit      # nur statisches Audit
python3 tests/run.py --tests      # nur Unit-Tests
python3 tests/run.py --fuzz 50000 # intensiveres Fuzzing
python3 tests/run.py -j 1         # Unit-Tests nacheinander in einem Prozess (langsam, nur zur Fehlersuche)
```

Exit-Code `0` = alles grün, sonst `1` (für CI/Pre-Push geeignet).

Die Unit-Tests laufen standardmäßig **parallel**: jedes Testmodul in einem eigenen Prozess (bis zu 8 gleichzeitig,
`-j N` ändert das). Die langsamsten Module starten zuerst (Dauern des letzten Laufs in `tests/.timings.json`, nicht
im Repo). Das dauert rund 75 Sekunden statt fast einer halben Stunde und verhindert, dass ein Modul
Zustand oder offene Fenster an das nächste vererbt. Zusätzlich schließt `tests/__init__.py` nach jedem Test die
Fenster, die der Test geöffnet hat (ohne Rückfragen); so bleibt auch ein Lauf mit `-j 1` oder
`python3 -m unittest discover` schnell genug.

## Was geprüft wird

| Datei | Prüft | Warum sicherheitsrelevant |
|-------|-------|---------------------------|
| `test_core.py` | IP-/Masken-/Hostname-Validierung, `mask()` | Falsche Adresse würde ausgerollt; Passwörter dürfen nie im Klartext ins Log |
| `test_redfish.py` | `xq()` (XML-Injektion), `scrub()` (Geheimnis-Schwärzung), RIBCL-Trockenlauf-Maskierung | Benutzer/Passwort/Hostname gehen in RIBCL-XML – Ausbruch verhindern |
| `test_dhcp.py` | DHCP-Options-/Paket-Parser, Adressvergabe | Server verarbeitet Pakete aus dem Netz; nie außerhalb des Bereichs vergeben |
| `test_scanner.py` | Handy-HTTPS-Endpunkt: Token-Zwang, Größenlimits, JSON | Größter Netz-Angriffspunkt (nimmt Bilder/Passwörter an) |
| `test_selfguard.py` | Eigenschutz: Schutzliste, eigene Kennung, Sperre vor UEFI/Firmware/Neustart | Das Tool darf nie den Host abschalten, auf dem es läuft |
| `test_i18n.py` | Jeder Oberflächentext in allen vier Sprachen, Platzhalter gleich | Keine halb deutschen Meldungen, keine Formatfehler zur Laufzeit |
| `test_vbmc.py` (Security) | Zertifikats-Fingerabdruck je Gerät, Sessions, ein Anmeldeversuch, Sicherung, Revisionsprotokoll, Härtung | Kein Passwort an ein fremdes Zertifikat, keine Kontosperre, Nachweis jeder Änderung |
| `test_generalprobe.py` | Generalprobe: jede Funktion einmal gegen virtuelle BMCs aller sieben Generationen (iDRAC 8/9, iLO 4/5/6, XCC, Supermicro) – Verbindungstest, Neuware scharf, UEFI, Dienste, Konten, Rotation, Firmware – und über die Oberfläche | Grüne Server nur mit dem neuen Administrator ansprechen (nie mit dem gesperrten Werkskonto); Fenster bleibt offen, solange Firmware oder Rotation läuft |
| `test_secrets_in_log.py` | Echte Trocken- und scharfe Läufe gegen virtuelle BMCs aller Hersteller | Kein Passwort im Protokoll, auch nicht auf Stufe Debug |
| `test_capture_submit.py` | Nachprüfung vor dem Einsenden einer Aufnahme | Keine Seriennummern, Adressen, Zertifikate oder Schlüssel in weitergegebenen Dateien |
| `test_vbmc_odata.py` | Virtuelle BMCs: jede Ressource typisiert, keine toten Verweise, `$metadata` | Grundlage für den DMTF-Validator in der CI (`tools/ci/validate_vbmc.py`) |
| `test_sushy.py` | Gegenprobe gegen sushy-tools (OpenStack, unabhängige Redfish-Implementierung) | Unbekannte Geräte: kein falscher Herstellerablauf, Aufnahme und Schwärzung funktionieren |
| `fuzz.py` | Zufalls-/Kaputtdaten an DHCP- und Scanner-Parser | Kein Absturz durch bösartige Eingaben |
| `audit.py` | Statische Mustersuche im Quelltext | Shell-Injektion, eval/exec, pickle, unsicheres TLS außerhalb der Ausnahmen |

## Externe Programme (optional)

`test_scanner.py` braucht **openssl**; Barcode-/OCR-Fuzzing nutzt **zbarimg**/**tesseract**, falls
vorhanden. Fehlt eines, wird der betroffene Test übersprungen (nicht als Fehler gewertet).

`test_sushy.py` braucht **sushy-emulator** (Paket `sushy-tools`): Pfad in `SUSHY_EMULATOR` oder im `PATH`.
Die CI installiert ihn in eine eigene Umgebung aus `tools/ci/sushy.lock`. Lokal z. B.:
`python3 -m venv /tmp/sushy && /tmp/sushy/bin/pip install --require-hashes -r tools/ci/sushy.lock` und dann
`SUSHY_EMULATOR=/tmp/sushy/bin/sushy-emulator python3 -m unittest tests.test_sushy`.

Unter CachyOS bei Bedarf: `sudo pacman -S openssl zbar tesseract tesseract-data-eng`

## Audit-Stufen

- **FAIL** – darf nicht vorkommen (bricht den Lauf ab).
- **INFO** – bewusst so gewollt und dokumentiert (abgeschaltete Zertifikatsprüfung für
  selbstsignierte BMC-Zertifikate, alte TLS-Versionen für iLO 3/4).
- **GOOD/WARN** – erwartete Absicherungen vorhanden bzw. fehlend.

## Erweitern

Neue Prüfung = neue `test_*`-Methode in der passenden Datei (Standard-`unittest`). Neues riskantes
Muster = Zeile in `FAIL_PATTERNS`/`INFO_PATTERNS` in `audit.py`. Neue Parser-Fläche = Funktion in
`fuzz.py` nach dem Vorbild von `fuzz_dhcp`.
