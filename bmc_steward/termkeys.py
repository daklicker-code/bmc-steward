"""BMC-Steward: Tasten -> Bytes für die Textkonsole (VT100/ANSI), gemeinsam für die echte Tastatur und die
Bildschirmtastatur. Kein Zustand, keine Oberfläche; deshalb einfach zu testen."""
from PyQt6.QtCore import Qt

KEYMAP_ENUM = {
    Qt.Key.Key_Up: b"\x1b[A", Qt.Key.Key_Down: b"\x1b[B", Qt.Key.Key_Right: b"\x1b[C", Qt.Key.Key_Left: b"\x1b[D",
    Qt.Key.Key_Home: b"\x1b[H", Qt.Key.Key_End: b"\x1b[F", Qt.Key.Key_PageUp: b"\x1b[5~",
    Qt.Key.Key_PageDown: b"\x1b[6~", Qt.Key.Key_Insert: b"\x1b[2~", Qt.Key.Key_Delete: b"\x1b[3~",
    Qt.Key.Key_Return: b"\r", Qt.Key.Key_Enter: b"\r", Qt.Key.Key_Backspace: b"\x08", Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Backtab: b"\x1b[Z", Qt.Key.Key_Escape: b"\x1b",
    Qt.Key.Key_F1: b"\x1bOP", Qt.Key.Key_F2: b"\x1bOQ", Qt.Key.Key_F3: b"\x1bOR", Qt.Key.Key_F4: b"\x1bOS",
    Qt.Key.Key_F5: b"\x1b[15~", Qt.Key.Key_F6: b"\x1b[17~", Qt.Key.Key_F7: b"\x1b[18~", Qt.Key.Key_F8: b"\x1b[19~",
    Qt.Key.Key_F9: b"\x1b[20~", Qt.Key.Key_F10: b"\x1b[21~", Qt.Key.Key_F11: b"\x1b[23~",
    Qt.Key.Key_F12: b"\x1b[24~",
}

KEYMAP = {k.value: v for k, v in KEYMAP_ENUM.items()}

# Für Konsolen, die nur Escape-Folgen statt F-Tasten verstehen (Esc+1 = F1 … Esc+0 = F10, Esc+! = F11, Esc+@ = F12)
FKEY_ESC = {Qt.Key.Key_F1.value + i: b"\x1b" + c.encode() for i, c in enumerate("1234567890!@")}

SHIFT = Qt.KeyboardModifier.ShiftModifier.value
CTRL = Qt.KeyboardModifier.ControlModifier.value
ALT = Qt.KeyboardModifier.AltModifier.value

# Strg+Zeichen, die keine Buchstaben sind: [ \ ] ^ _ und Leertaste (NUL)
_CTRL_PUNCT = {Qt.Key.Key_BracketLeft.value: 0x1b, Qt.Key.Key_Backslash.value: 0x1c,
               Qt.Key.Key_BracketRight.value: 0x1d, Qt.Key.Key_AsciiCircum.value: 0x1e,
               Qt.Key.Key_Underscore.value: 0x1f, Qt.Key.Key_Space.value: 0x00, Qt.Key.Key_At.value: 0x00}


def encode_key(key, mods=0, text="", cp437=False):
    """Bytes für den Server oder None (Taste hat keine Entsprechung, z. B. reine Umschalttasten).

    key = Qt.Key-Wert, mods = Summe aus SHIFT/CTRL/ALT, text = das von Qt gemeldete Zeichen.
    Sondertasten (Pfeile, F-Tasten …) gehen unabhängig von Umschalttasten als feste Folge, wie bisher;
    Alt davor setzt ein Escape voran (üblich für „Meta“). Strg+Buchstabe ergibt das Steuerzeichen."""
    mods = int(mods)
    key = int(key)
    if key in KEYMAP:
        seq = KEYMAP[key]
        if key == Qt.Key.Key_Tab.value and mods & SHIFT:
            seq = KEYMAP[Qt.Key.Key_Backtab.value]
        return (b"\x1b" + seq) if (mods & ALT and key != Qt.Key.Key_Escape.value) else seq
    if mods & CTRL:
        if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value:
            data = bytes([key - Qt.Key.Key_A.value + 1])
        elif key in _CTRL_PUNCT:
            data = bytes([_CTRL_PUNCT[key]])
        else:
            return None
    elif text and text.isprintable():
        data = text.encode("cp437", "replace") if cp437 else text.encode("utf-8")
    else:
        return None
    return (b"\x1b" + data) if mods & ALT else data
