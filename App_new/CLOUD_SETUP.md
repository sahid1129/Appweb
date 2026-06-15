# Configuración de Orígenes Remotos

## Google Drive

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Crea un proyecto nuevo (o selecciona uno existente)
3. Habilita **Google Drive API**:
   - Biblioteca → buscar "Google Drive API" → Habilitar
4. Crea credenciales OAuth 2.0:
   - Credenciales → Crear credenciales → ID de cliente OAuth
   - Tipo: **Aplicación de escritorio**
   - Nombre: "Launchpad"
5. Descarga el archivo JSON → renómbralo a `credentials.json`
6. Colócalo en:
   ```
   00_Launchpad/assets/credentials/credentials.json
   ```
7. Abre el Launchpad → haz clic en **☁️ Drive** en la barra de herramientas
8. Se abrirá tu navegador para autorizar la aplicación
9. ¡Listo! Tus archivos .md aparecerán en el árbol

> **Nota:** El token de autenticación se guarda en `token_drive.json`. Si lo borras, tendrás que autorizar de nuevo.

## GitHub (sin git local)

1. Ve a GitHub.com → **Settings** → **Developer settings**
2. **Personal access tokens** → **Tokens (classic)**
3. Haz clic en **Generate new token (classic)**
4. Dale un nombre (ej: "Launchpad")
5. Selecciona permisos:
   - `repo` (para repositorios privados)
   - `public_repo` (solo para públicos)
6. Genera el token → **copia el token** (solo se ve una vez)
7. Abre el Launchpad → haz clic en **🐙 GitHub** en la barra de herramientas
8. Pega el token en el diálogo que aparece
9. ¡Listo! Tus repos aparecerán en el árbol

> **No necesitas git instalado** — todo funciona via API REST.

---

## Uso

- Los archivos .md de Drive/GitHub se descargan a una carpeta temporal al abrirlos
- Al **Guardar (Ctrl+S)**, los cambios se suben automáticamente al origen correspondiente
- Si el archivo cambió en Drive mientras lo editabas, el Launchpad te preguntará antes de sobrescribir
- Los archivos temporales se limpian al cerrar el editor
