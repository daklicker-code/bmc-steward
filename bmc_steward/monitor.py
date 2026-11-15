"""BMC-Steward: Monitor links oben und Schalter für die Konsolen-Variante.

Der Monitor zeigt dezentes Schwarz-Weiß-Rauschen, solange keine Konsole verbunden ist. Sobald eine
Verbindung steht, erscheint deren Bild verkleinert im Monitor; ein Klick öffnet die Konsole in voller
Größe. Das Widget kennt die Konsole nicht selbst: Es fragt über eine Rückruffunktion, was gerade
anzuzeigen ist, und macht von dem Widget nur Bildschirmfotos (grab), es greift nirgends ein.
"""
import os
from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient, QTextOption)
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget
from .i18n import T


NOISE_W, NOISE_H = 96, 60       # Rauschbild; wird grob hochskaliert, das wirkt ruhiger als feines Korn
NOISE_MS = 70                   # ca. 14 Bilder pro Sekunde
GRAB_MS = 400                   # Konsolen-Miniatur: ca. 2,5 Bilder pro Sekunde reichen als Vorschau
# Grauwerte nur zwischen 12 und 40 von 255: sehr dunkel und kontrastarm, damit es nicht ablenkt
_NOISE_LUT = bytes(12 + i * 28 // 255 for i in range(256))


# ---------------------------------------------------------------------------------------- Logo-Aufbau
# Gleiche Formen wie das Markenzeichen in branding.py (Koordinaten im 120er-Raster), hier als Pfade, damit sich
# jedes Teil einzeln aufbauen lässt: erst die Kugel, dann Leiterbahnen, Chip und Anschlüsse, zuletzt das Häkchen.
# Ein Durchlauf dauert CYCLE_MS und wiederholt sich: Aufbau, kurz stehen (mit grünem Schein), Abbau, Pause.
CYCLE_MS = 8000                 # alle 8 Sekunden beginnt der Aufbau von vorn
LOGO_MS = 4000                  # Dauer des Aufbaus
HOLD_MS = 1800                  # fertiges Logo bleibt stehen; in dieser Zeit klingt der Schein aus
UNBUILD_MS = 1700               # Dauer des Abbaus (rückwärts: das Häkchen geht zuerst)
LOGO_SIZE = 88                  # Kantenlänge des Logos im Bildschirm (zentriert)
CHECK_START = 0.90              # ab hier (Anteil von LOGO_MS) zeichnet sich das Häkchen
GLOW_FADE_MS = HOLD_MS          # so lange klingt der grüne Schein nach dem fertigen Häkchen aus


def _line(x1, y1, x2, y2):
    path = QPainterPath(QPointF(x1, y1))
    path.lineTo(QPointF(x2, y2))
    return path


_SPHERE_C, _SPHERE_R = QPointF(60, 63), 45
_SPHERE_RECT = QRectF(_SPHERE_C.x() - _SPHERE_R, _SPHERE_C.y() - _SPHERE_R, 2 * _SPHERE_R, 2 * _SPHERE_R)
_SPHERE = QPainterPath()                    # Umriss beginnt oben und läuft gegen den Uhrzeigersinn einmal herum
_SPHERE.arcMoveTo(_SPHERE_RECT, 90)
_SPHERE.arcTo(_SPHERE_RECT, 90, 360)
_CHIP = QPainterPath()
_CHIP.addRoundedRect(QRectF(46, 50, 28, 28), 5, 5)
_CHECK = QPainterPath(QPointF(53, 64))
_CHECK.lineTo(QPointF(58, 69))
_CHECK.lineTo(QPointF(68, 58))
_TRACES = [_line(32, 44, 44, 44), _line(76, 44, 88, 44), _line(32, 82, 44, 82), _line(76, 82, 88, 82)]
_DOTS = [(32, 44), (88, 44), (32, 82), (88, 82)]
_PINS = [_line(x, 45, x, 50) for x in (51, 60, 69)] + [_line(x, 78, x, 83) for x in (51, 60, 69)]


def _ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def logo_stages(t):
    """Fortschritt (0..1) jedes Logo-Teils zum Zeitpunkt t (0..1 der Gesamtdauer). Das Häkchen beginnt erst
    bei 90 % und ist damit immer das Letzte."""
    def stage(a, b):
        return _ease((t - a) / (b - a))
    return {"sphere": stage(0.00, 0.35), "fill": stage(0.25, 0.45), "traces": stage(0.40, 0.55),
            "dots": stage(0.52, 0.62), "chip": stage(0.58, 0.76), "pins": stage(0.74, 0.88),
            "check": stage(CHECK_START, 1.00)}


def logo_progress(ms):
    """Aufbaustand (0..1) des Logos, ms nach Beginn des Durchlaufs; wiederholt sich alle CYCLE_MS.
    Aufbau, Stehen, Abbau (derselbe Weg rückwärts), danach eine kurze Pause ohne Logo."""
    ms %= CYCLE_MS
    if ms < LOGO_MS:
        return ms / LOGO_MS
    ms -= LOGO_MS
    if ms < HOLD_MS:
        return 1.0
    ms -= HOLD_MS
    if ms < UNBUILD_MS:
        return 1.0 - ms / UNBUILD_MS
    return 0.0


def glow_strength(ms):
    """Stärke (0..1) des grünen Scheins ums Logo, ms nach Beginn des Durchlaufs: Er wächst zügig, während sich das
    Häkchen zeichnet, ist mit dem fertigen Häkchen am hellsten und klingt danach langsam aus."""
    ms %= CYCLE_MS
    start = CHECK_START * LOGO_MS
    if ms <= start:
        return 0.0
    if ms <= LOGO_MS:
        return _ease((ms - start) / (LOGO_MS - start))
    x = (ms - LOGO_MS) / GLOW_FADE_MS
    if x >= 1:
        return 0.0
    return (1 - x) ** 2                         # erst schneller, dann immer sanfter


def _stroke(p, path, color, width, frac):
    """Zeichnet die ersten frac (0..1) der Linie; gestrichelt mit einer Strichlänge, die dem Teil entspricht."""
    if frac <= 0:
        return
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if frac < 1:
        full = path.length() / width            # Strichmuster zählt in Stiftbreiten
        pen.setDashPattern([max(frac * full, 0.001), full * 2])
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _draw_glow(p, g):
    """Grüner Schein um die Kugel (Logo-Koordinaten), g = Stärke 0..1."""
    if g <= 0:
        return
    halo = QRadialGradient(_SPHERE_C, 80)
    halo.setColorAt(0.0, QColor(52, 211, 153, int(120 * g)))
    halo.setColorAt(0.55, QColor(52, 211, 153, int(55 * g)))
    halo.setColorAt(1.0, QColor(52, 211, 153, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(halo))
    p.drawEllipse(_SPHERE_C, 80, 80)
    for width, alpha in ((26, 28), (17, 45), (10, 70)):    # weich auslaufender Rand an der Kugel entlang
        pen = QPen(QColor(52, 211, 153, int(alpha * g)), width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_SPHERE)


def draw_logo(p, rect, t, glow=0.0):
    """Zeichnet das Logo teilweise aufgebaut (t = 0 nichts, t = 1 fertig) in das Quadrat rect,
    mit grünem Schein der Stärke glow (0..1)."""
    st = logo_stages(t)
    green, teal = QColor("#34d399"), QColor("#2dd4bf")
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.x(), rect.y())
    k = min(rect.width(), rect.height()) / 120.0
    p.scale(k, k)
    _draw_glow(p, glow)
    if st["fill"] > 0:                      # Kugel: Licht von links oben, zum Rand hin dunkler
        a = st["fill"]
        shade = QRadialGradient(QPointF(_SPHERE_C.x() - 16, _SPHERE_C.y() - 18), _SPHERE_R * 1.45)
        shade.setColorAt(0.0, QColor(30, 96, 82, int(255 * a)))
        shade.setColorAt(0.55, QColor(13, 52, 45, int(255 * a)))
        shade.setColorAt(1.0, QColor(5, 22, 19, int(255 * a)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shade))
        p.drawEllipse(_SPHERE_C, _SPHERE_R, _SPHERE_R)
        shine = QRadialGradient(QPointF(_SPHERE_C.x() - 20, _SPHERE_C.y() - 24), 14)   # kleiner Glanzpunkt
        shine.setColorAt(0.0, QColor(255, 255, 255, int(60 * a)))
        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(shine))
        p.drawEllipse(QPointF(_SPHERE_C.x() - 20, _SPHERE_C.y() - 24), 14, 14)
    _stroke(p, _SPHERE, green, 5, st["sphere"])
    for path in _TRACES:
        _stroke(p, path, teal, 3.5, st["traces"])
    if st["dots"] > 0:
        dot = QColor(green)
        dot.setAlphaF(st["dots"])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        for x, y in _DOTS:
            p.drawEllipse(QPointF(x, y), 4, 4)
    _stroke(p, _CHIP, green, 4, st["chip"])
    for path in _PINS:
        _stroke(p, path, green, 3, st["pins"])
    _stroke(p, _CHECK, teal, 4, st["check"])
    p.restore()


class AnimatedLogo(QWidget):
    """Das Markenzeichen, wie im Monitor animiert (Aufbau, grüner Schein, Abbau, alle CYCLE_MS von vorn),
    auf durchsichtigem Grund. Der Takt läuft nur, solange das Widget sichtbar ist."""
    FRAME_MS = 40

    def __init__(self, size=72, parent=None):
        super().__init__(parent)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        pad = size // 4                         # Platz für den Schein rundherum
        self._size = size
        self.setFixedSize(size + 2 * pad, size + 2 * pad)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def elapsed(self):
        return self._clock.elapsed() if self._clock.isValid() else 0

    def showEvent(self, e):
        super().showEvent(e)
        self._clock.start()                     # bei jedem Zeigen von vorn
        self._timer.start(self.FRAME_MS)

    def hideEvent(self, e):
        self._timer.stop()
        self._clock.invalidate()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        ms = self.elapsed()
        r = self.rect()
        box = QRectF(r.center().x() - self._size / 2 + 0.5, r.center().y() - self._size / 2 + 0.5,
                     self._size, self._size)
        draw_logo(p, box, logo_progress(ms), glow_strength(ms))
        p.end()


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
        self._logo_clock = QElapsedTimer()      # läuft, solange das Rauschen zu sehen ist
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
        if live:
            self._logo_clock.invalidate()
        elif not self._logo_clock.isValid():
            self._logo_clock.start()            # Logo baut sich von vorn auf, sobald das Rauschen beginnt
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

    def _logo_progress(self):
        """Wie weit das Logo gerade aufgebaut ist (0..1); läuft alle CYCLE_MS von vorn."""
        if not self._logo_clock.isValid():
            return 0.0
        return logo_progress(self._logo_clock.elapsed())

    def _logo_glow(self):
        """Stärke des grünen Scheins ums Logo (0..1)."""
        if not self._logo_clock.isValid():
            return 0.0
        return glow_strength(self._logo_clock.elapsed())

    def _set_interval(self, ms):
        if self._timer.interval() != ms:
            self._timer.setInterval(ms)

    def showEvent(self, e):
        super().showEvent(e)
        self._tick()
        self._timer.start(NOISE_MS)

    def hideEvent(self, e):
        self._timer.stop()                      # unsichtbar kostet der Monitor nichts
        self._logo_clock.invalidate()           # beim nächsten Zeigen baut sich das Logo neu auf
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
            size = min(LOGO_SIZE, scr.width() - 8, scr.height() - 8)
            box = QRectF(scr.center().x() - size / 2, scr.center().y() - size / 2, size, size)   # mittig
            draw_logo(p, box, self._logo_progress(), self._logo_glow())
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
