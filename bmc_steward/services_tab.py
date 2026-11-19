"""BMC-Steward: Reiter „Dienste" - NTP, Syslog, LDAP/AD, HTTPS-Zertifikat, DNS, VLAN, IPv6, Netzwerkport
und Härtung (IPMI/SSDP/SNMP, Kontosperre, Session-Zeitlimit, TLS-Mindestversion) über Standard-Redfish."""
import html
import os
from pathlib import Path

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                             QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout,
                             QWidget)
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtCore import Qt

from . import capabilities, i18n
from .applog import LOG
from .core import LIVE_CSS
from .i18n import HERE, T
from .redfish import ServicesEngine, ServicesEngineThread


class ServicesTabMixin:
    """Reiter „Dienste" des Hauptfensters."""

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
        caps_btn = QPushButton(T("Fähigkeiten je Hersteller …"))
        caps_btn.setToolTip(T("Zeigt, welche Einstellung BMC-Steward bei welchem Hersteller setzen kann."))
        caps_btn.clicked.connect(self.show_capabilities)
        f.addRow("", caps_btn)
        self.svc_vendor.currentIndexChanged.connect(self.services_update_caps)

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

        self.dns_box = QGroupBox(T("DNS"))
        self.dns_box.setCheckable(True)
        self.dns_box.setChecked(False)
        self.dns_box.setToolTip(T("Setzt die DNS-Server am Management-Netzwerkinterface des BMC."))
        self.dns1 = QLineEdit()
        self.dns1.setPlaceholderText(T("z. B. 10.20.0.1"))
        self.dns2 = QLineEdit()
        self.dns2.setPlaceholderText(T("optional"))
        f = QFormLayout(self.dns_box)
        self.tip_row(f, T("DNS-Server 1"), self.dns1, T("Pflicht, wenn DNS eingerichtet wird."))
        self.tip_row(f, T("DNS-Server 2"), self.dns2, T("Optional, zweiter DNS-Server."))

        self.vlan_box = QGroupBox(T("VLAN"))
        self.vlan_box.setCheckable(True)
        self.vlan_box.setChecked(False)
        self.vlan_box.setToolTip(T("VLAN-Tag am Management-Netzwerkinterface des BMC (Standard-Redfish)."))
        self.vlan_id = QSpinBox()
        self.vlan_id.setRange(0, 4094)
        self.vlan_id.setSpecialValueText(T("VLAN abschalten"))
        f = QFormLayout(self.vlan_box)
        self.tip_row(f, T("VLAN-ID"), self.vlan_id, T("0 schaltet das VLAN-Tag wieder ab (Zugriff über das "
                                                       "untaggte Netz)."))

        self.ipv6_box = QGroupBox(T("IPv6"))
        self.ipv6_box.setCheckable(True)
        self.ipv6_box.setChecked(False)
        self.ipv6_box.setToolTip(T("IPv6 am Management-Netzwerkinterface des BMC."))
        self.ipv6_mode = QComboBox()
        for label, key in ((T("Aus"), "disabled"), (T("Automatisch (SLAAC)"), "slaac"),
                           (T("DHCPv6"), "dhcpv6"), (T("Statisch"), "static")):
            self.ipv6_mode.addItem(label, key)
        self.ipv6_mode.currentIndexChanged.connect(self.ipv6_mode_changed)
        self.ipv6_addr = QLineEdit()
        self.ipv6_addr.setPlaceholderText(T("z. B. fd00::10"))
        self.ipv6_prefix = QSpinBox()
        self.ipv6_prefix.setRange(1, 128)
        self.ipv6_prefix.setValue(64)
        self.ipv6_gw = QLineEdit()
        self.ipv6_gw.setPlaceholderText(T("optional"))
        f = QFormLayout(self.ipv6_box)
        self.tip_row(f, T("Betriebsart"), self.ipv6_mode,
                     T("Aus, automatisch per Router (SLAAC), per DHCPv6-Server oder eine feste Adresse."))
        self.tip_row(f, T("Adresse"), self.ipv6_addr, T("Nur bei „Statisch“ nötig."))
        self.tip_row(f, T("Präfixlänge"), self.ipv6_prefix, T("Meist 64."))
        self.tip_row(f, T("Gateway"), self.ipv6_gw, T("Optional, nur bei „Statisch“."))
        self.ipv6_mode_changed()

        self.nicmode_box = QGroupBox(T("Netzwerkport (Shared/Dedicated)"))
        self.nicmode_box.setCheckable(True)
        self.nicmode_box.setChecked(False)
        self.nicmode_box.setToolTip(T("Eigener Management-Port (Dedicated) oder gemeinsam mit dem LOM/OCP-Port "
                                      "(Shared). Bislang nur bei Dell über Redfish gesetzt; bei den anderen "
                                      "Herstellern bitte in der Oberfläche umstellen."))
        self.nicmode = QComboBox()
        for label, key in ((T("Eigener Port (Dedicated)"), "dedicated"), (T("Gemeinsam (Shared)"), "shared")):
            self.nicmode.addItem(label, key)
        f = QFormLayout(self.nicmode_box)
        self.tip_row(f, T("Modus"), self.nicmode, self.nicmode_box.toolTip())

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
        self.hard_tls_min = QComboBox()
        for label, key in ((T("unverändert"), ""), (T("TLS 1.2"), "tls12"), (T("TLS 1.3"), "tls13")):
            self.hard_tls_min.addItem(label, key)
        self.hard_tls_min.setToolTip(T(
            "Setzt die TLS-Mindestversion des BMC.\n"
            "TLS 1.2: empfohlen für Kompatibilität mit Monitoring-Tools und älteren Clients.\n"
            "TLS 1.3: höchste Sicherheit, nicht von allen Hersteller-Firmwares unterstützt.\n"
            "Wird derzeit für Dell iDRAC über die iDRAC-Attribute-API gesetzt;\n"
            "bei anderen Herstellern wird die Einstellung versucht und bei Fehler übersprungen."))
        f = QFormLayout(self.hard_box)
        f.addRow(self.hard_ipmi)
        f.addRow(self.hard_ssdp)
        f.addRow(self.hard_snmp)
        self.tip_row(f, T("Kontosperre nach Fehlversuchen"), self.hard_lock,
                     T("Sperrt ein Konto nach so vielen falschen Passwörtern (0 = unverändert)."))
        self.tip_row(f, T("Sperrdauer"), self.hard_lock_min, T("So lange bleibt ein gesperrtes Konto gesperrt."))
        self.tip_row(f, T("Session-Zeitlimit"), self.hard_tmo,
                     T("Untätige Anmeldungen (Weboberfläche, Redfish) enden nach dieser Zeit (0 = unverändert)."))
        self.tip_row(f, T("TLS-Mindestversion"), self.hard_tls_min,
                     self.hard_tls_min.toolTip())

        settings = QWidget()
        sv = QVBoxLayout(settings)
        for w in (box_login, self.ntp_box, self.syslog_box, self.ldap_box, self.cert_box, self.dns_box,
                 self.vlan_box, self.ipv6_box, self.nicmode_box, self.hard_box):
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
        self.services_update_caps()
        return page

    # ---------- Fähigkeitsmatrix ----------
    def services_vendors(self):
        """Hersteller, für die die Dienste gelten: die feste Wahl oben, sonst die erkannten der Serverliste."""
        fixed = self.svc_vendor.currentData()
        if fixed:
            return {fixed}
        col = getattr(self, "vendor_col", None)
        if col is None or not hasattr(self, "table"):
            return set()
        out = set()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, col)
            v = capabilities.vendor_from_label(item.text() if item else "")
            if v:
                out.add(v)
        return out

    def services_update_caps(self, *_):
        """Graut Bereiche aus, die bei keinem Server der Liste gehen, und nennt die Hersteller, bei denen ein
        Bereich übersprungen wird (Fähigkeitsmatrix, capabilities.py)."""
        if not hasattr(self, "hard_box"):
            return
        vendors = self.services_vendors()
        pairs = [(self.ntp_box, "ntp"), (self.syslog_box, "syslog"), (self.ldap_box, "ldap"),
                 (self.cert_box, "cert"), (self.dns_box, "dns"), (self.vlan_box, "vlan"),
                 (self.ipv6_box, "ipv6"), (self.nicmode_box, "nicmode"), (self.hard_box, "hard")]
        hints = getattr(self, "_cap_hints", None)
        if hints is None:
            hints = self._cap_hints = {}
            for box, key in pairs:
                lab = QLabel()
                lab.setObjectName("capHint")
                lab.setWordWrap(True)
                lab.setStyleSheet("color: #c9a100;")
                lab.hide()
                lay = box.layout()
                if isinstance(lay, QFormLayout):
                    lay.insertRow(0, lab)
                else:
                    lay.insertWidget(0, lab)
                hints[key] = lab
        for box, key in pairs:
            usable, hint = capabilities.check(key, vendors)
            if not usable and box.isChecked():
                box.setChecked(False)
            box.setEnabled(usable)
            box.setToolTip(hint or "")
            hints[key].setText(hint)
            hints[key].setVisible(bool(hint))
        for widget, key in ((self.ntp_tz, "timezone"), (self.hard_tls_min, "tls_min")):
            usable, hint = capabilities.check(key, vendors)
            widget.setEnabled(usable)
            if not usable and isinstance(widget, QLineEdit):
                widget.clear()
            if not usable and isinstance(widget, QComboBox):
                widget.setCurrentIndex(0)

    def show_capabilities(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(T("Fähigkeiten je Hersteller"))
        dlg.resize(760, 620)
        lay = QVBoxLayout(dlg)
        intro = QLabel(T("Was BMC-Steward je Hersteller einstellen kann. Was bei keinem Server der Liste geht, "
                         "ist auf den Seiten ausgegraut; bei gemischten Listen wird es bei den übrigen "
                         "Herstellern ohne Fehler übersprungen. Was ein bestimmtes Gerät wirklich kann, zeigt "
                         "eine Aufnahme (Seite „Aufnahme“, Fähigkeitsbericht)."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        view = QTextBrowser()
        view.setHtml(capabilities.matrix_html())
        lay.addWidget(view, 1)
        box = QDialogButtonBox()
        box.addButton(T("Schließen"), QDialogButtonBox.ButtonRole.RejectRole)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        dlg.exec()

    def ldap_group_add(self):
        r = self.ldap_groups.rowCount()
        self.ldap_groups.insertRow(r)
        self.ldap_groups.setItem(r, 0, QTableWidgetItem(""))
        self.ldap_groups.setItem(r, 1, QTableWidgetItem("Administrator"))

    def ldap_group_remove(self):
        for r in sorted({i.row() for i in self.ldap_groups.selectedIndexes()}, reverse=True):
            self.ldap_groups.removeRow(r)

    def ipv6_mode_changed(self, *_):
        static = self.ipv6_mode.currentData() == "static"
        for w in (self.ipv6_addr, self.ipv6_prefix, self.ipv6_gw):
            w.setEnabled(static)

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
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.services_page))
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
        if self.dns_box.isChecked():
            if not self.dns1.text().strip():
                return bad(self.dns1, T("DNS: mindestens den ersten Server eintragen."))
            out["dns"] = {"servers": [self.dns1.text(), self.dns2.text()]}
        if self.vlan_box.isChecked():
            out["vlan"] = {"id": self.vlan_id.value()}
        if self.ipv6_box.isChecked():
            mode = self.ipv6_mode.currentData() or "disabled"
            if mode == "static" and not self.ipv6_addr.text().strip():
                return bad(self.ipv6_addr, T("IPv6: bei „Statisch“ die Adresse eintragen."))
            out["ipv6"] = {"mode": mode, "address": self.ipv6_addr.text(), "prefix": self.ipv6_prefix.value(),
                           "gateway": self.ipv6_gw.text()}
        if self.nicmode_box.isChecked():
            out["nicmode"] = {"mode": self.nicmode.currentData() or "dedicated"}
        if self.hard_box.isChecked():
            out["hard"] = {"ipmi": self.hard_ipmi.isChecked(), "ssdp": self.hard_ssdp.isChecked(),
                           "snmp": self.hard_snmp.isChecked(), "lockout": self.hard_lock.value(),
                           "lockout_min": self.hard_lock_min.value(), "timeout_min": self.hard_tmo.value(),
                           "tls_min": self.hard_tls_min.currentData() or ""}
            if not any(out["hard"][k] for k in ("ipmi", "ssdp", "snmp", "lockout", "timeout_min", "tls_min")):
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
        names = {"ntp": "NTP", "syslog": "Syslog", "ldap": "LDAP", "cert": T("HTTPS-Zertifikat"),
                 "dns": "DNS", "vlan": "VLAN", "ipv6": "IPv6", "nicmode": T("Netzwerkport"), "hard": T("Härtung")}
        chosen = ", ".join(names[k] for k in ("ntp", "syslog", "ldap", "cert", "dns", "vlan", "ipv6", "nicmode",
                                              "hard") if k in services)
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
        LOG.info(T("Dienste") + ": " + text, color, bold)

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
            "dns_on": self.dns_box.isChecked(), "dns1": self.dns1.text(), "dns2": self.dns2.text(),
            "vlan_on": self.vlan_box.isChecked(), "vlan_id": self.vlan_id.value(),
            "ipv6_on": self.ipv6_box.isChecked(), "ipv6_mode": self.ipv6_mode.currentData() or "disabled",
            "ipv6_addr": self.ipv6_addr.text(), "ipv6_prefix": self.ipv6_prefix.value(),
            "ipv6_gw": self.ipv6_gw.text(),
            "nicmode_on": self.nicmode_box.isChecked(), "nicmode": self.nicmode.currentData() or "dedicated",
            "hard_on": self.hard_box.isChecked(), "hard_ipmi": self.hard_ipmi.isChecked(),
            "hard_ssdp": self.hard_ssdp.isChecked(), "hard_snmp": self.hard_snmp.isChecked(),
            "hard_lock": self.hard_lock.value(), "hard_lock_min": self.hard_lock_min.value(),
            "hard_tmo": self.hard_tmo.value(), "hard_tls_min": self.hard_tls_min.currentData() or "",
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
        self.dns_box.setChecked(s.get("dns_on", False))
        self.dns1.setText(s.get("dns1", ""))
        self.dns2.setText(s.get("dns2", ""))
        self.vlan_box.setChecked(s.get("vlan_on", False))
        self.vlan_id.setValue(s.get("vlan_id", 0))
        self.ipv6_box.setChecked(s.get("ipv6_on", False))
        idx = self.ipv6_mode.findData(s.get("ipv6_mode", "disabled"))
        self.ipv6_mode.setCurrentIndex(max(0, idx))
        self.ipv6_addr.setText(s.get("ipv6_addr", ""))
        self.ipv6_prefix.setValue(s.get("ipv6_prefix", 64))
        self.ipv6_gw.setText(s.get("ipv6_gw", ""))
        self.nicmode_box.setChecked(s.get("nicmode_on", False))
        idx = self.nicmode.findData(s.get("nicmode", "dedicated"))
        self.nicmode.setCurrentIndex(max(0, idx))
        self.hard_box.setChecked(s.get("hard_on", False))
        self.hard_ipmi.setChecked(s.get("hard_ipmi", True))
        self.hard_ssdp.setChecked(s.get("hard_ssdp", True))
        self.hard_snmp.setChecked(s.get("hard_snmp", False))
        self.hard_lock.setValue(s.get("hard_lock", 5))
        self.hard_lock_min.setValue(s.get("hard_lock_min", 15))
        self.hard_tmo.setValue(s.get("hard_tmo", 15))
        tls_key = s.get("hard_tls_min", "")
        idx = self.hard_tls_min.findData(tls_key)
        self.hard_tls_min.setCurrentIndex(max(0, idx))
