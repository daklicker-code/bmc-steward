"""Zertifikats-Vertrauen: Ein unbekanntes Gerät mit anderem Zertifikat an einer Adresse, unter der schon ein
anderes Gerät gemerkt ist, wird gesperrt (außer bei „Neuware“, dort nur ein Hinweis). Sonst könnte jemand, der
sich dazwischenschaltet, einfach eine neue Geräte-Kennung melden und bekäme das Passwort."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._support import qapp

qapp()
from bmc_steward import trust
from bmc_steward.redfish import Engine, RedfishClient


class TestNewDeviceAtKnownAddress(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="bmc-pins-test-")
        # Aufräumen tolerant: Hintergrund-Threads früherer Tests können über trust.store() noch hineinschreiben
        self.addCleanup(shutil.rmtree, tmp, True)
        self.st = trust.PinStore(Path(tmp) / "pins.json")
        self.st.check("uuid:alt", "aa" * 32, "10.0.0.5")
        patcher = mock.patch.object(trust, "store", return_value=self.st)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, cls=RedfishClient, key="uuid:neu", fp="bb" * 32):
        lines = []
        rc = cls.__new__(cls)
        RedfishClient.__init__(rc, dry=False, log=lambda text, kind="": lines.append((text, kind)))
        rc.fp["10.0.0.5"] = fp
        rc.device_key = lambda ip, mode: key
        return rc, lines

    def test_blocks_unknown_device_with_other_certificate(self):
        rc, lines = self.client()
        self.assertFalse(rc.trust_device("10.0.0.5", "modern"))
        self.assertIn("10.0.0.5", rc.pin_err)
        self.assertNotIn("uuid:neu", self.st.pins, "gesperrtes Gerät darf nicht gemerkt werden")
        self.assertTrue(any("GESPERRT" in t for t, _ in lines))

    def test_known_device_unaffected(self):
        rc, _ = self.client(key="uuid:alt", fp="aa" * 32)
        self.assertTrue(rc.trust_device("10.0.0.5", "modern"))

    def test_other_address_unaffected(self):
        rc, _ = self.client()
        rc.fp["10.0.0.6"] = rc.fp.pop("10.0.0.5")
        self.assertTrue(rc.trust_device("10.0.0.6", "modern"))

    def test_removing_old_entry_allows_again(self):
        self.st.forget("uuid:alt")
        rc, _ = self.client()
        self.assertTrue(rc.trust_device("10.0.0.5", "modern"))
        self.assertIn("uuid:neu", self.st.pins)

    def test_new_hardware_only_warns(self):
        rc, lines = self.client(cls=Engine)
        self.assertTrue(rc.trust_device("10.0.0.5", "modern"))
        self.assertTrue(any(kind == "warn" for _, kind in lines), lines)


if __name__ == "__main__":
    unittest.main()
