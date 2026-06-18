# tests/test_auth.py
"""End-to-end tests for the auth API.

Run via ``python -m unittest tests.test_auth -v``.

Covers:
- /api/auth/status
- /api/auth/users (public, no hashes)
- /api/auth/register (first user = admin; subsequent requires admin token;
  rejects non-admin tokens, rejects duplicate usernames)
- /api/auth/login (success, wrong password, remember_me)
- /api/auth/users/{username} (admin delete, self protection)
- /api/auth/verify
- X-Session-Token guard on /api/tree
- /api/auth/logout invalidates the token
- PBKDF2 backwards compatibility (B8 regression)
- Rate limiting (5 failures -> 15 min lockout)
"""
import json
import unittest
from pathlib import Path

import requests

from tests._server import Server, REPO_ROOT

# Users created by this suite. Tracked so tearDown can clean them up
# if a test fails partway through and leaves artifacts in .users.json.
CREATED_USERS: list = []


class _AuthBase(unittest.TestCase):
    """Common setup: spawn the server and back up the dev's local data."""

    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start()
        cls.base = cls.server.base
        # Each test class boots its own fresh server with a clean
        # .users.json. The first user we register becomes the admin for
        # the whole class — subclasses call cls._register_first(...) in
        # their setUpClass to provision it.
        CREATED_USERS.clear()

    @classmethod
    def _register_first(cls, username, password):
        """Register as the first user of a fresh server (becomes admin)."""
        if username not in CREATED_USERS:
            CREATED_USERS.append(username)
        return requests.post(
            f"{cls.base}/api/auth/register",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": username, "password": password}),
            timeout=5,
        )

    @classmethod
    def tearDownClass(cls):
        # Clean up any test users we created so we don't pollute the repo.
        # We need an admin token; use the first user we registered.
        try:
            for uname in list(CREATED_USERS):
                if uname == "_test_admin":
                    continue  # never delete the admin
                # Find an admin token by re-logging in.
                lr = requests.post(
                    f"{cls.server.base}/api/auth/login",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps({"username": "_test_admin", "password": "pass1"}),
                    timeout=5,
                )
                if lr.status_code != 200:
                    break
                admin_token = lr.json()["token"]
                requests.delete(
                    f"{cls.server.base}/api/auth/users/{uname}",
                    headers={"X-Session-Token": admin_token},
                    timeout=5,
                )
        except Exception:
            pass
        cls.server.stop()

    def _register(self, username, password, token=None):
        if username not in CREATED_USERS:
            CREATED_USERS.append(username)
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Session-Token"] = token
        return requests.post(
            f"{self.base}/api/auth/register",
            headers=h,
            data=json.dumps({"username": username, "password": password}),
            timeout=5,
        )

    def _login(self, username, password, remember=False):
        return requests.post(
            f"{self.base}/api/auth/login",
            headers={"Content-Type": "application/json"},
            data=json.dumps({
                "username": username, "password": password, "remember_me": remember
            }),
            timeout=5,
        )


class AuthFlowTest(_AuthBase):
    def test_01_status_reports_has_users(self):
        r = requests.get(f"{self.base}/api/auth/status", timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("has_users", body)

    def test_02_register_first_user_becomes_admin(self):
        r = self._register("_test_admin", "pass1")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["is_admin"])
        self.assertGreater(len(body["token"]), 20)

    def test_03_register_second_user_requires_admin(self):
        # No token -> 403
        r = self._register("_test_user1", "pass2")
        self.assertEqual(r.status_code, 403)

        # With admin token -> 200
        lr = self._login("_test_admin", "pass1")
        self.assertEqual(lr.status_code, 200, lr.text)
        admin_token = lr.json()["token"]
        r = self._register("_test_user1", "pass2", token=admin_token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_admin"])

    def test_04_list_users_has_no_password_hashes(self):
        r = requests.get(f"{self.base}/api/auth/users", timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["has_users"])
        self.assertGreaterEqual(len(body["users"]), 2)
        for u in body["users"]:
            self.assertNotIn("password_hash", u)
            self.assertIn("username", u)
            self.assertIn("is_admin", u)

    def test_05_login_success(self):
        r = self._login("_test_admin", "pass1")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["is_admin"])
        self.assertIn("token", body)

    def test_06_login_wrong_password(self):
        # Use a dedicated user for failed-login checks so we don't burn
        # the admin's rate-limit budget for the rest of the suite.
        lr = self._login("_test_admin", "pass1")
        admin_token = lr.json()["token"]
        self._register("_test_wrong", "rightpw", token=admin_token)
        r = self._login("_test_wrong", "WRONG")
        self.assertEqual(r.status_code, 401)

    def test_07_min_password_length(self):
        lr = self._login("_test_admin", "pass1")
        admin_token = lr.json()["token"]
        r = self._register("_test_shorty", "abc", token=admin_token)
        self.assertEqual(r.status_code, 400)
        self.assertIn("4 caracteres", r.json()["detail"])

    def test_08_min_username_length(self):
        lr = self._login("_test_admin", "pass1")
        admin_token = lr.json()["token"]
        r = self._register("a", "longenough", token=admin_token)
        self.assertEqual(r.status_code, 400)
        self.assertIn("2 caracteres", r.json()["detail"])

    def test_09_session_token_guards_protected_endpoint(self):
        # /api/config is a cheap protected endpoint we can use as a proxy
        # for the auth guard. /api/tree would also work but is too slow
        # against the dev's actual workspace.
        r = requests.get(f"{self.base}/api/config", timeout=10)
        self.assertEqual(r.status_code, 401)
        lr = self._login("_test_admin", "pass1")
        token = lr.json()["token"]
        r = requests.get(
            f"{self.base}/api/config",
            headers={"X-Session-Token": token}, timeout=10,
        )
        self.assertEqual(r.status_code, 200)

    def test_10_verify_endpoint(self):
        lr = self._login("_test_admin", "pass1")
        token = lr.json()["token"]
        r = requests.get(f"{self.base}/api/auth/verify",
                         headers={"X-Session-Token": token}, timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["is_admin"])
        self.assertEqual(body["username"], "_test_admin")

        # Bogus token
        r = requests.get(f"{self.base}/api/auth/verify",
                         headers={"X-Session-Token": "deadbeef" * 8}, timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_11_admin_cannot_be_deleted(self):
        lr = self._login("_test_admin", "pass1")
        token = lr.json()["token"]
        r = requests.delete(
            f"{self.base}/api/auth/users/_test_admin",
            headers={"X-Session-Token": token}, timeout=5,
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("administrador", r.json()["detail"])

    def test_12_admin_can_delete_other_user(self):
        lr = self._login("_test_admin", "pass1")
        admin_token = lr.json()["token"]
        sr = self._register("_test_sacrificial", "pass3", token=admin_token)
        self.assertEqual(sr.status_code, 200, sr.text)
        r = requests.delete(
            f"{self.base}/api/auth/users/_test_sacrificial",
            headers={"X-Session-Token": admin_token}, timeout=5,
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_13_non_admin_cannot_delete(self):
        lr = self._login("_test_user1", "pass2")
        user_token = lr.json()["token"]
        r = requests.delete(
            f"{self.base}/api/auth/users/_test_admin",
            headers={"X-Session-Token": user_token}, timeout=5,
        )
        self.assertEqual(r.status_code, 403)

    def test_14_remember_me_returns_token(self):
        r1 = self._login("_test_admin", "pass1", remember=False)
        r2 = self._login("_test_admin", "pass1", remember=True)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        # Each login produces a fresh session token.
        self.assertNotEqual(r1.json()["token"], r2.json()["token"])

    def test_15_logout_invalidates_token(self):
        lr = self._login("_test_admin", "pass1")
        token = lr.json()["token"]
        # Sanity: token works (use /api/config to avoid slow /api/tree)
        r = requests.get(f"{self.base}/api/config",
                         headers={"X-Session-Token": token}, timeout=10)
        self.assertEqual(r.status_code, 200)
        # Logout
        r = requests.post(f"{self.base}/api/auth/logout",
                          headers={"X-Session-Token": token}, timeout=5)
        self.assertEqual(r.status_code, 200)
        # Same token no longer works
        r = requests.get(f"{self.base}/api/config",
                         headers={"X-Session-Token": token}, timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_16_register_second_user_requires_admin(self):
        """Bug fix: POST /api/auth/register must return 403 when the
        server already has at least one user and the request has no
        admin session token. The Phase 4 'create new user' flow on the
        client relies on this gate."""
        # The _test_admin user was created in setUpClass. A second
        # registration attempt without a token must be denied.
        r = self._register("_test_nobody", "pass1", token=None)
        self.assertEqual(r.status_code, 403)

    def test_17_register_second_user_with_admin_token_succeeds(self):
        """Phase 4 happy path: an authenticated admin can register a
        new user, and the response includes a session token so the
        new user could log in immediately if desired."""
        lr = self._login("_test_admin", "pass1")
        self.assertEqual(lr.status_code, 200)
        admin_token = lr.json()["token"]
        r = self._register("_test_phase4", "pass1", token=admin_token)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("success"))
        self.assertEqual(body.get("username"), "_test_phase4")
        self.assertFalse(body.get("is_admin"))
        self.assertTrue(body.get("token"))

    def test_18_register_with_non_admin_token_is_403(self):
        """A regular (non-admin) user must not be able to register
        others, even with a valid session token."""
        # Create a non-admin user using the admin token, then log in
        # as that user and try to register a third account.
        lr = self._login("_test_admin", "pass1")
        admin_token = lr.json()["token"]
        r = self._register("_test_regular", "pass1", token=admin_token)
        self.assertEqual(r.status_code, 200)
        regular_lr = self._login("_test_regular", "pass1")
        self.assertEqual(regular_lr.status_code, 200)
        regular_token = regular_lr.json()["token"]
        r = self._register("_test_should_fail", "pass1", token=regular_token)
        self.assertEqual(r.status_code, 403)

    def test_19_register_duplicate_username_is_400(self):
        """Trying to create a user whose name already exists returns
        400 with a clear detail message."""
        lr = self._login("_test_admin", "pass1")
        admin_token = lr.json()["token"]
        # _test_admin is already in the store; register it again.
        r = self._register("_test_admin", "pass1", token=admin_token)
        self.assertEqual(r.status_code, 400)
        self.assertIn("ya existe", r.json().get("detail", ""))


class Pbkdf2BackcompatTest(_AuthBase):
    """Bug B8 regression: legacy 100k-iteration hashes must still verify,
    and the next login should rehash them to 600k.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Provision the admin so the per-test register call works.
        cls._register_first("_test_legacy", "pass1")

    def test_legacy_hash_verifies_and_rehashes(self):
        # _test_legacy already exists from setUpClass. Manually rewrite
        # the stored hash to the legacy "salt:hash" form (no iteration
        # prefix → triggers the legacy code path in verify_password).
        users_path = REPO_ROOT / ".users.json"
        data = json.loads(users_path.read_text("utf-8"))
        key = "_test_legacy"
        from app.main import hash_password, PBKDF2_LEGACY_ITERATIONS
        legacy_hash = hash_password("pass1", iterations=PBKDF2_LEGACY_ITERATIONS)
        # legacy_hash = "100000:salt:hash" → strip the leading "100000:"
        salt_hex, hash_hex = legacy_hash.split(":")[1], legacy_hash.split(":")[2]
        data["users"][key]["password_hash"] = f"{salt_hex}:{hash_hex}"
        users_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")

        # Login should still succeed and trigger the opportunistic rehash.
        r = self._login("_test_legacy", "pass1")
        self.assertEqual(r.status_code, 200, r.text)

        # Verify the rehash upgraded the stored hash to 600k.
        data2 = json.loads(users_path.read_text("utf-8"))
        new_hash = data2["users"][key]["password_hash"]
        self.assertTrue(
            new_hash.startswith("600000:"),
            f"Expected rehash to 600000:, got {new_hash[:30]}",
        )

        # And the user can still log in with the same password afterwards.
        r = self._login("_test_legacy", "pass1")
        self.assertEqual(r.status_code, 200)


class RateLimitTest(_AuthBase):
    """Rate limiting: 5 failed attempts → 429 with remaining-seconds detail.

    Runs in a separate class so that an unrelated test's failed login does
    not consume attempts from the global .failed_logins.json store.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Register the admin (first user) so we can create the rate-limit
        # target as a non-admin.
        cls._register_first("_test_admin", "pass1")
        lr = cls._login(cls, "_test_admin", "pass1")
        assert lr.status_code == 200
        admin_token = lr.json()["token"]
        cls._register(cls, "_test_rl", "pass1", token=admin_token)
        if "_test_rl" not in CREATED_USERS:
            CREATED_USERS.append("_test_rl")

    def test_lockout_after_5_failures(self):
        # The rate limit is per IP+username. The _test_rl user was created
        # in setUpClass. Pre-warm with a successful login so the counter
        # starts at zero, then drive 5 failures to trigger the lockout.
        ok = self._login("_test_rl", "pass1")
        self.assertEqual(ok.status_code, 200)

        for _ in range(5):
            r = self._login("_test_rl", "WRONG")
            self.assertEqual(r.status_code, 401)
        r = self._login("_test_rl", "WRONG")
        self.assertEqual(r.status_code, 429)
        self.assertIn("segundos", r.json()["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
