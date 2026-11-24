"""Estado breve de los paneles destacados del menú lateral (Autos PPN, Metas y Personas detenidas).

Se muestra en cada página, así que es liviano y queda en memoria unos minutos por persona. Si algo falla, el menú
muestra el panel sin la línea de estado (nunca rompe la página)."""
import logging
import threading
import time
from datetime import date

import database as db

_CACHE = {}
DURACION = {"autos": 60, "metas": 600, "detenidos": 300, "obstaculizaciones": 300}
# Metas recorre todos los viajes del año (cientos de consultas): nunca se calcula mientras alguien espera la página.
EN_SEGUNDO_PLANO = {"metas"}
_EN_CURSO = set()
_LOCK = threading.Lock()


def _calcular(clave, fn):
    try:
        res = fn()
    except Exception:
        logging.exception(f"Estado del menú ({clave[0]}) no disponible")
        res = None
    with _LOCK:
        if len(_CACHE) > 500:
            _CACHE.clear()
        _CACHE[clave] = (time.monotonic(), res)
        _EN_CURSO.discard(clave)
    return res


def _cache(clave, segundos, fn):
    ahora = time.monotonic()
    v = _CACHE.get(clave)
    if v and ahora - v[0] < segundos:
        return v[1]
    if clave[0] in EN_SEGUNDO_PLANO:
        # Se muestra lo último que se calculó (o nada la primera vez) y se actualiza aparte.
        with _LOCK:
            lanzar = clave not in _EN_CURSO
            _EN_CURSO.add(clave)
        if lanzar:
            threading.Thread(target=_calcular, args=(clave, fn), name=f"menu-{clave[0]}", daemon=True).start()
        return v[1] if v else None
    return _calcular(clave, fn)


def _autos(u):
    import traslados as tr
    h = date.today().isoformat()
    coord = tr.puede_coordinar(u)
    hoy = [v for v in tr.listar_viajes(desde=h, hasta=h, usuario_id=None if coord else u["id"]) if v["estado"] != "CANCELADO"]
    texto = f"{len(hoy)} viaje(s) hoy" if hoy else "Sin viajes hoy"
    alerta = False
    if coord:
        sin_chofer = len(tr.listar_viajes(desde=h, estado="SOLICITADO"))
        if sin_chofer:
            texto += f" · {sin_chofer} sin chofer"
            alerta = True
    return {"texto": texto, "alerta": alerta}


def _metas(u):
    import auth
    import metas
    anio = date.today().year
    equipos = [e for e in db.listar_equipos() if auth.es_admin(u) or e["id"] == u.get("equipo_id")]
    if not equipos:
        return None
    seg = metas.seguimiento(anio, equipos)["equipos"]
    avances = [e["avance"] for e in seg if e["avance"] is not None]
    if not avances:
        return {"texto": "Sin metas cargadas", "alerta": False}
    atrasadas = sum(e["resumen"].get("atrasada", 0) for e in seg)
    prom = round(sum(avances) / len(avances))
    texto = (f"Avance {prom} %" if len(seg) == 1 else f"Avance {prom} % (promedio)") + (f" · {atrasadas} atrasada(s)" if atrasadas else "")
    return {"texto": texto, "alerta": bool(atrasadas)}


def _detenidos(u):
    import detenidos as det
    if det.puede_mantener(u):
        n = det.cantidad_alertas()
        return {"texto": f"{n} alerta(s) por revisar" if n else "Base sin alertas", "alerta": bool(n)}
    c = det.contar()
    return {"texto": f"{c['activos']} persona(s) alojada(s)", "alerta": False}


# Lo completa rutas_obstaculizaciones al registrarse (necesita saber quién tiene acceso a Notas).
OBSTACULIZACIONES = None


def _obstaculizaciones(u):
    """Registros que la persona puede ver, cargados este mes (respeta la misma visibilidad que el Registro)."""
    if OBSTACULIZACIONES is None:
        return None
    n = OBSTACULIZACIONES(u, date.today().replace(day=1).isoformat())
    if n is None:
        return None
    return {"texto": f"{n} registro(s) este mes" if n else "Sin registros este mes", "alerta": False}


def estado(usuario):
    """{'autos': {...}|None, 'metas': ..., 'detenidos': ...} para la persona (con caché)."""
    if not usuario:
        return {}
    uid = usuario["id"]
    return {"autos": _cache(("autos", uid), DURACION["autos"], lambda: _autos(usuario)),
            "metas": _cache(("metas", uid if not usuario.get("rol") in ("DIRECTOR", "SUBDIRECTOR") else "dir"), DURACION["metas"], lambda: _metas(usuario)),
            "detenidos": _cache(("detenidos", uid), DURACION["detenidos"], lambda: _detenidos(usuario)),
            "obstaculizaciones": _cache(("obstaculizaciones", uid), DURACION["obstaculizaciones"], lambda: _obstaculizaciones(usuario))}


def limpiar():
    _CACHE.clear()
