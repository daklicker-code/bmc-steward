"""Programmstart: Nichts Langsames darf das Fenster aufhalten. Geprüft wird, dass große optionale Pakete
(QtWebEngine, paramiko, pyghmi) erst bei Gebrauch geladen werden und dass Selbstschutz (SMBIOS über
PowerShell/hostnamectl) und Schlüsselbund (D-Bus) im Hintergrund suchen."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import qapp

ROOT = Path(__file__).resolve().parent.parent


class TestLazyImports(unittest.TestCase):
    def test_main_import_loads_no_heavy_optional_packages(self):
        code = ("import sys, json; import bmc_steward.main; "
                "print(json.dumps([m for m in ('paramiko', 'pyghmi', 'cryptography', "
                "'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineCore') if m in sys.modules]))")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", BMC_STEWARD_DATA=tempfile.mkdtemp())
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]), [])

    def test_lazy_module_loads_on_first_use(self):
        from bmc_steward import console
        self.assertIsNone(console._lazy("gibt_es_nicht_bmc_steward"))
        mod = console._lazy("json")
        self.assertIsNotNone(mod)
        self.assertIs(mod.dumps, json.dumps)


class TestSelfGuardInBackground(unittest.TestCase):
    def test_smbios_read_does_not_block_and_own_waits_for_it(self):
        from bmc_steward import selfguard
        started = threading.Event()

        def slow_smbios():
            started.set()
            time.sleep(0.6)                 # wie PowerShell unter Windows
            return "Dell Inc.", "PowerEdge R650", "4c4c4544-0042-3510-8048-b2c04f593132", ["ABC1234", ""]
        with patch.object(selfguard, "_smbios", slow_smbios), \
                patch.object(selfguard, "hypervisor", return_value=""):
            t0 = time.monotonic()
            g = selfguard.SelfGuard(Path(tempfile.mkdtemp()) / "g.json")
            self.assertLess(time.monotonic() - t0, 0.3)
            self.assertTrue(started.wait(2))
            self.assertEqual(g.virt, "")
            self.assertFalse(g.own_ready)
            self.assertEqual(g.own, ["4c4c4544-0042-3510-8048-b2c04f593132", "ABC1234"])   # wartet
            self.assertTrue(g.own_ready)

    def test_vm_needs_no_smbios_at_all(self):
        from bmc_steward import selfguard
        with patch.object(selfguard, "_smbios", side_effect=AssertionError("darf nicht laufen")), \
                patch.object(selfguard, "_vendor_product", return_value=("VMware, Inc.", "VMware20,1")), \
                patch.object(selfguard, "WINDOWS", False):
            g = selfguard.SelfGuard(Path(tempfile.mkdtemp()) / "g.json")
            self.assertEqual(g.virt, "VMware")
            self.assertTrue(g.own_ready)
            self.assertEqual(g.own, [])


class TestVaultInBackground(unittest.TestCase):
    def test_probe_runs_in_background_and_available_waits(self):
        from bmc_steward import vault
        if vault.keyring is None:
            self.skipTest("Paket keyring nicht installiert")
        import keyring.backend

        class Fake(keyring.backend.KeyringBackend):
            priority = 1

            def get_password(self, *a):
                return None

            def set_password(self, *a):
                pass

            def delete_password(self, *a):
                pass

        def slow():
            time.sleep(0.4)
            return Fake()
        with patch.object(vault, "_probe", None), patch.object(vault.keyring, "get_keyring", side_effect=slow):
            t0 = time.monotonic()
            vault.probe_async()
            self.assertLess(time.monotonic() - t0, 0.2)
            self.assertFalse(vault.ready())
            vault._probe.join(5)
            self.assertTrue(vault.ready())


class TestWindowStartsWithoutWaiting(unittest.TestCase):
    def test_guard_button_updates_when_background_read_finishes(self):
        qapp()
        from PyQt6.QtWidgets import QApplication
        from bmc_steward import i18n, main, selfguard
        i18n.build_texts()
        g = selfguard.SelfGuard(Path(tempfile.mkdtemp()) / "g.json", probe=False)
        gate = threading.Event()

        def probe():
            gate.wait(5)
            g._own = ["XYZ12345", "4c4c4544-0042-3510-8048-b2c04f593132"]
        g._probe = threading.Thread(target=probe, daemon=True)
        g._probe.start()
        with patch.object(selfguard, "_GUARD", g):
            w = main.Window()
            self.addCleanup(w.close)
            t0 = time.monotonic()
            w.update_guard_btn()                # wartet nicht auf den Hintergrund-Thread
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertTrue(w.guard_btn.text().endswith("(0)"), w.guard_btn.text())
            gate.set()
            deadline = time.monotonic() + 5
            while not w.guard_btn.text().endswith("(2)") and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.02)
            self.assertTrue(w.guard_btn.text().endswith("(2)"), w.guard_btn.text())


if __name__ == "__main__":
    unittest.main()
