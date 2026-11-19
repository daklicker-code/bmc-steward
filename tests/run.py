#!/usr/bin/env python3
"""BMC-Steward: ein Befehl zum Prüfen – Unit-Tests, statisches Audit und Fuzzing.

    python3 tests/run.py              # alles (Tests + Audit + kurzes Fuzzing)
    python3 tests/run.py --quick      # ohne Fuzzing
    python3 tests/run.py --fuzz 50000 # intensiveres Fuzzing
    python3 tests/run.py --audit      # nur statisches Audit
    python3 tests/run.py --tests      # nur Unit-Tests
    python3 tests/run.py -j 1         # Unit-Tests nacheinander in einem Prozess (wie früher, langsam)

Läuft headless (QT_QPA_PLATFORM=offscreen) und braucht keinen echten BMC. Exit-Code 0 = alles grün,
sonst 1 – damit auch für einen CI-Lauf geeignet. Auszuführen auf dem Entwicklungs-PC im Repo-Ordner.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)


def run_tests(verbosity):
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=HERE, pattern="test_*.py", top_level_dir=REPO)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return result.wasSuccessful(), result


def run_tests_parallel(jobs):
    """Jedes Testmodul in einem eigenen Prozess, `jobs` gleichzeitig. Schneller und ohne Wechselwirkungen
    zwischen den Modulen (geteilter Zustand, liegengebliebene Fenster). Gibt (ok, Zusammenfassung) zurück."""
    mods = sorted(f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))

    def one(mod):
        env = dict(os.environ, BMC_STEWARD_DATA=tempfile.mkdtemp(prefix="bmc-steward-test-"))
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, "-m", "unittest", f"tests.{mod}"], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=1800)
        out = p.stdout + p.stderr
        m = re.search(r"^Ran (\d+) test", out, re.M)
        return mod, p.returncode == 0, int(m.group(1)) if m else 0, time.monotonic() - t0, out

    t0, failed, total = time.monotonic(), [], 0
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for mod, ok, n, secs, out in ex.map(one, mods):
            total += n
            print(f"  {'ok ' if ok else 'FEHLER'} {mod:32} {n:4} Tests {secs:6.1f} s", flush=True)
            if not ok:
                failed.append((mod, out))
    for mod, out in failed:
        print("\n" + "-" * 70 + f"\n{mod}\n" + "-" * 70)
        print(out[-6000:])
    print(f"\n{total} Tests in {len(mods)} Modulen, {time.monotonic() - t0:.0f} s, "
          f"{len(failed)} Modul(e) mit Fehlern")
    return not failed


def run_audit():
    from tests.audit import scan, report
    findings, files = scan()
    ok = report(findings, files)
    return ok


def run_fuzz(n):
    from tests.fuzz import run as fuzz_run
    res = fuzz_run(dhcp_iter=n, scan_iter=max(50, n // 10))
    ok = True
    for name, (runs, crashes, examples) in res.items():
        print(f"  {name:6} {runs:>7} Läufe  {crashes} Crashes")
        for ex in examples:
            print("     ", ex)
        ok = ok and crashes == 0
    return ok


def main():
    p = argparse.ArgumentParser(description="BMC-Steward Prüfsuite")
    p.add_argument("--quick", action="store_true", help="ohne Fuzzing")
    p.add_argument("--fuzz", type=int, default=5000, help="Fuzz-Durchläufe (DHCP); 0 = aus")
    p.add_argument("--audit", action="store_true", help="nur statisches Audit")
    p.add_argument("--tests", action="store_true", help="nur Unit-Tests")
    p.add_argument("-v", "--verbose", action="count", default=1)
    p.add_argument("-j", "--jobs", type=int, default=min(8, os.cpu_count() or 2),
                   help="Testmodule parallel in so vielen Prozessen (1 = alles nacheinander in einem Prozess)")
    a = p.parse_args()

    only = a.audit or a.tests
    ok = True

    if a.tests or not only:
        print("=" * 70, "\nUNIT-TESTS\n" + "=" * 70)
        t_ok = run_tests_parallel(a.jobs) if a.jobs > 1 else run_tests(a.verbose)[0]
        ok = ok and t_ok

    if a.audit or not only:
        print("\n" + "=" * 70, "\nSTATISCHES AUDIT\n" + "=" * 70)
        ok = run_audit() and ok

    if not only and not a.quick and a.fuzz > 0:
        print("\n" + "=" * 70, "\nFUZZING\n" + "=" * 70)
        ok = run_fuzz(a.fuzz) and ok

    print("\n" + "=" * 70)
    print("GESAMT:", "ALLES GRÜN ✓" if ok else "FEHLER ✗")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
