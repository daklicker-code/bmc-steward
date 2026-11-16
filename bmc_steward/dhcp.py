"""BMC-Steward: DHCP-Server (Linux dnsmasq / Windows eingebaut) und DHCP-Erkennung."""
from pathlib import Path
from PyQt6.QtCore import (QT_VERSION_STR, QBuffer, QByteArray, QEvent, QIODevice, QLocale, QObject, QProcess, QRect, QSocketNotifier, QThread, QTimer, QUrl, Qt, pyqtSignal)
import glob
import ipaddress
import os
import random
import shutil
import signal
import socket
import struct
import tempfile
import time
from .i18n import T
from .core import WINDOWS, mask




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




# ======================================================================
# Kleiner DHCP-Server (für Windows; unter Linux wird dnsmasq verwendet)
# ======================================================================
class DhcpServer:
    """Vergibt Adressen aus einem Bereich an alle anfragenden Geräte. Nur für ein getrenntes Staging-Netz."""

    MAGIC = b"\x63\x82\x53\x63"

    def __init__(self, ip, mask, lo, hi, log=print, on_leases=lambda rows: None, stopped=lambda: False,
                 port=67, client_port=68, reply_to=None, lease_time=3600,
                 own_mac=None, on_own_lease=lambda row: None):
        self.ip, self.mask = ipaddress.IPv4Address(ip), ipaddress.IPv4Address(mask)
        self.lo, self.hi = ipaddress.IPv4Address(lo), ipaddress.IPv4Address(hi)
        self.log, self.on_leases, self.stopped = log, on_leases, stopped
        self.port, self.client_port, self.reply_to, self.lease_time = port, client_port, reply_to, lease_time
        self.own_mac = own_mac.lower() if own_mac else None
        self.on_own_lease = on_own_lease
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
            if int(ciaddr) and not self.reply_to:        # Verlängerung: direkt antworten
                dest = (str(ciaddr), self.client_port)
            # Eigener Rechner: separat melden, nicht in der Lease-Liste
            if self.own_mac and mac == self.own_mac:
                self.on_own_lease((str(ip), mac, name))
            else:
                self.log(T('Vergeben  {0}  an {1} {2}').format(ip, mac, name))
                self.on_leases(sorted(
                    (str(i), m, n) for m, (i, n) in self.acked.items()
                    if m != self.own_mac
                ))
            return self.reply(data, 5, ip), dest
        if mtype in (4, 7):                              # DECLINE / RELEASE
            self.assigned.pop(mac, None)
            if self.acked.pop(mac, None):
                self.on_leases(sorted(
                    (str(i), m, n) for m, (i, n) in self.acked.items()
                    if m != self.own_mac
                ))
        return None

    def run(self):
        """Läuft bis stopped() wahr wird. Wirft OSError, wenn der Port nicht geöffnet werden kann."""
        # Empfangs-Socket: bind auf 0.0.0.0 verhindert WinError 10049, wenn die DHCP-IP noch
        # nicht als lokales Interface konfiguriert ist (passiert kurz nach dem IP-Setzen).
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Send-Socket: explizit auf die Server-IP binden, damit Windows Broadcast-Pakete
        # über das richtige Interface (Direktverbindung) schickt und nicht über WLAN/LAN.
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            recv_sock.bind(("", self.port))
            recv_sock.settimeout(0.5)

            send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                send_sock.bind((str(self.ip), 0))
            except OSError:
                # IP noch nicht konfiguriert: Fallback auf 0.0.0.0 (Antworten könnten falsches
                # Interface nehmen, aber wenigstens kein Absturz)
                send_sock.bind(("", 0))

            self.log(T('DHCP-Server läuft auf {0}, Bereich {1} bis {2}').format(self.ip, self.lo, self.hi))
            while not self.stopped():
                try:
                    data, _ = recv_sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    continue        # z. B. Windows meldet "Port unreachable" eines früheren Sendens
                try:
                    answer = self.handle(data)
                    if answer:
                        send_sock.sendto(*answer)
                except Exception as e:      # ein kaputtes Paket darf den Server nicht beenden
                    self.log(T('Paket verworfen: {0}').format(e))
        finally:
            recv_sock.close()
            send_sock.close()
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




def _own_mac():
    """Ermittelt die MAC-Adresse des eigenen Rechners (erster verfügbarer Interface-Wert)."""
    import uuid
    n = uuid.getnode()
    return ':'.join(f'{(n >> (i * 8)) & 0xff:02x}' for i in range(5, -1, -1))


class DhcpThread(QThread):
    line = pyqtSignal(str)
    leases = pyqtSignal(list)
    own_lease = pyqtSignal(tuple)   # (ip, mac, name) des eigenen Rechners
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            DhcpServer(log=self.line.emit, on_leases=self.leases.emit,
                       stopped=self.isInterruptionRequested,
                       own_mac=_own_mac(), on_own_lease=self.own_lease.emit,
                       **self.kw).run()
            self.result.emit(0)
        except OSError as e:
            self.line.emit("error: " + T('Port 67 lässt sich nicht öffnen: {0}').format(e))
            self.result.emit(2)
