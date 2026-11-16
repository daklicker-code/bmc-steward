<h1><img src="docs/logo.svg" height="52" align="absmiddle" alt="">&nbsp;BMC-Steward</h1>

*English version: [README.md](README.md)*

Der BMC-Steward ist ein kleines Desktop-Tool für die Erstkonfiguration von Server-BMCs. Er bedient jeden
Server der Reihe nach: Er legt einen neuen Administrator an, setzt den Hostnamen und stellt den BMC von
der Werks- bzw. DHCP-Adresse auf eine feste Adresse um.

Unterstützt werden **Dell iDRAC**, **HPE iLO**, **Supermicro** und **Lenovo XCC** über Redfish. Ältere
HPE-Server mit **iLO 3 und iLO 4** laufen über die XML-Schnittstelle RIBCL, ältere Dell-Server mit
**iDRAC7/8** über Redfish (ab Firmware 2.50.50.50).

![BMC-Steward](docs/screenshot.png)

<details>
<summary>Screenshots aller Seiten</summary>

| | |
|---|---|
| **DHCP-Server** | **Neuware** (oben) |
| ![DHCP-Server](docs/screenshot-1-dhcp.png) | |
| **Bestandsserver** (Verbindungstest) | **UEFI-Einstellungen** |
| ![Bestandsserver](docs/screenshot-2b-existing.png) | ![UEFI-Einstellungen](docs/screenshot-3-uefi.png) |
| **Dienste** | **UEFI-Konsole** |
| ![Dienste](docs/screenshot-4-services.png) | ![UEFI-Konsole](docs/screenshot-5-console.png) |
| **Hilfe** | |
| ![Hilfe](docs/screenshot-6-help.png) | |

</details>

## Was BMC-Steward kann

Seit Version 3.0 steht links statt der Reiterleiste ein **Menü**; die gewählte Seite öffnet sich rechts daneben. Man arbeitet die Punkte der Reihe nach ab oder nutzt nur, was man braucht. **DHCP-Server** steht als dunkelgelber Knopf an erster Stelle, weil man dort beginnt.

**Monitor links oben:** Solange keine Konsole verbunden ist, zeigt er dezentes Schwarz-Weiß-Rauschen, in dem sich alle acht Sekunden das Logo – eine Kugel mit Netzwerk – aufbaut, beim Stern kurz grün leuchtet und wieder abbaut. Mit einer Verbindung erscheint die Server-Konsole dort als Miniatur; ein Klick darauf öffnet sie direkt in voller Größe. Der Schalter unter dem Monitor wählt die Konsolen-Variante: **HTML5** (eingebettet), **Text** (SOL/SSH) oder **Browser**. Eine verdeckte Web-Ansicht kann nicht neu gezeichnet werden, dann bleibt im Monitor das letzte Bild als Standbild stehen. In der UEFI-Konsole füllt der Knopf **Vollbild** den ganzen Bildschirm (die Textkonsole vergrößert dazu ihre Schrift); beendet wird er mit „Vollbild beenden“ in der Leiste oben, denn Esc und F11 gehören dem Server.

| Menüpunkt | Funktion |
|---|---|
| **DHCP-Server** | Vergibt in einem getrennten Staging-Netz Adressen an frische BMCs, damit das Tool sie überhaupt erreicht. Unter Linux über dnsmasq, unter Windows eingebaut. Gefundene Geräte wandern von selbst in die Serverliste (Haken „Automatisch übernehmen“, voreingestellt, abschaltbar) oder per Klick. |
| **Neuware** | Das Kernstück für frische Server: legt den neuen Administrator an, setzt den Hostnamen und stellt den BMC auf eine feste IP (Adresse, Subnetzmaske, Gateway) um. Server lassen sich nacheinander abarbeiten; fertige Zeilen werden grün und beim nächsten Lauf übersprungen. |
| **↳ Handy-Scanner** | Öffnet aus „Neuware“ eine kameragestützte Eingabe: Das Handy (im selben Netz oder per USB-Tethering/Hotspot verbunden) scannt Barcode oder Text (OCR) vom Geräteaufkleber, der Wert landet direkt im Passwortfeld. Es wird kein Foto auf dem Handy gespeichert, die Verbindung läuft verschlüsselt über einen geheimen Link. |
| **Bestandsserver** | Für bereits konfigurierte BMCs: Verbindung nur lesend testen (Port, Verschlüsselung, Hersteller, Anmeldung, Modell, Firmware, Administratorrechte). Ein Anmeldeversuch pro Gerät, damit kein Konto gesperrt wird. Erfolgreiche Server gehen in „Neuware“, „UEFI-Einstellungen“ und „Dienste“ über. |
| **UEFI-Einstellungen** | Liest die tatsächlich vorhandenen UEFI/BIOS-Attribute von einem Referenzgerät und setzt gewünschte Werte auf allen Servern der Liste. Passwort-, TPM- und Löschattribute sind gesperrt. |
| **Dienste** | Richtet herstellerübergreifend Verwaltungsdienste ein: NTP, Syslog, LDAP/AD mit Gruppen-zu-Rollen-Zuordnung, **Härtung** (IPMI über LAN, SSDP, SNMP aus; Kontosperre; Session-Zeitlimit) und – experimentell – HTTPS-Zertifikat-Import. |
| **Firmware** | Firmware-Updates über den Redfish-UpdateService: liest das Firmware-Inventar eines Referenzgeräts und spielt dann eine lokale Datei (Multipart-/HTTP-Push) oder eine Datei von einer URL (SimpleUpdate) auf alle Server der Liste – mit Live-Fortschritt und Verifikation. Trockenlauf ist Vorgabe; scharfe Läufe verlangen das Eintippen der Serverzahl. |
| **UEFI-Konsole** | Zeigt das UEFI/BIOS eines Servers über den BMC – als eingebettete HTML5-Konsole, Textkonsole (SOL/SSH) oder im Standardbrowser. |
| **Testlabor** | Stellt einen Cluster virtueller Dell/HPE/Lenovo/Supermicro-BMCs zusammen (`tools/vbmc/`) – je Server Firmware-Generation, Werks-Login, Werks-IP oder DHCP, Modell/Seriennummer, belegter Dell-Slot. Drei Netze: Loopback (ohne Root), **Konfigraum** (virtueller Staging-Switch, DHCP aus „DHCP-Server“) und **Router** (die Server hängen im echten LAN und erscheinen im Router). Szenarien lassen sich speichern und genau so wieder laden. |
| **Hilfe** | Erklärt jeden Schritt und die Besonderheiten je Hersteller, in der gewählten Sprache. |

Dazu kommt:

- **Trockenlauf** zeigt jeden Aufruf, ohne etwas zu ändern (voreingestellt).
- **Profile** merken sich wiederkehrende Einstellungen (nie Passwörter, nie die Serverliste).
- **CSV-Import/-Export** der Serverliste, auch direkt aus einem Excel-Export.
- **Mehrsprachig:** Deutsch, Englisch, Französisch, Schweizerdeutsch (Auswahl links unten unter dem Menü).
- **Virtuelle Testflotte** (`tools/vbmc/`): virtuelle BMCs aller vier Hersteller – Redfish mit echtem Adresswechsel, dazu echtes SSH und IPMI-SOL für die Konsole. Steuerbar aus der Seite „Testlabor“ oder eigenständig über die Kommandozeile.
- **Modular aufgebaut** (Paket `bmc_steward/`); entwickelt unter Linux, läuft aus dem Quellcode auch unter Windows (die fertige .exe ist archiviert und wird nicht mehr gepflegt).

## Schutz vor Fehlbedienung

- Der Trockenlauf ist voreingestellt; ein scharfer Lauf zeigt vorher, was passiert, und „Abbrechen“ ist
  vorausgewählt
- Fehlende Einträge sind gelb markiert, fehlerhafte rot, fertige grün
- Doppelte oder schon vergebene IP-Adressen werden abgelehnt, bevor etwas geändert wird
- Ein belegter Dell-User-Slot wird nie überschrieben
- Der DHCP-Server startet nicht in einem Netz, das nach Produktivnetz aussieht (Standardroute, per DHCP
  bezogene Adresse, öffentliche Adresse, ein anderer DHCP-Server antwortet)
- UEFI-Attribute für Passwörter, TPM und Löschfunktionen sind gesperrt
- Passwörter bleiben im Arbeitsspeicher, solange man sie nicht ausdrücklich speichert
- **Zertifikats-Vertrauen:** Das Zertifikat jedes BMC wird beim ersten Kontakt gemerkt; zeigt dasselbe Gerät
  später ein anderes, wird es gesperrt, bevor ein Passwort gesendet wird (Knopf „Zertifikate …“)
- **Anmeldung per Redfish-Session:** Das Passwort geht je Gerät einmal über die Leitung, am Ende wird abgemeldet;
  eine abgelehnte Anmeldung wird nie wiederholt (keine Kontosperre durch das Tool)
- **Sicherung vor jeder Änderung** (JSON je Gerät) und **Revisionsprotokoll** aller ändernden Aufrufe scharfer
  Läufe (Knopf „Sicherungen/Protokolle“)
- **Werkskonto wird gesperrt:** Nach erfolgreicher Kontrolle meldet sich das Tool mit dem neuen Administrator
  unter der neuen Adresse an und sperrt das Werkskonto (root, Administrator, ADMIN, USERID). Kann ein BMC Konten
  nicht abschalten, bekommt es ein nirgends gespeichertes Zufallspasswort. Abschaltbar über den Haken
  „Werkskonto danach sperren“
- **Firmware:** SHA-256-Prüfung gegen den Sollwert des Herstellers, die geprüfte Datei wird für alle Server
  verwendet; „Bei Fehler anhalten“ ist voreingestellt
- **Eigenschutz:** UEFI-Änderungen, Firmware-Updates und „Ins UEFI-Setup booten“ prüfen vorher die Identität
  des Servers. Der eigene Rechner und alle Server der Schutzliste (Knopf „Eigenschutz“ links unten) werden nie
  neu gestartet oder geflasht; ist die Identität im scharfen Lauf nicht lesbar, wird der Server übersprungen

## Voraussetzungen

- Python 3.9 oder neuer
- PyQt6
- Nur Linux: `dnsmasq` und polkit (`pkexec`) für den DHCP-Server
- Optional für die UEFI-Konsole: PyQt6-WebEngine (eingebettete Konsole), `pyte` + `paramiko` + `pyghmi` (Textkonsole, Linux und Windows)

## Installieren und starten

Voraussetzung ist Python 3.9 oder neuer. Die Oberfläche braucht PyQt6; der DHCP-Server und die
optionalen Teile (Handy-Scanner, UEFI-Konsole) haben eigene Zusatzprogramme.

### Arch Linux / CachyOS

```bash
# Grundinstallation
sudo pacman -S python-pyqt6 dnsmasq git

# Code holen und starten
git clone https://github.com/daklicker-code/bmc-steward.git
cd bmc-steward
python bmc-steward.py
```

Optionale Bestandteile:

```bash
# Handy-Scanner (Barcode + Texterkennung + QR-Code)
sudo pacman -S zbar tesseract tesseract-data-eng qrencode

# UEFI-Konsole: eingebettete Web-Konsole und Textkonsole (SOL/SSH)
sudo pacman -S python-pyqt6-webengine
pip install --break-system-packages pyte paramiko pyghmi
```

Bei aktiver Firewall (ufw) für den DHCP-Server UDP 67 und für den Handy-Scanner Port 8443 freigeben:

```bash
sudo ufw allow 67/udp
sudo ufw allow 8443/tcp
```

### Windows

> **Hinweis:** Die fertige Windows-**.exe** und ihr Build sind **archiviert und werden nicht mehr
> gepflegt** (siehe [`archive/windows-build/`](archive/windows-build/)). BMC-Steward läuft unter
> Windows weiter **aus dem Quellcode** — der Code ist plattformübergreifend — entwickelt und getestet
> wird aber unter Linux.

**Aus dem Quellcode:**

1. **Python installieren** von [python.org](https://www.python.org/downloads/) (Version 3.9 oder neuer),
   beim Setup **„Add python.exe to PATH“** ankreuzen.
2. **PyQt6 installieren** in der Eingabeaufforderung:
   ```powershell
   pip install PyQt6
   ```
3. **Code holen:** entweder mit Git
   ```powershell
   git clone https://github.com/daklicker-code/bmc-steward.git
   ```
   oder auf GitHub über **Code → Download ZIP** und entpacken.
4. **Starten** im Projektordner:
   ```powershell
   python bmc-steward.py
   ```

Der DHCP-Server ist unter Windows eingebaut (kein dnsmasq nötig) — beim Start die Firewall-Abfrage
zulassen. Der Handy-Scanner braucht zusätzlich `openssl`, `zbar`, `tesseract` und `qrencode` im PATH,
unter Windows von Hand zu installieren. Die eingebettete UEFI-Konsole braucht QtWebEngine, die
**Text**konsole (SOL/SSH) braucht `pyte`, `paramiko` und `pyghmi`; mit
`pip install PyQt6-WebEngine pyte paramiko pyghmi` nachinstallieren.

### Debian / Ubuntu

```bash
sudo apt install python3-pyqt6 dnsmasq git
git clone https://github.com/daklicker-code/bmc-steward.git
cd bmc-steward
python3 bmc-steward.py
```

## Bedienung

1. Den eigenen Rechner und die BMC-Ports der neuen Server an einen **getrennten Staging-Switch** hängen.
   Der Netzwerkkarte eine feste Adresse ohne Gateway geben, z. B. `192.168.0.1/24`.
2. „DHCP-Server“: DHCP-Server starten. Die BMCs holen sich eine Adresse und erscheinen in der Liste. Von dort
   in die Serverliste übernehmen.
3. „Neuware“: Werks-Login, neuen Administrator und je Server neue IP, Subnetzmaske, Gateway und Hostname
   eintragen.
4. Trockenlauf starten und das Protokoll lesen.
5. Haken bei „Trockenlauf“ entfernen, scharf starten und bestätigen.

Die eingebaute Hilfe (Menüpunkt „Hilfe“) erklärt jeden Schritt und die Unterschiede zwischen den Herstellern.

## CSV-Format

```
# aktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,fertig
192.168.0.101,,10.20.0.11,255.255.255.0,10.20.0.1,srv-idrac-01,
```

Die Passwort-Spalte darf fehlen. Semikolons gehen auch, ein Excel-Export lässt sich also direkt laden.

## Teststand

| Hersteller | Ausgelegt auf | Stand |
|---|---|---|
| Dell | iDRAC9 (14. bis 16. Generation) | erste Zielplattform (R6625) |
| Dell | iDRAC7/8 (12./13. Generation), ab Firmware 2.50.50.50 | nur gegen simulierte BMCs geprüft |
| HPE | iLO 5 und 6 | nur gegen simulierte BMCs geprüft |
| HPE | iLO 3 (G7) und iLO 4 (Gen8/Gen9), über RIBCL | nur gegen simulierte BMCs geprüft |
| Supermicro | X12/X13 | nur gegen simulierte BMCs geprüft |
| Lenovo | XCC2/XCC3 | nur gegen simulierte BMCs geprüft |

Auch die UEFI-Einstellungen, die Verwaltungsdienste („Dienste“) und der Windows-DHCP-Server sind bisher
nur gegen Simulationen geprüft. Abdeckung der Dienste: NTP bei allen vier Herstellern; Syslog nur bei
Dell und HPE (bei Supermicro/Lenovo übersprungen); LDAP mit Gruppen-Zuordnung bei allen vier; der
HTTPS-Zertifikat-Import ist experimentell und je Hersteller verschieden (bei HPE muss der Schlüssel aus
einer auf dem iLO erzeugten CSR stammen). Für iLO 3/4 gibt es die Dienste nicht.

Nicht unterstützt: iDRAC6, iLO 2 und älter sowie iDRAC7/8 mit Firmware vor 2.50.50.50 (das Tool erkennt
das, meldet es und ändert nichts). iLO 3 kann nur TLS 1.0/1.1; das Tool schaltet für solche Geräte von
selbst auf die alte Verschlüsselung um und vermerkt es im Protokoll.
**Vor einem großen Rollout immer zuerst ein einzelnes Gerät konfigurieren.**

## Betrieb in einer VM (VMware, Proxmox, Hyper-V)

- **Eigenschutz einrichten:** Die VM kennt ihren Host nicht. Unter „Eigenschutz“ alle Hosts eintragen, auf
  denen die VM laufen kann (Service-Tag/Seriennummer, System-UUID oder BMC-Adresse). Solange die Liste leer
  ist, zeigt der Knopf eine rote Warnung und jede Sicherheitsabfrage weist darauf hin.
- Die VM möglichst auf einem eigenen Management-Host/-Cluster betreiben, nicht auf den Servern, die sie
  bearbeitet. Während Firmware-Läufen kein vMotion, kein Snapshot mit Arbeitsspeicher, kein Suspend.
- Netzwerk: eigene Netzwerkkarte (vmxnet3) in der Staging-/OOB-Portgruppe, kein NAT. Der DHCP-Server und die
  SSDP-Suche brauchen dasselbe L2-Segment wie die BMCs. Für fabrikneue BMCs mit fester Werks-IP eine
  zusätzliche Adresse in deren Netz vergeben.
- Für Virtual Media holt sich der BMC die Datei selbst – er muss die VM erreichen können.
- Zeit per NTP **oder** VMware-Tools-Zeitabgleich, nicht beides.
- Gespeicherte Listen mit Passwörtern landen in jedem VM-Backup: nicht speichern oder die VM verschlüsseln.

## Sicherheitshinweise

- Das Tool nur in einem getrennten Staging-Netz verwenden.
- BMCs haben ab Werk selbstsignierte Zertifikate. Statt gegen eine CA prüft das Tool deshalb nach dem Prinzip
  „beim ersten Kontakt merken“: Der SHA-256-Fingerabdruck wird je Gerät gespeichert, ein späterer Wechsel sperrt
  das Gerät, bevor ein Passwort gesendet wird. Der allererste Kontakt ist ungeschützt – deshalb nur im getrennten
  Staging-Netz arbeiten und bei Bestandsservern den Fingerabdruck einmal mit der BMC-Oberfläche vergleichen.
- Arbeitsdaten (Fingerabdrücke `bmc-pins.json`, Sicherungen `sicherungen/`, Revisionsprotokoll `protokolle/`)
  liegen neben dem Tool, Rechte 0600/0700. Mit der Umgebungsvariable `BMC_STEWARD_DATA` lässt sich ein anderer
  Ordner wählen, z. B. ein verschlüsseltes Laufwerk.
- Eine gespeicherte Liste enthält Passwörter nur auf ausdrücklichen Wunsch, dann im Klartext. So eine Datei
  nach dem Rollout löschen.

## Lizenz

MIT, siehe [LICENSE](LICENSE). Die Software kommt ohne Gewähr; die Nutzung erfolgt auf eigenes Risiko.

## Autor

Björn Franzke
