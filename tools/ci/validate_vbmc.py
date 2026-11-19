#!/usr/bin/env python3
"""CI: DMTF Redfish-Service-Validator gegen die virtuellen BMCs aller Hersteller (tools/vbmc).

Startet je Hersteller einen virtuellen BMC auf 127.9.0.x, lässt den offiziellen Validator den ganzen Baum gegen
die DMTF-Schemata prüfen und fasst die Zähler zusammen (Konsole und GitHub-Zusammenfassung). Die HTML-Berichte
landen in validator-reports/ (Workflow-Artefakt).

Abbruch mit Exit 1, wenn ein Hersteller mehr Fehler hat als in tools/ci/validator-baseline.json erlaubt – so
fällt jede Verschlechterung der Simulatoren auf, ohne dass bekannte Abweichungen die CI rot färben. Steht ein
Hersteller nicht in der Basislinie, wird nur berichtet. Neue Basislinie schreiben: --write-baseline.

Nur Werkzeug für die CI, kein Teil des Programms. Abhängigkeit: tools/ci/validator.lock.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("validator-baseline.json")
VENDORS = {"dell": ("root", "calvin"), "hpe": ("Administrator", "Admin123!"),
           "lenovo": ("USERID", "PASSW0RD"), "smc": ("ADMIN", "ADMIN")}
PORT = 8443


def wait_port(host, port, proc, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"vBMC beendet (Code {proc.returncode})")
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"vBMC {host}:{port} antwortet nicht")


def validate(vendor, host, reports, schemas):
    """Validator als Bibliothek aufrufen; gibt (pass, warn, fail, skip) zurück."""
    from redfish_service_validator import console_scripts
    captured = {}
    original = console_scripts.print_summary

    def grab(sut):
        captured["sut"] = sut
        return original(sut)
    console_scripts.print_summary = grab
    user, pw = VENDORS[vendor]
    try:
        console_scripts.run_validator({
            "payload": None, "ext_http_proxy": None, "ext_https_proxy": None,
            "serv_http_proxy": None, "serv_https_proxy": None,
            "schema_directory": str(schemas), "logdir": str(reports / vendor), "debugging": False,
            "rhost": f"https://{host}:{PORT}", "user": user, "password": pw, "timeout": 30,
            "authtype": "Session", "mockup": None, "collectionlimit": ["LogEntry", "20"], "nooemcheck": False,
            "skipschema": schemas.joinpath(".geladen").exists(),
        })
    finally:
        console_scripts.print_summary = original
    schemas.joinpath(".geladen").touch()        # Schemata nur einmal herunterladen
    sut = captured.get("sut")
    if sut is None:
        raise RuntimeError("Validator konnte sich nicht verbinden (siehe Protokoll oben)")
    return {"pass": sut.pass_count, "warn": sut.warn_count, "fail": sut.fail_count, "skip": sut.skip_count}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vendors", nargs="*", help="Hersteller (Vorgabe: alle): " + ", ".join(VENDORS))
    ap.add_argument("--reports", default="validator-reports")
    ap.add_argument("--write-baseline", action="store_true", help="Ergebnis als neue Basislinie speichern")
    args = ap.parse_args()
    args.vendors = args.vendors or list(VENDORS)
    unknown = set(args.vendors) - set(VENDORS)
    if unknown:
        ap.error("unbekannt: " + ", ".join(sorted(unknown)))
    reports = Path(args.reports).resolve()
    schemas = reports / "SchemaFiles"
    schemas.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}

    results, problems = {}, []
    for n, vendor in enumerate(args.vendors, 11):
        host = f"127.9.0.{n}"
        proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "vbmc" / "vbmc.py"), f"{vendor}@{host}:{PORT}"],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            wait_port(host, PORT, proc)
            print(f"\n=== {vendor} ({host}) ===", flush=True)
            results[vendor] = validate(vendor, host, reports, schemas)
        except Exception as e:                    # ein Hersteller darf die anderen nicht verhindern
            results[vendor] = {"error": str(e)}
            problems.append(f"{vendor}: {e}")
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()

    lines = ["## Redfish-Service-Validator (DMTF) gegen die virtuellen BMCs", "",
             "| Hersteller | bestanden | Warnungen | Fehler | erlaubt (Basislinie) |", "|---|---|---|---|---|"]
    for vendor, r in results.items():
        allowed = baseline.get(vendor, {}).get("fail")
        if "error" in r:
            lines.append(f"| {vendor} | – | – | {r['error']} | {allowed if allowed is not None else '–'} |")
            continue
        lines.append(f"| {vendor} | {r['pass']} | {r['warn']} | {r['fail']} | "
                     f"{allowed if allowed is not None else 'nur Bericht'} |")
        if allowed is not None and r["fail"] > allowed:
            problems.append(f"{vendor}: {r['fail']} Fehler, erlaubt sind {allowed}")
    text = "\n".join(lines) + "\n"
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text + ("\n**Probleme:** " + "; ".join(problems) + "\n" if problems else ""))
    (reports / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    if args.write_baseline:
        BASELINE.write_text(json.dumps({v: {"fail": r["fail"]} for v, r in results.items() if "fail" in r},
                                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Basislinie geschrieben: {BASELINE}")
        return 0
    for p in problems:
        print("FEHLER:", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
