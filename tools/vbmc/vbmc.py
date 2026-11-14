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

Grundbetrieb braucht nur die Python-Standardbibliothek (openssl fürs Zertifikat).
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
__version__ = "1.1.0"
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

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "vbmc"

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
MODELS = {"dell": "PowerEdge R6625", "hpe": "ProLiant DL365 Gen11",
          "hpe_old": "ProLiant DL360p Gen8", "smc": "AS-2015HS-TNR",
          "lenovo": "ThinkSystem SR655 V3"}
FW = {"dell": "7.10.30.00", "hpe": "3.09", "hpe_old": "2.78", "smc": "01.05.02", "lenovo": "5.30"}
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
class BMC:
    def __init__(self, vendor, host, port, serial=None, model=None, user=None, password=None):
        self.vendor = vendor
        self.host, self.port = host, port
        self.console_host = host        # feste Adresse für SSH/IPMI (wandert bei Migration nicht)
        self.model = model or MODELS[vendor]
        self.serial = serial or self._gen_serial()
        fu, fp = FACTORY[vendor]
        self.factory_user = user or fu
        self.accounts = {self.factory_user: {"password": password or fp,
                                             "role": "Administrator", "enabled": True}}
        self.hostname = ""
        self.dhcp = True
        self.ipv4 = {"Address": host, "SubnetMask": "255.255.255.0", "Gateway": ""}
        self.power = "On"
        self.pending_ip = None
        self.attributes = dict(DELL_ATTRS) if vendor == "dell" else {}
        self.bios = dict(BIOS_ATTRS)
        self.extra = {}
        # Firmware-Inventar (Name -> {version, updateable}) und laufende Update-Tasks
        self.firmware = {
            "BMC": {"version": FW[vendor], "updateable": True,
                    "name": {"dell": "iDRAC", "hpe": "iLO 5", "hpe_old": "iLO 4",
                             "smc": "BMC", "lenovo": "XCC"}[vendor]},
            "BIOS": {"version": "1.8.2", "updateable": True, "name": "BIOS/UEFI"},
            "NIC.1": {"version": "22.5.9", "updateable": True, "name": "NIC Slot 1"},
            "PSU.1": {"version": "00.0A.0C", "updateable": False, "name": "Power Supply 1"},
        }
        self.tasks = {}                 # id -> {state, percent, target, newver}
        self._task_seq = 0
        self.lock = threading.Lock()
        self._redfish = []              # [(Server, thread)]
        self._ssh = None                # (socket, thread)
        self._ipmi = None               # FakeBMC-Instanz

    def _gen_serial(self):
        import random
        pre = {"dell": "CN", "hpe": "CZ", "hpe_old": "CZ", "smc": "S", "lenovo": "J"}[self.vendor]
        return pre + "".join(random.choice("0123456789ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(10))

    @property
    def addr(self):
        return f"{self.host}:{self.port}"

    def check_auth(self, user, pw):
        a = self.accounts.get(user)
        return bool(a) and a.get("enabled", True) and a.get("password") == pw


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


def _ipmi_process(vendor, host, port, serial, model, hostname, accounts):
    """Läuft im Kindprozess: ein einzelner IPMI-SOL-BMC."""
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

    shim = BMC(vendor, host, port, serial=serial, model=model)
    shim.hostname = hostname
    shim.accounts = accounts

    import queue as _queue

    class Fake(B.Bmc):
        def __init__(self):
            super().__init__({u: a["password"] for u, a in accounts.items()}, port=port, address=host)
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
            return "on"

        def power_on(self):
            pass

        def power_off(self):
            pass

        def power_cycle(self):
            pass

        def power_reset(self):
            pass

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


def start_ipmi(bmc):
    p = _MP.Process(
        target=_ipmi_process,
        args=(bmc.vendor, bmc.console_host, IPMI_PORT, bmc.serial, bmc.model,
              bmc.hostname, dict(bmc.accounts)),
        daemon=True)
    p.start()
    bmc._ipmi = p


# ------------------------------------------------------------------ Redfish-HTTP
class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, bmc, fleet):
        super().__init__(addr, Handler)
        self.bmc, self.fleet = bmc, fleet


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

    def authed(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return False
        return self.bmc.check_auth(user, pw)

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
        if p in ("/redfish/v1", "/redfish/v1/"):
            if b.vendor == "hpe_old":
                return self.send(404, rf_error("kein Redfish"))
            return self.send(200, self.root_doc())
        if p == "/xmldata":
            return self.xmldata()
        if p == "/":
            return self.landing()
        if not self.authed():
            return self.need_auth()
        mgr = f"/redfish/v1/Managers/{MGR[b.vendor]}"
        sysp = f"/redfish/v1/Systems/{SYS[b.vendor]}"
        if p == mgr:
            return self.send(200, self.manager_doc())
        if p == mgr + "/Attributes":
            return self.send(200, {"@odata.id": p, "Attributes": b.attributes})
        if p == mgr + "/DateTime":
            return self.send(200, {"@odata.id": p, "DateTime": time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                                   "StaticNTPServers": b.extra.get("ntp", [])})
        if p == mgr + "/NetworkProtocol":
            return self.send(200, {"@odata.id": p,
                                   "NTP": {"ProtocolEnabled": True, "NTPServers": b.extra.get("ntp", [])}})
        if p == f"{mgr}/EthernetInterfaces/{NIC.get(b.vendor, '1')}":
            return self.send(200, self.nic_doc(p))
        if p == "/redfish/v1/AccountService":
            return self.send(200, {"@odata.id": p, "ServiceEnabled": True,
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
        if not self.authed():
            return self.need_auth()
        mgr = f"/redfish/v1/Managers/{MGR[b.vendor]}"
        sysp = f"/redfish/v1/Systems/{SYS[b.vendor]}"
        if p == "/redfish/v1/AccountService/Accounts":
            return self.account_create(self.read_json())
        if p == mgr + "/Actions/Manager.Reset":
            self.read_raw()
            self.send(200, {"ok": True})
            if b.pending_ip:
                self.server.fleet.migrate(b, b.pending_ip)
                b.pending_ip = None
            return
        if p == sysp + "/Actions/ComputerSystem.Reset":
            self.read_raw()
            return self.send(200, {"ok": True})
        if "/Actions/" in p and ("Cert" in p or "Certificate" in p):
            self.read_raw()
            b.extra["cert"] = True
            return self.send(200, {"ok": True})
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
        mgr = f"/redfish/v1/Managers/{MGR[b.vendor]}"
        sysp = f"/redfish/v1/Systems/{SYS[b.vendor]}"
        if p == mgr + "/Attributes":
            return self.dell_attributes(data)
        if p == mgr + "/DateTime":
            b.extra["ntp"] = data.get("StaticNTPServers", [])
            return self.send(200, {"ok": True})
        if p == mgr + "/NetworkProtocol":
            if "NTP" in data:
                b.extra["ntp"] = data["NTP"].get("NTPServers", [])
            b.extra["syslog"] = True
            return self.send(200, {"ok": True})
        if p == "/redfish/v1/AccountService":
            b.extra["ldap"] = True
            return self.send(200, {"ok": True})
        if p == f"{mgr}/EthernetInterfaces/{NIC.get(b.vendor, '1')}":
            return self.nic_patch(data)
        m = re.match(r"^/redfish/v1/AccountService/Accounts/(.+)$", p)
        if m:
            return self.account_patch(m.group(1), data)
        if p == sysp + "/Bios/Settings":
            b.bios.update((data.get("Attributes") or {}))
            return self.send(200, {"ok": True})
        return self.send(404, rf_error("Nicht gefunden: " + p))

    # -- Dokumente ---------------------------------------------------------
    def root_doc(self):
        b = self.bmc
        oem = {"dell": {"Dell": {"@odata.type": "#DellService.v1_0_0.DellService"}},
               "hpe": {"Hpe": {"@odata.type": "#HpeiLO.v2_7_0.HpeiLO", "Manager": [{"ManagerType": "iLO 5"}]}},
               "smc": {"Supermicro": {"@odata.type": "#SmcService.v1_0_0.SmcService"}},
               "lenovo": {"Lenovo": {"@odata.type": "#LenovoService.v1_0_0.LenovoService"}}}[b.vendor]
        vf = {"dell": "Dell", "hpe": "HPE", "smc": "Supermicro", "lenovo": "Lenovo"}[b.vendor]
        return {"@odata.id": "/redfish/v1/", "RedfishVersion": "1.12.0", "Vendor": vf, "Oem": oem,
                "Managers": {"@odata.id": "/redfish/v1/Managers"},
                "Systems": {"@odata.id": "/redfish/v1/Systems"},
                "AccountService": {"@odata.id": "/redfish/v1/AccountService"},
                "UUID": "00000000-0000-0000-0000-" + b.serial[-12:].rjust(12, "0")}

    def manager_doc(self):
        b = self.bmc
        d = {"@odata.id": f"/redfish/v1/Managers/{MGR[b.vendor]}", "Id": MGR[b.vendor],
             "ManagerType": "BMC", "FirmwareVersion": FW[b.vendor], "Model": NAME[b.vendor],
             "PowerState": "On",
             "Actions": {"#Manager.Reset": {
                 "target": f"/redfish/v1/Managers/{MGR[b.vendor]}/Actions/Manager.Reset"}}}
        if b.vendor in NIC:
            d["EthernetInterfaces"] = {"@odata.id": f"/redfish/v1/Managers/{MGR[b.vendor]}/EthernetInterfaces"}
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
        members = [{"@odata.id": f"/redfish/v1/AccountService/Accounts/{i + 2}"}
                   for i in range(len(b.accounts))]
        return {"@odata.id": "/redfish/v1/AccountService/Accounts",
                "Members@odata.count": len(members), "Members": members}

    def system_doc(self, p):
        b = self.bmc
        return {"@odata.id": p, "Id": SYS[b.vendor], "Model": b.model,
                "Manufacturer": {"dell": "Dell Inc.", "hpe": "HPE", "smc": "Supermicro",
                                 "lenovo": "Lenovo"}[b.vendor],
                "SerialNumber": b.serial, "SKU": b.serial, "PowerState": b.power,
                "Actions": {"#ComputerSystem.Reset": {"target": p + "/Actions/ComputerSystem.Reset"}}}

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
        b = self.bmc
        if token in b.accounts:
            return token
        try:
            idx = int(token) - 2
            names = list(b.accounts.keys())
            if 0 <= idx < len(names):
                return names[idx]
        except ValueError:
            pass
        return None

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
            b.accounts[user] = {"password": data.get("Password", ""),
                                "role": data.get("RoleId", "Administrator"),
                                "enabled": data.get("Enabled", True)}
        return self.send(201, {"UserName": user})

    def account_patch(self, token, data):
        b = self.bmc
        name = self.resolve_account(token)
        with b.lock:
            if not name:
                user = data.get("UserName")
                if not user:
                    return self.send(400, rf_error("Konto nicht gefunden."))
                b.accounts[user] = {"password": data.get("Password", ""),
                                    "role": data.get("RoleId", "Administrator"),
                                    "enabled": data.get("Enabled", True)}
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
            self.server.fleet.migrate(b, attrs["IPv4Static.1.Address"])
            return
        return self.send(200, {"ok": True})

    def nic_patch(self, data):
        b = self.bmc
        host = data.get("HostName")
        if not host and isinstance(data.get("Oem"), dict):
            host = (data["Oem"].get("Hpe") or {}).get("HostName")
        if host:
            b.hostname = host
        if isinstance(data.get("DHCPv4"), dict) and "DHCPEnabled" in data["DHCPv4"]:
            b.dhcp = data["DHCPv4"]["DHCPEnabled"]
        if isinstance(data.get("Oem"), dict):
            for sub in data["Oem"].values():
                if isinstance(sub, dict) and isinstance(sub.get("DHCPv4"), dict):
                    b.dhcp = sub["DHCPv4"].get("Enabled", b.dhcp)
                if isinstance(sub, dict) and "DHCPEnabled" in sub:
                    b.dhcp = sub["DHCPEnabled"]
        addr = data.get("IPv4StaticAddresses") or data.get("IPv4Addresses")
        if addr and isinstance(addr, list) and addr[0].get("Address"):
            b.dhcp = False
            new = addr[0]["Address"]
            if b.vendor == "hpe":
                b.pending_ip = new
                return self.send(200, {"ok": True})
            self.send(200, {"ok": True})
            self.server.fleet.migrate(b, new)
            return
        return self.send(200, {"ok": True})

    # -- iLO 3/4: xmldata + RIBCL ------------------------------------------
    def xmldata(self):
        b = self.bmc
        if b.vendor != "hpe_old":
            return self.send(404, rf_error("kein xmldata"))
        xml = (f'<?xml version="1.0"?>\r\n<RIMP><HSI><SPN>{b.model}</SPN></HSI>'
               f'<MP><PN>Integrated Lights-Out 4 (iLO 4)</PN><FWRI>{FW["hpe_old"]}</FWRI>'
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
            parts.append(f'<GET_FW_VERSION FIRMWARE_VERSION="{FW["hpe_old"]}" MANAGEMENT_PROCESSOR="iLO4"/>\r\n'
                         + self._ribcl_resp(0x0000, "No error"))
        if "GET_ALL_USERS" in raw:
            users = "".join(f'<USER_LOGIN VALUE="{u}"/>' for u in b.accounts)
            parts.append(f"<GET_ALL_USERS>{users}</GET_ALL_USERS>\r\n" + self._ribcl_resp(0x0000, "No error"))
        if "ADD_USER" in raw:
            seg = raw[raw.find("ADD_USER"):]
            nu = re.search(r'USER_LOGIN\s*=\s*"([^"]*)"', seg)
            np_ = re.search(r'PASSWORD\s*=\s*"([^"]*)"', seg)
            if nu:
                b.accounts[nu.group(1)] = {"password": np_.group(1) if np_ else "",
                                           "role": "Administrator", "enabled": True}
            parts.append(self._ribcl_resp(0x0000, "No error"))
        if "MOD_USER" in raw:
            seg = raw[raw.find("MOD_USER"):]
            nu = re.search(r'USER_LOGIN\s*=\s*"([^"]*)"', seg)
            np_ = re.search(r'PASSWORD\s+value\s*=\s*"([^"]*)"', seg)
            if nu and nu.group(1) in b.accounts and np_:
                b.accounts[nu.group(1)]["password"] = np_.group(1)
            parts.append(self._ribcl_resp(0x0000, "No error"))
        new_ip = None
        if "MOD_NETWORK_SETTINGS" in raw:
            seg = raw[raw.find("MOD_NETWORK_SETTINGS"):]
            mi = re.search(r'IP_ADDRESS\s+value\s*=\s*"([^"]*)"', seg)
            mn = re.search(r'DNS_NAME\s+value\s*=\s*"([^"]*)"', seg)
            if mn:
                b.hostname = mn.group(1)
            if mi:
                new_ip, b.dhcp = mi.group(1), False
            parts.append(self._ribcl_resp(0x0000, "No error"))
        self.send_text(200, "".join(parts), "text/xml")
        if new_ip:
            self.server.fleet.migrate(b, new_ip)

    @staticmethod
    def _ribcl_resp(status, message):
        return ('<?xml version="1.0"?>\r\n<RIBCL VERSION="2.22">\r\n'
                f'<RESPONSE STATUS="0x{status:04X}" MESSAGE="{message}"/>\r\n</RIBCL>\r\n')

    def landing(self):
        b = self.bmc
        self.send_text(200,
            f"<!doctype html><meta charset=utf-8><title>vbmc {b.addr}</title>"
            f"<body style='font-family:system-ui;background:#111;color:#ddd;padding:2rem'>"
            f"<h1>Virtueller BMC</h1><p>Hersteller: <b>{NAME[b.vendor]}</b></p>"
            f"<p>Modell: {b.model}<br>Seriennummer: {b.serial}<br>Firmware: {FW[b.vendor]}</p>"
            f"<p>Adresse: {b.addr}<br>Hostname: {b.hostname or '(nicht gesetzt)'}<br>"
            f"DHCP: {'an' if b.dhcp else 'aus'}</p>"
            f"<p style='color:#888'>Reines Test-/Demo-Gerät für BMC-Steward.</p></body>",
            "text/html; charset=utf-8")


# ------------------------------------------------------------------ Flotte
class Fleet:
    def __init__(self, ctx, redfish_ports, console=False):
        self.ctx = ctx
        self.redfish_ports = redfish_ports      # z. B. [8443] oder [8443, 443]
        self.console = console
        self.bmcs = {}                          # addr -> BMC
        self.lock = threading.Lock()

    def _bind_redfish(self, bmc):
        bmc._redfish = []
        for port in self.redfish_ports:
            try:
                srv = Server((bmc.host, port), bmc, self)
            except PermissionError:
                print(f"  [redfish] {bmc.host}:{port} nicht gebunden (Rechte für Port < 1024). "
                      f"Konsolen-Reiter braucht Redfish auf 443 -> mit sudo starten.", file=sys.stderr)
                continue
            except OSError as e:
                print(f"  [redfish] {bmc.host}:{port} nicht gebunden ({e.errno}).", file=sys.stderr)
                continue
            srv.socket = self.ctx.wrap_socket(srv.socket, server_side=True)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            bmc._redfish.append(srv)
        if not bmc._redfish:
            raise OSError(f"keine Redfish-Ports gebunden für {bmc.host}")

    def _unbind_redfish(self, bmc):
        for srv in bmc._redfish:
            srv.shutdown()
            srv.server_close()
        bmc._redfish = []

    def start(self, bmc):
        with self.lock:
            if bmc.addr in self.bmcs:
                raise ValueError(f"Adresse {bmc.addr} läuft bereits")
            self._bind_redfish(bmc)
            if self.console:
                self._start_console(bmc)
            self.bmcs[bmc.addr] = bmc
            return bmc

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
            print(f"  [console] {bmc.console_host}: nicht gestartet: {', '.join(notes)}", file=sys.stderr)

    def stop(self, addr):
        with self.lock:
            bmc = self.bmcs.pop(addr, None)
        if not bmc:
            return False
        self._unbind_redfish(bmc)
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
        return True

    def migrate(self, bmc, new_addr):
        """Nur die Redfish-Adresse wandert; SSH/IPMI bleiben auf der Startadresse."""
        host, _, port = new_addr.partition(":")
        port = int(port or bmc.port)
        old = bmc.addr

        def do():
            time.sleep(0.6)
            with self.lock:
                self.bmcs.pop(old, None)
                self._unbind_redfish(bmc)
                bmc.host, bmc.port = host, port
                bmc.ipv4["Address"] = host
                try:
                    self._bind_redfish(bmc)
                    self.bmcs[bmc.addr] = bmc
                except Exception as e:
                    print(f"  [migrate] {old} -> {new_addr} fehlgeschlagen: {e}", file=sys.stderr)
                    return
            print(f"  [migrate] {old} -> {bmc.addr} ({NAME[bmc.vendor]})")
        threading.Thread(target=do, daemon=True).start()

    def table(self):
        with self.lock:
            return sorted(self.bmcs.values(), key=lambda b: b.addr)


# ------------------------------------------------------------------ CLI
def auto_host(index, base):
    return f"{base}.{11 + index}"


def parse_spec(spec, index, base, port):
    vendor, _, where = spec.partition("@")
    vendor = vendor.strip().lower()
    aliases = {"hp": "hpe", "ilo": "hpe", "idrac": "dell", "xcc": "lenovo",
               "supermicro": "smc", "ipmi": "smc", "hpe4": "hpe_old", "ilo4": "hpe_old", "ilo3": "hpe_old"}
    vendor = aliases.get(vendor, vendor)
    if vendor not in VENDORS:
        raise ValueError(f"unbekannter Hersteller: {spec} (erlaubt: {', '.join(VENDORS)})")
    if where:
        host, _, p = where.partition(":")
        port = int(p or port)
    else:
        host = auto_host(index, base)
    return BMC(vendor, host, port)


def build_fleet(args):
    bmcs, idx = [], 0
    if args.config:
        for e in json.loads(Path(args.config).read_text()):
            bmcs.append(BMC(e["vendor"], e.get("host") or auto_host(idx, args.base),
                            int(e.get("port", args.port)), serial=e.get("serial"), model=e.get("model"),
                            user=e.get("user"), password=e.get("password")))
            idx += 1
    if args.preset:
        for v in ("dell", "hpe", "lenovo", "smc"):
            bmcs.append(BMC(v, auto_host(idx, args.base), args.port))
            idx += 1
    if args.vendor:
        for _ in range(args.count):
            bmcs.append(BMC(args.vendor, auto_host(idx, args.base), args.port))
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
    w = max(len(b.addr) for b in bmcs)
    extra = "  SSH/IPMI (Konsole)" if fleet.console else ""
    print(f"  {'Redfish-Adresse'.ljust(w)}  Hersteller            Werks-Login{extra}")
    print(f"  {'-'*w}  --------------------  -------------------------"
          + ("  ------------------" if fleet.console else ""))
    for b in bmcs:
        u = b.factory_user
        pw = b.accounts[u]["password"] if u in b.accounts else "?"
        con = f"  {b.console_host} (22/623)" if fleet.console else ""
        print(f"  {b.addr.ljust(w)}  {NAME[b.vendor].ljust(20)}  {u} / {pw}{con}")


def interactive(fleet, args):
    print("\nKonsole: add <hersteller> [host[:port]] | list | rm <adresse> | quit")
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
                b = parse_spec(spec, len(fleet.table()), args.base, args.port)
                fleet.start(b)
                print(f"  gestartet: {b.addr}  {NAME[b.vendor]}  Login {b.factory_user} / "
                      f"{b.accounts[b.factory_user]['password']}")
            except Exception as e:
                print(f"  Fehler: {e}")
        elif cmd in ("rm", "del", "stop"):
            if not rest:
                print("  Nutzung: rm 127.9.0.11[:8443]")
                continue
            target = rest[0]
            if ":" not in target:
                cand = [b.addr for b in fleet.table() if b.host == target]
                target = cand[0] if cand else f"{target}:{args.port}"
            print("  entfernt" if fleet.stop(target) else "  nicht gefunden")
        else:
            print("  unbekannt. Befehle: add | list | rm | quit")


def main():
    ap = argparse.ArgumentParser(
        description="Virtuelle BMC-Flotte (Dell/HPE/Lenovo/Supermicro) zum Testen von BMC-Steward.")
    ap.add_argument("specs", nargs="*", help="Server, z. B. dell  hpe@127.9.0.12  lenovo@127.9.0.13:8443")
    ap.add_argument("--preset", action="store_true", help="je ein Server pro Hersteller")
    ap.add_argument("--vendor", choices=VENDORS, help="mit --count: N Server eines Herstellers")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--config", help="JSON-Datei mit einer Liste von Servern")
    ap.add_argument("--base", default="127.9.0", help="Basis für automatische 127.x-Adressen (Vorgabe 127.9.0)")
    ap.add_argument("--port", type=int, default=8443, help="Redfish-Port (Vorgabe 8443)")
    ap.add_argument("--console", action="store_true",
                    help="zusätzlich echtes SSH (22), IPMI-SOL (623) und Redfish auf 443 "
                         "(braucht paramiko+pyghmi und root/Rechte für Ports < 1024)")
    ap.add_argument("--interactive", "-i", action="store_true", help="Steuerkonsole offen lassen")
    args = ap.parse_args()

    bmcs = build_fleet(args)
    if not bmcs and not args.interactive:
        ap.print_help()
        print("\nBeispiel:  python3 vbmc.py --preset")
        return

    cert, key = ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)

    redfish_ports = [args.port]
    if args.console and REDFISH_STD not in redfish_ports:
        redfish_ports.append(REDFISH_STD)     # Konsolen-Reiter prüft Redfish auf 443
    if args.console:
        miss = []
        if paramiko is None:
            miss.append("paramiko")
        if _ipmibmc is None:
            miss.append("pyghmi")
        if miss:
            print(f"Hinweis: für --console fehlen Pakete: {', '.join(miss)} "
                  f"(z. B. sudo pacman -S python-paramiko python-pyghmi).", file=sys.stderr)

    fleet = Fleet(ctx, redfish_ports, console=args.console)
    started = []
    for b in bmcs:
        try:
            fleet.start(b)
            started.append(b)
        except Exception as e:
            print(f"Start {b.addr} fehlgeschlagen: {e}", file=sys.stderr)
    if not started and not args.interactive:
        sys.exit("Kein Server gestartet.")

    print(f"vbmc {__version__} - {len(started)} virtuelle Server laufen "
          f"(HTTPS, selbstsigniert). In BMC-Steward eintragen:\n")
    print_table(fleet)
    if args.console:
        print("\nKonsole: im Reiter „UEFI-Konsole“ die reine IP (ohne :Port) eintragen; "
              "Redfish-Konfig im Haupt-Reiter weiter als IP:PORT.")
    print("\nBeenden mit Strg-C." + ("" if args.interactive else "  (Steuerkonsole: --interactive)"))

    # SIGTERM (z. B. Stop-Knopf der GUI) wie Strg-C behandeln, damit die Aufräumroutine
    # unten läuft und auch die IPMI-Kindprozesse beendet werden.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        if args.interactive:
            interactive(fleet, args)
        else:
            while True:
                time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    print("\nbeende...")
    for b in fleet.table():
        fleet.stop(b.addr)


if __name__ == "__main__":
    main()
