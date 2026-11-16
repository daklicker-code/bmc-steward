"""Vollbildmodus der UEFI-Konsole.

Im Vollbild füllt das Fenster den Bildschirm; es bleiben nur die Konsole und die schmale Leiste mit
„Trennen“ und „Vollbild beenden“. Beim Beenden muss alles so zurückkommen, wie es vorher sichtbar war
(z. B. bleibt „Konsole starten“ versteckt, wenn es das schon war). Die Textkonsole passt im Vollbild ihre
Schrift dem Platz an. Alles läuft offscreen, die Fensteraufrufe werden nur mitgezählt.
"""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from PyQt6.QtWidgets import QWidget
from bmc_steward.console import TermScroll, TermView
from bmc_steward.main import Window


class TestTermViewFit(unittest.TestCase):
    def test_font_grows_to_fill_and_never_overflows(self):
        v = TermView()
        small = v.cw
        v.fit_to(1600, 900)
        self.assertGreater(v.cw, small)
        self.assertLessEqual(v.width(), 1600)
        self.assertLessEqual(v.height(), 900)

    def test_tiny_area_keeps_minimum_font(self):
        v = TermView()
        v.fit_to(10, 10)
        self.assertEqual(v.font().pointSize(), 8)

    def test_back_to_default_size(self):
        v = TermView()
        w0, h0 = v.width(), v.height()
        v.fit_to(1600, 900)
        v.set_font_size(TermScroll.DEFAULT_PT)
        self.assertEqual((v.width(), v.height()), (w0, h0))

    def test_resizing_font_keeps_grid(self):
        v = TermView()
        v.fit_to(1600, 900)
        self.assertEqual((v.cols, v.rows), (80, 24))


class TestFullscreen(unittest.TestCase):
    def setUp(self):
        self.w = Window()
        self.calls = []
        self.w.showFullScreen = lambda: self.calls.append("full")
        self.w.showNormal = lambda: self.calls.append("normal")
        self.w.showMaximized = lambda: self.calls.append("max")
        self.src = None
        self.w.console_source = lambda: self.src            # „Verbindung“ von Hand vorgeben
        # Fenster wird nie angezeigt; ob nie angefasste Kind-Widgets dann als „hidden“ gelten, hängt von Qt ab.
        # Darum Sichtbarkeit ausdrücklich setzen, dann sind die Prüfungen eindeutig.
        for part in (self.w.nav_side, self.w.status, self.w.con_panel, self.w.con_warn, self.w.con_copy,
                     self.w.con_connect, self.w.con_start, self.w.con_setup, self.w.con_fold, self.w.con_full,
                     self.w.con_disconnect, self.w.con_keys):
            part.setVisible(True)

    def connect(self):
        self.src = (QWidget(), True, "10.20.0.11")
        self.w.console_busy(True)

    def test_button_needs_a_connection(self):
        self.assertFalse(self.w.con_full.isEnabled())
        self.w.console_fullscreen(True)                     # ohne Verbindung: nichts
        self.assertFalse(self.w.con_is_full)
        self.assertEqual(self.calls, [])
        self.connect()
        self.assertTrue(self.w.con_full.isEnabled())

    def test_enter_hides_everything_but_console_and_bar(self):
        self.connect()
        self.w.console_fullscreen(True)
        self.assertTrue(self.w.con_is_full)
        self.assertEqual(self.calls, ["full"])
        for part in (self.w.nav_side, self.w.status, self.w.con_panel, self.w.con_warn, self.w.con_copy,
                     self.w.con_connect, self.w.con_setup, self.w.con_fold):
            self.assertTrue(part.isHidden(), part)
        self.assertFalse(self.w.con_full.isHidden())        # Ausstieg bleibt erreichbar
        self.assertFalse(self.w.con_disconnect.isHidden())
        self.assertFalse(self.w.con_keys.isHidden())        # Tastenleiste für die Textkonsole
        self.assertEqual(self.w.con_full.text(), "✕ Vollbild beenden")
        self.assertTrue(self.w.term_scroll.auto_fit)

    def test_exit_restores_previous_visibility(self):
        self.connect()
        self.w.con_start.setVisible(False)                  # war schon vorher versteckt (Textkonsole)
        self.w.console_panel_show(False)                    # Einstellungen waren eingeklappt
        self.w.console_fullscreen(True)
        self.w.console_fullscreen(False)
        self.assertEqual(self.calls, ["full", "normal"])
        self.assertFalse(self.w.nav_side.isHidden())
        self.assertFalse(self.w.status.isHidden())
        self.assertTrue(self.w.con_start.isHidden())
        self.assertTrue(self.w.con_panel.isHidden())
        self.assertEqual(self.w.con_full.text(), "⛶ Vollbild")
        self.assertFalse(self.w.term_scroll.auto_fit)

    def test_exit_returns_to_maximized_window(self):
        self.connect()
        self.w.isMaximized = lambda: True
        self.w.console_fullscreen(True)
        self.w.console_fullscreen(False)
        self.assertEqual(self.calls, ["full", "max"])

    def test_disconnect_leaves_fullscreen(self):
        self.connect()
        self.w.console_fullscreen(True)
        self.w.console_busy(False)                          # Trennen, Fehler oder Ende der Sitzung
        self.assertFalse(self.w.con_is_full)
        self.assertEqual(self.calls, ["full", "normal"])
        self.assertFalse(self.w.con_full.isEnabled())
        self.assertFalse(self.w.nav_side.isHidden())

    def test_button_toggles(self):
        self.connect()
        self.w.con_full.click()
        self.assertTrue(self.w.con_is_full)
        self.w.con_full.click()
        self.assertFalse(self.w.con_is_full)

    def test_repeated_calls_do_nothing(self):
        self.connect()
        self.w.console_fullscreen(True)
        self.w.console_fullscreen(True)
        self.assertEqual(self.calls, ["full"])
        self.w.console_fullscreen(False)
        self.w.console_fullscreen(False)
        self.assertEqual(self.calls, ["full", "normal"])


if __name__ == "__main__":
    unittest.main()
