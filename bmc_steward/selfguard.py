"""BMC-Steward: Eigenschutz – verhindert, dass das Tool den Server neu startet oder mit Firmware bespielt,
auf dem es selbst läuft.

Zwei Quellen für „das bin ich“:
  1. Die eigene SMBIOS-Kennung (UUID, Seriennummer) des Rechners. Das greift, wenn BMC-Steward direkt auf
     einem Server läuft, dessen BMC in der Liste steht.
  2. Eine Schutzliste, die man selbst pflegt (Seriennummer/Service-Tag, System-UUID oder BMC-Adresse).
     Die braucht man in einer virtuellen Maschine: Die VM sieht nur ihre eigene, virtuelle Kennung, nicht
     die des Hypervisor-Hosts (ESXi, Proxmox, Hyper-V). In die Liste gehören alle Hosts, auf denen die VM
     laufen kann (z. B. der ganze Management-Cluster).

Geprüft wird vor jeder Aktion, die den Server selbst betrifft: UEFI-Einstellungen (mit/ohne Neustart),
Firmware-Updates und „Neustart ins UEFI-Setup“ aus der Konsole. Kann die Identität des Zielservers im
scharfen Lauf nicht gelesen werden, wird der Server übersprungen (lieber nichts tun als den eigenen Host
abschalten).
"""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .i18n import HERE

GUARD_FILE = HERE / "bmc-selfguard.json"
WINDOWS = sys.platform.startswith("win")

# Hersteller-/Modellkennungen virtueller Maschinen (SMBIOS sys_vendor / product_name)
VIRT_MARKERS = (
    ("vmware", "VMware"), ("virtualbox", "VirtualBox"), ("innotek", "VirtualBox"), ("qemu", "KVM/QEMU"),
    ("kvm", "KVM/QEMU"), ("proxmox", "Proxmox"), ("microsoft corporation virtual", "Hyper-V"),
    ("virtual machine", "Hyper-V"), ("xen", "Xen"), ("parallels", "Parallels"), ("bochs", "KVM/QEMU"),
    ("nutanix", "Nutanix AHV"), ("openstack", "OpenStack"), ("amazon ec2", "AWS"), ("google", "GCE"),
)

# Werte, die Hersteller als Platzhalter eintragen – passen auf viele Geräte und taugen nicht zum Vergleich
PLACEHOLDERS = {"", "0", "NONE", "NA", "N/A", "DEFAULTSTRING", "TOBEFILLEDBYOEM", "TOBEFILLEDBYO.E.M.",
                "SYSTEMSERIALNUMBER", "0123456789", "123456789", "NOTSPECIFIED", "NOTAVAILABLE", "UNKNOWN",
                "00000000000000000000000000000000", "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
                "03000200040005000006000700080009"}


def norm(value):
    """Vergleichsform: ohne Leerzeichen, Bindestriche, Doppelpunkte; Großbuchstaben."""
    return re.sub(r"[\s\-:{}]", "", str(value or "")).upper()


def usable(value):
    n = norm(value)
    return len(n) >= 4 and n not in PLACEHOLDERS and n.replace(".", "") not in PLACEHOLDERS


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _powershell(expr):
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return ""
    try:
        return subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", expr], capture_output=True,
                              text=True, timeout=15, creationflags=0x08000000).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _smbios():
    """(Hersteller, Modell, UUID, Seriennummern) dieses Rechners, soweit ohne Root lesbar."""
    if WINDOWS:
        out = _powershell("$c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_ComputerSystemProduct;"
                          "$b=Get-CimInstance Win32_BIOS; $c.Manufacturer; $c.Model; $p.UUID; $p.IdentifyingNumber;"
                          "$b.SerialNumber")
        lines = (out.splitlines() + [""] * 5)[:5]
        return lines[0], lines[1], lines[2], [lines[3], lines[4]]
    base = "/sys/class/dmi/id/"
    vendor, product = _read(base + "sys_vendor"), _read(base + "product_name")
    uuid = _read(base + "product_uuid")                    # nur für root lesbar
    serials = [_read(base + "product_serial"), _read(base + "chassis_serial"), _read(base + "board_serial")]
    if not uuid or not any(serials):
        # Ohne Root: systemd-hostnamed liest die Werte privilegiert und gibt sie (je nach Version) heraus
        exe = shutil.which("hostnamectl")
        if exe:
            try:
                out = subprocess.run([exe, "--json=short"], capture_output=True, text=True, timeout=5).stdout
                info = json.loads(out or "{}")
                serials.append(str(info.get("HardwareSerial") or ""))
                uuid = uuid or str(info.get("ProductUUID") or "")
                vendor = vendor or str(info.get("HardwareVendor") or "")
                product = product or str(info.get("HardwareModel") or "")
            except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
                pass
    return vendor, product, uuid, serials


def hypervisor():
    """Name des Hypervisors, wenn BMC-Steward in einer VM läuft, sonst ''."""
    vendor, product, _, _ = _smbios()
    text = f"{vendor} {product}".lower()
    for marker, name in VIRT_MARKERS:
        if marker in text:
            return name
    if not WINDOWS and re.search(r"^flags\s*:.*\bhypervisor\b", _read("/proc/cpuinfo"), re.M):
        return "Hypervisor"
    return ""


class SelfGuard:
    """Hält Schutzliste und eigene Kennung. Eine Instanz je Programmlauf genügt (siehe get())."""

    def __init__(self, path=GUARD_FILE, probe=True):
        self.path = Path(path)
        self.entries = []
        self.virt = ""
        self.own = []                     # eigene SMBIOS-Kennungen (nur auf echter Hardware)
        self.load()
        if probe:
            self.refresh()

    # ---------- Datei ----------
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [str(e).strip() for e in data.get("protected", []) if str(e).strip()]
        except (OSError, ValueError, AttributeError):
            self.entries = []

    def save(self, entries):
        self.entries = [e.strip() for e in entries if e.strip()]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"protected": self.entries}, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------- eigene Kennung ----------
    def refresh(self):
        self.virt = hypervisor()
        _, _, uuid, serials = _smbios()
        # In einer VM ist die eigene Kennung die virtuelle der VM – die passt nie auf einen BMC
        self.own = [] if self.virt else [v for v in [uuid, *serials] if usable(v)]

    @property
    def armed(self):
        """True, wenn überhaupt etwas zum Vergleichen da ist."""
        return bool(self.entries or self.own)

    @property
    def unprotected_vm(self):
        """In einer VM ohne Schutzliste: der eigene Host kann nicht erkannt werden."""
        return bool(self.virt) and not self.entries

    # ---------- Vergleich ----------
    def match_ip(self, ip):
        """Eintrag aus der Schutzliste, der die BMC-Adresse ip nennt, oder None."""
        raw = str(ip).strip()
        host = raw[1:].split("]")[0] if raw.startswith("[") else (raw.split(":")[0] if raw.count(":") == 1 else raw)
        try:
            want = ipaddress.ip_address(host)
        except ValueError:
            return None
        for e in self.entries:
            if e == raw:
                return e
            try:
                if ipaddress.ip_address(e) == want:
                    return e
            except ValueError:
                continue
        return None

    def match(self, system):
        """Prüft ein Redfish-ComputerSystem-Dokument. Gibt den passenden Wert zurück oder None."""
        if not isinstance(system, dict):
            return None
        def sub(d, key):
            v = d.get(key) if isinstance(d, dict) else None
            return v if isinstance(v, dict) else {}
        dell = sub(sub(system, "Oem"), "Dell").get("DellSystem")
        dell = dell if isinstance(dell, dict) else {}
        found = [system.get("SerialNumber"), system.get("SKU"), system.get("UUID"),
                 dell.get("NodeID"), dell.get("ChassisServiceTag")]
        theirs = {norm(v) for v in found if usable(v)}
        for e in self.entries + self.own:
            if usable(e) and norm(e) in theirs:
                return e
        return None

    @staticmethod
    def identity(system):
        """Kurzbeschreibung des Zielservers für das Protokoll."""
        if not isinstance(system, dict):
            return ""
        parts = [str(system.get(k)).strip() for k in ("Model", "SerialNumber", "SKU") if system.get(k)]
        return " · ".join(dict.fromkeys(parts))


_GUARD = None


def get():
    global _GUARD
    if _GUARD is None:
        _GUARD = SelfGuard()
    return _GUARD
