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
```

Exit-Code `0` = alles grün, sonst `1` (für CI/Pre-Push geeignet).

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
| `fuzz.py` | Zufalls-/Kaputtdaten an DHCP- und Scanner-Parser | Kein Absturz durch bösartige Eingaben |
| `audit.py` | Statische Mustersuche im Quelltext | Shell-Injektion, eval/exec, pickle, unsicheres TLS außerhalb der Ausnahmen |

## Externe Programme (optional)

`test_scanner.py` braucht **openssl**; Barcode-/OCR-Fuzzing nutzt **zbarimg**/**tesseract**, falls
vorhanden. Fehlt eines, wird der betroffene Test übersprungen (nicht als Fehler gewertet).

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
