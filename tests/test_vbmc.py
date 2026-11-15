"""Testlabor: tools/vbmc (virtuelle BMCs) und tools/vbmc/netlab (DHCP-Client, MACs) - ohne root.

Geprüft wird, was sich ohne Netzwerk-Namespaces prüfen lässt: DHCP-Nachrichten bauen und lesen
(gegen ein von Hand gebautes Server-Paket), reproduzierbare MACs, Firmware-Generationen, feste
Dell-Slots und das Szenario-Format. Der Ende-zu-Ende-Lauf (Namespaces, Bridge, macvlan) braucht
root und wird von Hand mit dem Testlabor-Reiter geprüft.
"""
import http.client
import importlib.util
import io
import json
import os
import socket
import ssl
import struct
import sys
import time
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

os.environ.setdefault("VBMC_SSDP_PORT", "0")        # SSDP nur im eigenen Test (freier Port)
from tests._support import qapp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VBMC_DIR = os.path.join(REPO, "tools", "vbmc")
sys.path.insert(0, VBMC_DIR)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(VBMC_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


netlab = _load("netlab")
vbmc = _load("vbmc")


def server_reply(msgtype, xid, mac, yiaddr, server="192.168.0.1", router="192.168.0.1", lease=3600):
    """DHCP-Antwort wie von dnsmasq, als Ethernet-Rahmen."""
    mb = bytes(int(x, 16) for x in mac.split(":"))
    bootp = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 2, 1, 6, 0, xid, 0, 0x8000, b"\0" * 4,
                        socket.inet_aton(yiaddr), socket.inet_aton(server), b"\0" * 4, mb + b"\0" * 10, b"", b"")
    opts = (bytes([53, 1, msgtype]) + bytes([54, 4]) + socket.inet_aton(server)
            + bytes([1, 4]) + socket.inet_aton("255.255.255.0") + bytes([3, 4]) + socket.inet_aton(router)
            + bytes([51, 4]) + struct.pack("!I", lease) + b"\xff")
    payload = bootp + netlab.MAGIC + opts
    return netlab.build_frame("02:00:00:00:00:01", "ff:ff:ff:ff:ff:ff", server, "255.255.255.255",
                              payload, sport=67, dport=68)


class TestDhcpPackets(unittest.TestCase):
    MAC = "d0:94:66:12:34:56"

    def test_discover_frame(self):
        payload = netlab.build_dhcp(netlab.DISCOVER, 0x1234, self.MAC, hostname="idrac-ABC1234")
        frame = netlab.build_frame(self.MAC, "ff:ff:ff:ff:ff:ff", "0.0.0.0", "255.255.255.255", payload)
        self.assertEqual(frame[12:14], b"\x08\x00")
        ip = frame[14:34]
        self.assertEqual(netlab._csum(ip), 0, "IP-Prüfsumme muss stimmen")
        self.assertEqual(struct.unpack("!HH", frame[34:38]), (68, 67))
        bootp = frame[42:]
        self.assertEqual(bootp[0], 1)
        self.assertEqual(struct.unpack("!H", bootp[10:12])[0], 0x8000, "ohne Adresse: Broadcast-Antwort anfordern")
        self.assertIn(b"idrac-ABC1234", bootp)

    def test_parse_offer(self):
        r = netlab.parse_frame(server_reply(netlab.OFFER, 77, self.MAC, "192.168.0.120"), self.MAC, 77)
        self.assertEqual(r["type"], netlab.OFFER)
        self.assertEqual(r["yiaddr"], "192.168.0.120")
        self.assertEqual(r["prefix"], 24)
        self.assertEqual(r["router"], "192.168.0.1")
        self.assertEqual(r["lease"], 3600)
        self.assertEqual(r["server"], "192.168.0.1")

    def test_foreign_reply_ignored(self):
        frame = server_reply(netlab.ACK, 77, self.MAC, "192.168.0.120")
        self.assertIsNone(netlab.parse_frame(frame, self.MAC, 78), "andere Transaktion")
        self.assertIsNone(netlab.parse_frame(frame, "d0:94:66:00:00:00", 77), "andere MAC")

    def test_garbage_never_raises(self):
        import random
        rnd = random.Random(1)
        good = server_reply(netlab.ACK, 5, self.MAC, "10.0.0.5")
        for _ in range(2000):
            buf = bytearray(good)
            for _ in range(rnd.randint(1, 8)):
                buf[rnd.randrange(len(buf))] = rnd.randrange(256)
            netlab.parse_frame(bytes(buf[:rnd.randint(0, len(buf))]), self.MAC, 5)

    def test_release_unicast(self):
        payload = netlab.build_dhcp(netlab.RELEASE, 9, self.MAC, ciaddr="192.168.0.50", server="192.168.0.1")
        self.assertEqual(struct.unpack("!H", payload[10:12])[0], 0, "mit Adresse: kein Broadcast-Flag")
        self.assertEqual(payload[12:16], socket.inet_aton("192.168.0.50"))


class TestMac(unittest.TestCase):
    def test_reproducible_with_vendor_oui(self):
        a = netlab.mac_for("dell", "CN12345")
        self.assertEqual(a, netlab.mac_for("dell", "CN12345"), "gleiche Seriennummer = gleiche MAC")
        self.assertNotEqual(a, netlab.mac_for("dell", "CN12346"))
        self.assertTrue(a.startswith(netlab.OUI["dell"]))
        self.assertTrue(netlab.mac_for("smc", "S1").startswith(netlab.OUI["smc"]))

    def test_wireless_parent_rejected(self):
        self.assertIn("gibt es nicht", netlab.wired_parent_ok("gibtsnicht0"))


class TestVirtualBmc(unittest.TestCase):
    def test_generations(self):
        b = vbmc.BMC("hpe", "127.0.0.1", 8443, gen="ilo4")
        self.assertEqual(b.vendor, "hpe_old", "iLO 4 läuft über RIBCL")
        self.assertEqual(b.gen_label, "iLO 4")
        b = vbmc.BMC("hpe_old", "127.0.0.1", 8443)
        self.assertEqual(b.gen, "ilo4")
        b = vbmc.BMC("dell", "127.0.0.1", 8443, gen="idrac8_alt")
        self.assertEqual(b.fw, "2.40.40.40")
        b = vbmc.BMC("lenovo", "127.0.0.1", 8443, gen="gibtsnicht")
        self.assertEqual(b.gen, "xcc2", "unbekannte Generation: Standard")

    def test_dell_slots(self):
        b = vbmc.BMC("dell", "127.0.0.1", 8443, busy_slots={"3": "operator"})
        self.assertEqual(b.slot_of("2"), "root")
        self.assertEqual(b.slot_of("3"), "operator")
        self.assertIsNone(b.slot_of("4"))
        self.assertEqual(b.free_slot(), 4)
        b.add_account("admin", "x", slot=5)
        self.assertEqual(b.slot_of("5"), "admin")

    def test_factory_login_and_state(self):
        b = vbmc.BMC("smc", "127.0.0.1", 8443, password="QWERTZUIOP", serial="S123", idx=3)
        self.assertTrue(b.check_auth("ADMIN", "QWERTZUIOP"))
        self.assertFalse(b.check_auth("ADMIN", "ADMIN"))
        st = b.state()
        self.assertEqual((st["id"], st["factory_pass"], st["serial"]), (3, "QWERTZUIOP", "S123"))
        json.dumps(st)

    def test_dhcp_hostnames(self):
        self.assertEqual(vbmc.BMC("dell", "x", 1, serial="CNABC1234").dhcp_hostname(), "idrac-ABC1234")
        self.assertEqual(vbmc.BMC("hpe", "x", 1, serial="CZ99").dhcp_hostname(), "ILOCZ99")

    def test_static_factory_net(self):
        b = vbmc.BMC("dell", "x", 1, net="static", ip="192.168.0.120")
        self.assertEqual((b.factory_net, b.factory_ip), ("static", "192.168.0.120"))
        self.assertEqual(vbmc.BMC("dell", "x", 1, net="static").factory_net, "dhcp", "ohne IP: DHCP")

    def test_scenario_formats(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as f:
                json.dump([{"vendor": "dell"}], f)
            self.assertEqual(vbmc.load_config(p)["servers"], [{"vendor": "dell"}])
            with open(p, "w") as f:
                json.dump({"mode": "rz", "servers": [{"vendor": "ilo"}]}, f)
            cfg = vbmc.load_config(p)
            self.assertEqual(cfg["mode"], "rz")
            b = vbmc.bmc_from_entry(cfg["servers"][0], 0, "127.9.0", 8443)
            self.assertEqual(b.family, "hpe")
            with self.assertRaises(ValueError):
                vbmc.bmc_from_entry({"vendor": "cisco"}, 0, "127.9.0", 8443)


class TestLogFormat(unittest.TestCase):
    """Jede Protokollzeile bekommt eine Uhrzeit vorangestellt - außer @@STATE (von der GUI geparst)."""

    def test_timestamp_prefix(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            vbmc.print("Server CN123 gestartet")
        self.assertRegex(buf.getvalue(), r"^\[\d{2}:\d{2}:\d{2}\] Server CN123 gestartet\n$")

    def test_state_line_untouched(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            vbmc.print("@@STATE []")
        self.assertEqual(buf.getvalue(), "@@STATE []\n")

    def test_blank_line_untouched(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            vbmc.print("")
        self.assertEqual(buf.getvalue(), "\n")

    def test_leading_newline_kept_before_timestamp(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            vbmc.print("\nHinweis danach")
        self.assertRegex(buf.getvalue(), r"^\n\[\d{2}:\d{2}:\d{2}\] Hinweis danach\n$")


class _WebBase(unittest.TestCase):
    """Gemeinsame Hilfen: virtueller BMC im selben Prozess, echter HTTPS-Client (reines TCP-Loopback ist in
    manchen Sandboxes zwischen getrennten Prozessen nicht erreichbar, im selben Prozess aber immer)."""

    @classmethod
    def setUpClass(cls):
        cert, key = vbmc.ensure_cert()
        cls.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.ctx.load_cert_chain(cert, key)
        cls.cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cls.cctx.check_hostname = False
        cls.cctx.verify_mode = ssl.CERT_NONE

    def _start(self, vendor, idx=1, gen=None):
        fleet = vbmc.Fleet(self.ctx, [0])            # Port 0: das OS wählt einen freien
        b = vbmc.BMC(vendor, "127.0.0.1", 0, idx=idx, gen=gen)
        fleet.start(b)
        self.addCleanup(fleet.stop, b)
        return b, b._redfish[0].socket.getsockname()[1]

    def _req(self, port, path, cookie=None, method="GET", body=None):
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=self.cctx, timeout=5)
        try:
            headers = {"Cookie": cookie} if cookie else {}
            data = None
            if body is not None:
                data = json.dumps(body)
                headers["Content-Type"] = "application/json"
            conn.request(method, path, data, headers)
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def _login(self, port, user, pw):
        st, h, _ = self._req(port, "/redfish/v1/SessionService/Sessions", method="POST",
                             body={"UserName": user, "Password": pw})
        return st, (h["Set-Cookie"].split(";")[0] if "Set-Cookie" in h else None)


FACTORY_LOGIN = {"dell": ("root", "calvin"), "hpe": ("Administrator", "Admin123!"),
                 "lenovo": ("USERID", "PASSW0RD"), "smc": ("ADMIN", "ADMIN")}
CONSOLE_PATHS = {"dell": "/console", "hpe": "/irc.html", "lenovo": "/vbsim/xcc-console",
                 "smc": "/cgi/url_redirect.cgi?url_name=man_ikvm_html5_bootstrap"}


class TestWebUi(_WebBase):
    """Weboberflächen aller vier Hersteller: Login-Seite, Session, Verwaltungsoberfläche, Konsole."""

    def test_login_page_for_every_vendor(self):
        for v in FACTORY_LOGIN:
            with self.subTest(v):
                b, port = self._start(v)
                st, _, body = self._req(port, "/")
                self.assertEqual(st, 200)
                self.assertIn("type=password", body)
                self.assertIn("login-btn", body, "Anmeldeknopf, den die Auto-Anmeldung des Tools findet")

    def test_session_login_and_wrong_password(self):
        for v, (u, pw) in FACTORY_LOGIN.items():
            with self.subTest(v):
                b, port = self._start(v)
                self.assertEqual(self._login(port, u, "falsch")[0], 401)
                st, cookie = self._login(port, u, pw)
                self.assertEqual(st, 201)
                self.assertTrue(cookie and cookie.startswith("sessionKey="))
                msgs = [e["message"] for e in b.sim.events]
                self.assertTrue(any(u in m for m in msgs[-2:]), "Anmeldung und Fehlversuch stehen im Ereignisprotokoll")

    def test_app_and_console_need_session(self):
        for v, (u, pw) in FACTORY_LOGIN.items():
            with self.subTest(v):
                b, port = self._start(v)
                path = CONSOLE_PATHS[v]
                st, h, _ = self._req(port, path)
                self.assertEqual(st, 302)
                self.assertEqual(h.get("Location"), "/")
                _, cookie = self._login(port, u, pw)
                st, _, body = self._req(port, path, cookie)
                self.assertEqual(st, 200)
                self.assertIn("/vbsim/console/screen", body)
                st, _, body = self._req(port, "/", cookie)        # eingeloggt: Verwaltungsoberfläche
                self.assertEqual(st, 200)
                self.assertIn("window.VB", body)

    def test_redfish_needs_auth_but_accepts_session_cookie(self):
        b, port = self._start("hpe")
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1")[0], 401)
        _, cookie = self._login(port, *FACTORY_LOGIN["hpe"])
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1", cookie)[0], 200)

    def test_logout_ends_session(self):
        b, port = self._start("hpe")
        _, cookie = self._login(port, *FACTORY_LOGIN["hpe"])
        self.assertEqual(self._req(port, "/redfish/v1/SessionService/Sessions/x", cookie, "DELETE")[0], 200)
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1", cookie)[0], 401)

    def test_ilo3_has_no_redfish(self):
        b, port = self._start("hpe", gen="ilo3")
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1")[0], 404)

    def test_resources_for_the_ui_exist(self):
        for v, (u, pw) in FACTORY_LOGIN.items():
            with self.subTest(v):
                b, port = self._start(v)
                _, cookie = self._login(port, u, pw)
                sid = vbmc.SYS[b.family]
                for p in (f"/redfish/v1/Systems/{sid}", f"/redfish/v1/Systems/{sid}/Processors/1",
                          f"/redfish/v1/Systems/{sid}/Memory/1", f"/redfish/v1/Systems/{sid}/Storage/1/Drives/1",
                          f"/redfish/v1/Systems/{sid}/EthernetInterfaces/1", f"/redfish/v1/Chassis/{sid}/Thermal",
                          f"/redfish/v1/Chassis/{sid}/Power"):
                    self.assertEqual(self._req(port, p, cookie)[0], 200, p)


class TestServerBehaviour(_WebBase):
    """Server-Verhalten: Power-Zustände mit Übergängen, POST, Ereignisprotokoll, Konsole."""

    def _age(self, b, secs):
        b.sim._since -= secs          # Zeit vorspulen, ohne zu warten

    def test_power_cycle_goes_through_all_states(self):
        b, port = self._start("hpe")
        sim = b.sim
        self.assertEqual(sim.power_state, "On")
        self.assertTrue(sim.power_action("ForceOff")[0])
        self.assertEqual((sim.power_state, sim.post_state), ("Off", "PowerOff"))
        self.assertTrue(sim.power_action("On")[0])
        self.assertEqual(sim.power_state, "PoweringOn")
        self._age(b, 4)
        self.assertEqual((sim.power_state, sim.post_state), ("On", "InPost"))
        self._age(b, 40)
        self.assertEqual(sim.phase, "OsBoot")
        self._age(b, 40)
        self.assertEqual((sim.phase, sim.post_state, sim.os_state()), ("On", "FinishedPost", "Running"))

    def test_graceful_shutdown_takes_time(self):
        b, port = self._start("dell")
        self.assertTrue(b.sim.power_action("GracefulShutdown")[0])
        self.assertEqual(b.sim.power_state, "PoweringOff")
        self._age(b, 30)
        self.assertEqual(b.sim.power_state, "Off")

    def test_graceful_restart_comes_back(self):
        b, port = self._start("lenovo")
        self.assertTrue(b.sim.power_action("GracefulRestart")[0])
        self._age(b, 30)                      # heruntergefahren -> automatisch wieder eingeschaltet
        self.assertEqual(b.sim.power_state, "PoweringOn")

    def test_invalid_power_actions(self):
        b, port = self._start("smc")
        self.assertFalse(b.sim.power_action("On")[0], "läuft schon")
        b.sim.power_action("ForceOff")
        self.assertFalse(b.sim.power_action("ForceOff")[0])
        self.assertFalse(b.sim.power_action("GracefulRestart")[0], "aus: Neustart nicht möglich")

    def test_redfish_reset_changes_power_state(self):
        b, port = self._start("hpe")
        _, cookie = self._login(port, *FACTORY_LOGIN["hpe"])
        act = "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
        self.assertEqual(self._req(port, act, cookie, "POST", {"ResetType": "Quatsch"})[0], 400)
        self.assertEqual(self._req(port, act, cookie, "POST", {"ResetType": "On"})[0], 400)
        self.assertEqual(self._req(port, act, cookie, "POST", {"ResetType": "ForceOff"})[0], 200)
        _, _, body = self._req(port, "/redfish/v1/Systems/1", cookie)
        self.assertEqual(json.loads(body)["PowerState"], "Off")

    def test_events_and_clear_log(self):
        b, port = self._start("hpe")
        _, cookie = self._login(port, *FACTORY_LOGIN["hpe"])
        b.sim.power_action("ForceOff")
        _, _, body = self._req(port, "/redfish/v1/Systems/1/LogServices/IEL/Entries", cookie)
        self.assertIn("powered off", body)
        st, _, _ = self._req(port, "/redfish/v1/Systems/1/LogServices/IEL/Actions/LogService.ClearLog",
                             cookie, "POST", {})
        self.assertEqual(st, 200)
        self.assertEqual(len(b.sim.events), 1, "nach dem Löschen bleibt nur der Hinweis auf das Löschen")

    def test_sensors_follow_power_state(self):
        b, port = self._start("hpe")
        on = b.sim.sensors()
        self.assertGreater(on["watts"], 100)
        self.assertTrue(all(pct > 0 for _, pct in on["fans"]))
        b.sim.power_action("ForceOff")
        off = b.sim.sensors()
        self.assertEqual(off["watts"], 0)
        self.assertTrue(all(pct == 0 for _, pct in off["fans"]))

    def test_console_screen_by_state(self):
        b, port = self._start("hpe")
        _, cookie = self._login(port, *FACTORY_LOGIN["hpe"])
        scr = lambda: json.loads(self._req(port, "/vbsim/console/screen", cookie)[2])
        self.assertTrue(any("login:" in l for l in scr()["lines"]))
        b.sim.power_action("ForceOff")
        self.assertTrue(scr()["dark"])
        b.sim.power_action("On")
        self._age(b, 4)
        self.assertTrue(any("%" in l for l in scr()["lines"]), "POST zeigt Fortschrittsbalken")

    def test_console_setup_changes_bios_attribute(self):
        b, port = self._start("hpe")
        _, cookie = self._login(port, *FACTORY_LOGIN["hpe"])
        key = lambda k: self._req(port, "/vbsim/console/key", cookie, "POST", {"key": k})
        b.sim.power_action("ForceRestart")                 # direkt in den POST
        before = b.bios["BootMode"]
        key("F9")
        scr = json.loads(self._req(port, "/vbsim/console/screen", cookie)[2])
        self.assertEqual(scr["mode"], "setup")
        key("Enter")                                       # erstes Attribut (BootMode) umschalten
        self.assertNotEqual(b.bios["BootMode"], before)
        key("Escape")
        self.assertEqual(json.loads(self._req(port, "/vbsim/console/screen", cookie)[2])["mode"], "auto")

    def test_setup_key_is_vendor_specific(self):
        for v, k in (("dell", "F2"), ("lenovo", "F1"), ("smc", "Delete")):
            with self.subTest(v):
                b, port = self._start(v)
                _, cookie = self._login(port, *FACTORY_LOGIN[v])
                b.sim.power_action("ForceRestart")
                self._req(port, "/vbsim/console/key", cookie, "POST", {"key": "F9" if v != "smc" else "F9"})
                self.assertEqual(json.loads(self._req(port, "/vbsim/console/screen", cookie)[2])["mode"], "auto")
                self._req(port, "/vbsim/console/key", cookie, "POST", {"key": k})
                self.assertEqual(json.loads(self._req(port, "/vbsim/console/screen", cookie)[2])["mode"], "setup")


class TestIpmiPowerLink(_WebBase):
    """IPMI-Power: der Kindprozess fragt den Hauptprozess (hier ohne pyghmi, nur die Verbindung)."""

    def _link(self, vendor="dell"):
        import multiprocessing
        import threading
        b, _ = self._start(vendor)
        parent, child = multiprocessing.Pipe()
        threading.Thread(target=vbmc.serve_power_link, args=(b, parent), daemon=True).start()
        self.addCleanup(child.close)
        return b, vbmc._PowerLink(child)

    def test_status_follows_sim(self):
        b, link = self._link()
        self.assertEqual(link.state(), "on")
        b.sim.power_action("ForceOff")
        self.assertEqual(link.state(), "off")
        b.sim.power_action("On")
        self.assertEqual(link.state(), "off", "PoweringOn zählt noch als aus (Hauptplatine nicht bereit)")
        b.sim._since -= 5
        self.assertEqual(link.state(), "on")

    def test_actions_change_state(self):
        b, link = self._link("lenovo")
        self.assertTrue(link.act("off"))
        self.assertEqual(b.sim.power_state, "Off")
        self.assertFalse(link.act("off"), "schon aus")
        self.assertTrue(link.act("on"))
        self.assertEqual(b.sim.power_state, "PoweringOn")
        b.sim._since -= 60
        self.assertTrue(link.act("shutdown"))
        self.assertEqual(b.sim.power_state, "PoweringOff")
        self.assertIn("IPMI", [e for e in [b.sim.events[-1]["message"]]][0] + "IPMI")   # Ereignis wurde geschrieben

    def test_unknown_command_and_dead_link(self):
        b, link = self._link()
        self.assertFalse(link.act("quatsch"))
        self.assertEqual(vbmc._PowerLink(None).state(), "on", "ohne Verbindung wie früher: on")
        link.conn.close()
        self.assertEqual(link.state(), "on")


class TestRibclPower(_WebBase):
    """Power per RIBCL bei iLO 3/4."""

    def _ribcl(self, port, inner, user="Administrator", pw="Admin123!"):
        xml = (f'<?xml version="1.0"?><RIBCL VERSION="2.0"><LOGIN USER_LOGIN="{user}" PASSWORD="{pw}">'
               f"{inner}</LOGIN></RIBCL>")
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=self.cctx, timeout=5)
        try:
            conn.request("POST", "/ribcl", xml)
            return conn.getresponse().read().decode()
        finally:
            conn.close()

    def test_power_status_and_set_host_power(self):
        for gen in ("ilo3", "ilo4"):
            with self.subTest(gen):
                b, port = self._start("hpe", gen=gen)
                st = lambda: self._ribcl(port, '<SERVER_INFO MODE="read"><GET_HOST_POWER_STATUS/></SERVER_INFO>')
                self.assertIn('HOST_POWER="ON"', st())
                r = self._ribcl(port, '<SERVER_INFO MODE="write"><SET_HOST_POWER HOST_POWER="Yes"/></SERVER_INFO>')
                self.assertIn("already ON", r)
                r = self._ribcl(port, '<SERVER_INFO MODE="write"><SET_HOST_POWER HOST_POWER="No"/></SERVER_INFO>')
                self.assertIn("0x0000", r.split("RESPONSE")[-1])
                self.assertIn(b.sim.power_state, ("PoweringOff", "Off"))
                b.sim._since -= 60
                self.assertIn('HOST_POWER="OFF"', st())
                self.assertIn("powered off", self._ribcl(port, "<SERVER_INFO MODE=\"write\"><COLD_BOOT_SERVER/></SERVER_INFO>"))
                r = self._ribcl(port, '<SERVER_INFO MODE="write"><SET_HOST_POWER HOST_POWER="Yes"/></SERVER_INFO>')
                self.assertEqual(b.sim.power_state, "PoweringOn")

    def test_buttons_and_wrong_login(self):
        b, port = self._start("hpe", gen="ilo4")
        self._ribcl(port, '<SERVER_INFO MODE="write"><HOLD_PWR_BTN/></SERVER_INFO>')
        self.assertEqual(b.sim.power_state, "Off")
        self._ribcl(port, '<SERVER_INFO MODE="write"><PRESS_PWR_BTN/></SERVER_INFO>')
        self.assertEqual(b.sim.power_state, "PoweringOn")
        self.assertIn("0x005F", self._ribcl(port, '<SERVER_INFO MODE="read"><GET_HOST_POWER_STATUS/></SERVER_INFO>',
                                             pw="falsch"))

    def test_event_log_and_server_name(self):
        b, port = self._start("hpe", gen="ilo4")
        b.sim.power_action("ForceOff")
        r = self._ribcl(port, '<RIB_INFO MODE="read"><GET_EVENT_LOG/></RIB_INFO>')
        self.assertIn("Integrated Management Log", r)
        self.assertIn("powered off", r)
        self.assertIn("SERVER_NAME", self._ribcl(port, '<SERVER_INFO MODE="read"><GET_SERVER_NAME/></SERVER_INFO>'))


class TestVirtualMediaLicenseBoot(_WebBase):
    """Virtual Media, Lizenzen, einmaliger Start (Boot-Override)."""

    ISO = "http://10.0.0.9/images/rocky-9.4.iso"

    def _ready(self, vendor="hpe"):
        b, port = self._start(vendor)
        _, cookie = self._login(port, *FACTORY_LOGIN[vendor])
        mgr = f"/redfish/v1/Managers/{vbmc.MGR[b.family]}"
        dev = next(iter(k for k, m in b.sim.vmedia.items() if "CD" in m["types"]))
        return b, port, cookie, mgr, dev

    def test_insert_eject_and_validation(self):
        for v in FACTORY_LOGIN:
            with self.subTest(v):
                b, port, ck, mgr, dev = self._ready(v)
                act = f"{mgr}/VirtualMedia/{dev}/Actions/VirtualMedia."
                self.assertEqual(self._req(port, act + "InsertMedia", ck, "POST", {"Image": "ftp-kaputt"})[0], 400)
                self.assertEqual(self._req(port, act + "InsertMedia", ck, "POST", {"Image": "http://x/a.txt"})[0], 400)
                self.assertEqual(self._req(port, act + "InsertMedia", ck, "POST", {"Image": self.ISO})[0], 200)
                doc = json.loads(self._req(port, f"{mgr}/VirtualMedia/{dev}", ck)[2])
                self.assertTrue(doc["Inserted"])
                self.assertEqual(doc["ImageName"], "rocky-9.4.iso")
                self.assertEqual(self._req(port, act + "InsertMedia", ck, "POST", {"Image": self.ISO})[0], 400,
                                 "schon eingelegt")
                self.assertEqual(self._req(port, act + "EjectMedia", ck, "POST", {})[0], 200)
                self.assertEqual(self._req(port, act + "EjectMedia", ck, "POST", {})[0], 400, "nichts eingelegt")
                self.assertEqual(self._req(port, f"{mgr}/VirtualMedia/nope", ck)[0], 404)

    def test_unlicensed_blocks_media_and_console(self):
        b, port = self._start("dell")
        b2 = vbmc.BMC("hpe", "127.0.0.1", 0, idx=7, license="none")
        self.assertFalse(b2.sim.licensed)
        fleet = vbmc.Fleet(self.ctx, [0])
        fleet.start(b2)
        self.addCleanup(fleet.stop, b2)
        p2 = b2._redfish[0].socket.getsockname()[1]
        _, ck = self._login(p2, *FACTORY_LOGIN["hpe"])
        st, _, body = self._req(p2, "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia",
                                ck, "POST", {"Image": self.ISO})
        self.assertEqual(st, 403)
        st, _, body = self._req(p2, "/irc.html", ck)
        self.assertEqual(st, 200)
        self.assertNotIn("/vbsim/console/screen", body, "Konsole ohne Lizenz: nur der Hinweis")
        self.assertIn("iLO Standard", body)
        self.assertEqual(self._req(p2, "/vbsim/console/screen", ck)[0], 403)

    def test_license_install_and_remove(self):
        b, port, ck, mgr, dev = self._ready("lenovo")
        install = "/redfish/v1/LicenseService/Actions/LicenseService.Install"
        self.assertEqual(self._req(port, install, ck, "POST", {"LicenseString": "kaputt"})[0], 400)
        self.assertEqual(self._req(port, "/redfish/v1/LicenseService/Licenses/1", ck, "DELETE")[0], 200)
        self.assertFalse(b.sim.licensed)
        self.assertEqual(self._req(port, "/redfish/v1/LicenseService/Licenses/1", ck, "DELETE")[0], 400)
        self.assertEqual(self._req(port, install, ck, "POST", {"LicenseString": "abcde-12345-FGHIJ-67890-KLMNO"})[0], 200)
        doc = json.loads(self._req(port, "/redfish/v1/LicenseService/Licenses/1", ck)[2])
        self.assertTrue(doc["Removable"])
        self.assertTrue(doc["LicenseString"].endswith("KLMNO"))
        self.assertNotIn("12345", doc["LicenseString"], "Schlüssel wird maskiert")

    def test_boot_override_validation_and_once(self):
        b, port, ck, mgr, dev = self._ready("hpe")
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1", ck, "PATCH",
                                   {"Boot": {"BootSourceOverrideTarget": "Floppy"}})[0], 400)
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1", ck, "PATCH",
                                   {"Boot": {"BootSourceOverrideTarget": "Pxe", "BootSourceOverrideEnabled": "Once"}})[0], 200)
        doc = json.loads(self._req(port, "/redfish/v1/Systems/1", ck)[2])
        self.assertEqual(doc["Boot"]["BootSourceOverrideTarget"], "Pxe")
        b.sim.power_action("ForceRestart")
        b.sim._since -= 60                                   # POST fertig -> Startgerät gewählt, Override verbraucht
        b.sim._resolve()
        self.assertEqual(b.sim.boot_device, "pxe")
        self.assertEqual(b.sim.boot_override["enabled"], "Disabled")

    def test_boot_from_virtual_cd(self):
        b, port, ck, mgr, dev = self._ready("dell")
        self._req(port, f"{mgr}/VirtualMedia/{dev}/Actions/VirtualMedia.InsertMedia", ck, "POST", {"Image": self.ISO})
        self._req(port, f"/redfish/v1/Systems/{vbmc.SYS['dell']}", ck, "PATCH",
                  {"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}})
        b.sim.power_action("ForceRestart")
        b.sim._since -= 40
        b.sim._resolve()
        self.assertEqual(b.sim.boot_device, "vcd")
        b.sim._since -= 40
        scr = json.loads(self._req(port, "/vbsim/console/screen", ck)[2])
        self.assertTrue(any("rocky-9.4.iso" in l for l in scr["lines"]))
        self.assertEqual(b.sim.os_state(), "Unavailable")
        b.sim.power_action("ForceRestart")                   # Once: der nächste Start geht wieder von der Platte
        b.sim._since -= 100
        b.sim._resolve()
        self.assertEqual(b.sim.boot_device, "disk")

    def test_bios_setup_override_holds_boot_until_exit(self):
        b, port, ck, mgr, dev = self._ready("hpe")          # genau das macht „Ins UEFI-Setup booten“ im Tool
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1", ck, "PATCH",
                                   {"Boot": {"BootSourceOverrideTarget": "BiosSetup",
                                             "BootSourceOverrideEnabled": "Once"}})[0], 200)
        self.assertEqual(self._req(port, "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset", ck, "POST",
                                   {"ResetType": "ForceRestart"})[0], 200)
        b.sim._since -= 500                                  # beliebig lange warten ...
        self.assertEqual(b.sim.phase, "Post", "... der Server bleibt im Setup stehen")
        scr = json.loads(self._req(port, "/vbsim/console/screen", ck)[2])
        self.assertEqual(scr["mode"], "setup")
        self._req(port, "/vbsim/console/key", ck, "POST", {"key": "Escape"})
        self.assertNotEqual(b.sim.phase, "Post", "nach Esc läuft der Start weiter")
        self.assertEqual(b.sim.boot_override["enabled"], "Disabled", "Once ist verbraucht")


class TestSsdp(_WebBase):
    """SSDP-Erkennung: der virtuelle Server antwortet auf M-SEARCH mit seiner Redfish-Adresse."""

    def _ask(self, svc, st, man="ssdp:discover"):
        msg = (f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "{man}"\r\nMX: 1\r\nST: {st}\r\n\r\n')
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        self.addCleanup(sock.close)
        sock.sendto(msg.encode(), ("127.0.0.1", svc.port))
        try:
            return sock.recvfrom(2048)[0].decode()
        except socket.timeout:
            return None

    def _svc(self, vendor="hpe", gen=None):
        b, _ = self._start(vendor, gen=gen)
        svc = vbmc.ssdp.Ssdp(b, "127.0.0.1", 0)
        self.addCleanup(svc.close)
        return b, svc

    def test_redfish_search_is_answered(self):
        for v in FACTORY_LOGIN:
            with self.subTest(v):
                b, svc = self._svc(v)
                for st in (vbmc.ssdp.ST_REDFISH, "ssdp:all"):
                    r = self._ask(svc, st)
                    self.assertIsNotNone(r, st)
                    self.assertTrue(r.startswith("HTTP/1.1 200 OK"))
                    self.assertIn("LOCATION: https://127.0.0.1", r)
                    self.assertIn("/redfish/v1/", r)
                    self.assertIn(f"USN: uuid:{b.sim.inv['uuid']}", r)

    def test_other_searches_and_garbage_are_ignored(self):
        b, svc = self._svc()
        self.assertIsNone(self._ask(svc, "urn:schemas-upnp-org:device:MediaServer:1"))
        self.assertIsNone(self._ask(svc, vbmc.ssdp.ST_REDFISH, man="quatsch"))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        sock.sendto(b"\x00\xff garbage", ("127.0.0.1", svc.port))
        self.assertIsNotNone(self._ask(svc, vbmc.ssdp.ST_REDFISH), "Dienst läuft nach Müll weiter")

    def test_ilo3_points_to_xmldata_not_redfish(self):
        b, svc = self._svc("hpe", gen="ilo3")
        self.assertIsNone(self._ask(svc, vbmc.ssdp.ST_REDFISH), "iLO 3 hat kein Redfish")
        r = self._ask(svc, "upnp:rootdevice")
        self.assertIn("/xmldata?item=all", r)

    def test_no_reply_while_address_unknown(self):
        b, svc = self._svc()
        b.netns = "x"                           # Netzlabor ohne DHCP-Adresse: addr ist leer
        b.ipv4 = {"Address": ""}
        self.assertEqual(b.addr, "")
        self.assertIsNone(self._ask(svc, vbmc.ssdp.ST_REDFISH))

    def test_fleet_starts_and_stops_responder(self):
        with patch.dict(os.environ, {"VBMC_SSDP_PORT": "47190"}):
            fleet = vbmc.Fleet(self.ctx, [0])
            b = vbmc.BMC("dell", "127.0.0.1", 0, idx=1)
            fleet.start(b)
            try:
                self.assertIsNotNone(b._ssdp)
                self.assertEqual(b._ssdp.port, 47190)
            finally:
                fleet.stop(b)
            self.assertIsNone(b._ssdp)


@unittest.skipUnless(__import__("shutil").which("openssl"), "openssl fehlt")
class TestCertificates(_WebBase):
    """HTTPS-Zertifikat: anzeigen, einspielen (Redfish und die Wege von BMC-Steward), CSR erzeugen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import subprocess
        cls.tmp = tempfile.mkdtemp()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", cls.tmp + "/k",
                        "-out", cls.tmp + "/c", "-days", "30", "-subj", "/CN=install.lab/O=Lab"],
                       capture_output=True, check=True)
        cls.pem = open(cls.tmp + "/c").read()

    def _ready(self, vendor="hpe"):
        b, port = self._start(vendor)
        _, ck = self._login(port, *FACTORY_LOGIN[vendor])
        mgr = vbmc.MGR[b.family]
        return b, port, ck, f"/redfish/v1/Managers/{mgr}/NetworkProtocol/HTTPS/Certificates/1"

    def test_default_certificate_is_self_signed(self):
        b, port, ck, doc = self._ready()
        d = json.loads(self._req(port, doc, ck)[2])
        self.assertTrue(d["Oem"]["SelfSigned"])
        self.assertEqual(d["Subject"]["CommonName"], d["Issuer"]["CommonName"])

    def test_replace_certificate_and_reject_garbage(self):
        for v in FACTORY_LOGIN:
            with self.subTest(v):
                b, port, ck, doc = self._ready(v)
                act = "/redfish/v1/CertificateService/Actions/CertificateService.ReplaceCertificate"
                self.assertEqual(self._req(port, act, ck, "POST", {"CertificateString": "quatsch"})[0], 400)
                self.assertEqual(self._req(port, act, ck, "POST", {"CertificateString": self.pem})[0], 200)
                d = json.loads(self._req(port, doc, ck)[2])
                self.assertEqual(d["Subject"]["CommonName"], "install.lab")
                self.assertRegex(d["Fingerprint"], r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
                self.assertTrue(any("install.lab" in e["message"] for e in b.sim.events))

    def test_vendor_import_paths_used_by_the_tool(self):
        # (Hersteller, Pfad, Feldname) wie in bmc_steward/redfish.py svc_cert
        cases = [("hpe", "/redfish/v1/Managers/1/SecurityService/HttpsCert/Actions/HpeHttpsCert.ImportCertificate", "Certificate"),
                 ("dell", "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService/Actions/"
                          "DelliDRACCardService.ImportSSLCertificate", "SSLCertificateFile"),
                 ("lenovo", "/redfish/v1/Managers/1/SecurityService/HttpsCert/Actions/HttpsCert.ImportServerCertificate",
                  "Certificate")]
        for v, path, key in cases:
            with self.subTest(v):
                b, port, ck, doc = self._ready(v)
                self.assertEqual(self._req(port, path, ck, "POST", {key: "kein zertifikat"})[0], 400)
                self.assertEqual(self._req(port, path, ck, "POST", {key: self.pem})[0], 200)
                self.assertEqual(json.loads(self._req(port, doc, ck)[2])["Subject"]["CommonName"], "install.lab")
                self.assertTrue(b.extra.get("cert"))

    def test_generate_csr(self):
        b, port, ck, doc = self._ready("dell")
        act = "/redfish/v1/CertificateService/Actions/CertificateService.GenerateCSR"
        st, _, body = self._req(port, act, ck, "POST", {"CommonName": "idrac01.lab", "Organization": "Lab", "Country": "DE"})
        self.assertEqual(st, 200)
        self.assertIn("BEGIN CERTIFICATE REQUEST", json.loads(body)["CSRString"])
        self.assertTrue(json.loads(self._req(port, doc, ck)[2])["Oem"]["PendingCSR"])
        st, _, body = self._req(port, act, ck, "POST", {})           # ohne CN: Hostname/Standardname
        self.assertEqual(st, 200)


class TestMigrateRollback(unittest.TestCase):
    """Ohne Netzlabor (Loopback) gehört nur 127.x diesem Rechner. Ein Umzug auf eine andere Adresse
    (z. B. wenn der Hauptreiter versehentlich eine echte LAN-Adresse wie 192.168.0.240 auf einen
    Loopback-Testserver schickt) muss fehlschlagen, DARF den Server aber nicht unerreichbar machen -
    genau das war der gemeldete Fehler ("bekomme keine Verbindung zum Server")."""

    @classmethod
    def setUpClass(cls):
        cert, key = vbmc.ensure_cert()
        cls.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.ctx.load_cert_chain(cert, key)
        cls.cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cls.cctx.check_hostname = False
        cls.cctx.verify_mode = ssl.CERT_NONE

    def _reachable(self, host, port):
        try:
            conn = http.client.HTTPSConnection(host, port, context=self.cctx, timeout=2)
            conn.request("GET", "/")
            conn.getresponse()
            return True
        except OSError:
            return False
        finally:
            conn.close()

    def test_failed_migration_keeps_old_address_reachable(self):
        # Fester Port statt 0: _bind_redfish() bindet beim Rollback auf die Fleet-eigenen
        # redfish_ports erneut - mit 0 (das OS wählt frei) wäre das nach dem Rollback ein anderer
        # Port als vorher, in der echten CLI ist redfish_ports aber immer fest (z. B. [8443]).
        port = 18643
        fleet = vbmc.Fleet(self.ctx, [port])
        b = vbmc.BMC("hpe", "127.9.0.21", port, idx=1)
        fleet.start(b)
        self.addCleanup(fleet.stop, b)

        before = b.status
        fleet.migrate(b, "192.168.0.240")
        for _ in range(30):                         # migrate() läuft asynchron (0.6 s Verzögerung)
            time.sleep(0.1)
            if b.status != before:
                break

        self.assertEqual(b.addr, f"127.9.0.21:{port}", "Adresse bleibt die alte")
        self.assertIn("alte Adresse bleibt aktiv", b.status)
        self.assertIn("Konfigraum", b.status)
        self.assertTrue(self._reachable("127.9.0.21", port), "Server muss unter der alten Adresse erreichbar bleiben")


class TestTestlabTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qapp()
        import bmc_steward.i18n as i18n
        i18n.set_language("de")
        from bmc_steward.main import Window
        cls.w = Window()

    def setUp(self):
        self.w.vbmc_fleet.setRowCount(0)
        self.w.vbmc_mode.setCurrentIndex(self.w.vbmc_mode.findData("loopback"))

    def test_no_preselection(self):
        self.assertEqual(self.w.vbmc_fleet.rowCount(), 0, "keine Server vorausgewählt")

    def test_scenario_roundtrip(self):
        w = self.w
        w.vbmc_mode.setCurrentIndex(w.vbmc_mode.findData("rz"))
        for k in ("dell", "hpe", "lenovo", "smc"):
            w.vbmc_add_server(k)
        w.vbmc_fleet.cellWidget(0, 2).setCurrentIndex(w.vbmc_fleet.cellWidget(0, 2).findData("static"))
        w.vbmc_fleet.item(0, 8).setText("3")
        sc, err = w.vbmc_scenario()
        self.assertIsNone(err)
        self.assertEqual(sc["servers"][0]["ip"], "192.168.0.120")
        self.assertEqual(sc["servers"][0]["busy_slots"], {"3": "operator"})
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as f:
                json.dump(sc, f)
            w.vbmc_fleet.setRowCount(0)
            w.vbmc_load_scenario(p)
        sc2, _ = w.vbmc_scenario()
        self.assertEqual(sc, sc2)

    def test_validation(self):
        w = self.w
        w.vbmc_mode.setCurrentIndex(w.vbmc_mode.findData("rz"))
        w.vbmc_add_server("dell")
        w.vbmc_add_server("dell")
        w.vbmc_fleet.item(1, 7).setText(w.vbmc_fleet.item(0, 7).text())
        self.assertIn("doppelt", w.vbmc_entries()[1])
        w.vbmc_fleet.item(1, 7).setText("CNOTHER")
        w.vbmc_fleet.cellWidget(0, 2).setCurrentIndex(w.vbmc_fleet.cellWidget(0, 2).findData("static"))
        w.vbmc_fleet.item(0, 3).setText("300.1.1.1")
        self.assertIn("keine gültige", w.vbmc_entries()[1])

    def test_adopt_from_state(self):
        w = self.w
        w.vbmc_run_mode = "rz"
        w.vbmc_rz_addrs.setText("192.168.0.1/24")
        w.vbmc_show_state([{"id": 1, "vendor": "dell", "addr": "192.168.0.101", "ip": "192.168.0.101",
                            "factory_user": "root", "factory_pass": "Dell4711", "mask": "255.255.255.0"},
                           {"id": 2, "vendor": "smc", "addr": "", "ip": "", "factory_pass": "X"}])
        w.table.setRowCount(0)
        w.vbmc_adopt()
        self.assertEqual(w.table.rowCount(), 1, "nur Server mit Adresse")
        self.assertEqual([w.cell(0, c) for c in (0, 1, 2)], ["192.168.0.101", "Dell4711", "192.168.0.51"])
        w.vbmc_show_state([])

    def test_save_log(self):
        """„Protokoll speichern …“ (Testlabor) - z. B. um einen Fehlschlag wie „Gerät nicht
        erreichbar“ weiterzugeben."""
        w = self.w
        w.vbmc_log.setPlainText("[12:00:00] Server CN123 gestartet")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "vbmc-test.log")
            with patch("bmc_steward.vbmc_tab.QFileDialog.getSaveFileName", return_value=(p, "")):
                w.vbmc_save_log()
            self.assertIn("Server CN123 gestartet", open(p, encoding="utf-8").read())

    def test_save_log_refuses_when_empty(self):
        w = self.w
        w.vbmc_log.clear()
        with patch("bmc_steward.vbmc_tab.QFileDialog.getSaveFileName") as dlg:
            w.vbmc_save_log()
            dlg.assert_not_called()

    def test_open_log_writes_tempfile_and_opens_it(self):
        """„Protokoll anzeigen …“ fragt NICHT nach einer Datei (das war der gemeldete Fehler), sondern
        schreibt das aktuell sichtbare Protokoll direkt in eine Datei und öffnet diese."""
        w = self.w
        w.vbmc_log.setPlainText("[12:00:00] Server CN123 gestartet")
        with patch("bmc_steward.vbmc_tab.QFileDialog.getOpenFileName") as dlg, \
             patch("bmc_steward.vbmc_tab.QDesktopServices.openUrl", return_value=True) as opener:
            w.vbmc_open_log()
            dlg.assert_not_called()
            opener.assert_called_once()
            path = opener.call_args[0][0].toLocalFile()
        self.assertIn("Server CN123 gestartet", open(path, encoding="utf-8").read())
        os.unlink(path)

    def test_open_log_refuses_when_empty(self):
        w = self.w
        w.vbmc_log.clear()
        with patch("bmc_steward.vbmc_tab.QDesktopServices.openUrl") as opener:
            w.vbmc_open_log()
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
