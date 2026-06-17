// Active UI script loaded by index.html. assets/ui/ui.js is a deprecated mirror.
window.__APPWEB_UI_MARKER__ = true;

window.onerror = function(message, source, lineno, colno, error) {
  var errText = "Error JS: " + message + " (" + (source ? source.split('/').pop() : 'inline') + ":" + lineno + ")";
  console.error(errText);
  var el = document.getElementById("statusbar");
  if (el) el.textContent = errText;
};

function escapeHtml(text) {
  var div = document.createElement("div");
  div.appendChild(document.createTextNode(text));
  return div.innerHTML;
}

// Register DAX language for highlight.js if loaded
try {
  if (typeof hljs !== 'undefined') {
    hljs.registerLanguage('dax', function(hljs) {
      const KEYWORDS = {
        keyword: 'VAR RETURN EVALUATE DEFINE MEASURE ORDER BY START AT ASC DESC TRUE FALSE',
        built_in: 'CALCULATE FILTER ALL VALUES SUM AVERAGE RELATED RELATEDTABLE DIVIDE IF AND OR SWITCH SUMX AVERAGEX MIN MAX MINX MAXX COUNT COUNTA COUNTX COUNTROWS COUNTBLANK DISTINCT KEEPFILTERS USERELATIONSHIP CROSSFILTER TREATAS SELECTCOLUMNS ADDCOLUMNS SUMMARIZE SUMMARIZECOLUMNS CALCULATETABLE'
      };
      return {
        name: 'dax',
        case_insensitive: true,
        keywords: KEYWORDS,
        contains: [
          hljs.C_LINE_COMMENT_MODE,
          hljs.C_BLOCK_COMMENT_MODE,
          {
            className: 'comment',
            begin: '--', end: '$'
          },
          hljs.QUOTE_STRING_MODE,
          {
            className: 'number',
            begin: hljs.C_NUMBER_RE,
            relevance: 0
          },
          {
            className: 'symbol',
            begin: /'/, end: /'/
          },
          {
            className: 'type',
            begin: /\[/, end: /\]/
          }
        ]
      };
    });
  }
} catch(e) { console.warn("dax highlight.js registration error:", e); }

var bridge = null;
var treeData = null;
var selectedPath = "";
var selectedNode = null;
var currentFilePath = "";
var currentFileSource = "local";
var editorInstance = null;
var mermaidObserver = null;
var editorDirty = false;
var saveTimer = null;
var hasUncommittedChanges = false;
var isCommitting = false;
var closeAfterCommit = false;
var activeColIndex = -1;
var isOfficeActive = false;
var currentOfficeType = ""; // "word" or "excel"
var editorViewMode = "quick";

/* ========== Validacion al inicio ========== */
function validateAll() {
  console.log("DEBUG JS: bridge exists?", !!bridge);
  if (bridge) {
    console.log("DEBUG JS: bridge.showEditor type:", typeof bridge.showEditor);
    console.log("DEBUG JS: bridge.setTreeData type:", typeof bridge.setTreeData);
    console.log("DEBUG JS: bridge.ready type:", typeof bridge.ready);
  }
  var issues = [];
  if (typeof bridge === 'undefined' || !bridge) issues.push("Bridge no conectado");
  if (typeof toastui === 'undefined') issues.push("Toast UI Editor no cargado");
  if (issues.length > 0) {
    showStatus("Validacion: " + issues.join(" · "));
  } else {
    showStatus("Listo — todas las funciones disponibles");
  }
}

function showStatus(msg) {
  console.log("STATUSBAR:", msg);
  var el = document.getElementById("statusbar");
  if (el) el.textContent = msg;
}

// Populate the build info badge as soon as the DOM is ready so the
// operator can see which commit is in the wild, even before login.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setBuildInfo);
} else {
  setBuildInfo();
}

/* ========== QWebChannel ========== */
new QWebChannel(qt.webChannelTransport, function(channel) {
  bridge = channel.objects.bridge;

  bridge.setTreeData.connect(function(json) {
    console.log("DEBUG JS: setTreeData signal received! length =", json.length);
    treeData = JSON.parse(json);
    renderTree(treeData);
  });

  bridge.showFolderInfo.connect(function(json) {
    var d = JSON.parse(json);
    showInfo(d.title, d.meta, d.files);
    if (d.path) {
      updateTreeSelection(d.path);
    }
  });

  bridge.showBlocInfo.connect(function(json) {
    var d = JSON.parse(json);
    showBlocInfo(d);
    if (d.path) {
      updateTreeSelection(d.path);
    }
  });

  bridge.showEditor.connect(function(jsonStr) {
    console.log("DEBUG JS: showEditor signal received in JS! json =", jsonStr.substring(0, 100) + "...");
    try {
      var data = JSON.parse(jsonStr);
      window._last_signal_info = data.info || {};
      if (data.is_office) {
        openOfficeEditor(data.path, data.name, data.suffix, data.source);
      } else {
        openEditor(data.path, data.name, data.content, data.source);
      }
    } catch(e) {
      console.error("DEBUG JS: showEditor parse error", e);
    }
  });

  bridge.showImage.connect(function(jsonStr) {
    console.log("DEBUG JS: showImage signal received in JS!");
    try {
      var data = JSON.parse(jsonStr);
      showImagePreview(data.name, data.dataUri);
    } catch(e) {
      console.error("DEBUG JS: showImage parse error", e);
    }
  });

  bridge.showFileInfo.connect(function(json) {
    var d = JSON.parse(json);
    showFileInfo(d);
  });

  bridge.setStatus.connect(function(msg) {
    showStatus(msg);
    // Mostrar errores de GitHub en el diálogo si está abierto
    if (msg.indexOf("Error GitHub") >= 0 || msg.indexOf("inválido") >= 0) {
      var cr = document.getElementById("cloud-result");
      if (cr) { cr.textContent = msg; cr.style.color = "#dc2626"; }
    }
  });

  bridge.saveResult.connect(function(success) {
    onSaveResult(success);
  });

  bridge.showLoading.connect(function(loading) {
    document.getElementById("loading").classList.toggle("active", loading);
    _githubSyncing = loading;
  });

  bridge.requireGithubToken.connect(function() {
    showSettingsDialog();
    switchSettingsTab(null, 'settings-tab-github');
  });

  bridge.showConflictDialog.connect(function(jsonStr) {
    console.log("DEBUG JS: showConflictDialog signal received in JS!");
    try {
      var data = JSON.parse(jsonStr);
      showConflictModal(data.name, data.service);
    } catch(e) {
      console.error("DEBUG JS: showConflictDialog parse error", e);
    }
  });

  bridge.closeEditorSignal.connect(function() {
    performClose();
  });

  bridge.chatResponseReceived.connect(function(resJson) {
    if (typeof window.activeChatCallback === 'function') {
      window.activeChatCallback(resJson);
      window.activeChatCallback = null;
    }
  });

  bridge.mermaidDiagramGenerated.connect(function(resJson) {
    if (typeof window.activeMermaidCallback === 'function') {
      window.activeMermaidCallback(resJson);
      window.activeMermaidCallback = null;
    }
  });

  bridge.copilotResultReceived.connect(function(resJson) {
    if (typeof window.activeCopilotCallback === 'function') {
      window.activeCopilotCallback(resJson);
      window.activeCopilotCallback = null;
    }
  });

  if (bridge.githubFoldersLoaded) {
    bridge.githubFoldersLoaded.connect(function(json) {
      const folders = JSON.parse(json);
      populateGithubFoldersDropdown(folders);
    });
  }

  if (bridge.driveFoldersLoaded) {
    bridge.driveFoldersLoaded.connect(function(json) {
      const folders = JSON.parse(json);
      populateDriveFoldersDropdown(folders);
    });
  }

  if (bridge.showVisualizer) {
    bridge.showVisualizer.connect(function(jsonStr) {
      try {
        var data = JSON.parse(jsonStr);
        window._last_signal_info = data.info || {};
        openVisualizer(data.path, data.name, data.type, data.content, data.source);
      } catch(e) {
        console.error("showVisualizer parse error", e);
      }
    });
  }

  // Get active remote source filter on startup
  if (typeof bridge.getActiveRemoteSource === 'function') {
    bridge.getActiveRemoteSource(function(source) {
      updateSourceFilterButtons(source);
    });
  }

  bridge.ready();
  setTimeout(validateAll, 1000);
  initElementClickWatchers();
});

/* ========== Tree ========== */
function renderTree(data) {
  var tree = document.getElementById("tree");
  tree.innerHTML = "";
  data.forEach(function(node) {
    tree.appendChild(buildTreeNode(node, 0));
  });
}

function buildTreeNode(node, depth) {
  node._depth = depth;
  var div = document.createElement("div");
  div.className = "tree-item";
  div.dataset.path = node.path || "";

  var isFolder = node._type === "folder" || node._type === "area" || node._type === "bloque" ||
                 node._type === "group" || node._type === "drive_folder" || node._type === "drive" ||
                 node._type === "github" || node._type === "github_repo" || node._type === "dir";

  var header = document.createElement("div");
  header.className = "tree-header";
  if (node._type === "area" || node._type === "bloque" || node._type === "group" ||
      node._type === "drive" || node._type === "github") {
    header.classList.add("bold");
  }
  header.style.paddingLeft = (8 + depth * 18) + "px";
  if (node.color) header.style.color = node.color;

  // Toggle (solo para items expandibles)
  var hasToggle = (node._type === "area" || node._type === "bloque" || node._type === "group" ||
                   node._type === "drive" || node._type === "github" || node._type === "folder" ||
                   node._type === "drive_folder" || node._type === "github_repo" || node._type === "dir");
  if (hasToggle) {
    var toggle = document.createElement("span");
    toggle.className = "toggle" + (node._expanded ? " expanded" : "");
    toggle.textContent = "▶";
    node._toggleEl = toggle;
    var nodeRef = node;
    toggle.onclick = function(e) {
      e.stopPropagation();
      if (nodeRef._type === "drive" || nodeRef._type === "github") {
        if (nodeRef._expanded) {
          nodeRef._expanded = false;
          toggle.classList.remove("expanded");
          var c = toggle.parentElement.parentElement.querySelector(".tree-children");
          if (c) c.classList.remove("open");
        } else {
          var t = toggle;
          toggleBranch(t, nodeRef);
        }
        return;
      }
      toggleBranch(toggle, nodeRef);
    };
    header.appendChild(toggle);
  }

  var lbl = document.createElement("span");
  lbl.className = "label";
  lbl.textContent = node.name;
  header.appendChild(lbl);

  if (node.size) {
    var sz = document.createElement("span");
    sz.className = "size";
    sz.textContent = node.size;
    header.appendChild(sz);
  }

  // Click handler
  var nodeRef2 = node;
  header.onclick = function(e) {
    e.stopPropagation();
    onTreeItemClick(nodeRef2);
  };

  // Drag & drop en carpetas
  if (isFolder || node.droppable) {
    header.ondragover = function(e) { e.preventDefault(); header.classList.add("drag-over"); };
    header.ondragleave = function() { header.classList.remove("drag-over"); };
    header.ondrop = function(e) { e.preventDefault(); header.classList.remove("drag-over"); onDrop(e, node); };
  }

  div.appendChild(header);

  // Children container (solo si tiene toggle)
  if (hasToggle) {
    var childrenContainer = document.createElement("div");
    childrenContainer.className = "tree-children" + (node._expanded ? " open" : "");
    (node.children || []).forEach(function(child) {
      childrenContainer.appendChild(buildTreeNode(child, depth + 1));
    });
    div.appendChild(childrenContainer);
  }

  return div;
}

function getNodeKey(node) {
  if (node._type === "group") {
    return "group://" + node.name;
  }
  if (node._type === "drive") {
    return "drive://";
  }
  if (node._type === "drive_folder") {
    return "drive://folder/" + node.remoteId;
  }
  if (node._type === "github") {
    return "github://";
  }
  if (node._type === "github_repo") {
    return "github://repo/" + node.repo;
  }
  if (node._type === "dir") {
    return "github://dir/" + node.repo + "/" + node.path;
  }
  return node.path || "";
}

function toggleBranch(toggleEl, node) {
  node._expanded = !node._expanded;
  toggleEl.classList.toggle("expanded");
  var container = toggleEl.parentElement.parentElement.querySelector(".tree-children");
  if (container) container.classList.toggle("open");
  
  if (node._expanded && (!node.children || node.children.length === 0 || !node._loaded)) {
    if (node._type === "drive" || node._type === "drive_folder" || node._type === "github_repo" || node._type === "dir") {
      lazyLoadTreeChildren(node, container, toggleEl);
    }
  }
  if (bridge) {
    bridge.setFolderExpanded(getNodeKey(node), node._expanded);
  }
}

function lazyLoadTreeChildren(node, containerEl, toggleEl) {
  if (node._loaded) return;
  
  containerEl.innerHTML = "";
  var loadingEl = document.createElement("div");
  loadingEl.style.padding = "4px 8px";
  loadingEl.style.paddingLeft = (8 + (node._depth + 1) * 18) + "px";
  loadingEl.style.color = "var(--text-muted)";
  loadingEl.style.fontStyle = "italic";
  loadingEl.textContent = "⏳ Cargando...";
  containerEl.appendChild(loadingEl);
  
  var url = "";
  if (node._type === "drive" || node._type === "drive_folder") {
    var folderId = node._type === "drive" ? (window.appConfig && window.appConfig.drive_base_folder_id || "root") : node.remoteId;
    url = API_BASE_URL + "/api/sync/drive/files?folder_id=" + encodeURIComponent(folderId);
  } else if (node._type === "github_repo" || node._type === "dir") {
    var repo = node.repo;
    var path = node._type === "github_repo" ? (node.path || "") : node.path;
    url = API_BASE_URL + "/api/sync/github/files?repo=" + encodeURIComponent(repo) + "&path=" + encodeURIComponent(path);
  } else {
    return;
  }
  
  fetch(url)
    .then(r => r.json())
    .then(data => {
      containerEl.innerHTML = "";
      node.children = [];
      node._loaded = true;
      
      if (data.success && data.files && data.files.length > 0) {
        data.files.forEach(function(f) {
          var childNode = {};
          if (node._type === "drive" || node._type === "drive_folder") {
            var isFolder = f[0] === "folder";
            var icon = isFolder ? "📁" : "📎";
            var ext = f[4] || "";
            var color = "#6b7280";
            if (ext === ".md") color = "#2563eb";
            else if (ext === ".pdf") color = "#dc2626";
            else if (ext === ".xlsx" || ext === ".csv") color = "#16a34a";
            else if (ext === ".png" || ext === ".jpg" || ext === ".jpeg") color = "#9333ea";
            
            childNode = {
              name: isFolder ? "📁 " + f[1] : icon + " " + f[1],
              _type: isFolder ? "drive_folder" : "drive_file",
              path: isFolder ? "drive://folder/" + f[2] : "drive://file/" + f[2],
              remoteId: f[2],
              remoteName: f[1],
              color: color,
              children: isFolder ? [] : undefined
            };
          } else {
            var isFolder = f[0] === "dir";
            var name = f[1];
            var fp = f[2];
            var sha = f[3] || "";
            var color = isFolder ? "#7c3aed" : "#6b7280";
            var ext = name.split('.').pop().toLowerCase();
            if (!isFolder) {
              if (ext === "md") color = "#2563eb";
              else if (ext === "pdf") color = "#dc2626";
              else if (ext === "xlsx" || ext === "csv") color = "#16a34a";
              else if (["png", "jpg", "jpeg"].includes(ext)) color = "#9333ea";
            }
            
            childNode = {
              name: isFolder ? "📁 " + name : "📄 " + name,
              _type: isFolder ? "dir" : "github_file",
              repo: node.repo,
              path: fp,
              sha: sha,
              remoteName: name,
              color: color,
              children: isFolder ? [] : undefined
            };
          }
          
          node.children.push(childNode);
          containerEl.appendChild(buildTreeNode(childNode, node._depth + 1));
        });
      } else {
        var emptyEl = document.createElement("div");
        emptyEl.style.padding = "4px 8px";
        emptyEl.style.paddingLeft = (8 + (node._depth + 1) * 18) + "px";
        emptyEl.style.color = "var(--text-muted)";
        emptyEl.textContent = "(Vacío)";
        containerEl.appendChild(emptyEl);
      }
    })
    .catch(err => {
      containerEl.innerHTML = "";
      var errEl = document.createElement("div");
      errEl.style.padding = "4px 8px";
      errEl.style.paddingLeft = (8 + (node._depth + 1) * 18) + "px";
      errEl.style.color = "#ef4444";
      errEl.textContent = "⚠️ Error: " + err.message;
      containerEl.appendChild(errEl);
    });
}

function updateTreeSelection(path) {
  selectedPath = path;
  document.querySelectorAll(".tree-item").forEach(function(el) {
    el.classList.toggle("selected", el.dataset.path === path);
  });
}

function getSelectedPath() { return selectedPath; }

function getExplorerPath() {
  if (!selectedNode) return "";
  if (selectedNode._type === "github_file" || selectedNode._type === "dir" || selectedNode._type === "github_repo") {
    var repo = selectedNode.repo || "";
    var path = selectedNode.path || "";
    return "github://localpath/" + repo + "/" + path;
  }
  if (selectedNode._type === "drive_file" || selectedNode._type === "drive_folder") {
    var fid = selectedNode.remoteId || "";
    var name = selectedNode.remoteName || selectedNode.name.replace(/^[^\s]+\s/, '');
    return "drive://localpath/" + fid + "/" + name;
  }
  return selectedPath;
}

/* ========== Tree Click ========== */
function onTreeItemClick(node) {
  console.log("DEBUG JS: onTreeItemClick called", node);
  showStatus("Clic en: " + node.name + " (tipo: " + node._type + ")");
  selectedNode = node;
  selectedPath = node.path || "";
  updateTreeSelection(selectedPath);

  var isFolderLike = (node._type === "folder" || node._type === "area" || node._type === "bloque" ||
                      node._type === "group" || node._type === "drive" || node._type === "github" ||
                      node._type === "drive_folder" || node._type === "github_repo" || node._type === "dir");

  if (node._type === "folder") {
    console.log("DEBUG JS: invoking bridge.itemClicked for folder path =", node.path);
    bridge.itemClicked(node.path, node._type);
  } else if (node._type === "drive_folder") {
    console.log("DEBUG JS: invoking bridge.driveFolderClicked path =", node.path, "id =", node.remoteId);
    bridge.driveFolderClicked(node.path, node.remoteId);
  } else if (node._type === "github_repo" || node._type === "dir") {
    console.log("DEBUG JS: invoking bridge.githubBrowse repo =", node.repo, "path =", node.path);
    bridge.githubBrowse(node.repo, node.path);
  } else if (node._type === "drive" && (!node.children || node.children.length === 0)) {
    bridge.syncDrive();
  } else if (node._type === "github" && (!node.children || node.children.length === 0)) {
    bridge.syncGithub("");
  }

  if (isFolderLike) {
    toggleNodeExpand(node);
    return;
  }

  if (node._type === "drive_file") {
    const tab = openTabs.find(t => t.source === "drive" && t.info && t.info.remote_id === node.remoteId);
    if (tab) {
      setActiveTab(tab.path);
      return;
    }
    console.log("DEBUG JS: invoking bridge.driveFileClicked id =", node.remoteId, "name =", node.remoteName);
    bridge.driveFileClicked(node.remoteId, node.remoteName);
    return;
  }
  if (node._type === "github_file") {
    var remoteName = node.remoteName || node.name.replace(/^[^\s]+\s/, '');
    const tab = openTabs.find(t => t.source === "github" && t.info && t.info.remote_repo === node.repo && t.info.remote_id === node.path);
    if (tab) {
      setActiveTab(tab.path);
      return;
    }
    console.log("DEBUG JS: invoking bridge.githubFileClicked repo =", node.repo, "path =", node.path, "name =", remoteName);
    bridge.githubFileClicked(node.repo, node.path, remoteName);
    return;
  }
  if (node._type === "file") {
    const tab = openTabs.find(t => t.path === node.path);
    if (tab) {
      setActiveTab(tab.path);
      return;
    }
    console.log("DEBUG JS: invoking bridge.itemClicked for file path =", node.path);
    bridge.itemClicked(node.path, node._type);
    return;
  }
  // Areas, grupos, cloud roots: toggle
  console.log("DEBUG JS: element clicked requires toggle. type =", node._type);
  toggleNodeExpand(node);
}

function toggleNodeExpand(node) {
  if (node._toggleEl) {
    node._toggleEl.click();
  }
}

/* ========== Info View ========== */
function showInfo(title, meta, files) {
  switchView("info-view");
  setEl("info-title", title);
  setEl("info-meta", meta || "");
  var container = document.getElementById("info-files");
  container.innerHTML = "";
  if (!files || files.length === 0) {
    container.innerHTML = '<p style="color:var(--text-muted);font-style:italic">(vacío)</p>';
    return;
  }
  files.forEach(function(f) {
    var row = document.createElement("div");
    row.className = "file-row";
    var nameSpan = document.createElement("span");
    nameSpan.className = "name";
    nameSpan.textContent = f.name;
    nameSpan.style.color = f.color || "inherit";
    row.appendChild(nameSpan);
    if (f.size) { var sz = document.createElement("span"); sz.className = "size"; sz.textContent = f.size; row.appendChild(sz); }
    if (f.date) { var dt = document.createElement("span"); dt.className = "date"; dt.textContent = f.date; row.appendChild(dt); }
    if (f.path) { row.style.cursor = "pointer"; row.onclick = function() { bridge.itemClicked(f.path, "file"); }; }
    container.appendChild(row);
  });
}

function showBlocInfo(d) {
  switchView("info-view");
  setEl("info-title", d.title);
  setEl("info-meta", d.meta);
  var container = document.getElementById("info-files");
  container.innerHTML = "";
  if (d.pages && d.pages.length > 0) {
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    ["Página", "Tipo", "Descripción"].forEach(function(h) {
      var th = document.createElement("th"); th.textContent = h; hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    d.pages.forEach(function(p) {
      var tr = document.createElement("tr");
      [p.page, p.type, p.desc].forEach(function(v, i) {
        var td = document.createElement("td");
        td.textContent = v;
        if (i === 1 && p.typeColor) td.style.color = p.typeColor;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
  }
  if (d.sources && d.sources.length > 0) {
    var sep = document.createElement("div"); sep.className = "sep"; container.appendChild(sep);
    d.sources.forEach(function(s) {
      var row = document.createElement("div"); row.className = "file-row";
      var sp = document.createElement("span"); sp.className = "name"; sp.textContent = "📄 " + s; sp.style.color = "#555";
      row.appendChild(sp); container.appendChild(row);
    });
  }
}

function showFileInfo(d) {
  switchView("info-view");
  setEl("info-title", "📃 " + d.name);
  setEl("info-meta", "Ubicación: " + d.parent + "\nTamaño: " + d.size + "\nModificado: " + d.date);
  var c = document.getElementById("info-files");
  c.innerHTML = '<p style="color:var(--text-muted);font-style:italic;margin-bottom:12px;">(vista previa no disponible)</p>' +
                '<button onclick="bridge.openInExplorer(selectedPath)" onmouseover="this.style.background=\'var(--hover)\'" onmouseout="this.style.background=\'var(--bg)\'" style="border:1px solid var(--border); border-radius:var(--radius); padding:6px 16px; cursor:pointer; font-size:12px; background:var(--bg); color:var(--text); display:inline-flex; align-items:center; gap:6px; transition:background 0.15s;">📁 Abrir ubicación en explorador</button>';
}

function setEl(id, text) {
  var el = document.getElementById(id);
  if (el) el.textContent = text;
}

/* ========== Editor View ========== */
function openEditorDirect(path, name, content, source) {
  console.log("DEBUG JS: openEditor called path =", path, "name =", name, "source =", source);
  window.collapsedCodeBlocks = new Set();
  showStatus("Cargando editor para: " + name);
  
  // Ensure mode button is visible for markdown
  ensureEditorModeControls();
  var modeBtn = document.getElementById("btn-editor-mode");
  if (modeBtn) modeBtn.style.display = "inline-block";
  var modeGroup = document.getElementById("editor-mode-group");
  if (modeGroup) modeGroup.style.display = "inline-flex";

  // Hide other editor types
  document.getElementById("markdown-editor-container").style.display = "block";
  document.getElementById("docx-editor-container").style.display = "none";
  document.getElementById("xlsx-editor-container").style.display = "none";
  showDocxEditor(false);

  if (typeof toastui === 'undefined' || typeof toastui.Editor === 'undefined') {
    showStatus("ERROR: Toast UI Editor no cargado - " + name);
    console.error("toastui not loaded for", name);
    // Fallback: show raw content in textarea
    switchView("editor-view");
    currentFilePath = path;
    setEl("editor-name", "📝 " + name + " (sin editor)");
    var container = document.getElementById("markdown-editor-container");
    container.innerHTML = '<textarea style="width:100%;height:100%;border:none;padding:16px;font-family:monospace;font-size:14px;resize:none">' + escapeHtml(content) + '</textarea>';
    return;
  }
  switchView("editor-view");
  currentFilePath = path;
  currentFileSource = source || "local";
  updateTreeSelection(path);
  hasUncommittedChanges = false;
  isCommitting = false;
  closeAfterCommit = false;
  isOfficeActive = false;
  currentOfficeType = "";

  setEl("editor-name", "📝 " + name);
  var sourceTag = document.getElementById("editor-source");
  if (source && source !== "local") {
    sourceTag.style.display = "inline";
    sourceTag.textContent = source === "drive" ? "☁️ Drive" : "🐙 GitHub";
  } else {
    sourceTag.style.display = "none";
  }
  setEl("editor-meta", "");

  // Actualizar visibilidad de botones del editor
  var commitBtn = document.getElementById("btn-commit-file");
  if (commitBtn) {
    if (source && source !== "local") {
      commitBtn.style.display = "inline";
    } else {
      commitBtn.style.display = "none";
    }
  }
  if (modeBtn) {
    modeBtn.textContent = "👁️ Vista de Lectura";
  }

  var container = document.getElementById("markdown-editor-container");
  container.innerHTML = "";
  editorViewMode = "quick";
  updateEditorModeButtons();

  if (editorInstance) {
    try { editorInstance.destroy(); } catch(e) {}
    editorInstance = null;
  }

  try { if (typeof mermaid !== 'undefined') mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' }); } catch(e) {}

  var mermaidBtn = document.createElement("button");
  mermaidBtn.textContent = "📊";
  mermaidBtn.title = "Insertar/Editar Diagrama Mermaid";
  mermaidBtn.className = "toastui-editor-toolbar-icons mermaid";
  mermaidBtn.style.fontSize = "16px";
  mermaidBtn.onclick = function() {
    openMermaidModal();
  };

  var aiCopilotBtn = document.createElement("button");
  aiCopilotBtn.textContent = "✨";
  aiCopilotBtn.title = "Copilot de IA (Mejorar, resumir, tablas...)";
  aiCopilotBtn.className = "toastui-editor-toolbar-icons ai-copilot";
  aiCopilotBtn.style.fontSize = "16px";
  aiCopilotBtn.onclick = function() {
    openAiCopilotModal();
  };

  var hljsInstance = null;
  try { if (typeof hljs !== 'undefined') hljsInstance = hljs; } catch(e) {}

  editorInstance = new toastui.Editor({
    el: container,
    height: "100%",
    initialValue: content || "",
    initialEditType: "markdown",
    previewStyle: "tab",
    hideModeSwitch: true,
    usageStatistics: false,
    codeBlockLanguages: [
      "python", "java", "javascript", "typescript", "dax", "sql",
      "html", "css", "bash", "json", "xml", "yaml", "markdown",
      "c", "cpp", "csharp", "ruby", "php", "go", "rust", "swift",
      "r", "matlab", "powershell", "dockerfile", "makefile", "kotlin", "scala"
    ],
    highlightjs: hljsInstance,
    toolbarItems: [
      ["heading", "bold", "italic", "strike"],
      ["hr", "quote"],
      ["ul", "ol", "task"],
      ["table", "image", "link"],
      ["code", "codeblock"],
      ["scrollSync"],
      [{ name: "mermaid", el: mermaidBtn }, { name: "ai-copilot", el: aiCopilotBtn }]
    ],
    hooks: {
      addImageBlobHook: uploadImageBlob
    },
    customHTMLRenderer: {
      htmlBlock: {
        iframe(node) {
          return [{ type: "openTag", tagName: "iframe", attributes: node.attrs }];
        }
      },
      image(node, context) {
        if (context && typeof context.skipChildren === 'function') {
          context.skipChildren();
        }
        if (context && !context.entering) {
          return { type: 'html', content: '' };
        }
        var src = node.destination || '';
        var alt = node.firstChild ? node.firstChild.literal : '';
        if (src && !src.startsWith("http://") && !src.startsWith("https://") && !src.startsWith("data:")) {
          var sessionToken = sessionStorage.getItem("app_session_token") || localStorage.getItem("app_session_token");
          var tokenQuery = sessionToken ? "&token=" + encodeURIComponent(sessionToken) : "";
          if (currentFileSource === "local") {
            var separator = currentFilePath.includes('\\') ? '\\' : '/';
            var lastSep = currentFilePath.lastIndexOf(separator);
            var noteDir = lastSep >= 0 ? currentFilePath.substring(0, lastSep) : "";
            var cleanSrc = src.replace(/^\.?\/+/, "");
            var fullImgPath = noteDir + separator + cleanSrc;
            src = API_BASE_URL + "/api/file/raw?path=" + encodeURIComponent(fullImgPath) + "&source=local" + tokenQuery;
          } else if (currentFileSource === "github") {
            const tab = openTabs.find(t => t.path === currentFilePath);
            var remoteRepo = (tab && tab.info && tab.info.remote_repo) ? tab.info.remote_repo : "";
            var remoteNotePath = (tab && tab.info && tab.info.remote_id) ? tab.info.remote_id : "";
            var lastSlash = remoteNotePath.lastIndexOf('/');
            var remoteImgFolder = lastSlash >= 0 ? remoteNotePath.substring(0, lastSlash) : "";
            var cleanSrc = src.replace(/^\.?\/+/, "");
            var relativeImgPath = remoteImgFolder ? (remoteImgFolder + "/" + cleanSrc) : cleanSrc;
            src = API_BASE_URL + "/api/file/raw?path=" + encodeURIComponent(relativeImgPath) + "&source=github&remote_repo=" + encodeURIComponent(remoteRepo) + tokenQuery;
          }
        }
        return {
          type: 'openTag',
          tagName: 'img',
          selfClose: true,
          attributes: {
            'src': src,
            'alt': alt,
            'style': 'max-width:100%; cursor:pointer;',
            'onclick': 'if(window.openImageInVisualizer) window.openImageInVisualizer("' + src.replace(/\\/g, '\\\\') + '");'
          }
        };
      },
      codeBlock(node) {
        var info = node.info || '';
        if (info.trim().toLowerCase() === 'mermaid') {
          return [
            { type: 'openTag', tagName: 'div', attributes: { 'class': 'mermaid-block', 'style': 'display:block; margin: 10px 0;' } },
            { type: 'text', content: node.literal },
            { type: 'closeTag', tagName: 'div' }
          ];
        }
        // Fallback code block tokens to prevent slice errors
        return [
          { type: 'openTag', tagName: 'pre' },
          { type: 'openTag', tagName: 'code', attributes: info ? { 'data-language': info } : {} },
          { type: 'text', content: node.literal },
          { type: 'closeTag', tagName: 'code' },
          { type: 'closeTag', tagName: 'pre' }
        ];
      }
    }
  });

  // Intercept paste event for Excel compatibility
  container.addEventListener('paste', handleExcelPaste, true);

  // Sync editor mode with top toolbar button
  editorInstance.on('changeMode', function() {
    var currentMode = getEditorMode();
    var modeBtn = document.getElementById("btn-editor-mode");
    var container = document.getElementById('editor-container');
    
    if (container && container.classList.contains('full-preview-mode')) {
      container.classList.remove('full-preview-mode');
    }
    
    if (modeBtn) {
      modeBtn.textContent = "👁️ Vista de Lectura";
    }
    
    if (currentMode === 'wysiwyg') {
      showStatus("Editor Visual (WYSIWYG)");
    } else {
      showStatus("Editor Markdown");
    }
    
    setupMermaidObserver();
    setTimeout(renderMermaidPreview, 300);
    setTimeout(applyCustomStyles, 300);
    setTimeout(styleCodeBlocks, 300);
    setTimeout(renderWysiwygMermaidOverlays, 300);
  });

  // Syntax highlighting en preview via hook (no toca ProseMirror)
  try {
    editorInstance.addHook('beforePreviewRender', function(html) {
      if (typeof hljs === 'undefined') return html;
      var temp = document.createElement('div');
      temp.innerHTML = html;
      temp.querySelectorAll('pre code').forEach(function(el) {
        var lang = (el.className.match(/language-(\w+)/) || [])[1];
        var result;
        if (lang && hljs.getLanguage(lang)) {
          result = hljs.highlight(el.textContent, { language: lang });
        } else {
          result = hljs.highlightAuto(el.textContent);
        }
        if (result && result.value) {
          el.innerHTML = result.value;
          el.className = 'hljs ' + (result.language ? 'language-' + result.language : '');
        }
      });
      return temp.innerHTML;
    });
  } catch(e) { console.warn("hljs hook error:", e); }

  const wwContainer = container.querySelector('.toastui-editor-ww-container');
  if (wwContainer) {
    wwContainer.addEventListener('scroll', renderWysiwygMermaidOverlays);
  }

  editorDirty = false;
  editorInstance.on("change", function() {
    editorDirty = true;
    hasUncommittedChanges = true;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(autoSave, 3000);

    // Mark current tab as uncommitted
    var tab = openTabs.find(t => t.path === currentFilePath);
    if (tab && !tab.uncommitted) {
      tab.uncommitted = true;
      renderTabsBar();
    }

    // Record history snapshot and update word count
    recordHistorySnapshotDebounced();
    updateWordCount();

    // Only run preview updates if we are in split/markdown mode (where the static preview pane is visible)
    if (getEditorMode() === 'markdown') {
      if (window.changeTimeout) clearTimeout(window.changeTimeout);
      window.changeTimeout = setTimeout(() => {
        applyCustomStyles();
        styleCodeBlocks();
      }, 300);
    }
  });
  
  // Setup MutationObserver to render Mermaid blocks reactively when they appear in preview DOM
  setupMermaidObserver();
  
  setTimeout(applyCustomStyles, 500);
  setTimeout(styleCodeBlocks, 500);
  setTimeout(renderWysiwygMermaidOverlays, 500);

  // Initialize history and word count with initial file content
  initHistory(content);
  updateWordCount();
  setEditorViewMode("quick");
}

function setupMermaidObserver() {
  if (mermaidObserver) {
    try { mermaidObserver.disconnect(); } catch(e) {}
    mermaidObserver = null;
  }
  
  const mode = getEditorMode();
  if (mode !== 'markdown') {
    // In WYSIWYG mode, we do not need the MutationObserver for preview blocks
    return;
  }
  
  var target = document.querySelector('.toastui-editor-contents') || document.querySelector('.toastui-editor-md-preview');
  if (!target) {
    // If not found yet, try again after a small delay
    setTimeout(setupMermaidObserver, 200);
    return;
  }
  
  mermaidObserver = new MutationObserver(function(mutations) {
    if (window.mermaidRenderTimeout) clearTimeout(window.mermaidRenderTimeout);
    window.mermaidRenderTimeout = setTimeout(renderMermaidPreview, 100);
  });
  
  mermaidObserver.observe(target, {
    childList: true,
    subtree: true
  });
  
  // Initial run
  setTimeout(renderMermaidPreview, 100);
}

function renderMermaidPreview() {
  try {
    if (typeof mermaid === 'undefined') return;
    var preview = document.querySelector('.toastui-editor-contents') || document.querySelector('.toastui-editor-md-preview') || document.querySelector('#editor-container');
    if (!preview) return;
    
    var blocks = preview.querySelectorAll('.mermaid-block');
    blocks.forEach(function(el) {
      if (el.classList.contains('mermaid-rendered')) return;
      var text = el.textContent.trim();
      var id = 'mmd-' + Math.random().toString(36).slice(2, 9);
      el.classList.add('mermaid-rendered');
      el.style.display = 'block';
      
      // Clear contents and show loading spinner/text
      el.innerHTML = '<div style="font-size: 12px; color: var(--text-muted); padding: 8px;">⏳ Renderizando diagrama...</div>';
      
      try {
        mermaid.render(id, text, el).then(function(result) {
          el.innerHTML = result.svg;
          el.classList.add('mermaid-svg');
          if (result.bindFunctions) {
            try {
              result.bindFunctions(el);
            } catch(e) {
              console.warn("bindFunctions error:", e);
            }
          }
        }).catch(function(err) {
          console.error("mermaid promise error:", err);
          el.innerHTML = '<span style="color:#dc2626; font-size:12px; font-family: sans-serif; white-space: normal;">⚠️ Error al renderizar diagrama Mermaid: ' + escapeHtml(err.message || err) + '</span><pre style="margin-top: 8px; font-family: monospace; font-size: 12px; background: rgba(0,0,0,0.05); padding: 8px; border-radius: 4px; overflow: auto; max-height: 150px; text-align: left; white-space: pre;">' + escapeHtml(text) + '</pre>';
        });
      } catch (err) {
        console.error("mermaid render exception:", err);
        el.innerHTML = '<span style="color:#dc2626; font-size:12px; font-family: sans-serif; white-space: normal;">⚠️ Error al renderizar diagrama Mermaid: ' + escapeHtml(err.message || err) + '</span><pre style="margin-top: 8px; font-family: monospace; font-size: 12px; background: rgba(0,0,0,0.05); padding: 8px; border-radius: 4px; overflow: auto; max-height: 150px; text-align: left; white-space: pre;">' + escapeHtml(text) + '</pre>';
      }
    });
  } catch(e) { console.warn("mermaid render outer error:", e); }
}

function autoSave() {
  if (isOfficeActive) return;
  if (!editorDirty || !currentFilePath || !editorInstance) return;
  var content = editorInstance.getMarkdown();
  bridge.saveFile(currentFilePath, content);
}

function saveCurrentFile() {
  if (isOfficeActive) {
    if (!currentFilePath) return;
    showStatus("Guardando archivo de oficina...");
    if (currentOfficeType === "excel") {
      try {
        var base64Data = XLSX.write(currentWorkbook, {type: 'base64', bookType: 'xlsx'});
        bridge.saveFileBase64(currentFilePath, base64Data, function(ok) {
          if (ok) {
            showStatus("Hoja de cálculo guardada");
            hasUncommittedChanges = false;
          } else {
            showStatus("Error al guardar Excel");
          }
        });
      } catch(e) {
        showStatus("Error al exportar Excel: " + e.message);
      }
    } else if (currentOfficeType === "word") {
      try {
        var htmlContent = getDocxExportHtml();
        if (typeof htmlDocx !== 'undefined') {
          var docContent = htmlDocx.asBlob(htmlContent);
          var reader = new FileReader();
          reader.onloadend = function() {
            var base64Data = reader.result.split(',')[1];
            bridge.saveFileBase64(currentFilePath, base64Data, function(ok) {
              if (ok) {
                showStatus("Documento de Word guardado");
                hasUncommittedChanges = false;
              } else {
                showStatus("Error al guardar Word");
              }
            });
          };
          reader.readAsDataURL(docContent);
        } else {
          showStatus("Error: Librería html-docx-js no disponible");
        }
      } catch(e) {
        showStatus("Error al exportar Word: " + e.message);
      }
    }
    return;
  }

  if (!editorInstance || !currentFilePath) {
    showStatus("No hay archivo abierto en el editor");
    return;
  }
  var content = editorInstance.getMarkdown();
  bridge.saveFile(currentFilePath, content);
}

function performCloseDirect() {
  if (editorInstance) {
    try { editorInstance.destroy(); } catch(e) {}
    editorInstance = null;
  }
  
  // Clear WYSIWYG overlays
  const overlaysContainer = document.getElementById('wysiwyg-mermaid-overlays');
  if (overlaysContainer) {
    overlaysContainer.innerHTML = '';
    overlaysContainer.style.display = 'none';
  }

  isOfficeActive = false;
  currentOfficeType = "";
  document.getElementById("docx-editor-container").innerHTML = "";
  document.getElementById("xlsx-grid-area").innerHTML = "";
  document.getElementById("xlsx-tabs-bar").innerHTML = "";

  currentFilePath = "";
  currentFileSource = "local";
  editorDirty = false;
  hasUncommittedChanges = false;
  isCommitting = false;
  closeAfterCommit = false;
  switchView("info-view");
  bridge.closeEditor();
}

function performClose() {
  performCloseDirect();
  if (activeTabPath) {
    const idx = openTabs.findIndex(t => t.path === activeTabPath);
    if (idx !== -1) {
      openTabs.splice(idx, 1);
    }
    if (openTabs.length === 0) {
      activeTabPath = "";
      currentFilePath = "";
      document.getElementById("tabs-bar").style.display = "none";
    } else {
      const nextActiveIdx = Math.min(idx, openTabs.length - 1);
      setActiveTab(openTabs[nextActiveIdx].path);
    }
  }
}

function closeEditorDirect() {
  if (!editorInstance && !isOfficeActive) return;

  if (hasUncommittedChanges && currentFileSource !== "local") {
    var choice = confirm("¿Deseas SUBIR tus cambios a " + (currentFileSource === "github" ? "GitHub" : "Drive") + " antes de cerrar?\n\n- [Aceptar]: Subir y Cerrar\n- [Cancelar]: Cerrar SIN Subir");
    if (choice) {
      closeAfterCommit = true;
      isCommitting = true;
      showStatus("Subiendo cambios antes de cerrar...");
      saveCurrentFile();
      setTimeout(function() {
        bridge.commitActiveFile();
      }, 500);
      return;
    }
  }

  performClose();
}

function closeEditor() {
  closeTab(currentFilePath);
}

function openCurrentFileLocation() {
  if (currentFilePath && bridge) {
    bridge.openInExplorer(currentFilePath);
  } else {
    showStatus("No hay archivo abierto en el editor");
  }
}

function commitCurrentFile() {
  if (bridge && currentFileSource !== "local") {
    saveCurrentFile();
    setTimeout(function() {
      closeAfterCommit = false;
      isCommitting = true;
      showStatus("Subiendo cambios a la nube...");
      bridge.commitActiveFile();
    }, 500);
  }
}

function ensureEditorModeControls() {
  var modeBtn = document.getElementById("btn-editor-mode");
  if (!modeBtn || document.getElementById("btn-mode-quick")) return;

  var group = document.createElement("div");
  group.id = "editor-mode-group";
  group.className = "editor-mode-group";

  var quickBtn = document.createElement("button");
  quickBtn.id = "btn-mode-quick";
  quickBtn.textContent = "Vista rapida";
  quickBtn.title = "Abrir editor y vista previa lado a lado";
  quickBtn.onclick = function() { setEditorViewMode("quick"); };

  modeBtn.textContent = "Lectura";
  modeBtn.title = "Lectura completa";
  modeBtn.onclick = function() { setEditorViewMode("read"); };

  modeBtn.parentNode.insertBefore(group, modeBtn);
  group.appendChild(quickBtn);
  group.appendChild(modeBtn);
  updateEditorModeButtons();
}

function updateEditorModeButtons() {
  ["quick", "read"].forEach(function(mode) {
    var id = mode === "quick" ? "btn-mode-quick" : "btn-editor-mode";
    var btn = document.getElementById(id);
    if (btn) btn.classList.toggle("active", editorViewMode === mode);
  });
}

function setEditorViewMode(mode) {
  if (!editorInstance || isOfficeActive) {
    editorViewMode = "quick";
    updateEditorModeButtons();
    if (isOfficeActive && currentOfficeType === "word") {
      showStatus("Documento Word en modo edicion");
    }
    return;
  }

  var container = document.getElementById("editor-container");
  if (!container) return;

  editorViewMode = mode;
  container.classList.remove("full-preview-mode");
  container.classList.remove("quick-preview-mode");

  if (mode === "read") {
    if (getEditorMode() === "wysiwyg") {
      window.lastEditorMode = "wysiwyg";
      editorInstance.changeMode("markdown");
    }
    container.classList.add("full-preview-mode");
    showStatus("Vista de Lectura Completa (Mermaid Activo)");
    setTimeout(function() {
      setupMermaidObserver();
      renderMermaidPreview();
      applyCustomStyles();
      styleCodeBlocks();
    }, 100);
  } else {
    editorViewMode = "quick";
    if (getEditorMode() === "wysiwyg") {
      window.lastEditorMode = "wysiwyg";
      editorInstance.changeMode("markdown");
    }
    container.classList.add("quick-preview-mode");
    showStatus("Vista rapida: editor y preview");
    setTimeout(function() {
      setupMermaidObserver();
      renderMermaidPreview();
      applyCustomStyles();
      styleCodeBlocks();
    }, 100);
  }

  updateEditorModeButtons();
}

function toggleEditorMode() {
  setEditorViewMode(editorViewMode === "read" ? "quick" : "read");
}

function onSaveResult(success) {
  if (isCommitting) {
    isCommitting = false;
    if (success) {
      hasUncommittedChanges = false;
      const tab = openTabs.find(t => t.path === currentFilePath);
      if (tab) tab.uncommitted = false;
      renderTabsBar();
      setEl("editor-meta", "✅ Subido a la nube");
      showStatus("Subido a la nube con éxito");
      if (closeAfterCommit) {
        performClose();
      }
    } else {
      setEl("editor-meta", "❌ Error de subida");
      showStatus("Error al subir a la nube");
    }
    return;
  }

  if (success) {
    editorDirty = false;
    hasUncommittedChanges = false;
    const tab = openTabs.find(t => t.path === currentFilePath);
    if (tab) tab.uncommitted = false;
    renderTabsBar();
    setEl("editor-meta", "✅ Guardado local");
    showStatus("Guardado local");
  } else {
    setEl("editor-meta", "❌ Error");
    showStatus("Error al guardar");
  }
}

/* ========== Image View ========== */
function showImagePreview(name, dataUri) {
  switchView("image-view");
  setEl("img-title", "🖼️ " + name);
  setEl("img-meta", "");
  var img = document.getElementById("img-preview");
  if (dataUri) {
    img.src = dataUri;
    img.style.display = "block";
    document.getElementById("img-no-preview").style.display = "none";
  } else {
    img.style.display = "none";
    document.getElementById("img-no-preview").style.display = "block";
  }
}

/* ========== View Switching ========== */
function switchView(viewId) {
  document.querySelectorAll(".view").forEach(function(v) { v.classList.remove("active"); });
  var el = document.getElementById(viewId);
  if (el) el.classList.add("active");
}

/* ========== Sidebar ========== */
function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("collapsed");
}

/* ========== Drag & Drop ========== */
function onDrop(event, node) {
  var files = event.dataTransfer.files;
  if (!files || files.length === 0) {
    // Pueden ser URLs (desde navegador)
    var urls = event.dataTransfer.getData("text/uri-list");
    if (!urls) return;
  }
  var paths = [];
  for (var i = 0; i < files.length; i++) {
    paths.push(files[i].path);
  }
  if (paths.length > 0) {
    bridge.filesDropped(node.path, JSON.stringify(paths));
  }
}

/* ========== Cloud Dialogs ========== */
var cloudDialogMode = "";
var _githubSyncing = false;

/* ========== Settings Dialog ========== */
var _githubSyncing = false;

function showSettingsDialog() {
  document.getElementById("settings-dialog").classList.add("active");
  switchSettingsTab(null, 'settings-tab-roots');
  loadSettingsRootsHistory();
  loadSettingsGithubSection();
  loadSettingsDriveSection();
  loadSettingsDeepseekSection();
  loadSettingsAccountSection();
}

function closeSettingsDialog() {
  document.getElementById("settings-dialog").classList.remove("active");
}

function switchSettingsTab(e, tabId) {
  document.querySelectorAll('.settings-tab-btn').forEach(btn => {
    btn.classList.remove('active');
  });
  document.querySelectorAll('.settings-tab-content').forEach(content => {
    content.classList.remove('active');
  });
  
  if (e) {
    e.currentTarget.classList.add('active');
  } else {
    const activeBtn = document.querySelector(`.settings-tab-btn[data-target="${tabId}"]`);
    if (activeBtn) activeBtn.classList.add('active');
  }
  document.getElementById(tabId).classList.add('active');
}

// --- Roots Tab ---
function loadSettingsRootsHistory() {
  if (!bridge) return;
  
  bridge.getRootsJson(function(rootsJson) {
    const data = JSON.parse(rootsJson);
    const listEl = document.getElementById("roots-history-list");
    listEl.innerHTML = "";
    
    document.getElementById("active-root-path").textContent = data.current || "Ninguna seleccionada";
    document.getElementById("active-root-path").title = data.current || "";
    
    if (!data.roots || data.roots.length === 0) {
      listEl.innerHTML = '<div style="padding: 12px; font-size:12px; color:var(--text-muted); text-align:center;">No hay carpetas en el historial</div>';
      return;
    }
    
    data.roots.forEach(function(path) {
      const item = document.createElement("div");
      item.className = "roots-history-item";
      
      const pathText = document.createElement("span");
      pathText.className = "path-text";
      pathText.textContent = path;
      pathText.title = "Hacer doble clic para abrir: " + path;
      pathText.onclick = function() {
        settingsSelectRootPath(path);
      };
      
      const actions = document.createElement("div");
      actions.className = "actions";
      
      const selectBtn = document.createElement("button");
      selectBtn.className = "select-btn";
      selectBtn.textContent = "Abrir";
      selectBtn.onclick = function() {
        settingsSelectRootPath(path);
      };
      
      const removeBtn = document.createElement("button");
      removeBtn.className = "remove-btn";
      removeBtn.textContent = "Eliminar";
      removeBtn.onclick = function() {
        settingsRemoveRootPath(path);
      };
      
      actions.appendChild(selectBtn);
      actions.appendChild(removeBtn);
      
      item.appendChild(pathText);
      item.appendChild(actions);
      listEl.appendChild(item);
    });
  });
}

function settingsChangeRoot() {
  if (bridge) {
    bridge.changeRoot();
    setTimeout(loadSettingsRootsHistory, 1500);
  }
}

function settingsSelectRootPath(path) {
  if (bridge) {
    bridge.selectRootPath(path);
    closeSettingsDialog();
  }
}

function settingsRemoveRootPath(path) {
  if (bridge) {
    bridge.removeRootPath(path);
    setTimeout(loadSettingsRootsHistory, 300);
  }
}

// --- GitHub Tab ---
function loadSettingsGithubSection() {
  const tokenInput = document.getElementById("settings-github-token");
  const reposWrapper = document.getElementById("settings-github-repos-wrapper");
  const repoSelect = document.getElementById("settings-github-repo-select");
  const connectBtn = document.getElementById("settings-github-connect-btn");
  const disconnectBtn = document.getElementById("settings-github-disconnect-btn");
  const resultEl = document.getElementById("settings-github-result");
  
  resultEl.textContent = "";
  
  var currentToken = "";
  if (bridge && typeof bridge.getGithubToken === "function") {
    currentToken = bridge.getGithubToken();
  }
  tokenInput.value = currentToken;
  
  if (currentToken) {
    resultEl.textContent = "⌛ Cargando repositorios remotos...";
    resultEl.style.color = "var(--text-muted)";
    connectBtn.disabled = true;
    
    bridge.testGithubConnection(currentToken, function(resultJson) {
      const res = JSON.parse(resultJson);
      connectBtn.disabled = false;
      if (res.success) {
        tokenInput.style.display = "none";
        reposWrapper.style.display = "block";
        disconnectBtn.style.display = "inline-block";
        connectBtn.textContent = "Probar Conexión";
        
        repoSelect.innerHTML = "";
        const defaultOpt = document.createElement("option");
        defaultOpt.value = "";
        defaultOpt.textContent = "📁 [Mostrar todos los repositorios]";
        repoSelect.appendChild(defaultOpt);
        
        res.repos.forEach(function(r) {
          const opt = document.createElement("option");
          opt.value = r[0];
          opt.textContent = "📦 " + r[0];
          repoSelect.appendChild(opt);
        });
        
        bridge.getGithubSelectedRepo(function(selected) {
          repoSelect.value = selected || "";
        });
        
        repoSelect.onchange = function() {
          bridge.setGithubSelectedRepo(repoSelect.value);
          setupSettingsGithubBasePath();
        };
        
        setupSettingsGithubBasePath();
        resultEl.textContent = "✅ Conectado a GitHub";
        resultEl.style.color = "#16a34a";
      } else {
        tokenInput.style.display = "block";
        reposWrapper.style.display = "none";
        disconnectBtn.style.display = "none";
        connectBtn.textContent = "Conectar";
        resultEl.textContent = "❌ Error: " + res.error;
        resultEl.style.color = "#dc2626";
      }
    });
  } else {
    tokenInput.style.display = "block";
    reposWrapper.style.display = "none";
    disconnectBtn.style.display = "none";
    connectBtn.textContent = "Conectar";
  }
}

function settingsGithubConnect() {
  const tokenInput = document.getElementById("settings-github-token");
  const token = tokenInput.value.trim();
  if (!token) {
    const resultEl = document.getElementById("settings-github-result");
    resultEl.textContent = "❌ Ingresa un token válido";
    resultEl.style.color = "#dc2626";
    return;
  }
  loadGithubReposForSettings(token);
}

function loadGithubReposForSettings(token) {
  const tokenInput = document.getElementById("settings-github-token");
  const reposWrapper = document.getElementById("settings-github-repos-wrapper");
  const repoSelect = document.getElementById("settings-github-repo-select");
  const connectBtn = document.getElementById("settings-github-connect-btn");
  const disconnectBtn = document.getElementById("settings-github-disconnect-btn");
  const resultEl = document.getElementById("settings-github-result");
  
  resultEl.textContent = "⌛ Conectando con GitHub...";
  resultEl.style.color = "var(--text-muted)";
  connectBtn.disabled = true;
  
  bridge.testGithubConnection(token, function(resultJson) {
    const res = JSON.parse(resultJson);
    connectBtn.disabled = false;
    if (res.success) {
      tokenInput.style.display = "none";
      reposWrapper.style.display = "block";
      disconnectBtn.style.display = "inline-block";
      connectBtn.textContent = "Probar Conexión";
      
      repoSelect.innerHTML = "";
      const defaultOpt = document.createElement("option");
      defaultOpt.value = "";
      defaultOpt.textContent = "📁 [Mostrar todos los repositorios]";
      repoSelect.appendChild(defaultOpt);
      
      res.repos.forEach(function(r) {
        const opt = document.createElement("option");
        opt.value = r[0];
        opt.textContent = "📦 " + r[0];
        repoSelect.appendChild(opt);
      });
      
      bridge.getGithubSelectedRepo(function(selected) {
        repoSelect.value = selected || "";
      });
      
      repoSelect.onchange = function() {
        bridge.setGithubSelectedRepo(repoSelect.value);
        setupSettingsGithubBasePath();
      };
      
      setupSettingsGithubBasePath();
      resultEl.textContent = "✅ Conexión establecida con éxito";
      resultEl.style.color = "#16a34a";
    } else {
      tokenInput.style.display = "block";
      reposWrapper.style.display = "none";
      disconnectBtn.style.display = "none";
      connectBtn.textContent = "Conectar";
      resultEl.textContent = "❌ Error: " + res.error;
      resultEl.style.color = "#dc2626";
    }
  });
}

function settingsGithubDisconnect() {
  if (confirm("¿Estás seguro de que deseas desconectar tu cuenta de GitHub y borrar el token guardado?")) {
    if (bridge) {
      bridge.clearGithubToken();
      bridge.setGithubSelectedRepo("");
    }
    const tokenInput = document.getElementById("settings-github-token");
    tokenInput.value = "";
    tokenInput.style.display = "block";
    document.getElementById("settings-github-repos-wrapper").style.display = "none";
    document.getElementById("settings-github-disconnect-btn").style.display = "none";
    document.getElementById("settings-github-connect-btn").textContent = "Conectar";
    document.getElementById("settings-github-result").textContent = "Cuenta desconectada";
    document.getElementById("settings-github-result").style.color = "var(--text-muted)";
  }
}

function populateGithubFoldersDropdown(folders) {
  const select = document.getElementById("settings-github-base-path");
  if (!select) return;
  
  let currentValue = select.value;
  select.innerHTML = "";
  
  const rootOpt = document.createElement("option");
  rootOpt.value = "";
  rootOpt.textContent = "📁 [Raíz del repositorio]";
  select.appendChild(rootOpt);
  
  folders.forEach(function(folder) {
    const opt = document.createElement("option");
    opt.value = folder;
    opt.textContent = "📁 " + folder;
    select.appendChild(opt);
  });
  
  if (currentValue && folders.includes(currentValue)) {
    select.value = currentValue;
  } else if (bridge && typeof bridge.getGithubBasePath === "function") {
    bridge.getGithubBasePath(function(path) {
      select.value = path || "";
    });
  }
}

function setupSettingsGithubBasePath() {
  const select = document.getElementById("settings-github-base-path");
  if (!select) return;
  
  select.innerHTML = "";
  const loadingOpt = document.createElement("option");
  loadingOpt.value = "";
  loadingOpt.textContent = "⏳ Cargando carpetas...";
  select.appendChild(loadingOpt);
  
  if (bridge && typeof bridge.fetchGithubFolders === "function") {
    bridge.fetchGithubFolders();
  }
  
  select.onchange = function() {
    if (bridge && typeof bridge.saveGithubBasePath === "function") {
      bridge.saveGithubBasePath(select.value);
    }
  };
}

// --- Drive Tab ---
function loadSettingsDriveSection() {
  const credsStatus = document.getElementById("drive-creds-status");
  const authStatus = document.getElementById("drive-auth-status");
  const linkBtn = document.getElementById("settings-drive-link-btn");
  const unlinkBtn = document.getElementById("settings-drive-unlink-btn");
  const resultEl = document.getElementById("settings-drive-result");
  
  resultEl.textContent = "";
  
  if (bridge) {
    bridge.getDriveConfigStatus(function(statusJson) {
      const status = JSON.parse(statusJson);
      
      if (status.has_creds) {
        credsStatus.textContent = "✅ credentials.json cargado";
        credsStatus.style.color = "#16a34a";
      } else {
        credsStatus.textContent = "❌ credentials.json ausente";
        credsStatus.style.color = "#dc2626";
      }
      
      if (status.is_authenticated) {
        authStatus.textContent = "✅ Vinculado";
        authStatus.style.color = "#16a34a";
        linkBtn.style.display = "none";
        unlinkBtn.style.display = "inline-block";
      } else {
        authStatus.textContent = "Desconectado";
        authStatus.style.color = "var(--text-muted)";
        linkBtn.style.display = "inline-block";
        unlinkBtn.style.display = "none";
        
        if (!status.has_creds) {
          linkBtn.disabled = true;
          linkBtn.title = "Sube primero el archivo credentials.json";
        } else {
          linkBtn.disabled = false;
          linkBtn.title = "";
        }
      }
    });

    const baseFolderInput = document.getElementById("settings-drive-base-folder-id");
    if (baseFolderInput) {
      baseFolderInput.innerHTML = "";
      const loadingOpt = document.createElement("option");
      loadingOpt.value = "";
      loadingOpt.textContent = "⏳ Cargando carpetas...";
      baseFolderInput.appendChild(loadingOpt);
      
      if (typeof bridge.fetchDriveFolders === "function") {
        bridge.fetchDriveFolders();
      }
      
      baseFolderInput.onchange = function() {
        if (typeof bridge.saveDriveBaseFolderId === "function") {
          bridge.saveDriveBaseFolderId(baseFolderInput.value);
        }
      };
    }
  }
}

function populateDriveFoldersDropdown(folders) {
  const select = document.getElementById("settings-drive-base-folder-id");
  if (!select) return;
  
  let currentValue = select.value;
  select.innerHTML = "";
  
  const rootOpt = document.createElement("option");
  rootOpt.value = "";
  rootOpt.textContent = "📁 [Raíz de Drive]";
  select.appendChild(rootOpt);
  
  folders.forEach(function(folder) {
    const opt = document.createElement("option");
    opt.value = folder.id;
    opt.textContent = "📁 " + folder.name;
    select.appendChild(opt);
  });
  
  const hasValue = folders.some(f => f.id === currentValue);
  if (currentValue && hasValue) {
    select.value = currentValue;
  } else if (bridge && typeof bridge.getDriveBaseFolderId === "function") {
    bridge.getDriveBaseFolderId(function(folderId) {
      select.value = folderId || "";
    });
  }
}

function settingsSelectDriveCreds() {
  if (bridge) {
    bridge.selectDriveCreds();
    setTimeout(loadSettingsDriveSection, 1500);
  }
}

function settingsDriveLink() {
  if (bridge && typeof bridge.syncDrive === "function" && window.location.protocol === "file:") {
    showStatus("Vinculando Google Drive (Desktop)...");
    bridge.syncDrive();
    setTimeout(loadSettingsDriveSection, 3000);
  } else {
    showStatus("Abriendo autenticación de Google Drive...");
    var sessionToken = sessionStorage.getItem("app_session_token") || localStorage.getItem("app_session_token");
    var tokenQuery = sessionToken ? "?token=" + encodeURIComponent(sessionToken) : "";
    var authUrl = API_BASE_URL + "/api/sync/drive/auth" + tokenQuery;
    window.open(authUrl, "_blank", "width=600,height=600");
    var checks = 0;
    var interval = setInterval(function() {
      loadSettingsDriveSection();
      checks++;
      if (checks > 15) clearInterval(interval);
    }, 2000);
  }
}

function settingsDriveUnlink() {
  if (confirm("¿Deseas desvincular tu cuenta de Google Drive y borrar las credenciales de sesión?")) {
    if (bridge) {
      bridge.clearDriveToken();
    }
    setTimeout(loadSettingsDriveSection, 300);
  }
}

function toggleAiProviderFields() {
  const providerSelect = document.getElementById("settings-ai-provider");
  const dsSection = document.getElementById("settings-deepseek-section");
  const gemSection = document.getElementById("settings-gemini-section");
  if (!providerSelect || !dsSection || !gemSection) return;
  
  const provider = providerSelect.value;
  if (provider === "gemini") {
    dsSection.style.display = "none";
    gemSection.style.display = "block";
  } else {
    dsSection.style.display = "block";
    gemSection.style.display = "none";
  }
}

function loadSettingsDeepseekSection() {
  const providerSelect = document.getElementById("settings-ai-provider");
  const dsKeyInput = document.getElementById("settings-deepseek-key");
  const gemKeyInput = document.getElementById("settings-gemini-key");
  const gemModelSelect = document.getElementById("settings-gemini-model");
  const resultEl = document.getElementById("settings-deepseek-result");
  
  if (!dsKeyInput || !resultEl) return;
  
  resultEl.textContent = "";
  
  if (bridge && typeof bridge.getAiSettings === "function") {
    bridge.getAiSettings(function(settingsJson) {
      const settings = JSON.parse(settingsJson);
      if (providerSelect) providerSelect.value = settings.active_provider || "deepseek";
      dsKeyInput.value = settings.deepseek_api_key || "";
      if (gemKeyInput) gemKeyInput.value = settings.gemini_api_key || "";
      if (gemModelSelect) gemModelSelect.value = settings.gemini_model || "gemini-1.5-flash";
      toggleAiProviderFields();
    });
  } else {
    // Fallback to old slots
    if (bridge && typeof bridge.getDeepseekKey === "function") {
      dsKeyInput.value = bridge.getDeepseekKey();
    }
    toggleAiProviderFields();
  }
}

function settingsDeepseekSave() {
  const providerSelect = document.getElementById("settings-ai-provider");
  const dsKeyInput = document.getElementById("settings-deepseek-key");
  const gemKeyInput = document.getElementById("settings-gemini-key");
  const gemModelSelect = document.getElementById("settings-gemini-model");
  const resultEl = document.getElementById("settings-deepseek-result");
  
  if (!dsKeyInput || !resultEl) return;
  
  resultEl.textContent = "⌛ Guardando...";
  resultEl.style.color = "var(--text-muted)";
  
  const provider = providerSelect ? providerSelect.value : "deepseek";
  const dsKey = dsKeyInput.value.trim();
  const gemKey = gemKeyInput ? gemKeyInput.value.trim() : "";
  const gemModel = gemModelSelect ? gemModelSelect.value : "gemini-1.5-flash";
  
  if (bridge && typeof bridge.saveAiSettings === "function") {
    bridge.saveAiSettings(provider, dsKey, gemKey, gemModel, function(resJson) {
      const res = JSON.parse(resJson);
      if (res.success) {
        resultEl.textContent = "✅ Configuración de IA guardada correctamente.";
        resultEl.style.color = "#16a34a";
      } else {
        resultEl.textContent = "❌ Error al guardar: " + res.error;
        resultEl.style.color = "#dc2626";
      }
    });
  } else if (bridge && typeof bridge.saveDeepseekKey === "function") {
    // Fallback
    bridge.saveDeepseekKey(dsKey, function(resJson) {
      const res = JSON.parse(resJson);
      if (res.success) {
        resultEl.textContent = "✅ Clave guardada correctamente.";
        resultEl.style.color = "#16a34a";
      } else {
        resultEl.textContent = "❌ Error al guardar: " + res.error;
        resultEl.style.color = "#dc2626";
      }
    });
  }
}


// --- Account Tab (per-user workspace + integrations) ---
function _currentUsername() {
  return localStorage.getItem("app_current_user") || "";
}

function _sessionHeaders() {
  const token = sessionStorage.getItem("app_session_token")
             || localStorage.getItem("app_session_token")
             || "";
  return token ? { "X-Session-Token": token, "Content-Type": "application/json" }
               : { "Content-Type": "application/json" };
}

function _setResult(el, msg, ok) {
  if (!el) return;
  el.textContent = msg;
  el.style.color = ok ? "#16a34a" : "#dc2626";
}

function loadSettingsAccountSection() {
  const username = _currentUsername();
  const roleEl = document.getElementById("settings-account-role");
  const userEl = document.getElementById("settings-account-username");
  if (userEl) userEl.textContent = username || "—";
  if (roleEl) {
    const isAdmin = localStorage.getItem("app_is_admin") === "1";
    roleEl.textContent = isAdmin ? "(Administrador)" : "";
  }
  if (!username) return;

  const headers = { "X-Session-Token": sessionStorage.getItem("app_session_token")
                              || localStorage.getItem("app_session_token") || "" };

  // Workspace
  fetch(API_BASE_URL + "/api/auth/users/" + encodeURIComponent(username) + "/workspace",
        { headers: headers })
    .then(r => r.ok ? r.json() : Promise.reject(r))
    .then(data => {
      const pathEl = document.getElementById("settings-account-workspace-path");
      if (pathEl) pathEl.textContent = data.workspace_root + (data.exists ? "" : "  ⚠️ (no existe)");
    })
    .catch(() => {});

  // Integrations
  fetch(API_BASE_URL + "/api/auth/users/" + encodeURIComponent(username) + "/integrations",
        { headers: headers })
    .then(r => r.ok ? r.json() : Promise.reject(r))
    .then(data => {
      const ghStatus = document.getElementById("settings-account-github-status");
      const ghInput  = document.getElementById("settings-account-github-input");
      const ghClear  = document.getElementById("settings-account-github-clear-btn");
      if (ghStatus) {
        ghStatus.textContent = data.github_connected
          ? "🟢 Conectado a GitHub con tu token"
          : "⚪ Sin token propio (usará el global del servidor)";
        ghStatus.style.color = data.github_connected ? "#16a34a" : "var(--text-muted)";
      }
      if (ghInput) ghInput.value = "";
      if (ghClear) ghClear.style.display = data.github_connected ? "" : "none";

      const drStatus  = document.getElementById("settings-account-drive-status");
      const drLink    = document.getElementById("settings-account-drive-link-btn");
      const drUnlink  = document.getElementById("settings-account-drive-unlink-btn");
      if (drStatus) {
        drStatus.textContent = data.drive_connected
          ? "🟢 Conectado a Google Drive"
          : "⚪ No vinculado";
        drStatus.style.color = data.drive_connected ? "#16a34a" : "var(--text-muted)";
      }
      if (drLink)   drLink.style.display   = data.drive_connected ? "none" : "";
      if (drUnlink) drUnlink.style.display = data.drive_connected ? "" : "none";
    })
    .catch(() => {});
}

function settingsAccountSaveWorkspace() {
  const username = _currentUsername();
  const input = document.getElementById("settings-account-workspace-input");
  const result = document.getElementById("settings-account-workspace-result");
  if (!username || !input || !result) return;

  const newPath = input.value.trim();
  if (!newPath) {
    _setResult(result, "❌ Escribe una ruta válida.", false);
    return;
  }

  result.textContent = "⌛ Guardando...";
  result.style.color = "var(--text-muted)";

  fetch(API_BASE_URL + "/api/auth/users/" + encodeURIComponent(username) + "/workspace",
    { method: "PUT", headers: _sessionHeaders(),
      body: JSON.stringify({ workspace_root: newPath }) })
    .then(r => r.json().then(body => ({ status: r.status, body })))
    .then(({ status, body }) => {
      if (status === 200 && body.success) {
        _setResult(result, "✅ Carpeta actualizada. Refrescando árbol...", true);
        input.value = "";
        const pathEl = document.getElementById("settings-account-workspace-path");
        if (pathEl) pathEl.textContent = body.workspace_root;
        if (bridge && typeof bridge.refreshTree === "function") {
          bridge.refreshTree();
        }
        loadSettingsRootsHistory();
      } else {
        _setResult(result, "❌ " + (body.detail || "Error al guardar"), false);
      }
    })
    .catch(err => _setResult(result, "❌ " + err.message, false));
}

function settingsAccountSaveGithub() {
  const username = _currentUsername();
  const input = document.getElementById("settings-account-github-input");
  const result = document.getElementById("settings-account-github-result");
  if (!username || !input || !result) return;
  const token = input.value.trim();
  if (!token) {
    _setResult(result, "❌ Pega un token antes de guardar.", false);
    return;
  }

  result.textContent = "⌛ Guardando...";
  result.style.color = "var(--text-muted)";

  fetch(API_BASE_URL + "/api/auth/users/" + encodeURIComponent(username) + "/integrations",
    { method: "PUT", headers: _sessionHeaders(),
      body: JSON.stringify({ github_token: token }) })
    .then(r => r.json().then(body => ({ status: r.status, body })))
    .then(({ status, body }) => {
      if (status === 200 && body.success) {
        _setResult(result, "✅ Token guardado.", true);
        input.value = "";
        loadSettingsAccountSection();
        if (bridge && typeof bridge.refreshTree === "function") bridge.refreshTree();
      } else {
        _setResult(result, "❌ " + (body.detail || "Error al guardar"), false);
      }
    })
    .catch(err => _setResult(result, "❌ " + err.message, false));
}

function settingsAccountClearGithub() {
  const username = _currentUsername();
  const result = document.getElementById("settings-account-github-result");
  if (!username) return;
  if (!confirm("¿Desconectar tu token personal de GitHub?\nSe usará el token global del servidor (si existe).")) return;

  fetch(API_BASE_URL + "/api/auth/users/" + encodeURIComponent(username) + "/integrations",
    { method: "PUT", headers: _sessionHeaders(),
      body: JSON.stringify({ clear_github: true }) })
    .then(r => r.json().then(body => ({ status: r.status, body })))
    .then(({ status, body }) => {
      if (status === 200 && body.success) {
        _setResult(result, "✅ Token eliminado.", true);
        loadSettingsAccountSection();
        if (bridge && typeof bridge.refreshTree === "function") bridge.refreshTree();
      } else {
        _setResult(result, "❌ " + (body.detail || "Error"), false);
      }
    })
    .catch(err => _setResult(result, "❌ " + err.message, false));
}

function settingsAccountLinkDrive() {
  const result = document.getElementById("settings-account-drive-result");
  // The OAuth flow redirects the browser; the server will route the
  // resulting credentials to *this* user because the X-Session-Token
  // is forwarded by api_bridge.js? No — for the redirect we need to
  // bake the session token into the URL. Send the token as a query
  // param so the server's drive_auth endpoint can pick it up.
  const token = sessionStorage.getItem("app_session_token")
             || localStorage.getItem("app_session_token") || "";
  if (!token) {
    _setResult(result, "❌ No hay sesión activa.", false);
    return;
  }
  _setResult(result, "⌛ Abriendo Google…", true);
  // Open in same tab so the callback can return the user here.
  window.location.href = API_BASE_URL + "/api/sync/drive/auth?token="
                       + encodeURIComponent(token);
}

function settingsAccountUnlinkDrive() {
  const username = _currentUsername();
  const result = document.getElementById("settings-account-drive-result");
  if (!username) return;
  if (!confirm("¿Desconectar tu cuenta de Google Drive?")) return;

  fetch(API_BASE_URL + "/api/auth/users/" + encodeURIComponent(username) + "/integrations",
    { method: "PUT", headers: _sessionHeaders(),
      body: JSON.stringify({ clear_drive: true }) })
    .then(r => r.json().then(body => ({ status: r.status, body })))
    .then(({ status, body }) => {
      if (status === 200 && body.success) {
        _setResult(result, "✅ Google Drive desconectado.", true);
        loadSettingsAccountSection();
        if (bridge && typeof bridge.refreshTree === "function") bridge.refreshTree();
      } else {
        _setResult(result, "❌ " + (body.detail || "Error"), false);
      }
    })
    .catch(err => _setResult(result, "❌ " + err.message, false));
}


function pushCodeChanges() {
  if (currentFilePath && currentFileSource !== "local") {
    commitCurrentFile();
  } else {
    if (typeof bridge !== 'undefined' && bridge) {
      if (confirm("¿Deseas subir todos los archivos de código modificados (launchpad.py, launchpad.bat, requirements.txt) a GitHub?")) {
        showStatus("Subiendo código a GitHub...");
        bridge.showLoading(true);
        bridge.pushCodeToGithub();
      }
    }
  }
}

/* ========== Conflict Dialog ========== */
function showConflictModal(name, service) {
  var txt = document.getElementById("conflict-text");
  if (txt) {
    txt.textContent = "El archivo '" + name + "' ya ha sido descargado en la carpeta temporal y contiene diferencias respecto a la versión en " + service + ".\n\n¿Deseas continuar con la versión remota (sobrescribir) o mantener tus cambios locales temporales?";
  }
  var diag = document.getElementById("conflict-dialog");
  if (diag) diag.classList.add("active");
}

function resolveConflict(useRemote) {
  var diag = document.getElementById("conflict-dialog");
  if (diag) diag.classList.remove("active");
  if (bridge) {
    bridge.resolveConflict(useRemote);
  }
}

/* ========== Keyboard Shortcuts ========== */
document.addEventListener("keydown", function(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveCurrentFile(); }
  if ((e.ctrlKey || e.metaKey) && e.key === "o") { e.preventDefault(); var ep = getExplorerPath(); if (ep) bridge.openInExplorer(ep); }
  if ((e.ctrlKey || e.metaKey) && e.key === "r") { e.preventDefault(); bridge.refreshTree(); }
  if ((e.ctrlKey || e.metaKey) && e.key === "b") { e.preventDefault(); toggleSidebar(); }
  if (e.key === "Escape" && window.pendingMoveSource) { e.preventDefault(); resetMoveState(); showStatus("Movimiento cancelado"); }
});

/* ========== New File & Rename Handlers ========== */
function createNewMdFile() {
  if (!bridge) return;
  var explorerPath = getExplorerPath();
  var isCloud = false;
  if (selectedNode) {
    var t = selectedNode._type || "";
    isCloud = (t.startsWith("github") || t.startsWith("drive") || t === "dir" || t === "drive_folder");
  }
  
  if (isCloud) {
    var filename = prompt("Nombre del nuevo archivo (con extensión .md):", "nota_nueva.md");
    if (!filename) return;
    if (!filename.toLowerCase().endsWith(".md")) {
      filename += ".md";
    }
    bridge.createCloudFile(explorerPath, filename);
  } else {
    bridge.createNewFile(explorerPath);
  }
}

function deleteSelectedItem() {
  if (!bridge) return;
  if (!selectedNode) {
    showStatus("Selecciona un archivo o carpeta para eliminar");
    return;
  }
  
  var currentName = selectedNode.name.replace(/^[^\s]+\s/, '');
  var isCloud = false;
  var t = selectedNode._type || "";
  isCloud = (t.startsWith("github") || t.startsWith("drive") || t === "dir" || t === "drive_folder");
  
  var choice = confirm("¿Estás seguro de que deseas ELIMINAR permanentemente '" + currentName + "'?\nEsta acción no se puede deshacer.");
  if (!choice) return;

  if (isCloud) {
    bridge.deleteCloudItem(JSON.stringify(selectedNode));
  } else {
    bridge.deleteItem(selectedPath);
  }
}

function renameSelectedItem() {
  if (!bridge) return;
  if (!selectedNode) {
    showStatus("Selecciona un archivo o carpeta para renombrar");
    return;
  }
  
  var currentName = selectedNode.name.replace(/^[^\s]+\s/, '');
  var isCloud = false;
  var t = selectedNode._type || "";
  isCloud = (t.startsWith("github") || t.startsWith("drive") || t === "dir" || t === "drive_folder");
  
  var newName = prompt("Nuevo nombre:", currentName);
  if (!newName) return;
  
  if (isCloud) {
    bridge.renameCloudItem(JSON.stringify(selectedNode), newName);
  } else {
    bridge.renameItem(selectedPath, newName);
  }
}

function moveSelectedItem() {
  if (!bridge) return;
  
  var moveBtn = document.getElementById("btn-move-item");
  
  // Si no tenemos un origen seleccionado, cortamos el elemento actual
  if (!window.pendingMoveSource) {
    if (!selectedNode) {
      showStatus("Selecciona un archivo o carpeta para mover");
      return;
    }
    
    var t = selectedNode._type || "";
    var isCloud = (t.startsWith("github") || t.startsWith("drive") || t === "dir" || t === "drive_folder");
    if (isCloud) {
      showStatus("Mover archivos en la nube no está soportado");
      return;
    }
    
    window.pendingMoveSource = selectedPath;
    window.pendingMoveSourceName = selectedNode.name.replace(/^[^\s]+\s/, '');
    
    if (moveBtn) {
      moveBtn.innerHTML = "<span>📋</span><span>Pegar</span>";
      moveBtn.title = "Pegar elemento cortado en el destino seleccionado (o presiona Esc para cancelar)";
    }
    showStatus("Cortado: '" + window.pendingMoveSourceName + "'. Selecciona el destino y haz clic en Pegar.");
  } else {
    // Si ya tenemos origen, la selección actual es la carpeta de destino
    var targetFolder = "";
    if (selectedNode) {
      var t = selectedNode._type || "";
      // Si el destino es carpeta o directorio, lo usamos
      if (t === "folder" || t === "drive_folder" || t === "dir") {
        targetFolder = selectedPath;
      } else {
        // Si es un archivo, obtenemos su carpeta contenedora (directorio padre)
        var sep = selectedPath.includes('/') ? '/' : '\\';
        targetFolder = selectedPath.substring(0, selectedPath.lastIndexOf(sep));
      }
    } else {
      // Si no hay nada seleccionado, usamos la raíz activa
      targetFolder = getExplorerPath();
    }
    
    if (!targetFolder) {
      showStatus("No se pudo determinar la carpeta de destino.");
      return;
    }
    
    // Validar que el origen y destino no sean el mismo o descendientes
    var sep = targetFolder.includes('/') ? '/' : '\\';
    if (window.pendingMoveSource === targetFolder || targetFolder.startsWith(window.pendingMoveSource + sep)) {
      showStatus("Movimiento cancelado (destino inválido o idéntico)");
      resetMoveState();
      return;
    }
    
    bridge.moveItem(window.pendingMoveSource, targetFolder);
    resetMoveState();
  }
}

function resetMoveState() {
  window.pendingMoveSource = null;
  window.pendingMoveSourceName = null;
  var moveBtn = document.getElementById("btn-move-item");
  if (moveBtn) {
    moveBtn.innerHTML = "<span>📂</span><span>Mover</span>";
    moveBtn.title = "Mover elemento seleccionado";
  }
}

/* ========== Custom Markdown Editor Enhancements ========== */

function getEditorMode() {
  if (!editorInstance) return 'markdown';
  if (typeof editorInstance.isMarkdownMode === 'function') {
    return editorInstance.isMarkdownMode() ? 'markdown' : 'wysiwyg';
  }
  if (typeof editorInstance.isWysiwygMode === 'function') {
    return editorInstance.isWysiwygMode() ? 'wysiwyg' : 'markdown';
  }
  if (typeof editorInstance.getCurrentMode === 'function') {
    return editorInstance.getCurrentMode();
  }
  if (typeof editorInstance.getMode === 'function') {
    return editorInstance.getMode();
  }
  if (typeof editorInstance.isMarkdown === 'function') {
    return editorInstance.isMarkdown() ? 'markdown' : 'wysiwyg';
  }
  try {
    if (editorInstance.isWysiwyg && typeof editorInstance.isWysiwyg === 'function') {
      return editorInstance.isWysiwyg() ? 'wysiwyg' : 'markdown';
    }
  } catch(e) {}
  
  const el = editorInstance.getEl ? editorInstance.getEl() : null;
  if (el) {
    if (el.querySelector('.toastui-editor-ww-container') && el.querySelector('.toastui-editor-ww-container').style.display !== 'none') {
      return 'wysiwyg';
    }
  }
  return 'markdown';
}

function tsvToMarkdown(tsv) {
  const lines = tsv.trim().split('\n');
  if (lines.length === 0) return null;
  
  const rows = lines.map(line => line.replace(/\r$/, '').split('\t'));
  const maxCols = Math.max(...rows.map(r => r.length));
  if (maxCols <= 1 && rows.length <= 1) return null;
  
  let markdown = '\n';
  const headers = rows[0].map(cell => cell.trim() || ' ');
  markdown += '| ' + headers.join(' | ') + ' |\n';
  markdown += '| ' + Array(maxCols).fill('---').join(' | ') + ' |\n';
  
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    const paddedRow = row.map(cell => cell.trim());
    while (paddedRow.length < maxCols) paddedRow.push('');
    markdown += '| ' + paddedRow.join(' | ') + ' |\n';
  }
  markdown += '\n';
  return markdown;
}

function handleExcelPaste(e) {
  const html = e.clipboardData.getData('text/html');
  const text = e.clipboardData.getData('text/plain');
  
  // Evitar procesar como Excel si proviene de Word
  if (html && (
    html.includes('urn:schemas-microsoft-com:office:word') ||
    html.includes('MsoNormal') ||
    html.includes('MsoListParagraph') ||
    html.includes('MsoTableGrid') ||
    html.includes('WordDocument') ||
    html.includes('name="Generator" content="Microsoft Word"')
  )) {
    return;
  }
  
  if (text && text.includes('\t') && text.includes('\n')) {
    // Validar densidad de columnas para asegurarnos de que es una tabla estructurada
    const lines = text.trim().split('\n');
    const rows = lines.map(line => line.replace(/\r$/, '').split('\t'));
    const totalCols = rows.reduce((acc, row) => acc + row.length, 0);
    const avgCols = totalCols / rows.length;
    
    // Si tiene un promedio de menos de 1.5 columnas, es texto tabulado y no una tabla Excel
    if (avgCols < 1.5) {
      return;
    }

    const mdTable = tsvToMarkdown(text);
    if (mdTable) {
      e.preventDefault();
      e.stopPropagation();
      if (!editorInstance) return;
      const mode = getEditorMode();
      if (mode === 'markdown') {
        editorInstance.insertText(mdTable);
      } else {
        editorInstance.changeMode('markdown');
        editorInstance.insertText(mdTable);
        editorInstance.changeMode('wysiwyg');
      }
      showStatus("Tabla de Excel convertida e insertada");
    }
  }
}

function isMarkdownTableBlock(blockText) {
  const lines = blockText.trim().split('\n');
  return lines.length >= 2 && lines[0].includes('|') && lines[1].includes('|') && lines[1].includes('-');
}

function getBlocksFromMarkdown(md) {
  const lines = md.split('\n');
  const blocks = [];
  let currentBlock = [];
  let inCodeBlock = false;
  for (let line of lines) {
    if (line.trim().startsWith('```')) inCodeBlock = !inCodeBlock;
    if (line.trim() === '' && !inCodeBlock) {
      if (currentBlock.length > 0) {
        blocks.push(currentBlock.join('\n'));
        currentBlock = [];
      }
    } else {
      currentBlock.push(line);
    }
  }
  if (currentBlock.length > 0) blocks.push(currentBlock.join('\n'));
  return blocks;
}

function parseStylesFromMarkdown(md) {
  const blocks = getBlocksFromMarkdown(md);
  const tableStyles = [];
  const blockStyles = [];
  let tableIndex = 0, blockIndex = 0;
  blocks.forEach(block => {
    if (isMarkdownTableBlock(block)) {
      const m = block.match(/^<!--\s*table-style:\s*([^\n>]*)\s*-->/);
      tableStyles.push({ index: tableIndex++, style: m ? m[1] : '' });
    } else {
      const m = block.match(/^<!--\s*block-style:\s*([^\n>]*)\s*-->/);
      blockStyles.push({ index: blockIndex++, style: m ? m[1] : '' });
    }
  });
  return { tableStyles, blockStyles };
}

function applyCustomStyles() {
  if (!editorInstance) return;
  const md = editorInstance.getMarkdown();
  const styles = parseStylesFromMarkdown(md);
  const containers = document.querySelectorAll('.toastui-editor-contents, .toastui-editor-md-preview');
  containers.forEach(container => {
    applyStylesToContainer(container, styles);
    setupInteractiveTableTools(container);
  });
}

function applyStylesToContainer(container, styles) {
  container.querySelectorAll('table').forEach((table, idx) => {
    const s = styles.tableStyles.find(item => item.index === idx);
    s && s.style ? table.setAttribute('style', s.style) : table.removeAttribute('style');
  });
  const blocks = Array.from(container.querySelectorAll('p, blockquote, h1, h2, h3, h4, h5, h6, pre')).filter(b => !b.closest('li, td, th'));
  blocks.forEach((block, idx) => {
    const s = styles.blockStyles.find(item => item.index === idx);
    s && s.style ? block.setAttribute('style', s.style) : block.removeAttribute('style');
  });
}

function styleCodeBlocks() {
  // Only style code blocks (headers, copy button) in the static preview containers, NOT in ProseMirror!
  const containers = document.querySelectorAll('.toastui-editor-contents, .toastui-editor-md-preview');
  containers.forEach(container => {
    container.querySelectorAll('pre').forEach(pre => {
      const code = pre.querySelector('code');
      if (!code) return;

      let lang = 'text';
      const classMatch = code.className.match(/language-(\w+)/) || pre.className.match(/language-(\w+)/);
      if (classMatch) {
        lang = classMatch[1];
      } else {
        const dataLang = code.getAttribute('data-language') || pre.getAttribute('data-language');
        if (dataLang) {
          lang = dataLang;
        }
      }

      // 1. Create header if missing
      let header = pre.querySelector('.code-block-header');
      if (!header) {
        header = document.createElement('div');
        header.className = 'code-block-header';
        header.contentEditable = 'false';

        const langSpan = document.createElement('span');
        langSpan.className = 'code-block-lang';
        langSpan.textContent = lang.toUpperCase();

        // Container for actions (copy and collapse)
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'code-block-actions';
        actionsDiv.style.display = 'flex';
        actionsDiv.style.gap = '8px';
        actionsDiv.style.alignItems = 'center';

        // Collapse Button
        const collapseBtn = document.createElement('button');
        collapseBtn.className = 'code-block-collapse-btn';

        const codeText = code.textContent;
        const isCurrentlyCollapsed = window.collapsedCodeBlocks && window.collapsedCodeBlocks.has(codeText);
        if (isCurrentlyCollapsed) {
          pre.classList.add('code-block-collapsed');
          collapseBtn.innerHTML = `<span>▶</span> <span class="collapse-text">Maximizar</span>`;
        } else {
          collapseBtn.innerHTML = `<span>▼</span> <span class="collapse-text">Minimizar</span>`;
        }

        collapseBtn.onclick = (e) => {
          e.stopPropagation();
          e.preventDefault();
          const isCollapsed = pre.classList.toggle('code-block-collapsed');
          if (!window.collapsedCodeBlocks) {
            window.collapsedCodeBlocks = new Set();
          }
          if (isCollapsed) {
            window.collapsedCodeBlocks.add(codeText);
            collapseBtn.innerHTML = `<span>▶</span> <span class="collapse-text">Maximizar</span>`;
          } else {
            window.collapsedCodeBlocks.delete(codeText);
            collapseBtn.innerHTML = `<span>▼</span> <span class="collapse-text">Minimizar</span>`;
          }
        };

        // Copy Button
        const copyBtn = document.createElement('button');
        copyBtn.className = 'code-block-copy';
        copyBtn.innerHTML = `<span>📋</span> <span class="copy-text">Copiar</span>`;
        copyBtn.onclick = (e) => {
          e.stopPropagation();
          e.preventDefault();
          const textToCopy = code.innerText || code.textContent;
          navigator.clipboard.writeText(textToCopy).then(() => {
            copyBtn.classList.add('copied');
            copyBtn.innerHTML = `<span>✔️</span> <span class="copy-text">Copiado</span>`;
            setTimeout(() => {
              copyBtn.classList.remove('copied');
              copyBtn.innerHTML = `<span>📋</span> <span class="copy-text">Copiar</span>`;
            }, 2000);
          }).catch(err => {
            console.error('Error al copiar:', err);
          });
        };

        // Explain Button (AI helper)
        const explainBtn = document.createElement('button');
        explainBtn.className = 'code-block-explain-btn';
        explainBtn.innerHTML = `<span>💡</span> <span class="explain-text">Explicar</span>`;
        explainBtn.onclick = (e) => {
          e.stopPropagation();
          e.preventDefault();
          const codeText = code.innerText || code.textContent;
          explainCodeBlock(lang, codeText);
        };

        actionsDiv.appendChild(explainBtn);
        actionsDiv.appendChild(collapseBtn);
        actionsDiv.appendChild(copyBtn);

        header.appendChild(langSpan);
        header.appendChild(actionsDiv);
        
        pre.style.position = 'relative';
        pre.insertBefore(header, pre.firstChild);
      }
    });
  });
}

function renderWysiwygMermaidOverlays() {
  // Discarded inline overlays in WYSIWYG mode as requested by user
  const overlaysContainer = document.getElementById('wysiwyg-mermaid-overlays');
  if (overlaysContainer) {
    overlaysContainer.remove();
  }
  // Clean up ProseMirror pre and code elements styles
  document.querySelectorAll('.toastui-editor-ww-container .ProseMirror pre').forEach(pre => {
    pre.style.height = '';
    pre.classList.remove('mermaid-render-mode');
    pre.classList.remove('mermaid-code-mode');
    const code = pre.querySelector('code');
    if (code) {
      code.style.display = '';
    }
  });
}

let floatingToolbar = null;
let activeElement = null;
let activeElementType = null;
let activeElementIndex = -1;

function createFloatingToolbar() {
  if (document.getElementById('floating-style-toolbar')) return;
  floatingToolbar = document.createElement('div');
  floatingToolbar.id = 'floating-style-toolbar';
  floatingToolbar.className = 'floating-style-toolbar hidden';
  floatingToolbar.innerHTML = `
    <div class="toolbar-section"><label>Ancho</label><div class="btn-group"><button class="tb-w-btn" data-val="">Auto</button><button class="tb-w-btn" data-val="width: 50%;">50%</button><button class="tb-w-btn" data-val="width: 75%;">75%</button><button class="tb-w-btn" data-val="width: 100%;">100%</button></div></div>
    <div class="toolbar-divider"></div>
    <div class="toolbar-section"><label>Letra</label><div class="btn-group"><button class="tb-f-btn" data-val="">Def</button><button class="tb-f-btn" data-val="font-size: 12px;">12px</button><button class="tb-f-btn" data-val="font-size: 14px;">14px</button><button class="tb-f-btn" data-val="font-size: 16px;">16px</button></div></div>
    <div class="toolbar-divider tb-table-only"></div>
    <div class="toolbar-section tb-table-only"><label>Alinear</label><div class="btn-group"><button id="tb-align-left" title="Izquierda">⬅️</button><button id="tb-align-center" title="Centro">⬌</button><button id="tb-align-right" title="Derecha">➡️</button></div></div>
    <div class="toolbar-divider tb-table-only"></div>
    <div class="toolbar-section tb-table-only"><label>Tablas</label><button id="tb-add-col">➕ Col</button><button id="tb-del-col" class="btn-danger">➖ Col</button><button id="tb-add-row">➕ Fila</button><button id="tb-del-row" class="btn-danger">➖ Fila</button></div>
    <div class="toolbar-divider"></div><button id="tb-close" title="Cerrar">✕</button>
  `;
  document.body.appendChild(floatingToolbar);
  document.querySelectorAll('.tb-w-btn').forEach(btn => btn.onclick = () => { document.querySelectorAll('.tb-w-btn').forEach(b => b.classList.remove('active')); btn.classList.add('active'); updateActiveElementStyle(); });
  document.querySelectorAll('.tb-f-btn').forEach(btn => btn.onclick = () => { document.querySelectorAll('.tb-f-btn').forEach(b => b.classList.remove('active')); btn.classList.add('active'); updateActiveElementStyle(); });
  document.getElementById('tb-align-left').onclick = () => modifyTableAlignment('left');
  document.getElementById('tb-align-center').onclick = () => modifyTableAlignment('center');
  document.getElementById('tb-align-right').onclick = () => modifyTableAlignment('right');
  document.getElementById('tb-add-col').onclick = () => modifyTableStructure('add-col');
  document.getElementById('tb-del-col').onclick = () => modifyTableStructure('del-col');
  document.getElementById('tb-add-row').onclick = () => modifyTableStructure('add-row');
  document.getElementById('tb-del-row').onclick = () => modifyTableStructure('del-row');
  document.getElementById('tb-close').onclick = hideToolbar;
}

function initElementClickWatchers() {
  document.addEventListener('click', function(e) {
    if (e.target.closest('#floating-style-toolbar')) return;
    const container = document.getElementById('editor-container');
    if (!container || !container.contains(e.target)) { hideToolbar(); return; }
    const table = e.target.closest('table');
    if (table) {
      const cell = e.target.closest('td, th');
      activeColIndex = cell ? Array.from(cell.closest('tr').children).indexOf(cell) : -1;
      showToolbarForElement(table, 'table');
    } else {
      hideToolbar();
    }
  });
  document.addEventListener('scroll', hideToolbar, true);
}

function showToolbarForElement(el, type) {
  createFloatingToolbar();
  activeElement = el; activeElementType = 'table';
  const container = el.closest('.toastui-editor-contents, .ProseMirror');
  if (!container) return;
  
  activeElementIndex = Array.from(container.querySelectorAll('table')).indexOf(el);
  document.querySelectorAll('.tb-table-only').forEach(i => i.style.display = 'flex');
  
  const style = el.getAttribute('style') || '';
  document.querySelectorAll('.tb-w-btn').forEach(btn => btn.classList.toggle('active', (btn.dataset.val === '' && !style.includes('width:')) || (btn.dataset.val !== '' && style.includes(btn.dataset.val))));
  document.querySelectorAll('.tb-f-btn').forEach(btn => btn.classList.toggle('active', (btn.dataset.val === '' && !style.includes('font-size:')) || (btn.dataset.val !== '' && style.includes(btn.dataset.val))));
  if (activeColIndex !== -1) {
    const md = editorInstance.getMarkdown();
    const tables = getTablesFromMarkdown(md);
    if (activeElementIndex < tables.length) {
      const align = getColumnAlignment(tables[activeElementIndex].fullText, activeColIndex);
      ['left', 'center', 'right'].forEach(a => document.getElementById('tb-align-' + a).classList.toggle('active', align === a));
    }
  }
  const rect = el.getBoundingClientRect();
  floatingToolbar.style.left = (rect.left + (rect.width - 320) / 2 + window.scrollX) + 'px';
  floatingToolbar.style.top = (rect.top - 42 + window.scrollY) + 'px';
  floatingToolbar.classList.remove('hidden');
}

function hideToolbar() { if (floatingToolbar) floatingToolbar.classList.add('hidden'); activeElement = null; activeElementIndex = -1; }

function updateActiveElementStyle() {
  if (!editorInstance || activeElementIndex === -1) return;
  const w = document.querySelector('.tb-w-btn.active')?.dataset.val || '';
  const f = document.querySelector('.tb-f-btn.active')?.dataset.val || '';
  const style = (w + ' ' + f).trim();
  const md = editorInstance.getMarkdown();
  const updatedMd = (activeElementType === 'table') ? updateTableStyleInMarkdown(md, activeElementIndex, style) : updateBlockStyleInMarkdown(md, activeElementIndex, style);
  if (updatedMd !== md) {
    editorInstance.setMarkdown(updatedMd, false);
    editorDirty = true;
    applyCustomStyles();
  }
}

function getColumnAlignment(tableText, colIndex) {
  const lines = tableText.replace(/<!--.*?-->\n?/, '').trim().split('\n');
  if (lines.length < 2) return 'left';
  const cells = lines[1].trim().split('|');
  const offset = cells[0] === '' ? 1 : 0;
  const val = (cells[colIndex + offset] || '').trim();
  if (val.startsWith(':') && val.endsWith(':')) return 'center';
  if (val.endsWith(':')) return 'right';
  return 'left';
}

function modifyTableAlignment(align) {
  if (!editorInstance || activeElementIndex === -1 || activeColIndex === -1) return;
  const md = editorInstance.getMarkdown();
  const tables = getTablesFromMarkdown(md);
  if (activeElementIndex >= tables.length) return;
  const table = tables[activeElementIndex];
  const newTable = modifyMarkdownTableAlignment(table.fullText, activeColIndex, align);
  editorInstance.setMarkdown(md.substring(0, table.index) + newTable + md.substring(table.index + table.length), false);
  applyCustomStyles();
}

function modifyMarkdownTableAlignment(tableText, colIndex, align) {
  let style = '', clean = tableText;
  const m = tableText.match(/^(<!--\s*table-style:\s*[^\n>]*\s*-->\s*\n)/);
  if (m) { style = m[1]; clean = tableText.substring(m[0].length); }
  const lines = clean.trim().split('\n');
  const cells = lines[1].trim().split('|');
  const offset = cells[0] === '' ? 1 : 0;
  const idx = colIndex + offset;
  if (idx < cells.length) {
    cells[idx] = (align === 'left' ? ' :--- ' : align === 'center' ? ' :---: ' : ' ---: ');
    lines[1] = cells.join('|');
  }
  return style + lines.join('\n') + '\n';
}

function modifyTableStructure(action) {
  if (!editorInstance || activeElementIndex === -1) return;
  const md = editorInstance.getMarkdown();
  const tables = getTablesFromMarkdown(md);
  if (activeElementIndex >= tables.length) return;
  const t = tables[activeElementIndex];
  let style = '', clean = t.fullText;
  const m = t.fullText.match(/^(<!--\s*table-style:\s*[^\n>]*\s*-->\s*\n)/);
  if (m) { style = m[1]; clean = t.fullText.substring(m[0].length); }
  let rows = clean.trim().split('\n').map(l => l.split('|').map(c => c.trim()));
  if (action === 'add-col') rows.forEach((r, i) => r.splice(r.length - 1, 0, i === 1 ? '---' : ' '));
  else if (action === 'del-col') rows.forEach(r => r.length > 2 && r.splice(r.length - 2, 1));
  else if (action === 'add-row') rows.push(Array(rows[0].length).fill(' '));
  else if (action === 'del-row' && rows.length > 2) rows.pop();
  const newTable = style + rows.map(r => '| ' + r.join(' | ') + ' |').join('\n') + '\n';
  editorInstance.setMarkdown(md.substring(0, t.index) + newTable + md.substring(t.index + t.length), false);
  applyCustomStyles();
}

function updateTableStyleInMarkdown(md, target, style) {
  const tableRegex = /(?:(?:^|\n)(?:<!--\s*table-style:\s*[^\n>]*\s*-->\s*\n)?)?((?:^[^\n]*\|[^\n]*\n)(?:^[^\n]*\|[-:\s|]+\|[^\n]*(?:\n|$))(?:^[^\n]*\|[^\n]*(?:\n|$))*)/gm;
  let match, count = 0, res = '', last = 0;
  while ((match = tableRegex.exec(md)) !== null) {
    res += md.substring(last, match.index);
    res += (count === target ? (style ? `<!-- table-style: ${style} -->\n` : '') : (match[0].startsWith('\n') ? '\n' : '')) + match[1];
    last = tableRegex.lastIndex; count++;
  }
  return res + md.substring(last);
}

function updateBlockStyleInMarkdown(md, target, style) {
  const blocks = getBlocksFromMarkdown(md);
  let bIdx = 0;
  const newBlocks = blocks.map(b => {
    if (isMarkdownTableBlock(b)) return b;
    if (bIdx++ === target) return style ? `<!-- block-style: ${style} -->\n${b.replace(/^<!--\s*block-style:\s*[^\n>]*\s*-->\s*\n?/, '')}` : b.replace(/^<!--\s*block-style:\s*[^\n>]*\s*-->\s*\n?/, '');
    return b;
  });
  return newBlocks.join('\n\n');
}

// ==========================================================================
// Mermaid Modal Control Functions
// ==========================================================================
var activeTemplateId = 'flowchart';
var panZoomInstance = null;
var renderTimeout = null;

const mermaidTemplates = [
  {
    id: 'flowchart',
    name: 'Diagrama de Flujo',
    desc: 'Procesos de decisión y lógica de control',
    icon: '📊',
    code: `graph TD
    A[Inicio] --> B{¿Es válido?}
    B -- Sí --> C[Procesar datos]
    B -- No --> D[Mostrar error]
    C --> E[Fin]
    D --> E`
  },
  {
    id: 'class',
    name: 'Diagrama de Clases',
    desc: 'Estructura de clases y relaciones de herencia',
    icon: '📦',
    code: `classDiagram
    class Vehiculo {
        +String marca
        +String modelo
        +encender() void
    }
    class Auto {
        +int puertas
        +abrirMaletera() void
    }
    Vehiculo <|-- Auto : Hereda`
  },
  {
    id: 'pie',
    name: 'Gráfico Circular',
    desc: 'Visualización de datos en porcentajes',
    icon: '🍕',
    code: `pie title Distribución de Costos del Proyecto
    "Desarrollo Frontend" : 40
    "Backend & APIs" : 30
    "DevOps & Deploy" : 15
    "Pruebas & QA" : 15`
  },
  {
    id: 'timeline',
    name: 'Línea de Tiempo',
    desc: 'Cronograma de hitos y eventos en secuencia',
    icon: '📅',
    code: `timeline
    title Cronograma de Hitos 2026
    section Q1
        Planificación : Análisis de requisitos : Diseño inicial
    section Q2
        Desarrollo : Integración de APIs : Pruebas internas
    section Q3
        Lanzamiento Beta : Correcciones : Lanzamiento Oficial`
  },
  {
    id: 'sequence',
    name: 'Diagrama de Secuencia',
    desc: 'Visualización de interacción y flujo de mensajes entre objetos',
    icon: '⚡',
    code: `sequenceDiagram
    title Flujo de Autenticación
    Cliente->>API: POST /login
    API->>BaseDatos: Consultar usuario
    BaseDatos-->>API: Retornar registro
    API-->>Cliente: Retornar JWT token`
  },
  {
    id: 'architecture',
    name: 'Arquitectura Cloud',
    desc: 'Documentación de componentes e infraestructura',
    icon: '☁️',
    code: `architecture-beta
    group api(logos:aws-apigateway)[API Gateway]
    service db(logos:aws-rds)[Base de Datos] in api
    service web(logos:aws-ec2)[Servidor Web] in api
    
    db:L -- R:web`
  },
  {
    id: 'venn',
    name: 'Diagrama de Venn',
    desc: 'Intersección y relación de conjuntos (simulado)',
    icon: '⚪',
    code: `%% Diagrama de Venn (Simulación con Clases)
classDiagram
    class Conjunto_A {
        +Solo_en_A
    }
    class Interseccion {
        +Comun_A_y_B
    }
    class Conjunto_B {
        +Solo_en_B
    }
    note "Nota: Los diagramas de Venn se pueden simular con relaciones de clases."`
  },
  {
    id: 'ishikawa',
    name: 'Causa-Efecto (Ishikawa)',
    desc: 'Análisis de causas raíz (espina de pescado)',
    icon: '🐟',
    code: `graph LR
    Efecto[Problema: Retraso en Entrega]
    
    %% Categorías Principales
    Personal --> Efecto
    Procesos --> Efecto
    Tecnologia --> Efecto
    
    %% Subcausas Personal
    FaltaCapacitacion[Falta de Capacitación] --> Personal
    Sobrecarga[Sobrecarga Laboral] --> Personal
    
    %% Subcausas Procesos
    Burocracia[Aprobaciones Lentas] --> Procesos
    FaltaEstandar[Falta de Estándar] --> Procesos
    
    %% Subcausas Tecnología
    ServidoresLentos[Servidores Lentos] --> Tecnologia
    HerramientasObsoletas[Herramientas Obsoletas] --> Tecnologia`
  },
  {
    id: 'treeview',
    name: 'TreeView (Estructura)',
    desc: 'Jerarquía de carpetas y archivos',
    icon: '🌳',
    code: `mindmap
    root((Proyecto))
      Carpeta_A
        Archivo_A1
        Archivo_A2
      Carpeta_B
        Archivo_B1
        Subcarpeta_B2
          Archivo_B2_1`
  },
  {
    id: 'sequence',
    name: 'Secuencia',
    desc: 'Flujo de mensajes entre sistemas y actores',
    icon: '🔄',
    code: `sequenceDiagram
    actor Usuario
    participant Servidor
    Usuario->>Servidor: Petición de datos
    Servidor-->>Usuario: Respuesta HTTP 200`
  },
  {
    id: 'mindmap',
    name: 'Mapa Mental',
    desc: 'Lluvia de ideas y conceptos jerárquicos',
    icon: '🧠',
    code: `mindmap
    root((Mi Idea))
      Desarrollo
        Frontend
        Backend
      Diseño
        Mockups
        Logo`
  },
  {
    id: 'erd',
    name: 'Entidad Relación (ERD)',
    desc: 'Modelado de bases de datos relacionales',
    icon: '🗄️',
    code: `erDiagram
    CLIENTE ||--o{ ORDEN : realiza
    CLIENTE {
        int id PK
        string nombre
    }
    ORDEN {
        int id PK
        float total
    }`
  },
  {
    id: 'state',
    name: 'Estados',
    desc: 'Transiciones de estado en sistemas reactivos',
    icon: '🚥',
    code: `stateDiagram-v2
    [*] --> Apagado
    Apagado --> Encendido : Presionar botón
    Encendido --> Apagado : Presionar botón`
  },
  {
    id: 'gantt',
    name: 'Cronograma (Gantt)',
    desc: 'Planificación de tareas en el tiempo',
    icon: '📅',
    code: `gantt
    title Plan de Desarrollo
    dateFormat YYYY-MM-DD
    section Desarrollo
    Fase 1 :active, 2026-06-01, 10d
    Fase 2 : 2026-06-11, 12d`
  }
];

window.mermaidSelectedText = "";
window.chosenTemplateId = "flowchart";

function openMermaidModal() {
  if (!editorInstance) {
    alert("No hay un editor Markdown activo. Abre un archivo .md primero.");
    return;
  }

  if (typeof switchView === 'function') {
    switchView('editor-view');
  }
  
  // Detect selected text in the editor
  var selectedText = (typeof editorInstance.getSelectedText === 'function') ? editorInstance.getSelectedText() || "" : "";
  var cleanText = selectedText.trim();
  
  // Check if it is an existing diagram to edit
  var isExistingMermaid = cleanText.includes("```mermaid") || 
      cleanText.startsWith("flowchart") || 
      cleanText.startsWith("graph") || 
      cleanText.startsWith("sequenceDiagram") ||
      cleanText.startsWith("classDiagram") ||
      cleanText.startsWith("erDiagram") ||
      cleanText.startsWith("mindmap") ||
      cleanText.startsWith("gantt") ||
      cleanText.startsWith("timeline") ||
      cleanText.startsWith("pie") ||
      cleanText.startsWith("sequence") ||
      cleanText.startsWith("zenuml") ||
      cleanText.startsWith("architecture-beta");
      
  if (isExistingMermaid) {
    var initialCode = "";
    if (selectedText.includes("```mermaid")) {
      var match = selectedText.match(/```mermaid\s*([\s\S]*?)\s*```/);
      initialCode = match ? match[1].trim() : selectedText.replace(/```mermaid/g, "").replace(/```/g, "").trim();
    } else {
      initialCode = cleanText;
    }
    // Set template ID according to code to prevent undefined template types
    window.chosenTemplateId = detectTemplateIdFromCode(initialCode);
    // Open canvas editor directly without showing selector
    openCanvasWithCode(initialCode);
    return;
  }
  
  // Otherwise, show selector dialog
  var selector = document.getElementById('diagramSelectorModal');
  if (!selector) return;
  
  window.chosenTemplateId = 'flowchart'; // Set default template ID
  selector.classList.remove('hidden');
  window.mermaidSelectedText = cleanText;
  
  // Setup the selector template grid
  setupSelectorTemplates();
  
  // Reset confirmation box and loader
  var aiConfirmBox = document.getElementById('selector-ai-confirm-box');
  var aiLoading = document.getElementById('selector-ai-loading');
  if (aiConfirmBox) aiConfirmBox.classList.add('hidden');
  if (aiLoading) aiLoading.classList.add('hidden');

  // If there is selected text, auto-show the AI confirmation box for the default template
  if (cleanText && aiConfirmBox) {
    aiConfirmBox.classList.remove('hidden');
    var previewText = cleanText;
    if (previewText.length > 150) {
      previewText = previewText.substring(0, 150) + "...";
    }
    var previewEl = document.getElementById('selector-ai-text-preview');
    if (previewEl) {
      previewEl.textContent = 'Texto: "' + previewText + '"';
    }
    var insInput = document.getElementById('selector-ai-instructions');
    if (insInput) {
      insInput.value = "";
      insInput.focus();
    }
  }
}

function closeDiagramSelectorModal() {
  var selector = document.getElementById('diagramSelectorModal');
  if (selector) selector.classList.add('hidden');
}

function setupSelectorTemplates() {
  var grid = document.getElementById('selector-templates-grid');
  if (!grid) return;
  
  grid.innerHTML = '';
  mermaidTemplates.forEach(function(tpl) {
    var card = document.createElement('button');
    card.className = 'template-card' + (tpl.id === window.chosenTemplateId ? ' active' : '');
    
    card.innerHTML = `
      <div class="template-icon" style="font-size: 20px; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
        ${tpl.icon}
      </div>
      <div class="template-details" style="display: flex; flex-direction: column; align-items: flex-start; text-align: left; min-width: 0;">
        <span class="template-name" style="font-weight: 600; font-size: 13px; color: var(--modal-text-primary);">${tpl.name}</span>
        <span class="template-desc" style="font-size: 11px; color: var(--modal-text-secondary); text-align: left;">${tpl.desc}</span>
      </div>
    `;
    
    card.addEventListener('click', function() {
      // Highlight selection
      document.querySelectorAll('#selector-templates-grid .template-card').forEach(function(el) {
        el.classList.remove('active');
      });
      card.classList.add('active');
      window.chosenTemplateId = tpl.id;
      
      // Check if there is selected text to structure with IA
      var aiConfirmBox = document.getElementById('selector-ai-confirm-box');
      if (window.mermaidSelectedText) {
        if (aiConfirmBox) {
          aiConfirmBox.classList.remove('hidden');
          var previewText = window.mermaidSelectedText;
          if (previewText.length > 150) {
            previewText = previewText.substring(0, 150) + "...";
          }
          var previewEl = document.getElementById('selector-ai-text-preview');
          if (previewEl) {
            previewEl.textContent = 'Texto: "' + previewText + '"';
          }
          
          // Focus instruction input
          var insInput = document.getElementById('selector-ai-instructions');
          if (insInput) {
            insInput.value = "";
            insInput.focus();
          }
        }
      } else {
        // No selected text, open canvas modal directly with this template code
        openCanvasWithCode(tpl.code);
      }
    });
    
    grid.appendChild(card);
  });
}

function openCanvasWithCode(code) {
  closeDiagramSelectorModal();
  
  var modal = document.getElementById('mermaidModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  
  // Reset tab to manual
  switchCanvasTab('manual');
  
  // Setup mouse/key listeners on the selectable text area
  var noteArea = document.getElementById('canvas-note-text-area');
  if (noteArea && !noteArea._selectionEventsSetup) {
    noteArea._selectionEventsSetup = true;
    noteArea.addEventListener('mouseup', checkCanvasTextSelection);
    noteArea.addEventListener('keyup', checkCanvasTextSelection);
    noteArea.addEventListener('touchend', checkCanvasTextSelection);
  }
  
  var codeEditor = document.getElementById('modal-code-editor');
  if (codeEditor) {
    codeEditor.value = code;
    updateModalLineNumbers();
    setTimeout(function() {
      renderModalDiagram();
      codeEditor.focus();
    }, 150);
  }
}

function switchCanvasTab(tab) {
  var tabManual = document.getElementById('canvas-tab-manual');
  var tabAi = document.getElementById('canvas-tab-ai');
  var paneManual = document.getElementById('canvas-pane-manual');
  var paneAi = document.getElementById('canvas-pane-ai');

  if (!tabManual || !tabAi || !paneManual || !paneAi) return;

  if (tab === 'manual') {
    tabManual.classList.add('active');
    tabManual.style.borderBottomColor = 'var(--modal-primary)';
    tabManual.style.color = 'var(--modal-text-primary)';
    
    tabAi.classList.remove('active');
    tabAi.style.borderBottomColor = 'transparent';
    tabAi.style.color = 'var(--modal-text-secondary)';
    
    paneManual.classList.remove('hidden');
    paneAi.classList.add('hidden');
  } else if (tab === 'ai') {
    tabAi.classList.add('active');
    tabAi.style.borderBottomColor = 'var(--modal-primary)';
    tabAi.style.color = 'var(--modal-text-primary)';
    
    tabManual.classList.remove('active');
    tabManual.style.borderBottomColor = 'transparent';
    tabManual.style.color = 'var(--modal-text-secondary)';
    
    paneAi.classList.remove('hidden');
    paneManual.classList.add('hidden');
    
    // Load current note text into the selectable container
    var noteTextContainer = document.getElementById('canvas-note-text-area');
    if (noteTextContainer && typeof editorInstance !== 'undefined' && editorInstance) {
      var currentMd = editorInstance.getMarkdown();
      noteTextContainer.textContent = currentMd ? currentMd.trim() : "(La nota actual está vacía o no tiene texto)";
    }
    
    // Hide the action box until selection is made
    var actionBox = document.getElementById('canvas-ai-action-box');
    if (actionBox) actionBox.classList.add('hidden');
    
    // Reset selection instruction input
    var insInput = document.getElementById('canvas-ai-instructions');
    if (insInput) insInput.value = '';
    
    window.canvasActiveSelectedText = '';
  }
}

function checkCanvasTextSelection() {
  var container = document.getElementById('canvas-note-text-area');
  var actionBox = document.getElementById('canvas-ai-action-box');
  var previewEl = document.getElementById('canvas-ai-selection-preview');
  if (!container || !actionBox) return;

  var sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
    actionBox.classList.add('hidden');
    return;
  }

  var range = sel.getRangeAt(0);
  if (container.contains(range.commonAncestorContainer)) {
    var selectedText = sel.toString().trim();
    if (selectedText.length > 0) {
      window.canvasActiveSelectedText = selectedText;
      if (previewEl) {
        var display = selectedText;
        if (display.length > 150) {
          display = display.substring(0, 150) + "...";
        }
        previewEl.textContent = display;
      }
      actionBox.classList.remove('hidden');
    } else {
      actionBox.classList.add('hidden');
    }
  } else {
    actionBox.classList.add('hidden');
  }
}

function triggerCanvasSelectionDiagramming() {
  var insInput = document.getElementById('canvas-ai-instructions');
  var instructions = insInput ? insInput.value.trim() : '';
  var selectedText = window.canvasActiveSelectedText || '';
  if (!selectedText && editorInstance && typeof editorInstance.getSelectedText === 'function') {
    selectedText = editorInstance.getSelectedText() || '';
  }
  var diagramType = window.chosenTemplateId || 'flowchart';
  
  var aiLoading = document.getElementById('canvas-ai-loading');
  var actionBox = document.getElementById('canvas-ai-action-box');
  
  if (aiLoading) aiLoading.classList.remove('hidden');
  if (actionBox) actionBox.classList.add('hidden');
  
  if (bridge && typeof bridge.generateMermaidDiagram === "function") {
    window.activeMermaidCallback = function(resJson) {
      if (aiLoading) aiLoading.classList.add('hidden');
      
      try {
        var data = JSON.parse(resJson);
        if (data.success) {
          var codeEditor = document.getElementById('modal-code-editor');
          if (codeEditor) {
            codeEditor.value = data.code;
            updateModalLineNumbers();
          }
          renderModalDiagram();
          switchCanvasTab('manual');
        } else {
          alert("Error de DeepSeek: " + data.error);
          if (actionBox) actionBox.classList.remove('hidden');
        }
      } catch(e) {
        alert("Error procesando respuesta de DeepSeek: " + e);
        if (actionBox) actionBox.classList.remove('hidden');
      }
    };
    var styleEl = document.getElementById('canvas-ai-style');
    var styleOption = styleEl ? styleEl.value : '';
    bridge.generateMermaidDiagram(instructions, selectedText, diagramType, styleOption);
  } else {
    if (aiLoading) aiLoading.classList.add('hidden');
    alert("Función de IA no disponible en el bridge de Python.");
    if (actionBox) actionBox.classList.remove('hidden');
  }

}

function proceedWithTemplateWithoutAi() {
  var tpl = mermaidTemplates.find(function(t) { return t.id === window.chosenTemplateId; });
  var code = tpl ? tpl.code : mermaidTemplates[0].code;
  openCanvasWithCode(code);
}

function proceedWithAi() {
  var prompt = document.getElementById('selector-ai-instructions').value;
  var selectedText = window.mermaidSelectedText;
  var diagramType = window.chosenTemplateId;
  
  var aiLoading = document.getElementById('selector-ai-loading');
  var aiConfirmBox = document.getElementById('selector-ai-confirm-box');
  
  if (aiLoading) aiLoading.classList.remove('hidden');
  if (aiConfirmBox) aiConfirmBox.classList.add('hidden');
  
  if (bridge && typeof bridge.generateMermaidDiagram === "function") {
    window.activeMermaidCallback = function(resJson) {
      if (aiLoading) aiLoading.classList.add('hidden');
      
      try {
        var data = JSON.parse(resJson);
        if (data.success) {
          openCanvasWithCode(data.code);
        } else {
          alert("Error de DeepSeek: " + data.error);
          if (aiConfirmBox) aiConfirmBox.classList.remove('hidden');
        }
      } catch(e) {
        alert("Error procesando respuesta de DeepSeek: " + e);
        if (aiConfirmBox) aiConfirmBox.classList.remove('hidden');
      }
    };
    var styleEl = document.getElementById('selector-ai-style');
    var styleOption = styleEl ? styleEl.value : '';
    bridge.generateMermaidDiagram(prompt, selectedText, diagramType, styleOption);
  } else {
    if (aiLoading) aiLoading.classList.add('hidden');
    alert("Función de IA no disponible en el bridge de Python.");
    if (aiConfirmBox) aiConfirmBox.classList.remove('hidden');
  }
}

function closeMermaidModal() {
  var modal = document.getElementById('mermaidModal');
  if (modal) {
    modal.classList.add('hidden');
  }
  if (panZoomInstance) {
    try { panZoomInstance.destroy(); } catch(e) {}
    panZoomInstance = null;
  }
}

function renderModalDiagram() {
  if (typeof mermaid === 'undefined') return;
  
  var codeEditor = document.getElementById('modal-code-editor');
  var renderTarget = document.getElementById('modal-mermaid-render-target');
  var errorBanner = document.getElementById('modal-error-banner');
  var errorMessage = document.getElementById('modal-error-message');
  
  if (!codeEditor || !renderTarget) return;
  
  var code = codeEditor.value.trim();
  if (!code) {
    renderTarget.innerHTML = '<p style="color: var(--modal-text-muted); font-size: 13px; padding: 20px; text-align: center;">Escribe código para ver el renderizado.</p>';
    return;
  }

  var renderId = 'mermaid-modal-render-' + Math.floor(Math.random() * 1000000);
  
  try {
    if (errorBanner) errorBanner.classList.add('hidden');
    mermaid.render(renderId, code, renderTarget).then(function(result) {
      renderTarget.innerHTML = result.svg;
      initializeModalPanZoom();
    }).catch(function(error) {
      console.error('Error render modal promise:', error);
      if (errorBanner) errorBanner.classList.remove('hidden');
      if (errorMessage) errorMessage.textContent = error.message || error.toString();
    });
  } catch (error) {
    console.error('Error render modal sync:', error);
    if (errorBanner) errorBanner.classList.remove('hidden');
    if (errorMessage) errorMessage.textContent = error.message || error.toString();
  }
}

function updateModalLineNumbers() {
  var codeEditor = document.getElementById('modal-code-editor');
  var lineNumbers = document.getElementById('modal-line-numbers');
  if (!codeEditor || !lineNumbers) return;
  
  var lines = codeEditor.value.split('\n');
  var lineCount = lines.length;
  var numbersHtml = '';
  for (var i = 1; i <= lineCount; i++) {
    numbersHtml += `<div>${i}</div>`;
  }
  lineNumbers.innerHTML = numbersHtml;
}

function initializeModalPanZoom() {
  var renderTarget = document.getElementById('modal-mermaid-render-target');
  if (!renderTarget) return;
  
  if (panZoomInstance) {
    try { panZoomInstance.destroy(); } catch(e) {}
    panZoomInstance = null;
  }
  
  var svgElement = renderTarget.querySelector('svg');
  if (svgElement && typeof svgPanZoom !== 'undefined') {
    svgElement.setAttribute('width', '100%');
    svgElement.setAttribute('height', '100%');
    svgElement.style.maxWidth = '100%';
    svgElement.style.maxHeight = '100%';
    
    try {
      panZoomInstance = svgPanZoom(svgElement, {
        zoomEnabled: true,
        controlIconsEnabled: false,
        fit: true,
        center: true,
        minZoom: 0.05,
        maxZoom: 15,
        zoomScaleSensitivity: 0.15
      });
    } catch(e) {
      console.warn("svgPanZoom initialization failed:", e);
    }
  }
}

function triggerModalDelayedRender() {
  if (renderTimeout) clearTimeout(renderTimeout);
  renderTimeout = setTimeout(function() {
    renderModalDiagram();
  }, 800);
}

// Hook up modal events
document.addEventListener('DOMContentLoaded', function() {
  checkAuthStatus();

  // Collapse sidebar by default on mobile
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById("sidebar");
    if (sidebar) sidebar.classList.add("collapsed");
  }

  var btnInsert = document.getElementById('btn-modal-insert');
  var btnCancel = document.getElementById('btn-modal-cancel');
  var codeEditor = document.getElementById('modal-code-editor');
  var lineNumbers = document.getElementById('modal-line-numbers');
  
  if (btnInsert) {
    btnInsert.addEventListener('click', function() {
      if (!codeEditor || !editorInstance) {
        alert("No hay un editor Markdown activo para insertar el diagrama.");
        return;
      }
      var code = codeEditor.value;
      var markdownBlock = `\n\`\`\`mermaid\n${code}\n\`\`\`\n`;
      if (typeof editorInstance.insertText === 'function') {
        editorInstance.insertText(markdownBlock);
      } else if (typeof editorInstance.replaceSelection === 'function') {
        editorInstance.replaceSelection(markdownBlock);
      } else if (typeof editorInstance.getMarkdown === 'function' && typeof editorInstance.setMarkdown === 'function') {
        editorInstance.setMarkdown((editorInstance.getMarkdown() || "") + markdownBlock);
      }
      if (typeof editorInstance.focus === 'function') {
        editorInstance.focus();
      }
      closeMermaidModal();
    });
  }
  
  if (btnCancel) {
    btnCancel.addEventListener('click', closeMermaidModal);
  }
  
  if (codeEditor) {
    codeEditor.addEventListener('scroll', function() {
      if (lineNumbers) lineNumbers.scrollTop = codeEditor.scrollTop;
    });
    
    codeEditor.addEventListener('input', function() {
      updateModalLineNumbers();
      triggerModalDelayedRender();
    });
    
    codeEditor.addEventListener('keydown', function(e) {
      if (e.key === 'Tab') {
        e.preventDefault();
        var start = codeEditor.selectionStart;
        var end = codeEditor.selectionEnd;
        
        codeEditor.value = codeEditor.value.substring(0, start) + "    " + codeEditor.value.substring(end);
        codeEditor.selectionStart = codeEditor.selectionEnd = start + 4;
        
        updateModalLineNumbers();
        triggerModalDelayedRender();
      }
      
      if (e.ctrlKey && e.key === 'Enter') {
        e.preventDefault();
        renderModalDiagram();
      }
    });
  }
  
  var zoomIn = document.getElementById('btn-modal-zoom-in');
  var zoomOut = document.getElementById('btn-modal-zoom-out');
  var zoomReset = document.getElementById('btn-modal-zoom-reset');
  
  if (zoomIn) {
    zoomIn.addEventListener('click', function() {
      if (panZoomInstance) panZoomInstance.zoomIn();
    });
  }
  if (zoomOut) {
    zoomOut.addEventListener('click', function() {
      if (panZoomInstance) panZoomInstance.zoomOut();
    });
  }
  if (zoomReset) {
    zoomReset.addEventListener('click', function() {
      if (panZoomInstance) {
        panZoomInstance.resetZoom();
        panZoomInstance.center();
      }
    });
  }
});

function triggerAiGeneration() {
  var promptInput = document.getElementById('modal-ai-instructions');
  var prompt = promptInput ? promptInput.value.trim() : "";
  var selectedText = window.mermaidSelectedText || "";
  var diagramType = window.chosenTemplateId || 'flowchart';
  
  var statusEl = document.getElementById('modal-ai-status');
  var btnGenerate = document.getElementById('btn-modal-ai-generate');
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.textContent = '⏳ Estructurando información con DeepSeek IA...';
  }
  if (btnGenerate) btnGenerate.disabled = true;
  
  if (bridge && typeof bridge.generateMermaidDiagram === "function") {
    window.activeMermaidCallback = function(resJson) {
      if (statusEl) statusEl.style.display = 'none';
      if (btnGenerate) btnGenerate.disabled = false;
      
      try {
        var data = JSON.parse(resJson);
        if (data.success) {
          var codeEditor = document.getElementById('modal-code-editor');
          if (codeEditor) {
            codeEditor.value = data.code;
            updateModalLineNumbers();
            renderModalDiagram();
          }
        } else {
          alert("Error de DeepSeek: " + data.error);
        }
      } catch(e) {
        alert("Error procesando respuesta de DeepSeek: " + e);
      }
    };
    var styleEl = document.getElementById('modal-ai-style');
    var styleOption = styleEl ? styleEl.value : '';
    bridge.generateMermaidDiagram(prompt, selectedText, diagramType, styleOption);
  } else {
    if (statusEl) statusEl.style.display = 'none';
    if (btnGenerate) btnGenerate.disabled = false;
    alert("Función de IA no disponible en el bridge de Python.");
  }
}

/* ==========================================================================
   Sidebar Tabs & Chatbot Panel Logic
   ========================================================================== */

function switchSidebarTab(tabName) {
  // Remove active class from all tabs and content panes
  document.querySelectorAll('.sidebar-tab-btn').forEach(function(btn) {
    btn.classList.remove('active');
  });
  document.querySelectorAll('.sidebar-content-pane').forEach(function(pane) {
    pane.classList.remove('active');
  });
  
  // Add active class to selected elements
  if (tabName === 'explorer') {
    var tabExp = document.getElementById('tab-explorer');
    var paneExp = document.getElementById('sidebar-explorer-pane');
    if (tabExp) tabExp.classList.add('active');
    if (paneExp) paneExp.classList.add('active');
  } else if (tabName === 'chatbot') {
    var tabChat = document.getElementById('tab-chatbot');
    var paneChat = document.getElementById('sidebar-chatbot-pane');
    if (tabChat) tabChat.classList.add('active');
    if (paneChat) paneChat.classList.add('active');
    // Auto focus chatbot input
    var input = document.getElementById('chatbot-input');
    if (input) input.focus();
  }
}

var chatbotHistory = [];
var jefaturaHistory = [];
var currentChatbotMode = 'general';

function switchChatbotMode(mode) {
  if (currentChatbotMode === mode) return;
  currentChatbotMode = mode;
  
  // Actualizar clases de pestañas activas
  var tabGeneral = document.getElementById('chat-tab-general');
  var tabJefatura = document.getElementById('chat-tab-jefatura');
  if (tabGeneral) tabGeneral.classList.toggle('active', mode === 'general');
  if (tabJefatura) tabJefatura.classList.toggle('active', mode === 'jefatura');
  
  // Limpiar contenedor de mensajes
  var container = document.getElementById('chatbot-messages-container');
  if (container) {
    container.innerHTML = '';
    
    // Repopular mensajes
    var historyToUse = mode === 'general' ? chatbotHistory : jefaturaHistory;
    
    if (historyToUse.length === 0) {
      // Mensaje de bienvenida inicial
      var welcomeEl = document.createElement('div');
      welcomeEl.className = 'chatbot-msg system';
      if (mode === 'general') {
        welcomeEl.innerHTML = '🤖 Hola. Soy tu Asistente de IA general. Puedes preguntarme dudas, pedirme que estructure notas, cree mapas o explique temas.';
      } else {
        welcomeEl.innerHTML = '💼 Bienvenido al Redactor Profesional. Escribe aquí el borrador o mensaje informal que deseas enviarle a tu jefatura o supervisor, y te ayudaré a reestructurarlo con un tono formal, claro y profesional.';
      }
      container.appendChild(welcomeEl);
    } else {
      // Re-renderizar historial guardado
      historyToUse.forEach(function(msg) {
        appendChatbotMessage(msg.role, msg.content);
      });
    }
    container.scrollTop = container.scrollHeight;
  }
  
  // Cambiar placeholder del input
  var inputEl = document.getElementById('chatbot-input');
  if (inputEl) {
    if (mode === 'general') {
      inputEl.placeholder = 'Pregunta algo al Asistente de IA...';
    } else {
      inputEl.placeholder = 'Escribe el borrador del mensaje informal para tu jefe...';
    }
  }
}

function appendChatbotMessage(sender, text) {
  var container = document.getElementById('chatbot-messages-container');
  if (!container) return;
  
  var msgEl = document.createElement('div');
  msgEl.className = 'chatbot-msg ' + sender;
  
  if (sender === 'system') {
    msgEl.innerHTML = text;
  } else {
    msgEl.innerHTML = parseMarkdownToHtml(text);
  }
  
  container.appendChild(msgEl);
  container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
  var map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
  };
  return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}

function sendChatbotMessage() {
  var inputEl = document.getElementById('chatbot-input');
  var btnSend = document.getElementById('btn-chatbot-send');
  if (!inputEl || !btnSend) return;
  
  var text = inputEl.value.trim();
  if (!text) return;
  
  // Clear input
  inputEl.value = '';
  
  // Append user message
  appendChatbotMessage('user', text);
  
  // Add to history
  var historyToUse = currentChatbotMode === 'general' ? chatbotHistory : jefaturaHistory;
  historyToUse.push({ role: 'user', content: text });
  
  // Disable elements and show loading indicator
  btnSend.disabled = true;
  inputEl.disabled = true;
  
  // Append loading bubble
  var loadingId = 'chatbot-loading-' + Math.floor(Math.random() * 1000000);
  var container = document.getElementById('chatbot-messages-container');
  if (container) {
    var loadEl = document.createElement('div');
    loadEl.className = 'chatbot-msg assistant';
    loadEl.id = loadingId;
    loadEl.innerHTML = '<div class="chatbot-loading-dots"><span></span><span></span><span></span></div>';
    container.appendChild(loadEl);
    container.scrollTop = container.scrollHeight;
  }
  
  if (bridge && typeof bridge.chatWithDeepseek === 'function') {
    window.activeChatCallback = function(resJson) {
      // Remove loading bubble
      var loadBubble = document.getElementById(loadingId);
      if (loadBubble) loadBubble.remove();
      
      btnSend.disabled = false;
      inputEl.disabled = false;
      inputEl.focus();
      
      try {
        var data = JSON.parse(resJson);
        if (data.success) {
          appendChatbotMessage('assistant', data.reply);
          var historyToUseNow = currentChatbotMode === 'general' ? chatbotHistory : jefaturaHistory;
          historyToUseNow.push({ role: 'assistant', content: data.reply });
        } else {
          appendChatbotMessage('system', '⚠️ Error: ' + escapeHtml(data.error));
        }
      } catch(e) {
        appendChatbotMessage('system', '⚠️ Error parsing response: ' + escapeHtml(e.toString()));
      }
    };
    bridge.chatWithDeepseek(JSON.stringify(historyToUse), text, currentChatbotMode);
  } else {
    var loadBubble = document.getElementById(loadingId);
    if (loadBubble) loadBubble.remove();
    
    btnSend.disabled = false;
    inputEl.disabled = false;
    appendChatbotMessage('system', '⚠️ El bridge de Python no está disponible.');
  }
}

function handleChatbotKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendChatbotMessage();
  }
}

function explainCodeBlock(lang, code) {
  // Ensure sidebar is open
  var sidebar = document.getElementById("sidebar");
  if (sidebar && sidebar.classList.contains("collapsed")) {
    toggleSidebar();
  }
  // Switch to chatbot tab
  switchSidebarTab('chatbot');
  
  // Set chatbot input text
  var inputEl = document.getElementById('chatbot-input');
  if (inputEl) {
    inputEl.value = "Por favor, documenta y explica detalladamente este código en " + lang.toUpperCase() + ":\n\n```" + lang + "\n" + code + "\n```";
    sendChatbotMessage();
  }
}

function openAiCopilotModal() {
  if (!editorInstance) return;
  var selectedText = editorInstance.getSelectedText() || "";
  var previewEl = document.getElementById('copilot-text-preview');
  if (previewEl) {
    previewEl.textContent = selectedText.trim() ? selectedText.trim() : "(sin texto seleccionado — por favor, selecciona un fragmento en el editor)";
  }
  
  var modal = document.getElementById('aiCopilotModal');
  if (modal) {
    modal.classList.remove('hidden');
  }
}

function closeAiCopilotModal() {
  var modal = document.getElementById('aiCopilotModal');
  if (modal) {
    modal.classList.add('hidden');
  }
  var statusEl = document.getElementById('copilot-status');
  if (statusEl) statusEl.style.display = 'none';
}

function runCopilotAction(action) {
  if (!editorInstance) return;
  var selectedText = editorInstance.getSelectedText() || "";
  if (!selectedText.trim()) {
    alert("Por favor, selecciona primero un fragmento de texto en el editor.");
    return;
  }
  
  var statusEl = document.getElementById('copilot-status');
  if (statusEl) {
    statusEl.style.display = 'block';
  }
  
  if (bridge && typeof bridge.runCopilotAction === 'function') {
    window.activeCopilotCallback = function(resJson) {
      if (statusEl) statusEl.style.display = 'none';
      closeAiCopilotModal();
      
      try {
        var data = JSON.parse(resJson);
        if (data.success) {
          editorInstance.replaceSelection(data.result);
          showStatus("Texto procesado por IA Copilot con éxito.");
        } else {
          alert("Error de DeepSeek: " + data.error);
        }
      } catch(e) {
        alert("Error procesando respuesta de DeepSeek: " + e.toString());
      }
    };
    bridge.runCopilotAction(action, selectedText);
  } else {
    if (statusEl) statusEl.style.display = 'none';
    alert("El Copilot de IA no está disponible en el bridge de Python.");
  }
}

/* ========== Remote Source Selector Filter ========== */
function setRemoteSourceFilter(source) {
  if (bridge && typeof bridge.setActiveRemoteSource === 'function') {
    bridge.setActiveRemoteSource(source);
    updateSourceFilterButtons(source);
  }
}

function updateSourceFilterButtons(source) {
  document.querySelectorAll('.explorer-source-btn').forEach(function(btn) {
    btn.classList.remove('active');
  });
  var activeBtn = document.getElementById('src-tab-' + source);
  if (activeBtn) {
    activeBtn.classList.add('active');
  }
}

/* ========== Visualizer and Office Editor JS Helpers ========== */

function openOfficeEditorDirect(path, name, suffix, source) {
  console.log("openOfficeEditor: path =", path, "suffix =", suffix);
  showStatus("Cargando documento de oficina: " + name);
  switchView("editor-view");
  ensureEditorModeControls();
  currentFilePath = path;
  currentFileSource = source || "local";
  isOfficeActive = true;
  hasUncommittedChanges = false;
  
  // Update name and source tags
  setEl("editor-name", "📝 " + name);
  var sourceTag = document.getElementById("editor-source");
  if (source && source !== "local") {
    sourceTag.style.display = "inline";
    sourceTag.textContent = source === "drive" ? "☁️ Drive" : "🐙 GitHub";
  } else {
    sourceTag.style.display = "none";
  }
  setEl("editor-meta", "");
  
  var commitBtn = document.getElementById("btn-commit-file");
  if (commitBtn) {
    commitBtn.style.display = (source && source !== "local") ? "inline" : "none";
  }
  
  var modeBtn = document.getElementById("btn-editor-mode");
  if (modeBtn) modeBtn.style.display = "none"; // Hide preview button for office files
  var modeGroup = document.getElementById("editor-mode-group");
  if (modeGroup) modeGroup.style.display = "none";
  
  // Hide all sub-containers
  document.getElementById("markdown-editor-container").style.display = "none";
  document.getElementById("docx-editor-container").style.display = "none";
  document.getElementById("xlsx-editor-container").style.display = "none";
  
  if (editorInstance) {
    try { editorInstance.destroy(); } catch(e) {}
    editorInstance = null;
  }
  
  if (suffix === ".docx" || suffix === ".doc") {
    currentOfficeType = "word";
    ensureDocxToolbar();
    showDocxEditor(true);
    
    const tab = openTabs.find(t => t.path === path);
    if (tab && tab.content && tab.content !== "<p>⏳ Cargando documento...</p>") {
      document.getElementById("docx-editor-container").innerHTML = tab.content;
      document.getElementById("docx-editor-container").oninput = function() {
        hasUncommittedChanges = true;
        tab.uncommitted = true;
        renderTabsBar();
      };
      return;
    }
    
    document.getElementById("docx-editor-container").innerHTML = "<p>⏳ Cargando documento...</p>";
    bridge.getFileBase64(path, function(base64Data) {
      if (!base64Data) {
        document.getElementById("docx-editor-container").innerHTML = "<p>[Documento vacío - Escribe aquí]</p>";
        document.getElementById("docx-editor-container").oninput = function() {
          hasUncommittedChanges = true;
          if (tab) { tab.uncommitted = true; renderTabsBar(); }
        };
        return;
      }
      try {
        var binaryString = atob(base64Data);
        var len = binaryString.length;
        var bytes = new Uint8Array(len);
        for (var i = 0; i < len; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }
        var arrayBuffer = bytes.buffer;
        
        if (typeof mammoth !== 'undefined') {
          mammoth.convertToHtml({arrayBuffer: arrayBuffer})
            .then(function(result) {
              document.getElementById("docx-editor-container").innerHTML = result.value || "<p>[Documento vacío]</p>";
              document.getElementById("docx-editor-container").oninput = function() {
                hasUncommittedChanges = true;
                if (tab) { tab.uncommitted = true; renderTabsBar(); }
              };
              // Cache initial html
              if (tab) tab.content = document.getElementById("docx-editor-container").innerHTML;
            })
            .catch(function(err) {
              document.getElementById("docx-editor-container").innerHTML = "<p>Error al renderizar docx: " + err.message + "</p>";
            });
        } else {
          document.getElementById("docx-editor-container").innerHTML = "<p>Mammoth.js no cargado.</p>";
        }
      } catch(e) {
        document.getElementById("docx-editor-container").innerHTML = "<p>Error de conversión: " + e.message + "</p>";
      }
    });
  } else if (suffix === ".xlsx" || suffix === ".xls" || suffix === ".csv") {
    currentOfficeType = "excel";
    showDocxEditor(false);
    document.getElementById("xlsx-editor-container").style.display = "flex";
    
    const tab = openTabs.find(t => t.path === path);
    if (tab && tab.workbook) {
      currentWorkbook = tab.workbook;
      currentActiveSheetName = tab.activeSheetName || currentWorkbook.SheetNames[0];
      renderXlsxWorkbook();
      return;
    }
    
    document.getElementById("xlsx-grid-area").innerHTML = "<p>⏳ Cargando hoja de cálculo...</p>";
    openXlsxEditor(path, name, source);
  }
}

function getDocxExportHtml() {
  var body = document.getElementById("docx-editor-container").innerHTML || "<p></p>";
  return '<!DOCTYPE html><html><head><meta charset="utf-8">' +
    '<style>' +
    'body{font-family:Calibri,Arial,sans-serif;font-size:11pt;line-height:1.45;color:#111827;}' +
    'h1{font-size:20pt;margin:0 0 12pt 0;} h2{font-size:16pt;margin:14pt 0 8pt 0;}' +
    'p{margin:0 0 8pt 0;} table{border-collapse:collapse;width:100%;margin:8pt 0;}' +
    'td,th{border:1px solid #cbd5e1;padding:5pt;vertical-align:top;} th{background:#f1f5f9;font-weight:bold;}' +
    'ul,ol{margin-top:0;margin-bottom:8pt;}' +
    '</style></head><body>' + body + '</body></html>';
}

function ensureDocxToolbar() {
  var editor = document.getElementById("docx-editor-container");
  if (!editor || document.getElementById("docx-toolbar")) return;

  var toolbar = document.createElement("div");
  toolbar.id = "docx-toolbar";
  toolbar.innerHTML =
    '<button onclick="docxExec(\'bold\')" title="Negrita"><strong>B</strong></button>' +
    '<button onclick="docxExec(\'italic\')" title="Cursiva"><em>I</em></button>' +
    '<button onclick="docxExec(\'underline\')" title="Subrayado"><u>U</u></button>' +
    '<span class="docx-sep"></span>' +
    '<button onclick="applyDocxBlock(\'h1\')" title="Titulo 1">H1</button>' +
    '<button onclick="applyDocxBlock(\'h2\')" title="Titulo 2">H2</button>' +
    '<button onclick="applyDocxBlock(\'p\')" title="Parrafo">P</button>' +
    '<span class="docx-sep"></span>' +
    '<button onclick="docxExec(\'insertUnorderedList\')" title="Lista">Lista</button>' +
    '<button onclick="docxExec(\'insertOrderedList\')" title="Lista numerada">1.</button>' +
    '<button onclick="insertDocxTable()" title="Insertar tabla">Tabla</button>';
  editor.parentNode.insertBefore(toolbar, editor);
  setupDocxEditorEvents();
}

function showDocxEditor(show) {
  var toolbar = document.getElementById("docx-toolbar");
  var editor = document.getElementById("docx-editor-container");
  if (toolbar) toolbar.style.display = show ? "flex" : "none";
  if (editor) editor.style.display = show ? "block" : "none";
}

function setupDocxEditorEvents() {
  var editor = document.getElementById("docx-editor-container");
  if (!editor || editor.dataset.docxReady === "1") return;
  editor.dataset.docxReady = "1";
  editor.addEventListener("input", function() {
    markCurrentTabDirty();
  });
  editor.addEventListener("keydown", function(event) {
    if (event.ctrlKey && event.key.toLowerCase() === "b") {
      event.preventDefault();
      docxExec("bold");
    } else if (event.ctrlKey && event.key.toLowerCase() === "i") {
      event.preventDefault();
      docxExec("italic");
    }
  });
}

function markCurrentTabDirty() {
  hasUncommittedChanges = true;
  var tab = openTabs.find(t => t.path === currentFilePath);
  if (tab) {
    tab.uncommitted = true;
    if (currentOfficeType === "word") {
      var docxEl = document.getElementById("docx-editor-container");
      if (docxEl) tab.content = docxEl.innerHTML;
    }
    renderTabsBar();
  }
}

function docxExec(command) {
  var editor = document.getElementById("docx-editor-container");
  if (!editor) return;
  editor.focus();
  document.execCommand(command, false, null);
  markCurrentTabDirty();
}

function applyDocxBlock(tagName) {
  var editor = document.getElementById("docx-editor-container");
  if (!editor) return;
  editor.focus();
  document.execCommand("formatBlock", false, "<" + tagName + ">");
  markCurrentTabDirty();
}

function insertDocxTable() {
  var editor = document.getElementById("docx-editor-container");
  if (!editor) return;
  editor.focus();
  var html = '<table><tbody><tr><th>Campo</th><th>Detalle</th></tr><tr><td></td><td></td></tr><tr><td></td><td></td></tr></tbody></table><p></p>';
  document.execCommand("insertHTML", false, html);
  markCurrentTabDirty();
}

let currentWorkbook = null;
let currentActiveSheetName = "";

function openXlsxEditor(path, name, source) {
  bridge.getFileBase64(path, function(base64Data) {
    if (!base64Data) {
      var wb = XLSX.utils.book_new();
      var ws = XLSX.utils.aoa_to_sheet([["", "", ""], ["", "", ""], ["", "", ""]]);
      XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
      currentWorkbook = wb;
    } else {
      try {
        currentWorkbook = XLSX.read(base64Data, {type: 'base64'});
      } catch(e) {
        console.error("XLSX read error", e);
        showStatus("Error abriendo Excel");
        return;
      }
    }
    
    // Cache workbook in tab
    const tab = openTabs.find(t => t.path === path);
    if (tab) {
      tab.workbook = currentWorkbook;
      tab.activeSheetName = currentWorkbook.SheetNames[0];
    }
    
    renderXlsxWorkbook();
  });
}

function renderXlsxWorkbook() {
  const tabsBar = document.getElementById("xlsx-tabs-bar");
  tabsBar.innerHTML = "";
  
  const sheetNames = currentWorkbook.SheetNames;
  if (sheetNames.length === 0) return;
  
  currentActiveSheetName = sheetNames[0];
  
  sheetNames.forEach(function(sheetName) {
    const btn = document.createElement("button");
    btn.className = "xlsx-tab-btn" + (sheetName === currentActiveSheetName ? " active" : "");
    btn.textContent = sheetName;
    btn.onclick = function() {
      document.querySelectorAll(".xlsx-tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentActiveSheetName = sheetName;
      renderXlsxGrid(sheetName);
    };
    tabsBar.appendChild(btn);
  });
  
  renderXlsxGrid(currentActiveSheetName);
}

function renderXlsxGrid(sheetName) {
  const gridArea = document.getElementById("xlsx-grid-area");
  gridArea.innerHTML = "";
  
  const ws = currentWorkbook.Sheets[sheetName];
  let range = { s: {r: 0, c: 0}, e: {r: 10, c: 5} };
  if (ws['!ref']) {
    try {
      range = XLSX.utils.decode_range(ws['!ref']);
    } catch(e) {}
  }
  let maxR = Math.max(range.e.r, 20); 
  let maxC = Math.max(range.e.c, 10); 
  
  const table = document.createElement("table");
  table.className = "xlsx-grid-table";
  
  // Header row
  const headerRow = document.createElement("tr");
  const corner = document.createElement("th");
  corner.textContent = "";
  headerRow.appendChild(corner);
  for (let c = 0; c <= maxC; c++) {
    const th = document.createElement("th");
    th.textContent = XLSX.utils.encode_col(c);
    headerRow.appendChild(th);
  }
  table.appendChild(headerRow);
  
  // Data rows
  for (let r = 0; r <= maxR; r++) {
    const tr = document.createElement("tr");
    const rowNumCell = document.createElement("th");
    rowNumCell.textContent = r + 1;
    tr.appendChild(rowNumCell);
    
    for (let c = 0; c <= maxC; c++) {
      const td = document.createElement("td");
      const cellRef = XLSX.utils.encode_cell({r: r, c: c});
      const cellVal = ws[cellRef] ? (ws[cellRef].v !== undefined ? ws[cellRef].v : "") : "";
      
      const input = document.createElement("input");
      input.type = "text";
      input.className = "xlsx-cell-input";
      input.value = cellVal;
      
      input.onfocus = function() {
        document.getElementById("xlsx-active-cell").textContent = cellRef;
      };
      
      input.onchange = function() {
        var val = input.value;
        if (val !== "" && !isNaN(val)) {
          ws[cellRef] = {t: 'n', v: parseFloat(val)};
        } else {
          ws[cellRef] = {t: 's', v: val};
        }
        hasUncommittedChanges = true;
        
        let currentRef = ws['!ref'] || "A1:A1";
        try {
          let curRange = XLSX.utils.decode_range(currentRef);
          if (r > curRange.e.r || c > curRange.e.c) {
            curRange.e.r = Math.max(curRange.e.r, r);
            curRange.e.c = Math.max(curRange.e.c, c);
            ws['!ref'] = XLSX.utils.encode_range(curRange);
          }
        } catch(e) {}
      };
      
      td.appendChild(input);
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
  gridArea.appendChild(table);
}

function xlsxAddRow() {
  if (!currentWorkbook || !currentActiveSheetName) return;
  const ws = currentWorkbook.Sheets[currentActiveSheetName];
  let range = { s: {r: 0, c: 0}, e: {r: 0, c: 0} };
  if (ws['!ref']) {
    try { range = XLSX.utils.decode_range(ws['!ref']); } catch(e) {}
  }
  range.e.r++;
  ws['!ref'] = XLSX.utils.encode_range(range);
  hasUncommittedChanges = true;
  renderXlsxGrid(currentActiveSheetName);
}

function xlsxAddCol() {
  if (!currentWorkbook || !currentActiveSheetName) return;
  const ws = currentWorkbook.Sheets[currentActiveSheetName];
  let range = { s: {r: 0, c: 0}, e: {r: 0, c: 0} };
  if (ws['!ref']) {
    try { range = XLSX.utils.decode_range(ws['!ref']); } catch(e) {}
  }
  range.e.c++;
  ws['!ref'] = XLSX.utils.encode_range(range);
  hasUncommittedChanges = true;
  renderXlsxGrid(currentActiveSheetName);
}

function xlsxDeleteRow() {
  if (!currentWorkbook || !currentActiveSheetName) return;
  const ws = currentWorkbook.Sheets[currentActiveSheetName];
  let range = { s: {r: 0, c: 0}, e: {r: 0, c: 0} };
  if (ws['!ref']) {
    try { range = XLSX.utils.decode_range(ws['!ref']); } catch(e) {}
  }
  if (range.e.r > 0) {
    range.e.r--;
    ws['!ref'] = XLSX.utils.encode_range(range);
    hasUncommittedChanges = true;
    renderXlsxGrid(currentActiveSheetName);
  }
}

function openVisualizerDirect(path, name, type, content, source) {
  console.log("openVisualizer: type =", type, "name =", name);
  switchView("visualizer-view");
  currentFilePath = path;
  currentFileSource = source || "local";
  
  setEl("visualizer-name", "👁️ " + name);
  setEl("visualizer-meta", "Solo lectura · Origen: " + (source === "drive" ? "Google Drive" : source === "github" ? "GitHub" : "Local"));
  
  // Hide all sub-containers
  document.getElementById("vis-pdf-container").style.display = "none";
  document.getElementById("vis-video-container").style.display = "none";
  document.getElementById("vis-image-container").style.display = "none";
  document.getElementById("vis-code-container").style.display = "none";
  document.getElementById("vis-notebook-container").style.display = "none";
  document.getElementById("vis-audio-container").style.display = "none";
  document.getElementById("vis-generic-container").style.display = "none";
  
  const fileUrl = "file:///" + path.replace(/\\/g, "/");
  
  if (type === "pdf") {
    document.getElementById("vis-pdf-container").style.display = "flex";
    renderPdf(fileUrl);
  } else if (type === "video") {
    document.getElementById("vis-video-container").style.display = "flex";
    const player = document.getElementById("vis-video-player");
    player.src = "";
  } else if (type === "image") {
    document.getElementById("vis-image-container").style.display = "flex";
    document.getElementById("vis-image-preview").src = fileUrl;
  } else if (type === "audio") {
    document.getElementById("vis-audio-container").style.display = "flex";
    const player = document.getElementById("vis-audio-player");
    player.src = "";
  } else if (type === "code") {
    document.getElementById("vis-code-container").style.display = "block";
    const codeBlock = document.getElementById("vis-code-block");
    codeBlock.textContent = content || "";
    codeBlock.className = "hljs";
    if (typeof hljs !== 'undefined') {
      hljs.highlightElement(codeBlock);
    }
  } else if (type === "notebook") {
    document.getElementById("vis-notebook-container").style.display = "block";
    renderNotebook(content);
  } else if (type === "generic") {
    document.getElementById("vis-generic-container").style.display = "flex";
    setEl("vis-generic-name", name);
  }
}

function closeVisualizer() {
  const scrollArea = document.getElementById("pdf-scroll-area");
  if (scrollArea) scrollArea.innerHTML = "";
  currentPdfDoc = null;
  document.getElementById("vis-video-player").src = "";
  document.getElementById("vis-image-preview").src = "";
  document.getElementById("vis-audio-player").src = "";
  
  // Close the active tab instead of switching view directly
  closeTab(currentFilePath);
}

function renderNotebook(jsonStr) {
  const container = document.getElementById("vis-notebook-cells");
  container.innerHTML = "";
  
  try {
    const nb = JSON.parse(jsonStr);
    const cells = nb.cells || [];
    
    cells.forEach(function(cell, idx) {
      const cellDiv = document.createElement("div");
      cellDiv.className = "ipynb-cell";
      
      const type = cell.cell_type;
      const sourceLines = cell.source || [];
      const sourceText = Array.isArray(sourceLines) ? sourceLines.join("") : sourceLines;
      
      if (type === "markdown") {
        const prompt = document.createElement("div");
        prompt.className = "ipynb-cell-prompt";
        prompt.textContent = `Markdown [${idx}]`;
        cellDiv.appendChild(prompt);
        
        const mdDiv = document.createElement("div");
        mdDiv.style.color = "var(--text)";
        mdDiv.style.fontSize = "14px";
        mdDiv.style.lineHeight = "1.5";
        
        let html = sourceText
          .replace(/^### (.*$)/gim, '<h3>$1</h3>')
          .replace(/^## (.*$)/gim, '<h2>$1</h2>')
          .replace(/^# (.*$)/gim, '<h1>$1</h1>')
          .replace(/^\- (.*$)/gim, '<ul><li>$1</li></ul>')
          .replace(/^\* (.*$)/gim, '<ul><li>$1</li></ul>')
          .replace(/\*\*(.*)\*\*/gim, '<strong>$1</strong>')
          .replace(/\*(.*)\*/gim, '<em>$1</em>')
          .replace(/\n/g, '<br>');
        mdDiv.innerHTML = html;
        cellDiv.appendChild(mdDiv);
        
      } else if (type === "code") {
        const executionCount = cell.execution_count !== null ? cell.execution_count : " ";
        const prompt = document.createElement("div");
        prompt.className = "ipynb-cell-prompt";
        prompt.textContent = `In [${executionCount}]:`;
        cellDiv.appendChild(prompt);
        
        const codePre = document.createElement("pre");
        codePre.className = "ipynb-input-area";
        const codeBlock = document.createElement("code");
        codeBlock.className = "python hljs";
        codeBlock.textContent = sourceText;
        codePre.appendChild(codePre.firstChild); // Keep correct highlighting target
        codeBlock.textContent = sourceText;
        codePre.innerHTML = "";
        codePre.appendChild(codeBlock);
        cellDiv.appendChild(codePre);
        
        if (typeof hljs !== 'undefined') {
          hljs.highlightElement(codeBlock);
        }
        
        const outputs = cell.outputs || [];
        outputs.forEach(function(out) {
          const outArea = document.createElement("div");
          
          if (out.output_type === "stream") {
            const outText = Array.isArray(out.text) ? out.text.join("") : out.text;
            outArea.className = "ipynb-output-area";
            outArea.textContent = outText;
            cellDiv.appendChild(outArea);
          } else if (out.output_type === "execute_result" || out.output_type === "display_data") {
            const data = out.data || {};
            if (data["image/png"]) {
              const img = document.createElement("img");
              img.src = "data:image/png;base64," + data["image/png"].trim().replace(/\n/g, "");
              img.style.maxWidth = "100%";
              img.style.marginTop = "8px";
              img.style.borderRadius = "var(--radius)";
              cellDiv.appendChild(img);
            } else if (data["text/plain"]) {
              const txt = Array.isArray(data["text/plain"]) ? data["text/plain"].join("") : data["text/plain"];
              outArea.className = "ipynb-output-area";
              outArea.textContent = txt;
              cellDiv.appendChild(outArea);
            }
          }
        });
      }
      
      container.appendChild(cellDiv);
    });
  } catch(e) {
    container.innerHTML = `<p style="color:#dc2626">Error leyendo notebook: ${e.message}</p>`;
  }
}

/* ========== Minimalist Tab System ========== */
let openTabs = [];
let activeTabPath = "";

function addTab(fileInfo) {
  // Save active state of the current tab before switching
  saveActiveTabState();
  
  let existing = openTabs.find(t => t.path === fileInfo.path);
  if (!existing) {
    existing = {
      path: fileInfo.path,
      name: fileInfo.name,
      source: fileInfo.source,
      type: fileInfo.type,
      content: fileInfo.content,
      suffix: fileInfo.suffix || "",
      isOffice: fileInfo.type === "excel" || fileInfo.type === "word",
      uncommitted: false,
      info: fileInfo.info || {}
    };
    openTabs.push(existing);
  }
  
  setActiveTab(existing.path);
}

function saveActiveTabState() {
  if (!activeTabPath) return;
  const tab = openTabs.find(t => t.path === activeTabPath);
  if (!tab) return;
  
  if (tab.type === "markdown" && editorInstance) {
    try {
      tab.content = editorInstance.getMarkdown();
    } catch(e) {}
  } else if (tab.type === "word") {
    const docxEl = document.getElementById("docx-editor-container");
    if (docxEl) {
      tab.content = docxEl.innerHTML;
    }
  } else if (tab.type === "excel") {
    tab.workbook = currentWorkbook;
    tab.activeSheetName = currentActiveSheetName;
  }
}

function setActiveTab(path) {
  if (activeTabPath && activeTabPath !== path) {
    const oldTab = openTabs.find(t => t.path === activeTabPath);
    if (oldTab && oldTab.type === "markdown" && editorDirty && editorInstance) {
      if (saveTimer) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
      var content = editorInstance.getMarkdown();
      bridge.saveFile(oldTab.path, content);
      editorDirty = false;
    }
  }

  // On mobile screens, collapse the sidebar automatically when selecting a tab/file
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById("sidebar");
    if (sidebar) sidebar.classList.add("collapsed");
  }

  saveActiveTabState();
  
  activeTabPath = path;
  openTabs.forEach(t => t.active = (t.path === path));
  
  renderTabsBar();
  
  const tab = openTabs.find(t => t.path === path);
  if (!tab) return;
  
  currentFilePath = tab.path;
  currentFileSource = tab.source || "local";
  
  if (tab.type === "markdown") {
    isOfficeActive = false;
    currentOfficeType = "";
    switchView("editor-view");
    
    document.getElementById("markdown-editor-container").style.display = "block";
    document.getElementById("docx-editor-container").style.display = "none";
    document.getElementById("xlsx-editor-container").style.display = "none";
    
    setEl("editor-name", "📝 " + tab.name);
    updateSourceTag("editor-source", tab.source);
    setEl("editor-meta", "");
    
    var modeBtn = document.getElementById("btn-editor-mode");
    if (modeBtn) modeBtn.style.display = "inline-block";
    
    var commitBtn = document.getElementById("btn-commit-file");
    if (commitBtn) {
      commitBtn.style.display = (tab.source && tab.source !== "local") ? "inline" : "none";
    }
    
    openEditorDirect(tab.path, tab.name, tab.content, tab.source);
    
  } else if (tab.type === "word" || tab.type === "excel") {
    isOfficeActive = true;
    switchView("editor-view");
    
    var modeBtn = document.getElementById("btn-editor-mode");
    if (modeBtn) modeBtn.style.display = "none";
    
    var commitBtn = document.getElementById("btn-commit-file");
    if (commitBtn) {
      commitBtn.style.display = (tab.source && tab.source !== "local") ? "inline" : "none";
    }
    
    document.getElementById("markdown-editor-container").style.display = "none";
    
    openOfficeEditorDirect(tab.path, tab.name, tab.suffix, tab.source);
    
  } else {
    isOfficeActive = false;
    currentOfficeType = "";
    switchView("visualizer-view");
    openVisualizerDirect(tab.path, tab.name, tab.type, tab.content, tab.source);
  }
}

function openExternalVisualizer(tab) {
  if (!tab) return;
  currentFilePath = tab.path;
  currentFileSource = tab.source || "local";
  if (bridge && typeof bridge.openVisualizerWindow === 'function') {
    bridge.openVisualizerWindow(tab.path, tab.name || "Archivo", tab.type || "generic");
  }
  switchView("info-view");
  setEl("info-title", tab.name || "Archivo");
  setEl("info-meta", "Visualizador abierto en ventana independiente");
  var container = document.getElementById("info-files");
  if (container) {
    container.innerHTML = '<p style="color:var(--text-muted);font-style:italic;margin-bottom:12px;">Este tipo de archivo ya no se muestra como panel dividido dentro del aplicativo.</p>' +
                          '<button onclick="openCurrentFileLocation()" onmouseover="this.style.background=\'var(--hover)\'" onmouseout="this.style.background=\'var(--bg)\'" style="border:1px solid var(--border); border-radius:var(--radius); padding:6px 16px; cursor:pointer; font-size:12px; background:var(--bg); color:var(--text); display:inline-flex; align-items:center; gap:6px; transition:background 0.15s;">Abrir ubicacion</button>';
  }
  showStatus("Visualizador externo: " + (tab.name || tab.path));
}

function updateSourceTag(elementId, source) {
  var tag = document.getElementById(elementId);
  if (!tag) return;
  if (source && source !== "local") {
    tag.style.display = "inline";
    tag.textContent = source === "drive" ? "☁️ Drive" : "🐙 GitHub";
  } else {
    tag.style.display = "none";
  }
}

function closeTab(path, event) {
  if (event) event.stopPropagation();
  
  const tabIdx = openTabs.findIndex(t => t.path === path);
  if (tabIdx === -1) return;
  
  const tab = openTabs[tabIdx];
  if (tab.uncommitted || (hasUncommittedChanges && activeTabPath === path)) {
    if (tab.source !== "local") {
      var choice = confirm("¿Deseas SUBIR tus cambios a " + (tab.source === "github" ? "GitHub" : "Drive") + " antes de cerrar?\n\n- [Aceptar]: Subir y Cerrar\n- [Cancelar]: Cerrar SIN Subir");
      if (choice) {
        closeAfterCommit = true;
        isCommitting = true;
        showStatus("Subiendo cambios antes de cerrar...");
        saveCurrentFile();
        setTimeout(function() {
          bridge.commitActiveFile();
        }, 500);
        return;
      }
    } else {
      if (!confirm("Hay cambios sin guardar. ¿Deseas cerrar la pestaña de todos modos?")) {
        return;
      }
    }
  }
  
  openTabs.splice(tabIdx, 1);
  
  if (openTabs.length === 0) {
    activeTabPath = "";
    currentFilePath = "";
    isOfficeActive = false;
    document.getElementById("tabs-bar").style.display = "none";
    performCloseDirect();
  } else {
    if (activeTabPath === path) {
      const nextActiveIdx = Math.min(tabIdx, openTabs.length - 1);
      setActiveTab(openTabs[nextActiveIdx].path);
    } else {
      renderTabsBar();
    }
  }
}

function renderTabsBar() {
  const bar = document.getElementById("tabs-bar");
  if (!bar) return;
  
  if (openTabs.length === 0) {
    bar.style.display = "none";
    return;
  }
  
  bar.style.display = "flex";
  bar.innerHTML = "";
  
  openTabs.forEach(function(tab) {
    const tabEl = document.createElement("div");
    tabEl.className = "tab-item" + (tab.active ? " active" : "");
    tabEl.onclick = function() {
      setActiveTab(tab.path);
    };
    
    // Icon based on type
    let icon = "📄";
    if (tab.type === "markdown") icon = "📝";
    else icon = getTabIcon(tab.type);
    
    const iconSpan = document.createElement("span");
    iconSpan.textContent = icon + " ";
    tabEl.appendChild(iconSpan);
    
    const nameSpan = document.createElement("span");
    nameSpan.textContent = tab.name;
    nameSpan.style.overflow = "hidden";
    nameSpan.style.textOverflow = "ellipsis";
    tabEl.appendChild(nameSpan);
    
    // Unsaved indicator
    if (tab.uncommitted || (hasUncommittedChanges && tab.active)) {
      const dot = document.createElement("span");
      dot.style.color = "var(--accent)";
      dot.style.fontSize = "16px";
      dot.style.lineHeight = "0";
      dot.innerHTML = " &bull;";
      tabEl.appendChild(dot);
    }
    
    const closeBtn = document.createElement("span");
    closeBtn.className = "tab-close";
    closeBtn.textContent = "✕";
    closeBtn.onclick = function(e) {
      closeTab(tab.path, e);
    };
    tabEl.appendChild(closeBtn);
    
    bar.appendChild(tabEl);
  });
}

function getTabIcon(type) {
  switch(type) {
    case "pdf": return "📕";
    case "image": return "🖼️";
    case "video": return "🎥";
    case "audio": return "🎵";
    case "notebook": return "📓";
    case "code": return "💻";
    case "excel": return "📊";
    case "word": return "📝";
    default: return "📄";
  }
}

// Wrappers to integrate with the tab system
function openEditor(path, name, content, source) {
  const info = window._last_signal_info || {};
  window._last_signal_info = null;
  
  addTab({
    path: path,
    name: name,
    type: "markdown",
    content: content,
    source: source,
    suffix: ".md",
    info: info
  });
}

function openOfficeEditor(path, name, suffix, source) {
  const info = window._last_signal_info || {};
  window._last_signal_info = null;
  
  const type = (suffix === ".xlsx" || suffix === ".xls" || suffix === ".csv") ? "excel" : "word";
  addTab({
    path: path,
    name: name,
    type: type,
    content: "",
    source: source,
    suffix: suffix,
    info: info
  });
}

function openVisualizer(path, name, type, content, source) {
  const info = window._last_signal_info || {};
  window._last_signal_info = null;
  
  addTab({
    path: path,
    name: name,
    type: type,
    content: content,
    source: source,
    suffix: "." + type,
    info: info
  });
}

function popOutVisualizer() {
  const tab = openTabs.find(t => t.path === currentFilePath);
  if (tab) {
    if (bridge && typeof bridge.openVisualizerWindow === 'function') {
      bridge.openVisualizerWindow(tab.path, tab.name || "Archivo", tab.type || "generic");
    } else {
      showStatus("El puente no está disponible para abrir ventana externa");
    }
  }
}

function popOutFileExternally() {
  if (currentFilePath) {
    if (bridge && typeof bridge.openFileExternally === 'function') {
      bridge.openFileExternally(currentFilePath);
    } else {
      showStatus("El puente no está disponible para abrir archivo externamente");
    }
  }
}

/* ========== PDF.js Dynamic Canvas Rendering and Zoom ========== */
let currentPdfDoc = null;
let currentPdfScale = 1.25;
let currentPdfFileUrl = "";

function renderPdf(fileUrl) {
  currentPdfFileUrl = fileUrl;
  const scrollArea = document.getElementById("pdf-scroll-area");
  scrollArea.innerHTML = "<div style='color: var(--text-muted); padding: 20px; font-size: 14px;'>⏳ Cargando PDF...</div>";
  setEl("pdf-page-info", "Páginas: --");
  
  if (typeof pdfjsLib === 'undefined') {
    scrollArea.innerHTML = "<div style='color: #ef4444; padding: 20px;'>Error: PDF.js no cargado.</div>";
    return;
  }
  
  pdfjsLib.GlobalWorkerOptions.workerSrc = '../editor/lib/pdf.worker.min.js';
  
  pdfjsLib.getDocument(fileUrl).promise.then(function(pdf) {
    currentPdfDoc = pdf;
    scrollArea.innerHTML = "";
    setEl("pdf-page-info", "Páginas: " + pdf.numPages);
    document.getElementById("pdf-zoom-percent").textContent = Math.round(currentPdfScale * 100) + "%";
    
    for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
      const pageWrapper = document.createElement("div");
      pageWrapper.className = "pdf-page-wrapper";
      pageWrapper.dataset.pageNum = pageNum;
      pageWrapper.style.marginBottom = "16px";
      
      const canvas = document.createElement("canvas");
      pageWrapper.appendChild(canvas);
      scrollArea.appendChild(pageWrapper);
      
      renderPdfPage(pdf, pageNum, canvas);
    }
  }).catch(function(error) {
    console.error("Error al cargar PDF:", error);
    scrollArea.innerHTML = "<div style='color: #ef4444; padding: 20px;'>Error al cargar el PDF: " + error.message + "</div>";
  });
}

function renderPdfPage(pdf, pageNum, canvas) {
  pdf.getPage(pageNum).then(function(page) {
    const ctx = canvas.getContext('2d');
    const viewport = page.getViewport({ scale: currentPdfScale });
    
    const dpr = window.devicePixelRatio || 1;
    canvas.width = viewport.width * dpr;
    canvas.height = viewport.height * dpr;
    canvas.style.width = viewport.width + "px";
    canvas.style.height = viewport.height + "px";
    
    ctx.scale(dpr, dpr);
    
    const renderContext = {
      canvasContext: ctx,
      viewport: viewport
    };
    
    page.render(renderContext);
  });
}

function pdfZoomIn() {
  if (!currentPdfDoc) return;
  if (currentPdfScale >= 3.0) return;
  currentPdfScale += 0.25;
  reRenderPdf();
}

function pdfZoomOut() {
  if (!currentPdfDoc) return;
  if (currentPdfScale <= 0.5) return;
  currentPdfScale -= 0.25;
  reRenderPdf();
}

function pdfZoomFit() {
  if (!currentPdfDoc) return;
  
  currentPdfDoc.getPage(1).then(function(page) {
    const scrollArea = document.getElementById("pdf-scroll-area");
    const containerWidth = scrollArea.clientWidth - 48; // padding + borders
    const viewport = page.getViewport({ scale: 1.0 });
    currentPdfScale = containerWidth / viewport.width;
    
    currentPdfScale = Math.max(0.5, Math.min(2.5, currentPdfScale));
    reRenderPdf();
  });
}

function reRenderPdf() {
  if (!currentPdfDoc) return;
  document.getElementById("pdf-zoom-percent").textContent = Math.round(currentPdfScale * 100) + "%";
  const wrappers = document.querySelectorAll(".pdf-page-wrapper");
  wrappers.forEach(function(wrapper) {
    const pageNum = parseInt(wrapper.dataset.pageNum);
    const canvas = wrapper.querySelector("canvas");
    if (canvas) {
      renderPdfPage(currentPdfDoc, pageNum, canvas);
    }
  });
}

function setupInteractiveTableTools(container) {
  // Eliminadas las funciones de filtrado e interactividad de las tablas a petición del usuario
}

// ========== Custom Local Change History / Restore Points ==========
let editorHistory = [];
let maxHistorySize = 10;
let historyTimeout = null;

function initHistory(initialContent) {
  editorHistory = [initialContent || ""];
  updateHistoryButtonState();
  
  // Close menu if open
  const menu = document.getElementById("editor-history-menu");
  if (menu) menu.style.display = "none";
}

function recordHistorySnapshotDebounced() {
  if (historyTimeout) clearTimeout(historyTimeout);
  historyTimeout = setTimeout(recordHistorySnapshot, 1500);
}

function recordHistorySnapshot() {
  if (!editorInstance) return;
  const currentMarkdown = editorInstance.getMarkdown() || "";
  
  if (editorHistory.length > 0 && editorHistory[editorHistory.length - 1] === currentMarkdown) {
    return;
  }
  
  editorHistory.push(currentMarkdown);
  if (editorHistory.length > maxHistorySize) {
    editorHistory.shift();
  }
  
  updateHistoryButtonState();
}

function updateHistoryButtonState() {
  const btn = document.getElementById("btn-editor-history");
  if (btn) {
    btn.style.display = (editorHistory.length > 1) ? "inline-block" : "none";
  }
}

function toggleHistoryMenu() {
  const menu = document.getElementById("editor-history-menu");
  if (!menu) return;
  
  if (menu.style.display === "none") {
    renderHistoryList();
    menu.style.display = "block";
  } else {
    menu.style.display = "none";
  }
}

function renderHistoryList() {
  const listContainer = document.getElementById("editor-history-list");
  if (!listContainer) return;
  
  listContainer.innerHTML = "";
  const currentText = editorInstance ? editorInstance.getMarkdown() || "" : "";
  
  let count = 0;
  for (let i = editorHistory.length - 2; i >= 0; i--) {
    const text = editorHistory[i];
    if (text === currentText) continue;
    count++;
    
    const item = document.createElement("div");
    item.className = "history-menu-item";
    item.style.padding = "6px 8px";
    item.style.border = "1px solid var(--border)";
    item.style.borderRadius = "4px";
    item.style.cursor = "pointer";
    item.style.fontSize = "11px";
    item.style.background = "#f9fafb";
    item.style.transition = "background 0.15s";
    
    item.onmouseover = function() { item.style.background = "var(--hover)"; };
    item.onmouseout = function() { item.style.background = "#f9fafb"; };
    
    const snippet = text.slice(0, 40).replace(/\n/g, " ") + (text.length > 40 ? "..." : "");
    const stepLabel = `Retroceder ${editorHistory.length - 1 - i} paso(s)`;
    
    item.innerHTML = `<div style="font-weight: 600; color: var(--accent);">${stepLabel}</div>` +
                     `<div style="color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px;">${snippet || "(Archivo vacío)"}</div>`;
    
    item.onclick = function() {
      restoreHistoryState(i);
      toggleHistoryMenu();
    };
    
    listContainer.appendChild(item);
  }
  
  if (count === 0) {
    listContainer.innerHTML = '<div style="font-size:11px; color:var(--text-muted); text-align:center; padding:8px;">No hay estados anteriores disponibles aún.</div>';
  }
}

function restoreHistoryState(idx) {
  if (!editorInstance || idx < 0 || idx >= editorHistory.length) return;
  
  const targetText = editorHistory[idx];
  editorInstance.setMarkdown(targetText);
  
  editorHistory = editorHistory.slice(0, idx + 1);
  updateHistoryButtonState();
  
  showStatus("Restaurado a estado anterior con éxito");
}

function updateWordCount() {
  if (!editorInstance) return;
  const text = editorInstance.getMarkdown() || "";
  const chars = text.length;
  
  // Calculate word count
  const cleanText = text.trim();
  const words = cleanText === "" ? 0 : cleanText.split(/\s+/).length;
  
  // Reading time (200 words per minute average)
  const readingTime = Math.max(1, Math.ceil(words / 200));
  
  const metaEl = document.getElementById("editor-meta");
  if (metaEl) {
    metaEl.textContent = `Palabras: ${words} · Caracteres: ${chars} · Lectura: ~${readingTime} min`;
  }
}

/* ========== AUTHENTICATION SYSTEM – Multi-User ========== */

// State
window._loginSelectedUser = null;
window._loginUsers = [];

// Avatar color palette (deterministic per username)
var _avatarPalettes = [
  ['#6366f1','#4f46e5'], ['#8b5cf6','#7c3aed'], ['#ec4899','#db2777'],
  ['#14b8a6','#0d9488'], ['#f59e0b','#d97706'], ['#ef4444','#dc2626'],
  ['#3b82f6','#2563eb'], ['#10b981','#059669'], ['#f97316','#ea580c'],
  ['#06b6d4','#0891b2']
];
function _avatarGradient(username) {
  var idx = 0;
  for (var i = 0; i < username.length; i++) idx += username.charCodeAt(i);
  var p = _avatarPalettes[idx % _avatarPalettes.length];
  return 'linear-gradient(135deg,' + p[0] + ',' + p[1] + ')';
}
function _avatarInitials(name) {
  var parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function _loginShowPhase(phaseId) {
  ['login-phase-users','login-phase-password','login-phase-setup'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = (id === phaseId) ? 'flex' : 'none';
  });
}

function _renderUserCards(users) {
  var grid = document.getElementById('login-users-grid');
  if (!grid) return;
  grid.innerHTML = '';
  if (!users || users.length === 0) {
    grid.innerHTML = '<p style="color:rgba(255,255,255,0.35);font-size:13px;margin:0">No hay usuarios registrados.</p>';
    return;
  }
  users.forEach(function(u) {
    var card = document.createElement('div');
    card.className = 'login-user-card';
    card.onclick = function() { loginSelectUser(u); };

    var avatar = document.createElement('div');
    avatar.className = 'login-user-avatar';
    avatar.style.background = _avatarGradient(u.username);
    avatar.textContent = _avatarInitials(u.display_name || u.username);

    var name = document.createElement('div');
    name.className = 'login-user-card-name';
    name.textContent = u.display_name || u.username;

    card.appendChild(avatar);
    card.appendChild(name);

    if (u.is_admin) {
      var badge = document.createElement('span');
      badge.className = 'login-user-card-role';
      badge.textContent = 'Admin';
      card.appendChild(badge);
    }
    grid.appendChild(card);
  });
}

function checkAuthStatus() {
  fetch(API_BASE_URL + '/api/auth/users')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var overlay = document.getElementById('login-overlay');
      if (!overlay) return;

      if (!data.has_users) {
        overlay.style.display = 'flex';
        _loginShowPhase('login-phase-setup');
        var inp = document.getElementById('setup-username-input');
        if (inp) setTimeout(function() { inp.focus(); }, 100);
        return;
      }

      window._loginUsers = data.users || [];
      var token = sessionStorage.getItem('app_session_token') || localStorage.getItem('app_session_token');
      if (token) {
        fetch(API_BASE_URL + '/api/auth/verify')
          .then(function(r) { return r.ok ? r.json() : null; })
          .then(function(res) {
            if (res && res.success) {
              _onLoginSuccess(token, res.username, res.is_admin);
            } else {
              showLoginOverlay();
            }
          })
          .catch(function() { showLoginOverlay(); });
      } else {
        showLoginOverlay();
      }
    })
    .catch(function(err) { console.error('checkAuthStatus error:', err); });
}

function showLoginOverlay() {
  var overlay = document.getElementById('login-overlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  _loginShowPhase('login-phase-users');
  _renderUserCards(window._loginUsers);
  setBuildInfo();
  maybeShowForgotLink();
}

function loginSelectUser(user) {
  window._loginSelectedUser = user;
  var av = document.getElementById('login-sel-avatar');
  if (av) { av.style.background = _avatarGradient(user.username); av.textContent = _avatarInitials(user.display_name || user.username); }
  var nm = document.getElementById('login-sel-name');
  if (nm) nm.textContent = user.display_name || user.username;
  var rl = document.getElementById('login-sel-role');
  if (rl) rl.textContent = user.is_admin ? 'Administrador' : '';
  var err = document.getElementById('login-error');
  if (err) err.textContent = '';
  var pw = document.getElementById('login-password-input');
  if (pw) { pw.value = ''; setTimeout(function() { pw.focus(); }, 80); }
  _loginShowPhase('login-phase-password');
}

function loginGoBack() {
  var err = document.getElementById('login-error');
  if (err) err.textContent = '';
  window._loginSelectedUser = null;
  _loginShowPhase('login-phase-users');
}

function submitLogin() {
  var user = window._loginSelectedUser;
  var pwInput = document.getElementById('login-password-input');
  var remem = document.getElementById('login-remember-me');
  var errEl = document.getElementById('login-error');
  if (!user || !pwInput || !errEl) return;
  errEl.textContent = '';
  var password = pwInput.value;
  if (!password) { errEl.textContent = 'La contraseña no puede estar vacía.'; return; }

  setLoginLoading('login-submit-btn', 'login-submit-btn', 'login-submit-btn', null, true);

  fetch(API_BASE_URL + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user.username, password: password, remember_me: remem ? remem.checked : false })
  })
  .then(function(r) {
    if (r.ok) return r.json();
    return r.json().then(function(d) { throw new Error(d.detail || 'Error al iniciar sesión'); });
  })
  .then(function(res) {
    if (res.success && res.token) { pwInput.value = ''; _onLoginSuccess(res.token, res.username, res.is_admin); }
    else { errEl.textContent = 'Error al iniciar sesión.'; }
  })
  .catch(function(err) {
    errEl.textContent = err.message;
    showToast(err.message, 'error');
  })
  .finally(function() {
    setLoginLoading('login-submit-btn', null, null, null, false);
  });
}

function submitSetup() {
  var unInput = document.getElementById('setup-username-input');
  var pwInput = document.getElementById('setup-password-input');
  var errEl = document.getElementById('setup-error');
  if (!unInput || !pwInput || !errEl) return;
  errEl.textContent = '';
  var username = unInput.value.trim();
  var password = pwInput.value;
  if (username.length < 2) { errEl.textContent = 'El usuario debe tener al menos 2 caracteres.'; return; }
  if (password.length < 4) { errEl.textContent = 'La contraseña debe tener al menos 4 caracteres.'; return; }

  setLoginLoading('setup-submit-btn', 'setup-submit-btn', 'setup-submit-btn', null, true);

  fetch(API_BASE_URL + '/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: username, password: password })
  })
  .then(function(r) {
    if (r.ok) return r.json();
    return r.json().then(function(d) { throw new Error(d.detail || 'Error al crear la cuenta'); });
  })
  .then(function(res) {
    if (res.success && res.token) { unInput.value = ''; pwInput.value = ''; _onLoginSuccess(res.token, res.username, res.is_admin); }
    else { errEl.textContent = 'Error al crear la cuenta.'; }
  })
  .catch(function(err) {
    errEl.textContent = err.message;
    showToast(err.message, 'error');
  })
  .finally(function() {
    setLoginLoading('setup-submit-btn', null, null, null, false);
  });
}

// ============================================================
// Login UI helpers (Phase 3)
// ============================================================

function togglePasswordVisibility(inputId, btn) {
  var input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (btn) btn.textContent = '🙈';
  } else {
    input.type = 'password';
    if (btn) btn.textContent = '👁';
  }
}

function setLoginLoading(btnId, spinnerId, labelId, arrowId, isLoading) {
  var btn = document.getElementById(btnId);
  var spinner = document.getElementById(spinnerId);
  var label = document.getElementById(labelId);
  var arrow = document.getElementById(arrowId);
  if (btn) btn.disabled = !!isLoading;
  if (spinner) spinner.style.display = isLoading ? 'inline-block' : 'none';
  if (arrow) arrow.style.display = isLoading ? 'none' : 'inline';
  if (label) label.style.opacity = isLoading ? '0.6' : '1';
}

function showToast(message, kind) {
  // kind: undefined | 'error' | 'success'
  var container = document.getElementById('toast-container');
  if (!container) return;
  var el = document.createElement('div');
  el.className = 'toast' + (kind ? ' toast-' + kind : '');
  var icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.textContent = kind === 'error' ? '⚠️' : (kind === 'success' ? '✅' : 'ℹ️');
  var txt = document.createElement('span');
  txt.textContent = message;
  el.appendChild(icon);
  el.appendChild(txt);
  container.appendChild(el);
  setTimeout(function() {
    if (el.parentNode) el.parentNode.removeChild(el);
  }, 5000);
}

function showPasswordResetModal() {
  var modal = document.getElementById('password-reset-modal');
  if (!modal) return;
  // Populate the server URL hint dynamically
  var urlSpan = document.getElementById('modal-server-url');
  if (urlSpan) urlSpan.textContent = window.location.origin;
  modal.style.display = 'flex';
}

function closePasswordResetModal() {
  var modal = document.getElementById('password-reset-modal');
  if (modal) modal.style.display = 'none';
}

function setBuildInfo() {
  // The build SHA is injected via <meta name="x-build-sha"> at deploy
  // time; if not present we fall back to "dev". This is intentionally
  // tiny so the operator can sanity-check which version is in the wild.
  var meta = document.querySelector('meta[name="x-build-sha"]');
  var sha = (meta && meta.getAttribute('content')) || 'dev';
  var el = document.getElementById('login-build-info');
  if (el) {
    el.textContent = 'build ' + (sha === 'dev' ? sha : sha.substring(0, 7));
    el.title = sha;
  }
}

// Show the "forgot password" link whenever there is at least one user
// (i.e. we are not in the first-time setup phase).
function maybeShowForgotLink() {
  var link = document.getElementById('login-forgot-btn');
  if (!link) return;
  var users = (window._loginUsers || []);
  link.style.display = users.length > 0 ? 'block' : 'none';
}

function _onLoginSuccess(token, username, isAdmin) {
  localStorage.setItem('app_session_token', token);
  sessionStorage.setItem('app_session_token', token);
  localStorage.setItem('app_current_user', username || '');
  localStorage.setItem('app_is_admin', isAdmin ? '1' : '0');
  var overlay = document.getElementById('login-overlay');
  if (overlay) overlay.style.display = 'none';
  _updateAdminBadge(isAdmin);
  _applyAdminGating(isAdmin);
  initTheme();
  syncConfigFromServer();
  if (bridge && typeof bridge.refreshTree === 'function') bridge.refreshTree();
  if (typeof window.initRealtimeSync === 'function') window.initRealtimeSync();
}

function _updateAdminBadge(isAdmin) {
  var badge = document.getElementById('admin-badge');
  if (badge) badge.style.display = isAdmin ? 'inline-block' : 'none';
}

function _applyAdminGating(isAdmin) {
  var settingsBtn = document.querySelector('button[onclick="showSettingsDialog()"]');
  if (settingsBtn) settingsBtn.style.display = isAdmin ? '' : 'none';
}

function adminAddUser(username, password, callback) {
  var token = localStorage.getItem('app_session_token');
  fetch(API_BASE_URL + '/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-session-token': token },
    body: JSON.stringify({ username: username, password: password })
  })
  .then(function(r) { if (r.ok) return r.json(); return r.json().then(function(d) { throw new Error(d.detail||'Error'); }); })
  .then(function(res) { if (callback) callback(null, res); })
  .catch(function(err) { if (callback) callback(err.message); });
}

function adminDeleteUser(username, callback) {
  var token = localStorage.getItem('app_session_token');
  fetch(API_BASE_URL + '/api/auth/users/' + encodeURIComponent(username), {
    method: 'DELETE', headers: { 'x-session-token': token }
  })
  .then(function(r) { if (r.ok) return r.json(); return r.json().then(function(d) { throw new Error(d.detail||'Error'); }); })
  .then(function(res) { if (callback) callback(null, res); })
  .catch(function(err) { if (callback) callback(err.message); });
}

// Legacy compat stubs
function submitAuth() {}
function handleLoginKeydown(e) { if (e.key === 'Enter') submitLogin(); }
function syncConfigFromServer() {
  fetch(API_BASE_URL + "/api/config")
    .then(r => {
      if (r.ok) return r.json();
      throw new Error("Error obteniendo configuración");
    })
    .then(cfg => {
      if (cfg) {
        if (cfg.github_token) localStorage.setItem("github_token", cfg.github_token);
        if (cfg.deepseek_api_key) localStorage.setItem("deepseek_api_key", cfg.deepseek_api_key);
        if (cfg.gemini_api_key) localStorage.setItem("gemini_api_key", cfg.gemini_api_key);
        if (cfg.gemini_model) localStorage.setItem("gemini_model", cfg.gemini_model);
        if (cfg.active_ai_provider) localStorage.setItem("active_ai_provider", cfg.active_ai_provider);
      }
      showLoginOverlay();
    })
    .catch(err => console.error("Error sincronizando config desde el servidor:", err));
}

function logout() {
  // Stop real-time sync before clearing the session token
  if (typeof window.stopRealtimeSync === "function") window.stopRealtimeSync();
  fetch(API_BASE_URL + "/api/auth/logout", { method: "POST" })
    .finally(() => {
      sessionStorage.removeItem("app_session_token");
      localStorage.removeItem("app_session_token");
      localStorage.removeItem("github_token");
      localStorage.removeItem("deepseek_api_key");
      localStorage.removeItem("gemini_api_key");
      localStorage.removeItem("gemini_model");
      localStorage.removeItem("active_ai_provider");
      if (window.appConfig) {
        window.appConfig.github_token = "";
        window.appConfig.deepseek_api_key = "";
        window.appConfig.gemini_api_key = "";
        window.appConfig.gemini_model = "";
        window.appConfig.active_ai_provider = "";
      }
      showLoginOverlay();
    });
}

/* ========== THEME MANAGEMENT ========== */
function initTheme() {
  var storedTheme = localStorage.getItem("app-theme") || "dark";
  var btn = document.getElementById("btn-theme-toggle");
  
  if (storedTheme === "light") {
    document.body.classList.add("light-theme");
    if (btn) btn.querySelector("span").textContent = "☀️";
  } else {
    document.body.classList.remove("light-theme");
    if (btn) btn.querySelector("span").textContent = "🌙";
  }
}

function toggleTheme() {
  var btn = document.getElementById("btn-theme-toggle");
  if (document.body.classList.contains("light-theme")) {
    document.body.classList.remove("light-theme");
    localStorage.setItem("app-theme", "dark");
    if (btn) btn.querySelector("span").textContent = "🌙";
  } else {
    document.body.classList.add("light-theme");
    localStorage.setItem("app-theme", "light");
    if (btn) btn.querySelector("span").textContent = "☀️";
  }
}

/* ========== IMAGE UPLOAD SYSTEMS ========== */
function uploadImageBlob(blob, callback) {
  var reader = new FileReader();
  reader.onload = function(e) {
    var base64Data = e.target.result.split(',')[1];
    var ext = blob.type.split('/')[1] || "png";
    var filename = "img_" + Date.now() + "." + ext;
    
    const tab = openTabs.find(t => t.path === currentFilePath);
    var source = currentFileSource;
    
    var payload = {
      base64_data: base64Data,
      source: source,
      mimetype: blob.type || "image/png",
      path: filename
    };
    
    if (source === "local") {
      var separator = currentFilePath.includes('\\') ? '\\' : '/';
      var lastSep = currentFilePath.lastIndexOf(separator);
      var noteDir = lastSep >= 0 ? currentFilePath.substring(0, lastSep) : "";
      var fullPath = noteDir + separator + "images" + separator + filename;
      payload.path = fullPath;
      
      showStatus("Guardando imagen local...");
      fetch(API_BASE_URL + "/api/file/base64/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
      .then(r => r.json())
      .then(res => {
        if (res.success) {
          showStatus("Imagen guardada en images/" + filename);
          callback("images/" + filename, filename);
        } else {
          showStatus("Error al guardar imagen: " + (res.error || "error desconocido"));
        }
      })
      .catch(err => {
        showStatus("Error de red: " + err.message);
      });
      
    } else if (source === "github") {
      var remoteNotePath = (tab && tab.info && tab.info.remote_id) ? tab.info.remote_id : "";
      var remoteRepo = (tab && tab.info && tab.info.remote_repo) ? tab.info.remote_repo : "";
      
      var lastSlash = remoteNotePath.lastIndexOf('/');
      var remoteImgFolder = lastSlash >= 0 ? remoteNotePath.substring(0, lastSlash) + "/images" : "images";
      var remoteImgPath = remoteImgFolder + "/" + filename;
      
      payload.path = remoteImgPath;
      payload.remote_repo = remoteRepo;
      payload.remote_id = remoteImgPath;
      payload.sha = null;
      
      showStatus("Subiendo imagen a GitHub...");
      fetch(API_BASE_URL + "/api/file/base64/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
      .then(r => r.json())
      .then(res => {
        if (res.success) {
          showStatus("Imagen subida a GitHub");
          callback("images/" + filename, filename);
        } else {
          showStatus("Error al subir a GitHub: " + (res.error || "error desconocido"));
        }
      })
      .catch(err => {
        showStatus("Error de red: " + err.message);
      });
      
    } else if (source === "drive") {
      payload.path = filename;
      
      showStatus("Subiendo imagen a Google Drive...");
      fetch(API_BASE_URL + "/api/file/base64/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
      .then(r => r.json())
      .then(res => {
        if (res.success && res.remote_id) {
          showStatus("Imagen subida a Google Drive");
          var embedUrl = "https://docs.google.com/uc?export=view&id=" + res.remote_id;
          callback(embedUrl, filename);
        } else {
          showStatus("Error al subir a Drive: " + (res.error || "error desconocido"));
        }
      })
      .catch(err => {
        showStatus("Error de red: " + err.message);
      });
    }
  };
  reader.readAsDataURL(blob);
  return false; // Evita la inserción de imágenes duplicadas/por defecto en ToastUI Editor
}

/* ========== MERMAID UTILS ========== */
function detectTemplateIdFromCode(code) {
  var clean = code.trim().toLowerCase();
  if (clean.includes("venn")) return "venn";
  if (clean.includes("classdiagram")) return "class";
  if (clean.includes("sequencediagram")) return "sequence";
  if (clean.includes("erdiagram")) return "erd";
  if (clean.includes("statediagram")) return "state";
  if (clean.includes("gantt")) return "gantt";
  if (clean.includes("pie")) return "pie";
  if (clean.includes("timeline")) return "timeline";
  if (clean.includes("mindmap")) return "mindmap";
  if (clean.includes("architecture")) return "architecture";
  return "flowchart";
}

/* ========== IMAGE ZOOM IN VISUALIZER ========== */
var currentImageScale = 1.0;

function imageZoomIn() {
  const img = document.getElementById("vis-image-preview");
  if (!img) return;
  currentImageScale += 0.2;
  if (currentImageScale > 5.0) currentImageScale = 5.0;
  applyImageZoom();
}

function imageZoomOut() {
  const img = document.getElementById("vis-image-preview");
  if (!img) return;
  currentImageScale -= 0.2;
  if (currentImageScale < 0.2) currentImageScale = 0.2;
  applyImageZoom();
}

function imageZoomFit() {
  currentImageScale = 1.0;
  applyImageZoom(true);
}

function applyImageZoom(fit = false) {
  const img = document.getElementById("vis-image-preview");
  if (!img) return;
  
  var percentEl = document.getElementById("image-zoom-percent");
  if (percentEl) percentEl.textContent = Math.round(currentImageScale * 100) + "%";
  
  if (fit) {
    img.style.maxWidth = "100%";
    img.style.maxHeight = "100%";
    img.style.width = "auto";
    img.style.transform = "none";
  } else {
    img.style.maxWidth = "none";
    img.style.maxHeight = "none";
    img.style.width = (img.naturalWidth * currentImageScale) + "px";
    img.style.transform = "none";
  }
}

/* ========== EXTENDED CHATBOT MARKDOWN PARSER ========== */
function parseMarkdownToHtml(text) {
  var html = escapeHtml(text);
  
  // Code blocks: ```lang code ```
  html = html.replace(/```(\w*)\n([\s\S]*?)\n```/g, function(match, lang, code) {
    return '<pre class="chatbot-code-block" style="background:#1e1e2e; color:#cdd6f4; padding:10px; border-radius:6px; font-family:monospace; margin:8px 0; overflow-x:auto;">' + code + '</pre>';
  });
  
  // Inline code: `code`
  html = html.replace(/`(.*?)`/g, '<code style="background:rgba(0,0,0,0.1); padding:2px 4px; border-radius:4px; font-family:monospace; color:var(--accent);">$1</code>');
  
  // Bold: **text**
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  
  // Italic: *text*
  html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
  
  // Headers: ### text
  html = html.replace(/^### (.*?)$/gm, '<h4 style="margin:10px 0 4px 0; font-size:13px; font-weight:600;">$1</h4>');
  html = html.replace(/^## (.*?)$/gm, '<h3 style="margin:12px 0 4px 0; font-size:14px; font-weight:600;">$1</h3>');
  html = html.replace(/^# (.*?)$/gm, '<h2 style="margin:14px 0 4px 0; font-size:15px; font-weight:600;">$1</h2>');
  
  // Bullet lists: - item
  html = html.replace(/^\s*[-*+]\s+(.*?)$/gm, '<li style="margin-left:16px; margin-bottom:4px; list-style-type:disc;">$1</li>');
  
  // Line breaks
  html = html.replace(/\n/g, '<br>');
  
  return html;
}

window.openImageInVisualizer = function(src) {
  console.log("openImageInVisualizer:", src);
  switchView("visualizer-view");
  
  document.getElementById("vis-pdf-container").style.display = "none";
  document.getElementById("vis-video-container").style.display = "none";
  document.getElementById("vis-image-container").style.display = "flex";
  document.getElementById("vis-code-container").style.display = "none";
  document.getElementById("vis-notebook-container").style.display = "none";
  document.getElementById("vis-audio-container").style.display = "none";
  document.getElementById("vis-generic-container").style.display = "none";
  
  setEl("visualizer-name", "🖼️ Imagen");
  setEl("visualizer-meta", "Vista previa");
  
  const img = document.getElementById("vis-image-preview");
  if (img) {
    img.src = src;
    currentImageScale = 1.0;
    applyImageZoom(true);
  }
};


// ============================================================
// Real-Time Sync – SSE Event Listeners & Banner Notification
// ============================================================

(function initRealtimeSyncUI() {
  // ── Banner helper ─────────────────────────────────────────
  function _showReloadBanner(message) {
    var existing = document.getElementById('rt-sync-banner');
    if (existing) existing.remove();

    var banner = document.createElement('div');
    banner.id = 'rt-sync-banner';
    banner.style.cssText = [
      'position:fixed',
      'top:72px',
      'left:50%',
      'transform:translateX(-50%)',
      'z-index:99999',
      'background:linear-gradient(135deg,#1e293b,#0f172a)',
      'color:#f1f5f9',
      'border:1px solid #334155',
      'border-left:4px solid #3b82f6',
      'border-radius:12px',
      'padding:12px 20px',
      'display:flex',
      'align-items:center',
      'gap:12px',
      'font-size:14px',
      'box-shadow:0 8px 32px rgba(0,0,0,0.5)',
      'backdrop-filter:blur(8px)',
      'max-width:520px',
      'animation:rtBannerIn 0.3s ease'
    ].join(';');

    // Inject keyframe if missing
    if (!document.getElementById('rt-banner-style')) {
      var style = document.createElement('style');
      style.id = 'rt-banner-style';
      style.textContent = '@keyframes rtBannerIn{from{opacity:0;transform:translateX(-50%) translateY(-12px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}';
      document.head.appendChild(style);
    }

    var icon = document.createElement('span');
    icon.textContent = '🔄';
    icon.style.fontSize = '18px';

    var text = document.createElement('span');
    text.textContent = message;
    text.style.flex = '1';

    var btn = document.createElement('button');
    btn.textContent = 'Recargar';
    btn.style.cssText = [
      'background:#3b82f6',
      'color:#fff',
      'border:none',
      'border-radius:6px',
      'padding:6px 14px',
      'cursor:pointer',
      'font-size:13px',
      'font-weight:600',
      'white-space:nowrap'
    ].join(';');
    btn.onclick = function () {
      banner.remove();
      if (typeof loadCurrentFile === 'function') loadCurrentFile();
    };

    var close = document.createElement('button');
    close.textContent = '✕';
    close.title = 'Descartar';
    close.style.cssText = [
      'background:transparent',
      'color:#94a3b8',
      'border:none',
      'cursor:pointer',
      'font-size:16px',
      'padding:0 4px',
      'line-height:1'
    ].join(';');
    close.onclick = function () { banner.remove(); };

    banner.appendChild(icon);
    banner.appendChild(text);
    banner.appendChild(btn);
    banner.appendChild(close);
    document.body.appendChild(banner);

    // Auto-dismiss after 12 seconds
    setTimeout(function () { if (banner.parentNode) banner.remove(); }, 12000);
  }

  // ── Determine currently open file path ───────────────────
  function _currentOpenPath() {
    // Try the global state variable used across ui.js (adapt as needed)
    if (window._currentFilePath) return window._currentFilePath;
    if (window.currentOpenFile && window.currentOpenFile.path) return window.currentOpenFile.path;
    if (window.appState && window.appState.currentFilePath) return window.appState.currentFilePath;
    return null;
  }

  // ── Handle file_saved event ───────────────────────────────
  window.addEventListener('realtime:file_saved', function (e) {
    var detail = e.detail || {};
    var savedPath = detail.path || '';
    var openPath = _currentOpenPath();

    console.log('[RT] file_saved event received:', detail);

    // Only notify if the saved file is the one currently open
    if (openPath && savedPath && openPath === savedPath) {
      _showReloadBanner('📂 Este archivo fue actualizado desde otra sesión.');
    }
  });

  // ── Handle tree_changed event ─────────────────────────────
  window.addEventListener('realtime:tree_changed', function (e) {
    var detail = e.detail || {};
    console.log('[RT] tree_changed event received:', detail);

    // Refresh the file explorer tree
    if (bridge && typeof bridge.refreshTree === 'function') {
      bridge.refreshTree();
    }
  });

  // ── Auto-start SSE if already authenticated ───────────────
  // This handles the case where the user refreshes the page while already logged in
  document.addEventListener('DOMContentLoaded', function () {
    var token = sessionStorage.getItem('app_session_token') || localStorage.getItem('app_session_token');
    if (token && typeof window.initRealtimeSync === 'function') {
      window.initRealtimeSync();
    }
  });

  // If DOM is already loaded (script at end of body), start immediately
  if (document.readyState !== 'loading') {
    var token = sessionStorage.getItem('app_session_token') || localStorage.getItem('app_session_token');
    if (token && typeof window.initRealtimeSync === 'function') {
      window.initRealtimeSync();
    }
  }
})();
