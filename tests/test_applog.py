"""Gemeinsames Protokoll: Hauptfenster und Testlabor zeigen dasselbe, Stufen Info/Debug, Knopf „Protokoll“,
Meldungen schon beim Start, jede Aktion wird protokolliert, keine Passwörter."""
import os
import sys
import unittest
from unittest.mock import patch
from tests._support import qapp

app = qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QPushButton
from bmc_steward import applog
from bmc_steward.applog import DEBUG, INFO, LOG, ActionLogger, LevelCombo, LogLabel, LogView
from bmc_steward.main import Window


def texts():
    return [e.text for e in LOG.entries()]


class TestAppLog(unittest.TestCase):
    def setUp(self):
        LOG.clear()
        LOG.set_level(INFO)

    def test_info_is_default_and_debug_is_hidden(self):
        LOG.info("sichtbar")
        LOG.debug("technisch")
        self.assertEqual(texts(), ["sichtbar"])
        LOG.set_level(DEBUG)
        self.assertEqual(texts(), ["sichtbar", "technisch"])

    def test_switching_back_hides_debug_again(self):
        LOG.debug("technisch")
        LOG.set_level(DEBUG)
        self.assertEqual(texts(), ["technisch"])
        LOG.set_level(INFO)
        self.assertEqual(texts(), [])

    def test_text_has_time_level_and_indented_continuation(self):
        LOG.info("erste\nzweite")
        LOG.set_level(DEBUG)
        LOG.debug("detail")
        lines = LOG.as_text().split("\n")
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO  erste$")
        self.assertEqual(lines[1], "    zweite")
        self.assertRegex(lines[2], r" DEBUG detail$")

    def test_blank_messages_are_ignored(self):
        LOG.info("   ")
        LOG.info("")
        self.assertEqual(texts(), [])

    def test_buffer_is_bounded(self):
        with patch.object(applog, "MAX_ENTRIES", 100):
            log = applog.AppLog()                   # eigener Speicher, die Ansichten bleiben unberührt
        for i in range(150):
            log.info(str(i))
        got = [e.text for e in log.entries()]
        self.assertEqual(len(got), 100)
        self.assertEqual(got[0], "50")
        self.assertEqual(got[-1], "149")


class TestViews(unittest.TestCase):
    def setUp(self):
        LOG.clear()
        LOG.set_level(INFO)

    def test_view_shows_existing_and_new_lines(self):
        LOG.info("vorher")
        v = LogView()
        LOG.info("nachher")
        self.assertEqual(v.toPlainText().split("\n"), ["vorher", "nachher"])

    def test_empty_log_is_still_a_visible_widget(self):
        from PyQt6.QtWidgets import QVBoxLayout, QWidget
        parent = QWidget()
        QVBoxLayout(parent).addWidget(LogView())
        v = parent.findChild(LogView)
        self.assertEqual(v.toPlainText(), "")
        self.assertFalse(v.isHidden())

    def test_two_views_and_level_switch_stay_in_sync(self):
        a, b = LogView(), LogView()
        LOG.info("a")
        LOG.debug("d")
        self.assertEqual(a.toPlainText(), b.toPlainText())
        self.assertNotIn("d", a.toPlainText().split("\n"))
        LOG.set_level(DEBUG)
        self.assertEqual(a.toPlainText().split("\n"), ["a", "d"])
        self.assertEqual(b.toPlainText().split("\n"), ["a", "d"])

    def test_level_combos_follow_each_other(self):
        c1, c2 = LevelCombo(), LevelCombo()
        c1.setCurrentIndex(c1.findData(DEBUG))
        c1.activated.emit(c1.currentIndex())
        self.assertEqual(LOG.level, DEBUG)
        self.assertEqual(c2.currentData(), DEBUG)

    def test_status_label_logs_each_new_message_once(self):
        s = LogLabel("Bereit")
        s.setText("Liste geladen")
        s.setText("Liste geladen")
        s.setText("<b>Fertig</b>")
        s.setText("")
        self.assertEqual(texts(), ["Liste geladen", "Fertig"])


class TestMainWindow(unittest.TestCase):
    def setUp(self):
        LOG.clear()
        LOG.set_level(INFO)
        self.w = Window()

    def test_both_log_areas_show_the_same_log(self):
        self.w.say("Hersteller: Dell")
        self.assertIn("Hersteller: Dell", self.w.log.toPlainText())
        self.assertIn("Hersteller: Dell", self.w.vbmc_log.toPlainText())
        self.assertEqual(self.w.log.toPlainText(), self.w.vbmc_log.toPlainText())

    def test_save_buttons_are_gone_and_open_button_is_called_protokoll(self):
        names = [b.text() for b in self.w.findChildren(QPushButton)]
        self.assertIn("Protokoll", names)
        self.assertEqual([n for n in names if "Protokoll speichern" in n or "Protokoll öffnen" in n
                          or "Protokoll anzeigen" in n], [])
        self.assertFalse(hasattr(self.w, "save_log"))
        self.assertFalse(hasattr(self.w, "vbmc_save_log"))

    def test_protokoll_button_opens_text_file_without_dialog(self):
        self.w.say("Hostname ok")
        with patch("bmc_steward.main.QFileDialog.getOpenFileName") as dlg, \
             patch("bmc_steward.applog.QDesktopServices.openUrl", return_value=True) as opener:
            self.w.open_log()
            dlg.assert_not_called()
            opener.assert_called_once()
            path = opener.call_args[0][0].toLocalFile()
        try:
            self.assertTrue(path.endswith(".log"))
            self.assertIn("Hostname ok", open(path, encoding="utf-8").read())
        finally:
            os.unlink(path)

    def test_testlab_button_opens_the_same_log(self):
        self.w.say("gemeinsam")
        with patch("bmc_steward.applog.QDesktopServices.openUrl", return_value=True) as opener:
            self.w.vbmc_open_log()
            path = opener.call_args[0][0].toLocalFile()
        try:
            self.assertIn("gemeinsam", open(path, encoding="utf-8").read())
        finally:
            os.unlink(path)

    def test_empty_log_still_opens(self):
        LOG.clear()
        with patch("bmc_steward.applog.QDesktopServices.openUrl", return_value=True) as opener:
            self.w.open_log()
            path = opener.call_args[0][0].toLocalFile()
        os.unlink(path)

    def test_missing_editor_shows_path(self):
        self.w.say("Zeile")
        with patch("bmc_steward.applog.QDesktopServices.openUrl", return_value=False), \
             patch("bmc_steward.applog.QMessageBox.warning") as warn:
            self.w.open_log()
            warn.assert_called_once()

    def test_status_messages_land_in_the_log(self):
        self.w.status.setText("Profil „x“ gespeichert (ohne Passwörter)")
        self.assertIn("Profil „x“ gespeichert (ohne Passwörter)", texts())

    def test_testlab_status_lands_in_the_log(self):
        self.w.vbmc_set_status("Cluster gestoppt.")
        self.assertIn("Testlabor: Cluster gestoppt.", texts())

    def test_testlab_output_lines_land_in_the_log(self):
        class Proc:
            def state(self):                    # gilt als beendet: beim Schließen des Fensters wird nichts gestoppt
                from PyQt6.QtCore import QProcess
                return QProcess.ProcessState.NotRunning

            def readAllStandardOutput(self):
                return b"[12:00:00] Server CN123 gestartet\n@@STATE []\n"
        self.w.vbmc_proc = Proc()
        self.w.vbmc_read()
        self.assertIn("Testlabor: [12:00:00] Server CN123 gestartet", texts())
        self.assertFalse([t for t in texts() if "@@STATE" in t])

    def test_page_change_is_logged_only_in_debug(self):
        self.w.tabs.setCurrentIndex(2)
        name = self.w.tabs.menu.item(2).text()
        self.assertNotIn(f"Seite: {name}", texts())          # Bedienung flutet das Info-Protokoll nicht
        LOG.set_level(DEBUG)
        self.addCleanup(LOG.set_level, INFO)
        self.assertIn(f"Seite: {name}", texts())

    def test_debug_window_line_only_in_debug(self):
        LOG.clear()
        w = Window()
        self.assertFalse([t for t in texts() if t.startswith("Fenster aufgebaut")])
        LOG.set_level(DEBUG)
        self.assertTrue([t for t in texts() if t.startswith("Fenster aufgebaut")])
        w.close()


class TestActionLogger(unittest.TestCase):
    def setUp(self):
        LOG.clear()
        LOG.set_level(INFO)
        self.w = Window()
        self.w.show()
        LOG.clear()                                 # nur noch das, was die Prüfung selbst auslöst
        self.f = ActionLogger()

    def tearDown(self):
        self.w.close()

    def _click(self, button):
        pos = QPointF(button.rect().center())
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, button.mapToGlobal(pos),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.f.eventFilter(button, ev)

    def test_button_press_is_logged_with_its_label(self):
        b = QPushButton("Scharf starten", self.w)
        b.resize(100, 30)
        self._click(b)
        self.assertEqual(texts(), [])                         # Info: keine Klickmeldungen
        LOG.set_level(DEBUG)
        self.addCleanup(LOG.set_level, INFO)
        self.assertEqual(texts(), ["Knopf: Scharf starten"])

    def test_checkbox_state_is_logged(self):
        self.w.dry.setChecked(True)
        self._click(self.w.dry)
        app.processEvents()
        self.assertFalse([t for t in texts() if t.startswith("Haken „")])
        LOG.set_level(DEBUG)
        self.addCleanup(LOG.set_level, INFO)
        self.assertTrue([t for t in texts() if t.startswith("Haken „")])

    def test_disabled_button_is_not_logged(self):
        b = QPushButton("Gesperrt", self.w)
        b.resize(100, 30)
        b.setEnabled(False)
        self._click(b)
        self.assertEqual(texts(), [])

    def test_release_outside_button_is_not_a_click(self):
        b = QPushButton("Daneben", self.w)
        b.resize(100, 30)
        pos = QPointF(500, 500)
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos, Qt.MouseButton.LeftButton,
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.f.eventFilter(b, ev)
        self.assertEqual(texts(), [])

    def test_text_fields_are_never_logged(self):
        self.w.new_pass.setText("Geheim123!")
        app.processEvents()
        self.assertFalse([t for t in LOG.as_text().split("\n") if "Geheim123!" in t])


class TestStartup(unittest.TestCase):
    def test_startup_messages_exist_before_any_window(self):
        """run() schreibt Version, Sprache und Voreinstellung ins Protokoll, bevor das Fenster erscheint."""
        import bmc_steward.main as m
        LOG.clear()
        LOG.set_level(INFO)
        hook = sys.excepthook
        with patch.object(m, "Window"), patch.object(m, "QApplication") as qa, \
             patch.object(m, "set_language"), patch.object(m, "load_lang", return_value="de"), \
             patch.object(m.applog, "install"), patch.object(m, "MAIN", None), patch.dict(os.environ):
            qa.return_value.exec.return_value = 0
            try:
                m.run()
            finally:
                sys.excepthook = hook
        t = texts()
        self.assertTrue(any(x.startswith("BMC-Steward ") and x.endswith(" gestartet") for x in t))
        self.assertTrue(any(x.startswith("Sprache: ") for x in t))
        self.assertTrue(any("Trockenlauf ist voreingestellt" in x for x in t))
        self.assertEqual(t[-1], "BMC-Steward beendet")


if __name__ == "__main__":
    unittest.main()
