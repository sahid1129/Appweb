# app/services/sync_service.py
import json
import pickle
import base64
import time
import os
import shutil
import logging
from pathlib import Path
from io import BytesIO
from typing import List, Dict, Tuple, Any, Optional, Union

# Logger config
logger = logging.getLogger("sync_service")

BASE = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE / "config.json"
CREDS_PATH = BASE / "assets" / "credentials" / "credentials.json"
CACHE_DIR = BASE / "_api_cache"
CACHE_TTL = 300  # 5 minutes in seconds

EXT_TXT = {".txt", ".log", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".json", ".xml", ".py", ".js", ".ts", ".html", ".css", ".bat", ".sh", ".sql", ".java", ".cpp", ".c", ".h", ".cs", ".go", ".rs", ".php", ".r", ".swift", ".kt", ".toml", ".properties"}

def is_binary_file(suffix: str) -> bool:
    s = suffix.lower()
    return s not in EXT_TXT and s not in (".md", ".ipynb")

_MEMORY_CONFIG = {}

def load_config() -> dict:
    cfg = {"roots": [], "github_token": "", "drive_token": "", "last_root": ""}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception:
            pass
    cfg.update(_MEMORY_CONFIG)
    return cfg

def save_config(data: dict):
    _MEMORY_CONFIG.update(data)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")

# ========== Cache Helpers ==========
def _cache_path(service: str, repo_id: str, file_path: str = "") -> Path:
    base = CACHE_DIR / service / repo_id.replace("/", "_").replace(":", "_")
    if file_path:
        return base / file_path.replace("/", os.sep)
    return base

def _cache_get(service: str, repo_id: str, file_path: str) -> Optional[Union[str, bytes]]:
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

def _cache_put(service: str, repo_id: str, file_path: str, content: Union[str, bytes], sha: str = ""):
    fp = _cache_path(service, repo_id, file_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    
    if isinstance(content, bytes):
        fp.write_bytes(content)
        size = len(content)
    else:
        fp.write_text(content, encoding="utf-8")
        size = len(content.encode("utf-8"))

    meta_path = fp.parent / ".meta.json"
    meta = {"files": {}}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except Exception:
            pass
    meta["files"][file_path] = {"fetched_at": time.time(), "sha": sha, "size": size}
    meta_path.write_text(json.dumps(meta, indent=2), "utf-8")

def _cache_get_listing(service: str, repo_id: str, path: str = "") -> Optional[list]:
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

def _cache_put_listing(service: str, repo_id: str, items: list, path: str = ""):
    key = f"_listing_{path}" if path else "_listing_root"
    fp = _cache_path(service, repo_id, key + ".json")
    fp.parent.mkdir(parents=True, exist_ok=True)
    data = {"fetched_at": time.time(), "path": path, "items": items}
    fp.write_text(json.dumps(data, indent=2), "utf-8")

def _cache_clear_repo(service: str, repo_id: str):
    p = _cache_path(service, repo_id)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


# ========== Google Drive Service ==========
class GoogleDriveSyncService:
    def __init__(self):
        self.service = None
        self._creds = None
        self._load_token()

    def _load_token(self):
        try:
            data = load_config()
            tok = data.get("drive_token", "")
            if tok:
                self._creds = pickle.loads(base64.b64decode(tok))
        except Exception as e:
            logger.error(f"Error loading Drive token: {e}")

    def _save_token(self):
        try:
            data = load_config()
            data["drive_token"] = base64.b64encode(pickle.dumps(self._creds)).decode()
            save_config(data)
        except Exception as e:
            logger.error(f"Error saving Drive token: {e}")

    def clear_token(self):
        self._creds = None
        self.service = None
        data = load_config()
        data["drive_token"] = ""
        save_config(data)

    @property
    def is_configured(self) -> bool:
        data = load_config()
        return CREDS_PATH.exists() or bool(data.get("drive_token"))

    @property
    def is_authenticated(self) -> bool:
        return self.service is not None

    def authenticate(self) -> bool:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError("Faltan dependencias de Google API. Ejecuta pip install google-api-python-client google-auth-oauthlib")

        try:
            if self._creds and self._creds.valid:
                self.service = build("drive", "v3", credentials=self._creds)
                return True

            if self._creds and self._creds.expired and self._creds.refresh_token:
                self._creds.refresh(Request())
            else:
                if not CREDS_PATH.exists():
                    raise FileNotFoundError(f"No se encuentra el archivo de credenciales de Google en {CREDS_PATH}")
                # Nota: En un entorno web real de producción se usa OAuth2 web flow, 
                # pero para desarrollo local podemos usar el flow local.
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDS_PATH),
                    ["https://www.googleapis.com/auth/drive"]
                )
                self._creds = flow.run_local_server(port=0, open_browser=True)

            self.service = build("drive", "v3", credentials=self._creds)
            self._save_token()
            return True
        except Exception as e:
            logger.error(f"Error en Google Drive Auth: {e}")
            raise

    def list_files(self, folder_id: str = "root") -> list:
        if not self.service:
            self.authenticate()
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
            logger.error(f"Error listing Drive files: {e}")
            return []

    def get_all_folders(self) -> list:
        if not self.service:
            self.authenticate()
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
            logger.error(f"Error listando carpetas en Drive: {e}")
            return []

    def download(self, file_id: str) -> Optional[str]:
        if not self.service:
            self.authenticate()
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
            from googleapiclient.http import MediaIoBaseDownload
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            
            val = fh.getvalue()
            try:
                content = val.decode("utf-8")
            except Exception:
                content = val.decode("utf-8", errors="replace")
            _cache_put("drive", file_id, "content.md", content, "")
            return content
        except Exception as e:
            logger.error(f"Error downloading from Drive: {e}")
            return None

    def download_binary(self, file_id: str, local_dest_path: str) -> bool:
        if not self.service:
            self.authenticate()
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
                
            dest = Path(local_dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                from googleapiclient.http import MediaIoBaseDownload
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return True
        except Exception as e:
            logger.error(f"Error downloading binary from Drive: {e}")
            return False

    def get_revision(self, file_id: str) -> Optional[str]:
        if not self.service:
            self.authenticate()
        try:
            meta = self.service.files().get(fileId=file_id, fields="modifiedTime").execute()
            return meta.get("modifiedTime")
        except Exception:
            return None

    def upload(self, file_id: str, content: Union[str, bytes], mimetype: str = "text/markdown") -> Tuple[bool, Optional[str]]:
        if not self.service:
            self.authenticate()
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
            logger.error(f"Error uploading to Drive: {e}")
            return False, None

    def create_file(self, parent_folder_id: str, name: str, content: Union[str, bytes], mimetype: str = "text/markdown") -> Tuple[bool, Optional[str], Optional[str]]:
        if not self.service:
            self.authenticate()
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
            logger.error(f"Error creating file in Drive: {e}")
            return False, None, None

    def delete_file(self, file_id: str) -> bool:
        if not self.service:
            self.authenticate()
        try:
            self.service.files().update(fileId=file_id, body={'trashed': True}).execute()
            return True
        except Exception as e:
            logger.error(f"Error deleting from Drive: {e}")
            return False

    def rename_file(self, file_id: str, new_name: str) -> bool:
        if not self.service:
            self.authenticate()
        try:
            self.service.files().update(fileId=file_id, body={'name': new_name}).execute()
            return True
        except Exception as e:
            logger.error(f"Error renaming in Drive: {e}")
            return False


# ========== GitHub Service ==========
class GitHubSyncService:
    def __init__(self):
        self.g = None
        self._token = None
        self._api_calls = 0
        self._api_remaining = 5000
        self._load_token()

    @property
    def api_status(self) -> str:
        return f"API: {self._api_calls} calls · {self._api_remaining} restantes"

    def _increment_api(self):
        self._api_calls += 1
        self._api_remaining -= 1

    def _load_token(self):
        try:
            data = load_config()
            self._token = data.get("github_token", "")
            if self._token:
                from github import Github
                self.g = Github(self._token, timeout=30)
        except Exception as e:
            logger.error(f"Error loading GitHub token: {e}")

    def _save_token(self):
        try:
            data = load_config()
            data["github_token"] = self._token or ""
            save_config(data)
        except Exception as e:
            logger.error(f"Error saving GitHub token: {e}")

    def clear_token(self):
        self.g = None
        self._token = None
        data = load_config()
        data["github_token"] = ""
        save_config(data)

    @property
    def is_configured(self) -> bool:
        data = load_config()
        return bool(data.get("github_token"))

    @property
    def is_authenticated(self) -> bool:
        return self.g is not None

    def authenticate(self, token: str = None) -> Tuple[bool, str]:
        self._increment_api()
        try:
            from github import Github, BadCredentialsException, RateLimitExceededException, GithubException
        except ImportError:
            raise RuntimeError("Falta la dependencia PyGithub. Ejecuta pip install PyGithub")

        if token is None:
            token = self._token
        if not token:
            return False, "Token vacío"
        token = token.strip()

        if token == self._token and self.g is not None:
            return True, ""

        try:
            shutil.rmtree(CACHE_DIR / "github", ignore_errors=True)
        except Exception:
            pass

        try:
            self.g = Github(token, timeout=30)
            self.g.get_user().login
            self._token = token
            self._save_token()
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

    def list_repos(self) -> List[Tuple[str, str, str]]:
        if not self.g:
            return []
        cached = _cache_get_listing("github", "user_repos")
        if cached is not None:
            return cached
        self._increment_api()
        try:
            repos = []
            for repo in self.g.get_user().get_repos(affiliation="owner,collaborator", sort="updated"):
                repos.append((repo.full_name, repo.name, repo.default_branch))
            _cache_put_listing("github", "user_repos", repos)
            return repos
        except Exception as e:
            logger.error(f"Error listando repositorios: {e}")
            return []

    def get_all_folders(self, repo_full_name: str) -> list:
        if not self.g:
            self.authenticate()
        if not self.g:
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
            logger.error(f"Error listando carpetas en GitHub: {e}")
            return []

    def list_files(self, repo_full_name: str, path: str = "") -> list:
        if path:
            path = path.lstrip("/")
        if not self.g:
            self.authenticate()
        if not self.g:
            return []
        cached = _cache_get_listing("github", repo_full_name, path)
        if cached is not None:
            return cached
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            contents = repo.get_contents(path)
            items = []
            # contents can be a list or a ContentFile
            if not isinstance(contents, list):
                contents = [contents]
            for c in contents:
                if c.type == "dir":
                    items.append(("dir", c.name, c.path))
                elif c.name.endswith(".md"):
                    items.append(("file", c.name, c.path, c.sha))
            _cache_put_listing("github", repo_full_name, items, path)
            return items
        except Exception as e:
            logger.error(f"Error listando archivos de GitHub: {e}")
            return []

    def download(self, repo_full_name: str, path: str) -> Optional[str]:
        path = path.lstrip("/")
        if not self.g:
            self.authenticate()
        if not self.g:
            return None
        cached = _cache_get("github", repo_full_name, path)
        if cached is not None:
            return cached
        self._increment_api()
        try:
            contents = self.g.get_repo(repo_full_name).get_contents(path)
            if isinstance(contents, list):
                return None
            content = contents.decoded_content.decode("utf-8", errors="replace")
            _cache_put("github", repo_full_name, path, content, contents.sha)
            return content
        except Exception as e:
            logger.error(f"Error descargando desde GitHub: {e}")
            return None

    def download_binary(self, repo_full_name: str, path: str, local_dest_path: str) -> bool:
        path = path.lstrip("/")
        if not self.g:
            self.authenticate()
        if not self.g:
            return False
        try:
            repo = self.g.get_repo(repo_full_name)
            contents = repo.get_contents(path)
            if isinstance(contents, list):
                return False
            
            try:
                content_bytes = contents.decoded_content
            except Exception as decode_err:
                logger.warning(f"Error decodificando contenido usando PyGithub: {decode_err}. Intentando descarga directa desde download_url.")
                if hasattr(contents, "download_url") and contents.download_url:
                    import urllib.request
                    req = urllib.request.Request(contents.download_url)
                    req.add_header("User-Agent", "FastAPI-App")
                    if self._token:
                        req.add_header("Authorization", f"token {self._token}")
                    with urllib.request.urlopen(req) as response:
                        content_bytes = response.read()
                else:
                    raise decode_err

            dest = Path(local_dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content_bytes)
            return True
        except Exception as e:
            logger.error(f"Error descargando binario de GitHub: {e}")
            return False

    def get_sha(self, repo_full_name: str, path: str) -> Optional[str]:
        path = path.lstrip("/")
        if not self.g:
            self.authenticate()
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
            contents = self.g.get_repo(repo_full_name).get_contents(path)
            if isinstance(contents, list):
                return None
            return contents.sha
        except Exception:
            return None

    def create_file(self, repo_full_name: str, path: str, content: Union[str, bytes], message: str = "Create file via Launchpad") -> bool:
        path = path.lstrip("/")
        if not self.g:
            self.authenticate()
        if not self.g:
            return False
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            repo.create_file(path, message, content)
            _cache_clear_repo("github", repo_full_name)
            return True
        except Exception as e:
            logger.error(f"Error creating file on GitHub: {e}")
            return False

    def commit(self, repo_full_name: str, path: str, content: Union[str, bytes], sha: str, message: str = "Edit via Launchpad") -> bool:
        path = path.lstrip("/")
        if not self.g:
            self.authenticate()
        if not self.g:
            return False
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            repo.update_file(path, message, content, sha)
            _cache_put("github", repo_full_name, path, content, "")
            _cache_clear_repo("github", repo_full_name)
            return True
        except Exception as e:
            logger.error(f"Error commiteando a GitHub: {e}")
            return False

    def delete_file(self, repo_full_name: str, path: str, sha: str, message: str = "Delete file via Launchpad") -> bool:
        path = path.lstrip("/")
        if not self.g:
            self.authenticate()
        if not self.g:
            return False
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            repo.delete_file(path, message, sha)
            _cache_clear_repo("github", repo_full_name)
            return True
        except Exception as e:
            logger.error(f"Error al eliminar en GitHub: {e}")
            return False

    def rename_file(self, repo_full_name: str, old_path: str, new_path: str, sha: str, message: str = "Rename file via Launchpad") -> bool:
        old_path = old_path.lstrip("/")
        new_path = new_path.lstrip("/")
        if not self.g:
            self.authenticate()
        if not self.g:
            return False
        self._increment_api()
        try:
            repo = self.g.get_repo(repo_full_name)
            contents = repo.get_contents(old_path)
            if isinstance(contents, list):
                return False
            content_bytes = contents.decoded_content
            content_str = content_bytes.decode("utf-8", errors="replace")
            # Create new file with content
            repo.create_file(new_path, message, content_str)
            # Delete old file
            repo.delete_file(old_path, f"Delete old file after rename: {old_path}", sha)
            _cache_clear_repo("github", repo_full_name)
            return True
        except Exception as e:
            logger.error(f"Error al renombrar en GitHub: {e}")
            return False
