"""Standardgröße des Hauptfensters: 1680 × 980, aber nie größer als der verfügbare Bildschirm."""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from PyQt6.QtWidgets import QApplication
from bmc_steward.main import Window


class TestDefaultSize(unittest.TestCase):
    def test_default_is_1680_by_980_or_screen_limit(self):
        avail = QApplication.primaryScreen().availableGeometry()
        w, h = Window.default_size()
        self.assertEqual(w, min(1680, avail.width()))
        self.assertEqual(h, min(980, avail.height()))

    def test_never_larger_than_screen(self):
        avail = QApplication.primaryScreen().availableGeometry()
        w, h = Window.default_size()
        self.assertLessEqual(w, avail.width())
        self.assertLessEqual(h, avail.height())


if __name__ == "__main__":
    unittest.main()
