#!/usr/bin/env python3
"""vbmc - Virtuelle BMC-Flotte zum Testen und Vorführen von BMC-Steward.

Erzeugt und startet beliebig viele "virtuelle" Server der Hersteller Dell (iDRAC),
HPE (iLO), Lenovo (XCC) und Supermicro. Jeder virtuelle Server verhält sich nach
außen wie ein echter BMC:

  * Redfish über HTTPS: Herstellererkennung, Benutzer/Netzwerk/Hostname,
    UEFI/BIOS-Attribute, NTP/Syslog/LDAP/Zertifikat, Power/Reset; beim
    Umkonfigurieren wechselt er tatsächlich die Adresse (Kontroll- und
    Inventarphase von BMC-Steward läuft durch). iLO 3/4 zusätzlich über RIBCL/XML.
  * Echtes SSH (paramiko) und echtes IPMI-Serial-over-LAN (pyghmi) für die
    UEFI-/Text-Konsole von BMC-Steward - nur im Modus --console, weil BMC-Steward
    dafür die Standardports 22 (SSH) und 623 (IPMI) sowie Redfish auf 443 anspricht.

Grundbetrieb braucht nur die Python-Standardbibliothek (openssl fürs Zertifikat). Mit --netlab rz/router
bekommt jeder Server eine eigene Netzwerkkarte in einem Namespace (root, iproute2), siehe netlab.py.
--console braucht zusätzlich die Pakete paramiko und pyghmi sowie erhöhte Rechte
(Ports < 1024). Ohne diese Pakete/Rechte läuft der Redfish-Teil normal weiter.

Reines Test-/Demowerkzeug: Passwörter liegen im Klartext im Speicher - niemals mit
echten Zugangsdaten betreiben, nur im Labor.

Beispiele (auf dem CachyOS-PC, auf dem auch BMC-Steward getestet wird):
  python3 vbmc.py --preset                      # je ein Server pro Hersteller (Port 8443)
  python3 vbmc.py dell hpe lenovo smc
  python3 vbmc.py dell@127.9.0.11 hpe@127.9.0.12:8443
  python3 vbmc.py --vendor dell --count 5
  python3 vbmc.py --config fleet.example.json
  sudo python3 vbmc.py --preset --console       # zusätzlich SSH/IPMI/Redfish-443
  python3 vbmc.py --preset --interactive        # Steuerkonsole: add/list/rm/quit
"""
__version__ = "1.8.0"
__license__ = "MIT"

import argparse
import base64
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vbsim.state import ServerSim, RESET_TYPES      # noqa: E402
from vbsim import redfish_ext, ssdp, webui           # noqa: E402

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "vbmc"

# Ausgaben aus mehreren Threads nie ineinander schreiben (die GUI liest zeilenweise mit)
_PRINT_LOCK = threading.RLock()
_print = print


def print(*a, **k):                     # noqa: A001 - bewusst modulweit
    """Jede Zeile bekommt eine Uhrzeit vorangestellt, damit sich im gespeicherten Protokoll (BMC-Steward,
    Reiter Testlabor) nachvollziehen lässt, wann was passiert ist - z. B. ob ein Server schon lief, als
    die Konsole „Gerät nicht erreichbar“ meldete. Die @@STATE-Protokollzeilen (von der GUI geparst) und
    reine Leerzeilen bleiben unverändert."""
    k.setdefault("flush", True)
    first = a[0] if a else ""
    with _PRINT_LOCK:
        if isinstance(first, str) and (first.startswith("@@") or first.strip() == ""):
            _print(*a, **k)
        else:
            ts = time.strftime("%H:%M:%S")
            rest = a[1:]
            if isinstance(first, str) and first.startswith("\n"):
                _print(f"\n[{ts}] {first[1:]}", *rest, **k)
            else:
                _print(f"[{ts}] {first}", *rest, **k)

# Optionale Abhängigkeiten nur für die Konsole (--console):
try:
    import paramiko
except ImportError:
    paramiko = None
try:
    from pyghmi.ipmi import bmc as _ipmibmc
    from pyghmi.ipmi.private import session as _ipmisession
    from pyghmi.ipmi.private import serversession as _ipmiserversession
except ImportError:
    _ipmibmc = None

VENDORS = ("dell", "hpe", "smc", "lenovo", "hpe_old")
NAME = {"dell": "Dell (iDRAC)", "hpe": "HPE (iLO 5/6)", "hpe_old": "HPE (iLO 3/4, RIBCL)",
        "smc": "Supermicro", "lenovo": "Lenovo (XCC)"}
FACTORY = {"dell": ("root", "calvin"), "hpe": ("Administrator", "Admin123!"),
           "hpe_old": ("Administrator", "Admin123!"), "smc": ("ADMIN", "ADMIN"),
           "lenovo": ("USERID", "PASSW0RD")}
MGR = {"dell": "iDRAC.Embedded.1", "hpe": "1", "smc": "1", "lenovo": "1"}
SYS = {"dell": "System.Embedded.1", "hpe": "1", "smc": "1", "lenovo": "1"}
NIC = {"hpe": "1", "smc": "1", "lenovo": "NIC"}
webui.configure(SYS=SYS, MGR=MGR)
MODELS = {"dell": "PowerEdge R6625", "hpe": "ProLiant DL365 Gen11",
          "hpe_old": "ProLiant DL360p Gen8", "smc": "AS-2015HS-TNR",
          "lenovo": "ThinkSystem SR655 V3"}
FW = {"dell": "7.10.30.00", "hpe": "3.09", "hpe_old": "2.78", "smc": "01.05.02", "lenovo": "5.30"}

# Pfad der HTML5-Konsole je Hersteller - identisch zu CONSOLE_PATH in bmc_steward/console.py, damit
# der virtuelle Server dort landet, wo BMC-Steward nach der Anmeldung automatisch hinspringt.
CONSOLE_UI_PATH = {"dell": "/console", "hpe": "/irc.html", "hpe_old": "/irc.html",
                   "smc": "/cgi/url_redirect.cgi", "lenovo": "/"}
ACCENT = {"dell": "#2f7dd1", "hpe": "#2fae59", "hpe_old": "#2fae59", "smc": "#c98a2b", "lenovo": "#d1352f"}

# Firmware-Generationen je Hersteller: Kürzel -> (Anzeige, Firmware, Modell)
GENS = {
    "dell": {"idrac9": ("iDRAC9", "7.10.30.00", "PowerEdge R6625"),
             "idrac10": ("iDRAC10", "1.20.25.00", "PowerEdge R770"),
             "idrac8": ("iDRAC8", "2.83.83.83", "PowerEdge R630"),
             "idrac8_alt": ("iDRAC8 (Firmware < 2.50.50.50)", "2.40.40.40", "PowerEdge R630")},
    "hpe": {"ilo6": ("iLO 6", "1.62", "ProLiant DL365 Gen11"),
            "ilo5": ("iLO 5", "3.09", "ProLiant DL360 Gen10 Plus"),
            "ilo4": ("iLO 4", "2.78", "ProLiant DL360p Gen8"),
            "ilo3": ("iLO 3", "1.94", "ProLiant DL380 G7")},
    "lenovo": {"xcc2": ("XCC2", "5.30", "ThinkSystem SR655 V3"),
               "xcc3": ("XCC3", "1.10", "ThinkSystem SR650 V4"),
               "xcc1": ("XCC", "9.90", "ThinkSystem SR650")},
    "smc": {"x13": ("X13/H13", "01.05.02", "AS-2015HS-TNR"),
            "x12": ("X12", "01.03.09", "SYS-120U-TNR"),
            "x11": ("X11", "01.74.11", "SYS-1029U-TR4")},
}
DEFAULT_GEN = {"dell": "idrac9", "hpe": "ilo6", "hpe_old": "ilo4", "lenovo": "xcc2", "smc": "x13"}
OLD_ILO = ("ilo3", "ilo4")              # laufen über RIBCL (Hersteller-Kürzel hpe_old)
# SSH-Befehl, mit dem BMC-Steward die serielle Konsole startet (smc nutzt IPMI-SOL)
SSH_SOL_CMD = {"dell": "console com2", "hpe": "vsp", "hpe_old": "vsp", "lenovo": "console 1"}

SSH_PORT, IPMI_PORT, REDFISH_STD = 22, 623, 443


# ------------------------------------------------------------------ Zertifikat
def ensure_cert():
    CACHE.mkdir(parents=True, exist_ok=True)
    cert, key = CACHE / "cert.pem", CACHE / "key.pem"
    if not (cert.exists() and key.exists()):
        try:
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
                 "-out", str(cert), "-days", "3650", "-nodes", "-subj", "/CN=vbmc"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            sys.exit(f"Konnte kein Zertifikat erzeugen (openssl nötig): {e}")
    return str(cert), str(key)


_SSH_KEY = None


def ssh_hostkey():
    global _SSH_KEY
    if _SSH_KEY is None:
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / "ssh_host_rsa"
        if path.exists():
            _SSH_KEY = paramiko.RSAKey(filename=str(path))
        else:
            _SSH_KEY = paramiko.RSAKey.generate(2048)
            _SSH_KEY.write_private_key_file(str(path))
    return _SSH_KEY


# ------------------------------------------------------------------ Zustand
def mask_of(prefix):
    import ipaddress
    return str(ipaddress.IPv4Network(f"0.0.0.0/{int(prefix)}").netmask)


def prefix_of(mask, default=24):
    import ipaddress
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen if mask else default
    except ValueError:
        return default


class BMC:
    def __init__(self, vendor, host, port, serial=None, model=None, user=None, password=None,
                 gen=None, mac=None, net=None, ip=None, mask=None, gw=None, busy_slots=None, idx=0,
                 license=None):
        # HPE iLO 3/4 laufen über RIBCL: intern Hersteller hpe_old
        if vendor == "hpe" and gen in OLD_ILO:
            vendor = "hpe_old"
        if vendor == "hpe_old" and gen not in OLD_ILO:
            gen = "ilo4"
        self.vendor = vendor
        self.family = "hpe" if vendor == "hpe_old" else vendor
        self.gen = gen if gen in GENS[self.family] else DEFAULT_GEN[vendor]
        self.gen_label, self.fw, gen_model = GENS[self.family][self.gen]
        self.idx = idx
        self.host, self.port = host, port
        self.console_host = host        # feste Adresse für SSH/IPMI (wandert bei Migration nicht)
        self.model = model or gen_model
        self.serial = serial or self._gen_serial()
        self.mac = mac
        fu, fp = FACTORY[vendor]
        self.factory_user = user or fu
        self.factory_pass = password or fp
        # Konten mit festem Slot (Dell: 2 = root, neue Benutzer ab 3)
        self.accounts = {self.factory_user: {"password": self.factory_pass,
                                             "role": "Administrator", "enabled": True, "slot": 2}}
        for slot, who in (busy_slots or {}).items():
            if who and who not in self.accounts:
                self.accounts[who] = {"password": "Fremd123!", "role": "Operator", "enabled": True,
                                      "slot": int(slot)}
        self.hostname = ""
        # Werkszustand Netz (nur im Netzlabor wirksam): DHCP oder feste Werks-IP
        self.factory_net = "static" if (net == "static" and ip) else "dhcp"
        self.factory_ip, self.factory_mask, self.factory_gw = ip, mask or "255.255.255.0", gw or ""
        self.netns = None               # Netzwerk-Namespace im Netzlabor
        self.dhcpc = None               # laufender DHCP-Client
        self.link_up = True
        self.status = ""
        self.dhcp = True
        self.ipv4 = {"Address": host, "SubnetMask": "255.255.255.0", "Gateway": ""}
        self.pending_ip = None
        self.attributes = dict(DELL_ATTRS) if vendor == "dell" else {}
        self.bios = dict(BIOS_ATTRS)
        self.extra = {}
        # Firmware-Inventar (Name -> {version, updateable}) und laufende Update-Tasks
        self.firmware = {
            "BMC": {"version": self.fw, "updateable": True, "name": self.gen_label},
            "BIOS": {"version": "1.8.2", "updateable": True, "name": "BIOS/UEFI"},
            "NIC.1": {"version": "22.5.9", "updateable": True, "name": "NIC Slot 1"},
            "PSU.1": {"version": "00.0A.0C", "updateable": False, "name": "Power Supply 1"},
        }
        self.tasks = {}                 # id -> {state, percent, target, newver}
        self._task_seq = 0
        self.lock = threading.Lock()
        self.license_cfg = license      # "none" = ohne Lizenz starten, sonst Standardstufe des Herstellers
        self.sim = ServerSim(self)      # Power/POST/Ereignisse/Sensoren (siehe vbsim/state.py)
        self._redfish = []              # [(Server, thread)]
        self._ssh = None                # (socket, thread)
        self._ipmi = None               # FakeBMC-Instanz
        self._ssdp = None               # SSDP-Antwortdienst (vbsim/ssdp.py)

    def _gen_serial(self):
        import random
        pre = {"dell": "CN", "hpe": "CZ", "hpe_old": "CZ", "smc": "S", "lenovo": "J"}[self.vendor]
        return pre + "".join(random.choice("0123456789ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(10))

    @property
    def addr(self):
        """Adresse, unter der BMC-Steward den Server erreicht. Im Netzlabor die echte IP (Port 443,
        daher ohne :Port) - leer, solange noch keine Adresse vom DHCP-Server da ist."""
        if self.netns:
            return self.ipv4.get("Address") or ""
        return f"{self.host}:{self.port}"

    def check_auth(self, user, pw):
        a = self.accounts.get(user)
        return bool(a) and a.get("enabled", True) and a.get("password") == pw

    def dhcp_hostname(self):
        """Name, mit dem sich der BMC ab Werk beim DHCP-Server meldet (wie die echten Geräte)."""
        return {"dell": f"idrac-{self.serial[-7:]}", "hpe": f"ILO{self.serial}", "hpe_old": f"ILO{self.serial}",
                "lenovo": f"XCC-{self.serial}", "smc": ""}[self.vendor]

    def slot_of(self, token):
        """Kontoname zu einer Konto-Id (Slot-Nummer oder Name) oder None."""
        if token in self.accounts:
            return token
        try:
            slot = int(token)
        except ValueError:
            return None
        return next((n for n, a in self.accounts.items() if a.get("slot") == slot), None)

    def free_slot(self):
        used = {a.get("slot") for a in self.accounts.values()}
        return next(s for s in range(3, 64) if s not in used)

    def add_account(self, user, password, role="Administrator", enabled=True, slot=None):
        self.accounts[user] = {"password": password, "role": role, "enabled": enabled,
                               "slot": slot if slot is not None else self.free_slot()}

    def state(self):
        """Zustand für die Übersicht im Testlabor (JSON)."""
        a = self.accounts.get(self.factory_user)
        return {"id": self.idx, "vendor": self.vendor, "name": NAME[self.vendor], "gen": self.gen,
                "gen_label": self.gen_label, "model": self.model, "serial": self.serial,
                "mac": self.mac or "", "addr": self.addr, "ip": self.ipv4.get("Address") or "",
                "mask": self.ipv4.get("SubnetMask") or "", "gw": self.ipv4.get("Gateway") or "",
                "dhcp": self.dhcp, "hostname": self.hostname, "link": self.link_up,
                "netns": self.netns or "", "status": self.status,
                "factory_user": self.factory_user, "factory_pass": self.factory_pass,
                "factory_valid": bool(a and a.get("password") == self.factory_pass),
                "users": sorted(self.accounts)}


DELL_ATTRS = {
    "NIC.1.DNSRacName": "idrac-factory",
    "IPv4Static.1.Address": "", "IPv4Static.1.Netmask": "", "IPv4Static.1.Gateway": "",
    "IPv4.1.DHCPEnable": "Enabled",
    "NTPConfigGroup.1.NTP1": "", "NTPConfigGroup.1.NTP2": "", "NTPConfigGroup.1.NTP3": "",
    "Time.1.Timezone": "",
    "SysLog.1.SysLogEnable": "Disabled", "SysLog.1.Server1": "", "SysLog.1.Port": "514",
}
BIOS_ATTRS = {
    "BootMode": "Uefi", "SriovGlobalEnable": "Disabled", "LogicalProc": "Enabled",
    "ProcVirtualization": "Enabled", "SysProfile": "PerfOptimized", "MemTest": "Disabled",
    "SecureBoot": "Disabled", "PxeDev1EnDis": "Disabled", "IntegratedRaid": "Enabled",
}


# ------------------------------------------------------------------ Konsole (gemeinsamer Inhalt)
def console_banner(bmc):
    v = bmc.vendor
    title = {"dell": "Dell iDRAC - Serial Console (COM2)", "hpe": "HPE iLO Virtual Serial Port",
             "hpe_old": "HPE iLO 4 Virtual Serial Port", "smc": "Supermicro SOL Console",
             "lenovo": "Lenovo XCC Serial Console"}[v]
    return (f"\r\n{'='*58}\r\n {title}\r\n {bmc.model}   S/N {bmc.serial}\r\n{'='*58}\r\n"
            "\r\n  *** UEFI/BIOS Boot Menu (virtuell) ***\r\n"
            "   [1] Continue   [2] Boot Manager   [3] Device Manager\r\n"
            "   [4] BIOS Setup [5] Reboot\r\n"
            "\r\n  Press <ENTER> to refresh, a key to select, Ctrl-] to quit.\r\n"
            f"\r\n{bmc.hostname or 'localhost'}:/> ").encode()


def console_reply(bmc, data):
    """Antwort auf Tastendrücke - kleine, glaubwürdige Reaktion."""
    text = data.decode("latin-1", "replace")
    out = []
    for ch in text:
        if ch in ("\r", "\n"):
            out.append("\r\n" + console_banner(bmc).decode("latin-1"))
        elif ch == "1":
            out.append("1\r\n  Continuing boot...\r\n  EFI Shell> \r\n")
        elif ch == "4":
            out.append("4\r\n  Entering BIOS Setup (virtuell): BootMode=%s, SecureBoot=%s\r\n"
                       % (bmc.bios.get("BootMode"), bmc.bios.get("SecureBoot")))
        elif ch == "5":
            out.append("5\r\n  Rebooting virtual host...\r\n")
        else:
            out.append(ch)         # Echo
    return "".join(out).encode("latin-1", "replace")


def login_page(bmc, error=""):
    """Anmeldeseite der Web-Konsole - mit Benutzer-/Passwortfeld, damit BMC-Steward sie wie bei einem
    echten BMC per JavaScript ausfüllen und absenden kann."""
    accent = ACCENT.get(bmc.vendor, "#3b82f6")
    err = f"<p style='color:#f87171;margin:0 0 .8rem;font-size:.85rem'>{error}</p>" if error else ""
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{NAME[bmc.vendor]} – Anmeldung</title>"
        "<body style='margin:0;font-family:system-ui;background:#0c0f14;color:#e5e7eb;"
        "display:flex;align-items:center;justify-content:center;height:100vh'>"
        f"<form method=get action='/' style='background:#171b22;padding:2rem 2.5rem;border-radius:10px;"
        f"border-top:4px solid {accent};min-width:280px;box-shadow:0 10px 30px rgba(0,0,0,.4)'>"
        f"<h2 style='margin:0 0 .2rem;font-size:1.1rem'>{NAME[bmc.vendor]}</h2>"
        f"<p style='margin:0 0 1.1rem;color:#9ca3af;font-size:.8rem'>{bmc.model} · {bmc.addr}</p>"
        f"{err}"
        "<label style='display:block;font-size:.8rem;color:#9ca3af;margin-bottom:.2rem'>Benutzername</label>"
        "<input name=user type=text autofocus style='width:100%;box-sizing:border-box;padding:.5rem;"
        "margin-bottom:.8rem;border-radius:5px;border:1px solid #333;background:#0c0f14;color:#e5e7eb'>"
        "<label style='display:block;font-size:.8rem;color:#9ca3af;margin-bottom:.2rem'>Passwort</label>"
        "<input name=pass type=password style='width:100%;box-sizing:border-box;padding:.5rem;"
        "margin-bottom:1.1rem;border-radius:5px;border:1px solid #333;background:#0c0f14;color:#e5e7eb'>"
        f"<button type=submit style='width:100%;padding:.55rem;border:0;border-radius:5px;"
        f"background:{accent};color:#fff;font-size:.9rem;cursor:pointer'>Anmelden</button>"
        "</form></body>"
    )


def console_preview_page(bmc):
    """Kleine, gerahmte Konsolenvorschau nach der Anmeldung - wie bei einem echten BMC. Klick öffnet
    die UEFI-Oberfläche in einem eigenen Fenster (BMC-Steward fängt das als weiteren Reiter ab)."""
    accent = ACCENT.get(bmc.vendor, "#3b82f6")
    open_js = "window.open('/vbmc-kvm','_blank','width=960,height=680')"
    return (
        "<!doctype html><meta charset=utf-8>"
        f"<title>{NAME[bmc.vendor]} – Remote-Konsole</title>"
        "<body style='margin:0;font-family:system-ui;background:#0c0f14;color:#e5e7eb;"
        "display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;gap:1rem'>"
        f"<div onclick=\"{open_js}\" title='Konsole öffnen' "
        "style='width:420px;height:236px;border:3px solid #2a2f3a;border-radius:6px;cursor:pointer;"
        "background:linear-gradient(160deg,#0a0e13,#161b22);display:flex;align-items:center;"
        "justify-content:center;position:relative;overflow:hidden' "
        f"onmouseover=\"this.style.borderColor='{accent}'\" onmouseout=\"this.style.borderColor='#2a2f3a'\">"
        "<div style='text-align:center;color:#6b7280'>"
        f"<div style='font-size:.8rem;letter-spacing:.03em'>{bmc.hostname or bmc.model}</div>"
        "<div style='font-size:.7rem;margin-top:.4rem'>Vorschau – zum Öffnen klicken</div></div>"
        f"<div style='position:absolute;bottom:6px;right:8px;font-size:.65rem;color:#374151'>"
        f"{bmc.gen_label} {bmc.fw}</div></div>"
        f"<div style='font-size:.85rem;color:#9ca3af'>{NAME[bmc.vendor]} · {bmc.model} · {bmc.addr}</div>"
        f"<button onclick=\"{open_js}\" style='padding:.5rem 1.2rem;border:0;border-radius:5px;"
        f"background:{accent};color:#fff;cursor:pointer'>Konsole öffnen</button>"
        "</body>"
    )


def console_kvm_page(bmc):
    """Simulierte UEFI-Setup-Oberfläche (Klick auf die Vorschau) - zeigt die über BMC-Steward (Reiter 3)
    tatsächlich gesetzten BIOS-Attribute, rein zur Ansicht."""
    b, accent = bmc, ACCENT.get(bmc.vendor, "#3b82f6")

    def row(label, value):
        return (f"<div style='display:flex;justify-content:space-between;padding:.35rem 0;"
                f"border-bottom:1px solid #1f2430'><span style='color:#9ca3af'>{label}</span>"
                f"<span>{value}</span></div>")

    main = (row("Hersteller", NAME[b.vendor]) + row("Modell", b.model) + row("Seriennummer", b.serial)
            + row("Firmware", f"{b.gen_label} {b.fw}") + row("Hostname", b.hostname or "(nicht gesetzt)")
            + row("Adresse", f"{b.addr}{' (DHCP)' if b.dhcp else ''}"))
    adv = (row("SR-IOV Global Enable", b.bios.get("SriovGlobalEnable", "-"))
           + row("Logical Processor", b.bios.get("LogicalProc", "-"))
           + row("Virtualization Technology", b.bios.get("ProcVirtualization", "-"))
           + row("Memory Test", b.bios.get("MemTest", "-"))
           + row("PXE Device 1", b.bios.get("PxeDev1EnDis", "-"))
           + row("Integrated RAID", b.bios.get("IntegratedRaid", "-")))
    boot = (row("Boot Mode", b.bios.get("BootMode", "-")) + row("Secure Boot", b.bios.get("SecureBoot", "-"))
            + row("System Profile", b.bios.get("SysProfile", "-")))
    panels = {"main": ("Main", main), "adv": ("Advanced", adv), "boot": ("Boot", boot)}
    tabs = "".join(f"<div id=t_{k} class='tab{' on' if k == 'main' else ''}' "
                   f"onclick=\"tab('{k}')\">{label}</div>" for k, (label, _) in panels.items())
    bodies = "".join(f"<div id={k} class=panel style='display:{'block' if k == 'main' else 'none'}'>"
                      f"{content}</div>" for k, (_, content) in panels.items())
    return (
        "<!doctype html><meta charset=utf-8>"
        f"<title>{NAME[b.vendor]} – UEFI-Setup</title>"
        "<style>"
        "body{margin:0;font-family:system-ui,monospace;background:#05070a;color:#d8dde6;height:100vh;"
        "display:flex;flex-direction:column}"
        f".head{{background:{accent};color:#fff;padding:.6rem 1rem;font-size:.95rem}}"
        ".tabs{display:flex;background:#0c0f14;border-bottom:1px solid #1f2430}"
        f".tab{{padding:.6rem 1.1rem;cursor:pointer;font-size:.85rem;color:#8b93a3}}"
        f".tab.on{{color:#fff;border-bottom:2px solid {accent}}}"
        ".panel{padding:1rem 1.2rem;max-width:520px;font-size:.85rem}"
        ".foot{margin-top:auto;padding:.5rem 1rem;font-size:.7rem;color:#555;text-align:center}"
        "</style>"
        f"<div class=head>{NAME[b.vendor]} – UEFI-Setup-Utility (virtuell)</div>"
        f"<div class=tabs>{tabs}</div>{bodies}"
        "<div class=foot>Reines Test-/Demo-Gerät für BMC-Steward – nur Ansicht, keine echte Tastatureingabe.</div>"
        "<script>function tab(id){document.querySelectorAll('.panel').forEach(p=>p.style.display='none');"
        "document.getElementById(id).style.display='block';"
        "document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));"
        "document.getElementById('t_'+id).classList.add('on');}</script>"
    )


# ------------------------------------------------------------------ Netzlabor-Helfer
netlab = None                           # wird nur im Netzlabor geladen (Linux, root)


def _ns(name):
    """Kontext: Sockets im Namespace `name` anlegen (ohne Netzlabor: nichts tun)."""
    import contextlib
    if not name or netlab is None:
        return contextlib.nullcontext()
    return netlab.in_netns(name)


# ------------------------------------------------------------------ SSH-Konsole (paramiko)
class _SSHServer(paramiko.ServerInterface if paramiko else object):
    def __init__(self, bmc):
        self.bmc = bmc
        self.event = threading.Event()
        self.command = None

    def check_auth_password(self, user, pw):
        return paramiko.AUTH_SUCCESSFUL if self.bmc.check_auth(user, pw) else paramiko.AUTH_FAILED

    def get_allowed_auths(self, user):
        return "password"

    def check_channel_request(self, kind, cid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else \
            paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, c, term, w, h, pw, ph, modes):
        return True

    def check_channel_shell_request(self, c):
        self.event.set()
        return True

    def check_channel_exec_request(self, c, command):
        self.command = command
        self.event.set()
        return True


def _ssh_serve_conn(bmc, client):
    try:
        tr = paramiko.Transport(client)
        tr.add_server_key(ssh_hostkey())
        srv = _SSHServer(bmc)
        tr.start_server(server=srv)
        chan = tr.accept(15)
        if chan is None:
            tr.close()
            return
        srv.event.wait(10)
        chan.sendall(console_banner(bmc))
        chan.settimeout(0.3)
        end = time.monotonic() + 1800
        while time.monotonic() < end:
            try:
                d = chan.recv(4096)
                if not d:
                    break
                chan.sendall(console_reply(bmc, d))
            except socket.timeout:
                continue
            except Exception:
                break
        try:
            chan.send_exit_status(0)
            chan.close()
        except Exception:
            pass
        tr.close()
    except Exception:
        try:
            client.close()
        except Exception:
            pass


def start_ssh(bmc):
    with _ns(bmc.netns):                # im Netzlabor: Socket gehört zum Namespace des Servers
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bmc.console_host, SSH_PORT))
        sock.listen(5)

    def loop():
        while True:
            try:
                c, _ = sock.accept()
            except OSError:
                break
            threading.Thread(target=_ssh_serve_conn, args=(bmc, c), daemon=True).start()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    bmc._ssh = (sock, t)


# ------------------------------------------------------------------ IPMI-SOL (pyghmi)
# pyghmi verwendet globalen Session-Zustand und skaliert mit mehreren Servern in EINEM
# Prozess nicht zuverlässig. Darum läuft je virtuellem BMC ein eigener kleiner Prozess
# (wie ein echtes, einzelnes BMC) - robust und voneinander isoliert.
import multiprocessing


class _LiveAuth:
    """Benutzer/Passwörter für den IPMI-Kindprozess, laufend aus einer Datei des Hauptprozesses gelesen -
    wie bei einem echten BMC gilt ein neu angelegter Benutzer sofort auch für IPMI."""

    def __init__(self, path, initial):
        self.path, self.data, self.mtime = path, dict(initial), 0

    def get(self, user, default=None):
        try:
            m = os.stat(self.path).st_mtime
            if m != self.mtime:
                self.data = json.loads(Path(self.path).read_text())
                self.mtime = m
        except (OSError, ValueError):
            pass
        return self.data.get(user, default)


def _auth_snapshot(bmc):
    return {u: a["password"] for u, a in bmc.accounts.items() if a.get("enabled", True)}


# IPMI-Chassis-Befehle -> Redfish-ResetType (die Aktion läuft im Hauptprozess, wo der Server-Zustand liegt)
IPMI_POWER = {"on": "On", "off": "ForceOff", "shutdown": "GracefulShutdown", "cycle": "PowerCycle",
              "reset": "ForceRestart", "diag": "Nmi"}


class _PowerLink:
    """Kindprozess-Seite: fragt den Hauptprozess nach dem Power-Zustand und löst Power-Aktionen aus."""

    def __init__(self, conn):
        self.conn = conn

    def _ask(self, msg, timeout=2.0):
        if self.conn is None:
            return None
        try:
            self.conn.send(msg)
            return self.conn.recv() if self.conn.poll(timeout) else None
        except (OSError, EOFError, ValueError):
            return None

    def state(self):
        """„on“ oder „off“ (bei Verbindungsproblem „on“, wie früher)."""
        return "off" if self._ask(("state",)) == "off" else "on"

    def act(self, cmd):
        return self._ask(("act", cmd))


def serve_power_link(bmc, conn):
    """Hauptprozess-Seite: beantwortet die Anfragen des IPMI-Kindprozesses aus bmc.sim."""
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        try:
            if msg[0] == "state":
                conn.send("off" if bmc.sim.power_state in ("Off", "PoweringOn") else "on")
            elif msg[0] == "act":
                kind = IPMI_POWER.get(msg[1])
                conn.send(bmc.sim.power_action(kind, "IPMI", "")[0] if kind else False)
        except (OSError, ValueError):
            return


def _ipmi_process(vendor, host, port, serial, model, hostname, accounts, netns=None, authfile=None, powerconn=None):
    """Läuft im Kindprozess: ein einzelner IPMI-SOL-BMC."""
    if netns:                           # Netzlabor: der ganze Kindprozess lebt im Namespace des Servers
        import netlab
        netlab.enter_netns(netns)
        host = "0.0.0.0"                # alle IPv4-Adressen der Server-Karte (wandert mit beim Umzug)
    try:
        from pyghmi.ipmi import bmc as B
        from pyghmi.ipmi.private import session as S
        from pyghmi.ipmi.private import serversession as SS
    except ImportError:
        return
    # Downgrade-Patch: Client probiert SHA256, Server kann nur SHA1 -> Fehlerstatus
    # zwingt den Client selbst auf SHA1.
    _orig = SS.ServerSession.create_open_session_response

    def _patched(self, request):
        try:
            if len(request) > 12 and request[12] != 1:
                self.clientsessionid = request[4:8]
                return bytearray([request[0], 0x01, 0, 0]) + bytearray(self.clientsessionid) + bytearray(28)
        except Exception:
            pass
        return _orig(self, request)
    SS.ServerSession.create_open_session_response = _patched

    link = _PowerLink(powerconn)
    shim = BMC(vendor, "127.0.0.1", port, serial=serial, model=model)
    shim.hostname = hostname
    shim.accounts = accounts

    import queue as _queue

    class Fake(B.Bmc):
        def __init__(self):
            auth = {u: a["password"] for u, a in accounts.items()}
            super().__init__(_LiveAuth(authfile, auth) if authfile else auth, port=port, address=host)
            self.iohandler = self._key
            self._t = None
            self._q = _queue.Queue()
            threading.Thread(target=self._sender, daemon=True).start()

        def _sender(self):
            # Ausgaben gebündelt und AUSSERHALB des Empfangs-Callbacks senden,
            # damit Tastendruck-Antworten zuverlässig ankommen.
            while True:
                data = self._q.get()
                self._send(data)

        def get_system_guid(self):
            return self.uuid.bytes

        def is_active(self):
            return True

        def get_power_state(self):
            return link.state()

        def power_on(self):
            link.act("on")

        def power_off(self):
            link.act("off")

        def power_shutdown(self):
            link.act("shutdown")

        def power_cycle(self):
            link.act("cycle")

        def power_reset(self):
            link.act("reset")

        def cold_reset(self):
            pass

        def _send(self, data):
            try:
                for i in range(0, len(data), 200):     # SOL-Längenfeld ist 1 Byte
                    self.sol.send_data(bytes(data[i:i + 200]))
                    time.sleep(0.02)
            except Exception:
                pass

        def _key(self, data):
            self._q.put(console_reply(shim, bytes(data)))

        def activate_payload(self, request, session):
            # vorherige (evtl. verwaiste) SOL-Sitzung verwerfen, damit ein erneutes
            # Verbinden immer wieder sauber klappt (Client trennt oft ohne Deactivate).
            if self.activated:
                try:
                    self.sol.close()
                except Exception:
                    pass
                self.activated = False
                self.sol = None
            self._t = None
            super().activate_payload(request, session)
            if self.activated:
                self._t = threading.Thread(target=lambda: (time.sleep(0.3),
                                           self._q.put(console_banner(shim))), daemon=True)
                self._t.start()
    Fake()
    while True:
        S.Session.wait_for_rsp(30)


# "spawn": frischer Interpreter je Kind - kein Fork aus dem Thread-reichen Elternprozess,
# damit die pyghmi-Kinder so zuverlässig laufen wie ein einzeln gestarteter BMC.
_MP = multiprocessing.get_context("spawn")


_AUTH_DIR = None


def _write_auth(bmc):
    """Aktuelle Benutzer für den IPMI-Kindprozess ablegen (nur root/Besitzer lesbar)."""
    global _AUTH_DIR
    import tempfile
    if _AUTH_DIR is None:
        _AUTH_DIR = tempfile.mkdtemp(prefix="vbmc-ipmi-")
    snap = _auth_snapshot(bmc)
    if getattr(bmc, "_auth_last", None) == snap:
        return
    path = os.path.join(_AUTH_DIR, f"{bmc.idx}.json")
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(snap, f)
    os.replace(tmp, path)
    bmc._auth_last, bmc._authfile = snap, path


def start_ipmi(bmc):
    _write_auth(bmc)
    parent_conn, child_conn = _MP.Pipe()
    p = _MP.Process(
        target=_ipmi_process,
        args=(bmc.vendor, bmc.console_host, IPMI_PORT, bmc.serial, bmc.model,
              bmc.hostname, dict(bmc.accounts), bmc.netns, bmc._authfile, child_conn),
        daemon=True)
    p.start()
    child_conn.close()                  # nur der Kindprozess hält sein Ende; stirbt er, endet unser recv()
    threading.Thread(target=serve_power_link, args=(bmc, parent_conn), daemon=True).start()
    bmc._ipmi = p


# ------------------------------------------------------------------ Redfish-HTTP
class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, bmc, fleet):
        super().__init__(addr, Handler)
        self.bmc, self.fleet = bmc, fleet

    # Verbindungsabbrüche sind Alltag: BMC-Steward prüft TLS-Stufen und Erreichbarkeit mit kurzen
    # Verbindungen, die es sofort wieder schließt, und beim Umzug reißt die alte Verbindung ab.
    # Echte BMCs schweigen dazu - hier auch. Andere Fehler: eine Zeile statt Traceback.
    QUIET = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, ssl.SSLError)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, self.QUIET):
            return
        import traceback
        tb = traceback.extract_tb(exc.__traceback__)
        where = f" ({Path(tb[-1].filename).name}:{tb[-1].lineno})" if tb else ""
        print(f"  [redfish] Anfrage von {client_address[0]} fehlgeschlagen: {exc!r}{where}", file=sys.stderr)


def jbody(obj):
    return json.dumps(obj).encode()


def rf_error(msg):
    return {"error": {"code": "Base.1.0.GeneralError", "message": msg,
                      "@Message.ExtendedInfo": [{"Message": msg}]}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def bmc(self):
        return self.server.bmc

    def send(self, code, obj=None):
        body = b"" if obj is None else jbody(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def send_text(self, code, text, ctype):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def read_raw(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    SESSION_COOKIES = ("sessionKey", "SID", "QSESSIONID", "SessionToken", "vbsess")

    def session_token(self):
        t = self.headers.get("X-Auth-Token", "")
        if t:
            return t
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, val = part.strip().partition("=")
            if k in self.SESSION_COOKIES and val:
                return val
        return ""

    def session_user(self):
        """Angemeldeter Benutzer der Weboberfläche (Session) oder None."""
        s = self.bmc.sim.session(self.session_token())
        return s["user"] if s else None

    @property
    def client_ip(self):
        return self.client_address[0]

    def authed(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
            except Exception:
                return False
            return self.bmc.check_auth(user, pw)
        return self.session_user() is not None

    def ui_authed(self):
        """Eigene, von Basic-Auth unabhängige Anmeldung der HTML-Oberfläche (wie beim echten BMC)."""
        cookie = self.headers.get("Cookie", "")
        return f"vbuisess={self.bmc.idx}" in cookie

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def actor(self):
        """Wer die Aktion ausführt: Session-Benutzer oder Basic-Auth-Konto."""
        u = self.session_user()
        if u:
            return u
        h = self.headers.get("Authorization", "")
        try:
            return base64.b64decode(h[6:]).decode().partition(":")[0] if h.startswith("Basic ") else "unknown"
        except Exception:
            return "unknown"

    def session_create(self, data):
        b = self.bmc
        user, pw = str(data.get("UserName", "")), str(data.get("Password", ""))
        if not b.check_auth(user, pw):
            b.sim.log("login_fail", user, self.client_ip)
            acc = b.accounts.get(user)
            why = ("Benutzer unbekannt (bekannt: " + ", ".join(sorted(b.accounts)) + ")" if not acc else
                   "Konto deaktiviert" if not acc.get("enabled", True) else "Passwort falsch")
            print(f"  [login] {NAME[b.vendor]} {b.addr or b.host}: Anmeldung von „{user}“ abgelehnt: {why}",
                  file=sys.stderr)
            return self.send(401, rf_error("Anmeldung fehlgeschlagen: Benutzername oder Passwort falsch."))
        tok = b.sim.new_session(user, self.client_ip)
        body = jbody({"@odata.id": f"/redfish/v1/SessionService/Sessions/{tok[:8]}", "Id": tok[:8],
                      "UserName": user, "Name": "User Session"})
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Auth-Token", tok)
        self.send_header("Location", f"/redfish/v1/SessionService/Sessions/{tok[:8]}")
        self.send_header("Set-Cookie", f"sessionKey={tok}; Path=/; HttpOnly; Secure; SameSite=Lax")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        p = self.norm()
        if not self.authed():
            return self.need_auth()
        if p.startswith("/redfish/v1/SessionService/Sessions/"):
            tok = self.session_token()
            if tok:
                self.bmc.sim.end_session(tok)
            self.send_response(200)
            self.send_header("Set-Cookie", "sessionKey=; Path=/; Max-Age=0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p in ("/redfish/v1/LicenseService/Licenses/1", f"/redfish/v1/Managers/{MGR[self.bmc.family]}/LicenseService/1"):
            ok, msg = self.bmc.sim.lic_remove(self.actor())
            return self.send(200, {"ok": True}) if ok else self.send(400, rf_error(msg))
        return self.send(404, rf_error("Nicht gefunden: " + p))

    def cert_action(self, p, data):
        """Zertifikat einspielen (Redfish ReplaceCertificate und die Hersteller-Wege, die BMC-Steward nutzt)
        oder CSR erzeugen."""
        b = self.bmc
        if "CSR" in p.upper():
            ok, out = b.sim.cert_csr(self.actor(), str(data.get("CommonName") or ""), str(data.get("Organization") or ""),
                                     str(data.get("Country") or ""), str(data.get("State") or ""),
                                     str(data.get("City") or ""))
            if not ok:
                return self.send(400, rf_error(out))
            return self.send(200, {"CSRString": out, "CertificateCollection": {
                "@odata.id": f"/redfish/v1/Managers/{MGR[b.family]}/NetworkProtocol/HTTPS/Certificates"}})
        pem = next((data[k] for k in ("CertificateString", "Certificate", "SSLCertificateFile")
                    if isinstance(data.get(k), str) and data[k].strip()), None)
        if pem is not None:
            ok, msg = b.sim.cert_install(pem, self.actor())
            if not ok:
                return self.send(400, rf_error(msg))
        b.extra["cert"] = True
        return self.send(200, {"ok": True})

    def vmedia_insert(self, mid, image, wp=True):
        """Virtual Media einlegen; ohne Lizenz abgelehnt (wie bei echten BMCs)."""
        if not self.bmc.sim.licensed:
            return False, "A license is required for virtual media (license key not installed)."
        return self.bmc.sim.vm_insert(mid, image, wp, self.actor())

    def vmedia_action(self, mid, action, data):
        b = self.bmc
        if action == "InsertMedia":
            ok, msg = self.vmedia_insert(mid, data.get("Image"), data.get("WriteProtected", True))
        else:
            ok, msg = b.sim.vm_eject(mid, self.actor())
        if ok:
            return self.send(200, {"ok": True})
        return self.send(403 if "icense" in msg else 400, rf_error(msg))

    def need_auth(self):
        body = jbody(rf_error("Benutzername oder Passwort falsch."))
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="BMC"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def norm(self):
        p = self.path.split("?", 1)[0]
        if p != "/redfish/v1/" and len(p) > 1 and p.endswith("/"):
            p = p.rstrip("/")
        return p

    # -- GET ---------------------------------------------------------------
    def do_GET(self):
        p = self.norm()
        b = self.bmc
        if b.vendor == "hpe_old" and b.gen == "ilo3" and p.startswith("/redfish"):
            return self.send(404, rf_error("kein Redfish"))
        if p in ("/redfish/v1", "/redfish/v1/"):
            return self.send(200, self.root_doc())
        if p == "/xmldata":
            return self.xmldata()
        if webui.handle(self, "GET", p):
            return
        if p == "/":
            return self.landing()
        if p in ("/console", "/irc.html", "/cgi/url_redirect.cgi"):
            return self.console_preview()
        if p == "/vbmc-kvm":
            return self.console_kvm()
        if not self.authed():
            return self.need_auth()
        mgr = f"/redfish/v1/Managers/{MGR[b.family]}"
        sysp = f"/redfish/v1/Systems/{SYS[b.family]}"
        ext = redfish_ext.get(b, p, SYS, MGR)
        if ext is not None:
            return self.send(200, ext)
        if p == mgr:
            return self.send(200, self.manager_doc())
        if p == mgr + "/Attributes":
            if b.gen == "idrac8_alt":
                return self.send(404, rf_error("Ressource nicht vorhanden (Firmware zu alt)"))
            return self.send(200, {"@odata.id": p, "Attributes": b.attributes})
        if p == mgr + "/DateTime":
            return self.send(200, {"@odata.id": p, "DateTime": time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                                   "StaticNTPServers": b.extra.get("ntp", [])})
        if p == mgr + "/NetworkProtocol":
            doc = {"@odata.id": p, "NTP": {"ProtocolEnabled": True, "NTPServers": b.extra.get("ntp", [])}}
            for proto, port in (("IPMI", 623), ("SSDP", 1900), ("SNMP", 161)):
                doc[proto] = {"ProtocolEnabled": b.extra.get("proto", {}).get(proto, True), "Port": port}
            return self.send(200, doc)
        if p == f"{mgr}/EthernetInterfaces/{NIC.get(b.vendor, '1')}":
            return self.send(200, self.nic_doc(p))
        if p == "/redfish/v1/AccountService":
            lock = b.extra.get("lockout", {})
            return self.send(200, {"@odata.id": p, "ServiceEnabled": True,
                                   "AccountLockoutThreshold": lock.get("AccountLockoutThreshold", 0),
                                   "AccountLockoutDuration": lock.get("AccountLockoutDuration", 0),
                                   "AccountLockoutCounterResetAfter": lock.get("AccountLockoutCounterResetAfter", 0),
                                   "Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}})
        if p == "/redfish/v1/AccountService/Accounts":
            return self.send(200, self.accounts_doc())
        m = re.match(r"^/redfish/v1/AccountService/Accounts/(.+)$", p)
        if m:
            return self.account_get(m.group(1))
        if p == "/redfish/v1/Systems":
            return self.send(200, {"@odata.id": p, "Members@odata.count": 1,
                                   "Members": [{"@odata.id": sysp}]})
        if p == sysp:
            return self.send(200, self.system_doc(sysp))
        if p == sysp + "/Bios":
            return self.send(200, {"@odata.id": p, "Attributes": b.bios,
                                   "@Redfish.Settings": {"SettingsObject": {"@odata.id": p + "/Settings"}}})
        if p == sysp + "/Bios/Settings":
            return self.send(200, {"@odata.id": p, "Attributes": {}})
        # ---- UpdateService / Firmware ----
        if p == "/redfish/v1/UpdateService":
            return self.send(200, self.updateservice_doc())
        if p == "/redfish/v1/UpdateService/FirmwareInventory":
            members = [{"@odata.id": f"/redfish/v1/UpdateService/FirmwareInventory/{k}"}
                       for k in b.firmware]
            return self.send(200, {"@odata.id": p, "Members@odata.count": len(members),
                                   "Members": members})
        m = re.match(r"^/redfish/v1/UpdateService/FirmwareInventory/(.+)$", p)
        if m and m.group(1) in b.firmware:
            fw = b.firmware[m.group(1)]
            return self.send(200, {"@odata.id": p, "Id": m.group(1), "Name": fw["name"],
                                   "Version": fw["version"], "Updateable": fw["updateable"],
                                   "Status": {"State": "Enabled", "Health": "OK"}})
        m = re.match(r"^/redfish/v1/TaskService/Tasks/(.+)$", p)
        if m:
            return self.task_doc(m.group(1))
        if p == "/redfish/v1/TaskService":
            return self.send(200, {"@odata.id": p,
                                   "Tasks": {"@odata.id": "/redfish/v1/TaskService/Tasks"}})
        return self.send(404, rf_error("Nicht gefunden: " + p))

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        p = self.norm()
        b = self.bmc
        if p == "/ribcl":
            return self.ribcl()
        if p in ("/redfish/v1/SessionService/Sessions", "/redfish/v1/Sessions"):
            return self.session_create(self.read_json())
        if webui.handle(self, "POST", p):
            return
        if not self.authed():
            return self.need_auth()
        mgr = f"/redfish/v1/Managers/{MGR[b.family]}"
        sysp = f"/redfish/v1/Systems/{SYS[b.family]}"
        if p == "/redfish/v1/AccountService/Accounts":
            return self.account_create(self.read_json())
        if p == mgr + "/Actions/Manager.Reset":
            self.read_raw()
            b.sim.log("ilo_reset", self.actor())
            self.send(200, {"ok": True})
            if b.pending_ip:
                self.server.fleet.migrate(b, *b.pending_ip)
                b.pending_ip = None
            return
        if p == sysp + "/Actions/ComputerSystem.Reset":
            kind = self.read_json().get("ResetType", "")
            if kind not in RESET_TYPES:
                return self.send(400, rf_error(f"ResetType '{kind}' wird nicht unterstützt."))
            ok, msg = b.sim.power_action(kind, self.actor(), self.client_ip)
            if not ok:
                return self.send(400, rf_error(msg))
            return self.send(200, {"ok": True})
        m = re.match(r"^" + re.escape(mgr) + r"/VirtualMedia/([^/]+)/Actions/VirtualMedia\.(InsertMedia|EjectMedia)$", p)
        if m:
            return self.vmedia_action(m.group(1), m.group(2), self.read_json())
        if p in ("/redfish/v1/LicenseService/Actions/LicenseService.Install", mgr + "/LicenseService"):
            data = self.read_json()
            ok, msg = b.sim.lic_install(data.get("LicenseString") or data.get("LicenseKey"), self.actor())
            return self.send(200, {"ok": True}) if ok else self.send(400, rf_error(msg))
        if "/LogServices/" in p and p.endswith("/Actions/LogService.ClearLog"):
            self.read_raw()
            b.sim.clear_events(self.actor())
            return self.send(200, {"ok": True})
        if "/Actions/" in p and ("Cert" in p or "CSR" in p.upper()):
            return self.cert_action(p, self.read_json())
        # ---- Firmware: Multipart-/HTTP-Push und SimpleUpdate ----
        if p in ("/redfish/v1/UpdateService/update-multipart", "/redfish/v1/UpdateService/update",
                 "/redfish/v1/UpdateService/FirmwareInventory"):
            return self.firmware_push()
        if p == "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate":
            return self.firmware_simple_update(self.read_json())
        self.read_raw()
        return self.send(404, rf_error("Aktion nicht gefunden: " + p))

    # -- PATCH -------------------------------------------------------------
    def do_PATCH(self):
        p = self.norm()
        b = self.bmc
        if not self.authed():
            return self.need_auth()
        data = self.read_json()
        mgr = f"/redfish/v1/Managers/{MGR[b.family]}"
        sysp = f"/redfish/v1/Systems/{SYS[b.family]}"
        if p == mgr + "/Attributes":
            if b.gen == "idrac8_alt":
                return self.send(404, rf_error("Ressource nicht vorhanden (Firmware zu alt)"))
            return self.dell_attributes(data)
        if p == mgr + "/DateTime":
            b.extra["ntp"] = data.get("StaticNTPServers", [])
            return self.send(200, {"ok": True})
        if p == mgr + "/NetworkProtocol":
            if "NTP" in data:
                b.extra["ntp"] = data["NTP"].get("NTPServers", [])
            for proto in ("IPMI", "SSDP", "SNMP"):
                if isinstance(data.get(proto), dict) and "ProtocolEnabled" in data[proto]:
                    b.extra.setdefault("proto", {})[proto] = bool(data[proto]["ProtocolEnabled"])
            if "Oem" in data:
                b.extra["syslog"] = True
            return self.send(200, {"ok": True})
        if p == "/redfish/v1/AccountService":
            if "LDAP" in data:
                b.extra["ldap"] = True
            for k in ("AccountLockoutThreshold", "AccountLockoutDuration", "AccountLockoutCounterResetAfter"):
                if k in data:
                    b.extra.setdefault("lockout", {})[k] = int(data[k])
            return self.send(200, {"ok": True})
        if p == "/redfish/v1/SessionService":
            if "SessionTimeout" in data:
                t = int(data["SessionTimeout"])
                if not 30 <= t <= 86400:
                    return self.send(400, rf_error("SessionTimeout außerhalb 30..86400"))
                b.extra["session_timeout"] = t
            return self.send(200, {"ok": True})
        if p == f"{mgr}/EthernetInterfaces/{NIC.get(b.vendor, '1')}":
            return self.nic_patch(data)
        m = re.match(r"^/redfish/v1/AccountService/Accounts/(.+)$", p)
        if m:
            return self.account_patch(m.group(1), data)
        if p == sysp + "/Bios/Settings":
            b.bios.update((data.get("Attributes") or {}))
            b.sim.bios_changed(self.actor())
            return self.send(200, {"ok": True})
        if p == sysp and isinstance(data.get("Boot"), dict):
            bo = data["Boot"]
            tgt = bo.get("BootSourceOverrideTarget", b.sim.boot_override["target"])
            en = bo.get("BootSourceOverrideEnabled",
                        "Once" if tgt != "None" and "BootSourceOverrideEnabled" not in bo else
                        b.sim.boot_override["enabled"])
            ok, msg = b.sim.set_boot_override(tgt, en)
            return self.send(200, {"ok": True}) if ok else self.send(400, rf_error(msg))
        m = re.match(r"^" + re.escape(mgr) + r"/VirtualMedia/([^/]+)$", p)
        if m:
            mid = m.group(1)
            if "Image" in data:
                if data.get("Image") in (None, ""):
                    ok, msg = b.sim.vm_eject(mid, self.actor())
                else:
                    ok, msg = self.vmedia_insert(mid, data["Image"], data.get("WriteProtected", True))
                if not ok:
                    return self.send(400 if "icense" not in msg else 403, rf_error(msg))
            boot_next = ((data.get("Oem") or {}).get("Hpe") or {}).get("BootOnNextServerReset")
            if boot_next:
                b.sim.set_boot_override("Cd", "Once")
            return self.send(200, {"ok": True})
        if p in (sysp, f"/redfish/v1/Chassis/{SYS[b.family]}") and "IndicatorLED" in data:
            if data["IndicatorLED"] in ("Off", "Lit", "Blinking"):
                b.sim.uid_led = data["IndicatorLED"]
            return self.send(200, {"ok": True})
        return self.send(404, rf_error("Nicht gefunden: " + p))

    # -- Dokumente ---------------------------------------------------------
    def root_doc(self):
        b = self.bmc
        oem = {"dell": {"Dell": {"@odata.type": "#DellService.v1_0_0.DellService"}},
               "hpe": {"Hpe": {"@odata.type": "#HpeiLO.v2_7_0.HpeiLO",
                               "Manager": [{"ManagerType": b.gen_label, "ManagerFirmwareVersion": b.fw}]}},
               "hpe_old": {"Hp": {"@odata.type": "#HpiLOServiceExt.1.0.0.HpiLOServiceExt",
                                  "Manager": [{"ManagerType": b.gen_label, "ManagerFirmwareVersion": b.fw}]}},
               "smc": {"Supermicro": {"@odata.type": "#SmcService.v1_0_0.SmcService"}},
               "lenovo": {"Lenovo": {"@odata.type": "#LenovoService.v1_0_0.LenovoService"}}}[b.vendor]
        vf = {"dell": "Dell", "hpe": "HPE", "hpe_old": "", "smc": "Supermicro", "lenovo": "Lenovo"}[b.vendor]
        return {"@odata.id": "/redfish/v1/", "RedfishVersion": "1.12.0", "Vendor": vf, "Oem": oem,
                "Managers": {"@odata.id": "/redfish/v1/Managers"},
                "Systems": {"@odata.id": "/redfish/v1/Systems"},
                "AccountService": {"@odata.id": "/redfish/v1/AccountService"},
                "UUID": "00000000-0000-0000-0000-" + b.serial[-12:].rjust(12, "0")}

    def manager_doc(self):
        b = self.bmc
        d = {"@odata.id": f"/redfish/v1/Managers/{MGR[b.family]}", "Id": MGR[b.family],
             "ManagerType": "BMC", "FirmwareVersion": b.fw, "Model": b.gen_label,
             "PowerState": "On",
             "Actions": {"#Manager.Reset": {
                 "target": f"/redfish/v1/Managers/{MGR[b.family]}/Actions/Manager.Reset"}}}
        if b.vendor in NIC:
            d["EthernetInterfaces"] = {"@odata.id": f"/redfish/v1/Managers/{MGR[b.family]}/EthernetInterfaces"}
        return d

    def nic_doc(self, p):
        b = self.bmc
        d = {"@odata.id": p, "Id": NIC.get(b.vendor, "1"), "HostName": b.hostname,
             "IPv4Addresses": [dict(b.ipv4)],
             "IPv4StaticAddresses": [dict(b.ipv4)] if not b.dhcp else [],
             "DHCPv4": {"DHCPEnabled": b.dhcp}}
        if b.vendor == "hpe":
            d["Oem"] = {"Hpe": {"HostName": b.hostname, "DHCPv4": {"Enabled": b.dhcp}}}
        return d

    def accounts_doc(self):
        b = self.bmc
        members = [{"@odata.id": f"/redfish/v1/AccountService/Accounts/{s}"}
                   for s in sorted(a.get("slot", 2) for a in b.accounts.values())]
        return {"@odata.id": "/redfish/v1/AccountService/Accounts",
                "Members@odata.count": len(members), "Members": members}

    def system_doc(self, p):
        b = self.bmc
        return {"@odata.id": p, "Id": SYS[b.family], "Model": b.model,
                "Manufacturer": {"dell": "Dell Inc.", "hpe": "HPE", "hpe_old": "HP", "smc": "Supermicro",
                                 "lenovo": "Lenovo"}[b.vendor],
                "SerialNumber": b.serial, "SKU": b.serial, "PowerState": b.sim.power_state,
                **{k: v for k, v in redfish_ext.system_extras(b, p).items() if k != "HostName"},
                "Actions": {"#ComputerSystem.Reset": {
                    "target": p + "/Actions/ComputerSystem.Reset",
                    "ResetType@Redfish.AllowableValues": list(RESET_TYPES)}}}

    # -- UpdateService / Firmware ------------------------------------------
    def updateservice_doc(self):
        p = "/redfish/v1/UpdateService"
        return {"@odata.id": p, "Id": "UpdateService", "ServiceEnabled": True,
                "HttpPushUri": p + "/update",
                "MultipartHttpPushUri": p + "/update-multipart",
                "FirmwareInventory": {"@odata.id": p + "/FirmwareInventory"},
                "Actions": {"#UpdateService.SimpleUpdate": {
                    "target": p + "/Actions/UpdateService.SimpleUpdate",
                    "TransferProtocol@Redfish.AllowableValues": ["HTTP", "HTTPS", "TFTP", "NFS"]}}}

    @staticmethod
    def _bump(ver):
        """Letzte Zahl in der Version erhöhen, damit ein Update sichtbar wird."""
        m = list(re.finditer(r"\d+", ver))
        if not m:
            return ver + ".1"
        last = m[-1]
        return ver[:last.start()] + str(int(last.group()) + 1) + ver[last.end():]

    def _new_task(self, target):
        b = self.bmc
        with b.lock:
            b._task_seq += 1
            tid = str(b._task_seq)
            comp = target if target in b.firmware else "BMC"
            b.tasks[tid] = {"state": "Running", "percent": 0, "target": comp,
                            "newver": self._bump(b.firmware[comp]["version"]), "start": time.monotonic()}
        threading.Thread(target=self._run_task, args=(tid,), daemon=True).start()
        return tid

    def _run_task(self, tid):
        b = self.bmc
        for pct in (10, 30, 55, 80, 100):
            time.sleep(0.8)
            t = b.tasks.get(tid)
            if not t:
                return
            t["percent"] = pct
        t = b.tasks.get(tid)
        if t:
            b.firmware[t["target"]]["version"] = t["newver"]   # Flash übernommen
            t["state"] = "Completed"

    def send_accepted(self, task_id):
        body = jbody({"@odata.id": f"/redfish/v1/TaskService/Tasks/{task_id}",
                      "Id": task_id, "TaskState": "Running", "TaskStatus": "OK"})
        self.send_response(202)
        self.send_header("Location", f"/redfish/v1/TaskService/Tasks/{task_id}")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def firmware_push(self):
        self.read_raw()                 # Datei verwerfen - der Emulator prüft den Inhalt nicht
        return self.send_accepted(self._new_task("BMC"))

    def firmware_simple_update(self, data):
        targets = data.get("Targets") or []
        comp = "BMC"
        for tgt in targets:
            key = str(tgt).rstrip("/").rsplit("/", 1)[-1]
            if key in self.bmc.firmware:
                comp = key
                break
        if not str(data.get("ImageURI") or "").strip():
            return self.send(400, rf_error("ImageURI fehlt."))
        return self.send_accepted(self._new_task(comp))

    def task_doc(self, tid):
        t = self.bmc.tasks.get(tid)
        if not t:
            return self.send(404, rf_error("Task nicht gefunden: " + tid))
        msg = (f"Flash {t['target']} -> {t['newver']}" if t["state"] == "Completed"
               else f"Updating {t['target']} ({t['percent']}%)")
        return self.send(200, {"@odata.id": f"/redfish/v1/TaskService/Tasks/{tid}", "Id": tid,
                               "TaskState": t["state"], "PercentComplete": t["percent"],
                               "TaskStatus": "OK",
                               "Messages": [{"Message": msg, "Severity": "OK"}]})

    # -- Konten ------------------------------------------------------------
    def resolve_account(self, token):
        return self.bmc.slot_of(token)

    def account_get(self, token):
        b = self.bmc
        name = self.resolve_account(token)
        if name:
            a = b.accounts[name]
            return self.send(200, {"@odata.id": self.path.split("?")[0], "Id": token,
                                   "UserName": name, "RoleId": a["role"], "Enabled": a["enabled"]})
        return self.send(200, {"@odata.id": self.path.split("?")[0], "Id": token,
                               "UserName": "", "RoleId": "None", "Enabled": False})

    def account_create(self, data):
        b = self.bmc
        user = data.get("UserName")
        if not user:
            return self.send(400, rf_error("UserName fehlt."))
        with b.lock:
            if user in b.accounts:
                return self.send(409, rf_error("Benutzer existiert bereits."))
            b.add_account(user, data.get("Password", ""), data.get("RoleId", "Administrator"),
                          data.get("Enabled", True))
        return self.send(201, {"UserName": user})

    def account_patch(self, token, data):
        b = self.bmc
        name = self.resolve_account(token)
        with b.lock:
            if not name:
                user = data.get("UserName")
                if not user:
                    return self.send(400, rf_error("Konto nicht gefunden."))
                if user in b.accounts:
                    return self.send(409, rf_error("Benutzer existiert bereits in einem anderen Slot."))
                slot = int(token) if token.isdigit() else None      # leerer Slot: genau dort anlegen
                b.add_account(user, data.get("Password", ""), data.get("RoleId", "Administrator"),
                              data.get("Enabled", True), slot=slot)
                return self.send(200, {"ok": True})
            a = b.accounts[name]
            if "Password" in data:
                a["password"] = data["Password"]
            if "RoleId" in data:
                a["role"] = data["RoleId"]
            if "Enabled" in data:
                a["enabled"] = data["Enabled"]
            if data.get("UserName") and data["UserName"] != name:
                b.accounts[data["UserName"]] = b.accounts.pop(name)
        return self.send(200, {"ok": True})

    # -- Netzwerk ----------------------------------------------------------
    def dell_attributes(self, data):
        b = self.bmc
        attrs = data.get("Attributes") or {}
        b.attributes.update(attrs)
        if attrs.get("NIC.1.DNSRacName"):
            b.hostname = attrs["NIC.1.DNSRacName"]
        if attrs.get("IPv4Static.1.Address"):
            b.dhcp = False
            self.send(200, {"ok": True})
            self.server.fleet.migrate(b, attrs["IPv4Static.1.Address"],
                                      b.attributes.get("IPv4Static.1.Netmask"),
                                      b.attributes.get("IPv4Static.1.Gateway"))
            return
        if str(attrs.get("IPv4.1.DHCPEnable", "")).lower() == "enabled" and not b.dhcp:
            self.send(200, {"ok": True})
            self.server.fleet.set_dhcp(b)
            return
        return self.send(200, {"ok": True})

    def nic_patch(self, data):
        b = self.bmc
        host = data.get("HostName")
        if not host and isinstance(data.get("Oem"), dict):
            host = (data["Oem"].get("Hpe") or {}).get("HostName")
        if host:
            b.hostname = host
        was_dhcp = b.dhcp
        if isinstance(data.get("DHCPv4"), dict) and "DHCPEnabled" in data["DHCPv4"]:
            b.dhcp = data["DHCPv4"]["DHCPEnabled"]
        if isinstance(data.get("Oem"), dict):
            for sub in data["Oem"].values():
                if isinstance(sub, dict) and isinstance(sub.get("DHCPv4"), dict):
                    b.dhcp = sub["DHCPv4"].get("Enabled", b.dhcp)
                if isinstance(sub, dict) and "DHCPEnabled" in sub:
                    b.dhcp = sub["DHCPEnabled"]
        addr = data.get("IPv4StaticAddresses") or data.get("IPv4Addresses")
        if addr and isinstance(addr, list) and isinstance(addr[0], dict) and addr[0].get("Address"):
            b.dhcp = False
            new = (addr[0]["Address"], addr[0].get("SubnetMask"), addr[0].get("Gateway"))
            if b.vendor == "hpe":
                b.pending_ip = new          # iLO: wirkt erst nach dem Neustart
                return self.send(200, {"ok": True})
            self.send(200, {"ok": True})
            self.server.fleet.migrate(b, *new)
            return
        if b.dhcp and not was_dhcp:
            self.send(200, {"ok": True})
            self.server.fleet.set_dhcp(b)
            return
        return self.send(200, {"ok": True})

    # -- iLO 3/4: xmldata + RIBCL ------------------------------------------
    def xmldata(self):
        b = self.bmc
        if b.vendor != "hpe_old":
            return self.send(404, rf_error("kein xmldata"))
        xml = (f'<?xml version="1.0"?>\r\n<RIMP><HSI><SBSN>{b.serial}</SBSN><SPN>{b.model}</SPN></HSI>'
               f'<MP><PN>Integrated Lights-Out {b.gen[-1]} (iLO {b.gen[-1]})</PN><FWRI>{b.fw}</FWRI>'
               f'<SN>{b.serial}</SN></MP></RIMP>\r\n')
        return self.send_text(200, xml, "text/xml")

    def ribcl(self):
        b = self.bmc
        raw = self.read_raw().decode("utf-8", "replace")
        if b.vendor != "hpe_old":
            return self.send_text(200, self._ribcl_resp(0x0001, "Nicht unterstützt"), "text/xml")
        mu = re.search(r'USER_LOGIN\s*=\s*"([^"]*)"', raw)
        mp = re.search(r'PASSWORD\s*=\s*"([^"]*)"', raw)
        if not (mu and mp and b.check_auth(mu.group(1), mp.group(1))):
            return self.send_text(200, self._ribcl_resp(0x005F, "Login failed"), "text/xml")
        parts = [self._ribcl_resp(0x0000, "No error")]
        if "GET_FW_VERSION" in raw:
            parts.append(f'<GET_FW_VERSION FIRMWARE_VERSION="{b.fw}" MANAGEMENT_PROCESSOR="iLO{b.gen[-1]}"/>\r\n'
                         + self._ribcl_resp(0x0000, "No error"))
        if "GET_ALL_USERS" in raw:
            users = "".join(f'<USER_LOGIN VALUE="{u}"/>' for u in b.accounts)
            parts.append(f"<GET_ALL_USERS>{users}</GET_ALL_USERS>\r\n" + self._ribcl_resp(0x0000, "No error"))
        if "ADD_USER" in raw:
            seg = raw[raw.find("ADD_USER"):]
            nu = re.search(r'USER_LOGIN\s*=\s*"([^"]*)"', seg)
            np_ = re.search(r'PASSWORD\s*=\s*"([^"]*)"', seg)
            if nu and nu.group(1) not in b.accounts:
                b.add_account(nu.group(1), np_.group(1) if np_ else "")
            parts.append(self._ribcl_resp(0x0000, "No error"))
        if "MOD_USER" in raw:
            seg = raw[raw.find("MOD_USER"):]
            nu = re.search(r'USER_LOGIN\s*=\s*"([^"]*)"', seg)
            np_ = re.search(r'PASSWORD\s+value\s*=\s*"([^"]*)"', seg)
            if nu and nu.group(1) in b.accounts and np_:
                b.accounts[nu.group(1)]["password"] = np_.group(1)
            parts.append(self._ribcl_resp(0x0000, "No error"))
        sim, who = b.sim, mu.group(1)
        if "GET_HOST_POWER_STATUS" in raw:
            on = sim.power_state in ("On", "PoweringOff")
            parts.append(f'<GET_HOST_POWER HOST_POWER="{"ON" if on else "OFF"}"/>\r\n'
                         + self._ribcl_resp(0x0000, "No error"))
        if "GET_SERVER_NAME" in raw:
            parts.append(f'<SERVER_NAME VALUE="{b.hostname or b.model}"/>\r\n' + self._ribcl_resp(0x0000, "No error"))
        if "GET_EVENT_LOG" in raw:
            ev = "".join(f'<EVENT SEVERITY="{e["severity"]}" CLASS="{e["source"]}" '
                         f'LAST_UPDATE="{time.strftime("%m/%d/%Y %H:%M", time.localtime(e["time"]))}" '
                         f'INITIAL_UPDATE="{time.strftime("%m/%d/%Y %H:%M", time.localtime(e["time"]))}" '
                         f'COUNT="1" DESCRIPTION="{e["message"]}"/>' for e in reversed(sim.events))
            parts.append(f'<EVENT_LOG DESCRIPTION="Integrated Management Log">{ev}</EVENT_LOG>\r\n'
                         + self._ribcl_resp(0x0000, "No error"))
        # (RIBCL-Befehl, Redfish-Aktion, Bedingung-Text bei Fehler)
        for tag, kind, need in (("PRESS_PWR_BTN", "PushPowerButton", None), ("HOLD_PWR_BTN", "ForceOff", "on"),
                                ("COLD_BOOT_SERVER", "PowerCycle", "on"), ("WARM_BOOT_SERVER", "ForceRestart", "on"),
                                ("RESET_SERVER", "ForceRestart", "on")):
            if re.search(rf"<{tag}\b", raw):
                if need == "on" and sim.power_state == "Off":
                    parts.append(self._ribcl_resp(0x0001, "Server is currently powered off."))
                else:
                    sim.power_action(kind, who, self.client_ip)
                    parts.append(self._ribcl_resp(0x0000, "No error"))
        msp = re.search(r'<SET_HOST_POWER\s+HOST_POWER\s*=\s*"([^"]*)"', raw)
        if msp:
            want_on = msp.group(1).strip().lower() in ("yes", "on", "y")
            is_on = sim.power_state != "Off"
            if want_on == is_on:
                parts.append(self._ribcl_resp(0x0001, f"Host power is already {'ON' if is_on else 'OFF'}."))
            else:
                sim.power_action("On" if want_on else "GracefulShutdown", who, self.client_ip)
                parts.append(self._ribcl_resp(0x0000, "No error"))
        new_ip = None
        if "MOD_NETWORK_SETTINGS" in raw:
            seg = raw[raw.find("MOD_NETWORK_SETTINGS"):]
            mi = re.search(r'<IP_ADDRESS\s+value\s*=\s*"([^"]*)"', seg)
            mm = re.search(r'SUBNET_MASK\s+value\s*=\s*"([^"]*)"', seg)
            mg = re.search(r'GATEWAY_IP_ADDRESS\s+value\s*=\s*"([^"]*)"', seg)
            mn = re.search(r'DNS_NAME\s+value\s*=\s*"([^"]*)"', seg)
            if mn:
                b.hostname = mn.group(1)
            if mi:
                new_ip, b.dhcp = (mi.group(1), mm.group(1) if mm else None, mg.group(1) if mg else None), False
            parts.append(self._ribcl_resp(0x0000, "No error"))
        self.send_text(200, "".join(parts), "text/xml")
        if new_ip:
            self.server.fleet.migrate(b, *new_ip)

    @staticmethod
    def _ribcl_resp(status, message):
        return ('<?xml version="1.0"?>\r\n<RIBCL VERSION="2.22">\r\n'
                f'<RESPONSE STATUS="0x{status:04X}" MESSAGE="{message}"/>\r\n</RIBCL>\r\n')

    # -- Weboberfläche (eingebettete Konsole) -------------------------------
    # Verhält sich wie ein echter BMC: Anmeldeseite -> kleine, gerahmte Konsolenvorschau ->
    # Klick öffnet die (simulierte) UEFI-Oberfläche in einem eigenen Fenster/Reiter.
    def landing(self):
        b = self.bmc
        qs = parse_qs(urlsplit(self.path).query)
        user, pw = (qs.get("user") or [""])[0], (qs.get("pass") or [""])[0]
        if user or pw:
            if b.check_auth(user, pw):
                self.send_response(302)
                self.send_header("Set-Cookie", f"vbuisess={b.idx}; Path=/")
                self.send_header("Location", CONSOLE_UI_PATH.get(b.vendor, "/"))
                self.send_header("Content-Length", "0")
                return self.end_headers()
            return self.send_text(200, login_page(b, "Benutzername oder Passwort falsch."),
                                   "text/html; charset=utf-8")
        if self.ui_authed():
            return self.send_text(200, console_preview_page(b), "text/html; charset=utf-8")
        return self.send_text(200, login_page(b), "text/html; charset=utf-8")

    def console_preview(self):
        if not self.ui_authed():
            return self.redirect("/")
        return self.send_text(200, console_preview_page(self.bmc), "text/html; charset=utf-8")

    def console_kvm(self):
        if not self.ui_authed():
            return self.redirect("/")
        return self.send_text(200, console_kvm_page(self.bmc), "text/html; charset=utf-8")


# ------------------------------------------------------------------ Flotte
class Fleet:
    """Alle laufenden virtuellen Server. Ohne Netzlabor (lab=None) laufen sie auf 127.x-Adressen dieses
    Rechners; mit Netzlabor hat jeder Server einen eigenen Netzwerk-Namespace mit echter Karte."""

    def __init__(self, ctx, redfish_ports, console=False, lab=None, emit=None, legacy_ctx=None):
        self.ctx = ctx
        self.legacy_ctx = legacy_ctx            # TLS 1.0 für iLO 3 (wenn das System es noch kann)
        self.redfish_ports = redfish_ports      # z. B. [8443] oder [8443, 443]; Netzlabor: [443]
        self.console = console
        self.lab = lab
        self.emit = emit or (lambda: None)
        self.bmcs = {}                          # idx -> BMC
        self.lock = threading.Lock()
        threading.Thread(target=self._sync_auth, daemon=True).start()

    def _sync_auth(self):
        """Benutzeränderungen (Redfish/RIBCL) laufend an die IPMI-Kindprozesse weitergeben."""
        while True:
            time.sleep(1)
            for b in self.table():
                if b._ipmi is not None:
                    try:
                        _write_auth(b)
                    except OSError:
                        pass

    def _ctx_for(self, bmc):
        return self.legacy_ctx if (bmc.gen == "ilo3" and self.legacy_ctx) else self.ctx

    def _bind_redfish(self, bmc):
        bmc._redfish = []
        for port in self.redfish_ports:
            try:
                with _ns(bmc.netns):
                    srv = Server((bmc.host, port), bmc, self)
                    srv.socket = self._ctx_for(bmc).wrap_socket(srv.socket, server_side=True)
            except PermissionError:
                print(f"  [redfish] {bmc.host}:{port} nicht gebunden (Rechte für Port < 1024). "
                      f"Konsolen-Reiter braucht Redfish auf 443 -> mit sudo starten.", file=sys.stderr)
                continue
            except OSError as e:
                print(f"  [redfish] {bmc.host}:{port} nicht gebunden ({e.errno}).", file=sys.stderr)
                continue
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            bmc._redfish.append(srv)
        if not bmc._redfish:
            raise OSError(f"keine Redfish-Ports gebunden für {bmc.host}")

    def _start_ssdp(self, bmc):
        """SSDP-Antwortdienst: im Netzlabor auf der eigenen Karte (mit Multicast), sonst auf der Server-Adresse."""
        port = ssdp.ssdp_port()
        if not port:
            return
        try:
            with _ns(bmc.netns):
                bmc._ssdp = ssdp.Ssdp(bmc, "0.0.0.0" if bmc.netns else bmc.host, port, multicast=bool(bmc.netns))
        except OSError as e:
            print(f"  [ssdp] {bmc.host}:{port} nicht gebunden ({e.errno}); Erkennung per SSDP geht für diesen Server nicht.",
                  file=sys.stderr)

    def _unbind_redfish(self, bmc):
        for srv in bmc._redfish:
            srv.shutdown()
            srv.server_close()
        bmc._redfish = []

    def start(self, bmc):
        with self.lock:
            if bmc.idx in self.bmcs:
                raise ValueError(f"Server {bmc.idx} läuft bereits")
            if not self.lab and any(b.addr == bmc.addr for b in self.bmcs.values()):
                raise ValueError(f"Adresse {bmc.addr} läuft bereits")
            if self.lab:
                if not bmc.mac:
                    bmc.mac = netlab.mac_for(bmc.vendor, bmc.serial)
                bmc.netns = self.lab.add_server(bmc.idx, bmc.mac)
                bmc.host = bmc.console_host = "0.0.0.0"     # alle Adressen der eigenen Karte
                bmc.port = REDFISH_STD
                bmc.ipv4 = {"Address": "", "SubnetMask": "", "Gateway": ""}
            try:
                self._bind_redfish(bmc)
            except Exception:
                if self.lab and bmc.netns:
                    self.lab.remove_server(bmc.netns)
                raise
            self._start_ssdp(bmc)
            if self.console:
                self._start_console(bmc)
            self.bmcs[bmc.idx] = bmc
        if self.lab:
            self._factory_network(bmc)
        else:
            bmc.status = "läuft (Loopback)"
        self.emit()
        return bmc

    # -- Netz im Labor ---------------------------------------------------
    def _factory_network(self, bmc):
        if bmc.factory_net == "static":
            prefix = prefix_of(bmc.factory_mask)
            self.lab.set_address(bmc.netns, bmc.factory_ip, prefix, bmc.factory_gw or None)
            bmc.ipv4 = {"Address": bmc.factory_ip, "SubnetMask": mask_of(prefix), "Gateway": bmc.factory_gw}
            bmc.dhcp = False
            bmc.status = "feste Werks-IP"
            print(f"  [netz] {NAME[bmc.vendor]} {bmc.serial}: feste Werks-IP {bmc.factory_ip}")
        else:
            self._start_dhcp(bmc)

    def _start_dhcp(self, bmc):
        bmc.dhcp = True
        bmc.status = "sucht DHCP-Server"

        def bound(ip, prefix, gw, lease, server):
            if not bmc.dhcp:
                return
            self.lab.set_address(bmc.netns, ip, prefix, gw)
            bmc.ipv4 = {"Address": ip, "SubnetMask": mask_of(prefix), "Gateway": gw or ""}
            bmc.status = f"Adresse per DHCP von {server}"
            print(f"  [dhcp] {NAME[bmc.vendor]} {bmc.serial} ({bmc.mac}): {ip}/{prefix} von {server}")
            self.emit()

        def lost():
            self.lab.clear_address(bmc.netns)
            bmc.ipv4 = {"Address": "", "SubnetMask": "", "Gateway": ""}
            bmc.status = "Adresse verloren, sucht DHCP-Server"
            self.emit()

        bmc.dhcpc = netlab.DhcpClient("eth0", bmc.mac, ns=bmc.netns, hostname=bmc.dhcp_hostname(),
                                      on_bound=bound, on_lost=lost, log=print)
        bmc.dhcpc.start()

    def _stop_dhcp(self, bmc, release=True):
        if bmc.dhcpc:
            bmc.dhcpc.stop(release=release)
            bmc.dhcpc = None

    def set_dhcp(self, bmc):
        """BMC wieder auf DHCP stellen (Konfigurationsänderung von außen)."""
        bmc.dhcp = True
        if not self.lab:
            return

        def do():
            time.sleep(0.6)
            self.lab.clear_address(bmc.netns)
            bmc.ipv4 = {"Address": "", "SubnetMask": "", "Gateway": ""}
            if not bmc.dhcpc:
                self._start_dhcp(bmc)
            self.emit()
        threading.Thread(target=do, daemon=True).start()

    def set_link(self, idx, up):
        bmc = self.bmcs.get(idx)
        if not bmc or not self.lab:
            return False
        self.lab.set_link(bmc.idx, bmc.netns, up)
        bmc.link_up = up
        self.emit()
        return True

    # -- Konsole -----------------------------------------------------------
    def _start_console(self, bmc):
        notes = []
        if paramiko is not None:
            try:
                start_ssh(bmc)
            except PermissionError:
                notes.append("SSH:22 (Rechte)")
            except OSError as e:
                notes.append(f"SSH:22 ({e.errno})")
        else:
            notes.append("SSH (paramiko fehlt)")
        if _ipmibmc is not None:
            try:
                start_ipmi(bmc)
            except PermissionError:
                notes.append("IPMI:623 (Rechte)")
            except OSError as e:
                notes.append(f"IPMI:623 ({e.errno})")
        else:
            notes.append("IPMI (pyghmi fehlt)")
        if notes:
            where = bmc.serial if bmc.netns else bmc.console_host
            print(f"  [console] {where}: nicht gestartet: {', '.join(notes)}", file=sys.stderr)

    def find(self, key):
        """Server über Nummer, Adresse (mit/ohne Port) oder Seriennummer."""
        with self.lock:
            for b in self.bmcs.values():
                if str(b.idx) == str(key) or b.addr == key or b.serial == key or \
                        (b.addr and b.addr.split(":")[0] == key):
                    return b
        return None

    def stop(self, key):
        bmc = key if isinstance(key, BMC) else self.find(key)
        if not bmc:
            return False
        with self.lock:
            self.bmcs.pop(bmc.idx, None)
        self._stop_dhcp(bmc, release=True)
        self._unbind_redfish(bmc)
        if bmc._ssdp is not None:
            bmc._ssdp.close()
            bmc._ssdp = None
        if bmc._ssh:
            try:
                bmc._ssh[0].close()
            except Exception:
                pass
            bmc._ssh = None
        if bmc._ipmi is not None:
            try:
                bmc._ipmi.terminate()
            except Exception:
                pass
            bmc._ipmi = None
        if self.lab and bmc.netns:
            self.lab.remove_server(bmc.netns)
            bmc.netns = None
        return True

    def migrate(self, bmc, new_addr, mask=None, gw=None):
        """Neue Adresse übernehmen (wie ein echter BMC kurz nach der Antwort). Im Netzlabor bekommt die
        Karte des Servers die neue Adresse; ohne Netzlabor wandert der Redfish-Dienst auf die 127.x-Adresse
        (SSH/IPMI bleiben dort auf der Startadresse)."""
        host, _, port = str(new_addr).partition(":")
        old = bmc.addr

        if self.lab:
            def do_lab():
                time.sleep(0.6)
                self._stop_dhcp(bmc, release=True)
                prefix = prefix_of(mask or bmc.ipv4.get("SubnetMask"))
                try:
                    self.lab.set_address(bmc.netns, host, prefix, gw or None)
                except Exception as e:
                    bmc.status = f"Umstellen fehlgeschlagen: {e}"
                    print(f"  [migrate] {old} -> {host} fehlgeschlagen: {e}", file=sys.stderr)
                    self.emit()
                    return
                bmc.ipv4 = {"Address": host, "SubnetMask": mask_of(prefix), "Gateway": gw or ""}
                bmc.dhcp = False
                bmc.status = "feste IP (umgestellt)"
                print(f"  [migrate] {old or '(ohne Adresse)'} -> {host}/{prefix} ({NAME[bmc.vendor]})")
                self.emit()
            threading.Thread(target=do_lab, daemon=True).start()
            return

        port = int(port or bmc.port)

        def do():
            time.sleep(0.6)
            with self.lock:
                old_host, old_port, old_ipv4 = bmc.host, bmc.port, dict(bmc.ipv4)
                self._unbind_redfish(bmc)
                bmc.host, bmc.port = host, port
                bmc.ipv4["Address"] = host
                if mask:
                    bmc.ipv4["SubnetMask"] = mask
                if gw is not None:
                    bmc.ipv4["Gateway"] = gw
                try:
                    self._bind_redfish(bmc)
                except Exception as e:
                    # Ohne Netzlabor gehört nur 127.x diesem Rechner - eine andere Adresse lässt sich nie
                    # binden. Wie bei einem echten BMC darf eine abgelehnte/fehlgeschlagene Adressänderung
                    # die bestehende Erreichbarkeit nicht mit sich reißen: die alte Adresse bleibt aktiv.
                    hint = (" - ohne Netzlabor gehört nur 127.x diesem Rechner; für {0} das Testlabor im "
                            "Modus „Konfigraum“ oder „Zuhause/Router“ starten.".format(host)
                            if not host.startswith("127.") else "")
                    bmc.host, bmc.port, bmc.ipv4 = old_host, old_port, old_ipv4
                    try:
                        self._bind_redfish(bmc)
                        bmc.status = f"Umstellen fehlgeschlagen, alte Adresse bleibt aktiv: {e}{hint}"
                    except Exception as e2:
                        bmc.status = f"Umstellen fehlgeschlagen, auch alte Adresse nicht mehr erreichbar: {e2}"
                    print(f"  [migrate] {old} -> {new_addr} fehlgeschlagen: {e}{hint}", file=sys.stderr)
                    self.emit()
                    return
            print(f"  [migrate] {old} -> {bmc.addr} ({NAME[bmc.vendor]})")
            self.emit()
        threading.Thread(target=do, daemon=True).start()

    def table(self):
        with self.lock:
            return sorted(self.bmcs.values(), key=lambda b: b.idx)


# ------------------------------------------------------------------ CLI
def auto_host(index, base):
    return f"{base}.{11 + index}"


ALIASES = {"hp": "hpe", "ilo": "hpe", "idrac": "dell", "xcc": "lenovo", "supermicro": "smc",
           "ipmi": "smc", "hpe4": "hpe_old", "ilo4": "hpe_old", "ilo3": "hpe_old"}


def parse_spec(spec, index, base, port):
    vendor, _, where = spec.partition("@")
    vendor = vendor.strip().lower()
    gen = "ilo3" if vendor == "ilo3" else None
    vendor = ALIASES.get(vendor, vendor)
    if vendor not in VENDORS:
        raise ValueError(f"unbekannter Hersteller: {spec} (erlaubt: {', '.join(VENDORS)})")
    if where:
        host, _, p = where.partition(":")
        port = int(p or port)
    else:
        host = auto_host(index, base)
    return BMC(vendor, host, port, gen=gen, idx=index + 1)


def load_config(path):
    """Szenario-Datei: Liste von Servern oder {"servers": [...], "mode": ..., ...}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"servers": data}
    if not isinstance(data, dict):
        raise ValueError("Szenario muss eine Liste oder ein Objekt mit \"servers\" sein")
    data.setdefault("servers", [])
    return data


def bmc_from_entry(e, idx, base, port):
    vendor = ALIASES.get(str(e.get("vendor", "")).lower(), str(e.get("vendor", "")).lower())
    if vendor not in VENDORS:
        raise ValueError(f"unbekannter Hersteller in der Konfiguration: {e.get('vendor')!r}")
    busy = e.get("busy_slots") or {}
    if isinstance(busy, (int, str)) and str(busy).strip():
        busy = {str(busy).strip(): "operator"}
    return BMC(vendor, e.get("host") or auto_host(idx, base), int(e.get("port", port)),
               serial=e.get("serial") or None, model=e.get("model") or None,
               user=e.get("user") or None, password=e.get("password") or None,
               gen=e.get("gen") or None, mac=(e.get("mac") or "").lower() or None,
               net=e.get("net"), ip=e.get("ip") or None, mask=e.get("mask") or None,
               gw=e.get("gw") or None, busy_slots=busy, idx=idx + 1,
               license=e.get("license") or None)


def build_fleet(args, cfg=None):
    bmcs, idx = [], 0
    if cfg:
        for e in cfg["servers"]:
            bmcs.append(bmc_from_entry(e, idx, args.base, args.port))
            idx += 1
    if args.preset:
        for v in ("dell", "hpe", "lenovo", "smc"):
            bmcs.append(BMC(v, auto_host(idx, args.base), args.port, idx=idx + 1))
            idx += 1
    if args.vendor:
        for _ in range(args.count):
            bmcs.append(BMC(args.vendor, auto_host(idx, args.base), args.port, idx=idx + 1))
            idx += 1
    for spec in args.specs:
        bmcs.append(parse_spec(spec, idx, args.base, args.port))
        idx += 1
    return bmcs


def print_table(fleet):
    bmcs = fleet.table()
    if not bmcs:
        print("  (keine Server aktiv)")
        return
    lab = fleet.lab is not None
    w = max([15] + [len(b.addr) for b in bmcs])
    head = f"  {'Nr':>2}  {'Adresse'.ljust(w)}  {'Hersteller'.ljust(20)}  {'Generation'.ljust(12)}  Werks-Login"
    if lab:
        head += "                   MAC"
    elif fleet.console:
        head += "                   SSH/IPMI (Konsole)"
    print(head)
    print("  " + "-" * (len(head) - 2))
    for b in bmcs:
        login = f"{b.factory_user} / {b.factory_pass}"
        addr = b.addr or ("(DHCP …)" if b.dhcp else "?")
        line = f"  {b.idx:>2}  {addr.ljust(w)}  {NAME[b.vendor].ljust(20)}  {b.gen_label[:12].ljust(12)}  {login.ljust(24)}"
        if lab:
            line += f"  {b.mac}"
        elif fleet.console:
            line += f"  {b.console_host} (22/623)"
        print(line)


def interactive(fleet, args):
    print("\nKonsole: add <hersteller> [host[:port]] | list | rm <adresse|nr> | link <nr> up|down | quit")
    while True:
        try:
            line = input("vbmc> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        cmd, *rest = line.split()
        if cmd in ("quit", "exit", "q"):
            return
        if cmd in ("list", "ls"):
            print_table(fleet)
        elif cmd == "add":
            if not rest:
                print("  Nutzung: add dell [127.9.0.20[:8443]]")
                continue
            spec = rest[0] if "@" in rest[0] else (rest[0] + "@" + rest[1] if len(rest) > 1 else rest[0])
            try:
                nr = max([b.idx for b in fleet.table()] + [0])
                b = parse_spec(spec, nr, args.base, args.port)
                fleet.start(b)
                print(f"  gestartet: {b.addr or '(DHCP …)'}  {NAME[b.vendor]}  Login {b.factory_user} / "
                      f"{b.factory_pass}")
            except Exception as e:
                print(f"  Fehler: {e}")
        elif cmd in ("rm", "del", "stop"):
            if not rest:
                print("  Nutzung: rm 127.9.0.11[:8443] | rm <nr>")
                continue
            target = rest[0]
            if ":" not in target and not fleet.find(target):
                target = f"{target}:{args.port}"
            print("  entfernt" if fleet.stop(target) else "  nicht gefunden")
        elif cmd == "link" and len(rest) == 2:
            print("  ok" if fleet.set_link(int(rest[0]), rest[1] == "up") else "  nicht möglich (nur Netzlabor)")
        else:
            print("  unbekannt. Befehle: add | list | rm | link | quit")


def control_loop(fleet):
    """Steuerung über stdin für den Testlabor-Reiter von BMC-Steward (eine Zeile je Befehl)."""
    for line in sys.stdin:
        cmd, *rest = line.split() or [""]
        try:
            if cmd == "link" and len(rest) == 2:
                fleet.set_link(int(rest[0]), rest[1] == "up")
            elif cmd == "state":
                fleet.emit()
            elif cmd in ("quit", "exit"):
                return
        except Exception as e:
            print(f"  [steuerung] {line.strip()}: {e}", file=sys.stderr)


def make_emitter(fleet_ref, enabled):
    """@@STATE-Zeile mit dem Zustand aller Server (für die Übersicht im Testlabor-Reiter)."""
    def emit():
        if not enabled or not fleet_ref:
            return
        data = [b.state() for b in fleet_ref[0].table()]
        with _PRINT_LOCK:
            sys.stdout.write("@@STATE " + json.dumps(data, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return emit


def legacy_tls_ctx(cert, key):
    """TLS 1.0 wie ein iLO 3 - nur, wenn OpenSSL es auf diesem System noch zulässt."""
    try:
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        c.minimum_version = ssl.TLSVersion.TLSv1
        c.maximum_version = ssl.TLSVersion.TLSv1
        c.set_ciphers("DEFAULT:@SECLEVEL=0")
        c.load_cert_chain(cert, key)
        return c
    except (ssl.SSLError, ValueError, AttributeError):
        return None


def main():
    global netlab
    ap = argparse.ArgumentParser(
        description="Virtuelle BMC-Flotte (Dell/HPE/Lenovo/Supermicro) zum Testen von BMC-Steward.")
    ap.add_argument("specs", nargs="*", help="Server, z. B. dell  hpe@127.9.0.12  lenovo@127.9.0.13:8443")
    ap.add_argument("--preset", action="store_true", help="je ein Server pro Hersteller")
    ap.add_argument("--vendor", choices=VENDORS, help="mit --count: N Server eines Herstellers")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--config", help="Szenario (JSON): Liste von Servern oder {\"servers\": [...]}")
    ap.add_argument("--base", default="127.9.0", help="Basis für automatische 127.x-Adressen (Vorgabe 127.9.0)")
    ap.add_argument("--port", type=int, default=8443, help="Redfish-Port (Vorgabe 8443)")
    ap.add_argument("--console", action="store_true",
                    help="zusätzlich echtes SSH (22), IPMI-SOL (623) und Redfish auf 443 "
                         "(braucht paramiko+pyghmi und root/Rechte für Ports < 1024)")
    ap.add_argument("--netlab", choices=("rz", "router"),
                    help="Netzlabor (root): rz = isolierter Staging-Switch bmclab0, router = echtes LAN "
                         "über die Kabel-Netzwerkkarte (--parent)")
    ap.add_argument("--host-addr", action="append", default=[],
                    help="rz: Adresse(n) dieses Rechners am Staging-Switch, z. B. 192.168.0.1/24 "
                         "(mehrfach oder durch Komma getrennt)")
    ap.add_argument("--parent", help="router: Kabel-Netzwerkkarte, z. B. enp3s0")
    ap.add_argument("--state", action="store_true", help="Zustand als @@STATE-JSON-Zeilen ausgeben (GUI)")
    ap.add_argument("--control", action="store_true", help="Befehle zeilenweise über stdin (GUI)")
    ap.add_argument("--interactive", "-i", action="store_true", help="Steuerkonsole offen lassen")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config) if args.config else None
    except (OSError, ValueError) as e:
        sys.exit(f"Szenario nicht lesbar: {e}")
    mode = args.netlab or (cfg or {}).get("mode")
    if mode not in ("rz", "router"):
        mode = None
    raw = args.host_addr or (cfg or {}).get("host_addrs") or []
    if isinstance(raw, str):
        raw = [raw]
    host_addrs = [a.strip() for chunk in raw for a in str(chunk).split(",") if a.strip()]
    parent = args.parent or (cfg or {}).get("parent")

    try:
        bmcs = build_fleet(args, cfg)
    except (ValueError, KeyError) as e:
        sys.exit(f"Konfiguration fehlerhaft: {e}")
    if not bmcs and not args.interactive:
        ap.print_help()
        print("\nBeispiel:  python3 vbmc.py --preset")
        return

    cert, key = ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    legacy = legacy_tls_ctx(cert, key) if any(b.gen == "ilo3" for b in bmcs) else None
    if any(b.gen == "ilo3" for b in bmcs) and not legacy:
        print("Hinweis: TLS 1.0 ist auf diesem System abgeschaltet, iLO 3 läuft mit moderner "
              "Verschlüsselung.", file=sys.stderr)

    lab = None
    console = args.console
    if mode:
        try:
            import netlab as _netlab
        except ImportError as e:
            sys.exit(f"Netzlabor nicht verfügbar: {e}")
        netlab = _netlab
        lab = netlab.NetLab(mode, host_addrs or (["192.168.0.1/24"] if mode == "rz" else []),
                            parent=parent, log=print)
        try:
            lab.setup()
        except Exception as e:
            lab.teardown()
            sys.exit(f"Netzlabor konnte nicht starten: {e}")
        redfish_ports = [REDFISH_STD]
        console = True                          # root und eigene Adresse: Konsole auf Standardports
    else:
        redfish_ports = [args.port]
        if args.console and REDFISH_STD not in redfish_ports:
            redfish_ports.append(REDFISH_STD)     # Konsolen-Reiter prüft Redfish auf 443
    if console:
        miss = []
        if paramiko is None:
            miss.append("paramiko")
        if _ipmibmc is None:
            miss.append("pyghmi")
        if miss:
            print(f"Hinweis: für die Konsole fehlen Pakete: {', '.join(miss)} "
                  f"(sudo pacman -S python-paramiko; sudo pip install --break-system-packages pyghmi).",
                  file=sys.stderr)

    ref = []
    fleet = Fleet(ctx, redfish_ports, console=console, lab=lab,
                  emit=make_emitter(ref, args.state), legacy_ctx=legacy)
    ref.append(fleet)
    # SIGTERM (z. B. Stop-Knopf der GUI) wie Strg-C behandeln, damit die Aufräumroutine
    # unten läuft und auch die IPMI-Kindprozesse beendet werden.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    started = []
    try:
        for b in bmcs:
            try:
                fleet.start(b)
                started.append(b)
            except Exception as e:
                print(f"Start {NAME.get(b.vendor, b.vendor)} {b.addr or b.serial} fehlgeschlagen: {e}",
                      file=sys.stderr)
        if not started and not args.interactive:
            print("Kein Server gestartet.", file=sys.stderr)
            raise SystemExit(1)

        where = {"rz": "am Staging-Switch bmclab0", "router": f"im LAN an {parent}"}.get(
            mode, "(HTTPS, selbstsigniert)")
        print(f"vbmc {__version__} - {len(started)} virtuelle Server laufen {where}. In BMC-Steward eintragen:\n")
        print_table(fleet)
        if mode == "rz":
            print("\nDie Server holen sich ihre Adresse per DHCP: im Reiter 1 von BMC-Steward den DHCP-Server "
                  "auf bmclab0 starten (oder feste Werks-IP im Szenario).")
        elif mode == "router":
            print("\nDie Server holen sich ihre Adresse vom Router und erscheinen dort als eigene Geräte.")
        elif args.console:
            print("\nKonsole: im Reiter „UEFI-Konsole“ die reine IP (ohne :Port) eintragen; "
                  "Redfish-Konfig im Haupt-Reiter weiter als IP:PORT.")
        print("\nBeenden mit Strg-C." + ("" if (args.interactive or args.control) else "  (Steuerkonsole: --interactive)"))
        fleet.emit()

        if args.interactive:
            interactive(fleet, args)
        elif args.control:
            control_loop(fleet)
        else:
            while True:
                time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        print("\nbeende...")
        stoppers = [threading.Thread(target=fleet.stop, args=(b,)) for b in fleet.table()]
        for t in stoppers:                      # parallel: Leases freigeben, Dienste beenden
            t.start()
        for t in stoppers:
            t.join(10)
        if lab:
            lab.teardown()
        if _AUTH_DIR:
            import shutil
            shutil.rmtree(_AUTH_DIR, ignore_errors=True)
        fleet.emit()


if __name__ == "__main__":
    main()
