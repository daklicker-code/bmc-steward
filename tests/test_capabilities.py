"""Fähigkeitsmatrix (capabilities.py): stimmt mit dem überein, was die Engines tun, erkennt die Hersteller der
Serverliste und graut auf der Seite „Dienste“ aus, was bei keinem Server der Liste geht."""
import unittest
from unittest.mock import patch

from tests._support import qapp

qapp()
from bmc_steward import capabilities as caps          # noqa: E402
from bmc_steward.redfish import RedfishClient, ServicesEngine   # noqa: E402


class TestMatrix(unittest.TestCase):
    def test_every_feature_has_a_label_and_only_known_vendors(self):
        labels = caps.labels()
        for key, row in caps.MATRIX.items():
            with self.subTest(feature=key):
                self.assertIn(key, labels)
                self.assertLessEqual(set(row), set(caps.VENDORS))
                self.assertLessEqual(set(row.values()), {caps.YES, caps.PARTIAL, caps.NO})

    def test_vendor_labels_of_the_server_list_are_recognised(self):
        for key, name in RedfishClient.NAME.items():
            with self.subTest(vendor=key):
                self.assertEqual(caps.vendor_from_label(name), key)
                self.assertEqual(caps.vendor_from_label(f"{name} (Typ X42)"), key)
        for text in ("", "unbekannt", "nicht erreichbar", "Fehler"):
            self.assertIsNone(caps.vendor_from_label(text))

    def test_check_greys_out_only_when_no_vendor_can(self):
        self.assertEqual(caps.check("syslog", set()), (True, ""))                 # nichts erkannt: nichts sperren
        usable, hint = caps.check("syslog", {"smc", "lenovo"})
        self.assertFalse(usable)
        self.assertIn("Supermicro", hint)
        usable, hint = caps.check("syslog", {"dell", "smc"})
        self.assertTrue(usable)
        self.assertIn("Supermicro", hint)
        self.assertNotIn("Dell", hint)
        self.assertEqual(caps.check("ntp", {"dell", "hpe"}), (True, ""))
        self.assertEqual(caps.support("ntp", "irgendwas"), caps.PARTIAL)

    def test_matrix_html_lists_all_vendors_and_features(self):
        html = caps.matrix_html()
        for name in caps.vendor_names().values():
            self.assertIn(name, html)
        self.assertEqual(html.count("<tr>"), len(caps.MATRIX) + 1)


class TestMatrixMatchesEngines(unittest.TestCase):
    """Was die Matrix „nein“ nennt, überspringt die Engine ohne einen einzigen Aufruf am BMC."""

    def engine(self):
        eng = ServicesEngine(rows=[], cur_user="", services={}, dry=False, log=lambda *a, **k: None)
        eng.cur_user = "admin"
        return eng

    def test_no_entries_are_skipped_without_touching_the_bmc(self):
        eng = self.engine()
        calls = {"syslog": lambda v: eng.svc_syslog("10.0.0.5", "pw", v, {"server": "10.0.0.9"}),
                 "nicmode": lambda v: eng.svc_nicmode("10.0.0.5", "pw", v, {"mode": "dedicated"})}
        with patch.object(ServicesEngine, "api", side_effect=AssertionError("BMC darf nicht gefragt werden")):
            for feature, call in calls.items():
                for v in ("dell", "hpe", "lenovo", "smc"):
                    if caps.support(feature, v) == caps.NO:
                        with self.subTest(feature=feature, vendor=v):
                            self.assertIsNone(call(v))

    def test_yes_entries_are_attempted(self):
        eng = self.engine()
        seen = []

        def fake_api(*args, **kwargs):
            seen.append(args)
            return 200
        with patch.object(ServicesEngine, "api", side_effect=fake_api):
            for v in ("dell", "hpe"):
                self.assertEqual(caps.support("syslog", v), caps.YES)
                seen.clear()
                eng.svc_syslog("10.0.0.5", "pw", v, {"server": "10.0.0.9"})
                self.assertTrue(seen, v)


class TestServicesPage(unittest.TestCase):
    def setUp(self):
        from bmc_steward import i18n, main
        i18n.build_texts()
        self.w = main.Window()
        self.addCleanup(self.close)

    def close(self):
        self.w.dirty = self.w.plan_dirty = False      # sonst fragt das Fenster nach dem Speichern
        self.w.close()

    def set_vendors(self, *labels):
        from bmc_steward import i18n
        w = self.w
        w.table.setRowCount(0)
        for n, label in enumerate(labels, 1):
            vals = [""] * len(i18n.COLS)
            vals[0] = f"192.0.2.{n}"
            with patch.object(type(w), "probe_vendor", lambda *a: None):
                w.add_row(vals, label)
        w.services_update_caps()

    def test_unsupported_sections_are_greyed_out(self):
        w = self.w
        self.set_vendors("Supermicro (X13)", "Lenovo (XCC) (SR650)")
        self.assertFalse(w.syslog_box.isEnabled())
        self.assertFalse(w.nicmode_box.isEnabled())
        self.assertFalse(w.ntp_tz.isEnabled())
        self.assertTrue(w.ntp_box.isEnabled())
        self.assertIn("Supermicro", w.syslog_box.toolTip())

    def test_mixed_list_keeps_section_and_names_skipped_vendors(self):
        w = self.w
        self.set_vendors("Dell (iDRAC) (R660)", "Supermicro")
        self.assertTrue(w.syslog_box.isEnabled())
        hint = w._cap_hints["syslog"]
        self.assertFalse(hint.isHidden())
        self.assertIn("Supermicro", hint.text())

    def test_fixed_vendor_choice_wins_and_unknown_list_blocks_nothing(self):
        w = self.w
        self.set_vendors("unbekannt", "nicht erreichbar")
        self.assertTrue(all(b.isEnabled() for b in (w.syslog_box, w.nicmode_box, w.ntp_box)))
        w.svc_vendor.setCurrentIndex(w.svc_vendor.findData("smc"))
        self.assertFalse(w.syslog_box.isEnabled())
        w.svc_vendor.setCurrentIndex(w.svc_vendor.findData("dell"))
        self.assertTrue(w.syslog_box.isEnabled())

    def test_dialog_shows_matrix(self):
        from PyQt6.QtWidgets import QDialog, QTextBrowser
        shown = {}

        def fake_exec(dlg):
            shown["html"] = dlg.findChild(QTextBrowser).toHtml()
            return 0
        with patch.object(QDialog, "exec", fake_exec):
            self.w.show_capabilities()
        self.assertIn("Supermicro", shown["html"])


if __name__ == "__main__":
    unittest.main()
