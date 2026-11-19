"""Tests von BMC-Steward.

Arbeitsdaten der Tests (Zertifikats-Fingerabdrücke, Schlüsselbund-Zustand, Sicherungen ...) dürfen nie im
Repo-Ordner landen. Die Umgebungsvariable muss gesetzt sein, bevor irgendein Test bmc_steward.i18n lädt;
deshalb steht sie hier und nicht erst in tests/_support.py."""
import os as _os
import tempfile as _tempfile

_os.environ.setdefault("BMC_STEWARD_DATA", _tempfile.mkdtemp(prefix="bmc-steward-test-"))


# ---------------------------------------------------------------- Aufräumen nach jedem Test
# Viele Tests bauen Fenster (main.Window, Dialoge) und lassen sie offen. In einem Gesamtlauf sammeln sich
# so Hunderte unsichtbare Fenster an; jeder spätere Design-Wechsel und jede Ereignisschleife arbeitet sie
# alle mit ab. Der Gesamtlauf dauerte dadurch rund viermal so lang wie die Module einzeln (2026-11-19:
# 1544 s statt 390 s). Deshalb schließt und löscht dieser Haken nach jedem Test die Fenster, die während
# dieses Tests entstanden sind. Was setUpClass baut, bleibt für die übrigen Tests der Klasse stehen.
import unittest as _unittest

_orig_run = _unittest.TestCase.run


def _top_widgets():
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        return None, set()
    app = QApplication.instance()
    return app, (set(QApplication.topLevelWidgets()) if app is not None else set())


def _run_and_clean(self, result=None):
    _, before = _top_widgets()
    try:
        return _orig_run(self, result)
    finally:
        app, after = _top_widgets()
        new = [w for w in after - before]
        if app is not None and new:
            _close_quietly(new)


def _close_quietly(widgets):
    """Fenster ohne Rückfragen schließen und löschen. Abfragen („Liste nicht gespeichert“) würden im Test ewig
    auf einen Klick warten; sie werden deshalb für die Dauer des Aufräumens stummgeschaltet. Ein Fenster, das
    das Schließen ablehnt (z. B. weil noch ein Lauf aktiv ist), bleibt stehen, statt mitten im Lauf
    gelöscht zu werden."""
    from unittest.mock import patch
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QDialog, QMessageBox
    import sys
    no = QMessageBox.StandardButton.No
    # Eine Ausnahme in closeEvent (etwa durch ein Test-Double im Fenster) beendet unter PyQt6 sonst den ganzen
    # Prozess; beim Aufräumen ist sie egal. Der Test selbst ist zu diesem Zeitpunkt schon bewertet.
    with patch.object(sys, "excepthook", lambda *a: None), patch.object(QMessageBox, "exec", lambda *a, **k: 0), patch.object(QDialog, "exec", lambda *a, **k: 0), \
            patch.object(QMessageBox, "question", lambda *a, **k: no), \
            patch.object(QMessageBox, "warning", lambda *a, **k: no), \
            patch.object(QMessageBox, "information", lambda *a, **k: no), \
            patch.object(QMessageBox, "critical", lambda *a, **k: no):
        for w in widgets:
            try:
                if getattr(w, "dirty", None):
                    w.dirty = False             # sonst fragt das Hauptfenster nach dem Speichern
                if w.close():
                    w.deleteLater()
            except RuntimeError:                # von Qt schon gelöscht
                pass
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


_unittest.TestCase.run = _run_and_clean
