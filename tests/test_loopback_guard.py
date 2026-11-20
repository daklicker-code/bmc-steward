"""Testlabor im Modus Loopback: „Neue IP aus meinem echten Netz“ ist nur für den Trockenlauf. Ein scharfer
Lauf von 127.x auf eine echte Adresse wird vorher abgelehnt, statt vier Minuten auf „FEHLT“ zu warten."""
import unittest
from unittest.mock import patch

from tests._support import qapp


class TestHelper(unittest.TestCase):
    def test_loopback_to_real(self):
        from bmc_steward.core import loopback_to_real
        self.assertTrue(loopback_to_real("127.9.0.11:8443", "192.168.0.241"))
        self.assertFalse(loopback_to_real("127.9.0.11:8443", "127.9.0.51:8443"))
        self.assertFalse(loopback_to_real("192.168.0.120", "10.0.0.5"))
        self.assertFalse(loopback_to_real("", "10.0.0.5"))


class TestWindowRefusesLiveRun(unittest.TestCase):
    def test_live_run_from_loopback_to_real_net_is_refused(self):
        qapp()
        from bmc_steward import i18n
        i18n.set_language("de")
        from bmc_steward.main import Window
        w = Window()
        self.addCleanup(w.deleteLater)
        w.table.setRowCount(0)
        w.add_row(["127.9.0.11:8443", "calvin", "192.168.0.241", "255.255.255.0", "192.168.0.1", "vsrv-dell-1", ""])
        w.new_user.setText("klicker")
        w.new_pass.setText("Geheim123!")
        w.new_pass2.setText("Geheim123!")
        w.dry.setChecked(False)
        boxes = []
        with patch.object(type(w), "confirm", lambda *a, **k: True), \
                patch("bmc_steward.main.QMessageBox.warning", lambda *a, **k: boxes.append(a[2])):
            w.run()
        self.assertIsNone(w.worker)
        self.assertEqual(len(boxes), 1)
        self.assertIn("127.9.0.11:8443", boxes[0])


if __name__ == "__main__":
    unittest.main()
