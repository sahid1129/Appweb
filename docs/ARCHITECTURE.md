# Arquitectura de Appweb (Launchpad Web)

> Documento generado el 2026-06-17 sobre el commit `c5d08a7` de `origin/main`.
> Cubre la arquitectura actual del sistema, sus puntos de extensión y un mapa
> de mejoras priorizadas.

---

## 1. Visión general

Appweb es un **editor / explorador de notas multiusuario** con sincronización
a tres orígenes: sistema de archivos local, GitHub y Google Drive. La app
original era de escritorio (Qt/PySide6); esta versión es la reescritura como
SPA estática servida desde GitHub Pages y un backend FastAPI desplegado en
Render.

```
+-------------------+        HTTPS / JSON          +------------------+
|   GitHub Pages    | <--------------------------> |   Render.com     |
| (sahid1129/       |   X-Session-Token header     |   FastAPI app    |
|  Appweb)          |   + CORS allow_origin=*      |   (Uvicorn)      |
|                   |                              |                  |
|  index.html       |        SSE: /api/events/      |  app/main.py     |
|  ui.js            |        ?token=<hex>           |  app/services/*  |
|  ui.css           |                              |                  |
|  api_bridge.js    |                              +--------+---------+
|  editor/          |                                       |
|  visualizer/      |                          .users.json  .session_tokens.json
|                   |                          .failed_logins.json  .oauth_states.json
+-------------------+                          config.json (AI keys, default workspace)
                                                       |
                                                       v
                                            +-----------------------+
                                            | File system (workspace)|
                                            |   |        |          |
                                            | local  GitHub     Drive
                                            | FS    (per user) (per user)
                                            +-----------------------+
```

### Componentes principales

| Capa | Tecnología | Responsabilidad |
|------|------------|-----------------|
| Frontend estático | HTML + CSS + JS vanilla | UI, explorador, editor, chatbot, login, settings |
| Editor markdown | Toast UI Editor (vendored) | Edición de notas, vista previa, exportación a `.docx` |
| Visualizador | PDF.js, Mermaid, xlsx.js | Previsualización de archivos no markdown |
| Bridge de fetch | `api_bridge.js` | Auto-inyección de `X-Session-Token`, CORS, mock `QWebChannel` |
| Backend API | FastAPI + Uvicorn | 45 endpoints REST + 1 SSE |
| Sync GitHub | PyGithub | Listar / leer / escribir repos por usuario |
| Sync Drive | google-api-python-client | OAuth + listar / leer / escribir archivos por usuario |
| Persistencia | Archivos JSON en disco | `config.json`, `.users.json`, `.session_tokens.json`, `.failed_logins.json`, `.oauth_states.json` |
| AI | DeepSeek / Gemini (cliente HTTP) | Chat general, redactor de jefatura, copilot |

---

## 2. Frontend

### 2.1 Estructura de archivos

```
/
├── index.html              # Login overlay + UI principal (sidebar, editor, modals)
├── ui.js                   # 5798 líneas, lógica de UI
├── ui.css                  # 3266 líneas, tema oscuro + claro
├── api_bridge.js           # 1434 líneas, fetch wrapper + SSE + QWebChannel mock
├── assets/
│   ├── editor/lib/         # Toast UI Editor, PDF.js, Mermaid, highlight.js
│   └── ui/                 # Copia legacy de ui.js + api_bridge.js (referencia)
├── visualizer.html         # Visor de PDFs / imágenes / xlsx
├── *.ps1                   # Scripts de recuperación y diagnóstico
└── docs/                   # Esta documentación
```

`index.html` declara un único `<script src="ui.js?v=<sha>">`. La copia en
`assets/ui/ui.js` existe como referencia histórica y se ignora en producción.

### 2.2 Modelo de UI

La UI es un único SPA con tres áreas:

- **Sidebar izquierda** con dos pestañas (`#tab-explorer` y `#tab-chatbot`).
  El explorador muestra árbol local + GitHub + Drive mezclado. El chatbot
  tiene dos modos (`general`, `jefatura`).
- **Centro**: editor (Toast UI) o visualizador.
- **Overlay de login** con tres fases:
  1. Selector de usuario (cards).
  2. Entrada de contraseña.
  3. Setup inicial del admin (solo si no hay usuarios).

### 2.3 Cache-buster

Todos los assets versionados llevan `?v=<short_sha>` para forzar la descarga
tras cada deploy. Esto se actualiza manualmente en `index.html` al cerrar
cada PR / commit relevante.

### 2.4 Login y sesión

La pantalla de login es un único overlay con cuatro fases:

- **Fase 1 — `login-phase-users`** (selector de cards). `GET /api/auth/users` lista usuarios. Si `data.has_users === false` salta directo a Fase 3. Si hay al menos un usuario, muestra los cards y un card extra `+ Nuevo` (dashed border, ver §2.4.1).
- **Fase 2 — `login-phase-password`** (contraseña). `POST /api/auth/login` con PBKDF2-SHA256, 600k iteraciones, sal por usuario.
- **Fase 3 — `login-phase-setup`** (solo si no hay usuarios). Crea la cuenta del primer admin. Se invoca una sola vez en la vida del store.
- **Fase 4 — `login-phase-create-user`** (alta de usuarios adicionales, ver §2.4.1). Requiere autenticarse primero como admin.

Otros parámetros:

- **Token**: 32 bytes hex (`secrets.token_hex(32)`). Se guarda en `localStorage` y `sessionStorage` tal cual (sin hashear en cliente). El server hashea con SHA-256 para indexar `.session_tokens.json`.
- **Rate limit**: 5 intentos fallidos por IP+usuario → 15 min de bloqueo (`.failed_logins.json`).
- **Duración**: 24 h por defecto, 30 días si `remember_me=true`.
- **SSE pre-flight**: tras login, `GET /api/auth/verify` valida el token antes de abrir el `EventSource` (fix reciente, commit `c5d08a7`).

### 2.4.1 Alta de usuarios adicionales (Phase 4)

Para añadir usuarios una vez que el admin ya existe, el backend expone
`POST /api/auth/register` con la siguiente lógica (ver `app/main.py:1928`):

- Si `is_first_user` (store vacío): crea el usuario, lo marca como admin y devuelve token. Sin auth.
- Si ya hay usuarios: exige `X-Session-Token` de un **admin** (`is_admin_token(token)`). Si falla → `403 "Solo el administrador puede crear nuevos usuarios."`.

La UI expone ese flujo con el card `+ Nuevo` en Fase 1:

```
┌─ Fase 1 (cards) ─────────────────────┐
│ ¿Quién eres?                         │
│ ┌──────┐ ┌──────┐ ┌──────────────┐  │
│ │ _adm │ │ ana  │ │ ＋ Nuevo    │  │  ← click
│ └──────┘ └──────┘ └──────────────┘  │
└──────────────────────────────────────┘
        │ click
        ▼
┌─ Fase 4 (create user) ──────────────┐
│ Inicia sesión como administrador    │
│ Usuario: [_admin_______]             │
│ Contraseña: [••••••••••]  👁        │
│ [ Autenticar como administrador → ] │
│                                      │
│ ── tras autenticarse ──              │
│ Nuevo usuario:                       │
│ Usuario:    [______________]         │
│ Nombre:     [______________]         │
│ Contraseña: [______________]    👁   │
│ [ Crear cuenta → ]                   │
│ [← Volver]                           │
└──────────────────────────────────────┘
        │ success
        ▼ toast: "Usuario 'x' creado." + vuelta a Fase 1
```

**Token temporal de admin**: se guarda en `sessionStorage` bajo la clave
`app_admin_token` (separada de la `app_session_token` del usuario que se
logueará después). Se borra al volver a Fase 1, al cerrar la pestaña, o al
terminar el alta.

**Tests** (`tests/test_auth.py`):

- `test_16_register_second_user_requires_admin`: 403 sin token.
- `test_17_register_second_user_with_admin_token_succeeds`: 200 + nuevo token.
- `test_18_register_with_non_admin_token_is_403`: un usuario normal no puede escalar.
- `test_19_register_duplicate_username_is_400`: 400 con mensaje claro.

Esta UI no es vector de ataque: aunque cualquier usuario vea el card, el
endpoint rechaza sin `is_admin_token`. El card se oculta cuando
`users.length === 0` (la Fase 3 toma prioridad).

### 2.5 SSE (real-time sync)

```
GET /api/events/stream?token=<hex>   (text/event-stream)
```

Eventos emitidos: `file_saved`, `tree_changed`. Heartbeat cada 25 s. El
cliente re-conecta con backoff exponencial (2 s → 30 s). El bridge expone
`window.initRealtimeSync()` y `window.stopRealtimeSync()`. `ui.js` se
suscribe a `realtime:file_saved` y `realtime:tree_changed` como `CustomEvent`
en `window`.

### 2.6 Estructura interna de `ui.js` (secciones)

- `_onLoginSuccess` / `submitLogin` / `submitSetup` — autenticación.
- `bridge.*` (provisto por `api_bridge.js`) — tree, file, save, rename, move, delete.
- `syncConfigFromServer` — sincroniza AI keys al login.
- `initRealtimeSync` / listeners de `realtime:*` — eventos SSE.
- `showPasswordResetModal` / `closePasswordResetModal` — modal de master-key.
- `initTheme` / `toggleTheme` — tema oscuro/claro.
- `bridge.refreshTree`, `bridge.openInExplorer`, etc. — el "bridge" es el
  mock de `QWebChannel` que expone todos los métodos del backend.

---

## 3. Backend

### 3.1 Estructura

```
app/
├── main.py             # 2082 líneas, FastAPI app + 45 endpoints
└── services/
    ├── file_manager.py # 110 líneas, lectura/escritura local con _validate_path
    ├── explorer.py     # 293 líneas, build_workspace_tree unificado (local+gh+drive)
    ├── sync_service.py # 796 líneas, GitHubSyncService + GoogleDriveSyncService
    ├── ai_service.py   # 180 líneas, DeepSeek + Gemini cliente
    └── user_store.py   # 284 líneas, multi-usuario, integración, cache TTL
```

### 3.2 Modelo de seguridad y multi-tenancy

- **Auth por header `X-Session-Token`** en todas las rutas `/api/*` excepto las públicas (`/api/auth/login`, `/api/auth/register`, `/api/auth/status`, `/api/auth/users`, `/api/auth/admin/reset-password`, `/api/auth/admin/wipe-users`, `/api/sync/drive/callback`).
- **Middleware `dynamic_auth_middleware`** valida el token antes de cada request. Retorna `JSONResponse(401)` con headers CORS manuales para que el cliente JS pueda detectar el fallo.
- **Per-user workspace** resuelto por `user_store.get_workspace_root(username)` y cacheado con TTL 30 s.
- **Per-user integrations** (GitHub token, Drive OAuth) almacenadas en `.users.json` bajo `integrations`. `user_store.get_github_service_for(username)` y `get_drive_service_for(username)` devuelven instancias cacheadas.
- **AI keys globales** en `config.json` (DeepSeek, Gemini) — no por usuario.
- **Bootstrap admin**: en el arranque, si no hay usuarios, se crea `_admin` con `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` (env vars). Lazy fallback en `/api/auth/status` por si el disco se borra.
- **Master-key recovery**: `RENDER_ADMIN_KEY` habilita `POST /api/auth/admin/reset-password` y `POST /api/auth/admin/wipe-users`. Rate-limited a 5/h por IP, comparación con `hmac.compare_digest`. Si la env var no está, los endpoints retornan 404 (no leak).
- **Endpoint de diagnóstico** `GET /api/auth/admin/help` siempre público.

### 3.3 Servicios

#### `FileManagerService` (110 líneas)
- `_validate_path` resuelve la ruta y comprueba que esté dentro del `root` (anti-traversal).
- Operaciones: `read_text_file`, `save_text_file`, `read_binary_file_base64`, `save_binary_file_base64`, `create_new_file`, `create_new_folder`, `rename_item`, `delete_item`, `move_item`.
- **Limitación**: validación con `Path.resolve()`. En Windows funciona con mayúsculas/minúsculas y barras, pero si se mezcla con rutas remotas (Drive/GitHub) hay que sanitizar.

#### `ExplorerService` (293 líneas)
- `build_workspace_tree()` devuelve lista de nodos para el frontend. Mezcla archivos locales + Drive + GitHub según `active_source` por usuario.
- `_walk_dir`, `_populate_drive_subtree`, `_populate_github_subtree` — recursivos.
- `_icon_for_ext` y `_icon_for_file` — iconografía.
- **Limitación**: la mezcla local+Drive+GitHub se hace en cada `GET /api/tree` sin cache. Para workspaces grandes (miles de archivos) esto puede ser lento.

#### `GitHubSyncService` (446 líneas)
- Cache local en disco (`_cache_path`, `_cache_get`, `_cache_put`) con SHA para invalidación.
- `list_repos`, `get_all_folders`, `list_files`, `download`, `download_binary`, `get_sha`, `create_file`, `commit`, `delete_file`, `rename_file`.
- `_increment_api` lleva el contador de rate-limit de la API de GitHub.
- **Limitación**: el `commit` siempre pide SHA; sin SHA se rechaza. Hay que documentar para los clientes.

#### `GoogleDriveSyncService` (141 líneas)
- `service` es **thread-local** (`threading.local()`) por bug de SSL socket sharing (fix `a4bac51`).
- OAuth flow con `state` taggeado al username para enrutar el callback.
- `list_files`, `get_all_folders`, `download`, `download_binary`, `get_revision`, `upload`, `create_file`, `delete_file`, `rename_file`.
- **Limitación**: el token se guarda como pickle base64 en `.users.json` sin cifrar.

#### `AIService` (180 líneas)
- `call_ai_api` → DeepSeek (default) o Gemini.
- `chat_with_assistant(history, user_message, mode)` con dos modos.
- `run_copilot_action(action, text)` para resumir, mejorar, formalizar, table, spelling, translate.
- **Limitación**: el historial de chat no se persiste en backend; vive en memoria del cliente. Refrescar la pestaña lo pierde.

#### `user_store` (284 líneas)
- `load_users` / `save_users` → `.users.json`.
- `get_user` / `update_user`.
- Cache TTL 30 s para `get_workspace_root` e instancias de `GitHubSyncService` / `GoogleDriveSyncService`.
- `invalidate_user_cache` para forzar recarga.

### 3.4 Catálogo de endpoints (45 rutas)

| Método | Path | Auth | Notas |
|--------|------|------|-------|
| GET | `/` | no | Sirve frontend estático |
| GET | `/api/config` | sí | Lee AI keys |
| POST | `/api/config/save` | sí | Guarda AI keys |
| GET | `/api/tree` | sí | Árbol unificado |
| GET | `/api/file/read` | sí | Lee archivo (local/gh/drive) |
| POST | `/api/file/save` | sí | Guarda con SHA + source |
| GET | `/api/folder/info` | sí | Info de carpeta |
| GET | `/api/file/base64` | sí | Lee binario como base64 |
| POST | `/api/file/base64/save` | sí | Guarda binario |
| GET | `/api/file/raw` | sí | Lee raw (para img/video) |
| POST | `/api/file/create` | sí | Crea archivo |
| POST | `/api/file/create-cloud` | sí | Crea en Drive/GitHub |
| POST | `/api/file/delete` | sí | Elimina |
| POST | `/api/file/rename` | sí | Renombra |
| POST | `/api/file/move` | sí | Mueve |
| GET | `/api/events/stream` | sí | SSE |
| POST | `/api/sync/github/config` | sí | Conecta GitHub |
| POST | `/api/sync/github/clear` | sí | Desconecta |
| POST | `/api/sync/drive/clear` | sí | Desconecta Drive |
| POST | `/api/sync/active-source` | sí | Cambia fuente activa |
| GET | `/api/sync/github/folders` | sí | Lista carpetas |
| GET | `/api/sync/drive/folders` | sí | Lista carpetas |
| GET | `/api/sync/drive/files` | sí | Lista archivos |
| GET | `/api/sync/drive/config_status` | sí | Estado de Drive |
| POST | `/api/sync/drive/save_credentials` | sí | Sube `credentials.json` |
| GET | `/api/sync/drive/auth` | sí | Inicia OAuth |
| GET | `/api/sync/drive/callback` | no | Callback OAuth |
| GET | `/api/sync/github/files` | sí | Lista archivos |
| GET | `/api/auth/users/{u}/workspace` | sí | Lee workspace per-user |
| PUT | `/api/auth/users/{u}/workspace` | sí | Guarda workspace |
| GET | `/api/auth/users/{u}/integrations` | sí | Estado de integraciones |
| PUT | `/api/auth/users/{u}/integrations` | sí | Guarda integraciones |
| POST | `/api/ai/chat` | sí | Chat con AI |
| POST | `/api/ai/copilot` | sí | Acciones de copilot |
| GET | `/api/auth/status` | no | Estado global |
| GET | `/api/auth/users` | no | Lista usuarios |
| GET | `/api/auth/verify` | no* | Verifica token (* necesita header) |
| POST | `/api/auth/admin/reset-password` | **master-key** | Reset con master-key |
| POST | `/api/auth/admin/wipe-users` | **master-key** | Borra todos los usuarios |
| GET | `/api/auth/admin/help` | no | Diagnóstico |
| POST | `/api/auth/register` | no (primer user) / sí (admin) | Crea usuario. **Primer user = admin, sin auth. Resto requiere `X-Session-Token` de admin** (ver §2.4.1) |
| DELETE | `/api/auth/users/{u}` | sí (admin) | Borra usuario |
| POST | `/api/auth/login` | no | Login |
| POST | `/api/auth/setup` | no | Setup |
| POST | `/api/auth/logout` | sí | Logout |

### 3.5 Persistencia

Cinco archivos JSON en el directorio raíz del repo:

| Archivo | Tamaño típico | Volumen | Backup |
|---------|---------------|---------|--------|
| `config.json` | < 1 KB | 1/día | sí |
| `.users.json` | 1-10 KB | 1/registro | sí |
| `.session_tokens.json` | 1-50 KB | 1/login | NO (datos sensibles) |
| `.failed_logins.json` | < 5 KB | 1/intento | NO |
| `.oauth_states.json` | < 1 KB | 1/inicio OAuth | NO |

`Render` monta un disco efímero por instancia. **El plan gratis duerme las
instancias** y el contenido se borra en el siguiente deploy, lo que justifica
la "lazy bootstrap" en `/api/auth/status`. En un plan de pago se debería
migrar a una base de datos real (Postgres recomendado).

---

## 4. Testing

63 tests en `tests/`, ejecutados con `python -m unittest`. Cada test class
arranca su propio subproceso Uvicorn en puerto libre y respalda los
archivos de datos del dev local.

```
tests/
├── test_auth.py                  (17)
├── test_sse_auth.py              (4)
├── test_per_user_workspace.py    (18)
├── test_bootstrap_admin.py       (9)
├── test_admin_reset_password.py  (13)
├── test_admin_help.py            (2)
└── _server.py                    # helper de spawn
```

Suite completa: ~55 s.

---

## 5. Posibles mejoras (priorizadas)

### 5.1 Seguridad (prioridad alta)

| # | Mejora | Esfuerzo | Impacto |
|---|--------|----------|---------|
| S1 | Cifrar tokens de Drive en `.users.json` con clave derivada de `APP_SECRET` | M | Tokens de OAuth exfiltrables en backup |
| S2 | Sustituir PBKDF2 600k por Argon2id (mejor protección GPU) | M | Estándar OWASP 2024 |
| S3 | Implementar 2FA TOTP opcional para admins | L | Phishing / robo de credenciales |
| S4 | Sanitizar nombres de archivo antes de guardar (anti `..`, null bytes, Unicode bidi) | S | Traversal residual |
| S5 | CSP estricta en `index.html` (`default-src 'self'`) | S | XSS si se añade Markdown crudo |
| S6 | Rate-limit `/api/auth/verify` y `/api/auth/admin/help` (info leak) | S | Reconocimiento |
| S7 | HTTPS only + HSTS en producción (ya en Render, falta HSTS) | XS | Man-in-the-middle |

### 5.2 Robustez (prioridad alta)

| # | Mejora | Esfuerzo | Impacto |
|---|--------|----------|---------|
| R1 | Backend con `try/except` en cada endpoint que retorne JSON estructurado, no 500 | L | UX en errores reales |
| R2 | Reemplazar `.json` files por SQLite (1 archivo `.db` con tablas `users`, `sessions`, `failed_logins`, `oauth_states`) | L | Atomicidad, consultas, concurrencia |
| R3 | Mover `dynamic_auth_middleware` a un dependency de FastAPI (`Depends(get_session_user)`) en vez de un middleware que retorna `JSONResponse` | M | Menos CORS surprises, type hints |
| R4 | Refactorizar `main.py` (2082 líneas) en router por dominio (`auth.py`, `files.py`, `sync.py`, `ai.py`) | M | Mantenibilidad |
| R5 | Frontend: un solo `index.html` → separar templates por pantalla | L | Mantenibilidad, debugging |
| R6 | Refactorizar `ui.js` (5798 líneas) en módulos ES con build (Vite/Rollup) | L | Mantenibilidad, tree-shaking |
| R7 | Tipos TypeScript / JSDoc en `ui.js` | M | Menos bugs en runtime |

### 5.3 Rendimiento (prioridad media)

| # | Mejora | Esfuerzo | Impacto |
|---|--------|----------|---------|
| P1 | Cache de `/api/tree` con TTL 5 s por usuario | S | Árbol con >1000 archivos se siente laggy |
| P2 | Paginación / virtual scroll del árbol (cargar hijos on-demand) | M | Escalabilidad a >10k archivos |
| P3 | Compresión gzip en respuestas FastAPI (middleware) | XS | -60% ancho de banda |
| P4 | Cache-buster con hash del contenido real del asset, no SHA del commit | S | Ahorra redeploys |
| P5 | Service worker para offline de solo-lectura | L | UX sin red |
| P6 | Lazy-load de `pdf.js` / `mermaid.min.js` (no se usan en cada carga) | S | TTI más rápido |
| P7 | SSE con backpressure (limitar queue por subscriber) | S | Memoria si hay 100+ tabs |
| P8 | Tree-shake de Toast UI Editor (importar solo plugins usados) | M | -200 KB |

### 5.4 UX (prioridad media)

| # | Mejora | Esfuerzo | Impacto |
|---|--------|----------|---------|
| U1 | Persistir historial de chat en backend (por usuario) | M | Continuidad entre sesiones |
| U2 | Confirm dialog para delete / rename | S | Destrucción accidental de datos |
| U3 | Indicador de "guardando..." con debounce visible | XS | Claridad |
| U4 | Drag & drop de archivos al árbol | M | UX esperada |
| U5 | Búsqueda full-text en notas (FTS5 en SQLite) | L | Feature esperada |
| U6 | Tags / favoritos / recientes | L | Organización |
| U7 | Modo offline con sync en background | XL | Diferenciador fuerte |
| U8 | Tema automático según hora del día | XS | Detalle |
| U9 | PWA instalable (manifest + service worker) | M | UX móvil |

### 5.5 Operacional (prioridad media)

| # | Mejora | Esfuerzo | Impacto |
|---|--------|----------|---------|
| O1 | GitHub Actions CI: `pytest`, `flake8`, `mypy`, build check | M | Calidad de merges |
| O2 | Migrar de `python -m unittest` a `pytest` con cobertura `coverage` | S | Mejor DX, métricas |
| O3 | Pre-commit hooks (`black`, `ruff`, `bandit`) | S | Estilo + seguridad |
| O4 | Dockerizar backend + `docker-compose` para dev | M | "Works on my machine" |
| O5 | Logging estructurado (JSON) con `structlog` | S | Observabilidad |
| O6 | Sentry / GlitchTip para tracking de errores frontend + backend | S | Visibilidad de bugs |
| O7 | Documentar OpenAPI con Swagger UI (FastAPI ya lo tiene en `/docs`, exponer) | XS | Onboarding |
| O8 | Versionado de la API (`/api/v1/...`) para futuros breaking changes | S | Compatibilidad |
| O9 | `RATE_LIMIT_*` por endpoint configurable desde env | S | Defensa DoS |

### 5.6 Funcional (prioridad baja — features nuevas)

| # | Mejora | Esfuerzo | Impacto |
|---|--------|----------|---------|
| F1 | Editor colaborativo en tiempo real (Yjs / Automerge) | XL | Diferenciador |
| F2 | Comentarios por párrafo en notas | L | Trabajo en equipo |
| F3 | Versionado local de archivos (git-like snapshots) | L | Recuperación de cambios |
| F4 | OCR / extracción de texto en imágenes (Tesseract o API) | L | Notas sobre fotos |
| F5 | Speech-to-text para dictado de notas | L | Accesibilidad |
| F6 | Plugins / extensiones (similar a Obsidian) | XL | Ecosistema |
| F7 | Export / import de vaults (formato Obsidian, Joplin) | M | Migración |
| F8 | Diagramas en vivo (excalidraw) | L | Visualización |
| F9 | Kanban board sobre notas con frontmatter | M | Productividad |
| F10 | Mobile-first PWA con gestures | L | Uso en tablet |

---

## 6. Riesgos y deuda técnica conocida

1. **Plano único `app/main.py`** — 2082 líneas, difícil de navegar. Aceptable
   a corto plazo, crítico a mediano plazo.
2. **Archivos JSON en disco** — sin transacciones, vulnerable a corrupciones
   si Render se reinicia a mitad de una escritura. `save_sessions`,
   `save_users` escriben el archivo completo en cada llamada.
3. **Tokens de Drive sin cifrar** — si alguien con acceso al repo hace dump
   de `.users.json` (por un backup, por ejemplo), tiene acceso a los
   tokens OAuth de todos los usuarios.
4. **`ui.js` monolítico** — 5798 líneas sin módulos. Imposible de tree-shake.
   El primer `load` pesa > 1 MB.
5. **CORS `allow_origins=["*"]` con `allow_credentials=True`** — combinación
   inválida según spec. Funciona en navegadores permisivos, pero es una
   bandera roja de seguridad.
6. **Cache-buster manual** — bumpear el SHA en `index.html` se olvida. Un
   script de CI que derive el SHA y lo inyecte eliminaría el problema.
7. **No hay pruebas E2E** — solo unit + integration con servidor arrancado.
   Faltan pruebas del lado JS (`qunit`, `vitest`, `playwright`).
8. **`config.json` global con merge-sensible** — si dos usuarios editan AI
   keys a la vez, el último gana. Aceptable ahora, peligroso a escala.
9. **No hay auditoría** — un admin puede borrar usuarios sin dejar rastro.
   Faltaría un log append-only de acciones admin.

---

## 7. Roadmap sugerido

### Q3 2026 (ahora → 3 meses)
- [ ] R2 (SQLite) — desbloqueará todo lo demás.
- [ ] S1 (cifrar tokens de Drive).
- [ ] R4 (split `main.py` en routers).
- [ ] O1 (GitHub Actions).
- [ ] U2 (confirm dialogs).
- [ ] S4 (sanitizar nombres de archivo).

### Q4 2026
- [ ] R6 (modularizar `ui.js` con Vite).
- [ ] P3 (gzip).
- [ ] P6 (lazy-load PDF/Mermaid).
- [ ] U5 (búsqueda full-text).
- [ ] F7 (import/export Obsidian).

### Q1 2027
- [ ] S3 (2FA TOTP).
- [ ] U9 (PWA instalable).
- [ ] O6 (Sentry).
- [ ] F3 (versionado local de archivos).

### Q2 2027+
- [ ] F1 (editor colaborativo Yjs) — si hay tracción.
- [ ] F6 (plugins) — solo si hay comunidad.

---

## 8. Cómo correr / desplegar

### Local

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000` en el navegador.

### Tests

```powershell
python -m unittest tests.test_auth tests.test_sse_auth tests.test_per_user_workspace tests.test_bootstrap_admin tests.test_admin_reset_password tests.test_admin_help
```

### Despliegue a Render

1. Push a `origin/main` (incluye el bump de `?v=<sha>` en `index.html`).
2. Render detecta el push y redespliega automáticamente.
3. Configurar env vars en el dashboard: `BOOTSTRAP_ADMIN_USERNAME`,
   `BOOTSTRAP_ADMIN_PASSWORD`, `RENDER_ADMIN_KEY` (opcional),
   `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` (opcional).

### Recuperação de admin (si se pierde la contraseña)

```powershell
# Solo si RENDER_ADMIN_KEY está configurada en Render
$env:RENDER_ADMIN_KEY = "<the-key>"
.\recover_admin.ps1 -Username "_admin" -NewPassword "newpass"
```

O vía `change_bootstrap_password.ps1` desde el dashboard de Render.

---

## 9. Glosario

- **SSE**: Server-Sent Events — stream unidireccional server→client.
- **PBKDF2**: Password-Based Key Derivation Function 2 (KDF).
- **CORS**: Cross-Origin Resource Sharing.
- **CSP**: Content Security Policy.
- **TOTP**: Time-based One-Time Password (RFC 6238).
- **PKCE**: Proof Key for Code Exchange (OAuth 2.0).
- **SHA-256**: Secure Hash Algorithm 256 bits.
- **FTS5**: SQLite Full-Text Search versión 5.

---

*Fin del documento. Si encuentras partes obsoletas o quieres ampliar alguna
sección, edita directamente este archivo o pide a opencode que lo regenere
contra el commit actual.*
