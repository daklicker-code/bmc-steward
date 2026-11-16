"""BMC-Steward: Hauptfenster (alle Reiter) und Programmstart."""
from pathlib import Path
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QStyledItemDelegate, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPixmap)
from PyQt6.QtCore import (QT_VERSION_STR, QBuffer, QByteArray, QEvent, QIODevice, QLocale, QObject, QProcess, QRect, QSocketNotifier, QThread, QTimer, QUrl, Qt, pyqtSignal)
import html
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
from .i18n import BIOS_PROFILES, HELP, HERE, LANGS, LIST, PROFILES, T, load_lang, set_language
from .core import ABOUT, DONE_BG, ERR_BG, ERR_CSS, LIVE_CSS, MISS_BG, MISS_CSS, USER_RE, WINDOWS, __version__, ipv4, ipv4_port, mask, netmask, row_errors
from .branding import app_icon
from .monitor import AnimatedLogo
from .redfish import BiosEngine, BiosEngineThread, BiosReadThread, Engine, EngineThread, FirmwareEngineThread, FirmwareReadThread, ProbeEngine, ProbeThread, ServicesEngine, ServicesEngineThread, VendorProbeThread
from .dhcp import DhcpThread, probe_dhcp_servers, stop_leftover_dhcp
from .console import ConsoleMixin
from .sidenav import SideNav
from .firmware_tab import FirmwareTabMixin
from .vbmc_tab import VbmcTabMixin
from .scanner import ScannerDialog
from . import selfguard, trust


DHCP_YELLOW = "#c9a100"      # Menüpunkt „DHCP-Server“ als dunkelgelber Knopf
MAIN = None         # das Hauptfenster; beim Sprachwechsel wird es neu aufgebaut




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




class Window(ConsoleMixin, FirmwareTabMixin, VbmcTabMixin, QWidget):
    def __init__(self, state=None):
        super().__init__()
        self.setWindowTitle(f"BMC-Steward {__version__} · Dell iDRAC / HPE iLO / Supermicro / Lenovo XCC")
        self.setWindowIcon(app_icon())
        self.resize(1200, 800)
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
        f = QFormLayout(box_cur)
        self.tip_row(f, T("Hersteller"), self.vendor,
                     T("„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller, gemischte Listen sind möglich.\n"
                     "Eine feste Wahl überspringt die Erkennung und gilt für alle Zeilen. Nützlich, wenn die\n"
                     "Erkennung danebenliegt oder der Trockenlauf den richtigen Ablauf zeigen soll."))
        self.version_hint = QLabel()
        self.version_hint.setWordWrap(True)
        f.addRow("", self.version_hint)
        self.tip_row(f, T("Benutzer"), self.cur_user,
                     T("Benutzer, mit dem sich das Tool jetzt am BMC anmeldet, also der Werksbenutzer.\n"
                     "Leer lassen: Dann wird je Hersteller root, Administrator oder ADMIN verwendet."))
        f.addRow("", self.user_hint)
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
        f = QFormLayout(box_new)
        self.tip_row(f, T("Benutzer"), self.new_user,
                     T("Name des Administrator-Kontos, das auf allen Geräten angelegt wird.\n"
                     "Existiert der Name bei HPE, Supermicro oder Lenovo schon, werden nur Passwort und Rolle gesetzt."))
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
        self.tip_row(f, T("User-Slot (nur Dell)"), self.slot,
                     T("Nur Dell: Der iDRAC hat 16 feste Benutzerplätze, Platz 2 ist root.\n"
                     "Der neue Administrator wird in diesen Platz geschrieben. Ist der Platz schon\n"
                     "von einem anderen Benutzer belegt, wird nichts überschrieben. Standard ist Platz 3."))

        # --- Vorgaben für neue Zeilen ---
        self.def_mask = QLineEdit()
        self.def_mask.setPlaceholderText(T("z. B. 255.255.255.0 oder /24"))
        self.def_gw = QLineEdit()
        self.def_gw.setPlaceholderText(T("z. B. 10.20.0.1"))
        for w in (self.def_mask, self.def_gw):
            w.editingFinished.connect(self.validate_defaults)
        box_def = QGroupBox(T("Vorgaben für neue Zeilen"))
        f = QFormLayout(box_def)
        self.tip_row(f, T("Subnetzmaske"), self.def_mask,
                     T("Wird bei jeder neuen Zeile der Serverliste vorbelegt, auch bei Geräten aus der DHCP-Liste.\n"
                     "Leer lassen: Dann wird der Wert der vorherigen Zeile übernommen."))
        self.tip_row(f, T("Gateway"), self.def_gw,
                     T("Wird bei jeder neuen Zeile der Serverliste vorbelegt.\n"
                     "Leer lassen: Dann wird der Wert der vorherigen Zeile übernommen."))

        right = QVBoxLayout()
        right.addWidget(box_new)
        right.addWidget(box_def)
        top = QHBoxLayout()
        top.addWidget(box_cur)
        top.addLayout(right)

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
        btns = QHBoxLayout()
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addWidget(again)
        scan = QPushButton(T("Handy-Scanner …"))
        scan.setToolTip(T("Passwörter vom Aufkleber/Anhänger mit der Handy-Kamera abfotografieren und direkt in die\n"
                        "Spalte „Passwort“ eintragen. Das Handy speichert keine Bilder."))
        scan.clicked.connect(self.open_scanner)
        btns.addWidget(scan)
        btns.addWidget(load)
        btns.addWidget(save)
        btns.addStretch()
        btns.addWidget(self.count)
        box_list = QGroupBox(T("Serverliste"))
        v = QVBoxLayout(box_list)
        v.addWidget(self.table)
        v.addLayout(btns)

        # --- Start ---
        self.dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
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
        self.stop = QPushButton(T("Abbrechen"))
        self.stop.setToolTip(T("Bricht nach dem Server ab, der gerade bearbeitet wird"))
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.abort)
        self.serial_btn = QPushButton(T("Seriennummern auslesen"))
        self.serial_btn.setToolTip(T("Liest für alle Zeilen ohne Seriennummer die Seriennummer (Dell: Service-Tag) aus dem BMC.\n"
                                     "Nur lesend, ein Anmeldeversuch je Gerät mit dem Passwort der Zeile (iLO 3/4: ohne Anmeldung).\n"
                                     "Das geschieht auch automatisch beim Eintragen einer Aktuellen IP. Eingetragenes wird nie überschrieben."))
        self.serial_btn.clicked.connect(self.read_serials)
        log_open = QPushButton(T("Protokoll öffnen …"))
        log_open.setToolTip(T("Öffnet eine zuvor gespeicherte Protokoll-Textdatei zur Ansicht.\n"
                              "Ersetzt die aktuelle Anzeige, ändert aber nichts an den Servern."))
        log_open.clicked.connect(self.open_log)
        log_save = QPushButton(T("Protokoll speichern …"))
        log_save.setToolTip(T("Speichert das Protokoll als Textdatei, z. B. für die Dokumentation.\n"
                              "Passwörter stehen nicht darin."))
        log_save.clicked.connect(self.save_log)
        actions = QHBoxLayout()
        actions.addWidget(self.dry)
        actions.addStretch()
        actions.addWidget(self.serial_btn)
        actions.addWidget(log_open)
        actions.addWidget(log_save)
        actions.addWidget(self.stop)
        actions.addWidget(self.start)

        # --- Was fehlt noch? ---
        self.cells_missing = self.cells_bad = 0
        self.todo = QLabel()
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
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.status = QLabel(T("Bereit"))

        upper = QWidget()
        u = QVBoxLayout(upper)
        u.setContentsMargins(0, 0, 0, 0)
        u.addWidget(box_list)
        u.addLayout(hints)
        u.addLayout(actions)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(upper)
        split.addWidget(self.log)
        split.setSizes([380, 260])

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.addLayout(self.build_profiles())
        pl.addLayout(top)
        pl.addWidget(split, 1)
        monitor_block = self.build_monitor()       # Monitor links oben; muss vor build_console existieren
        self.tabs = SideNav()                      # Menü links, Seiten rechts (statt Reiterleiste)
        self.tabs.addTab(self.build_dhcp(), T("DHCP-Server"), accent=DHCP_YELLOW)
        self.tabs.addTab(page, T("Neuware"))
        self.tabs.addTab(self.build_existing(), T("Bestandsserver"))
        self.tabs.addTab(self.build_bios(), T("UEFI-Einstellungen"))
        self.tabs.addTab(self.build_services(), T("Dienste"))
        self.tabs.addTab(self.build_firmware(), T("Firmware"))
        self.tabs.addTab(self.build_console(), T("UEFI-Konsole"))
        self.tabs.addTab(self.build_vbmc(), T("Testlabor"))
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
        self.tabs.addTab(help_page, T("Hilfe"))
        self.lang = QComboBox()
        for code, name in LANGS:
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(i18n.LANG)))
        self.lang.setToolTip("Sprache · Language · Langue · Sproch")
        self.lang.activated.connect(self.switch_language)

        # Linke Spalte: Monitor mit Varianten-Schalter, darunter das Menü, ganz unten die Sprache
        side = QWidget()
        self.nav_side = side                       # wird im Vollbild der Konsole ausgeblendet
        side.setFixedWidth(272)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        sl.addWidget(monitor_block)
        sl.addWidget(self.tabs.menu, 1)
        self.guard_btn = QPushButton()
        self.guard_btn.clicked.connect(self.edit_selfguard)
        self.update_guard_btn()
        sl.addWidget(self.guard_btn)
        tools = QHBoxLayout()
        pins_btn = QPushButton(T("Zertifikate …"))
        pins_btn.setToolTip(T("Gemerkte Zertifikats-Fingerabdrücke der BMCs ansehen und einzelne entfernen "
                              "(nach gewolltem Zertifikatswechsel oder Mainboard-Tausch)."))
        pins_btn.clicked.connect(self.edit_pins)
        data_btn = QPushButton(T("Sicherungen/Protokolle"))
        data_btn.setToolTip(T("Öffnet den Datenordner mit den automatischen Sicherungen vor jeder Änderung und dem "
                              "Revisionsprotokoll aller scharfen Läufe:\n{0}").format(i18n.DATA))
        data_btn.clicked.connect(self.open_data_dir)
        tools.addWidget(pins_btn)
        tools.addWidget(data_btn)
        sl.addLayout(tools)
        sl.addWidget(self.lang)
        body = QHBoxLayout()
        body.addWidget(side)
        body.addWidget(self.tabs.stack, 1)

        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addWidget(self.status)

        self.dirty = False
        self.vendor_changed()
        self.read_profiles()
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
                "dhcp_auto": self.auto_take.isChecked()}

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
        self.auto_take.setChecked(bool(p.get("dhcp_auto", True)))      # ältere Profile ohne Angabe: an
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
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.log.append(f'<span style="{style}">{html.escape(text)}</span>')

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
            self.status.setText(T('Geladen: {0}').format(path))

    def save_dialog(self):
        """Fragt nach Datei und danach, ob Passwörter hinein sollen. Gibt True zurück, wenn gespeichert wurde."""
        path, _ = QFileDialog.getSaveFileName(self, T("Liste speichern"), str(LIST), T("CSV (*.csv)"))
        if not path:
            return False
        box = QMessageBox(QMessageBox.Icon.Question, T("Liste speichern"),
                          T("Sollen die Passwörter mit in die Datei?"), parent=self)
        box.setInformativeText(T("Ohne Passwörter enthält die Datei nur Adressen, Hostnamen und Einstellungen. "
                               "Mit Passwörtern stehen diese im Klartext in der Datei."))
        b_without = box.addButton(T("Ohne Passwörter"), QMessageBox.ButtonRole.AcceptRole)
        b_with = box.addButton(T("Mit Passwörtern"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_without)
        box.exec()
        if box.clickedButton() not in (b_without, b_with):
            return False
        try:
            self.write_file(path, with_passwords=box.clickedButton() is b_with)
        except OSError as e:
            QMessageBox.warning(self, T("Liste speichern"), T('Datei nicht schreibbar:\n{0}').format(e))
            return False
        self.dirty = False
        self.status.setText(T('Gespeichert in {0}').format(path) + (
            T(" (mit Passwörtern im Klartext)") if box.clickedButton() is b_with else T(" (ohne Passwörter)")))
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
        self.bios_refresh_ips()
        self.bios_read_profiles()
        return page

    def bios_refresh_ips(self):
        keep = self.bios_ref.currentText()
        self.bios_ref.blockSignals(True)
        self.bios_ref.clear()
        for ip, _ in self.reachable_rows():
            self.bios_ref.addItem(ip)
        idx = self.bios_ref.findText(keep) if keep else -1
        if idx >= 0:
            self.bios_ref.setCurrentIndex(idx)
        elif keep:
            self.bios_ref.setEditText(keep)
        self.bios_ref.blockSignals(False)

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

    # ---------- DHCP-Server (dnsmasq) ----------
    def build_dhcp(self):
        self.dhcp = None            # dnsmasq-Prozess (Linux)
        self.dhcp_thread = None     # eingebauter Server (Windows)
        self.lease_file = None
        self.lease_state = None

        self.iface = QComboBox()
        self.iface.currentIndexChanged.connect(self.suggest_range)
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip(T("Netzwerkkarten neu einlesen"))
        refresh.clicked.connect(self.fill_ifaces)
        row = QHBoxLayout()
        row.addWidget(self.iface, 1)
        row.addWidget(refresh)
        self.dhcp_from = QLineEdit()
        self.dhcp_to = QLineEdit()
        self.iface.setToolTip(T("Netzwerkkarte dieses Rechners, die am Staging-Switch hängt.\n"
                              "Nur auf dieser Karte werden Adressen vergeben. Sie braucht eine feste IP."))
        tip = (T("Aus diesem Bereich bekommen die Geräte vorübergehend eine Adresse,\n"
               "damit das Tool sie erreichen kann. Er muss im Netz der gewählten Karte liegen\n"
               "und groß genug für alle Geräte sein."))
        self.range_tip = tip
        rng = QHBoxLayout()
        rng.addWidget(self.dhcp_from)
        rng.addWidget(QLabel(T("bis")))
        rng.addWidget(self.dhcp_to)
        self.dhcp_btn = QPushButton(T("DHCP starten"))
        self.dhcp_btn.setMinimumHeight(34)
        self.dhcp_btn.setToolTip(T("Startet bzw. stoppt die Adressvergabe auf der gewählten Karte.\n"
                                 "Nur an einem getrennten Staging-Switch verwenden."))
        self.dhcp_btn.clicked.connect(self.toggle_dhcp)
        self.dhcp_state = QLabel(T("Gestoppt"))
        act = QHBoxLayout()
        act.addWidget(self.dhcp_state)
        act.addStretch()
        act.addWidget(self.dhcp_btn)

        box = QGroupBox(T("DHCP-Server für das Staging-Netz"))
        f = QFormLayout(box)
        f.addRow(T("Netzwerkkarte"), row)
        f.addRow(T("Adressbereich"), rng)
        f.addRow("", QLabel(T("<small><b>Nur an einem getrennten Staging-Switch starten</b>, nie im "
                            "Produktivnetz.</small>")))
        f.addRow(act)

        self.leases = QTableWidget(0, 3)
        self.leases.setHorizontalHeaderLabels([T("Vergebene IP"), T("MAC-Adresse"), T("Gemeldeter Name")])
        self.leases.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.leases.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.leases.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.leases.setToolTip(T("Geräte, die eine Adresse bekommen haben. Der gemeldete Name hilft bei der Zuordnung,\n"
                               "z. B. „idrac-“ plus Service-Tag oder „ILO“ plus Seriennummer."))
        take = QPushButton(T("In Serverliste übernehmen"))
        take.setToolTip(T("Trägt die vergebenen Adressen als „Aktuelle IP“ in die Serverliste ein.\n"
                        "Mit Markierung nur die markierten Geräte, sonst alle."))
        take.clicked.connect(self.take_leases)
        self.lease_count = QLabel(T("0 Geräte"))
        self.lease_taken = set()          # MACs, die schon in der Serverliste waren: nie ein zweites Mal eintragen
        self.auto_take = QCheckBox(T("Automatisch übernehmen"))
        self.auto_take.setChecked(True)
        self.auto_take.setToolTip(T("Trägt neu gefundene Geräte von selbst in die Serverliste ein (Aktuelle IP).\n"
                                    "Es wird nichts konfiguriert: Neue IP, Hostname und der Start bleiben Handarbeit.\n"
                                    "Ein Gerät kommt nur einmal in die Liste; wer eine Zeile löscht, bekommt sie nicht\n"
                                    "von selbst zurück. Während eines Laufs wartet die Übernahme bis zum Ende.\n"
                                    "Ohne Haken übernimmt nur der Knopf rechts."))
        self.auto_take.toggled.connect(self.auto_take_leases)
        self.auto_timer = QTimer(self)             # holt zurückgestellte Geräte nach (Lauf aktiv, Zelle in Bearbeitung)
        self.auto_timer.setInterval(2000)
        self.auto_timer.timeout.connect(self.auto_take_leases)
        lb = QHBoxLayout()
        lb.addWidget(self.lease_count)
        lb.addStretch()
        lb.addWidget(self.auto_take)
        lb.addWidget(take)
        self.own_laptop_label = QLabel()
        self.own_laptop_label.setVisible(False)
        self.own_laptop_label.setStyleSheet("color: gray; font-style: italic;")
        self.own_laptop_label.setToolTip(T("Dieser Rechner selbst hat eine Adresse vom DHCP-Server erhalten.\nEr erscheint nicht in der Geräteliste unten."))

        box2 = QGroupBox(T("Gefundene Geräte"))
        v = QVBoxLayout(box2)
        v.addWidget(self.own_laptop_label)
        v.addWidget(self.leases)
        v.addLayout(lb)

        self.dhcp_log = QTextEdit()
        self.dhcp_log.setReadOnly(True)
        self.dhcp_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.dhcp_log.setMaximumHeight(150)

        self.lease_timer = QTimer(self)
        self.lease_timer.setInterval(2000)
        self.lease_timer.timeout.connect(self.read_leases)
        self.dhcp_ip = None
        self.guard_timer = QTimer(self)             # prüft laufend, ob die Karte noch ein Staging-Netz ist
        self.guard_timer.setInterval(10000)
        self.guard_timer.timeout.connect(self.dhcp_guard)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(box)
        lay.addWidget(box2, 1)
        lay.addWidget(self.dhcp_log)
        self.fill_ifaces()
        return page

    def fill_ifaces(self):
        keep = (self.iface.currentData() or [None])[0]
        self.iface.blockSignals(True)
        self.iface.clear()
        for nic in QNetworkInterface.allInterfaces():
            if nic.flags() & QNetworkInterface.InterfaceFlag.IsLoopBack:
                continue
            label = nic.humanReadableName() if WINDOWS else nic.name()     # Windows: "Ethernet 2"
            v4 = [e for e in nic.addressEntries()
                  if e.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol]
            if v4:
                for e in v4:
                    ip, plen = e.ip().toString(), e.prefixLength()
                    self.iface.addItem(f"{label}  –  {ip}/{plen}", [nic.name(), ip, plen])
            else:
                self.iface.addItem(T('{0}  –  keine IPv4-Adresse').format(label), [nic.name(), None, None])
        names = [self.iface.itemData(i) for i in range(self.iface.count())]
        pick = next((i for i, d in enumerate(names) if d[0] == keep), None)
        if pick is None:                           # sonst erste Karte mit IPv4-Adresse
            pick = next((i for i, d in enumerate(names) if d[1]), 0)
        self.iface.setCurrentIndex(pick)
        self.iface.blockSignals(False)
        self.suggest_range()

    def iface_net(self):
        data = self.iface.currentData()
        if not data or not data[1]:
            return None
        return ipaddress.ip_interface(f"{data[1]}/{data[2]}")

    def suggest_range(self, *_):
        """Bereich vorschlagen, wenn der eingetragene nicht zum Netz der Karte passt."""
        me = self.iface_net()
        if not me:
            return
        try:
            if all(ipaddress.IPv4Address(w.text()) in me.network for w in (self.dhcp_from, self.dhcp_to)):
                return
        except ValueError:
            pass
        net = me.network
        if net.num_addresses >= 256:
            lo, hi = net.network_address + 100, net.network_address + 200
        else:
            lo, hi = net.network_address + 2, net.broadcast_address - 1
        self.dhcp_from.setText(str(lo))
        self.dhcp_to.setText(str(hi))
        self.validate_range()

    def validate_range(self, *_):
        """Markiert ungültige DHCP-Bereichsfelder, gibt eine Fehlermeldung zurück oder None."""
        me = self.iface_net()
        lo, hi = ipv4(self.dhcp_from.text().strip()), ipv4(self.dhcp_to.text().strip())
        msgs = {}
        for w, ip in ((self.dhcp_from, lo), (self.dhcp_to, hi)):
            if not ip:
                msgs[w] = T("Keine gültige IPv4-Adresse")
            elif me and ip not in me.network:
                msgs[w] = T('Liegt nicht im Netz der Karte ({0})').format(me.network)
            elif me and ip in (me.network.network_address, me.network.broadcast_address):
                msgs[w] = T("Netz- oder Broadcast-Adresse")
        if lo and hi and lo > hi and not msgs:
            msgs[self.dhcp_to] = T("Ende liegt vor dem Anfang des Bereichs")
        for w in (self.dhcp_from, self.dhcp_to):
            w.setStyleSheet("QLineEdit { border: 1px solid #d9534f; }" if w in msgs else "")
            w.setToolTip(msgs.get(w) or self.range_tip)
        return next(iter(msgs.values()), None)

    def dlog(self, text, color=None):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        self.dhcp_log.append(f'<span style="{style}">{html.escape(text)}</span>')

    def dhcp_running(self):
        return (bool(self.dhcp) and self.dhcp.state() != QProcess.ProcessState.NotRunning) \
            or (bool(self.dhcp_thread) and self.dhcp_thread.isRunning())

    def toggle_dhcp(self):
        if self.dhcp_running():
            self.stop_dhcp()
        else:
            self.start_dhcp()

    def start_dhcp(self):
        data = self.iface.currentData()
        me = self.iface_net()
        exe = None if WINDOWS else shutil.which("dnsmasq") or next(
            (p for p in ("/usr/bin/dnsmasq", "/usr/sbin/dnsmasq") if os.path.exists(p)), None)
        err = None
        if not data:
            err = T("Keine Netzwerkkarte gefunden.")
        elif not me and WINDOWS:
            err = (T('Die Karte „{0}“ hat keine IPv4-Adresse.\n\nIn den Adaptereinstellungen (ncpa.cpl) bei dieser Karte unter „Internetprotokoll, Version 4“ eine feste Adresse eintragen, z. B. 192.168.0.1 mit Maske 255.255.255.0, Gateway leer lassen.\n\nDanach hier auf ↻ klicken.').format(self.iface.currentText().split('  –  ')[0]))
        elif not me:
            err = (T('Die Karte {0} hat keine IPv4-Adresse. Feste Adresse setzen, z. B. im Terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nDanach hier auf ↻ klicken.').format(data[0], data[0]))
        elif WINDOWS:
            err = self.validate_range()
            if err:
                err = T("Adressbereich: ") + err
            else:
                lo, hi = (ipaddress.IPv4Address(w.text().strip()) for w in (self.dhcp_from, self.dhcp_to))
        elif not exe:
            err = T("dnsmasq ist nicht installiert:\n\nsudo pacman -S dnsmasq")
        elif os.geteuid() != 0 and not shutil.which("pkexec"):
            err = T("pkexec (polkit) fehlt, damit kann der DHCP-Server nicht mit Root-Rechten starten.")
        else:
            err = self.validate_range()
            if err:
                err = T("Adressbereich: ") + err
            else:
                lo, hi = (ipaddress.IPv4Address(w.text().strip()) for w in (self.dhcp_from, self.dhcp_to))
        if err:
            box = QMessageBox(QMessageBox.Icon.Warning, T("DHCP-Server"), err, parent=self)
            box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.exec()
            return

        # Schutz: in einem Netz, das nach Produktivnetz aussieht, startet der DHCP-Server gar nicht erst
        ip, found, block = str(me.ip), None, None
        if not me.ip.is_private:
            block = (T('Die Karte hat eine öffentliche Adresse ({0}). Ein Staging-Netz nutzt private Adressen, deshalb wird hier kein DHCP-Server gestartet.').format(ip))
        elif self.default_route_ip() == ip:
            block = (T("Über diese Karte läuft die Standardroute dieses Rechners (Internet bzw. Firmennetz). "
                     "Das ist kein getrenntes Staging-Netz, deshalb wird hier kein DHCP-Server gestartet.\n\n"
                     "Für das Staging die Karte mit fester Adresse und ohne Gateway einrichten."))
        elif self.address_is_dynamic(data[0], ip):
            block = (T("Diese Karte hat ihre eigene Adresse per DHCP bekommen. In diesem Netz läuft also schon ein "
                     "DHCP-Server, ein zweiter würde dort alle Geräte stören. Deshalb wird hier keiner "
                     "gestartet.\n\nFür das Staging der Karte eine feste Adresse geben."))
        else:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                found = probe_dhcp_servers(ip)
            finally:
                QApplication.restoreOverrideCursor()
            if found:
                others = ", ".join(sorted(found))
                block = (T('Auf dieser Karte antwortet bereits ein DHCP-Server ({0}). Ein zweiter würde dort alle Geräte stören, deshalb wird hier keiner gestartet.').format(others))
        if block:
            QMessageBox.critical(self, T("DHCP-Server nicht gestartet"), block)
            return
        info = (T("Kein anderer DHCP-Server hat innerhalb von 1,5 Sekunden geantwortet.")
                if found is not None else
                T("Ob dort schon ein DHCP-Server läuft, ließ sich nicht prüfen (Port 68 braucht Root-Rechte)."))
        info += " " + (T("Ein zweiter DHCP-Server in einem Produktivnetz stört dort alle Geräte.\n\n"
                       "Hängt diese Karte wirklich an einem getrennten Staging-Switch?"))
        if not self.confirm(T("DHCP-Server starten?"),
                            T('Auf {0} ({1}) werden Adressen von {2} bis {3} vergeben.').format(data[0], me.network, lo, hi), info):
            return

        self.dhcp_log.clear()
        self.lease_state = None
        self.dhcp_btn.setText(T("DHCP stoppen"))
        self.dhcp_ip = ip
        self.guard_timer.start()
        self.dhcp_state.setText(T("<b style='color:#3fa45b'>Läuft</b> auf {0}, {1} bis {2}").format(data[0] if not WINDOWS else data[1], lo, hi))
        self.iface.setEnabled(False)
        self.dhcp_from.setEnabled(False)
        self.dhcp_to.setEnabled(False)
        self.auto_timer.start()

        if WINDOWS:                                  # eingebauter Server, braucht kein Zusatzprogramm
            self.lease_file = None
            self.show_leases([])
            # APIPA-Adresse (169.254.x.x automatisch vergeben): feste IP setzen damit
            # der Send-Socket ans richtige Interface gebunden werden kann und die Lease
            # beim nächsten Start noch gültig ist.
            self._dhcp_set_ip(data[0], data[1], str(me.netmask))
            self.dhcp_thread = DhcpThread(self, ip=data[1], mask=str(me.netmask), lo=str(lo), hi=str(hi))
            self.dhcp_thread.line.connect(lambda t: self.dlog(t, "#d9534f" if t.startswith("error") else None))
            self.dhcp_thread.leases.connect(self.show_leases)
            self.dhcp_thread.own_lease.connect(self.show_own_lease)
            self.dhcp_thread.result.connect(self.dhcp_done)
            self.dhcp_thread.start()
            return

        import getpass
        import grp
        tmp = tempfile.mkdtemp(prefix="bmc-dhcp-")
        self.lease_file = os.path.join(tmp, "leases")
        args = [
            exe, "--keep-in-foreground", "--conf-file=/dev/null",
            "--port=0",                              # kein DNS, nur DHCP
            f"--interface={data[0]}", "--bind-interfaces", "--except-interface=lo",
            f"--dhcp-range={lo},{hi},{me.netmask},1h",
            "--dhcp-option=3", "--dhcp-option=6",    # weder Gateway noch DNS verteilen
            f"--dhcp-leasefile={self.lease_file}", f"--pid-file={os.path.join(tmp, 'pid')}",
            "--log-facility=-", "--log-dhcp",
            # nach dem Start Root-Rechte abgeben, damit das Tool den Dienst selbst beenden kann
            f"--user={getpass.getuser()}", f"--group={grp.getgrgid(os.getgid()).gr_name}",
        ]
        if os.geteuid() != 0:
            args.insert(0, "pkexec")
        self.dlog("$ " + " ".join(args), "gray")
        self.dhcp = QProcess(self)
        self.dhcp.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.dhcp.readyReadStandardOutput.connect(self.dhcp_output)
        self.dhcp.finished.connect(self.dhcp_done)
        self.dhcp.errorOccurred.connect(
            lambda e: e == QProcess.ProcessError.FailedToStart and self.dhcp_done(127))
        self.dhcp.start(args[0], args[1:])
        self.lease_timer.start()

    def dhcp_output(self):
        text = bytes(self.dhcp.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            bad = any(w in line.lower() for w in ("failed", "error", "cannot", "not authorized", "dismissed"))
            self.dlog(line, "#d9534f" if bad else None)

    def _dhcp_set_ip(self, iface_name, ip, mask):
        """Setzt auf Windows eine feste IP auf dem Interface, falls es gerade eine APIPA-Adresse hat.
        Merkt sich ob die IP geändert wurde, damit stop_dhcp sie zurücksetzen kann."""
        self._dhcp_restored_ip = False
        if not WINDOWS:
            return
        addr = ipaddress.ip_address(ip)
        if not addr.is_link_local:
            return   # feste IP schon konfiguriert, nichts zu tun
        try:
            subprocess.run(
                ["netsh", "interface", "ip", "set", "address",
                 f"name={iface_name}", "static", ip, mask],
                capture_output=True, timeout=10, creationflags=0x08000000)
            self._dhcp_restored_ip = True
            self.dlog(T("Interface {0}: feste IP {1}/{2} gesetzt (war APIPA)").format(iface_name, ip, mask), "gray")
        except (OSError, subprocess.SubprocessError) as e:
            self.dlog(T("IP setzen fehlgeschlagen: {0}").format(e), "#d9534f")

    def stop_dhcp(self):
        if not self.dhcp_running():
            return
        if self.dhcp_thread and self.dhcp_thread.isRunning():
            self.dhcp_thread.requestInterruption()
            self.dhcp_thread.wait(3000)
            # IP zurücksetzen wenn wir sie gesetzt haben
            if getattr(self, "_dhcp_restored_ip", False):
                data = self.iface.currentData()
                if data:
                    try:
                        subprocess.run(
                            ["netsh", "interface", "ip", "set", "address",
                             f"name={data[0]}", "dhcp"],
                            capture_output=True, timeout=10, creationflags=0x08000000)
                        self._dhcp_restored_ip = False
                    except (OSError, subprocess.SubprocessError):
                        pass
            return
        pid = self.dhcp.processId()
        self.dhcp.terminate()
        if not self.dhcp.waitForFinished(2000) and os.geteuid() != 0:
            # läuft noch mit Root-Rechten (z. B. Start nicht abgeschlossen): per pkexec beenden
            QProcess.execute("pkexec", ["kill", str(pid)])
            self.dhcp.waitForFinished(3000)

    def dhcp_done(self, code=0, *_):
        self.lease_timer.stop()
        self.auto_timer.stop()
        self.guard_timer.stop()
        if self.lease_file:
            self.read_leases()
            shutil.rmtree(os.path.dirname(self.lease_file), ignore_errors=True)
            self.lease_file = None
        self.dhcp_btn.setText(T("DHCP starten"))
        self.iface.setEnabled(True)
        self.dhcp_from.setEnabled(True)
        self.dhcp_to.setEnabled(True)
        self.own_laptop_label.setVisible(False)
        hints = {126: T("Anmeldung abgebrochen"), 127: T("Start nicht möglich oder Anmeldung fehlgeschlagen"),
                 2: T("Port 67 belegt oder Karte nicht bereit, siehe Protokoll")}
        if code in (0, 15) or (self.dhcp and self.dhcp.exitStatus() == QProcess.ExitStatus.CrashExit):
            self.dhcp_state.setText(T("Gestoppt"))
        else:
            self.dhcp_state.setText(T("<b style='color:#d9534f'>Beendet (Code {0})</b>: ").format(code)
                                    + hints.get(code, T("siehe Protokoll")))

    def read_leases(self):
        """Lease-Datei von dnsmasq lesen: Ablauf, MAC, IP, Name, Client-ID."""
        try:
            raw = Path(self.lease_file).read_text() if self.lease_file else ""
        except OSError:
            raw = ""
        if raw == self.lease_state:
            return
        self.lease_state = raw
        rows = []
        for line in raw.splitlines():
            f = line.split()
            if len(f) >= 4 and f[0].isdigit():
                rows.append((f[2], f[1], "" if f[3] == "*" else f[3]))
        self.show_leases(rows)

    def show_leases(self, rows):
        """Geräteliste anzeigen: Zeilen aus (IP, MAC, Name)."""
        rows = sorted(rows, key=lambda r: ipaddress.ip_address(r[0]))
        self.leases.setRowCount(len(rows))
        for r, vals in enumerate(rows):
            for c, val in enumerate(vals):
                self.leases.setItem(r, c, QTableWidgetItem(val))
        self.lease_count.setText(T('{0} Geräte').format(len(rows)))
        self.auto_take_leases()

    def show_own_lease(self, row):
        """Eigenen Rechner über der Geräteliste anzeigen (nicht in der Liste selbst)."""
        ip, mac, name = row
        text = T("Dieser Rechner: {0}  –  {1}").format(ip, name or mac)
        self.own_laptop_label.setText(text)
        self.own_laptop_label.setVisible(True)

    def lease_pairs(self, rows=None):
        """(IP, MAC) der angezeigten Geräte; mit rows nur die dieser Tabellenzeilen."""
        return [(self.leases.item(r, 0).text(), self.leases.item(r, 1).text())
                for r in (range(self.leases.rowCount()) if rows is None else rows)]

    def add_leased(self, pairs):
        """Geräte als „Aktuelle IP“ in die Serverliste eintragen. Gibt die neu eingetragenen IPs zurück."""
        known = {r[0] for r in self.rows(open_only=True)}      # fertige Server haben ihre alte IP abgegeben
        new = []
        for ip, mac in pairs:
            self.lease_taken.add(mac or ip)                    # auch schon bekannte: später nicht noch einmal anbieten
            if ip not in known and ip not in new:
                new.append(ip)
        if not new:
            return []
        for r in reversed(range(self.table.rowCount())):          # leere Zeilen entfernen
            if not any(self.cell(r, c) for c in (0, 2, 5)):
                self.table.removeRow(r)
        for ip in new:
            self.add_row()
            self.table.item(self.table.rowCount() - 1, 0).setText(ip)
        self.update_count()
        self.touch()
        return new

    def take_leases(self):
        sel = sorted({i.row() for i in self.leases.selectedIndexes()}) or range(self.leases.rowCount())
        new = self.add_leased(self.lease_pairs(sel))
        if not new:
            self.status.setText(T("Keine neuen Geräte zum Übernehmen"))
            return
        self.tabs.setCurrentIndex(1)
        self.status.setText(T('{0} Geräte übernommen: jetzt neue IP und Hostname eintragen').format(len(new)))

    def auto_take_leases(self, *_):
        """Neu gefundene Geräte von selbst in die Serverliste eintragen (Haken „Automatisch übernehmen“).
        Wartet, solange die Liste gesperrt ist (Lauf) oder eine Zelle bearbeitet wird; der Timer holt es nach."""
        if not self.auto_take.isChecked() or self.running() or not self.table.isEnabled() \
                or self.table.state() == QAbstractItemView.State.EditingState:
            return
        fresh = [(ip, mac) for ip, mac in self.lease_pairs() if (mac or ip) not in self.lease_taken]
        if not fresh:
            return
        new = self.add_leased(fresh)
        if new:
            text = T('{0} Geräte automatisch übernommen: jetzt neue IP und Hostname eintragen').format(len(new))
            self.status.setText(text)
            self.dlog(text, "#3fa45b")

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
            if not self.confirm(T("Scharf ausführen?"),
                                T('{0} Server werden jetzt wirklich umgestellt (Benutzer, Hostname, feste IP).').format(n),
                                info, n if far else None):
                return

        self.log.clear()
        skipped = len(self.rows()) - n
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.say(T('— {0}, {1} Server').format(mode, n) + (T(', {0} fertige übersprungen').format(skipped) if skipped else "") + " —", "gray")
        self.worker = EngineThread(
            self, rows=todo, cur_user=self.cur_user.text().strip(), cur_pass=self.cur_pass.text(),
            new_user=self.new_user.text(), new_pass=self.new_pass.text(), slot=self.slot.value(), dry=dry,
            vendor=self.vendor.currentData() or None)
        self.worker.line.connect(self.show_line)
        self.worker.row_done.connect(self.mark_done)
        self.worker.result.connect(self.done)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.table.setEnabled(False)
        self.status.setText(T("Läuft …"))
        self.worker.start()

    def show_line(self, line, kind=""):
        self.say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

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

    # ---------- Bestandsserver (schon konfigurierte BMCs: Verbindung prüfen) ----------
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

        box_all = QGroupBox(T("Zugangsdaten und Adressen schnell eintragen"))
        f = QFormLayout(box_all)
        self.tip_row(f, T("Hersteller"), self.ex_vendor, self.ex_vendor.toolTip())
        self.tip_row(f, T("Benutzer"), self.ex_user_all,
                     T("Benutzer, mit dem sich das Tool am BMC anmeldet. Leer lassen: Werksbenutzer je Hersteller."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.ex_pass_all),
                     T("Gilt für alle Zeilen, wenn Sie unten auf „Für alle Zeilen übernehmen“ klicken.\n"
                       "Abweichende Zugangsdaten tragen Sie direkt in der Tabelle je Zeile ein."))
        apply = QPushButton(T("Für alle Zeilen übernehmen"))
        apply.setToolTip(T("Trägt Benutzer und Passwort von oben in alle Zeilen ein und überschreibt vorhandene Werte."))
        apply.clicked.connect(self.ex_apply_all)
        f.addRow("", apply)
        rng = QHBoxLayout()
        rng.addWidget(self.ex_from)
        rng.addWidget(QLabel("–"))
        rng.addWidget(self.ex_to)
        add_rng = QPushButton(T("Bereich einfügen"))
        add_rng.setToolTip(T("Fügt für jede Adresse von … bis … eine Zeile ein (höchstens 256 auf einmal)."))
        add_rng.clicked.connect(self.ex_add_range)
        rng.addWidget(add_rng)
        f.addRow(T("Adressbereich"), rng)

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
        btns = QHBoxLayout()
        for b in (add, rem, pull):
            btns.addWidget(b)
        btns.addStretch()
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
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.addWidget(info)
        pl.addWidget(box_all)
        split = QSplitter(Qt.Orientation.Vertical)
        upper = QWidget()
        u = QVBoxLayout(upper)
        u.setContentsMargins(0, 0, 0, 0)
        u.addWidget(box_list)
        u.addLayout(act)
        split.addWidget(upper)
        split.addWidget(self.ex_log)
        split.setSizes([320, 240])
        pl.addWidget(split, 1)
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

    def ex_remove_rows(self):
        for r in sorted({i.row() for i in self.ex_table.selectedIndexes()}, reverse=True):
            self.ex_table.removeRow(r)

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

    @staticmethod
    def NAME_OF(res):
        return ProbeEngine.NAME.get(res.get("vendor", ""), "")

    def ex_abort(self):
        if self.ex_worker and self.ex_worker.isRunning():
            self.ex_worker.requestInterruption()
            self.ex_stop.setEnabled(False)
            self.ex_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def ex_done(self, code, *_):
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
        for w in (self.cur_user, self.bios_user, self.svc_user):
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

    # ---------- Dienste (NTP, Syslog, LDAP, HTTPS-Zertifikat) ----------
    def build_services(self):
        self.svc_worker = None
        self.cert_pem = ""
        self.cert_name = ""

        self.svc_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.svc_vendor.addItem(text, key)
        self.svc_user = QLineEdit()
        self.svc_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.svc_pass = self._pw()
        box_login = QGroupBox(T("Login (Werksbenutzer, gilt für alle Server der Liste)"))
        box_login.setToolTip(T("Die Dienste werden auf allen Servern der Serverliste („Neuware“) eingerichtet.\n"
                               "Fertige (grüne) Server werden unter ihrer neuen Adresse angesprochen.\n"
                               "Angemeldet wird mit dem Werksbenutzer; der Werks-Administrator bleibt bestehen."))
        f = QFormLayout(box_login)
        self.tip_row(f, T("Hersteller"), self.svc_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ fragt jedes Gerät."))
        self.tip_row(f, T("Benutzer"), self.svc_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.svc_pass),
                     T("Fällt für Zeilen ein, deren Spalte „Passwort“ in der Serverliste leer ist."))

        self.ntp_box = QGroupBox(T("NTP (Zeitserver)"))
        self.ntp_box.setCheckable(True)
        self.ntp_box.setChecked(False)
        self.ntp1 = QLineEdit()
        self.ntp1.setPlaceholderText(T("z. B. 10.20.0.1 oder pool.ntp.org"))
        self.ntp2 = QLineEdit()
        self.ntp2.setPlaceholderText(T("optional"))
        self.ntp_tz = QLineEdit()
        self.ntp_tz.setPlaceholderText(T("nur Dell, z. B. Europe/Berlin"))
        f = QFormLayout(self.ntp_box)
        self.tip_row(f, T("NTP-Server 1"), self.ntp1, T("Pflicht, wenn NTP eingerichtet wird."))
        self.tip_row(f, T("NTP-Server 2"), self.ntp2, T("Optional, zweiter Zeitserver."))
        self.tip_row(f, T("Zeitzone"), self.ntp_tz,
                     T("Nur bei Dell gesetzt. Bei HPE, Supermicro und Lenovo bitte in der Oberfläche wählen."))

        self.syslog_box = QGroupBox(T("Syslog"))
        self.syslog_box.setCheckable(True)
        self.syslog_box.setChecked(False)
        self.syslog_box.setToolTip(T("Nur Dell und HPE. Bei Supermicro und Lenovo wird Syslog übersprungen."))
        self.syslog_server = QLineEdit()
        self.syslog_server.setPlaceholderText(T("z. B. 10.20.0.9"))
        self.syslog_port = QLineEdit("514")
        f = QFormLayout(self.syslog_box)
        self.tip_row(f, T("Server"), self.syslog_server, T("Adresse des Syslog-Empfängers."))
        self.tip_row(f, T("Port"), self.syslog_port, T("Standard 514."))

        self.ldap_box = QGroupBox(T("LDAP / Active Directory"))
        self.ldap_box.setCheckable(True)
        self.ldap_box.setChecked(False)
        self.ldap_server = QLineEdit()
        self.ldap_server.setPlaceholderText(T("z. B. ad.example.com"))
        self.ldap_port = QLineEdit("636")
        self.ldap_base = QLineEdit()
        self.ldap_base.setPlaceholderText("dc=example,dc=com")
        self.ldap_bind = QLineEdit()
        self.ldap_bind.setPlaceholderText(T("optional, z. B. cn=svc,dc=example,dc=com"))
        self.ldap_bindpw = self._pw()
        self.ldap_userattr = QLineEdit("sAMAccountName")
        self.ldap_groupattr = QLineEdit("memberOf")
        f = QFormLayout(self.ldap_box)
        self.tip_row(f, T("Server"), self.ldap_server, T("Adresse des LDAP-/AD-Servers."))
        self.tip_row(f, T("Port"), self.ldap_port, T("389 unverschlüsselt, 636 für LDAPS."))
        self.tip_row(f, T("Base-DN"), self.ldap_base, T("Ab wo gesucht wird, z. B. dc=example,dc=com."))
        self.tip_row(f, T("Bind-DN"), self.ldap_bind,
                     T("Dienstkonto zum Suchen. Leer lassen, wenn der Server anonyme Suche erlaubt."))
        self.tip_row(f, T("Bind-Passwort"), self._with_eye(self.ldap_bindpw), T("Passwort des Bind-DN."))
        self.tip_row(f, T("Benutzer-Attribut"), self.ldap_userattr,
                     T("Active Directory: sAMAccountName. OpenLDAP oft uid."))
        self.tip_row(f, T("Gruppen-Attribut"), self.ldap_groupattr, T("Meist memberOf."))
        self.ldap_groups = QTableWidget(0, 2)
        self.ldap_groups.setHorizontalHeaderLabels([T("LDAP-Gruppe"), T("Rolle")])
        self.ldap_groups.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ldap_groups.setMaximumHeight(140)
        self.ldap_groups.setToolTip(T("Ordnet LDAP-Gruppen einer Rolle zu: Administrator, Operator oder ReadOnly.\n"
                                      "Leer lassen, wenn keine Gruppen-Zuordnung gebraucht wird."))
        gadd = QPushButton(T("+ Gruppe"))
        gadd.clicked.connect(self.ldap_group_add)
        grem = QPushButton(T("– Gruppe"))
        grem.clicked.connect(self.ldap_group_remove)
        gbar = QHBoxLayout()
        gbar.addWidget(gadd)
        gbar.addWidget(grem)
        gbar.addStretch()
        f.addRow(QLabel(T("Gruppen → Rolle")))
        f.addRow(self.ldap_groups)
        f.addRow(gbar)

        self.cert_box = QGroupBox(T("HTTPS-Zertifikat"))
        self.cert_box.setCheckable(True)
        self.cert_box.setChecked(False)
        self.cert_box.setToolTip(T("Experimentell und je Hersteller verschieden. Importiert ein von einer CA signiertes\n"
                                   "Zertifikat (PEM). Bei HPE muss der passende Schlüssel aus einer auf dem iLO erzeugten\n"
                                   "Zertifikatsanforderung (CSR) stammen, sonst scheitert der Import."))
        self.cert_label = QLabel(T("keine Datei gewählt"))
        cert_btn = QPushButton(T("PEM-Datei wählen …"))
        cert_btn.clicked.connect(self.cert_load)
        f = QFormLayout(self.cert_box)
        crow = QHBoxLayout()
        crow.addWidget(cert_btn)
        crow.addWidget(self.cert_label, 1)
        f.addRow(crow)

        self.hard_box = QGroupBox(T("Härtung (empfohlen)"))
        self.hard_box.setCheckable(True)
        self.hard_box.setChecked(False)
        self.hard_box.setToolTip(T("Schaltet unnötige Netzwerkdienste ab und setzt Kontosperre und Session-Zeitlimit "
                                   "über Standard-Redfish. Was ein BMC nicht kennt, wird übersprungen."))
        self.hard_ipmi = QCheckBox(T("IPMI über LAN abschalten"))
        self.hard_ipmi.setChecked(True)
        self.hard_ipmi.setToolTip(T("IPMI hat schwache Anmeldeverfahren. Achtung: Die Textkonsole (SOL) bei Supermicro "
                                    "und Überwachungssysteme, die IPMI nutzen, gehen danach nicht mehr."))
        self.hard_ssdp = QCheckBox(T("SSDP-Ankündigung abschalten"))
        self.hard_ssdp.setChecked(True)
        self.hard_ssdp.setToolTip(T("Der BMC kündigt sich dann nicht mehr per Multicast im Netz an."))
        self.hard_snmp = QCheckBox(T("SNMP abschalten"))
        self.hard_snmp.setToolTip(T("Nur, wenn kein Monitoring per SNMP genutzt wird."))
        self.hard_lock = QSpinBox()
        self.hard_lock.setRange(0, 20)
        self.hard_lock.setValue(5)
        self.hard_lock.setSpecialValueText(T("unverändert"))
        self.hard_lock_min = QSpinBox()
        self.hard_lock_min.setRange(1, 1440)
        self.hard_lock_min.setValue(15)
        self.hard_lock_min.setSuffix(" min")
        self.hard_tmo = QSpinBox()
        self.hard_tmo.setRange(0, 1440)
        self.hard_tmo.setValue(15)
        self.hard_tmo.setSuffix(" min")
        self.hard_tmo.setSpecialValueText(T("unverändert"))
        f = QFormLayout(self.hard_box)
        f.addRow(self.hard_ipmi)
        f.addRow(self.hard_ssdp)
        f.addRow(self.hard_snmp)
        self.tip_row(f, T("Kontosperre nach Fehlversuchen"), self.hard_lock,
                     T("Sperrt ein Konto nach so vielen falschen Passwörtern (0 = unverändert)."))
        self.tip_row(f, T("Sperrdauer"), self.hard_lock_min, T("So lange bleibt ein gesperrtes Konto gesperrt."))
        self.tip_row(f, T("Session-Zeitlimit"), self.hard_tmo,
                     T("Untätige Anmeldungen (Weboberfläche, Redfish) enden nach dieser Zeit (0 = unverändert)."))

        settings = QWidget()
        sv = QVBoxLayout(settings)
        for w in (box_login, self.ntp_box, self.syslog_box, self.ldap_box, self.cert_box, self.hard_box):
            sv.addWidget(w)
        sv.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(settings)

        self.svc_dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.svc_dry.setChecked(True)
        self.svc_dry.setToolTip(T("Mit Haken wird nichts geändert; das Protokoll zeigt nur die Aufrufe."))
        self.svc_start = QPushButton(T("Trockenlauf für alle Server der Liste"))
        self.svc_start.setMinimumHeight(34)
        self.svc_start.clicked.connect(self.services_run)
        self.svc_stop = QPushButton(T("Abbrechen"))
        self.svc_stop.setEnabled(False)
        self.svc_stop.clicked.connect(self.svc_abort)
        self.svc_status = QLabel(T("Bereit"))
        act = QHBoxLayout()
        act.addWidget(self.svc_dry)
        act.addStretch()
        act.addWidget(self.svc_stop)
        act.addWidget(self.svc_start)

        self.svc_log = QTextEdit()
        self.svc_log.setReadOnly(True)
        self.svc_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        page = QWidget()
        pl = QVBoxLayout(page)
        hint = QLabel("<small>" + T("Nur angehakte Abschnitte werden angewendet. Alles wird auf den Servern der "
                                    "Liste in „Neuware“ gesetzt, grüne unter ihrer neuen Adresse.") + "</small>")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(scroll)
        low = QWidget()
        ll = QVBoxLayout(low)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(act)
        ll.addWidget(self.svc_log)
        split.addWidget(low)
        split.setSizes([470, 210])
        pl.addWidget(split, 1)
        return page

    def ldap_group_add(self):
        r = self.ldap_groups.rowCount()
        self.ldap_groups.insertRow(r)
        self.ldap_groups.setItem(r, 0, QTableWidgetItem(""))
        self.ldap_groups.setItem(r, 1, QTableWidgetItem("Administrator"))

    def ldap_group_remove(self):
        for r in sorted({i.row() for i in self.ldap_groups.selectedIndexes()}, reverse=True):
            self.ldap_groups.removeRow(r)

    def cert_load(self):
        path, _ = QFileDialog.getOpenFileName(self, T("Zertifikat wählen"), str(HERE),
                                              T("Zertifikat (*.pem *.crt *.cer);;Alle Dateien (*)"))
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            QMessageBox.warning(self, T("Zertifikat"), T('Datei nicht lesbar:\n{0}').format(e))
            return
        if "BEGIN CERTIFICATE" not in text:
            QMessageBox.warning(self, T("Zertifikat"),
                                T("Die Datei enthält kein PEM-Zertifikat (erwartet „BEGIN CERTIFICATE“)."))
            return
        self.cert_pem = text
        self.cert_name = os.path.basename(path)
        self.cert_label.setText(self.cert_name)

    def services_dict(self):
        """Sammelt die angehakten Dienste und prüft die Pflichtfelder. Gibt ein dict zurück, {} wenn
        nichts angehakt ist, oder None bei einem Eingabefehler (dann wird eine Meldung gezeigt)."""
        def bad(widget, msg):
            self.tabs.setCurrentIndex(3)
            QMessageBox.warning(self, T("Dienste"), msg)
            if widget:
                widget.setFocus()
            return None
        out = {}
        if self.ntp_box.isChecked():
            if not self.ntp1.text().strip():
                return bad(self.ntp1, T("NTP: mindestens den ersten Server eintragen."))
            out["ntp"] = {"servers": [self.ntp1.text(), self.ntp2.text()], "timezone": self.ntp_tz.text()}
        if self.syslog_box.isChecked():
            if not self.syslog_server.text().strip():
                return bad(self.syslog_server, T("Syslog: Server eintragen."))
            port = self.syslog_port.text().strip() or "514"
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                return bad(self.syslog_port, T("Port muss eine Zahl von 1 bis 65535 sein."))
            out["syslog"] = {"server": self.syslog_server.text(), "port": int(port)}
        if self.ldap_box.isChecked():
            if not self.ldap_server.text().strip() or not self.ldap_base.text().strip():
                return bad(self.ldap_server, T("LDAP: Server und Base-DN eintragen."))
            port = self.ldap_port.text().strip() or "636"
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                return bad(self.ldap_port, T("Port muss eine Zahl von 1 bis 65535 sein."))
            groups = []
            for r in range(self.ldap_groups.rowCount()):
                g = self.ldap_groups.item(r, 0).text().strip() if self.ldap_groups.item(r, 0) else ""
                role = self.ldap_groups.item(r, 1).text().strip() if self.ldap_groups.item(r, 1) else ""
                if g:
                    if role and role not in ServicesEngine.ROLES:
                        return bad(self.ldap_groups,
                                   T("Rolle muss Administrator, Operator oder ReadOnly sein (Gruppe {0}).").format(g))
                    groups.append((g, role))
            out["ldap"] = {"server": self.ldap_server.text(), "port": int(port), "base": self.ldap_base.text(),
                           "bind_dn": self.ldap_bind.text(), "bind_pw": self.ldap_bindpw.text(),
                           "user_attr": self.ldap_userattr.text(), "group_attr": self.ldap_groupattr.text(),
                           "groups": groups}
        if self.cert_box.isChecked():
            if not self.cert_pem.strip():
                return bad(None, T("HTTPS-Zertifikat: erst eine PEM-Datei wählen."))
            out["cert"] = {"pem": self.cert_pem}
        if self.hard_box.isChecked():
            out["hard"] = {"ipmi": self.hard_ipmi.isChecked(), "ssdp": self.hard_ssdp.isChecked(),
                           "snmp": self.hard_snmp.isChecked(), "lockout": self.hard_lock.value(),
                           "lockout_min": self.hard_lock_min.value(), "timeout_min": self.hard_tmo.value()}
            if not any(out["hard"][k] for k in ("ipmi", "ssdp", "snmp", "lockout", "timeout_min")):
                return bad(None, T("Härtung: mindestens eine Einstellung wählen."))
        return out

    def services_run(self):
        services = self.services_dict()
        if services is None:
            return
        if not services:
            QMessageBox.warning(self, T("Dienste"), T("Kein Abschnitt angehakt. Oben mindestens einen aktivieren."))
            return
        rows = [(ip, pw or self.svc_pass.text()) for ip, pw in self.reachable_rows()]
        if not rows:
            QMessageBox.warning(self, T("Dienste"), T("Die Serverliste („Neuware“) ist leer."))
            return
        dry = self.svc_dry.isChecked()
        names = {"ntp": "NTP", "syslog": "Syslog", "ldap": "LDAP", "cert": T("HTTPS-Zertifikat"), "hard": T("Härtung")}
        chosen = ", ".join(names[k] for k in ("ntp", "syslog", "ldap", "cert", "hard") if k in services)
        if not dry:
            info = T("Dienste: {0}").format(chosen) + "\n\n" + T(
                "Es werden Verwaltungseinstellungen der BMCs geändert. Eine falsche LDAP- oder "
                "Zertifikatseinstellung kann die Anmeldung erschweren; der lokale Administrator bleibt "
                "aber bestehen.")
            if services.get("hard", {}).get("ipmi"):
                info += "\n\n" + T("Härtung: Nach dem Abschalten von IPMI geht die Textkonsole (SOL) bei Supermicro "
                                    "nicht mehr, ebenso Überwachung per IPMI.")
            if not self.confirm(T("Scharf ausführen?"),
                                T('Auf {0} Servern werden jetzt wirklich Dienste eingerichtet.').format(len(rows)),
                                info, len(rows)):
                return
        self.svc_log.clear()
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.svc_say(T('— {0}, {1} Server, {2} —').format(mode, len(rows), chosen), "gray")
        self.svc_worker = ServicesEngineThread(
            self, rows=rows, cur_user=self.svc_user.text().strip(), services=services, dry=dry,
            vendor=self.svc_vendor.currentData() or None)
        self.svc_worker.line.connect(self.svc_show_line)
        self.svc_worker.result.connect(self.svc_done)
        self.svc_start.setEnabled(False)
        self.svc_stop.setEnabled(True)
        self.svc_status.setText(T("Läuft …"))
        self.svc_worker.start()

    def svc_say(self, text, color=None, bold=False):
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.svc_log.append(f'<span style="{style}">{html.escape(text)}</span>')

    def svc_show_line(self, line, kind=""):
        self.svc_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")

    def svc_abort(self):
        if self.svc_worker and self.svc_worker.isRunning():
            self.svc_worker.requestInterruption()
            self.svc_stop.setEnabled(False)
            self.svc_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def svc_done(self, code, *_):
        self.svc_start.setEnabled(True)
        self.svc_stop.setEnabled(False)
        self.svc_status.setText(T("Fertig ohne Fehler") if code == 0 else T('Beendet mit Fehlern (Code {0})').format(code))

    def svc_mode_changed(self, *_):
        dry = self.svc_dry.isChecked()
        self.svc_start.setText(T("Trockenlauf für alle Server der Liste") if dry
                               else T("Scharf auf allen Servern der Liste setzen"))
        self.svc_start.setStyleSheet("" if dry else LIVE_CSS)

    def export_services(self):
        return {
            "vendor": self.svc_vendor.currentData() or "", "user": self.svc_user.text(), "pass": self.svc_pass.text(),
            "ntp_on": self.ntp_box.isChecked(), "ntp1": self.ntp1.text(), "ntp2": self.ntp2.text(),
            "tz": self.ntp_tz.text(), "syslog_on": self.syslog_box.isChecked(),
            "syslog_server": self.syslog_server.text(), "syslog_port": self.syslog_port.text(),
            "ldap_on": self.ldap_box.isChecked(), "ldap_server": self.ldap_server.text(),
            "ldap_port": self.ldap_port.text(), "ldap_base": self.ldap_base.text(), "ldap_bind": self.ldap_bind.text(),
            "ldap_bindpw": self.ldap_bindpw.text(), "ldap_userattr": self.ldap_userattr.text(),
            "ldap_groupattr": self.ldap_groupattr.text(),
            "ldap_groups": [(self.ldap_groups.item(r, 0).text() if self.ldap_groups.item(r, 0) else "",
                             self.ldap_groups.item(r, 1).text() if self.ldap_groups.item(r, 1) else "")
                            for r in range(self.ldap_groups.rowCount())],
            "cert_on": self.cert_box.isChecked(), "cert_pem": self.cert_pem, "cert_name": self.cert_name,
            "hard_on": self.hard_box.isChecked(), "hard_ipmi": self.hard_ipmi.isChecked(),
            "hard_ssdp": self.hard_ssdp.isChecked(), "hard_snmp": self.hard_snmp.isChecked(),
            "hard_lock": self.hard_lock.value(), "hard_lock_min": self.hard_lock_min.value(),
            "hard_tmo": self.hard_tmo.value(),
        }

    def import_services(self, s):
        self.svc_vendor.setCurrentIndex(max(0, self.svc_vendor.findData(s["vendor"])))
        self.svc_user.setText(s["user"])
        self.svc_pass.setText(s["pass"])
        self.ntp_box.setChecked(s["ntp_on"])
        self.ntp1.setText(s["ntp1"])
        self.ntp2.setText(s["ntp2"])
        self.ntp_tz.setText(s["tz"])
        self.syslog_box.setChecked(s["syslog_on"])
        self.syslog_server.setText(s["syslog_server"])
        self.syslog_port.setText(s["syslog_port"])
        self.ldap_box.setChecked(s["ldap_on"])
        self.ldap_server.setText(s["ldap_server"])
        self.ldap_port.setText(s["ldap_port"])
        self.ldap_base.setText(s["ldap_base"])
        self.ldap_bind.setText(s["ldap_bind"])
        self.ldap_bindpw.setText(s["ldap_bindpw"])
        self.ldap_userattr.setText(s["ldap_userattr"])
        self.ldap_groupattr.setText(s["ldap_groupattr"])
        for g, role in s["ldap_groups"]:
            r = self.ldap_groups.rowCount()
            self.ldap_groups.insertRow(r)
            self.ldap_groups.setItem(r, 0, QTableWidgetItem(g))
            self.ldap_groups.setItem(r, 1, QTableWidgetItem(role))
        self.cert_box.setChecked(s["cert_on"])
        self.cert_pem = s["cert_pem"]
        self.cert_name = s["cert_name"]
        self.cert_label.setText(self.cert_name or T("keine Datei gewählt"))
        self.hard_box.setChecked(s.get("hard_on", False))
        self.hard_ipmi.setChecked(s.get("hard_ipmi", True))
        self.hard_ssdp.setChecked(s.get("hard_ssdp", True))
        self.hard_snmp.setChecked(s.get("hard_snmp", False))
        self.hard_lock.setValue(s.get("hard_lock", 5))
        self.hard_lock_min.setValue(s.get("hard_lock_min", 15))
        self.hard_tmo.setValue(s.get("hard_tmo", 15))

    # ---------- Zertifikate und Datenordner ----------
    def open_data_dir(self):
        path = i18n.private_dir(i18n.DATA)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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
            self.guard_btn.setText(T("⚠ Eigenschutz: Schutzliste leer"))
            self.guard_btn.setStyleSheet("color:#d9534f;font-weight:bold;")
            self.guard_btn.setToolTip(T("BMC-Steward läuft in einer VM ({0}). Den Host dieser VM in die "
                                        "Schutzliste eintragen, sonst kann er versehentlich neu gestartet "
                                        "werden.").format(g.virt))
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

    @staticmethod
    def address_is_dynamic(name, ip):
        """True, wenn die Karte ihre Adresse per DHCP bekommen hat (dann läuft in dem Netz schon ein
        DHCP-Server). None, wenn es sich nicht feststellen lässt."""
        try:
            if WINDOWS:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"(Get-NetIPAddress -IPAddress {ip} -AddressFamily IPv4).PrefixOrigin"],
                    capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout.strip()
                return "dhcp" in out.lower() if out else None
            out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", name],
                                 capture_output=True, text=True, timeout=3).stdout
            for line in out.splitlines():
                if f" {ip}/" in line:
                    return " dynamic" in line
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return None

    def dhcp_guard(self):
        """Läuft alle paar Sekunden mit: stoppt den DHCP-Server, sobald die Karte kein Staging-Netz mehr ist,
        oder sobald während des Laufs ein fremder DHCP-Server im selben Netz auftaucht (z. B. weil jemand
        den Staging-Switch versehentlich mit dem Produktivnetz verbindet). Die Vorab-Prüfung beim Start
        sieht nur den Zustand zum Start-Zeitpunkt; dieser laufende Check deckt auch spätere Änderungen ab."""
        if not self.dhcp_running():
            return
        if self.default_route_ip() == self.dhcp_ip:
            self.stop_dhcp()
            self.dlog(T("DHCP-Server gestoppt: Über diese Karte läuft jetzt die Standardroute, "
                      "das ist kein getrenntes Staging-Netz mehr."), "#d9534f")
            return
        found = probe_dhcp_servers(self.dhcp_ip, timeout=0.5)
        others = (found or set()) - {self.dhcp_ip}
        if others:
            self.stop_dhcp()
            self.dlog(T('DHCP-Server gestoppt: Im Staging-Netz ist während des Laufs ein weiterer '
                      'DHCP-Server aufgetaucht ({0}). Zwei DHCP-Server im selben Netz würden Geräte '
                      'stören.').format(", ".join(sorted(others))), "#d9534f")

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
            "auto_take": self.auto_take.isChecked(), "lease_taken": sorted(self.lease_taken),
            "rows": [([self.cell(r, c) for c in range(len(i18n.COLS))], self.is_done(r))
                     for r in range(self.table.rowCount())],
            "bios_profile": self.bios_current_profile(), "bios_vendor": self.bios_vendor.currentData() or "",
            "bios_ref": self.bios_ref.currentText(), "bios_live": dict(self.bios_live),
            "bios_new": self.bios_wanted(), "bios_pending": dict(self.bios_pending),
            "services": self.export_services(),
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
        self.auto_take.setChecked(s.get("auto_take", True))
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
        """Zeigt eine zuvor gespeicherte Protokoll-Textdatei an (nur Ansicht, ändert nichts an den Servern)."""
        path, _ = QFileDialog.getOpenFileName(self, T("Protokoll öffnen"), str(HERE),
                                              T("Text (*.log *.txt);;Alle Dateien (*)"))
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            QMessageBox.warning(self, T("Protokoll öffnen"), T('Datei nicht lesbar:\n{0}').format(e))
            return
        self.log.setPlainText(text)
        self.status.setText(T("Protokoll geladen aus {0}").format(path))

    def save_log(self):
        """Schreibt das Protokoll des letzten Laufs in eine Textdatei (enthält keine Passwörter)."""
        text = self.log.toPlainText()
        if not text.strip():
            self.status.setText(T("Das Protokoll ist noch leer."))
            return
        name = "bmc-steward-" + time.strftime("%Y%m%d-%H%M%S") + ".log"
        path, _ = QFileDialog.getSaveFileName(self, T("Protokoll speichern"), str(HERE / name),
                                              T("Text (*.log *.txt);;Alle Dateien (*)"))
        if not path:
            return
        try:
            Path(path).write_text(text + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, T("Protokoll speichern"), T('Datei nicht schreibbar:\n{0}').format(e))
            return
        self.status.setText(T("Protokoll gespeichert in {0}").format(path))




def _excepthook(kind, err, tb):
    """PyQt6 bricht bei unbehandelten Fehlern in Slots ab; stattdessen nur melden."""
    import traceback
    traceback.print_exception(kind, err, tb)

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
    app = QApplication(sys.argv)
    app.setApplicationName("bmc-steward")
    app.setApplicationDisplayName("BMC-Steward")
    app.setDesktopFileName("bmc-steward")
    app.setWindowIcon(app_icon())
    global MAIN
    MAIN = Window()
    MAIN.show()
    app.aboutToQuit.connect(lambda: MAIN.stop_scanner())
    code = app.exec()
    MAIN.stop_scanner()
    return code


if __name__ == "__main__":
    raise SystemExit(run())

