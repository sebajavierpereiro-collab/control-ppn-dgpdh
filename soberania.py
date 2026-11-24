"""Política de aislamiento y soberanía de datos — «Modo Seguro Institucional».

Requisito permanente: el sistema nunca transmite información al exterior de forma automática.

Cómo se hace cumplir (no depende de que cada módulo se acuerde):
  * Se intercepta TODA conexión de red que abre el proceso (a nivel de socket: requests, urllib, correo, descargas).
    Las conexiones a la red interna (localhost, IP privadas, nombres sin dominio y los dominios internos configurados)
    pasan. Cualquier otra se bloquea y queda registrada, salvo que el destino esté autorizado expresamente en
    config.ini ([seguridad] destinos_autorizados) — y aun así cada conexión queda registrada.
  * Con el modo seguro activo (por defecto) se bloquean además: respaldos en carpetas de nube pública (OneDrive,
    Google Drive, Dropbox, iCloud, Box), IA remota y cualquier integración no autorizada.
  * Una función futura que necesite salir al exterior por pedido de una persona debe usar `salida_confirmada(...)`:
    muestra antes qué datos se envían y a dónde, pide confirmación expresa, y registra usuario, fecha, destino, tipo de
    información y motivo. No hay envíos silenciosos ni en segundo plano.

Desactivar el modo seguro solo es posible desde config.ini ([seguridad] modo_seguro_institucional = false), nunca
desde la pantalla, y aun así las conexiones externas automáticas siguen necesitando un destino autorizado.
"""
import contextlib
import contextvars
import ipaddress
import logging
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

CONFIG = {"modo_seguro": True, "dominios_internos": [], "destinos_autorizados": [], "sistemas_organismo": [], "instalado": False}
_ORIG = {}
_PERMISO = contextvars.ContextVar("salida_confirmada", default=None)
_LOCAL = threading.local()
_IPS_AUTORIZADAS = {}
CARPETAS_NUBE = ("onedrive", "google drive", "googledrive", "mi unidad", "my drive", "dropbox", "icloud", "icloud drive", "box sync", "box")
TIPOS = {"RED": "Conexión de red", "RESPALDO": "Respaldo", "IA": "IA remota", "CORREO": "Correo", "INTEGRACION": "Integración"}


class SalidaBloqueada(ConnectionError):
    """El Modo Seguro Institucional bloqueó una conexión o copia al exterior."""


def configurar(config):
    try:
        CONFIG["modo_seguro"] = config.getboolean("seguridad", "modo_seguro_institucional", fallback=True)
        lista = lambda k: [x.strip().lower().lstrip(".") for x in config.get("seguridad", k, fallback="").replace(";", ",").split(",") if x.strip()]
        CONFIG["dominios_internos"] = lista("dominios_internos")
        CONFIG["destinos_autorizados"] = lista("destinos_autorizados")
    except Exception:
        CONFIG["modo_seguro"] = True
    _IPS_AUTORIZADAS.clear()


# ---------------------------------------------------------------- clasificación de destinos
_REDES_INTERNAS = [ipaddress.ip_network(r) for r in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
                                                      "::1/128", "fc00::/7", "fe80::/10")]


def _ip_interna(ip):
    """Solo las redes privadas de verdad (RFC 1918, loopback, enlace local); no los rangos reservados de documentación."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return any(a in r for r in _REDES_INTERNAS if r.version == a.version)


def _coincide(host, dominios):
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in dominios)


def es_interno(host):
    """True si el destino es de la red del organismo: localhost, IP privada, nombre sin dominio (servidor de la red
    local, p. ej. SRV-DGPDH) o un dominio interno configurado."""
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host:
        return True
    if host in ("localhost",) or _ip_interna(host):
        return True
    if "." not in host and ":" not in host:
        return True
    return _coincide(host, CONFIG["dominios_internos"])


def autorizado(host):
    return _coincide(host, CONFIG["destinos_autorizados"]) or _coincide(host, CONFIG["sistemas_organismo"])


def autorizar_sistemas_del_organismo(urls):
    """Los sistemas propios del organismo que la institución configuró expresamente en config.ini (portal PPN, Mesa de
    Entradas, almacén de archivos, sistema de la Procuración) cuentan como autorizados aunque estén en un dominio
    público; cada conexión queda registrada igual. La IA y el correo NO entran acá: si son externos, se bloquean."""
    CONFIG["sistemas_organismo"] = sorted({_host_de(u) for u in urls if _host_de(u) and not es_interno(_host_de(u))})
    _IPS_AUTORIZADAS.clear()


def clasificar(url_o_host):
    """'interno' | 'autorizado' | 'externo' para mostrar en Administración."""
    host = _host_de(url_o_host)
    if not host:
        return "sin configurar"
    if es_interno(host):
        return "interno"
    return "autorizado" if autorizado(host) else "externo"


def _host_de(url_o_host):
    t = (url_o_host or "").strip()
    if not t:
        return ""
    if "://" in t:
        from urllib.parse import urlsplit
        return (urlsplit(t).hostname or "").lower()
    return t.split("/")[0].rsplit(":", 1)[0].strip("[]").lower() if t.count(":") <= 1 else t.strip("[]").lower()


def _ip_autorizada(ip):
    destinos = CONFIG["destinos_autorizados"] + CONFIG["sistemas_organismo"]
    if not destinos:
        return False
    if not _IPS_AUTORIZADAS:
        for d in destinos:
            try:
                for f in _ORIG.get("getaddrinfo", socket.getaddrinfo)(d, None):
                    _IPS_AUTORIZADAS[f[4][0]] = d
            except Exception:
                continue
    return ip in _IPS_AUTORIZADAS


# ---------------------------------------------------------------- registro (auditoría)
def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS salidas_externas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, usuario TEXT, destino TEXT NOT NULL, tipo TEXT NOT NULL,
        datos TEXT, motivo TEXT, origen TEXT, resultado TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_salidas_fecha ON salidas_externas(fecha)")


def registrar(destino, tipo, resultado, motivo="", datos="", usuario=None, origen=None):
    if getattr(_LOCAL, "registrando", False):
        return
    _LOCAL.registrando = True
    try:
        import database as db
        with db._conexion() as con:
            con.execute("INSERT INTO salidas_externas (fecha, usuario, destino, tipo, datos, motivo, origen, resultado) VALUES (?,?,?,?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), usuario or _usuario_actual(), str(destino)[:300],
                         tipo, str(datos)[:300], str(motivo)[:300], (origen or _origen())[:120], resultado))
    except Exception:
        logging.exception("No se pudo registrar la salida externa")
    finally:
        _LOCAL.registrando = False
    if resultado == "BLOQUEADA":
        logging.warning(f"Modo Seguro Institucional: se bloqueó una salida a {destino} ({TIPOS.get(tipo, tipo)}; {motivo or 'sin motivo'})")


def _usuario_actual():
    try:
        import database as db
        u = db.USUARIO_ACTUAL.get()
        return (u or {}).get("nombre") or "Sistema (automático)"
    except Exception:
        return "Sistema (automático)"


def _origen():
    """Qué módulo de la app pidió la conexión (para saber por qué se intentó)."""
    base = str(Path(__file__).resolve().parent)
    f = sys._getframe(2)
    while f:
        nombre = f.f_code.co_filename
        if nombre.startswith(base) and not nombre.endswith("soberania.py"):
            return f"{Path(nombre).stem}.{f.f_code.co_name}"
        f = f.f_back
    return "desconocido"


def listar(limite=200):
    import database as db
    with db._conexion() as con:
        return [dict(f, tipo_etiqueta=TIPOS.get(f["tipo"], f["tipo"])) for f in con.execute("SELECT * FROM salidas_externas ORDER BY id DESC LIMIT ?", (limite,))]


# ---------------------------------------------------------------- control de cada conexión
def verificar_destino(host, puerto=None, ip=None, tipo="RED", motivo=""):
    """Deja pasar lo interno; lo externo solo con destino autorizado (registrado) o confirmación expresa (registrada)."""
    if (host and es_interno(host)) or (ip and _ip_interna(ip)):
        return True
    destino = f"{host or ip}:{puerto}" if puerto else (host or ip)
    permiso = _PERMISO.get()
    if permiso:
        registrar(destino, permiso["tipo"], "PERMITIDA (confirmada)", permiso["motivo"], permiso["datos"], permiso["usuario"])
        return True
    if (host and autorizado(host)) or (ip and _ip_autorizada(ip)):
        registrar(destino, tipo, "PERMITIDA (destino autorizado)", motivo)
        return True
    registrar(destino, tipo, "BLOQUEADA", motivo or "conexión automática al exterior")
    raise SalidaBloqueada(f"Modo Seguro Institucional: no se permite conectar con {destino} (fuera de la red del organismo).")


def instalar():
    """Intercepta las conexiones del proceso. Idempotente."""
    if CONFIG["instalado"]:
        return
    _ORIG["connect"] = socket.socket.connect
    _ORIG["connect_ex"] = socket.socket.connect_ex
    _ORIG["create_connection"] = socket.create_connection
    _ORIG["getaddrinfo"] = socket.getaddrinfo

    nombres = {}  # ip -> nombre pedido (para registrar y autorizar por nombre)

    def _ip_de(direccion):
        return direccion[0] if isinstance(direccion, tuple) and direccion else None

    def getaddrinfo(host, *a, **k):
        res = _ORIG["getaddrinfo"](host, *a, **k)
        if isinstance(host, str) and host:
            if len(nombres) > 2000:
                nombres.clear()
            for f in res:
                nombres[f[4][0]] = host
        return res

    def _host_para(ip):
        return getattr(_LOCAL, "host", None) or nombres.get(ip) or ip

    def connect(self, direccion):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            ip = _ip_de(direccion)
            verificar_destino(_host_para(ip), direccion[1] if isinstance(direccion, tuple) and len(direccion) > 1 else None, ip)
        return _ORIG["connect"](self, direccion)

    def connect_ex(self, direccion):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            ip = _ip_de(direccion)
            verificar_destino(_host_para(ip), None, ip)
        return _ORIG["connect_ex"](self, direccion)

    def create_connection(direccion, *a, **k):
        host = _ip_de(direccion)
        if host and not es_interno(host):
            verificar_destino(host, direccion[1] if len(direccion) > 1 else None)
        _LOCAL.host = host
        try:
            return _ORIG["create_connection"](direccion, *a, **k)
        finally:
            _LOCAL.host = None

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
    socket.getaddrinfo = getaddrinfo
    CONFIG["instalado"] = True


def desinstalar():
    if not CONFIG["instalado"]:
        return
    socket.socket.connect = _ORIG["connect"]
    socket.socket.connect_ex = _ORIG["connect_ex"]
    socket.create_connection = _ORIG["create_connection"]
    socket.getaddrinfo = _ORIG["getaddrinfo"]
    CONFIG["instalado"] = False


@contextlib.contextmanager
def salida_confirmada(usuario, destino, tipo, datos, motivo):
    """Para una función futura que, a pedido de una persona, deba enviar algo al exterior. La pantalla que la usa tiene que
    haber mostrado antes la advertencia (qué datos, a dónde) y recibido la confirmación expresa; acá se registra."""
    if not usuario or not destino or not datos or not motivo:
        raise SalidaBloqueada("Falta la confirmación expresa (persona, destino, datos y motivo).")
    token = _PERMISO.set({"usuario": (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario,
                          "tipo": tipo, "datos": datos, "motivo": motivo})
    try:
        yield
    finally:
        _PERMISO.reset(token)


# ---------------------------------------------------------------- respaldos y estado
def ruta_en_nube(ruta):
    """True si la carpeta parece sincronizada con una nube pública."""
    import re
    partes = [p.strip().lower() for p in re.split(r"[\\/]+", str(ruta)) if p.strip()]
    return any(p == n or p.startswith(n + " -") or p.startswith(n + "-") for p in partes for n in CARPETAS_NUBE)


def verificar_respaldo(ruta):
    if CONFIG["modo_seguro"] and ruta and ruta_en_nube(ruta):
        registrar(str(ruta), "RESPALDO", "BLOQUEADA", "respaldo automático en una carpeta de nube pública")
        raise SalidaBloqueada(f"Modo Seguro Institucional: la carpeta de respaldo {ruta} está sincronizada con una nube pública. "
                              "Usá una carpeta de red o un disco del organismo.")
    return True


def estado(destinos):
    """Resumen para Administración. `destinos`: lista de (nombre, valor configurado, tipo)."""
    filas = []
    for nombre, valor, tipo in destinos:
        if tipo == "carpeta":
            clase = "sin configurar" if not valor else ("nube pública" if ruta_en_nube(valor) else "interno")
        else:
            clase = clasificar(valor)
        filas.append({"nombre": nombre, "valor": valor or "", "clase": clase})
    return {"modo_seguro": CONFIG["modo_seguro"], "instalado": CONFIG["instalado"], "destinos": filas,
            "dominios_internos": CONFIG["dominios_internos"], "autorizados": CONFIG["destinos_autorizados"],
            "riesgos": [f for f in filas if f["clase"] in ("externo", "nube pública")]}
