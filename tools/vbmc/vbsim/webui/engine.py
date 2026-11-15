"""Weboberflächen der virtuellen BMCs: Login, Verwaltungsoberfläche (SPA), Remote-Konsole.

Jeder Hersteller hat einen eigenen "Skin": Layout (Menü links oder oben), Farben, Menüstruktur,
Begriffe, Power-Aktionen, Anmelde- und Konsolenpfade. Die Seiten lesen live aus Redfish, es gibt keine
Parallelwelt. Alle Seiten tragen einen deutlichen Hinweis, dass es eine Simulation ist; es werden keine
Hersteller-Logos nachgebaut."""
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .. import console as vcon

K = {}                                  # von vbmc.py gesetzt: SYS/MGR (siehe configure)


def configure(**kw):
    K.update(kw)


APP_JS = (Path(__file__).with_name("app.js")).read_text(encoding="utf-8")

# ---------------------------------------------------------------- Texte
EN = {
    "on": "On", "off": "Off", "poweringOn": "Powering on", "poweringOff": "Powering off",
    "logout": "Log out", "cancel": "Cancel", "ok": "OK", "done": "Done.",
    "simNote": "Simulated server (BMC-Steward test lab) – no real hardware.",
    "systemInfo": "System information", "model": "Model", "serial": "Serial number", "bios": "BIOS version",
    "cpu": "Processors", "memory": "Memory", "power": "Power", "health": "Health", "toggleUid": "Toggle",
    "bmcInfo": "Management controller", "bmcName": "Name", "firmware": "Firmware", "ip": "IPv4 address",
    "mac": "MAC address", "hostname": "Hostname", "recentEvents": "Latest events", "severity": "Severity",
    "time": "Time", "desc": "Description", "allEvents": "Show all events", "quickLaunch": "Quick launch",
    "launchConsole": "Launch remote console", "powerCtl": "Power control", "processors": "Processors",
    "socket": "Socket", "cores": "Cores/Threads", "status": "Status", "slot": "Slot", "size": "Size",
    "type": "Type", "vendor": "Vendor", "storage": "Storage", "capacity": "Capacity", "nics": "Network adapters",
    "name": "Name", "speed": "Speed", "link": "Link", "serverPower": "Server power", "state": "State",
    "bootStage": "Boot stage", "powerUse": "Power consumption", "present": "Present", "average": "Average",
    "peak": "Peak", "psu": "Power supplies", "output": "Output", "input": "Input", "confirmPower": "Really execute \"%s\"?",
    "temps": "Temperatures", "sensor": "Sensor", "reading": "Reading", "warnAt": "Caution", "critAt": "Critical",
    "fans": "Fans", "eventLog": "Event log", "source": "Source", "clearLog": "Clear log",
    "confirmClear": "Clear the event log?", "remoteConsole": "Remote console",
    "consoleHelp": "Opens the HTML5 console in a new window. Click the preview to start.",
    "networkSettings": "Network settings", "mask": "Subnet mask", "gateway": "Gateway", "enabled": "Enabled",
    "disabled": "Disabled", "netHint": "Change the address with BMC-Steward or the Redfish API.",
    "users": "Local users", "id": "ID", "username": "User name", "role": "Role", "firmwareInv": "Firmware inventory",
    "version": "Version", "updateable": "Updateable", "yes": "Yes", "no": "No", "biosAttrs": "BIOS attributes",
    "attribute": "Attribute", "value": "Value", "biosHint": "Read-only view. Change values with BMC-Steward or in the console setup.",
    "user": "User name", "password": "Password", "wrong": "Login failed. Check user name and password.",
    "expired": "Your session has expired. Please log in again.", "consoleTitle": "Remote console",
    "ctrlAltDel": "Ctrl+Alt+Del", "fullscreen": "Fullscreen", "noSignal": "No video signal",
}
DE = dict(EN, **{
    "on": "Ein", "off": "Aus", "poweringOn": "Wird eingeschaltet", "poweringOff": "Wird heruntergefahren",
    "logout": "Abmelden", "cancel": "Abbrechen", "done": "Erledigt.",
    "simNote": "Simulierter Server (BMC-Steward-Testlabor) – keine echte Hardware.",
    "systemInfo": "Systeminformationen", "model": "Modell", "serial": "Seriennummer", "bios": "BIOS-Version",
    "cpu": "Prozessoren", "memory": "Arbeitsspeicher", "power": "Strom", "health": "Zustand", "toggleUid": "Umschalten",
    "bmcInfo": "Verwaltungscontroller", "bmcName": "Name", "firmware": "Firmware", "ip": "IPv4-Adresse",
    "mac": "MAC-Adresse", "hostname": "Hostname", "recentEvents": "Letzte Ereignisse", "severity": "Schweregrad",
    "time": "Zeit", "desc": "Beschreibung", "allEvents": "Alle Ereignisse anzeigen", "quickLaunch": "Schnellstart",
    "launchConsole": "Remote-Konsole starten", "powerCtl": "Stromsteuerung", "processors": "Prozessoren",
    "socket": "Sockel", "cores": "Kerne/Threads", "status": "Status", "slot": "Steckplatz", "size": "Größe",
    "type": "Typ", "vendor": "Hersteller", "storage": "Speicher", "capacity": "Kapazität", "nics": "Netzwerkadapter",
    "name": "Name", "speed": "Geschwindigkeit", "link": "Verbindung", "serverPower": "Server-Strom", "state": "Zustand",
    "bootStage": "Startphase", "powerUse": "Leistungsaufnahme", "present": "Aktuell", "average": "Durchschnitt",
    "peak": "Spitze", "psu": "Netzteile", "output": "Ausgang", "input": "Eingang",
    "confirmPower": "„%s“ wirklich ausführen?", "temps": "Temperaturen", "sensor": "Sensor", "reading": "Messwert",
    "warnAt": "Warnung", "critAt": "Kritisch", "fans": "Lüfter", "eventLog": "Ereignisprotokoll",
    "source": "Quelle", "clearLog": "Protokoll löschen", "confirmClear": "Ereignisprotokoll löschen?",
    "remoteConsole": "Remote-Konsole",
    "consoleHelp": "Öffnet die HTML5-Konsole in einem neuen Fenster. Zum Starten die Vorschau anklicken.",
    "networkSettings": "Netzwerkeinstellungen", "mask": "Subnetzmaske", "gateway": "Gateway", "enabled": "Aktiviert",
    "disabled": "Deaktiviert", "netHint": "Die Adresse ändert man mit BMC-Steward oder über die Redfish-API.",
    "users": "Lokale Benutzer", "id": "ID", "username": "Benutzername", "role": "Rolle",
    "firmwareInv": "Firmware-Inventar", "version": "Version", "updateable": "Aktualisierbar", "yes": "Ja", "no": "Nein",
    "biosAttrs": "BIOS-Attribute", "attribute": "Attribut", "value": "Wert",
    "biosHint": "Nur Ansicht. Werte ändert man mit BMC-Steward oder im Setup der Konsole.",
    "user": "Benutzername", "password": "Passwort", "wrong": "Anmeldung fehlgeschlagen. Benutzername oder Passwort prüfen.",
    "expired": "Die Sitzung ist abgelaufen. Bitte neu anmelden.", "consoleTitle": "Remote-Konsole",
    "fullscreen": "Vollbild", "noSignal": "Kein Videosignal",
})

EN.update({
    "vmedia": "Virtual media", "device": "Device", "mediaTypes": "Media types", "image": "Image",
    "inserted": "Inserted", "notInserted": "Not inserted", "eject": "Eject", "insertMedia": "Insert media",
    "imageUrl": "Image URL", "writeProtect": "Write protected", "insert": "Insert",
    "bootNext": "One-time boot", "bootTarget": "Boot target", "bootEnabled": "Mode", "apply": "Apply",
    "license": "License", "licenseName": "Edition", "licenseType": "Type", "licenseKey": "License key",
    "installDate": "Installed", "installKey": "Install license key", "install": "Install",
    "removeKey": "Remove license", "confirmRemoveKey": "Remove the installed license key?",
    "licenseHint": "Format: five groups of five letters/digits. Without a license, virtual media and the remote console are not available.",
    "needLicenseVm": "Virtual media requires a license. Install a license key on the License page.",
    "needLicenseConsole": "The remote console requires a license.",
    "needLicenseTitle": "License required",
    "needLicensePage": "The installed edition (%s) does not include the remote console. Install a license key to use it.",
})
DE.update({
    "vmedia": "Virtuelle Medien", "device": "Gerät", "mediaTypes": "Medientypen", "image": "Image",
    "inserted": "Eingelegt", "notInserted": "Nicht eingelegt", "eject": "Auswerfen", "insertMedia": "Medium einlegen",
    "imageUrl": "Image-URL", "writeProtect": "Schreibgeschützt", "insert": "Einlegen",
    "bootNext": "Einmaliger Start", "bootTarget": "Startziel", "bootEnabled": "Modus", "apply": "Übernehmen",
    "license": "Lizenz", "licenseName": "Edition", "licenseType": "Typ", "licenseKey": "Lizenzschlüssel",
    "installDate": "Installiert", "installKey": "Lizenzschlüssel installieren", "install": "Installieren",
    "removeKey": "Lizenz entfernen", "confirmRemoveKey": "Den installierten Lizenzschlüssel entfernen?",
    "licenseHint": "Format: fünf Gruppen aus je fünf Buchstaben/Ziffern. Ohne Lizenz sind Virtual Media und die Remote-Konsole nicht verfügbar.",
    "needLicenseVm": "Virtuelle Medien brauchen eine Lizenz. Einen Lizenzschlüssel auf der Lizenzseite installieren.",
    "needLicenseConsole": "Die Remote-Konsole braucht eine Lizenz.",
    "needLicenseTitle": "Lizenz erforderlich",
    "needLicensePage": "Die installierte Edition (%s) enthält die Remote-Konsole nicht. Zum Nutzen einen Lizenzschlüssel installieren.",
})

# Power-Aktionen: (Beschriftung, Redfish-ResetType, gefährlich)
ACT_HPE = [("Momentary Press", "PushPowerButton", 0), ("Cold Boot", "PowerCycle", 1), ("Reset", "ForceRestart", 1),
           ("Graceful Shutdown", "GracefulShutdown", 0), ("Press and Hold", "ForceOff", 1), ("Power On", "On", 0)]
ACT_DELL = [("Power On System", "On", 0), ("Power Off System", "ForceOff", 1), ("Graceful Shutdown", "GracefulShutdown", 0),
            ("NMI (Non-Masking Interrupt)", "Nmi", 1), ("Reset System (warm boot)", "ForceRestart", 1),
            ("Power Cycle System (cold boot)", "PowerCycle", 1)]
ACT_XCC = [("Power On", "On", 0), ("Shut Down OS and Power Off", "GracefulShutdown", 0),
           ("Power Off Immediately", "ForceOff", 1), ("Restart OS", "GracefulRestart", 0),
           ("Restart Immediately", "ForceRestart", 1), ("NMI", "Nmi", 1)]
ACT_SMC = [("Power On", "On", 0), ("Power Off – Immediate", "ForceOff", 1), ("Power Off – Orderly Shutdown", "GracefulShutdown", 0),
           ("Power Cycle", "PowerCycle", 1), ("Reset", "ForceRestart", 1)]


def N(k, label):
    return {"k": k, "label": label}


SKINS = {
    "hpe": dict(
        layout="left", product="Integrated Lights-Out", accent="#01a982", head_bg="#ffffff", head_fg="#1a1a1a",
        nav_bg="#f4f5f6", nav_fg="#333", nav_on_bg="#e1f5ef", body_bg="#f7f7f7", font="'Segoe UI',Arial,sans-serif",
        home="/index.html", login_btn="Log In", console_path="/irc.html", nic="1",
        nav=[{"section": "Information"}, N("overview", "Overview"), N("sysinfo", "System Information"),
             N("events", "iLO Event Log"), {"section": "Remote Console & Media"}, N("console", "Remote Console"),
             N("vmedia", "Virtual Media"),
             {"section": "Power & Thermal"}, N("power", "Power"), N("thermal", "Temperatures & Fans"),
             {"section": "iLO"}, N("network", "Network"), {"section": "Administration"},
             N("firmware", "Firmware & OS Software"), N("license", "Licensing"), N("users", "User Administration"), N("bios", "BIOS/Platform Configuration")],
        actions=ACT_HPE, t={}),
    "dell": dict(
        layout="top", product="Integrated Dell Remote Access Controller", accent="#0076ce", head_bg="#1b1b1b", head_fg="#ffffff",
        nav_bg="#2d2d2d", nav_fg="#e0e0e0", nav_on_bg="#0076ce", body_bg="#f2f2f2", font="'Roboto','Segoe UI',Arial,sans-serif",
        home="/restgui/start.html", login_btn="Log In", console_path="/console", nic="1",
        nav=[N("overview", "Dashboard"), N("sysinfo", "System"), N("power", "Power"), N("thermal", "Cooling"),
             N("events", "Logs"), N("bios", "Configuration"), N("firmware", "Maintenance"),
             N("network", "iDRAC Settings"), N("license", "Licenses"), N("users", "Users"), N("console", "Virtual Console"),
             N("vmedia", "Virtual Media")],
        actions=ACT_DELL, t={"on": "ON", "off": "OFF"}),
    "lenovo": dict(
        layout="left", product="Lenovo XClarity Controller", accent="#e2231a", head_bg="#2b3138", head_fg="#ffffff",
        nav_bg="#3a424b", nav_fg="#dfe3e8", nav_on_bg="#e2231a", body_bg="#eef0f2", font="'Helvetica Neue',Arial,sans-serif",
        home="/", login_btn="Log In", console_path="/vbsim/xcc-console", nic="NIC",
        nav=[N("overview", "Home"), N("events", "Event"), N("sysinfo", "Inventory"), N("thermal", "Utilization"),
             N("power", "Power Control"), N("console", "Remote Console"), N("vmedia", "Virtual Media"),
             N("firmware", "Firmware Update"), N("license", "License"),
             N("bios", "Server Configuration"), N("network", "BMC Configuration"), N("users", "Users & Security")],
        actions=ACT_XCC, t={}),
    "smc": dict(
        layout="top", product="Supermicro BMC", accent="#1f4e9c", head_bg="#16325c", head_fg="#ffffff",
        nav_bg="#2a5db0", nav_fg="#ffffff", nav_on_bg="#16325c", body_bg="#e9edf3", font="Arial,Helvetica,sans-serif",
        home="/cgi/url_redirect.cgi?url_name=mainmenu", login_btn="Login", console_path="/cgi/url_redirect.cgi", nic="1",
        nav=[N("overview", "System"), N("thermal", "Server Health"), N("sysinfo", "Hardware"), N("events", "Event Log"),
             N("network", "Configuration"), N("console", "Remote Control"), N("vmedia", "Virtual Media"),
             N("firmware", "Maintenance"), N("license", "License"),
             N("power", "Power Control"), N("users", "Users"), N("bios", "BIOS")],
        actions=ACT_SMC, t={}),
}


def lang_of(h):
    return "de" if (h.headers.get("Accept-Language", "") or "").lower().startswith("de") else "en"


def texts(h, skin):
    return dict(DE if lang_of(h) == "de" else EN, **skin["t"])


def css(sk):
    left = sk["layout"] == "left"
    return f"""
*{{box-sizing:border-box}}html,body{{margin:0;height:100%}}
body{{font:14px/1.45 {sk['font']};background:{sk['body_bg']};color:#222}}
#app{{min-height:100%;display:flex;flex-direction:column}}
.top{{display:flex;justify-content:space-between;align-items:center;height:52px;padding:0 18px;background:{sk['head_bg']};color:{sk['head_fg']};border-bottom:3px solid {sk['accent']}}}
.brand{{display:flex;align-items:center;gap:10px}}.logo{{width:14px;height:26px;background:{sk['accent']};border-radius:2px;display:inline-block}}
.bt{{font-weight:600;font-size:15px}}.sub{{opacity:.7;font-size:12px;margin-left:6px}}
.topr{{display:flex;gap:14px;align-items:center;font-size:12px}}
.body{{flex:1;display:flex;{'flex-direction:row' if left else 'flex-direction:column'}}}
.nav{{background:{sk['nav_bg']};color:{sk['nav_fg']};{'width:230px;padding:10px 0;flex-shrink:0' if left else 'display:flex;flex-wrap:wrap;padding:0 12px'}}}
.navsec{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;opacity:.6;padding:12px 18px 4px}}
.navitem{{display:block;color:{sk['nav_fg']};text-decoration:none;{'padding:8px 18px' if left else 'padding:11px 15px'};font-size:13.5px}}
.navitem:hover{{background:rgba(0,0,0,.07)}}.navitem.on{{background:{sk['nav_on_bg']};{'color:#fff' if sk['nav_on_bg'] in ('#0076ce','#e2231a','#16325c') else 'color:#006b53;font-weight:600'}}}
main{{flex:1;padding:18px 24px;min-width:0}}h2{{margin:0 0 14px;font-weight:500;font-size:20px}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}@media(max-width:1000px){{.cols{{grid-template-columns:1fr}}}}
.card{{background:#fff;border:1px solid #dcdfe3;border-radius:4px;margin-bottom:16px}}
.card h3{{margin:0;padding:10px 14px;font-size:14px;font-weight:600;border-bottom:1px solid #e6e8eb;display:flex;justify-content:space-between;align-items:center}}
.card>div{{padding:12px 14px;overflow:auto}}
table{{border-collapse:collapse;width:100%}}.kv th{{text-align:left;font-weight:500;color:#667;width:38%;padding:5px 8px 5px 0;vertical-align:top}}
.kv td{{padding:5px 0}}.grid th{{text-align:left;font-size:12px;color:#556;padding:6px 8px;border-bottom:2px solid #e1e4e8;white-space:nowrap}}
.grid td{{padding:6px 8px;border-bottom:1px solid #eef0f2;font-size:13px}}.muted{{color:#889}}
.badge{{display:inline-block;padding:1px 9px;border-radius:10px;font-size:12px;font-weight:600;color:#fff}}
.badge.ok{{background:#2e9d4f}}.badge.warn{{background:#d98a00}}.badge.bad{{background:#c62828}}.badge.off{{background:#78808c}}
.btn{{font:inherit;padding:6px 14px;border:1px solid #b9bec6;background:#fff;border-radius:3px;cursor:pointer;color:#222;text-decoration:none;display:inline-block}}
.btn.pri{{background:{sk['accent']};border-color:{sk['accent']};color:#fff}}.btn.danger{{border-color:#c62828;color:#c62828}}
.btn.flat{{border:0;background:transparent;color:inherit;text-decoration:underline;padding:2px 6px}}.acts .btn{{margin:0 6px 8px 0}}
.right{{float:right}}.err{{color:#c62828}}
.form label{{display:block;margin:8px 0 3px;font-size:12px;color:#556}}.form select,.form input[type=text]{{padding:6px;border:1px solid #b9bec6;border-radius:3px;font:inherit;max-width:100%}}
.thumb{{width:380px;max-width:100%;height:210px;background:#05070a;border:3px solid #2a2f3a;border-radius:4px;cursor:pointer;overflow:hidden;margin:10px 0}}
.thumb pre{{margin:0;padding:6px;color:#cfd6df;font:7px/8px monospace}}
.foot{{padding:8px 24px;font-size:11px;color:#778;border-top:1px solid #dcdfe3;background:#fff}}
.shade{{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:9}}
.dlg{{background:#fff;padding:20px 24px;border-radius:5px;min-width:320px;box-shadow:0 10px 40px rgba(0,0,0,.3)}}.dlgb{{text-align:right;margin-top:16px}}.dlgb .btn{{margin-left:8px}}
.toast{{position:fixed;right:20px;bottom:50px;background:#2e9d4f;color:#fff;padding:10px 16px;border-radius:4px;z-index:10}}.toast.bad{{background:#c62828}}
"""


def page(title, head, body, sk):
    return ("<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{css(sk)}{head}</style></head><body>{body}</body></html>")


def cfg(h, sk):
    b = h.bmc
    t = texts(h, sk)
    sysid, mgrid = K["SYS"][b.family], K["MGR"][b.family]
    ls = {"hpe": "IEL", "dell": "Lclog", "lenovo": "StdEventLog", "smc": "Log1"}[b.family]
    nav = [n for n in sk["nav"] if not n.get("k") or n["k"] in PAGES]
    return {"vendor": b.vendor, "family": b.family, "layout": sk["layout"], "product": f"{sk['product']} ({b.gen_label})"
            if b.family == "hpe" else sk["product"], "model": b.model, "hostLabel": b.hostname or b.dhcp_hostname() or b.model,
            "sys": f"/redfish/v1/Systems/{sysid}", "mgr": f"/redfish/v1/Managers/{mgrid}",
            "chassis": f"/redfish/v1/Chassis/{sysid}", "license": "/redfish/v1/LicenseService/Licenses/1", "log": f"/redfish/v1/Systems/{sysid}/LogServices/{ls}",
            "nic": sk["nic"], "addr": (b.ipv4.get("Address") or b.host), "bmcMac": b.sim.inv["bmc_mac"],
            "consolePath": sk["console_path"], "nav": nav, "powerActions": sk["actions"], "t": t,
            "locale": "de-DE" if lang_of(h) == "de" else "en-GB"}


PAGES = {"overview", "sysinfo", "events", "console", "power", "thermal", "network", "users", "firmware", "bios",
         "vmedia", "license"}


def login_html(h, error="", expired=False):
    sk = SKINS[h.bmc.family if h.bmc.family in SKINS else "hpe"]
    b, t = h.bmc, texts(h, sk)
    msg = t["wrong"] if error else (t["expired"] if expired else "")
    body = f"""
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:{sk['head_bg']}">
<form id=f style="background:#fff;color:#222;padding:28px 34px;border-radius:4px;min-width:330px;border-top:5px solid {sk['accent']}">
<h2 style="margin:0 0 2px;font-size:19px">{sk['product']}</h2>
<div style="color:#778;font-size:12px;margin-bottom:16px">{b.model} · {b.gen_label}</div>
<div id=msg style="color:#c62828;font-size:13px;min-height:18px;margin-bottom:6px">{msg}</div>
<label style="font-size:12px;color:#556">{t['user']}</label>
<input id=username name=username type=text autocomplete=username autofocus style="width:100%;padding:8px;margin:3px 0 12px;border:1px solid #b9bec6;border-radius:3px;font:inherit">
<label style="font-size:12px;color:#556">{t['password']}</label>
<input id=password name=password type=password autocomplete=current-password style="width:100%;padding:8px;margin:3px 0 16px;border:1px solid #b9bec6;border-radius:3px;font:inherit">
<button id=login-btn type=submit class="btn pri" style="width:100%;padding:9px">{sk['login_btn']}</button>
<div style="margin-top:14px;font-size:11px;color:#889">{t['simNote']}</div>
</form></div>
<script>
document.getElementById('f').addEventListener('submit',function(e){{e.preventDefault();
var m=document.getElementById('msg');
fetch('/redfish/v1/SessionService/Sessions',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},
body:JSON.stringify({{UserName:document.getElementById('username').value,Password:document.getElementById('password').value}})}})
.then(function(r){{if(r.status===201){{location.replace({json.dumps(sk['home'])});}}else{{m.textContent={json.dumps(t['wrong'])};}}}})
.catch(function(){{m.textContent='Connection error';}});}});
</script>"""
    return page(f"{sk['product']} – Login", "", body, sk)


def app_html(h):
    sk = SKINS[h.bmc.family if h.bmc.family in SKINS else "hpe"]
    c = cfg(h, sk)
    body = ('<div id=app></div><script>window.VB=' + json.dumps(c).replace("</", "<\\/") + ";</script><script>"
            + APP_JS + "</script>")
    return page(f"{sk['product']} – {h.bmc.model}", "", body, sk)


def console_html(h):
    sk = SKINS[h.bmc.family if h.bmc.family in SKINS else "hpe"]
    t = texts(h, sk)
    b = h.bmc
    if not b.sim.licensed:
        body = (f"<div style='max-width:520px;margin:12vh auto;background:#fff;padding:26px 30px;border-radius:4px;"
                f"border-top:5px solid {sk['accent']}'><h2 style='margin:0 0 10px'>{t['needLicenseTitle']}</h2>"
                f"<p>{t['needLicensePage'] % b.sim.lic_name}</p></div>")
        return page(f"{sk['product']} – {t['consoleTitle']}", "", body, sk)
    head = """
html,body{background:#0b0d10;color:#cfd6df;overflow:hidden}
.bar{display:flex;gap:10px;align-items:center;padding:6px 12px;background:%s;color:%s;font-size:13px;border-bottom:2px solid %s}
.bar .sp{flex:1}.bar .btn{padding:3px 10px;font-size:12px}
#scr{margin:10px auto;width:max-content;max-width:100%%;background:#000;border:1px solid #222;padding:10px}
#scr pre{margin:0;font:15px/17px 'DejaVu Sans Mono',Consolas,monospace;color:#d8dde6;white-space:pre;outline:0}
#scr.dark pre{color:#444}.cur{background:#d8dde6;color:#000}
.st{padding:4px 12px;font-size:12px;color:#8b93a3}
""" % (sk["head_bg"], sk["head_fg"], sk["accent"])
    body = f"""
<div class=bar><b>{sk['product']} – {t['consoleTitle']}</b><span>{b.model} · {b.serial}</span><span class=sp></span>
<span id=ps></span><button class=btn id=cad>{t.get('ctrlAltDel','Ctrl+Alt+Del')}</button></div>
<div id=scr tabindex=0><pre id=s></pre></div>
<div class=st id=st>{t['simNote']}</div>
<script>
var scr=document.getElementById('scr'),pre=document.getElementById('s'),ps=document.getElementById('ps');
function draw(d){{scr.className=d.dark?'dark':'';var out=d.lines.slice();
 if(d.cursor){{var r=d.cursor[0],c=d.cursor[1],l=out[r]||'';out[r]=l.slice(0,c).replace(/</g,'&lt;')+'\\u0001'+l.slice(c+1);}}
 pre.innerHTML=out.map(function(l){{return l.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace('\\u0001','<span class=cur> </span>');}}).join('\\n');
 ps.textContent=d.power;}}
function poll(){{fetch('/vbsim/console/screen',{{credentials:'same-origin'}}).then(function(r){{
 if(r.status===401){{location.replace('/');throw 0;}}return r.json();}}).then(draw).catch(function(){{}});}}
setInterval(poll,700);poll();
document.addEventListener('keydown',function(e){{
 var k=e.key;if(['F1','F2','F9','F10','F11','F12','Delete','Escape','ArrowUp','ArrowDown','Enter'].indexOf(k)<0)return;
 e.preventDefault();
 fetch('/vbsim/console/key',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{key:k}})}}).then(poll);}});
document.getElementById('cad').onclick=function(){{fetch('/redfish/v1/Systems/{K["SYS"][b.family]}/Actions/ComputerSystem.Reset',
 {{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{ResetType:'ForceRestart'}})}}).then(poll);}};
scr.focus();
</script>"""
    return page(f"{sk['product']} – {t['consoleTitle']}", head, body, sk)


# ---------------------------------------------------------------- Routing
def handle(h, method, p):
    """True, wenn die Weboberfläche die Anfrage bearbeitet hat."""
    b = h.bmc
    if b.family not in SKINS:
        return False
    sk = SKINS[b.family]
    home = urlsplit(sk["home"]).path
    qs = parse_qs(urlsplit(h.path).query)
    if method == "GET":
        if p in ("/", "/login.html"):
            if h.session_user():
                if p == "/":                   # Adresse bleibt "/": der Hash-Router der App übernimmt
                    return h.send_text(200, app_html(h), "text/html; charset=utf-8") or True
                return h.redirect(sk["home"]) or True
            return h.send_text(200, login_html(h, expired=bool(qs.get("expired"))), "text/html; charset=utf-8") or True
        is_console = p in ("/irc.html", "/console", "/vbsim/xcc-console")
        if p == "/cgi/url_redirect.cgi":
            un = (qs.get("url_name") or [""])[0]
            is_console = "ikvm" in un or "console" in un or not un
        if p == home and not is_console or p in ("/index.html", "/restgui/start.html"):
            if not h.session_user():
                return h.redirect("/") or True
            return h.send_text(200, app_html(h), "text/html; charset=utf-8") or True
        if is_console:
            if not h.session_user():
                return h.redirect("/") or True
            return h.send_text(200, console_html(h), "text/html; charset=utf-8") or True
        if p == "/vbsim/console/screen":
            if not h.session_user():
                return h.send(401, {"error": {"message": "login required"}}) or True
            if not b.sim.licensed:
                return h.send(403, {"error": {"message": "license required"}}) or True
            return h.send(200, vcon.screen(b)) or True
    if method == "POST" and p == "/vbsim/console/key":
        if not h.session_user():
            return h.send(401, {"error": {"message": "login required"}}) or True
        if not b.sim.licensed:
            return h.send(403, {"error": {"message": "license required"}}) or True
        vcon.key(b, str(h.read_json().get("key", "")))
        return h.send(200, {"ok": True}) or True
    return False
