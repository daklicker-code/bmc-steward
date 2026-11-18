"""openssl finden – auch unter Windows, wo es meist nicht im PATH liegt.

Windows bringt kein openssl mit. Es steckt aber oft in Git for Windows, in einer OpenSSL-Installation,
in MSYS2, Strawberry Perl oder Conda – und ist dann nicht im PATH des laufenden Programms (oder der PATH
wurde erst nach dem Start des Programms ergänzt). Diese Datei sucht deshalb an den üblichen Orten.

ACHTUNG: Diese Datei gibt es zweimal, identisch: `bmc_steward/ossl.py` und `tools/vbmc/vbsim/ossl.py` (das
Testlabor kommt nur mit der Standardbibliothek aus und darf `bmc_steward` nicht importieren).
`tests/test_ossl.py` prüft, dass beide gleich sind."""
import os
import shutil
import sys

_cache = {}


def _registry_path():
    """Systemweiter und benutzerbezogener PATH aus der Registry (Windows) – auch was nach dem Start dazukam."""
    if sys.platform != "win32":
        return []
    out = []
    try:
        import winreg
        for root, sub in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, sub) as k:
                    val, _t = winreg.QueryValueEx(k, "Path")
                out += [os.path.expandvars(p.strip().strip('"')) for p in str(val).split(os.pathsep) if p.strip()]
            except OSError:
                pass
    except ImportError:
        pass
    return out


def _windows_dirs():
    env = os.environ.get
    roots = [r for r in (env("ProgramFiles"), env("ProgramW6432"), env("ProgramFiles(x86)"),
                         env("LOCALAPPDATA") and os.path.join(env("LOCALAPPDATA"), "Programs"),
                         env("ProgramData"), env("SystemDrive", "C:") + os.sep) if r]
    sub = (r"Git\usr\bin", r"Git\mingw64\bin", r"Git\bin", r"OpenSSL-Win64\bin", r"OpenSSL-Win32\bin",
           r"OpenSSL\bin", r"OpenSSL-Win64", r"FireDaemon OpenSSL 3\bin", r"Strawberry\c\bin",
           r"msys64\usr\bin", r"msys64\mingw64\bin", r"msys64\ucrt64\bin", r"cygwin64\bin",
           r"Anaconda3\Library\bin", r"miniconda3\Library\bin", r"chocolatey\bin", r"Wireshark")
    dirs = [os.path.join(r, s) for r in roots for s in sub]
    for base in (sys.prefix, getattr(sys, "base_prefix", sys.prefix)):         # Python/Conda im Umfeld
        dirs += [os.path.join(base, "Library", "bin"), os.path.join(base, "Scripts"), base]
    home = os.path.expanduser("~")
    dirs += [os.path.join(home, "scoop", "shims"), os.path.join(home, "scoop", "apps", "git", "current", "usr", "bin"),
             os.path.join(home, "scoop", "apps", "openssl", "current", "bin")]
    return dirs


def find_openssl(refresh=False):
    """Pfad zu openssl(.exe) oder None. Reihenfolge: Umgebungsvariable OPENSSL_EXE, PATH, Registry-PATH, übliche Orte."""
    if not refresh and "p" in _cache:
        return _cache["p"]
    found = None
    cand = os.environ.get("OPENSSL_EXE") or os.environ.get("OPENSSL_BIN")
    if cand and os.path.isfile(cand):
        found = cand
    if not found:
        found = shutil.which("openssl")
    if not found and sys.platform == "win32":
        for d in _registry_path() + _windows_dirs():
            for name in ("openssl.exe", "openssl.bat", "openssl.cmd"):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    found = p
                    break
            if found:
                break
    _cache["p"] = found
    return found


def hint():
    """Kurzer Hinweis, was zu tun ist, wenn openssl fehlt."""
    if sys.platform == "win32":
        return ("openssl nicht gefunden. Installieren (z. B. `winget install ShiningLight.OpenSSL.Light` oder "
                "Git for Windows) oder den vollen Pfad in der Umgebungsvariable OPENSSL_EXE angeben.")
    return "openssl nicht gefunden. Über die Paketverwaltung installieren (z. B. `apt install openssl`)."
