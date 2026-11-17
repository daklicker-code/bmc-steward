"""Laufzeit-Überwachung des DHCP-Servers (Window.dhcp_guard in bmc_steward.dhcp_tab).

dhcp_guard() läuft alle paar Sekunden, solange der eigene DHCP-Server läuft, und muss ihn in zwei
Fällen stoppen: wenn die Karte inzwischen die Standardroute trägt (kein getrenntes Staging-Netz
mehr), oder wenn im selben Netz ein fremder DHCP-Server auftaucht (z. B. weil der Staging-Switch
versehentlich mit dem Produktivnetz verbunden wird). Die eigene Antwort darf dabei nie als „fremd“
gewertet werden, und ohne laufenden Server darf gar nichts passieren.
"""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")          # deterministische Texte, unabhängig vom gespeicherten Profil
import bmc_steward.dhcp_tab as dhcp_tab_mod
from bmc_steward.main import Window


class TestDhcpGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def setUp(self):
        self.w.dhcp_ip = "192.168.50.1"
        self.w.dhcp_running = lambda: True
        self.w.default_route_ip = lambda: None     # kein Routen-Konflikt, sofern nicht überschrieben
        self.calls = []
        self.w.stop_dhcp = lambda: self.calls.append("stop")
        self.w.dlog = lambda *a, **k: self.calls.append(a)
        self._orig_probe = dhcp_tab_mod.probe_dhcp_servers
        dhcp_tab_mod.probe_dhcp_servers = lambda ip, timeout=1.5: self._probe_result

    def tearDown(self):
        dhcp_tab_mod.probe_dhcp_servers = self._orig_probe

    def test_not_running_does_nothing(self):
        self.w.dhcp_running = lambda: False
        dhcp_tab_mod.probe_dhcp_servers = lambda *a, **k: self.fail("probe_dhcp_servers sollte nicht aufgerufen werden")
        self.w.dhcp_guard()
        self.assertEqual(self.calls, [])

    def test_default_route_conflict_stops_without_probing(self):
        self.w.default_route_ip = lambda: self.w.dhcp_ip
        dhcp_tab_mod.probe_dhcp_servers = lambda *a, **k: self.fail("Routen-Konflikt muss vor dem Probe greifen")
        self.w.dhcp_guard()
        self.assertEqual(self.calls[0], "stop")
        self.assertIn("Standardroute", self.calls[1][0])

    def test_no_foreign_server_keeps_running(self):
        self._probe_result = None             # Probe nicht möglich (z. B. Port 68 ohne Root)
        self.w.dhcp_guard()
        self.assertEqual(self.calls, [])

    def test_only_own_server_answers_keeps_running(self):
        self._probe_result = {self.w.dhcp_ip}   # eigener dnsmasq antwortet auf die eigene Discover-Probe
        self.w.dhcp_guard()
        self.assertEqual(self.calls, [])

    def test_foreign_server_stops_and_logs_its_address(self):
        self._probe_result = {self.w.dhcp_ip, "192.168.50.99"}
        self.w.dhcp_guard()
        self.assertEqual(self.calls[0], "stop")
        self.assertIn("192.168.50.99", self.calls[1][0])
        self.assertNotIn(self.w.dhcp_ip, self.calls[1][0])

    def test_multiple_foreign_servers_are_all_named(self):
        self._probe_result = {self.w.dhcp_ip, "192.168.50.98", "192.168.50.99"}
        self.w.dhcp_guard()
        self.assertEqual(self.calls[0], "stop")
        self.assertIn("192.168.50.98", self.calls[1][0])
        self.assertIn("192.168.50.99", self.calls[1][0])


if __name__ == "__main__":
    unittest.main()
