"""BMC-Steward: Reiter „Bestandsserver" - Verbindung zu schon konfigurierten BMCs nur lesend prüfen und
erfolgreich getestete Server in „Neuware“, „UEFI-Einstellungen“ und „Dienste“ übernehmen."""
import html
import ipaddress

from PyQt6.QtWidgets import (QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QSplitter, QStyledItemDelegate,
                             QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtGui import QBrush, QColor, QFontDatabase
from PyQt6.QtCore import Qt

from . import i18n
from .applog import LOG
from .core import ipv4
from .i18n import T
from .redfish import ProbeEngine, ProbeThread


class PwDelegate(QStyledItemDelegate):
    """Zeigt Passwörter in der Tabelle als Punkte; beim Bearbeiten bleibt die Eingabe verdeckt."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = "•" * len(option.text)

    def createEditor(self, parent, option, index):
        e = super().createEditor(parent, option, index)
        if isinstance(e, QLineEdit):
            e.setEchoMode(QLineEdit.EchoMode.Password)
        return e


class ExistingTabMixin:
    """Reiter „Bestandsserver" des Hauptfensters."""

    def build_existing(self):
        self.ex_worker = None
        self.ex_ok = {}          # (Adresse, Port, Benutzer, Passwort) -> Ergebnis; nur für unverändert getestete Zeilen
        self.ex_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.ex_vendor.addItem(text, key)
        self.ex_vendor.setToolTip(T("„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller.\n"
                                    "Eine feste Wahl überspringt die Erkennung und gilt für alle Zeilen."))
        self.ex_user_all = QLineEdit()
        self.ex_user_all.setPlaceholderText(T("automatisch je Hersteller"))
        self.ex_pass_all = self._pw()
        self.ex_from = QLineEdit()
        self.ex_from.setPlaceholderText(T("z. B. 10.20.0.11"))
        self.ex_to = QLineEdit()
        self.ex_to.setPlaceholderText(T("z. B. 10.20.0.30"))

        # Zugangsdaten und Adressbereich kompakt in zwei Zeilen (mehr Platz für die Serverliste)
        box_all = QGroupBox(T("Zugangsdaten und Adressen schnell eintragen"))
        g = QGridLayout(box_all)
        g.setContentsMargins(8, 2, 8, 4)
        g.setVerticalSpacing(3)
        user_tip = T("Benutzer, mit dem sich das Tool am BMC anmeldet. Leer lassen: Werksbenutzer je Hersteller.")
        pass_tip = T("Gilt für alle Zeilen, wenn Sie unten auf „Für alle Zeilen übernehmen“ klicken.\n"
                     "Abweichende Zugangsdaten tragen Sie direkt in der Tabelle je Zeile ein.")
        self.ex_user_all.setToolTip(user_tip)
        self.ex_vendor.setMinimumWidth(150)
        pw_all = self._with_eye(self.ex_pass_all)
        pw_all.setToolTip(pass_tip)
        apply = QPushButton(T("Für alle Zeilen übernehmen"))
        apply.setToolTip(T("Trägt Benutzer und Passwort von oben in alle Zeilen ein und überschreibt vorhandene Werte."))
        apply.clicked.connect(self.ex_apply_all)
        for col, (label, w, tip) in enumerate(((T("Hersteller"), self.ex_vendor, self.ex_vendor.toolTip()),
                                               (T("Benutzer"), self.ex_user_all, user_tip),
                                               (T("Passwort"), pw_all, pass_tip))):
            lab = QLabel(label)
            lab.setToolTip(tip)
            g.addWidget(lab, 0, col * 2)
            g.addWidget(w, 0, col * 2 + 1)
        g.addWidget(apply, 0, 6)
        rng = QHBoxLayout()
        rng.addWidget(self.ex_from)
        rng.addWidget(QLabel("–"))
        rng.addWidget(self.ex_to)
        add_rng = QPushButton(T("Bereich einfügen"))
        add_rng.setToolTip(T("Fügt für jede Adresse von … bis … eine Zeile ein (höchstens 256 auf einmal)."))
        add_rng.clicked.connect(self.ex_add_range)
        g.addWidget(QLabel(T("Adressbereich")), 1, 0)
        g.addLayout(rng, 1, 1, 1, 5)
        g.addWidget(add_rng, 1, 6)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        g.setColumnStretch(5, 1)

        self.ex_table = QTableWidget(0, 5)
        self.ex_table.setHorizontalHeaderLabels([T("Adresse (BMC)"), T("Port"), T("Benutzer"), T("Passwort"), T("Status")])
        self.ex_table.setItemDelegateForColumn(3, PwDelegate(self.ex_table))
        self.ex_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ex_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        for c, tip in enumerate((T("IPv4-Adresse des BMC (iDRAC, iLO, Supermicro-IPMI, XCC)."),
                                 T("HTTPS-Port des BMC, normalerweise 443."),
                                 T("Benutzer dieses Geräts. Leer: Werksbenutzer je Hersteller."),
                                 T("Passwort dieses Geräts."),
                                 T("Ergebnis des Verbindungstests."))):
            self.ex_table.horizontalHeaderItem(c).setToolTip(tip)
        add = QPushButton(T("+ Server"))
        add.clicked.connect(lambda: self.ex_add_row())
        rem = QPushButton(T("– Entfernen"))
        rem.clicked.connect(self.ex_remove_rows)
        pull = QPushButton(T("Aus Serverliste holen"))
        pull.setToolTip(T("Übernimmt Adressen und Passwörter aus „Neuware“ (Aktuelle IP, Passwort)."))
        pull.clicked.connect(self.ex_pull)
        self.ex_audit_btn = QPushButton(T("Systemaudit …"))
        self.ex_audit_btn.setToolTip(T("Liest den markierten Server (sonst den ersten) für das Audit-Blatt aus und "
                                       "öffnet „Systemaudit“. Nur lesend."))
        self.ex_audit_btn.clicked.connect(self.ex_audit)
        btns = QHBoxLayout()
        for b in (add, rem, pull):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(self.ex_audit_btn)
        box_list = QGroupBox(T("Bestandsserver"))
        v = QVBoxLayout(box_list)
        v.addWidget(self.ex_table)
        v.addLayout(btns)

        self.ex_test = QPushButton(T("Verbindung testen"))
        self.ex_test.setMinimumHeight(38)
        self.ex_test.setDefault(True)
        self.ex_test.setToolTip(T("Prüft je Server nur lesend: Port, Verschlüsselung, Hersteller, Anmeldung, Modell,\n"
                                  "Firmware und Administratorrechte. Es wird nichts geändert. Jeder Server bekommt\n"
                                  "genau einen Anmeldeversuch, damit ein falsches Passwort kein Konto sperrt."))
        self.ex_test.clicked.connect(self.ex_run)
        self.ex_stop = QPushButton(T("Abbrechen"))
        self.ex_stop.setEnabled(False)
        self.ex_stop.clicked.connect(self.ex_abort)
        self.ex_take = QPushButton(T("Übernehmen in Neuware, UEFI, Dienste"))
        self.ex_take.setToolTip(T("Übernimmt alle erfolgreich getesteten Server in die Serverliste („Neuware“) und trägt\n"
                                  "den Benutzer in „Neuware“, „UEFI-Einstellungen“ und „Dienste“ ein. Danach dort die Einstellungen wählen und starten."))
        self.ex_take.clicked.connect(self.ex_transfer)
        self.ex_status = QLabel(T("Bereit"))
        act = QHBoxLayout()
        act.addWidget(self.ex_status, 1)
        act.addWidget(self.ex_take)
        act.addWidget(self.ex_stop)
        act.addWidget(self.ex_test)

        self.ex_log = QTextEdit()
        self.ex_log.setReadOnly(True)
        self.ex_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        info = QLabel(T("Für Server, die schon konfiguriert sind: erst die Verbindung prüfen, dann die erfolgreichen "
                        "Server in „Neuware“, „UEFI-Einstellungen“ und „Dienste“ übernehmen. Das Tool speichert nichts."))
        info.setWordWrap(True)
        box_all.setToolTip(info.text())              # Erklärung als Tooltip statt als eigene Textzeilen
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(4, 4, 4, 4)
        pl.setSpacing(4)
        pl.addWidget(box_all)
        split = QSplitter(Qt.Orientation.Vertical)
        upper = QWidget()
        u = QVBoxLayout(upper)
        u.setContentsMargins(0, 0, 0, 0)
        u.addWidget(box_list)
        u.addLayout(act)
        split.addWidget(upper)
        split.addWidget(self.ex_log)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([620, 130])
        split.setChildrenCollapsible(False)
        pl.addWidget(split, 1)
        self.ex_table.itemChanged.connect(self.ex_update_lamp)
        self.ex_add_row()
        return page

    def ex_add_row(self, values=None):
        r = self.ex_table.rowCount()
        values = values or ["", "443", self.ex_user_all.text().strip(), self.ex_pass_all.text()]
        self.ex_table.insertRow(r)
        for c, val in enumerate(values):
            self.ex_table.setItem(r, c, QTableWidgetItem(val))
        st = QTableWidgetItem("")
        st.setFlags(st.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.ex_table.setItem(r, 4, st)
        self.ex_table.setCurrentCell(r, 0)
        self.ex_update_lamp()

    def ex_remove_rows(self):
        for r in sorted({i.row() for i in self.ex_table.selectedIndexes()}, reverse=True):
            self.ex_table.removeRow(r)
        self.ex_update_lamp()

    def ex_update_lamp(self, *_):
        """Leuchte am Menüpunkt „Bestandsserver“: rot, solange keine Zeile erfolgreich getestet ist, grün, wenn alle
        es sind, gelb während des Tests oder wenn danach neue Server dazugekommen sind."""
        if self.__dict__.get("ex_tab") is None:
            return                                      # Seite ist noch im Aufbau (oder Testattrappe)
        total = ok = 0
        for r in range(self.ex_table.rowCount()):
            if not self.ex_cell(r, 0) or self.ex_table.item(r, 3) is None:
                continue
            total += 1
            ok += self.ex_key(r) in self.ex_ok
        from .sidenav import list_lamp
        self.tabs.setLamp(self.ex_tab, *list_lamp(total, ok, self.__dict__.get("ex_lamp_running", False)))

    def ex_audit(self):
        """Markierte (sonst erste ausgefüllte) Zeile an „Systemaudit“."""
        r = self.ex_table.currentRow()
        if r < 0 or not self.ex_cell(r, 0):
            r = next((i for i in range(self.ex_table.rowCount()) if self.ex_cell(i, 0)), -1)
        if r < 0:
            return self.open_audit("", "", "")
        port = self.ex_cell(r, 1) or "443"
        ip = self.ex_cell(r, 0) if port == "443" else f"{self.ex_cell(r, 0)}:{port}"
        pw = self.ex_table.item(r, 3).text() if self.ex_table.item(r, 3) else ""
        self.open_audit(ip, self.ex_cell(r, 2), pw)

    def ex_cell(self, r, c):
        item = self.ex_table.item(r, c)
        return item.text().strip() if item else ""

    def ex_apply_all(self):
        for r in range(self.ex_table.rowCount()):
            self.ex_table.item(r, 2).setText(self.ex_user_all.text().strip())
            self.ex_table.item(r, 3).setText(self.ex_pass_all.text())

    def ex_drop_empty(self):
        for r in range(self.ex_table.rowCount() - 1, -1, -1):
            if not any(self.ex_cell(r, c) for c in (0, 2, 3)):
                self.ex_table.removeRow(r)

    def ex_add_range(self):
        a, b = ipv4(self.ex_from.text().strip()), ipv4(self.ex_to.text().strip())
        if not a or not b or int(b) < int(a):
            QMessageBox.warning(self, T("Adressbereich"), T("Bitte eine gültige Start- und Endadresse (Start vor Ende) eintragen."))
            return
        if int(b) - int(a) >= 256:
            QMessageBox.warning(self, T("Adressbereich"), T("Höchstens 256 Adressen auf einmal."))
            return
        self.ex_drop_empty()
        have = {self.ex_cell(r, 0) for r in range(self.ex_table.rowCount())}
        for n in range(int(a), int(b) + 1):
            ip = str(ipaddress.IPv4Address(n))
            if ip not in have:
                self.ex_add_row(["" + ip, "443", self.ex_user_all.text().strip(), self.ex_pass_all.text()])

    def ex_pull(self):
        self.ex_drop_empty()
        have = {self.ex_cell(r, 0) for r in range(self.ex_table.rowCount())}
        for r in range(self.table.rowCount()):
            ip, pw = self.cell(r, 0), self.cell(r, 1) or self.cur_pass.text()
            if ip and ip not in have:
                self.ex_add_row([ip, "443", self.cur_user.text().strip(), pw])
                have.add(ip)

    def ex_say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "") + ("font-weight:bold;" if bold else "")
        self.ex_log.append(f'<span style="{style}">{html.escape(text)}</span>')
        LOG.info(T("Bestandsserver") + ": " + text, color, bold)

    def ex_show_line(self, line, kind=""):
        self.ex_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def ex_collect(self):
        """(Zeile, Adresse[:Port], Benutzer, Passwort) aller ausgefüllten Zeilen und die Fehlermeldungen."""
        rows, errs, seen = [], [], set()
        for r in range(self.ex_table.rowCount()):
            ip, port, user, pw = (self.ex_cell(r, c) for c in range(4))
            if not (ip or user or pw):
                continue
            n, port = r + 1, port or "443"
            if not ipv4(ip):
                errs.append(T('Zeile {0}: keine gültige IPv4-Adresse').format(n))
            elif not (port.isdigit() and 1 <= int(port) <= 65535):
                errs.append(T('Zeile {0}: ungültiger Port').format(n))
            elif not self.ex_table.item(r, 3).text():
                errs.append(T('Zeile {0}: Passwort fehlt').format(n))
            elif (ip, port) in seen:
                errs.append(T('Zeile {0}: Adresse und Port kommen mehrfach vor').format(n))
            else:
                seen.add((ip, port))
                rows.append((r, ip if port == "443" else f"{ip}:{port}", user,
                             self.ex_table.item(r, 3).text()))
        return rows, errs

    def ex_key(self, r):
        return (self.ex_cell(r, 0), self.ex_cell(r, 1) or "443", self.ex_cell(r, 2), self.ex_table.item(r, 3).text())

    def ex_run(self):
        if self.ex_worker and self.ex_worker.isRunning():
            return
        rows, errs = self.ex_collect()
        if errs or not rows:
            QMessageBox.warning(self, T("Bestandsserver"), "\n".join(errs) or T("Die Liste ist leer."))
            return
        self.ex_log.clear()
        self.ex_ok = {}
        self.ex_rowof = {ip: r for r, ip, _, _ in rows}
        for r, *_ in rows:
            self.ex_table.item(r, 4).setText(T("wartet …"))
            self.ex_table.item(r, 4).setBackground(QBrush())
        self.ex_say(T('— Verbindungstest, {0} Server, nur lesend —').format(len(rows)), "gray")
        self.ex_worker = ProbeThread(self, rows=[(ip, u, pw) for _, ip, u, pw in rows],
                                     vendor=self.ex_vendor.currentData() or None)
        self.ex_worker.line.connect(self.ex_show_line)
        self.ex_worker.row.connect(self.ex_row_result)
        self.ex_worker.result.connect(self.ex_done)
        self.ex_test.setEnabled(False)
        self.ex_stop.setEnabled(True)
        self.ex_table.setEnabled(False)
        self.ex_status.setText(T("Läuft …"))
        self.ex_lamp_running = True
        self.ex_update_lamp()
        self.ex_worker.start()

    def ex_row_result(self, ip, res):
        r = self.ex_rowof.get(ip)
        item = self.ex_table.item(r, 4) if r is not None else None
        if item is None:
            return
        if res["ok"]:
            self.ex_ok[self.ex_key(r)] = res
            admin = {True: "", False: " · " + T("kein Admin"), None: ""}[res.get("admin")]
            item.setText(T("OK") + " · " + (res.get("model") or self.NAME_OF(res)) + (f" {res['fw']}" if res.get("fw") else "") + admin)
            item.setBackground(QBrush(QColor("#f0ad4e" if res.get("admin") is False else "#3fa45b")))
        else:
            item.setText(T("Fehler") + ": " + res.get("text", ""))
            item.setBackground(QBrush(QColor("#d9534f")))
        self.ex_update_lamp()

    @staticmethod
    def NAME_OF(res):
        return ProbeEngine.NAME.get(res.get("vendor", ""), "")

    def ex_abort(self):
        if self.ex_worker and self.ex_worker.isRunning():
            self.ex_worker.requestInterruption()
            self.ex_stop.setEnabled(False)
            self.ex_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def ex_done(self, code, *_):
        self.ex_lamp_running = False
        self.ex_update_lamp()
        self.ex_test.setEnabled(True)
        self.ex_stop.setEnabled(False)
        self.ex_table.setEnabled(True)
        self.ex_status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))

    def ex_transfer(self):
        good = [(r, self.ex_ok[self.ex_key(r)]) for r in range(self.ex_table.rowCount())
                if self.ex_cell(r, 0) and self.ex_key(r) in self.ex_ok]
        if not good:
            QMessageBox.information(self, T("Bestandsserver"), T("Noch kein erfolgreich getesteter Server. Erst „Verbindung testen“."))
            return
        users = {}
        for r, res in good:
            users.setdefault(res["user"], []).append(r)
        user = max(users, key=lambda k: len(users[k]))
        take = [r for r in users[user] if self.ex_cell(r, 1) in ("", "443")]
        left = len(good) - len(take)
        self.ex_drop_main()
        have = {self.cell(r, 0) for r in range(self.table.rowCount())}
        for r in take:
            ip = self.ex_cell(r, 0)
            if ip not in have:
                self.add_row([ip, self.ex_table.item(r, 3).text(), "", self.def_mask.text().strip(),
                              self.def_gw.text().strip(), ""])
        for w in (self.cur_user, self.bios_user, self.svc_user, self.fw_user, self.users_user):
            w.setText(user)
        self.update_count()
        self.touch()
        msg = T('{0} Server in die Serverliste („Neuware“) übernommen, Benutzer „{1}“ eingetragen.').format(len(take), user)
        if left:
            msg += "\n\n" + T('{0} getestete Server blieben draußen: anderer Benutzer als „{1}“ oder Port ungleich 443. '
                              '„Neuware“, „UEFI-Einstellungen“ und „Dienste“ kennen nur einen Benutzer für alle Server; diese Server in einem zweiten Durchgang übernehmen.').format(left, user)
        QMessageBox.information(self, T("Bestandsserver"), msg)
        self.tabs.setCurrentIndex(1)

    def ex_drop_main(self):
        """Leere Zeilen der Serverliste („Neuware“) entfernen, damit die übernommenen Server oben stehen."""
        for r in range(self.table.rowCount() - 1, -1, -1):
            if not any(self.cell(r, c) for c in range(len(i18n.COLS))):
                self.table.removeRow(r)
