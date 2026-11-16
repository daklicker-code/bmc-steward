"""BMC-Steward: Revisionsprotokoll.

Jeder ändernde Aufruf eines scharfen Laufs (alles außer GET) wird als eine JSON-Zeile festgehalten:
Zeitpunkt, Rechner, Benutzer, Programmversion, Lauf-Nummer, Gerät (Adresse und Kennung), Methode, Pfad,
HTTP-Ergebnis und der gesendete Inhalt – Passwörter als ***, Zertifikate gekürzt. So lässt sich später
belegen, wer wann was an welchem Server geändert hat (Change-Management, Nachweise).

Ablage: <Datenordner>/protokolle/aenderungen-JJJJ-MM.jsonl, Ordner 0700, Datei 0600. Ein Fehler beim
Schreiben bricht nie einen Lauf ab, wird aber einmal im Lauf-Protokoll gemeldet.
"""
import getpass
import json
import os
import socket
import sys
import threading
import time

from . import i18n

_LOCK = threading.Lock()


def folder():
    return i18n.DATA / "protokolle"


def current_file():
    return folder() / time.strftime("aenderungen-%Y-%m.jsonl")


def _user():
    try:
        return getpass.getuser()
    except Exception:
        return "?"


def write(entry):
    """Hängt einen Eintrag an. Gibt None oder eine Fehlermeldung zurück."""
    from .core import __version__
    rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": socket.gethostname(), "user": _user(),
           "version": __version__}
    rec.update(entry)
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        with _LOCK:
            path = current_file()
            i18n.private_dir(path.parent)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(line)
            if not sys.platform.startswith("win"):
                os.chmod(path, 0o600)
        return None
    except OSError as e:
        return str(e)
