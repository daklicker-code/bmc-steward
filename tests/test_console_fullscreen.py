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
        self.assertFalse(self.w.con_fs_handle.isHidden())   # Streifen oben bleibt: Ausstieg ist erreichbar
        self.assertFalse(self.w.con_keys.isHidden())        # Tastenleiste für die Textkonsole
        self.assertTrue(self.w.con_tools.isHidden())        # Leiste ist standardmäßig eingeklappt: Konsole bekommt alles
        self.assertTrue(self.w.con_full.isHidden())
        self.assertTrue(self.w.con_disconnect.isHidden())
        self.assertEqual(self.w.con_lay.contentsMargins().left(), 0)
        self.w.con_fs_handle.click()                        # Leiste einblenden
        self.assertFalse(self.w.con_full.isHidden())
        self.assertFalse(self.w.con_disconnect.isHidden())
        self.assertFalse(self.w.con_tools.isHidden())
        self.w.con_fs_handle.click()                        # wieder einklappen
        self.assertTrue(self.w.con_full.isHidden())
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
        self.assertTrue(self.w.con_fs_handle.isHidden())
        self.assertFalse(self.w.con_full.isHidden())        # Leiste und Ränder wie vorher
        self.assertFalse(self.w.con_disconnect.isHidden())
        self.assertFalse(self.w.con_tools.isHidden())
        self.assertGreater(self.w.con_lay.contentsMargins().left(), 0)

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

    def test_button_click_in_collapsed_bar_state_still_exits_via_handle(self):
        self.connect()
        self.w.console_fullscreen(True)
        self.w.con_fs_handle.click()                        # einblenden
        self.w.con_full.click()                             # „Vollbild beenden“
        self.assertFalse(self.w.con_is_full)
        self.assertTrue(self.w.con_fs_handle.isHidden())

    def test_bar_choice_is_remembered_for_the_next_fullscreen(self):
        self.connect()
        self.w.console_fullscreen(True)
        self.w.con_fs_handle.click()                        # Leiste bleibt offen
        self.w.console_fullscreen(False)
        self.w.console_fullscreen(True)
        self.assertFalse(self.w.con_full.isHidden())

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


class TestFullscreenFillsScreen(unittest.TestCase):
    def test_no_margins_in_fullscreen_and_restored_after(self):
        w = Window()
        w.showFullScreen = w.showNormal = w.showMaximized = lambda: None
        w.console_source = lambda: (QWidget(), True, "x")
        w.console_busy(True)
        before = [(l.contentsMargins().left(), l.spacing()) for l in (w.main_lay, w.main_body, w.con_frame.layout())]
        w.console_fullscreen(True)
        for lay in (w.con_lay, w.main_lay, w.main_body, w.con_frame.layout()):
            self.assertEqual((lay.contentsMargins().left(), lay.contentsMargins().top(), lay.spacing()), (0, 0, 0))
        self.assertIn("border-radius: 0", w.con_frame.styleSheet())
        w.console_fullscreen(False)
        after = [(l.contentsMargins().left(), l.spacing()) for l in (w.main_lay, w.main_body, w.con_frame.layout())]
        self.assertEqual(before, after)
        self.assertNotIn("border-radius: 0", w.con_frame.styleSheet())


class TestBiosCredentialsFollowAddress(unittest.TestCase):
    """UEFI-Einstellungen: Benutzer und Passwort tragen sich wie in der UEFI-Konsole aus der Serverliste ein."""

    def setUp(self):
        self.w = Window()
        self.w.table.setRowCount(0)
        self.w.add_row(["10.0.0.5", "werkspw-a", "", "", "", ""])
        self.w.add_row(["10.0.0.6", "werkspw-b", "", "", "", ""])
        self.w.bios_refresh_ips()

    def test_password_follows_selected_address(self):
        self.w.bios_ref.setEditText("10.0.0.6")
        self.assertEqual(self.w.bios_pass.text(), "werkspw-b")
        self.w.bios_ref.setEditText("10.0.0.5")
        self.assertEqual(self.w.bios_pass.text(), "werkspw-a")

    def test_first_row_is_preselected_with_its_password(self):
        self.assertEqual(self.w.bios_ref.currentText(), "10.0.0.5")
        self.assertEqual(self.w.bios_pass.text(), "werkspw-a")

    def test_typed_address_leaves_fields_alone(self):
        self.w.bios_user.setText("mein-admin")
        self.w.bios_pass.setText("mein-pw")
        self.w.bios_ref.setEditText("192.0.2.77")
        self.assertEqual((self.w.bios_user.text(), self.w.bios_pass.text()), ("mein-admin", "mein-pw"))

    def test_no_change_while_reading(self):
        self.w.bios_read_btn.setEnabled(False)
        self.w.bios_ref.setEditText("10.0.0.6")
        self.assertEqual(self.w.bios_pass.text(), "werkspw-a")
