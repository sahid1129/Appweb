# app/main.py
import os
import re
import asyncio
import hmac
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, AsyncGenerator
from fastapi import FastAPI, HTTPException, Query, status, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Import services
from app.services.sync_service import (
    GoogleDriveSyncService,
    GitHubSyncService,
    load_config,
    save_config,
    _MEMORY_CONFIG
)
from app.services.file_manager import FileManagerService
from app.services.explorer import ExplorerService
from app.services.ai_service import AIService
from app.services import user_store

app = FastAPI(
    title="Launchpad Web API",
    description="API para el explorador de notas híbrido migrado a web",
    version="1.0.0"
)

# CORS configurations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Habilitado para desarrollo local y Vercel/GitHub Pages
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
import hashlib
import secrets
from fastapi.responses import JSONResponse

# Helper functions for multi-worker session token persistence
import json
from datetime import timedelta

SESSIONS_JSON_PATH = Path(__file__).resolve().parent.parent / ".session_tokens.json"
FAILED_LOGINS_PATH = Path(__file__).resolve().parent.parent / ".failed_logins.json"
OAUTH_STATES_PATH = Path(__file__).resolve().parent.parent / ".oauth_states.json"
USERS_PATH = Path(__file__).resolve().parent.parent / ".users.json"

def save_oauth_verifier(state: str, verifier: str, username: str = ""):
    try:
        data = {}
        if OAUTH_STATES_PATH.exists():
            try:
                with open(OAUTH_STATES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data[state] = {
            "verifier": verifier,
            "username": username,
            "created_at": datetime.utcnow().isoformat()
        }
        # Clean up old states (older than 10 minutes)
        now = datetime.utcnow()
        clean_data = {}
        for k, v in data.items():
            try:
                created = datetime.fromisoformat(v["created_at"])
                if now - created < timedelta(minutes=10):
                    clean_data[k] = v
            except Exception:
                pass
        with open(OAUTH_STATES_PATH, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=2)
    except Exception:
        pass

def pop_oauth_verifier(state: str) -> Optional[dict]:
    """Return the full state entry (verifier, username) or None."""
    if not state or not OAUTH_STATES_PATH.exists():
        return None
    try:
        with open(OAUTH_STATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        entry = data.pop(state, None)
        with open(OAUTH_STATES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return entry
    except Exception:
        return None

def load_sessions() -> dict:
    if SESSIONS_JSON_PATH.exists():
        try:
            with open(SESSIONS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "tokens" in data:
                    return data["tokens"]
        except Exception:
            pass
    return {}

def save_sessions(sessions: dict):
    try:
        SESSIONS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SESSIONS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump({"tokens": sessions}, f, indent=2)
    except Exception:
        pass

def add_session(token: str, username: str, expires_in_seconds: int = 86400):
    sessions = load_sessions()
    now = datetime.utcnow()
    # Clean expired sessions dynamically
    sessions = {k: v for k, v in sessions.items() if datetime.fromisoformat(v["expires_at"]) > now}
    
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    expires_at = (now + timedelta(seconds=expires_in_seconds)).isoformat()
    
    sessions[token_hash] = {
        "username": username,
        "created_at": now.isoformat(),
        "expires_at": expires_at
    }
    save_sessions(sessions)

def verify_session_token(token: str) -> bool:
    if not token:
        return False
    sessions = load_sessions()
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    
    session = sessions.get(token_hash)
    if not session:
        return False
        
    expires_at_str = session.get("expires_at")
    if not expires_at_str:
        return False
        
    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        if datetime.utcnow() > expires_at:
            # Remove expired session
            del sessions[token_hash]
            save_sessions(sessions)
            return False
        return True
    except Exception:
        return False

def remove_session_token(token: str):
    if not token:
        return
    sessions = load_sessions()
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    if token_hash in sessions:
        del sessions[token_hash]
        save_sessions(sessions)

# OWASP 2023 recommends >= 600000 iterations for PBKDF2-HMAC-SHA256.
# Legacy hashes used 100000 — keep verifying those, but new hashes use the
# stronger value. Stored format: "iterations:salt_hex:hash_hex".
PBKDF2_ITERATIONS = 600000
PBKDF2_LEGACY_ITERATIONS = 100000

def hash_password(password: str, salt: bytes = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    if salt is None:
        salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f"{iterations}:{salt.hex()}:{pwd_hash.hex()}"

def verify_password(stored_hash_str: str, password: str) -> bool:
    try:
        parts = stored_hash_str.split(":")
        if len(parts) == 3:
            iterations = int(parts[0])
            salt_hex, hash_hex = parts[1], parts[2]
        elif len(parts) == 2:
            # Legacy: "salt:hash" assumed 100k iterations.
            iterations = PBKDF2_LEGACY_ITERATIONS
            salt_hex, hash_hex = parts[0], parts[1]
        else:
            return False
        salt = bytes.fromhex(salt_hex)
        pwd_hash = bytes.fromhex(hash_hex)
        new_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
        return pwd_hash == new_hash
    except Exception:
        return False

# ── Multi-User Store ─────────────────────────────────────────────────────────

def load_users() -> dict:
    if USERS_PATH.exists():
        try:
            with open(USERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "users" in data:
                    return data
        except Exception:
            pass
    return {"users": {}, "admin": ""}

def save_users(data: dict):
    try:
        USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def get_session_username(token: str) -> Optional[str]:
    """Return username associated with a valid session token, or None."""
    if not token:
        return None
    sessions = load_sessions()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = sessions.get(token_hash)
    if not session:
        return None
    try:
        expires_at = datetime.fromisoformat(session["expires_at"])
        if datetime.utcnow() > expires_at:
            return None
        return session.get("username")
    except Exception:
        return None

def is_admin_token(token: str) -> bool:
    """Return True if the token belongs to the admin user."""
    username = get_session_username(token)
    if not username:
        return False
    users_data = load_users()
    return username.lower() == users_data.get("admin", "").lower()

def migrate_legacy_user():
    """Run the one-time startup tasks: legacy single-user migration, then
    bootstrap a default admin if no users exist at all.

    The bootstrap admin is intended to make a fresh install (or a freshly
    redeployed server) immediately usable. Credentials are read from
    env vars ``BOOTSTRAP_ADMIN_USERNAME`` and ``BOOTSTRAP_ADMIN_PASSWORD``;
    if not provided, defaults are ``_admin`` / ``admin``. The credentials
    are printed to stdout so the operator can see them in the server log.
    Disable by setting ``BOOTSTRAP_ADMIN_DISABLED=1``.
    """
    users_data = load_users()

    # 1) Legacy single-user config.json → .users.json (unchanged behaviour).
    if not users_data["users"]:
        cfg = load_config()
        username = (os.environ.get("APP_USERNAME") or cfg.get("app_username", "")).strip()
        pw_hash = (os.environ.get("APP_PASSWORD_HASH") or cfg.get("app_password_hash", "")).strip()
        if username and pw_hash:
            key = username.lower()
            users_data["users"][key] = {
                "username": username,
                "display_name": username,
                "password_hash": pw_hash,
                "created_at": datetime.utcnow().isoformat(),
                "last_login": None
            }
            users_data["admin"] = key
            save_users(users_data)

            # Best-effort cleanup of legacy keys from config.json.
            if not os.environ.get("APP_USERNAME") and not os.environ.get("APP_PASSWORD_HASH"):
                try:
                    cfg.pop("app_username", None)
                    cfg.pop("app_password_hash", None)
                    save_config(cfg)
                except Exception:
                    pass

    # 2) Bootstrap a default admin if there are still no users. This makes
    #    a fresh deploy immediately usable so the operator does not have
    #    to interact with the setup phase. Configurable via env vars.
    if not users_data.get("users") and not os.environ.get("BOOTSTRAP_ADMIN_DISABLED"):
        admin_username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "_admin").strip() or "_admin"
        admin_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "admin")
        if len(admin_password) < 4:
            admin_password = "admin"  # honour the min-length rule
        try:
            now = datetime.utcnow().isoformat()
            users_data["users"][admin_username.lower()] = {
                "username": admin_username,
                "display_name": admin_username,
                "password_hash": hash_password(admin_password),
                "created_at": now,
                "last_login": None,
                "workspace_root": str(WORKSPACE_ROOT) if WORKSPACE_ROOT else "",
            }
            users_data["admin"] = admin_username.lower()
            save_users(users_data)
            print(
                f"\n[AUTH] Bootstrap admin created: username='{admin_username}' "
                f"password='{admin_password}'\n"
                f"       Override with BOOTSTRAP_ADMIN_USERNAME / "
                f"BOOTSTRAP_ADMIN_PASSWORD env vars.\n"
                f"       Disable by setting BOOTSTRAP_ADMIN_DISABLED=1.\n"
            )
        except Exception as e:
            print(f"[AUTH] Failed to create bootstrap admin: {e}")

# Lockout Rate Limiting Helpers
def load_failed_logins() -> dict:
    if FAILED_LOGINS_PATH.exists():
        try:
            with open(FAILED_LOGINS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}

def save_failed_logins(data: dict):
    try:
        FAILED_LOGINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILED_LOGINS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def check_lockout(ip: str, username: str) -> tuple:
    """Returns (is_blocked, remaining_seconds)"""
    data = load_failed_logins()
    now = datetime.utcnow().timestamp()
    
    # Check IP block
    ip_entry = data.get(f"ip:{ip}")
    if ip_entry and ip_entry.get("blocked_until", 0) > now:
        return True, int(ip_entry["blocked_until"] - now)
        
    # Check username block
    user_entry = data.get(f"user:{username.strip().lower()}")
    if user_entry and user_entry.get("blocked_until", 0) > now:
        return True, int(user_entry["blocked_until"] - now)
        
    return False, 0

def record_failed_login(ip: str, username: str):
    data = load_failed_logins()
    now = datetime.utcnow().timestamp()
    
    for key in [f"ip:{ip}", f"user:{username.strip().lower()}"]:
        entry = data.get(key, {"attempts": 0, "last_attempt": 0, "blocked_until": 0})
        
        # Reset attempts if blocked_until was set but expired
        if entry.get("blocked_until", 0) < now and entry.get("blocked_until", 0) > 0:
            entry["attempts"] = 0
            entry["blocked_until"] = 0
            
        entry["attempts"] += 1
        entry["last_attempt"] = now
        
        if entry["attempts"] >= 5:
            entry["blocked_until"] = now + 900 # Block for 15 minutes
            
        data[key] = entry
        
    save_failed_logins(data)

def reset_failed_logins(ip: str, username: str):
    data = load_failed_logins()
    changed = False
    for key in [f"ip:{ip}", f"user:{username.strip().lower()}"]:
        if key in data:
            del data[key]
            changed = True
    if changed:
        save_failed_logins(data)

def validate_password_strength(password: str) -> str:
    """Validate password (min 4 chars). Returns error msg or empty string."""
    if len(password) < 4:
        return "La contraseña debe tener al menos 4 caracteres."
    return ""

@app.middleware("http")
async def dynamic_auth_middleware(request: Request, call_next):
    # Allow all OPTIONS requests to bypass auth for CORS preflight compatibility
    if request.method == "OPTIONS":
        return await call_next(request)
        
    path = request.url.path
    
    # Check session authentication first for API routes
    cfg = load_config()
    pw_hash = os.environ.get("APP_PASSWORD_HASH")
    if not pw_hash:
        raw_pw = os.environ.get("APP_PASSWORD")
        if raw_pw:
            pw_hash = hash_password(raw_pw)
        else:
            pw_hash = cfg.get("app_password_hash", "")
    
    PUBLIC_PATHS = {"/api/auth/status", "/api/auth/login", "/api/auth/users", "/api/auth/register", "/api/auth/admin/reset-password", "/api/sync/drive/callback"}
    if path.startswith("/api/") and path not in PUBLIC_PATHS:
        users_data = load_users()
        has_any_user = bool(users_data.get("users"))
        if has_any_user:
            session_token = request.headers.get("x-session-token") or request.query_params.get("token")
            if not session_token or not verify_session_token(session_token):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized: Invalid or missing session token"})
                
    # Extract GitHub Token
    token = request.headers.get("x-github-token")
    if token:
        github_sync.authenticate(token)
    
    # Extract Google Drive Token if present
    drive_token = request.headers.get("x-drive-token")
    if drive_token:
        try:
            import pickle
            import base64
            drive_sync._creds = pickle.loads(base64.b64decode(drive_token))
            from googleapiclient.discovery import build
            drive_sync.service = build("drive", "v3", credentials=drive_sync._creds)
        except Exception:
            pass
            
    # Extract AI Keys and Provider Settings
    ds_key = request.headers.get("x-deepseek-api-key")
    if ds_key:
        _MEMORY_CONFIG["deepseek_api_key"] = ds_key
        
    gem_key = request.headers.get("x-gemini-api-key")
    if gem_key:
        _MEMORY_CONFIG["gemini_api_key"] = gem_key
        
    ai_provider = request.headers.get("x-active-ai-provider")
    if ai_provider:
        _MEMORY_CONFIG["active_ai_provider"] = ai_provider
        
    gem_model = request.headers.get("x-gemini-model")
    if gem_model:
        _MEMORY_CONFIG["gemini_model"] = gem_model
        
    response = await call_next(request)
    return response

# ============================================================
# Real-Time Event Broadcasting (Server-Sent Events)
# ============================================================

class EventBroadcaster:
    """Manages a list of active SSE client queues and broadcasts events to all."""
    def __init__(self):
        self._clients: List[asyncio.Queue] = []
        self._lock: Optional[asyncio.Lock] = None  # Created lazily inside event loop

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        async with self._get_lock():
            self._clients.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        async with self._get_lock():
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    async def broadcast(self, event_type: str, data: dict):
        """Send an SSE event to all connected clients."""
        import json
        payload = json.dumps({"type": event_type, **data})
        dead: List[asyncio.Queue] = []
        async with self._get_lock():
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        # Remove unresponsive clients
        if dead:
            async with self._get_lock():
                for q in dead:
                    try:
                        self._clients.remove(q)
                    except ValueError:
                        pass

    def broadcast_sync(self, event_type: str, data: dict):
        """Thread-safe wrapper to broadcast from sync (non-async) route handlers."""
        import json
        payload = json.dumps({"type": event_type, **data})
        dead: List[asyncio.Queue] = []
        clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
            except Exception:
                dead.append(q)
        # Best-effort cleanup (no lock in sync context)
        for q in dead:
            try:
                self._clients.remove(q)
            except (ValueError, Exception):
                pass


broadcaster = EventBroadcaster()


# Inicializar servicios
drive_sync = GoogleDriveSyncService()
github_sync = GitHubSyncService()
ai_service = AIService()

# Determine the fallback workspace root. Used when no per-user
# workspace_root is configured (anonymous requests, or users who haven't
# been assigned one yet).
cfg = load_config()
WORKSPACE_ROOT = Path(cfg.get("last_root", ""))
if not WORKSPACE_ROOT or not WORKSPACE_ROOT.exists():
    WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

file_manager = FileManagerService(WORKSPACE_ROOT)
explorer_service = ExplorerService(WORKSPACE_ROOT, drive_sync, github_sync)


# ========== Per-user resolution helpers ==========

def get_workspace_root_for_token(token: str) -> Path:
    """Return the workspace root for the session identified by ``token``.

    Falls back to the global ``WORKSPACE_ROOT`` if the token is invalid
    or no per-user workspace is configured. Used by per-user endpoints
    so each user sees their own folder.
    """
    username = get_session_username(token)
    if username:
        return user_store.get_workspace_root(username)
    return WORKSPACE_ROOT


def get_services_for_token(token: str):
    """Return ``(file_manager, explorer_service, github, drive)`` for the
    session. Each user gets a ``FileManagerService`` rooted in their own
    workspace and per-user ``GitHubSyncService``/``GoogleDriveSyncService``
    instances.
    """
    root = get_workspace_root_for_token(token)
    fm = FileManagerService(root)
    username = get_session_username(token) or ""
    gh = user_store.get_github_service_for(username) if username else github_sync
    dr = user_store.get_drive_service_for(username) if username else drive_sync
    ex = ExplorerService(root, dr, gh)
    return fm, ex, gh, dr


# --- Modelos de Peticiones (Pydantic) ---
class ConfigSavePayload(BaseModel):
    config: dict

class FileSavePayload(BaseModel):
    path: str
    content: str
    source: str = "local"
    remote_id: Optional[str] = None
    remote_repo: Optional[str] = None
    sha: Optional[str] = None
    mimetype: Optional[str] = "text/markdown"

class Base64SavePayload(BaseModel):
    path: str
    base64_data: str
    source: str = "local"
    remote_id: Optional[str] = None
    remote_repo: Optional[str] = None
    sha: Optional[str] = None
    mimetype: Optional[str] = "image/png"

class SetupPasswordPayload(BaseModel):
    username: str
    password: str

class LoginPayload(BaseModel):
    username: str
    password: str
    remember_me: Optional[bool] = False

class AdminResetPasswordPayload(BaseModel):
    username: str
    new_password: str

class FileCreatePayload(BaseModel):
    parent_folder: str
    name: str
    type: str  # "file" o "folder"
    content: Optional[str] = ""

class FileCreateCloudPayload(BaseModel):
    parent_folder: str
    filename: str
    content: Optional[str] = ""

class FileActionPayload(BaseModel):
    path: str
    source: str = "local"
    remote_id: Optional[str] = None
    remote_repo: Optional[str] = None
    sha: Optional[str] = None

class FileRenamePayload(BaseModel):
    path: str
    new_name: str
    source: str = "local"
    remote_id: Optional[str] = None
    remote_repo: Optional[str] = None
    sha: Optional[str] = None

class FileMovePayload(BaseModel):
    src_path: str
    dst_folder: str

class GithubConfigPayload(BaseModel):
    token: str

class ActiveSourcePayload(BaseModel):
    source: str

class ChatPayload(BaseModel):
    history: List[Dict[str, str]]
    message: str
    mode: Optional[str] = "general"

class CopilotPayload(BaseModel):
    action: str
    text: str


# --- Métodos de Ayuda para Formateo y Parsing ---

def _fmt_size(bytes_: int) -> str:
    if bytes_ < 1024:
        return f"{bytes_} B"
    elif bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.1f} KB"
    return f"{bytes_ / (1024 * 1024):.1f} MB"

def _fmt_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")

def _parse_frontmatter_id(path: Path) -> Optional[str]:
    try:
        text = path.read_text("utf-8", errors="replace")
        m = re.search(r'^id:\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None

def _parse_index_table(path: Path) -> tuple:
    pages, sources = [], []
    try:
        text = path.read_text("utf-8", errors="replace")
        in_page, in_src = False, False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("| Página | Tipo | Descripción |"):
                in_page, in_src = True, False
                continue
            if s.startswith("| Archivo | Descripción |"):
                in_src, in_page = True, False
                continue
            if in_page:
                if s.startswith("|---") or not s.startswith("|") or s == "|":
                    in_page = False
                    continue
                parts = [x.strip() for x in s.split("|")]
                if len(parts) >= 4:
                    pages.append((parts[1].strip("[]"), parts[2], parts[3]))
            if in_src:
                if not s.startswith("|") or s == "|":
                    in_src = False
                    continue
                if s.startswith("|---"):
                    continue
                parts = [x.strip() for x in s.split("|")]
                if len(parts) >= 3:
                    src = parts[1].strip("`")
                    if src and not src.startswith("Archivo"):
                        desc = parts[2] if len(parts) > 2 else ""
                        sources.append(f"{src} — {desc}" if desc else src)
    except Exception:
        pass
    return pages, sources

def _parse_cloud_path(path_str: str) -> Optional[dict]:
    if not path_str:
        return None
    
    if path_str.startswith("github://localpath/"):
        rel = path_str[len("github://localpath/"):]
        parts = rel.split("/")
        if len(parts) >= 2:
            repo = f"{parts[0]}/{parts[1]}"
            inner_path = "/".join(parts[2:])
            return {"service": "github", "repo": repo, "path": inner_path}
    elif path_str.startswith("github://repo/"):
        repo = path_str[len("github://repo/"):]
        return {"service": "github", "repo": repo, "path": ""}
    elif path_str.startswith("github://dir/"):
        rel = path_str[len("github://dir/"):]
        parts = rel.split("/")
        if len(parts) >= 2:
            repo = f"{parts[0]}/{parts[1]}"
            inner_path = "/".join(parts[2:])
            return {"service": "github", "repo": repo, "path": inner_path}
    
    if path_str.startswith("drive://localpath/"):
        rel = path_str[len("drive://localpath/"):]
        parts = rel.split("/")
        if parts:
            folder_id = parts[0]
            return {"service": "drive", "folder_id": folder_id}
    elif path_str.startswith("drive://folder/"):
        folder_id = path_str[len("drive://folder/"):]
        return {"service": "drive", "folder_id": folder_id}
    elif path_str == "drive://":
        cfg = load_config()
        folder_id = cfg.get("drive_base_folder_id", "") or "root"
        return {"service": "drive", "folder_id": folder_id}
        
    return None


# --- Rutas de la API ---

@app.get("/")
def read_root():
    return {
        "status": "online",
        "workspace_root": str(WORKSPACE_ROOT),
        "github_configured": github_sync.is_configured,
        "drive_configured": drive_sync.is_configured
    }

# Configuración general
@app.get("/api/config")
def get_current_config():
    return load_config()

@app.post("/api/config/save")
def save_current_config(payload: ConfigSavePayload):
    try:
        # Load the existing config on disk to merge and preserve backend-only keys
        existing = load_config()
        new_config = payload.config
        
        # Merge credentials if they are missing or empty in the payload to prevent accidental overwrites
        for key in ["drive_token", "github_token"]:
            if key not in new_config or not new_config[key]:
                if existing.get(key):
                    new_config[key] = existing[key]
                    
        save_config(new_config)
        # Recargar raíz si cambia
        global WORKSPACE_ROOT, file_manager, explorer_service
        last_root = new_config.get("last_root", "")
        if last_root and Path(last_root).exists():
            WORKSPACE_ROOT = Path(last_root)
            file_manager = FileManagerService(WORKSPACE_ROOT)
            explorer_service = ExplorerService(WORKSPACE_ROOT, drive_sync, github_sync)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Workspace Explorer Tree
@app.get("/api/tree")
def get_tree(request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        if token:
            _, ex, _, _ = get_services_for_token(token)
            return ex.build_workspace_tree()
        return explorer_service.build_workspace_tree()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Leer archivos (Local, GitHub o Google Drive)
@app.get("/api/file/read")
def read_file(
    request: Request,
    path: str,
    source: str = "local",
    remote_id: Optional[str] = None,
    remote_repo: Optional[str] = None,
    sha: Optional[str] = None
):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, ex, gh, dr = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        if source == "local":
            # Retornar contenido de texto local (workspace del usuario)
            content = fm.read_text_file(path)
            return {
                "path": path,
                "name": Path(path).name,
                "content": content,
                "source": "local",
                "suffix": Path(path).suffix.lower(),
                "info": {"source": "local"}
            }

        elif source == "drive":
            if not remote_id:
                raise HTTPException(status_code=400, detail="Falta remote_id para archivo de Drive")
            content = dr.download(remote_id)
            if content is None:
                raise HTTPException(status_code=500, detail="Error descargando archivo de Google Drive")

            # Guardamos caché local temporal en _temp_files para que el visualizador funcione si es necesario
            temp_path = Path(__file__).resolve().parent.parent / "_temp_files" / f"drive_{remote_id}.md"
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(content, encoding="utf-8")

            return {
                "path": str(temp_path),
                "name": Path(path).name if path else f"drive_{remote_id}.md",
                "content": content,
                "source": "drive",
                "suffix": ".md",
                "info": {"source": "drive", "remote_id": remote_id}
            }

        elif source == "github":
            if not remote_repo or not path:
                raise HTTPException(status_code=400, detail="Faltan parámetros de repositorio/ruta para GitHub")

            if not gh.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub. Por favor, configura tu token.")

            content = gh.download(remote_repo, path)
            if content is None:
                raise HTTPException(status_code=404, detail="Archivo no encontrado en GitHub o error de descarga")

            # Guardamos caché local temporal
            safe_name = path.replace("/", "_")
            temp_path = Path(__file__).resolve().parent.parent / "_temp_files" / f"github_{safe_name}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(content, encoding="utf-8")

            current_sha = sha or gh.get_sha(remote_repo, path)

            return {
                "path": str(temp_path),
                "name": Path(path).name,
                "content": content,
                "source": "github",
                "suffix": Path(path).suffix.lower(),
                "info": {"source": "github", "remote_repo": remote_repo, "remote_id": path, "sha": current_sha}
            }

        else:
            raise HTTPException(status_code=400, detail=f"Origen no soportado: {source}")

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Guardar archivos (Local, GitHub o Google Drive)
@app.post("/api/file/save")
def save_file(payload: FileSavePayload, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, ex, gh, dr = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        result = None
        if payload.source == "local":
            fm.save_text_file(payload.path, payload.content)
            result = {"success": True, "message": "Archivo guardado localmente"}

        elif payload.source == "drive":
            if payload.remote_id:
                # Actualizar archivo existente en Google Drive
                ok, modified_time = dr.upload(payload.remote_id, payload.content, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al subir cambios a Google Drive")
                result = {"success": True, "modifiedTime": modified_time}
            else:
                # Crear nuevo archivo en Drive
                cfg_drive = load_config()
                parent_id = cfg_drive.get("drive_base_folder_id", "") or "root"
                name = Path(payload.path).name if payload.path else "Sin_titulo.md"
                ok, fid, mtime = dr.create_file(parent_id, name, payload.content, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al crear archivo en Google Drive")
                result = {"success": True, "remote_id": fid, "modifiedTime": mtime}

        elif payload.source == "github":
            github_path = payload.remote_id or payload.path
            if not payload.remote_repo or not github_path:
                raise HTTPException(status_code=400, detail="Faltan repositorio o ruta para GitHub")

            if not gh.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub. Por favor, configura tu token.")

            sha = payload.sha or gh.get_sha(payload.remote_repo, github_path, bypass_cache=True)
            if not sha:
                raise HTTPException(status_code=404, detail="No se pudo obtener el SHA del archivo en GitHub para el commit")

            ok = gh.commit(payload.remote_repo, github_path, payload.content, sha, "Edit via Launchpad Web")
            if not ok:
                raise HTTPException(status_code=500, detail="Error al hacer commit en GitHub")

            new_sha = gh.get_sha(payload.remote_repo, github_path, bypass_cache=True)
            result = {"success": True, "sha": new_sha}

        else:
            raise HTTPException(status_code=400, detail=f"Origen no soportado: {payload.source}")

        # Broadcast real-time event to all connected clients
        if result and result.get("success"):
            broadcaster.broadcast_sync("file_saved", {
                "path": payload.path,
                "source": payload.source,
                "remote_id": payload.remote_id or "",
                "remote_repo": payload.remote_repo or "",
                "saved_at": datetime.utcnow().isoformat()
            })
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Inspección de Carpetas y Bloques locales
@app.get("/api/folder/info")
def get_folder_info(path: str, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        workspace_root = get_workspace_root_for_token(token) if token else WORKSPACE_ROOT
        fm, _, _, _ = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        # Validate the path is inside the user's workspace.
        try:
            p = fm._validate_path(path)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        if not p.exists():
            raise HTTPException(status_code=404, detail="La ruta especificada no existe")

        index_path = p / "4_Doc_Obsidian_IA" / "index.md"
        if index_path.exists():
            # Retornar Bloque Info
            id_tag = _parse_frontmatter_id(index_path)
            pages, sources = _parse_index_table(index_path)
            title = f"{id_tag} — {p.name}" if id_tag else p.name
            raw_path = p / "1_Data_Raw"
            n_src = sum(1 for _ in raw_path.iterdir()) if raw_path.is_dir() else 0

            COLOR_PAGE = {
                "resumen": "#2e7d32", "entidad": "#1565c0",
                "concepto": "#e65100", "guia": "#6a1b9a"
            }

            pages_data = []
            for page, ptype, desc in pages:
                pages_data.append({
                    "page": page,
                    "type": ptype,
                    "desc": desc,
                    "typeColor": COLOR_PAGE.get(ptype, "#333")
                })

            return {
                "type": "bloque",
                "data": {
                    "title": title,
                    "meta": f"Bloque: {p.name}\n{len(pages)} páginas wiki · {n_src} fuentes",
                    "pages": pages_data,
                    "sources": sources,
                    "path": str(p)
                }
            }
        else:
            # Retornar Folder Info normal
            files = sorted(f for f in p.iterdir() if f.is_file())
            parent_name = p.parent.name if p.parent != workspace_root else "Raíz"
            items = []
            for f in files:
                sz = _fmt_size(f.stat().st_size)
                dt = _fmt_date(f.stat().st_mtime)
                items.append({"name": f.name, "size": sz, "date": dt, "path": str(f)})

            return {
                "type": "folder",
                "data": {
                    "title": p.name,
                    "meta": f"Ubicación: {parent_name} / {p.name}\nArchivos: {len(files)}",
                    "files": items,
                    "path": str(p)
                }
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Leer y Guardar Archivos en Base64 (para elementos binarios/imágenes)
@app.get("/api/file/base64")
def get_file_base64(path: str, request: Request):
    try:
        if "%" in path:
            path = urllib.parse.unquote(path)
        if path.startswith("file:///"):
            path = path[8:]
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, _, _, _ = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        base64_str = fm.read_binary_file_base64(path)
        return {"success": True, "base64": base64_str}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/file/base64/save")
def save_file_base64(payload: Base64SavePayload, request: Request):
    try:
        import base64
        binary_data = base64.b64decode(payload.base64_data)
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, _, gh, dr = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)

        if payload.source == "local":
            fm.save_binary_file_base64(payload.path, payload.base64_data)
            return {"success": True, "message": "Archivo binario guardado localmente"}

        elif payload.source == "drive":
            if payload.remote_id:
                ok, modified_time = dr.upload(payload.remote_id, binary_data, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al subir cambios binarios a Google Drive")
                return {"success": True, "modifiedTime": modified_time, "remote_id": payload.remote_id}
            else:
                cfg_drive = load_config()
                parent_id = cfg_drive.get("drive_base_folder_id", "") or "root"
                name = Path(payload.path).name if payload.path else "imagen.png"
                ok, fid, mtime = dr.create_file(parent_id, name, binary_data, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al crear archivo binario en Google Drive")
                return {"success": True, "remote_id": fid, "modifiedTime": mtime}

        elif payload.source == "github":
            github_path = payload.remote_id or payload.path
            if not payload.remote_repo or not github_path:
                raise HTTPException(status_code=400, detail="Faltan repositorio o ruta para GitHub")

            if not gh.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub")

            sha = payload.sha or gh.get_sha(payload.remote_repo, github_path, bypass_cache=True)
            if sha:
                ok = gh.commit(payload.remote_repo, github_path, binary_data, sha, "Upload binary via Launchpad")
            else:
                ok = gh.create_file(payload.remote_repo, github_path, binary_data, "Upload binary via Launchpad")

            if not ok:
                raise HTTPException(status_code=500, detail="Error al subir archivo binario a GitHub")

            new_sha = gh.get_sha(payload.remote_repo, github_path, bypass_cache=True)
            return {"success": True, "sha": new_sha}

        else:
            raise HTTPException(status_code=400, detail=f"Origen no soportado: {payload.source}")

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/file/raw")
def get_file_raw(
    request: Request,
    path: str,
    source: str = "local",
    remote_repo: Optional[str] = None,
    remote_id: Optional[str] = None
):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, _, gh, dr = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        if source == "local":
            if "%" in path:
                path = urllib.parse.unquote(path)
            if path.startswith("file:///"):
                path = path[8:]

            p = fm._validate_path(path)
            if not p.is_file():
                raise HTTPException(status_code=404, detail="El archivo no existe")
            file_path = p
        elif source == "github":
            if not remote_repo or not path:
                raise HTTPException(status_code=400, detail="Faltan parámetros de repositorio/ruta para GitHub")

            if not gh.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub")

            safe_repo = remote_repo.replace("/", "_")
            safe_path = path.replace("/", "_")
            temp_path = Path(__file__).resolve().parent.parent / "_temp_files" / f"raw_github_{safe_repo}_{safe_path}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            ok = gh.download_binary(remote_repo, path, str(temp_path))
            if not ok:
                raise HTTPException(status_code=500, detail="Error al descargar archivo binario de GitHub")
            file_path = temp_path
        elif source == "drive":
            if not remote_id:
                raise HTTPException(status_code=400, detail="Falta remote_id para Google Drive")

            if not dr.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en Google Drive")

            temp_path = Path(__file__).resolve().parent.parent / "_temp_files" / f"raw_drive_{remote_id}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            ok = dr.download_binary(remote_id, str(temp_path))
            if not ok:
                raise HTTPException(status_code=500, detail="Error al descargar archivo binario de Google Drive")
            file_path = temp_path
        else:
            raise HTTPException(status_code=400, detail=f"Origen no soportado: {source}")

        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = "application/octet-stream"

        from fastapi.responses import FileResponse
        return FileResponse(file_path, media_type=mime_type)
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Crear Archivos/Carpetas locales
@app.post("/api/file/create")
def create_file_or_folder(payload: FileCreatePayload, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, _, _, _ = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        if payload.type == "file":
            new_path = fm.create_new_file(payload.parent_folder, payload.name, payload.content)
            broadcaster.broadcast_sync("tree_changed", {"action": "create", "path": new_path, "item_type": "file"})
            return {"success": True, "path": new_path}
        elif payload.type == "folder":
            new_path = fm.create_new_folder(payload.parent_folder, payload.name)
            broadcaster.broadcast_sync("tree_changed", {"action": "create", "path": new_path, "item_type": "folder"})
            return {"success": True, "path": new_path}
        else:
            raise HTTPException(status_code=400, detail="Tipo inválido. Debe ser 'file' o 'folder'")
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Crear Archivos en la Nube (GitHub o Google Drive)
@app.post("/api/file/create-cloud")
def create_cloud_file(payload: FileCreateCloudPayload, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        _, _, gh, dr = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        parsed = _parse_cloud_path(payload.parent_folder)
        if not parsed:
            raise HTTPException(status_code=400, detail="Destino en la nube inválido")

        service = parsed["service"]
        default_content = payload.content or f"# {payload.filename.replace('.md', '').replace('_', ' ')}\n"

        if service == "github":
            if not gh.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub. Por favor, configura tu token.")
            repo = parsed["repo"]
            inner_path = parsed["path"]
            full_path = f"{inner_path}/{payload.filename}" if inner_path else payload.filename
            full_path = full_path.lstrip("/")
            ok = gh.create_file(repo, full_path, default_content)
            if not ok:
                raise HTTPException(status_code=500, detail="Error creando archivo en GitHub")

            # Devolver repo y full_path para que el frontend pueda abrir el archivo de inmediato
            return {"success": True, "path": f"github://dir/{repo}/{full_path}", "repo": repo, "full_path": full_path}

        elif service == "drive":
            if not dr.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en Google Drive")
            folder_id = parsed["folder_id"]
            ok, fid, mtime = dr.create_file(folder_id, payload.filename, default_content)
            if not ok:
                raise HTTPException(status_code=500, detail="Error creando archivo en Google Drive")
            return {"success": True, "remote_id": fid, "path": f"drive://file/{fid}"}

        else:
            raise HTTPException(status_code=400, detail="Servicio no soportado")

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Eliminar Archivos/Carpetas locales o en la nube
@app.post("/api/file/delete")
def delete_file_or_folder(payload: FileActionPayload, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, _, gh, dr = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        if payload.source == "local":
            fm.delete_item(payload.path)
            broadcaster.broadcast_sync("tree_changed", {"action": "delete", "path": payload.path, "source": "local"})
            return {"success": True}
        elif payload.source == "drive":
            if not payload.remote_id:
                raise HTTPException(status_code=400, detail="Falta remote_id de Drive")
            ok = dr.delete_file(payload.remote_id)
            if ok:
                broadcaster.broadcast_sync("tree_changed", {"action": "delete", "path": payload.path or payload.remote_id, "source": "drive"})
            return {"success": ok}
        elif payload.source == "github":
            if not payload.remote_repo or not payload.path:
                raise HTTPException(status_code=400, detail="Faltan datos de GitHub")
            sha = payload.sha or gh.get_sha(payload.remote_repo, payload.path, bypass_cache=True)
            ok = gh.delete_file(payload.remote_repo, payload.path, sha)
            if ok:
                broadcaster.broadcast_sync("tree_changed", {"action": "delete", "path": payload.path, "source": "github"})
            return {"success": ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Renombrar
@app.post("/api/file/rename")
def rename_file_or_folder(payload: FileRenamePayload, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, _, gh, dr = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        if payload.source == "local":
            new_path = fm.rename_item(payload.path, payload.new_name)
            broadcaster.broadcast_sync("tree_changed", {"action": "rename", "path": payload.path, "new_path": new_path, "source": "local"})
            return {"success": True, "path": new_path}
        elif payload.source == "drive":
            if not payload.remote_id:
                raise HTTPException(status_code=400, detail="Falta remote_id")
            ok = dr.rename_file(payload.remote_id, payload.new_name)
            if ok:
                broadcaster.broadcast_sync("tree_changed", {"action": "rename", "path": payload.path, "source": "drive"})
            return {"success": ok}
        elif payload.source == "github":
            if not payload.remote_repo or not payload.path:
                raise HTTPException(status_code=400, detail="Faltan datos de GitHub")
            sha = payload.sha or gh.get_sha(payload.remote_repo, payload.path, bypass_cache=True)
            new_path = str(Path(payload.path).parent / payload.new_name).replace("\\", "/")
            ok = gh.rename_file(payload.remote_repo, payload.path, new_path, sha)
            if ok:
                broadcaster.broadcast_sync("tree_changed", {"action": "rename", "path": payload.path, "new_path": new_path, "source": "github"})
            return {"success": ok, "path": new_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mover
@app.post("/api/file/move")
def move_file_or_folder(payload: FileMovePayload, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        fm, _, _, _ = get_services_for_token(token) if token else (file_manager, explorer_service, github_sync, drive_sync)
        new_path = fm.move_item(payload.src_path, payload.dst_folder)
        broadcaster.broadcast_sync("tree_changed", {"action": "move", "path": payload.src_path, "new_path": new_path, "source": "local"})
        return {"success": True, "path": new_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Server-Sent Events (SSE) – Real-Time Sync Endpoint
# ============================================================

@app.get("/api/events/stream")
async def events_stream(request: Request):
    """
    SSE endpoint – each connected browser tab subscribes here.
    The server pushes events whenever a file is saved, created,
    deleted, renamed, or moved from any other session.
    Auth is validated via query param ?token=<session_token> or
    X-Session-Token header (same as other protected endpoints).
    """
    # Validate session token before subscribing. EventSource cannot send custom
    # headers, so we accept the token via ?token=<hex> as well as the header.
    token = request.headers.get("x-session-token") or request.query_params.get("token")
    if not token or not verify_session_token(token):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized: Invalid or missing session token"},
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        q = await broadcaster.subscribe()
        try:
            # Send an initial heartbeat so the client knows it's connected
            yield "event: connected\ndata: {\"status\": \"ok\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send a periodic heartbeat to keep the connection alive
                    yield "event: heartbeat\ndata: {}\n\n"
        finally:
            await broadcaster.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Needed for nginx/Render proxies
            "Connection": "keep-alive",
        }
    )


# --- Google Drive & GitHub Conexiones ---

@app.post("/api/sync/github/config")
def config_github(payload: GithubConfigPayload, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        username = get_session_username(token) if token else None
        if not username:
            raise HTTPException(status_code=401, detail="Unauthorized")
        # Per-user authenticate (this also caches under user_store)
        user_store.set_github_token(username, payload.token)
        gh = user_store.get_github_service_for(username)
        # Force re-auth with the new token
        ok, err = gh.authenticate(payload.token)
        if ok:
            repos = gh.list_repos() or []
            return {"success": True, "repos": repos}
        else:
            return {"success": False, "error": err}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sync/github/clear")
def clear_github(request: Request):
    token = request.headers.get("x-session-token") or request.query_params.get("token")
    username = get_session_username(token) if token else None
    if username:
        user_store.clear_github_token(username)
        user_store.invalidate_user_cache(username)
    return {"success": True}

@app.post("/api/sync/drive/clear")
def clear_drive(request: Request):
    token = request.headers.get("x-session-token") or request.query_params.get("token")
    username = get_session_username(token) if token else None
    if username:
        user_store.clear_drive_token(username)
        user_store.invalidate_user_cache(username)
    return {"success": True}

@app.post("/api/sync/active-source")
def set_active_source(payload: ActiveSourcePayload):
    try:
        cfg = load_config()
        cfg["active_remote_source"] = payload.source
        save_config(cfg)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync/github/folders")
def get_github_folders(repo: str, request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        username = get_session_username(token) if token else None
        gh = user_store.get_github_service_for(username) if username else github_sync
        folders = gh.get_all_folders(repo)
        return folders
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync/drive/folders")
def get_drive_folders(request: Request):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        username = get_session_username(token) if token else None
        dr = user_store.get_drive_service_for(username) if username else drive_sync
        folders = dr.get_all_folders()
        return folders
    except (RuntimeError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync/drive/files")
def get_drive_files(request: Request, folder_id: str = "root"):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        username = get_session_username(token) if token else None
        dr = user_store.get_drive_service_for(username) if username else drive_sync
        files = dr.list_files(folder_id)
        return {"success": True, "files": files}
    except (RuntimeError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync/drive/config_status")
def get_drive_config_status_endpoint():
    from app.services.sync_service import CREDS_PATH, load_config
    cfg = load_config()
    return {
        "success": True,
        "has_creds": CREDS_PATH.exists(),
        "is_authenticated": bool(cfg.get("drive_token", ""))
    }

@app.post("/api/sync/drive/save_credentials")
def save_drive_credentials(payload: dict):
    if "installed" not in payload and "web" not in payload:
        raise HTTPException(status_code=400, detail="Formato de credenciales inválido. Debe contener 'installed' o 'web'.")
    try:
        import json
        from app.services.sync_service import CREDS_PATH
        CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CREDS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync/drive/auth")
def drive_auth(request: Request):
    from app.services.sync_service import CREDS_PATH, _MEMORY_CONFIG
    if not CREDS_PATH.exists():
        raise HTTPException(status_code=400, detail="No se encuentra credentials.json. Súbelo primero en la configuración.")

    from google_auth_oauthlib.flow import Flow
    scheme = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", "localhost:8000")
    redirect_uri = f"{scheme}://{host}/api/sync/drive/callback"

    flow = Flow.from_client_secrets_file(
        str(CREDS_PATH),
        scopes=["https://www.googleapis.com/auth/drive"],
        redirect_uri=redirect_uri
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true"
    )
    # Save the code_verifier for the callback (compatible with multi-worker).
    # Also tag the state with the username so the callback can route the
    # resulting credentials to the right user's record.
    sess_token = request.headers.get("x-session-token") or request.query_params.get("token")
    username = get_session_username(sess_token) if sess_token else ""
    if hasattr(flow, "code_verifier") and flow.code_verifier:
        save_oauth_verifier(state, flow.code_verifier, username=username)

    from fastapi.responses import RedirectResponse
    return RedirectResponse(auth_url)

@app.get("/api/sync/drive/callback")
def drive_callback(request: Request, code: str, state: str = None):
    from app.services.sync_service import CREDS_PATH
    if not CREDS_PATH.exists():
        raise HTTPException(status_code=400, detail="No se encuentra credentials.json")
        
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    
    scheme = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", "localhost:8000")
    redirect_uri = f"{scheme}://{host}/api/sync/drive/callback"
    
    # Recuperar el code_verifier (y el username asociado) usando el state
    code_verifier = None
    oauth_username = ""
    if state:
        entry = pop_oauth_verifier(state) or {}
        code_verifier = entry.get("verifier")
        oauth_username = entry.get("username", "")
        
    try:
        flow = Flow.from_client_secrets_file(
            str(CREDS_PATH),
            scopes=["https://www.googleapis.com/auth/drive"],
            redirect_uri=redirect_uri,
            code_verifier=code_verifier
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Persist per-user. If we know the username (from the state tag),
        # write the creds into the user's record. Otherwise fall back to
        # the legacy global config.json slot for backward compatibility.
        if oauth_username:
            try:
                user_store.set_drive_token(oauth_username, creds)
            except Exception:
                # Fallback to global if per-user persistence failed.
                drive_sync._creds = creds
                drive_sync.service = build("drive", "v3", credentials=creds)
                drive_sync._save_token()
        else:
            drive_sync._creds = creds
            drive_sync.service = build("drive", "v3", credentials=creds)
            drive_sync._save_token()
        
        from fastapi.responses import HTMLResponse
        html_content = """
        <html>
            <head>
                <title>Conexión exitosa</title>
                <style>
                    body { font-family: sans-serif; text-align: center; padding: 50px; background-color: #0f172a; color: #f1f5f9; }
                    .card { background-color: #1e293b; padding: 30px; border-radius: 8px; display: inline-block; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.5); }
                    h1 { color: #22c55e; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>✅ ¡Google Drive conectado!</h1>
                    <p>La autenticación se realizó con éxito.</p>
                    <p>Puedes cerrar esta pestaña y regresar a la aplicación.</p>
                </div>
            </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    except Exception as e:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=f"<h3>Error en la vinculación de Google Drive: {str(e)}</h3>", status_code=500)

@app.get("/api/sync/github/files")
def get_github_files(repo: str, request: Request, path: str = ""):
    try:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        username = get_session_username(token) if token else None
        gh = user_store.get_github_service_for(username) if username else github_sync
        files = gh.list_files(repo, path)
        return {"success": True, "files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Per-user workspace & integrations (Phase 2)
# ============================================================

class WorkspaceSetPayload(BaseModel):
    workspace_root: str


def _require_session_username(request: Request) -> str:
    """Extract username from session token, or raise 401."""
    token = request.headers.get("x-session-token") or request.query_params.get("token")
    username = get_session_username(token) if token else None
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return username


def _can_admin_user(target_username: str, requester_username: str) -> bool:
    """Return True if requester is the admin or is acting on themselves."""
    users_data = load_users()
    admin_key = (users_data.get("admin") or "").lower()
    return requester_username.lower() == admin_key or requester_username.lower() == target_username.lower()


@app.get("/api/auth/users/{target_username}/workspace")
def get_user_workspace(target_username: str, request: Request):
    requester = _require_session_username(request)
    if not _can_admin_user(target_username, requester):
        raise HTTPException(status_code=403, detail="Solo el admin o el propio usuario puede ver su workspace.")
    root = user_store.get_workspace_root(target_username)
    return {
        "username": target_username,
        "workspace_root": str(root),
        "exists": root.exists(),
    }


@app.put("/api/auth/users/{target_username}/workspace")
def set_user_workspace(target_username: str, payload: WorkspaceSetPayload, request: Request):
    requester = _require_session_username(request)
    if not _can_admin_user(target_username, requester):
        raise HTTPException(status_code=403, detail="Solo el admin o el propio usuario puede cambiar su workspace.")
    try:
        user_store.set_workspace_root(target_username, payload.workspace_root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Invalidate caches so the next request rebuilds services with the new root.
    user_store.invalidate_user_cache(target_username)
    return {"success": True, "workspace_root": payload.workspace_root}


class IntegrationsPatchPayload(BaseModel):
    github_token: Optional[str] = None
    clear_github: Optional[bool] = None
    clear_drive: Optional[bool] = None


@app.get("/api/auth/users/{target_username}/integrations")
def get_user_integrations(target_username: str, request: Request):
    requester = _require_session_username(request)
    if not _can_admin_user(target_username, requester):
        raise HTTPException(status_code=403, detail="Solo el admin o el propio usuario puede ver sus integraciones.")
    user = user_store.get_user(target_username) or {}
    integrations = user.get("integrations") or {}
    # We never return the raw tokens; just booleans indicating presence.
    return {
        "username": target_username,
        "github_connected": bool(integrations.get("github_token")),
        "drive_connected": bool(integrations.get("drive_token")),
    }


@app.put("/api/auth/users/{target_username}/integrations")
def set_user_integrations(target_username: str, payload: IntegrationsPatchPayload, request: Request):
    requester = _require_session_username(request)
    if not _can_admin_user(target_username, requester):
        raise HTTPException(status_code=403, detail="Solo el admin o el propio usuario puede modificar sus integraciones.")
    # Ensure the target user exists.
    if not user_store.get_user(target_username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if payload.clear_github:
        user_store.clear_github_token(target_username)
    elif payload.github_token is not None:
        user_store.set_github_token(target_username, payload.github_token)
        # Force the cached service to re-authenticate with the new token.
        gh = user_store.get_github_service_for(target_username)
        gh.authenticate(payload.github_token)
    if payload.clear_drive:
        user_store.clear_drive_token(target_username)
    user_store.invalidate_user_cache(target_username)
    return {"success": True}


# --- AI Chatbot & Copilot ---

@app.post("/api/ai/chat")
def chat_endpoint(payload: ChatPayload):
    try:
        reply = ai_service.chat_with_assistant(payload.history, payload.message, mode=payload.mode)
        return {"success": True, "reply": reply}
    except Exception as e:
        return {"success": False, "error": f"Error de la IA: {str(e)}"}

@app.post("/api/ai/copilot")
def copilot_endpoint(payload: CopilotPayload):
    try:
        result = ai_service.run_copilot_action(payload.action, payload.text)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": f"Error de Copilot: {str(e)}"}

# --- Authentication Endpoints ---

# Pydantic models for new auth
class RegisterPayload(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None

# Run migration on import (no-op if already done)
migrate_legacy_user()

@app.get("/api/auth/status")
def auth_status():
    """Quick status: are there any users configured?

    On the first call after startup, if the user store is empty, run the
    bootstrap again. This is a safety net for the case where the import-time
    ``migrate_legacy_user()`` ran before the persistent disk was mounted
    (e.g. on Render, where the persistent disk is mounted at container
    start, not at module import).
    """
    users_data = load_users()
    if not users_data.get("users") and not os.environ.get("BOOTSTRAP_ADMIN_DISABLED"):
        migrate_legacy_user()
        users_data = load_users()
    has_users = bool(users_data.get("users"))
    return {"success": True, "has_users": has_users, "password_set": has_users}

@app.get("/api/auth/users")
def list_users():
    """Public endpoint — returns the list of users for the login screen (no passwords)."""
    users_data = load_users()
    public_users = []
    admin_key = users_data.get("admin", "")
    for key, user in users_data["users"].items():
        public_users.append({
            "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "is_admin": key == admin_key,
            "last_login": user.get("last_login")
        })
    return {
        "success": True,
        "users": public_users,
        "has_users": len(public_users) > 0
    }

@app.get("/api/auth/verify")
def auth_verify(request: Request):
    token = request.headers.get("x-session-token") or request.query_params.get("token")
    if token and verify_session_token(token):
        username = get_session_username(token)
        users_data = load_users()
        is_admin = username.lower() == users_data.get("admin", "").lower() if username else False
        return {"success": True, "username": username, "is_admin": is_admin}
    raise HTTPException(status_code=401, detail="Unauthorized")


# ---------- Master-key password recovery ----------
#
# This endpoint is the safety valve for the case where the operator
# forgets the bootstrap password. It is gated by RENDER_ADMIN_KEY
# (or any env var you set), which is a long random string the operator
# keeps outside the repo (in the Render dashboard, in a password
# manager, etc.). If the env var is not set, the endpoint returns 404
# so the feature is invisible to the rest of the app.
#
# Rate limit: 5 attempts per IP per hour.

_admin_key_attempts: dict = {}  # ip -> [timestamps]
_ADMIN_KEY_WINDOW = 3600       # 1 hour
_ADMIN_KEY_MAX_ATTEMPTS = 5


def _check_admin_key_rate_limit(ip: str) -> bool:
    """Return True if the IP is allowed to attempt another reset."""
    now = time.time()
    arr = _admin_key_attempts.get(ip, [])
    arr = [t for t in arr if now - t < _ADMIN_KEY_WINDOW]
    if len(arr) >= _ADMIN_KEY_MAX_ATTEMPTS:
        return False
    arr.append(now)
    _admin_key_attempts[ip] = arr
    return True


@app.post("/api/auth/admin/reset-password")
def admin_reset_password(payload: AdminResetPasswordPayload, request: Request):
    """Reset any user's password using the master key.

    The endpoint is intentionally noisy in the log so the operator
    can audit every reset. If the env var is not set, the endpoint
    returns 404 so the feature is fully opt-in.
    """
    master = os.environ.get("RENDER_ADMIN_KEY", "").strip()
    if not master:
        # Feature not enabled on this deployment.
        raise HTTPException(status_code=404, detail="Not Found")

    # Constant-time compare to avoid timing oracles.
    presented = request.headers.get("X-Admin-Key", "")
    if not presented or not hmac.compare_digest(presented, master):
        client_ip = request.client.host if request.client else "unknown"
        if not _check_admin_key_rate_limit(client_ip):
            raise HTTPException(
                status_code=429,
                detail="Demasiados intentos. Vuelve a intentarlo más tarde.",
            )
        raise HTTPException(status_code=403, detail="Invalid admin key")

    # Validate the new password using the same rules as register.
    err = validate_password_strength(payload.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)

    users_data = load_users()
    key = payload.username.strip().lower()
    if key not in users_data["users"]:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    users_data["users"][key]["password_hash"] = hash_password(payload.new_password)
    save_users(users_data)
    user_store.invalidate_user_cache(payload.username)
    print(
        f"[AUTH] Master-key password reset: username={payload.username!r} "
        f"remote={request.client.host if request.client else 'unknown'}",
        flush=True,
    )
    return {
        "success": True,
        "username": users_data["users"][key]["username"],
    }


@app.post("/api/auth/register")
def auth_register(payload: RegisterPayload, request: Request):
    """Create a new user. First user is always admin and requires no auth.
    Subsequent users require a valid admin session token."""
    users_data = load_users()
    is_first_user = len(users_data["users"]) == 0

    if not is_first_user:
        token = request.headers.get("x-session-token") or request.query_params.get("token")
        if not is_admin_token(token):
            raise HTTPException(status_code=403, detail="Solo el administrador puede crear nuevos usuarios.")

    username = payload.username.strip()
    if len(username) < 2:
        raise HTTPException(status_code=400, detail="El usuario debe tener al menos 2 caracteres.")

    key = username.lower()
    if key in users_data["users"]:
        raise HTTPException(status_code=400, detail="El nombre de usuario ya existe.")

    err = validate_password_strength(payload.password)
    if err:
        raise HTTPException(status_code=400, detail=err)

    pw_hash = hash_password(payload.password)
    now = datetime.utcnow().isoformat()
    users_data["users"][key] = {
        "username": username,
        "display_name": payload.display_name or username,
        "password_hash": pw_hash,
        "created_at": now,
        "last_login": None
    }
    if is_first_user:
        users_data["admin"] = key

    save_users(users_data)

    session_token = secrets.token_hex(32)
    add_session(session_token, username, expires_in_seconds=86400)
    return {
        "success": True,
        "token": session_token,
        "username": username,
        "is_admin": is_first_user
    }

@app.delete("/api/auth/users/{target_username}")
def delete_user(target_username: str, request: Request):
    """Delete a user. Admin only. Cannot delete the admin account."""
    token = request.headers.get("x-session-token") or request.query_params.get("token")
    if not is_admin_token(token):
        raise HTTPException(status_code=403, detail="Solo el administrador puede eliminar usuarios.")

    users_data = load_users()
    key = target_username.lower()

    if key == users_data.get("admin", ""):
        raise HTTPException(status_code=400, detail="No puedes eliminar al usuario administrador.")

    if key not in users_data["users"]:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    del users_data["users"][key]
    save_users(users_data)
    return {"success": True}

@app.post("/api/auth/login")
def auth_login(payload: LoginPayload, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    username = payload.username.strip()

    is_blocked, remaining = check_lockout(client_ip, username)
    if is_blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos fallidos. Bloqueado por {remaining} segundos."
        )

    users_data = load_users()
    key = username.lower()
    user = users_data["users"].get(key)

    if not user or not verify_password(user["password_hash"], payload.password):
        record_failed_login(client_ip, username)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")

    reset_failed_logins(client_ip, username)

    # Update last_login timestamp
    users_data["users"][key]["last_login"] = datetime.utcnow().isoformat()

    # Opportunistic rehash: if stored hash uses legacy iterations, upgrade it.
    stored_hash = users_data["users"][key].get("password_hash", "")
    if stored_hash and not stored_hash.startswith(f"{PBKDF2_ITERATIONS}:"):
        try:
            users_data["users"][key]["password_hash"] = hash_password(payload.password)
        except Exception:
            pass  # Keep the legacy hash if rehash fails; login still succeeded.

    # Auto-assign a per-user workspace_root the first time we see the user.
    # We use the current global WORKSPACE_ROOT (or last_root in config) as
    # the default so the migration is invisible.
    if not users_data["users"][key].get("workspace_root"):
        try:
            users_data["users"][key]["workspace_root"] = str(WORKSPACE_ROOT)
        except Exception:
            pass

    save_users(users_data)

    is_admin = key == users_data.get("admin", "")
    expires_in = 30 * 24 * 3600 if payload.remember_me else 24 * 3600
    session_token = secrets.token_hex(32)
    add_session(session_token, user["username"], expires_in_seconds=expires_in)
    return {
        "success": True,
        "token": session_token,
        "username": user["username"],
        "is_admin": is_admin
    }

# Backward-compat stub — redirects to register
@app.post("/api/auth/setup")
def auth_setup(payload: SetupPasswordPayload):
    from fastapi.responses import JSONResponse as JR
    users_data = load_users()
    if users_data["users"]:
        raise HTTPException(status_code=400, detail="Ya hay usuarios configurados. Usa /api/auth/register.")
    username = payload.username.strip()
    if len(username) < 2:
        raise HTTPException(status_code=400, detail="El usuario debe tener al menos 2 caracteres.")
    err = validate_password_strength(payload.password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    pw_hash = hash_password(payload.password)
    now = datetime.utcnow().isoformat()
    key = username.lower()
    users_data["users"][key] = {
        "username": username, "display_name": username,
        "password_hash": pw_hash, "created_at": now, "last_login": None
    }
    users_data["admin"] = key
    save_users(users_data)
    token = secrets.token_hex(32)
    add_session(token, username, expires_in_seconds=86400)
    return {"success": True, "token": token, "username": username, "is_admin": True}

@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token = request.headers.get("x-session-token")
    if token:
        remove_session_token(token)
    return {"success": True}

