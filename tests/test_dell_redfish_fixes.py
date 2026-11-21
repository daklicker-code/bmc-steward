"""Dell-iDRAC-Verhalten, das gegen einen iDRAC9-Simulator auffiel (Version 3.11.1):

1. TLS-Mindestversion steht beim iDRAC9 im Attribut WebServer.1.TLSProtocol (nicht Security.1.TLSProtocol);
   der Wert für TLS 1.3 heißt je nach Firmware „TLS 1.3 Only“ o. ä. – das Tool liest die vorhandenen Attribute.
2. Ein 400 mit MessageId „…PropertyUnknown“ gilt als „nicht unterstützt“, auch wenn der Meldungstext das Wort
   nicht enthält (der iDRAC schickt immer Message und MessageId).
3. UEFI-Werte über Bios/Settings übernimmt der iDRAC nur mit Konfigurationsauftrag: @Redfish.SettingsApplyTime
   im PATCH, sonst ein Auftrag über Managers/iDRAC.Embedded.1/Jobs.
4. DNS-Server über die Attribut-API: IPv4.1.DNS1/DNS2 (bzw. IPv4Static.1.DNS1/DNS2), nicht NIC.1.DNSServer1.

Ein kleiner nachgebauter iDRAC ersetzt api(); geprüft wird, was gesendet wird und was das Gerät daraus macht."""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.redfish import BiosEngine, RedfishClient, ServicesEngine, UsersEngine

MGR = "/redfish/v1/Managers/iDRAC.Embedded.1"
SYS = "/redfish/v1/Systems/System.Embedded.1"


def err(msg_id, message):
    return {"error": {"@Message.ExtendedInfo": [{"MessageId": msg_id, "Message": message}],
                      "code": "Base.1.12.GeneralError", "message": "A general error has occurred."}}


class FakeIdrac:
    """Antwortet wie ein iDRAC9 auf die hier benutzten Pfade."""

    TLS_VALUES = ("TLS 1.1 and Higher", "TLS 1.2 and Higher", "TLS 1.3 Only")

    def __init__(self, attrs=None, apply_time=True):
        self.attrs = dict(attrs if attrs is not None else {
            "WebServer.1.TLSProtocol": "TLS 1.2 and Higher", "IPv4.1.DNS1": "0.0.0.0", "IPv4.1.DNS2": "0.0.0.0",
            "IPv4.1.DNSFromDHCP": "Enabled", "NIC.1.DNSRacName": "idrac"})
        self.apply_time = apply_time            # alte Firmware ohne @Redfish.SettingsApplyTime: False
        self.bios = {"LogicalProc": "Enabled", "BootMode": "Uefi"}
        self.pending, self.jobs, self.calls = {}, [], []
        self.power = "On"

    def reboot(self):
        if self.jobs:                           # nur ein Auftrag übernimmt die ausstehenden Werte
            self.bios.update(self.pending)
            self.pending, self.jobs = {}, []

    def __call__(self, method, path, data):
        self.calls.append((method, path, data))
        if path == MGR + "/Attributes":
            if method == "GET":
                return 200, {"Attributes": dict(self.attrs)}
            new = (data or {}).get("Attributes", {})
            for k, v in new.items():
                if k not in self.attrs:
                    return 400, err("Base.1.12.PropertyUnknown",
                                    f"The property {k} is not in the list of valid properties for the resource.")
                if k == "WebServer.1.TLSProtocol" and v not in self.TLS_VALUES:
                    return 400, err("Base.1.12.PropertyValueNotInList",
                                    f"The value {v} for the property {k} is not in the list of acceptable values.")
            self.attrs.update(new)
            return 200, {}
        if path == SYS + "/Bios" and method == "GET":
            return 200, {"Attributes": dict(self.bios),
                         "@Redfish.Settings": {"SettingsObject": {"@odata.id": SYS + "/Bios/Settings"}}}
        if path == SYS + "/Bios/Settings" and method == "PATCH":
            if "@Redfish.SettingsApplyTime" in data and not self.apply_time:
                return 400, err("Base.1.12.PropertyUnknown", "The property @Redfish.SettingsApplyTime is not supported.")
            self.pending.update(data["Attributes"])
            if "@Redfish.SettingsApplyTime" in data:
                self.jobs.append("JID_1")
                return 202, {}
            return 200, {}
        if path == MGR + "/Jobs" and method == "POST":
            if data.get("TargetSettingsURI") != SYS + "/Bios/Settings" or not self.pending:
                return 400, err("IDRAC.2.9.SYS011", "No pending values.")
            self.jobs.append("JID_2")
            return 200, {}
        if path == SYS and method == "GET":
            return 200, {"PowerState": self.power}
        if path == SYS + "/Actions/ComputerSystem.Reset":
            self.reboot()
            return 204, None
        return 404, None


def wire(engine, dev):
    def api(ip, user, pw, method, path, data=None, timeout=None, _retry=True):
        engine.code, engine.body = dev(method, path, data)
        return engine.code
    engine.api = api
    engine.logged = []
    engine.log = lambda text="", kind="": engine.logged.append((text, kind))
    engine.cur_user = "root"
    return engine


def services(dev):
    return wire(ServicesEngine([], "root", {}, dry=False), dev)


class TestTlsAttribute(unittest.TestCase):
    def test_tls12_goes_to_webserver_attribute(self):
        dev = FakeIdrac(attrs={"WebServer.1.TLSProtocol": "TLS 1.1 and Higher"})
        self.assertTrue(services(dev).svc_hard("10.0.0.1", "pw", "dell", {"tls_min": "tls12"}))
        self.assertEqual(dev.attrs["WebServer.1.TLSProtocol"], "TLS 1.2 and Higher")

    def test_tls13_uses_value_the_device_accepts(self):
        dev = FakeIdrac()
        self.assertTrue(services(dev).svc_hard("10.0.0.1", "pw", "dell", {"tls_min": "tls13"}))
        self.assertEqual(dev.attrs["WebServer.1.TLSProtocol"], "TLS 1.3 Only")

    def test_never_sends_unknown_security_attribute(self):
        dev = FakeIdrac()
        services(dev).svc_hard("10.0.0.1", "pw", "dell", {"tls_min": "tls12"})
        sent = [d for m, p, d in dev.calls if m == "PATCH"]
        self.assertFalse(any("Security.1.TLSProtocol" in (d or {}).get("Attributes", {}) for d in sent), sent)

    def test_older_attribute_name_still_used_when_device_has_it(self):
        dev = FakeIdrac(attrs={"Security.1.TLSProtocol": "TLS 1.1 and Higher"})
        services(dev).svc_hard("10.0.0.1", "pw", "dell", {"tls_min": "tls12"})
        self.assertEqual(dev.attrs["Security.1.TLSProtocol"], "TLS 1.2 and Higher")

    def test_device_without_tls_attribute_is_skipped_not_failed(self):
        dev = FakeIdrac(attrs={"NIC.1.DNSRacName": "x"})
        e = services(dev)
        self.assertIsNone(e.svc_hard("10.0.0.1", "pw", "dell", {"tls_min": "tls13"}))
        self.assertFalse([t for t, k in e.logged if k == "err"], e.logged)

    def test_no_attributes_resource_is_skipped(self):
        dev = FakeIdrac()
        e = services(dev)
        self.assertIsNone(e.svc_hard("10.0.0.1", "pw", "lenovo", {"tls_min": "tls12"}))


class TestUnsupportedByMessageId(unittest.TestCase):
    def test_property_unknown_message_id_counts_as_unsupported(self):
        dev = FakeIdrac()
        e = services(dev)
        res = e.soft("X", "10.0.0.1", "pw", "PATCH", MGR + "/Attributes", {"Attributes": {"Nope.1.Thing": "a"}})
        self.assertIsNone(res)

    def test_users_engine_too(self):
        dev = FakeIdrac()
        e = wire(UsersEngine(dry=False), dev)
        self.assertIsNone(e.soft("X", "10.0.0.1", "root", "pw", "PATCH", MGR + "/Attributes",
                                 {"Attributes": {"Nope.1.Thing": "a"}}))

    def test_real_errors_stay_errors(self):
        c = RedfishClient(dry=False)
        c.body = err("Base.1.12.InternalError", "Internal error.")
        self.assertFalse(c.unsupported())


class TestBiosNeedsJob(unittest.TestCase):
    def run_bios(self, dev, reboot=True):
        e = wire(BiosEngine([("10.0.0.1", "pw")], "root", {"LogicalProc": "Disabled"}, dry=False, vendor="dell",
                            reboot=reboot), dev)
        e.self_guard = lambda *a: True
        e.backup = lambda *a: None
        return e.run(), e

    def test_values_are_applied_after_reboot(self):
        dev = FakeIdrac()
        ok, _e = self.run_bios(dev)
        self.assertTrue(ok)
        self.assertEqual(dev.bios["LogicalProc"], "Disabled")

    def test_old_firmware_gets_job_via_jobs_collection(self):
        dev = FakeIdrac(apply_time=False)
        ok, _e = self.run_bios(dev)
        self.assertTrue(ok)
        self.assertEqual(dev.bios["LogicalProc"], "Disabled")
        self.assertTrue(any(m == "POST" and p == MGR + "/Jobs" for m, p, d in dev.calls))

    def test_without_reboot_a_job_waits_for_next_restart(self):
        dev = FakeIdrac()
        ok, _e = self.run_bios(dev, reboot=False)
        self.assertTrue(ok)
        self.assertEqual(dev.bios["LogicalProc"], "Enabled")
        self.assertTrue(dev.jobs)
        dev.reboot()
        self.assertEqual(dev.bios["LogicalProc"], "Disabled")


class TestBoardSwapUefiNeedsJob(unittest.TestCase):
    def test_restore_creates_job(self):
        from bmc_steward.board_swap import BoardSwapEngine
        dev = FakeIdrac()
        e = wire(BoardSwapEngine.__new__(BoardSwapEngine), dev)
        e.dry = False
        e.live = lambda ip, pw, path: dev("GET", path, None)[1]
        ok = e.restore_bios("10.0.0.1", "pw", "dell", {"resources": {SYS + "/Bios": {"Attributes": {"LogicalProc": "Disabled"}}}})
        self.assertTrue(ok)
        dev.reboot()
        self.assertEqual(dev.bios["LogicalProc"], "Disabled")


class TestDnsAttributes(unittest.TestCase):
    def fallback(self, dev, servers):
        e = services(dev)
        orig = e.soft
        first = {"done": False}

        def soft(label, ip, pw, method, path, body):
            if not first["done"]:               # EthernetInterfaces nimmt StaticNameServers nicht an
                first["done"] = True
                return None
            return orig(label, ip, pw, method, path, body)
        e.soft = soft
        return e.svc_dns("10.0.0.1", "pw", "dell", {"servers": servers})

    def test_uses_ipv4_dns_attributes(self):
        dev = FakeIdrac()
        self.assertTrue(self.fallback(dev, ["10.20.0.1", "10.20.0.2"]))
        self.assertEqual((dev.attrs["IPv4.1.DNS1"], dev.attrs["IPv4.1.DNS2"]), ("10.20.0.1", "10.20.0.2"))
        self.assertEqual(dev.attrs["IPv4.1.DNSFromDHCP"], "Disabled")

    def test_static_group_when_ipv4_group_missing(self):
        dev = FakeIdrac(attrs={"IPv4Static.1.DNS1": "0.0.0.0", "IPv4Static.1.DNS2": "0.0.0.0"})
        self.assertTrue(self.fallback(dev, ["10.20.0.1"]))
        self.assertEqual((dev.attrs["IPv4Static.1.DNS1"], dev.attrs["IPv4Static.1.DNS2"]), ("10.20.0.1", "0.0.0.0"))

    def test_never_sends_nic_dnsserver(self):
        dev = FakeIdrac()
        self.fallback(dev, ["10.20.0.1"])
        sent = [d for m, p, d in dev.calls if m == "PATCH"]
        self.assertFalse(any(k.startswith("NIC.1.DNSServer") for d in sent for k in (d or {}).get("Attributes", {})), sent)


if __name__ == "__main__":
    unittest.main()
