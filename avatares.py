"""Imágenes de perfil: se recortan al cuadrado y se guardan chicas (PNG 192x192) en datos/avatares."""
import io
from pathlib import Path

import database as db

MAX_BYTES = 4_000_000
LADO = 192


def _carpeta():
    return Path(db.RUTA_DB).parent / "avatares"


def ruta(usuario_id):
    return _carpeta() / f"{int(usuario_id)}.png"


def guardar(usuario_id, contenido):
    if not contenido or len(contenido) > MAX_BYTES:
        raise ValueError("La imagen está vacía o pesa más de 4 MB.")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise ValueError("Falta instalar Pillow en el servidor (pip install Pillow) para subir imágenes.")
    try:
        img = Image.open(io.BytesIO(contenido))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (LADO, LADO), method=Image.LANCZOS)
    except Exception:
        raise ValueError("No pude leer esa imagen (usá JPG o PNG).")
    _carpeta().mkdir(parents=True, exist_ok=True)
    img.save(ruta(usuario_id), format="PNG", optimize=True)


def borrar(usuario_id):
    try:
        ruta(usuario_id).unlink()
    except FileNotFoundError:
        pass
