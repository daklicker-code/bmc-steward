"""BMC-Steward: Seitenmenü statt Reiterleiste.

Die Menüpunkte stehen links unter dem Monitor, die Seiten rechts daneben. Die Schnittstelle ähnelt
absichtlich der des QTabWidget (addTab, setCurrentIndex, currentIndex, indexOf), damit der übrige Code
Seiten wie bisher über ihre Nummer ansprechen kann.
"""
from PyQt6.QtCore import QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QTextOption
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QStackedWidget, QStyle, QStyledItemDelegate
from .i18n import T


NAV_CSS = (
    "QListWidget#nav { border: 1px solid palette(mid); border-radius: 6px; padding: 4px; outline: 0; }"
    "QListWidget#nav::item { padding: 6px 10px; border-radius: 4px; }"
    "QListWidget#nav::item:hover:!selected { background: palette(alternate-base); }"
    "QListWidget#nav::item:selected { background: palette(highlight); color: palette(highlighted-text); }")


ACCENT_ROLE = Qt.ItemDataRole.UserRole + 1


class AccentDelegate(QStyledItemDelegate):
    """Zeichnet Menüpunkte mit Akzentfarbe (Datenrolle ACCENT_ROLE) als abgerundeten Knopf in dieser Farbe;
    alle anderen Punkte zeichnet unverändert das Stylesheet."""

    def paint(self, painter, option, index):
        accent = index.data(ACCENT_ROLE)
        if not accent:
            super().paint(painter, option, index)
            return
        base = QColor(accent)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        fill = base.darker(125) if selected else base.lighter(115) if hover else base
        rect = QRectF(option.rect).adjusted(1, 2, -1, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(option.palette.highlight().color(), 2) if selected else QPen(base.darker(150), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 6, 6)
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#000000"))                       # schwarz auf Dunkelgelb: gut lesbar
        opt = QTextOption(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        opt.setWrapMode(QTextOption.WrapMode.NoWrap)
        painter.drawText(rect.adjusted(10, 0, -10, 0), str(index.data(Qt.ItemDataRole.DisplayRole)), opt)
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
        self.menu.currentRowChanged.connect(self._row_changed)

    def addTab(self, widget, text, accent=None):
        """Neue Seite mit Menüpunkt `text` am Ende; gibt ihre Nummer zurück. Mit `accent` (z. B. "#c9a100")
        erscheint der Menüpunkt als Knopf in dieser Farbe."""
        item = QListWidgetItem(text)
        item.setSizeHint(QSize(0, 34 if accent else 30))
        if accent:
            item.setData(ACCENT_ROLE, accent)
        self.menu.addItem(item)
        return self.stack.addWidget(widget)

    def _row_changed(self, row):
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            self.currentChanged.emit(row)

    def setCurrentIndex(self, i):
        self.menu.setCurrentRow(i)

    def currentIndex(self):
        return self.stack.currentIndex()

    def indexOf(self, widget):
        return self.stack.indexOf(widget)

    def count(self):
        return self.stack.count()
