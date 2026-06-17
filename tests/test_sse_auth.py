# tests/test_sse_auth.py
"""Regression test for the SSE auth fix (bug B10).

The /api/events/stream endpoint must reject any request that does not
present a valid session token (header or query param).
"""
import json
import unittest

import requests

from tests._server import Server


class SseAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.start()
        cls.base = cls.server.base
        # Bootstrap an admin + login to obtain a valid session token.
        rr = requests.post(
            f"{cls.base}/api/auth/register",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "_test_sse_user", "password": "pass1"}),
            timeout=5,
        )
        if rr.status_code != 200:
            # Already registered from a prior run that didn't clean up.
            pass
        lr = requests.post(
            f"{cls.base}/api/auth/login",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "_test_sse_user", "password": "pass1"}),
            timeout=5,
        )
        cls.valid_token = lr.json()["token"]

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_sse_without_token_returns_401(self):
        r = requests.get(f"{self.base}/api/events/stream", timeout=3, stream=True)
        r.close()
        self.assertIn(r.status_code, (401, 403),
                      f"Expected 401/403, got {r.status_code}")
        self.assertNotEqual(r.status_code, 200)

    def test_sse_with_invalid_query_token_returns_401(self):
        r = requests.get(
            f"{self.base}/api/events/stream?token=deadbeef" * 8,
            timeout=3, stream=True,
        )
        r.close()
        self.assertIn(r.status_code, (401, 403))
        self.assertNotEqual(r.status_code, 200)

    def test_sse_with_invalid_header_token_returns_401(self):
        r = requests.get(
            f"{self.base}/api/events/stream",
            headers={"X-Session-Token": "deadbeef" * 8},
            timeout=3, stream=True,
        )
        r.close()
        self.assertIn(r.status_code, (401, 403))
        self.assertNotEqual(r.status_code, 200)

    def test_sse_with_valid_query_token_connects(self):
        with requests.get(
            f"{self.base}/api/events/stream",
            params={"token": self.valid_token},
            timeout=5, stream=True,
        ) as r:
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
            chunk = next(r.iter_content(chunk_size=128), b"")
            self.assertIn(b"connected", chunk.lower(),
                          f"Expected 'connected' event, got: {chunk!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
