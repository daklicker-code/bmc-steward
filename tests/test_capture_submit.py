"""Einsenden von Aufnahmen: unabhängige Nachprüfung vor dem Packen, ZIP, vorbereitetes GitHub-Formular."""
import copy
import ssl
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch

from tests._support import REPO, qapp

qapp()
from bmc_steward import capture as C                  # noqa: E402
from tests.test_capture import sample                 # noqa: E402

VENDORS = ("dell", "hpe", "lenovo", "smc")


def forget_pins(addr):
    """Zufällige Testports wiederholen sich in einem Lauf: alte Zertifikats-Merkungen für diese Adresse
    entfernen, sonst sperrt die Prüfung (zu Recht) ein „anderes Gerät“ unter derselben Adresse."""
    from bmc_steward import trust
    st = trust.store()
    for key in list(st.for_addr(addr)):
        st.forget(key)


class TestSubmissionCheck(unittest.TestCase):
    def redacted(self):
        cap = sample()
        C.redact(cap)
        return cap

    def test_redacted_sample_passes(self):
        self.assertEqual(C.submission_problems(self.redacted()), [])

    def test_unredacted_capture_is_refused(self):
        problems = C.submission_problems(sample())
        self.assertEqual(len(problems), 1)
        self.assertIn("nicht geschwärzt", problems[0])

    def test_leftover_values_are_found_without_naming_them(self):
        for kind, doc in (("MAC", {"MACAddress": "a4:bf:01:23:45:67"}),
                          ("SERIAL", {"SerialNumber": "CN7016309999"}),
                          ("IPV4", {"Address": "10.44.1.7"})):
            with self.subTest(kind=kind):
                cap = self.redacted()
                cap["tree"]["/redfish/v1/Extra"] = dict(doc)
                problems = C.submission_problems(cap)
                self.assertTrue(problems)
                self.assertIn(kind, problems[0])
                for value in doc.values():
                    self.assertNotIn(value, " ".join(problems))     # der Wert selbst steht nie in der Meldung

    def test_keys_certificates_and_mail_addresses_are_found(self):
        for text in ("-----BEGIN CERTIFICATE-----\nMIIB", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH",
                     "admin@firma.example.com"):
            with self.subTest(text=text[:12]):
                cap = self.redacted()
                cap["tree"]["/redfish/v1/Extra"] = {"Description": text}
                self.assertTrue(C.submission_problems(cap))


class TestPacking(unittest.TestCase):
    def test_pack_writes_zip_with_report_and_refuses_bad_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = sample()
            C.redact(cap)
            cap["meta"].update(vendor="dell", model="PowerEdge R660", firmware="7.10.30.00")
            C.save_capture(cap, Path(tmp) / "aufnahme")
            z = C.pack_submission(Path(tmp) / "aufnahme", tmp)
            self.assertEqual(z.name, "bmc-steward-aufnahme-dell-PowerEdge-R660-7.10.30.00.zip")
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
                self.assertIn("aufnahme/FAEHIGKEITEN.txt", names)
                self.assertIn("aufnahme/" + C.META_FILE, names)
                self.assertNotIn("CN7016301XYZ", "".join(zf.read(n).decode("utf-8", "replace") for n in names))

            raw = sample()                                   # ungeschwärzt gespeichert: wird nicht gepackt
            C.save_capture(raw, Path(tmp) / "roh")
            with self.assertRaises(ValueError):
                C.pack_submission(Path(tmp) / "roh", tmp)

    def test_issue_link_prefills_the_form(self):
        cap = {"meta": {"vendor_name": "Dell iDRAC", "model": "R660", "firmware": "7.10"}}
        url = C.issue_url(cap)
        self.assertTrue(url.startswith("https://github.com/daklicker-code/bmc-steward/issues/new?template=aufnahme.yml&"))
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(q["model"], ["R660"])
        self.assertEqual(q["firmware"], ["7.10"])

    def test_issue_form_exists_with_matching_field_ids(self):
        form = (REPO / ".github" / "ISSUE_TEMPLATE" / "aufnahme.yml").read_text(encoding="utf-8")
        for field in ("id: vendor", "id: model", "id: firmware", "id: zip"):
            self.assertIn(field, form)


class TestRealCapturesPass(unittest.TestCase):
    """Geschwärzte Aufnahmen der virtuellen BMCs aller Hersteller bestehen die Nachprüfung (kein Fehlalarm)."""

    def test_vbmc_captures_of_every_vendor_can_be_packed(self):
        from tests.test_vbmc import FACTORY_LOGIN, vbmc
        cert, key = vbmc.ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        for vendor in VENDORS:
            with self.subTest(vendor=vendor), tempfile.TemporaryDirectory() as tmp:
                fleet = vbmc.Fleet(ctx, [0])
                b = vbmc.BMC(vendor, "127.0.0.1", 0, idx=1)
                fleet.start(b)
                try:
                    port = b._redfish[0].socket.getsockname()[1]
                    user, pw = FACTORY_LOGIN[vendor]
                    forget_pins("127.0.0.1:%d" % port)
                    cap, rep, err = C.run_capture("127.0.0.1:%d" % port, user, pw, Path(tmp) / "x", vendor=vendor)
                finally:
                    fleet.stop(b)
                self.assertEqual(err, "")
                self.assertEqual(C.submission_problems(copy.deepcopy(cap)), [])
                self.assertTrue(C.pack_submission(Path(tmp) / "x", tmp).exists())


class TestCaptureTabSubmit(unittest.TestCase):
    def test_button_packs_and_offers_the_form(self):
        from PyQt6.QtWidgets import QMessageBox
        from bmc_steward import i18n, main
        i18n.build_texts()
        w = main.Window()
        self.addCleanup(w.close)
        with tempfile.TemporaryDirectory() as tmp:
            cap = sample()
            C.redact(cap)
            C.save_capture(cap, Path(tmp) / "a")
            w.cap_last_dir = Path(tmp) / "a"
            opened = []
            with patch("bmc_steward.capture_tab.QFileDialog.getExistingDirectory", return_value=tmp), \
                    patch("bmc_steward.capture_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                    patch("bmc_steward.capture_tab.QDesktopServices.openUrl", side_effect=lambda u: opened.append(u)):
                w.capture_submit()
            self.assertTrue(list(Path(tmp).glob("bmc-steward-aufnahme-*.zip")))
            urls = [u.toString() for u in opened]
            self.assertTrue(any("template=aufnahme.yml" in u for u in urls), urls)
            self.assertIn(".zip", w.cap_out.toPlainText())


if __name__ == "__main__":
    unittest.main()
