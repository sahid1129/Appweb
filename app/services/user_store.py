"""User store: per-user workspace root and integrations.

Schema (``.users.json``):
    {
        "admin": "<lowercase username>",
        "users": {
            "<lowercase username>": {
                "username": "...",
                "display_name": "...",
                "avatar_emoji": "...",
                "password_hash": "iter:salt:hash" or "salt:hash" (legacy),
                "created_at": "...",
                "last_login": "...",
                "workspace_root": "C:/path/to/notes"        # NEW
                "integrations": {                            # NEW
                    "github_token": "ghp_...",
                    "drive_token": "<base64 pickle>"
                }
            }
        }
    }

All paths in this module use ``pathlib.Path``; absolute paths are required
for ``workspace_root`` so the validation logic in
``app.services.file_manager.FileManagerService._validate_path`` is correct.
"""
from __future__ import annotations

import json
import os
import pickle
import base64
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("user_store")

USERS_PATH = Path(__file__).resolve().parent.parent.parent / ".users.json"

# Per-user service cache. Keyed by username; values are (instance, ts) so
# we can rebuild them when integrations change.
_USER_CACHE_TTL = 30.0  # seconds
_user_cache: Dict[str, tuple] = {}
_user_cache_lock = threading.Lock()

# Default workspace: the directory the server was started from.
DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent


# ========== Persistence helpers ==========

def load_users() -> dict:
    if USERS_PATH.exists():
        try:
            with open(USERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "users" in data:
                    return data
        except Exception:
            logger.exception("Failed to read .users.json")
    return {"users": {}, "admin": ""}


def save_users(data: dict):
    try:
        USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to write .users.json")


def get_user(username: str) -> Optional[dict]:
    key = (username or "").strip().lower()
    data = load_users()
    return data["users"].get(key)


def update_user(username: str, patch: dict) -> dict:
    """Merge ``patch`` into the user record and save.

    Returns the updated user record, or raises KeyError if not found.
    """
    key = (username or "").strip().lower()
    data = load_users()
    if key not in data["users"]:
        raise KeyError(f"User not found: {username}")
    data["users"][key].update(patch)
    save_users(data)
    invalidate_user_cache(key)
    return data["users"][key]


# ========== Per-user cache ==========

def invalidate_user_cache(username: Optional[str] = None):
    """Drop cached per-user service instances.

    If ``username`` is given, only that user's cache is cleared. Otherwise
    the whole cache is flushed.
    """
    with _user_cache_lock:
        if username is None:
            _user_cache.clear()
        else:
            _user_cache.pop(username.strip().lower(), None)


def _user_cached_get(key: str) -> Optional[Any]:
    with _user_cache_lock:
        entry = _user_cache.get(key)
        if not entry:
            return None
        value, ts = entry
        if time.time() - ts > _USER_CACHE_TTL:
            _user_cache.pop(key, None)
            return None
        return value


def _user_cached_set(key: str, value: Any):
    with _user_cache_lock:
        _user_cache[key] = (value, time.time())


# ========== Per-user GitHub service ==========

def get_github_service_for(username: str):
    """Return a ``GitHubSyncService`` configured for ``username``.

    Falls back to the global token in ``config.json`` if the user has none
    of their own. The instance is cached per-user for a short TTL.
    """
    from app.services.sync_service import GitHubSyncService, load_config

    key = (username or "").strip().lower()
    cached = _user_cached_get(f"github:{key}")
    if cached is not None:
        return cached

    user = get_user(key)
    token = ""
    if user:
        token = user.get("integrations", {}).get("github_token", "") or ""

    if not token:
        # Fall back to global env-var / config token.
        cfg = load_config()
        token = cfg.get("github_token", "") or os.environ.get("GITHUB_TOKEN", "")

    svc = GitHubSyncService()
    if token:
        ok, _ = svc.authenticate(token)
        if not ok:
            logger.warning("GitHub auth failed for user %s", key)
    _user_cached_set(f"github:{key}", svc)
    return svc


# ========== Per-user Drive service ==========

def get_drive_service_for(username: str):
    """Return a ``GoogleDriveSyncService`` configured for ``username``.

    Per-user ``drive_token`` (base64-pickled OAuth creds) takes priority.
    Falls back to the global config token if the user has none.
    """
    from app.services.sync_service import GoogleDriveSyncService

    key = (username or "").strip().lower()
    cached = _user_cached_get(f"drive:{key}")
    if cached is not None:
        return cached

    user = get_user(key)
    svc = GoogleDriveSyncService()
    if user:
        blob = user.get("integrations", {}).get("drive_token", "") or ""
        if blob:
            try:
                svc._creds = pickle.loads(base64.b64decode(blob))
                from googleapiclient.discovery import build
                svc.service = build("drive", "v3", credentials=svc._creds)
            except Exception:
                logger.exception("Failed to restore per-user Drive creds")
    _user_cached_set(f"drive:{key}", svc)
    return svc


def save_user_drive_token(username: str, creds) -> dict:
    """Persist the user's Drive OAuth creds into their record."""
    blob = base64.b64encode(pickle.dumps(creds)).decode()
    return update_user(username, {"integrations_drive_token": blob})


# Note: ``update_user`` does a shallow merge, so we use the underscored
# key ``integrations_drive_token`` as a top-level helper. The store layer
# is responsible for nesting it under ``integrations`` before save.
# To keep things simple we instead provide a dedicated helper that
# rewrites the integrations sub-dict atomically.


def _set_integration(username: str, name: str, value) -> dict:
    key = (username or "").strip().lower()
    data = load_users()
    if key not in data["users"]:
        raise KeyError(f"User not found: {username}")
    integrations = data["users"][key].get("integrations") or {}
    if value is None:
        integrations.pop(name, None)
    else:
        integrations[name] = value
    data["users"][key]["integrations"] = integrations
    save_users(data)
    invalidate_user_cache(key)
    return data["users"][key]


def set_github_token(username: str, token: str) -> dict:
    return _set_integration(username, "github_token", token or "")


def set_drive_token(username: str, base64_blob: str) -> dict:
    return _set_integration(username, "drive_token", base64_blob or "")


def clear_github_token(username: str) -> dict:
    return _set_integration(username, "github_token", "")


def clear_drive_token(username: str) -> dict:
    return _set_integration(username, "drive_token", "")


# ========== Workspace root ==========

def get_workspace_root(username: str) -> Path:
    """Resolve the absolute workspace root for ``username``.

    Resolution order:
        1. ``users[username].workspace_root`` (if set and exists)
        2. ``os.environ["APPWORKSPACE_ROOT"]`` (shared fallback)
        3. The directory the server was started from
    """
    user = get_user(username)
    if user:
        stored = (user.get("workspace_root") or "").strip()
        if stored:
            p = Path(stored)
            try:
                p = p.resolve()
            except Exception:
                pass
            if p.exists() and p.is_dir():
                return p

    env_root = os.environ.get("APPWORKSPACE_ROOT", "").strip()
    if env_root:
        p = Path(env_root)
        if p.exists() and p.is_dir():
            return p.resolve()

    return DEFAULT_WORKSPACE_ROOT.resolve()


def set_workspace_root(username: str, path: str) -> dict:
    """Persist a per-user workspace root.

    The path must exist and be a directory; otherwise raises ValueError.
    """
    if not path or not path.strip():
        raise ValueError("Empty workspace path")
    p = Path(path)
    try:
        p = p.resolve()
    except Exception:
        raise ValueError(f"Invalid workspace path: {path}")
    if not p.exists() or not p.is_dir():
        raise ValueError(f"Workspace path does not exist or is not a directory: {p}")
    return update_user(username, {"workspace_root": str(p)})
