"""Ingreso con la cuenta del portal PPN (SSO del organismo, el mismo que da acceso a Expedientes y a Fichas de reclamo).

Cómo funciona el portal (ppn-sso):
  1. La persona entra al portal con su usuario PPN y elige esta aplicación.
  2. El portal la manda a  {url de esta app}/auth/ingreso/{token}  (el token dura un día).
  3. Esta app consulta  POST {sso}/api/application-session/get-session  con las cabeceras
     `authorization: <token>` y `application-id: <uuid de esta app en el portal>` y recibe la
     persona (nombre, correo, área) y su rol en esta aplicación.
  4. Al salir se avisa a  /api/application-session/logout ; mientras tanto se puede verificar
     que la sesión siga viva con  /check-session .
  Además el portal expone  GET /api/sync/users  y  /api/sync/areas  (con `application-id`) para
  traer la lista de personas habilitadas en esta aplicación y las áreas del organismo.

Criterio: el portal solo IDENTIFICA a la persona. Qué puede hacer adentro (rol, equipo) lo sigue
decidiendo la Administración de esta app. Si la persona no tiene usuario acá, no entra sola: queda
en la lista de «pendientes de vincular» para que alguien de la Dirección la habilite.

Configuración (config.ini):
  [sso]
  url = https://sso.ppn.gov.ar          ; dirección del backend del portal (sin / al final)
  aplicacion_id =                       ; uuid de esta aplicación en el portal
  portal_url = https://portal.ppn.gov.ar; dirección que ve la gente (para el botón «Ingresar con el portal»)
  verificar_cada_minutos = 10           ; cada cuánto se confirma que la sesión del portal sigue abierta
"""
import json
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.request

from pathlib import Path

import database as db

RUTA_AREAS = Path(__file__).resolve().parent / "datos_semilla" / "areas_portal_ppn.json"

URL = ""
APLICACION_ID = ""
PORTAL_URL = ""
VERIFICAR_CADA = 600
TIEMPO_ESPERA = 8


def configurar(config):
    global URL, APLICACION_ID, PORTAL_URL, VERIFICAR_CADA
    URL = (config.get("sso", "url", fallback="") or "").strip().rstrip("/")
    APLICACION_ID = (config.get("sso", "aplicacion_id", fallback="") or "").strip()
    PORTAL_URL = (config.get("sso", "portal_url", fallback="") or "").strip().rstrip("/")
    try:
        VERIFICAR_CADA = max(60, int(config.get("sso", "verificar_cada_minutos", fallback="10")) * 60)
    except ValueError:
        VERIFICAR_CADA = 600


def activo():
    return bool(URL and APLICACION_ID)


class ErrorPortal(Exception):
    """El portal no respondió o rechazó el pedido."""


def _pedir(metodo, ruta, token=None, abrir=urllib.request.urlopen):
    cabeceras = {"application-id": APLICACION_ID, "Accept": "application/json"}
    if token:
        cabeceras["authorization"] = token
    pedido = urllib.request.Request(URL + ruta, method=metodo, headers=cabeceras, data=b"" if metodo == "POST" else None)
    try:
        with abrir(pedido, timeout=TIEMPO_ESPERA) as r:
            cuerpo = r.read().decode("utf-8") or "null"
            return r.status, json.loads(cuerpo)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "null")
        except Exception:
            return e.code, None
    except Exception as e:  # sin red, DNS, certificado, tiempo agotado...
        raise ErrorPortal(f"No se pudo consultar el portal PPN: {e}") from e


# Las funciones de red se pueden reemplazar en las pruebas.
_abrir = urllib.request.urlopen


def obtener_sesion(token):
    """Datos de la persona para un token del portal, o None si el token no sirve."""
    estado, datos = _pedir("POST", "/api/application-session/get-session", token, _abrir)
    if estado != 200 or not datos or not datos.get("user"):
        return None
    u = datos["user"]
    area = datos.get("area") or u.get("area") or {}
    return {
        "sso_id": u.get("id"),
        "nombre": " ".join(x for x in (u.get("firstname"), u.get("lastname")) if x).strip(),
        "correo": (u.get("email") or "").strip().lower(),
        "habilitado": bool(u.get("isEnabled", True)),
        "area": area.get("name") or "",
        "area_id": area.get("id"),
        "rol_portal": (datos.get("role") or {}).get("name") or "",
        "permisos_portal": [p.get("name") for p in datos.get("permissions") or [] if p.get("name")],
    }


def sesion_viva(token):
    """True/False según el portal; None si no se pudo consultar (en ese caso no se corta la sesión)."""
    try:
        estado, _ = _pedir("POST", "/api/application-session/check-session", token, _abrir)
    except ErrorPortal:
        return None
    return estado == 200


def cerrar_sesion(token):
    try:
        _pedir("POST", "/api/application-session/logout", token, _abrir)
    except ErrorPortal as e:
        logging.info(str(e))


def personas_del_portal():
    """Personas habilitadas en esta aplicación según el portal: [{sso_id, nombre, correo, area, rol_portal, habilitado}]."""
    estado, datos = _pedir("GET", "/api/sync/users", abrir=_abrir)
    if estado != 200 or datos is None:
        raise ErrorPortal("El portal no devolvió la lista de personas (revisá el ID de aplicación).")
    lista = datos.get("users") if isinstance(datos, dict) else datos
    salida = []
    for u in lista or []:
        area = u.get("area") or {}
        rol = u.get("role") or {}
        salida.append({"sso_id": u.get("id"), "nombre": " ".join(x for x in (u.get("firstname"), u.get("lastname")) if x).strip(),
                       "correo": (u.get("email") or "").strip().lower(), "habilitado": bool(u.get("isEnabled", True)),
                       "area": area.get("name") or "", "rol_portal": rol.get("name") or ""})
    return salida


def areas_del_portal():
    estado, datos = _pedir("GET", "/api/sync/areas", abrir=_abrir)
    if estado != 200 or datos is None:
        raise ErrorPortal("El portal no devolvió las áreas.")
    return [{"id": a.get("id"), "nombre": (a.get("name") or "").strip(), "activa": bool(a.get("active", True)),
             "sede": a.get("headquarter") or ""} for a in (datos.get("areas") if isinstance(datos, dict) else datos) or []]


# ---------------------------------------------------------------------
# Vinculación con los usuarios de esta app
# ---------------------------------------------------------------------
def _norm(texto):
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\bn\s*[°ºo]\s*", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(t.split())


_PALABRAS_VACIAS = {"equipo", "de", "del", "la", "el", "los", "las", "y", "area", "federal", "complejo", "penitenciario", "n"}


def equipo_para_area(nombre_area, equipos):
    """Sugiere el equipo de esta app que corresponde a un área del portal (solo sugerencia; decide una persona)."""
    na = _norm(nombre_area)
    if not na:
        return None
    for e in equipos:
        if _norm(e["nombre"]) == na:
            return e
    ta = set(na.split()) - _PALABRAS_VACIAS
    mejor, puntaje = None, 0.0
    for e in equipos:
        te = set(_norm(e["nombre"]).split()) - _PALABRAS_VACIAS
        if not te or not ta:
            continue
        p = len(ta & te) / len(ta | te)
        if p > puntaje:
            mejor, puntaje = e, p
    return mejor if puntaje >= 0.5 else None


def usuario_local_para(persona):
    """Usuario de esta app que corresponde a la persona del portal: primero por vínculo guardado, después por correo."""
    if persona.get("sso_id") is not None:
        u = db.usuario_por_sso_id(persona["sso_id"])
        if u:
            return u, "vinculado"
    if persona.get("correo"):
        u = db.usuario_por_correo(persona["correo"])
        if u:
            return u, "correo"
    return None, None


def cruce(personas, usuarios, equipos):
    """Cuadro para Administración: cada persona del portal con su usuario local (si hay) y el equipo sugerido."""
    por_sso = {u.get("sso_id"): u for u in usuarios if u.get("sso_id") is not None}
    por_correo = {(u.get("correo") or "").lower(): u for u in usuarios if u.get("correo")}
    filas = []
    for p in sorted(personas, key=lambda x: _norm(x["nombre"])):
        u = por_sso.get(p["sso_id"])
        como = "vinculado" if u else None
        if not u and p["correo"] in por_correo:
            u, como = por_correo[p["correo"]], "correo"
        eq = equipo_para_area(p["area"], equipos)
        filas.append(dict(p, usuario=u, como=como, equipo_sugerido=eq))
    return filas


# ---------------------------------------------------------------------
# Preparación (sin conexión): qué falta para poder vincular el día que se decida
# ---------------------------------------------------------------------
def areas_locales():
    """Copia de referencia de las áreas del portal (tomada de su código), para asignar equipos sin conectarse."""
    try:
        return json.loads(RUTA_AREAS.read_text(encoding="utf-8")).get("areas", [])
    except Exception:
        return []


def preparacion(usuarios, equipos, areas=None):
    """Chequeo de preparación: correos (con los que el portal reconoce a cada persona) y equipos con su área del portal."""
    areas = areas if areas is not None else areas_locales()
    por_id = {a["id"]: a for a in areas}
    activos = [u for u in usuarios if u.get("activo")]
    sin_correo = [u for u in activos if not (u.get("correo") or "").strip()]
    vistos, repetidos = {}, []
    for u in activos:
        c = (u.get("correo") or "").strip().lower()
        if c:
            vistos.setdefault(c, []).append(u)
    repetidos = [(c, us) for c, us in vistos.items() if len(us) > 1]
    filas_eq = []
    for e in equipos:
        actual = por_id.get(e.get("sso_area_id")) if e.get("sso_area_id") else None
        sugerida = None if actual else equipo_para_area(e["nombre"], [{"id": a["id"], "nombre": a["nombre"]} for a in areas if a.get("activa", True)])
        filas_eq.append({"equipo": e, "area": actual, "sugerida": sugerida})
    sin_area = [f for f in filas_eq if not f["area"]]
    total = len(activos) + len(equipos)
    listos = (len(activos) - len(sin_correo)) + (len(equipos) - len(sin_area))
    return {"sin_correo": sin_correo, "repetidos": repetidos, "equipos": filas_eq, "sin_area": sin_area,
            "areas": sorted(areas, key=lambda a: _norm(a["nombre"])), "porcentaje": round(100 * listos / total) if total else 100,
            "activos": len(activos)}
