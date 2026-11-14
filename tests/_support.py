"""Gemeinsame Hilfen für alle Tests: Offscreen-Qt, eine QApplication, Repo-Pfad, Skip-Helfer.

Qt-Module (core, redfish, scanner ...) brauchen zum Importieren keine echte Anzeige, aber einige
Bildfunktionen (QImage) und QObject/Signale wollen eine QApplication. Die legen wir genau einmal an.
Der Plattform-Treiber "offscreen" sorgt dafür, dass nichts auf den Bildschirm will.
"""
import os
import shutil
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_app = None


def qapp():
    """Liefert die eine QApplication (legt sie beim ersten Aufruf an)."""
    global _app
    if _app is None:
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
    return _app


def need(*binaries):
    """unittest-Skip-Dekorator, wenn eines der externen Programme (openssl/zbarimg/tesseract) fehlt."""
    missing = [b for b in binaries if not shutil.which(b)]
    return unittest.skipIf(missing, "fehlende Programme: " + ", ".join(missing))
