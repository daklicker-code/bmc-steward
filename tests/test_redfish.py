"""Redfish/RIBCL-Sicherheit: XML-Injektion (xq), Geheimnis-Schwärzung (scrub) und die
Passwort-Maskierung im Trockenlauf-Protokoll von ribcl().

Bei iLO 3/4 werden Benutzername, Passwort und Hostname in ein RIBCL-XML eingesetzt. Ohne sauberes
Escaping könnte ein Passwort wie  pw"/><DELETE_USER .../>  fremdes XML einschleusen.
"""
import unittest
import xml.etree.ElementTree as ET
from tests._support import qapp

qapp()
from bmc_steward.redfish import xq, scrub, RedfishClient


class TestXmlQuote(unittest.TestCase):
    def test_escapes_all_dangerous_chars(self):
        out = xq('a" onx="><INJECT>&x')
        self.assertTrue(out.startswith('"') and out.endswith('"'))
        inner = out[1:-1]
        # Die Ausbruch-Zeichen dürfen nicht roh vorkommen; & nur als Entity-Präfix (&quot; &amp; ...)
        for ch in ('<', '>', '"'):
            self.assertNotIn(ch, inner, ch)
        self.assertNotRegex(inner, r"&(?!(amp|quot|lt|gt|apos);)")

    def test_injection_cannot_create_elements(self):
        # Ein RIBCL-Fragment mit bösartigem Passwort muss wohlgeformtes XML mit genau EINEM Element bleiben
        evil = 'x"/><ADD_USER USER_NAME="hax" PASSWORD="y"><ADMIN_PRIV value="Yes"/></ADD_USER><LOGIN A="'
        frag = f"<MOD_USER USER_LOGIN={xq('root')} PASSWORD={xq(evil)}/>"
        root = ET.fromstring(frag)
        self.assertEqual(root.tag, "MOD_USER")
        self.assertEqual(list(root), [])                 # keine eingeschleusten Kindelemente
        self.assertEqual(root.get("PASSWORD"), evil)     # Wert kommt unverändert wieder heraus

    def test_full_login_document_wellformed(self):
        doc = (f'<RIBCL VERSION="2.0"><LOGIN USER_LOGIN={xq("ro><ot")} PASSWORD={xq("a&b>c<d")}>'
               f'<RIB_INFO MODE="read"><GET_FW_VERSION/></RIB_INFO></LOGIN></RIBCL>')
        root = ET.fromstring(doc)
        self.assertEqual(root.find("LOGIN").get("USER_LOGIN"), "ro><ot")


class TestScrub(unittest.TestCase):
    def test_passwords_redacted_at_any_depth(self):
        data = {"UserName": "admin", "Password": "topsecret",
                "Oem": {"ldap": {"password": "bindpw"}}, "list": [{"Password": "x"}]}
        out = scrub(data)
        self.assertNotIn("topsecret", out)
        self.assertNotIn("bindpw", out)
        self.assertIn("admin", out)        # Nicht-Geheimnisse bleiben erhalten
        self.assertIn("***", out)

    def test_certificates_shortened(self):
        out = scrub({"CertificateString": "-----BEGIN CERT-----AAAA...." * 50})
        self.assertIn("<cert>", out)
        self.assertLess(len(out), 200)

    def test_none(self):
        self.assertEqual(scrub(None), "")


class TestRibclDryRunMasking(unittest.TestCase):
    """Im Trockenlauf wird das RIBCL-Fragment protokolliert – ohne jedes Passwort im Klartext."""

    def _log_of(self, inner, user="root", pw="LOGINPW"):
        lines = []
        c = RedfishClient(dry=True, log=lines.append)
        code = c.ribcl("10.0.0.1", user, pw, "write", "USER_INFO", inner)
        return code, "\n".join(lines)

    def test_add_user_password_masked(self):
        code, log = self._log_of('<ADD_USER USER_NAME="x" USER_LOGIN="x" PASSWORD="NEWPASS123"><ADMIN_PRIV value="Yes"/></ADD_USER>')
        self.assertEqual(code, 200)
        self.assertNotIn("NEWPASS123", log)
        self.assertIn("***", log)

    def test_mod_user_value_form_masked(self):
        _, log = self._log_of('<MOD_USER USER_LOGIN="root"><PASSWORD value="NEWPASS123"/></MOD_USER>')
        self.assertNotIn("NEWPASS123", log)

    def test_login_password_not_logged(self):
        # Das Anmeldepasswort steht gar nicht im protokollierten Fragment
        _, log = self._log_of('<GET_FW_VERSION/>', pw="LOGINPW")
        self.assertNotIn("LOGINPW", log)


if __name__ == "__main__":
    unittest.main()
