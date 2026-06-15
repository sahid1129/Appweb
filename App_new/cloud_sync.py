"""Orígenes remotos: Google Drive + GitHub — tokens via config.json portable."""

import json
import pickle
import base64
import time
import os
from pathlib import Path
from io import BytesIO
from PySide6.QtCore import QObject, Signal, QTimer

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
CREDS_PATH = BASE / "assets" / "credentials" / "credentials.json"
CACHE_DIR = BASE / "_api_cache"
CACHE_TTL = 300  # 5 minutes in seconds
EXT_TXT = {".txt", ".log", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".json", ".xml", ".py", ".js", ".ts", ".html", ".css", ".bat", ".sh", ".sql", ".java", ".cpp", ".c", ".h", ".cs", ".go", ".rs", ".php", ".r", ".swift", ".kt", ".toml", ".properties"}

def is_binary_file(suffix):
    s = suffix.lower()
    return s not in EXT_TXT and s not in (".md", ".ipynb")


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception:
            pass
    return {"roots": [], "github_token": "", "drive_token": "", "last_root": ""}


def save_config(data):
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


# ========== Cache helpers ==========

def _cache_path(service, repo_id, file_path=""):
    """Return cache directory/file for a given service + repo."""
    base = CACHE_DIR / service / repo_id.replace("/", "_").replace(":", "_")
    if file_path:
        return base / file_path.replace("/", os.sep)
    return base


def _cache_get(service, repo_id, file_path):
    """Get cached file content. Returns None if not cached or expired."""
    fp = _cache_path(service, repo_id, file_path)
    if fp.exists():
        suffix = Path(file_path).suffix.lower()
        is_binary = is_binary_file(suffix)
        
        meta_path = fp.parent / ".meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                finfo = meta.get("files", {}).get(file_path, {})
                if time.time() - finfo.get("fetched_at", 0) < CACHE_TTL:
                    if is_binary:
                        return fp.read_bytes()
                    else:
                        return fp.read_text("utf-8", errors="replace")
            except Exception:
                pass
        # If no meta but file exists, return it
        if is_binary:
            return fp.read_bytes()
        else:
            return fp.read_text("utf-8", errors="replace")
    return None


def _cache_put(service, repo_id, file_path, content, sha=""):
    """Cache file content."""
    fp = _cache_path(service, repo_id, file_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    
    if isinstance(content, bytes):
        fp.write_bytes(content)
        size = len(content)
    else:
        fp.write_text(content, encoding="utf-8")
        size = len(content.encode("utf-8"))

    # Update meta
    meta_path = fp.parent / ".meta.json"
    meta = {"files": {}}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except Exception:
            pass
    meta["files"][file_path] = {"fetched_at": time.time(), "sha": sha,
                                 "size": size}
    meta_path.write_text(json.dumps(meta, indent=2), "utf-8")


def _cache_get_listing(service, repo_id, path=""):
    """Get cached directory listing. Returns list or None."""
    key = f"_listing_{path}" if path else "_listing_root"
    fp = _cache_path(service, repo_id, key + ".json")
    if fp.exists():
        try:
            data = json.loads(fp.read_text("utf-8"))
            if time.time() - data.get("fetched_at", 0) < CACHE_TTL:
                return data.get("items", [])
        except Exception:
            pass
    return None


def _cache_put_listing(service, repo_id, items, path=""):
    """Cache directory listing."""
    key = f"_listing_{path}" if path else "_listing_root"
    fp = _cache_path(service, repo_id, key + ".json")
    fp.parent.mkdir(parents=True, exist_ok=True)
    data = {"fetched_at": time.time(), "path": path, "items": items}
    fp.write_text(json.dumps(data, indent=2), "utf-8")


def _cache_clear_repo(service, repo_id):
    """Clear cache for a specific repo."""
    p = _cache_path(service, repo_id)
    if p.exists():
        import shutil
        shutil.rmtree(p)


class GoogleDriveSync(QObject):
    filesLoaded = Signal(list)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = None
        self._creds = None
        self._load_token()

    def _load_token(self):
        try:
            data = load_config()
            tok = data.get("drive_token", "")
            if tok:
                self._creds = pickle.loads(base64.b64decode(tok))
        except Exception:
            pass

    def _save_token(self):
        try:
            data = load_config()
            data["drive_token"] = base64.b64encode(pickle.dumps(self._creds)).decode()
            save_config(data)
        except Exception:
            pass

    def clear_token(self):
        self._creds = None
        self.service = None
        data = load_config()
        data["drive_token"] = ""
        save_config(data)

    @property
    def is_configured(self):
        data = load_config()
        return CREDS_PATH.exists() or bool(data.get("drive_token"))

    @property
    def is_authenticated(self):
        return self.service is not None

    def authenticate(self):
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
        except ImportError:
            self.error.emit("Faltan dependencias: pip install google-api-python-client google-auth-oauthlib")
            return False

        try:
            if self._creds and self._creds.valid:
                self.service = build("drive", "v3", credentials=self._creds)
                return True

            if self._creds and self._creds.expired and self._creds.refresh_token:
                self._creds.refresh(Request())
            else:
                if not CREDS_PATH.exists():
                    self.error.emit(f"No se encuentra {CREDS_PATH}")
                    return False
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDS_PATH),
                    ["https://www.googleapis.com/auth/drive"]
                )
                self._creds = flow.run_local_server(port=0, open_browser=True)

            self.service = build("drive", "v3", credentials=self._creds)
            self._save_token()
            return True
        except Exception as e:
            self.error.emit(f"Error Google Drive: {e}")
            return False

    def list_files(self, folder_id="root"):
        if not self.service:
            return []
        key = folder_id if folder_id != "root" else "root"
        cached = _cache_get_listing("drive", key)
        if cached is not None:
            return cached
        try:
            items = []
            page_token = None
            while True:
                results = self.service.files().list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                    pageToken=page_token,
                    pageSize=100
                ).execute()
                for f in results.get("files", []):
                    if f["mimeType"] == "application/vnd.google-apps.folder":
                        items.append(("folder", f["name"], f["id"], f["modifiedTime"], ""))
                    else:
                        mime = f.get("mimeType", "")
                        name = f.get("name", "")
                        if mime == "application/vnd.google-apps.spreadsheet":
                            if not name.lower().endswith(".xlsx") and not name.lower().endswith(".xls"):
                                name += ".xlsx"
                        elif mime == "application/vnd.google-apps.document":
                            if not name.lower().endswith(".docx") and not name.lower().endswith(".doc"):
                                name += ".docx"
                        ext = Path(name).suffix.lower()
                        items.append(("file", name, f["id"], f["modifiedTime"], ext))
                page_token = results.get("nextPageToken")
                if not page_token:
                    break
            _cache_put_listing("drive", key, items, "")
            return items
        except Exception as e:
            self.error.emit(f"Error listando Drive: {e}")
            return []

    def get_all_folders(self):
        if not self.service:
            if not self.authenticate():
                return []
        try:
            folders = []
            page_token = None
            while True:
                results = self.service.files().list(
                    q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                    fields="nextPageToken, files(id, name)",
                    pageToken=page_token,
                    pageSize=100
                ).execute()
                for f in results.get("files", []):
                    folders.append({"name": f["name"], "id": f["id"]})
                page_token = results.get("nextPageToken")
                if not page_token:
                    break
            folders.sort(key=lambda x: x["name"].lower())
            return folders
        except Exception as e:
            self.error.emit(f"Error listando carpetas de Drive: {e}")
            return []

    def download(self, file_id):
        if not self.service:
            return None
        cached = _cache_get("drive", file_id, "content.md")
        if cached is not None:
            return cached
        try:
            meta = self.service.files().get(fileId=file_id, fields="mimeType").execute()
            mime = meta.get("mimeType", "")
            if mime == "application/vnd.google-apps.spreadsheet":
                request = self.service.files().export_media(
                    fileId=file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            elif mime == "application/vnd.google-apps.document":
                request = self.service.files().export_media(
                    fileId=file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
            else:
                request = self.service.files().get_media(fileId=file_id)
                
            fh = BytesIO()
            try:
                from googleapiclient.http import MediaIoBaseDownload
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            except ImportError:
                fh.write(request.execute())
            
            # If exported binary, return content description or try decoding
            val = fh.getvalue()
            try:
                content = val.decode("utf-8")
            except Exception:
                content = val.decode("utf-8", errors="replace")
            _cache_put("drive", file_id, "content.md", content, "")
            return content
        except Exception as e:
            self.error.emit(f"Error descargando: {e}")
            return None

    def download_binary(self, file_id, local_dest_path):
        if not self.service:
            return False
        try:
            meta = self.service.files().get(fileId=file_id, fields="mimeType").execute()
            mime = meta.get("mimeType", "")
            if mime == "application/vnd.google-apps.spreadsheet":
                request = self.service.files().export_media(
                    fileId=file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            elif mime == "application/vnd.google-apps.document":
                request = self.service.files().export_media(
                    fileId=file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
            else:
                request = self.service.files().get_media(fileId=file_id)
                
            local_dest_path = Path(local_dest_path)
            local_dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_dest_path, "wb") as f:
                try:
                    from googleapiclient.http import MediaIoBaseDownload
                    downloader = MediaIoBaseDownload(f, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                except ImportError:
                    f.write(request.execute())
            return True
        except Exception as e:
            self.error.emit(f"Error descargando binario de Drive: {e}")
            return False

    def get_revision(self, file_id):
        if not self.service:
            return None
        try:
            meta = self.service.files().get(fileId=file_id, fields="modifiedTime").execute()
            return meta.get("modifiedTime")
        except Exception:
            return None

    def upload(self, file_id, content, mimetype="text/markdown"):
        if not self.service:
            return False, None
        try:
            from googleapiclient.http import MediaInMemoryUpload
            if isinstance(content, str):
                body_bytes = content.encode("utf-8")
            else:
                body_bytes = content
            media = MediaInMemoryUpload(body_bytes, mimetype=mimetype, resumable=False)
            updated = self.service.files().update(
                fileId=file_id,
                media_body=media,
                fields="id, modifiedTime"
            ).execute()
            return True, updated.get("modifiedTime")
        except Exception as e:
            self.error.emit(f"Error subiendo: {e}")
            return False, None

    def create_file(self, parent_folder_id, name, content, mimetype="text/markdown"):
        if not self.service:
            return False, None, None
        try:
            from googleapiclient.http import MediaInMemoryUpload
            file_metadata = {
                "name": name,
                "parents": [parent_folder_id] if parent_folder_id else []
            }
            if isinstance(content, str):
                body_bytes = content.encode("utf-8")
            else:
                body_bytes = content
            media = MediaInMemoryUpload(body_bytes, mimetype=mimetype, resumable=False)
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, modifiedTime"
            ).execute()
            return True, file.get("id"), file.get("modifiedTime")
        except Exception as e:
            self.error.emit(f"Error creando archivo en Drive: {e}")
            return False, None, None

    def delete_file(self, file_id):
        if not self.service:
            return False
        try:
            self.service.files().update(fileId=file_id, body={'trashed': True}).execute()
            return True
        except Exception as e:
            self.error.emit(f"Error al eliminar en Drive: {e}")
            return False

    def rename_file(self, file_id, new_name):
        if not self.service:
            return False
        try:
            self.service.files().update(fileId=file_id, body={'name': new_name}).execute()
            return True
        except Exception as e:
            self.error.emit(f"Error al renombrar en Drive: {e}")
            return False


class GitHubSync(QObject):
    reposLoaded = Signal(list)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.g = None
        self._token = None
        self._api_calls = 0
        self._api_remaining = 5000
        self._load_token()

    @property
    def api_status(self):
        return f"API: {self._api_calls} calls · {self._api_remaining} restantes"

    def _increment_api(self):
        self._api_calls += 1
        self._api_remaining -= 1
        if self._api_remaining < 100:
            self.error.emit(f"⚠️ Límite API bajo: {self._api_remaining} requests")

    def _load_token(self):
        try:
            data = load_config()
            self._token = data.get("github_token", "")
        except Exception:
            pass

    def _save_token(self):
        try:
            data = load_config()
            data["github_token"] = self._token or ""
            save_config(data)
        except Exception:
            pass

    def clear_token(self):
        self.g = None
        self._token = None
        data = load_config()
        data["github_token"] = ""
        save_config(data)

    @property
    def is_configured(self):
        data = load_config()
        return bool(data.get("github_token"))

    @property
    def is_authenticated(self):
        return self.g is not None

    def authenticate(self, token=None):
        self._increment_api()
        try:
            from github import Github
        except ImportError:
            self.error.emit("Faltan dependencias: pip install PyGithub")
            return False, "Dependencia PyGithub no instalada"

        if token is None:
            token = self._token
        if not token:
            return False, "Token vacío"
        token = token.strip()
        if len(token) < 10:
            return False, "Token demasiado corto"

        # Guardar token primero (aunque falle la verificación, el token se conserva)
        self._token = token
        self._save_token()

        # Limpiar caché de GitHub para forzar la carga fresca del nuevo token
        try:
            import shutil
            shutil.rmtree(CACHE_DIR / "github", ignore_errors=True)
        except Exception:
            pass

        try:
            from github import BadCredentialsException, RateLimitExceededException, GithubException
            self.g = Github(token, timeout=30)
            self.g.get_user().login
            return True, ""
        except BadCredentialsException:
            self.g = None
            return False, "Token inválido o revocado por GitHub"
        except RateLimitExceededException:
            self.g = None
            return False, "Límite de tasa excedido. Espera unos minutos."
        except GithubException as e:
            self.g = None
            return False, f"GitHub error {e.status}: {e.data.get('message', 'desconocido')}"
        except Exception as e:
            self.g = None
            return False, f"Error de conexión: {e}"

    def list_repos(self):
        if not self.g:
            return []
        cached = _cache_get_listing("github", "user_repos")
        if cached is not None:
            return cached
        self._increment_api()
        if cached is not None:
            return cached
        try:
            repos = []
            for repo in self.g.get_user().get_repos(affiliation="owner,collaborator", sort="updated"):
                repos.append((repo.full_name, repo.name, repo.default_branch))
            _cache_put_listing("github", "user_repos", repos)
            return repos
        except Exception as e:
            self.error.emit(f"Error listando repos: {e}")
            return []

    def get_all_folders(self, repo_full_name):
        if not self.g:
            authenticated, _ = self.authenticate()
            if not authenticated or not self.g:
                return []
        try:
            self._increment_api()
            repo = self.g.get_repo(repo_full_name)
            default_branch = repo.default_branch
            tree = repo.get_git_tree(default_branch, recursive=True)
            folders = []
            for element in tree.tree:
                if element.type == "tree":
                    folders.append(element.path)
            folders.sort()
            return folders
        except Exception as e:
            self.error.emit(f"Error cargando carpetas de GitHub: {e}")
            return []

    def list_files(self, repo_full_name, path=""):
        if not self.g:
            return []
        cached = _cache_get_listing("github", repo_full_name, path)
        if cached is not None:
            return cached
        self._increment_api()
        if cached is not None:
            return cached
        try:
            repo = self.g.get_repo(repo_full_name)
            contents = repo.get_contents(path)
            items = []
            for c in contents:
                if c.type == "dir":
                    items.append(("dir", c.name, c.path))
                elif c.name.endswith(".md"):
                    items.append(("file", c.name, c.path, c.sha))
            _cache_put_listing("github", repo_full_name, items, path)
            return items
        except Exception as e:
            self.error.emit(f"Error listando archivos: {e}")
            return []

    def download(self, repo_full_name, path):
        if not self.g:
            return None
        cached = _cache_get("github", repo_full_name, path)
        if cached is not None:
            return cached
        self._increment_api()
        try:
            contents = self.g.get_repo(repo_full_name).get_contents(path)
            content = contents.decoded_content.decode("utf-8", errors="replace")
            _cache_put("github", repo_full_name, path, content, contents.sha)
            return content
        except Exception as e:
            self.error.emit(f"Error descargando: {e}")
            return None

    def download_binary(self, repo_full_name, path, local_dest_path):
        if not self.g:
            return False
        try:
            repo = self.g.get_repo(repo_full_name)
            contents = repo.get_contents(path)
            content_bytes = contents.decoded_content
            local_dest_path = Path(local_dest_path)
            local_dest_path.parent.mkdir(parents=True, exist_ok=True)
            local_dest_path.write_bytes(content_bytes)
            return True
        except Exception as e:
            self.error.emit(f"Error descargando binario de GitHub: {e}")
            return False

    def get_sha(self, repo_full_name, path):
        if not self.g:
            return None
        meta_path = _cache_path("github", repo_full_name, path).parent / ".meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                finfo = meta.get("files", {}).get(path, {})
                if finfo.get("sha"):
                    return finfo["sha"]
            except Exception:
                pass
        self._increment_api()
        try:
            return self.g.get_repo(repo_full_name).get_contents(path).sha
        except Exception:
            return None

    def commit(self, repo_full_name, path, content, sha, message="Edit via Launchpad"):
        if not self.g:
            return False
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            repo.update_file(path, message, content, sha)
            # Invalidate cache for this file
            _cache_put("github", repo_full_name, path, content, "")
            # Refresh listing cache
            _cache_clear_repo("github", repo_full_name)
            return True
        except Exception as e:
            self.error.emit(f"Error commiteando: {e}")
            return False

    def delete_file(self, repo_full_name, path, sha, message="Delete file via Launchpad"):
        if not self.g:
            return False
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            repo.delete_file(path, message, sha)
            return True
        except Exception as e:
            self.error.emit(f"Error al eliminar en GitHub: {e}")
            return False

    def rename_file(self, repo_full_name, old_path, new_path, sha, message="Rename file via Launchpad"):
        if not self.g:
            return False
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            contents = repo.get_contents(old_path)
            content_bytes = contents.decoded_content
            content_str = content_bytes.decode("utf-8", errors="replace")
            # Create new file with content
            repo.create_file(new_path, message, content_str)
            # Delete old file
            repo.delete_file(old_path, f"Delete old file after rename: {old_path}", sha)
            return True
        except Exception as e:
            self.error.emit(f"Error al renombrar en GitHub: {e}")
            return False


class DownloadQueue(QObject):
    """Cola de descargas asíncronas en segundo plano — no bloquea la interfaz."""

    progress = Signal(int, int)
    finished = Signal()
    fileReady = Signal(str, str, str)

    def __init__(self, github_sync, parent=None):
        super().__init__(parent)
        self._g = github_sync
        self._queue = []
        self._total = 0
        self._current = 0
        self._failed = 0
        self._running = False
        self._thread = None

    @property
    def is_running(self):
        return self._running

    def enqueue(self, repo, path, name):
        self._queue.append((repo, path, name))

    def start(self):
        if self._running:
            return
        if not self._queue:
            self.finished.emit()
            return
        self._running = True
        self._total = len(self._queue)
        self._current = 0
        self._failed = 0
        self.progress.emit(self._current, self._total)
        
        import threading
        self._thread = threading.Thread(target=self._run_downloads, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._queue.clear()

    def _run_downloads(self):
        import time
        while self._running and self._queue:
            try:
                repo, path, name = self._queue.pop(0)
            except IndexError:
                break
            content = self._g.download(repo, path)
            if content is not None:
                self.fileReady.emit(repo, path, content)
            else:
                self._failed += 1
            self._current += 1
            self.progress.emit(self._current, self._total)
            time.sleep(0.1)
        self._running = False
        self.finished.emit()
