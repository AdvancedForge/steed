"""The built-in password vault: sealing, keys, one-time codes and imports."""

import json
import tempfile
import unittest
from pathlib import Path

import nacl.utils

from browser import password_vault as pv


class FakeKeys:
    def __init__(self, key=None):
        self.value = key or nacl.utils.random(32)

    def key(self, create=True):
        return self.value


class NoKeyring:
    def key(self, create=True):
        return None


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "passwords.vault"
        self.keys = FakeKeys()

    def tearDown(self):
        self.directory.cleanup()

    def vault(self, keys=None):
        vault = pv.PasswordVault(self.path, keys or self.keys)
        if not vault.needs_master_password():
            vault.unlock()
        return vault

    def test_logins_are_sealed_on_disk_and_reopen_with_the_keyring_key(self):
        vault = self.vault()
        login = vault.save("https://Example.org/login?next=1", "ada", "s3cret-PASSWORD")
        self.assertEqual(login.origin, "https://example.org")
        raw = self.path.read_text()
        self.assertNotIn("s3cret-PASSWORD", raw)
        # Not the short name itself: random ciphertext can contain "ada".
        self.assertNotIn('"ada"', raw)
        self.assertNotIn("example.org", raw)
        self.assertEqual(json.loads(raw)["kdf"], "keyring")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        again = self.vault()
        self.assertEqual(
            again.logins("https://example.org")[0].password, "s3cret-PASSWORD"
        )
        wrong = pv.PasswordVault(self.path, FakeKeys())
        with self.assertRaises(pv.VaultLocked):
            wrong.unlock()

    def test_master_password_when_there_is_no_keyring(self):
        vault = pv.PasswordVault(self.path, NoKeyring())
        self.assertTrue(vault.needs_master_password())
        with self.assertRaises(pv.VaultLocked):
            vault.unlock()
        vault.unlock("correct horse battery")
        vault.save("https://example.org", "ada", "pw-one-two")
        reopened = pv.PasswordVault(self.path, NoKeyring())
        with self.assertRaises(pv.VaultLocked):
            reopened.unlock("wrong")
        reopened.unlock("correct horse battery")
        self.assertEqual(len(reopened.logins()), 1)
        # A master-password vault stays one even once a keyring appears.
        self.assertTrue(pv.PasswordVault(self.path, FakeKeys()).needs_master_password())

    def test_same_site_and_username_updates_and_sites_stay_separate(self):
        vault = self.vault()
        first = vault.save("https://example.org", "ada", "old-password")
        updated = vault.save("https://example.org/other", "ada", "new-password")
        self.assertEqual(first.id, updated.id)
        self.assertEqual(updated.password, "new-password")
        vault.save("https://example.org", "grace", "pw-grace-1")
        vault.save("http://example.org", "ada", "pw-plain-http")
        vault.save("https://example.org:8443", "ada", "pw-port")
        self.assertEqual(len(vault.logins("https://example.org")), 2)
        self.assertEqual(len(vault.logins()), 4)
        self.assertEqual(
            [login.username for login in vault.logins(query="grace")], ["grace"]
        )
        vault.touch(first.id)
        self.assertEqual(vault.logins("https://example.org")[0].id, first.id)
        vault.delete(first.id)
        with self.assertRaises(pv.VaultError):
            vault.get(first.id)
        for bad in (
            "file:///etc/passwd",
            "ftp://example.org",
            "https://u:p@example.org",
        ):
            with self.assertRaises(pv.VaultError):
                vault.save(bad, "ada", "password")
        with self.assertRaises(pv.VaultError):
            vault.save("https://example.org", "ada", "")

    def test_never_save_list(self):
        vault = self.vault()
        vault.never_save("https://bank.example/login")
        self.assertTrue(vault.never_saves("https://bank.example"))
        vault.never_save("https://bank.example", never=False)
        self.assertFalse(vault.never_saves("https://bank.example"))

    def test_totp_matches_rfc_6238_vectors(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
        self.assertEqual(pv.totp_code(secret, now=59)[0], "287082")
        uri = f"otpauth://totp/Example:ada?secret={secret}&digits=8"
        self.assertEqual(pv.totp_code(uri, now=1111111109)[0], "07081804")
        self.assertEqual(pv.totp_code(secret, now=59)[1], 1)
        for bad in ("not base32!", "otpauth://hotp/x?secret=GEZDGNBV", ""):
            with self.assertRaises(pv.VaultError):
                pv.totp_code(bad)
        vault = self.vault()
        with self.assertRaises(pv.VaultError):
            vault.save("https://example.org", "ada", "pw-x", totp="nope!")

    def test_generated_passwords_mix_character_classes(self):
        for _ in range(20):
            password = pv.generate_password()
            self.assertEqual(len(password), 20)
            self.assertTrue(any(c.isupper() for c in password))
            self.assertTrue(any(c.isdigit() for c in password))
        with self.assertRaises(pv.VaultError):
            pv.generate_password(8)

    def test_imports_common_exports(self):
        vault = self.vault()
        keepass = (
            '"Group","Title","Username","Password","URL","Notes","TOTP"\n'
            '"Root","Mail","ada","mail-pw","https://mail.example/","","GEZDGNBVGY3TQOJQ"\n'
            '"Root","No site","x","y","","",""\n'
        )
        self.assertEqual(vault.import_csv(keepass), (1, 0, 1))
        self.assertTrue(vault.logins("https://mail.example")[0].totp)
        chrome = "name,url,username,password,note\nShop,https://shop.example/a,ada,shop-pw,\n"
        firefox = (
            "url,username,password,httpRealm,formActionOrigin\n"
            "https://mail.example,ada,new-mail-pw,,\n"
        )
        bitwarden = (
            "folder,favorite,type,name,notes,fields,reprompt,login_uri,login_username,"
            "login_password,login_totp\n,,login,Code,,,,code.example,ada,code-pw,\n"
        )
        self.assertEqual(vault.import_csv(chrome), (1, 0, 0))
        self.assertEqual(vault.import_csv(firefox), (0, 1, 0))
        self.assertEqual(vault.import_csv(bitwarden), (1, 0, 0))
        self.assertEqual(vault.logins("https://code.example")[0].password, "code-pw")
        self.assertEqual(
            vault.logins("https://mail.example")[0].password, "new-mail-pw"
        )
        with self.assertRaises(pv.VaultError):
            vault.import_csv("a,b\n1,2\n")
        exported = vault.export_csv()
        self.assertIn("shop-pw", exported)
        fresh = pv.PasswordVault(Path(self.directory.name) / "other.vault", FakeKeys())
        fresh.unlock()
        self.assertEqual(fresh.import_csv(exported)[0], 3)

    def test_damaged_inputs_raise_vault_errors_not_crashes(self):
        for bad in (
            "otpauth://totp/x?secret=GEZDGNBV&digits=x",
            "otpauth://totp/x?secret=GEZDGNBV&period=1e3",
        ):
            with self.assertRaises(pv.VaultError):
                pv.totp_code(bad)
        vault = pv.PasswordVault(self.path, NoKeyring())
        with self.assertRaises(pv.VaultLocked):
            vault.unlock("short")
        vault.unlock("long enough master")
        header = json.loads(self.path.read_text())
        self.assertEqual(header["opslimit"], pv.OPSLIMIT)
        del header["salt"]
        self.path.write_text(json.dumps(header))
        with self.assertRaises(pv.VaultError):
            pv.PasswordVault(self.path, NoKeyring()).unlock("long enough master")
        header["salt"] = "AAAA"
        header["opslimit"] = 1
        self.path.write_text(json.dumps(header))
        with self.assertRaises(pv.VaultError):
            pv.PasswordVault(self.path, NoKeyring()).unlock("long enough master")

    def test_locked_vault_refuses_access(self):
        vault = pv.PasswordVault(self.path, self.keys)
        with self.assertRaises(pv.VaultLocked):
            vault.logins()
        vault.unlock()
        vault.lock()
        with self.assertRaises(pv.VaultLocked):
            vault.save("https://example.org", "a", "b")


if __name__ == "__main__":
    unittest.main()


class AutofillVaultTests(unittest.TestCase):
    """Addresses and cards live sealed in the same vault as logins."""

    # A synthetic test number (it passes the Luhn check; no real card).
    CARD = "4111 1111 1111 1111"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "passwords.vault"
        self.keys = FakeKeys()

    def tearDown(self):
        self.directory.cleanup()

    def vault(self):
        vault = pv.PasswordVault(self.path, self.keys)
        vault.unlock()
        return vault

    def test_cards_and_addresses_are_sealed_and_reopen(self):
        vault = self.vault()
        card = vault.save_autofill(
            "cards",
            {
                "number": self.CARD,
                "name": "Ada Example",
                "exp_month": "3",
                "exp_year": "29",
            },
        )
        self.assertEqual(card["number"], "4111111111111111")
        self.assertEqual((card["exp_month"], card["exp_year"]), ("03", "2029"))
        self.assertEqual(pv.card_label(card), "Card ending 1111")
        vault.save_autofill(
            "addresses",
            {
                "name": "Ada Example",
                "street": "1 Test Lane\nFlat 2",
                "city": "Exampleton",
                "postal": "EX1 2AB",
            },
        )
        raw = self.path.read_text()
        for secret in ("4111111111111111", "Test Lane", "Exampleton"):
            self.assertNotIn(secret, raw)
        again = self.vault()
        self.assertEqual(again.autofill("cards")[0]["number"], "4111111111111111")
        address = again.autofill("addresses")[0]
        self.assertEqual(pv.address_label(address), "Ada Example, 1 Test Lane")
        self.assertNotIn("csc", again.autofill("cards")[0])

    def test_invalid_cards_are_refused_with_a_message(self):
        vault = self.vault()
        with self.assertRaisesRegex(pv.VaultError, "card number"):
            vault.save_autofill("cards", {"number": "4111 1111 1111 1112"})
        with self.assertRaisesRegex(pv.VaultError, "expiry"):
            vault.save_autofill("cards", {"number": self.CARD, "exp_month": "13"})
        with self.assertRaisesRegex(pv.VaultError, "part of the address"):
            vault.save_autofill("addresses", {})
        self.assertEqual(vault.autofill("cards"), [])

    def test_same_card_or_address_updates_instead_of_copying(self):
        vault = self.vault()
        first = vault.save_autofill("cards", {"number": self.CARD, "exp_month": "1"})
        second = vault.save_autofill(
            "cards", {"number": self.CARD, "exp_month": "2", "nickname": "Work"}
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(vault.autofill("cards")), 1)
        self.assertEqual(pv.card_label(second), "Work (ending 1111)")
        vault.delete_autofill("cards", first["id"])
        self.assertEqual(vault.autofill("cards"), [])

    def test_sections_from_a_newer_steed_survive_a_write(self):
        vault = self.vault()
        vault.save("https://example.org", "ada", "s3cret-PASSWORD")
        # As a newer Steed might have left it: a section this one doesn't know.
        vault._contents.extra = {"future_section": [{"kept": True}]}
        vault._write()
        again = self.vault()
        again.save("https://example.com", "bea", "an0ther-PASSWORD")
        reopened = self.vault()
        self.assertEqual(reopened._contents.extra, {"future_section": [{"kept": True}]})
        self.assertEqual(len(reopened.logins()), 2)
