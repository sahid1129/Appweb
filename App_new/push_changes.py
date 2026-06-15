import os
import json
from pathlib import Path
from github import Github

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
REPO_NAME = "sahid1129/Notas_Trabajo"
CORRECT_FOLDER = "Wiki_Estudio_Jun_26/00_Launchpad"
WRONG_FOLDER = "00_Launchpad"
FILES_TO_COMMIT = ["launchpad.py", "launchpad.bat", "requirements.txt", "assets/ui/index.html", "assets/ui/ui.js", "assets/ui/ui.css", "cloud_sync.py"]

def main():
    if not CONFIG_PATH.exists():
        print("Error: No se encuentra config.json")
        return

    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        token = cfg.get("github_token", "")
        if not token:
            print("Error: No se encontro github_token en config.json")
            return

        g = Github(token)
        repo = g.get_repo(REPO_NAME)
        print(f"Conectado a GitHub. Repositorio: {REPO_NAME}")

        # 1. Subir cambios a la ruta correcta
        for filename in FILES_TO_COMMIT:
            local_file = BASE / filename
            if not local_file.exists():
                print(f"Archivo local no encontrado: {filename}")
                continue

            github_path = f"{CORRECT_FOLDER}/{filename}"
            content = local_file.read_text(encoding="utf-8")

            try:
                # Obtener el SHA actual del archivo en GitHub
                print(f"Obteniendo SHA para {github_path}...")
                remote_file = repo.get_contents(github_path)
                sha = remote_file.sha
                
                # Actualizar el archivo
                print(f"Subiendo cambios para {github_path}...")
                repo.update_file(
                    path=github_path,
                    message=f"Update {filename} (Bugfix y soporte venv en Python 3.14)",
                    content=content,
                    sha=sha
                )
                print(f"¡{filename} actualizado en la ruta correcta!")
            except Exception as e:
                if "404" in str(e):
                    print(f"Creando {github_path} en GitHub...")
                    repo.create_file(
                        path=github_path,
                        message=f"Create {filename} (Soporte venv)",
                        content=content
                    )
                    print(f"¡{filename} creado en la ruta correcta!")
                else:
                    print(f"Error al procesar {github_path}: {e}")

        # 2. Limpiar/Eliminar archivos subidos por error en la raíz
        print("\nLimpiando archivos creados por error en la raíz...")
        for filename in FILES_TO_COMMIT:
            wrong_path = f"{WRONG_FOLDER}/{filename}"
            try:
                remote_file = repo.get_contents(wrong_path)
                repo.delete_file(
                    path=wrong_path,
                    message=f"Remove accidental root file {filename}",
                    sha=remote_file.sha
                )
                print(f"Eliminado archivo incorrecto: {wrong_path}")
            except Exception as e:
                print(f"No se pudo eliminar {wrong_path}: {e}")

    except Exception as e:
        print(f"Error general: {e}")

if __name__ == "__main__":
    main()
