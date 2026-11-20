"""BMC-Steward: Seite „Systemaudit“ – Audit-Blatt eines Servers als PDF.

Liest per Redfish nur lesend (GET, ein Anmeldeversuch) Hersteller, Modell und Seriennummer sowie die
Bauteile mit Part- und Seriennummern: Arbeitsspeicher, Prozessoren, Netzteile und Datenträger. Gleiche
Bauteile (gleiche Partnummer und Bezeichnung) werden zu einer Zeile mit Anzahl gebündelt. Alles bleibt in der
Tabelle von Hand änderbar; Checkliste, Bemerkung und Bearbeiter kommen dazu. „Als PDF speichern“ zeichnet das
Blatt (A4 quer): Kopf mit Hersteller/Modell/Seriennummer, Checkliste, Bemerkung, Unterschriftsfeld und rechts
die Bauteiltabelle; reicht eine Seite nicht, geht die Tabelle auf weiteren Seiten weiter.
"""
from PyQt6.QtCore import QDate, QMarginsF, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from .i18n import T
from .redfish import RedfishClient

ROWS_PER_PAGE = 24


def check_labels():
    """Punkte der Checkliste wie auf der Papiervorlage."""
    return [T("Systemtest erfolgreich"), T("FW-Update durchgeführt (optional)"),
            T("Datenträger gelöscht oder entsorgt"), T("Service-Prozessor zurückgesetzt"),
            T("Aufkleber entfernt"), T("System einbuchen")]


def group_items(items):
    """Bauteile mit gleicher Partnummer und Bezeichnung zu [Partnummer, Bezeichnung, Anzahl, [Seriennummern]]
    bündeln; Reihenfolge des ersten Auftretens bleibt."""
    rows, index = [], {}
    for it in items:
        key = (it.get("part", ""), it.get("component", ""))
        if key not in index:
            index[key] = len(rows)
            rows.append([key[0], key[1], 0, []])
        row = rows[index[key]]
        row[2] += 1
        if it.get("serial"):
            row[3].append(it["serial"])
    return rows


def _clean(v):
    return " ".join(str(v or "").split())


def _absent(doc):
    return (doc.get("Status") or {}).get("State") == "Absent"


def _part(doc):
    """Ersatzteilnummer (HPE: SparePartNumber, auch unter Oem), sonst die Partnummer."""
    for oem in (doc.get("Oem") or {}).values():
        if isinstance(oem, dict) and oem.get("SparePartNumber"):
            return _clean(oem["SparePartNumber"])
    return _clean(doc.get("SparePartNumber") or doc.get("PartNumber"))


def _size(gb):
    if gb >= 1000:
        return f"{gb / 1000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " TB"
    return f"{gb:g} GB"


class AuditEngine(RedfishClient):
    """Nur lesend: Kopf und Bauteile eines Servers aus Redfish."""

    STRICT_NEW = False          # ein Gerät zum ersten Mal zu sehen ist hier normal: nur warnen

    def __init__(self, log=print, stopped=lambda: False):
        super().__init__(dry=False, log=log, stopped=stopped)

    def get(self, ip, pw, path):
        code = self.api(ip, self.cur_user, pw, "GET", path, timeout=20)
        return self.body if self.ok(code) and isinstance(self.body, dict) else None

    def members(self, ip, pw, path):
        doc = self.get(ip, pw, path) or {}
        for m in doc.get("Members") or []:
            sub = self.get(ip, pw, m.get("@odata.id", "")) if isinstance(m, dict) else None
            if sub:
                yield sub

    def read(self, ip, user, pw):
        res = {"ok": False, "text": "", "vendor": "", "manufacturer": "", "model": "", "serial": "", "items": []}
        if self.tls_mode(ip) is None:
            res["text"] = T("Kein HTTPS-Handshake möglich")
            return res
        v = self.vendor(ip)
        if v == "hpe_old":
            res["text"] = T("iLO 3/4 liefert kein Inventar über Redfish. Bauteile von Hand eintragen.")
            return res
        if v not in self.NAME:
            res["text"] = T("Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)")
            return res
        res["vendor"] = v
        self.cur_user = user or self.DEFAULT_USER[v]
        self.log(T('  Hersteller: {0}, Anmeldung als {1}').format(self.NAME[v], self.cur_user))
        sysp = self.sys_path(v)
        code = self.api(ip, self.cur_user, pw, "GET", sysp, timeout=20)
        if code == 401:
            res["text"] = T("Anmeldung abgelehnt (Benutzer oder Passwort falsch, oder Konto gesperrt)")
            return res
        if not self.ok(code) or not isinstance(self.body, dict):
            res["text"] = T('Anmeldung nicht möglich (HTTP {0})').format(code or T("keine Verbindung"))
            return res
        s = self.body
        serial = s.get("SKU") if v == "dell" and s.get("SKU") else s.get("SerialNumber")
        res.update(ok=True, manufacturer=_clean(s.get("Manufacturer")), model=_clean(s.get("Model")),
                   serial=_clean(serial))
        items = res["items"]
        for d in self.members(ip, pw, sysp + "/Memory"):
            if _absent(d) or not d.get("CapacityMiB"):
                continue
            items.append({"part": _part(d), "component": T("DIMM {0} GB").format(int(d["CapacityMiB"]) // 1024),
                          "serial": _clean(d.get("SerialNumber"))})
        for d in self.members(ip, pw, sysp + "/Processors"):
            if _absent(d) or (d.get("ProcessorType") not in (None, "CPU")):
                continue
            items.append({"part": _part(d), "component": "CPU " + _clean(d.get("Model") or d.get("Name")),
                          "serial": _clean(d.get("SerialNumber"))})
        seen = set()
        for ch in self.members(ip, pw, "/redfish/v1/Chassis"):
            psus = []
            if isinstance(ch.get("Power"), dict):
                psus = (self.get(ip, pw, ch["Power"].get("@odata.id", "")) or {}).get("PowerSupplies") or []
            elif isinstance(ch.get("PowerSubsystem"), dict):
                sub = self.get(ip, pw, ch["PowerSubsystem"].get("@odata.id", "")) or {}
                if isinstance(sub.get("PowerSupplies"), dict):
                    psus = list(self.members(ip, pw, sub["PowerSupplies"].get("@odata.id", "")))
            for d in psus:
                if not isinstance(d, dict) or _absent(d):
                    continue
                sn = _clean(d.get("SerialNumber"))
                if sn and sn in seen:           # dasselbe Netzteil unter mehreren Chassis
                    continue
                seen.add(sn)
                watts = d.get("PowerCapacityWatts")
                comp = T("Netzteil {0} W").format(watts) if watts else _clean(d.get("Model") or d.get("Name"))
                items.append({"part": _part(d), "component": comp, "serial": sn})
        for st in self.members(ip, pw, sysp + "/Storage"):
            for ref in st.get("Drives") or []:
                d = self.get(ip, pw, ref.get("@odata.id", "")) if isinstance(ref, dict) else None
                if not d or _absent(d):
                    continue
                cap = d.get("CapacityBytes")
                what = " ".join(x for x in (_size(cap / 10**9) if cap else "", _clean(d.get("MediaType"))) if x)
                items.append({"part": _part(d), "component": T("Datenträger {0}").format(what or _clean(d.get("Model"))),
                              "serial": _clean(d.get("SerialNumber"))})
        self.log(T("  {0} Bauteile gelesen.").format(len(items)), "ok")
        return res


class AuditThread(QThread):
    line = pyqtSignal(str, str)
    done = pyqtSignal(object)

    def __init__(self, parent, **kw):
        super().__init__(parent)
        self.kw = kw

    def run(self):
        eng = AuditEngine(lambda text, kind="": self.line.emit(text, kind), self.isInterruptionRequested)
        try:
            res = eng.read(**self.kw)
        except Exception as e:                      # nie die Seite mitreißen
            self.line.emit(T('  FEHLER: unerwarteter Abbruch: {0!r}').format(e), "err")
            res = {"ok": False, "text": repr(e)}
        finally:
            try:
                eng.close_sessions()
            except Exception:
                pass
        self.done.emit(res)


# ---------------------------------------------------------------- PDF
def render_pdf(path, head, rows, checks, remark, staff, date):
    """Audit-Blatt als PDF (A4 quer) nach der Papiervorlage. `rows`: [Partnummer, Bezeichnung, Anzahl,
    [Seriennummern]]; Seriennummern, die nicht in eine Zelle passen, laufen in Folgezeilen weiter."""
    from PyQt6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    w.setPageOrientation(QPageLayout.Orientation.Landscape)
    w.setPageMargins(QMarginsF(0, 0, 0, 0))
    w.setResolution(300)
    w.setTitle(T("Systemaudit") + (" " + head.get("serial", "") if head.get("serial") else ""))
    w.setCreator("BMC-Steward")
    k = w.resolution() / 25.4                      # Bildpunkte je Millimeter
    p = QPainter(w)
    if not p.isActive():
        raise OSError(T("PDF-Datei lässt sich nicht schreiben: {0}").format(path))
    pen = QPen(Qt.GlobalColor.black)
    pen.setWidthF(0.25 * k)
    p.setPen(pen)
    base = QFont("Sans Serif")
    base.setPointSizeF(9)
    bold = QFont(base)
    bold.setBold(True)
    left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    center = Qt.AlignmentFlag.AlignCenter
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

    def R(x, y, ww, hh):
        return QRectF(x * k, y * k, ww * k, hh * k)

    def text(x, y, ww, hh, s, align=left, font=base, pad=1.0):
        p.setFont(font)
        r = R(x + pad, y, ww - 2 * pad, hh)
        p.drawText(r, align, p.fontMetrics().elidedText(str(s), Qt.TextElideMode.ElideRight, int(r.width())))

    def grid(x, y, widths, n, rh):
        total = sum(widths)
        for i in range(n + 1):
            p.drawLine(R(x, y + i * rh, total, 0).topLeft(), R(x, y + i * rh, total, 0).topRight())
        cx = x
        for wd in [0] + widths:
            cx += wd
            p.drawLine(R(cx, y, 0, n * rh).topLeft(), R(cx, y, 0, n * rh).bottomLeft())

    # Tabellenzeilen: Seriennummern auf die Spaltenbreite verteilen
    tx, ty, rh = 103.0, 27.0, 5.5
    widths = [40.0, 60.0, 15.0, 67.0]
    small = QFont(base)
    small.setPointSizeF(8)
    p.setFont(small)
    fm = p.fontMetrics()
    room = (widths[3] - 2) * k
    lines = []
    for part, comp, count, serials in rows:
        chunks, cur = [], ""
        for s in serials:
            cand = f"{cur} / {s}" if cur else s
            if cur and fm.horizontalAdvance(cand) > room:
                chunks.append(cur)
                cand = s
            cur = cand
        if cur:
            chunks.append(cur)
        lines.append([part, comp, str(count) if count else "", chunks[0] if chunks else ""])
        lines += [["", "", "", c] for c in chunks[1:]]
    pages = [lines[i:i + ROWS_PER_PAGE] for i in range(0, max(len(lines), 1), ROWS_PER_PAGE)] or [[]]

    for no, chunk in enumerate(pages, 1):
        if no > 1:
            w.newPage()
        p.drawRect(R(10, 12, 277, 8))
        title = QFont(bold)
        title.setPointSizeF(12)
        text(10, 12, 277, 8, T("Systemaudit"), center, title)
        if no == 1:
            hx, hy = 15.0, 27.0
            labels = (T("Hersteller:"), T("Modell/Typ:"), T("Seriennummer:"))
            values = (head.get("manufacturer", ""), head.get("model", ""), head.get("serial", ""))
            grid(hx, hy, [28, 52], 3, rh)
            for i, (lab, val) in enumerate(zip(labels, values)):
                text(hx, hy + i * rh, 28, rh, lab, left, bold)
                text(hx + 28, hy + i * rh, 52, rh, val, center)
            text(hx, 47, 80, 5, T("Checkliste"), left, bold, 0)
            grid(hx, 52, [72, 8], 6, 5.0)
            for i, lab in enumerate(check_labels()):
                text(hx, 52 + i * 5.0, 72, 5.0, lab)
                if i < len(checks) and checks[i]:
                    text(hx + 72, 52 + i * 5.0, 8, 5.0, "x", center, bold)
            p.drawRect(R(hx, 88, 80, 32))
            text(hx, 88, 80, 6, T("Bemerkung:"))
            p.setFont(base)
            p.drawText(R(hx + 1.5, 94, 77, 25), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                                                  | Qt.TextFlag.TextWordWrap), remark or "")
            p.drawRect(R(hx, 126, 80, 30))
            text(hx, 127, 80, 6, T("Bearbeitung durch Mitarbeiter:"))
            text(hx, 134, 80, 6, staff or "", left, bold, 1.5)
            p.drawLine(R(hx + 2, 147, 50, 0).topLeft(), R(hx + 2, 147, 50, 0).topRight())
            text(hx, 148, 80, 6, T("Datum und Unterschrift"))
        heads = (T("Partnummer"), T("Komponente/Bauteil"), T("Anzahl"), T("Seriennummer"))
        grid(tx, ty, widths, ROWS_PER_PAGE + 1, rh)
        cx = tx
        for wd, hd in zip(widths, heads):
            text(cx, ty, wd, rh, hd, left, bold)
            cx += wd
        for i, line in enumerate(chunk):
            cx = tx
            for j, (wd, val) in enumerate(zip(widths, line)):
                text(cx, ty + (i + 1) * rh, wd, rh, val, right if j == 2 else left, small)
                cx += wd
        text(10, 196, 40, 6, str(no))
        text(237, 196, 50, 6, date, right)
    p.end()
    return len(pages)


# ---------------------------------------------------------------- Seite
def _card(title):
    box = QGroupBox(title)
    f = QFormLayout(box)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, f


class AuditPage(QWidget):
    """Eingabe, Vorschau der Bauteile und Speichern als PDF. `sources` liefert die Server zur Auswahl:
    Liste von (Anzeige, Adresse, Benutzer, Passwort) aus „Neuware“ und „Bestandsserver“."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None
        self.sources = lambda: []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        rightw = QVBoxLayout()
        outer.addLayout(left, 1)
        outer.addLayout(rightw, 1)

        box, f = _card(T("1 · Server"))
        self.addr_box = QComboBox()
        self.addr_box.setEditable(True)
        self.addr_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.addr_box.setToolTip(T("Server aus „Neuware“ und „Bestandsserver“ wählen (trägt Benutzer und Passwort "
                                   "ein) oder eine Adresse eintippen."))
        self.addr_box.activated.connect(self.pick)
        self.addr = self.addr_box.lineEdit()
        self.addr.setPlaceholderText(T("BMC-Adresse, z. B. 10.20.0.21"))
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip(T("Server aus „Neuware“ und „Bestandsserver“ neu einlesen"))
        refresh.clicked.connect(self.refresh)
        arow = QHBoxLayout()
        arow.setContentsMargins(0, 0, 0, 0)
        arow.addWidget(self.addr_box, 1)
        arow.addWidget(refresh)
        self.user = QLineEdit()
        self.user.setPlaceholderText(T("leer = Werksbenutzer des Herstellers"))
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setToolTip(T("Wird nirgends gespeichert und steht nicht im PDF. Nach einem scharfen Lauf sind die "
                             "Passwörter gelöscht: dann hier das Passwort des neuen Administrators eintragen; es "
                             "bleibt für die nächsten Server mit demselben Benutzer stehen."))
        f.addRow(T("Adresse"), arow)
        f.addRow(T("Benutzer"), self.user)
        f.addRow(T("Passwort"), self.pw)
        h = QHBoxLayout()
        self.read_btn = QPushButton(T("Auslesen (nur lesend)"))
        self.read_btn.setToolTip(T("Liest Hersteller, Modell, Seriennummer und die Bauteile (Arbeitsspeicher, "
                                   "Prozessoren, Netzteile, Datenträger) mit Part- und Seriennummern. Ändert nichts."))
        self.read_btn.clicked.connect(self.read)
        h.addWidget(self.read_btn)
        h.addStretch(1)
        f.addRow("", h)
        left.addWidget(box)

        box, f = _card(T("2 · Kopf"))
        self.maker, self.model, self.serial = QLineEdit(), QLineEdit(), QLineEdit()
        f.addRow(T("Hersteller"), self.maker)
        f.addRow(T("Modell/Typ"), self.model)
        f.addRow(T("Seriennummer"), self.serial)
        left.addWidget(box)

        box = QGroupBox(T("3 · Checkliste"))
        v = QVBoxLayout(box)
        self.checks = [QCheckBox(t) for t in check_labels()]
        for c in self.checks:
            v.addWidget(c)
        f2 = QFormLayout()
        self.remark = QPlainTextEdit()
        self.remark.setPlaceholderText(T("Bemerkung (optional)"))
        self.remark.setFixedHeight(70)
        self.staff = QLineEdit()
        self.staff.setPlaceholderText(T("Name, optional; unterschrieben wird auf dem Ausdruck"))
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd.MM.yyyy")
        f2.addRow(T("Bemerkung"), self.remark)
        f2.addRow(T("Mitarbeiter"), self.staff)
        f2.addRow(T("Datum"), self.date)
        v.addLayout(f2)
        left.addWidget(box)
        left.addStretch(1)

        box = QGroupBox(T("4 · Bauteile"))
        v = QVBoxLayout(box)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([T("Partnummer"), T("Komponente/Bauteil"), T("Anzahl"),
                                              T("Seriennummer")])
        self.table.setToolTip(T("Alles von Hand änderbar. Mehrere Seriennummern mit „/“ trennen."))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        v.addWidget(self.table, 1)
        h = QHBoxLayout()
        add = QPushButton(T("Zeile hinzufügen"))
        add.clicked.connect(lambda: self.add_row(["", "", "1", ""]))
        rem = QPushButton(T("Zeile entfernen"))
        rem.clicked.connect(self.remove_rows)
        self.pdf_btn = QPushButton(T("Als PDF speichern …"))
        self.pdf_btn.setToolTip(T("Speichert das Audit-Blatt (A4 quer) als PDF-Datei."))
        self.pdf_btn.clicked.connect(self.ask_pdf)
        h.addWidget(add)
        h.addWidget(rem)
        h.addStretch(1)
        h.addWidget(self.pdf_btn)
        v.addLayout(h)
        rightw.addWidget(box, 3)
        self.out = QTextBrowser()
        self.out.setMaximumHeight(130)
        rightw.addWidget(self.out, 1)

    # ---------- Tabelle ----------
    def add_row(self, vals):
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, val in enumerate(vals):
            self.table.setItem(r, c, QTableWidgetItem(str(val)))

    def remove_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def rows(self):
        out = []
        for r in range(self.table.rowCount()):
            vals = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "") for c in range(4)]
            if not any(vals):
                continue
            serials = [s.strip() for s in vals[3].split("/") if s.strip()]
            count = int(vals[2]) if vals[2].isdigit() else 0
            out.append([vals[0], vals[1], count, serials])
        return out

    # ---------- Auslesen ----------
    def say(self, text, kind=""):
        import html
        color = {"err": "#d9534f", "warn": "#c9a100", "ok": "#3c9a5f", "head": ""}.get(kind, "")
        t = html.escape(text).replace("  ", "&nbsp;&nbsp;")
        self.out.append(f'<span style="color:{color}">{t}</span>' if color else f"<span>{t}</span>")

    def take(self, ip, user, pw):
        """Server eintragen. Ohne Passwort (nach einem scharfen Lauf gelöscht) bleibt ein schon eingetipptes
        stehen, wenn der Benutzer derselbe ist – sonst wird es geleert, nie an einen anderen Benutzer gegeben."""
        same = (user or "") == self.user.text().strip()
        self.addr.setText(ip)
        self.user.setText(user or "")
        if pw or not same:
            self.pw.setText(pw or "")

    def start(self, ip, user, pw):
        """Von einer anderen Seite aus: Server übernehmen und gleich auslesen."""
        self.take(ip, user, pw)
        self.read()

    def refresh(self):
        """Auswahlliste aus „Neuware“ und „Bestandsserver“ neu aufbauen; die Eingabe bleibt stehen."""
        keep = self.addr.text()
        self.addr_box.blockSignals(True)
        self.addr_box.clear()
        for label, ip, user, pw in self.sources():
            self.addr_box.addItem(label, (ip, user, pw))
        self.addr_box.setCurrentIndex(-1)
        self.addr.setText(keep)
        self.addr_box.blockSignals(False)

    def pick(self, i):
        data = self.addr_box.itemData(i)
        if data:
            self.take(*data)

    def read(self):
        ip = self.addr.text().strip()
        if not ip:
            self.say(T("Aktuelle IP eintragen."), "err")
            return
        if not self.pw.text():
            self.out.clear()
            self.say(T("Passwort fehlt – nach einem scharfen Lauf sind die Passwörter gelöscht. Passwort für {0} "
                       "eintragen, dann „Auslesen“. Ohne Passwort wird keine Anmeldung versucht.").format(
                         self.user.text().strip() or T("den Werksbenutzer")), "err")
            self.pw.setFocus()
            return
        self.out.clear()
        self.say("== " + ip, "head")
        self.read_btn.setEnabled(False)
        self.thread = AuditThread(self, ip=ip, user=self.user.text().strip(), pw=self.pw.text())
        self.thread.line.connect(self.say)
        self.thread.done.connect(self.done)
        self.thread.start()

    def done(self, res):
        self.read_btn.setEnabled(True)
        if not res.get("ok"):
            self.say(T("  FEHLER: {0}").format(res.get("text") or "?"), "err")
            return
        self.maker.setText(res["manufacturer"])
        self.model.setText(res["model"])
        self.serial.setText(res["serial"])
        self.table.setRowCount(0)
        for part, comp, count, serials in group_items(res["items"]):
            self.add_row([part, comp, str(count), " / ".join(serials)])
        self.table.resizeColumnsToContents()
        self.say(T("Fertig. Werte prüfen, Checkliste ausfüllen, dann „Als PDF speichern …“."), "ok")

    # ---------- PDF ----------
    def ask_pdf(self):
        from pathlib import Path
        name = "Systemaudit-{0}-{1}.pdf".format(self.serial.text().strip() or "Server",
                                                self.date.date().toString("yyyy-MM-dd"))
        name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        path, _ = QFileDialog.getSaveFileName(self, T("Als PDF speichern …"), str(Path.home() / name),
                                              T("PDF-Datei (*.pdf)"))
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.save_pdf(path)

    def save_pdf(self, path):
        head = {"manufacturer": self.maker.text().strip(), "model": self.model.text().strip(),
                "serial": self.serial.text().strip()}
        try:
            n = render_pdf(path, head, self.rows(), [c.isChecked() for c in self.checks],
                           self.remark.toPlainText().strip(), self.staff.text().strip(),
                           self.date.date().toString("dd.MM.yyyy"))
        except OSError as e:
            self.say(T("  FEHLER: {0}").format(e), "err")
            return False
        self.say(T("PDF gespeichert ({0} Seite(n)): {1}").format(n, path), "ok")
        return True

    def stop_thread(self):
        if self.thread is not None and self.thread.isRunning():
            self.thread.requestInterruption()
            self.thread.wait(3000)
