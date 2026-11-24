"""
auth.py — Usuarios, roles, equipos y contraseñas.

Modelo de organización (definido por la institución, no por este código):
- Equipos: cada equipo tiene asesores y coordinadores.
- Dirección General: Director y Subdirector, cada uno con firma individual.
- Mesa de Entrada: jefe, coordinador y asesores propios.

El login es local (usuario + clave guardados en esta misma base SQLite, con
contraseña con hash + salt — no en texto plano). No hay integración con
ningún directorio externo: cada persona tiene su usuario y clave, su rol y
su equipo (ver database.py, tabla `usuarios`).
"""
import hashlib
import os

# ---------------------------------------------------------------------
# ROLES
# ---------------------------------------------------------------------
ROL_ASESOR = "ASESOR"
ROL_COORDINADOR = "COORDINADOR"
ROL_DIRECTOR = "DIRECTOR"
ROL_SUBDIRECTOR = "SUBDIRECTOR"
ROL_MESA_JEFE = "MESA_JEFE"
ROL_MESA_COORDINADOR = "MESA_COORDINADOR"
ROL_MESA_ASESOR = "MESA_ASESOR"
ROL_CHOFER = "CHOFER"

ROLES = (
    ROL_ASESOR,
    ROL_COORDINADOR,
    ROL_DIRECTOR,
    ROL_SUBDIRECTOR,
    ROL_MESA_JEFE,
    ROL_MESA_COORDINADOR,
    ROL_MESA_ASESOR,
    ROL_CHOFER,
)

ETIQUETAS_ROL = {
    ROL_ASESOR: "Asesor",
    ROL_COORDINADOR: "Coordinador",
    ROL_DIRECTOR: "Director General",
    ROL_SUBDIRECTOR: "Subdirectora/Subdirector General",
    ROL_MESA_JEFE: "Mesa de Entrada — Jefe/a",
    ROL_MESA_COORDINADOR: "Mesa de Entrada — Coordinador/a",
    ROL_MESA_ASESOR: "Mesa de Entrada — Asesor/a",
    ROL_CHOFER: "Chofer",
}

# Los Directores Generales (Director y Subdirector) tienen permiso total:
# administración de usuarios/equipos y visibilidad de todas las notas.
ROLES_DIRECCION = frozenset({ROL_DIRECTOR, ROL_SUBDIRECTOR})
ROLES_MESA = frozenset({ROL_MESA_JEFE, ROL_MESA_COORDINADOR, ROL_MESA_ASESOR})
# Mesa de Entradas es un ÁREA (jefe, coordinador y asesores propios). Sus funciones (números, despacho, envíos,
# numeración, «Solo Mesa de Entradas») las ejerce esa área; la Dirección General (Director y Subdirector/a, con las
# mismas facultades) —y cualquier perfil superior que se cree: agregarlo acá— puede ejercerlas EXCEPCIONALMENTE,
# y queda registrado en el historial como excepcional.
ROLES_MESA_EXCEPCIONAL = frozenset({ROL_DIRECTOR, ROL_SUBDIRECTOR})


def funciones_mesa(usuario):
    """¿Puede ejercer funciones de Mesa de Entradas? (el área, o excepcionalmente el Director General)."""
    return bool(usuario) and (usuario["rol"] in ROLES_MESA or usuario["rol"] in ROLES_MESA_EXCEPCIONAL)


def mesa_excepcional(usuario):
    """Las ejerce sin ser del área: se registra como excepcional."""
    return bool(usuario) and usuario["rol"] not in ROLES_MESA and usuario["rol"] in ROLES_MESA_EXCEPCIONAL


# Roles que pertenecen a un Equipo (asesores/coordinadores "de equipo",
# distinto de Mesa de Entrada y de Dirección General).
ROLES_DE_EQUIPO = frozenset({ROL_ASESOR, ROL_COORDINADOR})

# Roles que tienen una firma individual propia (ver nucleo.FIRMAS_POR_ROL).
ROLES_CON_FIRMA_PROPIA = frozenset({ROL_DIRECTOR, ROL_SUBDIRECTOR})


def es_admin(usuario):
    """Los Directores Generales (Director y Subdirector) administran
    usuarios y equipos desde el panel /admin."""
    return bool(usuario) and usuario["rol"] in ROLES_DIRECCION


# ---------------------------------------------------------------------
# PRIORIDAD DE LAS NOTAS
# ---------------------------------------------------------------------
# Opcional al subir; si no se indica, la nota es NORMAL.
PRIORIDAD_NORMAL = "NORMAL"
PRIORIDADES = ("URGENTE", "PRIORITARIA", "NORMAL")
ETIQUETAS_PRIORIDAD = {"URGENTE": "URGENTE", "PRIORITARIA": "Prioritaria", "NORMAL": "Normal"}
# Menor número = más urgente (para ordenar).
RANGO_PRIORIDAD = {"URGENTE": 0, "PRIORITARIA": 1, "NORMAL": 2}

CLAVE_MINIMO_CARACTERES = 8
# Claves que no se aceptan nunca (la de fábrica del Director y las típicas).
CLAVES_PROHIBIDAS = {"director123", "12345678", "123456789", "password", "contraseña", "qwertyui", "dgpdh2024"}


def validar_clave_nueva(clave, usuario=None):
    """Devuelve un mensaje de error si la clave no cumple la política, o None."""
    if len(clave) < CLAVE_MINIMO_CARACTERES:
        return f"La clave tiene que tener al menos {CLAVE_MINIMO_CARACTERES} caracteres."
    if clave.lower() in CLAVES_PROHIBIDAS or clave.lower() == (usuario or "").lower():
        return "Esa clave es demasiado fácil de adivinar. Elegí otra."
    if clave.isdigit() or clave.isalpha():
        return "Combiná letras con números u otros símbolos."
    return None


# ---------------------------------------------------------------------
# CONTRASEÑAS (hash + salt, sin dependencias externas)
# ---------------------------------------------------------------------
_ITERACIONES = 200_000


def generar_hash_clave(clave_en_texto):
    """Devuelve (hash_hex, salt_hex). PBKDF2-HMAC-SHA256 de la librería
    estándar: no hace falta bcrypt/passlib para un volumen de usuarios tan
    chico (una institución)."""
    salt = os.urandom(16)
    hash_bytes = hashlib.pbkdf2_hmac("sha256", clave_en_texto.encode("utf-8"), salt, _ITERACIONES)
    return hash_bytes.hex(), salt.hex()


def verificar_clave(clave_en_texto, hash_hex, salt_hex):
    salt = bytes.fromhex(salt_hex)
    hash_bytes = hashlib.pbkdf2_hmac("sha256", clave_en_texto.encode("utf-8"), salt, _ITERACIONES)
    return hash_bytes.hex() == hash_hex
