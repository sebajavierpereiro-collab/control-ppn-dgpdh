"""Imágenes de firma personal de cada usuario (DIRECTOR/SUBDIRECTOR).
Patrón idéntico a avatares.py pero con dimensiones aptas para firma."""
from pathlib import Path
from PIL import Image, ImageOps

CARPETA = Path("datos/firmas_usuarios")
CARPETA.mkdir(parents=True, exist_ok=True)
ANCHO, ALTO = 400, 150


def ruta(usuario_id: int) -> Path:
    return CARPETA / f"{usuario_id}.png"


def existe(usuario_id: int) -> bool:
    return ruta(usuario_id).is_file()


def guardar(usuario_id: int, contenido: bytes) -> None:
    """Guarda la imagen redimensionada a ANCHO×ALTO con fondo blanco."""
    from io import BytesIO
    img = Image.open(BytesIO(contenido)).convert("RGBA")
    img = ImageOps.fit(img, (ANCHO, ALTO), Image.LANCZOS)
    fondo = Image.new("RGB", (ANCHO, ALTO), "white")
    fondo.paste(img, mask=img.split()[3])
    fondo.save(ruta(usuario_id), "PNG")


def borrar(usuario_id: int) -> None:
    p = ruta(usuario_id)
    if p.is_file():
        p.unlink()
