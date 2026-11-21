"""Portscan-Plugin (docs/plugin-portscan): Eingaben, Scan gegen lokale Ports, Laden über das Pluginsystem."""
import importlib.util
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import REPO, qapp

qapp()
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from bmc_steward import i18n, plugins  # noqa: E402

SRC = REPO / "docs" / "plugin-portscan"
spec = importlib.util.spec_from_file_location("portscan_scan_test", SRC / "scan.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    return s


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestParse(unittest.TestCase):
    def test_ports(self):
        self.assertEqual(scan.parse_ports("443, 22 ;80 20-22"), (20, 21, 22, 80, 443))
        self.assertIn(623, scan.parse_ports("bmc"))
        self.assertIn(17990, scan.parse_ports("BMC"))
        self.assertEqual(len(scan.parse_ports("all")), 65535)
        for bad in ("", "0", "65536", "80-20", "abc", "1-x"):
            with self.assertRaises(ValueError, msg=bad):
                scan.parse_ports(bad)

    def test_targets(self):
        self.assertEqual(scan.parse_targets("10.0.0.5 10.0.0.5,10.0.0.0/30"), ["10.0.0.5", "10.0.0.1", "10.0.0.2"])
        self.assertEqual(scan.parse_targets("10.0.0.8-10"), ["10.0.0.8", "10.0.0.9", "10.0.0.10"])
        self.assertEqual(scan.parse_targets("10.0.0.254-10.0.1.1"),
                         ["10.0.0.254", "10.0.0.255", "10.0.1.0", "10.0.1.1"])
        self.assertEqual(scan.parse_targets("10.0.0.7/32 ::1 bmc-01.lab"), ["10.0.0.7", "::1", "bmc-01.lab"])
        for bad in ("10.0.0.9-5", "10.0.0.0/8", "10.0.0.1-300", "x/24", "a;b|c", "bmc$1"):
            with self.assertRaises(ValueError, msg=bad):
                scan.parse_targets(bad)


class TestScan(unittest.TestCase):
    def test_open_closed_and_banner(self):
        srv = listener()
        self.addCleanup(srv.close)
        port, closed = srv.getsockname()[1], free_port()

        import threading

        def greet():
            try:
                c, _ = srv.accept()
                c.sendall(b"SSH-2.0-Test\r\n")
                time.sleep(0.2)
                c.close()
            except OSError:
                pass
        threading.Thread(target=greet, daemon=True).start()
        self.assertEqual(scan.probe_tcp("127.0.0.1", port, 1.0, banner=True), ("offen", "SSH-2.0-Test"))
        self.assertEqual(scan.probe_tcp("127.0.0.1", closed, 1.0)[0], "geschlossen")
        self.assertEqual(scan.probe_tcp("nicht-aufloesbar.invalid", 80, 0.5)[0], "unbekannt")

    def test_scan_collects_and_stops(self):
        srv = listener()
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        seen, progress = [], []
        rows = scan.scan(["127.0.0.1"], (port, free_port()), workers=4, timeout=0.5,
                         on_result=seen.append, on_progress=lambda d, t: progress.append((d, t)))
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["state"] for r in rows}, {"offen", "geschlossen"})
        self.assertEqual(progress[-1], (2, 2))
        self.assertEqual(len(seen), 2)
        # Abbruch: nach dem Stopp keine weiteren Prüfungen
        calls = []
        rows = scan.scan(["10.0.0.1"], tuple(range(1, 201)), workers=1, stopped=lambda: len(calls) >= 3,
                         probe=lambda h, p, t, b: calls.append(p) or ("gefiltert", ""))
        self.assertLess(len(calls), 10)

    def test_ipmi_and_limits(self):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.bind(("127.0.0.1", 0))
        self.addCleanup(u.close)
        import threading

        def pong():
            data, addr = u.recvfrom(64)
            if data == scan.RMCP_PING:
                u.sendto(scan.RMCP_PONG_PREFIX + bytes(16), addr)
        threading.Thread(target=pong, daemon=True).start()
        self.assertEqual(scan.probe_ipmi("127.0.0.1", 1.0, port=u.getsockname()[1])[0], "offen")
        rows = scan.scan(["10.0.0.1"], (22,), ipmi=True, probe=lambda *a: ("gefiltert", ""),
                         probe_udp=lambda h, t: ("offen", ""))
        self.assertEqual([(r["proto"], r["port"], r["service"]) for r in rows],
                         [("tcp", 22, "SSH"), ("udp", 623, "IPMI (RMCP)")])
        with self.assertRaises(ValueError):
            scan.scan(["10.0.0.%d" % i for i in range(1, 6)], scan.parse_ports("all"))
        self.assertIn("host,port,proto", scan.to_csv(rows))


class TestAsPlugin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bmc-portscan-"))
        p = patch.object(i18n, "DATA", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        b = patch.object(plugins, "BUILTIN_DIR", self.tmp / "mitgeliefert")   # nur die Plugins dieses Tests
        b.start()
        self.addCleanup(b.stop)
        plugins._MODULES.clear()
        self.addCleanup(plugins._MODULES.clear)
        os.environ.pop("BMC_STEWARD_NO_PLUGINS", None)
        shutil.copytree(SRC, self.tmp / "plugins" / "portscan",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for root, ds, fs in os.walk(self.tmp / "plugins"):
            os.chmod(root, 0o700)
        plugins.save_state({"portscan": plugins.digest(self.tmp / "plugins" / "portscan")})

    def page(self):
        host = plugins.Host()
        found = host.load()
        self.assertTrue(found[0].loaded, found[0].error)
        pages = host.build_pages()
        self.assertEqual(len(pages), 1)
        self.assertIsInstance(pages[0][2], QWidget)
        return host, pages[0][2]

    def test_loads_and_scans_from_page(self):
        i18n.set_language("de")
        host, w = self.page()
        self.addCleanup(w.close)
        srv = listener()
        self.addCleanup(srv.close)
        w.targets.setText("127.0.0.1")
        w.ports.setText(f"{srv.getsockname()[1]},{free_port()}")
        w.ipmi.setChecked(False)
        lamps = []
        api = w.api
        with patch.object(type(api), "set_lamp", lambda self, color=None: lamps.append(color)):
            self.assertTrue(w.start(confirm=False))
            self.assertEqual(lamps, ["#f2c200"])            # gelb wie das Testlabor, solange der Scan läuft
            deadline = time.monotonic() + 10
            while w.worker is not None and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.02)
        self.assertEqual(lamps, ["#f2c200", None])
        deadline = time.monotonic() + 10
        while w.worker is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        self.assertIsNone(w.worker)
        self.assertEqual(w.table.rowCount(), 1)          # nur offene
        self.assertEqual(w.table.item(0, 4).text(), "offen")
        w.only_open.setChecked(False)
        self.assertEqual(w.table.rowCount(), 2)
        out = w.save_csv(str(self.tmp / "out.csv"))
        self.assertIn("geschlossen", Path(out).read_text())
        # Plugin-Ordner bleibt unverändert (sonst wäre die Freigabe beim nächsten Start weg)
        self.assertEqual(plugins.Plugin(self.tmp / "plugins" / "portscan").status(plugins.load_state())[1], True)
        host.emit("closing")

    def test_english_and_bad_input(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "de")
        host, w = self.page()
        self.addCleanup(w.close)
        self.assertEqual(host.pages[0][1], "Port scan")
        w.targets.setText("10.0.0.9-1")
        with patch.object(sys.modules[type(w).__module__].QMessageBox, "warning") as warn:
            self.assertFalse(w.start(confirm=False))
        warn.assert_called_once()

    def test_every_text_translated(self):
        import ast
        tree = ast.parse((SRC / "plugin.py").read_text(encoding="utf-8"))
        used = {n.args[0].value for n in ast.walk(tree)
                if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) == "t"
                and n.args and isinstance(n.args[0], ast.Constant)}
        texts = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
                                      and getattr(n.targets[0], "id", "") == "TEXTS"))
        labels = {"eigene", "BMC-Dienste", "Server-Dienste", "bekannte Dienste", "alle 65535",
                  "Ziel", "Port", "Protokoll", "Dienst", "Zustand", "Banner",
                  "offen", "geschlossen", "gefiltert", "unbekannt"}
        self.assertEqual(sorted((used | labels) - set(texts)), [])
        self.assertTrue(all(len(v) == 3 and all(v) for v in texts.values()))


if __name__ == "__main__":
    unittest.main()
