"""BMC-Steward: Konstanten, Farben/CSS, Adressvalidierung und Passwort-Maskierung."""
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPixmap)
import ipaddress
import os
import re
from .i18n import T


__author__ = "Björn Franzke"
__version__ = "2.0.5"
__license__ = "MIT"
URL = "https://github.com/daklicker-code/bmc-steward"


WINDOWS = os.name == "nt"




ERR_BG = QColor(217, 83, 79, 150)      # Fehler


MISS_BG = QColor(240, 173, 78, 150)    # Pflichtfeld noch leer


MISS_CSS = "QLineEdit { border: 2px solid #f0ad4e; background: rgba(240, 173, 78, 90); }"


ERR_CSS = "QLineEdit { border: 2px solid #d9534f; background: rgba(217, 83, 79, 80); }"


LIVE_CSS = ("QPushButton { background: #c9302c; color: white; font-weight: bold; padding: 4px 14px; }"
            "QPushButton:disabled { background: #8a5a58; color: #dddddd; }")


ABOUT = (f"<hr><p><small>BMC-Steward {__version__} · © 2026 {__author__} · MIT License · "
         f'<a href="{URL}">{URL}</a></small></p>')


DONE_BG = QColor(63, 164, 91, 90)      # Server fertig konfiguriert


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,16}$")




def ipv4(text):
    """IPv4-Adresse oder None. Führende Nullen und Kurzformen wie 10.1 sind ungültig."""
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        return None




def netmask(text):
    """Netz zu einer Subnetzmaske (255.255.255.0) oder None bei ungültiger Maske."""
    if not ipv4(text):
        return None
    try:
        net = ipaddress.IPv4Network(f"0.0.0.0/{text}")
    except ValueError:
        return None
    return net if 1 <= net.prefixlen <= 30 else None




def row_errors(v):
    """Prüft eine Tabellenzeile, gibt {Spalte: Meldung} zurück ("" = Pflichtfeld leer)."""
    e = {}
    for c in (0, 2, 3, 4, 5):
        if not v[c]:
            e[c] = ""
    if v[0] and not ipv4(v[0]):
        e[0] = T("Keine gültige IPv4-Adresse")
    if "," in v[1]:
        e[1] = T("Kommas im Passwort werden nicht unterstützt")
    new, gw = ipv4(v[2]), ipv4(v[4])
    mask = netmask(v[3])
    if v[2] and not new:
        e[2] = T("Keine gültige IPv4-Adresse")
    if v[3] and not mask:
        e[3] = T("Keine gültige Subnetzmaske (z. B. 255.255.255.0 oder /24)")
    if v[4] and not gw:
        e[4] = T("Keine gültige IPv4-Adresse")
    if new and mask:
        net = ipaddress.IPv4Network(f"{new}/{mask.prefixlen}", strict=False)
        if new in (net.network_address, net.broadcast_address):
            e[2] = T('Netz- oder Broadcast-Adresse von {0}').format(net)
        if gw and gw not in net:
            e[4] = T('Gateway liegt nicht im Netz der neuen IP ({0})').format(net)
        elif gw and gw in (net.network_address, net.broadcast_address):
            e[4] = T('Netz- oder Broadcast-Adresse von {0}').format(net)
    if new and gw and new == gw:
        e[4] = T("Gateway und neue IP sind identisch")
    if v[5] and not HOST_RE.match(v[5]):
        e[5] = T("Nur Buchstaben, Ziffern und Bindestrich (nicht am Anfang/Ende), höchstens 63 Zeichen")
    return e




def mask(text):
    """Passwörter kommen nie ins Protokoll: nur Länge und Form (A Großbuchstabe, a Kleinbuchstabe, 9 Ziffer, # Rest)."""
    return f"{len(text)} Z. " + "".join("A" if c.isupper() else "a" if c.islower() else "9" if c.isdigit() else "#"
                                       for c in text[:40])
