"""Adress-/Feldvalidierung (core.row_errors u. a.) und die Passwort-Maskierung core.mask().

Diese Funktionen entscheiden, ob eine Zeile überhaupt an einen Server geschickt wird – eine Lücke
hier könnte eine falsche IP ausrollen. mask() ist die Zusage, dass Passwörter nie im Klartext ins
Protokoll geraten.
"""
import unittest
from tests._support import qapp

qapp()  # core importiert PyQt6.QtGui
from bmc_steward.core import ipv4, netmask, row_errors, mask, HOST_RE, USER_RE


def row(cur="", pw="", new="", msk="", gw="", host=""):
    return [cur, pw, new, msk, gw, host]


class TestIpValidation(unittest.TestCase):
    def test_valid(self):
        self.assertIsNotNone(ipv4("192.168.1.10"))

    def test_leading_zero_rejected(self):
        # 010.1.1.1 ist mehrdeutig (oktal) und muss abgelehnt werden
        self.assertIsNone(ipv4("010.1.1.1"))

    def test_shortforms_and_junk_rejected(self):
        for bad in ("10.1", "1.2.3.4.5", "256.1.1.1", "", " 1.1.1.1", "1.1.1.1 ", "0x7f.0.0.1", "abc"):
            self.assertIsNone(ipv4(bad), bad)

    def test_netmask_range(self):
        self.assertEqual(netmask("255.255.255.0").prefixlen, 24)
        self.assertIsNone(netmask("255.255.255.255"))   # /32 ausgeschlossen (1..30)
        self.assertIsNone(netmask("0.0.0.0"))           # /0 ausgeschlossen
        self.assertIsNone(netmask("255.0.255.0"))       # keine zusammenhängende Maske


class TestRowErrors(unittest.TestCase):
    def test_clean_row_has_no_errors(self):
        self.assertEqual(row_errors(row("192.168.1.9", "calvin", "192.168.1.10",
                                        "255.255.255.0", "192.168.1.1", "idrac01")), {})

    def test_empty_required_fields_flagged(self):
        e = row_errors(row())
        for col in (0, 2, 3, 4, 5):   # Passwort (1) ist optional
            self.assertIn(col, e)
        self.assertNotIn(1, e)

    def test_password_comma_rejected(self):
        # Kommas würden die CSV/Serialisierung zerreißen
        self.assertIn(1, row_errors(row(pw="pa,ss")))

    def test_gateway_outside_subnet(self):
        e = row_errors(row("192.168.1.9", "", "192.168.1.10", "255.255.255.0", "10.0.0.1", "h"))
        self.assertIn(4, e)

    def test_network_and_broadcast_rejected(self):
        self.assertIn(2, row_errors(row(new="192.168.1.0", msk="255.255.255.0", gw="192.168.1.1", host="h")))
        self.assertIn(2, row_errors(row(new="192.168.1.255", msk="255.255.255.0", gw="192.168.1.1", host="h")))

    def test_gateway_equals_new_ip(self):
        self.assertIn(4, row_errors(row(new="192.168.1.10", msk="255.255.255.0", gw="192.168.1.10", host="h")))

    def test_hostname_charset(self):
        self.assertIn(5, row_errors(row(host="bad_host")))     # Unterstrich
        self.assertIn(5, row_errors(row(host="-start")))       # Bindestrich am Anfang
        self.assertEqual(row_errors(row("1.1.1.1", "", "1.1.1.2", "255.255.255.0", "1.1.1.1", "ok-host")), {})


class TestHostUserRegex(unittest.TestCase):
    def test_hostname_length_cap(self):
        self.assertIsNone(HOST_RE.match("a" * 64))
        self.assertIsNotNone(HOST_RE.match("a" * 63))

    def test_user_regex(self):
        self.assertIsNotNone(USER_RE.match("admin_1"))
        self.assertIsNone(USER_RE.match("a" * 17))
        self.assertIsNone(USER_RE.match("space name"))


class TestPasswordMasking(unittest.TestCase):
    """Sicherheitszusage: mask() gibt nur Länge und Form zurück, nie den Klartext."""

    def test_form_string(self):
        self.assertEqual(mask("Calvin1!"), "8 Z. Aaaaaa9#")

    def test_never_leaks_plaintext(self):
        for pw in ("Sup3rSecret!", "hunter2", "P@ssw0rd123", "örtchen", "A" * 80):
            m = mask(pw)
            self.assertNotIn(pw, m, pw)
            # Der Formteil besteht nur aus A/a/9/# (plus Längen-Präfix "<n> Z. ")
            form = m.split("Z. ", 1)[1] if "Z. " in m else ""
            self.assertTrue(set(form) <= set("Aa9#"), form)

    def test_length_capped_at_40(self):
        # Auch die Form wird bei 40 Zeichen gekappt, damit lange Eingaben das Log nicht fluten
        self.assertLessEqual(len(mask("x" * 200).split("Z. ", 1)[1]), 40)

    def test_empty(self):
        self.assertEqual(mask(""), "0 Z. ")


if __name__ == "__main__":
    unittest.main()
