"""Aufbauplaner-Plugin für BMC-Steward (Plugin-API 1, ab BMC-Steward 3.14.0).

Plant Racks, Verkabelung und Adressen, prüft Redundanz und PDU-Last, erstellt eine Stückliste und übergibt
Hostnamen und BMC-Adressen an „Neuware“ oder die Server ans Testlabor.

Installieren: diesen Ordner nach <Datenordner>/plugins/aufbauplaner/ kopieren, BMC-Steward starten,
unter ⚙ → „Plugins“ freigeben und neu starten. Die Seite „Aufbauplaner“ erscheint im Menü unter „Plugins“.
"""
from . import texts
from .page import PlannerPage           # beim Laden importieren: BMC-Steward schreibt dann kein .pyc in den Ordner
from .texts import T

MIN_APP = (3, 14, 0)            # put_servers, testlab_*, set_lamp und close_request gibt es erst ab hier


def _version(text):
    out = []
    for part in str(text).split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out + [0] * (3 - len(out)))


def register(api):
    texts.set_lang(api.lang)
    if _version(api.app_version) < MIN_APP or not hasattr(api, "put_servers"):
        raise RuntimeError(T("Aufbauplaner braucht BMC-Steward {0} oder neuer (installiert: {1}).").format(
            ".".join(map(str, MIN_APP)), api.app_version))

    state = {"page": None}

    def page():
        w = PlannerPage(api)
        state["page"] = w
        return w

    def close_request():
        """Beim Beenden: ungespeicherten Plan speichern, verwerfen oder das Beenden abbrechen."""
        w = state["page"]
        return True if w is None else w.planner_confirm_discard()

    api.add_page(T("Aufbauplaner"), page, mark="▦")
    api.on("close_request", close_request)
