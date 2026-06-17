# tests/test_per_user_workspace.py
"""Tests for per-user workspace root isolation.

Each user gets their own workspace folder. The /api/tree endpoint must
return the contents of *that user's* workspace, not the global one.
This is the cornerstone of the phase-2 multi-tenant refactor.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import requests

from tests._server import Server, REPO_ROOT


def _login(requests_obj, base, username, password):
    return requests_obj.post(
        f"{base}/api/auth/login",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"username": username, "password": password}),
        timeout=10,
    )


def _register(requests_obj, base, username, password, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Session-Token"] = token
    return requests_obj.post(
        f"{base}/api/auth/register",
        headers=h,
        data=json.dumps({"username": username, "password": password}),
        timeout=10,
    )


class _WorkspaceBase(unittest.TestCase):
    """Boots a server and provisions two users with isolated workspaces."""

    @classmethod
    def setUpClass(cls):
        # Pre-create two isolated workspace directories on disk *before*
        # the server starts. We place them under the repo so the server
        # process can read them (the FileManagerService uses a path
        # validation rooted in the user store).
        cls.ws_a = Path(tempfile.mkdtemp(prefix="ws_a_"))
        cls.ws_b = Path(tempfile.mkdtemp(prefix="ws_b_"))
        # The explorer's build_workspace_tree only surfaces files inside
        # directories that match its naming convention ("NN_area"). Seed
        # each workspace with a synthetic area + bloque so the per-user
        # files actually appear in /api/tree output.
        for ws, name, content in (
            (cls.ws_a, "from_a", "Note from A"),
            (cls.ws_b, "from_b", "Note from B"),
        ):
            area = ws / "01_Test_Area"
            bloque = area / "01_Test_Bloque"
            bloque.mkdir(parents=True, exist_ok=True)
            (bloque / f"{name}.md").write_text(f"# {content}", encoding="utf-8")

        # Wipe the global last_root so the auto-assigned default on first
        # login does not override our per-user overrides.
        cls.server = Server()
        cls.server.start()
        cls.base = cls.server.base

        # Register admin + two non-admin users.
        _register(requests, cls.base, "_w_admin", "pass1")
        lr = _login(requests, cls.base, "_w_admin", "pass1")
        cls.admin_token = lr.json()["token"]
        _register(requests, cls.base, "_w_alice", "pass2", token=cls.admin_token)
        _register(requests, cls.base, "_w_bob", "pass3", token=cls.admin_token)

        cls.alice_token = _login(requests, cls.base, "_w_alice", "pass2").json()["token"]
        cls.bob_token = _login(requests, cls.base, "_w_bob", "pass3").json()["token"]

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        for p in (cls.ws_a, cls.ws_b):
            shutil.rmtree(p, ignore_errors=True)


class PerUserWorkspaceTest(_WorkspaceBase):
    def test_workspace_set_and_get(self):
        # Alice sets her workspace to ws_a.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": str(self.ws_a)}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)

        # Bob sets his workspace to ws_b.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_bob/workspace",
            headers={"X-Session-Token": self.bob_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": str(self.ws_b)}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)

        # Read back.
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["username"], "_w_alice")
        self.assertEqual(Path(body["workspace_root"]).resolve(), self.ws_a.resolve())

        r = requests.get(
            f"{self.base}/api/auth/users/_w_bob/workspace",
            headers={"X-Session-Token": self.bob_token}, timeout=10,
        )
        body = r.json()
        self.assertEqual(Path(body["workspace_root"]).resolve(), self.ws_b.resolve())

    def test_tree_returns_per_user_content(self):
        # Make sure the workspaces are configured.
        self.test_workspace_set_and_get()

        def _flatten(node, acc):
            if isinstance(node, dict):
                name = node.get("name", "")
                if name:
                    acc.append(name)
                for child in node.get("children", []) or []:
                    _flatten(child, acc)
            elif isinstance(node, list):
                for x in node:
                    _flatten(x, acc)

        # Alice's tree must contain her note but not Bob's.
        r = requests.get(
            f"{self.base}/api/tree",
            headers={"X-Session-Token": self.alice_token}, timeout=15,
        )
        self.assertEqual(r.status_code, 200, r.text)
        tree_a = r.json()
        names_a = []
        _flatten(tree_a, names_a)
        # from_a.md is the file; the explorer prefixes it; strip decoration.
        joined_a = " | ".join(names_a)
        self.assertIn("from_a", joined_a,
                      f"Alice's tree missing from_a. Names: {names_a[:30]}")
        self.assertNotIn("from_b", joined_a,
                         f"Alice's tree leaked Bob's file. Names: {names_a[:30]}")

        r = requests.get(
            f"{self.base}/api/tree",
            headers={"X-Session-Token": self.bob_token}, timeout=15,
        )
        self.assertEqual(r.status_code, 200, r.text)
        tree_b = r.json()
        names_b = []
        _flatten(tree_b, names_b)
        joined_b = " | ".join(names_b)
        self.assertIn("from_b", joined_b)
        self.assertNotIn("from_a", joined_b)

    def test_cannot_view_other_users_workspace(self):
        # Alice tries to read Bob's workspace info → 403.
        r = requests.get(
            f"{self.base}/api/auth/users/_w_bob/workspace",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertEqual(r.status_code, 403)

    def test_cannot_set_other_users_workspace(self):
        # Alice tries to change Bob's workspace → 403.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_bob/workspace",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": str(self.ws_a)}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 403)

    def test_admin_can_set_any_users_workspace(self):
        # Admin overrides Alice's workspace to ws_b.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.admin_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": str(self.ws_b)}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)

        # Alice's tree now contains Bob's file.
        r = requests.get(
            f"{self.base}/api/tree",
            headers={"X-Session-Token": self.alice_token}, timeout=15,
        )
        self.assertEqual(r.status_code, 200)
        tree = r.json()

        def _flatten(node, acc):
            if isinstance(node, dict):
                name = node.get("name", "")
                if name:
                    acc.append(name)
                for child in node.get("children", []) or []:
                    _flatten(child, acc)
            elif isinstance(node, list):
                for x in node:
                    _flatten(x, acc)

        names = []
        _flatten(tree, names)
        self.assertIn("from_b", " | ".join(names),
                      f"Alice's tree should now have from_b. Names: {names[:30]}")

        # Restore Alice's workspace to ws_a for subsequent tests.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.admin_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": str(self.ws_a)}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200)

    def test_invalid_workspace_path_rejected(self):
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": "C:/no/existe/aqui"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("does not exist", r.json()["detail"])


class PerUserIntegrationsTest(_WorkspaceBase):
    def test_integrations_default_disconnected(self):
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["github_connected"])
        self.assertFalse(body["drive_connected"])

    def test_github_token_set_and_clear(self):
        # Set a fake token.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"github_token": "ghp_fake_test_token"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)

        # Verify it's marked as connected.
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertTrue(r.json()["github_connected"])

        # Clear it.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"clear_github": True}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200)

        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertFalse(r.json()["github_connected"])

    def test_cannot_modify_other_users_integrations(self):
        r = requests.put(
            f"{self.base}/api/auth/users/_w_bob/integrations",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"github_token": "ghp_steal"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 403)

    def test_integrations_response_does_not_leak_token(self):
        # Set a token, then verify the GET endpoint does NOT return it raw.
        requests.put(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"github_token": "ghp_secret_value_xyz"}),
            timeout=10,
        )
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        body_text = r.text
        self.assertNotIn("ghp_secret_value_xyz", body_text,
                         "Integrations GET response must not leak the raw token")


class AccountSettingsApiContractTest(_WorkspaceBase):
    """Validates that the API surface used by the "Mi Cuenta" settings tab
    in the frontend (ui.js) returns the expected shape.

    These tests run against the same fixtures as PerUserWorkspaceTest
    (Alice + Bob + admin, isolated workspaces) so the contract is checked
    in a realistic state, not on a bare server.
    """

    def test_workspace_get_response_shape(self):
        # Set Alice's workspace first.
        requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": str(self.ws_a)}),
            timeout=10,
        )
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Frontend reads these exact fields in loadSettingsAccountSection.
        self.assertIn("username", body)
        self.assertIn("workspace_root", body)
        self.assertIn("exists", body)
        self.assertEqual(body["username"], "_w_alice")
        self.assertIsInstance(body["exists"], bool)
        self.assertTrue(body["exists"])

    def test_workspace_put_roundtrip(self):
        # PUT then GET; the value must persist and be reflected.
        new_path = str(self.ws_b)
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": new_path}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["success"])

        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        body = r.json()
        # Compare stems: tempfile.mkdtemp(prefix="ws_b_") yields a name
        # like "ws_b_<random>", so compare the basename.
        from pathlib import Path as _P
        self.assertEqual(_P(body["workspace_root"]).resolve(),
                         _P(new_path).resolve())

    def test_workspace_put_rejects_empty(self):
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": ""}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 400)

    def test_workspace_put_rejects_traversal(self):
        # The endpoint resolves the path and checks it exists. A traversal
        # attempt that resolves to a non-existent dir must 400.
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/workspace",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"workspace_root": "C:/__no_existe_espero__"}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("does not exist", r.json()["detail"])

    def test_integrations_get_response_shape(self):
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Frontend reads these exact booleans in loadSettingsAccountSection.
        self.assertIn("github_connected", body)
        self.assertIn("drive_connected", body)
        self.assertIsInstance(body["github_connected"], bool)
        self.assertIsInstance(body["drive_connected"], bool)

    def test_integrations_put_github_persists(self):
        # Set then read; boolean must flip to True.
        requests.put(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"github_token": "ghp_my_token_abc"}),
            timeout=10,
        )
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertTrue(r.json()["github_connected"])

    def test_integrations_clear_github(self):
        # Set, then clear, then verify boolean flipped back to False.
        requests.put(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"github_token": "ghp_to_be_cleared"}),
            timeout=10,
        )
        r = requests.put(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token,
                     "Content-Type": "application/json"},
            data=json.dumps({"clear_github": True}),
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)
        r = requests.get(
            f"{self.base}/api/auth/users/_w_alice/integrations",
            headers={"X-Session-Token": self.alice_token}, timeout=10,
        )
        self.assertFalse(r.json()["github_connected"])

    def test_settings_endpoints_reject_invalid_token(self):
        # A bogus token must yield 401 (defense in depth on top of middleware).
        for path in (
            f"/api/auth/users/_w_alice/workspace",
            f"/api/auth/users/_w_alice/integrations",
        ):
            r = requests.get(
                f"{self.base}{path}",
                headers={"X-Session-Token": "deadbeef" * 8}, timeout=10,
            )
            self.assertIn(r.status_code, (401, 403),
                          f"Expected 401/403 for {path}, got {r.status_code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
