"""BMC-Steward: Seitenmenü statt Reiterleiste.

Die Menüpunkte stehen links unter dem Monitor, die Seiten rechts daneben. Die Schnittstelle ähnelt
absichtlich der des QTabWidget (addTab, setCurrentIndex, currentIndex, indexOf), damit der übrige Code
Seiten wie bisher über ihre Nummer ansprechen kann.
"""
from PyQt6.QtCore import QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QTextOption
from PyQt6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QScrollArea, QStackedWidget, QStyle, QStyledItemDelegate
from .i18n import T


NAV_CSS = ("QListWidget#nav { border: none; background: transparent; padding: 0; outline: 0; }"
           "QListWidget#nav::item { border: none; padding: 0; }")


ACCENT_ROLE = Qt.ItemDataRole.UserRole + 1      # Farbe, in der der Punkt unter der Maus als Knopf aufleuchtet
MARK_ROLE = Qt.ItemDataRole.UserRole + 2        # Inhalt des Kreises links: Schrittnummer oder Symbol
SECTION_ROLE = Qt.ItemDataRole.UserRole + 3     # Überschrift über dem Punkt (Beginn eines Bereichs)
LAMP_ROLE = Qt.ItemDataRole.UserRole + 4        # Farbe der Leuchte rechts („hier läuft gerade etwas“), sonst leer
HEAD_H, ROW_H = 26, 34
LAB_LAMP = "#f2c200"         # Leuchte „Testlabor“: virtuelle Server laufen
PLAN_LAMP = "#3b8beb"        # Leuchte „Aufbauplaner“: Ausfall-Simulation läuft


class AccentDelegate(QStyledItemDelegate):
    """Zeichnet die Menüpunkte im Stil des Programms: Kreis mit Schrittnummer bzw. Symbol, Name, die aktive
    Seite mit hellem Grund und Akzentstreifen links. Ein Punkt mit Akzentfarbe (DHCP-Server) hat einen Kreis
    in dieser Farbe und leuchtet unter der Maus als Knopf in ihr auf; im Ruhezustand ist er ein normaler Punkt.
    Beginnt mit einem Punkt ein Bereich, steht dessen Überschrift darüber."""

    def sizeHint(self, option, index):
        return QSize(0, ROW_H + (HEAD_H if index.data(SECTION_ROLE) else 0))

    def paint(self, painter, option, index):
        from . import theme
        t = theme.tokens()
        accent = index.data(ACCENT_ROLE)             # Knopffarbe unter der Maus
        idle_accent = t["dhcp"] if accent else None   # Kreis und Schrift im Ruhezustand, passend zum Design
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        section = index.data(SECTION_ROLE)
        if section:
            f = QFont(option.font)
            f.setPointSizeF(max(6.5, f.pointSizeF() * 0.78))
            f.setBold(True)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
            painter.setFont(f)
            painter.setPen(QColor(t["dim"]))
            painter.drawText(QRectF(rect.x() + 10, rect.y() + 6, rect.width() - 20, HEAD_H - 6),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, section.upper())
            rect.setTop(rect.top() + HEAD_H)
        box = rect.adjusted(2, 2, -2, -2)
        text_col = QColor(t["muted"])
        if accent and hover:                        # Akzentfarbe leuchtet nur unter der Maus auf
            base = QColor(accent)
            painter.setPen(QPen(QColor(t["accent"]), 2) if selected else QPen(base.darker(150), 1))
            painter.setBrush(base.darker(125) if selected else base.lighter(115))
            painter.drawRoundedRect(box, 7, 7)
            text_col = QColor("#000000")
        elif selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t["accent_soft"]))
            painter.drawRoundedRect(box, 7, 7)
            painter.setBrush(QColor(t["accent"]))
            painter.drawRoundedRect(QRectF(box.x(), box.y() + 4, 3, box.height() - 8), 1.5, 1.5)
            text_col = QColor(t["text"])
        elif hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t["card"]))
            painter.drawRoundedRect(box, 7, 7)
            text_col = QColor(t["text"])
        # Kreis mit Nummer oder Symbol
        mark = index.data(MARK_ROLE)
        x = box.x() + 10
        if mark is not None:
            d = 20
            circle = QRectF(x, box.center().y() - d / 2, d, d)
            if selected and not (accent and hover):
                painter.setPen(QPen(QColor(t["accent"]), 1))
                painter.setBrush(QColor(t["accent"]))
                mark_col = QColor(t["on_accent"])
            elif accent:
                painter.setPen(QPen(QColor("#000000") if hover else QColor(idle_accent), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                mark_col = QColor("#000000") if hover else QColor(idle_accent)
            else:
                painter.setPen(QPen(QColor(t["line2"]), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                mark_col = QColor(t["dim"])
            painter.drawEllipse(circle)
            f = QFont(option.font)
            f.setPointSizeF(max(6.5, f.pointSizeF() * 0.78))
            f.setBold(selected)
            painter.setFont(f)
            painter.setPen(mark_col)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(mark))
            x += d + 10
        f = QFont(option.font)
        f.setBold(selected or bool(accent and hover))
        painter.setFont(f)
        painter.setPen(QColor(idle_accent) if accent and not hover and not selected else text_col)
        opt = QTextOption(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        opt.setWrapMode(QTextOption.WrapMode.NoWrap)
        lamp = index.data(LAMP_ROLE)
        right = box.right() - 6
        if lamp:                                    # Leuchte rechts: in dieser Seite läuft gerade etwas
            d = 12
            dot = QRectF(box.right() - 12 - d, box.center().y() - d / 2, d, d)
            painter.setPen(QPen(QColor(lamp).darker(160), 1))
            painter.setBrush(QColor(lamp))
            painter.drawEllipse(dot)
            right = dot.left() - 6
        painter.setPen(QColor(idle_accent) if accent and not hover and not selected else text_col)
        painter.drawText(QRectF(x, box.y(), right - x, box.height()),
                         str(index.data(Qt.ItemDataRole.DisplayRole)), opt)
        painter.restore()


class SideNav(QObject):
    """Menü (QListWidget, `menu`) und Seitenstapel (QStackedWidget, `stack`) in einem. Beide Widgets
    setzt der Aufrufer selbst ins Fenster; hier wird nur dafür gesorgt, dass sie im Gleichschritt laufen."""
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.menu = QListWidget()
        self.menu.setObjectName("nav")
        self.menu.setStyleSheet(NAV_CSS)
        self.menu.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.menu.setAccessibleName(T("Menü"))
        self.menu.setItemDelegate(AccentDelegate(self.menu))
        self.stack = QStackedWidget()
        self._pages = []                        # die Seiten selbst (im Stapel steht ggf. ihr Scrollbereich)
        self.menu.currentRowChanged.connect(self._row_changed)

    def addTab(self, widget, text, accent=None, mark=None, section=None, scroll=False):
        """Neue Seite mit Menüpunkt `text` am Ende; gibt ihre Nummer zurück. Mit `accent` (z. B. "#c9a100")
        leuchtet der Menüpunkt unter der Maus als Knopf in dieser Farbe auf. `mark` steht im Kreis links
        (Schrittnummer oder Symbol), `section` beginnt einen neuen Bereich mit dieser Überschrift. Mit
        `scroll` liegt eine breite Seite in einem Scrollbereich, damit sie das Fenster nicht breit zwingt."""
        item = QListWidgetItem(text)
        item.setSizeHint(QSize(0, ROW_H + (HEAD_H if section else 0)))
        if accent:
            item.setData(ACCENT_ROLE, accent)
        if mark is not None:
            item.setData(MARK_ROLE, mark)
        if section:
            item.setData(SECTION_ROLE, section)
        self.menu.addItem(item)
        self._pages.append(widget)
        if scroll:
            area = QScrollArea()
            area.setObjectName("pageScroll")
            area.setWidget(widget)
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setStyleSheet("QScrollArea#pageScroll, QScrollArea#pageScroll > QWidget > QWidget"
                               " { background: transparent; }")
            return self.stack.addWidget(area)
        return self.stack.addWidget(widget)

    def widget(self, i):
        """Seite Nummer `i` (nicht ihr Scrollbereich)."""
        return self._pages[i] if 0 <= i < len(self._pages) else None

    def currentWidget(self):
        return self.widget(self.currentIndex())

    def _row_changed(self, row):
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            self.currentChanged.emit(row)

    def setCurrentIndex(self, i):
        self.menu.setCurrentRow(i)

    def currentIndex(self):
        return self.stack.currentIndex()

    def indexOf(self, widget):
        return self._pages.index(widget) if widget in self._pages else self.stack.indexOf(widget)

    def count(self):
        return self.stack.count()

    def setLamp(self, i, color=None):
        """Leuchte rechts am Menüpunkt `i` ein- (Farbe, z. B. "#e8b400") oder ausschalten (None)."""
        item = self.menu.item(i) if i is not None else None
        if item is not None and item.data(LAMP_ROLE) != color:
            item.setData(LAMP_ROLE, color)
            tip = T("Hier läuft gerade etwas.") if color else ""
            item.setToolTip(tip)
