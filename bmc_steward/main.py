"""BMC-Steward: Hauptfenster (alle Reiter) und Programmstart."""
from pathlib import Path
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPixmap)
from PyQt6.QtCore import (QT_VERSION_STR, QBuffer, QByteArray, QEvent, QIODevice, QLocale, QObject, QRect, QSocketNotifier, QThread, QTimer, QUrl, Qt, pyqtSignal)
import html
import platform
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from . import i18n
from .i18n import HELP, HERE, LANGS, LIST, PROFILES, T, load_lang, set_language
from .core import intro_label, ABOUT, DONE_BG, ERR_BG, ERR_CSS, LIVE_CSS, MISS_BG, MISS_CSS, USER_RE, WINDOWS, __version__, ipv4, ipv4_port, mask, netmask, row_errors
from .branding import app_icon
from .monitor import AnimatedLogo
from .redfish import Engine, EngineThread, VendorProbeThread
from .dhcp import stop_leftover_dhcp
from .console import ConsoleMixin
from .sidenav import SideNav
from . import deps
from .firmware_tab import FirmwareTabMixin
from .users_tab import UsersTabMixin
from .capture_tab import CaptureTabMixin
from .vbmc_tab import VbmcTabMixin
from .planner_tab import PlannerTabMixin
from .bios_tab import BiosTabMixin
from .dhcp_tab import DhcpTabMixin
from .existing_tab import ExistingTabMixin
from .services_tab import ServicesTabMixin
from .scanner import ScannerDialog
from . import applog, selfguard, theme, trust, vault
from .applog import LOG, LevelCombo, LogLabel, LogView


DHCP_YELLOW = "#c9a100"      # Menüpunkt „DHCP-Server“ als dunkelgelber Knopf
MAIN = None         # das Hauptfenster; beim Sprachwechsel wird es neu aufgebaut




class RestoreDialog(QDialog):
    """Listet automatische Sicherungen auf und ermöglicht das selektive Vorbefüllen der Neuware-Maske
    mit gesicherten Netzwerkeinstellungen (IP, Subnetz, Gateway, Hostname)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(T("Aus Sicherung wiederherstellen"))
        self.setMinimumSize(900, 560)
        self._selected_data = None   # dict mit den extrahierten Netzwerkwerten des aktuell gewählten Backups

        # -- linke Seite: Dateiliste ------------------------------------------------
        self.file_table = QTableWidget(0, 5)
        self.file_table.setHorizontalHeaderLabels(
            [T("Datum / Uhrzeit"), T("IP-Adresse"), T("Hersteller"), T("Anlass"), T("Seriennr.")])
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.selectionModel().selectionChanged.connect(self._on_select)

        # -- rechte Seite: Details --------------------------------------------------
        detail_box = QGroupBox(T("Gesicherte Netzwerkeinstellungen"))
        df = QFormLayout(detail_box)
        self._d_ip      = QLabel("—"); self._d_mask = QLabel("—")
        self._d_gw      = QLabel("—"); self._d_host = QLabel("—")
        self._d_bak_ip  = QLabel("—"); self._d_time = QLabel("—")
        df.addRow(T("IP-Adresse:"),       self._d_ip)
        df.addRow(T("Subnetzmaske:"),     self._d_mask)
        df.addRow(T("Gateway:"),          self._d_gw)
        df.addRow(T("Hostname:"),         self._d_host)
        df.addRow(T("Letzte Adresse:"),   self._d_bak_ip)
        df.addRow(T("Sicherungszeit:"),   self._d_time)

        hint = QLabel(T(
            "„Als neue Zeile eintragen“ fügt in der Neuware-Maske eine neue Zeile\n"
            "mit der gesicherten Ziel-IP, Subnetz, Gateway und Hostname ein.\n"
            "Passwort und Hersteller müssen dort noch eingetragen werden."))
        hint.setWordWrap(True)

        self._apply_btn = QPushButton(T("Als neue Zeile eintragen"))
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self.accept)

        close_btn = QPushButton(T("Schließen"))
        close_btn.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._apply_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)

        right = QVBoxLayout()
        right.addWidget(detail_box)
        right.addWidget(hint)
        right.addStretch()
        right.addLayout(btn_row)

        # -- Haupt-Layout -----------------------------------------------------------
        split = QHBoxLayout()
        split.addWidget(self.file_table, 3)
        right_w = QWidget()
        right_w.setLayout(right)
        split.addWidget(right_w, 2)

        lay = QVBoxLayout(self)
        lay.addLayout(split)

        self._load_backups()

    # ---- Hilfsfunktionen ----------------------------------------------------------

    @staticmethod
    def _extract_network(resources):
        """Sucht in den gespeicherten Redfish-Ressourcen nach Netzwerkdaten eines EthernetInterface."""
        ip = mask = gw = host = ""
        for path, data in resources.items():
            if not isinstance(data, dict):
                continue
            # Einzelnes Interface (z. B. .../EthernetInterfaces/NIC.1)
            if "/EthernetInterfaces/" in path and path.count("/EthernetInterfaces/") == 1:
                if not host:
                    host = data.get("HostName", "")
                for key in ("IPv4StaticAddresses", "IPv4Addresses"):
                    addrs = data.get(key) or []
                    if addrs and isinstance(addrs[0], dict):
                        a = addrs[0]
                        if not ip:
                            ip   = a.get("Address", "")
                        if not mask:
                            mask = a.get("SubnetMask", "")
                        if not gw:
                            gw   = a.get("Gateway", "")
                        break
        return {"ip": ip, "mask": mask, "gw": gw, "host": host}

    def _load_backups(self):
        """Liest alle Backup-JSON-Dateien und füllt die Tabelle."""
        from . import i18n as _i18n
        base = _i18n.DATA / "sicherungen"
        entries = []
        if base.is_dir():
            for json_path in sorted(base.rglob("*.json"), reverse=True):
                try:
                    with open(json_path, encoding="utf-8") as fh:
                        doc = json.load(fh)
                except Exception:
                    continue
                meta = doc.get("meta", {})
                net  = self._extract_network(doc.get("resources", {}))
                entries.append((meta, net, json_path, doc.get("resources", {})))

        self.file_table.setRowCount(len(entries))
        self._entries = entries
        for row, (meta, net, path, _) in enumerate(entries):
            ts    = meta.get("time", "")[:16].replace("T", " ")
            addr  = meta.get("address", "")
            vendor = meta.get("vendor", "")
            reason = meta.get("reason", "")
            serial = path.parent.name
            for col, val in enumerate((ts, addr, vendor, reason, serial)):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.file_table.setItem(row, col, item)

    def _on_select(self):
        rows = self.file_table.selectedItems()
        if not rows:
            self._apply_btn.setEnabled(False)
            self._selected_data = None
            return
        row = self.file_table.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        meta, net, path, _ = self._entries[row]
        self._d_ip.setText(net["ip"]   or "—")
        self._d_mask.setText(net["mask"] or "—")
        self._d_gw.setText(net["gw"]   or "—")
        self._d_host.setText(net["host"] or "—")
        self._d_bak_ip.setText(meta.get("address", "—"))
        self._d_time.setText(meta.get("time", "—")[:19].replace("T", " "))
        self._selected_data = {
            "new_ip":   net["ip"],
            "mask":     net["mask"],
            "gw":       net["gw"],
            "host":     net["host"],
            "cur_ip":   meta.get("address", ""),
            "vendor":   meta.get("vendor", ""),
        }
        self._apply_btn.setEnabled(bool(net["ip"]))


class Window(ConsoleMixin, FirmwareTabMixin, CaptureTabMixin, UsersTabMixin, VbmcTabMixin, BiosTabMixin, DhcpTabMixin,
            ExistingTabMixin, ServicesTabMixin, PlannerTabMixin, QWidget):
    DEFAULT_SIZE = (1680, 980)           # Standardgröße des Fensters

    @classmethod
    def default_size(cls):
        """Standardgröße, aber nie größer als der verfügbare Bildschirm."""
        w, h = cls.DEFAULT_SIZE
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w, h = min(w, avail.width()), min(h, avail.height())
        return w, h

    def __init__(self, state=None):
        super().__init__()
        if theme._current is None:                 # erstes Fenster: Design setzen (run() tut es sonst schon)
            theme.apply(QApplication.instance())
        self.setWindowTitle(f"BMC-Steward {__version__} · Dell iDRAC / HPE iLO / Supermicro / Lenovo XCC")
        self.setWindowIcon(app_icon())
        self.resize(*self.default_size())
        self.worker = None
        self.bios_worker = None

        # --- Aktueller Login ---
        self.vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.vendor.addItem(text, key)
        self.vendor.currentIndexChanged.connect(self.vendor_changed)
        self.cur_user = QLineEdit()
        self.cur_pass = self._pw()
        self.user_hint = QLabel()
        apply_pw = QPushButton(T("Passwort für alle Server übernehmen"))
        apply_pw.setToolTip(T("Trägt das Passwort von oben in die Spalte „Passwort“ aller Zeilen ein\n"
                            "und überschreibt dort vorhandene Einträge."))
        apply_pw.clicked.connect(self.apply_password)
        box_cur = QGroupBox(T("Aktueller Login (Auslieferungszustand)"))
        box_cur.setToolTip(T("Mit diesen Daten meldet sich das Tool an den Geräten an, um sie umzustellen."))
        f = self.compact_form(box_cur)
        self.vendor_tip_base = T("„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller, gemischte Listen sind möglich.\n"
                                 "Eine feste Wahl überspringt die Erkennung und gilt für alle Zeilen. Nützlich, wenn die\n"
                                 "Erkennung danebenliegt oder der Trockenlauf den richtigen Ablauf zeigen soll.")
        self.user_tip_base = T("Benutzer, mit dem sich das Tool jetzt am BMC anmeldet, also der Werksbenutzer.\n"
                               "Leer lassen: Dann wird je Hersteller root, Administrator oder ADMIN verwendet.")
        self.tip_row(f, T("Hersteller"), self.vendor, self.vendor_tip_base)
        self.tip_row(f, T("Benutzer"), self.cur_user, self.user_tip_base)
        # Die Hinweise (Generationen, Werksbenutzer) stehen als Tooltip an Hersteller/Benutzer statt als eigene Zeilen
        self.version_hint = QLabel()
        self.version_hint.setVisible(False)
        self.user_hint.setVisible(False)
        self.tip_row(f, T("Passwort"), self._with_eye(self.cur_pass),
                     T("Passwort des Werksbenutzers. Es gilt für alle Zeilen, deren Spalte „Passwort“ leer ist,\n"
                     "und wird bei neuen Zeilen vorbelegt. Hat jedes Gerät ein eigenes Passwort\n"
                     "(HPE, Supermicro, Lenovo), dieses in der Tabelle je Zeile eintragen."))
        f.addRow("", apply_pw)

        # --- Neuer Benutzer ---
        self.new_user = QLineEdit()
        self.new_pass = self._pw()
        self.new_pass2 = self._pw()
        self.slot = QSpinBox()
        self.slot.setRange(3, 16)
        self.slot.setValue(3)
        box_new = QGroupBox(T("Neuer Administrator"))
        box_new.setToolTip(T("Dieses Konto wird auf jedem Gerät der Liste eingerichtet."))
        f = self.compact_form(box_new)
        user_tip = T("Name des Administrator-Kontos, das auf allen Geräten angelegt wird.\n"
                     "Existiert der Name bei HPE, Supermicro oder Lenovo schon, werden nur Passwort und Rolle gesetzt.")
        slot_tip = T("Nur Dell: Der iDRAC hat 16 feste Benutzerplätze, Platz 2 ist root.\n"
                     "Der neue Administrator wird in diesen Platz geschrieben. Ist der Platz schon\n"
                     "von einem anderen Benutzer belegt, wird nichts überschrieben. Standard ist Platz 3.")
        slot_lab = QLabel(T("User-Slot (nur Dell)"))
        slot_lab.setToolTip(slot_tip)
        self.slot.setToolTip(slot_tip)
        urow = QWidget()
        uh = QHBoxLayout(urow)
        uh.setContentsMargins(0, 0, 0, 0)
        uh.addWidget(self.new_user, 1)
        uh.addWidget(slot_lab)
        uh.addWidget(self.slot)
        self.tip_row(f, T("Benutzer"), urow, user_tip)
        self.new_user.setToolTip(user_tip)
        self.tip_row(f, T("Passwort"), self._with_eye(self.new_pass),
                     T("Passwort des neuen Administrators, auf allen Geräten gleich.\n"
                     "Die Geräte prüfen ihre eigenen Regeln, iLO verlangt z. B. mindestens 8 Zeichen.\n"
                     "Vor dem scharfen Lauf über das Auge kontrollieren."))
        self.new_pass2_chk = QCheckBox(T("Passwort wiederholen"))
        self.new_pass2_chk.setToolTip(
            T("Zur Sicherheit das Passwort ein zweites Mal eingeben.\n"
              "Solange nicht angehakt, ist das Feld deaktiviert und wird nicht geprüft.\n"
              "Angehakt: Stimmen die beiden Eingaben nicht überein, startet kein Lauf."))
        self.new_pass2_box = self._with_eye(self.new_pass2)
        self.new_pass2_box.setEnabled(False)
        self.new_pass2_box.setToolTip(self.new_pass2_chk.toolTip())
        self.new_pass2_chk.toggled.connect(self.new_pass2_box.setEnabled)
        self.new_pass2_chk.toggled.connect(self.touch)
        self.new_pass2_chk.toggled.connect(self.update_missing)
        f.addRow(self.new_pass2_chk, self.new_pass2_box)
        self.lock_factory = QCheckBox(T("Werkskonto danach sperren (empfohlen)"))
        self.lock_factory.setChecked(True)
        self.lock_factory.setToolTip(T(
            "Nach der Kontrolle meldet sich das Tool mit dem neuen Administrator unter der neuen Adresse an und\n"
            "sperrt das Werkskonto (root, Administrator, ADMIN, USERID), dessen Passwort auf dem Aufkleber steht.\n"
            "Nur wenn der neue Administrator dort nachweislich funktioniert und Administratorrechte hat.\n"
            "Kann ein BMC Konten nicht abschalten, bekommt das Werkskonto ein Zufallspasswort, das nirgends\n"
            "gespeichert wird; mit dem neuen Administrator lässt es sich jederzeit zurücksetzen."))
        self.clear_pass_after = QCheckBox(T("Passwörter nach scharfem Lauf löschen"))
        self.clear_pass_after.setChecked(True)
        self.clear_pass_after.setToolTip(T(
            "Leert nach jedem scharfen Lauf alle Passwortfelder (Werkskonto, neuer Administrator, je Zeile).\n"
            "Die Passwörter bleiben nur so lange im Arbeitsspeicher, wie nötig. Trockenlauf löscht nichts.\n"
            "Nach dem Löschen müssen die Passwörter vor dem nächsten scharfen Lauf neu eingegeben werden."))
        f.addRow(self.lock_factory)                 # untereinander: die Seite bleibt auch auf 1366er-Bildschirmen schmal
        f.addRow(self.clear_pass_after)

        # --- Vorgaben für neue Zeilen ---
        self.def_mask = QLineEdit()
        self.def_mask.setPlaceholderText(T("z. B. 255.255.255.0 oder /24"))
        self.def_gw = QLineEdit()
        self.def_gw.setPlaceholderText(T("z. B. 10.20.0.1"))
        for w in (self.def_mask, self.def_gw):
            w.editingFinished.connect(self.validate_defaults)
        box_def = QGroupBox(T("Vorgaben für neue Zeilen"))
        box_def.setToolTip(T("Gelten für Zeilen, die danach neu entstehen („+ Server“, „Server suchen“, DHCP-Übernahme).\n"
                             "Schon vorhandene Zeilen ändert erst der Knopf „Auf Liste anwenden“."))
        f = self.compact_form(box_def)
        self.tip_row(f, T("Subnetzmaske"), self.def_mask,
                     T("Wird bei jeder neuen Zeile der Serverliste vorbelegt, auch bei Geräten aus der DHCP-Liste.\n"
                     "Leer lassen: Dann wird der Wert der vorherigen Zeile übernommen."))
        self.tip_row(f, T("Gateway"), self.def_gw,
                     T("Wird bei jeder neuen Zeile der Serverliste vorbelegt.\n"
                     "Leer lassen: Dann wird der Wert der vorherigen Zeile übernommen."))
        apply_def = QPushButton(T("Auf Liste anwenden"))
        apply_def.setToolTip(T("Trägt Subnetzmaske und Gateway in die markierten Zeilen ein (ohne Markierung: in alle Zeilen)\n"
                               "und überschreibt dort vorhandene Werte. Ein leeres Feld lässt die Spalte unverändert."))
        apply_def.clicked.connect(self.apply_defaults)
        f.addRow("", apply_def)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(box_cur, 4)
        top.addWidget(box_new, 6)
        top.addWidget(box_def, 3)

        # --- Serverliste ---
        self.vendor_col = len(i18n.COLS)     # zusätzliche, nur angezeigte Spalte, keine Daten-COLS-Spalte
        self.vendor_tip = T("Hersteller/Typ, automatisch anhand der Aktuelle-IP ermittelt. Nur Anzeige,\n"
                           "nicht editierbar; wird beim Eintragen/Ändern der Aktuelle IP neu geprüft.")
        self.vendor_threads = {}             # Aktuelle IP -> laufender VendorProbeThread
        self.table = QTableWidget(0, len(i18n.COLS) + 1)
        self.table.setHorizontalHeaderLabels(i18n.COLS + [T("Hersteller/Typ")])
        for c, tip in enumerate(i18n.COL_TIPS):
            self.table.horizontalHeaderItem(c).setToolTip(tip)
        self.table.horizontalHeaderItem(self.vendor_col).setToolTip(self.vendor_tip)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.update_count)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.itemChanged.connect(self.vault_on_item_changed)
        add = QPushButton(T("+ Server"))
        add.setToolTip(T("Neue Zeile. Passwort, Subnetzmaske und Gateway werden vorbelegt."))
        add.clicked.connect(lambda: self.add_row())
        rem = QPushButton(T("– Entfernen"))
        rem.setToolTip(T("Entfernt die markierten Zeilen."))
        rem.clicked.connect(self.remove_rows)
        again = QPushButton(T("Erneut bearbeiten"))
        again.setToolTip(T("Nimmt bei den markierten Zeilen die grüne Fertig-Markierung zurück,\n"
                         "damit sie beim nächsten Start wieder bearbeitet werden."))
        again.clicked.connect(self.reopen_rows)
        load = QPushButton(T("Liste laden …"))
        load.setToolTip(T("Lädt eine gespeicherte Liste oder eine CSV-Datei, z. B. aus Excel:\n"
                        "aktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,seriennummer\n"
                        "(seriennummer ist optional, auch ganz ohne diese Spalte ladbar)"))
        load.clicked.connect(self.load_dialog)
        save = QPushButton(T("Liste speichern …"))
        save.setToolTip(T("Speichert die Liste in eine Datei. Das Tool speichert nie von selbst.\n"
                        "Beim Speichern wird gefragt, ob die Passwörter mit in die Datei sollen."))
        save.clicked.connect(self.save_dialog)
        self.count = QLabel()
        self.count.setObjectName("pill")
        btns = QHBoxLayout()
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addWidget(again)
        scan = QPushButton(T("Handy-Scanner …"))
        scan.setToolTip(T("Passwörter vom Aufkleber/Anhänger mit der Handy-Kamera abfotografieren und direkt in die\n"
                        "Spalte „Passwort“ eintragen. Das Handy speichert keine Bilder."))
        scan.clicked.connect(self.open_scanner)
        btns.addWidget(scan)
        find = QPushButton(T("Server suchen …"))
        find.setToolTip(T("Adresse unbekannt? Sucht im Netz nach BMCs (nur lesend, ohne Anmeldung) und übernimmt die\n"
                          "markierten Treffer als neue Zeilen in diese Liste."))
        find.clicked.connect(lambda: self.open_netscan(False))
        btns.addWidget(find)
        btns.addWidget(load)
        btns.addWidget(save)
        btns.addStretch()
        btns.addWidget(self.count)

        self.vault_use = QCheckBox(T("Passwörter im Schlüsselbund speichern (statt im Klartext)"))
        self.vault_use.toggled.connect(self.vault_use_toggled)
        vault.probe_async()
        self.vault_show_availability()
        vault_clear = QPushButton(T("Schlüsselbund für diese Liste leeren"))
        vault_clear.setToolTip(T("Entfernt die Schlüsselbund-Einträge aller Server dieser Liste wieder. "
                                 "Ändert nichts an der Tabelle selbst."))
        vault_clear.clicked.connect(self.vault_clear_list)
        vrow = QHBoxLayout()
        vrow.addWidget(self.vault_use)
        vrow.addStretch()
        vrow.addWidget(vault_clear)

        box_list = QGroupBox(T("Serverliste"))
        v = QVBoxLayout(box_list)
        v.addWidget(self.table)
        v.addLayout(btns)
        v.addLayout(vrow)

        # --- Start ---
        self.dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.dry.setObjectName("toggle")
        self.dry.setChecked(True)
        self.dry.setToolTip(T("Mit Haken wird nichts geändert: Das Protokoll zeigt nur, welche Aufrufe\n"
                            "an welches Gerät gingen. Ohne Haken werden die Geräte wirklich umgestellt."))
        self.start = QPushButton(T("Start"))
        self.start.setDefault(True)
        self.start.setMinimumHeight(38)
        self.start.setToolTip(T("Prüft die Eingaben und bearbeitet dann alle noch offenen Zeilen der Reihe nach:\n"
                              "Benutzer anlegen, Hostname setzen, feste Adresse setzen.\n"
                              "Fertige (grüne) Zeilen werden übersprungen. Vor einem scharfen Lauf\n"
                              "wird nachgefragt."))
        self.start.clicked.connect(self.run)
        self.start.setObjectName("primary")
        self.stop = QPushButton(T("Abbrechen"))
        self.stop.setToolTip(T("Bricht nach dem Server ab, der gerade bearbeitet wird"))
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.abort)
        self.serial_btn = QPushButton(T("Seriennummern auslesen"))
        self.serial_btn.setToolTip(T("Liest für alle Zeilen ohne Seriennummer die Seriennummer (Dell: Service-Tag) aus dem BMC.\n"
                                     "Nur lesend, ein Anmeldeversuch je Gerät mit dem Passwort der Zeile (iLO 3/4: ohne Anmeldung).\n"
                                     "Das geschieht auch automatisch beim Eintragen einer Aktuellen IP. Eingetragenes wird nie überschrieben."))
        self.serial_btn.clicked.connect(self.read_serials)
        log_open = QPushButton(T("Protokoll"))
        log_open.setToolTip(T("Öffnet das Protokoll als Textdatei im Standard-Editor.\n"
                              "Passwörter stehen nicht darin; geändert wird nichts."))
        log_open.clicked.connect(self.open_log)
        self.log_level = LevelCombo()
        actions = QHBoxLayout()
        actions.addWidget(self.dry)
        actions.addStretch()
        actions.addWidget(self.serial_btn)
        actions.addWidget(log_open)
        actions.addWidget(self.log_level)
        actions.addWidget(self.stop)
        actions.addWidget(self.start)

        # --- Was fehlt noch? ---
        self.cells_missing = self.cells_bad = 0
        self.todo = QLabel()
        self.todo.setObjectName("todoBox")
        self.todo.setWordWrap(True)
        self.todo.setToolTip(T("Zeigt, was vor dem Start noch auszufüllen ist. Die betroffenen Felder sind\n"
                               "gelb (fehlt noch) oder rot (fehlerhaft) markiert."))
        legend = QLabel(
            "<small><span style='background:#f0ad4e; color:#000000'>&nbsp;" + T("fehlt noch") + "&nbsp;</span> "
            "<span style='background:#d9534f; color:#ffffff'>&nbsp;" + T("fehlerhaft") + "&nbsp;</span> "
            "<span style='background:#3fa45b; color:#ffffff'>&nbsp;" + T("fertig") + "&nbsp;</span></small>")
        hints = QHBoxLayout()
        hints.addWidget(self.todo, 1)
        hints.addWidget(legend)

        # --- Protokoll ---
        self.log = LogView()                       # gemeinsames Protokoll, auch im Testlabor
        self.status = LogLabel(T("Bereit"))

        upper = QWidget()
        u = QVBoxLayout(upper)
        u.setContentsMargins(0, 0, 0, 0)
        u.addWidget(box_list)
        u.addLayout(hints)
        u.addLayout(actions)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(upper)
        split.addWidget(self.log)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([640, 130])
        split.setChildrenCollapsible(False)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(4, 4, 4, 4)
        pl.setSpacing(4)
        pl.addLayout(self.build_profiles())
        pl.addLayout(top)
        pl.addWidget(split, 1)
        monitor_block = self.build_monitor()       # Monitor links oben; muss vor build_console existieren
        self.tabs = SideNav()                      # Menü links, Seiten rechts (statt Reiterleiste)
        self.deps_footers = []                     # Randbereich je Seite: fehlende optionale Pakete
        dhcp_page = self.build_dhcp()
        self.tabs.addTab(dhcp_page, T("DHCP-Server"), accent=DHCP_YELLOW, mark=0, section=T("Einrichtung"))
        pl.insertWidget(0, intro_label(T(
            "<b>Neuware</b> richtet frische Server ein: Für jede Zeile der Serverliste legt das Tool den neuen "
            "Administrator an, setzt den Hostnamen und stellt den BMC auf die feste Adresse um. Danach meldet es sich "
            "zur Kontrolle unter der neuen Adresse an und sperrt das Werkskonto.<br><b>Ablauf:</b> Server eintragen "
            "(oder aus „DHCP-Server“ übernehmen), oben den neuen Administrator eingeben, im Trockenlauf prüfen, dann "
            "scharf starten. Fertige Zeilen werden grün und beim nächsten Lauf übersprungen.")))
        self.tabs.addTab(page, T("Neuware"), mark=1, scroll=True)
        self.tabs.addTab(self.build_existing(), T("Bestandsserver"), mark=2)
        con_page = self.build_console()
        for widget, key in ((dhcp_page, "dhcp"), (page, "new"), (con_page, "console")):
            self.deps_footers.append(deps.attach(widget, key))
        self.tabs.addTab(self.build_bios(), T("UEFI-Einstellungen"), mark=3)
        self.services_page = self.build_services()
        self.services_page.layout().insertWidget(0, intro_label(T(
            "<b>Dienste</b> richtet auf allen Servern der Serverliste dieselben Verwaltungsdienste ein, z. B. "
            "Zeitserver (NTP), Syslog, LDAP/Active Directory, DNS und die Härtung (unsichere Dienste aus, Kontosperre, "
            "Sitzungs-Zeitlimit). Geändert wird nur, was angehakt ist; der Trockenlauf zeigt vorher jeden Aufruf.")))
        self.tabs.addTab(self.services_page, T("Dienste"), mark=4)
        self.tabs.addTab(self.build_users(), T("Benutzerkonten"), mark=5)
        fw_page = self.build_firmware()
        fw_page.layout().insertWidget(0, intro_label(T(
            "<b>Firmware</b> spielt ein Update auf alle Server der Serverliste: erst das Inventar eines Referenzgeräts "
            "lesen, dann eine Datei vom PC oder eine URL wählen. Der Trockenlauf ist voreingestellt; ein scharfer Lauf "
            "verlangt das Eintippen der Serverzahl. Tipp: vorher und nachher unter „Aufnahme“ aufnehmen und "
            "vergleichen.")))
        self.tabs.addTab(fw_page, T("Firmware"), mark=6)
        self.tabs.addTab(con_page, T("UEFI-Konsole"), mark="▢", section=T("Werkzeuge"))
        self.tabs.addTab(self.build_capture(), T("Aufnahme"), mark="◉")
        vbmc_page = self.build_vbmc()
        self.vbmc_page = vbmc_page
        self.deps_footers.append(deps.attach(vbmc_page, "vbmc"))
        self.tabs.addTab(vbmc_page, T("Testlabor"), mark="⚗", scroll=True)
        self.tabs.addTab(self.build_planner(), T("Aufbauplaner"), mark="▦")
        help_view = QTextBrowser()
        help_view.setOpenExternalLinks(True)
        help_view.setHtml(HELP[i18n.LANG] + ABOUT)
        help_page = QWidget()                      # Hilfe-Text, darunter das animierte Logo
        hl = QVBoxLayout(help_page)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(help_view, 1)
        self.help_logo = AnimatedLogo(64)
        hl.addWidget(self.help_logo, 0, Qt.AlignmentFlag.AlignHCenter)
        self.tabs.addTab(help_page, T("Hilfe"), mark="?")
        self.add_page_headers()
        self.lang = QComboBox()
        for code, name in LANGS:
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(i18n.LANG)))
        self.lang.setToolTip("Sprache · Language · Langue · Sproch")
        self.lang.activated.connect(self.switch_language)
        self.design = QComboBox()
        for key, text in (("auto", T("Design: automatisch")), ("light", T("Design: hell")),
                          ("dark", T("Design: dunkel"))):
            self.design.addItem(text, key)
        self.design.setCurrentIndex(max(0, self.design.findData(theme.load_choice())))
        self.design.setToolTip(T("Automatisch folgt der Hell/Dunkel-Einstellung des Systems (Windows und Linux). "
                                 "Die Wahl merkt sich das Tool in bmc-design.json."))
        self.design.activated.connect(self.design_changed)

        # Linke Spalte: Monitor mit Varianten-Schalter, darunter das Menü, ganz unten die Sprache
        side = QWidget()
        side.setObjectName("sidebar")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.nav_side = side                       # wird im Vollbild der Konsole ausgeblendet
        side.setFixedWidth(272)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 10)
        sl.setSpacing(8)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(32, 32))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        bname = QLabel("BMC-Steward")
        bname.setObjectName("brandName")
        bver = QLabel(T("Version {0}").format(__version__))
        bver.setObjectName("brandVersion")
        names.addWidget(bname)
        names.addWidget(bver)
        brand.addLayout(names, 1)
        sl.addLayout(brand)
        sl.addWidget(monitor_block)
        sl.addWidget(self.tabs.menu, 1)
        self.guard_btn = QPushButton()
        self.guard_btn.clicked.connect(self.edit_selfguard)
        self.update_guard_btn()
        pins_btn = QPushButton(T("Zertifikate"))
        pins_btn.setToolTip(T("Gemerkte Zertifikats-Fingerabdrücke der BMCs ansehen und einzelne entfernen "
                              "(nach gewolltem Zertifikatswechsel oder Mainboard-Tausch)."))
        pins_btn.clicked.connect(self.edit_pins)
        data_btn = QPushButton(T("Sicherungen"))
        data_btn.setToolTip(T("Öffnet den Datenordner mit den automatischen Sicherungen vor jeder Änderung und dem "
                              "Revisionsprotokoll aller scharfen Läufe:\n{0}").format(i18n.DATA))
        data_btn.clicked.connect(self.open_data_dir)
        restore_btn = QPushButton(T("Wiederherstellen"))
        restore_btn.setToolTip(T(
            "Zeigt alle automatischen Sicherungen und ermöglicht das Vorbefüllen\n"
            "der Neuware-Maske mit gesicherten Netzwerkeinstellungen."))
        restore_btn.clicked.connect(self.open_restore_dialog)
        tools = QGridLayout()                      # 2 x 2 statt vier abgeschnittener Knöpfe in einer Reihe
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        for n, btn in enumerate((self.guard_btn, pins_btn, data_btn, restore_btn)):
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)   # Breite kommt vom Raster
            tools.addWidget(btn, n // 2, n % 2)
        sl.addLayout(tools)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addWidget(self.lang, 1)
        bottom.addWidget(self.design, 1)
        sl.addLayout(bottom)
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(side)
        body.addWidget(self.tabs.stack, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 12, 6)
        lay.addLayout(body, 1)
        self.status.setContentsMargins(14, 2, 0, 0)
        lay.addWidget(self.status)
        self.main_lay, self.main_body = lay, body        # Ränder fallen im Vollbild der Konsole weg

        self.dirty = False
        self.vendor_changed()
        self.read_profiles()
        if vault.ready():
            self.vault_load_state()                 # sonst nach der Schlüsselbund-Suche (vault_show_availability)
        self.add_row()
        self.update_count()
        self.tabs.setCurrentIndex(0)
        for w in (self.dhcp_from, self.dhcp_to):
            w.textChanged.connect(self.validate_range)
        self.iface.currentIndexChanged.connect(self.validate_range)
        self.validate_range()

        # Änderungen merken, damit beim Schließen nachgefragt werden kann
        self.table.itemChanged.connect(self.touch)
        for w in (self.cur_user, self.cur_pass, self.new_user, self.new_pass):
            w.textChanged.connect(self.touch)
        for w in (self.cur_pass, self.new_user, self.new_pass, self.new_pass2):
            w.textChanged.connect(self.update_missing)
        self.dry.toggled.connect(self.mode_changed)
        self.bios_dry.toggled.connect(self.bios_mode_changed)
        self.svc_dry.toggled.connect(self.svc_mode_changed)
        self.dirty = False
        if state:
            self.import_state(state)
        elif stop_leftover_dhcp():
            self.status.setText(T("Ein DHCP-Server aus einem früheren, abgebrochenen Lauf lief noch und wurde beendet."))
        self.mode_changed()
        self.bios_mode_changed()
        self.svc_mode_changed()
        self.tabs.currentChanged.connect(self.page_logged)
        self.tabs.currentChanged.connect(
            lambda i: self.planner_page_shown() if i == self.tabs.indexOf(self.planner_page) else None)
        self.tabs.currentChanged.connect(
            lambda i: self.services_update_caps() if i == self.tabs.indexOf(self.services_page) else None)
        applog.watch(self)
        w, h = self.width(), self.height()
        LOG.debug(T("Fenster aufgebaut ({0} × {1}), Sprache {2}").format(w, h, i18n.LANG))

    def add_page_headers(self):
        """Kopf jeder Arbeitsseite: Bereich › Schritt als Pfad, darunter der Titel (wie im Entwurf). Konsole,
        Aufbauplaner und Hilfe brauchen den Platz selbst und bekommen keinen."""
        titles = {T("DHCP-Server"): T("DHCP-Server für das Staging-Netz"), T("Neuware"): T("Neuware einrichten"),
                  T("Bestandsserver"): T("Bestandsserver prüfen"), T("UEFI-Einstellungen"): T("UEFI-Einstellungen setzen"),
                  T("Dienste"): T("Dienste einrichten"), T("Benutzerkonten"): T("Benutzerkonten verwalten"),
                  T("Firmware"): T("Firmware aktualisieren"), T("Aufnahme"): T("Aufnahme und Vergleich"),
                  T("Testlabor"): T("Testlabor")}
        from .sidenav import MARK_ROLE
        section = ""
        for i in range(self.tabs.count()):
            item = self.tabs.menu.item(i)
            section = item.data(Qt.ItemDataRole.UserRole + 3) or section
            title = titles.get(item.text())
            page = self.tabs.widget(i)
            lay = page.layout() if title else None
            if lay is None:
                continue
            mark = item.data(MARK_ROLE)
            crumb = section + (" › " + T("Schritt {0}").format(mark) if isinstance(mark, int) else "")
            head = QWidget()
            hl = QVBoxLayout(head)
            hl.setContentsMargins(2, 6, 2, 2)
            hl.setSpacing(0)
            c = QLabel(crumb)
            c.setObjectName("pageCrumb")
            t = QLabel(title)
            t.setObjectName("pageTitle")
            hl.addWidget(c)
            hl.addWidget(t)
            lay.insertWidget(0, head)

    def design_changed(self, *_):
        choice = self.design.currentData() or "auto"
        theme.save_choice(choice)
        name = theme.apply(QApplication.instance(), choice)
        LOG.debug(T("Design: {0}").format(name))

    def page_logged(self, i):
        item = self.tabs.menu.item(i)
        if item is not None:
            LOG.debug(T("Seite: {0}").format(item.text()))

    # ---------- Hilfen ----------
    @staticmethod
    def tip_row(form, label, widget, tip):
        """Formularzeile, bei der Beschriftung und Feld denselben Hilfetext zeigen."""
        lab = QLabel(label)
        lab.setToolTip(tip)
        widget.setToolTip(tip)
        form.addRow(lab, widget)

    def touch(self, *_):
        self.dirty = True

    # ---------- Profile ----------
    def build_profiles(self):
        self.profiles = {}
        self.profile = QComboBox()
        self.profile.setMinimumWidth(240)
        self.profile.setToolTip(
            T("Ein Profil merkt sich die Einstellungen dieser Maske: Hersteller, Werksbenutzer, neuen\n"
            "Administrator, User-Slot, Vorgaben für neue Zeilen und die DHCP-Einstellungen.\n"
            "Passwörter und die Serverliste gehören nicht dazu."))
        self.profile.activated.connect(self.profile_selected)
        bar = QHBoxLayout()
        lab = QLabel(T("Profil"))
        lab.setToolTip(self.profile.toolTip())
        bar.addWidget(lab)
        bar.addWidget(self.profile)
        for text, tip, slot in (
                (T("Speichern"), T("Speichert die aktuellen Einstellungen in das gewählte Profil.\n"
                              "Ohne gewähltes Profil wird ein neues angelegt."), self.profile_save),
                (T("Neu …"), T("Legt aus den aktuellen Einstellungen ein neues Profil an."), self.profile_new),
                (T("Umbenennen …"), T("Gibt dem gewählten Profil einen anderen Namen."), self.profile_rename),
                (T("Löschen"), T("Löscht das gewählte Profil. Die Einstellungen in der Maske bleiben stehen."),
                 self.profile_delete)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()
        return bar

    def profile_values(self):
        """Aktuelle Einstellungen der Maske, ohne Passwörter."""
        return {"vendor": self.vendor.currentData() or "", "cur_user": self.cur_user.text().strip(),
                "new_user": self.new_user.text().strip(), "slot": self.slot.value(),
                "mask": self.def_mask.text().strip(), "gateway": self.def_gw.text().strip(),
                "dhcp_if": (self.iface.currentData() or [""])[0],
                "dhcp_from": self.dhcp_from.text().strip(), "dhcp_to": self.dhcp_to.text().strip(),
                "dhcp_auto": self.auto_take.isChecked(), "lock_factory": self.lock_factory.isChecked(),
                "clear_pass_after": self.clear_pass_after.isChecked()}

    def apply_profile(self, p):
        dirty = self.dirty                      # ein Profilwechsel ändert die Serverliste nicht
        self.vendor.setCurrentIndex(max(0, self.vendor.findData(str(p.get("vendor", "")))))
        self.cur_user.setText(str(p.get("cur_user", "")))
        self.new_user.setText(str(p.get("new_user", "")))
        if str(p.get("slot", "")).isdigit():
            self.slot.setValue(int(p["slot"]))
        self.def_mask.setText(str(p.get("mask", "")))
        self.def_gw.setText(str(p.get("gateway", "")))
        for i in range(self.iface.count()):
            if self.iface.itemData(i)[0] == p.get("dhcp_if"):
                self.iface.setCurrentIndex(i)
        if p.get("dhcp_from") and p.get("dhcp_to"):
            self.dhcp_from.setText(str(p["dhcp_from"]))
            self.dhcp_to.setText(str(p["dhcp_to"]))
        self.auto_take.setChecked(bool(p.get("dhcp_auto", False)))     # ältere Profile ohne Angabe: aus
        self.lock_factory.setChecked(bool(p.get("lock_factory", True)))
        self.clear_pass_after.setChecked(bool(p.get("clear_pass_after", True)))
        self.validate_defaults()
        self.validate_range()
        self.dirty = dirty

    def current_profile(self):
        name = self.profile.currentText()
        return name if name in self.profiles else None

    def refill_profiles(self, select=None):
        self.profile.clear()
        self.profile.addItem(i18n.NO_PROFILE)
        self.profile.addItems(sorted(self.profiles, key=str.lower))
        self.profile.setCurrentIndex(max(0, self.profile.findText(select or i18n.NO_PROFILE)))

    def read_profiles(self):
        """Profile beim Start einlesen und das zuletzt genutzte anwenden."""
        last = None
        try:
            data = json.loads(PROFILES.read_text(encoding="utf-8"))
            self.profiles = {str(k): v for k, v in data.get("profiles", {}).items() if isinstance(v, dict)}
            last = data.get("last")
        except (OSError, ValueError, AttributeError):
            self.profiles = {}
        self.refill_profiles(last)
        if self.current_profile():
            self.apply_profile(self.profiles[self.current_profile()])

    def write_profiles(self):
        try:
            PROFILES.write_text(json.dumps({"lang": i18n.LANG, "last": self.current_profile(), "profiles": self.profiles},
                                           indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError as e:
            QMessageBox.warning(self, T("Profile"), T('{0} nicht schreibbar:\n{1}').format(PROFILES.name, e))
            return False

    def profile_selected(self, *_):
        name = self.current_profile()
        if name:
            self.apply_profile(self.profiles[name])
            self.status.setText(T('Profil „{0}“ geladen').format(name))
        if PROFILES.exists():
            self.write_profiles()               # merkt sich das zuletzt genutzte Profil

    def profile_save(self):
        name = self.current_profile()
        if not name:
            return self.profile_new()
        self.profiles[name] = self.profile_values()
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ gespeichert (ohne Passwörter)').format(name))

    def ask_profile_name(self, title, preset=""):
        name, good = QInputDialog.getText(self, title, T("Name des Profils:"), text=preset)
        name = name.strip()
        if not good or not name:
            return None
        if name == i18n.NO_PROFILE:
            QMessageBox.warning(self, title, T("Dieser Name ist reserviert."))
            return None
        return name

    def profile_new(self):
        name = self.ask_profile_name(T("Neues Profil"))
        if not name:
            return
        if name in self.profiles and QMessageBox.question(
                self, T("Neues Profil"), T('Das Profil „{0}“ gibt es schon. Überschreiben?').format(name)
        ) != QMessageBox.StandardButton.Yes:
            return
        self.profiles[name] = self.profile_values()
        self.refill_profiles(name)
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ gespeichert (ohne Passwörter)').format(name))

    def profile_rename(self):
        old = self.current_profile()
        if not old:
            self.status.setText(T("Kein Profil gewählt"))
            return
        name = self.ask_profile_name(T("Profil umbenennen"), old)
        if not name or name == old:
            return
        if name in self.profiles:
            QMessageBox.warning(self, T("Profil umbenennen"), T('Das Profil „{0}“ gibt es schon.').format(name))
            return
        self.profiles[name] = self.profiles.pop(old)
        self.refill_profiles(name)
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ heißt jetzt „{1}“').format(old, name))

    def profile_delete(self):
        name = self.current_profile()
        if not name:
            self.status.setText(T("Kein Profil gewählt"))
            return
        if QMessageBox.question(self, T("Profil löschen"), T('Profil „{0}“ wirklich löschen?').format(name)
                                ) != QMessageBox.StandardButton.Yes:
            return
        del self.profiles[name]
        self.refill_profiles()
        if self.write_profiles():
            self.status.setText(T('Profil „{0}“ gelöscht').format(name))

    def validate_defaults(self):
        """Prüft die Vorgaben für neue Zeilen und markiert ungültige Felder."""
        m = re.fullmatch(r"/?(\d{1,2})", self.def_mask.text().strip())      # /24 oder 24 -> 255.255.255.0
        if m and int(m.group(1)) <= 32:
            self.def_mask.setText(str(ipaddress.IPv4Network(f"0.0.0.0/{m.group(1)}").netmask))
        mask, gw = self.def_mask.text().strip(), self.def_gw.text().strip()
        bad = {self.def_mask: bool(mask) and not netmask(mask), self.def_gw: bool(gw) and not ipv4(gw)}
        for w, wrong in bad.items():
            w.setStyleSheet("QLineEdit { border: 1px solid #d9534f; }" if wrong else "")
        return not any(bad.values())

    def vendor_changed(self, *_):
        v = self.vendor.currentData() or ""
        self.cur_user.setPlaceholderText(i18n.USER_HINT[v])
        self.cur_pass.setPlaceholderText(i18n.PASS_HINT[v])
        self.user_hint.setText(
            T("<small>Leer lassen für: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>") if not v
            else T('<small>Leer lassen für den Werksbenutzer {0}</small>').format(i18n.USER_HINT[v]))
        self.version_hint.setText(f"<small>{i18n.VERSION_HINT[v]}</small>")
        self.version_hint.setToolTip(i18n.VERSION_HINT[v])
        # Die Hinweise stehen kompakt als Tooltip an Hersteller und Benutzer (die Labels sind ausgeblendet)
        self.vendor.setToolTip(self.vendor_tip_base + "\n\n" + i18n.VERSION_HINT[v])
        self.cur_user.setToolTip(self.user_tip_base + "\n\n" + re.sub(r"<[^>]+>", "", self.user_hint.text()))
        self.slot.setEnabled(v in ("", "dell"))

    @staticmethod
    def _pw(text=""):
        e = QLineEdit(text)
        e.setEchoMode(QLineEdit.EchoMode.Password)
        return e

    @staticmethod
    def _with_eye(edit):
        btn = QPushButton("👁")
        btn.setCheckable(True)
        btn.setFixedWidth(36)
        btn.toggled.connect(lambda on: edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit)
        h.addWidget(btn)
        return w

    def say(self, text, color=None, bold=False):
        LOG.info(text, color, bold)

    # ---------- Serverliste ----------
    def add_row(self, values=None, vendor_text=""):
        """Neue Zeile; ohne Werte gelten die Vorgaben, sonst Maske und Gateway der letzten Zeile."""
        r = self.table.rowCount()
        if values is None:
            values = [""] * len(i18n.COLS)
            values[1] = self.cur_pass.text()
            values[3] = self.def_mask.text().strip() or (self.cell(r - 1, 3) if r else "")
            values[4] = self.def_gw.text().strip() or (self.cell(r - 1, 4) if r else "")
        self.table.insertRow(r)
        for c, val in enumerate(values):
            self.table.setItem(r, c, QTableWidgetItem(val))
        vitem = QTableWidgetItem(vendor_text)
        vitem.setFlags(vitem.flags() & ~Qt.ItemFlag.ItemIsEditable)
        vitem.setToolTip(self.vendor_tip)
        self.table.setItem(r, self.vendor_col, vitem)
        self.table.setCurrentCell(r, 0)
        if not vendor_text and ipv4(values[0] if values else ""):
            self.probe_vendor(values[0])

    def netscan_to_list(self, rows):
        """Treffer der Netzsuche als neue Zeilen („Aktuelle IP“, Seriennummer, erkannter Hersteller); schon
        vorhandene Adressen werden übersprungen. Passwort, Maske und Gateway kommen aus den Vorgaben oben."""
        have = {self.cell(r, 0) for r in range(self.table.rowCount())}
        added = 0
        for row in rows:
            ip = row["ip"]
            if ip in have:
                continue
            values = [""] * len(i18n.COLS)
            values[0] = ip
            values[1] = self.cur_pass.text()
            values[3] = self.def_mask.text().strip()
            values[4] = self.def_gw.text().strip()
            values[6] = row.get("serial", "")
            self.add_row(values, Engine.NAME.get(row["vendor"], ""))
            have.add(ip)
            added += 1
        self.update_count()
        self.touch()
        self.say(T("Server suchen: {0} neue Zeilen in der Serverliste.").format(added))

    def remove_rows(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self.update_count()
        self.touch()

    def is_done(self, r):
        """Zeile ist fertig, solange die Aktuelle IP noch die ist, mit der sie umgestellt wurde."""
        item = self.table.item(r, 0)
        return bool(item) and item.text().strip() != "" and item.data(Qt.ItemDataRole.UserRole) == item.text().strip()

    def mark_done(self, ip):
        """Der Engine-Thread meldet einen fertig umgestellten Server: Zeile grün markieren."""
        for r in range(self.table.rowCount()):
            if self.cell(r, 0) == ip and not self.is_done(r):
                self.table.blockSignals(True)
                self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, ip)
                self.table.blockSignals(False)
                break
        self.update_count()
        if hasattr(self, "con_pws"):                    # UEFI-Konsole: neue Adresse samt neuem Administrator anbieten
            self.console_refresh_ips()

    @staticmethod
    def compact_form(box):
        """Formular mit engen Abständen für die Kopf-Felder der Seite."""
        f = QFormLayout(box)
        f.setContentsMargins(8, 2, 8, 4)
        f.setVerticalSpacing(3)
        return f

    def apply_defaults(self):
        """Subnetzmaske/Gateway der Vorgaben in markierte (sonst alle) Zeilen der Serverliste eintragen."""
        if not self.validate_defaults():
            self.status.setText(T("Subnetzmaske oder Gateway ist ungültig."))
            return
        mask, gw = self.def_mask.text().strip(), self.def_gw.text().strip()
        if not (mask or gw):
            self.status.setText(T("Bitte zuerst Subnetzmaske und/oder Gateway oben eintragen."))
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}) or range(self.table.rowCount())
        n = 0
        for r in rows:
            if not any(self.cell(r, c) for c in range(len(i18n.COLS))):
                continue                                    # leere Zeilen auslassen, wie beim Passwort
            if mask:
                self.table.item(r, 3).setText(mask)
            if gw:
                self.table.item(r, 4).setText(gw)
            n += 1
        self.touch()
        self.status.setText(T("Subnetzmaske/Gateway in {0} Zeilen eingetragen.").format(n))

    def apply_password(self):
        for r in range(self.table.rowCount()):
            if any(self.cell(r, c) for c in range(len(i18n.COLS))):
                self.table.item(r, 1).setText(self.cur_pass.text())

    def cell(self, r, c):
        item = self.table.item(r, c)
        return item.text().strip() if item else ""

    def on_item_changed(self, item):
        """Aktuelle IP geändert: Hersteller/Typ neu ermitteln (eigene Spalte, kein Pflichtfeld)."""
        if item.column() != 0:
            return
        ip = item.text().strip()
        vitem = self.table.item(item.row(), self.vendor_col)
        if vitem:
            self.table.blockSignals(True)
            vitem.setText("")
            vitem.setToolTip(self.vendor_tip)
            self.table.blockSignals(False)
        if ipv4(ip):
            self.probe_vendor(ip)

    def probe_vendor(self, ip):
        """Startet die Hersteller-Erkennung für eine Adresse im Hintergrund (nicht blockierend)."""
        old = self.vendor_threads.get(ip)
        if old and old.isRunning():
            return      # schon unterwegs für diese Adresse
        pw = next((self.cell(r, 1) for r in range(self.table.rowCount()) if self.cell(r, 0) == ip), "")
        th = VendorProbeThread(self, ip, self.cur_user.text().strip(), pw)
        th.done.connect(self.on_vendor_probe_done)
        th.serial.connect(self.on_serial_read)
        self.vendor_threads[ip] = th
        th.start()

    # ---------- Schlüsselbund (statt Klartext-Passwörter in der Serverliste) ----------
    def vault_on_item_changed(self, item):
        """Solange der Haken „Passwörter im Schlüsselbund speichern“ gesetzt ist, wird ein von Hand
        eingetragenes oder geändertes Passwort (Spalte 1) sofort mitgesichert; ändert sich stattdessen die
        Seriennummer (Spalte 6, z. B. durch „Seriennummern auslesen“) eines Server mit Passwort, wandert der
        Eintrag vom alten auf den neuen Schlüssel (Seriennummer statt Aktuelle IP)."""
        if not getattr(self, "vault_use", None) or not self.vault_use.isChecked() or item.column() not in (1, 6):
            return
        r = item.row()
        ip, pw, serial = self.cell(r, 0), self.cell(r, 1), self.cell(r, 6)
        if item.column() == 6 and pw:
            vault.delete("", ip)        # alter Schlüssel war die IP, jetzt gilt die Seriennummer
        if pw:
            vault.store(serial, ip, pw)

    def vault_store_all_rows(self):
        """Übernimmt jedes nicht leere Passwort der Serverliste in den Schlüsselbund. Gibt die Anzahl
        übernommener Passwörter zurück (0, wenn kein Schlüsselbund verfügbar ist)."""
        return sum(1 for r in range(self.table.rowCount())
                  if self.cell(r, 1) and vault.store(self.cell(r, 6), self.cell(r, 0), self.cell(r, 1)))

    def vault_use_toggled(self, checked):
        self.vault_save_state()
        if not checked:
            return
        n = self.vault_store_all_rows()
        if n:
            self.status.setText(T('{0} Passwörter aus der Serverliste in den Schlüsselbund übernommen.')
                                .format(n))

    def vault_autofill(self):
        """Füllt leere Passwort-Felder aus dem Schlüsselbund auf (Seriennummer, sonst Aktuelle IP);
        bestehende Passwörter werden nie überschrieben. Gibt die Anzahl aufgefüllter Zeilen zurück."""
        if not vault.available():
            return 0
        n = 0
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            if self.cell(r, 1):
                continue
            ip, serial = self.cell(r, 0), self.cell(r, 6)
            found = vault.fetch(serial, ip)
            if found and self.table.item(r, 1):
                self.table.item(r, 1).setText(found)
                n += 1
        self.table.blockSignals(False)
        return n

    def vault_clear_list(self):
        rows = [(self.cell(r, 0), self.cell(r, 6)) for r in range(self.table.rowCount())
                if self.cell(r, 0) or self.cell(r, 6)]
        if not rows:
            return
        if not self.confirm(T("Schlüsselbund leeren?"),
                            T("Entfernt die Schlüsselbund-Einträge aller {0} Server dieser Liste.")
                            .format(len(rows))):
            return
        n = sum(1 for ip, serial in rows if vault.delete(serial, ip))
        self.status.setText(T('{0} Schlüsselbund-Einträge entfernt.').format(n))

    def vault_show_availability(self, later=False):
        """Haken „Schlüsselbund“ freischalten oder sperren, sobald feststeht, ob es einen gibt (die Suche
        läuft beim Start im Hintergrund, siehe vault.probe_async)."""
        if not vault.ready():
            self.vault_use.setEnabled(False)
            QTimer.singleShot(200, lambda: self.vault_show_availability(True))
            return
        self.vault_use.setEnabled(vault.available())
        if vault.available():
            self.vault_use.setToolTip(T("Beim Speichern der Liste wandern die Passwörter in den "
                                        "System-Schlüsselbund (Secret Service/libsecret, Windows-"
                                        "Anmeldeinformationsverwaltung, macOS-Schlüsselbundverwaltung) "
                                        "statt in die Datei; die Datei enthält dann nur noch Adressen, "
                                        "Hostnamen und Einstellungen. Beim Laden werden leere "
                                        "Passwort-Felder automatisch aus dem Schlüsselbund aufgefüllt "
                                        "(Zuordnung über die Seriennummer, sonst über die Aktuelle IP). "
                                        "Ebenso wird jedes von Hand eingetragene oder geänderte Passwort "
                                        "sofort mitgesichert, solange der Haken gesetzt ist."))
        else:
            self.vault_use.setToolTip(T("Kein Schlüsselbund auf diesem Rechner erreichbar (Paket "
                                        "„keyring“ fehlt, oder es läuft kein Secret-Service-Dienst, z. B. "
                                        "bei einer Sitzung ohne Desktop-Umgebung). Passwörter bleiben wie "
                                        "bisher nur in der Tabelle/CSV."))
        if later:                                   # beim Aufbau lädt __init__ den Zustand selbst
            self.vault_load_state()

    def vault_load_state(self):
        try:
            data = json.loads(i18n.VAULT_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            data = {}
        self.vault_use.setChecked(bool(data.get("use_vault", False)) and vault.available())

    def vault_save_state(self):
        try:
            i18n.VAULT_STATE.write_text(json.dumps({"use_vault": self.vault_use.isChecked()}, indent=1),
                                        encoding="utf-8")
        except OSError:
            pass

    def on_vendor_probe_done(self, ip, text, tip):
        """Trägt das Ergebnis in jede Zeile ein, deren Aktuelle IP noch mit der geprüften übereinstimmt."""
        self.vendor_threads.pop(ip, None)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            if self.cell(r, 0) == ip:
                vitem = self.table.item(r, self.vendor_col)
                if vitem:
                    vitem.setText(text)
                    vitem.setToolTip(tip)
        self.table.blockSignals(False)
        self.services_update_caps()                 # Fähigkeitsmatrix: erkannte Hersteller berücksichtigen

    def on_serial_read(self, ip, serial):
        """Ausgelesene Seriennummer in die Zeilen dieser Adresse eintragen; Eingetipptes bleibt unangetastet."""
        changed = False
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            if self.cell(r, 0) == ip and not self.cell(r, 6):
                self.table.item(r, 6).setText(serial)
                changed = True
        self.table.blockSignals(False)
        if changed:
            self.touch()

    def read_serials(self):
        """Fragt für alle Zeilen ohne Seriennummer die Seriennummer beim BMC ab (nur lesen, ein Anmeldeversuch)."""
        todo = [self.cell(r, 0) for r in range(self.table.rowCount())
                if ipv4_port(self.cell(r, 0)) and not self.cell(r, 6)]
        for ip in dict.fromkeys(todo):
            self.probe_vendor(ip)
        self.status.setText(T("Seriennummern werden ausgelesen: {0} Adresse(n). Die Spalte füllt sich, sobald die "
                              "Geräte antworten.").format(len(set(todo))) if todo else
                            T("Keine Zeile ohne Seriennummer und mit gültiger Aktueller IP."))

    def rows(self, open_only=False):
        """Alle nicht leeren Zeilen als Listen mit 6 Feldern; mit open_only ohne die fertigen."""
        out = []
        for r in range(self.table.rowCount()):
            vals = [self.cell(r, c) for c in range(len(i18n.COLS))]
            if (vals[0] or vals[2] or vals[5]) and not (open_only and self.is_done(r)):    # nur vorbelegte Felder zählen nicht
                out.append(vals)
        return out

    def update_count(self, *_):
        issues = self.validate_table()
        self.cells_missing = sum(1 for r, c, m in issues if not m)
        self.cells_bad = sum(1 for r, c, m in issues if m)
        bad = len({r for r, c, m in issues if m})
        total = len(self.rows())
        fertig = total - len(self.rows(open_only=True))
        self.count.setText(T('{0} Server').format(total)
                           + (T(" · <span style='color:#3fa45b'>{0} fertig</span>").format(fertig) if fertig else '')
                           + (T(" · <span style='color:#d9534f'>{0} mit Fehlern</span>").format(bad) if bad else ''))
        self.update_missing()

    def load(self, path, settings=False):
        """Liest eine Liste ein; mit settings=True auch die gespeicherten Felder der Maske."""
        try:
            lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as e:
            QMessageBox.warning(self, T("Liste laden"), T('Datei nicht lesbar:\n{0}').format(e))
            return False
        self.table.blockSignals(True)
        for line in lines:
            line = line.strip()
            if line.startswith("#@ ") and "=" in line:
                if not settings:
                    continue
                key, val = line[3:].split("=", 1)
                field = {"cur_user": self.cur_user, "cur_pass": self.cur_pass,
                         "new_user": self.new_user, "new_pass": self.new_pass,
                         "dhcp_from": self.dhcp_from, "dhcp_to": self.dhcp_to}.get(key)
                if field:
                    field.setText(val)
                    if key == "new_pass":
                        self.new_pass2.setText(val)
                elif key == "slot" and val.isdigit():
                    self.slot.setValue(int(val))
                elif key == "vendor":
                    self.vendor.setCurrentIndex(max(0, self.vendor.findData(val)))
                elif key == "dhcp_if":
                    for i in range(self.iface.count()):
                        if self.iface.itemData(i)[0] == val:
                            self.iface.setCurrentIndex(i)
                continue
            if not line or line.startswith("#"):
                continue
            f = [x.strip() for x in line.replace(";", ",").split(",")]     # Excel exportiert mit Semikolon
            done = False
            vendor_text = ""
            if len(f) == 9:               # 9. Spalte: Hersteller/Typ, nur Anzeige (neueres Format)
                vendor_text = f.pop()
            if len(f) == 8:              # 8. Spalte: „fertig“, Server schon umgestellt
                done = f.pop().lower() in ("fertig", "done", "1", "x")
            if len(f) == 5:              # ohne Passwort-Spalte (ältestes Format)
                f.insert(1, "")
            if len(f) == 6:              # ohne Seriennummer-Spalte (Format vor deren Einführung)
                f.append("")
            if len(f) == 7:
                self.add_row(f, vendor_text=vendor_text)
                if done and f[0]:
                    self.table.item(self.table.rowCount() - 1, 0).setData(Qt.ItemDataRole.UserRole, f[0])
        self.table.blockSignals(False)
        self._vault_autofilled = self.vault_autofill()
        self.update_count()
        return True

    def load_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, T("Liste laden"), str(HERE), T("CSV (*.csv);;Alle Dateien (*)"))
        if not path:
            return
        replace = True
        if self.rows():
            box = QMessageBox(QMessageBox.Icon.Question, T("Liste laden"),
                              T("Es sind schon Einträge vorhanden."), parent=self)
            b_replace = box.addButton(T("Ersetzen"), QMessageBox.ButtonRole.AcceptRole)
            b_append = box.addButton(T("Anhängen"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() not in (b_replace, b_append):
                return
            replace = box.clickedButton() is b_replace
        if replace:
            self.table.setRowCount(0)
        else:   # leere Zeilen am Ende entfernen
            for r in reversed(range(self.table.rowCount())):
                if not any(self.cell(r, c) for c in (0, 2, 5)):
                    self.table.removeRow(r)
        if self.load(path, settings=replace):
            if self.table.rowCount() == 0:
                self.add_row()
            self.dirty = not replace
            self.tabs.setCurrentIndex(1)
            self.status.setText(T('Geladen: {0}').format(path) + (
                T(' ({0} Passwörter aus dem Schlüsselbund aufgefüllt)').format(self._vault_autofilled)
                if self._vault_autofilled else ''))

    def save_dialog(self):
        """Fragt nach Datei und danach, ob/wie Passwörter hinein sollen. Gibt True zurück, wenn gespeichert
        wurde."""
        path, _ = QFileDialog.getSaveFileName(self, T("Liste speichern"), str(LIST), T("CSV (*.csv)"))
        if not path:
            return False
        box = QMessageBox(QMessageBox.Icon.Question, T("Liste speichern"),
                          T("Sollen die Passwörter mit in die Datei?"), parent=self)
        info = T("Ohne Passwörter enthält die Datei nur Adressen, Hostnamen und Einstellungen. "
                 "Mit Passwörtern stehen diese im Klartext in der Datei.")
        b_vault = None
        if vault.available():
            info += " " + T("Im Schlüsselbund speichern legt sie stattdessen im System-Schlüsselbund ab "
                            "(Zuordnung über Seriennummer, sonst Aktuelle IP); die Datei bleibt dann wie "
                            "„Ohne Passwörter“.")
            b_vault = box.addButton(T("Im Schlüsselbund speichern"), QMessageBox.ButtonRole.ActionRole)
        box.setInformativeText(info)
        b_without = box.addButton(T("Ohne Passwörter"), QMessageBox.ButtonRole.AcceptRole)
        b_with = box.addButton(T("Mit Passwörtern"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_vault if b_vault and self.vault_use.isChecked() else b_without)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_without, b_with, b_vault):
            return False
        try:
            self.write_file(path, with_passwords=clicked is b_with)
        except OSError as e:
            QMessageBox.warning(self, T("Liste speichern"), T('Datei nicht schreibbar:\n{0}').format(e))
            return False
        n = self.vault_store_all_rows() if clicked is b_vault else 0
        self.dirty = False
        self.status.setText(T('Gespeichert in {0}').format(path) + (
            T(" (mit Passwörtern im Klartext)") if clicked is b_with
            else T(" ({0} Passwörter im Schlüsselbund)").format(n) if clicked is b_vault
            else T(" (ohne Passwörter)")))
        return True

    def write_file(self, path, with_passwords=False):
        """Schreibt Liste und Einstellungen; Passwörter nur auf ausdrücklichen Wunsch."""
        settings = [("vendor", self.vendor.currentData() or ""), ("cur_user", self.cur_user.text()),
                    ("new_user", self.new_user.text()), ("slot", self.slot.value()),
                    ("dhcp_if", (self.iface.currentData() or [""])[0]),
                    ("dhcp_from", self.dhcp_from.text()), ("dhcp_to", self.dhcp_to.text())]
        if with_passwords:
            settings += [("cur_pass", self.cur_pass.text()), ("new_pass", self.new_pass.text())]
        text = "".join(f"#@ {k}={v}\n" for k, v in settings)
        text += "# aktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,seriennummer,fertig,hersteller\n"
        for r in range(self.table.rowCount()):
            vals = [self.cell(r, c) for c in range(len(i18n.COLS))]
            if not (vals[0] or vals[2] or vals[5]):
                continue
            if not with_passwords:
                vals[1] = ""
            text += ",".join(vals + ["fertig" if self.is_done(r) else "", self.cell(r, self.vendor_col)]) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if not WINDOWS:
            os.chmod(path, 0o600)       # nur für den eigenen Benutzer lesbar

    def validate_table(self):
        """Färbt die Zellen ein (gelb = fehlt noch, rot = fehlerhaft, grün = fertig) und gibt
        [(Zeile, Spalte, Meldung)] zurück ("" = Pflichtfeld leer)."""
        t = self.table
        t.blockSignals(True)
        rows = {}
        for r in range(t.rowCount()):
            m = re.fullmatch(r"/?(\d{1,2})", self.cell(r, 3))      # /24 oder 24 -> 255.255.255.0
            if m and int(m.group(1)) <= 32:
                t.item(r, 3).setText(str(ipaddress.IPv4Network(f"0.0.0.0/{m.group(1)}").netmask))
            vals = [self.cell(r, c) for c in range(len(i18n.COLS))]
            if vals[0] or vals[2] or vals[5]:
                rows[r] = vals
        errs = {r: row_errors(v) for r, v in rows.items()}
        for c in (0, 2, 5, 6):
            seen = {}
            for r, v in rows.items():
                key = v[c].lower()
                # die alte IP eines fertigen Servers ist frei und darf neu vergeben werden
                if key and c not in errs[r] and not (c == 0 and self.is_done(r)):
                    seen.setdefault(key, []).append(r)
            for dup in (x for x in seen.values() if len(x) > 1):
                nums = ", ".join(str(x + 1) for x in dup)
                for r in dup:
                    errs[r][c] = T('{0} kommt mehrfach vor (Zeilen {1})').format(i18n.COLS[c], nums)
        # die neue IP darf nicht die aktuelle Adresse eines anderen, noch offenen Geräts sein
        current = {v[0]: r for r, v in rows.items() if v[0] and not self.is_done(r)}
        for r, v in rows.items():
            other = current.get(v[2])
            if other is not None and other != r and 2 not in errs[r] and not self.is_done(r):
                errs[r][2] = T('Diese Adresse ist die aktuelle IP von Zeile {0}').format(other + 1)
        empty_tip = "\n\n" + T("Pflichtfeld, noch leer.")
        out = []
        for r in range(t.rowCount()):
            done = self.is_done(r)
            for c in range(len(i18n.COLS)):
                item = t.item(r, c)
                if not item:
                    continue
                msg = errs.get(r, {}).get(c)
                if msg is not None:
                    item.setBackground(ERR_BG if msg else MISS_BG)
                    item.setToolTip(msg if msg else i18n.COL_TIPS[c] + empty_tip)
                    out.append((r, c, msg))
                elif not rows and c not in (1, 6) and not item.text().strip():
                    item.setBackground(MISS_BG)         # Liste noch ganz leer: zeigen, wo es losgeht
                    item.setToolTip(i18n.COL_TIPS[c] + empty_tip)
                elif done:
                    item.setBackground(DONE_BG)
                    item.setToolTip(T("Fertig umgestellt, wird beim nächsten Start übersprungen.\n"
                                    "Soll das Gerät noch einmal bearbeitet werden: Zeile markieren und "
                                    "„Erneut bearbeiten“ klicken."))
                else:
                    item.setBackground(QBrush())
                    item.setToolTip(i18n.COL_TIPS[c])
            vitem = t.item(r, self.vendor_col)
            if vitem:
                vitem.setBackground(DONE_BG if done else QBrush())
        t.blockSignals(False)
        return out

    def check(self):
        """Prüft alle Eingaben vor einem Lauf. Gibt eine Fehlermeldung zurück oder None."""
        if not self.new_user.text() or not self.new_pass.text():
            return T("Neuen Benutzer und Passwort ausfüllen.")
        if not USER_RE.match(self.new_user.text()):
            return (T("Neuer Benutzer: nur Buchstaben, Ziffern, Punkt, Bindestrich und Unterstrich, "
                    "höchstens 16 Zeichen."))
        if len(self.new_pass.text()) < 8:
            return T("Neues Passwort: mindestens 8 Zeichen. Kürzere lehnen die meisten BMCs ab.")
        if self.new_pass2_chk.isChecked() and self.new_pass.text() != self.new_pass2.text():
            return T("Neues Passwort: Die beiden Eingaben stimmen nicht überein.")
        if not self.rows():
            return T("Die Serverliste ist leer.")
        errs = self.validate_table()
        if errs:
            r, c, msg = errs[0]
            self.table.setCurrentCell(r, c)
            if not msg:
                msg = T("fehlt")
            text = T('Zeile {0}, {1}: {2}').format(r + 1, i18n.COLS[c], msg)
            if len(errs) > 1:
                text += "\n\n" + T('({0} weitere markierte Felder)').format(len(errs) - 1)
            return text
        own = {str(i.ip) for i in self.local_ifaces()}
        pool = None
        if self.dhcp_running():
            pool = ipv4(self.dhcp_from.text().strip()), ipv4(self.dhcp_to.text().strip())
        for r in range(self.table.rowCount()):
            if self.is_done(r) or not (self.cell(r, 0) or self.cell(r, 2)):
                continue
            if not self.cell(r, 1) and not self.cur_pass.text():
                return T('Zeile {0}: kein Passwort (weder in der Zeile noch oben).').format(r + 1)
            new = ipv4(self.cell(r, 2))
            if new and str(new) in own:
                return T('Zeile {0}: Die neue IP ist eine Adresse dieses Rechners.').format(r + 1)
            if new and pool and pool[0] and pool[1] and pool[0] <= new <= pool[1]:
                return (T('Zeile {0}: Die neue IP liegt im Bereich des laufenden DHCP-Servers ({1} bis {2}). Dort könnte sie ein zweites Mal vergeben werden.').format(r + 1, pool[0], pool[1]))
        return None

    def running(self):
        return (bool(self.worker) and self.worker.isRunning()) \
            or (bool(getattr(self, "bios_worker", None)) and self.bios_worker.isRunning()) \
            or (bool(getattr(self, "svc_worker", None)) and self.svc_worker.isRunning())

    def open_scanner(self):
        if getattr(self, "scanner", None) is None:
            self.scanner = ScannerDialog(self)
        self.scanner.show()
        self.scanner.raise_()

    def scan_to_row(self, value, col=1):
        """Trägt einen vom Handy gelesenen Wert in die gewählte Spalte (Scanner-Fenster, Standard: Passwort)
        ein: markierte Zeile, sonst die erste offene, in der genau diese Spalte noch leer ist. Gibt die
        Zeilennummer (ab 1) zurück, 0 wenn nichts frei war. Danach rückt die Markierung vor."""
        n = self.table.rowCount()
        r = self.table.currentRow()
        if not 0 <= r < n or self.is_done(r):
            r = next((i for i in range(n) if not self.is_done(i) and not self.cell(i, col)), -1)
        if r < 0:
            return 0
        item = self.table.item(r, col)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(r, col, item)
        item.setText(value)
        if r + 1 < n:
            self.table.setCurrentCell(r + 1, col)
        return r + 1

    def closeEvent(self, event):
        if getattr(self, "switching", False):   # Sprachwechsel: das neue Fenster hat alles übernommen
            self.stop_scanner()
            event.accept()
            return
        LOG.debug(T("Fenster wird geschlossen"))
        if self.running():
            QMessageBox.information(self, T("Lauf aktiv"), T("Es läuft noch eine Konfiguration. "
                                    "Bitte warten oder erst abbrechen."))
            event.ignore()
            return
        if self.dirty and self.rows():
            box = QMessageBox(QMessageBox.Icon.Question, T("Beenden"),
                              T("Die Serverliste wurde nicht gespeichert."), parent=self)
            box.setInformativeText(T("Das Tool speichert nichts von selbst. Ohne Speichern ist die Liste "
                                   "nach dem Schließen weg."))
            b_save = box.addButton(T("Speichern …"), QMessageBox.ButtonRole.AcceptRole)
            b_drop = box.addButton(T("Verwerfen"), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is b_save:
                if not self.save_dialog():
                    event.ignore()
                    return
            elif box.clickedButton() is not b_drop:
                event.ignore()
                return
        if not self.planner_confirm_discard():
            event.ignore()
            return
        self.stop_scanner()
        self.stop_dhcp()
        self.console_shutdown()
        self.vbmc_shutdown()
        for th in list(self.vendor_threads.values()):      # kurze Gnadenfrist, sonst Warnung/Absturz beim Beenden
            th.wait(300)
        event.accept()

    def stop_scanner(self):
        """Scanner-Server und Hotspot sicher beenden, Fehler dabei nie nach aussen lassen."""
        sc = getattr(self, "scanner", None)
        if sc is not None:
            try:
                sc.stop_server()
                sc.close()
            except Exception:
                pass

    # ---------- UEFI/BIOS-Einstellungen ----------
    def row_login(self, ip):
        """(Benutzer, Passwort) für eine Adresse aus der Serverliste. Eine fertig umgestellte (grüne) Zeile kennt
        nur noch den neuen Benutzer mit dem neuen Passwort (oben eingetragen); sonst gilt das Passwort der Zeile
        mit dem Werksbenutzer (Benutzer leer = automatisch je Hersteller)."""
        for r in range(self.table.rowCount()):
            vals = [self.cell(r, c) for c in range(len(i18n.COLS))]
            done = self.is_done(r)
            if (vals[2] if done else vals[0]) == ip:
                nu, np_ = self.new_user.text().strip(), self.new_pass.text()
                if done and nu and np_:
                    return nu, np_
                return "", vals[1]
        return "", ""

    def reachable_rows(self):
        """(Adresse, Passwort) je Zeile; fertig umgestellte (grüne) Zeilen sind jetzt unter der neuen IP erreichbar."""
        out = []
        for r in range(self.table.rowCount()):
            vals = [self.cell(r, c) for c in range(len(i18n.COLS))]
            if vals[0] or vals[2] or vals[5]:
                ip = vals[2] if self.is_done(r) else vals[0]
                if ip:
                    out.append((ip, vals[1]))
        return out

    # ---------- Ablauf ----------
    def run(self):
        self.table.setCurrentCell(-1, -1)      # laufende Zellbearbeitung abschließen
        err = self.check()
        if err:
            QMessageBox.warning(self, T("Eingabe prüfen"), err)
            return
        # Seriennummer (Spalte 7) ist reine Notiz und geht nicht an den BMC: nur die ersten 6 Felder
        # weiterreichen, auf die Engine.run() angewiesen ist.
        todo = [v[:6] for v in self.rows(open_only=True)]       # fertige (grüne) Zeilen werden übersprungen
        if not todo:
            QMessageBox.information(self, T("Nichts zu tun"), T("Alle Server der Liste sind schon umgestellt.\n\n"
                                    "Neue Zeilen eintragen."))
            return
        n = len(todo)
        dry = self.dry.isChecked()
        if not dry:
            nets = [i.network for i in self.local_ifaces()]
            lines, far = [], 0
            for cur, _, new, _, _, host in todo:
                cur_ip = ipv4_port(cur)      # Testlabor-Adressen haben Form IP:Port
                off = cur_ip is None or not any(cur_ip in net for net in nets)
                far += off
                lines.append(f"{cur} → {new} ({host})" + ("  ⚠" if off else ""))
            info = "\n".join(lines[:15])
            if n > 15:
                info += "\n" + T('… und {0} weitere').format(n - 15)
            if far:
                info += "\n\n" + (T("⚠ Die aktuelle IP liegt in keinem Netz, an dem dieser Rechner direkt hängt. "
                                  "Für ein Staging-Gerät ist das ungewöhnlich. Ist es wirklich das richtige Gerät?"))
            if self.lock_factory.isChecked():
                info += "\n\n" + T("Nach erfolgreicher Kontrolle wird auf jedem Server das Werkskonto gesperrt. Danach "
                                    "gilt nur noch der neue Administrator – Passwort vorher sicher ablegen.")
            if not self.confirm(T("Scharf ausführen?"),
                                T('{0} Server werden jetzt wirklich umgestellt (Benutzer, Hostname, feste IP).').format(n),
                                info, n if far else None):
                return

        skipped = len(self.rows()) - n
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.say(T('— {0}, {1} Server').format(mode, n) + (T(', {0} fertige übersprungen').format(skipped) if skipped else "") + " —", "gray")
        self.worker = EngineThread(
            self, rows=todo, cur_user=self.cur_user.text().strip(), cur_pass=self.cur_pass.text(),
            new_user=self.new_user.text(), new_pass=self.new_pass.text(), slot=self.slot.value(), dry=dry,
            vendor=self.vendor.currentData() or None, lock_factory=self.lock_factory.isChecked())
        self.worker.line.connect(self.show_line)
        self.worker.row_done.connect(self.mark_done)
        self.worker.result.connect(self.done)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.table.setEnabled(False)
        self.status.setText(T("Läuft …"))
        self.worker.start()

    def show_line(self, line, kind=""):
        self.say(line, {"err": "#d9534f", "ok": "#3fa45b", "warn": "#e08e0b"}.get(kind), bold=kind == "head")

    def abort(self):
        if self.running():
            self.worker.requestInterruption()
            self.stop.setEnabled(False)
            self.status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def done(self, code, *_):
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        self.table.setEnabled(True)
        self.status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))
        if code == 0 and not self.dry.isChecked() and self.clear_pass_after.isChecked():
            self.cur_pass.clear()
            self.new_pass.clear()
            self.new_pass2.clear()
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 1)
                if item:
                    item.setText("")

    # ---------- Zertifikate und Datenordner ----------
    def open_data_dir(self):
        path = i18n.private_dir(i18n.DATA)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_restore_dialog(self):
        """Öffnet den Sicherungs-Browser. Wählt der Benutzer „Als neue Zeile eintragen",
        wird in der Neuware-Tabelle eine vorbefüllte Zeile angelegt."""
        dlg = RestoreDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg._selected_data
        if not data:
            return
        from .i18n import COLS
        n_cols = len(COLS)
        # Neue Zeile anlegen; Spalten: 0=aktuelle IP, 2=neue IP, 3=Subnetz, 4=Gateway, 5=Hostname
        self.add_row([""] * n_cols)
        r = self.table.rowCount() - 1
        field_map = {0: data.get("cur_ip", ""), 2: data.get("new_ip", ""),
                     3: data.get("mask", ""), 4: data.get("gw", ""), 5: data.get("host", "")}
        for col, val in field_map.items():
            if val:
                item = self.table.item(r, col)
                if item:
                    item.setText(val)
        # Hersteller in Spalte 7 (falls vorhanden und COLS gross genug)
        if n_cols > 7 and data.get("vendor"):
            item = self.table.item(r, 7)
            if item:
                item.setText(data["vendor"])
        self.table.scrollToItem(self.table.item(r, 0))
        vendor_key = data.get("vendor", "")
        if vendor_key:
            idx = self.vendor.findData(vendor_key)
            if idx >= 0:
                self.vendor.setCurrentIndex(idx)
        self.tabs.setCurrentWidget(self.tabs.widget(1))   # Neuware-Reiter
        self.touch()

    def edit_pins(self):
        st = trust.store()
        st.load()
        dlg = QDialog(self)
        dlg.setWindowTitle(T("Zertifikate"))
        dlg.resize(760, 420)
        lay = QVBoxLayout(dlg)
        info = QLabel(T("BMC-Steward merkt sich beim ersten Kontakt das Zertifikat jedes BMC. Zeigt dasselbe Gerät "
                        "später ein anderes, wird es gesperrt, bevor ein Passwort gesendet wird. War der Wechsel "
                        "gewollt (neues Zertifikat, Mainboard-Tausch, Werksreset), den Eintrag hier entfernen; beim "
                        "nächsten Kontakt wird das neue Zertifikat gemerkt. Den Fingerabdruck vorher mit der "
                        "Anzeige in der BMC-Oberfläche vergleichen."))
        info.setWordWrap(True)
        lay.addWidget(info)
        filt = QLineEdit()
        filt.setPlaceholderText(T("Suchen (Adresse, Kennung, Fingerabdruck)"))
        lay.addWidget(filt)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels([T("Adresse"), T("Gerät"), T("Fingerabdruck (SHA-256)"), T("Zuletzt gesehen")])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(table, 1)

        def fill():
            needle = filt.text().strip().lower()
            items = sorted(st.pins.items(), key=lambda kv: kv[1].get("ip", ""))
            items = [(k, v) for k, v in items
                     if not needle or needle in (k + " " + v.get("ip", "") + " " + v.get("fp", "")).lower()]
            table.setRowCount(len(items))
            for r, (k, v) in enumerate(items):
                cells = [v.get("ip", ""), k.split(":", 1)[-1], trust.short(v.get("fp")), v.get("seen", "")]
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    it.setData(Qt.ItemDataRole.UserRole, k)
                    it.setToolTip(trust.pretty(v.get("fp")) + (
                        "\n" + T("vorher: {0}").format(trust.pretty(v["previous"])) if v.get("previous") else ""))
                    table.setItem(r, c, it)
        filt.textChanged.connect(fill)
        fill()
        row = QHBoxLayout()
        b_del = QPushButton(T("Markierte entfernen"))
        b_close = QPushButton(T("Schließen"))
        row.addWidget(b_del)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)

        def remove():
            keys = {table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole) for i in table.selectedIndexes()}
            if not keys:
                return
            if QMessageBox.question(self, T("Zertifikate"), T("{0} Einträge entfernen? Beim nächsten Kontakt wird das "
                                    "dann gezeigte Zertifikat ungeprüft übernommen.").format(len(keys))) \
                    != QMessageBox.StandardButton.Yes:
                return
            for k in keys:
                st.forget(k)
            fill()
        b_del.clicked.connect(remove)
        b_close.clicked.connect(dlg.accept)
        dlg.exec()

    # ---------- Eigenschutz ----------
    def update_guard_btn(self):
        g = selfguard.get()
        if g.unprotected_vm:
            self.guard_btn.setText(T("⚠ Eigenschutz"))
            self.guard_btn.setStyleSheet("color:#d9534f;font-weight:bold;")
            self.guard_btn.setToolTip(T("BMC-Steward läuft in einer VM ({0}). Den Host dieser VM in die "
                                        "Schutzliste eintragen, sonst kann er versehentlich neu gestartet "
                                        "werden.").format(g.virt))
        elif not g.own_ready:             # eigene Kennung wird noch im Hintergrund gelesen
            self.guard_btn.setText(T("Eigenschutz ({0})").format(len(g.entries)))
            self.guard_btn.setStyleSheet("")
            self.guard_btn.setToolTip(T("Server, die BMC-Steward nie neu startet oder mit Firmware bespielt."))
            QTimer.singleShot(250, self.update_guard_btn)
        else:
            self.guard_btn.setText(T("Eigenschutz ({0})").format(len(g.entries) + len(g.own)))
            self.guard_btn.setStyleSheet("")
            self.guard_btn.setToolTip(T("Server, die BMC-Steward nie neu startet oder mit Firmware bespielt."))

    def selfguard_note(self):
        """Zusatztext für Sicherheitsabfragen, wenn der eigene Host nicht erkannt werden kann."""
        g = selfguard.get()
        if not g.unprotected_vm:
            return ""
        return "\n\n" + T("ACHTUNG: BMC-Steward läuft in einer VM ({0}) und die Schutzliste ist leer. Steht "
                           "der Host dieser VM in der Liste, schaltet sich das Tool mitten im Lauf selbst ab. "
                           "Vorher unter „Eigenschutz“ eintragen.").format(g.virt)

    def edit_selfguard(self):
        g = selfguard.get()
        g.refresh()
        dlg = QDialog(self)
        dlg.setWindowTitle(T("Eigenschutz"))
        dlg.resize(560, 460)
        lay = QVBoxLayout(dlg)
        if g.virt:
            where = T("BMC-Steward läuft in einer virtuellen Maschine ({0}). Die VM kennt ihren Host nicht: "
                      "Tragen Sie hier alle Hosts ein, auf denen diese VM laufen kann (z. B. den ganzen "
                      "Management-Cluster).").format(g.virt)
        elif g.own:
            where = T("BMC-Steward läuft auf echter Hardware; die eigene Kennung wurde erkannt und ist "
                      "automatisch geschützt ({0} Werte).").format(len(g.own))
        else:
            where = T("Eigene Kennung nicht lesbar (unter Linux braucht das Root-Rechte). Läuft BMC-Steward "
                      "auf einem Server, dessen BMC in der Liste stehen könnte, diesen hier eintragen.")
        info = QLabel(where + "\n\n" + T(
            "Je Zeile ein Eintrag: Seriennummer bzw. Service-Tag, System-UUID oder BMC-Adresse. Diese Server "
            "werden nie neu gestartet, bekommen keine UEFI-Änderungen und keine Firmware. Im scharfen Lauf "
            "wird außerdem jeder Server übersprungen, dessen Identität nicht lesbar ist."))
        info.setWordWrap(True)
        lay.addWidget(info)
        edit = QPlainTextEdit("\n".join(g.entries))
        edit.setPlaceholderText("ABC1234\n4c4c4544-0042-3510-8048-b2c04f593132\n10.20.0.11")
        lay.addWidget(edit, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b_save, b_cancel = QPushButton(T("Speichern")), QPushButton(T("Abbrechen"))
        row.addWidget(b_save)
        row.addWidget(b_cancel)
        lay.addLayout(row)
        b_cancel.clicked.connect(dlg.reject)
        b_save.clicked.connect(dlg.accept)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            g.save(edit.toPlainText().splitlines())
        except OSError as e:
            QMessageBox.warning(self, T("Eigenschutz"), T('Datei nicht schreibbar:\n{0}').format(e))
        self.update_guard_btn()

    def confirm(self, title, text, info="", type_count=None):
        """Sicherheitsabfrage, bei der „Abbrechen“ vorausgewählt ist. Mit type_count muss zusätzlich
        diese Zahl eingetippt werden."""
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        if info:
            box.setInformativeText(info)
        b_yes = box.addButton(T("Ja"), QMessageBox.ButtonRole.YesRole)
        b_no = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_no)
        box.setEscapeButton(b_no)
        box.exec()
        if box.clickedButton() is not b_yes:
            return False
        if type_count is None:
            return True
        typed, good = QInputDialog.getText(
            self, title, T("Zur Bestätigung die Anzahl der betroffenen Server eintippen:"))
        if good and typed.strip() == str(type_count):
            return True
        if good:
            QMessageBox.information(self, title, T("Die Zahl stimmt nicht. Es wurde nichts ausgeführt."))
        return False

    @staticmethod
    def local_ifaces():
        """IPv4-Adressen dieses Rechners samt Netz (ohne Loopback)."""
        out = []
        for nic in QNetworkInterface.allInterfaces():
            for e in nic.addressEntries():
                if e.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol and not e.ip().isLoopback():
                    try:
                        out.append(ipaddress.ip_interface(f"{e.ip().toString()}/{e.prefixLength()}"))
                    except ValueError:
                        pass
        return out

    @staticmethod
    def default_route_ip():
        """Eigene Adresse der Karte, über die die Standardroute läuft, oder None. Es wird nichts gesendet."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))         # Testnetz-Adresse; UDP-connect wählt nur die Route
            return s.getsockname()[0]
        except OSError:
            return None
        finally:
            s.close()

    def reopen_rows(self):
        """Nimmt bei den markierten Zeilen die grüne Fertig-Markierung zurück."""
        self.table.blockSignals(True)
        for r in {i.row() for i in self.table.selectedIndexes()}:
            if self.table.item(r, 0):
                self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, None)
        self.table.blockSignals(False)
        self.update_count()

    STATE_FIELDS = ("cur_user", "cur_pass", "new_user", "new_pass", "new_pass2", "def_mask", "def_gw", "dhcp_from", "dhcp_to",
                    "bios_user", "bios_pass")

    def export_state(self):
        """Alles, was beim Sprachwechsel ins neu aufgebaute Fenster mitgenommen wird."""
        return {
            "geometry": self.saveGeometry(), "tab": self.tabs.currentIndex(), "dirty": self.dirty,
            "profile": self.current_profile(), "vendor": self.vendor.currentData() or "",
            "slot": self.slot.value(), "iface": (self.iface.currentData() or [None])[0],
            "texts": {k: getattr(self, k).text() for k in self.STATE_FIELDS},
            "new_pass2_chk": self.new_pass2_chk.isChecked(),
            "lock_factory": self.lock_factory.isChecked(),
            "clear_pass_after": self.clear_pass_after.isChecked(),
            "auto_take": self.auto_take.isChecked(), "lease_taken": sorted(self.lease_taken),
            "rows": [([self.cell(r, c) for c in range(len(i18n.COLS))], self.is_done(r))
                     for r in range(self.table.rowCount())],
            "bios_profile": self.bios_current_profile(), "bios_vendor": self.bios_vendor.currentData() or "",
            "bios_ref": self.bios_ref.currentText(), "bios_live": dict(self.bios_live),
            "bios_new": self.bios_wanted(), "bios_pending": dict(self.bios_pending),
            "services": self.export_services(),
            "planner": self.plan.to_dict(), "planner_path": self.plan_path, "planner_dirty": self.plan_dirty,
        }

    def import_state(self, s):
        self.restoreGeometry(s["geometry"])
        self.profile.setCurrentIndex(max(0, self.profile.findText(s["profile"] or i18n.NO_PROFILE)))
        self.vendor.setCurrentIndex(max(0, self.vendor.findData(s["vendor"])))
        self.slot.setValue(s["slot"])
        for i in range(self.iface.count()):
            if self.iface.itemData(i)[0] == s["iface"]:
                self.iface.setCurrentIndex(i)
        for key, text in s["texts"].items():
            getattr(self, key).setText(text)
        self.new_pass2_chk.setChecked(s.get("new_pass2_chk", False))
        self.lock_factory.setChecked(s.get("lock_factory", True))
        self.clear_pass_after.setChecked(s.get("clear_pass_after", True))
        self.auto_take.setChecked(s.get("auto_take", False))
        self.lease_taken = set(s.get("lease_taken", []))
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for vals, done in s["rows"]:
            self.add_row(vals)
            if done:
                self.table.item(self.table.rowCount() - 1, 0).setData(Qt.ItemDataRole.UserRole, vals[0])
        self.table.blockSignals(False)
        self.bios_profile.setCurrentIndex(max(0, self.bios_profile.findText(s["bios_profile"] or i18n.NO_PROFILE)))
        self.bios_vendor.setCurrentIndex(max(0, self.bios_vendor.findData(s["bios_vendor"])))
        self.bios_live = s["bios_live"]
        self.bios_pending = dict(s["bios_pending"], **s["bios_new"])
        self.bios_fill_table(keep_values=False)
        self.bios_pending = s["bios_pending"]
        self.bios_refresh_ips()
        self.bios_ref.setEditText(s["bios_ref"])
        self.import_services(s["services"])
        if s.get("planner"):
            from .planner import Plan
            self.planner_set_plan(Plan.from_dict(s["planner"]), dirty=s.get("planner_dirty", False))
            self.plan_path = s.get("planner_path")
        self.validate_defaults()
        self.validate_range()
        self.update_count()
        self.tabs.setCurrentIndex(s["tab"])
        self.dirty = s["dirty"]

    def switch_language(self, *_):
        """Baut das Fenster in der gewählten Sprache neu auf; Eingaben und Serverliste bleiben erhalten."""
        global MAIN
        code = self.lang.currentData()
        if code == i18n.LANG:
            return
        if self.running() or self.dhcp_running() or (self.bios_reader and self.bios_reader.isRunning()) \
                or self.console_active():
            self.lang.setCurrentIndex(self.lang.findData(i18n.LANG))
            QMessageBox.information(self, T("Sprache"), T("Die Sprache lässt sich nur wechseln, solange kein Lauf "
                                    "aktiv ist und der DHCP-Server gestoppt ist."))
            return
        self.table.setCurrentCell(-1, -1)
        state = self.export_state()
        set_language(code)
        self.write_profiles()                   # merkt sich die Sprache für den nächsten Start
        MAIN = Window(state)
        MAIN.show()
        self.switching = True
        self.close()
        self.deleteLater()

    def update_missing(self, *_):
        """Hebt Pflichtfelder hervor, die noch leer (gelb) oder fehlerhaft (rot) sind, und schreibt über
        den Start-Knopf, was vor dem Lauf noch fehlt."""
        if not hasattr(self, "todo"):
            return
        user, pw, pw2 = self.new_user.text(), self.new_pass.text(), self.new_pass2.text()
        open_rows = self.rows(open_only=True)
        marks = {
            self.new_user: "miss" if not user else "" if USER_RE.match(user) else "err",
            self.new_pass: "miss" if not pw else "" if len(pw) >= 8 else "err",
            self.new_pass2: (("miss" if not pw2 else "" if pw2 == pw else "err")
                              if self.new_pass2_chk.isChecked() else ""),
            self.cur_pass: "miss" if not self.cur_pass.text() and any(not v[1] for v in open_rows) else "",
        }
        for w, mark in marks.items():
            w.setStyleSheet({"miss": MISS_CSS, "err": ERR_CSS}.get(mark, ""))
        todo = []
        if marks[self.new_user]:
            todo.append(T("Neuer Administrator: Benutzer"))
        if marks[self.new_pass] or marks[self.new_pass2]:
            todo.append(T("Neuer Administrator: Passwort"))
        if marks[self.cur_pass]:
            todo.append(T("Werkspasswort"))
        if not self.rows():
            todo.append(T("mindestens ein Server in der Liste"))
        if self.cells_missing:
            todo.append(T("{0} leere Pflichtfelder in der Serverliste").format(self.cells_missing))
        if self.cells_bad:
            todo.append(T("{0} fehlerhafte Felder in der Serverliste").format(self.cells_bad))
        if todo:
            self.todo.setText("<span style='background:#f0ad4e; color:#000000'>&nbsp;" + T("Es fehlt noch:")
                              + "&nbsp;</span> " + html.escape(", ".join(todo)))
            return
        if not open_rows:
            text = T("Alle Server der Liste sind fertig.")
        elif self.dry.isChecked():
            text = T("Alles ausgefüllt. Jetzt den Trockenlauf starten.")
        else:
            text = T("Alles ausgefüllt. Der nächste Start stellt die Geräte wirklich um.")
        self.todo.setText("<span style='background:#3fa45b; color:#ffffff'>&nbsp;OK&nbsp;</span> " + html.escape(text))

    def mode_changed(self, *_):
        """Der Start-Knopf sagt immer, was er tun wird; scharf ist er rot."""
        dry = self.dry.isChecked()
        self.start.setText(T("Trockenlauf starten") if dry else T("Scharf starten"))
        self.start.setStyleSheet("" if dry else LIVE_CSS)
        self.update_missing()

    def bios_mode_changed(self, *_):
        dry = self.bios_dry.isChecked()
        self.bios_start.setText(T("Trockenlauf für alle Server der Liste") if dry
                                else T("Scharf auf allen Servern der Liste setzen"))
        self.bios_start.setStyleSheet("" if dry else LIVE_CSS)

    def open_log(self):
        """Öffnet das gemeinsame Protokoll als Textdatei im Standard-Editor."""
        applog.open_in_editor(self)


def _excepthook(kind, err, tb):
    """PyQt6 bricht bei unbehandelten Fehlern in Slots ab; stattdessen nur melden."""
    import traceback
    traceback.print_exception(kind, err, tb)
    try:        # unter Windows gibt es keine Konsole: Fehler auch ins Protokoll, sonst bleibt ein Klick wirkungslos
        LOG.error("Interner Fehler: " + "".join(traceback.format_exception(kind, err, tb)).strip())
    except Exception:
        pass

def run():
    set_language(load_lang())
    sys.excepthook = _excepthook
    # Chromium (QtWebEngine) meldet bei jedem BMC mit selbstsigniertem Zertifikat einen fehlgeschlagenen
    # Handshake (net_error -202), obwohl BmcPage das Zertifikat danach akzeptiert. Das flutet die Konsole;
    # nur fatale Chromium-Meldungen anzeigen. Wer alles sehen will, setzt QTWEBENGINE_CHROMIUM_FLAGS selbst.
    if "QTWEBENGINE_CHROMIUM_FLAGS" not in os.environ:
        flags = "--log-level=3"
        if selfguard.get().virt:
            # In VMs (VMware, Proxmox, Hyper-V) gibt es meist keine echte GPU: Chromium zeichnet sonst die
            # Web-Konsole gelegentlich schwarz. Software-Zeichnen ist dort zuverlässiger.
            flags += " --disable-gpu --disable-gpu-compositing"
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
    if selfguard.get().virt and "QT_QUICK_BACKEND" not in os.environ:
        os.environ["QT_QUICK_BACKEND"] = "software"
    # Linux: Ohne installierte .desktop-Datei meldet das Desktop-Portal beim Start „App info not found“. Das ist
    # harmlos (nur die Anmeldung beim Portal entfällt), verwirrt aber; deshalb nur diese eine Meldung ausblenden.
    from PyQt6.QtCore import QLoggingCategory
    QLoggingCategory.setFilterRules("qt.qpa.services.warning=false")
    app = QApplication(sys.argv)
    theme.apply(app, sheet=False)             # hell/dunkel, automatisch nach System; Stylesheet nach dem Aufbau
    theme.follow_system(app)
    LOG.info(T("BMC-Steward {0} gestartet").format(__version__), bold=True)
    LOG.info(T("Sprache: {0}").format(i18n.LANG))
    LOG.info(T("Trockenlauf ist voreingestellt: Es wird erst etwas geändert, wenn „Scharf starten“ gedrückt wird."))
    LOG.debug(T("System: {0} · Python {1} · Qt {2}").format(
        f"{platform.system()} {platform.release()} {platform.machine()}",   # platform.platform() startet uname
        platform.python_version(), QT_VERSION_STR))
    LOG.debug(T("Datenordner: {0}").format(i18n.DATA))
    LOG.debug(T("Eigener Rechner: {0}").format(selfguard.get().virt or T("echte Hardware")))
    app.setApplicationName("bmc-steward")
    app.setApplicationDisplayName("BMC-Steward")
    app.setDesktopFileName("bmc-steward")
    app.setWindowIcon(app_icon())
    global MAIN
    MAIN = Window()
    app.setStyleSheet(theme.stylesheet(theme.current()))   # einmal für das fertige Fenster statt je Seite
    MAIN.show()
    # Bedienung protokollieren erst jetzt: Vorher kann niemand klicken, und der Filter müsste sonst die
    # zehntausenden Ereignisse des Fensteraufbaus durch Python schleusen (spürbar beim Start).
    applog.install(app)
    app.aboutToQuit.connect(lambda: MAIN.stop_scanner())
    code = app.exec()
    MAIN.stop_scanner()
    LOG.info(T("BMC-Steward beendet"))
    return code


if __name__ == "__main__":
    raise SystemExit(run())

