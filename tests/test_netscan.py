"""Netzsuche nach BMCs (bmc_steward.netscan): Zielliste, Erkennung ohne Anmeldung, Läufe gegen die
DMTF-Mockups (echte HTTPS-Antworten auf 127.0.0.1) und der IPMI-Ping gegen einen kleinen UDP-Antworter.
Es darf nie etwas anderes als ein GET ohne Zugangsdaten gesendet werden."""
import ipaddress
import socket
import threading
import unittest
import unittest.mock
from tests._support import qapp, need

qapp()
from tests._dmtf_mock import DmtfMockServer
from bmc_steward import netscan


class TestTargets(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(netscan.parse_targets("10.0.0.0/30"), ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(netscan.parse_targets("10.0.0.5-7, 10.0.0.6"), ["10.0.0.5", "10.0.0.6", "10.0.0.7"])
        self.assertEqual(netscan.parse_targets("10.0.0.5-10.0.1.1")[-1], "10.0.1.1")
        self.assertEqual(netscan.parse_targets("192.168.1.9\n192.168.1.9"), ["192.168.1.9"])
        self.assertEqual(netscan.parse_targets("10.0.0.0/32"), ["10.0.0.0"])

    def test_limit_and_garbage(self):
        with self.assertRaises(netscan.TooMany):
            netscan.parse_targets("10.0.0.0/16", limit=1000)
        with self.assertRaises(netscan.TooMany):
            netscan.parse_targets("10.0.0.1-10.0.9.1", limit=100)
        for bad in ("abc", "10.0.0.9-3", "::1", "10.0.0.0/8x", "300.1.1.1"):
            with self.assertRaises(ValueError, msg=bad):
                netscan.parse_targets(bad)

    def test_ports(self):
        self.assertEqual(netscan.parse_ports(" 443, 8443 443"), (443, 8443))
        self.assertEqual(netscan.parse_ports(""), (443,))
        for bad in ("0", "70000", "x"):
            with self.assertRaises(ValueError):
                netscan.parse_ports(bad)

    def test_suggestions_shrink_big_networks(self):
        ifs = [ipaddress.ip_interface("192.168.5.20/24"), ipaddress.ip_interface("10.8.3.4/16"),
               ipaddress.ip_interface("169.254.1.1/16")]
        self.assertEqual(netscan.suggest_ranges(ifs), ["192.168.5.0/24", "10.8.3.0/24"])


class TestClassify(unittest.TestCase):
    def test_vendors(self):
        c = netscan.classify_root
        self.assertEqual(c({"Vendor": "Dell Inc.", "Product": "Integrated Dell Remote Access Controller",
                            "UUID": "u"})[0], "dell")
        self.assertEqual(c({"Oem": {"Supermicro": {}}})[0], "smc")
        self.assertEqual(c({"Oem": {"Lenovo": {}}})[0], "lenovo")
        self.assertEqual(c({"Oem": {"Hpe": {"Manager": [{"Model": "iLO 5", "SerialNumber": "CZ123"}]}}})[:3],
                         ("hpe", "iLO 5", "CZ123"))
        self.assertEqual(c({"Oem": {"Hp": {}}})[:2], ("hpe_old", "iLO 4"))
        self.assertEqual(c({"Vendor": "Acme"})[0], "unbekannt")
        self.assertEqual(c(None)[0], "unbekannt")
        self.assertEqual(c([])[0], "unbekannt")

    def test_xmldata(self):
        xml = ("<RIMP><HSI><SBSN>CZ3333ABC</SBSN></HSI><MP><PN>Integrated Lights-Out 3 (iLO 3)</PN>"
               "<FWRI>1.89</FWRI></MP></RIMP>")
        self.assertEqual(netscan.parse_xmldata(xml), ("hpe_old", "iLO 3 (1.89)", "CZ3333ABC"))
        self.assertEqual(netscan.parse_xmldata("<x/>")[0], "unbekannt")


@need("openssl")
class TestScanAgainstMockups(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = {}
        for v in ("dell", "hpe", "lenovo", "smc"):
            s = DmtfMockServer(v)
            s.start()
            cls.servers[v] = s

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers.values():
            s.stop()

    def test_each_vendor_recognised_without_login(self):
        for v, s in self.servers.items():
            with self.subTest(v):
                seen = []
                rows = netscan.scan(["127.0.0.1"], ports=(s.port,), ipmi=False, on_found=seen.append)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["vendor"], v)
                self.assertEqual(rows[0]["port"], s.port)
                self.assertTrue(rows[0]["redfish"])
                self.assertTrue(rows[0]["uuid"])
                self.assertEqual(len(rows[0]["fp"]), 64)
                self.assertEqual(len(seen), 1)

    def test_only_unauthenticated_get_is_sent(self):
        s = self.servers["dell"]
        log, saved = [], {}
        handler = s.httpd.RequestHandlerClass
        for meth in ("do_GET", "do_POST", "do_PATCH", "do_DELETE", "do_PUT"):
            orig = getattr(handler, meth, None)
            saved[meth] = orig

            def wrap(self, _m=meth, _f=orig):
                log.append((_m, self.path, self.headers.get("Authorization"), self.headers.get("X-Auth-Token")))
                return _f(self) if _f else self.send_error(501)
            setattr(handler, meth, wrap)
        try:
            netscan.scan(["127.0.0.1"], ports=(s.port,), ipmi=False)
        finally:
            for meth, orig in saved.items():
                if orig is None:
                    delattr(handler, meth)
                else:
                    setattr(handler, meth, orig)
        self.assertTrue(log)
        for m, path, auth, token in log:
            self.assertEqual(m, "do_GET")
            self.assertIsNone(auth)
            self.assertIsNone(token)
            self.assertTrue(path.startswith("/redfish/v1") or path.startswith("/xmldata"))

    def test_closed_port_and_non_bmc_are_not_listed(self):
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        port = dead.getsockname()[1]
        dead.close()                                        # Port frei = nichts hört zu
        self.assertEqual(netscan.scan(["127.0.0.1"], ports=(port,), ipmi=False), [])

    def test_stop_and_progress(self):
        s = self.servers["hpe"]
        prog = []
        rows = netscan.scan(["127.0.0.1"] * 3, ports=(s.port,), ipmi=False, on_progress=lambda d, t: prog.append((d, t)))
        self.assertEqual(prog[-1], (3, 3))
        stop = netscan.scan(["127.0.0.1"], ports=(s.port,), ipmi=False, stopped=lambda: True)
        self.assertEqual(stop, [])

    def test_csv(self):
        rows = netscan.scan(["127.0.0.1"], ports=(self.servers["lenovo"].port,), ipmi=False)
        text = netscan.rows_to_csv(rows)
        self.assertTrue(text.startswith("IP;Hersteller;"))
        self.assertIn("127.0.0.1;Lenovo XCC", text)


class TestRmcp(unittest.TestCase):
    def test_ping_is_valid_asf_presence_ping(self):
        p = netscan.RMCP_PING
        self.assertEqual(len(p), 12)
        self.assertEqual(p[:4], b"\x06\x00\xff\x06")        # RMCP-Kopf, Klasse ASF
        self.assertEqual(p[4:8], b"\x00\x00\x11\xbe")       # IANA 4542
        self.assertEqual(p[8], 0x80)                        # Presence Ping

    def test_capabilities_request_is_wellformed(self):
        p = netscan.IPMI_CAPS
        self.assertEqual(p[:4], b"\x06\x00\xff\x07")        # RMCP-Kopf, Klasse IPMI
        self.assertEqual(p[4], 0)                            # keine Authentifizierung
        self.assertEqual(p[5:13], b"\x00" * 8)               # Sitzungsnummer und -ID 0: ohne Sitzung
        self.assertEqual(p[13], len(p) - 14)                 # Längenangabe
        msg = p[14:]
        self.assertEqual((msg[0] + msg[1] + msg[2]) & 0xFF, 0)            # Prüfsumme 1
        self.assertEqual(sum(msg[3:]) & 0xFF, 0)                           # Prüfsumme 2
        self.assertEqual(msg[5], 0x38)                       # Befehl: Get Channel Authentication Capabilities

    def test_sweep_finds_answering_host_only(self):
        sent = []                                           # Port 623 ist privilegiert: Socket wird ersetzt

        class FakeSock:
            def __init__(self, *a, **k):
                self.q = []
            def setblocking(self, b): pass
            def sendto(self, data, addr):
                sent.append((data, addr))
                if addr[0] == "10.9.9.2" and data == netscan.RMCP_PING:
                    self.q.append((netscan.RMCP_PONG_PREFIX + b"\x00" * 7, (addr[0], 623)))
                if addr[0] == "10.9.9.3" and data == netscan.IPMI_CAPS:          # nur die Fähigkeitsabfrage beantwortet
                    self.q.append((netscan.IPMI_REPLY_PREFIX + b"\x00" * 20, (addr[0], 623)))
            def recvfrom(self, n): return self.q.pop(0)
            def close(self): pass
            fileno = lambda self: 0

        fake = FakeSock()
        real_select = netscan.select.select
        netscan.select.select = lambda r, w, x, t: ([fake] if fake.q else [], [], [])
        real_socket = netscan.socket.socket
        netscan.socket.socket = lambda *a, **k: fake
        try:
            found = netscan.rmcp_sweep(["10.9.9.1", "10.9.9.2", "10.9.9.3"], wait=0.05, rate=100000)
        finally:
            netscan.select.select = real_select
            netscan.socket.socket = real_socket
        self.assertEqual(found, {"10.9.9.2", "10.9.9.3"})
        self.assertTrue(all(d in (netscan.RMCP_PING, netscan.IPMI_CAPS) and a[1] == 623 for d, a in sent))


class TestArp(unittest.TestCase):
    def test_parse(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("IP address       HW type     Flags       HW address            Mask     Device\n"
                    "10.0.0.7         0x1         0x2         AA:BB:CC:00:11:22     *        eth0\n"
                    "10.0.0.8         0x1         0x0         00:00:00:00:00:00     *        eth0\n")
        try:
            self.assertEqual(netscan.arp_table(f.name), {"10.0.0.7": "aa:bb:cc:00:11:22"})
        finally:
            os.unlink(f.name)
        self.assertEqual(netscan.arp_table("/nonexistent"), {})


@need("openssl")
class TestDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = DmtfMockServer("dell")
        cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def wait(self, dlg, limit=20):
        import time
        from PyQt6.QtWidgets import QApplication
        end = time.time() + limit
        while dlg.thread and dlg.thread.isRunning() and time.time() < end:
            QApplication.processEvents()
            time.sleep(0.02)
        QApplication.processEvents()

    def make(self, console=True):
        from bmc_steward.netscan_dialog import NetScanDialog
        d = NetScanDialog(None, [ipaddress.ip_interface("127.0.0.5/24")], console=console)
        d.range.setText("127.0.0.1")
        d.ports.setText(str(self.srv.port))
        d.ipmi.setChecked(False)
        return d

    def test_default_range_is_own_network(self):
        from bmc_steward.netscan_dialog import NetScanDialog
        d = NetScanDialog(None, [ipaddress.ip_interface("192.168.7.9/24")])
        self.assertEqual(d.range.text(), "192.168.7.0/24")

    def test_run_lists_hit_and_hands_it_over(self):
        d = self.make()
        d.start()
        self.wait(d)
        self.assertEqual(d.table.rowCount(), 1)
        self.assertEqual(d.table.item(0, 1).text(), "Dell iDRAC")
        self.assertTrue(d.table.item(0, 0).text().endswith(f":{self.srv.port}"))      # Port steht dabei, weil nicht 443
        self.assertTrue(d.go.isEnabled())
        self.assertFalse(d.b_console.isEnabled())                       # nichts markiert
        d.table.selectRow(0)
        self.assertTrue(d.b_console.isEnabled() and d.b_list.isEnabled())
        got_console, got_list = [], []
        d.use_in_console.connect(lambda a, v: got_console.append((a, v)))
        d.add_to_list.connect(lambda rows: got_list.append(rows))
        d.to_list()
        self.assertEqual(got_list[0][0]["ip"], "127.0.0.1")
        d.to_console()
        self.assertEqual(got_console, [(f"127.0.0.1:{self.srv.port}", "dell")])

    def test_bad_range_and_port_are_reported(self):
        d = self.make()
        d.range.setText("kein netz")
        d.start()
        self.assertIn("Unverständliche Angabe", d.info.text())
        self.assertIsNone(d.thread)
        d.range.setText("127.0.0.1")
        d.ports.setText("99999")
        d.start()
        self.assertIsNone(d.thread)
        d.range.setText("")
        d.ports.setText("443")
        d.start()
        self.assertIn("Bereich", d.info.text())

    def test_big_range_asks_first(self):
        from PyQt6.QtWidgets import QMessageBox
        d = self.make()
        d.range.setText("10.0.0.0/21")
        with unittest.mock.patch.object(QMessageBox, "exec", lambda s_: 0), \
                unittest.mock.patch.object(QMessageBox, "clickedButton", lambda s_: None):
            d.start()
        self.assertIsNone(d.thread)                                  # Nein: nichts gestartet

    def test_nothing_found_message(self):
        d = self.make()
        d.range.setText("127.0.0.1")
        d.ports.setText("1")
        d.start()
        self.wait(d)
        self.assertEqual(d.table.rowCount(), 0)
        self.assertIn("Nichts gefunden", d.info.text())

    def test_csv_export(self):
        import os, tempfile
        d = self.make()
        d.start()
        self.wait(d)
        target = os.path.join(tempfile.mkdtemp(), "t.csv")
        with unittest.mock.patch("bmc_steward.netscan_dialog.QFileDialog.getSaveFileName", return_value=(target, "")):
            d.save_csv()
        with open(target, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("127.0.0.1;Dell iDRAC", text)

    def test_rows_stay_sorted_by_address(self):
        d = self.make()
        for ip in ("10.0.0.20", "10.0.0.3", "10.0.0.100"):
            d.on_found({"ip": ip, "port": 443, "vendor": "dell", "model": "", "serial": "", "uuid": "",
                        "redfish": True, "ipmi": False, "mac": "", "fp": "", "note": ""})
        self.assertEqual([d.table.item(r, 0).text() for r in range(3)], ["10.0.0.3", "10.0.0.20", "10.0.0.100"])
        d.on_found({"ip": "10.0.0.20", "port": 443, "vendor": "dell", "model": "", "serial": "", "uuid": "",
                    "redfish": True, "ipmi": True, "mac": "", "fp": "", "note": ""})
        self.assertEqual(d.table.rowCount(), 3)                      # Aktualisierung statt neuer Zeile
        self.assertEqual(d.table.item(1, 5).text(), "ja")

    def test_close_stops_running_search(self):
        d = self.make()
        d.range.setText("10.255.255.0/28")                           # nicht erreichbar: dauert, bis abgebrochen wird
        d.start()
        d.close()
        self.assertFalse(d.thread.isRunning())


if __name__ == "__main__":
    unittest.main()
