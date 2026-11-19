# Herkunft und Lizenzen / Provenance and licences

*English below.*

Stand: Version 3.4.2. Dies ist eine Bestandsaufnahme des Entwicklers, **keine Rechtsberatung**.

## Grundsatz

BMC-Steward ist reine Open-Source-Software (MIT). Es enthält **keinen Code, keine Firmware, keine Bilder, Logos,
Schriften, Oberflächentexte oder Dokumentationstexte** der Hersteller. Alles im Repository ist selbst geschrieben
oder stammt aus den unten genannten freien Quellen. Beim Weiterbauen gilt: Nur verwenden, was verwendet werden darf;
nichts aus Hersteller-Firmware, -Software oder -Handbüchern abschreiben.

## Was an Hersteller-Bezeichnern trotzdem vorkommt

Ein Verwaltungswerkzeug muss mit echten Geräten sprechen. Dafür stehen im Code **Bezeichner der Schnittstellen**
(Namen, keine Texte oder Programme). Sie werden wie bei jeder interoperablen Software benutzt, um mit den Geräten zu
reden, und stammen aus der Beobachtung der Schnittstelle bzw. aus öffentlich zugänglichen Schnittstellenbeschreibungen.

| Bereich | Beispiele | Herkunft |
|---|---|---|
| Redfish-Standard | `/redfish/v1/Systems`, `Managers`, `Actions/ComputerSystem.Reset`, `ResetType`, `AccountService`, `VirtualMedia` | DMTF-Standard (öffentlich, DSP0266 / DSP8010) |
| Herstellererweiterungen (OEM) | Pfade unter `Managers/iDRAC.Embedded.1`, `Oem/Hpe`, `Oem/Lenovo`, Aktion `HpeHttpsCert.ImportCertificate` | Schnittstellennamen echter Geräte, nötig zur Erkennung und Steuerung |
| Dell-Attribute | `NIC.1.DNSRacName`, `IPv4Static.1.Address`, `NTPConfigGroup.1.NTP1`, `SysLog.1.Server1`, `Time.1.Timezone` | Attributnamen der Geräteschnittstelle (nur Namen) |
| BIOS-Attribute | `BootMode`, `SecureBoot`, `LogicalProc`, `ProcVirtualization`, `SysProfile`, `SriovGlobalEnable`, `MemTest` | Attributnamen der Geräteschnittstelle (nur Namen); Werte kommen vom Gerät |
| RIBCL (iLO 3/4) | Elemente wie `RIBCL`, `LOGIN`, `GET_ALL_USERS`, `ADD_USER`, `MOD_USER`, `MOD_NETWORK_SETTINGS`, `GET_FW_VERSION` | Befehlsnamen der XML-Schnittstelle (nur Namen, kein Beispieltext) |
| Textkonsole | SSH-Befehle `console com2`, `vsp`, `console 1` | kurze Gerätebefehle |
| Erkennung | Felder `Manufacturer`, `Vendor`, `/xmldata` | Antwortfelder echter Geräte |

Was **nicht** im Code steht: Meldungstexte, Hilfetexte, Seitenlayouts, CSS, Skripte, Grafiken, Icons, Beispielantworten
oder Tabellen aus Hersteller-Dokumentation oder -Firmware.

## Simulator (`tools/vbmc`)

Der Simulator trägt die Fantasienamen **Böll, Häpe, Venolo, Hypernano**; Modellnamen enden auf `_v` (virtuell). Seine
Oberflächen, Texte, Farben und Symbole sind selbst gestaltet. Die internen Profil-Schlüssel (`dell`, `hpe`, `lenovo`, `smc`)
und die Protokoll-Kennungen auf dem Draht entsprechen den echten Geräten, damit BMC-Steward seine Erkennung testen kann.
Die früher mitgelieferten Redfish-Mockup-Bäume der Hersteller wurden entfernt (3.4.1); die Testdaten in `tests/_trees.py`
sind selbst geschrieben.

## Abhängigkeiten

| Paket | Lizenz | Hinweis |
|---|---|---|
| PyQt6, PyQt6-WebEngine | GPL-3.0 (alternativ kommerziell von Riverbank) | Die Oberfläche nutzt PyQt6. Wer BMC-Steward **zusammen mit** PyQt6 weitergibt (z. B. als fertiges Paket), muss die GPL-3.0 für das Gesamtpaket erfüllen. Der eigene Code bleibt MIT. Alternative: PySide6 (LGPL), dafür wäre eine Umstellung nötig. |
| Qt (PyQt6-Qt6) | LGPL-3.0 | |
| pyte | LGPL-3.0 | optional |
| paramiko | LGPL-2.1 | optional |
| pyghmi | Apache-2.0 | optional |
| keyring | MIT | optional |
| cryptography | Apache-2.0 / BSD | |

Die genauen Lizenzen der installierten Versionen stehen in deren Paketen (`pip show <paket>`); bitte vor einer
Weitergabe prüfen.

## Marken

Alle Hersteller- und Produktnamen (z. B. Dell, iDRAC, HPE, iLO, Lenovo, XClarity, Supermicro) sind Marken ihrer
jeweiligen Inhaber. Sie werden nur beschreibend genannt, um die unterstützten Geräte zu bezeichnen. BMC-Steward steht
in keiner Verbindung zu diesen Firmen und wird weder von ihnen unterstützt noch empfohlen.

---

# English

As of version 3.4.2. This is the developer's own inventory, **not legal advice**.

**Principle.** BMC-Steward is plain open-source software (MIT). It contains no code, firmware, images, logos, fonts,
UI texts or documentation texts from vendors. Everything is self-written or comes from the free sources listed here.
When extending: only use what you are allowed to use; never copy from vendor firmware, software or manuals.

**Vendor identifiers that remain.** To talk to real devices the code contains interface identifiers (names, not texts or
programs): standard DMTF Redfish paths, OEM paths and attribute names, BIOS attribute names, RIBCL element names, short
device commands and detection fields (see the German table above). Not in the code: message or help texts, page layouts,
CSS, scripts, graphics, icons, sample responses or tables taken from vendor documentation or firmware.

**Simulator.** Fantasy vendors Böll, Häpe, Venolo, Hypernano; model names end in `_v`. All UI, texts, colours and icons
are self-made. Internal profile keys and wire identifiers match real devices so BMC-Steward can test its detection.
The vendor Redfish mockup trees formerly shipped were removed in 3.4.1.

**Dependencies.** PyQt6 and PyQt6-WebEngine are GPL-3.0 (or commercial): anyone distributing BMC-Steward *together with*
PyQt6 must satisfy the GPL-3.0 for the combined package; BMC-Steward's own code stays MIT. PySide6 (LGPL) is an alternative.
Qt LGPL-3.0, pyte LGPL-3.0, paramiko LGPL-2.1, pyghmi Apache-2.0, keyring MIT, cryptography Apache-2.0/BSD. Check the
installed versions before redistribution.

**Trademarks.** All vendor and product names are trademarks of their respective owners, used descriptively only.
BMC-Steward is not affiliated with, endorsed by or supported by these companies.
