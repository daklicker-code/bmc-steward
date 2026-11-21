"""BMC-Steward: Pluginsystem – eigene Erweiterungen, ohne das Programm selbst zu ändern.

Ein Plugin ist ein Ordner unter <Datenordner>/plugins/<kennung>/ mit zwei Dateien:

    plugin.json   {"name": "…", "version": "1.0", "api": 1, "description": "…", "author": "…"}
    plugin.py     def register(api): …

`register(api)` bekommt ein PluginAPI-Objekt: eigene Seiten im Menü (Bereich „Plugins“), Ereignisse
(Fenster fertig, Server fertig umgestellt, Lauf beendet, Rückfrage vor dem Beenden, Programmende), das
gemeinsame Protokoll, die Serverliste ohne Passwörter (lesen und Hostname/Adressen eintragen), einen eigenen,
nur für den Benutzer lesbaren Datenordner, Konsolen-Sitzungen, die als Reiter im Monitor links oben erscheinen
(api.add_console, Terminal-Widget über api.terminal), die Leuchte am eigenen Menüpunkt und Server fürs
Testlabor (ab 3.14.0: put_servers, set_lamp, testlab_state, testlab_load, Ereignis close_request).

Mitgelieferte Plugins (bmc_steward/plugins_mitgeliefert/<kennung>/, z. B. der Aufbauplaner) gehören zum
Programm: Sie laden ab dem ersten Start ohne Freigabe und lassen sich in den Einstellungen abschalten. Ein
eigenes Plugin mit derselben Kennung wird dann nicht geladen (es würde sonst doppelt erscheinen).

Sicherheit: Ein Plugin ist Python-Code und läuft mit allen Rechten des Programms. Deshalb lädt BMC-Steward
eigene Plugins nicht von selbst:
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
EVENTS = ("window_ready", "server_done", "run_finished", "close_request", "closing")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MAX_BYTES = 20 * 1024 * 1024        # größere Plugin-Ordner werden nicht gelesen
WINDOWS = sys.platform.startswith("win")

_MODULES = {}                        # Kennung -> (Fingerabdruck, Modul); bleibt über einen Sprachwechsel
BUILTIN_DIR = Path(__file__).resolve().parent / "plugins_mitgeliefert"


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


def load_off():
    """Kennungen der mitgelieferten Plugins, die der Benutzer abgeschaltet hat."""
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
        off = data.get("builtin_off", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        off = []
    return {k for k in off if isinstance(k, str) and ID_RE.match(k)} if isinstance(off, list) else set()


def save_state(enabled, off=None):
    """Freigaben eigener Plugins speichern; `off` (abgeschaltete mitgelieferte) bleibt ohne Angabe erhalten."""
    off = load_off() if off is None else set(off)
    i18n.write_private(state_file(), json.dumps({"enabled": dict(sorted(enabled.items())),
                                                 "builtin_off": sorted(off)}, indent=1))


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

    def __init__(self, path, builtin=False):
        self.path = Path(path)
        self.id = self.path.name
        self.builtin = bool(builtin)     # mitgeliefert: gehört zum Programm, keine Freigabe nötig
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
            if not self.builtin:             # mitgelieferte prüft die Signatur des Programms, nicht die Freigabe
                self.digest = digest(self.path)
        except (OSError, ValueError) as e:
            self.problem = str(e) if not isinstance(e, json.JSONDecodeError) else T("plugin.json ist kein gültiges JSON")
            if isinstance(e, FileNotFoundError):
                self.problem = T("{0} fehlt").format(Path(e.filename or "plugin.json").name)

    def status(self, enabled, off=None):
        """(Kurztext, ladbar) für die Einstellungen und den Start. `off`: abgeschaltete mitgelieferte Plugins."""
        if self.problem:
            return T("fehlerhaft: {0}").format(self.problem), False
        if self.builtin:
            if self.id in (load_off() if off is None else off):
                return T("mitgeliefert, abgeschaltet"), False
            if self.error:
                return T("Fehler: {0}").format(self.error), False
            return (T("aktiv") if self.loaded else T("mitgeliefert")), True
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


def _folders(base):
    try:
        return sorted(p for p in Path(base).iterdir()
                      if p.is_dir() and not p.is_symlink() and not p.name.startswith((".", "_")))
    except OSError:
        return []


def discover():
    """Alle Plugins: zuerst die mitgelieferten, dann die eigenen, jeweils nach Kennung sortiert. Ein eigenes
    Plugin mit der Kennung eines mitgelieferten wird nicht geladen."""
    builtin = [Plugin(p, builtin=True) for p in _folders(BUILTIN_DIR)]
    ids = {p.id for p in builtin}
    own = [Plugin(p) for p in _folders(plugin_dir())]
    for p in own:
        if p.id in ids and not p.problem:
            p.problem = T("ersetzt durch das mitgelieferte Plugin gleichen Namens")
    return builtin + own


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


class ConsoleHandle:
    """Eine Konsolen-Sitzung eines Plugins im Monitor. remove() nimmt sie heraus, set_title() benennt sie um."""

    def __init__(self, host, plugin, title, widget, activate, hidden_ok):
        self._host, self._plugin = host, plugin
        self.title, self.widget, self._activate, self.hidden_ok = str(title)[:60], widget, activate, hidden_ok
        self.alive = True

    def set_title(self, title):
        self.title = str(title)[:60]

    def remove(self):
        self.alive = False
        if self in self._host.consoles:
            self._host.consoles.remove(self)


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
        """Ruft fn bei einem Ereignis auf: window_ready(), server_done(ip), run_finished(code), closing().
        close_request() kommt, bevor das Fenster geschlossen wird; gibt fn False zurück, bleibt es offen
        (z. B. nach „Abbrechen“ in einer eigenen Rückfrage zu ungespeicherten Daten)."""
        if event not in EVENTS:
            raise ValueError(T("unbekanntes Ereignis {0}, möglich: {1}").format(event, ", ".join(EVENTS)))
        if not callable(fn):
            raise TypeError("fn")
        self._host.hooks[event].append((self._plugin, fn))

    def add_page(self, title, page, mark="◆"):
        """Eigene Seite im Menü (Bereich „Plugins“). `page` ist ein QWidget oder eine Funktion, die eins
        liefert (wird beim Fensteraufbau aufgerufen)."""
        self._host.pages.append((self._plugin, str(title), page, str(mark or "◆")[:2]))

    def add_console(self, title, widget, activate=None, hidden_ok=True):
        """Konsolen-Sitzung als Reiter im Monitor (links oben) anzeigen. `widget` wird nur abfotografiert (grab),
        nie verändert. Klick auf das Monitorbild: das Programm wechselt zur Plugin-Seite, die das Widget enthält,
        und ruft danach `activate()` (z. B. um den richtigen Reiter des Plugins zu zeigen). Gibt ein ConsoleHandle
        zurück (remove(), set_title())."""
        from PyQt6.QtWidgets import QWidget
        if not isinstance(widget, QWidget):
            raise TypeError("widget")
        if activate is not None and not callable(activate):
            raise TypeError("activate")
        handle = ConsoleHandle(self._host, self._plugin, title, widget, activate, bool(hidden_ok))
        self._host.consoles.append(handle)
        return handle

    def terminal(self):
        """Terminal-Widget des Programms (VT100/ANSI, wie die Textkonsole) oder None, wenn das Paket pyte fehlt.
        Daten vom Gerät mit .feed(bytes) anzeigen; Tastendrücke kommen als Signal .send(bytes)."""
        from .console import TermView, pyte
        return TermView() if pyte is not None else None

    def servers(self):
        """Serverliste von „Neuware“ ohne Passwörter: Liste von dicts mit ip, new_ip, mask, gateway, hostname,
        serial, done, vendor (erkannter Hersteller/Modell als Text, sonst "") und row (Zeile, für put_servers)."""
        w = self._host.window
        if w is None or not hasattr(w, "table"):
            return []
        out = []
        vcol = getattr(w, "vendor_col", None)
        for r in range(w.table.rowCount()):
            vals = [w.cell(r, c) for c in range(len(i18n.COLS))]
            if vals[0] or vals[2] or vals[5] or vals[6]:
                out.append({"ip": vals[0], "new_ip": vals[2], "mask": vals[3], "gateway": vals[4],
                            "hostname": vals[5], "serial": vals[6], "done": w.is_done(r),
                            "vendor": w.cell(r, vcol) if vcol is not None else "", "row": r})
        return out

    def put_servers(self, entries, show=True):
        """Trägt Hostname und Adressen in die Serverliste von „Neuware“ ein. `entries`: dicts mit row (Zeile aus
        servers(), None = neue Zeile), new_ip, mask, gateway, hostname, serial (nur für neue Zeilen). Fehlende
        Schlüssel lassen die Spalte unverändert. Geändert wird am Server erst beim Lauf in „Neuware“.
        Wechselt danach zu „Neuware“ (show=False: nicht). Gibt (aktualisiert, neu) zurück."""
        from PyQt6.QtWidgets import QTableWidgetItem
        w = self._host.window
        if w is None or not hasattr(w, "table"):
            return 0, 0
        cols = {"new_ip": 2, "mask": 3, "gateway": 4, "hostname": 5}
        updated = added = 0
        for e in entries:
            vals = {c: str(e[k]) for k, c in cols.items() if e.get(k) is not None}
            r = e.get("row")
            if not isinstance(r, int) or not 0 <= r < w.table.rowCount():
                values = [""] * len(i18n.COLS)
                values[1] = w.cur_pass.text()
                for c, val in vals.items():
                    values[c] = val
                values[6] = str(e.get("serial") or "")
                w.add_row(values)
                added += 1
                continue
            for c, val in vals.items():
                item = w.table.item(r, c)
                if item is None:
                    w.table.setItem(r, c, QTableWidgetItem(val))
                else:
                    item.setText(val)
            updated += 1
        w.update_count()
        w.touch()
        if show and hasattr(w, "new_page"):
            w.tabs.setCurrentIndex(w.tabs.indexOf(w.new_page))
        return updated, added

    def testlab_state(self):
        """{"running": Testlabor läuft, "servers": Zahl der Serverzeilen im Testlabor}."""
        w = self._host.window
        if w is None or not hasattr(w, "vbmc_fleet"):
            return {"running": False, "servers": 0}
        return {"running": bool(w.vbmc_running()), "servers": w.vbmc_fleet.rowCount()}

    def testlab_load(self, servers, status="", show=True):
        """Ersetzt die Serverzeilen des Testlabors. `servers`: dicts mit vendor (dell, hpe, lenovo oder smc),
        optional serial und model; andere Hersteller werden übersprungen. Nicht, solange das Labor läuft
        (dann False). Wechselt danach zum Testlabor (show=False: nicht)."""
        w = self._host.window
        if w is None or not hasattr(w, "vbmc_fleet") or w.vbmc_running():
            return False
        w.vbmc_fleet.setRowCount(0)
        for s in servers:
            if s.get("vendor") in ("dell", "hpe", "lenovo", "smc"):
                w.vbmc_add_server(s["vendor"], {"serial": s.get("serial") or None, "model": s.get("model") or None})
        w.vbmc_mode_changed()
        if status:
            w.vbmc_set_status(str(status))
        if show:
            w.tabs.setCurrentIndex(w.tabs.indexOf(w.vbmc_page))
        return True

    def set_lamp(self, color=None):
        """Leuchte am Menüpunkt der eigenen Seite(n) ein- (Farbe, z. B. "#3b8beb") oder ausschalten (None)."""
        w = self._host.window
        tabs = getattr(w, "tabs", None)
        if tabs is None:
            return
        for plugin, widget in self._host.page_widgets:
            if plugin is self._plugin:
                try:
                    i = tabs.indexOf(widget)
                except RuntimeError:                # Seite schon abgebaut
                    continue
                if i >= 0:
                    tabs.setLamp(i, color)


class Host:
    """Plugins eines Hauptfensters: lädt die freigegebenen, sammelt Seiten und Ereignisse."""

    def __init__(self, window=None):
        self.window = window
        self.hooks = {e: [] for e in EVENTS}
        self.pages = []
        self.plugins = []
        self.consoles = []           # ConsoleHandle der Plugins, Reihenfolge = Reihenfolge im Monitor
        self.page_widgets = []       # (Plugin, QWidget) der gebauten Seiten, für set_lamp

    def load(self):
        """Freigegebene Plugins laden und register(api) aufrufen. Gibt die Liste aller gefundenen zurück."""
        from .applog import LOG
        if disabled():
            LOG.info(T("Plugins abgeschaltet (BMC_STEWARD_NO_PLUGINS)"))
            return []
        enabled, off = load_state(), load_off()
        self.plugins = discover()
        for p in self.plugins:
            text, ok = p.status(enabled, off)
            if not ok:
                if p.id in enabled or (p.builtin and p.id not in off):
                    LOG.info(T("Plugin {0} nicht geladen: {1}").format(p.name, text))
                continue
            pages, hooks, cons = len(self.pages), {e: len(v) for e, v in self.hooks.items()}, len(self.consoles)
            try:
                _import(p).register(PluginAPI(p, self))
            except Exception as e:          # noqa: BLE001 – ein Plugin darf das Programm nicht anhalten
                del self.pages[pages:]
                del self.consoles[cons:]
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
            self.page_widgets.append((plugin, widget))
        return out

    def console_sessions(self):
        """Offene Konsolen der Plugins für den Monitor: [(Schlüssel, Widget, verdeckt_ok, Beschriftung)]."""
        out = []
        for h in list(self.consoles):
            try:
                h.widget.objectName()               # wirft, wenn das Widget schon abgebaut ist
            except RuntimeError:
                h.remove()
                continue
            out.append((f"plugin:{id(h)}", h.widget, h.hidden_ok, h.title or h._plugin.name))
        return out

    def console_activate(self, key, window=None):
        """Klick auf die Monitor-Vorschau einer Plugin-Konsole. True, wenn etwas geöffnet wurde."""
        from .applog import LOG
        h = next((h for h in self.consoles if f"plugin:{id(h)}" == key), None)
        if h is None:
            return False
        done = False
        tabs = getattr(window, "tabs", None)
        if tabs is not None:                    # zuerst zur Plugin-Seite, die das Widget enthält …
            for i in range(tabs.count()):
                page = tabs.widget(i)
                if page is not None and (page is h.widget or page.isAncestorOf(h.widget)):
                    tabs.setCurrentIndex(i)
                    done = True
                    break
        if h._activate is not None:             # … dann darf das Plugin die Sitzung selbst nach vorn holen
            try:
                h._activate()
                done = True
            except Exception as e:          # noqa: BLE001
                LOG.info(T("Plugin {0}: Fehler bei {1}: {2}").format(h._plugin.name, "activate", _short(e)))
        if done:
            try:
                h.widget.setFocus()
            except RuntimeError:
                pass
        return done

    def allow_close(self):
        """Fragt alle Plugins (Ereignis close_request), ob das Fenster schließen darf. Ein Fehler zählt als Ja,
        damit ein kaputtes Plugin das Beenden nie verhindert."""
        from .applog import LOG
        for plugin, fn in list(self.hooks.get("close_request", ())):
            try:
                if fn() is False:
                    return False
            except Exception as e:          # noqa: BLE001
                LOG.info(T("Plugin {0}: Fehler bei {1}: {2}").format(plugin.name, "close_request", _short(e)))
                LOG.debug(traceback.format_exc())
        return True

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
            builtin = QLabel(T("Mitgelieferte Plugins (z. B. der Aufbauplaner) gehören zum Programm: Sie sind von "
                               "Anfang an aktiv und lassen sich hier abschalten."))
            builtin.setWordWrap(True)
            builtin.setObjectName("muted")
            lay.addWidget(builtin)
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
            self.enabled, self.off = load_state(), load_off()
            self.found = discover()
            loaded = {pid for pid, (dg, _) in _MODULES.items()}
            self.table.setRowCount(len(self.found))
            for r, p in enumerate(self.found):
                p.loaded = p.id in loaded and _MODULES[p.id][0] == p.digest
                box = QTableWidgetItem()
                box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
                             | (Qt.ItemFlag.ItemIsEnabled if not p.problem else Qt.ItemFlag.NoItemFlags))
                ok = (p.id not in self.off) if p.builtin else (p.id in self.enabled and self.enabled[p.id] == p.digest)
                box.setCheckState(Qt.CheckState.Checked if ok else Qt.CheckState.Unchecked)
                self.table.setItem(r, 0, box)
                name = QTableWidgetItem(p.name)
                name.setToolTip("\n".join(x for x in (p.description, p.author and T("von {0}").format(p.author),
                                                      T("mitgeliefert mit BMC-Steward") if p.builtin else "",
                                                      str(p.path), "" if p.builtin else "SHA-256 " + (p.digest or "–"))
                                           if x))
                self.table.setItem(r, 1, name)
                self.table.setItem(r, 2, QTableWidgetItem(p.version))
                self.table.setItem(r, 3, QTableWidgetItem(p.status(self.enabled, self.off)[0]))

        def wanted(self):
            return {p.id: p for r, p in enumerate(self.found)
                    if self.table.item(r, 0).checkState() == Qt.CheckState.Checked and not p.problem
                    and not p.builtin}

        def switched_off(self):
            return {p.id for r, p in enumerate(self.found)
                    if p.builtin and self.table.item(r, 0).checkState() != Qt.CheckState.Checked}

        def apply(self, parent=None):
            """Freigaben speichern. Neu freigegebene erst nach Rückfrage. True, wenn sich etwas geändert hat."""
            want, off = self.wanted(), self.switched_off()
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
            changed = result != self.enabled or off != self.off
            if changed:
                save_state(result, off)
            self.fill()
            return changed

    return Page()
