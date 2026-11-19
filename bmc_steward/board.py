"""BMC-Steward: Ablauf „Mainboard getauscht“ für genau einen Server.

Nach einem Boardtausch hat der BMC ein neues Zertifikat und eine neue Geräte-Kennung, und je Hersteller fehlt
etwas anderes (Seriennummer, Service-Tag, Lizenz, Einstellungen). Der Ablauf bündelt das:

1. Zertifikats-Merkungen der Adresse (und des alten Boards aus der Sicherung) vergessen, das neue Zertifikat
   wird beim ersten Kontakt neu gemerkt.
2. Hersteller erkennen, Eigenschutz prüfen, den Zustand des neuen Boards sichern.
3. Kennung des Gehäuses: HPE iLO 5/6 setzt Seriennummer und Produkt-ID als UEFI-Attribut, optional die
   iLO-Lizenz. Dell holt Service-Tag, Lizenz und Einstellungen beim nächsten Start selbst über „Easy Restore“
   zurück. Bei Lenovo und Supermicro geht das nur mit den Werkzeugen des Herstellers (OneCLI bzw. SUM); dort
   vergleicht BMC-Steward nur und sagt, was zu tun ist. Geraten oder über ungeprüfte Wege geschrieben wird nichts.
4. Optional aus der Sicherung des alten Boards zurückspielen: UEFI-Attribute (nur vorhandene, nie Passwort-,
   TPM- oder Lösch-Attribute und nie die Kennung) und das Ein/Aus der Netzwerkdienste (IPMI, SSH, SNMP …).
   Konten und Passwörter stehen nicht in der Sicherung und bleiben unberührt.
"""
import json
import re

from PyQt6.QtCore import QThread, pyqtSignal

from . import trust
from .i18n import T
from .redfish import BiosEngine, RedfishClient, addr_key

# UEFI-Attribute, die zur Kennung des Gehäuses gehören: werden nie aus einer Sicherung übernommen
IDENT_ATTRS = ("serialnumber", "productid", "servicetag", "assettag", "uuid", "sku")
LICENSE_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")
IDENT_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,19}$")


def load_backup(path):
    """Liest eine Sicherung (siehe RedfishClient.backup); gibt das Dokument oder None zurück."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("resources"), dict):
        return None
    return doc


def backup_system(doc):
    """ComputerSystem-Ressource aus einer Sicherung (für Seriennummer/SKU des alten Boards)."""
    for path, res in (doc or {}).get("resources", {}).items():
        if re.fullmatch(r"/redfish/v1/Systems/[^/]+", path.rstrip("/")) and isinstance(res, dict):
            return res
    return {}


def backup_part(doc, suffix):
    """Erste Ressource der Sicherung, deren Pfad auf suffix endet (z. B. "/Bios", "/NetworkProtocol")."""
    for path, res in (doc or {}).get("resources", {}).items():
        if path.rstrip("/").endswith(suffix) and isinstance(res, dict):
            return res
    return None


class BoardEngine(RedfishClient):
    """Mainboard-Tausch für genau einen Server. Protokollzeilen gehen an log()."""

    def __init__(self, ip, user, pw, backup_path=None, hpe_serial="", hpe_pid="", hpe_license="",
                 restore_bios=False, restore_services=False, dry=True, log=print, stopped=lambda: False,
                 vendor=None):
        super().__init__(dry, log, stopped)
        self.ip, self.user_set, self.pw = ip.strip(), user.strip(), pw
        self.backup_path = backup_path
        self.hpe_serial, self.hpe_pid = hpe_serial.strip().upper(), hpe_pid.strip().upper()
        self.hpe_license = hpe_license.strip().upper()
        self.restore_bios, self.restore_services = restore_bios, restore_services
        self.force_vendor = vendor
        self.old = None

    # ---------- Hilfen ----------
    def read(self, path):
        """GET auch im Trockenlauf (nur lesen, wie beim Eigenschutz); gibt das JSON-Objekt oder None."""
        dry, self.dry = self.dry, False
        try:
            code = self.code = self.api(self.ip, self.cur_user, self.pw, "GET", path)
        finally:
            self.dry = dry
        return self.body if self.ok(code) and isinstance(self.body, dict) else None

    def bios(self, vendor):
        """(Attribute, Pfad zum Schreiben) des UEFI, auch im Trockenlauf gelesen; (None, None), wenn nicht lesbar."""
        body = self.read(self.sys_path(vendor) + "/Bios")
        attrs = body.get("Attributes") if body else None
        if not isinstance(attrs, dict):
            return None, None
        settings = self.sys_path(vendor) + "/Bios/Settings"
        rs = body.get("@Redfish.Settings")
        if isinstance(rs, dict) and isinstance(rs.get("SettingsObject"), dict):
            settings = rs["SettingsObject"].get("@odata.id") or settings
        return attrs, settings

    def forget_certs(self):
        """Schritt 1: Merkungen dieser Adresse und des alten Boards vergessen. Im Trockenlauf nur anzeigen und
        das Gerät unter dieser Adresse bloß mit Warnung statt Sperre ansprechen."""
        st = trust.store()
        addr = addr_key(self.ip)
        keys = set(st.for_addr(addr))
        old_key = ((self.old or {}).get("meta") or {}).get("device")
        if old_key and old_key in st.pins:
            keys.add(old_key)
        if not keys:
            self.log(T("  Zertifikate: für diese Adresse ist nichts gemerkt."))
            return
        shown = ", ".join(trust.short(st.pins[k]["fp"]) for k in sorted(keys))
        if self.dry:
            self.log(T("  Trockenlauf: würde {0} gemerkte Zertifikate vergessen ({1}).").format(len(keys), shown))
            self.STRICT_NEW = False
            return
        for k in keys:
            st.forget(k)
        self.log(T("  Gemerkte Zertifikate vergessen ({0}). Das neue Zertifikat wird beim ersten Kontakt gemerkt.")
                 .format(shown), "ok")

    # ---------- Kennung des Gehäuses je Hersteller ----------
    def ident_hpe(self, system):
        old_sys = backup_system(self.old)
        serial = self.hpe_serial or str(old_sys.get("SerialNumber") or "").strip().upper()
        pid = self.hpe_pid or str(old_sys.get("SKU") or "").strip().upper()
        if not serial and not pid:
            self.log(T("  HPE: keine Seriennummer/Produkt-ID angegeben und keine Sicherung gewählt, Kennung bleibt "
                       "unverändert."))
            return True
        bad = [v for v in (serial, pid) if v and not IDENT_RE.match(v)]
        if bad:                                     # nur aus der Sicherung möglich, Eingaben prüft run() vorab
            self.log(T("  FEHLER: ungültige Seriennummer/Produkt-ID: {0}").format(", ".join(bad)), "err")
            return False
        attrs, settings = self.bios("hpe")
        settings = settings or "/redfish/v1/Systems/1/Bios/Settings/"
        now = {"SerialNumber": str(system.get("SerialNumber") or "").strip().upper(),
               "ProductId": str(system.get("SKU") or "").strip().upper()}
        if isinstance(attrs, dict):
            now.update({k: str(attrs[k]).strip().upper() for k in ("SerialNumber", "ProductId") if k in attrs})
        wanted = {k: v for k, v in (("SerialNumber", serial), ("ProductId", pid)) if v and now.get(k) != v}
        if not wanted:
            self.log(T("  HPE: Seriennummer {0} und Produkt-ID {1} stimmen schon.").format(serial or "–", pid or "–"),
                     "ok")
            return True
        if not self.attempt(T("Seriennummer/Produkt-ID"), self.ip, self.pw, "PATCH", settings, {"Attributes": wanted}):
            return False
        self.log(T("  Seriennummer/Produkt-ID vorgemerkt: {0} (gilt nach dem nächsten Neustart des Servers)").format(
            ", ".join(f"{k}={v}" for k, v in wanted.items())), "ok")
        return True

    def license_hpe(self):
        if not self.hpe_license:
            return True
        if not self.attempt(T("iLO-Lizenz einspielen"), self.ip, self.pw, "POST", "/redfish/v1/Managers/1/LicenseService",
                            {"LicenseKey": self.hpe_license}):
            return False
        self.log(T("  iLO-Lizenz eingespielt (…{0}).").format(self.hpe_license[-5:]), "ok")
        return True

    def ident_dell(self, system):
        tag = str(system.get("SKU") or "").strip().upper()
        old = str(backup_system(self.old).get("SKU") or "").strip().upper()
        if tag and (not old or tag == old):
            self.log(T("  Dell: Service-Tag {0} ist vorhanden.").format(tag), "ok")
            return True
        self.log(T("  Dell: Service-Tag fehlt oder passt nicht zur Sicherung (jetzt {0}, Sicherung {1}).").format(
            tag or "–", old or "–"), "err")
        self.log(T("  Dell holt Service-Tag, Lizenz und Einstellungen selbst zurück („Easy Restore“): Server neu "
                   "starten und die Abfrage beim Start in der UEFI-Konsole bestätigen. BMC-Steward schreibt das "
                   "Service-Tag nicht."))
        return True

    def ident_other(self, system, vendor):
        serial = str(system.get("SerialNumber") or "").strip().upper()
        old = str(backup_system(self.old).get("SerialNumber") or "").strip().upper()
        name = self.NAME[vendor]
        if serial and (not old or serial == old):
            self.log(T("  {0}: Seriennummer {1} ist vorhanden.").format(name, serial), "ok")
            return True
        self.log(T("  {0}: Seriennummer fehlt oder passt nicht zur Sicherung (jetzt {1}, Sicherung {2}).").format(
            name, serial or "–", old or "–"), "err")
        if vendor == "lenovo":
            self.log(T("  Lenovo: Maschinentyp und Seriennummer mit „Lenovo XClarity Essentials OneCLI“ neu "
                       "schreiben (Werte vom Aufkleber am Gehäuse). Über Redfish geht das nicht, BMC-Steward "
                       "schreibt hier nichts."))
        else:
            self.log(T("  Supermicro: die DMI-Daten (Kennungen von Board und Gehäuse) mit dem „Supermicro Update "
                       "Manager“ (SUM) neu schreiben. Über Redfish geht das nicht, BMC-Steward schreibt hier nichts."))
        return True

    # ---------- Aus der Sicherung zurückspielen ----------
    def restore_bios_attrs(self, vendor):
        saved = backup_part(self.old, "/Bios")
        saved = saved.get("Attributes") if isinstance(saved, dict) else None
        if not isinstance(saved, dict) or not saved:
            self.log(T("  UEFI: in der Sicherung stehen keine UEFI-Attribute."))
            return True
        live, settings = self.bios(vendor)
        if live is None and not self.dry:
            self.fail(T("BIOS-Attribute lesen"), self.code)
            return False
        wanted, missing = {}, []
        for k, v in saved.items():
            low = k.lower()
            if BiosEngine.blocked(k) or any(i in low for i in IDENT_ATTRS):
                continue
            if live is None:
                wanted[k] = v
            elif k not in live:
                missing.append(k)
            elif live[k] != v:
                wanted[k] = v
        if missing:
            self.log(T("  UEFI: auf dem neuen Board nicht vorhanden, übersprungen: ") + ", ".join(sorted(missing)))
        if live is None:
            self.log(T("  Trockenlauf: Attribute nicht wirklich verglichen, zeige alle aus der Sicherung"))
        if not wanted:
            self.log(T("  UEFI: alle Attribute stimmen schon mit der Sicherung überein."), "ok")
            return True
        path = settings or self.sys_path(vendor) + "/Bios/Settings"
        code = self.api(self.ip, self.cur_user, self.pw, "PATCH", path, {"Attributes": wanted})
        if not self.ok(code):
            self.fail(T("BIOS-Attribute setzen"), code)
            return False
        self.log(T("  UEFI: {0} Attribute aus der Sicherung vorgemerkt (gelten nach dem nächsten Neustart): {1}").format(
            len(wanted), ", ".join(sorted(wanted))), "ok")
        return True

    def restore_protocols(self, vendor):
        saved = backup_part(self.old, "/NetworkProtocol")
        if not isinstance(saved, dict):
            self.log(T("  Dienste: in der Sicherung stehen keine Netzwerkdienste."))
            return True
        mgr = self.MGR_PATH.get(vendor, "/redfish/v1/Managers/1") + "/NetworkProtocol"
        live = self.read(mgr)
        if live is None and not self.dry:
            self.fail(T("Netzwerkdienste lesen"), self.code)
            return False
        wanted = {}
        for name, val in saved.items():
            if not isinstance(val, dict) or not isinstance(val.get("ProtocolEnabled"), bool):
                continue
            cur = (live or {}).get(name)
            if live is not None and (not isinstance(cur, dict) or cur.get("ProtocolEnabled") == val["ProtocolEnabled"]):
                continue
            wanted[name] = {"ProtocolEnabled": val["ProtocolEnabled"]}
        if not wanted:
            self.log(T("  Dienste: stimmen schon mit der Sicherung überein."), "ok")
            return True
        code = self.api(self.ip, self.cur_user, self.pw, "PATCH", mgr, wanted)
        if not self.ok(code):
            self.fail(T("Netzwerkdienste setzen"), code)
            return False
        self.log(T("  Dienste wie in der Sicherung: {0}").format(", ".join(
            f"{k} {T('an') if v['ProtocolEnabled'] else T('aus')}" for k, v in sorted(wanted.items()))), "ok")
        return True

    # ---------- Ablauf ----------
    def run(self):
        """True, wenn alles ohne Fehler durchlief."""
        self.log(f"== {self.ip}", "head")
        bad = [v for v in (self.hpe_serial, self.hpe_pid) if v and not IDENT_RE.match(v)]
        if bad:
            self.log(T("  FEHLER: ungültige Seriennummer/Produkt-ID: {0}").format(", ".join(bad)), "err")
            return False
        if self.hpe_license and not LICENSE_RE.match(self.hpe_license):
            self.log(T("  FEHLER: Lizenzschlüssel ungültig (erwartet: fünf Gruppen zu fünf Zeichen)."), "err")
            return False
        self.old = load_backup(self.backup_path)
        if self.backup_path and self.old is None:
            self.log(T("  FEHLER: Sicherung nicht lesbar: {0}").format(self.backup_path), "err")
            return False
        if self.old:
            meta = self.old.get("meta") or {}
            self.log(T("  Sicherung des alten Boards: {0} ({1}, {2})").format(
                meta.get("time", "?"), meta.get("address", "?"), self.NAME.get(meta.get("vendor"), "?")))
        self.forget_certs()
        v = self.force_vendor or self.vendor(self.ip)
        if self.dry and v not in self.NAME:
            self.log(T('  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Ablauf für HPE').format(v))
            v = "hpe"
        if v == "offline":
            self.log(T("  FEHLER: nicht erreichbar"), "err")
            return False
        if v not in self.NAME:
            self.log(T("  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)"), "err")
            self.unknown_hint()
            return False
        self.log(T('  Hersteller: {0}').format(self.NAME[v]))
        if v == "hpe_old":
            self.log(T("  iLO 3/4: Seriennummer und Produkt-ID lassen sich nur im UEFI (RBSU) von Hand setzen. "
                       "Die Zertifikate sind zurückgesetzt, sonst ändert BMC-Steward hier nichts."))
            return True
        old_vendor = ((self.old or {}).get("meta") or {}).get("vendor")
        if old_vendor and old_vendor != v:
            self.log(T("  FEHLER: Die Sicherung stammt von einem anderen Hersteller ({0}).").format(
                self.NAME.get(old_vendor, old_vendor)), "err")
            return False
        self.cur_user = self.user_set or self.DEFAULT_USER[v]
        if not self.self_guard(self.ip, self.cur_user, self.pw, v):
            return False
        system = self.read(self.sys_path(v))
        if system is None:
            if not self.dry:
                self.fail(T("Systemdaten lesen"), self.code)
                return False
            self.log(T("  Trockenlauf: Systemdaten nicht lesbar, zeige den Ablauf ungeprüft"))
            system = {}
        if not self.dry:
            self.backup(self.ip, self.cur_user, self.pw, v, "mainboard")
        good = True
        if v == "hpe":
            good = self.ident_hpe(system) and self.license_hpe()
        elif v == "dell":
            good = self.ident_dell(system)
        else:
            good = self.ident_other(system, v)
        if good and self.old and self.restore_services:
            good = self.restore_protocols(v)
        if good and self.old and self.restore_bios:
            good = self.restore_bios_attrs(v)
        elif (self.restore_bios or self.restore_services) and not self.old:
            self.log(T("  Hinweis: ohne Sicherung gibt es nichts zurückzuspielen."))
        self.close_sessions()
        self.log("")
        self.log(T("Fertig ohne Fehler") if good else T("Beendet mit Fehlern"), "ok" if good else "err")
        return good


class BoardEngineThread(QThread):
    line = pyqtSignal(str, str)
    result = pyqtSignal(int)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        try:
            good = BoardEngine(log=lambda text, kind="": self.line.emit(text, kind),
                               stopped=self.isInterruptionRequested, **self.kw).run()
        except Exception as e:
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            good = False
        self.result.emit(0 if good else 1)
