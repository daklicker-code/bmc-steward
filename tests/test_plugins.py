"""Pluginsystem: nur freigegebene, unveränderte Plugins laden; Fehler eines Plugins halten nichts an."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import qapp

qapp()
from PyQt6.QtWidgets import QLabel, QMessageBox  # noqa: E402

from bmc_steward import i18n, plugins  # noqa: E402

i18n.set_language("de")

GOOD = '''
from PyQt6.QtWidgets import QLabel
CALLS = []

def register(api):
    CALLS.append(("register", api.id, api.api_version))
    api.on("server_done", lambda ip: CALLS.append(("done", ip)))
    api.on("window_ready", lambda: CALLS.append(("ready", len(api.servers()))))
    api.add_page("Meine Seite", lambda: QLabel("hallo"), mark="✎")
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bmc-plugins-"))
        p = patch.object(i18n, "DATA", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        plugins._MODULES.clear()
        self.addCleanup(plugins._MODULES.clear)
        os.environ.pop("BMC_STEWARD_NO_PLUGINS", None)

    def make(self, pid, code=GOOD, meta=None):
        d = i18n.private_dir(self.tmp / "plugins" / pid)
        (d / "plugin.json").write_text(json.dumps(meta or {"name": pid.title(), "version": "1.0", "api": 1}))
        (d / "plugin.py").write_text(code)
        return d

    def approve(self, *pids):
        plugins.save_state({p: plugins.digest(self.tmp / "plugins" / p) for p in pids})

    def module(self, pid):
        return plugins._MODULES[pid][1]


class TestLoading(Base):
    def test_not_loaded_without_approval(self):
        self.make("eins")
        host = plugins.Host()
        found = host.load()
        self.assertEqual([p.id for p in found], ["eins"])
        self.assertFalse(found[0].loaded)
        self.assertEqual(host.pages, [])
        self.assertNotIn("eins", plugins._MODULES)

    def test_approved_plugin_registers_page_and_hooks(self):
        self.make("eins")
        self.approve("eins")
        host = plugins.Host()
        found = host.load()
        self.assertTrue(found[0].loaded)
        mod = self.module("eins")
        self.assertEqual(mod.CALLS, [("register", "eins", 1)])
        pages = host.build_pages()
        self.assertEqual([(t, m) for t, m, _ in pages], [("Meine Seite", "✎")])
        self.assertIsInstance(pages[0][2], QLabel)
        host.emit("server_done", "10.0.0.5")
        self.assertIn(("done", "10.0.0.5"), mod.CALLS)

    def test_changed_after_approval_is_not_loaded(self):
        d = self.make("eins")
        self.approve("eins")
        (d / "plugin.py").write_text(GOOD + "\n# nachträglich geändert\n")
        found = plugins.Host().load()
        self.assertFalse(found[0].loaded)
        self.assertIn(plugins.T("geändert seit der Freigabe – erneut freigeben"),
                      found[0].status(plugins.load_state())[0])
        # auch eine neue Datei im Ordner zählt als Änderung
        (d / "plugin.py").write_text(GOOD)
        self.assertEqual(plugins.digest(d), plugins.load_state()["eins"])
        (d / "extra.py").write_text("X = 1\n")
        self.assertNotEqual(plugins.digest(d), plugins.load_state()["eins"])

    @unittest.skipIf(sys.platform.startswith("win"), "Dateirechte nur unter Linux/macOS")
    def test_world_writable_folder_blocked(self):
        d = self.make("eins")
        self.approve("eins")
        os.chmod(d, 0o777)
        found = plugins.Host().load()
        self.assertFalse(found[0].loaded)
        self.assertTrue(found[0].status(plugins.load_state())[0].startswith(plugins.T("gesperrt: {0}")[:8]))
        os.chmod(d, 0o700)
        os.chmod(d / "plugin.py", 0o666)
        self.assertFalse(plugins.Host().load()[0].loaded)

    def test_symlink_rejected(self):
        d = self.make("eins")
        (d / "link.py").symlink_to("/etc/hostname")
        p = plugins.Plugin(d)
        self.assertTrue(p.problem)
        self.assertEqual(p.digest, "")

    def test_broken_plugin_does_not_stop_others(self):
        self.make("a-kaputt", code="from PyQt6.QtWidgets import QLabel\n"
                                   "def register(api):\n    api.add_page('halb', QLabel)\n    raise RuntimeError('kaputt')\n")
        self.make("b-gut")
        self.approve("a-kaputt", "b-gut")
        host = plugins.Host()
        found = {p.id: p for p in host.load()}
        self.assertFalse(found["a-kaputt"].loaded)
        self.assertIn("kaputt", found["a-kaputt"].error)
        self.assertTrue(found["b-gut"].loaded)
        self.assertEqual([t for t, _, _ in host.build_pages()], ["Meine Seite"])    # halbe Seite fällt weg

    def test_import_error_and_missing_register(self):
        self.make("syntax", code="def register(api)\n")
        self.make("ohne", code="X = 1\n")
        self.approve("syntax", "ohne")
        found = {p.id: p for p in plugins.Host().load()}
        self.assertIn("SyntaxError", found["syntax"].error)
        self.assertIn("register", found["ohne"].error)
        self.assertNotIn("bmc_steward_plugin_ohne", sys.modules)

    def test_failing_hook_and_page_are_contained(self):
        self.make("eins", code="def register(api):\n"
                               "    api.on('closing', lambda: 1 / 0)\n"
                               "    api.add_page('kaputt', lambda: 'kein widget')\n")
        self.approve("eins")
        host = plugins.Host()
        host.load()
        host.emit("closing")                          # darf nicht werfen
        self.assertEqual(host.build_pages(), [])

    def test_unknown_event_rejected(self):
        self.make("eins", code="def register(api):\n    api.on('gibtsnicht', print)\n")
        self.approve("eins")
        p = plugins.Host().load()[0]
        self.assertFalse(p.loaded)
        self.assertIn("gibtsnicht", p.error)

    def test_bad_manifests(self):
        self.make("alt", meta={"name": "Alt", "api": 99})
        d = self.make("kaputt")
        (d / "plugin.json").write_text("{nicht json")
        self.make("Gross-Buchstaben")
        d2 = i18n.private_dir(self.tmp / "plugins" / "leer")
        (d2 / "plugin.json").write_text(json.dumps({"name": "x", "api": 1}))
        found = {p.id: p for p in plugins.discover()}
        self.assertIn("99", found["alt"].problem)
        self.assertEqual(found["kaputt"].problem, plugins.T("plugin.json ist kein gültiges JSON"))
        self.assertTrue(found["Gross-Buchstaben"].problem)
        self.assertEqual(found["leer"].problem, plugins.T("plugin.py fehlt"))
        self.assertTrue(all(not p.status({k: "0" * 64 for k in found})[1] for p in found.values()))

    def test_safe_mode(self):
        self.make("eins")
        self.approve("eins")
        with patch.dict(os.environ, {"BMC_STEWARD_NO_PLUGINS": "1"}):
            self.assertEqual(plugins.Host().load(), [])
        self.assertNotIn("eins", plugins._MODULES)

    def test_no_bytecode_in_plugin_folder(self):
        d = self.make("eins")
        (d / "helfer.py").write_text("WERT = 42\n")
        (d / "plugin.py").write_text("from .helfer import WERT\ndef register(api):\n    api.log(WERT)\n")
        self.approve("eins")
        self.assertTrue(plugins.Host().load()[0].loaded)
        self.assertFalse((d / "__pycache__").exists())
        self.assertTrue(plugins.Host().load()[0].loaded)         # Fingerabdruck passt auch beim zweiten Mal

    def test_state_file_ignores_garbage(self):
        plugins.state_file().write_text(json.dumps({"enabled": {"ok": "a" * 64, "../x": "a" * 64, "kurz": "ab"}}))
        self.assertEqual(plugins.load_state(), {"ok": "a" * 64})
        plugins.state_file().write_text("[]")
        self.assertEqual(plugins.load_state(), {})

    def test_data_dir_private(self):
        self.make("eins", code="def register(api):\n    (api.data_dir / 'x.txt').write_text('1')\n")
        self.approve("eins")
        self.assertTrue(plugins.Host().load()[0].loaded)
        d = self.tmp / "plugin-daten" / "eins"
        self.assertTrue((d / "x.txt").is_file())
        if not sys.platform.startswith("win"):
            self.assertEqual(d.stat().st_mode & 0o077, 0)


class TestSettingsPage(Base):
    def test_approve_after_confirmation(self):
        self.make("eins")
        page = plugins.settings_page()
        self.assertEqual(page.table.rowCount(), 1)
        page.table.item(0, 0).setCheckState(plugins_checked())
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.No) as ask:
            self.assertFalse(page.apply())
        ask.assert_called_once()
        self.assertEqual(plugins.load_state(), {})
        page.table.item(0, 0).setCheckState(plugins_checked())
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(page.apply())
        self.assertEqual(plugins.load_state(), {"eins": plugins.digest(self.tmp / "plugins" / "eins")})
        # wieder abwählen: ohne Rückfrage entfernt
        page.table.item(0, 0).setCheckState(plugins_unchecked())
        with patch.object(QMessageBox, "warning") as ask:
            self.assertTrue(page.apply())
        ask.assert_not_called()
        self.assertEqual(plugins.load_state(), {})

    def test_settings_dialog_has_plugin_tab(self):
        from bmc_steward.settings import SettingsDialog
        dlg = SettingsDialog()
        tabs = dlg.findChild(__import__("PyQt6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget)
        self.assertIn(plugins.T("Plugins"), [tabs.tabText(i) for i in range(tabs.count())])
        dlg.save()                                    # ohne Plugins: keine Rückfrage, kein Fehler
        dlg.deleteLater()


def plugins_checked():
    from PyQt6.QtCore import Qt
    return Qt.CheckState.Checked


def plugins_unchecked():
    from PyQt6.QtCore import Qt
    return Qt.CheckState.Unchecked


class TestWindow(Base):
    def test_page_in_menu_and_events(self):
        self.make("eins")
        self.approve("eins")
        from bmc_steward.main import Window
        w = Window()
        self.addCleanup(lambda: (setattr(w, "dirty", False), w.close()))
        texts = [w.tabs.menu.item(i).text() for i in range(w.tabs.count())]
        self.assertIn("Meine Seite", texts)
        self.assertLess(texts.index("Meine Seite"), texts.index(plugins.T("Hilfe")))
        mod = self.module("eins")
        self.assertIn(("ready", 0), mod.CALLS)
        w.table.item(0, 0).setText("10.0.0.7")
        w.mark_done("10.0.0.7")
        self.assertIn(("done", "10.0.0.7"), mod.CALLS)
        servers = plugins.PluginAPI(w.plugin_host.plugins[0], w.plugin_host).servers()
        self.assertEqual(servers[0]["ip"], "10.0.0.7")
        self.assertTrue(servers[0]["done"])
        self.assertNotIn("password", servers[0])

    def test_console_api(self):
        self.make("kons", code='''
from PyQt6.QtWidgets import QLabel
STATE = {}

def register(api):
    w = QLabel("Konsole")
    w.resize(200, 100)
    STATE["handle"] = api.add_console("Switch 1", w, activate=lambda: STATE.setdefault("opened", 1))
    STATE["term"] = api.terminal()
    STATE["w"] = w
''')
        self.approve("kons")
        host = plugins.Host()
        self.assertTrue(host.load()[0].loaded)
        mod = self.module("kons")
        sess = host.console_sessions()
        self.assertEqual(len(sess), 1)
        key, widget, hidden_ok, title = sess[0]
        self.assertEqual(title, "Switch 1")
        self.assertTrue(hidden_ok)
        self.assertTrue(host.console_activate(key))
        self.assertEqual(mod.STATE["opened"], 1)
        self.assertFalse(host.console_activate("plugin:0"))
        from bmc_steward.console import pyte
        self.assertEqual(mod.STATE["term"] is None, pyte is None)
        mod.STATE["handle"].set_title("Switch A")
        self.assertEqual(host.console_sessions()[0][3], "Switch A")
        mod.STATE["handle"].remove()
        self.assertEqual(host.console_sessions(), [])
        with self.assertRaises(TypeError):
            plugins.PluginAPI(host.plugins[0], host).add_console("x", "kein Widget")

    def test_failed_register_removes_its_consoles(self):
        self.make("halb", code='''
from PyQt6.QtWidgets import QLabel

def register(api):
    api.add_console("x", QLabel("x"))
    raise RuntimeError("kaputt")
''')
        self.approve("halb")
        host = plugins.Host()
        host.load()
        self.assertEqual(host.consoles, [])

    def test_example_plugin_loads(self):
        import shutil
        from tests._support import REPO
        shutil.copytree(REPO / "docs" / "plugin-beispiel", self.tmp / "plugins" / "beispiel")
        for root, ds, fs in os.walk(self.tmp / "plugins"):
            os.chmod(root, 0o700)
        self.approve("beispiel")
        host = plugins.Host()
        self.assertTrue(host.load()[0].loaded)
        self.assertEqual(len(host.build_pages()), 1)
        host.emit("server_done", "10.0.0.9")
        self.assertIn("10.0.0.9", (self.tmp / "plugin-daten" / "beispiel" / "fertig.csv").read_text())


if __name__ == "__main__":
    unittest.main()


class TestRestartAfterApproval(Base):
    """Nach einer geänderten Freigabe fragt das Programm, ob es neu starten soll."""

    def window(self):
        from bmc_steward.main import Window
        w = Window()
        self.addCleanup(lambda: (setattr(w, "dirty", False), w.close()))
        return w

    def test_settings_report_changed_approvals(self):
        from bmc_steward.settings import SettingsDialog
        self.make("eins")
        dlg = SettingsDialog()
        dlg.plugin_page.table.item(0, 0).setCheckState(plugins_checked())
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes):
            dlg.save()
        self.assertTrue(dlg.plugins_changed)
        dlg2 = SettingsDialog()
        dlg2.save()
        self.assertFalse(dlg2.plugins_changed)

    def test_asks_and_restarts(self):
        from bmc_steward import main
        w = self.window()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as ask, \
                patch.object(main.QProcess, "startDetached", return_value=(True, 4711)) as start, \
                patch.object(main.QApplication, "quit") as quit_:
            self.assertTrue(w.ask_restart())
        ask.assert_called_once()
        start.assert_called_once()
        self.assertEqual(start.call_args[0][0], sys.executable)
        quit_.assert_called_once()
        self.assertFalse(w.isVisible())

    def test_no_restart_when_declined_or_close_cancelled(self):
        from bmc_steward import main
        w = self.window()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
                patch.object(main.QProcess, "startDetached") as start:
            self.assertFalse(w.ask_restart())
        start.assert_not_called()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(main.Window, "close", return_value=False), \
                patch.object(main.QProcess, "startDetached") as start, \
                patch.object(main.QApplication, "quit") as quit_:
            self.assertFalse(w.ask_restart())             # z. B. Lauf aktiv oder Liste nicht gespeichert
        start.assert_not_called()
        quit_.assert_not_called()

    def test_open_settings_asks_only_after_change(self):
        from bmc_steward import main, settings
        w = self.window()
        for changed, calls in ((False, 0), (True, 1)):
            def fake_exec(dlg, changed=changed):
                dlg.plugins_changed = changed
                return 1
            with patch.object(settings.SettingsDialog, "exec", fake_exec), \
                    patch.object(main.Window, "ask_restart") as ask:
                w.open_settings()
            self.assertEqual(ask.call_count, calls)
