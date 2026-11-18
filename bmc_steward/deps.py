"""BMC-Steward: Optionale Pakete je Seite prüfen und einen passenden Installationshinweis zeigen.

Jede Seite, die Zusatzprogramme oder Python-Pakete braucht, bekommt unten einen schmalen Randbereich
(`DepsFooter`). Er erscheint nur, wenn auf diesem Rechner etwas fehlt, nennt wofür es gebraucht wird und zeigt
den Installationsbefehl für das erkannte System (Arch, Debian/Ubuntu, Fedora, openSUSE, Windows) zum Kopieren.
Es wird nichts selbst installiert: Der Befehl wird nur angezeigt.
"""
import importlib.util
import os
import shlex
import shutil
import sys
import sysconfig
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .i18n import T
from .ossl import find_openssl

WINDOWS = sys.platform.startswith("win")


def distro():
    """Familie des Linux-Systems: "arch", "debian", "fedora", "suse" oder "" (unbekannt/kein Linux)."""
    if not sys.platform.startswith("linux"):
        return ""
    ids = []
    for f in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            for line in Path(f).read_text(encoding="utf-8").splitlines():
                key, _, val = line.partition("=")
                if key in ("ID", "ID_LIKE"):
                    ids += val.strip().strip('"').lower().split()
            break
        except OSError:
            continue
    for fam, names in (("arch", ("arch", "cachyos", "manjaro", "endeavouros")),
                       ("debian", ("debian", "ubuntu", "linuxmint", "pop")),
                       ("fedora", ("fedora", "rhel", "centos", "rocky", "almalinux")),
                       ("suse", ("suse", "opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles"))):
        if any(i in names for i in ids):
            return fam
    return ""


# Paketnamen der Zusatzprogramme je System (None: gibt es dort nicht über die Paketverwaltung)
BIN_PKGS = {
    "dnsmasq": {"arch": "dnsmasq", "debian": "dnsmasq", "fedora": "dnsmasq", "suse": "dnsmasq"},
    "pkexec": {"arch": "polkit", "debian": "pkexec", "fedora": "polkit", "suse": "polkit"},
    "openssl": {"arch": "openssl", "debian": "openssl", "fedora": "openssl", "suse": "openssl"},
    "zbarimg": {"arch": "zbar", "debian": "zbar-tools", "fedora": "zbar", "suse": "zbar"},
    "tesseract": {"arch": "tesseract tesseract-data-eng", "debian": "tesseract-ocr", "fedora": "tesseract",
                  "suse": "tesseract-ocr tesseract-ocr-traineddata-english"},
    "qrencode": {"arch": "qrencode", "debian": "qrencode", "fedora": "qrencode", "suse": "qrencode"},
}
PKG_MANAGER = {"arch": "sudo pacman -S --needed", "debian": "sudo apt install", "fedora": "sudo dnf install",
               "suse": "sudo zypper install"}
# Python-Pakete, die bei einem PyQt6 aus der Paketverwaltung ebenfalls von dort kommen müssen (sonst passen
# die Qt-Bibliotheken nicht zusammen)
QT_PKGS = {"PyQt6-WebEngine": {"arch": "python-pyqt6-webengine", "debian": "python3-pyqt6.qtwebengine",
                               "fedora": "python3-pyqt6-webengine", "suse": "python3-PyQt6-WebEngine"}}


def _webengine():
    mod = sys.modules.get("bmc_steward.console")
    if mod is not None:                     # console.py hat den Import schon versucht (vor der QApplication)
        return bool(getattr(mod, "WEBENGINE", False))
    return importlib.util.find_spec("PyQt6.QtWebEngineWidgets") is not None


def _has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _is_root():
    return not WINDOWS and hasattr(os, "geteuid") and os.geteuid() == 0


class Dep:
    """Ein optionales Paket: `kind` "py" (Python-Modul `probe`) oder "bin" (Programm `probe` im PATH)."""

    def __init__(self, name, kind, probe=None, check=None, linux_only=False, need=lambda: True):
        self.name, self.kind, self.probe, self.linux_only, self.need = name, kind, probe or name, linux_only, need
        self._check = check

    def applies(self):
        return not (self.linux_only and WINDOWS) and self.need()

    def present(self):
        if self._check:
            return self._check()
        if self.kind == "py":
            return _has_module(self.probe)
        return bool(shutil.which(self.probe))


def groups():
    """Seite -> [(Zweck, [Dep, …])]. Die Zwecke sind übersetzte Kurztexte."""
    return {
        "dhcp": [(T("DHCP-Server unter Linux"), [
            Dep("dnsmasq", "bin", check=lambda: bool(shutil.which("dnsmasq")) or any(
                os.path.exists(p) for p in ("/usr/bin/dnsmasq", "/usr/sbin/dnsmasq")), linux_only=True),
            Dep("pkexec", "bin", linux_only=True, need=lambda: not _is_root())])],
        "new": [(T("Handy-Scanner"), [Dep("openssl", "bin", check=lambda: bool(find_openssl())), Dep("zbarimg", "bin"), Dep("tesseract", "bin"),
                                       Dep("qrencode", "bin")]),
                (T("Passwörter im Schlüsselbund"), [Dep("keyring", "py")])],
        "console": [(T("Eingebettete HTML5-Konsole"), [Dep("PyQt6-WebEngine", "py", check=_webengine)]),
                    (T("Textkonsole (SOL/SSH)"), [Dep("pyte", "py"), Dep("paramiko", "py"),
                                                  Dep("pyghmi", "py")])],
        "vbmc": [(T("UEFI-Konsole der virtuellen Server"), [Dep("paramiko", "py"), Dep("pyghmi", "py")]),
                 (T("Netze Konfigraum und Router"), [Dep("pkexec", "bin", linux_only=True,
                                                         need=lambda: not _is_root())])],
    }


def _externally_managed():
    """True, wenn pip ohne venv hier nicht installieren darf (PEP 668, z. B. Arch, Debian 12)."""
    if sys.prefix != sys.base_prefix:
        return False
    try:
        return (Path(sysconfig.get_paths()["stdlib"]) / "EXTERNALLY-MANAGED").exists()
    except (KeyError, OSError):
        return False


def _qt_from_distro():
    """True, wenn PyQt6 aus der Paketverwaltung stammt (dann muss WebEngine auch von dort kommen)."""
    if WINDOWS or sys.prefix != sys.base_prefix:
        return False
    spec = importlib.util.find_spec("PyQt6")
    origin = str(spec.origin if spec and spec.origin else "")
    return origin.startswith(("/usr/lib/", "/usr/lib64/"))


def install_commands(missing, fam=None):
    """Befehle (Liste von Zeilen), die die Deps in `missing` nachinstallieren."""
    fam = distro() if fam is None else fam
    cmds, sys_pkgs, manual, pip = [], [], [], []
    for d in missing:
        if d.kind == "bin":
            pkg = BIN_PKGS.get(d.probe, {}).get(fam)
            (sys_pkgs.append(pkg) if pkg else manual.append(d.name))
        elif d.name in QT_PKGS and fam in QT_PKGS[d.name] and _qt_from_distro():
            sys_pkgs.append(QT_PKGS[d.name][fam])
        else:
            pip.append(d.name)
    if sys_pkgs:
        cmds.append(PKG_MANAGER[fam] + " " + " ".join(dict.fromkeys(sys_pkgs)))
    if pip:
        py = "py" if WINDOWS else shlex.quote(sys.executable or "python3")
        extra = " --user --break-system-packages" if _externally_managed() else ""
        cmds.append("%s -m pip install%s %s" % (py, extra, " ".join(dict.fromkeys(pip))))
    if manual:
        cmds.append(T("Von Hand installieren und in den PATH legen: {0}").format(", ".join(manual)))
    return cmds


def missing_for(page):
    """[(Zweck, [fehlende Dep, …])] einer Seite; leer, wenn alles da ist."""
    out = []
    for purpose, deps in groups().get(page, []):
        miss = [d for d in deps if d.applies() and not d.present()]
        if miss:
            out.append((purpose, miss))
    return out


class DepsFooter(QFrame):
    """Randbereich unten auf einer Seite: was fehlt, wofür, und der Befehl zum Nachinstallieren."""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.setObjectName("depsFooter")
        self.setStyleSheet("QFrame#depsFooter { border-top: 1px solid palette(mid); }")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(6, 4, 6, 2)
        self.lay.setSpacing(2)
        self.refresh()

    def refresh(self):
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        missing = missing_for(self.page)
        self.setVisible(bool(missing))
        if not missing:
            return
        head = QLabel("<small><b>%s</b> %s</small>" % (
            T("Optionale Pakete fehlen:"), ";  ".join("%s – %s" % (p, ", ".join(d.name for d in ds))
                                                    for p, ds in missing)))
        head.setWordWrap(True)
        head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lay.addWidget(head)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        for cmd in install_commands([d for _, ds in missing for d in ds]):
            row = QHBoxLayout()
            edit = QLineEdit(cmd)
            edit.setReadOnly(True)
            edit.setFont(mono)
            edit.setCursorPosition(0)
            copy = QPushButton(T("Kopieren"))
            copy.setToolTip(T("Befehl in die Zwischenablage kopieren"))
            copy.clicked.connect(lambda _=False, c=cmd: QGuiApplication.clipboard().setText(c))
            row.addWidget(edit, 1)
            row.addWidget(copy)
            self.lay.addLayout(row)
        tail = QLabel("<small>%s</small>" % T("Nach der Installation BMC-Steward neu starten. Alles andere "
                                               "funktioniert auch ohne diese Pakete."))
        tail.setWordWrap(True)
        self.lay.addWidget(tail)


def attach(page_widget, page):
    """Hängt den Randbereich unten an das Layout einer Seite an und gibt ihn zurück."""
    footer = DepsFooter(page)
    page_widget.layout().addWidget(footer)
    return footer
