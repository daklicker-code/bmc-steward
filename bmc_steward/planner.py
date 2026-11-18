"""BMC-Steward: Aufbauplaner – Datenmodell ohne Oberfläche.

Ein Plan besteht aus Racks (Name, Höhe in Höheneinheiten), Geräten (Server, Switches, Firewall, Patchpanel,
PDU, Blindblende) und Kabeln zwischen zwei Ports. Ein Gerät steht in einem Rack ab Höheneinheit `pos`
(von unten gezählt, 1 = unterste HE) und belegt `u` HE nach oben.

Server aus der Serverliste („Neuware“) erscheinen als Bestand (`inv`); sie bleiben beim Ausbauen im Plan
und lassen sich wieder einbauen. Alles andere stammt aus dem Katalog und verschwindet beim Ausbauen.

Hier steht nur Logik (Platz prüfen, Kabel, Prüfung, Netzplan, Stückliste, Ausfall-Simulation, Speichern),
damit sie sich ohne Fenster testen lässt. Die Seite selbst baut planner_tab.py.
"""
import ipaddress
import re

from .i18n import T

FORMAT = "bmc-steward-plan"
VERSION = 1
MIN_RACK_U, MAX_RACK_U, MAX_DEV_U = 4, 60, 10
HE_M = 0.04445                       # eine Höheneinheit in Metern
RACK_PITCH_M = 0.6                   # Abstand zweier Racks in einer Reihe (Rackbreite)
MIN_GRID, MAX_COLS, MAX_ROWS = 2, 104, 99    # Bodenraster: Spalten A … CZ, Reihen 1 … 99
ORIENTS = ("N", "S", "O", "W")             # Rackfront zeigt nach Norden, Süden, Osten oder Westen
TILE_M = 0.6                               # Bodenplatte 600 × 600 mm; ein Rack belegt 1 × 2 Platten
CABLE_LENGTHS = (0.5, 1, 1.5, 2, 3, 5, 7, 10, 15, 20, 30)
PDU_RESERVE = 0.8                    # über 80 % Last bleibt keine Reserve für eine A/B-Einspeisung
VENDORS = ("dell", "hpe", "lenovo", "smc", "gen")
KINDS = ("server", "switch", "fw", "patch", "pdu", "blank")
NETS = ("mgmt", "data", "storage", "uplink", "patch")
PORT_KINDS = ("bmc", "data", "mgmt", "up", "patch")

# Katalog: Schlüssel -> Bauplan. ports = [(Anzahl, Portart, Präfix der Port-Kennung)]
CATALOG = {
    "oob":     dict(kind="switch", role="oob", u=1, w=60, model="OOB-Mgmt 24×1G", prefix="oob",
                    ports=[(22, "mgmt", "p"), (2, "up", "u")]),
    "access":  dict(kind="switch", role="access", u=1, w=120, model="Access 48×1G + 4×SFP+", prefix="acc",
                    ports=[(24, "data", "p"), (4, "up", "u")]),
    "leaf":    dict(kind="switch", role="leaf", u=1, w=350, model="Leaf 48×25G + 8×100G", prefix="leaf",
                    ports=[(20, "data", "p"), (4, "up", "u")]),
    "spine":   dict(kind="switch", role="spine", u=1, w=450, model="Spine 32×100G", prefix="spine",
                    ports=[(16, "up", "u")]),
    "fw":      dict(kind="fw", role="fw", u=1, w=200, model="Firewall 8×10G + 2 WAN", prefix="fw",
                    ports=[(8, "data", "p"), (2, "up", "u")]),
    "patch":   dict(kind="patch", role="patch", u=1, w=0, model="Patchpanel 24×Cat6a", prefix="pp",
                    ports=[(24, "patch", "p")]),
    "pdu":     dict(kind="pdu", role="pdu", u=1, w=0, cap=3700, model="PDU 16 A / 230 V", prefix="pdu", ports=[]),
    "blank":   dict(kind="blank", role="blank", u=1, w=0, model="1 HE", prefix="blende", ports=[]),
    "server1": dict(kind="server", role="server", u=1, w=450, model="", prefix="neu", ports=None),
    "server2": dict(kind="server", role="server", u=2, w=750, model="", prefix="neu", ports=None),
}


def catalog_texts():
    """Anzeigename und Kurzbeschreibung je Katalog-Eintrag in der gewählten Sprache (Reihenfolge = Bibliothek)."""
    return [
        (T("Switches"), [("oob", T("OOB-Mgmt-Switch"), T("24×1G · BMC-Netz")),
                         ("access", T("Access-Switch"), "48×1G + 4×SFP+"),
                         ("leaf", T("Leaf-Switch"), "48×25G + 8×100G"),
                         ("spine", T("Spine-Switch"), "32×100G")]),
        (T("Infrastruktur"), [("fw", T("Firewall"), "8×10G + 2 WAN"),
                              ("patch", T("Patchpanel"), T("24 Ports Cat6a")),
                              ("pdu", T("PDU 16 A"), T("3,7 kW Anschlussleistung")),
                              ("blank", T("Blindblende"), T("Luftführung"))]),
        (T("Geplante Server"), [("server1", T("Neuer Server (1 HE)"), T("Platzhalter, noch nicht geliefert")),
                                ("server2", T("Neuer Server (2 HE)"), T("Platzhalter, noch nicht geliefert"))]),
    ]


def server_ports(u):
    """BMC-Port plus zwei Netzwerkkarten (1 HE) bzw. vier (ab 2 HE)."""
    return [{"id": "bmc", "k": "bmc"}] + [{"id": f"n{i}", "k": "data"} for i in range(1, (4 if u > 1 else 2) + 1)]


def build_ports(spec):
    return [{"id": f"{pre}{i}", "k": kind} for n, kind, pre in spec for i in range(1, n + 1)]


def port_label(pid):
    """Anzeigename einer Port-Kennung: bmc -> BMC, n2 -> NIC 2, p7 -> Port 7, u1 -> Uplink 1."""
    if pid == "bmc":
        return "BMC"
    m = re.fullmatch(r"([a-z]+)(\d+)", pid or "")
    if not m:
        return str(pid)
    pre, num = m.groups()
    if pre == "n":
        return f"NIC {num}"
    if pre == "u":
        return T("Uplink {0}").format(num)
    return T("Port {0}").format(num)


def vendor_of(text):
    """Hersteller aus dem Anzeigetext der Serverliste („Dell iDRAC9“, „HPE iLO 6“ …) oder "gen"."""
    t = (text or "").lower()
    for key, words in (("dell", ("dell", "idrac")), ("hpe", ("hpe", "ilo", "proliant")),
                       ("lenovo", ("lenovo", "xcc", "thinksystem")), ("smc", ("supermicro", "smc", "nutanix"))):
        if any(w in t for w in words):
            return key
    return "gen"


def col_name(i):
    """Spaltenname wie im Rechenzentrum (und in Tabellen): 0 -> A, 25 -> Z, 26 -> AA, 27 -> AB …"""
    name = ""
    i += 1
    while i > 0:
        i, rest = divmod(i - 1, 26)
        name = chr(65 + rest) + name
    return name


def col_index(name):
    """Umkehrung von col_name: "A" -> 0, "AA" -> 26; None bei ungültigem Namen."""
    if not name or not name.isalpha() or not name.isascii():
        return None
    i = 0
    for ch in name.upper():
        i = i * 26 + ord(ch) - 64
    return i - 1


def coord_name(x, y):
    """Koordinate einer Bodenplatte, z. B. (2, 6) -> "C07"."""
    return f"{col_name(x)}{y + 1:02d}"


def parse_coord(text):
    """"C07", "c7" oder "AA12" -> (Spalte, Reihe) mit Zählung ab 0; None, wenn es keine Koordinate ist."""
    m = re.fullmatch(r"\s*([A-Za-z]{1,3})\s*-?\s*(\d{1,3})\s*", text or "")
    if not m:
        return None
    x, y = col_index(m.group(1)), int(m.group(2)) - 1
    return (x, y) if x is not None and y >= 0 else None


def rack_tiles(x, y, orient):
    """Bodenplatten eines Racks (600 × 1200 mm): zwei Platten in Blickrichtung der Front."""
    return {(x, y), (x, y + 1)} if orient in ("N", "S") else {(x, y), (x + 1, y)}


def round_length(m):
    return next((x for x in CABLE_LENGTHS if x >= m - 1e-9), float(int(m) + 1))


class Plan:
    """Racks, Geräte und Kabel eines Aufbaus."""

    def __init__(self):
        self.name = ""
        self.racks = []
        self.devs = []
        self.cables = []
        self.seq = 0
        self.ip_start = "10.20.0.11"
        self.mask = "255.255.255.0"
        self.gw = "10.20.0.1"
        self.schema = "{name}"
        self.site, self.room, self.row = "", "", ""     # Standort › Raum › Reihe
        self.cols, self.rows = 26, 16                   # Bodenraster des Raums (Spalten A …, Reihen 1 …)

    # ------------------------------------------------------------ Grundlagen
    def _id(self, prefix):
        self.seq += 1
        return f"{prefix}{self.seq}"

    def rack(self, rid):
        return next((r for r in self.racks if r["id"] == rid), None)

    def dev(self, did):
        return next((d for d in self.devs if d["id"] == did), None)

    def cable(self, cid):
        return next((c for c in self.cables if c["id"] == cid), None)

    def placed(self, kind=None):
        return [d for d in self.devs if d.get("rack") and (kind is None or d["kind"] == kind)]

    def rack_index(self, rid):
        return next((i for i, r in enumerate(self.racks) if r["id"] == rid), -1)

    # ------------------------------------------------------------ Racks
    def add_rack(self, name=None, h=42, at=None):
        h = max(MIN_RACK_U, min(MAX_RACK_U, int(h)))
        if not name:
            n = len(self.racks) + 1
            names = {r["name"] for r in self.racks}
            while f"R{n:02d}" in names:
                n += 1
            name = f"R{n:02d}"
        r = {"id": self._id("r"), "name": str(name)[:20], "h": h, "x": None, "y": None, "orient": "S"}
        self.racks.append(r)
        if at is not None and self.can_stand(r["id"], at[0], at[1], r["orient"]):
            r["x"], r["y"] = at
        else:
            spot = self.free_spot()
            if spot:
                r["x"], r["y"] = spot
        return r

    # ------------------------------------------------------------ Grundriss
    def coord(self, r):
        return coord_name(r["x"], r["y"]) if r.get("x") is not None else "–"

    def occupied(self, ignore=None):
        tiles = {}
        for r in self.racks:
            if r["id"] != ignore and r.get("x") is not None:
                for t in rack_tiles(r["x"], r["y"], r["orient"]):
                    tiles[t] = r["id"]
        return tiles

    def can_stand(self, rid, x, y, orient):
        """Passt das Rack mit dieser Ausrichtung auf Platte (x, y), ohne über den Raum hinaus oder auf ein
        anderes Rack zu ragen?"""
        tiles = rack_tiles(x, y, orient)
        if any(not (0 <= tx < self.cols and 0 <= ty < self.rows) for tx, ty in tiles):
            return False
        busy = self.occupied(ignore=rid)
        return not any(t in busy for t in tiles)

    def free_spot(self, orient="S"):
        """Nächster freier Stellplatz: Racks stehen nebeneinander in einer Reihe, ab Platte C03."""
        last = [r for r in self.racks if r.get("x") is not None]
        if last:
            r = last[-1]
            cand = (r["x"] + (1 if r["orient"] in ("N", "S") else 0), r["y"] + (0 if r["orient"] in ("N", "S") else 1))
            if self.can_stand(None, cand[0], cand[1], orient):
                return cand
        start = (min(2, self.cols - 1), min(2, self.rows - 2))
        order = [(x, y) for y in range(self.rows) for x in range(self.cols)]
        order.sort(key=lambda t: (t[1] < start[1], t[1], t[0] < start[0], t[0]))
        return next((t for t in order if self.can_stand(None, t[0], t[1], orient)), None)

    def move_rack(self, rid, x, y, orient=None):
        r = self.rack(rid)
        orient = orient or r["orient"]
        if not self.can_stand(rid, x, y, orient):
            return False
        r["x"], r["y"], r["orient"] = x, y, orient
        return True

    def set_grid(self, cols, rows):
        """Neue Rastergröße; gibt die Racks zurück, die dann außerhalb stünden (dann bleibt alles, wie es war)."""
        cols = max(MIN_GRID, min(MAX_COLS, int(cols)))
        rows = max(MIN_GRID, min(MAX_ROWS, int(rows)))
        out = [r for r in self.racks if r.get("x") is not None and any(
            tx >= cols or ty >= rows for tx, ty in rack_tiles(r["x"], r["y"], r["orient"]))]
        if not out:
            self.cols, self.rows = cols, rows
        return out

    def remove_rack(self, rid):
        """Nur leere Racks lassen sich entfernen; gibt False zurück, wenn noch Geräte darin stehen."""
        if any(d.get("rack") == rid for d in self.devs):
            return False
        self.racks = [r for r in self.racks if r["id"] != rid]
        return True

    def set_rack_height(self, rid, h):
        """Neue Höhe; gibt die Geräte zurück, die darüber hinausragen würden (dann bleibt alles, wie es war)."""
        r = self.rack(rid)
        h = max(MIN_RACK_U, min(MAX_RACK_U, int(h)))
        over = [d for d in self.devs if d.get("rack") == rid and d["pos"] + d["u"] - 1 > h]
        if not over:
            r["h"] = h
        return over

    # ------------------------------------------------------------ Geräte
    def new_device(self, key):
        """Gerät aus dem Katalog, noch nicht eingebaut (erst place() nimmt es in den Plan auf)."""
        c = CATALOG[key]
        same = [d for d in self.devs if d.get("cat") == key or d.get("prefix") == c["prefix"]]
        n = len(same) + 1
        names = {d["name"] for d in self.devs}
        while f"{c['prefix']}-{n:02d}" in names:
            n += 1
        d = {"id": self._id("d"), "cat": key, "prefix": c["prefix"], "kind": c["kind"], "role": c["role"],
             "name": f"{c['prefix']}-{n:02d}", "model": c["model"], "u": c["u"], "w": c["w"], "cap": c.get("cap", 0),
             "vendor": "gen" if c["kind"] == "server" else "", "serial": "", "inv": False,
             "ports": server_ports(c["u"]) if c["ports"] is None else build_ports(c["ports"]),
             "rack": None, "pos": None}
        return d

    def sync_inventory(self, entries):
        """Gleicht den Bestand mit der Serverliste ab. entries: [{key, name, vendor, model, serial}].
        Neue Server kommen nicht eingebaut dazu, verschwundene nicht eingebaute fallen weg; eingebaute
        bleiben stehen (Merker `missing`), damit kein Plan durch eine geänderte Liste kaputtgeht."""
        keys = set()
        for e in entries:
            key = str(e.get("key") or "").strip()
            if not key or key in keys:
                continue
            keys.add(key)
            d = next((x for x in self.devs if x.get("inv") and x.get("key") == key), None)
            if d is None:
                d = {"id": self._id("i"), "inv": True, "key": key, "kind": "server", "role": "server", "cat": None, "prefix": "",
                     "u": 1, "w": 500, "cap": 0, "ports": server_ports(1), "rack": None, "pos": None}
                self.devs.append(d)
            d["name"] = str(e.get("name") or key)[:40]
            d["vendor"] = e.get("vendor") if e.get("vendor") in VENDORS else "gen"
            d["model"] = str(e.get("model") or "")[:60]
            d["serial"] = str(e.get("serial") or "")[:40]
            d.pop("missing", None)
        for d in list(self.devs):
            if d.get("inv") and d.get("key") not in keys:
                if d.get("rack"):
                    d["missing"] = True
                else:
                    self.devs.remove(d)

    def fits(self, rid, pos, u, ignore=None):
        r = self.rack(rid)
        if r is None or pos is None or pos < 1 or pos + u - 1 > r["h"]:
            return False
        return not any(d["id"] != ignore and d.get("rack") == rid and pos <= d["pos"] + d["u"] - 1
                       and d["pos"] <= pos + u - 1 for d in self.devs)

    def place(self, d, rid, pos):
        """Baut ein Gerät ein (oder setzt es um). False, wenn der Platz nicht reicht."""
        if not self.fits(rid, pos, d["u"], d["id"]):
            return False
        d["rack"], d["pos"] = rid, int(pos)
        if d not in self.devs:
            self.devs.append(d)
        return True

    def free_pos(self, rid, u, top=True):
        """Erste freie Position für `u` HE, von oben (top) oder von unten gesucht; sonst None."""
        r = self.rack(rid)
        if r is None:
            return None
        rng = range(r["h"] - u + 1, 0, -1) if top else range(1, r["h"] - u + 2)
        return next((p for p in rng if self.fits(rid, p, u)), None)

    def unplace(self, d):
        """Baut aus und zieht alle Kabel ab. Bestand bleibt im Plan, Katalog-Geräte fallen weg."""
        self.cables = [c for c in self.cables if c["a"][0] != d["id"] and c["b"][0] != d["id"]]
        if d.get("inv"):
            d["rack"] = d["pos"] = None
        elif d in self.devs:
            self.devs.remove(d)

    def set_height(self, d, u):
        """Neue Einbauhöhe. False, wenn sie an der jetzigen Stelle nicht passt."""
        u = max(1, min(MAX_DEV_U, int(u)))
        if d.get("rack") and not self.fits(d["rack"], d["pos"], u, d["id"]):
            return False
        d["u"] = u
        if d["kind"] == "server":
            want = server_ports(u)
            ids = {p["id"] for p in want}
            self.cables = [c for c in self.cables if not ((c["a"][0] == d["id"] and c["a"][1] not in ids)
                                                         or (c["b"][0] == d["id"] and c["b"][1] not in ids))]
            d["ports"] = want
        return True

    # ------------------------------------------------------------ Kabel
    @staticmethod
    def port(d, pid):
        return next((p for p in d["ports"] if p["id"] == pid), None)

    def cable_at(self, did, pid):
        return next((c for c in self.cables if tuple(c["a"]) == (did, pid) or tuple(c["b"]) == (did, pid)), None)

    @staticmethod
    def other_end(c, did, pid):
        return tuple(c["b"]) if tuple(c["a"]) == (did, pid) else tuple(c["a"])

    def auto_net(self, a, ap, b, bp):
        ka = self.port(self.dev(a), ap)["k"]
        kb = self.port(self.dev(b), bp)["k"]
        kinds = {ka, kb}
        if kinds & {"bmc", "mgmt"}:
            return "mgmt"
        if "patch" in kinds:
            return "patch"
        if "up" in kinds:
            return "uplink"
        return "data"

    def connect(self, a, ap, b, bp, net=None):
        """Kabel zwischen zwei Ports. Gibt (Kabel, None) oder (None, Fehlertext) zurück."""
        da, db = self.dev(a), self.dev(b)
        if not da or not db or not self.port(da, ap) or not self.port(db, bp):
            return None, T("Unbekannter Port.")
        if a == b:
            return None, T("Ein Gerät lässt sich nicht mit sich selbst verkabeln.")
        if not da.get("rack") or not db.get("rack"):
            return None, T("Beide Geräte müssen eingebaut sein.")
        for did, pid in ((a, ap), (b, bp)):
            if self.cable_at(did, pid):
                return None, T("{0} · {1} ist schon belegt.").format(self.dev(did)["name"], port_label(pid))
        c = {"id": self._id("c"), "a": [a, ap], "b": [b, bp],
             "net": net if net in NETS else self.auto_net(a, ap, b, bp)}
        self.cables.append(c)
        return c, None

    def disconnect(self, cid):
        self.cables = [c for c in self.cables if c["id"] != cid]

    def cable_length(self, c):
        """Geschätzte Länge in Metern, auf Handelslängen aufgerundet: im Rack über die HE-Differenz, zwischen
        Racks über die Rackoberseite (Kabelkanal) plus Reserve."""
        A, B = self.dev(c["a"][0]), self.dev(c["b"][0])
        if A["rack"] == B["rack"]:
            m = abs(A["pos"] - B["pos"]) * HE_M + 0.6
        else:
            ra, rb = self.rack(A["rack"]), self.rack(B["rack"])
            if ra.get("x") is not None and rb.get("x") is not None:     # Weg über den Grundriss
                gap = (abs(ra["x"] - rb["x"]) + abs(ra["y"] - rb["y"])) * TILE_M
            else:
                gap = abs(self.rack_index(A["rack"]) - self.rack_index(B["rack"])) * RACK_PITCH_M
            m = (gap
                 + (ra["h"] - A["pos"] + rb["h"] - B["pos"]) * HE_M + 1.2)
        return round_length(m)

    def medium(self, net):
        return {"mgmt": T("Cat6a-Patchkabel"), "patch": T("Cat6a-Patchkabel"), "data": T("DAC-Kabel 25G SFP28"),
                "storage": T("DAC-Kabel 25G SFP28"), "uplink": T("AOC/LWL 100G QSFP28")}[net]

    def auto_cable(self):
        """Verkabelt offene Server: BMC an den ersten OOB-Switch, NIC 1/2 abwechselnd an Leaf/Access-Switches
        (NIC 1 an den ersten, NIC 2 an den zweiten). Gibt die Zahl neuer Kabel zurück."""
        oob = next((d for d in self.placed("switch") if d["role"] == "oob"), None)
        fabric = [d for d in self.placed("switch") if d["role"] in ("leaf", "access")]

        def free(sw, kind):
            return next((p["id"] for p in sw["ports"] if p["k"] == kind and not self.cable_at(sw["id"], p["id"])), None)

        n = 0
        for s in sorted(self.placed("server"), key=lambda d: (self.rack_index(d["rack"]), -d["pos"])):
            if oob and not self.cable_at(s["id"], "bmc"):
                p = free(oob, "mgmt")
                if p and self.connect(s["id"], "bmc", oob["id"], p)[0]:
                    n += 1
            for i, nic in enumerate(("n1", "n2")):
                if not fabric or self.cable_at(s["id"], nic):
                    continue
                sw = fabric[i % len(fabric)]
                p = free(sw, "data")
                if p and self.connect(s["id"], nic, sw["id"], p)[0]:
                    n += 1
        return n

    def apply_template(self, key, rid):
        """Vorlage in ein Rack setzen. Gibt (neue Geräte, Fehlertext oder None) zurück."""
        r = self.rack(rid)
        if r is None:
            return [], T("Zuerst ein Rack anlegen oder auswählen.")
        made = []
        for cat in {"head": ("patch", "oob"), "ha": ("leaf", "leaf")}[key]:
            d = self.new_device(cat)
            pos = self.free_pos(rid, d["u"])
            if pos is None:
                for x in made:
                    self.unplace(x)
                return [], T("Im Rack {0} ist nicht genug Platz.").format(r["name"])
            self.place(d, rid, pos)
            made.append(d)
        if key == "head" and not any(d["kind"] == "pdu" and d["rack"] == rid for d in self.devs):
            p = self.new_device("pdu")
            pos = self.free_pos(rid, 1, top=False)
            if pos is not None:
                self.place(p, rid, pos)
                made.append(p)
        if key == "ha":
            self.connect(made[0]["id"], "u1", made[1]["id"], "u1")
            self.connect(made[0]["id"], "u2", made[1]["id"], "u2")
        return made, None

    # ------------------------------------------------------------ Auswertung
    def rack_load(self, rid):
        ds = [d for d in self.devs if d.get("rack") == rid]
        return {"used": sum(d["u"] for d in ds), "w": sum(d.get("w") or 0 for d in ds),
                "cap": sum(d.get("cap") or 0 for d in ds)}

    def checks(self):
        """Auffälligkeiten des Plans: [(Stufe err|warn|info, Text, Geräte-ID oder None)]."""
        out = []
        kw = lambda w: f"{w / 1000:.1f} kW"
        for r in self.racks:
            L = self.rack_load(r["id"])
            if L["w"] and not L["cap"]:
                out.append(("warn", T("Rack {0}: keine PDU eingeplant ({1} Last)").format(r["name"], kw(L["w"])), None))
            elif L["cap"] and L["w"] > L["cap"]:
                out.append(("err", T("Rack {0}: PDU überlastet ({1} bei {2})").format(r["name"], kw(L["w"]),
                                                                                     kw(L["cap"])), None))
            elif L["cap"] and L["w"] > L["cap"] * PDU_RESERVE:
                out.append(("warn", T("Rack {0}: PDU über 80 % – keine Reserve für A/B-Einspeisung").format(r["name"]),
                            None))
            if r.get("x") is None:
                out.append(("warn", T("Rack {0}: kein Stellplatz im Grundriss").format(r["name"]), None))
            if L["used"] > r["h"] * 0.9:
                out.append(("info", T("Rack {0} ist fast voll ({1} HE frei)").format(r["name"], r["h"] - L["used"]),
                            None))
        for d in self.placed("server"):
            if d.get("missing"):
                out.append(("warn", T("{0}: steht nicht mehr in der Serverliste").format(d["name"]), d["id"]))
            if not self.cable_at(d["id"], "bmc"):
                out.append(("warn", T("{0}: BMC nicht verkabelt – nicht erreichbar").format(d["name"]), d["id"]))
            nics = [c for c in (self.cable_at(d["id"], p["id"]) for p in d["ports"] if p["k"] == "data")
                    if c and c["net"] != "storage"]
            if not nics:
                out.append(("warn", T("{0}: keine Datenverbindung").format(d["name"]), d["id"]))
            elif len(nics) == 1:
                out.append(("info", T("{0}: nur eine Datenleitung, keine Redundanz").format(d["name"]), d["id"]))
            else:
                peers = {self.other_end(c, d["id"], c["a"][1] if c["a"][0] == d["id"] else c["b"][1])[0]
                         for c in nics}
                if len(peers) == 1:
                    out.append(("info", T("{0}: beide Datenleitungen am selben Switch").format(d["name"]), d["id"]))
        switches = self.placed("switch")
        if len(switches) > 1:
            for s in switches:
                if not any(self.cable_at(s["id"], p["id"]) for p in s["ports"] if p["k"] == "up"):
                    out.append(("info", T("{0}: kein Uplink").format(s["name"]), s["id"]))
        open_inv = sum(1 for d in self.devs if d.get("inv") and not d.get("rack"))
        if open_inv:
            out.append(("info", T("{0} Server aus der Serverliste noch nicht eingebaut").format(open_inv), None))
        if not self.net_plan_ok():
            out.append(("err", T("Erste BMC-IP „{0}“ ist keine gültige Adresse").format(self.ip_start), None))
        order = {"err": 0, "warn": 1, "info": 2}
        return sorted(out, key=lambda x: order[x[0]])

    def net_plan_ok(self):
        try:
            ipaddress.IPv4Address(self.ip_start)
            return True
        except ValueError:
            return False

    def hostname(self, d):
        r = self.rack(d["rack"])
        h = (self.schema or "{name}").replace("{rack}", r["name"].lower()).replace("{pos}", f"{d['pos']:02d}") \
            .replace("{name}", d["name"])
        h = re.sub(r"[^A-Za-z0-9-]+", "-", h).strip("-").lower()
        return h[:63]

    def net_plan(self):
        """Je eingebautem Server: Hostname nach Schema, BMC-IP (fortlaufend ab ip_start) und Switchport."""
        servers = sorted(self.placed("server"), key=lambda d: (self.rack_index(d["rack"]), -d["pos"]))
        try:
            base = ipaddress.IPv4Address(self.ip_start)
        except ValueError:
            base = None
        out = []
        for i, d in enumerate(servers):
            c = self.cable_at(d["id"], "bmc")
            sp = None
            if c:
                od, op = self.other_end(c, d["id"], "bmc")
                sp = f"{self.dev(od)['name']} · {port_label(op)}"
            ip = None
            if base is not None:
                try:
                    ip = str(base + i)
                except ipaddress.AddressValueError:
                    ip = None
            out.append({"dev": d, "host": self.hostname(d), "ip": ip, "port": sp})
        return out

    def bom(self):
        """Stückliste: [(Gruppe, Artikel, Länge oder "", Anzahl)]."""
        rows = []
        devs = {}
        for d in self.placed():
            key = T("Server aus der Serverliste") if d.get("inv") else (d["model"] or d["name"])
            devs[key] = devs.get(key, 0) + 1
        rows += [(T("Geräte"), k, "", n) for k, n in sorted(devs.items())]
        cab = {}
        trx = 0
        for c in self.cables:
            k = (self.medium(c["net"]), self.cable_length(c))
            cab[k] = cab.get(k, 0) + 1
            if c["net"] == "uplink":
                trx += 2
        rows += [(T("Kabel"), m, f"{l:g} m".replace(".", ","), n) for (m, l), n in sorted(cab.items())]
        if trx:
            rows.append((T("Kabel"), T("Transceiver QSFP28"), "", trx))
        hu = sum(d["u"] for d in self.placed() if d["kind"] != "blank")
        if hu:
            rows.append((T("Montage"), T("Käfigmuttern und Schrauben"), "", hu * 4))
        if self.cables:
            rows.append((T("Montage"), T("Kabeletiketten"), "", len(self.cables) * 2))
        return rows

    def simulate(self, broken=(), failed=()):
        """Ausfall-Simulation: Zustand je Gerät ("ok", "degraded", "down") bei gezogenen Kabeln (broken) und
        ausgefallenen Geräten (failed). Ein Server ist erreichbar, solange sein BMC-Kabel steckt und das
        Gegenstück läuft; fällt ein Teil seiner Datenleitungen weg, ist er „eingeschränkt“."""
        broken, failed = set(broken), set(failed)

        def alive(c):
            return c["id"] not in broken and c["a"][0] not in failed and c["b"][0] not in failed

        state = {}
        for d in self.placed():
            if d["id"] in failed:
                state[d["id"]] = "down"
            elif d["kind"] != "server":
                state[d["id"]] = "ok"
            else:
                b = self.cable_at(d["id"], "bmc")
                if not b or not alive(b):
                    state[d["id"]] = "down"
                    continue
                links = [c for c in (self.cable_at(d["id"], p["id"]) for p in d["ports"] if p["k"] == "data") if c]
                up = [c for c in links if alive(c)]
                state[d["id"]] = "degraded" if links and len(up) < len(links) else "ok"
        return state

    # ------------------------------------------------------------ Speichern / Laden
    def to_dict(self):
        return {"format": FORMAT, "version": VERSION, "name": self.name, "seq": self.seq, "racks": self.racks,
                "devices": self.devs, "cables": self.cables,
                "net": {"ip_start": self.ip_start, "mask": self.mask, "gw": self.gw, "schema": self.schema},
                "location": {"site": self.site, "room": self.room, "row": self.row},
                "floor": {"cols": self.cols, "rows": self.rows}}

    @classmethod
    def from_dict(cls, data):
        """Liest einen gespeicherten Plan; unbrauchbare Einträge werden übersprungen statt geraten.
        Wirft ValueError, wenn es gar kein Plan ist."""
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError(T("Keine Plan-Datei von BMC-Steward."))
        p = cls()
        p.name = str(data.get("name") or "")[:60]
        loc = data.get("location") if isinstance(data.get("location"), dict) else {}
        for k in ("site", "room", "row"):
            if isinstance(loc.get(k), str):
                setattr(p, k, loc[k][:40])
        net = data.get("net") if isinstance(data.get("net"), dict) else {}
        for k in ("ip_start", "mask", "gw", "schema"):
            if isinstance(net.get(k), str):
                setattr(p, k, net[k][:64])
        ids = set()
        floor = data.get("floor") if isinstance(data.get("floor"), dict) else {}
        try:
            p.cols = max(MIN_GRID, min(MAX_COLS, int(floor.get("cols", p.cols))))
            p.rows = max(MIN_GRID, min(MAX_ROWS, int(floor.get("rows", p.rows))))
        except (TypeError, ValueError):
            pass
        for r in data.get("racks") or []:
            try:
                rid, h = str(r["id"]), int(r["h"])
            except (KeyError, TypeError, ValueError):
                continue
            if rid in ids:
                continue
            ids.add(rid)
            rack = {"id": rid, "name": str(r.get("name") or rid)[:20], "h": max(MIN_RACK_U, min(MAX_RACK_U, h)),
                    "x": None, "y": None, "orient": r.get("orient") if r.get("orient") in ORIENTS else "S"}
            p.racks.append(rack)
            x, y = r.get("x"), r.get("y")
            if (isinstance(x, int) and isinstance(y, int) and not isinstance(x, bool) and not isinstance(y, bool)
                    and p.can_stand(rid, x, y, rack["orient"])):
                rack["x"], rack["y"] = x, y
            else:
                spot = p.free_spot(rack["orient"])
                if spot:
                    rack["x"], rack["y"] = spot
        for d in data.get("devices") or []:
            try:
                did, kind, u = str(d["id"]), str(d["kind"]), int(d["u"])
            except (KeyError, TypeError, ValueError):
                continue
            if did in ids or kind not in KINDS or not 1 <= u <= MAX_DEV_U:
                continue
            ports = [x for x in (d.get("ports") or []) if isinstance(x, dict) and isinstance(x.get("id"), str)
                     and x.get("k") in PORT_KINDS]
            if kind == "server":
                ports = server_ports(u)
            dev = {"id": did, "kind": kind, "u": u, "ports": ports,
                   "role": str(d.get("role") or kind), "cat": d.get("cat") if d.get("cat") in CATALOG else None,
                   "prefix": str(d.get("prefix") or ""), "name": str(d.get("name") or did)[:40],
                   "model": str(d.get("model") or "")[:60], "vendor": d.get("vendor") if d.get("vendor") in VENDORS else "",
                   "serial": str(d.get("serial") or "")[:40], "inv": bool(d.get("inv")),
                   "w": max(0, int(d.get("w") or 0)) if str(d.get("w") or 0).isdigit() else 0,
                   "cap": max(0, int(d.get("cap") or 0)) if str(d.get("cap") or 0).isdigit() else 0,
                   "rack": None, "pos": None}
            if dev["inv"]:
                dev["key"] = str(d.get("key") or did)
            ids.add(did)
            p.devs.append(dev)
            rid, pos = d.get("rack"), d.get("pos")
            if rid and isinstance(pos, int) and not isinstance(pos, bool):
                p.place(dev, str(rid), pos)
            if not dev["rack"] and not dev["inv"]:
                p.devs.remove(dev)                  # Katalog-Geräte gibt es nur eingebaut
        for c in data.get("cables") or []:
            try:
                (a, ap), (b, bp) = c["a"], c["b"]
            except (KeyError, TypeError, ValueError):
                continue
            new, _ = p.connect(str(a), str(ap), str(b), str(bp), c.get("net"))
            if new and isinstance(c.get("id"), str) and c["id"] not in ids:
                new["id"] = c["id"]
                ids.add(c["id"])
        nums = [int(m.group(1)) for x in ids for m in [re.search(r"(\d+)$", x)] if m]
        p.seq = max([p.seq, int(data.get("seq") or 0) if str(data.get("seq") or 0).isdigit() else 0] + nums)
        return p


def demo_plan(inventory):
    """Beispiel zum Ausprobieren: zwei Racks, Management-Kopf, zwei Leafs, Firewall, PDU; die ersten Server
    der Serverliste werden eingebaut und verkabelt."""
    p = Plan()
    p.name = T("Beispiel")
    p.site, p.room, p.row = "FRA", T("Raum 2"), "C"
    p.cols, p.rows = 20, 12
    r1 = p.add_rack("C04", 42, at=(2, 3))                  # Racks heißen wie ihr Stellplatz
    r2 = p.add_rack("D04", 24, at=(3, 3))
    for x in (4, 5):
        p.add_rack(coord_name(x, 3), 42, at=(x, 3))
    for x in range(2, 6):                                  # gegenüber, Fronten zum Kaltgang
        r = p.add_rack(coord_name(x, 7), 42, at=(x, 7))
        r["orient"] = "N"
    p.sync_inventory(inventory)
    pos = 42
    for key in ("patch", "oob", "leaf", "leaf", "fw"):
        d = p.new_device(key)
        p.place(d, r1["id"], pos)
        pos -= 1
    for rid in (r1["id"], r2["id"]):
        p.place(p.new_device("pdu"), rid, 1)
    pos = 35
    for d in [x for x in p.devs if x.get("inv")][:4]:
        if pos - d["u"] + 1 < 2:
            break
        p.place(d, r1["id"], pos - d["u"] + 1)
        pos -= d["u"]
    leafs = [d for d in p.placed("switch") if d["role"] == "leaf"]
    p.connect(leafs[0]["id"], "u1", leafs[1]["id"], "u1")
    fw = p.placed("fw")[0]
    p.connect(leafs[0]["id"], "u2", fw["id"], "u1")
    oob = next(d for d in p.placed("switch") if d["role"] == "oob")
    p.connect(oob["id"], "u1", fw["id"], "p1")
    p.auto_cable()
    return p
