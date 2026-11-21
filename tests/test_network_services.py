"""Tests für "Netzwerk komplett" (Version 3.0.26): DNS, VLAN, IPv6 und Netzwerkport (Shared/Dedicated)
in ServicesEngine. Wie TestTlsMinVersion in test_p0_features.py: soft()/log() werden gemockt, geprüft wird
nur, welcher Pfad und welcher Body gesendet würde (kein echtes Netzwerk)."""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.redfish import ServicesEngine

VENDORS = ("dell", "hpe", "smc", "lenovo")


def make_engine():
    e = ServicesEngine.__new__(ServicesEngine)
    e.dry = False
    e.stopped = lambda: False
    e.log_lines = []
    e.log = lambda *a, **kw: e.log_lines.append(a[0] if a else "")
    e.soft_calls = []

    def fake_soft(label, ip, pw, method, path, body):
        e.soft_calls.append({"label": label, "path": path, "body": body})
        return True

    e.soft = fake_soft
    e.MGR_PATH = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1"}
    e.ETH_PATH = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1",
                  "hpe": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                  "smc": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                  "lenovo": "/redfish/v1/Managers/1/EthernetInterfaces/NIC"}
    return e


class TestDns(unittest.TestCase):
    def test_sends_static_name_servers_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = make_engine()
                e.svc_dns("10.0.0.1", "pw", v, {"servers": ["10.20.0.1", "10.20.0.2"]})
                self.assertEqual(len(e.soft_calls), 1)
                self.assertEqual(e.soft_calls[0]["path"], e.ETH_PATH[v])
                self.assertEqual(e.soft_calls[0]["body"], {"StaticNameServers": ["10.20.0.1", "10.20.0.2"]})

    def test_second_server_optional(self):
        e = make_engine()
        e.svc_dns("10.0.0.1", "pw", "dell", {"servers": ["10.20.0.1", ""]})
        self.assertEqual(e.soft_calls[0]["body"]["StaticNameServers"], ["10.20.0.1"])

    def test_no_servers_means_nothing_sent(self):
        e = make_engine()
        res = e.svc_dns("10.0.0.1", "pw", "dell", {"servers": ["", ""]})
        self.assertIsNone(res)
        self.assertEqual(e.soft_calls, [])

    def test_dell_falls_back_to_attributes_when_interface_unsupported(self):
        e = make_engine()
        calls = iter([None, True])          # erster soft()-Aufruf (EthernetInterfaces) "nicht unterstützt"

        def fake_soft(label, ip, pw, method, path, body):
            e.soft_calls.append({"label": label, "path": path, "body": body})
            return next(calls)

        e.soft = fake_soft
        e.mgr_attributes = lambda ip, pw, v: {"IPv4.1.DNS1": "0.0.0.0", "IPv4.1.DNS2": "0.0.0.0", "IPv4.1.DNSFromDHCP": "Enabled"}
        res = e.svc_dns("10.0.0.1", "pw", "dell", {"servers": ["10.20.0.1"]})
        self.assertTrue(res)
        self.assertEqual(len(e.soft_calls), 2)
        self.assertIn("/Attributes", e.soft_calls[1]["path"])
        self.assertEqual(e.soft_calls[1]["body"]["Attributes"],
                         {"IPv4.1.DNS1": "10.20.0.1", "IPv4.1.DNS2": "0.0.0.0", "IPv4.1.DNSFromDHCP": "Disabled"})


class TestVlan(unittest.TestCase):
    def test_enables_vlan_with_id(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = make_engine()
                e.svc_vlan("10.0.0.1", "pw", v, {"id": 42})
                self.assertEqual(e.soft_calls[0]["body"], {"VLAN": {"VLANEnable": True, "VLANId": 42}})

    def test_id_zero_disables_vlan(self):
        e = make_engine()
        e.svc_vlan("10.0.0.1", "pw", "dell", {"id": 0})
        self.assertEqual(e.soft_calls[0]["body"], {"VLAN": {"VLANEnable": False}})

    def test_missing_id_means_disable(self):
        e = make_engine()
        e.svc_vlan("10.0.0.1", "pw", "dell", {})
        self.assertEqual(e.soft_calls[0]["body"], {"VLAN": {"VLANEnable": False}})


class TestIpv6(unittest.TestCase):
    def test_disabled_clears_static_addresses(self):
        e = make_engine()
        e.svc_ipv6("10.0.0.1", "pw", "dell", {"mode": "disabled"})
        body = e.soft_calls[0]["body"]
        self.assertFalse(body["IPv6AutoConfigEnabled"])
        self.assertEqual(body["DHCPv6"]["OperatingMode"], "Disabled")
        self.assertEqual(body["IPv6StaticAddresses"], [])

    def test_slaac_enables_autoconfig(self):
        e = make_engine()
        e.svc_ipv6("10.0.0.1", "pw", "dell", {"mode": "slaac"})
        body = e.soft_calls[0]["body"]
        self.assertTrue(body["IPv6AutoConfigEnabled"])
        self.assertEqual(body["DHCPv6"]["OperatingMode"], "Disabled")

    def test_dhcpv6_sets_stateful(self):
        e = make_engine()
        e.svc_ipv6("10.0.0.1", "pw", "dell", {"mode": "dhcpv6"})
        body = e.soft_calls[0]["body"]
        self.assertFalse(body["IPv6AutoConfigEnabled"])
        self.assertEqual(body["DHCPv6"]["OperatingMode"], "Stateful")

    def test_static_sets_address_and_prefix(self):
        e = make_engine()
        e.svc_ipv6("10.0.0.1", "pw", "dell",
                   {"mode": "static", "address": "fd00::10", "prefix": 48, "gateway": "fd00::1"})
        body = e.soft_calls[0]["body"]
        self.assertEqual(body["IPv6StaticAddresses"], [{"Address": "fd00::10", "PrefixLength": 48}])
        self.assertEqual(body["IPv6DefaultGateway"], "fd00::1")

    def test_static_without_gateway_omits_it(self):
        e = make_engine()
        e.svc_ipv6("10.0.0.1", "pw", "dell", {"mode": "static", "address": "fd00::10"})
        self.assertNotIn("IPv6DefaultGateway", e.soft_calls[0]["body"])

    def test_static_without_address_sends_nothing(self):
        e = make_engine()
        res = e.svc_ipv6("10.0.0.1", "pw", "dell", {"mode": "static", "address": ""})
        self.assertIsNone(res)
        self.assertEqual(e.soft_calls, [])

    def test_default_prefix_is_64(self):
        e = make_engine()
        e.svc_ipv6("10.0.0.1", "pw", "dell", {"mode": "static", "address": "fd00::10"})
        self.assertEqual(e.soft_calls[0]["body"]["IPv6StaticAddresses"][0]["PrefixLength"], 64)


class TestNicMode(unittest.TestCase):
    def test_dell_dedicated(self):
        e = make_engine()
        e.svc_nicmode("10.0.0.1", "pw", "dell", {"mode": "dedicated"})
        self.assertEqual(e.soft_calls[0]["body"]["Attributes"]["NIC.1.NicSelection"], "Dedicated")

    def test_dell_shared(self):
        e = make_engine()
        e.svc_nicmode("10.0.0.1", "pw", "dell", {"mode": "shared"})
        self.assertEqual(e.soft_calls[0]["body"]["Attributes"]["NIC.1.NicSelection"], "LOM1")

    def test_other_vendors_skipped_with_hint(self):
        for v in ("hpe", "smc", "lenovo"):
            with self.subTest(vendor=v):
                e = make_engine()
                res = e.svc_nicmode("10.0.0.1", "pw", v, {"mode": "dedicated"})
                self.assertIsNone(res)
                self.assertEqual(e.soft_calls, [])
                self.assertTrue(e.log_lines)


if __name__ == "__main__":
    unittest.main()
