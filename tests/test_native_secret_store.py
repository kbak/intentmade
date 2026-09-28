"""Test the installed native encrypted store, not a mocked versioned interface."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import sandbox
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.utils.cipher import Cipher


class NativeSecretStoreTests(unittest.TestCase):
    def test_refresh_preserves_other_secrets_and_encryption(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSecretsStore(temporary, Cipher("fixture-encryption-key"))
            store.set_secret("CODEX_AUTH_JSON", "old-fixture-login")
            store.set_secret("unrelated", "keep-fixture-value")
            value, version = sandbox.load_credential(store)
            sandbox.sync_credential(store, version, value, "new-fixture-login")
            self.assertEqual(store.get_secret("CODEX_AUTH_JSON"), "new-fixture-login")
            self.assertEqual(store.get_secret("unrelated"), "keep-fixture-value")
            contents = (Path(temporary) / "secrets.json").read_text()
            self.assertNotIn("new-fixture-login", contents)
            self.assertNotIn("keep-fixture-value", contents)

    def test_concurrent_change_or_deletion_never_gets_overwritten(self):
        for delete in (False, True):
            with self.subTest(delete=delete), tempfile.TemporaryDirectory() as temporary:
                store = FileSecretsStore(temporary, Cipher("fixture-encryption-key"))
                store.set_secret("CODEX_AUTH_JSON", "original")
                old, version = sandbox.load_credential(store)
                if delete:
                    store.delete_secret("CODEX_AUTH_JSON")
                else:
                    store.set_secret("CODEX_AUTH_JSON", "concurrent-login")
                sandbox.sync_credential(store, version, old, "stale-worker-refresh")
                self.assertEqual(
                    store.get_secret("CODEX_AUTH_JSON"), None if delete else "concurrent-login"
                )

    def test_corrupt_store_is_never_replaced_and_missing_login_cannot_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSecretsStore(temporary, Cipher("fixture-encryption-key"))
            with self.assertRaises(RuntimeError):
                sandbox.load_credential(store)
            store.set_secret("CODEX_AUTH_JSON", "original")
            old, version = sandbox.load_credential(store)
            path = Path(temporary) / "secrets.json"
            path.write_text("{invalid")
            with self.assertRaises(RuntimeError):
                sandbox.sync_credential(store, version, old, "refresh")
            self.assertEqual(path.read_text(), "{invalid")

    def test_credentials_use_native_versioned_api(self):
        store = Mock()
        store.load_versioned_secret.return_value = ("old", 7)
        self.assertEqual(sandbox.load_credential(store), ("old", 7))
        sandbox.sync_credential(store, 7, "old", "new")
        store.replace_versioned_secret.assert_called_once_with("CODEX_AUTH_JSON", 7, "new")
        store.get_secret.assert_not_called()
        store.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
