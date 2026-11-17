"""BMC-Steward: Passwörter optional im System-Schlüsselbund statt im Klartext in der Serverliste/CSV.

Nutzt das Paket `keyring` (Secret Service/libsecret unter Linux, Windows Credential Manager, macOS
Schlüsselbundverwaltung) - ein sehr dünner Wrapper, keine eigene Kryptografie. Ist `keyring` nicht
installiert, oder ist unter Linux kein Secret-Service-Dienst erreichbar (z. B. Headless-Betrieb ohne
D-Bus-Sitzung), liefert `available()` False: alles bleibt dann beim bisherigen Verhalten (Passwörter nur
in der Tabelle/CSV), nichts bricht und nichts wird stillschweigend woanders abgelegt.

Schlüssel je Server: die Seriennummer, wenn bekannt (überlebt einen IP-Wechsel durch DHCP), sonst die
aktuelle IP. Das ist – wie die ganze Serverliste – kein kryptografisch stabiler Geräte-Fingerabdruck wie
bei trust.py (dort zählt die Redfish-UUID); zwei Geräte mit zufällig derselben Seriennummer oder IP (z. B.
nach einem RMA, oder dieselbe IP in zwei verschiedenen Netzen zu verschiedenen Zeiten) teilen sich dasselbe
Schlüsselbund-Feld. Praktisch unkritisch, weil ein „falscher" Treffer nur ein falsches Passwort einträgt,
das dann beim nächsten Verbindungsversuch ganz normal als Anmeldefehler auffällt."""
import logging

SERVICE = "bmc-steward"
LOG = logging.getLogger("bmc_steward.vault")

try:
    import keyring
    import keyring.backends.fail
    import keyring.errors
except ImportError:                 # Paket fehlt: Schlüsselbund einfach nicht anbieten
    keyring = None


def available():
    """True, wenn ein echtes Schlüsselbund-Backend da ist (nicht nur die eingebaute Attrappe, die
    `keyring` benutzt, wenn es keinen brauchbaren Dienst findet)."""
    if keyring is None:
        return False
    try:
        return not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)
    except Exception:
        return False


def key_for(serial, ip):
    """Schlüsselbund-Eintragsname für einen Server: Seriennummer, wenn bekannt, sonst Aktuelle IP."""
    serial = (serial or "").strip()
    return "serial:" + serial if serial else "ip:" + (ip or "").strip()


def store(serial, ip, password):
    """Legt das Passwort im Schlüsselbund ab (oder löscht den Eintrag, wenn password leer ist).
    True = geklappt, False = kein Schlüsselbund verfügbar oder Fehler (siehe Protokoll)."""
    if not available():
        return False
    key = key_for(serial, ip)
    if not key.split(":", 1)[1]:    # weder Seriennummer noch IP -> kein sinnvoller Schlüssel
        return False
    try:
        if password:
            keyring.set_password(SERVICE, key, password)
        else:
            delete(serial, ip)
        return True
    except keyring.errors.KeyringError as e:
        LOG.warning("Schlüsselbund: Speichern von %s fehlgeschlagen: %r", key, e)
        return False


def fetch(serial, ip):
    """Gespeichertes Passwort, oder "" wenn keins hinterlegt ist oder kein Schlüsselbund verfügbar ist."""
    if not available():
        return ""
    key = key_for(serial, ip)
    if not key.split(":", 1)[1]:
        return ""
    try:
        return keyring.get_password(SERVICE, key) or ""
    except keyring.errors.KeyringError as e:
        LOG.warning("Schlüsselbund: Lesen von %s fehlgeschlagen: %r", key, e)
        return ""


def delete(serial, ip):
    """Entfernt einen Eintrag, falls vorhanden. True = entfernt oder war ohnehin nicht da."""
    if not available():
        return False
    key = key_for(serial, ip)
    try:
        keyring.delete_password(SERVICE, key)
    except keyring.errors.PasswordDeleteError:
        pass          # gab keinen Eintrag - kein Fehler
    except keyring.errors.KeyringError as e:
        LOG.warning("Schlüsselbund: Löschen von %s fehlgeschlagen: %r", key, e)
        return False
    return True
