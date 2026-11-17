"""BMC-Steward: ein gemeinsames Protokoll für das ganze Programm.

Hauptfenster und Testlabor zeigen dasselbe Protokoll. Es füllt sich schon beim Start des Programms und enthält
jede Aktion (Knöpfe, Haken, Auswahl, Seitenwechsel) sowie die Meldungen der Läufe. Zwei Stufen:

* **Info** (Standard): Aktionen und Ergebnisse.
* **Debug**: zusätzlich technische Einzelheiten (Umgebung, Fenstergröße, Pfade).

Alles bleibt im Speicher; die Textdatei mit dem Protokoll entsteht erst, wenn man „Protokoll“ drückt.
Passwörter gehören nie hinein: Eingabefelder werden nicht protokolliert, nur Beschriftungen von Knöpfen.
"""
import collections
import html
import re
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontDatabase
from PyQt6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QLabel, QMessageBox,
                             QRadioButton, QTabWidget, QTextEdit)

from .i18n import T

INFO, DEBUG = 20, 10
MAX_ENTRIES = 20000                     # ältere Zeilen fallen heraus, damit der Speicher nicht wächst
DEBUG_COLOR = "#8a8a8a"


class Entry:
    __slots__ = ("ts", "level", "text", "color", "bold")

    def __init__(self, level, text, color=None, bold=False):
        self.ts = time.time()
        self.level = level
        self.text = text
        self.color = color
        self.bold = bold


class AppLog(QObject):
    """Der Speicher des Protokolls. Ansichten hängen sich an die Signale."""
    added = pyqtSignal(object)          # Entry, schon nach Stufe gefiltert
    level_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._entries = collections.deque(maxlen=MAX_ENTRIES)
        self.level = INFO

    def add(self, level, text, color=None, bold=False):
        text = str(text)
        if not text.strip():
            return
        e = Entry(level, text, color, bold)
        self._entries.append(e)
        if level >= self.level:
            self.added.emit(e)

    def info(self, text, color=None, bold=False):
        self.add(INFO, text, color, bold)

    def debug(self, text, color=None, bold=False):
        self.add(DEBUG, text, color or DEBUG_COLOR, bold)

    def set_level(self, level):
        level = DEBUG if level <= DEBUG else INFO
        if level == self.level:
            return
        self.level = level
        self.level_changed.emit(level)

    def entries(self):
        """Alle Zeilen, die in der gewählten Stufe sichtbar sind."""
        return [e for e in self._entries if e.level >= self.level]

    def clear(self):
        self._entries.clear()
        self.level_changed.emit(self.level)         # Ansichten neu aufbauen

    def as_text(self):
        """Das Protokoll als Text für die Datei: Zeit, Stufe, Meldung (mehrzeilige Meldungen eingerückt)."""
        lines = []
        for e in self.entries():
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.ts))
            tag = "DEBUG" if e.level < INFO else "INFO "
            first, *rest = e.text.split("\n")
            lines.append(f"{stamp} {tag} {first}")
            lines += ["    " + r for r in rest]
        return "\n".join(lines)


LOG = AppLog()


def info(text, color=None, bold=False):
    LOG.info(text, color, bold)


def debug(text):
    LOG.debug(text)


class LogView(QTextEdit):
    """Zeigt das gemeinsame Protokoll; wird immer angezeigt, auch wenn es (noch) leer ist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        LOG.added.connect(self._add)
        LOG.level_changed.connect(self._rebuild)
        self._rebuild()

    @staticmethod
    def _html(e):
        style = "white-space:pre-wrap;" + (f"color:{e.color};" if e.color else "")
        if e.bold:
            style += "font-weight:bold;"
        return f'<span style="{style}">{html.escape(e.text)}</span>'

    def _add(self, e):
        self.append(self._html(e))

    def _rebuild(self, *_):
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            for e in LOG.entries():
                self.append(self._html(e))
        finally:
            self.setUpdatesEnabled(True)


class LevelCombo(QComboBox):
    """Schalter Info/Debug; alle Schalter im Programm stehen immer gleich."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.addItem(T("Info"), INFO)
        self.addItem(T("Debug"), DEBUG)
        self.setToolTip(T("Stufe des Protokolls. Info zeigt Aktionen und Ergebnisse, Debug zusätzlich technische "
                          "Einzelheiten."))
        self.setCurrentIndex(self.findData(LOG.level))
        self.activated.connect(self._chosen)
        LOG.level_changed.connect(self._sync)

    def _chosen(self, *_):
        LOG.set_level(self.currentData())
        LOG.info(T("Protokollstufe: {0}").format(self.currentText()))

    def _sync(self, level):
        self.setCurrentIndex(self.findData(level))


class LogLabel(QLabel):
    """Statuszeile: jede neue Meldung steht zusätzlich im Protokoll (gleiche Meldung hintereinander nur einmal)."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._last = ""

    def setText(self, text):
        super().setText(text)
        plain = re.sub(r"<[^>]+>", "", str(text)).strip()
        if plain and plain != self._last:
            self._last = plain
            LOG.info(plain)


def open_in_editor(parent):
    """Schreibt das Protokoll in eine Textdatei und öffnet sie im Standard-Editor. Gibt den Pfad zurück."""
    path = Path(tempfile.gettempdir()) / ("bmc-steward-" + time.strftime("%Y%m%d-%H%M%S") + ".log")
    try:
        path.write_text(LOG.as_text() + "\n", encoding="utf-8")
    except OSError as e:
        QMessageBox.warning(parent, T("Protokoll"), T('Datei nicht schreibbar:\n{0}').format(e))
        return None
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        QMessageBox.warning(parent, T("Protokoll"),
                            T("Kein Standardprogramm für Textdateien gefunden.\nDatei liegt unter {0}").format(path))
        return None
    LOG.debug(T("Protokoll geöffnet: {0}").format(path))
    return path


def _label(widget):
    text = widget.text() if hasattr(widget, "text") else ""
    text = (text or "").replace("&", "").strip()
    if not text:
        text = (widget.toolTip() or "").split("\n")[0].strip()
    return text


class ActionLogger(QObject):
    """Protokolliert jede Bedienung: Knopfdruck, Haken, Auswahl, Seitenwechsel. Eingaben in Textfeldern nicht."""

    def eventFilter(self, obj, ev):
        t = ev.type()
        if isinstance(obj, QAbstractButton) and obj.isEnabled():
            clicked = False
            if t == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                clicked = obj.rect().contains(ev.position().toPoint())
            elif t == QEvent.Type.KeyRelease and ev.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                    and not ev.isAutoRepeat() and obj.hasFocus():
                clicked = True
            if clicked:
                self._button(obj)
        return False

    def _button(self, b):
        label = _label(b)
        if not label:
            return
        if isinstance(b, (QCheckBox, QRadioButton)) or b.isCheckable():
            # Der neue Zustand steht erst nach dem Klick fest.
            QTimer.singleShot(0, lambda: LOG.info(
                T("Haken „{0}“: {1}").format(label, T("an") if b.isChecked() else T("aus"))))
        else:
            LOG.info(T("Knopf: {0}").format(label))


def watch(root):
    """Hängt sich an Auswahlfelder, Zahlfelder und Reiter unterhalb von root."""
    for cb in root.findChildren(QComboBox):
        if not isinstance(cb, LevelCombo):
            cb.activated.connect(lambda _i, cb=cb: LOG.info(T("Auswahl: {0}").format(cb.currentText())))
    for sp in root.findChildren(QAbstractSpinBox):
        sp.editingFinished.connect(lambda sp=sp: LOG.debug(T("Zahl geändert: {0}").format(sp.text())))
    for tw in root.findChildren(QTabWidget):
        tw.currentChanged.connect(lambda i, tw=tw: LOG.debug(T("Reiter: {0}").format(tw.tabText(i))))


_logger = None


def install(app):
    """Einmal pro Programm: die Bedienung des ganzen Programms protokollieren."""
    global _logger
    if _logger is None:
        _logger = ActionLogger(app)
        app.installEventFilter(_logger)
    return _logger
