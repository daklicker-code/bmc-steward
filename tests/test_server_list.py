"""Serverliste (Window, bmc_steward.main): die neue Spalte „Seriennummer“ und das Zielfeld des
Handy-Scanners.

Seriennummer ist ein optionales, siebtes Feld je Zeile (nach Hostname), nur zur eigenen Übersicht -
anders als Aktuelle IP/Neue IP/Hostname wird es nie an den BMC gesendet und ist kein Pflichtfeld.
Der Handy-Scanner kann seine Treffer wahlweise dort statt ins Passwort-Feld eintragen (Window.scan_to_row).
Gleichzeitig müssen alte, gespeicherte Listen ohne diese Spalte weiter ladbar bleiben.
"""
import tempfile
import unittest
from pathlib import Path
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.main import Window


class TestSeriennummerSpalte(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def setUp(self):
        self.w.table.setRowCount(0)
        self.w.add_row()     # Window.__init__ fügt schon eine leere Zeile ein, hier für jeden Test frisch

    def test_scan_to_row_default_targets_password(self):
        row = self.w.scan_to_row("geheim123")
        self.assertEqual(row, 1)
        self.assertEqual(self.w.cell(0, 1), "geheim123")
        self.assertEqual(self.w.cell(0, 6), "")

    def test_scan_to_row_can_target_serial_column(self):
        row = self.w.scan_to_row("SN-ABC123", col=6)
        self.assertEqual(row, 1)
        self.assertEqual(self.w.cell(0, 6), "SN-ABC123")
        self.assertEqual(self.w.cell(0, 1), "")

    def test_scan_to_row_advances_to_next_row_same_column(self):
        self.w.add_row()
        self.w.table.setCurrentCell(0, 6)
        self.w.scan_to_row("SN-1", col=6)
        self.assertEqual(self.w.table.currentRow(), 1)
        self.assertEqual(self.w.table.currentColumn(), 6)

    def test_scan_to_row_skips_rows_where_target_column_already_filled(self):
        self.w.table.item(0, 6).setText("SN-ALREADY")
        self.w.add_row()
        row = self.w.scan_to_row("SN-NEW", col=6)
        self.assertEqual(row, 2)          # Zeile 1 hat schon eine Seriennummer, Zeile 2 ist dran
        self.assertEqual(self.w.cell(1, 6), "SN-NEW")

    def test_serial_is_not_a_required_field(self):
        self.w.table.item(0, 0).setText("10.0.0.5")
        self.w.table.item(0, 2).setText("10.0.0.6")
        self.w.table.item(0, 5).setText("host1")
        errs = self.w.validate_table()
        self.assertFalse(any(c == 6 for _, c, _ in errs))

    def test_duplicate_serial_is_flagged(self):
        self.w.add_row()
        for r, ip in ((0, "10.0.0.5"), (1, "10.0.0.7")):
            self.w.table.item(r, 0).setText(ip)
            self.w.table.item(r, 6).setText("SAME-SERIAL")
        errs = self.w.validate_table()
        self.assertTrue(any(c == 6 and msg for _, c, msg in errs))

    def test_run_does_not_pass_serial_to_the_engine_row_tuple(self):
        """Engine.run() erwartet genau 6 Felder je Zeile; die Seriennummer (7.) darf dort nicht ankommen."""
        self.w.table.item(0, 0).setText("10.0.0.5")
        self.w.table.item(0, 2).setText("10.0.0.6")
        self.w.table.item(0, 3).setText("255.255.255.0")
        self.w.table.item(0, 5).setText("host1")
        self.w.table.item(0, 6).setText("SN-XYZ")
        todo = [v[:6] for v in self.w.rows(open_only=True)]
        self.assertEqual(len(todo[0]), 6)


class TestListeLadenMitSeriennummer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def setUp(self):
        self.w.table.setRowCount(0)

    def _tmp(self, text):
        path = Path(tempfile.mkstemp(suffix=".csv")[1])
        path.write_text(text, encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    def test_loads_old_format_without_serial_column(self):
        path = self._tmp("10.0.0.5,pw,10.0.0.6,255.255.255.0,10.0.0.1,host1\n")
        self.assertTrue(self.w.load(str(path)))
        self.assertEqual(self.w.cell(0, 0), "10.0.0.5")
        self.assertEqual(self.w.cell(0, 6), "")

    def test_loads_new_format_with_serial_column(self):
        path = self._tmp("10.0.0.5,pw,10.0.0.6,255.255.255.0,10.0.0.1,host1,SN-123\n")
        self.assertTrue(self.w.load(str(path)))
        self.assertEqual(self.w.cell(0, 6), "SN-123")

    def test_loads_new_format_with_serial_fertig_and_hersteller(self):
        path = self._tmp("10.0.0.5,pw,10.0.0.6,255.255.255.0,10.0.0.1,host1,SN-123,fertig,dell\n")
        self.assertTrue(self.w.load(str(path)))
        self.assertEqual(self.w.cell(0, 6), "SN-123")
        self.assertTrue(self.w.is_done(0))

    def test_roundtrip_write_then_load_keeps_serial(self):
        self.w.add_row(["10.0.0.5", "pw", "10.0.0.6", "255.255.255.0", "10.0.0.1", "host1", "SN-RT"])
        path = Path(tempfile.mkstemp(suffix=".csv")[1])
        self.addCleanup(path.unlink)
        self.w.write_file(str(path), with_passwords=True)
        self.w.table.setRowCount(0)
        self.assertTrue(self.w.load(str(path)))
        self.assertEqual(self.w.cell(0, 6), "SN-RT")


class TestVorgabenAnwenden(unittest.TestCase):
    """„Auf Liste anwenden“: Subnetzmaske/Gateway aus den Vorgaben in markierte (sonst alle) vorhandene Zeilen."""

    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def setUp(self):
        w = self.w
        w.table.setRowCount(0)
        for ip in ("10.0.0.5", "10.0.0.6", "10.0.0.7"):
            w.add_row([ip, "pw", "", "", "", "", ""])
        w.add_row()                                   # leere Zeile bleibt unberührt
        w.def_mask.setText("/24")
        w.def_gw.setText("10.0.0.1")
        w.table.clearSelection()

    def test_all_rows_without_selection(self):
        self.w.apply_defaults()
        for r in range(3):
            self.assertEqual((self.w.cell(r, 3), self.w.cell(r, 4)), ("255.255.255.0", "10.0.0.1"))

    def test_only_selected_rows(self):
        self.w.table.selectRow(1)
        self.w.apply_defaults()
        self.assertEqual(self.w.cell(0, 3), "")
        self.assertEqual(self.w.cell(1, 3), "255.255.255.0")
        self.assertEqual(self.w.cell(2, 4), "")

    def test_empty_field_keeps_column(self):
        self.w.def_gw.setText("")
        self.w.add_row(["10.0.0.9", "pw", "", "255.255.0.0", "10.0.9.1", "", ""])
        self.w.apply_defaults()
        self.assertEqual(self.w.cell(4, 3), "255.255.255.0")
        self.assertEqual(self.w.cell(4, 4), "10.0.9.1", "leeres Gateway-Feld überschreibt nichts")

    def test_invalid_values_change_nothing(self):
        self.w.def_gw.setText("kein-gateway")
        self.w.apply_defaults()
        self.assertEqual(self.w.cell(0, 3), "")


if __name__ == "__main__":
    unittest.main()
