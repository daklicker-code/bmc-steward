"""BMC-Steward: Handy-Scanner — HTML/JS-Seite, OCR/Barcode-Pipeline, HTTPS-Server und Dialog."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import Counter, deque
from pathlib import Path
from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface
from PyQt6.QtCore import (QT_VERSION_STR, QBuffer, QByteArray, QEvent, QIODevice, QLocale, QObject, QProcess, QRect, QSocketNotifier, QThread, QTimer, QUrl, Qt, pyqtSignal)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QStyledItemDelegate, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPixmap)
import html
import json
import os
import secrets
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import zipfile
from .i18n import T
from .core import WINDOWS, __version__, mask
def _win():
    from . import main
    return main.Window




# ---------- Handy-Scanner: Passwort per Handy-Kamera ----------
# Das Handy öffnet eine Seite dieses Tools (HTTPS, selbst signiertes Zertifikat, geheimer Link) und zeigt
# das Kamerabild nur an. Aufgenommene Bilder gehen nicht in die Fotos des Handys: Sie bleiben im
# Arbeitsspeicher der Seite, der PC liest den Text daraus (zbarimg/tesseract) und löscht das Bild sofort.
SCAN_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<title>BMC-Steward Scanner</title>
<style>
 html,body{height:100%}
 body{font:16px system-ui,sans-serif;margin:0;padding:8px;background:#1d2126;color:#eee;box-sizing:border-box;
  height:100vh;height:100dvh;display:flex;flex-direction:column;gap:6px;overflow:hidden}
 .seg{display:flex;gap:6px;flex:none}
 .seg label{flex:1;text-align:center;padding:7px 4px;border:1px solid #556;border-radius:8px;font-size:15px}
 .seg input{display:none}
 .seg label:has(input:checked){background:#2f7fd1;border-color:#2f7fd1;font-weight:bold}
 #area{flex:none;display:flex;align-items:center;justify-content:center;position:relative;background:#000;border-radius:8px;overflow:hidden}
 #wrap{position:relative;background:#000;overflow:hidden;touch-action:manipulation}
 #v{display:block;width:100%;height:100%;object-fit:cover}
 #box{position:absolute;border:1px solid #ffd54a;box-shadow:0 0 0 9999px rgba(0,0,0,.6);pointer-events:none;box-sizing:border-box}
 button,input[type=text]{font:inherit;padding:10px;border-radius:8px;border:1px solid #556;background:#2b3138;color:#eee;box-sizing:border-box}
 button.main{width:100%;flex:none;background:#d9534f;border-color:#d9534f;font-weight:bold}
 button.ok{background:#3fa45b;border-color:#3fa45b;font-weight:bold;white-space:nowrap}
 button.small{padding:6px 12px}
 #res{flex:none;display:flex;gap:6px;overflow-x:auto}
 #res:empty{display:none}
 #res button{white-space:nowrap;font-family:ui-monospace,monospace;padding:6px 10px}
 #msg{flex:none;min-height:1.3em;font-size:14px;line-height:1.3;max-height:2.7em;overflow:hidden}
 #ctl{flex:none;display:flex;gap:8px;align-items:center}
 #ctl:empty{display:none}
 #ctl input[type=range]{flex:1}
 #row{flex:none;display:flex;gap:6px}
 #val{flex:1;min-width:0}
 #cmp{display:none;flex:none;flex-direction:column;gap:3px;align-items:stretch}
 #prev{display:block;width:100%;max-height:20vh;object-fit:contain;border-radius:6px;border:1px solid #556;background:#000}
 #big{font-family:ui-monospace,monospace;font-size:1.7rem;font-weight:bold;letter-spacing:.04em;word-break:break-all;text-align:center;line-height:1.1;min-height:1.2em}
 .err{color:#ff8a80}.good{color:#7ddf94}
</style></head><body>
<div class="seg">
 <label><input type="radio" name="m" value="barcode"><span id="t_bc">Barcode</span></label>
 <label><input type="radio" name="m" value="ocr" checked><span id="t_ocr">Text (OCR)</span></label>
</div>
<div id="area"><div id="wrap"><video id="v" playsinline muted autoplay></video><div id="box"></div></div></div>
<div class="seg">
 <label><input type="radio" name="c" value="s"><span id="c_s">Klein</span></label>
 <label><input type="radio" name="c" value="m" checked><span id="c_m">Mittel</span></label>
 <label><input type="radio" name="c" value="f"><span id="c_f">Ganz</span></label>
</div>
<div id="ctl"><input type="range" id="zoom" style="display:none"><button class="small" id="torch" style="display:none">💡</button></div>
<button class="main" id="shot">Aufnehmen</button>
<div id="cmp"><img id="prev" alt=""><div id="big"></div></div>
<div id="res"></div>
<div id="row"><input type="text" id="val" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
 placeholder="Passwort"><button class="ok" id="send">Übernehmen</button></div>
<div id="msg"></div>
<script>
const de=(navigator.language||"de").startsWith("de");
const L=de?{shot:"● Aufnehmen",send:"Übernehmen",ph:"Passwort",busy:"Lese …",none:"Nichts erkannt. ",
 tip:"Tipp: Aufkleber antippen, Ausschnitt „Klein“ oder „Mittel“ wählen, bei Bedarf zoomen. Der gelbe Rahmen wird gelesen.",
 sent:"✓ Eingetragen in Zeile ",nosend:"Kein passendes Feld frei im Tool.",nocam:"Kamera nicht verfügbar. Bitte die Seite über HTTPS öffnen und die Kamera erlauben.",
 empty:"Erst aufnehmen oder Wert eintippen.",fail:"Fehler: ",s:"Klein",m:"Mittel",f:"Ganz"}
 :{shot:"● Capture",send:"Use",ph:"Password",busy:"Reading …",none:"Nothing recognised. ",
 tip:"Tip: tap the label, choose crop “Small” or “Medium”, zoom if needed. The yellow frame is read.",
 sent:"✓ Entered in row ",nosend:"No free field in the tool.",nocam:"Camera not available. Open the page via HTTPS and allow the camera.",
 empty:"Capture first or type a value.",fail:"Error: ",s:"Small",m:"Medium",f:"Full"};
const $=id=>document.getElementById(id), base=location.pathname.replace(/[^/]*$/,"");
$("shot").textContent=L.shot;$("send").textContent=L.send;$("val").placeholder=L.ph;
say(L.tip);$("c_s").textContent=L.s;$("c_m").textContent=L.m;$("c_f").textContent=L.f;
const SIZE={s:[.82,.5],m:[.96,.75],f:[1,1]};let cx=.5,cy=.5,track=null,prevUrl=null;
const BAND=0.30;      // Höhe des Kamerastreifens als Anteil der Breite; kurzer Streifen, oben/unten beschnitten, keine schwarzen Ränder
function say(t,c){$("msg").textContent=t;$("msg").className=c||""}
function pick(n){return document.querySelector("input[name="+n+"]:checked").value}
function fit(){return false}      // immer „cover": das Band zeigt einen breiten Streifen, nicht das ganze Hochkantbild
function layout(){
 const v=$("v");if(!v.videoWidth)return;
 const AW=$("area").clientWidth, h=Math.round(AW*BAND);
 $("v").style.objectFit="cover";
 $("area").style.height=h+"px";                 // Fläche umschließt den Streifen -> keine schwarzen Balken
 $("wrap").style.width=AW+"px";$("wrap").style.height=h+"px";box();
}
function geo(){
 const v=$("v"),W=$("wrap").clientWidth,H=$("wrap").clientHeight,s=(fit()?Math.min:Math.max)(W/v.videoWidth,H/v.videoHeight);
 return{s:s,ox:(v.videoWidth*s-W)/2,oy:(v.videoHeight*s-H)/2,W:W,H:H};
}
function box(){
 const [w,h]=SIZE[pick("c")];cx=Math.min(1-w/2,Math.max(w/2,cx));cy=Math.min(1-h/2,Math.max(h/2,cy));
 const b=$("box").style;b.left=(cx-w/2)*100+"%";b.top=(cy-h/2)*100+"%";b.width=w*100+"%";b.height=h*100+"%";
}
document.querySelectorAll("input[name=c]").forEach(e=>e.onchange=box);
document.querySelectorAll("input[name=m]").forEach(e=>e.onchange=()=>{
 document.querySelector("input[name=c][value=m]").checked=true;cx=cy=.5;layout()});
$("wrap").onclick=e=>{const r=$("wrap").getBoundingClientRect();cx=(e.clientX-r.left)/r.width;cy=(e.clientY-r.top)/r.height;box()};
addEventListener("resize",layout);if(window.ResizeObserver)new ResizeObserver(layout).observe($("area"));$("prev").onclick=()=>{$("cmp").style.display="none";$("big").textContent=""};$("v").addEventListener("loadedmetadata",layout);
async function cam(){
 try{
  const s=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"},width:{ideal:3840},height:{ideal:2160}},audio:false});
  $("v").srcObject=s;track=s.getVideoTracks()[0];
  const c=(track.getCapabilities&&track.getCapabilities())||{};
  if(c.focusMode&&c.focusMode.includes("continuous"))track.applyConstraints({advanced:[{focusMode:"continuous"}]}).catch(()=>{});
  if(c.zoom){const z=$("zoom");z.min=c.zoom.min;z.max=c.zoom.max;z.step=c.zoom.step||.1;z.style.display="";
   const st=z.step||.1,def=c.zoom.min+(c.zoom.max-c.zoom.min)*.25;z.value=Math.round(def/st)*st;  // Standard-Zoom ~25 %
   track.applyConstraints({advanced:[{zoom:+z.value}]}).catch(()=>{});
   z.oninput=()=>track.applyConstraints({advanced:[{zoom:+z.value}]}).catch(()=>{})}
  if(c.torch){let on=false;const t=$("torch");t.style.display="";t.onclick=()=>{on=!on;track.applyConstraints({advanced:[{torch:on}]}).catch(()=>{})}}
 }catch(e){say(L.nocam,"err")}
}
$("shot").onclick=async()=>{
 const v=$("v");if(!v.videoWidth){say(L.nocam,"err");return}
 const [w,h]=SIZE[pick("c")],g=geo(),
  sw=Math.round(w*g.W/g.s),sh=Math.round(h*g.H/g.s),
  sx=Math.max(0,Math.min(v.videoWidth-sw,Math.round((g.ox+(cx-w/2)*g.W)/g.s))),
  sy=Math.max(0,Math.min(v.videoHeight-sh,Math.round((g.oy+(cy-h/2)*g.H)/g.s))),
  k=Math.min(1,3200/Math.max(sw,sh)),c=document.createElement("canvas");
 c.width=Math.max(1,Math.round(sw*k));c.height=Math.max(1,Math.round(sh*k));
 c.getContext("2d").drawImage(v,sx,sy,sw,sh,0,0,c.width,c.height);
 say(L.busy);$("res").innerHTML="";
 c.toBlob(async b=>{
  if(prevUrl)URL.revokeObjectURL(prevUrl);prevUrl=URL.createObjectURL(b);$("prev").src=prevUrl;$("cmp").style.display="flex";$("big").textContent="";
  try{
   const r=await fetch(base+"decode?mode="+pick("m"),{method:"POST",body:b,headers:{"X-Client":JSON.stringify({win:[innerWidth,innerHeight],video:[v.videoWidth,v.videoHeight],wrap:[Math.round($("wrap").clientWidth),Math.round($("wrap").clientHeight)],crop:[sx,sy,sw,sh],out:[c.width,c.height],size:pick("c"),zoom:$("zoom")?$("zoom").value:null})}}),j=await r.json();
   if(j.error){say(j.error,"err");return}
   if(!j.texts.length){say(L.none+(j.info||""),"err");return}
   say("");$("big").textContent=j.texts[0];
   j.texts.forEach((t,i)=>{const e=document.createElement("button");e.textContent=t;e.onclick=()=>{$("val").value=t;$("big").textContent=t};$("res").appendChild(e);if(!i)$("val").value=t});
  }catch(e){say(L.fail+e,"err")}
 },"image/jpeg",0.95);
};
$("send").onclick=async()=>{
 const t=$("val").value.trim();if(!t){say(L.empty,"err");return}
 try{
  const r=await fetch(base+"set",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({value:t})}),j=await r.json();
  if(j.ok){$("val").value="";$("res").innerHTML="";$("cmp").style.display="none";$("big").textContent="";if(prevUrl){URL.revokeObjectURL(prevUrl);prevUrl=null}say(L.sent+j.row,"good")}else say(L.nosend,"err");
 }catch(e){say(L.fail+e,"err")}
};
cam();
</script></body></html>
"""



SCAN_MAX_IMAGE = 12 * 1024 * 1024




class ScanBridge(QObject):
    """Bringt einen Wert vom Server-Thread in den Hauptthread (die Oberfläche)."""
    value = pyqtSignal(str, object)
    connected = pyqtSignal()        # das Handy hat die Seite geladen
    started = pyqtSignal(object)    # Start fertig: (server|None, Fehlertext, ssid, wlan-passwort)




def scan_tools():
    """Welche Hilfsprogramme am PC vorhanden sind."""
    return {name: bool(shutil.which(name)) for name in ("openssl", "zbarimg", "tesseract", "qrencode")}




def plausible(text):
    """Taugt ein erkannter Text als Passwort/Seriennummer-Kandidat (genug Buchstaben/Ziffern, kein Rauschen)?"""
    t = text.strip()
    return (len(t) >= 5 and len(set(t)) >= 3        # mind. 3 verschiedene Zeichen: „EEE", „IIll" u. Ä. raus
            and sum(c.isalnum() for c in t) >= 0.7 * len(t))




def tesseract_run(exe, path, psm):
    """Ein Tesseract-Durchlauf: (Kandidaten, Fehlertext). Der Fehlertext ist leer, wenn Tesseract sauber lief."""
    try:
        r = subprocess.run([exe, path, "stdout", "--oem", "1", "--psm", psm, "--dpi", "300"], capture_output=True,
                           text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return [], T("Tesseract brauchte zu lange.")
    except (OSError, subprocess.SubprocessError) as e:
        return [], str(e)
    err = (r.stderr.strip().splitlines() or [""])[-1] if r.returncode else ""
    found = []
    for line in r.stdout.splitlines():
        line = " ".join(line.split())
        line = line.replace("—", "-").replace("–", "-").replace("‐", "-").replace("‑", "-")  # OCR-Gedankenstriche -> Bindestrich
        if plausible(line):
            found.append(line)
        found.extend(w for w in line.split(" ") if plausible(w) and w != line)
        joined = line.replace(" ", "")      # die Erkennung setzt gern ein Leerzeichen mitten ins Passwort
        if " " in line and len(joined) <= 64 and plausible(joined):
            found.append(joined)
    return found, err




def binarize(gray):
    """Streifen und Moiré (Foto vom Bildschirm) glätten, dann nach Otsu in Schwarz/Weiss wandeln."""
    w, h = gray.width(), gray.height()
    small = gray.scaled(max(1, w // 3), max(1, h // 3), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    blur = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    blur = blur.convertToFormat(QImage.Format.Format_Grayscale8)
    stride, rows = blur.bytesPerLine(), []
    ptr = blur.constBits()
    ptr.setsize(stride * h)
    raw = bytes(ptr)
    for y in range(h):
        rows.append(raw[y * stride:y * stride + w])
    data = b"".join(rows)
    hist = [0] * 256
    cnt = Counter(data)
    for v, n in cnt.items():
        hist[v] = n
    total, sum_all = len(data), sum(v * n for v, n in cnt.items())
    wb = sb = 0
    best, thr = -1.0, 128
    for t in range(256):
        wb += hist[t]
        if not wb:
            continue
        wf = total - wb
        if not wf:
            break
        sb += t * hist[t]
        mb, mf = sb / wb, (sum_all - sb) / wf
        between = wb * wf * (mb - mf) ** 2
        if between > best:
            best, thr = between, t
    table = bytes(0 if i <= thr else 255 for i in range(256))
    out = QImage(data.translate(table), w, h, w, QImage.Format.Format_Grayscale8)
    return out.copy(), thr




def locate_line(gray):
    """Findet im Foto das Band mit dunkler Schrift (Zeilen mit auffallend vielen dunklen Punkten) und schneidet es
    in voller Breite aus. None, wenn kein klares Band da ist."""
    w, h = gray.width(), gray.height()
    sw = 360
    sh = max(1, h * sw // w)
    sm = gray.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    bg = sm.scaled(max(1, sw // 12), max(1, sh // 12), Qt.AspectRatioMode.IgnoreAspectRatio,
                   Qt.TransformationMode.SmoothTransformation).scaled(
                       sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    bg = bg.convertToFormat(QImage.Format.Format_Grayscale8)

    def rows_of(im):
        ptr = im.constBits()
        ptr.setsize(im.bytesPerLine() * im.height())
        raw = bytes(ptr)
        return [raw[y * im.bytesPerLine():y * im.bytesPerLine() + sw] for y in range(im.height())]
    a, c = rows_of(sm), rows_of(bg)
    counts = [sum(1 for x in range(sw) if a[y][x] < c[y][x] - 30) for y in range(sh)]
    med = sorted(counts)[len(counts) // 2]
    thr = max(med * 1.5, 8)
    best, y = (0, 0, 0), 0
    while y < sh:
        if counts[y] > thr:
            e = y
            while e < sh and counts[e] > thr:
                e += 1
            if e - y > best[0]:
                best = (e - y, y, e)
            y = e
        else:
            y += 1
    n, y0, y1 = best
    if n < 3 or n > sh * 0.6:
        return None
    pad = max(2, int(n * 0.6))
    top, bottom = max(0, y0 - pad) * h // sh, min(sh, y1 + pad) * h // sh
    return gray.copy(0, top, w, max(1, bottom - top))




def line_variants(line):
    """Ausschnitt in mehreren Größen/Glättungen, jeweils mit weissem Rand (Tesseract mag Rand)."""
    for scale in (1.0, 1.5):
        base = line.scaled(max(1, int(line.width() * scale)), max(1, int(line.height() * scale)),
                           Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        for k in (1, 2, 3):      # k=1: scharfe Fassung (für klare Fotos), 2/3: weicher gegen Moiré vom Bildschirm
            im = base.scaled(max(1, base.width() // k), max(1, base.height() // k),
                             Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            im = im.scaled(base.width(), base.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_Grayscale8)
            out = QImage(im.width() + 80, im.height() + 80, QImage.Format.Format_Grayscale8)
            out.fill(255)
            pt = QPainter(out)
            pt.drawImage(40, 40, im)
            pt.end()
            yield f"zeile-x{scale}-w{k}", out




class ScanDebug:
    """Sammelt beim Debug-Modus, was beim Lesen passiert ist (Schritte, Kandidaten, zwischengespeicherte Bilder)."""

    def __init__(self, show_text, keep_images):
        self.show_text, self.keep_images = show_text, keep_images
        self.lines, self.images = [], []

    def add(self, text):
        self.lines.append(text)

    def cands(self, found):
        if not found:
            return "0"
        return f"{len(found)}: " + " | ".join(mask(t) for t in found[:6])

    def image(self, name, img):
        if self.keep_images:
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            self.images.append((name, bytes(ba)))




def scan_decode(jpeg, mode, dbg=None):
    """Liest Text aus einem Bild: (Liste möglicher Werte, Fehlertext, Info). Das Bild liegt nur kurz in einem
    privaten Temp-Ordner und wird gleich danach gelöscht. Texterkennung: Graustufen, kleine Ausschnitte
    werden vergrößert, dann Zeile/Wort/Block-Modi nacheinander, zuletzt invertiert (helle Schrift)."""
    exe = shutil.which("zbarimg" if mode == "barcode" else "tesseract")
    if not exe:
        return [], (T("zbarimg fehlt am PC. Unter CachyOS: sudo pacman -S zbar") if mode == "barcode"
                    else T("tesseract fehlt am PC. Unter CachyOS: sudo pacman -S tesseract tesseract-data-eng")), ""
    img = QImage.fromData(jpeg)
    if img.isNull():
        return [], T("Das Bild konnte nicht gelesen werden."), ""
    info = T("Bild {0}×{1} px.").format(img.width(), img.height())
    if dbg:
        dbg.add(f"Eingang: Modus {mode}, {len(jpeg)} Bytes, {img.width()}x{img.height()} px")
        if dbg.keep_images:
            dbg.images.append(("eingang.jpg", bytes(jpeg)))
    with tempfile.TemporaryDirectory(prefix="bmc-scan-") as d:
        if mode == "barcode":
            path = os.path.join(d, "frame.jpg")
            with open(path, "wb") as f:
                f.write(jpeg)
            try:
                r = subprocess.run([exe, "--quiet", "--raw", path], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as e:
                if dbg:
                    dbg.add(f"zbarimg Fehler: {e}")
                return [], T("Erkennung fehlgeschlagen: {0}").format(e), info
            if dbg:
                dbg.add(f"zbarimg Rückgabe {r.returncode}, stderr: {r.stderr.strip()[-200:]!r}")
            out = []
            for line in r.stdout.splitlines():
                line = " ".join(line.split())
                if line and line not in out and len(line) <= 200:
                    out.append(line)
            if out:
                return out[:12], "", info
            # zweiter Versuch: vergrößert und binarisiert; hilft bei kleinen oder unscharfen Codes
            im2 = QImage.fromData(jpeg).convertToFormat(QImage.Format.Format_Grayscale8)
            if not im2.isNull():
                longest = max(im2.width(), im2.height())
                if longest < 1800:
                    f = min(4, max(2, round(1800 / max(1, longest))))
                    im2 = im2.scaled(im2.width() * f, im2.height() * f, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                bw2, _ = binarize(im2)
                p2 = os.path.join(d, "bc2.png")
                bw2.save(p2, "PNG")
                try:
                    r2 = subprocess.run([exe, "--quiet", "--raw", p2], capture_output=True, text=True, timeout=30)
                except (OSError, subprocess.SubprocessError) as e:
                    r2 = None
                    if dbg:
                        dbg.add(f"zbarimg 2. Versuch Fehler: {e}")
                for line in (r2.stdout.splitlines() if r2 else []):
                    line = " ".join(line.split())
                    if line and line not in out and len(line) <= 200:
                        out.append(line)
                if dbg:
                    dbg.add(f"zbarimg 2. Versuch (vergrößert/SW): {len(out)} Treffer")
                if out:
                    return out[:12], "", info
            if dbg:
                dbg.add("Kein Barcode gefunden, versuche Texterkennung")
            texts, err, info2 = scan_decode(jpeg, "ocr", dbg)
            return texts, err, info
        gray0 = img.convertToFormat(QImage.Format.Format_Grayscale8)
        longest = max(gray0.width(), gray0.height())
        scale = 1 if longest >= 1800 else 2 if longest >= 900 else 3

        def grow(g):
            if scale == 1:
                return g
            return g.scaled(g.width() * scale, g.height() * scale, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)

        def soft(k):
            """Weichzeichnen über Verkleinern/Vergrößern: nimmt Streifen und Moiré (Foto vom Bildschirm) weg."""
            w, h = gray0.width(), gray0.height()
            sm = gray0.scaled(max(1, w // k), max(1, h // k), Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            return grow(sm.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation).convertToFormat(
                                      QImage.Format.Format_Grayscale8))

        gray = grow(gray0)
        votes = Counter()
        out, errs = [], []
        # 1. Textzeile im Foto suchen und gezielt lesen (hilft bei Hintergrund mit Muster)
        line = locate_line(gray0)
        if dbg:
            dbg.add("Textzeile: " + (f"gefunden, Ausschnitt {line.width()}x{line.height()}" if line else "nicht gefunden"))
        for name, im in (line_variants(line) if line else ()):
            path = os.path.join(d, name + ".png")
            im.save(path, "PNG")
            if dbg:
                dbg.image(name + ".png", im)
            for psm in ("7", "8", "13"):
                found, err = tesseract_run(exe, path, psm)
                if dbg:
                    dbg.add(f"tesseract {name} psm {psm}: Kandidaten {dbg.cands(found)}"
                            + (f", Fehler: {err}" if err else ""))
                if err and err not in errs:
                    errs.append(err)
                for t in found:
                    t = t.strip(" |_—-~“”\"'`.,;:")
                    if plausible(t) and len(t) <= 200:
                        votes[t] += 1
                        if t not in out:
                            out.append(t)
        bw, thr = binarize(gray)
        if dbg:
            dbg.add(f"Vorbereitung: Graustufen {gray.width()}x{gray.height()} (Faktor {scale}), Otsu-Schwelle {thr}")
        # Weichgezeichnete Fassungen zuerst: zeilenweise Erkennung, mehrere Varianten stimmen über das Ergebnis ab
        steps = (("weich2", soft(2), False, ("7",)), ("weich3", soft(3), False, ("7",)),
                 ("weich4", soft(4), False, ("7",)),
                 ("grau", gray, False, ("7", "8", "6")), ("sw", bw, False, ("7", "8", "6")),
                 ("grau-invers", gray, True, ("7", "6")), ("sw-invers", bw, True, ("7", "6")))
        for name, base, inverted, modes in steps:
            if (out and not name.startswith("weich")) or (len(votes) and max(votes.values()) >= 3):
                break
            im = base.copy()
            if inverted:
                im.invertPixels()
            path = os.path.join(d, name + ".png")
            im.save(path, "PNG")
            if dbg:
                dbg.image(name + ".png", im)
            for psm in modes:
                found, err = tesseract_run(exe, path, psm)
                if dbg:
                    dbg.add(f"tesseract {name} psm {psm}: Kandidaten {dbg.cands(found)}" + (f", Fehler: {err}" if err else ""))
                if err and err not in errs:
                    errs.append(err)
                for t in found:
                    if len(t) <= 200:
                        votes[t] += 1
                        if t not in out:
                            out.append(t)
                if out:
                    break
    if not out and errs:
        return [], T("Tesseract meldet: {0} (Sprachdaten fehlen? sudo pacman -S tesseract-data-eng)").format(errs[0]), info
    # Passwörter haben keine Leerzeichen und viele verschiedene Zeichen; Rauschen wie „EEE TTT“ kommt nach hinten
    out.sort(key=lambda t: (" " in t, -votes[t], -len(set(t.replace(" ", "")))))
    if dbg:
        dbg.add("Rangfolge: " + (" | ".join(f"{mask(t)} ({votes[t]}x)"
                                             for t in out[:6]) or "leer"))
    return out[:12], "", info




class ScanServer:
    """Kleiner HTTPS-Server für das Handy. Erreichbar nur über den geheimen Link, nimmt nur zwei Arten von
    Anfragen an (Bild zum Lesen, fertiger Wert) und schreibt nichts auf die Platte außer dem kurzlebigen
    Zertifikat im privaten Temp-Ordner."""

    def __init__(self, ip, bridge, port=8443):
        self.ip, self.bridge, self.port = ip, bridge, port
        self.token = secrets.token_urlsafe(6)
        self.tmp = None
        self.httpd = None
        self.limit = threading.Semaphore(2)
        self.events = deque(maxlen=400)      # Ablauf-Protokoll für den Log-Export (ohne Passwörter)
        self.debug = False                   # Zwischenbilder mitspeichern (zeigen das Passwort); Texte stehen nie im Klartext im Protokoll
        self.images = deque(maxlen=12)       # nur im Debug-Modus: (Name, Bytes) der letzten Aufnahmen

    def note(self, text):
        self.events.append(time.strftime("%H:%M:%S ") + text)

    def url(self):
        return f"https://{self.ip}:{self.port}/{self.token}/"

    def start(self):
        openssl = shutil.which("openssl")
        if not openssl:
            raise RuntimeError(T("openssl fehlt am PC (wird für das Zertifikat gebraucht)."))
        self.tmp = tempfile.mkdtemp(prefix="bmc-scan-cert-")
        crt, key = os.path.join(self.tmp, "c.pem"), os.path.join(self.tmp, "k.pem")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                        "-days", "2", "-subj", "/CN=bmc-steward", "-addext", f"subjectAltName=IP:{self.ip}"],
                       check=True, capture_output=True, timeout=60)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(crt, key)
        shutil.rmtree(self.tmp, ignore_errors=True)         # Zertifikat und Schlüssel sind geladen, Dateien weg
        self.tmp = None
        srv = self
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            timeout = 20
            server_version = "BMC-Steward"

            def log_message(self, *args):
                pass

            def reply(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(data)

            def route(self):
                """Pfad ohne Token, oder None bei falschem Token."""
                path, _, query = self.path.partition("?")
                parts = path.split("/")
                if len(parts) < 3 or not secrets.compare_digest(parts[1], token):
                    return None, ""
                return "/" + "/".join(parts[2:]), query

            def do_GET(self):
                rest, _ = self.route()
                srv.note(f"GET {'/' if rest == '/' else '?'} von {self.client_address[0]} "
                         f"({self.headers.get('User-Agent', '')[:120]})")
                if rest == "/":
                    self.reply(200, SCAN_HTML, "text/html")
                    srv.bridge.connected.emit()
                else:
                    self.reply(404, "{}")

            def do_POST(self):
                rest, query = self.route()
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    size = -1
                if rest not in ("/decode", "/set") or not 0 < size <= (SCAN_MAX_IMAGE if rest == "/decode" else 4096):
                    self.reply(404 if rest not in ("/decode", "/set") else 413, "{}")
                    return
                body = self.rfile.read(size)
                if rest == "/decode":
                    mode = "ocr" if "mode=ocr" in query else "barcode"
                    dbg = ScanDebug(False, srv.debug)
                    t0 = time.time()
                    with srv.limit:
                        texts, err, info = scan_decode(body, mode, dbg)
                    srv.note(f"POST decode Modus {mode}, {size} Bytes, Handy: {self.headers.get('X-Client', '-')[:300]}")
                    for line in dbg.lines:
                        srv.note("    " + line)
                    shown = " | ".join(mask(t) for t in texts[:6]) or "0 Treffer"
                    srv.note(f"    Ergebnis ({time.time() - t0:.1f} s): {shown}" + (f", Fehler: {err}" if err else ""))
                    if srv.debug:
                        stamp = time.strftime("%H%M%S")
                        srv.images.extend((f"{stamp}-{n}", b) for n, b in dbg.images)
                    del body
                    self.reply(200, json.dumps({"texts": texts, "error": err, "info": info}))
                    return
                try:
                    value = str(json.loads(body).get("value", "")).strip()
                except (ValueError, AttributeError):
                    value = ""
                if not value or len(value) > 200:
                    self.reply(400, json.dumps({"ok": False}))
                    return
                holder = {"row": 0, "done": threading.Event()}
                srv.note(f"POST set, {len(value)} Zeichen" + f", Form {mask(value)}")
                srv.bridge.value.emit(value, holder)
                holder["done"].wait(5)
                self.reply(200, json.dumps({"ok": bool(holder["row"]), "row": holder["row"]}))

        for port in (self.port, 0):
            try:
                self.httpd = ThreadingHTTPServer((self.ip, port), Handler)
                break
            except OSError:
                if port == 0:
                    raise
        self.port = self.httpd.server_address[1]
        self.httpd.daemon_threads = True
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None




SCAN_WAIT_SECONDS = 18      # so lange dauert der Start (Verbindungsaufbau) etwa


WAIT_STEPS = 100


HOTSPOT_CON = "BMC-Steward-Hotspot"


USB_TETHER_DRIVERS = ("rndis_host", "cdc_ether", "cdc_ncm", "cdc_eem", "ipheth")




def nm(*args, timeout=30):
    """NetworkManager-Aufruf (nur Linux). Gibt das CompletedProcess zurück."""
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)




def usb_tether_ifaces(sysfs="/sys/class/net"):
    """Netzwerkkarten, die ein per USB angeschlossenes Handy mit aktivem Tethering anlegt: Liste (Name, IPv4)."""
    out = []
    for nic in QNetworkInterface.allInterfaces():
        driver = os.path.basename(os.path.realpath(os.path.join(sysfs, nic.name(), "device", "driver")))
        if driver not in USB_TETHER_DRIVERS:
            continue
        for e in nic.addressEntries():
            if e.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol and not e.ip().isLoopback():
                out.append((nic.name(), e.ip().toString()))
    return out




def qr_pixmap(text):
    """QR-Code als Bild (mit qrencode), None wenn das Programm fehlt oder scheitert."""
    exe = shutil.which("qrencode")
    if not exe:
        return None
    try:
        png = subprocess.run([exe, "-t", "PNG", "-s", "5", "-m", "2", "-o", "-", text],
                             capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pix = QPixmap()
    return pix if pix.loadFromData(png) else None




class ScannerDialog(QDialog):
    """Fenster zum Handy-Scanner: Verbindungsweg wählen, starten, QR-Code mit dem Link zeigen."""

    WAYS = (("net", "Vorhandenes Netz (selbes WLAN/LAN)"),
            ("usb", "USB-Tethering (Handy per Kabel)"),
            ("hotspot", "Eigener WLAN-Hotspot dieses PCs"))

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.server = None
        self.hotspot = False        # True, solange der von uns angelegte Hotspot läuft
        self.bridge = ScanBridge()
        self.bridge.value.connect(self.on_value)
        self.bridge.connected.connect(self.on_phone_connected)
        self.bridge.started.connect(self.on_started)
        self.starting = False
        self.cancel_start = False
        self.wait_bar = QProgressBar()
        self.wait_bar.setRange(0, WAIT_STEPS)
        self.wait_bar.setFormat(T("Starte … %p %"))
        self.wait_bar.hide()
        self.wait_timer = QTimer(self)
        self.wait_timer.timeout.connect(self.wait_tick)
        self.setWindowTitle(T("Handy-Scanner (Passwort)"))
        self.setMinimumWidth(480)
        self.setMinimumHeight(560)
        self.way = QComboBox()
        for key, text in self.WAYS:
            self.way.addItem(T(text), key)
        self.way.setToolTip(T("Wie das Handy den PC erreicht:\n"
                              "Vorhandenes Netz: Handy und PC sind im selben WLAN/LAN.\n"
                              "USB-Tethering: Handy per Kabel anstecken und dort „USB-Tethering“ einschalten.\n"
                              "Hotspot: Der PC macht ein eigenes WLAN auf (NetworkManager); der WLAN-Adapter ist dann belegt."))
        if WINDOWS or not shutil.which("nmcli"):
            idx = self.way.findData("hotspot")
            self.way.model().item(idx).setEnabled(False)
        if WINDOWS:
            self.way.model().item(self.way.findData("usb")).setEnabled(False)
        self.iface = QComboBox()
        self.refresh = QPushButton("↻")
        self.refresh.setFixedWidth(34)
        self.refresh.setToolTip(T("Adressen neu einlesen, z. B. nachdem das Handy angesteckt wurde."))
        self.refresh.clicked.connect(self.fill_ifaces)
        self.toggle = QPushButton(T("Starten"))
        self.toggle.clicked.connect(self.toggle_server)
        self.qr_wifi = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.wifi_info = QLabel()
        self.wifi_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.qr = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.url = QLabel()
        self.url.setWordWrap(True)
        self.url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        tools = scan_tools()
        missing = [n for n, ok in tools.items() if not ok and n != "qrencode"]
        hint = QLabel(T("Hilfsprogramme am PC: Barcode braucht zbar, Texterkennung braucht tesseract.") + (
            "\n" + T("Es fehlt: {0}").format(", ".join(missing)) + "\nsudo pacman -S zbar tesseract tesseract-data-eng"
            if missing else ""))
        hint.setWordWrap(True)
        intro = QLabel(T("Das Handy zeigt nur die Kamera. Bilder werden nicht auf dem Handy gespeichert: Der PC liest "
                         "den Text und verwirft das Bild sofort. Der Wert landet im Feld „Passwort“ der markierten "
                         "Zeile (sonst der ersten ohne Passwort); danach springt die Markierung eine Zeile weiter."))
        intro.setWordWrap(True)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(T("Verbindungsweg")))
        row1.addWidget(self.way, 1)
        row2 = QHBoxLayout()
        row2.addWidget(self.iface, 1)
        row2.addWidget(self.refresh)
        row2.addWidget(self.toggle)
        self.debug = QCheckBox(T("Zwischenbilder mitspeichern (zeigen das Passwort!)"))
        self.debug.setToolTip(T("Das Protokoll enthält nie Passwörter, nur Länge und Form (A groß, a klein, 9 Ziffer, # Rest).\n"
                                "An: Zusätzlich liegen die Bilder der Erkennung im Export. Die zeigen das Passwort lesbar. "
                                "Nur zur Fehlersuche einschalten und die ZIP danach löschen."))
        self.debug.toggled.connect(self.set_debug)
        self.export_btn = QPushButton(T("Log exportieren …"))
        self.export_btn.clicked.connect(self.export_log)
        self.events, self.images = deque(maxlen=400), deque(maxlen=12)
        row3 = QHBoxLayout()
        row3.addWidget(self.debug, 1)
        row3.addWidget(self.export_btn)
        lay = QVBoxLayout(self)
        for w in (intro,):
            lay.addWidget(w)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addLayout(row3)
        lay.addWidget(self.wait_bar)
        for w in (self.qr_wifi, self.wifi_info, self.qr, self.url, self.state, hint, self.log):
            lay.addWidget(w)
        self.way.currentIndexChanged.connect(self.fill_ifaces)
        self.state.setText(T("Gestoppt"))
        self.fill_ifaces()

    def say(self, text):
        self.log.append(html.escape(text))
        self.events.append(time.strftime("%H:%M:%S ") + "[Fenster] " + text)

    def wait_start(self):
        self.wait_bar.setValue(0)
        self.wait_bar.setFormat(T("Starte … %p %"))
        self.wait_bar.show()
        self.wait_timer.start(1000 * SCAN_WAIT_SECONDS // WAIT_STEPS)

    def wait_tick(self):
        # bei 99 % warten, bis der Start wirklich fertig ist
        self.wait_bar.setValue(min(self.wait_bar.value() + 1, WAIT_STEPS - 1))

    def wait_stop(self, text=None):
        self.wait_timer.stop()
        if text:
            self.wait_bar.setValue(WAIT_STEPS)
            self.wait_bar.setFormat(text)
        else:
            self.wait_bar.hide()

    def on_phone_connected(self):
        self.say(T("Handy verbunden"))

    def set_debug(self, on):
        if self.server:
            self.server.debug = on
        self.events.append(time.strftime("%H:%M:%S ") + f"[Fenster] Zwischenbilder {'an' if on else 'aus'}")

    def export_log(self):
        """Protokoll (und im Debug-Modus die Zwischenbilder) als ZIP speichern."""
        if self.server:
            self.events.extend(e for e in self.server.events if e not in self.events)
            self.images.extend(i for i in self.server.images if i not in self.images)
        import platform
        head = [f"BMC-Steward {__version__}", f"System: {platform.platform()}",
                f"Python {platform.python_version()}, Qt {QT_VERSION_STR}",
                f"Zwischenbilder: {'an' if self.debug.isChecked() else 'aus'}",
                "Hilfsprogramme: " + ", ".join(f"{k}={'ja' if v else 'nein'}" for k, v in scan_tools().items())]
        for exe, arg in (("tesseract", "--version"), ("zbarimg", "--version")):
            path = shutil.which(exe)
            if path:
                try:
                    out = subprocess.run([path, arg], capture_output=True, text=True, timeout=10)
                    head.append(f"{exe}: " + ((out.stdout or out.stderr).splitlines() or [""])[0])
                except (OSError, subprocess.SubprocessError):
                    pass
        if self.server:
            head.append(f"Server: {self.server.ip}:{self.server.port}")
        path, _ = QFileDialog.getSaveFileName(self, T("Log exportieren"),
                                              str(Path.home() / f"bmc-steward-scanlog-{time.strftime('%Y%m%d-%H%M%S')}.zip"),
                                              T("ZIP (*.zip)"))
        if not path:
            return
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("log.txt", "\n".join(head + ["", *self.events]) + "\n")
                for name, data in self.images:
                    z.writestr("bilder/" + name, data)
        except OSError as e:
            QMessageBox.warning(self, T("Log exportieren"), str(e))
            return
        self.say(T("Log exportiert: {0}").format(path))

    def current_way(self):
        return self.way.currentData()

    def fill_ifaces(self, *_):
        """Füllt die Adressliste passend zum gewählten Weg."""
        way = self.current_way()
        self.iface.clear()
        if way == "hotspot":
            dev = "" if WINDOWS or not shutil.which("nmcli") else self.wifi_device()
            self.iface.addItem(dev or T("(kein WLAN-Adapter gefunden)"), dev)
            self.iface.setToolTip(T("WLAN-Adapter, der zum Hotspot wird."))
            return
        if way == "usb":
            found = usb_tether_ifaces()
            for name, ip in found:
                self.iface.addItem(f"{ip}  ({name})", ip)
            if not found:
                self.iface.addItem(T("(kein USB-Tethering erkannt, Handy anstecken und Tethering einschalten)"), "")
            self.iface.setToolTip(T("Adresse dieses PCs auf der USB-Verbindung zum Handy."))
            return
        own = [str(i.ip) for i in _win().local_ifaces()]
        for ip in own:
            self.iface.addItem(ip, ip)
        route = _win().default_route_ip()
        if route in own:
            self.iface.setCurrentIndex(own.index(route))
        self.iface.setToolTip(T("Eigene Adresse, unter der das Handy diesen Rechner erreicht (selbes WLAN/Netz)."))

    @staticmethod
    def wifi_device():
        try:
            for line in nm("-t", "-f", "DEVICE,TYPE", "device").stdout.splitlines():
                dev, _, kind = line.partition(":")
                if kind == "wifi":
                    return dev
        except (OSError, subprocess.SubprocessError):
            pass
        return ""

    def start_hotspot(self, dev):
        """Legt einen WLAN-Hotspot an. Gibt (ssid, passwort, ip) zurück, bei Fehler (None, Fehlertext, None)."""
        ssid = "BMC-Steward-" + secrets.token_hex(2)
        pw = secrets.token_urlsafe(9)[:12]
        try:
            nm("connection", "delete", HOTSPOT_CON)         # Rest eines früheren Laufs
            r = nm("device", "wifi", "hotspot", "ifname", dev, "con-name", HOTSPOT_CON, "ssid", ssid,
                   "password", pw, timeout=60)
            if r.returncode:
                return None, (r.stderr or r.stdout).strip(), None
            self.hotspot = True
            ip = ""
            for _ in range(10):
                ip = nm("-g", "IP4.ADDRESS", "device", "show", dev).stdout.strip().split("\n")[0].split("/")[0]
                if ip:
                    break
                time.sleep(0.5)
            if not ip:
                self.stop_hotspot()
                return None, T("Der Hotspot hat keine Adresse bekommen."), None
            return ssid, pw, ip
        except (OSError, subprocess.SubprocessError) as e:
            self.stop_hotspot()
            return None, str(e), None

    def stop_hotspot(self):
        if self.hotspot:
            try:
                nm("connection", "down", HOTSPOT_CON)
                nm("connection", "delete", HOTSPOT_CON)     # das Hotspot-Passwort soll nicht im System bleiben
            except (OSError, subprocess.SubprocessError):
                pass
            self.hotspot = False

    def toggle_server(self):
        if self.server:
            self.stop_server()
            return
        if self.starting:
            return
        way, ip = self.current_way(), self.iface.currentData() or ""
        if way == "hotspot":
            if not ip:
                QMessageBox.warning(self, T("Handy-Scanner"), T("Kein WLAN-Adapter gefunden."))
                return
            if QMessageBox.question(self, T("Hotspot starten?"),
                                    T("Der WLAN-Adapter {0} wird zum Hotspot. Eine bestehende WLAN-Verbindung dieses PCs "
                                      "wird dabei getrennt. Fortfahren?").format(ip),
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        elif not ip:
            QMessageBox.warning(self, T("Handy-Scanner"), T("Dieser Rechner hat keine passende Netzwerkadresse."))
            return
        self.starting = True
        self.toggle.setEnabled(False)
        for w in (self.way, self.iface, self.refresh):
            w.setEnabled(False)
        self.wait_start()               # Ladebalken läuft ab dem Klick bis der QR-Code da ist
        threading.Thread(target=self.start_worker, args=(way, ip), daemon=True).start()

    def start_worker(self, way, ip):
        """Hotspot und Server starten (blockiert, deshalb nicht im Fenster-Thread); Ergebnis per Signal."""
        ssid = wifi_pw = ""
        try:
            if way == "hotspot":
                ssid, wifi_pw, ip = self.start_hotspot(ip)
                if ssid is None:
                    self.bridge.started.emit((None, T("Hotspot fehlgeschlagen: {0}").format(wifi_pw), "", ""))
                    return
                if not ip:
                    self.stop_hotspot()
                    self.bridge.started.emit((None, T("Dieser Rechner hat keine passende Netzwerkadresse."), "", ""))
                    return
            server = ScanServer(ip, self.bridge)
            server.start()
            self.bridge.started.emit((server, "", ssid, wifi_pw))
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            self.stop_hotspot()
            self.bridge.started.emit((None, T("Start fehlgeschlagen: {0}").format(e), "", ""))

    def on_started(self, result):
        server, err, ssid, wifi_pw = result
        self.starting = False
        self.toggle.setEnabled(True)
        if self.cancel_start:           # Fenster wurde während des Starts geschlossen
            self.cancel_start = False
            if server is not None:
                server.stop()
            self.stop_server()
            return
        if server is None:
            self.wait_stop()
            for w in (self.way, self.iface, self.refresh):
                w.setEnabled(True)
            self.fill_ifaces()
            QMessageBox.warning(self, T("Handy-Scanner"), err)
            return
        server.debug = self.debug.isChecked()
        self.server = server
        self.toggle.setText(T("Beenden"))
        link = server.url()
        self.url.setText(f"<b>{html.escape(link)}</b>")
        if ssid:
            esc = lambda t: "".join("\\" + c if c in "\\;,:\"" else c for c in t)
            pix = qr_pixmap(f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(wifi_pw)};;")
            if pix:
                self.qr_wifi.setPixmap(pix)
                self.qr_wifi.setMinimumSize(pix.size())
            self.wifi_info.setText(T("1. Mit dem WLAN verbinden: {0}, Passwort {1}").format(ssid, wifi_pw))
        self.state.setText(T("Läuft. Link am Handy öffnen (QR-Code scannen), die Zertifikatswarnung einmal bestätigen, "
                             "Kamera erlauben. Bei aktiver Firewall den Port {0} freigeben (ufw: sudo ufw allow {0}/tcp).").format(server.port))
        pix = qr_pixmap(link)
        if pix:
            self.qr.setPixmap(pix)
            self.qr.setMinimumSize(pix.size())
        else:
            self.qr.setText(T("(Für einen QR-Code: sudo pacman -S qrencode)"))
        self.say(T("Scanner gestartet auf {0}").format(link.rsplit("/", 2)[0]))
        self.wait_stop(T("Bereit – QR-Code mit dem Handy scannen"))
        self.adjustSize()

    def stop_server(self):
        if self.starting:
            self.cancel_start = True
            return
        if self.server:
            self.events.extend(e for e in self.server.events if e not in self.events)
            self.images.extend(i for i in self.server.images if i not in self.images)
            self.server.stop()
            self.server = None
        self.stop_hotspot()
        self.toggle.setText(T("Starten"))
        for w in (self.way, self.iface, self.refresh):
            w.setEnabled(True)
        for w in (self.qr, self.qr_wifi, self.url, self.wifi_info):
            w.clear()
        self.qr.setMinimumSize(0, 0)
        self.qr_wifi.setMinimumSize(0, 0)
        self.wait_stop()
        self.adjustSize()
        self.state.setText(T("Gestoppt"))
        self.fill_ifaces()

    def on_value(self, value, holder):
        """Ein Wert vom Handy ist da: ins Passwort-Feld, ohne ihn irgendwo anzuzeigen."""
        row = self.win.scan_to_password(value)
        holder["row"] = row
        holder["done"].set()
        self.say(T("Passwort in Zeile {0} eingetragen").format(row) if row else T("Kein freies Feld, nichts eingetragen"))

    def closeEvent(self, event):
        self.stop_server()
        super().closeEvent(event)
