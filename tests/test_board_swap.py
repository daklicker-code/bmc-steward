"""Modus „Mainboard getauscht“: Sicherung des alten Boards auswerten, Identität des neuen Boards lesen,
HPE-Seriennummer/Produkt-ID setzen, altes Zertifikat vergessen, Werte in die Serverliste übernehmen."""
import json
import time
import unittest

from tests._support import qapp
from tests.test_vbmc import FACTORY_LOGIN, _WebBase

BACKUP = {
    "meta": {"address": "10.0.0.21", "device": "uuid:1111-2222", "vendor": "hpe", "time": "2026-11-01T10:00:00"},
    "resources": {
        "/redfish/v1/Managers/1/EthernetInterfaces/1": {
            "HostName": "srv-21", "IPv4StaticAddresses": [{"Address": "10.0.0.21", "SubnetMask": "255.255.255.0",
                                                           "Gateway": "10.0.0.1"}]},
        "/redfish/v1/Managers/1/EthernetInterfaces/2": {"IPv4Addresses": [{"Address": "0.0.0.0"}]},
    },
}


class TestBackupHelpers(unittest.TestCase):
    def setUp(self):
        qapp()
        from bmc_steward import i18n
        import pathlib
        import tempfile
        self.addCleanup(setattr, i18n, "DATA", i18n.DATA)
        i18n.DATA = pathlib.Path(tempfile.mkdtemp(prefix="bmc-steward-swap-"))
        self.i18n = i18n

    def test_summary_reads_hostname_and_static_address(self):
        from bmc_steward.board_swap import backup_summary
        s = backup_summary(BACKUP)
        self.assertEqual((s["hostname"], s["ip"], s["mask"], s["gw"]), ("srv-21", "10.0.0.21", "255.255.255.0", "10.0.0.1"))
        self.assertEqual(s["device"], "uuid:1111-2222")

    def test_summary_falls_back_to_dell_attributes(self):
        from bmc_steward.board_swap import backup_summary
        doc = {"meta": {}, "resources": {"/redfish/v1/Managers/iDRAC.Embedded.1/Attributes": {"Attributes": {
            "NIC.1.DNSRacName": "idrac-7", "IPv4Static.1.Address": "10.1.1.7", "IPv4Static.1.Netmask": "255.255.0.0",
            "IPv4Static.1.Gateway": "10.1.0.1"}}}}
        s = backup_summary(doc)
        self.assertEqual((s["hostname"], s["ip"], s["mask"], s["gw"]), ("idrac-7", "10.1.1.7", "255.255.0.0", "10.1.0.1"))

    def test_latest_backup_is_the_newest_file(self):
        from bmc_steward.board_swap import latest_backup
        d = self.i18n.DATA / "sicherungen" / "CZJ1234567"
        d.mkdir(parents=True)
        (d / "20261101-100000-neuware.json").write_text(json.dumps({"meta": {"time": "alt"}}), encoding="utf-8")
        (d / "20261105-100000-neuware.json").write_text(json.dumps({"meta": {"time": "neu"}}), encoding="utf-8")
        path, doc = latest_backup("czj1234567".upper())
        self.assertEqual(doc["meta"]["time"], "neu")
        self.assertEqual(latest_backup("GIBTSNICHT"), (None, None))

    def test_lenovo_commands_never_contain_the_password(self):
        from bmc_steward.board_swap import lenovo_commands
        lines = lenovo_commands("10.0.0.9", "USERID", "7X02CTO1WW", "J300ABCD")
        self.assertEqual(len(lines), 2)
        self.assertIn("SysInfoProdName 7X02CTO1WW", lines[0])
        self.assertIn("SysInfoSerialNum J300ABCD", lines[1])
        self.assertTrue(all("<Passwort>@10.0.0.9" in x for x in lines))


class TestBoardSwapLive(_WebBase):
    """Gegen einen virtuellen iLO: lesen, im Trockenlauf nichts ändern, scharf die UEFI-Attribute setzen."""

    def _engine(self, dry, lines):
        qapp()
        self._isolate_data()
        from bmc_steward.board_swap import BoardSwapEngine
        return BoardSwapEngine(dry, lambda text, kind="": lines.append(text))

    def test_read_reports_vendor_and_identity(self):
        b, port = self._start("hpe")
        user, pw = FACTORY_LOGIN["hpe"]
        lines = []
        eng = self._engine(False, lines)          # Lesen ist nur GET
        res = eng.read(f"127.0.0.1:{port}", "", pw)
        eng.close_sessions()
        self.assertTrue(res["ok"], "\n".join(lines))
        self.assertEqual(res["vendor"], "hpe")
        self.assertEqual(res["user"], user)
        self.assertEqual(res["serial"], b.serial)

    def test_dry_run_changes_nothing_live_sets_attributes(self):
        b, port = self._start("hpe")
        user, pw = FACTORY_LOGIN["hpe"]
        before = dict(b.bios)
        lines = []
        eng = self._engine(True, lines)
        self.assertTrue(eng.set_hpe_ident(f"127.0.0.1:{port}", user, pw, "CZJ7654321", "868703-B21"), "\n".join(lines))
        self.assertEqual(b.bios, before)
        eng = self._engine(False, lines)
        self.assertTrue(eng.set_hpe_ident(f"127.0.0.1:{port}", user, pw, "CZJ7654321", "868703-B21"), "\n".join(lines))
        eng.close_sessions()
        self.assertEqual((b.bios.get("SerialNumber"), b.bios.get("ProductId")), ("CZJ7654321", "868703-B21"))

    def test_dialog_forgets_old_pin_and_hands_over_backup_values(self):
        qapp()
        self._isolate_data()
        from PyQt6.QtWidgets import QApplication
        from bmc_steward import i18n, trust
        from bmc_steward.board_swap import BoardSwapDialog
        b, port = self._start("hpe")
        user, pw = FACTORY_LOGIN["hpe"]
        label = "CZJ7654321"
        d = i18n.DATA / "sicherungen" / label
        d.mkdir(parents=True)
        (d / "20261101-100000-neuware.json").write_text(json.dumps(BACKUP), encoding="utf-8")
        trust.store().check("uuid:1111-2222", "ab" * 32, "10.0.0.21")
        dlg = BoardSwapDialog(None, (f"127.0.0.1:{port}", pw, label), "")
        dlg.pid.setText("868703-B21")

        def wait():
            deadline = time.monotonic() + 30
            while dlg.thread.isRunning() and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.02)
            QApplication.processEvents()

        dlg.check()
        wait()
        self.assertIsNotNone(dlg.info)
        self.assertTrue(dlg.do_ident.isChecked() and dlg.do_pin.isChecked() and dlg.do_take.isChecked(),
                        dlg.out.toPlainText())
        dlg.dry.setChecked(False)
        dlg.execute()
        wait()
        self.assertNotIn("uuid:1111-2222", trust.store().pins)
        self.assertEqual(dlg.take, ("srv-21", "10.0.0.21", "255.255.255.0", "10.0.0.1", label))
        self.assertEqual(b.bios.get("SerialNumber"), label)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
