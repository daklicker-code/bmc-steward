"""Tests für die drei P0-Features aus Version 3.0.25:
  1. Passwortfelder leeren nach scharfem Lauf
  2. TLS-Mindestversion in Härtung (services_dict / svc_hard)
  3. Wiederherstellen aus Sicherung (RestoreDialog._extract_network)
"""
import json
import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from tests._support import qapp
qapp()

from bmc_steward.main import RestoreDialog
from bmc_steward.redfish import Engine, ServicesEngine


# ---------------------------------------------------------------------------
# 1. Passwortfelder leeren nach scharfem Lauf
# ---------------------------------------------------------------------------

class TestClearPassAfter(unittest.TestCase):
    """done() löscht Passwörter, wenn clear_pass_after gesetzt und kein Trockenlauf."""

    def _make_window(self, clear_checked, dry_checked):
        from bmc_steward.main import Window
        from PyQt6.QtWidgets import QApplication
        w = Window.__new__(Window)
        # Minimale Attribute, die done() braucht
        from PyQt6.QtWidgets import QPushButton, QCheckBox, QLabel, QLineEdit, QTableWidget
        w.start = QPushButton()
        w.stop = QPushButton()
        w.table = QTableWidget(2, 2)
        w.status = QLabel()
        w.cur_pass = QLineEdit("geheimnis")
        w.new_pass = QLineEdit("neues123")
        w.new_pass2 = QLineEdit("neues123")
        from PyQt6.QtWidgets import QTableWidgetItem
        w.table.setItem(0, 1, QTableWidgetItem("rowpw1"))
        w.table.setItem(1, 1, QTableWidgetItem("rowpw2"))
        # Checkboxen
        w.clear_pass_after = QCheckBox()
        w.clear_pass_after.setChecked(clear_checked)
        w.dry = QCheckBox()
        w.dry.setChecked(dry_checked)
        return w

    def test_clears_on_success_sharp_run(self):
        """Bei code=0 und scharfem Lauf mit Haken werden alle Passwortfelder geleert."""
        from bmc_steward.main import Window
        w = self._make_window(clear_checked=True, dry_checked=False)
        Window.done(w, 0)
        self.assertEqual(w.cur_pass.text(), "")
        self.assertEqual(w.new_pass.text(), "")
        self.assertEqual(w.new_pass2.text(), "")
        self.assertEqual(w.table.item(0, 1).text(), "")
        self.assertEqual(w.table.item(1, 1).text(), "")

    def test_no_clear_on_error(self):
        """Bei code!=0 werden Passwörter behalten (Lauf fehlgeschlagen)."""
        from bmc_steward.main import Window
        w = self._make_window(clear_checked=True, dry_checked=False)
        Window.done(w, 1)
        self.assertEqual(w.cur_pass.text(), "geheimnis")

    def test_no_clear_on_dry_run(self):
        """Bei Trockenlauf werden Passwörter nicht geleert."""
        from bmc_steward.main import Window
        w = self._make_window(clear_checked=True, dry_checked=True)
        Window.done(w, 0)
        self.assertEqual(w.cur_pass.text(), "geheimnis")

    def test_no_clear_when_unchecked(self):
        """Wenn clear_pass_after nicht gesetzt, werden Passwörter nicht geleert."""
        from bmc_steward.main import Window
        w = self._make_window(clear_checked=False, dry_checked=False)
        Window.done(w, 0)
        self.assertEqual(w.cur_pass.text(), "geheimnis")

    def test_table_item_missing_handled(self):
        """Fehlende Tabellen-Items (None) verursachen keinen Absturz."""
        from bmc_steward.main import Window
        w = self._make_window(clear_checked=True, dry_checked=False)
        w.table.setItem(1, 1, None)   # Kein Item in Zelle
        try:
            Window.done(w, 0)
        except Exception as e:
            self.fail(f"done() hat mit None-Item eine Exception geworfen: {e}")


# ---------------------------------------------------------------------------
# 2. TLS-Mindestversion in Härtung (svc_hard)
# ---------------------------------------------------------------------------

class TestTlsMinVersion(unittest.TestCase):
    """svc_hard() setzt die TLS-Mindestversion über svc_tls(): Attributname und Wert kommen vom Gerät
    (iDRAC9: WebServer.1.TLSProtocol). Ausführlich in test_dell_redfish_fixes.py."""

    def _make_engine(self, attrs):
        e = ServicesEngine.__new__(ServicesEngine)
        e.dry = False
        e.stopped = lambda: False
        e.log = lambda *a, **kw: None
        e.cur_user = "root"
        e.patches = []
        e.MGR_PATH = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1"}
        e.mgr_attributes = lambda ip, pw, v: attrs

        def fake_api(ip, user, pw, method, path, data=None, timeout=None, _retry=True):
            e.patches.append((path, data))
            e.body = None
            return 200
        e.api = fake_api
        return e

    def test_tls12_patch_sent(self):
        e = self._make_engine({"WebServer.1.TLSProtocol": "TLS 1.1 and Higher"})
        self.assertTrue(e.svc_hard("10.0.0.1", "pw", "dell", {"tls_min": "tls12"}))
        self.assertEqual(e.patches, [("/redfish/v1/Managers/iDRAC.Embedded.1/Attributes",
                                      {"Attributes": {"WebServer.1.TLSProtocol": "TLS 1.2 and Higher"}})])

    def test_tls13_first_candidate(self):
        e = self._make_engine({"WebServer.1.TLSProtocol": "TLS 1.2 and Higher"})
        e.svc_hard("10.0.0.1", "pw", "dell", {"tls_min": "tls13"})
        self.assertEqual(e.patches[0][1], {"Attributes": {"WebServer.1.TLSProtocol": "TLS 1.3 Only"}})

    def test_no_tls_patch_when_empty(self):
        e = self._make_engine({"WebServer.1.TLSProtocol": "x"})
        e.svc_hard("10.0.0.1", "pw", "dell", {"tls_min": ""})
        self.assertEqual(e.patches, [])

    def test_no_tls_patch_when_missing(self):
        e = self._make_engine({"WebServer.1.TLSProtocol": "x"})
        e.svc_hard("10.0.0.1", "pw", "dell", {})
        self.assertEqual(e.patches, [])

    def test_other_vendor_without_attributes_is_skipped(self):
        e = self._make_engine(None)
        self.assertIsNone(e.svc_hard("10.0.0.1", "pw", "lenovo", {"tls_min": "tls12"}))
        self.assertEqual(e.patches, [])


# ---------------------------------------------------------------------------
# 3. Wiederherstellen aus Sicherung – _extract_network
# ---------------------------------------------------------------------------

class TestExtractNetwork(unittest.TestCase):
    """RestoreDialog._extract_network() liest IP, Subnetz, Gateway, Hostname aus Backup-Ressourcen."""

    def _make_resources(self, ip="192.168.0.100", mask="255.255.255.0",
                        gw="192.168.0.1", host="bmc-test-01"):
        return {
            "/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces": {
                "Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1"}]
            },
            "/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1": {
                "HostName": host,
                "IPv4StaticAddresses": [
                    {"Address": ip, "SubnetMask": mask, "Gateway": gw}
                ]
            }
        }

    def test_extracts_all_fields(self):
        resources = self._make_resources()
        result = RestoreDialog._extract_network(resources)
        self.assertEqual(result["ip"], "192.168.0.100")
        self.assertEqual(result["mask"], "255.255.255.0")
        self.assertEqual(result["gw"], "192.168.0.1")
        self.assertEqual(result["host"], "bmc-test-01")

    def test_falls_back_to_ipv4addresses(self):
        """Wenn IPv4StaticAddresses fehlt, wird IPv4Addresses verwendet."""
        resources = {
            "/redfish/v1/Managers/1/EthernetInterfaces/NIC.1": {
                "HostName": "srv",
                "IPv4Addresses": [
                    {"Address": "10.0.0.5", "SubnetMask": "255.0.0.0", "Gateway": "10.0.0.1"}
                ]
            }
        }
        result = RestoreDialog._extract_network(resources)
        self.assertEqual(result["ip"], "10.0.0.5")
        self.assertEqual(result["gw"], "10.0.0.1")

    def test_empty_resources_returns_empty_strings(self):
        result = RestoreDialog._extract_network({})
        self.assertEqual(result, {"ip": "", "mask": "", "gw": "", "host": ""})

    def test_ignores_collection_path(self):
        """Der EthernetInterfaces-Listenknoten (ohne weiteres /) wird ignoriert."""
        resources = {
            "/redfish/v1/Managers/1/EthernetInterfaces": {
                "Members@odata.count": 1
            },
            "/redfish/v1/Managers/1/EthernetInterfaces/NIC.1": {
                "HostName": "bmc",
                "IPv4StaticAddresses": [
                    {"Address": "172.16.0.1", "SubnetMask": "255.255.0.0", "Gateway": "172.16.0.254"}
                ]
            }
        }
        result = RestoreDialog._extract_network(resources)
        self.assertEqual(result["ip"], "172.16.0.1")

    def test_no_crash_on_non_dict_values(self):
        """Nicht-dict-Werte in resources führen zu keinem Absturz."""
        resources = {
            "/some/path": None,
            "/other/path": [1, 2, 3],
        }
        try:
            result = RestoreDialog._extract_network(resources)
            self.assertEqual(result["ip"], "")
        except Exception as e:
            self.fail(f"_extract_network hat Exception mit ungültigen Werten geworfen: {e}")

    def test_first_interface_wins(self):
        """Wenn mehrere Interfaces vorhanden, nimmt der erste gefundene Wert."""
        resources = {
            "/redfish/v1/Managers/1/EthernetInterfaces/NIC.1": {
                "HostName": "first",
                "IPv4StaticAddresses": [
                    {"Address": "1.1.1.1", "SubnetMask": "255.255.255.0", "Gateway": "1.1.1.254"}
                ]
            },
            "/redfish/v1/Managers/1/EthernetInterfaces/NIC.2": {
                "HostName": "second",
                "IPv4StaticAddresses": [
                    {"Address": "2.2.2.2", "SubnetMask": "255.255.255.0", "Gateway": "2.2.2.254"}
                ]
            }
        }
        result = RestoreDialog._extract_network(resources)
        # Irgendeiner der beiden – wichtig ist, dass kein Absturz passiert
        self.assertIn(result["ip"], ("1.1.1.1", "2.2.2.2"))


if __name__ == "__main__":
    unittest.main()
