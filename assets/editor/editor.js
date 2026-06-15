// Bóveda Editor - JavaScript Logic with Mermaid Modal Integration

var editor = null;
var currentFilePath = "";
var bridge = null;
var hasChanges = false;
var saveTimer = null;

// Preloaded Mermaid Templates
const templates = [
    {
        id: 'flowchart',
        name: 'Diagrama de Flujo',
        desc: 'Procesos de decisión y lógica de control',
        icon: 'git-commit',
        code: `flowchart TD
    A[Inicio: Usuario solicita acceso] --> B{¿Está autenticado?}
    B -- Sí --> C[Obtener datos del panel]
    B -- No --> D[Redirigir a Login]
    D --> E[Procesar Credenciales]
    E --> F{¿Credenciales Válidas?}
    F -- Sí --> C
    F -- No --> G[Mostrar error de login]
    G --> D
    C --> H[Renderizar Dashboard]
    H --> I[Fin]
    
    style A fill:#6366f1,stroke:#fff,stroke-width:2px,color:#fff
    style I fill:#a855f7,stroke:#fff,stroke-width:2px,color:#fff
    style B fill:#14b8a6,stroke:#fff,stroke-width:2px,color:#fff
    style F fill:#14b8a6,stroke:#fff,stroke-width:2px,color:#fff`
    },
    {
        id: 'mindmap',
        name: 'Mapa Mental',
        desc: 'Organización de conceptos e ideas jerárquicas',
        icon: 'network',
        code: `mindmap
  root((Desarrollo Web))
    Frontend
      HTML5
      CSS3
        Flexbox
        CSS Grid
        Tailwind
      JavaScript
        Frameworks
          React
          Vue
          Svelte
    Backend
      Servidores
        Node.js
        Python
        Go
      Bases de Datos
        Relacionales
          PostgreSQL
          MySQL
        NoSQL
          MongoDB
          Redis
    DevOps
      CI-CD
      Docker
      Cloud Providers
        AWS
        GCP`
    },
    {
        id: 'sequence',
        name: 'Secuencia de Eventos',
        desc: 'Flujo de mensajes entre sistemas',
        icon: 'arrow-right-left',
        code: `sequenceDiagram
    autonumber
    actor Cliente as Usuario (Cliente Web)
    participant API as API Gateway
    participant Auth as Servicio Auth
    participant DB as Base de Datos

    Cliente->>API: POST /auth/login (credenciales)
    activate API
    API->>Auth: Validar credenciales
    activate Auth
    Auth->>DB: Buscar usuario por email
    activate DB
    DB-->>Auth: Retornar hash de contraseña
    deactivate DB
    Auth->>Auth: Comparar contraseñas
    Auth-->>API: Credenciales válidas (ID Usuario)
    deactivate Auth
    API->>Auth: Generar JWT Token
    activate Auth
    Auth-->>API: JWT Token (expira en 24h)
    deactivate Auth
    API-->>Cliente: HTTP 200 OK + JWT Token
    deactivate API`
    },
    {
        id: 'class',
        name: 'Clases (OOP)',
        desc: 'Estructura de clases y relaciones de herencia',
        icon: 'package-open',
        code: `classDiagram
    class Vehiculo {
        +String marca
        +String modelo
        +int año
        +encender() void
        +apagar() void
    }
    class Auto {
        +int cantidadPuertas
        +abrirMaletera() void
    }
    class Moto {
        +boolean tieneSidecar
        +hacerCaballito() void
    }
    class Motor {
        +String tipo
        +int cilindrada
        +arrancar() void
    }
    
    Vehiculo <|-- Auto : Hereda
    Vehiculo <|-- Moto : Hereda
    Vehiculo *-- Motor : Contiene (Composición)`
    },
    {
        id: 'erd',
        name: 'Entidad Relación (BD)',
        desc: 'Modelado de bases de datos relacionales',
        icon: 'database',
        code: `erDiagram
    CLIENTE ||--o{ ORDEN : realiza
    ORDEN ||--|{ DETALLE_ORDEN : contiene
    PRODUCTO ||--o{ DETALLE_ORDEN : se_incluye_en
    CLIENTE {
        int id PK
        string nombre
        string email
        string telefono
    }
    ORDEN {
        int id PK
        date fecha
        float total
        int cliente_id FK
    }
    PRODUCTO {
        int id PK
        string nombre
        float precio
        int stock
    }
    DETALLE_ORDEN {
        int orden_id PK, FK
        int producto_id PK, FK
        int cantidad
        float precio_unitario
    }`
    },
    {
        id: 'state',
        name: 'Diagrama de Estados',
        desc: 'Ciclo de vida y transiciones de estados',
        icon: 'circle-dot',
        code: `stateDiagram-v2
    [*] --> Desconectado : Encender dispositivo
    Desconectado --> BuscandoRed : Iniciar escaneo
    BuscandoRed --> Conectando : Red encontrada
    BuscandoRed --> Desconectado : Tiempo límite excedido
    Conectando --> Conectado : Autenticación exitosa
    Conectando --> Desconectado : Error de clave
    Conectado --> Desconectado : Pérdida de señal
    Conectado --> [*] : Apagar dispositivo`
    },
    {
        id: 'gantt',
        name: 'Cronograma (Gantt)',
        desc: 'Planificación de proyectos y tareas en el tiempo',
        icon: 'calendar-days',
        code: `gantt
    title Cronograma de Lanzamiento de App
    dateFormat  YYYY-MM-DD
    section Planificación
    Definición de requerimientos :active, des1, 2026-06-01, 7d
    Diseño de Arquitectura       :      des2, after des1, 5d
    section Desarrollo
    Base de datos e APIs        :active, dev1, after des2, 12d
    Desarrollo del Frontend     :      dev2, after des2, 15d
    section Pruebas & Deploy
    Pruebas de Integración      :      test1, after dev2, 5d
    Despliegue a Producción      :      milestone, after test1, 0d`
    },
    {
        id: 'journey',
        name: 'Viaje del Usuario',
        desc: 'Pasos y experiencia emocional del usuario',
        icon: 'map',
        code: `journey
    title Compra de un Producto en Tienda Online
    section Descubrimiento
      Buscar producto: 5: Usuario
      Ver detalle del producto: 4: Usuario, Sistema
    section Decisión
      Agregar al carrito: 5: Usuario
      Ver costo de envío: 2: Usuario, Sistema
    section Compra
      Ingresar dirección: 3: Usuario
      Realizar pago: 4: Usuario, PasarelaPago
    section Entrega
      Recibir email de confirmación: 5: Sistema
      Recibir el producto: 5: Repartidor`
    }
];

// Modal Elements references
let activeTemplateId = 'flowchart';
let panZoomInstance = null;
let renderTimeout = null;

// Initialize Toast UI Editor
function initEditor(initialContent, fileName) {
  document.getElementById("fileName").textContent = fileName || "—";

  if (editor) {
    editor.destroy();
    editor = null;
  }

  // Create custom toolbar button for Mermaid
  const mermaidBtn = document.createElement("button");
  mermaidBtn.className = "toastui-editor-toolbar-icons mermaid-btn";
  mermaidBtn.style.backgroundImage = "none";
  mermaidBtn.style.margin = "0";
  mermaidBtn.innerHTML = "🧜‍♀️";
  mermaidBtn.type = "button";
  mermaidBtn.addEventListener("click", openMermaidModal);

  editor = new toastui.Editor({
    el: document.querySelector("#editor"),
    height: "100%",
    initialValue: initialContent || "",
    initialEditType: "wysiwyg",
    previewStyle: "vertical",
    hideModeSwitch: false,
    usageStatistics: false,
    toolbarItems: [
      ["heading", "bold", "italic", "strike"],
      ["hr", "quote"],
      ["ul", "ol", "task"],
      ["table", "link"],
      ["code", "codeblock"],
      [
        {
          name: "mermaid",
          tooltip: "Insertar/Editar Diagrama Mermaid",
          el: mermaidBtn
        }
      ],
      ["scrollSync"]
    ],
    customHTMLRenderer: {
      htmlBlock: {
        iframe(node) {
          return [
            { type: "openTag", tagName: "iframe", attributes: node.attrs }
          ];
        }
      }
    }
  });

  hasChanges = false;
  setStatus("", "");

  editor.on("change", function() {
    hasChanges = true;
    setStatus("Sin guardar", "unsaved");

    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function() {
      autoSave();
    }, 3000);
  });
}

function setStatus(text, cls) {
  var el = document.getElementById("saveStatus");
  el.textContent = text;
  el.className = cls || "";
}

function autoSave() {
  if (!hasChanges || !bridge || !currentFilePath) return;
  setStatus("Guardando…", "saving");
  var md = editor.getMarkdown();
  bridge.saveContent(currentFilePath, md);
}

function doSave() {
  if (!editor || !bridge || !currentFilePath) return;
  var md = editor.getMarkdown();
  setStatus("Guardando…", "saving");
  bridge.saveContent(currentFilePath, md);
}

// ==========================================================================
// Mermaid Modal Control Functions
// ==========================================================================

const modal = document.getElementById('mermaidModal');
const modalCodeEditor = document.getElementById('modal-code-editor');
const modalLineNumbers = document.getElementById('modal-line-numbers');
const modalTemplatesContainer = document.getElementById('modal-templates-container');
const modalRenderTarget = document.getElementById('modal-mermaid-render-target');
const modalErrorBanner = document.getElementById('modal-error-banner');
const modalErrorMessage = document.getElementById('modal-error-message');

function openMermaidModal() {
  if (!editor) return;
  
  modal.classList.remove('hidden');
  lucide.createIcons();
  
  // Setup templates bar
  setupModalTemplates();

  // Detección de código existente: si el usuario seleccionó un bloque ```mermaid lo extraemos
  let selectedText = editor.getSelectedText() || "";
  let initialCode = "";
  
  if (selectedText.includes("```mermaid")) {
    const match = selectedText.match(/```mermaid\s*([\s\S]*?)\s*```/);
    initialCode = match ? match[1].trim() : selectedText.replace(/```mermaid/g, "").replace(/```/g, "").trim();
  } else if (
    selectedText.trim().startsWith("flowchart") || 
    selectedText.trim().startsWith("graph") || 
    selectedText.trim().startsWith("sequenceDiagram") ||
    selectedText.trim().startsWith("classDiagram") ||
    selectedText.trim().startsWith("erDiagram") ||
    selectedText.trim().startsWith("mindmap") ||
    selectedText.trim().startsWith("gantt")
  ) {
    initialCode = selectedText.trim();
  } else {
    // Si no hay código seleccionado, carga la plantilla por defecto
    initialCode = templates[0].code;
  }
  
  modalCodeEditor.value = initialCode;
  updateModalLineNumbers();
  
  // Render initially
  setTimeout(() => {
    renderModalDiagram();
  }, 100);
  
  modalCodeEditor.focus();
}

function closeMermaidModal() {
  modal.classList.add('hidden');
  if (panZoomInstance) {
    panZoomInstance.destroy();
    panZoomInstance = null;
  }
}

// Inyectar bloque en la nota de Toast UI
document.getElementById('btn-modal-insert').addEventListener('click', () => {
  const code = modalCodeEditor.value;
  const markdownBlock = `\n\`\`\`mermaid\n${code}\n\`\`\`\n`;
  editor.insertText(markdownBlock);
  closeMermaidModal();
});

// Botón de Cancelar Modal
document.getElementById('btn-modal-cancel').addEventListener('click', closeMermaidModal);

// Template list population inside Modal
function setupModalTemplates() {
  modalTemplatesContainer.innerHTML = '';
  templates.forEach(tpl => {
    const card = document.createElement('button');
    card.className = `template-card ${tpl.id === activeTemplateId ? 'active' : ''}`;
    card.id = `modal-tpl-${tpl.id}`;
    card.innerHTML = `
      <div class="template-icon">
        <i data-lucide="${tpl.icon}"></i>
      </div>
      <div class="template-details">
        <span class="template-name">${tpl.name}</span>
        <span class="template-desc">${tpl.desc}</span>
      </div>
    `;
    
    card.addEventListener('click', () => {
      document.querySelectorAll('.template-card').forEach(el => el.classList.remove('active'));
      card.classList.add('active');
      activeTemplateId = tpl.id;
      modalCodeEditor.value = tpl.code;
      updateModalLineNumbers();
      renderModalDiagram();
    });

    modalTemplatesContainer.appendChild(card);
  });
  lucide.createIcons();
}

// Synced Line Numbers scroll
modalCodeEditor.addEventListener('scroll', () => {
  modalLineNumbers.scrollTop = modalCodeEditor.scrollTop;
});

// Input updates
modalCodeEditor.addEventListener('input', () => {
  updateModalLineNumbers();
  triggerModalDelayedRender();
});

// Key intercepts (Tab indent & Ctrl+Enter insertion)
modalCodeEditor.addEventListener('keydown', (e) => {
  if (e.key === 'Tab') {
    e.preventDefault();
    const start = modalCodeEditor.selectionStart;
    const end = modalCodeEditor.selectionEnd;
    
    modalCodeEditor.value = modalCodeEditor.value.substring(0, start) + "    " + modalCodeEditor.value.substring(end);
    modalCodeEditor.selectionStart = modalCodeEditor.selectionEnd = start + 4;
    
    updateModalLineNumbers();
    triggerModalDelayedRender();
  }
  
  if (e.ctrlKey && e.key === 'Enter') {
    e.preventDefault();
    renderModalDiagram();
  }
});

function updateModalLineNumbers() {
  const lines = modalCodeEditor.value.split('\n');
  const lineCount = lines.length;
  let numbersHtml = '';
  for (let i = 1; i <= lineCount; i++) {
    numbersHtml += `<div>${i}</div>`;
  }
  modalLineNumbers.innerHTML = numbersHtml;
}

// Render Engine (Mermaid v10 sync)
function renderModalDiagram() {
  if (!window.mermaid) return;
  
  const code = modalCodeEditor.value.trim();
  if (!code) {
    modalRenderTarget.innerHTML = '<p class="empty-history">Escribe código para ver el renderizado.</p>';
    return;
  }

  const renderId = 'mermaid-modal-render-' + Math.floor(Math.random() * 1000000);

  try {
    modalErrorBanner.classList.add('hidden');
    window.mermaid.render(renderId, code, modalRenderTarget).then(function(result) {
      modalRenderTarget.innerHTML = result.svg;
      initializeModalPanZoom();
    }).catch(function(error) {
      console.error('Error render modal:', error);
      modalErrorBanner.classList.remove('hidden');
      modalErrorMessage.textContent = error.message || error.toString();
    });
  } catch (error) {
    console.error('Error render modal sync:', error);
    modalErrorBanner.classList.remove('hidden');
    modalErrorMessage.textContent = error.message || error.toString();
  }
}

function triggerModalDelayedRender() {
  if (renderTimeout) clearTimeout(renderTimeout);
  renderTimeout = setTimeout(() => {
    renderModalDiagram();
  }, 800);
}

// Pan & Zoom controls for modal
function initializeModalPanZoom() {
  if (panZoomInstance) {
    panZoomInstance.destroy();
    panZoomInstance = null;
  }
  const svgElement = modalRenderTarget.querySelector('svg');
  if (svgElement) {
    svgElement.setAttribute('width', '100%');
    svgElement.setAttribute('height', '100%');
    svgElement.style.maxWidth = '100%';
    svgElement.style.maxHeight = '100%';
    
    panZoomInstance = svgPanZoom(svgElement, {
      zoomEnabled: true,
      controlIconsEnabled: false,
      fit: true,
      center: true,
      minZoom: 0.05,
      maxZoom: 15,
      zoomScaleSensitivity: 0.15
    });
  }
}

document.getElementById('btn-modal-zoom-in').addEventListener('click', () => {
  if (panZoomInstance) panZoomInstance.zoomIn();
});
document.getElementById('btn-modal-zoom-out').addEventListener('click', () => {
  if (panZoomInstance) panZoomInstance.zoomOut();
});
document.getElementById('btn-modal-zoom-reset').addEventListener('click', () => {
  if (panZoomInstance) {
    panZoomInstance.resetZoom();
    panZoomInstance.center();
  }
});

// ==========================================================================
// QWebChannel bindings
// ==========================================================================

new QWebChannel(qt.webChannelTransport, function(channel) {
  bridge = channel.objects.bridge;

  bridge.loadContent.connect(function(filePath, markdown) {
    currentFilePath = filePath;
    var name = filePath.split("/").pop().split("\\").pop();
    initEditor(markdown, name);
  });

  bridge.saveResult.connect(function(filePath, success) {
    if (filePath === currentFilePath) {
      if (success) {
        hasChanges = false;
        setStatus("Guardado", "saved");
      } else {
        setStatus("Error al guardar", "error");
      }
    }
  });

  if (typeof bridge.editorReady === "function") {
    bridge.editorReady();
  }

  bridge.ready();
});
