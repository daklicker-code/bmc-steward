"""UEFI-Konsole: Textfenster (Farben, Verlauf, Markieren, Tastenübergabe) und die Seite drumherum
(Statusanzeige, Werkzeugleiste, Tastenzustellung, Einfügen, Bildschirmfoto, Vorlieben, Neuverbinden, Netzsuche).
Alles offscreen; Verbindungen werden nur vorgetäuscht (kein Netz)."""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
from bmc_steward import console as con
from bmc_steward.console import TermScroll, TermView
from bmc_steward.main import Window
from bmc_steward.termkeys import ALT, CTRL, SHIFT

K = Qt.Key
KM = Qt.KeyboardModifier


def press(view, key, mods=KM.NoModifier, text=""):
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, text))


NO_PYTE = unittest.skipIf(con.pyte is None, "Paket pyte nicht installiert")


@NO_PYTE
class TestTermView(unittest.TestCase):
    def setUp(self):
        self.v = TermView()
        self.sent, self.copies, self.pastes = [], [], []
        self.v.send.connect(self.sent.append)
        self.v.copy_requested.connect(lambda: self.copies.append(1))
        self.v.paste_requested.connect(lambda: self.pastes.append(1))

    def fill(self, n):
        self.v.feed("".join(f"zeile {i}\r\n" for i in range(n)).encode())

    def test_themes_exist_and_switch(self):
        self.assertGreaterEqual(len(con.THEMES), 4)
        for key in con.THEMES:
            self.v.set_theme(key)
            self.assertEqual(self.v.theme, key)
            self.v.grab()                                    # Zeichnen darf nie fehlschlagen
        self.v.set_theme("gibt-es-nicht")
        self.assertEqual(self.v.theme, key)                 # unbekannt: unverändert
        self.assertEqual({k for k, _ in con.theme_names()}, set(con.THEMES))

    def test_feed_counts_bytes_and_renders(self):
        self.v.feed(b"hallo")
        self.assertEqual(self.v.rx_bytes, 5)
        self.assertIn("hallo", self.v.lines()[0])
        self.v.reset()
        self.assertEqual(self.v.rx_bytes, 0)

    def test_history_scroll_and_return_on_new_data(self):
        self.fill(80)
        self.assertFalse(self.v.scrolled_back())
        self.v.scroll_history(True)
        self.assertTrue(self.v.scrolled_back())
        old = self.v.lines()[0]
        self.v.scroll_history(False)
        self.assertNotEqual(self.v.lines()[0], old)
        self.v.scroll_history(True)
        self.v.feed(b"neu")                                  # neue Daten holen den Verlauf nach unten
        self.assertFalse(self.v.scrolled_back())

    def test_scroll_bottom(self):
        self.fill(80)
        for _ in range(5):
            self.v.scroll_history(True)
        self.v.scroll_bottom()
        self.assertFalse(self.v.scrolled_back())

    def test_selection_text(self):
        self.v.feed(b"alpha beta\r\ngamma delta\r\n")
        self.v.sel = ((6, 0), (4, 1))                       # rückwärts gezogen: gleiches Ergebnis wie vorwärts
        self.assertEqual(self.v.selected_text(), "beta\ngamma")
        self.v.sel = ((0, 0), (4, 0))
        self.assertEqual(self.v.selected_text(), "alpha")
        self.v.clear_selection()
        self.assertEqual(self.v.selected_text(), "")

    def test_select_all(self):
        self.v.feed(b"eins\r\nzwei")
        self.v.select_all()
        text = self.v.selected_text()
        self.assertTrue(text.startswith("eins\nzwei"))
        self.assertEqual(text.count("\n"), self.v.rows - 1)

    def test_keys_go_to_server_only_when_live(self):
        press(self.v, K.Key_A, text="a")
        self.assertEqual(self.sent, [])
        self.v.set_live(True)
        press(self.v, K.Key_A, text="a")
        press(self.v, K.Key_F2)
        press(self.v, K.Key_C, KM.ControlModifier, "\x03")
        self.assertEqual(self.sent, [b"a", b"\x1bOQ", b"\x03"])
        self.v.set_live(False)

    def test_ctrl_c_is_not_copy_but_ctrl_shift_c_is(self):
        self.v.set_live(True)
        press(self.v, K.Key_C, KM.ControlModifier, "\x03")
        self.assertEqual((self.sent, self.copies), ([b"\x03"], []))
        press(self.v, K.Key_C, KM.ControlModifier | KM.ShiftModifier, "C")
        self.assertEqual(self.copies, [1])
        self.assertEqual(self.sent, [b"\x03"])               # nichts zusätzlich zum Server
        self.v.set_live(False)

    def test_paste_shortcuts_even_when_not_live(self):
        press(self.v, K.Key_V, KM.ControlModifier | KM.ShiftModifier, "V")
        press(self.v, K.Key_Insert, KM.ShiftModifier)
        self.assertEqual(self.pastes, [1, 1])
        self.assertEqual(self.sent, [])

    def test_shift_pageup_scrolls_history_instead_of_sending(self):
        self.v.set_live(True)
        self.fill(80)
        press(self.v, K.Key_PageUp, KM.ShiftModifier)
        self.assertTrue(self.v.scrolled_back())
        self.assertEqual(self.sent, [])
        press(self.v, K.Key_A, text="a")                    # Tippen holt den Verlauf nicht von selbst zurück…
        self.assertEqual(self.sent, [b"a"])                  # …das macht die Seite (scroll_bottom); hier nur: wird gesendet
        self.v.set_live(False)

    def test_typing_clears_selection(self):
        self.v.set_live(True)
        self.v.sel = ((0, 0), (3, 0))
        press(self.v, K.Key_A, text="a")
        self.assertIsNone(self.v.sel)
        self.v.set_live(False)

    def test_blink_timer_only_while_live(self):
        self.assertFalse(self.v.timer.isActive())
        self.v.set_live(True)
        self.assertTrue(self.v.timer.isActive())
        self.v.set_live(False)
        self.assertFalse(self.v.timer.isActive())

    def test_badge_hint_does_not_break_painting(self):
        self.v.set_live(True)
        self.v.last_rx = time.monotonic() - 60               # lange nichts empfangen: Hinweisleiste
        self.v.grab()
        self.fill(80)
        self.v.scroll_history(True)
        self.v.grab()                                       # „Verlauf“-Leiste
        self.v.set_live(False)

    def test_resize_keeps_working_history_screen(self):
        self.v.set_size(132, 43)
        self.assertEqual((self.v.cols, self.v.rows), (132, 43))
        self.fill(120)
        self.v.scroll_history(True)
        self.assertTrue(self.v.scrolled_back())


class TestTermScrollZoom(unittest.TestCase):
    def test_zoom_clamped_and_applied(self):
        s = TermScroll(TermView())
        self.assertEqual(s.zoom(2), 13)
        self.assertEqual(s.term.font().pointSize(), 13)
        for _ in range(40):
            s.zoom(1)
        self.assertEqual(s.pt, TermScroll.MAX_PT)
        for _ in range(60):
            s.zoom(-1)
        self.assertEqual(s.pt, TermScroll.MIN_PT)

    def test_fullscreen_fit_ignores_zoom_then_returns(self):
        s = TermScroll(TermView())
        s.zoom(3)
        s.auto_fit = True
        s.resize(1000, 700)
        s.refit()
        s.auto_fit = False
        s.refit()
        self.assertEqual(s.term.font().pointSize(), 14)


class ConsolePage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bmc-console-state-")
        self.state = os.path.join(self.tmp, "bmc-console.json")
        self.patch = patch.object(i18n, "CONSOLE_STATE", __import__("pathlib").Path(self.state))
        self.patch.start()
        self.w = Window()

    def tearDown(self):
        self.patch.stop()

    # ---------- Statusanzeige ----------
    def test_led_states(self):
        w = self.w
        w.console_led("busy", "Verbinde …")
        self.assertEqual(w.con_frame.property("state"), "busy")
        self.assertEqual(w.con_summary.text(), "Verbinde …")
        w.console_led("error", "kaputt")
        self.assertEqual(w.con_frame.property("state"), "error")
        w.console_led("idle")
        self.assertEqual(w.con_summary.text(), "Nicht verbunden")
        self.assertFalse(w.con_clock.isActive())

    def test_live_starts_clock_and_collapses_settings(self):
        w = self.w
        w.con_panel.setVisible(True)
        w.console_live_text("10.0.0.5", "Text (SSH)", "dell")
        self.assertEqual(w.con_frame.property("state"), "live")
        self.assertIn("10.0.0.5", w.con_head.text())
        self.assertIn("Dell", w.con_head.text())
        self.assertTrue(w.con_clock.isActive())
        self.assertTrue(w.con_panel.isHidden())
        w.console_led("idle")
        self.assertFalse(w.con_clock.isActive())
        self.assertEqual(w.con_head.text(), "Keine Verbindung")

    def test_tick_shows_time_and_bytes_for_serial(self):
        w = self.w
        w.console_live_text("10.0.0.5", "Text", "dell")
        w.con_since = time.monotonic() - 3725
        w.con_serial.proc = object()                         # „läuft“
        w.term.rx_bytes = 2048
        try:
            w.console_tick()
        finally:
            w.con_serial.proc = None
        self.assertIn("01:02:05", w.con_time.text())
        self.assertIn("2.0 kB", w.con_rx.text())

    def test_connect_fail_goes_red(self):
        w = self.w
        w.console_busy(True)
        w.console_fail("Gerät nicht erreichbar.")
        self.assertEqual(w.con_frame.property("state"), "error")
        self.assertTrue(w.con_connect.isEnabled())

    # ---------- Werkzeugleiste ----------
    def test_tools_follow_mode_and_connection(self):
        w = self.w
        w.con_mode.setCurrentIndex(w.con_mode.findData("serial"))
        self.assertFalse(w.tool_copy.isHidden())
        self.assertTrue(w.tool_reload.isHidden())
        self.assertFalse(w.tool_copy.isEnabled())            # nicht verbunden
        w.con_mode.setCurrentIndex(w.con_mode.findData("web"))
        self.assertTrue(w.tool_copy.isHidden())
        self.assertFalse(w.tool_reload.isHidden())
        w.con_mode.setCurrentIndex(w.con_mode.findData("browser"))
        self.assertTrue(w.tool_kbd.isHidden())
        self.assertTrue(w.tool_shot.isHidden())

    def test_tools_enabled_when_connected(self):
        w = self.w
        w.con_mode.setCurrentIndex(w.con_mode.findData("serial"))
        w.con_serial.proc = object()
        try:
            w.console_tools_update()
            for b in (w.tool_copy, w.tool_paste, w.tool_shot, w.tool_kbd, w.tool_again):
                self.assertTrue(b.isEnabled())
        finally:
            w.con_serial.proc = None
        w.console_tools_update()
        self.assertFalse(w.tool_copy.isEnabled())

    def test_keyboard_toggle_hides_old_key_row_and_closes_on_disconnect(self):
        w = self.w
        w.con_mode.setCurrentIndex(w.con_mode.findData("serial"))
        w.con_serial.proc = object()
        w.console_tools_update()
        w.tool_kbd.setChecked(True)
        self.assertFalse(w.con_osk.isHidden())
        self.assertTrue(w.con_keys.isHidden())               # die Tastatur enthält Esc/F-Tasten schon
        w.con_found_vendor = "hpe"
        w.tool_kbd.setChecked(False)
        w.tool_kbd.setChecked(True)
        self.assertEqual(w.con_osk.vendor, "hpe")
        w.con_serial.proc = None
        w.console_tools_update()                             # Verbindung weg: Tastatur schließt
        self.assertFalse(w.tool_kbd.isChecked())
        self.assertTrue(w.con_osk.isHidden())

    # ---------- Tastenzustellung ----------
    def fake_serial(self):
        sent = []
        self.w.con_serial.proc = object()
        self.w.con_serial._write = sent.append
        return sent

    def test_deliver_to_serial(self):
        sent = self.fake_serial()
        try:
            self.assertTrue(self.w.console_deliver(K.Key_F2.value, 0, ""))
            self.assertTrue(self.w.console_deliver(K.Key_C.value, CTRL, "c"))
            self.assertTrue(self.w.console_deliver(ord("X"), 0, "x"))
        finally:
            self.w.con_serial.proc = None
        self.assertEqual(sent, [b"\x1bOQ", b"\x03", b"x"])

    def test_deliver_f_keys_as_escape_sequences(self):
        sent = self.fake_serial()
        try:
            self.w.con_osk.esc_box.setChecked(True)
            self.w.console_deliver(K.Key_F1.value, 0, "")
            self.w.console_deliver(K.Key_F12.value, 0, "")
            self.w.console_deliver(K.Key_F1.value, CTRL, "")        # mit Umschalttaste: normale Folge
        finally:
            self.w.con_serial.proc = None
        self.assertEqual(sent, [b"\x1b1", b"\x1b@", b"\x1bOP"])

    def test_deliver_uses_selected_encoding(self):
        sent = self.fake_serial()
        try:
            self.w.term.set_encoding(True)
            self.w.console_deliver(ord("Ö"), 0, "ö")
        finally:
            self.w.con_serial.proc = None
            self.w.term.set_encoding(False)
        self.assertEqual(sent, ["ö".encode("cp437")])

    def test_deliver_without_connection_says_so(self):
        self.assertFalse(self.w.console_deliver(K.Key_F2.value, 0, ""))
        self.assertIn("Nicht verbunden", self.w.con_status.text())

    def test_deliver_scrolls_history_back_down_and_clears_selection(self):
        sent = self.fake_serial()
        try:
            self.w.term.feed("".join(f"z{i}\r\n" for i in range(80)).encode())
            self.w.term.scroll_history(True)
            self.w.term.sel = ((0, 0), (2, 0))
            self.w.console_deliver(ord("A"), 0, "a")
        finally:
            self.w.con_serial.proc = None
        self.assertFalse(self.w.term.scrolled_back())
        self.assertIsNone(self.w.term.sel)

    def test_feeder_output_goes_through_deliver(self):
        sent = self.fake_serial()
        try:
            self.w.con_feeder.add_text("ab")
            while self.w.con_feeder.busy():
                self.w.con_feeder._tick()
        finally:
            self.w.con_serial.proc = None
        self.assertEqual(sent, [b"a", b"b"])

    # ---------- Kopieren, Einfügen, Bildschirmfoto ----------
    @NO_PYTE
    def test_copy_selection_or_whole_screen(self):
        w = self.w
        w.term.feed(b"geheim-frei\r\nzweite zeile")
        w.term.sel = ((0, 0), (5, 0))
        w.console_copy_text()
        self.assertEqual(QApplication.clipboard().text(), "geheim")
        w.term.clear_selection()
        w.console_copy_text()
        self.assertEqual(QApplication.clipboard().text().splitlines()[:2], ["geheim-frei", "zweite zeile"])
        self.assertIn("Zeichen", w.con_status.text())

    def test_paste_needs_connection(self):
        QApplication.clipboard().setText("x")
        self.w.console_paste()
        self.assertIn("nur mit verbundener", self.w.con_status.text())

    def test_paste_short_text_types_without_question(self):
        sent = self.fake_serial()
        try:
            QApplication.clipboard().setText("ls")
            with patch.object(QMessageBox, "exec") as ex:
                self.w.console_paste()
            self.assertFalse(ex.called)
            while self.w.con_feeder.busy():
                self.w.con_feeder._tick()
        finally:
            self.w.con_serial.proc = None
        self.assertEqual(sent, [b"l", b"s"])

    def test_paste_multiline_asks_and_respects_no(self):
        sent = self.fake_serial()
        try:
            QApplication.clipboard().setText("rm -rf /\nreboot")
            box = {}

            def fake_exec(self_):
                box["b"] = self_
                for b in self_.buttons():
                    if self_.buttonRole(b) == QMessageBox.ButtonRole.RejectRole:
                        box["clicked"] = b
                return 0
            with patch.object(QMessageBox, "exec", fake_exec), \
                    patch.object(QMessageBox, "clickedButton", lambda s_: box["clicked"]):
                self.w.console_paste()
            self.assertFalse(self.w.con_feeder.busy())       # abgelehnt: nichts wird getippt
        finally:
            self.w.con_serial.proc = None
        self.assertEqual(sent, [])

    def test_paste_multiline_accepted(self):
        sent = self.fake_serial()
        try:
            QApplication.clipboard().setText("a\nb")
            box = {}

            def fake_exec(self_):
                for b in self_.buttons():
                    if self_.buttonRole(b) == QMessageBox.ButtonRole.AcceptRole:
                        box["clicked"] = b
                return 0
            with patch.object(QMessageBox, "exec", fake_exec), \
                    patch.object(QMessageBox, "clickedButton", lambda s_: box["clicked"]):
                self.w.console_paste()
            while self.w.con_feeder.busy():
                self.w.con_feeder._tick()
        finally:
            self.w.con_serial.proc = None
        self.assertEqual(sent, [b"a", b"\r", b"b"])

    def test_disconnect_cancels_typing(self):
        self.fake_serial()
        try:
            self.w.con_feeder.add_text("a" * 50)
            self.w.console_disconnect()
        finally:
            self.w.con_serial.proc = None
        self.assertFalse(self.w.con_feeder.busy())

    def test_snapshot_to_file_and_clipboard(self):
        w = self.w
        w.console_source = lambda: (w.term, True, "10.0.0.5:8443")
        w.term.feed(b"foto")
        target = os.path.join(self.tmp, "k.png")
        with patch("bmc_steward.console.QFileDialog.getSaveFileName", return_value=(target, "")) as dlg:
            w.console_snapshot(False)
        self.assertTrue(os.path.getsize(target) > 100)
        with open(target, "rb") as f:
            self.assertEqual(f.read(4), b"\x89PNG")
        self.assertIn("10.0.0.5_8443", dlg.call_args[0][2])       # Doppelpunkt im Vorschlagsnamen ersetzt
        w.console_snapshot(True)
        self.assertFalse(QApplication.clipboard().pixmap().isNull())

    def test_snapshot_without_console(self):
        self.w.console_source = lambda: None
        self.w.console_snapshot(False)
        self.assertIn("Kein Konsolenbild", self.w.con_status.text())

    # ---------- Zoom, Vorlieben ----------
    def test_zoom_text_console_persists(self):
        w = self.w
        w.con_mode.setCurrentIndex(w.con_mode.findData("serial"))
        w.console_zoom(2)
        self.assertEqual(w.term_scroll.pt, 13)
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["pt"], 13)

    def test_preferences_roundtrip(self):
        w = self.w
        w.con_theme.setCurrentIndex(w.con_theme.findData("green"))
        w.con_osk.layout_box.setCurrentText("AZERTY")
        w.con_mode.setCurrentIndex(w.con_mode.findData("serial"))
        w.console_zoom(1)
        w2 = Window()
        self.assertEqual(w2.con_theme.currentData(), "green")
        self.assertEqual(w2.term.theme, "green")
        self.assertEqual(w2.con_osk.layout_name, "AZERTY")
        self.assertEqual(w2.term_scroll.pt, 12)
        self.assertEqual(oct(os.stat(self.state).st_mode & 0o777), oct(0o600))
        self.assertNotIn("pass", open(self.state, encoding="utf-8").read().lower())

    def test_broken_state_file_is_ignored(self):
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("{kaputt")
        w = Window()
        self.assertEqual(w.term.theme, "classic")
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump({"theme": "x", "pt": 999, "osk_layout": "DVORAK"}, f)
        w = Window()
        self.assertEqual((w.term.theme, w.term_scroll.pt, w.con_osk.layout_name), ("classic", 11, "QWERTZ"))

    # ---------- Neu verbinden ----------
    def test_auto_reconnect_schedules_retry_until_cap(self):
        w = self.w
        w.con_auto.setChecked(True)
        calls = []
        with patch.object(con.QTimer, "singleShot", lambda ms, fn: calls.append((ms, fn))):
            w.con_user_stop = False
            w.console_serial_ended(None)
            self.assertEqual(calls[0][0], 3000)
            self.assertEqual(w.con_retries, 1)
            w.con_retries = con.AUTO_RECONNECT_MAX
            w.console_serial_ended(None)
            self.assertEqual(len(calls), 1)                  # Obergrenze erreicht: kein weiterer Versuch

    def test_no_auto_reconnect_after_user_disconnect_or_unchecked(self):
        w = self.w
        calls = []
        with patch.object(con.QTimer, "singleShot", lambda ms, fn: calls.append(ms)):
            w.con_auto.setChecked(False)
            w.con_user_stop = False
            w.console_serial_ended(None)
            w.con_auto.setChecked(True)
            w.con_user_stop = True
            w.console_serial_ended(None)
        self.assertEqual(calls, [])

    def test_retry_connects_only_when_still_wanted(self):
        w = self.w
        seen = []
        w.console_connect = lambda *a, **k: seen.append(k)
        w.con_auto.setChecked(True)
        w.con_user_stop = False
        w.console_auto_retry()
        self.assertEqual(seen, [{"auto": True}])
        w.con_user_stop = True
        w.console_auto_retry()
        self.assertEqual(len(seen), 1)

    def test_manual_connect_resets_retry_counter(self):
        w = self.w
        w.con_retries = 7
        w.con_ip.setEditText("10.0.0.5")
        with patch.object(con.ConsoleProbeThread, "start", lambda self_: None):
            w.console_connect()
        self.assertEqual(w.con_retries, 0)
        self.assertFalse(w.con_user_stop)
        self.assertEqual(w.con_frame.property("state"), "busy")
        w.console_busy(False)

    def test_reconnect_disconnects_then_connects(self):
        w = self.w
        order = []
        w.console_disconnect = lambda: order.append("trennen")
        w.console_connect = lambda *a, **k: order.append("verbinden")
        w.console_reconnect()
        self.assertEqual(order, ["trennen", "verbinden"])

    # ---------- Vollbild mit Tastatur ----------
    def test_fullscreen_hides_strip_and_header_and_restores(self):
        w = self.w
        w.showFullScreen = lambda: None
        w.showNormal = lambda: None
        w.showMaximized = lambda: None
        w.console_source = lambda: (QWidget(), True, "x")
        for part in (w.con_strip, w.con_head, w.con_tools):
            part.setVisible(True)
        w.console_busy(True)
        w.console_fullscreen(True)
        self.assertTrue(w.con_strip.isHidden())
        self.assertTrue(w.con_head.isHidden())
        self.assertTrue(w.con_tools.isHidden())              # Werkzeugleiste gehört zur einklappbaren Leiste (standardmäßig zu)
        w.con_fs_handle.click()
        self.assertFalse(w.con_tools.isHidden())
        w.console_fullscreen(False)
        self.assertFalse(w.con_tools.isHidden())
        self.assertFalse(w.con_strip.isHidden())
        self.assertFalse(w.con_head.isHidden())

    # ---------- Netzsuche ----------
    def test_netscan_buttons_present(self):
        texts = {b.text() for b in self.w.findChildren(con.QPushButton)}
        self.assertIn("🔍 Suchen …", texts)
        self.assertIn("Server suchen …", texts)

    def test_take_found_fills_console_fields(self):
        w = self.w
        w.console_take_found("10.1.2.3", "lenovo")
        self.assertEqual(w.con_ip.currentText(), "10.1.2.3")
        self.assertEqual(w.con_vendor.currentData(), "lenovo")
        w.console_take_found("10.1.2.4:8443", "ipmi")         # kein Herstellerschlüssel der Liste: Automatik
        self.assertEqual(w.con_vendor.currentData(), "")
        self.assertEqual(w.tabs.currentIndex(), w.tabs.indexOf(w.con_page))

    def test_found_servers_go_into_list_without_duplicates(self):
        w = self.w
        before = w.table.rowCount()
        rows = [{"ip": "10.5.0.1", "vendor": "dell", "serial": "ABC123"},
                {"ip": "10.5.0.2", "vendor": "smc", "serial": ""},
                {"ip": "10.5.0.1", "vendor": "dell", "serial": "ABC123"}]
        with patch.object(Window, "probe_vendor"):        # kein Netz im Test
            w.netscan_to_list(rows)
            w.netscan_to_list(rows[:1])                     # nochmal: nichts Neues
        self.assertEqual(w.table.rowCount(), before + 2)
        ips = [w.cell(r, 0) for r in range(w.table.rowCount())]
        self.assertEqual(ips.count("10.5.0.1"), 1)
        r = ips.index("10.5.0.1")
        self.assertEqual(w.cell(r, 6), "ABC123")
        self.assertIn("Dell", w.table.item(r, w.vendor_col).text())
        self.assertTrue(w.table.item(r, w.vendor_col).text())     # Hersteller steht sofort in der Spalte

    def test_open_netscan_reuses_dialog_per_mode(self):
        w = self.w
        w.open_netscan(True)
        first = w.netscan_dlg
        w.open_netscan(True)
        self.assertIs(w.netscan_dlg, first)
        w.open_netscan(False)
        self.assertIsNot(w.netscan_dlg, first)
        self.assertTrue(w.netscan_dlg.b_console.isHidden())
        w.netscan_dlg.close()


if __name__ == "__main__":
    unittest.main()
