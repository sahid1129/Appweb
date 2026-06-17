# tests/test_admin_reset_password.py
"""Tests for the master-key password recovery endpoint.

POST /api/auth/admin/reset-password is gated by the env var
RENDER_ADMIN_KEY. If the env var is not set, the endpoint returns
404 so the feature is invisible.

Rate limit: 5 attempts per IP per hour (per the implementation).
"""
import json
import unittest

import requests

from tests._server import Server, REPO_ROOT


ADMIN_KEY = "test-master-key-do-not-use-in-prod-1234567890"


def _register(base, username, password, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Session-Token"] = token
    return requests.post(
        f"{base}/api/auth/register",
        headers=h,
        data=json.dumps({"username": username, "password": password}),
        timeout=10,
    )


def _login(base, username, password):
    return requests.post(
        f"{base}/api/auth/login",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"username": username, "password": password}),
        timeout=10,
    )


class AdminKeyDisabledTest(unittest.TestCase):
    """Without RENDER_ADMIN_KEY the endpoint is hidden (404)."""

    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start()  # no admin_key passed
        cls.base = cls.server.base
        # Provision an admin so we have a valid target to reset.
        _register(cls.base, "_adm", "oldpassword")

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_endpoint_returns_404_when_feature_disabled(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json",
                     "X-Admin-Key": "anything"},
            data=json.dumps({"username": "_adm", "new_password": "newpass1"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 404)


class AdminKeyHappyPathTest(unittest.TestCase):
    """With RENDER_ADMIN_KEY set, reset works and the new password
    can be used to log in."""

    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start(admin_key=ADMIN_KEY)
        cls.base = cls.server.base
        _register(cls.base, "_adm", "oldpassword")

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_reset_password_with_correct_key(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json",
                     "X-Admin-Key": ADMIN_KEY},
            data=json.dumps({"username": "_adm", "new_password": "brandnew"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["username"], "_adm")

    def test_login_with_new_password_succeeds(self):
        # Self-contained: reset to "newpass_a" first, then verify both
        # the old and new passwords behave as expected. This avoids the
        # alphabetical-order trap with the test above.
        requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json",
                     "X-Admin-Key": ADMIN_KEY},
            data=json.dumps({"username": "_adm", "new_password": "newpass_a"}),
            timeout=10,
        )

        # Old password no longer works.
        r = _login(self.base, "_adm", "oldpassword")
        self.assertEqual(r.status_code, 401)

        # New password works.
        r = _login(self.base, "_adm", "newpass_a")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_admin"])


class AdminKeyAuthTest(unittest.TestCase):
    """The endpoint rejects requests without a valid key."""

    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start(admin_key=ADMIN_KEY)
        cls.base = cls.server.base
        _register(cls.base, "_adm", "oldpassword")

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_missing_key_returns_403(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "_adm", "new_password": "newpass1"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 403)

    def test_wrong_key_returns_403(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json",
                     "X-Admin-Key": "definitely-wrong"},
            data=json.dumps({"username": "_adm", "new_password": "newpass1"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 403)

    def test_unknown_user_returns_404(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json",
                     "X-Admin-Key": ADMIN_KEY},
            data=json.dumps({"username": "_ghost", "new_password": "newpass1"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 404)

    def test_short_password_returns_400(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json",
                     "X-Admin-Key": ADMIN_KEY},
            data=json.dumps({"username": "_adm", "new_password": "abc"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("4 caracteres", r.json()["detail"])


class AdminKeyRateLimitTest(unittest.TestCase):
    """5 wrong-key attempts in a row yield 429."""

    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start(admin_key=ADMIN_KEY)
        cls.base = cls.server.base
        _register(cls.base, "_adm", "oldpassword")

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_rate_limit_after_5_failed_attempts(self):
        # Fire 5 wrong attempts. The first 5 should all be 403.
        for _ in range(5):
            r = requests.post(
                f"{self.base}/api/auth/admin/reset-password",
                headers={"Content-Type": "application/json",
                         "X-Admin-Key": "wrong"},
                data=json.dumps({"username": "_adm", "new_password": "newpass1"}),
                timeout=10,
            )
            self.assertEqual(r.status_code, 403)
        # The 6th attempt should be rate-limited (429) since the
        # counter is checked on a wrong-key response.
        r = requests.post(
            f"{self.base}/api/auth/admin/reset-password",
            headers={"Content-Type": "application/json",
                     "X-Admin-Key": "wrong"},
            data=json.dumps({"username": "_adm", "new_password": "newpass1"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 429)


class AdminWipeUsersTest(unittest.TestCase):
    """Wipe all users via master key.

    This is the nuclear recovery path: when the operator has lost
    track of every account (e.g. wiped persistent disk + lost
    BOOTSTRAP_ADMIN_PASSWORD), they can call /api/auth/admin/wipe-users
    to delete the entire user store. The next /api/auth/status call
    re-runs the bootstrap and recreates _admin with the default
    password.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start(admin_key=ADMIN_KEY)
        cls.base = cls.server.base
        # Provision two users.
        _register(cls.base, "_adm", "oldpass")
        lr = _login(cls.base, "_adm", "oldpass")
        admin_token = lr.json()["token"]
        _register(cls.base, "_user1", "userpw", token=admin_token)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_wipe_requires_master_key(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/wipe-users", timeout=10,
        )
        self.assertEqual(r.status_code, 403)

    def test_wipe_rejects_wrong_key(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/wipe-users", timeout=10,
            headers={"X-Admin-Key": "wrong"},
        )
        self.assertEqual(r.status_code, 403)

    def test_wipe_deletes_all_users(self):
        # Self-contained: re-create the fixtures in case earlier
        # tests in this class already wiped them. This avoids the
        # alphabetical-order trap.
        _register(self.base, "_seed1", "seed1pw")
        lr = _login(self.base, "_seed1", "seed1pw")
        seed_token = lr.json()["token"]
        _register(self.base, "_seed2", "seed2pw", token=seed_token)

        # Sanity: 2 users
        r = requests.get(f"{self.base}/api/auth/users", timeout=5)
        self.assertEqual(len(r.json()["users"]), 2)

        r = requests.post(
            f"{self.base}/api/auth/admin/wipe-users", timeout=10,
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["deleted_users"], 2)

        # The user list is now empty.
        r = requests.get(f"{self.base}/api/auth/users", timeout=5)
        self.assertEqual(r.json()["users"], [])

    def test_wipe_clears_sessions_and_failed_logins(self):
        # Self-contained: re-create the fixtures.
        _register(self.base, "_seed1", "seed1pw")
        lr = _login(self.base, "_seed1", "seed1pw")
        # Generate a few failed-login records so the cleanup path is
        # actually exercised.
        for _ in range(3):
            requests.post(
                f"{self.base}/api/auth/login", timeout=5,
                headers={"Content-Type": "application/json"},
                data=json.dumps({"username": "_seed1", "password": "WRONG"}),
            )

        # Wipe.
        r = requests.post(
            f"{self.base}/api/auth/admin/wipe-users", timeout=10,
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        self.assertEqual(r.status_code, 200)

        # The session tokens file should be gone (or empty).
        sessions_path = REPO_ROOT / ".session_tokens.json"
        failed_path = REPO_ROOT / ".failed_logins.json"
        if sessions_path.exists():
            data = json.loads(sessions_path.read_text("utf-8") or "{}")
            self.assertEqual(data.get("tokens", {}), {})
        if failed_path.exists():
            data = json.loads(failed_path.read_text("utf-8") or "{}")
            self.assertEqual(data, {})


class AdminWipeDisabledTest(unittest.TestCase):
    """Without RENDER_ADMIN_KEY the wipe endpoint is hidden."""

    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start()  # no admin_key
        cls.base = cls.server.base
        _register(cls.base, "_adm", "oldpass")

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_wipe_returns_404_when_feature_disabled(self):
        r = requests.post(
            f"{self.base}/api/auth/admin/wipe-users", timeout=10,
            headers={"X-Admin-Key": "anything"},
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
