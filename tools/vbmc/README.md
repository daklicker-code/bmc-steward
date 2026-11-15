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
* Nur für `--console` zusätzlich: `paramiko` und `pyghmi` sowie erhöhte Rechte,
  weil BMC-Steward die Standardports 22/623/443 anspricht. Da `--console` mit
  `sudo` läuft, müssen beide Pakete für **root** sichtbar sein (systemweit):
  * `paramiko` ist in den Repos: `sudo pacman -S python-paramiko`
  * `pyghmi` liegt auf Arch/CachyOS im AUR: `yay -S python-pyghmi`
    (oder ohne AUR-Helfer `sudo pip install --break-system-packages pyghmi`)
  * Prüfen: `sudo python3 -c "import paramiko, pyghmi; print('ok')"`

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

## Netzlabor: Konfigraum und Router (echte Netzwerkpräsenz)

Mit `--netlab` bekommt jeder virtuelle Server einen eigenen **Netzwerk-Namespace** mit eigener
Netzwerkkarte `eth0`, einer **MAC mit Hersteller-Kennung** (aus der Seriennummer abgeleitet, also
reproduzierbar), einem eigenen **DHCP-Client** (meldet sich wie das echte Gerät, z. B. `idrac-ABC1234`,
`ILO…`) und den **Standardports 443/22/623**. Beim Umkonfigurieren bekommt seine Karte wirklich die
neue Adresse, Maske und Gateway - der alte Lease wird freigegeben.

| Modus | Aufbau | Adresse ab Werk |
|---|---|---|
| `--netlab rz` | virtuelle Bridge `bmclab0` = Staging-Switch, dieser PC mit `--host-addr` (z. B. `192.168.0.1/24`, ohne Gateway) | DHCP über den DHCP-Reiter von BMC-Steward (Karte `bmclab0`) oder feste Werks-IP |
| `--netlab router --parent enp3s0` | macvlan an der **Kabel**-Netzwerkkarte, dieser PC erreicht die Server über den Labor-Anschluss `bmcshim` (eigene Adresse vom Router, /32-Routen je Server) | DHCP vom Router (FritzBox o. ä.); die Server erscheinen dort als eigene Geräte |

Braucht root (`ip netns`, Bridge, macvlan) und `iproute2`. Beim Beenden (Strg-C, SIGTERM oder
`quit` auf stdin) werden Leases freigegeben und alle Namespaces/Geräte entfernt; Reste eines
Absturzes räumt der nächste Start weg. WLAN geht im Router-Modus nicht (ein WLAN-Router nimmt von
einem Client keine zusätzlichen MAC-Adressen an).

```bash
sudo python3 vbmc.py --netlab rz --config szenarien/rz-gemischt.json
sudo python3 vbmc.py --netlab router --parent enp3s0 --config szenarien/router-zuhause.json
```

### Szenarien

Ein Szenario ist eine JSON-Datei (der Testlabor-Reiter liest und schreibt dasselbe Format):

```json
{"mode": "rz", "host_addrs": "192.168.0.1/24", "servers": [
  {"vendor": "dell", "gen": "idrac9", "serial": "CN7RZ01", "password": "Dell4711Rz"},
  {"vendor": "dell", "serial": "CN7FIX1", "net": "static", "ip": "192.168.0.120"},
  {"vendor": "dell", "gen": "idrac8_alt", "serial": "CN7OLD1"},
  {"vendor": "dell", "serial": "CN7SLOT", "busy_slots": {"3": "operator"}},
  {"vendor": "hpe", "gen": "ilo4", "serial": "CZILO400004"}
]}
```

* `gen`: Dell `idrac9`/`idrac10`/`idrac8`/`idrac8_alt` (Firmware < 2.50.50.50, keine Attribute-API),
  HPE `ilo6`/`ilo5`/`ilo4`/`ilo3` (iLO 3/4 über RIBCL, iLO 3 ohne Redfish und mit TLS 1.0, sofern
  OpenSSL es noch zulässt), Lenovo `xcc3`/`xcc2`/`xcc1`, Supermicro `x13`/`x12`/`x11`.
* `net`/`ip`/`mask`/`gw`: Werkszustand (`dhcp` oder `static`); nur im Netzlabor wirksam.
* `busy_slots`: Dell-User-Slots, die schon ein anderer Benutzer belegt.
* `user`/`password`, `model`, `serial`, `mac`: Werks-Login, Typenschild, Kennung.

Beispiele in `szenarien/`: gemischte Konfigraum-Flotte, zwei Dells mit derselben festen Werks-IP,
Sonderfälle (alte Firmware, belegter Slot, iLO 3/4), Heimnetz am Router, Loopback-Schnelltest.

Für den Testlabor-Reiter gibt es zusätzlich `--state` (Zustand als `@@STATE`-JSON-Zeilen) und
`--control` (Befehle über stdin: `link <nr> up|down`, `state`, `quit`).

## Hinweise

* Jeder IPMI-BMC läuft als **eigener Prozess** (pyghmi verträgt mehrere Server in
  einem Prozess nicht zuverlässig). Daher sauber mit **Strg-C** beenden, nicht den
  Elternprozess hart killen – sonst bleiben IPMI-Kindprozesse auf Port 623 liegen.
* Ohne Netzlabor bleiben SSH/IPMI bei einer Redfish-IP-Migration auf der **Startadresse**
  (pyghmis globaler Zustand lässt kein sauberes Umziehen des IPMI-Sockets zu).
  Für den Konsolen-Test also die Startadresse des Servers verwenden. Im Netzlabor ziehen
  SSH/IPMI mit um, und neu angelegte Benutzer gelten auch für IPMI.
* Nicht nachgebildet: die eingebettete HTML5-Web-Konsole des BMC (nur die reine
  Landing-Seite auf 443 antwortet).

## Lizenz

MIT, wie das Hauptprojekt.
