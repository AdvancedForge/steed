"""The built-in password vault: sealing, keys, one-time codes and imports."""

import json
import tempfile
import unittest
from pathlib import Path

import nacl.utils

import password_vault as pv


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
        self.assertNotIn("ada", raw)
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
