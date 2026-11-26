"""Offline sign-in password recovery: `flask --app app reset-password USERNAME --password-file PATH`.

Synthetic accounts and credentials only; the CLI runs in-process through Flask's test runner.
"""

import os
import sqlite3
import stat
from contextlib import closing
from unittest import mock

from tests.test_accounts import (ALICE_PW, BOB_KEY, BOB_PW, BOB_SECRET, BOB_VAULT,
                                 AccountTestCase)

DONE = "Password reset. All sign-in sessions revoked."
SENTINEL_PW = "synthetic-generated-password-sentinel"


class PasswordRecoveryTests(AccountTestCase):
    def reset(self, *args):
        return self.app.test_cli_runner().invoke(args=["reset-password", *args])

    def password_file(self, name="new-password"):
        return os.path.join(self.tmp.name, name)

    def hash_of(self, username):
        with closing(sqlite3.connect(self.db_path)) as db:
            return db.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()[0]

    def test_resets_one_user_revokes_logins_and_locks_vault(self):
        alice, bob = self.pair()
        bob_id = bob.user["id"]
        bob2 = self.login("bob", BOB_PW)
        self.init_vault(bob, BOB_VAULT)  # unlocked in this process
        self.save_bedrock(bob, BOB_KEY, BOB_SECRET)
        vault_path = os.path.join(self.tmp.name, "users", str(bob_id), "settings.vault")
        with open(vault_path, "rb") as fh:
            ciphertext = fh.read()
        alice_hash = self.hash_of("alice")
        path = self.password_file()

        result = self.reset("Bob", "--password-file", path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(DONE, result.output)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as fh:
            password = fh.read().strip()
        self.assertGreaterEqual(len(password), 24)
        new_hash = self.hash_of("bob")
        for secret in (password, new_hash, BOB_PW):
            self.assertNotIn(secret, result.output)

        for stale in (bob, bob2):  # every Bob login is revoked
            self.assertEqual(stale.get("/api/settings").status_code, 401)
        self.login("bob", BOB_PW, expect=401)
        bob3 = self.login("bob", password)  # the file's password works through the real login API
        vault = self.app.extensions["sous_vaults"]["vaults"][bob_id]
        self.assertTrue(vault.locked)
        with open(vault_path, "rb") as fh:
            self.assertEqual(fh.read(), ciphertext)
        self.assertEqual(self.settings(bob3)["vault"], {"initialized": True, "locked": True})
        resp = bob3.send("POST", "/api/settings/unlock", {"passphrase": BOB_VAULT})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(self.settings(bob3)["bedrock"]["has_credentials"])

        self.assertEqual(self.hash_of("alice"), alice_hash)  # other account untouched
        self.assertEqual(alice.get("/api/settings").status_code, 200)
        self.login("alice", ALICE_PW)

    def test_unknown_or_invalid_user_creates_no_file(self):
        alice, bob = self.pair()
        before = self.db_dump()
        for name in ("nobody", "has space", "ab"):
            path = self.password_file(f"pw-{name}")
            result = self.reset(name, "--password-file", path)
            self.assertNotEqual(result.exit_code, 0, name)
            self.assertFalse(os.path.lexists(path), name)
        result = self.reset("bob")  # --password-file is required
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(self.db_dump(), before)
        self.assertEqual(bob.get("/api/settings").status_code, 200)

    def test_existing_path_or_symlink_is_refused_without_changes(self):
        alice, bob = self.pair()
        existing = self.password_file("existing")
        with open(existing, "w") as fh:
            fh.write("keep me")
        dangling_target = self.password_file("dangling-target")
        dangling = self.password_file("dangling")
        os.symlink(dangling_target, dangling)
        linked = self.password_file("linked")
        os.symlink(existing, linked)
        before = self.db_dump()
        for path in (existing, dangling, linked):
            result = self.reset("bob", "--password-file", path)
            self.assertNotEqual(result.exit_code, 0, path)
            self.assertNotIn(DONE, result.output)
        self.assertEqual(self.db_dump(), before)
        with open(existing) as fh:
            self.assertEqual(fh.read(), "keep me")
        self.assertFalse(os.path.lexists(dangling_target))
        self.assertTrue(os.path.islink(dangling) and os.path.islink(linked))
        self.assertEqual(bob.get("/api/settings").status_code, 200)
        self.login("bob", BOB_PW)

    def test_database_failure_removes_created_file_and_keeps_password(self):
        alice, bob = self.pair()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("CREATE TRIGGER synthetic_failure BEFORE UPDATE ON users"
                       " BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
            db.commit()
        before = self.db_dump()
        path = self.password_file()
        with mock.patch("accounts.secrets.token_urlsafe", return_value=SENTINEL_PW):
            result = self.reset("bob", "--password-file", path)
        self.assertNotEqual(result.exit_code, 0)
        self.assertNotIn(SENTINEL_PW, result.output)
        self.assertNotIn(DONE, result.output)
        self.assertFalse(os.path.lexists(path))
        self.assertEqual(self.db_dump(), before)
        self.assertEqual(bob.get("/api/settings").status_code, 200)

    def test_hash_failure_creates_no_file(self):
        alice, bob = self.pair()
        before = self.db_dump()
        path = self.password_file()
        with mock.patch("accounts.generate_password_hash", side_effect=ValueError("synthetic")):
            result = self.reset("bob", "--password-file", path)
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(os.path.lexists(path))
        self.assertEqual(self.db_dump(), before)
