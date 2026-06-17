# tests/_server.py
"""Shared helpers for spawning the FastAPI app under uvicorn in a subprocess.

The tests run against a live uvicorn worker that uses the repo's actual
``config.json`` and ``.users.json``. Tests back up those files, run against
the real server, then restore.

NOTE on Windows: ``requests`` + uvicorn + a long-lived ``Session`` can deadlock
on keep-alive socket reuse when the server is being terminated between tests.
We sidestep this by using fresh ``requests`` calls per request (no Session) and
by never sharing sockets between subprocesses.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

PERSISTENT_FILES = [
    ".users.json",
    ".session_tokens.json",
    ".failed_logins.json",
    "config.json",
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: Optional[subprocess.Popen] = None
        self._backups: dict = {}

    def _backup(self):
        for name in PERSISTENT_FILES:
            p = REPO_ROOT / name
            if p.exists():
                bak = p.with_suffix(p.suffix + ".bak_test")
                if bak.exists():
                    bak.unlink()
                p.replace(bak)
                self._backups[name] = bak

    def _restore(self):
        for name, bak in self._backups.items():
            tgt = REPO_ROOT / name
            try:
                # Remove whatever the test created, then restore the bak.
                if tgt.exists():
                    tgt.unlink()
                if bak.exists():
                    bak.replace(tgt)
            except Exception:
                pass
        # Files that didn't have a bak at backup time (didn't exist) but
        # were created by the test need to be removed.
        for name in PERSISTENT_FILES:
            tgt = REPO_ROOT / name
            if name not in self._backups and tgt.exists():
                # Heuristic: a freshly created persistent file inside a
                # test is almost certainly test pollution. Clean it up.
                try:
                    tgt.unlink()
                except Exception:
                    pass
        self._backups.clear()

    def start(self, env_extra: Optional[dict] = None, timeout: float = 20.0,
              enable_bootstrap: bool = False,
              bootstrap_username: Optional[str] = None,
              bootstrap_password: Optional[str] = None,
              admin_key: Optional[str] = None):
        self._backup()
        env = os.environ.copy()
        # Disable the bootstrap admin by default for tests: the suite
        # manages its own admin via the auth endpoints and must not
        # collide with an admin left behind by a previous run. Tests
        # that exercise the bootstrap path pass enable_bootstrap=True.
        if not enable_bootstrap:
            env["BOOTSTRAP_ADMIN_DISABLED"] = "1"
        else:
            env.pop("BOOTSTRAP_ADMIN_DISABLED", None)
            if bootstrap_username:
                env["BOOTSTRAP_ADMIN_USERNAME"] = bootstrap_username
            if bootstrap_password:
                env["BOOTSTRAP_ADMIN_PASSWORD"] = bootstrap_password
        # Master-key recovery is opt-in per test. By default the env
        # var is cleared so /api/auth/admin/reset-password returns 404.
        env.pop("RENDER_ADMIN_KEY", None)
        if admin_key:
            env["RENDER_ADMIN_KEY"] = admin_key
        if env_extra:
            env.update(env_extra)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = requests.get(self.base + "/", timeout=1.0)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"Server did not start on port {self.port}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
                except Exception:
                    pass
        self.proc = None
        self._restore()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
