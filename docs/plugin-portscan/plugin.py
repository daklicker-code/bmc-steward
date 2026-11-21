"""Portscan-Plugin für BMC-Steward (Plugin-API 1).

Installieren: diesen Ordner nach <Datenordner>/plugins/portscan/ kopieren, BMC-Steward starten,
unter ⚙ → „Plugins“ freigeben und neu starten. Die Seite „Portscan“ erscheint im Menü unter „Plugins“.

Nur Netze scannen, für die du zuständig bist oder eine Erlaubnis hast.
"""
import queue
import threading

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                             QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import scan

# Deutsch -> (Englisch, Französisch, Schweizerdeutsch)
TEXTS = {
    "Portscan": ("Port scan", "Scan de ports", "Portscan"),
    "Ziele": ("Targets", "Cibles", "Ziil"),
    "Ports": ("Ports", "Ports", "Ports"),
    "Vorlage": ("Preset", "Modèle", "Vorlag"),
    "eigene": ("custom", "personnalisé", "eigeni"),
    "BMC-Dienste": ("BMC services", "Services BMC", "BMC-Dienscht"),
    "Server-Dienste": ("Server services", "Services serveur", "Server-Dienscht"),
    "bekannte Dienste": ("known services", "services connus", "bekannti Dienscht"),
    "alle 65535": ("all 65535", "les 65535", "alli 65535"),
    "Zeitlimit (s)": ("Timeout (s)", "Délai (s)", "Ziitlimit (s)"),
    "Gleichzeitig": ("Parallel", "En parallèle", "Glichziitig"),
    "Banner lesen": ("Read banner", "Lire la bannière", "Banner läse"),
    "IPMI über UDP 623": ("IPMI via UDP 623", "IPMI via UDP 623", "IPMI über UDP 623"),
    "nur offene zeigen": ("show open only", "afficher seulement les ouverts", "nur offni zeige"),
    "Aus Serverliste": ("From server list", "Depuis la liste", "Us de Serverlischte"),
    "Start": ("Start", "Démarrer", "Start"),
    "Stopp": ("Stop", "Arrêter", "Stopp"),
    "CSV speichern …": ("Save CSV …", "Enregistrer CSV …", "CSV spichere …"),
    "Ziel": ("Target", "Cible", "Ziil"),
    "Port": ("Port", "Port", "Port"),
    "Protokoll": ("Protocol", "Protocole", "Protokoll"),
    "Dienst": ("Service", "Service", "Dienscht"),
    "Zustand": ("State", "État", "Zuestand"),
    "Banner": ("Banner", "Bannière", "Banner"),
    "offen": ("open", "ouvert", "offe"),
    "geschlossen": ("closed", "fermé", "zue"),
    "gefiltert": ("filtered", "filtré", "gfilteret"),
    "unbekannt": ("unresolved", "non résolu", "unbekannt"),
    "Eingabe prüfen": ("Check input", "Vérifier la saisie", "Iigab prüefe"),
    "Ungültige Angabe: {0}": ("Invalid entry: {0}", "Saisie invalide : {0}", "Ungültigi Aagab: {0}"),
    "Großer Scan": ("Large scan", "Scan important", "Grosse Scan"),
    "{0} Prüfungen, im schlechtesten Fall etwa {1} Minuten. Starten?": (
        "{0} probes, worst case about {1} minutes. Start?",
        "{0} tests, au pire environ {1} minutes. Démarrer ?",
        "{0} Prüefige, im schlächtischte Fall öppe {1} Minute. Starte?"),
    "{0} von {1} geprüft, {2} offen": ("{0} of {1} probed, {2} open", "{0} sur {1} testés, {2} ouverts",
                                         "{0} vo {1} prüeft, {2} offe"),
    "Fertig: {0} offene Ports auf {1} Zielen": ("Done: {0} open ports on {1} targets",
                                               "Terminé : {0} ports ouverts sur {1} cibles",
                                               "Fertig: {0} offni Ports uf {1} Ziil"),
    "Abgebrochen": ("Stopped", "Interrompu", "Abbroche"),
    "Nur Verbindungsaufbau, keine Anmeldung. Nur Netze scannen, für die du zuständig bist.": (
        "Connection attempts only, no login. Only scan networks you are responsible for.",
        "Tentatives de connexion uniquement, sans authentification. Ne scanner que vos propres réseaux.",
        "Nur Verbindigsufbau, kei Aamäldig. Nur Netz scanne, für die du zueständig bisch."),
    "Adressen, Netze oder Bereiche, z. B. 10.0.0.5, 10.0.0.0/24, 10.0.0.10-20": (
        "Addresses, networks or ranges, e.g. 10.0.0.5, 10.0.0.0/24, 10.0.0.10-20",
        "Adresses, réseaux ou plages, p. ex. 10.0.0.5, 10.0.0.0/24, 10.0.0.10-20",
        "Adrässe, Netz oder Beriich, z. B. 10.0.0.5, 10.0.0.0/24, 10.0.0.10-20"),
    "Scan {0}: {1} Ziele, {2} Ports": ("Scan {0}: {1} targets, {2} ports", "Scan {0} : {1} cibles, {2} ports",
                                       "Scan {0}: {1} Ziil, {2} Ports"),
    "CSV gespeichert: {0}": ("CSV saved: {0}", "CSV enregistré : {0}", "CSV gspicheret: {0}"),
}
LANGS = ("en", "fr", "gsw")

PRESET_CHOICES = (("eigene", ""), ("BMC-Dienste", "bmc"), ("Server-Dienste", "server"),
                  ("bekannte Dienste", "top"), ("alle 65535", "all"))


def register(api):
    def t(text):
        if api.lang in LANGS and text in TEXTS:
            return TEXTS[text][LANGS.index(api.lang)]
        return text

    state = {"page": None}

    def page():
        w = PortscanPage(api, t)
        state["page"] = w
        return w

    def closing():
        if state["page"] is not None:
            state["page"].stop()

    api.add_page(t("Portscan"), page, mark="⌁")
    api.on("closing", closing)


class PortscanPage(QWidget):
    COLS = ("Ziel", "Port", "Protokoll", "Dienst", "Zustand", "Banner")

    def __init__(self, api, t):
        super().__init__()
        self.api, self.t = api, t
        self.rows, self.results = [], queue.Queue()
        self.stop_flag, self.worker, self.total, self.done = threading.Event(), None, 0, 0
        lay = QVBoxLayout(self)
        note = QLabel(t("Nur Verbindungsaufbau, keine Anmeldung. Nur Netze scannen, für die du zuständig bist."))
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)

        form = QFormLayout()
        trow = QHBoxLayout()
        self.targets = QLineEdit()
        self.targets.setPlaceholderText(t("Adressen, Netze oder Bereiche, z. B. 10.0.0.5, 10.0.0.0/24, 10.0.0.10-20"))
        take = QPushButton(t("Aus Serverliste"))
        take.clicked.connect(self.from_servers)
        trow.addWidget(self.targets, 1)
        trow.addWidget(take)
        form.addRow(t("Ziele"), trow)

        prow = QHBoxLayout()
        self.ports = QLineEdit("bmc")
        self.preset = QComboBox()
        for label, key in PRESET_CHOICES:
            self.preset.addItem(t(label), key)
        self.preset.setCurrentIndex(1)
        self.preset.currentIndexChanged.connect(
            lambda _: self.preset.currentData() and self.ports.setText(self.preset.currentData()))
        prow.addWidget(self.ports, 1)
        prow.addWidget(QLabel(t("Vorlage")))
        prow.addWidget(self.preset)
        form.addRow(t("Ports"), prow)

        orow = QHBoxLayout()
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0.1, 10.0)
        self.timeout.setSingleStep(0.1)
        self.timeout.setValue(0.8)
        self.workers = QSpinBox()
        self.workers.setRange(1, 512)
        self.workers.setValue(64)
        self.banner = QCheckBox(t("Banner lesen"))
        self.ipmi = QCheckBox(t("IPMI über UDP 623"))
        self.ipmi.setChecked(True)
        self.only_open = QCheckBox(t("nur offene zeigen"))
        self.only_open.setChecked(True)
        self.only_open.toggled.connect(self.redraw)
        for wdg in (QLabel(t("Zeitlimit (s)")), self.timeout, QLabel(t("Gleichzeitig")), self.workers,
                    self.banner, self.ipmi, self.only_open):
            orow.addWidget(wdg)
        orow.addStretch(1)
        form.addRow("", orow)
        lay.addLayout(form)

        brow = QHBoxLayout()
        self.start_btn = QPushButton(t("Start"))
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton(t("Stopp"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        self.csv_btn = QPushButton(t("CSV speichern …"))
        self.csv_btn.clicked.connect(self.save_csv)
        self.progress = QProgressBar()
        self.status = QLabel("")
        for wdg in (self.start_btn, self.stop_btn, self.csv_btn):
            brow.addWidget(wdg)
        brow.addWidget(self.progress, 1)
        lay.addLayout(brow)
        lay.addWidget(self.status)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([t(c) for c in self.COLS])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        for c in range(len(self.COLS) - 1):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.drain)

    # ---------------------------------------------------------------- Eingaben
    def from_servers(self):
        ips = []
        for s in self.api.servers():
            for key in ("ip", "new_ip"):
                if s.get(key) and s[key] not in ips:
                    ips.append(s[key])
        if ips:
            self.targets.setText(", ".join(ips))

    def parsed(self):
        try:
            targets = scan.parse_targets(self.targets.text())
            ports = scan.parse_ports(self.ports.text())
        except ValueError as e:
            QMessageBox.warning(self, self.t("Eingabe prüfen"), self.t("Ungültige Angabe: {0}").format(e))
            return None
        if not targets:
            QMessageBox.warning(self, self.t("Eingabe prüfen"), self.t("Ungültige Angabe: {0}").format("–"))
            return None
        return targets, ports

    # ---------------------------------------------------------------- Ablauf
    def start(self, confirm=True):
        if self.running():
            return False
        got = self.parsed()
        if got is None:
            return False
        targets, ports = got
        secs, n = scan.estimate(targets, ports, self.workers.value(), self.timeout.value(), self.ipmi.isChecked())
        if n > scan.MAX_PROBES:
            QMessageBox.warning(self, self.t("Eingabe prüfen"),
                                self.t("Ungültige Angabe: {0}").format(f"{n} > {scan.MAX_PROBES}"))
            return False
        if confirm and n > scan.CONFIRM_ABOVE:
            ans = QMessageBox.question(self, self.t("Großer Scan"),
                                       self.t("{0} Prüfungen, im schlechtesten Fall etwa {1} Minuten. Starten?")
                                       .format(n, max(1, round(secs / 60))))
            if ans != QMessageBox.StandardButton.Yes:
                return False
        self.rows, self.results = [], queue.Queue()
        self.table.setRowCount(0)
        self.stop_flag = threading.Event()
        self.total, self.done = n, 0
        self.progress.setRange(0, n)
        self.progress.setValue(0)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.api.log(self.t("Scan {0}: {1} Ziele, {2} Ports").format(self.targets.text().strip()[:200],
                                                                      len(targets), len(ports)))
        opts = dict(workers=self.workers.value(), timeout=self.timeout.value(), banner=self.banner.isChecked(),
                    ipmi=self.ipmi.isChecked())
        self.n_targets = len(targets)
        self.worker = threading.Thread(target=self._run, args=(targets, ports, opts, self.stop_flag, self.results),
                                       daemon=True, name="portscan")
        self.worker.start()
        self.timer.start()
        self.lamp(True)
        return True

    def _run(self, targets, ports, opts, stop_flag, out):
        try:
            scan.scan(targets, ports, stopped=stop_flag.is_set, on_result=lambda r: out.put(("row", r)),
                      on_progress=lambda d, t: out.put(("progress", d)), **opts)
        except Exception as e:          # noqa: BLE001
            out.put(("error", f"{type(e).__name__}: {e}"))
        out.put(("end", None))

    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def lamp(self, on):
        """Gelbe Leuchte am Menüpunkt wie beim Testlabor, solange der Scan läuft (set_lamp ab BMC-Steward 3.14.0)."""
        set_lamp = getattr(self.api, "set_lamp", None)
        if set_lamp is not None:
            set_lamp("#f2c200" if on else None)

    def stop(self):
        self.stop_flag.set()

    def drain(self):
        ended, added = False, False
        try:
            while True:
                kind, val = self.results.get_nowait()
                if kind == "row":
                    self.rows.append(val)
                    added = True
                elif kind == "progress":
                    self.done = val
                elif kind == "error":
                    self.api.log(val)
                    self.status.setText(val)
                elif kind == "end":
                    ended = True
        except queue.Empty:
            pass
        if added:
            self.redraw()
        self.progress.setValue(self.done)
        n_open = sum(r["state"] == "offen" for r in self.rows)
        if ended:
            self.timer.stop()
            self.worker = None
            self.lamp(False)
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            if self.stop_flag.is_set():
                msg = self.t("Abgebrochen")
            else:
                hosts = len({r["host"] for r in self.rows if r["state"] == "offen"})
                msg = self.t("Fertig: {0} offene Ports auf {1} Zielen").format(n_open, hosts)
            self.status.setText(msg)
            self.api.log(msg)
        else:
            self.status.setText(self.t("{0} von {1} geprüft, {2} offen").format(self.done, self.total, n_open))

    def visible_rows(self):
        rows = [r for r in self.rows if r["state"] == "offen" or not self.only_open.isChecked()]
        return sorted(rows, key=lambda r: (r["host"], r["proto"], r["port"]))

    def redraw(self):
        rows = self.visible_rows()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            port = QTableWidgetItem()
            port.setData(0, r["port"])     # numerisch sortieren
            vals = (QTableWidgetItem(r["host"]), port, QTableWidgetItem(r["proto"].upper()),
                    QTableWidgetItem(r["service"]), QTableWidgetItem(self.t(r["state"])),
                    QTableWidgetItem(r["banner"]))
            for c, item in enumerate(vals):
                self.table.setItem(i, c, item)
        self.table.setSortingEnabled(True)

    def save_csv(self, path=None):
        if not path:
            default = str(self.api.data_dir / f"portscan-{scan.stamp()}.csv")
            path, _ = QFileDialog.getSaveFileName(self, self.t("CSV speichern …"), default, "CSV (*.csv)")
        if not path:
            return ""
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(scan.to_csv(self.visible_rows()))
        msg = self.t("CSV gespeichert: {0}").format(path)
        self.status.setText(msg)
        self.api.log(msg)
        return path
