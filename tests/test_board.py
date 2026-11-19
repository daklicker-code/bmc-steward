"""Mainboard-Tausch (board.py) gegen den Testlabor-Simulator: altes Board sichern, neues Board unter derselben
Adresse wieder auf Stand bringen – Zertifikate, Kennung je Hersteller, Lizenz, Dienste und UEFI aus der Sicherung."""
import unittest

from tests._support import qapp
from tests.test_vbmc import FACTORY_LOGIN, _WebBase

qapp()
from bmc_steward import trust                                 # noqa: E402
from bmc_steward.board import BoardEngine, load_backup         # noqa: E402
from bmc_steward.redfish import RedfishClient                  # noqa: E402


class _BoardBase(_WebBase):
    def old_board_backup(self, vendor, tweak=None):
        """Altes Board starten, Einstellungen verändern (tweak), sichern wie vor jeder Änderung; Pfad zurück."""
        old, port = self._start(vendor, idx=1)
        if tweak:
            tweak(old)
        user, pw = FACTORY_LOGIN[vendor]
        c = RedfishClient(dry=False, log=lambda *a, **k: None)
        c.cur_user = user
        path = c.backup(f"127.0.0.1:{port}", user, pw, vendor, "neuware")
        c.close_sessions()
        self.assertIsNotNone(path)
        return old, path

    def new_board(self, vendor):
        """Neues Board; unter seiner Adresse ist noch das Zertifikat eines anderen Geräts gemerkt (wie nach dem
        Tausch unter derselben festen Adresse)."""
        new, port = self._start(vendor, idx=2)
        addr = f"127.0.0.1:{port}"
        trust.store().check("uuid:altes-board-" + addr, "AB" * 32, addr)
        return new, addr

    def run_board(self, addr, vendor, **kw):
        lines = []
        user, pw = FACTORY_LOGIN[vendor]
        kw.setdefault("dry", False)
        eng = BoardEngine(addr, user, pw, log=lambda line, kind="": lines.append(line), **kw)
        return eng.run(), "\n".join(lines)


class TestHpeBoard(_BoardBase):
    def test_new_hpe_board_gets_identity_license_services_and_uefi_of_the_old_one(self):
        def tweak(b):
            b.bios["SysProfile"] = "PerfPerWattOptimizedDapc"
            b.extra.setdefault("proto", {})["IPMI"] = False
        old, path = self.old_board_backup("hpe", tweak)
        new, addr = self.new_board("hpe")
        self.assertNotEqual(old.serial, new.serial)
        ok, log = self.run_board(addr, "hpe", backup_path=str(path), hpe_license="ABCDE-12345-FGHIJ-67890-KLMNO",
                                 restore_bios=True, restore_services=True)
        self.assertTrue(ok, log)
        self.assertEqual(new.bios["SerialNumber"], old.serial)          # aus der Sicherung
        self.assertEqual(new.bios["ProductId"], old.serial)             # SKU des Simulators = Seriennummer
        self.assertEqual(new.bios["SysProfile"], "PerfPerWattOptimizedDapc")
        self.assertFalse(new.extra["proto"]["IPMI"])
        self.assertTrue(new.sim.licensed)
        pins = trust.store().for_addr(addr)
        self.assertEqual(len(pins), 1)
        self.assertNotIn("uuid:altes-board-" + addr, pins)              # alte Merkung vergessen, neues Board gemerkt
        self.assertIn("vergessen", log)

    def test_values_typed_in_win_over_the_backup(self):
        old, path = self.old_board_backup("hpe")
        new, addr = self.new_board("hpe")
        ok, log = self.run_board(addr, "hpe", backup_path=str(path), hpe_serial="CZJ1234567", hpe_pid="868703-B21")
        self.assertTrue(ok, log)
        self.assertEqual((new.bios["SerialNumber"], new.bios["ProductId"]), ("CZJ1234567", "868703-B21"))
        self.assertIn("Neustart", log)

    def test_dry_run_changes_nothing_and_keeps_the_pins(self):
        old, path = self.old_board_backup("hpe", lambda b: b.bios.update(SysProfile="Custom"))
        new, addr = self.new_board("hpe")
        before = dict(new.bios)
        ok, log = self.run_board(addr, "hpe", backup_path=str(path), restore_bios=True, restore_services=True,
                                 dry=True)
        self.assertTrue(ok, log)
        self.assertEqual(new.bios, before)
        self.assertIn("uuid:altes-board-" + addr, trust.store().for_addr(addr))
        self.assertIn("DRYRUN", log)
        self.assertIn("SysProfile", log)                               # echter Vergleich auch im Trockenlauf

    def test_identity_attributes_are_never_restored_from_the_backup(self):
        old, path = self.old_board_backup("hpe", lambda b: b.bios.update(SerialNumber="ALT0000001", BiosPassword="x"))
        new, addr = self.new_board("hpe")
        ok, log = self.run_board(addr, "hpe", backup_path=str(path), restore_bios=True, hpe_serial="NEU0000001")
        self.assertTrue(ok, log)
        self.assertEqual(new.bios["SerialNumber"], "NEU0000001")
        self.assertNotIn("BiosPassword", new.bios)

    def test_bad_input_stops_before_anything_is_sent(self):
        new, addr = self.new_board("hpe")
        before = dict(new.bios)
        for kw in ({"hpe_license": "KAPUTT"}, {"hpe_serial": "kein gültiger wert"}):
            with self.subTest(**kw):
                ok, log = self.run_board(addr, "hpe", hpe_serial=kw.get("hpe_serial", "CZJ1234567"),
                                         hpe_license=kw.get("hpe_license", ""))
                self.assertFalse(ok)
                self.assertEqual(new.bios, before)
                self.assertIn("uuid:altes-board-" + addr, trust.store().for_addr(addr))   # auch nichts vergessen


class TestOtherVendors(_BoardBase):
    def test_dell_with_service_tag_only_checks(self):
        old, path = self.old_board_backup("dell")
        new, addr = self.new_board("dell")
        new.serial = old.serial                                          # Easy Restore hat das Service-Tag zurückgeholt
        before = dict(new.bios)
        ok, log = self.run_board(addr, "dell", backup_path=str(path))
        self.assertTrue(ok, log)
        self.assertIn(old.serial, log)
        self.assertEqual(new.bios, before)

    def test_dell_without_matching_service_tag_points_to_easy_restore(self):
        old, path = self.old_board_backup("dell")
        new, addr = self.new_board("dell")
        ok, log = self.run_board(addr, "dell", backup_path=str(path))
        self.assertTrue(ok, log)
        self.assertIn("Easy Restore", log)

    def test_lenovo_and_supermicro_only_say_which_tool_writes_the_identity(self):
        for vendor, tool in (("lenovo", "OneCLI"), ("smc", "SUM")):
            with self.subTest(vendor=vendor):
                old, path = self.old_board_backup(vendor)
                new, addr = self.new_board(vendor)
                before = dict(new.bios)
                ok, log = self.run_board(addr, vendor, backup_path=str(path))
                self.assertTrue(ok, log)
                self.assertIn(tool, log)
                self.assertEqual(new.bios, before)

    def test_backup_of_another_vendor_is_refused(self):
        old, path = self.old_board_backup("dell")
        new, addr = self.new_board("hpe")
        ok, log = self.run_board(addr, "hpe", backup_path=str(path), restore_bios=True)
        self.assertFalse(ok)
        self.assertIn("anderen Hersteller", log)
        self.assertNotIn("SerialNumber", new.bios)


class TestBackupFiles(unittest.TestCase):
    def test_unreadable_backup_is_none(self):
        self.assertIsNone(load_backup(None))
        self.assertIsNone(load_backup("/gibt/es/nicht.json"))


if __name__ == "__main__":
    unittest.main()
