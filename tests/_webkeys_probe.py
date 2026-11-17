"""Hilfsprogramm für tests/test_osk.py: schickt Tasten über send_qt_key an eine echte QtWebEngine-Seite
und gibt aus, was die Seite als keydown gesehen hat (JSON auf stdout). Läuft in einem eigenen Prozess, weil
QtWebEngine vor der QApplication geladen werden muss. Wird nicht als Test gesammelt (Name beginnt mit _)."""
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(os, "geteuid") and os.geteuid() == 0:            # Chromium läuft als root nur ohne Sandbox
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --log-level=3")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

app = QApplication(["probe"])
from bmc_steward.osk import K, send_qt_key
from bmc_steward.termkeys import ALT, CTRL, SHIFT

HTML = """<html><body><input id=i autofocus><script>window.log=[];
document.addEventListener('keydown',e=>log.push([e.key,e.code,e.keyCode,e.ctrlKey,e.altKey,e.shiftKey]));
document.getElementById('i').focus();</script></body></html>"""


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.03)


view = QWebEngineView()
view.resize(400, 300)
view.setHtml(HTML)
view.show()
loaded = []
view.loadFinished.connect(loaded.append)
end = time.time() + 30
while not loaded and time.time() < end:
    pump(0.05)
pump(1.0)
view.setFocus()
view.activateWindow()
pump(0.5)
send_qt_key(view, K.Key_F2.value, 0, "")
send_qt_key(view, K.Key_Delete.value, CTRL | ALT, "")
send_qt_key(view, ord("A"), SHIFT, "A")
send_qt_key(view, K.Key_Return.value, 0, "")
pump(1.0)
out = []
view.page().runJavaScript("JSON.stringify(log)", out.append)
end = time.time() + 10
while not out and time.time() < end:
    pump(0.05)
print(json.dumps(json.loads(out[0] or "[]")))
