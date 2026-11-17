"""BMC-Steward: Netzsuche nach BMCs, deren Adresse unbekannt ist (nur lesend, ohne Anmeldung).

Was geschieht (und was nicht):
  - Je Adresse ein TCP-Verbindungsaufbau auf den Webport (Standard 443). Antwortet jemand, wird die
    Redfish-Wurzel /redfish/v1/ gelesen, die laut Standard ohne Anmeldung zugänglich ist (bei iLO 3 und
    frühem iLO 4 ersatzweise /xmldata). Daraus kommen Hersteller, Produkt, Seriennummer/UUID.
  - Optional ein IPMI-Ping (RMCP/ASF-Presence-Ping und „Get Channel Authentication Capabilities“, beides ohne
    Anmeldung, UDP 623): findet BMCs, deren Weboberfläche aus ist.
  - Optional eine SSDP-Suche (Multicast): findet BMCs, die sich selbst ankündigen, auch außerhalb des
    angegebenen Bereichs im selben Segment.
  - Es werden nie Zugangsdaten gesendet, nichts geändert, nichts im Vertrauensspeicher (trust.py)
    abgelegt. Das Zertifikat wird nur als Fingerabdruck gemerkt, damit der Treffer später wiedererkannt wird.

Der Kern kennt kein Qt (außer dem Übersetzer T) und lässt sich deshalb direkt testen.
"""
import concurrent.futures as cf
import http.client
import ipaddress
import json
import re
import select
import socket
import ssl
import struct
import threading
import time
from .i18n import T
from .redfish import CONTEXTS, fingerprint

MAX_TARGETS = 65536             # Obergrenze je Lauf (ein /16)
CONFIRM_ABOVE = 1024            # darüber fragt der Dialog noch einmal nach
DEFAULT_PORTS = (443,)
MAX_WORKERS = 256

# RMCP/ASF-Presence-Ping: RMCP-Kopf (Version 6, Klasse ASF), IANA-Nummer 4542, Typ 0x80, Länge 0
RMCP_PING = bytes([0x06, 0x00, 0xFF, 0x06, 0x00, 0x00, 0x11, 0xBE, 0x80, 0x00, 0x00, 0x00])
RMCP_PONG_PREFIX = bytes([0x06, 0x00, 0xFF, 0x06, 0x00, 0x00, 0x11, 0xBE, 0x40])
# IPMI „Get Channel Authentication Capabilities“ (ohne Sitzung, ohne Anmeldung): antwortet fast jeder BMC mit IPMI
# über LAN, auch wenn er den ASF-Ping ignoriert. Antwort beginnt wie die Anfrage mit dem RMCP-Kopf der Klasse IPMI.
IPMI_CAPS = bytes.fromhex("0600ff07" "00" "00000000" "00000000" "09" "2018c8810038" "8e04b5")
IPMI_REPLY_PREFIX = bytes([0x06, 0x00, 0xFF, 0x07])

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_SEARCH = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
               "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()

VENDOR_NAME = {"dell": "Dell iDRAC", "hpe": "HPE iLO 5/6", "hpe_old": "HPE iLO 3/4", "smc": "Supermicro",
               "lenovo": "Lenovo XCC"}


# ---------------------------------------------------------------------------------------- Zielliste
class TooMany(ValueError):
    """Mehr Adressen als erlaubt."""


def parse_targets(text, limit=MAX_TARGETS):
    """Bereichsangabe -> Liste von IPv4-Adressen (Zeichenketten), ohne Doppelte, in Eingabereihenfolge.

    Erlaubt, getrennt durch Komma, Leerzeichen oder Zeilenumbruch:
      192.168.1.0/24          Netz (ohne Netz- und Broadcast-Adresse, außer bei /31 und /32)
      10.0.0.5                einzelne Adresse
      10.0.0.5-10.0.0.40      Bereich von bis
      10.0.0.5-40             Bereich, nur die letzte Zahl
    ValueError mit lesbarem Text bei Unsinn oder wenn mehr als `limit` Adressen herauskämen."""
    out, seen = [], set()

    def add(addr):
        s = str(addr)
        if s not in seen:
            seen.add(s)
            out.append(s)
            if len(out) > limit:
                raise TooMany(T("Zu viele Adressen (mehr als {0}). Bitte den Bereich verkleinern.").format(limit))

    for part in re.split(r"[,\s;]+", text.strip()):
        if not part:
            continue
        try:
            if "/" in part:
                net = ipaddress.ip_network(part, strict=False)
                if net.version != 4:
                    raise ValueError("v6")
                hosts = net.hosts() if net.prefixlen < 31 else iter(net)
                for a in hosts:
                    add(a)
            elif "-" in part:
                lo, hi = part.split("-", 1)
                lo = ipaddress.IPv4Address(lo)
                hi = ipaddress.IPv4Address(hi if "." in hi else ".".join(str(lo).split(".")[:3] + [hi]))
                if int(hi) < int(lo):
                    raise ValueError("order")
                if int(hi) - int(lo) + 1 > limit:
                    raise TooMany(T("Zu viele Adressen (mehr als {0}). Bitte den Bereich verkleinern.").format(limit))
                for n in range(int(lo), int(hi) + 1):
                    add(ipaddress.IPv4Address(n))
            else:
                add(ipaddress.IPv4Address(part))
        except TooMany:
            raise
        except ValueError:
            raise ValueError(T("Unverständliche Angabe: {0}").format(part)) from None
    return out


def parse_ports(text):
    """„443, 8443“ -> (443, 8443). Leer = Standard. ValueError bei Unsinn."""
    ports = []
    for p in re.split(r"[,\s;]+", (text or "").strip()):
        if p:
            if not p.isdigit() or not 0 < int(p) < 65536:
                raise ValueError(T("Unverständliche Angabe: {0}").format(p))
            if int(p) not in ports:
                ports.append(int(p))
    return tuple(ports) or DEFAULT_PORTS


def suggest_ranges(ifaces, max_prefix_hosts=1024):
    """Vorschläge aus den eigenen Netzen (ipaddress-Interfaces): je Netz die Netzangabe, zu große Netze
    (mehr als max_prefix_hosts Adressen) auf das /24 der eigenen Adresse verkleinert."""
    out = []
    for ifc in ifaces:
        net = ifc.network
        if net.version != 4 or net.is_loopback or net.is_link_local:
            continue
        if net.num_addresses > max_prefix_hosts:
            net = ipaddress.ip_network(f"{ifc.ip}/24", strict=False)
        if str(net) not in out:
            out.append(str(net))
    return out


# ---------------------------------------------------------------------------------------- Erkennung
def classify_root(root):
    """Hersteller, Produkt, Seriennummer/UUID aus der Redfish-Wurzel (dict). Gleiche Regeln wie
    RedfishClient.vendor(), aber ohne Zustand. Gibt (schlüssel, produkt, seriennummer, uuid) zurück;
    Schlüssel „unbekannt“, wenn es kein bekannter BMC ist."""
    if not isinstance(root, dict):
        return "unbekannt", "", "", ""
    oem = root.get("Oem") if isinstance(root.get("Oem"), dict) else {}
    text = (str(root.get("Vendor") or "") + " " + " ".join(oem)).lower()
    product = str(root.get("Product") or "")
    serial = ""
    if "dell" in text:
        key = "dell"
    elif "supermicro" in text or "smc" in text:
        key = "smc"
    elif "lenovo" in text:
        key = "lenovo"
    elif "Hp" in oem:
        key, product = "hpe_old", product or "iLO 4"
    elif "hp" in text or "hewlett" in text:
        key = "hpe"
    else:
        key = "unbekannt"
    hpe = oem.get("Hpe") or oem.get("Hp")
    if isinstance(hpe, dict):                               # HPE nennt Verwaltungsdaten schon in der Wurzel
        mgr = hpe.get("Manager")
        if isinstance(mgr, list) and mgr and isinstance(mgr[0], dict):
            product = product or str(mgr[0].get("Model") or "")
            serial = str(mgr[0].get("SerialNumber") or "")
    return key, product.strip(), serial.strip(), str(root.get("UUID") or "")


def parse_xmldata(text):
    """Geräteauskunft /xmldata (iLO 3/4 ohne Redfish): (schlüssel, produkt, seriennummer). Anderes -> unbekannt."""
    name = re.search(r"<PN>([^<]*)</PN>", text)
    if not name or "Lights-Out" not in name.group(1):
        return "unbekannt", "", ""
    fw = re.search(r"<FWRI>([^<]*)</FWRI>", text)
    short = re.search(r"\((iLO[^)]*)\)", name.group(1))
    sn = re.search(r"<SBSN>([^<]*)</SBSN>", text)
    product = (short.group(1) if short else name.group(1).strip()) + (f" ({fw.group(1)})" if fw else "")
    key = "hpe_old" if re.search(r"iLO ?[34]\b", name.group(1)) else "unbekannt"
    return key, product, (sn.group(1).strip() if sn else "")


def _fetch(ip, port, path, timeout, ctxname):
    """GET ohne Anmeldung. Gibt (Statuscode, Text, Zertifikats-DER) zurück."""
    raw = socket.create_connection((ip, port), timeout=timeout)
    try:
        tls = CONTEXTS[ctxname].wrap_socket(raw, server_hostname=None)
    except Exception:
        raw.close()
        raise
    try:
        der = tls.getpeercert(binary_form=True)
        conn = http.client.HTTPSConnection(ip, port, timeout=timeout)
        conn.sock = tls
        conn.request("GET", path, headers={"Accept": "application/json", "Connection": "close",
                                           "User-Agent": "BMC-Steward-Suche"})
        resp = conn.getresponse()
        body = resp.read(65536).decode("utf-8", "replace")
        return resp.status, body, der
    finally:
        try:
            tls.close()
        except OSError:
            pass


def open_port(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_web(ip, port, timeout=3.0):
    """Fragt einen offenen Webport. Gibt ein Ergebnis-dict, wenn es ein BMC ist (oder ein HTTPS-Gerät ohne
    erkennbaren BMC: Schlüssel „unbekannt“), None, wenn gar kein HTTPS antwortet."""
    for ctxname in ("modern", "old", "oldest"):
        try:
            status, body, der = _fetch(ip, port, "/redfish/v1/", timeout, ctxname)
        except (ssl.SSLError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            continue                                        # Handshake abgelehnt: ältere Stufe versuchen
        except (OSError, http.client.HTTPException):
            return None
        root = None
        if status == 200:
            try:
                root = json.loads(body)
            except ValueError:
                root = None
        key, product, serial, uuid = classify_root(root)
        res = {"ip": ip, "port": port, "vendor": key, "model": product, "serial": serial, "uuid": uuid,
               "redfish": isinstance(root, dict) and "RedfishVersion" in root, "tls": ctxname,
               "fp": fingerprint(der) if der else ""}
        if key == "unbekannt":                              # kein Redfish: vielleicht iLO 3/4
            try:
                st2, txt, _ = _fetch(ip, port, "/xmldata?item=All", timeout, ctxname)
                if st2 == 200:
                    k2, p2, s2 = parse_xmldata(txt)
                    if k2 != "unbekannt":
                        res.update(vendor=k2, model=p2, serial=s2)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                pass
        return res
    return None


# ---------------------------------------------------------------------------------------- IPMI und SSDP
def rmcp_sweep(targets, wait=2.0, stopped=lambda: False, rate=400):
    """Sendet den RMCP-Presence-Ping und die IPMI-Fähigkeitsabfrage an alle Adressen und gibt die Menge der
    antwortenden zurück.
    rate = Pakete pro Sekunde (klein halten: Das Netz soll nicht belastet werden)."""
    found = set()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return found
    sock.setblocking(False)
    wanted = set(targets)
    try:
        def drain(timeout):
            while True:
                r, _, _ = select.select([sock], [], [], timeout)
                if not r:
                    return
                try:
                    data, addr = sock.recvfrom(512)
                except OSError:
                    return
                if addr[0] in wanted and (data.startswith(RMCP_PONG_PREFIX) or data.startswith(IPMI_REPLY_PREFIX)):
                    found.add(addr[0])
                timeout = 0
        gap = 1.0 / max(rate, 1)
        for ip in targets:
            if stopped():
                return found
            for packet in (RMCP_PING, IPMI_CAPS):
                try:
                    sock.sendto(packet, (ip, 623))
                except OSError:
                    pass
            drain(gap)
        end = time.monotonic() + wait
        while time.monotonic() < end and not stopped():
            drain(0.1)
    finally:
        sock.close()
    return found


def ssdp_search(wait=3.0, stopped=lambda: False):
    """Multicast-Suche; gibt {ip: (Server-Zeile, Adresse aus LOCATION)} zurück. Nur Hinweise, keine Gewissheit."""
    found = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setblocking(False)
        sock.sendto(SSDP_SEARCH, SSDP_ADDR)
    except OSError:
        return found
    try:
        end = time.monotonic() + wait
        while time.monotonic() < end and not stopped():
            r, _, _ = select.select([sock], [], [], 0.2)
            if not r:
                continue
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                continue
            text = data.decode("latin-1", "replace")
            server = re.search(r"(?im)^server:\s*(.*)$", text)
            loc = re.search(r"(?im)^location:\s*(.*)$", text)
            found.setdefault(addr[0], ((server.group(1).strip() if server else ""),
                                       (loc.group(1).strip() if loc else "")))
    finally:
        sock.close()
    return found


def arp_table(path="/proc/net/arp"):
    """{ip: mac} aus dem ARP-Zwischenspeicher (nur Linux; sonst leer). Es wird nichts gesendet."""
    out = {}
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            next(f, None)
            for line in f:
                cols = line.split()
                if len(cols) >= 4 and cols[3] != "00:00:00:00:00:00":
                    out[cols[0]] = cols[3].lower()
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------------------- Lauf
def scan(targets, ports=DEFAULT_PORTS, workers=64, connect_timeout=0.8, http_timeout=4.0, ipmi=True, ssdp=False,
         on_found=lambda row: None, on_progress=lambda done, total: None, on_phase=lambda text: None,
         stopped=lambda: False):
    """Sucht BMCs und meldet jeden Treffer sofort über on_found(row). Gibt die Trefferliste (nach Adresse
    sortiert) zurück. row-Schlüssel: ip, port, vendor, model, serial, uuid, redfish, ipmi, mac, fp, note.
    Abbruch über stopped(); bereits gefundene Treffer bleiben erhalten."""
    workers = max(1, min(int(workers), MAX_WORKERS))
    rows = {}
    lock = threading.Lock()
    total = len(targets)
    done = [0]

    def work(ip):
        if stopped():
            return
        for port in ports:
            if stopped():
                return
            if open_port(ip, port, connect_timeout):
                res = probe_web(ip, port, http_timeout)
                if res and (res["vendor"] != "unbekannt"):
                    res.setdefault("ipmi", False)
                    res.update(mac="", note="")
                    with lock:
                        rows[ip] = res
                    on_found(res)
                    return

    on_phase(T("Suche nach Weboberflächen (Port {0}) …").format(", ".join(str(p) for p in ports)))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(work, ip) for ip in targets]
        for f in cf.as_completed(futs):
            try:
                f.result()
            except Exception:
                pass
            done[0] += 1
            on_progress(done[0], total)

    if ipmi and not stopped():
        on_phase(T("Suche nach IPMI (UDP 623) …"))
        for ip in rmcp_sweep(targets, stopped=stopped):
            if ip in rows:
                rows[ip]["ipmi"] = True
                on_found(rows[ip])                          # Zeile aktualisieren
            else:
                rows[ip] = {"ip": ip, "port": 0, "vendor": "ipmi", "model": "", "serial": "", "uuid": "",
                            "redfish": False, "ipmi": True, "mac": "", "fp": "", "note": T("nur IPMI antwortet")}
                on_found(rows[ip])
    if ssdp and not stopped():
        on_phase(T("Suche per SSDP (Multicast) …"))
        for ip, (server, loc) in ssdp_search(stopped=stopped).items():
            if ip in rows:
                continue
            hint = (server + " " + loc).lower()
            if any(w in hint for w in ("idrac", "ilo", "xclarity", "xcc", "supermicro", "ipmi", "bmc", "dell",
                                       "hewlett", "lenovo")):
                rows[ip] = {"ip": ip, "port": 0, "vendor": "unbekannt", "model": server[:60], "serial": "",
                            "uuid": "", "redfish": False, "ipmi": False, "mac": "", "fp": "",
                            "note": T("nur per SSDP gesehen (außerhalb des Bereichs oder ohne Weboberfläche)")}
                on_found(rows[ip])
    arp = arp_table()
    for ip, row in rows.items():
        row["mac"] = arp.get(ip, "")
    result = sorted(rows.values(), key=lambda r: tuple(int(x) for x in r["ip"].split(".")))
    on_phase(T("Fertig: {0} Treffer.").format(len(result)) if not stopped() else T("Abgebrochen."))
    return result


def vendor_label(key):
    return VENDOR_NAME.get(key, T("IPMI (ohne Web)") if key == "ipmi" else T("unbekannt"))


def rows_to_csv(rows):
    """Trefferliste als CSV-Text (Semikolon, wie die Serverliste)."""
    head = ["IP", "Hersteller", "Produkt", "Seriennummer", "UUID", "Redfish", "IPMI", "MAC", "Hinweis"]
    lines = [";".join(head)]
    for r in rows:
        vals = [r["ip"], vendor_label(r["vendor"]), r.get("model", ""), r.get("serial", ""), r.get("uuid", ""),
                "ja" if r.get("redfish") else "nein", "ja" if r.get("ipmi") else "nein", r.get("mac", ""),
                r.get("note", "")]
        lines.append(";".join(str(v).replace(";", ",").replace("\n", " ") for v in vals))
    return "\n".join(lines) + "\n"
