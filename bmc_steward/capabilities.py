"""BMC-Steward: Fähigkeitsmatrix – was BMC-Steward je Hersteller kann.

Eine Tabelle statt verstreuter Sonderfälle: Die Oberfläche graut damit aus, was bei keinem Server der Liste geht,
und nennt die Hersteller, bei denen eine Einstellung übersprungen wird. Die Werte beschreiben, was die Engines in
redfish.py tun (Tests in tests/test_capabilities.py halten beides gleich):

  YES      – eigener, geprüfter Weg für diesen Hersteller
  PARTIAL  – Standard-Redfish über soft(): gesetzt, wenn die Firmware es kann, sonst „nicht unterstützt,
             übersprungen“ (kein Fehler). Ob es geht, zeigt erst der Lauf oder eine Aufnahme (Fähigkeitsbericht).
  NO       – BMC-Steward überspringt es bei diesem Hersteller immer
"""
from .i18n import T

YES, PARTIAL, NO = "yes", "partial", "no"
VENDORS = ("dell", "hpe", "hpe_old", "lenovo", "smc")

# Funktion -> {Hersteller: Zustand}. Fehlt ein Hersteller, gilt YES.
MATRIX = {
    # Neuware: Administrator, Hostname, feste IP (redfish.Engine, iLO 3/4 über RIBCL)
    "setup":    {},
    # UEFI-Einstellungen (BiosEngine; iLO 3 kann es nicht, iLO 4 erst ab Gen9 mit Firmware 2.30)
    "bios":     {"hpe_old": PARTIAL},
    # Dienste (ServicesEngine.run überspringt iLO 3/4 komplett)
    "ntp":      {"hpe_old": NO},
    "timezone": {"hpe": NO, "lenovo": NO, "smc": NO, "hpe_old": NO},          # nur Dell setzt die Zeitzone
    "syslog":   {"lenovo": NO, "smc": NO, "hpe_old": NO},                       # svc_syslog: kein einheitlicher Weg
    "ldap":     {"hpe_old": NO},                                               # DMTF-AccountService.LDAP
    "cert":     {"lenovo": PARTIAL, "smc": PARTIAL, "hpe_old": NO},            # Dell/HPE: eigene Import-Aktion
    "dns":      {"dell": YES, "hpe": PARTIAL, "lenovo": PARTIAL, "smc": PARTIAL, "hpe_old": NO},
    "vlan":     {"dell": PARTIAL, "hpe": PARTIAL, "lenovo": PARTIAL, "smc": PARTIAL, "hpe_old": NO},
    "ipv6":     {"dell": PARTIAL, "hpe": PARTIAL, "lenovo": PARTIAL, "smc": PARTIAL, "hpe_old": NO},
    "nicmode":  {"hpe": NO, "lenovo": NO, "smc": NO, "hpe_old": NO},             # nur Dell-Attribut
    "hard":     {"dell": PARTIAL, "hpe": PARTIAL, "lenovo": PARTIAL, "smc": PARTIAL, "hpe_old": NO},
    "tls_min":  {"hpe": PARTIAL, "lenovo": PARTIAL, "smc": PARTIAL, "hpe_old": NO},   # Dell: iDRAC-Attribut
    # Benutzerkonten (UsersEngine; iLO 3/4 über RIBCL ohne Rollen/Abschalten/SSH-Schlüssel)
    "users":    {},
    "roles":    {"hpe_old": NO},
    "enable":   {"hpe_old": NO},
    "ssh_keys": {"dell": PARTIAL, "hpe": PARTIAL, "lenovo": PARTIAL, "smc": PARTIAL, "hpe_old": NO},
    # Firmware (FirmwareEngine überspringt iLO 3/4)
    "firmware": {"hpe_old": NO},
}


def labels():
    """Anzeigenamen der Funktionen (übersetzt), in der Reihenfolge der Matrix."""
    return {
        "setup": T("Neuware: Administrator, Hostname, feste IP"), "bios": T("UEFI-Einstellungen"),
        "ntp": T("NTP"), "timezone": T("Zeitzone"), "syslog": T("Syslog"), "ldap": T("LDAP"),
        "cert": T("HTTPS-Zertifikat"), "dns": T("DNS"), "vlan": T("VLAN"), "ipv6": T("IPv6"),
        "nicmode": T("Netzwerkport (Shared/Dedicated)"), "hard": T("Härtung"),
        "tls_min": T("TLS-Mindestversion"), "users": T("Konten anlegen, löschen, Passwort"),
        "roles": T("Rolle ändern"), "enable": T("Konto abschalten"), "ssh_keys": T("SSH-Schlüssel"),
        "firmware": T("Firmware-Update"),
    }


def vendor_names():
    return {"dell": "Dell iDRAC", "hpe": "HPE iLO 5/6", "hpe_old": "HPE iLO 3/4", "lenovo": "Lenovo XCC",
            "smc": "Supermicro"}


def state_text(state):
    return {YES: T("ja"), PARTIAL: T("je nach Firmware"), NO: T("nein")}[state]


def support(feature, vendor):
    """Zustand für einen Hersteller; unbekannte Hersteller gelten als PARTIAL (nichts wird ausgegraut)."""
    if vendor not in VENDORS:
        return PARTIAL
    return MATRIX[feature].get(vendor, YES)


def vendor_from_label(text):
    """Herstellerschlüssel aus dem Text der Spalte „Hersteller/Typ“ („HPE (iLO 3/4) (ProLiant DL380 G7)“ …),
    sonst None (noch nicht erkannt, unbekannt, nicht erreichbar)."""
    t = (text or "").lower()
    if "ilo 3/4" in t or "ilo 3" in t or "ilo 4" in t:
        return "hpe_old"
    for key, words in (("dell", ("idrac", "dell")), ("hpe", ("ilo", "hpe")), ("lenovo", ("xcc", "lenovo")),
                       ("smc", ("supermicro",))):
        if any(w in t for w in words):
            return key
    return None


def check(feature, vendors):
    """Für die Hersteller einer Serverliste: (benutzbar, Hinweis).
    benutzbar ist False, wenn kein bekannter Hersteller der Liste die Funktion kann (dann ausgrauen).
    Hinweis nennt die Hersteller, bei denen sie übersprungen wird; leer, wenn alle können oder keiner bekannt ist."""
    known = sorted({v for v in vendors if v in VENDORS})
    if not known:
        return True, ""
    names = vendor_names()
    no = [names[v] for v in known if support(feature, v) == NO]
    if len(no) == len(known):
        return False, T("Bei keinem Server der Liste möglich ({0}).").format(", ".join(no))
    if no:
        return True, T("Wird übersprungen bei: {0}").format(", ".join(no))
    return True, ""


def matrix_html():
    """Die ganze Matrix als HTML-Tabelle (Hilfe/Dialog)."""
    names, labs = vendor_names(), labels()
    mark = {YES: "✔", PARTIAL: "◐", NO: "–"}
    head = "".join(f"<th>{names[v]}</th>" for v in VENDORS)
    rows = []
    for key in MATRIX:
        cells = "".join(f"<td align='center' title='{state_text(support(key, v))}'>{mark[support(key, v)]}</td>"
                        for v in VENDORS)
        rows.append(f"<tr><td>{labs[key]}</td>{cells}</tr>")
    legend = T("✔ ja · ◐ je nach Firmware (wird sonst ohne Fehler übersprungen) · – nein")
    return (f"<table cellspacing='0' cellpadding='4' border='1'><tr><th></th>{head}</tr>{''.join(rows)}</table>"
            f"<p>{legend}</p>")
