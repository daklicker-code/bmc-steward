"""Integration der Schlüsselbund-Verwaltung (vault.py, Version 3.0.26) in die Serverliste (Window,
bmc_steward.main): Haken „Passwörter im Schlüsselbund speichern“, automatisches Auffüllen leerer
Passwort-Felder beim Laden, Übernahme von Hand eingetragener Passwörter, und das Leeren des
Schlüsselbunds für die aktuelle Liste. Läuft wie test_vault.py ohne echten Secret-Service-Dienst, mit
einem kleinen gefälschten Schlüsselbund-Backend."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import qapp
from tests.test_vault import FakeKeyring

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward import vault
from bmc_steward.main import Window


class TestVaultIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def setUp(self):
        self.fake = FakeKeyring()
        patcher = patch.object(vault, "keyring", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        avail_patcher = patch.object(vault, "available", return_value=True)
        avail_patcher.start()
        self.addCleanup(avail_patcher.stop)
        self.w.table.setRowCount(0)
        self.w.add_row()
        self.w.vault_use.setChecked(False)

    def fill_row(self, r, ip="", pw="", serial=""):
        for c, val in ((0, ip), (1, pw), (6, serial)):
            self.w.table.item(r, c).setText(val)

    # ---------- vault_autofill ----------
    def test_autofill_fills_empty_password_from_vault_by_serial(self):
        vault.store("SN-1", "", "AusDemSchluesselbund!")
        self.fill_row(0, ip="10.0.0.1", serial="SN-1")
        n = self.w.vault_autofill()
        self.assertEqual(n, 1)
        self.assertEqual(self.w.cell(0, 1), "AusDemSchluesselbund!")

    def test_autofill_falls_back_to_ip_without_serial(self):
        vault.store("", "10.0.0.1", "PerIp!")
        self.fill_row(0, ip="10.0.0.1")
        n = self.w.vault_autofill()
        self.assertEqual(n, 1)
        self.assertEqual(self.w.cell(0, 1), "PerIp!")

    def test_autofill_never_overwrites_an_existing_password(self):
        vault.store("SN-1", "", "AusDemSchluesselbund!")
        self.fill_row(0, ip="10.0.0.1", pw="SchonDa!", serial="SN-1")
        n = self.w.vault_autofill()
        self.assertEqual(n, 0)
        self.assertEqual(self.w.cell(0, 1), "SchonDa!")

    def test_autofill_is_a_noop_without_a_vault(self):
        with patch.object(vault, "available", return_value=False):
            self.fill_row(0, ip="10.0.0.1")
            self.assertEqual(self.w.vault_autofill(), 0)

    # ---------- vault_on_item_changed ----------
    def test_password_edit_is_stored_live_when_checkbox_is_checked(self):
        self.w.vault_use.setChecked(True)
        self.fill_row(0, ip="10.0.0.1", serial="SN-2")
        self.w.table.item(0, 1).setText("NeuEingegeben!")
        self.assertEqual(vault.fetch("SN-2", "10.0.0.1"), "NeuEingegeben!")

    def test_password_edit_is_not_stored_when_checkbox_is_unchecked(self):
        self.w.vault_use.setChecked(False)
        self.fill_row(0, ip="10.0.0.1", serial="SN-3")
        self.w.table.item(0, 1).setText("NichtGesichert!")
        self.assertEqual(vault.fetch("SN-3", "10.0.0.1"), "")

    def test_filling_in_the_serial_afterwards_moves_the_entry_from_ip_to_serial(self):
        self.w.vault_use.setChecked(True)
        self.fill_row(0, ip="10.0.0.1", pw="BleibtGleich!")
        self.assertEqual(vault.fetch("", "10.0.0.1"), "BleibtGleich!")
        self.w.table.item(0, 6).setText("SN-4")
        self.assertEqual(vault.fetch("", "10.0.0.1"), "")
        self.assertEqual(vault.fetch("SN-4", "10.0.0.1"), "BleibtGleich!")

    # ---------- vault_use_toggled / vault_store_all_rows ----------
    def test_enabling_the_checkbox_pushes_existing_passwords_into_the_vault(self):
        self.fill_row(0, ip="10.0.0.1", pw="Schon1!", serial="SN-5")
        self.w.add_row()
        self.fill_row(1, ip="10.0.0.2", pw="Schon2!")
        self.w.vault_use.setChecked(True)
        self.assertEqual(vault.fetch("SN-5", "10.0.0.1"), "Schon1!")
        self.assertEqual(vault.fetch("", "10.0.0.2"), "Schon2!")

    def test_rows_without_a_password_are_skipped(self):
        self.fill_row(0, ip="10.0.0.1")
        self.w.vault_use.setChecked(True)
        self.assertEqual(vault.fetch("", "10.0.0.1"), "")

    # ---------- vault_clear_list ----------
    def test_clear_list_removes_every_row_and_asks_first(self):
        self.fill_row(0, ip="10.0.0.1", pw="Weg1!", serial="SN-6")
        self.w.add_row()
        self.fill_row(1, ip="10.0.0.2", pw="Weg2!")
        self.w.vault_use.setChecked(True)
        asked = []
        with patch.object(self.w, "confirm", lambda *a, **k: (asked.append(a) or True)):
            self.w.vault_clear_list()
        self.assertTrue(asked)
        self.assertEqual(vault.fetch("SN-6", "10.0.0.1"), "")
        self.assertEqual(vault.fetch("", "10.0.0.2"), "")

    def test_clear_list_does_nothing_without_confirmation(self):
        self.fill_row(0, ip="10.0.0.1", pw="Bleibt!", serial="SN-7")
        self.w.vault_use.setChecked(True)
        with patch.object(self.w, "confirm", lambda *a, **k: False):
            self.w.vault_clear_list()
        self.assertEqual(vault.fetch("SN-7", "10.0.0.1"), "Bleibt!")

    # ---------- load() füllt aus dem Schlüsselbund auf ----------
    def test_load_autofills_passwords_for_a_list_saved_without_them(self):
        vault.store("SN-8", "", "AusDerListeGeladen!")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liste.csv"
            path.write_text("10.0.0.9,,,,,host9,SN-8\n", encoding="utf-8")
            self.w.table.setRowCount(0)
            self.assertTrue(self.w.load(str(path)))
        self.assertEqual(self.w.cell(0, 1), "AusDerListeGeladen!")
        self.assertEqual(self.w._vault_autofilled, 1)


if __name__ == "__main__":
    unittest.main()
