"""Tasten -> Bytes für die Textkonsole (bmc_steward.termkeys). Das ist, was der Server tatsächlich empfängt:
Fehler hier bedeuten falsche Tasten im UEFI. Die Sondertasten bleiben unabhängig von Umschalttasten bei den bisherigen
festen Folgen (UEFI-Konsolen verstehen keine xterm-Modifikatoren)."""
import unittest
from tests._support import qapp

qapp()
from PyQt6.QtCore import Qt
from bmc_steward.termkeys import ALT, CTRL, FKEY_ESC, KEYMAP, SHIFT, encode_key

K = Qt.Key


class TestEncode(unittest.TestCase):
    def test_special_keys(self):
        self.assertEqual(encode_key(K.Key_Up.value), b"\x1b[A")
        self.assertEqual(encode_key(K.Key_F1.value), b"\x1bOP")
        self.assertEqual(encode_key(K.Key_F9.value), b"\x1b[20~")
        self.assertEqual(encode_key(K.Key_Delete.value), b"\x1b[3~")
        self.assertEqual(encode_key(K.Key_Return.value), b"\r")
        self.assertEqual(encode_key(K.Key_Escape.value), b"\x1b")
        self.assertEqual(encode_key(K.Key_Backspace.value), b"\x08")

    def test_all_function_keys_present(self):
        for i in range(1, 13):
            self.assertIn(getattr(K, f"Key_F{i}").value, KEYMAP)

    def test_modifiers_do_not_change_special_keys(self):
        self.assertEqual(encode_key(K.Key_Up.value, CTRL | SHIFT), b"\x1b[A")
        self.assertEqual(encode_key(K.Key_F2.value, SHIFT), b"\x1bOQ")

    def test_shift_tab_is_backtab(self):
        self.assertEqual(encode_key(K.Key_Tab.value, SHIFT), b"\x1b[Z")
        self.assertEqual(encode_key(K.Key_Tab.value), b"\t")

    def test_alt_prefixes_escape(self):
        self.assertEqual(encode_key(K.Key_X.value, ALT, "x"), b"\x1bx")
        self.assertEqual(encode_key(K.Key_Left.value, ALT), b"\x1b\x1b[D")
        self.assertEqual(encode_key(K.Key_Escape.value, ALT), b"\x1b")      # kein doppeltes Escape

    def test_ctrl_letters(self):
        self.assertEqual(encode_key(K.Key_A.value, CTRL, "\x01"), b"\x01")
        self.assertEqual(encode_key(K.Key_C.value, CTRL, "c"), b"\x03")
        self.assertEqual(encode_key(K.Key_Z.value, CTRL), b"\x1a")
        self.assertEqual(encode_key(K.Key_BracketRight.value, CTRL), b"\x1d")      # Telnet-Escape Strg+]
        self.assertEqual(encode_key(K.Key_Space.value, CTRL, " "), b"\x00")
        self.assertIsNone(encode_key(K.Key_1.value, CTRL, "1"))

    def test_printable_text_utf8_and_cp437(self):
        self.assertEqual(encode_key(K.Key_A.value, 0, "a"), b"a")
        self.assertEqual(encode_key(ord("Ö"), 0, "ö"), "ö".encode("utf-8"))
        self.assertEqual(encode_key(ord("Ö"), 0, "ö", cp437=True), "ö".encode("cp437"))
        self.assertEqual(encode_key(K.Key_Space.value, 0, " "), b" ")

    def test_nothing_for_bare_modifiers_or_empty(self):
        self.assertIsNone(encode_key(K.Key_Shift.value, SHIFT, ""))
        self.assertIsNone(encode_key(K.Key_Control.value, CTRL, ""))
        self.assertIsNone(encode_key(K.Key_A.value, 0, ""))
        self.assertIsNone(encode_key(K.Key_A.value, 0, "\x07"))        # nicht druckbar

    def test_fkey_escape_sequences(self):
        self.assertEqual(FKEY_ESC[K.Key_F1.value], b"\x1b1")
        self.assertEqual(FKEY_ESC[K.Key_F10.value], b"\x1b0")
        self.assertEqual(FKEY_ESC[K.Key_F11.value], b"\x1b!")
        self.assertEqual(FKEY_ESC[K.Key_F12.value], b"\x1b@")
        self.assertEqual(len(FKEY_ESC), 12)


if __name__ == "__main__":
    unittest.main()
