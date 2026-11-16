"""„Protokoll öffnen“ im Hauptfenster: öffnet das Protokoll als Textdatei, fragt NICHT nach einer Datei."""
import os
import unittest
from unittest.mock import patch
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.main import Window


class TestOpenLog(unittest.TestCase):
    def setUp(self):
        self.w = Window()

    def test_writes_textfile_and_opens_it_without_file_dialog(self):
        self.w.log.setPlainText("Hersteller: Dell, Anmeldung als root\nHostname ok")
        with patch("bmc_steward.main.QFileDialog.getOpenFileName") as dlg, \
             patch("bmc_steward.main.QDesktopServices.openUrl", return_value=True) as opener:
            self.w.open_log()
            dlg.assert_not_called()
            opener.assert_called_once()
            path = opener.call_args[0][0].toLocalFile()
        try:
            self.assertTrue(path.endswith(".log"))
            self.assertIn("Hostname ok", open(path, encoding="utf-8").read())
        finally:
            os.unlink(path)

    def test_empty_log_opens_nothing(self):
        self.w.log.clear()
        with patch("bmc_steward.main.QDesktopServices.openUrl") as opener:
            self.w.open_log()
            opener.assert_not_called()

    def test_missing_editor_shows_path_instead_of_failing(self):
        self.w.log.setPlainText("Zeile")
        with patch("bmc_steward.main.QDesktopServices.openUrl", return_value=False), \
             patch("bmc_steward.main.QMessageBox.warning") as warn:
            self.w.open_log()
            warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
