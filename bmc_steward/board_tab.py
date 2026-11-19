"""BMC-Steward: Seite „Mainboard-Tausch“ – nach dem Tausch des Mainboards einen Server wieder auf Stand bringen
(Zertifikate, Kennung des Gehäuses je Hersteller, Einstellungen aus der Sicherung des alten Boards)."""
import html
import json

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtGui import QFontDatabase

from . import i18n
from .applog import LOG
from .board import IDENT_RE, LICENSE_RE, BoardEngineThread, backup_system
from .core import intro_label
from .i18n import T
from .redfish import RedfishClient


class BoardTabMixin:
    """Seite „Mainboard-Tausch“ des Hauptfensters."""

    def build_board(self):
        self.board_worker = None
        self.board_backups = []          # (Pfad, Metadaten, System-Ressource) je Sicherung, neueste zuerst

        self.board_ip = QComboBox()
        self.board_ip.setEditable(True)
        self.board_ip.setToolTip(T("Adresse des BMC mit dem neuen Mainboard. Vorschläge kommen aus der Serverliste "
                                   "(„Neuware“)."))
        self.board_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.board_vendor.addItem(text, key)
        self.board_user = QLineEdit()
        self.board_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.board_pass = self._pw()
        box_srv = QGroupBox(T("Server mit neuem Mainboard"))
        f = QFormLayout(box_srv)
        f.addRow(T("Adresse"), self.board_ip)
        self.tip_row(f, T("Hersteller"), self.board_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den\n"
                       "Hersteller dieses einen Geräts selbst."))
        self.tip_row(f, T("Benutzer"), self.board_user,
                     T("Ein neues Board meldet sich meist mit dem Werkskonto. Leer lassen: Dann wird je\n"
                       "Hersteller der Werksbenutzer verwendet (root/Administrator/ADMIN/USERID)."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.board_pass),
                     T("Passwort für diesen Benutzer, bei einem neuen Board meist das Werkspasswort vom Aufkleber."))

        self.board_backup = QComboBox()
        self.board_backup.setToolTip(T("Automatische Sicherung, die BMC-Steward vor einer Änderung am alten Board\n"
                                       "angelegt hat. Daraus kommen Seriennummer, Produkt-ID und die Einstellungen\n"
                                       "zum Zurückspielen. Vorgewählt ist die neueste Sicherung derselben Adresse."))
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip(T("Sicherungen und Adressen neu einlesen"))
        refresh.clicked.connect(self.board_refresh)
        brow = QHBoxLayout()
        brow.addWidget(self.board_backup, 1)
        brow.addWidget(refresh)
        self.board_backup_info = QLabel()
        self.board_backup_info.setWordWrap(True)
        box_bak = QGroupBox(T("Sicherung des alten Boards"))
        fb = QFormLayout(box_bak)
        fb.addRow(T("Sicherung"), brow)
        fb.addRow("", self.board_backup_info)

        self.board_serial = QLineEdit()
        self.board_pid = QLineEdit()
        self.board_lic = QLineEdit()
        for w in (self.board_serial, self.board_pid, self.board_lic):
            w.textEdited.connect(lambda txt, w=w: w.setText(txt.upper()))
        self.board_serial.setMaxLength(20)
        self.board_pid.setMaxLength(20)
        self.board_lic.setMaxLength(29)
        self.board_lic.setPlaceholderText("XXXXX-XXXXX-XXXXX-XXXXX-XXXXX")
        box_hpe = QGroupBox(T("HPE iLO 5/6"))
        fh = QFormLayout(box_hpe)
        ident_tip = T("Das neue Board kennt Seriennummer und Produkt-ID des Gehäuses nicht. Beide stehen auf dem\n"
                      "Aufkleber am Gehäuse und müssen genau übereinstimmen. Leer lassen: Dann gelten die Werte aus\n"
                      "der Sicherung. Gesetzt wird als UEFI-Einstellung, wirksam nach dem nächsten Neustart.")
        self.tip_row(fh, T("Seriennummer"), self.board_serial, ident_tip)
        self.tip_row(fh, T("Produkt-ID"), self.board_pid, ident_tip)
        self.tip_row(fh, T("Lizenzschlüssel"), self.board_lic,
                     T("Optional: die iLO-Lizenz (z. B. Advanced) hängt am Board und muss neu eingespielt werden.\n"
                       "Leer lassen, wenn keine Lizenz gebraucht wird."))
        other = QLabel(T("Dell holt Service-Tag, Lizenz und Einstellungen beim nächsten Start selbst zurück („Easy "
                         "Restore“, Abfrage in der UEFI-Konsole bestätigen). Bei Lenovo (OneCLI) und Supermicro "
                         "(SUM) schreibt das Werkzeug des Herstellers die Kennung; BMC-Steward prüft dort nur und "
                         "sagt, was fehlt."))
        other.setWordWrap(True)

        self.board_rest_svc = QCheckBox(T("Netzwerkdienste wie auf dem alten Board (IPMI, SSH, SNMP …)"))
        self.board_rest_svc.setChecked(True)
        self.board_rest_svc.setToolTip(T("Schaltet die Dienste so ein oder aus, wie sie in der Sicherung standen.\n"
                                         "Konten und Passwörter stehen nicht in der Sicherung und bleiben unberührt."))
        self.board_rest_bios = QCheckBox(T("UEFI-Einstellungen aus der Sicherung übernehmen"))
        self.board_rest_bios.setToolTip(T("Setzt jedes UEFI-Attribut, das auf dem neuen Board anders steht, auf den\n"
                                          "Wert aus der Sicherung (z. B. Startreihenfolge, Energieprofil). Nie gesetzt\n"
                                          "werden Passwort-, TPM- und Lösch-Attribute und die Kennung. Wirksam nach\n"
                                          "dem nächsten Neustart; es wird kein Neustart ausgelöst."))
        box_rest = QGroupBox(T("Aus der Sicherung zurückspielen"))
        vr = QVBoxLayout(box_rest)
        vr.addWidget(self.board_rest_svc)
        vr.addWidget(self.board_rest_bios)

        self.board_dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.board_dry.setChecked(True)
        self.board_dry.setToolTip(T("Mit Haken wird nur gelesen und angezeigt, was geändert würde.\n"
                                    "Gemerkte Zertifikate bleiben im Trockenlauf erhalten."))
        self.board_start = QPushButton(T("Mainboard-Tausch abschließen"))
        self.board_start.setMinimumHeight(34)
        self.board_start.clicked.connect(self.board_run)
        self.board_stop = QPushButton(T("Abbrechen"))
        self.board_stop.setEnabled(False)
        self.board_stop.clicked.connect(self.board_abort)
        actions = QHBoxLayout()
        actions.addWidget(self.board_dry)
        actions.addStretch()
        actions.addWidget(self.board_stop)
        actions.addWidget(self.board_start)

        self.board_log = QTextEdit()
        self.board_log.setReadOnly(True)
        self.board_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.board_status = QLabel(T("Bereit"))

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(intro_label(T(
            "<b>Mainboard-Tausch</b> bringt genau einen Server nach dem Tausch des Mainboards wieder auf Stand: Die "
            "gemerkten Zertifikate der Adresse werden vergessen (das neue Board hat ein neues), je Hersteller wird "
            "die Kennung des Gehäuses geprüft bzw. gesetzt, und auf Wunsch kommen Dienste und UEFI-Einstellungen "
            "aus der Sicherung des alten Boards zurück. Erst im Trockenlauf prüfen, dann scharf starten.")))
        top = QHBoxLayout()
        top.addWidget(box_srv, 1)
        top.addWidget(box_bak, 1)
        lay.addLayout(top)
        mid = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(box_hpe)
        left.addWidget(other)
        mid.addLayout(left, 1)
        mid.addWidget(box_rest, 1)
        lay.addLayout(mid)
        lay.addLayout(actions)
        lay.addWidget(self.board_log, 1)
        lay.addWidget(self.board_status)
        self.board_ip.currentTextChanged.connect(self.board_pick_backup)
        self.board_backup.currentIndexChanged.connect(self.board_show_backup)
        self.board_refresh()
        return page

    # ---------- Sicherungen ----------
    def board_load_backups(self):
        base = i18n.DATA / "sicherungen"
        out = []
        if base.is_dir():
            for path in base.rglob("*.json"):
                try:
                    with open(path, encoding="utf-8") as fh:
                        doc = json.load(fh)
                except (OSError, ValueError):
                    continue
                if isinstance(doc, dict) and isinstance(doc.get("resources"), dict):
                    out.append((path, doc.get("meta") or {}, backup_system(doc)))
        out.sort(key=lambda e: (str(e[1].get("time", "")), e[0].name), reverse=True)
        return out

    def board_refresh(self):
        keep_ip = self.board_ip.currentText()
        self.board_ip.blockSignals(True)
        self.board_ip.clear()
        for ip, _ in self.reachable_rows():
            self.board_ip.addItem(ip)
        self.board_ip.setEditText(keep_ip)
        self.board_ip.blockSignals(False)
        self.board_backups = self.board_load_backups()
        self.board_backup.blockSignals(True)
        self.board_backup.clear()
        self.board_backup.addItem(T("keine Sicherung verwenden"), None)
        for path, meta, system in self.board_backups:
            ident = system.get("SKU") if meta.get("vendor") == "dell" else system.get("SerialNumber")
            self.board_backup.addItem("  ·  ".join(str(x) for x in (
                str(meta.get("time", "?"))[:16].replace("T", " "), meta.get("address", "?"),
                RedfishClient.NAME.get(meta.get("vendor"), "?"), ident or "–", meta.get("reason", "")) if x),
                str(path))
        self.board_backup.blockSignals(False)
        self.board_pick_backup()

    def board_pick_backup(self, *_):
        """Wählt die neueste Sicherung derselben Adresse, die nicht schon vom neuen Board stammt."""
        ip = self.board_ip.currentText().strip()
        idx = 0
        for i, (path, meta, _) in enumerate(self.board_backups):
            if ip and meta.get("address") == ip and meta.get("reason") != "mainboard":
                idx = i + 1
                break
        self.board_backup.setCurrentIndex(idx)
        self.board_show_backup()

    def board_show_backup(self, *_):
        i = self.board_backup.currentIndex() - 1
        if i < 0 or i >= len(self.board_backups):
            self.board_backup_info.setText(T("Ohne Sicherung: nur Zertifikate zurücksetzen und die Kennung prüfen."))
            return
        _, meta, system = self.board_backups[i]
        self.board_backup_info.setText(T("Seriennummer {0}, Produkt-ID/Service-Tag {1}, Modell {2}").format(
            system.get("SerialNumber") or "–", system.get("SKU") or "–", system.get("Model") or "–"))

    # ---------- Lauf ----------
    def board_run(self):
        ip = self.board_ip.currentText().strip()
        if not ip:
            QMessageBox.warning(self, T("Mainboard-Tausch"), T("Bitte die Adresse des Servers eintragen."))
            return
        if not self.board_pass.text():
            QMessageBox.warning(self, T("Mainboard-Tausch"), T("Bitte das Passwort eintragen."))
            return
        serial, pid, lic = (w.text().strip().upper() for w in (self.board_serial, self.board_pid, self.board_lic))
        bad = [n for n, v, rx in ((T("Seriennummer"), serial, IDENT_RE), (T("Produkt-ID"), pid, IDENT_RE),
                                  (T("Lizenzschlüssel"), lic, LICENSE_RE)) if v and not rx.match(v)]
        if bad:
            QMessageBox.warning(self, T("Mainboard-Tausch"), T("Ungültig: {0}. Erlaubt sind Großbuchstaben, Ziffern und "
                                                             "Bindestrich, genau wie auf dem Aufkleber.").format(", ".join(bad)))
            return
        backup = self.board_backup.currentData()
        dry = self.board_dry.isChecked()
        if not dry:
            info = T("Die gemerkten Zertifikate dieser Adresse werden vergessen; das Gerät, das sich jetzt unter "
                     "{0} meldet, wird als neues Board gemerkt. Vorher sicherstellen, dass es wirklich der Server "
                     "mit dem neuen Mainboard ist.").format(ip)
            if serial or pid:
                info += "\n\n" + T("HPE: Seriennummer {0}, Produkt-ID {1} (wirkt nach dem nächsten Neustart des Servers).").format(
                    serial or "–", pid or "–")
            if backup and self.board_rest_bios.isChecked():
                info += "\n\n" + T("Falsche UEFI-Werte können dazu führen, dass ein Server nicht mehr startet.")
            info += self.selfguard_note()
            if not self.confirm(T("Scharf ausführen?"), T("Mainboard-Tausch für {0} jetzt wirklich abschließen?").format(ip),
                                info):
                return
        self.board_log.clear()
        self.board_say(T("— Trockenlauf —") if dry else T("— Scharfer Lauf —"), "gray")
        self.board_worker = BoardEngineThread(
            self, ip=ip, user=self.board_user.text(), pw=self.board_pass.text(), backup_path=backup,
            hpe_serial=serial, hpe_pid=pid, hpe_license=lic, restore_bios=self.board_rest_bios.isChecked(),
            restore_services=self.board_rest_svc.isChecked(), dry=dry, vendor=self.board_vendor.currentData() or None)
        self.board_worker.line.connect(self.board_show_line)
        self.board_worker.result.connect(self.board_done)
        self.board_start.setEnabled(False)
        self.board_stop.setEnabled(True)
        self.board_status.setText(T("Läuft …"))
        self.board_worker.start()

    def board_say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.board_log.append(f'<span style="{style}">{html.escape(text)}</span>')
        LOG.info(T("Mainboard-Tausch") + ": " + text, color, bold)

    def board_show_line(self, line, kind=""):
        self.board_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def board_abort(self):
        if self.board_worker and self.board_worker.isRunning():
            self.board_worker.requestInterruption()
            self.board_stop.setEnabled(False)

    def board_done(self, code, *_):
        self.board_start.setEnabled(True)
        self.board_stop.setEnabled(False)
        self.board_refresh()
        self.board_status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))
