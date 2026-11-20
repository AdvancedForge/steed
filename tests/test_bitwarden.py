"""Bitwarden as a login source, through a synthetic bw command line."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from services.bitwarden import Bitwarden, BitwardenError, Logins

FAKE = str(Path(__file__).parent / "fixtures" / "fake_bw.py")
ORIGIN = "https://example.test"


class BitwardenTests(unittest.TestCase):
    def test_unlock_lists_exact_sites_and_locks(self):
        bitwarden = Bitwarden(FAKE)
        self.assertTrue(bitwarden.available)
        self.assertEqual(bitwarden.logins(ORIGIN), [])  # Locked: nothing.
        with self.assertRaisesRegex(BitwardenError, "did not unlock"):
            bitwarden.unlock("wrong")
        with patch("subprocess.run", wraps=__import__("subprocess").run) as run:
            bitwarden.unlock("synthetic-master")
        # The master password goes in the environment, never the command line.
        self.assertNotIn("synthetic-master", " ".join(run.call_args.args[0]))
        self.assertTrue(bitwarden.unlocked)
        found = bitwarden.logins(ORIGIN)
        self.assertEqual([login.id for login in found], ["bw:item-1"])
        self.assertEqual(found[0].username, "synthetic-user@example.test")
        self.assertNotIn("STEED_BW_PASSWORD", os.environ)
        bitwarden.lock()
        self.assertFalse(bitwarden.unlocked)
        self.assertEqual(bitwarden.logins(ORIGIN), [])

    def test_steeds_vault_and_bitwarden_together(self):
        class Vault:
            touched = []

            def logins(self, origin):
                return ["own"]

            def touch(self, login_id):
                self.touched.append(login_id)

        bitwarden = Bitwarden(FAKE)
        bitwarden.unlock("synthetic-master")
        vault = Vault()
        both = Logins(vault, bitwarden)
        found = both.logins(ORIGIN)
        self.assertEqual(found[0], "own")
        self.assertEqual(found[1].id, "bw:item-1")
        both.touch("bw:item-1")
        both.touch("own-id")
        self.assertEqual(vault.touched, ["own-id"])
        bitwarden.session = "expired"  # Bitwarden locked itself meanwhile.
        bitwarden.cache.clear()
        self.assertEqual(Logins(None, bitwarden).logins(ORIGIN), [])


if __name__ == "__main__":
    unittest.main()
