"""BMC-Steward: Pluginsystem – eigene Erweiterungen, ohne das Programm selbst zu ändern.

Ein Plugin ist ein Ordner unter <Datenordner>/plugins/<kennung>/ mit zwei Dateien:

    plugin.json   {"name": "…", "version": "1.0", "api": 1, "description": "…", "author": "…"}
    plugin.py     def register(api): …

`register(api)` bekommt ein PluginAPI-Objekt: eigene Seiten im Menü (Bereich „Plugins“), Ereignisse
(Fenster fertig, Server fertig umgestellt, Lauf beendet, Programmende), das gemeinsame Protokoll, die
Serverliste ohne Passwörter und einen eigenen, nur für den Benutzer lesbaren Datenordner.

Sicherheit: Ein Plugin ist Python-Code und läuft mit allen Rechten des Programms. Deshalb lädt BMC-Steward
nichts von selbst:
  - Jedes Plugin muss in den Einstellungen (Reiter „Plugins“) einzeln freigegeben werden.
  - Bei der Freigabe merkt sich das Tool einen SHA-256-Fingerabdruck aller Dateien des Ordners. Ändert sich
    danach auch nur ein Byte, wird das Plugin nicht mehr geladen, bis es erneut freigegeben wird.
  - Unter Linux/macOS wird ein Ordner oder eine Datei, die andere Benutzer beschreiben dürfen oder die einem
    fremden Benutzer gehört, nicht geladen.
  - BMC_STEWARD_NO_PLUGINS=1 startet ohne jedes Plugin (abgesicherter Modus).
Fehler in einem Plugin landen im Protokoll und halten das Programm nicht an.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import traceback
from pathlib import Path

from . import i18n
from .i18n import T

API_VERSION = 1
EVENTS = ("window_ready", "server_done", "run_finished", "closing")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MAX_BYTES = 20 * 1024 * 1024        # größere Plugin-Ordner werden nicht gelesen
WINDOWS = sys.platform.startswith("win")

_MODULES = {}                        # Kennung -> (Fingerabdruck, Modul); bleibt über einen Sprachwechsel


def plugin_dir():
    return i18n.DATA / "plugins"


def state_file():
    return i18n.DATA / "bmc-plugins.json"


def disabled():
    """Abgesicherter Modus: BMC_STEWARD_NO_PLUGINS gesetzt (nicht leer, nicht 0)."""
    return os.environ.get("BMC_STEWARD_NO_PLUGINS", "").strip() not in ("", "0")


def load_state():
    """{Kennung: Fingerabdruck} der freigegebenen Plugins."""
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
        enabled = data.get("enabled", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        enabled = {}
    return {k: v for k, v in enabled.items()
            if isinstance(k, str) and ID_RE.match(k) and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)}


def save_state(enabled):
    i18n.write_private(state_file(), json.dumps({"enabled": dict(sorted(enabled.items()))}, indent=1))


def _files(path):
    """Alle Dateien des Plugin-Ordners, sortiert; symbolische Links zählen als Fehler."""
    out = []
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs.sort()
        for name in dirs:
            if os.path.islink(os.path.join(root, name)):
                raise ValueError(T("enthält einen symbolischen Link: {0}").format(name))
        for name in sorted(files):
            p = Path(root) / name
            if p.is_symlink():
                raise ValueError(T("enthält einen symbolischen Link: {0}").format(name))
            out.append(p)
    return sorted(out, key=lambda p: p.relative_to(path).as_posix())


def digest(path):
    """SHA-256 über Namen und Inhalt aller Dateien des Ordners."""
    path = Path(path)
    h, total = hashlib.sha256(), 0
    for p in _files(path):
        data = p.read_bytes()
        total += len(data)
        if total > MAX_BYTES:
            raise ValueError(T("Ordner ist größer als {0} MB").format(MAX_BYTES // (1024 * 1024)))
        rel = p.relative_to(path).as_posix().encode("utf-8")
        h.update(len(rel).to_bytes(4, "big") + rel + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def unsafe(path):
    """Grund, warum der Ordner nicht geladen werden darf (fremd beschreibbar), sonst ""."""
    if WINDOWS:
        return ""
    me = os.getuid()
    path = Path(path)
    for p in [path.parent, path] + [Path(r) / n for r, ds, fs in os.walk(path) for n in ds + fs]:
        try:
            st = p.lstat()
        except OSError:
            continue
        if st.st_mode & 0o022:
            return T("{0} ist für andere Benutzer beschreibbar").format(p)
        if st.st_uid not in (me, 0):
            return T("{0} gehört einem anderen Benutzer").format(p)
    return ""


class Plugin:
    """Ein gefundener Plugin-Ordner mit Angaben aus plugin.json und Ladezustand."""

    def __init__(self, path):
        self.path = Path(path)
        self.id = self.path.name
        self.name, self.version, self.description, self.author = self.id, "", "", ""
        self.digest, self.problem, self.error = "", "", ""
        self.loaded = False
        try:
            if not ID_RE.match(self.id):
                raise ValueError(T("Ordnername nur aus a–z, 0–9, - und _"))
            meta = json.loads((self.path / "plugin.json").read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError(T("plugin.json ist kein Objekt"))
            self.name = str(meta.get("name") or self.id)[:80]
            self.version = str(meta.get("version") or "")[:40]
            self.description = str(meta.get("description") or "")[:500]
            self.author = str(meta.get("author") or "")[:80]
            if meta.get("api") != API_VERSION:
                raise ValueError(T("braucht Plugin-API {0}, dieses Programm bietet {1}").format(
                    meta.get("api"), API_VERSION))
            if not (self.path / "plugin.py").is_file():
                raise ValueError(T("plugin.py fehlt"))
            self.digest = digest(self.path)
        except (OSError, ValueError) as e:
            self.problem = str(e) if not isinstance(e, json.JSONDecodeError) else T("plugin.json ist kein gültiges JSON")
            if isinstance(e, FileNotFoundError):
                self.problem = T("{0} fehlt").format(Path(e.filename or "plugin.json").name)

    def status(self, enabled):
        """(Kurztext, ladbar) für die Einstellungen und den Start."""
        if self.problem:
            return T("fehlerhaft: {0}").format(self.problem), False
        if self.id not in enabled:
            return T("nicht freigegeben"), False
        if enabled[self.id] != self.digest:
            return T("geändert seit der Freigabe – erneut freigeben"), False
        why = unsafe(self.path)
        if why:
            return T("gesperrt: {0}").format(why), False
        if self.error:
            return T("Fehler: {0}").format(self.error), False
        return (T("aktiv") if self.loaded else T("freigegeben")), True


def discover():
    """Alle Plugin-Ordner, nach Kennung sortiert."""
    base = plugin_dir()
    try:
        entries = sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))
    except OSError:
        return []
    return [Plugin(p) for p in entries]


def _import(plugin):
    """Lädt plugin.py als eigenes Modul (Unterdateien per relativem Import). Ohne .pyc im Plugin-Ordner,
    sonst passte der Fingerabdruck beim nächsten Start nicht mehr."""
    cached = _MODULES.get(plugin.id)
    if cached and cached[0] == plugin.digest:
        return cached[1]
    name = "bmc_steward_plugin_" + plugin.id.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, plugin.path / "plugin.py",
                                                  submodule_search_locations=[str(plugin.path)])
    module = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        sys.dont_write_bytecode = old
    if not callable(getattr(module, "register", None)):
        sys.modules.pop(name, None)
        raise ValueError(T("plugin.py hat keine Funktion register(api)"))
    _MODULES[plugin.id] = (plugin.digest, module)
    return module


def _short(err):
    return f"{type(err).__name__}: {err}"[:300]


class PluginAPI:
    """Was ein Plugin in register(api) bekommt. Nur diese Schnittstelle ist zugesichert (API_VERSION)."""

    def __init__(self, plugin, host):
        self._plugin, self._host = plugin, host
        self.id, self.name, self.version = plugin.id, plugin.name, plugin.version
        self.api_version = API_VERSION
        from .core import __version__
        self.app_version = __version__

    @property
    def lang(self):
        """Eingestellte Sprache: de, en, fr oder gsw."""
        return i18n.LANG

    @property
    def data_dir(self):
        """Eigener Ordner des Plugins (nur für den Benutzer lesbar), wird bei Bedarf angelegt."""
        return i18n.private_dir(i18n.DATA / "plugin-daten" / self.id)

    def log(self, text):
        from .applog import LOG
        LOG.info(f"[{self.name}] {text}")

    def debug(self, text):
        from .applog import LOG
        LOG.debug(f"[{self.name}] {text}")

    def on(self, event, fn):
        """Ruft fn bei einem Ereignis auf: window_ready(), server_done(ip), run_finished(code), closing()."""
        if event not in EVENTS:
            raise ValueError(T("unbekanntes Ereignis {0}, möglich: {1}").format(event, ", ".join(EVENTS)))
        if not callable(fn):
            raise TypeError("fn")
        self._host.hooks[event].append((self._plugin, fn))

    def add_page(self, title, page, mark="◆"):
        """Eigene Seite im Menü (Bereich „Plugins“). `page` ist ein QWidget oder eine Funktion, die eins
        liefert (wird beim Fensteraufbau aufgerufen)."""
        self._host.pages.append((self._plugin, str(title), page, str(mark or "◆")[:2]))

    def servers(self):
        """Serverliste von „Neuware“ ohne Passwörter: Liste von dicts mit ip, new_ip, mask, gateway, hostname,
        serial und done."""
        w = self._host.window
        if w is None or not hasattr(w, "table"):
            return []
        out = []
        for r in range(w.table.rowCount()):
            vals = [w.cell(r, c) for c in range(len(i18n.COLS))]
            if vals[0] or vals[2] or vals[5]:
                out.append({"ip": vals[0], "new_ip": vals[2], "mask": vals[3], "gateway": vals[4],
                            "hostname": vals[5], "serial": vals[6], "done": w.is_done(r)})
        return out


class Host:
    """Plugins eines Hauptfensters: lädt die freigegebenen, sammelt Seiten und Ereignisse."""

    def __init__(self, window=None):
        self.window = window
        self.hooks = {e: [] for e in EVENTS}
        self.pages = []
        self.plugins = []

    def load(self):
        """Freigegebene Plugins laden und register(api) aufrufen. Gibt die Liste aller gefundenen zurück."""
        from .applog import LOG
        if disabled():
            LOG.info(T("Plugins abgeschaltet (BMC_STEWARD_NO_PLUGINS)"))
            return []
        enabled = load_state()
        self.plugins = discover()
        for p in self.plugins:
            _, ok = p.status(enabled)
            if not ok:
                if p.id in enabled:
                    LOG.info(T("Plugin {0} nicht geladen: {1}").format(p.name, p.status(enabled)[0]))
                continue
            pages, hooks = len(self.pages), {e: len(v) for e, v in self.hooks.items()}
            try:
                _import(p).register(PluginAPI(p, self))
            except Exception as e:          # noqa: BLE001 – ein Plugin darf das Programm nicht anhalten
                del self.pages[pages:]
                for ev, n in hooks.items():
                    del self.hooks[ev][n:]
                p.error = _short(e)
                LOG.info(T("Plugin {0} konnte nicht geladen werden: {1}").format(p.name, p.error))
                LOG.debug(traceback.format_exc())
                continue
            p.loaded = True
            LOG.info(T("Plugin {0} {1} geladen").format(p.name, p.version))
        return self.plugins

    def build_pages(self):
        """[(Titel, Symbol, QWidget)] der Plugin-Seiten; fehlerhafte Seiten fallen weg."""
        from PyQt6.QtWidgets import QWidget
        from .applog import LOG
        out = []
        for plugin, title, page, mark in self.pages:
            try:
                widget = page() if callable(page) and not isinstance(page, QWidget) else page
                if not isinstance(widget, QWidget):
                    raise TypeError(T("Seite ist kein QWidget"))
            except Exception as e:          # noqa: BLE001
                plugin.error = _short(e)
                LOG.info(T("Plugin {0}: Seite „{1}“ fehlerhaft: {2}").format(plugin.name, title, plugin.error))
                LOG.debug(traceback.format_exc())
                continue
            out.append((title, mark, widget))
        return out

    def emit(self, event, *args):
        """Ereignis an alle Plugins; ein Fehler betrifft nur das eine Plugin."""
        from .applog import LOG
        for plugin, fn in list(self.hooks.get(event, ())):
            try:
                fn(*args)
            except Exception as e:          # noqa: BLE001
                LOG.info(T("Plugin {0}: Fehler bei {1}: {2}").format(plugin.name, event, _short(e)))
                LOG.debug(traceback.format_exc())


def settings_page():
    """Reiter „Plugins“ der Einstellungen: QWidget mit .apply(parent) -> bool (Freigaben gespeichert)."""
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

    class Page(QWidget):
        def __init__(self):
            super().__init__()
            lay = QVBoxLayout(self)
            intro = QLabel(T("Plugins erweitern BMC-Steward um eigene Seiten und Abläufe. Ein Plugin ist ein Ordner "
                             "mit plugin.json und plugin.py in {0}. Plugins laufen mit allen Rechten des Programms: "
                             "nur freigeben, was aus vertrauenswürdiger Quelle stammt. Ändert sich eine Datei nach "
                             "der Freigabe, wird das Plugin nicht mehr geladen.").format(plugin_dir()))
            intro.setWordWrap(True)
            lay.addWidget(intro)
            if disabled():
                off = QLabel(T("Plugins abgeschaltet (BMC_STEWARD_NO_PLUGINS)"))
                off.setObjectName("muted")
                lay.addWidget(off)
            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels([T("Freigegeben"), T("Name"), T("Version"), T("Zustand")])
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.verticalHeader().setVisible(False)
            hh = self.table.horizontalHeader()
            for c in range(3):
                hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            hh.setStretchLastSection(True)
            lay.addWidget(self.table, 1)
            btns = QHBoxLayout()
            folder = QPushButton(T("Plugin-Ordner öffnen"))
            folder.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(i18n.private_dir(plugin_dir())))))
            again = QPushButton(T("Neu einlesen"))
            again.clicked.connect(self.fill)
            btns.addWidget(folder)
            btns.addWidget(again)
            btns.addStretch(1)
            lay.addLayout(btns)
            note = QLabel(T("Änderungen an den Freigaben wirken nach einem Neustart; nach dem Speichern bietet BMC-Steward ihn an."))
            note.setObjectName("muted")
            lay.addWidget(note)
            self.fill()

        def fill(self):
            self.enabled = load_state()
            self.found = discover()
            loaded = {pid for pid, (dg, _) in _MODULES.items()}
            self.table.setRowCount(len(self.found))
            for r, p in enumerate(self.found):
                p.loaded = p.id in loaded and _MODULES[p.id][0] == p.digest
                box = QTableWidgetItem()
                box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
                             | (Qt.ItemFlag.ItemIsEnabled if not p.problem else Qt.ItemFlag.NoItemFlags))
                ok = p.id in self.enabled and self.enabled[p.id] == p.digest
                box.setCheckState(Qt.CheckState.Checked if ok else Qt.CheckState.Unchecked)
                self.table.setItem(r, 0, box)
                name = QTableWidgetItem(p.name)
                name.setToolTip("\n".join(x for x in (p.description, p.author and T("von {0}").format(p.author),
                                                      str(p.path), "SHA-256 " + (p.digest or "–")) if x))
                self.table.setItem(r, 1, name)
                self.table.setItem(r, 2, QTableWidgetItem(p.version))
                self.table.setItem(r, 3, QTableWidgetItem(p.status(self.enabled)[0]))

        def wanted(self):
            return {p.id: p for r, p in enumerate(self.found)
                    if self.table.item(r, 0).checkState() == Qt.CheckState.Checked and not p.problem}

        def apply(self, parent=None):
            """Freigaben speichern. Neu freigegebene erst nach Rückfrage. True, wenn sich etwas geändert hat."""
            want = self.wanted()
            new = [p for pid, p in want.items() if self.enabled.get(pid) != p.digest]
            if new:
                names = "\n".join(f"• {p.name} {p.version} ({p.path})" for p in new)
                ans = QMessageBox.warning(
                    parent or self, T("Plugins freigeben"),
                    T("Diese Plugins werden beim nächsten Start geladen und laufen mit allen Rechten des "
                      "Programms – sie können Zugangsdaten lesen und jeden Server ändern:\n\n{0}\n\n"
                      "Nur freigeben, wenn du den Code kennst oder der Quelle vertraust.").format(names),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if ans != QMessageBox.StandardButton.Yes:
                    for p in new:
                        want.pop(p.id)
            result = {pid: p.digest for pid, p in want.items()}
            # Freigaben für Ordner, die gerade fehlen (z. B. umbenannt), bleiben nicht stehen
            changed = result != self.enabled
            if changed:
                save_state(result)
            self.fill()
            return changed

    return Page()
