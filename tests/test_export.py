"""Exporte: Inventar als CSV/Excel/JSON, Druck, automatische Ablage, Übergabeprotokoll, QR-Code auf dem Blatt."""
import csv
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

from tests._support import qapp
from tests.test_vbmc import _WebBase

HEAD = {"manufacturer": "HPE", "model": "DL360 G6", "serial": "CZJ025045P"}
ROWS = [["501533-001", "DIMM 2GB", 2, ["RAKWEA7TMZ44J6", "RAKWEA7TMZ44J7"]],
        ["511777-001", "Netzteil 460W", 2, ["5ANLE0CLL3H90K", "5ANLE0BLLYV3LI"]],
        ["", "=HYPERLINK(\"x\")", 1, []]]


def tmpdir():
    return pathlib.Path(tempfile.mkdtemp(prefix="bmc-steward-export-"))


def zbar(png):
    """Inhalt der QR-Codes im Bild (zbarimg), oder None, wenn zbarimg fehlt."""
    exe = shutil.which("zbarimg")
    if not exe:
        return None
    r = subprocess.run([exe, "--raw", "-q", str(png)], capture_output=True, text=True, timeout=30)
    return r.stdout.split()


class TestQR(unittest.TestCase):
    def test_sizes_and_function_patterns(self):
        from bmc_steward.qr import encode
        m = encode("CZJ025045P")
        self.assertEqual(len(m), 21)                     # Version 1
        self.assertTrue(all(m[0][:7]) and all(m[6][:7]))  # Finder oben links
        self.assertEqual(len(encode("x" * 100)), 41)     # Version 6
        self.assertEqual(len(encode("x" * 150)), 49)     # Version 8 (mit Versionsinformation)
        with self.assertRaises(ValueError):
            encode("x" * 500)

    def test_decodes_with_zbar(self):
        qapp()
        from PyQt6.QtGui import QColor, QImage
        from bmc_steward.qr import encode
        if not shutil.which("zbarimg"):
            self.skipTest("zbarimg nicht installiert")
        for text in ("CZJ025045P", "J1634SZ4FM-Lenovo-ThinkSystem", "Ä" * 3 + "-" + "9" * 60, "x" * 150):
            m = encode(text)
            n, scale, quiet = len(m), 6, 4
            img = QImage((n + 2 * quiet) * scale, (n + 2 * quiet) * scale, QImage.Format.Format_RGB32)
            img.fill(QColor("white"))
            for y, row in enumerate(m):
                for x, dark in enumerate(row):
                    if dark:
                        for dy in range(scale):
                            for dx in range(scale):
                                img.setPixelColor((x + quiet) * scale + dx, (y + quiet) * scale + dy, QColor("black"))
            path = tmpdir() / "qr.png"
            img.save(str(path))
            out = subprocess.run(["zbarimg", "--raw", "-q", str(path)], capture_output=True, timeout=30).stdout
            self.assertEqual(out.decode("utf-8").strip(), text, f"Version {(n - 17) // 4}")


class TestInventoryExport(_WebBase):
    def setUp(self):
        qapp()
        self._isolate_data()

    def test_csv_for_excel(self):
        from bmc_steward import export
        path = tmpdir() / "inv.csv"
        export.write_inventory(path, [(HEAD, ROWS)])
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))            # BOM: Excel erkennt UTF-8
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines(), delimiter=";"))
        self.assertEqual(rows[1][:6], ["CZJ025045P", "HPE", "DL360 G6", "501533-001", "DIMM 2GB", "2"])
        self.assertEqual(rows[1][6], "RAKWEA7TMZ44J6 / RAKWEA7TMZ44J7")
        self.assertTrue(rows[3][4].startswith("'="))                # keine Formel aus Gerätedaten

    def test_xlsx_is_readable(self):
        from bmc_steward import export
        path = tmpdir() / "inv.xlsx"
        export.write_inventory(path, [(HEAD, ROWS), ({"serial": "ABC1234", "model": "R650"}, ROWS[:1])])
        with zipfile.ZipFile(path) as z:
            self.assertIn("[Content_Types].xml", z.namelist())
            sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows = sheet.findall(".//m:sheetData/m:row", ns)
        self.assertEqual(len(rows), 1 + 3 + 1)
        cells = rows[1].findall("m:c", ns)
        self.assertEqual("".join(cells[0].itertext()), "CZJ025045P")
        self.assertEqual(cells[5].find("m:v", ns).text, "2")         # Anzahl als Zahl

    def test_json(self):
        from bmc_steward import export
        path = tmpdir() / "inv.json"
        export.write_json(path, [(HEAD, ROWS)], [True, False, False, False, True, False], "Bem.",
                          {"name": "Max", "email": "m@x.org"}, "21.11.2026")
        d = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(d["server"][0]["seriennummer"], "CZJ025045P")
        self.assertEqual(d["server"][0]["bauteile"][0]["seriennummern"], ["RAKWEA7TMZ44J6", "RAKWEA7TMZ44J7"])
        self.assertEqual(d["mitarbeiter"]["name"], "Max")
        self.assertEqual(sum(d["checkliste"].values()), 2)
        self.assertEqual(d["datum"], "21.11.2026")

    def test_archive_copy(self):
        from bmc_steward import export, i18n
        pdf = tmpdir() / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        dest = export.archive(pdf, ["CZJ025045P"])
        self.assertTrue(dest.exists())
        self.assertEqual(dest.parent, i18n.DATA / "audits" / "CZJ025045P")
        many = export.archive(pdf, ["A", "B"])
        self.assertEqual(many.parent, i18n.DATA / "audits" / "mehrere")


class TestPrintAndQrOnSheet(unittest.TestCase):
    def test_print_to_printer(self):
        qapp()
        from PyQt6.QtPrintSupport import QPrinter
        from bmc_steward.sysaudit import render_sheets
        path = tmpdir() / "druck.pdf"
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(path))
        n = render_sheets(printer, [(HEAD, ROWS), (HEAD, ROWS)], [False] * 6, "", "", "21.11.2026")
        self.assertEqual(n, 2)
        self.assertTrue(path.read_bytes().startswith(b"%PDF"))

    def test_qr_on_sheet_holds_serial(self):
        qapp()
        from bmc_steward.sysaudit import render_sheets
        if not (shutil.which("pdftoppm") and shutil.which("zbarimg")):
            self.skipTest("pdftoppm/zbarimg nicht installiert")
        d = tmpdir()
        render_sheets(d / "a.pdf", [(HEAD, ROWS)], [False] * 6, "", "", "21.11.2026")
        subprocess.run(["pdftoppm", "-r", "200", "-png", str(d / "a.pdf"), str(d / "s")], check=True, timeout=60)
        self.assertEqual(zbar(next(d.glob("s*.png"))), ["CZJ025045P"])


class TestPageExports(_WebBase):
    def test_page_exports_and_archives(self):
        qapp()
        self._isolate_data()
        from bmc_steward import i18n
        from bmc_steward.sysaudit import AuditPage
        page = AuditPage(None)
        self.addCleanup(page.close)
        page.serial.setText("CZJ025045P")
        page.add_row(["501533-001", "DIMM 2GB", "2", "A / B"])
        d = tmpdir()
        for kind in ("csv", "xlsx", "json"):
            self.assertTrue(page.export_to(kind, d / f"x.{kind}"))
            self.assertTrue((d / f"x.{kind}").stat().st_size > 0)
        self.assertTrue(page.save_pdf(d / "x.pdf"))
        self.assertTrue(list((i18n.DATA / "audits" / "CZJ025045P").glob("*.pdf")))   # automatische Ablage


class TestHandover(_WebBase):
    def test_handover_from_new_hardware(self):
        qapp()
        self._isolate_data()
        from bmc_steward import i18n
        i18n.set_language("de")
        from bmc_steward.main import Window
        w = Window()
        self.addCleanup(w.deleteLater)
        w.table.setRowCount(0)
        w.add_row(["10.0.0.5", "geheimes-werkpw", "10.0.1.5", "255.255.255.0", "10.0.1.1", "srv-a", "CZJ1"])
        d = tmpdir()
        for kind in ("csv", "xlsx", "pdf"):
            path = d / f"uebergabe.{kind}"
            self.assertTrue(w.handover_to(path), kind)
            raw = path.read_bytes()
            self.assertNotIn(b"geheimes-werkpw", raw)
        rows = list(csv.reader((d / "uebergabe.csv").read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
        self.assertEqual(rows[1][:6], ["10.0.0.5", "10.0.1.5", "255.255.255.0", "10.0.1.1", "srv-a", "CZJ1"])


if __name__ == "__main__":
    unittest.main()


class TestOpenPdfAndHandoverMail(_WebBase):
    def test_open_pdf_from_page(self):
        qapp()
        self._isolate_data()
        from unittest.mock import patch
        from PyQt6.QtGui import QDesktopServices
        from bmc_steward.sysaudit import AuditPage
        page = AuditPage(None)
        self.addCleanup(page.close)
        page.serial.setText("CZJ025045P")
        page.add_row(["501533-001", "DIMM 2GB", "2", "A / B"])
        opened = []
        with patch.object(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True):
            self.assertTrue(page.open_pdf())
        self.assertEqual(len(opened), 1)
        self.assertTrue(pathlib.Path(opened[0]).read_bytes().startswith(b"%PDF"))
        self.assertIn("CZJ025045P", opened[0])

    def test_handover_by_mail(self):
        qapp()
        self._isolate_data()
        import email
        from tests.test_settings_mail import _SMTP, smtp_server
        from bmc_steward import i18n, settings
        from bmc_steward.sysaudit import save_staff
        i18n.set_language("de")
        from bmc_steward.main import Window
        port = smtp_server(self)
        settings.save({"smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none",
                       "smtp_from": "audit@example.org"})
        save_staff([{"name": "Erika", "email": "erika@example.org"}])
        w = Window()
        self.addCleanup(w.deleteLater)
        w.audit.reload_staff()
        w.audit.staff.setCurrentIndex(w.audit.staff.findText("Erika"))
        w.table.setRowCount(0)
        w.add_row(["10.0.0.5", "geheimes-werkpw", "10.0.1.5", "255.255.255.0", "10.0.1.1", "srv-a", "CZJ1"])
        dlg = w.handover_mail_dialog()
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.to.currentText(), "erika@example.org")
        self.assertIn("Übergabeprotokoll", dlg.subject.text())
        self.assertTrue(dlg.send(wait=True), dlg.status.text())
        msg = email.message_from_bytes(_SMTP.box[0]["data"])
        att = [p for p in msg.walk() if p.get_filename()]
        self.assertTrue(att[0].get_filename().endswith(".pdf"))
        self.assertTrue(att[0].get_payload(decode=True).startswith(b"%PDF"))
        self.assertNotIn(b"geheimes-werkpw", _SMTP.box[0]["data"])
