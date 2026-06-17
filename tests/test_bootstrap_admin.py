# tests/test_bootstrap_admin.py
"""Tests for the bootstrap admin feature.

When the server starts with an empty .users.json, it should create
a default admin so the operator can immediately log in. This is
critical for hosted deployments (Render, etc.) where the persistent
disk may be empty after a redeploy.

The bootstrap is configurable via env vars:
- BOOTSTRAP_ADMIN_USERNAME (default: "_admin")
- BOOTSTRAP_ADMIN_PASSWORD (default: "admin")
- BOOTSTRAP_ADMIN_DISABLED=1 to skip the bootstrap
"""
import json
import os
import socket
import unittest
from pathlib import Path

import requests

from tests._server import Server, REPO_ROOT


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _BootstrapBase(unittest.TestCase):
    """Each subclass starts its own server with its own bootstrap config."""

    @classmethod
    def setUpClass(cls):
        # Ensure no leftover .users.json from previous tests
        for f in (".users.json", ".session_tokens.json",
                  ".failed_logins.json", ".oauth_states.json"):
            p = REPO_ROOT / f
            if p.exists():
                p.unlink()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "server") and cls.server:
            cls.server.stop()
        # Final cleanup so we don't leak a bootstrap admin into the
        # next test that runs without BOOTSTRAP_ADMIN_DISABLED.
        for f in (".users.json", ".session_tokens.json",
                  ".failed_logins.json", ".oauth_states.json"):
            p = REPO_ROOT / f
            if p.exists():
                p.unlink()


class BootstrapDisabledTest(_BootstrapBase):
    """When BOOTSTRAP_ADMIN_DISABLED=1, no admin is auto-created."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = Server()
        cls.server.start(enable_bootstrap=False)
        cls.base = cls.server.base

    def test_no_users_after_startup(self):
        r = requests.get(f"{self.base}/api/auth/status", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["has_users"],
                         "Bootstrap should be disabled for this server")

    def test_auth_status_does_not_bootstrap(self):
        # The lazy fallback in auth_status must also respect the flag.
        for _ in range(3):
            r = requests.get(f"{self.base}/api/auth/status", timeout=5)
            self.assertFalse(r.json()["has_users"])
        # No .users.json should have been written.
        self.assertFalse((REPO_ROOT / ".users.json").exists())


class BootstrapDefaultTest(_BootstrapBase):
    """With no env var override, the server creates _admin/admin."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = Server()
        cls.server.start(enable_bootstrap=True)
        cls.base = cls.server.base

    def test_default_admin_present_in_list(self):
        r = requests.get(f"{self.base}/api/auth/users", timeout=5)
        self.assertEqual(r.status_code, 200)
        usernames = [u["username"] for u in r.json()["users"]]
        self.assertIn("_admin", usernames)

    def test_default_admin_login_succeeds(self):
        r = requests.post(
            f"{self.base}/api/auth/login", timeout=5,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "_admin", "password": "admin"}),
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["is_admin"])
        self.assertEqual(body["username"], "_admin")

    def test_status_reports_has_users(self):
        r = requests.get(f"{self.base}/api/auth/status", timeout=5)
        self.assertTrue(r.json()["has_users"])

    def test_bootstrap_admin_can_create_more_users(self):
        # Once logged in as the bootstrap admin, we should be able to
        # register additional users normally.
        lr = requests.post(
            f"{self.base}/api/auth/login", timeout=5,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "_admin", "password": "admin"}),
        )
        admin_token = lr.json()["token"]
        r = requests.post(
            f"{self.base}/api/auth/register", timeout=5,
            headers={"Content-Type": "application/json",
                     "X-Session-Token": admin_token},
            data=json.dumps({"username": "_test_extra", "password": "pass1"}),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_admin"])


class BootstrapCustomCredsTest(_BootstrapBase):
    """Custom BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = Server()
        cls.server.start(
            enable_bootstrap=True,
            bootstrap_username="boss",
            bootstrap_password="topsecret",
        )
        cls.base = cls.server.base

    def test_custom_username_in_list(self):
        r = requests.get(f"{self.base}/api/auth/users", timeout=5)
        usernames = [u["username"] for u in r.json()["users"]]
        self.assertIn("boss", usernames)
        self.assertNotIn("_admin", usernames,
                        "Default _admin must NOT be created when env var is set")

    def test_custom_login_succeeds(self):
        r = requests.post(
            f"{self.base}/api/auth/login", timeout=5,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "boss", "password": "topsecret"}),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_admin"])


class BootstrapLazyFallbackTest(_BootstrapBase):
    """If .users.json is empty when /api/auth/status is called, the
    bootstrap kicks in. This guards against persistent disks that
    mount after the import-time migration.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Pre-create an empty .users.json so the import-time bootstrap
        # sees "users" exists but is empty. Wait, that triggers the
        # legacy migration which adds nothing useful. Instead, we let
        # the import-time bootstrap create the admin, then DELETE the
        # file before the test runs, and finally hit /api/auth/status
        # to verify the lazy fallback re-creates it.
        cls.server = Server()
        cls.server.start(enable_bootstrap=True)
        cls.base = cls.server.base

    def test_lazy_fallback_creates_admin(self):
        # Confirm the admin exists first.
        r = requests.get(f"{self.base}/api/auth/users", timeout=5)
        self.assertTrue(any(u["username"] == "_admin" for u in r.json()["users"]))

        # Wipe .users.json to simulate a fresh persistent disk.
        (REPO_ROOT / ".users.json").unlink()

        # The next /api/auth/status call must trigger the lazy fallback
        # and re-create the admin. (In a real Render redeploy this is
        # exactly the situation: the disk is empty, the import-time
        # migration ran before the disk was mounted, and the first
        # request from a user finds nothing.)
        r = requests.get(f"{self.base}/api/auth/status", timeout=5)
        self.assertTrue(r.json()["has_users"],
                        "Lazy bootstrap in auth_status did not fire")

        # The admin should be usable.
        r = requests.post(
            f"{self.base}/api/auth/login", timeout=5,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "_admin", "password": "admin"}),
        )
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
