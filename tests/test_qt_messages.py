"""Qt-Konsolenmeldungen: Der KDE-Dateidialog (KIO) löst beim Aufräumen massenhaft
„QObject::disconnect: wildcard call disconnects from destroyed signal of …“ aus – harmloses Rauschen von
Qt 6 mit KDE Frameworks 6. Genau diese Meldung wird ausgeblendet, alle anderen Qt-Meldungen bleiben sichtbar."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CODE = r"""
from PyQt6.QtCore import qWarning, qDebug
import bmc_steward.main as m
m.install_qt_message_filter()
qWarning("QObject::disconnect: wildcard call disconnects from destroyed signal of KIO::Worker::unnamed")
qWarning("QObject::disconnect: wildcard call disconnects from destroyed signal of QNativeSocketEngine::unnamed")
qWarning("andere Warnung bleibt sichtbar")
qDebug("Debug bleibt sichtbar")
"""


class TestQtMessageFilter(unittest.TestCase):
    def run_code(self):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", BMC_STEWARD_DATA=tempfile.mkdtemp())
        env.pop("QT_MESSAGE_PATTERN", None)
        env.pop("QT_LOGGING_RULES", None)
        p = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stderr

    def test_kio_disconnect_noise_hidden_others_kept(self):
        err = self.run_code()
        self.assertNotIn("wildcard call disconnects", err)
        self.assertIn("andere Warnung bleibt sichtbar", err)
        self.assertIn("Debug bleibt sichtbar", err)
