"""BMC-Steward: Reiter „Testlabor" - startet, überwacht und stoppt die virtuelle
BMC-Flotte (tools/vbmc/vbmc.py) zum Testen ohne echte Hardware."""
import shutil
import sys
from pathlib import Path

from PyQt6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QProcess, QTimer, Qt

from .i18n import HERE, T
from .core import WINDOWS

# Reihenfolge und Beschriftung der wählbaren virtuellen Hersteller
VBMC_VENDORS = [("dell", "Dell (iDRAC)"), ("hpe", "HPE (iLO 5/6)"),
                ("lenovo", "Lenovo (XCC)"), ("smc", "Supermicro"),
                ("hpe_old", "HPE (iLO 3/4)")]


def _vbmc_script():
    """Pfad zu tools/vbmc/vbmc.py oder None (z. B. in der gebauten .exe nicht enthalten)."""
    cand = HERE / "tools" / "vbmc" / "vbmc.py"
    return cand if cand.exists() else None


class VbmcTabMixin:
    """Reiter „Testlabor" des Hauptfensters."""

    def build_vbmc(self):
        self.vbmc_script = _vbmc_script()
        self.vbmc_proc = None

        intro = QLabel(T("Startet virtuelle Dell/HPE/Lenovo/Supermicro-Server, die sich wie echte BMCs "
                         "verhalten - zum Testen und Vorführen ohne Hardware. Die hier gezeigten Adressen "
                         "trägt man im Haupt-Reiter als IP:PORT ein."))
        intro.setWordWrap(True)

        # --- Flotte zusammenstellen ---
        box = QGroupBox(T("Flotte zusammenstellen"))
        f = QFormLayout(box)
        self.vbmc_checks = {}
        vrow = QHBoxLayout()
        for key, label in VBMC_VENDORS:
            cb = QCheckBox(label)
            cb.setChecked(key in ("dell", "hpe", "lenovo", "smc"))
            self.vbmc_checks[key] = cb
            vrow.addWidget(cb)
        vrow.addStretch()
        vwrap = QWidget()
        vwrap.setLayout(vrow)
        f.addRow(T("Hersteller"), vwrap)

        self.vbmc_count = QSpinBox()
        self.vbmc_count.setRange(1, 50)
        self.vbmc_count.setToolTip(T("Wie viele Server je angehaktem Hersteller."))
        f.addRow(T("Anzahl je Hersteller"), self.vbmc_count)

        self.vbmc_base = QLineEdit("127.9.0")
        self.vbmc_base.setToolTip(T("Basis für die automatischen Loopback-Adressen, z. B. 127.9.0 -> 127.9.0.11, .12, ..."))
        f.addRow(T("Basis-Adresse"), self.vbmc_base)

        self.vbmc_port = QSpinBox()
        self.vbmc_port.setRange(1, 65535)
        self.vbmc_port.setValue(8443)
        self.vbmc_port.setToolTip(T("Redfish-Port. Port 443 bräuchte Root; mit 8443 geht es ohne."))
        f.addRow(T("Redfish-Port"), self.vbmc_port)

        self.vbmc_console = QCheckBox(T("UEFI-Konsole aktivieren (echtes SSH + IPMI, braucht Root)"))
        self.vbmc_console.setToolTip(T("Zusätzlich SSH (22), IPMI-SOL (623) und Redfish auf 443. Diese Ports "
                                       "verlangen Root-Rechte und die Pakete paramiko + pyghmi. Der Start "
                                       "läuft dann über eine Rechteabfrage (pkexec)."))
        f.addRow("", self.vbmc_console)

        # --- Knöpfe ---
        self.vbmc_start_btn = QPushButton(T("Flotte starten"))
        self.vbmc_start_btn.setMinimumHeight(30)
        self.vbmc_start_btn.clicked.connect(self.vbmc_start)
        self.vbmc_stop_btn = QPushButton(T("Flotte stoppen"))
        self.vbmc_stop_btn.setEnabled(False)
        self.vbmc_stop_btn.clicked.connect(self.vbmc_stop)
        btns = QHBoxLayout()
        btns.addWidget(self.vbmc_start_btn)
        btns.addWidget(self.vbmc_stop_btn)
        btns.addStretch()

        self.vbmc_status = QLabel()
        self.vbmc_status.setWordWrap(True)

        self.vbmc_log = QPlainTextEdit()
        self.vbmc_log.setReadOnly(True)
        self.vbmc_log.setFont(QFont("monospace"))
        self.vbmc_log.setPlaceholderText(T("Ausgabe der Flotte (Adressen, Werks-Logins, Statusmeldungen) …"))

        lay = QVBoxLayout()
        lay.addWidget(intro)
        lay.addWidget(box)
        lay.addLayout(btns)
        lay.addWidget(self.vbmc_status)
        lay.addWidget(self.vbmc_log, 1)
        page = QWidget()
        page.setLayout(lay)

        if self.vbmc_script is None:
            self.vbmc_start_btn.setEnabled(False)
            self.vbmc_set_status(T("tools/vbmc/vbmc.py nicht gefunden. Der Reiter funktioniert nur, wenn "
                                   "BMC-Steward aus dem Quellcode läuft."), True)
        else:
            self.vbmc_set_status(T("Bereit."))
        return page

    # ---------- Bedienung ----------
    def vbmc_set_status(self, text, err=False):
        self.vbmc_status.setText(text)
        self.vbmc_status.setStyleSheet("color: #c9302c;" if err else "color: #2e9e4f;")

    def vbmc_running(self):
        return self.vbmc_proc is not None and self.vbmc_proc.state() != QProcess.ProcessState.NotRunning

    def vbmc_start(self):
        if self.vbmc_running() or self.vbmc_script is None:
            return
        specs = []
        for key, cb in self.vbmc_checks.items():
            if cb.isChecked():
                specs += [key] * self.vbmc_count.value()
        if not specs:
            self.vbmc_set_status(T("Bitte mindestens einen Hersteller anhaken."), True)
            return

        args = [str(self.vbmc_script), *specs,
                "--base", self.vbmc_base.text().strip() or "127.9.0",
                "--port", str(self.vbmc_port.value())]
        program, pre = sys.executable, []
        if self.vbmc_console.isChecked():
            args.append("--console")
            if not WINDOWS and shutil.which("pkexec"):
                program, pre = "pkexec", [sys.executable]   # grafische Rechteabfrage
            else:
                cmd = "sudo python3 " + " ".join([str(self.vbmc_script), *specs,
                      "--base", self.vbmc_base.text().strip() or "127.9.0",
                      "--port", str(self.vbmc_port.value()), "--console"])
                self.vbmc_set_status(T("Für die Konsole sind Root-Rechte nötig; pkexec ist nicht verfügbar. "
                                       "Bitte im Terminal starten:") + "\n  " + cmd, True)
                return

        self.vbmc_log.clear()
        self.vbmc_proc = QProcess(self)
        self.vbmc_proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.vbmc_proc.readyReadStandardOutput.connect(self.vbmc_read)
        self.vbmc_proc.finished.connect(self.vbmc_finished)
        self.vbmc_proc.errorOccurred.connect(self.vbmc_error)
        self.vbmc_proc.start(program, pre + args)

        self.vbmc_start_btn.setEnabled(False)
        self.vbmc_stop_btn.setEnabled(True)
        for w in (*self.vbmc_checks.values(), self.vbmc_count, self.vbmc_base,
                  self.vbmc_port, self.vbmc_console):
            w.setEnabled(False)
        self.vbmc_set_status(T("Flotte läuft. Adressen stehen unten; mit „Flotte stoppen“ beenden."))

    def vbmc_stop(self):
        if not self.vbmc_running():
            return
        self.vbmc_stop_btn.setEnabled(False)
        self.vbmc_proc.terminate()      # SIGTERM -> vbmc räumt auf (inkl. IPMI-Kindprozesse)
        QTimer.singleShot(4000, lambda: self.vbmc_proc.kill()
                          if self.vbmc_running() else None)

    def vbmc_read(self):
        data = bytes(self.vbmc_proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.vbmc_log.appendPlainText(data.rstrip("\n"))

    def vbmc_error(self, _err):
        self.vbmc_set_status(T("Start fehlgeschlagen: {0}").format(self.vbmc_proc.errorString()), True)

    def vbmc_finished(self, *_):
        self.vbmc_start_btn.setEnabled(self.vbmc_script is not None)
        self.vbmc_stop_btn.setEnabled(False)
        for w in (*self.vbmc_checks.values(), self.vbmc_count, self.vbmc_base,
                  self.vbmc_port, self.vbmc_console):
            w.setEnabled(True)
        self.vbmc_set_status(T("Flotte gestoppt."))

    def vbmc_shutdown(self):
        """Beim Schließen des Programms die Flotte mitnehmen."""
        if self.vbmc_running():
            self.vbmc_proc.terminate()
            self.vbmc_proc.waitForFinished(3000)
            if self.vbmc_running():
                self.vbmc_proc.kill()
