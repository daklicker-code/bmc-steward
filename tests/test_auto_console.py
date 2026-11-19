"""DHCP-Seite: neu angeschlossene Server automatisch in der UEFI-Konsole öffnen (Haken, voreingestellt aus).
Es zählen nur Geräte, die nach dem Setzen des Hakens auftauchen; ein Neustart wird nie ausgelöst."""
import unittest

from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.main import Window


class TestAutoConsole(unittest.TestCase):
    def setUp(self):
        self.w = Window()
        self.w.dirty = False
        self.addCleanup(self.w.close)

    def test_off_by_default_and_only_new_devices_count(self):
        w = self.w
        self.assertFalse(w.auto_console.isChecked())
        w.show_leases([("10.99.0.10", "aa:bb:cc:00:00:01", "")])
        self.assertEqual(w.console_wait, {})                     # Haken aus: nichts
        w.auto_console.setChecked(True)
        self.assertIn("aa:bb:cc:00:00:01", w.console_seen)       # schon da: zählt nicht
        w.show_leases([("10.99.0.10", "aa:bb:cc:00:00:01", ""), ("10.99.0.11", "aa:bb:cc:00:00:02", "")])
        self.assertEqual(list(w.console_wait), ["10.99.0.11"])
        self.assertTrue(w.console_timer.isActive())
        w.auto_console.setChecked(False)
        self.assertFalse(w.console_timer.isActive())

    def test_ready_server_fills_the_console(self):
        w = self.w
        w.auto_console.setChecked(True)
        w.console_wait["10.99.0.12"] = 1
        w.cur_pass.setText("")
        calls = []
        w.console_connect = lambda *a, **k: calls.append(a)

        class Sock:
            def disconnectFromHost(self):
                pass

            def deleteLater(self):
                pass
        w.auto_console_ready("10.99.0.12", Sock())
        self.assertEqual(w.con_ip.currentText(), "10.99.0.12")
        self.assertEqual(w.tabs.currentIndex(), w.tabs.indexOf(w.con_page))
        self.assertEqual(calls, [])                              # ohne Passwort keine Anmeldung
        self.assertNotIn("10.99.0.12", w.console_wait)


if __name__ == "__main__":
    unittest.main()
