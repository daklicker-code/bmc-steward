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


class TestSecretScrub(unittest.TestCase):
    def test_nested_and_vendor_style_keys(self):
        out = scrub({"Attributes": {"Users.3.Password": "geheim", "SNMP.1.AgentCommunity": "public"},
                     "LDAP": {"Authentication": {"Password": "x"}}, "Token": "t", "UserName": "admin"})
        for leak in ("geheim", "public", '"x"', '"t"'):
            self.assertNotIn(leak, out)
        self.assertIn("admin", out)

    def test_license_key_is_masked(self):
        self.assertNotIn("ABCDE-12345", scrub({"LicenseKey": "ABCDE-12345-FGHIJ-67890-KLMNO"}))

    def test_backups_on_disk_carry_no_secrets(self):
        from bmc_steward.redfish import redact
        doc = {"/Managers/1/Attributes": {"Attributes": {"SNMP.1.AgentCommunity": "public", "NIC.1.DNSRacName": "r1",
                                                         "Users.2.Password": None}},
               "/Managers/1/NetworkProtocol": {"IPMI": {"ProtocolEnabled": False}},
               "list": [{"Password": "geheim"}]}
        out = redact(doc)
        self.assertIsNone(out["/Managers/1/Attributes"]["Attributes"]["SNMP.1.AgentCommunity"])
        self.assertEqual(out["/Managers/1/Attributes"]["Attributes"]["NIC.1.DNSRacName"], "r1")
        self.assertEqual(out["/Managers/1/NetworkProtocol"], {"IPMI": {"ProtocolEnabled": False}})
        self.assertIsNone(out["list"][0]["Password"])
        self.assertEqual(doc["list"][0]["Password"], "geheim")          # Original bleibt unverändert


class TestFactoryLock(unittest.TestCase):
    def _eng(self, cur_user, new_user="ops"):
        lines = []
        e = Engine(rows=[], cur_user=cur_user, cur_pass="", new_user=new_user, new_pass="x", dry=True,
                   log=lambda line, kind="": lines.append(line))
        e.cur_user = cur_user
        return e, lines

    def test_only_factory_accounts_are_locked(self):
        for user in ("root", "Administrator", "ADMIN", "USERID", "admin"):
            self.assertEqual(self._eng(user)[0].factory_user_of("dell"), user)
        self.assertIsNone(self._eng("max.mustermann")[0].factory_user_of("dell"))
        self.assertIsNone(self._eng("root", new_user="ROOT")[0].factory_user_of("dell"))

    def test_random_password_meets_rules(self):
        for _ in range(200):
            pw = Engine.random_password()
            self.assertEqual(len(pw), 16)
            self.assertRegex(pw, r"[a-z]")
            self.assertRegex(pw, r"[A-Z]")
            self.assertRegex(pw, r"[0-9]")
            self.assertRegex(pw, r"[-_!+]")

    def test_fallback_to_random_password_when_disable_unsupported(self):
        calls = []
        e, lines = self._eng("root")
        e.dry = False
        docs = {"/redfish/v1/AccountService/Accounts": {"Members": [{"@odata.id": "/a/2"}, {"@odata.id": "/a/3"}]},
                "/a/2": {"UserName": "root", "Enabled": True}, "/a/3": {"UserName": "ops", "RoleId": "Administrator"}}

        def api(ip, user, pw, method, path, data=None, timeout=30):
            calls.append((method, path, data))
            e.body = docs.get(path)
            if method == "PATCH" and "Enabled" in data:
                e.body = {"error": {"@Message.ExtendedInfo": [{"MessageId": "Base.1.8.PropertyNotWritable"}]}}
                return 400
            return 200
        e.api = api
        self.assertTrue(e.lock_factory_account("10.0.0.9", "hpe", "root"))
        pw_calls = [c for c in calls if c[0] == "PATCH" and "Password" in (c[2] or {})]
        self.assertEqual(len(pw_calls), 1)
        self.assertEqual(pw_calls[0][1], "/a/2")
        self.assertNotIn(pw_calls[0][2]["Password"], "\n".join(lines))


class TestFactoryLockFlow(unittest.TestCase):
    """Reihenfolge im Lauf: Werkskonto erst nach erfolgreicher Kontrolle unter der neuen Adresse sperren."""

    def _run(self, verify_ok, lock_ok=True, lock_factory=True):
        events = []

        class E(Engine):
            def vendor(self, ip):
                return "dell"

            def in_use(self, ip):
                return False

            def tls_mode(self, ip, timeout=10):
                return "modern"

            def backup(self, *a, **k):
                return None

            def setup_dell(self, ip, pw, new_ip, mask, gw, host):
                events.append("setup")
                self.verify_path = "/m"
                return True

            def check_login(self, ip, path):
                events.append(("verify", ip))
                return verify_ok

            def identity(self, ip):
                return ""

            def lock_factory_account(self, ip, vendor, factory):
                events.append(("lock", ip, vendor, factory))
                return lock_ok

        e = E(rows=[("10.0.0.5", "calvin", "10.0.0.50", "255.255.255.0", "10.0.0.1", "h")], cur_user="",
              cur_pass="calvin", new_user="ops", new_pass="x", dry=False, verify_wait=1, vendor="dell",
              log=lambda *a, **k: None, lock_factory=lock_factory)
        e.pause = lambda s: None
        return e.run(), events

    def test_lock_after_successful_verify(self):
        ok, ev = self._run(True)
        self.assertTrue(ok)
        self.assertEqual(ev, ["setup", ("verify", "10.0.0.50"), ("lock", "10.0.0.50", "dell", "root")])

    def test_no_lock_when_verify_fails(self):
        ok, ev = self._run(False)
        self.assertFalse(ok)
        self.assertNotIn("lock", [e[0] for e in ev if isinstance(e, tuple)])

    def test_failed_lock_marks_run_failed(self):
        ok, _ = self._run(True, lock_ok=False)
        self.assertFalse(ok)

    def test_option_off(self):
        ok, ev = self._run(True, lock_factory=False)
        self.assertTrue(ok)
        self.assertNotIn("lock", [e[0] for e in ev if isinstance(e, tuple)])
