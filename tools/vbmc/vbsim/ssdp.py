"""SSDP-Antwortdienst des virtuellen BMC (UDP 1900).

Echte BMCs melden sich auf M-SEARCH im Netz (Redfish: `urn:dmtf-org:service:redfish-rest:1`), so finden
Werkzeuge sie ohne Adressliste. Hier antwortet jeder virtuelle Server per Unicast mit seiner Redfish-Adresse.
Im Netzlabor (eigene Netzwerkkarte) tritt er zusätzlich der Multicast-Gruppe 239.255.255.250 bei; im
Loopback gibt es kein Multicast, dort lässt sich ein M-SEARCH direkt an die Adresse des Servers schicken.

Port: Umgebungsvariable VBMC_SSDP_PORT (Standard 1900, 0 schaltet den Dienst ab)."""
import os
import socket
import struct
import threading

GROUP = "239.255.255.250"
ST_REDFISH = "urn:dmtf-org:service:redfish-rest:1"


def ssdp_port():
    try:
        return int(os.environ.get("VBMC_SSDP_PORT", "1900"))
    except ValueError:
        return 1900


def ascii_name(x):
    """HTTP-/SSDP-Kopfzeilen bleiben ASCII (Umlaute der Fantasienamen werden umschrieben)."""
    return x.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}))


class Ssdp:
    def __init__(self, bmc, bind_host, port, multicast=False):
        self.bmc, self.stop = bmc, threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind_host, port))
        if multicast:
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                     struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0")))
            except OSError:
                pass
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self):
        self.stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def _location(self):
        b = self.bmc
        if not b.addr:
            return ""                       # noch keine Adresse (DHCP läuft)
        if b.vendor == "hpe_old":
            return f"https://{b.addr}/xmldata?item=all"
        return f"https://{b.addr}/redfish/v1/"

    def reply_for(self, data):
        """Antworttext auf eine M-SEARCH-Anfrage oder None."""
        try:
            text = data.decode("latin-1")
        except Exception:
            return None
        lines = text.split("\r\n")
        if not lines or not lines[0].upper().startswith("M-SEARCH"):
            return None
        hdr = {}
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            if v:
                hdr[k.strip().upper()] = v.strip().strip('"')
        if hdr.get("MAN", "") != "ssdp:discover":
            return None
        st = hdr.get("ST", "")
        b = self.bmc
        redfish = b.vendor != "hpe_old" or b.gen != "ilo3"
        if st in ("ssdp:all", "upnp:rootdevice") or (st == ST_REDFISH and redfish):
            loc = self._location()
            if not loc:
                return None
            ans = ST_REDFISH if (st in ("ssdp:all", ST_REDFISH) and redfish) else "upnp:rootdevice"
            return ("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\n"
                    f"LOCATION: {loc}\r\nSERVER: {ascii_name(b.gen_label)}/{b.fw} UPnP/1.0\r\nST: {ans}\r\n"
                    f"USN: uuid:{b.sim.inv['uuid']}::{ans}\r\n\r\n")
        return None

    def _loop(self):
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                ans = self.reply_for(data)
                if ans:
                    self.sock.sendto(ans.encode("latin-1"), addr)
            except OSError:
                pass
