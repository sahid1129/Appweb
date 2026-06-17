# tests/test_admin_help.py
"""Tests for the public /api/auth/admin/help diagnostic endpoint.

The endpoint tells the operator which recovery path is available
(master key configured or not) and gives step-by-step instructions.
It must be public so the operator can probe it from a shell without
a session.
"""
import unittest

import requests

from tests._server import Server


class AdminHelpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start()  # no admin_key
        cls.base = cls.server.base

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_endpoint_is_public(self):
        r = requests.get(f"{self.base}/api/auth/admin/help", timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        # No master key on this server.
        self.assertFalse(body["master_key_configured"])
        # The endpoint must include instructions for the operator.
        self.assertIn("instructions", body)
        self.assertIn("if_master_key_missing", body["instructions"])
        self.assertIn("alternative_no_master_key", body["instructions"])


class AdminHelpWithKeyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start(admin_key="some-test-key")
        cls.base = cls.server.base

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_reports_master_key_configured(self):
        r = requests.get(f"{self.base}/api/auth/admin/help", timeout=5)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["master_key_configured"])
        self.assertIn("if_master_key_configured", body["instructions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
