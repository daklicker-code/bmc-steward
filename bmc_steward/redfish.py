"""BMC-Steward: Redfish/RIBCL-Client, Hersteller-Erkennung, Konfigurations-Engines und ihre Threads."""
from PyQt6.QtCore import (QT_VERSION_STR, QBuffer, QByteArray, QEvent, QIODevice, QLocale, QObject, QProcess, QRect, QSocketNotifier, QThread, QTimer, QUrl, Qt, pyqtSignal)
import base64
import errno
import functools
import hashlib
import secrets
from xml.sax.saxutils import escape
import http.client
import json
import ipaddress
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path
from . import i18n
from .i18n import T
from .core import mask
from . import selfguard, trust, audit




# ======================================================================
# Redfish-Ablauf (reines Python, läuft unter Linux und Windows)
# ======================================================================
TLS = ssl.create_default_context()


TLS.check_hostname = False


TLS.verify_mode = ssl.CERT_NONE          # BMCs haben ab Werk selbstsignierte Zertifikate




def old_tls(highest=None):
    """TLS-Einstellung für alte BMCs: TLS 1.0/1.1 und alte Chiffren zulassen. iLO 3 kann höchstens
    TLS 1.1, frühe Firmware bricht schon ab, wenn TLS 1.2 nur angeboten wird."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")         # Python warnt vor TLS 1.0; hier ist es Absicht
        ctx.minimum_version = ssl.TLSVersion.TLSv1
        if highest:
            ctx.maximum_version = highest
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return ctx




# Stufen von neu nach alt; welche ein Gerät braucht, ermittelt RedfishClient.tls_mode()
CONTEXTS = {"modern": TLS, "old": old_tls(), "oldest": old_tls(ssl.TLSVersion.TLSv1_1)}


# BMCs immer direkt ansprechen, nie über einen (System-)Proxy
OPENERS = {name: urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPSHandler(context=ctx))
           for name, ctx in CONTEXTS.items()}




def hostport(ip):
    host, _, port = ip.partition(":")
    return host, int(port or 443)


def addr_key(ip):
    """Einheitliche Schreibweise einer Adresse: ohne :443, sonst mit Port."""
    host, port = hostport(str(ip))
    return host if port == 443 else f"{host}:{port}"


def fingerprint(der):
    return hashlib.sha256(der or b"").hexdigest()


class PinMismatch(ssl.SSLError):
    """Das Gerät zeigt ein anderes Zertifikat als vorher: Verbindung wird abgebrochen, bevor etwas gesendet wird."""


class PinnedConnection(http.client.HTTPSConnection):
    """HTTPS-Verbindung, die nach dem Handshake den Zertifikats-Fingerabdruck prüfen lässt (checker)."""

    def __init__(self, host, *args, checker=None, **kw):
        super().__init__(host, *args, **kw)
        self.checker = checker

    def connect(self):
        super().connect()
        if self.checker:
            try:
                self.checker(addr_key(f"{self.host}:{self.port}"), self.sock.getpeercert(binary_form=True))
            except PinMismatch:
                self.sock.close()
                raise


class PinnedHandler(urllib.request.HTTPSHandler):
    def __init__(self, context, checker):
        super().__init__(context=context)
        self.ctx, self.checker = context, checker

    def https_open(self, req):
        return self.do_open(functools.partial(PinnedConnection, checker=self.checker), req, context=self.ctx)


# Aufrufe, nach denen ein BMC sein Zertifikat erneuern darf (Hostname, Adresse, Neustart, Zertifikat-Import)
CERT_CHANGING = re.compile(r"cert|reset|hostname|dnsracname|dns_name|ipv4|dhcp|mod_network|reset_rib", re.I)




def xq(value):
    """Wert als XML-Attribut in doppelten Anführungszeichen (für RIBCL)."""
    return '"' + escape(str(value), {'"': "&quot;"}) + '"'




SECRET_KEYS = ("password", "passwd", "passphrase", "secret", "community", "token")   # als *** zeigen


def secret_key(key):
    """True für Schlüssel, deren Wert nie im Protokoll stehen darf (auch z. B. Dells „Users.3.Password“)."""
    low = str(key).lower()
    return any(s in low for s in SECRET_KEYS)


CERT_KEYS = ("sslcertificatefile", "certificate", "certificatestring")   # langen Zertifikatstext kürzen




def scrub(data):
    """JSON für das Trockenlauf-Protokoll, Passwörter maskiert und Zertifikatsinhalte gekürzt (auch
    tief verschachtelt, z. B. das Bind-Passwort bei LDAP)."""
    def walk(o):
        if isinstance(o, dict):
            return {k: ("***" if secret_key(k) else "<cert>" if k.lower() in CERT_KEYS else walk(v))
                    for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return "" if data is None else json.dumps(walk(data), separators=(",", ":"), ensure_ascii=False)





class RedfishClient:
    """Gemeinsamer Unterbau: HTTP/Redfish-Zugriff und Hersteller-Erkennung, ohne eigenen Ablauf."""

    DEFAULT_USER = {"dell": "root", "hpe": "Administrator", "hpe_old": "Administrator", "smc": "ADMIN",
                    "lenovo": "USERID"}
    NAME = {"dell": "Dell (iDRAC)", "hpe": "HPE (iLO)", "hpe_old": "HPE (iLO 3/4)", "smc": "Supermicro",
            "lenovo": "Lenovo (XCC)"}
    SYS_ID = {"dell": "System.Embedded.1", "hpe": "1", "smc": "1", "lenovo": "1"}

    def __init__(self, dry=True, log=print, stopped=lambda: False):
        self.dry, self.log, self.stopped = dry, log, stopped
        self.cur_user = ""
        self.code, self.body = 0, None
        self.resp_headers = {}  # Kopfzeilen der letzten Antwort (kleingeschrieben), z. B. "allow"
        self.tls = {}           # Adresse -> TLS-Stufe, gilt nur für diesen Lauf
        self.model = ""         # was vendor() über ein altes Gerät herausgefunden hat, z. B. "iLO 3"
        self.xml = ""           # letzte RIBCL-Antwort
        self.fp = {}            # Adresse -> Zertifikats-Fingerabdruck in diesem Lauf
        self.keys = {}          # Adresse -> Geräte-Kennung (Redfish-UUID / Seriennummer)
        self.expect = set()     # Geräte, an denen dieser Lauf selbst etwas Zertifikat-Relevantes geändert hat
        self.pin_err = {}       # Adresse -> Grund, warum dem Gerät nicht vertraut wird
        self.sessions = {}      # (Adresse, Benutzer) -> (Token, Session-Pfad)
        self.basic_only = set()  # Adressen ohne nutzbaren SessionService
        self.denied = {}        # (Adresse, Benutzer) -> HTTP-Code einer abgelehnten Anmeldung (kein 2. Versuch)
        self.use_sessions = True
        self.run_id = secrets.token_hex(4)
        self.audit_err = False
        self._openers = {}

    def opener(self, mode):
        """urllib-Opener ohne Proxy, der jedes Zertifikat gegen den Fingerabdruck dieses Laufs prüft."""
        if mode not in self._openers:
            self._openers[mode] = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                              PinnedHandler(CONTEXTS[mode], self.pin_check))
        return self._openers[mode]

    # ---------- Zertifikats-Vertrauen ----------
    def pin_check(self, addr, der):
        """Wird bei jedem TLS-Handshake aufgerufen. Wirft PinMismatch, wenn sich das Zertifikat in diesem Lauf
        ändert, ohne dass BMC-Steward selbst etwas dafür getan hat."""
        fp = fingerprint(der)
        known = self.fp.get(addr)
        if known is None or known == fp:
            self.fp[addr] = fp
            return
        key = self.keys.get(addr)
        if key and key in self.expect:
            self.fp[addr] = fp
            trust.store().check(key, fp, addr, accept_change=True)
            self.log(T("  Zertifikat nach eigener Änderung erneuert, neu gemerkt (SHA-256 {0})").format(trust.short(fp)))
            return
        msg = T("Zertifikat hat sich während des Laufs geändert (vorher {0}, jetzt {1})").format(
            trust.short(known), trust.short(fp))
        self.pin_err[addr] = msg
        raise PinMismatch(msg)

    def device_key(self, ip, mode):
        """Kennung des Geräts ohne Anmeldung: UUID aus /redfish/v1, bei iLO 3/4 die Seriennummer aus /xmldata."""
        try:
            with self.opener(mode).open(f"https://{ip}/redfish/v1/", timeout=10) as r:
                root = json.loads(r.read(200000) or b"{}")
            u = str(root.get("UUID") or "").strip() if isinstance(root, dict) else ""
            if u and u.strip("0-") and u.lower() != "ffffffff-ffff-ffff-ffff-ffffffffffff":
                return "uuid:" + u.lower()
        except PinMismatch:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError, AttributeError):
            pass
        try:
            with self.opener(mode).open(f"https://{ip}/xmldata?item=All", timeout=10) as r:
                text = r.read(20000).decode("utf-8", "replace")
            m = re.search(r"<SBSN>\s*([^<\s][^<]*?)\s*</SBSN>", text)
            if m:
                return "sn:" + m.group(1).upper()
        except PinMismatch:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass
        return "addr:" + addr_key(ip)

    def trust_device(self, ip, mode):
        """Vergleicht das Zertifikat mit dem gemerkten Fingerabdruck dieses Geräts (vor jeder Anmeldung).
        True = vertrauenswürdig."""
        addr = addr_key(ip)
        key = self.device_key(ip, mode)
        self.keys[addr] = key
        fp = self.fp.get(addr)
        if not fp:
            return True
        state, old = trust.store().check(key, fp, addr, accept_change=key in self.expect)
        if state == "new":
            self.log(T("  Neues Gerät: Zertifikat gemerkt (SHA-256 {0})").format(trust.short(fp)))
        elif state == "renewed":
            self.log(T("  Zertifikat nach eigener Änderung erneuert, neu gemerkt (SHA-256 {0})").format(trust.short(fp)))
        elif state == "changed":
            msg = T("Zertifikat dieses Geräts hat sich geändert (gemerkt {0}, jetzt {1}). Möglicher Angriff "
                    "(Man-in-the-Middle) – es wurde kein Passwort gesendet. War der Wechsel gewollt (neues "
                    "Zertifikat, Mainboard-Tausch, Werksreset), den Eintrag unter „Zertifikate“ (links unten) "
                    "entfernen.").format(trust.short(old), trust.short(fp))
            self.pin_err[addr] = msg
            self.log("  " + T("GESPERRT: ") + msg, "err")
            return False
        return True

    def note_change(self, ip, text):
        """Nach eigenen Änderungen, die das Zertifikat erneuern können: Wechsel für dieses Gerät zulassen."""
        if CERT_CHANGING.search(text or ""):
            key = self.keys.get(addr_key(ip))
            if key:
                self.expect.add(key)

    def audit(self, ip, method, path, code, data):
        """Revisionsprotokoll für ändernde Aufrufe im scharfen Lauf."""
        if self.dry or method == "GET":
            return
        err = audit.write({"run": self.run_id, "tool": type(self).__name__, "target": addr_key(ip),
                           "device": self.keys.get(addr_key(ip), ""), "method": method, "path": path,
                           "status": code, "body": data if isinstance(data, str) else scrub(data)})
        if err and not self.audit_err:
            self.audit_err = True
            self.log(T("  Warnung: Revisionsprotokoll nicht schreibbar ({0})").format(err))

    # ---------- Sessions ----------
    def auth_header(self, ip, user, pw, mode, renew=False):
        """(Header-Name, Wert) für die Anmeldung, oder (None, HTTP-Code), wenn die Anmeldung abgelehnt wurde.
        Bevorzugt eine Redfish-Session (Passwort geht einmal über die Leitung, BMC-Protokoll bleibt
        übersichtlich); kennt der BMC keine Sessions, wird Basic Auth genutzt. Eine abgelehnte Anmeldung wird
        nie mit Basic Auth wiederholt, damit kein Konto gesperrt wird."""
        addr = addr_key(ip)
        basic = ("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
        if not self.use_sessions or addr in self.basic_only:
            return basic
        sk = (addr, user)
        if sk in self.denied:
            return (None, self.denied[sk])
        if sk in self.sessions and not renew:
            return ("X-Auth-Token", self.sessions[sk][0])
        self.sessions.pop(sk, None)
        req = urllib.request.Request(f"https://{ip}/redfish/v1/SessionService/Sessions", method="POST",
                                     data=json.dumps({"UserName": user, "Password": pw}).encode())
        req.add_header("Content-Type", "application/json")
        try:
            with self.opener(mode).open(req, timeout=20) as r:
                token, loc, raw = r.headers.get("X-Auth-Token"), r.headers.get("Location"), r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            if e.code in (401, 403):
                try:
                    self.body = json.loads(raw) if raw else None
                except ValueError:
                    self.body = None
                self.denied[sk] = e.code
                return (None, e.code)
            self.basic_only.add(addr)
            return basic
        except PinMismatch:
            return (None, 0)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            return (None, 0)
        if not token:
            self.basic_only.add(addr)
            return basic
        if loc and loc.startswith("http"):
            loc = urllib.parse.urlsplit(loc).path
        if not loc:
            try:
                loc = (json.loads(raw) or {}).get("@odata.id")
            except (ValueError, AttributeError):
                loc = None
        self.sessions[sk] = (token, loc)
        return ("X-Auth-Token", token)

    def close_sessions(self):
        """Meldet alle Sessions dieses Laufs wieder ab (BMCs erlauben nur wenige gleichzeitig)."""
        for (addr, _user), (token, loc) in list(self.sessions.items()):
            if not loc:
                continue
            mode = self.tls.get(addr) or self.tls.get(addr + ":443") or "modern"
            req = urllib.request.Request(f"https://{addr}{loc}", method="DELETE")
            req.add_header("X-Auth-Token", token)
            try:
                with self.opener(mode).open(req, timeout=5) as r:
                    r.read()
            except Exception:
                pass
        self.sessions.clear()

    def __init_subclass__(cls, **kw):
        """Jeder Ablauf (run) meldet am Ende seine Sessions ab, auch bei Abbruch oder Fehler."""
        super().__init_subclass__(**kw)
        if "run" in cls.__dict__:
            orig = cls.run

            @functools.wraps(orig)
            def run(self, *a, **k):
                try:
                    return orig(self, *a, **k)
                finally:
                    self.close_sessions()
            cls.run = run

    def sys_path(self, vendor):
        return f"/redfish/v1/Systems/{self.SYS_ID[vendor]}"

    def pause(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.stopped():
            time.sleep(0.2)

    # ---------- Sicherung vor Änderungen ----------
    MGR_PATH = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1"}        # sonst /redfish/v1/Managers/1

    # Dediziertes Management-Netzwerkinterface je Hersteller (für DNS/VLAN/IPv6, siehe ServicesEngine weiter
    # unten). Nur Dell ist echt geprüft (iDRAC9); die übrigen Pfade folgen dem gleichen Namensschema wie die
    # Neuware-Einrichtung (setup_hpe/setup_smc/setup_lenovo), sind aber auf echter Hardware ungeprüft – deshalb
    # läuft alles darüber über soft() (404/405/501 = "nicht unterstützt", kein Fehler).
    ETH_PATH = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1",
                "hpe": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "smc": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "lenovo": "/redfish/v1/Managers/1/EthernetInterfaces/NIC"}

    def backup(self, ip, user, pw, vendor, reason):
        """Liest vor einer Änderung die wichtigsten Einstellungen (nur GET) und legt sie als JSON ab:
        <Datenordner>/sicherungen/<Seriennummer>/<Zeit>-<Anlass>.json. Grundlage zum Nachsehen und
        Zurückstellen. Ein Fehler hier bricht den Lauf nicht ab. Gibt den Dateipfad oder None zurück."""
        if self.dry or vendor not in self.SYS_ID:
            return None
        mgr = self.MGR_PATH.get(vendor, "/redfish/v1/Managers/1")
        sysp = self.sys_path(vendor)
        data, budget = {}, [80]

        def get(path, members=0):
            if budget[0] <= 0 or self.stopped():
                return None
            budget[0] -= 1
            code = self.api(ip, user, pw, "GET", path, timeout=20)
            if code in (401, 403) and not data:
                raise PermissionError(code)
            if not self.ok(code) or not isinstance(self.body, dict):
                return None
            doc = self.body
            data[path] = doc
            for m in (doc.get("Members") or [])[:members]:
                sub = m.get("@odata.id") if isinstance(m, dict) else None
                if sub:
                    get(sub)
            return doc

        try:
            system = get(sysp) or {}
            for path, members in ((sysp + "/Bios", 0), (mgr, 0), (mgr + "/NetworkProtocol", 0),
                                  (mgr + "/EthernetInterfaces", 4), ("/redfish/v1/AccountService", 0),
                                  ("/redfish/v1/AccountService/Accounts", 16), ("/redfish/v1/SessionService", 0),
                                  ("/redfish/v1/UpdateService/FirmwareInventory", 40)):
                get(path, members)
            if vendor == "dell":
                get(mgr + "/Attributes")
        except PermissionError:
            return None
        if not data:
            self.log(T("  Warnung: Sicherung nicht möglich (nichts lesbar), es geht ohne weiter."))
            return None
        ident = str(system.get("SKU") if vendor == "dell" and system.get("SKU") else system.get("SerialNumber") or "")
        folder = re.sub(r"[^A-Za-z0-9._-]+", "_", ident.strip() or addr_key(ip)).strip("_") or "unbekannt"
        from .core import __version__
        doc = {"meta": {"address": addr_key(ip), "device": self.keys.get(addr_key(ip), ""), "vendor": vendor,
                        "reason": reason, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "version": __version__,
                        "run": self.run_id}, "resources": data}
        path = i18n.DATA / "sicherungen" / folder / (time.strftime("%Y%m%d-%H%M%S-") + reason + ".json")
        try:
            i18n.write_private(path, json.dumps(doc, indent=1, ensure_ascii=False))
        except OSError as e:
            self.log(T("  Warnung: Sicherung nicht schreibbar ({0}), es geht ohne weiter.").format(e))
            return None
        self.log(T("  Einstellungen gesichert: {0}").format(path), "ok")
        return path

    # ---------- Eigenschutz ----------
    def self_guard(self, ip, user, pw, vendor, guard=None):
        """True = dieser Server darf geändert/neu gestartet werden. Liest dazu die Identität des Zielservers
        (nur GET, auch im Trockenlauf) und vergleicht sie mit der Schutzliste und der eigenen Kennung
        (siehe selfguard.py). Im scharfen Lauf gilt: Identität nicht lesbar = überspringen."""
        g = guard or selfguard.get()
        hit = g.match_ip(ip)
        if hit:
            self.log(T("  GESPERRT (Eigenschutz): Adresse {0} steht in der Schutzliste. Nichts geändert.").format(hit),
                     "err")
            return False
        if not g.armed and not g.virt:
            return True
        if self.dry and ip not in self.tls:
            self.log(T("  Eigenschutz: Identität im Trockenlauf nicht geprüft (Gerät nicht erreichbar)."))
            return True
        dry, self.dry = self.dry, False
        try:
            code = self.api(ip, user, pw, "GET", self.sys_path(vendor))
        finally:
            self.dry = dry
        body = self.body if self.ok(code) and isinstance(self.body, dict) else None
        if body is None:
            if self.dry:
                self.log(T("  Eigenschutz: Identität nicht lesbar; im scharfen Lauf würde dieser Server übersprungen."))
                return True
            self.log(T("  GESPERRT (Eigenschutz): Identität des Servers nicht lesbar, aus Sicherheitsgründen "
                       "übersprungen."), "err")
            return False
        hit = g.match(body)
        if hit:
            self.log(T("  GESPERRT (Eigenschutz): {0} ist der Rechner, auf dem BMC-Steward läuft (Treffer: {1}). "
                       "Nichts geändert.").format(g.identity(body) or ip, hit), "err")
            return False
        if g.unprotected_vm:
            self.log(T("  Warnung: BMC-Steward läuft in einer VM ({0}), die Schutzliste ist leer. Der eigene Host "
                       "wird nicht erkannt.").format(g.virt))
        return True

    # ---------- TLS ----------
    def tls_mode(self, ip, timeout=10):
        """TLS-Stufe des Geräts: modern | old | oldest, None = nicht erreichbar oder nicht vertrauenswürdig.
        Alte BMCs (iLO 3, iDRAC7/8, alte Firmware) können nur TLS 1.0/1.1; ermittelt wird das einmal je Gerät
        mit einem reinen Verbindungsaufbau. Danach wird das Zertifikat mit dem gemerkten Fingerabdruck des
        Geräts verglichen (trust.py) – vor jeder Anmeldung."""
        addr = addr_key(ip)
        if addr in self.pin_err:
            return None
        if ip in self.tls:
            return self.tls[ip]
        for name in ("modern", "old", "oldest"):
            try:
                with socket.create_connection(hostport(ip), timeout=timeout) as raw:
                    with CONTEXTS[name].wrap_socket(raw) as tls:
                        self.pin_check(addr, tls.getpeercert(binary_form=True))
                self.tls[ip] = name
                break
            except PinMismatch as e:
                self.log("  " + T("GESPERRT: ") + str(e.args[0] if e.args else e), "err")
                return None
            except (ssl.SSLError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                continue                # Handshake abgelehnt: die nächste, ältere Stufe versuchen
            except OSError:
                return None
        else:
            return None
        try:
            if not self.trust_device(ip, self.tls[ip]):
                del self.tls[ip]
                return None
        except PinMismatch as e:
            self.tls.pop(ip, None)
            self.log("  " + T("GESPERRT: ") + str(e.args[0] if e.args else e), "err")
            return None
        return self.tls[ip]

    # ---------- HTTP ----------
    def api(self, ip, user, pw, method, path, data=None, timeout=30, _retry=True):
        """Sendet eine Anfrage, gibt den HTTP-Code zurück (0 = keine Verbindung); Antwort in self.body."""
        self.body = None
        self.resp_headers = {}
        if self.dry:
            self.log(f"  DRYRUN {method} https://{ip}{path} {scrub(data)}")
            return 200
        mode = self.tls_mode(ip, min(timeout, 10))
        if mode is None:
            return 0
        name, value = self.auth_header(ip, user, pw, mode)
        if name is None:
            return value
        req = urllib.request.Request(f"https://{ip}{path}", method=method,
                                     data=None if data is None else json.dumps(data).encode())
        req.add_header("Content-Type", "application/json")
        req.add_header(name, value)
        try:
            with self.opener(mode).open(req, timeout=timeout) as r:
                code, raw = r.status, r.read()
                self.resp_headers = {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
            self.resp_headers = {k.lower(): v for k, v in e.headers.items()}
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            code, raw = 0, b""
        if code == 401 and name == "X-Auth-Token" and _retry:
            self.sessions.pop((addr_key(ip), user), None)       # Session abgelaufen (z. B. BMC-Neustart)
            return self.api(ip, user, pw, method, path, data, timeout, _retry=False)
        if method != "GET":
            self.note_change(ip, path + " " + (json.dumps(data) if data is not None else ""))
            self.audit(ip, method, path, code, data)
        if code == 0:
            return 0
        try:
            self.body = json.loads(raw) if raw else None
        except ValueError:
            self.body = None
        return code

    @staticmethod
    def ok(code):
        return 200 <= code <= 209

    def errmsg(self):
        """Meldungen des BMC aus der letzten Antwort."""
        found = set()

        def walk(o):
            if isinstance(o, dict):
                m = o.get("Message") or o.get("MessageId")
                if isinstance(m, str) and m:
                    found.add(m)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(self.body)
        return [f"    {m}" for m in sorted(found)]

    def fail_lines(self, what, code):
        http = str(code) if code else T("keine Verbindung")
        return [T('  FEHLER: {0} (HTTP {1})').format(what, http)] + self.errmsg()

    def fail(self, what, code):
        self.log_lines(self.fail_lines(what, code))

    def attempt(self, what, ip, pw, method, path, *bodies):
        """Probiert die JSON-Varianten der Reihe nach (Firmware-Unterschiede), die erste erfolgreiche zählt."""
        for body in bodies or (None,):
            self.code = self.api(ip, self.cur_user, pw, method, path, body)
            if self.ok(self.code):
                return True
        self.fail(what, self.code)
        return False

    # ---------- RIBCL (iLO 3 und iLO 4) ----------
    def ribcl(self, ip, user, pw, mode, section, inner, timeout=60):
        """Schickt einen RIBCL-Befehl (XML über HTTPS) an ein iLO 3/4. Rückgabe wie api(): 200 = ok,
        0 = keine Verbindung, 401 = Anmeldung abgelehnt, 400 = vom iLO abgelehnt (Meldungen in self.body),
        sonst der HTTP-Code. Die Antwort steht in self.xml."""
        self.body, self.xml = None, ""
        if self.dry:
            shown = re.sub(r"""(PASSWORD(?:\s+value)?\s*=\s*)"[^"]*\"""", r'\1"***"', inner)
            self.log(f"  DRYRUN POST https://{ip}/ribcl {shown}")
            return 200
        tls = self.tls_mode(ip, min(timeout, 10))
        if tls is None:
            return 0
        doc = ('<?xml version="1.0"?>\r\n<RIBCL VERSION="2.0">\r\n'
               f'<LOGIN USER_LOGIN={xq(user)} PASSWORD={xq(pw)}>\r\n<{section} MODE="{mode}">\r\n{inner}\r\n'
               f'</{section}>\r\n</LOGIN>\r\n</RIBCL>\r\n').encode()
        host, port = hostport(ip)
        conn = PinnedConnection(host, port, timeout=timeout, context=CONTEXTS[tls], checker=self.pin_check)
        try:
            conn.putrequest("POST", "/ribcl", skip_accept_encoding=True)
            conn.putheader("TE", "chunked")
            conn.putheader("Connection", "Close")
            conn.putheader("Content-Length", str(len(doc)))
            conn.endheaders(doc)
            r = conn.getresponse()
            status = r.status
            try:
                raw = r.read()
            except http.client.IncompleteRead as e:
                raw = e.partial
        except (OSError, http.client.HTTPException):
            status, raw = 0, b""
        finally:
            conn.close()
        self.xml = raw.decode("utf-8", "replace")
        if mode != "read":
            shown = re.sub(r"""(PASSWORD(?:\s+value)?\s*=\s*)"[^"]*\"""", r'\1"***"', inner)
            self.note_change(ip, inner)
            self.audit(ip, "RIBCL", section, status, shown)
        if status == 0:
            return 0
        if status != 200:
            return status
        # Die Antwort besteht aus mehreren XML-Dokumenten, eines je Teilschritt, jedes mit eigenem Status
        found = re.findall(r"""<RESPONSE\s+STATUS\s*=\s*["'](0x[0-9A-Fa-f]+)["']\s+MESSAGE\s*=\s*(?:"([^"]*)"|'([^']*)')""",
                           self.xml)
        if not found:
            self.body = {"Message": T("Keine gültige Antwort der XML-Schnittstelle (RIBCL)")}
            return 400
        bad = [(int(st, 16), a or b) for st, a, b in found if int(st, 16)]
        if not bad:
            return 200
        self.body = {"error": [{"Message": m} for _, m in bad]}
        return 401 if any(st == 0x5F for st, _ in bad) else 400

    def read_serial(self, ip, vendor, user, pw):
        """Seriennummer des Servers (Dell: Service-Tag), nur lesend; leer, wenn nicht lesbar. Es gibt genau einen
        Anmeldeversuch, damit ein falsches Passwort keinen BMC sperrt. iLO 3/4 verrät sie ohne Anmeldung über
        /xmldata, alle anderen über Redfish (Systems, notfalls Chassis). Im Probelauf (dry) wird nichts gelesen."""
        if self.dry:
            return ""
        try:
            if vendor == "hpe_old":
                mode = self.tls_mode(ip, 5)
                if mode is None:
                    return ""
                with self.opener(mode).open(f"https://{ip}/xmldata?item=All", timeout=8) as r:
                    text = r.read(20000).decode("utf-8", "replace")
                m = re.search(r"<SBSN>([^<]*)</SBSN>", text)
                return " ".join(m.group(1).split()) if m else ""
            if not pw:
                return ""

            def first(collection):
                if not self.ok(self.api(ip, user, pw, "GET", collection, timeout=10)) or not isinstance(self.body, dict):
                    return None
                members = self.body.get("Members") or []
                path = members[0].get("@odata.id") if members and isinstance(members[0], dict) else None
                if not path or not self.ok(self.api(ip, user, pw, "GET", path, timeout=10)) \
                        or not isinstance(self.body, dict):
                    return None
                return self.body

            def clean(v):
                return " ".join(str(v or "").split())
            for collection in ("/redfish/v1/Systems", "/redfish/v1/Chassis"):
                b = first(collection)
                if b is None:
                    if self.code in (401, 403):
                        return ""                      # Anmeldung abgelehnt: nicht weiter versuchen
                    continue
                serial, sku = clean(b.get("SerialNumber")), clean(b.get("SKU"))
                found = (sku or serial) if vendor == "dell" else (serial or sku)
                if found:
                    return found
            return ""
        except Exception:
            return ""

    def vendor(self, ip):
        """dell | hpe | hpe_old | smc | lenovo | unbekannt | offline (Abfrage ohne Anmeldung).
        hpe_old = iLO 3/4. Was über ein altes Gerät bekannt ist, steht danach in self.model."""
        self.model = ""
        timeout = 3 if self.dry else 10
        mode = self.tls_mode(ip, timeout)
        if mode is None:
            return "offline"
        root = None
        try:
            with self.opener(mode).open(f"https://{ip}/redfish/v1/", timeout=timeout) as r:
                root = json.loads(r.read())
        except urllib.error.HTTPError:
            pass                        # kein Redfish: kann ein iLO 3 oder altes iLO 4 sein
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            return "offline"
        except ValueError:
            pass
        if isinstance(root, dict):
            oem = root.get("Oem") if isinstance(root.get("Oem"), dict) else {}
            text = (str(root.get("Vendor") or "") + " " + " ".join(oem)).lower()
            if "dell" in text:
                return "dell"
            if "supermicro" in text or "smc" in text:
                return "smc"
            if "lenovo" in text:
                return "lenovo"
            if "Hp" in oem:                 # iLO 4 meldet sich mit „Hp“, iLO 5 und neuer mit „Hpe“
                self.model = "iLO 4"
                return "hpe_old"
            if "hp" in text or "hewlett" in text:
                return "hpe"
        return self.old_ilo(ip, mode, timeout)

    def old_ilo(self, ip, mode, timeout):
        """Erkennt iLO ohne Redfish über die Geräteauskunft /xmldata, die ab Werk ohne Anmeldung antwortet."""
        try:
            with self.opener(mode).open(f"https://{ip}/xmldata?item=All", timeout=timeout) as r:
                text = r.read(20000).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            return "unbekannt"
        name = re.search(r"<PN>([^<]*)</PN>", text)
        if not name or "Lights-Out" not in name.group(1):
            return "unbekannt"
        fw = re.search(r"<FWRI>([^<]*)</FWRI>", text)
        short = re.search(r"\((iLO[^)]*)\)", name.group(1))
        self.model = (short.group(1) if short else name.group(1).strip()) + (f", Firmware {fw.group(1)}" if fw else "")
        return "hpe_old" if re.search(r"iLO ?[34]\b", name.group(1)) else "unbekannt"

    def unknown_hint(self):
        if self.model:
            self.log(T('    Erkannt: {0}. Dieses Gerät wird nicht unterstützt.').format(self.model))
        self.log(T("    Hinweis: Geräte ohne Redfish (iDRAC6, iLO 2, sehr alte Firmware) werden nicht unterstützt."))

    def log_lines(self, lines):
        """Erste Zeile als Fehler, die folgenden (Meldungen des BMC) normal."""
        for i, line in enumerate(lines):
            self.log(line, "" if i else "err")




class Engine(RedfishClient):
    """Konfiguriert Benutzer/Netzwerk aller Server der Liste; Protokollzeilen gehen an log()."""

    def __init__(self, rows, cur_user, cur_pass, new_user, new_pass, slot=3, dry=True,
                 log=print, stopped=lambda: False, verify_wait=240, vendor=None, on_done=lambda ip: None,
                 lock_factory=True):
        super().__init__(dry, log, stopped)
        self.rows, self.cur_user_set, self.cur_pass = rows, cur_user, cur_pass
        self.new_user, self.new_pass, self.slot = new_user, new_pass, slot
        self.verify_wait, self.force_vendor = verify_wait, vendor
        self.on_done = on_done      # wird mit der bisherigen IP aufgerufen, sobald ein Server umgestellt ist
        self.verify_path = ""
        self.lock_factory = lock_factory    # Werkskonto nach erfolgreicher Kontrolle sperren
        self.factory = {}                   # neue IP -> (Hersteller, Werksbenutzer)

    # ---------- Werkskonto sperren ----------
    @staticmethod
    def random_password(length=16):
        """Zufallspasswort, das die Regeln aller vier Hersteller erfüllt (groß, klein, Ziffer, Sonderzeichen,
        höchstens 20 Zeichen). Wird nirgends gespeichert."""
        lower, upper, digits, special = "abcdefghijkmnopqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "-_!+"
        chars = [secrets.choice(c) for c in (lower, upper, digits, special)]
        chars += [secrets.choice(lower + upper + digits + special) for _ in range(length - 4)]
        secrets.SystemRandom().shuffle(chars)
        return "".join(chars)

    def factory_user_of(self, vendor):
        """Werksbenutzer, der nach dem Lauf gesperrt werden darf, oder None (mit Begründung im Protokoll)."""
        user = self.cur_user
        defaults = {u.lower() for u in self.DEFAULT_USER.values()}
        if user.lower() == self.new_user.lower():
            self.log(T("  Werkskonto: neuer Administrator ist dasselbe Konto, wird nicht gesperrt."))
            return None
        if user.lower() not in defaults:
            self.log(T("  Werkskonto: Anmeldung lief nicht über ein Werkskonto ({0}), es wird nichts gesperrt.")
                     .format(user))
            return None
        return user

    def lock_factory_account(self, ip, vendor, factory):
        """Sperrt das Werkskonto, angemeldet als NEUER Administrator unter der NEUEN Adresse – erst nachdem die
        Kontrolle dort geklappt hat. Bevorzugt „Enabled: false“; kennt der BMC das nicht, bekommt das Konto ein
        Zufallspasswort, das nirgends gespeichert wird (mit dem neuen Administrator jederzeit zurücksetzbar).
        Gibt True zurück, wenn das Werkskonto danach nicht mehr mit dem Werkspasswort nutzbar ist."""
        user, pw = self.new_user, self.new_pass
        if vendor == "hpe_old":
            code = self.ribcl(ip, user, pw, "write", "USER_INFO",
                              f"<MOD_USER USER_LOGIN={xq(factory)}><PASSWORD value={xq(self.random_password())}/>"
                              f"</MOD_USER>")
            if self.ok(code):
                self.log(T("  Werkskonto {0}: Passwort auf Zufallswert gesetzt (iLO 3/4 kann Konten nicht "
                           "abschalten; Wert wird nicht gespeichert).").format(factory), "ok")
                return True
            self.fail(T("Werkskonto {0} sperren").format(factory), code)
            return False
        coll = "/redfish/v1/AccountService/Accounts"
        code = self.api(ip, user, pw, "GET", coll)
        if not self.ok(code) or not isinstance(self.body, dict):
            self.fail(T("Konten lesen"), code)
            return False
        target, me = None, None
        for m in self.body.get("Members") or []:
            path = m.get("@odata.id") if isinstance(m, dict) else None
            if not path or not self.ok(self.api(ip, user, pw, "GET", path)) or not isinstance(self.body, dict):
                continue
            name = str(self.body.get("UserName") or "")
            if name.lower() == factory.lower():
                target = (path, dict(self.body))
            elif name.lower() == user.lower():
                me = dict(self.body)
        if me is None or str(me.get("RoleId", "")).lower() not in ("administrator", "admin"):
            self.log(T("  FEHLER: Neuer Administrator {0} ist nicht als Administrator eingetragen; Werkskonto bleibt "
                       "zur Sicherheit aktiv.").format(user), "err")
            return False
        if target is None:
            self.log(T("  Werkskonto {0}: nicht (mehr) vorhanden.").format(factory), "ok")
            return True
        path, doc = target
        if doc.get("Enabled") is False:
            self.log(T("  Werkskonto {0}: schon gesperrt.").format(factory), "ok")
            return True
        code = self.api(ip, user, pw, "PATCH", path, {"Enabled": False})
        if self.ok(code) and self.ok(self.api(ip, user, pw, "GET", path)) and isinstance(self.body, dict) \
                and self.body.get("Enabled") is False:
            self.log(T("  Werkskonto {0}: gesperrt.").format(factory), "ok")
            return True
        code = self.api(ip, user, pw, "PATCH", path, {"Password": self.random_password()})
        if self.ok(code):
            self.log(T("  Werkskonto {0}: lässt sich hier nicht abschalten, Passwort auf Zufallswert gesetzt (wird "
                       "nicht gespeichert; mit dem neuen Administrator jederzeit zurücksetzbar).").format(factory), "ok")
            return True
        self.fail(T("Werkskonto {0} sperren").format(factory), code)
        return False

    def find_account(self, ip, pw, collection):
        """Pfad des Kontos mit UserName == neuer Benutzer oder None."""
        if not self.ok(self.api(ip, self.cur_user, pw, "GET", collection)) or not isinstance(self.body, dict):
            return None
        for m in self.body.get("Members") or []:
            path = m.get("@odata.id") if isinstance(m, dict) else None
            if path and self.ok(self.api(ip, self.cur_user, pw, "GET", path)) \
                    and isinstance(self.body, dict) and self.body.get("UserName") == self.new_user:
                return path
        return None

    def existing_account(self, ip, pw, collection, code):
        """Anlegen schlug fehl: gibt es das Konto schon, Passwort und Rolle setzen. Gibt das gültige Passwort zurück."""
        msg = self.fail_lines(T("Benutzer"), code)
        acct = self.find_account(ip, pw, collection)
        if not acct:
            self.log_lines(msg)
            return None
        if not self.attempt(T("Benutzer (vorhandenes Konto)"), ip, pw, "PATCH", acct,
                            {"Password": self.new_pass, "RoleId": "Administrator"}):
            return None
        self.log(T('  Benutzer {0} existierte schon, Passwort und Rolle gesetzt').format(self.new_user), "ok")
        return self.new_pass if self.new_user == self.cur_user else pw     # eigenes Passwort geändert

    # ---------- Dell iDRAC ----------
    def setup_dell(self, ip, pw, new_ip, mask, gw, host):
        mgr = "/redfish/v1/Managers/iDRAC.Embedded.1"
        acct = f"/redfish/v1/AccountService/Accounts/{self.slot}"
        self.verify_path = mgr
        if not self.attempt(T("Login/Erreichbarkeit"), ip, pw, "GET", mgr):
            return False
        fw = self.body.get("FirmwareVersion") if isinstance(self.body, dict) else None
        if fw:
            self.log(T('  Firmware {0}').format(fw))
        # iDRAC7/8 vor 2.50.50.50 kennen die Attribute nicht, über die Hostname und Netzwerk gesetzt werden
        if self.api(ip, self.cur_user, pw, "GET", mgr + "/Attributes") in (404, 405, 501):
            self.log(T("  FEHLER: Diese iDRAC-Firmware ist zu alt für das Tool (iDRAC7/8 erst ab 2.50.50.50). "
                       "Bitte zuerst die Firmware aktualisieren. Nichts geändert."), "err")
            return False
        # Schutz: einen fremden Benutzer im Slot nie überschreiben
        if not self.attempt(T("User-Slot lesen"), ip, pw, "GET", acct):
            return False
        owner = str(self.body.get("UserName") or "") if isinstance(self.body, dict) else ""
        if owner and owner != self.new_user:
            self.log(T('  FEHLER: User-Slot {0} ist schon belegt (Benutzer {1}). Nichts überschrieben, bitte einen freien Slot wählen.').format(self.slot, owner), "err")
            return False
        if not self.attempt(T("Benutzer"), ip, pw, "PATCH", acct,
                            {"UserName": self.new_user, "Password": self.new_pass,
                             "RoleId": "Administrator", "Enabled": True}):
            return False
        self.log(T('  Benutzer {0} ok').format(self.new_user), "ok")
        pw = self.new_pass if self.new_user == self.cur_user else pw     # eigenes Passwort geändert
        if not self.attempt(T("Hostname"), ip, pw, "PATCH", mgr + "/Attributes",
                            {"Attributes": {"NIC.1.DNSRacName": host}}):
            return False
        self.log(T("  Hostname ok"), "ok")
        # Netzwerk zuletzt - danach ist die alte IP weg
        code = self.api(ip, self.cur_user, pw, "PATCH", mgr + "/Attributes", {"Attributes": {
            "IPv4Static.1.Address": new_ip, "IPv4Static.1.Netmask": mask, "IPv4Static.1.Gateway": gw,
            "IPv4.1.DHCPEnable": "Disabled"}})
        if self.ok(code) or code == 0:          # 0 = Verbindung beim IP-Wechsel abgerissen
            if not self.moved(ip, code):
                return False
            self.log(T("  Netzwerk gesetzt"), "ok")
            return True
        self.fail(T("Netzwerk"), code)
        return False

    # ---------- HPE iLO ----------
    def setup_hpe(self, ip, pw, new_ip, mask, gw, host):
        nic = "/redfish/v1/Managers/1/EthernetInterfaces/1/"       # dedizierter iLO-Port
        accounts = "/redfish/v1/AccountService/Accounts/"
        self.verify_path = "/redfish/v1/Managers/1/"
        if not self.attempt(T("Login/Erreichbarkeit"), ip, pw, "GET", "/redfish/v1/Managers/1/"):
            if self.cur_user == "root":
                self.log(T("    Hinweis: bei HPE heißt der Werksbenutzer meist Administrator"))
            return False
        # 1) Benutzer anlegen; existiert er schon, Passwort und Rolle setzen
        code = self.api(ip, self.cur_user, pw, "POST", accounts,
                        {"UserName": self.new_user, "Password": self.new_pass, "RoleId": "Administrator"})
        if self.ok(code):
            self.log(T('  Benutzer {0} ok').format(self.new_user), "ok")
        else:
            pw = self.existing_account(ip, pw, accounts, code)
            if pw is None:
                return False
        # 2) Hostname
        if not self.attempt(T("Hostname"), ip, pw, "PATCH", nic,
                            {"HostName": host}, {"Oem": {"Hpe": {"HostName": host}}}):
            return False
        self.log(T("  Hostname ok"), "ok")
        # 3) DHCP aus, 4) statische Adresse (wirkt erst nach dem iLO-Neustart)
        if not self.attempt(T("DHCP abschalten"), ip, pw, "PATCH", nic,
                            {"DHCPv4": {"DHCPEnabled": False}, "Oem": {"Hpe": {"DHCPv4": {"Enabled": False}}}},
                            {"DHCPv4": {"DHCPEnabled": False}},
                            {"Oem": {"Hpe": {"DHCPv4": {"Enabled": False}}}}):
            return False
        addr = [{"Address": new_ip, "SubnetMask": mask, "Gateway": gw}]
        if not self.attempt(T("Statische IP"), ip, pw, "PATCH", nic,
                            {"IPv4StaticAddresses": addr}, {"IPv4Addresses": addr}):
            return False
        self.log(T("  Netzwerk gesetzt"), "ok")
        # 5) iLO neu starten, damit Hostname und Adresse aktiv werden
        if not self.attempt(T("iLO-Neustart"), ip, pw, "POST", "/redfish/v1/Managers/1/Actions/Manager.Reset/",
                            {"ResetType": "ForceRestart"}, {}):
            return False
        self.log(T("  iLO-Neustart ausgelöst"), "ok")
        return True

    # ---------- HPE iLO 3 und iLO 4 (RIBCL) ----------
    PRIVS = "".join(f'<{p} value="Yes"/>' for p in ("ADMIN_PRIV", "REMOTE_CONS_PRIV", "RESET_SERVER_PRIV",
                                                     "VIRTUAL_MEDIA_PRIV", "CONFIG_ILO_PRIV"))

    def setup_hpe_old(self, ip, pw, new_ip, mask, gw, host):
        self.verify_path = "ribcl"
        if len(host) > 49:
            self.log(T("  FEHLER: Hostname zu lang, iLO 3/4 erlaubt höchstens 49 Zeichen. Nichts geändert."), "err")
            return False
        # 1) Anmeldung prüfen
        code = self.ribcl(ip, self.cur_user, pw, "read", "RIB_INFO", "<GET_FW_VERSION/>")
        if not self.ok(code):
            self.fail(T("Login/Erreichbarkeit"), code)
            if self.cur_user == "root":
                self.log(T("    Hinweis: bei HPE heißt der Werksbenutzer meist Administrator"))
            if code not in (0, 401):
                self.log(T("    Hinweis: iLO 3 und 4 werden über die XML-Schnittstelle (RIBCL) konfiguriert. "
                           "iLO 2 und älter werden nicht unterstützt."))
            return False
        fw = re.search(r'FIRMWARE_VERSION\s*=\s*"([^"]*)"', self.xml)
        kind = re.search(r'MANAGEMENT_PROCESSOR\s*=\s*"([^"]*)"', self.xml)
        if fw and kind:
            self.log(T('  Gerät: {0}, Firmware {1}').format(kind.group(1), fw.group(1)))
        # 2) Benutzer anlegen; existiert er schon, Passwort und Rechte setzen
        code = self.ribcl(ip, self.cur_user, pw, "read", "USER_INFO", "<GET_ALL_USERS/>")
        if not self.ok(code):
            self.fail(T("Benutzer lesen"), code)
            return False
        logins = re.findall(r'USER_LOGIN\s+VALUE\s*=\s*"([^"]+)"', self.xml)
        known = next((u for u in logins if u.lower() == self.new_user.lower()), None)
        if known:
            code = self.ribcl(ip, self.cur_user, pw, "write", "USER_INFO",
                              f"<MOD_USER USER_LOGIN={xq(known)}><PASSWORD value={xq(self.new_pass)}/>"
                              f"{self.PRIVS}</MOD_USER>")
            if not self.ok(code):
                self.fail(T("Benutzer (vorhandenes Konto)"), code)
                return False
            self.log(T('  Benutzer {0} existierte schon, Passwort und Rolle gesetzt').format(known), "ok")
            if known.lower() == self.cur_user.lower():
                pw = self.new_pass                  # eigenes Passwort geändert
        else:
            code = self.ribcl(ip, self.cur_user, pw, "write", "USER_INFO",
                              f"<ADD_USER USER_NAME={xq(self.new_user)} USER_LOGIN={xq(self.new_user)} "
                              f"PASSWORD={xq(self.new_pass)}>{self.PRIVS}</ADD_USER>")
            if not self.ok(code):
                self.fail(T("Benutzer"), code)
                return False
            self.log(T('  Benutzer {0} ok').format(self.new_user), "ok")
        # 3) Hostname und Netzwerk in einem Schritt, zuletzt: das iLO startet danach von selbst neu
        code = self.ribcl(ip, self.cur_user, pw, "write", "RIB_INFO",
                          f'<MOD_NETWORK_SETTINGS><DHCP_ENABLE value="No"/><IP_ADDRESS value={xq(new_ip)}/>'
                          f"<SUBNET_MASK value={xq(mask)}/><GATEWAY_IP_ADDRESS value={xq(gw)}/>"
                          f"<DNS_NAME value={xq(host)}/></MOD_NETWORK_SETTINGS>")
        if self.ok(code) or code == 0:              # 0 = Verbindung beim Neustart abgerissen
            if not self.moved(ip, code):
                return False
            self.log(T("  Netzwerk und Hostname gesetzt, das iLO startet von selbst neu"), "ok")
            return True
        self.fail(T("Netzwerk und Hostname"), code)
        return False

    def identity(self, ip):
        """Modell und Seriennummer des Servers (nur lesen, mit dem neuen Benutzer). Leer, wenn nicht lesbar;
        ein Fehler hier stört den Lauf nie."""
        try:
            if not self.ok(self.api(ip, self.new_user, self.new_pass, "GET", "/redfish/v1/Systems", timeout=10)) \
                    or not isinstance(self.body, dict):
                return ""
            members = self.body.get("Members") or []
            path = members[0].get("@odata.id") if members and isinstance(members[0], dict) else None
            if not path or not self.ok(self.api(ip, self.new_user, self.new_pass, "GET", path, timeout=10)) \
                    or not isinstance(self.body, dict):
                return ""
            b = self.body
            model = " ".join(str(b.get("Model") or "").split())
            serial = " ".join(str(b.get("SerialNumber") or "").split())
            tag = " ".join(str(b.get("SKU") or "").split()) if b.get("SKU") != b.get("SerialNumber") else ""
            parts = [model] if model else []
            if serial:
                parts.append(T("Seriennummer {0}").format(serial))
            if tag and tag != serial:
                parts.append(T("Service-Tag/SKU {0}").format(tag))
            return " · ".join(parts)
        except Exception:
            return ""

    def check_login(self, ip, path):
        """Kontrolle am Ende: Anmeldung mit dem neuen Benutzer unter der neuen Adresse."""
        if path == "ribcl":
            return self.ok(self.ribcl(ip, self.new_user, self.new_pass, "read", "RIB_INFO", "<GET_FW_VERSION/>",
                                      timeout=10))
        return self.ok(self.api(ip, self.new_user, self.new_pass, "GET", path, timeout=10))

    # ---------- Supermicro ----------
    def setup_smc(self, ip, pw, new_ip, mask, gw, host):
        nic = "/redfish/v1/Managers/1/EthernetInterfaces/1"
        accounts = "/redfish/v1/AccountService/Accounts"
        self.verify_path = "/redfish/v1/Managers/1"
        if not self.attempt(T("Login/Erreichbarkeit"), ip, pw, "GET", "/redfish/v1/Managers/1"):
            if self.cur_user != "ADMIN":
                self.log(T("    Hinweis: bei Supermicro heißt der Werksbenutzer meist ADMIN"))
            return False
        # 1) Benutzer anlegen. Neuere Firmware gibt ohne AccountTypes nur Redfish-Zugriff,
        #    deshalb erst mit Zugriffsarten versuchen, dann ohne (ältere Firmware).
        code, types = 0, None
        for types in (["Redfish", "WebUI", "IPMI", "HostConsole", "ManagerConsole", "KVMIP", "VirtualMedia"],
                      ["Redfish", "WebUI", "IPMI"], None):
            body = {"UserName": self.new_user, "Password": self.new_pass, "RoleId": "Administrator", "Enabled": True}
            if types:
                body["AccountTypes"] = types
            code = self.api(ip, self.cur_user, pw, "POST", accounts, body)
            if self.ok(code):
                break
        if self.ok(code):
            self.log(T('  Benutzer {0} ok').format(self.new_user) + (
                "" if types or self.dry else T(" (ohne Zugriffsarten angelegt: Web-Anmeldung bitte einmal prüfen)")), "ok")
        else:
            pw = self.existing_account(ip, pw, accounts, code)
            if pw is None:
                return False
        # 2) Hostname
        if not self.attempt(T("Hostname"), ip, pw, "PATCH", nic, {"HostName": host}):
            return False
        self.log(T("  Hostname ok"), "ok")
        # 3) Netzwerk zuletzt - der BMC wechselt die Adresse sofort
        addr = [{"Address": new_ip, "SubnetMask": mask, "Gateway": gw}]
        code = 0
        for body in ({"DHCPv4": {"DHCPEnabled": False}, "IPv4StaticAddresses": addr},
                     {"DHCPv4": {"DHCPEnabled": False}, "IPv4Addresses": addr}):
            code = self.api(ip, self.cur_user, pw, "PATCH", nic, body)
            if self.ok(code) or code == 0:      # 0 = Verbindung beim IP-Wechsel abgerissen
                if not self.moved(ip, code):
                    return False
                self.log(T("  Netzwerk gesetzt"), "ok")
                return True
        self.fail(T("Netzwerk"), code)
        return False

    # ---------- Lenovo XCC ----------
    def setup_lenovo(self, ip, pw, new_ip, mask, gw, host):
        nic = "/redfish/v1/Managers/1/EthernetInterfaces/NIC"
        accounts = "/redfish/v1/AccountService/Accounts"
        self.verify_path = "/redfish/v1/Managers/1"
        if not self.attempt(T("Login/Erreichbarkeit"), ip, pw, "GET", "/redfish/v1/Managers/1"):
            if self.cur_user != "USERID":
                self.log(T("    Hinweis: bei Lenovo heißt der Werksbenutzer meist USERID"))
            return False
        # 1) Benutzer anlegen; existiert er schon, Passwort und Rolle setzen
        code = self.api(ip, self.cur_user, pw, "POST", accounts,
                        {"UserName": self.new_user, "Password": self.new_pass, "RoleId": "Administrator"})
        if self.ok(code):
            self.log(T('  Benutzer {0} ok').format(self.new_user), "ok")
        else:
            pw = self.existing_account(ip, pw, accounts, code)
            if pw is None:
                return False
        # 2) Hostname
        if not self.attempt(T("Hostname"), ip, pw, "PATCH", nic, {"HostName": host}):
            return False
        self.log(T("  Hostname ok"), "ok")
        # 3) Netzwerk zuletzt - der BMC wechselt die Adresse sofort
        addr = [{"Address": new_ip, "SubnetMask": mask, "Gateway": gw}]
        code = 0
        for body in ({"IPv4StaticAddresses": addr, "Oem": {"Lenovo": {"DHCPEnabled": False}}},
                     {"IPv4StaticAddresses": addr, "DHCPv4": {"DHCPEnabled": False}},
                     {"IPv4StaticAddresses": addr}):
            code = self.api(ip, self.cur_user, pw, "PATCH", nic, body)
            if self.ok(code) or code == 0:      # 0 = Verbindung beim IP-Wechsel abgerissen
                if not self.moved(ip, code):
                    return False
                self.log(T("  Netzwerk gesetzt"), "ok")
                return True
        self.fail(T("Netzwerk"), code)
        return False

    # ---------- Hauptschleife ----------
    def pause(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.stopped():
            time.sleep(0.2)

    def run(self):
        """Gibt True zurück, wenn alle Server ohne Fehler durchliefen."""
        done, failed, inventory = [], [], []
        for cur_ip, row_pw, new_ip, mask, gw, host in self.rows:
            if self.stopped():
                self.log(T("== Abgebrochen, übrige Server nicht bearbeitet"), "err")
                failed.append(T("Abbruch"))
                break
            pw = row_pw or self.cur_pass
            if not pw:
                self.log(T('== {0}: kein Passwort (weder in der Zeile noch oben)').format(cur_ip), "err")
                failed.append(cur_ip)
                continue
            self.log(f"== {cur_ip} -> {new_ip} ({host})", "head")
            v = self.force_vendor or self.vendor(cur_ip)
            if self.dry and v not in self.NAME:
                self.log(T('  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Dell-Ablauf').format(v))
                v = "dell"
            if v == "offline":
                self.log(T("  FEHLER: nicht erreichbar"), "err")
                failed.append(cur_ip)
                continue
            if v not in self.NAME:
                self.log(T("  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)"), "err")
                self.unknown_hint()
                failed.append(cur_ip)
                continue
            # Schutz: keine Adresse vergeben, unter der schon ein anderes Gerät antwortet
            if new_ip != cur_ip and self.in_use(new_ip):
                self.log(T('  FEHLER: Die neue IP {0} ist schon vergeben, dort antwortet bereits ein Gerät. Nichts geändert.').format(new_ip), "err")
                failed.append(cur_ip)
                continue
            self.cur_user = self.cur_user_set or self.DEFAULT_USER[v]
            self.log(T('  Hersteller: {0}, Anmeldung als {1}').format(self.NAME[v], self.cur_user))
            if not self.dry:
                self.tls_mode(cur_ip)
            if self.tls.get(cur_ip, "modern") != "modern":
                self.log(T("  Hinweis: altes Gerät, die Verbindung nutzt veraltete Verschlüsselung (TLS 1.0/1.1)."))
            if not self.dry and v != "hpe_old":
                self.backup(cur_ip, self.cur_user, pw, v, "neuware")
            if getattr(self, "setup_" + v)(cur_ip, pw, new_ip, mask, gw, host):
                done.append((new_ip, self.verify_path))
                if self.lock_factory:
                    factory = self.factory_user_of(v)
                    if factory:
                        self.factory[new_ip] = (v, factory)
                        if self.dry:
                            self.log(T("  Trockenlauf: würde nach erfolgreicher Kontrolle das Werkskonto {0} "
                                       "sperren (angemeldet als {1}).").format(factory, self.new_user))
                if not self.dry:
                    self.on_done(cur_ip)        # Zeile in der Maske als fertig markieren
            else:
                failed.append(cur_ip)

        # Kontrolle: neue IP mit neuem Benutzer erreichbar? (iLO braucht nach dem Neustart 1-3 Minuten)
        if not self.dry and done and not self.stopped():
            self.log("")
            self.log(T('Kontrolle der neuen Adressen (bis zu {0}s)... Die Liste bleibt bis dahin '
                      'gesperrt; „Abbrechen“ beendet die Kontrolle vorzeitig.').format(self.verify_wait))
            pending, start, end = list(done), time.monotonic(), time.monotonic() + self.verify_wait
            unlocked = []
            self.pause(min(self.verify_wait, 20))
            while pending and not self.stopped():
                still, out_of_time = [], False
                for ip, path in pending:
                    # Zeitbudget je Server prüfen, nicht erst nach der ganzen Runde: ein einzelner
                    # verzögert antwortender Server soll die anderen nicht über die Wartezeit hinaus
                    # verzögern (sonst wirkt der Lauf "eingefroren", obwohl er nur sehr lange braucht).
                    if out_of_time:
                        still.append((ip, path))
                        continue
                    if self.check_login(ip, path):
                        info = self.identity(ip) if path != "ribcl" else ""
                        self.log(T('  OK      {0}').format(ip) + (f"   {info}" if info else ""), "ok")
                        if info:
                            inventory.append(f"{ip}   {info}")
                        if ip in self.factory and not self.lock_factory_account(ip, *self.factory[ip]):
                            unlocked.append(ip)
                    else:
                        still.append((ip, path))
                    if time.monotonic() >= end:
                        out_of_time = True
                pending = still
                if not pending or out_of_time or self.stopped():
                    break
                self.log(T('  ... noch {0} von {1} Server(n) nicht erreichbar, warte weiter ({2}s vergangen)')
                         .format(len(pending), len(done), int(time.monotonic() - start)))
                self.pause(10)
            for ip, _ in pending:
                self.log(T('  FEHLT   {0}').format(ip), "err")
                failed.append(ip)
            for ip in unlocked:
                self.log(T("  WERKSKONTO AKTIV   {0}: bitte von Hand sperren oder Passwort ändern.").format(ip), "err")
                failed.append(ip)

        if inventory:
            self.log("")
            self.log(T("Inventar (neue IP, Modell, Seriennummer):"), "head")
            for line in inventory:
                self.log("  " + line)
        self.log("")
        summary = T('Fertig: {0} konfiguriert, {1} Fehler').format(len(done), len(failed))
        self.log(summary + (": " + " ".join(failed) if failed else ""), "err" if failed else "ok")
        return not failed

    @staticmethod
    def in_use(ip):
        """True, wenn unter der Adresse schon ein Gerät antwortet (Verbindung klappt oder wird aktiv abgelehnt).
        Im gesamten Loopback-Bereich (127.0.0.0/8, Testlabor) lehnt der Kernel jeden unbelegten Port sofort
        aktiv ab, unabhängig davon, ob dort ein virtueller Server läuft - "aktiv abgelehnt" ist dort also kein
        Beweis. Dort zählt nur eine tatsächlich angenommene Verbindung."""
        try:
            loopback = ipaddress.IPv4Address(ip).is_loopback
        except ValueError:
            loopback = False
        alive = {0} if loopback else {0, errno.ECONNREFUSED, getattr(errno, "WSAECONNREFUSED", 10061)}
        for port in (443, 80, 22, 445):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                if s.connect_ex((ip, port)) in alive:
                    return True
            except OSError:
                pass
            finally:
                s.close()
        return False

    @staticmethod
    def alive(ip):
        """True, wenn unter der Adresse noch ein BMC auf Port 443 antwortet."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            return s.connect_ex((ip, 443)) == 0
        except OSError:
            return False
        finally:
            s.close()

    def moved(self, ip, code):
        """Nach dem Setzen der Adresse: True, wenn der BMC die alte Adresse verlassen hat. Eine saubere
        Antwort (2xx) gilt als erledigt. Riss nur die Verbindung ab (0), wird bis zu 15 Sekunden geprüft,
        ob das Gerät unter der alten Adresse verschwindet - sonst wäre ein gezogenes Kabel ein „Erfolg“."""
        if code != 0 or self.dry:
            return True
        end = time.monotonic() + 15
        while not self.stopped():
            if not self.alive(ip):
                return True
            if time.monotonic() >= end:
                break
            self.pause(2)
        self.log(T("  FEHLER: Die Verbindung brach ab, aber das Gerät antwortet weiter unter der alten Adresse. "
                   "Netzwerk nicht umgestellt."), "err")
        return False




class BiosEngine(RedfishClient):
    """Setzt UEFI/BIOS-Attribute auf allen Servern der Liste. Eigener, von den Netzwerk-Profilen
    getrennter Bereich: Attribute kommen aus einer eigenen Vorlage (Name -> gewünschter Wert) und
    werden je Gerät gegen die tatsächlich vorhandenen geprüft - nicht vorhandene werden übersprungen,
    nicht geraten. Protokollzeilen gehen an log()."""

    def __init__(self, rows, cur_user, attrs, dry=True, log=print, stopped=lambda: False,
                 vendor=None, reboot=True):
        super().__init__(dry, log, stopped)
        self.rows, self.cur_user_set, self.attrs = rows, cur_user, attrs
        self.force_vendor, self.reboot = vendor, reboot

    def bios_path(self, vendor):
        return self.sys_path(vendor) + "/Bios"

    def read_bios(self, ip, user, pw, vendor):
        """Liest die aktuellen Attribute und den Pfad zum Schreiben; gibt (Attribute, Pfad) oder (None, None)."""
        code = self.code = self.api(ip, user, pw, "GET", self.bios_path(vendor))
        if not self.ok(code) or not isinstance(self.body, dict):
            return None, None
        attrs = self.body.get("Attributes")
        if not isinstance(attrs, dict):
            return None, None
        settings_path = self.bios_path(vendor) + "/Settings"
        rs = self.body.get("@Redfish.Settings")
        if isinstance(rs, dict) and isinstance(rs.get("SettingsObject"), dict):
            settings_path = rs["SettingsObject"].get("@odata.id") or settings_path
        return attrs, settings_path

    def power_state(self, ip, user, pw, vendor):
        code = self.api(ip, user, pw, "GET", self.sys_path(vendor))
        return self.body.get("PowerState") if self.ok(code) and isinstance(self.body, dict) else None

    @classmethod
    def coerce(cls, current, new):
        """Passt den eingegebenen Text an den Typ des bisherigen Werts an. Gibt (True, Wert) zurück oder
        (False, None), wenn der Text nicht zum Typ passt - dann wird nichts geraten."""
        text = str(new).strip()
        if isinstance(current, bool):
            if text.lower() in cls.TRUE:
                return True, True
            if text.lower() in cls.FALSE:
                return True, False
            return False, None
        try:
            if isinstance(current, int):
                return True, int(text)
            if isinstance(current, float):
                return True, float(text)
        except ValueError:
            return False, None
        return True, text

    def apply_row(self, ip, pw, vendor):
        user = self.cur_user_set or self.DEFAULT_USER[vendor]
        self.cur_user = user
        if not self.self_guard(ip, user, pw, vendor):
            return False
        self.backup(ip, user, pw, vendor, "uefi")
        live, settings_path = self.read_bios(ip, user, pw, vendor)
        checked = live is not None
        if not checked:
            if not self.dry:
                self.fail(T("BIOS-Attribute lesen"), self.code)
                return False
            self.log(T("  Trockenlauf: Attribute nicht wirklich gelesen, zeige den Ablauf ungeprüft"))
            live, settings_path = {}, self.bios_path(vendor) + "/Settings"
        wanted, missing, same, bad, locked = {}, [], [], [], []
        for key, val in self.attrs.items():
            if self.blocked(key):
                locked.append(key)
            elif not checked:
                wanted[key] = val
            elif key not in live:
                missing.append(key)
            else:
                good, value = self.coerce(live[key], val)
                if not good:
                    bad.append(key)
                elif value == live[key]:
                    same.append(key)
                else:
                    wanted[key] = value
        if locked:
            self.log(T("  FEHLER: gesperrt, wird nie gesetzt (Passwort-, TPM- und Lösch-Attribute): ")
                     + ", ".join(sorted(locked)), "err")
        if bad:
            self.log(T("  FEHLER: Wert passt nicht zum Attribut (Zahl bzw. Ein/Aus erwartet), nicht gesetzt: ")
                     + ", ".join(sorted(bad)), "err")
        if missing:
            self.log(T("  nicht auf diesem Gerät vorhanden, übersprungen: ") + ", ".join(sorted(missing)))
        if same:
            self.log(T("  schon so eingestellt: ") + ", ".join(sorted(same)))
        clean = not locked and not bad
        if not wanted:
            self.log(T("  nichts zu ändern, kein Neustart"))
            return clean
        code = self.api(ip, user, pw, "PATCH", settings_path, {"Attributes": wanted})
        if not self.ok(code):
            self.fail(T("BIOS-Attribute setzen"), code)
            return False
        self.log(T("  gesetzt: ") + ", ".join(f"{k}={v}" for k, v in wanted.items()), "ok")
        if not self.reboot:
            self.log(T("  Hinweis: wird erst beim nächsten Neustart übernommen (kein Neustart ausgelöst)"))
            return clean
        state = self.power_state(ip, user, pw, vendor)
        reset = "ForceRestart" if self.dry else {"Off": "On", "On": "ForceRestart"}.get(state)
        if not reset:
            self.log(T("  Hinweis: Betriebszustand unbekannt, kein Neustart ausgelöst. Bitte von Hand neu starten."))
            return clean
        code = self.api(ip, user, pw, "POST", self.sys_path(vendor) + "/Actions/ComputerSystem.Reset",
                        {"ResetType": reset})
        if self.ok(code) or code == 0:
            self.log(T('  Neustart ausgelöst ({0}); die Attribute greifen danach').format(reset), "ok")
            return clean
        self.fail(T("Neustart"), code)
        return False

    def run(self):
        """Gibt True zurück, wenn alle Server ohne Fehler durchliefen."""
        done, failed = [], []
        for cur_ip, row_pw in self.rows:
            if self.stopped():
                self.log(T("== Abgebrochen, übrige Server nicht bearbeitet"), "err")
                failed.append(T("Abbruch"))
                break
            if not row_pw:
                self.log(T('== {0}: kein Passwort').format(cur_ip), "err")
                failed.append(cur_ip)
                continue
            self.log(f"== {cur_ip}", "head")
            v = self.force_vendor or self.vendor(cur_ip)
            if self.dry and v not in self.NAME:
                self.log(T('  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Dell-Ablauf').format(v))
                v = "dell"
            if v == "offline":
                self.log(T("  FEHLER: nicht erreichbar"), "err")
                failed.append(cur_ip)
                continue
            if v not in self.NAME:
                self.log(T("  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)"), "err")
                self.unknown_hint()
                failed.append(cur_ip)
                continue
            self.log(T('  Hersteller: {0}').format(self.NAME[v]))
            if v == "hpe_old":
                self.log(T("  Hinweis: iLO 3 kann keine UEFI-Einstellungen über Redfish. Bei iLO 4 geht es nur ab "
                           "Gen9 mit Firmware 2.30 und ist ungeprüft."))
                v = "hpe"
            if self.apply_row(cur_ip, row_pw, v):
                done.append(cur_ip)
            else:
                failed.append(cur_ip)
        self.log("")
        summary = T('Fertig: {0} Server ohne Fehler, {1} mit Fehlern').format(len(done), len(failed))
        self.log(summary + (": " + " ".join(failed) if failed else ""), "err" if failed else "ok")
        return not failed

    # Attribute, die das Tool nie setzt: ein Tippfehler dort sperrt den Server aus oder kostet Daten
    BLOCKED = ("password", "passwd", "pwd", "tpm", "trustedcomputing", "erase", "sanitize")
    TRUE = ("1", "true", "wahr", "ja", "yes", "oui", "on", "enabled")
    FALSE = ("0", "false", "falsch", "nein", "no", "non", "off", "disabled")

    @classmethod
    def blocked(cls, name):
        low = str(name).lower()
        return any(b in low for b in cls.BLOCKED)




class ServicesEngine(RedfishClient):
    """Richtet Verwaltungsdienste auf allen Servern der Liste ein: NTP, Syslog, LDAP (mit
    Gruppen-Zuordnung) und das HTTPS-Zertifikat. Jeder Dienst ist einzeln an- oder abschaltbar;
    nur gewaehlte Dienste werden angewendet. Ein Dienst, den ein Hersteller nicht kennt, wird
    uebersprungen, nicht geraten. Protokollzeilen gehen an log()."""

    ROLES = ("Administrator", "Operator", "ReadOnly")
    LABEL = {"ntp": "NTP", "syslog": "Syslog", "ldap": "LDAP", "cert": "HTTPS-Zertifikat", "hard": "Härtung",
             "dns": "DNS", "vlan": "VLAN", "ipv6": "IPv6", "nicmode": "Netzwerkport"}
    UNSUPPORTED = re.compile(r"PropertyUnknown|PropertyNotWritable|NotSupported|not supported|ActionNotSupported|"
                             r"PropertyValueNotInList|ResourceNotFound|InsufficientPrivilege", re.I)

    def __init__(self, rows, cur_user, services, dry=True, log=print, stopped=lambda: False, vendor=None):
        super().__init__(dry, log, stopped)
        self.rows, self.cur_user_set, self.services = rows, cur_user, services
        self.force_vendor = vendor

    def one(self, label, ip, pw, method, path, *bodies, accept_drop=False):
        """Schickt die JSON-Varianten der Reihe nach, die erste erfolgreiche zaehlt. accept_drop: ein
        Verbindungsabriss (0) gilt als Erfolg (manche BMCs starten beim Zertifikat-Import neu)."""
        for body in bodies or (None,):
            self.code = self.api(ip, self.cur_user, pw, method, path, body)
            if self.ok(self.code):
                self.log(T("  {0}: gesetzt").format(label), "ok")
                return True
        if accept_drop and self.code == 0:
            self.log(T("  {0}: gesendet, die Verbindung brach dabei ab (der BMC startet oft neu).").format(label), "ok")
            return True
        self.fail(label, self.code)
        return False

    # ---- je Dienst: True = gesetzt, None = auf diesem Hersteller nicht unterstuetzt, False = Fehler ----
    def svc_ntp(self, ip, pw, v, cfg):
        servers = [s.strip() for s in cfg.get("servers", []) if s.strip()][:3]
        tz = cfg.get("timezone", "").strip()
        if v == "dell":
            attrs = {"NTPConfigGroup.1.NTPEnable": "Enabled"}
            for i, srv in enumerate(servers, 1):
                attrs["NTPConfigGroup.1.NTP%d" % i] = srv
            if tz:
                attrs["Time.1.Timezone"] = tz
            return self.one(T("NTP"), ip, pw, "PATCH",
                            "/redfish/v1/Managers/iDRAC.Embedded.1/Attributes", {"Attributes": attrs})
        if v == "hpe":
            ok = self.one(T("NTP"), ip, pw, "PATCH", "/redfish/v1/Managers/1/DateTime",
                          {"StaticNTPServers": servers or [""]})
            if ok and tz:
                self.log(T("    Zeitzone bei HPE bitte in der iLO-Oberfläche setzen (hier nicht gesetzt)."))
            return ok
        if v in ("smc", "lenovo"):
            ok = self.one(T("NTP"), ip, pw, "PATCH", "/redfish/v1/Managers/1/NetworkProtocol",
                          {"NTP": {"ProtocolEnabled": True, "NTPServers": servers}})
            if ok and tz:
                self.log(T("    Zeitzone bei diesem Hersteller bitte in der Oberfläche setzen (hier nicht gesetzt)."))
            return ok
        return None

    def svc_syslog(self, ip, pw, v, cfg):
        server = cfg.get("server", "").strip()
        port = int(cfg.get("port") or 514)
        if v == "dell":
            return self.one(T("Syslog"), ip, pw, "PATCH", "/redfish/v1/Managers/iDRAC.Embedded.1/Attributes",
                            {"Attributes": {"SysLog.1.SysLogEnable": "Enabled",
                                            "SysLog.1.Server1": server, "SysLog.1.Port": str(port)}})
        if v == "hpe":
            return self.one(T("Syslog"), ip, pw, "PATCH", "/redfish/v1/Managers/1/NetworkProtocol",
                            {"Oem": {"Hpe": {"RemoteSyslogEnabled": True,
                                             "RemoteSyslogServer": server, "RemoteSyslogPort": port}}})
        return None          # Supermicro/Lenovo: kein einheitlicher Redfish-Weg

    def svc_ldap(self, ip, pw, v, cfg):
        search = {"BaseDistinguishedNames": [cfg.get("base", "").strip()]}
        if cfg.get("user_attr", "").strip():
            search["UsernameAttribute"] = cfg["user_attr"].strip()
        if cfg.get("group_attr", "").strip():
            search["GroupNameAttribute"] = cfg["group_attr"].strip()
        ldap = {"ServiceEnabled": True, "LDAPService": {"SearchSettings": search}}
        if cfg.get("bind_dn", "").strip():
            ldap["Authentication"] = {"AuthenticationType": "UsernameAndPassword",
                                      "Username": cfg["bind_dn"].strip(), "Password": cfg.get("bind_pw", "")}
        groups = [(g.strip(), (r.strip() or "Administrator")) for g, r in cfg.get("groups", []) if g.strip()]
        if groups:
            ldap["RemoteRoleMapping"] = [{"RemoteGroup": g, "LocalRole": r} for g, r in groups]
        host = cfg.get("server", "").strip()
        port = int(cfg.get("port") or 636)
        bodies = [{"LDAP": dict(ldap, ServiceAddresses=["%s:%d" % (host, port)])},
                  {"LDAP": dict(ldap, ServiceAddresses=["ldap://%s:%d" % (host, port)])}]
        return self.one(T("LDAP"), ip, pw, "PATCH", "/redfish/v1/AccountService", *bodies)

    def svc_cert(self, ip, pw, v, cfg):
        pem = cfg.get("pem", "")
        if not pem.strip():
            return None
        if v == "dell":
            path = ("/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DelliDRACCardService"
                    "/Actions/DelliDRACCardService.ImportSSLCertificate")
            return self.one(T("HTTPS-Zertifikat"), ip, pw, "POST", path,
                            {"CertificateType": "Server", "SSLCertificateFile": pem},
                            {"SSLCertType": "Server", "SSLCertificateFile": pem}, accept_drop=True)
        if v == "hpe":
            path = "/redfish/v1/Managers/1/SecurityService/HttpsCert/Actions/HpeHttpsCert.ImportCertificate"
            return self.one(T("HTTPS-Zertifikat"), ip, pw, "POST", path, {"Certificate": pem}, accept_drop=True)
        path = "/redfish/v1/Managers/1/SecurityService/HttpsCert/Actions/HttpsCert.ImportServerCertificate"
        return self.one(T("HTTPS-Zertifikat"), ip, pw, "POST", path, {"Certificate": pem}, accept_drop=True)

    def soft(self, label, ip, pw, method, path, body):
        """Wie one(), aber eine Einstellung, die der BMC nicht kennt, gilt als „nicht unterstützt“ (None)
        statt als Fehler. Rückgabe True / None / False."""
        self.code = self.api(ip, self.cur_user, pw, method, path, body)
        if self.ok(self.code):
            self.log(T("  {0}: gesetzt").format(label), "ok")
            return True
        if self.code in (404, 405, 501) or (self.code == 400 and self.UNSUPPORTED.search(" ".join(self.errmsg()))):
            self.log(T("  {0}: auf diesem Gerät nicht unterstützt, übersprungen").format(label))
            return None
        self.fail(label, self.code)
        return False

    IPV6_MODE_LABEL = {"disabled": "Aus", "slaac": "Automatisch (SLAAC)", "dhcpv6": "DHCPv6", "static": "Statisch"}

    def svc_dns(self, ip, pw, v, cfg):
        """DNS-Server am Management-Interface. Standard-Redfish (StaticNameServers); bei Dell zusätzlich
        über die Attribute-API, falls das Interface selbst es nicht annimmt."""
        servers = [s.strip() for s in cfg.get("servers", []) if s.strip()][:2]
        if not servers:
            return None
        nic = self.ETH_PATH.get(v)
        res = self.soft(T("DNS"), ip, pw, "PATCH", nic, {"StaticNameServers": servers}) if nic else None
        if res is not None or v != "dell":
            return res
        return self.soft(T("DNS"), ip, pw, "PATCH", self.MGR_PATH["dell"] + "/Attributes",
                         {"Attributes": {"NIC.1.DNSServer1": servers[0],
                                         "NIC.1.DNSServer2": servers[1] if len(servers) > 1 else ""}})

    def svc_vlan(self, ip, pw, v, cfg):
        """VLAN-Tag am Management-Interface, über das Standard-Redfish-VLAN-Objekt. ID 0 = VLAN abschalten."""
        nic = self.ETH_PATH.get(v)
        if not nic:
            return None
        vid = int(cfg.get("id") or 0)
        body = {"VLAN": {"VLANEnable": True, "VLANId": vid}} if vid else {"VLAN": {"VLANEnable": False}}
        return self.soft(T("VLAN"), ip, pw, "PATCH", nic, body)

    def svc_ipv6(self, ip, pw, v, cfg):
        """IPv6 am Management-Interface: aus, SLAAC, DHCPv6 oder statisch (DMTF-Standardeigenschaften
        IPv6AutoConfigEnabled/DHCPv6/IPv6StaticAddresses)."""
        nic = self.ETH_PATH.get(v)
        if not nic:
            return None
        mode = cfg.get("mode") or "disabled"
        if mode == "static":
            addr = cfg.get("address", "").strip()
            if not addr:
                return None
            body = {"IPv6AutoConfigEnabled": False, "DHCPv6": {"OperatingMode": "Disabled"},
                    "IPv6StaticAddresses": [{"Address": addr, "PrefixLength": int(cfg.get("prefix") or 64)}]}
            gw = cfg.get("gateway", "").strip()
            if gw:
                body["IPv6DefaultGateway"] = gw
        elif mode == "dhcpv6":
            body = {"IPv6AutoConfigEnabled": False, "DHCPv6": {"OperatingMode": "Stateful"}}
        elif mode == "slaac":
            body = {"IPv6AutoConfigEnabled": True, "DHCPv6": {"OperatingMode": "Disabled"}}
        else:
            body = {"IPv6AutoConfigEnabled": False, "DHCPv6": {"OperatingMode": "Disabled"},
                    "IPv6StaticAddresses": []}
        return self.soft(T("IPv6"), ip, pw, "PATCH", nic, body)

    def svc_nicmode(self, ip, pw, v, cfg):
        """Eigener Management-Port (Dedicated) oder gemeinsam mit LOM/OCP (Shared). Nicht einheitlich über
        Standard-Redfish lösbar; bislang nur für Dell (iDRAC-Attribut NIC.1.NicSelection) umgesetzt, bei den
        übrigen Herstellern bitte in der Weboberfläche umstellen (Hinweis im Protokoll)."""
        mode = cfg.get("mode") or "dedicated"
        if v == "dell":
            value = "Dedicated" if mode == "dedicated" else "LOM1"
            return self.soft(T("Netzwerkport"), ip, pw, "PATCH", self.MGR_PATH["dell"] + "/Attributes",
                             {"Attributes": {"NIC.1.NicSelection": value}})
        self.log(T("  Netzwerkport (Shared/Dedicated): bei diesem Hersteller bitte in der Oberfläche umstellen."))
        return None

    def svc_hard(self, ip, pw, v, cfg):
        """Härtung über Standard-Redfish: unnötige Netzwerkdienste aus, Kontosperre, Session-Zeitlimit."""
        mgr = self.MGR_PATH.get(v, "/redfish/v1/Managers/1")
        results = []
        for proto, label in (("IPMI", T("IPMI über LAN aus")), ("SSDP", T("SSDP aus")), ("SNMP", T("SNMP aus"))):
            if cfg.get(proto.lower()):
                results.append(self.soft(T("Härtung: {0}").format(label), ip, pw, "PATCH", mgr + "/NetworkProtocol",
                                         {proto: {"ProtocolEnabled": False}}))
        n, minutes = int(cfg.get("lockout") or 0), int(cfg.get("lockout_min") or 0)
        if n > 0 and minutes > 0:
            secs = minutes * 60
            results.append(self.soft(T("Härtung: Kontosperre nach {0} Fehlversuchen für {1} min").format(n, minutes),
                                     ip, pw, "PATCH", "/redfish/v1/AccountService",
                                     {"AccountLockoutThreshold": n, "AccountLockoutDuration": secs,
                                      "AccountLockoutCounterResetAfter": secs}))
        tmo = int(cfg.get("timeout_min") or 0)
        if tmo > 0:
            results.append(self.soft(T("Härtung: Session-Zeitlimit {0} min").format(tmo), ip, pw, "PATCH",
                                     "/redfish/v1/SessionService", {"SessionTimeout": max(30, min(86400, tmo * 60))}))
        tls_key = cfg.get("tls_min") or ""
        _TLS_LABEL = {"tls12": "TLS 1.2 and Higher", "tls13": "TLS 1.3 and Higher"}
        if tls_key in _TLS_LABEL:
            tls_val = _TLS_LABEL[tls_key]
            # Dell iDRAC: Attribute-API; für andere Hersteller soft() – überspringt bei 404/405/501
            mgr_attrs = mgr + "/Attributes"
            results.append(self.soft(T("Härtung: TLS-Mindestversion {0}").format(tls_val),
                                     ip, pw, "PATCH", mgr_attrs,
                                     {"Attributes": {"Security.1.TLSProtocol": tls_val}}))
        if False in results:
            return False
        return True if True in results else None

    def run(self):
        """Gibt True zurueck, wenn alle Server ohne Fehler durchliefen."""
        order = [("ntp", self.svc_ntp), ("syslog", self.svc_syslog), ("ldap", self.svc_ldap), ("cert", self.svc_cert),
                 ("dns", self.svc_dns), ("vlan", self.svc_vlan), ("ipv6", self.svc_ipv6),
                 ("nicmode", self.svc_nicmode), ("hard", self.svc_hard)]
        active = [(k, fn) for k, fn in order if k in self.services]
        done, failed = [], []
        for ip, row_pw in self.rows:
            if self.stopped():
                self.log(T("== Abgebrochen, übrige Server nicht bearbeitet"), "err")
                failed.append(T("Abbruch"))
                break
            if not row_pw:
                self.log(T('== {0}: kein Passwort').format(ip), "err")
                failed.append(ip)
                continue
            self.log("== " + ip, "head")
            v = self.force_vendor or self.vendor(ip)
            if self.dry and v not in self.NAME and v != "hpe_old":
                self.log(T('  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Dell-Ablauf').format(v))
                v = "dell"
            if v == "offline":
                self.log(T("  FEHLER: nicht erreichbar"), "err")
                failed.append(ip)
                continue
            if v == "hpe_old":
                self.log(T("  iLO 3/4: Dienste werden hier nicht unterstützt, übersprungen."), "err")
                failed.append(ip)
                continue
            if v not in self.NAME:
                self.log(T("  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)"), "err")
                self.unknown_hint()
                failed.append(ip)
                continue
            self.cur_user = self.cur_user_set or self.DEFAULT_USER[v]
            self.log(T('  Hersteller: {0}, Anmeldung als {1}').format(self.NAME[v], self.cur_user))
            self.backup(ip, self.cur_user, row_pw, v, "dienste")
            bad = False
            for key, fn in active:
                res = fn(ip, row_pw, v, self.services[key])
                if res is None:
                    self.log(T('  {0}: auf diesem Hersteller nicht unterstützt, übersprungen').format(T(self.LABEL[key])))
                elif res is False:
                    bad = True
            (failed if bad else done).append(ip)
        self.log("")
        summary = T('Fertig: {0} Server ohne Fehler, {1} mit Fehlern').format(len(done), len(failed))
        self.log(summary + (": " + " ".join(failed) if failed else ""), "err" if failed else "ok")
        return not failed




class UsersEngine(RedfishClient):
    """Benutzerverwaltung über Redfish AccountService: Konten eines Geräts lesen/anlegen/löschen, Rolle,
    Aktiv-Status und Passwort setzen, SSH-Public-Keys je Konto (DMTF-Standard ManagerAccount „Keys“, auf
    echter Hardware bislang ungeprüft – deshalb über soft(), das 404/405/501 als „nicht unterstützt“ statt
    als Fehler behandelt). iLO 3/4 kennt weder Rollen noch SSH-Keys über seine Schnittstelle (RIBCL); dort
    sind nur Konten anlegen/löschen/Passwort setzen möglich. Für die Passwort-Rotation arbeitet run() wie
    ServicesEngine die Serverliste ab."""

    ACCOUNTS = "/redfish/v1/AccountService/Accounts"
    ROLES = ("Administrator", "Operator", "ReadOnly")
    PRIVS = "".join(f'<{p} value="Yes"/>' for p in ("ADMIN_PRIV", "REMOTE_CONS_PRIV", "RESET_SERVER_PRIV",
                                                     "VIRTUAL_MEDIA_PRIV", "CONFIG_ILO_PRIV"))
    UNSUPPORTED = re.compile(r"PropertyUnknown|PropertyNotWritable|NotSupported|not supported|ActionNotSupported|"
                             r"PropertyValueNotInList|ResourceNotFound|InsufficientPrivilege", re.I)

    def __init__(self, rows=None, cur_user="", dry=True, log=print, stopped=lambda: False, vendor=None):
        super().__init__(dry, log, stopped)
        self.rows, self.cur_user_set = rows or [], cur_user
        self.force_vendor = vendor

    def soft(self, label, ip, user, pw, method, path, body=None):
        """Wie api(), aber eine Einstellung, die der BMC nicht kennt, gilt als „nicht unterstützt“ (None)
        statt als Fehler. Rückgabe True / None / False."""
        code = self.api(ip, user, pw, method, path, body)
        if self.ok(code):
            self.log(T("  {0}: gesetzt").format(label), "ok")
            return True
        if code in (404, 405, 501) or (code == 400 and self.UNSUPPORTED.search(" ".join(self.errmsg()))):
            self.log(T("  {0}: auf diesem Gerät nicht unterstützt, übersprungen").format(label))
            return None
        self.fail(label, code)
        return False

    # ---------- Konten lesen ----------
    def list_accounts(self, ip, user, pw, v):
        """[{"path","username","role","enabled","locked","keys_path","ssh_keys"}] oder None bei Fehler.
        Leere Dell-Slots (kein Benutzername) werden nicht aufgeführt."""
        if v == "hpe_old":
            return self._list_accounts_ribcl(ip, user, pw)
        if not self.ok(self.api(ip, user, pw, "GET", self.ACCOUNTS)) or not isinstance(self.body, dict):
            return None
        out = []
        for m in self.body.get("Members") or []:
            path = m.get("@odata.id") if isinstance(m, dict) else None
            if not path or not self.ok(self.api(ip, user, pw, "GET", path)) or not isinstance(self.body, dict):
                continue
            b = self.body
            name = str(b.get("UserName") or "")
            if not name:
                continue
            keys = b.get("Keys")
            keys_path = keys.get("@odata.id") if isinstance(keys, dict) else None
            out.append({"path": path, "username": name, "role": str(b.get("RoleId") or ""),
                       "enabled": bool(b.get("Enabled", True)), "locked": bool(b.get("Locked", False)),
                       "keys_path": keys_path, "ssh_keys": self._count_ssh_keys(ip, user, pw, keys_path)})
        return out

    def _count_ssh_keys(self, ip, user, pw, keys_path):
        if not keys_path or not self.ok(self.api(ip, user, pw, "GET", keys_path)) or not isinstance(self.body, dict):
            return 0
        return len(self.body.get("Members") or [])

    def _list_accounts_ribcl(self, ip, user, pw):
        code = self.ribcl(ip, user, pw, "read", "USER_INFO", "<GET_ALL_USERS/>")
        if not self.ok(code):
            return None
        logins = re.findall(r'USER_LOGIN\s+VALUE\s*=\s*"([^"]+)"', self.xml)
        return [{"path": login, "username": login, "role": "Administrator", "enabled": True, "locked": False,
                "keys_path": None, "ssh_keys": 0} for login in logins]

    def _own_account_path(self, ip, user, pw, v):
        """Pfad des Kontos, mit dem gerade angemeldet ist (für die Passwort-Rotation)."""
        if not self.ok(self.api(ip, user, pw, "GET", self.ACCOUNTS)) or not isinstance(self.body, dict):
            return None
        for m in self.body.get("Members") or []:
            path = m.get("@odata.id") if isinstance(m, dict) else None
            if path and self.ok(self.api(ip, user, pw, "GET", path)) and isinstance(self.body, dict) \
                    and str(self.body.get("UserName") or "").lower() == user.lower():
                return path
        return None

    # ---------- Konten ändern ----------
    def create_account(self, ip, user, pw, v, new_user, new_pass, role="Administrator"):
        if v == "hpe_old":
            code = self.ribcl(ip, user, pw, "write", "USER_INFO",
                              f"<ADD_USER USER_NAME={xq(new_user)} USER_LOGIN={xq(new_user)} "
                              f"PASSWORD={xq(new_pass)}>{self.PRIVS}</ADD_USER>")
        elif v == "dell":
            if not self.ok(self.api(ip, user, pw, "GET", self.ACCOUNTS)) or not isinstance(self.body, dict):
                self.fail(T("Konten lesen"), self.code)
                return False
            empty = None
            for m in self.body.get("Members") or []:
                path = m.get("@odata.id") if isinstance(m, dict) else None
                if path and self.ok(self.api(ip, user, pw, "GET", path)) and isinstance(self.body, dict) \
                        and not self.body.get("UserName"):
                    empty = path
                    break
            if not empty:
                self.log(T("  FEHLER: kein freier Benutzer-Slot mehr frei."), "err")
                return False
            code = self.api(ip, user, pw, "PATCH", empty,
                           {"UserName": new_user, "Password": new_pass, "RoleId": role, "Enabled": True})
        else:
            code = self.api(ip, user, pw, "POST", self.ACCOUNTS,
                           {"UserName": new_user, "Password": new_pass, "RoleId": role, "Enabled": True})
        if self.ok(code):
            self.log(T('  Benutzer {0} angelegt').format(new_user), "ok")
            return True
        self.fail(T("Benutzer anlegen"), code)
        return False

    def delete_account(self, ip, user, pw, v, account):
        """account: Redfish-Pfad des Kontos, bei iLO 3/4 der Login-Name."""
        if v == "hpe_old":
            code = self.ribcl(ip, user, pw, "write", "USER_INFO", f"<DELETE_USER USER_LOGIN={xq(account)}/>")
        elif v == "dell":
            # Dell-Slots lassen sich nicht löschen, nur leeren und abschalten
            code = self.api(ip, user, pw, "PATCH", account, {"UserName": "", "Enabled": False})
        else:
            code = self.api(ip, user, pw, "DELETE", account)
        if self.ok(code):
            self.log(T('  Benutzer {0} gelöscht').format(account), "ok")
            return True
        self.fail(T("Benutzer löschen"), code)
        return False

    def set_role(self, ip, user, pw, v, account, role):
        if v == "hpe_old":
            self.log(T("  Rolle: iLO 3/4 kennt nur eine feste Rechtemenge, nichts geändert."))
            return None
        code = self.api(ip, user, pw, "PATCH", account, {"RoleId": role})
        if self.ok(code):
            self.log(T('  Rolle auf {0} gesetzt').format(role), "ok")
            return True
        self.fail(T("Rolle ändern"), code)
        return False

    def set_enabled(self, ip, user, pw, v, account, enabled):
        if v == "hpe_old":
            self.log(T("  Aktiv/Inaktiv: bei iLO 3/4 bitte das Konto löschen oder das Passwort ändern."))
            return None
        code = self.api(ip, user, pw, "PATCH", account, {"Enabled": bool(enabled)})
        if self.ok(code):
            self.log(T('  Konto {0}').format(T("aktiviert") if enabled else T("deaktiviert")), "ok")
            return True
        self.fail(T("Aktiv/Inaktiv setzen"), code)
        return False

    def rotate_password(self, ip, user, pw, v, account, new_pass, login_name=""):
        """account: Redfish-Pfad, bei iLO 3/4 der Login-Name."""
        if v == "hpe_old":
            code = self.ribcl(ip, user, pw, "write", "USER_INFO",
                              f"<MOD_USER USER_LOGIN={xq(account)}><PASSWORD value={xq(new_pass)}/></MOD_USER>")
        else:
            code = self.api(ip, user, pw, "PATCH", account, {"Password": new_pass})
        if self.ok(code):
            self.log(T('  Passwort von {0} neu gesetzt').format(login_name or account), "ok")
            return True
        self.fail(T("Passwort setzen"), code)
        return False

    # ---------- SSH-Public-Keys (DMTF-Standard, ungeprüft außerhalb von Tests) ----------
    def list_ssh_keys(self, ip, user, pw, keys_path):
        """[{"path","key"}] vorhandener SSH-Keys dieses Kontos, oder [] (keine oder nicht unterstützt)."""
        if not keys_path or not self.ok(self.api(ip, user, pw, "GET", keys_path)) or not isinstance(self.body, dict):
            return []
        out = []
        for m in self.body.get("Members") or []:
            path = m.get("@odata.id") if isinstance(m, dict) else None
            if path and self.ok(self.api(ip, user, pw, "GET", path)) and isinstance(self.body, dict):
                out.append({"path": path, "key": str(self.body.get("KeyString") or "")})
        return out

    def add_ssh_key(self, ip, user, pw, keys_path, pubkey):
        """Legt einen neuen SSH-Public-Key am Konto an. None = dieser Hersteller/diese Firmware kennt das
        (noch) nicht (404/405/501), kein Fehler."""
        if not keys_path:
            self.log(T("  SSH-Key: auf diesem Gerät nicht unterstützt, übersprungen"))
            return None
        return self.soft(T("SSH-Key"), ip, user, pw, "POST", keys_path, {"KeyType": "SSH", "KeyString": pubkey})

    def delete_ssh_key(self, ip, user, pw, key_path):
        if not key_path:
            return None
        return self.soft(T("SSH-Key löschen"), ip, user, pw, "DELETE", key_path)

    # ---------- Passwort-Rotation über die ganze Serverliste ----------
    def run(self):
        """Rotiert das Passwort des aktuell angemeldeten Benutzers auf allen Servern der Liste; je Server ein
        neues Zufallspasswort (Engine.random_password). Gibt (ok, {Adresse: neues_Passwort}) zurück; die
        neuen Passwörter sind NUR hier im Rückgabewert und im Protokoll – nirgends sonst gespeichert."""
        done, failed, new_pw = [], [], {}
        for ip, row_pw in self.rows:
            if self.stopped():
                self.log(T("== Abgebrochen, übrige Server nicht bearbeitet"), "err")
                failed.append(T("Abbruch"))
                break
            if not row_pw:
                self.log(T('== {0}: kein Passwort').format(ip), "err")
                failed.append(ip)
                continue
            self.log("== " + ip, "head")
            v = self.force_vendor or self.vendor(ip)
            if self.dry and v not in self.NAME:
                self.log(T('  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Dell-Ablauf').format(v))
                v = "dell"
            if v == "offline":
                self.log(T("  FEHLER: nicht erreichbar"), "err")
                failed.append(ip)
                continue
            if v not in self.NAME:
                self.log(T("  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)"), "err")
                self.unknown_hint()
                failed.append(ip)
                continue
            user = self.cur_user_set or self.DEFAULT_USER[v]
            self.log(T('  Hersteller: {0}, Anmeldung als {1}').format(self.NAME[v], user))
            self.backup(ip, user, row_pw, v, "rotation")
            account = user if v == "hpe_old" else self._own_account_path(ip, user, row_pw, v)
            if account is None:
                self.log(T("  FEHLER: eigenes Konto nicht gefunden"), "err")
                failed.append(ip)
                continue
            pw2 = Engine.random_password()
            if self.rotate_password(ip, user, row_pw, v, account, pw2, login_name=user):
                new_pw[ip] = pw2
                done.append(ip)
            else:
                failed.append(ip)
        self.log("")
        summary = T('Fertig: {0} Server ohne Fehler, {1} mit Fehlern').format(len(done), len(failed))
        self.log(summary + (": " + " ".join(failed) if failed else ""), "err" if failed else "ok")
        return not failed, new_pw




class FirmwareEngine(RedfishClient):
    """Firmware-Updates über den Redfish-UpdateService: liest das Firmware-Inventar, lädt eine Datei
    per Multipart-/HTTP-Push hoch oder stößt ein SimpleUpdate von einer URL an und verfolgt den Task
    bis zum Ende. Arbeitet die Serverliste der Reihe nach ab. Dieser Vorgang ist heikel - Trockenlauf
    ist Vorgabe, und ein Fehlschlag kann einen BMC unbrauchbar machen; immer erst ein Einzelgerät."""

    US = "/redfish/v1/UpdateService"

    def __init__(self, rows, cur_user, cfg, dry=True, log=print, stopped=lambda: False, vendor=None):
        super().__init__(dry, log, stopped)
        self.rows, self.cur_user_set, self.cfg = rows, cur_user, cfg
        self.force_vendor = vendor

    # ---------- Rohzugriff für Datei-Upload (api() schickt nur JSON) ----------
    def raw(self, ip, user, pw, method, path, body=None, ctype=None, timeout=600):
        """Wie api(), aber mit beliebigem Body (z. B. Multipart). Gibt (code, location) zurück; die
        JSON-Antwort steht in self.body. location ist der Task-Pfad aus dem Location-Header."""
        self.body = None
        mode = self.tls_mode(ip, min(timeout, 10))
        if mode is None:
            return 0, None
        for attempt in (1, 2):
            name, value = self.auth_header(ip, user, pw, mode)
            if name is None:
                return value, None
            req = urllib.request.Request(f"https://{ip}{path}", method=method, data=body)
            if ctype:
                req.add_header("Content-Type", ctype)
            req.add_header(name, value)
            try:
                with self.opener(mode).open(req, timeout=timeout) as r:
                    code, loc, raw = r.status, r.headers.get("Location"), r.read()
            except urllib.error.HTTPError as e:
                code, loc, raw = e.code, e.headers.get("Location"), e.read()
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                code, loc, raw = 0, None, b""
            if code == 401 and name == "X-Auth-Token" and attempt == 1:
                self.sessions.pop((addr_key(ip), user), None)
                continue
            break
        self.audit(ip, method, path, code, f"<{len(body or b'')} Bytes, {ctype or 'ohne Typ'}>")
        if code == 0:
            return 0, None
        try:
            self.body = json.loads(raw) if raw else None
        except ValueError:
            self.body = None
        return code, loc

    def inventory(self, ip, user, pw):
        """Liste der Firmware-Komponenten: [{id, name, version, updateable}] oder None."""
        self.code = self.api(ip, user, pw, "GET", self.US + "/FirmwareInventory")
        if not self.ok(self.code) or not isinstance(self.body, dict):
            return None
        out = []
        for m in self.body.get("Members") or []:
            path = m.get("@odata.id") if isinstance(m, dict) else None
            if path and self.ok(self.api(ip, user, pw, "GET", path)) and isinstance(self.body, dict):
                b = self.body
                out.append({"id": b.get("Id") or path.rsplit("/", 1)[-1],
                            "name": b.get("Name") or "", "version": b.get("Version") or "",
                            "updateable": bool(b.get("Updateable")), "path": path})
        return out

    def us_doc(self, ip, user, pw):
        return self.body if self.ok(self.api(ip, user, pw, "GET", self.US)) and isinstance(self.body, dict) else None

    @staticmethod
    def _multipart(params, filename, data):
        """application/json-Teil „UpdateParameters" + Datei-Teil „UpdateFile" nach Redfish."""
        boundary = "----vbmcBoundary%d" % int(time.monotonic() * 1000)
        pre = ("--%s\r\nContent-Disposition: form-data; name=\"UpdateParameters\"\r\n"
               "Content-Type: application/json\r\n\r\n%s\r\n"
               "--%s\r\nContent-Disposition: form-data; name=\"UpdateFile\"; filename=\"%s\"\r\n"
               "Content-Type: application/octet-stream\r\n\r\n" % (
                   boundary, json.dumps(params), boundary, filename)).encode()
        post = ("\r\n--%s--\r\n" % boundary).encode()
        return pre + data + post, "multipart/form-data; boundary=%s" % boundary

    def wait_task(self, ip, user, pw, task_path, timeout=1800):
        """Verfolgt den Update-Task bis zum Ende. True = fertig, False = Fehler/Zeit abgelaufen."""
        end = time.monotonic() + timeout
        last = -1
        while not self.stopped():
            code = self.api(ip, user, pw, "GET", task_path, timeout=30)
            if not self.ok(code) or not isinstance(self.body, dict):
                # Manche BMCs sind während des BMC-Flashs kurz weg; weiter versuchen
                if time.monotonic() >= end:
                    self.log(T("  FEHLER: Task nicht mehr lesbar (HTTP {0})").format(code or T("keine Verbindung")), "err")
                    return False
                self.pause(5)
                continue
            state = self.body.get("TaskState") or ""
            pct = self.body.get("PercentComplete")
            if pct != last and pct is not None:
                self.log(T("    {0} … {1}%").format(state, pct))
                last = pct
            if state in ("Completed",):
                return True
            if state in ("Exception", "Killed", "Cancelled", "Interrupted"):
                self.log_lines([T("  FEHLER: Update fehlgeschlagen ({0})").format(state)] + self.errmsg())
                return False
            if time.monotonic() >= end:
                self.log(T("  FEHLER: Zeitüberschreitung beim Update"), "err")
                return False
            self.pause(5)
        return False

    def start_update(self, ip, user, pw, us, apply_time, targets):
        """Lädt die Datei hoch bzw. stößt SimpleUpdate an. Gibt den Task-Pfad oder None zurück."""
        src = self.cfg.get("source")
        params = {"@Redfish.OperationApplyTime": apply_time}
        if targets:
            params["Targets"] = targets
        if src == "uri":
            uri = self.cfg.get("uri", "").strip()
            data = {"ImageURI": uri, "TransferProtocol": self.cfg.get("proto", "HTTP"),
                    "@Redfish.OperationApplyTime": apply_time}
            if targets:
                data["Targets"] = targets
            code, loc = self.raw(ip, user, pw, "POST", self.US + "/Actions/UpdateService.SimpleUpdate",
                                 json.dumps(data).encode(), "application/json", timeout=120)
        else:
            path = Path(self.cfg.get("file", ""))
            data = getattr(self, "fw_data", None)
            if data is None:
                try:
                    data = path.read_bytes()
                except OSError as e:
                    self.log(T("  FEHLER: Datei nicht lesbar: {0}").format(e), "err")
                    return None
            mp_uri = us.get("MultipartHttpPushUri")
            if mp_uri:
                body, ctype = self._multipart(params, path.name, data)
                code, loc = self.raw(ip, user, pw, "POST", mp_uri, body, ctype)
            else:                                   # einfacher Push: nur die Rohdatei
                push = us.get("HttpPushUri") or (self.US + "/update")
                code, loc = self.raw(ip, user, pw, "POST", push, data, "application/octet-stream")
        if not self.ok(code):
            self.fail(T("Update starten"), code)
            return None
        # Task-Pfad aus Location-Header oder Body
        if loc:
            return loc
        if isinstance(self.body, dict):
            return self.body.get("@odata.id") or (
                "/redfish/v1/TaskService/Tasks/" + str(self.body["Id"]) if self.body.get("Id") else None)
        return None

    def apply_row(self, ip, pw, vendor):
        user = self.cur_user_set or self.DEFAULT_USER[vendor]
        self.cur_user = user
        apply_time = "Immediate" if self.cfg.get("apply", "immediate") == "immediate" else "OnReset"
        targets = self.cfg.get("targets") or []
        src = self.cfg.get("source")
        label = (T("URL {0}").format(self.cfg.get("uri", "")) if src == "uri"
                 else T("Datei {0}").format(Path(self.cfg.get("file", "")).name))
        tgt = (", ".join(t.rsplit("/", 1)[-1] for t in targets) if targets else T("automatisch"))
        if not self.self_guard(ip, user, pw, vendor):
            return False
        if self.dry:
            self.log(T("  Trockenlauf: würde {0} einspielen (Ziel: {1}, Übernahme: {2})").format(
                label, tgt, apply_time))
            return True
        self.backup(ip, user, pw, vendor, "firmware")
        us = self.us_doc(ip, user, pw)
        if us is None:
            self.fail(T("UpdateService lesen"), self.code)
            return False
        before = {c["id"]: c["version"] for c in (self.inventory(ip, user, pw) or [])}
        self.log(T("  {0} wird eingespielt (Übernahme: {1}) …").format(label, apply_time))
        task = self.start_update(ip, user, pw, us, apply_time, targets)
        if not task:
            return False
        self.log(T("  Task: {0}").format(task))
        if not self.wait_task(ip, user, pw, task):
            return False
        after = {c["id"]: c["version"] for c in (self.inventory(ip, user, pw) or [])}
        changed = [T("{0}: {1} → {2}").format(k, before.get(k, "?"), v)
                   for k, v in after.items() if before.get(k) != v]
        if changed:
            self.log(T("  Firmware aktualisiert: {0}").format(", ".join(changed)), "ok")
        elif apply_time == "OnReset":
            self.log(T("  Hochgeladen, wird beim nächsten Neustart übernommen."), "ok")
        else:
            self.log(T("  Update abgeschlossen."), "ok")
        return True

    def check_source(self):
        """Prüft die Quelle einmal vor dem ersten Server: Datei lesen, SHA-256 bilden und mit dem Sollwert
        vergleichen. Die geprüften Bytes werden für alle Server verwendet (kein erneutes Lesen, das eine
        zwischenzeitlich ausgetauschte Datei einspielen könnte). Gibt False bei Abweichung zurück."""
        self.fw_data = None
        if self.cfg.get("source") == "uri":
            uri = self.cfg.get("uri", "")
            if uri.lower().startswith(("http://", "tftp://")) or self.cfg.get("proto") in ("HTTP", "TFTP"):
                self.log(T("Hinweis: Die Datei wird unverschlüsselt geholt; der BMC prüft die Signatur des "
                           "Pakets selbst. Wenn möglich HTTPS verwenden."))
            return True
        path = Path(self.cfg.get("file", ""))
        try:
            data = path.read_bytes()
        except OSError as e:
            self.log(T("FEHLER: Datei nicht lesbar: {0}").format(e), "err")
            return False
        digest = hashlib.sha256(data).hexdigest()
        self.log(T("Datei {0}: {1:.1f} MB, SHA-256 {2}").format(path.name, len(data) / 1048576, digest))
        want = re.sub(r"[^0-9a-f]", "", str(self.cfg.get("sha256") or "").lower())
        if want:
            if want != digest:
                self.log(T("FEHLER: Prüfsumme stimmt nicht mit dem Sollwert überein (erwartet {0}). Die Datei ist "
                           "beschädigt oder nicht die richtige. Es wurde nichts eingespielt.").format(want), "err")
                return False
            self.log(T("Prüfsumme stimmt mit dem Sollwert überein."), "ok")
        elif not self.dry:
            self.log(T("Hinweis: Kein Sollwert für die Prüfsumme angegeben; bitte mit der Angabe des Herstellers "
                       "vergleichen."))
        self.fw_data = data
        return True

    def run(self):
        done, failed = [], []
        if not self.check_source():
            self.log("")
            self.log(T('Fertig: {0} Server ohne Fehler, {1} mit Fehlern').format(0, len(self.rows)), "err")
            return False
        stop_on_error = self.cfg.get("stop_on_error", True) and not self.dry
        for ip, row_pw in self.rows:
            if self.stopped():
                self.log(T("== Abgebrochen, übrige Server nicht bearbeitet"), "err")
                failed.append(T("Abbruch"))
                break
            if failed and stop_on_error:
                self.log(T("== Angehalten nach dem ersten Fehler, übrige Server nicht bearbeitet "
                           "(„Bei Fehler anhalten“)."), "err")
                break
            if not row_pw:
                self.log(T('== {0}: kein Passwort').format(ip), "err")
                failed.append(ip)
                continue
            self.log("== " + ip, "head")
            v = self.force_vendor or self.vendor(ip)
            if self.dry and v not in self.NAME and v != "hpe_old":
                self.log(T('  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Dell-Ablauf').format(v))
                v = "dell"
            if v == "offline":
                self.log(T("  FEHLER: nicht erreichbar"), "err")
                failed.append(ip)
                continue
            if v == "hpe_old":
                self.log(T("  iLO 3/4: Firmware-Update hier nicht unterstützt (RIBCL), übersprungen."), "err")
                failed.append(ip)
                continue
            if v not in self.NAME:
                self.log(T("  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)"), "err")
                self.unknown_hint()
                failed.append(ip)
                continue
            self.log(T('  Hersteller: {0}').format(self.NAME[v]))
            (done if self.apply_row(ip, row_pw, v) else failed).append(ip)
        self.log("")
        summary = T('Fertig: {0} Server ohne Fehler, {1} mit Fehlern').format(len(done), len(failed))
        self.log(summary + (": " + " ".join(failed) if failed else ""), "err" if failed else "ok")
        return not failed


class ProbeEngine(RedfishClient):
    """Prüft nur lesend, ob sich Bestandsserver (schon konfigurierte BMCs) erreichen und anmelden lassen:
    Port, TLS-Stufe, Hersteller, Login, Modell, Firmware und ob das Konto Administratorrechte hat.
    Jeder Server bekommt genau einen Anmeldeversuch, damit ein falsches Passwort kein Konto sperrt."""

    MGR = {"dell": "/redfish/v1/Managers/iDRAC.Embedded.1"}        # sonst /redfish/v1/Managers/1
    TLS_NAME = {"modern": "TLS 1.2 oder neuer", "old": "veraltet (TLS 1.0/1.1 möglich)", "oldest": "nur TLS 1.0/1.1"}

    def __init__(self, rows, vendor=None, log=print, stopped=lambda: False, on_result=lambda ip, res: None):
        super().__init__(False, log, stopped)
        self.rows, self.force_vendor, self.on_result = rows, vendor, on_result

    def probe(self, ip, user, pw):
        res = {"ok": False, "vendor": "", "user": user, "model": "", "fw": "", "admin": None, "text": ""}

        def fail(text, hint=""):
            res["text"] = text
            self.log(T('  FEHLER: {0}').format(text), "err")
            if hint:
                self.log("    " + hint)
            return res

        host, port = hostport(ip)
        try:
            socket.create_connection((host, port), timeout=5).close()
        except OSError as e:
            return fail(T('Port {0} auf {1} nicht erreichbar ({2})').format(port, host, e.strerror or e),
                        T("Adresse und Port prüfen, außerdem Routing/Firewall zwischen diesem Rechner und dem BMC-Netz."))
        self.log(T('  Port {0}: offen').format(port), "ok")
        mode = self.tls_mode(ip)
        if mode is None:
            return fail(T("Kein HTTPS-Handshake möglich"),
                        T("Der BMC antwortet am Port, spricht aber kein HTTPS oder ist noch nicht bereit."))
        self.log(T('  Verschlüsselung: {0}').format(T(self.TLS_NAME[mode])), "ok" if mode == "modern" else "")
        v = self.force_vendor or self.vendor(ip)
        if v == "offline":
            return fail(T("nicht erreichbar"))
        if v not in self.NAME:
            self.unknown_hint()
            return fail(T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)"))
        res["vendor"] = v
        user = user or self.DEFAULT_USER[v]
        res["user"] = self.cur_user = user
        self.log(T('  Hersteller: {0}, Anmeldung als {1}').format(self.NAME[v], user))
        again = T("Nicht erneut versuchen, bevor Benutzer und Passwort geklärt sind: Mehrfache Fehlversuche können das Konto sperren.")
        if v == "hpe_old":
            code = self.ribcl(ip, user, pw, "read", "RIB_INFO", "<GET_FW_VERSION/>", timeout=30)
            if code == 401:
                return fail(T("Anmeldung abgelehnt (Benutzer oder Passwort falsch)"), again)
            if code != 200:
                return fail(T('keine brauchbare Antwort der XML-Schnittstelle (HTTP {0})').format(code or T("keine Verbindung")))
            fw = re.search(r'FIRMWARE_VERSION\s*=\s*"([^"]*)"', self.xml)
            mp = re.search(r'MANAGEMENT_PROCESSOR\s*=\s*"([^"]*)"', self.xml)
            res["fw"], res["model"] = fw.group(1) if fw else "", (mp.group(1) if mp else "") or self.model
            res["admin"] = self.ribcl(ip, user, pw, "read", "USER_INFO", "<GET_ALL_USERS/>", timeout=30) == 200
        else:
            code = self.api(ip, user, pw, "GET", self.MGR.get(v, "/redfish/v1/Managers/1"), timeout=20)
            if code == 401:
                return fail(T("Anmeldung abgelehnt (Benutzer oder Passwort falsch, oder Konto gesperrt)"), again)
            if code == 403:
                return fail(T("Anmeldung ok, aber das Konto darf den BMC nicht lesen (HTTP 403)"))
            if not self.ok(code) or not isinstance(self.body, dict):
                return fail(T('Anmeldung nicht möglich (HTTP {0})').format(code or T("keine Verbindung")))
            res["fw"] = str(self.body.get("FirmwareVersion") or "")
            res["model"] = str(self.body.get("Model") or "")
            if self.ok(self.api(ip, user, pw, "GET", self.sys_path(v), timeout=20)) and isinstance(self.body, dict):
                res["system"] = " ".join(str(self.body.get(k) or "") for k in ("Model", "SerialNumber")).strip()
                res["power"] = str(self.body.get("PowerState") or "")
            code = self.api(ip, user, pw, "GET", "/redfish/v1/AccountService/Accounts", timeout=20)
            res["admin"] = True if self.ok(code) else False if code == 403 else None
        res["ok"] = True
        self.log(T('  Anmeldung: ok'), "ok")
        self.log(T('  BMC: {0}, Firmware {1}').format(res["model"] or "?", res["fw"] or "?"))
        if res.get("system"):
            self.log(T('  Server: {0}, Stromzustand {1}').format(res["system"], res.get("power") or "?"))
        if res["admin"] is False:
            self.log(T("  Hinweis: Das Konto hat keine Administratorrechte, Konfigurieren wird scheitern."), "err")
        elif res["admin"] is None:
            self.log(T("  Hinweis: Administratorrechte nicht prüfbar."))
        return res

    def run(self):
        good = 0
        for ip, user, pw in self.rows:
            if self.stopped():
                self.log(T("== Abgebrochen, übrige Server nicht bearbeitet"), "err")
                break
            self.log("== " + ip, "head")
            try:
                res = self.probe(ip, user, pw)
            except Exception as e:
                res = {"ok": False, "text": repr(e)}
                self.log(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good += bool(res["ok"])
            self.on_result(ip, res)
        total = len(self.rows)
        self.log("")
        self.log(T('Fertig: {0} von {1} Servern erreichbar und angemeldet').format(good, total),
                 "ok" if good == total else "err")
        return good == total




class EngineThread(QThread):
    line = pyqtSignal(str, str)     # Text, Art (err/ok/head)
    row_done = pyqtSignal(str)      # bisherige IP eines fertig umgestellten Servers
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            good = Engine(log=lambda text, kind="": self.line.emit(text, kind),
                          stopped=self.isInterruptionRequested, on_done=self.row_done.emit, **self.kw).run()
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good = False
        self.result.emit(0 if good else 1)




class ProbeThread(QThread):
    line = pyqtSignal(str, str)
    row = pyqtSignal(str, object)       # Adresse, Ergebnis-Dict
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            good = ProbeEngine(log=lambda text, kind="": self.line.emit(text, kind),
                               stopped=self.isInterruptionRequested,
                               on_result=lambda ip, res: self.row.emit(ip, res), **self.kw).run()
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good = False
        self.result.emit(0 if good else 1)




class BiosEngineThread(QThread):
    line = pyqtSignal(str, str)
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            good = BiosEngine(log=lambda text, kind="": self.line.emit(text, kind),
                              stopped=self.isInterruptionRequested, **self.kw).run()
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good = False
        self.result.emit(0 if good else 1)




class ServicesEngineThread(QThread):
    line = pyqtSignal(str, str)
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            good = ServicesEngine(log=lambda text, kind="": self.line.emit(text, kind),
                                  stopped=self.isInterruptionRequested, **self.kw).run()
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good = False
        self.result.emit(0 if good else 1)




class UsersEngineThread(QThread):
    """Passwort-Rotation über die ganze Serverliste (UsersEngine.run())."""
    line = pyqtSignal(str, str)
    result = pyqtSignal(int)
    rotated = pyqtSignal(dict)      # Adresse -> neues Passwort, nur für erfolgreiche Server

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            good, new_pw = UsersEngine(log=lambda text, kind="": self.line.emit(text, kind),
                                       stopped=self.isInterruptionRequested, **self.kw).run()
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good, new_pw = False, {}
        self.rotated.emit(new_pw)
        self.result.emit(0 if good else 1)




class UsersListThread(QThread):
    """Liest einmalig die Kontenliste eines einzelnen Geräts, ändert nichts."""
    done = pyqtSignal(object, str, str)     # Liste oder None, Hersteller, Fehlertext

    def __init__(self, parent, ip, user, pw, vendor):
        super().__init__(parent)
        self.ip, self.user, self.pw, self.vendor = ip, user, pw, vendor

    def run(self):
        ue = UsersEngine(dry=False)
        try:
            v = self.vendor or ue.vendor(self.ip)
            if v == "offline":
                self.done.emit(None, v, T("Gerät nicht erreichbar."))
                return
            if v not in ue.NAME:
                self.done.emit(None, v, T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)."))
                return
            user = self.user or ue.DEFAULT_USER[v]
            accounts = ue.list_accounts(self.ip, user, self.pw, v)
            if accounts is None:
                self.done.emit(None, v, "\n".join(ue.fail_lines(T("Konten lesen"), ue.code)))
            else:
                self.done.emit(accounts, v, "")
        except Exception as e:
            self.done.emit(None, "", T('Unerwarteter Fehler: {0!r}').format(e))




class UsersActionThread(QThread):
    """Führt eine einzelne Kontenaktion auf einem Gerät aus (anlegen/löschen/Rolle/Passwort/SSH-Key). fn
    bekommt die (scharfe) UsersEngine übergeben und liefert True/False/None (None = nicht unterstützt,
    gilt als Erfolg ohne Änderung)."""
    line = pyqtSignal(str, str)
    result = pyqtSignal(object)

    def __init__(self, parent, fn, dry=False):
        super().__init__(parent)
        self.fn, self.dry = fn, dry

    def run(self):
        ue = UsersEngine(dry=self.dry, log=lambda text, kind="": self.line.emit(text, kind))
        try:
            res = self.fn(ue)
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            res = False
        self.result.emit(res)




class FirmwareEngineThread(QThread):
    line = pyqtSignal(str, str)
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            good = FirmwareEngine(log=lambda text, kind="": self.line.emit(text, kind),
                                  stopped=self.isInterruptionRequested, **self.kw).run()
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good = False
        self.result.emit(0 if good else 1)


class FirmwareReadThread(QThread):
    """Liest einmalig das Firmware-Inventar eines Referenzgeräts, ändert nichts."""
    done = pyqtSignal(object, str, str)     # Liste von Komponenten oder None, Hersteller, Fehlertext

    def __init__(self, parent, ip, user, pw, vendor):
        super().__init__(parent)
        self.ip, self.user, self.pw, self.vendor = ip, user, pw, vendor

    def run(self):
        fe = FirmwareEngine(rows=[], cur_user=self.user, cfg={}, dry=False)
        try:
            v = self.vendor or fe.vendor(self.ip)
            if v == "offline":
                self.done.emit(None, v, T("Gerät nicht erreichbar."))
                return
            if v == "hpe_old":
                self.done.emit(None, v, T("iLO 3/4: Firmware-Update wird hier nicht unterstützt."))
                return
            if v not in fe.NAME:
                self.done.emit(None, v, T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)."))
                return
            user = self.user or fe.DEFAULT_USER[v]
            inv = fe.inventory(self.ip, user, self.pw)
            if inv is None:
                self.done.emit(None, v, "\n".join(fe.fail_lines(T("Firmware-Inventar lesen"), fe.code)))
            else:
                self.done.emit(inv, v, "")
        except Exception as e:
            self.done.emit(None, "", T('Unerwarteter Fehler: {0!r}').format(e))
        finally:
            fe.close_sessions()


class BiosReadThread(QThread):
    """Liest einmalig die UEFI/BIOS-Attribute eines einzelnen Referenzgeräts, ändert nichts."""
    done = pyqtSignal(object, str, str)     # Attribute-Dict oder None, Hersteller, Fehlertext

    def __init__(self, parent, ip, user, pw, vendor):
        super().__init__(parent)
        self.ip, self.user, self.pw, self.vendor = ip, user, pw, vendor

    def run(self):
        be = BiosEngine(rows=[], cur_user=self.user, attrs={}, dry=False)
        try:
            v = self.vendor or be.vendor(self.ip)
            if v == "offline":
                self.done.emit(None, v, T("Gerät nicht erreichbar."))
                return
            if v not in be.NAME:
                self.done.emit(None, v, T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)."))
                return
            user = self.user or be.DEFAULT_USER[v]
            v = "hpe" if v == "hpe_old" else v      # iLO 4 ab Gen9 kann Redfish, iLO 3 scheitert beim Lesen
            attrs, _ = be.read_bios(self.ip, user, self.pw, v)
            if attrs is None:
                self.done.emit(None, v, "\n".join(be.fail_lines(T("BIOS-Attribute lesen"), be.code)))
            else:
                self.done.emit(attrs, v, "")
        except Exception as e:
            self.done.emit(None, "", T('Unerwarteter Fehler: {0!r}').format(e))
        finally:
            be.close_sessions()


class VendorProbeThread(QObject):
    """Ermittelt einmalig Hersteller/Typ einer Adresse, ohne Anmeldung (wie vendor()); läuft im Hintergrund,
    damit die Serverliste („Neuware“) beim Eintragen der Aktuelle-IP nicht blockiert.
    Bewusst ein Daemon-Thread statt QThread: Eine Abfrage an eine tote Adresse kann bis zur Zeitüberschreitung
    dauern, und ein noch laufender QThread bricht das Programm beim Beenden ab."""
    done = pyqtSignal(str, str, str)     # Aktuelle IP, Anzeigetext, Tooltip
    serial = pyqtSignal(str, str)        # Aktuelle IP, ausgelesene Seriennummer (nur wenn lesbar)

    def __init__(self, parent, ip, user="", pw=""):
        super().__init__(parent)
        self.ip = ip
        self.user, self.pw = user, pw
        self._t = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self._t.start()

    def isRunning(self):
        return self._t.is_alive()

    def wait(self, ms=0):
        self._t.join(ms / 1000 if ms else None)
        return not self._t.is_alive()

    def _emit(self, *args):
        try:
            self.done.emit(*args)
        except RuntimeError:            # Fenster schon geschlossen
            pass

    def run(self):
        rc = RedfishClient(dry=True, log=lambda *a, **k: None)
        try:
            v = rc.vendor(self.ip)
        except Exception as e:
            self._emit(self.ip, T("Fehler"), T('Unerwarteter Fehler: {0!r}').format(e))
            return
        if v == "offline":
            self._emit(self.ip, T("nicht erreichbar"),
                       T("Gerät antwortet nicht (Zeitüberschreitung oder Verbindung abgelehnt)."))
        elif v in RedfishClient.NAME:
            label = RedfishClient.NAME[v] + (f" ({rc.model})" if rc.model else "")
            self._emit(self.ip, label, label)
            reader = RedfishClient(dry=False, log=lambda *a, **k: None)
            try:
                serial = reader.read_serial(self.ip, v, self.user or RedfishClient.DEFAULT_USER[v], self.pw)
            finally:
                reader.close_sessions()
            if serial:
                try:
                    self.serial.emit(self.ip, serial)
                except RuntimeError:            # Fenster schon geschlossen
                    pass
        else:
            label = T("unbekannt") + (f" ({rc.model})" if rc.model else "")
            self._emit(self.ip, label, T("Weder Dell iDRAC, HPE iLO, Supermicro noch Lenovo XCC erkannt.")
                       + (f"\n{rc.model}" if rc.model else ""))
