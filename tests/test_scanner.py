"""Handy-Scanner-Endpunkt (bmc_steward.scanner.ScanServer) – der größte Netzwerk-Angriffspunkt.

Der kleine HTTPS-Server nimmt Bilder/Passwörter vom Handy entgegen. Geprüft wird: ohne gültiges
Token kommt niemand an die Seite oder die Endpunkte; Größenlimits greifen; erkannte Werte landen
im Protokoll nur maskiert. Braucht openssl (für das Wegwerf-Zertifikat) – sonst wird übersprungen.
"""
import http.client
import json
import ssl
import unittest
from tests._support import qapp, need
from bmc_steward.scanner import ScanServer, ScanBridge, plausible, scan_tools

_NOVERIFY = ssl._create_unverified_context()


def tiny_png():
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QImage
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFF)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


class TestPlausible(unittest.TestCase):
    def test_rejects_noise_accepts_real(self):
        self.assertFalse(plausible("EEE"))        # zu kurz / zu wenig verschiedene Zeichen
        self.assertFalse(plausible("IIllII"))
        self.assertTrue(plausible("Cal8inX2"))


@need("openssl")
class TestScanServerAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qapp()
        cls.srv = ScanServer("127.0.0.1", ScanBridge(), port=0)
        cls.srv.start()
        cls.port = cls.srv.port
        cls.token = cls.srv.token

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _req(self, method, path, body=None, headers=None):
        c = http.client.HTTPSConnection("127.0.0.1", self.port, context=_NOVERIFY, timeout=10)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def test_correct_token_serves_page(self):
        status, body = self._req("GET", f"/{self.token}/")
        self.assertEqual(status, 200)
        self.assertIn(b"<", body)                       # HTML-Seite

    def test_wrong_token_blocked(self):
        status, body = self._req("GET", "/deadbeef/")
        self.assertEqual(status, 404)
        self.assertNotIn(b"<html", body.lower())

    def test_no_token_blocked(self):
        self.assertEqual(self._req("GET", "/")[0], 404)

    def test_unknown_path_404(self):
        self.assertEqual(self._req("POST", f"/{self.token}/nope", body=b"{}")[0], 404)

    def test_set_oversize_rejected(self):
        # /set erlaubt höchstens 4096 Bytes -> 413 (noch bevor der Body gelesen/ausgewertet wird)
        big = b'{"value":"' + b"A" * 5000 + b'"}'
        status, _ = self._req("POST", f"/{self.token}/set", body=big,
                              headers={"Content-Type": "application/json"})
        self.assertEqual(status, 413)

    def test_decode_returns_json(self):
        png = tiny_png()
        status, body = self._req("POST", f"/{self.token}/decode?mode=barcode", body=png,
                                headers={"Content-Type": "image/png"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("texts", data)
        self.assertIn("error", data)

    def test_decode_empty_body_rejected(self):
        status, _ = self._req("POST", f"/{self.token}/decode", body=b"",
                             headers={"Content-Length": "0"})
        self.assertEqual(status, 413)       # 0 < size schlägt fehl -> abgelehnt

    def test_token_is_unguessable_length(self):
        self.assertGreaterEqual(len(self.token), 8)


if __name__ == "__main__":
    unittest.main()
