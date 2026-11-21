"""Leuchten im Seitenmenü: „Neuware“ und „Bestandsserver“ zeigen den Stand ihrer Serverliste (rot = noch keiner
konfiguriert, grün = alle, gelb = Lauf oder neue Server nach der Konfiguration), die UEFI-Konsole leuchtet gelb,
solange sie verbindet oder verbunden ist."""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.main import Window
from bmc_steward.sidenav import DONE_LAMP, LAB_LAMP, LAMP_ROLE, OPEN_LAMP, list_lamp


def row(ip):
    values = [""] * len(i18n.COLS)
    values[0], values[2], values[5] = ip, "10.1.0." + ip.rsplit(".", 1)[1], "srv" + ip.rsplit(".", 1)[1]
    return values


class TestListLamp(unittest.TestCase):
    def test_states(self):
        self.assertEqual(list_lamp(0, 0)[0], None)
        self.assertEqual(list_lamp(2, 0)[0], OPEN_LAMP)
        self.assertEqual(list_lamp(2, 2)[0], DONE_LAMP)
        self.assertEqual(list_lamp(3, 2)[0], LAB_LAMP)
        self.assertEqual(list_lamp(2, 2, running=True)[0], LAB_LAMP)
        self.assertEqual(list_lamp(0, 0, running=True)[0], LAB_LAMP)


class TestMenuLamps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    @classmethod
    def tearDownClass(cls):
        cls.w.dirty = False
        cls.w.close()

    def lamp(self, i):
        return self.w.tabs.menu.item(i).data(LAMP_ROLE)

    def test_new_hardware(self):
        w = self.w
        w.table.setRowCount(0)
        w.add_row()
        self.assertIsNone(self.lamp(w.new_tab))                   # leere Liste: keine Leuchte
        w.add_row(row("10.0.0.11"), "Dell")
        w.add_row(row("10.0.0.12"), "Dell")
        w.update_count()
        self.assertEqual(self.lamp(w.new_tab), OPEN_LAMP)         # noch nicht konfiguriert: rot
        w.mark_done("10.0.0.11")
        w.mark_done("10.0.0.12")
        self.assertEqual(self.lamp(w.new_tab), DONE_LAMP)         # alle fertig: grün
        w.add_row(row("10.0.0.13"), "Dell")
        w.update_count()
        self.assertEqual(self.lamp(w.new_tab), LAB_LAMP)          # neuer Server dazugekommen: gelb
        w.mark_done("10.0.0.13")
        self.assertEqual(self.lamp(w.new_tab), DONE_LAMP)
        w.new_lamp_running = True                                 # Lauf: gelb
        w.new_update_lamp()
        self.assertEqual(self.lamp(w.new_tab), LAB_LAMP)
        w.done(1)
        self.assertEqual(self.lamp(w.new_tab), DONE_LAMP)
        w.table.selectAll()
        w.remove_rows()
        self.assertIsNone(self.lamp(w.new_tab))

    def test_existing_servers(self):
        w = self.w
        w.ex_table.setRowCount(0)
        w.ex_update_lamp()
        self.assertIsNone(self.lamp(w.ex_tab))
        w.ex_add_row(["10.0.0.21", "443", "root", "pw"])
        w.ex_add_row(["10.0.0.22", "443", "root", "pw"])
        self.assertEqual(self.lamp(w.ex_tab), OPEN_LAMP)
        w.ex_rowof = {"10.0.0.21": 0, "10.0.0.22": 1}
        ok = {"ok": True, "vendor": "dell", "model": "R6625", "admin": True, "user": "root"}
        w.ex_row_result("10.0.0.21", ok)
        self.assertEqual(self.lamp(w.ex_tab), LAB_LAMP)           # teils getestet
        w.ex_row_result("10.0.0.22", ok)
        self.assertEqual(self.lamp(w.ex_tab), DONE_LAMP)
        w.ex_add_row(["10.0.0.23", "443", "root", "pw"])
        self.assertEqual(self.lamp(w.ex_tab), LAB_LAMP)           # neuer Server nach dem Test
        w.ex_table.selectRow(2)
        w.ex_remove_rows()
        self.assertEqual(self.lamp(w.ex_tab), DONE_LAMP)
        w.ex_table.item(0, 3).setText("anders")                   # geänderte Zugangsdaten gelten als ungetestet
        self.assertEqual(self.lamp(w.ex_tab), LAB_LAMP)

    def test_uefi_console(self):
        w = self.w
        self.assertIsNone(self.lamp(w.con_tab))
        w.console_led("busy")
        self.assertEqual(self.lamp(w.con_tab), LAB_LAMP)
        w.console_led("live", "10.0.0.11 · Web")
        self.assertEqual(self.lamp(w.con_tab), LAB_LAMP)
        w.console_led("error")
        self.assertIsNone(self.lamp(w.con_tab))
        w.console_led("idle")
        self.assertIsNone(self.lamp(w.con_tab))


if __name__ == "__main__":
    unittest.main()
