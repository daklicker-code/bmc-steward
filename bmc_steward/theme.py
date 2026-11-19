"""BMC-Steward: Erscheinungsbild für das ganze Programm – dunkel und hell.

„Automatisch“ (Vorgabe) folgt der Hell/Dunkel-Einstellung des Systems, unter Windows wie unter Linux, und
wechselt mit, wenn das System umschaltet. Meldet das System nichts, gilt: Windows hell, sonst dunkel.
Die Wahl steht in <Datenordner>/bmc-design.json (nur das Design, keine Zugangsdaten).

Gezeichnet wird mit dem Qt-Stil „Fusion“, weil nur er Farbpalette und Stylesheet auf allen Systemen gleich
umsetzt. Seiten mit eigener Zeichenfläche (Aufbauplaner, Konsole) fragen mit current() nach dem Design und
hängen sich mit on_change() an Wechsel.
"""
import json
import sys
import types
import weakref
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPalette

from .i18n import DATA, write_private

CHOICES = ("auto", "light", "dark")
PREFS = DATA / "bmc-design.json"
WINDOWS = sys.platform.startswith("win")

TOKENS = {
    "dark": dict(bg="#0b1214", panel="#0f191b", card="#101a1c", line="#1d2b2e", line2="#23363a",
                 input="#0b1214", btn="#142124", text="#d6e2e0", muted="#8aa29e", dim="#5f7a76",
                 accent="#34d399", accent2="#6ee7b7", accent_soft="#16302b", accent_strong="#1f6b52",
                 acc_top="#34d399", acc_bot="#22b07f", on_accent="#04261b", warn="#fbbf24", err="#f87171",
                 warn_soft="#2a1c07", err_soft="#2a1212", dhcp="#facc15", dhcp_soft="#2a2408", dhcp_line="#6b5a12"),
    "light": dict(bg="#f3f5f4", panel="#ffffff", card="#ffffff", line="#e3e8e6", line2="#d6dedb",
                  input="#fbfcfc", btn="#ffffff", text="#1f2a28", muted="#5d6b68", dim="#94a3a0",
                  accent="#16604f", accent2="#16604f", accent_soft="#e3f2ec", accent_strong="#16604f",
                  acc_top="#1d7a64", acc_bot="#16604f", on_accent="#ffffff", warn="#b45309", err="#c0392b",
                  warn_soft="#fff4e2", err_soft="#fdecec", dhcp="#7a5c00", dhcp_soft="#fdf6dc", dhcp_line="#f1df9b"),
}

_current = None
_listeners = []
_icons_dir = None


# ------------------------------------------------------------------ Wahl merken
def load_choice():
    try:
        choice = json.loads(PREFS.read_text(encoding="utf-8")).get("theme")
    except (OSError, ValueError, AttributeError):
        choice = None
    return choice if choice in CHOICES else "auto"


def save_choice(choice):
    try:
        write_private(PREFS, json.dumps({"theme": choice}) + "\n")
    except OSError:
        pass


def system_scheme():
    """"dark" oder "light" laut System; ohne Auskunft Windows hell, sonst dunkel."""
    app = QGuiApplication.instance()
    try:
        scheme = app.styleHints().colorScheme() if app else None
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except AttributeError:                          # Qt älter als 6.5
        pass
    return "light" if WINDOWS else "dark"


def resolve(choice):
    return system_scheme() if choice == "auto" else choice


def current():
    """Das gerade aktive Design ("dark" oder "light")."""
    return _current or resolve(load_choice())


def tokens(name=None):
    return TOKENS[name or current()]


def on_change(callback):
    """callback(name) nach jedem Wechsel des Designs. Methoden werden nur schwach gehalten: Ist ihr Fenster
    weg, fällt die Anmeldung von selbst heraus."""
    _listeners.append(weakref.WeakMethod(callback) if isinstance(callback, types.MethodType) else (lambda: callback))


# ------------------------------------------------------------------ Palette und Stylesheet
def palette(name):
    t = TOKENS[name]
    pal = QPalette()
    roles = QPalette.ColorRole
    for role, key in ((roles.Window, "bg"), (roles.WindowText, "text"), (roles.Base, "input"),
                      (roles.AlternateBase, "card"), (roles.Text, "text"), (roles.Button, "btn"),
                      (roles.ButtonText, "text"), (roles.Highlight, "accent_strong"), (roles.Mid, "line2"),
                      (roles.PlaceholderText, "dim"), (roles.ToolTipBase, "card"), (roles.ToolTipText, "text"),
                      (roles.Light, "line2"), (roles.Midlight, "line"), (roles.Dark, "line2"), (roles.Shadow, "bg"),
                      (roles.Link, "accent")):
        pal.setColor(role, QColor(t[key]))
    pal.setColor(roles.HighlightedText, QColor("#ffffff"))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["dim"]))
    return pal


def _icons(name):
    """Kleine SVG-Grafiken für Haken, Schalter und Pfeile (Stylesheets können keine Daten-URLs)."""
    global _icons_dir
    if _icons_dir is None:
        _icons_dir = Path(tempfile.mkdtemp(prefix="bmc-steward-theme-"))
    t = TOKENS[name]
    files = {
        "check": f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path d="M3.5 8.5l3 3 6-7" '
                 f'fill="none" stroke="{t["on_accent"]}" stroke-width="2.2" stroke-linecap="round" '
                 f'stroke-linejoin="round"/></svg>',
        "toggle_on": f'<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20"><rect x="0.5" y="0.5" '
                     f'width="35" height="19" rx="9.5" fill="{t["accent"]}"/><circle cx="26" cy="10" r="7" '
                     f'fill="{t["on_accent"]}"/></svg>',
        "toggle_off": f'<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20"><rect x="0.5" y="0.5" '
                      f'width="35" height="19" rx="9.5" fill="{t["line2"]}" stroke="{t["dim"]}"/>'
                      f'<circle cx="10" cy="10" r="7" fill="{t["muted"]}"/></svg>',
        "down": f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6"><path d="M1 1l4 4 4-4" '
                f'fill="none" stroke="{t["muted"]}" stroke-width="1.6" stroke-linecap="round"/></svg>',
        "up": f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6"><path d="M1 5l4-4 4 4" '
              f'fill="none" stroke="{t["muted"]}" stroke-width="1.6" stroke-linecap="round"/></svg>',
    }
    out = {}
    for key, svg in files.items():
        p = _icons_dir / f"{name}-{key}.svg"
        if not p.exists():
            p.write_text(svg, encoding="utf-8")
        out[key] = p.as_posix()
    return out


def stylesheet(name):
    t = dict(TOKENS[name])
    t.update({f"i_{k}": v for k, v in _icons(name).items()})
    return """
QToolTip {{ background: {card}; color: {text}; border: 1px solid {line2}; padding: 8px 10px; }}
QWidget#sidebar {{ background: {panel}; border-right: 1px solid {line}; }}
QLabel#brandName {{ font-size: 15px; font-weight: 600; }}
QLabel#brandVersion, QLabel#muted {{ color: {dim}; font-size: 11px; }}
QLabel#pageCrumb {{ color: {dim}; font-size: 11px; }}
QLabel#pageTitle {{ font-size: 19px; font-weight: 600; }}
QLabel#navSection {{ color: {dim}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QGroupBox {{ background: {card}; border: 1px solid {line}; border-radius: 10px; margin-top: 4px;
            padding: 26px 8px 6px 8px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: padding; subcontrol-position: top left; left: 12px; top: 7px;
                   padding: 0; color: {muted}; }}
QGroupBox QWidget {{ font-weight: normal; }}
QPushButton, QToolButton {{ background: {btn}; color: {text}; border: 1px solid {line2}; border-radius: 7px;
                           padding: 4px 10px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {accent}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {accent_soft}; }}
QPushButton:checked, QToolButton:checked {{ background: {accent_soft}; border-color: {accent}; color: {accent2}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {dim}; border-color: {line}; }}
QPushButton:default {{ border-color: {accent}; }}
QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {acc_top}, stop:1 {acc_bot});
                      color: {on_accent}; border: none; font-weight: bold; padding: 8px 18px; }}
QPushButton#primary:disabled {{ background: {line2}; color: {dim}; }}
QPushButton#danger {{ background: {err_soft}; color: {err}; border: 1px solid {err}; font-weight: bold; }}
QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {input}; color: {text}; border: 1px solid {line2}; border-radius: 6px; padding: 4px 7px;
    selection-background-color: {accent_strong}; selection-color: #ffffff; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ color: {dim}; background: {bg}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({i_down}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {card}; border: 1px solid {line2}; selection-background-color: {accent_soft};
                              selection-color: {text}; outline: 0; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; width: 16px; background: transparent; }}
QAbstractSpinBox::up-arrow {{ image: url({i_up}); width: 10px; height: 6px; }}
QAbstractSpinBox::down-arrow {{ image: url({i_down}); width: 10px; height: 6px; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {line2}; border-radius: 4px; background: {input}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url({i_check}); }}
QCheckBox::indicator:disabled {{ background: {bg}; border-color: {line}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {line2}; border-radius: 9px; background: {input}; }}
QRadioButton::indicator:checked {{ border: 5px solid {accent}; width: 8px; height: 8px; background: {on_accent}; }}
QRadioButton::indicator:disabled {{ background: {bg}; border-color: {line}; }}
QCheckBox#toggle::indicator {{ width: 36px; height: 20px; border: none; background: transparent;
                              image: url({i_toggle_off}); }}
QCheckBox#toggle::indicator:checked {{ image: url({i_toggle_on}); }}
QTableView, QTableWidget, QListView, QListWidget, QTreeView, QTreeWidget {{
    background: {input}; alternate-background-color: {card}; color: {text}; border: 1px solid {line};
    border-radius: 8px; gridline-color: {line}; selection-background-color: {accent_strong};
    selection-color: #ffffff; outline: 0; }}
QHeaderView::section {{ background: {panel}; color: {muted}; border: none; border-bottom: 1px solid {line};
                       border-right: 1px solid {line}; padding: 5px 6px; }}
QTableCornerButton::section {{ background: {panel}; border: none; }}
QTabWidget::pane {{ border: 1px solid {line}; border-radius: 8px; top: -1px; background: {card}; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 6px 12px; border: none;
               border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}
QMenu {{ background: {card}; color: {text}; border: 1px solid {line2}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {accent_soft}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 4px 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {line2}; border-radius: 4px; min-height: 28px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {line2}; border-radius: 4px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {line}; }}
QProgressBar {{ background: {input}; border: 1px solid {line2}; border-radius: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
QLabel#pill {{ border-radius: 9px; padding: 2px 9px; background: {accent_soft}; color: {accent2}; }}
QLabel#todoBox {{ background: {warn_soft}; color: {warn}; border: 1px solid {warn}; border-radius: 8px;
                 padding: 6px 10px; }}
QLabel#todoBox[ok="true"] {{ background: {accent_soft}; color: {accent2}; border-color: {accent}; }}
QLabel#pageIntro {{ background: {card}; border: 1px solid {line}; border-radius: 8px; padding: 7px 10px;
                   color: {muted}; }}
""".format(**t)


def apply(app, choice=None, sheet=True):
    """Design setzen ("auto", "light", "dark"; ohne Angabe die gemerkte Wahl). Gibt das aktive Design zurück.
    sheet=False lässt das Stylesheet weg (beim Start: erst nach dem Fensteraufbau setzen, sonst gestaltet Qt
    jede Seite beim Einhängen erneut)."""
    global _current
    choice = choice or load_choice()
    name = resolve(choice)
    if app is not None:
        if app.property("bmcFusion") is None:      # einmal: mit Stylesheet meldet style() keinen Namen mehr
            app.setStyle("Fusion")
            app.setProperty("bmcFusion", True)
        app.setPalette(palette(name))
        if sheet:
            app.setStyleSheet(stylesheet(name))
        from . import tooltips
        tooltips.install(app)                      # Tooltips als schmale, lesbare Kästchen
    changed = name != _current
    _current = name
    if changed:
        for ref in list(_listeners):
            cb = ref()
            if cb is None:
                _listeners.remove(ref)
                continue
            try:
                cb(name)
            except RuntimeError:                    # Fenster schon zerstört
                _listeners.remove(ref)
    return name


def follow_system(app):
    """Bei „Automatisch“ mitwechseln, wenn das System zwischen hell und dunkel umschaltet."""
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: load_choice() == "auto" and apply(app, "auto"))
    except AttributeError:
        pass
