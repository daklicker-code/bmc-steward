"""Beispiel-Plugin für BMC-Steward (Plugin-API 1).

Installieren: diesen Ordner nach <Datenordner>/plugins/beispiel/ kopieren, BMC-Steward starten,
unter ⚙ → „Plugins“ freigeben und neu starten.

Die Schnittstelle `api`:
    api.id, api.name, api.version, api.app_version, api.api_version, api.lang (de, en, fr, gsw)
    api.data_dir                       eigener Ordner (nur für den Benutzer lesbar)
    api.log(text), api.debug(text)     ins gemeinsame Protokoll
    api.on(ereignis, fn)               window_ready(), server_done(ip), run_finished(code), closing()
    api.add_page(titel, widget_oder_funktion, mark="◆")
    api.servers()                      Serverliste ohne Passwörter (ip, new_ip, mask, gateway, hostname, serial, done)
    api.add_console(titel, widget)     eigene Konsole als Reiter im Monitor; Menüpunkt leuchtet gelb, solange sie offen ist
    api.set_lamp(True)                 gelbe Leuchte am Menüpunkt wie beim Testlabor („läuft“), None = aus
    api.publish(thema, daten)          JSON-fähige Daten für andere Plugins (ab 3.20.0), api.published(thema) liest sie
    api.subscribe(thema, fn)           fn(daten) bei jeder Veröffentlichung, z. B. „aufbauplaner.plan“ (Plan des Aufbauplaners)
"""
import csv
import time

from PyQt6.QtWidgets import QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


def register(api):
    log_file = api.data_dir / "fertig.csv"

    def server_done(ip):
        with open(log_file, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow([time.strftime("%Y-%m-%d %H:%M:%S"), ip])
        api.log(f"{ip} in {log_file} vermerkt")

    def page():
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(f"Fertig umgestellte Server landen in {log_file}."))
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Aktuelle IP", "Neue IP", "Hostname", "fertig"])
        lay.addWidget(table, 1)

        def refresh():
            rows = api.servers()
            table.setRowCount(len(rows))
            for r, s in enumerate(rows):
                for c, key in enumerate(("ip", "new_ip", "hostname")):
                    table.setItem(r, c, QTableWidgetItem(s[key]))
                table.setItem(r, 3, QTableWidgetItem("✓" if s["done"] else ""))

        btn = QPushButton("Aktualisieren")
        btn.clicked.connect(refresh)
        lay.addWidget(btn)
        return w

    api.on("server_done", server_done)
    api.add_page("Fertig-Liste", page, mark="✎")
