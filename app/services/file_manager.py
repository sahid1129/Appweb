# app/services/file_manager.py
import base64
import os
import shutil
from pathlib import Path
from typing import Dict, Any, Union

class FileManagerService:
    def __init__(self, root_path: Union[str, Path]):
        self.root = Path(root_path).resolve()

    def _validate_path(self, path: Union[str, Path]) -> Path:
        """Valida que la ruta esté dentro del workspace root para prevenir Path Traversal."""
        p = Path(path).resolve()
        # Permitir si es la raíz o hijo de la raíz
        if self.root not in p.parents and self.root != p:
            raise PermissionError("Acceso denegado: La ruta está fuera del directorio raíz.")
        return p

    def read_text_file(self, path: str) -> str:
        """Lee un archivo de texto en UTF-8."""
        p = self._validate_path(path)
        if not p.is_file():
            raise FileNotFoundError(f"El archivo no existe: {path}")
        return p.read_text(encoding="utf-8")

    def save_text_file(self, path: str, content: str) -> bool:
        """Guarda un archivo de texto en UTF-8."""
        p = self._validate_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return True

    def read_binary_file_base64(self, path: str) -> str:
        """Lee un archivo binario y retorna su contenido codificado en Base64."""
        p = self._validate_path(path)
        if not p.is_file():
            raise FileNotFoundError(f"El archivo no existe: {path}")
        data = p.read_bytes()
        return base64.b64encode(data).decode("utf-8")

    def save_binary_file_base64(self, path: str, base64_data: str) -> bool:
        """Guarda un archivo binario a partir de datos codificados en Base64."""
        p = self._validate_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(base64_data)
        p.write_bytes(data)
        return True

    def create_new_file(self, parent_folder: str, name: str, content: str = "") -> str:
        """Crea un nuevo archivo en una carpeta y retorna su ruta."""
        folder = self._validate_path(parent_folder)
        if not folder.is_dir():
            raise FileNotFoundError(f"La carpeta no existe: {parent_folder}")
        file_path = folder / name
        self._validate_path(file_path) # Doble verificación
        if file_path.exists():
            raise FileExistsError(f"El archivo ya existe: {name}")
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)

    def create_new_folder(self, parent_folder: str, name: str) -> str:
        """Crea una subcarpeta."""
        folder = self._validate_path(parent_folder)
        if not folder.is_dir():
            raise FileNotFoundError(f"La carpeta padre no existe: {parent_folder}")
        new_folder = folder / name
        self._validate_path(new_folder)
        new_folder.mkdir(parents=True, exist_ok=True)
        return str(new_folder)

    def rename_item(self, path: str, new_name: str) -> str:
        """Renombra un archivo o directorio y retorna la nueva ruta."""
        p = self._validate_path(path)
        if not p.exists():
            raise FileNotFoundError(f"El elemento no existe: {path}")
        new_path = p.parent / new_name
        self._validate_path(new_path)
        if new_path.exists():
            raise FileExistsError(f"Ya existe un elemento con el nombre: {new_name}")
        p.rename(new_path)
        return str(new_path)

    def delete_item(self, path: str) -> bool:
        """Elimina un archivo o directorio (recursivamente)."""
        p = self._validate_path(path)
        if not p.exists():
            raise FileNotFoundError(f"El elemento no existe: {path}")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return True

    def move_item(self, src_path: str, dst_folder: str) -> str:
        """Mueve un archivo o directorio a otra carpeta."""
        src = self._validate_path(src_path)
        dst = self._validate_path(dst_folder)
        if not src.exists():
            raise FileNotFoundError(f"El origen no existe: {src_path}")
        if not dst.is_dir():
            raise FileNotFoundError(f"El destino no existe o no es carpeta: {dst_folder}")
        
        target = dst / src.name
        self._validate_path(target)
        if target.exists():
            raise FileExistsError(f"Ya existe un archivo o carpeta en el destino con el nombre: {src.name}")
        
        shutil.move(str(src), str(target))
        return str(target)
