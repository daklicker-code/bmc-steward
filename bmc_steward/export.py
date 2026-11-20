"""BMC-Steward: Exporte für Systemaudit und Serverliste.

* Inventar (Bauteile je Server) als CSV (Semikolon, UTF-8 mit BOM, öffnet direkt in Excel/LibreOffice) oder
  als Excel-Datei (.xlsx, ohne Fremdpaket: ZIP mit SpreadsheetML, Kopfzeile fixiert, Filter).
* Audit als JSON für Asset- oder Ticket-Systeme.
* Automatische Ablage jedes Audit-PDFs im Datenordner (audits/<Seriennummer>/).
* Übergabeprotokoll der Serverliste aus „Neuware“ als PDF, Excel oder CSV – nie mit Passwörtern.
Werte aus Geräten können beliebigen Text enthalten: In CSV wird ein führendes =, +, - oder @ entschärft, damit
Tabellenprogramme ihn nicht als Formel ausführen; in .xlsx stehen Texte ohnehin nie als Formel.
"""
import csv
import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import i18n
from .i18n import T


def inventory_header():
    return [T("Seriennummer"), T("Hersteller"), T("Modell/Typ"), T("Partnummer"), T("Komponente/Bauteil"),
            T("Anzahl"), T("Seriennummern")]


def inventory_rows(sheets):
    """Eine Zeile je Bauteil-Gruppe und Server; Server ohne Bauteile bekommen eine Zeile mit dem Kopf."""
    out = []
    for head, rows in sheets:
        h = [head.get("serial", ""), head.get("manufacturer", ""), head.get("model", "")]
        if not rows:
            out.append(h + ["", "", "", ""])
        for part, comp, count, serials in rows:
            out.append(h + [part, comp, count, " / ".join(serials)])
    return out


def _safe_csv(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        for r in rows:
            w.writerow([_safe_csv(v) for v in r])


_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, sheet_name, header, rows):
    """Minimale, gültige .xlsx-Datei: ein Blatt, fette fixierte Kopfzeile, Filter, Zahlen als Zahlen."""
    sheet_name = re.sub(r"[\[\]:*?/\\]", "-", sheet_name)[:31] or "Tabelle"
    widths = [len(str(h)) for h in header]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], min(60, len(str(v))))

    def cell(ref, v, style=0):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{ref}"{" s=" + chr(34) + str(style) + chr(34) if style else ""}><v>{v}</v></c>'
        text = escape(_BAD_XML.sub("", str(v)))
        st = f' s="{style}"' if style else ""
        return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{text}</t></is></c>'

    xml_rows = []
    for n, r in enumerate([header] + list(rows), 1):
        cells = "".join(cell(f"{_col(i)}{n}", v, 1 if n == 1 else 0) for i, v in enumerate(r))
        xml_rows.append(f'<row r="{n}">{cells}</row>')
    last = f"{_col(len(header) - 1)}{len(rows) + 1}"
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w + 2}" customWidth="1"/>' for i, w in enumerate(widths))
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{ct}.sheet.main+xml"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{ct}.worksheet+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="{ct}.styles+xml"/></Types>'),
        "_rels/.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{pkg}">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{main}" xmlns:r="{rel}">'
            f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
            f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
            f"'{escape(sheet_name)}'!$A$1:${_col(len(header) - 1)}${len(rows) + 1}</definedName></definedNames>"
            '</workbook>'),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{pkg}">'
            f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{rel}/styles" Target="styles.xml"/></Relationships>'),
        "xl/styles.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{main}">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '</styleSheet>'),
        "xl/worksheets/sheet1.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{main}">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/></sheetView></sheetViews>'
            f'<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData>'
            f'<autoFilter ref="A1:{last}"/></worksheet>'),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"))


def write_table(path, sheet_name, header, rows):
    """Nach Dateiendung: .xlsx → Excel, sonst CSV."""
    if str(path).lower().endswith(".xlsx"):
        write_xlsx(path, sheet_name, header, rows)
    else:
        write_csv(path, header, rows)


def write_inventory(path, sheets):
    write_table(path, T("Inventar"), inventory_header(), inventory_rows(sheets))


def write_json(path, sheets, checks, remark, staff, date):
    """Audit maschinenlesbar: Kopf, Bauteile, Checkliste, Bemerkung, Mitarbeiter, Datum."""
    from . import core
    from .sysaudit import check_labels
    if isinstance(staff, dict):
        person = {k: v for k, v in staff.items() if v}
    else:
        person = {"name": staff} if staff else {}
    doc = {
        "werkzeug": f"BMC-Steward {core.__version__}",
        "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "datum": date,
        "mitarbeiter": person,
        "bemerkung": remark,
        "checkliste": {label: bool(checks[i]) if i < len(checks) else False for i, label in enumerate(check_labels())},
        "server": [{"hersteller": h.get("manufacturer", ""), "modell": h.get("model", ""),
                    "seriennummer": h.get("serial", ""),
                    "bauteile": [{"partnummer": p, "komponente": c, "anzahl": n, "seriennummern": list(s)}
                                 for p, c, n, s in rows]} for h, rows in sheets],
    }
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def archive_dir():
    return i18n.DATA / "audits"


def archive(pdf, serials):
    """Kopie des PDFs nach <Datenordner>/audits/<Seriennummer>/ (mehrere Server: audits/mehrere/)."""
    serials = [s for s in serials if s]
    name = serials[0] if len(serials) == 1 else ("mehrere" if serials else "ohne-seriennummer")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    dest_dir = i18n.private_dir(archive_dir() / name)
    dest = dest_dir / (time.strftime("%Y%m%d-%H%M%S") + "-" + Path(pdf).name)
    n = 1
    while dest.exists():
        n += 1
        dest = dest_dir / (time.strftime("%Y%m%d-%H%M%S") + f"-{n}-" + Path(pdf).name)
    shutil.copyfile(pdf, dest)
    return dest


# ---------------------------------------------------------------- Übergabeprotokoll
def handover_header():
    return [T("Aktuelle IP"), T("Neue IP"), T("Subnetzmaske"), T("Gateway"), T("Hostname"), T("Seriennummer"),
            T("Hersteller/Typ"), T("Status")]


def render_handover(path, rows, staff, date):
    """Übergabeprotokoll als PDF (A4 quer): Tabelle der Server, darunter Felder für Übergabe und Übernahme."""
    from PyQt6.QtCore import QMarginsF, QRectF, Qt
    from PyQt6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
    from .sysaudit import staff_lines
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    w.setPageOrientation(QPageLayout.Orientation.Landscape)
    w.setPageMargins(QMarginsF(0, 0, 0, 0))
    w.setResolution(300)
    w.setTitle(T("Übergabeprotokoll"))
    w.setCreator("BMC-Steward")
    k = w.resolution() / 25.4
    p = QPainter(w)
    if not p.isActive():
        raise OSError(T("PDF-Datei lässt sich nicht schreiben: {0}").format(path))
    pen = QPen(Qt.GlobalColor.black)
    pen.setWidthF(0.25 * k)
    p.setPen(pen)
    base = QFont("Sans Serif")
    base.setPointSizeF(8.5)
    bold = QFont(base)
    bold.setBold(True)
    title = QFont(bold)
    title.setPointSizeF(12)
    left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    def R(x, y, ww, hh):
        return QRectF(x * k, y * k, ww * k, hh * k)

    def text(x, y, ww, hh, s, font=base, align=left):
        p.setFont(font)
        r = R(x + 1, y, ww - 2, hh)
        p.drawText(r, align, p.fontMetrics().elidedText(str(s), Qt.TextElideMode.ElideRight, int(r.width())))

    widths = [32, 32, 30, 28, 44, 36, 45, 30]
    x0, y0, rh, per_page = 10.0, 27.0, 6.0, 22
    pages = [rows[i:i + per_page] for i in range(0, max(len(rows), 1), per_page)] or [[]]
    name, more = staff_lines(staff)
    for no, chunk in enumerate(pages, 1):
        if no > 1:
            w.newPage()
        p.drawRect(R(10, 12, 277, 8))
        text(10, 12, 277, 8, T("Übergabeprotokoll"), title, Qt.AlignmentFlag.AlignCenter)
        n = len(chunk) + 1
        for i in range(n + 1):
            p.drawLine(R(x0, y0 + i * rh, sum(widths), 0).topLeft(), R(x0, y0 + i * rh, sum(widths), 0).topRight())
        cx = x0
        for wd in [0] + widths:
            cx += wd
            p.drawLine(R(cx, y0, 0, n * rh).topLeft(), R(cx, y0, 0, n * rh).bottomLeft())
        cx = x0
        for wd, h in zip(widths, handover_header()):
            text(cx, y0, wd, rh, h, bold)
            cx += wd
        for r, row in enumerate(chunk, 1):
            cx = x0
            for wd, v in zip(widths, row):
                text(cx, y0 + r * rh, wd, rh, v)
                cx += wd
        if no == len(pages):
            y = 172
            for x, label, who in ((10, T("Übergeben durch:"), name), (150, T("Übernommen durch:"), "")):
                text(x, y, 120, 5, label, bold)
                text(x, y + 5, 120, 5, who)
                if who and more:
                    text(x, y + 9.5, 120, 4.5, " · ".join(more))
                p.drawLine(R(x + 1, y + 20, 80, 0).topLeft(), R(x + 1, y + 20, 80, 0).topRight())
                text(x, y + 21, 120, 5, T("Datum und Unterschrift"))
        text(10, 196, 40, 6, str(no))
        text(237, 196, 50, 6, date, base, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    p.end()
    return len(pages)


def write_handover(path, rows, staff, date):
    """Nach Dateiendung: .pdf, .xlsx oder CSV."""
    if str(path).lower().endswith(".pdf"):
        return render_handover(path, rows, staff, date)
    write_table(path, T("Übergabe"), handover_header(), rows)
    return 1
