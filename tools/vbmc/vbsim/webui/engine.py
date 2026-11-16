"""Weboberflächen der virtuellen BMCs: Login, Verwaltungsoberfläche (SPA), Remote-Konsole.

Jeder Hersteller hat einen eigenen Skin, der dem Bedienkonzept des Originals folgt: Seitenrahmen
(Kopfleiste, Menü oben/links, Reiter), Menüstruktur, Begriffe, Dashboard, Anmeldeseite und Werkzeugleiste
der Konsole. Die Seiten lesen live aus Redfish, es gibt keine Parallelwelt.

Rechtliche Leitplanken (bewusst so gebaut, bitte beim Weiterbauen beibehalten):
- keine Logos, Wortmarken, Hersteller-Grafiken, -Icons oder -Schriften; alle Icons sind eigene, einfache SVGs
- kein Code, CSS oder Bild aus Hersteller-Firmware übernommen; nachgebaut wird nur Aufbau und Bedienablauf
- Marken (iDRAC, iLO, XClarity …) nicht als Seitentitel, nur beschreibend im Simulationshinweis
  („…-Profil“); Farben angelehnt, aber nicht die exakten Markenfarben
- jede Seite (auch die Anmeldung) trägt sichtbar den Hinweis, dass es eine Simulation ist
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .. import console as vcon

K = {}                                  # von vbmc.py gesetzt: SYS/MGR (siehe configure)


def configure(**kw):
    K.update(kw)


APP_JS = (Path(__file__).with_name("app.js")).read_text(encoding="utf-8")

# ---------------------------------------------------------------- Texte
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

EN.update({
    "certificate": "HTTPS certificate", "subject": "Subject", "issuer": "Issuer", "validFrom": "Valid from",
    "validTo": "Valid until", "fingerprint": "SHA-256 fingerprint", "selfSigned": "Self-signed",
    "genCsr": "Generate certificate signing request", "commonName": "Common name (CN)",
    "organization": "Organization", "country": "Country (2 letters)", "importCert": "Import certificate",
    "pem": "Certificate (PEM)",
    "certHint": "Simulation: the imported certificate is stored and shown here, but the TLS handshake of this lab server keeps using its lab certificate.",
})
DE.update({
    "certificate": "HTTPS-Zertifikat", "subject": "Antragsteller", "issuer": "Aussteller", "validFrom": "Gültig ab",
    "validTo": "Gültig bis", "fingerprint": "SHA-256-Fingerabdruck", "selfSigned": "Selbstsigniert",
    "genCsr": "Zertifikatsanforderung (CSR) erzeugen", "commonName": "Common Name (CN)",
    "organization": "Organisation", "country": "Land (2 Buchstaben)", "importCert": "Zertifikat importieren",
    "pem": "Zertifikat (PEM)",
    "certHint": "Simulation: Das importierte Zertifikat wird gespeichert und hier angezeigt; der TLS-Handshake dieses Laborservers nutzt aber weiter sein Laborzertifikat.",
})

EN.update({
    "simNote": "Simulated server (BMC-Steward test lab) – not a vendor product, no real hardware.",
    "simShort": "SIMULATION", "sysHealth": "System Health", "healthy": "Healthy", "degraded": "Warning",
    "critical": "Critical", "batteries": "Batteries", "cooling": "Cooling", "cpus": "CPUs",
    "psus": "Power Supplies", "temperatures": "Temperatures", "voltages": "Voltages", "storageH": "Storage",
    "identify": "Identify System", "powerMenu": "Power", "info": "Information", "statusH": "Status",
    "uidLed": "UID Indicator", "productName": "Product Name", "productId": "Product ID", "systemRom": "System ROM",
    "licType": "License Type", "bmcFw": "Firmware Version", "bmcHost": "Controller Hostname",
    "sysHealthShort": "System Health", "bmcHealth": "Controller Health", "serverPower": "Server Power",
    "healthSummary": "Health Summary", "activeEvents": "Active System Events", "noActive": "No active events.",
    "sysInfoSettings": "System Information and Settings", "machineName": "Machine Name",
    "machineType": "Machine Type/Model", "systemState": "System State", "quickActions": "Quick Actions",
    "powerAction": "Power Action", "consolePreview": "Remote Console Preview", "launch": "Launch",
    "powerUtil": "Power Utilization", "systemBoard": "System Board", "fanH": "Fan", "pciAdapters": "PCI Adapters",
    "summary": "Summary", "fwRev": "Firmware Revision", "biosVer": "BIOS Version", "bmcMac": "BMC MAC address",
    "refreshPreview": "Refresh Preview Image", "ipmiPower": "Power Control via IPMI", "hostIdent": "Host Identification",
    "server": "Server", "userH": "User", "na": "Not available in the simulation.", "refresh": "Refresh",
    "physDisks": "Physical Disks", "recentLogs": "Recent Logs", "osState": "Operating System",
    "uidOn": "On", "uidOff": "Off", "running": "Running", "systemOn": "System running", "systemOff": "System off",
    "inPost": "In POST",
})
DE.update({
    "simNote": "Simulierter Server (BMC-Steward-Testlabor) – kein Herstellerprodukt, keine echte Hardware.",
    "sysHealth": "Systemzustand", "healthy": "Fehlerfrei", "degraded": "Warnung", "critical": "Kritisch",
    "batteries": "Batterien", "cooling": "Kühlung", "cpus": "Prozessoren", "psus": "Netzteile",
    "temperatures": "Temperaturen", "voltages": "Spannungen", "storageH": "Speicher", "identify": "System identifizieren",
    "powerMenu": "Strom", "info": "Informationen", "statusH": "Status", "uidLed": "UID-Anzeige",
    "productName": "Produktname", "productId": "Produkt-ID", "systemRom": "System-ROM", "licType": "Lizenztyp",
    "bmcFw": "Firmware-Version", "bmcHost": "Controller-Hostname", "sysHealthShort": "Systemzustand",
    "bmcHealth": "Controller-Zustand", "serverPower": "Server-Strom", "healthSummary": "Zustandsübersicht",
    "activeEvents": "Aktive Systemereignisse", "noActive": "Keine aktiven Ereignisse.",
    "sysInfoSettings": "Systeminformationen und -einstellungen", "machineName": "Maschinenname",
    "machineType": "Maschinentyp/-modell", "systemState": "Systemzustand", "quickActions": "Schnellaktionen",
    "powerAction": "Stromaktion", "consolePreview": "Vorschau Remote-Konsole", "launch": "Starten",
    "powerUtil": "Stromverbrauch", "systemBoard": "Systemplatine", "fanH": "Lüfter", "pciAdapters": "PCI-Adapter",
    "summary": "Übersicht", "fwRev": "Firmware-Revision", "biosVer": "BIOS-Version", "bmcMac": "BMC-MAC-Adresse",
    "refreshPreview": "Vorschau aktualisieren", "ipmiPower": "Stromsteuerung über IPMI", "hostIdent": "Host-Identifikation",
    "server": "Server", "userH": "Benutzer", "na": "In der Simulation nicht verfügbar.", "refresh": "Aktualisieren",
    "physDisks": "Physische Laufwerke", "recentLogs": "Letzte Protokolleinträge", "osState": "Betriebssystem",
    "uidOn": "Ein", "uidOff": "Aus", "running": "Läuft", "systemOn": "System läuft", "systemOff": "System aus",
    "inPost": "Im POST",
})

# Power-Aktionen: (Beschriftung, Redfish-ResetType, gefährlich)
ACT_HPE = [("Momentary Press", "PushPowerButton", 0), ("Press and Hold", "ForceOff", 1), ("Reset", "ForceRestart", 1),
           ("Cold Boot", "PowerCycle", 1), ("Graceful Shutdown", "GracefulShutdown", 0), ("Power On", "On", 0)]
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


def G(label, *items, icon=""):
    return {"label": label, "icon": icon, "items": list(items)}


# Konsolen-Menüs: (Beschriftung, Aktion oder Liste von (Beschriftung, Aktion))
#   p:<ResetType>  boot:<Target>  cad  full  refresh  close  na
CON_POWER_DELL = [("Power On System", "p:On"), ("Power Off System", "p:ForceOff"), ("Graceful Shutdown", "p:GracefulShutdown"),
                  ("Reset System (warm boot)", "p:ForceRestart"), ("Power Cycle System (cold boot)", "p:PowerCycle")]

SKINS = {
    "hpe": dict(
        shell="hpe", product="Lights-Out Management", home="/index.html", login_btn="Log In", console_path="/irc.html", nic="1",
        menu=[G("Information", N("overview", "Overview"), N("events", "Event Log"), icon="info"),
              G("System Information", N("sysinfo", "Summary"), N("storage", "Storage"), icon="server"),
              G("Firmware & OS Software", N("firmware", "Installed Firmware"), icon="chip"),
              G("Remote Console & Media", N("console", "Remote Console"), N("vmedia", "Virtual Media"), icon="screen"),
              G("Power & Thermal", N("power", "Server Power"), N("thermal", "Fans & Temperatures"), icon="power"),
              G("Dedicated Network Port", N("network", "Summary"), icon="net"),
              G("Administration", N("users", "User Administration"), N("license", "Licensing"), icon="gear"),
              G("Security", N("certificate", "SSL Certificate"), icon="lock"),
              G("System Configuration", N("bios", "BIOS/Platform Configuration"), icon="sliders")],
        actions=ACT_HPE,
        console_menu=[("Power Switch", [("Momentary Press", "p:PushPowerButton"), ("Press and Hold", "p:ForceOff"),
                                        ("Cold Boot", "p:PowerCycle"), ("Reset", "p:ForceRestart")]),
                      ("Virtual Drives", [("Image File CD/DVD-ROM", "na"), ("URL CD/DVD-ROM", "na")]),
                      ("Keyboard", [("Ctrl-Alt-Del", "cad")]), ("Fullscreen", "full"), ("Help", "na")],
        t={"en": {}, "de": {}}),
    "dell": dict(
        shell="dell", product="Remote Access Controller", home="/restgui/start.html", login_btn="Log In",
        console_path="/console", nic="1",
        menu=[G("Dashboard", N("overview", "Dashboard")),
              G("System", N("sysinfo", "Inventory"), N("power", "Power"), N("thermal", "Cooling")),
              G("Storage", N("storage", "Physical Disks")),
              G("Configuration", N("bios", "BIOS Settings"), N("vmedia", "Virtual Media"), N("console", "Virtual Console"),
                N("license", "Licenses")),
              G("Maintenance", N("events", "Lifecycle Log"), N("firmware", "System Update")),
              G("Controller Settings", N("network", "Network"), N("users", "Users"), N("certificate", "SSL Certificate"))],
        actions=ACT_DELL,
        console_menu=[("Boot", [("BIOS Setup", "boot:BiosSetup"), ("Virtual CD/DVD/ISO", "boot:Cd"), ("PXE", "boot:Pxe"),
                                ("Normal Boot", "boot:None")]),
                      ("Power", CON_POWER_DELL), ("Chat", "na"), ("Keyboard", [("Ctrl+Alt+Del", "cad")]),
                      ("Screen Capture", "na"), ("Refresh", "refresh"), ("Full Screen", "full"),
                      ("Disconnect Viewer", "close"), ("Console Controls", "na"), ("Virtual Media", "na")],
        t={"en": {"on": "ON", "off": "OFF", "serial": "Service Tag", "bmcFw": "Controller Firmware Version",
                  "systemRom": "BIOS Version"},
           "de": {"on": "EIN", "off": "AUS", "serial": "Service-Tag", "bmcFw": "Controller-Firmware-Version",
                  "systemRom": "BIOS-Version"}}),
    "lenovo": dict(
        shell="lenovo", product="Systems Management Controller", home="/", login_btn="Log In",
        console_path="/vbsim/xcc-console", nic="NIC",
        menu=[G("Home", N("overview", "Home"), icon="home"), G("Events", N("events", "Event Log"), icon="bell"),
              G("Inventory", N("sysinfo", "Inventory"), icon="list"), G("Utilization", N("thermal", "Utilization"), icon="gauge"),
              G("Storage", N("storage", "Detail"), icon="disk"), G("Remote Console", N("console", "Remote Console"), icon="screen"),
              G("Firmware Update", N("firmware", "Firmware Update"), icon="chip"),
              G("Server Configuration", N("power", "Power Policy & Actions"), N("vmedia", "Media Mount"),
                N("bios", "System Settings"), icon="server"),
              G("BMC Configuration", N("network", "Network"), N("users", "User/LDAP"), N("certificate", "Security"),
                N("license", "License"), icon="gear")],
        actions=ACT_XCC,
        console_menu=[("Screen Capture", "na"), ("Keyboard", [("Ctrl+Alt+Del", "cad")]), ("Mouse", "na"),
                      ("Media", "na"), ("Power", [(a[0], "p:" + a[1]) for a in ACT_XCC]), ("Full Screen", "full"),
                      ("Quit", "close")],
        t={"en": {"serial": "Serial Number"}, "de": {}}),
    "smc": dict(
        shell="smc", product="Baseboard Management Controller", home="/cgi/url_redirect.cgi?url_name=mainmenu",
        login_btn="Login", console_path="/cgi/url_redirect.cgi", nic="1",
        menu=[G("System", N("overview", "Summary"), N("sysinfo", "Hardware Information")),
              G("Server Health", N("thermal", "Sensor Readings"), N("events", "Event Log"), N("storage", "Storage Monitoring")),
              G("Configuration", N("network", "Network"), N("users", "Users"), N("certificate", "SSL Certification")),
              G("Remote Control", N("console", "Console Redirection"), N("power", "Power Control")),
              G("Virtual Media", N("vmedia", "CD-ROM Image")),
              G("Maintenance", N("firmware", "Firmware Update"), N("bios", "BIOS Configuration")),
              G("Miscellaneous", N("license", "License Activation"))],
        actions=ACT_SMC,
        console_menu=[("Virtual Media", "na"), ("Record", "na"), ("Macro", [("Ctrl+Alt+Del", "cad")]),
                      ("Options", [("Full Screen", "full"), ("Refresh", "refresh")]), ("User List", "na"),
                      ("Capture", "na"), ("Power Control", [(a[0], "p:" + a[1]) for a in ACT_SMC]), ("Exit", "close")],
        t={"en": {}, "de": {}}),
}

PAGES = {"overview", "sysinfo", "events", "console", "power", "thermal", "network", "users", "firmware", "bios",
         "vmedia", "license", "certificate", "storage"}

# Seitenbeschreibungen (Supermicro zeigt unter jedem Seitentitel einen Erklärungssatz)
DESC = {"overview": "Summary of the system and the management controller.",
        "sysinfo": "Processors, memory and network adapters of the host.",
        "thermal": "Readings of all temperature and fan sensors.", "events": "Events logged by the management controller.",
        "storage": "Physical drives reported by the storage controller.", "network": "Network settings of the controller port.",
        "users": "Users that may log in to the controller.", "certificate": "Certificate of the web server.",
        "console": "Launch the console redirection window.", "power": "Control the host power.",
        "vmedia": "Mount an image as virtual CD-ROM and choose the next boot device.",
        "firmware": "Installed firmware versions.", "bios": "Current BIOS settings (read only).",
        "license": "Activate a license key."}


def lang_of(h):
    return "de" if (h.headers.get("Accept-Language", "") or "").lower().startswith("de") else "en"


def texts(h, skin):
    lang = lang_of(h)
    t = dict(DE if lang == "de" else EN)
    t.update(skin["t"]["en"])
    if lang != "en":
        t.update(skin["t"].get(lang, {}))
    return t


def gen_no(b):
    m = re.search(r"(\d+)", b.gen_label or "")
    return m.group(1) if m and b.family != "smc" else ""


def product(sk, b):
    n = gen_no(b)
    return f"{sk['product']} {n}".strip()


VENDOR = {"dell": "Dell", "hpe": "HPE", "lenovo": "Lenovo", "smc": "Supermicro"}


def edition(name):
    """Lizenz-Edition ohne Produktmarke („iDRAC9 Enterprise“ → „Enterprise“) für Kopfzeile/Anmeldung."""
    return re.sub(r"^(iDRAC\d*|iLO\s*\d*|XCC\d*)\s+", "", name or "")


def profile(b):
    """Beschreibende (nominative) Nennung des nachgebildeten Geräts, nur im Simulationshinweis."""
    return f"{VENDOR.get(b.family, b.family)} {b.gen_label}"


# ---------------------------------------------------------------- CSS
BASE_CSS = """
*{box-sizing:border-box}html,body{margin:0;height:100%}
#app{min-height:100%;display:flex;flex-direction:column}
table{border-collapse:collapse;width:100%}
.kv th{text-align:left;font-weight:400;color:var(--mut);width:42%;padding:6px 10px 6px 0;vertical-align:top}
.kv td{padding:6px 0;font-weight:500}
.grid th{text-align:left;padding:7px 9px;white-space:nowrap}.grid td{padding:7px 9px}
.muted{color:var(--mut)}.err{color:#c4262e}.right{float:right}
.badge{display:inline-flex;align-items:center;gap:5px;font-weight:600;font-size:12px}
.badge:before{content:"";width:9px;height:9px;border-radius:50%;background:currentColor}
.badge.ok{color:#2b8a3e}.badge.warn{color:#c77700}.badge.bad{color:#c4262e}.badge.off{color:#7b828c}
.btn{font:inherit;padding:6px 14px;border:1px solid #a9afb7;background:#fff;cursor:pointer;color:#222;text-decoration:none;display:inline-block;line-height:1.3}
.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}.btn.danger{color:#c4262e;border-color:#d6a3a6}
.btn.flat{border:0;background:transparent;color:inherit;padding:2px 6px;text-decoration:underline}
.acts .btn{margin:0 6px 8px 0}
.form label{display:block;margin:9px 0 3px;font-size:12px;color:var(--mut)}
.form select,.form input[type=text]{padding:6px 7px;border:1px solid #a9afb7;font:inherit;max-width:100%}
.form textarea{padding:6px;border:1px solid #a9afb7;font:12px monospace;max-width:100%}
.thumb{width:100%;max-width:400px;aspect-ratio:16/10;background:#000;cursor:pointer;overflow:hidden;position:relative}
.thumb pre{margin:0;padding:6px;color:#cfd6df;font:6.5px/7.5px monospace;white-space:pre}
.thumb .lic{color:#f3b3b3;padding:12px;font-size:13px}
.shade{position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:30}
.dlg{background:#fff;padding:22px 26px;min-width:340px;box-shadow:0 10px 40px rgba(0,0,0,.35)}
.dlgb{text-align:right;margin-top:18px}.dlgb .btn{margin-left:8px}
.toast{position:fixed;right:20px;bottom:46px;background:#2b8a3e;color:#fff;padding:10px 16px;z-index:40;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.toast.bad{background:#c4262e}
.sim{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.08em;padding:2px 7px;border:1px solid currentColor;border-radius:2px;opacity:.85}
.foot{padding:7px 20px;font-size:11px;color:var(--mut);border-top:1px solid var(--line);background:#fff}
.ico{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;vertical-align:-3px;flex-shrink:0}
.dd{position:relative;display:inline-block}.ddm{display:none;position:absolute;z-index:20;top:100%;left:0;min-width:240px;background:#fff;border:1px solid var(--line);box-shadow:0 6px 18px rgba(0,0,0,.18);padding:4px 0}
.dd.open .ddm{display:block}.ddm button,.ddm a{display:block;width:100%;text-align:left;border:0;background:none;padding:8px 14px;font:inherit;color:#222;cursor:pointer;text-decoration:none}
.ddm button:hover,.ddm a:hover{background:#eef3f8}
.hl{display:grid;grid-template-columns:1fr auto;gap:2px 10px}.hl div{padding:5px 0;border-bottom:1px solid var(--line2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:10px}
.tile{border:1px solid var(--line);padding:12px 10px;text-align:center;font-size:12.5px;background:#fff}
.tile .st{display:block;margin:0 auto 7px;width:26px;height:26px}
.bar{height:12px;background:#e3e6ea;position:relative}.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--acc)}
"""

CSS = {
    # ---- HPE: Navigation links (hell), Seitentitel „Gruppe - Seite“, Reiter mit grüner Linie, Symbolleiste oben rechts
    "hpe": """
:root{--acc:#139b76;--mut:#646a71;--line:#d9dcdf;--line2:#eceef0}
body{font:13px/1.45 Arial,Helvetica,sans-serif;background:#fff;color:#1e2226}
.lay{flex:1;display:flex;min-height:0}
.nav{width:236px;flex-shrink:0;background:#f2f3f4;border-right:1px solid var(--line);display:flex;flex-direction:column}
.brandblk{background:#2a2d30;color:#fff;padding:14px 16px 12px}
.brandblk .mk{display:inline-block;width:44px;height:6px;background:var(--acc);margin-bottom:9px}
.brandblk .p{font-size:15px;font-weight:700;letter-spacing:.01em}.brandblk .m{font-size:11.5px;opacity:.75;margin-top:2px}
.brandblk .sim{margin-top:8px;color:#9fe0cc}
.navg{display:flex;align-items:center;gap:9px;padding:9px 16px;color:#1e2226;text-decoration:none;border-left:4px solid transparent;font-size:13px}
.navg:hover{background:#e6e8ea}.navg.on{border-left-color:var(--acc);background:#fff;font-weight:700}
.navg .ico{color:#6b7178}
.navthumb{margin-top:auto;padding:12px 14px;border-top:1px solid var(--line)}
.navthumb .thumb{aspect-ratio:16/10}.navthumb .lnk{font-size:11.5px;margin-top:6px}.navthumb a{color:#0d6e88;margin-right:6px}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.top{display:flex;align-items:center;justify-content:space-between;padding:0 18px;height:50px;border-bottom:1px solid var(--line)}
.top h1{font-size:18px;font-weight:400;margin:0}.top h1 b{font-weight:700}
.icons{display:flex;gap:16px;align-items:center;font-size:12px;color:#40464c}.icons span{display:inline-flex;gap:5px;align-items:center}
.icons .ico{width:18px;height:18px}
.tabs{display:flex;gap:2px;padding:0 18px;border-bottom:1px solid var(--line)}
.tabs a{padding:10px 14px 8px;color:#40464c;text-decoration:none;border-bottom:3px solid transparent}
.tabs a.on{border-bottom-color:var(--acc);color:#000;font-weight:700}
main{padding:18px 20px;flex:1;min-width:0}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:20px}@media(max-width:1100px){.cols{grid-template-columns:1fr}}
.card{margin-bottom:22px}.card h3{margin:0 0 8px;padding-bottom:6px;font-size:14px;font-weight:700;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
.grid th{background:#f2f3f4;border-bottom:1px solid var(--line);font-size:12px;color:#40464c}.grid td{border-bottom:1px solid var(--line2)}
.btn{border-radius:2px}.btn.pri{font-weight:700}
""",
    # ---- Dell: dunkle Kopfleiste, waagerechtes Menü mit Aufklappern, große Seitentitel mit Reitern, weiße Kacheln
    "dell": """
:root{--acc:#1a6fbf;--mut:#5f656c;--line:#d5d8dc;--line2:#ebedef}
body{font:14px/1.45 "Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:#f1f2f3;color:#1f2327}
.top{display:flex;align-items:center;justify-content:space-between;height:48px;padding:0 18px;background:#232425;color:#fff}
.brand{display:flex;align-items:center;gap:12px}.brand .mk{width:26px;height:26px;border:2px solid #fff;border-radius:3px;display:grid;place-items:center;font-size:10px;font-weight:700}
.brand .p{font-size:15px}.brand .ed{font-size:12px;opacity:.75}
.topr{display:flex;align-items:center;gap:18px;font-size:13px}.topr .ico{width:18px;height:18px}.topr .sim{color:#ffd27a}
.topr button{background:none;border:0;color:#fff;cursor:pointer;font:inherit;display:inline-flex;gap:6px;align-items:center}
.menu{display:flex;background:#fff;border-bottom:1px solid var(--line);padding:0 10px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.menu>.dd>a,.menu>a{display:block;padding:13px 16px 11px;color:#1f2327;text-decoration:none;border-bottom:3px solid transparent;font-size:14px}
.menu .on>a,.menu>a.on{border-bottom-color:var(--acc);color:var(--acc)}
.menu .dd:hover .ddm{display:block}.menu .ddm{top:calc(100% + 0px)}
.phead{padding:16px 24px 0}.phead h1{margin:0;font-size:24px;font-weight:300}
.tabs{display:flex;gap:4px;margin-top:10px;border-bottom:1px solid var(--line)}
.tabs a{padding:8px 14px;color:#3a3f45;text-decoration:none;border-bottom:3px solid transparent}.tabs a.on{border-bottom-color:var(--acc);color:#000;font-weight:600}
main{padding:16px 24px;flex:1;min-width:0}
.dashbar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.dgrid{display:grid;grid-template-columns:minmax(230px,1fr) minmax(300px,1.4fr) minmax(280px,1.2fr);gap:16px}
@media(max-width:1150px){.dgrid{grid-template-columns:1fr 1fr}}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:1000px){.cols{grid-template-columns:1fr}}
.card{background:#fff;border:1px solid var(--line);margin-bottom:16px}
.card h3{margin:0;padding:12px 16px;font-size:15px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
.card>div{padding:2px 16px 14px;overflow:auto}
.bighealth{display:flex;align-items:center;gap:12px;font-size:20px;font-weight:300;margin:4px 0 12px}.bighealth .st{width:38px;height:38px}
.grid th{border-bottom:2px solid var(--line);font-size:12.5px;color:#4a5057;font-weight:600}.grid td{border-bottom:1px solid var(--line2)}
.btn{border-radius:2px;border-color:#8f969e}.btn.pri{font-weight:600}
""",
    # ---- Lenovo: dunkle Kopfleiste, dunkle Navigation mit Symbolen links, Kacheln auf hellgrauem Grund
    "lenovo": """
:root{--acc:#2f7fcf;--mut:#5d636a;--line:#d8dbdf;--line2:#edeff1}
body{font:13.5px/1.45 "Helvetica Neue",Helvetica,Arial,sans-serif;background:#eef0f2;color:#1e2226}
.top{display:flex;align-items:center;justify-content:space-between;height:50px;padding:0 16px;background:#16191c;color:#fff}
.brand{display:flex;align-items:center;gap:12px}.brand .mk{width:30px;height:20px;border:2px solid #c9ced4;display:grid;place-items:center;font-size:9px;font-weight:700;color:#c9ced4}
.brand .p{font-size:15px;font-weight:600}.brand .sys{font-size:12.5px;color:#aab1b9;border-left:1px solid #3b4148;padding-left:12px}
.topr{display:flex;gap:16px;align-items:center;font-size:12.5px}.topr span{display:inline-flex;gap:5px;align-items:center}.topr .sim{color:#8cc4ff}
.topr button{background:none;border:0;color:#fff;cursor:pointer;font:inherit}
.lay{flex:1;display:flex;min-height:0}
.nav{width:220px;flex-shrink:0;background:#262a2f;color:#cfd4da;padding:8px 0}
.navg{display:flex;align-items:center;gap:10px;padding:10px 16px;color:#cfd4da;text-decoration:none;border-left:3px solid transparent}
.navg:hover{background:#30353b}.navg.on{background:#353b42;border-left-color:var(--acc);color:#fff}
.navsub{padding:7px 16px 7px 45px;display:block;color:#aeb5bd;text-decoration:none;font-size:12.5px}.navsub.on{color:#fff;font-weight:600}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.phead{padding:14px 20px 0}.phead h1{margin:0;font-size:20px;font-weight:400}
main{padding:14px 20px;flex:1;min-width:0}
.hgrid{display:grid;grid-template-columns:1.25fr 1fr;gap:16px}@media(max-width:1100px){.hgrid{grid-template-columns:1fr}}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:1000px){.cols{grid-template-columns:1fr}}
.card{background:#fff;border-top:3px solid #c9ced4;margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.card h3{margin:0;padding:11px 15px;font-size:14.5px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
.card>div{padding:2px 15px 14px;overflow:auto}
.grid th{background:#f4f5f6;border-bottom:1px solid var(--line);font-size:12px;color:#4a5057;font-weight:600}.grid td{border-bottom:1px solid var(--line2)}
.btn{border-radius:3px}.form select{min-width:240px}
""",
    # ---- Supermicro (klassisch): Banner, blaue Menüleiste, graue Untermenü-Spalte, blaue Seitentitel mit Beschreibung
    "smc": """
:root{--acc:#2557a7;--mut:#555b62;--line:#c4ccd8;--line2:#e1e6ee}
body{font:12.5px/1.4 Arial,Helvetica,sans-serif;background:#fff;color:#1c1f23}
.banner{display:flex;align-items:center;justify-content:space-between;height:58px;padding:0 16px;background:linear-gradient(#fff,#eef1f5);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:12px}.brand .mk{width:32px;height:32px;border:3px solid var(--acc);display:grid;place-items:center;font-size:11px;font-weight:700;color:var(--acc)}
.brand .p{font-size:17px;font-weight:700;color:#24467f}.brand .sim{color:#b06200}
.links{font-size:12px;color:#333;display:flex;gap:6px;align-items:center}.links a,.links button{color:#24467f;background:none;border:0;font:inherit;cursor:pointer;text-decoration:none}
.menu{display:flex;background:linear-gradient(#3a6cba,#21498f);border-bottom:2px solid #163469}
.menu a{color:#fff;text-decoration:none;font-weight:700;padding:8px 16px;border-right:1px solid rgba(255,255,255,.25);font-size:12.5px}
.menu a:hover{background:rgba(255,255,255,.12)}.menu a.on{background:linear-gradient(#f6f8fb,#dfe6f0);color:#163469}
.lay{flex:1;display:flex;min-height:0}
.side{width:200px;flex-shrink:0;background:#e9edf3;border-right:1px solid var(--line);padding:10px 0}
.side a{display:block;padding:6px 14px;color:#1c3d73;text-decoration:none}.side a:before{content:"\\25B8  ";color:#7a8aa3}
.side a.on{font-weight:700;background:#fff}
.main{flex:1;min-width:0;padding:12px 20px}
.main h1{color:#1f4e9c;font-size:17px;margin:4px 0 4px}.desc{margin:0 0 12px;color:#444;border-bottom:1px solid var(--line);padding-bottom:8px}
.cols{display:grid;grid-template-columns:1.3fr 1fr;gap:18px}@media(max-width:1000px){.cols{grid-template-columns:1fr}}
.card{border:1px solid var(--line);margin-bottom:14px}
.card h3{margin:0;padding:6px 10px;font-size:12.5px;font-weight:700;color:#fff;background:linear-gradient(#5e86c4,#3c66a8);display:flex;justify-content:space-between;align-items:center}
.card>div{padding:8px 10px;overflow:auto}
.kv th{color:#1c1f23;font-weight:700}.kv tr:nth-child(odd){background:#f3f5f9}.kv th,.kv td{padding:5px 8px}
.grid th{background:linear-gradient(#f6f8fb,#dbe2ec);border:1px solid var(--line);font-size:12px}.grid td{border:1px solid var(--line2)}
.btn{background:linear-gradient(#fdfdfd,#e2e6ec);border:1px solid #8c97a8;border-radius:3px;padding:4px 12px;font-size:12px}
.btn.pri{background:linear-gradient(#4b7bc6,#2a579e);border-color:#22457f;color:#fff}
.badge{font-weight:700}
""",
}


def css(sk):
    return BASE_CSS + CSS[sk["shell"]]


def page(title, head, body, sk):
    return ("<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{css(sk)}{head}</style></head><body>{body}</body></html>")


def skin_of(h):
    return SKINS[h.bmc.family if h.bmc.family in SKINS else "hpe"]


def cfg(h, sk):
    b = h.bmc
    t = texts(h, sk)
    sysid, mgrid = K["SYS"][b.family], K["MGR"][b.family]
    ls = {"hpe": "IEL", "dell": "Lclog", "lenovo": "StdEventLog", "smc": "Log1"}[b.family]
    menu = []
    for g in sk["menu"]:
        items = [i for i in g["items"] if i["k"] in PAGES]
        if items:
            menu.append(dict(g, items=items))
    return {"vendor": b.vendor, "family": b.family, "shell": sk["shell"], "product": product(sk, b),
            "profile": profile(b), "model": b.model, "serial": b.serial, "gen": b.gen_label,
            "hostLabel": b.hostname or b.dhcp_hostname() or b.model,
            "sys": f"/redfish/v1/Systems/{sysid}", "mgr": f"/redfish/v1/Managers/{mgrid}",
            "chassis": f"/redfish/v1/Chassis/{sysid}", "license": "/redfish/v1/LicenseService/Licenses/1",
            "cert": f"/redfish/v1/Managers/{mgrid}/NetworkProtocol/HTTPS/Certificates/1", "log": f"/redfish/v1/Systems/{sysid}/LogServices/{ls}",
            "nic": sk["nic"], "addr": (b.ipv4.get("Address") or b.host), "bmcMac": b.sim.inv["bmc_mac"],
            "consolePath": sk["console_path"], "menu": menu, "powerActions": sk["actions"], "t": t, "desc": DESC,
            "user": h.session_user() or "", "locale": "de-DE" if lang_of(h) == "de" else "en-GB"}


# ---------------------------------------------------------------- Anmeldung
LOGIN_JS = """
document.getElementById('f').addEventListener('submit',function(e){e.preventDefault();
var m=document.getElementById('msg');
fetch('/redfish/v1/SessionService/Sessions',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
body:JSON.stringify({UserName:document.getElementById('username').value,Password:document.getElementById('password').value})})
.then(function(r){if(r.status===201){location.replace(%s);}else{m.textContent=%s;}})
.catch(function(){m.textContent='Connection error';});});
"""

LOGIN_CSS = {
    "hpe": """
body{background:#1f2224;color:#fff}.lg{min-height:100vh;display:flex;align-items:center;justify-content:center;gap:60px;padding:30px;flex-wrap:wrap}
.lg .who{max-width:340px}.lg .mk{width:60px;height:8px;background:var(--acc);margin-bottom:18px}
.lg .who h1{font-size:34px;font-weight:300;margin:0 0 6px}.lg .who .m{opacity:.75;font-size:14px}
.lg form{width:330px}.lg label{display:block;font-size:12px;opacity:.8;margin:12px 0 4px}
.lg input,.lg select{width:100%;padding:9px;border:1px solid #555;background:#2c3033;color:#fff;font:inherit}
.lg .btn{width:100%;margin-top:20px;padding:10px;font-weight:700}#msg{color:#ff8a8a;min-height:18px;font-size:13px}
.lg .sn{font-size:11px;opacity:.6;margin-top:16px}
""",
    "dell": """
body{background:radial-gradient(ellipse at 30% 20%,#3a3d41,#141516 70%)}.lg{min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{background:#fff;width:400px;padding:30px 36px;box-shadow:0 12px 50px rgba(0,0,0,.5)}
.box .mk{width:42px;height:42px;border:3px solid #232425;border-radius:4px;display:grid;place-items:center;font-weight:700;font-size:13px;margin-bottom:14px}
.box h1{font-size:21px;font-weight:300;margin:0}.box .ed{display:inline-block;margin:6px 0 14px;font-size:12px;color:#fff;background:#4a5057;padding:2px 8px}
.box label{display:block;font-size:12.5px;color:var(--mut);margin:11px 0 4px}.box input,.box select{width:100%;padding:8px;border:1px solid #9aa1a8;font:inherit}
.box .btn{width:100%;margin-top:20px;padding:9px}#msg{color:#c4262e;min-height:18px;font-size:13px}
.box .inf{margin-top:18px;padding-top:12px;border-top:1px solid var(--line);font-size:12px;color:var(--mut);display:grid;grid-template-columns:auto 1fr;gap:3px 12px}
.box .sn{font-size:11px;color:var(--mut);margin-top:12px}
""",
    "lenovo": """
body{background:#16191c}.lg{min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{background:#fff;width:440px;box-shadow:0 12px 50px rgba(0,0,0,.5)}.box .hd{background:#262a2f;color:#fff;padding:18px 26px}
.box .hd .mk{width:34px;height:22px;border:2px solid #c9ced4;display:inline-grid;place-items:center;font-size:9px;font-weight:700;color:#c9ced4;margin-right:10px;vertical-align:middle}
.box .hd h1{display:inline;font-size:18px;font-weight:500;vertical-align:middle}.box .bd{padding:20px 26px 24px}
.box .inf{font-size:12px;color:var(--mut);display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin-bottom:12px}
.box label{display:block;font-size:12.5px;color:var(--mut);margin:11px 0 4px}.box input,.box select{width:100%;padding:8px;border:1px solid #9aa1a8;font:inherit;border-radius:3px}
.box .btn{width:100%;margin-top:20px;padding:9px}#msg{color:#c4262e;min-height:18px;font-size:13px}.box .sn{font-size:11px;color:var(--mut);margin-top:12px}
""",
    "smc": """
body{background:#fff}.lg{min-height:100vh;display:flex;flex-direction:column}
.lg .banner{height:70px}.lg .mid{flex:1;display:flex;align-items:flex-start;justify-content:center;padding-top:9vh}
.box{width:360px;border:1px solid var(--line);background:linear-gradient(#f7f9fc,#e6ebf2)}.box h2{margin:0;padding:8px 12px;font-size:13px;color:#fff;background:linear-gradient(#5e86c4,#3c66a8)}
.box form{padding:16px 22px 20px}.box .row{display:grid;grid-template-columns:90px 1fr;align-items:center;margin:8px 0}
.box input,.box select{padding:5px;border:1px solid #8c97a8;font:inherit;width:100%}.box .btn{margin:14px 0 0 90px}
#msg{color:#c4262e;min-height:16px;font-size:12px}.box .sn{font-size:11px;color:var(--mut);margin-top:12px}
""",
}


def login_html(h, error="", expired=False):
    sk = skin_of(h)
    b, t = h.bmc, texts(h, sk)
    msg = t["wrong"] if error else (t["expired"] if expired else "")
    prod = product(sk, b)
    sim = f"<div class=sn><span class=sim>{t['simShort']}</span> {t['simNote']} ({profile(b)}-Profil)</div>"
    pw = ("<input id=password name=password type=password autocomplete=current-password>")
    lang = ("<select><option>English</option><option>Deutsch</option></select>")
    btn = f"<button id=login-btn type=submit class='btn pri'>{sk['login_btn']}</button>"
    s = sk["shell"]
    if s == "hpe":
        body = f"""<div class=lg><div class=who><div class=mk></div><h1>{prod}</h1>
<div class=m>{b.model}</div><div class=m>{b.hostname or b.dhcp_hostname() or ''}</div></div>
<form id=f><div id=msg>{msg}</div><label>Local User Name</label><input id=username name=username type=text autocomplete=username autofocus>
<label>{t['password']}</label>{pw}<label>Language</label>{lang}{btn}{sim}</form></div>"""
    elif s == "dell":
        body = f"""<div class=lg><form id=f class=box><div class=mk>vB</div><h1>{prod}</h1><div class=ed>{edition(b.sim.lic_name)}</div>
<div id=msg>{msg}</div><label>Username</label><input id=username name=username type=text autocomplete=username autofocus>
<label>Password</label>{pw}<label>Domain</label><select><option>This Controller</option></select>{btn}
<div class=inf><span>Type</span><b>{b.model}</b><span>{t['serial']}</span><b>{b.serial}</b>
<span>Hostname</span><b>{b.hostname or b.dhcp_hostname() or '–'}</b></div>{sim}</form></div>"""
    elif s == "lenovo":
        body = f"""<div class=lg><div class=box><div class=hd><span class=mk>vB</span><h1>{prod}</h1></div><div class=bd>
<div class=inf><span>{t['machineName']}</span><b>{b.hostname or b.dhcp_hostname() or '–'}</b><span>{t['machineType']}</span><b>{b.model}</b>
<span>{t['serial']}</span><b>{b.serial}</b></div>
<form id=f><div id=msg>{msg}</div><label>{t['user']}</label><input id=username name=username type=text autocomplete=username autofocus>
<label>{t['password']}</label>{pw}<label>Language</label>{lang}{btn}</form>{sim}</div></div></div>"""
    else:
        body = f"""<div class=lg><div class=banner><div class=brand><span class=mk>vB</span><span class=p>{prod}</span></div></div>
<div class=mid><div class=box><h2>Please Login</h2><form id=f><div id=msg>{msg}</div>
<div class=row><label>Username</label><input id=username name=username type=text autocomplete=username autofocus></div>
<div class=row><label>Password</label>{pw}</div><div class=row><label>Language</label>{lang}</div>{btn}{sim}</form></div></div></div>"""
    body += "<script>" + LOGIN_JS % (json.dumps(sk["home"]), json.dumps(t["wrong"])) + "</script>"
    return page(f"{prod} – Login", LOGIN_CSS[s], body, sk)


def app_html(h):
    sk = skin_of(h)
    c = cfg(h, sk)
    body = ('<div id=app></div><script>window.VB=' + json.dumps(c).replace("</", "<\\/") + ";</script><script>"
            + APP_JS + "</script>")
    return page(f"{c['product']} – {h.bmc.model}", "", body, sk)


# ---------------------------------------------------------------- Remote-Konsole
CON_CSS = {
    "hpe": (".cbar{background:#f2f3f4;color:#1e2226;border-bottom:1px solid #c9cdd1}.cbar .mi:hover,.cbar .dd.open>.mi{background:#dfe2e5}"
            ".sbar{background:#2a2d30;color:#cfd4da}"),
    "dell": (".cbar{background:#2b2c2e;color:#f2f2f2}.cbar .mi:hover,.cbar .dd.open>.mi{background:#3e4043}"
             ".sbar{background:#1b1c1d;color:#9aa0a6}"),
    "lenovo": (".cbar{background:#262a2f;color:#e6e9ec}.cbar .mi:hover,.cbar .dd.open>.mi{background:#353b42}"
               ".sbar{background:#16191c;color:#9aa1a9}"),
    "smc": (".cbar{background:linear-gradient(#f7f7f4,#e4e3dc);color:#111;border-bottom:1px solid #a9a89f;font-family:Tahoma,Arial,sans-serif}"
            ".cbar .mi:hover,.cbar .dd.open>.mi{background:#cfdcf0}.sbar{background:#e4e3dc;color:#333}"),
}


def console_html(h):
    sk = skin_of(h)
    t = texts(h, sk)
    b = h.bmc
    prod = product(sk, b)
    if not b.sim.licensed:
        body = (f"<div style='max-width:520px;margin:12vh auto;background:#fff;padding:26px 30px;border-top:5px solid var(--acc)'>"
                f"<h2 style='margin:0 0 10px'>{t['needLicenseTitle']}</h2><p>{t['needLicensePage'] % b.sim.lic_name}</p>"
                f"<p class=muted style='font-size:12px'><span class=sim>{t['simShort']}</span> {t['simNote']}</p></div>")
        return page(f"{prod} – {t['consoleTitle']}", "body{background:#e9ebee;font:14px Arial,sans-serif}", body, sk)
    head = """
html,body{background:#0b0d10;color:#cfd6df;overflow:hidden}
.cbar{display:flex;align-items:stretch;font-size:13px;user-select:none;height:30px}
.cbar .mi{display:flex;align-items:center;padding:0 12px;cursor:pointer;background:none;border:0;color:inherit;font:inherit}
.cbar .dd{display:flex}.cbar .ddm{color:#222}.cbar .sp{flex:1}.cbar .ps{display:flex;align-items:center;padding:0 12px;font-size:12px;gap:8px}
#scr{margin:10px auto;width:max-content;max-width:100%;background:#000;border:1px solid #222;padding:10px}
#scr pre{margin:0;font:15px/17px 'DejaVu Sans Mono',Consolas,monospace;color:#d8dde6;white-space:pre;outline:0;min-width:80ch;min-height:425px}
#scr.dark pre{color:#444}.cur{background:#d8dde6;color:#000}
.sbar{position:fixed;left:0;right:0;bottom:0;padding:4px 12px;font-size:11.5px;display:flex;gap:18px}
""" + CON_CSS[sk["shell"]]

    def item(lbl, act):
        return f"<button class=mi data-a='{act}'>{lbl}</button>" if isinstance(act, str) else (
            "<div class=dd><button class=mi data-m=1>" + lbl + " &#9662;</button><div class=ddm>"
            + "".join(f"<button data-a='{a}'>{l}</button>" for l, a in act) + "</div></div>")
    menu = "".join(item(lbl, act) for lbl, act in sk["console_menu"])
    sysid = K["SYS"][b.family]
    body = f"""
<div class=cbar>{menu}<span class=sp></span><span class=ps><span class=sim>{t['simShort']}</span><span id=ps></span></span></div>
<div id=scr tabindex=0><pre id=s></pre></div>
<div class=sbar><span>{prod}</span><span>{b.model} · {b.serial}</span><span id=res></span><span style="margin-left:auto">{t['simNote']}</span></div>
<script>
var scr=document.getElementById('scr'),pre=document.getElementById('s'),ps=document.getElementById('ps');
var SYS='/redfish/v1/Systems/{sysid}',NA={json.dumps(t['na'])};
function draw(d){{scr.className=d.dark?'dark':'';var out=d.lines.slice();
 if(d.cursor){{var r=d.cursor[0],c=d.cursor[1],l=out[r]||'';out[r]=l.slice(0,c).replace(/</g,'&lt;')+'\\u0001'+l.slice(c+1);}}
 pre.innerHTML=out.map(function(l){{return l.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace('\\u0001','<span class=cur> </span>');}}).join('\\n');
 ps.textContent=d.power;document.getElementById('res').textContent=Math.max.apply(null,[80].concat(d.lines.map(function(l){{return l.length;}})))+' x '+d.lines.length;}}
function poll(){{fetch('/vbsim/console/screen',{{credentials:'same-origin'}}).then(function(r){{
 if(r.status===401){{location.replace('/');throw 0;}}return r.json();}}).then(draw).catch(function(){{}});}}
setInterval(poll,700);poll();
function rf(p,m,b){{return fetch(p,{{method:m,credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b)}});}}
function note(m){{var s=document.createElement('div');s.className='toast';s.textContent=m;document.body.appendChild(s);setTimeout(function(){{s.remove();}},3000);}}
function act(a){{
 if(a.indexOf('p:')===0){{if(confirm(a.slice(2)+'?'))rf(SYS+'/Actions/ComputerSystem.Reset','POST',{{ResetType:a.slice(2)}}).then(poll);}}
 else if(a.indexOf('boot:')===0){{rf(SYS,'PATCH',{{Boot:{{BootSourceOverrideTarget:a.slice(5),BootSourceOverrideEnabled:a==='boot:None'?'Disabled':'Once'}}}}).then(function(){{note('Next boot: '+a.slice(5));}});}}
 else if(a==='cad'){{rf(SYS+'/Actions/ComputerSystem.Reset','POST',{{ResetType:'ForceRestart'}}).then(poll);}}
 else if(a==='full'){{(document.documentElement.requestFullscreen||function(){{}}).call(document.documentElement);}}
 else if(a==='refresh'){{poll();}} else if(a==='close'){{window.close();}} else {{note(NA);}}
 scr.focus();}}
document.addEventListener('click',function(e){{
 var m=e.target.closest('[data-m]'),a=e.target.closest('[data-a]');
 [].forEach.call(document.querySelectorAll('.dd.open'),function(d){{if(!m||d!==m.parentNode)d.classList.remove('open');}});
 if(m){{m.parentNode.classList.toggle('open');return;}}if(a)act(a.getAttribute('data-a'));}});
document.addEventListener('keydown',function(e){{
 var k=e.key;if(['F1','F2','F9','F10','F11','F12','Delete','Escape','ArrowUp','ArrowDown','Enter'].indexOf(k)<0)return;
 e.preventDefault();
 fetch('/vbsim/console/key',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{key:k}})}}).then(poll);}});
scr.focus();
</script>"""
    return page(f"{prod} – {t['consoleTitle']}", head, body, sk)


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
