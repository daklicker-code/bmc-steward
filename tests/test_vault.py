"""Tests für vault.py (Version 3.0.26): dünner Wrapper um das Paket `keyring`, mit dem Passwörter der
Serverliste optional im System-Schlüsselbund statt im Klartext liegen können. Läuft ohne echten
Secret-Service-Dienst: ein kleines gefälschtes Schlüsselbund-Backend ersetzt `vault.keyring`."""
import unittest
from unittest.mock import patch

from bmc_steward import vault


class FakeKeyringErrors:
    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass


class FakeKeyring:
    """Hält Einträge nur im Prozessspeicher, wie ein echtes Backend aber ohne Betriebssystem-Anbindung."""

    errors = FakeKeyringErrors

    def __init__(self):
        self.store = {}

    def set_password(self, service, key, password):
        self.store[(service, key)] = password

    def get_password(self, service, key):
        return self.store.get((service, key))

    def delete_password(self, service, key):
        if (service, key) not in self.store:
            raise self.errors.PasswordDeleteError("kein Eintrag")
        del self.store[(service, key)]


class TestKeyFor(unittest.TestCase):
    def test_prefers_serial_over_ip(self):
        self.assertEqual(vault.key_for("ABC123", "10.0.0.1"), "serial:ABC123")

    def test_falls_back_to_ip_without_serial(self):
        self.assertEqual(vault.key_for("", "10.0.0.1"), "ip:10.0.0.1")
        self.assertEqual(vault.key_for(None, "10.0.0.1"), "ip:10.0.0.1")

    def test_strips_whitespace(self):
        self.assertEqual(vault.key_for("  ABC123  ", ""), "serial:ABC123")


class TestAvailable(unittest.TestCase):
    def test_false_when_package_missing(self):
        with patch.object(vault, "keyring", None):
            self.assertFalse(vault.available())

    def test_false_with_the_builtin_fail_backend(self):
        import keyring.backends.fail
        with patch.object(vault.keyring, "get_keyring", return_value=keyring.backends.fail.Keyring()):
            self.assertFalse(vault.available())

    def test_true_with_a_working_backend(self):
        with patch.object(vault.keyring, "get_keyring", return_value=FakeKeyring()):
            self.assertTrue(vault.available())


class TestStoreFetchDelete(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKeyring()
        patcher = patch.object(vault, "keyring", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        avail_patcher = patch.object(vault, "available", return_value=True)
        avail_patcher.start()
        self.addCleanup(avail_patcher.stop)

    def test_store_then_fetch_round_trip(self):
        self.assertTrue(vault.store("ABC123", "10.0.0.1", "Geheim123!"))
        self.assertEqual(vault.fetch("ABC123", "10.0.0.1"), "Geheim123!")

    def test_fetch_by_ip_when_no_serial_was_given_at_store_time(self):
        vault.store("", "10.0.0.1", "Geheim123!")
        self.assertEqual(vault.fetch(None, "10.0.0.1"), "Geheim123!")

    def test_fetch_missing_entry_returns_empty_string(self):
        self.assertEqual(vault.fetch("NOPE", ""), "")

    def test_store_empty_password_deletes_an_existing_entry(self):
        vault.store("ABC123", "10.0.0.1", "Geheim123!")
        self.assertTrue(vault.store("ABC123", "10.0.0.1", ""))
        self.assertEqual(vault.fetch("ABC123", "10.0.0.1"), "")

    def test_store_without_serial_or_ip_does_nothing(self):
        self.assertFalse(vault.store("", "", "Geheim123!"))
        self.assertEqual(self.fake.store, {})

    def test_delete_existing_entry(self):
        vault.store("ABC123", "10.0.0.1", "Geheim123!")
        self.assertTrue(vault.delete("ABC123", "10.0.0.1"))
        self.assertEqual(vault.fetch("ABC123", "10.0.0.1"), "")

    def test_delete_missing_entry_is_not_an_error(self):
        self.assertTrue(vault.delete("NOPE", ""))

    def test_serial_and_ip_use_separate_entries(self):
        vault.store("ABC123", "10.0.0.1", "SerialPw")
        vault.store("", "10.0.0.2", "IpPw")
        self.assertEqual(vault.fetch("ABC123", "10.0.0.9"), "SerialPw")   # IP wechselt, Serial bleibt gültig
        self.assertEqual(vault.fetch("", "10.0.0.2"), "IpPw")


class TestUnavailable(unittest.TestCase):
    """Ohne Schlüsselbund dürfen store()/fetch()/delete() nie werfen, nur False/"" liefern."""

    def setUp(self):
        patcher = patch.object(vault, "available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_store_returns_false(self):
        self.assertFalse(vault.store("ABC123", "10.0.0.1", "Geheim123!"))

    def test_fetch_returns_empty_string(self):
        self.assertEqual(vault.fetch("ABC123", "10.0.0.1"), "")

    def test_delete_returns_false(self):
        self.assertFalse(vault.delete("ABC123", "10.0.0.1"))


if __name__ == "__main__":
    unittest.main()
