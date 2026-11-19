"""Virtuelle BMCs nach Redfish-Standard: jede Ressource mit @odata.type, keine Verweise ins Leere, $metadata.
Grundlage dafür, dass der DMTF-Validator in der CI (tools/ci/validate_vbmc.py) etwas Sinnvolles prüfen kann."""
import json
import ssl
import tempfile
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from tests._support import forget_pins, qapp

qapp()
from bmc_steward import capture as C                  # noqa: E402

VENDORS = ("dell", "hpe", "lenovo", "smc")
# Pfade, die die Aufnahme gezielt abfragt, ohne dass ein Simulator sie verlinkt (404 ist dort richtig)
PROBED_ONLY = {"/redfish/v1/EventService", "/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1"}
VENDOR_OWN = ("/Attributes",)                         # herstellereigene Ressourcen ohne DMTF-Typ


def links(doc):
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == "@odata.id" and isinstance(v, str):
                yield v
            else:
                yield from links(v)
    elif isinstance(doc, list):
        for v in doc:
            yield from links(v)


class TestVbmcOData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_vbmc import FACTORY_LOGIN, vbmc
        cls.vbmc, cls.login = vbmc, FACTORY_LOGIN
        cert, key = vbmc.ensure_cert()
        cls.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.ctx.load_cert_chain(cert, key)

    def crawl(self, vendor):
        with tempfile.TemporaryDirectory() as tmp:
            fleet = self.vbmc.Fleet(self.ctx, [0])
            b = self.vbmc.BMC(vendor, "127.0.0.1", 0, idx=1)
            fleet.start(b)
            try:
                port = b._redfish[0].socket.getsockname()[1]
                user, pw = self.login[vendor]
                forget_pins("127.0.0.1:%d" % port)
                cap, _, err = C.run_capture("127.0.0.1:%d" % port, user, pw, Path(tmp) / "x", vendor=vendor,
                                            do_redact=False)
                ctx = ssl.create_default_context()
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                with urllib.request.urlopen("https://127.0.0.1:%d/redfish/v1/$metadata" % port, context=ctx) as r:
                    meta = r.read().decode()
            finally:
                fleet.stop(b)
        self.assertEqual(err, "")
        return cap, meta

    def test_every_resource_is_typed_and_every_link_resolves(self):
        for vendor in VENDORS:
            with self.subTest(vendor=vendor):
                cap, meta = self.crawl(vendor)
                tree = cap["tree"]
                for path, doc in tree.items():
                    if path.endswith(VENDOR_OWN):
                        continue
                    self.assertTrue(str(doc.get("@odata.type", "")).startswith("#"), path)
                    self.assertTrue(doc.get("Name"), path)
                    for target in links(doc):
                        target = target.split("#", 1)[0].rstrip("/") or target
                        if target in ("/redfish/v1",) or "/Actions/" in target:
                            continue
                        with self.subTest(link=target):
                            self.assertNotIn(cap["errors"].get(target), (404,), f"{path} verweist auf {target}")
                self.assertLessEqual({p for p, c in cap["errors"].items() if c == 404}, PROBED_ONLY)

                root = ET.fromstring(meta)                # $metadata ist gültiges XML mit DMTF-Verweisen
                ns = {"edmx": "http://docs.oasis-open.org/odata/ns/edmx"}
                refs = {r.get("Uri") for r in root.findall("edmx:Reference", ns)}
                self.assertIn("http://redfish.dmtf.org/schemas/v1/ServiceRoot_v1.xml", refs)
                used = {str(d.get("@odata.type", ""))[1:].split(".")[0] for d in tree.values() if d.get("@odata.type")}
                listed = {u.rsplit("/", 1)[1][:-len("_v1.xml")] for u in refs}
                std = {t for t in used if not t.startswith(("Dell", "Hpe", "Hp", "Smc", "Lenovo"))}
                self.assertLessEqual(std, listed, json.dumps(sorted(std - listed)))


if __name__ == "__main__":
    unittest.main()
