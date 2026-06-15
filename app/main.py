# app/main.py
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import services
from app.services.sync_service import (
    GoogleDriveSyncService,
    GitHubSyncService,
    load_config,
    save_config
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
            content = github_sync.download(remote_repo, path)
            if content is None:
                raise HTTPException(status_code=500, detail="Error descargando archivo de GitHub")

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
            if not payload.remote_repo or not payload.path:
                raise HTTPException(status_code=400, detail="Faltan repositorio o ruta para GitHub")
            
            sha = payload.sha or github_sync.get_sha(payload.remote_repo, payload.path)
            if not sha:
                raise HTTPException(status_code=404, detail="No se pudo obtener el SHA del archivo en GitHub para el commit")

            ok = github_sync.commit(payload.remote_repo, payload.path, payload.content, sha, "Edit via Launchpad Web")
            if not ok:
                raise HTTPException(status_code=500, detail="Error al hacer commit en GitHub")
            
            new_sha = github_sync.get_sha(payload.remote_repo, payload.path)
            return {"success": True, "sha": new_sha}

        else:
            raise HTTPException(status_code=400, detail=f"Origen no soportado: {payload.source}")

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
        file_manager.save_binary_file_base64(payload.path, payload.base64_data)
        return {"success": True}
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
