"""BMC-Steward: Seite „Mainboard-Tausch“ für genau einen Server.

Vier Szenarien: Systemplatine mit BMC (häufigster Fall), nur BMC (Modul getauscht oder auf Werkseinstellungen),
nur Prozessorplatine (BMC-Modul bleibt) und ganzer Server durch ein Ersatzgerät ersetzt. Das Szenario bestimmt,
welche der Schritte unten angeboten werden (SCENARIOS); beim Ersatzgerät wird die Kennung nie geändert und die
Sicherung unter der Seriennummer des alten Servers gesucht. Ohne Sicherung lassen sich Hostname und Adresse von
Hand eintragen, eine Sicherung kann auch von Hand gewählt werden.

Nach einem Tausch der Systemplatine kennt das neue Board die Identität des Gehäuses nicht (Seriennummer bzw.
Service-Tag, Produkt-ID/Maschinentyp), hat ein neues BMC-Zertifikat und keine Einstellungen. Der Dialog
1. liest nur lesend, was das neue Board meldet, und vergleicht es mit dem Aufkleber am Gehäuse,
2. sucht die letzte Sicherung des alten Boards (sicherungen/<Seriennummer>/) und zeigt Hostname und Adresse,
3. sagt je Hersteller, was zu tun ist, und erledigt, was per Redfish sicher geht:
   - HPE iLO 5/6: Seriennummer und Produkt-ID als UEFI-Attribute (SerialNumber/ProductId, gilt nach Neustart)
   - Dell: Service-Tag kommt über „Easy Restore“ beim nächsten Start; hier nur Prüfung und Anleitung
   - Lenovo: VPD nur über Provisioning Manager oder OneCLI; der Dialog erzeugt die Befehle (ohne Passwort)
   - Supermicro: DMI nur über SUM (GetDmiInfo/ChangeDmiInfo, Lizenz nötig); Anleitung
4. vergisst auf Wunsch das gemerkte Zertifikat des alten Boards,
5. spielt auf Wunsch bei HPE die iLO-Lizenz neu ein und aus der Sicherung das Ein/Aus der Netzwerkdienste
   (IPMI, SSDP, SNMP …) und die UEFI-Attribute zurück (nur vorhandene, nie Passwort-, TPM-, Lösch- oder
   Kennungs-Attribute; Konten und Passwörter stehen nicht in der Sicherung) und
6. trägt Hostname und Adresse aus der Sicherung in die Serverliste ein; Benutzer und Netzwerk richtet dann
   der normale Neuware-Lauf ein.
Ändernde Schritte respektieren den Trockenlauf (voreingestellt) und laufen nach Sicherung und Eigenschutz.
"""
import html
import json
import re
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea, QTextBrowser, QVBoxLayout,
                             QWidget)

from . import i18n, trust
from .i18n import T
from .redfish import BiosEngine, RedfishClient

# UEFI-Attribute, die zur Kennung des Gehäuses gehören: werden nie aus einer Sicherung übernommen
IDENT_ATTRS = ("serialnumber", "productid", "servicetag", "assettag", "uuid", "sku")
LICENSE_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")


def folder_name(serial):
    """Ordnername der Sicherungen zu einer Seriennummer (wie RedfishClient.backup)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (serial or "").strip()).strip("_")


def latest_backup(serial):
    """Neueste Sicherung des Geräts mit dieser Seriennummer: (Pfad, Inhalt) oder (None, None)."""
    name = folder_name(serial)
    if not name:
        return None, None
    base = i18n.DATA / "sicherungen" / name
    for path in sorted(base.glob("*.json"), reverse=True):
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None, None


def backup_summary(doc):
    """Wichtigste Angaben einer Sicherung: Zeit, Gerätekennung (für das Zertifikat), Hostname, feste Adresse."""
    meta = doc.get("meta") or {}
    res = {"time": meta.get("time", ""), "device": meta.get("device", ""), "vendor": meta.get("vendor", ""),
           "address": meta.get("address", ""), "hostname": "", "ip": "", "mask": "", "gw": ""}
    resources = doc.get("resources") or {}
    for path, body in sorted(resources.items()):
        if not isinstance(body, dict) or "/Managers/" not in path or "/EthernetInterfaces/" not in path:
            continue
        addrs = [a for a in (body.get("IPv4StaticAddresses") or []) + (body.get("IPv4Addresses") or [])
                 if isinstance(a, dict) and a.get("Address") and a.get("Address") != "0.0.0.0"]
        if addrs and not res["ip"]:
            res.update(ip=addrs[0].get("Address", ""), mask=addrs[0].get("SubnetMask", "") or "",
                       gw=addrs[0].get("Gateway", "") or "")
        if body.get("HostName") and not res["hostname"]:
            res["hostname"] = str(body["HostName"])
    for path, body in resources.items():                 # Dell: Werte stehen (auch) in den iDRAC-Attributen
        if path.endswith("/Attributes") and isinstance(body, dict):
            attrs = body.get("Attributes") or {}
            res["hostname"] = res["hostname"] or str(attrs.get("NIC.1.DNSRacName") or "")
            res["ip"] = res["ip"] or str(attrs.get("IPv4Static.1.Address") or "")
            res["mask"] = res["mask"] or str(attrs.get("IPv4Static.1.Netmask") or "")
            res["gw"] = res["gw"] or str(attrs.get("IPv4Static.1.Gateway") or "")
    return res


def backup_part(doc, suffix):
    """Erste Ressource der Sicherung, deren Pfad auf suffix endet (z. B. "/Bios", "/NetworkProtocol")."""
    for path, res in sorted(((doc or {}).get("resources") or {}).items()):
        if path.rstrip("/").endswith(suffix) and isinstance(res, dict):
            return res
    return None


def lenovo_commands(ip, user, mtm, serial):
    """OneCLI-Befehle für das VPD-Update nach Lenovo-Anleitung; das Passwort steht nie darin."""
    bmc = f"--bmc {user}:<Passwort>@{ip}"
    lines = []
    if mtm:
        lines.append(f"onecli config set SYSTEM_PROD_DATA.SysInfoProdName {mtm} {bmc}")
    if serial:
        lines.append(f"onecli config set SYSTEM_PROD_DATA.SysInfoSerialNum {serial} {bmc}")
    return lines


def pid_matches(vendor, res, label_pid):
    """Produkt-ID (HPE) bzw. Maschinentyp/Modell (Lenovo, MTM) vom Aufkleber gegen das, was das Board meldet.
    Lenovo XCC meldet den MTM als SKU und im Modell als „… -[7X02CTO1WW]-“. Leeres Feld oder ein Hersteller
    ohne setzbaren Typ (Dell, Supermicro) zählt als passend."""
    if not label_pid:
        return True
    sku, model = str(res.get("sku") or "").upper(), str(res.get("model") or "").upper()
    if vendor == "hpe":
        return sku == label_pid
    if vendor == "lenovo":
        return label_pid in (sku, model) or f"[{label_pid}]" in model
    return True


class BoardSwapEngine(RedfishClient):
    """Liest die Identität des neuen Boards (nur GET, ein Anmeldeversuch) und setzt bei HPE iLO 5/6 Seriennummer
    und Produkt-ID, die iLO-Lizenz sowie Dienste und UEFI-Attribute aus der Sicherung des alten Boards."""

    # Ein neues Board unter einer Adresse, an der das alte gemerkt ist, ist hier der Normalfall: nur warnen
    # (wie bei „Neuware“), statt schon das Prüfen zu sperren. Ein Zertifikatswechsel desselben Geräts sperrt weiter.
    STRICT_NEW = False

    def read(self, ip, user, pw):
        res = {"ok": False, "vendor": "", "user": user, "serial": "", "sku": "", "model": "", "uuid": "",
               "power": "", "text": ""}
        if self.tls_mode(ip) is None:
            res["text"] = T("Kein HTTPS-Handshake möglich")
            return res
        v = self.vendor(ip)
        if v not in self.NAME:
            res["text"] = T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)")
            return res
        res["vendor"] = v
        res["user"] = self.cur_user = user or self.DEFAULT_USER[v]
        self.log(T('  Hersteller: {0}, Anmeldung als {1}').format(self.NAME[v], self.cur_user))
        if v == "hpe_old":
            res.update(ok=True, text=T("iLO 3/4: Seriennummer und Produkt-ID nur im RBSU (F9 beim Start) setzen."))
            return res
        code = self.api(ip, self.cur_user, pw, "GET", self.sys_path(v), timeout=20)
        if code == 401:
            res["text"] = T("Anmeldung abgelehnt (Benutzer oder Passwort falsch, oder Konto gesperrt)")
            return res
        if not self.ok(code) or not isinstance(self.body, dict):
            res["text"] = T('Anmeldung nicht möglich (HTTP {0})').format(code or T("keine Verbindung"))
            return res
        b = self.body
        res.update(ok=True, serial=str(b.get("SerialNumber") or "").strip(), sku=str(b.get("SKU") or "").strip(),
                   model=str(b.get("Model") or "").strip(), uuid=str(b.get("UUID") or "").strip(),
                   power=str(b.get("PowerState") or ""))
        self.log(T("  Das Board meldet: Modell {0}, Seriennummer {1}, SKU {2}").format(
            res["model"] or "–", res["serial"] or "–", res["sku"] or "–"), "ok")
        return res

    def set_hpe_ident(self, ip, user, pw, serial, pid):
        """HPE iLO 5/6: SerialNumber/ProductId als UEFI-Attribute vormerken (gilt nach dem nächsten Neustart)."""
        return self.apply(ip, user, pw, "hpe", serial=serial, pid=pid)

    def apply(self, ip, user, pw, vendor, serial="", pid="", license="", doc=None, services=False, bios=False):
        """Alle ändernden Schritte für ein Board, nach Eigenschutz und Sicherung des neuen Boards:
        HPE-Kennung, HPE-Lizenz, dann Dienste und UEFI aus der Sicherung `doc` des alten Boards."""
        self.cur_user = user or self.DEFAULT_USER[vendor]
        attrs = {k: v for k, v in (("SerialNumber", serial), ("ProductId", pid)) if v} if vendor == "hpe" else {}
        license = license if vendor == "hpe" else ""
        services, bios = bool(doc and services), bool(doc and bios)
        if not (attrs or license or services or bios):
            return True
        if license and not LICENSE_RE.match(license):
            self.log(T("  FEHLER: Lizenzschlüssel ungültig (erwartet: fünf Gruppen zu fünf Zeichen)."), "err")
            return False
        if not self.self_guard(ip, self.cur_user, pw, vendor):
            return False
        if not self.dry:
            self.backup(ip, self.cur_user, pw, vendor, "mainboard")
        if attrs:
            if not self.attempt(T("Seriennummer/Produkt-ID"), ip, pw, "PATCH", self.sys_path("hpe") + "/Bios/Settings",
                                {"Attributes": attrs}):
                return False
            self.log(T("  Seriennummer/Produkt-ID vorgemerkt: {0} (gilt nach dem nächsten Neustart des Servers)").format(
                ", ".join(f"{k}={v}" for k, v in attrs.items())), "ok")
        if license:
            if not self.attempt(T("iLO-Lizenz einspielen"), ip, pw, "POST", "/redfish/v1/Managers/1/LicenseService",
                                {"LicenseKey": license}):
                return False
            self.log(T("  iLO-Lizenz eingespielt (…{0}).").format(license[-5:]), "ok")
        if services and not self.restore_services(ip, pw, vendor, doc):
            return False
        if bios and not self.restore_bios(ip, pw, vendor, doc):
            return False
        return True

    def live(self, ip, pw, path):
        """GET auch im Trockenlauf (nur lesen, wie beim Eigenschutz); gibt das JSON-Objekt oder None."""
        dry, self.dry = self.dry, False
        try:
            code = self.code = self.api(ip, self.cur_user, pw, "GET", path)
        finally:
            self.dry = dry
        return self.body if self.ok(code) and isinstance(self.body, dict) else None

    def restore_services(self, ip, pw, vendor, doc):
        """Ein/Aus der Netzwerkdienste wie in der Sicherung; nur was auf dem neuen Board anders steht."""
        saved = backup_part(doc, "/NetworkProtocol")
        if not isinstance(saved, dict):
            self.log(T("  Dienste: in der Sicherung stehen keine Netzwerkdienste."))
            return True
        path = self.MGR_PATH.get(vendor, "/redfish/v1/Managers/1") + "/NetworkProtocol"
        now = self.live(ip, pw, path)
        if now is None:
            self.fail(T("Netzwerkdienste lesen"), self.code)
            return False
        wanted = {}
        for name, val in saved.items():
            cur = now.get(name)
            if isinstance(val, dict) and isinstance(val.get("ProtocolEnabled"), bool) and isinstance(cur, dict) \
                    and cur.get("ProtocolEnabled") != val["ProtocolEnabled"]:
                wanted[name] = {"ProtocolEnabled": val["ProtocolEnabled"]}
        if not wanted:
            self.log(T("  Dienste: stimmen schon mit der Sicherung überein."), "ok")
            return True
        if not self.attempt(T("Netzwerkdienste setzen"), ip, pw, "PATCH", path, wanted):
            return False
        self.log(T("  Dienste wie in der Sicherung: {0}").format(", ".join(
            f"{k} {T('an') if v['ProtocolEnabled'] else T('aus')}" for k, v in sorted(wanted.items()))), "ok")
        return True

    def restore_bios(self, ip, pw, vendor, doc):
        """UEFI-Attribute wie in der Sicherung: nur auf dem neuen Board vorhandene und abweichende, nie
        Passwort-, TPM-, Lösch- oder Kennungs-Attribute. Wirksam nach dem nächsten Neustart, keiner wird ausgelöst."""
        saved = backup_part(doc, "/Bios")
        saved = saved.get("Attributes") if isinstance(saved, dict) else None
        if not isinstance(saved, dict) or not saved:
            self.log(T("  UEFI: in der Sicherung stehen keine UEFI-Attribute."))
            return True
        body = self.live(ip, pw, self.sys_path(vendor) + "/Bios")
        now = body.get("Attributes") if body else None
        if not isinstance(now, dict):
            self.fail(T("BIOS-Attribute lesen"), self.code)
            return False
        settings = self.sys_path(vendor) + "/Bios/Settings"
        rs = body.get("@Redfish.Settings")
        if isinstance(rs, dict) and isinstance(rs.get("SettingsObject"), dict):
            settings = rs["SettingsObject"].get("@odata.id") or settings
        wanted, missing = {}, []
        for k, v in saved.items():
            if BiosEngine.blocked(k) or any(i in k.lower() for i in IDENT_ATTRS):
                continue
            if k not in now:
                missing.append(k)
            elif now[k] != v:
                wanted[k] = v
        if missing:
            self.log(T("  UEFI: auf dem neuen Board nicht vorhanden, übersprungen: ") + ", ".join(sorted(missing)))
        if not wanted:
            self.log(T("  UEFI: alle Attribute stimmen schon mit der Sicherung überein."), "ok")
            return True
        if not self.attempt(T("BIOS-Attribute setzen"), ip, pw, "PATCH", settings, {"Attributes": wanted}):
            return False
        self.log(T("  UEFI: {0} Attribute aus der Sicherung vorgemerkt (gelten nach dem nächsten Neustart): {1}").format(
            len(wanted), ", ".join(sorted(wanted))), "ok")
        return True


class BoardSwapThread(QThread):
    line = pyqtSignal(str, str)
    done = pyqtSignal(object)

    def __init__(self, parent, job, dry=True, **kw):
        super().__init__(parent)
        self.job, self.dry, self.kw = job, dry, kw

    def run(self):
        # Lesen geht immer wirklich ans Gerät (nur GET); der Trockenlauf gilt nur fürs Setzen
        eng = BoardSwapEngine(self.dry and self.job != "read", lambda text, kind="": self.line.emit(text, kind), self.isInterruptionRequested)
        try:
            if self.job == "read":
                res = eng.read(**self.kw)
            else:
                res = {"ok": eng.apply(**self.kw)}
        except Exception as e:                      # nie den Dialog mitreißen
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            res = {"ok": False, "text": repr(e)}
        finally:
            try:
                eng.close_sessions()
            except Exception:
                pass
        self.done.emit(res)





# Szenarien: was getauscht wurde, bestimmt, welche Schritte sinnvoll sind.
#   ident: Kennung (Seriennummer/Produkt-ID) setzen   lic: Lizenz neu   pin: altes Zertifikat vergessen
#   svc: Dienste aus der Sicherung   bios: UEFI aus der Sicherung   take: Hostname/Adresse in die Serverliste
SCENARIOS = {
    "board": dict(ident=True, lic=True, pin=True, svc=True, bios=True, take=True),
    "bmc": dict(ident=True, lic=True, pin=True, svc=True, bios=False, take=True),
    "cpu": dict(ident=True, lic=False, pin=False, svc=False, bios=True, take=False),
    "server": dict(ident=False, lic=True, pin=True, svc=True, bios=True, take=True),
}


def scenario_texts():
    """(Schlüssel, Titel, Erklärung) je Szenario, in der aktuellen Sprache."""
    return [
        ("board", T("Systemplatine mit BMC (häufigster Fall)"),
         T("Kennung, BMC-Einstellungen, Zertifikat und Lizenz sind weg und kommen zurück.")),
        ("bmc", T("Nur BMC: Modul getauscht oder auf Werkseinstellungen"),
         T("Kennung bleibt meist erhalten. BMC-Einstellungen, Zertifikat und Lizenz sind weg.")),
        ("cpu", T("Nur Prozessorplatine, BMC-Modul bleibt"),
         T("Der BMC behält Adresse und Konten. Kennung und UEFI-Einstellungen müssen zurück.")),
        ("server", T("Ganzer Server durch Ersatzgerät ersetzt"),
         T("Einstellungen des alten Servers übernehmen. Die Seriennummer bleibt die des Ersatzgeräts.")),
    ]


def vendor_advice(v, scen, same_ident):
    """Hinweise je Hersteller und Szenario: Liste von (Text, Art)."""
    out = []
    if v == "hpe":
        if scen == "server":
            out.append(T("HPE: Die Seriennummer des Ersatzgeräts bleibt. Eine iLO-Lizenz lässt sich je nach "
                         "Lizenzart übertragen: am alten iLO entfernen, hier eintragen."))
        elif scen == "bmc":
            out.append(T("HPE: Der iLO sitzt auf der Systemplatine. Nach dem Zurücksetzen auf Werkseinstellungen "
                         "bleiben Seriennummer und Produkt-ID erhalten, die Lizenz neu einspielen."))
        elif same_ident:
            out.append((T("HPE: Seriennummer und Produkt-ID stimmen schon."), "ok"))
        else:
            out.append(T("HPE: Seriennummer und Produkt-ID setzen (Haken unten). Sie gelten nach dem nächsten "
                         "Neustart des Servers. Die iLO-Lizenz danach neu einspielen."))
    elif v == "hpe_old":
        out.append(T("  HPE iLO 3/4: Seriennummer und Produkt-ID beim Start mit F9 im RBSU unter "
                     "„Advanced Options → Service Options“ eintragen.").strip())
    elif v == "dell":
        if scen == "server":
            out.append(T("Dell: Lizenzen hängen am Service-Tag. Für das Ersatzgerät im Dell Digital Locker neu "
                         "zuweisen und importieren."))
        elif same_ident:
            out.append((T("  Dell: Der Service-Tag stimmt, „Easy Restore“ hat gegriffen. Lizenz und "
                          "UEFI-Einstellungen prüfen.").strip(), "ok"))
        else:
            out.append(T("  Dell: Den Service-Tag stellt „Easy Restore“ wieder her. Server einschalten und die Abfrage "
                         "beim Start bestätigen. Kommt keine Abfrage, setzt ihn der Dell-Service; per Redfish geht das "
                         "nicht.").strip())
    elif v == "lenovo":
        if scen == "server":
            out.append(T("Lenovo: Features on Demand (FoD) hängen an der Seriennummer. Schlüssel für das "
                         "Ersatzgerät im Lenovo-Portal neu zuweisen."))
        elif scen == "bmc":
            out.append(T("Lenovo: Beim Tausch des System-I/O-Boards (XCC) bleibt die Kennung auf der "
                         "Prozessorplatine. FoD-Schlüssel neu aktivieren."))
        elif not same_ident:
            out.append(T("  Lenovo: Maschinentyp und Seriennummer lassen sich nur einmal setzen, solange beide leer "
                         "sind. Mit dem Provisioning Manager (F1 beim Start, „Update VPD“) oder mit OneCLI:").strip())
        else:
            out.append((T("Lenovo: Maschinentyp und Seriennummer stimmen schon."), "ok"))
    elif v == "smc":
        if scen in ("board", "cpu") and not same_ident:
            out.append(T("  Supermicro: Die DMI-Daten lassen sich mit dem Supermicro Update Manager (SUM) ändern: erst "
                         "GetDmiInfo, Datei bearbeiten, dann ChangeDmiInfo. Dafür ist eine Lizenz (SFT-OOB-LIC oder "
                         "SFT-DCMS-SINGLE) nötig.").strip())
        if scen in ("board", "bmc", "server"):
            out.append(T("Supermicro: Die BMC-Lizenz hängt an der BMC-MAC-Adresse und muss neu angefordert werden."))
    return [x if isinstance(x, tuple) else (x, "") for x in out]


def _card(title):
    box = QGroupBox(title)
    f = QFormLayout(box)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, f


class BoardSwapPage(QWidget):
    """Seite „Mainboard-Tausch“ für genau einen Server, als Folge von Schritten:
    1 Szenario, 2 neues Board erreichen und prüfen, 3 Kennung vom Gehäuse-Aufkleber, 4 Sicherung und Ziel,
    5 Auswahl der Schritte und Ausführen. `row` = (Aktuelle IP, Passwort, Seriennummer) zum Vorbelegen.
    Nach einem scharfen Lauf steht in `take` (Hostname, IP, Maske, Gateway, Seriennummer) und das Signal
    `take_ready` meldet es dem Hauptfenster (Serverliste)."""
    take_ready = pyqtSignal(object)          # (Aktuelle IP, Hostname, IP, Maske, Gateway, Seriennummer)
    console_wanted = pyqtSignal(str, str, str)   # Adresse, Benutzer, Passwort
    search_wanted = pyqtSignal()             # „Server suchen …“
    row_wanted = pyqtSignal()                # Werte der markierten Zeile der Serverliste holen
    next_wanted = pyqtSignal()               # weiter zu „Neuware“

    def __init__(self, parent=None, row=("", "", ""), user=""):
        super().__init__(parent)
        self.info = None            # Ergebnis von „Prüfen“
        self.backup = None          # Zusammenfassung der Sicherung
        self.backup_doc = None      # die Sicherung selbst (Dienste und UEFI zum Zurückspielen)
        self.backup_path = None
        self.manual = None          # von Hand gewählte Sicherung (Pfad, Inhalt): hat Vorrang vor der Suche
        self.take = None
        self.thread = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(8)

        # 1 · Szenario
        box = QGroupBox(T("1 · Was wurde getauscht?"))
        v = QVBoxLayout(box)
        v.setSpacing(2)
        self.scen = QButtonGroup(self)
        self.scen_btn = {}
        for key, title, text in scenario_texts():
            rb = QRadioButton(title)
            rb.setProperty("scen", key)
            rb.setToolTip(text)
            lab = QLabel(text)
            lab.setObjectName("muted")
            lab.setWordWrap(True)
            lab.setContentsMargins(26, 0, 0, 4)
            self.scen.addButton(rb)
            self.scen_btn[key] = rb
            v.addWidget(rb)
            v.addWidget(lab)
        self.scen_btn["board"].setChecked(True)
        self.scen.buttonToggled.connect(self.scenario_changed)
        lay.addWidget(box)

        # 2 · Neues Board erreichen
        box, f = _card(T("2 · Neues Board erreichen"))
        self.addr = QLineEdit(row[0])
        self.addr.setPlaceholderText(T("z. B. 192.168.0.120 (DHCP-Liste oder „Suchen …“)"))
        find = QPushButton(T("Suchen …"))
        find.setToolTip(T("Adresse unbekannt? Sucht im Netz nach BMCs (nur lesend, ohne Anmeldung)."))
        find.clicked.connect(self.search_wanted)
        fromlist = QPushButton(T("Aus Serverliste"))
        fromlist.setToolTip(T("Übernimmt Aktuelle IP, Passwort und Seriennummer der markierten Zeile der Serverliste."))
        fromlist.clicked.connect(self.row_wanted)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.addr, 1)
        h.addWidget(find)
        h.addWidget(fromlist)
        f.addRow(T("Aktuelle IP"), h)
        self.user = QLineEdit(user)
        self.user.setPlaceholderText(T("automatisch je Hersteller"))
        f.addRow(T("Benutzer"), self.user)
        self.pw = QLineEdit(row[1])
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setToolTip(T("Werkspasswort des neuen Boards.\n\n"
                             "Es steht auf dem Anhänger des Ersatzteils oder im Beipackzettel, nicht auf dem "
                             "Aufkleber am Gehäuse. Wird nirgends gespeichert."))
        f.addRow(T("Passwort"), self.pw)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        self.check_btn = QPushButton(T("Prüfen (nur lesend)"))
        self.check_btn.setToolTip(T("Liest, was das neue Board meldet, vergleicht es mit dem Aufkleber und sucht "
                                    "die Sicherung des alten Boards. Ändert nichts."))
        self.check_btn.clicked.connect(self.check)
        self.con_btn = QPushButton(T("UEFI-Konsole öffnen"))
        self.con_btn.setToolTip(T("Öffnet das neue Board in der UEFI-Konsole, z. B. für Dell „Easy Restore“ oder "
                                  "das HPE RBSU. Benutzer und Passwort werden nur in die Felder übernommen."))
        self.con_btn.clicked.connect(lambda: self.console_wanted.emit(self.addr.text().strip(),
                                                                      self.user.text().strip(), self.pw.text()))
        h.addWidget(self.check_btn)
        h.addWidget(self.con_btn)
        h.addStretch(1)
        f.addRow("", h)
        lay.addWidget(box)

        # 3 · Kennung
        box, f = _card(T("3 · Kennung (Aufkleber am Gehäuse)"))
        self.serial = QLineEdit(row[2])
        self.serial.setPlaceholderText(T("Seriennummer bzw. Service-Tag (Dell)"))
        self.pid = QLineEdit()
        self.pid.setPlaceholderText(T("HPE: z. B. 868703-B21 · Lenovo: z. B. 7X02CTO1WW"))
        self.pid.setToolTip(T("HPE: Produkt-ID (Product ID) vom Aufkleber.\n"
                              "Lenovo: Maschinentyp und Modell (MTM).\n"
                              "Dell und Supermicro: leer lassen."))
        self.old_serial = QLineEdit()
        self.old_serial.setPlaceholderText(T("nur bei Ersatzgerät: Seriennummer des alten Servers"))
        self.old_serial.setToolTip(T("Unter dieser Seriennummer sucht das Tool die Sicherung des alten Servers. "
                                     "Die Seriennummer des Ersatzgeräts wird nicht geändert."))
        for w in (self.serial, self.pid, self.old_serial):
            w.setMaxLength(24)
            w.textEdited.connect(lambda txt, w=w: w.setText(txt.upper()))
        self.old_serial.editingFinished.connect(self.find_backup)
        self.lic = QLineEdit()
        self.lic.setPlaceholderText(T("HPE: iLO-Lizenz, optional (XXXXX-XXXXX-XXXXX-XXXXX-XXXXX)"))
        self.lic.setToolTip(T("Die iLO-Lizenz (z. B. Advanced) hängt am Board und muss nach dem Tausch neu\n"
                              "eingespielt werden. Leer lassen, wenn keine gebraucht wird."))
        self.lic.setMaxLength(29)
        self.lic.textEdited.connect(lambda txt: self.lic.setText(txt.upper()))
        self.lic.textChanged.connect(self.update_actions)
        self.old_serial_lab = QLabel(T("Alter Server"))
        f.addRow(T("Seriennummer"), self.serial)
        f.addRow(T("Produkt-ID / Typ"), self.pid)
        f.addRow(self.old_serial_lab, self.old_serial)
        f.addRow(T("Lizenzschlüssel"), self.lic)
        lay.addWidget(box)

        # 4 · Sicherung und Ziel
        box, f = _card(T("4 · Sicherung und Ziel"))
        self.backup_lbl = QLabel(T("Noch nicht gesucht – erst „Prüfen“."))
        self.backup_lbl.setWordWrap(True)
        self.pick_btn = QPushButton(T("Andere Sicherung …"))
        self.pick_btn.setToolTip(T("Eine Sicherung von Hand wählen, z. B. wenn sie unter der Adresse statt der "
                                   "Seriennummer abgelegt ist."))
        self.pick_btn.clicked.connect(self.pick_backup)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.backup_lbl, 1)
        h.addWidget(self.pick_btn)
        f.addRow(T("Sicherung"), h)
        self.host = QLineEdit()
        self.new_ip = QLineEdit()
        self.new_mask = QLineEdit()
        self.new_gw = QLineEdit()
        for w, ph in ((self.host, T("aus der Sicherung, sonst von Hand")), (self.new_ip, "10.20.0.21"),
                      (self.new_mask, "255.255.255.0"), (self.new_gw, "10.20.0.1")):
            w.setPlaceholderText(ph)
            w.textChanged.connect(self.update_actions)
        f.addRow(T("Hostname"), self.host)
        f.addRow(T("Neue IP"), self.new_ip)
        f.addRow(T("Subnetzmaske"), self.new_mask)
        f.addRow(T("Gateway"), self.new_gw)
        lay.addWidget(box)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left)
        scroll.setMinimumWidth(520)
        outer.addWidget(scroll, 5)

        # rechts: 5 · Schritte, Ergebnis, Ausführen
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        box = QGroupBox(T("5 · Was soll passieren?"))
        v = QVBoxLayout(box)
        self.do_ident = QCheckBox(T("HPE: Seriennummer und Produkt-ID setzen (gilt nach dem nächsten Neustart)"))
        self.do_ident.setToolTip(T("HPE iLO 5/6: schreibt Seriennummer und Produkt-ID vom Aufkleber ins neue Board.\n\n"
                                   "Wirksam nach dem nächsten Neustart. Es wird kein Neustart ausgelöst."))
        self.do_lic = QCheckBox(T("HPE: iLO-Lizenz einspielen"))
        self.do_pin = QCheckBox(T("Gemerktes Zertifikat des alten Mainboards vergessen"))
        self.do_svc = QCheckBox(T("Netzwerkdienste wie auf dem alten Board (IPMI, SSDP, SNMP …)"))
        self.do_svc.setToolTip(T("Schaltet die Dienste so ein oder aus, wie sie in der Sicherung standen.\n"
                                 "Konten und Passwörter stehen nicht in der Sicherung und bleiben unberührt."))
        self.do_bios = QCheckBox(T("UEFI-Einstellungen aus der Sicherung übernehmen (gilt nach dem nächsten Neustart)"))
        self.do_bios.setToolTip(T("Setzt jedes UEFI-Attribut, das auf dem neuen Board anders steht, auf den\n"
                                  "Wert aus der Sicherung (z. B. Startreihenfolge, Energieprofil). Nie gesetzt\n"
                                  "werden Passwort-, TPM- und Lösch-Attribute und die Kennung. Wirksam nach\n"
                                  "dem nächsten Neustart; es wird kein Neustart ausgelöst."))
        self.do_take = QCheckBox(T("Hostname und Adresse in die Serverliste übernehmen"))
        self.do_take.setToolTip(T("Trägt Hostname, neue IP, Maske und Gateway aus Schritt 4 in die Serverliste ein.\n\n"
                                  "Administrator und Netzwerk richtet danach „Neuware“ ein."))
        for w in (self.do_ident, self.do_lic, self.do_pin, self.do_svc, self.do_bios, self.do_take):
            w.setEnabled(False)
            v.addWidget(w)
        rl.addWidget(box)
        self.out = QTextBrowser()
        self.out.setOpenExternalLinks(False)
        self.out.setPlaceholderText(T("Hier erscheint das Ergebnis von „Prüfen“ und „Ausführen“."))
        rl.addWidget(self.out, 1)
        bar = QHBoxLayout()
        self.dry = QCheckBox(T("Trockenlauf (nichts ändern, nur zeigen)"))
        self.dry.setObjectName("toggle")
        self.dry.setChecked(True)
        self.run_btn = QPushButton(T("Ausführen"))
        self.run_btn.setObjectName("primary")
        self.run_btn.setMinimumHeight(34)
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self.execute)
        self.next_btn = QPushButton(T("Weiter in „Neuware“ →"))
        self.next_btn.setToolTip(T("Wechselt zu „Neuware“: Dort legt der Lauf den Administrator an und stellt "
                                   "Hostname und Adresse ein."))
        self.next_btn.clicked.connect(self.next_wanted)
        bar.addWidget(self.dry)
        bar.addStretch(1)
        bar.addWidget(self.next_btn)
        bar.addWidget(self.run_btn)
        rl.addLayout(bar)
        outer.addWidget(right, 4)
        # Beschriftungen aller Karten gleich breit: die Felder stehen untereinander auf einer Linie
        labels = [f.labelForField(f.itemAt(i, QFormLayout.ItemRole.FieldRole).widget()
                                  or f.itemAt(i, QFormLayout.ItemRole.FieldRole).layout())
                  for f in left.findChildren(QFormLayout) for i in range(f.rowCount())
                  if f.itemAt(i, QFormLayout.ItemRole.FieldRole) is not None]
        labels = [x for x in labels if isinstance(x, QLabel)]
        width = max((x.sizeHint().width() for x in labels), default=0)
        for x in labels:
            x.setMinimumWidth(width)
        self.scenario_changed()

    # ---------- Hilfen ----------
    def scenario(self):
        b = self.scen.checkedButton()
        return b.property("scen") if b is not None else "board"

    def set_row(self, ip, pw, serial):
        """Werte einer Zeile der Serverliste übernehmen (leere Werte lassen das Feld, wie es ist)."""
        for w, val in ((self.addr, ip), (self.pw, pw), (self.serial, serial)):
            if val:
                w.setText(val)

    def scenario_changed(self, *_):
        server = self.scenario() == "server"
        self.old_serial.setEnabled(server)
        self.old_serial_lab.setEnabled(server)
        if self.info is not None:
            self.find_backup()
        self.update_actions()

    def say(self, text, kind=""):
        color = {"err": "#d9534f", "ok": "#3fb950", "warn": "#d29922", "head": ""}.get(kind, "")
        t = html.escape(text).replace("  ", "&nbsp;&nbsp;")
        if kind == "head":
            t = f"<b>{t}</b>"
        self.out.append(f'<span style="color:{color}">{t}</span>' if color else f"<span>{t}</span>")

    def busy(self, on):
        self.check_btn.setEnabled(not on)
        self.run_btn.setEnabled(not on and self.info is not None and self.info.get("ok", False))

    # ---------- Prüfen ----------
    def check(self):
        ip = self.addr.text().strip()
        if not ip or not self.serial.text().strip():
            self.say(T("Aktuelle IP und Seriennummer vom Aufkleber eintragen."), "err")
            return
        self.out.clear()
        self.info = None
        self.update_actions()
        self.say("== " + ip, "head")
        self.busy(True)
        self.thread = BoardSwapThread(self, "read", dry=True, ip=ip, user=self.user.text().strip(), pw=self.pw.text())
        self.thread.line.connect(self.say)
        self.thread.done.connect(self.checked)
        self.thread.start()

    def backup_key(self):
        """Seriennummer, unter der die Sicherung gesucht wird: beim Ersatzgerät die des alten Servers."""
        if self.scenario() == "server":
            return self.old_serial.text().strip().upper()
        return self.serial.text().strip().upper()

    def find_backup(self):
        """Sicherung zur passenden Seriennummer suchen und Ziel-Felder vorbelegen."""
        path, doc = self.manual or latest_backup(self.backup_key())
        self.use_backup(path, doc)

    def pick_backup(self):
        start = i18n.DATA / "sicherungen"
        name, _ = QFileDialog.getOpenFileName(self, T("Sicherung wählen"), str(start), T("Sicherung (*.json)"))
        if not name:
            return
        try:
            doc = json.loads(Path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.say(T("  FEHLER: {0}").format(e), "err")
            return
        if not isinstance(doc, dict) or "resources" not in doc:
            self.say(T("Das ist keine Sicherung von BMC-Steward."), "err")
            return
        self.manual = (Path(name), doc)
        self.use_backup(Path(name), doc)

    def use_backup(self, path, doc):
        v = (self.info or {}).get("vendor", "")
        self.backup_path = path
        self.backup = backup_summary(doc) if doc else None
        self.backup_doc = doc if doc and (not v or self.backup["vendor"] in ("", v)) else None
        if self.backup:
            b = self.backup
            self.backup_lbl.setText(T("{0} vom {1}").format(path.name, b["time"] or "?"))
            self.say(T("  Sicherung des alten Boards gefunden: {0} ({1})").format(path.name, b["time"]), "ok")
            self.say(T("    Hostname {0}, Adresse {1}/{2}, Gateway {3}").format(
                b["hostname"] or "–", b["ip"] or "–", b["mask"] or "–", b["gw"] or "–"))
            for w, val in ((self.host, b["hostname"]), (self.new_ip, b["ip"]), (self.new_mask, b["mask"]),
                           (self.new_gw, b["gw"])):
                if val:
                    w.setText(val)
            if doc and self.backup_doc is None:
                self.say(T("  Die Sicherung stammt von einem anderen Hersteller ({0}); Dienste und UEFI werden nicht "
                           "daraus übernommen.").format(RedfishClient.NAME.get(b["vendor"], b["vendor"])), "warn")
        else:
            self.backup_lbl.setText(T("Keine gefunden. Hostname und Adresse von Hand eintragen."))
            self.say(T("  Keine Sicherung zu dieser Seriennummer gefunden (Ordner sicherungen/{0}).").format(
                folder_name(self.backup_key()) or "–"))
        self.update_actions()

    def checked(self, res):
        self.info = res
        label_sn, label_pid = self.serial.text().strip().upper(), self.pid.text().strip().upper()
        v = res.get("vendor", "")
        if not res.get("ok"):
            self.say(T("  FEHLER: {0}").format(res.get("text") or "?"), "err")
            self.info = None
            self.update_actions()
            self.busy(False)
            return
        scen = self.scenario()
        ident = res.get("sku") if v == "dell" else res.get("serial")
        same = bool(ident) and ident.upper() == label_sn
        if same:
            self.say(T("  Seriennummer stimmt mit dem Aufkleber überein ({0}).").format(ident), "ok")
        elif scen == "server":
            self.say(T("  Ersatzgerät meldet Seriennummer „{0}“; sie bleibt unverändert.").format(ident or "–"))
        else:
            self.say(T("  Seriennummer passt nicht: Board meldet „{0}“, Aufkleber „{1}“.").format(ident or "–", label_sn),
                     "warn")
        if v in ("hpe", "lenovo") and scen != "server" and not pid_matches(v, res, label_pid):
            same = False
            self.say(T("  Produkt-ID/Typ passt nicht: Board meldet „{0}“, Aufkleber „{1}“.").format(
                res.get("sku") or res.get("model") or "–", label_pid), "warn")
        self.find_backup()
        self.say("")
        self.say(T("Was zu tun ist:"), "head")
        for text, kind in vendor_advice(v, scen, same):
            self.say("  " + text, kind)
        if v == "lenovo" and not same and scen in ("board", "cpu"):
            for line in lenovo_commands(self.addr.text().strip(), res.get("user", "USERID"), label_pid, label_sn):
                self.say("    " + line)
        self.say(T("  Danach in „Neuware“ starten: Der Lauf legt den Administrator an und stellt Hostname und Adresse "
                   "ein. Die Eigenschutz-Liste und gespeicherte Profile bleiben unverändert."))
        self.update_actions(fresh=True)
        self.busy(False)

    def update_actions(self, *_, fresh=False):
        """Haken je Szenario, Hersteller und Sicherung freigeben; nach „Prüfen“ (fresh) auch vorbelegen."""
        res = self.info if self.info and self.info.get("ok") else None
        allow = SCENARIOS[self.scenario()]
        v = (res or {}).get("vendor", "")
        label_sn, label_pid = self.serial.text().strip().upper(), self.pid.text().strip().upper()
        doc = self.backup_doc
        key = (self.backup or {}).get("device", "")
        has_pin = bool(key) and key in trust.store().pins
        states = {
            self.do_ident: allow["ident"] and v == "hpe" and bool(label_sn or label_pid),
            self.do_lic: allow["lic"] and v == "hpe" and bool(self.lic.text().strip()),
            self.do_pin: allow["pin"] and has_pin,
            self.do_svc: allow["svc"] and doc is not None and v in ("hpe", "dell", "lenovo", "smc")
            and backup_part(doc, "/NetworkProtocol") is not None,
            self.do_bios: allow["bios"] and doc is not None and v in ("hpe", "dell", "lenovo", "smc")
            and backup_part(doc, "/Bios") is not None,
            self.do_take: allow["take"] and bool(self.new_ip.text().strip() or self.host.text().strip()),
        }
        for w, on in states.items():
            was = w.isEnabled()
            w.setEnabled(bool(res) and on)
            if not w.isEnabled():
                w.setChecked(False)
            elif fresh or not was:
                if w is self.do_ident:
                    w.setChecked(res.get("serial", "").upper() != label_sn
                                 or bool(label_pid and res.get("sku", "").upper() != label_pid))
                elif w is self.do_bios:
                    w.setChecked(False)         # UEFI nur bewusst: falsche Werte können den Start verhindern
                else:
                    w.setChecked(True)
        self.do_pin.setToolTip(T("Das alte Board ist als {0} gemerkt. Das neue Board hat ein anderes Zertifikat; der "
                                 "alte Eintrag ist danach überflüssig.").format(key) if has_pin else "")

    # ---------- Ausführen ----------
    def execute(self):
        if self.info is None:
            return
        dry = self.dry.isChecked()
        lic = self.lic.text().strip().upper() if self.do_lic.isChecked() else ""
        if lic and not LICENSE_RE.match(lic):
            self.say(T("  FEHLER: Lizenzschlüssel ungültig (erwartet: fünf Gruppen zu fünf Zeichen)."), "err")
            return
        if not dry and self.do_bios.isChecked() and QMessageBox.question(
                self, T("Mainboard getauscht"),
                T("UEFI-Einstellungen aus der Sicherung übernehmen? Falsche UEFI-Werte können dazu führen, dass ein "
                  "Server nicht mehr startet.")) != QMessageBox.StandardButton.Yes:
            return
        self.say("")
        self.say(T("— Trockenlauf —") if dry else T("— Scharfer Lauf —"), "head")
        if self.do_pin.isChecked() and self.backup:
            key = self.backup["device"]
            if dry:
                self.say(T("  würde das gemerkte Zertifikat {0} vergessen").format(key))
            else:
                trust.store().forget(key)
                self.say(T("  Gemerktes Zertifikat {0} vergessen.").format(key), "ok")
        if self.do_take.isChecked():
            vals = (self.host.text().strip(), self.new_ip.text().strip(), self.new_mask.text().strip(),
                    self.new_gw.text().strip())
            if dry:
                self.say(T("  würde in die Serverliste übernehmen: Hostname {0}, Adresse {1}").format(
                    vals[0] or "–", vals[1] or "–"))
            else:
                self.take = vals + (self.serial.text().strip().upper(),)
                self.take_ready.emit((self.addr.text().strip(),) + self.take)
                self.say(T("  In die Serverliste übernommen: Hostname {0}, Adresse {1}").format(
                    vals[0] or "–", vals[1] or "–"), "ok")
        ident = self.do_ident.isChecked()
        if ident or lic or self.do_svc.isChecked() or self.do_bios.isChecked():
            self.busy(True)
            self.thread = BoardSwapThread(self, "apply", dry=dry, ip=self.addr.text().strip(),
                                          user=self.info.get("user", ""), pw=self.pw.text(),
                                          vendor=self.info.get("vendor", ""),
                                          serial=self.serial.text().strip().upper() if ident else "",
                                          pid=self.pid.text().strip().upper() if ident else "", license=lic,
                                          doc=self.backup_doc, services=self.do_svc.isChecked(),
                                          bios=self.do_bios.isChecked())
            self.thread.line.connect(self.say)
            self.thread.done.connect(lambda res, dry=dry: self.ident_done(res, dry))
            self.thread.start()
        elif not dry:
            self.ident_done({"ok": True}, dry)

    def ident_done(self, res, dry=True):
        self.busy(False)
        if not dry and res.get("ok"):
            self.clear_secrets()                    # Passwort und Lizenz nur so lange wie nötig im Speicher
            self.say(T("Fertig. Passwort und Lizenzschlüssel sind aus den Feldern gelöscht. Weiter in „Neuware“ "
                       "für Administrator und Netzwerk."), "ok")

    def clear_secrets(self):
        """Passwort und Lizenz aus den Feldern nehmen (nach dem Lauf oder beim Schließen)."""
        self.pw.clear()
        self.lic.clear()

    def stop_thread(self):
        if self.thread is not None and self.thread.isRunning():
            self.thread.requestInterruption()
            self.thread.wait(15000)
