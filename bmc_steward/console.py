"""BMC-Steward: UEFI-Konsole (eingebettete Web-Konsole, Text-Konsole SOL/SSH, externer Browser)."""
from PyQt6.QtWidgets import (QAbstractItemView, QFrame, QMenu, QToolButton, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QStyledItemDelegate, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPixmap)
from PyQt6.QtCore import (QT_VERSION_STR, QBuffer, QByteArray, QEvent, QIODevice, QLocale, QObject, QProcess, QRect, QSocketNotifier, QThread, QTimer, QUrl, Qt, pyqtSignal)
import codecs
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import time
from . import i18n
from .i18n import T
from .core import WINDOWS
from .redfish import RedfishClient, fingerprint
from . import trust
from . import deps
from .monitor import MonitorWidget, SegmentSwitch
from .netscan_dialog import NetScanDialog
from .osk import KeyFeeder, OnScreenKeyboard, send_qt_key
from .termkeys import ALT, CTRL, FKEY_ESC, KEYMAP, KEYMAP_ENUM, SHIFT, encode_key





# ======================================================================================================
#  UEFI-Konsole: zeigt das UEFI/BIOS eines Servers über den BMC an, bedienbar wie am Original.
#  Drei Wege: eingebettete HTML5-Konsole des BMC (Bild, Maus, Tastatur), Textkonsole über
#  Serial-over-LAN bzw. SSH und Öffnen im Standardbrowser. Es wird nichts gespeichert.
# ======================================================================================================
try:                                    # optional; muss vor dem QApplication importiert werden
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE = True
except ImportError:
    WEBENGINE = False


try:
    import pyte
except ImportError:
    pyte = None


try:                                    # SSH-Textkonsole (Dell/HPE/Lenovo), plattformunabhängig
    import paramiko
except ImportError:
    paramiko = None


try:                                    # IPMI-Serial-over-LAN (Supermicro), plattformunabhängig
    from pyghmi.ipmi import console as ipmiconsole
except ImportError:
    ipmiconsole = None



# Pfad der HTML5-Konsole je Hersteller (nach der Anmeldung); Lenovo: Startseite, dort „Remote Console“
CONSOLE_PATH = {"dell": "/console", "hpe": "/irc.html", "hpe_old": "/irc.html",
                "smc": "/cgi/url_redirect.cgi?url_name=man_ikvm_html5_bootstrap", "lenovo": "/"}


# Befehl, mit dem man sich per SSH auf die serielle Konsole des BMC schaltet
SSH_SOL_CMD = {"dell": "console com2", "hpe": "vsp", "hpe_old": "vsp", "lenovo": "console 1"}


LED_COLOR = {"idle": "#9ca3af", "busy": "#f59e0b", "live": "#34d399", "error": "#ef4444"}

TOOLS_CSS = ("QWidget#contools QToolButton { border: 1px solid palette(mid); border-radius: 6px; padding: 4px 9px;"
             " background: palette(button); }"
             "QWidget#contools QToolButton:hover { border-color: #34d399; background: palette(alternate-base); }"
             "QWidget#contools QToolButton:checked { background: #34d399; color: #04261b; border-color: #34d399;"
             " font-weight: bold; }"
             "QWidget#contools QToolButton:disabled { color: palette(mid); border-color: palette(midlight); }"
             "QWidget#contools QToolButton::menu-indicator { image: none; }")

STRIP_CSS = "QFrame#constrip { border: 1px solid palette(mid); border-radius: 6px; }"

FRAME_CSS = ("QFrame#conframe { background: #0b0f14; border: 2px solid #2b3340; border-radius: 10px; }"
             "QFrame#conframe[state=\"busy\"] { border-color: #f59e0b; }"
             "QFrame#conframe[state=\"live\"] { border-color: #34d399; }"
             "QFrame#conframe[state=\"error\"] { border-color: #ef4444; }"
             "QLabel#conhead { color: #9aa4b2; padding: 5px 12px 3px 12px; font-size: 9pt; }"
             "QLabel#connote { color: #9aa4b2; }"
             "QTabWidget::pane { border: none; } ")

AUTO_RECONNECT_MAX = 20                 # so oft versucht „Automatisch neu verbinden“ es hintereinander


TERM_SIZES = [("80 × 24", 80, 24), ("80 × 25", 80, 25), ("100 × 31", 100, 31), ("132 × 43", 132, 43)]



AUTOLOGIN_JS = """
(function (u, p) {
  function vis(e) { return e && e.offsetParent !== null && !e.disabled; }
  function setv(e, v) {
    var d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    d.set.call(e, v);
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
  }
  var all = [].slice.call(document.querySelectorAll('input'));
  var pw = all.filter(function (e) { return e.type === 'password' && vis(e); })[0];
  if (!pw) return 'wait';
  var us = all.filter(function (e) { return vis(e) && /^(text|email|)$/.test(e.type) &&
           (e.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING); });
  var user = us[us.length - 1];
  if (!user) return 'wait';
  setv(user, u); setv(pw, p);
  var scope = pw.form || document;
  var btns = [].slice.call(scope.querySelectorAll('button, input[type=submit], input[type=button], a.btn'));
  var go = btns.filter(function (b) { return vis(b) && b.type !== 'reset' &&
           /log ?in|sign ?in|anmeld|submit|ok|connexion|login/i.test((b.innerText || b.value || b.id || '')); })[0] ||
           btns.filter(vis)[0];
  if (go) go.click(); else if (pw.form) pw.form.submit();
  return 'done';
})(%s, %s)
"""




def tls_blocks_chromium(mode):
    """Chromium (QtWebEngine) spricht kein TLS 1.0/1.1; alte BMCs gehen dort nicht."""
    return mode in ("old", "oldest")




class ConsoleProbeThread(QThread):
    """Ermittelt Hersteller und TLS-Stufe des BMC (nur Verbindungsaufbau und Redfish-Wurzel, ohne Anmeldung)."""
    done = pyqtSignal(str, str)         # Hersteller, TLS-Stufe

    def __init__(self, parent, ip, vendor):
        super().__init__(parent)
        self.ip, self.vendor = ip, vendor

    def run(self):
        try:
            rc = RedfishClient(dry=False)
            v = self.vendor or rc.vendor(self.ip)
            self.done.emit(v, rc.tls_mode(self.ip, 10) or "")
        except Exception:
            self.done.emit("offline", "")




class BootSetupThread(QThread):
    """Setzt „beim nächsten Start ins UEFI-Setup“ und startet den Server neu (bzw. schaltet ihn ein)."""
    done = pyqtSignal(bool, str)

    def __init__(self, parent, ip, user, pw, vendor):
        super().__init__(parent)
        self.ip, self.user, self.pw, self.vendor = ip, user, pw, vendor

    def run(self):
        rc = None
        try:
            rc = RedfishClient(dry=False)
            v = self.vendor or rc.vendor(self.ip)
            if v not in ("dell", "hpe", "smc", "lenovo"):
                self.done.emit(False, T("Dieser Hersteller bzw. diese Generation kann das nicht per Redfish. "
                                        "Bitte im UEFI-Bild F2/F9/Entf selbst drücken."))
                return
            user = self.user or rc.DEFAULT_USER[v]
            blocked = []
            rc.log = lambda line, kind="": blocked.append(line.strip()) if kind == "err" else None
            if not rc.self_guard(self.ip, user, self.pw, v):
                self.done.emit(False, "\n".join(blocked) or T("Eigenschutz: Neustart gesperrt."))
                return
            path = rc.sys_path(v)
            code = rc.api(self.ip, user, self.pw, "PATCH", path,
                          {"Boot": {"BootSourceOverrideTarget": "BiosSetup", "BootSourceOverrideEnabled": "Once"}})
            if not rc.ok(code):
                self.done.emit(False, "\n".join(rc.fail_lines(T("Start ins UEFI-Setup vormerken"), code)))
                return
            state = rc.power_state(self.ip, user, self.pw, v)
            action = "On" if state == "Off" else "ForceRestart"
            code = rc.api(self.ip, user, self.pw, "POST", f"{path}/Actions/ComputerSystem.Reset",
                          {"ResetType": action})
            if not rc.ok(code):
                self.done.emit(False, "\n".join(rc.fail_lines(T("Neustart auslösen"), code)))
                return
            self.done.emit(True, T("Vorgemerkt, Server startet neu und landet im UEFI-Setup. "
                                   "Das Bild erscheint nach etwa ein bis zwei Minuten."))
        except Exception as e:
            self.done.emit(False, T('Unerwarteter Fehler: {0!r}').format(e))
        finally:
            if rc is not None:
                rc.close_sessions()




# ---------------------------------------------------------------------------------------- Textkonsole
ANSI_COLORS = {"black": "#000000", "red": "#cd0000", "green": "#00cd00", "brown": "#cdcd00", "blue": "#3050ff",
               "magenta": "#cd00cd", "cyan": "#00cdcd", "white": "#e5e5e5", "brightblack": "#7f7f7f",
               "brightred": "#ff3030", "brightgreen": "#30ff30", "brightbrown": "#ffff30",
               "brightblue": "#6080ff", "brightmagenta": "#ff30ff", "brightcyan": "#30ffff",
               "brightwhite": "#ffffff"}




# Farbschemata der Textkonsole: Schlüssel -> (Hintergrund, Vordergrund, Markierung)
THEMES = {"classic": ("#000000", "#c8c8c8", "#264f78"), "green": ("#001a08", "#3dff72", "#0b6b2c"),
          "amber": ("#1a0f00", "#ffb000", "#6b4a00"), "light": ("#f7f7f2", "#1c1c1c", "#b5d5ff"),
          "contrast": ("#000000", "#ffffff", "#1d4ed8")}
HISTORY_LINES = 2000                    # Zeilen Verlauf, mit Mausrad oder Bild↑/Bild↓ (Umschalt) erreichbar


def theme_names():
    return [("classic", T("Klassisch (grau auf schwarz)")), ("green", T("Grün (Phosphor)")),
            ("amber", T("Bernstein")), ("light", T("Hell")), ("contrast", T("Hoher Kontrast"))]


class TermView(QWidget):
    """Kleines Terminal (VT100/ANSI) auf Basis von pyte. Tasten gehen als Bytes an send(); Bild nur zur Anzeige.
    Mit Verlauf (Mausrad), Markieren/Kopieren, Farbschemata, blinkendem Cursor und Hinweis bei fehlendem Signal."""
    send = pyqtSignal(bytes)
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()
    NO_SIGNAL_S = 8                     # so lange ohne Daten (und leerer Bildschirm): Hinweis einblenden

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(11)
        self.setFont(font)
        self.fm = QFontMetrics(font)
        self.cw, self.ch = self.fm.horizontalAdvance("M"), self.fm.height()
        self.cp437 = False
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")
        self.live = False
        self.theme = "classic"
        self.sel = None                 # ((x1, y1), (x2, y2)) in Bildschirmzellen, oder None
        self.blink_on = True
        self.rx_bytes = 0
        self.last_rx = time.monotonic()
        self.setMouseTracking(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.set_size(80, 24)

    # ---- Größe, Schrift, Farben ----
    def set_size(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen = pyte.HistoryScreen(cols, rows, history=HISTORY_LINES, ratio=0.5) if pyte else None
        self.stream = pyte.Stream(self.screen) if pyte else None
        self.sel = None
        self.setFixedSize(cols * self.cw + 2, rows * self.ch + 2)
        self.update()

    def set_font_size(self, pt):
        """Schriftgröße ändern, ohne den Bildschirminhalt zu verlieren (set_size würde ihn leeren)."""
        font = QFont(self.font())
        if font.pointSize() == pt:
            return
        font.setPointSize(pt)
        self.setFont(font)
        self.fm = QFontMetrics(font)
        self.cw, self.ch = self.fm.horizontalAdvance("M"), self.fm.height()
        self.setFixedSize(self.cols * self.cw + 2, self.rows * self.ch + 2)
        self.update()

    def fit_to(self, w, h):
        """Größte Schrift (8 bis 48 pt), bei der das Raster in w × h Pixel passt; für den Vollbildmodus."""
        best = 8
        for pt in range(8, 49):
            font = QFont(self.font())
            font.setPointSize(pt)
            fm = QFontMetrics(font)
            if self.cols * fm.horizontalAdvance("M") + 2 > w or self.rows * fm.height() + 2 > h:
                break
            best = pt
        self.set_font_size(best)

    def set_theme(self, key):
        if key in THEMES:
            self.theme = key
            self.update()

    def set_encoding(self, cp437):
        self.cp437 = cp437
        self.dec = codecs.getincrementaldecoder("cp437" if cp437 else "utf-8")("replace")

    def reset(self):
        self.set_size(self.cols, self.rows)
        self.set_encoding(self.cp437)
        self.rx_bytes = 0
        self.last_rx = time.monotonic()

    def set_live(self, on):
        self.live = on
        self.last_rx = time.monotonic()
        if on:
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def tick(self):
        self.blink_on = not self.blink_on
        self.update()

    # ---- Daten, Verlauf ----
    def feed(self, data):
        if self.stream:
            self.rx_bytes += len(data)
            self.last_rx = time.monotonic()
            self.stream.feed(self.dec.decode(data))        # neue Daten holen den Verlauf selbst nach unten
            self.update()

    def scrolled_back(self):
        h = getattr(self.screen, "history", None)
        return bool(h) and h.position < h.size

    def scroll_history(self, up):
        if self.screen is None:
            return
        (self.screen.prev_page if up else self.screen.next_page)()
        self.sel = None
        self.update()

    def scroll_bottom(self):
        while self.scrolled_back():
            self.screen.next_page()
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            e.ignore()                  # Strg+Rad: der Scroll-Bereich zoomt
            return
        dy = e.angleDelta().y()
        if dy:
            self.scroll_history(dy > 0)
        e.accept()

    # ---- Text, Markieren ----
    def lines(self):
        """Sichtbarer Bildschirm als Textzeilen (ohne Leerraum am Ende)."""
        if not self.screen:
            return []
        return [self.screen.display[y].rstrip() for y in range(self.rows)]

    def cell_at(self, pos):
        x = min(max((pos.x() - 1) // self.cw, 0), self.cols - 1)
        y = min(max((pos.y() - 1) // self.ch, 0), self.rows - 1)
        return x, y

    def sel_range(self):
        if not self.sel:
            return None
        a, b = self.sel
        return (a, b) if (a[1], a[0]) <= (b[1], b[0]) else (b, a)

    def selected_text(self):
        r = self.sel_range()
        if not r or not self.screen:
            return ""
        (x1, y1), (x2, y2) = r
        out = []
        disp = self.screen.display
        for y in range(y1, y2 + 1):
            lo = x1 if y == y1 else 0
            hi = x2 if y == y2 else self.cols - 1
            out.append(disp[y][lo:hi + 1].rstrip())
        return "\n".join(out)

    def select_all(self):
        self.sel = ((0, 0), (self.cols - 1, self.rows - 1))
        self.update()

    def clear_selection(self):
        if self.sel:
            self.sel = None
            self.update()

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MouseButton.LeftButton:
            c = self.cell_at(e.position().toPoint())
            self.sel = (c, c)
            self.update()
        elif e.button() == Qt.MouseButton.MiddleButton:
            self.paste_requested.emit()

    def mouseMoveEvent(self, e):
        if self.sel and e.buttons() & Qt.MouseButton.LeftButton:
            self.sel = (self.sel[0], self.cell_at(e.position().toPoint()))
            self.update()

    def mouseReleaseEvent(self, e):
        if self.sel and self.sel[0] == self.sel[1]:
            self.sel = None             # bloßer Klick markiert nichts
            self.update()

    def context_menu(self, pos):
        m = QMenu(self)
        a_copy = m.addAction(T("Markierung kopieren") if self.sel else T("Ganzen Bildschirm kopieren"))
        a_copy.triggered.connect(self.copy_requested.emit)
        a_paste = m.addAction(T("Einfügen (tippt in den Server)"))
        a_paste.setEnabled(self.live)
        a_paste.triggered.connect(self.paste_requested.emit)
        m.addSeparator()
        m.addAction(T("Alles markieren")).triggered.connect(self.select_all)
        m.exec(self.mapToGlobal(pos))

    # ---- Zeichnen ----
    def _color(self, name, default):
        if name == "default":
            return default
        if name in ANSI_COLORS:
            return QColor(ANSI_COLORS[name])
        if re.fullmatch(r"[0-9a-fA-F]{6}", name):
            return QColor("#" + name)
        return default

    def paintEvent(self, _):
        p = QPainter(self)
        bgs, fgs, sels = THEMES[self.theme]
        bg0, fg0, selc = QColor(bgs), QColor(fgs), QColor(sels)
        p.fillRect(self.rect(), bg0)
        if not self.screen:
            return
        p.setFont(self.font())
        asc = self.fm.ascent()
        cur = self.screen.cursor
        rng = self.sel_range()
        for y in range(self.rows):
            line = self.screen.buffer[y]
            for x in range(self.cols):
                c = line[x]
                fg, bg = self._color(c.fg, fg0), self._color(c.bg, bg0)
                if c.reverse:
                    fg, bg = bg, fg
                r = QRect(1 + x * self.cw, 1 + y * self.ch, self.cw, self.ch)
                chosen = bool(rng) and (rng[0][1], rng[0][0]) <= (y, x) <= (rng[1][1], rng[1][0])
                if chosen:
                    bg = selc
                if bg != bg0:
                    p.fillRect(r, bg)
                if c.data.strip():
                    f = QFont(self.font())
                    f.setBold(bool(c.bold))
                    f.setUnderline(bool(c.underscore))
                    p.setFont(f)
                    p.setPen(fg)
                    p.drawText(r.x(), r.y() + asc, c.data)
        if self.live and not cur.hidden and self.hasFocus() and self.blink_on and not self.scrolled_back():
            p.fillRect(QRect(1 + cur.x * self.cw, 1 + cur.y * self.ch, self.cw, self.ch), QColor(200, 200, 200, 110))
        if not self.hasFocus():
            p.setPen(QColor(120, 120, 120))
            p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        if self.scrolled_back():
            self._badge(p, T("↑ Verlauf – Mausrad nach unten oder Taste drücken"), top=True)
        if self.live and self.rx_bytes == 0 and time.monotonic() - self.last_rx > self.NO_SIGNAL_S:
            self._badge(p, T("Noch kein Text vom Server. Ist im UEFI „Console Redirection“ an? Dann hier eine Taste drücken."),
                        top=False)

    def _badge(self, p, text, top):
        """Kleine Hinweisleiste oben oder unten im Bild."""
        f = QFont(self.font())
        f.setPointSize(max(8, f.pointSize() - 2))
        fm = QFontMetrics(f)
        w = min(self.width() - 16, fm.horizontalAdvance(text) + 20)
        h = fm.height() + 8
        r = QRect((self.width() - w) // 2, 6 if top else self.height() - h - 6, w, h)
        p.setFont(f)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 24, 31, 215))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor("#e5e7eb"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, fm.elidedText(text, Qt.TextElideMode.ElideRight, w - 12))

    # ---- Tastatur ----
    def focusNextPrevChild(self, _next):
        return False                    # Tab gehört dem UEFI, nicht der Fokusreihenfolge

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:
            e.accept()                  # Strg+Buchstabe usw. nicht als Programmkürzel abfangen
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_C:       # Strg+Umschalt+C: kopieren (Strg+C gehört dem Server)
            self.copy_requested.emit()
            return
        if ((mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_V) or \
                (mods == Qt.KeyboardModifier.ShiftModifier and key == Qt.Key.Key_Insert):
            self.paste_requested.emit()
            return
        if not self.live:
            return
        if mods & Qt.KeyboardModifier.ShiftModifier and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.scroll_history(key == Qt.Key.Key_PageUp)                   # Umschalt+Bild: Verlauf, geht nicht zum Server
            return
        bits = (SHIFT if mods & Qt.KeyboardModifier.ShiftModifier else 0) | \
               (CTRL if mods & Qt.KeyboardModifier.ControlModifier else 0) | \
               (ALT if mods & Qt.KeyboardModifier.AltModifier else 0)
        data = encode_key(key, bits, e.text(), self.cp437)
        if data is not None:
            self.clear_selection()
            self.scroll_bottom()
            self.send.emit(data)




class TermScroll(QScrollArea):
    """Scroll-Bereich der Textkonsole. Mit auto_fit (Vollbild) passt er die Schrift an, damit das Raster den
    Platz füllt; sonst gilt die eingestellte Schriftgröße (Standard 11 pt, mit Strg+Rad oder A−/A+ änderbar)."""
    DEFAULT_PT = 11
    MIN_PT, MAX_PT = 8, 32

    def __init__(self, term):
        super().__init__()
        self.term, self.auto_fit = term, False
        self.pt = self.DEFAULT_PT
        self.setWidget(term)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def refit(self):
        if self.auto_fit:
            size = self.viewport().size()
            self.term.fit_to(size.width(), size.height())
        else:
            self.term.set_font_size(self.pt)

    def zoom(self, delta):
        """Schrift um delta pt ändern (außerhalb des Vollbilds)."""
        self.pt = min(self.MAX_PT, max(self.MIN_PT, self.pt + delta))
        self.refit()
        return self.pt

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier and not self.auto_fit:
            self.zoom(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
            return
        super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.auto_fit:
            self.refit()


class SerialSession(QObject):
    """Textkonsole ohne externe Programme: SSH über paramiko (Dell/HPE/Lenovo),
    IPMI-Serial-over-LAN über pyghmi (Supermicro). Füllt eine TermView.
    Läuft unter Linux und Windows gleich."""

    _feed = pyqtSignal(bytes)       # Daten vom BMC -> TermView (immer im GUI-Thread)
    _done = pyqtSignal(object)      # Verbindung von der Gegenseite beendet (Code oder None)

    def __init__(self, view, on_end):
        super().__init__()
        self.view, self.on_end = view, on_end
        self.proc = None            # Sitzungsobjekt, solange verbunden (dient als „läuft"-Flag)
        self.reader = None
        self._write = None          # Funktion zum Senden von Bytes an den BMC
        self._close = None          # Funktion zum Schließen der Verbindung
        self._feed.connect(self.view.feed)
        self._done.connect(self._finish)

    @staticmethod
    def missing(method, vendor):
        """Fehlende Python-Pakete für das Verfahren; leere Liste = alles da."""
        if method == "ipmitool":
            return [] if ipmiconsole is not None else ["python-pyghmi"]
        return [] if paramiko is not None else ["python-paramiko"]

    def start(self, method, ip, user, pw, vendor):
        host = ip.strip("[]")
        if method == "ipmitool":
            self._start_sol(host, user, pw)
        else:
            self._start_ssh(host, user, pw, vendor)

    # ---- SSH (paramiko) ----
    def _start_ssh(self, host, user, pw, vendor):
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw = dict(hostname=host, username=user, password=pw, timeout=10,
                  allow_agent=False, look_for_keys=False, banner_timeout=15, auth_timeout=15)
        if vendor == "hpe_old":                 # iLO 3/4 sprechen nur alte SSH-Verfahren
            kw["disabled_algorithms"] = {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
        try:
            cli.connect(**kw)
            tr = cli.get_transport()
            chan = tr.open_session(timeout=10)
            chan.get_pty(term="xterm", width=self.view.cols, height=self.view.rows)
            chan.exec_command(SSH_SOL_CMD[vendor])
        except Exception as e:
            try:
                cli.close()
            except Exception:
                pass
            raise OSError(str(e))
        self.proc = cli
        self._write = lambda d: chan.sendall(d)

        def _close():
            try:
                chan.close()
            finally:
                cli.close()
        self._close = _close
        self.reader = _ReaderThread(lambda: self._ssh_loop(chan))
        self.reader.start()

    def _ssh_loop(self, chan):
        code = None
        try:
            while True:
                data = chan.recv(65536)
                if not data:
                    break
                self._feed.emit(data)
        except Exception:
            pass
        finally:
            try:
                code = chan.recv_exit_status()
            except Exception:
                code = None
        self._done.emit(code)

    # ---- IPMI-SOL (pyghmi) ----
    def _start_sol(self, host, user, pw):
        def out(data):                           # wird vom pyghmi-Thread gerufen
            if isinstance(data, str):
                data = data.encode("latin-1", "replace")
            if isinstance(data, (bytes, bytearray)):
                self._feed.emit(bytes(data))
        try:
            sol = ipmiconsole.Console(bmc=host, userid=user, password=pw,
                                      iohandler=out, force=True)
        except Exception as e:
            raise OSError(str(e))
        self.proc = sol
        self._write = lambda d: sol.send_data(d)
        self._close = lambda: sol.close()
        self.reader = _ReaderThread(lambda: self._sol_loop(sol))
        self.reader.start()

    def _sol_loop(self, sol):
        try:
            sol.main_loop()
        except Exception:
            pass
        self._done.emit(None)

    # ---- gemeinsam ----
    def write(self, data):
        if self._write is not None:
            try:
                self._write(data)
            except Exception:
                pass

    def _finish(self, code):
        """Gegenseite hat beendet (aus dem Lese-Thread gemeldet)."""
        if self.proc is None:                    # schon per stop() getrennt
            return
        self.proc = None
        self._write = self._close = None
        self.on_end(code)

    def stop(self, ended=False):
        """Vom Benutzer getrennt: schließt die Verbindung, ohne on_end auszulösen."""
        self.proc = None
        if self._close:
            try:
                self._close()
            except Exception:
                pass
        self._write = self._close = None
        if self.reader:
            self.reader.wait(3000)
            self.reader = None
        if ended:
            self.on_end(None)


class _ReaderThread(QThread):
    """Führt eine blockierende Lese-/Sitzungsschleife im Hintergrund aus."""

    def __init__(self, target):
        super().__init__()
        self._target = target

    def run(self):
        self._target()




# ---------------------------------------------------------------------------------------- Web-Konsole
if WEBENGINE:
    class BmcPage(QWebEnginePage):
        """Akzeptiert das selbstsignierte Zertifikat nur des gewählten BMC und leitet Popups in eigene Reiter."""

        def __init__(self, profile, host, popup, parent=None):
            super().__init__(profile, parent)
            self.host, self.popup = QUrl(f"https://{host}").host(), popup
            self.certificateError.connect(self.cert)

        def cert(self, error):
            """Nur das Zertifikat des gewählten BMC annehmen – und nur, wenn es zum gemerkten Fingerabdruck
            passt (trust.py). Ein fremdes Zertifikat unter derselben Adresse wird abgelehnt."""
            if error.url().host() != self.host:
                error.rejectCertificate()
                return
            try:
                chain = error.certificateChain()
                der = bytes(chain[0].toDer()) if chain else b""
            except Exception:
                der = b""
            port = error.url().port(443)
            addr = self.host if port == 443 else f"{self.host}:{port}"
            known = set(trust.store().for_addr(addr).values())
            if der and known and fingerprint(der) not in known:
                error.rejectCertificate()
                return
            error.acceptCertificate()

        def createWindow(self, _type):
            return self.popup()




class ConsoleMixin:
    """Seite „UEFI-Konsole“ des Hauptfensters samt Monitor und Varianten-Schalter links oben."""

    def build_monitor(self):
        """Monitor mit Schalter für die Konsolen-Variante; steht im Hauptfenster links oben über dem Menü.

        Der Monitor zeigt Rauschen, solange nichts verbunden ist, sonst die Konsole als Miniatur; ein
        Klick öffnet sie in voller Größe. Der Schalter spiegelt die Auswahl „Anzeige“ der Konsolenseite
        (gleiche Reihenfolge: HTML5, Text, Browser) und wird in build_console damit verbunden."""
        self.monitor = MonitorWidget(
            self.console_source,
            T("Kein Signal. Ein Klick öffnet die UEFI-Konsole, dort lässt sich eine Verbindung herstellen."),
            T("Live-Vorschau der Server-Konsole. Ein Klick öffnet sie in voller Größe."))
        self.monitor.clicked.connect(self.console_enter)
        lack = " " + T("Benötigtes Paket fehlt, siehe Hinweis auf der Seite „UEFI-Konsole“.")
        self.con_switch = SegmentSwitch([
            ("HTML5", T("Bild: eingebettete HTML5-Konsole des BMC, Maus und Tastatur wie am Original.")
             + (lack if not WEBENGINE else ""), not WEBENGINE),
            (T("Text"), T("Text: serielle Konsole über IPMI-SOL oder SSH. Nur wenn im UEFI „Console Redirection“ aktiv ist.")
             + (lack if not pyte else ""), not pyte),
            ("Browser", T("Bild: BMC-Seite im Standardbrowser öffnen, ohne Auto-Anmeldung."), False)])
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.monitor)
        lay.addWidget(self.con_switch)
        return box

    def console_source(self):
        """Für den Monitor: (Widget, auch verdeckt abgreifbar, Beschriftung) der laufenden Konsole oder None."""
        ip = self.con_ip.currentText().strip()
        if self.con_serial.proc:
            return self.term, True, ip             # normales Widget: lässt sich auch unsichtbar abbilden
        if self.con_web_tabs and self.con_web_tabs.count():
            view = self.con_web_tabs.currentWidget()
            if view is not None:
                return view, False, ip             # Web-Ansicht zeichnet nur, solange sie sichtbar ist
        return None

    def console_enter(self):
        """Klick auf den Monitor: zur Konsolenseite. Steht eine Verbindung, direkt in die Vollansicht der
        Konsole (Einstellungen ausgeblendet), sonst mit den Einstellungen, um eine Verbindung herzustellen."""
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.con_page))
        src = self.console_source()
        self.console_panel_show(src is None)
        if src is not None:
            src[0].setFocus()

    def console_panel_show(self, show):
        """Blendet die Verbindungseinstellungen ein oder aus, damit die Konsole den ganzen Platz bekommt."""
        self.con_panel.setVisible(show)
        self.con_fold.setText(T("▲ Einstellungen ausblenden") if show else T("▼ Einstellungen einblenden"))

    def console_fullscreen_label(self):
        self.con_full.setText(T("✕ Vollbild beenden") if self.con_is_full else T("⛶ Vollbild"))

    def console_fs_bar(self, show):
        """Im Vollbild die Leiste (Trennen, Vollbild beenden, Werkzeuge) ein- oder ausklappen. Ausgeklappt ist
        nur der schmale Streifen oben zu sehen, die Konsole füllt den Rest des Bildschirms."""
        self.con_fs_bar_on = show
        for w in (self.con_disconnect, self.con_full, self.con_tools):
            w.setVisible(show)
        self.con_fs_handle.setText(T("▴ Leiste einklappen") if show else T("▾ Leiste einblenden (Vollbild beenden, Trennen, Tastatur …)"))
        self.con_fs_handle.setToolTip(T("Klappt die Leiste mit „Vollbild beenden“, „Trennen“ und den Werkzeugen ein oder aus."))
        self.term_scroll.refit()

    def console_fullscreen(self, on):
        """Vollbild: Das Fenster füllt den Bildschirm und die Konsole bekommt die ganze Fläche. Oben bleibt nur ein
        schmaler Streifen, der die Leiste (Trennen, Vollbild beenden, Werkzeuge) auf Klick ein- und ausklappt;
        bei der Textkonsole bleibt die Tastenleiste. Menü, Monitor, Statuszeile, Verbindungsangaben und Hinweise
        sind so lange ausgeblendet; beim Beenden kommt alles so zurück, wie es vorher sichtbar war. Ohne laufende
        Konsole geschieht nichts."""
        if on == self.con_is_full or (on and self.console_source() is None):
            return
        hidden = [self.con_panel, self.con_warn, self.con_copy, self.con_connect, self.con_start,
                  self.con_setup, self.con_fold, self.con_strip, self.con_head, self.con_status, self.nav_side, self.status]
        bar = [self.con_disconnect, self.con_full, self.con_tools]      # Leiste: im Vollbild ein- und ausklappbar
        if on:
            self.con_fs_saved = {w: not w.isHidden() for w in hidden + bar}
            frame_lay = self.con_frame.layout()
            self.con_fs_margins = [(lay.contentsMargins(), lay.spacing())
                                   for lay in (self.con_lay, self.main_lay, self.main_body, frame_lay)]
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.con_page))
            for w in hidden:
                w.setVisible(False)
            for lay in (self.con_lay, self.main_lay, self.main_body, frame_lay):      # keine Ränder: Konsole füllt alles
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(0)
            self.con_frame.setStyleSheet(FRAME_CSS + "QFrame#conframe { border: none; border-radius: 0; }")
            self.con_fs_handle.setVisible(True)
            self.console_fs_bar(self.con_fs_bar_on)
            self.con_fs_max = self.isMaximized()
            self.showFullScreen()
        else:
            if self.con_fs_max:
                self.showMaximized()
            else:
                self.showNormal()
            self.con_fs_handle.setVisible(False)
            for w, was_visible in self.con_fs_saved.items():
                w.setVisible(was_visible)
            self.con_fs_saved = {}
            frame_lay = self.con_frame.layout()
            for lay, (margins, spacing) in zip((self.con_lay, self.main_lay, self.main_body, frame_lay), self.con_fs_margins):
                lay.setContentsMargins(margins)
                lay.setSpacing(spacing)
            self.con_frame.setStyleSheet(FRAME_CSS)
        self.con_is_full = on
        self.console_fullscreen_label()
        self.term_scroll.auto_fit = on          # Textkonsole: Schrift an den Platz anpassen bzw. zurücksetzen
        self.term_scroll.refit()
        src = self.console_source()
        if src is not None:
            src[0].setFocus()

    def build_console(self):
        self.con_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.con_vendor.addItem(text, key)
        self.con_ip = QComboBox()
        self.con_ip.setEditable(True)
        self.con_ip.setToolTip(T("Adresse des BMC. Vorschläge aus der Serverliste („Neuware“), es geht aber jede\n"
                               "erreichbare Adresse."))
        self.con_pws = {}
        self.con_ip.activated.connect(self.console_pick_ip)
        self.con_ip.currentTextChanged.connect(self.console_pick_ip)     # Zugangsdaten folgen der Adresse automatisch
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip(T("Adressen aus der Serverliste („Neuware“) neu einlesen"))
        refresh.clicked.connect(self.console_refresh_ips)
        self.con_scan = QPushButton(T("🔍 Suchen …"))
        self.con_scan.setToolTip(T("Adresse unbekannt? Sucht im Netz nach BMCs (nur lesend, ohne Anmeldung) und trägt den\n"
                                   "gewählten Treffer hier ein."))
        self.con_scan.clicked.connect(lambda: self.open_netscan(True))
        iprow = QHBoxLayout()
        iprow.addWidget(self.con_ip, 1)
        iprow.addWidget(refresh)
        iprow.addWidget(self.con_scan)
        self.con_user = QLineEdit()
        self.con_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.con_pass = self._pw()
        self.con_mode = QComboBox()
        self.con_mode.addItem(T("Bild: Web-Konsole des BMC (eingebettet)"), "web")
        self.con_mode.addItem(T("Text: serielle Konsole (SOL/SSH)"), "serial")
        self.con_mode.addItem(T("Bild: im Standardbrowser öffnen"), "browser")
        self.con_mode.currentIndexChanged.connect(self.console_mode_changed)
        self.con_method = QComboBox()
        self.con_method.addItem(T("Automatisch"), "auto")
        self.con_method.addItem("IPMI-SOL", "ipmitool")
        self.con_method.addItem("SSH", "ssh")
        self.con_term = QComboBox()
        self.con_term.addItem("UTF-8 / VT-UTF8", False)
        self.con_term.addItem("ANSI (CP437)", True)
        self.con_size = QComboBox()
        for label, c, r in TERM_SIZES:
            self.con_size.addItem(label, (c, r))
        self.con_size.currentIndexChanged.connect(self.console_size_changed)

        box = QGroupBox(T("Verbindung"))
        f = QFormLayout(box)
        self.tip_row(f, T("Hersteller"), self.con_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den Hersteller\n"
                       "dieses einen Geräts selbst."))
        f.addRow(T("Adresse"), iprow)
        self.tip_row(f, T("Benutzer"), self.con_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.con_pass),
                     T("Wird nur für diese Verbindung verwendet und nirgends gespeichert."))
        self.tip_row(f, T("Anzeige"), self.con_mode,
                     T("Bild: echtes Konsolenbild mit Maus und Tastatur wie am Original (HTML5-Konsole des BMC).\n"
                       "Text: serielle Umleitung; geht nur, wenn im UEFI „Console Redirection“ aktiv ist.\n"
                       "Browser: öffnet die BMC-Seite im Standardbrowser, ohne Auto-Anmeldung."))
        self.con_serial_box = QWidget()
        sf = QFormLayout(self.con_serial_box)
        sf.setContentsMargins(0, 0, 0, 0)
        self.tip_row(sf, T("Verfahren"), self.con_method,
                     T("Automatisch: Supermicro über IPMI-SOL (pyghmi), Dell/HPE/Lenovo über SSH (paramiko), sonst IPMI-SOL.\n"
                       "IPMI über LAN muss im BMC aktiviert sein (bei Dell ab Werk aus)."))
        self.tip_row(sf, T("Terminaltyp"), self.con_term,
                     T("Muss zur Einstellung „Terminal Type“ im UEFI passen: VT100/VT-UTF8 → UTF-8,\n"
                       "ANSI → CP437 (Rahmenzeichen)."))
        self.tip_row(sf, T("Größe"), self.con_size,
                     T("Zeichen × Zeilen; muss zur Einstellung im UEFI passen (meist 80 × 24 oder 80 × 25)."))
        f.addRow(self.con_serial_box)

        self.con_connect = QPushButton(T("Verbinden"))
        self.con_connect.setMinimumHeight(30)
        self.con_connect.clicked.connect(self.console_connect)
        self.con_disconnect = QPushButton(T("Trennen"))
        self.con_disconnect.setEnabled(False)
        self.con_disconnect.clicked.connect(self.console_disconnect)
        self.con_start = QPushButton(T("Konsole starten"))
        self.con_start.setEnabled(False)
        self.con_start.setToolTip(T("Öffnet die Konsole des BMC in einem eigenen Reiter."))
        self.con_start.clicked.connect(self.console_goto)
        self.con_setup = QPushButton(T("Ins UEFI-Setup booten …"))
        self.con_setup.setToolTip(T("Merkt „beim nächsten Start ins UEFI-Setup“ vor und startet den Server hart neu\n"
                                    "(per Redfish). Nur mit Rückfrage."))
        self.con_setup.clicked.connect(self.console_boot_setup)
        self.con_status = QLabel()
        self.con_status.setWordWrap(True)
        self.con_fold = QPushButton()
        self.con_fold.setToolTip(T("Blendet die Verbindungsangaben aus, damit die Konsole den ganzen Platz bekommt."))
        self.con_fold.clicked.connect(lambda: self.console_panel_show(self.con_panel.isHidden()))
        self.con_is_full = False            # Vollbild aktiv (muss vor dem ersten console_busy stehen)
        self.con_fs_saved = {}              # Sichtbarkeit der im Vollbild versteckten Teile, zum Wiederherstellen
        self.con_fs_max = False
        self.con_fs_bar_on = False          # Leiste im Vollbild: aus = die Konsole bekommt die ganze Fläche
        self.con_fs_handle = QPushButton()  # schmaler Streifen oben im Vollbild, klappt die Leiste ein und aus
        self.con_fs_handle.setFlat(True)
        self.con_fs_handle.setFixedHeight(18)
        self.con_fs_handle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.con_fs_handle.setStyleSheet("QPushButton { background: #1c2430; color: #9fb0c3; border: 0; font-size: 11px; }"
                                         "QPushButton:hover { background: #2b3a4d; color: #ffffff; }")
        self.con_fs_handle.clicked.connect(lambda: self.console_fs_bar(not self.con_fs_bar_on))
        self.con_fs_handle.hide()
        self.con_full = QPushButton()
        self.con_full.setEnabled(False)
        self.con_full.setToolTip(T("Die Konsole füllt den ganzen Bildschirm. Beenden über den Streifen oben (Leiste einblenden)\n"
                                   "und dort „Vollbild beenden“, denn Esc und F11 gehören dem Server."))
        self.con_full.clicked.connect(lambda: self.console_fullscreen(not self.con_is_full))
        self.console_fullscreen_label()
        btns = QHBoxLayout()
        btns.addWidget(self.con_connect)
        btns.addWidget(self.con_disconnect)
        btns.addWidget(self.con_start)
        btns.addStretch()
        btns.addWidget(self.con_setup)
        btns.addWidget(self.con_full)
        btns.addWidget(self.con_fold)

        # Tastenleiste für die Textkonsole (F-Tasten fangen Desktop und Fenstermanager manchmal ab)
        self.con_keys = QWidget()
        kl = QHBoxLayout(self.con_keys)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(QLabel(T("Tasten:")))
        for label, seq in [("Esc", b"\x1b"), ("F1", b"\x1bOP"), ("F2", b"\x1bOQ"), ("F9", b"\x1b[20~"),
                           ("F10", b"\x1b[21~"), ("F11", b"\x1b[23~"), ("F12", b"\x1b[24~"),
                           ("Entf", b"\x1b[3~"), ("Esc+1", b"\x1b1"), ("Esc+2", b"\x1b2"), ("Esc+0", b"\x1b0")]:
            b = QPushButton(label)
            b.setFixedWidth(52)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, s=seq: self.console_send(s))
            kl.addWidget(b)
        kl.addStretch()
        self.con_keys.setToolTip(T("Sendet die Taste an den Server. „Esc+1“ usw. ersetzen F1 usw. auf Konsolen,\n"
                                   "die nur Escape-Folgen verstehen."))

        self.con_stack = QStackedWidget()
        self.con_web_tabs = None
        if WEBENGINE:
            self.con_profile = QWebEngineProfile(self)          # ohne Namen: nichts wird auf Platte gelegt
            self.con_profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            self.con_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
            self.con_web_tabs = QTabWidget()
            self.con_web_tabs.setTabsClosable(True)
            self.con_web_tabs.tabCloseRequested.connect(self.console_close_tab)
            self.con_stack.addWidget(self.con_web_tabs)
        else:
            self.con_stack.addWidget(self.console_note(T(
                "Die eingebettete Web-Konsole braucht QtWebEngine. Installation auf dem Rechner mit diesem Tool:")
                + "".join("\n    " + c for c in deps.install_commands([deps.Dep("PyQt6-WebEngine", "py")]))))
        self.term = TermView()
        self.term.send.connect(self.console_send)
        self.term_scroll = TermScroll(self.term)
        self.con_stack.addWidget(self.term_scroll)
        self.con_stack.addWidget(self.console_note(T(
            "„Verbinden“ öffnet die Anmeldeseite des BMC im Standardbrowser. Die Anmeldung erfolgt dort von Hand;\n"
            "die HTML5-Konsole wird im BMC über „Remote Console“ bzw. „Virtual Console“ gestartet.")))
        self.con_copy = QPushButton(T("Passwort kopieren (30 s)"))
        self.con_copy.clicked.connect(self.console_copy_pw)

        warn = QLabel(T("Achtung: Tasten gehen direkt an den Server. Dieser Reiter ändert von sich aus nichts, "
                        "alles Weitere geschieht in der UEFI-Oberfläche selbst."))
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #c9302c;")
        self.con_warn = warn
        # Verbindungsangaben und Paketliste lassen sich einklappen; Knöpfe, Status und Warnung bleiben sichtbar
        self.con_panel = QWidget()
        pan = QVBoxLayout(self.con_panel)
        pan.setContentsMargins(0, 0, 0, 0)
        pan.addWidget(box)
        self.con_fkeys_esc = False
        self.con_found_vendor = ""
        self.con_user_stop = False
        self.con_retries = 0
        self.con_since = None
        self.con_clock = QTimer(self)
        self.con_clock.setInterval(1000)
        self.con_clock.timeout.connect(self.console_tick)
        self.console_build_tools()
        self.console_build_frame()
        self.console_build_strip()
        self.con_osk = OnScreenKeyboard(i18n.LANG)
        self.con_osk.hide()
        self.con_feeder = KeyFeeder(self)
        self.con_feeder.key.connect(self.console_deliver)
        self.con_osk.key.connect(self.console_deliver)
        self.con_osk.keys.connect(lambda ks: self.con_feeder.add_keys(ks))
        self.con_osk.type_text.connect(lambda text, enter: self.con_feeder.add_text(text, enter))
        self.con_osk.fkeys_as_esc.connect(lambda on: setattr(self, "con_fkeys_esc", on))
        self.con_osk.layout_box.currentTextChanged.connect(lambda *_: self.console_save_state())
        lay = QVBoxLayout()
        self.con_lay = lay
        lay.addWidget(self.con_fs_handle)
        lay.addWidget(self.con_panel)
        lay.addLayout(btns)
        lay.addWidget(self.con_tools)
        lay.addWidget(self.con_strip)
        lay.addWidget(self.con_status)
        lay.addWidget(warn)
        lay.addWidget(self.con_keys)
        lay.addWidget(self.con_copy)
        lay.addWidget(self.con_frame, 1)
        lay.addWidget(self.con_osk)
        page = QWidget()
        page.setLayout(lay)
        self.con_page = page
        self.con_switch.changed.connect(self.con_mode.setCurrentIndex)
        self.console_panel_show(True)

        self.con_probe = self.con_boot = None
        self.con_serial = SerialSession(self.term, self.console_serial_ended)
        self.con_login = None            # Zustand der Auto-Anmeldung der eingebetteten Konsole
        self.con_timer = QTimer(self)
        self.con_timer.timeout.connect(self.console_login_tick)
        self.console_load_state()
        self.console_mode_changed()
        self.console_refresh_ips()
        for w in (self.new_user, self.new_pass):             # neuer Administrator oben geändert: Felder nachziehen
            w.textChanged.connect(lambda *_: self.console_pick_ip(only_done=True))
        return page

    @staticmethod
    def console_note(text):
        lab = QLabel(text)
        lab.setObjectName("connote")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lab

    # ---------- Werkzeugleiste, Statusleiste, Rahmen ----------
    def console_tool(self, text, tip, slot=None, checkable=False):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)        # der Fokus bleibt in der Konsole
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        if slot:
            (b.toggled if checkable else b.clicked).connect(slot)
        return b

    def console_build_tools(self):
        self.con_tools = QWidget()
        self.con_tools.setObjectName("contools")
        self.con_tools.setStyleSheet(TOOLS_CSS)
        h = QHBoxLayout(self.con_tools)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        self.tool_kbd = self.console_tool("⌨ " + T("Tastatur"), T("Bildschirmtastatur ein-/ausblenden: Strg/Alt/F-Tasten, "
                                          "Hersteller-Tasten (Setup, Boot-Menü) und „Text tippen“. Hilft, wenn der Desktop "
                                          "Tasten abfängt."), self.console_toggle_osk, True)
        self.tool_copy = self.console_tool("⧉ " + T("Kopieren"), T("Textkonsole: Markierung (mit der Maus ziehen) bzw. den "
                                           "ganzen Bildschirm in die Zwischenablage. Auch Strg+Umschalt+C."), self.console_copy_text)
        self.tool_paste = self.console_tool("⎘ " + T("Einfügen"), T("Textkonsole: Zwischenablage in den Server tippen "
                                            "(langsam, damit nichts verloren geht). Auch Strg+Umschalt+V oder mittlere Maustaste."),
                                            self.console_paste)
        self.tool_shot = self.console_tool("📷 " + T("Bildschirmfoto"), T("Aktuelles Konsolenbild als PNG speichern oder in "
                                           "die Zwischenablage legen."))
        menu = QMenu(self.tool_shot)
        menu.addAction(T("In Datei speichern …")).triggered.connect(lambda: self.console_snapshot(False))
        menu.addAction(T("In die Zwischenablage")).triggered.connect(lambda: self.console_snapshot(True))
        self.tool_shot.setMenu(menu)
        self.tool_shot.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tool_zoom_out = self.console_tool("A−", T("Schrift/Ansicht verkleinern (auch Strg+Mausrad)."),
                                               lambda: self.console_zoom(-1))
        self.tool_zoom_in = self.console_tool("A+", T("Schrift/Ansicht vergrößern (auch Strg+Mausrad)."),
                                              lambda: self.console_zoom(1))
        self.tool_reload = self.console_tool("⟳ " + T("Neu laden"), T("Bild-Konsole: Seite des BMC neu laden."),
                                             self.console_reload)
        self.tool_again = self.console_tool("↻ " + T("Neu verbinden"), T("Trennt und verbindet mit denselben Angaben "
                                            "erneut."), self.console_reconnect)
        self.con_theme = QComboBox()
        for key, name in theme_names():
            self.con_theme.addItem(name, key)
        self.con_theme.setToolTip(T("Farbschema der Textkonsole."))
        self.con_theme.currentIndexChanged.connect(self.console_theme_changed)
        self.con_auto = QCheckBox(T("Automatisch neu verbinden"))
        self.con_auto.setToolTip(T("Textkonsole: Reißt die Verbindung ab (z. B. weil der BMC beim Neustart die Sitzung "
                                   "beendet), wird nach 3 Sekunden neu verbunden, höchstens {0}-mal hintereinander.")
                                 .format(AUTO_RECONNECT_MAX))
        for w in (self.tool_kbd, self.tool_copy, self.tool_paste, self.tool_shot, self.tool_zoom_out,
                  self.tool_zoom_in, self.tool_reload, self.tool_again):
            h.addWidget(w)
        h.addSpacing(8)
        self.con_theme_label = QLabel(T("Farbe"))
        h.addWidget(self.con_theme_label)
        h.addWidget(self.con_theme)
        h.addStretch()
        h.addWidget(self.con_auto)
        self.term.copy_requested.connect(self.console_copy_text)
        self.term.paste_requested.connect(self.console_paste)

    def console_build_strip(self):
        self.con_strip = QFrame()
        self.con_strip.setObjectName("constrip")
        self.con_strip.setStyleSheet(STRIP_CSS)
        h = QHBoxLayout(self.con_strip)
        h.setContentsMargins(10, 4, 10, 4)
        self.con_led = QLabel("●")
        self.con_summary = QLabel()
        f = self.con_summary.font()
        f.setBold(True)
        self.con_summary.setFont(f)
        self.con_info = QLabel()
        self.con_time = QLabel()
        self.con_rx = QLabel()
        for lab in (self.con_info, self.con_time, self.con_rx):
            lab.setStyleSheet("color: palette(mid);")
        h.addWidget(self.con_led)
        h.addWidget(self.con_summary)
        h.addStretch()
        h.addWidget(self.con_info)
        h.addSpacing(14)
        h.addWidget(self.con_time)
        h.addSpacing(14)
        h.addWidget(self.con_rx)
        self.console_led("idle")

    def console_build_frame(self):
        self.con_frame = QFrame()
        self.con_frame.setObjectName("conframe")
        self.con_frame.setStyleSheet(FRAME_CSS)
        v = QVBoxLayout(self.con_frame)
        v.setContentsMargins(4, 0, 4, 4)
        v.setSpacing(0)
        self.con_head = QLabel(T("Keine Verbindung"))
        self.con_head.setObjectName("conhead")
        v.addWidget(self.con_head)
        v.addWidget(self.con_stack, 1)
        self.term_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

    def console_led(self, state, summary=""):
        """Zustandsanzeige der Konsole: idle (grau), busy (gelb), live (grün), error (rot)."""
        self.con_led.setStyleSheet(f"color: {LED_COLOR[state]}; font-size: 15pt;")
        self.con_summary.setText(summary or {"idle": T("Nicht verbunden"), "busy": T("Verbinde …"),
                                            "live": T("Verbunden"), "error": T("Fehler")}[state])
        self.con_frame.setProperty("state", state)
        self.con_frame.style().unpolish(self.con_frame)
        self.con_frame.style().polish(self.con_frame)
        if state == "live":
            if self.con_since is None:
                self.con_since = time.monotonic()
            self.con_clock.start()
        else:
            self.con_clock.stop()
            self.con_since = None
            self.con_time.clear()
            self.con_rx.clear()
            self.con_info.clear()
        if state in ("idle", "error"):
            self.con_head.setText(T("Keine Verbindung"))
        self.console_tools_update()

    def console_live_text(self, ip, how, vendor):
        """Kopfzeile des Rahmens und Zusammenfassung bei bestehender Verbindung. Die Verbindungsangaben klappen
        ein, damit die Konsole den Platz bekommt (der Knopf „Einstellungen einblenden“ holt sie zurück)."""
        self.console_panel_show(False)
        name = RedfishClient.NAME.get(vendor, vendor or "")
        self.con_head.setText(f"{ip}  ·  {how}" + (f"  ·  {name}" if name else ""))
        self.console_led("live", f"{ip} · {how}")

    def console_tick(self):
        if self.con_since is None:
            return
        sec = int(time.monotonic() - self.con_since)
        self.con_time.setText(f"⏱ {sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}")
        if self.con_serial.proc:
            n = self.term.rx_bytes
            self.con_rx.setText("↓ " + (f"{n} B" if n < 1024 else f"{n / 1024:.1f} kB"))
            self.con_info.setText(f"{self.term.cols}×{self.term.rows}")
        else:
            self.con_rx.clear()
            self.con_info.clear()

    def console_tools_update(self):
        """Welche Werkzeuge gerade etwas tun können (je nach Anzeige und Verbindung)."""
        if not (hasattr(self, "con_osk") and hasattr(self, "con_serial")):
            return                                      # Seite ist noch im Aufbau
        mode = self.con_mode.currentData()
        live = self.console_source() is not None
        serial = mode == "serial"
        web = mode == "web"
        for w in (self.tool_copy, self.tool_paste, self.con_theme, self.con_theme_label, self.con_auto):
            w.setVisible(serial)
        self.tool_reload.setVisible(web)
        self.tool_kbd.setVisible(mode != "browser")
        self.tool_shot.setVisible(mode != "browser")
        self.tool_zoom_out.setVisible(mode != "browser")
        self.tool_zoom_in.setVisible(mode != "browser")
        self.tool_again.setVisible(mode != "browser")
        self.tool_copy.setEnabled(live)
        self.tool_paste.setEnabled(live)
        self.tool_shot.setEnabled(live)
        self.tool_reload.setEnabled(live)
        self.tool_again.setEnabled(live)
        self.tool_kbd.setEnabled(live or self.tool_kbd.isChecked())
        self.con_osk.set_mode(mode)
        if not live and self.tool_kbd.isChecked():
            self.tool_kbd.setChecked(False)

    # ---------- Bildschirmtastatur, Tastenzustellung ----------
    def console_toggle_osk(self, on):
        self.con_osk.set_mode(self.con_mode.currentData() or "web")
        self.con_osk.set_vendor(self.con_found_vendor or self.con_vendor.currentData() or "")
        self.con_osk.setVisible(on)
        self.con_keys.setVisible(self.con_mode.currentData() == "serial" and not on)     # die Tastatur enthält diese Tasten schon

    def console_deliver(self, key, mods, text):
        """Eine Taste (Qt-Taste, Umschalttasten, Zeichen) zum Server: Text = als Bytes über SOL/SSH, Bild = als echtes
        Tastenereignis an die eingebettete Web-Konsole. Gibt zurück, ob sie angekommen sein kann."""
        if self.con_serial.proc:
            if self.con_fkeys_esc and not mods and key in FKEY_ESC:
                data = FKEY_ESC[key]
            else:
                data = encode_key(key, mods, text, self.term.cp437)
            if data:
                self.term.clear_selection()
                self.term.scroll_bottom()
                self.con_serial.write(data)
            return True
        if self.con_web_tabs and self.con_web_tabs.count():
            view = self.con_web_tabs.currentWidget()
            if view is not None:
                send_qt_key(view, key, mods, text)
                return True
        self.console_set_status(T("Nicht verbunden: Die Taste ist nirgends angekommen."), True)
        return False

    # ---------- Kopieren, Einfügen, Bildschirmfoto, Zoom ----------
    def console_copy_text(self):
        text = self.term.selected_text() or "\n".join(self.term.lines()).rstrip()
        if not text:
            self.console_set_status(T("Nichts zu kopieren."), True)
            return
        QApplication.clipboard().setText(text)
        self.console_set_status(T("{0} Zeichen in die Zwischenablage kopiert.").format(len(text)))

    def console_paste(self):
        if not self.con_serial.proc:
            self.console_set_status(T("Einfügen geht nur mit verbundener Textkonsole."), True)
            return
        text = QApplication.clipboard().text()
        if not text:
            self.console_set_status(T("Die Zwischenablage ist leer."), True)
            return
        lines = text.replace("\r\n", "\n").count("\n") + 1
        if lines > 1 or len(text) > 200:
            box = QMessageBox(QMessageBox.Icon.Question, T("Einfügen"),
                              T("{0} Zeichen in {1} Zeilen in den Server tippen?").format(len(text), lines), parent=self)
            box.setInformativeText(T("Jede Zeile endet mit Enter. Das lässt sich nicht zurücknehmen."))
            go = box.addButton(T("Tippen"), QMessageBox.ButtonRole.AcceptRole)
            no = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(no)
            box.exec()
            if box.clickedButton() is not go:
                return
        n = self.con_feeder.add_text(text)
        self.console_set_status(T("{0} Zeichen werden getippt …").format(n))

    def console_snapshot(self, to_clipboard):
        src = self.console_source()
        if src is None:
            self.console_set_status(T("Kein Konsolenbild vorhanden."), True)
            return
        pix = src[0].grab()
        if pix.isNull():
            self.console_set_status(T("Das Bild ließ sich nicht aufnehmen (Konsole muss sichtbar sein)."), True)
            return
        if to_clipboard:
            QApplication.clipboard().setPixmap(pix)
            self.console_set_status(T("Bildschirmfoto liegt in der Zwischenablage."))
            return
        name = f"konsole-{src[2].replace(':', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        path, _ = QFileDialog.getSaveFileName(self, T("Bildschirmfoto speichern"), name, "PNG (*.png)")
        if path:
            if pix.save(path, "PNG"):
                self.console_set_status(T("Gespeichert: {0}").format(path))
            else:
                self.console_set_status(T("Speichern fehlgeschlagen: {0}").format(path), True)

    def console_zoom(self, delta):
        if self.con_serial.proc or self.con_mode.currentData() == "serial":
            self.term_scroll.zoom(delta)
            self.console_save_state()
        elif self.con_web_tabs and self.con_web_tabs.count():
            view = self.con_web_tabs.currentWidget()
            view.setZoomFactor(min(3.0, max(0.25, view.zoomFactor() + 0.1 * delta)))

    def console_reload(self):
        if self.con_web_tabs and self.con_web_tabs.count():
            self.con_web_tabs.currentWidget().reload()

    def console_reconnect(self):
        """Trennt und verbindet mit den Angaben in den Feldern neu."""
        self.console_disconnect()
        self.console_connect()

    def console_theme_changed(self, *_):
        self.term.set_theme(self.con_theme.currentData())
        self.console_save_state()

    # ---------- Vorlieben ----------
    def console_load_state(self):
        try:
            st = json.loads(i18n.CONSOLE_STATE.read_text(encoding="utf-8"))
            if not isinstance(st, dict):
                st = {}
        except (OSError, ValueError):
            st = {}
        i = self.con_theme.findData(st.get("theme"))
        if i >= 0:
            self.con_theme.setCurrentIndex(i)
        self.term.set_theme(self.con_theme.currentData())
        pt = st.get("pt")
        if isinstance(pt, int) and TermScroll.MIN_PT <= pt <= TermScroll.MAX_PT:
            self.term_scroll.pt = pt
            self.term_scroll.refit()
        if st.get("osk_layout") in ("QWERTZ", "QWERTY", "AZERTY"):
            self.con_osk.layout_box.setCurrentText(st["osk_layout"])

    def console_save_state(self):
        if not hasattr(self, "con_osk"):
            return
        st = {"theme": self.con_theme.currentData(), "pt": self.term_scroll.pt,
              "osk_layout": self.con_osk.layout_name}
        try:
            i18n.write_private(i18n.CONSOLE_STATE, json.dumps(st))
        except OSError:
            pass

    # ---------- Bedienung der Maske ----------
    def console_refresh_ips(self):
        """Adressliste aus „Neuware“ neu aufbauen. Ohne bisherige Eingabe wird die erste fertig umgestellte Zeile
        (sonst die erste) vorgewählt; Benutzer und Passwort tragen sich dazu automatisch ein."""
        keep = self.con_ip.currentText()
        self.con_ip.blockSignals(True)
        self.con_ip.clear()
        self.con_pws = {}
        for ip, pw in self.reachable_rows():
            self.con_pws[ip] = pw
            self.con_ip.addItem(ip)
        for r in range(self.table.rowCount()):          # Adresse gerade umgestellt: auf die neue wechseln
            if self.is_done(r) and keep and keep == self.cell(r, 0):
                keep = self.cell(r, 2)
        if not keep and self.con_pws:
            keep = next((ip for ip in self.con_pws if self.row_login(ip)[0]), next(iter(self.con_pws)))
        if keep:
            self.con_ip.setEditText(keep)
        self.con_ip.blockSignals(False)
        self.console_pick_ip()

    def open_netscan(self, console):
        """Fenster „Server suchen“. console=True: Treffer lassen sich auf der Konsolenseite eintragen,
        sonst nur in die Serverliste übernehmen."""
        dlg = getattr(self, "netscan_dlg", None)
        if dlg is None or dlg.isHidden() or dlg.parent() is not self or getattr(dlg, "for_console", None) != console:
            if dlg is not None and dlg.isVisible():
                dlg.close()
            dlg = NetScanDialog(self, self.local_ifaces(), console=console)
            dlg.for_console = console
            dlg.use_in_console.connect(self.console_take_found)
            dlg.add_to_list.connect(self.netscan_to_list)
            self.netscan_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def console_take_found(self, addr, vendor):
        """Treffer der Netzsuche auf der Konsolenseite eintragen (Benutzer und Passwort bleiben, wie sie sind)."""
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.con_page))
        self.con_ip.setEditText(addr)
        idx = self.con_vendor.findData(vendor)
        self.con_vendor.setCurrentIndex(idx if idx >= 0 else 0)
        self.console_set_status(T("Adresse {0} übernommen. Benutzer und Passwort eintragen, dann „Verbinden“.").format(addr))
        self.con_pass.setFocus()

    def console_pick_ip(self, *_, only_done=False):
        """Übernimmt Benutzer und Passwort der Zeile aus „Neuware“ (nur in die Felder, gespeichert wird nichts).
        Fertig umgestellte Zeilen kennen nur noch den neuen Benutzer mit dem neuen Passwort."""
        ip = self.con_ip.currentText()
        if ip not in self.con_pws or not self.con_ip.isEnabled():
            return                                      # von Hand getippte Adresse oder laufende Verbindung: nichts anfassen
        user, pw = self.row_login(ip)
        if only_done and not user:
            return
        pw = pw or self.cur_pass.text()
        if user:
            self.con_user.setText(user)
            self.con_user_auto = user
        elif getattr(self, "con_user_auto", "") and self.con_user.text().strip() == self.con_user_auto:
            self.con_user.clear()                       # frühere automatische Vorgabe nicht auf andere Geräte mitnehmen
            self.con_user_auto = ""
        if pw:
            self.con_pass.setText(pw)

    def console_mode_changed(self, *_):
        mode = self.con_mode.currentData()
        self.con_stack.setCurrentIndex({"web": 0, "serial": 1, "browser": 2}[mode])
        self.con_serial_box.setVisible(mode == "serial")
        self.con_keys.setVisible(mode == "serial" and not (hasattr(self, "tool_kbd") and self.tool_kbd.isChecked()))
        self.con_copy.setVisible(mode == "browser")
        self.con_start.setVisible(mode == "web")
        self.con_switch.set_index(self.con_mode.currentIndex())     # Schalter links oben folgt der Auswahl
        self.console_tools_update()

    def console_size_changed(self, *_):
        if not self.con_serial.proc:
            c, r = self.con_size.currentData()
            self.term.set_size(c, r)

    def console_active(self):
        return bool(self.con_serial.proc) or bool(self.con_probe and self.con_probe.isRunning()) \
            or bool(self.con_boot and self.con_boot.isRunning()) or bool(self.con_web_tabs and self.con_web_tabs.count())

    def console_set_status(self, text, err=False):
        self.con_status.setText(text)
        self.con_status.setStyleSheet("color: #c9302c;" if err else "")

    def console_busy(self, busy):
        self.con_connect.setEnabled(not busy)
        self.con_disconnect.setEnabled(busy)
        for w in (self.con_vendor, self.con_ip, self.con_user, self.con_pass, self.con_mode, self.con_method,
                  self.con_term, self.con_size, self.con_setup, self.con_switch, self.con_scan):
            w.setEnabled(not busy)
        self.con_full.setEnabled(busy)          # Vollbild nur mit Verbindung
        self.console_tools_update()
        if not busy:
            self.console_fullscreen(False)      # Trennen, Fehler oder Ende der Sitzung: raus aus dem Vollbild

    def console_creds(self):
        ip = self.con_ip.currentText().strip()
        if not ip:
            self.console_set_status(T("Bitte eine Adresse eintragen."), True)
            return None
        nu, np_ = self.row_login(ip)
        if nu and self.con_user.text().strip() == nu and self.con_pass.text() != np_ \
                and self.con_pass.text() == self.con_pws.get(ip, self.con_pass.text()):
            self.con_pass.setText(np_)                  # neuer Benutzer, aber noch das Werks-Passwort der Zeile im Feld
        return ip, self.con_user.text().strip(), self.con_pass.text(), self.con_vendor.currentData() or ""

    def console_copy_pw(self):
        pw = self.con_pass.text()
        if not pw:
            return
        cb = QApplication.clipboard()
        cb.setText(pw)
        QTimer.singleShot(30000, lambda: cb.clear() if cb.text() == pw else None)
        self.console_set_status(T("Passwort liegt 30 Sekunden in der Zwischenablage."))

    # ---------- Verbinden ----------
    def console_connect(self, *_, auto=False):
        creds = self.console_creds()
        if not creds:
            return
        ip, _user, _pw, vendor = creds
        self.con_user_stop = False
        if not auto:
            self.con_retries = 0
        self.console_busy(True)
        self.console_led("busy", T("Verbinde {0} …").format(ip))
        self.console_set_status(T("Prüfe {0} …").format(ip))
        self.con_probe = ConsoleProbeThread(self, ip, vendor)
        self.con_probe.done.connect(self.console_probed)
        self.con_probe.start()

    def console_probed(self, vendor, tls):
        ip, user, pw, _ = self.console_creds() or ("", "", "", "")
        mode = self.con_mode.currentData()
        if vendor == "offline":
            self.console_fail(T("Gerät nicht erreichbar."))
            return
        if vendor not in RedfishClient.NAME:
            self.console_fail(T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)."))
            return
        user = user or RedfishClient.DEFAULT_USER[vendor]
        self.con_found_vendor = vendor
        self.con_osk.set_vendor(vendor)
        if mode == "web":
            self.console_open_web(ip, user, pw, vendor, tls)
        elif mode == "serial":
            self.console_open_serial(ip, user, pw, vendor)
        else:
            url = f"https://{ip}/"
            QDesktopServices.openUrl(QUrl(url))
            self.console_busy(False)
            self.console_led("idle")
            self.console_set_status(T("Im Browser geöffnet: {0}").format(url))

    def console_fail(self, text):
        self.console_busy(False)
        self.console_led("error", text)
        self.console_set_status(text, True)

    # ---------- Eingebettete Web-Konsole ----------
    def console_open_web(self, ip, user, pw, vendor, tls):
        if not WEBENGINE:
            self.console_fail(T("QtWebEngine fehlt, siehe Hinweis unten. Alternativ „Text“ oder „Standardbrowser“ wählen."))
            return
        if tls_blocks_chromium(tls):
            self.console_fail(T("Dieser BMC kann nur TLS 1.0/1.1 (z. B. iLO 3/4, iDRAC7/8 mit alter Firmware). "
                                "Die eingebettete Konsole geht damit nicht, bitte „Text“ wählen."))
            self.con_mode.setCurrentIndex(self.con_mode.findData("serial"))     # Schalter steht schon auf „Text“
            return
        self.console_close_all_tabs()
        view = self.console_new_view(ip)
        self.con_web_tabs.addTab(view, f"{ip}")
        self.con_web_tabs.tabBar().setTabButton(0, self.con_web_tabs.tabBar().ButtonPosition.RightSide, None)
        view.setUrl(QUrl(f"https://{ip}/"))
        self.con_login = {"user": user, "pw": pw, "vendor": vendor, "ip": ip, "tries": 0, "state": "wait",
                          "view": view}
        self.con_start.setEnabled(True)
        self.console_live_text(ip, T("Bild (HTML5)"), vendor)
        self.con_timer.start(1000)
        self.console_set_status(T("Verbunden mit {0}. Anmeldung läuft; danach bleibt die Verwaltungsseite des BMC offen.").format(ip))

    def console_new_view(self, ip):
        view = QWebEngineView()
        page = BmcPage(self.con_profile, ip, self.console_popup, view)
        view.setPage(page)
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        page.fullScreenRequested.connect(lambda r: r.accept())
        return view

    def console_popup(self):
        """Der BMC öffnet die Konsole oft in einem neuen Fenster: stattdessen als weiterer Reiter."""
        ip = self.con_login["ip"] if self.con_login else ""
        view = self.console_new_view(ip)
        i = self.con_web_tabs.addTab(view, T("Konsole"))
        self.con_web_tabs.setCurrentIndex(i)
        return view.page()

    def console_close_tab(self, i):
        if i == 0:
            return
        w = self.con_web_tabs.widget(i)
        self.con_web_tabs.removeTab(i)
        w.deleteLater()

    def console_close_all_tabs(self):
        self.con_timer.stop()
        self.con_login = None
        if self.con_web_tabs:
            while self.con_web_tabs.count():
                w = self.con_web_tabs.widget(0)
                self.con_web_tabs.removeTab(0)
                w.deleteLater()

    def console_login_tick(self):
        """Füllt die Anmeldeseite aus, sobald sie da ist (die Seiten der BMCs bauen sich nach dem Laden auf)."""
        st = self.con_login
        if not st or st["state"] != "wait":
            if st and st["state"] == "done":
                st["after"] = st.get("after", 0) + 1
                if st["after"] == 5:                    # Anmeldung hatte Zeit: Verwaltungsseite bleibt stehen
                    self.con_timer.stop()
                    self.console_set_status(T("Angemeldet. Die Verwaltungsseite des BMC ist offen; "
                                              "„Konsole starten“ öffnet die Konsole in einem eigenen Reiter."))
            return
        st["tries"] += 1
        if st["tries"] > 25:
            self.con_timer.stop()
            self.console_set_status(T("Anmeldeseite nicht erkannt. Bitte im Fenster von Hand anmelden."), True)
            return
        js = AUTOLOGIN_JS % (json.dumps(st["user"]), json.dumps(st["pw"]))
        st["view"].page().runJavaScript(js, lambda res, s=st: self.console_login_result(s, res))

    def console_login_result(self, st, res):
        if res == "done" and st["state"] == "wait":
            st["state"] = "done"

    def console_goto(self):
        st = self.con_login
        if not st:
            return
        path = CONSOLE_PATH.get(st["vendor"], "/")
        if path == "/":                                 # z. B. Lenovo: die Konsole startet man in der Verwaltungsseite
            self.con_web_tabs.setCurrentIndex(0)
            self.console_set_status(T("Auf der Verwaltungsseite „Remote Console“ wählen; die Konsole öffnet sich "
                                      "dann in einem eigenen Reiter."))
            return
        while self.con_web_tabs.count() > 1:            # alte Konsolen-Reiter schließen, genau einer bleibt
            self.console_close_tab(1)
        view = self.console_new_view(st["ip"])
        i = self.con_web_tabs.addTab(view, T("Konsole"))
        self.con_web_tabs.setCurrentIndex(i)
        view.setUrl(QUrl(f"https://{st['ip']}{path}"))

    # ---------- Textkonsole ----------
    def console_open_serial(self, ip, user, pw, vendor):
        if not pyte:
            self.console_fail(T("Es fehlt: {0}. Installation: {1}").format(
                "pyte", "; ".join(deps.install_commands([deps.Dep("pyte", "py")]))))
            return
        method = self.con_method.currentData()
        if method == "auto":
            method = "ipmitool" if vendor == "smc" or vendor not in SSH_SOL_CMD else "ssh"
        lacking = SerialSession.missing(method, vendor)
        if lacking:
            names = [p.replace("python-", "") for p in lacking]
            how = "; ".join(deps.install_commands([deps.Dep(n, "py") for n in names]))
            self.console_fail(T("Es fehlt: {0}. Installation: {1}").format(", ".join(names), how))
            return
        if method == "ssh" and vendor not in SSH_SOL_CMD:
            self.console_fail(T("Für diesen Hersteller gibt es kein SSH-Verfahren, bitte IPMI-SOL wählen."))
            return
        c, r = self.con_size.currentData()
        self.term.set_size(c, r)
        self.term.set_encoding(self.con_term.currentData())
        self.term.reset()
        try:
            self.con_serial.start(method, ip, user, pw, vendor)
        except OSError as e:
            self.console_fail(T('Start fehlgeschlagen: {0}').format(e))
            return
        self.term.set_live(True)
        self.term.setFocus()
        self.console_live_text(ip, T("Text ({0})").format("IPMI-SOL" if method == "ipmitool" else "SSH"), vendor)
        self.console_set_status(T("Textkonsole verbunden ({0}). In das schwarze Feld klicken, dann tippen. "
                                  "Bleibt es schwarz, ist im UEFI die „Console Redirection“ aus oder die Größe falsch.")
                                .format("IPMI-SOL" if method == "ipmitool" else "SSH"))

    def console_send(self, data):
        if self.con_serial.proc:
            self.con_serial.write(data)
            self.term.setFocus()

    def console_serial_ended(self, code):
        self.term.set_live(False)
        self.con_feeder.cancel()
        self.console_busy(False)
        self.console_panel_show(True)
        self.console_led("error" if code else "idle", T("Textkonsole beendet (Code {0}).").format(code))
        self.console_set_status(T("Textkonsole beendet (Code {0}).").format(code), bool(code))
        if self.con_auto.isChecked() and not self.con_user_stop and self.con_retries < AUTO_RECONNECT_MAX:
            self.con_retries += 1
            self.console_set_status(T("Verbindung weg. Neuer Versuch in 3 Sekunden ({0}/{1}) …")
                                    .format(self.con_retries, AUTO_RECONNECT_MAX))
            QTimer.singleShot(3000, self.console_auto_retry)

    def console_auto_retry(self):
        if self.con_auto.isChecked() and not self.con_user_stop and self.con_connect.isEnabled():
            self.console_connect(auto=True)

    # ---------- Trennen / Aufräumen ----------
    def console_disconnect(self):
        self.con_user_stop = True
        self.con_feeder.cancel()
        if self.con_serial.proc:
            self.con_serial.stop()
            self.term.set_live(False)
        self.console_close_all_tabs()
        self.con_start.setEnabled(False)
        self.console_busy(False)
        self.console_led("idle")
        self.console_panel_show(True)           # sonst stünde man vor einer leeren Vollansicht
        self.console_set_status(T("Getrennt."))

    # ---------- Ins UEFI-Setup booten ----------
    def console_boot_setup(self):
        creds = self.console_creds()
        if not creds:
            return
        ip, user, pw, vendor = creds
        box = QMessageBox(QMessageBox.Icon.Warning, T("Ins UEFI-Setup booten"),
                          T("Server {0} wird jetzt HART neu gestartet.").format(ip), parent=self)
        box.setInformativeText(T("Ein laufendes Betriebssystem wird ohne Herunterfahren beendet (wie die "
                                 "Reset-Taste), nicht gespeicherte Daten gehen verloren. Nur für Server ohne "
                                 "laufenden Betrieb verwenden."))
        go = box.addButton(T("Hart neu starten"), QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not go:
            return
        self.console_busy(True)
        self.con_disconnect.setEnabled(False)
        self.console_set_status(T("Sende an {0} …").format(ip))
        self.con_boot = BootSetupThread(self, ip, user, pw, vendor)
        self.con_boot.done.connect(self.console_booted)
        self.con_boot.start()

    def console_booted(self, ok, text):
        self.console_busy(False)
        self.console_set_status(text, not ok)

    def console_shutdown(self):
        """Beim Beenden: Sitzungen sauber schließen."""
        self.con_feeder.cancel()
        self.con_clock.stop()
        self.con_serial.stop()
        self.console_close_all_tabs()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)     # Seiten vor dem Profil freigeben
