"""DHCP-Server-Robustheit (bmc_steward.dhcp).

Der eingebaute DHCP-Server (Windows; unter Linux dnsmasq) hört auf einem Netzwerk-Port und
verarbeitet Pakete, die von beliebigen Geräten kommen. Ein fehlerhaftes oder bösartiges Paket darf
den Server weder abstürzen lassen noch zu einem Lese-/Indexfehler führen. Außerdem darf nie eine
Adresse außerhalb des eingestellten Bereichs vergeben werden.
"""
import ipaddress
import struct
import unittest
from tests._support import qapp

qapp()
from bmc_steward.dhcp import DhcpServer


def discover(xid=b"ABCD", mac=b"\x02\x00\x00\x11\x22\x33", mtype=1, opts_extra=b"", magic=DhcpServer.MAGIC):
    opts = bytes([53, 1, mtype]) + opts_extra + b"\xff"
    pkt = struct.pack("!BBBB4sHH4s4s4s4s16s192x4s",
                      1, 1, len(mac), 0, xid, 0, 0x8000,
                      b"\0" * 4, b"\0" * 4, b"\0" * 4, b"\0" * 4,
                      mac + b"\0" * (16 - len(mac)), magic) + opts
    return pkt + b"\0" * max(0, 300 - len(pkt))


class TestOptionsParser(unittest.TestCase):
    def test_overlong_length_truncates_not_crashes(self):
        # Option 12 behauptet 200 Bytes, es folgen nur 2 -> Slice kürzt still
        out = DhcpServer.options(bytes([53, 1, 1, 12, 200, 65, 66]))
        self.assertEqual(out[12], b"AB")

    def test_empty_and_padding_and_terminator(self):
        self.assertEqual(DhcpServer.options(b""), {})
        self.assertEqual(DhcpServer.options(bytes([0, 0, 53, 1, 3, 255, 9, 9])), {53: b"\x03"})

    def test_dangling_code_without_length(self):
        # Letztes Byte ist ein Code ohne Längenbyte -> kein IndexError
        self.assertEqual(DhcpServer.options(bytes([53])), {})


class TestHandleRobustness(unittest.TestCase):
    def setUp(self):
        self.srv = DhcpServer("192.168.50.1", "255.255.255.0", "192.168.50.10", "192.168.50.12")

    def test_too_short_or_wrong_magic_returns_none(self):
        self.assertIsNone(self.srv.handle(b""))
        self.assertIsNone(self.srv.handle(b"\x01" * 240))                  # falsche Magic
        self.assertIsNone(self.srv.handle(b"\x02" + b"\x00" * 300))        # kein Request (op=2)

    def test_malformed_packets_never_raise(self):
        import random
        rng = random.Random(1)
        for _ in range(3000):
            blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 600)))
            try:
                self.srv.handle(blob)
            except Exception as e:   # pragma: no cover
                self.fail(f"handle() warf {type(e).__name__}: {e}")

    def test_discover_yields_offer_in_range(self):
        ans = self.srv.handle(discover())
        self.assertIsNotNone(ans)
        pkt, dest = ans
        self.assertEqual(pkt[0], 2)                      # BOOTREPLY
        self.assertEqual(pkt[4:8], b"ABCD")              # xid gespiegelt
        yi = ipaddress.IPv4Address(pkt[16:20])
        self.assertTrue(ipaddress.IPv4Address("192.168.50.10") <= yi <= ipaddress.IPv4Address("192.168.50.12"))
        self.assertGreaterEqual(len(pkt), 300)           # auf BOOTP-Mindestlänge gepolstert

    def test_pool_exhaustion_returns_none(self):
        # Nur 3 Adressen im Bereich .10-.12; vier verschiedene MACs -> die vierte bekommt nichts
        offers = []
        for i in range(4):
            mac = bytes([0x02, 0, 0, 0, 0, i])
            ans = self.srv.handle(discover(mac=mac))
            offers.append(ans)
        self.assertIsNone(offers[3])
        got = {ipaddress.IPv4Address(a[0][16:20]) for a in offers[:3]}
        self.assertEqual(len(got), 3)                    # keine doppelte Vergabe

    def test_allocate_respects_bounds(self):
        lo, hi = ipaddress.IPv4Address("192.168.50.10"), ipaddress.IPv4Address("192.168.50.12")
        # Wunschadresse außerhalb des Bereichs wird NICHT vergeben; stattdessen eine aus dem Bereich
        ip = self.srv.allocate("m", ipaddress.IPv4Address("10.0.0.1"))
        self.assertTrue(lo <= ip <= hi)
        self.assertNotEqual(ip, ipaddress.IPv4Address("10.0.0.1"))
        # Zweite MAC bekommt ebenfalls eine Adresse aus dem Bereich
        ip2 = self.srv.allocate("m2")
        self.assertTrue(lo <= ip2 <= hi)


if __name__ == "__main__":
    unittest.main()
