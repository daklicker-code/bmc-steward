"""BMC-Steward: Reiter „UEFI-Einstellungen" - UEFI/BIOS-Attribute eines Referenzgeräts lesen und auf
allen Servern der Serverliste ("Neuware") setzen, mit eigenen Vorlagen getrennt von den Profilen dort."""
import html
import json

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                             QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtGui import QBrush, QColor, QFontDatabase
from PyQt6.QtCore import Qt

from . import i18n
from .applog import LOG
from .i18n import BIOS_PROFILES, T
from .redfish import BiosEngine, BiosEngineThread, BiosReadThread, Engine


class BiosTabMixin:
    """Reiter „UEFI-Einstellungen" des Hauptfensters."""

    def build_bios(self):
        self.bios_live = {}        # zuletzt gelesene Attribute eines Referenzgeräts: Name -> aktueller Wert
        self.bios_pending = {}     # aus einer geladenen Vorlage, noch nicht in der Tabelle angezeigt
        self.bios_reader = None

        self.bios_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.bios_vendor.addItem(text, key)
        self.bios_user = QLineEdit()
        self.bios_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.bios_pass = self._pw()
        ref_refresh = QPushButton("↻")
        ref_refresh.setFixedWidth(36)
        ref_refresh.setToolTip(T("Adressen aus der Serverliste („Neuware“) neu einlesen"))
        ref_refresh.clicked.connect(self.bios_refresh_ips)
        self.bios_ref = QComboBox()
        self.bios_ref.setEditable(True)
        self.bios_pws = {}                 # Adresse -> Passwort der Zeile aus „Neuware“ (nur zum Vorbefüllen)
        self.bios_user_auto = ""
        self.bios_ref.setToolTip(T("Adresse des Geräts, von dem die Attribute gelesen werden.\n"
                                 "Vorschläge kommen aus der Serverliste in „Neuware“, es geht aber jede\n"
                                 "erreichbare Adresse."))
        refrow = QHBoxLayout()
        refrow.addWidget(self.bios_ref, 1)
        refrow.addWidget(ref_refresh)
        self.bios_read_btn = QPushButton(T("Attribute lesen"))
        self.bios_read_btn.setToolTip(T("Liest die tatsächlich vorhandenen UEFI-Attribute von diesem einen\n"
                                      "Gerät. Nur Lesen, es wird nichts verändert."))
        self.bios_read_btn.clicked.connect(self.bios_read)
        self.bios_read_status = QLabel()

        box_ref = QGroupBox(T("Referenzgerät (nur zum Lesen)"))
        box_ref.setToolTip(T("Da sich UEFI-Attribute je Modell unterscheiden, zeigt das Tool nur die\n"
                           "tatsächlich vorhandenen eines echten Geräts an, statt sie zu erraten."))
        f = QFormLayout(box_ref)
        self.tip_row(f, T("Hersteller"), self.bios_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den\n"
                     "Hersteller dieses einen Geräts selbst."))
        self.tip_row(f, T("Benutzer"), self.bios_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet\n"
                     "(root/Administrator/ADMIN/USERID)."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.bios_pass),
                     T("Gilt zum Lesen des Referenzgeräts und, wenn die Vorlage später angewendet wird,\n"
                     "für alle Zeilen der Serverliste ohne eigenes Passwort."))
        f.addRow(T("Adresse"), refrow)
        act = QHBoxLayout()
        act.addWidget(self.bios_read_status)
        act.addStretch()
        act.addWidget(self.bios_read_btn)
        f.addRow(act)

        self.bios_filter = QLineEdit()
        self.bios_filter.setPlaceholderText(T("Attribut suchen …"))
        self.bios_filter.setToolTip(T("Blendet Zeilen aus, deren Name den Suchtext nicht enthält."))
        self.bios_filter.textChanged.connect(self.bios_apply_filter)
        self.bios_table = QTableWidget(0, 3)
        self.bios_table.setHorizontalHeaderLabels([T("Attribut"), T("Aktueller Wert"), T("Neuer Wert")])
        self.bios_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bios_table.setToolTip(T("Nur die Spalte „Neuer Wert“ ist bearbeitbar. Leer lassen, um ein\n"
                                   "Attribut unverändert zu lassen. Graue Zeilen (Passwörter, TPM,\n"
                                   "Löschfunktionen) sind gesperrt."))
        box_table = QGroupBox(T("UEFI/BIOS-Attribute"))
        vt = QVBoxLayout(box_table)
        vt.addWidget(self.bios_filter)
        vt.addWidget(self.bios_table)

        self.bios_dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.bios_dry.setChecked(True)
        self.bios_dry.setToolTip(T("Mit Haken wird nichts gesendet, das Protokoll zeigt nur den Ablauf.\n"
                                 "Ohne Haken werden die ausgefüllten „Neuer Wert“-Felder auf jedem Server\n"
                                 "der Liste („Neuware“) gesetzt, der das jeweilige Attribut tatsächlich hat."))
        self.bios_reboot = QCheckBox(T("Danach hart neu starten"))
        self.bios_reboot.setChecked(False)
        self.bios_reboot.setToolTip(T("UEFI-Einstellungen werden erst nach einem Neustart aktiv. Ohne Haken\n"
                                    "werden sie nur vorgemerkt, es wird kein Neustart ausgelöst.\n"
                                    "Mit Haken wird ein laufender Server hart neu gestartet (wie mit der\n"
                                    "Reset-Taste), ein ausgeschalteter wird eingeschaltet. Nur für Server\n"
                                    "ohne laufendes Betriebssystem verwenden."))
        self.bios_start = QPushButton(T("Auf allen Servern der Liste setzen"))
        self.bios_start.setMinimumHeight(34)
        self.bios_start.setToolTip(T("Wendet jedes ausgefüllte „Neuer Wert“-Feld auf jede Zeile der\n"
                                   "Serverliste („Neuware“) an, der Reihe nach, mit Login wie oben."))
        self.bios_start.clicked.connect(self.bios_run)
        self.bios_stop = QPushButton(T("Abbrechen"))
        self.bios_stop.setToolTip(T("Bricht nach dem Server ab, der gerade bearbeitet wird"))
        self.bios_stop.setEnabled(False)
        self.bios_stop.clicked.connect(self.bios_abort)
        actions = QHBoxLayout()
        actions.addWidget(self.bios_dry)
        actions.addWidget(self.bios_reboot)
        actions.addStretch()
        actions.addWidget(self.bios_stop)
        actions.addWidget(self.bios_start)

        self.bios_log = QTextEdit()
        self.bios_log.setReadOnly(True)
        self.bios_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.bios_status = QLabel(T("Bereit"))

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addLayout(self.bios_build_profiles())
        lay.addWidget(box_ref)
        lay.addWidget(box_table, 1)
        lay.addLayout(actions)
        lay.addWidget(self.bios_log)
        lay.addWidget(self.bios_status)
        self.bios_ref.activated.connect(self.bios_pick_ip)
        self.bios_ref.currentTextChanged.connect(self.bios_pick_ip)     # Zugangsdaten folgen der Adresse automatisch
        for w in (self.new_user, self.new_pass):                         # neuer Administrator oben geändert: Felder nachziehen
            w.textChanged.connect(lambda *_: self.bios_pick_ip(only_done=True))
        self.bios_refresh_ips()
        self.bios_read_profiles()
        return page

    def bios_refresh_ips(self):
        keep = self.bios_ref.currentText()
        self.bios_ref.blockSignals(True)
        self.bios_ref.clear()
        self.bios_pws = {}
        for ip, pw in self.reachable_rows():
            self.bios_pws[ip] = pw
            self.bios_ref.addItem(ip)
        idx = self.bios_ref.findText(keep) if keep else -1
        if idx >= 0:
            self.bios_ref.setCurrentIndex(idx)
        elif keep:
            self.bios_ref.setEditText(keep)
        elif self.bios_pws:                                # ohne bisherige Eingabe: erste fertig umgestellte, sonst erste Zeile
            first = next((ip for ip in self.bios_pws if self.row_login(ip)[0]), next(iter(self.bios_pws)))
            self.bios_ref.setCurrentIndex(self.bios_ref.findText(first))
        self.bios_ref.blockSignals(False)
        self.bios_pick_ip()

    def bios_pick_ip(self, *_, only_done=False):
        """Übernimmt Benutzer und Passwort der Zeile aus „Neuware“ (nur in die Felder, gespeichert wird nichts),
        wie auf der Seite „UEFI-Konsole“. Fertig umgestellte Zeilen kennen nur noch den neuen Benutzer mit dem
        neuen Passwort. Von Hand getippte Adressen und laufendes Lesen bleiben unangetastet."""
        ip = self.bios_ref.currentText().strip()
        if ip not in self.bios_pws or not self.bios_read_btn.isEnabled():
            return
        user, pw = self.row_login(ip)
        if only_done and not user:
            return
        if not user:
            pw = pw or self.cur_pass.text()             # Werkskonto: sonst das Werks-Passwort von oben
        if user:
            self.bios_user.setText(user)
            self.bios_user_auto = user
            if not pw:
                self.bios_pass.clear()                  # Passwort nach scharfem Lauf geleert: neu eintippen, kein altes
        elif self.bios_user_auto and self.bios_user.text().strip() == self.bios_user_auto:
            self.bios_user.clear()                         # frühere automatische Vorgabe nicht auf andere Geräte mitnehmen
            self.bios_user_auto = ""
        if pw:
            self.bios_pass.setText(pw)

    def bios_read(self):
        ip = self.bios_ref.currentText().strip()
        if not ip:
            QMessageBox.warning(self, T("Attribute lesen"),
                                T("Bitte eine Adresse eintragen oder aus der Serverliste wählen."))
            return
        self.bios_read_btn.setEnabled(False)
        self.bios_read_status.setText(T("Lese …"))
        self.bios_reader = BiosReadThread(self, ip, self.bios_user.text().strip(), self.bios_pass.text(),
                                          self.bios_vendor.currentData() or None)
        self.bios_reader.done.connect(self.bios_read_done)
        self.bios_reader.start()

    def bios_read_done(self, attrs, vendor, err):
        self.bios_read_btn.setEnabled(True)
        if attrs is None:
            self.bios_read_status.setText("")
            QMessageBox.warning(self, T("Attribute lesen"), err or T("Attribute konnten nicht gelesen werden."))
            return
        self.bios_live = {str(k): v for k, v in attrs.items()}
        self.bios_fill_table()
        self.bios_read_status.setText(T('{0} Attribute ({1})').format(len(self.bios_live), Engine.NAME.get(vendor, vendor)))

    def bios_fill_table(self, keep_values=True):
        old_new = {}
        if keep_values:
            for r in range(self.bios_table.rowCount()):
                name, val = self.bios_table.item(r, 0), self.bios_table.item(r, 2)
                if name and val and val.text().strip():
                    old_new[name.text()] = val.text().strip()
        self.bios_table.setRowCount(0)
        for name in sorted(self.bios_live, key=str.lower):
            cur = self.bios_live[name]
            locked = BiosEngine.blocked(name)
            r = self.bios_table.rowCount()
            self.bios_table.insertRow(r)
            it0, it1 = QTableWidgetItem(name), QTableWidgetItem("" if cur is None else str(cur))
            it2 = QTableWidgetItem("" if locked else old_new.get(name) or self.bios_pending.get(name, ""))
            it0.setFlags(it0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it1.setFlags(it1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if locked:
                it2.setFlags(it2.flags() & ~Qt.ItemFlag.ItemIsEditable)
                for it in (it0, it1, it2):
                    it.setForeground(QBrush(QColor(140, 140, 140)))
                    it.setToolTip(T("Gesperrt: Passwort-, TPM- und Lösch-Attribute ändert das Tool nie. "
                                  "Ein Fehler dort kann den Server aussperren oder Daten kosten."))
            self.bios_table.setItem(r, 0, it0)
            self.bios_table.setItem(r, 1, it1)
            self.bios_table.setItem(r, 2, it2)
        self.bios_apply_filter()

    def bios_apply_filter(self, *_):
        needle = self.bios_filter.text().strip().lower()
        for r in range(self.bios_table.rowCount()):
            name = self.bios_table.item(r, 0)
            self.bios_table.setRowHidden(r, bool(needle) and needle not in name.text().lower())

    def bios_wanted(self):
        """Attribut -> ausgefüllter „Neuer Wert“, für alle nicht leeren und nicht gesperrten Zeilen."""
        out = {}
        for r in range(self.bios_table.rowCount()):
            name, val = self.bios_table.item(r, 0).text(), self.bios_table.item(r, 2).text().strip()
            if val and not BiosEngine.blocked(name):
                out[name] = val
        return out

    # ---------- UEFI-Vorlagen (eigener Bereich, getrennt von den Profilen in „Neuware“) ----------
    def bios_build_profiles(self):
        self.bios_profiles = {}
        self.bios_profile = QComboBox()
        self.bios_profile.setMinimumWidth(240)
        self.bios_profile.setToolTip(
            T("Eine Vorlage merkt sich Hersteller und die ausgefüllten „Neuer Wert“-Felder (Attribut ->\n"
            "Wert). Eigener Bereich, getrennt von den Profilen in „Neuware“ und ohne Passwörter."))
        self.bios_profile.activated.connect(self.bios_profile_selected)
        bar = QHBoxLayout()
        lab = QLabel(T("UEFI-Vorlage"))
        lab.setToolTip(self.bios_profile.toolTip())
        bar.addWidget(lab)
        bar.addWidget(self.bios_profile)
        for text, tip, slot in (
                (T("Speichern"), T("Speichert die aktuellen „Neuer Wert“-Felder in die gewählte Vorlage.\n"
                              "Ohne gewählte Vorlage wird eine neue angelegt."), self.bios_profile_save),
                (T("Neu …"), T("Legt aus den aktuellen Feldern eine neue Vorlage an."), self.bios_profile_new),
                (T("Umbenennen …"), T("Gibt der gewählten Vorlage einen anderen Namen."), self.bios_profile_rename),
                (T("Löschen"), T("Löscht die gewählte Vorlage."), self.bios_profile_delete)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()
        return bar

    def bios_profile_values(self):
        return {"vendor": self.bios_vendor.currentData() or "", "attrs": self.bios_wanted()}

    def bios_apply_profile(self, p):
        self.bios_vendor.setCurrentIndex(max(0, self.bios_vendor.findData(str(p.get("vendor", "")))))
        attrs = p.get("attrs") if isinstance(p.get("attrs"), dict) else {}
        self.bios_pending = {str(k): str(v) for k, v in attrs.items() if not BiosEngine.blocked(k)}
        for r in range(self.bios_table.rowCount()):
            name = self.bios_table.item(r, 0).text()
            if name in self.bios_pending:
                self.bios_table.item(r, 2).setText(self.bios_pending[name])

    def bios_current_profile(self):
        name = self.bios_profile.currentText()
        return name if name in self.bios_profiles else None

    def bios_refill_profiles(self, select=None):
        self.bios_profile.clear()
        self.bios_profile.addItem(i18n.NO_PROFILE)
        self.bios_profile.addItems(sorted(self.bios_profiles, key=str.lower))
        self.bios_profile.setCurrentIndex(max(0, self.bios_profile.findText(select or i18n.NO_PROFILE)))

    def bios_read_profiles(self):
        last = None
        try:
            data = json.loads(BIOS_PROFILES.read_text(encoding="utf-8"))
            self.bios_profiles = {str(k): v for k, v in data.get("profiles", {}).items() if isinstance(v, dict)}
            last = data.get("last")
        except (OSError, ValueError, AttributeError):
            self.bios_profiles = {}
        self.bios_refill_profiles(last)
        if self.bios_current_profile():
            self.bios_apply_profile(self.bios_profiles[self.bios_current_profile()])

    def bios_write_profiles(self):
        try:
            BIOS_PROFILES.write_text(json.dumps({"last": self.bios_current_profile(),
                                                 "profiles": self.bios_profiles},
                                                indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError as e:
            QMessageBox.warning(self, T("UEFI-Vorlagen"), T('{0} nicht schreibbar:\n{1}').format(BIOS_PROFILES.name, e))
            return False

    def bios_profile_selected(self, *_):
        name = self.bios_current_profile()
        if name:
            self.bios_apply_profile(self.bios_profiles[name])
            self.bios_status.setText(T('Vorlage „{0}“ geladen').format(name))
        if BIOS_PROFILES.exists():
            self.bios_write_profiles()

    def bios_profile_save(self):
        name = self.bios_current_profile()
        if not name:
            return self.bios_profile_new()
        self.bios_profiles[name] = self.bios_profile_values()
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ gespeichert').format(name))

    def bios_profile_new(self):
        name = self.ask_profile_name(T("Neue UEFI-Vorlage"))
        if not name:
            return
        if name in self.bios_profiles and QMessageBox.question(
                self, T("Neue UEFI-Vorlage"), T('Die Vorlage „{0}“ gibt es schon. Überschreiben?').format(name)
        ) != QMessageBox.StandardButton.Yes:
            return
        self.bios_profiles[name] = self.bios_profile_values()
        self.bios_refill_profiles(name)
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ gespeichert').format(name))

    def bios_profile_rename(self):
        old = self.bios_current_profile()
        if not old:
            self.bios_status.setText(T("Keine Vorlage gewählt"))
            return
        name = self.ask_profile_name(T("UEFI-Vorlage umbenennen"), old)
        if not name or name == old:
            return
        if name in self.bios_profiles:
            QMessageBox.warning(self, T("UEFI-Vorlage umbenennen"), T('Die Vorlage „{0}“ gibt es schon.').format(name))
            return
        self.bios_profiles[name] = self.bios_profiles.pop(old)
        self.bios_refill_profiles(name)
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ heißt jetzt „{1}“').format(old, name))

    def bios_profile_delete(self):
        name = self.bios_current_profile()
        if not name:
            self.bios_status.setText(T("Keine Vorlage gewählt"))
            return
        if QMessageBox.question(self, T("UEFI-Vorlage löschen"), T('Vorlage „{0}“ wirklich löschen?').format(name)
                                ) != QMessageBox.StandardButton.Yes:
            return
        del self.bios_profiles[name]
        self.bios_refill_profiles()
        if self.bios_write_profiles():
            self.bios_status.setText(T('Vorlage „{0}“ gelöscht').format(name))

    # ---------- UEFI-Lauf ----------
    def bios_run(self):
        wanted = self.bios_wanted()
        if not wanted:
            QMessageBox.warning(self, T("UEFI-Einstellungen"), T("Keine „Neuer Wert“-Felder ausgefüllt."))
            return
        rows = [(ip, pw or self.bios_pass.text()) for ip, pw in self.reachable_rows()]
        if not rows:
            QMessageBox.warning(self, T("UEFI-Einstellungen"), T("Die Serverliste („Neuware“) ist leer."))
            return
        dry = self.bios_dry.isChecked()
        reboot = self.bios_reboot.isChecked()
        if not dry:
            lines = [f"{k} = {v}" for k, v in sorted(wanted.items())]
            info = "\n".join(lines[:15])
            if len(lines) > 15:
                info += "\n" + T('… und {0} weitere').format(len(lines) - 15)
            info += "\n\n" + (
                T("Danach wird jeder Server hart neu gestartet, wie mit der Reset-Taste. Ein laufendes "
                "Betriebssystem wird dabei nicht sauber heruntergefahren.") if reboot else
                T("Es wird kein Neustart ausgelöst; die Änderung wirkt erst beim nächsten Neustart."))
            info += "\n\n" + T("Falsche UEFI-Werte können dazu führen, dass ein Server nicht mehr startet.")
            info += self.selfguard_note()
            if not self.confirm(T("Scharf ausführen?"),
                                T('Auf {0} Servern werden jetzt wirklich UEFI-Einstellungen geändert.').format(len(rows)),
                                info, len(rows)):
                return

        self.bios_log.clear()
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.bios_say(T('— {0}, {1} Server —').format(mode, len(rows)), "gray")
        self.bios_worker = BiosEngineThread(
            self, rows=rows, cur_user=self.bios_user.text().strip(), attrs=wanted, dry=dry,
            vendor=self.bios_vendor.currentData() or None, reboot=reboot)
        self.bios_worker.line.connect(self.bios_show_line)
        self.bios_worker.result.connect(self.bios_done)
        self.bios_start.setEnabled(False)
        self.bios_stop.setEnabled(True)
        self.bios_status.setText(T("Läuft …"))
        self.bios_worker.start()

    def bios_say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.bios_log.append(f'<span style="{style}">{html.escape(text)}</span>')
        LOG.info(T("UEFI-Einstellungen") + ": " + text, color, bold)

    def bios_show_line(self, line, kind=""):
        self.bios_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def bios_abort(self):
        if self.bios_worker and self.bios_worker.isRunning():
            self.bios_worker.requestInterruption()
            self.bios_stop.setEnabled(False)
            self.bios_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def bios_done(self, code, *_):
        self.bios_start.setEnabled(True)
        self.bios_stop.setEnabled(False)
        self.bios_status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))
