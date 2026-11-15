"""netlab - echte Netzwerkpräsenz für die virtuellen BMCs von vbmc (nur Linux, braucht root).

Jeder virtuelle Server bekommt einen eigenen Netzwerk-Namespace mit eigener Netzwerkkarte
(eth0), eigener MAC-Adresse (Hersteller-OUI), eigener IP und eigenem DHCP-Client. Er verhält
sich damit im Netz wie ein echter BMC: holt sich per DHCP eine Adresse oder hat ab Werk eine
feste, antwortet auf den Standardports (443/22/623) und zieht beim Umkonfigurieren wirklich
auf die neue Adresse um.

Zwei Aufbauten:

  rz      Rechenzentrum: eine isolierte Linux-Bridge "bmclab0" als Staging-Switch. Dieser
          Rechner hängt mit festen Adressen daran (z. B. 192.168.0.1/24, ohne Gateway), so wie
          die Staging-Karte im RZ. DHCP liefert der DHCP-Reiter von BMC-Steward (dnsmasq auf
          bmclab0) - oder die Server haben eine feste Werks-IP.

  router  Zuhause: jeder Server hängt per macvlan an der Kabel-Netzwerkkarte, holt sich seine
          Adresse vom Router (z. B. FritzBox) und erscheint dort als eigenes Gerät. Weil Linux
          zwischen einer Karte und ihren macvlan-Kindern keinen Verkehr zulässt, bekommt dieser
          Rechner einen eigenen Labor-Anschluss "bmcshim" (ebenfalls macvlan, Adresse vom
          Router) und je virtuellem Server eine /32-Route darüber.

Nur Standardbibliothek; die Befehle laufen über iproute2 ("ip").
"""
import contextlib
import hashlib
import ipaddress
import os
import random
import shutil
import socket
import struct
import subprocess
import threading
import time

NS_PREFIX = "vbmc-"         # Namespaces vbmc-1, vbmc-2, ...
BRIDGE = "bmclab0"          # RZ-Modus: Staging-Switch
SHIM = "bmcshim"            # Router-Modus: Anschluss dieses Rechners an die macvlan-Geräte
HOST_VETH = "vbh-"          # RZ-Modus: hostseitiges Ende der Kabel (vbh-1, ...)
MV_TMP = "vbm-"             # Router-Modus: macvlan vor dem Verschieben in den Namespace

# Hersteller-Präfixe (OUI), damit Router und DHCP-Listen den Hersteller anzeigen
OUI = {"dell": "d0:94:66", "hpe": "94:40:c9", "hpe_old": "94:40:c9",
       "lenovo": "08:94:ef", "smc": "ac:1f:6b"}


def _find_ip():
    return shutil.which("ip") or next(
        (p for p in ("/usr/bin/ip", "/usr/sbin/ip", "/sbin/ip", "/bin/ip") if os.path.exists(p)), None)


IP = _find_ip()


class NetLabError(RuntimeError):
    pass


def sh(*args, ns=None, check=True):
    """iproute2-Befehl ausführen (optional in einem Namespace). Gibt stdout zurück."""
    if not IP:
        raise NetLabError("iproute2 (Befehl \"ip\") fehlt")
    cmd = [IP] + (["-n", ns] if ns else []) + [str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise NetLabError(f"{' '.join(cmd[1:])}: {r.stderr.strip() or r.returncode}")
    return r.stdout


def _sysctl(path, value):
    """Schreibt /proc/sys/...; gibt den alten Wert zurück (None, wenn nicht möglich)."""
    try:
        with open(path) as f:
            old = f.read().strip()
        with open(path, "w") as f:
            f.write(str(value))
        return old
    except OSError:
        return None


def _nm_unmanaged(dev):
    """NetworkManager soll die Labor-Geräte nicht anfassen (falls vorhanden)."""
    nmcli = shutil.which("nmcli")
    if nmcli:
        subprocess.run([nmcli, "device", "set", dev, "managed", "no"],
                       capture_output=True, timeout=5)


def mac_for(vendor, seed):
    """Reproduzierbare MAC: Hersteller-OUI + 3 Bytes aus der Seriennummer (gleiche Seriennummer =
    gleiche MAC = der Router vergibt beim nächsten Lauf wieder dieselbe Adresse)."""
    h = hashlib.sha256(str(seed).encode()).digest()
    return OUI.get(vendor, "02:00:00") + ":" + ":".join(f"{b:02x}" for b in h[:3])


# ------------------------------------------------------------------ Namespaces
_HOST_NS = None


def _host_ns_fd():
    """Netz-Namespace des Hauptprozesses (einmal beim ersten Aufruf aus dem Hauptthread geöffnet)."""
    global _HOST_NS
    if _HOST_NS is None:
        _HOST_NS = os.open("/proc/self/ns/net", os.O_RDONLY)
    return _HOST_NS


@contextlib.contextmanager
def in_netns(name):
    """Alles, was in diesem Block an Sockets angelegt wird, gehört zum Namespace `name`.
    setns() wirkt nur auf den aufrufenden Thread; danach geht es zurück in den Host-Namespace.
    Sockets behalten ihren Namespace, auch wenn sie später aus einem anderen Thread benutzt werden."""
    if not name:
        yield
        return
    host = _host_ns_fd()
    fd = os.open(f"/run/netns/{name}", os.O_RDONLY)
    try:
        os.setns(fd, os.CLONE_NEWNET)
        try:
            yield
        finally:
            os.setns(host, os.CLONE_NEWNET)
    finally:
        os.close(fd)


def enter_netns(name):
    """Für Kindprozesse (IPMI): den ganzen Prozess dauerhaft in den Namespace verlegen."""
    if name:
        fd = os.open(f"/run/netns/{name}", os.O_RDONLY)
        try:
            os.setns(fd, os.CLONE_NEWNET)
        finally:
            os.close(fd)


def wired_parent_ok(dev):
    """Fehlertext, wenn `dev` sich nicht als Elternkarte für macvlan eignet, sonst None."""
    base = f"/sys/class/net/{dev}"
    if not os.path.isdir(base):
        return f"Netzwerkkarte {dev} gibt es nicht."
    if os.path.isdir(base + "/wireless") or os.path.isdir(base + "/phy80211"):
        return (f"{dev} ist eine WLAN-Karte. WLAN-Router nehmen von einem Client keine zusätzlichen "
                "MAC-Adressen an, der Router-Modus braucht die Kabel-Netzwerkkarte.")
    return None


# ------------------------------------------------------------------ Netzlabor
class NetLab:
    def __init__(self, mode, host_addrs=(), parent=None, log=print):
        if mode not in ("rz", "router"):
            raise NetLabError(f"unbekannter Netzlabor-Modus: {mode}")
        self.mode = mode
        self.host_addrs = [ipaddress.ip_interface(a) for a in host_addrs]
        self.parent = parent
        self.log = log
        self.shim_ip = None
        self.shim_dhcp = None
        self._sysctl_restore = []
        self._routes = {}               # Namespace -> /32-Route (Router-Modus)
        self.lock = threading.Lock()

    # -- Auf- und Abbau --------------------------------------------------
    @staticmethod
    def cleanup_stale():
        """Reste eines abgestürzten Laufs entfernen (Namespaces, Bridge, Shim, Kabel)."""
        if not IP:
            return
        out = sh("netns", "list", check=False)
        for line in out.splitlines():
            name = line.split()[0] if line.split() else ""
            if name.startswith(NS_PREFIX):
                sh("netns", "del", name, check=False)
        links = sh("-o", "link", "show", check=False)
        for line in links.splitlines():
            parts = line.split(":")
            if len(parts) > 1:
                dev = parts[1].strip().split("@")[0]
                if dev in (BRIDGE, SHIM) or dev.startswith(HOST_VETH) or dev.startswith(MV_TMP):
                    sh("link", "del", dev, check=False)

    def setup(self):
        if os.geteuid() != 0:
            raise NetLabError("Das Netzlabor braucht root (Start über pkexec oder sudo).")
        if not IP:
            raise NetLabError("iproute2 (Befehl \"ip\") fehlt.")
        _host_ns_fd()
        self.cleanup_stale()
        if self.mode == "rz":
            sh("link", "add", BRIDGE, "type", "bridge")
            _nm_unmanaged(BRIDGE)
            # kein STP-Warten: Ports sind sofort aktiv, wie an einem einfachen Staging-Switch
            sh("link", "set", BRIDGE, "type", "bridge", "stp_state", "0", "forward_delay", "0", check=False)
            sh("link", "set", BRIDGE, "up")
            for a in self.host_addrs:
                sh("addr", "add", str(a), "dev", BRIDGE)
            self.log(f"  [netlab] Staging-Switch {BRIDGE} aktiv, dieser Rechner: "
                     + (", ".join(str(a) for a in self.host_addrs) or "(ohne Adresse)"))
            return
        # Router-Modus
        err = wired_parent_ok(self.parent or "")
        if err:
            raise NetLabError(err)
        sh("link", "add", SHIM, "link", self.parent, "type", "macvlan", "mode", "bridge")
        _nm_unmanaged(SHIM)
        # lokal verwaltete, pro Karte gleichbleibende MAC: der Router gibt dem Anschluss immer dieselbe IP
        h = hashlib.sha256(("shim-" + self.parent).encode()).digest()
        sh("link", "set", SHIM, "address", "02:62:6d:" + ":".join(f"{b:02x}" for b in h[:3]))
        # Nur für die eigenen Adressen auf ARP antworten - sonst melden Karte und Shim abwechselnd
        # ihre MAC für dieselbe IP (ARP-Flattern im Router)
        for dev in (SHIM, self.parent):
            for key, val in (("arp_ignore", 1), ("arp_announce", 2)):
                path = f"/proc/sys/net/ipv4/conf/{dev}/{key}"
                old = _sysctl(path, val)
                if old is not None and dev == self.parent:
                    self._sysctl_restore.append((path, old))
        sh("link", "set", SHIM, "up")
        got = threading.Event()

        def bound(ip, prefix, gw, lease, server):
            with self.lock:
                if self.shim_ip and self.shim_ip != ip:
                    sh("addr", "del", f"{self.shim_ip}/32", "dev", SHIM, check=False)
                self.shim_ip = ip
                sh("addr", "replace", f"{ip}/32", "dev", SHIM)
                for ns, route_ip in self._routes.items():
                    sh("route", "replace", f"{route_ip}/32", "dev", SHIM, "src", ip, check=False)
            got.set()

        self.shim_dhcp = DhcpClient(SHIM, sh_mac(SHIM), ns=None, hostname="bmclab-host",
                                    on_bound=bound, log=self.log)
        self.shim_dhcp.start()
        if not got.wait(20):
            raise NetLabError(f"Der Router hat dem Labor-Anschluss an {self.parent} innerhalb von 20 s "
                              "keine Adresse gegeben. Hängt die Karte am Router, läuft dort DHCP?")
        self.log(f"  [netlab] Labor-Anschluss {SHIM} an {self.parent}: {self.shim_ip}")

    def teardown(self):
        if self.shim_dhcp:
            self.shim_dhcp.stop(release=True)
        self.cleanup_stale()
        for path, old in self._sysctl_restore:
            _sysctl(path, old)
        self._sysctl_restore = []

    # -- je Server -------------------------------------------------------
    def add_server(self, idx, mac):
        """Namespace mit Netzwerkkarte eth0 anlegen. Gibt den Namespace-Namen zurück."""
        ns = f"{NS_PREFIX}{idx}"
        sh("netns", "add", ns)
        sh("link", "set", "lo", "up", ns=ns)
        if self.mode == "rz":
            host_end = f"{HOST_VETH}{idx}"
            sh("link", "add", host_end, "type", "veth", "peer", "name", "eth0", "netns", ns)
            _nm_unmanaged(host_end)
            sh("link", "set", host_end, "master", BRIDGE)
            sh("link", "set", host_end, "up")
        else:
            tmp = f"{MV_TMP}{idx}"
            sh("link", "add", tmp, "link", self.parent, "type", "macvlan", "mode", "bridge")
            sh("link", "set", tmp, "netns", ns)
            sh("link", "set", tmp, "name", "eth0", ns=ns)
        sh("link", "set", "eth0", "address", mac, ns=ns)
        sh("link", "set", "eth0", "up", ns=ns)
        return ns

    def set_address(self, ns, ip, prefix, gw=None):
        """Adresse der Server-Karte setzen (ersetzt die bisherige), wie ein echter BMC beim Umstellen."""
        sh("addr", "flush", "dev", "eth0", "scope", "global", ns=ns, check=False)
        sh("addr", "add", f"{ip}/{prefix}", "dev", "eth0", ns=ns)
        if gw:
            sh("route", "replace", "default", "via", gw, "dev", "eth0", ns=ns, check=False)
        # andere Geräte sofort informieren (wie ein echter BMC nach einem Adresswechsel)
        sh("neigh", "flush", "dev", "eth0", ns=ns, check=False)
        if self.mode == "router":
            with self.lock:
                old = self._routes.get(ns)
                if old and old != ip:
                    sh("route", "del", f"{old}/32", "dev", SHIM, check=False)
                self._routes[ns] = ip
                args = ["route", "replace", f"{ip}/32", "dev", SHIM]
                if self.shim_ip:
                    args += ["src", self.shim_ip]
                sh(*args, check=False)

    def clear_address(self, ns):
        sh("addr", "flush", "dev", "eth0", "scope", "global", ns=ns, check=False)
        if self.mode == "router":
            with self.lock:
                old = self._routes.pop(ns, None)
                if old:
                    sh("route", "del", f"{old}/32", "dev", SHIM, check=False)

    def set_link(self, idx, ns, up):
        """Netzwerkkabel stecken/ziehen. RZ: am Switch-Port, Router: an der Server-Karte."""
        state = "up" if up else "down"
        if self.mode == "rz":
            sh("link", "set", f"{HOST_VETH}{idx}", state, check=False)
        else:
            sh("link", "set", "eth0", state, ns=ns, check=False)

    def remove_server(self, ns):
        self.clear_address(ns)
        sh("netns", "del", ns, check=False)


def sh_mac(dev, ns=None):
    try:
        with open(f"/sys/class/net/{dev}/address") as f:
            if not ns:
                return f.read().strip()
    except OSError:
        pass
    out = sh("-o", "link", "show", "dev", dev, ns=ns, check=False)
    tok = out.split()
    return tok[tok.index("link/ether") + 1] if "link/ether" in tok else "00:00:00:00:00:00"


# ------------------------------------------------------------------ DHCP-Client
MAGIC = b"\x63\x82\x53\x63"
DISCOVER, OFFER, REQUEST, DECLINE, ACK, NAK, RELEASE = 1, 2, 3, 4, 5, 6, 7
ETH_P_IP = 0x0800


def _csum(data):
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def _mac_bytes(mac):
    return bytes(int(x, 16) for x in mac.split(":"))


def build_dhcp(msgtype, xid, mac, ciaddr="0.0.0.0", requested=None, server=None, hostname="", secs=0):
    """DHCP-Nachricht (BOOTP-Teil) bauen."""
    mb = _mac_bytes(mac)
    flags = 0x8000 if ciaddr == "0.0.0.0" else 0       # ohne Adresse: Antwort bitte als Broadcast
    pkt = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 1, 1, 6, 0, xid, secs, flags,
                      socket.inet_aton(ciaddr), b"\0" * 4, b"\0" * 4, b"\0" * 4,
                      mb + b"\0" * 10, b"", b"") + MAGIC
    opts = bytes([53, 1, msgtype]) + bytes([61, 7, 1]) + mb
    if hostname:
        h = hostname.encode()[:63]
        opts += bytes([12, len(h)]) + h
    if requested:
        opts += bytes([50, 4]) + socket.inet_aton(requested)
    if server:
        opts += bytes([54, 4]) + socket.inet_aton(server)
    if msgtype in (DISCOVER, REQUEST):
        opts += bytes([55, 7, 1, 3, 6, 15, 51, 54, 58]) + bytes([57, 2]) + struct.pack("!H", 1500)
    return pkt + opts + b"\xff"


def build_frame(src_mac, dst_mac, src_ip, dst_ip, payload, sport=68, dport=67):
    """Ethernet + IPv4 + UDP um eine DHCP-Nachricht (für einen Raw-Socket ohne IP-Adresse)."""
    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    iph = struct.pack("!BBHHHBBH4s4s", 0x45, 0x10, 20 + len(udp), random.randint(0, 0xFFFF), 0, 64, 17, 0,
                      socket.inet_aton(src_ip), socket.inet_aton(dst_ip))
    iph = iph[:10] + struct.pack("!H", _csum(iph)) + iph[12:]
    return _mac_bytes(dst_mac) + _mac_bytes(src_mac) + struct.pack("!H", ETH_P_IP) + iph + udp


def parse_frame(frame, mac=None, xid=None):
    """DHCP-Antwort aus einem Ethernet-Rahmen lesen. Gibt dict oder None zurück - auch bei kaputten
    Paketen nie eine Ausnahme (im Heimnetz kann jedes Gerät irgendetwas senden)."""
    try:
        return _parse_frame(frame, mac, xid)
    except (IndexError, struct.error, OSError, ValueError, TypeError):
        return None


def _parse_frame(frame, mac=None, xid=None):
    if len(frame) < 14 + 20 + 8 + 240 or frame[12:14] != b"\x08\x00":
        return None
    ip = frame[14:]
    ihl = (ip[0] & 0x0F) * 4
    if ip[9] != 17:
        return None
    udp = ip[ihl:]
    if len(udp) < 8 + 240 or struct.unpack("!H", udp[2:4])[0] != 68:
        return None
    b = udp[8:]
    op, _, _, _, rxid = struct.unpack("!BBBBI", b[:8])
    if op != 2 or (xid is not None and rxid != xid):
        return None
    if mac and b[28:34] != _mac_bytes(mac):
        return None
    if b[236:240] != MAGIC:
        return None
    opts, i = {}, 240
    while i < len(b):
        code = b[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(b):
            break
        ln = b[i + 1]
        opts[code] = b[i + 2:i + 2 + ln]
        i += 2 + ln
    if len(opts.get(53, b"")) != 1:
        return None
    out = {"type": opts[53][0], "yiaddr": socket.inet_ntoa(b[16:20]), "xid": rxid,
           "src_mac": ":".join(f"{x:02x}" for x in frame[6:12])}
    if 1 in opts and len(opts[1]) == 4:
        out["prefix"] = ipaddress.IPv4Network(f"0.0.0.0/{socket.inet_ntoa(opts[1])}").prefixlen
    if 3 in opts and len(opts[3]) >= 4:
        out["router"] = socket.inet_ntoa(opts[3][:4])
    if 51 in opts and len(opts[51]) == 4:
        out["lease"] = struct.unpack("!I", opts[51])[0]
    if 54 in opts and len(opts[54]) == 4:
        out["server"] = socket.inet_ntoa(opts[54])
    if 58 in opts and len(opts[58]) == 4:
        out["t1"] = struct.unpack("!I", opts[58])[0]
    return out


class DhcpClient(threading.Thread):
    """Kleiner DHCP-Client über einen Raw-Socket (wie ein BMC ab Werk): sucht immer wieder einen
    Server, nimmt die Adresse, verlängert sie und gibt sie beim Beenden wieder frei."""

    def __init__(self, ifname, mac, ns=None, hostname="", on_bound=None, on_lost=None, log=print):
        super().__init__(daemon=True)
        self.ifname, self.mac, self.ns, self.hostname = ifname, mac, ns, hostname
        self.on_bound, self.on_lost, self.log = on_bound, on_lost, log
        self.ip = self.server = self.server_mac = None
        self._stop = threading.Event()
        with in_netns(ns):
            self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_IP))
            self.sock.bind((ifname, ETH_P_IP))
        self.sock.settimeout(0.5)

    def _send(self, payload, unicast=False):
        if unicast and self.server_mac and self.ip and self.server:
            frame = build_frame(self.mac, self.server_mac, self.ip, self.server, payload)
        else:
            frame = build_frame(self.mac, "ff:ff:ff:ff:ff:ff", "0.0.0.0", "255.255.255.255", payload)
        try:
            self.sock.send(frame)
        except OSError:
            pass                        # Kabel gezogen o. ä.: wie ein echter Client weiter versuchen

    def _wait(self, xid, types, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self._stop.is_set():
            try:
                frame = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(0.5)
                continue
            r = parse_frame(frame, self.mac, xid)
            if r and r["type"] in types:
                return r
        return None

    def stop(self, release=True):
        if release and self.ip and self.server:
            self._send(build_dhcp(RELEASE, random.getrandbits(32), self.mac, ciaddr=self.ip,
                                  server=self.server), unicast=True)
        self._stop.set()

    def run(self):
        backoff = 2
        while not self._stop.is_set():
            xid = random.getrandbits(32)
            self._send(build_dhcp(DISCOVER, xid, self.mac, hostname=self.hostname))
            offer = self._wait(xid, (OFFER,), 3)
            if not offer:
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 8)
                continue
            backoff = 2
            server = offer.get("server")
            self._send(build_dhcp(REQUEST, xid, self.mac, requested=offer["yiaddr"], server=server,
                                  hostname=self.hostname))
            ack = self._wait(xid, (ACK, NAK), 3)
            if not ack or ack["type"] == NAK:
                continue
            self._bound(ack)
            # Verlängern, solange der Server mitspielt
            while not self._stop.is_set():
                lease = ack.get("lease", 3600)
                if self._stop.wait(max(5, ack.get("t1", lease // 2))):
                    return
                deadline = time.monotonic() + max(5, lease // 2)
                renewed = None
                while not renewed and time.monotonic() < deadline and not self._stop.is_set():
                    xid = random.getrandbits(32)
                    self._send(build_dhcp(REQUEST, xid, self.mac, ciaddr=self.ip, hostname=self.hostname),
                               unicast=True)
                    renewed = self._wait(xid, (ACK, NAK), 4)
                    if not renewed:
                        self._send(build_dhcp(REQUEST, xid, self.mac, ciaddr=self.ip, hostname=self.hostname))
                        renewed = self._wait(xid, (ACK, NAK), 4)
                if self._stop.is_set():
                    return
                if not renewed or renewed["type"] == NAK:
                    self.log(f"  [dhcp] {self.ifname}: Adresse {self.ip} verloren, suche neu")
                    self.ip = None
                    if self.on_lost:
                        self.on_lost()
                    break
                ack = renewed
                self._bound(ack)

    def _bound(self, ack):
        changed = ack["yiaddr"] != self.ip
        self.ip, self.server, self.server_mac = ack["yiaddr"], ack.get("server"), ack["src_mac"]
        if changed and self.on_bound:
            self.on_bound(self.ip, ack.get("prefix", 24), ack.get("router"), ack.get("lease", 3600), self.server)
