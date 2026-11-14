# vbmc – virtuelle BMC-Flotte zum Testen von BMC-Steward

`vbmc.py` erzeugt und startet beliebig viele **virtuelle** Server der Hersteller
Dell (iDRAC), HPE (iLO), Lenovo (XCC) und Supermicro. Jeder virtuelle Server
verhält sich nach außen wie ein echter BMC und dient dazu, BMC-Steward zu testen
und vorzuführen – ohne echte Hardware.

Es ist ein eigenständiges Entwickler-/Test-Werkzeug und nicht Teil der Anwendung.

> ⚠️ Reines Labor-Werkzeug. Passwörter liegen im Klartext im Speicher. Niemals mit
> echten Zugangsdaten oder in einem produktiven Netz betreiben.

## Was nachgebildet wird

* **Redfish über HTTPS** – Herstellererkennung (`/redfish/v1/`, bei iLO 3/4 über
  `/xmldata` + RIBCL), Benutzer anlegen/ändern, Hostname, DHCP aus + statische IP,
  UEFI/BIOS-Attribute lesen/schreiben, NTP/Syslog/LDAP/Zertifikat, Power/Reset.
* **Echter Adresswechsel** – beim Umkonfigurieren zieht der Server tatsächlich auf
  die neue IP um, sodass Kontroll- und Inventarphase von BMC-Steward durchlaufen.
* **Echtes SSH und IPMI-Serial-over-LAN** für die UEFI-/Text-Konsole (Modus
  `--console`): SSH (paramiko) für Dell/HPE/Lenovo, IPMI-SOL (pyghmi) für
  Supermicro – genau die Zuordnung, die BMC-Steward automatisch wählt.

## Voraussetzungen

* Python 3.9+ und `openssl` (für das selbstsignierte Zertifikat; wird einmalig
  erzeugt und unter `~/.cache/vbmc/` abgelegt).
* Nur für `--console` zusätzlich: `paramiko` und `pyghmi`
  (`sudo pacman -S python-paramiko python-pyghmi` bzw. `pip install paramiko pyghmi`)
  sowie erhöhte Rechte, weil BMC-Steward die Standardports 22/623/443 anspricht.

Der Grundbetrieb (nur Redfish) braucht keine Zusatzpakete und keine root-Rechte:
Die Server laufen auf `127.x`-Adressen mit hohem Port (Vorgabe **8443**).

## Benutzung

```bash
python3 vbmc.py --preset                 # je ein Server pro Hersteller (Port 8443)
python3 vbmc.py dell hpe lenovo smc        # eigene Auswahl, Adressen automatisch
python3 vbmc.py dell@127.9.0.11 hpe@127.9.0.12:8443
python3 vbmc.py --vendor dell --count 5    # 5 Dell-iDRACs
python3 vbmc.py --config fleet.example.json
python3 vbmc.py --preset --interactive     # Steuerkonsole: add / list / rm / quit
sudo python3 vbmc.py --preset --console    # zusätzlich SSH (22), IPMI (623), Redfish (443)
```

Beim Start wird eine Tabelle mit Adressen und Werks-Logins ausgegeben, die man so
in BMC-Steward einträgt.

### Eintragen in BMC-Steward

* **Haupt-Reiter (Konfiguration + Migration):** Adresse als `IP:PORT`, z. B.
  `127.9.0.11:8443`.
* **Reiter „UEFI-Konsole“ (nur mit `--console`):** die **reine IP ohne Port**,
  z. B. `127.9.0.11` (SSH/IPMI/Redfish liegen dort auf den Standardports 22/623/443).

## Hinweise

* Jeder IPMI-BMC läuft als **eigener Prozess** (pyghmi verträgt mehrere Server in
  einem Prozess nicht zuverlässig). Daher sauber mit **Strg-C** beenden, nicht den
  Elternprozess hart killen – sonst bleiben IPMI-Kindprozesse auf Port 623 liegen.
* SSH/IPMI bleiben bei einer Redfish-IP-Migration auf der **Startadresse**
  (pyghmis globaler Zustand lässt kein sauberes Umziehen des IPMI-Sockets zu).
  Für den Konsolen-Test also die Startadresse des Servers verwenden.
* Nicht nachgebildet: die eingebettete HTML5-Web-Konsole des BMC (nur die reine
  Landing-Seite auf 443 antwortet).

## Lizenz

MIT, wie das Hauptprojekt.
