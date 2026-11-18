"""Sicherheit: Kein Passwort landet im Protokoll – weder im Trockenlauf noch im scharfen Lauf, auch nicht auf
Stufe Debug. Echte Läufe der Neuware-Engine gegen virtuelle BMCs aller Hersteller (im selben Prozess)."""
import unittest

from tests.test_vbmc import _WebBase, qapp

# Eindeutige Werte, die sonst nirgends vorkommen – so fällt jedes Auftauchen im Protokoll sofort auf
FACTORY_PW = {"dell": "Wk-Dell-91xQ!", "hpe": "Wk-Hpe-82yR!", "lenovo": "Wk-Len-73zS!", "smc": "Wk-Smc-64aT!",
              "hpe_old": "Wk-Ilo4-55bU!"}
FACTORY_USER = {"dell": "root", "hpe": "Administrator", "lenovo": "USERID", "smc": "ADMIN",
                "hpe_old": "Administrator"}
NEW_PW = "Neu-Geheim-7x!Q"


class TestNoSecretsInLog(_WebBase):
    def setUp(self):
        qapp()
        from bmc_steward.applog import DEBUG, INFO, LOG
        self.LOG = LOG
        LOG.clear()
        LOG.set_level(DEBUG)
        self.addCleanup(LOG.set_level, INFO)

    def _run(self, vendor, dry):
        from bmc_steward.redfish import Engine
        b, port = self._start(vendor)
        user = FACTORY_USER[vendor]
        b.accounts[user]["password"] = FACTORY_PW[vendor]
        addr = f"127.0.0.1:{port}"
        lines = []

        def log(line, kind=""):
            lines.append(line)
            self.LOG.debug(line)                    # wie im Fenster: alles landet auch im Programmprotokoll

        eng = Engine(rows=[(addr, FACTORY_PW[vendor], addr, "255.255.255.0", "127.0.0.1", f"srv-{vendor}")],
                     cur_user="", cur_pass="", new_user="ops", new_pass=NEW_PW, dry=dry, log=log,
                     verify_wait=3, lock_factory=True)
        eng.run()
        return "\n".join(lines)

    def _assert_clean(self, text, vendor):
        everything = text + "\n" + self.LOG.as_text()
        for secret in (FACTORY_PW[vendor], NEW_PW):
            self.assertNotIn(secret, everything, f"{vendor}: Passwort im Protokoll")

    def test_dry_run_never_logs_passwords(self):
        for vendor in ("dell", "hpe", "lenovo", "smc", "hpe_old"):
            with self.subTest(vendor=vendor):
                text = self._run(vendor, dry=True)
                self.assertTrue(text.strip())              # es wurde wirklich etwas protokolliert
                self._assert_clean(text, vendor)

    def test_live_run_never_logs_passwords(self):
        for vendor in ("dell", "hpe", "lenovo", "smc", "hpe_old"):
            with self.subTest(vendor=vendor):
                text = self._run(vendor, dry=False)
                self.assertIn("ops", text)                 # der neue Administrator kommt vor, sein Passwort nicht
                self._assert_clean(text, vendor)


if __name__ == "__main__":
    unittest.main()
