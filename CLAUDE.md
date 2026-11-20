# BMC-Steward – Hinweise für Claude

Sprache im Projekt: Deutsch (Commits, Release-Texte, Kommentare, Antworten an den Nutzer).

## Arbeitsweise

- **Nur eine Claude-Session arbeitet gleichzeitig am Repository.** Bevor du auf einen Branch pushst oder
  einen Pull Request anlegst: `git fetch` und prüfen, ob auf `main` inzwischen fremde Commits liegen
  (Claude-Session im Commit-Text). Wenn ja, erst darauf aufbauen und den Nutzer darauf hinweisen.
- Nie direkt auf `main` pushen. Änderungen gehen über einen Branch und einen Pull Request.

## Version und Release-Text

- Jede Änderung am Programm bekommt eine neue Version: `__version__` in `bmc_steward/core.py` und
  `.github/release-notes/vX.Y.Z.md`. Erste Zeile `# BMC-Steward X.Y.Z – Titel`, dann `*English below.*`,
  der deutsche Text, `---` und derselbe Inhalt auf Englisch (Format siehe vorhandene Dateien).
- Reine Test- oder CI-Änderungen bekommen keine eigene Version.
- Jeder Oberflächentext `T("…")` braucht Übersetzungen in `bmc_steward/i18n.py` (Englisch, Französisch,
  Schweizerdeutsch); `tests/test_i18n.py` prüft das.

## Release nach dem Merge – ohne Nachfrage

Sobald ein Pull Request mit einer neuen Version gemergt ist, legst du Tag und GitHub-Release selbst an.
Der Nutzer will das nicht jedes Mal bestätigen.

1. Tags lassen sich aus der Cloud-Umgebung nicht pushen (der Proxy bricht `git push origin <tag>` ab).
   Tag und Release legt deshalb der Workflow **Releases** (`.github/workflows/releases.yml`) an, gestartet
   über die GitHub-Actions-API mit dem Eingabewert `ziel`.
2. **Achtung:** Der Workflow legt für *jede* Datei in `.github/release-notes/`, zu der es noch keinen
   Release gibt, einen an, und setzt *jeden* fehlenden Tag auf denselben `ziel`. Fehlen mehrere Versionen,
   landen sonst alle Tags auf einem Commit.
   - Vorher prüfen: `git ls-remote --tags origin 'v*'` gegen die Dateien in `.github/release-notes/`.
   - `ziel` immer als volle Commit-SHA angeben, nie `main` (ein Push dazwischen würde den Tag verschieben).
   - Fehlt genau eine Version: Workflow auf `main` starten, `ziel` = SHA des Merge-Commits dieser Version.
   - Fehlen mehrere: je Version einen eigenen Lauf, älteste zuerst. Den Lauf auf einem Ref starten, dessen
     neueste Release-Notes-Datei genau diese Version ist (z. B. den eigenen, schon gemergten Arbeitsbranch
     per `--force-with-lease` auf den Versions-Commit setzen), `ziel` = SHA dieses Commits. Erst wenn der
     Lauf fertig ist, den nächsten starten. Danach den Arbeitsbranch wieder auf `main` vorspulen.
3. Ziel-Commit ist der Merge-Commit des Pull Requests (bzw. der Versions-Commit, wenn direkt auf `main`).
4. Danach prüfen: Tag zeigt auf den richtigen Commit, Release ist veröffentlicht, die neueste Version ist
   „Latest“. Der Workflow startet anschließend selbst das Signieren (`attest.yml`).

## Tests

- Abhängigkeiten in einem frischen Container zuerst installieren, sonst scheitert schon der Import:
  `pip install --require-hashes -r requirements.lock -r requirements-optional.lock` und
  `apt-get install -y libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libasound2t64 zbar-tools tesseract-ocr tesseract-ocr-eng`.
- Ganze Suite: `python3 tests/run.py` (parallel, etwa 2 Minuten; Unit-Tests, statisches Audit, Fuzzing).
  Vor jedem Pull Request laufen lassen.
- Die Tracebacks „Exception occurred during processing of request“ im Testprotokoll sind Rauschen der
  simulierten Server (Client schließt die Verbindung), keine Fehlschläge.
- Simulierte Server bekommen zufällige Ports auf `127.0.0.1`. Ein neuer Testserver muss die
  Zertifikats-Merkungen seiner Adresse vergessen (`tests._support.forget_pins`), sonst sperrt die
  Zertifikatsprüfung zu Recht.
- Für einen Fehler zuerst einen Test schreiben, der ohne den Fix fehlschlägt, dann den Fix.
