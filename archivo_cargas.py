"""Archivo original de cada base cargada (listados de detenidos, planillas de metas, unidades, viajes, usuarios).

Cada carga confirmada guarda el archivo TAL COMO SE SUBIÓ, byte a byte, en
    datos/bases_cargadas/<tipo>/<año>/<AAAA-MM-DD_HHMM>_carga<N>_<nombre original>
con su huella SHA-256 registrada. Nunca se borra (ni si la carga se anula). La carpeta entra en el respaldo nocturno
(respaldo.CARPETAS_DOCUMENTOS) y queda en la PC: tiene datos personales, va con los mismos permisos que la base.
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path

import database as db

TIPOS = {"detenidos": "Listados de personas detenidas", "metas": "Planillas de metas", "unidades_menu": "Unidades del Menú de la Procuración",
         "unidades_csv": "CSV de unidades", "viajes": "Excel de viajes", "usuarios": "Importación de usuarios"}


def carpeta():
    c = Path(db.RUTA_DB).parent / "bases_cargadas"
    c.mkdir(parents=True, exist_ok=True)
    return c


def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS cargas_archivos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, carga_id INTEGER, archivo TEXT, ruta TEXT NOT NULL,
        sha256 TEXT NOT NULL, bytes INTEGER, usuario TEXT, fecha TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_cargas_archivos ON cargas_archivos(tipo, carga_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_cargas_archivos_hash ON cargas_archivos(sha256)")


def huella(contenido):
    return hashlib.sha256(contenido or b"").hexdigest()


def _nombre_seguro(nombre):
    base = Path(str(nombre or "archivo")).name
    return re.sub(r"[^\w.\- ()áéíóúñÁÉÍÓÚÑ]", "_", base)[:120] or "archivo"


def ya_cargado(contenido, tipo=None):
    """La carga anterior con el mismo archivo (misma huella), si la hay."""
    sql, args = "SELECT * FROM cargas_archivos WHERE sha256 = ?", [huella(contenido)]
    if tipo:
        sql += " AND tipo = ?"; args.append(tipo)
    with db._conexion() as con:
        f = con.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()
    return dict(f) if f else None


def guardar(tipo, carga_id, nombre, contenido, usuario=None):
    """Guarda el original y lo registra. Nunca pisa uno existente. Devuelve el id del registro."""
    if contenido is None:
        return None
    ahora = datetime.now()
    destino_dir = carpeta() / tipo / str(ahora.year)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / (f"{ahora:%Y-%m-%d_%H%M}_" + (f"carga{carga_id}_" if carga_id else "") + _nombre_seguro(nombre))
    k = 1
    while destino.exists():
        destino = destino.with_name(f"{destino.stem}_{k}{destino.suffix}"); k += 1
    destino.write_bytes(contenido)
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario
    with db._conexion() as con:
        return con.execute("""INSERT INTO cargas_archivos (tipo, carga_id, archivo, ruta, sha256, bytes, usuario, fecha) VALUES (?,?,?,?,?,?,?,?)""",
                           (tipo, carga_id, nombre, str(destino.relative_to(carpeta())), huella(contenido), len(contenido), quien,
                            ahora.isoformat(timespec="seconds"))).lastrowid


def de_carga(tipo, carga_id):
    with db._conexion() as con:
        f = con.execute("SELECT * FROM cargas_archivos WHERE tipo = ? AND carga_id = ? ORDER BY id LIMIT 1", (tipo, carga_id)).fetchone()
    return dict(f) if f else None


def de_cargas(tipo):
    with db._conexion() as con:
        return {f["carga_id"]: dict(f) for f in con.execute("SELECT * FROM cargas_archivos WHERE tipo = ?", (tipo,))}


def leer(registro):
    """(nombre, bytes) del original; verifica que la huella siga siendo la misma."""
    ruta = (carpeta() / registro["ruta"]).resolve()
    if carpeta().resolve() not in ruta.parents or not ruta.is_file():
        raise FileNotFoundError("El archivo original no está en la carpeta de bases cargadas.")
    contenido = ruta.read_bytes()
    if huella(contenido) != registro["sha256"]:
        raise ValueError("El archivo guardado no coincide con su huella digital: puede haber sido modificado.")
    return registro["archivo"] or ruta.name, contenido
