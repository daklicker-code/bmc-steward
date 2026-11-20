# BMC-Steward – Hinweise für Claude

Sprache im Projekt: Deutsch (Commits, Release-Texte, Kommentare, Antworten an den Nutzer).

## Arbeitsweise

- **Direkt auf `main` arbeiten und pushen – keine Arbeitsbranches, keine Pull Requests.** Ausdrücklicher
  Wunsch des Nutzers; gilt auch, wenn die Umgebung einen `claude/…`-Branch vorgibt. Auf GitHub soll es nur
  `main` und die Versions-Tags geben. Nach der Änderung (Tests grün) ohne Nachfrage nach `main` pushen.
  Liegengebliebene `claude/…`-Branches auf GitHub löschen.
- **Nur eine Claude-Session arbeitet gleichzeitig am Repository.** Vor jedem Push: `git fetch origin main`
  und prüfen, ob dort inzwischen fremde Commits liegen (Claude-Session im Commit-Text). Wenn ja, die eigenen
  Commits darauf setzen (`git rebase origin/main`), Tests erneut laufen lassen, den Nutzer darauf hinweisen.
  Nie force-pushen.

## Version und Release-Text

- Jede Änderung am Programm bekommt eine neue Version: `__version__` in `bmc_steward/core.py` und
  `.github/release-notes/vX.Y.Z.md`. Erste Zeile `# BMC-Steward X.Y.Z – Titel`, dann `*English below.*`,
  der deutsche Text, `---` und derselbe Inhalt auf Englisch (Format siehe vorhandene Dateien).
- Reine Test- oder CI-Änderungen bekommen keine eigene Version.
- **Hilfe und README ohne Nachfrage mitpflegen:** Ändert sich, was der Nutzer sieht oder tun muss, im selben
  Commit die eingebaute Hilfe (`bmc_steward/i18n.py`, Hilfe-Texte in allen vier Sprachen) und die
  GitHub-Beschreibung (`README.md` und `README.de.md`) anpassen, soweit die Stelle dort beschrieben ist.
- Jeder Oberflächentext `T("…")` braucht Übersetzungen in `bmc_steward/i18n.py` (Englisch, Französisch,
  Schweizerdeutsch); `tests/test_i18n.py` prüft das.

## Release nach dem Push – ohne Nachfrage

Sobald eine neue Version auf `main` liegt, legst du Tag und GitHub-Release selbst an.
Der Nutzer will das nicht jedes Mal bestätigen.

1. Tags lassen sich aus der Cloud-Umgebung nicht pushen (der Proxy bricht `git push origin <tag>` ab).
   Tag und Release legt deshalb der Workflow **Releases** (`.github/workflows/releases.yml`) an, gestartet
   über die GitHub-Actions-API mit dem Eingabewert `ziel`.
2. **Achtung:** Der Workflow legt für *jede* Datei in `.github/release-notes/`, zu der es noch keinen
   Release gibt, einen an, und setzt *jeden* fehlenden Tag auf denselben `ziel`. Fehlen mehrere Versionen,
   landen sonst alle Tags auf einem Commit.
   - Vorher prüfen: `git ls-remote --tags origin 'v*'` gegen die Dateien in `.github/release-notes/`.
   - `ziel` immer als volle Commit-SHA angeben, nie `main` (ein Push dazwischen würde den Tag verschieben).
   - Die SHA mit `git rev-parse <commit>` ermitteln und von dort übernehmen, nie abtippen oder ergänzen.
   - Eine Version: Versions-Commit nach `main` pushen, Workflow auf `main` starten, `ziel` = SHA dieses Commits.
   - Mehrere Versionen: einzeln nacheinander nach `main` pushen (`git push origin <sha>:refs/heads/main`,
     älteste zuerst) und nach jedem Push einen Lauf mit `ziel` = SHA dieses Versions-Commits. Die nächste
     Version erst pushen, wenn der Lauf fertig ist – so ist immer genau eine Version ohne Release.
3. Ziel-Commit ist immer der Versions-Commit auf `main`.
4. Danach prüfen: Tag zeigt auf den richtigen Commit, Release ist veröffentlicht, die neueste Version ist
   „Latest“. Der Workflow startet anschließend selbst das Signieren (`attest.yml`).

## Tests

- Abhängigkeiten in einem frischen Container zuerst installieren, sonst scheitert schon der Import:
  `pip install --require-hashes -r requirements.lock -r requirements-optional.lock` und
  `apt-get install -y libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libasound2t64 zbar-tools tesseract-ocr tesseract-ocr-eng`.
- Ganze Suite: `python3 tests/run.py` (parallel, etwa 2 Minuten; Unit-Tests, statisches Audit, Fuzzing).
  Vor jedem Push laufen lassen. Mehr parallele Prozesse als Kerne (`-j`) führen zu Zeitüberschreitungen.
- Die Tracebacks „Exception occurred during processing of request“ im Testprotokoll sind Rauschen der
  simulierten Server (Client schließt die Verbindung), keine Fehlschläge.
- Simulierte Server bekommen zufällige Ports auf `127.0.0.1`. Ein neuer Testserver muss die
  Zertifikats-Merkungen seiner Adresse vergessen (`tests._support.forget_pins`), sonst sperrt die
  Zertifikatsprüfung zu Recht.
- Für einen Fehler zuerst einen Test schreiben, der ohne den Fix fehlschlägt, dann den Fix.
