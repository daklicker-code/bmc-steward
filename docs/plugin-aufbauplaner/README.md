# Aufbauplaner – Plugin für BMC-Steward

*English below.*

Plant den Einbau vor Ort: Racks aufstellen, Server aus der Serverliste und Geräte aus dem Katalog einbauen,
verkabeln, prüfen (Redundanz, PDU-Last, BMCs ohne Kabel), Ausfälle simulieren, Stückliste erstellen und
Hostnamen und BMC-Adressen an „Neuware“ übergeben oder die Server ins Testlabor legen.

Bis BMC-Steward 3.13 war der Aufbauplaner fest eingebaut; seit **3.14.0** gibt es ihn nur noch als Plugin.
Ältere Programmversionen lehnen das Plugin mit einer Meldung im Protokoll ab.

## Installieren

1. Diesen Ordner nach `<Datenordner>/plugins/aufbauplaner/` kopieren (Datenordner: neben dem Programm oder
   `BMC_STEWARD_DATA`). Unter Linux darf der Ordner nur für den eigenen Benutzer beschreibbar sein.
2. BMC-Steward starten, ⚙ → „Plugins“ → „Aufbauplaner“ anhaken, speichern, Neustart bestätigen.
3. Die Seite **Aufbauplaner** steht im Menü unter „Plugins“. Die Hilfe steht im Menü **Plan** der Seite.

Pläne aus früheren Versionen (`aufbauplan.json`) lassen sich unverändert laden.

## Dateien

| Datei | Inhalt |
|---|---|
| `plugin.json` | Name, Version, Plugin-API 1 |
| `plugin.py` | `register(api)`: Seite, Rückfrage beim Beenden, Versionsprüfung |
| `page.py` | die Seite (Zeichenflächen, Dialoge, Bedienung) |
| `planner.py` | Datenmodell ohne Oberfläche (Platz, Kabel, Prüfung, Netzplan, Stückliste, Simulation) |
| `texts.py` | Übersetzungen (Englisch, Französisch, Schweizerdeutsch) und Hilfe |

Tests: `tests/test_plugin_aufbauplaner.py` und `tests/test_plugin_aufbauplaner_parts.py` im Projekt.

---

# Rack planner – plugin for BMC-Steward

Plans the installation on site: set up racks, fit servers from the server list and devices from the catalogue,
cable them, check them (redundancy, PDU load, BMCs without cable), simulate failures, produce a bill of materials
and hand hostnames and BMC addresses to “New hardware” or put the servers into the test lab.

Up to BMC-Steward 3.13 the rack planner was built in; since **3.14.0** it is only available as a plugin. Older
program versions refuse the plugin with a message in the log.

## Installing

1. Copy this folder to `<data folder>/plugins/aufbauplaner/` (data folder: next to the program or
   `BMC_STEWARD_DATA`). On Linux, only your own user may write to the folder.
2. Start BMC-Steward, ⚙ → “Plugins” → tick “Aufbauplaner”, save, confirm the restart.
3. The **Rack planner** page is in the menu under “Plugins”. Its help is in the page's **Plan** menu.

Plans from earlier versions (`aufbauplan.json`) load unchanged.
