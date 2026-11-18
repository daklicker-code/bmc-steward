"""BMC-Steward: Seite „Aufbauplaner“ – Racks zusammenstellen, Geräte einbauen, verkabeln, prüfen.

Links die Bibliothek (Server aus der Serverliste, Switches, Infrastruktur, Platzhalter), in der Mitte die
Racks als Zeichenfläche (RackCanvas), rechts Eigenschaften, Prüfung, Netze und Stückliste. Der Plan selbst
(planner.Plan) kennt keine Oberfläche. Gespeichert wird nur auf Knopfdruck – wie überall im Tool.
"""
import json
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPainterPathStroker, QPen,
                         QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
                             QHBoxLayout, QHeaderView, QStyle, QTreeWidget, QTreeWidgetItem,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                             QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from . import i18n
from .applog import LOG
from .core import intro_label
from .i18n import HERE, T
from .planner import CATALOG, MAX_DEV_U, MAX_RACK_U, MIN_RACK_U, NETS, Plan, catalog_texts, demo_plan, port_label, \
    vendor_of

MIME = "application/x-bmc-steward-planner"

# Farben der Zeichenfläche: helle Rack-Ansicht im Stil üblicher DCIM-Werkzeuge, unabhängig vom System-Thema
BG = QColor("#e6eaef")
RACK_BG = QColor("#f8fafc")
RACK_LINE = QColor("#4b5563")
RAIL = QColor("#9ca3af")
STRIPE = QColor("#e5e7eb")
TEXT = QColor("#111827")
MUTED = QColor("#4b5563")
DIM = QColor("#9ca3af")
ACCENT = QColor("#2563eb")
TITLE_BG = QColor("#1f3a5f")
TITLE_SEL = QColor("#2563eb")
NET_COLOR = {"mgmt": QColor("#ca8a04"), "data": QColor("#2563eb"), "storage": QColor("#9333ea"),
             "uplink": QColor("#059669"), "patch": QColor("#64748b")}
PORT_EDGE = {"bmc": QColor("#a16207"), "mgmt": QColor("#a16207"), "data": QColor("#1e40af"),
             "up": QColor("#047857"), "patch": QColor("#475569")}
PORT_FILL = QColor("#374151")
KIND_FILL = {"server": ("#e5e7eb", "#6b7280"), "switch": ("#dbe7f5", "#3b6ea5"), "fw": ("#f6dada", "#b45454"),
             "patch": ("#e2e5ea", "#6b7280"), "pdu": ("#f5e7c8", "#a87b2a"), "blank": ("#eef0f3", "#b6bcc6")}
STRIPE_COLOR = {"dell": "#2563eb", "hpe": "#01a982", "smc": "#9333ea", "lenovo": "#dc2626", "gen": "#6b7280",
                "switch": "#0284c7", "fw": "#e11d48", "pdu": "#d97706", "patch": "#64748b", "blank": "#cbd5e1"}
STATE_COLOR = {"ok": QColor("#16a34a"), "degraded": QColor("#d97706"), "down": QColor("#dc2626")}
LEGEND = {"mgmt": "#facc15", "data": "#93c5fd", "storage": "#d8b4fe", "uplink": "#6ee7b7"}
BAR_CSS = ("QFrame#plBar { background: #1f3a5f; border-radius: 3px; }"
           "QFrame#plBar QPushButton, QFrame#plBar QToolButton { color: #ffffff; background: transparent;"
           " border: 1px solid #3d6491; border-radius: 3px; padding: 4px 10px; }"
           "QFrame#plBar QPushButton:hover, QFrame#plBar QToolButton:hover { background: #2b4d78; }"
           "QFrame#plBar QPushButton:checked { background: #2563eb; border-color: #93c5fd; }"
           "QFrame#plBar QPushButton#seg { border-radius: 0; padding: 4px 12px; }"
           "QFrame#plBar QComboBox { background: #ffffff; color: #111827; border-radius: 3px; padding: 3px 8px; }"
           "QFrame#plBar QLabel#legend { color: #ffffff; }")
LEVEL_COLOR = {"err": "#d9534f", "warn": "#c77700", "info": "#2f7fb5"}
LEVEL_MARK = {"err": "✕", "warn": "!", "info": "i"}


def net_names():
    return {"mgmt": T("Management (BMC)"), "data": T("Daten"), "storage": T("Storage"), "uplink": T("Uplink"),
            "patch": T("Patch")}


def _group(title):
    """Attributgruppe der Eigenschaften: Rahmen mit Titel und Formular darin."""
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setContentsMargins(8, 6, 8, 6)
    return box, form


class PlannerLibrary(QListWidget):
    """Bibliothek: Einträge lassen sich in ein Rack ziehen (oder doppelklicken)."""

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setWordWrap(True)
        self.setSpacing(1)
        self.setStyleSheet("QListWidget::item { padding: 3px 6px; }")

    def mimeTypes(self):
        return [MIME]

    def mimeData(self, items):
        md = QMimeData()
        payload = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        if payload:
            md.setData(MIME, payload.encode("utf-8"))
        return md


class RackCanvas(QWidget):
    """Zeichnet die Racks samt Geräten, Ports und Kabeln und nimmt Maus und Drag & Drop entgegen."""
    changed = pyqtSignal()               # Plan geändert
    selectionChanged = pyqtSignal()
    message = pyqtSignal(str, bool)      # Text, Fehler?
    simClicked = pyqtSignal(str, str)    # ("cable"|"dev", id) im Simulationsmodus
    deleteRequested = pyqtSignal()

    UH, RW, GAP, M, HEAD, NUM = 16, 300, 72, 14, 46, 24

    def __init__(self):
        super().__init__()
        self.plan = Plan()
        self.sel = None                  # ("dev"|"cable"|"rack", id)
        self.cable_mode = False
        self.net_choice = None           # None = automatisch
        self.pending = None              # (Gerät, Port) des ersten Klicks beim Verkabeln
        self.sim = None                  # {Geräte-ID: Zustand} während der Ausfall-Simulation
        self.broken, self.failed = set(), set()
        self.ghost = None                # (Rack-ID, Position, Höhe, passt?)
        self.drag = None
        self.view = "front"              # "front" (Gerätefronten) oder "rear" (Ports und Kabel)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(T("Racks"))
        self.relayout()

    # ------------------------------------------------------------ Geometrie
    def relayout(self):
        n = max(1, len(self.plan.racks))
        h = max([r["h"] for r in self.plan.racks] or [24])
        self.setMinimumSize(2 * self.M + n * (self.RW + self.GAP), self.M + self.HEAD + h * self.UH + 2 * self.M)
        self.update()

    def rack_x(self, i):
        return self.M + i * (self.RW + self.GAP)

    def top(self):
        return self.M + self.HEAD

    def slot_rect(self, i):
        r = self.plan.racks[i]
        x = self.rack_x(i)
        return QRectF(x + self.NUM + 4, self.top(), self.RW - self.NUM - 8, r["h"] * self.UH)

    def dev_rect(self, d):
        i = self.plan.rack_index(d["rack"])
        s = self.slot_rect(i)
        h = self.plan.racks[i]["h"]
        return QRectF(s.x(), s.y() + (h - (d["pos"] + d["u"] - 1)) * self.UH + 0.5, s.width(), d["u"] * self.UH - 1)

    def port_rects(self, d, rect=None):
        """[(Port-ID, Rechteck)] rechtsbündig im Gerät; ab sieben Ports in zwei Reihen."""
        ports = d["ports"]
        if not ports:
            return []
        rect = rect or self.dev_rect(d)
        two = len(ports) > 6
        size, step = (6, 7) if two else (8, 10)
        items, col, row, extra, prev = [], -1, 1, 0, None
        for p in ports:
            if two and prev is not None and p["k"] != prev:
                row, extra = 1, extra + 3
            if two:
                if row == 1:
                    col, row = col + 1, 0
                else:
                    row = 1
            else:
                col += 1
            items.append((p["id"], col, row, extra))
            prev = p["k"]
        total = (col + 1) * step - (step - size) + extra
        x0 = rect.right() - 6 - total
        cy = rect.center().y()
        out = []
        for pid, c, r, ex in items:
            y = (cy - size - 0.5 + r * (size + 1)) if two else cy - size / 2
            out.append((pid, QRectF(x0 + c * step + ex, y, size, size)))
        return out

    def port_center(self, did, pid):
        d = self.plan.dev(did)
        for p, rc in self.port_rects(d):
            if p == pid:
                return rc.center()
        return None

    def cable_path(self, c):
        p1, p2 = self.port_center(*c["a"]), self.port_center(*c["b"])
        if p1 is None or p2 is None:
            return None
        path = QPainterPath(p1)
        A, B = self.plan.dev(c["a"][0]), self.plan.dev(c["b"][0])
        if A["rack"] == B["rack"]:
            bx = max(p1.x(), p2.x()) + 16 + min(46, abs(p2.y() - p1.y()) * 0.12)
            path.cubicTo(QPointF(bx, p1.y()), QPointF(bx, p2.y()), p2)
        else:
            ty = min(p1.y(), p2.y()) - 30 - abs(p2.x() - p1.x()) * 0.05
            path.cubicTo(QPointF(p1.x() + 40, ty), QPointF(p2.x() + 40, ty), p2)
        return path

    # ------------------------------------------------------------ Treffer
    def port_at(self, pt):
        if self.view != "rear":
            return None
        for d in self.plan.placed():
            r = self.dev_rect(d)
            if r.adjusted(0, -1, 0, 1).contains(pt):
                for pid, rc in self.port_rects(d, r):
                    if rc.adjusted(-2, -1, 2, 1).contains(pt):
                        return d["id"], pid
        return None

    def dev_at(self, pt):
        return next((d for d in self.plan.placed() if self.dev_rect(d).contains(pt)), None)

    def cable_hit(self, pt):
        if self.view != "rear":
            return None
        st = QPainterPathStroker()
        st.setWidth(8)
        for c in reversed(self.plan.cables):
            path = self.cable_path(c)
            if path is not None and st.createStroke(path).contains(pt):
                return c["id"]
        return None

    def rack_at(self, pt):
        for i, r in enumerate(self.plan.racks):
            x = self.rack_x(i)
            if QRectF(x, self.M, self.RW, self.HEAD + r["h"] * self.UH + 6).contains(pt):
                return r["id"]
        return None

    def target(self, pt, u, off=0):
        """Rack und Position (unterste HE) für ein Gerät mit `u` HE, dessen oberste HE unter dem Mauszeiger liegt."""
        for i, r in enumerate(self.plan.racks):
            x = self.rack_x(i)
            if x - self.GAP / 2 <= pt.x() <= x + self.RW + self.GAP / 2:
                idx = int((pt.y() - self.top()) // self.UH) - off
                pos = r["h"] - idx - u + 1
                return r["id"], max(1, min(r["h"] - u + 1, pos))
        return None

    # ------------------------------------------------------------ Zeichnen
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), BG)
        if not self.plan.racks:
            p.setPen(MUTED)
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter,
                       T("Noch kein Rack – oben „＋ Rack“ klicken."))
            return
        small = QFont(self.font())
        small.setPointSizeF(7)
        bold = QFont(self.font())
        bold.setBold(True)
        for i, r in enumerate(self.plan.racks):
            self._paint_rack(p, i, r, small, bold)
        for d in self.plan.placed():
            self._paint_dev(p, d)
        if self.view == "rear":
            for c in self.plan.cables:
                self._paint_cable(p, c)
        if self.ghost:
            rid, pos, u, ok = self.ghost
            i = self.plan.rack_index(rid)
            if i >= 0:
                s = self.slot_rect(i)
                h = self.plan.racks[i]["h"]
                g = QRectF(s.x(), s.y() + (h - (pos + u - 1)) * self.UH, s.width(), u * self.UH)
                col = QColor("#16a34a" if ok else "#dc2626")
                fill = QColor(col)
                fill.setAlpha(60)
                p.setBrush(fill)
                p.setPen(QPen(col, 1, Qt.PenStyle.DashLine))
                p.drawRect(g)

    def _paint_rack(self, p, i, r, small, bold):
        x = self.rack_x(i)
        L = self.plan.rack_load(r["id"])
        sel = self.sel == ("rack", r["id"])
        # Titelleiste: Name, Höhe, Ansicht
        title = QRectF(x, self.M, self.RW, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TITLE_SEL if sel else TITLE_BG)
        p.drawRect(title)
        p.setFont(bold)
        p.setPen(QColor("#ffffff"))
        p.drawText(title.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                   f"{r['name']}  ·  {r['h']} " + T("HE"))
        p.setFont(small)
        p.setPen(QColor("#c7d2fe"))
        p.drawText(title.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   T("Vorderseite") if self.view == "front" else T("Rückseite"))
        # Belegung und Last
        p.setPen(MUTED)
        load = f"{L['used']}/{r['h']} {T('HE')} · {L['w'] / 1000:.1f}".replace(".", ",") + \
               (f"/{L['cap'] / 1000:.1f} kW".replace(".", ",") if L["cap"] else " kW")
        p.drawText(QRectF(x, self.M + 23, self.RW, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   load)
        bar = QRectF(x, self.M + 28, self.RW / 2, 5)
        p.setPen(QPen(QColor("#9ca3af"), 0.8))
        p.setBrush(QColor("#ffffff"))
        p.drawRect(bar)
        share = L["used"] / r["h"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#dc2626" if share > 0.9 else "#2563eb"))
        p.drawRect(QRectF(bar.x(), bar.y(), bar.width() * min(1, share), bar.height()))
        # Gehäuse mit Holmen, Einbaufläche, HE-Zählung
        frame = QRectF(x, self.top() - 4, self.RW, r["h"] * self.UH + 8)
        p.setBrush(QColor("#374151"))
        p.setPen(QPen(TITLE_SEL if sel else QColor("#1f2937"), 2 if sel else 1))
        p.drawRect(frame)
        s = self.slot_rect(i)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(RACK_BG)
        p.drawRect(s)
        p.setBrush(RAIL)
        p.drawRect(QRectF(s.x() - 3, s.y(), 3, s.height()))
        p.drawRect(QRectF(s.right(), s.y(), 3, s.height()))
        p.setPen(QPen(STRIPE, 1))
        for u in range(1, r["h"]):
            y = s.y() + u * self.UH - 0.5
            p.drawLine(QPointF(s.x(), y), QPointF(s.right(), y))
        for u in range(1, r["h"] + 1):
            y = s.y() + (r["h"] - u) * self.UH
            p.setPen(QColor("#f3f4f6") if u % 5 == 0 or u == 1 else QColor("#9ca3af"))
            p.drawText(QRectF(x, y, self.NUM - 2, self.UH), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       str(u))

    def _paint_dev(self, p, d):
        rect = self.dev_rect(d)
        fill, edge = KIND_FILL.get(d["kind"], KIND_FILL["server"])
        state = self.sim.get(d["id"]) if self.sim else None
        if d["kind"] == "blank":
            p.setBrush(QBrush(QColor(edge), Qt.BrushStyle.BDiagPattern))
        else:
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0, QColor(fill).lighter(106))
            grad.setColorAt(1, QColor(fill).darker(106))
            p.setBrush(QBrush(grad))
        border = QColor(edge)
        if state and state != "ok":
            border = STATE_COLOR[state]
        p.setPen(QPen(border, 1.4 if state and state != "ok" else 1))
        p.drawRect(rect)
        if d["kind"] != "blank":                     # Griffe links und rechts wie an der Gerätefront
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(edge).lighter(125))
            p.drawRect(QRectF(rect.x() + 1, rect.y() + 2, 3, rect.height() - 4))
            p.drawRect(QRectF(rect.right() - 4, rect.y() + 2, 3, rect.height() - 4))
        stripe = STRIPE_COLOR.get(d.get("vendor") or "gen" if d["kind"] == "server" else d["kind"], "#6b7280")
        x = rect.x() + 8
        if d["kind"] != "blank":
            led = STATE_COLOR[state] if state else QColor("#9ca3af")
            p.setBrush(led)
            p.setPen(QPen(QColor("#ffffff"), 0.6))
            p.drawEllipse(QPointF(x + 2, rect.center().y() if d["u"] == 1 else rect.y() + 8), 2.8, 2.8)
            x += 9
        ports = self.port_rects(d, rect) if self.view == "rear" else []
        right = (ports[0][1].x() - 6) if ports else rect.right() - 8
        if self.view == "front" and d["kind"] != "blank":   # Herstellerkennung rechts an der Front
            badge = {"dell": "DELL", "hpe": "HPE", "lenovo": "LENOVO", "smc": "SMC"}.get(d.get("vendor") or "")
            badge = badge or {"switch": "SW", "fw": "FW", "pdu": "PDU", "patch": "PP"}.get(d["kind"], "")
            if badge:
                f = QFont(self.font())
                f.setPointSizeF(6.5)
                f.setBold(True)
                p.setFont(f)
                w = max(22, len(badge) * 6 + 8)
                br = QRectF(rect.right() - 8 - w, rect.center().y() - 6 if d["u"] == 1 else rect.y() + 3, w, 12)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(stripe))
                p.drawRoundedRect(br, 2, 2)
                p.setPen(QColor("#ffffff"))
                p.drawText(br, Qt.AlignmentFlag.AlignCenter, badge)
                right = br.x() - 6
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(TEXT if d["kind"] != "blank" else MUTED)
        name = d["name"] + (" ⚠" if d.get("missing") else "")
        if d["u"] > 1:
            p.drawText(QRectF(x, rect.y() + 1, right - x, 14), Qt.AlignmentFlag.AlignVCenter, name)
            f.setBold(False)
            f.setPointSizeF(7)
            p.setFont(f)
            p.setPen(MUTED)
            p.drawText(QRectF(x, rect.y() + 14, right - x, 13), Qt.AlignmentFlag.AlignVCenter, d.get("model") or "")
        else:
            p.drawText(QRectF(x, rect.y(), right - x, rect.height()), Qt.AlignmentFlag.AlignVCenter, name)
        for pid, rc in ports:
            kind = self.plan.port(d, pid)["k"]
            c = self.plan.cable_at(d["id"], pid)
            p.setPen(QPen(PORT_EDGE.get(kind, RACK_LINE), 1))
            p.setBrush(NET_COLOR[c["net"]] if c else PORT_FILL)
            p.drawRect(rc)
            if self.pending == (d["id"], pid):
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(ACCENT, 1.6))
                p.drawRect(rc.adjusted(-2, -2, 2, 2))
        if self.sel == ("dev", d["id"]):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ACCENT, 2))
            p.drawRect(rect.adjusted(-1, -1, 1, 1))

    def _paint_cable(self, p, c):
        path = self.cable_path(c)
        if path is None:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        dead = self.sim is not None and (c["id"] in self.broken or c["a"][0] in self.failed
                                         or c["b"][0] in self.failed)
        if self.sel == ("cable", c["id"]):
            p.setPen(QPen(QColor(37, 99, 235, 90), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPath(path)
        col = QColor("#dc2626") if dead else QColor(NET_COLOR[c["net"]])
        pen = QPen(col, 1.8, Qt.PenStyle.DotLine if dead else Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(path)

    # ------------------------------------------------------------ Maus und Tastatur
    def select(self, sel):
        self.sel = sel
        self.update()
        self.selectionChanged.emit()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = e.position()
        self.setFocus()
        if self.sim is not None:
            cid = self.cable_hit(pt)
            d = None if cid else self.dev_at(pt)
            if cid:
                self.simClicked.emit("cable", cid)
            elif d and d["kind"] in ("server", "switch", "fw", "patch"):
                self.simClicked.emit("dev", d["id"])
            return
        hit = self.port_at(pt)
        if hit and self.cable_mode:
            self.port_clicked(*hit)
            return
        if self.cable_mode:
            cid = self.cable_hit(pt)
            if cid:
                self.select(("cable", cid))
            return
        d = self.dev_at(pt)
        if d:
            self.select(("dev", d["id"]))
            off = int((pt.y() - self.dev_rect(d).y()) // self.UH)
            self.drag = {"id": d["id"], "off": off, "start": pt, "active": False}
            return
        cid = self.cable_hit(pt)
        if cid:
            self.select(("cable", cid))
            return
        rid = self.rack_at(pt)
        self.select(("rack", rid) if rid else None)

    def mouseMoveEvent(self, e):
        pt = e.position()
        if self.drag:
            if not self.drag["active"] and (pt - self.drag["start"]).manhattanLength() < 5:
                return
            self.drag["active"] = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            d = self.plan.dev(self.drag["id"])
            t = self.target(pt, d["u"], self.drag["off"])
            self.ghost = (t[0], t[1], d["u"], self.plan.fits(t[0], t[1], d["u"], d["id"])) if t else None
            self.update()
            return
        over_port = self.cable_mode and self.port_at(pt)
        self.setCursor(Qt.CursorShape.PointingHandCursor if over_port or (self.sim is not None and (
            self.cable_hit(pt) or self.dev_at(pt))) else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        drag, ghost = self.drag, self.ghost
        self.drag, self.ghost = None, None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if drag and drag["active"]:
            d = self.plan.dev(drag["id"])
            if ghost and ghost[3]:
                self.plan.place(d, ghost[0], ghost[1])
                self.changed.emit()
            elif ghost:
                self.message.emit(T("Dort ist kein Platz: {0} HE ab HE {1} sind belegt.").format(d["u"], ghost[1]),
                                  True)
            self.update()

    def port_clicked(self, did, pid):
        ex = self.plan.cable_at(did, pid)
        if self.pending is None:
            if ex:
                self.select(("cable", ex["id"]))
            else:
                self.pending = (did, pid)
                self.message.emit(T("{0} · {1} gewählt – jetzt den Gegenport anklicken (Esc bricht ab).").format(
                    self.plan.dev(did)["name"], port_label(pid)), False)
                self.update()
            return
        if self.pending == (did, pid):
            self.pending = None
            self.update()
            return
        c, err = self.plan.connect(*self.pending, did, pid, self.net_choice)
        if err:
            self.message.emit(err, True)
            return
        self.pending = None
        self.select(("cable", c["id"]))
        self.message.emit(T("Kabel gezogen: {0} · {1} ↔ {2} · {3}").format(
            self.plan.dev(c["a"][0])["name"], port_label(c["a"][1]), self.plan.dev(c["b"][0])["name"],
            port_label(c["b"][1])), False)
        self.changed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.pending = None
            self.select(None)
        elif e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.sim is None:
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------ Drag & Drop aus der Bibliothek
    def _payload_u(self, md):
        kind, _, key = bytes(md.data(MIME)).decode("utf-8", "replace").partition(":")
        if kind == "cat" and key in CATALOG:
            return kind, key, CATALOG[key]["u"]
        d = self.plan.dev(key) if kind == "inv" else None
        return (kind, key, d["u"]) if d else (None, None, 1)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME) and self.sim is None:
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        kind, key, u = self._payload_u(e.mimeData())
        t = self.target(e.position(), u)
        if not kind or not t:
            self.ghost = None
            self.update()
            e.ignore()
            return
        self.ghost = (t[0], t[1], u, self.plan.fits(t[0], t[1], u, key if kind == "inv" else None))
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.ghost = None
        self.update()

    def dropEvent(self, e):
        kind, key, u = self._payload_u(e.mimeData())
        t = self.target(e.position(), u)
        self.ghost = None
        if not kind or not t:
            self.update()
            return
        d = self.plan.new_device(key) if kind == "cat" else self.plan.dev(key)
        if not self.plan.place(d, t[0], t[1]):
            self.message.emit(T("Dort ist kein Platz: {0} HE ab HE {1} sind belegt.").format(u, t[1]), True)
            self.update()
            return
        e.acceptProposedAction()
        self.sel = ("dev", d["id"])
        self.changed.emit()
        self.selectionChanged.emit()


class PlannerTabMixin:
    """Seite „Aufbauplaner“ des Hauptfensters."""

    # ================================================================ Aufbau
    def build_planner(self):
        self.plan_path = None
        self.plan_dirty = False
        self._pl_filling = False
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)
        v.addWidget(intro_label(T(
            "<b>Aufbauplaner</b> plant den Einbau vor Ort: Racks mit frei wählbarer Höhe anlegen, Server aus der "
            "Serverliste und Switches hineinziehen, Ports verkabeln. Die Prüfung zeigt Lücken (BMC ohne Kabel, "
            "fehlende Redundanz, PDU-Last), „Netze“ vergibt Hostnamen und BMC-Adressen und übernimmt sie in "
            "„Neuware“, „Ins Testlabor“ baut die Server als virtuelle BMCs nach. Gespeichert wird nur auf "
            "Knopfdruck.")))

        # Werkzeugleiste (dunkelblau wie die Kopfzeile üblicher DCIM-Werkzeuge)
        bar = QFrame()
        bar.setObjectName("plBar")
        bar.setStyleSheet(BAR_CSS)
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(8, 5, 8, 5)
        tb.setSpacing(6)
        file_btn = QToolButton()
        file_btn.setText(T("Plan"))
        file_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        fm = QMenu(file_btn)
        fm.addAction(T("Neuer Plan"), self.planner_new)
        fm.addAction(T("Plan laden …"), self.planner_load)
        fm.addAction(T("Plan speichern …"), self.planner_save)
        fm.addSeparator()
        fm.addAction(T("Beispielaufbau laden"), self.planner_demo)
        file_btn.setMenu(fm)
        tb.addWidget(file_btn)
        add_rack = QPushButton(T("＋ Rack"))
        add_rack.setToolTip(T("Neues Rack mit 42 HE anlegen; die Höhe lässt sich danach rechts ändern."))
        add_rack.clicked.connect(self.planner_add_rack)
        tb.addWidget(add_rack)
        tpl_btn = QToolButton()
        tpl_btn.setText(T("Vorlagen"))
        tpl_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tm = QMenu(tpl_btn)
        tm.addAction(T("Management-Kopf (Patchpanel, OOB-Switch, PDU)"), lambda: self.planner_template("head"))
        tm.addAction(T("HA-Paar (zwei Leaf-Switches mit Querverbindung)"), lambda: self.planner_template("ha"))
        tm.addSeparator()
        tm.addAction(T("Offene Server automatisch verkabeln"), self.planner_auto_cable)
        tpl_btn.setMenu(tm)
        tb.addWidget(tpl_btn)
        tb.addSpacing(12)
        self.pl_view_btns = {}
        group = QButtonGroup(bar)
        group.setExclusive(True)
        for key, text in (("front", T("Vorderseite")), ("rear", T("Rückseite"))):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setObjectName("seg")
            btn.clicked.connect(lambda _=False, k=key: self.planner_set_view(k))
            group.addButton(btn)
            tb.addWidget(btn)
            self.pl_view_btns[key] = btn
        self.pl_view_btns["front"].setToolTip(T("Gerätefronten mit Namen und Hersteller"))
        self.pl_view_btns["rear"].setToolTip(T("Rückseite mit Ports und Kabeln"))
        tb.addSpacing(12)
        self.pl_cable_btn = QPushButton(T("Verkabeln (K)"))
        self.pl_cable_btn.setCheckable(True)
        self.pl_cable_btn.setToolTip(T("Port anklicken, dann den Gegenport. Ein belegter Port zeigt sein Kabel. "
                                       "Esc bricht ab, Entf löscht das gewählte Kabel."))
        self.pl_cable_btn.toggled.connect(self.planner_cable_mode)
        tb.addWidget(self.pl_cable_btn)
        self.pl_net = QComboBox()
        self.pl_net.addItem(T("Kabeltyp: automatisch"), None)
        for k, name in net_names().items():
            if k != "patch":
                self.pl_net.addItem(name, k)
        self.pl_net.currentIndexChanged.connect(lambda *_: setattr(self.pl_canvas, "net_choice",
                                                                   self.pl_net.currentData()))
        tb.addWidget(self.pl_net)
        self.pl_sim_btn = QPushButton(T("Ausfall simulieren"))
        self.pl_sim_btn.setCheckable(True)
        self.pl_sim_btn.setToolTip(T("Kabel oder Geräte anklicken, um sie ausfallen zu lassen; die Server zeigen, "
                                     "ob sie erreichbar bleiben. Ändert nichts am Plan."))
        self.pl_sim_btn.toggled.connect(self.planner_sim_toggle)
        tb.addWidget(self.pl_sim_btn)
        tb.addStretch(1)
        legend = QLabel(" ".join(f"<span style='color:{LEGEND[k]}'>━</span> {n}"
                                 for k, n in net_names().items() if k != "patch"))
        legend.setObjectName("legend")
        tb.addWidget(legend)
        v.addWidget(bar)

        # Pfad zum gewählten Objekt
        self.pl_crumb = QLabel()
        self.pl_crumb.setObjectName("plCrumb")
        self.pl_crumb.setStyleSheet("QLabel#plCrumb { background: #eef2f7; color: #1f3a5f; border: 1px solid #c9d3df;"
                                    " padding: 3px 8px; }")
        v.addWidget(self.pl_crumb)

        # Links: Navigator (Standort › Raum › Reihe › Rack › Gerät) und Katalog
        self.pl_tree = QTreeWidget()
        self.pl_tree.setHeaderHidden(True)
        self.pl_tree.itemClicked.connect(self.planner_tree_clicked)
        self.pl_lib = PlannerLibrary()
        self.pl_lib.itemDoubleClicked.connect(self.planner_lib_double)
        lib_box = QWidget()
        ll = QVBoxLayout(lib_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.pl_lib, 1)
        hint = QLabel(T("In ein Rack ziehen oder doppelklicken."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        ll.addWidget(hint)
        self.pl_left = QTabWidget()
        self.pl_left.addTab(self.pl_tree, T("Navigator"))
        self.pl_left.addTab(lib_box, T("Katalog"))
        self.pl_left.setCurrentIndex(1)

        # Mitte: Rack-Ansicht, darunter Objektliste und Simulationsprotokoll
        self.pl_canvas = RackCanvas()
        self.pl_canvas.changed.connect(self.planner_changed)
        self.pl_canvas.selectionChanged.connect(self.planner_selection_changed)
        self.pl_canvas.message.connect(self.planner_say)
        self.pl_canvas.simClicked.connect(self.planner_sim_click)
        self.pl_canvas.deleteRequested.connect(self.planner_delete_selected)
        self.pl_scroll = QScrollArea()
        self.pl_scroll.setWidget(self.pl_canvas)
        self.pl_scroll.setWidgetResizable(True)
        self.pl_scroll.setStyleSheet("QScrollArea { background: #e6eaef; border: 1px solid #c9d3df; }")
        objects = QWidget()
        ol = QVBoxLayout(objects)
        ol.setContentsMargins(0, 4, 0, 0)
        self.pl_filter = QLineEdit()
        self.pl_filter.setPlaceholderText(T("Objekte filtern (Name, Typ, Modell, Rack) …"))
        self.pl_filter.setClearButtonEnabled(True)
        self.pl_filter.textChanged.connect(self.planner_filter_objects)
        ol.addWidget(self.pl_filter)
        self.pl_objects = QTableWidget(0, 9)
        self.pl_objects.setHorizontalHeaderLabels([T("Name"), T("Typ"), T("Modell"), T("Rack"), T("HE"),
                                                   T("Höhe"), T("Leistung"), T("Anschlüsse"), T("Status")])
        self.pl_objects.verticalHeader().setVisible(False)
        self.pl_objects.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pl_objects.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pl_objects.setAlternatingRowColors(True)
        self.pl_objects.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.pl_objects.horizontalHeader().setStretchLastSection(True)
        self.pl_objects.cellClicked.connect(self.planner_object_clicked)
        ol.addWidget(self.pl_objects, 1)
        self.pl_simlog = QPlainTextEdit()
        self.pl_simlog.setReadOnly(True)
        self.pl_bottom = QTabWidget()
        self.pl_bottom.addTab(objects, T("Objekte"))
        self.pl_bottom.addTab(self.pl_simlog, T("Simulation"))
        center = QSplitter(Qt.Orientation.Vertical)
        center.addWidget(self.pl_scroll)
        center.addWidget(self.pl_bottom)
        center.setStretchFactor(0, 3)
        center.setStretchFactor(1, 1)
        center.setSizes([560, 190])
        center.setChildrenCollapsible(False)

        # Rechts: Eigenschaften, Prüfung, Netze, Stückliste
        self.pl_tabs = QTabWidget()
        self.pl_tabs.addTab(self._planner_props(), T("Eigenschaften"))
        self.pl_checks = QListWidget()
        self.pl_checks.setWordWrap(True)
        self.pl_checks.itemClicked.connect(self.planner_check_clicked)
        self.pl_tabs.addTab(self.pl_checks, T("Prüfung"))
        self.pl_tabs.addTab(self._planner_nets(), T("Netze"))
        self.pl_tabs.addTab(self._planner_bom(), T("Stückliste"))

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.pl_left)
        split.addWidget(center)
        split.addWidget(self.pl_tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([240, 760, 340])
        split.setChildrenCollapsible(False)
        v.addWidget(split, 1)
        self.pl_status = QLabel()
        self.pl_status.setWordWrap(True)
        v.addWidget(self.pl_status)

        QShortcut(QKeySequence("K"), page, self.pl_cable_btn.toggle,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.planner_page = page
        self.planner_set_view("front")
        plan = Plan()
        plan.add_rack("R01", 42)
        self.planner_set_plan(plan, dirty=False)
        return page

    def _planner_props(self):
        self.pl_props = QStackedWidget()
        # 0: Übersicht mit Standort
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, f = _group(T("Standort"))
        self.pl_loc = {}
        for key, label in (("site", T("Standort")), ("room", T("Raum")), ("row", T("Reihe"))):
            edit = QLineEdit()
            edit.setMaxLength(40)
            edit.editingFinished.connect(lambda k=key, e=edit: self.planner_loc_edit(k, e))
            f.addRow(label, edit)
            self.pl_loc[key] = edit
        lay.addWidget(box)
        self.pl_summary = QLabel()
        self.pl_summary.setWordWrap(True)
        self.pl_summary.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.pl_summary, 1)
        self.pl_props.addWidget(w)
        # 1: Gerät, gegliedert in Allgemein, Einbau, Strom, Anschlüsse
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pl_d_name = QLineEdit()
        self.pl_d_name.editingFinished.connect(lambda: self.planner_dev_edit("name"))
        self.pl_d_model = QLineEdit()
        self.pl_d_model.editingFinished.connect(lambda: self.planner_dev_edit("model"))
        self.pl_d_u = QSpinBox()
        self.pl_d_u.setRange(1, MAX_DEV_U)
        self.pl_d_u.setSuffix(" " + T("HE"))
        self.pl_d_u.valueChanged.connect(lambda *_: self.planner_dev_edit("u"))
        self.pl_d_w = QSpinBox()
        self.pl_d_w.setRange(0, 30000)
        self.pl_d_w.setSingleStep(50)
        self.pl_d_w.setSuffix(" W")
        self.pl_d_w.valueChanged.connect(lambda *_: self.planner_dev_edit("w"))
        self.pl_d_w_label = QLabel(T("Leistung"))
        self.pl_d_pos = QLabel()
        self.pl_d_serial = QLabel()
        self.pl_d_serial.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pl_d_note = QLabel()
        self.pl_d_note.setWordWrap(True)
        self.pl_d_note.setStyleSheet("color: palette(mid);")
        self.pl_d_ports = QTableWidget(0, 2)
        self.pl_d_ports.setHorizontalHeaderLabels([T("Port"), T("Verbunden mit")])
        self.pl_d_ports.verticalHeader().setVisible(False)
        self.pl_d_ports.horizontalHeader().setStretchLastSection(True)
        self.pl_d_ports.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pl_d_ports.setMinimumHeight(160)
        box, f = _group(T("Allgemein"))
        f.addRow(T("Name"), self.pl_d_name)
        f.addRow(T("Modell"), self.pl_d_model)
        f.addRow(T("Seriennummer"), self.pl_d_serial)
        f.addRow(self.pl_d_note)
        lay.addWidget(box)
        box, f = _group(T("Einbau"))
        f.addRow(T("Position"), self.pl_d_pos)
        f.addRow(T("Einbauhöhe"), self.pl_d_u)
        lay.addWidget(box)
        box, f = _group(T("Strom"))
        f.addRow(self.pl_d_w_label, self.pl_d_w)
        lay.addWidget(box)
        box, f = _group(T("Anschlüsse"))
        f.addRow(self.pl_d_ports)
        lay.addWidget(box, 1)
        row = QHBoxLayout()
        self.pl_d_unplace = QPushButton(T("Ausbauen"))
        self.pl_d_unplace.setToolTip(T("Nimmt das Gerät aus dem Rack und zieht seine Kabel ab (Entf)."))
        self.pl_d_unplace.clicked.connect(self.planner_delete_selected)
        row.addWidget(self.pl_d_unplace)
        row.addStretch(1)
        lay.addLayout(row)
        self.pl_props.addWidget(w)
        # 2: Kabel
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, f = _group(T("Verbindung"))
        lay.addWidget(box)
        lay.addStretch(1)
        self.pl_c_from, self.pl_c_to, self.pl_c_med, self.pl_c_len, self.pl_c_tag = (QLabel() for _ in range(5))
        self.pl_c_tag.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pl_c_net = QComboBox()
        for k, name in net_names().items():
            self.pl_c_net.addItem(name, k)
        self.pl_c_net.currentIndexChanged.connect(self.planner_cable_net)
        f.addRow(T("Von"), self.pl_c_from)
        f.addRow(T("Nach"), self.pl_c_to)
        f.addRow(T("Netz"), self.pl_c_net)
        f.addRow(T("Medium"), self.pl_c_med)
        f.addRow(T("Länge"), self.pl_c_len)
        f.addRow(T("Etikett"), self.pl_c_tag)
        b = QPushButton(T("Kabel entfernen"))
        b.clicked.connect(self.planner_delete_selected)
        f.addRow(b)
        self.pl_props.addWidget(w)
        # 3: Rack
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, f = _group(T("Rack"))
        lay.addWidget(box)
        lay.addStretch(1)
        self.pl_r_name = QLineEdit()
        self.pl_r_name.setMaxLength(20)
        self.pl_r_name.editingFinished.connect(self.planner_rack_edit)
        self.pl_r_h = QSpinBox()
        self.pl_r_h.setRange(MIN_RACK_U, MAX_RACK_U)
        self.pl_r_h.setSuffix(" " + T("HE"))
        self.pl_r_h.valueChanged.connect(self.planner_rack_edit)
        self.pl_r_load = QLabel()
        f.addRow(T("Name"), self.pl_r_name)
        f.addRow(T("Höhe"), self.pl_r_h)
        f.addRow(T("Belegung"), self.pl_r_load)
        b = QPushButton(T("Rack entfernen"))
        b.setToolTip(T("Nur leere Racks lassen sich entfernen."))
        b.clicked.connect(self.planner_delete_selected)
        f.addRow(b)
        self.pl_props.addWidget(w)
        return self.pl_props

    def _planner_nets(self):
        w = QWidget()
        v = QVBoxLayout(w)
        f = QFormLayout()
        self.pl_n_ip, self.pl_n_mask, self.pl_n_gw, self.pl_n_schema = (QLineEdit() for _ in range(4))
        self.pl_n_schema.setToolTip(T("Platzhalter: {rack} = Rackname, {pos} = unterste HE, {name} = Gerätename"))
        for key, edit in (("ip_start", self.pl_n_ip), ("mask", self.pl_n_mask), ("gw", self.pl_n_gw),
                          ("schema", self.pl_n_schema)):
            edit.editingFinished.connect(lambda k=key, e=edit: self.planner_net_edit(k, e))
        f.addRow(T("Erste BMC-IP"), self.pl_n_ip)
        f.addRow(T("Subnetzmaske"), self.pl_n_mask)
        f.addRow(T("Gateway"), self.pl_n_gw)
        f.addRow(T("Hostname"), self.pl_n_schema)
        v.addLayout(f)
        self.pl_n_table = QTableWidget(0, 4)
        self.pl_n_table.setHorizontalHeaderLabels([T("Server"), T("Hostname"), T("BMC-IP"), T("Switchport")])
        self.pl_n_table.verticalHeader().setVisible(False)
        self.pl_n_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pl_n_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.pl_n_table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.pl_n_table, 1)
        b = QPushButton(T("In „Neuware“ übernehmen"))
        b.setToolTip(T("Trägt Hostname, neue IP, Subnetzmaske und Gateway in die Serverliste ein; Platzhalter "
                       "kommen als neue Zeilen dazu. Geändert wird erst beim Lauf in „Neuware“."))
        b.clicked.connect(self.planner_to_neuware)
        v.addWidget(b)
        b = QPushButton(T("Ins Testlabor übernehmen"))
        b.setToolTip(T("Ersetzt die Serverzeilen im Testlabor durch die eingebauten Server (Hersteller, Modell, "
                       "Seriennummer). Switches und Kabel bildet das Testlabor nicht nach."))
        b.clicked.connect(self.planner_to_lab)
        v.addWidget(b)
        return w

    def _planner_bom(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.pl_bom = QTableWidget(0, 4)
        self.pl_bom.setHorizontalHeaderLabels([T("Gruppe"), T("Artikel"), T("Länge"), T("Anzahl")])
        self.pl_bom.verticalHeader().setVisible(False)
        self.pl_bom.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pl_bom.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.pl_bom.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.pl_bom, 1)
        note = QLabel(T("Kabellängen sind geschätzt (HE-Abstand, zwischen Racks über den Kabelkanal) und auf "
                        "Handelslängen aufgerundet."))
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        v.addWidget(note)
        b = QPushButton(T("Als CSV exportieren …"))
        b.clicked.connect(self.planner_export_csv)
        v.addWidget(b)
        return w

    # ================================================================ Zustand
    @property
    def plan(self):
        return self.pl_canvas.plan

    def planner_set_plan(self, plan, dirty=True):
        self.planner_sim_stop()
        self.pl_canvas.plan = plan
        self.pl_canvas.sel = None
        self.pl_canvas.pending = None
        plan.sync_inventory(self.planner_inventory())
        self.plan_dirty = dirty
        self.planner_refresh()

    def planner_inventory(self):
        """Server der Serverliste („Neuware“) als Bestand: Schlüssel Seriennummer, sonst aktuelle IP, sonst Name."""
        out = []
        table = getattr(self, "table", None)
        if table is None:
            return out
        for r in range(table.rowCount()):
            ip, host, serial = self.cell(r, 0), self.cell(r, 5), self.cell(r, 6)
            key = f"sn:{serial}" if serial else f"ip:{ip}" if ip else f"host:{host}" if host else ""
            if not key:
                continue
            vtext = self.cell(r, self.vendor_col)
            out.append({"key": key, "name": host or ip or serial, "vendor": vendor_of(vtext), "model": vtext,
                        "serial": serial, "row": r})
        return out

    def planner_page_shown(self):
        """Beim Öffnen der Seite die Serverliste neu einlesen (sie kann sich in „Neuware“ geändert haben)."""
        self.plan.sync_inventory(self.planner_inventory())
        self.planner_refresh()

    def planner_changed(self):
        self.plan_dirty = True
        if self.pl_canvas.sim is not None:
            self.planner_sim_recompute(log=False)
        self.planner_refresh()

    def planner_refresh(self):
        self.pl_canvas.relayout()
        self.planner_fill_lib()
        self.planner_fill_checks()
        self.planner_fill_nets()
        self.planner_fill_bom()
        self.planner_fill_tree()
        self.planner_fill_objects()
        self.planner_selection_changed()

    def planner_selection_changed(self):
        self.planner_fill_props()
        self.planner_fill_crumb()

    # ================================================================ Ansicht, Navigator, Objektliste
    def planner_set_view(self, view):
        self.pl_canvas.view = view
        self.pl_canvas.pending = None
        for key, btn in self.pl_view_btns.items():
            btn.setChecked(key == view)
        if view == "front" and self.pl_cable_btn.isChecked():
            self.pl_cable_btn.setChecked(False)
        self.pl_canvas.update()

    def planner_loc_edit(self, key, edit):
        val = edit.text().strip()
        if getattr(self.plan, key) != val:
            setattr(self.plan, key, val)
            self.plan_dirty = True
            self.planner_fill_tree()
            self.planner_fill_crumb()

    def planner_location(self):
        p = self.plan
        return [p.site or T("Standort"), p.room or T("Raum"), T("Reihe {0}").format(p.row) if p.row else T("Reihe")]

    def planner_fill_crumb(self):
        parts = self.planner_location()
        sel = self.pl_canvas.sel
        plan = self.plan
        rid = None
        if sel and sel[0] == "rack":
            rid = sel[1]
        elif sel and sel[0] == "dev" and plan.dev(sel[1]):
            rid = plan.dev(sel[1]).get("rack")
        elif sel and sel[0] == "cable" and plan.cable(sel[1]):
            rid = plan.dev(plan.cable(sel[1])["a"][0]).get("rack")
        if rid and plan.rack(rid):
            parts.append(T("Rack {0}").format(plan.rack(rid)["name"]))
        if sel and sel[0] == "dev" and plan.dev(sel[1]):
            d = plan.dev(sel[1])
            parts.append(f"{T('HE')} {d['pos']} · {d['name']}" if d.get("rack") else d["name"])
        elif sel and sel[0] == "cable" and plan.cable(sel[1]):
            c = plan.cable(sel[1])
            parts.append(T("Kabel {0} ↔ {1}").format(plan.dev(c["a"][0])["name"], plan.dev(c["b"][0])["name"]))
        self.pl_crumb.setText("  ›  ".join(parts))

    def planner_fill_tree(self):
        tree = self.pl_tree
        tree.clear()
        style = self.style()
        site, room, row = self.planner_location()
        top = QTreeWidgetItem([site])
        top.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon))
        r_item = QTreeWidgetItem(top, [room])
        r_item.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        row_item = QTreeWidgetItem(r_item, [row])
        row_item.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        for r in self.plan.racks:
            L = self.plan.rack_load(r["id"])
            it = QTreeWidgetItem(row_item, [f"{r['name']}  ({L['used']}/{r['h']} {T('HE')})"])
            it.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon))
            it.setData(0, Qt.ItemDataRole.UserRole, f"rack:{r['id']}")
            devs = sorted((d for d in self.plan.devs if d.get("rack") == r["id"]), key=lambda d: -d["pos"])
            for d in devs:
                child = QTreeWidgetItem(it, [f"{T('HE')} {d['pos']:>2}  {d['name']}"])
                child.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon if d["kind"] == "server"
                                                    else QStyle.StandardPixmap.SP_DriveNetIcon))
                child.setData(0, Qt.ItemDataRole.UserRole, f"dev:{d['id']}")
        tree.addTopLevelItem(top)
        tree.expandToDepth(3)
        for i in range(row_item.childCount()):
            rack_item = row_item.child(i)
            rack_item.setExpanded(rack_item.childCount() <= 14)

    def planner_tree_clicked(self, item, _col=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            kind, _, ident = data.partition(":")
            self.planner_select(kind, ident)

    def planner_select(self, kind, ident):
        """Objekt auswählen und in der Rack-Ansicht sichtbar machen."""
        cv = self.pl_canvas
        cv.select((kind, ident))
        if kind == "dev" and self.plan.dev(ident) and self.plan.dev(ident).get("rack"):
            r = cv.dev_rect(self.plan.dev(ident))
        elif kind == "rack" and self.plan.rack(ident):
            i = self.plan.rack_index(ident)
            r = QRectF(cv.rack_x(i), cv.M, cv.RW, cv.HEAD)
        else:
            return
        self.pl_scroll.ensureVisible(int(r.center().x()), int(r.center().y()), 120, 140)
        self.pl_tabs.setCurrentIndex(0)

    def planner_kind_name(self, d):
        return {"server": T("Server"), "switch": T("Switch"), "fw": T("Firewall"), "patch": T("Patchpanel"),
                "pdu": T("PDU"), "blank": T("Blindblende")}.get(d["kind"], d["kind"])

    def planner_fill_objects(self):
        plan = self.plan
        worst = {}
        order = {"err": 0, "warn": 1, "info": 2}
        for level, _, did in plan.checks():
            if did and order[level] < order.get(worst.get(did), 9):
                worst[did] = level
        status = {"err": T("Fehler"), "warn": T("Warnung"), "info": T("Hinweis")}
        devs = sorted(plan.placed(), key=lambda d: (plan.rack_index(d["rack"]), -d["pos"]))
        t = self.pl_objects
        t.setSortingEnabled(False)
        t.setRowCount(len(devs))
        for i, d in enumerate(devs):
            used = sum(1 for p in d["ports"] if plan.cable_at(d["id"], p["id"]))
            power = d.get("cap") if d["kind"] == "pdu" else d.get("w")
            vals = [d["name"], self.planner_kind_name(d), d.get("model") or "", plan.rack(d["rack"])["name"],
                    d["pos"], d["u"], f"{power} W" if power else "", f"{used}/{len(d['ports'])}" if d["ports"] else "",
                    status.get(worst.get(d["id"]), T("OK"))]
            for c, val in enumerate(vals):
                it = QTableWidgetItem()
                it.setData(Qt.ItemDataRole.DisplayRole, val)
                it.setData(Qt.ItemDataRole.UserRole, d["id"])
                if c == 8 and d["id"] in worst:
                    it.setForeground(QColor(LEVEL_COLOR[worst[d["id"]]]))
                t.setItem(i, c, it)
        t.setSortingEnabled(True)
        self.pl_bottom.setTabText(0, T("Objekte ({0})").format(len(devs)))
        self.planner_filter_objects(self.pl_filter.text())

    def planner_filter_objects(self, text):
        text = (text or "").strip().lower()
        t = self.pl_objects
        for r in range(t.rowCount()):
            row = " ".join(t.item(r, c).text().lower() for c in range(4) if t.item(r, c))
            t.setRowHidden(r, bool(text) and text not in row)

    def planner_object_clicked(self, row, _col=0):
        item = self.pl_objects.item(row, 0)
        if item is not None:
            self.planner_select("dev", item.data(Qt.ItemDataRole.UserRole))

    def planner_say(self, text, err=False):
        self.pl_status.setText(text)
        self.pl_status.setStyleSheet("color: #d9534f;" if err else "")

    # ================================================================ Bibliothek
    def planner_fill_lib(self):
        lib = self.pl_lib
        lib.clear()

        def header(text):
            it = QListWidgetItem(text)
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setBackground(self.palette().alternateBase())
            lib.addItem(it)

        inv = [d for d in self.plan.devs if d.get("inv")]
        open_inv = [d for d in inv if not d.get("rack")]
        header(T("Serverliste ({0} offen · {1} eingebaut)").format(len(open_inv), len(inv) - len(open_inv)))
        if not inv:
            it = QListWidgetItem(T("Leer – Server zuerst in „Neuware“ eintragen oder per DHCP übernehmen."))
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            lib.addItem(it)
        for d in open_inv:
            it = QListWidgetItem(f"{d['name']}\n{d.get('model') or T('Hersteller unbekannt')} · {d['u']} " + T("HE"))
            it.setData(Qt.ItemDataRole.UserRole, f"inv:{d['id']}")
            it.setToolTip(T("In ein Rack ziehen; die Einbauhöhe lässt sich danach rechts ändern."))
            lib.addItem(it)
        for group, entries in catalog_texts():
            header(group)
            for key, name, sub in entries:
                it = QListWidgetItem(f"{name}\n{sub} · {CATALOG[key]['u']} " + T("HE"))
                it.setData(Qt.ItemDataRole.UserRole, f"cat:{key}")
                lib.addItem(it)

    def planner_lib_double(self, item):
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, _, key = payload.partition(":")
        rid = self.planner_target_rack()
        if rid is None:
            self.planner_say(T("Zuerst ein Rack anlegen."), True)
            return
        d = self.plan.new_device(key) if kind == "cat" else self.plan.dev(key)
        pos = self.plan.free_pos(rid, d["u"], top=d["kind"] != "pdu")
        if pos is None or not self.plan.place(d, rid, pos):
            self.planner_say(T("Im Rack {0} ist nicht genug Platz.").format(self.plan.rack(rid)["name"]), True)
            return
        self.pl_canvas.sel = ("dev", d["id"])
        self.planner_changed()

    def planner_target_rack(self):
        """Gewähltes Rack bzw. das Rack des gewählten Geräts, sonst das erste."""
        sel = self.pl_canvas.sel
        if sel and sel[0] == "rack" and self.plan.rack(sel[1]):
            return sel[1]
        if sel and sel[0] == "dev":
            d = self.plan.dev(sel[1])
            if d and d.get("rack"):
                return d["rack"]
        return self.plan.racks[0]["id"] if self.plan.racks else None

    # ================================================================ Werkzeugleiste
    def planner_add_rack(self):
        r = self.plan.add_rack(h=42)
        self.pl_canvas.sel = ("rack", r["id"])
        self.planner_changed()
        self.pl_scroll.horizontalScrollBar().setValue(self.pl_scroll.horizontalScrollBar().maximum())

    def planner_cable_mode(self, on):
        if on and self.pl_sim_btn.isChecked():
            self.pl_sim_btn.setChecked(False)
        if on and self.pl_canvas.view != "rear":
            self.planner_set_view("rear")
        self.pl_canvas.cable_mode = on
        self.pl_canvas.pending = None
        self.pl_canvas.update()
        self.planner_say(T("Verkabeln: Port anklicken, dann den Gegenport.") if on else "")

    def planner_template(self, key):
        made, err = self.plan.apply_template(key, self.planner_target_rack())
        if err:
            self.planner_say(err, True)
            return
        self.planner_say(T("Vorlage eingefügt: {0} Geräte.").format(len(made)))
        self.planner_changed()

    def planner_auto_cable(self):
        n = self.plan.auto_cable()
        if n:
            self.planner_say(T("{0} Kabel automatisch gezogen.").format(n))
            self.planner_changed()
        else:
            self.planner_say(T("Nichts zu verkabeln – fehlt ein OOB- oder Leaf-Switch, oder sind alle Ports "
                               "belegt?"), True)

    def planner_delete_selected(self):
        sel = self.pl_canvas.sel
        if not sel:
            return
        kind, ident = sel
        if kind == "cable":
            self.plan.disconnect(ident)
        elif kind == "dev":
            d = self.plan.dev(ident)
            if d:
                self.plan.unplace(d)
        elif kind == "rack":
            if not self.plan.remove_rack(ident):
                self.planner_say(T("Das Rack ist nicht leer – erst die Geräte ausbauen."), True)
                return
        self.pl_canvas.sel = None
        self.planner_changed()

    # ================================================================ Eigenschaften
    def planner_fill_props(self):
        self._pl_filling = True
        try:
            sel = self.pl_canvas.sel
            plan = self.plan
            if sel and sel[0] == "dev" and plan.dev(sel[1]) and plan.dev(sel[1]).get("rack"):
                self._planner_fill_dev(plan.dev(sel[1]))
            elif sel and sel[0] == "cable" and plan.cable(sel[1]):
                self._planner_fill_cable(plan.cable(sel[1]))
            elif sel and sel[0] == "rack" and plan.rack(sel[1]):
                r = plan.rack(sel[1])
                L = plan.rack_load(r["id"])
                self.pl_r_name.setText(r["name"])
                self.pl_r_h.setValue(r["h"])
                self.pl_r_load.setText(T("{0} von {1} HE · {2} W Last · {3} W PDU").format(
                    L["used"], r["h"], L["w"], L["cap"]))
                self.pl_props.setCurrentIndex(3)
            else:
                self.pl_canvas.sel = None
                for key, edit in self.pl_loc.items():
                    if not edit.hasFocus():
                        edit.setText(getattr(plan, key))
                devs = plan.placed()
                warn = sum(1 for c in plan.checks() if c[0] != "info")
                self.pl_summary.setText(T(
                    "<p><b>{0}</b> Racks · <b>{1}</b> Geräte · <b>{2}</b> Kabel · <b>{3}</b> Hinweise</p>"
                    "<p><b>So geht's</b><br>1. Server und Switches aus der Bibliothek in ein Rack ziehen.<br>"
                    "2. Gerät oder Rack anklicken: Name, Einbauhöhe, Rackhöhe hier ändern.<br>"
                    "3. „Verkabeln“ (Taste K): Port anklicken, dann den Gegenport.<br>"
                    "4. „Prüfung“ zeigt Lücken, „Netze“ vergibt Hostnamen und Adressen.<br>"
                    "5. „Ausfall simulieren“ zeigt, welche Server ein gezogenes Kabel trifft.</p>").format(
                    len(plan.racks), len(devs), len(plan.cables), warn))
                self.pl_props.setCurrentIndex(0)
        finally:
            self._pl_filling = False

    def _planner_fill_dev(self, d):
        plan = self.plan
        self.pl_d_name.setText(d["name"])
        self.pl_d_name.setReadOnly(bool(d.get("inv")))
        self.pl_d_model.setText(d.get("model") or "")
        self.pl_d_u.setValue(d["u"])
        pdu = d["kind"] == "pdu"
        self.pl_d_w_label.setText(T("Anschlussleistung") if pdu else T("Leistung"))
        self.pl_d_w.setValue(d.get("cap") if pdu else d.get("w") or 0)
        r = plan.rack(d["rack"])
        span = f"{d['pos']}–{d['pos'] + d['u'] - 1}" if d["u"] > 1 else str(d["pos"])
        self.pl_d_pos.setText(T("Rack {0} · HE {1}").format(r["name"], span))
        self.pl_d_serial.setText(d.get("serial") or "–")
        self.pl_d_note.setText(
            T("Aus der Serverliste: Der Name kommt von dort (Hostname bzw. Adresse).") if d.get("inv") else
            T("Platzhalter: Seriennummer und Hersteller kommen später mit der Lieferung.") if d["kind"] == "server"
            else "")
        self.pl_d_unplace.setText(T("Ausbauen") if d.get("inv") else T("Entfernen"))
        ports = d["ports"]
        self.pl_d_ports.setRowCount(len(ports))
        for i, p in enumerate(ports):
            c = plan.cable_at(d["id"], p["id"])
            peer = ""
            if c:
                od, op = plan.other_end(c, d["id"], p["id"])
                peer = f"{plan.dev(od)['name']} · {port_label(op)}"
            a = QTableWidgetItem(port_label(p["id"]))
            if c:
                a.setForeground(NET_COLOR[c["net"]].darker(115))
            self.pl_d_ports.setItem(i, 0, a)
            self.pl_d_ports.setItem(i, 1, QTableWidgetItem(peer or T("frei")))
        self.pl_props.setCurrentIndex(1)

    def _planner_fill_cable(self, c):
        plan = self.plan
        A, B = plan.dev(c["a"][0]), plan.dev(c["b"][0])
        self.pl_c_from.setText(f"{A['name']} · {port_label(c['a'][1])}")
        self.pl_c_to.setText(f"{B['name']} · {port_label(c['b'][1])}")
        self.pl_c_net.setCurrentIndex(max(0, self.pl_c_net.findData(c["net"])))
        self.pl_c_med.setText(plan.medium(c["net"]))
        self.pl_c_len.setText(T("{0} m (geschätzt)").format(f"{plan.cable_length(c):g}".replace(".", ",")))
        tag = lambda d, pid: f"{d['name']}/{port_label(pid).replace(' ', '')}"
        self.pl_c_tag.setText(f"{tag(A, c['a'][1])} ↔ {tag(B, c['b'][1])}")
        self.pl_props.setCurrentIndex(2)

    def planner_dev_edit(self, field):
        if self._pl_filling:
            return
        sel = self.pl_canvas.sel
        d = self.plan.dev(sel[1]) if sel and sel[0] == "dev" else None
        if not d:
            return
        if field == "name" and not d.get("inv"):
            name = self.pl_d_name.text().strip()[:40]
            if name and name != d["name"]:
                d["name"] = name
                self.planner_changed()
        elif field == "model":
            d["model"] = self.pl_d_model.text().strip()[:60]
            self.planner_changed()
        elif field == "u":
            u = self.pl_d_u.value()
            if u != d["u"]:
                if self.plan.set_height(d, u):
                    self.planner_changed()
                else:
                    self.planner_say(T("{0} HE passen hier nicht – darüber ist belegt. Erst umsetzen.").format(u),
                                     True)
                    self._pl_filling = True
                    self.pl_d_u.setValue(d["u"])
                    self._pl_filling = False
        elif field == "w":
            d["cap" if d["kind"] == "pdu" else "w"] = self.pl_d_w.value()
            self.plan_dirty = True
            self.pl_canvas.update()
            self.planner_fill_checks()

    def planner_cable_net(self, *_):
        if self._pl_filling:
            return
        sel = self.pl_canvas.sel
        c = self.plan.cable(sel[1]) if sel and sel[0] == "cable" else None
        if c and self.pl_c_net.currentData() in NETS:
            c["net"] = self.pl_c_net.currentData()
            self.planner_changed()

    def planner_rack_edit(self, *_):
        if self._pl_filling:
            return
        sel = self.pl_canvas.sel
        r = self.plan.rack(sel[1]) if sel and sel[0] == "rack" else None
        if not r:
            return
        name = self.pl_r_name.text().strip()
        if name and name != r["name"]:
            r["name"] = name
        h = self.pl_r_h.value()
        if h != r["h"]:
            over = self.plan.set_rack_height(r["id"], h)
            if over:
                self.planner_say(T("{0} sitzt über HE {1} – erst umsetzen.").format(
                    ", ".join(d["name"] for d in over), h), True)
        self.planner_changed()

    # ================================================================ Prüfung, Netze, Stückliste
    def planner_fill_checks(self):
        checks = self.plan.checks()
        self.pl_checks.clear()
        for level, text, did in checks:
            it = QListWidgetItem(f"{LEVEL_MARK[level]}  {text}")
            it.setForeground(QColor(LEVEL_COLOR[level]))
            it.setData(Qt.ItemDataRole.UserRole, did)
            self.pl_checks.addItem(it)
        if not checks:
            it = QListWidgetItem("✓  " + T("Keine Auffälligkeiten"))
            it.setForeground(QColor("#3fa45b"))
            self.pl_checks.addItem(it)
        bad = sum(1 for c in checks if c[0] != "info")
        self.pl_tabs.setTabText(1, T("Prüfung ({0})").format(bad) if bad else T("Prüfung"))

    def planner_check_clicked(self, item):
        did = item.data(Qt.ItemDataRole.UserRole)
        d = self.plan.dev(did) if did else None
        if d and d.get("rack"):
            self.planner_select("dev", did)

    def planner_fill_nets(self):
        p = self.plan
        for edit, val in ((self.pl_n_ip, p.ip_start), (self.pl_n_mask, p.mask), (self.pl_n_gw, p.gw),
                          (self.pl_n_schema, p.schema)):
            if not edit.hasFocus():
                edit.setText(val)
        rows = p.net_plan()
        t = self.pl_n_table
        t.setRowCount(len(rows))
        for i, n in enumerate(rows):
            vals = [n["dev"]["name"], n["host"], n["ip"] or "–", n["port"] or T("fehlt")]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 3 and not n["port"]:
                    it.setForeground(QColor("#c77700"))
                t.setItem(i, c, it)

    def planner_net_edit(self, key, edit):
        val = edit.text().strip()
        if getattr(self.plan, key) != val:
            setattr(self.plan, key, val)
            self.plan_dirty = True
            self.planner_fill_nets()
            self.planner_fill_checks()

    def planner_fill_bom(self):
        rows = self.plan.bom()
        self.pl_bom.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, val in enumerate(row):
                it = QTableWidgetItem(str(val))
                if c == 3:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.pl_bom.setItem(i, c, it)

    def planner_export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, T("Stückliste exportieren"), str(HERE / "stueckliste.csv"),
                                              T("CSV-Datei (*.csv)"))
        if not path:
            return
        lines = [";".join([T("Gruppe"), T("Artikel"), T("Länge"), T("Anzahl")])]
        lines += [";".join(str(x).replace(";", ",") for x in row) for row in self.plan.bom()]
        lines += ["", ";".join([T("Rack"), T("HE"), T("Gerät"), T("Modell"), T("Einbauhöhe")])]
        for d in sorted(self.plan.placed(), key=lambda d: (self.plan.rack_index(d["rack"]), -d["pos"])):
            lines.append(";".join([self.plan.rack(d["rack"])["name"], str(d["pos"]), d["name"],
                                   (d.get("model") or "").replace(";", ","), str(d["u"])]))
        try:
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        except OSError as e:
            QMessageBox.warning(self, T("Stückliste exportieren"), T("Datei nicht schreibbar:\n{0}").format(e))
            return
        self.planner_say(T("Stückliste gespeichert: {0}").format(path))

    # ================================================================ Übergabe an Neuware und Testlabor
    def planner_to_neuware(self):
        import ipaddress
        rows = self.plan.net_plan()
        if not rows:
            self.planner_say(T("Noch kein Server eingebaut."), True)
            return
        try:
            ipaddress.IPv4Address(self.plan.ip_start)
            ipaddress.IPv4Network(f"0.0.0.0/{self.plan.mask}")
            if self.plan.gw:
                ipaddress.IPv4Address(self.plan.gw)
        except ValueError:
            QMessageBox.warning(self, T("In „Neuware“ übernehmen"),
                                T("Erste BMC-IP, Subnetzmaske oder Gateway sind ungültig."))
            return
        box = QMessageBox(QMessageBox.Icon.Question, T("In „Neuware“ übernehmen"),
                          T("{0} Server bekommen in der Serverliste Hostname, neue IP, Subnetzmaske und Gateway "
                            "aus dem Plan. Vorhandene Einträge in diesen Spalten werden überschrieben.").format(
                              len(rows)), parent=self)
        ok = box.addButton(T("Übernehmen"), QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not ok:
            return
        by_key = {e["key"]: e["row"] for e in self.planner_inventory()}
        updated = added = 0
        for n in rows:
            d = n["dev"]
            vals = {2: n["ip"] or "", 3: self.plan.mask, 4: self.plan.gw, 5: n["host"]}
            r = by_key.get(d.get("key")) if d.get("inv") else None
            if r is None:
                values = [""] * len(i18n.COLS)
                values[1] = self.cur_pass.text()
                for c, val in vals.items():
                    values[c] = val
                values[6] = d.get("serial") or ""
                self.add_row(values)
                added += 1
            else:
                for c, val in vals.items():
                    item = self.table.item(r, c)
                    if item is None:
                        self.table.setItem(r, c, QTableWidgetItem(val))
                    else:
                        item.setText(val)
                updated += 1
        self.update_count()
        self.touch()
        self.planner_page_shown()
        LOG.info(T("Aufbauplaner: {0} Zeilen in „Neuware“ aktualisiert, {1} neu angelegt.").format(updated, added))
        self.tabs.setCurrentIndex(1)

    def planner_to_lab(self):
        servers = sorted(self.plan.placed("server"), key=lambda d: (self.plan.rack_index(d["rack"]), -d["pos"]))
        known = [d for d in servers if d.get("vendor") in ("dell", "hpe", "lenovo", "smc")]
        if not known:
            self.planner_say(T("Kein eingebauter Server mit bekanntem Hersteller – Platzhalter lassen sich nicht "
                               "virtuell nachbauen."), True)
            return
        if self.vbmc_running():
            QMessageBox.information(self, T("Ins Testlabor übernehmen"),
                                    T("Das Testlabor läuft gerade. Bitte erst stoppen."))
            return
        if self.vbmc_fleet.rowCount():
            box = QMessageBox(QMessageBox.Icon.Question, T("Ins Testlabor übernehmen"),
                              T("Die {0} Serverzeilen im Testlabor werden durch {1} Server aus dem Plan "
                                "ersetzt.").format(self.vbmc_fleet.rowCount(), len(known)), parent=self)
            ok = box.addButton(T("Ersetzen"), QMessageBox.ButtonRole.AcceptRole)
            cancel = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() is not ok:
                return
        self.vbmc_fleet.setRowCount(0)
        serials = set()
        for d in known:
            serial = d.get("serial") or None
            if serial in serials:
                serial = None
            serials.add(serial)
            self.vbmc_add_server(d["vendor"], {"serial": serial, "model": d.get("model") or None})
        self.vbmc_mode_changed()
        skipped = len(servers) - len(known)
        msg = T("Aus dem Aufbauplan übernommen: {0} Server").format(len(known))
        if skipped:
            msg += " " + T("({0} Platzhalter ohne Hersteller übersprungen)").format(skipped)
        self.vbmc_set_status(msg)
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.vbmc_page))

    # ================================================================ Ausfall-Simulation
    def planner_sim_toggle(self, on):
        cv = self.pl_canvas
        if on:
            if self.pl_cable_btn.isChecked():
                self.pl_cable_btn.setChecked(False)
            cv.broken, cv.failed = set(), set()
            cv.sel = None
            self.planner_set_view("rear")
            self.pl_simlog.clear()
            self.pl_bottom.setCurrentIndex(1)
            self.planner_fill_crumb()
            self.planner_sim_recompute(log=False)
            self.planner_sim_log(T("Simulation: Kabel oder Gerät anklicken, um es ausfallen zu lassen; nochmal "
                                   "klicken stellt es wieder her. Der Plan bleibt unverändert."))
            self.planner_sim_summary()
        else:
            cv.sim = None
            cv.broken, cv.failed = set(), set()
            self.pl_bottom.setCurrentIndex(0)
            self.planner_say("")
            self.planner_fill_crumb()
            cv.update()

    def planner_sim_stop(self):
        if getattr(self, "pl_sim_btn", None) is not None and self.pl_sim_btn.isChecked():
            self.pl_sim_btn.setChecked(False)

    def planner_sim_log(self, text):
        from datetime import datetime
        self.pl_simlog.appendPlainText(f"{datetime.now():%H:%M:%S}  {text}")

    def planner_sim_recompute(self, log=True):
        cv = self.pl_canvas
        before = dict(cv.sim or {})
        cv.sim = self.plan.simulate(cv.broken, cv.failed)
        if log:
            words = {"ok": T("wieder voll erreichbar"), "degraded": T("eingeschränkt – Redundanz greift"),
                     "down": T("nicht mehr erreichbar")}
            changed = False
            for d in self.plan.placed("server"):
                s = cv.sim.get(d["id"])
                if before.get(d["id"]) != s:
                    self.planner_sim_log(f"  → {d['name']}: {words[s]}")
                    changed = True
            if not changed:
                self.planner_sim_log("  → " + T("keine Auswirkung auf die Server"))
        cv.update()
        self.planner_sim_summary()

    def planner_sim_summary(self):
        cv = self.pl_canvas
        servers = self.plan.placed("server")
        up = sum(1 for d in servers if cv.sim.get(d["id"]) != "down")
        deg = sum(1 for d in servers if cv.sim.get(d["id"]) == "degraded")
        self.planner_say(T("Simulation: {0} von {1} Servern erreichbar, {2} davon eingeschränkt.").format(
            up, len(servers), deg), up < len(servers))

    def planner_sim_click(self, kind, ident):
        cv = self.pl_canvas
        if kind == "cable":
            c = self.plan.cable(ident)
            A, B = self.plan.dev(c["a"][0]), self.plan.dev(c["b"][0])
            what = f"{A['name']} · {port_label(c['a'][1])} ↔ {B['name']} · {port_label(c['b'][1])}"
            if ident in cv.broken:
                cv.broken.discard(ident)
                self.planner_sim_log(T("Kabel wieder gesteckt: {0}").format(what))
            else:
                cv.broken.add(ident)
                self.planner_sim_log(T("Kabel gezogen: {0}").format(what))
        else:
            d = self.plan.dev(ident)
            if ident in cv.failed:
                cv.failed.discard(ident)
                self.planner_sim_log(T("Gerät läuft wieder: {0}").format(d["name"]))
            else:
                cv.failed.add(ident)
                self.planner_sim_log(T("Gerät ausgefallen: {0}").format(d["name"]))
        self.planner_sim_recompute()

    # ================================================================ Datei
    def planner_confirm_discard(self):
        """True, wenn der aktuelle Plan verworfen werden darf (gespeichert, leer oder bestätigt)."""
        if not self.plan_dirty or not (self.plan.placed() or self.plan.cables):
            return True
        box = QMessageBox(QMessageBox.Icon.Question, T("Aufbauplaner"), T("Der Plan wurde nicht gespeichert."),
                          parent=self)
        box.setInformativeText(T("Das Tool speichert nichts von selbst. Ohne Speichern ist der Plan weg."))
        b_save = box.addButton(T("Speichern …"), QMessageBox.ButtonRole.AcceptRole)
        b_drop = box.addButton(T("Verwerfen"), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is b_save:
            return self.planner_save()
        return box.clickedButton() is b_drop

    def planner_new(self):
        if not self.planner_confirm_discard():
            return
        plan = Plan()
        plan.add_rack("R01", 42)
        self.plan_path = None
        self.planner_set_plan(plan, dirty=False)
        self.planner_say(T("Neuer Plan mit einem 42-HE-Rack."))

    def planner_demo(self):
        if not self.planner_confirm_discard():
            return
        self.plan_path = None
        self.planner_set_plan(demo_plan(self.planner_inventory()), dirty=False)
        self.planner_say(T("Beispielaufbau geladen – die ersten Server der Serverliste sind eingebaut."))

    def planner_save(self):
        start = self.plan_path or str(HERE / "aufbauplan.json")
        path, _ = QFileDialog.getSaveFileName(self, T("Plan speichern"), start, T("Aufbauplan (*.json)"))
        if not path:
            return False
        try:
            Path(path).write_text(json.dumps(self.plan.to_dict(), indent=1, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, T("Plan speichern"), T("Datei nicht schreibbar:\n{0}").format(e))
            return False
        self.plan_path = path
        self.plan_dirty = False
        self.planner_say(T("Plan gespeichert: {0}").format(path))
        LOG.info(T("Aufbauplan gespeichert: {0}").format(path))
        return True

    def planner_load(self, path=None):
        if not self.planner_confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, T("Plan laden"), self.plan_path or str(HERE),
                                                  T("Aufbauplan (*.json);;Alle Dateien (*)"))
            if not path:
                return
        try:
            plan = Plan.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("Plan laden"), T("Datei nicht lesbar:\n{0}").format(e))
            return
        self.plan_path = str(path)
        self.planner_set_plan(plan, dirty=False)
        self.planner_say(T("Plan geladen: {0}").format(path))
