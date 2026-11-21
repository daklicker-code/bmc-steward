"""BMC-Steward: Seite „Systemaudit“ – Audit-Blatt eines Servers als PDF.

Liest per Redfish nur lesend (GET, ein Anmeldeversuch) Hersteller, Modell und Seriennummer sowie die
Bauteile mit Part- und Seriennummern: Arbeitsspeicher, Prozessoren, Netzteile und Datenträger. Gleiche
Bauteile (gleiche Partnummer und Bezeichnung) werden zu einer Zeile mit Anzahl gebündelt. Alles bleibt in der
Tabelle von Hand änderbar; Checkliste, Bemerkung und Bearbeiter kommen dazu. „Als PDF speichern“ zeichnet das
Blatt (A4 quer): Kopf mit Hersteller/Modell/Seriennummer, Checkliste, Bemerkung, Unterschriftsfeld und rechts
die Bauteiltabelle; reicht eine Seite nicht, geht die Tabelle auf weiteren Seiten weiter.
"""
from PyQt6.QtCore import QDate, QMarginsF, QRectF, Qt, QThread, pyqtSignal
import json

from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTextBrowser,
                             QVBoxLayout, QWidget)

from . import i18n
from .i18n import T
from .redfish import RedfishClient
from .core import set_role

STAFF_FIELDS = ("name", "email", "phone", "address", "extra")


def pdf_dir():
    """Vorgeschlagener Ordner für PDFs: Einstellung „Ordner für PDFs“, sonst der persönliche Ordner."""
    from pathlib import Path
    from . import settings
    d = settings.get("audit_dir")
    return Path(d) if d and Path(d).is_dir() else Path.home()


def staff_file():
    """Mitarbeiterliste im Datenordner (nur für den eigenen Benutzer lesbar)."""
    return i18n.DATA / "bmc-mitarbeiter.json"


def load_staff():
    """Gespeicherte Mitarbeiter: Liste von {name, email, phone, address, extra}, nach Name sortiert."""
    try:
        data = json.loads(staff_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = [{k: str(e.get(k) or "") for k in STAFF_FIELDS} for e in data if isinstance(e, dict)]
    return sorted((e for e in out if e["name"].strip()), key=lambda e: e["name"].lower())


def save_staff(entries):
    """Mitarbeiter speichern; Einträge ohne Namen fallen weg."""
    clean = [{k: str(e.get(k) or "").strip() for k in STAFF_FIELDS} for e in entries]
    clean = sorted((e for e in clean if e["name"]), key=lambda e: e["name"].lower())
    i18n.write_private(staff_file(), json.dumps(clean, ensure_ascii=False, indent=1))
    return clean

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


class AuditBatchThread(QThread):
    """Liest mehrere Server nacheinander (je Server ein Anmeldeversuch, nur lesend)."""
    line = pyqtSignal(str, str)
    done = pyqtSignal(object)           # Liste von (Anzeige, Ergebnis)

    def __init__(self, parent, servers):
        super().__init__(parent)
        self.servers = servers          # (Anzeige, Adresse, Benutzer, Passwort)

    def run(self):
        out = []
        for label, ip, user, pw in self.servers:
            if self.isInterruptionRequested():
                break
            self.line.emit("== " + label, "head")
            eng = AuditEngine(lambda text, kind="": self.line.emit(text, kind), self.isInterruptionRequested)
            try:
                res = eng.read(ip, user, pw)
            except Exception as e:                  # ein Server darf den Rest nicht mitreißen
                res = {"ok": False, "text": repr(e)}
            finally:
                try:
                    eng.close_sessions()
                except Exception:
                    pass
            if not res.get("ok"):
                self.line.emit(T("  FEHLER: {0}").format(res.get("text") or "?"), "err")
            out.append((label, res))
        self.done.emit(out)


# ---------------------------------------------------------------- PDF
def staff_lines(staff):
    """Mitarbeiter als (Name, [weitere Zeilen]); `staff` ist ein Name oder ein Eintrag aus der Mitarbeiterliste."""
    if not isinstance(staff, dict):
        return str(staff or ""), []
    more = [" ".join(str(staff.get(k) or "").split()) for k in ("email", "phone")]
    more.append(", ".join(x.strip() for x in str(staff.get("address") or "").splitlines() if x.strip()))
    more.append(" ".join(str(staff.get("extra") or "").split()))
    return str(staff.get("name") or ""), [x for x in more if x]


def render_pdf(path, head, rows, checks, remark, staff, date):
    """Audit-Blatt eines Servers als PDF; siehe render_sheets."""
    return render_sheets(path, [(head, rows)], checks, remark, staff, date)


def render_sheets(path, sheets, checks, remark, staff, date):
    """Audit-Blätter als ein PDF (A4 quer) nach der Papiervorlage, je Server ein Blatt. `sheets`: Liste von
    (Kopf, Zeilen) mit Zeilen [Partnummer, Bezeichnung, Anzahl, [Seriennummern]]; Seriennummern, die nicht in
    eine Zelle passen, laufen in Folgezeilen weiter, lange Listen auf Folgeseiten. Checkliste, Bemerkung,
    Mitarbeiter und Datum gelten für alle Blätter. Gibt die Seitenzahl zurück."""
    from PyQt6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
    from PyQt6.QtGui import QPagedPaintDevice
    serial0 = sheets[0][0].get("serial", "") if len(sheets) == 1 else ""
    if isinstance(path, QPagedPaintDevice):     # Drucker (QPrinter) statt Datei
        w = path
        if hasattr(w, "setFullPage"):
            w.setFullPage(True)                 # Koordinaten ab Blattkante wie im PDF
        if hasattr(w, "setDocName"):
            w.setDocName(T("Systemaudit") + (" " + serial0 if serial0 else ""))
    else:
        w = QPdfWriter(str(path))
        w.setResolution(300)
        w.setTitle(T("Systemaudit") + (" " + serial0 if serial0 else ""))
        w.setCreator("BMC-Steward")
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    w.setPageOrientation(QPageLayout.Orientation.Landscape)
    w.setPageMargins(QMarginsF(0, 0, 0, 0))
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
    small = QFont(base)
    small.setPointSizeF(8)
    title = QFont(bold)
    title.setPointSizeF(12)
    left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    center = Qt.AlignmentFlag.AlignCenter
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    name, more = staff_lines(staff)

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

    tx, ty, rh = 103.0, 27.0, 5.5
    widths = [40.0, 60.0, 15.0, 67.0]
    p.setFont(small)
    fm = p.fontMetrics()
    room = (widths[3] - 2) * k

    def table_lines(rows):
        """Zeilen der Bauteiltabelle; Seriennummern auf die Spaltenbreite verteilt."""
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
        return lines

    def draw_qr(content, x, y, size):
        """QR-Code (Seriennummer zum Scannen beim Einbuchen); die weiße Fläche drumherum dient als Ruhezone."""
        from PyQt6.QtGui import QColor
        from .qr import encode
        try:
            mod = encode(content)
        except ValueError:
            return
        cell = size / len(mod)
        for yy, row in enumerate(mod):
            for xx, dark in enumerate(row):
                if dark:
                    p.fillRect(R(x + xx * cell, y + yy * cell, cell, cell), QColor("black"))

    def left_block(head):
        hx, hy = 15.0, 27.0
        labels = (T("Hersteller:"), T("Modell/Typ:"), T("Seriennummer:"))
        values = (head.get("manufacturer", ""), head.get("model", ""), head.get("serial", ""))
        grid(hx, hy, [28, 34], 3, rh)
        if head.get("serial"):
            draw_qr(head["serial"], hx + 66, hy - 0.5, 15.0)
        for i, (lab, val) in enumerate(zip(labels, values)):
            text(hx, hy + i * rh, 28, rh, lab, left, bold)
            text(hx + 28, hy + i * rh, 34, rh, val, center)
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
        p.drawRect(R(hx, 126, 80, 64))
        text(hx, 127, 80, 6, T("Bearbeitung durch Mitarbeiter:"))
        text(hx, 134, 80, 6, name, left, bold, 1.5)
        for i, line in enumerate(more[:6]):
            text(hx, 139.5 + i * 4.3, 80, 4.3, line, left, small, 1.5)
        p.drawLine(R(hx + 2, 180, 50, 0).topLeft(), R(hx + 2, 180, 50, 0).topRight())
        text(hx, 181, 80, 6, T("Datum und Unterschrift"))

    no = 0
    for head, rows in sheets:
        lines = table_lines(rows)
        pages = [lines[i:i + ROWS_PER_PAGE] for i in range(0, max(len(lines), 1), ROWS_PER_PAGE)] or [[]]
        for part, chunk in enumerate(pages):
            no += 1
            if no > 1:
                w.newPage()
            p.drawRect(R(10, 12, 277, 8))
            text(10, 12, 277, 8, T("Systemaudit"), center, title)
            if part == 0:
                left_block(head)
            else:
                text(15, 27, 80, 6, T("Fortsetzung: {0}").format(
                    " · ".join(x for x in (head.get("model", ""), head.get("serial", "")) if x)), left, bold, 0)
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
    return no


# ---------------------------------------------------------------- Seite
def _card(title):
    box = QGroupBox(title)
    f = QFormLayout(box)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, f


def archive_pdf(path, serials, say):
    """Kopie in die automatische Ablage, wenn eingeschaltet (Einstellungen → Systemaudit)."""
    from . import export, settings
    if not settings.get("audit_archive"):
        return None
    try:
        dest = export.archive(path, serials)
    except OSError as e:
        say(T("  Ablage nicht möglich: {0}").format(e), "warn")
        return None
    say(T("  Abgelegt: {0}").format(dest))
    return dest


class _Exports:
    """Gemeinsame Exporte für die Seite und den Dialog „Mehrere Server“: Drucken, Excel, CSV, JSON."""
    KINDS = {"xlsx": ("Excel-Datei (*.xlsx)", "Inventar"), "csv": ("CSV-Datei (*.csv)", "Inventar"),
             "json": ("JSON-Datei (*.json)", "Systemaudit")}

    def sheets(self):
        raise NotImplementedError

    def common_values(self):
        raise NotImplementedError

    def export_button(self):
        from PyQt6.QtWidgets import QMenu
        btn = QPushButton(T("Exportieren …"))
        btn.setToolTip(T("Drucken, Inventar als Excel- oder CSV-Datei, Audit als JSON (z. B. für ein Asset- oder "
                         "Ticket-System)."))
        menu = QMenu(btn)
        menu.addAction(T("Als PDF öffnen"), self.open_pdf)
        menu.addAction(T("Drucken …"), self.print_sheets)
        menu.addSeparator()
        menu.addAction(T("Inventar als Excel-Datei …"), lambda: self.ask_export("xlsx"))
        menu.addAction(T("Inventar als CSV-Datei …"), lambda: self.ask_export("csv"))
        menu.addAction(T("Audit als JSON-Datei …"), lambda: self.ask_export("json"))
        btn.setMenu(menu)
        return btn

    def export_name(self, kind):
        sheets = self.sheets()
        what = sheets[0][0].get("serial") if len(sheets) == 1 and sheets[0][0].get("serial") else \
            T("{0}-Server").format(len(sheets))
        name = "{0}-{1}-{2}.{3}".format(self.KINDS[kind][1], what, self.common_values()[3].replace(".", "-"), kind)
        return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)

    def ask_export(self, kind):
        if not self.sheets():
            self.say(T("Noch nichts zum Exportieren – erst auslesen."), "err")
            return
        path, _ = QFileDialog.getSaveFileName(self, T("Exportieren …"), str(pdf_dir() / self.export_name(kind)),
                                              T(self.KINDS[kind][0]))
        if path:
            if not path.lower().endswith("." + kind):
                path += "." + kind
            self.export_to(kind, path)

    def export_to(self, kind, path):
        from . import export
        sheets = self.sheets()
        try:
            if kind == "json":
                export.write_json(path, sheets, *self.common_values())
            else:
                export.write_inventory(path, sheets)
        except OSError as e:
            self.say(T("  FEHLER: {0}").format(e), "err")
            return False
        self.say(T("Exportiert: {0}").format(path), "ok")
        return True

    def open_pdf(self):
        """PDF in einen privaten Zwischenordner schreiben und im Standardbetrachter öffnen (nicht abgelegt)."""
        import tempfile
        from pathlib import Path
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        if not self.sheets():
            self.say(T("Noch nichts zum Exportieren – erst auslesen."), "err")
            return False
        name = self.export_name("json")[:-5].replace(self.KINDS["json"][1], "Systemaudit", 1) + ".pdf"
        path = Path(tempfile.mkdtemp(prefix="bmc-steward-pdf-")) / name
        try:
            render_sheets(path, self.sheets(), *self.common_values())
        except OSError as e:
            self.say(T("  FEHLER: {0}").format(e), "err")
            return False
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.say(T("Kein Programm zum Anzeigen von PDFs gefunden. Datei liegt unter {0}").format(path), "warn")
            return False
        self.say(T("PDF geöffnet: {0}").format(path), "ok")
        return True

    def print_sheets(self):
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
        if not self.sheets():
            self.say(T("Noch nichts zum Drucken – erst auslesen."), "err")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle(T("Systemaudit drucken"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            n = render_sheets(printer, self.sheets(), *self.common_values())
        except OSError as e:
            self.say(T("  FEHLER: {0}").format(e), "err")
            return
        self.say(T("An den Drucker geschickt: {0} Seite(n).").format(n), "ok")


class AuditPage(_Exports, QWidget):
    """Eingabe, Vorschau der Bauteile und Speichern als PDF. `sources` liefert die Server zur Auswahl:
    Liste von (Anzeige, Adresse, Benutzer, Passwort) aus „Neuware“ und „Bestandsserver“."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None
        self.sources = lambda: []
        self.scanner = lambda: None         # Hauptfenster: Handy-Scanner für diese Seite öffnen
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
        set_role(self.read_btn, "go")
        self.read_btn.setToolTip(T("Liest Hersteller, Modell, Seriennummer und die Bauteile (Arbeitsspeicher, "
                                   "Prozessoren, Netzteile, Datenträger) mit Part- und Seriennummern. Ändert nichts."))
        self.read_btn.clicked.connect(self.read)
        h.addWidget(self.read_btn)
        self.batch_btn = QPushButton(T("Mehrere Server …"))
        self.batch_btn.setToolTip(T("Mehrere Server aus „Neuware“ und „Bestandsserver“ auf einmal auslesen und als "
                                    "ein PDF speichern (je Server ein Blatt). Checkliste, Bemerkung, Mitarbeiter und "
                                    "Datum gelten für alle."))
        self.batch_btn.clicked.connect(lambda: BatchAuditDialog(self).exec())
        h.addWidget(self.batch_btn)
        self.scan_btn = QPushButton(T("Handy-Scanner …"))
        set_role(self.scan_btn, "scan")
        self.scan_btn.setToolTip(T("Passwort, Seriennummer oder andere Felder dieser Seite mit der Handy-Kamera "
                                   "abfotografieren und eintragen. Das Handy speichert keine Bilder."))
        self.scan_btn.clicked.connect(lambda: self.scanner())
        h.addWidget(self.scan_btn)
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
        self.staff = QComboBox()
        self.staff.setEditable(True)
        self.staff.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.staff.lineEdit().setPlaceholderText(T("Name, optional; unterschrieben wird auf dem Ausdruck"))
        self.staff.setToolTip(T("Angelegten Mitarbeiter wählen (mit E-Mail, Telefon, Adresse und Zusatz aufs Blatt) "
                                "oder einen Namen eintippen."))
        staff_btn = QPushButton(T("Mitarbeiter …"))
        staff_btn.setToolTip(T("Mitarbeiter anlegen, ändern oder löschen."))
        staff_btn.clicked.connect(self.edit_staff)
        srow = QHBoxLayout()
        srow.setContentsMargins(0, 0, 0, 0)
        srow.addWidget(self.staff, 1)
        srow.addWidget(staff_btn)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd.MM.yyyy")
        f2.addRow(T("Bemerkung"), self.remark)
        f2.addRow(T("Mitarbeiter"), srow)
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
        for c, wd in enumerate((150, 190, 70)):
            self.table.setColumnWidth(c, wd)
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
        self.mail_btn = QPushButton(T("Per E-Mail …"))
        self.mail_btn.setToolTip(T("Schickt das Audit-Blatt als PDF an den Mitarbeiter oder an andere Empfänger "
                                   "(SMTP aus den Einstellungen oder das eigene Mailprogramm)."))
        self.mail_btn.clicked.connect(lambda: MailDialog(self, self.pdf_for_mail).exec())
        h.addWidget(self.export_button())
        h.addWidget(self.pdf_btn)
        h.addWidget(self.mail_btn)
        v.addLayout(h)
        rightw.addWidget(box, 3)
        self.reload_staff()
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
        """Server aus der Liste übernehmen und – voreingestellt – gleich auslesen."""
        from . import settings
        data = self.addr_box.itemData(i)
        if data:
            self.take(*data)
            if settings.get("audit_auto_read"):
                self.read()

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
        path, _ = QFileDialog.getSaveFileName(self, T("Als PDF speichern …"), str(pdf_dir() / name),
                                              T("PDF-Datei (*.pdf)"))
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.save_pdf(path)

    # ---------- Mitarbeiter ----------
    def reload_staff(self):
        keep = self.staff.currentText()
        self.staff.blockSignals(True)
        self.staff.clear()
        for e in load_staff():
            self.staff.addItem(e["name"], e)
        i = self.staff.findText(keep)
        self.staff.setCurrentIndex(i)
        if i < 0:
            self.staff.setEditText(keep)
        self.staff.blockSignals(False)

    def current_staff(self):
        """Gewählter Mitarbeiter (Eintrag mit Kontaktangaben) oder der eingetippte Name."""
        text = self.staff.currentText().strip()
        i = self.staff.findText(text)
        return self.staff.itemData(i) if i >= 0 else text

    def edit_staff(self):
        StaffDialog(self).exec()
        self.reload_staff()

    def common(self):
        """Was für jedes Blatt gilt: Checkliste, Bemerkung, Mitarbeiter, Datum."""
        return ([c.isChecked() for c in self.checks], self.remark.toPlainText().strip(), self.current_staff(),
                self.date.date().toString("dd.MM.yyyy"))

    def mail_values(self, count=1):
        """Werte für die Platzhalter in Betreff und Text der Mail."""
        st = self.current_staff()
        return {"seriennummer": self.serial.text().strip(), "modell": self.model.text().strip(),
                "hersteller": self.maker.text().strip(), "datum": self.date.date().toString("dd.MM.yyyy"),
                "mitarbeiter": st.get("name", "") if isinstance(st, dict) else st, "anzahl": count}

    def pdf_for_mail(self):
        """Audit-Blatt in einen privaten Zwischenordner schreiben: (Pfad, Platzhalterwerte)."""
        import tempfile
        from pathlib import Path
        name = "Systemaudit-{0}-{1}.pdf".format(self.serial.text().strip() or "Server",
                                                self.date.date().toString("yyyy-MM-dd"))
        name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        path = Path(tempfile.mkdtemp(prefix="bmc-steward-mail-")) / name
        render_pdf(path, {"manufacturer": self.maker.text().strip(), "model": self.model.text().strip(),
                          "serial": self.serial.text().strip()}, self.rows(), *self.common())
        archive_pdf(path, [self.serial.text().strip()], self.say)
        return path, self.mail_values()

    def head(self):
        return {"manufacturer": self.maker.text().strip(), "model": self.model.text().strip(),
                "serial": self.serial.text().strip()}

    def sheets(self):
        """Immer ein Blatt – auch leer oder von Hand ausgefüllt, ohne vorher auszulesen."""
        return [(self.head(), self.rows())]

    def common_values(self):
        return self.common()

    def save_pdf(self, path):
        head = {"manufacturer": self.maker.text().strip(), "model": self.model.text().strip(),
                "serial": self.serial.text().strip()}
        try:
            n = render_pdf(path, head, self.rows(), *self.common())
        except OSError as e:
            self.say(T("  FEHLER: {0}").format(e), "err")
            return False
        self.say(T("PDF gespeichert ({0} Seite(n)): {1}").format(n, path), "ok")
        archive_pdf(path, [head["serial"]], self.say)
        return True

    def stop_thread(self):
        if self.thread is not None and self.thread.isRunning():
            self.thread.requestInterruption()
            self.thread.wait(3000)


class StaffDialog(QDialog):
    """Mitarbeiter anlegen, ändern, löschen. Pflicht ist nur der Name."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("Mitarbeiter"))
        self.resize(640, 380)
        self.entries = load_staff()
        self.current = -1
        lay = QHBoxLayout(self)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_entry)
        lay.addWidget(self.list, 1)
        right = QVBoxLayout()
        f = QFormLayout()
        self.name, self.email, self.phone, self.extra = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.address = QPlainTextEdit()
        self.address.setFixedHeight(64)
        self.name.setPlaceholderText(T("Pflichtfeld"))
        for wdg in (self.email, self.phone, self.extra):
            wdg.setPlaceholderText(T("optional"))
        self.address.setPlaceholderText(T("optional"))
        f.addRow(T("Name"), self.name)
        f.addRow(T("E-Mail"), self.email)
        f.addRow(T("Telefon"), self.phone)
        f.addRow(T("Adresse"), self.address)
        f.addRow(T("Zusatz"), self.extra)
        right.addLayout(f)
        info = QLabel(T("Gespeichert im Datenordner, nur für den eigenen Benutzer lesbar. Auf dem Audit-Blatt stehen "
                        "Name und die ausgefüllten Angaben."))
        info.setWordWrap(True)
        right.addWidget(info)
        right.addStretch(1)
        h = QHBoxLayout()
        for text, slot in ((T("Neu"), self.new_entry), (T("Speichern"), self.save_entry),
                           (T("Löschen"), self.delete_entry), (T("Schließen"), self.accept)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)
        right.addLayout(h)
        lay.addLayout(right, 2)
        self.fill_list()
        self.new_entry()

    def fill_list(self, select=-1):
        self.list.blockSignals(True)
        self.list.clear()
        for e in self.entries:
            self.list.addItem(e["name"])
        self.list.blockSignals(False)
        if select >= 0:
            self.list.setCurrentRow(select)

    def show_entry(self, row):
        self.current = row
        e = self.entries[row] if 0 <= row < len(self.entries) else {k: "" for k in STAFF_FIELDS}
        self.name.setText(e["name"])
        self.email.setText(e["email"])
        self.phone.setText(e["phone"])
        self.address.setPlainText(e["address"])
        self.extra.setText(e["extra"])

    def new_entry(self):
        self.list.setCurrentRow(-1)
        self.show_entry(-1)
        self.name.setFocus()

    def save_entry(self):
        e = {"name": self.name.text().strip(), "email": self.email.text().strip(), "phone": self.phone.text().strip(),
             "address": self.address.toPlainText().strip(), "extra": self.extra.text().strip()}
        if not e["name"]:
            QMessageBox.warning(self, T("Mitarbeiter"), T("Bitte einen Namen eintragen."))
            return
        if 0 <= self.current < len(self.entries):
            self.entries[self.current] = e
        else:
            self.entries.append(e)
        try:
            self.entries = save_staff(self.entries)
        except OSError as err:
            QMessageBox.warning(self, T("Mitarbeiter"), T("Nicht gespeichert: {0}").format(err))
            return
        self.fill_list(next(i for i, x in enumerate(self.entries) if x == e))

    def delete_entry(self):
        if not 0 <= self.current < len(self.entries):
            return
        del self.entries[self.current]
        self.entries = save_staff(self.entries)
        self.fill_list()
        self.new_entry()


class BatchAuditDialog(_Exports, QDialog):
    """Mehrere Server auswählen, nacheinander auslesen und als ein PDF speichern (je Server ein Blatt)."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.thread = None
        self.target = None              # fester Speicherort (Tests); sonst wird nachgefragt
        self.setWindowTitle(T("Systemaudit für mehrere Server"))
        self.resize(620, 480)
        lay = QVBoxLayout(self)
        info = QLabel(T("Server wählen. Checkliste, Bemerkung, Mitarbeiter und Datum der Seite gelten für "
                        "alle Blätter. Ohne Passwort (z. B. nach einem scharfen Lauf) gilt das Passwort der "
                        "Seite, wenn der Benutzer derselbe ist; sonst wird der Server übersprungen."))
        info.setWordWrap(True)
        lay.addWidget(info)
        self.list = QListWidget()
        for src in page.sources():
            it = QListWidgetItem(src[0])
            it.setData(Qt.ItemDataRole.UserRole, src)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        lay.addWidget(self.list, 2)
        h = QHBoxLayout()
        for text, state in ((T("Alle"), Qt.CheckState.Checked), (T("Keine"), Qt.CheckState.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, st=state: [self.list.item(i).setCheckState(st)
                                                         for i in range(self.list.count())])
            h.addWidget(b)
        h.addStretch(1)
        self.go = QPushButton(T("Auslesen und als PDF speichern …"))
        set_role(self.go, "go")
        self.go.clicked.connect(self.start)
        h.addWidget(self.go)
        self.last_pdf = None
        self.mail_btn = QPushButton(T("Per E-Mail …"))
        self.mail_btn.setEnabled(False)
        self.mail_btn.setToolTip(T("Schickt das zuletzt gespeicherte PDF per E-Mail."))
        self.mail_btn.clicked.connect(lambda: MailDialog(self, lambda: self.last_pdf).exec())
        h.addWidget(self.mail_btn)
        self.sheet_list = []
        self.export_btn = self.export_button()
        self.export_btn.setEnabled(False)
        h.addWidget(self.export_btn)
        lay.addLayout(h)
        self.out = QTextBrowser()
        lay.addWidget(self.out, 1)

    def say(self, text, kind=""):
        import html
        color = {"err": "#d9534f", "warn": "#c9a100", "ok": "#3c9a5f"}.get(kind, "")
        t = html.escape(text).replace("  ", "&nbsp;&nbsp;")
        self.out.append(f'<span style="color:{color}">{t}</span>' if color else f"<span>{t}</span>")

    def chosen(self):
        """Angehakte Server mit Passwort; ohne Passwort das der Seite bei gleichem Benutzer, sonst übersprungen."""
        out, page_user, page_pw = [], self.page.user.text().strip(), self.page.pw.text()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            label, ip, user, pw = it.data(Qt.ItemDataRole.UserRole)
            if not pw and (user or "") == page_user:
                pw = page_pw
            if not pw:
                self.say(T("{0}: übersprungen, Passwort fehlt (auf der Seite eintragen).").format(label), "warn")
                continue
            out.append((label, ip, user, pw))
        return out

    def start(self):
        self.out.clear()
        servers = self.chosen()
        if not servers:
            self.say(T("Kein Server mit Passwort gewählt."), "err")
            return
        self.go.setEnabled(False)
        self.thread = AuditBatchThread(self, servers)
        self.thread.line.connect(self.say)
        self.thread.done.connect(self.finished_reading)
        self.thread.start()

    def finished_reading(self, results):
        self.go.setEnabled(True)
        good = [(label, r) for label, r in results if r.get("ok")]
        bad = len(results) - len(good)
        if not good:
            self.say(T("Kein Server ließ sich auslesen; kein PDF erstellt."), "err")
            return
        self.sheet_list = [({"manufacturer": r["manufacturer"], "model": r["model"], "serial": r["serial"]},
                            group_items(r["items"])) for _, r in good]
        self.export_btn.setEnabled(True)            # auch ohne gespeichertes PDF: drucken, Excel, CSV, JSON
        path = self.target
        if not path:
            from pathlib import Path
            name = "Systemaudit-{0}-Server-{1}.pdf".format(len(good), self.page.date.date().toString("yyyy-MM-dd"))
            path, _ = QFileDialog.getSaveFileName(self, T("Als PDF speichern …"), str(pdf_dir() / name),
                                                  T("PDF-Datei (*.pdf)"))
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
        sheets = self.sheet_list
        try:
            n = render_sheets(path, sheets, *self.page.common())
        except OSError as e:
            self.say(T("  FEHLER: {0}").format(e), "err")
            return
        self.say(T("PDF gespeichert: {0} Server, {1} Seite(n): {2}").format(len(good), n, path), "ok")
        archive_pdf(path, [h["serial"] for h, _ in sheets], self.say)
        values = self.page.mail_values(len(good))
        if len(good) > 1:
            values["seriennummer"] = T("{0} Server").format(len(good))
            values["modell"] = ", ".join(r["model"] for _, r in good if r.get("model"))
        else:
            values.update(seriennummer=good[0][1]["serial"], modell=good[0][1]["model"],
                          hersteller=good[0][1]["manufacturer"])
        self.last_pdf = (path, values)
        self.mail_btn.setEnabled(True)
        if bad:
            self.say(T("{0} Server nicht ausgelesen (siehe oben), nicht im PDF.").format(bad), "warn")

    def sheets(self):
        return self.sheet_list

    def common_values(self):
        return self.page.common()

    def done(self, code):
        if self.thread is not None and self.thread.isRunning():
            self.thread.requestInterruption()
            self.thread.wait(5000)
        super().done(code)


class MailThread(QThread):
    done = pyqtSignal(str)              # "" = verschickt, sonst die Fehlermeldung

    def __init__(self, parent, job):
        super().__init__(parent)
        self.job = job

    def run(self):
        self.done.emit(self.job())


class MailDialog(QDialog):
    """Audit-PDF per E-Mail: an den Mitarbeiter (vorgeschlagen) oder an beliebige Empfänger."""

    def __init__(self, parent, maker, staff=None, texts=None, title=None):
        """`maker` liefert (PDF-Pfad, Platzhalterwerte) oder None; `staff` (vorgeschlagener Empfänger) und
        `texts` (Betreff, Text) sind ohne Angabe die des Systemaudits."""
        from . import settings, vault
        super().__init__(parent)
        self.maker = maker
        self.thread = None
        self.cfg = settings.load()
        self.setWindowTitle(title or T("Audit per E-Mail"))
        self.resize(560, 460)
        made = maker()
        self.pdf, values = made if made else (None, {})
        lay = QVBoxLayout(self)
        f = QFormLayout()
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.to = QComboBox()
        self.to.setEditable(True)
        for e in load_staff():
            if e["email"]:
                self.to.addItem(e["email"])
        if staff is not None:
            cur = staff
        else:
            cur = parent.page.current_staff() if hasattr(parent, "page") else parent.current_staff()
        mine = cur.get("email", "") if isinstance(cur, dict) else ""
        self.to.setEditText(mine)
        self.to.setToolTip(T("Mehrere Empfänger mit Komma oder Semikolon trennen."))
        self.cc = QLineEdit()
        self.cc.setPlaceholderText(T("optional"))
        subj, body = texts or settings.mail_texts(self.cfg)
        self.subject = QLineEdit(mailer_fill(subj, values))
        self.body = QPlainTextEdit(mailer_fill(body, values))
        f.addRow(T("An"), self.to)
        f.addRow(T("Kopie"), self.cc)
        f.addRow(T("Betreff"), self.subject)
        f.addRow(T("Text"), self.body)
        self.attach = QLabel(str(self.pdf.name) if self.pdf else T("kein PDF"))
        f.addRow(T("Anhang"), self.attach)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox(T("Passwort im System-Schlüsselbund merken"))
        self.remember.setEnabled(vault.available())
        self.vault_key = f"smtp-{self.cfg['smtp_user']}@{self.cfg['smtp_host']}"
        if self.cfg["smtp_user"]:
            saved = vault.fetch(self.vault_key, "")
            self.pw.setText(saved)
            self.remember.setChecked(bool(saved))
            f.addRow(T("SMTP-Passwort"), self.pw)
            f.addRow("", self.remember)
        lay.addLayout(f)
        self.status = QLabel(T("Versand über {0}").format(self.cfg["smtp_host"]) if self.cfg["smtp_host"]
                             else T("Kein SMTP-Server eingestellt (Einstellungen → E-Mail). Das eigene "
                                    "Mailprogramm geht trotzdem."))
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        h = QHBoxLayout()
        self.send_btn = QPushButton(T("Senden"))
        set_role(self.send_btn, "go")
        self.send_btn.setEnabled(bool(self.cfg["smtp_host"] and self.pdf))
        self.send_btn.clicked.connect(lambda: self.send())
        client = QPushButton(T("Im Mailprogramm öffnen"))
        client.setToolTip(T("Öffnet das eigene Mailprogramm mit Empfänger, Betreff, Text und dem PDF als Anhang."))
        client.setEnabled(bool(self.pdf))
        client.clicked.connect(self.open_client)
        close = QPushButton(T("Schließen"))
        close.clicked.connect(self.reject)
        h.addWidget(client)
        h.addStretch(1)
        h.addWidget(self.send_btn)
        h.addWidget(close)
        lay.addLayout(h)

    def recipients(self):
        from . import mailer
        return mailer.addresses(self.to.currentText()), mailer.addresses(self.cc.text())

    def send(self, wait=False):
        """SMTP-Versand im Hintergrund; `wait` (Tests) wartet und gibt True/False zurück."""
        from . import mailer, vault
        try:
            to, cc = self.recipients()
        except ValueError as e:
            self.status.setText(T("Ungültige Adresse: {0}").format(e))
            return False
        if not to:
            self.status.setText(T("Bitte mindestens einen Empfänger eintragen."))
            return False
        if self.cfg["smtp_user"] and not self.pw.text():
            self.status.setText(T("Bitte das SMTP-Passwort eintragen."))
            self.pw.setFocus()
            return False
        if self.cfg["smtp_user"] and self.remember.isEnabled():
            vault.store(self.vault_key, "", self.pw.text() if self.remember.isChecked() else "")
        cfg, pw, subject, body, pdf = dict(self.cfg), self.pw.text(), self.subject.text(), \
            self.body.toPlainText(), self.pdf

        def job():
            try:
                mailer.send(cfg, pw, to, subject, body, [pdf], cc=cc)
                return ""
            except Exception as e:              # Netz, Anmeldung, Zertifikat: verständlich melden
                return str(e) or type(e).__name__
        self.send_btn.setEnabled(False)
        self.status.setText(T("Wird gesendet …"))
        self.thread = MailThread(self, job)
        self.thread.done.connect(self.sent)
        self.thread.start()
        if wait:
            self.thread.wait(30000)
            from PyQt6.QtWidgets import QApplication
            for _ in range(5):
                QApplication.processEvents()
            return self.status.property("ok") is True
        return True

    def sent(self, err):
        from .applog import LOG
        self.send_btn.setEnabled(True)
        self.status.setProperty("ok", not err)
        if err:
            self.status.setText(T("Nicht gesendet: {0}").format(err))
            LOG.info(T("Audit-Mail nicht gesendet: {0}").format(err))
        else:
            self.status.setText(T("Gesendet an {0}.").format(self.to.currentText()))
            LOG.info(T("Audit-Mail gesendet an {0} ({1})").format(self.to.currentText(), self.pdf.name))

    def open_client(self):
        import subprocess
        from . import mailer
        try:
            to, _ = self.recipients()
        except ValueError as e:
            self.status.setText(T("Ungültige Adresse: {0}").format(e))
            return
        cmd = mailer.client_command(to, self.subject.text(), self.body.toPlainText(), self.pdf)
        if cmd:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.status.setText(T("Mailprogramm geöffnet."))
                return
            except OSError:
                pass
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.pdf.parent)))
        self.status.setText(T("Kein Mailprogramm mit Anhang gefunden. Der Ordner mit dem PDF ist geöffnet: "
                              "{0}").format(self.pdf))

    def reject(self):
        if self.thread is not None and self.thread.isRunning():
            self.thread.wait(30000)
        super().reject()


def mailer_fill(template, values):
    from . import mailer
    return mailer.fill(template, values)
