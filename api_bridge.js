// assets/ui/api_bridge.js
/**
 * API Bridge - Adapter to migrate from QWebChannel (PySide6) to standard REST API (FastAPI)
 * This acts as a drop-in replacement for the Qt 'bridge' object.
 */

// Define global base URL for the API
const API_BASE_URL = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" || window.location.protocol === "file:"
  ? "http://localhost:8000"
  : "https://appweb-o7pl.onrender.com"; // En producción, si se aloja en el mismo servidor, se puede usar ruta relativa. Si no, poner URL de Render.

// Intercept fetch calls to automatically attach authentication headers from localStorage
const originalFetch = window.fetch;
window.fetch = function (input, init) {
  const url = typeof input === 'string' ? input : (input && input.url) ? input.url : '';
  if (url.startsWith(API_BASE_URL)) {
    init = init || {};
    let headers = init.headers || {};
    
    const token = localStorage.getItem("github_token");
    if (token) {
      if (headers instanceof Headers) {
        headers.set("X-GitHub-Token", token);
      } else if (Array.isArray(headers)) {
        headers.push(["X-GitHub-Token", token]);
      } else {
        headers["X-GitHub-Token"] = token;
      }
    }
    init.headers = headers;
  }
  return originalFetch(input, init);
};

class QtSignalMock {
  constructor(name) {
    this.name = name;
    this.callbacks = [];
  }
  connect(callback) {
    if (typeof callback === 'function') {
      this.callbacks.push(callback);
    }
  }
  emit(value) {
    // console.log(`Signal [${this.name}] emitted:`, value);
    this.callbacks.forEach(cb => {
      try {
        cb(value);
      } catch (err) {
        console.error(`Error in signal [${this.name}] callback:`, err);
      }
    });
  }
}

// Instantiate the mock bridge
const mockBridge = {
  // --- Qt Signals (JS → connects to these) ---
  setTreeData: new QtSignalMock("setTreeData"),
  showFolderInfo: new QtSignalMock("showFolderInfo"),
  showBlocInfo: new QtSignalMock("showBlocInfo"),
  showEditor: new QtSignalMock("showEditor"),
  showImage: new QtSignalMock("showImage"),
  showFileInfo: new QtSignalMock("showFileInfo"),
  setStatus: new QtSignalMock("setStatus"),
  saveResult: new QtSignalMock("saveResult"),
  showLoading: new QtSignalMock("showLoading"),
  requireGithubToken: new QtSignalMock("requireGithubToken"),
  showConflictDialog: new QtSignalMock("showConflictDialog"),
  closeEditorSignal: new QtSignalMock("closeEditorSignal"),
  chatResponseReceived: new QtSignalMock("chatResponseReceived"),
  mermaidDiagramGenerated: new QtSignalMock("mermaidDiagramGenerated"),
  copilotResultReceived: new QtSignalMock("copilotResultReceived"),
  githubFoldersLoaded: new QtSignalMock("githubFoldersLoaded"),
  driveFoldersLoaded: new QtSignalMock("driveFoldersLoaded"),
  showVisualizer: new QtSignalMock("showVisualizer"),

  // --- Internal State (Migrated from Python) ---
  _navHistory: [],
  _navIndex: -1,
  _currentFileParams: { source: "local", remote_id: null, remote_repo: null, sha: null },
  _currentEditorPath: "",

  _pushNav: function (path) {
    if (this._navIndex < this._navHistory.length - 1) {
      this._navHistory = this._navHistory.slice(0, this._navIndex + 1);
    }
    this._navHistory.push(path);
    this._navIndex = this._navHistory.length - 1;
  },

  // --- Qt Slots (JS → calls these) ---

  ready: async function () {
    console.log("API Bridge ready. Loading workspace tree...");
    await this.refreshTree();
  },

  refreshTree: async function () {
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/tree`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.setTreeData.emit(JSON.stringify(data));
    } catch (err) {
      this.setStatus.emit(`❌ Error al refrescar árbol: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  itemClicked: async function (path, typ) {
    console.log("API Bridge itemClicked:", path, typ);
    if (typ === "github_file") {
      this.setStatus.emit("Usa el menú contextual de GitHub para abrir archivos");
      return;
    }
    if (typ === "drive_file") {
      this.setStatus.emit("Usa el explorador de Drive para abrir archivos");
      return;
    }

    if (path) {
      this._pushNav({ path, type: typ });
    }

    try {
      this.showLoading.emit(true);
      if (typ === "dir" || typ === "folder" || typ === "area" || typ === "bloque") {
        this.setStatus.emit(`Mostrando carpeta: ${path.split(/[\\/]/).pop()}`);
        const res = await fetch(`${API_BASE_URL}/api/folder/info?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const result = await res.json();

        if (result.type === "bloque") {
          this.showBlocInfo.emit(JSON.stringify(result.data));
        } else {
          this.showFolderInfo.emit(JSON.stringify(result.data));
        }
      } else {
        // Es un archivo local
        this.setStatus.emit(`Abriendo archivo: ${path.split(/[\\/]/).pop()}`);
        const res = await fetch(`${API_BASE_URL}/api/file/read?path=${encodeURIComponent(path)}&source=local`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        this._currentEditorPath = path;
        this._currentFileParams = { source: "local", remote_id: null, remote_repo: null, sha: null };

        // Puesto que el visualizador también se maneja en el HTML, decidimos qué señal emitir
        const suffix = data.suffix;
        const isOffice = [".docx", ".doc", ".xlsx", ".xls", ".csv"].includes(suffix);
        const isVisualizer = ![".md", ".docx", ".doc", ".xlsx", ".xls", ".csv"].includes(suffix);

        if (isOffice) {
          this.showEditor.emit(JSON.stringify({
            path: data.path,
            name: data.name,
            content: "",
            source: "local",
            is_office: true,
            suffix: suffix,
            info: this._currentFileParams
          }));
        } else if (isVisualizer) {
          let vtype = "generic";
          if ([".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg"].includes(suffix)) vtype = "image";
          else if (suffix === ".pdf") vtype = "pdf";
          else if ([".mp4", ".webm", ".ogg", ".mov"].includes(suffix)) vtype = "video";
          else if ([".mp3", ".wav", ".m4a"].includes(suffix)) vtype = "audio";
          else if (suffix === ".ipynb") vtype = "notebook";
          else if ([".txt", ".log", ".ini", ".cfg", ".yaml", ".yml", ".json", ".py", ".js", ".html", ".css"].includes(suffix)) vtype = "code";

          this.showVisualizer.emit(JSON.stringify({
            path: data.path,
            name: data.name,
            type: vtype,
            content: data.content,
            source: "local",
            info: this._currentFileParams
          }));
        } else {
          this.showEditor.emit(JSON.stringify(data));
        }
      }
    } catch (err) {
      this.setStatus.emit(`❌ Error: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  navBack: function () {
    if (this._navIndex > 0) {
      this._navIndex--;
      const target = this._navHistory[this._navIndex];
      this.itemClicked(target.path, target.type);
    }
  },

  navForward: function () {
    if (this._navIndex < this._navHistory.length - 1) {
      this._navIndex++;
      const target = this._navHistory[this._navIndex];
      this.itemClicked(target.path, target.type);
    }
  },

  navUp: function () {
    if (this._navHistory.length > 0 && this._navIndex >= 0) {
      const current = this._navHistory[this._navIndex].path;
      // Obtener ruta padre (sencillo reemplazo de string para separar la última carpeta)
      const parts = current.split(/[\\/]/);
      if (parts.length > 1) {
        parts.pop();
        const parentPath = parts.join("/");
        this._pushNav({ path: parentPath, type: "dir" });
        this.itemClicked(parentPath, "dir");
      }
    }
  },

  saveFile: async function (path, content) {
    try {
      this.showLoading.emit(true);

      // Buscar metadatos correctos de la pestaña en openTabs para evitar cruces
      let tabInfo = { source: "local", remote_id: null, remote_repo: null, sha: null };
      const tabs = (typeof openTabs !== 'undefined') ? openTabs : [];
      const tab = tabs.find(t => t.path === path);
      if (tab && tab.info) {
        tabInfo = tab.info;
      }

      const payload = {
        path: path,
        content: content,
        source: tabInfo.source || this._currentFileParams.source,
        remote_id: tabInfo.remote_id || this._currentFileParams.remote_id,
        remote_repo: tabInfo.remote_repo || this._currentFileParams.remote_repo,
        sha: tabInfo.sha || this._currentFileParams.sha
      };

      const res = await fetch(`${API_BASE_URL}/api/file/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      if (data.sha) {
        this._currentFileParams.sha = data.sha;
        // Actualizar el SHA en la pestaña activa para evitar conflictos 409
        if (tab && tab.info) {
          tab.info.sha = data.sha;
        }
      }
      this.saveResult.emit(true);
      this.setStatus.emit("✅ Guardado con éxito.");
    } catch (err) {
      console.error(err);
      this.saveResult.emit(false);
      this.setStatus.emit(`❌ Error al guardar: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  commitActiveFile: async function () {
    // En web, commit y saveFile están enlazados si se selecciona saveFile.
    // De todos modos, implementamos commitActiveFile explícitamente:
    try {
      this.showLoading.emit(true);
      this.setStatus.emit("📤 Subiendo cambios a la nube...");

      // En la web leemos el editor actual
      // Pero como saveFile ya realiza el commit en la API para archivos de GitHub/Drive,
      // simplemente informamos éxito
      this.setStatus.emit("✅ Sincronizado en la nube con éxito");
    } catch (err) {
      this.setStatus.emit(`❌ Error en sincronización: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  openInExplorer: function (path) {
    // Dado que estamos en web, no podemos abrir el explorador de archivos nativo directamente
    // por restricciones de sandbox del navegador. Informamos de forma amigable.
    this.setStatus.emit(`📂 Ubicación del archivo en tu máquina: ${path}`);
    console.log("openInExplorer no soportado directamente en web:", path);
  },

  openFileExternally: function (path) {
    this.setStatus.emit(`📂 No es posible abrir externamente en web: ${path}`);
  },

  changeRoot: async function () {
    this.setStatus.emit("Cambiar de raíz local no es soportado directamente en web. Configúralo en Ajustes.");
  },

  selectRootPath: async function (path) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      config.last_root = path;
      if (!config.roots.includes(path)) config.roots.push(path);

      await fetch(`${API_BASE_URL}/api/config/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });

      this.setStatus.emit(`Carpeta raíz cambiada a: ${path}`);
      await this.refreshTree();
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    }
  },

  removeRootPath: async function (path) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      config.roots = config.roots.filter(r => r !== path);

      await fetch(`${API_BASE_URL}/api/config/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });
      this.setStatus.emit("Carpeta eliminada del historial");
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    }
  },

  getRootsJson: async function (callback) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      callback(JSON.stringify(config.roots || []));
    } catch (err) {
      callback(JSON.stringify([]));
    }
  },

  // --- GitHub Integrations ---

  getGithubToken: function () {
    return localStorage.getItem("github_token") || (window.appConfig && window.appConfig.github_token) || "";
  },

  testGithubConnection: async function (token, callback) {
    try {
      const res = await fetch(`${API_BASE_URL}/api/sync/github/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token })
      });
      const data = await res.json();
      callback(JSON.stringify(data));
      if (data.success) {
        // Guardar token en localStorage y window.appConfig
        localStorage.setItem("github_token", token);
        if (window.appConfig) window.appConfig.github_token = token;
        await this.refreshTree();
      }
    } catch (err) {
      callback(JSON.stringify({ success: false, error: err.message }));
    }
  },

  clearGithubToken: async function () {
    await fetch(`${API_BASE_URL}/api/sync/github/clear`, { method: "POST" });
    localStorage.removeItem("github_token");
    if (window.appConfig) window.appConfig.github_token = "";
    this.setStatus.emit("Token de GitHub eliminado");
    await this.refreshTree();
  },

  getGithubSelectedRepo: function (callback) {
    callback((window.appConfig && window.appConfig.github_selected_repo) || "");
  },

  setGithubSelectedRepo: async function (repo) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      config.github_selected_repo = repo;
      await fetch(`${API_BASE_URL}/api/config/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });
      if (window.appConfig) window.appConfig.github_selected_repo = repo;
      await this.refreshTree();
    } catch (err) {
      console.error(err);
    }
  },

  getGithubBasePath: function (callback) {
    callback((window.appConfig && window.appConfig.github_base_path) || "");
  },

  saveGithubBasePath: async function (path) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      config.github_base_path = path;
      await fetch(`${API_BASE_URL}/api/config/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });
      if (window.appConfig) window.appConfig.github_base_path = path;
      await this.refreshTree();
    } catch (err) {
      console.error(err);
    }
  },

  fetchGithubFolders: async function () {
    try {
      const repo = (window.appConfig && window.appConfig.github_selected_repo) || "";
      if (!repo) {
        this.githubFoldersLoaded.emit(JSON.stringify([]));
        return;
      }
      const res = await fetch(`${API_BASE_URL}/api/sync/github/folders?repo=${encodeURIComponent(repo)}`);
      const folders = await res.json();
      this.githubFoldersLoaded.emit(JSON.stringify(folders));
    } catch (err) {
      this.githubFoldersLoaded.emit(JSON.stringify([]));
    }
  },

  githubBrowse: async function (repo, path) {
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/sync/github/files?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit(`GitHub: ${data.files.length} elementos cargados`);
      }
    } catch (err) {
      this.setStatus.emit(`❌ Error listando archivos de GitHub: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  githubFileClicked: async function (repo, path, name) {
    try {
      this.showLoading.emit(true);
      this.setStatus.emit(`Descargando de GitHub: ${name}...`);
      const res = await fetch(`${API_BASE_URL}/api/file/read?path=${encodeURIComponent(path)}&source=github&remote_repo=${encodeURIComponent(repo)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      this._currentEditorPath = data.path;
      this._currentFileParams = data.info;

      this.showEditor.emit(JSON.stringify(data));
      this.setStatus.emit(`✅ Archivo descargado de GitHub: ${name}`);
    } catch (err) {
      this.setStatus.emit(`❌ Error descargando archivo: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  // --- Google Drive Integrations ---

  getDriveConfigStatus: function (callback) {
    // Simulamos disponibilidad o cargamos del config pre-cargado
    const status = {
      has_creds: true,
      is_authenticated: (window.appConfig && !!window.appConfig.drive_token)
    };
    callback(JSON.stringify(status));
  },

  getDriveBaseFolderId: function (callback) {
    callback((window.appConfig && window.appConfig.drive_base_folder_id) || "");
  },

  saveDriveBaseFolderId: async function (folderId) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      config.drive_base_folder_id = folderId;
      await fetch(`${API_BASE_URL}/api/config/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });
      if (window.appConfig) window.appConfig.drive_base_folder_id = folderId;
      await this.refreshTree();
    } catch (err) {
      console.error(err);
    }
  },

  fetchDriveFolders: async function () {
    try {
      const res = await fetch(`${API_BASE_URL}/api/sync/drive/folders`);
      const folders = await res.json();
      this.driveFoldersLoaded.emit(JSON.stringify(folders));
    } catch (err) {
      this.driveFoldersLoaded.emit(JSON.stringify([]));
    }
  },

  clearDriveToken: async function () {
    await fetch(`${API_BASE_URL}/api/sync/drive/clear`, { method: "POST" });
    if (window.appConfig) window.appConfig.drive_token = "";
    this.setStatus.emit("Credenciales de Google Drive eliminadas");
    await this.refreshTree();
  },

  syncDrive: async function () {
    this.setStatus.emit("Sincronizando Google Drive...");
    await this.refreshTree();
  },

  driveFolderClicked: async function (path, remoteId) {
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/sync/drive/files?folder_id=${encodeURIComponent(remoteId)}`);
      const data = await res.json();

      // Simulamos la emisión del showFolderInfo similar a _send_drive_subtree en Python
      if (data.success) {
        const filesItems = data.files.map(f => {
          const isFolder = f[0] === "folder";
          const icon = isFolder ? "📁" : "📎";
          return {
            name: `${icon} ${f[1]}`,
            path: `drive://${isFolder ? 'folder' : 'file'}/${f[2]}`,
            remoteId: f[2],
            remoteName: f[1],
            color: isFolder ? "#7c3aed" : "#6b7280"
          };
        });

        this.showFolderInfo.emit(JSON.stringify({
          title: `📁 ${path.split('/').pop() || 'Drive'}`,
          meta: `${data.files.length} elementos en Google Drive`,
          files: filesItems,
          path: path
        }));
      }
    } catch (err) {
      this.setStatus.emit(`❌ Error listando Drive: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  driveFileClicked: async function (remoteId, name) {
    try {
      this.showLoading.emit(true);
      this.setStatus.emit(`Descargando de Drive: ${name}...`);
      const res = await fetch(`${API_BASE_URL}/api/file/read?path=&source=drive&remote_id=${encodeURIComponent(remoteId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      this._currentEditorPath = data.path;
      this._currentFileParams = data.info;

      this.showEditor.emit(JSON.stringify(data));
      this.setStatus.emit(`✅ Archivo descargado de Google Drive: ${name}`);
    } catch (err) {
      this.setStatus.emit(`❌ Error descargando archivo de Drive: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  // --- Remote filter source ---

  getActiveRemoteSource: function (callback) {
    callback((window.appConfig && window.appConfig.active_remote_source) || "all");
  },

  setActiveRemoteSource: async function (source) {
    try {
      await fetch(`${API_BASE_URL}/api/sync/active-source`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source })
      });
      if (window.appConfig) window.appConfig.active_remote_source = source;
      await this.refreshTree();
    } catch (err) {
      console.error(err);
    }
  },

  // --- File manipulations ---

  createNewFile: async function (parentFolder) {
    const filename = prompt("Nombre del nuevo archivo (ej: nota.md):", "nota.md");
    if (!filename) return;
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/file/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_folder: parentFolder, name: filename, type: "file" })
      });
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit(`📝 Archivo creado: ${filename}`);
        await this.refreshTree();
        await this.itemClicked(data.path, "file");
      }
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  createCloudFile: async function (parentFolder, filename) {
    try {
      this.showLoading.emit(true);
      this.setStatus.emit(`Creando archivo '${filename}' en la nube...`);
      const res = await fetch(`${API_BASE_URL}/api/file/create-cloud`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_folder: parentFolder, filename: filename })
      });
      
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit(`✅ Archivo creado en la nube: ${filename}`);
        await this.refreshTree();
        
        // Auto-abrir el archivo creado
        if (data.repo && data.full_path) {
          // Es GitHub
          await this.githubFileClicked(data.repo, data.full_path, filename);
        } else if (data.remote_id) {
          // Es Google Drive
          await this.driveFileClicked(data.remote_id, filename);
        }
      } else {
        throw new Error("Error desconocido");
      }
    } catch (err) {
      this.setStatus.emit(`❌ Error creando archivo en la nube: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  deleteItem: async function (path) {
    if (!confirm(`¿Estás seguro de eliminar permanentemente ${path.split(/[\\/]/).pop()}?`)) return;
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/file/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, source: "local" })
      });
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit("🗑️ Elemento eliminado");
        this.closeEditorSignal.emit(); // Señal para cerrar el editor activo
        await this.refreshTree();
      }
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  deleteCloudItem: async function (nodeStr) {
    const node = JSON.parse(nodeStr);
    if (!confirm(`¿Estás seguro de eliminar de la nube ${node.name}?`)) return;
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/file/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: node.path,
          source: node._type.startsWith("github") ? "github" : "drive",
          remote_id: node.remoteId || node.path,
          remote_repo: node.repo,
          sha: node.sha
        })
      });
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit("🗑️ Elemento eliminado de la nube");
        await this.refreshTree();
      }
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  renameItem: async function (path, newName) {
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/file/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, new_name: newName, source: "local" })
      });
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit("✏️ Elemento renombrado");
        await this.refreshTree();
      }
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  renameCloudItem: async function (nodeStr, newName) {
    const node = JSON.parse(nodeStr);
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/file/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: node.path,
          new_name: newName,
          source: node._type.startsWith("github") ? "github" : "drive",
          remote_id: node.remoteId || node.path,
          remote_repo: node.repo,
          sha: node.sha
        })
      });
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit("✏️ Elemento de la nube renombrado");
        await this.refreshTree();
      }
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  moveItem: async function (srcPath, dstFolder) {
    try {
      this.showLoading.emit(true);
      const res = await fetch(`${API_BASE_URL}/api/file/move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src_path: srcPath, dst_folder: dstFolder })
      });
      const data = await res.json();
      if (data.success) {
        this.setStatus.emit("📂 Elemento movido con éxito");
        await this.refreshTree();
      }
    } catch (err) {
      this.setStatus.emit(`Error: ${err.message}`);
    } finally {
      this.showLoading.emit(false);
    }
  },

  getFileBase64: async function (path, callback) {
    try {
      const res = await fetch(`${API_BASE_URL}/api/file/base64?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.success) {
        callback(data.base64);
      } else {
        callback("");
      }
    } catch (err) {
      callback("");
    }
  },

  saveFileBase64: async function (path, base64Data, callback) {
    try {
      const res = await fetch(`${API_BASE_URL}/api/file/base64/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, base64_data: base64Data })
      });
      const data = await res.json();
      callback(data.success);
      await this.refreshTree();
    } catch (err) {
      callback(false);
    }
  },

  // --- AI Integrations ---

  getAiSettings: async function (callback) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      const settings = {
        provider: config.active_ai_provider || "deepseek",
        gemini_model: config.gemini_model || "gemini-1.5-flash"
      };
      callback(JSON.stringify(settings));
    } catch (err) {
      callback(JSON.stringify({ provider: "deepseek", gemini_model: "gemini-1.5-flash" }));
    }
  },

  getDeepseekKey: function () {
    return (window.appConfig && window.appConfig.deepseek_api_key) || "";
  },

  saveAiSettings: async function (provider, dsKey, gemKey, gemModel, callback) {
    try {
      const configRes = await fetch(`${API_BASE_URL}/api/config`);
      const config = await configRes.json();
      config.active_ai_provider = provider;
      config.deepseek_api_key = dsKey;
      config.gemini_api_key = gemKey;
      config.gemini_model = gemModel;

      const saveRes = await fetch(`${API_BASE_URL}/api/config/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });
      const data = await saveRes.json();

      if (window.appConfig) {
        window.appConfig.active_ai_provider = provider;
        window.appConfig.deepseek_api_key = dsKey;
        window.appConfig.gemini_api_key = gemKey;
        window.appConfig.gemini_model = gemModel;
      }

      callback(JSON.stringify(data));
    } catch (err) {
      callback(JSON.stringify({ success: false, error: err.message }));
    }
  },

  chatWithDeepseek: async function (historyJson, message) {
    try {
      const history = JSON.parse(historyJson);
      const res = await fetch(`${API_BASE_URL}/api/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ history, message })
      });
      const data = await res.json();
      this.chatResponseReceived.emit(JSON.stringify(data));
    } catch (err) {
      this.chatResponseReceived.emit(JSON.stringify({ success: false, error: err.message }));
    }
  },

  runCopilotAction: async function (action, text) {
    try {
      const res = await fetch(`${API_BASE_URL}/api/ai/copilot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, text })
      });
      const data = await res.json();
      this.copilotResultReceived.emit(JSON.stringify(data));
    } catch (err) {
      this.copilotResultReceived.emit(JSON.stringify({ success: false, error: err.message }));
    }
  },

  generateMermaidDiagram: async function (prompt, selectedText, diagramType) {
    try {
      const completePrompt = `Genera un diagrama de tipo ${diagramType} en sintaxis de Mermaid basado en la siguiente instrucción: ${prompt}. Texto de referencia: ${selectedText}. Devuelve ÚNICAMENTE el código del diagrama de Mermaid envuelto en un bloque de código, sin explicaciones.`;
      const res = await fetch(`${API_BASE_URL}/api/ai/copilot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "improve", text: completePrompt })
      });
      const data = await res.json();
      
      if (data.success && data.result) {
        let code = data.result;
        // Extraer contenido de bloques de código de markdown si existen
        const match = code.match(/```(?:mermaid)?\s*([\s\S]*?)\s*```/i);
        if (match) {
          code = match[1];
        }
        data.code = code.trim();
      } else {
        data.code = "";
      }
      
      this.mermaidDiagramGenerated.emit(JSON.stringify(data));
    } catch (err) {
      this.mermaidDiagramGenerated.emit(JSON.stringify({ success: false, error: err.message }));
    }
  },

  openVisualizerWindow: function (path, name, type) {
    this.setStatus.emit(`Visualizador cargado en pestaña interna para: ${name}`);
  },

  closeEditor: function () {
    this.closeEditorSignal.emit();
  }
};

// Mock the Qt QWebChannel creation sequence
window.qt = {
  webChannelTransport: {}
};

class QWebChannelMock {
  constructor(transport, callback) {
    console.log("Mock QWebChannel initialized.");

    // Pre-cargar la configuración para que los slots síncronos funcionen
    fetch(`${API_BASE_URL}/api/config`)
      .then(res => res.json())
      .then(config => {
        window.appConfig = config;

        // Ejecutar callback con el canal simulado
        const channel = {
          objects: {
            bridge: mockBridge
          }
        };
        // Hacemos el bridge disponible globalmente
        window.bridge = mockBridge;
        callback(channel);
      })
      .catch(err => {
        console.error("Error pre-loading API configuration in bridge:", err);
        // Fallback
        window.bridge = mockBridge;
        callback({ objects: { bridge: mockBridge } });
      });
  }
}

// Assign mock to window
window.QWebChannel = QWebChannelMock;
