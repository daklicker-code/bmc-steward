"""Design: hell und dunkel für das ganze Programm, „automatisch“ nach System, Wahl gemerkt."""
import unittest
from unittest import mock

from tests._support import qapp

qapp()
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from bmc_steward import theme


class TestTheme(unittest.TestCase):
    def tearDown(self):
        theme.save_choice("auto")
        theme.apply(QApplication.instance(), "auto")

    def test_auto_follows_system_scheme(self):
        hints = QApplication.instance().styleHints()
        for scheme, name in ((Qt.ColorScheme.Dark, "dark"), (Qt.ColorScheme.Light, "light")):
            with mock.patch.object(type(hints), "colorScheme", return_value=scheme):
                self.assertEqual(theme.system_scheme(), name)
                self.assertEqual(theme.apply(QApplication.instance(), "auto"), name)

    def test_unknown_system_falls_back_per_platform(self):
        hints = QApplication.instance().styleHints()
        with mock.patch.object(type(hints), "colorScheme", return_value=Qt.ColorScheme.Unknown):
            for win, name in ((True, "light"), (False, "dark")):
                with mock.patch.object(theme, "WINDOWS", win):
                    self.assertEqual(theme.system_scheme(), name)

    def test_choice_is_remembered_and_applied(self):
        app = QApplication.instance()
        for choice in ("light", "dark"):
            theme.save_choice(choice)
            self.assertEqual(theme.load_choice(), choice)
            self.assertEqual(theme.apply(app), choice)
            self.assertEqual(app.palette().window().color().name(), theme.TOKENS[choice]["bg"])
            self.assertIn(theme.TOKENS[choice]["accent"], app.styleSheet())
        self.assertTrue(app.property("bmcFusion"))

    def test_listeners_hear_changes(self):
        seen = []
        theme.on_change(seen.append)
        theme.apply(QApplication.instance(), "light")
        theme.apply(QApplication.instance(), "dark")
        self.assertEqual(seen[-2:], ["light", "dark"])

    def test_both_designs_define_the_same_tokens(self):
        self.assertEqual(set(theme.TOKENS["dark"]), set(theme.TOKENS["light"]))


if __name__ == "__main__":
    unittest.main()
