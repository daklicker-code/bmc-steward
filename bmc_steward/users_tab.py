"""BMC-Steward: Reiter „Benutzer" - Benutzerverwaltung über den Redfish-AccountService.

Zwei Bereiche: oben die Konten eines einzelnen, gewählten Geräts (Liste/Anlegen/Löschen/Rolle/
Passwort/SSH-Key), unten die Passwort-Rotation über die ganze Serverliste („Neuware“), auf Wunsch
(On-Demand) oder geplant (ein In-App-Zeitgeber, läuft nur solange BMC-Steward läuft). Neu gesetzte
Passwörter landen nie nur im Protokoll: sie werden in der Serverliste nachgetragen und zusätzlich in
einer eigenen, nur für den eigenen Benutzer lesbaren Datei gesichert - bis die Schlüsselbund-
Integration (geplant) das übernimmt, ist das die Absicherung gegen Aussperren.
"""
import json
import time

from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                             QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtCore import QTimer, Qt

from . import i18n, vault
from .applog import LOG
from .i18n import T
from .redfish import Engine, UsersActionThread, UsersEngine, UsersEngineThread, UsersListThread
from .core import set_role


class NewAccountDialog(QDialog):
    """Kleiner Dialog für ein neues Konto: Benutzername, Passwort, Rolle."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(T("Neues Konto"))
        lay = QFormLayout(self)
        self.user = QLineEdit()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        gen = QPushButton(T("Zufallspasswort"))
        gen.clicked.connect(lambda: self.pw.setText(Engine.random_password()))
        pwrow = QHBoxLayout()
        pwrow.addWidget(self.pw, 1)
        pwrow.addWidget(gen)
        self.role = QComboBox()
        for r in UsersEngine.ROLES:
            self.role.addItem(r, r)
        lay.addRow(T("Benutzername"), self.user)
        lay.addRow(T("Passwort"), pwrow)
        lay.addRow(T("Rolle"), self.role)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addRow(buttons)

    def values(self):
        return self.user.text().strip(), self.pw.text(), self.role.currentData()


class UsersTabMixin:
    """Reiter „Benutzer" des Hauptfensters."""

    def build_users(self):
        self.users_reader = None
        self.users_worker = None
        self.users_action_thread = None
        self.users_accounts = []
        self.users_vendor_cur = ""
        self.users_ref_pws = {}

        # --- Konten eines Geräts ---
        login_box = QGroupBox(T("Gerät"))
        self.users_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.users_vendor.addItem(text, key)
        self.users_user = QLineEdit()
        self.users_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.users_pass = self._pw()
        lf = QFormLayout(login_box)
        self.tip_row(lf, T("Hersteller"), self.users_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ fragt das Gerät."))
        self.tip_row(lf, T("Benutzer"), self.users_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet."))
        self.tip_row(lf, T("Passwort"), self._with_eye(self.users_pass),
                     T("Fällt für Geräte ein, deren Spalte „Passwort“ in der Serverliste leer ist."))
        self.users_ip = QComboBox()
        self.users_ip.setEditable(True)
        self.users_ip.setToolTip(T("Adresse des Geräts. Vorschläge aus der Serverliste („Neuware“)."))
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.clicked.connect(self.users_refresh_ips)
        load_btn = QPushButton(T("Konten laden"))
        set_role(load_btn, "go")
        load_btn.clicked.connect(self.users_load)
        iprow = QHBoxLayout()
        iprow.addWidget(QLabel(T("Adresse")))
        iprow.addWidget(self.users_ip, 1)
        iprow.addWidget(refresh)
        iprow.addWidget(load_btn)

        accounts_box = QGroupBox(T("Konten"))
        accounts_box.setToolTip(T("Konten dieses Geräts (Redfish-AccountService). SSH-Key-Verwaltung ist "
                                  "ein neuerer DMTF-Standard und auf echter Hardware ungeprüft; nicht "
                                  "unterstützende Firmware wird übersprungen, nicht als Fehler gemeldet."))
        av = QVBoxLayout(accounts_box)
        av.addLayout(iprow)
        self.users_table = QTableWidget(0, 5)
        self.users_table.setHorizontalHeaderLabels(
            [T("Benutzer"), T("Rolle"), T("Aktiv"), T("Gesperrt"), T("SSH-Keys")])
        self.users_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.users_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.users_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.users_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.users_table.setMaximumHeight(170)
        av.addWidget(self.users_table)
        self.users_status = QLabel()
        self.users_status.setWordWrap(True)
        av.addWidget(self.users_status)

        btn_add = QPushButton(T("Anlegen …"))
        btn_add.clicked.connect(self.users_create)
        btn_del = QPushButton(T("Löschen"))
        btn_del.clicked.connect(self.users_delete)
        btn_role = QPushButton(T("Rolle ändern …"))
        btn_role.clicked.connect(self.users_change_role)
        btn_pw = QPushButton(T("Passwort setzen …"))
        btn_pw.clicked.connect(self.users_set_password)
        btn_key_add = QPushButton(T("SSH-Key setzen …"))
        btn_key_add.clicked.connect(self.users_add_ssh_key)
        btn_key_del = QPushButton(T("SSH-Keys entfernen"))
        btn_key_del.clicked.connect(self.users_delete_ssh_keys)
        self.users_buttons = [btn_add, btn_del, btn_role, btn_pw, btn_key_add, btn_key_del]
        brow = QHBoxLayout()
        for b in self.users_buttons:
            brow.addWidget(b)
        av.addLayout(brow)

        # --- Passwort-Rotation über die Serverliste ---
        rot_box = QGroupBox(T("Passwort-Rotation (ganze Serverliste „Neuware“)"))
        rot_box.setToolTip(T("Rotiert das Passwort des gerade angemeldeten Benutzers auf allen Servern der "
                             "Liste. Neue Passwörter werden sofort in der Spalte „Passwort“ nachgetragen "
                             "und zusätzlich in einer eigenen Datei gesichert (siehe „Sicherungen/"
                             "Protokolle“) - die Serverliste danach bitte speichern, sonst gehen sie beim "
                             "Beenden verloren."))
        rv = QVBoxLayout(rot_box)
        self.rot_dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.rot_dry.setChecked(True)
        self.rot_dry.toggled.connect(self.rot_dry_changed)
        self.rot_now = QPushButton()
        set_role(self.rot_now, "go")
        self.rot_now.clicked.connect(self.rot_run_now)
        row1 = QHBoxLayout()
        row1.addWidget(self.rot_dry)
        row1.addStretch()
        row1.addWidget(self.rot_now)
        rv.addLayout(row1)

        self.rot_enabled = QCheckBox(T("Geplante Rotation aktiv"))
        self.rot_enabled.setToolTip(T("Läuft nur, solange BMC-Steward geöffnet ist (kein Systemdienst). "
                                      "Prüft stündlich, ob das Intervall abgelaufen ist, und rotiert dann "
                                      "scharf und ohne Rückfrage - Protokoll und neue Passwörter wie oben."))
        self.rot_interval = QSpinBox()
        self.rot_interval.setRange(1, 3650)
        self.rot_interval.setValue(90)
        self.rot_interval.setSuffix(" " + T("Tage"))
        self.rot_last = QLabel()
        row2 = QFormLayout()
        row2.addRow(self.rot_enabled)
        row2.addRow(T("Intervall"), self.rot_interval)
        row2.addRow(T("Zuletzt gelaufen"), self.rot_last)
        rv.addLayout(row2)
        self.rot_enabled.toggled.connect(self.rot_save_state)
        self.rot_interval.valueChanged.connect(self.rot_save_state)

        self.users_log = QTextEdit()
        self.users_log.setReadOnly(True)
        self.users_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        settings = QWidget()
        sl = QVBoxLayout(settings)
        for w in (login_box, accounts_box, rot_box):
            sl.addWidget(w)
        sl.addStretch()

        page = QWidget()
        pl = QVBoxLayout(page)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(settings)
        split.addWidget(self.users_log)
        split.setSizes([520, 200])
        pl.addWidget(split, 1)

        self.rot_timer = QTimer(self)
        self.rot_timer.setInterval(60 * 60 * 1000)      # stündlich prüfen
        self.rot_timer.timeout.connect(self.rot_check)
        self.rot_timer.start()
        self.rot_dry_changed()
        self.rot_load_state()
        self.users_refresh_ips()
        return page

    # ---------- Protokoll ----------
    def users_say(self, text, color=None, bold=False):
        import html
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.users_log.append(f'<span style="{style}">{html.escape(text)}</span>')
        LOG.info(T("Benutzerkonten") + ": " + text, color, bold)

    def users_show_line(self, line, kind=""):
        self.users_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    # ---------- Konten eines Geräts ----------
    def users_refresh_ips(self):
        keep = self.users_ip.currentText()
        self.users_ip.clear()
        self.users_ref_pws = {}
        for ip, pw in self.reachable_rows():
            self.users_ip.addItem(ip)
            self.users_ref_pws[ip] = pw
        if keep:
            self.users_ip.setEditText(keep)

    def users_current_login(self):
        """(ip, user, pw) für das gewählte Gerät, mit Vorgabe-Passwort aus der Serverliste."""
        ip = self.users_ip.currentText().strip()
        pw = self.users_ref_pws.get(ip, "") or self.users_pass.text() or self.cur_pass.text()
        return ip, self.users_user.text().strip(), pw

    def users_load(self):
        ip, user, pw = self.users_current_login()
        if not ip:
            self.users_status.setText(T("Bitte eine Adresse eintragen."))
            return
        if self.users_reader and self.users_reader.isRunning():
            return
        self.users_status.setText(T("Lese Konten …"))
        self.users_reader = UsersListThread(self, ip, user, pw, self.users_vendor.currentData() or "")
        self.users_reader.done.connect(self.users_loaded)
        self.users_reader.start()

    def users_loaded(self, accounts, vendor, err):
        self.users_vendor_cur = vendor
        if accounts is None:
            self.users_table.setRowCount(0)
            self.users_accounts = []
            self.users_status.setText(T("Fehler: {0}").format(err))
            return
        self.users_accounts = accounts
        self.users_table.setRowCount(len(accounts))
        for r, a in enumerate(accounts):
            self.users_table.setItem(r, 0, QTableWidgetItem(a["username"]))
            self.users_table.setItem(r, 1, QTableWidgetItem(a["role"]))
            self.users_table.setItem(r, 2, QTableWidgetItem("✔" if a["enabled"] else "—"))
            self.users_table.setItem(r, 3, QTableWidgetItem("✔" if a["locked"] else "—"))
            self.users_table.setItem(r, 4, QTableWidgetItem(str(a["ssh_keys"])))
        self.users_status.setText(T("{0} Konten gelesen ({1}).").format(len(accounts), vendor))

    def users_selected(self):
        rows = sorted({i.row() for i in self.users_table.selectedIndexes()})
        if len(rows) != 1 or rows[0] >= len(self.users_accounts):
            return None
        return self.users_accounts[rows[0]]

    def users_busy(self, busy):
        for b in self.users_buttons:
            b.setEnabled(not busy)

    def users_run_action(self, label, fn, reload_after=True):
        """Startet eine einzelne Kontenaktion im Hintergrund; fn(ue) macht die eigentliche Arbeit."""
        if self.users_action_thread and self.users_action_thread.isRunning():
            return
        self.users_say(T('— {0} —').format(label), "gray")
        self.users_busy(True)
        self.users_action_thread = UsersActionThread(self, fn)
        self.users_action_thread.line.connect(self.users_show_line)
        self.users_action_thread.result.connect(lambda res: self.users_action_done(res, reload_after))
        self.users_action_thread.start()

    def users_action_done(self, res, reload_after):
        self.users_busy(False)
        if res is False:
            self.users_status.setText(T("Fehler, siehe Protokoll."))
        elif reload_after:
            self.users_load()

    def users_create(self):
        ip, user, pw = self.users_current_login()
        if not ip:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Bitte eine Adresse eintragen."))
            return
        dlg = NewAccountDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_user, new_pass, role = dlg.values()
        if not new_user or not new_pass:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Bitte Benutzername und Passwort eintragen."))
            return
        v = self.users_vendor.currentData() or self.users_vendor_cur
        self.users_run_action(T("Benutzer {0} anlegen").format(new_user),
                              lambda ue: ue.create_account(ip, user or ue.DEFAULT_USER.get(v, user), pw, v,
                                                            new_user, new_pass, role))

    def users_delete(self):
        a = self.users_selected()
        if not a:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Bitte ein Konto in der Tabelle wählen."))
            return
        if not self.confirm(T("Benutzer löschen?"),
                            T("Konto {0} wird jetzt wirklich gelöscht.").format(a["username"])):
            return
        ip, user, pw = self.users_current_login()
        v = self.users_vendor.currentData() or self.users_vendor_cur
        self.users_run_action(T("Benutzer {0} löschen").format(a["username"]),
                              lambda ue: ue.delete_account(ip, user or ue.DEFAULT_USER.get(v, user), pw, v,
                                                            a["path"]))

    def users_change_role(self):
        a = self.users_selected()
        if not a:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Bitte ein Konto in der Tabelle wählen."))
            return
        role, ok = QInputDialog.getItem(self, T("Rolle ändern"), T("Neue Rolle für {0}").format(a["username"]),
                                        list(UsersEngine.ROLES),
                                        max(0, list(UsersEngine.ROLES).index(a["role"]) if a["role"] in
                                            UsersEngine.ROLES else 0), False)
        if not ok:
            return
        ip, user, pw = self.users_current_login()
        v = self.users_vendor.currentData() or self.users_vendor_cur
        self.users_run_action(T("Rolle von {0} ändern").format(a["username"]),
                              lambda ue: ue.set_role(ip, user or ue.DEFAULT_USER.get(v, user), pw, v,
                                                     a["path"], role))

    def users_set_password(self):
        a = self.users_selected()
        if not a:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Bitte ein Konto in der Tabelle wählen."))
            return
        new_pass, ok = QInputDialog.getText(self, T("Passwort setzen"),
                                            T("Neues Passwort für {0}").format(a["username"]),
                                            QLineEdit.EchoMode.Password)
        if not ok or not new_pass:
            return
        ip, user, pw = self.users_current_login()
        v = self.users_vendor.currentData() or self.users_vendor_cur
        account = a["username"] if v == "hpe_old" else a["path"]
        self.users_run_action(T("Passwort von {0} setzen").format(a["username"]),
                              lambda ue: ue.rotate_password(ip, user or ue.DEFAULT_USER.get(v, user), pw, v,
                                                            account, new_pass, login_name=a["username"]))

    def users_add_ssh_key(self):
        a = self.users_selected()
        if not a:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Bitte ein Konto in der Tabelle wählen."))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(T("SSH-Key setzen"))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(T("Öffentlicher SSH-Key für {0}").format(a["username"])))
        edit = QPlainTextEdit()
        edit.setPlaceholderText("ssh-ed25519 AAAA... kommentar")
        lay.addWidget(edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        pubkey = edit.toPlainText().strip()
        if not pubkey:
            return
        ip, user, pw = self.users_current_login()
        v = self.users_vendor.currentData() or self.users_vendor_cur
        self.users_run_action(T("SSH-Key für {0} setzen").format(a["username"]),
                              lambda ue: ue.add_ssh_key(ip, user or ue.DEFAULT_USER.get(v, user), pw,
                                                        a["keys_path"], pubkey))

    def users_delete_ssh_keys(self):
        a = self.users_selected()
        if not a or not a["ssh_keys"]:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Dieses Konto hat keine gespeicherten SSH-Keys."))
            return
        if not self.confirm(T("SSH-Keys entfernen?"),
                            T("Alle SSH-Keys von {0} werden jetzt wirklich entfernt.").format(a["username"])):
            return
        ip, user, pw = self.users_current_login()
        v = self.users_vendor.currentData() or self.users_vendor_cur

        def do(ue):
            keys = ue.list_ssh_keys(ip, user or ue.DEFAULT_USER.get(v, user), pw, a["keys_path"])
            ok = True
            for k in keys:
                if ue.delete_ssh_key(ip, user or ue.DEFAULT_USER.get(v, user), pw, k["path"]) is False:
                    ok = False
            return ok
        self.users_run_action(T("SSH-Keys von {0} entfernen").format(a["username"]), do)

    # ---------- Passwort-Rotation ----------
    def rot_dry_changed(self, *_):
        dry = self.rot_dry.isChecked()
        self.rot_now.setText(T("Trockenlauf für alle Server der Liste") if dry
                             else T("Scharf: Passwörter jetzt rotieren"))
        self.rot_now.setStyleSheet("" if dry else "background:#c9302c; color:white; font-weight:bold;")

    def rot_run_now(self):
        rows = self.batch_rows(self.users_pass.text())
        if not rows:
            QMessageBox.warning(self, T("Benutzerkonten"), T("Die Serverliste („Neuware“) ist leer."))
            return
        dry = self.rot_dry.isChecked()
        if not dry:
            info = T("Auf allen Servern der Liste wird jetzt wirklich ein neues, zufälliges Passwort "
                     "gesetzt. Die neuen Passwörter werden in der Serverliste nachgetragen und "
                     "zusätzlich gesichert.") + self.selfguard_note()
            if not self.confirm(T("Passwörter wirklich rotieren?"),
                                T("Auf {0} Servern wird das Passwort geändert.").format(len(rows)),
                                info, len(rows)):
                return
        self.rot_start(rows, dry)

    def rot_start(self, rows, dry):
        if self.users_worker and self.users_worker.isRunning():
            return
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.users_say(T('— Passwort-Rotation, {0}, {1} Server —').format(mode, len(rows)), "gray")
        self.users_worker = UsersEngineThread(self, rows=rows, cur_user=self.users_user.text().strip(), dry=dry)
        self.users_worker.line.connect(self.users_show_line)
        self.users_worker.rotated.connect(self.rot_done)
        self.users_worker.start()

    def rot_done(self, new_pw):
        """Neue Passwörter in die Serverliste. Gespeichert wird nur auf Wunsch: in den Schlüsselbund, wenn
        dessen Haken gesetzt ist, sonst fragt das Tool (voreingestellt: nicht speichern)."""
        if not new_pw:
            return
        in_vault, leftover = {}, {}
        use_vault = getattr(self, "vault_use", None) is not None and self.vault_use.isChecked() and vault.available()
        for ip, pw2 in new_pw.items():
            serial = ""
            for r in range(self.table.rowCount()):
                if self.cell(r, 0) == ip or self.cell(r, 2) == ip:
                    self.table.item(r, 1).setText(pw2)
                    # fertige Zeile: rotiert wurde der neue Administrator, nicht das (gesperrte) Werkskonto
                    self.table.item(r, 1).setData(Qt.ItemDataRole.UserRole, pw2 if self.is_done(r) else None)
                    serial = self.cell(r, 6)
            if use_vault and vault.store(serial, ip, pw2):
                in_vault[ip] = pw2
            else:
                leftover[ip] = pw2
        parts = [T("  Neue Passwörter stehen jetzt in der Serverliste (Spalte „Passwort“)")]
        if in_vault:
            parts.append(T("und im Schlüsselbund.") if not leftover
                         else T('und für {0} davon zusätzlich im Schlüsselbund.').format(len(in_vault)))
        if leftover and self.rot_ask_save(len(leftover)):
            path = self.rot_save_results(leftover)
            parts.append(T('und zusätzlich gesichert unter {0}.').format(path))
        parts.append(T("Bitte die Serverliste jetzt speichern."))
        self.users_say(" ".join(parts), "ok")
        self.rot_enabled_timestamp()

    def rot_ask_save(self, n):
        """Fragt, ob neu rotierte Passwörter als Datei gesichert werden sollen. Voreingestellt: nein."""
        box = QMessageBox(QMessageBox.Icon.Warning, T("Neue Passwörter sichern?"),
                          T("{0} neue Passwörter stehen nur in der Serverliste.").format(n), parent=self)
        box.setInformativeText(T("Ohne Sicherung sind sie weg, wenn die Liste nicht gespeichert wird. Eine Datei "
                                 "enthält sie im Klartext (nur für diesen Benutzer lesbar). Besser: Haken "
                                 "„Passwörter im Schlüsselbund speichern“ in „Neuware“ setzen."))
        b_file = box.addButton(T("Als Datei sichern"), QMessageBox.ButtonRole.DestructiveRole)
        b_no = box.addButton(T("Nicht speichern"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_no)
        box.exec()
        return box.clickedButton() is b_file

    @staticmethod
    def rot_save_results(new_pw):
        """Sichert neu rotierte Passwörter in einer eigenen, nur für den eigenen Benutzer lesbaren Datei
        (0600) - bis die Schlüsselbund-Integration das übernimmt. Gibt den Dateipfad zurück."""
        path = i18n.DATA / "passwort-rotation" / (time.strftime("%Y%m%d-%H%M%S") + ".json")
        i18n.write_private(path, json.dumps(new_pw, indent=1, ensure_ascii=False))
        return path

    # ---------- geplante Rotation ----------
    def rot_load_state(self):
        try:
            data = json.loads(i18n.ROTATION_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            data = {}
        self.rot_enabled.blockSignals(True)
        self.rot_interval.blockSignals(True)
        self.rot_enabled.setChecked(bool(data.get("enabled", False)))
        self.rot_interval.setValue(int(data.get("interval_days") or 90))
        self.rot_enabled.blockSignals(False)
        self.rot_interval.blockSignals(False)
        self._rot_last_run = str(data.get("last_run") or "")
        self.rot_last.setText(self._rot_last_run or T("noch nie"))

    def rot_save_state(self, *_):
        data = {"enabled": self.rot_enabled.isChecked(), "interval_days": self.rot_interval.value(),
                "last_run": getattr(self, "_rot_last_run", "")}
        try:
            i18n.write_private(i18n.ROTATION_STATE, json.dumps(data, indent=1, ensure_ascii=False))
        except OSError:
            pass

    def rot_enabled_timestamp(self):
        self._rot_last_run = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.rot_last.setText(self._rot_last_run)
        self.rot_save_state()

    def rot_check(self):
        """Stündlich: löst die geplante Rotation aus, wenn aktiv und das Intervall abgelaufen ist. Läuft
        scharf und ohne Rückfrage - die geplante Rotation ist eine bewusste Entscheidung in der Oberfläche,
        kein Trockenlauf-Vorschlag."""
        if not self.rot_enabled.isChecked():
            return
        if self.users_worker and self.users_worker.isRunning():
            return
        due = True
        if self._rot_last_run:
            try:
                last = time.mktime(time.strptime(self._rot_last_run, "%Y-%m-%dT%H:%M:%S"))
                due = (time.time() - last) >= self.rot_interval.value() * 86400
            except ValueError:
                due = True
        if not due:
            return
        rows = self.batch_rows(self.users_pass.text())
        if not rows:
            return
        self.users_say(T("— Geplante Rotation wird ausgelöst (Intervall {0} Tage) —")
                       .format(self.rot_interval.value()), "gray")
        self.rot_start(rows, dry=False)
