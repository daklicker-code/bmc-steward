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
import re
import socket
import ssl
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

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


class TestConsoleUi(unittest.TestCase):
    """Eingebettete Web-Konsole: Anmeldung (Cookie, unabhängig von Basic-Auth), Vorschau, UEFI-Seite -
    über einen echten HTTPS-Client im selben Prozess (reines TCP-Loopback ist in manchen Sandboxes
    zwischen getrennten Prozessen nicht erreichbar, im selben Prozess aber immer)."""

    @classmethod
    def setUpClass(cls):
        cert, key = vbmc.ensure_cert()
        cls.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.ctx.load_cert_chain(cert, key)
        cls.cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cls.cctx.check_hostname = False
        cls.cctx.verify_mode = ssl.CERT_NONE

    def _start(self, vendor, idx=1):
        fleet = vbmc.Fleet(self.ctx, [0])            # Port 0: das OS wählt einen freien
        b = vbmc.BMC(vendor, "127.0.0.1", 0, idx=idx)
        fleet.start(b)
        self.addCleanup(fleet.stop, b)
        port = b._redfish[0].socket.getsockname()[1]
        return b, port

    def _req(self, port, path, cookie=None, user=None, pw=None):
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=self.cctx, timeout=5)
        try:
            if user:
                sep = "&" if "?" in path else "?"
                path += f"{sep}user={user}&pass={pw}"
            conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def test_login_form_shown_when_unauthed(self):
        b, port = self._start("dell")
        st, _, body = self._req(port, "/")
        self.assertEqual(st, 200)
        self.assertIn("Anmelden", body)
        self.assertIn("type=password", body)

    def test_wrong_password_shows_error(self):
        b, port = self._start("dell")
        st, _, body = self._req(port, "/", user="root", pw="falsch")
        self.assertEqual(st, 200)
        self.assertIn("falsch", body)

    def test_login_sets_cookie_and_redirects_to_console_path(self):
        b, port = self._start("dell")
        st, h, _ = self._req(port, "/", user="root", pw="calvin")
        self.assertEqual(st, 302)
        self.assertEqual(h.get("Location"), "/console")
        self.assertIn(f"vbuisess={b.idx}", h.get("Set-Cookie", ""))

    def test_console_path_requires_cookie(self):
        b, port = self._start("dell")
        st, h, _ = self._req(port, "/console")
        self.assertEqual(st, 302)
        self.assertEqual(h.get("Location"), "/")

    def test_preview_and_kvm_with_cookie(self):
        b, port = self._start("dell")
        _, h, _ = self._req(port, "/", user="root", pw="calvin")
        cookie = h["Set-Cookie"].split(";")[0]
        st, _, body = self._req(port, "/console", cookie)
        self.assertEqual(st, 200)
        self.assertIn("vbmc-kvm", body, "Vorschau öffnet die UEFI-Seite per window.open()")
        st, _, body = self._req(port, "/vbmc-kvm", cookie)
        self.assertEqual(st, 200)
        self.assertIn("UEFI-Setup-Utility", body)
        self.assertIn(b.serial, body)

    def test_kvm_reflects_bios_attribute_changes(self):
        b, port = self._start("dell")
        b.bios["SecureBoot"] = "Enabled"
        _, h, _ = self._req(port, "/", user="root", pw="calvin")
        cookie = h["Set-Cookie"].split(";")[0]
        _, _, body = self._req(port, "/vbmc-kvm", cookie)
        self.assertIn("Enabled", body)

    def test_lenovo_console_path_is_root(self):
        b, port = self._start("lenovo", idx=2)
        st, h, _ = self._req(port, "/", user="USERID", pw="PASSW0RD")
        self.assertEqual(st, 302)
        self.assertEqual(h.get("Location"), "/")
        st, _, body = self._req(port, "/", cookie=f"vbuisess={b.idx}")
        self.assertEqual(st, 200)
        self.assertIn("vbmc-kvm", body)


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

    def test_save_and_open_log(self):
        """Die Knöpfe „Protokoll öffnen/speichern“ (Testlabor) - z. B. um einen Fehlschlag wie
        „Gerät nicht erreichbar“ weiterzugeben."""
        w = self.w
        w.vbmc_log.setPlainText("[12:00:00] Server CN123 gestartet")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "vbmc-test.log")
            with patch("bmc_steward.vbmc_tab.QFileDialog.getSaveFileName", return_value=(p, "")):
                w.vbmc_save_log()
            self.assertIn("Server CN123 gestartet", open(p, encoding="utf-8").read())
            w.vbmc_log.clear()
            with patch("bmc_steward.vbmc_tab.QFileDialog.getOpenFileName", return_value=(p, "")):
                w.vbmc_open_log()
            self.assertIn("Server CN123 gestartet", w.vbmc_log.toPlainText())

    def test_save_log_refuses_when_empty(self):
        w = self.w
        w.vbmc_log.clear()
        with patch("bmc_steward.vbmc_tab.QFileDialog.getSaveFileName") as dlg:
            w.vbmc_save_log()
            dlg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
