"""BMC-Steward: Seite „Aufbauplaner“ – Racks zusammenstellen, Geräte einbauen, verkabeln, prüfen.

Links die Bibliothek (Server aus der Serverliste, Switches, Infrastruktur, Platzhalter), in der Mitte die
Racks als Zeichenfläche (RackCanvas), rechts Eigenschaften, Prüfung, Netze und Stückliste. Der Plan selbst
(planner.Plan) kennt keine Oberfläche. Gespeichert wird nur auf Knopfdruck – wie überall im Tool.
"""
import ipaddress
import json
import re
from html import escape as html_escape
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPainterPathStroker, QPen,
                         QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
                             QHBoxLayout, QHeaderView, QInputDialog, QStyle, QTreeWidget, QTreeWidgetItem,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                             QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import i18n
from .applog import LOG
from .core import intro_label
from . import theme
from .i18n import HERE, T
from .planner import (CATALOG, SFP_PRESETS, compatible, MAX_COLS, MAX_DEV_U, MAX_RACK_U, MAX_ROWS, MIN_GRID, MIN_RACK_U, NETS, RACK_SIZES,
                      Plan, catalog_texts, col_name, coord_name, demo_plan, netapp_demo_plan, parse_coord, port_name,
                      rack_tiles, vendor_of)

MIME = "application/x-bmc-steward-planner"
# Farbschemata. Hell im Stil üblicher DCIM-Werkzeuge (Vorgabe unter Windows), dunkel wie der Rest von
# BMC-Steward (Vorgabe unter Linux). apply_theme() setzt die Namen unten; die Zeichenflächen lesen sie beim Zeichnen.
_C = QColor
THEMES = {
    "light": dict(
        BG=_C("#e6eaef"), RACK_BG=_C("#f8fafc"), RACK_LINE=_C("#4b5563"), RAIL=_C("#9ca3af"), STRIPE=_C("#e5e7eb"),
        TEXT=_C("#111827"), MUTED=_C("#4b5563"), DIM=_C("#9ca3af"), ACCENT=_C("#2563eb"), TITLE_BG=_C("#1f3a5f"),
        TITLE_SEL=_C("#2563eb"), TITLE_TEXT=_C("#ffffff"), TITLE_SUB=_C("#c7d2fe"), BAR_EDGE=_C("#9ca3af"),
        BAR_BG=_C("#ffffff"), CHASSIS=_C("#374151"), CHASSIS_EDGE=_C("#1f2937"), NUM_MAJOR=_C("#f3f4f6"),
        NUM_MINOR=_C("#9ca3af"), LED_IDLE=_C("#9ca3af"), LED_RING=_C("#ffffff"), PORT_FILL=_C("#374151"),
        NET_COLOR={"mgmt": _C("#ca8a04"), "data": _C("#2563eb"), "storage": _C("#9333ea"), "uplink": _C("#059669"),
                   "patch": _C("#64748b"), "fc": _C("#ea580c"), "sas": _C("#0891b2"), "cluster": _C("#be185d")},
        PORT_EDGE={"bmc": _C("#a16207"), "mgmt": _C("#a16207"), "data": _C("#1e40af"), "up": _C("#047857"),
                   "patch": _C("#475569"), "fc": _C("#c2410c"), "sas": _C("#0e7490"), "cl": _C("#9d174d")},
        KIND_FILL={"server": ("#e5e7eb", "#6b7280"), "switch": ("#dbe7f5", "#3b6ea5"), "fw": ("#f6dada", "#b45454"),
                   "patch": ("#e2e5ea", "#6b7280"), "pdu": ("#f5e7c8", "#a87b2a"), "blank": ("#eef0f3", "#b6bcc6"),
                   "storage": ("#dde6f3", "#1f4f8f"), "shelf": ("#e6ebf2", "#51678a"), "bridge": ("#f3e2e2", "#9b3b3b")},
        STATE_COLOR={"ok": _C("#16a34a"), "degraded": _C("#d97706"), "down": _C("#dc2626")},
        FLOOR_BG=_C("#f4f6f9"), GRID=_C("#dde3ea"), GRID_MAJOR=_C("#b8c2cf"), HEAD_BG=_C("#e2e8f0"),
        HEAD_TEXT=_C("#334155"), TILE_SEL=_C("#bfdbfe"),
        LOAD_FILL=(("#dcfce7", "#16a34a"), ("#fef3c7", "#d97706"), ("#fee2e2", "#dc2626")),
        LEGEND={"mgmt": "#facc15", "data": "#93c5fd", "storage": "#d8b4fe", "uplink": "#6ee7b7", "fc": "#fdba74",
                "sas": "#67e8f9", "cluster": "#f9a8d4"},
        BAR_CSS=("QFrame#plBar { background: #1f3a5f; border-radius: 3px; }"
                 "QFrame#plBar QPushButton, QFrame#plBar QToolButton { color: #ffffff; background: transparent;"
                 " border: 1px solid #3d6491; border-radius: 3px; padding: 4px 10px; }"
                 "QFrame#plBar QPushButton:hover, QFrame#plBar QToolButton:hover { background: #2b4d78; }"
                 "QFrame#plBar QPushButton:checked { background: #2563eb; border-color: #93c5fd; }"
                 "QFrame#plBar QPushButton#seg { border-radius: 0; padding: 4px 12px; }"
                 "QFrame#plBar QComboBox { background: #ffffff; color: #111827; border-radius: 3px; padding: 3px 8px; }"
                 "QFrame#plBar QLabel { color: #ffffff; }"),
        CRUMB_CSS="QLabel#plCrumb { background: #eef2f7; color: #1f3a5f; border: 1px solid #c9d3df; padding: 3px 8px; }",
        SCROLL_CSS="QScrollArea { background: #e6eaef; border: 1px solid #c9d3df; }",
        LIB_CSS="QListWidget { background: #ffffff; color: #111827; } QListWidget::item { padding: 3px 6px; }",
    ),
    "dark": dict(
        BG=_C("#0b1214"), RACK_BG=_C("#0a1012"), RACK_LINE=_C("#2a3c3f"), RAIL=_C("#2f4245"), STRIPE=_C("#111b1d"),
        TEXT=_C("#d6e2e0"), MUTED=_C("#8aa29e"), DIM=_C("#4f6764"), ACCENT=_C("#34d399"), TITLE_BG=_C("#16302b"),
        TITLE_SEL=_C("#1f6b52"), TITLE_TEXT=_C("#ecfdf5"), TITLE_SUB=_C("#6ee7b7"), BAR_EDGE=_C("#23363a"),
        BAR_BG=_C("#0f191b"), CHASSIS=_C("#16201f"), CHASSIS_EDGE=_C("#2a3c3f"), NUM_MAJOR=_C("#8aa29e"),
        NUM_MINOR=_C("#3d5450"), LED_IDLE=_C("#2f4245"), LED_RING=_C("#0b1214"), PORT_FILL=_C("#0a1012"),
        NET_COLOR={"mgmt": _C("#facc15"), "data": _C("#60a5fa"), "storage": _C("#c084fc"), "uplink": _C("#34d399"),
                   "patch": _C("#94a3b8"), "fc": _C("#fb923c"), "sas": _C("#22d3ee"), "cluster": _C("#f472b6")},
        PORT_EDGE={"bmc": _C("#a68a16"), "mgmt": _C("#7a6512"), "data": _C("#2e5a8a"), "up": _C("#1f8f66"),
                   "patch": _C("#3b5357"), "fc": _C("#9a3412"), "sas": _C("#155e75"), "cl": _C("#831843")},
        KIND_FILL={"server": ("#18262b", "#2f4a50"), "switch": ("#12293a", "#22506e"), "fw": ("#2e161b", "#5a2a32"),
                   "patch": ("#1b2124", "#3a454b"), "pdu": ("#2b2113", "#5a4320"), "blank": ("#121b1d", "#2a3a3e"),
                   "storage": ("#13233a", "#2c4f80"), "shelf": ("#161f2b", "#34465e"), "bridge": ("#2a1717", "#5e2c2c")},
        STATE_COLOR={"ok": _C("#34d399"), "degraded": _C("#fbbf24"), "down": _C("#f87171")},
        FLOOR_BG=_C("#0b1214"), GRID=_C("#142024"), GRID_MAJOR=_C("#22363b"), HEAD_BG=_C("#0f191b"),
        HEAD_TEXT=_C("#8aa29e"), TILE_SEL=_C("#183631"),
        LOAD_FILL=(("#0f3a2c", "#34d399"), ("#3a2a08", "#fbbf24"), ("#3b1717", "#f87171")),
        LEGEND={"mgmt": "#facc15", "data": "#60a5fa", "storage": "#c084fc", "uplink": "#34d399", "fc": "#fb923c",
                "sas": "#22d3ee", "cluster": "#f472b6"},
        BAR_CSS=("QFrame#plBar { background: #0f191b; border: 1px solid #1d2b2e; border-radius: 6px; }"
                 "QFrame#plBar QPushButton, QFrame#plBar QToolButton { color: #bcd0cd; background: #142124;"
                 " border: 1px solid #23363a; border-radius: 6px; padding: 4px 10px; }"
                 "QFrame#plBar QPushButton:hover, QFrame#plBar QToolButton:hover { border-color: #2f5049; }"
                 "QFrame#plBar QPushButton:checked { background: #183631; border-color: #34d399; color: #6ee7b7; }"
                 "QFrame#plBar QPushButton#seg { border-radius: 0; padding: 4px 12px; }"
                 "QFrame#plBar QComboBox { background: #111c1e; color: #d6e2e0; border: 1px solid #23363a;"
                 " border-radius: 6px; padding: 3px 8px; }"
                 "QFrame#plBar QLabel { color: #8aa29e; }"),
        CRUMB_CSS="QLabel#plCrumb { background: #101a1c; color: #8aa29e; border: 1px solid #1d2b2e; padding: 3px 8px; }",
        SCROLL_CSS="QScrollArea { background: #0b1214; border: 1px solid #1d2b2e; }",
        LIB_CSS=("QListWidget { background: #0b1214; color: #d6e2e0; border: 1px solid #1d2b2e; }"
                 "QListWidget::item { padding: 3px 6px; } QListWidget::item:selected { background: #1f6b52; }"),
    ),
}
# Eigene, herstellerneutrale Palette (keine Markenfarben): die Streifen unterscheiden nur die Geräteart/Gruppe.
STRIPE_COLOR = {"dell": "#8a4fc0", "hpe": "#1f8f8f", "smc": "#4a8f2a", "lenovo": "#c9741a", "gen": "#6b7280",
                "switch": "#0284c7", "fw": "#b0457a", "pdu": "#a07a1a", "patch": "#64748b", "blank": "#cbd5e1",
                "netapp": "#3b6ea5", "atto": "#8c5a3c", "brocade": "#7a8c2e", "storage": "#3b6ea5", "shelf": "#51678a",
                "bridge": "#8c5a3c"}


def apply_theme(name):
    """Setzt die Farbnamen dieses Moduls auf das Schema `name` ("light" oder "dark")."""
    globals().update(THEMES[name if name in THEMES else "light"])


# Vorgabe hell; die Namen stehen hier ausdrücklich, damit Werkzeuge wie pyflakes sie kennen
_L = THEMES["light"]
(BG, RACK_BG, RACK_LINE, RAIL, STRIPE, TEXT, MUTED, DIM, ACCENT, TITLE_BG, TITLE_SEL, TITLE_TEXT, TITLE_SUB,
    BAR_EDGE, BAR_BG, CHASSIS, CHASSIS_EDGE, NUM_MAJOR, NUM_MINOR, LED_IDLE, LED_RING, PORT_FILL, NET_COLOR,
    PORT_EDGE, KIND_FILL, STATE_COLOR, FLOOR_BG, GRID, GRID_MAJOR, HEAD_BG, HEAD_TEXT, TILE_SEL, LOAD_FILL,
    LEGEND, BAR_CSS, CRUMB_CSS, SCROLL_CSS, LIB_CSS) = (_L[k] for k in ('BG', 'RACK_BG', 'RACK_LINE', 'RAIL', 'STRIPE',
    'TEXT', 'MUTED', 'DIM', 'ACCENT', 'TITLE_BG', 'TITLE_SEL', 'TITLE_TEXT', 'TITLE_SUB', 'BAR_EDGE',
    'BAR_BG', 'CHASSIS', 'CHASSIS_EDGE', 'NUM_MAJOR', 'NUM_MINOR', 'LED_IDLE', 'LED_RING', 'PORT_FILL',
    'NET_COLOR', 'PORT_EDGE', 'KIND_FILL', 'STATE_COLOR', 'FLOOR_BG', 'GRID', 'GRID_MAJOR', 'HEAD_BG',
    'HEAD_TEXT', 'TILE_SEL', 'LOAD_FILL', 'LEGEND', 'BAR_CSS', 'CRUMB_CSS', 'SCROLL_CSS', 'LIB_CSS'))
LEVEL_COLOR = {"err": "#d9534f", "warn": "#c77700", "info": "#2f7fb5"}
LEVEL_MARK = {"err": "✕", "warn": "!", "info": "i"}


def net_names():
    return {"mgmt": T("Management (BMC)"), "data": T("Daten"), "storage": T("Storage"), "uplink": T("Uplink"),
            "patch": T("Patch"), "fc": T("Fibre Channel"), "sas": T("SAS"), "cluster": T("Cluster")}


class BarScroll(QScrollArea):
    """Scrollbereich für die Werkzeugleiste: so hoch wie die Leiste plus Platz für die Bildlaufleiste. Die Höhe
    wird jedes Mal neu bestimmt – das Stylesheet kommt beim Start erst nach dem Aufbau dazu."""

    def sizeHint(self):
        w = self.widget()
        h = (w.sizeHint().height() if w else 0) + self.horizontalScrollBar().sizeHint().height() + 2
        return QSize(super().sizeHint().width(), h)

    def minimumSizeHint(self):
        return QSize(0, self.sizeHint().height())


def rack_label(plan, r):
    """„C04“ oder „Netz-1 · C04“: der Stellplatz nur, wenn er nicht schon der Name ist."""
    coord = plan.coord(r)
    return r["name"] if coord in (r["name"], "–") else f"{r['name']} · {coord}"


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


class ZoomPan:
    """Gemeinsames für Grundriss und Rack-Ansicht: das Mausrad vergrößert oder verkleinert um den Mauszeiger,
    die mittlere Maustaste verschiebt die Ansicht, ein Rechtsklick fragt nach dem Kontextmenü. Gezeichnet und
    gerechnet wird in Modellkoordinaten; `zoom` skaliert nur die Darstellung."""
    ZMIN, ZMAX, ZSTEP = 0.3, 3.0, 1.15

    def zp_init(self):
        self.zoom = 1.0
        self.scroll = None               # die umgebende QScrollArea (setzt die Seite)
        self._pan = None

    def _pt(self, e):
        return QPointF(e.position()) / self.zoom

    def relayout(self):
        w, h = self.base_size()
        self.setMinimumSize(int(w * self.zoom), int(h * self.zoom))
        self.update()

    def set_zoom(self, z, anchor=None):
        """Zoom setzen; `anchor` (Widget-Koordinaten) bleibt dabei an derselben Stelle im Fenster."""
        z = max(self.ZMIN, min(self.ZMAX, z))
        if abs(z - self.zoom) < 1e-4:
            return
        sa = self.scroll
        hb, vb = (sa.horizontalScrollBar(), sa.verticalScrollBar()) if sa else (None, None)
        if anchor is None and sa:
            anchor = QPointF(hb.value() + sa.viewport().width() / 2, vb.value() + sa.viewport().height() / 2)
        model = QPointF(anchor) / self.zoom if anchor is not None else None
        in_view = QPointF(anchor) - QPointF(hb.value(), vb.value()) if sa and anchor is not None else None
        self.zoom = z
        self.relayout()
        if sa and model is not None:
            vp = sa.viewport().size()
            self.resize(max(self.minimumWidth(), vp.width()), max(self.minimumHeight(), vp.height()))
            hb.setRange(0, max(0, self.width() - vp.width()))
            vb.setRange(0, max(0, self.height() - vp.height()))
            hb.setValue(int(model.x() * z - in_view.x()))
            vb.setValue(int(model.y() * z - in_view.y()))
        self.zoomChanged.emit(self.zoom)

    def zoom_fit(self):
        if not self.scroll:
            return
        vp = self.scroll.viewport().size()
        w, h = self.base_size()
        self.set_zoom(min(vp.width() / max(1, w), vp.height() / max(1, h)) * 0.98)

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        if not steps or e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            super().wheelEvent(e)                # Umschalt + Rad: normal blättern
            return
        self.set_zoom(self.zoom * self.ZSTEP ** steps, e.position())
        e.accept()

    def zp_press(self, e):
        if e.button() == Qt.MouseButton.MiddleButton and self.scroll:
            self._pan = (e.globalPosition(), self.scroll.horizontalScrollBar().value(),
                         self.scroll.verticalScrollBar().value())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        return False

    def zp_move(self, e):
        if not self._pan:
            return False
        start, h0, v0 = self._pan
        d = e.globalPosition() - start
        self.scroll.horizontalScrollBar().setValue(int(h0 - d.x()))
        self.scroll.verticalScrollBar().setValue(int(v0 - d.y()))
        return True

    def zp_release(self, e):
        if self._pan and e.button() == Qt.MouseButton.MiddleButton:
            self._pan = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return True
        return False

    def contextMenuEvent(self, e):
        kind, ident = self.hit_for_menu(QPointF(e.pos()) / self.zoom)
        self.contextRequested.emit(kind, ident, e.globalPos())


class RackCanvas(ZoomPan, QWidget):
    """Zeichnet die Racks samt Geräten, Ports und Kabeln und nimmt Maus und Drag & Drop entgegen."""
    changed = pyqtSignal()               # Plan geändert
    selectionChanged = pyqtSignal()
    message = pyqtSignal(str, bool)      # Text, Fehler?
    simClicked = pyqtSignal(str, str)    # ("cable"|"dev", id) im Simulationsmodus
    deleteRequested = pyqtSignal()
    zoomChanged = pyqtSignal(float)
    contextRequested = pyqtSignal(str, object, object)   # Art, Kennung, Bildschirmposition

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
        self.highlight = set()           # Kabel-IDs einer angezeigten Strecke
        self.zp_init()
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(T("Racks"))
        self.relayout()

    # ------------------------------------------------------------ Geometrie
    def base_size(self):
        n = max(1, len(self.plan.racks))
        h = max([r["h"] for r in self.plan.racks] or [24])
        return 2 * self.M + n * (self.RW + self.GAP), self.M + self.HEAD + h * self.UH + 2 * self.M

    def slot_at(self, pt):
        """(Rack-ID, HE) der Höheneinheit unter dem Punkt oder None."""
        for i, r in enumerate(self.plan.racks):
            s = self.slot_rect(i)
            if s.contains(pt):
                return r["id"], r["h"] - int((pt.y() - s.y()) // self.UH)
        return None

    def hit_for_menu(self, pt):
        hit = self.port_at(pt)
        if hit:
            return "port", hit
        cid = self.cable_hit(pt)
        if cid:
            return "cable", cid
        d = self.dev_at(pt)
        if d:
            return "dev", d["id"]
        slot = self.slot_at(pt)
        if slot:
            return "slot", slot
        rid = self.rack_at(pt)
        return ("rack", rid) if rid else ("none", None)

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
        p.scale(self.zoom, self.zoom)
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
        p.setPen(TITLE_TEXT)
        p.drawText(title.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                   f"{r['name']}  ·  {r['h']} " + T("HE"))
        p.setFont(small)
        p.setPen(TITLE_SUB)
        p.drawText(title.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   T("Vorderseite") if self.view == "front" else T("Rückseite"))
        # Belegung und Last
        p.setPen(MUTED)
        load = f"{L['used']}/{r['h']} {T('HE')} · {L['w'] / 1000:.1f}".replace(".", ",") + \
               (f"/{L['cap'] / 1000:.1f} kW".replace(".", ",") if L["cap"] else " kW")
        p.drawText(QRectF(x, self.M + 23, self.RW, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   load)
        bar = QRectF(x, self.M + 28, self.RW / 2, 5)
        p.setPen(QPen(BAR_EDGE, 0.8))
        p.setBrush(BAR_BG)
        p.drawRect(bar)
        share = L["used"] / r["h"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(STATE_COLOR["down"] if share > 0.9 else ACCENT)
        p.drawRect(QRectF(bar.x(), bar.y(), bar.width() * min(1, share), bar.height()))
        # Gehäuse mit Holmen, Einbaufläche, HE-Zählung
        frame = QRectF(x, self.top() - 4, self.RW, r["h"] * self.UH + 8)
        p.setBrush(CHASSIS)
        p.setPen(QPen(TITLE_SEL if sel else CHASSIS_EDGE, 2 if sel else 1))
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
            p.setPen(NUM_MAJOR if u % 5 == 0 or u == 1 else NUM_MINOR)
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
        stripe = STRIPE_COLOR.get(d.get("vendor") or "gen" if d["kind"] == "server" else d.get("vendor") or d["kind"],
                                  "#6b7280")
        x = rect.x() + 8
        if d["kind"] != "blank":
            led = STATE_COLOR[state] if state else LED_IDLE
            p.setBrush(led)
            p.setPen(QPen(LED_RING, 0.6))
            p.drawEllipse(QPointF(x + 2, rect.center().y() if d["u"] == 1 else rect.y() + 8), 2.8, 2.8)
            x += 9
        ports = self.port_rects(d, rect) if self.view == "rear" else []
        right = (ports[0][1].x() - 6) if ports else rect.right() - 8
        if self.view == "front" and d["kind"] != "blank":   # Herstellerkennung rechts an der Front
            badge = {"dell": "DELL", "hpe": "HPE", "lenovo": "LENOVO", "smc": "SMC", "netapp": "NETAPP",
                     "atto": "ATTO", "brocade": "BROCADE"}.get(d.get("vendor") or "")
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
            if self.sel == ("port", (d["id"], pid)):
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(ACCENT, 2))
                p.drawRect(rc.adjusted(-2.5, -2.5, 2.5, 2.5))
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
            halo = QColor(ACCENT)
            halo.setAlpha(90)
            p.setPen(QPen(halo, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPath(path)
        col = QColor(STATE_COLOR["down"]) if dead else QColor(NET_COLOR[c["net"]])
        width = 1.8
        if self.highlight:                           # Strecke: ihre Kabel kräftig, alle anderen blass
            if c["id"] in self.highlight:
                halo = QColor(ACCENT)
                halo.setAlpha(110)
                p.setPen(QPen(halo, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPath(path)
                width = 3
            else:
                col.setAlpha(45)
        pen = QPen(col, width, Qt.PenStyle.DotLine if dead else Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(path)

    # ------------------------------------------------------------ Maus und Tastatur
    def select(self, sel):
        self.sel = sel
        self.update()
        self.selectionChanged.emit()

    def mousePressEvent(self, e):
        if self.zp_press(e) or e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self._pt(e)
        self.setFocus()
        if self.sim is not None:
            cid = self.cable_hit(pt)
            d = None if cid else self.dev_at(pt)
            if cid:
                self.simClicked.emit("cable", cid)
            elif d and d["kind"] in ("server", "switch", "fw", "patch", "storage", "shelf", "bridge"):
                self.simClicked.emit("dev", d["id"])
            return
        hit = self.port_at(pt)
        if hit and self.cable_mode:
            self.port_clicked(*hit)
            return
        if hit:                                      # Rückseite: Port auswählen (Eigenschaften, SFP …)
            self.select(("port", hit))
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
        if self.zp_move(e):
            return
        pt = self._pt(e)
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
        port = self.port_at(pt)
        self.setCursor(Qt.CursorShape.PointingHandCursor if (self.cable_mode and port) or (self.sim is not None and (
            self.cable_hit(pt) or self.dev_at(pt))) else Qt.CursorShape.ArrowCursor)
        self.setToolTip(self.tip_at(pt, port))

    def tip_at(self, pt, port=None):
        """Kurzinfo unter dem Mauszeiger: Port mit Gegenstelle, Kabel oder Gerät."""
        plan = self.plan
        if port:
            d = plan.dev(port[0])
            text = f"{d['name']} · {port_name(d, port[1])}"
            c = plan.cable_at(*port)
            if c:
                od, op = plan.other_end(c, *port)
                text += f"  →  {plan.dev(od)['name']} · {port_name(plan.dev(od), op)}  ({net_names()[c['net']]})"
            else:
                text += "  –  " + T("frei")
            return text
        cid = self.cable_hit(pt)
        if cid:
            c = plan.cable(cid)
            A, B = plan.dev(c["a"][0]), plan.dev(c["b"][0])
            return (f"{A['name']} · {port_name(A, c['a'][1])}  ↔  {B['name']} · {port_name(B, c['b'][1])}\n"
                    f"{net_names()[c['net']]} · {plan.cable_medium(c)} · {plan.cable_length(c):g} m")
        d = self.dev_at(pt)
        if d:
            used = sum(1 for p in d["ports"] if plan.cable_at(d["id"], p["id"]))
            span = f"{d['pos']}–{d['pos'] + d['u'] - 1}" if d["u"] > 1 else str(d["pos"])
            lines = [f"<b>{d['name']}</b>", d.get("model") or "", f"{T('HE')} {span} · {d.get('w') or 0} W"]
            if d["ports"]:
                lines.append(T("{0} von {1} Anschlüssen belegt").format(used, len(d["ports"])))
            return "<br>".join(x for x in lines if x)
        return ""

    def mouseReleaseEvent(self, e):
        if self.zp_release(e):
            return
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
                    self.plan.dev(did)["name"], port_name(self.plan.dev(did), pid)), False)
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
            self.plan.dev(c["a"][0])["name"], port_name(self.plan.dev(c["a"][0]), c["a"][1]),
            self.plan.dev(c["b"][0])["name"], port_name(self.plan.dev(c["b"][0]), c["b"][1])), False)
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
        if e.mimeData().hasFormat(MIME) and self.sim is None and self._payload_u(e.mimeData())[0]:
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        kind, key, u = self._payload_u(e.mimeData())
        t = self.target(self._pt(e), u)
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
        t = self.target(self._pt(e), u)
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


class FloorCanvas(ZoomPan, QWidget):
    """Grundriss des Raums: Bodenplatten mit Koordinaten (Spalten A … Z, AA …, Reihen 01 …) und die Racks
    darauf. Racks lassen sich verschieben, ein Doppelklick öffnet sie in der Rack-Ansicht."""
    rackClicked = pyqtSignal(str)
    rackOpened = pyqtSignal(str)
    tileClicked = pyqtSignal(int, int)
    moved = pyqtSignal()
    message = pyqtSignal(str, bool)
    zoomChanged = pyqtSignal(float)
    contextRequested = pyqtSignal(str, object, object)
    rackDropped = pyqtSignal(int, int, int)        # Höhe, Spalte, Reihe

    TS, HEAD, LEFT, M, FOOT = 42, 24, 30, 10, 30

    def __init__(self, racks_view):
        super().__init__()
        self.rv = racks_view             # die Rack-Ansicht hält Plan und Auswahl
        self.tile_sel = None
        self.drag = None
        self.ghost = None                # (x, y, passt?, Ausrichtung)
        self.zp_init()
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(T("Grundriss"))
        self.relayout()

    @property
    def plan(self):
        return self.rv.plan

    def base_size(self):
        p = self.plan
        return 2 * self.M + self.LEFT + p.cols * self.TS, 2 * self.M + self.HEAD + p.rows * self.TS + self.FOOT

    def hit_for_menu(self, pt):
        r = self.rack_at(pt)
        if r:
            return "rack", r["id"]
        t = self.tile_at(pt)
        return ("tile", t) if t else ("none", None)

    def tile_rect(self, x, y):
        return QRectF(self.M + self.LEFT + x * self.TS, self.M + self.HEAD + y * self.TS, self.TS, self.TS)

    def tile_at(self, pt):
        x = int((pt.x() - self.M - self.LEFT) // self.TS)
        y = int((pt.y() - self.M - self.HEAD) // self.TS)
        return (x, y) if 0 <= x < self.plan.cols and 0 <= y < self.plan.rows else None

    def rack_rect(self, r, x=None, y=None, orient=None):
        x = r["x"] if x is None else x
        y = r["y"] if y is None else y
        orient = orient or r["orient"]
        rect = QRectF()
        for tx, ty in rack_tiles(x, y, orient):
            rect = rect.united(self.tile_rect(tx, ty))
        return rect.adjusted(2, 2, -2, -2)

    def rack_at(self, pt):
        for r in self.plan.racks:
            if r.get("x") is not None and self.rack_rect(r).contains(pt):
                return r
        return None

    # ------------------------------------------------------------ Zeichnen
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plan = self.plan
        p.fillRect(self.rect(), FLOOR_BG)
        p.scale(self.zoom, self.zoom)
        x0, y0 = self.M + self.LEFT, self.M + self.HEAD
        w, h = plan.cols * self.TS, plan.rows * self.TS
        if self.tile_sel and self.tile_sel[0] < plan.cols and self.tile_sel[1] < plan.rows:
            p.fillRect(self.tile_rect(*self.tile_sel), TILE_SEL)
        for x in range(plan.cols + 1):
            p.setPen(QPen(GRID_MAJOR if x % 5 == 0 else GRID, 1))
            p.drawLine(QPointF(x0 + x * self.TS, y0), QPointF(x0 + x * self.TS, y0 + h))
        for y in range(plan.rows + 1):
            p.setPen(QPen(GRID_MAJOR if y % 5 == 0 else GRID, 1))
            p.drawLine(QPointF(x0, y0 + y * self.TS), QPointF(x0 + w, y0 + y * self.TS))
        sel = self.rv.sel
        for r in plan.racks:
            if r.get("x") is not None:
                self._paint_rack(p, r, sel == ("rack", r["id"]))
        if self.ghost:
            gx, gy, ok, orient = self.ghost
            col = QColor(STATE_COLOR["ok"] if ok else STATE_COLOR["down"])
            fill = QColor(col)
            fill.setAlpha(70)
            p.setBrush(fill)
            p.setPen(QPen(col, 1.5, Qt.PenStyle.DashLine))
            p.drawRect(self.rack_rect({"orient": orient}, gx, gy))
        self._paint_legend(p, y0 + h + 8)
        self._paint_axes(p, x0, y0, w, h)

    def _paint_axes(self, p, x0, y0, w, h):
        """Spalten- und Reihenbeschriftung; sie bleibt beim Zoomen und Blättern am Rand des Fensters stehen."""
        plan = self.plan
        vis = QRectF(self.visibleRegion().boundingRect())
        vis = QRectF(vis.x() / self.zoom, vis.y() / self.zoom, vis.width() / self.zoom, vis.height() / self.zoom)
        top = max(self.M, vis.top())
        left = max(self.M, vis.left())
        small = QFont(self.font())
        small.setPointSizeF(7)
        p.setFont(small)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(HEAD_BG)
        p.drawRect(QRectF(x0, top, w, self.HEAD - 2))
        p.drawRect(QRectF(left, y0, self.LEFT - 2, h))
        p.drawRect(QRectF(left, top, self.LEFT - 2, self.HEAD - 2))
        p.setPen(HEAD_TEXT)
        for x in range(plan.cols):
            p.drawText(QRectF(x0 + x * self.TS, top, self.TS, self.HEAD - 2), Qt.AlignmentFlag.AlignCenter,
                       col_name(x))
        for y in range(plan.rows):
            p.drawText(QRectF(left, y0 + y * self.TS, self.LEFT - 6, self.TS),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{y + 1:02d}")

    def _paint_rack(self, p, r, selected):
        rect = self.rack_rect(r)
        L = self.plan.rack_load(r["id"])
        share = L["used"] / r["h"]
        fill, edge = LOAD_FILL[0 if share < 0.5 else 1 if share < 0.8 else 2]
        p.setBrush(QColor(fill))
        p.setPen(QPen(ACCENT if selected else QColor(edge), 2.2 if selected else 1.2))
        p.drawRect(rect)
        # Front: kräftiger Strich an der Seite, auf die die Front zeigt
        p.setPen(QPen(TEXT, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        o = r["orient"]
        a, b = {"N": (rect.topLeft(), rect.topRight()), "S": (rect.bottomLeft(), rect.bottomRight()),
                "W": (rect.topLeft(), rect.bottomLeft()), "O": (rect.topRight(), rect.bottomRight())}[o]
        p.drawLine(a, b)
        inner = rect.adjusted(3, 3, -3, -3)
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(TEXT)
        p.drawText(QRectF(inner.x(), inner.y() + 2, inner.width(), 14), Qt.AlignmentFlag.AlignHCenter, r["name"])
        f.setBold(False)
        f.setPointSizeF(6.5)
        p.setFont(f)
        p.setPen(MUTED)
        if self.plan.coord(r) != r["name"]:
            p.drawText(QRectF(inner.x(), inner.y() + 16, inner.width(), 12), Qt.AlignmentFlag.AlignHCenter,
                       self.plan.coord(r))
        p.setPen(QColor(edge))
        p.drawText(QRectF(inner.x(), inner.y() + 28, inner.width(), 12), Qt.AlignmentFlag.AlignHCenter,
                   f"{round(share * 100)} %")
        bar = QRectF(inner.x() + 2, inner.bottom() - 6, inner.width() - 4, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(GRID_MAJOR)
        p.drawRect(bar)
        p.setBrush(QColor(edge))
        p.drawRect(QRectF(bar.x(), bar.y(), bar.width() * min(1, share), bar.height()))

    def _paint_legend(self, p, y):
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        p.setFont(f)
        x = self.M + self.LEFT
        items = [(LOAD_FILL[0], T("Belegung unter 50 %")), (LOAD_FILL[1], T("50–80 %")), (LOAD_FILL[2], T("über 80 %"))]
        for (fill, edge), text in items:
            p.setBrush(QColor(fill))
            p.setPen(QPen(QColor(edge), 1.2))
            p.drawRect(QRectF(x, y + 3, 14, 10))
            p.setPen(MUTED)
            w = p.fontMetrics().horizontalAdvance(text)
            p.drawText(QRectF(x + 20, y, w + 4, 16), Qt.AlignmentFlag.AlignVCenter, text)
            x += w + 40
        p.setPen(QPen(TEXT, 3))
        p.drawLine(QPointF(x, y + 8), QPointF(x + 16, y + 8))
        p.setPen(MUTED)
        text = T("Rackfront")
        p.drawText(QRectF(x + 22, y, p.fontMetrics().horizontalAdvance(text) + 4, 16), Qt.AlignmentFlag.AlignVCenter,
                   text)

    # ------------------------------------------------------------ Maus
    def mousePressEvent(self, e):
        if self.zp_press(e) or e.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        pt = self._pt(e)
        r = self.rack_at(pt)
        if r:
            t = self.tile_at(pt)
            self.tile_sel = None
            self.drag = {"id": r["id"], "start": pt, "active": False,
                         "off": (t[0] - r["x"], t[1] - r["y"]) if t else (0, 0)}
            self.rackClicked.emit(r["id"])
            self.update()
            return
        t = self.tile_at(pt)
        self.tile_sel = t
        if t:
            self.tileClicked.emit(*t)
        self.update()

    def mouseMoveEvent(self, e):
        if self.zp_move(e):
            return
        pt = self._pt(e)
        t = self.tile_at(pt)
        r = self.rack_at(pt)
        if r:
            L = self.plan.rack_load(r["id"])
            self.setToolTip(f"<b>{r['name']}</b> · {self.plan.coord(r)}<br>"
                            + T("{0} von {1} HE belegt · {2} W").format(L["used"], r["h"], L["w"]))
        else:
            self.setToolTip(coord_name(*t) if t else "")
        if not self.drag:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.rack_at(pt) else Qt.CursorShape.ArrowCursor)
            return
        if not self.drag["active"] and (pt - self.drag["start"]).manhattanLength() < 5:
            return
        self.drag["active"] = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if t:
            gx, gy = t[0] - self.drag["off"][0], t[1] - self.drag["off"][1]
            r = self.plan.rack(self.drag["id"])
            self.ghost = (gx, gy, self.plan.can_stand(r["id"], gx, gy, r["orient"]), r["orient"])
        self.update()

    def mouseReleaseEvent(self, e):
        if self.zp_release(e):
            return
        drag, ghost = self.drag, self.ghost
        self.drag = self.ghost = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if drag and drag["active"] and ghost:
            r = self.plan.rack(drag["id"])
            if ghost[2] and self.plan.move_rack(r["id"], ghost[0], ghost[1]):
                self.message.emit(T("Rack {0} steht jetzt auf {1}.").format(r["name"], self.plan.coord(r)), False)
                self.moved.emit()
            else:
                self.message.emit(T("Auf {0} ist kein Platz für Rack {1}.").format(
                    coord_name(max(0, ghost[0]), max(0, ghost[1])), r["name"]), True)
        self.update()

    def mouseDoubleClickEvent(self, e):
        r = self.rack_at(self._pt(e))
        if r:
            self.rackOpened.emit(r["id"])

    # ------------------------------------------------------------ Racks aus dem Katalog ziehen
    @staticmethod
    def _rack_payload(md):
        kind, _, val = bytes(md.data(MIME)).decode("utf-8", "replace").partition(":")
        return int(val) if kind == "rack" and val.isdigit() else None

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME) and self._rack_payload(e.mimeData()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        t = self.tile_at(self._pt(e))
        if not t or not self._rack_payload(e.mimeData()):
            self.ghost = None
            self.update()
            e.ignore()
            return
        self.ghost = (t[0], t[1], self.plan.can_stand(None, t[0], t[1], "S"), "S")
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.ghost = None
        self.update()

    def dropEvent(self, e):
        h = self._rack_payload(e.mimeData())
        t = self.tile_at(self._pt(e))
        self.ghost = None
        self.update()
        if h and t:
            e.acceptProposedAction()
            self.rackDropped.emit(h, t[0], t[1])


class ConnectDialog(QDialog):
    """Gegenstelle für einen Port wählen: Rack, Gerät, freier passender Port – über alle Racks hinweg."""

    def __init__(self, parent, plan, did, pid):
        super().__init__(parent)
        self.plan, self.src, self.choice = plan, (did, pid), None
        d = plan.dev(did)
        self.kind = plan.port(d, pid)["k"]
        self.setWindowTitle(T("Verbinden"))
        self.resize(640, 460)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(T("Von: {0}").format(f"<b>{html_escape(d['name'])} · "
                                                 f"{html_escape(port_name(d, pid))}</b>")))
        row = QHBoxLayout()
        self.rack = QComboBox()
        self.rack.addItem(T("Alle Racks"), None)
        for r in plan.racks:
            self.rack.addItem(rack_label(plan, r), r["id"])
        self.find = QLineEdit()
        self.find.setPlaceholderText(T("Gerät suchen …"))
        self.find.setClearButtonEnabled(True)
        row.addWidget(self.rack)
        row.addWidget(self.find, 1)
        v.addLayout(row)
        lists = QHBoxLayout()
        self.devs = QListWidget()
        self.ports = QListWidget()
        lists.addWidget(self.devs, 3)
        lists.addWidget(self.ports, 2)
        v.addLayout(lists, 1)
        row = QHBoxLayout()
        self.net = QComboBox()
        self.net.addItem(T("Kabeltyp: automatisch"), None)
        for k, name in net_names().items():
            self.net.addItem(name, k)
        row.addWidget(self.net)
        row.addStretch(1)
        self.ok = QPushButton(T("Verbinden"))
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        cancel = QPushButton(T("Abbrechen"))
        row.addWidget(cancel)
        row.addWidget(self.ok)
        v.addLayout(row)
        self.rack.currentIndexChanged.connect(self.fill_devs)
        self.find.textChanged.connect(self.fill_devs)
        self.devs.currentItemChanged.connect(self.fill_ports)
        self.ports.currentItemChanged.connect(lambda *_: self.ok.setEnabled(self.ports.currentItem() is not None))
        self.ports.itemDoubleClicked.connect(lambda *_: self.accept_choice())
        self.ok.clicked.connect(self.accept_choice)
        cancel.clicked.connect(self.reject)
        own = plan.dev(did)["rack"]
        i = self.rack.findData(own)
        self.fill_devs()
        if i >= 0 and not self.devs.count():
            self.rack.setCurrentIndex(0)

    def free_ports(self, d):
        return [p for p in d["ports"] if not self.plan.cable_at(d["id"], p["id"])
                and compatible(self.kind, p["k"])]

    def fill_devs(self, *_):
        rid, text = self.rack.currentData(), self.find.text().strip().lower()
        self.devs.clear()
        for d in sorted(self.plan.placed(), key=lambda d: (self.plan.rack_index(d["rack"]), -d["pos"])):
            if d["id"] == self.src[0] or (rid and d["rack"] != rid):
                continue
            free = self.free_ports(d)
            if not free:
                continue
            r = self.plan.rack(d["rack"])
            label = f"{d['name']}  ·  {r['name']} {T('HE')} {d['pos']}  ·  " + T("{0} frei").format(len(free))
            if text and text not in label.lower() and text not in (d.get("model") or "").lower():
                continue
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, d["id"])
            self.devs.addItem(it)
        if self.devs.count():
            self.devs.setCurrentRow(0)
        else:
            self.ports.clear()

    def fill_ports(self, *_):
        self.ports.clear()
        it = self.devs.currentItem()
        if it is None:
            return
        d = self.plan.dev(it.data(Qt.ItemDataRole.UserRole))
        for p in self.free_ports(d):
            pi = QListWidgetItem(port_name(d, p["id"]))
            pi.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.ports.addItem(pi)
        if self.ports.count():
            self.ports.setCurrentRow(0)

    def accept_choice(self):
        di, pi = self.devs.currentItem(), self.ports.currentItem()
        if di is None or pi is None:
            return
        self.choice = (di.data(Qt.ItemDataRole.UserRole), pi.data(Qt.ItemDataRole.UserRole), self.net.currentData())
        self.accept()


class TraceDialog(QDialog):
    """Streckenansicht: der ganze Weg einer Verbindung von Ende zu Ende, durch Patchpanels hindurch, mit Rack,
    Stellplatz, HE, Port, Transceiver und Kabel (Medium, Länge, Netz)."""

    def __init__(self, parent, plan, hops):
        super().__init__(parent)
        self.setWindowTitle(T("Strecke"))
        self.resize(720, 520)
        v = QVBoxLayout(self)
        cables = [c for _, _, c in hops if c]
        total = sum(plan.cable_length(c) for c in cables)
        panels = len({d["id"] for d, _, _ in hops if d["kind"] == "patch"})
        v.addWidget(QLabel(T("{0} Kabel · {1} m gesamt (geschätzt) · {2} Patchpanels").format(
            len(cables), f"{total:g}".replace(".", ","), panels)))
        view = QTextBrowser()
        view.setHtml(self.html(plan, hops))
        v.addWidget(view, 1)
        self.text = view.toPlainText()
        row = QHBoxLayout()
        copy = QPushButton(T("Als Text kopieren"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text))
        close = QPushButton(T("Schließen"))
        close.clicked.connect(self.accept)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)

    @staticmethod
    def html(plan, hops):
        t = theme.tokens()
        out = [f"<table cellspacing='0' cellpadding='6' width='100%' style='color:{t['text']}'>"]
        for n, (d, pid, c) in enumerate(hops):
            r = plan.rack(d["rack"])
            p = plan.port(d, pid) or {}
            extra = " · ".join(x for x in (p.get("sfp"), p.get("speed"), p.get("ip"), p.get("vlan") and
                                            "VLAN " + p["vlan"]) if x)
            out.append(
                f"<tr><td style='background:{t['accent_soft']}; border:1px solid {t['line2']}'>"
                f"<b>{html_escape(d['name'])}</b> · {html_escape(port_name(d, pid))}<br>"
                f"<span style='color:{t['muted']}'>{html_escape(rack_label(plan, r))} · {T('HE')} {d['pos']}"
                f"{' · ' + html_escape(d.get('model') or '') if d.get('model') else ''}</span>"
                + (f"<br><span style='color:{t['muted']}'>{html_escape(extra)}</span>" if extra else "")
                + "</td></tr>")
            if c:
                out.append(f"<tr><td align='center' style='color:{t['muted']}'>│&nbsp; {html_escape(plan.cable_medium(c))}"
                           f" · {plan.cable_length(c):g} m · {html_escape(net_names()[c['net']])} &nbsp;│</td></tr>")
            elif n + 1 < len(hops) and d["kind"] == "patch":
                out.append(f"<tr><td align='center' style='color:{t['dim']}'>"
                           f"{T('durch das Patchpanel')}</td></tr>")
        out.append("</table>")
        return "".join(out)


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
        fm.addAction(T("Beispiel NetApp MetroCluster laden"), self.planner_demo_netapp)
        file_btn.setMenu(fm)
        tb.addWidget(file_btn)
        self.pl_undo_btn = QPushButton("↶")
        self.pl_undo_btn.setToolTip(T("Rückgängig (Strg+Z)"))
        self.pl_undo_btn.clicked.connect(self.planner_undo)
        self.pl_redo_btn = QPushButton("↷")
        self.pl_redo_btn.setToolTip(T("Wiederholen (Strg+Y)"))
        self.pl_redo_btn.clicked.connect(self.planner_redo)
        tb.addWidget(self.pl_undo_btn)
        tb.addWidget(self.pl_redo_btn)
        add_rack = QPushButton(T("＋ Rack"))
        add_rack.setToolTip(T("Neues Rack mit 42 HE anlegen; die Höhe lässt sich danach rechts ändern."))
        add_rack.clicked.connect(lambda: self.planner_add_rack())
        tb.addWidget(add_rack)
        tpl_btn = QToolButton()
        tpl_btn.setText(T("Vorlagen"))
        tpl_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tm = QMenu(tpl_btn)
        tm.addAction(T("Management-Kopf (Patchpanel, OOB-Switch, PDU)"), lambda: self.planner_template("head"))
        tm.addAction(T("HA-Paar (zwei Leaf-Switches mit Querverbindung)"), lambda: self.planner_template("ha"))
        tm.addSeparator()
        tm.addAction(T("NetApp MetroCluster FC (FC-Switches, Cluster-Switches, AFF, ATTO-Bridges, Shelves)"),
                     lambda: self.planner_template("metrocluster"))
        tm.addSeparator()
        tm.addAction(T("Offene Geräte automatisch verkabeln"), self.planner_auto_cable)
        tpl_btn.setMenu(tm)
        tb.addWidget(tpl_btn)
        tb.addSpacing(12)
        self.pl_view_btns = {}
        group = QButtonGroup(bar)
        group.setExclusive(True)
        for key, text in (("floor", T("Grundriss")), ("front", T("Vorderseite")), ("rear", T("Rückseite"))):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setObjectName("seg")
            btn.clicked.connect(lambda _=False, k=key: self.planner_set_view(k))
            group.addButton(btn)
            tb.addWidget(btn)
            self.pl_view_btns[key] = btn
        self.pl_view_btns["floor"].setToolTip(T("Raum von oben: Bodenplatten mit Koordinaten, Racks verschieben, "
                                                "Doppelklick öffnet ein Rack"))
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
        tb.addSpacing(12)
        zoom_out = QPushButton("−")
        zoom_out.setToolTip(T("Verkleinern (Mausrad zurück, Strg+Minus)"))
        zoom_out.clicked.connect(lambda: self.planner_zoom(1 / ZoomPan.ZSTEP))
        self.pl_zoom_label = QPushButton("100 %")
        self.pl_zoom_label.setToolTip(T("Auf 100 % zurücksetzen (Strg+0)"))
        self.pl_zoom_label.clicked.connect(lambda: self.planner_zoom(None))
        zoom_in = QPushButton("+")
        zoom_in.setToolTip(T("Vergrößern (Mausrad vor, Strg+Plus)"))
        zoom_in.clicked.connect(lambda: self.planner_zoom(ZoomPan.ZSTEP))
        fit = QPushButton(T("Einpassen"))
        fit.setToolTip(T("Alles ins Fenster einpassen"))
        fit.clicked.connect(self.planner_zoom_fit)
        for b in (zoom_out, self.pl_zoom_label, zoom_in, fit):
            b.setObjectName("seg")
            tb.addWidget(b)
        tb.addStretch(1)
        legend = QLabel()
        legend.setObjectName("legend")
        self.pl_legend = legend
        self.pl_bar = bar
        # bei schmalem Fenster waagerecht scrollbar statt das ganze Fenster breit zu zwingen
        bar_scroll = BarScroll()
        bar_scroll.setObjectName("plBarScroll")
        bar_scroll.setWidget(bar)
        bar_scroll.setWidgetResizable(True)
        bar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        bar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        bar_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bar_scroll.setStyleSheet("QScrollArea#plBarScroll, QScrollArea#plBarScroll > QWidget > QWidget"
                                 " { background: transparent; }")
        v.addWidget(bar_scroll)

        # Pfad zum gewählten Objekt, rechts daneben die Kabelfarben und das Design
        self.pl_crumb = QLabel()
        self.pl_crumb.setObjectName("plCrumb")
        crumb_row = QHBoxLayout()
        crumb_row.setSpacing(10)
        crumb_row.addWidget(self.pl_crumb, 1)
        crumb_row.addWidget(legend)
        v.addLayout(crumb_row)

        # Links: Navigator (Standort › Raum › Reihe › Rack › Gerät) und Katalog
        self.pl_tree = QTreeWidget()
        self.pl_tree.setHeaderHidden(True)
        self.pl_tree.itemClicked.connect(self.planner_tree_clicked)
        self.pl_lib_filter = QLineEdit()
        self.pl_lib_filter.setPlaceholderText(T("Katalog durchsuchen …"))
        self.pl_lib_filter.setClearButtonEnabled(True)
        self.pl_lib_filter.textChanged.connect(self.planner_filter_lib)
        self.pl_lib = PlannerLibrary()
        self.pl_lib.itemDoubleClicked.connect(self.planner_lib_double)
        lib_box = QWidget()
        ll = QVBoxLayout(lib_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.pl_lib_filter)
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
        self.pl_canvas.contextRequested.connect(self.planner_context)
        self.pl_canvas.zoomChanged.connect(self.planner_zoom_shown)
        self.pl_scroll = QScrollArea()
        self.pl_scroll.setWidget(self.pl_canvas)
        self.pl_scroll.setWidgetResizable(True)
        self.pl_canvas.scroll = self.pl_scroll
        self.pl_floor = FloorCanvas(self.pl_canvas)
        self.pl_floor.rackClicked.connect(lambda rid: self.planner_select("rack", rid, scroll=False))
        self.pl_floor.rackOpened.connect(self.planner_open_rack)
        self.pl_floor.tileClicked.connect(self.planner_tile_clicked)
        self.pl_floor.moved.connect(self.planner_changed)
        self.pl_floor.message.connect(self.planner_say)
        self.pl_floor.contextRequested.connect(self.planner_context)
        self.pl_floor.zoomChanged.connect(self.planner_zoom_shown)
        self.pl_floor.rackDropped.connect(lambda h, x, y: self.planner_add_rack(h=h, at=(x, y)))
        self.pl_floor_scroll = QScrollArea()
        self.pl_floor_scroll.setWidget(self.pl_floor)
        self.pl_floor_scroll.setWidgetResizable(True)
        self.pl_floor.scroll = self.pl_floor_scroll
        for bar_ in (self.pl_floor_scroll.horizontalScrollBar(), self.pl_floor_scroll.verticalScrollBar()):
            bar_.valueChanged.connect(lambda *_: self.pl_floor.update())
        self.pl_views = QStackedWidget()
        self.pl_views.addWidget(self.pl_floor_scroll)
        self.pl_views.addWidget(self.pl_scroll)
        objects = QWidget()
        ol = QVBoxLayout(objects)
        ol.setContentsMargins(0, 4, 0, 0)
        self.pl_filter = QLineEdit()
        self.pl_filter.setPlaceholderText(T("Objekte filtern (Name, Typ, Modell, Rack) …"))
        self.pl_filter.setClearButtonEnabled(True)
        self.pl_filter.textChanged.connect(self.planner_filter_objects)
        ol.addWidget(self.pl_filter)
        self.pl_objects = QTableWidget(0, 10)
        self.pl_objects.setHorizontalHeaderLabels([T("Name"), T("Typ"), T("Modell"), T("Rack"), T("Stellplatz"),
                                                   T("HE"), T("Höhe"), T("Leistung"), T("Anschlüsse"), T("Status")])
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
        self.pl_bottom.addTab(self._planner_participants(), T("Netzwerkteilnehmer"))
        self.pl_bottom.addTab(self.pl_simlog, T("Simulation"))
        center = QSplitter(Qt.Orientation.Vertical)
        center.addWidget(self.pl_views)
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

        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, slot in (("K", self.pl_cable_btn.toggle), ("Ctrl+Z", self.planner_undo),
                           ("Ctrl+Y", self.planner_redo), ("Ctrl+Shift+Z", self.planner_redo),
                           ("Ctrl+S", self.planner_save), ("Ctrl++", lambda: self.planner_zoom(ZoomPan.ZSTEP)),
                           ("Ctrl+=", lambda: self.planner_zoom(ZoomPan.ZSTEP)),
                           ("Ctrl+-", lambda: self.planner_zoom(1 / ZoomPan.ZSTEP)),
                           ("Ctrl+0", lambda: self.planner_zoom(None)), ("F", lambda: self.planner_set_view("floor")),
                           ("V", lambda: self.planner_set_view("front")), ("R", lambda: self.planner_set_view("rear"))):
            QShortcut(QKeySequence(keys), page, slot, context=ctx)
        self.pl_undo, self.pl_redo, self.pl_snap = [], [], None
        self.planner_page = page
        self.planner_apply_theme()
        theme.on_change(self.planner_apply_theme)
        self.planner_set_view("floor")
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
        self.pl_grid_cols, self.pl_grid_rows = QSpinBox(), QSpinBox()
        self.pl_grid_cols.setRange(MIN_GRID, MAX_COLS)
        self.pl_grid_rows.setRange(MIN_GRID, MAX_ROWS)
        self.pl_grid_cols.setToolTip(T("Anzahl Bodenplatten in der Breite: A … Z, danach AA, AB …"))
        self.pl_grid_rows.setToolTip(T("Anzahl Bodenplatten in der Tiefe: 01, 02 …"))
        for spin in (self.pl_grid_cols, self.pl_grid_rows):
            spin.valueChanged.connect(self.planner_grid_edit)
        self.pl_grid_label = QLabel()
        grid = QHBoxLayout()
        grid.addWidget(self.pl_grid_cols)
        grid.addWidget(QLabel("×"))
        grid.addWidget(self.pl_grid_rows)
        grid.addWidget(self.pl_grid_label, 1)
        f.addRow(T("Raster"), grid)
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
        self.pl_r_coord = QLineEdit()
        self.pl_r_coord.setMaxLength(6)
        self.pl_r_coord.setToolTip(T("Linke obere Bodenplatte des Racks im Grundriss, z. B. C07 oder AA12. Dort "
                                     "lässt sich das Rack auch mit der Maus verschieben."))
        self.pl_r_coord.editingFinished.connect(self.planner_rack_place)
        self.pl_r_orient = QComboBox()
        for key, text in (("S", T("Front nach Süden")), ("N", T("Front nach Norden")), ("O", T("Front nach Osten")),
                          ("W", T("Front nach Westen"))):
            self.pl_r_orient.addItem(text, key)
        self.pl_r_orient.currentIndexChanged.connect(self.planner_rack_place)
        f.addRow(T("Name"), self.pl_r_name)
        f.addRow(T("Höhe"), self.pl_r_h)
        f.addRow(T("Stellplatz"), self.pl_r_coord)
        f.addRow(T("Ausrichtung"), self.pl_r_orient)
        f.addRow(T("Belegung"), self.pl_r_load)
        b = QPushButton(T("Rack entfernen"))
        b.setToolTip(T("Nur leere Racks lassen sich entfernen."))
        b.clicked.connect(self.planner_delete_selected)
        f.addRow(b)
        self.pl_props.addWidget(w)
        # 4: Port
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, f = _group(T("Port"))
        self.pl_p_where = QLabel()
        self.pl_p_where.setWordWrap(True)
        self.pl_p_peer = QLabel()
        self.pl_p_peer.setWordWrap(True)
        f.addRow(T("Anschluss"), self.pl_p_where)
        f.addRow(T("Gegenstelle"), self.pl_p_peer)
        row = QHBoxLayout()
        self.pl_p_connect = QPushButton(T("Verbinden …"))
        self.pl_p_connect.setToolTip(T("Gegenstelle aus einer Liste wählen – auch in einem anderen Rack."))
        self.pl_p_connect.clicked.connect(lambda: self.planner_connect_dialog(*self.pl_canvas.sel[1]))
        self.pl_p_trace = QPushButton(T("Strecke …"))
        self.pl_p_trace.setToolTip(T("Zeigt den ganzen Weg dieser Verbindung, auch durch Patchpanels."))
        self.pl_p_trace.clicked.connect(lambda: self.planner_show_trace(*self.pl_canvas.sel[1]))
        self.pl_p_unplug = QPushButton(T("Trennen"))
        self.pl_p_unplug.clicked.connect(self.planner_port_unplug)
        for b in (self.pl_p_connect, self.pl_p_trace, self.pl_p_unplug):
            row.addWidget(b)
        f.addRow(row)
        lay.addWidget(box)
        box, f = _group(T("Transceiver und Netz"))
        self.pl_p_sfp = QComboBox()
        self.pl_p_sfp.setEditable(True)
        self.pl_p_speed = QComboBox()
        self.pl_p_speed.setEditable(True)
        self.pl_p_speed.addItems(["", "1G", "10G", "25G", "40G", "100G", "FC 16G", "FC 32G", "SAS 12G"])
        self.pl_p_mac, self.pl_p_ip, self.pl_p_vlan, self.pl_p_desc = (QLineEdit() for _ in range(4))
        self.pl_p_mac.setPlaceholderText("00:11:22:33:44:55")
        self.pl_p_ip.setPlaceholderText(T("z. B. 10.20.0.11/24"))
        self.pl_p_vlan.setPlaceholderText(T("z. B. 100 oder 100,200"))
        f.addRow(T("SFP / Medium"), self.pl_p_sfp)
        f.addRow(T("Geschwindigkeit"), self.pl_p_speed)
        f.addRow("MAC", self.pl_p_mac)
        f.addRow("IP", self.pl_p_ip)
        f.addRow("VLAN", self.pl_p_vlan)
        f.addRow(T("Beschreibung"), self.pl_p_desc)
        for key, wdg in (("sfp", self.pl_p_sfp), ("speed", self.pl_p_speed)):
            wdg.lineEdit().editingFinished.connect(lambda k=key, x=wdg: self.planner_port_edit(k, x.currentText()))
            wdg.activated.connect(lambda _i, k=key, x=wdg: self.planner_port_edit(k, x.currentText()))
        for key, wdg in (("mac", self.pl_p_mac), ("ip", self.pl_p_ip), ("vlan", self.pl_p_vlan),
                         ("desc", self.pl_p_desc)):
            wdg.editingFinished.connect(lambda k=key, x=wdg: self.planner_port_edit(k, x.text()))
        lay.addWidget(box)
        lay.addStretch(1)
        self.pl_props.addWidget(w)
        return self.pl_props

    def _planner_participants(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        self.pl_part_filter = QLineEdit()
        self.pl_part_filter.setPlaceholderText(T("Teilnehmer filtern (Gerät, IP, MAC, VLAN, SFP) …"))
        self.pl_part_filter.setClearButtonEnabled(True)
        self.pl_part_filter.textChanged.connect(self.planner_filter_participants)
        row.addWidget(self.pl_part_filter, 1)
        b = QPushButton(T("Als CSV exportieren …"))
        b.clicked.connect(self.planner_export_participants)
        row.addWidget(b)
        v.addLayout(row)
        self.pl_parts = QTableWidget(0, 8)
        self.pl_parts.setHorizontalHeaderLabels([T("Gerät"), T("Rack"), T("Port"), T("SFP / Medium"), "IP", "MAC",
                                                 "VLAN", T("Gegenstelle (Ende der Strecke)")])
        self.pl_parts.verticalHeader().setVisible(False)
        self.pl_parts.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pl_parts.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pl_parts.setAlternatingRowColors(True)
        self.pl_parts.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.pl_parts.horizontalHeader().setStretchLastSection(True)
        self.pl_parts.cellClicked.connect(self.planner_participant_clicked)
        v.addWidget(self.pl_parts, 1)
        return w

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

    def planner_set_plan(self, plan, dirty=True, keep_history=False):
        if not keep_history and hasattr(self, "pl_undo"):
            self.pl_undo, self.pl_redo = [], []
        self.planner_sim_stop()
        self.pl_canvas.plan = plan
        self.pl_canvas.sel = None
        self.pl_canvas.pending = None
        plan.sync_inventory(self.planner_inventory())
        self.plan_dirty = dirty
        if hasattr(self, "pl_undo"):
            self.pl_snap = json.dumps(plan.to_dict(), sort_keys=True)
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

    def planner_record(self):
        """Merkt den Stand für Rückgängig; wird nach jeder Änderung am Plan aufgerufen."""
        now = json.dumps(self.plan.to_dict(), sort_keys=True)
        if self.pl_snap is not None and now != self.pl_snap:
            self.pl_undo.append(self.pl_snap)
            del self.pl_undo[:-100]
            self.pl_redo.clear()
        self.pl_snap = now
        self.planner_update_history()

    def planner_update_history(self):
        self.pl_undo_btn.setEnabled(bool(self.pl_undo))
        self.pl_redo_btn.setEnabled(bool(self.pl_redo))

    def _planner_restore(self, snap):
        sel = self.pl_canvas.sel
        self.pl_snap = snap
        view = self.planner_current_view()
        self.planner_set_plan(Plan.from_dict(json.loads(snap)), dirty=True, keep_history=True)
        self.pl_snap = snap
        if sel and ((sel[0] == "dev" and self.plan.dev(sel[1])) or (sel[0] == "rack" and self.plan.rack(sel[1]))
                    or (sel[0] == "cable" and self.plan.cable(sel[1]))):
            self.pl_canvas.select(sel)
        self.planner_set_view(view)
        self.planner_update_history()

    def planner_undo(self):
        if self.pl_undo and self.pl_canvas.sim is None:
            self.pl_redo.append(self.pl_snap)
            self._planner_restore(self.pl_undo.pop())
            self.planner_say(T("Rückgängig gemacht."))

    def planner_redo(self):
        if self.pl_redo and self.pl_canvas.sim is None:
            self.pl_undo.append(self.pl_snap)
            self._planner_restore(self.pl_redo.pop())
            self.planner_say(T("Wiederholt."))

    def planner_changed(self):
        self.plan_dirty = True
        self.planner_record()
        if self.pl_canvas.sim is not None:
            self.planner_sim_recompute(log=False)
        self.planner_refresh()

    def planner_refresh(self):
        self.pl_canvas.relayout()
        self.pl_floor.relayout()
        self.planner_fill_lib()
        self.planner_fill_checks()
        self.planner_fill_nets()
        self.planner_fill_bom()
        self.planner_fill_tree()
        self.planner_fill_objects()
        self.planner_fill_participants()
        self.planner_selection_changed()

    def planner_selection_changed(self):
        self.planner_fill_props()
        self.planner_fill_crumb()
        self.pl_floor.update()

    # ================================================================ Ansicht, Navigator, Objektliste
    def planner_set_view(self, view):
        for key, btn in self.pl_view_btns.items():
            btn.setChecked(key == view)
        self.pl_views.setCurrentIndex(0 if view == "floor" else 1)
        if view == "floor":
            if self.pl_sim_btn.isChecked():
                self.pl_sim_btn.setChecked(False)
        else:
            self.pl_canvas.view = view
        self.pl_canvas.pending = None
        if view != "rear" and self.pl_cable_btn.isChecked():
            self.pl_cable_btn.setChecked(False)
        self.pl_canvas.update()
        self.pl_floor.update()
        self.planner_zoom_shown()
        if not self.pl_canvas.cable_mode and self.pl_canvas.sim is None:
            self.planner_say(T("Grundriss: Mausrad zoomt, mittlere Maustaste verschiebt, Rechtsklick öffnet das Menü, "
                               "Doppelklick öffnet ein Rack.") if view == "floor" else
                             T("Racks: Geräte aus dem Katalog hineinziehen, Rechtsklick für weitere Möglichkeiten, "
                               "Mausrad zoomt."))

    def planner_current_view(self):
        return "floor" if self.pl_views.currentIndex() == 0 else self.pl_canvas.view


    def planner_tile_clicked(self, x, y):
        self.pl_canvas.select(None)
        busy = self.plan.occupied().get((x, y))
        if busy is None:
            self.planner_say(T("Stellplatz {0} gewählt – „＋ Rack“ stellt dort ein neues Rack auf.").format(
                coord_name(x, y)))

    # ================================================================ Design
    def planner_apply_theme(self, *_):
        """Farben der Zeichenflächen und der eigenen Leisten an das Design des Programms anpassen."""
        name = theme.current()
        self.planner_theme = name
        apply_theme(name)
        self.pl_bar.setStyleSheet(BAR_CSS)
        self.pl_crumb.setStyleSheet(CRUMB_CSS)
        self.pl_scroll.setStyleSheet(SCROLL_CSS)
        self.pl_floor_scroll.setStyleSheet(SCROLL_CSS)
        self.pl_lib.setStyleSheet(LIB_CSS)
        self.planner_fill_lib()
        self.pl_legend.setText(" ".join(f"<span style='color:{NET_COLOR[k].name()}'>━━</span> {n}"
                                        for k, n in net_names().items() if k != "patch"))
        self.pl_canvas.update()
        self.pl_floor.update()

    def planner_loc_edit(self, key, edit):
        val = edit.text().strip()
        if getattr(self.plan, key) != val:
            setattr(self.plan, key, val)
            self.plan_dirty = True
            self.planner_record()
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
        elif sel and sel[0] == "port" and plan.dev(sel[1][0]):
            rid = plan.dev(sel[1][0]).get("rack")
        if rid and plan.rack(rid):
            rk = plan.rack(rid)
            parts.append(T("Rack {0}").format(rk["name"]) + (f" ({plan.coord(rk)})" if plan.coord(rk) != rk["name"] else ""))
        if sel and sel[0] == "port" and plan.dev(sel[1][0]):
            d = plan.dev(sel[1][0])
            parts.append(f"{T('HE')} {d['pos']} · {d['name']} · {port_name(d, sel[1][1])}")
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
            where = "" if self.plan.coord(r) == r["name"] else f" · {self.plan.coord(r)}"
            it = QTreeWidgetItem(row_item, [f"{r['name']}{where}  ({L['used']}/{r['h']} {T('HE')})"])
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

    def planner_select(self, kind, ident, scroll=True):
        """Objekt auswählen und in der Rack-Ansicht sichtbar machen."""
        cv = self.pl_canvas
        cv.select((kind, ident))
        if not scroll:
            self.pl_tabs.setCurrentIndex(0)
            return
        if kind == "rack" and self.planner_current_view() == "floor" and self.plan.rack(ident) \
                and self.plan.rack(ident).get("x") is not None:
            r = self.pl_floor.rack_rect(self.plan.rack(ident))
            z = self.pl_floor.zoom
            self.pl_floor_scroll.ensureVisible(int(r.center().x() * z), int(r.center().y() * z), int(80 * z),
                                               int(80 * z))
            self.pl_tabs.setCurrentIndex(0)
            return
        if kind == "dev" and self.planner_current_view() == "floor":
            self.planner_set_view("front")
        if kind == "dev" and self.plan.dev(ident) and self.plan.dev(ident).get("rack"):
            d = self.plan.dev(ident)
            r = cv.dev_rect(d)
            i = self.plan.rack_index(d["rack"])
        elif kind == "rack" and self.plan.rack(ident):
            i = self.plan.rack_index(ident)
            r = QRectF(cv.rack_x(i), cv.M, cv.RW, cv.HEAD)
        else:
            return
        z = cv.zoom                                  # ganzes Rack in der Breite zeigen, Kabel rechts daneben
        x = cv.rack_x(i) + cv.RW / 2 + 20
        self.pl_scroll.ensureVisible(int(x * z), int(r.center().y() * z), int((cv.RW / 2 + 50) * z), int(140 * z))
        self.pl_tabs.setCurrentIndex(0)

    def planner_kind_name(self, d):
        if d["kind"] == "switch" and d.get("role") == "fc":
            return T("FC-Switch")
        return {"server": T("Server"), "switch": T("Switch"), "fw": T("Firewall"), "patch": T("Patchpanel"),
                "pdu": T("PDU"), "blank": T("Blindblende"), "storage": T("Storage-Controller"),
                "shelf": T("Disk Shelf"), "bridge": T("FC-Bridge")}.get(d["kind"], d["kind"])

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
                    plan.coord(plan.rack(d["rack"])), d["pos"], d["u"], f"{power} W" if power else "", f"{used}/{len(d['ports'])}" if d["ports"] else "",
                    status.get(worst.get(d["id"]), T("OK"))]
            for c, val in enumerate(vals):
                it = QTableWidgetItem()
                it.setData(Qt.ItemDataRole.DisplayRole, val)
                it.setData(Qt.ItemDataRole.UserRole, d["id"])
                if c == 9 and d["id"] in worst:
                    it.setForeground(QColor(LEVEL_COLOR[worst[d["id"]]]))
                t.setItem(i, c, it)
        t.setSortingEnabled(True)
        self.pl_bottom.setTabText(0, T("Objekte ({0})").format(len(devs)))
        self.planner_filter_objects(self.pl_filter.text())

    def planner_filter_objects(self, text):
        text = (text or "").strip().lower()
        t = self.pl_objects
        for r in range(t.rowCount()):
            row = " ".join(t.item(r, c).text().lower() for c in range(5) if t.item(r, c))
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
            it.setBackground(QBrush(HEAD_BG))
            it.setForeground(QBrush(TEXT))
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
        header(T("Racks"))
        for h in RACK_SIZES:
            it = QListWidgetItem(T("Rack {0} HE").format(h) + "\n" + T("600 × 1200 mm · auf den Grundriss ziehen"))
            it.setData(Qt.ItemDataRole.UserRole, f"rack:{h}")
            it.setToolTip(T("Auf eine freie Bodenplatte im Grundriss ziehen oder doppelklicken."))
            lib.addItem(it)
        for group, entries in catalog_texts():
            header(group)
            for key, name, sub in entries:
                it = QListWidgetItem(f"{name}\n{sub} · {CATALOG[key]['u']} " + T("HE"))
                it.setData(Qt.ItemDataRole.UserRole, f"cat:{key}")
                it.setToolTip(CATALOG[key]["model"] or name)
                lib.addItem(it)
        self.planner_filter_lib(self.pl_lib_filter.text())

    def planner_filter_lib(self, text):
        """Katalog eingrenzen; eine Überschrift bleibt sichtbar, solange darunter etwas passt."""
        text = (text or "").strip().lower()
        lib = self.pl_lib
        head, seen = None, False
        for i in range(lib.count()):
            it = lib.item(i)
            is_head = bool(it.flags() & Qt.ItemFlag.ItemIsEnabled) and not it.data(Qt.ItemDataRole.UserRole) \
                and it.font().bold()
            if is_head:
                if head is not None:
                    head.setHidden(bool(text) and not seen)
                head, seen = it, False
                continue
            hit = not text or text in it.text().lower() or text in (it.toolTip() or "").lower()
            it.setHidden(not hit)
            seen = seen or (hit and bool(it.data(Qt.ItemDataRole.UserRole)))
        if head is not None:
            head.setHidden(bool(text) and not seen)

    def planner_lib_double(self, item):
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, _, key = payload.partition(":")
        if kind == "rack":
            self.planner_add_rack(h=int(key))
            return
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
    def planner_add_rack(self, h=42, at=None):
        r = self.plan.add_rack(h=h, at=at or self.pl_floor.tile_sel)
        self.pl_floor.tile_sel = None
        if r.get("x") is None:
            self.planner_say(T("Rack {0} angelegt, aber im Grundriss ist kein Platz mehr frei.").format(r["name"]),
                             True)
        self.pl_canvas.sel = ("rack", r["id"])
        self.planner_changed()
        if self.planner_current_view() != "floor":
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
            elif sel and sel[0] == "port" and plan.dev(sel[1][0]) and plan.dev(sel[1][0]).get("rack"):
                self._planner_fill_port(*sel[1])
            elif sel and sel[0] == "rack" and plan.rack(sel[1]):
                r = plan.rack(sel[1])
                L = plan.rack_load(r["id"])
                self.pl_r_name.setText(r["name"])
                self.pl_r_h.setValue(r["h"])
                self.pl_r_coord.setText(plan.coord(r) if r.get("x") is not None else "")
                self.pl_r_orient.setCurrentIndex(max(0, self.pl_r_orient.findData(r["orient"])))
                self.pl_r_load.setText(T("{0} von {1} HE · {2} W Last · {3} W PDU").format(
                    L["used"], r["h"], L["w"], L["cap"]))
                self.pl_props.setCurrentIndex(3)
            else:
                self.pl_canvas.sel = None
                for key, edit in self.pl_loc.items():
                    if not edit.hasFocus():
                        edit.setText(getattr(plan, key))
                self.pl_grid_cols.setValue(plan.cols)
                self.pl_grid_rows.setValue(plan.rows)
                self.pl_grid_label.setText(f"A01 … {coord_name(plan.cols - 1, plan.rows - 1)}")
                devs = plan.placed()
                warn = sum(1 for c in plan.checks() if c[0] != "info")
                self.pl_summary.setText(T(
                    "<p><b>{0}</b> Racks · <b>{1}</b> Geräte · <b>{2}</b> Kabel · <b>{3}</b> Hinweise</p>"
                    "<p><b>So geht's</b><br>1. Server und Switches aus der Bibliothek in ein Rack ziehen.<br>"
                    "2. Gerät oder Rack anklicken: Name, Einbauhöhe, Rackhöhe hier ändern.<br>"
                    "3. „Verkabeln“ (Taste K): Port anklicken, dann den Gegenport.<br>"
                    "4. „Prüfung“ zeigt Lücken, „Netze“ vergibt Hostnamen und Adressen.<br>"
                    "5. „Ausfall simulieren“ zeigt, welche Server ein gezogenes Kabel trifft.<br>"
                    "6. Mausrad zoomt, mittlere Maustaste verschiebt, Rechtsklick öffnet ein Menü, "
                    "Strg+Z macht rückgängig.</p>").format(
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
                peer = f"{plan.dev(od)['name']} · {port_name(plan.dev(od), op)}"
            a = QTableWidgetItem(port_name(d, p["id"]))
            if c:
                a.setForeground(NET_COLOR[c["net"]].darker(115))
            self.pl_d_ports.setItem(i, 0, a)
            self.pl_d_ports.setItem(i, 1, QTableWidgetItem(peer or T("frei")))
        self.pl_props.setCurrentIndex(1)

    def _planner_fill_cable(self, c):
        plan = self.plan
        A, B = plan.dev(c["a"][0]), plan.dev(c["b"][0])
        self.pl_c_from.setText(f"{A['name']} · {port_name(A, c['a'][1])}")
        self.pl_c_to.setText(f"{B['name']} · {port_name(B, c['b'][1])}")
        self.pl_c_net.setCurrentIndex(max(0, self.pl_c_net.findData(c["net"])))
        self.pl_c_med.setText(plan.cable_medium(c))
        self.pl_c_len.setText(T("{0} m (geschätzt)").format(f"{plan.cable_length(c):g}".replace(".", ",")))
        tag = lambda d, pid: f"{d['name']}/{port_name(d, pid).replace(' ', '')}"
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
            self.planner_record()
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

    def planner_rack_place(self, *_):
        if self._pl_filling:
            return
        sel = self.pl_canvas.sel
        r = self.plan.rack(sel[1]) if sel and sel[0] == "rack" else None
        if not r:
            return
        text = self.pl_r_coord.text().strip()
        orient = self.pl_r_orient.currentData()
        xy = parse_coord(text) if text else ((r["x"], r["y"]) if r.get("x") is not None else None)
        if xy is None:
            self.planner_say(T("„{0}“ ist keine Koordinate – erwartet z. B. C07 oder AA12.").format(text), True)
        elif not self.plan.move_rack(r["id"], xy[0], xy[1], orient):
            self.planner_say(T("Auf {0} ist kein Platz für Rack {1}.").format(coord_name(*xy), r["name"]), True)
        else:
            self.planner_say(T("Rack {0} steht jetzt auf {1}.").format(r["name"], self.plan.coord(r)))
        self.planner_changed()

    def planner_grid_edit(self, *_):
        if self._pl_filling:
            return
        out = self.plan.set_grid(self.pl_grid_cols.value(), self.pl_grid_rows.value())
        if out:
            self.planner_say(T("{0} stünde dann außerhalb des Rasters – erst verschieben.").format(
                ", ".join(r["name"] for r in out)), True)
        self.planner_changed()

    # ================================================================ Ports, Verbinden, Strecke, Teilnehmer
    def planner_select_port(self, did, pid):
        if self.pl_canvas.view != "rear":
            self.planner_set_view("rear")
        self.pl_canvas.select(("port", (did, pid)))
        d = self.plan.dev(did)
        r = self.pl_canvas.dev_rect(d)
        z = self.pl_canvas.zoom
        self.pl_scroll.ensureVisible(int(r.right() * z), int(r.center().y() * z), int(160 * z), int(140 * z))
        self.pl_tabs.setCurrentIndex(0)

    def _planner_fill_port(self, did, pid):
        plan = self.plan
        d = plan.dev(did)
        p = plan.port(d, pid)
        r = plan.rack(d["rack"])
        self.pl_p_where.setText(f"<b>{html_escape(d['name'])} · {html_escape(port_name(d, pid))}</b><br>"
                                + T("Rack {0} · HE {1}").format(rack_label(plan, r), d["pos"]))
        c = plan.cable_at(did, pid)
        if c:
            end_d, end_p, _ = plan.trace(did, pid)[-1]
            od, op = plan.other_end(c, did, pid)
            text = f"{plan.dev(od)['name']} · {port_name(plan.dev(od), op)}"
            if end_d["id"] != od:
                text += "<br>" + T("Ende der Strecke: {0}").format(f"{end_d['name']} · {port_name(end_d, end_p)}")
            self.pl_p_peer.setText(text)
        else:
            self.pl_p_peer.setText(T("frei"))
        self.pl_p_connect.setEnabled(not c)
        self.pl_p_trace.setEnabled(bool(c))
        self.pl_p_unplug.setEnabled(bool(c))
        self.pl_p_sfp.clear()
        self.pl_p_sfp.addItems([""] + SFP_PRESETS.get(p["k"], []))
        self.pl_p_sfp.setEditText(p.get("sfp", ""))
        self.pl_p_sfp.lineEdit().setCursorPosition(0)          # Anfang zeigen, nicht das Ende eines langen Namens
        self.pl_p_speed.setEditText(p.get("speed", ""))
        self.pl_p_mac.setText(p.get("mac", ""))
        self.pl_p_ip.setText(p.get("ip", ""))
        self.pl_p_vlan.setText(p.get("vlan", ""))
        self.pl_p_desc.setText(p.get("desc", ""))
        self.pl_props.setCurrentIndex(4)

    def planner_port_edit(self, key, value):
        if self._pl_filling:
            return
        sel = self.pl_canvas.sel
        if not sel or sel[0] != "port":
            return
        d = self.plan.dev(sel[1][0])
        p = self.plan.port(d, sel[1][1]) if d else None
        if p is None:
            return
        value = (value or "").strip()[:60]
        if key == "mac" and value:
            value = value.lower().replace("-", ":")
            if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", value):
                self.planner_say(T("„{0}“ ist keine MAC-Adresse (z. B. 00:11:22:33:44:55).").format(value), True)
                return
        if key == "ip" and value:
            try:
                ipaddress.ip_interface(value)
            except ValueError:
                self.planner_say(T("„{0}“ ist keine IP-Adresse (z. B. 10.20.0.11 oder 10.20.0.11/24).").format(value),
                                 True)
                return
        if (p.get(key) or "") == value:
            return
        if value:
            p[key] = value
        else:
            p.pop(key, None)
        self.plan_dirty = True
        self.planner_record()
        self.planner_fill_participants()
        self.planner_fill_bom()

    def planner_port_unplug(self):
        sel = self.pl_canvas.sel
        if sel and sel[0] == "port":
            c = self.plan.cable_at(*sel[1])
            if c:
                self.plan.disconnect(c["id"])
                self.planner_changed()

    def planner_connect_dialog(self, did, pid):
        """Gegenstelle aus einer Liste wählen – schneller als Scrollen, wenn die Racks weit auseinander stehen."""
        dlg = ConnectDialog(self, self.plan, did, pid)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.choice:
            return
        od, op, net = dlg.choice
        c, err = self.plan.connect(did, pid, od, op, net)
        if err:
            self.planner_say(err, True)
            return
        A, B = self.plan.dev(did), self.plan.dev(od)
        self.planner_say(T("Kabel gezogen: {0} · {1} ↔ {2} · {3}").format(
            A["name"], port_name(A, pid), B["name"], port_name(B, op)))
        self.pl_canvas.sel = ("port", (did, pid))
        self.planner_changed()

    def planner_show_trace(self, did, pid):
        hops = self.plan.trace(did, pid)
        # vom Ende her lesen, wenn der Port mitten in der Strecke liegt (Patchpanel)
        start = hops[-1]
        hops = self.plan.trace(start[0]["id"], start[1]) if len(hops) > 1 else hops
        self.pl_canvas.highlight = {c["id"] for _, _, c in hops if c}
        if self.planner_current_view() != "rear":
            self.planner_set_view("rear")
        self.pl_canvas.update()
        try:
            TraceDialog(self, self.plan, hops).exec()
        finally:
            self.pl_canvas.highlight = set()
            self.pl_canvas.update()

    def planner_fill_participants(self):
        rows = self.plan.participants()
        t = self.pl_parts
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            d, p = r["dev"], r["port"]
            rack = self.plan.rack(d["rack"])
            vals = [d["name"], rack_label(self.plan, rack), port_name(d, p["id"]), r["sfp"],
                    r["ip"], r["mac"], r["vlan"], r["peer"]]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                it.setData(Qt.ItemDataRole.UserRole, f"{d['id']}\x1f{p['id']}")
                t.setItem(i, c, it)
        t.setSortingEnabled(True)
        self.pl_bottom.setTabText(1, T("Netzwerkteilnehmer ({0})").format(len(rows)))
        self.planner_filter_participants(self.pl_part_filter.text())

    def planner_filter_participants(self, text):
        text = (text or "").strip().lower()
        t = self.pl_parts
        for r in range(t.rowCount()):
            row = " ".join(t.item(r, c).text().lower() for c in range(t.columnCount()) if t.item(r, c))
            t.setRowHidden(r, bool(text) and text not in row)

    def planner_participant_clicked(self, row, _col=0):
        item = self.pl_parts.item(row, 0)
        if item is not None:
            did, _, pid = item.data(Qt.ItemDataRole.UserRole).partition("\x1f")
            self.planner_select_port(did, pid)

    def planner_export_participants(self):
        path, _ = QFileDialog.getSaveFileName(self, T("Netzwerkteilnehmer exportieren"),
                                              str(HERE / "teilnehmer.csv"), T("CSV-Datei (*.csv)"))
        if not path:
            return
        head = [T("Gerät"), T("Rack"), T("Stellplatz"), T("HE"), T("Port"), T("SFP / Medium"), T("Geschwindigkeit"),
                "IP", "MAC", "VLAN", T("Beschreibung"), T("Gegenstelle (Ende der Strecke)")]
        lines = [";".join(head)]
        for r in self.plan.participants():
            d, p = r["dev"], r["port"]
            rack = self.plan.rack(d["rack"])
            vals = [d["name"], rack["name"], self.plan.coord(rack), str(d["pos"]), port_name(d, p["id"]), r["sfp"],
                    p.get("speed", ""), r["ip"], r["mac"], r["vlan"], p.get("desc", ""), r["peer"]]
            lines.append(";".join(str(v).replace(";", ",") for v in vals))
        try:
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        except OSError as e:
            QMessageBox.warning(self, T("Netzwerkteilnehmer exportieren"), T("Datei nicht schreibbar:\n{0}").format(e))
            return
        self.planner_say(T("Netzwerkteilnehmer gespeichert: {0}").format(path))

    # ================================================================ Zoom
    def planner_active_canvas(self):
        return self.pl_floor if self.planner_current_view() == "floor" else self.pl_canvas

    def planner_zoom(self, factor):
        cv = self.planner_active_canvas()
        cv.set_zoom(1.0 if factor is None else cv.zoom * factor)

    def planner_zoom_fit(self):
        self.planner_active_canvas().zoom_fit()

    def planner_zoom_shown(self, *_):
        self.pl_zoom_label.setText(f"{round(self.planner_active_canvas().zoom * 100)} %")

    # ================================================================ Kontextmenüs (Rechtsklick)
    def planner_context(self, kind, ident, gpos):
        menu = self.planner_context_menu(kind, ident)
        if menu is not None and not menu.isEmpty():
            menu.exec(gpos)

    def planner_context_menu(self, kind, ident):
        """Menü zum Objekt unter dem Mauszeiger. Getrennt von planner_context, damit es sich prüfen lässt."""
        plan = self.plan
        menu = QMenu(self.planner_page)
        if self.pl_canvas.sim is not None:
            menu.addAction(T("Simulation beenden"), lambda: self.pl_sim_btn.setChecked(False))
            return menu
        if kind == "tile":
            x, y = ident
            menu.addSection(T("Bodenplatte {0}").format(coord_name(x, y)))
            sub = menu.addMenu(T("Rack hier aufstellen"))
            free = plan.can_stand(None, x, y, "S")
            sub.setEnabled(free)
            for h in RACK_SIZES:
                sub.addAction(T("Rack {0} HE").format(h), lambda h=h: self.planner_add_rack(h=h, at=(x, y)))
            act = menu.addAction(T("Rackreihe ab hier aufstellen …"), lambda: self.planner_add_row(x, y))
            act.setEnabled(free)
            menu.addSeparator()
            menu.addAction(T("Alles einpassen"), self.planner_zoom_fit)
        elif kind == "rack":
            r = plan.rack(ident)
            self.pl_canvas.select(("rack", ident))
            menu.addSection(T("Rack {0}").format(r["name"]))
            menu.addAction(T("Vorderseite öffnen"), lambda: self.planner_open_rack(ident))
            menu.addAction(T("Rückseite öffnen"), lambda: self.planner_open_rack(ident, "rear"))
            menu.addSeparator()
            menu.addAction(T("Umbenennen …"), lambda: self.planner_rename_rack(ident))
            if r.get("x") is not None and plan.coord(r) != r["name"]:
                menu.addAction(T("Nach Stellplatz benennen ({0})").format(plan.coord(r)),
                               lambda: self.planner_rack_set(ident, name=plan.coord(r)))
            hm = menu.addMenu(T("Höhe"))
            for h in sorted(set(RACK_SIZES) | {r["h"]}):
                a = hm.addAction(T("{0} HE").format(h), lambda h=h: self.planner_rack_set(ident, h=h))
                a.setCheckable(True)
                a.setChecked(h == r["h"])
            om = menu.addMenu(T("Front ausrichten"))
            for key, text in (("S", T("Front nach Süden")), ("N", T("Front nach Norden")),
                              ("O", T("Front nach Osten")), ("W", T("Front nach Westen"))):
                a = om.addAction(text, lambda k=key: self.planner_rack_set(ident, orient=k))
                a.setCheckable(True)
                a.setChecked(key == r["orient"])
            tm = menu.addMenu(T("Vorlage einsetzen"))
            for key, text in (("head", T("Management-Kopf (Patchpanel, OOB-Switch, PDU)")),
                              ("ha", T("HA-Paar (zwei Leaf-Switches mit Querverbindung)")),
                              ("metrocluster", T("NetApp MetroCluster FC (FC-Switches, Cluster-Switches, AFF, "
                                                 "ATTO-Bridges, Shelves)"))):
                tm.addAction(text, lambda k=key: (self.pl_canvas.select(("rack", ident)), self.planner_template(k)))
            menu.addSeparator()
            menu.addAction(T("Rack löschen …"), lambda: self.planner_delete_rack(ident))
        elif kind == "slot":
            rid, pos = ident
            r = plan.rack(rid)
            menu.addSection(T("Rack {0} · HE {1}").format(r["name"], pos))
            sub = menu.addMenu(T("Hier einbauen"))
            inv = [d for d in plan.devs if d.get("inv") and not d.get("rack")]
            if inv:
                im = sub.addMenu(T("Server aus der Serverliste"))
                for d in inv:
                    a = im.addAction(f"{d['name']} ({d['u']} {T('HE')})",
                                     lambda d=d: self.planner_insert(rid, pos, inv=d["id"]))
                    a.setEnabled(self._planner_slot_for(rid, pos, d["u"]) is not None)
            for group, entries in catalog_texts():
                gm = sub.addMenu(group)
                for key, name, _ in entries:
                    u = CATALOG[key]["u"]
                    a = gm.addAction(f"{name} ({u} {T('HE')})", lambda k=key: self.planner_insert(rid, pos, cat=k))
                    a.setEnabled(self._planner_slot_for(rid, pos, u) is not None)
            menu.addSeparator()
            menu.addAction(T("Rack-Eigenschaften"), lambda: self.planner_select("rack", rid))
        elif kind == "dev":
            d = plan.dev(ident)
            self.pl_canvas.select(("dev", ident))
            menu.addSection(d["name"])
            menu.addAction(T("Eigenschaften"), lambda: self.pl_tabs.setCurrentIndex(0))
            if not d.get("inv"):
                menu.addAction(T("Umbenennen …"), lambda: self.planner_rename_dev(ident))
            hm = menu.addMenu(T("Einbauhöhe"))
            for u in sorted({1, 2, 3, 4, d["u"]}):
                a = hm.addAction(T("{0} HE").format(u), lambda u=u: self.planner_dev_height(ident, u))
                a.setCheckable(True)
                a.setChecked(u == d["u"])
                a.setEnabled(u == d["u"] or plan.fits(d["rack"], d["pos"], u, d["id"]))
            if not d.get("inv") and d.get("cat"):
                menu.addAction(T("Duplizieren"), lambda: self.planner_duplicate(ident))
            n = sum(1 for p in d["ports"] if plan.cable_at(d["id"], p["id"]))
            if n:
                menu.addAction(T("Alle Kabel abziehen ({0})").format(n), lambda: self.planner_unplug(ident))
            menu.addSeparator()
            menu.addAction(T("Ausbauen") if d.get("inv") else T("Entfernen"),
                           lambda: (self.pl_canvas.select(("dev", ident)), self.planner_delete_selected()))
        elif kind == "port":
            did, pid = ident
            d = plan.dev(did)
            c = plan.cable_at(did, pid)
            menu.addSection(f"{d['name']} · {port_name(d, pid)}")
            menu.addAction(T("Port-Eigenschaften (SFP, IP, MAC, VLAN)"), lambda: self.planner_select_port(did, pid))
            if c:
                menu.addAction(T("Strecke anzeigen …"), lambda: self.planner_show_trace(did, pid))
                od, op = plan.other_end(c, did, pid)
                menu.addAction(T("Gegenstelle zeigen: {0}").format(f"{plan.dev(od)['name']} · "
                                                                  f"{port_name(plan.dev(od), op)}"),
                               lambda: self.planner_select("dev", od))
                menu.addAction(T("Kabel entfernen"), lambda: self.planner_remove_cable(c["id"]))
            else:
                menu.addAction(T("Verbinden mit … (auch anderes Rack)"), lambda: self.planner_connect_dialog(did, pid))
                menu.addAction(T("Kabel von hier ziehen"), lambda: self.planner_start_cable(did, pid))
        elif kind == "cable":
            c = plan.cable(ident)
            self.pl_canvas.select(("cable", ident))
            A, B = plan.dev(c["a"][0]), plan.dev(c["b"][0])
            menu.addSection(f"{A['name']} ↔ {B['name']}")
            menu.addAction(T("Strecke anzeigen …"), lambda: self.planner_show_trace(*c["a"]))
            nm = menu.addMenu(T("Netz"))
            for k, name in net_names().items():
                a = nm.addAction(name, lambda k=k: self.planner_set_cable_net(ident, k))
                a.setCheckable(True)
                a.setChecked(k == c["net"])
            menu.addAction(T("Kabel entfernen"), lambda: self.planner_remove_cable(ident))
        else:
            menu.addAction(T("＋ Rack"), lambda: self.planner_add_rack())
            menu.addAction(T("Alles einpassen"), self.planner_zoom_fit)
        return menu

    def _planner_slot_for(self, rid, he, u):
        """Unterste HE für ein Gerät mit `u` HE, das an HE `he` sitzen soll (oben bündig, sonst unten bündig)."""
        for pos in (he - u + 1, he):
            if self.plan.fits(rid, pos, u):
                return pos
        return None

    def planner_insert(self, rid, he, cat=None, inv=None):
        d = self.plan.new_device(cat) if cat else self.plan.dev(inv)
        pos = self._planner_slot_for(rid, he, d["u"])
        if pos is None or not self.plan.place(d, rid, pos):
            self.planner_say(T("Dort ist kein Platz: {0} HE ab HE {1} sind belegt.").format(d["u"], he), True)
            return
        self.pl_canvas.sel = ("dev", d["id"])
        self.planner_changed()

    def planner_open_rack(self, rid, view="front"):
        self.planner_set_view(view)
        self.planner_select("rack", rid)

    def planner_add_row(self, x, y):
        n, ok = QInputDialog.getInt(self, T("Rackreihe aufstellen"), T("Wie viele Racks nebeneinander (42 HE)?"),
                                    4, 1, 30)
        if not ok:
            return
        made = 0
        for i in range(n):
            if not self.plan.can_stand(None, x + i, y, "S"):
                break
            r = self.plan.add_rack(coord_name(x + i, y), 42, at=(x + i, y))
            made += 1 if r.get("x") is not None else 0
        self.pl_floor.tile_sel = None
        self.planner_say(T("{0} Racks aufgestellt.").format(made) + ("" if made == n else " " + T(
            "Weiter rechts war kein Platz mehr.")), made < n)
        self.planner_changed()

    def planner_rename_rack(self, rid):
        r = self.plan.rack(rid)
        name, ok = QInputDialog.getText(self, T("Rack umbenennen"), T("Neuer Name:"), text=r["name"])
        if ok and name.strip():
            self.planner_rack_set(rid, name=name.strip()[:20])

    def planner_rack_set(self, rid, name=None, h=None, orient=None):
        r = self.plan.rack(rid)
        if name:
            r["name"] = name
        if h and h != r["h"]:
            over = self.plan.set_rack_height(rid, h)
            if over:
                self.planner_say(T("{0} sitzt über HE {1} – erst umsetzen.").format(
                    ", ".join(d["name"] for d in over), h), True)
        if orient and orient != r["orient"] and r.get("x") is not None:
            if not self.plan.move_rack(rid, r["x"], r["y"], orient):
                self.planner_say(T("Gedreht passt Rack {0} hier nicht hin.").format(r["name"]), True)
        self.pl_canvas.sel = ("rack", rid)
        self.planner_changed()

    def planner_delete_rack(self, rid):
        r = self.plan.rack(rid)
        inside = [d for d in self.plan.devs if d.get("rack") == rid]
        if inside:
            box = QMessageBox(QMessageBox.Icon.Question, T("Rack löschen"),
                              T("Rack {0} enthält {1} Geräte. Trotzdem löschen?").format(r["name"], len(inside)),
                              parent=self)
            box.setInformativeText(T("Server aus der Serverliste kommen zurück in den Katalog, alle anderen Geräte "
                                     "und ihre Kabel fallen weg. Strg+Z macht es rückgängig."))
            ok = box.addButton(T("Löschen"), QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() is not ok:
                return
        self.plan.remove_rack(rid, force=True)
        self.pl_canvas.sel = None
        self.planner_say(T("Rack {0} gelöscht.").format(r["name"]))
        self.planner_changed()

    def planner_rename_dev(self, did):
        d = self.plan.dev(did)
        name, ok = QInputDialog.getText(self, T("Gerät umbenennen"), T("Neuer Name:"), text=d["name"])
        if ok and name.strip():
            d["name"] = name.strip()[:40]
            self.planner_changed()

    def planner_dev_height(self, did, u):
        d = self.plan.dev(did)
        if u != d["u"] and not self.plan.set_height(d, u):
            self.planner_say(T("{0} HE passen hier nicht – darüber ist belegt. Erst umsetzen.").format(u), True)
            return
        self.planner_changed()

    def planner_duplicate(self, did):
        d = self.plan.dev(did)
        new = self.plan.new_device(d["cat"])
        new.update(model=d["model"], w=d["w"], cap=d.get("cap", 0))
        if new["u"] != d["u"]:
            self.plan.set_height(new, d["u"])
        pos = next((p for p in range(d["pos"] - new["u"], 0, -1) if self.plan.fits(d["rack"], p, new["u"])), None)
        pos = pos or self.plan.free_pos(d["rack"], new["u"])
        if pos is None:
            self.planner_say(T("Im Rack {0} ist nicht genug Platz.").format(self.plan.rack(d["rack"])["name"]), True)
            return
        self.plan.place(new, d["rack"], pos)
        self.pl_canvas.sel = ("dev", new["id"])
        self.planner_changed()

    def planner_unplug(self, did):
        self.plan.cables = [c for c in self.plan.cables if c["a"][0] != did and c["b"][0] != did]
        self.planner_changed()

    def planner_remove_cable(self, cid):
        self.plan.disconnect(cid)
        if self.pl_canvas.sel == ("cable", cid):
            self.pl_canvas.sel = None
        self.planner_changed()

    def planner_set_cable_net(self, cid, net):
        c = self.plan.cable(cid)
        if c and net in NETS:
            c["net"] = net
            self.planner_changed()

    def planner_start_cable(self, did, pid):
        if not self.pl_cable_btn.isChecked():
            self.pl_cable_btn.setChecked(True)
        self.pl_canvas.pending = (did, pid)
        self.pl_canvas.update()
        d = self.plan.dev(did)
        self.planner_say(T("{0} · {1} gewählt – jetzt den Gegenport anklicken (Esc bricht ab).").format(
            d["name"], port_name(d, pid)))

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
            self.planner_record()
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
        LOG.debug(T("Aufbauplaner: {0} Zeilen in „Neuware“ aktualisiert, {1} neu angelegt.").format(updated, added))
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
            self.pl_bottom.setCurrentWidget(self.pl_simlog)
            self.planner_fill_crumb()
            self.planner_fill_props()
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
            for d in self.plan.placed():
                if d["kind"] not in ("server", "storage", "shelf", "bridge"):
                    continue
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
        servers = [d for d in self.plan.placed() if d["kind"] in ("server", "storage", "shelf", "bridge")]
        up = sum(1 for d in servers if cv.sim.get(d["id"]) != "down")
        deg = sum(1 for d in servers if cv.sim.get(d["id"]) == "degraded")
        self.planner_say(T("Simulation: {0} von {1} Geräten erreichbar, {2} davon eingeschränkt.").format(
            up, len(servers), deg), up < len(servers))

    def planner_sim_click(self, kind, ident):
        cv = self.pl_canvas
        if kind == "cable":
            c = self.plan.cable(ident)
            A, B = self.plan.dev(c["a"][0]), self.plan.dev(c["b"][0])
            what = f"{A['name']} · {port_name(A, c['a'][1])} ↔ {B['name']} · {port_name(B, c['b'][1])}"
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

    def planner_demo_netapp(self):
        if not self.planner_confirm_discard():
            return
        self.plan_path = None
        self.planner_set_plan(netapp_demo_plan(self.planner_inventory()), dirty=False)
        self.planner_say(T("Beispiel NetApp MetroCluster geladen: Rack D04 enthält AFF-HA-Paar, ATTO-Bridges, Shelves, "
                           "FC- und Cluster-Switches."))

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
        LOG.debug(T("Aufbauplan gespeichert: {0}").format(path))
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
