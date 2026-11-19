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
# Arbeitsdaten der Tests (Zertifikats-Fingerabdrücke, Sicherungen, Revisionsprotokoll) nie ins Repo schreiben
import tempfile as _tempfile
os.environ.setdefault("BMC_STEWARD_DATA", _tempfile.mkdtemp(prefix="bmc-steward-test-"))

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


def forget_pins(addr):
    """Zufällige Testports wiederholen sich in einem Lauf: alte Zertifikats-Merkungen für diese Adresse
    entfernen, sonst sperrt die Prüfung (zu Recht) ein „anderes Gerät“ unter derselben Adresse. Aufrufen,
    sobald ein frischer Testserver seinen Port hat – nie zwischen Neustarts desselben Servers, deren
    Zertifikats-Merkung ein Test gerade prüfen will."""
    from bmc_steward import trust
    st = trust.store()
    for key in list(st.for_addr(addr)):
        st.forget(key)


def need(*binaries):
    """unittest-Skip-Dekorator, wenn eines der externen Programme (openssl/zbarimg/tesseract) fehlt."""
    missing = [b for b in binaries if not shutil.which(b)]
    return unittest.skipIf(missing, "fehlende Programme: " + ", ".join(missing))
