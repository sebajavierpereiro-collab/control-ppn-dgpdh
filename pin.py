"""Ola 5 · Reforma 93: segundo factor para Dirección y Mesa de Entradas.

Decisión del organismo: un PIN de 4 números (sin celular). Se pide UNA sola vez por sesión, la primera vez que la
persona firma una nota (Dirección) o despacha (Mesa de Entradas). Después de confirmarlo, el resto de la sesión no se
vuelve a pedir; al salir o al cerrarse la sesión por inactividad, se vuelve a pedir.

  * El PIN se guarda con hash + salt (igual que la clave), nunca en texto.
  * 5 intentos fallidos seguidos bloquean el PIN por 15 minutos y avisan a la Dirección.
  * Si alguien lo olvida, Administración lo borra y la persona crea uno nuevo en la próxima firma.
"""
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

import auth
import database as db

ROLES_CON_PIN = auth.ROLES_DIRECCION | auth.ROLES_MESA
MAX_INTENTOS = 5
MINUTOS_BLOQUEO = 15
_ITERACIONES = 200_000
_RUTAS = re.compile(r"^/(nota/\d+/(firmar|firmar-y-seguir|despachar)|bloque/\d+/despachar|despacho/despachar)$")
CLAVE_SESION = "pin_ok"


def requiere(usuario):
    return bool(usuario) and usuario.get("rol") in ROLES_CON_PIN


def ruta_protegida(metodo, ruta):
    return metodo == "POST" and bool(_RUTAS.match(ruta or ""))


def tiene_pin(usuario):
    return bool(usuario and usuario.get("pin_hash"))


def sesion_confirmada(sesion, usuario):
    return bool(usuario) and sesion.get(CLAVE_SESION) == usuario["id"]


def _hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), _ITERACIONES).hex()


def validar_nuevo(pin, repetido=None):
    pin = (pin or "").strip()
    if not re.fullmatch(r"\d{4}", pin):
        return "El PIN tiene que ser de 4 números."
    if repetido is not None and pin != (repetido or "").strip():
        return "Los dos PIN no coinciden."
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        return "Ese PIN es muy fácil de adivinar (números iguales o seguidos). Elegí otro."
    return None


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _evento(usuario, tipo, detalle="", ip=None):
    with db._conexion() as con:
        con.execute("INSERT INTO seguridad_eventos (usuario_id, usuario, tipo, detalle, ip, fecha) VALUES (?,?,?,?,?,?)",
                    (usuario["id"] if usuario else None, (usuario or {}).get("usuario"), tipo, detalle, ip, _ahora()))


def crear(usuario, pin, repetido, ip=None):
    """Crea (o reemplaza) el PIN. Devuelve un mensaje de error o None."""
    error = validar_nuevo(pin, repetido)
    if error:
        return error
    salt = os.urandom(16).hex()
    with db._conexion() as con:
        con.execute("UPDATE usuarios SET pin_hash = ?, pin_salt = ?, pin_creado_en = ? WHERE id = ?",
                    (_hash(pin.strip(), salt), salt, _ahora(), usuario["id"]))
    _evento(usuario, "PIN_CREADO", ip=ip)
    return None


def _fallidos_recientes(usuario_id):
    """Intentos fallidos seguidos (desde el último acierto o creación) y la fecha del último."""
    with db._conexion() as con:
        filas = con.execute("SELECT tipo, fecha FROM seguridad_eventos WHERE usuario_id = ? AND tipo IN "
                            "('PIN_OK', 'PIN_FALLIDO', 'PIN_CREADO', 'PIN_BORRADO') ORDER BY id DESC LIMIT 20", (usuario_id,)).fetchall()
    n, ultimo = 0, None
    for f in filas:
        if f["tipo"] != "PIN_FALLIDO":
            break
        n += 1
        ultimo = ultimo or f["fecha"]
    return n, ultimo


def bloqueado_hasta(usuario):
    n, ultimo = _fallidos_recientes(usuario["id"])
    if n < MAX_INTENTOS or not ultimo:
        return None
    hasta = datetime.fromisoformat(ultimo) + timedelta(minutes=MINUTOS_BLOQUEO)
    return hasta if hasta > datetime.now(timezone.utc) else None


def verificar(usuario, pin, ip=None):
    """Devuelve (ok, mensaje)."""
    if not tiene_pin(usuario):
        return False, "Todavía no creaste tu PIN."
    hasta = bloqueado_hasta(usuario)
    if hasta:
        minutos = max(1, int((hasta - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
        return False, f"Demasiados intentos fallidos. Probá de nuevo en {minutos} minuto(s) o pedí a Administración que borre tu PIN."
    if re.fullmatch(r"\d{4}", (pin or "").strip()) and _hash(pin.strip(), usuario["pin_salt"]) == usuario["pin_hash"]:
        _evento(usuario, "PIN_OK", ip=ip)
        return True, ""
    _evento(usuario, "PIN_FALLIDO", ip=ip)
    n, _ = _fallidos_recientes(usuario["id"])
    if n >= MAX_INTENTOS:
        try:
            directores = [u["id"] for u in db.listar_usuarios() if u["activo"] and u["rol"] in auth.ROLES_DIRECCION and u["id"] != usuario["id"]]
            db.notificar_usuarios(directores, f"El PIN de firma de {usuario['nombre']} quedó bloqueado por {MAX_INTENTOS} intentos fallidos.",
                                  "/admin/seguridad/pin")
        except Exception:
            pass
        return False, f"PIN incorrecto. Se bloqueó por {MINUTOS_BLOQUEO} minutos."
    return False, f"PIN incorrecto. Te quedan {MAX_INTENTOS - n} intento(s)."


def borrar(usuario_id, por=None):
    with db._conexion() as con:
        con.execute("UPDATE usuarios SET pin_hash = NULL, pin_salt = NULL, pin_creado_en = NULL WHERE id = ?", (usuario_id,))
    u = db.obtener_usuario(usuario_id)
    if u:
        _evento(u, "PIN_BORRADO", f"por {por['nombre']}" if por else "")


def estado_usuarios():
    """Para Administración: quién tiene que usar PIN, si ya lo creó y si está bloqueado."""
    salida = []
    for u in db.listar_usuarios():
        if not u["activo"] or u["rol"] not in ROLES_CON_PIN:
            continue
        completo = db.obtener_usuario(u["id"]) or u
        salida.append({"id": u["id"], "nombre": u["nombre"], "rol": auth.ETIQUETAS_ROL.get(u["rol"], u["rol"]),
                       "tiene": tiene_pin(completo), "creado": (completo.get("pin_creado_en") or "")[:10],
                       "bloqueado": bool(tiene_pin(completo) and bloqueado_hasta(completo))})
    return salida


def eventos_recientes(limite=30):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM seguridad_eventos ORDER BY id DESC LIMIT ?", (limite,))]
