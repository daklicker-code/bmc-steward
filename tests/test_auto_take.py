"""DHCP-Reiter: „Automatisch übernehmen“ (Window.auto_take_leases).

Neu gefundene Geräte landen von selbst als „Aktuelle IP“ in der Serverliste - das ist die einzige Aufgabe der
Automatik. Konfiguriert wird nichts. Sicherheitsrelevant ist deshalb vor allem, was NICHT passieren darf:
ein Gerät doppelt eintragen (auch nach „fertig“ oder nach manuellem Löschen), während eines Laufs in die
gesperrte Liste schreiben oder eine gerade bearbeitete Zelle überschreiben.
"""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from PyQt6.QtWidgets import QAbstractItemView
from bmc_steward.main import Window

A = ("10.0.0.11", "aa:bb:cc:00:00:01", "idrac-ABC")
B = ("10.0.0.12", "aa:bb:cc:00:00:02", "ILOCZ123")
C = ("10.0.0.13", "aa:bb:cc:00:00:03", "")


class TestAutoTake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()
        cls.initial_auto = cls.w.auto_take.isChecked()     # Zustand direkt nach dem Start, vor jedem setUp

    def setUp(self):
        w = self.w
        w.show_leases([])                  # alte Geräte raus, bevor der Haken wieder gesetzt wird
        w.lease_taken.clear()
        w.auto_take.setChecked(True)
        w.table.setRowCount(0)
        w.add_row()                        # eine leere Zeile wie nach dem Start

    def tearDown(self):
        self.w.__dict__.pop("running", None)     # Attrappe aus einzelnen Tests wieder entfernen

    def ips(self):
        return [r[0] for r in self.w.rows()]

    def test_default_is_off(self):
        self.assertFalse(self.initial_auto)

    def test_new_device_is_entered_by_itself(self):
        self.w.show_leases([A])
        self.assertEqual(self.ips(), ["10.0.0.11"])

    def test_empty_placeholder_row_is_replaced_not_kept(self):
        self.w.show_leases([A, B])
        self.assertEqual(self.w.table.rowCount(), 2)

    def test_same_device_is_never_entered_twice(self):
        self.w.show_leases([A])
        self.w.show_leases([A, B])
        self.w.auto_take_leases()
        self.assertEqual(self.ips(), ["10.0.0.11", "10.0.0.12"])

    def test_switched_off_enters_nothing(self):
        self.w.auto_take.setChecked(False)
        self.w.show_leases([A])
        self.assertEqual(self.ips(), [])

    def test_switching_on_picks_up_devices_found_meanwhile(self):
        self.w.auto_take.setChecked(False)
        self.w.show_leases([A])
        self.w.auto_take.setChecked(True)
        self.assertEqual(self.ips(), ["10.0.0.11"])

    def test_manual_button_still_works_and_blocks_a_later_duplicate(self):
        self.w.auto_take.setChecked(False)
        self.w.show_leases([A, B])
        self.w.take_leases()
        self.assertEqual(self.ips(), ["10.0.0.11", "10.0.0.12"])
        self.w.auto_take.setChecked(True)
        self.assertEqual(self.ips(), ["10.0.0.11", "10.0.0.12"])

    def test_deleted_row_does_not_come_back(self):
        self.w.show_leases([A, B])
        self.w.table.removeRow(0)
        self.w.auto_take_leases()
        self.assertEqual(self.ips(), ["10.0.0.12"])

    def test_finished_server_is_not_entered_again(self):
        self.w.show_leases([A])
        self.w.mark_done("10.0.0.11")      # umgestellt: die alte Adresse steht noch in der Lease-Liste
        self.w.auto_take_leases()
        self.assertEqual(len(self.w.rows()), 1)
        self.assertEqual(len(self.w.rows(open_only=True)), 0)

    def test_new_device_with_reused_ip_is_a_new_device(self):
        self.w.show_leases([A])
        self.w.mark_done("10.0.0.11")
        self.w.show_leases([("10.0.0.11", "aa:bb:cc:00:00:99", "")])     # andere MAC, gleiche IP
        self.assertEqual(len(self.w.rows(open_only=True)), 1)

    def test_waits_during_a_run_and_catches_up_afterwards(self):
        self.w.running = lambda: True
        self.w.show_leases([C])
        self.assertEqual(self.ips(), [])
        del self.w.running
        self.w.auto_take_leases()          # so ruft es der Timer nach dem Lauf
        self.assertEqual(self.ips(), ["10.0.0.13"])

    def test_waits_while_a_cell_is_being_edited(self):
        editing = lambda: QAbstractItemView.State.EditingState
        self.w.table.state = editing
        try:
            self.w.show_leases([C])
            self.assertEqual(self.ips(), [])
        finally:
            del self.w.table.state
        self.w.auto_take_leases()
        self.assertEqual(self.ips(), ["10.0.0.13"])

    def test_profile_keeps_the_setting_and_old_profiles_mean_off(self):
        self.assertTrue(self.w.profile_values()["dhcp_auto"])
        self.w.apply_profile({"dhcp_auto": False})
        self.assertFalse(self.w.auto_take.isChecked())
        self.assertFalse(self.w.profile_values()["dhcp_auto"])
        self.w.apply_profile({"dhcp_auto": True})
        self.assertTrue(self.w.auto_take.isChecked())
        self.w.apply_profile({"vendor": ""})            # Profil aus einer älteren Version, ohne die Angabe
        self.assertFalse(self.w.auto_take.isChecked())

    def test_language_switch_state_carries_setting_and_known_devices(self):
        self.w.auto_take.setChecked(False)
        self.w.lease_taken.add("aa:bb:cc:00:00:01")
        st = self.w.export_state()
        self.assertFalse(st["auto_take"])
        self.assertEqual(st["lease_taken"], ["aa:bb:cc:00:00:01"])


if __name__ == "__main__":
    unittest.main()
