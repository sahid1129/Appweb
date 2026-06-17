# Appweb — agent guide

## Stack
- **Frontend**: vanilla HTML/CSS/JS. Two copies of `ui.js` — root (`ui.js`) and `assets/ui/ui.js` — both loaded from `index.html:678` (only `ui.js` is used). Toast UI Editor in `assets/editor/lib/`.
- **Backend**: FastAPI Python in `app/main.py`, services in `app/services/`.
- **Config**: `config.json` (workspace roots, AI keys, global tokens). Server process also writes `.users.json`, `.session_tokens.json`, `.failed_logins.json`, `.oauth_states.json` at repo root.
- **No build step**, no package manager for frontend.

## Run locally
```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Requires Python deps from `requirements.txt`.

## Run tests
```powershell
python -m unittest tests.test_auth tests.test_sse_auth tests.test_per_user_workspace
```
39 tests, ~37s. Each test class spins its own uvicorn subprocess on a free port and backs up the dev's local data files.

## Login / Auth system
- **Multi-user**. First registered user becomes admin. Users stored in `.users.json` with PBKDF2-hashed passwords.
- **3 login phases**: (1) user card selector, (2) password, (3) first-time admin setup.
- Public endpoints: `GET /api/auth/status`, `GET /api/auth/users`, `POST /api/auth/login`, `POST /api/auth/register`.
- All other `/api/*` routes require `X-Session-Token` header (SHA-256 hashed, stored in `localStorage` + `sessionStorage`).
- **Rate limiting**: 5 failed attempts → 15-minute lockout per IP+user pair (`.failed_logins.json`).
- Session duration: 24h (default) or 30d (`remember_me=true`).
- PBKDF2: 600k iterations (OWASP 2023). Legacy 100k hashes still verify, opportunistically rehashed on next login.
- `logout()` calls `POST /api/auth/logout`, clears all tokens from storage, calls `window.stopRealtimeSync()`, then shows login overlay.

## Per-user workspace (Phase 2)
- Each user gets their own `workspace_root` stored in `.users.json` under their record.
- Endpoints: `GET/PUT /api/auth/users/{username}/workspace` (admin or self only).
- Auto-assigned on first login from the global `last_root` for migration invisibility.
- All file/tree endpoints resolve the workspace per session via `get_services_for_token(token)` in `app/main.py`.
- Path validation still uses `FileManagerService._validate_path` to prevent traversal.
- `WORKSPACE_ROOT` global is the fallback when no session token is provided.

## Per-user integrations
- GitHub: per-user token stored in `.users.json` under `integrations.github_token`. `user_store.get_github_service_for(username)` returns a cached `GitHubSyncService` instance.
- Google Drive: per-user OAuth creds (pickled + base64) stored in `integrations.drive_token`. The OAuth `state` parameter is tagged with the username so the callback routes credentials correctly.
- Endpoints: `GET/PUT /api/auth/users/{username}/integrations` (admin or self only).
- Tokens are never returned in API responses — only `*_connected` booleans.
- AI keys (DeepSeek, Gemini) remain **global** in `config.json`.

## "Mi Cuenta" settings tab (Phase 3)
- HTML tab in `index.html` (`#settings-tab-account`) + JS handlers in `ui.js`:
  - `loadSettingsAccountSection()` — fetches workspace + integrations on dialog open
  - `settingsAccountSaveWorkspace()` — PUT workspace path
  - `settingsAccountSaveGithub()` / `settingsAccountClearGithub()` — toggle per-user token
  - `settingsAccountLinkDrive()` — redirects to `/api/sync/drive/auth?token=<session>` so the OAuth state can be tagged with the right user
  - `settingsAccountUnlinkDrive()` — clears the per-user drive token
- Frontend uses `app_current_user` and `app_session_token` from `localStorage`/`sessionStorage` (set by `_onLoginSuccess`).
- All requests go through `fetch` (no `api_bridge.js` shim needed) with the `X-Session-Token` header.
- The Account tab is visible to all users; admin can also see and edit other users' workspace via `PUT /api/auth/users/{u}/workspace` (the in-UI flow targets the current user only).

## Real-time sync
- SSE endpoint at `GET /api/events/stream?token=<session_token>`. Server pushes `file_saved` and `tree_changed` events. **Auth required** (B10 fix).
- Initiated by `window.initRealtimeSync()` after login, torn down by `window.stopRealtimeSync()` on logout.
- Exponential backoff reconnect (2s–30s).

## API Bridge
- `api_bridge.js` mocks Qt `QWebChannel` for the web. Every `fetch` to `API_BASE_URL` auto-attaches tokens from `localStorage`.
- `API_BASE_URL` auto-detects: `localhost:8000` for `file://` or LAN; `https://appweb-o7pl.onrender.com` for production.

## Key endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/tree` | Workspace tree (local + GitHub + Drive) — per-user |
| GET | `/api/file/read` | Read file (local / github / drive) — per-user |
| POST | `/api/file/save` | Save file with source metadata + SHA — per-user |
| GET | `/api/folder/info` | Folder info — per-user, validated against user workspace |
| GET | `/api/config` | Read full config (global) |
| POST | `/api/config/save` | Write config (merge-sensitive, global) |
| POST | `/api/ai/chat` | Chat with AI assistant |
| POST | `/api/ai/copilot` | AI copilot actions |
| GET | `/api/events/stream` | SSE real-time sync (auth required) |
| GET | `/api/auth/users/{u}/workspace` | Per-user workspace info |
| PUT | `/api/auth/users/{u}/workspace` | Set per-user workspace (admin or self) |
| GET | `/api/auth/users/{u}/integrations` | Per-user integration status (booleans only) |
| PUT | `/api/auth/users/{u}/integrations` | Set per-user GitHub/Drive tokens |
| GET | `/api/sync/drive/auth` | Initiate Drive OAuth (per-user via state tag) |

## Conventions
- Dark theme default in `ui.css` (CSS vars). Light theme via `.light-theme` class on `<body>`.
- Password min length: 4 chars. Username min length: 2 chars.
- Windows paths throughout (backslashes), but backend normalizes to `/` where needed.
- All per-user persistence is in `.users.json`; the `config.json` only holds global state (AI keys, default workspace).
