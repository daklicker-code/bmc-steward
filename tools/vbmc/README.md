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
* `license`: `"none"` startet den Server ohne Lizenz (Virtual Media und Konsole gesperrt), sonst voll lizenziert.
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
* Power per IPMI (`chassis power status|on|off|soft|cycle|reset`) fragt über eine Pipe den Hauptprozess und
  zeigt denselben Zustand wie Redfish und Weboberfläche. Dieser Weg braucht `pyghmi` (siehe oben); ohne
  `pyghmi` ist er nicht automatisch testbar (die Pipe-Anbindung ist es, siehe `tests/test_vbmc.py`).

## Server-Simulation und Weboberflächen (ab 1.4)

Jeder virtuelle Server verhält sich wie ein laufendes Gerät, nicht nur wie eine Redfish-Attrappe
(Paket `vbsim/`):

* **Zustandsautomat** (`vbsim/state.py`): Aus → Einschalten → POST (mit Phasen) → Betriebssystemstart → Läuft;
  Herunterfahren dauert, ein Neustart fährt herunter und wieder hoch. Alle Redfish-`ResetType`-Werte
  (`On`, `ForceOff`, `GracefulShutdown`, `GracefulRestart`, `ForceRestart`, `PushPowerButton`, `PowerCycle`,
  `Nmi`). Unzulässige Aktionen (z. B. Einschalten, obwohl an) liefern wie echte BMCs HTTP 400.
* **Ereignisprotokoll** im Wortlaut des Herstellers, Sensoren (Temperaturen, Lüfter, Netzteile, Leistung),
  die vom Power-Zustand abhängen, Hardware-Inventar (CPU, DIMMs, Laufwerke, Netzwerkkarten).
* **Weboberfläche je Hersteller** (`vbsim/webui/`): Anmeldung über eine Redfish-Session (Cookie
  `sessionKey`/`X-Auth-Token`), danach Übersicht, Systeminformationen, Power-Steuerung, Temperaturen/Lüfter,
  Ereignisprotokoll, Netzwerk, Benutzer, Firmware, BIOS. Layout, Menüs und Begriffe unterscheiden sich je
  Hersteller (iLO: Menü links; iDRAC und Supermicro: Reiter oben; XCC: dunkles Menü links). Die Seiten
  lesen dieselben Redfish-Endpunkte, die auch BMC-Steward benutzt; was das Tool ändert, ist sofort sichtbar.
* **Remote-Konsole** (`vbsim/console.py`): Text-Bildschirm je Zustand (kein Signal, POST mit
  Fortschritt, Kernelmeldungen, Login-Prompt). Im POST öffnet die herstellertypische Taste (iLO F9, iDRAC F2,
  XCC F1, Supermicro Entf) ein bedienbares Setup; Änderungen landen in den BIOS-Attributen.
* Zeitverhalten über `VBMC_TIME_SCALE` (Standard 1; 0.2 = fünfmal schneller, 0 = sofort).
* Alle Seiten tragen den Hinweis „Simulierter Server“; es werden keine Hersteller-Logos nachgebaut.

Ab 1.7: **herstellernahe Oberflächen.** Jeder Skin folgt dem Bedienkonzept des Originals:

| Profil | Rahmen | Startseite | Konsole |
|---|---|---|---|
| Dell (iDRAC9-Stil) | dunkle Kopfleiste, Menü oben mit Aufklappern, große Seitentitel mit Reitern | Dashboard: Systemzustand je Komponente, Systeminformationen, Konsolenvorschau, letzte Protokolleinträge, Power-Aufklapper, „Identify System“ | Menü Boot/Power/Keyboard/… (Boot setzt den einmaligen Start) |
| HPE (iLO-5/6-Stil) | helle Navigation links mit Symbolen und Konsolen-Miniatur, Titel „Gruppe - Seite“, Reiter | Information/Status in zwei Spalten, Symbole für Power/Zustand/UID oben rechts | Power Switch/Virtual Drives/Keyboard |
| Lenovo (XCC2-Stil) | dunkle Kopfleiste, dunkle Navigation links mit Untermenüs | Zustandskacheln + aktive Ereignisse, Systeminformationen, Schnellaktionen, Konsolenvorschau, Leistungsbalken | Screen Capture/Keyboard/Power/… |
| Supermicro (klassisch) | Banner, blaue Menüleiste, graue Untermenü-Spalte, blaue Titel mit Beschreibung | Summary, Konsolenvorschau, „Power Control via IPMI“ | Virtual Media/Macro/Options/Power Control/Exit |

Ab 1.8 je Generation eigene Varianten (Auswahl automatisch über die Generation des Servers):

| Generation | Rahmen |
|---|---|
| Dell iDRAC8 | Baum-Navigation links (Server / Controller Settings / Hardware / Storage), Reiter als Karteikarten, graublaue Verläufe |
| HPE iLO 4 (auch iLO 3) | graue Abschnittsbalken links mit Unterpunkten, dunkle Titelleiste, blaue Reiter |
| Lenovo XCC1 | wie XCC2, aber helle Navigation und flache Kästen |
| Supermicro X13/H13 | neue Oberfläche: dunkelblaue Navigation links, weiße Kopfleiste, abgerundete Kacheln |

iLO 3 hat im Simulator kein Redfish; die Anmeldeseite erscheint, die Verwaltungsoberfläche bleibt dort leer.

Rechtliche Leitplanken (in `vbsim/webui/engine.py` dokumentiert, beim Weiterbauen beibehalten): kein Logo,
keine Wortmarke, keine Hersteller-Schrift, -Grafik oder -Icons (alle Symbole sind eigene SVG-Formen); kein
Code/CSS aus Hersteller-Firmware; Seitentitel sind Gattungsbegriffe („Remote Access Controller“,
„Lights-Out Management“, „Systems Management Controller“, „Baseboard Management Controller“); Markennamen
erscheinen nur beschreibend im Simulationshinweis („Dell iDRAC9-Profil“); Farben angelehnt, nicht die exakten
Markenfarben; jede Seite inklusive Anmeldung und Konsole zeigt „SIMULATION“. Die Redfish-Daten (Modell,
Manager-Modell, Lizenzname) bleiben wie gehabt, weil BMC-Steward sie zur Erkennung braucht.

Ab 1.5 zusätzlich:

* **Virtual Media** (Redfish `VirtualMedia`, `InsertMedia`/`EjectMedia`, bei iLO auch PATCH mit
  `BootOnNextServerReset`; Seite „Virtual Media“ je Oberfläche). Geräte je Hersteller wie beim Original
  (iLO `1`/`2`, iDRAC `RemovableDisk`/`CD`, XCC `EXT1`/`EXT2`, Supermicro `CD1`/`USB1`). Es wird nichts
  heruntergeladen; geprüft werden URI-Schema und Endung (`.iso`, bei USB auch `.img`).
* **Einmaliger Start** (`Boot.BootSourceOverrideTarget`/`-Enabled`): `Cd` bootet vom eingelegten Image (die Konsole
  zeigt das Installationsmenü), `Pxe` zeigt einen fehlgeschlagenen Netzwerkstart, `BiosSetup` hält den
  Start im BIOS-Setup an, bis man es in der Konsole mit Esc verlässt. Genau das nutzt „Ins UEFI-Setup booten“
  im Tool. `Once` wird beim Start verbraucht.
* **Lizenzen** (Redfish `LicenseService`, Seite „Lizenz“): Ohne Lizenz (iLO Standard, iDRAC Basic, XCC Standard,
  Supermicro ohne Schlüssel) sind Virtual Media und Remote-Konsole gesperrt (HTTP 403 bzw. Hinweisseite), wie bei
  den Originalen. Ein Schlüssel im Format `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX` schaltet die Standard-Vollstufe frei,
  angezeigt wird er maskiert. Start ohne Lizenz: `"license": "none"` im Eintrag der Konfiguration.
* **RIBCL-Power für iLO 3/4:** `GET_HOST_POWER_STATUS`, `SET_HOST_POWER`, `PRESS_PWR_BTN`, `HOLD_PWR_BTN`,
  `COLD_BOOT_SERVER`, `WARM_BOOT_SERVER`, `RESET_SERVER`, dazu `GET_SERVER_NAME` und `GET_EVENT_LOG`.
* Der Browser-Test `tests/test_vbmc_ui.py` bedient alle Oberflächen in einem echten Chrome (Playwright);
  er wird übersprungen, wenn beides fehlt.

### Neu in 1.6.1

- **Seriennummer nach „Neuware“:** „In Serverliste übernehmen“ trägt die Seriennummer des Testlabor-Servers mit ein (auch in schon vorhandene Zeilen ohne Seriennummer). iLO 3/4 liefert sie wie echte Firmware als `<SBSN>` in `/xmldata`.

### Neu in 1.6

- **SSDP-Erkennung:** Jeder virtuelle Server beantwortet `M-SEARCH` (`ssdp:all`, `upnp:rootdevice`, `urn:dmtf-org:service:redfish-rest:1`) mit seiner Redfish-Adresse (iLO 3: `/xmldata?item=all`). Im Netzlabor auf der eigenen Karte inklusive Multicast, sonst unicast auf der Server-Adresse. Port per `VBMC_SSDP_PORT` (Standard 1900, `0` schaltet SSDP ab).
- **Zertifikatsseiten:** Seite „SSL-Zertifikat" in allen Herstellerskins: Zertifikat anzeigen, CSR erzeugen, PEM einspielen. Redfish: `CertificateService` (`GenerateCSR`, `ReplaceCertificate`), `.../NetworkProtocol/HTTPS/Certificates/1` und die Hersteller-Importwege, die BMC-Steward nutzt (Dell `ImportSSLCertificate`, HPE `ImportCertificate`, Lenovo `ImportServerCertificate`). Ungültiges PEM wird mit 400 abgelehnt, ein Eintrag im Ereignisprotokoll entsteht. Braucht `openssl` im Pfad.
- **Testlabor-Reiter:** Neue Spalte „Lizenz" (Lizenziert / Ohne Lizenz) pro Server.

Noch nicht nachgebildet: Inhalt der Virtual-Media-Images, IPMI-Bootgerät. Das importierte Zertifikat wird nur gespeichert und angezeigt; der TLS-Handshake nutzt weiter das Laborzertifikat.

## Lizenz

MIT, wie das Hauptprojekt.
