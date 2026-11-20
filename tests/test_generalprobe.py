"""Generalprobe: jede Funktion einmal Ende-zu-Ende gegen virtuelle BMCs aller Hersteller und Generationen.

Ein Server durchläuft den ganzen Lebenszyklus wie im Betrieb: Verbindungstest mit Werkszugang → Neuware scharf
(neuer Administrator, Hostname, Adresse, Werkskonto gesperrt) → Verbindungstest mit dem neuen Administrator →
UEFI lesen und setzen → Dienste → Benutzerkonten anlegen/ändern/löschen → Passwort-Rotation → Firmware
(Inventar, Prüfsumme, Datei und URL). Geprüft wird jeweils der Zustand im virtuellen BMC, nicht nur das Protokoll.

Dazu die Oberfläche: nach einem scharfen Neuware-Lauf melden sich UEFI-, Dienste-, Firmware- und Rotationslauf
für grüne Zeilen mit dem neuen Administrator an (nie mit dem gesperrten Werkskonto), und das Fenster lässt
sich nicht schließen, solange ein Firmware-Update oder eine Rotation läuft.
"""
import hashlib
import os
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.test_vbmc import _WebBase, qapp, vbmc

NEW_USER, NEW_PW = "ops", "Neu-Geheim-7x!Q"
FACTORY = {"dell": ("root", "calvin"), "hpe": ("Administrator", "Admin123!"), "lenovo": ("USERID", "PASSW0RD"),
           "smc": ("ADMIN", "ADMIN"), "hpe_old": ("Administrator", "Admin123!")}
# (Hersteller, Generation): jede Firmware-Familie, die BMC-Steward unterscheidet
TARGETS = [("dell", "idrac9"), ("dell", "idrac8"), ("hpe", "ilo6"), ("hpe", "ilo5"), ("hpe", "ilo4"),
           ("lenovo", "xcc2"), ("smc", "x13")]
SERVICES = {"ntp": {"servers": ["10.0.0.1", "10.0.0.2"], "timezone": "Europe/Berlin"},
            "syslog": {"server": "10.0.0.5", "port": 514},
            "ldap": {"server": "ldap.example", "port": 636, "base": "dc=ex,dc=org",
                     "groups": [("admins", "Administrator")]},
            "dns": {"servers": ["10.0.0.53"]},
            "hard": {"ipmi": True, "ssdp": True, "snmp": True, "lockout": 5, "lockout_min": 10, "timeout_min": 30}}


def _free_port():
    """Fester Port statt 0: Beim Adresswechsel bindet der virtuelle BMC neu – mit 0 wäre das ein anderer Port."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def settle(b, timeout=10):
    """Wartet, bis der virtuelle BMC seinen Adresswechsel abgeschlossen hat (er bindet asynchron neu)."""
    end = time.monotonic() + timeout
    while b.moving and time.monotonic() < end:
        time.sleep(0.02)


def _fast_pause(self, seconds):
    time.sleep(min(seconds, 0.05))


class _Fleet(_WebBase):
    """Virtueller BMC an festem Port, kurze Wartezeiten, Eigenschutz ohne Rechnerkennung."""

    def setUp(self):
        qapp()
        self._isolate_data()
        from bmc_steward import i18n
        from bmc_steward.redfish import RedfishClient
        from bmc_steward.selfguard import SelfGuard
        i18n.set_language("de")
        guard = SelfGuard(os.path.join(str(i18n.DATA), "guard.json"), probe=False)
        for p in (patch("bmc_steward.selfguard.get", return_value=guard),
                  patch.object(RedfishClient, "pause", _fast_pause),
                  patch.object(vbmc, "TASK_STEP", 0.02),
                  patch.object(vbmc, "BMC_RESTART", 0.3)):
            p.start()
            self.addCleanup(p.stop)

    def boot(self, vendor, gen):
        port = _free_port()
        fleet = vbmc.Fleet(self.ctx, [port])
        b = vbmc.BMC(vendor, "127.0.0.1", port, idx=1, gen=gen)
        fleet.start(b)
        self.addCleanup(fleet.stop, b)
        return b, f"127.0.0.1:{port}"


class TestLifecycleAllVendors(_Fleet):
    def test_every_function_once_per_vendor_and_generation(self):
        for vendor, gen in TARGETS:
            with self.subTest(vendor=vendor, gen=gen):
                self._lifecycle(vendor, gen)

    def _lifecycle(self, vendor, gen):
        from bmc_steward.redfish import (BiosEngine, Engine, FirmwareEngine, ProbeEngine, ServicesEngine,
                                         UsersEngine)
        b, addr = self.boot(vendor, gen)
        v = b.vendor
        fuser, fpw = FACTORY[v]
        lines = []

        def log(line, kind=""):
            lines.append((kind, line))

        def errors():
            return [t for k, t in lines if k == "err"]

        # 1 Bestandsserver: Werkszugang, nur lesend
        res = {}
        self.assertTrue(ProbeEngine([(addr, "", fpw)], log=log, on_result=lambda ip, r: res.update(r)).run())
        self.assertEqual(res["vendor"], v)
        self.assertTrue(res["admin"])

        # 2 Neuware: Trockenlauf ändert nichts
        row = [(addr, fpw, addr, "255.255.255.0", "127.0.0.1", "srv-gp")]
        self.assertTrue(Engine(row, "", "", NEW_USER, NEW_PW, dry=True, log=log, verify_wait=30).run())
        self.assertNotIn(NEW_USER, b.accounts)

        # 3 Neuware scharf: Administrator, Hostname, Werkskonto gesperrt (iLO 3/4: Zufallspasswort)
        lines.clear()
        self.assertTrue(Engine(row, "", "", NEW_USER, NEW_PW, dry=False, log=log, verify_wait=30).run(), lines)
        settle(b)
        self.assertEqual(b.accounts[NEW_USER]["password"], NEW_PW)
        self.assertEqual(b.hostname, "srv-gp")
        if v == "hpe_old":
            self.assertNotEqual(b.accounts[fuser]["password"], fpw)
        else:
            self.assertFalse(b.accounts[fuser]["enabled"])

        # 4 Verbindungstest mit dem neuen Administrator; ein zweiter Neuware-Lauf schadet nicht
        res.clear()
        self.assertTrue(ProbeEngine([(addr, NEW_USER, NEW_PW)], log=log, on_result=lambda ip, r: res.update(r)).run())
        self.assertTrue(res["admin"])
        row2 = [(addr, NEW_PW, addr, "255.255.255.0", "127.0.0.1", "srv-gp")]
        self.assertTrue(Engine(row2, NEW_USER, "", NEW_USER, NEW_PW, dry=False, log=log, verify_wait=30).run())
        settle(b)

        # 5 UEFI lesen und setzen
        be = BiosEngine([], NEW_USER, {}, dry=False)
        attrs, _ = be.read_bios(addr, NEW_USER, NEW_PW, "hpe" if v == "hpe_old" else v)
        self.assertEqual(attrs.get("BootMode"), "Uefi")
        lines.clear()
        self.assertFalse(BiosEngine([(addr, NEW_PW)], NEW_USER, {"BootMode": "Bios", "AdminPassword": "x"},
                                    dry=False, log=log, reboot=False).run())    # gesperrtes Attribut = Fehler …
        self.assertNotIn("AdminPassword", b.bios)                               # … und geht nie raus
        self.assertEqual(b.bios["BootMode"], "Bios", lines)                     # der Rest wird gesetzt

        # 6 Dienste (iLO 3/4: sauber übersprungen, nichts geändert)
        lines.clear()
        ok = ServicesEngine([(addr, NEW_PW)], NEW_USER, SERVICES, dry=False, log=log).run()
        if v == "hpe_old":
            self.assertFalse(ok)
            self.assertTrue(any("nicht unterstützt" in e for e in errors()), lines)
        else:
            self.assertTrue(ok, lines)
            ntp = (b.attributes.get("NTPConfigGroup.1.NTP1") if v == "dell" else (b.extra.get("ntp") or [""])[0])
            self.assertEqual(ntp, "10.0.0.1")

        # 7 Benutzerkonten: anlegen, Rolle, abschalten, Passwort, löschen
        ue = UsersEngine(cur_user=NEW_USER, dry=False, log=log)
        lines.clear()
        self.assertTrue(ue.create_account(addr, NEW_USER, NEW_PW, v, "gp-tmp", "Tmp-Pass-123!x", "Operator"), lines)
        acc = {a["username"]: a for a in ue.list_accounts(addr, NEW_USER, NEW_PW, v)}
        self.assertIn("gp-tmp", acc)
        path = acc["gp-tmp"]["path"]
        if v != "hpe_old":
            self.assertTrue(ue.set_role(addr, NEW_USER, NEW_PW, v, path, "ReadOnly"))
            self.assertTrue(ue.set_enabled(addr, NEW_USER, NEW_PW, v, path, False))
            self.assertEqual((b.accounts["gp-tmp"]["role"], b.accounts["gp-tmp"]["enabled"]), ("ReadOnly", False))
        self.assertTrue(ue.rotate_password(addr, NEW_USER, NEW_PW, v, path, "Tmp-Pass-456!y", login_name="gp-tmp"))
        self.assertEqual(b.accounts["gp-tmp"]["password"], "Tmp-Pass-456!y")
        self.assertTrue(ue.delete_account(addr, NEW_USER, NEW_PW, v, path), lines)
        self.assertNotIn("gp-tmp", b.accounts)
        self.assertNotIn("gp-tmp", [a["username"] for a in ue.list_accounts(addr, NEW_USER, NEW_PW, v)])

        # 8 Passwort-Rotation: neues Passwort steht nur im Rückgabewert und gilt sofort
        lines.clear()
        ok, new_pw = UsersEngine([(addr, NEW_PW)], NEW_USER, dry=False, log=log).run()
        self.assertTrue(ok, lines)
        self.assertEqual(b.accounts[NEW_USER]["password"], new_pw[addr])
        self.assertNotIn(new_pw[addr], " ".join(t for _, t in lines))
        pw = new_pw[addr]

        # 9 Firmware (iLO 3/4: nicht unterstützt, nichts eingespielt)
        fw = os.path.join(tempfile.mkdtemp(), "fw.bin")
        with open(fw, "wb") as fh:
            fh.write(os.urandom(4096))
        with open(fw, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        cfg = {"source": "file", "file": fw, "sha256": sha, "apply": "immediate"}
        before = {k: c["version"] for k, c in b.firmware.items()}
        self.assertFalse(FirmwareEngine([(addr, pw)], NEW_USER, dict(cfg, sha256="0" * 64), dry=False,
                                        log=log).run())
        self.assertEqual(b.tasks, {}, "falsche Prüfsumme: nichts hochgeladen")
        lines.clear()
        ok = FirmwareEngine([(addr, pw)], NEW_USER, cfg, dry=False, log=log).run()
        if v == "hpe_old":
            self.assertFalse(ok)
            self.assertEqual(b.tasks, {})
            return
        self.assertTrue(ok, lines)
        inv = FirmwareEngine([], NEW_USER, {}, dry=False).inventory(addr, NEW_USER, pw)
        self.assertTrue(inv)
        self.assertNotEqual({k: c["version"] for k, c in b.firmware.items()}, before)
        self.assertTrue(FirmwareEngine([(addr, pw)], NEW_USER, {"source": "uri", "uri": "https://files.example/fw.bin",
                                                                "proto": "HTTPS", "apply": "onreset"},
                                       dry=False, log=log).run(), lines)


class TestRestartBeforeLock(_Fleet):
    """Ein BMC, der nach dem Einrichten neu startet und seine Adresse behält (iLO mit gleicher IP), antwortet direkt
    nach dem Reset-Befehl noch kurz. Das Werkskonto darf erst gesperrt werden, wenn er einmal weg und wieder da war –
    sonst liefe die Sperre in sein Herunterfahren."""

    def test_ilo_same_address_is_locked_only_after_restart(self):
        from bmc_steward.redfish import Engine
        for gen in ("ilo6", "ilo4"):
            with self.subTest(gen=gen), patch.object(vbmc, "BMC_RESTART", 1.0):
                b, addr = self.boot("hpe", gen)
                fuser, fpw = FACTORY[b.vendor]
                seen = []
                orig = Engine.lock_factory_account

                def lock(eng, *a):
                    seen.append((b.moving, b.status))          # Zustand des BMC beim Sperren
                    return orig(eng, *a)
                lines = []
                with patch.object(Engine, "lock_factory_account", lock):
                    ok = Engine([(addr, fpw, addr, "255.255.255.0", "127.0.0.254", "srv")], "", "", NEW_USER, NEW_PW,
                                dry=False, log=lambda t, k="": lines.append(t), verify_wait=30).run()
                self.assertTrue(ok, lines)
                self.assertTrue(any("startet neu" in t for t in lines), lines)
                self.assertEqual(seen, [(0, "neu gestartet")], "erst nach dem Neustart sperren")
                if b.vendor == "hpe_old":
                    self.assertNotEqual(b.accounts[fuser]["password"], fpw)
                else:
                    self.assertFalse(b.accounts[fuser]["enabled"])


class TestVerifyAfterRestart(unittest.TestCase):
    """Kontrollphase von Engine.run() ohne Netzwerk: wer wartet worauf."""

    def _engine(self, rows, restarts, alive):
        qapp()
        from bmc_steward.redfish import Engine
        self.logs, self.locked, self.checked, self.pauses, self.alive_calls = [], [], [], [], []
        eng = Engine(rows=rows, cur_user="", cur_pass="pw", new_user="ops", new_pass="Neu-Pass-1!", dry=False,
                     log=lambda t, k="": self.logs.append(t), verify_wait=30, vendor="hpe")
        eng.pause = lambda s: self.pauses.append(s)
        eng.in_use = lambda ip: False
        eng.tls_mode = lambda ip, timeout=10: "modern"
        eng.backup = lambda *a, **k: None

        def setup(cur_ip, pw, new_ip, mask, gw, host):
            eng.verify_path = "/redfish/v1/Managers/1/"
            eng.restarts = restarts
            return True
        eng.setup_hpe = setup
        eng.check_login = lambda ip, path: self.checked.append(ip) or True
        eng.identity = lambda ip: ""
        eng.lock_factory_account = lambda ip, v, f: self.locked.append(ip) or True

        def fake_alive(ip):
            self.alive_calls.append(ip)
            return alive.pop(0) if alive else False
        eng.alive = fake_alive
        return eng

    def test_same_address_waits_until_gone_then_checks(self):
        eng = self._engine([("10.0.0.5", "pw", "10.0.0.5", "255.255.255.0", "10.0.0.1", "h")], True,
                           alive=[True, True, False])
        self.assertTrue(eng.run(), self.logs)
        self.assertEqual(len(self.alive_calls), 3)                  # zweimal noch da, dann weg
        self.assertEqual((self.checked, self.locked), (["10.0.0.5"], ["10.0.0.5"]))

    def test_never_restarted_is_checked_but_factory_account_stays(self):
        eng = self._engine([("10.0.0.5", "pw", "10.0.0.5", "255.255.255.0", "10.0.0.1", "h")], True, alive=[])
        eng.alive = lambda ip: True                                 # geht nie weg
        eng.RESTART_SEEN = 0
        self.assertFalse(eng.run())
        self.assertEqual(self.locked, [])
        text = "\n".join(self.logs)
        self.assertIn("nicht neu gestartet", text)
        self.assertIn("WERKSKONTO AKTIV", text)

    def test_new_address_or_no_restart_needs_no_waiting(self):
        for rows, restarts in (([("10.0.0.5", "pw", "10.0.0.50", "255.255.255.0", "10.0.0.1", "h")], True),
                               ([("10.0.0.5", "pw", "10.0.0.5", "255.255.255.0", "10.0.0.1", "h")], False)):
            with self.subTest(rows=rows, restarts=restarts):
                eng = self._engine(rows, restarts, alive=[])
                self.assertTrue(eng.run(), self.logs)
                self.assertEqual(self.alive_calls, [])
                self.assertEqual(self.locked, [rows[0][2]])
                self.assertLessEqual(self.pauses[0], 2, "keine feste 20-s-Wartezeit mehr")


class TestDellReservedSlot(_Fleet):
    def test_new_account_never_goes_to_reserved_slot_1(self):
        """Ein echter iDRAC listet alle 16 Slots; Slot 1 ist anonym reserviert und nicht beschreibbar."""
        from bmc_steward.redfish import UsersEngine
        b, addr = self.boot("dell", "idrac9")
        ue = UsersEngine(cur_user="root", dry=False, log=lambda *a, **k: None)
        self.assertTrue(ue.create_account(addr, "root", "calvin", "dell", "neu", "Neu-Pass-123!", "Operator"))
        self.assertNotEqual(b.accounts["neu"]["slot"], 1)
        self.assertEqual(b.accounts["neu"]["slot"], 3)


class TestRowUser(unittest.TestCase):
    def test_third_field_sets_user_only_for_that_row(self):
        qapp()
        from bmc_steward.redfish import BiosEngine
        e = BiosEngine([("a", "1", "ops"), ("b", "2"), ("c", "3", "")], "admin", {}, dry=True)
        seen = [(ip, pw, e.login_user("dell")) for ip, pw in e.rows3()]
        self.assertEqual(seen, [("a", "1", "ops"), ("b", "2", "admin"), ("c", "3", "admin")])
        self.assertEqual(e.login_user("dell"), "admin")
        e.cur_user_set = ""
        self.assertEqual(e.login_user("hpe"), "Administrator")


class TestWindowAfterLiveRun(_Fleet):
    """Oberfläche: Neuware scharf über den Knopf, danach UEFI, Dienste, Firmware und Rotation über ihre Seiten."""

    def setUp(self):
        super().setUp()
        from bmc_steward.main import Window
        self.w = Window()
        self.addCleanup(self.w.deleteLater)
        def no_box(*a, **k):
            raise AssertionError("unerwartete Meldung: " + " / ".join(str(x) for x in a[1:]))
        for p in (patch.object(type(self.w), "confirm", lambda *a, **k: True),
                  patch("bmc_steward.main.QMessageBox.warning", no_box)):
            p.start()
            self.addCleanup(p.stop)

    def wait(self, thread, timeout=60):
        end = time.monotonic() + timeout
        while thread.isRunning() and time.monotonic() < end:
            qapp().processEvents()
            time.sleep(0.01)
        self.assertFalse(thread.isRunning(), "Lauf hängt")
        for _ in range(20):
            qapp().processEvents()

    def test_done_rows_use_new_admin_on_every_page(self):
        w = self.w
        servers = [self.boot("dell", "idrac9"), self.boot("hpe", "ilo6")]
        w.table.setRowCount(0)
        for i, (b, addr) in enumerate(servers):
            w.add_row([addr, FACTORY[b.vendor][1], addr, "255.255.255.0", "127.0.0.254", f"gp{i}", ""])
        w.new_user.setText(NEW_USER)
        w.new_pass.setText(NEW_PW)
        w.new_pass2.setText(NEW_PW)
        w.dry.setChecked(False)
        w.clear_pass_after.setChecked(False)       # Werkspasswörter bleiben in den Zeilen stehen
        w.run()                                    # Wartezeiten der Kontrolle sind in setUp verkürzt
        self.wait(w.worker)
        for b, _ in servers:
            settle(b)
        self.assertEqual([w.is_done(r) for r in range(2)], [True, True], w.log.toPlainText()[-3000:])
        self.assertEqual(w.batch_rows("seite"), [(addr, NEW_PW, NEW_USER) for _, addr in servers])

        # UEFI-Einstellungen: der neue Administrator ändert, kein einziger Login mit dem Werkspasswort
        w.bios_dry.setChecked(False)
        w.bios_reboot.setChecked(False)
        w.bios_user.clear()
        with patch.object(type(w), "bios_wanted", lambda self: {"BootMode": "Bios"}):
            w.bios_run()
        self.wait(w.bios_worker)
        self.assertEqual([b.bios["BootMode"] for b, _ in servers], ["Bios", "Bios"], w.bios_log.toPlainText())

        # Dienste
        w.svc_dry.setChecked(False)
        w.svc_user.clear()
        with patch.object(type(w), "services_dict", lambda self: {"ntp": SERVICES["ntp"]}):
            w.services_run()
        self.wait(w.svc_worker)
        self.assertIn("Fertig: 2 Server ohne Fehler", w.svc_log.toPlainText())

        # Firmware
        fw = os.path.join(tempfile.mkdtemp(), "fw.bin")
        with open(fw, "wb") as fh:
            fh.write(b"\x00" * 2048)
        w.fw_dry.setChecked(False)
        w.fw_user.clear()
        cfg = {"source": "file", "file": fw, "sha256": "", "apply": "immediate", "stop_on_error": True, "targets": []}
        with patch.object(type(w), "firmware_config", lambda self: dict(cfg)):
            w.firmware_run()
        self.assertTrue(w.busy())                                   # Firmware läuft: Fenster bleibt offen
        ev = _CloseEvent()
        with patch("bmc_steward.main.QMessageBox.information"):
            w.closeEvent(ev)
        self.assertTrue(ev.ignored)
        self.wait(w.fw_worker)
        self.assertIn("Fertig: 2 Server ohne Fehler", w.fw_log.toPlainText())

        # Passwort-Rotation: neues Admin-Passwort landet in der Zeile und gilt danach für alle Seiten
        w.rot_dry.setChecked(False)
        w.users_user.clear()
        with patch.object(type(w), "rot_ask_save", lambda self, n: False):
            w.rot_run_now()
            self.wait(w.users_worker)
        rotated = [b.accounts[NEW_USER]["password"] for b, _ in servers]
        self.assertNotIn(NEW_PW, rotated)
        self.assertEqual([pw for _, pw, _ in w.batch_rows("")], rotated)
        self.assertEqual(w.row_login(servers[0][1]), (NEW_USER, rotated[0]))
        w.table.item(0, 1).setText("von Hand geändert")             # Markierung gilt nur für den rotierten Text
        self.assertEqual(w.row_login(servers[0][1]), (NEW_USER, NEW_PW))


class _CloseEvent:
    ignored = accepted = False

    def ignore(self):
        self.ignored = True

    def accept(self):
        self.accepted = True


if __name__ == "__main__":
    unittest.main()
