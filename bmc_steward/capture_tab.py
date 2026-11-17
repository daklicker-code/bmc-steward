"""BMC-Steward: Reiter „Aufnahme“ - Redfish-Baum eines Geräts lesen und speichern, zwei Aufnahmen vergleichen.

Der Reiter liest nur (siehe capture.py). Oben: ein Gerät aufnehmen, auf Wunsch mit geschwärzten persönlichen
Daten; unten: zwei gespeicherte Aufnahmen desselben Geräts vergleichen (z. B. vor/nach einem Firmware-Update).
"""
import time
from pathlib import Path

from PyQt6.QtCore import QThread, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontDatabase
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from . import capture, i18n
from .applog import LOG
from .i18n import T


class CaptureThread(QThread):
    """Nimmt ein Gerät auf (nur lesend), schwärzt und speichert."""
    line = pyqtSignal(str)
    progress = pyqtSignal(int)
    done = pyqtSignal(object, object, str)          # Aufnahme oder None, Schwärzungsbericht, Fehlertext

    def __init__(self, parent, ip, user, pw, vendor, out, do_redact):
        super().__init__(parent)
        self.args = (ip, user, pw, out, vendor or None, do_redact)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ip, user, pw, out, vendor, do_redact = self.args
        try:
            cap, rep, err = capture.run_capture(
                ip, user, pw, out, vendor, do_redact, log=lambda text, *a: self.line.emit(str(text)),
                stopped=lambda: self._stop, progress=lambda n, path: self.progress.emit(n))
        except Exception as e:                      # pragma: no cover - Sicherheitsnetz
            cap, rep, err = None, {}, T('Unerwarteter Fehler: {0!r}').format(e)
        self.done.emit(cap, rep, err)


class CaptureTabMixin:
    """Reiter „Aufnahme“ des Hauptfensters."""

    def build_capture(self):
        self.cap_thread = None
        self.cap_last_dir = None
        self.cap_diff_text = ""
        self.cap_ref_pws = {}
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        # --- Gerät aufnehmen ---
        box = QGroupBox(T("Gerät aufnehmen (nur lesend)"))
        box.setToolTip(T("Liest den Redfish-Baum eines Geräts (nur GET) und speichert ihn im DMTF-Mockup-Format. "
                         "Daraus entstehen Testdaten für BMC-Steward und den Testlabor-Simulator, ein "
                         "Fähigkeitsbericht und die Grundlage für den Vergleich nach einem Firmware-Update."))
        self.cap_vendor = QComboBox()
        for text, key in i18n.VENDORS:
            self.cap_vendor.addItem(text, key)
        self.cap_user = QLineEdit()
        self.cap_user.setPlaceholderText(T("automatisch je Hersteller"))
        self.cap_pass = self._pw()
        self.cap_ip = QComboBox()
        self.cap_ip.setEditable(True)
        self.cap_ip.setToolTip(T("Adresse des Geräts. Vorschläge aus der Serverliste („Neuware“)."))
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.clicked.connect(self.capture_refresh_ips)
        iprow = QWidget()
        ih = QHBoxLayout(iprow)
        ih.setContentsMargins(0, 0, 0, 0)
        ih.addWidget(self.cap_ip, 1)
        ih.addWidget(refresh)
        self.cap_dir = QLineEdit(str(i18n.DATA / "aufnahmen"))
        self.cap_dir.setToolTip(T("Übergeordneter Ordner. Jede Aufnahme bekommt darin einen eigenen, neuen Unterordner."))
        pick = QPushButton(T("Wählen …"))
        pick.clicked.connect(lambda: self.capture_pick(self.cap_dir))
        dirrow = QWidget()
        dh = QHBoxLayout(dirrow)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.addWidget(self.cap_dir, 1)
        dh.addWidget(pick)
        self.cap_redact = QCheckBox(T("Persönliche Daten schwärzen (empfohlen)"))
        self.cap_redact.setChecked(True)
        self.cap_redact.setToolTip(T("Ersetzt Seriennummern, Service-Tags, UUIDs, MAC- und IP-Adressen, Hostnamen, "
                                     "eigene Benutzernamen, Zertifikate und Schlüssel durch Platzhalter. Die "
                                     "Struktur bleibt erhalten. Vor dem Weitergeben trotzdem kurz in die Dateien sehen."))
        form = QFormLayout()
        self.tip_row(form, T("Hersteller"), self.cap_vendor,
                     T("Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ fragt das Gerät."))
        self.tip_row(form, T("Benutzer"), self.cap_user,
                     T("Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet."))
        self.tip_row(form, T("Passwort"), self._with_eye(self.cap_pass),
                     T("Fällt für Geräte ein, deren Spalte „Passwort“ in der Serverliste leer ist."))
        form.addRow(QLabel(T("Adresse")), iprow)
        form.addRow(QLabel(T("Zielordner")), dirrow)
        form.addRow(self.cap_redact)
        self.cap_start = QPushButton(T("Aufnehmen"))
        self.cap_start.clicked.connect(self.capture_start)
        self.cap_stop = QPushButton(T("Abbrechen"))
        self.cap_stop.setEnabled(False)
        self.cap_stop.clicked.connect(lambda: self.cap_thread and self.cap_thread.stop())
        self.cap_open = QPushButton(T("Ordner öffnen"))
        self.cap_open.setEnabled(False)
        self.cap_open.clicked.connect(self.capture_open_folder)
        btns = QHBoxLayout()
        btns.addWidget(self.cap_start)
        btns.addWidget(self.cap_stop)
        btns.addWidget(self.cap_open)
        btns.addStretch(1)
        self.cap_status = QLabel()
        self.cap_status.setWordWrap(True)
        self.cap_out = QPlainTextEdit()
        self.cap_out.setReadOnly(True)
        self.cap_out.setFont(mono)
        bv = QVBoxLayout(box)
        bv.addLayout(form)
        bv.addLayout(btns)
        bv.addWidget(self.cap_status)
        bv.addWidget(self.cap_out, 1)

        # --- Vergleichen ---
        cmp_box = QGroupBox(T("Zwei Aufnahmen vergleichen"))
        cmp_box.setToolTip(T("Zeigt, was zwischen zwei Aufnahmen desselben Geräts weggefallen oder nur noch lesbar "
                             "ist (z. B. nach einem Firmware-Update) und was neu ist. Messwerte und Zustände "
                             "werden nicht verglichen."))
        self.cap_a = QLineEdit()
        self.cap_b = QLineEdit()
        rows = QFormLayout()
        for label, edit in ((T("Vorher (A)"), self.cap_a), (T("Nachher (B)"), self.cap_b)):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            b = QPushButton(T("Wählen …"))
            b.clicked.connect(lambda _=False, e=edit: self.capture_pick(e))
            h.addWidget(edit, 1)
            h.addWidget(b)
            rows.addRow(QLabel(label), w)
        self.cap_cmp = QPushButton(T("Vergleichen"))
        self.cap_cmp.clicked.connect(self.capture_compare)
        self.cap_save = QPushButton(T("Als Text speichern …"))
        self.cap_save.setEnabled(False)
        self.cap_save.clicked.connect(self.capture_save_diff)
        cb = QHBoxLayout()
        cb.addWidget(self.cap_cmp)
        cb.addWidget(self.cap_save)
        cb.addStretch(1)
        self.cap_diff = QPlainTextEdit()
        self.cap_diff.setReadOnly(True)
        self.cap_diff.setFont(mono)
        cv = QVBoxLayout(cmp_box)
        cv.addLayout(rows)
        cv.addLayout(cb)
        cv.addWidget(self.cap_diff, 1)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(box, 3)
        lay.addWidget(cmp_box, 2)
        return page

    # ---------- Aufnehmen ----------
    def capture_refresh_ips(self):
        keep = self.cap_ip.currentText()
        self.cap_ip.clear()
        self.cap_ref_pws = {}
        for ip, pw in self.reachable_rows():
            self.cap_ip.addItem(ip)
            self.cap_ref_pws[ip] = pw
        if keep:
            self.cap_ip.setEditText(keep)

    def capture_pick(self, edit):
        start = edit.text().strip() or str(i18n.DATA)
        folder = QFileDialog.getExistingDirectory(self, T("Ordner wählen"), start)
        if folder:
            edit.setText(folder)

    def capture_start(self):
        ip = self.cap_ip.currentText().strip()
        if not ip:
            QMessageBox.warning(self, T("Aufnahme"), T("Bitte eine Adresse eintragen."))
            return
        redact = self.cap_redact.isChecked()
        if not redact:
            ask = QMessageBox.question(
                self, T("Aufnahme"),
                T("Ohne Schwärzen enthält die Aufnahme Seriennummern, Adressen, Hostnamen und Zertifikate des Geräts. "
                  "Nur für den eigenen Gebrauch speichern. Trotzdem ohne Schwärzen aufnehmen?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if ask != QMessageBox.StandardButton.Yes:
                return
        vendor = self.cap_vendor.currentData() or ""
        pw = self.cap_ref_pws.get(ip, "") or self.cap_pass.text() or self.cur_pass.text()
        base = Path(self.cap_dir.text().strip() or str(i18n.DATA / "aufnahmen"))
        out = base / ("%s-%s" % (vendor or "geraet", time.strftime("%Y%m%d-%H%M%S")))
        self.cap_out.clear()
        self.cap_status.setText(T("Lese …"))
        self.cap_start.setEnabled(False)
        self.cap_stop.setEnabled(True)
        self.cap_open.setEnabled(False)
        self.cap_thread = CaptureThread(self, ip, self.cap_user.text().strip(), pw, vendor, out, redact)
        self.cap_thread.line.connect(self.cap_out.appendPlainText)
        self.cap_thread.progress.connect(lambda n: self.cap_status.setText(T("{0} Ressourcen gelesen …").format(n)))
        self.cap_thread.done.connect(self.capture_done)
        LOG.info(T("Aufnahme gestartet ({0})").format(vendor or T("Automatisch erkennen")))
        self.cap_thread.start()

    def capture_done(self, cap, rep, err):
        self.cap_start.setEnabled(True)
        self.cap_stop.setEnabled(False)
        thread, self.cap_thread = self.cap_thread, None
        out = thread.args[3] if thread else None
        if thread:
            thread.wait(2000)
        if cap is None:
            self.cap_status.setText(T("Aufnahme fehlgeschlagen."))
            self.cap_out.appendPlainText(err)
            return
        self.cap_last_dir = out
        self.cap_open.setEnabled(True)
        text = capture.report_text(cap)
        if rep:
            text += "\n\n" + T("Geschwärzt: {0}").format(", ".join("%s %d" % kv for kv in sorted(rep.items())))
        elif not cap["meta"].get("redacted"):
            text += "\n\n" + T("NICHT geschwärzt: Die Aufnahme enthält persönliche Daten des Geräts.")
        self.cap_out.setPlainText(text + "\n\n" + T("Gespeichert in {0}").format(out))
        self.cap_status.setText(T("Fertig: {0} Ressourcen gespeichert.").format(len(cap["tree"])))
        for edit in (self.cap_a, self.cap_b):                    # gleich als Vergleichsseite vormerken
            if not edit.text().strip():
                edit.setText(str(out))
                break

    def capture_open_folder(self):
        if self.cap_last_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.cap_last_dir)))

    # ---------- Vergleichen ----------
    def capture_compare(self):
        a, b = self.cap_a.text().strip(), self.cap_b.text().strip()
        if not a or not b:
            QMessageBox.warning(self, T("Aufnahme"), T("Bitte beide Ordner wählen."))
            return
        try:
            ca, cb = capture.load_capture(a), capture.load_capture(b)
        except (ValueError, OSError) as e:
            self.cap_diff.setPlainText(str(e))
            self.cap_save.setEnabled(False)
            return
        self.cap_diff_text = capture.diff_text(capture.diff_captures(ca, cb))
        self.cap_diff.setPlainText(self.cap_diff_text)
        self.cap_save.setEnabled(True)

    def capture_save_diff(self):
        name, _ = QFileDialog.getSaveFileName(self, T("Vergleich speichern"), str(i18n.DATA / "vergleich.txt"),
                                              T("Textdatei") + " (*.txt)")
        if name:
            try:
                Path(name).write_text(self.cap_diff_text + "\n", encoding="utf-8")
            except OSError as e:
                QMessageBox.warning(self, T("Aufnahme"), T('Datei nicht schreibbar:\n{0}').format(e))
