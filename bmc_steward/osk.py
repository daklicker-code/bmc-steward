"""BMC-Steward: Bildschirmtastatur für die UEFI-Konsole.

Wozu: Manche Tasten kommen nie beim Server an, weil sie vorher abgefangen werden (F-Tasten, Esc, Alt-Kombinationen
vom Desktop; in einer VM oder per Fernzugriff auf diesen Rechner noch mehr). Außerdem kennt nicht jeder die
Escape-Folgen der seriellen Konsole. Die Tastatur erzeugt nur Tastenereignisse (Qt-Taste, Umschalttasten,
Zeichen); wohin sie gehen und wie sie übersetzt werden, entscheidet die Konsolenseite (Text: termkeys.encode_key,
Bild: echte Tastenereignisse an die eingebettete Web-Konsole).
"""
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)
from .i18n import T
from .termkeys import ALT, CTRL, SHIFT

K = Qt.Key

# (normal, mit Umschalt) je Taste; Reihen von der Zahlenreihe bis zur untersten Buchstabenreihe.
LAYOUTS = {
    "QWERTZ": [
        list(zip("^1234567890ß´", "°!\"§$%&/()=?`")),
        list(zip("qwertzuiopü+", "QWERTZUIOPÜ*")),
        list(zip("asdfghjklöä#", "ASDFGHJKLÖÄ'")),
        list(zip("<yxcvbnm,.-", ">YXCVBNM;:_")),
    ],
    "QWERTY": [
        list(zip("`1234567890-=", "~!@#$%^&*()_+")),
        list(zip("qwertyuiop[]", "QWERTYUIOP{}")),
        list(zip("asdfghjkl;'\\", "ASDFGHJKL:\"|")),
        list(zip("zxcvbnm,./", "ZXCVBNM<>?")),
    ],
    "AZERTY": [
        list(zip("²&é\"'(-è_çà)=", "~1234567890°+")),
        list(zip("azertyuiop^$", "AZERTYUIOP¨£")),
        list(zip("qsdfghjklmù*", "QSDFGHJKLM%µ")),
        list(zip("<wxcvbn,;:!", ">WXCVBN?./§")),
    ],
}
LAYOUT_FOR_LANG = {"de": "QWERTZ", "gsw": "QWERTZ", "fr": "AZERTY", "en": "QWERTY"}

# Zeichen, die je nach Layout schwer zu finden sind (AltGr-Ebene usw.): immer als eigene Reihe
SYMBOLS = "@ # $ % & * ( ) - _ = + [ ] { } \\ | / ? : ; ' \" < > ~ ` ^ ! , .".split()

# Tasten, die laut POST-Zeile je Hersteller dorthin führen: (Taste, Beschriftung); „extra“ = Name der Zusatztaste
VENDOR_KEYS = {
    "dell": {"setup": ("F2", K.Key_F2), "boot": ("F11", K.Key_F11), "pxe": ("F12", K.Key_F12),
             "extra": ("lc", "F10", K.Key_F10)},
    "hpe": {"setup": ("F9", K.Key_F9), "boot": ("F11", K.Key_F11), "pxe": ("F12", K.Key_F12),
            "extra": ("ip", "F10", K.Key_F10)},
    "lenovo": {"setup": ("F1", K.Key_F1), "boot": ("F11", K.Key_F11), "pxe": ("F12", K.Key_F12),
               "extra": ("diag", "F10", K.Key_F10)},
    "smc": {"setup": ("Entf", K.Key_Delete), "boot": ("F11", K.Key_F11), "pxe": ("F12", K.Key_F12)},
}
VENDOR_KEYS["hpe_old"] = VENDOR_KEYS["hpe"]


def extra_label(kind, key):
    return {"lc": T("Lifecycle-Controller"), "ip": T("Intelligent Provisioning"), "diag": T("Diagnose")}[kind] + f"  ({key})"


OSK_CSS = """
QWidget#osk { background: palette(window); }
QWidget#osk QPushButton { border: 1px solid palette(mid); border-bottom-width: 2px; border-radius: 5px;
                          background: palette(button); padding: 2px 4px; min-width: 22px; min-height: 22px; }
QWidget#osk QPushButton:hover { background: palette(alternate-base); border-color: #34d399; }
QWidget#osk QPushButton:pressed { background: #34d399; color: #04261b; }
QWidget#osk QPushButton[mod="armed"] { background: #34d399; color: #04261b; font-weight: bold; }
QWidget#osk QPushButton[mod="locked"] { background: #f59e0b; color: #2b1a00; font-weight: bold; }
QWidget#osk QPushButton[macro="true"] { border-color: #34d399; font-weight: bold; }
QWidget#osk QLabel[h="true"] { color: palette(mid); font-size: 8pt; }
"""

MOD_BITS = (("shift", SHIFT, "⇧ Umschalt"), ("ctrl", CTRL, "Strg"), ("alt", ALT, "Alt"))      # Texte über T() anzeigen


def key_code(ch):
    """Qt-Tastenwert zu einem Zeichen (ASCII und Latin-1 entsprechen den Qt.Key-Werten der Großbuchstaben)."""
    if len(ch) != 1:
        return 0
    if ch == "ß":
        return K.Key_ssharp.value
    return ord(ch.upper()) if len(ch.upper()) == 1 else ord(ch)


# Zeichen, die ein echtes Tastenereignis dieser Tasten trägt (ohne Strg/Alt)
IMPLICIT_TEXT = {K.Key_Return.value: "\r", K.Key_Tab.value: "\t", K.Key_Backspace.value: "\b",
                 K.Key_Escape.value: "\x1b", K.Key_Space.value: " "}


def send_qt_key(widget, key, mods, text=""):
    """Schickt eine Taste als echte Tastenereignisse an ein Widget (z. B. die eingebettete Web-Konsole).
    Umschalttasten werden als eigene Tastendrücke davor und danach gesendet, weil viele Web-Konsolen den Zustand
    selbst mitzählen. Ziel ist der Fokus-Stellvertreter der Seite (bei QWebEngineView das Chromium-Widget)."""
    target = widget.focusProxy() or widget
    mods = int(mods)
    if not text and not mods & (CTRL | ALT):
        text = IMPLICIT_TEXT.get(int(key), "")
    held, press = 0, []
    for _name, bit, _label in MOD_BITS:
        if mods & bit:
            mk = {SHIFT: K.Key_Shift, CTRL: K.Key_Control, ALT: K.Key_Alt}[bit]
            held |= bit
            press.append((mk, held))
    seq = [(QEvent.Type.KeyPress, k, m, "") for k, m in press]
    seq.append((QEvent.Type.KeyPress, key, mods, text))
    seq.append((QEvent.Type.KeyRelease, key, mods, ""))
    for k, m in reversed(press):
        seq.append((QEvent.Type.KeyRelease, k, m, ""))
    for typ, k, m, tx in seq:
        QApplication.sendEvent(target, QKeyEvent(typ, int(k), Qt.KeyboardModifier(m), tx))


class KeyFeeder(QObject):
    """Gibt eine Warteschlange von Tasten gemächlich aus (Einfügen und „Text tippen“): Manche UEFI-Eingaben
    verschlucken Zeichen, wenn sie zu schnell kommen. Der Takt ist in Millisekunden einstellbar."""
    key = pyqtSignal(int, int, str)
    finished = pyqtSignal()

    def __init__(self, parent=None, interval_ms=12):
        super().__init__(parent)
        self.queue = []
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._tick)

    def set_interval(self, ms):
        self.timer.setInterval(max(1, int(ms)))

    def busy(self):
        return bool(self.queue)

    def add_text(self, text, enter=False):
        """Text zeichenweise vormerken; Zeilenumbruch = Enter, Tab = Tab. Gibt die Zahl der Tasten zurück."""
        n = 0
        for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
            if ch == "\n":
                self.queue.append((K.Key_Return.value, 0, ""))
            elif ch == "\t":
                self.queue.append((K.Key_Tab.value, 0, ""))
            elif ch.isprintable():
                self.queue.append((key_code(ch) or K.Key_unknown.value, 0, ch))
            else:
                continue
            n += 1
        if enter:
            self.queue.append((K.Key_Return.value, 0, ""))
            n += 1
        if self.queue and not self.timer.isActive():
            self.timer.start()
        return n

    def add_keys(self, keys):
        self.queue.extend(keys)
        if self.queue and not self.timer.isActive():
            self.timer.start()

    def cancel(self):
        self.queue.clear()
        self.timer.stop()

    def _tick(self):
        if not self.queue:
            self.timer.stop()
            self.finished.emit()
            return
        k, m, t = self.queue.pop(0)
        self.key.emit(k, m, t)
        if not self.queue:
            self.timer.stop()
            self.finished.emit()


class OnScreenKeyboard(QWidget):
    """Die Tastatur selbst. Signale: key(Qt-Taste, Umschalttasten, Zeichen) für jede gedrückte Taste;
    type_text(Text, mit Enter) für „Text tippen“; fkeys_as_esc(bool) für die Escape-Folgen statt F-Tasten."""
    key = pyqtSignal(int, int, str)
    keys = pyqtSignal(list)
    type_text = pyqtSignal(str, bool)
    fkeys_as_esc = pyqtSignal(bool)

    def __init__(self, lang="de", parent=None):
        super().__init__(parent)
        self.setObjectName("osk")
        self.setStyleSheet(OSK_CSS)
        self.mode = "serial"                    # serial | web: bestimmt die Folge für Strg+Alt+Entf
        self.vendor = ""
        self.state = {"shift": 0, "ctrl": 0, "alt": 0}      # 0 aus, 1 einmalig, 2 fest
        self.mod_btn = {}
        self.char_btns = []                     # (Knopf, normal, mit Umschalt)
        self.layout_name = LAYOUT_FOR_LANG.get(lang, "QWERTY")
        self.show_full = False                  # Standard: kompakt (ohne Buchstabenblock)

        self.layout_box = QComboBox()
        self.layout_box.addItems(list(LAYOUTS))
        self.layout_box.setCurrentText(self.layout_name)
        self.layout_box.setToolTip(T("Anordnung der Buchstaben auf der Bildschirmtastatur. Dem Server ist sie egal: "
                                     "er bekommt immer das Zeichen, das auf der Taste steht."))
        self.layout_box.currentTextChanged.connect(self.set_layout)
        self.esc_box = QCheckBox(T("F-Tasten als Esc-Folgen"))
        self.esc_box.setToolTip(T("Nur Textkonsole. Für UEFI-Oberflächen, die F-Tasten nicht verstehen: Esc+1 … Esc+0 "
                                  "statt F1 … F10, Esc+! statt F11, Esc+@ statt F12."))
        self.esc_box.toggled.connect(self.fkeys_as_esc.emit)

        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 4, 0, 0)
        self.body.setSpacing(3)
        self.main = QVBoxLayout()
        self.main.setSpacing(3)
        self.body.addLayout(self.main)
        self.build()

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText(T("Text, der Zeichen für Zeichen getippt wird …"))
        self.text_edit.setToolTip(T("Tippt den Text in die Konsole, mit kleinem Abstand zwischen den Zeichen. Für lange Eingaben "
                                    "(Kernel-Parameter, URLs). Wird nirgends gespeichert."))
        self.text_edit.returnPressed.connect(self.send_text)
        self.enter_box = QCheckBox(T("mit Enter"))
        send = QPushButton(T("Tippen"))
        send.clicked.connect(self.send_text)
        send.setProperty("macro", True)
        row = QHBoxLayout()
        row.addWidget(QLabel(T("Text tippen:")))
        row.addWidget(self.text_edit, 1)
        row.addWidget(self.enter_box)
        row.addWidget(send)
        self.body.addLayout(row)

    # ---------- Aufbau ----------
    def btn(self, text, slot, tip="", w=None, macro=False):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)    # Fokus bleibt in der Konsole
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if w:
            b.setMinimumWidth(w)
        if tip:
            b.setToolTip(tip)
        if macro:
            b.setProperty("macro", True)
        b.clicked.connect(slot)
        return b

    def key_btn(self, label, key, tip="", w=None):
        return self.btn(label, lambda _=False, k=key: self.press(int(k), ""), tip, w)

    def row(self, widgets):
        h = QHBoxLayout()
        h.setSpacing(3)
        for w in widgets:
            h.addWidget(w)
        self.main.addLayout(h)
        return h

    def build(self):
        keep = (self.layout_box, self.esc_box)
        while self.main.count():                    # beim Layoutwechsel alles neu
            it = self.main.takeAt(0)
            if it.widget() is not None:
                it.widget().hide()
                it.widget().deleteLater()
                continue
            lay = it.layout()
            if lay is not None:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w is None:
                        continue
                    if w in keep:
                        w.setParent(None)
                    else:
                        w.deleteLater()
                lay.deleteLater()
        self.mod_btn.clear()
        self.char_btns.clear()

        # Zeile 1: Hersteller-Tasten und Kombinationen
        self.macro_row = QHBoxLayout()
        self.macro_row.setSpacing(3)
        self.main.addLayout(self.macro_row)
        self.fill_macros()

        # Zeile 2: Esc und F-Tasten
        fr = [self.key_btn("Esc", K.Key_Escape)] + [self.key_btn(f"F{i}", getattr(K, f"Key_F{i}")) for i in range(1, 13)]
        self.row(fr)

        # Zeile 3: Umschalttasten, Bedienung, Pfeile, Navigation (immer sichtbar, auch in der kompakten Ansicht)
        self.row([self.mod("shift"), self.mod("ctrl"), self.mod("alt"),
                  self.key_btn("⇥", K.Key_Tab, T("Tabulator"), 36),
                  self.key_btn("⏎", K.Key_Return, T("Enter"), 44),
                  self.key_btn("⌫", K.Key_Backspace, T("Rücktaste"), 44),
                  self.btn(T("Leertaste"), lambda _=False: self.press(K.Key_Space.value, " "), "", 150),
                  self.key_btn("←", K.Key_Left, "", 34), self.key_btn("↓", K.Key_Down, "", 34),
                  self.key_btn("↑", K.Key_Up, "", 34), self.key_btn("→", K.Key_Right, "", 34),
                  self.key_btn(T("Einfg"), K.Key_Insert, T("Einfg (Insert)"), 40),
                  self.key_btn(T("Entf"), K.Key_Delete, T("Entf (Delete)"), 40),
                  self.key_btn(T("Pos1"), K.Key_Home, "", 40), self.key_btn(T("Ende"), K.Key_End, "", 40),
                  self.key_btn(T("Bild↑"), K.Key_PageUp, "", 40), self.key_btn(T("Bild↓"), K.Key_PageDown, "", 40)])

        # Vollständige Tastatur (aufklappbar)
        self.full = QWidget()
        fl = QVBoxLayout(self.full)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(3)
        rows = LAYOUTS[self.layout_name]
        for chars, indent in ((rows[0], 0), (rows[1], 24), (rows[2], 40), (rows[3], 60)):
            h = QHBoxLayout()
            h.setSpacing(3)
            h.addSpacing(indent)
            for n, sft in chars:
                h.addWidget(self.char(n, sft))
            h.addStretch()
            fl.addLayout(h)
        for chunk in (SYMBOLS[:19], SYMBOLS[19:]):
            h = QHBoxLayout()
            h.setSpacing(3)
            for c in chunk:
                h.addWidget(self.char(c, c))
            h.addStretch()
            fl.addLayout(h)
        self.main.addWidget(self.full)
        self.full.setVisible(self.show_full)

        # Optionen
        opt = QHBoxLayout()
        self.full_btn = QPushButton(T("▼ Alle Tasten") if not self.show_full else T("▲ Weniger Tasten"))
        self.full_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.full_btn.setToolTip(T("Zeigt die ganze Tastatur mit Buchstaben, Ziffern und Sonderzeichen."))
        self.full_btn.clicked.connect(self.toggle_full)
        opt.addWidget(self.full_btn)
        opt.addWidget(QLabel(T("Anordnung:")))
        opt.addWidget(self.layout_box)
        opt.addWidget(self.esc_box)
        opt.addStretch()
        hint = QLabel(T("Strg/Alt/Umschalt: ein Klick = nächste Taste, zweiter Klick = bleibt gedrückt, dritter = aus."))
        hint.setProperty("h", True)
        opt.addWidget(hint)
        self.main.addLayout(opt)
        self.refresh_chars()

    def fill_macros(self):
        while self.macro_row.count():
            w = self.macro_row.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        vk = VENDOR_KEYS.get(self.vendor)
        lab = QLabel(T("Hersteller:") if vk else T("Hersteller unbekannt:"))
        self.macro_row.addWidget(lab)
        if vk:
            for kind, text, tip in (("setup", T("Setup"), T("Ins UEFI-Setup (Taste {0}), im POST-Bild, solange „Press … for Setup“ steht.")),
                                    ("boot", T("Boot-Menü"), T("Einmaliges Boot-Menü (Taste {0}).")),
                                    ("pxe", T("PXE-Boot"), T("Netzwerk-Start (Taste {0}) – nicht bei jedem Modell belegt."))):
                label, key = vk[kind]
                label = T(label) if label == "Entf" else label
                self.macro_row.addWidget(self.btn(f"{text}  ({label})", lambda _=False, k=key: self.press(int(k), "", plain=True),
                                                  tip.format(label), macro=True))
            if "extra" in vk:
                kind, key_name, key = vk["extra"]
                self.macro_row.addWidget(self.btn(extra_label(kind, key_name), lambda _=False, k=key: self.press(int(k), "", plain=True),
                                                  T("Zusätzliche Hersteller-Taste im POST-Bild."), macro=True))
        self.macro_row.addWidget(self.btn(T("Strg+Alt+Entf"), self.ctrl_alt_del,
                                          T("Neustart-Kombination. Bild: echte Tastenfolge Strg+Alt+Entf. Text: die Folge "
                                            "Esc R Esc r Esc R, die viele UEFI/BIOS (AMI, Insyde) als Strg+Alt+Entf deuten."),
                                          macro=True))
        self.macro_row.addStretch()

    def mod(self, name):
        label = T({n: t for n, _b, t in MOD_BITS}[name])
        b = self.btn(label, lambda _=False, n=name: self.cycle(n), T("Umschalttaste: einmal, fest, aus"), 74)
        self.mod_btn[name] = b
        self.paint_mod(name)
        return b

    def char(self, normal, shifted):
        b = self.btn(normal, lambda _=False, n=normal, s=shifted: self.press_char(n, s))
        b.setMinimumWidth(30)
        self.char_btns.append((b, normal, shifted))
        return b

    # ---------- Zustand ----------
    def toggle_full(self):
        self.show_full = not self.show_full
        self.full.setVisible(self.show_full)
        self.full_btn.setText(T("▲ Weniger Tasten") if self.show_full else T("▼ Alle Tasten"))

    def set_layout(self, name):
        if name in LAYOUTS and name != self.layout_name:
            self.layout_name = name
            self.build()

    def set_mode(self, mode):
        self.mode = mode
        self.esc_box.setVisible(mode == "serial")

    def set_vendor(self, vendor):
        if vendor != self.vendor:
            self.vendor = vendor
            self.fill_macros()

    def cycle(self, name):
        self.state[name] = (self.state[name] + 1) % 3
        self.paint_mod(name)
        self.refresh_chars()

    def paint_mod(self, name):
        b = self.mod_btn.get(name)
        if b is None:
            return
        b.setProperty("mod", {0: "", 1: "armed", 2: "locked"}[self.state[name]])
        b.style().unpolish(b)
        b.style().polish(b)

    def refresh_chars(self):
        shift = bool(self.state["shift"])
        for b, n, s in self.char_btns:
            b.setText(s if shift else n)

    def mods(self):
        m = 0
        for name, bit, _l in MOD_BITS:
            if self.state[name]:
                m |= bit
        return m

    def release_once(self):
        for name in self.state:
            if self.state[name] == 1:
                self.state[name] = 0
                self.paint_mod(name)
        self.refresh_chars()

    # ---------- Tasten ----------
    def press(self, key, text, plain=False):
        """plain: Hersteller-Taste ohne die gerade gehaltenen Umschalttasten."""
        self.key.emit(key, 0 if plain else self.mods(), text)
        if not plain:
            self.release_once()

    def press_char(self, normal, shifted):
        """Zeichen der Taste; mit Umschalt das obere. Gemeldet wird das fertige Zeichen samt gehaltenen
        Umschalttasten (Strg+C kommt so als Strg mit Taste C an)."""
        ch = shifted if self.state["shift"] else normal
        self.key.emit(key_code(ch), self.mods(), ch)
        self.release_once()

    def ctrl_alt_del(self):
        if self.mode == "web":
            self.key.emit(K.Key_Delete.value, CTRL | ALT, "")
        else:
            self.keys.emit([(K.Key_Escape.value, 0, ""), (ord("R"), 0, "R"), (K.Key_Escape.value, 0, ""),
                            (ord("R"), 0, "r"), (K.Key_Escape.value, 0, ""), (ord("R"), 0, "R")])

    def send_text(self):
        text = self.text_edit.text()
        if text:
            self.type_text.emit(text, self.enter_box.isChecked())
            self.text_edit.clear()
