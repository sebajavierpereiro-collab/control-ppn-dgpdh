"""Reporte de actividad por usuario y día (solo Dirección).

Se arma con lo que el sistema ya registra: ingresos (login correcto),
accesos a documentos y acciones sobre notas (historial). No hay "cierre de
sesión" registrado, así que el tiempo conectado es una ESTIMACIÓN: del primer
al último movimiento del día (con un corte si hay más de 60 min sin actividad).
"""
from datetime import datetime, timedelta, timezone

import database as db
import fechas

CORTE_INACTIVIDAD = timedelta(minutes=60)


def _dt(valor):
    try:
        d = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(fechas.zona())


def _rango_utc(desde, hasta):
    z = fechas.zona()
    d = datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=z)
    h = datetime.strptime(hasta, "%Y-%m-%d").replace(tzinfo=z) + timedelta(days=1)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds"), h.astimezone(timezone.utc).isoformat(timespec="seconds")


def generar(desde=None, hasta=None):
    hoy = datetime.now(fechas.zona()).date()
    hasta = hasta or hoy.isoformat()
    desde = desde or (hoy - timedelta(days=6)).isoformat()
    ini, fin = _rango_utc(desde, hasta)
    eventos = []  # (nombre, datetime, tipo)
    with db._conexion() as con:
        nombres = {f["usuario"].lower(): f["nombre"] for f in con.execute("SELECT usuario, nombre FROM usuarios")}
        for f in con.execute("SELECT usuario, fecha FROM intentos_login WHERE exito = 1 AND fecha >= ? AND fecha < ?", (ini, fin)):
            eventos.append((nombres.get(f["usuario"], f["usuario"]), f["fecha"], "ingreso"))
        for f in con.execute("SELECT usuario, fecha FROM accesos_documentos WHERE fecha >= ? AND fecha < ?", (ini, fin)):
            eventos.append((f["usuario"] or "Sistema", f["fecha"], "acceso"))
        for f in con.execute("SELECT usuario, fecha FROM historial_estados WHERE fecha >= ? AND fecha < ?", (ini, fin)):
            eventos.append((f["usuario"] or "Sistema", f["fecha"], "accion"))
    grupos = {}
    for nombre, fecha, tipo in eventos:
        if nombre == "Sistema":
            continue
        d = _dt(fecha)
        if d is None:
            continue
        grupos.setdefault((nombre, d.date().isoformat()), []).append((d, tipo))
    filas = []
    for (nombre, dia), lst in grupos.items():
        lst.sort(key=lambda x: x[0])
        conectado = timedelta()
        for (a, _), (b, _) in zip(lst, lst[1:]):
            if b - a <= CORTE_INACTIVIDAD:
                conectado += b - a
        filas.append({
            "usuario": nombre, "dia": dia,
            "ingresos": sum(1 for _, t in lst if t == "ingreso"),
            "primero": lst[0][0].strftime("%H:%M"), "ultimo": lst[-1][0].strftime("%H:%M"),
            "minutos": int(conectado.total_seconds() // 60),
            "accesos": sum(1 for _, t in lst if t == "acceso"),
            "acciones": sum(1 for _, t in lst if t == "accion"),
        })
    filas.sort(key=lambda f: (f["dia"], f["usuario"]), reverse=True)
    return {"desde": desde, "hasta": hasta, "filas": filas}
