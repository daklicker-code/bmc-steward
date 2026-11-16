"""vBMC Router-Modus Isolation: Simulator-Instanzen werden aus DHCP-Lease-Liste gefiltert.

Problem: Wenn der vBMC-Simulator im Router-Modus auf derselben physischen NIC lauft wie
ein echter Server, erscheinen beide in derselben DHCP-Lease-Liste. Die Simulator-Eintraege
(erkennbar an ihren MACs aus vbmc_servers) muessen herausgefiltert werden, damit der Benutzer
nur echte Hardware sieht.

Konkrete Situation, die diese Aenderung ausgeloest hat:
  - 6 vBMC-Instanzen mit 02:00:00:*-MACs (Fallback-OUI fuer unbekannten Vendor)
  - Lenovo SR650 direkt mit Laptop verbunden
  - DHCP zeigte nur die 6 Simulator-Eintraege, SR650 (statische IP) gar nicht
  - vBMC_running_macs() liefert die bekannten Simulator-MACs
  - show_leases() filtert sie vor dem Anzeigen heraus

Tests:
  1. Simulator-MAC wird aus der Lease-Tabelle gefiltert
  2. Echter Server neben Simulator bleibt in der Tabelle
  3. Ohne laufenden Simulator: kein Filtering, alle Geraete sichtbar
  4. Fallback-OUI 02:00:00:* (unbekannter Vendor) wird genauso gefiltert
  5. Gross-/Kleinschreibung der MAC-Adresse spielt keine Rolle
"""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.main import Window

# Typische vBMC-Simulator-MACs (Lenovo-OUI 08:94:ef, korrekte Konfiguration)
VBMC_LENOVO_1 = ("10.0.0.101", "08:94:ef:3a:1b:cc", "XCC-SIM0001")
VBMC_LENOVO_2 = ("10.0.0.102", "08:94:ef:7f:2d:aa", "XCC-SIM0002")
VBMC_DELL_1   = ("10.0.0.103", "d0:94:66:11:22:33", "idrac-SIM001")

# vBMC-Simulator mit Fallback-OUI (Vendor unbekannt → 02:00:00:*)
VBMC_FALLBACK_1 = ("169.254.0.100", "02:00:00:d0:94:a4", "")
VBMC_FALLBACK_2 = ("169.254.0.101", "02:00:00:23:ab:35", "")
VBMC_FALLBACK_3 = ("169.254.0.102", "02:00:00:12:c9:47", "")

# Echter Lenovo SR650 (andere MAC, physisch angeschlossen)
REAL_SR650 = ("10.0.1.50", "d0:94:66:aa:bb:01", "XCC-J1A2B3C")  # physische Lenovo-MAC

# Echter Dell-Server (kein Simulator)
REAL_DELL = ("10.0.1.60", "24:b6:fd:ee:ff:01", "idrac-EEF1234")


def _make_vbmc_servers(macs_and_names):
    """Hilfsfunktion: Simuliert den vbmc_servers-Zustand wie er von vbmc_tab gesetzt wird."""
    return [{"mac": mac, "addr": "10.0.0.1", "status": "lauft", "serial": f"SIM{i}"}
            for i, (mac, _name) in enumerate(macs_and_names)]


class TestVbmcRouterIsolation(unittest.TestCase):
    """Prueft, dass vBMC-Simulator-MACs in der DHCP-Lease-Tabelle unsichtbar sind."""

    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def setUp(self):
        w = self.w
        w.show_leases([])
        w.lease_taken.clear()
        w.auto_take.setChecked(True)
        w.table.setRowCount(0)
        w.add_row()
        # Simulator standardmaessig gestoppt (leere Liste)
        w.vbmc_servers = []

    def ips_in_lease_table(self):
        return {self.w.leases.item(r, 0).text()
                for r in range(self.w.leases.rowCount())}

    def ips_in_server_list(self):
        return [r[0] for r in self.w.rows()]

    # ------------------------------------------------------------------ Kerntests

    def test_simulator_mac_filtered_from_lease_table(self):
        """Simulator lauft: seine MAC wird nicht in der DHCP-Tabelle angezeigt."""
        self.w.vbmc_servers = _make_vbmc_servers([
            (VBMC_LENOVO_1[1], "sim1"), (VBMC_LENOVO_2[1], "sim2"),
        ])
        self.w.show_leases([VBMC_LENOVO_1, VBMC_LENOVO_2])
        self.assertEqual(self.ips_in_lease_table(), set(),
                         "Simulator-Eintraege duerften nicht in der DHCP-Tabelle erscheinen")

    def test_real_server_stays_visible_alongside_simulator(self):
        """Echter Server neben laufendem Simulator: nur echter Server erscheint in der Tabelle."""
        self.w.vbmc_servers = _make_vbmc_servers([
            (VBMC_LENOVO_1[1], "sim1"), (VBMC_LENOVO_2[1], "sim2"), (VBMC_DELL_1[1], "sim3"),
        ])
        self.w.show_leases([VBMC_LENOVO_1, VBMC_LENOVO_2, VBMC_DELL_1, REAL_SR650])
        # Nur der echte SR650 darf sichtbar sein
        self.assertEqual(self.ips_in_lease_table(), {REAL_SR650[0]},
                         "Echter Server muss sichtbar bleiben, auch wenn Simulator gleichzeitig lauft")

    def test_no_simulator_running_all_devices_visible(self):
        """Ohne laufenden Simulator: kein Filtering, alle Geraete sichtbar."""
        self.w.vbmc_servers = []   # kein Simulator aktiv
        self.w.show_leases([REAL_SR650, REAL_DELL])
        self.assertEqual(self.ips_in_lease_table(), {REAL_SR650[0], REAL_DELL[0]},
                         "Ohne Simulator darf kein Filtering stattfinden")

    def test_fallback_oui_filtered(self):
        """Fallback-OUI 02:00:00:* (unbekannter Vendor) wird genauso gefiltert wie korrekte OUI."""
        self.w.vbmc_servers = _make_vbmc_servers([
            (VBMC_FALLBACK_1[1], ""), (VBMC_FALLBACK_2[1], ""), (VBMC_FALLBACK_3[1], ""),
        ])
        self.w.show_leases([VBMC_FALLBACK_1, VBMC_FALLBACK_2, VBMC_FALLBACK_3])
        self.assertEqual(self.ips_in_lease_table(), set(),
                         "Fallback-02:00:00-MACs muessen herausgefiltert werden")

    def test_fallback_oui_real_server_survives(self):
        """Genau das urspruengliche Fehlerbild: 6 Fallback-vBMC + echter Server → nur echter sichtbar."""
        fallbacks = [
            ("169.254.0.100", "02:00:00:d0:94:a4", ""),
            ("169.254.0.101", "02:00:00:23:ab:35", ""),
            ("169.254.0.102", "02:00:00:12:c9:47", ""),
            ("169.254.0.103", "02:00:00:cf:2f:c8", ""),
            ("169.254.0.104", "02:00:00:cd:d4:9d", ""),
            ("169.254.0.105", "02:00:00:66:c8:1c", ""),
        ]
        self.w.vbmc_servers = _make_vbmc_servers([(e[1], "") for e in fallbacks])
        self.w.show_leases(fallbacks + [REAL_SR650])
        self.assertEqual(self.ips_in_lease_table(), {REAL_SR650[0]},
                         "Exaktes Fehlerbild: 6 Simulator-Eintraege muessen verschwinden, SR650 bleibt")

    def test_mac_case_insensitive(self):
        """Gross-/Kleinschreibung der MAC-Adresse spielt keine Rolle beim Vergleich."""
        # vbmc_servers enthaelt Grossbuchstaben, Lease-Liste Kleinbuchstaben
        mac_upper = VBMC_LENOVO_1[1].upper()   # "08:94:EF:3A:1B:CC"
        mac_lower = VBMC_LENOVO_1[1].lower()   # "08:94:ef:3a:1b:cc"
        self.w.vbmc_servers = [{"mac": mac_upper, "addr": "x", "serial": "1"}]
        self.w.show_leases([(VBMC_LENOVO_1[0], mac_lower, "sim")])
        self.assertEqual(self.ips_in_lease_table(), set(),
                         "MAC-Vergleich muss case-insensitiv sein")

    def test_auto_take_ignores_simulator_entries(self):
        """auto_take_leases() uebernimmt keine Simulator-Eintraege in die Serverliste."""
        self.w.vbmc_servers = _make_vbmc_servers([
            (VBMC_LENOVO_1[1], "sim1"), (VBMC_LENOVO_2[1], "sim2"),
        ])
        self.w.show_leases([VBMC_LENOVO_1, VBMC_LENOVO_2, REAL_SR650])
        # In der Serverliste darf nur der echte SR650 stehen
        result_ips = self.ips_in_server_list()
        self.assertIn(REAL_SR650[0], result_ips)
        self.assertNotIn(VBMC_LENOVO_1[0], result_ips)
        self.assertNotIn(VBMC_LENOVO_2[0], result_ips)

    def test_vbmc_running_macs_empty_when_stopped(self):
        """vbmc_running_macs() gibt leere Menge zurueck, wenn kein Simulator lauft."""
        self.w.vbmc_servers = []
        self.assertEqual(self.w.vbmc_running_macs(), set())

    def test_vbmc_running_macs_returns_lowercase(self):
        """vbmc_running_macs() liefert immer Kleinbuchstaben, unabhaengig vom Quellformat."""
        self.w.vbmc_servers = [{"mac": "08:94:EF:AA:BB:CC", "addr": "x", "serial": "1"}]
        macs = self.w.vbmc_running_macs()
        self.assertEqual(macs, {"08:94:ef:aa:bb:cc"})

    def test_vbmc_running_macs_skips_empty_mac(self):
        """vbmc_running_macs() ignoriert Server ohne MAC (noch keine Netzadresse bekommen)."""
        self.w.vbmc_servers = [
            {"mac": "08:94:ef:11:22:33", "addr": "10.0.0.1", "serial": "A"},
            {"mac": "",                  "addr": "",           "serial": "B"},   # noch kein Netz
        ]
        macs = self.w.vbmc_running_macs()
        self.assertEqual(macs, {"08:94:ef:11:22:33"})


if __name__ == "__main__":
    unittest.main()
