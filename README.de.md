# BMC-Steward

*English version: [README.md](README.md)*

Der BMC-Steward ist ein kleines Desktop-Tool für die Erstkonfiguration von Server-BMCs. Er bedient jeden
Server der Reihe nach: Er legt einen neuen Administrator an, setzt den Hostnamen und stellt den BMC von
der Werks- bzw. DHCP-Adresse auf eine feste Adresse um.

Unterstützt werden **Dell iDRAC**, **HPE iLO**, **Supermicro** und **Lenovo XCC** über Redfish. Ältere
HPE-Server mit **iLO 3 und iLO 4** laufen über die XML-Schnittstelle RIBCL, ältere Dell-Server mit
**iDRAC7/8** über Redfish (ab Firmware 2.50.50.50).

![BMC-Steward](docs/screenshot.png)

## Funktionen

- Eine einzige Python-Datei, läuft unter Linux und Windows
- Eingebauter DHCP-Server für einen getrennten Staging-Switch (unter Linux dnsmasq, unter Windows
  eingebaut)
- Serverliste mit Prüfung der Eingaben, CSV-Import und -Export, Profile für wiederkehrende Einstellungen
- Trockenlauf, der jeden Aufruf zeigt, ohne etwas zu ändern
- Server lassen sich einzeln nacheinander konfigurieren; fertige Zeilen werden grün und übersprungen
- Optional UEFI/BIOS-Einstellungen, gelesen von einem echten Referenzgerät
- Optionale Verwaltungsdienste für die ganze Liste: NTP, Syslog, LDAP/AD mit Gruppen-Rollen-Zuordnung, HTTPS-Zertifikat-Import
- Oberfläche in Deutsch, Englisch, Französisch und Schweizerdeutsch

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

## Voraussetzungen

- Python 3.9 oder neuer
- PyQt6
- Nur Linux: `dnsmasq` und polkit (`pkexec`) für den DHCP-Server

## Installieren und starten

**Arch Linux / CachyOS**

```bash
sudo pacman -S python-pyqt6 dnsmasq
python bmc-steward.py
```

**Debian / Ubuntu**

```bash
sudo apt install python3-pyqt6 dnsmasq
python3 bmc-steward.py
```

**Windows**

```powershell
pip install PyQt6
python bmc-steward.py
```

## Bedienung

1. Den eigenen Rechner und die BMC-Ports der neuen Server an einen **getrennten Staging-Switch** hängen.
   Der Netzwerkkarte eine feste Adresse ohne Gateway geben, z. B. `192.168.0.1/24`.
2. Reiter 1: DHCP-Server starten. Die BMCs holen sich eine Adresse und erscheinen in der Liste. Von dort
   in die Serverliste übernehmen.
3. Reiter 2: Werks-Login, neuen Administrator und je Server neue IP, Subnetzmaske, Gateway und Hostname
   eintragen.
4. Trockenlauf starten und das Protokoll lesen.
5. Haken bei „Trockenlauf“ entfernen, scharf starten und bestätigen.

Die eingebaute Hilfe (Reiter 4) erklärt jeden Schritt und die Unterschiede zwischen den Herstellern.

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

Auch die UEFI-Einstellungen, die Verwaltungsdienste (Reiter 4) und der Windows-DHCP-Server sind bisher
nur gegen Simulationen geprüft. Abdeckung der Dienste: NTP bei allen vier Herstellern; Syslog nur bei
Dell und HPE (bei Supermicro/Lenovo übersprungen); LDAP mit Gruppen-Zuordnung bei allen vier; der
HTTPS-Zertifikat-Import ist experimentell und je Hersteller verschieden (bei HPE muss der Schlüssel aus
einer auf dem iLO erzeugten CSR stammen). Für iLO 3/4 gibt es die Dienste nicht.

Nicht unterstützt: iDRAC6, iLO 2 und älter sowie iDRAC7/8 mit Firmware vor 2.50.50.50 (das Tool erkennt
das, meldet es und ändert nichts). iLO 3 kann nur TLS 1.0/1.1; das Tool schaltet für solche Geräte von
selbst auf die alte Verschlüsselung um und vermerkt es im Protokoll.
**Vor einem großen Rollout immer zuerst ein einzelnes Gerät konfigurieren.**

## Sicherheitshinweise

- Das Tool nur in einem getrennten Staging-Netz verwenden.
- BMCs haben ab Werk selbstsignierte Zertifikate, TLS-Zertifikate werden deshalb nicht geprüft.
- Eine gespeicherte Liste enthält Passwörter nur auf ausdrücklichen Wunsch, dann im Klartext. So eine Datei
  nach dem Rollout löschen.

## Lizenz

MIT, siehe [LICENSE](LICENSE). Die Software kommt ohne Gewähr; die Nutzung erfolgt auf eigenes Risiko.

## Autor

Björn Franzke
