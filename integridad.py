"""
integridad.py — Cadena de custodia de los documentos (huellas SHA-256).

Se guarda la huella del original al subirlo, del documento procesado cuando
termina la revisión y del firmado al insertar la firma. Sirve para:
  * detectar que un archivo fue alterado o reemplazado fuera de la app;
  * negarse a firmar un documento procesado que ya no es el que se revisó.
Las notas anteriores a esta función no tienen huella (NULL) y simplemente
no se verifican.
"""
import hashlib
from pathlib import Path

COLUMNAS = {
    "original": ("sha256_original", "ruta_original"),
    "resultado": ("sha256_resultado", "ruta_resultado"),
    "firmado": ("sha256_firmado", "ruta_firmada"),
}


def sha256_archivo(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def sha256_si_existe(ruta):
    try:
        return sha256_archivo(ruta) if ruta and Path(ruta).is_file() else None
    except OSError:
        return None


def verificar_nota(nota):
    """Para cada documento de la nota que tiene huella guardada, compara con
    el archivo actual. Devuelve una lista de
    {"documento", "huella", "estado": "OK"|"ALTERADO"|"FALTA_ARCHIVO"}."""
    resultado = []
    for nombre, (col_hash, col_ruta) in COLUMNAS.items():
        guardada = nota.get(col_hash)
        if not guardada:
            continue
        ruta = nota.get(col_ruta)
        if not ruta or not Path(ruta).is_file():
            estado = "FALTA_ARCHIVO"
        else:
            estado = "OK" if sha256_archivo(ruta) == guardada else "ALTERADO"
        resultado.append({"documento": nombre, "huella": guardada, "estado": estado})
    return resultado


def resultado_coincide(nota):
    """True si el documento procesado sigue siendo el que se revisó (o si no
    hay huella guardada para comparar)."""
    guardada = nota.get("sha256_resultado")
    if not guardada:
        return True
    ruta = nota.get("ruta_resultado")
    return bool(ruta) and Path(ruta).is_file() and sha256_archivo(ruta) == guardada
