"""Seite „Mainboard-Tausch“: Sicherung des alten Boards auswerten, Identität des neuen Boards lesen,
HPE-Seriennummer/Produkt-ID setzen, altes Zertifikat vergessen, Werte in die Serverliste übernehmen,
und je Szenario (Systemplatine, nur BMC, nur Prozessorplatine, Ersatzgerät) nur passende Schritte anbieten."""
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

    def test_scenarios_offer_only_fitting_steps(self):
        from bmc_steward.board_swap import SCENARIOS
        self.assertFalse(SCENARIOS["server"]["ident"])           # Ersatzgerät: Kennung nie ändern
        self.assertFalse(SCENARIOS["bmc"]["bios"])               # UEFI liegt auf der Hauptplatine, unberührt
        self.assertFalse(SCENARIOS["cpu"]["take"] or SCENARIOS["cpu"]["pin"])   # BMC bleibt: Adresse, Zertifikat
        self.assertTrue(all(SCENARIOS["board"].values()))

    def test_vendor_advice_covers_every_vendor_and_scenario(self):
        from bmc_steward.board_swap import SCENARIOS, vendor_advice
        for v in ("hpe", "hpe_old", "dell", "lenovo", "smc"):
            for scen in SCENARIOS:
                for same in (False, True):
                    adv = vendor_advice(v, scen, same)
                    self.assertTrue(all(isinstance(t, str) and k in ("", "ok") for t, k in adv), (v, scen))
        self.assertTrue(vendor_advice("hpe", "board", False))

    def test_lenovo_commands_never_contain_the_password(self):
        from bmc_steward.board_swap import lenovo_commands
        lines = lenovo_commands("10.0.0.9", "USERID", "7X02CTO1WW", "J300ABCD")
        self.assertEqual(len(lines), 2)
        self.assertIn("SysInfoProdName 7X02CTO1WW", lines[0])
        self.assertIn("SysInfoSerialNum J300ABCD", lines[1])
        self.assertTrue(all("<Passwort>@10.0.0.9" in x for x in lines))

    def test_lenovo_machine_type_is_compared_too(self):
        """Stimmt nur die Seriennummer, aber nicht der Maschinentyp, darf der Dialog nicht „stimmt schon“ melden."""
        from bmc_steward.board_swap import pid_matches
        res = {"sku": "7X02CTO1WW", "model": "ThinkSystem SR530 -[7X08CTO1WW]-"}
        self.assertTrue(pid_matches("lenovo", res, "7X02CTO1WW"))                 # SKU
        self.assertTrue(pid_matches("lenovo", {"sku": "", "model": "ThinkSystem SR530 -[7X08CTO1WW]-"}, "7X08CTO1WW"))
        self.assertFalse(pid_matches("lenovo", {"sku": "", "model": ""}, "7X02CTO1WW"))   # leeres VPD
        self.assertFalse(pid_matches("lenovo", {"sku": "7X06CTO1WW", "model": ""}, "7X02CTO1WW"))
        self.assertTrue(pid_matches("lenovo", {"sku": "", "model": ""}, ""))       # Feld leer: nichts zu prüfen
        self.assertFalse(pid_matches("hpe", {"sku": "868703-B21"}, "868704-B21"))
        self.assertTrue(pid_matches("dell", {"sku": "ABC1234"}, "IRGENDWAS"))       # Dell/Supermicro: kein Vergleich

    def test_lenovo_commands_use_placeholders_for_missing_label_values(self):
        from bmc_steward.board_swap import lenovo_commands
        lines = lenovo_commands("10.0.0.9", "USERID", "", "")
        self.assertEqual(len(lines), 2)                       # VPD nur einmal setzbar: immer beide Werte zeigen
        self.assertIn("SysInfoProdName <", lines[0])
        self.assertIn("SysInfoSerialNum <", lines[1])


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
        from bmc_steward.board_swap import BoardSwapPage
        b, port = self._start("hpe")
        user, pw = FACTORY_LOGIN["hpe"]
        label = "CZJ7654321"
        d = i18n.DATA / "sicherungen" / label
        d.mkdir(parents=True)
        (d / "20261101-100000-neuware.json").write_text(json.dumps(BACKUP), encoding="utf-8")
        trust.store().check("uuid:1111-2222", "ab" * 32, "10.0.0.21")
        dlg = BoardSwapPage(None, (f"127.0.0.1:{port}", pw, label), "")
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


class TestCheckWithoutLabel(_WebBase):
    """„Prüfen“ geht ohne Eingabe vom Aufkleber und zeigt, was das Board meldet."""

    def test_check_shows_board_identity_without_label_input(self):
        qapp()
        self._isolate_data()
        from PyQt6.QtWidgets import QApplication
        from bmc_steward.board_swap import BoardSwapPage
        b, port = self._start("lenovo")
        page = BoardSwapPage(None, (f"127.0.0.1:{port}", FACTORY_LOGIN["lenovo"][1], ""), "")
        self.addCleanup(page.close)
        page.check()
        self.assertIsNotNone(page.thread, page.out.toPlainText())
        deadline = time.monotonic() + 30
        while page.thread.isRunning() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        QApplication.processEvents()
        self.assertTrue(page.info and page.info.get("ok"), page.out.toPlainText())
        self.assertIn(b.serial, page.board_lbl.text())
        self.assertIn(b.model, page.board_lbl.text())
        self.assertEqual(page.serial.text(), "")             # Aufkleber-Feld bleibt, was der Nutzer eintippt


class TestScenarioPage(_WebBase):
    """Ersatzgerät: Sicherung unter der alten Seriennummer, Kennung des neuen Geräts bleibt unangetastet."""

    def test_spare_server_takes_old_settings_but_keeps_its_serial(self):
        qapp()
        self._isolate_data()
        from PyQt6.QtWidgets import QApplication
        from bmc_steward import i18n
        from bmc_steward.board_swap import BoardSwapPage
        b, port = self._start("hpe")
        d = i18n.DATA / "sicherungen" / "ALTSERVER1"
        d.mkdir(parents=True)
        (d / "20261101-100000-neuware.json").write_text(json.dumps(BACKUP), encoding="utf-8")
        page = BoardSwapPage(None, (f"127.0.0.1:{port}", FACTORY_LOGIN["hpe"][1], "NEUSERIE01"), "")
        self.addCleanup(page.close)
        page.scen_btn["server"].setChecked(True)
        page.old_serial.setText("ALTSERVER1")
        taken = []
        page.take_ready.connect(taken.append)
        page.check()
        deadline = time.monotonic() + 30
        while page.thread.isRunning() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        QApplication.processEvents()
        self.assertFalse(page.do_ident.isEnabled(), page.out.toPlainText())
        self.assertEqual(page.new_ip.text(), "10.0.0.21")
        self.assertTrue(page.do_take.isChecked())
        before = dict(b.bios)
        page.dry.setChecked(False)
        page.execute()
        QApplication.processEvents()
        self.assertEqual(taken[0][1:], ("srv-21", "10.0.0.21", "255.255.255.0", "10.0.0.1", "NEUSERIE01"))
        self.assertEqual(b.bios.get("SerialNumber"), before.get("SerialNumber"))
        self.assertEqual(page.pw.text(), "")                     # Passwort nach dem scharfen Lauf gelöscht

    def test_without_backup_hostname_and_address_can_be_typed(self):
        qapp()
        self._isolate_data()
        from bmc_steward.board_swap import BoardSwapPage
        page = BoardSwapPage(None, ("10.9.9.9", "", "KEINEBACKUP"), "")
        self.addCleanup(page.close)
        page.info = {"ok": True, "vendor": "dell", "user": "root", "serial": "", "sku": ""}
        page.find_backup()
        self.assertIsNone(page.backup)
        self.assertFalse(page.do_take.isEnabled())
        page.host.setText("srv-99")
        page.new_ip.setText("10.0.0.99")
        self.assertTrue(page.do_take.isEnabled() and page.do_take.isChecked())
        page.scen_btn["cpu"].setChecked(True)                   # BMC bleibt: nichts in die Liste
        self.assertFalse(page.do_take.isEnabled())


class TestRestoreFromBackupLive(_WebBase):
    """Lizenz, Dienste und UEFI des alten Boards auf das neue: gegen virtuelle BMCs, echte Sicherung."""

    def old_backup(self, vendor, tweak=None):
        from bmc_steward.redfish import RedfishClient
        old, port = self._start(vendor, idx=1)
        if tweak:
            tweak(old)
        user, pw = FACTORY_LOGIN[vendor]
        c = RedfishClient(dry=False, log=lambda *a, **k: None)
        c.cur_user = user
        path = c.backup(f"127.0.0.1:{port}", user, pw, vendor, "neuware")
        c.close_sessions()
        return old, json.loads(path.read_text(encoding="utf-8"))

    def engine(self, dry, lines):
        from bmc_steward.board_swap import BoardSwapEngine
        return BoardSwapEngine(dry, lambda text, kind="": lines.append(text))

    def test_license_services_and_uefi_come_back_on_the_new_board(self):
        qapp()
        def tweak(b):
            b.bios.update(SysProfile="PerfPerWattOptimizedDapc", SerialNumber="ALT0000001", AdminPassword="x")
            b.extra.setdefault("proto", {})["IPMI"] = False
        old, doc = self.old_backup("hpe", tweak)
        new, port = self._start("hpe", idx=2)
        user, pw = FACTORY_LOGIN["hpe"]
        lines = []
        eng = self.engine(False, lines)
        ok = eng.apply(f"127.0.0.1:{port}", "", pw, "hpe", license="ABCDE-12345-FGHIJ-67890-KLMNO", doc=doc,
                       services=True, bios=True)
        eng.close_sessions()
        self.assertTrue(ok, "\n".join(lines))
        self.assertEqual(new.bios["SysProfile"], "PerfPerWattOptimizedDapc")
        self.assertNotEqual(new.bios.get("SerialNumber"), "ALT0000001")      # Kennung nie aus der Sicherung
        self.assertNotIn("AdminPassword", new.bios)                          # Passwort-Attribute nie
        self.assertFalse(new.extra["proto"]["IPMI"])
        self.assertTrue(new.sim.licensed)
        self.assertEqual(new.sim.lic["key"], "ABCDE-12345-FGHIJ-67890-KLMNO")

    def test_dry_run_compares_for_real_but_changes_nothing(self):
        qapp()
        old, doc = self.old_backup("dell", lambda b: b.bios.update(SysProfile="Custom"))
        new, port = self._start("dell", idx=2)
        user, pw = FACTORY_LOGIN["dell"]
        before = dict(new.bios)
        lines = []
        eng = self.engine(True, lines)
        self.assertTrue(eng.apply(f"127.0.0.1:{port}", user, pw, "dell", doc=doc, services=True, bios=True),
                        "\n".join(lines))
        self.assertEqual(new.bios, before)
        self.assertIn("SysProfile", "\n".join(lines))
        self.assertIn("DRYRUN", "\n".join(lines))

    def test_bad_license_key_sends_nothing(self):
        qapp()
        self._isolate_data()
        new, port = self._start("hpe")
        user, pw = FACTORY_LOGIN["hpe"]
        lines = []
        eng = self.engine(False, lines)
        self.assertFalse(eng.apply(f"127.0.0.1:{port}", user, pw, "hpe", license="KAPUTT"))
        self.assertIn("ungültig", "\n".join(lines))

    def test_new_board_at_the_address_of_the_old_one_can_be_checked(self):
        qapp()
        from bmc_steward import trust
        new, port = self._start("hpe")
        addr = f"127.0.0.1:{port}"
        trust.store().check("uuid:altes-board", "ab" * 32, addr)       # altes Board unter derselben Adresse gemerkt
        lines = []
        eng = self.engine(False, lines)
        res = eng.read(addr, "", FACTORY_LOGIN["hpe"][1])
        eng.close_sessions()
        self.assertTrue(res["ok"], "\n".join(lines))
        self.assertNotIn("GESPERRT", "\n".join(lines))

    def test_dialog_offers_license_and_restore_only_where_they_fit(self):
        qapp()
        from PyQt6.QtWidgets import QApplication
        from bmc_steward import i18n
        from bmc_steward.board_swap import BoardSwapPage
        old, doc = self.old_backup("hpe")
        label = "CZJ7654321"
        d = i18n.DATA / "sicherungen" / label
        d.mkdir(parents=True, exist_ok=True)
        (d / "20261101-100000-neuware.json").write_text(json.dumps(doc), encoding="utf-8")
        new, port = self._start("hpe", idx=2)
        dlg = BoardSwapPage(None, (f"127.0.0.1:{port}", FACTORY_LOGIN["hpe"][1], label), "")
        self.addCleanup(dlg.close)

        def wait():
            deadline = time.monotonic() + 30
            while dlg.thread.isRunning() and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.02)
            QApplication.processEvents()

        dlg.check()
        wait()
        self.assertTrue(dlg.do_svc.isEnabled() and dlg.do_svc.isChecked(), dlg.out.toPlainText())
        self.assertTrue(dlg.do_bios.isEnabled())
        self.assertFalse(dlg.do_bios.isChecked())                       # UEFI nur bewusst
        self.assertFalse(dlg.do_lic.isEnabled())
        dlg.lic.setText("ABCDE-12345-FGHIJ-67890-KLMNO")                # auch nach dem Prüfen eingetippt
        self.assertTrue(dlg.do_lic.isEnabled() and dlg.do_lic.isChecked())
        dlg.dry.setChecked(False)
        dlg.execute()
        wait()
        self.assertEqual(new.sim.lic["key"], "ABCDE-12345-FGHIJ-67890-KLMNO", dlg.out.toPlainText())


if __name__ == "__main__":
    unittest.main()
