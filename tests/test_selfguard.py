"""Eigenschutz: BMC-Steward darf nie den Rechner neu starten oder flashen, auf dem es selbst läuft."""
import tempfile
import unittest
from pathlib import Path
from tests._support import qapp

qapp()
from bmc_steward.selfguard import SelfGuard, norm, usable
from bmc_steward.redfish import RedfishClient


def guard(entries=(), own=(), virt=""):
    g = SelfGuard(Path(tempfile.mkdtemp()) / "g.json", probe=False)
    g.entries, g.own, g.virt = list(entries), list(own), virt
    return g


DELL = {"Model": "PowerEdge R6625", "SerialNumber": "CN7016312345", "SKU": "ABC1234",
        "UUID": "4c4c4544-0042-3510-8048-b2c04f593132",
        "Oem": {"Dell": {"DellSystem": {"ChassisServiceTag": "ABC1234", "NodeID": "ABC1234"}}}}


class TestMatch(unittest.TestCase):
    def test_service_tag_uuid_serial(self):
        for e in ("abc1234", "4C4C4544-0042-3510-8048-B2C04F593132", "CN7016312345", "4c4c4544 0042 3510"
                  "8048b2c04f593132"):
            self.assertTrue(guard([e]).match(DELL), e)

    def test_other_server_passes(self):
        self.assertIsNone(guard(["XYZ9876"]).match(DELL))

    def test_placeholders_never_match(self):
        sm = {"SerialNumber": "To be filled by O.E.M.", "UUID": "03000200-0400-0500-0006-000700080009"}
        self.assertIsNone(guard(["To be filled by O.E.M.", "03000200-0400-0500-0006-000700080009"]).match(sm))
        self.assertFalse(usable("0123456789"))
        self.assertEqual(norm(" ab-c:d "), "ABCD")

    def test_own_identity_matches(self):
        self.assertTrue(guard(own=["4c4c4544-0042-3510-8048-b2c04f593132"]).match(DELL))

    def test_ip_entries(self):
        g = guard(["10.0.0.5", "127.0.0.1:8443"])
        self.assertEqual(g.match_ip("10.0.0.5"), "10.0.0.5")
        self.assertEqual(g.match_ip("10.0.0.5:443"), "10.0.0.5")
        self.assertEqual(g.match_ip("127.0.0.1:8443"), "127.0.0.1:8443")
        self.assertIsNone(g.match_ip("10.0.0.6"))
        self.assertIsNone(guard(["ABC1234"]).match_ip("10.0.0.5"))

    def test_save_load(self):
        g = guard()
        g.save(["  ABC1234 ", "", "10.0.0.5"])
        g2 = SelfGuard(g.path, probe=False)
        self.assertEqual(g2.entries, ["ABC1234", "10.0.0.5"])

    def test_unprotected_vm(self):
        self.assertTrue(guard(virt="VMware").unprotected_vm)
        self.assertFalse(guard(["X1234"], virt="VMware").unprotected_vm)


class FakeClient(RedfishClient):
    def __init__(self, dry, doc, reachable=True):
        super().__init__(dry=dry, log=lambda line, kind="": self.lines.append((kind, line)))
        self.lines, self.doc, self.calls = [], doc, []
        if reachable:
            self.tls["10.0.0.9"] = "modern"

    def api(self, ip, user, pw, method, path, data=None, timeout=30):
        self.calls.append((method, path, self.dry))
        self.body = self.doc
        return 200 if self.doc is not None else 401


class TestEngineGuard(unittest.TestCase):
    def test_blocks_own_host_even_in_dryrun(self):
        c = FakeClient(True, DELL)
        self.assertFalse(c.self_guard("10.0.0.9", "root", "x", "dell", guard(["ABC1234"])))
        self.assertEqual(c.calls, [("GET", "/redfish/v1/Systems/System.Embedded.1", False)])
        self.assertTrue(c.dry)                          # Trockenlauf-Schalter wieder hergestellt

    def test_blocks_by_ip_without_request(self):
        c = FakeClient(False, DELL)
        self.assertFalse(c.self_guard("10.0.0.9", "root", "x", "dell", guard(["10.0.0.9"])))
        self.assertEqual(c.calls, [])

    def test_live_unreadable_is_skipped(self):
        c = FakeClient(False, None)
        self.assertFalse(c.self_guard("10.0.0.9", "root", "x", "dell", guard(["ABC9999"])))

    def test_dry_unreadable_continues(self):
        c = FakeClient(True, None)
        self.assertTrue(c.self_guard("10.0.0.9", "root", "x", "dell", guard(["ABC9999"])))

    def test_dry_unreachable_no_request(self):
        c = FakeClient(True, DELL, reachable=False)
        self.assertTrue(c.self_guard("10.0.0.9", "root", "x", "dell", guard(["ABC1234"])))
        self.assertEqual(c.calls, [])

    def test_other_server_allowed(self):
        c = FakeClient(False, DELL)
        self.assertTrue(c.self_guard("10.0.0.9", "root", "x", "dell", guard(["ZZZ0000"])))

    def test_vm_unprotected_live_unreadable_skips(self):
        c = FakeClient(False, None)
        self.assertFalse(c.self_guard("10.0.0.9", "root", "x", "dell", guard(virt="VMware")))

    def test_nothing_armed_no_request(self):
        c = FakeClient(False, DELL)
        self.assertTrue(c.self_guard("10.0.0.9", "root", "x", "dell", guard()))
        self.assertEqual(c.calls, [])


if __name__ == "__main__":
    unittest.main()
