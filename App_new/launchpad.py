"""Launchpad v4 — UI 100% HTML con backend Python."""

import re
import sys
import json
import shutil
import base64
import tempfile
import traceback
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QFileDialog
from PySide6.QtCore import Qt, QUrl, QFileSystemWatcher, QTimer, QObject, Signal, Slot, QBuffer, QIODevice
from PySide6.QtGui import QAction, QKeySequence, QDesktopServices, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel

import cloud_sync

SUBCARPETAS = ("1_Data_Raw", "2_Data_Processed", "3_Dashboards", "4_Doc_Obsidian_IA")
EXT_IMG = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg")
EXT_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
EXT_TXT = {".txt", ".log", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".json", ".xml", ".py", ".js", ".ts", ".html", ".css", ".bat", ".sh", ".sql", ".java", ".cpp", ".c", ".h", ".cs", ".go", ".rs", ".php", ".r", ".swift", ".kt", ".toml", ".properties"}
def is_binary_file(suffix):
    s = suffix.lower()
    return s not in EXT_TXT and s not in (".md", ".ipynb")
COLOR_GREEN = "#1b5e20"
COLOR_GRAY = "#9e9e9e"
COLOR_ORANGE = "#e65100"
COLOR_VIOLET = "#7c3aed"
COLOR_BLUE = "#2563eb"

COLOR_PAGE = {
    "resumen": "#2e7d32", "entidad": "#1565c0",
    "concepto": "#e65100", "guia": "#6a1b9a",
}
def get_mimetype(suffix):
    s = suffix.lower()
    if s == ".md":
        return "text/markdown"
    elif s in (".docx", ".doc"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif s in (".xlsx", ".xls"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif s == ".pdf":
        return "application/pdf"
    elif s == ".png":
        return "image/png"
    elif s in (".jpg", ".jpeg"):
        return "image/jpeg"
    elif s == ".mp4":
        return "video/mp4"
    else:
        return "application/octet-stream"


class UIBridge(QObject):
    """Bridge between Python backend and HTML UI via QWebChannel."""

    setTreeData = Signal(str)
    showFolderInfo = Signal(str)
    showBlocInfo = Signal(str)
    showEditor = Signal(str)  # Packed JSON
    showImage = Signal(str)  # Packed JSON
    showFileInfo = Signal(str)
    setStatus = Signal(str)
    saveResult = Signal(bool)
    showLoading = Signal(bool)
    requireGithubToken = Signal()
    showConflictDialog = Signal(str)  # Packed JSON
    closeEditorSignal = Signal()
    chatResponseReceived = Signal(str)
    mermaidDiagramGenerated = Signal(str)
    copilotResultReceived = Signal(str)
    githubFoldersLoaded = Signal(str)
    driveFoldersLoaded = Signal(str)
    showVisualizer = Signal(str)  # Packed JSON for visualizer view
    triggerVisualizerWindow = Signal(str, str, str) # path, name, vtype

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self._visualizer_windows = []
        self.triggerVisualizerWindow.connect(self._on_trigger_visualizer_window)
        cfg = cloud_sync.load_config()
        last = cfg.get("last_root", "")
        if last and Path(last).exists():
            self.root = Path(last)
        else:
            self.root = root
        self._expanded_states = cfg.get("expanded_states", {})
        self.active_remote_source = cfg.get("active_remote_source", "all")
        self._watcher = None
        self.drive_sync = cloud_sync.GoogleDriveSync(self)
        self.github_sync = cloud_sync.GitHubSync(self)
        self._current_editor_path = None
        self._current_file_info = {}
        self._saved_tree_width = 0
        self._suppress_refresh = False
        self._nav_history = []
        self._nav_index = -1
        self._loading_active = False
        self._github_in_progress = False
        self._drive_in_progress = False
        self._github_repos = []
        self._github_nodes = {}
        self._drive_nodes = {}
        self._download_queue = cloud_sync.DownloadQueue(self.github_sync, self)
        self._download_queue.progress.connect(self._on_dl_progress)
        self._download_queue.finished.connect(self._on_dl_finished)
        self._download_queue.fileReady.connect(self._on_dl_ready)
        self.temp_dir = Path(__file__).resolve().parent / "_temp_files"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._pending_open_info = {}
        self._init_watcher()

    def _set_loading(self, active):
        self._loading_active = active
        self.showLoading.emit(active)

    def _init_watcher(self):
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_fs_change)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(600)
        self._refresh_timer.timeout.connect(self._full_refresh)
        self._watch_all_dirs()

    def _watch_all_dirs(self):
        paths = set()
        for p in self.root.rglob("*"):
            if p.is_dir() and p.name not in ("__pycache__", ".git", ".obsidian"):
                paths.add(str(p))
        if paths:
            try:
                self._watcher.addPaths(list(paths))
            except Exception:
                pass

    def _on_fs_change(self, path):
        if self._suppress_refresh:
            return
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _full_refresh(self):
        self._watch_all_dirs()
        self.scan_and_send()

    # ========== Slots (JS → Python) ==========

    def _push_nav(self, path):
        if self._nav_index < len(self._nav_history) - 1:
            self._nav_history = self._nav_history[:self._nav_index + 1]
        self._nav_history.append(path)
        self._nav_index = len(self._nav_history) - 1

    @Slot(str, str)
    def itemClicked(self, path, typ):
        print(f"DEBUG BACKEND: itemClicked called path={path}, typ={typ}", flush=True)
        if typ == "github_file":
            self.setStatus.emit("Usa el menú contextual de GitHub para abrir archivos")
            return
        if typ == "drive_file":
            self.setStatus.emit("Usa el explorador de Drive para abrir archivos")
            return
        p = Path(path)
        if path:
            self._push_nav(path)
        
        is_file = p.is_file()
        is_dir = p.is_dir()
        print(f"DEBUG BACKEND: Path properties for {path} -> exists={p.exists()}, is_file={is_file}", flush=True)
        
        if is_dir:
            self.setStatus.emit(f"Mostrando carpeta: {p.name}")
            self._show_folder_info(p)
        elif is_file:
            self.setStatus.emit(f"Abriendo archivo: {p.name}")
            self._open_file_by_type(p, "local", None)
        else:
            print(f"DEBUG BACKEND: clicked element did not match any category: {path}", flush=True)
            self.setStatus.emit(f"Elemento no reconocido: {p.name}")

    @Slot()
    def navBack(self):
        if self._nav_index > 0:
            self._nav_index -= 1
            p = Path(self._nav_history[self._nav_index])
            self.itemClicked(str(p), "dir" if p.is_dir() else "file")

    @Slot()
    def navForward(self):
        if self._nav_index < len(self._nav_history) - 1:
            self._nav_index += 1
            p = Path(self._nav_history[self._nav_index])
            self.itemClicked(str(p), "dir" if p.is_dir() else "file")

    @Slot()
    def navUp(self):
        if self._nav_history:
            current = Path(self._nav_history[self._nav_index])
            parent = current.parent
            if parent != current and parent.exists():
                self._push_nav(str(parent))
                self._show_folder_info(parent)

    @Slot(str, str)
    def saveFile(self, path, content):
        try:
            Path(path).write_text(content, encoding="utf-8")
            self.saveResult.emit(True)
        except Exception:
            self.saveResult.emit(False)

    @Slot()
    def commitActiveFile(self):
        self.showLoading.emit(True)
        import threading
        if threading.current_thread() is threading.main_thread():
            QApplication.processEvents()
            
        def run():
            try:
                finfo = self._current_file_info
                src = finfo.get("source", "local")
                path = self._current_editor_path
                if not path or not Path(path).exists():
                    self.setStatus.emit("❌ Error: No hay archivo activo o el archivo no existe")
                    self.saveResult.emit(False)
                    return

                suffix = Path(path).suffix.lower()
                is_binary = is_binary_file(suffix)
                
                if is_binary:
                    content = Path(path).read_bytes()
                else:
                    content = Path(path).read_text(encoding="utf-8")
                
                mimetype = get_mimetype(suffix)

                if src == "drive":
                    if finfo.get("remote_id"):
                        ok, rev = self.drive_sync.upload(finfo["remote_id"], content, mimetype)
                        if ok:
                            finfo["revision"] = rev
                            self.setStatus.emit("✅ Archivo subido con éxito a Google Drive")
                        else:
                            self.setStatus.emit("❌ Error al subir a Google Drive")
                        self.saveResult.emit(ok)
                    elif finfo.get("parent_folder_id"):
                        if not self.drive_sync.is_authenticated:
                            self.drive_sync.authenticate()
                        ok, fid, rev = self.drive_sync.create_file(finfo["parent_folder_id"], Path(path).name, content, mimetype)
                        if ok:
                            finfo["remote_id"] = fid
                            finfo["revision"] = rev
                            self.setStatus.emit("✅ Archivo creado y subido con éxito a Google Drive")
                            
                            parent_folder_id = finfo["parent_folder_id"]
                            cloud_sync._cache_clear_repo("drive", parent_folder_id)
                            cloud_sync._cache_clear_repo("drive", "root")
                            cfg_drive = cloud_sync.load_config()
                            base_folder = cfg_drive.get("drive_base_folder_id", "") or "root"
                            cloud_sync._cache_clear_repo("drive", base_folder)
                            if parent_folder_id in self._drive_nodes:
                                self._drive_nodes[parent_folder_id] = self.drive_sync.list_files(parent_folder_id)
                            if "root" in self._drive_nodes:
                                self._drive_nodes["root"] = self.drive_sync.list_files("root")
                            self.scan_and_send()
                        else:
                            self.setStatus.emit("❌ Error al crear archivo en Google Drive")
                        self.saveResult.emit(ok)
                    else:
                        self.setStatus.emit("❌ Error: Información de sincronización de Drive incompleta")
                        self.saveResult.emit(False)

                elif src == "github" and finfo.get("remote_repo"):
                    repo_name = finfo["remote_repo"]
                    remote_id = finfo["remote_id"]
                    sha = finfo.get("sha")
                    
                    if not sha:
                        sha = self.github_sync.get_sha(repo_name, remote_id)
                    
                    if sha:
                        ok = self.github_sync.commit(
                            repo_name, remote_id,
                            content, sha, "Edit via Launchpad"
                        )
                        if ok:
                            new_sha = self.github_sync.get_sha(repo_name, remote_id)
                            if new_sha:
                                finfo["sha"] = new_sha
                            self.setStatus.emit("✅ Archivo commiteado con éxito a GitHub")
                            
                            # Invalidate in-memory parent dir listing
                            parent_dir = str(Path(remote_id).parent).replace("\\", "/")
                            if parent_dir == ".":
                                parent_dir = ""
                            if (repo_name, parent_dir) in self._github_nodes:
                                self._github_nodes[(repo_name, parent_dir)] = self.github_sync.list_files(repo_name, parent_dir)
                            self.scan_and_send()
                        else:
                            self.setStatus.emit("❌ Error al commitear a GitHub")
                        self.saveResult.emit(ok)
                    else:
                        try:
                            token = self.github_sync._token
                            if not token:
                                cfg = cloud_sync.load_config()
                                token = cfg.get("github_token", "")
                            if token:
                                from github import Github
                                g = Github(token)
                                repo = g.get_repo(repo_name)
                                repo.create_file(
                                    path=remote_id,
                                    message="Create file via Launchpad",
                                    content=content
                                )
                                cloud_sync._cache_put("github", repo_name, remote_id, content, "")
                                cloud_sync._cache_clear_repo("github", repo_name)
                                new_sha = self.github_sync.get_sha(repo_name, remote_id)
                                if new_sha:
                                    finfo["sha"] = new_sha
                                self.setStatus.emit("✅ Archivo creado y commiteado con éxito a GitHub")
                                
                                # Invalidate in-memory parent dir listing and refetch
                                parent_dir = str(Path(remote_id).parent).replace("\\", "/")
                                if parent_dir == ".":
                                    parent_dir = ""
                                if (repo_name, parent_dir) in self._github_nodes:
                                    self._github_nodes[(repo_name, parent_dir)] = self.github_sync.list_files(repo_name, parent_dir)
                                self.scan_and_send()
                                self.saveResult.emit(True)
                            else:
                                self.setStatus.emit("❌ Error: Falta token de GitHub")
                                self.saveResult.emit(False)
                        except Exception as e:
                            self.setStatus.emit(f"❌ Error al crear archivo en GitHub: {e}")
                            self.saveResult.emit(False)
                else:
                    self.setStatus.emit("ℹ️ El archivo actual es local (no requiere subida)")
                    self.saveResult.emit(True)
            except Exception as e:
                self.setStatus.emit(f"❌ Error en la subida: {e}")
                self.saveResult.emit(False)
            finally:
                self.showLoading.emit(False)

        threading.Thread(target=run, daemon=True).start()

    @Slot(str, bool)
    def setFolderExpanded(self, key, expanded):
        self._expanded_states[key] = expanded
        cfg = cloud_sync.load_config()
        cfg["expanded_states"] = self._expanded_states
        cloud_sync.save_config(cfg)

    @Slot(str)
    def createNewFile(self, selected_path_str):
        try:
            initial_dir = self.root
            is_github = False
            is_drive = False
            github_repo = ""
            github_rel_path = ""
            drive_folder_id = ""
            
            if selected_path_str:
                if selected_path_str.startswith("github://localpath/"):
                    is_github = True
                    rel = selected_path_str[len("github://localpath/"):]
                    parts = rel.split("/")
                    if len(parts) >= 2:
                        github_repo = f"{parts[0]}/{parts[1]}"
                        github_rel_path = "/".join(parts[2:])
                    initial_dir = self.temp_dir / "github" / rel
                elif selected_path_str.startswith("drive://localpath/"):
                    is_drive = True
                    rel = selected_path_str[len("drive://localpath/"):]
                    parts = rel.split("/")
                    drive_folder_id = parts[0]
                    initial_dir = self.temp_dir / "drive" / drive_folder_id
                else:
                    p = Path(selected_path_str)
                    if p.exists():
                        initial_dir = p if p.is_dir() else p.parent
            
            initial_dir.mkdir(parents=True, exist_ok=True)
            
            file_path_str, _ = QFileDialog.getSaveFileName(
                None,
                "Crear Nuevo Archivo Markdown",
                str(initial_dir),
                "Markdown Files (*.md)"
            )
            
            if not file_path_str:
                return
                
            file_path = Path(file_path_str)
            if file_path.suffix.lower() != ".md":
                file_path = file_path.with_suffix(".md")
                
            name = file_path.name
            
            source = "local"
            info = None
            
            try:
                rel_to_github = file_path.relative_to(self.temp_dir / "github")
                parts = rel_to_github.parts
                if len(parts) >= 3:
                    repo = f"{parts[0]}/{parts[1]}"
                    inner_path = "/".join(parts[2:])
                    source = "github"
                    info = {
                        "source": "github",
                        "remote_repo": repo,
                        "remote_id": inner_path,
                        "sha": None
                    }
            except ValueError:
                pass
                
            try:
                rel_to_drive = file_path.relative_to(self.temp_dir / "drive")
                parts = rel_to_drive.parts
                if len(parts) >= 2:
                    folder_id = parts[0]
                    source = "drive"
                    info = {
                        "source": "drive",
                        "remote_id": None,
                        "parent_folder_id": folder_id,
                        "revision": None
                    }
            except ValueError:
                pass

            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("# " + name.replace(".md", "").replace("_", " "), encoding="utf-8")
            self.scan_and_send()
            self.setStatus.emit(f"✅ Archivo '{name}' creado con éxito")
            self._open_file_by_type(file_path, source, info)
            
            if source in ("github", "drive"):
                self.setStatus.emit(f"Subiendo nuevo archivo '{name}' a la nube...")
                self.commitActiveFile()
        except Exception as e:
            self.setStatus.emit(f"❌ Error al crear archivo: {e}")

    @Slot(str, str)
    def renameItem(self, old_path_str, new_name):
        try:
            old_path = Path(old_path_str)
            if not old_path.exists():
                self.setStatus.emit("❌ Error: El elemento seleccionado no existe")
                return
                
            new_path = old_path.parent / new_name
            if new_path.exists():
                self.setStatus.emit(f"⚠️ Ya existe un elemento con el nombre '{new_name}'")
                return
                
            old_path.rename(new_path)
            self.scan_and_send()
            self.setStatus.emit(f"✅ Renombrado a '{new_name}' con éxito")
            
            if self._current_editor_path and Path(self._current_editor_path).resolve() == old_path.resolve():
                self._current_editor_path = new_path
                data = {
                    "path": str(new_path),
                    "name": new_name,
                    "content": new_path.read_text(encoding="utf-8", errors="replace"),
                    "source": self._current_file_info.get("source", "local")
                }
                self.showEditor.emit(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            self.setStatus.emit(f"❌ Error al renombrar: {e}")

    @Slot(str, str)
    def moveItem(self, old_path_str, new_path_str):
        try:
            old_path = Path(old_path_str)
            if not old_path.exists():
                self.setStatus.emit("❌ Error: El elemento origen no existe")
                return
                
            new_path = Path(new_path_str)
            
            # If target is a directory or path ends with slash, move inside it
            if new_path.is_dir() or new_path_str.endswith("/") or new_path_str.endswith("\\"):
                new_path = new_path / old_path.name
                
            # Create parent dirs if they don't exist
            new_path.parent.mkdir(parents=True, exist_ok=True)
            
            if new_path.exists():
                self.setStatus.emit("⚠️ El archivo de destino ya existe")
                return
                
            old_path.rename(new_path)
            self.scan_and_send()
            self.setStatus.emit(f"✅ Elemento movido a '{new_path.name}' con éxito")
            
            if self._current_editor_path and Path(self._current_editor_path).resolve() == old_path.resolve():
                self._current_editor_path = str(new_path)
                data = {
                    "path": str(new_path),
                    "name": new_path.name,
                    "content": new_path.read_text(encoding="utf-8", errors="replace"),
                    "source": self._current_file_info.get("source", "local")
                }
                self.showEditor.emit(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            self.setStatus.emit(f"❌ Error al mover elemento: {e}")

    @Slot()
    def refreshTree(self):
        self.scan_and_send()

    @Slot(str)
    def openInExplorer(self, path_str):
        if not path_str:
            self.setStatus.emit("Selecciona un elemento primero")
            return
        
        if path_str.startswith("github://localpath/"):
            rel = path_str[len("github://localpath/"):]
            target = self.temp_dir / "github" / rel
        elif path_str.startswith("drive://localpath/"):
            rel = path_str[len("drive://localpath/"):]
            target = self.temp_dir / "drive" / rel
        else:
            target = Path(path_str)
            
        if not target.exists():
            if target.parent.exists():
                target = target.parent
            else:
                target = self.temp_dir
                
        actual_target = target if target.is_dir() else target.parent
        if actual_target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(actual_target)))
        else:
            self.setStatus.emit("❌ La ubicación no existe localmente")

    @Slot(str)
    def openFileExternally(self, path_str):
        if not path_str:
            return
        if path_str.startswith("github://localpath/"):
            rel = path_str[len("github://localpath/"):]
            target = self.temp_dir / "github" / rel
        elif path_str.startswith("drive://localpath/"):
            rel = path_str[len("drive://localpath/"):]
            target = self.temp_dir / "drive" / rel
        else:
            target = Path(path_str)
            
        if target.exists() and target.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            self.setStatus.emit(f"Abriendo {target.name} externamente...")
        else:
            self.setStatus.emit("❌ El archivo no existe localmente o no es un archivo válido")

    @Slot(str)
    def deleteItem(self, path_str):
        try:
            p = Path(path_str)
            if not p.exists():
                self.setStatus.emit("❌ Error: El elemento seleccionado no existe")
                return
                
            name = p.name
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
                
            if self._current_editor_path and Path(self._current_editor_path).resolve() == p.resolve():
                self.closeEditorSignal.emit()
                self.closeEditor()
                
            self.scan_and_send()
            self.setStatus.emit(f"🗑️ '{name}' eliminado con éxito")
        except Exception as e:
            self.setStatus.emit(f"❌ Error al eliminar: {e}")

    @Slot(str)
    def openContextDoc(self, path_str):
        if not path_str:
            self.setStatus.emit("Selecciona un elemento primero")
            return
        base = Path(path_str)
        if base.is_file():
            base = base.parent
        for candidate in [
            base / "Contexto_IA.md",
            base / "4_Doc_Obsidian_IA" / "Contexto_IA.md",
            base.parent / "Contexto_IA.md",
        ]:
            if candidate.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))
                return
        self.setStatus.emit("No se encontró Contexto_IA.md en esta ruta")

    @Slot()
    def _sync_drive_internal(self, silent=False):
        if self._drive_in_progress:
            return
        self._drive_in_progress = True
        if not silent:
            self.showLoading.emit(True)
        
        def run():
            try:
                if not self.drive_sync.is_authenticated:
                    if self.drive_sync.is_configured:
                        ok = self.drive_sync.authenticate()
                        if not ok:
                            self.setStatus.emit("No se pudo autenticar con Drive")
                            return
                    else:
                        self.setStatus.emit("Configura Drive en los Ajustes primero")
                        return
                cfg = cloud_sync.load_config()
                base_folder = cfg.get("drive_base_folder_id", "") or "root"
                files = self.drive_sync.list_files(base_folder) or []
                self._drive_nodes[base_folder] = files
                self.scan_and_send()
                self.setStatus.emit(f"Drive: {len(files)} items")
            except Exception as e:
                traceback.print_exc()
                self.setStatus.emit(f"Error Drive: {e}")
            finally:
                self._drive_in_progress = False
                if not silent:
                    self.showLoading.emit(False)
                
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def syncDrive(self):
        self._sync_drive_internal(silent=False)

    def _sync_github_internal(self, token, silent=False):
        if self._github_in_progress:
            return
        self._github_in_progress = True
        if not silent:
            self.showLoading.emit(True)
        
        def run():
            try:
                tok = token
                if not tok:
                    cfg = cloud_sync.load_config()
                    tok = cfg.get("github_token", "")
                if not tok:
                    self.requireGithubToken.emit()
                    return
                ok, err = self.github_sync.authenticate(tok)
                if ok:
                    repos = self.github_sync.list_repos() or []
                    self._github_repos = repos
                    self.scan_and_send()
                    self.setStatus.emit(f"GitHub: {len(repos)} repos")
                else:
                    self.setStatus.emit(f"❌ GitHub: {err or 'Token inválido'}")
                    self.requireGithubToken.emit()
            except Exception as e:
                traceback.print_exc()
                self.setStatus.emit(f"❌ Error GitHub: {e}")
                self.requireGithubToken.emit()
            finally:
                self._github_in_progress = False
                if not silent:
                    self.showLoading.emit(False)
                
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot(str)
    def syncGithub(self, token):
        self._sync_github_internal(token, silent=False)

    @Slot(result=str)
    def getGithubToken(self):
        cfg = cloud_sync.load_config()
        return cfg.get("github_token", "")

    @Slot(str, result=str)
    def testGithubConnection(self, token):
        try:
            ok, err = self.github_sync.authenticate(token)
            if ok:
                repos = self.github_sync.list_repos() or []
                self._github_repos = repos
                
                # Save token in config
                cfg = cloud_sync.load_config()
                cfg["github_token"] = token
                cloud_sync.save_config(cfg)
                
                import json
                return json.dumps({"success": True, "repos": repos})
            else:
                import json
                return json.dumps({"success": False, "error": err or "Token inválido"})
        except Exception as e:
            import json
            return json.dumps({"success": False, "error": str(e)})

    @Slot(str)
    def setGithubSelectedRepo(self, repo):
        cfg = cloud_sync.load_config()
        cfg["github_selected_repo"] = repo
        cloud_sync.save_config(cfg)
        self.scan_and_send()

    @Slot(result=str)
    def getGithubSelectedRepo(self):
        cfg = cloud_sync.load_config()
        return cfg.get("github_selected_repo", "")

    @Slot(result=str)
    def getDeepseekKey(self):
        cfg = cloud_sync.load_config()
        return cfg.get("deepseek_api_key", "")

    @Slot(str, result=str)
    def saveDeepseekKey(self, key):
        try:
            cfg = cloud_sync.load_config()
            cfg["deepseek_api_key"] = key.strip()
            cloud_sync.save_config(cfg)
            import json
            return json.dumps({"success": True})
        except Exception as e:
            import json
            return json.dumps({"success": False, "error": str(e)})

    @Slot(result=str)
    def getAiSettings(self):
        cfg = cloud_sync.load_config()
        model = cfg.get("gemini_model", "gemini-flash-latest")
        if model == "gemini-1.5-flash":
            model = "gemini-flash-latest"
        elif model == "gemini-1.5-pro":
            model = "gemini-pro-latest"
        elif model == "gemini-2.0-flash-exp":
            model = "gemini-2.0-flash"
        return json.dumps({
            "active_provider": cfg.get("active_ai_provider", "deepseek"),
            "deepseek_api_key": cfg.get("deepseek_api_key", ""),
            "gemini_api_key": cfg.get("gemini_api_key", ""),
            "gemini_model": model
        }, ensure_ascii=False)

    @Slot(str, str, str, str, result=str)
    def saveAiSettings(self, provider, deepseek_key, gemini_key, gemini_model):
        try:
            cfg = cloud_sync.load_config()
            cfg["active_ai_provider"] = provider.strip().lower()
            cfg["deepseek_api_key"] = deepseek_key.strip()
            cfg["gemini_api_key"] = gemini_key.strip()
            cfg["gemini_model"] = gemini_model.strip()
            cloud_sync.save_config(cfg)
            return json.dumps({"success": True}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    def _call_ai_api(self, system_prompt, messages_or_prompt, temperature=0.7):
        import urllib.request
        import json
        
        cfg = cloud_sync.load_config()
        provider = cfg.get("active_ai_provider", "deepseek").strip().lower()

        if provider == "gemini":
            api_key = cfg.get("gemini_api_key", "").strip()
            if not api_key:
                raise ValueError("No se ha configurado la clave de API de Gemini. Configúrala en Ajustes.")
            model = cfg.get("gemini_model", "gemini-flash-latest").strip()
            if model == "gemini-1.5-flash":
                model = "gemini-flash-latest"
            elif model == "gemini-1.5-pro":
                model = "gemini-pro-latest"
            elif model == "gemini-2.0-flash-exp":
                model = "gemini-2.0-flash"
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            
            contents = []
            if isinstance(messages_or_prompt, str):
                contents.append({
                    "role": "user",
                    "parts": [{"text": messages_or_prompt}]
                })
            else:
                for msg in messages_or_prompt:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    
                    if role == "system":
                        continue
                    
                    gemini_role = "user" if role == "user" else "model"
                    contents.append({
                        "role": gemini_role,
                        "parts": [{"text": content}]
                    })
            
            data = {
                "contents": contents,
                "generationConfig": {
                    "temperature": temperature
                }
            }
            
            if system_prompt:
                data["systemInstruction"] = {
                    "parts": [{"text": system_prompt}]
                }
                
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=45) as response:
                res_data = response.read().decode("utf-8")
                res_json = json.loads(res_data)
                
                if "candidates" not in res_json or not res_json["candidates"]:
                    if "error" in res_json:
                        raise Exception(res_json["error"].get("message", "Error de Gemini"))
                    raise Exception("La API de Gemini no devolvió respuestas válidas.")
                
                content = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                return content

        else: # deepseek / openrouter
            api_key = cfg.get("deepseek_api_key", "").strip()
            if not api_key:
                raise ValueError("No se ha configurado la clave de API de DeepSeek. Configúrala en Ajustes.")
                
            if api_key.startswith("sk-or-v1-"):
                url = "https://openrouter.ai/api/v1/chat/completions"
                model = "deepseek/deepseek-chat"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/sahid8v/launchpad",
                    "X-Title": "Launchpad Notes App",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                }
            else:
                url = "https://api.deepseek.com/chat/completions"
                model = "deepseek-chat"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                }
                
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
                
            if isinstance(messages_or_prompt, str):
                messages.append({"role": "user", "content": messages_or_prompt})
            else:
                for msg in messages_or_prompt:
                    if msg.get("role") != "system":
                        messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
                        
            data = {
                "model": model,
                "messages": messages,
                "temperature": temperature
            }
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=45) as response:
                res_data = response.read().decode("utf-8")
                res_json = json.loads(res_data)
                
                if "choices" not in res_json or not res_json["choices"]:
                    if "error" in res_json:
                        raise Exception(res_json["error"].get("message", "Error de DeepSeek"))
                    raise Exception("La API de DeepSeek no devolvió respuestas válidas.")
                    
                content = res_json["choices"][0]["message"]["content"].strip()
                return content

    @Slot(str, str, str)
    def generateMermaidDiagram(self, prompt, selected_text, diagram_type):
        import threading
        threading.Thread(
            target=self._generate_mermaid_diagram_worker,
            args=(prompt, selected_text, diagram_type),
            daemon=True
        ).start()

    def _generate_mermaid_diagram_worker(self, prompt, selected_text, diagram_type):
        try:
            cfg = cloud_sync.load_config()
            import json
            import re
            
            templates_info = {
                "flowchart": {
                    "header": "flowchart TD",
                    "desc": "Diagrama de flujo (procesos y decisiones)",
                    "example": "flowchart TD\n    A[Inicio] --> B{¿Es válido?}\n    B -- Sí --> C[Procesar datos]\n    B -- No --> D[Mostrar error]\n    C --> E[Fin]\n    D --> E"
                },
                "class": {
                    "header": "classDiagram",
                    "desc": "Diagrama de clases UML",
                    "example": "classDiagram\n    class Vehiculo {\n        +String marca\n        +encender() void\n    }\n    class Auto {\n        +abrirMaletera() void\n    }\n    Vehiculo <|-- Auto"
                },
                "pie": {
                    "header": "pie",
                    "desc": "Gráfico circular / Pie chart",
                    "example": "pie title Distribución\n    \"Categoría A\" : 45\n    \"Categoría B\" : 55"
                },
                "timeline": {
                    "header": "timeline",
                    "desc": "Línea de tiempo / Timeline",
                    "example": "timeline\n    title Cronograma\n    section Año 2026\n        Enero : Hito 1\n        Febrero : Hito 2"
                },
                "zenuml": {
                    "header": "sequenceDiagram",
                    "desc": "Diagrama de secuencia",
                    "example": "sequenceDiagram\n    title Flujo\n    Usuario->>Servidor: Petición\n    Servidor-->>Usuario: Respuesta"
                },
                "architecture": {
                    "header": "architecture-beta",
                    "desc": "Diagrama de arquitectura cloud",
                    "example": "architecture-beta\n    group api(logos:aws-apigateway)[API]\n    service db(logos:aws-rds)[DB] in api"
                },
                "venn": {
                    "header": "classDiagram",
                    "desc": "Diagrama de Venn (simulado con clases)",
                    "example": "%% Diagrama de Venn (Simulación con Clases)\nclassDiagram\n    class Conjunto_A\n    class Interseccion\n    class Conjunto_B"
                },
                "ishikawa": {
                    "header": "graph LR",
                    "desc": "Diagrama de Ishikawa / Causa-Efecto",
                    "example": "graph LR\n    Causa1 --> Efecto\n    Causa2 --> Efecto"
                },
                "treeview": {
                    "header": "mindmap",
                    "desc": "Estructura de árbol jerárquico",
                    "example": "mindmap\n    root((Proyecto))\n      Carpeta1\n        Archivo1\n      Carpeta2"
                },
                "sequence": {
                    "header": "sequenceDiagram",
                    "desc": "Diagrama de secuencia",
                    "example": "sequenceDiagram\n    Usuario->>Servidor: Petición\n    Servidor-->>Usuario: Respuesta"
                },
                "mindmap": {
                    "header": "mindmap",
                    "desc": "Mapa mental jerárquico",
                    "example": "mindmap\n    root((Tema Principal))\n      Subtema1\n        Detalle1\n      Subtema2"
                },
                "erd": {
                    "header": "erDiagram",
                    "desc": "Diagrama de entidad-relación",
                    "example": "erDiagram\n    CLIENTE ||--o{ ORDEN : realiza\n    CLIENTE {\n        int id PK\n        string nombre\n    }"
                },
                "state": {
                    "header": "stateDiagram-v2",
                    "desc": "Diagrama de estados",
                    "example": "stateDiagram-v2\n    [*] --> Estado1\n    Estado1 --> Estado2 : Transición\n    Estado2 --> [*]"
                },
                "gantt": {
                    "header": "gantt",
                    "desc": "Diagrama de Gantt / Cronograma",
                    "example": "gantt\n    title Cronograma\n    dateFormat YYYY-MM-DD\n    section Tarea\n    Diseño : 2026-06-01, 5d"
                }
            }

            info = templates_info.get(diagram_type, {
                "header": diagram_type,
                "desc": f"Diagrama tipo {diagram_type}",
                "example": f"{diagram_type}\n    %% Escribe aquí tu contenido"
            })

            system_prompt = (
                "Eres un experto en diagramas de Mermaid.js. Tu tarea es convertir el texto seleccionado "
                "o la instrucción del usuario en código de diagrama Mermaid válido.\n"
                "REGLAS CRÍTICAS:\n"
                f"1. El código generado DEBE ser del tipo: '{info['desc']}' y DEBE comenzar obligatoriamente con el encabezado exacto: '{info['header']}'.\n"
                f"Aquí tienes un ejemplo de la estructura correcta esperada para este tipo de diagrama:\n"
                f"```mermaid\n{info['example']}\n```\n"
                "2. NO inventes nuevas sintaxis. Asegúrate de que el diagrama sea 100% sintácticamente válido para Mermaid.js.\n"
                "3. Si el tipo de diagrama es 'mindmap' o 'treeview', recuerda usar la sintaxis de mindmap (ej. 'mindmap', luego 'root((Texto))', etc.).\n"
                "4. Retorna únicamente el código de Mermaid limpio. No incluyas explicaciones de texto, no uses formato markdown de bloques de código (```mermaid / ```), ni comentarios iniciales.\n"
                "5. Empieza el código del diagrama directamente en la primera línea."
            )
            
            user_prompt = f"Tipo de diagrama deseado: {diagram_type}\n"
            if selected_text:
                user_prompt += f"Texto de entrada original:\n{selected_text}\n"
            if prompt:
                user_prompt += f"Instrucciones de estructuración adicionales:\n{prompt}\n"
            if selected_text and prompt:
                user_prompt += "Por favor, combina el texto seleccionado y las instrucciones adicionales para devolver un diagrama Mermaid que se integre con la estructura del texto original."
            elif selected_text:
                user_prompt += "Por favor, usa el texto seleccionado como base para generar el diagrama Mermaid."
            elif prompt:
                user_prompt += "Aplica las instrucciones adicionales para generar el diagrama Mermaid."

            content = self._call_ai_api(system_prompt, user_prompt, temperature=0.2)
            
            # Robust extraction: look for ```mermaid ... ``` or ``` ... ```
            code_match = re.search(r"```(?:mermaid)?\s*([\s\S]+?)\s*```", content)
            if code_match:
                content = code_match.group(1).strip()
            else:
                content = content.replace("```mermaid", "").replace("```", "").strip()
            
            # Strip leading comments (like %%...) for the header check
            clean_content = content
            while clean_content.startswith("%%") or clean_content.startswith("\n") or clean_content.startswith(" "):
                lines = clean_content.splitlines()
                if lines and lines[0].strip().startswith("%%"):
                    clean_content = "\n".join(lines[1:]).strip()
                else:
                    clean_content = clean_content.strip()

            # Ensure it starts with the correct header
            expected_header = info["header"]
            starts_with_correct = False
            if expected_header.startswith("flowchart") or expected_header.startswith("graph"):
                starts_with_correct = clean_content.startswith("flowchart") or clean_content.startswith("graph")
            else:
                starts_with_correct = clean_content.startswith(expected_header)

            if not starts_with_correct:
                common_headers = ["graph", "flowchart", "classDiagram", "pie", "timeline", "zenuml", "architecture", "sequenceDiagram", "mindmap", "erDiagram", "stateDiagram", "gantt"]
                if not any(clean_content.startswith(h) for h in common_headers):
                    content = f"{expected_header}\n{content}"
            
            self.mermaidDiagramGenerated.emit(json.dumps({"success": True, "code": content.strip()}))
        except Exception as e:
            import json
            self.mermaidDiagramGenerated.emit(json.dumps({"success": False, "error": f"Error de conexión con la IA: {str(e)}"}))

    @Slot(str, str)
    def chatWithDeepseek(self, history_json, user_message):
        import threading
        threading.Thread(
            target=self._chat_with_deepseek_worker,
            args=(history_json, user_message),
            daemon=True
        ).start()

    def _chat_with_deepseek_worker(self, history_json, user_message):
        try:
            import json
            try:
                history = json.loads(history_json)
            except Exception:
                history = []
                
            system_prompt = "Eres un asistente general inteligente integrado en una aplicación de notas y mapas mentales. Ayuda al usuario a resumir información, organizar sus pensamientos y responder sus preguntas de forma clara y concisa en formato Markdown."
            
            messages = []
            for msg in history[-10:]:
                if "role" in msg and "content" in msg:
                    messages.append({"role": msg["role"], "content": msg["content"]})
                    
            messages.append({"role": "user", "content": user_message})
            
            content = self._call_ai_api(system_prompt, messages, temperature=0.7)
            self.chatResponseReceived.emit(json.dumps({"success": True, "reply": content}))
        except Exception as e:
            import json
            self.chatResponseReceived.emit(json.dumps({"success": False, "error": f"Error de conexión con la IA: {str(e)}"}))

    @Slot(str, str)
    def runCopilotAction(self, action, text):
        import threading
        threading.Thread(
            target=self._run_copilot_worker,
            args=(action, text),
            daemon=True
        ).start()

    def _run_copilot_worker(self, action, text):
        try:
            import json
            prompts = {
                "summarize": "Eres un asistente de redacción experto. Tu tarea es resumir el siguiente texto de forma concisa y clara en formato Markdown.",
                "improve": "Eres un asistente de redacción experto. Tu tarea es mejorar la redacción, el estilo y la fluidez del siguiente texto, corrigiendo cualquier problema. Devuelve únicamente el texto mejorado en formato Markdown, sin explicaciones ni rodeos.",
                "table": "Eres un asistente de datos experto. Tu tarea es convertir la información y los datos del siguiente texto en una tabla de Markdown bien formateada y limpia. Devuelve únicamente la tabla de Markdown, sin explicaciones ni comentarios.",
                "spelling": "Eres un corrector ortográfico experto. Tu tarea es corregir todos los errores ortográficos y gramaticales del siguiente texto, manteniendo el formato original. Devuelve únicamente el texto corregido, sin explicaciones.",
                "translate": "Eres un traductor experto. Tu tarea es traducir de forma natural el siguiente texto al idioma inglés. Devuelve únicamente la traducción, sin explicaciones ni comentarios."
            }
            
            system_prompt = prompts.get(action, "Eres un asistente de redacción experto.")
            content = self._call_ai_api(system_prompt, text, temperature=0.3)
            self.copilotResultReceived.emit(json.dumps({"success": True, "result": content}))
        except Exception as e:
            import json
            self.copilotResultReceived.emit(json.dumps({"success": False, "error": f"Error de conexión con la IA: {str(e)}"}))


    @Slot(str, str)
    def driveFolderClicked(self, path, remote_id):
        self.showLoading.emit(True)
        def run():
            try:
                items = self.drive_sync.list_files(remote_id)
                self._drive_nodes[remote_id] = items
                self.scan_and_send()
                self._send_drive_subtree(path, remote_id, items)
            except Exception as e:
                self.setStatus.emit(f"Error listando carpeta Drive: {e}")
            finally:
                self.showLoading.emit(False)
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot(str, str)
    def driveFileClicked(self, remote_id, name):
        local_temp_path = self.temp_dir / "drive" / remote_id / name
        is_local = local_temp_path.exists()
        
        if is_local:
            info = {
                "source": "drive", "remote_id": remote_id, "revision": None,
                "remote_repo": None, "sha": None
            }
            self._open_file_by_type(local_temp_path, "drive", info)
            self.setStatus.emit(f"📄 {name} abierto (comprobando actualizaciones...)")
        else:
            self.showLoading.emit(True)
            
        def run():
            try:
                suffix = Path(name).suffix.lower()
                is_binary = is_binary_file(suffix)
                kv = self.drive_sync.get_revision(remote_id)
                info = {
                    "source": "drive", "remote_id": remote_id, "revision": kv,
                    "remote_repo": None, "sha": None
                }
                
                if is_binary:
                    success = self.drive_sync.download_binary(remote_id, local_temp_path)
                    if success and not is_local:
                        self._open_file_by_type(local_temp_path, "drive", info)
                        self.setStatus.emit(f"📁 {name} descargado y abierto desde Drive")
                else:
                    content = self.drive_sync.download(remote_id)
                    if content is None:
                        self.setStatus.emit("❌ Error descargando de Drive")
                        return
                        
                    if local_temp_path.exists():
                        try:
                            local_content = local_temp_path.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            local_content = None
                            
                        if local_content == content:
                            self._open_file_by_type(local_temp_path, "drive", info)
                            self.setStatus.emit(f"📄 {name} está al día con Google Drive")
                        else:
                            self._pending_open_info = {
                                "source": "drive",
                                "remote_id": remote_id,
                                "name": name,
                                "remote_content": content,
                                "local_temp_path": str(local_temp_path)
                            }
                            self.showConflictDialog.emit(json.dumps({
                                "name": name,
                                "service": "Google Drive"
                            }, ensure_ascii=False))
                    else:
                        try:
                            local_temp_path.parent.mkdir(parents=True, exist_ok=True)
                            local_temp_path.write_text(content, encoding="utf-8")
                        except Exception as e:
                            self.setStatus.emit(f"Error escribiendo temporal: {e}")
                            
                        self._open_file_by_type(local_temp_path, "drive", info)
                        self.setStatus.emit(f"📄 {name} descargado y abierto de Drive")
            except Exception as e:
                traceback.print_exc()
                self.setStatus.emit(f"Error abriendo archivo Drive: {e}")
            finally:
                if not is_local:
                    self.showLoading.emit(False)
                    
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot(str, str, str)
    def githubFileClicked(self, repo, path, name):
        local_temp_path = self.temp_dir / "github" / repo / path
        is_local = local_temp_path.exists()
        
        if is_local:
            info = {
                "source": "github", "remote_repo": repo,
                "remote_id": path, "sha": None
            }
            self._open_file_by_type(local_temp_path, "github", info)
            self.setStatus.emit(f"📄 {name} abierto (comprobando actualizaciones...)")
        else:
            self.showLoading.emit(True)
            
        def run():
            try:
                suffix = Path(name).suffix.lower()
                is_binary = is_binary_file(suffix)
                sha = self.github_sync.get_sha(repo, path)
                info = {
                    "source": "github", "remote_repo": repo,
                    "remote_id": path, "sha": sha
                }
                
                if is_binary:
                    success = self.github_sync.download_binary(repo, path, local_temp_path)
                    if success and not is_local:
                        self._open_file_by_type(local_temp_path, "github", info)
                        self.setStatus.emit(f"📁 {name} descargado y abierto desde GitHub | {self.github_sync.api_status}")
                else:
                    content = self.github_sync.download(repo, path)
                    if content is None:
                        self.setStatus.emit("❌ Error descargando de GitHub")
                        return
                        
                    if local_temp_path.exists():
                        try:
                            local_content = local_temp_path.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            local_content = None
                            
                        if local_content == content:
                            self._open_file_by_type(local_temp_path, "github", info)
                            self.setStatus.emit(f"📄 {name} está al día con GitHub | {self.github_sync.api_status}")
                        else:
                            self._pending_open_info = {
                                "source": "github",
                                "repo": repo,
                                "path": path,
                                "name": name,
                                "remote_content": content,
                                "local_temp_path": str(local_temp_path)
                            }
                            self.showConflictDialog.emit(json.dumps({
                                "name": name,
                                "service": "GitHub"
                            }, ensure_ascii=False))
                    else:
                        try:
                            local_temp_path.parent.mkdir(parents=True, exist_ok=True)
                            local_temp_path.write_text(content, encoding="utf-8")
                        except Exception as e:
                            self.setStatus.emit(f"Error escribiendo temporal: {e}")
                            
                        self._open_file_by_type(local_temp_path, "github", info)
                        self.setStatus.emit(f"📄 {name} descargado y abierto de GitHub | {self.github_sync.api_status}")
            except Exception as e:
                traceback.print_exc()
                self.setStatus.emit(f"Error abriendo archivo GitHub: {e}")
            finally:
                if not is_local:
                    self.showLoading.emit(False)
                
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot(bool)
    def resolveConflict(self, use_remote):
        info = self._pending_open_info
        if not info:
            return

        source = info["source"]
        local_path = Path(info["local_temp_path"])
        name = info["name"]

        if use_remote:
            content = info["remote_content"]
            try:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_text(content, encoding="utf-8")
            except Exception as e:
                self.setStatus.emit(f"Error escribiendo archivo temporal: {e}")
        else:
            try:
                content = local_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                self.setStatus.emit(f"Error leyendo archivo temporal: {e}")
                content = info.get("remote_content", "")

        if source == "github":
            repo = info["repo"]
            path = info["path"]
            sha = self.github_sync.get_sha(repo, path)
            info_dict = {
                "source": "github", "remote_repo": repo,
                "remote_id": path, "sha": sha
            }
            self._open_file_by_type(local_path, "github", info_dict)
            self.setStatus.emit(f"📄 {name} abierto desde {'GitHub (servidor)' if use_remote else 'temporal local'}")
        elif source == "drive":
            remote_id = info["remote_id"]
            kv = self.drive_sync.get_revision(remote_id)
            info_dict = {
                "source": "drive", "remote_id": remote_id, "revision": kv,
                "remote_repo": None, "sha": None
            }
            self._open_file_by_type(local_path, "drive", info_dict)
            self.setStatus.emit(f"📄 {name} abierto desde {'Drive (servidor)' if use_remote else 'temporal local'}")

        self._pending_open_info = {}

    @Slot(str, str)
    def githubBrowse(self, repo, path_str):
        if self._download_queue.is_running:
            self.setStatus.emit("⚠️ Descarga en curso, espera...")
            return
        self._set_loading(True)
        import threading
        if threading.current_thread() is threading.main_thread():
            QApplication.processEvents()

        def run():
            try:
                items = self.github_sync.list_files(repo, path_str)
                self._github_nodes[(repo, path_str)] = items
                self.scan_and_send()
                
                md_files = [it for it in items if it[0] == "file" and it[1].endswith(".md") and it[3]]
                if md_files:
                    for item in md_files:
                        _, name, fp, sha = item
                        self._download_queue.enqueue(repo, fp, name)
                    api = self.github_sync.api_status
                    self.setStatus.emit(f"GitHub: {len(items)} items · encolando {len(md_files)} | {api}")
                    self._download_queue.start()
                else:
                    api = self.github_sync.api_status
                    self.setStatus.emit(f"GitHub: {len(items)} items | {api}")
                    self._set_loading(False)
            except Exception as e:
                self.setStatus.emit(f"Error listando GitHub: {e}")
                self._set_loading(False)

        threading.Thread(target=run, daemon=True).start()

    @Slot(int, int)
    def _on_dl_progress(self, current, total):
        self.setStatus.emit(f"Precargando {current}/{total} archivos de GitHub...")

    @Slot()
    def _on_dl_finished(self):
        api = self.github_sync.api_status
        self.setStatus.emit(f"✅ Precarga completa | {api}")
        self._set_loading(False)

    @Slot(str, str, str)
    def _on_dl_ready(self, repo, path, content):
        pass  # Ya cacheado por download()

    @Slot(str, str)
    def filesDropped(self, target_path, files_json):
        paths = json.loads(files_json)
        target = Path(target_path)
        if not target.is_dir():
            target = target.parent if target.is_file() else self.root
        copied = 0
        for p in paths:
            src = Path(p)
            if src.is_file():
                dest = target / src.name
                if dest.exists():
                    stem, suffix = dest.stem, dest.suffix
                    i = 1
                    while dest.exists():
                        dest = target / f"{stem}_{i}{suffix}"
                        i += 1
                try:
                    shutil.copy2(src, dest)
                    copied += 1
                except Exception:
                    pass
        if copied:
            self.scan_and_send()
            self.setStatus.emit(f"{copied} archivo(s) copiado(s)")

    @Slot()
    def closeEditor(self):
        self._current_editor_path = None
        self._current_file_info = {}
        cfg = cloud_sync.load_config()
        cfg.pop("last_editor_file", None)
        cfg.pop("last_editor_source", None)
        cfg.pop("last_editor_info", None)
        cloud_sync.save_config(cfg)

    @Slot()
    def pushCodeToGithub(self):
        self.showLoading.emit(True)
        import threading
        if threading.current_thread() is threading.main_thread():
            QApplication.processEvents()
            
        def run():
            try:
                token = self.github_sync._token
                if not token:
                    cfg = cloud_sync.load_config()
                    token = cfg.get("github_token", "")
                if not token:
                    self.setStatus.emit("❌ Error: No se encontró token de GitHub configurado")
                    self.showLoading.emit(False)
                    return

                from github import Github
                g = Github(token)
                repo = g.get_repo("sahid1129/Notas_Trabajo")
                
                files = ["launchpad.py", "launchpad.bat", "requirements.txt", "assets/ui/index.html", "assets/ui/ui.js", "assets/ui/ui.css"]
                base = Path(__file__).parent
                folder = "Wiki_Estudio_Jun_26/00_Launchpad"
                
                updated_count = 0
                for filename in files:
                    local_file = base / filename
                    if not local_file.exists():
                        continue
                    github_path = f"{folder}/{filename}"
                    content = local_file.read_text(encoding="utf-8")
                    
                    try:
                        remote_file = repo.get_contents(github_path)
                        sha = remote_file.sha
                        remote_content = remote_file.decoded_content.decode("utf-8", errors="replace")
                        if remote_content == content:
                            continue
                        repo.update_file(
                            path=github_path,
                            message=f"Update {filename} via Launchpad UI",
                            content=content,
                            sha=sha
                        )
                        updated_count += 1
                    except Exception as e:
                        if "404" in str(e):
                            repo.create_file(
                                path=github_path,
                                message=f"Create {filename} via Launchpad UI",
                                content=content
                            )
                            updated_count += 1
                        else:
                            raise e
                
                if updated_count > 0:
                    self.setStatus.emit(f"✅ Código subido exitosamente: {updated_count} archivo(s) actualizado(s)")
                else:
                    self.setStatus.emit("✅ Código al día en GitHub (sin cambios locales)")
            except Exception as e:
                traceback.print_exc()
                self.setStatus.emit(f"❌ Error al subir código: {e}")
            finally:
                self.showLoading.emit(False)

        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def selectDriveCreds(self):
        path, _ = QFileDialog.getOpenFileName(None, "Seleccionar credentials.json", "", "JSON (*.json)")
        if path:
            dest = cloud_sync.CREDS_PATH
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            self.setStatus.emit("✅ credentials.json copiado")
            self.syncDrive()

    @Slot()
    def changeRoot(self):
        folder = QFileDialog.getExistingDirectory(None, "Seleccionar carpeta del proyecto", str(self.root))
        if folder:
            self.root = Path(folder)
            cfg = cloud_sync.load_config()
            roots = cfg.get("roots", [])
            if str(self.root) not in roots:
                roots.append(str(self.root))
            cfg["roots"] = roots
            cfg["last_root"] = str(self.root)
            cloud_sync.save_config(cfg)
            self._watch_all_dirs()
            self.scan_and_send()
            self.setStatus.emit(f"Raíz cambiada: {self.root.name}")
            self.setTreeData.emit(json.dumps([
                {"name": self.root.name, "_type": "group", "path": "", "_expanded": True,
                 "children": self._build_tree()}
            ], ensure_ascii=False))

    @Slot(result=str)
    def getRootsJson(self):
        cfg = cloud_sync.load_config()
        data = {
            "current": str(self.root) if self.root else "",
            "roots": cfg.get("roots", [])
        }
        return json.dumps(data, ensure_ascii=False)

    @Slot(str)
    def selectRootPath(self, path):
        p = Path(path)
        if p.exists():
            self.root = p
            cfg = cloud_sync.load_config()
            cfg["last_root"] = str(self.root)
            roots = cfg.get("roots", [])
            if str(self.root) not in roots:
                roots.append(str(self.root))
                cfg["roots"] = roots
            cloud_sync.save_config(cfg)
            self._watch_all_dirs()
            self.scan_and_send()
            self.setStatus.emit(f"Carpeta activa cambiada a: {self.root.name}")
        else:
            self.setStatus.emit(f"❌ Error: La carpeta {path} ya no existe")

    @Slot(str)
    def removeRootPath(self, path):
        cfg = cloud_sync.load_config()
        roots = cfg.get("roots", [])
        if path in roots:
            roots.remove(path)
            cfg["roots"] = roots
            cloud_sync.save_config(cfg)
            self.setStatus.emit(f"Carpeta removida del historial: {Path(path).name}")

    @Slot()
    def clearGithubToken(self):
        self.github_sync.clear_token()
        self.setStatus.emit("Token de GitHub eliminado")

    @Slot()
    def clearDriveToken(self):
        self.drive_sync.clear_token()
        self.setStatus.emit("Credenciales de Google Drive eliminadas")

        has_creds = cloud_sync.CREDS_PATH.exists()
        is_authenticated = self.drive_sync.is_authenticated
        data = {
            "has_creds": has_creds,
            "is_authenticated": is_authenticated
        }
        return json.dumps(data)

    @Slot(result=str)
    def getDriveConfigStatus(self):
        has_creds = cloud_sync.CREDS_PATH.exists()
        is_authenticated = self.drive_sync.is_authenticated
        data = {
            "has_creds": has_creds,
            "is_authenticated": is_authenticated
        }
        return json.dumps(data)

    @Slot(str)
    def setActiveRemoteSource(self, source):
        self.active_remote_source = source
        cfg = cloud_sync.load_config()
        cfg["active_remote_source"] = source
        cloud_sync.save_config(cfg)
        self.scan_and_send()

    @Slot(result=str)
    def getActiveRemoteSource(self):
        cfg = cloud_sync.load_config()
        return cfg.get("active_remote_source", "all")

    @Slot(result=str)
    def getGithubBasePath(self):
        cfg = cloud_sync.load_config()
        return cfg.get("github_base_path", "")

    @Slot(str)
    def saveGithubBasePath(self, path):
        cfg = cloud_sync.load_config()
        cfg["github_base_path"] = path.strip()
        cloud_sync.save_config(cfg)
        self.scan_and_send()

    @Slot(result=str)
    def getDriveBaseFolderId(self):
        cfg = cloud_sync.load_config()
        return cfg.get("drive_base_folder_id", "")

    @Slot(str)
    def saveDriveBaseFolderId(self, folder_id):
        cfg = cloud_sync.load_config()
        cfg["drive_base_folder_id"] = folder_id.strip()
        cloud_sync.save_config(cfg)
        self.scan_and_send()

    @Slot()
    def fetchGithubFolders(self):
        import threading
        import json
        def run():
            cfg = cloud_sync.load_config()
            repo = cfg.get("github_selected_repo", "")
            if not repo:
                self.githubFoldersLoaded.emit(json.dumps([]))
                return
            folders = self.github_sync.get_all_folders(repo)
            self.githubFoldersLoaded.emit(json.dumps(folders))
        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def fetchDriveFolders(self):
        import threading
        import json
        def run():
            folders = self.drive_sync.get_all_folders()
            self.driveFoldersLoaded.emit(json.dumps(folders))
        threading.Thread(target=run, daemon=True).start()

    @Slot(str, result=str)
    def getFileBase64(self, path):
        import base64
        import urllib.parse
        import os
        try:
            if "%" in path:
                path = urllib.parse.unquote(path)
            if path.startswith("file:///"):
                path = path[8:]
            normalized_path = os.path.normpath(path)
            p = Path(normalized_path)
            if p.exists() and p.is_file():
                data = p.read_bytes()
                return base64.b64encode(data).decode("utf-8")
        except Exception as e:
            print(f"Error reading base64 for file {path}: {e}")
        return ""

    @Slot(str, str, result=bool)
    def saveFileBase64(self, path, base64_data):
        import base64
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = base64.b64decode(base64_data)
            p.write_bytes(data)
            self.scan_and_send()
            return True
        except Exception as e:
            print(f"Error saving base64 for file {path}: {e}")
            return False

    def _open_file_by_type(self, p, source="local", info=None):
        suffix = p.suffix.lower()
        if suffix == ".md":
            self._load_editor(p, source, info)
        elif suffix in (".docx", ".doc", ".xlsx", ".xls", ".csv"):
            self._load_office_editor(p, source, info)
        else:
            self._load_visualizer(p, source, info)

    def _load_office_editor(self, p, source="local", info=None):
        self._current_editor_path = p
        if info is not None:
            self._current_file_info = info
        else:
            self._current_file_info = {"source": source, "remote_id": None, "remote_repo": None, "sha": None}
        data = {
            "path": str(p),
            "name": p.name,
            "content": "",
            "source": source,
            "is_office": True,
            "suffix": p.suffix.lower(),
            "info": self._current_file_info
        }
        self.showEditor.emit(json.dumps(data, ensure_ascii=False))
        
        cfg = cloud_sync.load_config()
        cfg["last_editor_file"] = str(p)
        cfg["last_editor_source"] = source
        cfg["last_editor_info"] = self._current_file_info
        cfg.pop("last_selected_folder", None)
        cloud_sync.save_config(cfg)

    def _load_visualizer(self, p, source="local", info=None):
        suffix = p.suffix.lower()
        if info is not None:
            self._current_file_info = info
        else:
            self._current_file_info = {"source": source, "remote_id": None, "remote_repo": None, "sha": None}
        
        if suffix in EXT_IMG:
            vtype = "image"
        elif suffix == ".pdf":
            vtype = "pdf"
        elif suffix in (".mp4", ".webm", ".ogg", ".mov", ".avi", ".mkv"):
            vtype = "video"
        elif suffix in EXT_AUDIO:
            vtype = "audio"
        elif suffix == ".ipynb":
            vtype = "notebook"
        elif suffix in EXT_TXT or suffix in (".py", ".js", ".html", ".css", ".json", ".bat", ".sh"):
            vtype = "code"
        else:
            is_bin = is_binary_file(suffix)
            if not is_bin:
                vtype = "code"
            else:
                vtype = "generic"
            
        self.triggerVisualizerWindow.emit(str(p), p.name, vtype)

    def _parse_cloud_path(self, path_str):
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
            cfg = cloud_sync.load_config()
            folder_id = cfg.get("drive_base_folder_id", "") or "root"
            return {"service": "drive", "folder_id": folder_id}
            
        return None

    @Slot(str, str)
    def createCloudFile(self, selected_path_str, filename):
        self.showLoading.emit(True)
        def run():
            try:
                parsed = self._parse_cloud_path(selected_path_str)
                if not parsed:
                    self.setStatus.emit("❌ Error: No se pudo determinar el destino en la nube")
                    self.showLoading.emit(False)
                    return
                
                service = parsed["service"]
                default_content = f"# {filename.replace('.md', '').replace('_', ' ')}\n"
                
                if service == "github":
                    repo = parsed["repo"]
                    inner_path = parsed["path"]
                    full_path = f"{inner_path}/{filename}" if inner_path else filename
                    
                    self.setStatus.emit(f"Creando '{filename}' en GitHub...")
                    token = self.github_sync._token
                    if not token:
                        cfg = cloud_sync.load_config()
                        token = cfg.get("github_token", "")
                    
                    from github import Github
                    g = Github(token)
                    repo_obj = g.get_repo(repo)
                    
                    repo_obj.create_file(
                        path=full_path,
                        message=f"Create {filename} via Launchpad UI",
                        content=default_content
                    )
                    
                    cloud_sync._cache_clear_repo("github", repo)
                    self._github_nodes[(repo, inner_path)] = self.github_sync.list_files(repo, inner_path)
                    self.scan_and_send()
                    
                    local_temp_path = self.temp_dir / "github" / repo / full_path
                    local_temp_path.parent.mkdir(parents=True, exist_ok=True)
                    local_temp_path.write_text(default_content, encoding="utf-8")
                    
                    new_sha = self.github_sync.get_sha(repo, full_path)
                    info = {
                        "source": "github",
                        "remote_repo": repo,
                        "remote_id": full_path,
                        "sha": new_sha
                    }
                    self._open_file_by_type(local_temp_path, "github", info)
                    self.setStatus.emit(f"✅ Archivo '{filename}' creado y abierto desde GitHub")
                    
                elif service == "drive":
                    folder_id = parsed["folder_id"]
                    self.setStatus.emit(f"Creando '{filename}' en Google Drive...")
                    
                    if not self.drive_sync.is_authenticated:
                        self.drive_sync.authenticate()
                        
                    ok, file_id, rev = self.drive_sync.create_file(folder_id, filename, default_content)
                    if ok:
                        cloud_sync._cache_clear_repo("drive", folder_id)
                        cloud_sync._cache_clear_repo("drive", "root")
                        cfg_drive = cloud_sync.load_config()
                        base_folder = cfg_drive.get("drive_base_folder_id", "") or "root"
                        cloud_sync._cache_clear_repo("drive", base_folder)
                        
                        self._drive_nodes[folder_id] = self.drive_sync.list_files(folder_id)
                        self.scan_and_send()
                        
                        local_temp_path = self.temp_dir / "drive" / file_id / filename
                        local_temp_path.parent.mkdir(parents=True, exist_ok=True)
                        local_temp_path.write_text(default_content, encoding="utf-8")
                        
                        info = {
                            "source": "drive",
                            "remote_id": file_id,
                            "revision": rev,
                            "remote_repo": None,
                            "sha": None
                        }
                        self._open_file_by_type(local_temp_path, "drive", info)
                        self.setStatus.emit(f"✅ Archivo '{filename}' creado y abierto desde Google Drive")
                    else:
                        self.setStatus.emit("❌ Error al crear archivo en Google Drive")
            except Exception as e:
                self.setStatus.emit(f"❌ Error al crear archivo en la nube: {e}")
            finally:
                self.showLoading.emit(False)
                
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot(str, str)
    def renameCloudItem(self, node_json, new_name):
        self.showLoading.emit(True)
        def run():
            try:
                node = json.loads(node_json)
                ntype = node.get("_type")
                
                if ntype in ("github_file", "dir"):
                    repo = node.get("repo")
                    old_path = node.get("path")
                    sha = node.get("sha")
                    
                    old_path_p = Path(old_path)
                    new_path = str(old_path_p.parent / new_name).replace("\\", "/")
                    if old_path_p.parent == Path("."):
                        new_path = new_name
                    
                    self.setStatus.emit(f"Renombrando {old_path_p.name} a {new_name} en GitHub...")
                    ok = self.github_sync.rename_file(repo, old_path, new_path, sha)
                    if ok:
                        cloud_sync._cache_clear_repo("github", repo)
                        parent_dir = str(old_path_p.parent).replace("\\", "/")
                        if parent_dir == ".":
                            parent_dir = ""
                        self._github_nodes[(repo, parent_dir)] = self.github_sync.list_files(repo, parent_dir)
                        
                        if self._current_editor_path:
                            local_old_path = self.temp_dir / "github" / repo / old_path
                            local_new_path = self.temp_dir / "github" / repo / new_path
                            if local_old_path.exists():
                                try:
                                    local_new_path.parent.mkdir(parents=True, exist_ok=True)
                                    local_old_path.rename(local_new_path)
                                except Exception:
                                    pass
                            
                            if Path(self._current_editor_path).resolve() == local_old_path.resolve() or str(self._current_editor_path) == str(local_old_path):
                                self._current_editor_path = local_new_path
                                self._current_file_info["remote_id"] = new_path
                                new_sha = self.github_sync.get_sha(repo, new_path)
                                if new_sha:
                                    self._current_file_info["sha"] = new_sha
                                
                                data = {
                                    "path": str(local_new_path),
                                    "name": new_name,
                                    "content": local_new_path.read_text(encoding="utf-8", errors="replace") if local_new_path.exists() else "",
                                    "source": "github"
                                }
                                self.showEditor.emit(json.dumps(data, ensure_ascii=False))
                        
                        self.scan_and_send()
                        self.setStatus.emit(f"✅ Renombrado exitosamente a {new_name}")
                    else:
                        self.setStatus.emit("❌ Error al renombrar en GitHub")
                        
                elif ntype in ("drive_file", "drive_folder"):
                    file_id = node.get("remoteId")
                    self.setStatus.emit(f"Renombrando en Google Drive...")
                    ok = self.drive_sync.rename_file(file_id, new_name)
                    if ok:
                        cloud_sync._cache_clear_repo("drive", "root")
                        self._drive_nodes = {}
                        self.syncDrive()
                        
                        if self._current_editor_path and self._current_file_info.get("remote_id") == file_id:
                            local_old_path = Path(self._current_editor_path)
                            local_new_path = local_old_path.parent / new_name
                            if local_old_path.exists():
                                try:
                                    local_old_path.rename(local_new_path)
                                except Exception:
                                    pass
                            self._current_editor_path = local_new_path
                            data = {
                                "path": str(local_new_path),
                                "name": new_name,
                                "content": local_new_path.read_text(encoding="utf-8", errors="replace") if local_new_path.exists() else "",
                                "source": "drive"
                            }
                            self.showEditor.emit(json.dumps(data, ensure_ascii=False))
                            
                        self.setStatus.emit(f"✅ Renombrado en Google Drive exitosamente")
            except Exception as e:
                self.setStatus.emit(f"❌ Error al renombrar: {e}")
            finally:
                self.showLoading.emit(False)
                 
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot(str)
    def deleteCloudItem(self, node_json):
        self.showLoading.emit(True)
        def run():
            try:
                node = json.loads(node_json)
                ntype = node.get("_type")
                
                if ntype in ("github_file", "dir"):
                    repo = node.get("repo")
                    path = node.get("path")
                    sha = node.get("sha")
                    
                    if not sha:
                        sha = self.github_sync.get_sha(repo, path)
                        
                    self.setStatus.emit(f"Eliminando {Path(path).name} en GitHub...")
                    ok = self.github_sync.delete_file(repo, path, sha)
                    if ok:
                        cloud_sync._cache_clear_repo("github", repo)
                        path_p = Path(path)
                        parent_dir = str(path_p.parent).replace("\\", "/")
                        if parent_dir == ".":
                            parent_dir = ""
                        self._github_nodes[(repo, parent_dir)] = self.github_sync.list_files(repo, parent_dir)
                        
                        local_path = self.temp_dir / "github" / repo / path
                        if self._current_editor_path and (Path(self._current_editor_path).resolve() == local_path.resolve() or str(self._current_editor_path) == str(local_path)):
                            self.closeEditorSignal.emit()
                            self.closeEditor()
                            
                        try:
                            if local_path.exists():
                                local_path.unlink()
                        except Exception:
                            pass
                             
                        self.scan_and_send()
                        self.setStatus.emit(f"✅ Eliminado exitosamente de GitHub")
                    else:
                        self.setStatus.emit("❌ Error al eliminar de GitHub")
                        
                elif ntype in ("drive_file", "drive_folder"):
                    file_id = node.get("remoteId")
                    self.setStatus.emit(f"Eliminando de Google Drive...")
                    ok = self.drive_sync.delete_file(file_id)
                    if ok:
                        cloud_sync._cache_clear_repo("drive", "root")
                        self._drive_nodes = {}
                        self.syncDrive()
                        
                        if self._current_editor_path and self._current_file_info.get("remote_id") == file_id:
                            self.closeEditorSignal.emit()
                            self.closeEditor()
                             
                        self.setStatus.emit(f"✅ Eliminado exitosamente de Google Drive")
            except Exception as e:
                self.setStatus.emit(f"❌ Error al eliminar: {e}")
            finally:
                self.showLoading.emit(False)
                 
        import threading
        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def ready(self):
        issues = []
        cfg = cloud_sync.load_config()
        if not self.root.exists():
            issues.append("Raíz no existe")
            last = cfg.get("last_root", "")
            if last and Path(last).exists():
                self.root = Path(last)
                issues.pop()
        if self.root.exists() and len(list(self.root.iterdir())) == 0:
            issues.append("Carpeta vacía")
        if not (Path(__file__).parent / "assets" / "editor" / "lib" / "toastui-editor.js").exists():
            issues.append("Falta Toast UI Editor")
        if issues:
            self.setStatus.emit("⚠️ " + " · ".join(issues))
            import threading
            if threading.current_thread() is threading.main_thread():
                QApplication.processEvents()
            if not self.root.exists() or len(list(self.root.iterdir())) == 0:
                self.changeRoot()
                return
        self._watch_all_dirs()
        self.scan_and_send()

        # Auto-sincronizar GitHub y Drive de forma silenciosa al iniciar
        if self.github_sync.is_configured:
            self._sync_github_internal("", silent=True)
        if self.drive_sync.is_configured:
            self._sync_drive_internal(silent=True)
        # Restablecer el último estado de archivo o carpeta
        self._restore_last_state()

    # ========== Scan & Send Tree ==========

    def scan_and_send(self):
        self._suppress_refresh = True
        tree = self._build_tree()
        self.setTreeData.emit(json.dumps(tree, ensure_ascii=False))
        self._suppress_refresh = False
        import threading
        if threading.current_thread() is threading.main_thread():
            QApplication.processEvents()

    def _is_expanded(self, key, default_val):
        return self._expanded_states.get(key, default_val)

    def _walk_dir(self, path, depth=0):
        """Recursively build tree node for a directory and all subdirectories."""
        items = sorted(path.iterdir())
        children = []
        file_count = 0
        for item in items:
            name = item.name
            if name.startswith(".") or name == "__pycache__":
                continue
            if item.is_dir():
                child = self._walk_dir(item, depth + 1)
                # Skip empty directories in display
                if child["children"]:
                    children.append(child)
                else:
                    children.append(child)
            elif item.is_file():
                file_count += 1
                color = COLOR_BLUE if item.suffix == ".md" else "#6b7280"
                children.append({
                    "name": name, "_type": "file", "path": str(item),
                    "color": color, "size": self._fmt_size(item.stat().st_size)
                })
        count = sum(1 for c in children if c["_type"] == "file")
        return {
            "name": path.name, "_type": "folder", "path": str(path),
            "color": COLOR_GREEN if count > 0 else COLOR_GRAY,
            "droppable": True, "count": count,
            "_expanded": self._is_expanded(str(path), depth < 2), "children": children
        }

    def _build_tree(self):
        tree = []
        skip = {"00_Launchpad", ".obsidian", "__pycache__", ".git"}
        dirs = sorted(p for p in self.root.iterdir() if p.is_dir() and p.name not in skip)
        grouped = {"areas": [], "extras": [], "bandejas": []}
        for p in dirs:
            if self._is_prefixed(p.name):
                grouped["areas"].append(p)
            elif p.name in ("00_Plantillas_Base", "99_Recursos_y_Catalogos"):
                grouped["extras"].append(p)
            elif p.name in ("_inbox", "_sandbox"):
                grouped["bandejas"].append(p)

        for area_dir in grouped["areas"]:
            area_node = {"name": area_dir.name, "_type": "area", "path": str(area_dir),
                         "_expanded": self._is_expanded(str(area_dir), True), "children": []}
            for bloque_dir in sorted(area_dir.iterdir()):
                if not bloque_dir.is_dir() or not self._is_prefixed(bloque_dir.name):
                    continue
                bloque_node = {"name": bloque_dir.name, "_type": "bloque", "path": str(bloque_dir),
                               "_expanded": self._is_expanded(str(bloque_dir), False), "children": []}
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
                            "name": name, "_type": "file", "path": str(item),
                            "color": color, "size": self._fmt_size(item.stat().st_size)
                        })
                area_node["children"].append(bloque_node)
            if area_node["children"]:
                tree.append(area_node)

        if grouped["extras"]:
            extra = {"name": "Base y Catálogos", "_type": "group", "path": "", "_expanded": self._is_expanded("group://Base y Catálogos", True), "children": []}
            for p in grouped["extras"]:
                child = self._walk_dir(p)
                if child:
                    extra["children"].append(child)
            if extra["children"]:
                tree.append(extra)

        if grouped["bandejas"]:
            band = {"name": "Bandejas", "_type": "group", "path": "", "_expanded": self._is_expanded("group://Bandejas", True), "children": []}
            for p in grouped["bandejas"]:
                child = self._walk_dir(p)
                if child:
                    band["children"].append(child)
            if band["children"]:
                tree.append(band)

        active_src = self.active_remote_source

        if active_src in ("all", "drive"):
            cfg = cloud_sync.load_config()
            drive_base_folder_id = cfg.get("drive_base_folder_id", "") or "root"
            drive_node = {"name": "☁️ Google Drive", "_type": "drive", "path": "drive://",
                          "_expanded": self._is_expanded("drive://", False), "children": []}
            self._populate_drive_subtree(drive_node, drive_base_folder_id)
            tree.append(drive_node)

        if active_src in ("all", "github"):
            # Filter repos to only show the selected one (if configured)
            cfg = cloud_sync.load_config()
            selected_repo = cfg.get("github_selected_repo", "")
            github_base_path = cfg.get("github_base_path", "")

            github_node = {"name": "🐙 GitHub", "_type": "github", "path": "github://",
                           "_expanded": self._is_expanded("github://", bool(self._github_repos)), "children": []}
            if self._github_repos:
                for full_name, name, default_branch in self._github_repos:
                    if selected_repo and full_name != selected_repo:
                        continue
                    repo_key = f"github://repo/{full_name}"
                    repo_node = {
                        "name": f"📁 {full_name}",
                        "_type": "github_repo",
                        "repo": full_name,
                        "path": github_base_path,
                        "color": COLOR_VIOLET,
                        "_expanded": self._is_expanded(repo_key, False),
                        "children": []
                    }
                    self._populate_github_subtree(repo_node, full_name, github_base_path)
                    github_node["children"].append(repo_node)
            tree.append(github_node)

        return tree

    def _populate_drive_subtree(self, parent_node, folder_id):
        items = self._drive_nodes.get(folder_id)
        if items is not None:
            parent_key = parent_node.get("path")
            parent_node["_expanded"] = self._is_expanded(parent_key, True)
            for entry in items:
                ftype, name, fid, mtime, ext = entry
                if ftype == "folder":
                    folder_key = f"drive://folder/{fid}"
                    folder_node = {
                        "name": f"📁 {name}", "_type": "drive_folder",
                        "path": folder_key, "remoteId": fid,
                        "_expanded": self._is_expanded(folder_key, False), "children": []
                    }
                    self._populate_drive_subtree(folder_node, fid)
                    parent_node["children"].append(folder_node)
                else:
                    icon, color = self._icon_for_ext(ext)
                    parent_node["children"].append({
                        "name": f"{icon} {name}", "_type": "drive_file",
                        "path": f"drive://file/{fid}", "remoteId": fid, "remoteName": name,
                        "color": color
                    })

    def _populate_github_subtree(self, parent_node, repo, path):
        items = self._github_nodes.get((repo, path))
        if items is not None:
            if parent_node["_type"] == "github_repo":
                parent_key = f"github://repo/{repo}"
            else:
                parent_key = f"github://dir/{repo}/{path}"
            parent_node["_expanded"] = self._is_expanded(parent_key, True)
            for entry in items:
                if entry[0] == "dir":
                    _, name, fp = entry
                    dir_key = f"github://dir/{repo}/{fp}"
                    dir_node = {
                        "name": f"📁 {name}", "_type": "dir",
                        "repo": repo, "path": fp, "color": COLOR_VIOLET,
                        "_expanded": self._is_expanded(dir_key, False), "children": []
                    }
                    self._populate_github_subtree(dir_node, repo, fp)
                    parent_node["children"].append(dir_node)
                elif entry[0] == "file":
                    _, name, fp, sha = entry
                    icon, color = self._icon_for_file(name)
                    parent_node["children"].append({
                        "name": f"{icon} {name}", "_type": "github_file",
                        "repo": repo, "path": fp, "color": color, "sha": sha,
                        "remoteName": name,
                        "_expanded": False, "children": []
                    })



    def _send_drive_subtree(self, parent_path, folder_id, items):
        js = json.dumps({
            "parentPath": parent_path,
            "items": [{
                "name": f"📁 {n}" if t == "folder" else f"{self._icon_for_ext(e)[0]} {n}",
                "_type": "drive_folder" if t == "folder" else "drive_file",
                "path": f"drive://{'folder' if t == 'folder' else 'file'}/{iid}",
                "remoteId": iid, "remoteName": n,
                "color": self._icon_for_ext(e)[1] if t != "folder" else COLOR_VIOLET
            } for t, n, iid, _, e in items]
        }, ensure_ascii=False)
        # Send as folder info update
        self.showFolderInfo.emit(json.dumps({
            "title": f"📁 {Path(parent_path).name}",
            "meta": f"{len(items)} items",
            "files": [{"name": f"📁 {n}" if t == "folder" else f"{self._icon_for_ext(e)[0]} {n}",
                       "color": self._icon_for_ext(e)[1] if t != "folder" else COLOR_VIOLET}
                      for t, n, _, _, e in items]
        }))



    def _icon_for_ext(self, ext):
        icons = {".md": ("📄", COLOR_BLUE), ".docx": ("📃", "#6b7280"),
                 ".pdf": ("📃", "#dc2626"), ".xlsx": ("📊", "#16a34a"),
                 ".csv": ("📊", "#16a34a"), ".png": ("🖼️", "#9333ea"),
                 ".jpg": ("🖼️", "#9333ea"), ".jpeg": ("🖼️", "#9333ea")}
        return icons.get(ext, ("📎", "#6b7280"))

    def _icon_for_file(self, name):
        return self._icon_for_ext(Path(name).suffix.lower())

    def _is_prefixed(self, name):
        return len(name) > 2 and name[:2].isdigit() and name[2] == "_"

    def _fmt_size(self, bytes_):
        if bytes_ < 1024:
            return f"{bytes_} B"
        elif bytes_ < 1024 ** 2:
            return f"{bytes_ / 1024:.1f} KB"
        elif bytes_ < 1024 ** 3:
            return f"{bytes_ / 1024 ** 2:.1f} MB"
        return f"{bytes_ / 1024 ** 3:.1f} GB"

    def _fmt_date(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")

    # ========== Show Info / Editor / Image ==========

    def _load_editor(self, p, source="local", info=None):
        print(f"DEBUG BACKEND: _load_editor entry for {p.name}", flush=True)
        try:
            content = p.read_text("utf-8", errors="replace")
            print(f"DEBUG BACKEND: read {len(content)} characters from {p.name}", flush=True)
        except Exception as e:
            print(f"DEBUG BACKEND: failed to read {p.name}: {e}", flush=True)
            content = ""
        self._current_editor_path = p
        if info is not None:
            self._current_file_info = info
        else:
            self._current_file_info = {"source": source, "remote_id": None,
                                       "remote_repo": None, "sha": None}
        print(f"DEBUG BACKEND: emitting showEditor for {p.name}", flush=True)
        data = {
            "path": str(p),
            "name": p.name,
            "content": content,
            "source": source,
            "info": self._current_file_info
        }
        self.showEditor.emit(json.dumps(data, ensure_ascii=False))
        
        # Guardar en config el último archivo editado
        cfg = cloud_sync.load_config()
        cfg["last_editor_file"] = str(p)
        cfg["last_editor_source"] = source
        cfg["last_editor_info"] = self._current_file_info
        cfg.pop("last_selected_folder", None)
        cloud_sync.save_config(cfg)
        import threading
        if threading.current_thread() is threading.main_thread():
            QApplication.processEvents()

    def _show_folder_info(self, p):
        index_path = p / "4_Doc_Obsidian_IA" / "index.md"
        if index_path.exists():
            self._show_bloque_info(p, index_path)
            return
        files = sorted(f for f in p.iterdir() if f.is_file())
        parent_name = p.parent.name if p.parent != self.root else "Raíz"
        items = []
        for f in files:
            sz = self._fmt_size(f.stat().st_size)
            dt = self._fmt_date(f.stat().st_mtime)
            items.append({"name": f.name, "size": sz, "date": dt, "path": str(f)})
        self.showFolderInfo.emit(json.dumps({
            "title": p.name, "meta": f"Ubicación: {parent_name} / {p.name}\nArchivos: {len(files)}",
            "files": items, "path": str(p)
        }, ensure_ascii=False))
 
        # Guardar en config la última carpeta seleccionada
        cfg = cloud_sync.load_config()
        cfg["last_selected_folder"] = str(p)
        cfg.pop("last_editor_file", None)
        cloud_sync.save_config(cfg)
        import threading
        if threading.current_thread() is threading.main_thread():
            QApplication.processEvents()

    def _show_bloque_info(self, bloque_path, index_path):
        id_tag = self._parse_frontmatter_id(index_path)
        pages, sources = self._parse_index_table(index_path)
        title = f"{id_tag} — {bloque_path.name}" if id_tag else bloque_path.name
        raw_path = bloque_path / "1_Data_Raw"
        n_src = sum(1 for _ in raw_path.iterdir()) if raw_path.is_dir() else 0
        pages_data = []
        for page, ptype, desc in pages:
            pages_data.append({"page": page, "type": ptype, "desc": desc,
                               "typeColor": COLOR_PAGE.get(ptype, "#333")})
        self.showBlocInfo.emit(json.dumps({
            "title": title, "meta": f"Bloque: {bloque_path.name}\n{len(pages)} páginas wiki · {n_src} fuentes",
            "pages": pages_data, "sources": sources, "path": str(bloque_path)
        }, ensure_ascii=False))
 
        # Guardar en config la última carpeta/bloque seleccionada
        cfg = cloud_sync.load_config()
        cfg["last_selected_folder"] = str(bloque_path)
        cfg.pop("last_editor_file", None)
        cloud_sync.save_config(cfg)
        import threading
        if threading.current_thread() is threading.main_thread():
            QApplication.processEvents()

    def _parse_frontmatter_id(self, path):
        try:
            text = path.read_text("utf-8", errors="replace")
            m = re.search(r'^id:\s*"([^"]+)"', text, re.MULTILINE)
            return m.group(1) if m else None
        except Exception:
            return None

    def _parse_index_table(self, path):
        pages, sources = [], []
        try:
            text = path.read_text("utf-8", errors="replace")
            in_page, in_src = False, False
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("| Página | Tipo | Descripción |"):
                    in_page, in_src = True, False; continue
                if s.startswith("| Archivo | Descripción |"):
                    in_src, in_page = True, False; continue
                if in_page:
                    if s.startswith("|---") or not s.startswith("|") or s == "|":
                        in_page = False; continue
                    parts = [x.strip() for x in s.split("|")]
                    if len(parts) >= 4:
                        pages.append((parts[1].strip("[]"), parts[2], parts[3]))
                if in_src:
                    if not s.startswith("|") or s == "|":
                        in_src = False; continue
                    if s.startswith("|---"): continue
                    parts = [x.strip() for x in s.split("|")]
                    if len(parts) >= 3:
                        src = parts[1].strip("`")
                        if src and not src.startswith("Archivo"):
                            desc = parts[2] if len(parts) > 2 else ""
                            sources.append(f"{src} — {desc}" if desc else src)
        except Exception:
            pass
        return pages, sources

    def _show_image(self, p):
        pixmap = QPixmap(str(p))
        data_uri = ""
        if not pixmap.isNull():
            max_dim = 600
            if pixmap.width() > max_dim or pixmap.height() > max_dim:
                pixmap = pixmap.scaled(max_dim, max_dim, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            pixmap.save(buf, "PNG")
            buf.close()
            raw_bytes = bytes(buf.data())
            data_uri = "data:image/png;base64," + base64.b64encode(raw_bytes).decode()
        self.showImage.emit(json.dumps({
            "path": str(p),
            "name": p.name,
            "dataUri": data_uri
        }, ensure_ascii=False))

    def _show_file_info(self, p):
        sz = self._fmt_size(p.stat().st_size)
        dt = self._fmt_date(p.stat().st_mtime)
        self.showFileInfo.emit(json.dumps({
            "name": p.name, "parent": p.parent.name,
            "size": sz, "date": dt
        }, ensure_ascii=False))

    def _restore_last_state(self):
        try:
            cfg = cloud_sync.load_config()
            last_editor_file = cfg.get("last_editor_file", "")
            last_editor_source = cfg.get("last_editor_source", "local")
            last_editor_info = cfg.get("last_editor_info", {})
            
            if last_editor_file and Path(last_editor_file).exists():
                self._open_file_by_type(Path(last_editor_file), last_editor_source, last_editor_info)
            else:
                last_selected_folder = cfg.get("last_selected_folder", "")
                if last_selected_folder and Path(last_selected_folder).exists():
                    self._show_folder_info(Path(last_selected_folder))
        except Exception as e:
            print(f"Error al restaurar último estado: {e}")

    @Slot(str, str, str)
    def _on_trigger_visualizer_window(self, path, name, vtype):
        win = VisualizerWindow(path, name, vtype, self)
        self._visualizer_windows.append(win)
        win.show()

    @Slot(str, str, str)
    def openVisualizerWindow(self, path, name, vtype):
        self.triggerVisualizerWindow.emit(path, name, vtype)


class VisualizerWindow(QMainWindow):
    def __init__(self, file_path, name, vtype, parent_app, parent=None):
        super().__init__(parent)
        self.parent_app = parent_app
        self.setWindowTitle(f"Visualizador: {name}")
        self.resize(950, 750)
        
        self.web = QWebEngineView(self)
        self.setCentralWidget(self.web)
        
        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.parent_app)
        self.web.page().setWebChannel(self.channel)
        
        import urllib.parse
        html_path = Path(__file__).resolve().parent / "assets" / "ui" / "visualizer.html"
        params = {
            "path": file_path,
            "name": name,
            "type": vtype
        }
        url_params = urllib.parse.urlencode(params)
        url = f"file:///{str(html_path).replace('\\', '/')}?{url_params}"
        self.web.setUrl(QUrl(url))
        
    def closeEvent(self, event):
        if self.parent_app and hasattr(self.parent_app, "_visualizer_windows"):
            try:
                self.parent_app._visualizer_windows.remove(self)
            except ValueError:
                pass
        event.accept()


class ConsoleWebEnginePage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        level_name = str(level).split(".")[-1]
        msg_str = f"JS Console [{level_name}]: {message} (at {sourceID}:{lineNumber})"
        try:
            print(msg_str, flush=True)
        except UnicodeEncodeError:
            try:
                enc = sys.stdout.encoding or 'utf-8'
                print(msg_str.encode(enc, errors='replace').decode(enc), flush=True)
            except Exception:
                pass


class AppWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.root = Path(__file__).resolve().parent.parent
        self.setWindowTitle(f"Bóveda — {self.root.name}")
        self.setMinimumSize(1200, 750)
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.web = QWebEngineView()
        self.web.setPage(ConsoleWebEnginePage(self.web))
        
        # Enable built-in PDF viewer and local file accesses
        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        
        self.web.setMinimumSize(400, 300)

        self.channel = QWebChannel()
        self.bridge = UIBridge(self.root, self)
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

        ui_html = Path(__file__).parent / "assets" / "ui" / "index.html"
        self.web.load(QUrl.fromLocalFile(str(ui_html)))
        layout.addWidget(self.web)

        self.bridge.drive_sync.error.connect(lambda m: self.bridge.setStatus.emit(m))
        self.bridge.github_sync.error.connect(lambda m: self.bridge.setStatus.emit(m))

    def closeEvent(self, event):
        from PySide6.QtWidgets import QDialog, QCheckBox, QPushButton, QLabel, QHBoxLayout, QVBoxLayout
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Limpieza al salir")
        dialog.setMinimumWidth(380)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #1e1e2e;
                color: #cdd6f4;
                font-family: 'Outfit', 'Inter', sans-serif;
            }
            QLabel {
                color: #cdd6f4;
                font-size: 13px;
            }
            QCheckBox {
                color: #cdd6f4;
                font-size: 13px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #45475a;
                background-color: #313244;
            }
            QCheckBox::indicator:checked {
                background-color: #89b4fa;
                border-color: #89b4fa;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45475a;
            }
            QPushButton#btn-confirm {
                background-color: #89b4fa;
                color: #11111b;
                border: 1px solid #89b4fa;
            }
            QPushButton#btn-confirm:hover {
                background-color: #b4befe;
            }
        """)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)
        
        info_label = QLabel("¿Deseas limpiar archivos temporales o datos de conexiones antes de salir?")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        chk_conn = QCheckBox("Eliminar datos de conexiones (Tokens API)")
        chk_temp = QCheckBox("Eliminar caché y temporales de la carpeta principal")
        
        layout.addWidget(chk_conn)
        layout.addWidget(chk_temp)
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()
        
        btn_cancel = QPushButton("Cancelar")
        btn_confirm = QPushButton("Confirmar y Salir")
        btn_confirm.setObjectName("btn-confirm")
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_confirm)
        layout.addLayout(btn_layout)
        
        btn_cancel.clicked.connect(dialog.reject)
        btn_confirm.clicked.connect(dialog.accept)
        
        if dialog.exec() == QDialog.Accepted:
            # 1. Clean connections if checked
            if chk_conn.isChecked():
                config_path = Path(__file__).resolve().parent / "config.json"
                if config_path.exists():
                    try:
                        with open(config_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        data.pop("github_token", None)
                        data.pop("drive_token", None)
                        with open(config_path, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                    except Exception as e:
                        print(f"Error al limpiar conexiones: {e}")
            
            # 2. Clean temp and cache files if checked
            if chk_temp.isChecked():
                app_dir = Path(__file__).resolve().parent
                temp_dir = app_dir / "_temp_files"
                cache_dir = app_dir / "_api_cache"
                
                for path in [temp_dir, cache_dir]:
                    if path.exists():
                        try:
                            for item in path.iterdir():
                                if item.is_dir():
                                    shutil.rmtree(item)
                                else:
                                    item.unlink()
                        except Exception as e:
                            print(f"Error al vaciar {path.name}: {e}")
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = AppWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
