"""Statisches Sicherheits-Audit: durchsucht bmc_steward/*.py nach riskanten Mustern.

Drei Stufen:
  FAIL  – darf nicht vorkommen (Shell-Injektion, eval/exec, pickle, unsicheres YAML ...)
  INFO  – bewusst so gewollt und hier dokumentiert (abgeschaltete Zertifikatsprüfung für BMCs,
          alte TLS-Versionen für iLO 3/4). Kein Fehler, nur zur Erinnerung.
  GOOD  – positive Zusicherungen, die vorhanden sein sollen (Token-Vergleich, Passwort-Maskierung).

Das Audit liest nur Quelltext, führt nichts aus.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._support import REPO

PKG = REPO / "bmc_steward"

# (Regex, Erklärung) – ein Treffer = FAIL
FAIL_PATTERNS = [
    (r"shell\s*=\s*True", "subprocess mit shell=True (Shell-Injektion möglich)"),
    (r"os\.system\s*\(", "os.system() (Shell-Injektion möglich)"),
    (r"(?<![\.\w])eval\s*\(", "eval() auf Daten"),
    (r"(?<![\.\w])exec\s*\(", "exec() auf Daten"),      # Qt .exec() ist ausgenommen (Methodenaufruf)
    (r"pickle\.(load|loads)\s*\(", "pickle lädt beliebige Objekte"),
    (r"yaml\.load\s*\((?![^)]*Loader)", "yaml.load ohne SafeLoader"),
    (r"tempfile\.mktemp\s*\(", "tempfile.mktemp() (Race Condition) – mkstemp/mkdtemp nutzen"),
    (r"md5\s*\(", "MD5 (gebrochen) – für Sicherheitszwecke vermeiden"),
    (r"subprocess\.[A-Za-z_]+\(\s*f?[\"']", "subprocess mit String statt Argumentliste"),
]

# Bewusste Ausnahmen (nur Meldung, kein Fehler)
INFO_PATTERNS = [
    (r"verify_mode\s*=\s*ssl\.CERT_NONE", "Zertifikatsprüfung aus – ab Werk haben BMCs selbstsignierte Zertifikate"),
    (r"check_hostname\s*=\s*False", "Hostnamen-Prüfung aus – BMC-Zertifikate tragen keinen passenden Namen"),
    (r"SECLEVEL=0", "niedrige OpenSSL-Sicherheitsstufe – nur für iLO 3/4 mit alten Chiffren"),
    (r"TLSVersion\.TLSv1\b", "TLS 1.0 erlaubt – nur für sehr alte iLO-Firmware"),
    (r"OP_LEGACY_SERVER_CONNECT", "Legacy-Renegotiation – nur für alte BMCs"),
]

# Sollte vorhanden sein
GOOD_PATTERNS = [
    (r"secrets\.compare_digest", "Token-Vergleich zeitkonstant (gegen Timing-Angriffe)"),
    (r"\bmask\s*\(", "Passwort-Maskierung im Protokoll verwendet"),
    (r"secrets\.token_", "Scanner-Token kryptografisch zufällig"),
]


def _strip_strings_and_comments(line):
    """Grobe Bereinigung, damit Muster nicht in Kommentaren/Doku falsch anschlagen."""
    line = re.sub(r"#.*$", "", line)
    return line


def scan():
    findings = {"FAIL": [], "INFO": [], "GOOD": set()}
    files = sorted(PKG.glob("*.py"))
    for f in files:
        for n, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            line = _strip_strings_and_comments(raw)
            for rx, why in FAIL_PATTERNS:
                if re.search(rx, line):
                    findings["FAIL"].append((f.name, n, why, raw.strip()[:100]))
            for rx, why in INFO_PATTERNS:
                if re.search(rx, line):
                    findings["INFO"].append((f.name, n, why))
            for rx, why in GOOD_PATTERNS:
                if re.search(rx, line):
                    findings["GOOD"].add(why)
    return findings, [f.name for f in files]


def report(findings, files):
    print(f"Statisches Audit über {len(files)} Modul(e): {', '.join(files)}")
    for f in findings["FAIL"]:
        print(f"  FAIL  {f[0]}:{f[1]}  {f[2]}\n          {f[3]}")
    seen = set()
    for name, n, why in findings["INFO"]:
        if why not in seen:
            seen.add(why)
            print(f"  INFO  {why}")
    missing = [why for _, why in GOOD_PATTERNS if why not in findings["GOOD"]]
    for why in findings["GOOD"]:
        print(f"  GOOD  {why}")
    for why in missing:
        print(f"  WARN  erwartete Absicherung NICHT gefunden: {why}")
    ok = not findings["FAIL"]
    print(f"  => {'bestanden' if ok else str(len(findings['FAIL'])) + ' Problem(e)'}")
    return ok


if __name__ == "__main__":
    f, files = scan()
    sys.exit(0 if report(f, files) else 1)
