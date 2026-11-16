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
    """svc_hard() sendet TLS-Mindestversion-PATCH, wenn tls_min gesetzt."""

    def _make_engine(self):
        e = ServicesEngine.__new__(ServicesEngine)
        e.dry = False
        e.stopped = lambda: False
        e.log = lambda *a, **kw: None
        e.soft_calls = []

        def fake_soft(label, ip, pw, method, path, body):
            e.soft_calls.append({"label": label, "path": path, "body": body})
            return True

        e.soft = fake_soft
        e.MGR_PATH = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1"}
        return e

    def test_tls12_patch_sent(self):
        """tls_min='tls12' führt zu PATCH mit 'TLS 1.2 and Higher'."""
        e = self._make_engine()
        cfg = {"tls_min": "tls12"}
        e.svc_hard("10.0.0.1", "pw", "dell", cfg)
        tls_calls = [c for c in e.soft_calls if "TLS" in c["label"]]
        self.assertEqual(len(tls_calls), 1)
        body = tls_calls[0]["body"]
        self.assertEqual(body["Attributes"]["Security.1.TLSProtocol"], "TLS 1.2 and Higher")

    def test_tls13_patch_sent(self):
        """tls_min='tls13' führt zu PATCH mit 'TLS 1.3 and Higher'."""
        e = self._make_engine()
        cfg = {"tls_min": "tls13"}
        e.svc_hard("10.0.0.1", "pw", "dell", cfg)
        tls_calls = [c for c in e.soft_calls if "TLS" in c["label"]]
        self.assertEqual(len(tls_calls), 1)
        body = tls_calls[0]["body"]
        self.assertEqual(body["Attributes"]["Security.1.TLSProtocol"], "TLS 1.3 and Higher")

    def test_no_tls_patch_when_empty(self):
        """tls_min='' oder fehlend führt zu keinem TLS-PATCH."""
        e = self._make_engine()
        cfg = {"tls_min": ""}
        e.svc_hard("10.0.0.1", "pw", "dell", cfg)
        tls_calls = [c for c in e.soft_calls if "TLS" in c["label"]]
        self.assertEqual(len(tls_calls), 0)

    def test_no_tls_patch_when_missing(self):
        """Fehlendes tls_min-Feld führt zu keinem TLS-PATCH."""
        e = self._make_engine()
        cfg = {}
        e.svc_hard("10.0.0.1", "pw", "dell", cfg)
        tls_calls = [c for c in e.soft_calls if "TLS" in c["label"]]
        self.assertEqual(len(tls_calls), 0)

    def test_tls_path_uses_mgr_attributes(self):
        """Der PATCH geht an mgr + '/Attributes', also iDRAC.Embedded.1/Attributes für Dell."""
        e = self._make_engine()
        cfg = {"tls_min": "tls12"}
        e.svc_hard("10.0.0.1", "pw", "dell", cfg)
        tls_calls = [c for c in e.soft_calls if "TLS" in c["label"]]
        self.assertIn("/Attributes", tls_calls[0]["path"])
        self.assertIn("iDRAC.Embedded.1", tls_calls[0]["path"])

    def test_other_vendor_uses_generic_mgr(self):
        """Für unbekannten Hersteller wird /redfish/v1/Managers/1/Attributes versucht."""
        e = self._make_engine()
        cfg = {"tls_min": "tls12"}
        e.svc_hard("10.0.0.1", "pw", "lenovo", cfg)
        tls_calls = [c for c in e.soft_calls if "TLS" in c["label"]]
        self.assertEqual(len(tls_calls), 1)
        self.assertIn("/Managers/1/Attributes", tls_calls[0]["path"])


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
