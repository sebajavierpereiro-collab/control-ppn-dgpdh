"""Ola 6 (137): trazabilidad de lo que genera la IA local.

Cada llamada al modelo queda registrada: qué tarea (corrección, clasificación, resumen, consistencia…), sobre qué
documento, con qué modelo, cuándo, cuánto tardó y qué devolvió (recortado). Del pedido solo se guarda su tamaño y una
huella, no el texto: el documento ya está archivado y no se duplica. Cuando una persona decide sobre lo propuesto
(acepta, edita o rechaza en «Revisar»), también queda registrado. La IA solo propone: lo que se ve acá es la constancia.
"""
import contextlib
import contextvars
import hashlib
import time
from datetime import datetime, timezone

import database as db

CONTEXTO = contextvars.ContextVar("contexto_ia", default=None)
MAX_SALIDA = 2000
TAREAS = {"CORRECCION": "Corrección del texto", "CLASIFICAR": "Clasificación", "CONSISTENCIA": "Consistencia entre documentos",
          "MEJORAS": "Mejoras desde el informe", "RESUMEN": "Resumen", "CONSULTA": "Consulta en lenguaje común",
          "REVISION_HUMANA": "Decisión de la persona sobre lo propuesto", "VERIFICACION": "Verificación de la corrección"}


def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS ia_registro (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nota_id INTEGER, tarea TEXT NOT NULL, proceso TEXT, modelo TEXT,
        pedido_chars INTEGER, pedido_huella TEXT, salida TEXT, ok INTEGER NOT NULL DEFAULT 1, duracion_ms INTEGER,
        usuario TEXT, fecha TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ia_registro_nota ON ia_registro(nota_id, fecha)")


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def para(nota_id, tarea):
    """Marca las llamadas a la IA hechas dentro del bloque como de esta nota y esta tarea."""
    token = CONTEXTO.set({"nota_id": nota_id, "tarea": tarea})
    try:
        yield
    finally:
        CONTEXTO.reset(token)


class Cronometro:
    def __init__(self):
        self.inicio = time.monotonic()

    def ms(self):
        return int((time.monotonic() - self.inicio) * 1000)


# Propuesta 47: los números con forma de DNI, CUIL o LPU se guardan tapados en la salida (30.xxx.x45). Se apaga con
# [ia] tapar_documentos_registro = false.
TAPAR_DOCUMENTOS = True
_EXTRA = ("num_ctx", "tokens_estimados", "uso_ctx", "aviso_ctx", "modelo_digest", "version_prompt", "parametros")


def registrar_llamada(proceso, modelo, prompt, salida, ok=True, duracion_ms=None, extra=None):
    """Lo llama nucleo en cada consulta al modelo. Nunca interrumpe el trabajo si falla.
    `extra` (propuestas 20 y 27): num_ctx, tokens_estimados, uso_ctx, aviso_ctx, modelo_digest, version_prompt, parametros."""
    ctx = CONTEXTO.get() or {}
    texto = (salida or "")[:MAX_SALIDA]
    if TAPAR_DOCUMENTOS:
        from dgpdh.dominio.datos_personales import tapar_documentos
        texto = tapar_documentos(texto)
    base = {"nota_id": ctx.get("nota_id"), "tarea": ctx.get("tarea") or (proceso or "").upper(), "proceso": proceso, "modelo": modelo,
            "pedido_chars": len(prompt or ""), "pedido_huella": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16],
            "salida": texto, "ok": 1 if ok else 0, "duracion_ms": duracion_ms, "fecha": _ahora()}
    adicionales = {k: v for k, v in (extra or {}).items() if k in _EXTRA and v is not None}
    try:
        with db._conexion() as con:
            try:
                fila = {**base, **adicionales}
                con.execute(f"INSERT INTO ia_registro ({', '.join(fila)}) VALUES ({', '.join('?' * len(fila))})", tuple(fila.values()))
            except Exception:  # base sin la migración 0010: se registra lo de siempre
                con.execute(f"INSERT INTO ia_registro ({', '.join(base)}) VALUES ({', '.join('?' * len(base))})", tuple(base.values()))
    except Exception:
        pass


def avisos_de_contexto(limite=20):
    """Propuesta 20: los últimos pedidos que quedaron cerca del límite de la ventana o no entraron (Administración → IA)."""
    try:
        with db._conexion() as con:
            filas = [dict(f) for f in con.execute(
                "SELECT id, nota_id, tarea, proceso, modelo, num_ctx, tokens_estimados, uso_ctx, aviso_ctx, fecha FROM ia_registro "
                "WHERE aviso_ctx IS NOT NULL ORDER BY id DESC LIMIT ?", (limite,))]
    except Exception:
        return []
    for f in filas:
        f["tarea_etiqueta"] = TAREAS.get(f["tarea"], (f["tarea"] or "").capitalize())
    return filas


def registrar_revision(nota_id, decisiones, usuario):
    """Qué hizo la persona con lo que propuso la IA (solo los párrafos donde intervino la IA)."""
    de_ia = [d for d in decisiones if any(str(f).lower().startswith("ia") for f in (d.get("fuentes") or []))]
    if not de_ia:
        return None
    cuenta = {"aceptar": 0, "modificar": 0, "rechazar": 0}
    for d in de_ia:
        cuenta[d.get("decision", "aceptar")] = cuenta.get(d.get("decision", "aceptar"), 0) + 1
    texto = (f"{cuenta['aceptar']} párrafo(s) aceptados tal cual, {cuenta['modificar']} editados por la persona y "
             f"{cuenta['rechazar']} rechazados (de {len(de_ia)} con propuesta de la IA).")
    try:
        with db._conexion() as con:
            con.execute("INSERT INTO ia_registro (nota_id, tarea, salida, ok, usuario, fecha) VALUES (?,?,?,?,?,?)",
                        (nota_id, "REVISION_HUMANA", texto, 1, (usuario or {}).get("nombre"), _ahora()))
    except Exception:
        pass
    return cuenta


def de_nota(nota_id, limite=100):
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT * FROM ia_registro WHERE nota_id = ? ORDER BY id DESC LIMIT ?", (nota_id, limite))]
    for f in filas:
        f["tarea_etiqueta"] = TAREAS.get(f["tarea"], (f["tarea"] or "").capitalize())
    return filas


def resumen(desde=None, hasta=None):
    """Por tarea: llamadas, fallidas y tiempo medio (para Administración)."""
    sql, p = "SELECT tarea, COUNT(*) AS n, SUM(1 - ok) AS fallidas, AVG(duracion_ms) AS ms FROM ia_registro WHERE tarea != 'REVISION_HUMANA'", []
    if desde:
        sql += " AND fecha >= ?"
        p.append(desde)
    if hasta:
        sql += " AND fecha <= ?"
        p.append(hasta + "T23:59:59")
    with db._conexion() as con:
        return [dict(f, tarea_etiqueta=TAREAS.get(f["tarea"], f["tarea"])) for f in con.execute(sql + " GROUP BY tarea ORDER BY n DESC", p)]
