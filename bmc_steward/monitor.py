"""BMC-Steward: Monitor links oben und Schalter für die Konsolen-Variante.

Der Monitor zeigt dezentes Schwarz-Weiß-Rauschen, solange keine Konsole verbunden ist. Sobald eine
Verbindung steht, erscheint deren Bild verkleinert im Monitor; ein Klick öffnet die Konsole in voller
Größe. Das Widget kennt die Konsole nicht selbst: Es fragt über eine Rückruffunktion, was gerade
anzuzeigen ist, und macht von dem Widget nur Bildschirmfotos (grab), es greift nirgends ein.
"""
import os
from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QTextOption)
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget
from .i18n import T


NOISE_W, NOISE_H = 96, 60       # Rauschbild; wird grob hochskaliert, das wirkt ruhiger als feines Korn
NOISE_MS = 70                   # ca. 14 Bilder pro Sekunde
GRAB_MS = 400                   # Konsolen-Miniatur: ca. 2,5 Bilder pro Sekunde reichen als Vorschau
# Grauwerte nur zwischen 14 und 70 von 255: dunkel und kontrastarm, damit es nicht ablenkt
_NOISE_LUT = bytes(14 + i * 56 // 255 for i in range(256))


class MonitorWidget(QWidget):
    """Kleiner Bildschirm mit Gehäuse. info() liefert (Widget, auch_verdeckt_abgreifbar, Beschriftung)
    der laufenden Konsole oder None, wenn nichts verbunden ist."""
    clicked = pyqtSignal()
    PAD, CHIN = 9, 22           # Rand um den Bildschirm, Kinn mit Leuchtpunkt und Beschriftung

    def __init__(self, info, tip_idle, tip_live, parent=None):
        super().__init__(parent)
        self._info = info
        self._tips = (tip_idle, tip_live)
        self._frame = None      # letztes Bild der Konsole (QPixmap)
        self._live = False      # Konsole verbunden
        self._fresh = False     # Bild stammt aus dem laufenden Abgriff, sonst Standbild
        self._caption = ""
        self._hover = False
        self._noise = self._make_noise()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(196)
        self.setToolTip(tip_idle)
        self.setAccessibleName(T("Monitor"))

    def sizeHint(self):
        return QSize(272, 196)

    # ---------- Bildquelle ----------
    @staticmethod
    def _make_noise():
        raw = os.urandom(NOISE_W * NOISE_H).translate(_NOISE_LUT)
        return QImage(raw, NOISE_W, NOISE_H, NOISE_W, QImage.Format.Format_Grayscale8).copy()

    def _tick(self):
        try:
            info = self._info()
        except RuntimeError:                    # Konsolen-Widget wird gerade abgebaut
            info = None
        live = info is not None
        if live != self._live:
            self._live = live
            self.setToolTip(self._tips[1] if live else self._tips[0])
        if not live:
            self._frame = None
            self._caption = ""
            self._set_interval(NOISE_MS)
            self._noise = self._make_noise()
            self.update()
            return
        widget, hidden_ok, caption = info
        self._caption = caption
        self._set_interval(GRAB_MS)
        # Eine verdeckte Web-Ansicht liefert kein neues Bild; dann bleibt das letzte als Standbild stehen
        self._fresh = False
        try:
            if widget.width() > 8 and widget.height() > 8 and (hidden_ok or widget.isVisible()):
                pm = widget.grab()
                if not pm.isNull():
                    self._frame = pm
                    self._fresh = True
        except RuntimeError:
            pass
        self.update()

    def _set_interval(self, ms):
        if self._timer.interval() != ms:
            self._timer.setInterval(ms)

    def showEvent(self, e):
        super().showEvent(e)
        self._tick()
        self._timer.start(NOISE_MS)

    def hideEvent(self, e):
        self._timer.stop()                      # unsichtbar kostet der Monitor nichts
        super().hideEvent(e)

    # ---------- Zeichnen ----------
    def _screen(self):
        return QRectF(self.PAD, self.PAD, self.width() - 2 * self.PAD, self.height() - self.PAD - self.CHIN)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = self.palette().highlight().color()
        marked = self._hover or self.hasFocus()
        p.setPen(QPen(accent if marked else QColor("#4a4f58"), 2 if marked else 1))
        p.setBrush(QColor("#202226"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 9, 9)

        scr = self._screen()
        clip = QPainterPath()
        clip.addRoundedRect(scr, 4, 4)
        p.save()
        p.setClipPath(clip)
        p.fillRect(scr, QColor(0, 0, 0))
        if self._live and self._frame is not None:
            pm = self._frame
            dpr = pm.devicePixelRatio() or 1.0
            w, h = pm.width() / dpr, pm.height() / dpr
            k = min(scr.width() / w, scr.height() / h)
            tw, th = w * k, h * k
            target = QRectF(scr.x() + (scr.width() - tw) / 2, scr.y() + (scr.height() - th) / 2, tw, th)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawPixmap(target, pm, QRectF(pm.rect()))
        else:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(scr, self._noise)
        sheen = QLinearGradient(scr.topLeft(), scr.bottomLeft())    # leichter Glasglanz oben
        sheen.setColorAt(0.0, QColor(255, 255, 255, 22))
        sheen.setColorAt(0.45, QColor(255, 255, 255, 0))
        p.fillRect(scr, QBrush(sheen))
        p.restore()

        # Kinn: Beschriftung links, Leuchtpunkt rechts (grün = live, gelb = Standbild, grau = kein Signal)
        chin = QRectF(self.PAD, self.height() - self.CHIN, self.width() - 2 * self.PAD, self.CHIN)
        if not self._live:
            text, led = T("kein Signal"), QColor("#5b616b")
        elif self._fresh:
            text, led = self._caption, QColor("#34d399")
        else:
            text, led = (self._caption + " · " + T("Standbild")).strip(" ·"), QColor("#f0ad4e")
        font = QFont(self.font())
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(6.5, font.pointSizeF() - 1.5))
        p.setFont(font)
        p.setPen(QColor("#9aa0aa"))
        room = int(chin.width() - 18)
        opt = QTextOption(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        opt.setWrapMode(QTextOption.WrapMode.NoWrap)
        p.drawText(QRectF(chin.x(), chin.y(), room, chin.height()),
                   QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, room), opt)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(led)
        p.drawEllipse(QRectF(chin.right() - 8, chin.center().y() - 3, 6, 6))

    # ---------- Bedienung ----------
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class SegmentSwitch(QWidget):
    """Schalter aus nebeneinanderliegenden Knöpfen, von denen genau einer gewählt ist.
    items: Liste aus (Beschriftung, Tooltip, fehlt); „fehlt“ färbt den Knopf rot (Zusatzpaket nicht installiert).
    changed(Index) kommt nur bei Klicks des Benutzers, set_index() löst nichts aus."""
    changed = pyqtSignal(int)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setObjectName("seg")
        self.setStyleSheet(
            "#seg QPushButton { padding: 5px 4px; border: 1px solid palette(mid); border-radius: 0;"
            " background: palette(button); }"
            "#seg QPushButton[seg_pos='first'] { border-top-left-radius: 5px; border-bottom-left-radius: 5px; }"
            "#seg QPushButton[seg_pos='last'] { border-top-right-radius: 5px; border-bottom-right-radius: 5px; }"
            "#seg QPushButton[missing='true'] { color: #c9302c; }"
            "#seg QPushButton:checked { background: palette(highlight); color: palette(highlighted-text);"
            " font-weight: bold; }"
            "#seg QPushButton:disabled { color: palette(mid); }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = []
        last = len(items) - 1
        for i, (label, tip, missing) in enumerate(items):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setProperty("seg_pos", "first" if i == 0 else "last" if i == last else "mid")
            b.setProperty("missing", "true" if missing else "false")
            self._group.addButton(b, i)
            self._buttons.append(b)
            lay.addWidget(b)
        if self._buttons:
            self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self.changed)

    def index(self):
        return self._group.checkedId()

    def set_index(self, i):
        if 0 <= i < len(self._buttons):
            self._buttons[i].setChecked(True)
