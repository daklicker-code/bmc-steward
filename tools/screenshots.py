"""Screenshots für README/GitHub erzeugen (docs/screenshot*.png).

Läuft ohne Bildschirm (Qt offscreen) und ohne echte Hardware: virtuelle BMCs aller vier Hersteller laufen im
selben Prozess (tools/vbmc), die Seiten zeigen also echte Abläufe (Trockenlauf, Verbindungstest, UEFI-Attribute,
Mainboard-Tausch). Arbeitsdaten landen in einem temporären Ordner, nie im Repo.

    python3 tools/screenshots.py                 # Englisch, helles Design, nach docs/
    python3 tools/screenshots.py --lang de --theme dark --out /tmp/shots
"""
import argparse
import json
import os
import ssl
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BMC_STEWARD_DATA"] = tempfile.mkdtemp(prefix="bmc-steward-shots-")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "vbmc"))

SIZE = (1440, 900)
PW = {"dell": "calvin", "hpe": "Admin123!", "lenovo": "PASSW0RD", "smc": "ADMIN"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en")
    ap.add_argument("--theme", default="light")
    ap.add_argument("--out", default=str(REPO / "docs"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from bmc_steward import i18n, theme
    i18n.set_language(args.lang)
    theme.apply(app, args.theme)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    import vbmc

    # --- virtuelle BMCs im selben Prozess ---
    cert, key = vbmc.ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    bmcs = {}
    for idx, v in enumerate(("dell", "hpe", "lenovo", "smc", "dell", "hpe"), 1):
        fleet = vbmc.Fleet(ctx, [0])
        b = vbmc.BMC(v, "127.0.0.1", 0, idx=idx)
        fleet.start(b)
        bmcs[idx] = (v, f"127.0.0.1:{b._redfish[0].socket.getsockname()[1]}", b)

    # Bild 7 zeigt die Seite des mitgelieferten Aufbauplaner-Plugins (lädt von selbst)
    from bmc_steward.main import Window
    w = Window()
    w.resize(*SIZE)
    w.show()

    def pump(sec=0.3, until=None, limit=40):
        end = time.monotonic() + limit
        stop = time.monotonic() + sec
        while time.monotonic() < (end if until else stop):
            app.processEvents()
            time.sleep(0.02)
            if until and until():
                break
        for _ in range(5):
            app.processEvents()

    def page(name):
        names = [w.tabs.menu.item(i).text() for i in range(w.tabs.count())]
        w.tabs.setCurrentIndex(names.index(i18n.T(name)))
        pump(0.4)

    # Anzeige: statt 127.0.0.1:<Port> realistische Adressen aus dem Staging- und Zielnetz
    fake = {}
    for n, (v, addr, b) in bmcs.items():
        fake[addr] = f"192.0.2.{100 + n}"
        fake["Port " + addr.split(":")[1]] = "Port 443"
        fake[f"127.0.1.{n}"] = f"10.20.0.{10 + n}"

    def disguise():
        from PyQt6.QtWidgets import QComboBox, QLabel, QLineEdit, QTableWidget, QTextEdit

        def sub(text):
            for a, b in sorted(fake.items(), key=lambda kv: -len(kv[0])):
                text = text.replace(a, b)
            return text
        for te in w.findChildren(QTextEdit):
            h = te.toHtml()
            if "127.0." in h:
                bar = te.verticalScrollBar().value()
                te.setHtml(sub(h))
                te.verticalScrollBar().setValue(bar)
        for t in w.findChildren(QTableWidget):
            t.blockSignals(True)                    # sonst prüft die Serverliste die „neue“ Adresse erneut
            for r in range(t.rowCount()):
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    if it and "127.0." in it.text():
                        it.setText(sub(it.text()))
            t.blockSignals(False)
        for x in w.findChildren(QLabel) + w.findChildren(QLineEdit):
            if "127.0." in x.text():
                x.setText(sub(x.text()))
        for cb in w.findChildren(QComboBox):
            if cb.isEditable() and "127.0." in cb.currentText():
                cb.setEditText(sub(cb.currentText()))
        w.status.setText(sub(w.status.text()))
        pump(0.2)

    from bmc_steward.monitor import MonitorWidget
    for mon in w.findChildren(MonitorWidget):        # Logo im Monitor immer vollständig, mit grünem Schein
        mon._logo_progress = lambda: 1.0
        mon._logo_glow = lambda: 0.55
    for f in getattr(w, "deps_footers", []):         # Hinweise „optionales Paket fehlt“ gehören nicht ins Bild
        if f is not None:
            f.setVisible(False)

    def shot(fname):
        for mon in w.findChildren(MonitorWidget):
            mon.update()
        disguise()
        w.grab().save(str(out / fname))
        print("  ", out / fname)

    # --- Neuware: Serverliste mit sechs Geräten, Trockenlauf ---
    w.new_user.setText("steward")
    w.new_pass.setText("S3cure-Admin!")
    w.def_mask.setText("255.255.255.0")
    w.def_gw.setText("10.20.0.1")
    w.table.setRowCount(0)
    for n, (v, addr, b) in bmcs.items():
        w.add_row([addr, PW[v], f"127.0.1.{n}", "255.255.255.0", "127.0.1.254", f"srv-{v}-{n:02d}", ""])
    fake["127.0.1.254"] = "10.20.0.1"
    pump(until=lambda: all(w.cell(r, w.vendor_col) for r in range(w.table.rowCount())), limit=30)
    shown = {"dell": "Dell iDRAC9", "hpe": "HPE iLO 6", "lenovo": "Lenovo XCC2", "smc": "Supermicro X13"}
    serials = ["7XK2Q83", "CZJ2410ABC", "J30AB12", "S41275X2", "9FT4R73", "CZJ2410ABD"]
    page("Neuware")
    w.run()
    pump(until=lambda: not w.running(), limit=90)
    w.table.blockSignals(True)
    for r, (v, addr, b) in enumerate(bmcs.values()):
        w.table.item(r, 6).setText(serials[r])
        from PyQt6.QtWidgets import QTableWidgetItem
        w.table.setItem(r, w.vendor_col, QTableWidgetItem(shown[v]))
    w.table.blockSignals(False)
    shot("screenshot.png")

    # --- DHCP-Server: Geräteliste ---
    page("DHCP-Server")
    w.show_leases([("192.0.2.101", "d0:94:66:1a:2b:01", "idrac-7XK2Q83"),
                   ("192.0.2.102", "94:40:c9:3c:4d:02", "ILOCZJ2410ABC"),
                   ("192.0.2.103", "08:94:ef:5e:6f:03", "XCC-7X06-J30AB12"),
                   ("192.0.2.104", "3c:ec:ef:7a:8b:04", "")])
    w.dhcp_log.setPlainText("dnsmasq: started, version 2.90\n"
                            "dnsmasq-dhcp: DHCP, IP range 192.0.2.100 -- 192.0.2.200, lease time 1h\n"
                            "dnsmasq-dhcp: DHCPACK(eth0) 192.0.2.101 d0:94:66:1a:2b:01 idrac-7XK2Q83\n"
                            "dnsmasq-dhcp: DHCPACK(eth0) 192.0.2.102 94:40:c9:3c:4d:02 ILOCZJ2410ABC\n"
                            "dnsmasq-dhcp: DHCPACK(eth0) 192.0.2.103 08:94:ef:5e:6f:03 XCC-7X06-J30AB12\n"
                            "dnsmasq-dhcp: DHCPACK(eth0) 192.0.2.104 3c:ec:ef:7a:8b:04")
    pump(0.3)
    shot("screenshot-1-dhcp.png")

    # --- Bestandsserver: Verbindungstest ---
    page("Bestandsserver")
    w.ex_table.setRowCount(0)
    for n, (v, addr, b) in list(bmcs.items())[:4]:
        host, port = addr.split(":")
        w.ex_add_row([host, port, "", PW[v]])
    w.ex_run()
    pump(until=lambda: not (w.ex_worker and w.ex_worker.isRunning()), limit=90)
    for r in range(w.ex_table.rowCount()):                     # Adresse und Port wie bei echten Geräten
        addr = f"{w.ex_cell(r, 0)}:{w.ex_cell(r, 1)}"
        w.ex_table.item(r, 0).setText(fake.get(addr, w.ex_cell(r, 0)))
        w.ex_table.item(r, 1).setText("443")
    shot("screenshot-2b-existing.png")

    # --- UEFI-Einstellungen: Attribute eines Dell lesen ---
    page("UEFI-Einstellungen")
    w.bios_ref.setEditText(bmcs[1][1])
    w.bios_pass.setText(PW["dell"])
    w.bios_read()
    pump(until=lambda: w.bios_read_btn.isEnabled(), limit=60)
    shot("screenshot-3-uefi.png")

    page("Dienste")
    shot("screenshot-4-services.png")

    # --- Mainboard-Tausch: HPE mit Sicherung des alten Boards ---
    from bmc_steward.redfish import RedfishClient
    old = bmcs[6]
    c = RedfishClient(dry=False, log=lambda *a, **k: None)
    c.cur_user = "Administrator"
    path = c.backup(old[1], "Administrator", PW["hpe"], "hpe", "neuware")
    c.close_sessions()
    doc = json.loads(path.read_text(encoding="utf-8"))
    for rpath, body in doc["resources"].items():
        if "/EthernetInterfaces/" in rpath and isinstance(body, dict):
            body["HostName"] = "srv-hpe-02"
            body["IPv4StaticAddresses"] = [{"Address": "10.20.0.12", "SubnetMask": "255.255.255.0",
                                            "Gateway": "10.20.0.1"}]
            body["IPv4Addresses"] = body["IPv4StaticAddresses"]
            break
    label = "CZJ2410ABC"
    d = i18n.DATA / "sicherungen" / label
    d.mkdir(parents=True, exist_ok=True)
    (d / path.name).write_text(json.dumps(doc), encoding="utf-8")
    page("Mainboard-Tausch")
    w.swap.set_row(bmcs[2][1], PW["hpe"], label)
    w.swap.pid.setText("P52534-B21")
    w.swap.check()
    pump(until=lambda: not w.swap.thread.isRunning(), limit=60)
    w.swap.execute()
    pump(until=lambda: not (w.swap.thread and w.swap.thread.isRunning()), limit=60)
    w.swap.out.verticalScrollBar().setValue(0)
    shot("screenshot-8-board.png")

    page("UEFI-Konsole")
    shot("screenshot-5-console.png")

    page("Hilfe")
    shot("screenshot-6-help.png")

    planner = next(widget for plugin, widget in w.plugin_host.page_widgets if plugin.id == "aufbauplaner")
    w.tabs.setCurrentIndex(w.tabs.indexOf(planner))
    pump(0.4)
    planner.planner_demo()
    pump(1.0)
    shot("screenshot-7-planner.png")

    w.dirty = False
    planner.plan_dirty = False
    os._exit(0)                                     # virtuelle BMCs nicht einzeln herunterfahren


if __name__ == "__main__":
    main()
