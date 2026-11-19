"""BMC-Steward: Fenster „Server suchen“ (Netzsuche nach BMCs mit unbekannter Adresse, siehe netscan.py)."""
import ipaddress
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)
from . import netscan
from .i18n import T

COLS = ("Adresse", "Hersteller", "Produkt", "Seriennummer", "Web", "IPMI", "MAC", "Hinweis")
# Eigene Palette, keine Markenfarben
VENDOR_COLOR = {"dell": "#8a4fc0", "hpe": "#1f8f8f", "hpe_old": "#1f8f8f", "smc": "#4a8f2a", "lenovo": "#c9741a",
                "ipmi": "#8e7cc3"}


def ip_key(ip):
    try:
        return int(ipaddress.IPv4Address(ip))
    except ValueError:
        return 0


def address_of(row):
    """Adresse so, wie sie in die Felder des Tools gehört: ohne :443, sonst mit Port."""
    port = row.get("port") or 443
    return row["ip"] if port == 443 else f"{row['ip']}:{port}"


class ScanThread(QThread):
    found = pyqtSignal(dict)
    progress = pyqtSignal(int, int)
    phase = pyqtSignal(str)
    finished_ok = pyqtSignal(list)

    def __init__(self, parent, targets, ports, workers, ipmi, ssdp):
        super().__init__(parent)
        self.targets, self.ports, self.workers, self.ipmi, self.ssdp = targets, ports, workers, ipmi, ssdp
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        rows = []
        try:
            rows = netscan.scan(self.targets, self.ports, self.workers, ipmi=self.ipmi, ssdp=self.ssdp,
                                on_found=self.found.emit, on_progress=self.progress.emit,
                                on_phase=self.phase.emit, stopped=lambda: self._stop)
        except Exception as e:                                  # nie ein Absturz des Fensters
            self.phase.emit(T("Fehler bei der Suche: {0!r}").format(e))
        self.finished_ok.emit(rows)


class NetScanDialog(QDialog):
    """Suchbereich wählen, suchen, Treffer übernehmen. Das Fenster ändert selbst nichts an Geräten."""
    use_in_console = pyqtSignal(str, str)           # Adresse, Herstellerschlüssel
    add_to_list = pyqtSignal(list)                  # Liste von Treffer-dicts

    def __init__(self, parent, ifaces=(), console=True):
        super().__init__(parent)
        self.setWindowTitle(T("Server suchen"))
        self.resize(900, 560)
        self.thread = None
        self.rows = {}                              # ip -> Treffer

        intro = QLabel(T("Sucht im angegebenen Bereich nach BMCs (iDRAC, iLO, XCC, Supermicro). Es werden keine "
                         "Zugangsdaten gesendet und nichts verändert: nur ein Verbindungsaufbau und das Lesen "
                         "der öffentlichen Geräteangaben."))
        intro.setWordWrap(True)
        self.range = QLineEdit()
        suggestions = netscan.suggest_ranges(ifaces)
        self.range.setText(", ".join(suggestions[:3]))
        self.range.setPlaceholderText(T("z. B. 192.168.1.0/24, 10.0.0.5-10.0.0.40"))
        self.range.setToolTip(T("Netze (192.168.1.0/24), Bereiche (10.0.0.5-10.0.0.40 oder 10.0.0.5-40) und einzelne\n"
                                "Adressen, getrennt durch Komma oder Leerzeichen. Vorgeschlagen werden die Netze dieses\n"
                                "Rechners; große Netze sind auf das /24 der eigenen Adresse verkleinert."))
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(T("Bereich")))
        row1.addWidget(self.range, 1)
        for text in suggestions[:3]:
            b = QPushButton(text)
            b.setToolTip(T("Eigenes Netz einsetzen"))
            b.clicked.connect(lambda _=False, t=text: self.range.setText(t))
            row1.addWidget(b)

        self.ports = QLineEdit("443")
        self.ports.setMaximumWidth(120)
        self.ports.setToolTip(T("Webports der BMCs, durch Komma getrennt. Standard: 443."))
        self.workers = QSpinBox()
        self.workers.setRange(1, netscan.MAX_WORKERS)
        self.workers.setValue(64)
        self.workers.setToolTip(T("Wie viele Adressen gleichzeitig geprüft werden. Weniger ist schonender für Netz\n"
                                  "und Firewalls (Alarm von Einbruchserkennung möglich)."))
        self.ipmi = QCheckBox(T("auch IPMI (UDP 623)"))
        self.ipmi.setChecked(True)
        self.ipmi.setToolTip(T("Findet auch BMCs, deren Weboberfläche abgeschaltet ist. Ein kleines Paket je Adresse."))
        self.ssdp = QCheckBox(T("auch SSDP (Multicast)"))
        self.ssdp.setToolTip(T("Hört auf Ankündigungen im eigenen Netzsegment, auch außerhalb des Bereichs."))
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(T("Ports")))
        row2.addWidget(self.ports)
        row2.addWidget(QLabel(T("Parallel")))
        row2.addWidget(self.workers)
        row2.addWidget(self.ipmi)
        row2.addWidget(self.ssdp)
        row2.addStretch()
        self.go = QPushButton(T("Suche starten"))
        self.go.setDefault(True)
        self.go.clicked.connect(self.start)
        self.stop_btn = QPushButton(T("Stopp"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        row2.addWidget(self.go)
        row2.addWidget(self.stop_btn)

        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setFormat("%v / %m")
        self.info = QLabel(T("Bereit."))
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([T(c) for c in COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.table.cellDoubleClicked.connect(lambda *_: self.to_console() if console else self.to_list())

        self.b_console = QPushButton(T("In die Konsole übernehmen"))
        self.b_console.setToolTip(T("Trägt die Adresse (und den erkannten Hersteller) auf der Seite „UEFI-Konsole“ ein."))
        self.b_console.clicked.connect(self.to_console)
        self.b_list = QPushButton(T("In die Serverliste („Neuware“)"))
        self.b_list.setToolTip(T("Fügt die markierten Treffer als neue Zeilen in die Serverliste ein (ohne Doppelte)."))
        self.b_list.clicked.connect(self.to_list)
        self.b_copy = QPushButton(T("Adresse kopieren"))
        self.b_copy.clicked.connect(self.copy_address)
        self.b_csv = QPushButton(T("Als CSV speichern …"))
        self.b_csv.clicked.connect(self.save_csv)
        bot = QHBoxLayout()
        if console:
            bot.addWidget(self.b_console)
        bot.addWidget(self.b_list)
        bot.addWidget(self.b_copy)
        bot.addStretch()
        bot.addWidget(self.b_csv)
        if not console:
            self.b_console.hide()

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addWidget(self.bar)
        lay.addWidget(self.info)
        lay.addWidget(self.table, 1)
        lay.addLayout(bot)
        self.update_buttons()

    # ---------- Lauf ----------
    def start(self):
        try:
            targets = netscan.parse_targets(self.range.text())
            ports = netscan.parse_ports(self.ports.text())
        except ValueError as e:
            self.info.setText(str(e))
            return
        if not targets:
            self.info.setText(T("Bitte einen Bereich eintragen."))
            return
        if len(targets) > netscan.CONFIRM_ABOVE:
            box = QMessageBox(QMessageBox.Icon.Question, T("Server suchen"),
                              T("{0} Adressen prüfen? Das dauert etwas und erzeugt Netzverkehr.").format(len(targets)),
                              parent=self)
            yes = box.addButton(T("Suchen"), QMessageBox.ButtonRole.AcceptRole)
            no = box.addButton(T("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(no)
            box.exec()
            if box.clickedButton() is not yes:
                return
        self.table.setRowCount(0)
        self.rows = {}
        self.bar.setRange(0, len(targets))
        self.bar.setValue(0)
        self.set_running(True)
        self.thread = ScanThread(self, targets, ports, self.workers.value(), self.ipmi.isChecked(),
                                 self.ssdp.isChecked())
        self.thread.found.connect(self.on_found)
        self.thread.progress.connect(self.on_progress)       # Methode statt Lambda: Qt trennt sie mit dem Dialog
        self.thread.phase.connect(self.info.setText)
        self.thread.finished_ok.connect(self.on_finished)
        self.thread.start()

    def on_progress(self, done, total):
        self.bar.setValue(done)

    def stop(self):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.info.setText(T("Breche ab …"))

    def set_running(self, on):
        self.go.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        for w in (self.range, self.ports, self.workers, self.ipmi, self.ssdp):
            w.setEnabled(not on)

    def find_row(self, ip):
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == ip:
                return i
        return None

    def on_found(self, row):
        ip = row["ip"]
        self.rows[ip] = dict(row)
        r = self.find_row(ip)
        if r is None:                                           # neu: an der Stelle nach Adresse einsortieren
            r = 0
            while r < self.table.rowCount() and ip_key(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)) < ip_key(ip):
                r += 1
            self.table.insertRow(r)
        vals = [address_of(row), netscan.vendor_label(row["vendor"]), row.get("model", ""),
                row.get("serial", "") or (row.get("uuid", "")[:8] if row.get("uuid") else ""),
                T("ja") if row.get("redfish") else T("nein"), T("ja") if row.get("ipmi") else T("nein"),
                row.get("mac", ""), row.get("note", "")]
        for c, v in enumerate(vals):
            item = QTableWidgetItem(v)
            if c == 1 and row["vendor"] in VENDOR_COLOR:
                item.setForeground(QColor(VENDOR_COLOR[row["vendor"]]))
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            if c == 0:
                item.setData(Qt.ItemDataRole.UserRole, ip)
            self.table.setItem(r, c, item)
        self.info.setText(T("{0} Treffer bisher …").format(len(self.rows)))

    def on_finished(self, rows):
        for r in rows:                                          # endgültige Liste (z. B. mit MAC-Adressen) nachtragen
            self.on_found(r)
        self.set_running(False)
        if not rows:
            self.info.setText(T("Nichts gefunden. Bereich, Port und Netz prüfen (der Rechner muss das Netz der BMCs "
                                "erreichen)."))
        self.update_buttons()

    # ---------- Übernehmen ----------
    def selected(self):
        ips = []
        for i in sorted({x.row() for x in self.table.selectedIndexes()}):
            it = self.table.item(i, 0)
            if it:
                ips.append(it.data(Qt.ItemDataRole.UserRole))
        return [self.rows[ip] for ip in ips if ip in self.rows]

    def update_buttons(self, *_):
        sel = self.selected()
        self.b_console.setEnabled(len(sel) == 1)
        self.b_list.setEnabled(bool(sel))
        self.b_copy.setEnabled(bool(sel))
        self.b_csv.setEnabled(bool(self.rows))

    def to_console(self):
        sel = self.selected()
        if len(sel) == 1:
            self.use_in_console.emit(address_of(sel[0]), sel[0]["vendor"])
            self.accept()

    def to_list(self):
        sel = self.selected()
        if sel:
            self.add_to_list.emit(sel)
            self.info.setText(T("{0} Treffer an die Serverliste übergeben.").format(len(sel)))

    def copy_address(self):
        sel = self.selected()
        if sel:
            QGuiApplication.clipboard().setText("\n".join(address_of(r) for r in sel))

    def save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, T("Trefferliste speichern"), "bmc-suche.csv", "CSV (*.csv)")
        if path:
            rows = sorted(self.rows.values(), key=lambda r: ip_key(r["ip"]))
            with open(path, "w", encoding="utf-8") as f:
                f.write(netscan.rows_to_csv(rows))
            self.info.setText(T("Gespeichert: {0}").format(path))

    def closeEvent(self, e):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread.wait(5000)
        super().closeEvent(e)

    def done(self, code):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread.wait(5000)
        super().done(code)
