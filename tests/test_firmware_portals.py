"""Reiter „Firmware“: Knöpfe zu den Download-Seiten der vier Hersteller (öffnen nur den Browser, laden nichts)."""
import unittest
from unittest.mock import patch
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.main import Window
from bmc_steward.firmware_tab import FW_PORTALS


class TestFirmwarePortale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def test_vier_hersteller_mit_https(self):
        self.assertEqual([n for n, _, _ in FW_PORTALS], ["Dell", "HPE", "Lenovo", "Supermicro"])
        for name, url, tip in FW_PORTALS:
            self.assertTrue(url.startswith("https://"), name)
            self.assertIn(name, self.w.fw_portal_btns)

    def test_klick_oeffnet_die_richtige_adresse(self):
        for name, url, _ in FW_PORTALS:
            with patch("bmc_steward.firmware_tab.QDesktopServices.openUrl", return_value=True) as op:
                self.w.fw_portal_btns[name].click()
            op.assert_called_once()
            self.assertEqual(op.call_args[0][0].toString(), url)

    def test_hinweise_in_allen_sprachen_uebersetzt(self):
        try:
            for lang in ("en", "fr"):
                i18n.set_language(lang)
                for _, _, tip in FW_PORTALS:
                    self.assertNotEqual(i18n.T(tip), tip, f"{lang}: Übersetzung fehlt")
        finally:
            i18n.set_language("de")


if __name__ == "__main__":
    unittest.main()
