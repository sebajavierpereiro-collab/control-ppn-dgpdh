"""Compatibilidad con el almacenamiento de archivos PPN (ppn-storage): PREPARADO, SIN CONECTAR.

Cómo funciona ppn-storage (según su código, octubre 2026):
  * Cada aplicación es un «sistema» con un identificador (uuid) que hace de clave y de carpeta.
  * Subir:   POST {url}/api/file/   multipart, campo «file», cabecera `system-identifier-authorization: <uuid>`.
             Responde {filename, extension, size, createdAt}; el nombre original se reemplaza por un uuid.
  * Bajar:   GET  {url}/api/file/{uuid del sistema}/{filename}   (sin clave).
  * Borrar:  DELETE {url}/api/file/{filename}   (con la cabecera).
  * Estado:  GET  {url}/api/system/   -> espacio usado/libre y extensiones y tamaños permitidos.
  * Límite por extensión configurable (2 MB por defecto en un sistema nuevo).

Cuidado antes de usarlo para documentos de este sistema: la descarga no pide clave (cualquiera con el uuid y el
nombre del archivo lo baja), no guarda huellas (SHA-256) ni versiones. Los documentos firmados siguen guardándose
acá con su huella; esto queda listo para, por ejemplo, publicar una copia del PDF despachado si se decide.
"""
from pathlib import Path

URL = ""
SISTEMA_ID = ""
TIEMPO_ESPERA = 30


def configurar(config):
    global URL, SISTEMA_ID
    URL = (config.get("almacen_ppn", "url", fallback="") or "").strip().rstrip("/")
    SISTEMA_ID = (config.get("almacen_ppn", "sistema_id", fallback="") or "").strip()


def activo():
    return bool(URL and SISTEMA_ID)


def _cabeceras():
    return {"system-identifier-authorization": SISTEMA_ID}


def url_descarga(filename):
    return f"{URL}/api/file/{SISTEMA_ID}/{filename}" if activo() and filename else ""


def subir(ruta, sesion=None):
    """Sube un archivo y devuelve el nombre con el que quedó guardado (lo que hay que conservar)."""
    if not activo():
        raise RuntimeError("El almacenamiento PPN no está configurado.")
    import requests
    s = sesion or requests
    ruta = Path(ruta)
    with ruta.open("rb") as f:
        r = s.post(URL + "/api/file/", headers=_cabeceras(), files={"file": (ruta.name, f)}, timeout=TIEMPO_ESPERA)
    if r.status_code != 200:
        raise RuntimeError(f"El almacenamiento PPN rechazó el archivo (código {r.status_code}); revisá tamaño y extensión permitidos.")
    return r.json()["filename"]


def estado(sesion=None):
    if not activo():
        return None
    import requests
    s = sesion or requests
    r = s.get(URL + "/api/system/", headers=_cabeceras(), timeout=TIEMPO_ESPERA)
    r.raise_for_status()
    return r.json()


def eliminar(filename, sesion=None):
    if not activo():
        raise RuntimeError("El almacenamiento PPN no está configurado.")
    import requests
    s = sesion or requests
    r = s.delete(f"{URL}/api/file/{filename}", headers=_cabeceras(), timeout=TIEMPO_ESPERA)
    return r.status_code == 200
