"""BMC-Steward: UEFI-Konsole (eingebettete Web-Konsole, Text-Konsole SOL/SSH, externer Browser)."""
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QStyledItemDelegate, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)
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
from . import i18n
from .i18n import T
from .core import WINDOWS
from .redfish import RedfishClient
from .monitor import MonitorWidget, SegmentSwitch





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
        try:
            rc = RedfishClient(dry=False)
            v = self.vendor or rc.vendor(self.ip)
            if v not in ("dell", "hpe", "smc", "lenovo"):
                self.done.emit(False, T("Dieser Hersteller bzw. diese Generation kann das nicht per Redfish. "
                                        "Bitte im UEFI-Bild F2/F9/Entf selbst drücken."))
                return
            user = self.user or rc.DEFAULT_USER[v]
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




# ---------------------------------------------------------------------------------------- Textkonsole
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


ANSI_COLORS = {"black": "#000000", "red": "#cd0000", "green": "#00cd00", "brown": "#cdcd00", "blue": "#3050ff",
               "magenta": "#cd00cd", "cyan": "#00cdcd", "white": "#e5e5e5", "brightblack": "#7f7f7f",
               "brightred": "#ff3030", "brightgreen": "#30ff30", "brightbrown": "#ffff30",
               "brightblue": "#6080ff", "brightmagenta": "#ff30ff", "brightcyan": "#30ffff",
               "brightwhite": "#ffffff"}




class TermView(QWidget):
    """Kleines Terminal (VT100/ANSI) auf Basis von pyte. Tasten gehen als Bytes an send(); Bild nur zur Anzeige."""
    send = pyqtSignal(bytes)

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
        self.set_size(80, 24)

    def set_size(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen = pyte.Screen(cols, rows) if pyte else None
        self.stream = pyte.Stream(self.screen) if pyte else None
        self.setFixedSize(cols * self.cw + 2, rows * self.ch + 2)
        self.update()

    def set_encoding(self, cp437):
        self.cp437 = cp437
        self.dec = codecs.getincrementaldecoder("cp437" if cp437 else "utf-8")("replace")

    def reset(self):
        self.set_size(self.cols, self.rows)
        self.set_encoding(self.cp437)

    def feed(self, data):
        if self.stream:
            self.stream.feed(self.dec.decode(data))
            self.update()

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
        bg0, fg0 = QColor("#000000"), QColor("#c8c8c8")
        p.fillRect(self.rect(), bg0)
        if not self.screen:
            return
        p.setFont(self.font())
        asc = self.fm.ascent()
        cur = self.screen.cursor
        for y in range(self.rows):
            line = self.screen.buffer[y]
            for x in range(self.cols):
                c = line[x]
                fg, bg = self._color(c.fg, fg0), self._color(c.bg, bg0)
                if c.reverse:
                    fg, bg = bg, fg
                r = QRect(1 + x * self.cw, 1 + y * self.ch, self.cw, self.ch)
                if bg != bg0:
                    p.fillRect(r, bg)
                if c.data.strip():
                    f = QFont(self.font())
                    f.setBold(bool(c.bold))
                    f.setUnderline(bool(c.underscore))
                    p.setFont(f)
                    p.setPen(fg)
                    p.drawText(r.x(), r.y() + asc, c.data)
        if self.live and not cur.hidden and self.hasFocus():
            p.fillRect(QRect(1 + cur.x * self.cw, 1 + cur.y * self.ch, self.cw, self.ch), QColor(200, 200, 200, 110))
        if not self.hasFocus():
            p.setPen(QColor(120, 120, 120))
            p.drawRect(0, 0, self.width() - 1, self.height() - 1)

    def focusNextPrevChild(self, _next):
        return False                    # Tab gehört dem UEFI, nicht der Fokusreihenfolge

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)

    def mousePressEvent(self, e):
        self.setFocus()

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:
            e.accept()                  # Strg+Buchstabe usw. nicht als Programmkürzel abfangen
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if not self.live:
            return
        key = e.key()
        if key in KEYMAP:
            self.send.emit(KEYMAP[key])
            return
        mods = e.modifiers()
        text = e.text()
        if mods & Qt.KeyboardModifier.ControlModifier and Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value:
            self.send.emit(bytes([key - Qt.Key.Key_A.value + 1]))
        elif text and text.isprintable():
            self.send.emit(text.encode("cp437", "replace") if self.cp437 else text.encode("utf-8"))




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
            if error.url().host() == self.host:
                error.acceptCertificate()
            else:
                error.rejectCertificate()

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

    def build_console(self):
        self.con_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.con_vendor.addItem(text, key)
        self.con_ip = QComboBox()
        self.con_ip.setEditable(True)
        self.con_ip.setToolTip(T("Adresse des BMC. Vorschläge aus der Serverliste („Neuware“), es geht aber jede\n"
                               "erreichbare Adresse."))
        self.con_ip.activated.connect(self.console_pick_ip)
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip(T("Adressen aus der Serverliste („Neuware“) neu einlesen"))
        refresh.clicked.connect(self.console_refresh_ips)
        iprow = QHBoxLayout()
        iprow.addWidget(self.con_ip, 1)
        iprow.addWidget(refresh)
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

        pk = QGroupBox(T("Benötigte Pakete"))
        pl = QVBoxLayout(pk)
        row = QHBoxLayout()
        pkgs = [("QtWebEngine", WEBENGINE, "python-pyqt6-webengine"),
                ("pyte", bool(pyte), "python-pyte"),
                ("paramiko", paramiko is not None, "python-paramiko"),
                ("pyghmi", ipmiconsole is not None, "python-pyghmi")]
        lacking = []
        for name, ok, pkg in pkgs:
            lab = QLabel(("✔ " if ok else "❗ ") + name)
            lab.setStyleSheet("color: #2e9e4f; font-weight: bold;" if ok else "color: #c9302c; font-weight: bold;")
            lab.setToolTip(T("installiert") if ok else T("nicht installiert, Paket: {0}").format(pkg))
            row.addWidget(lab)
            if not ok:
                lacking.append(pkg)
        row.addStretch()
        pl.addLayout(row)
        if lacking:
            if WINDOWS:
                pip = {"python-pyqt6-webengine": "PyQt6-WebEngine", "python-pyte": "pyte",
                       "python-paramiko": "paramiko", "python-pyghmi": "pyghmi"}
                cmd = "    pip install " + " ".join(pip.get(p, p) for p in lacking)
            else:
                cmd = "    sudo pacman -S " + " ".join(lacking) + "      (Arch/CachyOS)"
            hint = QLabel(T("Fehlendes nachinstallieren, auf dem Rechner mit diesem Tool (Terminal):") + "\n" + cmd)
            hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            pl.addWidget(hint)
        pk.setToolTip(T("✔ installiert, ❗ fehlt. Je nach gewähltem Weg werden nicht alle gebraucht:\n"
                        "Bild: QtWebEngine · Text: pyte + pyghmi (Supermicro-SOL) bzw. paramiko (SSH, Dell/HPE/Lenovo)."))

        self.con_connect = QPushButton(T("Verbinden"))
        self.con_connect.setMinimumHeight(30)
        self.con_connect.clicked.connect(self.console_connect)
        self.con_disconnect = QPushButton(T("Trennen"))
        self.con_disconnect.setEnabled(False)
        self.con_disconnect.clicked.connect(self.console_disconnect)
        self.con_start = QPushButton(T("Konsole starten"))
        self.con_start.setEnabled(False)
        self.con_start.setToolTip(T("Springt nach der Anmeldung zur Konsolen-Seite des BMC."))
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
        btns = QHBoxLayout()
        btns.addWidget(self.con_connect)
        btns.addWidget(self.con_disconnect)
        btns.addWidget(self.con_start)
        btns.addStretch()
        btns.addWidget(self.con_setup)
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
                + ("\n    pip install PyQt6-WebEngine" if WINDOWS else
                   "\n    sudo pacman -S python-pyqt6-webengine      (Arch/CachyOS)\n"
                   "    pip install PyQt6-WebEngine                (sonst)")))
        self.term = TermView()
        self.term.send.connect(self.console_send)
        self.term_scroll = QScrollArea()
        self.term_scroll.setWidget(self.term)
        self.term_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.term_scroll.setWidgetResizable(False)
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
        # Verbindungsangaben und Paketliste lassen sich einklappen; Knöpfe, Status und Warnung bleiben sichtbar
        self.con_panel = QWidget()
        pan = QVBoxLayout(self.con_panel)
        pan.setContentsMargins(0, 0, 0, 0)
        pan.addWidget(box)
        pan.addWidget(pk)
        lay = QVBoxLayout()
        lay.addWidget(self.con_panel)
        lay.addLayout(btns)
        lay.addWidget(self.con_status)
        lay.addWidget(warn)
        lay.addWidget(self.con_keys)
        lay.addWidget(self.con_copy)
        lay.addWidget(self.con_stack, 1)
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
        self.console_mode_changed()
        self.console_refresh_ips()
        return page

    @staticmethod
    def console_note(text):
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lab

    # ---------- Bedienung der Maske ----------
    def console_refresh_ips(self):
        keep = self.con_ip.currentText()
        self.con_ip.clear()
        self.con_pws = {}
        for ip, pw in self.reachable_rows():
            self.con_ip.addItem(ip)
            self.con_pws[ip] = pw
        if keep:
            self.con_ip.setEditText(keep)

    def console_pick_ip(self, *_):
        """Übernimmt das Passwort der Zeile aus „Neuware“ (nur im Feld, gespeichert wird nichts)."""
        pw = self.con_pws.get(self.con_ip.currentText(), "") or self.cur_pass.text()
        if pw:
            self.con_pass.setText(pw)

    def console_mode_changed(self, *_):
        mode = self.con_mode.currentData()
        self.con_stack.setCurrentIndex({"web": 0, "serial": 1, "browser": 2}[mode])
        self.con_serial_box.setVisible(mode == "serial")
        self.con_keys.setVisible(mode == "serial")
        self.con_copy.setVisible(mode == "browser")
        self.con_start.setVisible(mode == "web")
        self.con_switch.set_index(self.con_mode.currentIndex())     # Schalter links oben folgt der Auswahl

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
                  self.con_term, self.con_size, self.con_setup, self.con_switch):
            w.setEnabled(not busy)

    def console_creds(self):
        ip = self.con_ip.currentText().strip()
        if not ip:
            self.console_set_status(T("Bitte eine Adresse eintragen."), True)
            return None
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
    def console_connect(self):
        creds = self.console_creds()
        if not creds:
            return
        ip, _user, _pw, vendor = creds
        self.console_busy(True)
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
        if mode == "web":
            self.console_open_web(ip, user, pw, vendor, tls)
        elif mode == "serial":
            self.console_open_serial(ip, user, pw, vendor)
        else:
            url = f"https://{ip}/"
            QDesktopServices.openUrl(QUrl(url))
            self.console_busy(False)
            self.console_set_status(T("Im Browser geöffnet: {0}").format(url))

    def console_fail(self, text):
        self.console_busy(False)
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
        self.con_timer.start(1000)
        self.console_set_status(T("Verbunden mit {0}. Anmeldung läuft; danach öffnet sich die Konsole.").format(ip))

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
                if st["after"] == 5:                    # Anmeldung hatte Zeit: zur Konsole weiterspringen
                    self.console_goto()
                    self.con_timer.stop()
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
        self.con_web_tabs.setCurrentIndex(0)
        st["view"].setUrl(QUrl(f"https://{st['ip']}{path}"))

    # ---------- Textkonsole ----------
    def console_open_serial(self, ip, user, pw, vendor):
        if not pyte:
            self.console_fail(T("Für die Textkonsole fehlt „pyte“: pip install pyte "
                                "(unter Arch/CachyOS: sudo pacman -S python-pyte)"))
            return
        method = self.con_method.currentData()
        if method == "auto":
            method = "ipmitool" if vendor == "smc" or vendor not in SSH_SOL_CMD else "ssh"
        lacking = SerialSession.missing(method, vendor)
        if lacking:
            pip = {"python-paramiko": "paramiko", "python-pyghmi": "pyghmi"}
            names = [pip.get(p, p) for p in lacking] if WINDOWS else lacking
            how = ("pip install " if WINDOWS else "sudo pacman -S ") + " ".join(names)
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
        self.term.live = True
        self.term.setFocus()
        self.console_set_status(T("Textkonsole verbunden ({0}). In das schwarze Feld klicken, dann tippen. "
                                  "Bleibt es schwarz, ist im UEFI die „Console Redirection“ aus oder die Größe falsch.")
                                .format("IPMI-SOL" if method == "ipmitool" else "SSH"))

    def console_send(self, data):
        if self.con_serial.proc:
            self.con_serial.write(data)
            self.term.setFocus()

    def console_serial_ended(self, code):
        self.term.live = False
        self.term.update()
        self.console_busy(False)
        self.console_panel_show(True)
        self.console_set_status(T("Textkonsole beendet (Code {0}).").format(code), bool(code))

    # ---------- Trennen / Aufräumen ----------
    def console_disconnect(self):
        if self.con_serial.proc:
            self.con_serial.stop()
            self.term.live = False
            self.term.update()
        self.console_close_all_tabs()
        self.con_start.setEnabled(False)
        self.console_busy(False)
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
        self.con_serial.stop()
        self.console_close_all_tabs()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)     # Seiten vor dem Profil freigeben
