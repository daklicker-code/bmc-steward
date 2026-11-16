"""BMC-Steward: Vertrauen in BMC-Zertifikate (Trust on first use).

BMCs haben ab Werk selbstsignierte Zertifikate, eine Prüfung gegen eine CA ist deshalb nicht möglich. Damit
sich trotzdem niemand unbemerkt dazwischenschalten kann (und dabei Admin-Passwörter abgreift), merkt sich
BMC-Steward beim ersten Kontakt den SHA-256-Fingerabdruck des Zertifikats – je Gerät, nicht je Adresse:
Werks-Adressen wie 192.168.0.120 wiederholen sich, die Geräte-Kennung (Redfish-UUID bzw. Seriennummer bei
iLO 3/4) nicht. Meldet dasselbe Gerät später ein anderes Zertifikat, wird es gesperrt, bevor ein Passwort
gesendet wird. Ändert BMC-Steward selbst etwas, das das Zertifikat erneuern kann (Hostname, Adresse,
Neustart, Zertifikat-Import), wird der neue Fingerabdruck für dieses Gerät übernommen.

Gespeichert wird in bmc-pins.json im Datenordner (siehe i18n.DATA), Rechte 0600.
"""
import json
import threading
import time

from . import i18n

_LOCK = threading.RLock()


def short(fp):
    """Kurzform eines Fingerabdrucks zum Vorlesen/Vergleichen: AB:CD:…:EF (erste und letzte 4 Bytes)."""
    fp = (fp or "").upper()
    pairs = [fp[i:i + 2] for i in range(0, len(fp), 2)]
    return ":".join(pairs[:4]) + ":…:" + ":".join(pairs[-4:]) if len(pairs) > 8 else ":".join(pairs)


def pretty(fp):
    fp = (fp or "").upper()
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))


class PinStore:
    def __init__(self, path=None):
        self.path = path
        self.pins = {}
        self.load()

    def file(self):
        return self.path or (i18n.DATA / "bmc-pins.json")

    def load(self):
        with _LOCK:
            try:
                data = json.loads(self.file().read_text(encoding="utf-8"))
                self.pins = {k: v for k, v in data.get("pins", {}).items() if isinstance(v, dict) and v.get("fp")}
            except (OSError, ValueError, AttributeError):
                self.pins = {}

    def save(self):
        with _LOCK:
            i18n.write_private(self.file(), json.dumps({"pins": self.pins}, indent=1, ensure_ascii=False) + "\n")

    def check(self, key, fp, addr, accept_change=False):
        """Vergleicht und merkt. Rückgabe: ("new"|"same"|"renewed", None) oder ("changed", alter_fp)."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with _LOCK:
            old = self.pins.get(key)
            if old is None:
                self.pins[key] = {"fp": fp, "ip": addr, "first": now, "seen": now}
                self._save_quiet()
                return "new", None
            if old["fp"] == fp:
                if old.get("ip") != addr or old.get("seen", "")[:10] != now[:10]:
                    old.update(ip=addr, seen=now)
                    self._save_quiet()
                return "same", None
            if accept_change:
                prev = old["fp"]
                old.update(fp=fp, ip=addr, seen=now, previous=prev)
                self._save_quiet()
                return "renewed", prev
            return "changed", old["fp"]

    def forget(self, key):
        with _LOCK:
            if self.pins.pop(key, None) is not None:
                self.save()

    def for_addr(self, addr):
        """Alle gemerkten Fingerabdrücke, die zuletzt unter dieser Adresse gesehen wurden."""
        with _LOCK:
            return {k: v["fp"] for k, v in self.pins.items() if v.get("ip") == addr}

    def _save_quiet(self):
        try:
            self.save()
        except OSError:
            pass            # nur lesbarer Ordner: Prüfung gilt dann nur für diesen Lauf


_STORE = None


def store():
    global _STORE
    with _LOCK:
        if _STORE is None or (_STORE.path is None and getattr(_STORE, "_data", None) != i18n.DATA):
            _STORE = PinStore()
            _STORE._data = i18n.DATA
        return _STORE
