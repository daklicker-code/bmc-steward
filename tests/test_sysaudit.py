"""Systemaudit: Inventar per Redfish lesen (nur GET), zu Zeilen bündeln und als PDF-Blatt speichern."""
import pathlib
import tempfile
import time
import unittest

from tests._support import qapp
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
