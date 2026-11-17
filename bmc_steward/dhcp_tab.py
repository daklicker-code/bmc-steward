"""BMC-Steward: Reiter „DHCP-Server" - vorübergehender DHCP-Server (dnsmasq unter Linux, eingebaut unter
Windows) für ein getrenntes Staging-Netz, um neue Geräte zu finden und in die Serverliste zu übernehmen."""
import html
import ipaddress
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFormLayout,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtCore import QProcess, QTimer, Qt

from .applog import LOG
from .core import WINDOWS, ipv4
from .dhcp import DhcpThread, probe_dhcp_servers
from .i18n import T


class DhcpTabMixin:
    """Reiter „DHCP-Server" des Hauptfensters."""

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
        self.auto_take.setChecked(False)
        self.auto_take.setToolTip(T("Trägt neu gefundene Geräte von selbst in die Serverliste ein (Aktuelle IP).\n"
                                    "Es wird nichts konfiguriert: Neue IP, Hostname und der Start bleiben Handarbeit.\n"
                                    "Ein Gerät kommt nur einmal in die Liste; wer eine Zeile löscht, bekommt sie nicht\n"
                                    "von selbst zurück. Während eines Laufs wartet die Übernahme bis zum Ende.\n"
                                    "Standardmäßig aus: ohne Haken übernimmt nur der Knopf rechts."))
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
        LOG.info("DHCP: " + text, color)

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
        """Geräteliste anzeigen: Zeilen aus (IP, MAC, Name).

        Laufende vBMC-Simulator-Instanzen werden herausgefiltert: Wenn der vBMC im Router-Modus
        auf derselben physischen NIC laueft wie ein echter Server, erscheinen beide in derselben
        DHCP-Lease-Liste. Die Simulator-MACs werden ausgeblendet, damit nur echte Hardware sichtbar ist."""
        vbmc_macs = self.vbmc_running_macs()
        if vbmc_macs:
            rows = [(ip, mac, name) for ip, mac, name in rows
                    if mac.lower() not in vbmc_macs]
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
