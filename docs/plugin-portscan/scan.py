"""Portscan-Plugin: Scan-Kern ohne Qt (direkt testbar).

Was geschieht: je Adresse und Port ein TCP-Verbindungsaufbau (connect), der sofort wieder geschlossen wird.
Optional wird ein Banner gelesen, das der Dienst von selbst sendet (z. B. SSH), und ein IPMI-Presence-Ping
(RMCP/ASF, UDP 623) geschickt. Es werden nie Zugangsdaten gesendet und nichts geändert.
"""
import concurrent.futures as cf
import ipaddress
import socket
import threading
import time

MAX_HOSTS = 4096
MAX_PROBES = 262144
CONFIRM_ABOVE = 5000

# Dienste, die auf BMCs und Servern typischerweise lauschen
SERVICES = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 88: "Kerberos", 111: "RPC",
    135: "MS-RPC", 139: "NetBIOS", 161: "SNMP", 389: "LDAP", 427: "SLP", 443: "HTTPS", 445: "SMB",
    623: "IPMI/RMCP", 636: "LDAPS", 664: "ASF-RMCP secure", 902: "VMware", 1433: "MSSQL", 2049: "NFS",
    2068: "Avocent KVM", 3260: "iSCSI", 3306: "MySQL", 3389: "RDP", 3520: "Lenovo IMM", 3668: "Supermicro VM",
    3669: "Supermicro VM", 5120: "Virtual Media", 5122: "Virtual Media", 5123: "Virtual Media",
    5432: "PostgreSQL", 5900: "VNC", 5901: "VNC", 5985: "WinRM", 5986: "WinRM HTTPS", 6443: "Kubernetes",
    7578: "Supermicro iKVM", 8080: "HTTP-Alt", 8443: "HTTPS-Alt", 8889: "Dell vConsole", 9090: "Cockpit",
    17988: "iLO Virtual Media", 17990: "iLO Remote Console",
}

PRESETS = {
    "bmc": (22, 23, 80, 443, 623, 2068, 3520, 3668, 3669, 5120, 5122, 5123, 5900, 7578, 8443, 8889,
            17988, 17990),
    "server": (22, 80, 111, 135, 139, 443, 445, 2049, 3260, 3389, 5900, 5985, 5986, 6443, 8080, 8443, 9090),
    "top": tuple(sorted(SERVICES)),
    "all": "1-65535",
}

RMCP_PING = bytes([0x06, 0x00, 0xFF, 0x06, 0x00, 0x00, 0x11, 0xBE, 0x80, 0x00, 0x00, 0x00])
RMCP_PONG_PREFIX = bytes([0x06, 0x00, 0xFF, 0x06, 0x00, 0x00, 0x11, 0xBE, 0x40])


def parse_ports(text):
    """'22, 80,443 1000-1010 bmc' -> sortiertes Tupel. Vorlagen: bmc, server, top, all."""
    out = set()
    for part in str(text).replace(",", " ").replace(";", " ").split():
        part = part.strip().lower()
        if part in PRESETS:
            preset = PRESETS[part]
            out.update(parse_ports(preset) if isinstance(preset, str) else preset)
            continue
        a, _, b = part.partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            raise ValueError(f"port: {part}") from None
        if not (1 <= lo <= hi <= 65535):
            raise ValueError(f"port: {part}")
        out.update(range(lo, hi + 1))
    if not out:
        raise ValueError("port: –")
    return tuple(sorted(out))


def parse_targets(text, limit=MAX_HOSTS):
    """Adressen, Netze (10.0.0.0/24), Bereiche (10.0.0.5-20 oder 10.0.0.5-10.0.0.20) und Hostnamen.
    Liste ohne Doppelte in Eingabereihenfolge; ValueError bei Unsinn oder mehr als `limit` Adressen."""
    out, seen = [], set()

    def add(addr):
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
            if len(out) > limit:
                raise ValueError(f"> {limit}")

    for part in str(text).replace(",", " ").replace(";", " ").split():
        if "/" in part:
            try:
                net = ipaddress.ip_network(part, strict=False)
            except ValueError:
                raise ValueError(part) from None
            if net.num_addresses > limit + 2:
                raise ValueError(f"> {limit}")
            hosts = list(net.hosts()) or [net.network_address]
            for h in hosts:
                add(str(h))
            continue
        a, sep, b = part.partition("-")
        if sep and a.count(".") == 3 and a.replace(".", "").isdigit():
            try:
                start = ipaddress.IPv4Address(a)
                end = ipaddress.IPv4Address(b) if "." in b else ipaddress.IPv4Address(
                    a.rsplit(".", 1)[0] + "." + b)
            except ValueError:
                raise ValueError(part) from None
            if int(end) < int(start):
                raise ValueError(part)
            if int(end) - int(start) + 1 > limit:
                raise ValueError(f"> {limit}")
            for n in range(int(start), int(end) + 1):
                add(str(ipaddress.IPv4Address(n)))
            continue
        try:
            add(str(ipaddress.ip_address(part)))
        except ValueError:
            if not all(c.isalnum() or c in ".-_" for c in part) or len(part) > 253:
                raise ValueError(part) from None
            add(part)              # Hostname, wird beim Scan aufgelöst
    return out


def service(port, proto="tcp"):
    if proto == "udp" and port == 623:
        return "IPMI (RMCP)"
    return SERVICES.get(port, "")


def probe_tcp(host, port, timeout=0.8, banner=False):
    """(Zustand, Banner): 'offen', 'geschlossen' (RST) oder 'gefiltert' (keine Antwort)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return "unbekannt", ""
    family, _, _, _, addr = infos[0]
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        err = s.connect_ex(addr)
        if err == 0:
            text = ""
            if banner:
                try:
                    s.settimeout(min(1.5, timeout * 2))
                    data = s.recv(256)
                    text = "".join(c if c.isprintable() else " " for c in
                                   data.decode("utf-8", "replace")).strip()[:120]
                except OSError:
                    pass
            return "offen", text
        if err in (111, 10061, 61):   # ECONNREFUSED (Linux, Windows, macOS)
            return "geschlossen", ""
        return "gefiltert", ""
    except socket.timeout:
        return "gefiltert", ""
    except OSError:
        return "gefiltert", ""
    finally:
        s.close()


def probe_ipmi(host, timeout=1.5, port=623):
    """IPMI-Presence-Ping (ohne Anmeldung). 'offen' bei gültiger Antwort, sonst 'gefiltert'."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except (socket.gaierror, UnicodeError):
        return "unbekannt", ""
    family, _, _, _, addr = infos[0]
    s = socket.socket(family, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(RMCP_PING, addr)
        data, _ = s.recvfrom(512)
        if data.startswith(RMCP_PONG_PREFIX):
            return "offen", "ASF Presence Pong"
        return "offen", ""
    except OSError:
        return "gefiltert", ""
    finally:
        s.close()


def scan(targets, ports, workers=64, timeout=0.8, banner=False, ipmi=False, stopped=lambda: False,
         on_result=lambda row: None, on_progress=lambda done, total: None, probe=probe_tcp, probe_udp=probe_ipmi):
    """Prüft alle (Ziel, Port). Jede Antwort geht an on_result(dict: host, port, proto, service, state,
    banner). Gibt die Liste aller Ergebnisse zurück, sortiert nach Ziel-Reihenfolge und Port."""
    jobs = [(h, p, "tcp") for h in targets for p in ports]
    if ipmi:
        jobs += [(h, 623, "udp") for h in targets]
    if len(jobs) > MAX_PROBES:
        raise ValueError(f"> {MAX_PROBES}")
    order = {h: i for i, h in enumerate(targets)}
    results, lock, done = [], threading.Lock(), [0]

    def work(job):
        host, port, proto = job
        if stopped():
            return None
        state, text = probe(host, port, timeout, banner) if proto == "tcp" else probe_udp(host, max(timeout, 1.0))
        return {"host": host, "port": port, "proto": proto, "service": service(port, proto),
                "state": state, "banner": text}

    with cf.ThreadPoolExecutor(max_workers=max(1, min(int(workers), 512))) as pool:
        futures = [pool.submit(work, j) for j in jobs]
        try:
            for fut in cf.as_completed(futures):
                row = fut.result()
                with lock:
                    done[0] += 1
                    n = done[0]
                if row is not None:
                    results.append(row)
                    on_result(row)
                on_progress(n, len(jobs))
                if stopped():
                    for f in futures:
                        f.cancel()
        finally:
            for f in futures:
                f.cancel()
    results.sort(key=lambda r: (order.get(r["host"], 0), r["proto"], r["port"]))
    return results


def to_csv(rows):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["host", "port", "proto", "service", "state", "banner"])
    for r in rows:
        w.writerow([r["host"], r["port"], r["proto"], r["service"], r["state"], r["banner"]])
    return buf.getvalue()


def estimate(targets, ports, workers, timeout, ipmi=False):
    """Grobe Dauer in Sekunden (schlechtester Fall: alles gefiltert)."""
    n = len(targets) * len(ports) + (len(targets) if ipmi else 0)
    return n * timeout / max(1, workers), n


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")
