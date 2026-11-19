"""Aufnahme- und Vergleichsmodus (capture.py): nur lesend, Schwärzung, Mockup-Format, Fähigkeitsbericht, Vergleich.

Die Aufnahme läuft gegen die vier DMTF-Mockups (tests/dmtf_mockups) über den echten RedfishClient; die
Schwärzung und der Vergleich zusätzlich mit kleinen, selbst gebauten Bäumen, in denen jeder persönliche Wert
bekannt ist."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests._support import qapp, need

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from tests._dmtf_mock import DmtfMockServer
from bmc_steward import capture as C

VENDORS = ("dell", "hpe", "lenovo", "smc")
ROOT = "/redfish/v1"


def sample():
    """Kleiner Baum mit bekannten persönlichen Werten."""
    return {
        "meta": {"vendor": "dell", "vendor_name": "Dell (iDRAC)", "host": "10.20.30.40:443", "redacted": False},
        "allow": {ROOT + "/Systems/S1": "GET, PATCH"},
        "errors": {}, "skipped": {},
        "tree": {
            ROOT: {"@odata.id": ROOT, "UUID": "11111111-2222-3333-4444-555555555555", "RedfishVersion": "1.17.0"},
            ROOT + "/Systems/S1": {
                "@odata.id": ROOT + "/Systems/S1", "SerialNumber": "CN7016301XYZ", "SKU": "ABC1234",
                "HostName": "rack7-idrac", "AssetTag": "ASSET-77", "BiosVersion": "7.00.00.171",
                "Description": "Server mit 10:30:45 Uhr", "Model": "PowerEdge R650"},
            ROOT + "/Managers/M1/EthernetInterfaces/NIC.1": {
                "@odata.id": ROOT + "/Managers/M1/EthernetInterfaces/NIC.1",
                "MACAddress": "AA:BB:CC:DD:EE:01", "PermanentMACAddress": "aa:bb:cc:dd:ee:01", "FQDN": "rack7-idrac.corp.example.com",
                "IPv4Addresses": [{"Address": "10.20.30.40", "SubnetMask": "255.255.255.0", "Gateway": "10.20.30.1"}],
                "StaticNameServers": ["10.20.30.2", "10.20.30.3"], "FirmwareVersion": "1.2.3.4",
                "IPv6Addresses": [{"Address": "fd00:1234::40"}]},
            ROOT + "/AccountService/Accounts/2": {"UserName": "max.mustermann", "Password": None},
            ROOT + "/AccountService/Accounts/1": {"UserName": "root"},
            ROOT + "/Managers/M1/Certificates/1": {
                "CertificateString": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
                "Subject": {"CommonName": "rack7-idrac.corp.example.com", "Organization": "Beispiel GmbH"}},
            ROOT + "/Managers/M1/NetworkProtocol": {"NTP": {"NTPServers": ["ntp.corp.example.com", "10.20.30.5"]},
                                                      "Description": "Zugriff über https://10.20.30.40/redfish"},
        }}


class TestRedaction(unittest.TestCase):
    SECRETS = ["CN7016301XYZ", "ABC1234", "rack7-idrac", "ASSET-77", "AA:BB:CC:DD:EE:01", "10.20.30.40", "10.20.30.1",
               "10.20.30.2", "10.20.30.3", "10.20.30.5", "fd00:1234::40", "max.mustermann",
               "11111111-2222-3333-4444-555555555555", "corp.example.com", "Beispiel GmbH", "BEGIN CERTIFICATE"]

    def redacted(self):
        cap = sample()
        rep = C.redact(cap)
        return cap, rep

    def test_no_secret_value_remains_anywhere(self):
        cap, _ = self.redacted()
        dump = json.dumps(cap, ensure_ascii=False).lower()
        for s in self.SECRETS:
            with self.subTest(secret=s):
                self.assertNotIn(s.lower(), dump)

    def test_report_counts_kinds_without_values(self):
        _, rep = self.redacted()
        for kind in ("SERIAL", "ASSET", "UUID", "HOST", "MAC", "IPV4", "IPV6", "USER"):
            self.assertGreater(rep.get(kind, 0), 0, kind)
        self.assertNotIn("CN7016301XYZ", json.dumps(rep))

    def test_structure_and_harmless_values_survive(self):
        cap, _ = self.redacted()
        nic = cap["tree"][ROOT + "/Managers/M1/EthernetInterfaces/NIC.1"]
        self.assertEqual(nic["IPv4Addresses"][0]["SubnetMask"], "255.255.255.0")      # Netzmaske bleibt
        self.assertEqual(nic["FirmwareVersion"], "1.2.3.4")                           # sieht wie eine IP aus, ist keine
        self.assertEqual(cap["tree"][ROOT + "/Systems/S1"]["BiosVersion"], "7.00.00.171")
        self.assertEqual(cap["tree"][ROOT + "/Systems/S1"]["Model"], "PowerEdge R650")
        self.assertIn("10:30:45", cap["tree"][ROOT + "/Systems/S1"]["Description"])    # Uhrzeit ist kein IPv6
        self.assertEqual(cap["tree"][ROOT + "/AccountService/Accounts/1"]["UserName"], "root")   # Standardkonto bleibt
        self.assertIsNone(cap["tree"][ROOT + "/AccountService/Accounts/2"]["Password"])
        self.assertEqual(cap["allow"][ROOT + "/Systems/S1"], "GET, PATCH")

    def test_pseudonyms_are_format_valid_and_consistent(self):
        cap, _ = self.redacted()
        nic = cap["tree"][ROOT + "/Managers/M1/EthernetInterfaces/NIC.1"]
        self.assertRegex(nic["MACAddress"], r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
        self.assertEqual(nic["MACAddress"], nic["PermanentMACAddress"])               # derselbe Wert, derselbe Ersatz
        self.assertRegex(nic["IPv4Addresses"][0]["Address"], r"^(192\.0\.2|198\.51\.100|203\.0\.113)\.\d+$")
        self.assertEqual(cap["meta"]["host"], "REDACTED")
        self.assertTrue(cap["meta"]["redacted"])

    def test_same_device_twice_gives_identical_result(self):
        a, b = sample(), sample()
        b["meta"]["host"] = "10.20.30.40:443"
        C.redact(a)
        C.redact(b)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_certificates_and_subjects_are_blanked(self):
        cap, _ = self.redacted()
        cert = cap["tree"][ROOT + "/Managers/M1/Certificates/1"]
        self.assertEqual(cert["CertificateString"], "REDACTED")
        self.assertTrue(all(v == "REDACTED" for v in cert["Subject"].values()))

    def test_secret_in_a_path_is_replaced_too(self):
        cap = sample()
        cap["tree"][ROOT + "/Chassis/CN7016301XYZ"] = {"Name": "Gehäuse"}
        cap["allow"][ROOT + "/Chassis/CN7016301XYZ"] = "GET"
        C.redact(cap)
        self.assertFalse(any("CN7016301XYZ" in p for p in list(cap["tree"]) + list(cap["allow"])))

    def test_short_values_are_not_swept_as_substrings(self):
        cap = {"meta": {"vendor": "dell", "host": "10.0.0.9"}, "allow": {}, "errors": {}, "skipped": {},
               "tree": {ROOT: {"HostName": "ab", "Name": "Cabinet about"}}}
        C.redact(cap)
        self.assertEqual(cap["tree"][ROOT]["Name"], "Cabinet about")


@need("openssl")
class TestCaptureAgainstMockups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def capture(self, vendor, **kw):
        srv = DmtfMockServer(vendor).start()
        self.addCleanup(srv.stop)
        cap, rep, err = C.run_capture(srv.addr, "", "pw", Path(self.tmp.name) / vendor, vendor=vendor, **kw)
        return srv, cap, rep, err

    def test_capture_reads_the_whole_tree_with_get_only(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                srv, cap, rep, err = self.capture(v)
                self.assertEqual(err, "")
                self.assertEqual(set(cap["tree"]), set(DmtfMockServer(v).tree) & set(cap["tree"]))
                self.assertGreaterEqual(len(cap["tree"]), 5)
                # einzige Ausnahme ist die Anmeldung selbst (Session anlegen/abmelden), nie eine Konfiguration
                writes = [(m, p) for m, p in srv.requests if m != "GET"]
                self.assertTrue(all("/SessionService/Sessions" in p for _, p in writes), writes)

    def test_allow_header_is_recorded(self):
        srv, cap, _, _ = self.capture("dell")
        self.assertIn("PATCH", cap["allow"][ROOT + "/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1"])
        self.assertIn("POST", cap["allow"][ROOT + "/AccountService/Accounts"])

    def test_saved_folder_is_a_dmtf_mockup_that_can_be_loaded_and_served_again(self):
        srv, cap, _, _ = self.capture("hpe", do_redact=False)
        folder = Path(self.tmp.name) / "hpe"
        self.assertTrue((folder / "redfish" / "v1" / "index.json").is_file())
        self.assertTrue((folder / C.META_FILE).is_file())
        loaded = C.load_capture(folder)
        self.assertEqual(loaded["tree"], cap["tree"])
        self.assertEqual(loaded["allow"], cap["allow"])
        # Rundreise: der gespeicherte Ordner lässt sich wieder ausliefern und erneut aufnehmen -> kein Unterschied
        again = DmtfMockServer("hpe")
        again.tree = copy.deepcopy(loaded["tree"])
        again.tree[ROOT]["UUID"] = "99999999-0000-4000-8000-000000000009"   # neues „Gerät“: sonst schlägt die Zertifikatsprüfung an
        again.start()
        self.addCleanup(again.stop)
        cap2, _, err = C.run_capture(again.addr, "", "pw", Path(self.tmp.name) / "hpe2", vendor="hpe", do_redact=False)
        self.assertEqual(err, "")
        self.assertEqual([f for f in C.diff_captures(loaded, cap2) if f[0] == "kritisch"], [])

    def test_redacted_capture_contains_no_ip_of_the_device(self):
        srv, cap, rep, _ = self.capture("lenovo")
        dump = json.dumps(cap, ensure_ascii=False)
        self.assertNotIn("127.0.0.1", dump)
        self.assertTrue(cap["meta"]["redacted"])

    def test_target_folder_must_be_empty(self):
        _, cap, _, _ = self.capture("smc")
        with self.assertRaises(ValueError):
            C.save_capture(cap, Path(self.tmp.name) / "smc")

    def test_stop_flag_ends_capture_early(self):
        srv = DmtfMockServer("dell").start()
        self.addCleanup(srv.stop)
        eng = C.CaptureEngine(log=lambda *a: None, stopped=lambda: True)
        cap, err = eng.capture(srv.addr, "", "pw", "dell")
        self.assertIsNone(cap)           # sofort abgebrochen: nichts gelesen

    def test_resource_limit_is_respected(self):
        srv = DmtfMockServer("dell").start()
        self.addCleanup(srv.stop)
        eng = C.CaptureEngine(log=lambda *a: None, max_resources=3)
        cap, err = eng.capture(srv.addr, "", "pw", "dell")
        self.assertEqual(len(cap["tree"]), 3)
        self.assertEqual(cap["meta"]["aborted"], "limit")

    def test_capabilities_match_the_mockup_trees(self):
        states = {}
        for v in VENDORS:
            _, cap, _, _ = self.capture(v, do_redact=False)
            states[v] = {label: s for label, s, _ in C.capabilities(cap)}
        keys = "SSH-Schlüssel je Konto"
        self.assertEqual(states["dell"][keys], "ja")
        self.assertEqual(states["dell"]["TLS-Mindestversion einstellbar"], "ja")
        for v in ("hpe", "lenovo", "smc"):
            self.assertEqual(states[v]["TLS-Mindestversion einstellbar"], "nein", v)   # Beleg zur „größten Lücke“
        for v in VENDORS:
            self.assertEqual(states[v]["Management-Netzwerkport (Pfad der Netzwerkeinstellungen)"], "ja", v)

    def test_report_text_is_readable(self):
        _, cap, _, _ = self.capture("dell")
        text = C.report_text(cap)
        self.assertIn("✔", text)
        self.assertIn("Fähigkeiten", text)


class TestCaptureHelpers(unittest.TestCase):
    def test_norm_path(self):
        self.assertEqual(C.norm_path("/redfish/v1/Systems/1/"), ROOT + "/Systems/1")
        self.assertEqual(C.norm_path("/redfish/v1/Systems/1#/Status"), ROOT + "/Systems/1")
        self.assertEqual(C.norm_path("/redfish/v1/x?$expand=*"), ROOT + "/x")
        self.assertIsNone(C.norm_path("/other/path"))
        self.assertIsNone(C.norm_path("/redfish/v1/../../etc/passwd"))
        self.assertIsNone(C.norm_path("https://other.example.com/redfish/v1/x", "10.0.0.1"))
        self.assertEqual(C.norm_path("https://10.0.0.1/redfish/v1/x", "10.0.0.1:443"), ROOT + "/x")

    def test_unsafe_paths_never_leave_the_target_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = {"meta": {"redacted": True}, "allow": {}, "errors": {}, "skipped": {},
                   "tree": {ROOT: {}, ROOT + "/../../evil": {"x": 1}, ROOT + "/a:b/c d": {"y": 2}}}
            C.save_capture(cap, Path(tmp) / "out")
            self.assertFalse((Path(tmp) / "evil").exists())
            self.assertEqual(sorted(C.load_capture(Path(tmp) / "out")["tree"]), [ROOT, ROOT + "/a:b/c d"])

    def test_member_caps(self):
        self.assertEqual(C.member_cap(ROOT + "/Managers/1/LogServices/Log1/Entries"), C.LOG_CAP)
        self.assertEqual(C.member_cap(ROOT + "/Registries"), 0)
        self.assertEqual(C.member_cap(ROOT + "/Systems"), C.MEMBER_CAP)


class TestDiff(unittest.TestCase):
    def pair(self):
        a = sample()
        C.redact(a)
        return a, copy.deepcopy(a)

    def levels(self, a, b):
        return [(lv, p, t) for lv, p, t in C.diff_captures(a, b)]

    def test_identical_captures_have_no_differences(self):
        a, b = self.pair()
        self.assertEqual(C.diff_captures(a, b), [])
        self.assertIn("Keine Unterschiede", C.diff_text([]))

    def test_lost_write_permission_is_critical(self):
        a, b = self.pair()
        path = ROOT + "/Systems/S1"
        b["allow"][path] = "GET"
        found = self.levels(a, b)
        self.assertEqual(found[0][0], "kritisch")
        self.assertIn("PATCH", found[0][2])
        self.assertEqual(found[0][1], path)

    def test_gained_write_permission_is_only_a_note(self):
        a, b = self.pair()
        path = ROOT + "/Systems/S1"
        a["allow"][path] = "GET"
        b["allow"][path] = "GET, PATCH"
        self.assertEqual([lv for lv, _, _ in self.levels(a, b)], ["hinweis"])

    def test_missing_allow_header_is_not_compared(self):
        a, b = self.pair()
        b["allow"][ROOT + "/Systems/S1"] = ""
        self.assertEqual(C.diff_captures(a, b), [])

    def test_removed_and_new_resources(self):
        a, b = self.pair()
        del b["tree"][ROOT + "/Managers/M1/NetworkProtocol"]
        b["tree"][ROOT + "/NeuerDienst"] = {"Id": "x"}
        found = self.levels(a, b)
        self.assertTrue(any(lv == "kritisch" and p.endswith("NetworkProtocol") for lv, p, _ in found))
        self.assertTrue(any(lv == "hinweis" and p.endswith("NeuerDienst") for lv, p, _ in found))

    def test_removed_action_and_allowable_value_are_critical(self):
        a, b = self.pair()
        p = ROOT + "/Systems/S1"
        a["tree"][p]["Actions"] = {"#ComputerSystem.Reset": {"target": "x", "ResetType@Redfish.AllowableValues": ["On", "ForceOff", "GracefulRestart"]}}
        b["tree"][p]["Actions"] = {"#ComputerSystem.Reset": {"target": "x", "ResetType@Redfish.AllowableValues": ["On", "ForceOff"]}}
        found = self.levels(a, b)
        self.assertTrue(any(lv == "kritisch" and "GracefulRestart" in t for lv, _, t in found))
        del b["tree"][p]["Actions"]["#ComputerSystem.Reset"]
        self.assertTrue(any(lv == "kritisch" and "Actions" in t for lv, _, t in self.levels(a, b)))

    def test_version_change_is_a_note_and_volatile_values_are_ignored(self):
        a, b = self.pair()
        p = ROOT + "/Systems/S1"
        b["tree"][p]["BiosVersion"] = "7.10.30"
        b["tree"][p]["PowerState"] = "Off"                           # Eigenschaft neu -> nur Hinweis
        a["tree"][p]["Status"] = {"Health": "OK"}
        b["tree"][p]["Status"] = {"Health": "Critical"}              # Werte werden nicht verglichen
        found = self.levels(a, b)
        self.assertTrue(all(lv == "hinweis" for lv, _, _ in found))
        self.assertTrue(any("7.00.00.171 → 7.10.30" in t for _, _, t in found))
        self.assertFalse(any("Critical" in t for _, _, t in found))

    def test_log_entries_and_sessions_are_ignored(self):
        a, b = self.pair()
        a["tree"][ROOT + "/Managers/1/LogServices/SEL/Entries/5"] = {"Message": "x"}
        a["tree"][ROOT + "/SessionService/Sessions/abc"] = {"Id": "abc"}
        self.assertEqual(C.diff_captures(a, b), [])

    def test_vendor_mismatch_is_reported(self):
        a, b = self.pair()
        b["meta"]["vendor"] = "hpe"
        self.assertEqual(C.diff_captures(a, b)[0][0], "kritisch")

    def test_diff_text_lists_critical_first(self):
        a, b = self.pair()
        b["allow"][ROOT + "/Systems/S1"] = "GET"
        b["tree"][ROOT + "/Neu"] = {}
        text = C.diff_text(C.diff_captures(a, b))
        self.assertLess(text.index("KRITISCH"), text.index("HINWEISE"))


class TestCli(unittest.TestCase):
    def test_diff_and_report_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = sample(), sample()
            b["allow"][ROOT + "/Systems/S1"] = "GET"
            for name, cap in (("a", a), ("b", b)):
                C.redact(cap)
                C.save_capture(cap, Path(tmp) / name)
            self.assertEqual(C.main(["diff", str(Path(tmp) / "a"), str(Path(tmp) / "a")]), 0)
            self.assertEqual(C.main(["diff", str(Path(tmp) / "a"), str(Path(tmp) / "b")]), 2)
            self.assertEqual(C.main(["report", str(Path(tmp) / "a")]), 0)
            self.assertEqual(C.main(["report", str(Path(tmp) / "fehlt")]), 1)


if __name__ == "__main__":
    unittest.main()


class TestCaptureTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bmc_steward.main import Window
        cls.w = Window()

    def test_tab_exists_among_tools_before_testlab(self):
        menu = self.w.tabs.menu
        names = [menu.item(i).text() for i in range(menu.count())]
        i = names.index("Aufnahme")
        self.assertEqual(names[i - 1], "UEFI-Konsole")
        self.assertEqual(names[i + 1], "Testlabor")

    def test_redaction_is_on_by_default_and_buttons_start_in_the_right_state(self):
        self.assertTrue(self.w.cap_redact.isChecked())
        self.assertTrue(self.w.cap_start.isEnabled())
        self.assertFalse(self.w.cap_stop.isEnabled())
        self.assertFalse(self.w.cap_open.isEnabled())
        self.assertFalse(self.w.cap_save.isEnabled())

    @need("openssl")
    def test_compare_of_two_saved_captures_shows_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = sample(), sample()
            b["allow"][ROOT + "/Systems/S1"] = "GET"
            for name, cap in (("a", a), ("b", b)):
                C.redact(cap)
                C.save_capture(cap, Path(tmp) / name)
            self.w.cap_a.setText(str(Path(tmp) / "a"))
            self.w.cap_b.setText(str(Path(tmp) / "b"))
            self.w.capture_compare()
            self.assertIn("KRITISCH", self.w.cap_diff.toPlainText())
            self.assertTrue(self.w.cap_save.isEnabled())
            self.w.cap_b.setText(str(Path(tmp) / "fehlt"))
            self.w.capture_compare()
            self.assertIn("nicht gefunden", self.w.cap_diff.toPlainText())
            self.assertFalse(self.w.cap_save.isEnabled())

    @need("openssl")
    def test_capture_thread_runs_against_a_mockup_and_reports_done(self):
        from PyQt6.QtTest import QSignalSpy
        from bmc_steward.capture_tab import CaptureThread
        srv = DmtfMockServer("lenovo").start()
        self.addCleanup(srv.stop)
        with tempfile.TemporaryDirectory() as tmp:
            th = CaptureThread(None, srv.addr, "", "pw", "lenovo", Path(tmp) / "x", True)
            spy = QSignalSpy(th.done)
            th.start()
            self.assertTrue(spy.wait(30000) or len(spy) == 1)
            th.wait(5000)
            cap, rep, err = spy[0]
            self.assertEqual(err, "")
            self.assertTrue(cap["meta"]["redacted"])
            self.assertTrue((Path(tmp) / "x" / "redfish" / "v1" / "index.json").is_file())


class TestCaptureAgainstVbmc(unittest.TestCase):
    """Aufnahme des eigenen Testlabor-Simulators: alle vier Hersteller, mit Anmeldung und nur lesend."""

    @classmethod
    def setUpClass(cls):
        import ssl
        from tests.test_vbmc import vbmc
        cls.vbmc = vbmc
        cert, key = vbmc.ensure_cert()
        cls.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.ctx.load_cert_chain(cert, key)

    def test_each_vendor_can_be_captured_redacted_and_reported(self):
        from tests.test_vbmc import FACTORY_LOGIN
        for vendor in VENDORS:
            with self.subTest(vendor=vendor), tempfile.TemporaryDirectory() as tmp:
                fleet = self.vbmc.Fleet(self.ctx, [0])
                b = self.vbmc.BMC(vendor, "127.0.0.1", 0, idx=1)
                fleet.start(b)
                try:
                    port = b._redfish[0].socket.getsockname()[1]
                    user, pw = FACTORY_LOGIN[vendor]
                    cap, rep, err = C.run_capture("127.0.0.1:%d" % port, user, pw, Path(tmp) / "x", vendor=vendor)
                finally:
                    fleet.stop(b)
                self.assertEqual(err, "")
                self.assertGreater(len(cap["tree"]), 10, "der Simulator ist groß genug für einen echten Baum")
                self.assertTrue(rep)
                self.assertNotIn("127.0.0.1", json.dumps(cap, ensure_ascii=False))
                states = {label: s for label, s, _ in C.capabilities(cap)}
                port = states["Management-Netzwerkport (Pfad der Netzwerkeinstellungen)"]
                if vendor == "dell":          # der Simulator bildet Dell-Netzwerkwerte über Attributes ab, nicht über einen NIC-Pfad
                    self.assertEqual(port, "nein")
                else:
                    self.assertEqual(port, "ja")
                loaded = C.load_capture(Path(tmp) / "x")
                self.assertEqual(C.diff_captures(loaded, loaded), [])


class TestFreeTextRedaction(unittest.TestCase):
    def test_ip_in_log_message_and_loopback_target_are_redacted(self):
        cap = sample()
        cap["meta"]["host"] = "127.0.0.1"
        cap["tree"][ROOT + "/Systems/S1"]["Note"] = "Successfully logged in using admin, from 10.20.30.40 and GUI. Target 127.0.0.1"
        C.redact(cap)
        dump = json.dumps(cap)
        self.assertNotIn("10.20.30.40", dump)
        self.assertNotIn("127.0.0.1", dump)
