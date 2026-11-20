"""Server-Zustandsmodell des virtuellen BMC.

Ein `ServerSim` hängt an jedem `BMC` (bmc.sim) und ist die einzige Quelle der Wahrheit für alles, was
ein echter Server "von selbst" tut: Power-Zustand mit Übergängen (Aus -> Einschalten -> POST -> Läuft ->
Herunterfahren), Ereignisprotokoll, Sensoren, Laufzeit und Hardware-Inventar. Weboberflächen, Redfish,
IPMI und SSH lesen nur hieraus; Aktionen (Einschalten, Reset ...) gehen über `power_action()`.

Zeit: Übergänge werden "faul" aufgelöst (beim nächsten Lesen), es laufen also keine Hintergrundthreads.
Mit der Umgebungsvariable VBMC_TIME_SCALE (z. B. 0.2) laufen POST und Herunterfahren schneller, in
Tests auf 0 (sofort fertig).
"""
import hashlib
import math
import os
import random
import threading
import time

# ---------------------------------------------------------------- Zeitverhalten (Sekunden bei Scale 1)
T_POWERING_ON = 3.0       # Redfish "PoweringOn": Netzteile/Standby -> Hauptplatine
T_POST = 28.0             # POST (Speichertest, Gerätesuche) bis das Betriebssystem startet
T_SHUTDOWN = 9.0          # Graceful: "PoweringOff" bis "Off"
T_OS_BOOT = 12.0          # Betriebssystem startet (nach POST)


def time_scale():
    try:
        return max(0.0, float(os.environ.get("VBMC_TIME_SCALE", "1")))
    except ValueError:
        return 1.0


# Power-Aktionen (Redfish ResetType) -> (Zielzustand, Ereignistext-Schlüssel)
RESET_TYPES = ("On", "ForceOff", "GracefulShutdown", "GracefulRestart", "ForceRestart",
               "PushPowerButton", "PowerCycle", "Nmi", "ForceOn")

# POST-Phasen: (Anteil der POST-Zeit, Redfish/iLO-PostState, Text für die Konsole)
POST_STAGES = [
    (0.00, "InPost", "BMC handshake / Power good"),
    (0.12, "InPost", "Initializing processors"),
    (0.30, "InPost", "Memory training and test"),
    (0.55, "InPost", "Detecting PCIe devices and storage controllers"),
    (0.78, "InPostDiscoveryComplete", "Option ROMs executed"),
    (0.92, "InPostDiscoveryComplete", "Preparing boot"),
]

# Wortlaut der Ereignisse: (Schweregrad, Quelle, Text). {x} und {y} werden eingesetzt.
# Alle Texte sind selbst formuliert (keine Meldungstexte echter Hersteller); jedes virtuelle Profil nutzt dieselben.
_EVENTS = {
    "power_on": ("OK", "Power", "Server powered on."),
    "power_off": ("OK", "Power", "Server powered off."),
    "button": ("OK", "Power", "Power button pressed."),
    "graceful_off": ("OK", "Power", "Graceful shutdown requested."),
    "post_ok": ("OK", "System", "Start-up self test finished."),
    "os_up": ("OK", "System", "Operating system is running."),
    "reset": ("Informational", "System", "Server restarted."),
    "ilo_reset": ("Informational", "BMC", "Management controller restarted by {x}."),
    "login": ("Informational", "Access", "Login of {x} from {y}."),
    "login_fail": ("Warning", "Access", "Failed login of {x} from {y}."),
    "logout": ("Informational", "Access", "Logout of {x} from {y}."),
    "net": ("Informational", "BMC", "Network settings changed by {x}."),
    "user": ("Informational", "Access", "Account changed by {x}: {y}."),
    "nmi": ("Warning", "System", "Diagnostic interrupt sent by {x}."),
    "bios": ("Informational", "Firmware", "UEFI settings changed by {x}."),
    "vm_insert": ("Informational", "Media", "Virtual media attached by {x}: {y}."),
    "vm_eject": ("Informational", "Media", "Virtual media detached by {x}."),
    "boot_vm": ("Informational", "System", "Server started from virtual media."),
    "lic_install": ("Informational", "License", "License key installed by {x}: {y}."),
    "lic_remove": ("Informational", "License", "License key removed by {x}."),
    "cert_install": ("Informational", "Security", "Certificate imported by {x}: {y}."),
    "csr": ("Informational", "Security", "Certificate request created by {x}."),
}
EVENT_TEXT = {"hpe": _EVENTS, "dell": _EVENTS, "lenovo": _EVENTS, "smc": _EVENTS}

import re

from . import certs

# Lizenzstufen je Hersteller (Index 0 = ohne Lizenz); Standardstufe = nach Schlüsselinstallation
TIERS = {"hpe": ["Häpe Standard", "Häpe Advanced", "Häpe Advanced Premium"],
         "dell": ["Böll 9 Basic", "Böll 9 Enterprise", "Böll 9 Datacenter"],
         "lenovo": ["Venolo Standard", "Venolo Advanced", "Venolo Enterprise"],
         "smc": ["Unlicensed", "SFT-DCMS-SINGLE", "SFT-OOB-LIC"]}
LIC_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")

# Virtual-Media-Geräte je Hersteller: id -> Medientypen
VMEDIA = {"hpe": {"1": ["USBStick"], "2": ["CD", "DVD"]},
          "dell": {"RemovableDisk": ["USBStick"], "CD": ["CD", "DVD"]},
          "lenovo": {"EXT1": ["CD", "DVD", "USBStick"], "EXT2": ["CD", "DVD", "USBStick"]},
          "smc": {"CD1": ["CD", "DVD"], "USB1": ["USBStick"]}}
BOOT_TARGETS = ["None", "Pxe", "Cd", "Hdd", "BiosSetup", "Usb", "UefiTarget"]
URI_SCHEMES = ("http://", "https://", "nfs://", "cifs://", "smb://", "ftp://")


def _fam(bmc):
    f = bmc.family
    return f if f in EVENT_TEXT else "hpe"


class ServerSim:
    """Zustand eines simulierten Servers. Alle Methoden sind thread-sicher (ein Lock)."""

    def __init__(self, bmc):
        self.bmc = bmc
        self.lock = threading.RLock()
        self._rng = random.Random(bmc.serial)
        self.t0_bmc = time.time() - self._rng.uniform(3, 40) * 86400     # BMC läuft schon länger
        self._phase = "On"                 # Off | PoweringOn | Post | OsBoot | On | ShuttingDown
        self._since = time.monotonic()
        self._restart_after_off = False
        self._phase_wall = time.time()
        self.power_on_wall = time.time() - self._rng.uniform(1, 30) * 86400   # Server läuft seit ...
        self.events = []
        self._eid = 0
        self.sessions = {}                 # token -> {user, ip, created, last}
        self.uid_led = "Off"
        self.boot_id = 0                   # zählt jeden (Neu-)Start; die Konsole setzt damit ihren Zustand zurück
        self.con = {"boot": -1, "mode": "auto", "sel": 0}
        self.in_setup = False              # Server steht im BIOS-Setup: der Start hält an, bis es verlassen wird
        self.boot_override = {"enabled": "Disabled", "target": "None"}
        self.boot_device = "disk"          # was beim letzten Start gebootet wurde: disk | vcd | pxe
        self.vmedia = {mid: {"types": t, "image": "", "inserted": False, "wp": True, "user": ""}
                       for mid, t in VMEDIA[_fam(bmc)].items()}
        fam = _fam(bmc)
        want = (getattr(bmc, "license_cfg", None) or "").lower()
        tiers = TIERS[fam]
        if want in ("none", "no", "off", "unlicensed", "0"):
            idx = 0
        else:
            idx = next((i for i, t in enumerate(tiers) if t.lower() == want), 1)
        cn = bmc.dhcp_hostname() or bmc.model
        self.cert = {"subject": cn, "issuer": cn, "self_signed": True, "not_before": time.time() - 86400 * 20,
                     "not_after": time.time() + 86400 * 3650,
                     "serial": hashlib.sha256(bmc.serial.encode()).hexdigest()[:16].upper(),
                     "fingerprint": ":".join(hashlib.sha256((bmc.serial + "c").encode()).hexdigest()[i:i + 2].upper()
                                             for i in range(0, 64, 2)), "pem": ""}
        self.csr_pem = ""
        self.lic = {"tier": idx, "key": "" if idx == 0 else "AB12C-D34EF-G56HJ-K78LM-N90PQ",
                    "since": time.time() - 86400 * 20 if idx else 0}
        self.nmi_count = 0
        self.inv = make_inventory(bmc, self._rng)
        self._seed_events()

    # ------------------------------------------------------------ Ereignisse
    def _seed_events(self):
        now = time.time()
        base = now - self._rng.uniform(20, 60) * 86400
        seeds = [("power_on", None, None), ("post_ok", None, None), ("os_up", None, None),
                 ("ilo_reset", "factory reset", None), ("login", "Administrator", "10.0.0.5")]
        for i, (key, x, y) in enumerate(seeds):
            self._event(key, x, y, when=base + i * 90)

    def _event(self, key, x="", y="", when=None, severity=None):
        fam = _fam(self.bmc)
        sev, src, text = EVENT_TEXT[fam][key]
        text = text.format(x=x or "", y=y or "")
        with self.lock:
            self._eid += 1
            self.events.append({"id": self._eid, "severity": severity or sev, "source": src,
                                "message": text, "time": when if when is not None else time.time(),
                                "key": key})
            if len(self.events) > 512:
                del self.events[:64]

    def log(self, key, x="", y=""):
        self._event(key, x, y)

    def clear_events(self, who="admin"):
        with self.lock:
            self.events.clear()
        self._event("ilo_reset", f"{who} (log cleared)")

    # ------------------------------------------------------------ Power-Automat
    def _dur(self, secs):
        return secs * time_scale()

    def _resolve(self):
        """Übergänge nachziehen, die seit dem letzten Lesen fällig geworden sind."""
        with self.lock:
            while True:
                age = time.monotonic() - self._since
                p = self._phase
                if p == "PoweringOn" and age >= self._dur(T_POWERING_ON):
                    self._enter("Post")
                elif p == "Post" and age >= self._dur(T_POST):
                    if self.in_setup:               # im BIOS-Setup: Start hält an
                        return
                    self._event("post_ok")
                    self._select_boot()
                    self._enter("OsBoot")
                elif p == "OsBoot" and age >= self._dur(T_OS_BOOT):
                    if self.boot_device == "disk":
                        self._event("os_up")
                    self._enter("On")
                elif p == "ShuttingDown" and age >= self._dur(T_SHUTDOWN):
                    self._enter("Off")
                    self._event("power_off")
                    if self._restart_after_off:
                        self._restart_after_off = False
                        self._start_power_on()
                else:
                    return

    def _enter(self, phase):
        if phase == "Off":
            self.in_setup = False
        self._phase = phase
        self._since = time.monotonic()
        self._phase_wall = time.time()

    def _start_power_on(self):
        self._enter("PoweringOn")
        self.power_on_wall = time.time()
        self._new_boot()
        self._event("power_on")

    def _new_boot(self):
        """Jeder (Neu-)Start: Setup-Haltepunkt zurücksetzen; „BiosSetup“ vormerken hält den Start im Setup an."""
        self.boot_id += 1
        self.in_setup = False
        bo = self.boot_override
        if bo["target"] == "BiosSetup" and bo["enabled"] != "Disabled":
            self.in_setup = True
            if bo["enabled"] == "Once":
                self.boot_override = {"enabled": "Disabled", "target": "None"}

    def _select_boot(self):
        """Am Ende des POST: Startgerät wählen (Override Cd mit eingelegtem Medium, Pxe, sonst Platte)."""
        bo = self.boot_override
        dev = "disk"
        if bo["enabled"] != "Disabled":
            if bo["target"] in ("Cd", "Usb") and self.vm_inserted(bo["target"]):
                dev = "vcd"
            elif bo["target"] == "Pxe":
                dev = "pxe"
            if bo["enabled"] == "Once":
                self.boot_override = {"enabled": "Disabled", "target": "None"}
        self.boot_device = dev
        if dev == "vcd":
            self._event("boot_vm")

    @property
    def phase(self):
        self._resolve()
        return self._phase

    @property
    def power_state(self):
        """Redfish-PowerState: On | Off | PoweringOn | PoweringOff."""
        p = self.phase
        return {"Off": "Off", "PoweringOn": "PoweringOn", "ShuttingDown": "PoweringOff"}.get(p, "On")

    @property
    def is_on(self):
        return self.phase in ("Post", "OsBoot", "On", "ShuttingDown")

    @property
    def post_state(self):
        """iLO Oem.Hpe.PostState: PowerOff | InPost | InPostDiscoveryComplete | FinishedPost."""
        p = self.phase
        if p in ("Off", "PoweringOn"):
            return "PowerOff"
        if p == "Post":
            return self._post_stage()[1]
        return "FinishedPost"

    def _post_stage(self):
        frac = min(1.0, (time.monotonic() - self._since) / max(1e-6, self._dur(T_POST))) \
            if time_scale() > 0 else 1.0
        cur = POST_STAGES[0]
        for st in POST_STAGES:
            if frac >= st[0]:
                cur = st
        return cur

    def post_progress(self):
        """0..100 während des POST, sonst 0 (Aus) oder 100."""
        p = self.phase
        if p in ("Off", "PoweringOn"):
            return 0
        if p != "Post":
            return 100
        if time_scale() <= 0:
            return 100
        return int(min(100, (time.monotonic() - self._since) / self._dur(T_POST) * 100))

    def post_text(self):
        p = self.phase
        if p == "Post":
            return self._post_stage()[2]
        return {"Off": "System is powered off", "PoweringOn": "Power good wait",
                "OsBoot": "Booting operating system", "On": "Operating system running",
                "ShuttingDown": "Shutting down"}[p]

    def os_state(self):
        p = self.phase
        if p in ("On", "ShuttingDown") and self.boot_device != "disk":
            return "Unavailable"
        return "Running" if p in ("On", "ShuttingDown") else ("Booting" if p == "OsBoot" else "Unavailable")

    def uptime_s(self):
        return int(time.time() - self.power_on_wall) if self.is_on else 0

    def bmc_uptime_s(self):
        return int(time.time() - self.t0_bmc)

    def power_action(self, kind, who="", ip=""):
        """Zentrale Power-Aktion (Redfish ResetType). Liefert (ok, Meldung)."""
        with self.lock:
            self._resolve()
            ph = self._phase
            off = ph == "Off"
            if kind in ("On", "ForceOn"):
                if not off:
                    return False, "Server is already powered on."
                self._start_power_on()
                return True, ""
            if kind == "PushPowerButton":
                self._event("button")
                if off:
                    self._start_power_on()
                else:
                    self._begin_shutdown(graceful=True)
                return True, ""
            if kind == "GracefulShutdown":
                if off:
                    return False, "Server is already powered off."
                self._event("graceful_off", who)
                self._begin_shutdown(graceful=True)
                return True, ""
            if kind == "ForceOff":
                if off:
                    return False, "Server is already powered off."
                self._enter("Off")
                self._event("power_off")
                return True, ""
            if kind in ("GracefulRestart", "ForceRestart", "PowerCycle"):
                if off:
                    return False, "Server is powered off; use On."
                if kind == "GracefulRestart":
                    self._event("graceful_off", who)
                    self._restart_after_off = True
                    self._begin_shutdown(graceful=True)
                elif kind == "ForceRestart":
                    self._event("reset", who)
                    self._start_power_on_quiet()
                else:                                   # PowerCycle: kurz aus, dann ein
                    self._enter("Off")
                    self._event("power_off")
                    self._start_power_on()
                return True, ""
            if kind == "Nmi":
                if off:
                    return False, "Server is powered off."
                self.nmi_count += 1
                self._event("nmi", who or "user")
                return True, ""
        return False, f"Unsupported ResetType: {kind}"

    def _begin_shutdown(self, graceful):
        if self._phase == "ShuttingDown":
            return
        self._enter("ShuttingDown")

    def _start_power_on_quiet(self):
        self._enter("Post")                 # Reset: kein Netzteil-Zyklus, direkt in den POST
        self.power_on_wall = time.time()
        self._new_boot()

    # ------------------------------------------------------------ Virtual Media
    def vm_inserted(self, target):
        """Gibt es ein eingelegtes Medium zum Startziel (Cd -> CD/DVD-Gerät, Usb -> USB-Gerät)?"""
        want = ("CD", "DVD") if target == "Cd" else ("USBStick",)
        return any(m["inserted"] and any(t in want for t in m["types"]) for m in self.vmedia.values())

    def vm_insert(self, mid, image, wp=True, who=""):
        with self.lock:
            m = self.vmedia.get(mid)
            if m is None:
                return False, f"Virtual media device '{mid}' does not exist."
            if not isinstance(image, str) or not image.lower().startswith(URI_SCHEMES):
                return False, "Image must be a URI (http, https, nfs, cifs or ftp)."
            name = image.rsplit("/", 1)[-1].split("?", 1)[0]
            ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
            ok_ext = ("iso",) if "CD" in m["types"] and "USBStick" not in m["types"] else ("iso", "img")
            if ext not in ok_ext:
                return False, f"Image type not supported for this device (expected .{' or .'.join(ok_ext)})."
            if m["inserted"]:
                return False, "Virtual media is already inserted. Eject it first."
            m.update(image=image, inserted=True, wp=bool(wp), user=who)
        self._event("vm_insert", who, name)
        return True, ""

    def vm_eject(self, mid, who=""):
        with self.lock:
            m = self.vmedia.get(mid)
            if m is None:
                return False, f"Virtual media device '{mid}' does not exist."
            if not m["inserted"]:
                return False, "No virtual media is inserted."
            m.update(image="", inserted=False, user="")
        self._event("vm_eject", who)
        return True, ""

    def vm_name(self, mid):
        m = self.vmedia.get(mid) or {}
        return (m.get("image") or "").rsplit("/", 1)[-1].split("?", 1)[0]

    def cd_image_name(self):
        """Name des Images im (ersten) CD/DVD-Gerät oder ''."""
        for mid, m in self.vmedia.items():
            if m["inserted"] and "CD" in m["types"]:
                return self.vm_name(mid)
        return ""

    def set_boot_override(self, target, enabled):
        if target not in BOOT_TARGETS:
            return False, f"BootSourceOverrideTarget '{target}' is not supported."
        if enabled not in ("Disabled", "Once", "Continuous"):
            return False, f"BootSourceOverrideEnabled '{enabled}' is not supported."
        if target == "None":
            enabled = "Disabled"
        with self.lock:
            self.boot_override = {"enabled": enabled, "target": target}
        return True, ""

    # ------------------------------------------------------------ Zertifikat
    def cert_install(self, pem, who=""):
        info = certs.parse_pem(pem)
        if info is None:
            return False, "The certificate is not valid (expected a PEM encoded X.509 certificate)."
        with self.lock:
            self.cert = {"subject": info["subject"], "issuer": info["issuer"],
                         "self_signed": info["subject"] == info["issuer"], "not_before": info["not_before"],
                         "not_after": info["not_after"], "serial": info["serial"],
                         "fingerprint": info["fingerprint"], "pem": pem.strip()}
        self._event("cert_install", who, info["subject"])
        return True, ""

    def cert_csr(self, who="", common_name="", org="", country="", state="", city=""):
        cn = common_name or self.bmc.hostname or self.bmc.dhcp_hostname() or self.bmc.model
        pem = certs.make_csr(cn, org, country, state, city)
        if not pem:
            return False, "CSR generation is not available (openssl missing or invalid subject)."
        self.csr_pem = pem
        self._event("csr", who)
        return True, pem

    # ------------------------------------------------------------ Lizenz
    @property
    def licensed(self):
        return self.lic["tier"] > 0

    @property
    def lic_name(self):
        name = TIERS[_fam(self.bmc)][self.lic["tier"]]
        if _fam(self.bmc) == "dell":          # iDRAC8/iDRAC10 heißen in der Lizenz wie ihre Generation
            name = name.replace("Böll 9", re.sub(r"\s*\(.*", "", self.bmc.gen_label or "Böll 9"))
        return name

    def lic_masked(self):
        k = self.lic["key"]
        return "XXXXX-XXXXX-XXXXX-XXXXX-" + k[-5:] if k else ""

    def lic_install(self, key, who=""):
        key = str(key or "").strip().upper()
        if not LIC_RE.match(key):
            return False, "The license key format is invalid (expected five groups of five characters)."
        default = 1
        with self.lock:
            self.lic = {"tier": default, "key": key, "since": time.time()}
        self._event("lic_install", who, "XXXXX-XXXXX-XXXXX-XXXXX-" + key[-5:])
        return True, ""

    def lic_remove(self, who=""):
        if not self.licensed:
            return False, "No license is installed."
        with self.lock:
            self.lic = {"tier": 0, "key": "", "since": 0}
        self._event("lic_remove", who)
        return True, ""

    def bios_changed(self, who):
        self._event("bios", who)

    # ------------------------------------------------------------ Sessions (Weboberfläche)
    SESSION_TTL = 1800

    def new_session(self, user, ip):
        tok = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        with self.lock:
            self.sessions[tok] = {"user": user, "ip": ip, "created": time.time(), "last": time.time()}
        self._event("login", user, ip)
        return tok

    def session(self, tok, touch=True):
        if not tok:
            return None
        with self.lock:
            s = self.sessions.get(tok)
            if not s:
                return None
            if time.time() - s["last"] > self.SESSION_TTL:
                del self.sessions[tok]
                return None
            if touch:
                s["last"] = time.time()
            return s

    def end_session(self, tok):
        with self.lock:
            s = self.sessions.pop(tok, None)
        if s:
            self._event("logout", s["user"], s["ip"])

    # ------------------------------------------------------------ Sensoren
    def _wave(self, key, amp, period=240.0):
        ph = int(hashlib.sha256(f"{self.bmc.serial}{key}".encode()).hexdigest()[:6], 16) % 628 / 100
        return amp * math.sin(time.time() / period * 2 * math.pi + ph)

    def sensors(self):
        """Temperaturen, Lüfter, Netzteile. Aus = Umgebungstemperatur, Lüfter stehen."""
        ph = self.phase
        on = ph in ("Post", "OsBoot", "On", "ShuttingDown")
        busy = 1.0 if ph == "On" else (0.55 if on else 0.0)
        inlet = round(21.5 + self._wave("inlet", 0.7), 1)
        temps = [("Inlet Ambient", inlet, 42, 47)]
        for i in range(1, self.inv["cpu_count"] + 1):
            t = inlet + 6 + busy * (17 + self._wave(f"cpu{i}", 4)) if on else inlet + 0.5
            temps.append((f"CPU {i}", round(t, 1), 95, 100))
        temps.append(("Memory Zone", round(inlet + 3 + busy * (10 + self._wave("mem", 2)), 1) if on
                      else round(inlet + 0.3, 1), 85, 90))
        temps.append(("Chipset", round(inlet + 9 + busy * 11, 1) if on else round(inlet + 0.4, 1), 100, 105))
        temps.append(("Power Supply Zone", round(inlet + 5 + busy * 8, 1) if on else round(inlet + 0.2, 1), 80, 90))
        fan_pct = 0 if not on else int(max(18, min(100, 22 + busy * 30 + self._wave("fan", 4))))
        fans = [(f"Fan {i}", fan_pct) for i in range(1, self.inv["fans"] + 1)]
        draw = 0.0
        if on:
            draw = (190 if ph != "On" else 205) + self.inv["cpu_count"] * 55 * busy + self._wave("pwr", 9)
        psu = []
        n = self.inv["psus"]
        for i in range(1, n + 1):
            w = round(draw / n if on else 0.0, 1)
            psu.append({"id": i, "watts": w, "capacity": self.inv["psu_watts"],
                        "state": "Enabled", "health": "OK", "input_v": 231 + (i % 2),
                        "model": self.inv["psu_model"], "serial": f"{self.bmc.serial[-6:]}P{i}",
                        "part": "P38995-B21"})
        return {"temps": temps, "fans": fans, "psu": psu, "watts": round(draw, 1),
                "avg_watts": round(draw * 0.96, 1), "peak_watts": round(draw * 1.18, 1)}

    def health(self):
        return "OK"


# ---------------------------------------------------------------- Hardware-Inventar
CPU_BY_FAMILY = {
    "hpe": ["AMD EPYC 9354 32-Core Processor", "Intel(R) Xeon(R) Gold 6430", "Intel(R) Xeon(R) Gold 5418Y"],
    "dell": ["AMD EPYC 9354 32-Core Processor", "Intel(R) Xeon(R) Gold 6430", "Intel(R) Xeon(R) Silver 4410Y"],
    "lenovo": ["AMD EPYC 7413 24-Core Processor", "Intel(R) Xeon(R) Gold 6430", "AMD EPYC 9354 32-Core Processor"],
    "smc": ["AMD EPYC 9254 24-Core Processor", "Intel(R) Xeon(R) Gold 6430", "AMD EPYC 9354 32-Core Processor"],
}
DIMM_BY_FAMILY = {"hpe": "Häpe", "dell": "Samsung", "lenovo": "Hynix", "smc": "Micron"}
DRIVE_BY_FAMILY = {"hpe": "NVMe-SSD 3,84 TB (virtuell)", "dell": "NVMe-SSD 3,84 TB (virtuell)",
                   "lenovo": "SATA-SSD 960 GB (virtuell)", "smc": "NVMe-SSD 1,92 TB (virtuell)"}


def _mac(seed, rng, oui):
    h = hashlib.sha256(f"{seed}{oui}".encode()).hexdigest()
    return oui + ":" + ":".join(h[i:i + 2] for i in (0, 2, 4)).upper()


def make_inventory(bmc, rng):
    fam = bmc.family
    cpu_model = rng.choice(CPU_BY_FAMILY[fam])
    cpus = 1 if "365" in bmc.model or "R6615" in bmc.model or "SR655" in bmc.model else 2
    cores = 32 if "9354" in cpu_model else (24 if "9254" in cpu_model or "7413" in cpu_model else 16)
    oui = {"hpe": "94:40:C9", "dell": "F4:02:70", "lenovo": "6C:FE:54", "smc": "AC:1F:6B"}[fam]
    nic_oui = {"hpe": "B8:83:03", "dell": "B0:7B:25", "lenovo": "08:94:EF", "smc": "3C:EC:EF"}[fam]
    dimms = []
    for i in range(1, 8 * cpus + 1):
        dimms.append({"slot": f"PROC {1 if i <= 8 else 2} DIMM {(i - 1) % 8 + 1}",
                      "size_gb": 32, "type": "DDR5", "mhz": 4800, "vendor": DIMM_BY_FAMILY[fam],
                      "part": f"M321R4GA3BB6-CQK{i:02d}", "serial": f"{bmc.serial[-5:]}D{i:02d}",
                      "state": "Enabled", "health": "OK"})
    drives = [{"id": f"Bay {i}", "model": DRIVE_BY_FAMILY[fam], "cap_gb": 3840 if fam in ("hpe", "dell") else 960,
               "media": "SSD", "protocol": "NVMe" if fam in ("hpe", "dell") else "SATA",
               "serial": f"S{rng.randint(10**9, 10**10 - 1)}", "health": "OK", "state": "Enabled"}
              for i in range(1, 5)]
    return {
        "cpu_model": cpu_model, "cpu_count": cpus, "cores": cores, "threads": cores * 2,
        "mhz": 3250, "dimms": dimms, "drives": drives,
        "ram_gb": sum(d["size_gb"] for d in dimms),
        "bios_ver": "1.8.2", "bios_date": "03/14/2025",
        "fans": 6 if bmc.family in ("hpe", "dell", "lenovo") else 4,
        "psus": 2, "psu_watts": 800, "psu_model": {"hpe": "800W Platinum (virtuell)",
                                                    "dell": "800W Titanium (virtuell)",
                                                    "lenovo": "750W Platinum (virtuell)",
                                                    "smc": "1200W Platinum (virtuell)"}[fam],
        "sys_mac": _mac(bmc.serial, rng, oui), "bmc_mac": _mac(bmc.serial + "b", rng, oui),
        "nics": [{"name": f"Port {i}", "mac": _mac(bmc.serial + str(i), rng, nic_oui),
                  "speed": "25 Gbps" if i < 3 else "1 Gbps", "state": "Up" if i == 1 else "Down"}
                 for i in range(1, 5)],
        "uuid": "%s-%s-%s-%s-%s" % tuple(hashlib.sha256(bmc.serial.encode()).hexdigest()[a:b]
                                        for a, b in ((0, 8), (8, 12), (12, 16), (16, 20), (20, 32))),
        "asset_tag": "", "rom_family": {"hpe": "A56", "dell": "", "lenovo": "ESE1", "smc": ""}[fam],
        "boot_order": ["NIC.Slot.1", "RAID.Slot.2", "Virtual CD/DVD"],
    }
