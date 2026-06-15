# app/main.py
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query, status, Request, Header
from fastapi.middleware.cors import CORSMiddleware
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

_ACTIVE_SESSION_TOKEN = None

def hash_password(password: str, salt: bytes = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ":" + pwd_hash.hex()

def verify_password(stored_hash_str: str, password: str) -> bool:
    try:
        salt_hex, hash_hex = stored_hash_str.split(":")
        salt = bytes.fromhex(salt_hex)
        pwd_hash = bytes.fromhex(hash_hex)
        new_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return pwd_hash == new_hash
    except Exception:
        return False

@app.middleware("http")
async def dynamic_auth_middleware(request: Request, call_next):
    # Allow all OPTIONS requests to bypass auth for CORS preflight compatibility
    if request.method == "OPTIONS":
        return await call_next(request)
        
    path = request.url.path
    
    # Check session authentication first for API routes
    cfg = load_config()
    pw_hash = cfg.get("app_password_hash", "")
    
    if path.startswith("/api/") and path not in ("/api/auth/status", "/api/auth/setup", "/api/auth/login"):
        if pw_hash:
            session_token = request.headers.get("x-session-token") or request.query_params.get("token")
            if not session_token or session_token != _ACTIVE_SESSION_TOKEN:
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

# Inicializar servicios
drive_sync = GoogleDriveSyncService()
github_sync = GitHubSyncService()
ai_service = AIService()

# Determinar la carpeta raíz del workspace
cfg = load_config()
WORKSPACE_ROOT = Path(cfg.get("last_root", ""))
if not WORKSPACE_ROOT or not WORKSPACE_ROOT.exists():
    WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

file_manager = FileManagerService(WORKSPACE_ROOT)
explorer_service = ExplorerService(WORKSPACE_ROOT, drive_sync, github_sync)


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
        save_config(payload.config)
        # Recargar raíz si cambia
        global WORKSPACE_ROOT, file_manager, explorer_service
        last_root = payload.config.get("last_root", "")
        if last_root and Path(last_root).exists():
            WORKSPACE_ROOT = Path(last_root)
            file_manager = FileManagerService(WORKSPACE_ROOT)
            explorer_service = ExplorerService(WORKSPACE_ROOT, drive_sync, github_sync)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Workspace Explorer Tree
@app.get("/api/tree")
def get_tree():
    try:
        return explorer_service.build_workspace_tree()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Leer archivos (Local, GitHub o Google Drive)
@app.get("/api/file/read")
def read_file(
    path: str,
    source: str = "local",
    remote_id: Optional[str] = None,
    remote_repo: Optional[str] = None,
    sha: Optional[str] = None
):
    try:
        if source == "local":
            # Retornar contenido de texto local
            content = file_manager.read_text_file(path)
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
            content = drive_sync.download(remote_id)
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
            
            if not github_sync.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub. Por favor, configura tu token.")

            content = github_sync.download(remote_repo, path)
            if content is None:
                raise HTTPException(status_code=404, detail="Archivo no encontrado en GitHub o error de descarga")

            # Guardamos caché local temporal
            safe_name = path.replace("/", "_")
            temp_path = Path(__file__).resolve().parent.parent / "_temp_files" / f"github_{safe_name}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(content, encoding="utf-8")

            current_sha = sha or github_sync.get_sha(remote_repo, path)

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
def save_file(payload: FileSavePayload):
    try:
        if payload.source == "local":
            file_manager.save_text_file(payload.path, payload.content)
            return {"success": True, "message": "Archivo guardado localmente"}

        elif payload.source == "drive":
            if payload.remote_id:
                # Actualizar archivo existente en Google Drive
                ok, modified_time = drive_sync.upload(payload.remote_id, payload.content, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al subir cambios a Google Drive")
                return {"success": True, "modifiedTime": modified_time}
            else:
                # Crear nuevo archivo en Drive
                cfg_drive = load_config()
                parent_id = cfg_drive.get("drive_base_folder_id", "") or "root"
                name = Path(payload.path).name if payload.path else "Sin_titulo.md"
                ok, fid, mtime = drive_sync.create_file(parent_id, name, payload.content, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al crear archivo en Google Drive")
                return {"success": True, "remote_id": fid, "modifiedTime": mtime}

        elif payload.source == "github":
            github_path = payload.remote_id or payload.path
            if not payload.remote_repo or not github_path:
                raise HTTPException(status_code=400, detail="Faltan repositorio o ruta para GitHub")
            
            if not github_sync.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub. Por favor, configura tu token.")

            sha = payload.sha or github_sync.get_sha(payload.remote_repo, github_path)
            if not sha:
                raise HTTPException(status_code=404, detail="No se pudo obtener el SHA del archivo en GitHub para el commit")

            ok = github_sync.commit(payload.remote_repo, github_path, payload.content, sha, "Edit via Launchpad Web")
            if not ok:
                raise HTTPException(status_code=500, detail="Error al hacer commit en GitHub")
            
            new_sha = github_sync.get_sha(payload.remote_repo, github_path)
            return {"success": True, "sha": new_sha}

        else:
            raise HTTPException(status_code=400, detail=f"Origen no soportado: {payload.source}")

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Inspección de Carpetas y Bloques locales
@app.get("/api/folder/info")
def get_folder_info(path: str):
    try:
        p = Path(path)
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
            parent_name = p.parent.name if p.parent != WORKSPACE_ROOT else "Raíz"
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
def get_file_base64(path: str):
    try:
        if "%" in path:
            path = urllib.parse.unquote(path)
        if path.startswith("file:///"):
            path = path[8:]
        
        base64_str = file_manager.read_binary_file_base64(path)
        return {"success": True, "base64": base64_str}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/file/base64/save")
def save_file_base64(payload: Base64SavePayload):
    try:
        import base64
        binary_data = base64.b64decode(payload.base64_data)

        if payload.source == "local":
            file_manager.save_binary_file_base64(payload.path, payload.base64_data)
            return {"success": True, "message": "Archivo binario guardado localmente"}

        elif payload.source == "drive":
            if payload.remote_id:
                ok, modified_time = drive_sync.upload(payload.remote_id, binary_data, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al subir cambios binarios a Google Drive")
                return {"success": True, "modifiedTime": modified_time, "remote_id": payload.remote_id}
            else:
                cfg_drive = load_config()
                parent_id = cfg_drive.get("drive_base_folder_id", "") or "root"
                name = Path(payload.path).name if payload.path else "imagen.png"
                ok, fid, mtime = drive_sync.create_file(parent_id, name, binary_data, payload.mimetype)
                if not ok:
                    raise HTTPException(status_code=500, detail="Error al crear archivo binario en Google Drive")
                return {"success": True, "remote_id": fid, "modifiedTime": mtime}

        elif payload.source == "github":
            github_path = payload.remote_id or payload.path
            if not payload.remote_repo or not github_path:
                raise HTTPException(status_code=400, detail="Faltan repositorio o ruta para GitHub")
            
            if not github_sync.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub")

            sha = payload.sha or github_sync.get_sha(payload.remote_repo, github_path)
            if sha:
                ok = github_sync.commit(payload.remote_repo, github_path, binary_data, sha, "Upload binary via Launchpad")
            else:
                ok = github_sync.create_file(payload.remote_repo, github_path, binary_data, "Upload binary via Launchpad")
                
            if not ok:
                raise HTTPException(status_code=500, detail="Error al subir archivo binario a GitHub")
            
            new_sha = github_sync.get_sha(payload.remote_repo, github_path)
            return {"success": True, "sha": new_sha}

        else:
            raise HTTPException(status_code=400, detail=f"Origen no soportado: {payload.source}")

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/file/raw")
def get_file_raw(
    path: str,
    source: str = "local",
    remote_repo: Optional[str] = None,
    remote_id: Optional[str] = None
):
    try:
        if source == "local":
            if "%" in path:
                path = urllib.parse.unquote(path)
            if path.startswith("file:///"):
                path = path[8:]
                
            p = file_manager._validate_path(path)
            if not p.is_file():
                raise HTTPException(status_code=404, detail="El archivo no existe")
            file_path = p
        elif source == "github":
            if not remote_repo or not path:
                raise HTTPException(status_code=400, detail="Faltan parámetros de repositorio/ruta para GitHub")
            
            if not github_sync.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub")
                
            safe_repo = remote_repo.replace("/", "_")
            safe_path = path.replace("/", "_")
            temp_path = Path(__file__).resolve().parent.parent / "_temp_files" / f"raw_github_{safe_repo}_{safe_path}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            
            ok = github_sync.download_binary(remote_repo, path, str(temp_path))
            if not ok:
                raise HTTPException(status_code=500, detail="Error al descargar archivo binario de GitHub")
            file_path = temp_path
        elif source == "drive":
            if not remote_id:
                raise HTTPException(status_code=400, detail="Falta remote_id para Google Drive")
                
            if not drive_sync.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en Google Drive")
                
            temp_path = Path(__file__).resolve().parent.parent / "_temp_files" / f"raw_drive_{remote_id}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            
            ok = drive_sync.download_binary(remote_id, str(temp_path))
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
def create_file_or_folder(payload: FileCreatePayload):
    try:
        if payload.type == "file":
            new_path = file_manager.create_new_file(payload.parent_folder, payload.name, payload.content)
            return {"success": True, "path": new_path}
        elif payload.type == "folder":
            new_path = file_manager.create_new_folder(payload.parent_folder, payload.name)
            return {"success": True, "path": new_path}
        else:
            raise HTTPException(status_code=400, detail="Tipo inválido. Debe ser 'file' o 'folder'")
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Crear Archivos en la Nube (GitHub o Google Drive)
@app.post("/api/file/create-cloud")
def create_cloud_file(payload: FileCreateCloudPayload):
    try:
        parsed = _parse_cloud_path(payload.parent_folder)
        if not parsed:
            raise HTTPException(status_code=400, detail="Destino en la nube inválido")
        
        service = parsed["service"]
        default_content = payload.content or f"# {payload.filename.replace('.md', '').replace('_', ' ')}\n"
        
        if service == "github":
            if not github_sync.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en GitHub. Por favor, configura tu token.")
            repo = parsed["repo"]
            inner_path = parsed["path"]
            full_path = f"{inner_path}/{payload.filename}" if inner_path else payload.filename
            full_path = full_path.lstrip("/")
            ok = github_sync.create_file(repo, full_path, default_content)
            if not ok:
                raise HTTPException(status_code=500, detail="Error creando archivo en GitHub")
            
            # Devolver repo y full_path para que el frontend pueda abrir el archivo de inmediato
            return {"success": True, "path": f"github://dir/{repo}/{full_path}", "repo": repo, "full_path": full_path}
            
        elif service == "drive":
            if not drive_sync.is_authenticated:
                raise HTTPException(status_code=401, detail="No autenticado en Google Drive")
            folder_id = parsed["folder_id"]
            ok, fid, mtime = drive_sync.create_file(folder_id, payload.filename, default_content)
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
def delete_file_or_folder(payload: FileActionPayload):
    try:
        if payload.source == "local":
            file_manager.delete_item(payload.path)
            return {"success": True}
        elif payload.source == "drive":
            if not payload.remote_id:
                raise HTTPException(status_code=400, detail="Falta remote_id de Drive")
            ok = drive_sync.delete_file(payload.remote_id)
            return {"success": ok}
        elif payload.source == "github":
            if not payload.remote_repo or not payload.path:
                raise HTTPException(status_code=400, detail="Faltan datos de GitHub")
            sha = payload.sha or github_sync.get_sha(payload.remote_repo, payload.path)
            ok = github_sync.delete_file(payload.remote_repo, payload.path, sha)
            return {"success": ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Renombrar
@app.post("/api/file/rename")
def rename_file_or_folder(payload: FileRenamePayload):
    try:
        if payload.source == "local":
            new_path = file_manager.rename_item(payload.path, payload.new_name)
            return {"success": True, "path": new_path}
        elif payload.source == "drive":
            if not payload.remote_id:
                raise HTTPException(status_code=400, detail="Falta remote_id")
            ok = drive_sync.rename_file(payload.remote_id, payload.new_name)
            return {"success": ok}
        elif payload.source == "github":
            if not payload.remote_repo or not payload.path:
                raise HTTPException(status_code=400, detail="Faltan datos de GitHub")
            sha = payload.sha or github_sync.get_sha(payload.remote_repo, payload.path)
            new_path = str(Path(payload.path).parent / payload.new_name).replace("\\", "/")
            ok = github_sync.rename_file(payload.remote_repo, payload.path, new_path, sha)
            return {"success": ok, "path": new_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mover
@app.post("/api/file/move")
def move_file_or_folder(payload: FileMovePayload):
    try:
        new_path = file_manager.move_item(payload.src_path, payload.dst_folder)
        return {"success": True, "path": new_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Google Drive & GitHub Conexiones ---

@app.post("/api/sync/github/config")
def config_github(payload: GithubConfigPayload):
    try:
        ok, err = github_sync.authenticate(payload.token)
        if ok:
            repos = github_sync.list_repos() or []
            return {"success": True, "repos": repos}
        else:
            return {"success": False, "error": err}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sync/github/clear")
def clear_github():
    github_sync.clear_token()
    return {"success": True}

@app.post("/api/sync/drive/clear")
def clear_drive():
    drive_sync.clear_token()
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
def get_github_folders(repo: str):
    try:
        folders = github_sync.get_all_folders(repo)
        return folders
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync/drive/folders")
def get_drive_folders():
    try:
        folders = drive_sync.get_all_folders()
        return folders
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync/drive/files")
def get_drive_files(folder_id: str = "root"):
    try:
        files = drive_sync.list_files(folder_id)
        return {"success": True, "files": files}
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

@app.get("/api/sync/github/files")
def get_github_files(repo: str, path: str = ""):
    try:
        files = github_sync.list_files(repo, path)
        return {"success": True, "files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- AI Chatbot & Copilot ---

@app.post("/api/ai/chat")
def chat_endpoint(payload: ChatPayload):
    try:
        reply = ai_service.chat_with_assistant(payload.history, payload.message)
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

@app.get("/api/auth/status")
def auth_status():
    cfg = load_config()
    password_set = bool(cfg.get("app_password_hash", ""))
    username = cfg.get("app_username", "")
    return {"success": True, "password_set": password_set, "username": username}

@app.get("/api/auth/verify")
def auth_verify(request: Request):
    token = request.headers.get("x-session-token")
    if token and token == _ACTIVE_SESSION_TOKEN:
        return {"success": True}
    raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/api/auth/setup")
def auth_setup(payload: SetupPasswordPayload):
    cfg = load_config()
    if cfg.get("app_password_hash", ""):
        raise HTTPException(status_code=400, detail="El password ya está configurado")
        
    username = payload.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="El usuario debe tener al menos 3 caracteres")
        
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="El password debe tener al menos 6 caracteres")
        
    h = hash_password(payload.password)
    cfg["app_username"] = username
    cfg["app_password_hash"] = h
    save_config(cfg)
    
    global _ACTIVE_SESSION_TOKEN
    _ACTIVE_SESSION_TOKEN = secrets.token_hex(32)
    return {"success": True, "token": _ACTIVE_SESSION_TOKEN}

@app.post("/api/auth/login")
def auth_login(payload: LoginPayload):
    cfg = load_config()
    pw_hash = cfg.get("app_password_hash", "")
    username_stored = cfg.get("app_username", "")
    if not pw_hash:
        raise HTTPException(status_code=400, detail="No hay password configurado. Realiza el setup primero.")
        
    username_input = payload.username.strip().lower()
    if username_input != username_stored.strip().lower() or not verify_password(pw_hash, payload.password):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
        
    global _ACTIVE_SESSION_TOKEN
    _ACTIVE_SESSION_TOKEN = secrets.token_hex(32)
    return {"success": True, "token": _ACTIVE_SESSION_TOKEN}

@app.post("/api/auth/logout")
def auth_logout():
    global _ACTIVE_SESSION_TOKEN
    _ACTIVE_SESSION_TOKEN = None
    return {"success": True}
