"""Selbst geschriebene Minimal-Redfish-Bäume (tests/_trees.py, kein Herstellermaterial) gegen den
echten RedfishClient/ServicesEngine-Code laufen lassen – statt nur mit unittest.mock vorgetäuschten Antworten.

Prüft mit statischen Ressourcenbäumen je Hersteller (Dell iDRAC9, HPE iLO5,
Lenovo XCC2, Supermicro X12):
1. vendor() erkennt jeden Hersteller korrekt allein am ServiceRoot (Vendor-Feld / Oem-Schlüssel).
2. svc_hard() (Härtung: IPMI/SSDP/SNMP aus, Kontosperre, Session-Timeout, TLS-Mindestversion) funktioniert
   gegen alle vier Bäume und überspringt die TLS-Mindestversion dort sauber (None statt Fehler), wo der Baum
   – wie auf echter Hardware außer Dell – keine Attributes-Ressource hat (siehe luecken-und-roadmap.md,
   "TLS-Mindestversion außer Dell ... kein echter Test").
3. svc_dns()/svc_vlan()/svc_ipv6() (Version 3.0.26, "Netzwerk komplett") patchen wirklich die
   EthernetInterfaces-Ressource aller vier Bäume – nicht nur gemockte Python-Aufrufe wie in
   test_network_services.py.
"""
import unittest
from tests._support import qapp, need

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from tests._dmtf_mock import DmtfMockServer
from bmc_steward.redfish import RedfishClient, ServicesEngine, UsersEngine

VENDORS = ("dell", "hpe", "lenovo", "smc")
MGR = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1", "hpe": "/redfish/v1/Managers/1",
       "lenovo": "/redfish/v1/Managers/1", "smc": "/redfish/v1/Managers/1"}
ETH = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1",
       "hpe": "/redfish/v1/Managers/1/EthernetInterfaces/1",
       "lenovo": "/redfish/v1/Managers/1/EthernetInterfaces/NIC",
       "smc": "/redfish/v1/Managers/1/EthernetInterfaces/1"}
ACCOUNT1 = "/redfish/v1/AccountService/Accounts/1"
DEFAULT_USER = {"dell": "root", "hpe": "Administrator", "lenovo": "USERID", "smc": "ADMIN"}


@need("openssl")
class TestVendorDetectionAgainstTrees(unittest.TestCase):
    """RedfishClient.vendor() allein anhand des ServiceRoot-Baums je Hersteller."""

    @classmethod
    def setUpClass(cls):
        cls.servers = {v: DmtfMockServer(v).start() for v in VENDORS}

    @classmethod
    def tearDownClass(cls):
        for srv in cls.servers.values():
            srv.stop()

    def test_each_tree_is_recognized_as_its_own_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                c = RedfishClient(dry=False)
                self.assertEqual(c.vendor(self.servers[v].addr), v)


@need("openssl")
class TestHardeningAgainstTrees(unittest.TestCase):
    """ServicesEngine.svc_hard() gegen die vier Hersteller-Bäume: muss überall funktionieren, und die
    TLS-Mindestversion muss dort, wo es keine Attributes-Ressource gibt, sauber übersprungen werden statt
    als Fehler zu zählen."""

    def setUp(self):
        self.servers = {v: DmtfMockServer(v).start() for v in VENDORS}
        self.cfg = {"ipmi": True, "ssdp": True, "snmp": True, "lockout": 5, "lockout_min": 10,
                    "timeout_min": 30, "tls_min": "tls13"}

    def tearDown(self):
        for srv in self.servers.values():
            srv.stop()

    def engine(self):
        e = ServicesEngine([], "", {}, dry=False)
        e.cur_user = "admin"
        return e

    def test_hardening_succeeds_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                self.assertTrue(e.svc_hard(self.servers[v].addr, "pw", v, self.cfg))

    def test_network_protocol_disabled_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                e.svc_hard(self.servers[v].addr, "pw", v, self.cfg)
                np_ = self.servers[v].resource(MGR[v] + "/NetworkProtocol")
                self.assertFalse(np_["IPMI"]["ProtocolEnabled"])
                self.assertFalse(np_["SSDP"]["ProtocolEnabled"])
                self.assertFalse(np_["SNMP"]["ProtocolEnabled"])

    def test_account_lockout_and_session_timeout_set_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                e.svc_hard(self.servers[v].addr, "pw", v, self.cfg)
                acc = self.servers[v].resource("/redfish/v1/AccountService")
                self.assertEqual(acc["AccountLockoutThreshold"], 5)
                self.assertEqual(acc["AccountLockoutDuration"], 600)
                ses = self.servers[v].resource("/redfish/v1/SessionService")
                self.assertEqual(ses["SessionTimeout"], 1800)

    def test_tls_min_applied_on_dell_attributes(self):
        e = self.engine()
        e.svc_hard(self.servers["dell"].addr, "pw", "dell", self.cfg)
        attrs = self.servers["dell"].resource(MGR["dell"] + "/Attributes")
        self.assertEqual(attrs["Attributes"]["Security.1.TLSProtocol"], "TLS 1.3 and Higher")

    def test_tls_min_gracefully_skipped_where_no_attributes_resource_exists(self):
        # HPE/Lenovo/Supermicro-Bäume haben (wie auf echter Hardware, laut Roadmap noch ungetestet) keine
        # Attributes-Ressource unter dem Manager-Pfad -> PATCH dorthin ist 404 -> soft() = None, kein Fehler.
        for v in ("hpe", "lenovo", "smc"):
            with self.subTest(vendor=v):
                self.assertNotIn(MGR[v] + "/Attributes", self.servers[v].tree)
                e = self.engine()
                # Erfolg insgesamt (True), obwohl die TLS-Teilaktion übersprungen wird
                self.assertTrue(e.svc_hard(self.servers[v].addr, "pw", v, self.cfg))

    def test_only_requested_protocols_are_touched(self):
        e = self.engine()
        cfg = {"ipmi": True, "ssdp": False, "snmp": False}
        e.svc_hard(self.servers["dell"].addr, "pw", "dell", cfg)
        np_ = self.servers["dell"].resource(MGR["dell"] + "/NetworkProtocol")
        self.assertFalse(np_["IPMI"]["ProtocolEnabled"])
        self.assertTrue(np_["SSDP"]["ProtocolEnabled"])      # unverändert, da nicht angefordert
        self.assertTrue(np_["SNMP"]["ProtocolEnabled"])


@need("openssl")
class TestNetworkAgainstTrees(unittest.TestCase):
    """svc_dns()/svc_vlan()/svc_ipv6() gegen die vier EthernetInterfaces-Bäume (Version 3.0.26)."""

    def setUp(self):
        self.servers = {v: DmtfMockServer(v).start() for v in VENDORS}

    def tearDown(self):
        for srv in self.servers.values():
            srv.stop()

    def engine(self):
        e = ServicesEngine([], "", {}, dry=False)
        e.cur_user = "admin"
        return e

    def test_dns_written_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                self.assertTrue(e.svc_dns(self.servers[v].addr, "pw", v, {"servers": ["10.20.0.1", "10.20.0.2"]}))
                eth = self.servers[v].resource(ETH[v])
                self.assertEqual(eth["StaticNameServers"], ["10.20.0.1", "10.20.0.2"])

    def test_vlan_written_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                self.assertTrue(e.svc_vlan(self.servers[v].addr, "pw", v, {"id": 100}))
                eth = self.servers[v].resource(ETH[v])
                self.assertTrue(eth["VLAN"]["VLANEnable"])
                self.assertEqual(eth["VLAN"]["VLANId"], 100)

    def test_vlan_disabled_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                self.assertTrue(e.svc_vlan(self.servers[v].addr, "pw", v, {"id": 0}))
                self.assertFalse(self.servers[v].resource(ETH[v])["VLAN"]["VLANEnable"])

    def test_ipv6_static_written_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                self.assertTrue(e.svc_ipv6(self.servers[v].addr, "pw", v,
                                           {"mode": "static", "address": "fd00::10", "prefix": 64,
                                            "gateway": "fd00::1"}))
                eth = self.servers[v].resource(ETH[v])
                self.assertEqual(eth["IPv6StaticAddresses"], [{"Address": "fd00::10", "PrefixLength": 64}])
                self.assertEqual(eth["IPv6DefaultGateway"], "fd00::1")
                self.assertFalse(eth["IPv6AutoConfigEnabled"])

    def test_dns_and_vlan_do_not_interfere(self):
        # Zwei verschiedene PATCHes auf dieselbe Ressource dürfen sich nicht gegenseitig überschreiben
        # (Merge, nicht Replace) - genau das, was ein echter Redfish-PATCH tun soll.
        e = self.engine()
        e.svc_dns(self.servers["dell"].addr, "pw", "dell", {"servers": ["10.20.0.1"]})
        e.svc_vlan(self.servers["dell"].addr, "pw", "dell", {"id": 7})
        eth = self.servers["dell"].resource(ETH["dell"])
        self.assertEqual(eth["StaticNameServers"], ["10.20.0.1"])
        self.assertEqual(eth["VLAN"], {"VLANEnable": True, "VLANId": 7})


@need("openssl")
class TestUsersAgainstTrees(unittest.TestCase):
    """UsersEngine (Version 3.0.26, Benutzerverwaltung) gegen die vier Konten-Bäume: Liste lesen,
    anlegen (POST bei HPE/Supermicro/Lenovo, PATCH eines freien Slots bei Dell), löschen, Rolle und
    Passwort setzen, SSH-Key anlegen/löschen (DMTF-Standard ManagerAccount „Keys“) - jeweils als echter
    HTTP-Roundtrip gegen tests/_dmtf_mock.py, nicht nur gemockte Python-Aufrufe wie in test_users.py."""

    def setUp(self):
        self.servers = {v: DmtfMockServer(v).start() for v in VENDORS}

    def tearDown(self):
        for srv in self.servers.values():
            srv.stop()

    @staticmethod
    def engine():
        return UsersEngine(dry=False)

    def test_list_accounts_finds_the_default_admin_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                accounts = e.list_accounts(self.servers[v].addr, DEFAULT_USER[v], "pw", v)
                self.assertEqual([a["username"] for a in accounts], [DEFAULT_USER[v]])
                self.assertTrue(accounts[0]["enabled"])

    def test_create_account_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                ok = e.create_account(self.servers[v].addr, DEFAULT_USER[v], "pw", v, "neu", "Geheim123!",
                                      "Operator")
                self.assertTrue(ok)
                accounts = e.list_accounts(self.servers[v].addr, DEFAULT_USER[v], "pw", v)
                self.assertIn("neu", [a["username"] for a in accounts])
                new = next(a for a in accounts if a["username"] == "neu")
                self.assertEqual(new["role"], "Operator")

    def test_create_account_dell_reuses_the_empty_slot_not_a_new_resource(self):
        e = self.engine()
        before = len(self.servers["dell"].resource("/redfish/v1/AccountService/Accounts")["Members"])
        self.assertTrue(e.create_account(self.servers["dell"].addr, "root", "pw", "dell", "neu",
                                         "Geheim123!", "Administrator"))
        after = self.servers["dell"].resource("/redfish/v1/AccountService/Accounts")["Members"]
        self.assertEqual(len(after), before)        # kein neuer Slot, der bestehende Slot 2 wurde befüllt
        slot2 = self.servers["dell"].resource("/redfish/v1/AccountService/Accounts/2")
        self.assertEqual(slot2["UserName"], "neu")

    def test_delete_account_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                e.create_account(self.servers[v].addr, DEFAULT_USER[v], "pw", v, "weg", "Geheim123!")
                before = e.list_accounts(self.servers[v].addr, DEFAULT_USER[v], "pw", v)
                target = next(a for a in before if a["username"] == "weg")
                self.assertTrue(e.delete_account(self.servers[v].addr, DEFAULT_USER[v], "pw", v, target["path"]))
                after = e.list_accounts(self.servers[v].addr, DEFAULT_USER[v], "pw", v)
                self.assertNotIn("weg", [a["username"] for a in after])

    def test_delete_account_dell_clears_the_slot_but_keeps_it(self):
        e = self.engine()
        self.assertTrue(e.delete_account(self.servers["dell"].addr, "root", "pw", "dell", ACCOUNT1))
        slot1 = self.servers["dell"].resource(ACCOUNT1)
        self.assertEqual(slot1["UserName"], "")
        self.assertFalse(slot1["Enabled"])

    def test_set_role_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                self.assertTrue(e.set_role(self.servers[v].addr, DEFAULT_USER[v], "pw", v, ACCOUNT1, "ReadOnly"))
                self.assertEqual(self.servers[v].resource(ACCOUNT1)["RoleId"], "ReadOnly")

    def test_rotate_password_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                self.assertTrue(e.rotate_password(self.servers[v].addr, DEFAULT_USER[v], "pw", v, ACCOUNT1,
                                                  "NeuesPw123!"))
                self.assertEqual(self.servers[v].resource(ACCOUNT1)["Password"], "NeuesPw123!")

    def test_ssh_key_add_list_and_delete_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = self.engine()
                keys_path = ACCOUNT1 + "/Keys"
                self.assertEqual(e.list_ssh_keys(self.servers[v].addr, DEFAULT_USER[v], "pw", keys_path), [])
                ok = e.add_ssh_key(self.servers[v].addr, DEFAULT_USER[v], "pw", keys_path,
                                   "ssh-ed25519 AAAAneu kommentar")
                self.assertTrue(ok)
                keys = e.list_ssh_keys(self.servers[v].addr, DEFAULT_USER[v], "pw", keys_path)
                self.assertEqual(len(keys), 1)
                self.assertEqual(keys[0]["key"], "ssh-ed25519 AAAAneu kommentar")
                self.assertTrue(e.delete_ssh_key(self.servers[v].addr, DEFAULT_USER[v], "pw", keys[0]["path"]))
                self.assertEqual(e.list_ssh_keys(self.servers[v].addr, DEFAULT_USER[v], "pw", keys_path), [])

    def test_rotation_run_rotates_the_logged_in_account_on_every_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = UsersEngine(rows=[(self.servers[v].addr, "pw")], cur_user=DEFAULT_USER[v], dry=False,
                                vendor=v)
                good, new_pw = e.run()
                self.assertTrue(good)
                self.assertEqual(list(new_pw), [self.servers[v].addr])
                self.assertEqual(self.servers[v].resource(ACCOUNT1)["Password"], new_pw[self.servers[v].addr])


@need("openssl")
class TestPortReuseBetweenTestServers(unittest.TestCase):
    """Zufällige Testports wiederholen sich in einem langen Lauf. Ein frischer Testserver darf nicht an der
    Zertifikats-Merkung eines früheren Servers unter derselben Adresse scheitern (in der vollen Suite schlug
    so test_rotate_password_on_every_vendor zufällig fehl). Nachgestellt: Beim Binden des Ports liegt für
    genau diese Adresse schon ein fremder Fingerabdruck vor."""

    def test_fresh_mock_server_ignores_pins_of_an_earlier_server_on_the_same_port(self):
        from unittest.mock import patch
        import tests._dmtf_mock as mock
        from bmc_steward import trust

        class ReusedPort(mock.ThreadingHTTPServer):
            def server_bind(self):
                super().server_bind()
                addr = "127.0.0.1:%d" % self.server_address[1]
                trust.store().check("frueherer-testserver-" + addr, "AA" * 32, addr)

        with patch.object(mock, "ThreadingHTTPServer", ReusedPort):
            srv = DmtfMockServer("dell").start()
        self.addCleanup(srv.stop)
        self.assertEqual(trust.store().for_addr(srv.addr), {})
        self.assertTrue(UsersEngine(dry=False).rotate_password(srv.addr, "root", "pw", "dell", ACCOUNT1,
                                                               "NeuesPw123!"))
        self.assertEqual(srv.resource(ACCOUNT1)["Password"], "NeuesPw123!")


if __name__ == "__main__":
    unittest.main()
