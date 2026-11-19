"""BMC-Steward: Modus „Mainboard getauscht“ für genau einen Server.

Nach einem Tausch der Systemplatine kennt das neue Board die Identität des Gehäuses nicht (Seriennummer bzw.
Service-Tag, Produkt-ID/Maschinentyp), hat ein neues BMC-Zertifikat und keine Einstellungen. Der Dialog
1. liest nur lesend, was das neue Board meldet, und vergleicht es mit dem Aufkleber am Gehäuse,
2. sucht die letzte Sicherung des alten Boards (sicherungen/<Seriennummer>/) und zeigt Hostname und Adresse,
3. sagt je Hersteller, was zu tun ist, und erledigt, was per Redfish sicher geht:
   - HPE iLO 5/6: Seriennummer und Produkt-ID als UEFI-Attribute (SerialNumber/ProductId, gilt nach Neustart)
   - Dell: Service-Tag kommt über „Easy Restore“ beim nächsten Start; hier nur Prüfung und Anleitung
   - Lenovo: VPD nur über Provisioning Manager oder OneCLI; der Dialog erzeugt die Befehle (ohne Passwort)
   - Supermicro: DMI nur über SUM (GetDmiInfo/ChangeDmiInfo, Lizenz nötig); Anleitung
4. vergisst auf Wunsch das gemerkte Zertifikat des alten Boards und
5. trägt Hostname und Adresse aus der Sicherung in die Serverliste ein; Benutzer und Netzwerk richtet dann
   der normale Neuware-Lauf ein.
Ändernde Schritte respektieren den Trockenlauf (voreingestellt) und laufen nach Sicherung und Eigenschutz.
"""
import html
import json
import re

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextBrowser, QVBoxLayout)

from . import i18n, trust
from .i18n import T
from .redfish import RedfishClient


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


def lenovo_commands(ip, user, mtm, serial):
    """OneCLI-Befehle für das VPD-Update nach Lenovo-Anleitung; das Passwort steht nie darin."""
    bmc = f"--bmc {user}:<Passwort>@{ip}"
    lines = []
    if mtm:
        lines.append(f"onecli config set SYSTEM_PROD_DATA.SysInfoProdName {mtm} {bmc}")
    if serial:
        lines.append(f"onecli config set SYSTEM_PROD_DATA.SysInfoSerialNum {serial} {bmc}")
    return lines


class BoardSwapEngine(RedfishClient):
    """Liest die Identität des neuen Boards (nur GET, ein Anmeldeversuch) und setzt bei HPE iLO 5/6 Seriennummer
    und Produkt-ID."""

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
        self.cur_user = user or self.DEFAULT_USER["hpe"]
        attrs = {k: v for k, v in (("SerialNumber", serial), ("ProductId", pid)) if v}
        if not attrs:
            return True
        if not self.self_guard(ip, self.cur_user, pw, "hpe"):
            return False
        if not self.dry:
            self.backup(ip, self.cur_user, pw, "hpe", "mainboard")
        if not self.attempt(T("Seriennummer/Produkt-ID"), ip, pw, "PATCH", self.sys_path("hpe") + "/Bios/Settings",
                            {"Attributes": attrs}):
            return False
        self.log(T("  Seriennummer/Produkt-ID vorgemerkt: {0} (gilt nach dem nächsten Neustart des Servers)").format(
            ", ".join(f"{k}={v}" for k, v in attrs.items())), "ok")
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
                res = {"ok": eng.set_hpe_ident(**self.kw)}
        except Exception as e:                      # nie den Dialog mitreißen
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            res = {"ok": False, "text": repr(e)}
        finally:
            try:
                eng.close_sessions()
            except Exception:
                pass
        self.done.emit(res)


class BoardSwapDialog(QDialog):
    """Dialog für einen Server. `row` = (Aktuelle IP, Passwort, Seriennummer) der markierten Zeile.
    Nach „Übernehmen“ steht in `take` (Hostname, IP, Maske, Gateway, Seriennummer) für die Serverliste."""

    def __init__(self, parent=None, row=("", "", ""), user=""):
        super().__init__(parent)
        self.setWindowTitle(T("Mainboard getauscht"))
        self.resize(760, 640)
        self.info = None            # Ergebnis von „Prüfen“
        self.backup = None          # Zusammenfassung der letzten Sicherung
        self.take = None
        self.thread = None
        lay = QVBoxLayout(self)
        intro = QLabel(T("Für genau einen Server nach dem Tausch der Systemplatine: Das Tool vergleicht, was das neue "
                         "Board meldet, mit dem Aufkleber am Gehäuse, sucht die Sicherung des alten Boards und zeigt je "
                         "Hersteller, was zu tun ist. Prüfen ändert nichts."))
        intro.setWordWrap(True)
        lay.addWidget(intro)

        box = QGroupBox(T("Neues Mainboard"))
        f = QFormLayout(box)
        self.addr = QLineEdit(row[0])
        self.user = QLineEdit(user)
        self.user.setPlaceholderText(T("automatisch je Hersteller"))
        self.pw = QLineEdit(row[1])
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow(T("Aktuelle IP"), self.addr)
        f.addRow(T("Benutzer"), self.user)
        f.addRow(T("Passwort"), self.pw)
        lay.addWidget(box)

        box = QGroupBox(T("Aufkleber am Gehäuse"))
        f = QFormLayout(box)
        self.serial = QLineEdit(row[2])
        self.serial.setPlaceholderText(T("Seriennummer bzw. Service-Tag (Dell)"))
        self.pid = QLineEdit()
        self.pid.setPlaceholderText(T("HPE: Produkt-ID (z. B. 868703-B21), Lenovo: Maschinentyp/Modell (z. B. 7X02CTO1WW)"))
        for w in (self.serial, self.pid):
            w.setMaxLength(24)
            w.textEdited.connect(lambda txt, w=w: w.setText(txt.upper()))
        f.addRow(T("Seriennummer"), self.serial)
        f.addRow(T("Produkt-ID / Typ"), self.pid)
        lay.addWidget(box)

        row_btn = QHBoxLayout()
        self.check_btn = QPushButton(T("Prüfen (nur lesend)"))
        self.check_btn.clicked.connect(self.check)
        row_btn.addWidget(self.check_btn)
        row_btn.addStretch(1)
        lay.addLayout(row_btn)

        self.out = QTextBrowser()
        self.out.setOpenExternalLinks(False)
        lay.addWidget(self.out, 1)

        self.do_ident = QCheckBox(T("HPE: Seriennummer und Produkt-ID setzen (gilt nach dem nächsten Neustart)"))
        self.do_pin = QCheckBox(T("Gemerktes Zertifikat des alten Mainboards vergessen"))
        self.do_take = QCheckBox(T("Hostname und Adresse aus der Sicherung in die Serverliste übernehmen"))
        self.dry = QCheckBox(T("Trockenlauf (nichts ändern, nur zeigen)"))
        self.dry.setChecked(True)
        for w in (self.do_ident, self.do_pin, self.do_take):
            w.setEnabled(False)
            lay.addWidget(w)
        lay.addWidget(self.dry)

        bb = QDialogButtonBox()
        self.run_btn = bb.addButton(T("Ausführen"), QDialogButtonBox.ButtonRole.ActionRole)
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self.execute)
        bb.addButton(T("Schließen"), QDialogButtonBox.ButtonRole.RejectRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ---------- Ausgabe ----------
    def say(self, text, kind=""):
        color = {"err": "#d9534f", "ok": "#3fb950", "warn": "#d29922", "head": ""}.get(kind, "")
        t = html.escape(text).replace("  ", "&nbsp;&nbsp;")
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
        self.say("== " + ip, "head")
        self.busy(True)
        self.thread = BoardSwapThread(self, "read", dry=True, ip=ip, user=self.user.text().strip(), pw=self.pw.text())
        self.thread.line.connect(self.say)
        self.thread.done.connect(self.checked)
        self.thread.start()

    def checked(self, res):
        self.info = res
        label_sn, label_pid = self.serial.text().strip().upper(), self.pid.text().strip().upper()
        v = res.get("vendor", "")
        if not res.get("ok"):
            self.say(T("  FEHLER: {0}").format(res.get("text") or "?"), "err")
            self.busy(False)
            return
        ident = res.get("sku") if v == "dell" else res.get("serial")
        if ident and ident.upper() == label_sn:
            self.say(T("  Seriennummer stimmt mit dem Aufkleber überein ({0}).").format(ident), "ok")
        else:
            self.say(T("  Seriennummer passt nicht: Board meldet „{0}“, Aufkleber „{1}“.").format(ident or "–", label_sn),
                     "warn")
        # Sicherung des alten Boards
        path, doc = latest_backup(label_sn)
        self.backup = backup_summary(doc) if doc else None
        if self.backup:
            b = self.backup
            self.say(T("  Sicherung des alten Boards gefunden: {0} ({1})").format(path.name, b["time"]), "ok")
            self.say(T("    Hostname {0}, Adresse {1}/{2}, Gateway {3}").format(
                b["hostname"] or "–", b["ip"] or "–", b["mask"] or "–", b["gw"] or "–"))
        else:
            self.say(T("  Keine Sicherung zu dieser Seriennummer gefunden (Ordner sicherungen/{0}).").format(
                folder_name(label_sn) or "–"))
        # Was je Hersteller zu tun ist
        self.say("")
        self.say(T("Was zu tun ist:"), "head")
        if v == "hpe":
            if res.get("serial", "").upper() == label_sn and (not label_pid or res.get("sku", "").upper() == label_pid):
                self.say(T("  HPE: Seriennummer und Produkt-ID stimmen schon."), "ok")
            else:
                self.say(T("  HPE: Seriennummer und Produkt-ID setzen (Haken unten). Sie gelten nach dem nächsten "
                           "Neustart des Servers. Die iLO-Lizenz danach neu einspielen."))
        elif v == "hpe_old":
            self.say(T("  HPE iLO 3/4: Seriennummer und Produkt-ID beim Start mit F9 im RBSU unter "
                       "„Advanced Options → Service Options“ eintragen."))
        elif v == "dell":
            if ident and ident.upper() == label_sn:
                self.say(T("  Dell: Der Service-Tag stimmt, „Easy Restore“ hat gegriffen. Lizenz und "
                           "UEFI-Einstellungen prüfen."), "ok")
            else:
                self.say(T("  Dell: Den Service-Tag stellt „Easy Restore“ wieder her. Server einschalten und die Abfrage "
                           "beim Start bestätigen. Kommt keine Abfrage, setzt ihn der Dell-Service; per Redfish geht das "
                           "nicht."))
        elif v == "lenovo":
            self.say(T("  Lenovo: Maschinentyp und Seriennummer lassen sich nur einmal setzen, solange beide leer "
                       "sind. Mit dem Provisioning Manager (F1 beim Start, „Update VPD“) oder mit OneCLI:"))
            for line in lenovo_commands(self.addr.text().strip(), res.get("user", "USERID"), label_pid, label_sn):
                self.say("    " + line)
        elif v == "smc":
            self.say(T("  Supermicro: Die DMI-Daten lassen sich mit dem Supermicro Update Manager (SUM) ändern: erst "
                       "GetDmiInfo, Datei bearbeiten, dann ChangeDmiInfo. Dafür ist eine Lizenz (SFT-OOB-LIC oder "
                       "SFT-DCMS-SINGLE) nötig."))
        self.say(T("  Danach in „Neuware“ starten: Der Lauf legt den Administrator an und stellt Hostname und Adresse "
                   "ein. Die Eigenschutz-Liste und gespeicherte Profile bleiben unverändert."))
        # Haken nur, wo sie sinnvoll sind
        can_ident = v == "hpe" and bool(label_sn or label_pid)
        self.do_ident.setEnabled(can_ident)
        self.do_ident.setChecked(can_ident and res.get("serial", "").upper() != label_sn)
        key = (self.backup or {}).get("device", "")
        has_pin = bool(key) and key in trust.store().pins
        self.do_pin.setEnabled(has_pin)
        self.do_pin.setChecked(has_pin)
        self.do_pin.setToolTip(T("Das alte Board ist als {0} gemerkt. Das neue Board hat ein anderes Zertifikat; der "
                                 "alte Eintrag ist danach überflüssig.").format(key) if has_pin else "")
        can_take = bool(self.backup and (self.backup["ip"] or self.backup["hostname"]))
        self.do_take.setEnabled(can_take)
        self.do_take.setChecked(can_take)
        self.busy(False)

    # ---------- Ausführen ----------
    def execute(self):
        if self.info is None:
            return
        dry = self.dry.isChecked()
        self.say("")
        self.say(T("— Trockenlauf —") if dry else T("— Scharfer Lauf —"), "head")
        if self.do_pin.isChecked() and self.backup:
            key = self.backup["device"]
            if dry:
                self.say(T("  würde das gemerkte Zertifikat {0} vergessen").format(key))
            else:
                trust.store().forget(key)
                self.say(T("  Gemerktes Zertifikat {0} vergessen.").format(key), "ok")
        if self.do_take.isChecked() and self.backup:
            b = self.backup
            if dry:
                self.say(T("  würde in die Serverliste übernehmen: Hostname {0}, Adresse {1}").format(
                    b["hostname"] or "–", b["ip"] or "–"))
            else:
                self.take = (b["hostname"], b["ip"], b["mask"], b["gw"], self.serial.text().strip().upper())
                self.say(T("  In die Serverliste übernommen: Hostname {0}, Adresse {1}").format(
                    b["hostname"] or "–", b["ip"] or "–"), "ok")
        if self.do_ident.isChecked():
            self.busy(True)
            self.thread = BoardSwapThread(self, "ident", dry=dry, ip=self.addr.text().strip(),
                                          user=self.info.get("user", ""), pw=self.pw.text(),
                                          serial=self.serial.text().strip().upper(), pid=self.pid.text().strip().upper())
            self.thread.line.connect(self.say)
            self.thread.done.connect(self.ident_done)
            self.thread.start()

    def ident_done(self, res):
        self.busy(False)

    def closeEvent(self, e):
        self.stop_thread()
        super().closeEvent(e)

    def done(self, code):
        self.stop_thread()
        super().done(code)

    def stop_thread(self):
        if self.thread is not None and self.thread.isRunning():
            self.thread.requestInterruption()
            self.thread.wait(15000)
