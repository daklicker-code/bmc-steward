"""Bildschirmtastatur (bmc_steward.osk): Umschalttasten, Zeichen, Hersteller-Tasten, Makros, Layouts, Takt-Ausgabe
und die Zustellung als echte Tastenereignisse an eine Web-Seite (eigener Prozess, braucht QtWebEngine)."""
import json
import os
import subprocess
import sys
import unittest
from tests._support import REPO, qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QWidget
from bmc_steward import osk
from bmc_steward.osk import K, KeyFeeder, OnScreenKeyboard
from bmc_steward.termkeys import ALT, CTRL, SHIFT


def collect(widget):
    got = []
    widget.key.connect(lambda k, m, t: got.append((k, m, t)))
    return got


class TestKeyCode(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(osk.key_code("a"), ord("A"))
        self.assertEqual(osk.key_code("A"), ord("A"))
        self.assertEqual(osk.key_code("1"), ord("1"))
        self.assertEqual(osk.key_code("="), ord("="))
        self.assertEqual(osk.key_code("ß"), K.Key_ssharp.value)
        self.assertEqual(osk.key_code("ü"), ord("Ü"))
        self.assertEqual(osk.key_code(""), 0)
        self.assertEqual(osk.key_code("ab"), 0)


class TestLayouts(unittest.TestCase):
    def test_every_layout_has_four_rows_of_pairs(self):
        for name, rows in osk.LAYOUTS.items():
            self.assertEqual(len(rows), 4, name)
            for row in rows:
                for pair in row:
                    self.assertEqual(len(pair), 2, (name, pair))

    def test_language_defaults(self):
        self.assertEqual(osk.LAYOUT_FOR_LANG["de"], "QWERTZ")
        self.assertEqual(osk.LAYOUT_FOR_LANG["fr"], "AZERTY")
        self.assertEqual(osk.LAYOUT_FOR_LANG["en"], "QWERTY")
        self.assertEqual(OnScreenKeyboard("fr").layout_name, "AZERTY")
        self.assertEqual(OnScreenKeyboard("xx").layout_name, "QWERTY")


class TestKeyboard(unittest.TestCase):
    def setUp(self):
        self.kb = OnScreenKeyboard("de")
        self.got = collect(self.kb)

    def click(self, text):
        for b in self.kb.findChildren(osk.QPushButton):
            if b.text() == text:
                b.click()
                return b
        self.fail(f"Knopf {text!r} fehlt")

    def test_plain_char(self):
        self.click("q")
        self.assertEqual(self.got, [(ord("Q"), 0, "q")])

    def test_shift_is_one_shot(self):
        self.kb.cycle("shift")
        self.assertEqual(self.kb.mods(), SHIFT)
        self.click("Q")                                    # Beschriftung folgt Umschalt
        self.assertEqual(self.got, [(ord("Q"), SHIFT, "Q")])
        self.assertEqual(self.kb.mods(), 0)                 # danach wieder los
        self.click("q")
        self.assertEqual(self.got[-1], (ord("Q"), 0, "q"))

    def test_shift_gives_upper_symbol(self):
        self.kb.cycle("shift")
        self.click("\"")                                    # obere Belegung der 2
        self.assertEqual(self.got[-1][2], "\"")

    def test_ctrl_c(self):
        self.kb.cycle("ctrl")
        self.click("c")
        self.assertEqual(self.got, [(ord("C"), CTRL, "c")])
        self.assertEqual(self.kb.mods(), 0)

    def test_lock_after_second_click_and_off_after_third(self):
        self.kb.cycle("ctrl")
        self.kb.cycle("ctrl")
        self.assertEqual(self.kb.state["ctrl"], 2)
        self.click("a")
        self.click("b")
        self.assertEqual([m for _, m, _ in self.got], [CTRL, CTRL])        # bleibt gedrückt
        self.kb.cycle("ctrl")
        self.assertEqual(self.kb.mods(), 0)

    def test_combined_modifiers_and_function_key(self):
        self.kb.cycle("ctrl")
        self.kb.cycle("alt")
        self.click("F2")
        self.assertEqual(self.got, [(K.Key_F2.value, CTRL | ALT, "")])

    def test_space_carries_its_character(self):
        self.click("Leertaste")
        self.assertEqual(self.got, [(K.Key_Space.value, 0, " ")])

    def test_arrow_and_enter(self):
        self.click("←")
        self.click("⏎")
        self.assertEqual([k for k, _, _ in self.got], [K.Key_Left.value, K.Key_Return.value])

    def test_layout_switch_rebuilds_and_keeps_state(self):
        self.kb.cycle("shift")
        self.kb.layout_box.setCurrentText("QWERTY")
        self.assertEqual(self.kb.layout_name, "QWERTY")
        self.assertEqual(self.kb.state["shift"], 1)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.click("Y")                                    # QWERTY hat Y in der oberen Buchstabenreihe, mit Umschalt groß
        self.assertEqual(self.got[-1][2], "Y")

    def test_compact_by_default_and_full_toggle(self):
        self.assertTrue(self.kb.full.isHidden())
        self.kb.toggle_full()
        self.assertFalse(self.kb.full.isHidden())
        self.kb.set_layout("AZERTY")                       # Zustand bleibt beim Neuaufbau
        self.assertFalse(self.kb.full.isHidden())

    def test_ctrl_alt_del_depends_on_mode(self):
        self.kb.set_mode("web")
        self.click("Strg+Alt+Entf")
        self.assertEqual(self.got, [(K.Key_Delete.value, CTRL | ALT, "")])
        seqs = []
        self.kb.keys.connect(seqs.append)
        self.kb.set_mode("serial")
        self.click("Strg+Alt+Entf")
        self.assertEqual(len(seqs), 1)
        texts = [t for _, _, t in seqs[0]]
        self.assertEqual(texts, ["", "R", "", "r", "", "R"])            # Esc R Esc r Esc R

    def test_vendor_keys(self):
        want = {"dell": ("F2", "F11", "F12"), "hpe": ("F9", "F11", "F12"), "lenovo": ("F1", "F11", "F12"),
                "smc": ("Entf", "F11", "F12")}
        for vendor, (setup, boot, pxe) in want.items():
            kb = OnScreenKeyboard("de")
            kb.set_vendor(vendor)
            labels = [b.text() for b in kb.findChildren(osk.QPushButton) if b.property("macro")]
            self.assertIn(f"Setup  ({setup})", labels, vendor)
            self.assertIn(f"Boot-Menü  ({boot})", labels, vendor)
            self.assertIn(f"PXE-Boot  ({pxe})", labels, vendor)
        self.assertEqual(osk.VENDOR_KEYS["hpe_old"], osk.VENDOR_KEYS["hpe"])

    def test_vendor_macro_ignores_held_modifiers(self):
        kb = OnScreenKeyboard("de")
        got = collect(kb)
        kb.set_vendor("dell")
        kb.cycle("ctrl")
        for b in kb.findChildren(osk.QPushButton):
            if b.text() == "Setup  (F2)":
                b.click()
        self.assertEqual(got, [(K.Key_F2.value, 0, "")])
        self.assertEqual(kb.state["ctrl"], 1)               # Strg bleibt für die nächste echte Taste

    def test_unknown_vendor_still_has_ctrl_alt_del(self):
        kb = OnScreenKeyboard("de")
        kb.set_vendor("")
        labels = [b.text() for b in kb.findChildren(osk.QPushButton)]
        self.assertIn("Strg+Alt+Entf", labels)
        self.assertFalse([t for t in labels if t.startswith("Setup")])

    def test_type_text_signal(self):
        sent = []
        self.kb.type_text.connect(lambda t, e: sent.append((t, e)))
        self.kb.text_edit.setText("ip=dhcp")
        self.kb.enter_box.setChecked(True)
        self.kb.send_text()
        self.assertEqual(sent, [("ip=dhcp", True)])
        self.assertEqual(self.kb.text_edit.text(), "")      # wird nicht aufgehoben
        self.kb.send_text()                                  # leer: nichts
        self.assertEqual(len(sent), 1)

    def test_esc_option_signal(self):
        flags = []
        self.kb.fkeys_as_esc.connect(flags.append)
        self.kb.esc_box.setChecked(True)
        self.assertEqual(flags, [True])
        self.kb.set_mode("web")
        self.assertTrue(self.kb.esc_box.isHidden())


class TestFeeder(unittest.TestCase):
    def pump(self, f, limit=200):
        for _ in range(limit):
            if not f.busy():
                return
            f._tick()

    def test_text_becomes_keys_in_order(self):
        f = KeyFeeder(interval_ms=1)
        got = []
        f.key.connect(lambda k, m, t: got.append((k, m, t)))
        n = f.add_text("Ab\n\t")
        self.assertEqual(n, 4)
        self.pump(f)
        self.assertEqual(got, [(ord("A"), 0, "A"), (ord("B"), 0, "b"), (K.Key_Return.value, 0, ""),
                               (K.Key_Tab.value, 0, "")])

    def test_enter_and_crlf(self):
        f = KeyFeeder(interval_ms=1)
        got = []
        f.key.connect(lambda k, m, t: got.append(k))
        f.add_text("x\r\ny", enter=True)
        self.pump(f)
        self.assertEqual(got, [ord("X"), K.Key_Return.value, ord("Y"), K.Key_Return.value])

    def test_control_characters_are_dropped(self):
        f = KeyFeeder(interval_ms=1)
        self.assertEqual(f.add_text("a\x07\x1bb"), 2)

    def test_cancel_stops_everything(self):
        f = KeyFeeder(interval_ms=1)
        got = []
        f.key.connect(lambda k, m, t: got.append(k))
        f.add_text("abcdef")
        f._tick()
        f.cancel()
        self.assertFalse(f.busy())
        self.assertFalse(f.timer.isActive())
        self.pump(f)
        self.assertEqual(len(got), 1)

    def test_finished_signal(self):
        f = KeyFeeder(interval_ms=1)
        done = []
        f.finished.connect(lambda: done.append(1))
        f.add_text("ab")
        self.pump(f)
        f._tick()
        self.assertTrue(done)

    def test_add_keys(self):
        f = KeyFeeder(interval_ms=1)
        got = []
        f.key.connect(lambda k, m, t: got.append((k, m, t)))
        f.add_keys([(K.Key_Escape.value, 0, ""), (ord("R"), 0, "R")])
        self.pump(f)
        self.assertEqual(len(got), 2)


class TestSendQtKey(unittest.TestCase):
    """Die Ereignisse, die ein Widget wirklich sieht – ohne Browser, mit einem Widget, das sie aufzeichnet."""

    class Rec(QWidget):
        def __init__(self):
            super().__init__()
            self.events = []

        def event(self, e):
            if e.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                self.events.append(("p" if e.type() == QEvent.Type.KeyPress else "r", e.key(),
                                    int(e.modifiers().value), e.text()))
                return True
            return super().event(e)

    def test_plain_key_press_and_release(self):
        w = self.Rec()
        osk.send_qt_key(w, K.Key_F2.value, 0, "")
        self.assertEqual(w.events, [("p", K.Key_F2.value, 0, ""), ("r", K.Key_F2.value, 0, "")])

    def test_modifiers_are_pressed_before_and_released_after(self):
        w = self.Rec()
        osk.send_qt_key(w, K.Key_Delete.value, CTRL | ALT, "")
        kinds = [(t, k) for t, k, _m, _x in w.events]
        self.assertEqual(kinds, [("p", K.Key_Control.value), ("p", K.Key_Alt.value), ("p", K.Key_Delete.value),
                                 ("r", K.Key_Delete.value), ("r", K.Key_Alt.value), ("r", K.Key_Control.value)])

    def test_implicit_text_for_enter_and_space(self):
        w = self.Rec()
        osk.send_qt_key(w, K.Key_Return.value, 0, "")
        osk.send_qt_key(w, K.Key_Space.value, 0, " ")
        self.assertEqual(w.events[0][3], "\r")
        self.assertEqual(w.events[2][3], " ")

    def test_ctrl_combination_has_no_implicit_text(self):
        w = self.Rec()
        osk.send_qt_key(w, K.Key_Space.value, CTRL, "")
        self.assertEqual([x for t, _k, _m, x in w.events if t == "p" and _k == K.Key_Space.value], [""])


class TestWebEngineDelivery(unittest.TestCase):
    """Echtes Chromium: kommen die Tasten als keydown mit richtigem key/code/Umschalt an?"""

    def test_keys_arrive_in_a_real_page(self):
        probe = os.path.join(REPO, "tests", "_webkeys_probe.py")
        try:
            r = subprocess.run([sys.executable, probe], capture_output=True, text=True, timeout=120,
                               env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        except subprocess.TimeoutExpired:
            self.skipTest("QtWebEngine antwortet nicht (Zeitüberschreitung)")
        if r.returncode != 0 or not r.stdout.strip():
            self.skipTest("QtWebEngine nicht nutzbar: " + (r.stderr.strip().splitlines() or ["?"])[-1][:120])
        rows = json.loads(r.stdout.strip().splitlines()[-1])
        keys = [(x[0], x[1], x[3], x[4], x[5]) for x in rows]      # key, code, ctrl, alt, shift
        self.assertIn(("F2", "F2", False, False, False), keys)
        self.assertIn(("Delete", "Delete", True, True, False), keys)
        self.assertIn(("A", "KeyA", False, False, True), keys)
        self.assertIn(("Enter", "Enter", False, False, False), keys)
        self.assertEqual([k[0] for k in keys].count("Control"), 1)
        self.assertEqual([k[0] for k in keys].count("Alt"), 1)


if __name__ == "__main__":
    unittest.main()
