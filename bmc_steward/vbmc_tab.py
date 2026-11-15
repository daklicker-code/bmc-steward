"""BMC-Steward: Reiter „Testlabor" - startet, überwacht und stoppt die virtuelle
BMC-Flotte (tools/vbmc/vbmc.py) zum Testen ohne echte Hardware."""
import shutil
import sys
from pathlib import Path

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QProcess, QTimer, Qt

from .i18n import HERE, T
from .core import WINDOWS, local_network

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
            cb.setChecked(False)
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
        self.vbmc_adopt_btn = QPushButton(T("Adressen in Serverliste übernehmen (Reiter 2)"))
        self.vbmc_adopt_btn.setEnabled(False)
        self.vbmc_adopt_btn.setToolTip(T("Trägt die laufenden virtuellen Server mit Adresse (inkl. :Port) und "
                                         "Werks-Passwort in die Serverliste ein und schlägt neue Adressen vor."))
        self.vbmc_adopt_btn.clicked.connect(self.vbmc_adopt)
        self.vbmc_adopt_mode = QComboBox()
        self.vbmc_adopt_mode.addItem(T("Neue IP: echtes Netz (Router) – zum Üben"), "net")
        self.vbmc_adopt_mode.addItem(T("Neue IP: Loopback – scharfe Migration läuft grün"), "loopback")
        self.vbmc_adopt_mode.setToolTip(T("Echtes Netz: Gateway/Subnetz werden erkannt und die neue IP mit "
                                          "deinem Präfix vorgefüllt (ideal für Trockenlauf). Loopback: die neue "
                                          "IP bleibt auf 127.x:Port, damit auch ein scharfer Lauf grün durchläuft."))
        btns = QHBoxLayout()
        btns.addWidget(self.vbmc_start_btn)
        btns.addWidget(self.vbmc_stop_btn)
        btns.addWidget(self.vbmc_adopt_btn)
        btns.addWidget(self.vbmc_adopt_mode)
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

        # Adressen vorab berechnen (wie vbmc: base.(11+index)) - für den Übernahme-Knopf
        base = self.vbmc_base.text().strip() or "127.9.0"
        port = self.vbmc_port.value()
        self.vbmc_servers = [(v, f"{base}.{11 + i}:{port}") for i, v in enumerate(specs)]

        args = [str(self.vbmc_script), *specs, "--base", base, "--port", str(port)]
        program, pre = sys.executable, ["-u"]       # -u: Ausgabe sofort, Liste erscheint live
        if self.vbmc_console.isChecked():
            args.append("--console")
            if not WINDOWS and shutil.which("pkexec"):
                program, pre = "pkexec", [sys.executable, "-u"]   # grafische Rechteabfrage
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
        self.vbmc_adopt_btn.setEnabled(True)
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
        self.vbmc_adopt_btn.setEnabled(False)
        for w in (*self.vbmc_checks.values(), self.vbmc_count, self.vbmc_base,
                  self.vbmc_port, self.vbmc_console):
            w.setEnabled(True)
        self.vbmc_set_status(T("Flotte gestoppt."))

    # Werks-Logins der virtuellen Server (müssen zu tools/vbmc/vbmc.py passen)
    FACTORY = {"dell": ("root", "calvin"), "hpe": ("Administrator", "Admin123!"),
               "hpe_old": ("Administrator", "Admin123!"), "smc": ("ADMIN", "ADMIN"),
               "lenovo": ("USERID", "PASSW0RD")}

    def vbmc_adopt(self):
        """Laufende virtuelle Server in die Serverliste (Reiter 2) eintragen."""
        servers = getattr(self, "vbmc_servers", [])
        if not servers:
            return
        existing = {self.cell(r, 0) for r in range(self.table.rowCount())}
        base = self.vbmc_base.text().strip() or "127.9.0"
        port = self.vbmc_port.value()
        # Modus: echtes Netz (Router) zum Üben oder Loopback (scharfe Migration läuft grün)
        net = local_network() if self.vbmc_adopt_mode.currentData() == "net" else None
        if net:
            mask, gw, new_prefix, new_port = net["mask"], net["gateway"], net["prefix"], ""
            if not self.def_mask.text().strip():
                self.def_mask.setText(mask)
            if not self.def_gw.text().strip():
                self.def_gw.setText(gw)
        else:
            mask, gw, new_prefix, new_port = "255.255.255.0", "", base, f":{port}"
        # Leere Zeilen (z. B. die leere Startzeile) entfernen, damit die Daten oben beginnen
        for r in reversed(range(self.table.rowCount())):
            if not any(self.cell(r, c) for c in (0, 2, 5)):
                self.table.removeRow(r)
        added, logins = 0, {}
        for i, (vendor, addr) in enumerate(servers):
            if addr in existing:
                continue
            user, pw = self.FACTORY.get(vendor, ("", ""))
            logins[vendor] = (user, pw)
            self.add_row()
            r = self.table.rowCount() - 1
            self.table.item(r, 0).setText(addr)                        # Aktuelle IP (mit :Port)
            # Passwort direkt je Zeile eintragen (Werte unterscheiden sich je Hersteller; das eine
            # globale Werkspasswort-Feld oben könnte nur einen davon abdecken).
            self.table.item(r, 1).setText(pw)
            # Neue IP: Präfix vorgefüllt, letzte Ziffer bleibt offen (beim Fallback komplett)
            self.table.item(r, 2).setText(f"{new_prefix}.{str(51 + i) + new_port if not net else ''}")
            self.table.item(r, 3).setText(mask)                        # Subnetzmaske
            self.table.item(r, 4).setText(gw)                          # Gateway
            self.table.item(r, 5).setText(f"vsrv-{vendor}-{i + 1}")    # Hostname-Vorschlag
            added += 1
        # Benutzer bleibt leer: Der Hauptablauf erkennt ihn automatisch je Hersteller (DEFAULT_USER),
        # solange "Werksbenutzer" oben leer bleibt. Passwort steht jetzt direkt je Zeile.
        self.update_count()
        self.touch()
        self.tabs.setCurrentIndex(1)
        if not added:
            return
        hint = ", ".join(f"{v}: {u}/{p}" for v, (u, p) in logins.items())
        if net:
            self.status.setText(T("{0} Server übernommen (Passwörter stehen schon in der Liste). Netz erkannt "
                                  "(Gateway {1}, Maske {2}); bei „Neue IP“ nur noch die letzte Ziffer eintragen. "
                                  "Werks-Login(s): {3}.").format(added, gw or "?", mask, hint))
        else:
            self.status.setText(T("{0} Server übernommen (Loopback, Passwörter stehen schon in der Liste). "
                                  "Werks-Login(s): {1}.").format(added, hint))

    def vbmc_shutdown(self):
        """Beim Schließen des Programms die Flotte mitnehmen."""
        if not self.vbmc_running():
            return
        pid = self.vbmc_proc.processId()
        via_pkexec = self.vbmc_proc.program() == "pkexec"
        self.vbmc_proc.terminate()
        if self.vbmc_proc.waitForFinished(3000):
            return
        if self.vbmc_running() and via_pkexec and not WINDOWS and shutil.which("pkexec"):
            # Mit --console läuft die Flotte dauerhaft als root (Ports < 1024); ein Signal von diesem,
            # nicht privilegierten Prozess aus verhallt wirkungslos. Wie beim DHCP-Server: über pkexec
            # selbst beenden, das darf root-Prozesse signalisieren.
            QProcess.execute("pkexec", ["kill", str(pid)])
            self.vbmc_proc.waitForFinished(3000)
        if self.vbmc_running():
            self.vbmc_proc.kill()
            self.vbmc_proc.waitForFinished(1000)
