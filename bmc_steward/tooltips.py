"""BMC-Steward: Tooltips als gut lesbare Kästchen statt langer Zeilen.

Ein Ereignisfilter an der Anwendung fängt jede Tooltip-Anfrage ab und zeigt den Text in einem schmalen
Kasten mit fester Breite: Zeilen, die im Quelltext nur zum Umbrechen getrennt sind, werden zu Absätzen
zusammengefügt, eine Leerzeile beginnt einen neuen Absatz, Zeilen mit „•“, „-“ oder „–“ werden zu einer
Liste. Steht vor einer Leerzeile ein kurzer erster Absatz, wird er als Überschrift fett gesetzt.
Das gilt für normale Bedienelemente, Tabellen- und Listeneinträge, Spaltenköpfe und Reiter; die Texte
selbst bleiben unverändert (auch für die Übersetzungen)."""
import html
import re

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QHeaderView, QTabBar, QToolTip

WIDTH = 340             # Breite des Kastens in Pixeln (bei langen Texten)
SHORT = 70              # bis zu dieser Länge bleibt ein Text einzeilig, ohne feste Breite
MARK = "<qt><!--bmc-tip-->"   # schon formatiert (beginnt mit <qt>, damit Qt es als HTML erkennt)
BULLET = re.compile(r"^\s*(?:[•·\-–*]|\d+[.)])\s+")


def _join(lines):
    """Zeilen eines Absatzes zusammenfügen. Zeilenumbrüche bleiben nur, wo sie etwas bedeuten: nach einem
    Doppelpunkt, vor einer Klammer-Zeile und bei Zeilen ohne Leerzeichen (z. B. CSV-Köpfe, Befehle)."""
    out = ""
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if not out:
            out = html.escape(line)
            continue
        prev = lines[i - 1].strip()
        keep = prev.endswith(":") or line.startswith("(") or " " not in line or " " not in prev
        out += ("<br>" if keep else " ") + html.escape(line)
    return out


def format_tip(text):
    """Tooltip-Text als HTML-Kasten. Kurze Texte bleiben, wie sie sind; HTML wird nur in die Breite gesetzt."""
    if not text or MARK in text:
        return text
    if Qt.mightBeRichText(text):
        if len(re.sub(r"<[^>]+>", "", text)) <= SHORT:
            return text
        return f"{MARK}<table width='{WIDTH}' cellpadding='0'><tr><td>{text}</td></tr></table></qt>"
    plain = text.strip()
    if len(plain) <= SHORT and "\n" not in plain:
        return text
    paras = re.split(r"\n\s*\n", plain)
    parts = []
    for n, para in enumerate(paras):
        lines = para.split("\n")
        if any(BULLET.match(x) for x in lines):
            items, lead = [], []
            for line in lines:
                if BULLET.match(line):
                    items.append([BULLET.sub("", line)])
                elif items:
                    items[-1].append(line)
                else:
                    lead.append(line)
            if lead:
                parts.append(f"<p style='margin:0 0 4px 0'>{_join(lead)}</p>")
            parts.append("<ul style='margin:0 0 4px -24px'>" + "".join(
                f"<li style='margin-bottom:2px'>{_join(i)}</li>" for i in items) + "</ul>")
            continue
        body = _join(lines)
        if n == 0 and len(paras) > 1 and len(para) <= 60 and "\n" not in para:
            parts.append(f"<p style='margin:0 0 6px 0'><b>{body}</b></p>")
        else:
            parts.append(f"<p style='margin:0 0 6px 0'>{body}</p>")
    inner = "".join(parts)
    inner = re.sub(r"<p style='margin:0 0 6px 0'>(.*)</p>$", r"<p style='margin:0'>\1</p>", inner)
    return f"{MARK}<table width='{WIDTH}' cellpadding='0'><tr><td>{inner}</td></tr></table></qt>"


class TipFilter(QObject):
    """Zeigt jeden Tooltip über format_tip(). Was sich nicht zuordnen lässt, geht unverändert an Qt."""

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.Type.ToolTip:
            return False
        try:
            text, rect = self._lookup(obj, ev.pos())
        except (RuntimeError, AttributeError, TypeError):
            return False
        if not text:
            return False
        nice = format_tip(text)
        if nice == text:
            return False
        if rect is None:
            QToolTip.showText(ev.globalPos(), nice, obj)
        else:
            QToolTip.showText(ev.globalPos(), nice, obj, rect)
        return True

    @staticmethod
    def _lookup(obj, pos):
        """(Tooltip-Text, Bereich, in dem er gilt) für das Element unter der Maus."""
        parent = obj.parent()
        if isinstance(parent, QHeaderView) and obj is parent.viewport():
            sec = parent.logicalIndexAt(pos)
            if sec < 0 or parent.model() is None:
                return "", None
            text = parent.model().headerData(sec, parent.orientation(), Qt.ItemDataRole.ToolTipRole)
            if parent.orientation() == Qt.Orientation.Horizontal:
                x = parent.sectionViewportPosition(sec)
                r = obj.rect().adjusted(0, 0, 0, 0)
                r.setLeft(x)
                r.setWidth(parent.sectionSize(sec))
            else:
                r = obj.rect()
                r.setTop(parent.sectionViewportPosition(sec))
                r.setHeight(parent.sectionSize(sec))
            return (text if isinstance(text, str) else ""), r
        if isinstance(parent, QAbstractItemView) and obj is parent.viewport():
            idx = parent.indexAt(pos)
            if not idx.isValid():
                return "", None
            text = idx.data(Qt.ItemDataRole.ToolTipRole)
            return (text if isinstance(text, str) else ""), parent.visualRect(idx)
        if isinstance(obj, QTabBar):
            i = obj.tabAt(pos)
            return (obj.tabToolTip(i) if i >= 0 else ""), (obj.tabRect(i) if i >= 0 else None)
        if isinstance(obj, QAbstractItemView):
            return "", None                          # Ansicht selbst: Qt fragt das Modell über den Viewport
        return obj.toolTip() if hasattr(obj, "toolTip") else "", None


_filter = None


def install(app=None):
    """Einmal je Anwendung einhängen (mehrfacher Aufruf schadet nicht)."""
    global _filter
    app = app or QApplication.instance()
    if app is None or _filter is not None:
        return
    _filter = TipFilter(app)
    app.installEventFilter(_filter)
