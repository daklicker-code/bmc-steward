"""BMC-Steward: Hilfeseite mit Inhaltsverzeichnis und Volltextsuche.

Der Hilfetext (i18n.HELP) ist in Gruppen (<h1>) und Abschnitte (<h2>) gegliedert. Links stehen Suchfeld und
Inhaltsverzeichnis, rechts der ganze Text. Das Verzeichnis folgt beim Blättern dem gerade sichtbaren
Abschnitt; die Suche markiert alle Treffer im Text, blendet Abschnitte ohne Treffer im Verzeichnis aus und
springt mit Enter/Umschalt+Enter (oder F3/Umschalt+F3) von Treffer zu Treffer.
"""
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut, QTextCursor, QTextDocument, QTextFormat
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QTextEdit, QToolButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .i18n import T

MIN_QUERY = 2          # ab so vielen Zeichen wird gesucht; ein einzelner Buchstabe träfe fast alles
POS_ROLE = Qt.ItemDataRole.UserRole


class HelpPage(QWidget):
    """Suchfeld und Inhaltsverzeichnis links, Hilfetext rechts."""

    def __init__(self, html, parent=None):
        super().__init__(parent)
        self.find = QLineEdit()
        self.find.setPlaceholderText(T("Hilfe durchsuchen … (Strg+F)"))
        self.find.setClearButtonEnabled(True)
        self.find.setToolTip(T("Durchsucht den ganzen Hilfetext. Enter springt zum nächsten Treffer, "
                               "Umschalt+Enter zum vorigen, Esc leert das Feld."))
        self.find.textChanged.connect(self.search)
        self.find.returnPressed.connect(lambda: self.step(1))
        self.prev_btn, self.next_btn = QToolButton(), QToolButton()
        for btn, text, tip, d in ((self.prev_btn, "▲", T("Voriger Treffer (Umschalt+F3)"), -1),
                                  (self.next_btn, "▼", T("Nächster Treffer (F3)"), 1)):
            btn.setText(text)
            btn.setToolTip(tip)
            btn.setAutoRaise(True)
            btn.clicked.connect(lambda _, d=d: self.step(d))
        self.hits_label = QLabel()
        self.hits_label.setObjectName("helpHits")
        self.toc = QTreeWidget()
        self.toc.setHeaderHidden(True)
        self.toc.setRootIsDecorated(False)
        self.toc.setIndentation(14)
        self.toc.setUniformRowHeights(True)
        self.toc.itemClicked.connect(self.toc_clicked)
        self.toc.itemActivated.connect(self.toc_clicked)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setHtml(html)
        self.view.verticalScrollBar().valueChanged.connect(self.follow_scroll)

        left = QWidget()
        left.setFixedWidth(270)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        ll.addWidget(self.find)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        bar.addWidget(self.hits_label, 1)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.next_btn)
        ll.addLayout(bar)
        ll.addWidget(self.toc, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(left)
        lay.addWidget(self.view, 1)

        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, slot in (("Ctrl+F", self.focus_find), ("F3", lambda: self.step(1)),
                           ("Shift+F3", lambda: self.step(-1)), ("Shift+Return", lambda: self.step(-1)),
                           ("Shift+Enter", lambda: self.step(-1)), ("Esc", self.clear_find)):
            QShortcut(QKeySequence(keys), self, slot, context=ctx)

        self.matches, self.current = [], -1
        self.sections = []          # (Anfang im Dokument, Eintrag im Verzeichnis), aufsteigend
        self.pinned = None          # angewählter Abschnitt, den das Mitlaufen beim Blättern nicht überschreibt
        self.build_toc()
        self.update_hits()
        theme.on_change(self.apply_theme)

    # ---------- Inhaltsverzeichnis ----------
    def build_toc(self):
        """Liest Gruppen (h1) und Abschnitte (h2) aus dem fertigen Dokument, damit Verzeichnis und Text nie
        auseinanderlaufen, auch wenn ein Abschnitt dazukommt."""
        group, bold = None, QFont(self.toc.font())
        bold.setBold(True)
        block = self.view.document().begin()
        while block.isValid():
            level, text = block.blockFormat().headingLevel(), block.text().strip()
            if level == 1 and text:
                group = QTreeWidgetItem(self.toc, [text])
                group.setFont(0, bold)
                group.setData(0, POS_ROLE, block.position())
                group.setData(0, POS_ROLE + 1, text)
                group.setToolTip(0, text)
                self.sections.append((block.position(), group))
            elif level == 2 and text:
                item = QTreeWidgetItem(group or self.toc, [text])
                item.setData(0, POS_ROLE, block.position())
                item.setData(0, POS_ROLE + 1, text)
                item.setToolTip(0, text)
                self.sections.append((block.position(), item))
            block = block.next()
        self.toc.expandAll()

    def toc_clicked(self, item, *_):
        """Springt zum Abschnitt; während einer Suche gleich zu seinem ersten Treffer."""
        pos = item.data(0, POS_ROLE)
        end = self.section_end(item)
        for i, cur in enumerate(self.matches):
            if pos <= cur.selectionStart() < end:
                self.current = i
                self.show_matches()
                return
        self.scroll_to(pos)
        self.mark_section(pos, pin=True)

    def section_end(self, item):
        """Erste Dokumentposition hinter dem Abschnitt (bei einer Gruppe: hinter ihrem letzten Abschnitt)."""
        starts = [p for p, it in self.sections if p > item.data(0, POS_ROLE)
                  and (item.parent() is not None or it.parent() is None)]
        return starts[0] if starts else self.view.document().characterCount()

    def scroll_to(self, pos):
        block = self.view.document().findBlock(pos)
        top = self.view.document().documentLayout().blockBoundingRect(block).top()
        self.view.verticalScrollBar().setValue(int(top))

    def follow_scroll(self, *_):
        """Markiert im Verzeichnis den Abschnitt, der oben im Text steht. Die letzten Abschnitte erreichen den
        oberen Rand nie: Wurde einer angewählt, bleibt er markiert, solange der Text ganz unten steht."""
        bar = self.view.verticalScrollBar()
        if self.pinned is not None and bar.value() >= bar.maximum():
            return
        self.pinned = None
        self.mark_section(self.view.cursorForPosition(QPoint(4, 24)).position())

    def mark_section(self, pos, pin=False):
        here = None
        for start, item in self.sections:
            if start > pos:
                break
            if not item.isHidden():
                here = item
        self.pinned = here if pin else None
        if here is not None and self.toc.currentItem() is not here:
            self.toc.blockSignals(True)
            self.toc.setCurrentItem(here)
            self.toc.scrollToItem(here)
            self.toc.blockSignals(False)

    # ---------- Suche ----------
    def focus_find(self):
        self.find.setFocus()
        self.find.selectAll()

    def clear_find(self):
        if self.find.text():
            self.find.clear()
        else:
            self.view.setFocus()

    def search(self, text):
        """Sucht alle Vorkommen (ohne Groß-/Kleinschreibung), zählt sie je Abschnitt und blendet Abschnitte
        ohne Treffer im Verzeichnis aus."""
        query = text.strip()
        doc = self.view.document()
        self.matches = []
        if len(query) >= MIN_QUERY:
            cur = doc.find(query, 0, QTextDocument.FindFlag(0))
            while not cur.isNull():
                self.matches.append(cur)
                cur = doc.find(query, cur, QTextDocument.FindFlag(0))
        searching = len(query) >= MIN_QUERY
        starts = [p for p, _ in self.sections]
        counts = {id(it): 0 for _, it in self.sections}
        for cur in self.matches:
            idx = max((i for i, p in enumerate(starts) if p <= cur.selectionStart()), default=None)
            if idx is not None:
                counts[id(self.sections[idx][1])] += 1
        for _, item in self.sections:
            name = item.data(0, POS_ROLE + 1)
            if item.parent() is None:          # Gruppe: eigene Treffer (Überschrift) plus die ihrer Abschnitte
                n = counts[id(item)] + sum(counts[id(item.child(i))] for i in range(item.childCount()))
            else:
                n = counts[id(item)]
            item.setText(0, f"{name}  ({n})" if searching and n else name)
            item.setHidden(searching and n == 0)
        self.current = 0 if self.matches else -1
        self.show_matches()

    def step(self, direction):
        if not self.matches:
            return
        self.current = (self.current + direction) % len(self.matches)
        self.show_matches()

    def show_matches(self, scroll=True):
        """Färbt alle Treffer ein, den aktuellen kräftiger, und rollt ihn ins Bild."""
        tok = theme.tokens()
        soft, strong = QColor(tok["dhcp_soft"]), QColor(tok["warn"])
        sels = []
        for i, cur in enumerate(self.matches):
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format.setBackground(QBrush(strong if i == self.current else soft))
            if i == self.current:
                sel.format.setForeground(QBrush(QColor(tok["bg"])))
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, False)
            sels.append(sel)
        self.view.setExtraSelections(sels)
        if scroll and 0 <= self.current < len(self.matches):
            cur = QTextCursor(self.matches[self.current])
            cur.clearSelection()
            self.view.setTextCursor(cur)
            bar = self.view.verticalScrollBar()       # Treffer ins obere Drittel, nicht an den Rand
            bar.setValue(bar.value() + self.view.cursorRect(cur).top() - self.view.viewport().height() // 3)
            self.mark_section(cur.position(), pin=True)
        self.update_hits()

    def update_hits(self):
        query = self.find.text().strip()
        if len(query) < MIN_QUERY:
            text = T("{0} Abschnitte").format(sum(1 for _, it in self.sections if it.parent() is not None))
        elif self.matches:
            text = T("Treffer {0} von {1}").format(self.current + 1, len(self.matches))
        else:
            text = T("Keine Treffer")
        self.hits_label.setText(text)
        for btn in (self.prev_btn, self.next_btn):
            btn.setEnabled(len(self.matches) > 1)

    def apply_theme(self, *_):
        self.show_matches(scroll=False)
