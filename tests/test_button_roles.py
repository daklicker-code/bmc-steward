"""Knopffarben nach Wirkung (starten/verbinden grün, stoppen/trennen rot, Handy-Scanner dunkeltürkis) und der
Handy-Scanner in „Bestandsserver“ und „Systemaudit“."""
import unittest

from tests._support import qapp

qapp()
from bmc_steward import i18n  # noqa: E402
i18n.set_language("de")
from bmc_steward.main import Window  # noqa: E402


class TestRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def test_start_and_connect_are_green(self):
        w = self.w
        for b in (w.start, w.bios_start, w.svc_start, w.fw_start, w.ex_test, w.con_connect, w.con_start,
                  w.cap_start, w.dhcp_btn, w.serial_btn, w.audit.read_btn, w.bios_read_btn, w.swap.check_btn):
            self.assertEqual(b.property("role"), "go", b.text())

    def test_stop_and_disconnect_are_red(self):
        w = self.w
        for b in (w.stop, w.bios_stop, w.svc_stop, w.fw_stop, w.ex_stop, w.cap_stop, w.con_disconnect):
            self.assertEqual(b.property("role"), "stop", b.text())

    def test_three_scanner_buttons_are_turquoise(self):
        w = self.w
        for b in (w.scan_btn, w.ex_scan_btn, w.audit.scan_btn):
            self.assertEqual(b.property("role"), "scan")
            self.assertIn("Handy-Scanner", b.text())

    def test_stylesheet_has_the_colours(self):
        from bmc_steward import theme
        for name in ("dark", "light"):
            css = theme.stylesheet(name)
            for role in ("go", "stop", "scan"):
                self.assertIn(f'QPushButton[role="{role}"]', css)

    def test_live_run_stays_red(self):
        w = self.w
        w.dry.setChecked(False)
        try:
            self.assertIn("#c9302c", w.start.styleSheet())
        finally:
            w.dry.setChecked(True)
        self.assertEqual(w.start.styleSheet(), "")

    def test_set_role_switches(self):
        from bmc_steward.core import set_role
        w = self.w
        set_role(w.dhcp_btn, "stop")
        self.assertEqual(w.dhcp_btn.property("role"), "stop")
        set_role(w.dhcp_btn, "go")


class TestScannerTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def tearDown(self):
        if getattr(self.w, "scanner", None) is not None:
            self.w.scanner.stop_server()
            self.w.scanner.hide()
            self.w.scanner = None

    def test_existing_servers(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        w = self.w
        w.ex_table.setRowCount(0)
        w.ex_add_row()
        w.ex_add_row()
        w.ex_table.setItem(0, 0, QTableWidgetItem("10.0.0.7"))
        w.ex_table.setCurrentCell(0, 0)
        w.ex_scan_btn.click()
        t = w.scanner.target
        self.assertEqual(t.currentText(), "Passwort")
        self.assertEqual(w.scan_value("geheim", t.currentData()), 1)
        self.assertEqual(w.ex_table.item(0, 3).text(), "geheim")
        self.assertEqual(w.scan_value("zweites", t.currentData()), 2)       # Markierung rückt vor
        self.assertEqual(w.ex_table.item(1, 3).text(), "zweites")

    def test_system_audit(self):
        w = self.w
        w.audit.scan_btn.click()
        t = w.scanner.target
        self.assertEqual(t.currentText(), "Passwort")
        self.assertEqual(w.scan_value("audit-pw", t.currentData()), -1)
        self.assertEqual(w.audit.pw.text(), "audit-pw")
        t.setCurrentIndex(t.findText("Seriennummer"))
        w.scan_value("CZJ025045P", t.currentData())
        self.assertEqual(w.audit.serial.text(), "CZJ025045P")

    def test_new_hardware_unchanged(self):
        w = self.w
        w.scan_btn.click()
        t = w.scanner.target
        self.assertEqual(t.currentText(), "Passwort")
        self.assertEqual(t.count(), len(i18n.COLS))


if __name__ == "__main__":
    unittest.main()
