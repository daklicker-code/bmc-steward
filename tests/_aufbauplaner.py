"""Hilfen für die Tests des mitgelieferten Aufbauplaner-Plugins (bmc_steward/plugins_mitgeliefert/aufbauplaner).

Das Plugin lädt wie beim Anwender von selbst über das Pluginsystem. package() liefert seine Module, window()
ein Hauptfenster mit der Plugin-Seite.
"""
import importlib

from tests._support import REPO, qapp

SRC = REPO / "bmc_steward" / "plugins_mitgeliefert" / "aufbauplaner"

qapp()


def package():
    """(plugin, planner, page, texts): die Module des Plugins, so wie BMC-Steward sie lädt."""
    from bmc_steward import plugins
    mod = plugins._import(plugins.Plugin(SRC, builtin=True))
    sub = [importlib.import_module(f"{mod.__name__}.{n}") for n in ("planner", "page", "texts")]
    return (mod, *sub)


def window():
    """Neues Hauptfenster mit frischer Aufbauplaner-Seite: (Fenster, Seite)."""
    from bmc_steward.main import Window
    _, _, page, _ = package()
    page.STATE.clear()
    w = Window()
    pg = next(widget for plugin, widget in w.plugin_host.page_widgets if plugin.id == "aufbauplaner")
    return w, pg


def close(w, pg):
    pg.plan_dirty = False
    w.dirty = False
    w.close()
