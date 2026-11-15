"""BMC-Steward: Reiter „Firmware" - Firmware-Updates über den Redfish-UpdateService.

Liest das Firmware-Inventar eines Referenzgeräts, spielt eine Datei per Multipart-/HTTP-Push
oder eine Datei von einer URL per SimpleUpdate ein und verfolgt den Fortschritt. Trockenlauf ist
Vorgabe; scharfe Läufe fragen mit Eingabe der Serverzahl nach. Der Vorgang ist heikel: ein
fehlgeschlagener BMC-Flash kann einen Server unbrauchbar machen - immer erst ein Einzelgerät.
"""
import os
from pathlib import Path

from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                             QProgressBar, QPushButton, QRadioButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtCore import Qt

from . import i18n
from .i18n import HERE, T
from .redfish import FirmwareEngineThread, FirmwareReadThread


class FirmwareTabMixin:
    """Reiter „Firmware" des Hauptfensters."""

    def build_firmware(self):
        self.fw_worker = None
        self.fw_reader = None
        self.fw_file = ""
        self.fw_inventory = []          # zuletzt gelesenes Inventar (mit Pfaden)

        # --- Login ---
        self.fw_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.fw_vendor.addItem(text, key)
        self.fw_user = QLineEdit()
        self.fw_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.fw_pass = self._pw()
        box_login = QGroupBox(T("Login (Werksbenutzer, gilt für alle Server der Liste)"))
        box_login.setToolTip(T("Das Update läuft über alle Server der Serverliste („Neuware“); grüne unter "
                               "ihrer neuen Adresse. Angemeldet wird mit dem Werksbenutzer."))
        f = QFormLayout(box_login)
        self.tip_row(f, T("Hersteller"), self.fw_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ fragt jedes Gerät."))
        self.tip_row(f, T("Benutzer"), self.fw_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet."))
        self.tip_row(f, T("Passwort"), self._with_eye(self.fw_pass),
                     T("Fällt für Zeilen ein, deren Spalte „Passwort“ in der Serverliste leer ist."))

        # --- Referenzgerät + Inventar ---
        inv_box = QGroupBox(T("Firmware-Inventar (Referenzgerät)"))
        inv_box.setToolTip(T("Liest nur lesend die vorhandenen Komponenten und Versionen eines Geräts. "
                             "Eine Auswahl in der Tabelle bestimmt, welche Komponenten das Update treffen "
                             "soll (nichts markiert = der Hersteller entscheidet selbst)."))
        iv = QVBoxLayout(inv_box)
        self.fw_ref_ip = QComboBox()
        self.fw_ref_ip.setEditable(True)
        self.fw_ref_ip.setToolTip(T("Adresse eines Geräts zum Auslesen. Vorschläge aus der Serverliste („Neuware“)."))
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.clicked.connect(self.fw_refresh_ips)
        read_btn = QPushButton(T("Inventar lesen"))
        read_btn.clicked.connect(self.fw_read_inventory)
        iprow = QHBoxLayout()
        iprow.addWidget(QLabel(T("Adresse")))
        iprow.addWidget(self.fw_ref_ip, 1)
        iprow.addWidget(refresh)
        iprow.addWidget(read_btn)
        iv.addLayout(iprow)
        self.fw_table = QTableWidget(0, 3)
        self.fw_table.setHorizontalHeaderLabels([T("Komponente"), T("Version"), T("Aktualisierbar")])
        self.fw_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.fw_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.fw_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fw_table.setMaximumHeight(170)
        iv.addWidget(self.fw_table)
        self.fw_inv_status = QLabel()
        self.fw_inv_status.setWordWrap(True)
        iv.addWidget(self.fw_inv_status)

        # --- Quelle ---
        src_box = QGroupBox(T("Firmware-Quelle"))
        sv = QVBoxLayout(src_box)
        self.fw_src_file = QRadioButton(T("Datei hochladen (Multipart-/HTTP-Push)"))
        self.fw_src_file.setChecked(True)
        self.fw_src_uri = QRadioButton(T("Von URL laden (SimpleUpdate)"))
        self.fw_src_file.toggled.connect(self.fw_src_changed)
        sv.addWidget(self.fw_src_file)
        self.fw_file_label = QLabel(T("keine Datei gewählt"))
        file_btn = QPushButton(T("Firmware-Datei wählen …"))
        file_btn.clicked.connect(self.fw_pick_file)
        frow = QHBoxLayout()
        frow.addWidget(file_btn)
        frow.addWidget(self.fw_file_label, 1)
        self.fw_file_row = QWidget()
        self.fw_file_row.setLayout(frow)
        sv.addWidget(self.fw_file_row)
        sv.addWidget(self.fw_src_uri)
        self.fw_uri = QLineEdit()
        self.fw_uri.setPlaceholderText(T("z. B. http://10.20.0.9/firmware/idrac_7.10.exe"))
        self.fw_proto = QComboBox()
        for p in ("HTTP", "HTTPS", "TFTP", "NFS"):
            self.fw_proto.addItem(p, p)
        self.fw_uri_row = QWidget()
        uf = QFormLayout(self.fw_uri_row)
        uf.setContentsMargins(20, 0, 0, 0)
        self.tip_row(uf, T("URL"), self.fw_uri, T("Der BMC lädt die Datei selbst von dieser Adresse."))
        self.tip_row(uf, T("Protokoll"), self.fw_proto, T("Übertragungsweg, mit dem der BMC die Datei holt."))
        sv.addWidget(self.fw_uri_row)

        self.fw_apply = QComboBox()
        self.fw_apply.addItem(T("Sofort übernehmen (BMC startet ggf. neu)"), "immediate")
        self.fw_apply.addItem(T("Erst beim nächsten Neustart"), "onreset")
        af = QFormLayout()
        self.tip_row(af, T("Übernahme"), self.fw_apply,
                     T("Sofort: der BMC flasht und startet ggf. neu. Beim Neustart: wird nur hochgeladen "
                       "und erst beim nächsten Neustart wirksam."))
        sv.addLayout(af)
        self.fw_src_changed()

        warn = QLabel(T("Achtung: Firmware-Updates sind heikel. Ein unterbrochener BMC-Flash kann einen Server "
                        "unbrauchbar machen. Nur passende Pakete des Herstellers verwenden, Strom sichern und "
                        "immer zuerst ein einzelnes Gerät testen."))
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #c9302c;")

        # --- Ausführung ---
        self.fw_dry = QCheckBox(T("Trockenlauf (nichts ändern)"))
        self.fw_dry.setChecked(True)
        self.fw_dry.toggled.connect(self.fw_dry_changed)
        self.fw_start = QPushButton()
        self.fw_start.setMinimumHeight(34)
        self.fw_start.clicked.connect(self.firmware_run)
        self.fw_stop = QPushButton(T("Abbrechen"))
        self.fw_stop.setEnabled(False)
        self.fw_stop.clicked.connect(self.fw_abort)
        self.fw_status = QLabel(T("Bereit"))
        self.fw_progress = QProgressBar()
        self.fw_progress.setRange(0, 100)
        self.fw_progress.setValue(0)
        self.fw_progress.setTextVisible(True)
        act = QHBoxLayout()
        act.addWidget(self.fw_dry)
        act.addStretch()
        act.addWidget(self.fw_stop)
        act.addWidget(self.fw_start)

        self.fw_log = QTextEdit()
        self.fw_log.setReadOnly(True)
        self.fw_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        settings = QWidget()
        sl = QVBoxLayout(settings)
        for w in (box_login, inv_box, src_box, warn):
            sl.addWidget(w)
        sl.addStretch()

        page = QWidget()
        pl = QVBoxLayout(page)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(settings)
        low = QWidget()
        ll = QVBoxLayout(low)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(act)
        ll.addWidget(self.fw_progress)
        ll.addWidget(self.fw_status)
        ll.addWidget(self.fw_log)
        split.addWidget(low)
        split.setSizes([520, 230])
        pl.addWidget(split, 1)

        self.fw_dry_changed()
        self.fw_refresh_ips()
        return page

    # ---------- Bedienung ----------
    def fw_src_changed(self, *_):
        file_mode = self.fw_src_file.isChecked()
        self.fw_file_row.setVisible(file_mode)
        self.fw_uri_row.setVisible(not file_mode)

    def fw_dry_changed(self, *_):
        dry = self.fw_dry.isChecked()
        self.fw_start.setText(T("Trockenlauf für alle Server der Liste") if dry
                              else T("Scharf: Firmware wirklich einspielen"))
        self.fw_start.setStyleSheet("" if dry else "background:#c9302c; color:white; font-weight:bold;")

    def fw_refresh_ips(self):
        keep = self.fw_ref_ip.currentText()
        self.fw_ref_ip.clear()
        self.fw_ref_pws = {}
        for ip, pw in self.reachable_rows():
            self.fw_ref_ip.addItem(ip)
            self.fw_ref_pws[ip] = pw
        if keep:
            self.fw_ref_ip.setEditText(keep)

    def fw_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, T("Firmware-Datei wählen"), str(HERE),
            T("Firmware (*.exe *.bin *.fwpkg *.rom *.d7 *.pm *.pldm *.img);;Alle Dateien (*)"))
        if not path:
            return
        self.fw_file = path
        size = os.path.getsize(path) / (1024 * 1024)
        self.fw_file_label.setText("%s  (%.1f MB)" % (os.path.basename(path), size))

    def fw_read_inventory(self):
        ip = self.fw_ref_ip.currentText().strip()
        if not ip:
            self.fw_inv_status.setText(T("Bitte eine Adresse eintragen."))
            return
        if self.fw_reader and self.fw_reader.isRunning():
            return
        pw = self.fw_ref_pws.get(ip, "") if hasattr(self, "fw_ref_pws") else ""
        pw = pw or self.fw_pass.text() or self.cur_pass.text()
        self.fw_inv_status.setText(T("Lese Inventar …"))
        self.fw_reader = FirmwareReadThread(self, ip, self.fw_user.text().strip(), pw,
                                            self.fw_vendor.currentData() or "")
        self.fw_reader.done.connect(self.fw_inventory_done)
        self.fw_reader.start()

    def fw_inventory_done(self, inv, vendor, err):
        if inv is None:
            self.fw_table.setRowCount(0)
            self.fw_inventory = []
            self.fw_inv_status.setText(T("Fehler: {0}").format(err))
            return
        self.fw_inventory = inv
        self.fw_table.setRowCount(len(inv))
        for r, c in enumerate(inv):
            self.fw_table.setItem(r, 0, QTableWidgetItem(c["name"] or c["id"]))
            self.fw_table.setItem(r, 1, QTableWidgetItem(c["version"]))
            self.fw_table.setItem(r, 2, QTableWidgetItem("✔" if c["updateable"] else "—"))
        self.fw_inv_status.setText(T("{0} Komponenten gelesen ({1}). Zeilen markieren, um das Update auf sie "
                                     "zu begrenzen.").format(len(inv), vendor))

    def fw_targets(self):
        """Pfade der in der Tabelle markierten Komponenten (leer = alle / automatisch)."""
        rows = sorted({i.row() for i in self.fw_table.selectedIndexes()})
        return [self.fw_inventory[r]["path"] for r in rows if r < len(self.fw_inventory)]

    def firmware_config(self):
        """Sammelt die Eingaben; None bei Fehler (mit Meldung)."""
        if self.fw_src_file.isChecked():
            if not self.fw_file or not os.path.isfile(self.fw_file):
                QMessageBox.warning(self, T("Firmware"), T("Bitte eine Firmware-Datei wählen."))
                return None
            cfg = {"source": "file", "file": self.fw_file}
        else:
            if not self.fw_uri.text().strip():
                QMessageBox.warning(self, T("Firmware"), T("Bitte eine URL eintragen."))
                return None
            cfg = {"source": "uri", "uri": self.fw_uri.text().strip(), "proto": self.fw_proto.currentData()}
        cfg["apply"] = self.fw_apply.currentData()
        cfg["targets"] = self.fw_targets()
        return cfg

    def firmware_run(self):
        cfg = self.firmware_config()
        if cfg is None:
            return
        rows = [(ip, pw or self.fw_pass.text()) for ip, pw in self.reachable_rows()]
        if not rows:
            QMessageBox.warning(self, T("Firmware"), T("Die Serverliste („Neuware“) ist leer."))
            return
        dry = self.fw_dry.isChecked()
        src = (T("Datei {0}").format(os.path.basename(cfg["file"])) if cfg["source"] == "file"
               else T("URL {0}").format(cfg["uri"]))
        if not dry:
            info = T("Es wird Firmware auf die BMCs geschrieben. Ein unterbrochener Flash kann einen Server "
                     "unbrauchbar machen. Quelle: {0}, Übernahme: {1}.").format(
                src, T("sofort") if cfg["apply"] == "immediate" else T("beim Neustart"))
            if not self.confirm(T("Firmware wirklich einspielen?"),
                                T("Auf {0} Servern wird jetzt wirklich Firmware eingespielt.").format(len(rows)),
                                info, len(rows)):
                return
        self.fw_log.clear()
        self.fw_progress.setValue(0)
        mode = T("Trockenlauf") if dry else T("Scharfer Lauf")
        self.fw_say(T('— {0}, {1} Server, {2} —').format(mode, len(rows), src), "gray")
        self.fw_worker = FirmwareEngineThread(
            self, rows=rows, cur_user=self.fw_user.text().strip(), cfg=cfg, dry=dry,
            vendor=self.fw_vendor.currentData() or None)
        self.fw_worker.line.connect(self.fw_show_line)
        self.fw_worker.result.connect(self.fw_done)
        self.fw_start.setEnabled(False)
        self.fw_stop.setEnabled(True)
        self.fw_status.setText(T("Läuft …"))
        self.fw_worker.start()

    def fw_say(self, text, color=None, bold=False):
        import html
        style = "white-space:pre-wrap;" + (f"color:{color};" if color else "")
        if bold:
            style += "font-weight:bold;"
        self.fw_log.append(f'<span style="{style}">{html.escape(text)}</span>')

    def fw_show_line(self, line, kind=""):
        self.fw_say(line, {"err": "#d9534f", "ok": "#3fa45b"}.get(kind), bold=kind == "head")
        # Fortschritt aus „… 42%" in die Leiste spiegeln
        s = line.strip()
        if s.endswith("%"):
            try:
                self.fw_progress.setValue(int(s.rsplit(None, 1)[-1].rstrip("%")))
            except ValueError:
                pass

    def fw_abort(self):
        if self.fw_worker and self.fw_worker.isRunning():
            self.fw_worker.requestInterruption()
            self.fw_stop.setEnabled(False)
            self.fw_status.setText(T("Wird nach dem aktuellen Server abgebrochen …"))

    def fw_done(self, code, *_):
        self.fw_start.setEnabled(True)
        self.fw_stop.setEnabled(False)
        self.fw_progress.setValue(100 if code == 0 else self.fw_progress.value())
        self.fw_status.setText(T("Fertig ohne Fehler") if code == 0
                               else T('Beendet mit Fehlern (Code {0})').format(code))
