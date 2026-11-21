"""Hilfen für die Tests des Aufbauplaner-Plugins (docs/plugin-aufbauplaner).

Das Plugin wird wie beim Anwender in den Datenordner der Tests kopiert (plugins/aufbauplaner/), freigegeben und
über das Pluginsystem geladen. package() liefert seine Module, window() ein Hauptfenster mit der Plugin-Seite.
"""
import importlib
import os
import shutil
import sys

from tests._support import REPO, qapp

SRC = REPO / "docs" / "plugin-aufbauplaner"
sys.dont_write_bytecode = True       # nie .pyc in den Plugin-Ordner, sonst passt der Fingerabdruck nicht mehr

qapp()


def install():
    """Kopiert das Plugin in den Datenordner und gibt es frei. Gibt den Zielordner zurück."""
    from bmc_steward import plugins
    dst = plugins.plugin_dir() / "aufbauplaner"
    if not dst.exists():
        shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    for root, dirs, files in os.walk(dst.parent):
        os.chmod(root, 0o700)
        for name in files:
            os.chmod(os.path.join(root, name), 0o600)
    plugins.save_state({**plugins.load_state(), "aufbauplaner": plugins.digest(dst)})
    return dst


def package():
    """(plugin, planner, page, texts): die Module des Plugins, so wie BMC-Steward sie lädt."""
    from bmc_steward import plugins
    mod = plugins._import(plugins.Plugin(install()))
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
