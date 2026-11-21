# Aufbauplaner – mitgeliefertes Plugin für BMC-Steward

*English below.*

Plant den Einbau vor Ort: Racks aufstellen, Server aus der Serverliste und Geräte aus dem Katalog einbauen,
verkabeln, prüfen (Redundanz, PDU-Last, BMCs ohne Kabel), Ausfälle simulieren, Stückliste erstellen und
Hostnamen und BMC-Adressen an „Neuware“ übergeben oder die Server ins Testlabor legen.

Der Aufbauplaner ist ein Plugin, wird aber mit BMC-Steward ausgeliefert: Ab **3.15.0** lädt das Programm ihn
beim Start von selbst, ohne Freigabe. Die Seite **Aufbauplaner** steht im Menü unter „Plugins“, die Hilfe im
Menü **Plan** der Seite. Abschalten lässt er sich unter ⚙ → „Plugins“ (Haken entfernen, Neustart).

Wer unter 3.14.0 eine Kopie nach `<Datenordner>/plugins/aufbauplaner/` gelegt hat, kann sie löschen – das
mitgelieferte Plugin hat Vorrang, die Kopie wird nicht mehr geladen. Gespeicherte Pläne laden unverändert.

| Datei | Inhalt |
|---|---|
| `plugin.json` | Name, Version, Plugin-API 1 |
| `plugin.py` | `register(api)`: Seite, Rückfrage beim Beenden, Versionsprüfung |
| `page.py` | die Seite (Zeichenflächen, Dialoge, Bedienung) |
| `planner.py` | Datenmodell ohne Oberfläche (Platz, Kabel, Prüfung, Netzplan, Stückliste, Simulation) |
| `texts.py` | Übersetzungen (Englisch, Französisch, Schweizerdeutsch) und Hilfe |

Tests: `tests/test_plugin_aufbauplaner.py` und `tests/test_plugin_aufbauplaner_parts.py`.

---

# Rack planner – bundled plugin for BMC-Steward

Plans the installation on site: set up racks, fit servers from the server list and devices from the catalogue,
cable them, check them (redundancy, PDU load, BMCs without cable), simulate failures, produce a bill of materials
and hand hostnames and BMC addresses to “New hardware” or put the servers into the test lab.

The rack planner is a plugin but ships with BMC-Steward: from **3.15.0** the program loads it at startup by
itself, without approval. The **Rack planner** page is in the menu under “Plugins”, its help in the page's
**Plan** menu. It can be switched off under ⚙ → “Plugins” (untick, restart).

If you put a copy into `<data folder>/plugins/aufbauplaner/` under 3.14.0, you can delete it – the bundled plugin
takes precedence and the copy is no longer loaded. Saved plans load unchanged.
