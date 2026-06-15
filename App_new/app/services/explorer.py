# app/services/explorer.py
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple
from app.services.sync_service import GoogleDriveSyncService, GitHubSyncService, load_config

COLOR_GREEN = "#1b5e20"
COLOR_GRAY = "#9e9e9e"
COLOR_ORANGE = "#e65100"
COLOR_VIOLET = "#7c3aed"
COLOR_BLUE = "#2563eb"

class ExplorerService:
    def __init__(self, root_path: str, drive_sync: GoogleDriveSyncService, github_sync: GitHubSyncService):
        self.root = Path(root_path).resolve()
        self.drive_sync = drive_sync
        self.github_sync = github_sync

    def build_workspace_tree(self) -> List[Dict[str, Any]]:
        """Genera el árbol completo del workspace (Local, Drive y GitHub)."""
        tree = []
        skip = {"00_Launchpad", ".obsidian", "__pycache__", ".git"}
        
        if not self.root.exists():
            return tree

        # 1. Obtener y ordenar directorios locales
        dirs = sorted(p for p in self.root.iterdir() if p.is_dir() and p.name not in skip)
        grouped = {"areas": [], "extras": [], "bandejas": []}
        
        for p in dirs:
            if self._is_prefixed(p.name):
                grouped["areas"].append(p)
            elif p.name in ("00_Plantillas_Base", "99_Recursos_y_Catalogos"):
                grouped["extras"].append(p)
            elif p.name in ("_inbox", "_sandbox"):
                grouped["bandejas"].append(p)

        # 2. Agregar Áreas y sus Bloques
        for area_dir in grouped["areas"]:
            area_node = {
                "name": area_dir.name, 
                "_type": "area", 
                "path": str(area_dir),
                "_expanded": True, # El cliente web decidirá si lo pliega/despliega
                "children": []
            }
            for bloque_dir in sorted(area_dir.iterdir()):
                if not bloque_dir.is_dir() or not self._is_prefixed(bloque_dir.name):
                    continue
                bloque_node = {
                    "name": bloque_dir.name, 
                    "_type": "bloque", 
                    "path": str(bloque_dir),
                    "_expanded": False, 
                    "children": []
                }
                for item in sorted(bloque_dir.iterdir()):
                    name = item.name
                    if name.startswith("."):
                        continue
                    if item.is_dir():
                        child = self._walk_dir(item)
                        if child:
                            bloque_node["children"].append(child)
                    elif item.is_file():
                        color = COLOR_BLUE if item.suffix == ".md" else "#6b7280"
                        bloque_node["children"].append({
                            "name": name, 
                            "_type": "file", 
                            "path": str(item),
                            "color": color, 
                            "size": self._fmt_size(item.stat().st_size)
                        })
                area_node["children"].append(bloque_node)
            if area_node["children"]:
                tree.append(area_node)

        # 3. Agregar Base y Catálogos
        if grouped["extras"]:
            extra = {
                "name": "Base y Catálogos", 
                "_type": "group", 
                "path": "", 
                "_expanded": True, 
                "children": []
            }
            for p in grouped["extras"]:
                child = self._walk_dir(p)
                if child:
                    extra["children"].append(child)
            if extra["children"]:
                tree.append(extra)

        # 4. Agregar Bandejas
        if grouped["bandejas"]:
            band = {
                "name": "Bandejas", 
                "_type": "group", 
                "path": "", 
                "_expanded": True, 
                "children": []
            }
            for p in grouped["bandejas"]:
                child = self._walk_dir(p)
                if child:
                    band["children"].append(child)
            if band["children"]:
                tree.append(band)

        # 5. Agregar Google Drive (Si está autenticado/configurado)
        cfg = load_config()
        active_src = cfg.get("active_remote_source", "all")

        if active_src in ("all", "drive") and self.drive_sync.is_configured:
            drive_base_folder_id = cfg.get("drive_base_folder_id", "") or "root"
            drive_node = {
                "name": "☁️ Google Drive", 
                "_type": "drive", 
                "path": "drive://",
                "_expanded": False, 
                "children": []
            }
            try:
                self._populate_drive_subtree(drive_node, drive_base_folder_id)
            except Exception as e:
                # Si falla la autenticación/carga, agregamos un mensaje de error como nodo
                drive_node["children"].append({
                    "name": f"⚠️ Error al conectar: {str(e)}", 
                    "_type": "error", 
                    "path": ""
                })
            tree.append(drive_node)

        # 6. Agregar GitHub (Si está autenticado/configurado)
        if active_src in ("all", "github") and self.github_sync.is_configured:
            selected_repo = cfg.get("github_selected_repo", "")
            github_base_path = cfg.get("github_base_path", "")

            github_node = {
                "name": "🐙 GitHub", 
                "_type": "github", 
                "path": "github://",
                "_expanded": False, 
                "children": []
            }
            try:
                repos = self.github_sync.list_repos() or []
                if repos:
                    for full_name, name, default_branch in repos:
                        if selected_repo and full_name != selected_repo:
                            continue
                        repo_node = {
                            "name": f"📁 {full_name}",
                            "_type": "github_repo",
                            "repo": full_name,
                            "path": github_base_path,
                            "color": COLOR_VIOLET,
                            "_expanded": False,
                            "children": []
                        }
                        self._populate_github_subtree(repo_node, full_name, github_base_path)
                        github_node["children"].append(repo_node)
            except Exception as e:
                github_node["children"].append({
                    "name": f"⚠️ Error al conectar: {str(e)}", 
                    "_type": "error", 
                    "path": ""
                })
            tree.append(github_node)

        return tree

    def _walk_dir(self, path: Path, depth: int = 0) -> Dict[str, Any]:
        """Recorre recursivamente un directorio local."""
        items = sorted(path.iterdir())
        children = []
        file_count = 0
        for item in items:
            name = item.name
            if name.startswith(".") or name == "__pycache__":
                continue
            if item.is_dir():
                child = self._walk_dir(item, depth + 1)
                children.append(child)
            elif item.is_file():
                file_count += 1
                color = COLOR_BLUE if item.suffix == ".md" else "#6b7280"
                children.append({
                    "name": name, 
                    "_type": "file", 
                    "path": str(item),
                    "color": color, 
                    "size": self._fmt_size(item.stat().st_size)
                })
        count = sum(1 for c in children if c["_type"] == "file")
        return {
            "name": path.name, 
            "_type": "folder", 
            "path": str(path),
            "color": COLOR_GREEN if count > 0 else COLOR_GRAY,
            "droppable": True, 
            "count": count,
            "_expanded": depth < 2, 
            "children": children
        }

    def _populate_drive_subtree(self, parent_node: Dict[str, Any], folder_id: str):
        """Llena de forma recursiva los nodos de Google Drive."""
        items = self.drive_sync.list_files(folder_id)
        if items:
            for entry in items:
                ftype, name, fid, mtime, ext = entry
                if ftype == "folder":
                    folder_key = f"drive://folder/{fid}"
                    folder_node = {
                        "name": f"📁 {name}", 
                        "_type": "drive_folder",
                        "path": folder_key, 
                        "remoteId": fid,
                        "_expanded": False, 
                        "children": []
                    }
                    self._populate_drive_subtree(folder_node, fid)
                    parent_node["children"].append(folder_node)
                else:
                    icon, color = self._icon_for_ext(ext)
                    parent_node["children"].append({
                        "name": f"{icon} {name}", 
                        "_type": "drive_file",
                        "path": f"drive://file/{fid}", 
                        "remoteId": fid, 
                        "remoteName": name,
                        "color": color
                    })

    def _populate_github_subtree(self, parent_node: Dict[str, Any], repo: str, path: str):
        """Llena de forma recursiva los nodos de GitHub."""
        items = self.github_sync.list_files(repo, path)
        if items:
            for entry in items:
                if entry[0] == "dir":
                    _, name, fp = entry
                    dir_key = f"github://dir/{repo}/{fp}"
                    dir_node = {
                        "name": f"📁 {name}", 
                        "_type": "dir",
                        "repo": repo, 
                        "path": fp, 
                        "color": COLOR_VIOLET,
                        "_expanded": False, 
                        "children": []
                    }
                    self._populate_github_subtree(dir_node, repo, fp)
                    parent_node["children"].append(dir_node)
                elif entry[0] == "file":
                    _, name, fp, sha = entry
                    icon, color = self._icon_for_file(name)
                    parent_node["children"].append({
                        "name": f"{icon} {name}", 
                        "_type": "github_file",
                        "repo": repo, 
                        "path": fp, 
                        "color": color, 
                        "sha": sha,
                        "remoteName": name,
                        "_expanded": False, 
                        "children": []
                    })

    def _icon_for_ext(self, ext: str) -> Tuple[str, str]:
        icons = {
            ".md": ("📄", COLOR_BLUE), 
            ".docx": ("📃", "#6b7280"),
            ".pdf": ("📃", "#dc2626"), 
            ".xlsx": ("📊", "#16a34a"),
            ".csv": ("📊", "#16a34a"), 
            ".png": ("🖼️", "#9333ea"),
            ".jpg": ("🖼️", "#9333ea"), 
            ".jpeg": ("🖼️", "#9333ea")
        }
        return icons.get(ext, ("📎", "#6b7280"))

    def _icon_for_file(self, name: str) -> Tuple[str, str]:
        return self._icon_for_ext(Path(name).suffix.lower())

    def _is_prefixed(self, name: str) -> bool:
        return len(name) > 2 and name[:2].isdigit() and name[2] == "_"

    def _fmt_size(self, bytes_: int) -> str:
        if bytes_ < 1024:
            return f"{bytes_} B"
        elif bytes_ < 1024 * 1024:
            return f"{bytes_ / 1024:.1f} KB"
        return f"{bytes_ / (1024 * 1024):.1f} MB"
