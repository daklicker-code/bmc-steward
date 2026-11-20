"""Systemaudit: Inventar per Redfish lesen (nur GET), zu Zeilen bündeln und als PDF-Blatt speichern."""
import pathlib
import tempfile
import time
import unittest

from tests._support import qapp
from tests.test_generalprobe import FACTORY, NEW_PW, NEW_USER, _Fleet, settle
from tests.test_vbmc import FACTORY_LOGIN, _WebBase


def pdf_pages(path):
    data = pathlib.Path(path).read_bytes()
    return data.count(b"/Type /Page") - data.count(b"/Type /Pages")


class TestRows(unittest.TestCase):
    def test_same_part_and_component_are_counted_together(self):
        from bmc_steward.sysaudit import group_items
        items = [{"part": "501533-001", "component": "DIMM 2GB", "serial": "A1"},
                 {"part": "462628-001", "component": "CPU Intel Xeon E5520", "serial": "F2010"},
                 {"part": "501533-001", "component": "DIMM 2GB", "serial": "A2"},
                 {"part": "", "component": "Netzteil 460W", "serial": ""}]
        rows = group_items(items)
        self.assertEqual(rows[0], ["501533-001", "DIMM 2GB", 2, ["A1", "A2"]])
        self.assertEqual(rows[1][2], 1)
        self.assertEqual(rows[2], ["", "Netzteil 460W", 1, []])

    def test_pdf_is_written_and_long_lists_get_more_pages(self):
        qapp()
        from bmc_steward.sysaudit import render_pdf
        head = {"manufacturer": "Hewlett Packard", "model": "DL360 G6", "serial": "CZJ025045P"}
        out = pathlib.Path(tempfile.mkdtemp(prefix="bmc-steward-audit-"))
        short = [["501533-001", "DIMM 2GB", 2, ["RAKWEA7TMZ44J6", "RAKWEA7TMZ44J7"]]]
        render_pdf(out / "a.pdf", head, short, [True, False, False, False, True, False], "Bemerkung", "Max",
                   "20.05.2020")
        self.assertTrue((out / "a.pdf").read_bytes().startswith(b"%PDF"))
        self.assertEqual(pdf_pages(out / "a.pdf"), 1)
        many = [[f"P{i}", f"Teil {i}", 1, [f"S{i}"]] for i in range(60)]
        render_pdf(out / "b.pdf", head, many, [False] * 6, "", "", "20.05.2020")
        self.assertGreater(pdf_pages(out / "b.pdf"), 1)


class TestAuditLive(_WebBase):
    """Gegen virtuelle BMCs: Kopf und Bauteile kommen aus Redfish, nichts wird geändert."""

    def _read(self, vendor):
        qapp()
        from bmc_steward.sysaudit import AuditEngine
        b, port = self._start(vendor)
        user, pw = FACTORY_LOGIN[vendor]
        lines = []
        eng = AuditEngine(log=lambda text, kind="": lines.append(text))
        res = eng.read(f"127.0.0.1:{port}", "", pw)
        eng.close_sessions()
        self.assertTrue(res["ok"], "\n".join(lines))
        return b, res

    def test_hpe_inventory(self):
        b, res = self._read("hpe")
        self.assertEqual((res["manufacturer"], res["model"], res["serial"]), ("HPE", b.model, b.serial))
        comps = [i["component"] for i in res["items"]]
        dimms = [i for i in res["items"] if i["component"].startswith("DIMM")]
        self.assertEqual(len(dimms), len(b.sim.inv["dimms"]))
        self.assertTrue(all(d["serial"] for d in dimms))
        self.assertTrue(any(c.startswith("CPU") for c in comps))
        self.assertEqual(sum(c.startswith(("Netzteil", "Power supply")) for c in comps), b.sim.inv["psus"])
        self.assertTrue(any(i["serial"] == b.sim.inv["drives"][0]["serial"] for i in res["items"]))

    def test_dell_uses_service_tag(self):
        b, res = self._read("dell")
        self.assertEqual(res["serial"], b.serial)          # Dell: SKU = Service-Tag (im Labor gleich)
        self.assertTrue(res["items"])


class TestAuditPage(_WebBase):
    def test_read_fill_and_save(self):
        qapp()
        from PyQt6.QtWidgets import QApplication
        from bmc_steward.sysaudit import AuditPage
        b, port = self._start("lenovo")
        page = AuditPage(None)
        self.addCleanup(page.close)
        page.addr.setText(f"127.0.0.1:{port}")
        page.pw.setText(FACTORY_LOGIN["lenovo"][1])
        page.read()
        deadline = time.monotonic() + 30
        while page.thread.isRunning() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        QApplication.processEvents()
        self.assertEqual(page.serial.text(), b.serial, page.out.toPlainText())
        self.assertGreater(page.table.rowCount(), 2)
        page.checks[0].setChecked(True)
        path = pathlib.Path(tempfile.mkdtemp(prefix="bmc-steward-audit-")) / "x.pdf"
        self.assertTrue(page.save_pdf(path))
        self.assertTrue(path.read_bytes().startswith(b"%PDF"))
        self.assertEqual(page.pw.text(), FACTORY_LOGIN["lenovo"][1])   # Passwort wird nicht gespeichert, bleibt nur im Feld
        self.assertNotIn(FACTORY_LOGIN["lenovo"][1].encode(), path.read_bytes())


if __name__ == "__main__":
    unittest.main()


class TestAuditFromOtherPages(unittest.TestCase):
    """„Systemaudit …“ in Neuware, Bestandsserver und UEFI-Konsole: Server übernehmen, Seite zeigen, auslesen."""

    @classmethod
    def setUpClass(cls):
        qapp()
        from bmc_steward import i18n
        i18n.set_language("de")
        from bmc_steward.main import Window
        cls.w = Window()

    def setUp(self):
        self.started = []
        self.w.audit.read = lambda: self.started.append((self.w.audit.addr.text(), self.w.audit.user.text(),
                                                        self.w.audit.pw.text()))

    def shown(self):
        return self.w.tabs.currentIndex() == self.w.tabs.indexOf(self.w.audit_page)

    def test_from_new_hardware_row(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        w = self.w
        w.table.setItem(0, 0, QTableWidgetItem("10.0.0.5"))
        w.table.setItem(0, 1, QTableWidgetItem("werk123"))
        w.table.setCurrentCell(0, 0)
        w.cur_user.setText("root")
        w.new_audit_btn.click()
        self.assertTrue(self.shown())
        self.assertEqual(self.started, [("10.0.0.5", "root", "werk123")])

    def test_from_existing_servers_row(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        w = self.w
        for c, val in enumerate(("10.0.0.7", "8443", "admin", "geheim")):
            w.ex_table.setItem(0, c, QTableWidgetItem(val))
        w.ex_table.setCurrentCell(0, 0)
        w.ex_audit_btn.click()
        self.assertTrue(self.shown())
        self.assertEqual(self.started, [("10.0.0.7:8443", "admin", "geheim")])

    def test_from_console(self):
        w = self.w
        w.con_ip.setEditText("10.0.0.9")
        w.con_user.setText("USERID")
        w.con_pass.setText("PASSW0RD")
        w.con_audit.click()
        self.assertTrue(self.shown())
        self.assertEqual(self.started, [("10.0.0.9", "USERID", "PASSW0RD")])

    def test_without_address_nothing_starts(self):
        self.w.con_ip.setEditText("")
        self.w.con_audit.click()
        self.assertEqual(self.started, [])


class TestPasswordAndServerChoice(unittest.TestCase):
    """Nie mit leerem Passwort anmelden; Server aus Neuware und Bestandsserver in einer Auswahlliste."""

    @classmethod
    def setUpClass(cls):
        qapp()
        from bmc_steward import i18n
        i18n.set_language("de")
        from bmc_steward.main import Window
        cls.w = Window()

    def test_empty_password_is_never_sent(self):
        page = self.w.audit
        page.thread = None
        page.pw.clear()
        page.start("10.0.0.5", "ops", "")
        self.assertIsNone(page.thread)
        self.assertIn("Passwort", page.out.toPlainText())

    def test_typed_admin_password_stays_for_the_next_green_server(self):
        page = self.w.audit
        started = []
        page.read = lambda: started.append(page.pw.text())
        try:
            page.user.setText("ops")
            page.pw.setText("Neu-Geheim")
            page.start("10.0.0.6", "ops", "")          # Passwort nach scharfem Lauf gelöscht
            page.start("10.0.0.7", "root", "")         # anderer Benutzer: altes Passwort nicht weitergeben
        finally:
            del page.read
        self.assertEqual(started, ["Neu-Geheim", ""])

    def test_server_list_offers_new_hardware_and_existing_servers(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        w = self.w
        w.table.setRowCount(0)
        w.add_row(["10.0.0.5", "werk123", "10.0.1.5", "255.255.255.0", "", "srv-a", ""])
        w.cur_user.setText("root")
        w.ex_table.setRowCount(0)
        w.ex_add_row()
        for c, val in enumerate(("10.0.0.7", "8443", "admin", "geheim")):
            w.ex_table.setItem(0, c, QTableWidgetItem(val))
        w.tabs.setCurrentIndex(w.tabs.indexOf(w.audit_page))      # Liste füllt sich beim Öffnen der Seite
        box = w.audit.addr_box
        texts = [box.itemText(i) for i in range(box.count())]
        self.assertTrue(any("10.0.0.5" in t and "srv-a" in t for t in texts), texts)
        self.assertTrue(any("10.0.0.7:8443" in t for t in texts), texts)
        i = next(i for i, t in enumerate(texts) if "10.0.0.7:8443" in t)
        box.setCurrentIndex(i)
        box.activated.emit(i)
        self.assertEqual((w.audit.addr.text(), w.audit.user.text(), w.audit.pw.text()),
                         ("10.0.0.7:8443", "admin", "geheim"))


class TestAuditAfterLiveRun(_Fleet):
    """Nachgestellt: scharfer Lauf mit „Passwörter nach scharfem Lauf löschen“, danach Audit aus Neuware."""

    def test_asks_for_password_instead_of_failed_login(self):
        from unittest.mock import patch
        from bmc_steward.main import Window
        w = Window()
        self.addCleanup(w.deleteLater)
        b, addr = self.boot("dell", "idrac9")
        w.table.setRowCount(0)
        w.add_row([addr, FACTORY["dell"][1], addr, "255.255.255.0", "127.0.0.254", "gp0", ""])
        w.new_user.setText(NEW_USER)
        w.new_pass.setText(NEW_PW)
        w.new_pass2.setText(NEW_PW)
        w.dry.setChecked(False)
        with patch.object(type(w), "confirm", lambda *a, **k: True):
            w.run()
            end = time.monotonic() + 60
            while w.worker.isRunning() and time.monotonic() < end:
                qapp().processEvents()
                time.sleep(0.01)
        for _ in range(20):
            qapp().processEvents()
        settle(b)
        self.assertTrue(w.is_done(0))
        self.assertEqual(w.new_pass.text(), "")            # gelöscht, wie voreingestellt
        w.table.setCurrentCell(0, 0)
        w.new_audit_btn.click()
        self.assertIsNone(w.audit.thread)                   # kein Anmeldeversuch mit leerem Passwort
        w.audit.pw.setText(NEW_PW)
        w.audit.read()
        end = time.monotonic() + 30
        while w.audit.thread.isRunning() and time.monotonic() < end:
            qapp().processEvents()
            time.sleep(0.01)
        for _ in range(10):
            qapp().processEvents()
        self.assertEqual(w.audit.serial.text(), b.serial, w.audit.out.toPlainText())
