"""BMC-Steward: Seitenmenü statt Reiterleiste.

Die Menüpunkte stehen links unter dem Monitor, die Seiten rechts daneben. Die Schnittstelle ähnelt
absichtlich der des QTabWidget (addTab, setCurrentIndex, currentIndex, indexOf), damit der übrige Code
Seiten wie bisher über ihre Nummer ansprechen kann.
"""
from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QStackedWidget
from .i18n import T


NAV_CSS = (
    "QListWidget#nav { border: 1px solid palette(mid); border-radius: 6px; padding: 4px; outline: 0; }"
    "QListWidget#nav::item { padding: 6px 10px; border-radius: 4px; }"
    "QListWidget#nav::item:hover:!selected { background: palette(alternate-base); }"
    "QListWidget#nav::item:selected { background: palette(highlight); color: palette(highlighted-text); }")


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
        self.stack = QStackedWidget()
        self.menu.currentRowChanged.connect(self._row_changed)

    def addTab(self, widget, text):
        """Neue Seite mit Menüpunkt `text` am Ende; gibt ihre Nummer zurück."""
        item = QListWidgetItem(text)
        item.setSizeHint(QSize(0, 30))
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
