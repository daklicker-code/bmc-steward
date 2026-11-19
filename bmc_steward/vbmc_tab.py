"""BMC-Steward: Reiter „Testlabor" - stellt eine virtuelle BMC-Cluster (tools/vbmc/vbmc.py) zusammen,
startet, überwacht und stoppt sie. Drei Netz-Modi:

  loopback  ohne Root, nur dieser PC: Server auf 127.x:Port (wie bisher)
  rz        Konfigraum: isolierter Staging-Switch bmclab0, DHCP über „DHCP-Server“ oder feste Werks-IP
  router    Zuhause: Server hängen an der Kabel-Netzwerkkarte, Adresse vom Router (z. B. FritzBox)

Jeder Server ist einzeln einstellbar (Hersteller, Firmware-Generation, Werkszustand Netz, Werks-Login,
Modell/Seriennummer, belegter Böll-Slot); die Zusammenstellung lässt sich als Szenario speichern und
genauso wieder laden. Gleiche Seriennummer = gleiche MAC = der Router vergibt dieselbe Adresse."""
import ipaddress
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                             QToolButton, QVBoxLayout, QWidget)
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtCore import QProcess, QTimer, QUrl, Qt
from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface

from .applog import LOG, LevelCombo, LogView, open_in_editor
from .i18n import HERE, T
from .core import WINDOWS, local_network

# Muss zu tools/vbmc/vbmc.py passen (GENS / FACTORY)
VENDORS = [("dell", "Böll"), ("hpe", "Häpe"), ("lenovo", "Venolo"), ("smc", "Hypernano")]
GENS = {
    "dell": [("idrac9", "Böll 9"), ("idrac10", "Böll 10"), ("idrac8", "Böll 8"),
             ("idrac8_alt", "Böll 8, Firmware zu alt (< 2.50.50.50)")],
    "hpe": [("ilo6", "Häpe 6"), ("ilo5", "Häpe 5"), ("ilo4", "Häpe 4 (RIBCL)"), ("ilo3", "Häpe 3 (RIBCL, TLS 1.0)")],
    "lenovo": [("xcc2", "Venolo 2"), ("xcc3", "Venolo 3"), ("xcc1", "Venolo 1")],
    "smc": [("x13", "Hypernano 13"), ("x12", "Hypernano 12"), ("x11", "Hypernano 11")],
}
FACTORY = {"dell": ("root", "calvin"), "hpe": ("Administrator", "Admin123!"),
           "lenovo": ("USERID", "PASSW0RD"), "smc": ("ADMIN", "ADMIN")}
SERIAL_PREFIX = {"dell": "CN", "hpe": "CZ", "lenovo": "J", "smc": "S"}
DELL_STATIC = "192.168.0.120"            # Profil Böll ab Werk ohne DHCP

MODES = [("loopback", "Loopback – ohne Root, nur dieser PC (127.x:Port)"),
         ("rz", "Konfigraum – isolierter Staging-Switch (bmclab0)"),
         ("router", "Zuhause/Router – echtes LAN über die Kabel-Netzwerkkarte")]

MODE_INFO = {
    'loopback': 'Die virtuellen Server laufen nur auf diesem PC und lauschen auf 127.x-Adressen mit eigenem Port. Es gibt kein echtes Netz und kein DHCP, Root ist nicht nötig.\nBeispiel: Server 1 ist unter 127.9.0.11:8443 erreichbar; in „Neuware“ trägt man als Aktuelle IP 127.9.0.11:8443 ein. Gut, um Oberfläche und Abläufe schnell auszuprobieren.',
    'rz': 'Ein virtueller, vom restlichen Netz getrennter Staging-Switch „bmclab0“ mit eigener Netzwerkkarte je Server, wie im Konfigraum. Die Server holen sich ihre Adresse vom DHCP-Server des Tools oder haben eine feste Werks-IP.\nBeispiel: Ein Dell mit fester Werks-IP 192.168.0.120 ist sofort unter https://192.168.0.120 erreichbar, ein HPE bekommt nach dem Start des DHCP-Servers z. B. 192.168.0.101. Braucht beim Start einmal Root.',
    'router': 'Die Server hängen mit eigener MAC am echten LAN (Kabel-Netzwerkkarte) und bekommen ihre Adresse vom Router, z. B. einer FritzBox, wo sie als eigene Geräte erscheinen.\nBeispiel: Ein HPE meldet sich als „ILOCZ12345“ beim Router und bekommt 192.168.178.45; das Umstellen auf eine feste Adresse wie 192.168.178.201 läuft wirklich durch. Braucht beim Start einmal Root, WLAN geht nicht.',
}

# Infobox zur Auswahl „Neue IP“ (einfach erklärt)
ADOPT_HELP = (
    "<p><b>Worum geht es?</b> Die virtuellen Server sind wie Spielzeug-Server in deinem Computer. Mit „In "
    "Serverliste übernehmen“ trägst du sie in die Liste „Neuware“ ein, so als wären es echte Server, die du "
    "einrichten willst. Dabei denkt sich das Tool für jeden Server eine <b>neue IP-Adresse</b> aus. Eine "
    "IP-Adresse ist wie die Hausnummer des Servers: Darüber findet man ihn.</p>"
    "<p>Du bestimmst, welche Art von neuer Adresse vorgeschlagen wird:</p>"
    "<p><b>Übungsadressen</b> (alles klappt)<br>Die neue Adresse bleibt auf deinem Computer (127.9.0.51:8443 …). "
    "Das ist, als würde der Server nur in ein anderes Zimmer im selben Haus ziehen: Er verlässt das Haus nie, "
    "deshalb findet das Tool ihn sofort wieder. Du kannst <b>richtig scharf</b> starten, und am Ende steht alles "
    "auf Grün. Gut zum Üben und Vorführen.</p>"
    "<p><b>Aus meinem echten Netz</b> (nur Probelauf)<br>Das Tool schaut, wie dein richtiges Netzwerk aussieht "
    "(Adresse des Routers und Netzmaske) und schlägt Adressen daraus vor. Du trägst nur noch die letzte Zahl ein. "
    "Das ist, als würdest du den Umzug in eine echte Stadt nur auf dem Papier planen. Die Spielzeug-Server können "
    "dort nicht wirklich einziehen, darum geht hier nur der <b>Probelauf</b>: Du siehst, was das Tool tun würde, "
    "aber es wird nichts verändert.</p>"
    "<p><b>Tipp:</b> Nimm die Übungsadressen, wenn du das ganze Tool einmal richtig durchspielen willst. Nimm "
    "das echte Netz, wenn du prüfen willst, ob deine geplanten Adressen und die Netzmaske sinnvoll aussehen.</p>"
    "<p><i>Diese Auswahl gibt es nur im Modus „Loopback“. In den anderen Modi (Konfigraum, Zuhause/Router) "
    "schlägt das Tool die neue Adresse automatisch passend zum Netz vor.</i></p>")

# Start grün (nur bei gefüllter Liste), Stopp rot (nur sichtbar, solange der Cluster läuft)
START_STYLE = ("QPushButton{background:#3fa45b;color:#fff;font-weight:bold;border:1px solid #2e8a47;"
               "border-radius:3px;padding:5px 14px}QPushButton:hover{background:#36934f}")
STOP_STYLE = ("QPushButton{background:#c9302c;color:#fff;font-weight:bold;border:1px solid #a52623;"
              "border-radius:3px;padding:5px 14px}QPushButton:hover{background:#b02a26}"
              "QPushButton:disabled{background:#d9a09e;border-color:#c98f8d}")

# Spalten der Cluster-Tabelle
C_VENDOR, C_GEN, C_NET, C_IP, C_USER, C_PASS, C_MODEL, C_SERIAL, C_SLOT, C_LIC = range(10)
# Spalten der Übersicht laufender Server
S_NR, S_DEV, S_ID, S_MAC, S_ADDR, S_STATE, S_LOGIN, S_LINK = range(8)


def _vbmc_script():
    """Pfad zu tools/vbmc/vbmc.py oder None (z. B. in der gebauten .exe nicht enthalten)."""
    cand = HERE / "tools" / "vbmc" / "vbmc.py"
    return cand if cand.exists() else None


def _scenario_dir():
    d = HERE / "tools" / "vbmc" / "szenarien"
    return d if d.is_dir() else HERE


def random_serial(vendor):
    chars = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
    return SERIAL_PREFIX[vendor] + "".join(random.choice(chars) for _ in range(9 if vendor != "dell" else 5))


def random_password(vendor):
    """Werkspasswort wie auf dem Aufkleber (Muster je Hersteller, nur fürs Labor)."""
    up = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    if vendor == "smc":
        return "".join(random.choice(up) for _ in range(10))
    if vendor == "hpe":
        return "".join(random.choice(up + "23456789") for _ in range(8))
    mix = up + up.lower() + "23456789"
    return "".join(random.choice(mix) for _ in range(10))


def wired_nics():
    """Kabel-Netzwerkkarten (und Bridges) für den Router-Modus: [(Name, Anzeige)]. Kein WLAN, keine
    virtuellen Karten, keine Ports einer Bridge (dann die Bridge selbst)."""
    out = []
    base = "/sys/class/net"
    if WINDOWS or not os.path.isdir(base):
        return out
    skip = ("lo", "bmclab", "bmcshim", "vbh-", "vbm-", "veth", "docker", "virbr", "br-", "tun", "tap", "wg")
    for dev in sorted(os.listdir(base)):
        p = f"{base}/{dev}"
        if dev.startswith(skip) or os.path.isdir(p + "/wireless") or os.path.isdir(p + "/phy80211"):
            continue
        try:
            if Path(p + "/type").read_text().strip() != "1" or os.path.exists(p + "/master"):
                continue
        except OSError:
            continue
        if not (os.path.exists(p + "/device") or os.path.isdir(p + "/bridge")):
            continue
        try:
            carrier = Path(p + "/carrier").read_text().strip() == "1"
        except OSError:
            carrier = False
        ips = []
        nic = QNetworkInterface.interfaceFromName(dev)
        for e in nic.addressEntries():
            if e.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                ips.append(f"{e.ip().toString()}/{e.prefixLength()}")
        label = f"{dev}  –  " + (", ".join(ips) if ips else T("keine IPv4")) + \
            ("" if carrier else "  " + T("(kein Kabel)"))
        out.append((dev, label, carrier and bool(ips)))
    return out


class VbmcTabMixin:
    """Reiter „Testlabor" des Hauptfensters."""

    # ================================================================ Aufbau
    def build_vbmc(self):
        self.vbmc_script = _vbmc_script()
        self._vbmc_busy = False          # Cluster läuft (oder startet gerade)
        self.vbmc_proc = None
        self.vbmc_servers = []           # letzter Zustand der laufenden Server (aus @@STATE)
        self.vbmc_buf = ""
        self.vbmc_last = ""
        self.vbmc_cfgfile = None
        self.vbmc_run_mode = None

        intro = QLabel(T("Virtuelle Server, die sich wie echte BMCs verhalten - zum "
                         "Testen und Vorführen ohne Hardware. Im Modus Konfigraum oder Router bekommt jeder "
                         "Server eine eigene Netzwerkkarte mit MAC, holt sich per DHCP eine Adresse (oder hat "
                         "eine feste Werks-IP) und zieht beim Umstellen wirklich um."))
        intro.setWordWrap(True)

        # --- Netz-Modus (kompakt: alles in einer Zeile, Erklärung nur auf Knopfdruck) ---
        mbox = QGroupBox(T("Netz"))
        mv = QVBoxLayout(mbox)
        mv.setContentsMargins(8, 2, 8, 4)
        mv.setSpacing(2)
        self.vbmc_mode = QComboBox()
        for key, label in MODES:
            self.vbmc_mode.addItem(T(label), key)
        if WINDOWS:
            for i in (1, 2):
                self.vbmc_mode.model().item(i).setEnabled(False)
        self.vbmc_mode.setToolTip(T("Loopback: läuft ohne Root nur auf diesem PC, Adressen als IP:PORT.\n"
                                    "Konfigraum: eigener virtueller Staging-Switch, DHCP über „DHCP-Server“.\n"
                                    "Router: die Server hängen im echten LAN und erscheinen im Router.\n"
                                    "Konfigraum und Router brauchen beim Start einmal Root (pkexec)."))
        self.vbmc_mode.currentIndexChanged.connect(self.vbmc_mode_changed)
        self.vbmc_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        # Erklärung samt Beispiel zum gewählten Modus: nur auf Knopfdruck („ⓘ“) sichtbar, spart eine Zeile
        self.vbmc_mode_info = QLabel()
        self.vbmc_mode_info.setWordWrap(True)
        self.vbmc_mode_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.vbmc_mode_info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.vbmc_mode_info.setStyleSheet("color:#6b7280;")
        self.vbmc_mode_info.setVisible(False)
        self.vbmc_mode_info_btn = QToolButton()
        self.vbmc_mode_info_btn.setText("ⓘ")
        self.vbmc_mode_info_btn.setCheckable(True)
        self.vbmc_mode_info_btn.setAutoRaise(True)
        self.vbmc_mode_info_btn.setToolTip(T("Erklärung und Beispiel zum gewählten Modus ein-/ausblenden"))
        self.vbmc_mode_info_btn.toggled.connect(self.vbmc_mode_info.setVisible)
        self._vbmc_page_info = {
            "rz": T("Nach dem Start ist in „DHCP-Server“ die Karte „bmclab0“ schon gewählt: dort den DHCP-Server "
                    "starten (wie am Staging-Switch). Server mit fester Werks-IP sind sofort da - zwei mit "
                    "derselben IP stören sich wie in echt."),
            "router": T("Jeder Server bekommt eine eigene MAC (Hersteller-Kennung) und holt sich seine Adresse "
                        "vom Router; er erscheint dort als eigenes Gerät. Dieser PC erreicht die Server über einen "
                        "eigenen Labor-Anschluss „bmcshim“ (bekommt ebenfalls eine Adresse vom Router)."),
        }

        self.vbmc_opts = QStackedWidget()
        # Loopback
        lw = QWidget()
        lf = QHBoxLayout(lw)
        lf.setContentsMargins(0, 0, 0, 0)
        self.vbmc_base = QLineEdit("127.9.0")
        self.vbmc_base.setMaximumWidth(110)
        self.vbmc_base.setToolTip(T("Basis für die automatischen Loopback-Adressen, z. B. 127.9.0 -> 127.9.0.11, .12, ..."))
        lf.addWidget(QLabel(T("Basis-Adresse")))
        lf.addWidget(self.vbmc_base)
        self.vbmc_port = QSpinBox()
        self.vbmc_port.setRange(1, 65535)
        self.vbmc_port.setValue(8443)
        self.vbmc_port.setToolTip(T("Redfish-Port. Port 443 bräuchte Root; mit 8443 geht es ohne."))
        lf.addWidget(QLabel(T("Redfish-Port")))
        lf.addWidget(self.vbmc_port)
        self.vbmc_console = QCheckBox(T("UEFI-Konsole aktivieren (echtes SSH + IPMI, braucht Root)"))
        self.vbmc_console.setToolTip(T("Zusätzlich SSH (22), IPMI-SOL (623) und Redfish auf 443. Diese Ports "
                                       "verlangen Root-Rechte und die Pakete paramiko + pyghmi. Der Start "
                                       "läuft dann über eine Rechteabfrage (pkexec)."))
        lf.addWidget(self.vbmc_console)
        lf.addStretch()
        self.vbmc_opts.addWidget(lw)
        # Konfigraum
        rw = QWidget()
        rf = QHBoxLayout(rw)
        rf.setContentsMargins(0, 0, 0, 0)
        self.vbmc_rz_addrs = QLineEdit("192.168.0.1/24")
        self.vbmc_rz_addrs.setToolTip(T("Adresse(n) dieses PCs am Staging-Switch, ohne Gateway - wie die Staging-"
                                        "Karte im Konfigraum. Mehrere durch Komma, z. B. 192.168.0.1/24, 10.20.30.1/24, "
                                        "damit auch die neuen Adressen im Zielnetz erreichbar sind."))
        rf.addWidget(QLabel(T("Dieser PC")))
        rf.addWidget(self.vbmc_rz_addrs, 1)
        self.vbmc_opts.addWidget(rw)
        # Router
        hw = QWidget()
        hf = QHBoxLayout(hw)
        hf.setContentsMargins(0, 0, 0, 0)
        self.vbmc_nic = QComboBox()
        self.vbmc_nic.setEditable(True)
        self.vbmc_nic.setToolTip(T("Kabel-Netzwerkkarte, die am Router hängt. WLAN geht nicht: ein WLAN-Router "
                                   "nimmt von einem Client keine zusätzlichen MAC-Adressen an."))
        nref = QPushButton("↻")
        nref.setFixedWidth(32)
        nref.setToolTip(T("Netzwerkkarten neu einlesen"))
        nref.clicked.connect(self.vbmc_fill_nics)
        hf.addWidget(QLabel(T("Netzwerkkarte")))
        hf.addWidget(self.vbmc_nic, 1)
        hf.addWidget(nref)
        self.vbmc_opts.addWidget(hw)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addWidget(QLabel(T("Modus")))
        mode_row.addWidget(self.vbmc_mode)
        mode_row.addWidget(self.vbmc_mode_info_btn)
        mode_row.addSpacing(12)
        mode_row.addWidget(self.vbmc_opts, 1)
        mv.addLayout(mode_row)
        mv.addWidget(self.vbmc_mode_info)

        # --- Cluster ---
        fbox = QGroupBox(T("Cluster-Konfiguration"))
        fbox.setToolTip(T("Jede Zeile ist ein virtueller Server. „Starten“ startet alle Zeilen zusammen."))
        fl = QVBoxLayout(fbox)
        self.vbmc_fleet = QTableWidget(0, 10)
        self.vbmc_fleet.setHorizontalHeaderLabels([T("Hersteller"), T("Generation"), T("Werks-Netz"), T("Werks-IP"),
                                                   T("Werks-Benutzer"), T("Werks-Passwort"), T("Modell"),
                                                   T("Seriennummer"), T("Belegter Slot"), T("Lizenz")])
        tips = [T("Hersteller des virtuellen Servers"),
                T("Firmware-Generation. „Böll 8, Firmware zu alt“ und Häpe 3/4 prüfen die Sonderwege von BMC-Steward."),
                T("Ab Werk per DHCP oder mit fester Werks-IP (z. B. Böll 192.168.0.120). Nur Konfigraum/Router."),
                T("Feste Werks-IP (nur bei „Feste IP“)"),
                T("Werksbenutzer, mit dem BMC-Steward sich anmeldet"),
                T("Werkspasswort (wie auf dem Aufkleber). Steht beim Übernehmen je Zeile in „Neuware“."),
                T("Leer = Standardmodell der Generation"),
                T("Bestimmt auch die MAC-Adresse: gleiche Seriennummer = gleiche MAC = gleiche DHCP-Adresse."),
                T("Nur Böll: dieser User-Slot ist schon von einem anderen Benutzer belegt (z. B. 3). "
                  "BMC-Steward darf ihn nicht überschreiben."),
                T("„Ohne Lizenz“: Virtual Media und Remote-Konsole des virtuellen Servers sind gesperrt, bis ein "
                  "Lizenzschlüssel auf seiner Lizenzseite installiert wird.")]
        for c, tip in enumerate(tips):
            self.vbmc_fleet.horizontalHeaderItem(c).setToolTip(tip)
        hh = self.vbmc_fleet.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_MODEL, QHeaderView.ResizeMode.Stretch)
        self.vbmc_fleet.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.vbmc_fleet.setMinimumHeight(200)
        fl.addWidget(self.vbmc_fleet)
        frow = QHBoxLayout()
        self.vbmc_add_btns = []
        for key, label in VENDORS:
            b = QPushButton("+ " + label.split(" (")[0])
            b.setToolTip(T("Einen virtuellen Server dieses Herstellers hinzufügen"))
            b.clicked.connect(lambda _=False, k=key: self.vbmc_add_server(k))
            frow.addWidget(b)
            self.vbmc_add_btns.append(b)
        self.vbmc_one_each = QPushButton(T("Je Hersteller einer"))
        self.vbmc_one_each.clicked.connect(lambda: [self.vbmc_add_server(k) for k, _ in VENDORS])
        self.vbmc_del_btn = QPushButton(T("Entfernen"))
        self.vbmc_del_btn.clicked.connect(self.vbmc_remove_rows)
        self.vbmc_rand_btn = QPushButton(T("Zufällige Werkspasswörter"))
        self.vbmc_rand_btn.setToolTip(T("Setzt für die markierten Zeilen (ohne Markierung: alle) zufällige "
                                        "Passwörter im Stil der Aufkleber, wie bei neuen Geräten."))
        self.vbmc_rand_btn.clicked.connect(self.vbmc_random_passwords)
        self.vbmc_load_btn = QPushButton(T("Szenario laden …"))
        self.vbmc_load_btn.clicked.connect(self.vbmc_load_scenario)
        self.vbmc_save_btn = QPushButton(T("Szenario speichern …"))
        self.vbmc_save_btn.setToolTip(T("Speichert Modus, Netz und alle Server (inkl. Seriennummern und "
                                        "Werkspasswörter) als JSON, um den Aufbau genau so nachzustellen."))
        self.vbmc_save_btn.clicked.connect(self.vbmc_save_scenario)
        for b in (self.vbmc_one_each, self.vbmc_del_btn, self.vbmc_rand_btn):
            frow.addWidget(b)
        frow.addStretch()
        frow.addWidget(self.vbmc_load_btn)
        frow.addWidget(self.vbmc_save_btn)
        fl.addLayout(frow)

        # --- Start/Stopp (Status in derselben Zeile) ---
        self.vbmc_start_btn = QPushButton(T("Starten"))
        self.vbmc_start_btn.setMinimumHeight(30)
        self.vbmc_start_btn.setMinimumWidth(110)
        self.vbmc_start_btn.clicked.connect(self.vbmc_start)
        self.vbmc_stop_btn = QPushButton(T("Stoppen"))
        self.vbmc_stop_btn.setMinimumHeight(30)
        self.vbmc_stop_btn.setMinimumWidth(110)
        self.vbmc_stop_btn.setStyleSheet(STOP_STYLE)
        self.vbmc_stop_btn.setEnabled(False)
        self.vbmc_stop_btn.setVisible(False)             # erscheint erst, wenn gestartet wurde
        self.vbmc_stop_btn.clicked.connect(self.vbmc_stop)
        self.vbmc_status = QLabel()
        self.vbmc_status.setWordWrap(True)
        btns = QHBoxLayout()
        btns.addWidget(self.vbmc_start_btn)
        btns.addWidget(self.vbmc_stop_btn)
        btns.addWidget(self.vbmc_status, 1)
        # „Starten“ nur grün, wenn die Liste gefüllt ist
        fm = self.vbmc_fleet.model()
        for sig in (fm.rowsInserted, fm.rowsRemoved, fm.modelReset):
            sig.connect(self.vbmc_update_start)

        # --- Laufende Server ---
        rbox = QGroupBox(T("Laufende Server"))
        rl = QVBoxLayout(rbox)
        self.vbmc_run = QTableWidget(0, 8)
        self.vbmc_run.setHorizontalHeaderLabels([T("Nr"), T("Gerät"), T("Modell · Seriennummer"), T("MAC"),
                                                 T("Adresse"), T("Zustand"), T("Werks-Login"), T("Kabel")])
        self.vbmc_run.horizontalHeaderItem(S_LINK).setToolTip(T('Simuliert das Netzwerkkabel des Servers (nur in den Modi „Konfigraum“ und „Zuhause/Router“; im Loopback-Modus gibt es keine Netzwerkkarten).\n„Kabel ziehen“ trennt die Netzwerkkarte des Servers vom virtuellen Switch bzw. vom Router: Der Server läuft weiter, ist im Netz aber nicht mehr erreichbar und bekommt keine Adresse per DHCP.\n„Kabel stecken“ verbindet ihn wieder: Er ist wieder erreichbar und kann sich per DHCP eine Adresse holen.\nZum Üben: Server nacheinander anschließen oder einen Kabelbruch bzw. Portausfall mitten in der Umstellung nachstellen.'))
        self.vbmc_run.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vbmc_run.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        rh = self.vbmc_run.horizontalHeader()
        rh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        rh.setSectionResizeMode(S_STATE, QHeaderView.ResizeMode.Stretch)
        self.vbmc_run.verticalHeader().setVisible(False)
        rl.addWidget(self.vbmc_run)
        arow = QHBoxLayout()
        self.vbmc_adopt_btn = QPushButton(T("In Serverliste übernehmen („Neuware“)"))
        self.vbmc_adopt_ready(False)
        self.vbmc_adopt_btn.setToolTip(T("Trägt die markierten (ohne Markierung: alle) laufenden Server mit ihrer "
                                         "aktuellen Adresse und ihrem Werks-Passwort in die Serverliste ein und "
                                         "schlägt neue Adressen vor."))
        self.vbmc_adopt_btn.clicked.connect(self.vbmc_adopt)
        self.vbmc_adopt_mode = QComboBox()
        self.vbmc_adopt_mode.addItem(T("Neue IP: aus meinem echten Netz (nur Probelauf)"), "net")
        self.vbmc_adopt_mode.addItem(T("Neue IP: Übungsadressen (alles klappt)"), "loopback")
        self.vbmc_adopt_mode.setToolTip(T("Welche neue IP soll vorgeschlagen werden? Ein Klick auf ⓘ erklärt es einfach."))
        self.vbmc_adopt_info_btn = QToolButton()
        self.vbmc_adopt_info_btn.setText("ⓘ")
        self.vbmc_adopt_info_btn.setAutoRaise(True)
        self.vbmc_adopt_info_btn.setToolTip(T("Erklärung zur neuen IP"))
        self.vbmc_adopt_info_btn.clicked.connect(self.vbmc_adopt_help)
        arow.addWidget(self.vbmc_adopt_btn)
        arow.addWidget(self.vbmc_adopt_mode)
        arow.addWidget(self.vbmc_adopt_info_btn)
        arow.addStretch()
        rl.addLayout(arow)

        self.vbmc_log = LogView()                   # dasselbe Protokoll wie im Hauptfenster

        lbox = QWidget()                            # ohne Rahmen und Titel: spart Platz
        ll = QVBoxLayout(lbox)
        ll.setContentsMargins(0, 2, 0, 0)
        ll.setSpacing(2)
        lrow = QHBoxLayout()
        lrow.setSpacing(6)
        self.vbmc_log_open_btn = QPushButton(T("Protokoll"))
        self.vbmc_log_open_btn.setFlat(True)
        self.vbmc_log_open_btn.setStyleSheet("QPushButton{font-weight:bold;padding:0 6px;border:none}"
                                             "QPushButton:hover{text-decoration:underline}")
        self.vbmc_log_open_btn.setToolTip(T("Öffnet das Protokoll als Textdatei im Standard-Editor.\n"
                                            "Passwörter stehen nicht darin; geändert wird nichts."))
        self.vbmc_log_open_btn.clicked.connect(self.vbmc_open_log)
        self.vbmc_log_level = LevelCombo()
        self.vbmc_log_level.setMaximumHeight(22)
        lrow.addWidget(self.vbmc_log_open_btn)
        lrow.addWidget(self.vbmc_log_level)
        lrow.addStretch()
        ll.addLayout(lrow)
        f = self.vbmc_log.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        self.vbmc_log.setFont(f)
        self.vbmc_log.setMinimumHeight(60)
        ll.addWidget(self.vbmc_log, 1)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addWidget(intro)
        tl.addWidget(mbox)
        tl.addWidget(fbox, 1)
        tl.addLayout(btns)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(rbox)
        split.addWidget(lbox)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 1)
        split.setChildrenCollapsible(False)
        lay = QVBoxLayout()
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(split)
        page = QWidget()
        page.setLayout(lay)

        self.vbmc_fill_nics()
        self.vbmc_mode_changed()
        self.vbmc_update_start()
        if self.vbmc_script is None:
            self.vbmc_set_status(T("tools/vbmc/vbmc.py nicht gefunden. Der Reiter funktioniert nur, wenn "
                                   "BMC-Steward aus dem Quellcode läuft."), True)
        else:
            self.vbmc_set_status(T("Bereit. Server hinzufügen (oder Szenario laden) und „Starten“."))
        return page

    # ================================================================ Cluster-Tabelle
    def vbmc_current_mode(self):
        return self.vbmc_mode.currentData()

    def vbmc_mode_changed(self, *_):
        mode = self.vbmc_current_mode()
        info = T(MODE_INFO[mode])
        if self._vbmc_page_info.get(mode):
            info += "\n\n" + self._vbmc_page_info[mode]
        self.vbmc_mode_info.setText(info)
        self.vbmc_opts.setCurrentIndex({"loopback": 0, "rz": 1, "router": 2}[mode])
        lab = mode != "loopback"
        for r in range(self.vbmc_fleet.rowCount()):
            self._vbmc_net_enable(r)
        self.vbmc_fleet.setColumnHidden(C_NET, not lab)
        self.vbmc_fleet.setColumnHidden(C_IP, not lab)

    def vbmc_fill_nics(self):
        cur = self.vbmc_nic.currentText().split()[0] if self.vbmc_nic.currentText() else ""
        self.vbmc_nic.clear()
        best = 0
        for i, (dev, label, good) in enumerate(wired_nics()):
            self.vbmc_nic.addItem(label, dev)
            if good and not best:
                best = i
            if dev == cur:
                best = i
        if self.vbmc_nic.count():
            self.vbmc_nic.setCurrentIndex(best)

    def vbmc_selected_nic(self):
        i = self.vbmc_nic.currentIndex()
        if i >= 0 and self.vbmc_nic.itemText(i) == self.vbmc_nic.currentText():
            return self.vbmc_nic.itemData(i)
        return (self.vbmc_nic.currentText().split() or [""])[0]

    def _vbmc_combo(self, items, value=None):
        cb = QComboBox()
        for key, label in items:
            cb.addItem(T(label), key)
        if value is not None:
            i = cb.findData(value)
            if i >= 0:
                cb.setCurrentIndex(i)
        return cb

    def vbmc_add_server(self, vendor, entry=None):
        """Zeile anlegen; `entry` ist ein Server-Eintrag aus einem Szenario."""
        e = entry or {}
        t = self.vbmc_fleet
        r = t.rowCount()
        t.insertRow(r)
        vcb = self._vbmc_combo(VENDORS, vendor)
        t.setCellWidget(r, C_VENDOR, vcb)
        gen = e.get("gen")
        if vendor == "hpe" and e.get("vendor") == "hpe_old" and gen not in ("ilo3", "ilo4"):
            gen = "ilo4"
        t.setCellWidget(r, C_GEN, self._vbmc_combo(GENS[vendor], gen))
        net = e.get("net") or ("dhcp")
        ncb = self._vbmc_combo([("dhcp", "DHCP"), ("static", "Feste IP")], net)
        t.setCellWidget(r, C_NET, ncb)
        t.setCellWidget(r, C_LIC, self._vbmc_combo([("full", "Lizenziert"), ("none", "Ohne Lizenz")],
                                                    "none" if e.get("license") == "none" else "full"))
        user, pw = FACTORY[vendor]
        vals = {C_IP: e.get("ip") or (DELL_STATIC if vendor == "dell" else ""),
                C_USER: e.get("user") or user, C_PASS: e.get("password") or pw,
                C_MODEL: e.get("model") or "", C_SERIAL: e.get("serial") or random_serial(vendor),
                C_SLOT: ""}
        busy = e.get("busy_slots") or {}
        if isinstance(busy, dict) and busy:
            vals[C_SLOT] = ",".join(sorted(busy))
        elif isinstance(busy, (int, str)):
            vals[C_SLOT] = str(busy)
        for c, v in vals.items():
            t.setItem(r, c, QTableWidgetItem(v))
        vcb.currentIndexChanged.connect(lambda _=0, w=vcb: self._vbmc_vendor_changed(w))
        ncb.currentIndexChanged.connect(lambda _=0, w=ncb: self._vbmc_net_enable(self._vbmc_row_of(w, C_NET)))
        self._vbmc_net_enable(r)
        self._vbmc_slot_enable(r)

    def _vbmc_row_of(self, widget, col):
        for r in range(self.vbmc_fleet.rowCount()):
            if self.vbmc_fleet.cellWidget(r, col) is widget:
                return r
        return -1

    def _vbmc_vendor_changed(self, widget):
        r = self._vbmc_row_of(widget, C_VENDOR)
        if r < 0:
            return
        t = self.vbmc_fleet
        vendor = widget.currentData()
        t.setCellWidget(r, C_GEN, self._vbmc_combo(GENS[vendor]))
        user, pw = FACTORY[vendor]
        t.item(r, C_USER).setText(user)
        t.item(r, C_PASS).setText(pw)
        t.item(r, C_SERIAL).setText(random_serial(vendor))
        t.item(r, C_MODEL).setText("")
        if not t.item(r, C_IP).text().strip() and vendor == "dell":
            t.item(r, C_IP).setText(DELL_STATIC)
        self._vbmc_slot_enable(r)

    def _vbmc_editable(self, item, on):
        if item is None:
            return
        flags = item.flags()
        item.setFlags(flags | Qt.ItemFlag.ItemIsEditable if on else flags & ~Qt.ItemFlag.ItemIsEditable)
        item.setForeground(self.palette().text() if on else self.palette().placeholderText())

    def _vbmc_net_enable(self, r):
        if r < 0:
            return
        ncb = self.vbmc_fleet.cellWidget(r, C_NET)
        static = bool(ncb) and ncb.currentData() == "static" and self.vbmc_current_mode() != "loopback"
        self._vbmc_editable(self.vbmc_fleet.item(r, C_IP), static)

    def _vbmc_slot_enable(self, r):
        vcb = self.vbmc_fleet.cellWidget(r, C_VENDOR)
        item = self.vbmc_fleet.item(r, C_SLOT)
        dell = bool(vcb) and vcb.currentData() == "dell"
        if not dell and item:
            item.setText("")
        self._vbmc_editable(item, dell)

    def vbmc_remove_rows(self):
        rows = sorted({i.row() for i in self.vbmc_fleet.selectedIndexes()}, reverse=True)
        for r in rows:
            self.vbmc_fleet.removeRow(r)

    def vbmc_random_passwords(self):
        rows = sorted({i.row() for i in self.vbmc_fleet.selectedIndexes()}) or range(self.vbmc_fleet.rowCount())
        for r in rows:
            vendor = self.vbmc_fleet.cellWidget(r, C_VENDOR).currentData()
            self.vbmc_fleet.item(r, C_PASS).setText(random_password(vendor))

    def vbmc_entries(self):
        """Cluster als Liste von Server-Einträgen (Szenario-Format) - oder (None, Fehlertext)."""
        t = self.vbmc_fleet
        out, serials = [], set()
        lab = self.vbmc_current_mode() != "loopback"
        for r in range(t.rowCount()):
            vendor = t.cellWidget(r, C_VENDOR).currentData()
            gen = t.cellWidget(r, C_GEN).currentData()
            net = t.cellWidget(r, C_NET).currentData()
            cell = {c: (t.item(r, c).text().strip() if t.item(r, c) else "") for c in
                    (C_IP, C_USER, C_PASS, C_MODEL, C_SERIAL, C_SLOT)}
            if not cell[C_PASS]:
                return None, T("Zeile {0}: Werks-Passwort fehlt.").format(r + 1)
            if not cell[C_SERIAL]:
                cell[C_SERIAL] = random_serial(vendor)
                t.item(r, C_SERIAL).setText(cell[C_SERIAL])
            if cell[C_SERIAL] in serials:
                return None, T("Zeile {0}: Seriennummer {1} kommt doppelt vor (sie bestimmt die MAC-Adresse).").format(
                    r + 1, cell[C_SERIAL])
            serials.add(cell[C_SERIAL])
            e = {"vendor": vendor, "gen": gen, "serial": cell[C_SERIAL], "user": cell[C_USER] or None,
                 "password": cell[C_PASS], "model": cell[C_MODEL] or None, "net": net}
            if net == "static":
                if lab:
                    try:
                        ipaddress.IPv4Address(cell[C_IP])
                    except ValueError:
                        return None, T("Zeile {0}: Werks-IP „{1}“ ist keine gültige IPv4-Adresse.").format(
                            r + 1, cell[C_IP])
                e["ip"] = cell[C_IP]
                e["mask"] = "255.255.255.0"
            if vendor == "dell" and cell[C_SLOT]:
                slots = {}
                for s in cell[C_SLOT].replace(";", ",").split(","):
                    s = s.strip()
                    if not s:
                        continue
                    if not s.isdigit() or not 3 <= int(s) <= 16:
                        return None, T("Zeile {0}: Belegter Slot „{1}“ - erlaubt sind 3 bis 16.").format(r + 1, s)
                    slots[s] = "operator" if not slots else f"operator{len(slots) + 1}"
                e["busy_slots"] = slots
            if t.cellWidget(r, C_LIC).currentData() == "none":
                e["license"] = "none"
            out.append({k: v for k, v in e.items() if v not in (None, "")})
        return out, None

    def vbmc_scenario(self):
        entries, err = self.vbmc_entries()
        if err:
            return None, err
        sc = {"mode": self.vbmc_current_mode(), "servers": entries}
        if sc["mode"] == "rz":
            sc["host_addrs"] = self.vbmc_rz_addrs.text().strip()
        elif sc["mode"] == "router":
            sc["parent"] = self.vbmc_selected_nic()
        else:
            sc["base"] = self.vbmc_base.text().strip() or "127.9.0"
            sc["port"] = self.vbmc_port.value()
        return sc, None

    def vbmc_save_scenario(self):
        sc, err = self.vbmc_scenario()
        if err:
            QMessageBox.warning(self, T("Szenario speichern"), err)
            return
        path, _ = QFileDialog.getSaveFileName(self, T("Szenario speichern"), str(_scenario_dir() / "szenario.json"),
                                              T("Szenario (*.json)"))
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(sc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, T("Szenario speichern"), T('Datei nicht schreibbar:\n{0}').format(e))
            return
        self.vbmc_set_status(T("Szenario gespeichert: {0}").format(path))

    def vbmc_load_scenario(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, T("Szenario laden"), str(_scenario_dir()),
                                                  T("Szenario (*.json);;Alle Dateien (*)"))
            if not path:
                return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(data, list):
                data = {"servers": data}
            servers = data.get("servers") or []
        except (OSError, ValueError, AttributeError) as e:
            QMessageBox.warning(self, T("Szenario laden"), T("Datei nicht lesbar:\n{0}").format(e))
            return
        self.vbmc_fleet.setRowCount(0)
        i = self.vbmc_mode.findData(data.get("mode", "loopback"))
        if i >= 0 and self.vbmc_mode.model().item(i).isEnabled():
            self.vbmc_mode.setCurrentIndex(i)
        if data.get("host_addrs"):
            ha = data["host_addrs"]
            self.vbmc_rz_addrs.setText(", ".join(ha) if isinstance(ha, list) else str(ha))
        if data.get("parent"):
            j = self.vbmc_nic.findData(data["parent"])
            if j >= 0:
                self.vbmc_nic.setCurrentIndex(j)
            else:
                self.vbmc_nic.setEditText(data["parent"])
        if data.get("base"):
            self.vbmc_base.setText(str(data["base"]))
        if data.get("port"):
            self.vbmc_port.setValue(int(data["port"]))
        skipped = 0
        for e in servers:
            v = str(e.get("vendor", "")).lower()
            v = "hpe" if v == "hpe_old" else v
            if v not in FACTORY:
                skipped += 1
                continue
            self.vbmc_add_server(v, e)
        self.vbmc_mode_changed()
        msg = T("Szenario geladen: {0} Server").format(self.vbmc_fleet.rowCount())
        if skipped:
            msg += " " + T("({0} unbekannte Einträge übersprungen)").format(skipped)
        self.vbmc_set_status(msg)

    # ================================================================ Start/Stopp
    def vbmc_set_status(self, text, err=False):
        self.vbmc_status.setText(text)
        self.vbmc_status.setStyleSheet("color: #c9302c;" if err else "color: #2e9e4f;")
        LOG.info(T("Testlabor") + ": " + text, "#c9302c" if err else None)

    def vbmc_open_log(self):
        """Öffnet das gemeinsame Protokoll als Textdatei im Standard-Editor."""
        open_in_editor(self)

    def vbmc_running(self):
        return self.vbmc_proc is not None and self.vbmc_proc.state() != QProcess.ProcessState.NotRunning

    def vbmc_running_macs(self):
        """Menge der MAC-Adressen aller aktuell laufenden vBMC-Instanzen (Kleinbuchstaben).

        Wird im DHCP-Reiter genutzt, um Simulator-Eintraege aus der Lease-Liste herauszufiltern,
        wenn der vBMC im Router-Modus auf derselben physischen NIC laueft wie ein echter Server."""
        return {s["mac"].lower() for s in self.vbmc_servers if s.get("mac")}

    def _vbmc_lock(self, running):
        for w in (self.vbmc_mode, self.vbmc_opts, self.vbmc_fleet, self.vbmc_one_each, self.vbmc_del_btn,
                  self.vbmc_rand_btn, self.vbmc_load_btn, *self.vbmc_add_btns):
            w.setEnabled(not running)
        self._vbmc_busy = running
        self.vbmc_stop_btn.setVisible(running)
        self.vbmc_stop_btn.setEnabled(running)
        self.vbmc_update_start()

    def vbmc_update_start(self, *_):
        """„Starten“: grün und bedienbar nur, wenn die Serverliste gefüllt ist und der Cluster nicht läuft."""
        ready = (self.vbmc_script is not None and not self._vbmc_busy
                 and self.vbmc_fleet.rowCount() > 0)
        self.vbmc_start_btn.setEnabled(ready)
        self.vbmc_start_btn.setStyleSheet(START_STYLE if ready else "")
        self.vbmc_start_btn.setToolTip("" if ready or self._vbmc_busy or self.vbmc_script is None else
                                       T("Bitte mindestens einen Server hinzufügen (oder ein Szenario laden)."))

    def vbmc_start(self):
        if self.vbmc_running() or self.vbmc_script is None:
            return
        sc, err = self.vbmc_scenario()
        if not err and not sc["servers"]:
            err = T("Bitte mindestens einen Server hinzufügen (oder ein Szenario laden).")
        mode = sc["mode"] if sc else None
        if not err and mode == "rz":
            try:
                addrs = [str(ipaddress.IPv4Interface(a.strip())) for a in sc["host_addrs"].split(",") if a.strip()]
            except ValueError as e:
                addrs, err = None, T("Konfigraum, Adresse dieses PCs: {0}").format(e)
            if addrs is not None and not addrs:
                err = T("Konfigraum: Adresse dieses PCs fehlt, z. B. 192.168.0.1/24.")
        if not err and mode == "router" and not sc["parent"]:
            err = T("Router: bitte die Kabel-Netzwerkkarte wählen.")
        need_root = mode in ("rz", "router") or (mode == "loopback" and self.vbmc_console.isChecked())
        root = not WINDOWS and os.geteuid() == 0
        if not err and need_root and not root and (WINDOWS or not shutil.which("pkexec")):
            err = T("Für diesen Modus sind Root-Rechte nötig; pkexec ist nicht verfügbar. Bitte im Terminal starten:") \
                + f"\n  sudo python3 {self.vbmc_script} --config <szenario.json>"
        if err:
            self.vbmc_set_status(err, True)
            return

        fd, cfg = tempfile.mkstemp(prefix="bmc-testlabor-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sc, f)
        self.vbmc_cfgfile = cfg
        args = [str(self.vbmc_script), "--config", cfg, "--state", "--control"]
        if mode == "rz":
            args += ["--netlab", "rz", "--host-addr", ",".join(addrs)]
        elif mode == "router":
            args += ["--netlab", "router", "--parent", sc["parent"]]
        else:
            args += ["--base", sc["base"], "--port", str(sc["port"])]
            if self.vbmc_console.isChecked():
                args.append("--console")
        if need_root and not root:
            program, pre = "pkexec", [sys.executable, "-u"]       # grafische Rechteabfrage
        else:
            program, pre = sys.executable, ["-u"]                 # -u: Ausgabe sofort

        LOG.info(T("— Testlabor wird gestartet —"), "gray")
        self.vbmc_last = ""
        self.vbmc_buf = ""
        self.vbmc_servers = []
        self.vbmc_run_mode = mode
        self.vbmc_iface_done = False
        self.vbmc_show_state([])
        self.vbmc_proc = QProcess(self)
        self.vbmc_proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.vbmc_proc.readyReadStandardOutput.connect(self.vbmc_read)
        self.vbmc_proc.finished.connect(self.vbmc_finished)
        self.vbmc_proc.errorOccurred.connect(self.vbmc_error)
        self.vbmc_proc.start(program, pre + args)
        self._vbmc_lock(True)
        self.vbmc_adopt_ready(False)
        self.vbmc_adopt_mode.setVisible(mode == "loopback")
        self.vbmc_adopt_info_btn.setVisible(mode == "loopback")
        self.vbmc_set_status(T("Cluster startet …") if not (need_root and not root)
                             else T("Cluster startet … (Rechteabfrage bestätigen)"))

    def vbmc_stop(self):
        """Über den Steuerkanal beenden: braucht keine Rechte, der Cluster räumt Netz und Leases selbst auf."""
        if not self.vbmc_running():
            return
        self.vbmc_stop_btn.setEnabled(False)
        self.vbmc_set_status(T("Cluster wird gestoppt (Adressen werden freigegeben) …"))
        self.vbmc_proc.write(b"quit\n")
        self.vbmc_proc.closeWriteChannel()
        QTimer.singleShot(15000, self._vbmc_force_stop)

    def _vbmc_force_stop(self):
        if not self.vbmc_running():
            return
        pid = self.vbmc_proc.processId()
        self.vbmc_proc.terminate()
        if not self.vbmc_proc.waitForFinished(3000) and self.vbmc_proc.program() == "pkexec" \
                and not WINDOWS and shutil.which("pkexec"):
            QProcess.execute("pkexec", ["kill", str(pid)])
            self.vbmc_proc.waitForFinished(3000)
        if self.vbmc_running():
            self.vbmc_proc.kill()

    def vbmc_read(self):
        data = bytes(self.vbmc_proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.vbmc_buf += data
        lines = self.vbmc_buf.split("\n")
        self.vbmc_buf = lines.pop()
        out = []
        for line in lines:
            if line.startswith("@@STATE "):
                try:
                    self.vbmc_show_state(json.loads(line[8:]))
                except ValueError:
                    pass
                self._vbmc_drop_cfg()       # der Cluster hat das Szenario gelesen
            else:
                out.append(line.rstrip("\r"))
        for line in out:
            if line.strip():
                self.vbmc_last = line
                LOG.info(T("Testlabor") + ": " + line)

    def _vbmc_drop_cfg(self):
        if self.vbmc_cfgfile:
            try:
                os.unlink(self.vbmc_cfgfile)
            except OSError:
                pass
            self.vbmc_cfgfile = None

    def vbmc_error(self, err):
        if err == QProcess.ProcessError.FailedToStart:
            self.vbmc_set_status(T("Start fehlgeschlagen: {0}").format(self.vbmc_proc.errorString()), True)

    def vbmc_finished(self, code=0, *_):
        self._vbmc_drop_cfg()
        if self.vbmc_buf.strip():
            self.vbmc_last = self.vbmc_buf.rstrip()
            LOG.info(T("Testlabor") + ": " + self.vbmc_last)
            self.vbmc_buf = ""
        self._vbmc_lock(False)
        self.vbmc_adopt_ready(False)
        self.vbmc_servers = []
        self.vbmc_show_state([])
        if code in (126, 127) and self.vbmc_proc and self.vbmc_proc.program() == "pkexec":
            self.vbmc_set_status(T("Rechteabfrage abgebrochen, der Cluster wurde nicht gestartet."), True)
        elif code:
            self.vbmc_set_status(T("Cluster beendet mit Fehler: {0}").format(self.vbmc_last or code), True)
        else:
            self.vbmc_set_status(T("Cluster gestoppt."))

    # ================================================================ Übersicht
    def vbmc_show_state(self, servers):
        self.vbmc_servers = servers
        t = self.vbmc_run
        t.setRowCount(len(servers))
        lab = self.vbmc_run_mode in ("rz", "router")
        t.setColumnHidden(S_MAC, not lab)
        t.setColumnHidden(S_LINK, not lab)
        for r, s in enumerate(servers):
            addr = s.get("addr") or "—"
            vals = {S_NR: str(s.get("id", r + 1)), S_DEV: f"{s.get('name', '')} · {s.get('gen_label', '')}",
                    S_ID: f"{s.get('model', '')} · {s.get('serial', '')}", S_MAC: s.get("mac", ""),
                    S_ADDR: addr, S_STATE: s.get("status", ""),
                    S_LOGIN: f"{s.get('factory_user', '')} / {s.get('factory_pass', '')}"}
            if not s.get("factory_valid", True):
                vals[S_LOGIN] += "  " + T("(geändert)")
            for c, v in vals.items():
                it = QTableWidgetItem(v)
                if c == S_ADDR and not s.get("addr"):
                    it.setForeground(self.palette().placeholderText())
                t.setItem(r, c, it)
            if lab:
                b = QPushButton(T("Kabel ziehen") if s.get("link", True) else T("Kabel stecken"))
                b.setToolTip(T('Zieht das Netzwerkkabel dieses Servers: Der Server läuft weiter, ist im Netz aber nicht mehr erreichbar (wie bei einem Kabelbruch oder abgeschaltetem Switch-Port).') if s.get("link", True) else T('Steckt das Netzwerkkabel dieses Servers wieder an: Er ist wieder erreichbar und kann sich per DHCP eine Adresse holen.'))
                b.clicked.connect(lambda _=False, i=s.get("id"), up=not s.get("link", True): self.vbmc_link(i, up))
                t.setCellWidget(r, S_LINK, b)
        self.vbmc_adopt_ready(any(s.get("addr") for s in servers))
        if servers and self.vbmc_run_mode == "rz" and not getattr(self, "vbmc_iface_done", True):
            self.vbmc_preselect_staging()
        if servers and self.vbmc_running():
            ready = sum(1 for s in servers if s.get("addr"))
            if ready < len(servers) and self.vbmc_run_mode == "rz":
                self.vbmc_set_status(T("Cluster läuft: {0} von {1} Servern haben eine Adresse. Die übrigen warten "
                                       "auf DHCP - in „DHCP-Server“ auf „bmclab0“ den DHCP-Server starten.").format(
                                           ready, len(servers)))
            elif ready < len(servers):
                self.vbmc_set_status(T("Cluster läuft: {0} von {1} Servern haben eine Adresse, die übrigen warten "
                                       "auf DHCP.").format(ready, len(servers)))
            else:
                self.vbmc_set_status(T("Cluster läuft: {0} Server erreichbar. Übernehmen in „Neuware“ oder dort "
                                       "selbst eintragen.").format(ready))

    def vbmc_preselect_staging(self):
        """Konfigraum-Modus: im DHCP-Reiter die Karte bmclab0 schon auswählen (wie die Staging-Karte im Konfigraum)."""
        self.vbmc_iface_done = True
        try:
            if self.dhcp_running():
                return
            self.fill_ifaces()
            for i in range(self.iface.count()):
                if (self.iface.itemData(i) or [None])[0] == "bmclab0":
                    self.iface.setCurrentIndex(i)
                    break
        except AttributeError:
            pass

    def vbmc_link(self, idx, up):
        if self.vbmc_running() and idx is not None:
            self.vbmc_proc.write(f"link {int(idx)} {'up' if up else 'down'}\n".encode())

    def vbmc_adopt_ready(self, ready):
        """„In Serverliste übernehmen“: grün, sobald es laufende Server mit Adresse zu übernehmen gibt, sonst farblos."""
        self.vbmc_adopt_btn.setEnabled(bool(ready))
        self.vbmc_adopt_btn.setStyleSheet(
            "QPushButton{background:#3fa45b;color:#fff;font-weight:bold;border:1px solid #2e8a47;"
            "border-radius:3px;padding:5px 12px}QPushButton:hover{background:#36934f}" if ready else "")

    def vbmc_adopt_help(self):
        """Infobox: was die Auswahl „Neue IP“ beim Übernehmen bewirkt, in einfachen Worten."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(T("Neue IP – was bedeutet das?"))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(T(ADOPT_HELP))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def vbmc_adopt(self):
        """Laufende virtuelle Server in die Serverliste („Neuware“) eintragen."""
        sel = sorted({i.row() for i in self.vbmc_run.selectedIndexes()})
        servers = [self.vbmc_servers[r] for r in sel if r < len(self.vbmc_servers)] or list(self.vbmc_servers)
        servers = [s for s in servers if s.get("addr")]
        if not servers:
            return
        existing = {self.cell(r, 0) for r in range(self.table.rowCount())}
        mode = self.vbmc_run_mode
        note = ""
        if mode == "loopback":
            base = self.vbmc_base.text().strip() or "127.9.0"
            port = self.vbmc_port.value()
            net = local_network() if self.vbmc_adopt_mode.currentData() == "net" else None
            if net:
                mask, gw, prefix, suffix, full = net["mask"], net["gateway"], net["prefix"], "", False
                note = T("Netz erkannt (Gateway {0}, Maske {1}); bei „Neue IP“ nur noch die letzte Ziffer "
                         "eintragen. Ein scharfer Lauf geht in diesem Übungsmodus nicht.").format(gw or "?", mask)
            else:
                mask, gw, prefix, suffix, full = "255.255.255.0", "", base, f":{port}", True
        elif mode == "rz":
            first = ipaddress.IPv4Interface(self.vbmc_rz_addrs.text().split(",")[0].strip())
            mask, gw = str(first.netmask), ""
            prefix, suffix, full = str(first.ip).rsplit(".", 1)[0], "", True
            note = T("Neue IP im Staging-Netz vorgeschlagen; Gateway des Zielnetzes noch eintragen.")
        else:
            s0 = servers[0]
            mask, gw = s0.get("mask") or "255.255.255.0", s0.get("gw") or ""
            prefix, suffix, full = s0["ip"].rsplit(".", 1)[0], "", False
            note = T("Router-Netz übernommen (Gateway {0}); bei „Neue IP“ eine freie letzte Ziffer außerhalb des "
                     "DHCP-Bereichs des Routers eintragen.").format(gw or "?")
        for r in reversed(range(self.table.rowCount())):          # leere Zeilen (z. B. Startzeile) entfernen
            if not any(self.cell(r, c) for c in (0, 2, 5)):
                self.table.removeRow(r)
        added = 0
        for i, s in enumerate(servers):
            if s["addr"] in existing:
                for r in range(self.table.rowCount()):             # Zeile gibt es schon: fehlende Seriennummer ergänzen
                    if self.cell(r, 0) == s["addr"] and not self.cell(r, 6) and s.get("serial"):
                        self.table.item(r, 6).setText(s["serial"])
                continue
            self.add_row()
            r = self.table.rowCount() - 1
            self.table.item(r, 0).setText(s["addr"])                       # Aktuelle IP (Loopback mit :Port)
            self.table.item(r, 1).setText(s.get("factory_pass", ""))       # Werks-Passwort je Zeile
            self.table.item(r, 2).setText(f"{prefix}.{str(51 + i) + suffix if full else ''}")
            self.table.item(r, 3).setText(mask)
            self.table.item(r, 4).setText(gw)
            vendor = "ilo" if s.get("vendor") == "hpe_old" else s.get("vendor", "srv")
            self.table.item(r, 5).setText(f"vsrv-{vendor}-{s.get('id', i + 1)}")
            self.table.item(r, 6).setText(s.get("serial", ""))             # Seriennummer des Testlabor-Servers
            added += 1
        # Benutzer bleibt leer: der Hauptablauf nimmt je Hersteller den Werksbenutzer, solange oben keiner steht
        self.update_count()
        self.touch()
        self.tabs.setCurrentIndex(1)
        skipped = len(servers) - added
        LOG.info(T("Testlabor") + f": übernommen {added}, schon in der Liste {skipped}")
        if added:
            self.status.setText(T("{0} Server übernommen, Werks-Passwörter stehen schon in der Liste.").format(added)
                                + (" " + T("{0} waren schon in der Liste.").format(skipped) if skipped else "")
                                + (" " + note if note else ""))
        else:
            self.status.setText(T("Nichts übernommen: Alle {0} Server stehen schon in der Liste (gleiche aktuelle "
                                  "IP). Zeilen in „Neuware“ entfernen, wenn sie neu angelegt werden sollen.").format(skipped))

    def vbmc_shutdown(self):
        """Beim Schließen des Programms der Cluster mitnehmen (räumt Netzlabor und Leases auf)."""
        if not self.vbmc_running():
            return
        self.vbmc_proc.write(b"quit\n")
        self.vbmc_proc.closeWriteChannel()
        if self.vbmc_proc.waitForFinished(12000):
            return
        self._vbmc_force_stop()
        self.vbmc_proc.waitForFinished(1000)
