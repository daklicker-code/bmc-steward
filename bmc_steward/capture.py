"""BMC-Steward: Aufnahme- und Vergleichsmodus (nur lesend).

Liest den Redfish-Baum eines echten Geräts (ausschließlich GET), schwärzt auf Wunsch persönliche Daten und
legt ihn im DMTF-Redfish-Mockup-Format ab (ein Ordner je Ressource mit „index.json“, Pfad = Redfish-Pfad). Daraus
entstehen (a) ein Mockup für den vBMC und den DMTF-Mockup-Server, (b) ein
Fähigkeitsbericht („SSH-Keys: ja/nein“ …) und (c) ein Vergleich zweier Aufnahmen desselben Geräts, etwa vor und
nach einem Firmware-Update: verschwundene Pfade und Aktionen, weggefallene Schreibrechte (Allow-Kopfzeile),
geänderte erlaubte Werte.

Grenzen: Eine Aufnahme ist eine Momentaufnahme. Zustandswechsel, Aufträge und Live-Sensoren bildet sie nicht ab.
Die Schwärzung ist nach bestem Wissen gebaut und prüft sich selbst, ersetzt aber nicht den Blick in die
Dateien, bevor sie weitergegeben werden.

Aufruf ohne Oberfläche:
    python3 -m bmc_steward.capture capture 10.0.0.5 --user root --out aufnahme-dell
    python3 -m bmc_steward.capture report aufnahme-dell
    python3 -m bmc_steward.capture diff aufnahme-vorher aufnahme-nachher
(Das Passwort kommt aus der Umgebungsvariable BMC_PASSWORD oder wird abgefragt.)
"""
import argparse
import collections
import datetime
import getpass
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

from .core import __version__
from .i18n import T
from .redfish import RedfishClient, hostport

ROOT = "/redfish/v1"
MAX_RESOURCES = 4000          # Obergrenze je Aufnahme
MEMBER_CAP = 60               # so viele Mitglieder je Collection werden verfolgt
LOG_CAP = 3                   # Log-Einträge je Log-Collection
NO_MEMBERS = ("/Registries", "/SessionService/Sessions")      # Collection ja, Mitglieder nein
SKIP_PREFIXES = (ROOT + "/JsonSchemas", ROOT + "/Schemas", ROOT + "/$metadata", ROOT + "/odata")
VOLATILE = ("/Entries/", "/Sessions/", "/Tasks/", "/Jobs/", "/Metrics", "/Telemetry", "/Subscriptions/")
META_FILE = "bmc-steward-aufnahme.json"
# Pfade, die auch dann gelesen werden, wenn der Dienststamm sie nicht verlinkt: Die Pfade, die BMC-Steward selbst
# benutzt, gehören in jede Aufnahme (ein „nicht gefunden“ ist dann ein Beleg für den Fähigkeitsbericht)
SEED_PATHS = ("/Managers", "/Systems", "/Chassis", "/AccountService", "/AccountService/Accounts",
              "/SessionService", "/UpdateService", "/CertificateService", "/EventService")
DEFAULT_USERS = {"root", "administrator", "admin", "userid", "readonly", "operator"}
EMPTY = {"", "n/a", "na", "none", "null", "unknown", "not specified", "not available", "0", "to be filled by o.e.m.",
         "default string", "system serial number"}

# Schlüssel (kleingeschrieben) -> Art; ihre Werte sind persönlich und werden gesammelt
SECRET_KEYS = {"serialnumber": "SERIAL", "sku": "SERIAL", "servicetag": "SERIAL", "assettag": "ASSET",
               "uuid": "UUID", "hostname": "HOST", "fqdn": "HOST", "domainname": "HOST",
               "macaddress": "MAC", "permanentmacaddress": "MAC"}
# Schlüssel, deren Wert immer ersetzt wird (Zertifikate, Schlüsselmaterial, Geheimnisse)
BLANK_KEYS = {"certificatestring", "keystring", "publickey", "fingerprint", "thumbprint",
              "subjectalternativenames", "privatekey"}
BLANK_HINTS = ("password", "token", "secret", "community")
ADDR_ENDINGS = ("address", "addresses", "gateway", "server", "servers", "uri", "host", "destination", "dns",
                "nameservers", "ntp")
IPV4 = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9])")
DOTTED_NAME = re.compile(r"(?=.*[A-Za-z])[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")
IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
CTX_IPV4 = re.compile(r"\b(?:from|by|to|at|host|ip|address|client|source|via)\s*[:=]?\s*(" + IPV4.pattern + ")", re.IGNORECASE)
MAC = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}(?![0-9A-Fa-f])")
UUID = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
                  r"(?![0-9A-Fa-f])")
WRITE_METHODS = {"PATCH", "POST", "PUT", "DELETE"}
WATCHED_VERSIONS = {"FirmwareVersion", "BiosVersion", "Version", "RedfishVersion", "Model"}


# ---------------------------------------------------------------- Auslesen

def norm_path(link, host=""):
    """Redfish-Pfad aus einem @odata.id (ohne Fragment/Abfrage/Schrägstrich am Ende), sonst None."""
    if not isinstance(link, str):
        return None
    if "://" in link:
        u = urllib.parse.urlsplit(link)
        if host and u.netloc.split(":")[0].lower() != host.split(":")[0].lower():
            return None
        link = u.path
    link = link.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if not link.startswith(ROOT) or ".." in link.split("/"):
        return None
    return link


def member_cap(path):
    last = path.rsplit("/", 1)[-1]
    if any(path.endswith(s) for s in NO_MEMBERS):
        return 0
    if last in ("Entries", "LogEntries"):
        return LOG_CAP
    return MEMBER_CAP


def links_of(doc, own, host=""):
    """Alle verfolgbaren Verweise eines Dokuments: [(Pfad, ist_Mitglied_einer_Collection, Nummer)]."""
    out = []

    def walk(o, in_members, idx):
        if isinstance(o, dict):
            link = norm_path(o.get("@odata.id"), host)
            if link and link != own:
                out.append((link, in_members, idx))
            for k, v in o.items():
                if k == "@odata.id" or k.startswith("Actions"):
                    continue
                walk(v, False, 0)
        elif isinstance(o, list):
            for n, v in enumerate(o):
                walk(v, in_members, n)
    if isinstance(doc, dict):
        members = doc.get("Members")
        for k, v in doc.items():
            if k in ("@odata.id", "Members") or k.startswith("Actions"):
                continue
            walk(v, False, 0)
        if isinstance(members, list):
            for n, m in enumerate(members):
                link = norm_path(m.get("@odata.id") if isinstance(m, dict) else None, host)
                if link and link != own:
                    out.append((link, True, n))
    return out


class CaptureEngine(RedfishClient):
    """Liest den Redfish-Baum eines Geräts. Sendet ausschließlich GET."""

    def __init__(self, log=print, stopped=lambda: False, progress=lambda n, path: None, max_resources=MAX_RESOURCES):
        super().__init__(False, log, stopped)
        self.progress, self.max_resources = progress, max_resources

    def capture(self, ip, user="", pw="", vendor=None):
        """Gibt (Aufnahme, Fehlertext) zurück; bei einem Fehler ist die Aufnahme None."""
        try:
            v = vendor or self.vendor(ip)
            if v == "offline":
                return None, T("Gerät nicht erreichbar.")
            if v == "hpe_old":
                return None, T("iLO 3/4 kennt kein Redfish: Aufnahme nicht möglich.")
            if v not in self.NAME:
                return None, T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC).")
            user = user or self.DEFAULT_USER[v]
            cap = {"meta": {"tool": "BMC-Steward " + __version__, "created": datetime.datetime.now(
                datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "vendor": v, "vendor_name": self.NAME[v],
                "host": ip, "redacted": False, "aborted": "", "requests": 0},
                "tree": {}, "allow": {}, "errors": {}, "skipped": {}}
            self._crawl(cap, ip, user, pw)
            if not cap["tree"]:
                code = cap["errors"].get(ROOT, 0)
                return None, "\n".join(self.fail_lines(T("Redfish-Wurzel lesen"), code))
            self._describe(cap)
            return cap, ""
        except Exception as e:                                  # pragma: no cover - Sicherheitsnetz für den Thread
            return None, T('Unerwarteter Fehler: {0!r}').format(e)
        finally:
            self.close_sessions()

    def _crawl(self, cap, ip, user, pw):
        tree, allow, errors, skipped = cap["tree"], cap["allow"], cap["errors"], cap["skipped"]
        seeds = [ROOT] + [ROOT + p for p in SEED_PATHS]
        nic = self.ETH_PATH.get(cap["meta"]["vendor"])
        if nic:
            mgr = nic.split("/EthernetInterfaces")[0]
            seeds += [mgr, mgr + "/NetworkProtocol", nic, ROOT + "/AccountService/Accounts/1/Keys"]
            if cap["meta"]["vendor"] == "dell":
                seeds.append(mgr + "/Attributes")
        queue, seen, taken, fails = collections.deque(dict.fromkeys(seeds)), set(seeds), {}, 0
        while queue:
            if self.stopped():
                cap["meta"]["aborted"] = "stopped"
                self.log(T("  Aufnahme abgebrochen."))
                break
            if len(tree) >= self.max_resources:
                cap["meta"]["aborted"] = "limit"
                self.log(T("  Obergrenze von {0} Ressourcen erreicht.").format(self.max_resources))
                break
            path = queue.popleft()
            code = self.api(ip, user, pw, "GET", path)
            cap["meta"]["requests"] += 1
            if code == 0:
                errors[path] = 0
                fails += 1
                if fails >= 5:
                    cap["meta"]["aborted"] = "connection"
                    self.log(T("  Verbindung zum Gerät verloren, Aufnahme beendet."))
                    break
                continue
            fails = 0
            if not self.ok(code) or not isinstance(self.body, dict):
                errors[path] = code
                continue
            doc = self.body
            tree[path] = doc
            allow[path] = self.resp_headers.get("allow", "")
            if len(tree) % 50 == 0:
                self.log(T("  {0} Ressourcen gelesen …").format(len(tree)))
            self.progress(len(tree), path)
            cap_n = member_cap(path)
            for link, is_member, n in links_of(doc, path, ip):
                if link in seen:
                    continue
                if any(link.startswith(p) for p in SKIP_PREFIXES):
                    seen.add(link)
                    skipped[link] = "schema"
                    continue
                if is_member:
                    taken[path] = taken.get(path, 0) + 1
                    if taken[path] > cap_n:
                        seen.add(link)
                        skipped[link] = "cap"
                        continue
                seen.add(link)
                queue.append(link)

    @staticmethod
    def _describe(cap):
        """Modell- und Firmware-Angaben für den Kopf der Aufnahme (nicht persönlich)."""
        tree, meta = cap["tree"], cap["meta"]
        meta["redfish_version"] = str(tree.get(ROOT, {}).get("RedfishVersion", ""))
        for path, doc in sorted(tree.items()):
            if re.fullmatch(ROOT + r"/Managers/[^/]+", path) and "firmware" not in meta:
                meta["firmware"] = str(doc.get("FirmwareVersion", ""))
                meta["manager_model"] = str(doc.get("Model", ""))
            if re.fullmatch(ROOT + r"/Systems/[^/]+", path) and "model" not in meta:
                meta["model"] = str(doc.get("Model", ""))
                meta["bios"] = str(doc.get("BiosVersion", ""))
        meta["resources"] = len(tree)


# ---------------------------------------------------------------- Schwärzen

def _usable(value):
    return isinstance(value, str) and value.strip().lower() not in EMPTY


def _boring_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return True
    return ip.is_unspecified or text.startswith("255.") or ip.is_loopback or ip.is_multicast


def _pseudonym(kind, n):
    if kind == "MAC":
        return "02:00:00:00:%02x:%02x" % (n >> 8 & 255, n & 255)
    if kind == "UUID":
        return "00000000-0000-4000-8000-%012x" % n
    if kind == "IPV4":
        nets = ("192.0.2.", "198.51.100.", "203.0.113.")
        return "%s%d" % (nets[(n - 1) // 250 % 3], (n - 1) % 250 + 1) if n <= 750 else "ADDR-%03d" % n
    if kind == "IPV6":
        return "2001:db8::%x" % n
    return "%s-%03d" % (kind, n)


def redact(cap):
    """Schwärzt die Aufnahme an Ort und Stelle. Gibt einen Bericht zurück {Art: Anzahl} (ohne die Werte selbst).
    Wirft ValueError, wenn nach dem Schwärzen noch ein gesammelter Wert zu finden ist."""
    found = {k: set() for k in ("SERIAL", "ASSET", "UUID", "HOST", "MAC", "IPV4", "IPV6", "USER")}

    def add(kind, value, force=False):
        value = value.strip()
        if not _usable(value):
            return
        if kind in ("IPV4", "IPV6") and _boring_ip(value) and not force:
            return
        found[kind].add(value)

    host = cap["meta"].get("host", "")
    if host:
        h = hostport(host)[0]
        add("IPV6" if ":" in h else "IPV4" if IPV4.fullmatch(h) else "HOST", h, force=True)

    texts = []
    for path, doc in cap["tree"].items():
        texts.append(path)

        def collect(obj, key=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    lk = str(k).lower()
                    if isinstance(v, str) and lk in SECRET_KEYS:
                        add(SECRET_KEYS[lk], v)
                    if lk == "username" and isinstance(v, str) and v.strip().lower() not in DEFAULT_USERS:
                        add("USER", v)
                    collect(v, str(k))
            elif isinstance(obj, list):
                for v in obj:
                    collect(v, key)
            elif isinstance(obj, str):
                lk = key.lower()
                texts.append(obj)
                if lk.endswith(ADDR_ENDINGS) or lk in ("ip", "ipv4", "ipv6") or lk.startswith(("ipv4", "ipv6")):
                    for m in IPV4.findall(obj):
                        add("IPV4", m)
                    for m in IPV6.findall(obj):
                        if m.count(":") >= 2:
                            add("IPV6", m)
                    if DOTTED_NAME.fullmatch(obj.strip()) and not IPV4.fullmatch(obj.strip()):
                        add("HOST", obj)                 # z. B. ein interner NTP- oder Syslog-Servername
        collect(doc)
    for t in texts:
        for m in MAC.findall(t):
            add("MAC", m)
        for m in UUID.findall(t):
            add("UUID", m)
        for m in CTX_IPV4.findall(t):                     # Freitext, z. B. "angemeldet von 10.0.0.5" im Ereignisprotokoll
            add("IPV4", m)

    for name in list(found["HOST"]):                      # Domäne eines vollständigen Namens ebenfalls schwärzen
        if name.count(".") >= 2:
            found["HOST"].add(name.split(".", 1)[1])
    # stabile Ersatzwerte: je Art sortiert durchnummeriert (zwei Aufnahmen desselben Geräts stimmen überein)
    mapping = {}
    for kind, values in found.items():
        for n, v in enumerate(sorted(values, key=lambda s: (s.lower(), s)), 1):
            mapping.setdefault(v.lower(), _pseudonym(kind, n))
    # lange Werte zuerst; kurze (< 4 Zeichen) nur über ihren Schlüssel, nie als Teilstring
    sweep = sorted((k for k in mapping if len(k) >= 4), key=len, reverse=True)
    rx = re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(re.escape(k) for k in sweep),
                    re.IGNORECASE) if sweep else None

    def sub(text):
        if rx:
            text = rx.sub(lambda m: mapping[m.group(0).lower()], text)
        return text

    def clean(obj, key=""):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                lk = str(k).lower()
                nk = sub(str(k))
                if lk in ("subject", "issuer") and isinstance(v, dict):
                    out[nk] = {kk: "REDACTED" for kk in v}
                elif lk in BLANK_KEYS and v not in (None, "", [], {}):
                    out[nk] = ["REDACTED"] if isinstance(v, list) else "REDACTED"
                elif any(h in lk for h in BLANK_HINTS) and isinstance(v, str) and v:
                    out[nk] = "REDACTED"
                elif isinstance(v, str) and lk in SECRET_KEYS and v.strip().lower() in mapping:
                    out[nk] = mapping[v.strip().lower()]
                elif isinstance(v, str) and lk == "username" and v.strip().lower() in mapping:
                    out[nk] = mapping[v.strip().lower()]
                else:
                    out[nk] = clean(v, str(k))
            return out
        if isinstance(obj, list):
            return [clean(v, key) for v in obj]
        if isinstance(obj, str):
            return sub(obj)
        return obj

    cap["tree"] = {sub(p): clean(d) for p, d in cap["tree"].items()}
    for part in ("allow", "errors", "skipped"):
        cap[part] = {sub(p): v for p, v in cap[part].items()}
    cap["meta"]["host"] = "REDACTED"
    cap["meta"]["redacted"] = True

    # Selbstprüfung: kein gesammelter Wert darf mehr vorkommen
    dump = json.dumps([cap["tree"], cap["allow"], cap["errors"], cap["skipped"], cap["meta"]],
                      ensure_ascii=False).lower()
    left = [v for v in mapping if len(v) >= 4 and re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(v), dump)]
    report = {k: len(v) for k, v in found.items() if v}
    if left:
        raise ValueError(T("Schwärzung unvollständig: {0} Wert(e) kommen noch vor. Aufnahme nicht gespeichert.")
                         .format(len(left)))
    return report


# ---------------------------------------------------------------- Speichern und Laden

def _segment(seg):
    seg = urllib.parse.quote(seg, safe="@+,=~$-._")
    return seg if len(seg) <= 150 else seg[:100] + "~" + hashlib.sha1(seg.encode()).hexdigest()[:8]


def resource_dir(root, path):
    return Path(root).joinpath(*[_segment(s) for s in path.strip("/").split("/")])


def save_capture(cap, out):
    """Schreibt die Aufnahme als DMTF-Mockup-Baum nach `out` (muss neu oder leer sein). Gibt die Anzahl der
    Dateien zurück."""
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError(T("Ordner ist nicht leer: {0}").format(out))
    out.mkdir(parents=True, exist_ok=True)
    if not cap["meta"].get("redacted"):
        try:
            os.chmod(out, 0o700)                # ungeschwärzte Aufnahme: nur für den eigenen Benutzer
        except OSError:
            pass
    n = 0
    for path, doc in cap["tree"].items():
        if ".." in path.split("/") or "\\" in path or "\0" in path:
            continue
        d = resource_dir(out, path)
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.json").write_text(json.dumps(doc, indent=4, ensure_ascii=False), encoding="utf-8")
        n += 1
    meta = {"meta": cap["meta"], "allow": cap["allow"], "errors": cap["errors"], "skipped": cap["skipped"]}
    (out / META_FILE).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "README.txt").write_text(README_TEXT, encoding="utf-8")
    return n + 2


README_TEXT = """Aufnahme des Redfish-Baums eines Geräts, erstellt mit BMC-Steward (nur lesend, ausschließlich GET).

Aufbau: DMTF-Redfish-Mockup (ein Ordner je Ressource, darin index.json). Ungewöhnliche Zeichen im Pfad sind als
%XX geschrieben. bmc-steward-aufnahme.json enthält Kopfdaten, die Allow-Kopfzeilen (Schreibrechte je Ressource),
nicht lesbare und bewusst ausgelassene Pfade (z. B. Log-Einträge jenseits der Obergrenze, Schemas).

Ausliefern:  python3 redfishMockupServer.py -D <dieser Ordner>   (DMTF Redfish-Mockup-Server)
Vergleichen: python3 -m bmc_steward.capture diff <Ordner A> <Ordner B>

Wurde "schwärzen" gewählt, sind Seriennummern, Service-Tags, UUIDs, MAC- und IP-Adressen, Hostnamen, nicht
standardmäßige Benutzernamen, Zertifikate und Schlüssel ersetzt (stabile Ersatzwerte). Das ist nach bestem Wissen
gebaut und prüft sich selbst; vor der Weitergabe trotzdem kurz in die Dateien sehen.
"""


def load_capture(folder):
    """Liest einen Aufnahme-Ordner (auch einen reinen DMTF-Mockup-Ordner ohne bmc-steward-aufnahme.json)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(T("Ordner nicht gefunden: {0}").format(folder))
    cap = {"meta": {}, "tree": {}, "allow": {}, "errors": {}, "skipped": {}}
    mf = folder / META_FILE
    if mf.is_file():
        data = json.loads(mf.read_text(encoding="utf-8"))
        for k in ("meta", "allow", "errors", "skipped"):
            cap[k] = data.get(k, {})
    for f in folder.rglob("index.json"):
        rel = f.parent.relative_to(folder).parts
        path = "/" + "/".join(urllib.parse.unquote(p) for p in rel)
        if path.startswith(ROOT):
            cap["tree"][path] = json.loads(f.read_text(encoding="utf-8"))
    if not cap["tree"]:
        raise ValueError(T("Keine Redfish-Ressourcen in {0}").format(folder))
    return cap


# ---------------------------------------------------------------- Fähigkeitsbericht

def _find(tree, pattern):
    rx = re.compile(pattern)
    return sorted(p for p in tree if rx.fullmatch(p))


def _has_key(doc, names, contains=False):
    """Kommt einer der Schlüssel irgendwo im Dokument vor (genau, oder mit contains=True als Teilstring)?"""
    names = {n.lower() for n in names}

    def hit(k):
        k = str(k).lower()
        return any(n in k for n in names) if contains else k in names

    def walk(o):
        if isinstance(o, dict):
            return any(hit(k) or walk(v) for k, v in o.items())
        if isinstance(o, list):
            return any(walk(v) for v in o)
        return False
    return walk(doc)


def _methods(allow, path):
    return {m.strip().upper() for m in (allow.get(path) or "").split(",") if m.strip()}


def capabilities(cap):
    """Liste von (Bezeichnung, Zustand, Einzelheit); Zustand: "ja", "nein" oder "?" (nicht erkennbar)."""
    tree, allow, v = cap["tree"], cap.get("allow", {}), cap["meta"].get("vendor", "")
    out = []

    def add(label, state, detail=""):
        out.append((label, state, detail))

    def yn(label, cond, detail_yes="", detail_no=""):
        add(label, "ja" if cond else "nein", detail_yes if cond else detail_no)

    ss = tree.get(ROOT + "/SessionService")
    yn(T("Anmeldung per Session"), ss is not None and ss.get("ServiceEnabled") is not False)
    nic_path = RedfishClient.ETH_PATH.get(v)
    nic = tree.get(nic_path) if nic_path else None
    if nic_path is None:
        add(T("Management-Netzwerkport (Pfad der Netzwerkeinstellungen)"), "?", T("Pfad für diesen Hersteller unbekannt"))
    else:
        yn(T("Management-Netzwerkport (Pfad der Netzwerkeinstellungen)"), nic is not None, nic_path,
           T("erwartet: {0}").format(nic_path))
    if nic is not None:
        yn(T("DNS-Server setzen"), "StaticNameServers" in nic)
        yn(T("VLAN"), _has_key(nic, ["VLAN", "VLANs", "VLANEnable"]))
        yn(T("IPv6 statisch/automatisch"), _has_key(nic, ["IPv6StaticAddresses", "IPv6AutoConfigEnabled", "DHCPv6"]))
        m = _methods(allow, nic_path)
        add(T("Netzwerkport schreibbar (PATCH erlaubt)"), "?" if not m else "ja" if "PATCH" in m else "nein", ", ".join(sorted(m)))
    npn = _find(tree, ROOT + r"/Managers/[^/]+/NetworkProtocol")
    np = tree[npn[0]] if npn else {}
    yn(T("NTP"), "NTP" in np)
    yn(T("IPMI über LAN abschaltbar"), "IPMI" in np)
    yn(T("SSDP abschaltbar"), "SSDP" in np)
    yn(T("SNMP"), "SNMP" in np)
    acc = tree.get(ROOT + "/AccountService/Accounts")
    yn(T("Konten verwalten (AccountService)"), acc is not None)
    if acc is not None:
        m = _methods(allow, ROOT + "/AccountService/Accounts")
        add(T("Konten anlegen (POST erlaubt)"), "?" if not m else "ja" if "POST" in m else "nein",
            T("Dell legt Konten nicht per POST an, sondern belegt einen freien Platz") if v == "dell" else "")
    keys = _find(tree, ROOT + r"/AccountService/Accounts/[^/]+/Keys")
    has_keys = bool(keys) or any(_has_key(d, ["Keys"]) for p, d in tree.items()
                                 if re.fullmatch(ROOT + r"/AccountService/Accounts/[^/]+", p))
    yn(T("SSH-Schlüssel je Konto"), has_keys)
    yn(T("LDAP-Anbindung"), "LDAP" in tree.get(ROOT + "/AccountService", {}) or _has_key(tree.get(ROOT + "/AccountService", {}), ["LDAP"]))
    tls = [p for p, d in tree.items() if _has_key(d, ["TLSProtocol", "TLSVersion", "TLSMin"], contains=True)]
    yn(T("TLS-Mindestversion einstellbar"), bool(tls), tls[0] if tls else "")
    yn(T("Virtual Media"), bool(_find(tree, ROOT + r"/(Managers|Systems)/[^/]+/VirtualMedia")))
    boots = [p for p, d in tree.items() if re.fullmatch(ROOT + r"/Systems/[^/]+", p)]
    yn(T("Einmaliger Start (Boot-Override)"), any(_has_key(tree[p].get("Boot", {}), ["BootSourceOverrideTarget"]) for p in boots))
    us = tree.get(ROOT + "/UpdateService", {})
    yn(T("Firmware-Update per Redfish (SimpleUpdate)"), _has_key(us.get("Actions", {}), ["#UpdateService.SimpleUpdate"]))
    yn(T("Zertifikatsdienst"), ROOT + "/CertificateService" in tree)
    yn(T("Ereignisdienst"), ROOT + "/EventService" in tree)
    yn(T("Log-Dienste"), bool(_find(tree, ROOT + r"/(Managers|Systems)/[^/]+/LogServices")))
    return out


def report_text(cap):
    m = cap["meta"]
    lines = [T("Aufnahme: {0}, Modell {1}, Firmware {2}, Redfish {3}").format(
        m.get("vendor_name", m.get("vendor", "?")), m.get("model") or "?", m.get("firmware") or "?",
        m.get("redfish_version") or "?"),
        T("{0} Ressourcen, {1} Anfragen, {2} nicht lesbar, {3} ausgelassen.").format(
            len(cap["tree"]), m.get("requests", "?"), len(cap.get("errors", {})), len(cap.get("skipped", {})))]
    if m.get("aborted"):
        lines.append(T("ACHTUNG: Aufnahme unvollständig ({0}).").format(m["aborted"]))
    lines.append("")
    lines.append(T("Fähigkeiten dieses Geräts (aus der Aufnahme):"))
    mark = {"ja": "✔", "nein": "✘", "?": "?"}
    for label, state, detail in capabilities(cap):
        lines.append("  %s %s%s" % (mark[state], label, (" – " + detail) if detail else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- Vergleich

def _flat(doc, prefix=""):
    """Menge der Schlüsselpfade eines Dokuments (Listenstellen als [], Members ausgelassen)."""
    out = set()
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in ("Members", "@odata.id", "@odata.etag", "@odata.context", "Members@odata.count") or \
                    k.startswith("@odata.") or (k.endswith("@odata.id") and not prefix):
                continue
            p = prefix + "." + k if prefix else k
            out.add(p)
            out |= _flat(v, p)
    elif isinstance(doc, list):
        for v in doc:
            out |= _flat(v, prefix + "[]")
    return out


def _allowable(doc, prefix=""):
    """{Schlüsselpfad: Menge erlaubter Werte} für jedes „…@Redfish.AllowableValues“."""
    out = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            p = prefix + "." + k if prefix else k
            if k.endswith("@Redfish.AllowableValues") and isinstance(v, list):
                out[p] = {json.dumps(x, sort_keys=True) for x in v}
            else:
                out.update(_allowable(v, p))
    elif isinstance(doc, list):
        for v in doc:
            out.update(_allowable(v, prefix + "[]"))
    return out


def _versions(doc):
    return {k: v for k, v in doc.items() if k in WATCHED_VERSIONS and isinstance(v, (str, int, float))} \
        if isinstance(doc, dict) else {}


def diff_captures(a, b):
    """Vergleicht zwei Aufnahmen. Gibt eine Liste von Befunden zurück: (Stufe, Pfad, Text) mit Stufe
    "kritisch" (etwas ist weggefallen oder nur noch lesbar) oder "hinweis" (neu oder geändert)."""
    out = []

    def add(level, path, text):
        out.append((level, path, text))

    ma, mb = a["meta"], b["meta"]
    if ma.get("vendor") and mb.get("vendor") and ma["vendor"] != mb["vendor"]:
        add("kritisch", "", T("Hersteller verschieden ({0} / {1}): Vergleich nur eingeschränkt sinnvoll.")
            .format(ma["vendor"], mb["vendor"]))
    for key, label in (("model", T("Modell")), ("firmware", T("Firmware des Managers")), ("bios", T("BIOS")),
                       ("redfish_version", T("Redfish-Version"))):
        if ma.get(key) and mb.get(key) and ma[key] != mb[key]:
            add("hinweis", "", "%s: %s → %s" % (label, ma[key], mb[key]))
    ta, tb = a["tree"], b["tree"]

    def volatile(p):
        return any(s in p + "/" for s in VOLATILE)
    gone = [p for p in sorted(ta) if p not in tb and p not in b.get("skipped", {}) and not volatile(p)]
    new = [p for p in sorted(tb) if p not in ta and p not in a.get("skipped", {}) and not volatile(p)]
    for p in gone:
        add("kritisch", p, T("Ressource fehlt in der zweiten Aufnahme."))
    for p in new:
        add("hinweis", p, T("Ressource ist neu."))
    for p in sorted(set(ta) & set(tb)):
        if volatile(p):
            continue
        da, db = ta[p], tb[p]
        ala, alb = _methods(a.get("allow", {}), p), _methods(b.get("allow", {}), p)
        if ala and alb:
            lost, got = (ala - alb) & WRITE_METHODS, (alb - ala) & WRITE_METHODS
            if lost:
                add("kritisch", p, T("Schreibrecht entfallen: {0} nicht mehr erlaubt (vorher: {1}, jetzt: {2}).")
                    .format(", ".join(sorted(lost)), ", ".join(sorted(ala)), ", ".join(sorted(alb))))
            if got:
                add("hinweis", p, T("Neu erlaubt: {0}.").format(", ".join(sorted(got))))
        fa, fb = _flat(da), _flat(db)
        for k in sorted(fa - fb):
            level = "kritisch" if k.startswith("Actions") or k.endswith("@Redfish.AllowableValues") else "hinweis"
            add(level, p, T("Eigenschaft fehlt jetzt: {0}").format(k))
        for k in sorted(fb - fa):
            add("hinweis", p, T("Eigenschaft neu: {0}").format(k))
        aa, ab = _allowable(da), _allowable(db)
        for k in sorted(set(aa) & set(ab)):
            for x in sorted(aa[k] - ab[k]):
                add("kritisch", p, T("Erlaubter Wert entfallen bei {0}: {1}").format(k.replace("@Redfish.AllowableValues", ""), x))
            for x in sorted(ab[k] - aa[k]):
                add("hinweis", p, T("Erlaubter Wert neu bei {0}: {1}").format(k.replace("@Redfish.AllowableValues", ""), x))
        va, vb = _versions(da), _versions(db)
        for k in sorted(set(va) & set(vb)):
            if va[k] != vb[k]:
                add("hinweis", p, "%s: %s → %s" % (k, va[k], vb[k]))
    out.sort(key=lambda f: (f[0] != "kritisch", f[1], f[2]))
    return out


def diff_text(findings):
    crit = [f for f in findings if f[0] == "kritisch"]
    note = [f for f in findings if f[0] != "kritisch"]
    if not findings:
        return T("Keine Unterschiede in Aufbau, Schreibrechten und erlaubten Werten.")
    lines = [T("{0} kritische Unterschiede, {1} Hinweise.").format(len(crit), len(note)), ""]
    for title, items in ((T("KRITISCH (etwas ist weggefallen oder nur noch lesbar):"), crit),
                         (T("HINWEISE (neu oder geändert):"), note)):
        if items:
            lines.append(title)
            lines += ["  %s%s" % ((f[1] + ": ") if f[1] else "", f[2]) for f in items]
            lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------- Aufruf ohne Oberfläche

def run_capture(ip, user, pw, out, vendor=None, do_redact=True, log=print, stopped=lambda: False,
                progress=lambda n, p: None):
    """Aufnehmen, schwärzen, speichern. Gibt (Aufnahme, Schwärzungsbericht, Fehlertext) zurück."""
    eng = CaptureEngine(log=log, stopped=stopped, progress=progress)
    cap, err = eng.capture(ip, user, pw, vendor)
    if cap is None:
        return None, {}, err
    rep = {}
    try:
        if do_redact:
            rep = redact(cap)
        save_capture(cap, out)
    except (ValueError, OSError) as e:
        return None, {}, str(e)
    return cap, rep, ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m bmc_steward.capture", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="Gerät aufnehmen (nur lesend)")
    c.add_argument("host")
    c.add_argument("--user", default="")
    c.add_argument("--out", required=True, help="neuer oder leerer Ordner")
    c.add_argument("--vendor", choices=sorted(RedfishClient.NAME), default=None)
    c.add_argument("--no-redact", action="store_true", help="Persönliche Daten NICHT schwärzen")
    r = sub.add_parser("report", help="Fähigkeitsbericht einer Aufnahme")
    r.add_argument("folder")
    d = sub.add_parser("diff", help="Zwei Aufnahmen vergleichen")
    d.add_argument("a")
    d.add_argument("b")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "capture":
            pw = os.environ.get("BMC_PASSWORD") or getpass.getpass("Passwort: ")
            cap, rep, err = run_capture(args.host, args.user, pw, args.out, args.vendor, not args.no_redact)
            if cap is None:
                print(err, file=sys.stderr)
                return 1
            print(report_text(cap))
            if rep:
                print("\n" + T("Geschwärzt: {0}").format(", ".join("%s %d" % kv for kv in sorted(rep.items()))))
            print(T("Gespeichert in {0}").format(args.out))
        elif args.cmd == "report":
            print(report_text(load_capture(args.folder)))
        else:
            findings = diff_captures(load_capture(args.a), load_capture(args.b))
            print(diff_text(findings))
            return 2 if any(f[0] == "kritisch" for f in findings) else 0
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
