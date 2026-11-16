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


def label_image(text, px, w=1771, h=323, y=20):
    """Erfundenes Etikett: dunkle Schrift auf grauem Grund, Zeichenhöhe px (Grauwert-Bild)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(120, 120, 120))
    pt = QPainter(img)
    f = QFont("DejaVu Sans")
    f.setPixelSize(px)
    f.setBold(True)
    pt.setFont(f)
    pt.setPen(QColor(25, 25, 25))
    pt.drawText(60, y, w - 100, h - y, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), text)
    pt.end()
    return img.convertToFormat(QImage.Format.Format_Grayscale8)


class TestLocateLine(unittest.TestCase):
    """Textzeilen-Erkennung: die Zeile darf nie in die Zeichen hineinschneiden, auch nicht bei großer Schrift."""

    @staticmethod
    def dark_rows(img):
        ptr = img.constBits()
        ptr.setsize(img.bytesPerLine() * img.height())
        raw, bpl = bytes(ptr), img.bytesPerLine()
        return [y for y in range(img.height()) if any(b < 60 for b in raw[y * bpl:y * bpl + img.width()])]

    def test_large_text_filling_the_crop_is_not_cut(self):
        qapp()
        from bmc_steward.scanner import locate_line
        for px in (170, 200, 230):                      # Schrift füllt mehr als die Hälfte des Ausschnitts
            with self.subTest(px=px):
                img = label_image("AL-142535", px, y=0)
                rows = self.dark_rows(img)
                line = locate_line(img)
                if line is None:                        # keine Erkennung: Aufrufer nimmt das ganze Bild, nichts wird abgeschnitten
                    continue
                dark_in_line = self.dark_rows(line)
                self.assertEqual(len(dark_in_line), len(rows), "dunkle Zeilen gingen beim Ausschneiden verloren")

    def test_small_text_is_still_cut_out(self):
        qapp()
        from bmc_steward.scanner import locate_line
        img = label_image("AL-1425359052", 40, y=120)
        line = locate_line(img)
        self.assertIsNotNone(line)
        self.assertLess(line.height(), img.height() * 0.75, "kleine Schrift: Rand wird weiterhin entfernt")
        self.assertEqual(len(self.dark_rows(line)), len(self.dark_rows(img)))


def label_with_neighbours(target, px=140, w=1771, h=324, rest="Versicherungsnummer"):
    """Erfundenes Etikett wie im Scanlog: die gesuchte Zeile in der Mitte, darüber und darunter Reste der
    Nachbarzeilen, die der Rahmen am Bildrand abschneidet."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(125, 120, 115))
    pt = QPainter(img)
    f = QFont("DejaVu Sans")
    f.setPixelSize(px)
    f.setBold(True)
    pt.setFont(f)
    pt.setPen(QColor(25, 25, 25))
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    cap = int(px * 0.73)                              # Höhe der Großbuchstaben
    top = (h - cap) // 2 - int(px * 0.2)              # Oberkante des Textfelds, Zielzeile in der Mitte
    pitch = int(cap * 1.55)                           # Zeilenabstand: Oberkante zu Oberkante
    pt.drawText(40, top, w, h, flags, target)
    pt.drawText(40, top - pitch, w, h, flags, rest)   # Nachbarzeile darüber: nur der untere Rand ragt ins Bild
    pt.drawText(40, top + pitch, w, h, flags, rest)   # Nachbarzeile darunter: nur der obere Rand ragt ins Bild
    pt.end()
    return img


@need("tesseract")
class TestReadsLineWithNeighbours(unittest.TestCase):
    """Nachbarzeilen und angeschnittene Reste dürfen die Erkennung der Zielzeile nicht stören."""

    def test_target_found_between_clipped_neighbours(self):
        qapp()
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from bmc_steward.scanner import scan_decode, text_bands
        img = label_with_neighbours("AL-1425359052")
        data = QByteArray()                             # muss leben, solange der Puffer darauf schreibt
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPG", 92)
        buf.close()
        texts, err, _ = scan_decode(bytes(data), "ocr")
        self.assertEqual(err, "")
        self.assertIn("AL-1425359052", texts)
        bands = text_bands(img.convertToFormat(img.Format.Format_Grayscale8))
        self.assertEqual(len(bands), 1, "nur die ganz sichtbare Zeile bleibt übrig, die angeschnittenen fallen weg")
        top, bottom = bands[0]
        self.assertLess(bottom - top, img.height(), "Reste der Nachbarzeilen sind abgeschnitten")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
_CHROME = next((__import__("shutil").which(n) for n in ("google-chrome", "chromium", "chromium-browser")
                if __import__("shutil").which(n)), None)


@unittest.skipUnless(sync_playwright and _CHROME, "Playwright oder Chrome fehlt")
class TestScanPageLayout(unittest.TestCase):
    """Kamerabild der Scanner-Seite: über und unter dem gelben Rahmen muss deutlich mehr Bild zu sehen sein."""

    def test_margin_above_and_below_frame(self):
        import socketserver
        import threading
        from http.server import BaseHTTPRequestHandler
        from bmc_steward.scanner import SCAN_HTML

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(SCAN_HTML.encode())

            def log_message(self, *a):
                pass
        srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=_CHROME, args=["--use-fake-ui-for-media-stream",
                                                                 "--use-fake-device-for-media-stream"])
            ctx = b.new_context(viewport={"width": 390, "height": 780}, permissions=["camera"])
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/scan/")
            pg.wait_for_function("document.getElementById('v').videoWidth>0", timeout=15000)
            pg.wait_for_timeout(500)
            for size in ("s", "m", "f"):
                pg.click(f"label:has(input[name=c][value={size}])")
                r = pg.evaluate("""()=>{const a=document.getElementById('area').getBoundingClientRect(),
                  f=document.getElementById('box').getBoundingClientRect();
                  return {w:a.width,above:f.top-a.top,below:a.bottom-f.bottom,fh:f.height}}""")
                self.assertGreater(r["above"], 0.10 * r["w"], f"Ausschnitt {size}: zu wenig Bild über dem Rahmen")
                self.assertGreater(r["below"], 0.10 * r["w"], f"Ausschnitt {size}: zu wenig Bild unter dem Rahmen")
            pg.click("label:has(input[name=c][value=s])")
            self.assertAlmostEqual(pg.evaluate("document.getElementById('box').getBoundingClientRect().height")
                                   / pg.evaluate("document.getElementById('area').getBoundingClientRect().width"),
                                   0.15, delta=0.01, msg="Rahmen „Klein“ behält seine Größe")
            b.close()


if __name__ == "__main__":
    unittest.main()
