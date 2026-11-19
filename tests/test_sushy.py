"""Gegenprobe mit einer fremden Redfish-Implementierung: sushy-tools (OpenStack), Treiber „fake“.

Unsere eigenen Simulatoren (tools/vbmc) teilen sich Annahmen mit dem Client. sushy-emulator ist unabhängig davon
geschrieben und herstellerneutral; hier zeigt sich, ob BMC-Steward mit einem Gerät, das es nicht kennt,
vernünftig umgeht: Hersteller „unbekannt“ statt eines falschen Ablaufs, Bestandsprüfung ohne Absturz, Aufnahme,
Schwärzung und Fähigkeitsbericht.

Läuft nur, wenn sushy-emulator da ist: Umgebungsvariable SUSHY_EMULATOR (Pfad) oder im PATH. In der CI richtet
der Job „sushy“ ihn in einer eigenen Umgebung aus tools/ci/sushy.lock ein.
"""
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests._support import qapp

qapp()
from bmc_steward import capture as C                  # noqa: E402
from bmc_steward.redfish import ProbeEngine, RedfishClient   # noqa: E402

EXE = os.environ.get("SUSHY_EMULATOR") or shutil.which("sushy-emulator")


def forget_pins(addr):
    """Zufällige Testports wiederholen sich in einem Lauf: alte Zertifikats-Merkungen für diese Adresse
    entfernen, sonst sperrt die Prüfung (zu Recht) ein „anderes Gerät“ unter derselben Adresse."""
    from bmc_steward import trust
    st = trust.store()
    for key in list(st.for_addr(addr)):
        st.forget(key)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(EXE, "sushy-emulator nicht installiert (SUSHY_EMULATOR setzen)")
class TestAgainstSushy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_vbmc import vbmc
        cert, key = vbmc.ensure_cert()
        cls.tmp = tempfile.TemporaryDirectory()
        conf = Path(cls.tmp.name) / "sushy.conf"
        conf.write_text("SUSHY_EMULATOR_STATE_DIR = %r\n" % str(Path(cls.tmp.name) / "state"), encoding="utf-8")
        cls.port = free_port()
        cls.addr = "127.0.0.1:%d" % cls.port
        forget_pins(cls.addr)
        cls.proc = subprocess.Popen([EXE, "--fake", "--config", str(conf), "-i", "127.0.0.1", "-p", str(cls.port),
                                     "--ssl-certificate", str(cert), "--ssl-key", str(key)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            if cls.proc.poll() is not None:
                raise RuntimeError("sushy-emulator beendet: " + cls.proc.stderr.read()[-500:])
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("sushy-emulator antwortet nicht")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.tmp.cleanup()

    def test_unknown_vendor_is_recognised_as_unknown(self):
        rc = RedfishClient(dry=False, log=lambda *a, **k: None)
        self.assertEqual(rc.vendor(self.addr), "unbekannt")

    def test_probe_of_existing_server_reports_instead_of_crashing(self):
        lines = []
        eng = ProbeEngine([], log=lambda line, *a, **k: lines.append(line))
        res = eng.probe(self.addr, "admin", "password")
        self.assertIn("ok", res)
        self.assertTrue(any("Port" in line for line in lines), lines)

    def test_capture_redacts_reports_and_can_be_packed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap, rep, err = C.run_capture(self.addr, "admin", "password", Path(tmp) / "x")
            self.assertEqual(err, "")
            self.assertTrue(any(p.startswith("/redfish/v1/Systems/") for p in cap["tree"]))
            self.assertGreater(len(cap["tree"]), 5)
            self.assertTrue(cap["meta"].get("redacted"))
            self.assertNotIn("00:5c:52:31:3a:9c", str(cap["tree"]).lower())   # MAC des Fake-Systems
            labels = [label for label, _, _ in C.capabilities(cap)]
            self.assertTrue(labels)
            self.assertEqual(C.submission_problems(cap), [])
            self.assertTrue(C.pack_submission(Path(tmp) / "x", tmp).exists())


if __name__ == "__main__":
    unittest.main()
