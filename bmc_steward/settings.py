"""BMC-Steward: Einstellungsmanager.

Alle Vorlieben an einem Ort, gespeichert in <Datenordner>/bmc-einstellungen.json (nur für den eigenen
Benutzer lesbar, ohne Passwörter): Darstellung (Schriftart, Schriftgröße, Design), Protokoll (Stufe beim
Start, fortlaufende Protokolldatei, Aufbewahrung, Zeilen im Speicher), Systemaudit (Auslesen bei Auswahl,
Ablageordner, Vorlagen für Betreff und Text der E-Mail), E-Mail-Versand (SMTP) und Experteneinstellungen
(Kontroll-Wartezeit nach dem Umstellen, Zeitlimit für BMC-Anfragen) und die Freigabe von Plugins. Das SMTP-Passwort liegt nie in der Datei,
höchstens im System-Schlüsselbund.
"""
import json
import time
from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFontComboBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                             QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from . import i18n, theme
from .i18n import T

# Schlüssel: (Vorgabe, kleinster, größter Wert) für Zahlen, sonst (Vorgabe,)
SPEC = {
    "font_family": ("",),                 # leer = Systemschrift
    "font_size": (0, 0, 20),              # 0 = Systemgröße, sonst 7 bis 20 pt
    "log_level": ("info",),               # info | debug
    "log_file": (False,),                 # Protokoll fortlaufend in <Datenordner>/protokolle/ schreiben
    "log_keep_days": (30, 1, 3650),
    "log_max_entries": (20000, 1000, 200000),
    "audit_auto_read": (True,),
    "audit_dir": ("",),                   # leer = persönlicher Ordner
    "audit_archive": (True,),             # jedes Audit-PDF zusätzlich nach <Datenordner>/audits/ kopieren
    "mail_subject": ("",),                # leer = Vorgabe der eingestellten Sprache (mail_texts)
    "mail_body": ("",),
    "smtp_host": ("",),
    "smtp_port": (587, 1, 65535),
    "smtp_security": ("starttls",),       # starttls | ssl | none
    "smtp_user": ("",),
    "smtp_from": ("",),
    "verify_wait": (240, 30, 1800),       # Sekunden: Kontrolle der neuen Adressen nach dem Umstellen
    "http_timeout": (30, 5, 300),         # Sekunden: Zeitlimit für BMC-Anfragen ohne eigenes Limit
}
CHOICES = {"log_level": ("info", "debug"), "smtp_security": ("starttls", "ssl", "none")}


def mail_texts(s=None):
    """(Betreff, Text) der Audit-Mail: eigene Vorlage, sonst die Vorgabe in der eingestellten Sprache."""
    s = s or load()
    return (s["mail_subject"].strip() or T("Systemaudit {seriennummer} vom {datum}"),
            s["mail_body"].strip() or T("Hallo,\n\nim Anhang das Systemaudit {seriennummer} ({modell}) vom "
                                        "{datum}.\n\nViele Grüße\n{mitarbeiter}"))


def settings_file():
    return i18n.DATA / "bmc-einstellungen.json"


def log_dir():
    return i18n.DATA / "protokolle" / "laufend"


def clean(data):
    """Nur bekannte Schlüssel, Werte im passenden Typ und Bereich; Fehlendes aus den Vorgaben."""
    out = {}
    for key, spec in SPEC.items():
        default, val = spec[0], data.get(key, spec[0])
        try:
            if isinstance(default, bool):
                val = bool(val)
            elif isinstance(default, int):
                val = min(max(int(val), spec[1]), spec[2])
                if key == "font_size" and 0 < val < 7:
                    val = 7
            else:
                val = str(val if val is not None else "")
                val = val if key in ("mail_subject", "mail_body") else val.strip()
        except (TypeError, ValueError):
            val = default
        if key in CHOICES and val not in CHOICES[key]:
            val = default
        out[key] = val
    return out


def load():
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return clean(data if isinstance(data, dict) else {})


def save(data):
    """Speichert (unbekannte Schlüssel fallen weg) und gibt die bereinigten Werte zurück."""
    merged = load()
    merged.update(data)
    merged = clean(merged)
    i18n.write_private(settings_file(), json.dumps(merged, ensure_ascii=False, indent=1))
    return merged


def get(key):
    return load()[key]


def cleanup_logs(days):
    """Protokolldateien, die älter als `days` Tage sind, löschen."""
    limit = time.time() - days * 86400
    for f in log_dir().glob("bmc-steward-*.log"):
        try:
            if f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass


def apply(app=None, s=None):
    """Einstellungen wirken lassen: Schrift, Protokoll, Zeitlimit. Ohne `app` nur Protokoll und Zeitlimit."""
    from . import applog
    from .redfish import RedfishClient
    s = s or load()
    if app is not None:
        font = QFont(app.font())
        if s["font_family"]:
            font.setFamily(s["font_family"])
        if s["font_size"]:
            font.setPointSize(s["font_size"])
        if not (s["font_family"] or s["font_size"]):
            from PyQt6.QtGui import QFontDatabase
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        app.setFont(font)
    applog.LOG.set_level(applog.DEBUG if s["log_level"] == "debug" else applog.INFO)
    applog.LOG.set_max(s["log_max_entries"])
    if s["log_file"]:
        cleanup_logs(s["log_keep_days"])
        applog.LOG.set_file(log_dir() / ("bmc-steward-" + time.strftime("%Y%m%d") + ".log"))
    else:
        applog.LOG.set_file(None)
    RedfishClient.TIMEOUT = s["http_timeout"]
    return s


class SettingsDialog(QDialog):
    """Einstellungen mit Reitern; „Speichern“ wirkt sofort."""

    def __init__(self, parent=None, app=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle(T("Einstellungen"))
        self.resize(640, 520)
        s = load()
        self.widgets = {}
        tabs = QTabWidget()

        # Darstellung
        page, f = self._page(tabs, T("Darstellung"))
        fam = QFontComboBox()
        fam.setEditable(False)
        sys_font = QLabel()
        if s["font_family"]:
            fam.setCurrentFont(QFont(s["font_family"]))
        use_sys = QCheckBox(T("Systemschrift verwenden"))
        use_sys.setChecked(not s["font_family"])
        fam.setEnabled(bool(s["font_family"]))
        use_sys.toggled.connect(lambda on: fam.setEnabled(not on))
        self.use_sys_font = use_sys
        f.addRow(T("Schriftart"), use_sys)
        f.addRow("", fam)
        self.widgets["font_family"] = fam
        size = QSpinBox()
        size.setRange(0, 20)
        size.setSpecialValueText(T("Systemgröße"))
        size.setSuffix(" pt")
        size.setValue(s["font_size"])
        f.addRow(T("Schriftgröße"), size)
        self.widgets["font_size"] = size
        design = QComboBox()
        for key, text in (("auto", T("automatisch")), ("light", T("hell")), ("dark", T("dunkel"))):
            design.addItem(text, key)
        design.setCurrentIndex(max(0, design.findData(theme.load_choice())))
        f.addRow(T("Design"), design)
        self.design = design
        sys_font.setText(T("Die Sprache stellst du links unten im Menü um."))
        sys_font.setObjectName("muted")
        f.addRow("", sys_font)

        # Protokoll
        page, f = self._page(tabs, T("Protokoll"))
        lvl = QComboBox()
        lvl.addItem(T("Info"), "info")
        lvl.addItem(T("Debug"), "debug")
        lvl.setCurrentIndex(lvl.findData(s["log_level"]))
        f.addRow(T("Stufe"), lvl)
        self.widgets["log_level"] = lvl
        lf = QCheckBox(T("Protokoll fortlaufend in eine Datei schreiben (je Tag eine Datei)"))
        lf.setChecked(s["log_file"])
        lf.setToolTip(T("Ordner: {0}\nPasswörter stehen nie darin.").format(log_dir()))
        f.addRow("", lf)
        self.widgets["log_file"] = lf
        keep = QSpinBox()
        keep.setRange(1, 3650)
        keep.setSuffix(T(" Tage"))
        keep.setValue(s["log_keep_days"])
        f.addRow(T("Protokolldateien aufbewahren"), keep)
        self.widgets["log_keep_days"] = keep
        mx = QSpinBox()
        mx.setRange(1000, 200000)
        mx.setSingleStep(1000)
        mx.setValue(s["log_max_entries"])
        mx.setToolTip(T("So viele Zeilen hält das Protokoll im Speicher; ältere fallen heraus."))
        f.addRow(T("Zeilen im Speicher"), mx)
        self.widgets["log_max_entries"] = mx
        open_btn = QPushButton(T("Ordner öffnen"))
        open_btn.clicked.connect(self.open_log_dir)
        f.addRow("", open_btn)

        # Systemaudit
        page, f = self._page(tabs, T("Systemaudit"))
        auto = QCheckBox(T("Server bei Auswahl in der Liste sofort auslesen"))
        auto.setChecked(s["audit_auto_read"])
        f.addRow("", auto)
        self.widgets["audit_auto_read"] = auto
        arch = QCheckBox(T("Jedes Audit-PDF automatisch im Datenordner ablegen (audits/<Seriennummer>/)"))
        arch.setChecked(s["audit_archive"])
        f.addRow("", arch)
        self.widgets["audit_archive"] = arch
        d = QLineEdit(s["audit_dir"])
        d.setPlaceholderText(str(Path.home()))
        pick = QPushButton("…")
        pick.setFixedWidth(36)
        pick.clicked.connect(lambda: self.pick_dir(d))
        row = QHBoxLayout()
        row.addWidget(d, 1)
        row.addWidget(pick)
        f.addRow(T("Ordner für PDFs"), row)
        self.widgets["audit_dir"] = d
        subj = QLineEdit(s["mail_subject"])
        subj.setPlaceholderText(mail_texts(clean({}))[0])
        f.addRow(T("E-Mail-Betreff"), subj)
        self.widgets["mail_subject"] = subj
        body = QPlainTextEdit(s["mail_body"])
        body.setPlaceholderText(mail_texts(clean({}))[1])
        body.setFixedHeight(110)
        f.addRow(T("E-Mail-Text"), body)
        self.widgets["mail_body"] = body
        hint = QLabel(T("Platzhalter: {seriennummer}, {modell}, {hersteller}, {datum}, {mitarbeiter}, {anzahl}"))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        f.addRow("", hint)

        # E-Mail
        page, f = self._page(tabs, T("E-Mail"))
        host = QLineEdit(s["smtp_host"])
        host.setPlaceholderText("smtp.example.org")
        f.addRow(T("SMTP-Server"), host)
        self.widgets["smtp_host"] = host
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(s["smtp_port"])
        f.addRow(T("Port"), port)
        self.widgets["smtp_port"] = port
        sec = QComboBox()
        for key, text in (("starttls", T("STARTTLS (meist Port 587)")), ("ssl", T("SSL/TLS (meist Port 465)")),
                          ("none", T("unverschlüsselt (nur im eigenen Netz)"))):
            sec.addItem(text, key)
        sec.setCurrentIndex(sec.findData(s["smtp_security"]))
        f.addRow(T("Verschlüsselung"), sec)
        self.widgets["smtp_security"] = sec
        user = QLineEdit(s["smtp_user"])
        user.setPlaceholderText(T("leer = ohne Anmeldung"))
        f.addRow(T("Benutzer"), user)
        self.widgets["smtp_user"] = user
        sender = QLineEdit(s["smtp_from"])
        sender.setPlaceholderText("audit@example.org")
        f.addRow(T("Absender"), sender)
        self.widgets["smtp_from"] = sender
        note = QLabel(T("Das Passwort wird beim Senden abgefragt und auf Wunsch im System-Schlüsselbund gemerkt, "
                        "nie in der Einstellungsdatei. Zertifikate des Mailservers werden geprüft."))
        note.setWordWrap(True)
        note.setObjectName("muted")
        f.addRow("", note)

        # Experten
        page, f = self._page(tabs, T("Experten"))
        warn = QLabel(T("Nur ändern, wenn du weißt, warum. Die Vorgaben passen für die meisten BMCs."))
        warn.setWordWrap(True)
        f.addRow(warn)
        vw = QSpinBox()
        vw.setRange(30, 1800)
        vw.setSuffix(" s")
        vw.setValue(s["verify_wait"])
        vw.setToolTip(T("So lange wartet „Neuware“ nach dem Umstellen, bis jeder Server unter der neuen Adresse "
                        "antwortet. HPE iLO braucht nach dem Neustart 1 bis 3 Minuten."))
        f.addRow(T("Kontrolle der neuen Adressen"), vw)
        self.widgets["verify_wait"] = vw
        to = QSpinBox()
        to.setRange(5, 300)
        to.setSuffix(" s")
        to.setValue(s["http_timeout"])
        to.setToolTip(T("Zeitlimit für Anfragen an den BMC, die kein eigenes Limit haben. Langsame BMCs: höher."))
        f.addRow(T("Zeitlimit für BMC-Anfragen"), to)
        self.widgets["http_timeout"] = to

        # Plugins: Freigaben je Plugin (eigene Datei bmc-plugins.json, siehe plugins.py)
        from . import plugins
        self.plugin_page = plugins.settings_page()
        tabs.addTab(self.plugin_page, T("Plugins"))

        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        info = QLabel(T("Gespeichert in {0}").format(settings_file()))
        info.setObjectName("muted")
        info.setWordWrap(True)
        lay.addWidget(info)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
                                | QDialogButtonBox.StandardButton.RestoreDefaults)
        btns.button(QDialogButtonBox.StandardButton.Save).setText(T("Speichern"))
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(T("Abbrechen"))
        btns.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText(T("Vorgaben"))
        btns.accepted.connect(self.save_and_close)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.defaults)
        lay.addWidget(btns)

    @staticmethod
    def _page(tabs, title):
        page = QWidget()
        f = QFormLayout(page)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        tabs.addTab(page, title)
        return page, f

    def values(self):
        out = {}
        for key, w in self.widgets.items():
            if isinstance(w, QFontComboBox):
                out[key] = "" if self.use_sys_font.isChecked() else w.currentFont().family()
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            elif isinstance(w, QSpinBox):
                out[key] = w.value()
            elif isinstance(w, QComboBox):
                out[key] = w.currentData()
            elif isinstance(w, QPlainTextEdit):
                out[key] = w.toPlainText()
            else:
                out[key] = w.text()
        return out

    def save(self):
        s = save(self.values())
        self.plugin_page.apply(self)
        theme.save_choice(self.design.currentData() or "auto")
        if self.app is not None:
            theme.apply(self.app, self.design.currentData() or "auto")
        apply(self.app, s)
        if self.app is not None:
            self.app.setStyleSheet(theme.stylesheet(theme.current()))
        return s

    def save_and_close(self):
        try:
            self.save()
        except OSError as e:
            QMessageBox.warning(self, T("Einstellungen"), T("Nicht gespeichert: {0}").format(e))
            return
        self.accept()

    def defaults(self):
        d = clean({})
        self.use_sys_font.setChecked(True)
        for key, w in self.widgets.items():
            val = d[key]
            if isinstance(w, QFontComboBox):
                continue
            if isinstance(w, QCheckBox):
                w.setChecked(val)
            elif isinstance(w, QSpinBox):
                w.setValue(val)
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(val)))
            elif isinstance(w, QPlainTextEdit):
                w.setPlainText(val)
            else:
                w.setText(val)
        self.design.setCurrentIndex(0)

    def pick_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, T("Ordner für PDFs"), edit.text() or str(Path.home()))
        if d:
            edit.setText(d)

    def open_log_dir(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(i18n.private_dir(log_dir()))))
