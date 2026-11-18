"""openssl-Suche (bmc_steward/ossl.py, identisch in tools/vbmc/vbsim/ossl.py)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
# direkt per Pfad laden: das Paket bmc_steward zieht PyQt6 nach sich, ossl.py braucht nur die Standardbibliothek
import importlib.util      # noqa: E402
_spec = importlib.util.spec_from_file_location("ossl_under_test", ROOT / "bmc_steward" / "ossl.py")
ossl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ossl)


class TestOssl(unittest.TestCase):
    def setUp(self):
        ossl._cache.clear()

    def test_beide_kopien_gleich(self):
        a = (ROOT / "bmc_steward" / "ossl.py").read_text(encoding="utf-8")
        b = (ROOT / "tools" / "vbmc" / "vbsim" / "ossl.py").read_text(encoding="utf-8")
        self.assertEqual(a, b)

    def test_umgebungsvariable_hat_vorrang(self):
        with tempfile.NamedTemporaryFile(suffix=".exe") as f, \
                mock.patch.dict(os.environ, {"OPENSSL_EXE": f.name}):
            self.assertEqual(ossl.find_openssl(refresh=True), f.name)

    def test_nicht_im_path_aber_an_ueblichem_ort(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "openssl.exe")
            open(exe, "w").close()
            with mock.patch.object(ossl.shutil, "which", return_value=None), \
                    mock.patch.object(ossl.sys, "platform", "win32"), \
                    mock.patch.object(ossl, "_registry_path", return_value=[]), \
                    mock.patch.object(ossl, "_windows_dirs", return_value=[d]), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENSSL_EXE", None)
                os.environ.pop("OPENSSL_BIN", None)
                self.assertEqual(ossl.find_openssl(refresh=True), exe)

    def test_nichts_gefunden(self):
        with mock.patch.object(ossl.shutil, "which", return_value=None), \
                mock.patch.object(ossl.sys, "platform", "win32"), \
                mock.patch.object(ossl, "_registry_path", return_value=[]), \
                mock.patch.object(ossl, "_windows_dirs", return_value=[]):
            os.environ.pop("OPENSSL_EXE", None)
            os.environ.pop("OPENSSL_BIN", None)
            self.assertIsNone(ossl.find_openssl(refresh=True))


if __name__ == "__main__":
    unittest.main()
