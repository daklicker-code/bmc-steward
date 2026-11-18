"""Installationshinweise für fehlende optionale Pakete (deps.py): richtige Befehle je System, Randbereich nur
bei fehlenden Paketen sichtbar."""
import unittest
from unittest import mock

from tests._support import qapp

qapp()
from bmc_steward import deps


class TestCommands(unittest.TestCase):
    def test_binaries_use_the_distro_package_names(self):
        miss = [deps.Dep("zbarimg", "bin"), deps.Dep("tesseract", "bin")]
        self.assertEqual(deps.install_commands(miss, fam="debian"), ["sudo apt install zbar-tools tesseract-ocr"])
        self.assertEqual(deps.install_commands(miss, fam="arch"),
                         ["sudo pacman -S --needed zbar tesseract tesseract-data-eng"])

    def test_python_packages_go_through_pip_of_this_interpreter(self):
        with mock.patch.object(deps, "_externally_managed", return_value=False):
            cmds = deps.install_commands([deps.Dep("paramiko", "py"), deps.Dep("pyghmi", "py")], fam="arch")
        self.assertEqual(len(cmds), 1)
        self.assertTrue(cmds[0].endswith("-m pip install paramiko pyghmi"), cmds[0])

    def test_pep668_systems_get_user_install(self):
        with mock.patch.object(deps, "_externally_managed", return_value=True):
            cmd = deps.install_commands([deps.Dep("pyte", "py")], fam="arch")[0]
        self.assertIn("--user --break-system-packages", cmd)

    def test_webengine_from_distro_when_pyqt_is_from_distro(self):
        with mock.patch.object(deps, "_qt_from_distro", return_value=True):
            self.assertEqual(deps.install_commands([deps.Dep("PyQt6-WebEngine", "py")], fam="arch"),
                             ["sudo pacman -S --needed python-pyqt6-webengine"])

    def test_unknown_system_names_the_programs(self):
        cmds = deps.install_commands([deps.Dep("qrencode", "bin")], fam="")
        self.assertEqual(len(cmds), 1)
        self.assertIn("qrencode", cmds[0])


class TestFooter(unittest.TestCase):
    def test_hidden_when_nothing_is_missing(self):
        with mock.patch.object(deps, "missing_for", return_value=[]):
            self.assertTrue(deps.DepsFooter("console").isHidden())

    def test_shows_command_when_something_is_missing(self):
        miss = [("Textkonsole (SOL/SSH)", [deps.Dep("paramiko", "py"), deps.Dep("pyghmi", "py")])]
        with mock.patch.object(deps, "missing_for", return_value=miss):
            f = deps.DepsFooter("console")
        self.assertFalse(f.isHidden())
        from PyQt6.QtWidgets import QLineEdit
        cmds = [e.text() for e in f.findChildren(QLineEdit)]
        self.assertTrue(any("paramiko pyghmi" in c for c in cmds), cmds)

    def test_every_page_key_is_known(self):
        self.assertEqual(set(deps.groups()), {"dhcp", "new", "console", "vbmc"})


if __name__ == "__main__":
    unittest.main()
