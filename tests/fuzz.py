"""Fuzzer: wirft zufällige/kaputte Eingaben auf die Parser, die im Betrieb Daten aus dem Netz sehen.

Ziel ist nicht „richtige" Ausgabe, sondern dass NICHTS abstürzt (keine unerwartete Exception) – ein
Absturz des DHCP-Servers oder des Scanner-Decoders wäre eine Lücke. Lässt sich einzeln starten:

    python3 tests/fuzz.py --iter 20000
"""
import os
import random
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._support import qapp


def fuzz_dhcp(iterations=5000, seed=0):
    """Zufällige Bytes an den DHCP-Paket-Parser. Gibt (Läufe, Crashes, [Beispiele]) zurück."""
    from bmc_steward.dhcp import DhcpServer
    srv = DhcpServer("192.168.99.1", "255.255.255.0", "192.168.99.10", "192.168.99.60")
    rng = random.Random(seed)
    crashes = []
    for _ in range(iterations):
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 700)))
        try:
            DhcpServer.options(blob)
            srv.handle(blob)
        except Exception as e:
            if len(crashes) < 10:
                crashes.append((repr(blob[:32]), f"{type(e).__name__}: {e}"))
    return iterations, len(crashes), crashes


def fuzz_scan(iterations=400, seed=0):
    """Zufällige Bytes als „Bild" an scan_decode (beide Modi). Gibt (Läufe, Crashes, [Beispiele]) zurück."""
    qapp()
    from bmc_steward.scanner import scan_decode
    rng = random.Random(seed)
    crashes = []
    for _ in range(iterations):
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 2048)))
        for mode in ("barcode", "ocr"):
            try:
                out = scan_decode(blob, mode)
                assert isinstance(out, tuple) and len(out) == 3
            except Exception as e:
                if len(crashes) < 10:
                    crashes.append((mode, repr(blob[:32]), f"{type(e).__name__}: {e}"))
    return iterations, len(crashes), crashes


def run(dhcp_iter=5000, scan_iter=400):
    results = {}
    for name, fn, it in (("dhcp", fuzz_dhcp, dhcp_iter), ("scan", fuzz_scan, scan_iter)):
        runs, crashes, examples = fn(it)
        results[name] = (runs, crashes, examples)
    return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--iter", type=int, default=5000, help="Durchläufe DHCP (Scan = 1/10 davon)")
    a = p.parse_args()
    res = run(a.iter, max(50, a.iter // 10))
    bad = 0
    for name, (runs, crashes, examples) in res.items():
        print(f"{name:6} {runs:>7} Läufe  {crashes} Crashes")
        for ex in examples:
            print("   ", ex)
        bad += crashes
    sys.exit(1 if bad else 0)
