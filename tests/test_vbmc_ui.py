"""Bedient die Weboberflächen der virtuellen BMCs in einem echten Browser (Playwright + Chrome/Chromium).

Fängt Fehler, die reine HTTP-Tests nicht sehen (z. B. ein JavaScript-Fehler beim Klick auf einen Knopf).
Wird übersprungen, wenn Playwright oder ein Chrome/Chromium fehlen."""
import os
import shutil
import ssl
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools", "vbmc"))
import vbmc  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
CHROME = next((shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser") if shutil.which(n)), None)

LOGIN = {"hpe": ("Administrator", "Admin123!"), "dell": ("root", "calvin"),
         "lenovo": ("USERID", "PASSW0RD"), "smc": ("ADMIN", "ADMIN")}


@unittest.skipIf(sync_playwright is None or CHROME is None, "Playwright oder Chrome/Chromium fehlt")
class TestBrowserUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cert, key = vbmc.ensure_cert()
        cls.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.ctx.load_cert_chain(cert, key)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def _open(self, vendor, **kw):
        fleet = vbmc.Fleet(self.ctx, [0])
        b = vbmc.BMC(vendor, "127.0.0.1", 0, idx=1, **kw)
        fleet.start(b)
        self.addCleanup(fleet.stop, b)
        port = b._redfish[0].socket.getsockname()[1]
        page = self.browser.new_context(ignore_https_errors=True).new_page()
        self.addCleanup(page.context.close)
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.goto(f"https://127.0.0.1:{port}/")
        user, pw = LOGIN[vendor]
        page.fill("#username", user)
        page.fill("#password", pw)
        page.click("#login-btn")
        page.wait_for_selector(".card")
        return b, page, f"https://127.0.0.1:{port}"

    def test_every_page_renders_without_script_errors(self):
        for v in LOGIN:
            with self.subTest(v):
                b, page, base = self._open(v)
                for k in ("overview", "sysinfo", "power", "thermal", "events", "console", "network", "users",
                          "firmware", "bios", "vmedia", "license"):
                    page.goto(f"{base}/#/{k}")
                    page.wait_for_selector("#main .card, #main .err", timeout=8000)
                    err = page.query_selector("#main .err")
                    self.assertIsNone(err, f"{v}/{k}: Fehler auf der Seite: {err.inner_text() if err else ''}")
                self.assertEqual(self.errors, [], f"{v}: JavaScript-Fehler")

    def test_power_button_changes_server_state(self):
        b, page, base = self._open("hpe")
        page.goto(f"{base}/#/power")
        page.wait_for_selector("[data-r=ForceOff]")
        page.click("[data-r=ForceOff]")
        page.click("#m-yes")
        page.wait_for_selector(".toast")
        self.assertEqual(b.sim.power_state, "Off")
        self.assertEqual(self.errors, [])

    def test_license_and_virtual_media_flow(self):
        b, page, base = self._open("dell", license="none")
        page.goto(f"{base}/#/vmedia")
        page.wait_for_selector("#vm-ins")
        page.fill("#vm-url", "http://10.0.0.9/x.iso")
        page.click("#vm-ins")
        page.wait_for_selector(".toast.bad")                       # ohne Lizenz abgelehnt
        page.goto(f"{base}/#/license")
        page.wait_for_selector("#lic-ins")
        page.fill("#lic-key", "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
        page.click("#lic-ins")
        page.wait_for_selector("#lic-rm")
        self.assertTrue(b.sim.licensed)
        page.goto(f"{base}/#/vmedia")
        page.wait_for_selector("#vm-ins")
        page.select_option("#vm-dev", "CD")                         # Dell: erstes Gerät wäre der USB-Stick
        page.fill("#vm-url", "http://10.0.0.9/x.iso")
        page.click("#vm-ins")
        page.wait_for_selector("[data-eject]")
        self.assertEqual(b.sim.cd_image_name(), "x.iso")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
