# Archiv: Windows-Build (nicht mehr gepflegt)

Die fertige Windows-`.exe` und ihr Build werden **nicht mehr gepflegt**. Der hier abgelegte
GitHub-Actions-Workflow `build-windows.yml` baute früher per PyInstaller eine `bmc-steward.exe`.
Er liegt bewusst **außerhalb** von `.github/workflows/`, damit GitHub ihn nicht mehr ausführt.

BMC-Steward läuft unter Windows weiterhin **aus dem Quellcode** (`python bmc-steward.py`), weil der
Code plattformübergreifend ist. Entwickelt und getestet wird aber unter Linux.

Wer den EXE-Build reaktivieren will: `build-windows.yml` zurück nach `.github/workflows/` kopieren.
