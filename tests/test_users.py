"""Tests für die Benutzerverwaltung (Version 3.0.26): UsersEngine - Konten lesen/anlegen/löschen,
Rolle, Aktiv-Status, Passwort und SSH-Keys, sowie die Passwort-Rotation über die Serverliste (run()).
Wie test_network_services.py: api()/ribcl()/log() werden gemockt, geprüft wird nur, welcher Pfad und
welcher Body gesendet würde (kein echtes Netzwerk)."""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from bmc_steward.redfish import UsersEngine

VENDORS = ("dell", "hpe", "smc", "lenovo")


def make_engine(script=None):
    """UsersEngine mit gemocktem api(): script ist eine Liste von (code, body)-Tupeln, die der Reihe
    nach für jeden api()-Aufruf zurückgegeben werden; danach 404/None."""
    e = UsersEngine.__new__(UsersEngine)
    e.dry = False
    e.stopped = lambda: False
    e.log_lines = []
    e.log = lambda *a, **kw: e.log_lines.append(a[0] if a else "")
    e.calls = []
    e.body = None
    e.sessions = {}
    e._script = list(script or [])

    def fake_api(ip, user, pw, method, path, data=None, timeout=30, _retry=True):
        e.calls.append({"method": method, "path": path, "data": data})
        code, body = e._script.pop(0) if e._script else (404, None)
        e.body = body
        return code
    e.api = fake_api
    e.ribcl_calls = []

    def fake_ribcl(ip, user, pw, mode, section, inner, timeout=60):
        e.ribcl_calls.append({"mode": mode, "section": section, "inner": inner})
        code, xml = e._script.pop(0) if e._script else (404, "")
        e.xml = xml
        return code
    e.ribcl = fake_ribcl
    return e


class TestListAccounts(unittest.TestCase):
    def test_skips_empty_dell_slot_and_reads_the_rest(self):
        e = make_engine(script=[
            (200, {"Members": [{"@odata.id": "/a/1"}, {"@odata.id": "/a/2"}]}),
            (200, {"UserName": "root", "RoleId": "Administrator", "Enabled": True}),
            (200, {"UserName": "", "RoleId": ""}),
        ])
        out = e.list_accounts("10.0.0.1", "root", "pw", "dell")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["username"], "root")
        self.assertEqual(out[0]["role"], "Administrator")
        self.assertTrue(out[0]["enabled"])
        self.assertEqual(out[0]["ssh_keys"], 0)

    def test_counts_ssh_keys_when_keys_navigation_present(self):
        e = make_engine(script=[
            (200, {"Members": [{"@odata.id": "/a/1"}]}),
            (200, {"UserName": "ops", "RoleId": "Operator", "Keys": {"@odata.id": "/a/1/Keys"}}),
            (200, {"Members": [{"@odata.id": "/a/1/Keys/1"}, {"@odata.id": "/a/1/Keys/2"}]}),
        ])
        out = e.list_accounts("10.0.0.1", "root", "pw", "hpe")
        self.assertEqual(out[0]["ssh_keys"], 2)
        self.assertEqual(out[0]["keys_path"], "/a/1/Keys")

    def test_returns_none_when_collection_unreadable(self):
        e = make_engine(script=[(401, None)])
        self.assertIsNone(e.list_accounts("10.0.0.1", "root", "pw", "dell"))

    def test_hpe_old_uses_ribcl_get_all_users(self):
        e = make_engine(script=[(200, '<RIBCL><USER_INFO MODE="read"><GET_ALL_USERS>'
                                      '<USER_LOGIN VALUE="Administrator"/>'
                                      '<USER_LOGIN VALUE="service"/>'
                                      '</GET_ALL_USERS></USER_INFO></RIBCL>')])
        out = e.list_accounts("10.0.0.1", "Administrator", "pw", "hpe_old")
        self.assertEqual([a["username"] for a in out], ["Administrator", "service"])


class TestCreateAccount(unittest.TestCase):
    def test_posts_to_collection_on_every_non_dell_vendor(self):
        for v in ("hpe", "smc", "lenovo"):
            with self.subTest(vendor=v):
                e = make_engine(script=[(201, {})])
                ok = e.create_account("10.0.0.1", "root", "pw", v, "neu", "Geheim123!", "Administrator")
                self.assertTrue(ok)
                self.assertEqual(e.calls[0]["method"], "POST")
                self.assertEqual(e.calls[0]["path"], e.ACCOUNTS)
                self.assertEqual(e.calls[0]["data"]["UserName"], "neu")

    def test_dell_patches_the_first_empty_slot(self):
        e = make_engine(script=[
            (200, {"Members": [{"@odata.id": "/a/2"}, {"@odata.id": "/a/3"}]}),
            (200, {"UserName": "root"}),
            (200, {"UserName": ""}),
            (200, {}),
        ])
        ok = e.create_account("10.0.0.1", "root", "pw", "dell", "neu", "Geheim123!", "Administrator")
        self.assertTrue(ok)
        self.assertEqual(e.calls[-1]["method"], "PATCH")
        self.assertEqual(e.calls[-1]["path"], "/a/3")
        self.assertEqual(e.calls[-1]["data"]["UserName"], "neu")

    def test_dell_fails_without_a_free_slot(self):
        e = make_engine(script=[
            (200, {"Members": [{"@odata.id": "/a/2"}]}),
            (200, {"UserName": "root"}),
        ])
        ok = e.create_account("10.0.0.1", "root", "pw", "dell", "neu", "Geheim123!", "Administrator")
        self.assertFalse(ok)

    def test_hpe_old_sends_add_user_via_ribcl(self):
        e = make_engine(script=[(200, "")])
        ok = e.create_account("10.0.0.1", "Administrator", "pw", "hpe_old", "neu", "Geheim123!")
        self.assertTrue(ok)
        self.assertEqual(e.ribcl_calls[0]["section"], "USER_INFO")
        self.assertIn("ADD_USER", e.ribcl_calls[0]["inner"])


class TestDeleteAccount(unittest.TestCase):
    def test_dell_clears_the_slot_instead_of_deleting(self):
        e = make_engine(script=[(200, {})])
        ok = e.delete_account("10.0.0.1", "root", "pw", "dell", "/a/3")
        self.assertTrue(ok)
        self.assertEqual(e.calls[0]["method"], "PATCH")
        self.assertEqual(e.calls[0]["data"], {"UserName": "", "Enabled": False})

    def test_other_vendors_send_http_delete(self):
        for v in ("hpe", "smc", "lenovo"):
            with self.subTest(vendor=v):
                e = make_engine(script=[(204, None)])
                ok = e.delete_account("10.0.0.1", "root", "pw", v, "/a/3")
                self.assertTrue(ok)
                self.assertEqual(e.calls[0]["method"], "DELETE")

    def test_hpe_old_sends_delete_user_via_ribcl(self):
        e = make_engine(script=[(200, "")])
        ok = e.delete_account("10.0.0.1", "Administrator", "pw", "hpe_old", "service")
        self.assertTrue(ok)
        self.assertIn("DELETE_USER", e.ribcl_calls[0]["inner"])


class TestRoleAndEnabled(unittest.TestCase):
    def test_set_role_patches_role_id(self):
        for v in ("dell", "hpe", "smc", "lenovo"):
            with self.subTest(vendor=v):
                e = make_engine(script=[(200, {})])
                ok = e.set_role("10.0.0.1", "root", "pw", v, "/a/3", "Operator")
                self.assertTrue(ok)
                self.assertEqual(e.calls[0]["data"], {"RoleId": "Operator"})

    def test_set_role_hpe_old_is_a_no_op(self):
        e = make_engine()
        self.assertIsNone(e.set_role("10.0.0.1", "Administrator", "pw", "hpe_old", "x", "Operator"))

    def test_set_enabled_patches_enabled_flag(self):
        e = make_engine(script=[(200, {})])
        ok = e.set_enabled("10.0.0.1", "root", "pw", "dell", "/a/3", False)
        self.assertTrue(ok)
        self.assertEqual(e.calls[0]["data"], {"Enabled": False})

    def test_set_enabled_hpe_old_is_a_no_op(self):
        e = make_engine()
        self.assertIsNone(e.set_enabled("10.0.0.1", "Administrator", "pw", "hpe_old", "x", False))


class TestRotatePassword(unittest.TestCase):
    def test_patches_password_on_every_redfish_vendor(self):
        for v in VENDORS:
            with self.subTest(vendor=v):
                e = make_engine(script=[(200, {})])
                ok = e.rotate_password("10.0.0.1", "root", "pw", v, "/a/3", "Neu123!!")
                self.assertTrue(ok)
                self.assertEqual(e.calls[0]["data"], {"Password": "Neu123!!"})

    def test_hpe_old_uses_mod_user_via_ribcl(self):
        e = make_engine(script=[(200, "")])
        ok = e.rotate_password("10.0.0.1", "Administrator", "pw", "hpe_old", "Administrator", "Neu123!!")
        self.assertTrue(ok)
        self.assertIn("MOD_USER", e.ribcl_calls[0]["inner"])


class TestSshKeys(unittest.TestCase):
    def test_add_key_skipped_without_keys_path(self):
        e = make_engine()
        self.assertIsNone(e.add_ssh_key("10.0.0.1", "root", "pw", None, "ssh-ed25519 AAAA"))
        self.assertEqual(e.calls, [])

    def test_add_key_posts_to_keys_collection(self):
        e = make_engine(script=[(201, {})])
        ok = e.add_ssh_key("10.0.0.1", "root", "pw", "/a/3/Keys", "ssh-ed25519 AAAA kommentar")
        self.assertTrue(ok)
        self.assertEqual(e.calls[0]["method"], "POST")
        self.assertEqual(e.calls[0]["path"], "/a/3/Keys")
        self.assertEqual(e.calls[0]["data"], {"KeyType": "SSH", "KeyString": "ssh-ed25519 AAAA kommentar"})

    def test_add_key_not_supported_is_none_not_error(self):
        e = make_engine(script=[(404, None)])
        self.assertIsNone(e.add_ssh_key("10.0.0.1", "root", "pw", "/a/3/Keys", "ssh-ed25519 AAAA"))

    def test_delete_key_skipped_without_path(self):
        e = make_engine()
        self.assertIsNone(e.delete_ssh_key("10.0.0.1", "root", "pw", None))

    def test_delete_key_sends_delete(self):
        e = make_engine(script=[(204, None)])
        ok = e.delete_ssh_key("10.0.0.1", "root", "pw", "/a/3/Keys/1")
        self.assertTrue(ok)
        self.assertEqual(e.calls[0]["method"], "DELETE")

    def test_list_keys_returns_key_strings(self):
        e = make_engine(script=[
            (200, {"Members": [{"@odata.id": "/a/3/Keys/1"}]}),
            (200, {"KeyString": "ssh-ed25519 AAAA kommentar"}),
        ])
        out = e.list_ssh_keys("10.0.0.1", "root", "pw", "/a/3/Keys")
        self.assertEqual(out, [{"path": "/a/3/Keys/1", "key": "ssh-ed25519 AAAA kommentar"}])

    def test_list_keys_empty_without_path(self):
        e = make_engine()
        self.assertEqual(e.list_ssh_keys("10.0.0.1", "root", "pw", None), [])


class TestRotationRun(unittest.TestCase):
    """UsersEngine.run(): Passwort-Rotation über die Serverliste."""

    def make_rotation_engine(self, rows, vendor="dell"):
        e = make_engine()
        e.rows = rows
        e.cur_user_set = ""
        e.force_vendor = vendor
        e.vendor = lambda ip: vendor
        e.backup = lambda *a, **k: None
        return e

    def test_rotates_every_server_and_returns_new_passwords(self):
        e = self.make_rotation_engine([("10.0.0.1", "altpw"), ("10.0.0.2", "altpw")])
        own_lookup = iter([
            {"Members": [{"@odata.id": "/a/2"}]},       # GET accounts (own account lookup) server 1
            {"UserName": "root"},                        # GET /a/2 server 1
            {"Members": [{"@odata.id": "/a/2"}]},       # server 2
            {"UserName": "root"},
        ])
        patch_ok = {"ok": True}

        def fake_api(ip, user, pw, method, path, data=None, timeout=30, _retry=True):
            e.calls.append({"method": method, "path": path, "data": data})
            if method == "PATCH":
                return 200
            e.body = next(own_lookup)
            return 200
        e.api = fake_api
        good, new_pw = e.run()
        self.assertTrue(good)
        self.assertEqual(set(new_pw), {"10.0.0.1", "10.0.0.2"})
        for pw in new_pw.values():
            self.assertEqual(len(pw), 16)

    def test_missing_password_counts_as_failed(self):
        e = self.make_rotation_engine([("10.0.0.1", "")])
        good, new_pw = e.run()
        self.assertFalse(good)
        self.assertEqual(new_pw, {})

    def test_offline_server_counts_as_failed(self):
        e = self.make_rotation_engine([("10.0.0.1", "pw")], vendor="offline")
        good, new_pw = e.run()
        self.assertFalse(good)
        self.assertEqual(new_pw, {})

    def test_own_account_not_found_counts_as_failed(self):
        e = self.make_rotation_engine([("10.0.0.1", "pw")])
        e.api = lambda *a, **kw: 401       # Konten nicht lesbar -> eigenes Konto nicht gefunden
        good, new_pw = e.run()
        self.assertFalse(good)
        self.assertEqual(new_pw, {})

    def test_hpe_old_rotates_via_ribcl_without_account_lookup(self):
        e = self.make_rotation_engine([("10.0.0.1", "pw")], vendor="hpe_old")
        e._script = [(200, "")]
        good, new_pw = e.run()
        self.assertTrue(good)
        self.assertEqual(list(new_pw), ["10.0.0.1"])
        self.assertIn("MOD_USER", e.ribcl_calls[0]["inner"])
