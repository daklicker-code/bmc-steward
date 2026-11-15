"""Redfish/RIBCL-Sicherheit: XML-Injektion (xq), Geheimnis-Schwärzung (scrub) und die
Passwort-Maskierung im Trockenlauf-Protokoll von ribcl().

Bei iLO 3/4 werden Benutzername, Passwort und Hostname in ein RIBCL-XML eingesetzt. Ohne sauberes
Escaping könnte ein Passwort wie  pw"/><DELETE_USER .../>  fremdes XML einschleusen.
"""
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from tests._support import qapp

qapp()
from bmc_steward.redfish import xq, scrub, RedfishClient, Engine


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


class TestVerifyWait(unittest.TestCase):
    """Die Kontrolle der neuen Adressen am Ende von Engine.run() (Server-konfigurieren-Tab) darf einen
    einzelnen langsam/nicht antwortenden Server nicht die anderen über das Zeitbudget hinaus mit sich
    ziehen lassen (sonst wirkt der Lauf eingefroren), und muss den Fortschritt protokollieren statt
    über die ganze Wartezeit kein einziges Protokoll-Zeichen auszugeben."""

    def _engine(self, verify_wait=30):
        self.logs = []
        eng = Engine(rows=[], cur_user="root", cur_pass="pw", new_user="admin", new_pass="secret",
                     dry=False, log=lambda t, k="": self.logs.append((t, k)), verify_wait=verify_wait)
        eng.pause = lambda seconds: None     # keine echten Sleeps im Test
        return eng

    def _run_with_fake_dell_rows(self, eng, addresses):
        """Lässt run() so viele (gemockte, immer erfolgreiche) Dell-Server durchlaufen, wie `addresses`
        neue IPs nennt, ohne echtes Netzwerk - damit ausschließlich die Kontroll-/Wartephase danach
        unter echten Bedingungen (echter run()-Code, nicht nachgebaut) getestet wird."""
        eng.rows = [(f"10.0.0.{i}", "pw", addr, "255.255.255.0", "10.0.0.1", f"host{i}")
                    for i, addr in enumerate(addresses, start=1)]
        eng.force_vendor = "dell"
        eng.in_use = lambda ip: False
        eng.tls_mode = lambda ip, timeout=10: "modern"

        def fake_setup_dell(cur_ip, pw, new_ip, mask, gw, host):
            eng.verify_path = "/redfish/v1/Managers/1"
            return True
        eng.setup_dell = fake_setup_dell

    def test_gives_up_on_a_server_once_the_budget_is_spent_without_checking_the_rest(self):
        """Drei Server bleiben in der Kontrollphase unerreichbar; das Zeitbudget läuft bereits beim
        zweiten aus. Der dritte darf dann gar nicht mehr geprüft werden (sonst zieht ein einzelner
        langsamer/nicht antwortender Server die anderen über die Wartezeit hinaus mit sich - genau das,
        was sich wie ein Einfrieren des Laufs anfühlt)."""
        eng = self._engine(verify_wait=100)
        self._run_with_fake_dell_rows(eng, ["1.2.3.4", "1.2.3.5", "1.2.3.6"])
        calls = []

        def fake_check_login(ip, path):
            calls.append(ip)
            return False
        eng.check_login = fake_check_login
        # Kontrollphase: start, end, Budget-Check nach Server 1 (noch ok), Budget-Check nach
        # Server 2 (aufgebraucht) - Server 3 wird dann gar nicht mehr geprüft.
        with patch("bmc_steward.redfish.time.monotonic", side_effect=[0, 0, 10, 150]):
            good = eng.run()
        self.assertFalse(good)
        self.assertEqual(calls, ["1.2.3.4", "1.2.3.5"])
        texts = [t for t, _ in self.logs]
        for addr in ("1.2.3.4", "1.2.3.5", "1.2.3.6"):
            self.assertTrue(any("FEHLT" in t and addr in t for t in texts), texts)

    def test_logs_progress_between_rounds_and_fehlt_lines_at_the_end(self):
        """Voller run()-Durchlauf mit einem (gemockten) erfolgreich umgestellten Dell-Server, der in
        der Kontrollphase danach dauerhaft nicht erreichbar bleibt: Zwischen den Runden muss ein
        Fortschritts-Protokoll erscheinen (nicht einfach 1000s Stille) und am Ende „FEHLT“ stehen."""
        eng = self._engine(verify_wait=100)
        self._run_with_fake_dell_rows(eng, ["1.2.3.5"])
        eng.check_login = lambda ip, path: False     # bleibt die ganze Kontrollzeit unerreichbar

        # Aufrufreihenfolge in der Kontrollphase: start, end(=start+verify_wait), Budget-Check Runde 1
        # (noch Zeit), Fortschritts-Log, Budget-Check Runde 2 (Budget aufgebraucht -> Abbruch).
        with patch("bmc_steward.redfish.time.monotonic", side_effect=[0, 0, 10, 20, 150]):
            good = eng.run()
        self.assertFalse(good)
        texts = [t for t, _ in self.logs]
        self.assertTrue(any("vergangen" in t for t in texts), texts)
        self.assertTrue(any("FEHLT" in t and "1.2.3.5" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()
