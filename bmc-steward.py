#!/usr/bin/env python3
"""BMC-Steward: BMC-Erstkonfiguration für Dell iDRAC, HPE iLO, Supermicro und Lenovo XCC.

Neue Geräte laufen über Redfish. Ältere werden mitbedient: iDRAC7/8 (ab Firmware 2.50.50.50) über
Redfish, iLO 3 und iLO 4 über die XML-Schnittstelle RIBCL.

Bedient jeden Server der Reihe nach. Eine Datei für Linux und Windows. Benötigt Python 3.9+ und PyQt6.
Oberfläche in Deutsch, Englisch, Französisch und Schweizerdeutsch.

Autor: Björn Franzke
Lizenz: MIT (siehe LICENSE)
Quelle: https://github.com/daklicker-code/bmc-steward
"""
__author__ = "Björn Franzke"
__version__ = "1.5.0"
__license__ = "MIT"
URL = "https://github.com/daklicker-code/bmc-steward"
import base64
import errno
import glob
import html
import http.client
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from xml.sax.saxutils import escape

from PyQt6.QtCore import QLocale, QProcess, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFontDatabase
from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QScrollArea, QSplitter, QStyledItemDelegate, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

WINDOWS = os.name == "nt"
LANGS = [("de", "Deutsch"), ("en", "English"), ("fr", "Français"), ("gsw", "Schwiizerdütsch")]
LANG = "de"
MAIN = None         # das Hauptfenster; beim Sprachwechsel wird es neu aufgebaut


def T(text):
    """Übersetzt einen deutschen Text der Oberfläche in die gewählte Sprache (TR steht am Dateiende)."""
    if LANG == "de":
        return text
    hit = TR.get(text)
    return hit[("en", "fr", "gsw").index(LANG)] if hit else text


# Ordner des Tools (bei einer mit PyInstaller gebauten .exe der Ordner der .exe)
HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
LIST = HERE / "servers.csv"          # Vorschlag für „Liste speichern“; von selbst wird nichts gespeichert
PROFILES = HERE / "bmc-profile.json"   # Profile: Einstellungen ohne Passwörter und ohne Serverliste
BIOS_PROFILES = HERE / "bmc-bios-profile.json"   # UEFI-Vorlagen: eigener Bereich, getrennt von PROFILES
def build_texts():
    """Texte, die schon vor dem Fenster gebraucht werden; nach einem Sprachwechsel neu aufrufen."""
    global NO_PROFILE, VENDORS, USER_HINT, PASS_HINT, VERSION_HINT, COL_TIPS, COLS
    NO_PROFILE = T("(kein Profil)")
    VENDORS = [(T("Automatisch erkennen"), ""), (T("Dell iDRAC"), "dell"), ("HPE iLO 5/6", "hpe"), ("HPE iLO 3/4", "hpe_old"), ("Supermicro", "smc"),
               (T("Lenovo XCC"), "lenovo")]
    USER_HINT = {"": T("automatisch je Hersteller"), "dell": "root", "hpe": "Administrator", "hpe_old": "Administrator",
                 "smc": "ADMIN", "lenovo": "USERID"}
    PASS_HINT = {"": T("Werkspasswort"), "dell": T("ab Werk calvin oder laut Service-Tag-Auszug"),
                 "hpe": T("steht auf dem Anhänger am Gerät"), "hpe_old": T("steht auf dem Anhänger am Gerät"),
                 "smc": T("steht auf dem Aufkleber am Gerät"),
                 "lenovo": T("steht auf dem Anhänger am Gerät")}
    VERSION_HINT = {
        "": T("Ausgelegt auf: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 bis 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. "
            "Andere Generationen zuerst an einem einzelnen Gerät testen."),
        "dell": T("iDRAC9 (14. bis 16. Generation, z. B. R6625) und iDRAC7/8 (12./13. Generation) ab Firmware "
                "2.50.50.50. iDRAC10 ist nicht geprüft, iDRAC6 wird nicht unterstützt."),
        "hpe_old": T("iLO 3 (G7) und iLO 4 (Gen8, Gen9), angesprochen über die XML-Schnittstelle RIBCL. "
                   "iLO 2 und älter werden nicht unterstützt."),
        "hpe": T("iLO 5 und 6 (Gen10, Gen10 Plus, Gen11, z. B. DL345/DL365). iLO 7 (Gen12) ist nicht geprüft."),
        "smc": T("Aktuelle X12/X13-Boards mit Redfish 1.x, auch baugleiche Nutanix-NX-Nodes. Bei älteren Boards "
               "können einzelne Redfish-Funktionen eine Lizenz erfordern."),
        "lenovo": T("ThinkSystem mit XCC2/XCC3 (z. B. SR6xx, SR8xx). Ältere Systeme mit XCC1 sind nicht geprüft."),
    }
    COL_TIPS = [
        T("Adresse, unter der der BMC jetzt erreichbar ist, z. B. die per DHCP vergebene."),
        T("Aktuelles Passwort dieses Geräts. Nur nötig, wenn es vom Werkspasswort oben abweicht\n"
        "(bei HPE, Supermicro und Lenovo ist es pro Gerät verschieden)."),
        T("Feste Adresse, die der BMC bekommen soll."),
        T("Subnetzmaske zur neuen Adresse, z. B. 255.255.255.0. Eingabe als /24 wird umgewandelt."),
        T("Gateway im Netz der neuen Adresse."),
        T("Name, den der BMC bekommt (iDRAC-, iLO- bzw. BMC-Hostname), nicht der Name des Betriebssystems."),
    ]
    COLS = [T("Aktuelle IP"), T("Passwort"), T("Neue IP"), T("Subnetzmaske"), T("Gateway"), T("Hostname")]


def load_lang():
    """Zuletzt gewählte Sprache, sonst die des Systems, sonst Englisch."""
    try:
        code = json.loads(PROFILES.read_text(encoding="utf-8")).get("lang")
    except (OSError, ValueError, AttributeError):
        code = None
    if code not in dict(LANGS):
        code = QLocale.system().name().split("_")[0].lower()
    return code if code in dict(LANGS) else "en"


def set_language(code):
    global LANG
    LANG = code
    build_texts()


def stop_leftover_dhcp():
    """Beendet dnsmasq-Prozesse, die ein früher abgestürzter Lauf des Tools hinterlassen hat (nur Linux).
    Ein vergessener DHCP-Server wäre gefährlich, sobald der Rechner in ein anderes Netz gesteckt wird."""
    count = 0
    if WINDOWS:
        return count
    for pidfile in glob.glob(os.path.join(tempfile.gettempdir(), "bmc-dhcp-*", "pid")):
        try:
            pid = int(Path(pidfile).read_text().strip())
            if Path(f"/proc/{pid}/comm").read_text().strip() == "dnsmasq":
                os.kill(pid, signal.SIGTERM)
                count += 1
        except (OSError, ValueError):
            pass
        shutil.rmtree(os.path.dirname(pidfile), ignore_errors=True)
    return count


ERR_BG = QColor(217, 83, 79, 150)      # Fehler
MISS_BG = QColor(240, 173, 78, 150)    # Pflichtfeld noch leer
MISS_CSS = "QLineEdit { border: 2px solid #f0ad4e; background: rgba(240, 173, 78, 90); }"
ERR_CSS = "QLineEdit { border: 2px solid #d9534f; background: rgba(217, 83, 79, 80); }"
LIVE_CSS = ("QPushButton { background: #c9302c; color: white; font-weight: bold; padding: 4px 14px; }"
            "QPushButton:disabled { background: #8a5a58; color: #dddddd; }")
ABOUT = (f"<hr><p><small>BMC-Steward {__version__} · © 2026 {__author__} · MIT License · "
         f'<a href="{URL}">{URL}</a></small></p>')
DONE_BG = QColor(63, 164, 91, 90)      # Server fertig konfiguriert
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,16}$")


def ipv4(text):
    """IPv4-Adresse oder None. Führende Nullen und Kurzformen wie 10.1 sind ungültig."""
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        return None


def netmask(text):
    """Netz zu einer Subnetzmaske (255.255.255.0) oder None bei ungültiger Maske."""
    if not ipv4(text):
        return None
    try:
        net = ipaddress.IPv4Network(f"0.0.0.0/{text}")
    except ValueError:
        return None
    return net if 1 <= net.prefixlen <= 30 else None


def row_errors(v):
    """Prüft eine Tabellenzeile, gibt {Spalte: Meldung} zurück ("" = Pflichtfeld leer)."""
    e = {}
    for c in (0, 2, 3, 4, 5):
        if not v[c]:
            e[c] = ""
    if v[0] and not ipv4(v[0]):
        e[0] = T("Keine gültige IPv4-Adresse")
    if "," in v[1]:
        e[1] = T("Kommas im Passwort werden nicht unterstützt")
    new, gw = ipv4(v[2]), ipv4(v[4])
    mask = netmask(v[3])
    if v[2] and not new:
        e[2] = T("Keine gültige IPv4-Adresse")
    if v[3] and not mask:
        e[3] = T("Keine gültige Subnetzmaske (z. B. 255.255.255.0 oder /24)")
    if v[4] and not gw:
        e[4] = T("Keine gültige IPv4-Adresse")
    if new and mask:
        net = ipaddress.IPv4Network(f"{new}/{mask.prefixlen}", strict=False)
        if new in (net.network_address, net.broadcast_address):
            e[2] = T('Netz- oder Broadcast-Adresse von {0}').format(net)
        if gw and gw not in net:
            e[4] = T('Gateway liegt nicht im Netz der neuen IP ({0})').format(net)
        elif gw and gw in (net.network_address, net.broadcast_address):
            e[4] = T('Netz- oder Broadcast-Adresse von {0}').format(net)
    if new and gw and new == gw:
        e[4] = T("Gateway und neue IP sind identisch")
    if v[5] and not HOST_RE.match(v[5]):
        e[5] = T("Nur Buchstaben, Ziffern und Bindestrich (nicht am Anfang/Ende), höchstens 63 Zeichen")
    return e


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


def xq(value):
    """Wert als XML-Attribut in doppelten Anführungszeichen (für RIBCL)."""
    return '"' + escape(str(value), {'"': "&quot;"}) + '"'


SECRET_KEYS = ("password",)                       # im Trockenlauf-Protokoll als *** zeigen
CERT_KEYS = ("sslcertificatefile", "certificate", "certificatestring")   # langen Zertifikatstext kürzen


def scrub(data):
    """JSON für das Trockenlauf-Protokoll, Passwörter maskiert und Zertifikatsinhalte gekürzt (auch
    tief verschachtelt, z. B. das Bind-Passwort bei LDAP)."""
    def walk(o):
        if isinstance(o, dict):
            return {k: ("***" if k.lower() in SECRET_KEYS else "<cert>" if k.lower() in CERT_KEYS else walk(v))
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
        self.tls = {}           # Adresse -> TLS-Stufe, gilt nur für diesen Lauf
        self.model = ""         # was vendor() über ein altes Gerät herausgefunden hat, z. B. "iLO 3"
        self.xml = ""           # letzte RIBCL-Antwort

    def sys_path(self, vendor):
        return f"/redfish/v1/Systems/{self.SYS_ID[vendor]}"

    def pause(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.stopped():
            time.sleep(0.2)

    # ---------- TLS ----------
    def tls_mode(self, ip, timeout=10):
        """TLS-Stufe des Geräts: modern | old | oldest, None = nicht erreichbar. Alte BMCs (iLO 3, iDRAC7/8,
        alte Firmware) können nur TLS 1.0/1.1; ermittelt wird das einmal je Gerät mit einem reinen
        Verbindungsaufbau, gesendet wird dabei nichts."""
        if ip in self.tls:
            return self.tls[ip]
        for name in ("modern", "old", "oldest"):
            try:
                with socket.create_connection(hostport(ip), timeout=timeout) as raw:
                    with CONTEXTS[name].wrap_socket(raw):
                        self.tls[ip] = name
                        return name
            except (ssl.SSLError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                continue                # Handshake abgelehnt: die nächste, ältere Stufe versuchen
            except OSError:
                return None
        return None

    # ---------- HTTP ----------
    def api(self, ip, user, pw, method, path, data=None, timeout=30):
        """Sendet eine Anfrage, gibt den HTTP-Code zurück (0 = keine Verbindung); Antwort in self.body."""
        self.body = None
        if self.dry:
            self.log(f"  DRYRUN {method} https://{ip}{path} {scrub(data)}")
            return 200
        mode = self.tls_mode(ip, min(timeout, 10))
        if mode is None:
            return 0
        req = urllib.request.Request(f"https://{ip}{path}", method=method,
                                     data=None if data is None else json.dumps(data).encode())
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
        try:
            with OPENERS[mode].open(req, timeout=timeout) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        except (urllib.error.URLError, OSError, http.client.HTTPException):
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
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=CONTEXTS[tls])
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
            return 0
        finally:
            conn.close()
        self.xml = raw.decode("utf-8", "replace")
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
            with OPENERS[mode].open(f"https://{ip}/redfish/v1/", timeout=timeout) as r:
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
            with OPENERS[mode].open(f"https://{ip}/xmldata?item=All", timeout=timeout) as r:
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
                 log=print, stopped=lambda: False, verify_wait=240, vendor=None, on_done=lambda ip: None):
        super().__init__(dry, log, stopped)
        self.rows, self.cur_user_set, self.cur_pass = rows, cur_user, cur_pass
        self.new_user, self.new_pass, self.slot = new_user, new_pass, slot
        self.verify_wait, self.force_vendor = verify_wait, vendor
        self.on_done = on_done      # wird mit der bisherigen IP aufgerufen, sobald ein Server umgestellt ist
        self.verify_path = ""

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
            if getattr(self, "setup_" + v)(cur_ip, pw, new_ip, mask, gw, host):
                done.append((new_ip, self.verify_path))
                if not self.dry:
                    self.on_done(cur_ip)        # Zeile in der Maske als fertig markieren
            else:
                failed.append(cur_ip)

        # Kontrolle: neue IP mit neuem Benutzer erreichbar? (iLO braucht nach dem Neustart 1-3 Minuten)
        if not self.dry and done and not self.stopped():
            self.log("")
            self.log(T('Kontrolle der neuen Adressen (bis zu {0}s)...').format(self.verify_wait))
            pending, end = list(done), time.monotonic() + self.verify_wait
            self.pause(min(self.verify_wait, 20))
            while pending and not self.stopped():
                still = []
                for ip, path in pending:
                    if self.check_login(ip, path):
                        info = self.identity(ip) if path != "ribcl" else ""
                        self.log(T('  OK      {0}').format(ip) + (f"   {info}" if info else ""), "ok")
                        if info:
                            inventory.append(f"{ip}   {info}")
                    else:
                        still.append((ip, path))
                pending = still
                if not pending or time.monotonic() >= end:
                    break
                self.pause(10)
            for ip, _ in pending:
                self.log(T('  FEHLT   {0}').format(ip), "err")
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
        """True, wenn unter der Adresse schon ein Gerät antwortet (Verbindung klappt oder wird aktiv abgelehnt)."""
        alive = {0, errno.ECONNREFUSED, getattr(errno, "WSAECONNREFUSED", 10061)}
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
    LABEL = {"ntp": "NTP", "syslog": "Syslog", "ldap": "LDAP", "cert": "HTTPS-Zertifikat"}

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

    def run(self):
        """Gibt True zurueck, wenn alle Server ohne Fehler durchliefen."""
        order = [("ntp", self.svc_ntp), ("syslog", self.svc_syslog), ("ldap", self.svc_ldap), ("cert", self.svc_cert)]
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



# ======================================================================
# Kleiner DHCP-Server (für Windows; unter Linux wird dnsmasq verwendet)
# ======================================================================
class DhcpServer:
    """Vergibt Adressen aus einem Bereich an alle anfragenden Geräte. Nur für ein getrenntes Staging-Netz."""

    MAGIC = b"\x63\x82\x53\x63"

    def __init__(self, ip, mask, lo, hi, log=print, on_leases=lambda rows: None, stopped=lambda: False,
                 port=67, client_port=68, reply_to=None, lease_time=3600):
        self.ip, self.mask = ipaddress.IPv4Address(ip), ipaddress.IPv4Address(mask)
        self.lo, self.hi = ipaddress.IPv4Address(lo), ipaddress.IPv4Address(hi)
        self.log, self.on_leases, self.stopped = log, on_leases, stopped
        self.port, self.client_port, self.reply_to, self.lease_time = port, client_port, reply_to, lease_time
        self.assigned = {}      # MAC -> IP (angeboten oder bestätigt)
        self.acked = {}         # MAC -> (IP, Name)

    def allocate(self, mac, wanted=None):
        if mac in self.assigned:
            return self.assigned[mac]
        used = set(self.assigned.values()) | {self.ip}
        if wanted and self.lo <= wanted <= self.hi and wanted not in used:
            self.assigned[mac] = wanted
            return wanted
        ip = self.lo
        while ip <= self.hi:
            if ip not in used:
                self.assigned[mac] = ip
                return ip
            ip += 1
        return None

    @staticmethod
    def options(raw):
        out, i = {}, 0
        while i < len(raw):
            code = raw[i]
            if code == 255:
                break
            if code == 0:
                i += 1
                continue
            if i + 1 >= len(raw):
                break
            out[code] = raw[i + 2:i + 2 + raw[i + 1]]
            i += 2 + raw[i + 1]
        return out

    def reply(self, req, mtype, yiaddr):
        opts = bytes([53, 1, mtype, 54, 4]) + self.ip.packed
        if mtype != 6:
            opts += bytes([51, 4]) + struct.pack("!I", self.lease_time) + bytes([1, 4]) + self.mask.packed
        pkt = struct.pack("!BBBB4sHH4s4s4s4s16s192x4s", 2, req[1], req[2], 0, req[4:8], 0,
                          struct.unpack("!H", req[10:12])[0], b"\0" * 4,
                          yiaddr.packed if yiaddr else b"\0" * 4, self.ip.packed, req[24:28],
                          req[28:44], self.MAGIC) + opts + b"\xff"
        return pkt + b"\0" * max(0, 300 - len(pkt))

    def handle(self, data):
        """Verarbeitet eine Anfrage, gibt (Antwortpaket, Zieladresse) oder None zurück."""
        if len(data) < 240 or data[0] != 1 or data[236:240] != self.MAGIC:
            return None
        hlen = min(data[2] or 6, 16)
        mac = ":".join(f"{b:02x}" for b in data[28:28 + hlen])
        ciaddr = ipaddress.IPv4Address(data[12:16])
        opts = self.options(data[240:])
        mtype = (opts.get(53) or b"\0")[0]
        name = (opts.get(12) or b"").decode("ascii", "replace").strip("\0")
        wanted = ipaddress.IPv4Address(opts[50]) if len(opts.get(50, b"")) == 4 else None
        dest = (self.reply_to or "255.255.255.255", self.client_port)

        if mtype == 1:                                   # DISCOVER -> OFFER
            ip = self.allocate(mac, wanted)
            if not ip:
                self.log(T("Adressbereich erschöpft, keine Adresse mehr frei für ") + mac)
                return None
            self.log(T('Angebot   {0}  an {1} {2}').format(ip, mac, name))
            return self.reply(data, 2, ip), dest
        if mtype == 3:                                   # REQUEST -> ACK / NAK
            server = opts.get(54)
            if server and server != self.ip.packed:      # Gerät hat einen anderen Server gewählt
                if mac not in self.acked:
                    self.assigned.pop(mac, None)
                return None
            if not server and mac not in self.assigned:
                return None                              # Gerät eines anderen Servers: nie abweisen (kein NAK)
            if not wanted and int(ciaddr):
                wanted = ciaddr
            ip = self.allocate(mac, wanted)
            if not ip or (wanted and ip != wanted):
                self.log(T('Abgelehnt {0}  von {1}').format(wanted, mac))
                return self.reply(data, 6, None), dest
            self.acked[mac] = (ip, name or self.acked.get(mac, (None, ""))[1])
            self.log(T('Vergeben  {0}  an {1} {2}').format(ip, mac, name))
            self.on_leases(sorted((str(i), m, n) for m, (i, n) in self.acked.items()))
            if int(ciaddr) and not self.reply_to:        # Verlängerung: direkt antworten
                dest = (str(ciaddr), self.client_port)
            return self.reply(data, 5, ip), dest
        if mtype in (4, 7):                              # DECLINE / RELEASE
            self.assigned.pop(mac, None)
            if self.acked.pop(mac, None):
                self.on_leases(sorted((str(i), m, n) for m, (i, n) in self.acked.items()))
        return None

    def run(self):
        """Läuft bis stopped() wahr wird. Wirft OSError, wenn der Port nicht geöffnet werden kann."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((str(self.ip), self.port))
            sock.settimeout(0.5)
            self.log(T('DHCP-Server läuft auf {0}, Bereich {1} bis {2}').format(self.ip, self.lo, self.hi))
            while not self.stopped():
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    continue        # z. B. Windows meldet "Port unreachable" eines früheren Sendens
                try:
                    answer = self.handle(data)
                    if answer:
                        sock.sendto(*answer)
                except Exception as e:      # ein kaputtes Paket darf den Server nicht beenden
                    self.log(T('Paket verworfen: {0}').format(e))
        finally:
            sock.close()
        self.log(T("DHCP-Server gestoppt"))


def probe_dhcp_servers(ip, timeout=1.5):
    """Sendet ein DHCPDISCOVER und sammelt die Adressen antwortender Server (reine Beobachtung, startet
    selbst keinen Server). Gibt None zurück, wenn Port 68 nicht geöffnet werden konnte (fehlende Rechte)."""
    xid = struct.pack("!I", random.getrandbits(32))
    mac = bytes([0x02, 0x00, 0x00] + [random.randint(0, 255) for _ in range(3)])   # lokal verwaltet, zufällig
    opts = bytes([53, 1, 1, 55, 3, 1, 3, 6, 255])          # DHCPDISCOVER, Parameter Request List, Ende
    pkt = struct.pack("!BBBB4sHH4s4s4s4s16s192x4s", 1, 1, 6, 0, xid, 0, 0x8000,
                      b"\0" * 4, b"\0" * 4, b"\0" * 4, b"\0" * 4, mac + b"\0" * 10, DhcpServer.MAGIC) + opts
    pkt += b"\0" * max(0, 300 - len(pkt))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind((ip, 68))
    except OSError:
        sock.close()
        return None
    try:
        sock.sendto(pkt, ("255.255.255.255", 67))
        found = set()
        sock.settimeout(0.3)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) >= 240 and data[0] == 2 and data[236:240] == DhcpServer.MAGIC and data[4:8] == xid:
                found.add(addr[0])
        return found
    finally:
        sock.close()


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


class PwDelegate(QStyledItemDelegate):
    """Zeigt Passwörter in der Tabelle als Punkte; beim Bearbeiten bleibt die Eingabe verdeckt."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = "•" * len(option.text)

    def createEditor(self, parent, option, index):
        e = super().createEditor(parent, option, index)
        if isinstance(e, QLineEdit):
            e.setEchoMode(QLineEdit.EchoMode.Password)
        return e


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


class DhcpThread(QThread):
    line = pyqtSignal(str)
    leases = pyqtSignal(list)
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            DhcpServer(log=self.line.emit, on_leases=self.leases.emit,
                       stopped=self.isInterruptionRequested, **self.kw).run()
            self.result.emit(0)
        except OSError as e:
            self.line.emit("error: " + T('Port 67 lässt sich nicht öffnen: {0}').format(e))
            self.result.emit(2)



# ======================================================================================================
#  UEFI-Konsole: zeigt das UEFI/BIOS eines Servers über den BMC an, bedienbar wie am Original.
#  Drei Wege: eingebettete HTML5-Konsole des BMC (Bild, Maus, Tastatur), Textkonsole über
#  Serial-over-LAN bzw. SSH und Öffnen im Standardbrowser. Es wird nichts gespeichert.
# ======================================================================================================
try:                                    # optional; muss vor dem QApplication importiert werden
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE = True
except ImportError:
    WEBENGINE = False
try:
    import pyte
except ImportError:
    pyte = None

import codecs

from PyQt6.QtCore import QEvent, QRect, QSocketNotifier, QUrl
from PyQt6.QtWidgets import QStackedWidget
from PyQt6.QtGui import QDesktopServices, QFont, QFontMetrics, QPainter

# Pfad der HTML5-Konsole je Hersteller (nach der Anmeldung); Lenovo: Startseite, dort „Remote Console“
CONSOLE_PATH = {"dell": "/console", "hpe": "/irc.html", "hpe_old": "/irc.html",
                "smc": "/cgi/url_redirect.cgi?url_name=man_ikvm_html5_bootstrap", "lenovo": "/"}
# Befehl, mit dem man sich per SSH auf die serielle Konsole des BMC schaltet
SSH_SOL_CMD = {"dell": "console com2", "hpe": "vsp", "hpe_old": "vsp", "lenovo": "console 1"}
TERM_SIZES = [("80 × 24", 80, 24), ("80 × 25", 80, 25), ("100 × 31", 100, 31), ("132 × 43", 132, 43)]

AUTOLOGIN_JS = """
(function (u, p) {
  function vis(e) { return e && e.offsetParent !== null && !e.disabled; }
  function setv(e, v) {
    var d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    d.set.call(e, v);
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
  }
  var all = [].slice.call(document.querySelectorAll('input'));
  var pw = all.filter(function (e) { return e.type === 'password' && vis(e); })[0];
  if (!pw) return 'wait';
  var us = all.filter(function (e) { return vis(e) && /^(text|email|)$/.test(e.type) &&
           (e.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING); });
  var user = us[us.length - 1];
  if (!user) return 'wait';
  setv(user, u); setv(pw, p);
  var scope = pw.form || document;
  var btns = [].slice.call(scope.querySelectorAll('button, input[type=submit], input[type=button], a.btn'));
  var go = btns.filter(function (b) { return vis(b) && b.type !== 'reset' &&
           /log ?in|sign ?in|anmeld|submit|ok|connexion|login/i.test((b.innerText || b.value || b.id || '')); })[0] ||
           btns.filter(vis)[0];
  if (go) go.click(); else if (pw.form) pw.form.submit();
  return 'done';
})(%s, %s)
"""


def tls_blocks_chromium(mode):
    """Chromium (QtWebEngine) spricht kein TLS 1.0/1.1; alte BMCs gehen dort nicht."""
    return mode in ("old", "oldest")


class ConsoleProbeThread(QThread):
    """Ermittelt Hersteller und TLS-Stufe des BMC (nur Verbindungsaufbau und Redfish-Wurzel, ohne Anmeldung)."""
    done = pyqtSignal(str, str)         # Hersteller, TLS-Stufe

    def __init__(self, parent, ip, vendor):
        super().__init__(parent)
        self.ip, self.vendor = ip, vendor

    def run(self):
        try:
            rc = RedfishClient(dry=False)
            v = self.vendor or rc.vendor(self.ip)
            self.done.emit(v, rc.tls_mode(self.ip, 10) or "")
        except Exception:
            self.done.emit("offline", "")


class BootSetupThread(QThread):
    """Setzt „beim nächsten Start ins UEFI-Setup“ und startet den Server neu (bzw. schaltet ihn ein)."""
    done = pyqtSignal(bool, str)

    def __init__(self, parent, ip, user, pw, vendor):
        super().__init__(parent)
        self.ip, self.user, self.pw, self.vendor = ip, user, pw, vendor

    def run(self):
        try:
            rc = RedfishClient(dry=False)
            v = self.vendor or rc.vendor(self.ip)
            if v not in ("dell", "hpe", "smc", "lenovo"):
                self.done.emit(False, T("Dieser Hersteller bzw. diese Generation kann das nicht per Redfish. "
                                        "Bitte im UEFI-Bild F2/F9/Entf selbst drücken."))
                return
            user = self.user or rc.DEFAULT_USER[v]
            path = rc.sys_path(v)
            code = rc.api(self.ip, user, self.pw, "PATCH", path,
                          {"Boot": {"BootSourceOverrideTarget": "BiosSetup", "BootSourceOverrideEnabled": "Once"}})
            if not rc.ok(code):
                self.done.emit(False, "\n".join(rc.fail_lines(T("Start ins UEFI-Setup vormerken"), code)))
                return
            state = rc.power_state(self.ip, user, self.pw, v)
            action = "On" if state == "Off" else "ForceRestart"
            code = rc.api(self.ip, user, self.pw, "POST", f"{path}/Actions/ComputerSystem.Reset",
                          {"ResetType": action})
            if not rc.ok(code):
                self.done.emit(False, "\n".join(rc.fail_lines(T("Neustart auslösen"), code)))
                return
            self.done.emit(True, T("Vorgemerkt, Server startet neu und landet im UEFI-Setup. "
                                   "Das Bild erscheint nach etwa ein bis zwei Minuten."))
        except Exception as e:
            self.done.emit(False, T('Unerwarteter Fehler: {0!r}').format(e))


# ---------------------------------------------------------------------------------------- Textkonsole
KEYMAP_ENUM = {
    Qt.Key.Key_Up: b"\x1b[A", Qt.Key.Key_Down: b"\x1b[B", Qt.Key.Key_Right: b"\x1b[C", Qt.Key.Key_Left: b"\x1b[D",
    Qt.Key.Key_Home: b"\x1b[H", Qt.Key.Key_End: b"\x1b[F", Qt.Key.Key_PageUp: b"\x1b[5~",
    Qt.Key.Key_PageDown: b"\x1b[6~", Qt.Key.Key_Insert: b"\x1b[2~", Qt.Key.Key_Delete: b"\x1b[3~",
    Qt.Key.Key_Return: b"\r", Qt.Key.Key_Enter: b"\r", Qt.Key.Key_Backspace: b"\x08", Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Backtab: b"\x1b[Z", Qt.Key.Key_Escape: b"\x1b",
    Qt.Key.Key_F1: b"\x1bOP", Qt.Key.Key_F2: b"\x1bOQ", Qt.Key.Key_F3: b"\x1bOR", Qt.Key.Key_F4: b"\x1bOS",
    Qt.Key.Key_F5: b"\x1b[15~", Qt.Key.Key_F6: b"\x1b[17~", Qt.Key.Key_F7: b"\x1b[18~", Qt.Key.Key_F8: b"\x1b[19~",
    Qt.Key.Key_F9: b"\x1b[20~", Qt.Key.Key_F10: b"\x1b[21~", Qt.Key.Key_F11: b"\x1b[23~",
    Qt.Key.Key_F12: b"\x1b[24~",
}
KEYMAP = {k.value: v for k, v in KEYMAP_ENUM.items()}
ANSI_COLORS = {"black": "#000000", "red": "#cd0000", "green": "#00cd00", "brown": "#cdcd00", "blue": "#3050ff",
               "magenta": "#cd00cd", "cyan": "#00cdcd", "white": "#e5e5e5", "brightblack": "#7f7f7f",
               "brightred": "#ff3030", "brightgreen": "#30ff30", "brightbrown": "#ffff30",
               "brightblue": "#6080ff", "brightmagenta": "#ff30ff", "brightcyan": "#30ffff",
               "brightwhite": "#ffffff"}


class TermView(QWidget):
    """Kleines Terminal (VT100/ANSI) auf Basis von pyte. Tasten gehen als Bytes an send(); Bild nur zur Anzeige."""
    send = pyqtSignal(bytes)

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(11)
        self.setFont(font)
        self.fm = QFontMetrics(font)
        self.cw, self.ch = self.fm.horizontalAdvance("M"), self.fm.height()
        self.cp437 = False
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")
        self.live = False
        self.set_size(80, 24)

    def set_size(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen = pyte.Screen(cols, rows) if pyte else None
        self.stream = pyte.Stream(self.screen) if pyte else None
        self.setFixedSize(cols * self.cw + 2, rows * self.ch + 2)
        self.update()

    def set_encoding(self, cp437):
        self.cp437 = cp437
        self.dec = codecs.getincrementaldecoder("cp437" if cp437 else "utf-8")("replace")

    def reset(self):
        self.set_size(self.cols, self.rows)
        self.set_encoding(self.cp437)

    def feed(self, data):
        if self.stream:
            self.stream.feed(self.dec.decode(data))
            self.update()

    def _color(self, name, default):
        if name == "default":
            return default
        if name in ANSI_COLORS:
            return QColor(ANSI_COLORS[name])
        if re.fullmatch(r"[0-9a-fA-F]{6}", name):
            return QColor("#" + name)
        return default

    def paintEvent(self, _):
        p = QPainter(self)
        bg0, fg0 = QColor("#000000"), QColor("#c8c8c8")
        p.fillRect(self.rect(), bg0)
        if not self.screen:
            return
        p.setFont(self.font())
        asc = self.fm.ascent()
        cur = self.screen.cursor
        for y in range(self.rows):
            line = self.screen.buffer[y]
            for x in range(self.cols):
                c = line[x]
                fg, bg = self._color(c.fg, fg0), self._color(c.bg, bg0)
                if c.reverse:
                    fg, bg = bg, fg
                r = QRect(1 + x * self.cw, 1 + y * self.ch, self.cw, self.ch)
                if bg != bg0:
                    p.fillRect(r, bg)
                if c.data.strip():
                    f = QFont(self.font())
                    f.setBold(bool(c.bold))
                    f.setUnderline(bool(c.underscore))
                    p.setFont(f)
                    p.setPen(fg)
                    p.drawText(r.x(), r.y() + asc, c.data)
        if self.live and not cur.hidden and self.hasFocus():
            p.fillRect(QRect(1 + cur.x * self.cw, 1 + cur.y * self.ch, self.cw, self.ch), QColor(200, 200, 200, 110))
        if not self.hasFocus():
            p.setPen(QColor(120, 120, 120))
            p.drawRect(0, 0, self.width() - 1, self.height() - 1)

    def focusNextPrevChild(self, _next):
        return False                    # Tab gehört dem UEFI, nicht der Fokusreihenfolge

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)

    def mousePressEvent(self, e):
        self.setFocus()

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:
            e.accept()                  # Strg+Buchstabe usw. nicht als Programmkürzel abfangen
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if not self.live:
            return
        key = e.key()
        if key in KEYMAP:
            self.send.emit(KEYMAP[key])
            return
        mods = e.modifiers()
        text = e.text()
        if mods & Qt.KeyboardModifier.ControlModifier and Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value:
            self.send.emit(bytes([key - Qt.Key.Key_A.value + 1]))
        elif text and text.isprintable():
            self.send.emit(text.encode("cp437", "replace") if self.cp437 else text.encode("utf-8"))


class SerialSession:
    """Startet ipmitool (SOL) oder ssh (über sshpass) in einem Pseudo-Terminal und füllt eine TermView."""

    def __init__(self, view, on_end):
        self.view, self.on_end = view, on_end
        self.proc = self.master = self.notifier = self.timer = None
        self.deact = None

    @staticmethod
    def missing(method, vendor):
        """Fehlende Programme für das Verfahren; leer = alles da."""
        need = ["ipmitool"] if method == "ipmitool" else ["ssh", "sshpass"]
        return [n for n in need if not shutil.which(n)]

    def start(self, method, ip, user, pw, vendor):
        import fcntl
        import pty
        import termios
        host = ip.strip("[]")
        env = dict(os.environ, TERM="xterm", LANG="C.UTF-8")
        if method == "ipmitool":
            argv = ["ipmitool", "-I", "lanplus", "-H", host, "-U", user, "-E", "sol", "activate"]
            env["IPMI_PASSWORD"] = pw                           # nicht in der Prozessliste sichtbar
            self.deact = (["ipmitool", "-I", "lanplus", "-H", host, "-U", user, "-E", "sol", "deactivate"], env)
        else:
            argv = ["sshpass", "-e", "ssh", "-tt", "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "PubkeyAuthentication=no",
                    "-o", "ConnectTimeout=10"]
            if vendor == "hpe_old":                             # iLO 3/4 sprechen nur alte SSH-Verfahren
                argv += ["-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
                         "-o", "KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1",
                         "-o", "Ciphers=+aes128-cbc,aes256-cbc"]
            argv += [f"{user}@{host}", SSH_SOL_CMD[vendor]]
            env["SSHPASS"] = pw
            self.deact = None
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.view.rows, self.view.cols, 0, 0))
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env,
                                     start_new_session=True, close_fds=True)
        os.close(slave)
        self.notifier = QSocketNotifier(self.master, QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self.readable)
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll)
        self.timer.start(500)

    def readable(self, *_):
        try:
            data = os.read(self.master, 65536)
        except OSError:
            data = b""
        if data:
            self.view.feed(data)
        else:
            self.poll()

    def poll(self):
        if self.proc and self.proc.poll() is not None:
            self.stop(ended=True)

    def write(self, data):
        if self.master is not None:
            try:
                os.write(self.master, data)
            except OSError:
                pass

    def stop(self, ended=False):
        if self.timer:
            self.timer.stop()
        if self.notifier:
            self.notifier.setEnabled(False)
        if self.proc and self.proc.poll() is None:
            if self.deact:                                       # SOL-Sitzung am BMC freigeben
                try:
                    subprocess.run(self.deact[0], env=self.deact[1], timeout=6, capture_output=True)
                except (OSError, subprocess.SubprocessError):
                    pass
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass
        code = self.proc.returncode if self.proc else None
        self.proc = self.master = self.notifier = self.timer = None
        if ended:
            self.on_end(code)


# ---------------------------------------------------------------------------------------- Web-Konsole
if WEBENGINE:
    class BmcPage(QWebEnginePage):
        """Akzeptiert das selbstsignierte Zertifikat nur des gewählten BMC und leitet Popups in eigene Reiter."""

        def __init__(self, profile, host, popup, parent=None):
            super().__init__(profile, parent)
            self.host, self.popup = QUrl(f"https://{host}").host(), popup
            self.certificateError.connect(self.cert)

        def cert(self, error):
            if error.url().host() == self.host:
                error.acceptCertificate()
            else:
                error.rejectCertificate()

        def createWindow(self, _type):
            return self.popup()


class ConsoleMixin:
    """Reiter „UEFI-Konsole“ des Hauptfensters."""

    def build_console(self):
        self.con_vendor = QComboBox()
        for text, key in VENDORS:
            self.con_vendor.addItem(text, key)
        self.con_ip = QComboBox()
        self.con_ip.setEditable(True)
        self.con_ip.setToolTip(T("Adresse des BMC. Vorschläge aus der Serverliste (Reiter 2), es geht aber jede\n"
                               "erreichbare Adresse."))
        self.con_ip.activated.connect(self.console_pick_ip)
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip(T("Adressen aus der Serverliste (Reiter 2) neu einlesen"))
        refresh.clicked.connect(self.console_refresh_ips)
        iprow = QHBoxLayout()
        iprow.addWidget(self.con_ip, 1)
        iprow.addWidget(refresh)
        self.con_user = QLineEdit()
        self.con_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.con_pass = self._pw()
        self.con_mode = QComboBox()
        self.con_mode.addItem(T("Bild: Web-Konsole des BMC (eingebettet)"), "web")
        self.con_mode.addItem(T("Text: serielle Konsole (SOL/SSH)"), "serial")
        self.con_mode.addItem(T("Bild: im Standardbrowser öffnen"), "browser")
        self.con_mode.currentIndexChanged.connect(self.console_mode_changed)
        self.con_method = QComboBox()
        self.con_method.addItem(T("Automatisch"), "auto")
        self.con_method.addItem("IPMI-SOL (ipmitool)", "ipmitool")
        self.con_method.addItem("SSH (sshpass)", "ssh")
        self.con_term = QComboBox()
        self.con_term.addItem("UTF-8 / VT-UTF8", False)
        self.con_term.addItem("ANSI (CP437)", True)
        self.con_size = QComboBox()
        for label, c, r in TERM_SIZES:
            self.con_size.addItem(label, (c, r))
        self.con_size.currentIndexChanged.connect(self.console_size_changed)

        box = QGroupBox(T("Verbindung"))
        f = QFormLayout(box)
        self.tip_row(f, T("Hersteller"), self.con_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den Hersteller\n"
                       "dieses einen Geräts selbst."))
        f.addRow(T("Adresse"), iprow)
        self.tip_row(f, T("Benutzer"), self.con_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.con_pass),
                     T("Wird nur für diese Verbindung verwendet und nirgends gespeichert."))
        self.tip_row(f, T("Anzeige"), self.con_mode,
                     T("Bild: echtes Konsolenbild mit Maus und Tastatur wie am Original (HTML5-Konsole des BMC).\n"
                       "Text: serielle Umleitung; geht nur, wenn im UEFI „Console Redirection“ aktiv ist.\n"
                       "Browser: öffnet die BMC-Seite im Standardbrowser, ohne Auto-Anmeldung."))
        self.con_serial_box = QWidget()
        sf = QFormLayout(self.con_serial_box)
        sf.setContentsMargins(0, 0, 0, 0)
        self.tip_row(sf, T("Verfahren"), self.con_method,
                     T("Automatisch: Supermicro über ipmitool, Dell/HPE/Lenovo über SSH (sshpass), sonst ipmitool.\n"
                       "IPMI über LAN muss im BMC aktiviert sein (bei Dell ab Werk aus)."))
        self.tip_row(sf, T("Terminaltyp"), self.con_term,
                     T("Muss zur Einstellung „Terminal Type“ im UEFI passen: VT100/VT-UTF8 → UTF-8,\n"
                       "ANSI → CP437 (Rahmenzeichen)."))
        self.tip_row(sf, T("Größe"), self.con_size,
                     T("Zeichen × Zeilen; muss zur Einstellung im UEFI passen (meist 80 × 24 oder 80 × 25)."))
        f.addRow(self.con_serial_box)

        pk = QGroupBox(T("Benötigte Pakete"))
        pl = QVBoxLayout(pk)
        row = QHBoxLayout()
        pkgs = [("QtWebEngine", WEBENGINE, "python-pyqt6-webengine")]
        if not WINDOWS:
            pkgs += [("pyte", bool(pyte), "python-pyte"), ("ipmitool", bool(shutil.which("ipmitool")), "ipmitool"),
                     ("sshpass", bool(shutil.which("sshpass")), "sshpass"), ("ssh", bool(shutil.which("ssh")), "openssh")]
        lacking = []
        for name, ok, pkg in pkgs:
            lab = QLabel(("✔ " if ok else "❗ ") + name)
            lab.setStyleSheet("color: #2e9e4f; font-weight: bold;" if ok else "color: #c9302c; font-weight: bold;")
            lab.setToolTip(T("installiert") if ok else T("nicht installiert, Paket: {0}").format(pkg))
            row.addWidget(lab)
            if not ok:
                lacking.append(pkg)
        row.addStretch()
        pl.addLayout(row)
        if lacking:
            hint = QLabel(T("Fehlendes nachinstallieren, auf dem Rechner mit diesem Tool (Terminal):")
                          + "\n    sudo pacman -S " + " ".join(lacking) + "      (Arch/CachyOS)")
            hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            pl.addWidget(hint)
        pk.setToolTip(T("✔ installiert, ❗ fehlt. Je nach gewähltem Weg werden nicht alle gebraucht:\n"
                        "Bild: QtWebEngine · Text: pyte + ipmitool (Supermicro) bzw. ssh + sshpass (Dell/HPE/Lenovo)."))

        self.con_connect = QPushButton(T("Verbinden"))
        self.con_connect.setMinimumHeight(30)
        self.con_connect.clicked.connect(self.console_connect)
        self.con_disconnect = QPushButton(T("Trennen"))
        self.con_disconnect.setEnabled(False)
        self.con_disconnect.clicked.connect(self.console_disconnect)
        self.con_start = QPushButton(T("Konsole starten"))
        self.con_start.setEnabled(False)
        self.con_start.setToolTip(T("Springt nach der Anmeldung zur Konsolen-Seite des BMC."))
        self.con_start.clicked.connect(self.console_goto)
        self.con_setup = QPushButton(T("Ins UEFI-Setup booten …"))
        self.con_setup.setToolTip(T("Merkt „beim nächsten Start ins UEFI-Setup“ vor und startet den Server hart neu\n"
                                    "(per Redfish). Nur mit Rückfrage."))
        self.con_setup.clicked.connect(self.console_boot_setup)
        self.con_status = QLabel()
        self.con_status.setWordWrap(True)
        btns = QHBoxLayout()
        btns.addWidget(self.con_connect)
        btns.addWidget(self.con_disconnect)
        btns.addWidget(self.con_start)
        btns.addStretch()
        btns.addWidget(self.con_setup)

        # Tastenleiste für die Textkonsole (F-Tasten fangen Desktop und Fenstermanager manchmal ab)
        self.con_keys = QWidget()
        kl = QHBoxLayout(self.con_keys)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(QLabel(T("Tasten:")))
        for label, seq in [("Esc", b"\x1b"), ("F1", b"\x1bOP"), ("F2", b"\x1bOQ"), ("F9", b"\x1b[20~"),
                           ("F10", b"\x1b[21~"), ("F11", b"\x1b[23~"), ("F12", b"\x1b[24~"),
                           ("Entf", b"\x1b[3~"), ("Esc+1", b"\x1b1"), ("Esc+2", b"\x1b2"), ("Esc+0", b"\x1b0")]:
            b = QPushButton(label)
            b.setFixedWidth(52)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, s=seq: self.console_send(s))
            kl.addWidget(b)
        kl.addStretch()
        self.con_keys.setToolTip(T("Sendet die Taste an den Server. „Esc+1“ usw. ersetzen F1 usw. auf Konsolen,\n"
                                   "die nur Escape-Folgen verstehen."))

        self.con_stack = QStackedWidget()
        self.con_web_tabs = None
        if WEBENGINE:
            self.con_profile = QWebEngineProfile(self)          # ohne Namen: nichts wird auf Platte gelegt
            self.con_profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            self.con_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
            self.con_web_tabs = QTabWidget()
            self.con_web_tabs.setTabsClosable(True)
            self.con_web_tabs.tabCloseRequested.connect(self.console_close_tab)
            self.con_stack.addWidget(self.con_web_tabs)
        else:
            self.con_stack.addWidget(self.console_note(T(
                "Die eingebettete Web-Konsole braucht QtWebEngine. Installation auf dem Rechner mit diesem Tool:")
                + ("\n    pip install PyQt6-WebEngine" if WINDOWS else
                   "\n    sudo pacman -S python-pyqt6-webengine      (Arch/CachyOS)\n"
                   "    pip install PyQt6-WebEngine                (sonst)")))
        self.term = TermView()
        self.term.send.connect(self.console_send)
        self.term_scroll = QScrollArea()
        self.term_scroll.setWidget(self.term)
        self.term_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.term_scroll.setWidgetResizable(False)
        self.con_stack.addWidget(self.term_scroll)
        self.con_stack.addWidget(self.console_note(T(
            "„Verbinden“ öffnet die Anmeldeseite des BMC im Standardbrowser. Die Anmeldung erfolgt dort von Hand;\n"
            "die HTML5-Konsole wird im BMC über „Remote Console“ bzw. „Virtual Console“ gestartet.")))
        self.con_copy = QPushButton(T("Passwort kopieren (30 s)"))
        self.con_copy.clicked.connect(self.console_copy_pw)

        warn = QLabel(T("Achtung: Tasten gehen direkt an den Server. Dieser Reiter ändert von sich aus nichts, "
                        "alles Weitere geschieht in der UEFI-Oberfläche selbst."))
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #c9302c;")
        lay = QVBoxLayout()
        lay.addWidget(box)
        lay.addWidget(pk)
        lay.addLayout(btns)
        lay.addWidget(self.con_status)
        lay.addWidget(warn)
        lay.addWidget(self.con_keys)
        lay.addWidget(self.con_copy)
        lay.addWidget(self.con_stack, 1)
        page = QWidget()
        page.setLayout(lay)

        self.con_probe = self.con_boot = None
        self.con_serial = SerialSession(self.term, self.console_serial_ended)
        self.con_login = None            # Zustand der Auto-Anmeldung der eingebetteten Konsole
        self.con_timer = QTimer(self)
        self.con_timer.timeout.connect(self.console_login_tick)
        self.console_mode_changed()
        self.console_refresh_ips()
        return page

    @staticmethod
    def console_note(text):
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lab

    # ---------- Bedienung der Maske ----------
    def console_refresh_ips(self):
        keep = self.con_ip.currentText()
        self.con_ip.clear()
        self.con_pws = {}
        for ip, pw in self.reachable_rows():
            self.con_ip.addItem(ip)
            self.con_pws[ip] = pw
        if keep:
            self.con_ip.setEditText(keep)

    def console_pick_ip(self, *_):
        """Übernimmt das Passwort der Zeile aus Reiter 2 (nur im Feld, gespeichert wird nichts)."""
        pw = self.con_pws.get(self.con_ip.currentText(), "") or self.cur_pass.text()
        if pw:
            self.con_pass.setText(pw)

    def console_mode_changed(self, *_):
        mode = self.con_mode.currentData()
        self.con_stack.setCurrentIndex({"web": 0, "serial": 1, "browser": 2}[mode])
        self.con_serial_box.setVisible(mode == "serial")
        self.con_keys.setVisible(mode == "serial")
        self.con_copy.setVisible(mode == "browser")
        self.con_start.setVisible(mode == "web")

    def console_size_changed(self, *_):
        if not self.con_serial.proc:
            c, r = self.con_size.currentData()
            self.term.set_size(c, r)

    def console_active(self):
        return bool(self.con_serial.proc) or bool(self.con_probe and self.con_probe.isRunning()) \
            or bool(self.con_boot and self.con_boot.isRunning()) or bool(self.con_web_tabs and self.con_web_tabs.count())

    def console_set_status(self, text, err=False):
        self.con_status.setText(text)
        self.con_status.setStyleSheet("color: #c9302c;" if err else "")

    def console_busy(self, busy):
        self.con_connect.setEnabled(not busy)
        self.con_disconnect.setEnabled(busy)
        for w in (self.con_vendor, self.con_ip, self.con_user, self.con_pass, self.con_mode, self.con_method,
                  self.con_term, self.con_size, self.con_setup):
            w.setEnabled(not busy)

    def console_creds(self):
        ip = self.con_ip.currentText().strip()
        if not ip:
            self.console_set_status(T("Bitte eine Adresse eintragen."), True)
            return None
        return ip, self.con_user.text().strip(), self.con_pass.text(), self.con_vendor.currentData() or ""

    def console_copy_pw(self):
        pw = self.con_pass.text()
        if not pw:
            return
        cb = QApplication.clipboard()
        cb.setText(pw)
        QTimer.singleShot(30000, lambda: cb.clear() if cb.text() == pw else None)
        self.console_set_status(T("Passwort liegt 30 Sekunden in der Zwischenablage."))

    # ---------- Verbinden ----------
    def console_connect(self):
        creds = self.console_creds()
        if not creds:
            return
        ip, _user, _pw, vendor = creds
        self.console_busy(True)
        self.console_set_status(T("Prüfe {0} …").format(ip))
        self.con_probe = ConsoleProbeThread(self, ip, vendor)
        self.con_probe.done.connect(self.console_probed)
        self.con_probe.start()

    def console_probed(self, vendor, tls):
        ip, user, pw, _ = self.console_creds() or ("", "", "", "")
        mode = self.con_mode.currentData()
        if vendor == "offline":
            self.console_fail(T("Gerät nicht erreichbar."))
            return
        if vendor not in RedfishClient.NAME:
            self.console_fail(T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)."))
            return
        user = user or RedfishClient.DEFAULT_USER[vendor]
        if mode == "web":
            self.console_open_web(ip, user, pw, vendor, tls)
        elif mode == "serial":
            self.console_open_serial(ip, user, pw, vendor)
        else:
            url = f"https://{ip}/"
            QDesktopServices.openUrl(QUrl(url))
            self.console_busy(False)
            self.console_set_status(T("Im Browser geöffnet: {0}").format(url))

    def console_fail(self, text):
        self.console_busy(False)
        self.console_set_status(text, True)

    # ---------- Eingebettete Web-Konsole ----------
    def console_open_web(self, ip, user, pw, vendor, tls):
        if not WEBENGINE:
            self.console_fail(T("QtWebEngine fehlt, siehe Hinweis unten. Alternativ „Text“ oder „Standardbrowser“ wählen."))
            return
        if tls_blocks_chromium(tls):
            self.console_fail(T("Dieser BMC kann nur TLS 1.0/1.1 (z. B. iLO 3/4, iDRAC7/8 mit alter Firmware). "
                                "Die eingebettete Konsole geht damit nicht, bitte „Text“ wählen."))
            return
        self.console_close_all_tabs()
        view = self.console_new_view(ip)
        self.con_web_tabs.addTab(view, f"{ip}")
        self.con_web_tabs.tabBar().setTabButton(0, self.con_web_tabs.tabBar().ButtonPosition.RightSide, None)
        view.setUrl(QUrl(f"https://{ip}/"))
        self.con_login = {"user": user, "pw": pw, "vendor": vendor, "ip": ip, "tries": 0, "state": "wait",
                          "view": view}
        self.con_start.setEnabled(True)
        self.con_timer.start(1000)
        self.console_set_status(T("Verbunden mit {0}. Anmeldung läuft; danach öffnet sich die Konsole.").format(ip))

    def console_new_view(self, ip):
        view = QWebEngineView()
        page = BmcPage(self.con_profile, ip, self.console_popup, view)
        view.setPage(page)
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        page.fullScreenRequested.connect(lambda r: r.accept())
        return view

    def console_popup(self):
        """Der BMC öffnet die Konsole oft in einem neuen Fenster: stattdessen als weiterer Reiter."""
        ip = self.con_login["ip"] if self.con_login else ""
        view = self.console_new_view(ip)
        i = self.con_web_tabs.addTab(view, T("Konsole"))
        self.con_web_tabs.setCurrentIndex(i)
        return view.page()

    def console_close_tab(self, i):
        if i == 0:
            return
        w = self.con_web_tabs.widget(i)
        self.con_web_tabs.removeTab(i)
        w.deleteLater()

    def console_close_all_tabs(self):
        self.con_timer.stop()
        self.con_login = None
        if self.con_web_tabs:
            while self.con_web_tabs.count():
                w = self.con_web_tabs.widget(0)
                self.con_web_tabs.removeTab(0)
                w.deleteLater()

    def console_login_tick(self):
        """Füllt die Anmeldeseite aus, sobald sie da ist (die Seiten der BMCs bauen sich nach dem Laden auf)."""
        st = self.con_login
        if not st or st["state"] != "wait":
            if st and st["state"] == "done":
                st["after"] = st.get("after", 0) + 1
                if st["after"] == 5:                    # Anmeldung hatte Zeit: zur Konsole weiterspringen
                    self.console_goto()
                    self.con_timer.stop()
            return
        st["tries"] += 1
        if st["tries"] > 25:
            self.con_timer.stop()
            self.console_set_status(T("Anmeldeseite nicht erkannt. Bitte im Fenster von Hand anmelden."), True)
            return
        js = AUTOLOGIN_JS % (json.dumps(st["user"]), json.dumps(st["pw"]))
        st["view"].page().runJavaScript(js, lambda res, s=st: self.console_login_result(s, res))

    def console_login_result(self, st, res):
        if res == "done" and st["state"] == "wait":
            st["state"] = "done"

    def console_goto(self):
        st = self.con_login
        if not st:
            return
        path = CONSOLE_PATH.get(st["vendor"], "/")
        self.con_web_tabs.setCurrentIndex(0)
        st["view"].setUrl(QUrl(f"https://{st['ip']}{path}"))

    # ---------- Textkonsole ----------
    def console_open_serial(self, ip, user, pw, vendor):
        if WINDOWS:
            self.console_fail(T("Die Textkonsole läuft nur unter Linux."))
            return
        if not pyte:
            self.console_fail(T("Für die Textkonsole fehlt „pyte“: sudo pacman -S python-pyte  (Arch/CachyOS) "
                                "bzw. pip install pyte"))
            return
        method = self.con_method.currentData()
        if method == "auto":
            method = "ipmitool" if vendor == "smc" or vendor not in SSH_SOL_CMD or not shutil.which("sshpass") \
                else "ssh"
        lacking = SerialSession.missing(method, vendor)
        if lacking:
            self.console_fail(T("Es fehlt: {0}. Installation: sudo pacman -S {1}  (Arch/CachyOS)").format(
                ", ".join(lacking), " ".join(lacking)))
            return
        if method == "ssh" and vendor not in SSH_SOL_CMD:
            self.console_fail(T("Für diesen Hersteller gibt es kein SSH-Verfahren, bitte IPMI-SOL wählen."))
            return
        c, r = self.con_size.currentData()
        self.term.set_size(c, r)
        self.term.set_encoding(self.con_term.currentData())
        self.term.reset()
        try:
            self.con_serial.start(method, ip, user, pw, vendor)
        except OSError as e:
            self.console_fail(T('Start fehlgeschlagen: {0}').format(e))
            return
        self.term.live = True
        self.term.setFocus()
        self.console_set_status(T("Textkonsole verbunden ({0}). In das schwarze Feld klicken, dann tippen. "
                                  "Bleibt es schwarz, ist im UEFI die „Console Redirection“ aus oder die Größe falsch.")
                                .format("ipmitool" if method == "ipmitool" else "SSH"))

    def console_send(self, data):
        if self.con_serial.proc:
            self.con_serial.write(data)
            self.term.setFocus()

    def console_serial_ended(self, code):
        self.term.live = False
        self.term.update()
        self.console_busy(False)
        self.console_set_status(T("Textkonsole beendet (Code {0}).").format(code), bool(code))

    # ---------- Trennen / Aufräumen ----------
    def console_disconnect(self):
        if self.con_serial.proc:
            self.con_serial.stop()
            self.term.live = False
            self.term.update()
        self.console_close_all_tabs()
        self.con_start.setEnabled(False)
        self.console_busy(False)
        self.console_set_status(T("Getrennt."))

    # ---------- Ins UEFI-Setup booten ----------
    def console_boot_setup(self):
        creds = self.console_creds()
        if not creds:
            return
        ip, user, pw, vendor = creds
        box = QMessageBox(QMessageBox.Icon.Warning, T("Ins UEFI-Setup booten"),
                          T("Server {0} wird jetzt HART neu gestartet.").format(ip), parent=self)
        box.setInformativeText(T("Ein laufendes Betriebssystem wird ohne Herunterfahren beendet (wie die "
                                 "Reset-Taste), nicht gespeicherte Daten gehen verloren. Nur für Server ohne "
                                 "laufenden Betrieb verwenden."))
        go = box.addButton(T("Hart neu starten"), QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not go:
            return
        self.console_busy(True)
        self.con_disconnect.setEnabled(False)
        self.console_set_status(T("Sende an {0} …").format(ip))
        self.con_boot = BootSetupThread(self, ip, user, pw, vendor)
        self.con_boot.done.connect(self.console_booted)
        self.con_boot.start()

    def console_booted(self, ok, text):
        self.console_busy(False)
        self.console_set_status(text, not ok)

    def console_shutdown(self):
        """Beim Beenden: Sitzungen sauber schließen."""
        self.con_serial.stop()
        self.console_close_all_tabs()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)     # Seiten vor dem Profil freigeben


class Window(ConsoleMixin, QWidget):
    def __init__(self, state=None):
        super().__init__()
        self.setWindowTitle(f"BMC-Steward {__version__} · Dell iDRAC / HPE iLO / Supermicro / Lenovo XCC")
        self.resize(900, 780)
        self.worker = None
        self.bios_worker = None

        # --- Aktueller Login ---
        self.vendor = QComboBox()
        for text, key in VENDORS:
            self.vendor.addItem(text, key)
        self.vendor.currentIndexChanged.connect(self.vendor_changed)
        self.cur_user = QLineEdit()
        self.cur_pass = self._pw()
        self.user_hint = QLabel()
        apply_pw = QPushButton(T("Passwort für alle Server übernehmen"))
        apply_pw.setToolTip(T("Trägt das Passwort von oben in die Spalte „Passwort“ aller Zeilen ein\n"
                            "und überschreibt dort vorhandene Einträge."))
        apply_pw.clicked.connect(self.apply_password)
        box_cur = QGroupBox(T("Aktueller Login (Auslieferungszustand)"))
        box_cur.setToolTip(T("Mit diesen Daten meldet sich das Tool an den Geräten an, um sie umzustellen."))
        f = QFormLayout(box_cur)
        self.tip_row(f, T("Hersteller"), self.vendor,
                     T("„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller, gemischte Listen sind möglich.\n"
                     "Eine feste Wahl überspringt die Erkennung und gilt für alle Zeilen. Nützlich, wenn die\n"
                     "Erkennung danebenliegt oder der Trockenlauf den richtigen Ablauf zeigen soll."))
        self.version_hint = QLabel()
        self.version_hint.setWordWrap(True)
        f.addRow("", self.version_hint)
        self.tip_row(f, T("Benutzer"), self.cur_user,
                     T("Benutzer, mit dem sich das Tool jetzt am BMC anmeldet, also der Werksbenutzer.\n"
                     "Leer lassen: Dann wird je Hersteller root, Administrator oder ADMIN verwendet."))
        f.addRow("", self.user_hint)
        self.tip_row(f, T("Passwort"), self._with_eye(self.cur_pass),
                     T("Passwort des Werksbenutzers. Es gilt für alle Zeilen, deren Spalte „Passwort“ leer ist,\n"
                     "und wird bei neuen Zeilen vorbelegt. Hat jedes Gerät ein eigenes Passwort\n"
                     "(HPE, Supermicro, Lenovo), dieses in der Tabelle je Zeile eintragen."))
        f.addRow("", apply_pw)

        # --- Neuer Benutzer ---
        self.new_user = QLineEdit()
        self.new_pass = self._pw()
        self.new_pass2 = self._pw()
        self.slot = QSpinBox()
        self.slot.setRange(3, 16)
        self.slot.setValue(3)
        box_new = QGroupBox(T("Neuer Administrator"))
        box_new.setToolTip(T("Dieses Konto wird auf jedem Gerät der Liste eingerichtet."))
        f = QFormLayout(box_new)
        self.tip_row(f, T("Benutzer"), self.new_user,
                     T("Name des Administrator-Kontos, das auf allen Geräten angelegt wird.\n"
                     "Existiert der Name bei HPE, Supermicro oder Lenovo schon, werden nur Passwort und Rolle gesetzt."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.new_pass),
                     T("Passwort des neuen Administrators, auf allen Geräten gleich.\n"
                     "Die Geräte prüfen ihre eigenen Regeln, iLO verlangt z. B. mindestens 8 Zeichen.\n"
                     "Vor dem scharfen Lauf über das Auge kontrollieren."))
        self.tip_row(f, T("Passwort wiederholen"), self._with_eye(self.new_pass2),
                     T("Das Passwort zur Sicherheit ein zweites Mal eingeben.\n"
                       "Stimmen die beiden Eingaben nicht überein, startet kein Lauf."))
        self.tip_row(f, T("User-Slot (nur Dell)"), self.slot,
                     T("Nur Dell: Der iDRAC hat 16 feste Benutzerplätze, Platz 2 ist root.\n"
                     "Der neue Administrator wird in diesen Platz geschrieben. Ist der Platz schon\n"
                     "von einem anderen Benutzer belegt, wird nichts überschrieben. Standard ist Platz 3."))

        # --- Vorgaben für neue Zeilen ---
        self.def_mask = QLineEdit()
        self.def_mask.setPlaceholderText(T("z. B. 255.255.255.0 oder /24"))
        self.def_gw = QLineEdit()
        self.def_gw.setPlaceholderText(T("z. B. 10.20.0.1"))
        for w in (self.def_mask, self.def_gw):
            w.editingFinished.connect(self.validate_defaults)
        box_def = QGroupBox(T("Vorgaben für neue Zeilen"))
        f = QFormLayout(box_def)
        self.tip_row(f, T("Subnetzmaske"), self.def_mask,
                     T("Wird bei jeder neuen Zeile der Serverliste vorbelegt, auch bei Geräten aus der DHCP-Liste.\n"
                     "Leer lassen: Dann wird der Wert der vorherigen Zeile übernommen."))
        self.tip_row(f, T("Gateway"), self.def_gw,
                     T("Wird bei jeder neuen Zeile der Serverliste vorbelegt.\n"
                     "Leer lassen: Dann wird der Wert der vorherigen Zeile übernommen."))

        right = QVBoxLayout()
        right.addWidget(box_new)
        right.addWidget(box_def)
        top = QHBoxLayout()
        top.addWidget(box_cur)
        top.addLayout(right)

        # --- Serverliste ---
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        for c, tip in enumerate(COL_TIPS):
            self.table.horizontalHeaderItem(c).setToolTip(tip)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.update_count)
        add = QPushButton(T("+ Server"))
        add.setToolTip(T("Neue Zeile. Passwort, Subnetzmaske und Gateway werden vorbelegt."))
        add.clicked.connect(lambda: self.add_row())
        rem = QPushButton(T("– Entfernen"))
        rem.setToolTip(T("Entfernt die markierten Zeilen."))
        rem.clicked.connect(self.remove_rows)
        again = QPushButton(T("Erneut bearbeiten"))
        again.setToolTip(T("Nimmt bei den markierten Zeilen die grüne Fertig-Markierung zurück,\n"
                         "damit sie beim nächsten Start wieder bearbeitet werden."))
        again.clicked.connect(self.reopen_rows)
        load = QPushButton(T("Liste laden …"))
        load.setToolTip(T("Lädt eine gespeicherte Liste oder eine CSV-Datei, z. B. aus Excel:\n"
                        "aktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname"))
        load.clicked.connect(self.load_dialog)
        save = QPushButton(T("Liste speichern …"))
        save.setToolTip(T("Speichert die Liste in eine Datei. Das Tool speichert nie von selbst.\n"
                        "Beim Speichern wird gefragt, ob die Passwörter mit in die Datei sollen."))
        save.clicked.connect(self.save_dialog)
        self.count = QLabel()
        btns = QHBoxLayout()
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addWidget(again)
        btns.addWidget(load)
        btns.addWidget(save)
        btns.addStretch()
        btns.addWidget(self.count)
        box_list = QGroupBox(T("Serverliste"))
        v = QVBoxLayout(box_list)
        v.addWidget(self.table)
        v.addLayout(btns)

        # --- Start ---
        self.dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.dry.setChecked(True)
        self.dry.setToolTip(T("Mit Haken wird nichts geändert: Das Protokoll zeigt nur, welche Aufrufe\n"
                            "an welches Gerät gingen. Ohne Haken werden die Geräte wirklich umgestellt."))
        self.start = QPushButton(T("Start"))
        self.start.setDefault(True)
        self.start.setMinimumHeight(38)
        self.start.setToolTip(T("Prüft die Eingaben und bearbeitet dann alle noch offenen Zeilen der Reihe nach:\n"
                              "Benutzer anlegen, Hostname setzen, feste Adresse setzen.\n"
                              "Fertige (grüne) Zeilen werden übersprungen. Vor einem scharfen Lauf\n"
                              "wird nachgefragt."))
        self.start.clicked.connect(self.run)
        self.stop = QPushButton(T("Abbrechen"))
        self.stop.setToolTip(T("Bricht nach dem Server ab, der gerade bearbeitet wird"))
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.abort)
        log_save = QPushButton(T("Protokoll speichern …"))
        log_save.setToolTip(T("Speichert das Protokoll als Textdatei, z. B. für die Dokumentation.\n"
                              "Passwörter stehen nicht darin."))
        log_save.clicked.connect(self.save_log)
        actions = QHBoxLayout()
        actions.addWidget(self.dry)
        actions.addStretch()
        actions.addWidget(log_save)
        actions.addWidget(self.stop)
        actions.addWidget(self.start)

        # --- Was fehlt noch? ---
        self.cells_missing = self.cells_bad = 0
        self.todo = QLabel()
        self.todo.setWordWrap(True)
        self.todo.setToolTip(T("Zeigt, was vor dem Start noch auszufüllen ist. Die betroffenen Felder sind\n"
                               "gelb (fehlt noch) oder rot (fehlerhaft) markiert."))
        legend = QLabel(
            "<small><span style='background:#f0ad4e; color:#000000'>&nbsp;" + T("fehlt noch") + "&nbsp;</span> "
            "<span style='background:#d9534f; color:#ffffff'>&nbsp;" + T("fehlerhaft") + "&nbsp;</span> "
            "<span style='background:#3fa45b; color:#ffffff'>&nbsp;" + T("fertig") + "&nbsp;</span></small>")
        hints = QHBoxLayout()
        hints.addWidget(self.todo, 1)
        hints.addWidget(legend)

        # --- Protokoll ---
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.status = QLabel(T("Bereit"))

        upper = QWidget()
        u = QVBoxLayout(upper)
        u.setContentsMargins(0, 0, 0, 0)
        u.addWidget(box_list)
        u.addLayout(hints)
        u.addLayout(actions)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(upper)
        split.addWidget(self.log)
        split.setSizes([380, 260])

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.addLayout(self.build_profiles())
        pl.addLayout(top)
        pl.addWidget(split, 1)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_dhcp(), T("1 · DHCP-Server"))
        self.tabs.addTab(page, T("2 · Server konfigurieren"))
        self.tabs.addTab(self.build_existing(), T("2b · Bestandsserver"))
        self.tabs.addTab(self.build_bios(), T("3 · UEFI-Einstellungen"))
        self.tabs.addTab(self.build_services(), T("4 · Dienste"))
        self.tabs.addTab(self.build_console(), T("5 · UEFI-Konsole"))
        help_view = QTextBrowser()
        help_view.setOpenExternalLinks(True)
        help_view.setHtml(HELP[LANG] + ABOUT)
        self.tabs.addTab(help_view, T("6 · Hilfe"))
        self.lang = QComboBox()
        for code, name in LANGS:
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(LANG)))
        self.lang.setToolTip("Sprache · Language · Langue · Sproch")
        self.lang.activated.connect(self.switch_language)
        self.tabs.setCornerWidget(self.lang)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(self.status)

        self.dirty = False
        self.vendor_changed()
        self.read_profiles()
        self.add_row()
        self.update_count()
        self.tabs.setCurrentIndex(0)
        for w in (self.dhcp_from, self.dhcp_to):
            w.textChanged.connect(self.validate_range)
        self.iface.currentIndexChanged.connect(self.validate_range)
        self.validate_range()

        # Änderungen merken, damit beim Schließen nachgefragt werden kann
        self.table.itemChanged.connect(self.touch)
        for w in (self.cur_user, self.cur_pass, self.new_user, self.new_pass):
            w.textChanged.connect(self.touch)
        for w in (self.cur_pass, self.new_user, self.new_pass, self.new_pass2):
            w.textChanged.connect(self.update_missing)
        self.dry.toggled.connect(self.mode_changed)
        self.bios_dry.toggled.connect(self.bios_mode_changed)
        self.svc_dry.toggled.connect(self.svc_mode_changed)
        self.dirty = False
        if state:
            self.import_state(state)
        elif stop_leftover_dhcp():
            self.status.setText(T("Ein DHCP-Server aus einem früheren, abgebrochenen Lauf lief noch und wurde beendet."))
        self.mode_changed()
        self.bios_mode_changed()
        self.svc_mode_changed()

    # ---------- Hilfen ----------
    @staticmethod
    def tip_row(form, label, widget, tip):
        """Formularzeile, bei der Beschriftung und Feld denselben Hilfetext zeigen."""
        lab = QLabel(label)
        lab.setToolTip(tip)
        widget.setToolTip(tip)
        form.addRow(lab, widget)

    def touch(self, *_):
        self.dirty = True

    # ---------- Profile ----------
    def build_profiles(self):
        self.profiles = {}
        self.profile = QComboBox()
        self.profile.setMinimumWidth(240)
        self.profile.setToolTip(
            T("Ein Profil merkt sich die Einstellungen dieser Maske: Hersteller, Werksbenutzer, neuen\n"
            "Administrator, User-Slot, Vorgaben für neue Zeilen und die DHCP-Einstellungen.\n"
            "Passwörter und die Serverliste gehören nicht dazu."))
        self.profile.activated.connect(self.profile_selected)
        bar = QHBoxLayout()
        lab = QLabel(T("Profil"))
        lab.setToolTip(self.profile.toolTip())
        bar.addWidget(lab)
        bar.addWidget(self.profile)
        for text, tip, slot in (
                (T("Speichern"), T("Speichert die aktuellen Einstellungen in das gewählte Profil.\n"
                              "Ohne gewähltes Profil wird ein neues angelegt."), self.profile_save),
                (T("Neu …"), T("Legt aus den aktuellen Einstellungen ein neues Profil an."), self.profile_new),
                (T("Umbenennen …"), T("Gibt dem gewählten Profil einen anderen Namen."), self.profile_rename),
                (T("Löschen"), T("Löscht das gewählte Profil. Die Einstellungen in der Maske bleiben stehen."),
                 self.profile_delete)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()
        return bar

    def profile_values(self):
        """Aktuelle Einstellungen der Maske, ohne Passwörter."""
        return {"vendor": self.vendor.currentData() or "", "cur_user": self.cur_user.text().strip(),
                "new_user": self.new_user.text().strip(), "slot": self.slot.value(),
                "mask": self.def_mask.text().strip(), "gateway": self.def_gw.text().strip(),
                "dhcp_if": (self.iface.currentData() or [""])[0],
                "dhcp_from": self.dhcp_from.text().strip(), "dhcp_to": self.dhcp_to.text().strip()}

    def apply_profile(self, p):
        dirty = self.dirty                      # ein Profilwechsel ändert die Serverliste nicht
        self.vendor.setCurrentIndex(max(0, self.vendor.findData(str(p.get("vendor", "")))))
        self.cur_user.setText(str(p.get("cur_user", "")))
        self.new_user.setText(str(p.get("new_user", "")))
        if str(p.get("slot", "")).isdigit():
            self.slot.setValue(int(p["slot"]))
        self.def_mask.setText(str(p.get("mask", "")))
        self.def_gw.setText(str(p.get("gateway", "")))
        for i in range(self.iface.count()):
            if self.iface.itemData(i)[0] == p.get("dhcp_if"):
                self.iface.setCurrentIndex(i)
        if p.get("dhcp_from") and p.get("dhcp_to"):
            self.dhcp_from.setText(str(p["dhcp_from"]))
            self.dhcp_to.setText(str(p["dhcp_to"]))
        self.validate_defaults()
        self.validate_range()
        self.dirty = dirty

    def current_profile(self):
        name = self.profile.currentText()
        return name if name in self.profiles else None

    def refill_profiles(self, select=None):
        self.profile.clear()
        self.profile.addItem(NO_PROFILE)
        self.profile.addItems(sorted(self.profiles, key=str.lower))
        self.profile.setCurrentIndex(max(0, self.profile.findText(select or NO_PROFILE)))

    def read_profiles(self):
        """Profile beim Start einlesen und das zuletzt genutzte anwenden."""
        last = None
        try:
            data = json.loads(PROFILES.read_text(encoding="utf-8"))
            self.profiles = {str(k): v for k, v in data.get("profiles", {}).items() if isinstance(v, dict)}
            last = data.get("last")
        except (OSError, ValueError, AttributeError):
            self.profiles = {}
        self.refill_profiles(last)
        if self.current_profile():
            self.apply_profile(self.profiles[self.current_profile()])

    def write_profiles(self):
        try:
            PROFILES.write_text(json.dumps({"lang": LANG, "last": self.current_profile(), "profiles": self.profiles},
                                           indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError as e:
            QMessageBox.warning(self, T("Profile"), T('{0} nicht schreibbar:\n{1}').format(PROFILES.name, e))
            return False

    def profile_selected(self, *_):
        name = self.current_profile()
        if name:
            self.apply_profile(self.profiles[name])
            self.status.setText(T('Profil „{0}“ geladen').format(name))
        if PROFILES.exists():
            self.write_profiles()               # merkt sich das zuletzt genutzte Profil

    def profile_save(self):
        name = self.current_profile()
        if not name:
            return self.profile_new()
        self.profiles[name] = self.profile_values()
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ gespeichert (ohne Passwörter)').format(name))

    def ask_profile_name(self, title, preset=""):
        name, good = QInputDialog.getText(self, title, T("Name des Profils:"), text=preset)
        name = name.strip()
        if not good or not name:
            return None
        if name == NO_PROFILE:
            QMessageBox.warning(self, title, T("Dieser Name ist reserviert."))
            return None
        return name

    def profile_new(self):
        name = self.ask_profile_name(T("Neues Profil"))
        if not name:
            return
        if name in self.profiles and QMessageBox.question(
                self, T("Neues Profil"), T('Das Profil „{0}“ gibt es schon. Überschreiben?').format(name)
        ) != QMessageBox.StandardButton.Yes:
            return
        self.profiles[name] = self.profile_values()
        self.refill_profiles(name)
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ gespeichert (ohne Passwörter)').format(name))

    def profile_rename(self):
        old = self.current_profile()
        if not old:
            self.status.setText(T("Kein Profil gewählt"))
            return
        name = self.ask_profile_name(T("Profil umbenennen"), old)
        if not name or name == old:
            return
        if name in self.profiles:
            QMessageBox.warning(self, T("Profil umbenennen"), T('Das Profil „{0}“ gibt es schon.').format(name))
            return
        self.profiles[name] = self.profiles.pop(old)
        self.refill_profiles(name)
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ heißt jetzt „{1}“').format(old, name))

    def profile_delete(self):
        name = self.current_profile()
        if not name:
            self.status.setText(T("Kein Profil gewählt"))
            return
        if QMessageBox.question(self, T("Profil löschen"), T('Profil „{0}“ wirklich löschen?').format(name)
                                ) != QMessageBox.StandardButton.Yes:
            return
        del self.profiles[name]
        self.refill_profiles()
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ gelöscht').format(name))

    def validate_defaults(self):
        """Prüft die Vorgaben für neue Zeilen und markiert ungültige Felder."""
        m = re.fullmatch(r"/?(\d{1,2})", self.def_mask.text().strip())      # /24 oder 24 -> 255.255.255.0
        if m and int(m.group(1)) <= 32:
            self.def_mask.setText(str(ipaddress.IPv4Network(f"0.0.0.0/{m.group(1)}").netmask))
        mask, gw = self.def_mask.text().strip(), self.def_gw.text().strip()
        bad = {self.def_mask: bool(mask) and not netmask(mask), self.def_gw: bool(gw) and not ipv4(gw)}
        for w, wrong in bad.items():
            w.setStyleSheet("QLineEdit { border: 1px solid #d9534f; }" if wrong else "")
        return not any(bad.values())

    def vendor_changed(self, *_):
        v = self.vendor.currentData() or ""
        self.cur_user.setPlaceholderText(USER_HINT[v])
        self.cur_pass.setPlaceholderText(PASS_HINT[v])
        self.user_hint.setText(
            T("<small>Leer lassen für: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>") if not v
            else T('<small>Leer lassen für den Werksbenutzer {0}</small>').format(USER_HINT[v]))
        self.version_hint.setText(f"<small>{VERSION_HINT[v]}</small>")
        self.version_hint.setToolTip(VERSION_HINT[v])
        self.slot.setEnabled(v in ("", "dell"))

    @staticmethod
    def _pw(text=""):
        e = QLineEdit(text)
        e.setEchoMode(QLineEdit.EchoMode.Password)
        return e

    @staticmethod
    def _with_eye(edit):
        btn = QPushButton("👁")
        btn.setCheckable(True)
        btn.setFixedWidth(36)
        btn.toggled.connect(lambda on: edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit)
        h.addWidget(btn)
        return w

    def say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.log.append(f'<span style="{style}">{html.escape(text)}</span>')

    # ---------- Serverliste ----------
    def add_row(self, values=None):
        """Neue Zeile; ohne Werte gelten die Vorgaben, sonst Maske und Gateway der letzten Zeile."""
        r = self.table.rowCount()
        if values is None:
            values = [""] * len(COLS)
            values[1] = self.cur_pass.text()
            values[3] = self.def_mask.text().strip() or (self.cell(r - 1, 3) if r else "")
            values[4] = self.def_gw.text().strip() or (self.cell(r - 1, 4) if r else "")
        self.table.insertRow(r)
        for c, val in enumerate(values):
            self.table.setItem(r, c, QTableWidgetItem(val))
        self.table.setCurrentCell(r, 0)

    def remove_rows(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self.update_count()
        self.touch()

    def is_done(self, r):
        """Zeile ist fertig, solange die Aktuelle IP noch die ist, mit der sie umgestellt wurde."""
        item = self.table.item(r, 0)
        return bool(item) and item.text().strip() != "" and item.data(Qt.ItemDataRole.UserRole) == item.text().strip()

    def mark_done(self, ip):
        """Der Engine-Thread meldet einen fertig umgestellten Server: Zeile grün markieren."""
        for r in range(self.table.rowCount()):
            if self.cell(r, 0) == ip and not self.is_done(r):
                self.table.blockSignals(True)
                self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, ip)
                self.table.blockSignals(False)
                break
        self.update_count()

    def apply_password(self):
        for r in range(self.table.rowCount()):
            if any(self.cell(r, c) for c in range(len(COLS))):
                self.table.item(r, 1).setText(self.cur_pass.text())

    def cell(self, r, c):
        item = self.table.item(r, c)
        return item.text().strip() if item else ""

    def rows(self, open_only=False):
        """Alle nicht leeren Zeilen als Listen mit 6 Feldern; mit open_only ohne die fertigen."""
        out = []
        for r in range(self.table.rowCount()):
            vals = [self.cell(r, c) for c in range(len(COLS))]
            if (vals[0] or vals[2] or vals[5]) and not (open_only and self.is_done(r)):    # nur vorbelegte Felder zählen nicht
                out.append(vals)
        return out

    def update_count(self, *_):
        issues = self.validate_table()
        self.cells_missing = sum(1 for r, c, m in issues if not m)
        self.cells_bad = sum(1 for r, c, m in issues if m)
        bad = len({r for r, c, m in issues if m})
        total = len(self.rows())
        fertig = total - len(self.rows(open_only=True))
        self.count.setText(T('{0} Server').format(total)
                           + (T(" · <span style='color:#3fa45b'>{0} fertig</span>").format(fertig) if fertig else '')
                           + (T(" · <span style='color:#d9534f'>{0} mit Fehlern</span>").format(bad) if bad else ''))
        self.update_missing()

    def load(self, path, settings=False):
        """Liest eine Liste ein; mit settings=True auch die gespeicherten Felder der Maske."""
        try:
            lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as e:
            QMessageBox.warning(self, T("Liste laden"), T('Datei nicht lesbar:\n{0}').format(e))
            return False
        self.table.blockSignals(True)
        for line in lines:
            line = line.strip()
            if line.startswith("#@ ") and "=" in line:
                if not settings:
                    continue
                key, val = line[3:].split("=", 1)
                field = {"cur_user": self.cur_user, "cur_pass": self.cur_pass,
                         "new_user": self.new_user, "new_pass": self.new_pass,
                         "dhcp_from": self.dhcp_from, "dhcp_to": self.dhcp_to}.get(key)
                if field:
                    field.setText(val)
                    if key == "new_pass":
                        self.new_pass2.setText(val)
                elif key == "slot" and val.isdigit():
                    self.slot.setValue(int(val))
                elif key == "vendor":
                    self.vendor.setCurrentIndex(max(0, self.vendor.findData(val)))
                elif key == "dhcp_if":
                    for i in range(self.iface.count()):
                        if self.iface.itemData(i)[0] == val:
                            self.iface.setCurrentIndex(i)
                continue
            if not line or line.startswith("#"):
                continue
            f = [x.strip() for x in line.replace(";", ",").split(",")]     # Excel exportiert mit Semikolon
            done = False
            if len(f) == 7:              # 7. Spalte: „fertig“, Server schon umgestellt
                done = f.pop().lower() in ("fertig", "done", "1", "x")
            if len(f) == 5:              # ohne Passwort-Spalte
                f.insert(1, "")
            if len(f) == 6:
                self.add_row(f)
                if done and f[0]:
                    self.table.item(self.table.rowCount() - 1, 0).setData(Qt.ItemDataRole.UserRole, f[0])
        self.table.blockSignals(False)
        self.update_count()
        return True

    def load_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, T("Liste laden"), str(HERE), T("CSV (*.csv);;Alle Dateien (*)"))
        if not path:
            return
        replace = True
        if self.rows():
            box = QMessageBox(QMessageBox.Icon.Question, T("Liste laden"),
                              T("Es sind schon Einträge vorhanden."), parent=self)
            b_replace = box.addButton(T("Ersetzen"), QMessageBox.ButtonRole.AcceptRole)
            b_append = box.addButton(T("Anhängen"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() not in (b_replace, b_append):
                return
            replace = box.clickedButton() is b_replace
        if replace:
            self.table.setRowCount(0)
        else:   # leere Zeilen am Ende entfernen
            for r in reversed(range(self.table.rowCount())):
                if not any(self.cell(r, c) for c in (0, 2, 5)):
                    self.table.removeRow(r)
        if self.load(path, settings=replace):
            if self.table.rowCount() == 0:
                self.add_row()
            self.dirty = not replace
            self.tabs.setCurrentIndex(1)
            self.status.setText(T('Geladen: {0}').format(path))

    def save_dialog(self):
        """Fragt nach Datei und danach, ob Passwörter hinein sollen. Gibt True zurück, wenn gespeichert wurde."""
        path, _ = QFileDialog.getSaveFileName(self, T("Liste speichern"), str(LIST), T("CSV (*.csv)"))
        if not path:
            return False
        box = QMessageBox(QMessageBox.Icon.Question, T("Liste speichern"),
                          T("Sollen die Passwörter mit in die Datei?"), parent=self)
        box.setInformativeText(T("Ohne Passwörter enthält die Datei nur Adressen, Hostnamen und Einstellungen. "
                               "Mit Passwörtern stehen diese im Klartext in der Datei."))
        b_without = box.addButton(T("Ohne Passwörter"), QMessageBox.ButtonRole.AcceptRole)
        b_with = box.addButton(T("Mit Passwörtern"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_without)
        box.exec()
        if box.clickedButton() not in (b_without, b_with):
            return False
        try:
            self.write_file(path, with_passwords=box.clickedButton() is b_with)
        except OSError as e:
            QMessageBox.warning(self, T("Liste speichern"), T('Datei nicht schreibbar:\n{0}').format(e))
            return False
        self.dirty = False
        self.status.setText(T('Gespeichert in {0}').format(path) + (
            T(" (mit Passwörtern im Klartext)") if box.clickedButton() is b_with else T(" (ohne Passwörter)")))
        return True

    def write_file(self, path, with_passwords=False):
        """Schreibt Liste und Einstellungen; Passwörter nur auf ausdrücklichen Wunsch."""
        settings = [("vendor", self.vendor.currentData() or ""), ("cur_user", self.cur_user.text()),
                    ("new_user", self.new_user.text()), ("slot", self.slot.value()),
                    ("dhcp_if", (self.iface.currentData() or [""])[0]),
                    ("dhcp_from", self.dhcp_from.text()), ("dhcp_to", self.dhcp_to.text())]
        if with_passwords:
            settings += [("cur_pass", self.cur_pass.text()), ("new_pass", self.new_pass.text())]
        text = "".join(f"#@ {k}={v}\n" for k, v in settings)
        text += "# aktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,fertig\n"
        for r in range(self.table.rowCount()):
            vals = [self.cell(r, c) for c in range(len(COLS))]
            if not (vals[0] or vals[2] or vals[5]):
                continue
            if not with_passwords:
                vals[1] = ""
            text += ",".join(vals + ["fertig" if self.is_done(r) else ""]) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if not WINDOWS:
            os.chmod(path, 0o600)       # nur für den eigenen Benutzer lesbar

    def validate_table(self):
        """Färbt die Zellen ein (gelb = fehlt noch, rot = fehlerhaft, grün = fertig) und gibt
        [(Zeile, Spalte, Meldung)] zurück ("" = Pflichtfeld leer)."""
        t = self.table
        t.blockSignals(True)
        rows = {}
        for r in range(t.rowCount()):
            m = re.fullmatch(r"/?(\d{1,2})", self.cell(r, 3))      # /24 oder 24 -> 255.255.255.0
            if m and int(m.group(1)) <= 32:
                t.item(r, 3).setText(str(ipaddress.IPv4Network(f"0.0.0.0/{m.group(1)}").netmask))
            vals = [self.cell(r, c) for c in range(len(COLS))]
            if vals[0] or vals[2] or vals[5]:
                rows[r] = vals
        errs = {r: row_errors(v) for r, v in rows.items()}
        for c in (0, 2, 5):
            seen = {}
            for r, v in rows.items():
                key = v[c].lower()
                # die alte IP eines fertigen Servers ist frei und darf neu vergeben werden
                if key and c not in errs[r] and not (c == 0 and self.is_done(r)):
                    seen.setdefault(key, []).append(r)
            for dup in (x for x in seen.values() if len(x) > 1):
                nums = ", ".join(str(x + 1) for x in dup)
                for r in dup:
                    errs[r][c] = T('{0} kommt mehrfach vor (Zeilen {1})').format(COLS[c], nums)
        # die neue IP darf nicht die aktuelle Adresse eines anderen, noch offenen Geräts sein
        current = {v[0]: r for r, v in rows.items() if v[0] and not self.is_done(r)}
        for r, v in rows.items():
            other = current.get(v[2])
            if other is not None and other != r and 2 not in errs[r] and not self.is_done(r):
                errs[r][2] = T('Diese Adresse ist die aktuelle IP von Zeile {0}').format(other + 1)
        empty_tip = "\n\n" + T("Pflichtfeld, noch leer.")
        out = []
        for r in range(t.rowCount()):
            done = self.is_done(r)
            for c in range(len(COLS)):
                item = t.item(r, c)
                if not item:
                    continue
                msg = errs.get(r, {}).get(c)
                if msg is not None:
                    item.setBackground(ERR_BG if msg else MISS_BG)
                    item.setToolTip(msg if msg else COL_TIPS[c] + empty_tip)
                    out.append((r, c, msg))
                elif not rows and c != 1 and not item.text().strip():
                    item.setBackground(MISS_BG)         # Liste noch ganz leer: zeigen, wo es losgeht
                    item.setToolTip(COL_TIPS[c] + empty_tip)
                elif done:
                    item.setBackground(DONE_BG)
                    item.setToolTip(T("Fertig umgestellt, wird beim nächsten Start übersprungen.\n"
                                    "Soll das Gerät noch einmal bearbeitet werden: Zeile markieren und "
                                    "„Erneut bearbeiten“ klicken."))
                else:
                    item.setBackground(QBrush())
                    item.setToolTip(COL_TIPS[c])
        t.blockSignals(False)
        return out

    def check(self):
        """Prüft alle Eingaben vor einem Lauf. Gibt eine Fehlermeldung zurück oder None."""
        if not self.new_user.text() or not self.new_pass.text():
            return T("Neuen Benutzer und Passwort ausfüllen.")
        if not USER_RE.match(self.new_user.text()):
            return (T("Neuer Benutzer: nur Buchstaben, Ziffern, Punkt, Bindestrich und Unterstrich, "
                    "höchstens 16 Zeichen."))
        if len(self.new_pass.text()) < 8:
            return T("Neues Passwort: mindestens 8 Zeichen. Kürzere lehnen die meisten BMCs ab.")
        if self.new_pass.text() != self.new_pass2.text():
            return T("Neues Passwort: Die beiden Eingaben stimmen nicht überein.")
        if not self.rows():
            return T("Die Serverliste ist leer.")
        errs = self.validate_table()
        if errs:
            r, c, msg = errs[0]
            self.table.setCurrentCell(r, c)
            if not msg:
                msg = T("fehlt")
            text = T('Zeile {0}, {1}: {2}').format(r + 1, COLS[c], msg)
            if len(errs) > 1:
                text += "\n\n" + T('({0} weitere markierte Felder)').format(len(errs) - 1)
            return text
        own = {str(i.ip) for i in self.local_ifaces()}
        pool = None
        if self.dhcp_running():
            pool = ipv4(self.dhcp_from.text().strip()), ipv4(self.dhcp_to.text().strip())
        for r in range(self.table.rowCount()):
            if self.is_done(r) or not (self.cell(r, 0) or self.cell(r, 2)):
                continue
            if not self.cell(r, 1) and not self.cur_pass.text():
                return T('Zeile {0}: kein Passwort (weder in der Zeile noch oben).').format(r + 1)
            new = ipv4(self.cell(r, 2))
            if new and str(new) in own:
                return T('Zeile {0}: Die neue IP ist eine Adresse dieses Rechners.').format(r + 1)
            if new and pool and pool[0] and pool[1] and pool[0] <= new <= pool[1]:
                return (T('Zeile {0}: Die neue IP liegt im Bereich des laufenden DHCP-Servers ({1} bis {2}). Dort könnte sie ein zweites Mal vergeben werden.').format(r + 1, pool[0], pool[1]))
        return None

    def running(self):
        return (bool(self.worker) and self.worker.isRunning()) \
            or (bool(getattr(self, "bios_worker", None)) and self.bios_worker.isRunning()) \
            or (bool(getattr(self, "svc_worker", None)) and self.svc_worker.isRunning())

    def closeEvent(self, event):
        if getattr(self, "switching", False):   # Sprachwechsel: das neue Fenster hat alles übernommen
            event.accept()
            return
        if self.running():
            QMessageBox.information(self, T("Lauf aktiv"), T("Es läuft noch eine Konfiguration. "
                                    "Bitte warten oder erst abbrechen."))
            event.ignore()
            return
        if self.dirty and self.rows():
            box = QMessageBox(QMessageBox.Icon.Question, T("Beenden"),
                              T("Die Serverliste wurde nicht gespeichert."), parent=self)
            box.setInformativeText(T("Das Tool speichert nichts von selbst. Ohne Speichern ist die Liste "
                                   "nach dem Schließen weg."))
            b_save = box.addButton(T("Speichern …"), QMessageBox.ButtonRole.AcceptRole)
            b_drop = box.addButton(T("Verwerfen"), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is b_save:
                if not self.save_dialog():
                    event.ignore()
                    return
            elif box.clickedButton() is not b_drop:
                event.ignore()
                return
        self.stop_dhcp()
        self.console_shutdown()
        event.accept()

    # ---------- UEFI/BIOS-Einstellungen ----------
    def reachable_rows(self):
        """(Adresse, Passwort) je Zeile; fertig umgestellte (grüne) Zeilen sind jetzt unter der neuen IP erreichbar."""
        out = []
        for r in range(self.table.rowCount()):
            vals = [self.cell(r, c) for c in range(len(COLS))]
            if vals[0] or vals[2] or vals[5]:
                ip = vals[2] if self.is_done(r) else vals[0]
                if ip:
                    out.append((ip, vals[1]))
        return out

    def build_bios(self):
        self.bios_live = {}        # zuletzt gelesene Attribute eines Referenzgeräts: Name -> aktueller Wert
        self.bios_pending = {}     # aus einer geladenen Vorlage, noch nicht in der Tabelle angezeigt
        self.bios_reader = None

        self.bios_vendor = QComboBox()
        for text, key in VENDORS:
            self.bios_vendor.addItem(text, key)
        self.bios_user = QLineEdit()
        self.bios_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.bios_pass = self._pw()
        ref_refresh = QPushButton("↻")
        ref_refresh.setFixedWidth(36)
        ref_refresh.setToolTip(T("Adressen aus der Serverliste (Reiter 2) neu einlesen"))
        ref_refresh.clicked.connect(self.bios_refresh_ips)
        self.bios_ref = QComboBox()
        self.bios_ref.setEditable(True)
        self.bios_ref.setToolTip(T("Adresse des Geräts, von dem die Attribute gelesen werden.\n"
                                 "Vorschläge kommen aus der Serverliste in Reiter 2, es geht aber jede\n"
                                 "erreichbare Adresse."))
        refrow = QHBoxLayout()
        refrow.addWidget(self.bios_ref, 1)
        refrow.addWidget(ref_refresh)
        self.bios_read_btn = QPushButton(T("Attribute lesen"))
        self.bios_read_btn.setToolTip(T("Liest die tatsächlich vorhandenen UEFI-Attribute von diesem einen\n"
                                      "Gerät. Nur Lesen, es wird nichts verändert."))
        self.bios_read_btn.clicked.connect(self.bios_read)
        self.bios_read_status = QLabel()

        box_ref = QGroupBox(T("Referenzgerät (nur zum Lesen)"))
        box_ref.setToolTip(T("Da sich UEFI-Attribute je Modell unterscheiden, zeigt das Tool nur die\n"
                           "tatsächlich vorhandenen eines echten Geräts an, statt sie zu erraten."))
        f = QFormLayout(box_ref)
        self.tip_row(f, T("Hersteller"), self.bios_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den\n"
                     "Hersteller dieses einen Geräts selbst."))
        self.tip_row(f, T("Benutzer"), self.bios_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet\n"
                     "(root/Administrator/ADMIN/USERID)."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.bios_pass),
                     T("Gilt zum Lesen des Referenzgeräts und, wenn die Vorlage später angewendet wird,\n"
                     "für alle Zeilen der Serverliste ohne eigenes Passwort."))
        f.addRow(T("Adresse"), refrow)
        act = QHBoxLayout()
        act.addWidget(self.bios_read_status)
        act.addStretch()
        act.addWidget(self.bios_read_btn)
        f.addRow(act)

        self.bios_filter = QLineEdit()
        self.bios_filter.setPlaceholderText(T("Attribut suchen …"))
        self.bios_filter.setToolTip(T("Blendet Zeilen aus, deren Name den Suchtext nicht enthält."))
        self.bios_filter.textChanged.connect(self.bios_apply_filter)
        self.bios_table = QTableWidget(0, 3)
        self.bios_table.setHorizontalHeaderLabels([T("Attribut"), T("Aktueller Wert"), T("Neuer Wert")])
        self.bios_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bios_table.setToolTip(T("Nur die Spalte „Neuer Wert“ ist bearbeitbar. Leer lassen, um ein\n"
                                   "Attribut unverändert zu lassen. Graue Zeilen (Passwörter, TPM,\n"
                                   "Löschfunktionen) sind gesperrt."))
        box_table = QGroupBox(T("UEFI/BIOS-Attribute"))
        vt = QVBoxLayout(box_table)
        vt.addWidget(self.bios_filter)
        vt.addWidget(self.bios_table)

        self.bios_dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.bios_dry.setChecked(True)
        self.bios_dry.setToolTip(T("Mit Haken wird nichts gesendet, das Protokoll zeigt nur den Ablauf.\n"
                                 "Ohne Haken werden die ausgefüllten „Neuer Wert“-Felder auf jedem Server\n"
                                 "der Liste (Reiter 2) gesetzt, der das jeweilige Attribut tatsächlich hat."))
        self.bios_reboot = QCheckBox(T("Danach hart neu starten"))
        self.bios_reboot.setChecked(False)
        self.bios_reboot.setToolTip(T("UEFI-Einstellungen werden erst nach einem Neustart aktiv. Ohne Haken\n"
                                    "werden sie nur vorgemerkt, es wird kein Neustart ausgelöst.\n"
                                    "Mit Haken wird ein laufender Server hart neu gestartet (wie mit der\n"
                                    "Reset-Taste), ein ausgeschalteter wird eingeschaltet. Nur für Server\n"
                                    "ohne laufendes Betriebssystem verwenden."))
        self.bios_start = QPushButton(T("Auf allen Servern der Liste setzen"))
        self.bios_start.setMinimumHeight(34)
        self.bios_start.setToolTip(T("Wendet jedes ausgefüllte „Neuer Wert“-Feld auf jede Zeile der\n"
                                   "Serverliste (Reiter 2) an, der Reihe nach, mit Login wie oben."))
        self.bios_start.clicked.connect(self.bios_run)
        self.bios_stop = QPushButton(T("Abbrechen"))
        self.bios_stop.setToolTip(T("Bricht nach dem Server ab, der gerade bearbeitet wird"))
        self.bios_stop.setEnabled(False)
        self.bios_stop.clicked.connect(self.bios_abort)
        actions = QHBoxLayout()
        actions.addWidget(self.bios_dry)
        actions.addWidget(self.bios_reboot)
        actions.addStretch()
        actions.addWidget(self.bios_stop)
        actions.addWidget(self.bios_start)

        self.bios_log = QTextEdit()
        self.bios_log.setReadOnly(True)
        self.bios_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.bios_status = QLabel(T("Bereit"))

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addLayout(self.bios_build_profiles())
        lay.addWidget(box_ref)
        lay.addWidget(box_table, 1)
        lay.addLayout(actions)
        lay.addWidget(self.bios_log)
        lay.addWidget(self.bios_status)
        self.bios_refresh_ips()
        self.bios_read_profiles()
        return page

    def bios_refresh_ips(self):
        keep = self.bios_ref.currentText()
        self.bios_ref.blockSignals(True)
        self.bios_ref.clear()
        for ip, _ in self.reachable_rows():
            self.bios_ref.addItem(ip)
        idx = self.bios_ref.findText(keep) if keep else -1
        if idx >= 0:
            self.bios_ref.setCurrentIndex(idx)
        elif keep:
            self.bios_ref.setEditText(keep)
        self.bios_ref.blockSignals(False)

    def bios_read(self):
        ip = self.bios_ref.currentText().strip()
        if not ip:
            QMessageBox.warning(self, T("Attribute lesen"),
                                T("Bitte eine Adresse eintragen oder aus der Serverliste wählen."))
            return
        self.bios_read_btn.setEnabled(False)
        self.bios_read_status.setText(T("Lese …"))
        self.bios_reader = BiosReadThread(self, ip, self.bios_user.text().strip(), self.bios_pass.text(),
                                          self.bios_vendor.currentData() or None)
        self.bios_reader.done.connect(self.bios_read_done)
        self.bios_reader.start()

    def bios_read_done(self, attrs, vendor, err):
        self.bios_read_btn.setEnabled(True)
        if attrs is None:
            self.bios_read_status.setText("")
            QMessageBox.warning(self, T("Attribute lesen"), err or T("Attribute konnten nicht gelesen werden."))
            return
        self.bios_live = {str(k): v for k, v in attrs.items()}
        self.bios_fill_table()
        self.bios_read_status.setText(T('{0} Attribute ({1})').format(len(self.bios_live), Engine.NAME.get(vendor, vendor)))

    def bios_fill_table(self, keep_values=True):
        old_new = {}
        if keep_values:
            for r in range(self.bios_table.rowCount()):
                name, val = self.bios_table.item(r, 0), self.bios_table.item(r, 2)
                if name and val and val.text().strip():
                    old_new[name.text()] = val.text().strip()
        self.bios_table.setRowCount(0)
        for name in sorted(self.bios_live, key=str.lower):
            cur = self.bios_live[name]
            locked = BiosEngine.blocked(name)
            r = self.bios_table.rowCount()
            self.bios_table.insertRow(r)
            it0, it1 = QTableWidgetItem(name), QTableWidgetItem("" if cur is None else str(cur))
            it2 = QTableWidgetItem("" if locked else old_new.get(name) or self.bios_pending.get(name, ""))
            it0.setFlags(it0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it1.setFlags(it1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if locked:
                it2.setFlags(it2.flags() & ~Qt.ItemFlag.ItemIsEditable)
                for it in (it0, it1, it2):
                    it.setForeground(QBrush(QColor(140, 140, 140)))
                    it.setToolTip(T("Gesperrt: Passwort-, TPM- und Lösch-Attribute ändert das Tool nie. "
                                  "Ein Fehler dort kann den Server aussperren oder Daten kosten."))
            self.bios_table.setItem(r, 0, it0)
            self.bios_table.setItem(r, 1, it1)
            self.bios_table.setItem(r, 2, it2)
        self.bios_apply_filter()

    def bios_apply_filter(self, *_):
        needle = self.bios_filter.text().strip().lower()
        for r in range(self.bios_table.rowCount()):
            name = self.bios_table.item(r, 0)
            self.bios_table.setRowHidden(r, bool(needle) and needle not in name.text().lower())

    def bios_wanted(self):
        """Attribut -> ausgefüllter „Neuer Wert“, für alle nicht leeren und nicht gesperrten Zeilen."""
        out = {}
        for r in range(self.bios_table.rowCount()):
            name, val = self.bios_table.item(r, 0).text(), self.bios_table.item(r, 2).text().strip()
            if val and not BiosEngine.blocked(name):
                out[name] = val
        return out

    # ---------- UEFI-Vorlagen (eigener Bereich, getrennt von den Profilen in Reiter 2) ----------
    def bios_build_profiles(self):
        self.bios_profiles = {}
        self.bios_profile = QComboBox()
        self.bios_profile.setMinimumWidth(240)
        self.bios_profile.setToolTip(
            T("Eine Vorlage merkt sich Hersteller und die ausgefüllten „Neuer Wert“-Felder (Attribut ->\n"
            "Wert). Eigener Bereich, getrennt von den Profilen in Reiter 2 und ohne Passwörter."))
        self.bios_profile.activated.connect(self.bios_profile_selected)
        bar = QHBoxLayout()
        lab = QLabel(T("UEFI-Vorlage"))
        lab.setToolTip(self.bios_profile.toolTip())
        bar.addWidget(lab)
        bar.addWidget(self.bios_profile)
        for text, tip, slot in (
                (T("Speichern"), T("Speichert die aktuellen „Neuer Wert“-Felder in die gewählte Vorlage.\n"
                              "Ohne gewählte Vorlage wird eine neue angelegt."), self.bios_profile_save),
                (T("Neu …"), T("Legt aus den aktuellen Feldern eine neue Vorlage an."), self.bios_profile_new),
                (T("Umbenennen …"), T("Gibt der gewählten Vorlage einen anderen Namen."), self.bios_profile_rename),
                (T("Löschen"), T("Löscht die gewählte Vorlage."), self.bios_profile_delete)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()
        return bar

    def bios_profile_values(self):
        return {"vendor": self.bios_vendor.currentData() or "", "attrs": self.bios_wanted()}

    def bios_apply_profile(self, p):
        self.bios_vendor.setCurrentIndex(max(0, self.bios_vendor.findData(str(p.get("vendor", "")))))
        attrs = p.get("attrs") if isinstance(p.get("attrs"), dict) else {}
        self.bios_pending = {str(k): str(v) for k, v in attrs.items() if not BiosEngine.blocked(k)}
        for r in range(self.bios_table.rowCount()):
            name = self.bios_table.item(r, 0).text()
            if name in self.bios_pending:
                self.bios_table.item(r, 2).setText(self.bios_pending[name])

    def bios_current_profile(self):
        name = self.bios_profile.currentText()
        return name if name in self.bios_profiles else None

    def bios_refill_profiles(self, select=None):
        self.bios_profile.clear()
        self.bios_profile.addItem(NO_PROFILE)
        self.bios_profile.addItems(sorted(self.bios_profiles, key=str.lower))
        self.bios_profile.setCurrentIndex(max(0, self.bios_profile.findText(select or NO_PROFILE)))

    def bios_read_profiles(self):
        last = None
        try:
            data = json.loads(BIOS_PROFILES.read_text(encoding="utf-8"))
            self.bios_profiles = {str(k): v for k, v in data.get("profiles", {}).items() if isinstance(v, dict)}
            last = data.get("last")
        except (OSError, ValueError, AttributeError):
            self.bios_profiles = {}
        self.bios_refill_profiles(last)
        if self.bios_current_profile():
            self.bios_apply_profile(self.bios_profiles[self.bios_current_profile()])

    def bios_write_profiles(self):
        try:
            BIOS_PROFILES.write_text(json.dumps({"last": self.bios_current_profile(),
                                                 "profiles": self.bios_profiles},
                                                indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError as e:
            QMessageBox.warning(self, T("UEFI-Vorlagen"), T('{0} nicht schreibbar:\n{1}').format(BIOS_PROFILES.name, e))
            return False

    def bios_profile_selected(self, *_):
        name = self.bios_current_profile()
        if name:
            self.bios_apply_profile(self.bios_profiles[name])
            self.bios_status.setText(T('Vorlage „{0}“ geladen').format(name))
        if BIOS_PROFILES.exists():
            self.bios_write_profiles()

    def bios_profile_save(self):
        name = self.bios_current_profile()
        if not name:
            return self.bios_profile_new()
        self.bios_profiles[name] = self.bios_profile_values()
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ gespeichert').format(name))

    def bios_profile_new(self):
        name = self.ask_profile_name(T("Neue UEFI-Vorlage"))
        if not name:
            return
        if name in self.bios_profiles and QMessageBox.question(
                self, T("Neue UEFI-Vorlage"), T('Die Vorlage „{0}“ gibt es schon. Überschreiben?').format(name)
        ) != QMessageBox.StandardButton.Yes:
            return
        self.bios_profiles[name] = self.bios_profile_values()
        self.bios_refill_profiles(name)
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ gespeichert').format(name))

    def bios_profile_rename(self):
        old = self.bios_current_profile()
        if not old:
            self.bios_status.setText(T("Keine Vorlage gewählt"))
            return
        name = self.ask_profile_name(T("UEFI-Vorlage umbenennen"), old)
        if not name or name == old:
            return
        if name in self.bios_profiles:
            QMessageBox.warning(self, T("UEFI-Vorlage umbenennen"), T('Die Vorlage „{0}“ gibt es schon.').format(name))
            return
        self.bios_profiles[name] = self.bios_profiles.pop(old)
        self.bios_refill_profiles(name)
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ heißt jetzt „{1}“').format(old, name))

    def bios_profile_delete(self):
        name = self.bios_current_profile()
        if not name:
            self.bios_status.setText(T("Keine Vorlage gewählt"))
            return
        if QMessageBox.question(self, T("UEFI-Vorlage löschen"), T('Vorlage „{0}“ wirklich löschen?').format(name)
                                ) != QMessageBox.StandardButton.Yes:
            return
        del self.bios_profiles[name]
        self.bios_refill_profiles()
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ gelöscht').format(name))

    # ---------- UEFI-Lauf ----------
    def bios_run(self):
        wanted = self.bios_wanted()
        if not wanted:
            QMessageBox.warning(self, T("UEFI-Einstellungen"), T("Keine „Neuer Wert“-Felder ausgefüllt."))
            return
        rows = [(ip, pw or self.bios_pass.text()) for ip, pw in self.reachable_rows()]
        if not rows:
            QMessageBox.warning(self, T("UEFI-Einstellungen"), T("Die Serverliste (Reiter 2) ist leer."))
            return
        dry = self.bios_dry.isChecked()
        reboot = self.bios_reboot.isChecked()
        if not dry:
            lines = [f"{k} = {v}" for k, v in sorted(wanted.items())]
            info = "\n".join(lines[:15])
            if len(lines) > 15:
                info += "\n" + T('… und {0} weitere').format(len(lines) - 15)
            info += "\n\n" + (
                T("Danach wird jeder Server hart neu gestartet, wie mit der Reset-Taste. Ein laufendes "
                "Betriebssystem wird dabei nicht sauber heruntergefahren.") if reboot else
                T("Es wird kein Neustart ausgelöst; die Änderung wirkt erst beim nächsten Neustart."))
            info += "\n\n" + T("Falsche UEFI-Werte können dazu führen, dass ein Server nicht mehr startet.")
            if not self.confirm(T("Scharf ausführen?"),
                                T('Auf {0} Servern werden jetzt wirklich UEFI-Einstellungen geändert.').format(len(rows)),
                                info, len(rows)):
                return

        self.bios_log.clear()
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.bios_say(T('— {0}, {1} Server —').format(mode, len(rows)), "gray")
        self.bios_worker = BiosEngineThread(
            self, rows=rows, cur_user=self.bios_user.text().strip(), attrs=wanted, dry=dry,
            vendor=self.bios_vendor.currentData() or None, reboot=reboot)
        self.bios_worker.line.connect(self.bios_show_line)
        self.bios_worker.result.connect(self.bios_done)
        self.bios_start.setEnabled(False)
        self.bios_stop.setEnabled(True)
        self.bios_status.setText(T("Läuft …"))
        self.bios_worker.start()

    def bios_say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.bios_log.append(f'<span style="{style}">{html.escape(text)}</span>')

    def bios_show_line(self, line, kind=""):
        self.bios_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def bios_abort(self):
        if self.bios_worker and self.bios_worker.isRunning():
            self.bios_worker.requestInterruption()
            self.bios_stop.setEnabled(False)
            self.bios_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def bios_done(self, code, *_):
        self.bios_start.setEnabled(True)
        self.bios_stop.setEnabled(False)
        self.bios_status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))

    # ---------- DHCP-Server (dnsmasq) ----------
    def build_dhcp(self):
        self.dhcp = None            # dnsmasq-Prozess (Linux)
        self.dhcp_thread = None     # eingebauter Server (Windows)
        self.lease_file = None
        self.lease_state = None

        self.iface = QComboBox()
        self.iface.currentIndexChanged.connect(self.suggest_range)
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip(T("Netzwerkkarten neu einlesen"))
        refresh.clicked.connect(self.fill_ifaces)
        row = QHBoxLayout()
        row.addWidget(self.iface, 1)
        row.addWidget(refresh)
        self.dhcp_from = QLineEdit()
        self.dhcp_to = QLineEdit()
        self.iface.setToolTip(T("Netzwerkkarte dieses Rechners, die am Staging-Switch hängt.\n"
                              "Nur auf dieser Karte werden Adressen vergeben. Sie braucht eine feste IP."))
        tip = (T("Aus diesem Bereich bekommen die Geräte vorübergehend eine Adresse,\n"
               "damit das Tool sie erreichen kann. Er muss im Netz der gewählten Karte liegen\n"
               "und groß genug für alle Geräte sein."))
        self.range_tip = tip
        rng = QHBoxLayout()
        rng.addWidget(self.dhcp_from)
        rng.addWidget(QLabel(T("bis")))
        rng.addWidget(self.dhcp_to)
        self.dhcp_btn = QPushButton(T("DHCP starten"))
        self.dhcp_btn.setMinimumHeight(34)
        self.dhcp_btn.setToolTip(T("Startet bzw. stoppt die Adressvergabe auf der gewählten Karte.\n"
                                 "Nur an einem getrennten Staging-Switch verwenden."))
        self.dhcp_btn.clicked.connect(self.toggle_dhcp)
        self.dhcp_state = QLabel(T("Gestoppt"))
        act = QHBoxLayout()
        act.addWidget(self.dhcp_state)
        act.addStretch()
        act.addWidget(self.dhcp_btn)

        box = QGroupBox(T("DHCP-Server für das Staging-Netz"))
        f = QFormLayout(box)
        f.addRow(T("Netzwerkkarte"), row)
        f.addRow(T("Adressbereich"), rng)
        f.addRow("", QLabel(T("<small><b>Nur an einem getrennten Staging-Switch starten</b>, nie im "
                            "Produktivnetz.</small>")))
        f.addRow(act)

        self.leases = QTableWidget(0, 3)
        self.leases.setHorizontalHeaderLabels([T("Vergebene IP"), T("MAC-Adresse"), T("Gemeldeter Name")])
        self.leases.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.leases.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.leases.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.leases.setToolTip(T("Geräte, die eine Adresse bekommen haben. Der gemeldete Name hilft bei der Zuordnung,\n"
                               "z. B. „idrac-“ plus Service-Tag oder „ILO“ plus Seriennummer."))
        take = QPushButton(T("In Serverliste übernehmen"))
        take.setToolTip(T("Trägt die vergebenen Adressen als „Aktuelle IP“ in die Serverliste ein.\n"
                        "Mit Markierung nur die markierten Geräte, sonst alle."))
        take.clicked.connect(self.take_leases)
        self.lease_count = QLabel(T("0 Geräte"))
        lb = QHBoxLayout()
        lb.addWidget(self.lease_count)
        lb.addStretch()
        lb.addWidget(take)
        box2 = QGroupBox(T("Gefundene Geräte"))
        v = QVBoxLayout(box2)
        v.addWidget(self.leases)
        v.addLayout(lb)

        self.dhcp_log = QTextEdit()
        self.dhcp_log.setReadOnly(True)
        self.dhcp_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.dhcp_log.setMaximumHeight(150)

        self.lease_timer = QTimer(self)
        self.lease_timer.setInterval(2000)
        self.lease_timer.timeout.connect(self.read_leases)
        self.dhcp_ip = None
        self.guard_timer = QTimer(self)             # prüft laufend, ob die Karte noch ein Staging-Netz ist
        self.guard_timer.setInterval(10000)
        self.guard_timer.timeout.connect(self.dhcp_guard)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(box)
        lay.addWidget(box2, 1)
        lay.addWidget(self.dhcp_log)
        self.fill_ifaces()
        return page

    def fill_ifaces(self):
        keep = (self.iface.currentData() or [None])[0]
        self.iface.blockSignals(True)
        self.iface.clear()
        for nic in QNetworkInterface.allInterfaces():
            if nic.flags() & QNetworkInterface.InterfaceFlag.IsLoopBack:
                continue
            label = nic.humanReadableName() if WINDOWS else nic.name()     # Windows: "Ethernet 2"
            v4 = [e for e in nic.addressEntries()
                  if e.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol]
            if v4:
                for e in v4:
                    ip, plen = e.ip().toString(), e.prefixLength()
                    self.iface.addItem(f"{label}  –  {ip}/{plen}", [nic.name(), ip, plen])
            else:
                self.iface.addItem(T('{0}  –  keine IPv4-Adresse').format(label), [nic.name(), None, None])
        names = [self.iface.itemData(i) for i in range(self.iface.count())]
        pick = next((i for i, d in enumerate(names) if d[0] == keep), None)
        if pick is None:                           # sonst erste Karte mit IPv4-Adresse
            pick = next((i for i, d in enumerate(names) if d[1]), 0)
        self.iface.setCurrentIndex(pick)
        self.iface.blockSignals(False)
        self.suggest_range()

    def iface_net(self):
        data = self.iface.currentData()
        if not data or not data[1]:
            return None
        return ipaddress.ip_interface(f"{data[1]}/{data[2]}")

    def suggest_range(self, *_):
        """Bereich vorschlagen, wenn der eingetragene nicht zum Netz der Karte passt."""
        me = self.iface_net()
        if not me:
            return
        try:
            if all(ipaddress.IPv4Address(w.text()) in me.network for w in (self.dhcp_from, self.dhcp_to)):
                return
        except ValueError:
            pass
        net = me.network
        if net.num_addresses >= 256:
            lo, hi = net.network_address + 100, net.network_address + 200
        else:
            lo, hi = net.network_address + 2, net.broadcast_address - 1
        self.dhcp_from.setText(str(lo))
        self.dhcp_to.setText(str(hi))
        self.validate_range()

    def validate_range(self, *_):
        """Markiert ungültige DHCP-Bereichsfelder, gibt eine Fehlermeldung zurück oder None."""
        me = self.iface_net()
        lo, hi = ipv4(self.dhcp_from.text().strip()), ipv4(self.dhcp_to.text().strip())
        msgs = {}
        for w, ip in ((self.dhcp_from, lo), (self.dhcp_to, hi)):
            if not ip:
                msgs[w] = T("Keine gültige IPv4-Adresse")
            elif me and ip not in me.network:
                msgs[w] = T('Liegt nicht im Netz der Karte ({0})').format(me.network)
            elif me and ip in (me.network.network_address, me.network.broadcast_address):
                msgs[w] = T("Netz- oder Broadcast-Adresse")
        if lo and hi and lo > hi and not msgs:
            msgs[self.dhcp_to] = T("Ende liegt vor dem Anfang des Bereichs")
        for w in (self.dhcp_from, self.dhcp_to):
            w.setStyleSheet("QLineEdit { border: 1px solid #d9534f; }" if w in msgs else "")
            w.setToolTip(msgs.get(w) or self.range_tip)
        return next(iter(msgs.values()), None)

    def dlog(self, text, color=None):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        self.dhcp_log.append(f'<span style="{style}">{html.escape(text)}</span>')

    def dhcp_running(self):
        return (bool(self.dhcp) and self.dhcp.state() != QProcess.ProcessState.NotRunning) \
            or (bool(self.dhcp_thread) and self.dhcp_thread.isRunning())

    def toggle_dhcp(self):
        if self.dhcp_running():
            self.stop_dhcp()
        else:
            self.start_dhcp()

    def start_dhcp(self):
        data = self.iface.currentData()
        me = self.iface_net()
        exe = None if WINDOWS else shutil.which("dnsmasq") or next(
            (p for p in ("/usr/bin/dnsmasq", "/usr/sbin/dnsmasq") if os.path.exists(p)), None)
        err = None
        if not data:
            err = T("Keine Netzwerkkarte gefunden.")
        elif not me and WINDOWS:
            err = (T('Die Karte „{0}“ hat keine IPv4-Adresse.\n\nIn den Adaptereinstellungen (ncpa.cpl) bei dieser Karte unter „Internetprotokoll, Version 4“ eine feste Adresse eintragen, z. B. 192.168.0.1 mit Maske 255.255.255.0, Gateway leer lassen.\n\nDanach hier auf ↻ klicken.').format(self.iface.currentText().split('  –  ')[0]))
        elif not me:
            err = (T('Die Karte {0} hat keine IPv4-Adresse. Feste Adresse setzen, z. B. im Terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nDanach hier auf ↻ klicken.').format(data[0], data[0]))
        elif WINDOWS:
            err = self.validate_range()
            if err:
                err = T("Adressbereich: ") + err
            else:
                lo, hi = (ipaddress.IPv4Address(w.text().strip()) for w in (self.dhcp_from, self.dhcp_to))
        elif not exe:
            err = T("dnsmasq ist nicht installiert:\n\nsudo pacman -S dnsmasq")
        elif os.geteuid() != 0 and not shutil.which("pkexec"):
            err = T("pkexec (polkit) fehlt, damit kann der DHCP-Server nicht mit Root-Rechten starten.")
        else:
            err = self.validate_range()
            if err:
                err = T("Adressbereich: ") + err
            else:
                lo, hi = (ipaddress.IPv4Address(w.text().strip()) for w in (self.dhcp_from, self.dhcp_to))
        if err:
            box = QMessageBox(QMessageBox.Icon.Warning, T("DHCP-Server"), err, parent=self)
            box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.exec()
            return

        # Schutz: in einem Netz, das nach Produktivnetz aussieht, startet der DHCP-Server gar nicht erst
        ip, found, block = str(me.ip), None, None
        if not me.ip.is_private:
            block = (T('Die Karte hat eine öffentliche Adresse ({0}). Ein Staging-Netz nutzt private Adressen, deshalb wird hier kein DHCP-Server gestartet.').format(ip))
        elif self.default_route_ip() == ip:
            block = (T("Über diese Karte läuft die Standardroute dieses Rechners (Internet bzw. Firmennetz). "
                     "Das ist kein getrenntes Staging-Netz, deshalb wird hier kein DHCP-Server gestartet.\n\n"
                     "Für das Staging die Karte mit fester Adresse und ohne Gateway einrichten."))
        elif self.address_is_dynamic(data[0], ip):
            block = (T("Diese Karte hat ihre eigene Adresse per DHCP bekommen. In diesem Netz läuft also schon ein "
                     "DHCP-Server, ein zweiter würde dort alle Geräte stören. Deshalb wird hier keiner "
                     "gestartet.\n\nFür das Staging der Karte eine feste Adresse geben."))
        else:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                found = probe_dhcp_servers(ip)
            finally:
                QApplication.restoreOverrideCursor()
            if found:
                others = ", ".join(sorted(found))
                block = (T('Auf dieser Karte antwortet bereits ein DHCP-Server ({0}). Ein zweiter würde dort alle Geräte stören, deshalb wird hier keiner gestartet.').format(others))
        if block:
            QMessageBox.critical(self, T("DHCP-Server nicht gestartet"), block)
            return
        info = (T("Kein anderer DHCP-Server hat innerhalb von 1,5 Sekunden geantwortet.")
                if found is not None else
                T("Ob dort schon ein DHCP-Server läuft, ließ sich nicht prüfen (Port 68 braucht Root-Rechte)."))
        info += " " + (T("Ein zweiter DHCP-Server in einem Produktivnetz stört dort alle Geräte.\n\n"
                       "Hängt diese Karte wirklich an einem getrennten Staging-Switch?"))
        if not self.confirm(T("DHCP-Server starten?"),
                            T('Auf {0} ({1}) werden Adressen von {2} bis {3} vergeben.').format(data[0], me.network, lo, hi), info):
            return

        self.dhcp_log.clear()
        self.lease_state = None
        self.dhcp_btn.setText(T("DHCP stoppen"))
        self.dhcp_ip = ip
        self.guard_timer.start()
        self.dhcp_state.setText(T("<b style='color:#3fa45b'>Läuft</b> auf {0}, {1} bis {2}").format(data[0] if not WINDOWS else data[1], lo, hi))
        self.iface.setEnabled(False)
        self.dhcp_from.setEnabled(False)
        self.dhcp_to.setEnabled(False)

        if WINDOWS:                                  # eingebauter Server, braucht kein Zusatzprogramm
            self.lease_file = None
            self.show_leases([])
            self.dhcp_thread = DhcpThread(self, ip=data[1], mask=str(me.netmask), lo=str(lo), hi=str(hi))
            self.dhcp_thread.line.connect(lambda t: self.dlog(t, "#d9534f" if t.startswith("error") else None))
            self.dhcp_thread.leases.connect(self.show_leases)
            self.dhcp_thread.result.connect(self.dhcp_done)
            self.dhcp_thread.start()
            return

        import getpass
        import grp
        tmp = tempfile.mkdtemp(prefix="bmc-dhcp-")
        self.lease_file = os.path.join(tmp, "leases")
        args = [
            exe, "--keep-in-foreground", "--conf-file=/dev/null",
            "--port=0",                              # kein DNS, nur DHCP
            f"--interface={data[0]}", "--bind-interfaces", "--except-interface=lo",
            f"--dhcp-range={lo},{hi},{me.netmask},1h",
            "--dhcp-option=3", "--dhcp-option=6",    # weder Gateway noch DNS verteilen
            f"--dhcp-leasefile={self.lease_file}", f"--pid-file={os.path.join(tmp, 'pid')}",
            "--log-facility=-", "--log-dhcp",
            # nach dem Start Root-Rechte abgeben, damit das Tool den Dienst selbst beenden kann
            f"--user={getpass.getuser()}", f"--group={grp.getgrgid(os.getgid()).gr_name}",
        ]
        if os.geteuid() != 0:
            args.insert(0, "pkexec")
        self.dlog("$ " + " ".join(args), "gray")
        self.dhcp = QProcess(self)
        self.dhcp.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.dhcp.readyReadStandardOutput.connect(self.dhcp_output)
        self.dhcp.finished.connect(self.dhcp_done)
        self.dhcp.errorOccurred.connect(
            lambda e: e == QProcess.ProcessError.FailedToStart and self.dhcp_done(127))
        self.dhcp.start(args[0], args[1:])
        self.lease_timer.start()

    def dhcp_output(self):
        text = bytes(self.dhcp.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            bad = any(w in line.lower() for w in ("failed", "error", "cannot", "not authorized", "dismissed"))
            self.dlog(line, "#d9534f" if bad else None)

    def stop_dhcp(self):
        if not self.dhcp_running():
            return
        if self.dhcp_thread and self.dhcp_thread.isRunning():
            self.dhcp_thread.requestInterruption()
            self.dhcp_thread.wait(3000)
            return
        pid = self.dhcp.processId()
        self.dhcp.terminate()
        if not self.dhcp.waitForFinished(2000) and os.geteuid() != 0:
            # läuft noch mit Root-Rechten (z. B. Start nicht abgeschlossen): per pkexec beenden
            QProcess.execute("pkexec", ["kill", str(pid)])
            self.dhcp.waitForFinished(3000)

    def dhcp_done(self, code=0, *_):
        self.lease_timer.stop()
        self.guard_timer.stop()
        if self.lease_file:
            self.read_leases()
            shutil.rmtree(os.path.dirname(self.lease_file), ignore_errors=True)
            self.lease_file = None
        self.dhcp_btn.setText(T("DHCP starten"))
        self.iface.setEnabled(True)
        self.dhcp_from.setEnabled(True)
        self.dhcp_to.setEnabled(True)
        hints = {126: T("Anmeldung abgebrochen"), 127: T("Start nicht möglich oder Anmeldung fehlgeschlagen"),
                 2: T("Port 67 belegt oder Karte nicht bereit, siehe Protokoll")}
        if code in (0, 15) or (self.dhcp and self.dhcp.exitStatus() == QProcess.ExitStatus.CrashExit):
            self.dhcp_state.setText(T("Gestoppt"))
        else:
            self.dhcp_state.setText(T("<b style='color:#d9534f'>Beendet (Code {0})</b>: ").format(code)
                                    + hints.get(code, T("siehe Protokoll")))

    def read_leases(self):
        """Lease-Datei von dnsmasq lesen: Ablauf, MAC, IP, Name, Client-ID."""
        try:
            raw = Path(self.lease_file).read_text() if self.lease_file else ""
        except OSError:
            raw = ""
        if raw == self.lease_state:
            return
        self.lease_state = raw
        rows = []
        for line in raw.splitlines():
            f = line.split()
            if len(f) >= 4 and f[0].isdigit():
                rows.append((f[2], f[1], "" if f[3] == "*" else f[3]))
        self.show_leases(rows)

    def show_leases(self, rows):
        """Geräteliste anzeigen: Zeilen aus (IP, MAC, Name)."""
        rows = sorted(rows, key=lambda r: ipaddress.ip_address(r[0]))
        self.leases.setRowCount(len(rows))
        for r, vals in enumerate(rows):
            for c, val in enumerate(vals):
                self.leases.setItem(r, c, QTableWidgetItem(val))
        self.lease_count.setText(T('{0} Geräte').format(len(rows)))

    def take_leases(self):
        sel = sorted({i.row() for i in self.leases.selectedIndexes()}) or range(self.leases.rowCount())
        known = {r[0] for r in self.rows(open_only=True)}      # fertige Server haben ihre alte IP abgegeben
        new = [self.leases.item(r, 0).text() for r in sel]
        new = [ip for ip in new if ip not in known]
        if not new:
            self.status.setText(T("Keine neuen Geräte zum Übernehmen"))
            return
        for r in reversed(range(self.table.rowCount())):          # leere Zeilen entfernen
            if not any(self.cell(r, c) for c in (0, 2, 5)):
                self.table.removeRow(r)
        for ip in new:
            self.add_row()
            self.table.item(self.table.rowCount() - 1, 0).setText(ip)
        self.update_count()
        self.touch()
        self.tabs.setCurrentIndex(1)
        self.status.setText(T('{0} Geräte übernommen: jetzt neue IP und Hostname eintragen').format(len(new)))

    # ---------- Ablauf ----------
    def run(self):
        self.table.setCurrentCell(-1, -1)      # laufende Zellbearbeitung abschließen
        err = self.check()
        if err:
            QMessageBox.warning(self, T("Eingabe prüfen"), err)
            return
        todo = self.rows(open_only=True)       # fertige (grüne) Zeilen werden übersprungen
        if not todo:
            QMessageBox.information(self, T("Nichts zu tun"), T("Alle Server der Liste sind schon umgestellt.\n\n"
                                    "Neue Zeilen eintragen."))
            return
        n = len(todo)
        dry = self.dry.isChecked()
        if not dry:
            nets = [i.network for i in self.local_ifaces()]
            lines, far = [], 0
            for cur, _, new, _, _, host in todo:
                off = not any(ipv4(cur) in net for net in nets)
                far += off
                lines.append(f"{cur} → {new} ({host})" + ("  ⚠" if off else ""))
            info = "\n".join(lines[:15])
            if n > 15:
                info += "\n" + T('… und {0} weitere').format(n - 15)
            if far:
                info += "\n\n" + (T("⚠ Die aktuelle IP liegt in keinem Netz, an dem dieser Rechner direkt hängt. "
                                  "Für ein Staging-Gerät ist das ungewöhnlich. Ist es wirklich das richtige Gerät?"))
            if not self.confirm(T("Scharf ausführen?"),
                                T('{0} Server werden jetzt wirklich umgestellt (Benutzer, Hostname, feste IP).').format(n),
                                info, n if far else None):
                return

        self.log.clear()
        skipped = len(self.rows()) - n
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.say(T('— {0}, {1} Server').format(mode, n) + (T(', {0} fertige übersprungen').format(skipped) if skipped else "") + " —", "gray")
        self.worker = EngineThread(
            self, rows=todo, cur_user=self.cur_user.text().strip(), cur_pass=self.cur_pass.text(),
            new_user=self.new_user.text(), new_pass=self.new_pass.text(), slot=self.slot.value(), dry=dry,
            vendor=self.vendor.currentData() or None)
        self.worker.line.connect(self.show_line)
        self.worker.row_done.connect(self.mark_done)
        self.worker.result.connect(self.done)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.table.setEnabled(False)
        self.status.setText(T("Läuft …"))
        self.worker.start()

    def show_line(self, line, kind=""):
        self.say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def abort(self):
        if self.running():
            self.worker.requestInterruption()
            self.stop.setEnabled(False)
            self.status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def done(self, code, *_):
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        self.table.setEnabled(True)
        self.status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))

    # ---------- Bestandsserver (schon konfigurierte BMCs: Verbindung prüfen) ----------
    def build_existing(self):
        self.ex_worker = None
        self.ex_ok = {}          # (Adresse, Port, Benutzer, Passwort) -> Ergebnis; nur für unverändert getestete Zeilen
        self.ex_vendor = QComboBox()
        for text, key in VENDORS:
            self.ex_vendor.addItem(text, key)
        self.ex_vendor.setToolTip(T("„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller.\n"
                                    "Eine feste Wahl überspringt die Erkennung und gilt für alle Zeilen."))
        self.ex_user_all = QLineEdit()
        self.ex_user_all.setPlaceholderText(T("automatisch je Hersteller"))
        self.ex_pass_all = self._pw()
        self.ex_from = QLineEdit()
        self.ex_from.setPlaceholderText(T("z. B. 10.20.0.11"))
        self.ex_to = QLineEdit()
        self.ex_to.setPlaceholderText(T("z. B. 10.20.0.30"))

        box_all = QGroupBox(T("Zugangsdaten und Adressen schnell eintragen"))
        f = QFormLayout(box_all)
        self.tip_row(f, T("Hersteller"), self.ex_vendor, self.ex_vendor.toolTip())
        self.tip_row(f, T("Benutzer"), self.ex_user_all,
                     T("Benutzer, mit dem sich das Tool am BMC anmeldet. Leer lassen: Werksbenutzer je Hersteller."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.ex_pass_all),
                     T("Gilt für alle Zeilen, wenn Sie unten auf „Für alle Zeilen übernehmen“ klicken.\n"
                       "Abweichende Zugangsdaten tragen Sie direkt in der Tabelle je Zeile ein."))
        apply = QPushButton(T("Für alle Zeilen übernehmen"))
        apply.setToolTip(T("Trägt Benutzer und Passwort von oben in alle Zeilen ein und überschreibt vorhandene Werte."))
        apply.clicked.connect(self.ex_apply_all)
        f.addRow("", apply)
        rng = QHBoxLayout()
        rng.addWidget(self.ex_from)
        rng.addWidget(QLabel("–"))
        rng.addWidget(self.ex_to)
        add_rng = QPushButton(T("Bereich einfügen"))
        add_rng.setToolTip(T("Fügt für jede Adresse von … bis … eine Zeile ein (höchstens 256 auf einmal)."))
        add_rng.clicked.connect(self.ex_add_range)
        rng.addWidget(add_rng)
        f.addRow(T("Adressbereich"), rng)

        self.ex_table = QTableWidget(0, 5)
        self.ex_table.setHorizontalHeaderLabels([T("Adresse (BMC)"), T("Port"), T("Benutzer"), T("Passwort"), T("Status")])
        self.ex_table.setItemDelegateForColumn(3, PwDelegate(self.ex_table))
        self.ex_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ex_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        for c, tip in enumerate((T("IPv4-Adresse des BMC (iDRAC, iLO, Supermicro-IPMI, XCC)."),
                                 T("HTTPS-Port des BMC, normalerweise 443."),
                                 T("Benutzer dieses Geräts. Leer: Werksbenutzer je Hersteller."),
                                 T("Passwort dieses Geräts."),
                                 T("Ergebnis des Verbindungstests."))):
            self.ex_table.horizontalHeaderItem(c).setToolTip(tip)
        add = QPushButton(T("+ Server"))
        add.clicked.connect(lambda: self.ex_add_row())
        rem = QPushButton(T("– Entfernen"))
        rem.clicked.connect(self.ex_remove_rows)
        pull = QPushButton(T("Aus Serverliste holen"))
        pull.setToolTip(T("Übernimmt Adressen und Passwörter aus Reiter 2 (Aktuelle IP, Passwort)."))
        pull.clicked.connect(self.ex_pull)
        btns = QHBoxLayout()
        for b in (add, rem, pull):
            btns.addWidget(b)
        btns.addStretch()
        box_list = QGroupBox(T("Bestandsserver"))
        v = QVBoxLayout(box_list)
        v.addWidget(self.ex_table)
        v.addLayout(btns)

        self.ex_test = QPushButton(T("Verbindung testen"))
        self.ex_test.setMinimumHeight(38)
        self.ex_test.setDefault(True)
        self.ex_test.setToolTip(T("Prüft je Server nur lesend: Port, Verschlüsselung, Hersteller, Anmeldung, Modell,\n"
                                  "Firmware und Administratorrechte. Es wird nichts geändert. Jeder Server bekommt\n"
                                  "genau einen Anmeldeversuch, damit ein falsches Passwort kein Konto sperrt."))
        self.ex_test.clicked.connect(self.ex_run)
        self.ex_stop = QPushButton(T("Abbrechen"))
        self.ex_stop.setEnabled(False)
        self.ex_stop.clicked.connect(self.ex_abort)
        self.ex_take = QPushButton(T("In Reiter 2–4 übernehmen"))
        self.ex_take.setToolTip(T("Übernimmt alle erfolgreich getesteten Server in die Serverliste (Reiter 2) und trägt\n"
                                  "den Benutzer in die Reiter 2, 3 und 4 ein. Danach dort die Einstellungen wählen und starten."))
        self.ex_take.clicked.connect(self.ex_transfer)
        self.ex_status = QLabel(T("Bereit"))
        act = QHBoxLayout()
        act.addWidget(self.ex_status, 1)
        act.addWidget(self.ex_take)
        act.addWidget(self.ex_stop)
        act.addWidget(self.ex_test)

        self.ex_log = QTextEdit()
        self.ex_log.setReadOnly(True)
        self.ex_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        info = QLabel(T("Für Server, die schon konfiguriert sind: erst die Verbindung prüfen, dann die erfolgreichen "
                        "Server in die Reiter 2–4 übernehmen. Das Tool speichert nichts."))
        info.setWordWrap(True)
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.addWidget(info)
        pl.addWidget(box_all)
        split = QSplitter(Qt.Orientation.Vertical)
        upper = QWidget()
        u = QVBoxLayout(upper)
        u.setContentsMargins(0, 0, 0, 0)
        u.addWidget(box_list)
        u.addLayout(act)
        split.addWidget(upper)
        split.addWidget(self.ex_log)
        split.setSizes([320, 240])
        pl.addWidget(split, 1)
        self.ex_add_row()
        return page

    def ex_add_row(self, values=None):
        r = self.ex_table.rowCount()
        values = values or ["", "443", self.ex_user_all.text().strip(), self.ex_pass_all.text()]
        self.ex_table.insertRow(r)
        for c, val in enumerate(values):
            self.ex_table.setItem(r, c, QTableWidgetItem(val))
        st = QTableWidgetItem("")
        st.setFlags(st.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.ex_table.setItem(r, 4, st)
        self.ex_table.setCurrentCell(r, 0)

    def ex_remove_rows(self):
        for r in sorted({i.row() for i in self.ex_table.selectedIndexes()}, reverse=True):
            self.ex_table.removeRow(r)

    def ex_cell(self, r, c):
        item = self.ex_table.item(r, c)
        return item.text().strip() if item else ""

    def ex_apply_all(self):
        for r in range(self.ex_table.rowCount()):
            self.ex_table.item(r, 2).setText(self.ex_user_all.text().strip())
            self.ex_table.item(r, 3).setText(self.ex_pass_all.text())

    def ex_drop_empty(self):
        for r in range(self.ex_table.rowCount() - 1, -1, -1):
            if not any(self.ex_cell(r, c) for c in (0, 2, 3)):
                self.ex_table.removeRow(r)

    def ex_add_range(self):
        a, b = ipv4(self.ex_from.text().strip()), ipv4(self.ex_to.text().strip())
        if not a or not b or int(b) < int(a):
            QMessageBox.warning(self, T("Adressbereich"), T("Bitte eine gültige Start- und Endadresse (Start vor Ende) eintragen."))
            return
        if int(b) - int(a) >= 256:
            QMessageBox.warning(self, T("Adressbereich"), T("Höchstens 256 Adressen auf einmal."))
            return
        self.ex_drop_empty()
        have = {self.ex_cell(r, 0) for r in range(self.ex_table.rowCount())}
        for n in range(int(a), int(b) + 1):
            ip = str(ipaddress.IPv4Address(n))
            if ip not in have:
                self.ex_add_row(["" + ip, "443", self.ex_user_all.text().strip(), self.ex_pass_all.text()])

    def ex_pull(self):
        self.ex_drop_empty()
        have = {self.ex_cell(r, 0) for r in range(self.ex_table.rowCount())}
        for r in range(self.table.rowCount()):
            ip, pw = self.cell(r, 0), self.cell(r, 1) or self.cur_pass.text()
            if ip and ip not in have:
                self.ex_add_row([ip, "443", self.cur_user.text().strip(), pw])
                have.add(ip)

    def ex_say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "") + ("font-weight:bold;" if bold else "")
        self.ex_log.append(f'<span style="{style}">{html.escape(text)}</span>')

    def ex_show_line(self, line, kind=""):
        self.ex_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def ex_collect(self):
        """(Zeile, Adresse[:Port], Benutzer, Passwort) aller ausgefüllten Zeilen und die Fehlermeldungen."""
        rows, errs, seen = [], [], set()
        for r in range(self.ex_table.rowCount()):
            ip, port, user, pw = (self.ex_cell(r, c) for c in range(4))
            if not (ip or user or pw):
                continue
            n, port = r + 1, port or "443"
            if not ipv4(ip):
                errs.append(T('Zeile {0}: keine gültige IPv4-Adresse').format(n))
            elif not (port.isdigit() and 1 <= int(port) <= 65535):
                errs.append(T('Zeile {0}: ungültiger Port').format(n))
            elif not self.ex_table.item(r, 3).text():
                errs.append(T('Zeile {0}: Passwort fehlt').format(n))
            elif (ip, port) in seen:
                errs.append(T('Zeile {0}: Adresse und Port kommen mehrfach vor').format(n))
            else:
                seen.add((ip, port))
                rows.append((r, ip if port == "443" else f"{ip}:{port}", user,
                             self.ex_table.item(r, 3).text()))
        return rows, errs

    def ex_key(self, r):
        return (self.ex_cell(r, 0), self.ex_cell(r, 1) or "443", self.ex_cell(r, 2), self.ex_table.item(r, 3).text())

    def ex_run(self):
        if self.ex_worker and self.ex_worker.isRunning():
            return
        rows, errs = self.ex_collect()
        if errs or not rows:
            QMessageBox.warning(self, T("Bestandsserver"), "\n".join(errs) or T("Die Liste ist leer."))
            return
        self.ex_log.clear()
        self.ex_ok = {}
        self.ex_rowof = {ip: r for r, ip, _, _ in rows}
        for r, *_ in rows:
            self.ex_table.item(r, 4).setText(T("wartet …"))
            self.ex_table.item(r, 4).setBackground(QBrush())
        self.ex_say(T('— Verbindungstest, {0} Server, nur lesend —').format(len(rows)), "gray")
        self.ex_worker = ProbeThread(self, rows=[(ip, u, pw) for _, ip, u, pw in rows],
                                     vendor=self.ex_vendor.currentData() or None)
        self.ex_worker.line.connect(self.ex_show_line)
        self.ex_worker.row.connect(self.ex_row_result)
        self.ex_worker.result.connect(self.ex_done)
        self.ex_test.setEnabled(False)
        self.ex_stop.setEnabled(True)
        self.ex_table.setEnabled(False)
        self.ex_status.setText(T("Läuft …"))
        self.ex_worker.start()

    def ex_row_result(self, ip, res):
        r = self.ex_rowof.get(ip)
        item = self.ex_table.item(r, 4) if r is not None else None
        if item is None:
            return
        if res["ok"]:
            self.ex_ok[self.ex_key(r)] = res
            admin = {True: "", False: " · " + T("kein Admin"), None: ""}[res.get("admin")]
            item.setText(T("OK") + " · " + (res.get("model") or self.NAME_OF(res)) + (f" {res['fw']}" if res.get("fw") else "") + admin)
            item.setBackground(QBrush(QColor("#f0ad4e" if res.get("admin") is False else "#3fa45b")))
        else:
            item.setText(T("Fehler") + ": " + res.get("text", ""))
            item.setBackground(QBrush(QColor("#d9534f")))

    @staticmethod
    def NAME_OF(res):
        return ProbeEngine.NAME.get(res.get("vendor", ""), "")

    def ex_abort(self):
        if self.ex_worker and self.ex_worker.isRunning():
            self.ex_worker.requestInterruption()
            self.ex_stop.setEnabled(False)
            self.ex_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def ex_done(self, code, *_):
        self.ex_test.setEnabled(True)
        self.ex_stop.setEnabled(False)
        self.ex_table.setEnabled(True)
        self.ex_status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))

    def ex_transfer(self):
        good = [(r, self.ex_ok[self.ex_key(r)]) for r in range(self.ex_table.rowCount())
                if self.ex_cell(r, 0) and self.ex_key(r) in self.ex_ok]
        if not good:
            QMessageBox.information(self, T("Bestandsserver"), T("Noch kein erfolgreich getesteter Server. Erst „Verbindung testen“."))
            return
        users = {}
        for r, res in good:
            users.setdefault(res["user"], []).append(r)
        user = max(users, key=lambda k: len(users[k]))
        take = [r for r in users[user] if self.ex_cell(r, 1) in ("", "443")]
        left = len(good) - len(take)
        self.ex_drop_main()
        have = {self.cell(r, 0) for r in range(self.table.rowCount())}
        for r in take:
            ip = self.ex_cell(r, 0)
            if ip not in have:
                self.add_row([ip, self.ex_table.item(r, 3).text(), "", self.def_mask.text().strip(),
                              self.def_gw.text().strip(), ""])
        for w in (self.cur_user, self.bios_user, self.svc_user):
            w.setText(user)
        self.update_count()
        self.touch()
        msg = T('{0} Server in die Serverliste (Reiter 2) übernommen, Benutzer „{1}“ eingetragen.').format(len(take), user)
        if left:
            msg += "\n\n" + T('{0} getestete Server blieben draußen: anderer Benutzer als „{1}“ oder Port ungleich 443. '
                              'Die Reiter 2 bis 4 kennen nur einen Benutzer für alle Server; diese Server in einem zweiten Durchgang übernehmen.').format(left, user)
        QMessageBox.information(self, T("Bestandsserver"), msg)
        self.tabs.setCurrentIndex(1)

    def ex_drop_main(self):
        """Leere Zeilen der Serverliste (Reiter 2) entfernen, damit die übernommenen Server oben stehen."""
        for r in range(self.table.rowCount() - 1, -1, -1):
            if not any(self.cell(r, c) for c in range(len(COLS))):
                self.table.removeRow(r)

    # ---------- Dienste (NTP, Syslog, LDAP, HTTPS-Zertifikat) ----------
    def build_services(self):
        self.svc_worker = None
        self.cert_pem = ""
        self.cert_name = ""

        self.svc_vendor = QComboBox()
        for text, key in VENDORS:
            self.svc_vendor.addItem(text, key)
        self.svc_user = QLineEdit()
        self.svc_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.svc_pass = self._pw()
        box_login = QGroupBox(T("Login (Werksbenutzer, gilt für alle Server der Liste)"))
        box_login.setToolTip(T("Die Dienste werden auf allen Servern der Serverliste (Reiter 2) eingerichtet.\n"
                               "Fertige (grüne) Server werden unter ihrer neuen Adresse angesprochen.\n"
                               "Angemeldet wird mit dem Werksbenutzer; der Werks-Administrator bleibt bestehen."))
        f = QFormLayout(box_login)
        self.tip_row(f, T("Hersteller"), self.svc_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ fragt jedes Gerät."))
        self.tip_row(f, T("Benutzer"), self.svc_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.svc_pass),
                     T("Fällt für Zeilen ein, deren Spalte „Passwort“ in der Serverliste leer ist."))

        self.ntp_box = QGroupBox(T("NTP (Zeitserver)"))
        self.ntp_box.setCheckable(True)
        self.ntp_box.setChecked(False)
        self.ntp1 = QLineEdit()
        self.ntp1.setPlaceholderText(T("z. B. 10.20.0.1 oder pool.ntp.org"))
        self.ntp2 = QLineEdit()
        self.ntp2.setPlaceholderText(T("optional"))
        self.ntp_tz = QLineEdit()
        self.ntp_tz.setPlaceholderText(T("nur Dell, z. B. Europe/Berlin"))
        f = QFormLayout(self.ntp_box)
        self.tip_row(f, T("NTP-Server 1"), self.ntp1, T("Pflicht, wenn NTP eingerichtet wird."))
        self.tip_row(f, T("NTP-Server 2"), self.ntp2, T("Optional, zweiter Zeitserver."))
        self.tip_row(f, T("Zeitzone"), self.ntp_tz,
                     T("Nur bei Dell gesetzt. Bei HPE, Supermicro und Lenovo bitte in der Oberfläche wählen."))

        self.syslog_box = QGroupBox(T("Syslog"))
        self.syslog_box.setCheckable(True)
        self.syslog_box.setChecked(False)
        self.syslog_box.setToolTip(T("Nur Dell und HPE. Bei Supermicro und Lenovo wird Syslog übersprungen."))
        self.syslog_server = QLineEdit()
        self.syslog_server.setPlaceholderText(T("z. B. 10.20.0.9"))
        self.syslog_port = QLineEdit("514")
        f = QFormLayout(self.syslog_box)
        self.tip_row(f, T("Server"), self.syslog_server, T("Adresse des Syslog-Empfängers."))
        self.tip_row(f, T("Port"), self.syslog_port, T("Standard 514."))

        self.ldap_box = QGroupBox(T("LDAP / Active Directory"))
        self.ldap_box.setCheckable(True)
        self.ldap_box.setChecked(False)
        self.ldap_server = QLineEdit()
        self.ldap_server.setPlaceholderText(T("z. B. ad.example.com"))
        self.ldap_port = QLineEdit("636")
        self.ldap_base = QLineEdit()
        self.ldap_base.setPlaceholderText("dc=example,dc=com")
        self.ldap_bind = QLineEdit()
        self.ldap_bind.setPlaceholderText(T("optional, z. B. cn=svc,dc=example,dc=com"))
        self.ldap_bindpw = self._pw()
        self.ldap_userattr = QLineEdit("sAMAccountName")
        self.ldap_groupattr = QLineEdit("memberOf")
        f = QFormLayout(self.ldap_box)
        self.tip_row(f, T("Server"), self.ldap_server, T("Adresse des LDAP-/AD-Servers."))
        self.tip_row(f, T("Port"), self.ldap_port, T("389 unverschlüsselt, 636 für LDAPS."))
        self.tip_row(f, T("Base-DN"), self.ldap_base, T("Ab wo gesucht wird, z. B. dc=example,dc=com."))
        self.tip_row(f, T("Bind-DN"), self.ldap_bind,
                     T("Dienstkonto zum Suchen. Leer lassen, wenn der Server anonyme Suche erlaubt."))
        self.tip_row(f, T("Bind-Passwort"), self._with_eye(self.ldap_bindpw), T("Passwort des Bind-DN."))
        self.tip_row(f, T("Benutzer-Attribut"), self.ldap_userattr,
                     T("Active Directory: sAMAccountName. OpenLDAP oft uid."))
        self.tip_row(f, T("Gruppen-Attribut"), self.ldap_groupattr, T("Meist memberOf."))
        self.ldap_groups = QTableWidget(0, 2)
        self.ldap_groups.setHorizontalHeaderLabels([T("LDAP-Gruppe"), T("Rolle")])
        self.ldap_groups.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ldap_groups.setMaximumHeight(140)
        self.ldap_groups.setToolTip(T("Ordnet LDAP-Gruppen einer Rolle zu: Administrator, Operator oder ReadOnly.\n"
                                      "Leer lassen, wenn keine Gruppen-Zuordnung gebraucht wird."))
        gadd = QPushButton(T("+ Gruppe"))
        gadd.clicked.connect(self.ldap_group_add)
        grem = QPushButton(T("– Gruppe"))
        grem.clicked.connect(self.ldap_group_remove)
        gbar = QHBoxLayout()
        gbar.addWidget(gadd)
        gbar.addWidget(grem)
        gbar.addStretch()
        f.addRow(QLabel(T("Gruppen → Rolle")))
        f.addRow(self.ldap_groups)
        f.addRow(gbar)

        self.cert_box = QGroupBox(T("HTTPS-Zertifikat"))
        self.cert_box.setCheckable(True)
        self.cert_box.setChecked(False)
        self.cert_box.setToolTip(T("Experimentell und je Hersteller verschieden. Importiert ein von einer CA signiertes\n"
                                   "Zertifikat (PEM). Bei HPE muss der passende Schlüssel aus einer auf dem iLO erzeugten\n"
                                   "Zertifikatsanforderung (CSR) stammen, sonst scheitert der Import."))
        self.cert_label = QLabel(T("keine Datei gewählt"))
        cert_btn = QPushButton(T("PEM-Datei wählen …"))
        cert_btn.clicked.connect(self.cert_load)
        f = QFormLayout(self.cert_box)
        crow = QHBoxLayout()
        crow.addWidget(cert_btn)
        crow.addWidget(self.cert_label, 1)
        f.addRow(crow)

        settings = QWidget()
        sv = QVBoxLayout(settings)
        for w in (box_login, self.ntp_box, self.syslog_box, self.ldap_box, self.cert_box):
            sv.addWidget(w)
        sv.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(settings)

        self.svc_dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.svc_dry.setChecked(True)
        self.svc_dry.setToolTip(T("Mit Haken wird nichts geändert; das Protokoll zeigt nur die Aufrufe."))
        self.svc_start = QPushButton(T("Trockenlauf für alle Server der Liste"))
        self.svc_start.setMinimumHeight(34)
        self.svc_start.clicked.connect(self.services_run)
        self.svc_stop = QPushButton(T("Abbrechen"))
        self.svc_stop.setEnabled(False)
        self.svc_stop.clicked.connect(self.svc_abort)
        self.svc_status = QLabel(T("Bereit"))
        act = QHBoxLayout()
        act.addWidget(self.svc_dry)
        act.addStretch()
        act.addWidget(self.svc_stop)
        act.addWidget(self.svc_start)

        self.svc_log = QTextEdit()
        self.svc_log.setReadOnly(True)
        self.svc_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        page = QWidget()
        pl = QVBoxLayout(page)
        hint = QLabel("<small>" + T("Nur angehakte Abschnitte werden angewendet. Alles wird auf den Servern der "
                                    "Liste in Reiter 2 gesetzt, grüne unter ihrer neuen Adresse.") + "</small>")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(scroll)
        low = QWidget()
        ll = QVBoxLayout(low)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(act)
        ll.addWidget(self.svc_log)
        split.addWidget(low)
        split.setSizes([470, 210])
        pl.addWidget(split, 1)
        return page

    def ldap_group_add(self):
        r = self.ldap_groups.rowCount()
        self.ldap_groups.insertRow(r)
        self.ldap_groups.setItem(r, 0, QTableWidgetItem(""))
        self.ldap_groups.setItem(r, 1, QTableWidgetItem("Administrator"))

    def ldap_group_remove(self):
        for r in sorted({i.row() for i in self.ldap_groups.selectedIndexes()}, reverse=True):
            self.ldap_groups.removeRow(r)

    def cert_load(self):
        path, _ = QFileDialog.getOpenFileName(self, T("Zertifikat wählen"), str(HERE),
                                              T("Zertifikat (*.pem *.crt *.cer);;Alle Dateien (*)"))
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            QMessageBox.warning(self, T("Zertifikat"), T('Datei nicht lesbar:\n{0}').format(e))
            return
        if "BEGIN CERTIFICATE" not in text:
            QMessageBox.warning(self, T("Zertifikat"),
                                T("Die Datei enthält kein PEM-Zertifikat (erwartet „BEGIN CERTIFICATE“)."))
            return
        self.cert_pem = text
        self.cert_name = os.path.basename(path)
        self.cert_label.setText(self.cert_name)

    def services_dict(self):
        """Sammelt die angehakten Dienste und prüft die Pflichtfelder. Gibt ein dict zurück, {} wenn
        nichts angehakt ist, oder None bei einem Eingabefehler (dann wird eine Meldung gezeigt)."""
        def bad(widget, msg):
            self.tabs.setCurrentIndex(3)
            QMessageBox.warning(self, T("Dienste"), msg)
            if widget:
                widget.setFocus()
            return None
        out = {}
        if self.ntp_box.isChecked():
            if not self.ntp1.text().strip():
                return bad(self.ntp1, T("NTP: mindestens den ersten Server eintragen."))
            out["ntp"] = {"servers": [self.ntp1.text(), self.ntp2.text()], "timezone": self.ntp_tz.text()}
        if self.syslog_box.isChecked():
            if not self.syslog_server.text().strip():
                return bad(self.syslog_server, T("Syslog: Server eintragen."))
            port = self.syslog_port.text().strip() or "514"
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                return bad(self.syslog_port, T("Port muss eine Zahl von 1 bis 65535 sein."))
            out["syslog"] = {"server": self.syslog_server.text(), "port": int(port)}
        if self.ldap_box.isChecked():
            if not self.ldap_server.text().strip() or not self.ldap_base.text().strip():
                return bad(self.ldap_server, T("LDAP: Server und Base-DN eintragen."))
            port = self.ldap_port.text().strip() or "636"
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                return bad(self.ldap_port, T("Port muss eine Zahl von 1 bis 65535 sein."))
            groups = []
            for r in range(self.ldap_groups.rowCount()):
                g = self.ldap_groups.item(r, 0).text().strip() if self.ldap_groups.item(r, 0) else ""
                role = self.ldap_groups.item(r, 1).text().strip() if self.ldap_groups.item(r, 1) else ""
                if g:
                    if role and role not in ServicesEngine.ROLES:
                        return bad(self.ldap_groups,
                                   T("Rolle muss Administrator, Operator oder ReadOnly sein (Gruppe {0}).").format(g))
                    groups.append((g, role))
            out["ldap"] = {"server": self.ldap_server.text(), "port": int(port), "base": self.ldap_base.text(),
                           "bind_dn": self.ldap_bind.text(), "bind_pw": self.ldap_bindpw.text(),
                           "user_attr": self.ldap_userattr.text(), "group_attr": self.ldap_groupattr.text(),
                           "groups": groups}
        if self.cert_box.isChecked():
            if not self.cert_pem.strip():
                return bad(None, T("HTTPS-Zertifikat: erst eine PEM-Datei wählen."))
            out["cert"] = {"pem": self.cert_pem}
        return out

    def services_run(self):
        services = self.services_dict()
        if services is None:
            return
        if not services:
            QMessageBox.warning(self, T("Dienste"), T("Kein Abschnitt angehakt. Oben mindestens einen aktivieren."))
            return
        rows = [(ip, pw or self.svc_pass.text()) for ip, pw in self.reachable_rows()]
        if not rows:
            QMessageBox.warning(self, T("Dienste"), T("Die Serverliste (Reiter 2) ist leer."))
            return
        dry = self.svc_dry.isChecked()
        names = {"ntp": "NTP", "syslog": "Syslog", "ldap": "LDAP", "cert": T("HTTPS-Zertifikat")}
        chosen = ", ".join(names[k] for k in ("ntp", "syslog", "ldap", "cert") if k in services)
        if not dry:
            info = T("Dienste: {0}").format(chosen) + "\n\n" + T(
                "Es werden Verwaltungseinstellungen der BMCs geändert. Eine falsche LDAP- oder "
                "Zertifikatseinstellung kann die Anmeldung erschweren; der lokale Administrator bleibt "
                "aber bestehen.")
            if not self.confirm(T("Scharf ausführen?"),
                                T('Auf {0} Servern werden jetzt wirklich Dienste eingerichtet.').format(len(rows)),
                                info, len(rows)):
                return
        self.svc_log.clear()
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.svc_say(T('— {0}, {1} Server, {2} —').format(mode, len(rows), chosen), "gray")
        self.svc_worker = ServicesEngineThread(
            self, rows=rows, cur_user=self.svc_user.text().strip(), services=services, dry=dry,
            vendor=self.svc_vendor.currentData() or None)
        self.svc_worker.line.connect(self.svc_show_line)
        self.svc_worker.result.connect(self.svc_done)
        self.svc_start.setEnabled(False)
        self.svc_stop.setEnabled(True)
        self.svc_status.setText(T("Läuft …"))
        self.svc_worker.start()

    def svc_say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.svc_log.append(f'<span style="{style}">{html.escape(text)}</span>')

    def svc_show_line(self, line, kind=""):
        self.svc_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def svc_abort(self):
        if self.svc_worker and self.svc_worker.isRunning():
            self.svc_worker.requestInterruption()
            self.svc_stop.setEnabled(False)
            self.svc_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def svc_done(self, code, *_):
        self.svc_start.setEnabled(True)
        self.svc_stop.setEnabled(False)
        self.svc_status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))

    def svc_mode_changed(self, *_):
        dry = self.svc_dry.isChecked()
        self.svc_start.setText(T("Trockenlauf für alle Server der Liste") if dry
                               else T("Scharf auf allen Servern der Liste setzen"))
        self.svc_start.setStyleSheet("" if dry else LIVE_CSS)

    def export_services(self):
        return {
            "vendor": self.svc_vendor.currentData() or "", "user": self.svc_user.text(), "pass": self.svc_pass.text(),
            "ntp_on": self.ntp_box.isChecked(), "ntp1": self.ntp1.text(), "ntp2": self.ntp2.text(),
            "tz": self.ntp_tz.text(), "syslog_on": self.syslog_box.isChecked(),
            "syslog_server": self.syslog_server.text(), "syslog_port": self.syslog_port.text(),
            "ldap_on": self.ldap_box.isChecked(), "ldap_server": self.ldap_server.text(),
            "ldap_port": self.ldap_port.text(), "ldap_base": self.ldap_base.text(), "ldap_bind": self.ldap_bind.text(),
            "ldap_bindpw": self.ldap_bindpw.text(), "ldap_userattr": self.ldap_userattr.text(),
            "ldap_groupattr": self.ldap_groupattr.text(),
            "ldap_groups": [(self.ldap_groups.item(r, 0).text() if self.ldap_groups.item(r, 0) else "",
                             self.ldap_groups.item(r, 1).text() if self.ldap_groups.item(r, 1) else "")
                            for r in range(self.ldap_groups.rowCount())],
            "cert_on": self.cert_box.isChecked(), "cert_pem": self.cert_pem, "cert_name": self.cert_name,
        }

    def import_services(self, s):
        self.svc_vendor.setCurrentIndex(max(0, self.svc_vendor.findData(s["vendor"])))
        self.svc_user.setText(s["user"])
        self.svc_pass.setText(s["pass"])
        self.ntp_box.setChecked(s["ntp_on"])
        self.ntp1.setText(s["ntp1"])
        self.ntp2.setText(s["ntp2"])
        self.ntp_tz.setText(s["tz"])
        self.syslog_box.setChecked(s["syslog_on"])
        self.syslog_server.setText(s["syslog_server"])
        self.syslog_port.setText(s["syslog_port"])
        self.ldap_box.setChecked(s["ldap_on"])
        self.ldap_server.setText(s["ldap_server"])
        self.ldap_port.setText(s["ldap_port"])
        self.ldap_base.setText(s["ldap_base"])
        self.ldap_bind.setText(s["ldap_bind"])
        self.ldap_bindpw.setText(s["ldap_bindpw"])
        self.ldap_userattr.setText(s["ldap_userattr"])
        self.ldap_groupattr.setText(s["ldap_groupattr"])
        for g, role in s["ldap_groups"]:
            r = self.ldap_groups.rowCount()
            self.ldap_groups.insertRow(r)
            self.ldap_groups.setItem(r, 0, QTableWidgetItem(g))
            self.ldap_groups.setItem(r, 1, QTableWidgetItem(role))
        self.cert_box.setChecked(s["cert_on"])
        self.cert_pem = s["cert_pem"]
        self.cert_name = s["cert_name"]
        self.cert_label.setText(self.cert_name or T("keine Datei gewählt"))

    def confirm(self, title, text, info="", type_count=None):
        """Sicherheitsabfrage, bei der „Abbrechen“ vorausgewählt ist. Mit type_count muss zusätzlich
        diese Zahl eingetippt werden."""
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        if info:
            box.setInformativeText(info)
        b_yes = box.addButton(T("Ja"), QMessageBox.ButtonRole.YesRole)
        b_no = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_no)
        box.setEscapeButton(b_no)
        box.exec()
        if box.clickedButton() is not b_yes:
            return False
        if type_count is None:
            return True
        typed, good = QInputDialog.getText(
            self, title, T("Zur Bestätigung die Anzahl der betroffenen Server eintippen:"))
        if good and typed.strip() == str(type_count):
            return True
        if good:
            QMessageBox.information(self, title, T("Die Zahl stimmt nicht. Es wurde nichts ausgeführt."))
        return False

    @staticmethod
    def local_ifaces():
        """IPv4-Adressen dieses Rechners samt Netz (ohne Loopback)."""
        out = []
        for nic in QNetworkInterface.allInterfaces():
            for e in nic.addressEntries():
                if e.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol and not e.ip().isLoopback():
                    try:
                        out.append(ipaddress.ip_interface(f"{e.ip().toString()}/{e.prefixLength()}"))
                    except ValueError:
                        pass
        return out

    @staticmethod
    def default_route_ip():
        """Eigene Adresse der Karte, über die die Standardroute läuft, oder None. Es wird nichts gesendet."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))         # Testnetz-Adresse; UDP-connect wählt nur die Route
            return s.getsockname()[0]
        except OSError:
            return None
        finally:
            s.close()

    @staticmethod
    def address_is_dynamic(name, ip):
        """True, wenn die Karte ihre Adresse per DHCP bekommen hat (dann läuft in dem Netz schon ein
        DHCP-Server). None, wenn es sich nicht feststellen lässt."""
        try:
            if WINDOWS:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"(Get-NetIPAddress -IPAddress {ip} -AddressFamily IPv4).PrefixOrigin"],
                    capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout.strip()
                return "dhcp" in out.lower() if out else None
            out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", name],
                                 capture_output=True, text=True, timeout=3).stdout
            for line in out.splitlines():
                if f" {ip}/" in line:
                    return " dynamic" in line
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return None

    def dhcp_guard(self):
        """Läuft alle paar Sekunden mit: stoppt den DHCP-Server, sobald die Karte kein Staging-Netz mehr ist."""
        if self.dhcp_running() and self.default_route_ip() == self.dhcp_ip:
            self.stop_dhcp()
            self.dlog(T("DHCP-Server gestoppt: Über diese Karte läuft jetzt die Standardroute, "
                      "das ist kein getrenntes Staging-Netz mehr."), "#d9534f")

    def reopen_rows(self):
        """Nimmt bei den markierten Zeilen die grüne Fertig-Markierung zurück."""
        self.table.blockSignals(True)
        for r in {i.row() for i in self.table.selectedIndexes()}:
            if self.table.item(r, 0):
                self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, None)
        self.table.blockSignals(False)
        self.update_count()

    STATE_FIELDS = ("cur_user", "cur_pass", "new_user", "new_pass", "new_pass2", "def_mask", "def_gw", "dhcp_from", "dhcp_to",
                    "bios_user", "bios_pass")

    def export_state(self):
        """Alles, was beim Sprachwechsel ins neu aufgebaute Fenster mitgenommen wird."""
        return {
            "geometry": self.saveGeometry(), "tab": self.tabs.currentIndex(), "dirty": self.dirty,
            "profile": self.current_profile(), "vendor": self.vendor.currentData() or "",
            "slot": self.slot.value(), "iface": (self.iface.currentData() or [None])[0],
            "texts": {k: getattr(self, k).text() for k in self.STATE_FIELDS},
            "rows": [([self.cell(r, c) for c in range(len(COLS))], self.is_done(r))
                     for r in range(self.table.rowCount())],
            "bios_profile": self.bios_current_profile(), "bios_vendor": self.bios_vendor.currentData() or "",
            "bios_ref": self.bios_ref.currentText(), "bios_live": dict(self.bios_live),
            "bios_new": self.bios_wanted(), "bios_pending": dict(self.bios_pending),
            "services": self.export_services(),
        }

    def import_state(self, s):
        self.restoreGeometry(s["geometry"])
        self.profile.setCurrentIndex(max(0, self.profile.findText(s["profile"] or NO_PROFILE)))
        self.vendor.setCurrentIndex(max(0, self.vendor.findData(s["vendor"])))
        self.slot.setValue(s["slot"])
        for i in range(self.iface.count()):
            if self.iface.itemData(i)[0] == s["iface"]:
                self.iface.setCurrentIndex(i)
        for key, text in s["texts"].items():
            getattr(self, key).setText(text)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for vals, done in s["rows"]:
            self.add_row(vals)
            if done:
                self.table.item(self.table.rowCount() - 1, 0).setData(Qt.ItemDataRole.UserRole, vals[0])
        self.table.blockSignals(False)
        self.bios_profile.setCurrentIndex(max(0, self.bios_profile.findText(s["bios_profile"] or NO_PROFILE)))
        self.bios_vendor.setCurrentIndex(max(0, self.bios_vendor.findData(s["bios_vendor"])))
        self.bios_live = s["bios_live"]
        self.bios_pending = dict(s["bios_pending"], **s["bios_new"])
        self.bios_fill_table(keep_values=False)
        self.bios_pending = s["bios_pending"]
        self.bios_refresh_ips()
        self.bios_ref.setEditText(s["bios_ref"])
        self.import_services(s["services"])
        self.validate_defaults()
        self.validate_range()
        self.update_count()
        self.tabs.setCurrentIndex(s["tab"])
        self.dirty = s["dirty"]

    def switch_language(self, *_):
        """Baut das Fenster in der gewählten Sprache neu auf; Eingaben und Serverliste bleiben erhalten."""
        global MAIN
        code = self.lang.currentData()
        if code == LANG:
            return
        if self.running() or self.dhcp_running() or (self.bios_reader and self.bios_reader.isRunning()) \
                or self.console_active():
            self.lang.setCurrentIndex(self.lang.findData(LANG))
            QMessageBox.information(self, T("Sprache"), T("Die Sprache lässt sich nur wechseln, solange kein Lauf "
                                    "aktiv ist und der DHCP-Server gestoppt ist."))
            return
        self.table.setCurrentCell(-1, -1)
        state = self.export_state()
        set_language(code)
        self.write_profiles()                   # merkt sich die Sprache für den nächsten Start
        MAIN = Window(state)
        MAIN.show()
        self.switching = True
        self.close()
        self.deleteLater()

    def update_missing(self, *_):
        """Hebt Pflichtfelder hervor, die noch leer (gelb) oder fehlerhaft (rot) sind, und schreibt über
        den Start-Knopf, was vor dem Lauf noch fehlt."""
        if not hasattr(self, "todo"):
            return
        user, pw, pw2 = self.new_user.text(), self.new_pass.text(), self.new_pass2.text()
        open_rows = self.rows(open_only=True)
        marks = {
            self.new_user: "miss" if not user else "" if USER_RE.match(user) else "err",
            self.new_pass: "miss" if not pw else "" if len(pw) >= 8 else "err",
            self.new_pass2: "miss" if not pw2 else "" if pw2 == pw else "err",
            self.cur_pass: "miss" if not self.cur_pass.text() and any(not v[1] for v in open_rows) else "",
        }
        for w, mark in marks.items():
            w.setStyleSheet({"miss": MISS_CSS, "err": ERR_CSS}.get(mark, ""))
        todo = []
        if marks[self.new_user]:
            todo.append(T("Neuer Administrator: Benutzer"))
        if marks[self.new_pass] or marks[self.new_pass2]:
            todo.append(T("Neuer Administrator: Passwort"))
        if marks[self.cur_pass]:
            todo.append(T("Werkspasswort"))
        if not self.rows():
            todo.append(T("mindestens ein Server in der Liste"))
        if self.cells_missing:
            todo.append(T("{0} leere Pflichtfelder in der Serverliste").format(self.cells_missing))
        if self.cells_bad:
            todo.append(T("{0} fehlerhafte Felder in der Serverliste").format(self.cells_bad))
        if todo:
            self.todo.setText("<span style='background:#f0ad4e; color:#000000'>&nbsp;" + T("Es fehlt noch:")
                              + "&nbsp;</span> " + html.escape(", ".join(todo)))
            return
        if not open_rows:
            text = T("Alle Server der Liste sind fertig.")
        elif self.dry.isChecked():
            text = T("Alles ausgefüllt. Jetzt den Trockenlauf starten.")
        else:
            text = T("Alles ausgefüllt. Der nächste Start stellt die Geräte wirklich um.")
        self.todo.setText("<span style='background:#3fa45b; color:#ffffff'>&nbsp;OK&nbsp;</span> " + html.escape(text))

    def mode_changed(self, *_):
        """Der Start-Knopf sagt immer, was er tun wird; scharf ist er rot."""
        dry = self.dry.isChecked()
        self.start.setText(T("Trockenlauf starten") if dry else T("Scharf starten"))
        self.start.setStyleSheet("" if dry else LIVE_CSS)
        self.update_missing()

    def bios_mode_changed(self, *_):
        dry = self.bios_dry.isChecked()
        self.bios_start.setText(T("Trockenlauf für alle Server der Liste") if dry
                                else T("Scharf auf allen Servern der Liste setzen"))
        self.bios_start.setStyleSheet("" if dry else LIVE_CSS)

    def save_log(self):
        """Schreibt das Protokoll des letzten Laufs in eine Textdatei (enthält keine Passwörter)."""
        text = self.log.toPlainText()
        if not text.strip():
            self.status.setText(T("Das Protokoll ist noch leer."))
            return
        name = "bmc-steward-" + time.strftime("%Y%m%d-%H%M%S") + ".log"
        path, _ = QFileDialog.getSaveFileName(self, T("Protokoll speichern"), str(HERE / name),
                                              T("Text (*.log *.txt);;Alle Dateien (*)"))
        if not path:
            return
        try:
            Path(path).write_text(text + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, T("Protokoll speichern"), T('Datei nicht schreibbar:\n{0}').format(e))
            return
        self.status.setText(T("Protokoll gespeichert in {0}").format(path))


# ======================================================================
# Übersetzungen: deutscher Text -> (English, Français, Schwiizerdütsch)
# ======================================================================
TR = {
"Seriennummer {0}": [
"serial number {0}",
"numéro de série {0}",
"Serienummere {0}"
],
"Service-Tag/SKU {0}": [
"service tag/SKU {0}",
"service tag/SKU {0}",
"Service-Tag/SKU {0}"
],
"Inventar (neue IP, Modell, Seriennummer):": [
"Inventory (new IP, model, serial number):",
"Inventaire (nouvelle IP, modèle, numéro de série) :",
"Inventar (neui IP, Modäll, Serienummere):"
],
"(kein Profil)": [
"(no profile)",
"(aucun profil)",
"(kei Profil)"
],
"automatisch je Hersteller": [
"automatic per vendor",
"automatique selon le constructeur",
"automatisch je Hersteller"
],
"Werkspasswort": [
"factory password",
"mot de passe d'usine",
"Werkspasswort"
],
"ab Werk calvin oder laut Service-Tag-Auszug": [
"factory default calvin, or as on the service tag sheet",
"calvin par défaut, ou selon la fiche du Service Tag",
"ab Werk calvin oder so wie's uf em Service-Tag-Uuszug staht"
],
"steht auf dem Anhänger am Gerät": [
"printed on the pull-out tag on the device",
"indiqué sur l'étiquette amovible de l'appareil",
"staht uf em Aahänger am Grät"
],
"steht auf dem Aufkleber am Gerät": [
"printed on the sticker on the device",
"indiqué sur l'autocollant de l'appareil",
"staht uf em Chläber am Grät"
],
"Geprüft: Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Andere Generationen zuerst an einem einzelnen Gerät testen.": [
"Tested: Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Try other generations on a single device first.",
"Testé : Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Essayer d'abord les autres générations sur un seul appareil.",
"Prüeft: Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Anderi Generatione zerscht a eim einzelne Grät teste."
],
"iDRAC9, 14. bis 16. Generation (z. B. R6625). iDRAC10 (17. Generation) ist nicht geprüft, einzelne Aufrufe können dort abweichen.": [
"iDRAC9, 14th to 16th generation (e.g. R6625). iDRAC10 (17th generation) is untested; individual calls may differ there.",
"iDRAC9, 14e à 16e génération (p. ex. R6625). iDRAC10 (17e génération) n'est pas testé, certains appels peuvent y différer.",
"iDRAC9, 14. bis 16. Generation (z. B. R6625). iDRAC10 (17. Generation) isch nöd prüeft, einzelni Uufrüef chönd det abwiiche."
],
"iLO 5 und 6 (Gen10, Gen10 Plus, Gen11, z. B. DL345/DL365). iLO 7 (Gen12) ist nicht geprüft.": [
"iLO 5 and 6 (Gen10, Gen10 Plus, Gen11, e.g. DL345/DL365). iLO 7 (Gen12) is untested.",
"iLO 5 et 6 (Gen10, Gen10 Plus, Gen11, p. ex. DL345/DL365). iLO 7 (Gen12) n'est pas testé.",
"iLO 5 und 6 (Gen10, Gen10 Plus, Gen11, z. B. DL345/DL365). iLO 7 (Gen12) isch nöd prüeft."
],
"Aktuelle X12/X13-Boards mit Redfish 1.x, auch baugleiche Nutanix-NX-Nodes. Bei älteren Boards können einzelne Redfish-Funktionen eine Lizenz erfordern.": [
"Current X12/X13 boards with Redfish 1.x, including identical Nutanix NX nodes. On older boards some Redfish functions may need a licence.",
"Cartes X12/X13 actuelles avec Redfish 1.x, y compris les nœuds Nutanix NX identiques. Sur les cartes plus anciennes, certaines fonctions Redfish peuvent exiger une licence.",
"Aktuelli X12/X13-Boards mit Redfish 1.x, au baugliichi Nutanix-NX-Nodes. Bi ältere Boards chönd einzelni Redfish-Funktione e Lizänz bruuche."
],
"ThinkSystem mit XCC2/XCC3 (z. B. SR6xx, SR8xx). Ältere Systeme mit XCC1 sind nicht geprüft.": [
"ThinkSystem with XCC2/XCC3 (e.g. SR6xx, SR8xx). Older systems with XCC1 are untested.",
"ThinkSystem avec XCC2/XCC3 (p. ex. SR6xx, SR8xx). Les systèmes plus anciens avec XCC1 ne sont pas testés.",
"ThinkSystem mit XCC2/XCC3 (z. B. SR6xx, SR8xx). Älteri System mit XCC1 sind nöd prüeft."
],
"Adresse, unter der der BMC jetzt erreichbar ist, z. B. die per DHCP vergebene.": [
"Address where the BMC can be reached right now, e.g. the one assigned by DHCP.",
"Adresse à laquelle le BMC est joignable actuellement, p. ex. celle attribuée par DHCP.",
"Adrässe, under dere de BMC jetzt erreichbar isch, z. B. die vom DHCP."
],
"Aktuelles Passwort dieses Geräts. Nur nötig, wenn es vom Werkspasswort oben abweicht\n(bei HPE, Supermicro und Lenovo ist es pro Gerät verschieden).": [
"Current password of this device. Only needed if it differs from the factory password above\n(on HPE, Supermicro and Lenovo it differs per device).",
"Mot de passe actuel de cet appareil. Nécessaire seulement s'il diffère du mot de passe d'usine ci-dessus\n(chez HPE, Supermicro et Lenovo, il est différent pour chaque appareil).",
"Aktuells Passwort vo dem Grät. Nur nötig, wenn's vom Werkspasswort obe abwiicht\n(bi HPE, Supermicro und Lenovo isch's pro Grät verschide)."
],
"Feste Adresse, die der BMC bekommen soll.": [
"Static address the BMC should get.",
"Adresse fixe que le BMC doit recevoir.",
"Feschti Adrässe, wo de BMC söll übercho."
],
"Subnetzmaske zur neuen Adresse, z. B. 255.255.255.0. Eingabe als /24 wird umgewandelt.": [
"Subnet mask for the new address, e.g. 255.255.255.0. Input like /24 is converted.",
"Masque de sous-réseau de la nouvelle adresse, p. ex. 255.255.255.0. Une saisie comme /24 est convertie.",
"Subnetzmaske zu de neue Adrässe, z. B. 255.255.255.0. E Iigab wie /24 wird umgwandlet."
],
"Gateway im Netz der neuen Adresse.": [
"Gateway in the network of the new address.",
"Passerelle dans le réseau de la nouvelle adresse.",
"Gateway im Netz vo de neue Adrässe."
],
"Name, den der BMC bekommt (iDRAC-, iLO- bzw. BMC-Hostname), nicht der Name des Betriebssystems.": [
"Name the BMC gets (iDRAC, iLO or BMC hostname), not the name of the operating system.",
"Nom donné au BMC (nom d'hôte iDRAC, iLO ou BMC), pas celui du système d'exploitation.",
"Name, wo de BMC überchunnt (iDRAC-, iLO- bzw. BMC-Hostname), nöd de Name vom Betriebssystem."
],
"Aktuelle IP": [
"Current IP",
"IP actuelle",
"Aktuelli IP"
],
"Passwort": [
"Password",
"Mot de passe",
"Passwort"
],
"Neue IP": [
"New IP",
"Nouvelle IP",
"Neui IP"
],
"Subnetzmaske": [
"Subnet mask",
"Masque de sous-réseau",
"Subnetzmaske"
],
"Gateway": [
"Gateway",
"Passerelle",
"Gateway"
],
"Hostname": [
"Hostname",
"Nom d'hôte",
"Hostname"
],
"Keine gültige IPv4-Adresse": [
"Not a valid IPv4 address",
"Adresse IPv4 non valide",
"Kei gültigi IPv4-Adrässe"
],
"Kommas im Passwort werden nicht unterstützt": [
"Commas in the password are not supported",
"Les virgules dans le mot de passe ne sont pas prises en charge",
"Komma im Passwort gönd nöd"
],
"Keine gültige Subnetzmaske (z. B. 255.255.255.0 oder /24)": [
"Not a valid subnet mask (e.g. 255.255.255.0 or /24)",
"Masque de sous-réseau non valide (p. ex. 255.255.255.0 ou /24)",
"Kei gültigi Subnetzmaske (z. B. 255.255.255.0 oder /24)"
],
"Gateway und neue IP sind identisch": [
"Gateway and new IP are identical",
"La passerelle et la nouvelle IP sont identiques",
"Gateway und neui IP sind gliich"
],
"Nur Buchstaben, Ziffern und Bindestrich (nicht am Anfang/Ende), höchstens 63 Zeichen": [
"Only letters, digits and hyphen (not at the start or end), at most 63 characters",
"Uniquement lettres, chiffres et tiret (pas au début ni à la fin), 63 caractères au maximum",
"Nur Buechstabe, Ziffere und Bindestrich (nöd am Aafang/Änd), höchschtens 63 Zeiche"
],
"Fertig: {0} konfiguriert, {1} Fehler": [
"Done: {0} configured, {1} errors",
"Terminé : {0} configurés, {1} erreurs",
"Fertig: {0} konfiguriert, {1} Fähler"
],
"Fertig: {0} Server ohne Fehler, {1} mit Fehlern": [
"Done: {0} servers without errors, {1} with errors",
"Terminé : {0} serveurs sans erreur, {1} avec erreurs",
"Fertig: {0} Server ohni Fähler, {1} mit Fähler"
],
"Aus diesem Bereich bekommen die Geräte vorübergehend eine Adresse,\ndamit das Tool sie erreichen kann. Er muss im Netz der gewählten Karte liegen\nund groß genug für alle Geräte sein.": [
"Devices temporarily get an address from this range\nso the tool can reach them. It must lie in the network of the selected card\nand be large enough for all devices.",
"Les appareils reçoivent temporairement une adresse de cette plage\npour que l'outil puisse les joindre. Elle doit se trouver dans le réseau de la carte choisie\net être assez grande pour tous les appareils.",
"Us dem Beriich überchömed d Grät vorübergehend e Adrässe,\ndamit s Tool sie erreicht. Er mues im Netz vo de gwählte Charte ligge\nund gross gnueg für alli Grät sii."
],
"Automatisch erkennen": [
"Detect automatically",
"Détecter automatiquement",
"Automatisch erchänne"
],
"Dell iDRAC": [
"Dell iDRAC",
"Dell iDRAC",
"Dell iDRAC"
],
"HPE iLO": [
"HPE iLO",
"HPE iLO",
"HPE iLO"
],
"Lenovo XCC": [
"Lenovo XCC",
"Lenovo XCC",
"Lenovo XCC"
],
"Netz- oder Broadcast-Adresse von {0}": [
"Network or broadcast address of {0}",
"Adresse de réseau ou de diffusion de {0}",
"Netz- oder Broadcast-Adrässe vo {0}"
],
"Gateway liegt nicht im Netz der neuen IP ({0})": [
"Gateway is not in the network of the new IP ({0})",
"La passerelle n'est pas dans le réseau de la nouvelle IP ({0})",
"Gateway liit nöd im Netz vo de neue IP ({0})"
],
"keine Verbindung": [
"no connection",
"pas de connexion",
"kei Verbindig"
],
"Benutzer": [
"User",
"Utilisateur",
"Benutzer"
],
"  Benutzer {0} existierte schon, Passwort und Rolle gesetzt": [
"  User {0} already existed, password and role set",
"  L'utilisateur {0} existait déjà, mot de passe et rôle définis",
"  Benutzer {0} hät's scho gäh, Passwort und Rolle gsetzt"
],
"  Benutzer {0} ok": [
"  User {0} ok",
"  Utilisateur {0} ok",
"  Benutzer {0} ok"
],
"  Hostname ok": [
"  Hostname ok",
"  Nom d'hôte ok",
"  Hostname ok"
],
"Netzwerk": [
"Network",
"Réseau",
"Netzwerk"
],
"  Netzwerk gesetzt": [
"  Network set",
"  Réseau configuré",
"  Netzwerk gsetzt"
],
"  iLO-Neustart ausgelöst": [
"  iLO restart triggered",
"  Redémarrage de l'iLO lancé",
"  iLO-Neustart uusglöst"
],
"Neustart": [
"Restart",
"Redémarrage",
"Neustart"
],
"DHCP-Server gestoppt": [
"DHCP server stopped",
"Serveur DHCP arrêté",
"DHCP-Server gstoppt"
],
"Passwort für alle Server übernehmen": [
"Apply password to all servers",
"Appliquer le mot de passe à tous les serveurs",
"Passwort für alli Server übernäh"
],
"Trägt das Passwort von oben in die Spalte „Passwort“ aller Zeilen ein\nund überschreibt dort vorhandene Einträge.": [
"Enters the password from above into the “Password” column of all rows\nand overwrites existing entries there.",
"Inscrit le mot de passe ci-dessus dans la colonne « Mot de passe » de toutes les lignes\net y écrase les entrées existantes.",
"Treit s Passwort vo obe i d Spalte „Passwort“ vo allne Ziile ii\nund überschriibt det was scho staht."
],
"Aktueller Login (Auslieferungszustand)": [
"Current login (factory state)",
"Connexion actuelle (état de livraison)",
"Aktuells Login (Uuslieferigszuestand)"
],
"Mit diesen Daten meldet sich das Tool an den Geräten an, um sie umzustellen.": [
"The tool uses these credentials to log in to the devices and reconfigure them.",
"L'outil utilise ces données pour se connecter aux appareils et les reconfigurer.",
"Mit dene Date mäldet sich s Tool a de Grät aa, zum sie umstelle."
],
"Hersteller": [
"Vendor",
"Constructeur",
"Hersteller"
],
"„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller, gemischte Listen sind möglich.\nEine feste Wahl überspringt die Erkennung und gilt für alle Zeilen. Nützlich, wenn die\nErkennung danebenliegt oder der Trockenlauf den richtigen Ablauf zeigen soll.": [
"“Detect automatically” asks each device for its vendor; mixed lists are possible.\nA fixed choice skips detection and applies to all rows. Useful if detection\ngets it wrong or the dry run should show the right sequence.",
"« Détecter automatiquement » demande son constructeur à chaque appareil ; les listes mixtes sont possibles.\nUn choix fixe saute la détection et vaut pour toutes les lignes. Utile si la détection\nse trompe ou si la simulation doit montrer le bon déroulement.",
"„Automatisch erchänne“ fröget jedes Grät nach sim Hersteller, gmischti Lischte gönd.\nE feschti Wahl überspringt d Erchännig und gilt für alli Ziile. Praktisch, wenn d\nErchännig danäbe liit oder de Trocheluuf de richtig Ablauf söll zeige."
],
"Benutzer, mit dem sich das Tool jetzt am BMC anmeldet, also der Werksbenutzer.\nLeer lassen: Dann wird je Hersteller root, Administrator oder ADMIN verwendet.": [
"User the tool logs in with at the BMC right now, i.e. the factory user.\nLeave empty: root, Administrator, ADMIN or USERID is used depending on the vendor.",
"Utilisateur avec lequel l'outil se connecte maintenant au BMC, donc l'utilisateur d'usine.\nLaisser vide : root, Administrator, ADMIN ou USERID est utilisé selon le constructeur.",
"Benutzer, mit dem sich s Tool jetzt am BMC aamäldet, also de Werksbenutzer.\nLäär laa: Denn wird je nach Hersteller root, Administrator, ADMIN oder USERID gnoh."
],
"Passwort des Werksbenutzers. Es gilt für alle Zeilen, deren Spalte „Passwort“ leer ist,\nund wird bei neuen Zeilen vorbelegt. Hat jedes Gerät ein eigenes Passwort\n(HPE, Supermicro, Lenovo), dieses in der Tabelle je Zeile eintragen.": [
"Password of the factory user. It applies to all rows whose “Password” column is empty\nand is pre-filled in new rows. If every device has its own password\n(HPE, Supermicro, Lenovo), enter it per row in the table.",
"Mot de passe de l'utilisateur d'usine. Il vaut pour toutes les lignes dont la colonne « Mot de passe » est vide\net est prérempli dans les nouvelles lignes. Si chaque appareil a son propre mot de passe\n(HPE, Supermicro, Lenovo), le saisir ligne par ligne dans le tableau.",
"Passwort vom Werksbenutzer. Es gilt für alli Ziile, wo d Spalte „Passwort“ läär isch,\nund wird bi neue Ziile vorbeleit. Hät jedes Grät es eigets Passwort\n(HPE, Supermicro, Lenovo), das i de Tabälle pro Ziile iiträge."
],
"Neuer Administrator": [
"New administrator",
"Nouvel administrateur",
"Neue Administrator"
],
"Dieses Konto wird auf jedem Gerät der Liste eingerichtet.": [
"This account is set up on every device in the list.",
"Ce compte est créé sur chaque appareil de la liste.",
"Das Konto wird uf jedem Grät vo de Lischte iigrichtet."
],
"Name des Administrator-Kontos, das auf allen Geräten angelegt wird.\nExistiert der Name bei HPE, Supermicro oder Lenovo schon, werden nur Passwort und Rolle gesetzt.": [
"Name of the administrator account created on all devices.\nIf the name already exists on HPE, Supermicro or Lenovo, only password and role are set.",
"Nom du compte administrateur créé sur tous les appareils.\nSi le nom existe déjà chez HPE, Supermicro ou Lenovo, seuls le mot de passe et le rôle sont définis.",
"Name vom Administrator-Konto, wo uf allne Grät aagleit wird.\nGit's de Name bi HPE, Supermicro oder Lenovo scho, wärded nur Passwort und Rolle gsetzt."
],
"Passwort des neuen Administrators, auf allen Geräten gleich.\nDie Geräte prüfen ihre eigenen Regeln, iLO verlangt z. B. mindestens 8 Zeichen.\nVor dem scharfen Lauf über das Auge kontrollieren.": [
"Password of the new administrator, the same on all devices.\nThe devices check their own rules; iLO, for example, requires at least 8 characters.\nCheck it with the eye button before a live run.",
"Mot de passe du nouvel administrateur, identique sur tous les appareils.\nLes appareils appliquent leurs propres règles ; iLO exige p. ex. au moins 8 caractères.\nLe vérifier avec l'œil avant une exécution réelle.",
"Passwort vom neue Administrator, uf allne Grät gliich.\nD Grät prüefed ihri eigete Regle, iLO verlangt z. B. mindeschtens 8 Zeiche.\nVor em scharfe Luuf über s Aug kontrolliere."
],
"User-Slot (nur Dell)": [
"User slot (Dell only)",
"Emplacement utilisateur (Dell uniquement)",
"User-Slot (nur Dell)"
],
"Nur Dell: Der iDRAC hat 16 feste Benutzerplätze, Platz 2 ist root.\nDer neue Administrator wird in diesen Platz geschrieben. Ist der Platz schon\nvon einem anderen Benutzer belegt, wird nichts überschrieben. Standard ist Platz 3.": [
"Dell only: the iDRAC has 16 fixed user slots, slot 2 is root.\nThe new administrator is written into this slot. If the slot is already\nused by another user, nothing is overwritten. Default is slot 3.",
"Dell uniquement : l'iDRAC a 16 emplacements utilisateur fixes, le 2 est root.\nLe nouvel administrateur est écrit dans cet emplacement. S'il est déjà\noccupé par un autre utilisateur, rien n'est écrasé. Par défaut : emplacement 3.",
"Nur Dell: De iDRAC hät 16 feschti Benutzerplätz, Platz 2 isch root.\nDe neu Administrator wird i dä Platz gschribe. Isch de Platz scho\nvo eme andere Benutzer bsetzt, wird nüt überschribe. Standard isch Platz 3."
],
"z. B. 255.255.255.0 oder /24": [
"e.g. 255.255.255.0 or /24",
"p. ex. 255.255.255.0 ou /24",
"z. B. 255.255.255.0 oder /24"
],
"Vorgaben für neue Zeilen": [
"Defaults for new rows",
"Valeurs par défaut des nouvelles lignes",
"Vorgabe für neui Ziile"
],
"Wird bei jeder neuen Zeile der Serverliste vorbelegt, auch bei Geräten aus der DHCP-Liste.\nLeer lassen: Dann wird der Wert der vorherigen Zeile übernommen.": [
"Pre-filled in every new row of the server list, also for devices from the DHCP list.\nLeave empty: the value of the previous row is used.",
"Prérempli dans chaque nouvelle ligne de la liste, y compris pour les appareils de la liste DHCP.\nLaisser vide : la valeur de la ligne précédente est reprise.",
"Wird bi jedere neue Ziile vo de Serverlischte vorbeleit, au bi Grät us de DHCP-Lischte.\nLäär laa: Denn wird de Wärt vo de vorherige Ziile übernoh."
],
"Wird bei jeder neuen Zeile der Serverliste vorbelegt.\nLeer lassen: Dann wird der Wert der vorherigen Zeile übernommen.": [
"Pre-filled in every new row of the server list.\nLeave empty: the value of the previous row is used.",
"Prérempli dans chaque nouvelle ligne de la liste.\nLaisser vide : la valeur de la ligne précédente est reprise.",
"Wird bi jedere neue Ziile vo de Serverlischte vorbeleit.\nLäär laa: Denn wird de Wärt vo de vorherige Ziile übernoh."
],
"+ Server": [
"+ Server",
"+ Serveur",
"+ Server"
],
"Neue Zeile. Passwort, Subnetzmaske und Gateway werden vorbelegt.": [
"New row. Password, subnet mask and gateway are pre-filled.",
"Nouvelle ligne. Mot de passe, masque et passerelle sont préremplis.",
"Neui Ziile. Passwort, Subnetzmaske und Gateway wärded vorbeleit."
],
"– Entfernen": [
"– Remove",
"– Retirer",
"– Ewägnäh"
],
"Entfernt die markierten Zeilen.": [
"Removes the selected rows.",
"Retire les lignes sélectionnées.",
"Nimmt di markierte Ziile ewäg."
],
"Erneut bearbeiten": [
"Process again",
"Traiter à nouveau",
"Nomal bearbeite"
],
"Nimmt bei den markierten Zeilen die grüne Fertig-Markierung zurück,\ndamit sie beim nächsten Start wieder bearbeitet werden.": [
"Removes the green “done” mark from the selected rows\nso they are processed again at the next start.",
"Retire la marque verte « terminé » des lignes sélectionnées\npour qu'elles soient retraitées au prochain démarrage.",
"Nimmt bi de markierte Ziile di grüen Fertig-Markierig zrugg,\ndamit sie bim nächschte Start wider bearbeitet wärded."
],
"Liste laden …": [
"Load list …",
"Charger la liste …",
"Lischte lade …"
],
"Lädt eine gespeicherte Liste oder eine CSV-Datei, z. B. aus Excel:\naktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname": [
"Loads a saved list or a CSV file, e.g. from Excel:\ncurrent_ip,password,new_ip,subnet_mask,gateway,hostname",
"Charge une liste enregistrée ou un fichier CSV, p. ex. depuis Excel :\nip_actuelle,mot_de_passe,nouvelle_ip,masque,passerelle,nom_hote",
"Ladt e gspeichereti Lischte oder e CSV-Datei, z. B. us em Excel:\naktuelli_ip,passwort,neui_ip,subnetzmaske,gateway,hostname"
],
"Liste speichern …": [
"Save list …",
"Enregistrer la liste …",
"Lischte speichere …"
],
"Speichert die Liste in eine Datei. Das Tool speichert nie von selbst.\nBeim Speichern wird gefragt, ob die Passwörter mit in die Datei sollen.": [
"Saves the list to a file. The tool never saves on its own.\nWhen saving you are asked whether the passwords should go into the file.",
"Enregistre la liste dans un fichier. L'outil n'enregistre jamais de lui-même.\nÀ l'enregistrement, il demande si les mots de passe doivent figurer dans le fichier.",
"Speicheret d Lischte i e Datei. S Tool speicheret nie vo sälber.\nBim Speichere wird gfröget, öb d Passwörter mit i d Datei sölled."
],
"Serverliste": [
"Server list",
"Liste des serveurs",
"Serverlischte"
],
"Trockenlauf (nichts ändern)": [
"Dry run (change nothing)",
"Simulation (ne rien modifier)",
"Trocheluuf (nüt ändere)"
],
"Mit Haken wird nichts geändert: Das Protokoll zeigt nur, welche Aufrufe\nan welches Gerät gingen. Ohne Haken werden die Geräte wirklich umgestellt.": [
"When ticked, nothing is changed: the log only shows which calls\nwould go to which device. Unticked, the devices are really reconfigured.",
"Coché, rien n'est modifié : le journal montre seulement quels appels\niraient à quel appareil. Décoché, les appareils sont réellement reconfigurés.",
"Mit Häägli wird nüt gänderet: S Protokoll zeiget nur, weli Uufrüef\na weles Grät giengted. Ohni Häägli wärded d Grät würkli umgstellt."
],
"Start": [
"Start",
"Démarrer",
"Start"
],
"Prüft die Eingaben und bearbeitet dann alle noch offenen Zeilen der Reihe nach:\nBenutzer anlegen, Hostname setzen, feste Adresse setzen.\nFertige (grüne) Zeilen werden übersprungen. Vor einem scharfen Lauf\nwird nachgefragt.": [
"Checks the input and then processes all open rows one after the other:\ncreate user, set hostname, set static address.\nDone (green) rows are skipped. Before a live run\nyou are asked to confirm.",
"Vérifie la saisie puis traite toutes les lignes encore ouvertes, l'une après l'autre :\ncréer l'utilisateur, définir le nom d'hôte, définir l'adresse fixe.\nLes lignes terminées (vertes) sont ignorées. Avant une exécution réelle,\nune confirmation est demandée.",
"Prüeft d Iigabe und bearbeitet denn alli no offene Ziile de Reihe naa:\nBenutzer aalegge, Hostname setze, feschti Adrässe setze.\nFertigi (grüeni) Ziile wärded überspronge. Vor eme scharfe Luuf\nwird nagfröget."
],
"Abbrechen": [
"Cancel",
"Annuler",
"Abbräche"
],
"Bricht nach dem Server ab, der gerade bearbeitet wird": [
"Stops after the server currently being processed",
"S'arrête après le serveur en cours de traitement",
"Bricht nach em Server ab, wo grad bearbeitet wird"
],
"Bereit": [
"Ready",
"Prêt",
"Parat. De Steward wartet uf Arbet."
],
"1 · DHCP-Server": [
"1 · DHCP server",
"1 · Serveur DHCP",
"1 · DHCP-Server"
],
"2 · Server konfigurieren": [
"2 · Configure servers",
"2 · Configurer les serveurs",
"2 · Server konfiguriere"
],
"3 · UEFI-Einstellungen": [
"3 · UEFI settings",
"3 · Réglages UEFI",
"3 · UEFI-Iistellige"
],
"4 · Hilfe": [
"4 · Help",
"4 · Aide",
"4 · Hilf"
],
"Ein Profil merkt sich die Einstellungen dieser Maske: Hersteller, Werksbenutzer, neuen\nAdministrator, User-Slot, Vorgaben für neue Zeilen und die DHCP-Einstellungen.\nPasswörter und die Serverliste gehören nicht dazu.": [
"A profile remembers the settings of this form: vendor, factory user, new\nadministrator, user slot, defaults for new rows and the DHCP settings.\nPasswords and the server list are not part of it.",
"Un profil mémorise les réglages de ce formulaire : constructeur, utilisateur d'usine, nouvel\nadministrateur, emplacement utilisateur, valeurs par défaut et réglages DHCP.\nLes mots de passe et la liste des serveurs n'en font pas partie.",
"Es Profil merkt sich d Iistellige vo dere Maske: Hersteller, Werksbenutzer, neue\nAdministrator, User-Slot, Vorgabe für neui Ziile und d DHCP-Iistellige.\nPasswörter und d Serverlischte ghöred nöd dezue."
],
"Profil": [
"Profile",
"Profil",
"Profil"
],
"Name des Profils:": [
"Profile name:",
"Nom du profil :",
"Name vom Profil:"
],
"Neues Profil": [
"New profile",
"Nouveau profil",
"Neus Profil"
],
"Profil umbenennen": [
"Rename profile",
"Renommer le profil",
"Profil umbenänne"
],
"Liste laden": [
"Load list",
"Charger la liste",
"Lischte lade"
],
"CSV (*.csv);;Alle Dateien (*)": [
"CSV (*.csv);;All files (*)",
"CSV (*.csv);;Tous les fichiers (*)",
"CSV (*.csv);;Alli Dateie (*)"
],
"Liste speichern": [
"Save list",
"Enregistrer la liste",
"Lischte speichere"
],
"CSV (*.csv)": [
"CSV (*.csv)",
"CSV (*.csv)",
"CSV (*.csv)"
],
"Sollen die Passwörter mit in die Datei?": [
"Should the passwords go into the file?",
"Faut-il inclure les mots de passe dans le fichier ?",
"Sölled d Passwörter mit i d Datei?"
],
"Ohne Passwörter enthält die Datei nur Adressen, Hostnamen und Einstellungen. Mit Passwörtern stehen diese im Klartext in der Datei.": [
"Without passwords the file only contains addresses, hostnames and settings. With passwords, they are stored in the file in plain text.",
"Sans mots de passe, le fichier ne contient que les adresses, noms d'hôte et réglages. Avec, ils y figurent en clair.",
"Ohni Passwörter hät d Datei nur Adrässe, Hostnäme und Iistellige dine. Mit Passwörter stönd die im Klartext i de Datei."
],
"Ohne Passwörter": [
"Without passwords",
"Sans mots de passe",
"Ohni Passwörter"
],
"Mit Passwörtern": [
"With passwords",
"Avec mots de passe",
"Mit Passwörter"
],
"Neuen Benutzer und Passwort ausfüllen.": [
"Fill in the new user and password.",
"Renseigner le nouvel utilisateur et le mot de passe.",
"Neue Benutzer und Passwort uusfülle."
],
"Neuer Benutzer: nur Buchstaben, Ziffern, Punkt, Bindestrich und Unterstrich, höchstens 16 Zeichen.": [
"New user: only letters, digits, dot, hyphen and underscore, at most 16 characters.",
"Nouvel utilisateur : uniquement lettres, chiffres, point, tiret et tiret bas, 16 caractères au maximum.",
"Neue Benutzer: nur Buechstabe, Ziffere, Punkt, Bindestrich und Understrich, höchschtens 16 Zeiche."
],
"Neues Passwort: mindestens 8 Zeichen. Kürzere lehnen die meisten BMCs ab.": [
"New password: at least 8 characters. Most BMCs reject shorter ones.",
"Nouveau mot de passe : au moins 8 caractères. La plupart des BMC refusent les plus courts.",
"Neus Passwort: mindeschtens 8 Zeiche. Chürzeri lehned di meischte BMCs ab."
],
"Die Serverliste ist leer.": [
"The server list is empty.",
"La liste des serveurs est vide.",
"D Serverlischte isch läär."
],
"Zeile {0}, {1}: {2}": [
"Row {0}, {1}: {2}",
"Ligne {0}, {1} : {2}",
"Ziile {0}, {1}: {2}"
],
"Adressen aus der Serverliste (Reiter 2) neu einlesen": [
"Re-read the addresses from the server list (tab 2)",
"Relire les adresses de la liste des serveurs (onglet 2)",
"Adrässe us de Serverlischte (Riiter 2) neu iiläse"
],
"Adresse des Geräts, von dem die Attribute gelesen werden.\nVorschläge kommen aus der Serverliste in Reiter 2, es geht aber jede\nerreichbare Adresse.": [
"Address of the device the attributes are read from.\nSuggestions come from the server list in tab 2, but any\nreachable address works.",
"Adresse de l'appareil dont les attributs sont lus.\nLes suggestions viennent de la liste de l'onglet 2, mais toute\nadresse joignable convient.",
"Adrässe vom Grät, vo dem d Attribut gläse wärded.\nVorschläg chömed us de Serverlischte im Riiter 2, es gaht aber jedi\nerreichbari Adrässe."
],
"Attribute lesen": [
"Read attributes",
"Lire les attributs",
"Attribut läse"
],
"Liest die tatsächlich vorhandenen UEFI-Attribute von diesem einen\nGerät. Nur Lesen, es wird nichts verändert.": [
"Reads the UEFI attributes that actually exist on this one\ndevice. Read only, nothing is changed.",
"Lit les attributs UEFI réellement présents sur cet\nappareil. Lecture seule, rien n'est modifié.",
"Liest d UEFI-Attribut, wo's uf dem einte Grät würkli git.\nNur läse, es wird nüt gänderet."
],
"Referenzgerät (nur zum Lesen)": [
"Reference device (read only)",
"Appareil de référence (lecture seule)",
"Referänzgrät (nur zum Läse)"
],
"Da sich UEFI-Attribute je Modell unterscheiden, zeigt das Tool nur die\ntatsächlich vorhandenen eines echten Geräts an, statt sie zu erraten.": [
"Because UEFI attributes differ per model, the tool only shows those\nthat really exist on a real device instead of guessing them.",
"Comme les attributs UEFI diffèrent selon le modèle, l'outil n'affiche que ceux\nréellement présents sur un vrai appareil, au lieu de les deviner.",
"Will sich UEFI-Attribut je nach Modäll underscheided, zeiget s Tool nur die,\nwo's uf eme ächte Grät würkli git, statt sie z errate."
],
"Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den\nHersteller dieses einen Geräts selbst.": [
"A fixed choice skips detection. “Detect automatically” determines the\nvendor of this one device itself.",
"Un choix fixe saute la détection. « Détecter automatiquement » détermine\nle constructeur de cet appareil.",
"E feschti Wahl überspringt d Erchännig. „Automatisch erchänne“ findet de\nHersteller vo dem einte Grät sälber use."
],
"Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet\n(root/Administrator/ADMIN/USERID).": [
"Leave empty: the factory user of the vendor is used\n(root/Administrator/ADMIN/USERID).",
"Laisser vide : l'utilisateur d'usine du constructeur est utilisé\n(root/Administrator/ADMIN/USERID).",
"Läär laa: Denn wird je nach Hersteller de Werksbenutzer gnoh\n(root/Administrator/ADMIN/USERID)."
],
"Gilt zum Lesen des Referenzgeräts und, wenn die Vorlage später angewendet wird,\nfür alle Zeilen der Serverliste ohne eigenes Passwort.": [
"Used to read the reference device and, when the template is applied later,\nfor all rows of the server list without a password of their own.",
"Sert à lire l'appareil de référence et, quand le modèle est appliqué ensuite,\npour toutes les lignes de la liste sans mot de passe propre.",
"Gilt zum Läse vom Referänzgrät und, wenn d Vorlag spöter aagwändet wird,\nfür alli Ziile vo de Serverlischte ohni eigets Passwort."
],
"Adresse": [
"Address",
"Adresse",
"Adrässe"
],
"Attribut suchen …": [
"Search attribute …",
"Rechercher un attribut …",
"Attribut sueche …"
],
"Blendet Zeilen aus, deren Name den Suchtext nicht enthält.": [
"Hides rows whose name does not contain the search text.",
"Masque les lignes dont le nom ne contient pas le texte recherché.",
"Bländet Ziile uus, wo de Name de Suechtext nöd dine hät."
],
"Nur die Spalte „Neuer Wert“ ist bearbeitbar. Leer lassen, um ein\nAttribut unverändert zu lassen. Graue Zeilen (Passwörter, TPM,\nLöschfunktionen) sind gesperrt.": [
"Only the “New value” column can be edited. Leave empty to keep an\nattribute unchanged. Grey rows (passwords, TPM,\nerase functions) are locked.",
"Seule la colonne « Nouvelle valeur » est modifiable. Laisser vide pour ne pas\nchanger un attribut. Les lignes grises (mots de passe, TPM,\nfonctions d'effacement) sont verrouillées.",
"Nur d Spalte „Neue Wärt“ cha me bearbeite. Läär laa, zum es\nAttribut nöd z ändere. Graui Ziile (Passwörter, TPM,\nLöschfunktione) sind gsperrt."
],
"UEFI/BIOS-Attribute": [
"UEFI/BIOS attributes",
"Attributs UEFI/BIOS",
"UEFI/BIOS-Attribut"
],
"Mit Haken wird nichts gesendet, das Protokoll zeigt nur den Ablauf.\nOhne Haken werden die ausgefüllten „Neuer Wert“-Felder auf jedem Server\nder Liste (Reiter 2) gesetzt, der das jeweilige Attribut tatsächlich hat.": [
"When ticked, nothing is sent; the log only shows the sequence.\nUnticked, the filled-in “New value” fields are set on every server\nof the list (tab 2) that actually has the attribute.",
"Coché, rien n'est envoyé ; le journal montre seulement le déroulement.\nDécoché, les champs « Nouvelle valeur » remplis sont appliqués sur chaque serveur\nde la liste (onglet 2) qui possède réellement l'attribut.",
"Mit Häägli wird nüt gschickt, s Protokoll zeiget nur de Ablauf.\nOhni Häägli wärded di uusgfüllte „Neue Wärt“-Fälder uf jedem Server\nvo de Lischte (Riiter 2) gsetzt, wo das Attribut würkli hät."
],
"Danach hart neu starten": [
"Then hard restart",
"Puis redémarrage forcé",
"Dänn hert neu starte"
],
"UEFI-Einstellungen werden erst nach einem Neustart aktiv. Ohne Haken\nwerden sie nur vorgemerkt, es wird kein Neustart ausgelöst.\nMit Haken wird ein laufender Server hart neu gestartet (wie mit der\nReset-Taste), ein ausgeschalteter wird eingeschaltet. Nur für Server\nohne laufendes Betriebssystem verwenden.": [
"UEFI settings only take effect after a restart. Unticked,\nthey are only staged and no restart is triggered.\nTicked, a running server is hard restarted (like pressing the\nreset button) and a powered-off one is switched on. Use only for\nservers without a running operating system.",
"Les réglages UEFI ne prennent effet qu'après un redémarrage. Décoché,\nils sont seulement mis en attente, sans redémarrage.\nCoché, un serveur en marche est redémarré de force (comme avec le\nbouton Reset) et un serveur éteint est allumé. À n'utiliser que pour\ndes serveurs sans système d'exploitation en cours d'exécution.",
"UEFI-Iistellige wärded erscht nach eme Neustart aktiv. Ohni Häägli\nwärded sie nur vorgmerkt, es wird kein Neustart uusglöst.\nMit Häägli wird en laufende Server hert neu gstartet (wie mit de\nReset-Taschte), en abgstellte wird iigschaltet. Nur für Server\nohni laufends Betriebssystem bruuche."
],
"Auf allen Servern der Liste setzen": [
"Set on all servers in the list",
"Appliquer à tous les serveurs de la liste",
"Uf allne Server vo de Lischte setze"
],
"Wendet jedes ausgefüllte „Neuer Wert“-Feld auf jede Zeile der\nServerliste (Reiter 2) an, der Reihe nach, mit Login wie oben.": [
"Applies every filled-in “New value” field to every row of the\nserver list (tab 2), one after the other, with the login above.",
"Applique chaque champ « Nouvelle valeur » rempli à chaque ligne de la\nliste des serveurs (onglet 2), l'une après l'autre, avec la connexion ci-dessus.",
"Wändet jedes uusgfüllte „Neue Wärt“-Fäld uf jedi Ziile vo de\nServerlischte (Riiter 2) aa, de Reihe naa, mit em Login wie obe."
],
"Lese …": [
"Reading …",
"Lecture …",
"Am Läse …"
],
"{0} Attribute ({1})": [
"{0} attributes ({1})",
"{0} attributs ({1})",
"{0} Attribut ({1})"
],
"Eine Vorlage merkt sich Hersteller und die ausgefüllten „Neuer Wert“-Felder (Attribut ->\nWert). Eigener Bereich, getrennt von den Profilen in Reiter 2 und ohne Passwörter.": [
"A template remembers the vendor and the filled-in “New value” fields (attribute ->\nvalue). A separate area, apart from the profiles in tab 2 and without passwords.",
"Un modèle mémorise le constructeur et les champs « Nouvelle valeur » remplis (attribut ->\nvaleur). Domaine à part, séparé des profils de l'onglet 2 et sans mots de passe.",
"E Vorlag merkt sich de Hersteller und di uusgfüllte „Neue Wärt“-Fälder (Attribut ->\nWärt). Eigene Beriich, trännt vo de Profil im Riiter 2 und ohni Passwörter."
],
"UEFI-Vorlage": [
"UEFI template",
"Modèle UEFI",
"UEFI-Vorlag"
],
"Neue UEFI-Vorlage": [
"New UEFI template",
"Nouveau modèle UEFI",
"Neui UEFI-Vorlag"
],
"UEFI-Vorlage umbenennen": [
"Rename UEFI template",
"Renommer le modèle UEFI",
"UEFI-Vorlag umbenänne"
],
"Trockenlauf": [
"Dry run",
"Simulation",
"Trocheluuf"
],
"Scharfer Lauf": [
"Live run",
"Exécution réelle",
"Scharfe Luuf"
],
"— {0}, {1} Server —": [
"— {0}, {1} servers —",
"— {0}, {1} serveurs —",
"— {0}, {1} Server —"
],
"Läuft …": [
"Running …",
"En cours …",
"De Steward isch dra …"
],
"Netzwerkkarten neu einlesen": [
"Re-read network cards",
"Relire les cartes réseau",
"Netzwerkcharte neu iiläse"
],
"Netzwerkkarte dieses Rechners, die am Staging-Switch hängt.\nNur auf dieser Karte werden Adressen vergeben. Sie braucht eine feste IP.": [
"Network card of this computer that is connected to the staging switch.\nAddresses are only handed out on this card. It needs a static IP.",
"Carte réseau de cet ordinateur reliée au switch de staging.\nLes adresses ne sont attribuées que sur cette carte. Elle doit avoir une IP fixe.",
"Netzwerkcharte vo dem Rächner, wo am Staging-Switch hanget.\nNur uf dere Charte wärded Adrässe vergäh. Sie bruucht e feschti IP."
],
"DHCP starten": [
"Start DHCP",
"Démarrer DHCP",
"DHCP starte"
],
"Startet bzw. stoppt die Adressvergabe auf der gewählten Karte.\nNur an einem getrennten Staging-Switch verwenden.": [
"Starts or stops handing out addresses on the selected card.\nUse only on a separate staging switch.",
"Démarre ou arrête l'attribution d'adresses sur la carte choisie.\nÀ n'utiliser que sur un switch de staging séparé.",
"Startet bzw. stoppt d Adrässvergab uf de gwählte Charte.\nNur a eme trännte Staging-Switch bruuche."
],
"Gestoppt": [
"Stopped",
"Arrêté",
"Gstoppt"
],
"DHCP-Server für das Staging-Netz": [
"DHCP server for the staging network",
"Serveur DHCP pour le réseau de staging",
"DHCP-Server fürs Staging-Netz"
],
"Netzwerkkarte": [
"Network card",
"Carte réseau",
"Netzwerkcharte"
],
"Adressbereich": [
"Address range",
"Plage d'adresses",
"Adrässberiich"
],
"Geräte, die eine Adresse bekommen haben. Der gemeldete Name hilft bei der Zuordnung,\nz. B. „idrac-“ plus Service-Tag oder „ILO“ plus Seriennummer.": [
"Devices that received an address. The reported name helps to identify them,\ne.g. “idrac-” plus service tag or “ILO” plus serial number.",
"Appareils ayant reçu une adresse. Le nom annoncé aide à les identifier,\np. ex. « idrac- » plus Service Tag ou « ILO » plus numéro de série.",
"Grät, wo e Adrässe übercho händ. De gmäldet Name hilft bi de Zueordnig,\nz. B. „idrac-“ plus Service-Tag oder „ILO“ plus Serienummere."
],
"In Serverliste übernehmen": [
"Add to server list",
"Reprendre dans la liste des serveurs",
"I d Serverlischte übernäh"
],
"Trägt die vergebenen Adressen als „Aktuelle IP“ in die Serverliste ein.\nMit Markierung nur die markierten Geräte, sonst alle.": [
"Enters the assigned addresses as “Current IP” in the server list.\nWith a selection only the selected devices, otherwise all.",
"Inscrit les adresses attribuées comme « IP actuelle » dans la liste des serveurs.\nAvec une sélection, seulement les appareils sélectionnés, sinon tous.",
"Treit di vergäbne Adrässe als „Aktuelli IP“ i d Serverlischte ii.\nMit Markierig nur di markierte Grät, susch alli."
],
"0 Geräte": [
"0 devices",
"0 appareil",
"0 Grät"
],
"Gefundene Geräte": [
"Devices found",
"Appareils trouvés",
"Gfundeni Grät"
],
"Ende liegt vor dem Anfang des Bereichs": [
"End is before the start of the range",
"La fin précède le début de la plage",
"S Änd liit vor em Aafang vom Beriich"
],
"Keine Netzwerkkarte gefunden.": [
"No network card found.",
"Aucune carte réseau trouvée.",
"Kei Netzwerkcharte gfunde."
],
"Die Karte hat eine öffentliche Adresse ({0}). Ein Staging-Netz nutzt private Adressen, deshalb wird hier kein DHCP-Server gestartet.": [
"The card has a public address ({0}). A staging network uses private addresses, so no DHCP server is started here.",
"La carte a une adresse publique ({0}). Un réseau de staging utilise des adresses privées ; aucun serveur DHCP n'est donc démarré ici.",
"D Charte hät e öffentlichi Adrässe ({0}). Es Staging-Netz bruucht privati Adrässe, drum wird da kein DHCP-Server gstartet."
],
"Kein anderer DHCP-Server hat innerhalb von 1,5 Sekunden geantwortet.": [
"No other DHCP server answered within 1.5 seconds.",
"Aucun autre serveur DHCP n'a répondu en 1,5 seconde.",
"Kein andere DHCP-Server hät innert 1,5 Sekunde Antwort gäh."
],
"Ob dort schon ein DHCP-Server läuft, ließ sich nicht prüfen (Port 68 braucht Root-Rechte).": [
"Whether a DHCP server is already running there could not be checked (port 68 needs root rights).",
"Impossible de vérifier si un serveur DHCP y fonctionne déjà (le port 68 exige les droits root).",
"Öb det scho en DHCP-Server lauft, hät sich nöd la prüefe (Port 68 bruucht Root-Rächt)."
],
"Ein zweiter DHCP-Server in einem Produktivnetz stört dort alle Geräte.\n\nHängt diese Karte wirklich an einem getrennten Staging-Switch?": [
"A second DHCP server in a production network disrupts all devices there.\n\nIs this card really connected to a separate staging switch?",
"Un second serveur DHCP dans un réseau de production y perturbe tous les appareils.\n\nCette carte est-elle vraiment reliée à un switch de staging séparé ?",
"En zweite DHCP-Server im Produktivnetz stört det alli Grät.\n\nHanget die Charte würkli a eme trännte Staging-Switch?"
],
"DHCP stoppen": [
"Stop DHCP",
"Arrêter DHCP",
"DHCP stoppe"
],
"<b style='color:#3fa45b'>Läuft</b> auf {0}, {1} bis {2}": [
"<b style='color:#3fa45b'>Running</b> on {0}, {1} to {2}",
"<b style='color:#3fa45b'>Actif</b> sur {0}, {1} à {2}",
"<b style='color:#3fa45b'>Lauft</b> uf {0}, {1} bis {2}"
],
"Anmeldung abgebrochen": [
"Authentication cancelled",
"Authentification annulée",
"Aamäldig abbroche"
],
"Start nicht möglich oder Anmeldung fehlgeschlagen": [
"Could not start, or authentication failed",
"Démarrage impossible ou échec de l'authentification",
"Start nöd möglich oder Aamäldig fählgschlage"
],
"Port 67 belegt oder Karte nicht bereit, siehe Protokoll": [
"Port 67 in use or card not ready, see log",
"Port 67 occupé ou carte non prête, voir le journal",
"Port 67 bsetzt oder Charte nöd parat, lueg im Protokoll"
],
"{0} Geräte": [
"{0} devices",
"{0} appareils",
"{0} Grät"
],
"{0} Geräte übernommen: jetzt neue IP und Hostname eintragen": [
"{0} devices added: now enter new IP and hostname",
"{0} appareils repris : saisir maintenant la nouvelle IP et le nom d'hôte",
"{0} Grät übernoh: jetzt neui IP und Hostname iiträge"
],
"Ja": [
"Yes",
"Oui",
"Ja"
],
"Zur Bestätigung die Anzahl der betroffenen Server eintippen:": [
"To confirm, type the number of servers affected:",
"Pour confirmer, saisir le nombre de serveurs concernés :",
"Zum Bestätige d Aazahl vo de betroffene Server iitippe:"
],
"  FEHLER: {0} (HTTP {1})": [
"  ERROR: {0} (HTTP {1})",
"  ERREUR : {0} (HTTP {1})",
"  FÄHLER: {0} (HTTP {1})"
],
"Benutzer (vorhandenes Konto)": [
"User (existing account)",
"Utilisateur (compte existant)",
"Benutzer (Konto git's scho)"
],
"Login/Erreichbarkeit": [
"Login/reachability",
"Connexion/accessibilité",
"Login/Erreichbarkeit"
],
"User-Slot lesen": [
"Read user slot",
"Lecture de l'emplacement utilisateur",
"User-Slot läse"
],
"  FEHLER: User-Slot {0} ist schon belegt (Benutzer {1}). Nichts überschrieben, bitte einen freien Slot wählen.": [
"  ERROR: user slot {0} is already in use (user {1}). Nothing overwritten, please choose a free slot.",
"  ERREUR : l'emplacement utilisateur {0} est déjà occupé (utilisateur {1}). Rien n'a été écrasé, choisir un emplacement libre.",
"  FÄHLER: User-Slot {0} isch scho bsetzt (Benutzer {1}). Nüt überschribe, bitte en freie Slot wähle."
],
"DHCP abschalten": [
"Disable DHCP",
"Désactivation du DHCP",
"DHCP abschalte"
],
"Statische IP": [
"Static IP",
"IP fixe",
"Statischi IP"
],
"iLO-Neustart": [
"iLO restart",
"Redémarrage de l'iLO",
"iLO-Neustart"
],
"  Hersteller: {0}, Anmeldung als {1}": [
"  Vendor: {0}, logging in as {1}",
"  Constructeur : {0}, connexion en tant que {1}",
"  Hersteller: {0}, Aamäldig als {1}"
],
"Kontrolle der neuen Adressen (bis zu {0}s)...": [
"Checking the new addresses (up to {0}s)...",
"Contrôle des nouvelles adresses (jusqu'à {0} s)...",
"Kontrolle vo de neue Adrässe (bis zu {0}s)..."
],
"  Trockenlauf: Attribute nicht wirklich gelesen, zeige den Ablauf ungeprüft": [
"  Dry run: attributes not actually read, showing the sequence unchecked",
"  Simulation : attributs non lus réellement, déroulement affiché sans vérification",
"  Trocheluuf: Attribut nöd würkli gläse, zeige de Ablauf unprüeft"
],
"  nichts zu ändern, kein Neustart": [
"  nothing to change, no restart",
"  rien à modifier, pas de redémarrage",
"  nüt z ändere, kein Neustart"
],
"BIOS-Attribute setzen": [
"Set BIOS attributes",
"Écriture des attributs BIOS",
"BIOS-Attribut setze"
],
"  gesetzt: ": [
"  set: ",
"  défini : ",
"  gsetzt: "
],
"  Hinweis: wird erst beim nächsten Neustart übernommen (kein Neustart ausgelöst)": [
"  Note: takes effect at the next restart (no restart triggered)",
"  Remarque : pris en compte au prochain redémarrage (aucun redémarrage lancé)",
"  Hiiwiis: wird erscht bim nächschte Neustart übernoh (kein Neustart uusglöst)"
],
"  Hinweis: Betriebszustand unbekannt, kein Neustart ausgelöst. Bitte von Hand neu starten.": [
"  Note: power state unknown, no restart triggered. Please restart by hand.",
"  Remarque : état d'alimentation inconnu, aucun redémarrage lancé. Redémarrer à la main.",
"  Hiiwiis: Betriebszuestand unbekannt, kein Neustart uusglöst. Bitte vo Hand neu starte."
],
"  Neustart ausgelöst ({0}); die Attribute greifen danach": [
"  Restart triggered ({0}); the attributes apply afterwards",
"  Redémarrage lancé ({0}) ; les attributs s'appliquent ensuite",
"  Neustart uusglöst ({0}); d Attribut griifed dänn"
],
"  Hersteller: {0}": [
"  Vendor: {0}",
"  Constructeur : {0}",
"  Hersteller: {0}"
],
"Angebot   {0}  an {1} {2}": [
"Offer     {0}  to {1} {2}",
"Offre     {0}  à {1} {2}",
"Aagebot   {0}  a {1} {2}"
],
"Vergeben  {0}  an {1} {2}": [
"Assigned  {0}  to {1} {2}",
"Attribué  {0}  à {1} {2}",
"Vergäh    {0}  a {1} {2}"
],
"DHCP-Server läuft auf {0}, Bereich {1} bis {2}": [
"DHCP server running on {0}, range {1} to {2}",
"Serveur DHCP actif sur {0}, plage {1} à {2}",
"DHCP-Server lauft uf {0}, Beriich {1} bis {2}"
],
"Speichern": [
"Save",
"Enregistrer",
"Speichere"
],
"Speichert die aktuellen Einstellungen in das gewählte Profil.\nOhne gewähltes Profil wird ein neues angelegt.": [
"Saves the current settings to the selected profile.\nWithout a selected profile a new one is created.",
"Enregistre les réglages actuels dans le profil choisi.\nSans profil choisi, un nouveau est créé.",
"Speicheret di aktuelle Iistellige is gwählte Profil.\nOhni gwählts Profil wird es neus aagleit."
],
"Neu …": [
"New …",
"Nouveau …",
"Neu …"
],
"Legt aus den aktuellen Einstellungen ein neues Profil an.": [
"Creates a new profile from the current settings.",
"Crée un nouveau profil à partir des réglages actuels.",
"Leit us de aktuelle Iistellige es neus Profil aa."
],
"Umbenennen …": [
"Rename …",
"Renommer …",
"Umbenänne …"
],
"Gibt dem gewählten Profil einen anderen Namen.": [
"Gives the selected profile another name.",
"Donne un autre nom au profil choisi.",
"Git em gwählte Profil en andere Name."
],
"Löschen": [
"Delete",
"Supprimer",
"Lösche"
],
"Löscht das gewählte Profil. Die Einstellungen in der Maske bleiben stehen.": [
"Deletes the selected profile. The settings in the form stay as they are.",
"Supprime le profil choisi. Les réglages du formulaire restent en place.",
"Löscht s gwählte Profil. D Iistellige i de Maske bliibed stah."
],
"Profil „{0}“ geladen": [
"Profile “{0}” loaded",
"Profil « {0} » chargé",
"Profil „{0}“ glade"
],
"Profil „{0}“ gespeichert (ohne Passwörter)": [
"Profile “{0}” saved (without passwords)",
"Profil « {0} » enregistré (sans mots de passe)",
"Profil „{0}“ gspeicheret (ohni Passwörter)"
],
"Dieser Name ist reserviert.": [
"This name is reserved.",
"Ce nom est réservé.",
"Dä Name isch reserviert."
],
"Kein Profil gewählt": [
"No profile selected",
"Aucun profil choisi",
"Kei Profil gwählt"
],
"Das Profil „{0}“ gibt es schon.": [
"The profile “{0}” already exists.",
"Le profil « {0} » existe déjà.",
"S Profil „{0}“ git's scho."
],
"Profil „{0}“ heißt jetzt „{1}“": [
"Profile “{0}” is now called “{1}”",
"Le profil « {0} » s'appelle maintenant « {1} »",
"Profil „{0}“ heisst jetzt „{1}“"
],
"Profil löschen": [
"Delete profile",
"Supprimer le profil",
"Profil lösche"
],
"Profil „{0}“ wirklich löschen?": [
"Really delete profile “{0}”?",
"Vraiment supprimer le profil « {0} » ?",
"Profil „{0}“ würkli lösche?"
],
"Profil „{0}“ gelöscht": [
"Profile “{0}” deleted",
"Profil « {0} » supprimé",
"Profil „{0}“ glöscht"
],
"<small>Leer lassen für: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>": [
"<small>Leave empty for: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>",
"<small>Laisser vide pour : Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>",
"<small>Läär laa für: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>"
],
"<small>Leer lassen für den Werksbenutzer {0}</small>": [
"<small>Leave empty for the factory user {0}</small>",
"<small>Laisser vide pour l'utilisateur d'usine {0}</small>",
"<small>Läär laa für de Werksbenutzer {0}</small>"
],
"Es sind schon Einträge vorhanden.": [
"There are already entries.",
"Il y a déjà des entrées.",
"Es hät scho Iiträg i de Lischte."
],
"Ersetzen": [
"Replace",
"Remplacer",
"Ersetze"
],
"Anhängen": [
"Append",
"Ajouter",
"Aahänke"
],
"Geladen: {0}": [
"Loaded: {0}",
"Chargé : {0}",
"Glade: {0}"
],
"Gespeichert in {0}": [
"Saved to {0}",
"Enregistré dans {0}",
"Gspeicheret i {0}"
],
"Diese Adresse ist die aktuelle IP von Zeile {0}": [
"This address is the current IP of row {0}",
"Cette adresse est l'IP actuelle de la ligne {0}",
"Die Adrässe isch di aktuell IP vo Ziile {0}"
],
"fehlt": [
"missing",
"manquant",
"fählt"
],
"Zeile {0}: kein Passwort (weder in der Zeile noch oben).": [
"Row {0}: no password (neither in the row nor above).",
"Ligne {0} : pas de mot de passe (ni dans la ligne ni en haut).",
"Ziile {0}: kei Passwort (weder i de Ziile no obe)."
],
"Zeile {0}: Die neue IP ist eine Adresse dieses Rechners.": [
"Row {0}: the new IP is an address of this computer.",
"Ligne {0} : la nouvelle IP est une adresse de cet ordinateur.",
"Ziile {0}: Di neu IP isch e Adrässe vo dem Rächner."
],
"Zeile {0}: Die neue IP liegt im Bereich des laufenden DHCP-Servers ({1} bis {2}). Dort könnte sie ein zweites Mal vergeben werden.": [
"Row {0}: the new IP lies in the range of the running DHCP server ({1} to {2}). It could be handed out a second time there.",
"Ligne {0} : la nouvelle IP est dans la plage du serveur DHCP actif ({1} à {2}). Elle pourrait y être attribuée une seconde fois.",
"Ziile {0}: Di neu IP liit im Beriich vom laufende DHCP-Server ({1} bis {2}). Det chönnt sie es zweits Mal vergäh wärde."
],
"Lauf aktiv": [
"Run active",
"Exécution en cours",
"Luuf aktiv"
],
"Es läuft noch eine Konfiguration. Bitte warten oder erst abbrechen.": [
"A configuration is still running. Please wait or cancel it first.",
"Une configuration est encore en cours. Patienter ou l'annuler d'abord.",
"De Steward isch no am Schaffe. Bitte warte oder zerscht abbräche."
],
"Beenden": [
"Quit",
"Quitter",
"Beände"
],
"Die Serverliste wurde nicht gespeichert.": [
"The server list has not been saved.",
"La liste des serveurs n'a pas été enregistrée.",
"D Serverlischte isch nöd gspeicheret worde."
],
"Das Tool speichert nichts von selbst. Ohne Speichern ist die Liste nach dem Schließen weg.": [
"The tool saves nothing on its own. Without saving, the list is gone after closing.",
"L'outil n'enregistre rien de lui-même. Sans enregistrement, la liste est perdue à la fermeture.",
"S Tool speicheret nüt vo sälber. Ohni Speichere isch d Lischte nach em Zuemache futsch."
],
"Speichern …": [
"Save …",
"Enregistrer …",
"Speichere …"
],
"Verwerfen": [
"Discard",
"Abandonner",
"Verwärfe"
],
"Attribut": [
"Attribute",
"Attribut",
"Attribut"
],
"Aktueller Wert": [
"Current value",
"Valeur actuelle",
"Aktuelle Wärt"
],
"Neuer Wert": [
"New value",
"Nouvelle valeur",
"Neue Wärt"
],
"Bitte eine Adresse eintragen oder aus der Serverliste wählen.": [
"Please enter an address or pick one from the server list.",
"Saisir une adresse ou en choisir une dans la liste des serveurs.",
"Bitte e Adrässe iiträge oder us de Serverlischte wähle."
],
"Speichert die aktuellen „Neuer Wert“-Felder in die gewählte Vorlage.\nOhne gewählte Vorlage wird eine neue angelegt.": [
"Saves the current “New value” fields to the selected template.\nWithout a selected template a new one is created.",
"Enregistre les champs « Nouvelle valeur » actuels dans le modèle choisi.\nSans modèle choisi, un nouveau est créé.",
"Speicheret di aktuelle „Neue Wärt“-Fälder i di gwählti Vorlag.\nOhni gwählti Vorlag wird e neui aagleit."
],
"Legt aus den aktuellen Feldern eine neue Vorlage an.": [
"Creates a new template from the current fields.",
"Crée un nouveau modèle à partir des champs actuels.",
"Leit us de aktuelle Fälder e neui Vorlag aa."
],
"Gibt der gewählten Vorlage einen anderen Namen.": [
"Gives the selected template another name.",
"Donne un autre nom au modèle choisi.",
"Git de gwählte Vorlag en andere Name."
],
"Löscht die gewählte Vorlage.": [
"Deletes the selected template.",
"Supprime le modèle choisi.",
"Löscht di gwählti Vorlag."
],
"Vorlage „{0}“ geladen": [
"Template “{0}” loaded",
"Modèle « {0} » chargé",
"Vorlag „{0}“ glade"
],
"Vorlage „{0}“ gespeichert": [
"Template “{0}” saved",
"Modèle « {0} » enregistré",
"Vorlag „{0}“ gspeicheret"
],
"Keine Vorlage gewählt": [
"No template selected",
"Aucun modèle choisi",
"Kei Vorlag gwählt"
],
"Die Vorlage „{0}“ gibt es schon.": [
"The template “{0}” already exists.",
"Le modèle « {0} » existe déjà.",
"D Vorlag „{0}“ git's scho."
],
"Vorlage „{0}“ heißt jetzt „{1}“": [
"Template “{0}” is now called “{1}”",
"Le modèle « {0} » s'appelle maintenant « {1} »",
"Vorlag „{0}“ heisst jetzt „{1}“"
],
"UEFI-Vorlage löschen": [
"Delete UEFI template",
"Supprimer le modèle UEFI",
"UEFI-Vorlag lösche"
],
"Vorlage „{0}“ wirklich löschen?": [
"Really delete template “{0}”?",
"Vraiment supprimer le modèle « {0} » ?",
"Vorlag „{0}“ würkli lösche?"
],
"Vorlage „{0}“ gelöscht": [
"Template “{0}” deleted",
"Modèle « {0} » supprimé",
"Vorlag „{0}“ glöscht"
],
"UEFI-Einstellungen": [
"UEFI settings",
"Réglages UEFI",
"UEFI-Iistellige"
],
"Keine „Neuer Wert“-Felder ausgefüllt.": [
"No “New value” fields filled in.",
"Aucun champ « Nouvelle valeur » rempli.",
"Kei „Neue Wärt“-Fälder uusgfüllt. Ohni Uuftrag macht de Steward nüt."
],
"Die Serverliste (Reiter 2) ist leer.": [
"The server list (tab 2) is empty.",
"La liste des serveurs (onglet 2) est vide.",
"D Serverlischte (Riiter 2) isch läär."
],
"Falsche UEFI-Werte können dazu führen, dass ein Server nicht mehr startet.": [
"Wrong UEFI values can stop a server from booting.",
"De mauvaises valeurs UEFI peuvent empêcher un serveur de démarrer.",
"Falschi UEFI-Wärt chönd dezue füehre, dass en Server nüme startet."
],
"Wird nach dem aktuellen Server abgebrochen …": [
"Stopping after the current server …",
"Arrêt après le serveur en cours …",
"Nach em aktuelle Server isch Schluss …"
],
"Fertig ohne Fehler": [
"Finished without errors",
"Terminé sans erreur",
"Fertig, alles tipptopp."
],
"Beendet mit Fehlern (Code {0})": [
"Finished with errors (code {0})",
"Terminé avec des erreurs (code {0})",
"Fertig, aber nöd ganz suuber: Es hät Fähler gäh (Code {0})"
],
"bis": [
"to",
"à",
"bis"
],
"<small><b>Nur an einem getrennten Staging-Switch starten</b>, nie im Produktivnetz.</small>": [
"<small><b>Start only on a separate staging switch</b>, never in the production network.</small>",
"<small><b>Démarrer uniquement sur un switch de staging séparé</b>, jamais dans le réseau de production.</small>",
"<small><b>Nur a eme trännte Staging-Switch starte</b>, nie im Produktivnetz.</small>"
],
"Vergebene IP": [
"Assigned IP",
"IP attribuée",
"Vergäbni IP"
],
"MAC-Adresse": [
"MAC address",
"Adresse MAC",
"MAC-Adrässe"
],
"Gemeldeter Name": [
"Reported name",
"Nom annoncé",
"Gmäldete Name"
],
"Die Karte „{0}“ hat keine IPv4-Adresse.\n\nIn den Adaptereinstellungen (ncpa.cpl) bei dieser Karte unter „Internetprotokoll, Version 4“ eine feste Adresse eintragen, z. B. 192.168.0.1 mit Maske 255.255.255.0, Gateway leer lassen.\n\nDanach hier auf ↻ klicken.": [
"The card “{0}” has no IPv4 address.\n\nIn the adapter settings (ncpa.cpl), enter a static address for this card under “Internet Protocol Version 4”, e.g. 192.168.0.1 with mask 255.255.255.0, and leave the gateway empty.\n\nThen click ↻ here.",
"La carte « {0} » n'a pas d'adresse IPv4.\n\nDans les paramètres de la carte (ncpa.cpl), saisir une adresse fixe sous « Protocole Internet version 4 », p. ex. 192.168.0.1 avec le masque 255.255.255.0, et laisser la passerelle vide.\n\nCliquer ensuite ici sur ↻.",
"D Charte „{0}“ hät kei IPv4-Adrässe.\n\nI de Adapteriistellige (ncpa.cpl) bi dere Charte under „Internetprotokoll, Version 4“ e feschti Adrässe iiträge, z. B. 192.168.0.1 mit Maske 255.255.255.0, Gateway läär laa.\n\nDänn da uf ↻ klicke."
],
"DHCP-Server": [
"DHCP server",
"Serveur DHCP",
"DHCP-Server"
],
"Über diese Karte läuft die Standardroute dieses Rechners (Internet bzw. Firmennetz). Das ist kein getrenntes Staging-Netz, deshalb wird hier kein DHCP-Server gestartet.\n\nFür das Staging die Karte mit fester Adresse und ohne Gateway einrichten.": [
"This computer's default route (internet or company network) runs over this card. That is not a separate staging network, so no DHCP server is started here.\n\nFor staging, set the card up with a static address and without a gateway.",
"La route par défaut de cet ordinateur (Internet ou réseau d'entreprise) passe par cette carte. Ce n'est pas un réseau de staging séparé ; aucun serveur DHCP n'est donc démarré ici.\n\nPour le staging, configurer la carte avec une adresse fixe et sans passerelle.",
"Über die Charte lauft d Standardroute vo dem Rächner (Internet bzw. Firmenetz). Das isch kei trännts Staging-Netz, drum wird da kein DHCP-Server gstartet.\n\nFürs Staging d Charte mit feschter Adrässe und ohni Gateway iirichte."
],
"DHCP-Server nicht gestartet": [
"DHCP server not started",
"Serveur DHCP non démarré",
"DHCP-Server nöd gstartet"
],
"DHCP-Server starten?": [
"Start DHCP server?",
"Démarrer le serveur DHCP ?",
"DHCP-Server starte?"
],
"Auf {0} ({1}) werden Adressen von {2} bis {3} vergeben.": [
"On {0} ({1}), addresses from {2} to {3} will be handed out.",
"Sur {0} ({1}), des adresses de {2} à {3} seront attribuées.",
"Uf {0} ({1}) wärded Adrässe vo {2} bis {3} vergäh."
],
"Keine neuen Geräte zum Übernehmen": [
"No new devices to add",
"Aucun nouvel appareil à reprendre",
"Kei neui Grät ume"
],
"Eingabe prüfen": [
"Check input",
"Vérifier la saisie",
"Iigab prüefe"
],
"Nichts zu tun": [
"Nothing to do",
"Rien à faire",
"Nüt z tue"
],
"Alle Server der Liste sind schon umgestellt.\n\nNeue Zeilen eintragen.": [
"All servers in the list have already been reconfigured.\n\nEnter new rows.",
"Tous les serveurs de la liste sont déjà reconfigurés.\n\nSaisir de nouvelles lignes.",
"Alli Server vo de Lischte sind scho umgstellt. De Steward hät Fiirabig.\n\nNeui Ziile iiträge."
],
"Die Zahl stimmt nicht. Es wurde nichts ausgeführt.": [
"The number does not match. Nothing was executed.",
"Le nombre ne correspond pas. Rien n'a été exécuté.",
"D Zahl stimmt nöd. Sicher isch sicher: Es isch nüt uusgfüehrt worde."
],
"DHCP-Server gestoppt: Über diese Karte läuft jetzt die Standardroute, das ist kein getrenntes Staging-Netz mehr.": [
"DHCP server stopped: the default route now runs over this card, so it is no longer a separate staging network.",
"Serveur DHCP arrêté : la route par défaut passe désormais par cette carte, ce n'est plus un réseau de staging séparé.",
"DHCP-Server gstoppt: Über die Charte lauft jetzt d Standardroute, das isch kei trännts Staging-Netz meh."
],
"Sprache": [
"Language",
"Langue",
"Sproch"
],
"Die Sprache lässt sich nur wechseln, solange kein Lauf aktiv ist und der DHCP-Server gestoppt ist.": [
"The language can only be changed while no run is active and the DHCP server is stopped.",
"La langue ne peut être changée que si aucune exécution n'est en cours et que le serveur DHCP est arrêté.",
"D Sproch laht sich nur wächsle, solang kein Luuf aktiv isch und de DHCP-Server gstoppt isch."
],
"    Hinweis: bei HPE heißt der Werksbenutzer meist Administrator": [
"    Note: on HPE the factory user is usually Administrator",
"    Remarque : chez HPE, l'utilisateur d'usine est généralement Administrator",
"    Hiiwiis: bi HPE heisst de Werksbenutzer meischtens Administrator"
],
"    Hinweis: bei Supermicro heißt der Werksbenutzer meist ADMIN": [
"    Note: on Supermicro the factory user is usually ADMIN",
"    Remarque : chez Supermicro, l'utilisateur d'usine est généralement ADMIN",
"    Hiiwiis: bi Supermicro heisst de Werksbenutzer meischtens ADMIN"
],
"    Hinweis: bei Lenovo heißt der Werksbenutzer meist USERID": [
"    Note: on Lenovo the factory user is usually USERID",
"    Remarque : chez Lenovo, l'utilisateur d'usine est généralement USERID",
"    Hiiwiis: bi Lenovo heisst de Werksbenutzer meischtens USERID"
],
"== Abgebrochen, übrige Server nicht bearbeitet": [
"== Cancelled, remaining servers not processed",
"== Annulé, serveurs restants non traités",
"== Abbroche, übrigi Server nöd bearbeitet"
],
"Abbruch": [
"cancelled",
"annulé",
"Abbruch"
],
"== {0}: kein Passwort (weder in der Zeile noch oben)": [
"== {0}: no password (neither in the row nor above)",
"== {0} : pas de mot de passe (ni dans la ligne ni en haut)",
"== {0}: kei Passwort (weder i de Ziile no obe)"
],
"  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Dell-Ablauf": [
"  Dry run: vendor not detectable ({0}), showing the Dell sequence",
"  Simulation : constructeur non détectable ({0}), affichage du déroulement Dell",
"  Trocheluuf: Hersteller nöd erchännbar ({0}), zeige de Dell-Ablauf"
],
"  FEHLER: nicht erreichbar": [
"  ERROR: not reachable",
"  ERREUR : injoignable",
"  FÄHLER: nöd erreichbar"
],
"  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)": [
"  ERROR: vendor not recognised (neither iDRAC, iLO, Supermicro nor XCC)",
"  ERREUR : constructeur non reconnu (ni iDRAC, ni iLO, ni Supermicro, ni XCC)",
"  FÄHLER: Hersteller nöd erchännt (weder iDRAC, iLO, Supermicro no XCC)"
],
"  FEHLER: Die neue IP {0} ist schon vergeben, dort antwortet bereits ein Gerät. Nichts geändert.": [
"  ERROR: the new IP {0} is already taken, a device answers there. Nothing changed.",
"  ERREUR : la nouvelle IP {0} est déjà prise, un appareil y répond. Rien n'a été modifié.",
"  FÄHLER: Di neu IP {0} isch scho vergäh, det antwortet scho es Grät. Nüt gänderet."
],
"  FEHLT   {0}": [
"  MISSING {0}",
"  ABSENT  {0}",
"  FÄHLT   {0}"
],
"BIOS-Attribute lesen": [
"Read BIOS attributes",
"Lecture des attributs BIOS",
"BIOS-Attribut läse"
],
"  FEHLER: gesperrt, wird nie gesetzt (Passwort-, TPM- und Lösch-Attribute): ": [
"  ERROR: locked, never set (password, TPM and erase attributes): ",
"  ERREUR : verrouillé, jamais défini (attributs de mot de passe, TPM et effacement) : ",
"  FÄHLER: gsperrt, wird nie gsetzt (Passwort-, TPM- und Lösch-Attribut): "
],
"  FEHLER: Wert passt nicht zum Attribut (Zahl bzw. Ein/Aus erwartet), nicht gesetzt: ": [
"  ERROR: value does not fit the attribute (number or on/off expected), not set: ",
"  ERREUR : la valeur ne convient pas à l'attribut (nombre ou activé/désactivé attendu), non défini : ",
"  FÄHLER: Wärt passt nöd zum Attribut (Zahl bzw. Ii/Uus erwartet), nöd gsetzt: "
],
"  nicht auf diesem Gerät vorhanden, übersprungen: ": [
"  not present on this device, skipped: ",
"  absent de cet appareil, ignoré : ",
"  uf dem Grät nöd vorhande, überspronge: "
],
"  schon so eingestellt: ": [
"  already set this way: ",
"  déjà réglé ainsi : ",
"  scho so iigstellt: "
],
"== {0}: kein Passwort": [
"== {0}: no password",
"== {0} : pas de mot de passe",
"== {0}: kei Passwort"
],
"Abgelehnt {0}  von {1}": [
"Refused   {0}  from {1}",
"Refusé    {0}  de {1}",
"Abglehnt  {0}  vo {1}"
],
"  FEHLER: unerwarteter Abbruch: {0!r}": [
"  ERROR: unexpected abort: {0!r}",
"  ERREUR : arrêt inattendu : {0!r}",
"  FÄHLER: unerwartete Abbruch: {0!r}"
],
"Gerät nicht erreichbar.": [
"Device not reachable.",
"Appareil injoignable.",
"Grät nöd erreichbar."
],
"Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC).": [
"Vendor not recognised (neither iDRAC, iLO, Supermicro nor XCC).",
"Constructeur non reconnu (ni iDRAC, ni iLO, ni Supermicro, ni XCC).",
"Hersteller nöd erchännt (weder iDRAC, iLO, Supermicro no XCC)."
],
"Unerwarteter Fehler: {0!r}": [
"Unexpected error: {0!r}",
"Erreur inattendue : {0!r}",
"Unerwartete Fähler: {0!r}"
],
"Ein DHCP-Server aus einem früheren, abgebrochenen Lauf lief noch und wurde beendet.": [
"A DHCP server from an earlier, aborted run was still running and has been stopped.",
"Un serveur DHCP d'une exécution précédente interrompue tournait encore et a été arrêté.",
"En DHCP-Server us eme früehnere, abbrochne Luuf isch no umegeischteret und isch beändet worde."
],
"Profile": [
"Profiles",
"Profils",
"Profil"
],
"{0} nicht schreibbar:\n{1}": [
"{0} cannot be written:\n{1}",
"Impossible d'écrire {0} :\n{1}",
"{0} laht sich nöd schriibe:\n{1}"
],
"Das Profil „{0}“ gibt es schon. Überschreiben?": [
"The profile “{0}” already exists. Overwrite?",
"Le profil « {0} » existe déjà. L'écraser ?",
"S Profil „{0}“ git's scho. Überschriibe?"
],
"{0} Server": [
"{0} servers",
"{0} serveurs",
"{0} Server"
],
" · <span style='color:#d9534f'>{0} mit Fehlern</span>": [
" · <span style='color:#d9534f'>{0} with errors</span>",
" · <span style='color:#d9534f'>{0} avec erreurs</span>",
" · <span style='color:#d9534f'>{0} mit Fähler</span>"
],
"Datei nicht lesbar:\n{0}": [
"File cannot be read:\n{0}",
"Fichier illisible :\n{0}",
"Datei nöd läsbar:\n{0}"
],
"Datei nicht schreibbar:\n{0}": [
"File cannot be written:\n{0}",
"Impossible d'écrire le fichier :\n{0}",
"Datei nöd schriibbar:\n{0}"
],
" (mit Passwörtern im Klartext)": [
" (with passwords in plain text)",
" (avec mots de passe en clair)",
" (mit Passwörter im Klartext)"
],
" (ohne Passwörter)": [
" (without passwords)",
" (sans mots de passe)",
" (ohni Passwörter)"
],
"{0} kommt mehrfach vor (Zeilen {1})": [
"{0} occurs more than once (rows {1})",
"{0} apparaît plusieurs fois (lignes {1})",
"{0} chunnt mehrfach vor (Ziile {1})"
],
"({0} weitere markierte Felder)": [
"({0} more marked fields)",
"({0} autres champs marqués)",
"({0} wiiteri markierti Fälder)"
],
"Attribute konnten nicht gelesen werden.": [
"Attributes could not be read.",
"Les attributs n'ont pas pu être lus.",
"D Attribut händ sich nöd la läse."
],
"UEFI-Vorlagen": [
"UEFI templates",
"Modèles UEFI",
"UEFI-Vorlage"
],
"Die Vorlage „{0}“ gibt es schon. Überschreiben?": [
"The template “{0}” already exists. Overwrite?",
"Le modèle « {0} » existe déjà. L'écraser ?",
"D Vorlag „{0}“ git's scho. Überschriibe?"
],
"… und {0} weitere": [
"… and {0} more",
"… et {0} autres",
"… und {0} wiiteri"
],
"Danach wird jeder Server hart neu gestartet, wie mit der Reset-Taste. Ein laufendes Betriebssystem wird dabei nicht sauber heruntergefahren.": [
"Afterwards every server is hard restarted, like pressing the reset button. A running operating system is not shut down cleanly.",
"Ensuite, chaque serveur est redémarré de force, comme avec le bouton Reset. Un système d'exploitation en marche n'est pas arrêté proprement.",
"Dänn wird jede Server hert neu gstartet, wie mit de Reset-Taschte. Es laufends Betriebssystem wird debii nöd suuber abegfahre."
],
"Es wird kein Neustart ausgelöst; die Änderung wirkt erst beim nächsten Neustart.": [
"No restart is triggered; the change takes effect at the next restart.",
"Aucun redémarrage n'est lancé ; la modification prend effet au prochain redémarrage.",
"Es wird kein Neustart uusglöst; d Änderig würkt erscht bim nächschte Neustart."
],
"Scharf ausführen?": [
"Run for real?",
"Exécuter réellement ?",
"Scharf uusfüehre?"
],
"Auf {0} Servern werden jetzt wirklich UEFI-Einstellungen geändert.": [
"UEFI settings will now really be changed on {0} servers.",
"Les réglages UEFI vont maintenant être réellement modifiés sur {0} serveurs.",
"Uf {0} Server wärded jetzt würkli UEFI-Iistellige gänderet."
],
"{0}  –  keine IPv4-Adresse": [
"{0}  –  no IPv4 address",
"{0}  –  pas d'adresse IPv4",
"{0}  –  kei IPv4-Adrässe"
],
"Liegt nicht im Netz der Karte ({0})": [
"Not in the network of the card ({0})",
"Hors du réseau de la carte ({0})",
"Liit nöd im Netz vo de Charte ({0})"
],
"Die Karte {0} hat keine IPv4-Adresse. Feste Adresse setzen, z. B. im Terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nDanach hier auf ↻ klicken.": [
"The card {0} has no IPv4 address. Set a static address, e.g. in the terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nThen click ↻ here.",
"La carte {0} n'a pas d'adresse IPv4. Définir une adresse fixe, p. ex. dans le terminal :\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nCliquer ensuite ici sur ↻.",
"D Charte {0} hät kei IPv4-Adrässe. Feschti Adrässe setze, z. B. im Terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nDänn da uf ↻ klicke."
],
"Diese Karte hat ihre eigene Adresse per DHCP bekommen. In diesem Netz läuft also schon ein DHCP-Server, ein zweiter würde dort alle Geräte stören. Deshalb wird hier keiner gestartet.\n\nFür das Staging der Karte eine feste Adresse geben.": [
"This card received its own address via DHCP. So a DHCP server is already running in this network, and a second one would disrupt all devices there. That is why none is started here.\n\nFor staging, give the card a static address.",
"Cette carte a reçu sa propre adresse par DHCP. Un serveur DHCP fonctionne donc déjà dans ce réseau, et un second y perturberait tous les appareils. C'est pourquoi aucun n'est démarré ici.\n\nPour le staging, donner une adresse fixe à la carte.",
"Die Charte hät ihri eigeti Adrässe per DHCP übercho. I dem Netz lauft also scho en DHCP-Server, en zweite würd det alli Grät störe. Drum wird da kein gstartet.\n\nFürs Staging de Charte e feschti Adrässe gäh."
],
"<b style='color:#d9534f'>Beendet (Code {0})</b>: ": [
"<b style='color:#d9534f'>Ended (code {0})</b>: ",
"<b style='color:#d9534f'>Terminé (code {0})</b> : ",
"<b style='color:#d9534f'>Beändet (Code {0})</b>: "
],
"⚠ Die aktuelle IP liegt in keinem Netz, an dem dieser Rechner direkt hängt. Für ein Staging-Gerät ist das ungewöhnlich. Ist es wirklich das richtige Gerät?": [
"⚠ The current IP is not in any network this computer is directly connected to. That is unusual for a staging device. Is it really the right device?",
"⚠ L'IP actuelle n'est dans aucun réseau auquel cet ordinateur est directement relié. C'est inhabituel pour un appareil en staging. Est-ce vraiment le bon appareil ?",
"⚠ Di aktuell IP liit i keim Netz, wo dä Rächner diräkt draa hanget. Für es Staging-Grät isch das ungwöhnlich. Isch es würkli s richtige Grät?"
],
"{0} Server werden jetzt wirklich umgestellt (Benutzer, Hostname, feste IP).": [
"{0} servers will now really be reconfigured (user, hostname, static IP).",
"{0} serveurs vont maintenant être réellement reconfigurés (utilisateur, nom d'hôte, IP fixe).",
"{0} Server wärded jetzt würkli umgstellt (Benutzer, Hostname, feschti IP)."
],
"— {0}, {1} Server": [
"— {0}, {1} servers",
"— {0}, {1} serveurs",
"— {0}, {1} Server"
],
" (ohne Zugriffsarten angelegt: Web-Anmeldung bitte einmal prüfen)": [
" (created without access types: please check the web login once)",
" (créé sans types d'accès : vérifier une fois la connexion web)",
" (ohni Zuegriffsarte aagleit: Web-Aamäldig bitte eimal prüefe)"
],
"Adressbereich erschöpft, keine Adresse mehr frei für ": [
"Address range exhausted, no address left for ",
"Plage d'adresses épuisée, plus d'adresse libre pour ",
"Adrässberiich uufbruucht, kei Adrässe meh frei für "
],
"Port 67 lässt sich nicht öffnen: {0}": [
"Port 67 cannot be opened: {0}",
"Impossible d'ouvrir le port 67 : {0}",
"Port 67 laht sich nöd uuftue: {0}"
],
" · <span style='color:#3fa45b'>{0} fertig</span>": [
" · <span style='color:#3fa45b'>{0} done</span>",
" · <span style='color:#3fa45b'>{0} terminés</span>",
" · <span style='color:#3fa45b'>{0} fertig</span>"
],
"Gesperrt: Passwort-, TPM- und Lösch-Attribute ändert das Tool nie. Ein Fehler dort kann den Server aussperren oder Daten kosten.": [
"Locked: the tool never changes password, TPM and erase attributes. A mistake there can lock you out of the server or cost data.",
"Verrouillé : l'outil ne modifie jamais les attributs de mot de passe, TPM et effacement. Une erreur peut bloquer l'accès au serveur ou coûter des données.",
"Gsperrt: Passwort-, TPM- und Lösch-Attribut änderet s Tool nie. En Fähler det cha de Server uussperre oder Date choschte."
],
"Netz- oder Broadcast-Adresse": [
"Network or broadcast address",
"Adresse de réseau ou de diffusion",
"Netz- oder Broadcast-Adrässe"
],
"Auf dieser Karte antwortet bereits ein DHCP-Server ({0}). Ein zweiter würde dort alle Geräte stören, deshalb wird hier keiner gestartet.": [
"A DHCP server already answers on this card ({0}). A second one would disrupt all devices there, so none is started here.",
"Un serveur DHCP répond déjà sur cette carte ({0}). Un second y perturberait tous les appareils ; aucun n'est donc démarré ici.",
"Uf dere Charte antwortet scho en DHCP-Server ({0}). En zweite würd det alli Grät störe, drum wird da kein gstartet."
],
"siehe Protokoll": [
"see log",
"voir le journal",
"lueg im Protokoll"
],
", {0} fertige übersprungen": [
", {0} done ones skipped",
", {0} terminés ignorés",
", {0} fertigi überspronge"
],
"  OK      {0}": [
"  OK      {0}",
"  OK      {0}",
"  OK      {0}"
],
"Paket verworfen: {0}": [
"Packet discarded: {0}",
"Paquet rejeté : {0}",
"Paket verworfe: {0}"
],
"Fertig umgestellt, wird beim nächsten Start übersprungen.\nSoll das Gerät noch einmal bearbeitet werden: Zeile markieren und „Erneut bearbeiten“ klicken.": [
"Reconfigured; skipped at the next start.\nTo process the device again: select the row and click “Process again”.",
"Reconfiguré ; ignoré au prochain démarrage.\nPour retraiter l'appareil : sélectionner la ligne et cliquer sur « Traiter à nouveau ».",
"Fertig umgstellt, wird bim nächschte Start überspronge.\nSöll s Grät nomal bearbeitet wärde: Ziile markiere und „Nomal bearbeite“ klicke."
],
"dnsmasq ist nicht installiert:\n\nsudo pacman -S dnsmasq": [
"dnsmasq is not installed:\n\nsudo pacman -S dnsmasq",
"dnsmasq n'est pas installé :\n\nsudo pacman -S dnsmasq",
"dnsmasq isch nöd installiert:\n\nsudo pacman -S dnsmasq"
],
"Pflichtfeld, noch leer.": [
"Required field, still empty.",
"Champ obligatoire, encore vide.",
"Pflichtfäld, no läär."
],
"Adressbereich: ": [
"Address range: ",
"Plage d'adresses : ",
"Adrässberiich: "
],
"pkexec (polkit) fehlt, damit kann der DHCP-Server nicht mit Root-Rechten starten.": [
"pkexec (polkit) is missing, so the DHCP server cannot start with root rights.",
"pkexec (polkit) est absent ; le serveur DHCP ne peut donc pas démarrer avec les droits root.",
"pkexec (polkit) fählt, drum cha de DHCP-Server nöd mit Root-Rächt starte."
],
"z. B. 10.20.0.1": [
"e.g. 10.20.0.1",
"p. ex. 10.20.0.1",
"z. B. 10.20.0.1"
],
"Passwort wiederholen": [
"Repeat password",
"Répéter le mot de passe",
"Passwort nomal"
],
"Das Passwort zur Sicherheit ein zweites Mal eingeben.\nStimmen die beiden Eingaben nicht überein, startet kein Lauf.": [
"Enter the password a second time to be safe.\nIf the two entries differ, no run starts.",
"Saisir le mot de passe une seconde fois par sécurité.\nSi les deux saisies diffèrent, rien ne démarre.",
"S Passwort zur Sicherheit es zweits Mal iitippe.\nStimmed di beide Iigabe nöd überii, startet kein Luuf."
],
"Neues Passwort: Die beiden Eingaben stimmen nicht überein.": [
"New password: the two entries do not match.",
"Nouveau mot de passe : les deux saisies ne correspondent pas.",
"Neus Passwort: Di beide Iigabe stimmed nöd überii."
],
"Protokoll speichern …": [
"Save log …",
"Enregistrer le journal …",
"Protokoll speichere …"
],
"Protokoll speichern": [
"Save log",
"Enregistrer le journal",
"Protokoll speichere"
],
"Speichert das Protokoll als Textdatei, z. B. für die Dokumentation.\nPasswörter stehen nicht darin.": [
"Saves the log as a text file, e.g. for documentation.\nIt contains no passwords.",
"Enregistre le journal dans un fichier texte, p. ex. pour la documentation.\nIl ne contient aucun mot de passe.",
"Speicheret s Protokoll als Textdatei, z. B. für d Dokumentation.\nPasswörter stönd keini dine."
],
"Das Protokoll ist noch leer.": [
"The log is still empty.",
"Le journal est encore vide.",
"S Protokoll isch no läär."
],
"Text (*.log *.txt);;Alle Dateien (*)": [
"Text (*.log *.txt);;All files (*)",
"Texte (*.log *.txt);;Tous les fichiers (*)",
"Text (*.log *.txt);;Alli Dateie (*)"
],
"Protokoll gespeichert in {0}": [
"Log saved to {0}",
"Journal enregistré dans {0}",
"Protokoll gspeicheret i {0}"
],
"Zeigt, was vor dem Start noch auszufüllen ist. Die betroffenen Felder sind\ngelb (fehlt noch) oder rot (fehlerhaft) markiert.": [
"Shows what still has to be filled in before starting. The fields concerned are\nmarked yellow (still missing) or red (wrong).",
"Indique ce qu'il reste à remplir avant de démarrer. Les champs concernés sont\nmarqués en jaune (manquant) ou en rouge (erroné).",
"Zeiget, was vor em Start no uuszfülle isch. Di betroffene Fälder sind\ngäl (fählt no) oder rot (fählerhaft) markiert."
],
"fehlt noch": [
"still missing",
"manquant",
"fählt no"
],
"fehlerhaft": [
"wrong",
"erroné",
"fählerhaft"
],
"fertig": [
"done",
"terminé",
"fertig"
],
"Neuer Administrator: Benutzer": [
"New administrator: user",
"Nouvel administrateur : utilisateur",
"Neue Administrator: Benutzer"
],
"Neuer Administrator: Passwort": [
"New administrator: password",
"Nouvel administrateur : mot de passe",
"Neue Administrator: Passwort"
],
"mindestens ein Server in der Liste": [
"at least one server in the list",
"au moins un serveur dans la liste",
"mindeschtens ein Server i de Lischte"
],
"{0} leere Pflichtfelder in der Serverliste": [
"{0} empty required fields in the server list",
"{0} champs obligatoires vides dans la liste des serveurs",
"{0} lääri Pflichtfälder i de Serverlischte"
],
"{0} fehlerhafte Felder in der Serverliste": [
"{0} wrong fields in the server list",
"{0} champs erronés dans la liste des serveurs",
"{0} fählerhafti Fälder i de Serverlischte"
],
"Es fehlt noch:": [
"Still missing:",
"Il manque encore :",
"Es fählt no:"
],
"Alle Server der Liste sind fertig.": [
"All servers in the list are done.",
"Tous les serveurs de la liste sont terminés.",
"Alli Server sind fertig. Ziit für es Kafi."
],
"Alles ausgefüllt. Jetzt den Trockenlauf starten.": [
"Everything is filled in. Now start the dry run.",
"Tout est rempli. Lancer maintenant la simulation.",
"Alles uusgfüllt, suuber. Jetzt de Trocheluuf starte."
],
"Alles ausgefüllt. Der nächste Start stellt die Geräte wirklich um.": [
"Everything is filled in. The next start really reconfigures the devices.",
"Tout est rempli. Le prochain démarrage reconfigure réellement les appareils.",
"Alles uusgfüllt. Jetzt gilt's ernscht: De nächscht Start stellt d Grät würkli um."
],
"Trockenlauf starten": [
"Start dry run",
"Lancer la simulation",
"Trocheluuf starte"
],
"Scharf starten": [
"Start live run",
"Lancer l'exécution réelle",
"Scharf starte"
],
"Trockenlauf für alle Server der Liste": [
"Dry run for all servers in the list",
"Simulation pour tous les serveurs de la liste",
"Trocheluuf für alli Server vo de Lischte"
],
"Scharf auf allen Servern der Liste setzen": [
"Set for real on all servers in the list",
"Appliquer réellement à tous les serveurs de la liste",
"Scharf uf allne Server vo de Lischte setze"
],
"  FEHLER: Die Verbindung brach ab, aber das Gerät antwortet weiter unter der alten Adresse. Netzwerk nicht umgestellt.": [
"  ERROR: the connection dropped, but the device still answers on the old address. Network not changed.",
"  ERREUR : la connexion a été coupée, mais l'appareil répond toujours à l'ancienne adresse. Réseau non modifié.",
"  FÄHLER: D Verbindig isch abgrisse, aber s Grät antwortet wiiter under de alte Adrässe. Netzwerk nöd umgstellt."
],
"Ausgelegt auf: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 bis 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Andere Generationen zuerst an einem einzelnen Gerät testen.": [
"Designed for: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 to 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Try other generations on a single device first.",
"Conçu pour : Dell iDRAC9, iDRAC7/8 · HPE iLO 3 à 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Essayer d'abord les autres générations sur un seul appareil.",
"Uusgleit uf: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 bis 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Anderi Generatione zerscht a eim einzelne Grät teste."
],
"iDRAC9 (14. bis 16. Generation, z. B. R6625) und iDRAC7/8 (12./13. Generation) ab Firmware 2.50.50.50. iDRAC10 ist nicht geprüft, iDRAC6 wird nicht unterstützt.": [
"iDRAC9 (14th to 16th generation, e.g. R6625) and iDRAC7/8 (12th/13th generation) from firmware 2.50.50.50. iDRAC10 is untested, iDRAC6 is not supported.",
"iDRAC9 (14e à 16e génération, p. ex. R6625) et iDRAC7/8 (12e/13e génération) à partir du firmware 2.50.50.50. iDRAC10 n'est pas testé, iDRAC6 n'est pas pris en charge.",
"iDRAC9 (14. bis 16. Generation, z. B. R6625) und iDRAC7/8 (12./13. Generation) ab Firmware 2.50.50.50. iDRAC10 isch nöd prüeft, iDRAC6 wird nöd understützt."
],
"iLO 3 (G7) und iLO 4 (Gen8, Gen9), angesprochen über die XML-Schnittstelle RIBCL. iLO 2 und älter werden nicht unterstützt.": [
"iLO 3 (G7) and iLO 4 (Gen8, Gen9), addressed through the RIBCL XML interface. iLO 2 and older are not supported.",
"iLO 3 (G7) et iLO 4 (Gen8, Gen9), pilotés via l'interface XML RIBCL. iLO 2 et versions antérieures ne sont pas pris en charge.",
"iLO 3 (G7) und iLO 4 (Gen8, Gen9), aagsproche über d XML-Schnittstell RIBCL. iLO 2 und älter wärded nöd understützt."
],
"Keine gültige Antwort der XML-Schnittstelle (RIBCL)": [
"No valid answer from the XML interface (RIBCL)",
"Pas de réponse valide de l'interface XML (RIBCL)",
"Kei gültigi Antwort vo de XML-Schnittstell (RIBCL)"
],
"    Erkannt: {0}. Dieses Gerät wird nicht unterstützt.": [
"    Detected: {0}. This device is not supported.",
"    Détecté : {0}. Cet appareil n'est pas pris en charge.",
"    Erchännt: {0}. Das Grät wird nöd understützt."
],
"    Hinweis: Geräte ohne Redfish (iDRAC6, iLO 2, sehr alte Firmware) werden nicht unterstützt.": [
"    Note: devices without Redfish (iDRAC6, iLO 2, very old firmware) are not supported.",
"    Remarque : les appareils sans Redfish (iDRAC6, iLO 2, firmware très ancien) ne sont pas pris en charge.",
"    Hiiwiis: Grät ohni Redfish (iDRAC6, iLO 2, uralti Firmware) wärded nöd understützt."
],
"  Firmware {0}": [
"  Firmware {0}",
"  Firmware {0}",
"  Firmware {0}"
],
"  FEHLER: Diese iDRAC-Firmware ist zu alt für das Tool (iDRAC7/8 erst ab 2.50.50.50). Bitte zuerst die Firmware aktualisieren. Nichts geändert.": [
"  ERROR: this iDRAC firmware is too old for the tool (iDRAC7/8 only from 2.50.50.50). Please update the firmware first. Nothing changed.",
"  ERREUR : ce firmware iDRAC est trop ancien pour l'outil (iDRAC7/8 seulement à partir de 2.50.50.50). Mettre d'abord le firmware à jour. Rien n'a été modifié.",
"  FÄHLER: Die iDRAC-Firmware isch z alt fürs Tool (iDRAC7/8 erscht ab 2.50.50.50). Bitte zerscht d Firmware aktualisiere. Nüt gänderet."
],
"  FEHLER: Hostname zu lang, iLO 3/4 erlaubt höchstens 49 Zeichen. Nichts geändert.": [
"  ERROR: hostname too long, iLO 3/4 allows at most 49 characters. Nothing changed.",
"  ERREUR : nom d'hôte trop long, iLO 3/4 accepte au plus 49 caractères. Rien n'a été modifié.",
"  FÄHLER: Hostname z lang, iLO 3/4 erlaubt höchschtens 49 Zeiche. Nüt gänderet."
],
"    Hinweis: iLO 3 und 4 werden über die XML-Schnittstelle (RIBCL) konfiguriert. iLO 2 und älter werden nicht unterstützt.": [
"    Note: iLO 3 and 4 are configured through the XML interface (RIBCL). iLO 2 and older are not supported.",
"    Remarque : iLO 3 et 4 sont configurés via l'interface XML (RIBCL). iLO 2 et versions antérieures ne sont pas pris en charge.",
"    Hiiwiis: iLO 3 und 4 wärded über d XML-Schnittstell (RIBCL) konfiguriert. iLO 2 und älter wärded nöd understützt."
],
"  Gerät: {0}, Firmware {1}": [
"  Device: {0}, firmware {1}",
"  Appareil : {0}, firmware {1}",
"  Grät: {0}, Firmware {1}"
],
"Benutzer lesen": [
"Read users",
"Lecture des utilisateurs",
"Benutzer läse"
],
"  Netzwerk und Hostname gesetzt, das iLO startet von selbst neu": [
"  Network and hostname set, the iLO restarts by itself",
"  Réseau et nom d'hôte définis, l'iLO redémarre de lui-même",
"  Netzwerk und Hostname gsetzt, s iLO startet vo sälber neu"
],
"Netzwerk und Hostname": [
"Network and hostname",
"Réseau et nom d'hôte",
"Netzwerk und Hostname"
],
"  Hinweis: altes Gerät, die Verbindung nutzt veraltete Verschlüsselung (TLS 1.0/1.1).": [
"  Note: old device, the connection uses outdated encryption (TLS 1.0/1.1).",
"  Remarque : appareil ancien, la connexion utilise un chiffrement obsolète (TLS 1.0/1.1).",
"  Hiiwiis: alts Grät, d Verbindig bruucht veralteti Verschlüsselig (TLS 1.0/1.1)."
],
"  Hinweis: iLO 3 kann keine UEFI-Einstellungen über Redfish. Bei iLO 4 geht es nur ab Gen9 mit Firmware 2.30 und ist ungeprüft.": [
"  Note: iLO 3 cannot do UEFI settings via Redfish. With iLO 4 it only works from Gen9 with firmware 2.30 and is untested.",
"  Remarque : iLO 3 ne gère pas les réglages UEFI via Redfish. Avec iLO 4, cela ne fonctionne qu'à partir de Gen9 avec le firmware 2.30 et n'est pas testé.",
"  Hiiwiis: iLO 3 cha kei UEFI-Iistellige über Redfish. Bi iLO 4 gaht's nume ab Gen9 mit Firmware 2.30 und isch nöd prüeft."
],
"LDAP": [
"LDAP",
"LDAP",
"LDAP"
],
"NTP": [
"NTP",
"NTP",
"NTP"
],
"Syslog": [
"Syslog",
"Syslog",
"Syslog"
],
"HTTPS-Zertifikat": [
"HTTPS certificate",
"Certificat HTTPS",
"HTTPS-Zertifikat"
],
"4 · Dienste": [
"4 · Services",
"4 · Services",
"4 · Dienscht"
],
"5 · Hilfe": [
"5 · Help",
"5 · Aide",
"5 · Hilf"
],
"Login (Werksbenutzer, gilt für alle Server der Liste)": [
"Login (factory user, used for every server in the list)",
"Connexion (utilisateur d'usine, pour tous les serveurs de la liste)",
"Login (Wärksbenutzer, gilt für alli Server vo de Lischte)"
],
"Die Dienste werden auf allen Servern der Serverliste (Reiter 2) eingerichtet.\nFertige (grüne) Server werden unter ihrer neuen Adresse angesprochen.\nAngemeldet wird mit dem Werksbenutzer; der Werks-Administrator bleibt bestehen.": [
"The services are set up on every server in the list (tab 2).\nFinished (green) servers are reached at their new address.\nLogin uses the factory user; the factory administrator stays in place.",
"Les services sont configurés sur tous les serveurs de la liste (onglet 2).\nLes serveurs terminés (verts) sont joints à leur nouvelle adresse.\nLa connexion utilise l'utilisateur d'usine ; l'administrateur d'usine reste en place.",
"D Dienscht wärded uf allne Server vo de Lischte (Riiter 2) iigrichtet.\nFertigi (grüeni) Server wärded under ihrer neue Adrässe aagsproche.\nAagmäldet wird mit em Wärksbenutzer; de Wärks-Administrator bliibt bestah."
],
"Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ fragt jedes Gerät.": [
"A fixed choice skips detection. “Automatic” asks each device.",
"Un choix fixe saute la détection. « Automatique » interroge chaque appareil.",
"E feschti Wahl überspringt d Erkennig. „Automatisch“ fragt jedes Grät."
],
"Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet.": [
"Leave empty: the factory user is used per vendor.",
"Laisser vide : l'utilisateur d'usine est utilisé selon le constructeur.",
"Läär laa: Denn wird je Hersteller de Wärksbenutzer gnoh."
],
"Fällt für Zeilen ein, deren Spalte „Passwort“ in der Serverliste leer ist.": [
"Used for rows whose “Password” column in the server list is empty.",
"Utilisé pour les lignes dont la colonne « Mot de passe » est vide.",
"Gilt für Ziile, wo d Spalte „Passwort“ i de Serverlischte läär isch."
],
"NTP (Zeitserver)": [
"NTP (time servers)",
"NTP (serveurs de temps)",
"NTP (Ziitserver)"
],
"z. B. 10.20.0.1 oder pool.ntp.org": [
"e.g. 10.20.0.1 or pool.ntp.org",
"p. ex. 10.20.0.1 ou pool.ntp.org",
"z. B. 10.20.0.1 oder pool.ntp.org"
],
"optional": [
"optional",
"facultatif",
"optional"
],
"nur Dell, z. B. Europe/Berlin": [
"Dell only, e.g. Europe/Berlin",
"Dell uniquement, p. ex. Europe/Berlin",
"nur Dell, z. B. Europe/Berlin"
],
"NTP-Server 1": [
"NTP server 1",
"Serveur NTP 1",
"NTP-Server 1"
],
"Pflicht, wenn NTP eingerichtet wird.": [
"Required when setting up NTP.",
"Obligatoire si NTP est configuré.",
"Pflicht, wenn NTP iigrichtet wird."
],
"NTP-Server 2": [
"NTP server 2",
"Serveur NTP 2",
"NTP-Server 2"
],
"Optional, zweiter Zeitserver.": [
"Optional, second time server.",
"Facultatif, deuxième serveur de temps.",
"Optional, zweite Ziitserver."
],
"Zeitzone": [
"Time zone",
"Fuseau horaire",
"Ziitzone"
],
"Nur bei Dell gesetzt. Bei HPE, Supermicro und Lenovo bitte in der Oberfläche wählen.": [
"Set on Dell only. On HPE, Supermicro and Lenovo please choose it in the web interface.",
"Défini uniquement sur Dell. Sur HPE, Supermicro et Lenovo, le choisir dans l'interface.",
"Nur bi Dell gsetzt. Bi HPE, Supermicro und Lenovo bitte i de Oberflächi wähle."
],
"Nur Dell und HPE. Bei Supermicro und Lenovo wird Syslog übersprungen.": [
"Dell and HPE only. On Supermicro and Lenovo syslog is skipped.",
"Dell et HPE uniquement. Sur Supermicro et Lenovo, syslog est ignoré.",
"Nur Dell und HPE. Bi Supermicro und Lenovo wird Syslog übersprunge."
],
"z. B. 10.20.0.9": [
"e.g. 10.20.0.9",
"p. ex. 10.20.0.9",
"z. B. 10.20.0.9"
],
"Server": [
"Server",
"Serveur",
"Server"
],
"Adresse des Syslog-Empfängers.": [
"Address of the syslog receiver.",
"Adresse du récepteur syslog.",
"Adrässe vom Syslog-Empfänger."
],
"Port": [
"Port",
"Port",
"Port"
],
"Standard 514.": [
"Default 514.",
"Par défaut 514.",
"Standard 514."
],
"LDAP / Active Directory": [
"LDAP / Active Directory",
"LDAP / Active Directory",
"LDAP / Active Directory"
],
"z. B. ad.example.com": [
"e.g. ad.example.com",
"p. ex. ad.example.com",
"z. B. ad.example.com"
],
"optional, z. B. cn=svc,dc=example,dc=com": [
"optional, e.g. cn=svc,dc=example,dc=com",
"facultatif, p. ex. cn=svc,dc=example,dc=com",
"optional, z. B. cn=svc,dc=example,dc=com"
],
"Adresse des LDAP-/AD-Servers.": [
"Address of the LDAP/AD server.",
"Adresse du serveur LDAP/AD.",
"Adrässe vom LDAP-/AD-Server."
],
"389 unverschlüsselt, 636 für LDAPS.": [
"389 plain, 636 for LDAPS.",
"389 en clair, 636 pour LDAPS.",
"389 unverschlüsselet, 636 für LDAPS."
],
"Base-DN": [
"Base DN",
"Base DN",
"Base-DN"
],
"Ab wo gesucht wird, z. B. dc=example,dc=com.": [
"Where the search starts, e.g. dc=example,dc=com.",
"Point de départ de la recherche, p. ex. dc=example,dc=com.",
"Ab wo gsuecht wird, z. B. dc=example,dc=com."
],
"Bind-DN": [
"Bind DN",
"Bind DN",
"Bind-DN"
],
"Dienstkonto zum Suchen. Leer lassen, wenn der Server anonyme Suche erlaubt.": [
"Service account for searching. Leave empty if the server allows anonymous search.",
"Compte de service pour la recherche. Laisser vide si la recherche anonyme est permise.",
"Dienstkonto zum Sueche. Läär laa, wenn de Server anonymi Suech erlaubt."
],
"Bind-Passwort": [
"Bind password",
"Mot de passe du bind",
"Bind-Passwort"
],
"Passwort des Bind-DN.": [
"Password of the bind DN.",
"Mot de passe du bind DN.",
"Passwort vom Bind-DN."
],
"Benutzer-Attribut": [
"User attribute",
"Attribut utilisateur",
"Benutzer-Attribut"
],
"Active Directory: sAMAccountName. OpenLDAP oft uid.": [
"Active Directory: sAMAccountName. OpenLDAP often uid.",
"Active Directory : sAMAccountName. OpenLDAP souvent uid.",
"Active Directory: sAMAccountName. OpenLDAP of uid."
],
"Gruppen-Attribut": [
"Group attribute",
"Attribut de groupe",
"Gruppe-Attribut"
],
"Meist memberOf.": [
"Usually memberOf.",
"En général memberOf.",
"Meischtens memberOf."
],
"Ordnet LDAP-Gruppen einer Rolle zu: Administrator, Operator oder ReadOnly.\nLeer lassen, wenn keine Gruppen-Zuordnung gebraucht wird.": [
"Maps LDAP groups to a role: Administrator, Operator or ReadOnly.\nLeave empty if no group mapping is needed.",
"Associe des groupes LDAP à un rôle : Administrator, Operator ou ReadOnly.\nLaisser vide si aucun mappage de groupe n'est nécessaire.",
"Ordnet LDAP-Gruppe ere Rolle zue: Administrator, Operator oder ReadOnly.\nLäär laa, wenn kei Gruppe-Zueordnig bruucht wird."
],
"+ Gruppe": [
"+ Group",
"+ Groupe",
"+ Gruppe"
],
"– Gruppe": [
"– Group",
"– Groupe",
"– Gruppe"
],
"Experimentell und je Hersteller verschieden. Importiert ein von einer CA signiertes\nZertifikat (PEM). Bei HPE muss der passende Schlüssel aus einer auf dem iLO erzeugten\nZertifikatsanforderung (CSR) stammen, sonst scheitert der Import.": [
"Experimental and vendor-specific. Imports a CA-signed certificate (PEM). On HPE the\nmatching key must come from a CSR generated on the iLO, otherwise the import fails.",
"Expérimental et propre au constructeur. Importe un certificat signé par une CA (PEM).\nSur HPE, la clé correspondante doit provenir d'une CSR générée sur l'iLO, sinon l'import échoue.",
"Experimentell und je Hersteller andersch. Importiert es vonere CA signiets Zertifikat (PEM).\nBi HPE mues de passend Schlüssel us ere uf em iLO erzüügte CSR cho, susch schiitert de Import."
],
"keine Datei gewählt": [
"no file chosen",
"aucun fichier choisi",
"kei Datei gwählt"
],
"PEM-Datei wählen …": [
"Choose PEM file …",
"Choisir un fichier PEM …",
"PEM-Datei wähle …"
],
"Mit Haken wird nichts geändert; das Protokoll zeigt nur die Aufrufe.": [
"When ticked nothing is changed; the log only shows the calls.",
"Si coché, rien n'est modifié ; le journal montre seulement les appels.",
"Mit Haagge wird nüt gänderet; s Protokoll zeigt nur d Uffruef."
],
"Zertifikat wählen": [
"Choose certificate",
"Choisir un certificat",
"Zertifikat wähle"
],
"Zertifikat (*.pem *.crt *.cer);;Alle Dateien (*)": [
"Certificate (*.pem *.crt *.cer);;All files (*)",
"Certificat (*.pem *.crt *.cer);;Tous les fichiers (*)",
"Zertifikat (*.pem *.crt *.cer);;Alli Datei (*)"
],
"LDAP-Gruppe": [
"LDAP group",
"Groupe LDAP",
"LDAP-Gruppe"
],
"Rolle": [
"Role",
"Rôle",
"Rolle"
],
"Gruppen → Rolle": [
"Groups → role",
"Groupes → rôle",
"Gruppe → Rolle"
],
"Zertifikat": [
"Certificate",
"Certificat",
"Zertifikat"
],
"Die Datei enthält kein PEM-Zertifikat (erwartet „BEGIN CERTIFICATE“).": [
"The file contains no PEM certificate (expected “BEGIN CERTIFICATE”).",
"Le fichier ne contient pas de certificat PEM (« BEGIN CERTIFICATE » attendu).",
"D Datei hät kei PEM-Zertifikat (erwartet „BEGIN CERTIFICATE“)."
],
"Dienste": [
"Services",
"Services",
"Dienscht"
],
"Kein Abschnitt angehakt. Oben mindestens einen aktivieren.": [
"No section ticked. Enable at least one above.",
"Aucune section cochée. En activer au moins une ci-dessus.",
"Kei Abschnitt aaghaaggt. Obe mindeschtens eine aktiviere."
],
"Es werden Verwaltungseinstellungen der BMCs geändert. Eine falsche LDAP- oder Zertifikatseinstellung kann die Anmeldung erschweren; der lokale Administrator bleibt aber bestehen.": [
"Management settings of the BMCs are changed. A wrong LDAP or certificate setting can make signing in harder; the local administrator stays in place, though.",
"Les réglages de gestion des BMC sont modifiés. Un mauvais réglage LDAP ou de certificat peut compliquer la connexion ; l'administrateur local reste toutefois en place.",
"Es wärded Verwaltigs-Iistellige vo de BMC gänderet. E falschi LDAP- oder Zertifikats-Iistellig cha s Aamälde erschwere; de lokal Administrator bliibt aber bestah."
],
"    Zeitzone bei HPE bitte in der iLO-Oberfläche setzen (hier nicht gesetzt).": [
"    Please set the time zone on HPE in the iLO interface (not set here).",
"    Veuillez définir le fuseau horaire HPE dans l'interface iLO (non défini ici).",
"    Ziitzone bi HPE bitte i de iLO-Oberflächi setze (da nöd gsetzt)."
],
"    Zeitzone bei diesem Hersteller bitte in der Oberfläche setzen (hier nicht gesetzt).": [
"    Please set the time zone for this vendor in its interface (not set here).",
"    Veuillez définir le fuseau horaire de ce constructeur dans son interface (non défini ici).",
"    Ziitzone bi dem Hersteller bitte i de Oberflächi setze (da nöd gsetzt)."
],
"  iLO 3/4: Dienste werden hier nicht unterstützt, übersprungen.": [
"  iLO 3/4: services are not supported here, skipped.",
"  iLO 3/4 : services non pris en charge ici, ignoré.",
"  iLO 3/4: Dienscht wärded da nöd understützt, übersprunge."
],
"Nur angehakte Abschnitte werden angewendet. Alles wird auf den Servern der Liste in Reiter 2 gesetzt, grüne unter ihrer neuen Adresse.": [
"Only ticked sections are applied. Everything is set on the servers in the list on tab 2, green ones at their new address.",
"Seules les sections cochées sont appliquées. Tout est défini sur les serveurs de la liste de l'onglet 2, les verts à leur nouvelle adresse.",
"Nur aaghaaggti Abschnitt wärded aagwändet. Alles wird uf de Server vo de Lischte i Riiter 2 gsetzt, grüeni under ihrer neue Adrässe."
],
"NTP: mindestens den ersten Server eintragen.": [
"NTP: enter at least the first server.",
"NTP : saisir au moins le premier serveur.",
"NTP: mindeschtens de erscht Server iiträge."
],
"Syslog: Server eintragen.": [
"Syslog: enter the server.",
"Syslog : saisir le serveur.",
"Syslog: de Server iiträge."
],
"Port muss eine Zahl von 1 bis 65535 sein.": [
"Port must be a number from 1 to 65535.",
"Le port doit être un nombre de 1 à 65535.",
"Port mues e Zahl vo 1 bis 65535 si."
],
"LDAP: Server und Base-DN eintragen.": [
"LDAP: enter server and base DN.",
"LDAP : saisir le serveur et le base DN.",
"LDAP: Server und Base-DN iiträge."
],
"HTTPS-Zertifikat: erst eine PEM-Datei wählen.": [
"HTTPS certificate: choose a PEM file first.",
"Certificat HTTPS : choisir d'abord un fichier PEM.",
"HTTPS-Zertifikat: zerscht e PEM-Datei wähle."
],
"— {0}, {1} Server, {2} —": [
"— {0}, {1} servers, {2} —",
"— {0}, {1} serveurs, {2} —",
"— {0}, {1} Server, {2} —"
],
"  {0}: gesendet, die Verbindung brach dabei ab (der BMC startet oft neu).": [
"  {0}: sent, the connection dropped (the BMC often restarts).",
"  {0} : envoyé, la connexion a été coupée (le BMC redémarre souvent).",
"  {0}: gschickt, d Verbindig isch abgrisse (de BMC startet of neu)."
],
"  {0}: gesetzt": [
"  {0}: set",
"  {0} : défini",
"  {0}: gsetzt"
],
"Dienste: {0}": [
"Services: {0}",
"Services : {0}",
"Dienscht: {0}"
],
"Auf {0} Servern werden jetzt wirklich Dienste eingerichtet.": [
"Services will now really be set up on {0} servers.",
"Les services vont maintenant vraiment être configurés sur {0} serveurs.",
"Uf {0} Server wärded jetz würklich Dienscht iigrichtet."
],
"  {0}: auf diesem Hersteller nicht unterstützt, übersprungen": [
"  {0}: not supported on this vendor, skipped",
"  {0} : non pris en charge sur ce constructeur, ignoré",
"  {0}: uf dem Hersteller nöd understützt, übersprunge"
],
"Rolle muss Administrator, Operator oder ReadOnly sein (Gruppe {0}).": [
"Role must be Administrator, Operator or ReadOnly (group {0}).",
"Le rôle doit être Administrator, Operator ou ReadOnly (groupe {0}).",
"D Rolle mues Administrator, Operator oder ReadOnly si (Gruppe {0})."
],
"Adresse des BMC. Vorschläge aus der Serverliste (Reiter 2), es geht aber jede\nerreichbare Adresse.": ["Address of the BMC. Suggestions come from the server list (tab 2), but any\nreachable address works.", "Adresse du BMC. Suggestions issues de la liste des serveurs (onglet 2), mais toute\nadresse joignable convient.", "Adrässe vom BMC. Vorschläg chömed us de Serverlischte (Reiter 2), aber jedi\nerreichbari Adrässe goht."],
"Bild: Web-Konsole des BMC (eingebettet)": ["Picture: BMC web console (embedded)", "Image : console web du BMC (intégrée)", "Bild: Web-Konsole vom BMC (iibettet)"],
"Text: serielle Konsole (SOL/SSH)": ["Text: serial console (SOL/SSH)", "Texte : console série (SOL/SSH)", "Text: serielli Konsole (SOL/SSH)"],
"Bild: im Standardbrowser öffnen": ["Picture: open in default browser", "Image : ouvrir dans le navigateur par défaut", "Bild: im Standardbrowser uftue"],
"Automatisch": ["Automatic", "Automatique", "Automatisch"],
"Verbindung": ["Connection", "Connexion", "Verbindig"],
"Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den Hersteller\ndieses einen Geräts selbst.": ["A fixed choice skips detection. \"Detect automatically\" determines the vendor\nof this one device itself.", "Un choix fixe saute la détection. « Détecter automatiquement » détermine le fabricant\nde cet appareil lui-même.", "E feschti Wahl überspringt d Erkennig. „Automatisch erkenne“ findet de Hersteller\nvo däm einte Grät sälber use."],
"Wird nur für diese Verbindung verwendet und nirgends gespeichert.": ["Used only for this connection and never stored.", "Utilisé uniquement pour cette connexion et jamais enregistré.", "Wird nume für die Verbindig bruucht und nienemer gspeicheret."],
"Anzeige": ["Display", "Affichage", "Aazeig"],
"Bild: echtes Konsolenbild mit Maus und Tastatur wie am Original (HTML5-Konsole des BMC).\nText: serielle Umleitung; geht nur, wenn im UEFI „Console Redirection“ aktiv ist.\nBrowser: öffnet die BMC-Seite im Standardbrowser, ohne Auto-Anmeldung.": ["Picture: real console image with mouse and keyboard like on the original (HTML5 console of the BMC).\nText: serial redirection; works only if \"Console Redirection\" is enabled in the UEFI.\nBrowser: opens the BMC page in the default browser, without automatic login.", "Image : vraie image de la console avec souris et clavier comme sur l'original (console HTML5 du BMC).\nTexte : redirection série ; ne fonctionne que si « Console Redirection » est activé dans l'UEFI.\nNavigateur : ouvre la page du BMC dans le navigateur par défaut, sans connexion automatique.", "Bild: ächts Konsolebild mit Muus und Tastatur wie am Original (HTML5-Konsole vom BMC).\nText: serielli Umleitig; goht nume, wenn im UEFI „Console Redirection“ aktiv isch.\nBrowser: macht d BMC-Siite im Standardbrowser uf, ohni Auto-Aamäldig."],
"Verfahren": ["Method", "Méthode", "Verfahre"],
"Automatisch: Supermicro über ipmitool, Dell/HPE/Lenovo über SSH (sshpass), sonst ipmitool.\nIPMI über LAN muss im BMC aktiviert sein (bei Dell ab Werk aus).": ["Automatic: Supermicro via ipmitool, Dell/HPE/Lenovo via SSH (sshpass), otherwise ipmitool.\nIPMI over LAN must be enabled in the BMC (off by default on Dell).", "Automatique : Supermicro via ipmitool, Dell/HPE/Lenovo via SSH (sshpass), sinon ipmitool.\nIPMI sur LAN doit être activé dans le BMC (désactivé par défaut chez Dell).", "Automatisch: Supermicro über ipmitool, Dell/HPE/Lenovo über SSH (sshpass), susch ipmitool.\nIPMI über LAN muess im BMC aktiviert sii (bi Dell ab Werk uus)."],
"Terminaltyp": ["Terminal type", "Type de terminal", "Terminaltyp"],
"Muss zur Einstellung „Terminal Type“ im UEFI passen: VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (Rahmenzeichen).": ["Must match the \"Terminal Type\" setting in the UEFI: VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (box characters).", "Doit correspondre au réglage « Terminal Type » de l'UEFI : VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (caractères de cadre).", "Muess zur Iistellig „Terminal Type“ im UEFI passe: VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (Rahmezeiche)."],
"Größe": ["Size", "Taille", "Grössi"],
"Zeichen × Zeilen; muss zur Einstellung im UEFI passen (meist 80 × 24 oder 80 × 25).": ["Characters × lines; must match the UEFI setting (usually 80 × 24 or 80 × 25).", "Caractères × lignes ; doit correspondre au réglage de l'UEFI (souvent 80 × 24 ou 80 × 25).", "Zeiche × Zile; muess zur Iistellig im UEFI passe (meischtens 80 × 24 oder 80 × 25)."],
"Verbinden": ["Connect", "Connecter", "Verbinde"],
"Trennen": ["Disconnect", "Déconnecter", "Trenne"],
"Konsole starten": ["Start console", "Lancer la console", "Konsole starte"],
"Springt nach der Anmeldung zur Konsolen-Seite des BMC.": ["Jumps to the console page of the BMC after login.", "Passe à la page de console du BMC après la connexion.", "Springt nach de Aamäldig zur Konsole-Siite vom BMC."],
"Ins UEFI-Setup booten …": ["Boot into UEFI setup …", "Démarrer dans la configuration UEFI …", "Is UEFI-Setup boote …"],
"Merkt „beim nächsten Start ins UEFI-Setup“ vor und startet den Server hart neu\n(per Redfish). Nur mit Rückfrage.": ["Sets \"enter UEFI setup on next boot\" and hard-restarts the server\n(via Redfish). Only after confirmation.", "Prépare « démarrer dans la configuration UEFI au prochain démarrage » et redémarre le serveur\nde force (via Redfish). Seulement après confirmation.", "Merkt sich „bim nöchschte Start is UEFI-Setup“ und startet de Server hert neu\n(per Redfish). Nume mit Rückfrag."],
"Sendet die Taste an den Server. „Esc+1“ usw. ersetzen F1 usw. auf Konsolen,\ndie nur Escape-Folgen verstehen.": ["Sends the key to the server. \"Esc+1\" etc. replace F1 etc. on consoles\nthat only understand escape sequences.", "Envoie la touche au serveur. « Esc+1 » etc. remplacent F1 etc. sur les consoles\nqui ne comprennent que les séquences d'échappement.", "Schickt d Taschte an Server. „Esc+1“ usw. ersetzed F1 usw. uf Konsole,\ndie nume Escape-Folge verstönd."],
"Passwort kopieren (30 s)": ["Copy password (30 s)", "Copier le mot de passe (30 s)", "Passwort kopiere (30 s)"],
"Achtung: Tasten gehen direkt an den Server. Dieser Reiter ändert von sich aus nichts, alles Weitere geschieht in der UEFI-Oberfläche selbst.": ["Caution: keys go directly to the server. This tab changes nothing by itself; everything else happens in the UEFI interface itself.", "Attention : les touches vont directement au serveur. Cet onglet ne modifie rien de lui-même ; tout le reste se passe dans l'interface UEFI.", "Achtung: D Taschte gönd diräkt zum Server. Dä Reiter ändered vo sich us nüt, alles anderi gschiet i de UEFI-Oberflächi sälber."],
"Passwort liegt 30 Sekunden in der Zwischenablage.": ["The password stays in the clipboard for 30 seconds.", "Le mot de passe reste 30 secondes dans le presse-papiers.", "S Passwort liit 30 Sekunde i de Zwüscheablag."],
"Konsole": ["Console", "Console", "Konsole"],
"Getrennt.": ["Disconnected.", "Déconnecté.", "Trennt."],
"Ins UEFI-Setup booten": ["Boot into UEFI setup", "Démarrer dans la configuration UEFI", "Is UEFI-Setup boote"],
"Ein laufendes Betriebssystem wird ohne Herunterfahren beendet (wie die Reset-Taste), nicht gespeicherte Daten gehen verloren. Nur für Server ohne laufenden Betrieb verwenden.": ["A running operating system is terminated without shutting down (like the reset button); unsaved data is lost. Use only for servers that are not in operation.", "Un système d'exploitation en cours est arrêté sans extinction propre (comme le bouton reset) ; les données non enregistrées sont perdues. À utiliser uniquement sur des serveurs hors production.", "Es lauffends Betriebssystem wird ohni Abefahre beändet (wie d Reset-Taschte), nöd gspeicheret Date gönd verlore. Nume für Server ohni lauffende Betrieb bruuche."],
"Hart neu starten": ["Hard restart", "Redémarrer de force", "Hert neu starte"],
"Vorgemerkt, Server startet neu und landet im UEFI-Setup. Das Bild erscheint nach etwa ein bis zwei Minuten.": ["Scheduled; the server restarts and lands in UEFI setup. The picture appears after about one to two minutes.", "Préparé ; le serveur redémarre et arrive dans la configuration UEFI. L'image apparaît après une à deux minutes environ.", "Vorgmerkt, de Server startet neu und landet im UEFI-Setup. S Bild chunnt nach öppe ein bis zwei Minute."],
"Tasten:": ["Keys:", "Touches :", "Taschte:"],
"„Verbinden“ öffnet die Anmeldeseite des BMC im Standardbrowser. Die Anmeldung erfolgt dort von Hand;\ndie HTML5-Konsole wird im BMC über „Remote Console“ bzw. „Virtual Console“ gestartet.": ["\"Connect\" opens the BMC login page in the default browser. You log in there by hand;\nthe HTML5 console is started in the BMC via \"Remote Console\" or \"Virtual Console\".", "« Connecter » ouvre la page de connexion du BMC dans le navigateur par défaut. La connexion se fait à la main ;\nla console HTML5 se lance dans le BMC via « Remote Console » ou « Virtual Console ».", "„Verbinde“ macht d Aamäldsiite vom BMC im Standardbrowser uf. D Aamäldig machsch det vo Hand;\nd HTML5-Konsole wird im BMC über „Remote Console“ bzw. „Virtual Console“ gstartet."],
"Bitte eine Adresse eintragen.": ["Please enter an address.", "Veuillez saisir une adresse.", "Bitte e Adrässe iitrage."],
"QtWebEngine fehlt, siehe Hinweis unten. Alternativ „Text“ oder „Standardbrowser“ wählen.": ["QtWebEngine is missing, see the note below. Alternatively choose \"Text\" or \"default browser\".", "QtWebEngine est absent, voir l'indication ci-dessous. Sinon choisir « Texte » ou « navigateur par défaut ».", "QtWebEngine fählt, lueg dr Hiiwiis unde. Alternativ „Text“ oder „Standardbrowser“ wähle."],
"Dieser BMC kann nur TLS 1.0/1.1 (z. B. iLO 3/4, iDRAC7/8 mit alter Firmware). Die eingebettete Konsole geht damit nicht, bitte „Text“ wählen.": ["This BMC only speaks TLS 1.0/1.1 (e.g. iLO 3/4, iDRAC7/8 with old firmware). The embedded console cannot connect; please choose \"Text\".", "Ce BMC ne gère que TLS 1.0/1.1 (p. ex. iLO 3/4, iDRAC7/8 avec ancien firmware). La console intégrée ne fonctionne pas ; veuillez choisir « Texte ».", "Dä BMC cha nume TLS 1.0/1.1 (z. B. iLO 3/4, iDRAC7/8 mit alter Firmware). D iibettet Konsole goht demit nöd, bitte „Text“ wähle."],
"Anmeldeseite nicht erkannt. Bitte im Fenster von Hand anmelden.": ["Login page not recognised. Please log in by hand in the window.", "Page de connexion non reconnue. Veuillez vous connecter à la main dans la fenêtre.", "Aamäldsiite nöd erkannt. Bitte im Fenschter vo Hand aamälde."],
"Die Textkonsole läuft nur unter Linux.": ["The text console runs on Linux only.", "La console texte ne fonctionne que sous Linux.", "D Textkonsole laufft nume under Linux."],
"Für die Textkonsole fehlt „pyte“: sudo pacman -S python-pyte  (Arch/CachyOS) bzw. pip install pyte": ["The text console needs \"pyte\": sudo pacman -S python-pyte  (Arch/CachyOS) or pip install pyte", "La console texte nécessite « pyte » : sudo pacman -S python-pyte  (Arch/CachyOS) ou pip install pyte", "Für d Textkonsole fählt „pyte“: sudo pacman -S python-pyte  (Arch/CachyOS) bzw. pip install pyte"],
"Für diesen Hersteller gibt es kein SSH-Verfahren, bitte IPMI-SOL wählen.": ["There is no SSH method for this vendor, please choose IPMI-SOL.", "Il n'y a pas de méthode SSH pour ce fabricant, veuillez choisir IPMI-SOL.", "Für dä Hersteller gits kei SSH-Verfahre, bitte IPMI-SOL wähle."],
"Dieser Hersteller bzw. diese Generation kann das nicht per Redfish. Bitte im UEFI-Bild F2/F9/Entf selbst drücken.": ["This vendor or generation cannot do this via Redfish. Please press F2/F9/Del yourself in the console picture.", "Ce fabricant ou cette génération ne le permet pas via Redfish. Veuillez appuyer vous-même sur F2/F9/Suppr dans l'image de la console.", "Dä Hersteller bzw. die Generation cha das nöd per Redfish. Bitte im Konsolebild sälber F2/F9/Entf drücke."],
"Prüfe {0} …": ["Checking {0} …", "Vérification de {0} …", "Prüef {0} …"],
"Verbunden mit {0}. Anmeldung läuft; danach öffnet sich die Konsole.": ["Connected to {0}. Logging in; the console opens afterwards.", "Connecté à {0}. Connexion en cours ; la console s'ouvre ensuite.", "Verbunde mit {0}. D Aamäldig lauft; danach goht d Konsole uf."],
"Textkonsole verbunden ({0}). In das schwarze Feld klicken, dann tippen. Bleibt es schwarz, ist im UEFI die „Console Redirection“ aus oder die Größe falsch.": ["Text console connected ({0}). Click into the black area, then type. If it stays black, \"Console Redirection\" is off in the UEFI or the size is wrong.", "Console texte connectée ({0}). Cliquer dans la zone noire, puis taper. Si elle reste noire, « Console Redirection » est désactivé dans l'UEFI ou la taille est incorrecte.", "Textkonsole verbunde ({0}). is schwarz Fäld klicke, denn tippe. Bliibt s schwarz, isch im UEFI d „Console Redirection“ uus oder d Grössi falsch."],
"Textkonsole beendet (Code {0}).": ["Text console ended (code {0}).", "Console texte terminée (code {0}).", "Textkonsole beändet (Code {0})."],
"Server {0} wird jetzt HART neu gestartet.": ["Server {0} will now be HARD restarted.", "Le serveur {0} va maintenant être redémarré DE FORCE.", "De Server {0} wird jetzt HERT neu gstartet."],
"Sende an {0} …": ["Sending to {0} …", "Envoi à {0} …", "Schicke a {0} …"],
"Die eingebettete Web-Konsole braucht QtWebEngine. Installation auf dem Rechner mit diesem Tool:": ["The embedded web console needs QtWebEngine. Installation on the computer running this tool:", "La console web intégrée nécessite QtWebEngine. Installation sur l'ordinateur qui exécute cet outil :", "D iibettet Web-Konsole bruucht QtWebEngine. Installation uf em Rechner mit däm Tool:"],
"Es fehlt: {0}. Installation: sudo pacman -S {1}  (Arch/CachyOS)": ["Missing: {0}. Installation: sudo pacman -S {1}  (Arch/CachyOS)", "Manquant : {0}. Installation : sudo pacman -S {1}  (Arch/CachyOS)", "Es fählt: {0}. Installation: sudo pacman -S {1}  (Arch/CachyOS)"],
"Start ins UEFI-Setup vormerken": ["Schedule boot into UEFI setup", "Préparer le démarrage dans la configuration UEFI", "Start is UEFI-Setup vormerke"],
"Neustart auslösen": ["Trigger restart", "Déclencher le redémarrage", "Neustart uslöse"],
"Im Browser geöffnet: {0}": ["Opened in browser: {0}", "Ouvert dans le navigateur : {0}", "Im Browser ufgmacht: {0}"],
"Start fehlgeschlagen: {0}": ["Start failed: {0}", "Échec du démarrage : {0}", "Start fehlgschlage: {0}"],
"5 · UEFI-Konsole": ["5 · UEFI console", "5 · Console UEFI", "5 · UEFI-Konsole"],
"6 · Hilfe": ["6 · Help", "6 · Aide", "6 · Hilf"],
"Benötigte Pakete": ["Required packages", "Paquets requis", "Nötigi Pakete"],
"installiert": ["installed", "installé", "installiert"],
"nicht installiert, Paket: {0}": ["not installed, package: {0}", "non installé, paquet : {0}", "nöd installiert, Paket: {0}"],
"Fehlendes nachinstallieren, auf dem Rechner mit diesem Tool (Terminal):": ["Install what is missing, on the computer running this tool (terminal):", "Installer ce qui manque, sur l'ordinateur qui exécute cet outil (terminal) :", "Fählendi nachinstalliere, uf em Rechner mit däm Tool (Terminal):"],
"✔ installiert, ❗ fehlt. Je nach gewähltem Weg werden nicht alle gebraucht:\nBild: QtWebEngine · Text: pyte + ipmitool (Supermicro) bzw. ssh + sshpass (Dell/HPE/Lenovo).": ["✔ installed, ❗ missing. Not all are needed, depending on the chosen way:\nPicture: QtWebEngine · Text: pyte + ipmitool (Supermicro) or ssh + sshpass (Dell/HPE/Lenovo).", "✔ installé, ❗ manquant. Tous ne sont pas nécessaires selon la voie choisie :\nImage : QtWebEngine · Texte : pyte + ipmitool (Supermicro) ou ssh + sshpass (Dell/HPE/Lenovo).", "✔ installiert, ❗ fählt. Je nach gwählte Wääg bruucht me nöd alli:\nBild: QtWebEngine · Text: pyte + ipmitool (Supermicro) bzw. ssh + sshpass (Dell/HPE/Lenovo)."],
"2b · Bestandsserver": ["2b · Existing servers", "2b · Serveurs existants", "2b · Bestehendi Server"],
"Bestandsserver": ["Existing servers", "Serveurs existants", "Bestehendi Server"],
"Adresse (BMC)": ["Address (BMC)", "Adresse (BMC)", "Adrässe (BMC)"],
"Status": ["Status", "Statut", "Status"],
"wartet …": ["waiting …", "en attente …", "wartet …"],
"OK": ["OK", "OK", "OK"],
"Fehler": ["Error", "Erreur", "Fähler"],
"kein Admin": ["not admin", "pas admin", "kei Admin"],
"Verbindung testen": ["Test connection", "Tester la connexion", "Verbindig teste"],
"In Reiter 2–4 übernehmen": ["Use in tabs 2–4", "Reprendre dans les onglets 2–4", "I Reiter 2–4 übernäh"],
"Aus Serverliste holen": ["Get from server list", "Prendre dans la liste", "Us de Serverlischte hole"],
"Für alle Zeilen übernehmen": ["Apply to all rows", "Appliquer à toutes les lignes", "Für alli Zile übernäh"],
"Bereich einfügen": ["Insert range", "Insérer la plage", "Bereich iifüege"],
"Zugangsdaten und Adressen schnell eintragen": ["Enter credentials and addresses quickly", "Saisie rapide des accès et adresses", "Zuegangsdate und Adrässe schnäll iitrage"],
"Für Server, die schon konfiguriert sind: erst die Verbindung prüfen, dann die erfolgreichen Server in die Reiter 2–4 übernehmen. Das Tool speichert nichts.": ["For servers that are already configured: check the connection first, then use the successful servers in tabs 2–4. The tool stores nothing.", "Pour les serveurs déjà configurés : vérifier d'abord la connexion, puis reprendre les serveurs réussis dans les onglets 2–4. L'outil n'enregistre rien.", "Für Server, wo scho konfiguriert sind: zerscht d Verbindig prüefe, dänn d erfolgriichä Server i d Reiter 2–4 übernäh. S Tool speicheret nüt."],
"— Verbindungstest, {0} Server, nur lesend —": ["— Connection test, {0} servers, read-only —", "— Test de connexion, {0} serveurs, lecture seule —", "— Verbindigstest, {0} Server, nume läse —"],
"Fertig: {0} von {1} Servern erreichbar und angemeldet": ["Done: {0} of {1} servers reachable and logged in", "Terminé : {0} serveurs sur {1} joignables et connectés", "Fertig: {0} vo {1} Server erreichbar und aagmäldet"],
"Port {0} auf {1} nicht erreichbar ({2})": ["Port {0} on {1} not reachable ({2})", "Port {0} sur {1} injoignable ({2})", "Port {0} uf {1} nöd erreichbar ({2})"],
"  Port {0}: offen": ["Port {0}: open", "Port {0} : ouvert", "Port {0}: offe"],
"  Anmeldung: ok": ["Login: ok", "Connexion : ok", "Aamäldig: ok"],
"  BMC: {0}, Firmware {1}": ["BMC: {0}, firmware {1}", "BMC : {0}, firmware {1}", "BMC: {0}, Firmware {1}"],
"  Server: {0}, Stromzustand {1}": ["Server: {0}, power state {1}", "Serveur : {0}, état d'alimentation {1}", "Server: {0}, Stromzuestand {1}"],
"  Verschlüsselung: {0}": ["Encryption: {0}", "Chiffrement : {0}", "Verschlüsselig: {0}"],
"TLS 1.2 oder neuer": ["TLS 1.2 or newer", "TLS 1.2 ou plus récent", "TLS 1.2 oder neuer"],
"veraltet (TLS 1.0/1.1 möglich)": ["outdated (TLS 1.0/1.1 possible)", "obsolète (TLS 1.0/1.1 possible)", "veralteti (TLS 1.0/1.1 möglich)"],
"nur TLS 1.0/1.1": ["TLS 1.0/1.1 only", "TLS 1.0/1.1 uniquement", "nume TLS 1.0/1.1"],
"Anmeldung abgelehnt (Benutzer oder Passwort falsch)": ["Login rejected (wrong user or password)", "Connexion refusée (utilisateur ou mot de passe incorrect)", "Aamäldig abglehnt (Benutzer oder Passwort falsch)"],
"Anmeldung abgelehnt (Benutzer oder Passwort falsch, oder Konto gesperrt)": ["Login rejected (wrong user or password, or account locked)", "Connexion refusée (utilisateur ou mot de passe incorrect, ou compte verrouillé)", "Aamäldig abglehnt (Benutzer oder Passwort falsch, oder Konto gsperrt)"],
"  Hinweis: Das Konto hat keine Administratorrechte, Konfigurieren wird scheitern.": ["Note: the account has no administrator rights, configuring will fail.", "Remarque : le compte n'a pas de droits d'administrateur, la configuration échouera.", "Hiiwiis: S Konto het kei Administratorrächt, s Konfiguriere wird schiitere."],
"Noch kein erfolgreich getesteter Server. Erst „Verbindung testen“.": ["No successfully tested server yet. Run “Test connection” first.", "Aucun serveur testé avec succès. Lancez d'abord « Tester la connexion ».", "No kei erfolgriich tested Server. Zerscht „Verbindig teste“."],
"Die Liste ist leer.": ["The list is empty.", "La liste est vide.", "D Lischte isch leer."],
"Passwort fehlt": ["password missing", "mot de passe manquant", "Passwort fehlt"],
"z. B. 10.20.0.11": ["e.g. 10.20.0.11", "p. ex. 10.20.0.11", "z. B. 10.20.0.11"],
"z. B. 10.20.0.30": ["e.g. 10.20.0.30", "p. ex. 10.20.0.30", "z. B. 10.20.0.30"]
}

HELP = {
"de": "<h2>BMC-Steward</h2>\n<p>Der BMC-Steward bedient jeden Server der Reihe nach: Er legt den neuen Administrator an, setzt den\nHostnamen und stellt den BMC auf eine feste Adresse um. Die Server müssen nicht alle auf einmal\neingetragen werden, sie können nach und nach dazukommen.</p>\n\n<h2>Schutz vor Fehlbedienung</h2>\n<p>Das Tool ist so gebaut, dass ein Fehlgriff möglichst nichts kaputt macht:</p>\n<ul>\n<li><b>Trockenlauf ist voreingestellt.</b> Solange der Haken gesetzt ist, wird nichts geändert.</li>\n<li><b>Jeder scharfe Lauf wird vorher gezeigt</b> (alte IP → neue IP, Hostname). In allen Abfragen ist\n    „Abbrechen“ vorausgewählt, Enter bricht also ab.</li>\n<li><b>Ungewöhnliche Fälle verlangen das Eintippen der Server-Anzahl</b>, z. B. wenn ein Gerät nicht im\n    eigenen Staging-Netz liegt, und immer bei UEFI-Änderungen.</li>\n<li><b>Adressen werden geprüft:</b> doppelte IPs oder Hostnamen, Gateway außerhalb des Netzes, eine\n    Adresse dieses Rechners, eine Adresse aus dem laufenden DHCP-Bereich und die aktuelle IP eines\n    anderen Geräts werden abgelehnt. Vor dem Umstellen prüft das Tool außerdem, ob unter der neuen IP\n    schon ein Gerät antwortet, und lässt das Gerät dann unverändert.</li>\n<li><b>Dell:</b> Ein schon belegter User-Slot wird nie überschrieben.</li>\n<li><b>Das neue Passwort wird zweimal eingegeben.</b> Stimmen die Eingaben nicht überein, startet kein\n    Lauf.</li>\n<li><b>Der Adresswechsel wird geprüft:</b> Reißt beim Umstellen nur die Verbindung ab, gilt ein Gerät\n    erst als fertig, wenn es unter der alten Adresse wirklich nicht mehr antwortet.</li>\n<li><b>DHCP-Server:</b> startet nicht auf einer Karte mit öffentlicher Adresse, mit der Standardroute\n    (Internet/Firmennetz), mit einer per DHCP bezogenen Adresse oder wenn dort schon ein DHCP-Server\n    antwortet. Er stoppt von selbst, wenn die Karte später zur Standardroute wird. Geräte, die ihre\n    Adresse von einem anderen Server haben, werden nie abgewiesen. Ein nach einem Absturz\n    übrig gebliebener DHCP-Server wird beim nächsten Start beendet.</li>\n<li><b>UEFI:</b> Passwort-, TPM- und Lösch-Attribute sind gesperrt. Werte, die nicht zum Typ passen,\n    werden nicht gesetzt. Ein Neustart ist nicht vorausgewählt.</li>\n<li><b>Passwörter</b> bleiben im Arbeitsspeicher und landen nur auf ausdrücklichen Wunsch in einer\n    Datei.</li>\n</ul>\n\n<h2>Ablauf</h2>\n<ol>\n<li><b>DHCP-Server</b> (Reiter 1): Die Netzwerkkarte am getrennten Staging-Switch braucht eine feste\n    Adresse ohne Gateway. DHCP starten, die Geräte holen sich eine Adresse und erscheinen in der Liste,\n    von dort „In Serverliste übernehmen“. Unter Linux läuft dafür dnsmasq, unter Windows ein\n    eingebauter Server.</li>\n<li><b>Serverliste</b> (Reiter 2): neue IP, Subnetzmaske, Gateway und Hostname eintragen. Was noch fehlt,\n    ist <b>gelb</b> markiert, Fehlerhaftes <b>rot</b>, Fertiges <b>grün</b>. Über dem Start-Knopf steht in\n    einer Zeile, was vor dem Lauf noch auszufüllen ist; der Grund zu jedem Feld steht im Tooltip.</li>\n<li><b>Trockenlauf</b>: zeigt im Protokoll, was gesendet würde.</li>\n<li><b>Scharfer Lauf</b>: Haken bei „Trockenlauf“ entfernen. Der Start-Knopf wird rot und heißt dann\n    „Scharf starten“. Klicken und die Abfrage bestätigen.</li>\n<li><b>Kontrolle</b>: Am Ende prüft das Tool, ob jede neue Adresse mit dem neuen Benutzer antwortet. Das\n    klappt nur, wenn dieser Rechner das neue Netz erreicht; sonst steht dort „FEHLT“, obwohl die\n    Umstellung geklappt haben kann.</li>\n</ol>\n\n<h2>Server nacheinander konfigurieren</h2>\n<ul>\n<li>Man kann einen Server konfigurieren, Start drücken, den nächsten anschließen und wieder Start\n    drücken, beliebig oft. Der DHCP-Server läuft dabei weiter.</li>\n<li>Erfolgreich umgestellte Zeilen werden <b>grün</b> markiert, bleiben in der Liste stehen und werden\n    beim nächsten Start übersprungen. Fehlgeschlagene werden erneut versucht.</li>\n<li>Soll ein grünes Gerät noch einmal bearbeitet werden: Zeile markieren, „Erneut bearbeiten“ und die\n    „Aktuelle IP“ auf seine jetzige Adresse setzen.</li>\n<li>Der Trockenlauf markiert nie etwas als fertig.</li>\n<li>„Liste speichern“ schreibt die grüne Markierung mit in die Datei (Spalte „fertig“), nach\n    „Liste laden“ sind die Zeilen wieder grün. So lässt sich ein Rollout auch an einem anderen Tag\n    fortsetzen.</li>\n</ul>\n\n<h2>Profile</h2>\n<ul>\n<li>Ein Profil merkt sich die Einstellungen der Maske: Hersteller, Werksbenutzer, neuen Administrator,\n    User-Slot, Vorgaben für neue Zeilen und die DHCP-Einstellungen. Passwörter und die Serverliste\n    gehören nie dazu.</li>\n<li>Die Profile liegen in <code>bmc-profile.json</code> neben dem Tool; beim Start werden das zuletzt\n    genutzte Profil und die zuletzt gewählte Sprache geladen.</li>\n</ul>\n\n<h2>UEFI-Einstellungen (Reiter 3)</h2>\n<ul>\n<li>UEFI-Attribute unterscheiden sich je Modell. Das Tool liest deshalb die tatsächlich vorhandenen\n    Attribute von einem <b>Referenzgerät</b>, statt sie zu raten. Lesen ändert nichts.</li>\n<li><b>Ändern:</b> bei den gewünschten Zeilen „Neuer Wert“ ausfüllen, die Suche grenzt die Liste ein.\n    Leere Felder bleiben unverändert. Graue Zeilen (Passwörter, TPM, Löschfunktionen) sind gesperrt.</li>\n<li><b>Anwenden:</b> „Auf allen Servern der Liste setzen“ nutzt die Serverliste aus Reiter 2; grüne\n    Zeilen werden unter ihrer neuen IP angesprochen. Jedes Gerät bekommt nur Attribute, die es auch\n    hat. Werte, die schon stimmen, werden übersprungen.</li>\n<li><b>Neustart:</b> UEFI-Werte wirken erst nach einem Neustart. Mit dem Haken „Danach hart neu\n    starten“ wird ein laufender Server hart neu gestartet (wie mit der Reset-Taste), ein\n    ausgeschalteter eingeschaltet. Nur bei Servern ohne laufendes Betriebssystem verwenden.</li>\n<li><b>UEFI-Vorlagen</b> merken sich Hersteller und „Neuer Wert“-Felder und liegen getrennt in\n    <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Dienste: NTP, Syslog, LDAP, Zertifikat (Reiter 4)</h2>\n<p>Reiter 4 richtet auf allen Servern der Liste gemeinsame Verwaltungsdienste ein. Nur angehakte\nAbschnitte werden angewendet; grüne (fertige) Server werden unter ihrer neuen Adresse angesprochen.\nAngemeldet wird mit dem Werksbenutzer, der Werks-Administrator bleibt bestehen. Der Trockenlauf ist\nvoreingestellt und zeigt nur, was gesendet würde; Passwörter und der Zertifikatsinhalt stehen dabei\nnicht im Protokoll.</p>\n<ul>\n<li><b>NTP:</b> ein bis zwei Zeitserver. Die Zeitzone wird nur bei Dell gesetzt; bei HPE, Supermicro\n    und Lenovo bitte in der Oberfläche wählen.</li>\n<li><b>Syslog:</b> nur Dell und HPE. Bei Supermicro und Lenovo gibt es keinen einheitlichen Weg, der\n    Abschnitt wird dort übersprungen.</li>\n<li><b>LDAP / Active Directory:</b> Server, Base-DN, optional ein Bind-Konto, Benutzer- und\n    Gruppen-Attribut. Unter „Gruppen → Rolle“ werden LDAP-Gruppen den Rollen Administrator, Operator\n    oder ReadOnly zugeordnet. Funktioniert bei allen vier Herstellern über den Standard-Redfish-Weg.</li>\n<li><b>HTTPS-Zertifikat:</b> importiert ein von einer CA signiertes PEM-Zertifikat. Das ist\n    experimentell und je Hersteller verschieden. Bei HPE muss der passende Schlüssel aus einer auf dem\n    iLO erzeugten Zertifikatsanforderung (CSR) stammen, sonst scheitert der Import. Beim Import kann der\n    BMC neu starten.</li>\n</ul>\n<p>Für jeden Dienst gilt: Ein Hersteller, der ihn nicht kennt, wird übersprungen, nicht geraten.\niLO 3/4 (über RIBCL) werden hier nicht bedient. Die Abläufe sind bisher nur gegen simulierte\nGegenstellen geprüft; vor einem großen Lauf immer zuerst ein einzelnes Gerät.</p>\n<h2>Hersteller im Vergleich</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Werksbenutzer</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Werkspasswort</b></td><td>calvin oder individuell (Service-Tag-Auszug)</td><td>individuell,\n    Anhänger am Gerät</td><td>individuell auf dem Aufkleber, alte Boards ADMIN</td><td>individuell,\n    Anhänger am Gerät</td></tr>\n<tr><td><b>Neuer Benutzer</b></td><td>in den User-Slot, nur wenn frei</td><td>neu angelegt</td>\n    <td>neu angelegt</td><td>neu angelegt</td></tr>\n<tr><td><b>Adresswechsel</b></td><td>sofort</td><td>nach iLO-Neustart (löst das Tool aus)</td>\n    <td>sofort</td><td>sofort</td></tr>\n<tr><td><b>Bis erreichbar</b></td><td>Sekunden</td><td>1 bis 3 Minuten</td><td>Sekunden</td>\n    <td>Sekunden</td></tr>\n</table>\n<p>Bleibt „Benutzer“ beim aktuellen Login leer, wird je Hersteller der Werksbenutzer verwendet; so gehen\nauch gemischte Listen. „Automatisch erkennen“ ermittelt den Hersteller pro Gerät, eine feste Wahl gilt\nfür alle Zeilen.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>User-Slot:</b> Standard 3 (Slot 2 ist root). Ist der Slot von einem anderen Benutzer belegt,\n    bricht das Tool für dieses Gerät ab und ändert nichts.</li>\n<li><b>Hostname:</b> gesetzt wird der DNS-Name des iDRAC, nicht der des Betriebssystems.</li>\n<li><b>Auslieferung:</b> je nach Bestellung DHCP oder feste Adresse 192.168.0.120. Mit fester Adresse\n    erscheint der iDRAC nicht in der DHCP-Liste; mehrere solche Geräte am selben Switch kollidieren,\n    dann einzeln anschließen.</li>\n<li><b>Generationen:</b> iDRAC9 (14. bis 16. Generation) und iDRAC7/8 (12. und 13. Generation). iDRAC7/8\n    brauchen mindestens Firmware 2.50.50.50; ältere erkennt das Tool, meldet es und ändert nichts.\n    iDRAC10 zuerst an einem Gerät testen. iDRAC6 wird nicht unterstützt.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Passwort pro Gerät:</b> steht auf dem Anhänger, je Zeile in die Spalte „Passwort“ eintragen.</li>\n<li><b>Zuordnung:</b> in der DHCP-Liste meldet sich ein iLO meist als „ILO“ plus Seriennummer.</li>\n<li><b>Neustart:</b> Hostname und Adresse werden erst nach dem iLO-Neustart aktiv; der Server selbst\n    läuft weiter.</li>\n<li><b>Port:</b> konfiguriert wird der dedizierte iLO-Port. Neue Passwörter brauchen mindestens 8\n    Zeichen.</li>\n<li><b>Generationen:</b> iLO 5 und 6 (Gen10 bis Gen11) über Redfish. iLO 7 ist nicht geprüft. Für iLO 3\n    und 4 siehe den nächsten Abschnitt.</li>\n</ul>\n\n<h2>HPE iLO 3 und iLO 4 (ältere Server)</h2>\n<ul>\n<li><b>Geräte:</b> iLO 3 steckt in G7-Servern, iLO 4 in Gen8 und Gen9. Das Tool erkennt sie von selbst;\n    wer den Hersteller fest wählt, nimmt „HPE iLO 3/4“.</li>\n<li><b>Anderer Weg:</b> iLO 3 kennt kein Redfish. Das Tool nutzt deshalb für iLO 3 und iLO 4 die\n    XML-Schnittstelle RIBCL, die beide ab Werk mitbringen. Im Trockenlauf stehen die XML-Befehle im\n    Protokoll, Passwörter sind dort durch *** ersetzt.</li>\n<li><b>Neustart:</b> Nach dem Setzen von Adresse und Hostname startet das iLO von selbst neu; der Server\n    läuft weiter. Bis es unter der neuen Adresse antwortet, vergehen 1 bis 3 Minuten.</li>\n<li><b>Alte Verschlüsselung:</b> iLO 3 kann nur TLS 1.0/1.1. Das Tool schaltet dafür je Gerät von selbst\n    auf die alte Verschlüsselung um und schreibt einen Hinweis ins Protokoll. Solche Geräte gehören in\n    ein abgeschottetes Verwaltungsnetz.</li>\n<li><b>Grenzen:</b> Hostname höchstens 49 Zeichen. UEFI-Einstellungen (Reiter 3) gehen bei iLO 3 nicht.\n    iLO 2 und älter werden nicht unterstützt.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Passwort pro Gerät:</b> steht auf dem Aufkleber, je Zeile eintragen.</li>\n<li><b>Zugriffsarten:</b> das Tool vergibt Web, IPMI und Konsole mit. Steht im Protokoll „ohne\n    Zugriffsarten angelegt“, die Web-Anmeldung einmal prüfen.</li>\n<li><b>Lizenz:</b> bei älteren Boards können Redfish-Funktionen eine Lizenz brauchen.</li>\n<li><b>Nutanix NX</b> wird als Supermicro erkannt.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Passwort pro Gerät:</b> steht auf dem Anhänger, je Zeile eintragen.</li>\n<li><b>Werksbenutzer:</b> USERID in Großbuchstaben.</li>\n<li><b>Port:</b> konfiguriert wird die Schnittstelle „NIC“, der dedizierte XCC-Port.</li>\n<li><b>Generationen:</b> XCC2/XCC3. XCC1 zuerst an einem Gerät testen.</li>\n</ul>\n\n<h2>Nutanix-Nodes (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>Das Tool setzt nur BMC-Adresse und BMC-Benutzer, nicht Hypervisor oder CVM.</li>\n<li>Nur <b>vor</b> der Cluster-Erstellung so umstellen. Bei einem laufenden Cluster das\n    Nutanix-Verfahren nutzen, weil der Cluster die BMC-Daten selbst verwendet.</li>\n</ul>\n\n<h2>Wenn etwas nicht klappt</h2>\n<ul>\n<li><b>DHCP startet nicht:</b> Die Meldung nennt den Grund. Meist hat die Karte ein Gateway oder ihre\n    Adresse per DHCP. Unter Linux z. B.:\n    <code>nmcli con add type ethernet ifname &lt;karte&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>Keine Geräte in der DHCP-Liste:</b> Firewall (Linux mit ufw: UDP 67 freigeben; Windows: die\n    Firewall-Abfrage zulassen) oder der BMC steht nicht auf DHCP.</li>\n<li><b>Port 67 belegt (Windows):</b> meist Internetverbindungsfreigabe oder mobiler Hotspot.</li>\n<li><b>HTTP 401:</b> Benutzer oder Passwort falsch. Feld „Benutzer“ leeren, Passwort der Zeile\n    prüfen.</li>\n<li><b>Hersteller nicht erkannt:</b> Das Gerät antwortet weder per Redfish noch als iLO 3/4. iDRAC6,\n    iLO 2 und sehr alte Firmware werden nicht unterstützt. Ein iLO 3/4, bei dem die Geräteauskunft\n    abgeschaltet wurde, lässt sich über die feste Wahl „HPE iLO 3/4“ trotzdem ansprechen.</li>\n<li><b>Benutzer abgelehnt:</b> meist Passwortregeln. Die Meldung des BMC steht unter der\n    Fehlerzeile.</li>\n<li><b>„neue IP ist schon vergeben“:</b> Unter der Adresse antwortet bereits ein Gerät. Andere Adresse\n    wählen.</li>\n<li><b>Liste aus Excel oder einem anderen Tool:</b> Ohne Spalte „fertig“ sind alle Zeilen offen. Schon\n    umgestellte Geräte sind unter der alten Adresse nicht erreichbar, diese Zeilen entfernen oder\n    anpassen.</li>\n</ul>\n\n<h2>Daten und Grenzen</h2>\n<ul>\n<li>Das Tool speichert nichts von selbst. „Liste speichern“ fragt, ob Passwörter mit in die Datei\n    sollen (dann im Klartext). Eine solche Datei nach dem Rollout löschen.</li>\n<li>Die Abläufe für HPE (auch iLO 3/4), ältere iDRAC7/8, Supermicro, Lenovo und UEFI sowie der\n    Windows-DHCP-Server sind bisher nur gegen simulierte Gegenstellen geprüft. Vor einem großen Lauf immer zuerst ein einzelnes Gerät\n    konfigurieren.</li>\n<li>„Protokoll speichern“ schreibt das Protokoll des letzten Laufs in eine Textdatei, z. B. für die\n    Dokumentation. Passwörter stehen nicht darin.</li>\n<li>Die Sprache lässt sich oben rechts umstellen, solange kein Lauf aktiv ist und der DHCP-Server\n    gestoppt ist.</li>\n</ul>\n",
"en": "<h2>BMC-Steward</h2>\n<p>BMC-Steward serves one server after another: it creates the new administrator, sets the hostname and\nswitches the BMC to a static address. You don't have to enter all servers at once; they can be added\none by one.</p>\n\n<h2>Protection against mistakes</h2>\n<p>The tool is built so that a slip does as little damage as possible:</p>\n<ul>\n<li><b>Dry run is the default.</b> As long as it is ticked, nothing is changed.</li>\n<li><b>Every live run is shown first</b> (old IP → new IP, hostname). “Cancel” is preselected in every\n    prompt, so pressing Enter cancels.</li>\n<li><b>Unusual cases require typing the number of servers</b>, e.g. when a device is not in the\n    computer's own staging network, and always for UEFI changes.</li>\n<li><b>Addresses are checked:</b> duplicate IPs or hostnames, a gateway outside the network, an address\n    of this computer, an address from the running DHCP range and the current IP of another device are\n    rejected. Before reconfiguring, the tool also checks whether a device already answers on the new IP\n    and, if so, leaves the device unchanged.</li>\n<li><b>Dell:</b> an occupied user slot is never overwritten.</li>\n<li><b>The new password is entered twice.</b> If the entries differ, no run starts.</li>\n<li><b>The address change is verified:</b> if only the connection drops during the change, a device\n    counts as done only once it really no longer answers on the old address.</li>\n<li><b>DHCP server:</b> does not start on a card with a public address, with the default route\n    (internet/company network), with an address obtained via DHCP, or when a DHCP server already\n    answers there. It stops by itself if the card later becomes the default route. Devices that got\n    their address from another server are never refused. A DHCP server left over after a crash is\n    stopped at the next start.</li>\n<li><b>UEFI:</b> password, TPM and erase attributes are locked. Values that don't match the type are\n    not set. A restart is not preselected.</li>\n<li><b>Passwords</b> stay in memory and only go into a file if you explicitly ask for it.</li>\n</ul>\n\n<h2>Procedure</h2>\n<ol>\n<li><b>DHCP server</b> (tab 1): the network card on the separate staging switch needs a static address\n    without a gateway. Start DHCP; the devices get an address and appear in the list. From there,\n    “Add to server list”. On Linux dnsmasq is used, on Windows a built-in server.</li>\n<li><b>Server list</b> (tab 2): enter new IP, subnet mask, gateway and hostname. Whatever is still\n    missing is marked <b>yellow</b>, wrong entries <b>red</b>, finished ones <b>green</b>. A line above the\n    Start button says what still has to be filled in; the tooltip of each field gives the reason.</li>\n<li><b>Dry run</b>: the log shows what would be sent.</li>\n<li><b>Live run</b>: untick “Dry run”. The Start button turns red and reads “Start live run”. Click it\n    and confirm the prompt.</li>\n<li><b>Check</b>: at the end the tool checks whether each new address answers with the new user. That\n    only works if this computer can reach the new network; otherwise it shows “MISSING” even though\n    the change may have worked.</li>\n</ol>\n\n<h2>Configuring servers one after another</h2>\n<ul>\n<li>You can configure one server, press Start, connect the next one and press Start again, as often as\n    you like. The DHCP server keeps running.</li>\n<li>Successfully reconfigured rows turn <b>green</b>, stay in the list and are skipped at the next\n    start. Failed ones are tried again.</li>\n<li>To process a green device again: select the row, click “Process again” and set “Current IP” to its\n    present address.</li>\n<li>A dry run never marks anything as done.</li>\n<li>“Save list” writes the green mark into the file as well (column “fertig”); after “Load list” the\n    rows are green again. This lets you continue a rollout on another day.</li>\n</ul>\n\n<h2>Profiles</h2>\n<ul>\n<li>A profile remembers the settings of the form: vendor, factory user, new administrator, user slot,\n    defaults for new rows and the DHCP settings. Passwords and the server list are never included.</li>\n<li>Profiles are stored in <code>bmc-profile.json</code> next to the tool; at start-up the last used\n    profile and the last chosen language are loaded.</li>\n</ul>\n\n<h2>UEFI settings (tab 3)</h2>\n<ul>\n<li>UEFI attributes differ per model. The tool therefore reads the attributes that actually exist from\n    a <b>reference device</b> instead of guessing. Reading changes nothing.</li>\n<li><b>Change:</b> fill in “New value” in the rows you want; the search narrows the list. Empty fields\n    stay unchanged. Grey rows (passwords, TPM, erase functions) are locked.</li>\n<li><b>Apply:</b> “Set on all servers in the list” uses the server list from tab 2; green rows are\n    addressed at their new IP. Each device only gets attributes it actually has. Values that are\n    already correct are skipped.</li>\n<li><b>Restart:</b> UEFI values only take effect after a restart. With “Then hard restart” ticked, a\n    running server is hard restarted (like pressing reset) and a powered-off one is switched on. Use\n    only for servers without a running operating system.</li>\n<li><b>UEFI templates</b> remember the vendor and the “New value” fields and are stored separately in\n    <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Services: NTP, syslog, LDAP, certificate (tab 4)</h2>\n<p>Tab 4 sets up shared management services on every server in the list. Only ticked sections are\napplied; green (finished) servers are reached at their new address. Login uses the factory user and the\nfactory administrator stays in place. The dry run is the default and only shows what would be sent;\npasswords and the certificate content do not appear in the log.</p>\n<ul>\n<li><b>NTP:</b> one or two time servers. The time zone is set on Dell only; on HPE, Supermicro and\n    Lenovo please choose it in the web interface.</li>\n<li><b>Syslog:</b> Dell and HPE only. There is no uniform way on Supermicro and Lenovo, so the section\n    is skipped there.</li>\n<li><b>LDAP / Active Directory:</b> server, base DN, an optional bind account, user and group attribute.\n    Under “Groups → role” LDAP groups are mapped to the roles Administrator, Operator or ReadOnly. Works\n    on all four vendors through the standard Redfish path.</li>\n<li><b>HTTPS certificate:</b> imports a CA-signed PEM certificate. This is experimental and\n    vendor-specific. On HPE the matching key must come from a CSR generated on the iLO, otherwise the\n    import fails. The BMC may restart during the import.</li>\n</ul>\n<p>For each service a vendor that does not know it is skipped, not guessed. iLO 3/4 (via RIBCL) are not\nhandled here. The procedures have so far only been tested against simulated counterparts; always\nconfigure a single device first before a large run.</p>\n<h2>Vendors compared</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Factory user</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Factory password</b></td><td>calvin or individual (service tag sheet)</td><td>individual,\n    tag on the device</td><td>individual on the sticker, old boards ADMIN</td><td>individual, tag on\n    the device</td></tr>\n<tr><td><b>New user</b></td><td>into the user slot, only if free</td><td>created</td><td>created</td>\n    <td>created</td></tr>\n<tr><td><b>Address change</b></td><td>immediate</td><td>after iLO restart (triggered by the tool)</td>\n    <td>immediate</td><td>immediate</td></tr>\n<tr><td><b>Until reachable</b></td><td>seconds</td><td>1 to 3 minutes</td><td>seconds</td>\n    <td>seconds</td></tr>\n</table>\n<p>If “User” in the current login stays empty, the vendor's factory user is used, so mixed lists work.\n“Detect automatically” determines the vendor per device; a fixed choice applies to all rows.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>User slot:</b> default 3 (slot 2 is root). If another user occupies the slot, the tool stops for\n    this device and changes nothing.</li>\n<li><b>Hostname:</b> the iDRAC DNS name is set, not the operating system's.</li>\n<li><b>As delivered:</b> DHCP or static 192.168.0.120, depending on the order. With a static address the\n    iDRAC does not appear in the DHCP list; several such devices on one switch collide, so connect\n    them one at a time.</li>\n<li><b>Generations:</b> iDRAC9 (14th to 16th generation) and iDRAC7/8 (12th and 13th generation). iDRAC7/8\n    need at least firmware 2.50.50.50; the tool detects older firmware, reports it and changes nothing.\n    Test iDRAC10 on one device first. iDRAC6 is not supported.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Password per device:</b> on the tag; enter it per row in the “Password” column.</li>\n<li><b>Identification:</b> in the DHCP list an iLO usually reports as “ILO” plus serial number.</li>\n<li><b>Restart:</b> hostname and address only become active after the iLO restart; the server itself\n    keeps running.</li>\n<li><b>Port:</b> the dedicated iLO port is configured. New passwords need at least 8 characters.</li>\n<li><b>Generations:</b> iLO 5 and 6 (Gen10 to Gen11) via Redfish. iLO 7 is untested. For iLO 3 and 4 see\n    the next section.</li>\n</ul>\n\n<h2>HPE iLO 3 and iLO 4 (older servers)</h2>\n<ul>\n<li><b>Devices:</b> iLO 3 is found in G7 servers, iLO 4 in Gen8 and Gen9. The tool detects them by\n    itself; if you pick the vendor by hand, choose “HPE iLO 3/4”.</li>\n<li><b>A different route:</b> iLO 3 has no Redfish. For iLO 3 and iLO 4 the tool therefore uses the RIBCL\n    XML interface, which both ship with. In a dry run the XML commands appear in the log, with\n    passwords replaced by ***.</li>\n<li><b>Restart:</b> after the address and hostname are set, the iLO restarts by itself; the server keeps\n    running. It takes 1 to 3 minutes until it answers on the new address.</li>\n<li><b>Old encryption:</b> iLO 3 only speaks TLS 1.0/1.1. The tool switches to the old encryption per\n    device by itself and writes a note to the log. Such devices belong in an isolated management\n    network.</li>\n<li><b>Limits:</b> hostname at most 49 characters. UEFI settings (tab 3) do not work with iLO 3. iLO 2\n    and older are not supported.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Password per device:</b> on the sticker; enter it per row.</li>\n<li><b>Access types:</b> the tool also grants web, IPMI and console. If the log says “created without\n    access types”, check the web login once.</li>\n<li><b>Licence:</b> on older boards some Redfish functions may need a licence.</li>\n<li><b>Nutanix NX</b> is detected as Supermicro.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Password per device:</b> on the tag; enter it per row.</li>\n<li><b>Factory user:</b> USERID in capitals.</li>\n<li><b>Port:</b> the “NIC” interface, i.e. the dedicated XCC port, is configured.</li>\n<li><b>Generations:</b> XCC2/XCC3. Test XCC1 on one device first.</li>\n</ul>\n\n<h2>Nutanix nodes (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>The tool only sets the BMC address and BMC user, not the hypervisor or CVM.</li>\n<li>Only change these <b>before</b> creating the cluster. On a running cluster use the Nutanix\n    procedure, because the cluster uses the BMC data itself.</li>\n</ul>\n\n<h2>Troubleshooting</h2>\n<ul>\n<li><b>DHCP does not start:</b> the message gives the reason. Usually the card has a gateway or got its\n    address via DHCP. On Linux, for example:\n    <code>nmcli con add type ethernet ifname &lt;card&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>No devices in the DHCP list:</b> firewall (Linux with ufw: allow UDP 67; Windows: allow the\n    firewall prompt) or the BMC is not set to DHCP.</li>\n<li><b>Port 67 in use (Windows):</b> usually Internet Connection Sharing or a mobile hotspot.</li>\n<li><b>HTTP 401:</b> wrong user or password. Clear “User”, check the row's password.</li>\n<li><b>Vendor not recognised:</b> the device answers neither via Redfish nor as an iLO 3/4. iDRAC6, iLO 2\n    and very old firmware are not supported. An iLO 3/4 whose device information has been switched off\n    can still be addressed by choosing “HPE iLO 3/4” by hand.</li>\n<li><b>User refused:</b> usually password rules. The BMC's message appears below the error line.</li>\n<li><b>“new IP is already taken”:</b> a device already answers on that address. Choose another one.</li>\n<li><b>List from Excel or another tool:</b> without the “fertig” column all rows are open. Devices\n    already reconfigured cannot be reached at their old address; remove or adjust those rows.</li>\n</ul>\n\n<h2>Data and limits</h2>\n<ul>\n<li>The tool saves nothing on its own. “Save list” asks whether passwords go into the file (then in plain\n    text). Delete such a file after the rollout.</li>\n<li>The procedures for HPE (including iLO 3/4), older iDRAC7/8, Supermicro, Lenovo and UEFI and the\n    Windows DHCP server have so far only been tested against simulated counterparts. Always configure a single device first before a large\n    run.</li>\n<li>“Save log” writes the log of the last run to a text file, e.g. for documentation. It contains no\n    passwords.</li>\n<li>The language can be changed at the top right while no run is active and the DHCP server is\n    stopped.</li>\n</ul>\n",
"fr": "<h2>BMC-Steward</h2>\n<p>BMC-Steward sert les serveurs l'un après l'autre : il crée le nouvel administrateur, définit le nom\nd'hôte et passe le BMC sur une adresse fixe. Inutile de saisir tous les serveurs d'un coup, ils peuvent\nêtre ajoutés au fur et à mesure.</p>\n\n<h2>Protection contre les erreurs de manipulation</h2>\n<p>L'outil est conçu pour qu'une fausse manœuvre cause le moins de dégâts possible :</p>\n<ul>\n<li><b>La simulation est activée par défaut.</b> Tant que la case est cochée, rien n'est modifié.</li>\n<li><b>Chaque exécution réelle est d'abord affichée</b> (ancienne IP → nouvelle IP, nom d'hôte).\n    « Annuler » est présélectionné dans toutes les questions : la touche Entrée annule.</li>\n<li><b>Les cas inhabituels exigent de saisir le nombre de serveurs</b>, p. ex. lorsqu'un appareil n'est\n    pas dans le réseau de staging de l'ordinateur, et toujours pour les modifications UEFI.</li>\n<li><b>Les adresses sont vérifiées :</b> IP ou noms d'hôte en double, passerelle hors du réseau, adresse\n    de cet ordinateur, adresse de la plage DHCP active et IP actuelle d'un autre appareil sont\n    refusées. Avant la reconfiguration, l'outil vérifie aussi si un appareil répond déjà sur la\n    nouvelle IP et, le cas échéant, ne modifie pas l'appareil.</li>\n<li><b>Dell :</b> un emplacement utilisateur occupé n'est jamais écrasé.</li>\n<li><b>Le nouveau mot de passe est saisi deux fois.</b> Si les saisies diffèrent, rien ne démarre.</li>\n<li><b>Le changement d'adresse est vérifié :</b> si seule la connexion est coupée pendant le\n    changement, un appareil n'est considéré comme terminé que lorsqu'il ne répond vraiment plus à\n    l'ancienne adresse.</li>\n<li><b>Serveur DHCP :</b> ne démarre pas sur une carte ayant une adresse publique, portant la route par\n    défaut (Internet/réseau d'entreprise), ayant obtenu son adresse par DHCP, ou si un serveur DHCP y\n    répond déjà. Il s'arrête de lui-même si la carte devient plus tard la route par défaut. Les\n    appareils ayant reçu leur adresse d'un autre serveur ne sont jamais refusés. Un serveur DHCP resté\n    actif après un plantage est arrêté au démarrage suivant.</li>\n<li><b>UEFI :</b> les attributs de mot de passe, TPM et effacement sont verrouillés. Les valeurs qui ne\n    correspondent pas au type ne sont pas appliquées. Le redémarrage n'est pas présélectionné.</li>\n<li><b>Les mots de passe</b> restent en mémoire et ne sont écrits dans un fichier que sur demande\n    explicite.</li>\n</ul>\n\n<h2>Déroulement</h2>\n<ol>\n<li><b>Serveur DHCP</b> (onglet 1) : la carte réseau reliée au switch de staging séparé doit avoir une\n    adresse fixe sans passerelle. Démarrer DHCP ; les appareils obtiennent une adresse et apparaissent\n    dans la liste, puis « Reprendre dans la liste des serveurs ». Sous Linux, dnsmasq est utilisé ;\n    sous Windows, un serveur intégré.</li>\n<li><b>Liste des serveurs</b> (onglet 2) : saisir nouvelle IP, masque, passerelle et nom d'hôte. Ce qui\n    manque encore est marqué en <b>jaune</b>, ce qui est erroné en <b>rouge</b>, ce qui est terminé en\n    <b>vert</b>. Une ligne au-dessus du bouton Démarrer indique ce qu'il reste à remplir ; l'infobulle de\n    chaque champ donne la raison.</li>\n<li><b>Simulation</b> : le journal montre ce qui serait envoyé.</li>\n<li><b>Exécution réelle</b> : décocher « Simulation ». Le bouton devient rouge et s'intitule « Lancer\n    l'exécution réelle ». Cliquer puis confirmer.</li>\n<li><b>Contrôle</b> : à la fin, l'outil vérifie que chaque nouvelle adresse répond avec le nouvel\n    utilisateur. Cela ne fonctionne que si cet ordinateur atteint le nouveau réseau ; sinon « ABSENT »\n    s'affiche, même si la modification a pu réussir.</li>\n</ol>\n\n<h2>Configurer les serveurs l'un après l'autre</h2>\n<ul>\n<li>On peut configurer un serveur, cliquer sur Démarrer, brancher le suivant et recommencer, autant de\n    fois que nécessaire. Le serveur DHCP continue de tourner.</li>\n<li>Les lignes reconfigurées avec succès passent en <b>vert</b>, restent dans la liste et sont ignorées\n    au démarrage suivant. Celles en échec sont retentées.</li>\n<li>Pour retraiter un appareil vert : sélectionner la ligne, cliquer sur « Traiter à nouveau » et\n    mettre son adresse actuelle dans « IP actuelle ».</li>\n<li>La simulation ne marque jamais rien comme terminé.</li>\n<li>« Enregistrer la liste » écrit aussi la marque verte dans le fichier (colonne « fertig ») ;\n    après « Charger la liste », les lignes sont de nouveau vertes. Un déploiement peut ainsi se\n    poursuivre un autre jour.</li>\n</ul>\n\n<h2>Profils</h2>\n<ul>\n<li>Un profil mémorise les réglages du formulaire : constructeur, utilisateur d'usine, nouvel\n    administrateur, emplacement utilisateur, valeurs par défaut et réglages DHCP. Les mots de passe et\n    la liste des serveurs n'en font jamais partie.</li>\n<li>Les profils sont dans <code>bmc-profile.json</code> à côté de l'outil ; au démarrage, le dernier\n    profil utilisé et la dernière langue choisie sont chargés.</li>\n</ul>\n\n<h2>Réglages UEFI (onglet 3)</h2>\n<ul>\n<li>Les attributs UEFI diffèrent selon le modèle. L'outil lit donc ceux qui existent réellement sur un\n    <b>appareil de référence</b> au lieu de les deviner. La lecture ne modifie rien.</li>\n<li><b>Modifier :</b> remplir « Nouvelle valeur » dans les lignes voulues ; la recherche filtre la\n    liste. Les champs vides restent inchangés. Les lignes grises (mots de passe, TPM, effacement) sont\n    verrouillées.</li>\n<li><b>Appliquer :</b> « Appliquer à tous les serveurs de la liste » utilise la liste de l'onglet 2 ;\n    les lignes vertes sont jointes à leur nouvelle IP. Chaque appareil ne reçoit que les attributs\n    qu'il possède. Les valeurs déjà correctes sont ignorées.</li>\n<li><b>Redémarrage :</b> les valeurs UEFI ne prennent effet qu'après un redémarrage. Avec « Puis\n    redémarrage forcé », un serveur en marche est redémarré de force (comme avec Reset) et un serveur\n    éteint est allumé. À réserver aux serveurs sans système d'exploitation en cours d'exécution.</li>\n<li><b>Les modèles UEFI</b> mémorisent le constructeur et les champs « Nouvelle valeur » et sont\n    enregistrés à part dans <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Services : NTP, syslog, LDAP, certificat (onglet 4)</h2>\n<p>L'onglet 4 configure des services de gestion communs sur tous les serveurs de la liste. Seules les\nsections cochées sont appliquées ; les serveurs verts (terminés) sont joints à leur nouvelle adresse.\nLa connexion utilise l'utilisateur d'usine et l'administrateur d'usine reste en place. La simulation\nest activée par défaut et montre seulement ce qui serait envoyé ; les mots de passe et le contenu du\ncertificat n'apparaissent pas dans le journal.</p>\n<ul>\n<li><b>NTP :</b> un ou deux serveurs de temps. Le fuseau horaire n'est défini que sur Dell ; sur HPE,\n    Supermicro et Lenovo, le choisir dans l'interface.</li>\n<li><b>Syslog :</b> Dell et HPE uniquement. Il n'existe pas de voie uniforme sur Supermicro et Lenovo,\n    la section y est ignorée.</li>\n<li><b>LDAP / Active Directory :</b> serveur, base DN, un compte de bind facultatif, attribut\n    utilisateur et de groupe. Sous « Groupes → rôle », les groupes LDAP sont associés aux rôles\n    Administrator, Operator ou ReadOnly. Fonctionne sur les quatre constructeurs via la voie Redfish\n    standard.</li>\n<li><b>Certificat HTTPS :</b> importe un certificat PEM signé par une CA. C'est expérimental et propre\n    au constructeur. Sur HPE, la clé correspondante doit provenir d'une CSR générée sur l'iLO, sinon\n    l'import échoue. Le BMC peut redémarrer pendant l'import.</li>\n</ul>\n<p>Pour chaque service, un constructeur qui ne le connaît pas est ignoré, rien n'est deviné. iLO 3/4\n(via RIBCL) ne sont pas gérés ici. Les procédures n'ont jusqu'ici été testées que contre des\néquipements simulés ; toujours configurer un seul appareil d'abord avant une grande série.</p>\n<h2>Comparaison des constructeurs</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Utilisateur d'usine</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Mot de passe d'usine</b></td><td>calvin ou individuel (fiche Service Tag)</td><td>individuel,\n    étiquette sur l'appareil</td><td>individuel sur l'autocollant, anciennes cartes ADMIN</td>\n    <td>individuel, étiquette sur l'appareil</td></tr>\n<tr><td><b>Nouvel utilisateur</b></td><td>dans l'emplacement, seulement s'il est libre</td><td>créé</td>\n    <td>créé</td><td>créé</td></tr>\n<tr><td><b>Changement d'adresse</b></td><td>immédiat</td><td>après redémarrage de l'iLO (lancé par\n    l'outil)</td><td>immédiat</td><td>immédiat</td></tr>\n<tr><td><b>Délai d'accès</b></td><td>secondes</td><td>1 à 3 minutes</td><td>secondes</td>\n    <td>secondes</td></tr>\n</table>\n<p>Si « Utilisateur » reste vide dans la connexion actuelle, l'utilisateur d'usine du constructeur est\nutilisé ; les listes mixtes fonctionnent donc. « Détecter automatiquement » identifie le constructeur de\nchaque appareil ; un choix fixe vaut pour toutes les lignes.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>Emplacement utilisateur :</b> 3 par défaut (le 2 est root). S'il est occupé par un autre\n    utilisateur, l'outil s'arrête pour cet appareil et ne modifie rien.</li>\n<li><b>Nom d'hôte :</b> c'est le nom DNS de l'iDRAC qui est défini, pas celui du système.</li>\n<li><b>À la livraison :</b> DHCP ou adresse fixe 192.168.0.120 selon la commande. Avec une adresse fixe,\n    l'iDRAC n'apparaît pas dans la liste DHCP ; plusieurs appareils de ce type sur le même switch\n    entrent en conflit, les brancher un par un.</li>\n<li><b>Générations :</b> iDRAC9 (14e à 16e génération) et iDRAC7/8 (12e et 13e génération). iDRAC7/8\n    exigent au moins le firmware 2.50.50.50 ; l'outil détecte un firmware plus ancien, le signale et ne\n    modifie rien. Tester d'abord iDRAC10 sur un seul appareil. iDRAC6 n'est pas pris en charge.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Mot de passe par appareil :</b> sur l'étiquette ; le saisir ligne par ligne dans « Mot de\n    passe ».</li>\n<li><b>Identification :</b> dans la liste DHCP, un iLO s'annonce en général « ILO » plus numéro de\n    série.</li>\n<li><b>Redémarrage :</b> nom d'hôte et adresse ne sont actifs qu'après le redémarrage de l'iLO ; le\n    serveur lui-même continue de tourner.</li>\n<li><b>Port :</b> c'est le port iLO dédié qui est configuré. Les nouveaux mots de passe exigent au moins\n    8 caractères.</li>\n<li><b>Générations :</b> iLO 5 et 6 (Gen10 à Gen11) via Redfish. iLO 7 n'est pas testé. Pour iLO 3 et 4,\n    voir la section suivante.</li>\n</ul>\n\n<h2>HPE iLO 3 et iLO 4 (serveurs plus anciens)</h2>\n<ul>\n<li><b>Appareils :</b> iLO 3 équipe les serveurs G7, iLO 4 les Gen8 et Gen9. L'outil les reconnaît de\n    lui-même ; si le constructeur est choisi à la main, prendre « HPE iLO 3/4 ».</li>\n<li><b>Autre voie :</b> iLO 3 ne connaît pas Redfish. Pour iLO 3 et iLO 4, l'outil utilise donc\n    l'interface XML RIBCL, présente d'origine sur les deux. En simulation, les commandes XML figurent\n    dans le journal, les mots de passe y sont remplacés par ***.</li>\n<li><b>Redémarrage :</b> une fois l'adresse et le nom d'hôte définis, l'iLO redémarre de lui-même ; le\n    serveur continue de tourner. Il faut 1 à 3 minutes avant qu'il réponde à la nouvelle adresse.</li>\n<li><b>Chiffrement ancien :</b> iLO 3 ne gère que TLS 1.0/1.1. L'outil bascule de lui-même, appareil par\n    appareil, sur l'ancien chiffrement et l'indique dans le journal. Ces appareils ont leur place dans\n    un réseau d'administration isolé.</li>\n<li><b>Limites :</b> nom d'hôte de 49 caractères au plus. Les réglages UEFI (onglet 3) ne fonctionnent pas\n    avec iLO 3. iLO 2 et versions antérieures ne sont pas pris en charge.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Mot de passe par appareil :</b> sur l'autocollant ; le saisir ligne par ligne.</li>\n<li><b>Types d'accès :</b> l'outil accorde aussi web, IPMI et console. Si le journal indique « créé sans\n    types d'accès », vérifier une fois la connexion web.</li>\n<li><b>Licence :</b> sur les cartes plus anciennes, certaines fonctions Redfish peuvent exiger une\n    licence.</li>\n<li><b>Nutanix NX</b> est reconnu comme Supermicro.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Mot de passe par appareil :</b> sur l'étiquette ; le saisir ligne par ligne.</li>\n<li><b>Utilisateur d'usine :</b> USERID en majuscules.</li>\n<li><b>Port :</b> c'est l'interface « NIC », le port XCC dédié, qui est configurée.</li>\n<li><b>Générations :</b> XCC2/XCC3. Tester d'abord XCC1 sur un seul appareil.</li>\n</ul>\n\n<h2>Nœuds Nutanix (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>L'outil ne définit que l'adresse et l'utilisateur du BMC, ni l'hyperviseur ni la CVM.</li>\n<li>Ne faire ce changement qu'<b>avant</b> la création du cluster. Sur un cluster en service, utiliser la\n    procédure Nutanix, car le cluster utilise lui-même les données du BMC.</li>\n</ul>\n\n<h2>En cas de problème</h2>\n<ul>\n<li><b>DHCP ne démarre pas :</b> le message indique la raison. Le plus souvent, la carte a une\n    passerelle ou a obtenu son adresse par DHCP. Sous Linux par exemple :\n    <code>nmcli con add type ethernet ifname &lt;carte&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>Aucun appareil dans la liste DHCP :</b> pare-feu (Linux avec ufw : autoriser UDP 67 ; Windows :\n    accepter la demande du pare-feu) ou le BMC n'est pas en DHCP.</li>\n<li><b>Port 67 occupé (Windows) :</b> en général le partage de connexion Internet ou un point d'accès\n    mobile.</li>\n<li><b>HTTP 401 :</b> utilisateur ou mot de passe erroné. Vider « Utilisateur », vérifier le mot de\n    passe de la ligne.</li>\n<li><b>Constructeur non reconnu :</b> l'appareil ne répond ni en Redfish ni comme iLO 3/4. iDRAC6, iLO 2\n    et les firmwares très anciens ne sont pas pris en charge. Un iLO 3/4 dont les informations\n    d'appareil ont été désactivées reste accessible en choisissant « HPE iLO 3/4 » à la main.</li>\n<li><b>Utilisateur refusé :</b> le plus souvent les règles de mot de passe. Le message du BMC figure\n    sous la ligne d'erreur.</li>\n<li><b>« la nouvelle IP est déjà prise » :</b> un appareil répond déjà à cette adresse. En choisir une\n    autre.</li>\n<li><b>Liste issue d'Excel ou d'un autre outil :</b> sans colonne « fertig », toutes les lignes sont\n    ouvertes. Les appareils déjà reconfigurés ne sont plus joignables à leur ancienne adresse ; retirer\n    ou corriger ces lignes.</li>\n</ul>\n\n<h2>Données et limites</h2>\n<ul>\n<li>L'outil n'enregistre rien de lui-même. « Enregistrer la liste » demande si les mots de passe doivent\n    figurer dans le fichier (alors en clair). Supprimer un tel fichier après le déploiement.</li>\n<li>Les procédures HPE (iLO 3/4 compris), iDRAC7/8 anciens, Supermicro, Lenovo et UEFI ainsi que le\n    serveur DHCP Windows n'ont été testés jusqu'ici que contre des équipements simulés. Toujours configurer d'abord un seul appareil avant\n    une grande série.</li>\n<li>« Enregistrer le journal » écrit le journal de la dernière exécution dans un fichier texte, p. ex.\n    pour la documentation. Il ne contient aucun mot de passe.</li>\n<li>La langue se change en haut à droite, tant qu'aucune exécution n'est en cours et que le serveur DHCP\n    est arrêté.</li>\n</ul>\n",
"gsw": "<h2>BMC-Steward</h2>\n<p>Grüezi! De BMC-Steward bedient jede Server schön de Reihe naa, und das ganz ohni Trinkgäld: Er leit de neu Administrator aa, setzt de Hostname\nund stellt de BMC uf e feschti Adrässe um. Mer mues nöd alli Server ufs Mal iiträge, sie chönd eis\nnach em andere dezuechoo.</p>\n\n<h2>Schutz gäge Fählbedienig</h2>\n<p>S Tool isch so baut, dass en Fählgriff möglichscht nüt kaputt macht:</p>\n<ul>\n<li><b>De Trocheluuf isch voriigstellt.</b> Solang s Häägli gsetzt isch, wird nüt gänderet.</li>\n<li><b>Jede scharfe Luuf wird vorher zeigt</b> (alti IP → neui IP, Hostname). I allne Abfrage isch\n    „Abbräche“ voruusgwählt, Enter bricht also ab.</li>\n<li><b>Bi ungwöhnliche Fäll mues mer d Aazahl Server iitippe</b>, z. B. wenn es Grät nöd im eigete\n    Staging-Netz liit, und immer bi UEFI-Änderige.</li>\n<li><b>D Adrässe wärded prüeft:</b> doppleti IPs oder Hostnäme, es Gateway usserhalb vom Netz, e\n    Adrässe vo dem Rächner, e Adrässe us em laufende DHCP-Beriich und di aktuell IP vo eme andere Grät\n    wärded abglehnt. Vor em Umstelle prüeft s Tool zuesätzlich, öb under de neue IP scho es Grät\n    antwortet, und laht s Grät dänn wie's isch.</li>\n<li><b>Dell:</b> En bsetzte User-Slot wird nie überschribe.</li>\n<li><b>S neu Passwort wird zweimal iitippt.</b> Stimmed d Iigabe nöd überii, startet kein Luuf.</li>\n<li><b>De Adrässwächsel wird prüeft:</b> Riisst bim Umstelle nur d Verbindig ab, gilt es Grät erscht als\n    fertig, wenn's under de alte Adrässe würkli nüme antwortet.</li>\n<li><b>DHCP-Server:</b> startet nöd uf ere Charte mit öffentlicher Adrässe, mit de Standardroute\n    (Internet/Firmenetz), mit ere per DHCP übercho Adrässe oder wenn det scho en DHCP-Server antwortet.\n    Er stoppt vo sälber, wenn d Charte spöter zur Standardroute wird. Grät, wo ihri Adrässe vo eme\n    andere Server händ, wärded nie abgwise. En DHCP-Server, wo nach eme Absturz no lauft, wird bim\n    nächschte Start beändet.</li>\n<li><b>UEFI:</b> Passwort-, TPM- und Lösch-Attribut sind gsperrt. Wärt, wo nöd zum Typ passed, wärded\n    nöd gsetzt. En Neustart isch nöd voruusgwählt.</li>\n<li><b>Passwörter</b> bliibed im Arbeitsspeicher und landed nur uf usdrückliche Wunsch i ere\n    Datei.</li>\n</ul>\n\n<h2>Ablauf</h2>\n<ol>\n<li><b>DHCP-Server</b> (Riiter 1): D Netzwerkcharte am trännte Staging-Switch bruucht e feschti Adrässe\n    ohni Gateway. DHCP starte, d Grät holed sich e Adrässe und tauched i de Lischte uf, vo det „I d\n    Serverlischte übernäh“. Under Linux lauft defür dnsmasq, under Windows en iibaute Server.</li>\n<li><b>Serverlischte</b> (Riiter 2): neui IP, Subnetzmaske, Gateway und Hostname iiträge. Was no fählt, isch\n    <b>gäl</b> markiert, Fählerhafts <b>rot</b>, Fertigs <b>grüen</b>. Über em Start-Chnopf staht i einere\n    Ziile, was vor em Luuf no uuszfülle isch; de Grund zu jedem Fäld staht im Tooltip.</li>\n<li><b>Trocheluuf</b>: zeiget im Protokoll, was gschickt würd.</li>\n<li><b>Scharfe Luuf</b>: Häägli bi „Trocheluuf“ ewägnäh. De Start-Chnopf wird rot und heisst dänn\n    „Scharf starte“. Klicke und d Abfrag bestätige.</li>\n<li><b>Kontrolle</b>: Am Schluss prüeft s Tool, öb jedi neui Adrässe mit em neue Benutzer antwortet. Das\n    gaht nur, wenn dä Rächner s neu Netz erreicht; susch staht det „FÄHLT“, obwohl d Umstellig\n    cha klappt ha.</li>\n</ol>\n\n<h2>Server eis nach em andere konfiguriere</h2>\n<ul>\n<li>Mer cha en Server konfiguriere, Start drücke, de nächscht aaschliesse und wider Start drücke, so\n    oft wie mer will. De DHCP-Server lauft debii wiiter.</li>\n<li>Erfolgriich umgstellti Ziile wärded <b>grüen</b> markiert, bliibed i de Lischte stah und wärded bim\n    nächschte Start überspronge. Fählgschlageni wärded nomal probiert.</li>\n<li>Söll es grüens Grät nomal bearbeitet wärde: Ziile markiere, „Nomal bearbeite“ und d „Aktuelli IP“\n    uf sini jetzigi Adrässe setze.</li>\n<li>De Trocheluuf markiert nie öppis als fertig.</li>\n<li>„Lischte speichere“ schriibt d grüen Markierig mit i d Datei (Spalte „fertig“), nach „Lischte\n    lade“ sind d Ziile wider grüen. So cha mer en Rollout au amene andere Tag wiitermache.</li>\n</ul>\n\n<h2>Profil</h2>\n<ul>\n<li>Es Profil merkt sich d Iistellige vo de Maske: Hersteller, Werksbenutzer, neue Administrator,\n    User-Slot, Vorgabe für neui Ziile und d DHCP-Iistellige. Passwörter und d Serverlischte ghöred nie\n    dezue.</li>\n<li>D Profil ligged i <code>bmc-profile.json</code> näbem Tool; bim Start wärded s zletscht bruuchte\n    Profil und di zletscht gwählti Sproch glade.</li>\n</ul>\n\n<h2>UEFI-Iistellige (Riiter 3)</h2>\n<ul>\n<li>UEFI-Attribut sind je nach Modäll andersch. S Tool liest drum die, wo's würkli git, vo eme\n    <b>Referänzgrät</b>, statt sie z errate. Läse änderet nüt.</li>\n<li><b>Ändere:</b> bi de gwünschte Ziile „Neue Wärt“ uusfülle, d Suechi grenzt d Lischte ii. Lääri\n    Fälder bliibed wie sie sind. Graui Ziile (Passwörter, TPM, Löschfunktione) sind gsperrt.</li>\n<li><b>Aawände:</b> „Uf allne Server vo de Lischte setze“ nimmt d Serverlischte us em Riiter 2; grüeni\n    Ziile wärded under ihrer neue IP aagsproche. Jedes Grät überchunnt nur Attribut, wo's au hät.\n    Wärt, wo scho stimmed, wärded überspronge.</li>\n<li><b>Neustart:</b> UEFI-Wärt würked erscht nach eme Neustart. Mit em Häägli „Dänn hert neu starte“\n    wird en laufende Server hert neu gstartet (wie mit de Reset-Taschte), en abgstellte iigschaltet.\n    Nur bi Server ohni laufends Betriebssystem bruuche.</li>\n<li><b>UEFI-Vorlage</b> merked sich Hersteller und „Neue Wärt“-Fälder und ligged trännt i\n    <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Dienscht: NTP, Syslog, LDAP, Zertifikat (Riiter 4)</h2>\n<p>Riiter 4 richtet uf allne Server vo de Lischte gmeinsami Verwaltigsdienscht ii. Nur aaghaaggti\nAbschnitt wärded aagwändet; grüeni (fertigi) Server wärded under ihrer neue Adrässe aagsproche.\nAagmäldet wird mit em Wärksbenutzer, de Wärks-Administrator bliibt bestah. De Trocheluuf isch\nvoriigstellt und zeigt nur, was gschickt wür; Passwörter und de Zertifikatsinhalt stönd debii nöd im\nProtokoll.</p>\n<ul>\n<li><b>NTP:</b> ein bis zwee Ziitserver. D Ziitzone wird nur bi Dell gsetzt; bi HPE, Supermicro und\n    Lenovo bitte i de Oberflächi wähle.</li>\n<li><b>Syslog:</b> nur Dell und HPE. Bi Supermicro und Lenovo gits kei einheitliche Wäg, de Abschnitt\n    wird dört übersprunge.</li>\n<li><b>LDAP / Active Directory:</b> Server, Base-DN, optional es Bind-Konto, Benutzer- und\n    Gruppe-Attribut. Under „Gruppe → Rolle“ wärded LDAP-Gruppe de Rolle Administrator, Operator oder\n    ReadOnly zuegordnet. Gaht bi allne vier Hersteller über de Standard-Redfish-Wäg.</li>\n<li><b>HTTPS-Zertifikat:</b> importiert es vonere CA signiets PEM-Zertifikat. Das isch experimentell und\n    je Hersteller andersch. Bi HPE mues de passend Schlüssel us ere uf em iLO erzüügte CSR cho, susch\n    schiitert de Import. Bim Import cha de BMC neu starte.</li>\n</ul>\n<p>Für jede Dienscht gilt: En Hersteller, wo ne nöd kännt, wird übersprunge, nöd grate. iLO 3/4 (über\nRIBCL) wärded da nöd bedient. D Abläuf sind bis jetz nur gäge simulierti Gägestelle prüeft; vor eme\ngrosse Luuf immer zerscht eis einzelns Grät.</p>\n<h2>Hersteller im Vergliich</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Werksbenutzer</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Werkspasswort</b></td><td>calvin oder individuell (Service-Tag-Uuszug)</td><td>individuell,\n    Aahänger am Grät</td><td>individuell uf em Chläber, alti Boards ADMIN</td><td>individuell,\n    Aahänger am Grät</td></tr>\n<tr><td><b>Neue Benutzer</b></td><td>in User-Slot, nur wenn frei</td><td>neu aagleit</td>\n    <td>neu aagleit</td><td>neu aagleit</td></tr>\n<tr><td><b>Adrässwächsel</b></td><td>sofort</td><td>nach em iLO-Neustart (löst s Tool uus)</td>\n    <td>sofort</td><td>sofort</td></tr>\n<tr><td><b>Bis erreichbar</b></td><td>Sekunde</td><td>1 bis 3 Minute</td><td>Sekunde</td>\n    <td>Sekunde</td></tr>\n</table>\n<p>Bliibt „Benutzer“ bim aktuelle Login läär, wird je nach Hersteller de Werksbenutzer gnoh; so gönd au\ngmischti Lischte. „Automatisch erchänne“ findet de Hersteller pro Grät use, e feschti Wahl gilt für alli\nZiile.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>User-Slot:</b> Standard 3 (Slot 2 isch root). Isch de Slot vo eme andere Benutzer bsetzt, bricht\n    s Tool für das Grät ab und änderet nüt.</li>\n<li><b>Hostname:</b> gsetzt wird de DNS-Name vom iDRAC, nöd dä vom Betriebssystem.</li>\n<li><b>Uuslieferig:</b> je nach Bstellig DHCP oder feschti Adrässe 192.168.0.120. Mit feschter Adrässe\n    chunnt de iDRAC nöd i d DHCP-Lischte; mehreri settigi Grät am gliiche Switch chömed sich i d Quere,\n    dänn eis nach em andere aaschliesse.</li>\n<li><b>Generatione:</b> iDRAC9 (14. bis 16. Generation) und iDRAC7/8 (12. und 13. Generation). iDRAC7/8\n    bruuched mindeschtens Firmware 2.50.50.50; älteri erchännt s Tool, mäldet's und änderet nüt.\n    iDRAC10 zerscht a eim Grät teste. iDRAC6 wird nöd understützt.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Passwort pro Grät:</b> staht uf em Aahänger, pro Ziile i d Spalte „Passwort“ iiträge.</li>\n<li><b>Zueordnig:</b> i de DHCP-Lischte mäldet sich en iLO meischtens als „ILO“ plus Serienummere.</li>\n<li><b>Neustart:</b> Hostname und Adrässe wärded erscht nach em iLO-Neustart aktiv; de Server sälber\n    lauft wiiter.</li>\n<li><b>Port:</b> konfiguriert wird de dediziert iLO-Port. Neui Passwörter bruuched mindeschtens 8\n    Zeiche.</li>\n<li><b>Generatione:</b> iLO 5 und 6 (Gen10 bis Gen11) über Redfish. iLO 7 isch nöd prüeft. Für iLO 3 und\n    4 lueg im nächschte Abschnitt.</li>\n</ul>\n\n<h2>HPE iLO 3 und iLO 4 (di ältere Semeschter)</h2>\n<ul>\n<li><b>Grät:</b> iLO 3 steckt i G7-Server, iLO 4 i Gen8 und Gen9. S Tool erchännt si vo sälber; wär de\n    Hersteller fescht wählt, nimmt „HPE iLO 3/4“.</li>\n<li><b>En andere Wäg:</b> iLO 3 kännt kei Redfish. S Tool bruucht drum für iLO 3 und iLO 4 d\n    XML-Schnittstell RIBCL, wo beidi ab Wärk debii händ. Im Trocheluuf stönd d XML-Befähl im Protokoll,\n    Passwörter sind dört dur *** ersetzt.</li>\n<li><b>Neustart:</b> Nach em Setze vo Adrässe und Hostname startet s iLO vo sälber neu; de Server lauft\n    wiiter. Bis es under de neue Adrässe antwortet, gaht's 1 bis 3 Minute.</li>\n<li><b>Alti Verschlüsselig:</b> iLO 3 cha nume TLS 1.0/1.1. S Tool schaltet defür pro Grät vo sälber uf\n    di alt Verschlüsselig um und schriibt en Hiiwiis is Protokoll. Söttigi Grät ghöred in es\n    abgschottets Verwaltigsnetz.</li>\n<li><b>Gränze:</b> Hostname höchschtens 49 Zeiche. UEFI-Iistellige (Riiter 3) gönd bi iLO 3 nöd. iLO 2\n    und älter wärded nöd understützt.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Passwort pro Grät:</b> staht uf em Chläber, pro Ziile iiträge.</li>\n<li><b>Zuegriffsarte:</b> s Tool git Web, IPMI und Konsole mit. Staht im Protokoll „ohni Zuegriffsarte\n    aagleit“, d Web-Aamäldig eimal prüefe.</li>\n<li><b>Lizänz:</b> bi ältere Boards chönd Redfish-Funktione e Lizänz bruuche.</li>\n<li><b>Nutanix NX</b> wird als Supermicro erchännt.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Passwort pro Grät:</b> staht uf em Aahänger, pro Ziile iiträge.</li>\n<li><b>Werksbenutzer:</b> USERID i Grossbuechstabe.</li>\n<li><b>Port:</b> konfiguriert wird d Schnittstell „NIC“, de dediziert XCC-Port.</li>\n<li><b>Generatione:</b> XCC2/XCC3. XCC1 zerscht a eim Grät teste.</li>\n</ul>\n\n<h2>Nutanix-Nodes (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>S Tool setzt nur BMC-Adrässe und BMC-Benutzer, nöd Hypervisor oder CVM.</li>\n<li>Nur <b>vor</b> em Erstelle vom Cluster so umstelle. Bi eme laufende Cluster s Nutanix-Verfahre\n    bruuche, will de Cluster d BMC-Date sälber bruucht.</li>\n</ul>\n\n<h2>Wenn öppis nöd klappt</h2>\n<ul>\n<li><b>DHCP startet nöd:</b> D Mäldig seit de Grund. Meischtens hät d Charte es Gateway oder ihri\n    Adrässe per DHCP. Under Linux z. B.:\n    <code>nmcli con add type ethernet ifname &lt;charte&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>Kei Grät i de DHCP-Lischte:</b> Firewall (Linux mit ufw: UDP 67 freigäh; Windows: d\n    Firewall-Abfrag zuelaa) oder de BMC staht nöd uf DHCP.</li>\n<li><b>Port 67 bsetzt (Windows):</b> meischtens Internetverbindigsfreigab oder mobile Hotspot.</li>\n<li><b>HTTP 401:</b> Benutzer oder Passwort falsch. Fäld „Benutzer“ lääre, Passwort vo de Ziile\n    prüefe.</li>\n<li><b>Hersteller nöd erchännt:</b> S Grät antwortet weder per Redfish no als iLO 3/4. iDRAC6, iLO 2 und\n    uralti Firmware wärded nöd understützt. Es iLO 3/4, wo d Grätuuskunft abgschaltet isch, laat sich\n    über di feschti Wahl „HPE iLO 3/4“ glich aaspräche.</li>\n<li><b>Benutzer abglehnt:</b> meischtens Passwortregle. D Mäldig vom BMC staht under de\n    Fählerziile.</li>\n<li><b>„neui IP isch scho vergäh“:</b> Under dere Adrässe antwortet scho es Grät. E anderi wähle.</li>\n<li><b>Lischte us em Excel oder eme andere Tool:</b> Ohni Spalte „fertig“ sind alli Ziile offe. Scho\n    umgstellti Grät sind under de alte Adrässe nöd erreichbar, die Ziile ewägnäh oder aapasse.</li>\n</ul>\n\n<h2>Date und Gränze</h2>\n<ul>\n<li>S Tool speicheret nüt vo sälber. „Lischte speichere“ fröget, öb Passwörter mit i d Datei sölled\n    (dänn im Klartext). E söttigi Datei nach em Rollout lösche.</li>\n<li>D Abläuf für HPE (au iLO 3/4), älteri iDRAC7/8, Supermicro, Lenovo und UEFI und de\n    Windows-DHCP-Server sind bis jetzt nur gäge simulierti Gägestelle prüeft. Vor eme grosse Luuf immer zerscht eis einzelns Grät\n    konfiguriere.</li>\n<li>„Protokoll speichere“ schriibt s Protokoll vom letschte Luuf i e Textdatei, z. B. für d\n    Dokumentation. Passwörter stönd keini dine.</li>\n<li>D Sproch cha mer obe rächts umstelle, solang kein Luuf aktiv isch und de DHCP-Server gstoppt\n    isch.</li>\n</ul>\n"
}

HELP["de"] = HELP["de"] + "<h3>UEFI-Konsole (Reiter 5)</h3>\n<p>Zeigt das UEFI/BIOS eines Servers über den BMC an. Drei Wege: <b>Bild</b> (eingebettete HTML5-Konsole des BMC, Maus und Tastatur wie am Original, Anmeldung automatisch), <b>Text</b> (serielle Konsole über IPMI-SOL oder SSH, nur Linux, braucht <code>pyte</code>, <code>ipmitool</code> und <code>sshpass</code>) und <b>Standardbrowser</b>. „Ins UEFI-Setup booten“ merkt per Redfish den Start ins Setup vor und startet den Server hart neu. Der Reiter speichert nichts. Alte BMCs mit nur TLS 1.0/1.1 gehen nur per Textkonsole.</p>\n"
HELP["en"] = HELP["en"] + "<h3>UEFI console (tab 5)</h3>\n<p>Shows a server's UEFI/BIOS through the BMC. Three ways: <b>Picture</b> (embedded HTML5 console of the BMC, mouse and keyboard like the original, automatic login), <b>Text</b> (serial console via IPMI-SOL or SSH, Linux only, needs <code>pyte</code>, <code>ipmitool</code> and <code>sshpass</code>) and <b>default browser</b>. \"Boot into UEFI setup\" schedules the next boot into setup via Redfish and hard-restarts the server. This tab stores nothing. Old BMCs with TLS 1.0/1.1 only work via the text console.</p>\n"
HELP["fr"] = HELP["fr"] + "<h3>Console UEFI (onglet 5)</h3>\n<p>Affiche l'UEFI/BIOS d'un serveur via le BMC. Trois voies : <b>Image</b> (console HTML5 intégrée du BMC, souris et clavier comme sur l'original, connexion automatique), <b>Texte</b> (console série via IPMI-SOL ou SSH, Linux uniquement, nécessite <code>pyte</code>, <code>ipmitool</code> et <code>sshpass</code>) et <b>navigateur par défaut</b>. « Démarrer dans la configuration UEFI » prépare via Redfish le prochain démarrage dans la configuration et redémarre le serveur de force. Cet onglet n'enregistre rien. Les anciens BMC avec TLS 1.0/1.1 seulement ne fonctionnent qu'en console texte.</p>\n"
HELP["gsw"] = HELP["gsw"] + "<h3>UEFI-Konsole (Reiter 5)</h3>\n<p>Zeigt s UEFI/BIOS vomene Server über de BMC aa. Drü Wääg: <b>Bild</b> (iibettet HTML5-Konsole vom BMC, Muus und Tastatur wie am Original, Aamäldig automatisch), <b>Text</b> (serielli Konsole über IPMI-SOL oder SSH, nume Linux, bruucht <code>pyte</code>, <code>ipmitool</code> und <code>sshpass</code>) und <b>Standardbrowser</b>. „Is UEFI-Setup boote“ merkt per Redfish de Start is Setup vor und startet de Server hert neu. De Reiter speicheret nüt. Alti BMCs mit nume TLS 1.0/1.1 gönd nume über d Textkonsole.</p>\n"


HELP["de"] = HELP["de"] + "<h3>Bestandsserver (Reiter 2b)</h3>\n<p>Für BMCs, die schon konfiguriert sind. Je Zeile Adresse, Port (Standard 443), Benutzer und Passwort eintragen, dann <b>Verbindung testen</b>: Das Tool prüft nur lesend Port, Verschlüsselung, Hersteller, Anmeldung, Modell, Firmware und Administratorrechte. Jeder Server bekommt genau einen Anmeldeversuch, damit ein falsches Passwort kein Konto sperrt. Erfolgreiche Server übernimmt <b>In Reiter 2–4 übernehmen</b> in die Serverliste; dort stellen Sie Netzwerk, UEFI und Dienste ein. Die Reiter 2 bis 4 kennen einen Benutzer für alle Server, abweichende Benutzer in einem zweiten Durchgang. Der Reiter speichert nichts.</p>\n"

HELP["en"] = HELP["en"] + "<h3>Existing servers (tab 2b)</h3>\n<p>For BMCs that are already configured. Enter address, port (default 443), user and password per row, then <b>Test connection</b>: the tool checks read-only the port, encryption, vendor, login, model, firmware and administrator rights. Each server gets exactly one login attempt so a wrong password cannot lock an account. <b>Use in tabs 2–4</b> copies the successful servers into the server list, where you set network, UEFI and services. Tabs 2 to 4 know one user for all servers; use different users in a second pass. This tab stores nothing.</p>\n"

HELP["fr"] = HELP["fr"] + "<h3>Serveurs existants (onglet 2b)</h3>\n<p>Pour les BMC déjà configurés. Saisissez adresse, port (443 par défaut), utilisateur et mot de passe par ligne, puis <b>Tester la connexion</b> : l'outil vérifie en lecture seule le port, le chiffrement, le fabricant, la connexion, le modèle, le firmware et les droits d'administrateur. Chaque serveur n'a qu'une seule tentative de connexion pour qu'un mauvais mot de passe ne verrouille pas un compte. <b>Reprendre dans les onglets 2–4</b> copie les serveurs réussis dans la liste, où vous réglez réseau, UEFI et services. Les onglets 2 à 4 ne connaissent qu'un utilisateur pour tous les serveurs ; les autres en deuxième passe. Cet onglet n'enregistre rien.</p>\n"

HELP["gsw"] = HELP["gsw"] + "<h3>Bestehendi Server (Reiter 2b)</h3>\n<p>Für BMCs, wo scho konfiguriert sind. Pro Zile Adrässe, Port (Standard 443), Benutzer und Passwort iitrage, dänn <b>Verbindig teste</b>: S Tool prüeft nume läsend Port, Verschlüsselig, Hersteller, Aamäldig, Modell, Firmware und Administratorrächt. Jede Server überchunnt gnau en Aamäldversuech, dass es falsches Passwort kei Konto sperrt. <b>I Reiter 2–4 übernäh</b> kopiert d erfolgriichä Server i d Serverlischte; det stellsch Netzwärk, UEFI und Dienscht i. D Reiter 2 bis 4 kännd eimal Benutzer für alli Server, anderi i emene zweite Durchgang. De Reiter speicheret nüt.</p>\n"

set_language(load_lang())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    MAIN = Window()
    MAIN.show()
    sys.exit(app.exec())
