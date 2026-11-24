"""
main.py — App web para corregir y firmar notas de la DGPDH.

Flujo (actualizado): el asesor sube el documento, pero la corrección NO
arranca sola — queda esperando que el coordinador la apruebe y la
transfiera ("Aprobar y enviar a corrección"). Recién ahí se dispara el
procesamiento automático. Cuando termina, el Director ve el documento
completo en dos columnas (original | propuesto) y decide, párrafo por
párrafo: aceptar, modificar a mano, o rechazar. El rechazo de una nota
COMPLETA (operativo, no de redacción) es una acción aparte, disponible
tanto para Coordinación como para Dirección.

Los originales nunca se borran ni se sobrescriben: se archivan en
datos/originales/<AAAA>/<MM>/ apenas se suben (ver _ruta_archivo_historico).

Cómo correrla (en el Windows donde está Word/LanguageTool/Ollama):
    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8000

Config: ver config.ini (se crea solo, con valores por defecto, la primera vez).
"""
import configparser
import csv
import html as html_lib
import io
import json
import math
import logging
import os
import random
import re
import secrets
import shutil
import itertools
import threading
import time
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import auth
import database as db
import nucleo
import flujo
import aprendizaje
import reportes
import analisis
import analisis_codigo
import monitor_latencia
try:
    from pyinstrument import Profiler as _Profiler
    _PYINSTRUMENT_OK = True
except ImportError:
    _PYINSTRUMENT_OK = False
import automatico
import fechas
import integridad
import despacho
import clasificacion_notas  # reforma 14/11/2026: clasificación al mandar a la firma y protocolo interno
import expedientes
import actividad
import carga
import avisos_correo
import plazos
import respaldo
import sso
import mesa_entradas
import agentes
import circuito
import almacen_ppn
import pin
import programados
import salud as salud_sistema

# =====================================================
# RUTAS DE TRABAJO Y CONFIGURACIÓN
# =====================================================
BASE_DIR = Path(__file__).resolve().parent
CARPETA_DATOS = BASE_DIR / "datos"
CARPETA_ORIGINALES = CARPETA_DATOS / "originales"  # repositorio histórico, nunca se borra
CARPETA_BASE = CARPETA_DATOS / "base"  # copias editadas por Coordinación antes de aprobar
CARPETA_PROCESADOS = CARPETA_DATOS / "procesados"
CARPETA_FIRMADOS = CARPETA_DATOS / "firmados"
for carpeta in (CARPETA_ORIGINALES, CARPETA_BASE, CARPETA_PROCESADOS, CARPETA_FIRMADOS):
    carpeta.mkdir(parents=True, exist_ok=True)

RUTA_REGLAS_PERSONALIZADAS = CARPETA_DATOS / "reglas_personalizadas.json"
# Lista editable de temas y sus palabras clave (se crea sola la primera vez).
RUTA_TEMAS = CARPETA_DATOS / "temas.json"

RUTA_CONFIG = BASE_DIR / "config.ini"
_CONFIG_TEMPLATE = r"""; Configuración de la app web de notas DGPDH.
; Podés editar estos valores sin tocar el código.

[firma]
nombre_firmante = Sebastian Javier Pereiro
archivo_firma = \\fsppn\e\Notas a la firma DGPDH\Notas a la firma\Entrada\firma_director.PNG
; Firma individual de la Subdirectora/Subdirector. Si se deja sin indicar,
; se usa un archivo "firma_subdirectora" con la misma extensión, en la
; misma carpeta que archivo_firma.
; archivo_firma_subdirectora = \\fsppn\e\Notas a la firma DGPDH\Notas a la firma\Entrada\firma_subdirectora.PNG
; Cantidad de párrafos finales del documento donde se busca el bloque de firma
ventana_busqueda_firma = 8

[sesiones]
; Clave para firmar las cookies de sesión (login). Se genera sola la
; primera vez (ver _cargar_config) — no hace falta tocarla a mano.
clave_secreta =

[sso]
; Ingreso con la cuenta del portal PPN (el mismo de Expedientes y Fichas de reclamo). Vacío = desactivado.
; url: dirección del backend del portal. aplicacion_id: el ID (uuid) que el portal le asigna a esta aplicación.
; En el portal, la dirección de esta aplicación tiene que ser la de este sistema (el portal agrega /auth/ingreso/<token>).
url =
aplicacion_id =
portal_url =
verificar_cada_minutos = 10

[mesa_entradas]
; Mesa de Entradas PPN (expedientes). PREPARADO, SIN CONECTAR: hace falta que Mesa de Entradas agregue una
; credencial de servicio. url = backend; url_web = dirección que ve la gente; token_servicio = la credencial.
url =
url_web =
token_servicio =

[almacen_ppn]
; Almacenamiento de archivos PPN (ppn-storage). PREPARADO, SIN CONECTAR. sistema_id = uuid del «sistema» de esta app.
url =
sistema_id =

[numeracion]
; Formato del número de nota y de proveído que propone el sistema ({numero}, {anio}, {anio2} = dos dígitos).
formato_nota = {numero}/{anio}
formato_proveido = {numero}/{anio}
; Excel donde hoy se registran las notas (opcional): de ahí se toma el último número usado.
excel_registro =

[archivo]
; true = el PDF del despacho se genera directamente en PDF/A (conservación a largo plazo).
pdfa_en_despacho = false

[plazos]
; Días sin moverse en una etapa a partir de los cuales la nota se marca en ámbar y en rojo en el listado.
dias_etapa_ambar = 3
dias_etapa_rojo = 6

[seguridad]
; Poné true si la app se publica por HTTPS (proxy inverso, ver README):
; las cookies de sesión pasan a viajar solo por conexión cifrada.
https = false
; Horas que dura una sesión abierta antes de pedir la clave de nuevo.
horas_sesion = 12
; Tamaño máximo de cada archivo que se sube (en MB).
max_mb = 30
; Minutos sin uso tras los que se cierra la sesión (0 = nunca).
minutos_inactividad = 60
; MODO SEGURO INSTITUCIONAL (soberanía de datos). El sistema nunca transmite información al exterior de forma
; automática: toda conexión fuera de la red del organismo se bloquea y queda registrada (Administración →
; Seguridad → Modo seguro). Con el modo activo también se bloquean respaldos en carpetas de nube pública.
modo_seguro_institucional = true
; Dominios de la red interna, separados por coma (ej.: ppn.local, intranet.ppn.gov.ar). localhost, IP privadas y
; nombres de servidor sin dominio ya se consideran internos.
dominios_internos =
; Destinos externos AUTORIZADOS por la institución (ej.: un servidor de correo propio). Vacío = ninguno.
; Cada conexión a un destino autorizado queda registrada igual.
destinos_autorizados =

[respaldos]
; Copia diaria de la base y de los documentos. Conviene una carpeta de RED
; o de otro disco: si la carpeta queda vacía se usa datos/respaldos.
activo = true
carpeta =
conservar = 14
hora = 18:00
; Segunda copia en OTRO equipo o disco de red (opcional), p. ej. \\\\servidor\\respaldos_dgpdh
copia_externa =

[ollama]
url = http://localhost:11434/api/generate
modelo = qwen2.5:7b
; Poné false para desactivar por completo la IA.
usar_ia = true
; Un modelo distinto por proceso (opcional). Si una clave falta o está
; vacía, ese proceso usa el "modelo" de arriba. Cada modelo hay que
; descargarlo antes con:  ollama pull <nombre>
; modelo_correccion =
; modelo_clasificacion =
; modelo_consistencia =
; modelo_redaccion =
; Verificador cruzado: un SEGUNDO modelo (idealmente distinto) que confirma
; los avisos de consistencia de la IA antes de mostrarlos. Si queda vacío se
; puede activar igual desde Administración → IA → Calidad con el modelo general.
; modelo_verificacion =

[correo]
; Avisos por correo y resumen diario. APAGADO por defecto: se activa en
; Administración → Estado del sistema → Correo. Solo se manda a quien tiene
; correo cargado. La clave queda en este archivo: usá una cuenta de servicio.
servidor =
puerto = 587
usuario =
clave =
remitente =
tls = true
resumen_hora = 08:00
; Dirección del sistema para los enlaces de los mails, ej: http://pc-notas:8000
url_base =

[ia_adaptativa]
; Con el interruptor activo (Administración → IA → Calidad), la IA corre
; cuando la PC tiene capacidad libre, aun en horario de oficina.
; Necesita: pip install psutil
activa = false
cpu_max_libre = 80
cpu_max_oficina = 35
ram_libre_min_mb = 1500

[expedientes]
; Carpeta con las planillas de expedientes (.xls/.xlsx). SOLO SE LEEN, nunca
; se modifican. Dejala vacía para desactivar. Los .xls necesitan: pip install xlrd
carpeta =

[detenidos]
; Base de personas detenidas (menú «Personas detenidas»).
; api_token: si se completa, otro sistema del organismo puede LEER la base en
; /api/detenidos con la cabecera «Authorization: Bearer <token>». Vacío = apagado.
api_token =
; PREPARADO, SIN CONECTAR: dirección y token del servicio del sistema del menú de
; la Procuración, para precargar los datos de una persona por DNI o LPU.
url_sistema_procuracion =
token_sistema_procuracion =

[ia_automatica]
; Análisis automático de TODOS los documentos cargados (clasificación,
; consistencia, mejoras y corrección previa), solo fuera del horario de
; oficina para no ralentizar el sistema. Hora de Buenos Aires.
activa = true
hora_inicio = 17:00
hora_fin = 07:00
; Sábados y domingos corre las 24 horas: se activa o desactiva en Administración → IA (activo si nunca se cambió).
; Mientras alguien usa el sistema la IA no toma trabajos nuevos; retoma cuando pasan estos segundos sin uso (0 = no pausar).
pausa_por_uso_segundos = 90
"""


def _cargar_config():
    parser = configparser.ConfigParser()
    if RUTA_CONFIG.exists():
        parser.read(RUTA_CONFIG, encoding="utf-8")
    else:
        try:
            RUTA_CONFIG.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        except Exception as e:
            logging.warning(f"No se pudo crear config.ini, se usan valores por defecto embebidos: {e}")
        parser.read_string(_CONFIG_TEMPLATE)

    # La clave de sesiones (login) tiene que existir y ser estable entre
    # reinicios (si cambia, se desloguea todo el mundo). Se genera sola la
    # primera vez que falta, y se guarda en config.ini para la próxima.
    if not parser.has_section("sesiones"):
        parser.add_section("sesiones")
    if not parser.get("sesiones", "clave_secreta", fallback="").strip():
        parser.set("sesiones", "clave_secreta", uuid.uuid4().hex + uuid.uuid4().hex)
        try:
            with RUTA_CONFIG.open("w", encoding="utf-8") as f:
                parser.write(f)
        except Exception as e:
            logging.warning(f"No se pudo guardar la clave de sesiones en config.ini: {e}")
    return parser


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)


def _sin_modo_seleccion_en_consola():
    """Windows: un clic dentro de la ventana negra del servidor activa «Seleccionar» (QuickEdit). Mientras está así,
    Windows deja de leer lo que el servidor escribe en pantalla y el servidor entero se queda esperando: el sistema
    no responde para nadie hasta que alguien aprieta Esc en esa ventana. Se apaga el modo selección solo para esta
    ventana (no cambia la configuración de Windows)."""
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        entrada = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        modo = ctypes.c_uint32()
        if k32.GetConsoleMode(entrada, ctypes.byref(modo)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            k32.SetConsoleMode(entrada, (modo.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
    except Exception as e:  # sin consola (servicio, pythonw): no hace falta
        logging.debug(f"No se pudo apagar el modo selección de la consola: {e}")


def _registro_en_archivo():
    """Además de la pantalla, el registro queda en datos/registro_sistema.log (rota a los 5 MB, guarda 5) para poder
    ver después qué pasó si el sistema se trabó o se cerró."""
    try:
        from logging.handlers import RotatingFileHandler
        carpeta = Path(db.RUTA_DB).parent
        carpeta.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(carpeta / "registro_sistema.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True)
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        h.setLevel(logging.INFO)
        logging.getLogger().addHandler(h)
        for nombre in ("uvicorn.error", "uvicorn.access"):
            logging.getLogger(nombre).addHandler(h)
    except Exception as e:
        logging.warning(f"No se pudo abrir el archivo de registro: {e}")


_sin_modo_seleccion_en_consola()
_registro_en_archivo()

# Se carga ACÁ (no solo en el evento startup) porque SessionMiddleware
# necesita la clave secreta ya en el momento de armar la app, no después.
_CONFIG = _cargar_config()

app = FastAPI(title="Notas DGPDH")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# Disponibles en cualquier template sin tener que agregarlos a mano en cada
# TemplateResponse: el usuario logueado ya viaja en request.state.usuario
# (lo deja ahí AuthMiddleware), así que alcanza con exponer estos helpers.
templates.env.globals["es_admin"] = auth.es_admin
templates.env.globals["funciones_mesa"] = auth.funciones_mesa
templates.env.globals["mesa_excepcional"] = auth.mesa_excepcional
templates.env.globals["ETIQUETAS_ROL"] = auth.ETIQUETAS_ROL
templates.env.globals["ROLES_MESA"] = auth.ROLES_MESA
templates.env.globals["ROLES_DIRECCION"] = auth.ROLES_DIRECCION
templates.env.globals["ROLES_DE_EQUIPO"] = auth.ROLES_DE_EQUIPO
templates.env.globals["PRIORIDADES"] = auth.PRIORIDADES
templates.env.globals["ETIQUETAS_PRIORIDAD"] = auth.ETIQUETAS_PRIORIDAD
templates.env.globals["etiqueta_tema"] = analisis.etiqueta_tema
templates.env.globals["ETIQUETAS_TRABAJO"] = automatico.ETIQUETAS_TRABAJO
templates.env.globals["ETIQUETAS_ESTADO_TRABAJO"] = automatico.ETIQUETAS_ESTADO_TRABAJO
templates.env.globals["contar_notificaciones"] = db.contar_notificaciones
import menu_paneles  # estado breve de Autos PPN, Metas y Personas detenidas en el menú lateral
templates.env.globals["paneles_menu"] = menu_paneles.estado


def _hoy_local():
    return datetime.now(fechas.zona()).date().isoformat()


templates.env.globals["hoy_local"] = _hoy_local
templates.env.globals["tomar_flash"] = lambda request: request.session.pop("flash", None)


def _flash(request, texto):
    """Mensaje de confirmación que se muestra una sola vez en la próxima pantalla."""
    request.session["flash"] = texto
import iconos  # noqa: E402
templates.env.globals["ico"] = iconos.svg  # íconos de línea en el contenido (evaluación de interfaz, propuesta 31)
templates.env.globals["hallazgo_vacio"] = lambda h: analisis.es_hallazgo_vacio(h.get("detalle"), h.get("valores"))


def _version_estaticos():
    """Cambia cuando se actualiza algún archivo de static/: se agrega a las
    direcciones de estilos y scripts para que el navegador no use copias viejas."""
    try:
        return str(int(max(p.stat().st_mtime for p in (BASE_DIR / "static").glob("*") if p.is_file())))
    except Exception:
        return "0"


templates.env.globals["static_v"] = _version_estaticos()
# Modo Seguro Institucional: htmx se sirve desde el propio servidor si está la copia local (static/htmx.min.js).
_HTMX_LOCAL = (BASE_DIR / "static" / "htmx.min.js").is_file()
if not _HTMX_LOCAL:  # propuestas 98/99: las pantallas nunca piden nada a internet; sin este archivo los botones dinámicos no andan
    logging.error("Falta static/htmx.min.js: guardá htmx 1.9.12 en la carpeta static (ver Administración → Seguridad).")
templates.env.globals["htmx_local"] = _HTMX_LOCAL
# Las fechas se guardan en UTC; en pantalla se muestran en hora de Buenos Aires.
templates.env.filters["hora"] = fechas.a_hora_local
templates.env.filters["fecha_corta"] = fechas.fecha_corta

EXTENSIONES_VALIDAS = {".docx", ".doc", ".pdf"}  # PDF: archivo directo o adjunto de un bloque (Beta 01)

# =====================================================
# LOGIN / PERMISOS
# =====================================================
# Rutas accesibles sin estar logueado.
_RUTAS_PUBLICAS = {"/login", "/salud"}
_HTTPS = _CONFIG.getboolean("seguridad", "https", fallback=False)
# Con la clave provisoria solo se puede cambiar la clave o salir.
_RUTAS_CLAVE_OBLIGATORIA = ("/perfil", "/logout", "/static")
# Quien no tiene acceso al sistema de notas solo ve lo compartido: inicio,
# traslados, el catálogo de unidades, avisos y su perfil.
_RUTAS_SIN_NOTAS = ("/inicio", "/traslados", "/unidades", "/avatar", "/perfil", "/logout", "/static", "/notificaciones", "/salud",
                    "/manual", "/pin/", "/datos/programados/", "/guia-estilo")


def _tiene_acceso_notas(usuario):
    """Dirección siempre; el resto según el permiso que da Administración."""
    return auth.es_admin(usuario) or bool(usuario.get("acceso_notas", 1))


def _tiene_acceso_autos(usuario):
    """Notas y Autos PPN son dos sistemas separados: Administración habilita cada uno por persona. La Dirección, los
    choferes y quien coordina traslados siempre ven Autos PPN."""
    return bool(usuario) and (auth.es_admin(usuario) or usuario["rol"] == auth.ROL_CHOFER or bool(usuario.get("coordina_traslados"))
                              or bool(usuario.get("acceso_traslados", 1)))


templates.env.globals["acceso_autos"] = _tiene_acceso_autos
_RUTAS_AUTOS = ("/traslados",)


def _pagina_inicial(usuario):
    """Después del login: quien ve Notas va al inicio; quien solo ve Autos PPN, a Autos PPN."""
    return "/inicio" if (_tiene_acceso_notas(usuario) or not _tiene_acceso_autos(usuario)) else "/traslados"


_METODOS_ESCRITURA = {"POST", "PUT", "PATCH", "DELETE"}


def _quiere_html(request: Request):
    return "text/html" in request.headers.get("accept", "") and not request.headers.get("hx-request")


def _respuesta_error(request: Request, estado: int, detalle: str):
    """Una pantalla amable para el navegador y JSON para htmx/fetch (la
    página muestra ese mensaje en un aviso, ver base.html)."""
    if _quiere_html(request):
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "codigo": estado, "detalle": detalle},
            status_code=estado,
        )
    return JSONResponse({"detail": detalle}, status_code=estado)


# Confirmación de todo lo que se crea o se guarda: si una acción terminó bien (vuelve a una pantalla) y no dejó
# su propio mensaje, se muestra uno general. Lo específico («Equipo X creado…») lo pone cada pantalla.
MENSAJE_GUARDADO = "Listo: se guardó correctamente."
_SIN_CONFIRMACION = ("/login", "/logout", "/notificaciones", "/pin", "/subir/verificar", "/firma-en-serie")
_RE_SIN_CONFIRMACION = re.compile(r"^/nota/\d+/(firmar|firmar-y-seguir)$")


def _confirmar_guardado(request, response):
    try:
        if request.method != "POST" or request.headers.get("x-sin-actividad"):
            return
        path = request.url.path
        if path.startswith(_SIN_CONFIRMACION) or _RE_SIN_CONFIRMACION.match(path):
            return
        navega = response.status_code in (302, 303) or (
            response.status_code == 200 and (response.headers.get("hx-redirect") or response.headers.get("hx-refresh")))
        if navega and not request.session.get("flash") and not request.session.get("flash_admin"):
            request.session["flash"] = MENSAJE_GUARDADO
    except Exception:
        logging.exception("No se pudo dejar la confirmación de guardado")


# Tiempos de respuesta: se guardan (solo en memoria, sin datos de las notas) los pedidos de más de 1 segundo, para ver
# en Administración → Estado del sistema → Base de datos qué pantalla se demora y cuánto.
PEDIDOS = {"total": 0, "lentos": 0, "max_ms": 0, "ultimos_lentos": []}
UMBRAL_LENTO_MS = 1000
_ultimo_perfil_texto: str = ""
_RE_NUMEROS_RUTA = re.compile(r"/\d+(?=/|$)")


def _contexto_lentitud():
    """Para diagnosticar la lentitud: uso del procesador y qué trabajo de fondo estaba corriendo en ese momento."""
    partes = []
    try:
        import psutil
        partes.append(f"CPU {psutil.cpu_percent(interval=None):.0f}%")
        partes.append(f"memoria {psutil.virtual_memory().percent:.0f}%")
    except Exception:
        pass
    try:
        if _CORRIGIENDO.get("id"):
            partes.append(f"corrigiendo la nota {_CORRIGIENDO['id']}")
        t = automatico._TRABAJADOR
        if t is not None and t.actual:
            partes.append(f"análisis de fondo: {str(t.actual)[:60]}")
    except Exception:
        pass
    return (" · " + " · ".join(partes)) if partes else ""


def _medir_pedido(request, response, ms):
    try:
        response.headers["Server-Timing"] = f"app;dur={ms:.0f}"
        PEDIDOS["total"] += 1
        PEDIDOS["max_ms"] = max(PEDIDOS["max_ms"], round(ms))
        if ms >= UMBRAL_LENTO_MS and not request.headers.get("x-sin-actividad"):
            PEDIDOS["lentos"] += 1
            PEDIDOS["ultimos_lentos"].insert(0, {"cuando": fechas.a_hora_local(datetime.now(timezone.utc).isoformat()),
                                                 "pantalla": request.method + " " + _RE_NUMEROS_RUTA.sub("/…", request.url.path),
                                                 "ms": round(ms)})
            del PEDIDOS["ultimos_lentos"][30:]
            base = getattr(request.state, "medicion_base", None) or {}
            en_base = f" · base: {base['n']} consulta(s), {base['ms']:.0f} ms" if base.get("n") else ""
            logging.info(f"Pedido lento: {request.method} {request.url.path} {ms:.0f} ms" + en_base + _contexto_lentitud())
            monitor_latencia.registrar(
                ruta=str(request.url.path), metodo=request.method, duracion_ms=ms,
                contexto={"query": str(request.url.query)[:200] if request.url.query else ""},
            )
            if ms > 5000 and _PYINSTRUMENT_OK:
                global _ultimo_perfil_texto
                _ultimo_perfil_texto = (
                    f"Request muy lento detectado: {request.method} {request.url.path} "
                    f"— {ms:.0f} ms — {datetime.now().isoformat(timespec='seconds')}"
                )
    except Exception:
        pass


def _origen_valido(request: Request):
    """Defensa contra CSRF: un formulario armado en OTRO sitio no puede
    disparar acciones, porque el navegador manda su Origin/Referer."""
    origen = request.headers.get("origin")
    if not origen or origen == "null":
        # «null»: el navegador no revela el origen (política de referencia estricta); se usa el Referer si viene.
        origen = request.headers.get("referer") or origen
    if not origen:
        return True  # clientes que no son navegador (scripts, pruebas)
    permitidos = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")}
    return urlparse(origen).netloc in permitidos - {""}


def _con_cabeceras(response):
    if _HTTPS:  # Ola 5 (reforma 92): con HTTPS activo, el navegador no vuelve a entrar por conexión sin cifrar
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


class AuthMiddleware(BaseHTTPMiddleware):
    """Exige login para toda la app, salvo /login y los estáticos. Deja el
    usuario logueado en request.state.usuario (y en db.USUARIO_ACTUAL, para
    que cada cambio en la base quede firmado por quien lo hizo), sin tener
    que repetir la verificación en cada endpoint. También rechaza pedidos de
    escritura que vengan de otro sitio (CSRF) y obliga a cambiar la clave
    provisoria."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method in _METODOS_ESCRITURA and not _origen_valido(request):
            return _con_cabeceras(_respuesta_error(
                request, 403, "El pedido no vino de esta aplicación, así que se rechazó por seguridad."
            ))
        if path.startswith("/static") or path in _RUTAS_PUBLICAS or path.startswith("/auth/ingreso/") or (
                path.startswith("/api/detenidos") and request.method == "GET" and request.headers.get("authorization", "").startswith("Bearer ")):
            return _con_cabeceras(await call_next(request))

        usuario_id = request.session.get("usuario_id")
        usuario = db.obtener_usuario(usuario_id) if usuario_id else None
        if usuario is None or not usuario["activo"]:
            if usuario_id:
                request.session.clear()
            if path == "/logout":
                return await call_next(request)
            if request.headers.get("hx-request"):
                return Response(status_code=200, headers={"HX-Redirect": "/login"})
            return RedirectResponse(url="/login")

        # Cierre de sesión por inactividad ([seguridad] minutos_inactividad; 0 = sin límite).
        ahora = int(time.time())
        ultima = request.session.get("ultima_actividad")
        if MINUTOS_INACTIVIDAD and ultima and ahora - int(ultima) > MINUTOS_INACTIVIDAD * 60:
            request.session.clear()
            if request.headers.get("hx-request"):
                return Response(status_code=200, headers={"HX-Redirect": "/login?inactividad=1"})
            return RedirectResponse(url="/login?inactividad=1")
        if not request.headers.get("x-sin-actividad"):  # los chequeos automáticos de la página no cuentan como uso
            request.session["ultima_actividad"] = ahora
            automatico.registrar_actividad()  # mientras alguien usa el sistema, la IA no toma trabajos nuevos
        # Quien entró por el portal PPN: cada tanto se confirma que la sesión del portal siga abierta.
        token_sso = request.session.get("sso_token")
        if token_sso and sso.activo() and ahora - int(request.session.get("sso_verificado") or 0) > sso.VERIFICAR_CADA:
            viva = sso.sesion_viva(token_sso)
            if viva is False:
                request.session.clear()
                if request.headers.get("hx-request"):
                    return Response(status_code=200, headers={"HX-Redirect": "/login?portal=1"})
                return RedirectResponse(url="/login?portal=1")
            request.session["sso_verificado"] = ahora
        request.state.usuario = usuario
        if usuario.get("debe_cambiar_clave") and not token_sso and not path.startswith(_RUTAS_CLAVE_OBLIGATORIA):
            if request.headers.get("hx-request"):
                return Response(status_code=200, headers={"HX-Redirect": "/perfil?obligatorio=1"})
            return RedirectResponse(url="/perfil?obligatorio=1")

        if not _tiene_acceso_notas(usuario) and not path.startswith(_RUTAS_SIN_NOTAS):
            if request.headers.get("hx-request"):
                return Response(status_code=200, headers={"HX-Redirect": "/inicio"})
            return RedirectResponse(url="/inicio")
        if path.startswith(_RUTAS_AUTOS) and not _tiene_acceso_autos(usuario):
            if request.headers.get("hx-request"):
                return Response(status_code=200, headers={"HX-Redirect": "/inicio"})
            return RedirectResponse(url="/inicio")

        # Ola 5 (reforma 93): Dirección y Mesa confirman su PIN una vez por sesión antes de firmar o despachar.
        if pin.ruta_protegida(request.method, path) and pin.requiere(usuario) and not pin.sesion_confirmada(request.session, usuario):
            return _con_cabeceras(_respuesta_error(request, 403, "Antes de firmar o despachar confirmá tu PIN de firma (se pide una sola vez por sesión). Recargá la página e intentá de nuevo."))

        ficha = db.USUARIO_ACTUAL.set(usuario)
        ficha_mesa = db.FUNCION_MESA_EXCEPCIONAL.set(False)  # cada pedido empieza sin la marca de función excepcional
        reloj = time.perf_counter()
        ficha_medicion = db.MEDICION_PEDIDO.set({"n": 0, "ms": 0.0})
        clave_vigia = _vigia_empezar(f"{request.method} {path}")
        try:
            response = await call_next(request)
        finally:
            _vigia_terminar(clave_vigia)
            request.state.medicion_base = db.MEDICION_PEDIDO.get()
            db.MEDICION_PEDIDO.reset(ficha_medicion)
            db.FUNCION_MESA_EXCEPCIONAL.reset(ficha_mesa)
            db.USUARIO_ACTUAL.reset(ficha)
        _medir_pedido(request, response, (time.perf_counter() - reloj) * 1000)
        _confirmar_guardado(request, response)
        if response.media_type == "text/html" or "text/html" in response.headers.get("content-type", ""):
            response.headers.setdefault("Cache-Control", "no-store")
        return _con_cabeceras(response)


# ---------------------------------------------------------------- vigía de pedidos colgados (diagnóstico)
# Si un pedido tarda más de VIGIA_SEGUNDOS, se escribe en el registro qué está haciendo cada hilo del servidor, una
# sola vez por pedido. Sirve para encontrar la causa de un cuelgue sin tener que reproducirlo. No cambia nada más.
VIGIA_SEGUNDOS = 25
_VIGIA_PEDIDOS = {}
_VIGIA_LOCK = threading.Lock()
_VIGIA_SEQ = itertools.count(1)


def _vigia_empezar(descripcion):
    clave = next(_VIGIA_SEQ)
    with _VIGIA_LOCK:
        _VIGIA_PEDIDOS[clave] = [time.monotonic(), descripcion, False]
    return clave


def _vigia_terminar(clave):
    with _VIGIA_LOCK:
        _VIGIA_PEDIDOS.pop(clave, None)


def _vigia_volcar(descripcion, segundos):
    import sys
    import traceback
    nombres = {t.ident: t.name for t in threading.enumerate()}
    partes = [f"Pedido colgado: {descripcion} lleva {segundos:.0f} s. Qué hace cada hilo del servidor:"]
    for ident, marco in sys._current_frames().items():
        if nombres.get(ident) == "vigia-pedidos":
            continue
        pila = "".join(traceback.format_stack(marco, limit=14))
        partes.append(f"--- hilo {nombres.get(ident, ident)}\n{pila}")
    logging.warning("\n".join(partes))


def _vigia_bucle():
    while True:
        time.sleep(5)
        try:
            ahora = time.monotonic()
            with _VIGIA_LOCK:
                colgados = [(c, v) for c, v in _VIGIA_PEDIDOS.items() if not v[2] and ahora - v[0] > VIGIA_SEGUNDOS]
                for _, v in colgados:
                    v[2] = True
            for _, v in colgados:
                _vigia_volcar(v[1], ahora - v[0])
        except Exception:
            logging.exception("Vigía de pedidos")


threading.Thread(target=_vigia_bucle, name="vigia-pedidos", daemon=True).start()


# Si todo el proceso queda congelado (algo que no suelta el intérprete, como una expresión regular que no termina),
# el vigía de arriba tampoco puede escribir. Para ese caso, faulthandler (que corre fuera del intérprete) vuelca las
# pilas de todos los hilos en datos/cuelgues.txt si el latido de abajo deja de renovarse durante 40 segundos.
def _latido_congelamiento():
    import faulthandler
    try:
        archivo = open(CARPETA_DATOS / "cuelgues.txt", "a", encoding="utf-8")  # noqa: SIM115 (queda abierto mientras corre)
    except Exception:
        logging.exception("No se pudo abrir datos/cuelgues.txt")
        return
    while True:
        try:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(40, repeat=False, file=archivo, exit=False)
        except Exception:
            logging.exception("Latido de congelamiento")
            return
        time.sleep(2)


def _vigia_profundo():
    """Si algo traba a TODO el proceso (ni el vigía puede escribir), el módulo faulthandler de Python vuelca igual, desde
    un hilo de C, qué hace cada hilo en datos/cuelgues.log. Un latido rearma el aviso cada 5 s; si el latido no corre
    durante 45 s, es que el proceso está trabado y se escribe el volcado."""
    import faulthandler
    try:
        ruta = Path(__file__).resolve().parent / "datos" / "cuelgues.log"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        archivo = open(ruta, "a", encoding="utf-8")  # queda abierto mientras corre el servidor
    except OSError:
        return
    while True:
        try:
            archivo.write("")
            faulthandler.dump_traceback_later(45, repeat=False, file=archivo, exit=False)
        except Exception:
            return
        time.sleep(5)


threading.Thread(target=_vigia_profundo, name="vigia-profundo", daemon=True).start()


MINUTOS_INACTIVIDAD = max(0, _CONFIG.getint("seguridad", "minutos_inactividad", fallback=60))
import soberania  # noqa: E402  (Modo Seguro Institucional: antes de cualquier conexión)
soberania.configurar(_CONFIG)
soberania.instalar()
sso.configurar(_CONFIG)
mesa_entradas.configurar(_CONFIG)
almacen_ppn.configurar(_CONFIG)
circuito.configurar(_CONFIG)
import detenidos  # noqa: E402
detenidos.configurar(_CONFIG)
soberania.autorizar_sistemas_del_organismo([sso.URL, sso.PORTAL_URL, mesa_entradas.URL, mesa_entradas.URL_WEB, almacen_ppn.URL,
                                            detenidos.EXTERNO.get("url", "")])
templates.env.globals["sso"] = sso
templates.env.globals["MINUTOS_INACTIVIDAD"] = MINUTOS_INACTIVIDAD
app.add_middleware(AuthMiddleware)
# IMPORTANTE: SessionMiddleware se agrega DESPUÉS de AuthMiddleware a
# propósito. En Starlette, el middleware agregado más tarde queda más
# "afuera" (se ejecuta primero en cada request): como AuthMiddleware
# necesita leer request.session, SessionMiddleware tiene que envolverlo
# (ejecutarse antes) — si el orden fuera al revés, request.session todavía
# no existiría cuando AuthMiddleware intenta leerlo.
app.add_middleware(
    SessionMiddleware,
    secret_key=_CONFIG.get("sesiones", "clave_secreta"),
    max_age=int(_CONFIG.getfloat("seguridad", "horas_sesion", fallback=12) * 3600),
    same_site="lax",
    https_only=_CONFIG.getboolean("seguridad", "https", fallback=False),
)
MAX_BYTES_SUBIDA = int(_CONFIG.getfloat("seguridad", "max_mb", fallback=30) * 1024 * 1024)


# ---- Errores: mensajes claros en vez de JSON crudo ----
@app.exception_handler(StarletteHTTPException)
async def _error_http(request: Request, exc: StarletteHTTPException):
    detalle = exc.detail if isinstance(exc.detail, str) else "No se pudo completar la acción."
    if exc.status_code == 404 and detalle == "Not Found":
        detalle = "Esa página no existe."
    return _respuesta_error(request, exc.status_code, detalle)


@app.exception_handler(RequestValidationError)
async def _error_validacion(request: Request, exc: RequestValidationError):
    return _respuesta_error(request, 422, "Faltan datos o alguno no es válido. Revisá el formulario.")


@app.exception_handler(db.ConflictoDeEstado)
async def _error_conflicto(request: Request, exc: db.ConflictoDeEstado):
    return _respuesta_error(request, 409, str(exc))


@app.exception_handler(Exception)
async def _error_inesperado(request: Request, exc: Exception):
    ref = secrets.token_hex(2).upper()  # propuesta 138 (U41): código corto para que soporte encuentre el error en el registro
    logging.exception(f"Error inesperado en {request.method} {request.url.path} [Ref. {ref}]")
    return _respuesta_error(request, 500, f"Ocurrió un error inesperado. Quedó registrado; avisá a Sistemas si se repite (Ref. {ref}).")


def usuario_actual(request: Request):
    """El AuthMiddleware ya garantiza que, si llegamos hasta un endpoint,
    request.state.usuario existe (o la request fue redirigida a /login)."""
    return getattr(request.state, "usuario", None)


def _exigir_rol(usuario, *roles_permitidos):
    """Los Directores Generales (Dirección) siempre pueden todo. Para el
    resto, el rol tiene que estar en `roles_permitidos`.

    Excepción: las funciones propias del área Mesa de Entradas (cuando los roles permitidos son solo los de Mesa)
    las ejerce esa área y, excepcionalmente, el Director General (auth.ROLES_MESA_EXCEPCIONAL); en ese caso
    todo lo que la acción deje en el historial queda marcado como excepcional."""
    if roles_permitidos and set(roles_permitidos) <= auth.ROLES_MESA:
        if not auth.funciones_mesa(usuario):
            raise HTTPException(status_code=403, detail="Es una función del área Mesa de Entradas")
        if auth.mesa_excepcional(usuario):
            db.FUNCION_MESA_EXCEPCIONAL.set(True)
        return
    if auth.es_admin(usuario):
        return
    if usuario["rol"] not in roles_permitidos:
        raise HTTPException(status_code=403, detail="No tenés permiso para esta acción")


def _puede_ver_nota(usuario, nota):
    """Visibilidad de una nota puntual (/nota/{id}), según el rol logueado:
    - Dirección General: todas.
    - Mesa de Entrada: solo las FIRMADA, más los documentos que todavía
      faltan firmar de un bloque que ya tiene alguno firmado (para ver qué
      falta antes de poder despachar el bloque completo).
    - Coordinador: las de su propio equipo.
    - Asesor: las suyas, o las de su equipo (ver /equipo).
    Un BORRADOR (sin enviar a Coordinación) solo lo ve quien lo subió."""
    if nota["estado"] == "BORRADOR":
        return nota["usuario_id"] == usuario["id"]
    if auth.es_admin(usuario):
        return True
    if usuario["rol"] in auth.ROLES_MESA:
        # Mesa sigue viendo lo que despachó: para registrar entregas en mano y respuestas.
        if nota["estado"] in ("FIRMADA", "PARA_REMITIR", "DESPACHADA", "ADJUNTO"):
            return True
        if nota.get("modalidad") == "SOLO_MESA" and nota["estado"] != "BORRADOR":
            return True
        if nota.get("bloque_id"):
            bloque = db.obtener_bloque(nota["bloque_id"])
            return bool(bloque) and any(n["estado"] in db.LISTAS_PARA_REMITIR for n in bloque["notas"])
        return False
    if usuario["rol"] == auth.ROL_COORDINADOR:
        return nota["equipo_id"] == usuario["equipo_id"]
    if usuario["rol"] == auth.ROL_ASESOR:
        return nota["usuario_id"] == usuario["id"] or nota["equipo_id"] == usuario["equipo_id"]
    return False


def _puede_gestionar_nota(usuario, nota):
    """Quién puede cambiar la prioridad de una nota y armar/modificar
    bloques con ella: el asesor que la subió, el Coordinador de su equipo y
    la Dirección General."""
    if auth.es_admin(usuario):
        return True
    if usuario["rol"] in auth.ROLES_MESA and (
        nota.get("modalidad") == "SOLO_MESA"
        or (db.es_pdf(nota) and nota["estado"] == "ADJUNTO")
    ):
        return True  # SOLO_MESA y PDFs confirmados por Dirección los gestiona Mesa
    if usuario["rol"] == auth.ROL_COORDINADOR:
        return nota["equipo_id"] == usuario["equipo_id"]
    if usuario["rol"] == auth.ROL_ASESOR:
        return nota["usuario_id"] == usuario["id"]
    return False


ESTADOS_ACTIVOS = ("BORRADOR", "SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "FIRMADA", "PARA_REMITIR", "ERROR", "ARCHIVADA", "ADJUNTO", "A_CONFIRMAR")
LISTOS_PARA_DESPACHO = ("FIRMADA", "PARA_REMITIR", "DESPACHADA", "ADJUNTO")  # A_CONFIRMAR no: requiere confirmación de Dirección


def _rango(prioridad):
    return auth.RANGO_PRIORIDAD.get(prioridad or "NORMAL", 2)


def _anotar_prioridad_efectiva(notas):
    """Agrega n["prioridad_efectiva"] a cada nota: su propia prioridad, o la
    más alta de su bloque si pertenece a uno (un bloque toma la prioridad
    más alta de sus documentos, porque se despacha todo junto)."""
    mejor_por_bloque = {}
    for n in notas:
        if n.get("bloque_id"):
            actual = mejor_por_bloque.get(n["bloque_id"])
            if actual is None or _rango(n["prioridad"]) < _rango(actual):
                mejor_por_bloque[n["bloque_id"]] = n["prioridad"]
    for n in notas:
        n["prioridad_efectiva"] = mejor_por_bloque.get(n.get("bloque_id"), n["prioridad"]) if n.get("bloque_id") else n["prioridad"]


def _ordenar_para_firma(notas):
    """Orden del panel "Pendientes de tu firma": URGENTE, Prioritaria, Normal;
    bloques y grupos de relacionadas quedan juntos (ver siguiente_firma.ordenar_con_grupos)."""
    import siguiente_firma as sf
    return sf.ordenar_con_grupos(notas)


def _respaldo_antes_de_migrar():
    """Respaldo completo (base + documentos) en la carpeta de respaldos de la PC antes de cambiar el esquema. Las migraciones
    solo agregan columnas y tablas; si el respaldo fallara, se registra con claridad y se sigue."""
    ok, resumen = respaldo.hacer_respaldo()
    (logging.info if ok else logging.error)("Respaldo previo a la actualización del esquema: " + resumen)


def _sin_seleccion_rapida_en_consola():
    """Windows: si alguien hace clic en la ventana negra de iniciar.bat, la consola entra en «modo de selección»
    (Edición rápida) y TODO el servidor se congela al escribir la siguiente línea del registro, hasta que se aprieta
    una tecla. Se apaga ese modo solo para esta ventana; el servidor sigue igual."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        entrada = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        modo = ctypes.c_uint32()
        if k32.GetConsoleMode(entrada, ctypes.byref(modo)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS, ENABLE_INSERT_MODE = 0x0040, 0x0080, 0x0020
            nuevo = (modo.value & ~ENABLE_QUICK_EDIT_MODE & ~ENABLE_INSERT_MODE) | ENABLE_EXTENDED_FLAGS
            if k32.SetConsoleMode(entrada, nuevo):
                logging.info("Consola: se desactivó la «Edición rápida» (un clic en la ventana ya no congela el servidor).")
    except Exception as e:
        logging.warning(f"No se pudo desactivar la «Edición rápida» de la consola: {e}")


@app.on_event("startup")
def al_iniciar():
    _sin_seleccion_rapida_en_consola()
    _threading.Thread(target=_latido_congelamiento, name="latido-congelamiento", daemon=True).start()
    respaldo.configurar(_CONFIG, CARPETA_DATOS)
    # antes de aplicar una migración pendiente sobre la base existente se hace un respaldo completo (ADR-0003)
    db.inicializar_db(antes_de_migrar=_respaldo_antes_de_migrar)
    # Si la carpeta de la app se movió (restauración, otro disco), las rutas
    # guardadas en la base se corrigen solas.
    try:
        corregidas = db.reubicar_rutas(CARPETA_DATOS)
        if corregidas:
            logging.info(f"Se reubicaron {corregidas} ruta(s) de documentos tras mover la carpeta")
    except Exception:
        logging.exception("No se pudieron reubicar las rutas")
    nucleo.inicializar_herramientas(_CONFIG)
    nucleo.cargar_reglas_personalizadas(RUTA_REGLAS_PERSONALIZADAS)
    analisis.cargar_temas(RUTA_TEMAS)
    automatico.configurar(_CONFIG)
    expedientes.configurar(_CONFIG, CARPETA_DATOS / "expedientes_importados")
    carga.configurar(_CONFIG)
    avisos_correo.configurar(_CONFIG)
    salud_sistema.configurar(CARPETA_DATOS, _CONFIG)
    programados.configurar(CARPETA_DATOS)
    # Un solo trabajador por carpeta de datos, aunque se arranquen varios
    # procesos de uvicorn (ver README).
    automatico.iniciar_trabajador(ruta_lock=CARPETA_DATOS / "trabajador.lock")
    try:  # notas que quedaron «aplicando correcciones» (reinicio, corte de luz): vuelven a la cola
        _recuperar_en_correccion()
        _sellar_auditoria()
        _threading.Thread(target=_vigilar_correcciones, name="vigila-correccion", daemon=True).start()
    except Exception:
        logging.exception("No se pudieron retomar las correcciones pendientes")
    # Ola 2: glosario institucional inicial (si está vacío) y datos del texto de las notas ya cargadas, en segundo plano.
    try:
        agentes.sembrar_glosario()
        circuito.crear_plantillas_base(CARPETA_DATOS / "modelos")
    except Exception:
        logging.exception("No se pudo cargar el glosario o las plantillas iniciales")
    import threading
    threading.Thread(target=agentes.relevar_entidades, name="relevar-entidades", daemon=True).start()
    threading.Thread(target=_relevar_lpu, name="relevar-lpu", daemon=True).start()
    try:  # Ola 4: desplazamientos de los viajes ya asignados
        import operacion
        operacion.relevar_desplazamientos()
    except Exception:
        logging.exception("No se pudieron armar los desplazamientos")


def _relevar_lpu():
    """Una sola vez: vuelve a correr la prerrevisión (reglas, sin IA) de los documentos en circuito para sumar la
    verificación del LPU contra la base de expedientes."""
    if db.obtener_ajuste("prerrevision_lpu_v1"):
        return
    try:
        for n in db.listar_notas(limite=100000):
            if n["estado"] not in ("DESPACHADA", "RECHAZADA"):
                try:
                    agentes.correr_prerrevision(n["id"])
                except Exception:
                    logging.exception("Prerrevisión (LPU) de la nota %s", n["id"])
        db.guardar_ajuste("prerrevision_lpu_v1", "1")
    except Exception:
        logging.exception("No se pudo relevar el LPU de las notas")


@app.on_event("shutdown")
def al_apagar():
    automatico.detener_trabajador()
    nucleo.cerrar_languagetool()


# =====================================================
# ENDPOINTS — LOGIN / LOGOUT / ADMINISTRACIÓN
# =====================================================
def _ids_pendientes_de_accion(usuario, notas, vistas=None):
    """Ids de las notas que esperan algo de esta persona según su perfil (reforma «solo lo pendiente»)."""
    vistas = db.ids_notas_vistas(usuario["id"]) if vistas is None else vistas
    ids = {n["id"] for n in notas if n["estado"] == "RECHAZADA" and n["id"] not in vistas}
    ids |= {n["id"] for n in notas if n["estado"] == "SUBIDA" and n.get("hallazgos_abiertos")
            and (usuario["rol"] != auth.ROL_ASESOR or n["usuario_id"] == usuario["id"])}
    if usuario["rol"] == auth.ROL_COORDINADOR:
        ids |= {n["id"] for n in notas if n["estado"] in ("SUBIDA", "ERROR")}
    elif usuario["rol"] == auth.ROL_ASESOR:
        ids |= {n["id"] for n in notas if n["estado"] == "ERROR" and n["usuario_id"] == usuario["id"]}
    elif auth.es_admin(usuario):
        ids |= {n["id"] for n in notas if n["estado"] in ("EN_REVISION", "PROCESADA", "ERROR", "A_CONFIRMAR")}
    elif usuario["rol"] in auth.ROLES_MESA:
        ids |= {n["id"] for n in notas if n["estado"] in db.LISTAS_PARA_REMITIR}
    return ids


class _SinAutos(Exception):
    """La persona no tiene habilitado Autos PPN: el inicio no muestra viajes."""


def _resumen_inicio(usuario):
    """Datos de las tarjetas del inicio (cada módulo muestra solo lo que le corresponde al usuario)."""
    r = {"notas": None, "viajes": None, "avisos": db.contar_notificaciones(usuario["id"]),
         "actividad": db.listar_notificaciones(usuario["id"], 5)}
    if _tiene_acceso_notas(usuario):
        visibles = _filtrar_por_visibilidad(db.listar_notas(), usuario)
        vistas = db.ids_notas_vistas(usuario["id"])
        semaforos = [plazos.semaforo(n) for n in visibles]
        r["notas"] = {
            "pendientes": len(_ids_pendientes_de_accion(usuario, visibles, vistas)),
            "rechazadas": sum(1 for n in visibles if n["estado"] == "RECHAZADA" and n["id"] not in vistas),
            "por_vencer": sum(1 for x in semaforos if x and x["nivel"] in ("rojo", "ambar")),
            "vencidas": sum(1 for x in semaforos if x and x["nivel"] == "vencida"),
            "quietas": sum(1 for n in visibles if (_antiguedad_etapa(n) or {}).get("nivel") == "rojo"),
            "en_curso": sum(1 for n in visibles if n["estado"] in ("SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "FIRMADA")),
            "para_firmar": sum(1 for n in visibles if n["estado"] == "PROCESADA") if auth.es_admin(usuario) else None,
            "con_error": sum(1 for n in visibles if n["estado"] == "ERROR"),
        }
    viajes_hoy = []
    try:
        if not _tiene_acceso_autos(usuario):
            raise _SinAutos()
        res = traslados.resumen(usuario, traslados.circuito_de(usuario))
        r["viajes"] = {"hoy": res["n_hoy"], "semana": res["n_semana"], "pendientes": res["n_pendientes"], "alertas": res["n_alertas"]}
        viajes_hoy = [{"texto": a["texto"], "motivo": "Autos PPN", "url": a["url"]} for a in res["alertas"] if a.get("nivel") == "alto"][:5]
        viajes_hoy += [{"texto": f"Viaje a {v['destino']} ({v.get('hora_salida') or 'sin hora'})", "motivo": "Hoy", "url": f"/traslados/{v['id']}"}
                       for v in res["hoy"]][:5]
    except _SinAutos:
        pass
    except Exception:
        logging.exception("No se pudo armar el resumen de traslados")
    # Reforma 23: «Qué necesita mi atención hoy» — una sola lista priorizada.
    try:
        if _tiene_acceso_notas(usuario):
            visibles = _filtrar_por_visibilidad(db.listar_notas(), usuario)
            _anotar_prioridad_efectiva(visibles)
            pend = _ids_pendientes_de_accion(usuario, visibles)
            respuestas = [{"texto": f"Respuesta de «{x['nombre_original']}»", "motivo": f"vence el {x['respuesta_vence'][8:10]}/{x['respuesta_vence'][5:7]}",
                           "url": f"/nota/{x['id']}"} for x in db.respuestas_por_vencer(3)
                          if auth.es_admin(usuario) or x.get("usuario_id") == usuario["id"]
                          or (usuario["rol"] == auth.ROL_COORDINADOR and x.get("equipo_id") == usuario.get("equipo_id"))]
            r["hoy"] = agentes.lista_hoy(usuario, visibles, pend, viajes_hoy + respuestas)[:12]
        else:
            r["hoy"] = agentes.lista_hoy(usuario, [], set(), viajes_hoy)[:12]
    except Exception:
        logging.exception("No se pudo armar la lista de hoy")
        r["hoy"] = []
    if auth.es_admin(usuario):
        try:
            r["reglas_nuevas"] = len(_patrones_pendientes())
        except Exception:
            r["reglas_nuevas"] = 0
    return r


@app.get("/inicio", response_class=HTMLResponse)
def pagina_inicio(request: Request):
    """Página principal: tarjetas con acceso a cada módulo y sus indicadores. Quien no tiene
    permiso de Notas entra directo a Traslados."""
    usuario = usuario_actual(request)
    if not _tiene_acceso_notas(usuario):
        if _tiene_acceso_autos(usuario):
            return RedirectResponse(url="/traslados")
        return templates.TemplateResponse("sin_sistemas.html", {"request": request, "usuario": usuario})
    return templates.TemplateResponse("inicio.html", {"request": request, "usuario": usuario, "res": _resumen_inicio(usuario),
                                                       "hoy": _hoy_local()})


@app.get("/login", response_class=HTMLResponse)
def login_formulario(request: Request, error: str = None, inactividad: int = 0, portal: int = 0):
    if request.session.get("usuario_id"):
        return RedirectResponse(url="/inicio")
    aviso = f"Se cerró la sesión por {MINUTOS_INACTIVIDAD} minutos sin uso. Volvé a ingresar." if inactividad else None
    if portal:
        aviso = "Tu sesión del portal PPN se cerró. Volvé a entrar desde el portal."
    return templates.TemplateResponse("login.html", {"request": request, "error": error, "aviso": aviso})


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    nombre_usuario = (form.get("usuario") or "").strip()
    clave = form.get("clave") or ""
    ip = request.client.host if request.client else ""

    if db.login_bloqueado(nombre_usuario, ip):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Demasiados intentos fallidos. Esperá 15 minutos y volvé a probar."},
            status_code=429,
        )

    usuario = db.obtener_usuario_por_nombre(nombre_usuario)
    correcto = bool(
        usuario
        and usuario["activo"]
        and auth.verificar_clave(clave, usuario["clave_hash"], usuario["clave_salt"])
    )
    db.registrar_intento_login(nombre_usuario, ip, correcto)
    if not correcto:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Usuario o clave incorrectos"},
            status_code=401,
        )

    request.session.clear()  # sesión nueva en cada ingreso
    request.session["usuario_id"] = usuario["id"]
    request.session["ultima_actividad"] = int(time.time())
    destino = "/perfil?obligatorio=1" if usuario.get("debe_cambiar_clave") else _pagina_inicial(usuario)
    volver = str(form.get("volver") or "")  # propuesta 140 (U43): después del cierre por inactividad, vuelve a la misma pantalla
    if volver and not usuario.get("debe_cambiar_clave") and not volver.startswith(("/login", "/logout", "/auth")):
        destino = rutas_ui.destino_interno(volver, destino)
    return RedirectResponse(url=destino, status_code=303)


@app.get("/sesion/activa")
def sesion_activa(request: Request):
    """La página avisa que la persona sigue usándola (el middleware ya renovó la actividad)."""
    return Response(status_code=204)


@app.get("/salud")
def salud():
    """Para monitoreo: responde 200 si la app y la base andan. Sin datos
    sensibles, por eso es pública (el detalle está en Administración)."""
    try:
        with db._conexion() as con:
            con.execute("SELECT 1").fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "base": f"error: {e}"}, status_code=503)
    return {"ok": True}


@app.get("/logout")
def logout(request: Request):
    token_sso = request.session.get("sso_token")
    request.session.clear()
    if token_sso and sso.activo():
        sso.cerrar_sesion(token_sso)
        if sso.PORTAL_URL:
            return RedirectResponse(url=sso.PORTAL_URL)
    return RedirectResponse(url="/login")


@app.get("/auth/ingreso/{token}")
def ingreso_portal(request: Request, token: str):
    """Llegada desde el portal PPN: el portal manda acá con un token de un día. Se pide al portal quién es la
    persona y se la hace entrar con SU usuario de esta app (vinculado o con el mismo correo). Si no tiene, queda
    pendiente para que la Administración la habilite: el portal identifica, los permisos se deciden acá."""
    ip = request.client.host if request.client else ""
    def error(texto, codigo=401):
        return templates.TemplateResponse("login.html", {"request": request, "error": texto}, status_code=codigo)
    if not sso.activo():
        return error("El ingreso con el portal PPN no está configurado en este sistema. Ingresá con tu usuario y clave.", 404)
    if db.login_bloqueado("portal-ppn", ip):
        return error("Demasiados intentos fallidos. Esperá 15 minutos y volvé a probar.", 429)
    try:
        persona = sso.obtener_sesion(token)
    except sso.ErrorPortal as e:
        logging.warning(str(e))
        return error("No se pudo comprobar tu ingreso con el portal PPN (sin conexión con el portal). Probá de nuevo o ingresá con tu usuario y clave.", 503)
    if not persona:
        db.registrar_intento_login("portal-ppn", ip, False)
        return error("El enlace del portal venció o no es válido. Volvé a entrar desde el portal PPN.")
    usuario, como = sso.usuario_local_para(persona)
    if not usuario:
        db.registrar_sso_pendiente(persona)
        return error(f"{persona['nombre'] or 'Tu cuenta'}: tu usuario del portal todavía no está habilitado en este sistema. "
                     "Ya quedó el pedido; la Dirección te va a dar acceso.", 403)
    if not usuario["activo"] or not persona["habilitado"]:
        return error("Tu usuario está desactivado. Consultá con la Dirección.", 403)
    if como == "correo":
        db.vincular_sso(usuario["id"], persona["sso_id"])
    db.registrar_intento_login(usuario["usuario"], ip, True)
    request.session.clear()
    request.session.update({"usuario_id": usuario["id"], "ultima_actividad": int(time.time()), "sso_token": token,
                            "sso_verificado": int(time.time())})
    return RedirectResponse(url=_pagina_inicial(usuario), status_code=303)


def _cantidad_admins_activos():
    return sum(1 for u in db.listar_usuarios() if u["activo"] and auth.es_admin(u))


def _estado_salud():
    """Detalle para el panel de Administración."""
    datos = {"base": "ok", "disco_libre_gb": None, "base_mb": None}
    try:
        with db._conexion() as con:
            con.execute("SELECT 1").fetchone()
    except Exception as e:
        datos["base"] = f"error: {e}"
    try:
        datos["disco_libre_gb"] = round(shutil.disk_usage(CARPETA_DATOS).free / 1e9, 1)
    except OSError:
        pass
    try:
        datos["base_mb"] = round(Path(db.RUTA_DB).stat().st_size / 1e6, 1)
    except OSError:
        pass
    return datos


ADMIN_PESTANAS = {
    "usuarios": ("Usuarios", [("listado", "Listado"), ("nuevo", "Usuario nuevo"), ("importar", "Importar"), ("revision", "Revisión trimestral"), ("portal", "Sistemas PPN (preparación)")]),
    "equipos": ("Equipos", [("listado", "Listado"), ("nuevo", "Equipo nuevo")]),
    "ia": ("Inteligencia artificial", [("modelos", "Modelos"), ("cola", "Cola y análisis"), ("calidad", "Tablero de agentes"), ("aprendizaje", "Aprendizaje"),
                                       ("referencia", "Documentos de referencia")]),
    "sistema": ("Estado del sistema", [("salud", "Salud del sistema"), ("estado", "Respaldos"), ("integridad", "Integridad"), ("base", "Base de datos"),
                                       ("correo", "Correo"), ("limpieza", "Borrar notas de prueba"), ("codigo", "Código")]),
    "seguridad": ("Seguridad", [("modo-seguro", "Modo seguro"), ("intentos", "Intentos fallidos"), ("accesos", "Accesos a documentos"), ("pin", "PIN de firma"), ("https", "Conexión cifrada")]),
}


def _volver_admin(request, mensaje=None, destino_fijo=None):
    """Después de una acción de Administración se vuelve a la pestaña donde
    estaba la persona (por el Referer), con el mensaje si hay. Después de CREAR algo se vuelve al listado
    (`destino_fijo`), donde se ve lo creado."""
    if mensaje:
        request.session["flash_admin"] = mensaje
    if destino_fijo:
        return RedirectResponse(url=destino_fijo, status_code=303)
    destino = "/admin"
    try:
        ruta = urlparse(request.headers.get("referer") or "").path
        if ruta == "/admin" or ruta.startswith("/admin/"):
            destino = ruta
    except Exception:
        pass
    return RedirectResponse(url=destino, status_code=303)


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/{pestana}", response_class=HTMLResponse)
@app.get("/admin/{pestana}/{sub}", response_class=HTMLResponse)
def admin_panel(request: Request, pestana: str = "usuarios", sub: str = "", mensaje: str = "", q: str = "", traer: int = 0,
                orden: str = "", dir: str = "asc", f_rol: str = "", f_equipo: str = "", f_estado: str = "", agrupar: str = ""):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # solo Dirección General (admin)
    if pestana not in ADMIN_PESTANAS:
        raise HTTPException(status_code=404, detail="Sección no encontrada")
    subs = [c for c, _ in ADMIN_PESTANAS[pestana][1]]
    if sub not in subs:
        sub = subs[0]
    mensaje = request.session.pop("flash_admin", None) or mensaje
    # Beta 01 (2): cada sección calcula solo lo suyo (antes, cualquier pestaña consultaba a la IA, la cola, los
    # respaldos y los accesos, y esperaba a Ollama).
    es_ia, es_sistema, es_seguridad = pestana == "ia", pestana == "sistema", pestana == "seguridad"
    usuarios = db.listar_usuarios()
    usuarios_total = len(usuarios)
    if q.strip():
        buscado = q.strip().lower()
        usuarios = [u for u in usuarios if buscado in f"{u['usuario']} {u['nombre']} {u.get('equipo_nombre') or ''}".lower()]
    vista_usuarios = None
    if pestana == "usuarios" and sub == "listado":
        usuarios, vista_usuarios = _ordenar_usuarios(usuarios, orden, dir, f_rol, f_equipo, f_estado, agrupar)
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "mensaje": mensaje,
            "pestana": pestana,
            "sub": sub,
            "pestanas": ADMIN_PESTANAS,
            "q": q,
            "usuarios_total": usuarios_total,
            "vista_usuarios": vista_usuarios,
            "feedback_ia": db.listar_feedback(50) if pestana == "ia" and sub == "aprendizaje" else [],
            "modelos_ia": nucleo.estado_modelos_ia() if es_ia else [],
            "ia_estado": automatico.estado_trabajador() if es_ia else {},
            "calidad": db.calidad_ia() if pestana == "ia" and sub == "calidad" else None,
            "tablero_agentes": db.tablero_agentes() if pestana == "ia" and sub == "calidad" else [],
            "adaptativa": carga.estado() if es_ia else {},
            "correo": {"activo": avisos_correo.activo(), "inmediatos": avisos_correo.inmediatos(),
                       "configurado": avisos_correo.configurado(), "servidor": avisos_correo.CONFIG["servidor"],
                       "hora": avisos_correo.CONFIG["resumen_hora"]},
            "verificador_activo": automatico.verificador_activo() if es_ia else False,
            "trabajos_ia": db.listar_trabajos(30) if es_ia else [],
            "usuario": usuario,
            "usuarios": usuarios,
            "equipos": db.listar_equipos(),
            "catalogo_unidades": db.listar_unidades() if pestana in ("equipos", "usuarios") else [],
            "unidades_de_equipo": {e["id"]: [u["id"] for u in db.unidades_de_equipo(e["id"])] for e in db.listar_equipos()} if pestana == "equipos" else {},
            "sugeridas_equipo": _sugeridas_por_equipo() if pestana == "equipos" else {},
            "delegaciones": [u for u in db.listar_unidades() if (u.get("zona") or "") == "Delegaciones PPN"] if pestana in ("usuarios", "equipos") else [],
            "integrantes_equipo": _integrantes_por_equipo() if pestana == "equipos" else {},
            "roles": auth.ROLES,
            "etiquetas_rol": auth.ETIQUETAS_ROL,
            "roles_de_equipo": auth.ROLES_DE_EQUIPO,
            "intentos_fallidos": db.listar_intentos_fallidos(15) if es_seguridad else [],
            "accesos": db.listar_accesos(25) if (es_seguridad or es_sistema) else [],
            "respaldo": respaldo.estado() if es_sistema else {},
            "revision": _datos_revision_usuarios() if pestana == "usuarios" and sub == "revision" else None,
            "portal": _datos_portal(traer) if pestana == "usuarios" and sub == "portal" else None,
            "salud": _estado_salud() if es_sistema else {},
            "limpieza": db.previa_limpieza_notas() if pestana == "sistema" and sub == "limpieza" else None,
            "limpieza_ultima": db.obtener_ajuste("limpieza_notas_ultima") if pestana == "sistema" and sub == "limpieza" else None,
            "n_archivados_sin_descarga": len(db.obtener_archivados_sin_descarga()) if es_sistema else 0,
            "ola5": _datos_admin_ola5(pestana, sub, request),
            "importacion": _revision_importacion(request) if pestana == "usuarios" and sub == "importar" else None,
            "clave_provisoria_importacion": importar_usuarios.CLAVE_PROVISORIA,
            "referencia": referencia_ia.resumen() if pestana == "ia" and sub == "referencia" else None,
        },
    )


_ORDEN_USUARIOS = {
    "usuario": lambda u: (u["usuario"] or "").lower(),
    "nombre": lambda u: (u["nombre"] or "").lower(),
    "rol": lambda u: (auth.ETIQUETAS_ROL.get(u["rol"], u["rol"]) or "").lower(),
    "equipo": lambda u: ((u.get("equipo_nombre") or "~").lower(), (u["nombre"] or "").lower()),
    "estado": lambda u: (0 if u["activo"] else 1, (u["nombre"] or "").lower()),
}


def _ordenar_usuarios(usuarios, orden, direccion, f_rol, f_equipo, f_estado, agrupar):
    """Listado de usuarios de Administración: filtros por rol, equipo y estado; orden por cualquier columna;
    y, si se pide, agrupado por equipo o por rol. Devuelve (usuarios, datos de la vista)."""
    if f_rol in auth.ROLES:
        usuarios = [u for u in usuarios if u["rol"] == f_rol]
    if f_equipo == "sin":
        usuarios = [u for u in usuarios if not u.get("equipo_id")]
    elif f_equipo.isdigit():
        usuarios = [u for u in usuarios if u.get("equipo_id") == int(f_equipo)]
    if f_estado == "activos":
        usuarios = [u for u in usuarios if u["activo"]]
    elif f_estado == "desactivados":
        usuarios = [u for u in usuarios if not u["activo"]]
    orden = orden if orden in _ORDEN_USUARIOS else ("equipo" if agrupar == "equipo" else "rol" if agrupar == "rol" else "nombre")
    usuarios = sorted(usuarios, key=_ORDEN_USUARIOS[orden], reverse=(direccion == "desc"))
    grupos = None
    if agrupar in ("equipo", "rol"):
        clave = (lambda u: u.get("equipo_nombre") or "Sin equipo") if agrupar == "equipo" else (lambda u: auth.ETIQUETAS_ROL.get(u["rol"], u["rol"]))
        grupos = {}
        for u in sorted(usuarios, key=lambda u: ((clave(u) == "Sin equipo"), clave(u).lower())):
            grupos.setdefault(clave(u), []).append(u)
        for k in grupos:
            grupos[k].sort(key=_ORDEN_USUARIOS["nombre" if orden in ("equipo", "rol") else orden], reverse=(direccion == "desc" and orden not in ("equipo", "rol")))
        grupos = list(grupos.items())
    return usuarios, {"orden": orden, "dir": "desc" if direccion == "desc" else "asc", "f_rol": f_rol, "f_equipo": f_equipo,
                      "f_estado": f_estado, "agrupar": agrupar if agrupar in ("equipo", "rol") else "", "grupos": grupos}


def _datos_admin_ola5(pestana, sub, request):
    """Secciones nuevas de Administración (ola 5): salud, base de datos, PIN de firma y conexión cifrada."""
    if pestana == "sistema" and sub == "salud":
        chequeos = salud_sistema.revisar()
        return {"chequeos": chequeos, "resumen": salud_sistema.resumen(chequeos), "historial": salud_sistema.historial(),
                "ultima": db.obtener_ajuste("salud_ultima")}
    if pestana == "sistema" and sub == "base":
        import migrar_postgresql
        try:
            tablas = migrar_postgresql.informe(db.RUTA_DB)
        except Exception:
            tablas = []
        return {"medidor": dict(db.MEDIDOR), "pedidos": PEDIDOS, "umbral_lento": UMBRAL_LENTO_MS, "tablas": tablas, "filas": sum(n for _, n in tablas),
                "usuarios_activos": sum(1 for u in db.listar_usuarios() if u["activo"])}
    if pestana == "sistema" and sub == "codigo":
        reporte_nombre = request.query_params.get("reporte", "")
        return {
            "reportes": analisis_codigo.listar_reportes(),
            "reporte_seleccionado": analisis_codigo.obtener_reporte(reporte_nombre) if reporte_nombre else {},
            "reporte_nombre": reporte_nombre,
            "lentos": monitor_latencia.listar_lentos(limite=50),
            "ultimo_perfil": _ultimo_perfil_texto,
            "pyinstrument_ok": _PYINSTRUMENT_OK,
        }
    if pestana == "seguridad" and sub == "modo-seguro":
        return {"soberania": soberania.estado(_destinos_configurados()), "salidas": soberania.listar(), "htmx_local": _HTMX_LOCAL}
    if pestana == "seguridad" and sub == "pin":
        return {"usuarios": pin.estado_usuarios(), "eventos": pin.eventos_recientes()}
    if pestana == "seguridad" and sub == "https":
        import https_interno
        return {"estado": https_interno.estado(), "activo": _HTTPS, "esquema": request.url.scheme if getattr(request, "url", None) is not None and hasattr(request.url, "scheme") else ""}
    return None


def _destinos_configurados():
    """Todo lo configurado que podría conectar con otro equipo, para revisar que sea de la red del organismo."""
    import almacen_ppn
    import detenidos
    return [("IA (Ollama)", nucleo.OLLAMA_URL, "url"), ("Correo (servidor SMTP)", avisos_correo.CONFIG.get("servidor", ""), "url"),
            ("Portal PPN (ingreso único)", sso.URL, "url"), ("Almacén de archivos PPN", almacen_ppn.URL, "url"),
            ("Sistema de Mesa de Entradas", mesa_entradas.URL, "url"),
            ("Sistema del menú de la Procuración (personas detenidas)", detenidos.EXTERNO.get("url", ""), "url"),
            ("Carpeta de respaldos", str(respaldo._carpeta()), "carpeta"),
            ("Copia externa de respaldos", str(respaldo.CONFIG.get("copia_externa") or ""), "carpeta")]


def _datos_portal(traer=False):
    """Pestaña «Portal PPN»: configuración, pedidos de acceso pendientes y (a pedido) el cruce con las personas del portal."""
    usuarios = db.listar_usuarios()
    equipos = db.listar_equipos()
    d = {"activo": sso.activo(), "url": sso.URL, "aplicacion_id": sso.APLICACION_ID, "portal_url": sso.PORTAL_URL,
         "pendientes": [], "vinculados": [u for u in usuarios if u.get("sso_id") is not None],
         "sin_vincular": [u for u in usuarios if u.get("sso_id") is None and u["activo"]], "cruce": None, "error": None,
         "prep": sso.preparacion(usuarios, equipos),
         "mesa": {"activo": mesa_entradas.activo(), "tipos": mesa_entradas.TIPOS,
                  "compat": mesa_entradas.compatibilidad(db.numeros_expediente_cargados())},
         "almacen": {"activo": almacen_ppn.activo()}}
    for p in db.listar_sso_pendientes():
        p["equipo_sugerido"] = sso.equipo_para_area(p.get("area"), equipos)
        p["usuario_sugerido"] = db.usuario_por_correo(p.get("correo")) if p.get("correo") else None
        d["pendientes"].append(p)
    if traer and sso.activo():
        try:
            d["cruce"] = sso.cruce(sso.personas_del_portal(), usuarios, equipos)
        except sso.ErrorPortal as e:
            d["error"] = str(e)
    return d


def _volver_portal(request, mensaje):
    request.session["flash_admin"] = mensaje
    return RedirectResponse(url="/admin/usuarios/portal", status_code=303)


@app.post("/admin/portal/areas")
async def admin_portal_areas(request: Request):
    """Guarda qué área del portal PPN corresponde a cada equipo (preparación; no se conecta a nada)."""
    _exigir_rol(usuario_actual(request))
    form = await request.form()
    validas = {a["id"] for a in sso.areas_locales()}
    cambios = 0
    for e in db.listar_equipos():
        v = str(form.get(f"area_{e['id']}") or "")
        nuevo = int(v) if v.isdigit() and int(v) in validas else None
        if nuevo != e.get("sso_area_id"):
            db.fijar_area_portal(e["id"], nuevo)
            cambios += 1
    return _volver_portal(request, f"Áreas del portal guardadas ({cambios} cambio(s)).")


@app.get("/admin/portal/planilla.csv")
def admin_portal_planilla(request: Request):
    """Planilla para quien administre el portal: personas activas con correo, rol y el área del portal de su equipo.
    Sirve para darlas de alta en «Notas DGPDH» el día que se decida vincular."""
    _exigir_rol(usuario_actual(request))
    import csv, io
    areas = {a["id"]: a["nombre"] for a in sso.areas_locales()}
    equipos = {e["id"]: e for e in db.listar_equipos()}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Usuario", "Nombre", "Correo", "Rol en Notas DGPDH", "Equipo", "ID área portal", "Área portal", "Vinculado"])
    for u in db.listar_usuarios():
        if not u["activo"]:
            continue
        eq = equipos.get(u.get("equipo_id")) or {}
        aid = eq.get("sso_area_id")
        w.writerow([u["usuario"], u["nombre"], u.get("correo") or "", auth.ETIQUETAS_ROL.get(u["rol"], u["rol"]), eq.get("nombre") or "",
                    aid or "", areas.get(aid, ""), "Sí" if u.get("sso_id") is not None else ""])
    return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="usuarios_para_portal_ppn.csv"'})


@app.post("/admin/portal/vincular")
async def admin_portal_vincular(request: Request):
    _exigir_rol(usuario_actual(request))
    form = await request.form()
    uid, sid = str(form.get("usuario_id") or ""), str(form.get("sso_id") or "")
    if not uid.isdigit() or not sid.isdigit() or not db.obtener_usuario(int(uid)):
        return _volver_portal(request, "⚠ Elegí a qué usuario vincular.")
    db.vincular_sso(int(uid), int(sid), (form.get("correo") or "").strip().lower() or None)
    return _volver_portal(request, f"Vinculado: {db.obtener_usuario(int(uid))['nombre']} ya puede entrar desde el portal PPN.")


@app.post("/admin/portal/desvincular")
async def admin_portal_desvincular(request: Request):
    _exigir_rol(usuario_actual(request))
    form = await request.form()
    uid = str(form.get("usuario_id") or "")
    if uid.isdigit():
        db.vincular_sso(int(uid), None)
    return _volver_portal(request, "Desvinculado del portal. Sigue pudiendo entrar con su usuario y clave.")


@app.post("/admin/portal/descartar")
async def admin_portal_descartar(request: Request):
    _exigir_rol(usuario_actual(request))
    form = await request.form()
    sid = str(form.get("sso_id") or "")
    if sid.isdigit():
        db.descartar_sso_pendiente(int(sid))
    return _volver_portal(request, "Pedido descartado.")


@app.post("/admin/portal/crear")
async def admin_portal_crear(request: Request):
    """Crea el usuario local de una persona del portal (con rol y equipo que elige la Dirección) y lo deja vinculado.
    Se le genera una clave al azar: entra por el portal; si algún día necesita clave propia, se la blanquea desde Listado."""
    _exigir_rol(usuario_actual(request))
    form = await request.form()
    sid = str(form.get("sso_id") or "")
    nombre = " ".join(str(form.get("nombre") or "").split())
    correo = (form.get("correo") or "").strip().lower()
    rol = (form.get("rol") or "").strip()
    equipo_id = form.get("equipo_id") or None
    if not sid.isdigit() or not nombre or rol not in auth.ROLES:
        return _volver_portal(request, "⚠ Faltan datos: nombre y rol.")
    if rol in auth.ROLES_DE_EQUIPO and not equipo_id:
        return _volver_portal(request, "⚠ Un Asesor o Coordinador tiene que tener equipo.")
    base = (correo.split("@")[0] if correo else sso._norm(nombre).replace(" ", ".")) or f"portal{sid}"
    nombre_usuario, n = base, 1
    while db.obtener_usuario_por_nombre(nombre_usuario):
        n += 1
        nombre_usuario = f"{base}{n}"
    hash_clave, salt = auth.generar_hash_clave(uuid.uuid4().hex + "Aa1")
    acceso = rol != auth.ROL_CHOFER
    equipo = int(equipo_id) if equipo_id and str(equipo_id).isdigit() and rol in auth.ROLES_DE_EQUIPO else None
    nuevo = db.crear_usuario(nombre_usuario, hash_clave, salt, nombre, rol, equipo, debe_cambiar_clave=True, acceso_notas=acceso)
    if not nuevo:
        return _volver_portal(request, "⚠ No se pudo crear el usuario.")
    db.vincular_sso(nuevo, int(sid), correo or None)
    traslados.sincronizar_choferes()
    return _volver_portal(request, f"Usuario «{nombre_usuario}» creado para {nombre} y vinculado al portal.")


@app.post("/admin/equipos")
async def admin_crear_equipo(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="Falta el nombre del equipo")
    db.crear_equipo(nombre)
    return _volver_admin(request, f"Equipo «{nombre}» creado correctamente.", "/admin/equipos")


@app.post("/admin/usuarios")
async def admin_crear_usuario(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    nombre_usuario = (form.get("usuario") or "").strip()
    clave = form.get("clave") or ""
    nombre = (form.get("nombre") or "").strip()
    rol = (form.get("rol") or "").strip()
    equipo_id = form.get("equipo_id") or None

    if not nombre_usuario or not clave or not nombre or rol not in auth.ROLES:
        raise HTTPException(status_code=422, detail="Faltan datos obligatorios o el rol no es válido")
    error_clave = auth.validar_clave_nueva(clave, nombre_usuario)
    if error_clave:
        raise HTTPException(status_code=422, detail=error_clave)
    if rol in auth.ROLES_DE_EQUIPO and not equipo_id:
        raise HTTPException(status_code=422, detail="Un Asesor o Coordinador tiene que tener un equipo asignado")
    if db.obtener_usuario_por_nombre(nombre_usuario):
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese nombre")

    # El equipo se guarda para cualquier rol (Mesa de Entradas, Dirección…); para Asesor y Coordinador es obligatorio.
    equipo_id = int(equipo_id) if str(equipo_id or "").isdigit() else None
    hash_clave, salt = auth.generar_hash_clave(clave)
    # La clave que pone el administrador es provisoria: la persona la cambia
    # en su primer ingreso.
    acceso = form.get("acceso_notas") == "1" or auth.ROLES_DIRECCION.issuperset({rol})
    if rol == auth.ROL_CHOFER:
        acceso = False
    nuevo_id = db.crear_usuario(nombre_usuario, hash_clave, salt, nombre, rol, equipo_id, debe_cambiar_clave=True, acceso_notas=acceso)
    if nuevo_id and form.get("acceso_autos_enviado"):
        db.actualizar_usuario(nuevo_id, nombre, rol, equipo_id, acceso_traslados=(form.get("acceso_autos") == "1" or rol == auth.ROL_CHOFER))
    circuito = form.get("circuito") if form.get("circuito") in ("AMBA", "DELEGACIONES") else "AMBA"
    deleg = int(form["delegacion_id"]) if str(form.get("delegacion_id") or "").isdigit() and circuito == "DELEGACIONES" else None
    if nuevo_id:
        db.actualizar_usuario(nuevo_id, nombre, rol, equipo_id, coordina_traslados=(form.get("coordina_traslados") == "1"),
                              circuito=circuito, delegacion_id=deleg, personal=(form.get("personal") == "1"))
    traslados.sincronizar_choferes()
    return _volver_admin(request, f"Usuario «{nombre}» ({nombre_usuario}) creado correctamente como "
                                  f"{auth.ETIQUETAS_ROL.get(rol, rol)}. La clave es provisoria: la cambia en su primer ingreso.",
                         "/admin/usuarios")


@app.post("/admin/usuarios/{usuario_id}/desactivar")
def admin_desactivar_usuario(request: Request, usuario_id: int):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    if usuario_id == usuario["id"]:
        raise HTTPException(status_code=409, detail="No podés desactivar tu propia cuenta.")
    objetivo = db.obtener_usuario(usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if auth.es_admin(objetivo) and objetivo["activo"] and _cantidad_admins_activos() <= 1:
        raise HTTPException(status_code=409, detail="Es el último administrador activo: no se puede desactivar.")
    db.desactivar_usuario(usuario_id)
    return _volver_admin(request, f"Usuario «{objetivo['nombre']}» desactivado (se conserva su historial).")


@app.post("/admin/usuarios/{usuario_id}/reactivar")
def admin_reactivar_usuario(request: Request, usuario_id: int):
    _exigir_rol(usuario_actual(request))
    objetivo = db.obtener_usuario(usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db.reactivar_usuario(usuario_id)
    return _volver_admin(request, f"Usuario «{objetivo['nombre']}» reactivado.")


@app.post("/admin/usuarios/{usuario_id}/editar")
async def admin_editar_usuario(request: Request, usuario_id: int):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    objetivo = db.obtener_usuario(usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    rol = (form.get("rol") or "").strip()
    equipo_id = form.get("equipo_id") or None
    if not nombre or rol not in auth.ROLES:
        raise HTTPException(status_code=422, detail="Faltan datos o el rol no es válido")
    if rol in auth.ROLES_DE_EQUIPO and not equipo_id:
        raise HTTPException(status_code=422, detail="Un Asesor o Coordinador tiene que tener un equipo asignado")
    if auth.es_admin(objetivo) and not auth.ROLES_DIRECCION.issuperset({rol}) and _cantidad_admins_activos() <= 1:
        raise HTTPException(status_code=409, detail="Es el último administrador: no se le puede quitar el rol.")
    # El equipo se guarda para cualquier rol (Mesa de Entradas, Dirección…); para Asesor y Coordinador es obligatorio.
    equipo_id = int(equipo_id) if str(equipo_id or "").isdigit() else None
    acceso = (form.get("acceso_notas") == "1") if form.get("acceso_notas_enviado") else None
    if acceso is False and auth.ROLES_DIRECCION.issuperset({rol}):
        acceso = True  # Dirección siempre ve Notas
    if rol == auth.ROL_CHOFER:
        acceso = False
    coordina = (form.get("coordina_traslados") == "1") if form.get("coordina_enviado") else None
    circuito = form.get("circuito") if form.get("circuito") in ("AMBA", "DELEGACIONES") else None
    deleg = -1
    if circuito:
        deleg = int(form["delegacion_id"]) if circuito == "DELEGACIONES" and str(form.get("delegacion_id") or "").isdigit() else None
    personal = (form.get("personal") == "1") if form.get("coordina_enviado") else None
    autos = (form.get("acceso_autos") == "1") if form.get("acceso_autos_enviado") else None
    if autos is False and (auth.ROLES_DIRECCION.issuperset({rol}) or rol == auth.ROL_CHOFER):
        autos = True  # la Dirección y los choferes siempre ven Autos PPN
    entrevistas = (form.get("carga_entrevistas") == "1") if form.get("coordina_enviado") else None
    mantiene = (form.get("mantiene_bases") == "1") if form.get("coordina_enviado") else None
    db.actualizar_usuario(usuario_id, nombre, rol, equipo_id, acceso_notas=acceso, coordina_traslados=coordina,
                          circuito=circuito, delegacion_id=deleg, personal=personal, acceso_traslados=autos, carga_entrevistas=entrevistas,
                          mantiene_bases=mantiene)
    traslados.sincronizar_choferes()
    equipos = {e["id"]: e["nombre"] for e in db.listar_equipos()}
    cambios = []
    if objetivo["rol"] != rol:
        cambios.append(f"rol: {auth.ETIQUETAS_ROL.get(objetivo['rol'], objetivo['rol'])} → {auth.ETIQUETAS_ROL.get(rol, rol)}")
    if objetivo.get("equipo_id") != equipo_id:
        cambios.append(f"equipo: {equipos.get(objetivo.get('equipo_id'), 'sin equipo')} → {equipos.get(equipo_id, 'sin equipo')}")
    consulta_obst = (form.get("consulta_obstaculizaciones") == "1") if form.get("coordina_enviado") else None
    if consulta_obst and (rol == auth.ROL_CHOFER or acceso is False):
        consulta_obst = False  # el permiso de consulta es para personas con Notas habilitado (nunca choferes)
    if consulta_obst is not None and bool(objetivo.get("consulta_obstaculizaciones")) != consulta_obst:
        with db._conexion() as con:
            con.execute("UPDATE usuarios SET consulta_obstaculizaciones = ? WHERE id = ?", (1 if consulta_obst else 0, usuario_id))
            from dgpdh.seguridad import auditoria as _au
            _au.registrar(con, accion="Permiso «Consulta de obstaculizaciones» " + ("habilitado" if consulta_obst else "quitado"), entidad="usuario",
                          entidad_id=usuario_id, actor=_au.Actor.desde(usuario if isinstance(usuario, dict) else None))
        cambios.append("consulta de obstaculizaciones: " + ("habilitada" if consulta_obst else "quitada"))
    if mantiene is not None and bool(objetivo.get("mantiene_bases")) != mantiene:
        cambios.append("mantenimiento de bases: " + ("habilitado" if mantiene else "quitado"))
        logging.info(f"Permiso de mantenimiento de bases de {nombre}: {'habilitado' if mantiene else 'quitado'} por {usuario['nombre'] if isinstance(usuario, dict) else usuario}")
    return _volver_admin(request, f"Datos de «{nombre}» guardados correctamente" + (f" ({'; '.join(cambios)})." if cambios else "."))


@app.post("/admin/usuarios/{usuario_id}/sistemas")
async def admin_sistemas_usuario(request: Request, usuario_id: int):
    """Habilita o deshabilita, desde el listado, el sistema de Notas o el de Autos PPN de una persona."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    objetivo = db.obtener_usuario(usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    form = await request.form()
    sistema, valor = form.get("sistema"), form.get("valor") == "1"
    nombre_sis = {"notas": "Notas a la firma", "autos": "Autos PPN"}.get(sistema)
    if not nombre_sis:
        raise HTTPException(status_code=422, detail="Sistema desconocido")
    fijo = None
    if objetivo["rol"] in auth.ROLES_DIRECCION:
        fijo = "La Dirección General ve siempre los dos sistemas."
    elif objetivo["rol"] == auth.ROL_CHOFER:
        fijo = "Los choferes ven solo Autos PPN." if (sistema == "notas" and valor) or (sistema == "autos" and not valor) else None
    if fijo:
        return _volver_admin(request, f"⚠ {fijo}")
    if sistema == "notas":
        db.actualizar_usuario(usuario_id, objetivo["nombre"], objetivo["rol"], objetivo.get("equipo_id"), acceso_notas=valor)
    else:
        db.actualizar_usuario(usuario_id, objetivo["nombre"], objetivo["rol"], objetivo.get("equipo_id"), acceso_traslados=valor)
    aviso = ""
    if sistema == "autos" and not valor and objetivo.get("coordina_traslados"):
        aviso = " Ojo: coordina traslados, así que sigue entrando a Autos PPN hasta que se le quite esa función."
    return _volver_admin(request, f"{nombre_sis} {'habilitado' if valor else 'deshabilitado'} para «{objetivo['nombre']}».{aviso}")


def _integrantes_por_equipo():
    """{equipo_id: {"total", "notas", "autos", "amba", "delegaciones"}} de los integrantes activos (para el panel de equipos)."""
    salida = {}
    for u in db.listar_usuarios():
        if not u["activo"] or not u.get("equipo_id"):
            continue
        d = salida.setdefault(u["equipo_id"], {"total": 0, "notas": 0, "autos": 0, "amba": 0, "delegaciones": 0})
        d["total"] += 1
        d["notas"] += 1 if (u["rol"] in auth.ROLES_DIRECCION or (u.get("acceso_notas") and u["rol"] != auth.ROL_CHOFER)) else 0
        d["autos"] += 1 if (u["rol"] in auth.ROLES_DIRECCION or u["rol"] == auth.ROL_CHOFER or u.get("acceso_traslados") or u.get("coordina_traslados")) else 0
        d["delegaciones" if u.get("circuito") == "DELEGACIONES" else "amba"] += 1
        d["entrevistas"] = d.get("entrevistas", 0) + (1 if u.get("carga_entrevistas") else 0)
    return salida


def _si_no(valor):
    return True if valor == "1" else False if valor == "0" else None


def _aplicar_lote(admin, objetivos, form, permitir_datos=True):
    """Aplica los mismos cambios a varios usuarios. Cada campo vacío = «sin cambios». Respeta las reglas de cada rol
    (la Dirección ve siempre los dos sistemas, los choferes solo Autos PPN, nadie se desactiva a sí mismo, no se deja sin
    administradores). Devuelve (aplicados, avisos)."""
    notas, autos = _si_no(form.get("notas")), _si_no(form.get("autos"))
    coordina, personal = _si_no(form.get("coordina")), _si_no(form.get("personal"))
    entrevistas = _si_no(form.get("entrevistas"))
    mantiene = _si_no(form.get("mantiene"))
    circuito = form.get("circuito") if form.get("circuito") in ("AMBA", "DELEGACIONES") else None
    deleg = -1
    if circuito == "DELEGACIONES" and str(form.get("delegacion_id") or "").isdigit():
        deleg = int(form["delegacion_id"])
    elif circuito == "AMBA":
        deleg = None
    rol = form.get("rol") if permitir_datos and form.get("rol") in auth.ROLES else None
    equipo = form.get("equipo_id") if permitir_datos else ""
    estado = form.get("estado") if permitir_datos and form.get("estado") in ("activar", "desactivar") else None
    if not any(x is not None for x in (notas, autos, coordina, personal, entrevistas, mantiene, circuito, rol, estado)) and not equipo:
        return 0, ["No se eligió ningún cambio."]
    aplicados, avisos = 0, []
    for u in objetivos:
        nuevo_rol = rol or u["rol"]
        nuevo_eq = u.get("equipo_id") if not equipo else (None if equipo == "sin" else int(equipo) if str(equipo).isdigit() else u.get("equipo_id"))
        if nuevo_rol in auth.ROLES_DE_EQUIPO and not nuevo_eq:
            avisos.append(f"{u['nombre']}: un Asesor o Coordinador necesita equipo (no se cambió).")
            continue
        if auth.es_admin(u) and nuevo_rol not in auth.ROLES_DIRECCION and _cantidad_admins_activos() <= 1:
            avisos.append(f"{u['nombre']}: es el último administrador (no se le quitó el rol).")
            continue
        n, a = notas, autos
        if nuevo_rol in auth.ROLES_DIRECCION:
            n = True if n is not None else None
            a = True if a is not None else None
        elif nuevo_rol == auth.ROL_CHOFER:
            n = False if n is not None else None
            a = True if a is not None else None
        db.actualizar_usuario(u["id"], u["nombre"], nuevo_rol, nuevo_eq, acceso_notas=n, coordina_traslados=coordina,
                              circuito=circuito, delegacion_id=deleg, personal=personal, acceso_traslados=a, carga_entrevistas=entrevistas,
                              mantiene_bases=mantiene)
        if estado == "desactivar" and u["activo"]:
            if u["id"] == admin["id"]:
                avisos.append("Tu propia cuenta no se desactiva.")
            elif auth.es_admin(u) and _cantidad_admins_activos() <= 1:
                avisos.append(f"{u['nombre']}: es el último administrador activo (no se desactivó).")
            else:
                db.desactivar_usuario(u["id"])
        elif estado == "activar" and not u["activo"]:
            db.reactivar_usuario(u["id"])
        aplicados += 1
    traslados.sincronizar_choferes()
    if (notas is not None or autos is not None) and any(u["rol"] in auth.ROLES_DIRECCION or u["rol"] == auth.ROL_CHOFER for u in objetivos):
        avisos.append("La Dirección ve siempre los dos sistemas y los choferes solo Autos PPN: en ellos se mantuvo.")
    return aplicados, avisos


@app.post("/admin/usuarios/lote")
async def admin_usuarios_lote(request: Request):
    """Los mismos cambios para varios usuarios marcados en el listado."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    ids = {int(x) for x in form.getlist("usuario_id") if str(x).isdigit()}
    objetivos = [u for u in db.listar_usuarios() if u["id"] in ids]
    if not objetivos:
        return _volver_admin(request, "⚠ Marcá al menos un usuario.")
    n, avisos = _aplicar_lote(usuario, objetivos, form)
    if not n:
        return _volver_admin(request, "⚠ " + " ".join(avisos))
    return _volver_admin(request, f"Cambios aplicados a {n} usuario(s)." + ((" " + " ".join(dict.fromkeys(avisos))) if avisos else ""))


@app.post("/admin/equipos/{equipo_id}/permisos")
async def admin_equipo_permisos(request: Request, equipo_id: int):
    """Permisos de un equipo: se aplican a cada integrante activo (sistemas, circuito y delegación)."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    equipo = next((e for e in db.listar_equipos() if e["id"] == equipo_id), None)
    if equipo is None:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")
    form = await request.form()
    objetivos = [u for u in db.listar_usuarios() if u["activo"] and u.get("equipo_id") == equipo_id]
    if not objetivos:
        request.session["flash_admin"] = f"⚠ El equipo «{equipo['nombre']}» no tiene integrantes activos."
        return RedirectResponse(url="/admin/equipos/listado", status_code=303)
    n, avisos = _aplicar_lote(usuario, objetivos, form, permitir_datos=False)
    request.session["flash_admin"] = (f"Permisos aplicados a los {n} integrante(s) de «{equipo['nombre']}»." if n else "⚠") + \
        ((" " + " ".join(dict.fromkeys(avisos))) if avisos else "")
    return RedirectResponse(url=f"/admin/equipos/listado#equipo-{equipo_id}", status_code=303)


# ---------- Importar usuarios desde Excel (pantalla) ----------
import importar_usuarios  # noqa: E402

CARPETA_IMPORTACIONES = CARPETA_DATOS / "importaciones"


def _archivo_importacion(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    return CARPETA_IMPORTACIONES / f"{token}.json"


def _revision_importacion(request):
    """La revisión de una planilla subida (sin guardar nada): qué usuarios y equipos se crearían, cuáles ya existen y errores."""
    token = request.query_params.get("revision", "")
    ruta = _archivo_importacion(token)
    if not ruta or not ruta.is_file():
        return {"token": None} if token else None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    r = importar_usuarios.procesar(datos["filas"], simular=True)
    r.update(token=token, archivo=datos.get("archivo", ""))
    return r


@app.get("/admin/usuarios/importar/modelo")
def admin_importar_modelo(request: Request):
    """Planilla modelo con las columnas que lee la importación."""
    _exigir_rol(usuario_actual(request))
    import openpyxl
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Usuarios"
    hoja.append(["Nombre", "Apellido", "Email", "Rol", "Area"])
    hoja.append(["Ana", "Pérez", "aperez@ppn.gov.ar", "Asesor", "Equipo de ejemplo"])
    hoja.append(["Carlos", "Gómez", "cgomez@ppn.gov.ar", "Coordinador", "Equipo de ejemplo"])
    hoja.append(["Marta", "Ruiz", "mruiz@ppn.gov.ar", "Asesor", "Mesa de Entradas"])
    for col, ancho in zip("ABCDE", (16, 18, 28, 16, 34)):
        hoja.column_dimensions[col].width = ancho
    buffer = io.BytesIO()
    libro.save(buffer)
    return StreamingResponse(iter([buffer.getvalue()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=modelo_importar_usuarios.xlsx"})


@app.post("/admin/usuarios/importar/revisar")
async def admin_importar_revisar(request: Request):
    """Lee la planilla y muestra qué se haría, SIN guardar nada todavía."""
    _exigir_rol(usuario_actual(request))
    form = await request.form()
    archivo = form.get("planilla")
    nombre = getattr(archivo, "filename", "") or ""
    if not nombre:
        return _volver_admin(request, "⚠ Elegí la planilla de Excel (.xlsx) con los usuarios.", "/admin/usuarios/importar")
    if not nombre.lower().endswith(".xlsx"):
        return _volver_admin(request, "⚠ La planilla tiene que ser de Excel (.xlsx). Si es .xls o .csv, abrila en Excel y guardala como .xlsx.",
                             "/admin/usuarios/importar")
    contenido = await archivo.read()
    if len(contenido) > 10 * 1024 * 1024:
        return _volver_admin(request, "⚠ La planilla supera los 10 MB.", "/admin/usuarios/importar")
    try:
        filas = importar_usuarios.leer_planilla(contenido)
    except Exception:
        return _volver_admin(request, "⚠ No se pudo leer la planilla: verificá que sea un Excel (.xlsx) válido.", "/admin/usuarios/importar")
    filas = [[None if v is None else str(v) for v in f] for f in filas if any(v not in (None, "") for v in f)]
    if not filas:
        return _volver_admin(request, "⚠ La planilla no tiene filas con datos.", "/admin/usuarios/importar")
    CARPETA_IMPORTACIONES.mkdir(parents=True, exist_ok=True)
    for viejo in list(CARPETA_IMPORTACIONES.glob("*.json")) + list(CARPETA_IMPORTACIONES.glob("*.orig")):  # revisiones abandonadas de más de un día
        try:
            if time.time() - viejo.stat().st_mtime > 86400:
                viejo.unlink()
        except OSError:
            pass
    token = secrets.token_hex(16)
    _archivo_importacion(token).write_text(json.dumps({"archivo": nombre, "filas": filas}, ensure_ascii=False), encoding="utf-8")
    _archivo_importacion(token).with_suffix(".orig").write_bytes(contenido)
    return RedirectResponse(url=f"/admin/usuarios/importar?revision={token}", status_code=303)


@app.post("/admin/usuarios/importar/confirmar")
async def admin_importar_confirmar(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    ruta = _archivo_importacion(form.get("token"))
    if not ruta or not ruta.is_file():
        return _volver_admin(request, "⚠ La revisión ya no está disponible: volvé a subir la planilla.", "/admin/usuarios/importar")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    r = importar_usuarios.procesar(datos["filas"], simular=False)
    orig = ruta.with_suffix(".orig")
    if orig.is_file():  # el original queda guardado en la carpeta de bases cargadas
        import archivo_cargas
        archivo_cargas.guardar("usuarios", None, datos.get("archivo"), orig.read_bytes(), usuario)
        orig.unlink(missing_ok=True)
    ruta.unlink(missing_ok=True)
    traslados.sincronizar_choferes()
    logging.info(f"Importación de usuarios por {usuario['nombre']}: {len(r['nuevos'])} nuevos, {len(r['equipos_nuevos'])} equipos")
    msg = (f"Importación lista: {len(r['nuevos'])} usuario(s) y {len(r['equipos_nuevos'])} equipo(s) nuevos"
           + (f"; {len(r['salteados'])} ya existían" if r["salteados"] else "")
           + (f"; {len(r['errores'])} fila(s) con error no se importaron" if r["errores"] else "") + ".")
    if r["nuevos"]:
        msg += f" Ingresan con su email y la clave provisoria {importar_usuarios.CLAVE_PROVISORIA}; el sistema les pide cambiarla."
    return _volver_admin(request, msg, "/admin/usuarios/listado")


@app.post("/admin/usuarios/{usuario_id}/clave-provisoria")
def admin_clave_provisoria(request: Request, usuario_id: int):
    """Para quien olvidó su clave: genera una provisoria (se muestra UNA vez)
    que la persona tiene que cambiar al ingresar."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    objetivo = db.obtener_usuario(usuario_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    letras = "abcdefghjkmnpqrstuvwxyz"
    provisoria = "".join(random.SystemRandom().choice(letras) for _ in range(7)) + str(random.SystemRandom().randint(100, 999))
    hash_clave, salt = auth.generar_hash_clave(provisoria)
    db.cambiar_clave_usuario(usuario_id, hash_clave, salt, debe_cambiar=True)
    request.session["flash_admin"] = (
        f"Clave provisoria de {objetivo['nombre']} ({objetivo['usuario']}): {provisoria} — "
        "pasásela en persona; tiene que cambiarla al ingresar. No se vuelve a mostrar."
    )
    return _volver_admin(request)


@app.post("/admin/respaldo/ahora")
def admin_respaldo_ahora(request: Request):
    _exigir_rol(usuario_actual(request))
    ok, resumen = respaldo.hacer_respaldo()
    return _volver_admin(request, resumen)


@app.post("/admin/limpieza-notas")
async def admin_limpieza_notas(request: Request):
    """Borra todas las notas (pruebas con notas ya despachadas) y conserva lo aprendido. Antes hace un respaldo
    completo: si el respaldo falla, no se borra nada."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    if str(form.get("confirmacion") or "").strip().upper() != "BORRAR NOTAS" or not form.get("entiendo"):
        return _volver_admin(request, "⚠ No se borró nada: escribí BORRAR NOTAS y marcá la casilla para confirmar.")
    ok, resumen = respaldo.hacer_respaldo()
    if not ok:
        return _volver_admin(request, f"⚠ No se borró nada porque el respaldo previo falló: {resumen}")
    r = db.limpiar_notas(CARPETA_DATOS, usuario["nombre"])
    logging.warning("Limpieza de notas por %s: %s", usuario["nombre"], r)
    return _volver_admin(request, f"Listo: se borraron {r['borrados'].get('notas', 0)} nota(s) y {r['archivos']} archivo(s). "
                                  f"Lo aprendido por la IA se conservó. Antes se hizo un respaldo ({resumen}).")


@app.post("/admin/respaldo/verificar")
def admin_respaldo_verificar(request: Request):
    _exigir_rol(usuario_actual(request))
    ok, detalle = respaldo.verificar_ultimo_respaldo()
    return _volver_admin(request, ("Prueba de restauración correcta: " if ok else "La prueba de restauración FALLÓ: ") + detalle)


def _datos_revision_usuarios():
    """Para la revisión trimestral: cada usuario con su último ingreso y señales a revisar."""
    ingresos = db.ultimos_ingresos()
    hoy = datetime.now(timezone.utc)
    filas = []
    for u in db.listar_usuarios():
        ultimo = ingresos.get((u["usuario"] or "").lower())
        dias = None
        if ultimo:
            try:
                f = datetime.fromisoformat(ultimo)
                dias = (hoy - (f if f.tzinfo else f.replace(tzinfo=timezone.utc))).days
            except ValueError:
                pass
        alertas = []
        if u["activo"] and (dias is None or dias > 60):
            alertas.append("sin ingresar hace más de 60 días" if dias is not None else "nunca ingresó")
        if u["activo"] and u["rol"] in auth.ROLES_DE_EQUIPO and not u.get("equipo_id"):
            alertas.append("sin equipo")
        if u["activo"] and (auth.es_admin(u) or u.get("coordina_traslados") or u.get("personal")):
            alertas.append("permisos amplios: confirmar")
        filas.append(dict(u, ultimo_ingreso=ultimo, dias=dias, alertas=alertas))
    filas.sort(key=lambda x: (not x["activo"], -len(x["alertas"]), x["nombre"].lower()))
    return {"usuarios": filas, "revisiones": db.listar_revisiones_usuarios(), "vencida": db.revision_usuarios_vencida()}


@app.post("/admin/usuarios/revision")
async def admin_revision_usuarios(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    db.registrar_revision_usuarios(usuario["nombre"], form.get("observaciones"))
    return _volver_admin(request, "Revisión de usuarios registrada. La próxima vence en 90 días.")


@app.post("/admin/integridad/verificar")
def admin_verificar_integridad(request: Request):
    """Recorre todas las notas con huella y avisa cuáles tienen un archivo
    alterado o faltante."""
    _exigir_rol(usuario_actual(request))
    revisadas, problemas = 0, []
    for resumen in db.listar_notas(limite=100000):
        nota = db.obtener_nota(resumen["id"])
        resultados = integridad.verificar_nota(nota)
        if resultados:
            revisadas += 1
        for r in resultados:
            if r["estado"] != "OK":
                problemas.append(f"#{nota['id']} {r['documento']}: {r['estado'].lower().replace('_', ' ')}")
    if problemas:
        mensaje = f"Se revisaron {revisadas} nota(s). PROBLEMAS: " + "; ".join(problemas[:12]) + ("…" if len(problemas) > 12 else "")
    else:
        mensaje = f"Se revisaron {revisadas} nota(s) con huella: todos los archivos coinciden."
    return _volver_admin(request, mensaje)


@app.post("/admin/codigo/analizar")
async def admin_codigo_analizar(request: Request, background_tasks: BackgroundTasks):
    """Dispara un análisis estático del código en background."""
    _exigir_rol(usuario_actual(request))
    background_tasks.add_task(analisis_codigo.ejecutar_analisis)
    _flash(request, "✓ Análisis iniciado en segundo plano. Recargá en unos segundos.")
    return RedirectResponse("/admin/sistema/codigo", status_code=303)


@app.get("/admin/codigo/exportar/{nombre}")
async def admin_codigo_exportar(nombre: str, request: Request):
    """Descarga un reporte de análisis de código como JSON."""
    _exigir_rol(usuario_actual(request))
    if not nombre.startswith("reporte_") or not nombre.endswith(".json"):
        raise HTTPException(400)
    p = Path("datos/reportes_codigo") / nombre
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/json",
                        headers={"Content-Disposition": f"attachment; filename={nombre}"})


# =====================================================
# REPOSITORIO HISTÓRICO DE ORIGINALES
# =====================================================
def _sanitizar_nombre_archivo(nombre):
    """Nombre de archivo seguro para disco, conservando el nombre original
    para que el repositorio sea legible por un humano (no solo un uuid).
    Reemplaza también '/' y '\\' porque, a diferencia de los otros
    caracteres prohibidos en Windows, estos dos Path los interpretaría como
    separadores de carpeta y cortaría el nombre a la mitad."""
    limpio = re.sub(r'[\\/:*?"<>|]', "_", nombre).strip()
    return limpio[:150] or "archivo"


def _ruta_archivo_historico(nota_id, nombre_original, sufijo):
    """Organiza el repositorio por fecha de SUBIDA (no de firma): es el
    único momento que toda nota tiene garantizado, incluso si después se
    rechaza y nunca llega a firmarse.

    Importante: se sanitiza el nombre ANTES de tocarlo con Path/.stem. Si
    se sanitizara después, un nombre con '/' ya habría sido partido por
    Path en "carpeta" + "archivo" y se perdería todo lo anterior a la
    barra (se detectó este bug probándolo con un nombre que traía '/')."""
    ahora = datetime.now(timezone.utc)
    carpeta = CARPETA_ORIGINALES / f"{ahora.year:04d}" / f"{ahora.month:02d}"
    carpeta.mkdir(parents=True, exist_ok=True)

    nombre_sanitizado = _sanitizar_nombre_archivo(nombre_original)
    if nombre_sanitizado.lower().endswith(sufijo.lower()):
        nombre_sanitizado = nombre_sanitizado[: -len(sufijo)]

    nombre = f"{nota_id}_{nombre_sanitizado}{sufijo}"
    return carpeta / nombre


# =====================================================
# ANÁLISIS AUTOMÁTICO (reformas 11 a 14)
# =====================================================
def _reanalizar_consistencia(nota_id):
    """Chequeo de consistencia por reglas, al instante (es barato y no usa
    IA), más la parte con IA encolada para la próxima ventana nocturna.
    Nunca debe tumbar la acción que lo dispara."""
    if db.es_pdf(db.obtener_nota(nota_id)):
        return  # un PDF archivado o adjunto no se analiza
    try:
        automatico.analizar_consistencia(nota_id, usar_ia=False)
        automatico.reencolar_relacionados(nota_id)
    except Exception:
        logging.exception(f"No se pudo recalcular la consistencia de la nota {nota_id}")


def _reanalizar_bloque(nota_ids):
    for nid in nota_ids:
        _reanalizar_consistencia(nid)


# =====================================================
# PROCESAMIENTO EN SEGUNDO PLANO
# =====================================================
def _tarea_generar_propuestas(nota_id: int, ruta_original: Path, nombre_original: str):
    """Analiza el documento y genera propuestas de corrección, SIN
    aplicarlas. Pasa a EN_REVISION siempre que el documento tenga algún
    párrafo con texto — sea o no una Nota — para que el Coordinador/Director
    puedan editarlo a mano aunque no se le haya aplicado IA/corrector (eso
    solo pasa con archivos cuyo nombre contiene "Nota", ver nucleo.es_nota).
    Un documento sin texto (caso borde) pasa directo a PROCESADA, sin nada
    que revisar. Se dispara recién después de /nota/{id}/aprobar-coordinacion,
    nunca al subir."""
    try:
        # Si la corrección ya se precalculó de noche sobre este mismo texto,
        # se usa tal cual; si no (aprobada antes de la corrida nocturna, o
        # el texto cambió), se calcula ahora como siempre.
        import trazabilidad_ia
        with trazabilidad_ia.para(nota_id, "CORRECCION"):
            resultado = automatico.propuestas_para_aprobacion(nota_id, ruta_original, nombre_original)

        if resultado["propuestas"]:
            # ruta_analizada: el documento EXACTO sobre el que se calcularon
            # las propuestas; al aplicar las decisiones se parte de ese mismo
            # archivo (así las ubicaciones de los párrafos coinciden).
            db.guardar_propuestas(
                nota_id,
                tipo=resultado["tipo"],
                observaciones=resultado["observaciones"],
                propuestas=resultado["propuestas"],
                todos_los_parrafos=resultado["todos_los_parrafos"],
                es_nota=resultado["corregido"],
                desde=("PROCESANDO",),
                ruta_analizada=ruta_original,
            )
            _reanalizar_consistencia(nota_id)
        else:
            ruta_resultado = CARPETA_PROCESADOS / f"{nota_id}.docx"
            nucleo.copiar_sin_cambios(ruta_original, ruta_resultado)
            db.guardar_resultado_sin_revision(
                nota_id, tipo=resultado["tipo"], ruta_resultado=ruta_resultado,
                sha256_resultado=integridad.sha256_si_existe(ruta_resultado),
            )
    except db.ConflictoDeEstado:
        logging.warning(f"La nota {nota_id} cambió de estado mientras se generaban las propuestas; se descartan")
    except Exception as e:
        logging.exception(f"Error generando propuestas para nota {nota_id}")
        db.marcar_error(nota_id, str(e))


# ---------------------------------------------------------------- cola de corrección (una nota por vez)
# Antes cada nota aprobada lanzaba su propio hilo: con un lote grande, decenas de correcciones competían a la vez por la
# IA y LanguageTool (todo se volvía lentísimo) y, si se reiniciaba la app, los hilos se perdían y las notas quedaban
# «aplicando correcciones» para siempre. Ahora hay UNA cola, en orden de prioridad, y al arrancar (y cada 10 minutos)
# las notas que quedaron en corrección sin estar en la cola vuelven a ella.
import queue as _queue
import threading as _threading
_COLA_CORR = _queue.PriorityQueue()
_EN_COLA_CORR = {}            # nota_id -> orden de llegada
_CORRIGIENDO = {"id": None, "desde": None}
_LOCK_CORR = _threading.Lock()
_HILO_CORR = {"hilo": None}
_RANGO_PRIO = {"URGENTE": 0, "PRIORITARIA": 1, "NORMAL": 2}


def _encolar_correccion(nota_id, ruta_base, nombre, prioridad="NORMAL"):
    with _LOCK_CORR:
        if nota_id in _EN_COLA_CORR or _CORRIGIENDO["id"] == nota_id:
            return False
        orden = time.monotonic()
        _EN_COLA_CORR[nota_id] = orden
        _COLA_CORR.put((_RANGO_PRIO.get(prioridad or "NORMAL", 2), orden, nota_id, str(ruta_base), nombre))
        if _HILO_CORR["hilo"] is None or not _HILO_CORR["hilo"].is_alive():
            _HILO_CORR["hilo"] = _threading.Thread(target=_trabajar_correcciones, name="correccion", daemon=True)
            _HILO_CORR["hilo"].start()
    return True


def _trabajar_correcciones():
    while True:
        _, _, nota_id, ruta, nombre = _COLA_CORR.get()
        with _LOCK_CORR:
            _EN_COLA_CORR.pop(nota_id, None)
            _CORRIGIENDO.update(id=nota_id, desde=time.monotonic())
        try:
            nota = db.obtener_nota(nota_id)
            if nota and nota["estado"] == "PROCESANDO":  # si la pasaron a la firma sin corrección, no se hace
                _tarea_generar_propuestas(nota_id, Path(ruta), nombre)
        except Exception:
            logging.exception(f"Error en la cola de corrección (nota {nota_id})")
        finally:
            with _LOCK_CORR:
                _CORRIGIENDO.update(id=None, desde=None)
            _COLA_CORR.task_done()


def _base_para_corregir(nota):
    ruta_base = Path(nota["ruta_base"]) if nota.get("ruta_base") else Path(nota["ruta_original"])
    if ruta_base.suffix.lower() == ".doc":
        ruta_docx = Path(str(ruta_base) + "x")
        if ruta_docx.exists():
            ruta_base = ruta_docx
        else:
            ruta_base = nucleo.convertir_doc_a_docx(ruta_base)
    return ruta_base


def _recuperar_en_correccion():
    """Notas en «corrección automática» que no están en la cola (se reinició la app, se cortó la luz): vuelven a ella.
    Barrera PDF: si un PDF llegó a PROCESANDO por el bug previo, se mueve a A_CONFIRMAR en vez de re-encolarse."""
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT id FROM notas WHERE estado = 'PROCESANDO' ORDER BY id")]
    n = 0
    for f in filas:
        if f["id"] in _EN_COLA_CORR or _CORRIGIENDO["id"] == f["id"]:
            continue
        nota = db.obtener_nota(f["id"])
        if db.es_pdf(nota):
            if not nota.get("tipo") or nota.get("tipo") == "DESCONOCIDO":
                db.actualizar_tipo(nota["id"], "ADJUNTO")
            db.actualizar_estado(nota["id"], "A_CONFIRMAR",
                                 comentario="PDF en corrección al reiniciar: movido a pendiente de confirmación de Dirección",
                                 desde=("PROCESANDO",))
            continue
        try:
            n += _encolar_correccion(nota["id"], _base_para_corregir(nota), nota["nombre_original"], nota.get("prioridad"))
        except Exception as e:
            db.marcar_error(nota["id"], f"No se pudo retomar la corrección: {e}")
    if n:
        logging.info(f"{n} nota(s) que habían quedado en corrección automática vuelven a la cola.")
    return n


def _vigilar_correcciones():
    while True:
        time.sleep(600)
        try:
            _recuperar_en_correccion()
        except Exception:
            logging.exception("No se pudo revisar la cola de corrección")
        _sellar_auditoria()


def _clave_auditoria():
    """Clave del sello de la auditoría (HMAC), guardada FUERA de la base, y archivo ancla de solo agregado. La primera vez se
    crea la clave y se anota hasta qué registro se selló sin clave (SHA-256 simple, antes de esta mejora)."""
    import secrets as _secrets
    from dgpdh.seguridad import auditoria
    ruta_clave, ancla = CARPETA_DATOS / "clave_auditoria.key", CARPETA_DATOS / "auditoria_ancla.txt"
    if not ruta_clave.is_file():
        with db._conexion() as con:
            ultimo = con.execute("SELECT COALESCE(MAX(id), 0) FROM auditoria WHERE hash IS NOT NULL").fetchone()[0]
        ruta_clave.write_text(_secrets.token_hex(32), encoding="utf-8")
        _, legado_previo = auditoria.leer_ancla(ancla)
        if not legado_previo:
            with ancla.open("a", encoding="utf-8") as a:
                a.write(f"LEGADO {ultimo}\n")
    return bytes.fromhex(ruta_clave.read_text(encoding="utf-8").strip()), ancla


def verificar_auditoria():
    from dgpdh.seguridad import auditoria
    clave, ancla = _clave_auditoria()
    with db._conexion() as con:
        return auditoria.verificar(con, clave, ancla)


def _sellar_auditoria():
    """Encadena con hash los registros nuevos de la auditoría única (ADR-0008); es una escritura corta."""
    try:
        from dgpdh.seguridad import auditoria
        clave, ancla = _clave_auditoria()
        with db._conexion() as con:
            n = auditoria.sellar(con, clave, ancla)
        if n:
            logging.info(f"Auditoría: {n} registro(s) sellados")
        rutas_notas_ia.sellar_registros(clave, ancla)  # propuesta 50: historial de notas, de fichas, de seguridad y de la IA
    except Exception:
        logging.exception("No se pudo sellar la auditoría")


def posicion_correccion(nota_id):
    """(lugar en la cola, total en la cola, corrigiéndose ahora) para mostrar en la ficha."""
    with _LOCK_CORR:
        if _CORRIGIENDO["id"] == nota_id:
            return {"ahora": True, "lugar": 0, "total": len(_EN_COLA_CORR)}
        if nota_id not in _EN_COLA_CORR:
            return None
        orden = sorted(_EN_COLA_CORR.items(), key=lambda x: x[1])
        return {"ahora": False, "lugar": [i for i, _ in orden].index(nota_id) + 1, "total": len(orden)}


templates.env.globals["posicion_correccion"] = posicion_correccion


def _pasar_sin_correccion(nota, usuario):
    """La nota va a la firma tal como la subieron, sin esperar a la corrección automática (queda en el historial)."""
    ruta_base = _base_para_corregir(nota)
    destino = CARPETA_PROCESADOS / f"{nota['id']}.docx"
    nucleo.copiar_sin_cambios(ruta_base, destino)
    db.actualizar_estado(nota["id"], "PROCESADA", desde=("PROCESANDO",),
                         comentario=f"Pasada a la firma sin corrección automática por {usuario['nombre']} (no se esperó a la IA)",
                         ruta_resultado=str(destino), sha256_resultado=integridad.sha256_si_existe(destino), ruta_analizada=None, alerta=None)
    with _LOCK_CORR:
        _EN_COLA_CORR.pop(nota["id"], None)  # si estaba en la cola, el trabajador la saltea


@app.post("/nota/{nota_id}/sin-correccion")
def nota_sin_correccion(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not (auth.es_admin(usuario) or _puede_gestionar_nota(usuario, nota)):
        raise HTTPException(status_code=403, detail="No podés cambiar el circuito de esta nota.")
    if nota["estado"] != "PROCESANDO":
        return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)
    try:
        _pasar_sin_correccion(nota, usuario)
    except db.ConflictoDeEstado:
        pass
    destino = f"/nota/{nota_id}?firmar=1" if auth.es_admin(usuario) else f"/nota/{nota_id}"
    return RedirectResponse(url=destino, status_code=303)


@app.post("/notas/sin-correccion")
async def notas_sin_correccion(request: Request):
    """Varias a la vez (por ejemplo, un lote que sigue en corrección): pasan a la firma tal como vinieron."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    ids = [int(x) for x in form.getlist("nota_ids") if str(x).isdigit()]
    hechas, errores = 0, 0
    for nid in ids:
        nota = db.obtener_nota(nid)
        if not nota or nota["estado"] != "PROCESANDO":
            continue
        try:
            _pasar_sin_correccion(nota, usuario)
            hechas += 1
        except Exception:
            logging.exception(f"No se pudo pasar a la firma la nota {nid}")
            errores += 1
    request.session["flash"] = (f"{hechas} documento(s) pasaron a la firma sin corrección automática. Ya podés firmarlos (también en serie)."
                                + (f" {errores} no se pudieron pasar." if errores else ""))
    return RedirectResponse(url="/", status_code=303)


# =====================================================
# ENDPOINTS — LISTADO Y SUBIDA
# =====================================================
POR_PAGINA = 50
CLAVES_ORDEN = {
    "nombre": lambda n: (n["nombre_original"] or "").lower(),
    "tipo": lambda n: (n["tipo"] or ""),
    "etapa": lambda n: (n["etapa"]["numero"], n["estado"]) if n["etapa"] else (99, n["estado"]),
    "prioridad": lambda n: _rango(n["prioridad_efectiva"]),
    "fecha": lambda n: (n["fecha_nota"] or ""),
}


AGRUPAR_NOTAS = {"equipo": "Equipo", "asesor": "Asesor", "prioridad": "Prioridad", "fecha": "Fecha de carga"}


def _agrupar_notas(notas, criterio, usuario):
    """[{titulo, notas}] en el orden útil para cada criterio. Cada grupo se muestra como una carpeta plegable,
    como las carpetas compartidas por equipo con las que se firma hoy."""
    from collections import OrderedDict
    hoy = datetime.now().date()
    def clave(n):
        if criterio == "equipo":
            return n.get("equipo_nombre") or "(sin equipo)"
        if criterio == "asesor":
            return n.get("asesor_nombre") or "(sin asesor)"
        if criterio == "prioridad":
            return n.get("prioridad_efectiva") or "NORMAL"
        return (n.get("creado_en") or "")[:10]
    grupos = OrderedDict()
    for n in notas:
        grupos.setdefault(clave(n), []).append(n)
    if criterio == "prioridad":
        orden = sorted(grupos, key=lambda k: auth.RANGO_PRIORIDAD.get(k, 9))
    elif criterio == "fecha":
        orden = sorted(grupos, reverse=True)
    else:
        propio = (usuario.get("equipo_nombre") if criterio == "equipo" else usuario.get("nombre")) or ""
        orden = sorted(grupos, key=lambda k: (k != propio, k.lower()))
    def titulo(k):
        if criterio == "prioridad":
            return auth.ETIQUETAS_PRIORIDAD.get(k, k)
        if criterio == "fecha":
            if not k:
                return "Sin fecha"
            try:
                d = datetime.strptime(k, "%Y-%m-%d").date()
            except ValueError:
                return k
            return "Hoy" if d == hoy else ("Ayer" if (hoy - d).days == 1 else d.strftime("%d/%m/%Y"))
        return k
    return [{"clave": k, "titulo": titulo(k), "notas": grupos[k]} for k in orden]


def _sugeridas_por_equipo():
    """{equipo_id: [(unidad, motivo)]} para proponer destinos habituales en Administración → Equipos."""
    import traslados as _tr
    catalogo = db.listar_unidades()
    return {e["id"]: _tr.sugerir_unidades_equipo(e["nombre"], catalogo, ya=[u["id"] for u in db.unidades_de_equipo(e["id"])])
            for e in db.listar_equipos()}


def _filtrar_por_visibilidad(notas, usuario):
    """Qué notas puede ver cada rol en el listado principal (ver Tarea
    "Sistema de permisos, equipos y login"):
    - Dirección General (Director/Subdirector): todas, sin restricción.
    - Mesa de Entrada: solo las FIRMADA (las que les corresponde despachar).
    - Coordinador: las de su propio equipo (incluye las suyas).
    - Asesor: solo las propias en el listado principal (ver /equipo para
      las de todo su equipo)."""
    # Los borradores (sin enviar) son privados de quien los subió.
    notas = [n for n in notas if n["estado"] != "BORRADOR" or n["usuario_id"] == usuario["id"]]
    if auth.es_admin(usuario):
        return notas
    if usuario["rol"] in auth.ROLES_MESA:
        visibles = [n for n in notas if n["estado"] in db.LISTAS_PARA_REMITIR
                    or n["estado"] == "ADJUNTO"
                    or (n.get("modalidad") == "SOLO_MESA" and n["estado"] != "BORRADOR")]
    elif usuario["rol"] == auth.ROL_COORDINADOR:
        visibles = [n for n in notas if n["equipo_id"] == usuario["equipo_id"]]
    elif usuario["rol"] == auth.ROL_ASESOR:
        visibles = [n for n in notas if n["usuario_id"] == usuario["id"]]
    else:
        return []
    return _con_adjuntos_de_sus_bloques(visibles, notas)


def _con_adjuntos_de_sus_bloques(visibles, todas):
    """Mejoras nov. 2026: los PDF ADJUNTO acompañan a su bloque, así que se ven en el listado de quien ve algún
    documento de ese bloque (aunque los haya subido otra persona o Mesa los viera solo en el panel de bloques)."""
    bloques = {n["bloque_id"] for n in visibles if n.get("bloque_id")}
    if not bloques:
        return visibles
    ya = {n["id"] for n in visibles}
    extra = [n for n in todas if n["estado"] == "ADJUNTO" and n.get("bloque_id") in bloques and n["id"] not in ya]
    if not extra:
        return visibles
    orden = {n["id"]: i for i, n in enumerate(todas)}
    return sorted(visibles + extra, key=lambda n: orden.get(n["id"], 0))


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    mostrar_despachadas: bool = False,
    orden: str = "",
    direccion: str = "asc",
    f_nombre: str = "",
    f_tipo: str = "",
    f_etapa: str = "",
    f_fecha: str = "",
    f_prioridad: str = "",
    f_tema: str = "",
    f_tarea: str = "",
    pagina: int = 1,
    vista: str = "",
    ver: str = "",
    agrupar: str = "",
    f_equipo: str = "",
):
    usuario = usuario_actual(request)
    todas = db.listar_notas()
    for nota in todas:
        nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
    # Se calcula sobre TODAS las notas (antes de filtrar por visibilidad)
    # para que la prioridad de un bloque considere a todos sus documentos.
    _anotar_prioridad_efectiva(todas)

    notas = _filtrar_por_visibilidad(todas, usuario)
    # Los borradores (sin enviar) van en su propio panel, no en el listado.
    borradores = [n for n in notas if n["estado"] == "BORRADOR"]
    notas = [n for n in notas if n["estado"] != "BORRADOR"]
    for n in borradores:
        n["etapa"] = flujo.etapa_para_estado(n["estado"])
        n["puede_gestionar"] = _puede_gestionar_nota(usuario, n)

    # Panel "Pendientes de tu firma" (solo Director/Subdirector): primero lo
    # que ya se puede firmar (PROCESADA), ordenado por prioridad y
    # antigüedad; debajo, lo que todavía está en revisión de correcciones.
    pendientes_firma = []
    pendientes_revision = []
    if auth.es_admin(usuario):
        pendientes_firma = _ordenar_para_firma([n for n in todas if n["estado"] == "PROCESADA"])
        pendientes_revision = _ordenar_para_firma([n for n in todas if n["estado"] == "EN_REVISION"])
        en_correccion = _ordenar_para_firma([n for n in todas if n["estado"] == "PROCESANDO"])
    else:
        en_correccion = []

    # Bloques que Mesa de Entrada tiene que poder despachar completos.
    bloques_mesa = []
    if usuario["rol"] in auth.ROLES_MESA:
        vistos = set()
        for n in todas:
            if n.get("bloque_id") and n["bloque_id"] not in vistos and n["estado"] in db.LISTAS_PARA_REMITIR:
                vistos.add(n["bloque_id"])
        for bid in sorted(vistos):
            bloque = db.obtener_bloque(bid)
            if bloque:
                for bn in bloque["notas"]:
                    bn["etapa"] = flujo.etapa_para_estado(bn["estado"])
                bloque["completo"] = all(bn["estado"] in LISTOS_PARA_DESPACHO for bn in bloque["notas"])
                bloque["faltan"] = sum(1 for bn in bloque["notas"] if bn["estado"] not in LISTOS_PARA_DESPACHO)
                bloque["hallazgos_abiertos"] = sum(
                    1 for bn in bloque["notas"] for h in db.listar_hallazgos(bn["id"]) if h["estado"] == "ABIERTO"
                )
                bloques_mesa.append(bloque)

    # Tareas pendientes de la persona (encabezado del segmento "Documentos").
    vistas = db.ids_notas_vistas(usuario["id"])
    rechazadas_nuevas = [n for n in notas if n["estado"] == "RECHAZADA" and n["id"] not in vistas]
    tareas = []
    con_alertas = [
        n for n in borradores + notas
        if n["estado"] in ("BORRADOR", "SUBIDA") and n.get("hallazgos_abiertos")
        and (usuario["rol"] != auth.ROL_ASESOR or n["usuario_id"] == usuario["id"])
    ]
    if usuario["rol"] == auth.ROL_COORDINADOR:
        tareas.append(("aprobar", "para aprobar", sum(1 for n in notas if n["estado"] == "SUBIDA")))
        tareas.append(("alertas", "con alertas de IA", len(con_alertas)))
        tareas.append(("rechazadas", "rechazadas sin ver", len(rechazadas_nuevas)))
    elif usuario["rol"] == auth.ROL_ASESOR:
        tareas.append(("borrador", "sin enviar", len(borradores)))
        tareas.append(("alertas", "con alertas de IA", len(con_alertas)))
        tareas.append(("rechazadas", "rechazadas sin ver", len(rechazadas_nuevas)))
    elif auth.es_admin(usuario):
        tareas.append(("revisar", "para revisar", sum(1 for n in notas if n["estado"] == "EN_REVISION")))
        tareas.append(("firmar", "para firmar", sum(1 for n in notas if n["estado"] == "PROCESADA")))
        n_confirmar_pdf = sum(1 for n in notas if n["estado"] == "A_CONFIRMAR")
        if n_confirmar_pdf:
            tareas.append(("confirmar_pdf", "PDFs a confirmar", n_confirmar_pdf))
    elif usuario["rol"] in auth.ROLES_MESA:
        tareas.append(("despachar", "para despachar", sum(1 for n in notas if n["estado"] in db.LISTAS_PARA_REMITIR)))
    # Beta 01: posibles duplicados (para revisar y decidir) y descartados (recuperables).
    posibles_duplicados = [n for n in notas if n.get("duplicado_de") and n["estado"] != "DESCARTADA"
                           and (usuario["rol"] != auth.ROL_ASESOR or n["usuario_id"] == usuario["id"])]
    if posibles_duplicados:
        tareas.append(("duplicados", "posibles duplicados", len(posibles_duplicados)))
    n_descartadas = sum(1 for n in notas if n["estado"] == "DESCARTADA")
    # «Pendientes de mi acción»: solo lo que espera algo de esta persona según su perfil.
    pendientes_ids = _ids_pendientes_de_accion(usuario, notas, vistas)
    pendientes_lista = [n for n in notas if n["id"] in pendientes_ids]
    # Preferencias de vista (se recuerdan por persona): qué ver y cómo agrupar.
    try:
        pref = json.loads(usuario.get("pref_notas") or "{}") or {}
    except (TypeError, ValueError):
        pref = {}
    cambio = False
    if ver in ("pendientes", "todos"):
        cambio |= pref.get("ver") != ver; pref["ver"] = ver
    if agrupar in AGRUPAR_NOTAS or agrupar == "ninguno":
        valor = "" if agrupar == "ninguno" else agrupar
        cambio |= pref.get("agrupar") != valor; pref["agrupar"] = valor
    if cambio:
        db.guardar_pref_notas(usuario["id"], pref)
    hay_busqueda = bool(f_tarea or f_nombre.strip() or f_tipo or f_etapa or f_fecha or f_prioridad or f_tema or mostrar_despachadas)
    entrada = not (ver or vista or agrupar or hay_busqueda or pagina > 1)
    ver = "todos" if hay_busqueda else (pref.get("ver") or "pendientes")
    # Beta 01 (164): al entrar nunca se abre una pestaña vacía si hay tareas: se abre «Pendientes de mi acción»
    # (para la Dirección, arriba queda además «Pendientes de tu firma»). La pestaña elegida a mano se respeta.
    if entrada and pendientes_lista:
        ver = "pendientes"
    agrupar = pref.get("agrupar") or ""
    # «Mis documentos» / «Documentos del equipo» (asesores y coordinadores). El equipo se ve en
    # solo lectura: las acciones siguen dependiendo de _puede_gestionar_nota.
    vista_opciones = None
    if usuario["rol"] in auth.ROLES_DE_EQUIPO and usuario.get("equipo_id"):
        del_equipo = [n for n in todas if n["equipo_id"] == usuario["equipo_id"] and n["estado"] != "BORRADOR"]
        mias = _con_adjuntos_de_sus_bloques([n for n in del_equipo if n["usuario_id"] == usuario["id"]], del_equipo)
        pendiente = lambda n: n["estado"] != "DESPACHADA"
        vista_opciones = {"mias": sum(1 for n in mias if pendiente(n)), "equipo": sum(1 for n in del_equipo if pendiente(n))}
        if vista not in ("mias", "equipo") or f_tarea:
            # por defecto (y al tocar una tarea): el asesor ve lo suyo; el coordinador, el equipo
            vista = "mias" if usuario["rol"] == auth.ROL_ASESOR else "equipo"
        notas = mias if vista == "mias" else del_equipo
    else:
        vista = ""
    # Contador de la pestaña «Todos los documentos» (lo que se ve sin filtros: sin despachados, archivados ni descartados).
    n_todos = sum(1 for n in notas if n["estado"] not in ("DESPACHADA", "DESCARTADA"))
    if ver == "pendientes":
        notas = pendientes_lista
    _ESTADO_TAREA = {"aprobar": ("SUBIDA",), "revisar": ("EN_REVISION",), "firmar": ("PROCESADA",), "despachar": db.LISTAS_PARA_REMITIR, "confirmar_pdf": ("A_CONFIRMAR",)}
    if f_tarea == "rechazadas":
        notas = rechazadas_nuevas
    elif f_tarea == "alertas":
        notas = [n for n in con_alertas if n["estado"] != "BORRADOR"]
    elif f_tarea in _ESTADO_TAREA:
        notas = [n for n in notas if n["estado"] in _ESTADO_TAREA[f_tarea]]
    elif f_tarea == "duplicados":
        notas = posibles_duplicados
    elif f_tarea == "descartadas":
        notas = [n for n in notas if n["estado"] == "DESCARTADA"]
    if f_tarea != "descartadas":
        notas = [n for n in notas if n["estado"] != "DESCARTADA"]  # los descartados solo en su filtro

    cantidad_despachadas = sum(1 for n in notas if n["estado"] == "DESPACHADA")
    if not mostrar_despachadas and not f_tarea:
        notas = [n for n in notas if n["estado"] != "DESPACHADA"]

    # Filtros reales (no solo orden): nombre por coincidencia parcial, tipo,
    # etapa y prioridad por igualdad, fecha de la nota por igualdad exacta
    # (AAAA-MM-DD).
    if f_nombre.strip():
        buscado = f_nombre.strip().lower()
        notas = [n for n in notas if buscado in (n["nombre_original"] or "").lower()]
    if f_tipo:
        notas = [n for n in notas if (n["tipo"] or "") == f_tipo]
    if f_etapa:
        notas = [n for n in notas if n["etapa"] and n["etapa"]["clave"] == f_etapa]
    if f_fecha:
        notas = [n for n in notas if (n["fecha_nota"] or "") == f_fecha]
    if f_prioridad:
        notas = [n for n in notas if n["prioridad_efectiva"] == f_prioridad]
    if f_tema:
        notas = [n for n in notas if (n.get("tema") or "") == f_tema]
    # Mejoras nov. 2026: al agrupar por equipo se puede quedar con un solo equipo.
    f_equipo = f_equipo.strip() if agrupar == "equipo" else ""
    equipos_opciones = []
    if agrupar == "equipo":
        ids_con_notas = {str(n.get("equipo_id")) for n in notas if n.get("equipo_id") is not None}
        equipos_opciones = [e for e in db.listar_equipos() if str(e["id"]) in ids_con_notas or str(e["id"]) == f_equipo]
    if f_equipo:
        notas = [n for n in notas if str(n.get("equipo_id") or "") == f_equipo]

    if orden in CLAVES_ORDEN:
        notas.sort(key=CLAVES_ORDEN[orden], reverse=(direccion == "desc"))
    else:
        # Orden por defecto: URGENTE y Prioritaria arriba; dentro de cada
        # prioridad se mantiene lo más reciente primero (el orden de la base).
        notas.sort(key=lambda n: _rango(n["prioridad_efectiva"]))

    # Agrupado a gusto (equipo, asesor, prioridad o fecha): se muestran todos, cada grupo como una carpeta.
    grupos = None
    if agrupar:
        grupos = _agrupar_notas(notas[:500], agrupar, usuario)
    # Paginado: con muchas notas la pantalla no se vuelve pesada.
    total = len(notas)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    notas = notas[(pagina - 1) * POR_PAGINA: pagina * POR_PAGINA]

    tipos_disponibles = sorted({n["tipo"] for n in todas if n["tipo"]})
    temas_disponibles = sorted({n["tema"] for n in todas if n.get("tema")})

    for n in notas:
        n["puede_gestionar"] = _puede_gestionar_nota(usuario, n) and n["estado"] in ESTADOS_ACTIVOS

    # Para el selector "agregar a un bloque existente" al armar bloques
    # desde el listado no hace falta nada más: se arma con las casillas.
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "usuario": usuario,
            "notas": notas,
            "etapas": flujo.ETAPAS,
            "mostrar_despachadas": mostrar_despachadas,
            "cantidad_despachadas": cantidad_despachadas,
            "orden": orden,
            "direccion": direccion,
            "f_nombre": f_nombre,
            "f_tipo": f_tipo,
            "f_etapa": f_etapa,
            "f_fecha": f_fecha,
            "f_prioridad": f_prioridad,
            "f_tema": f_tema,
            "f_tarea": f_tarea,
            "vista": vista,
            "vista_opciones": vista_opciones,
            "ver": ver,
            "agrupar": agrupar,
            "f_equipo": f_equipo,
            "equipos": equipos_opciones,
            "filtros_guardados": pref.get("filtros") or [],
            "grupos": grupos,
            "AGRUPAR_NOTAS": AGRUPAR_NOTAS,
            "n_pendientes": len(pendientes_lista),
            "n_todos": n_todos,
            "n_descartadas": n_descartadas,
            "tareas": tareas,
            "borradores": borradores,
            "prioridad_opciones": [(p, auth.ETIQUETAS_PRIORIDAD[p]) for p in auth.PRIORIDADES],
            "tipo_opciones": [(t, t) for t in tipos_disponibles],
            "etapa_opciones": [(e["clave"], e["nombre"]) for e in flujo.ETAPAS],
            "tema_opciones": [(t, analisis.etiqueta_tema(t)) for t in temas_disponibles],
            "volver": request.url.path + ("?" + request.url.query if request.url.query else ""),
            "pagina": pagina,
            "paginas": paginas,
            "total": total,
            "temas_disponibles": temas_disponibles,
            "tipos_disponibles": tipos_disponibles,
            "pendientes_firma": pendientes_firma,
            "pendientes_revision": pendientes_revision,
            "en_correccion": en_correccion,
            "bloques_mesa": bloques_mesa,
            "puede_armar_bloques": usuario["rol"] in (auth.ROL_ASESOR, auth.ROL_COORDINADOR) or auth.es_admin(usuario),
            "puede_seleccionar_mesa": usuario["rol"] in auth.ROLES_MESA,
            "expte_sugerencias": request.session.pop("expte_sugerencias", []),
        },
    )


@app.get("/equipo", response_class=HTMLResponse)
def ver_notas_equipo(request: Request):
    """Panel de las notas de todo el equipo del Asesor/Coordinador logueado
    (no solo las propias) — ver Tarea de permisos. Dirección General y
    Mesa de Entrada no tienen "equipo" propio, así que no tiene sentido
    para ellos; se los manda al listado principal."""
    usuario = usuario_actual(request)
    if usuario["rol"] not in auth.ROLES_DE_EQUIPO:
        return RedirectResponse(url="/")

    todas = db.listar_notas()
    _anotar_prioridad_efectiva(todas)
    notas = [n for n in todas if n["equipo_id"] == usuario["equipo_id"]
             and (n["estado"] != "BORRADOR" or n["usuario_id"] == usuario["id"])]
    for nota in notas:
        nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
    notas = [n for n in notas if n["estado"] != "DESPACHADA"]
    notas.sort(key=lambda n: _rango(n["prioridad_efectiva"]))

    return templates.TemplateResponse(
        "equipo.html",
        {"request": request, "usuario": usuario, "notas": notas},
    )


class _ArchivoRechazado(Exception):
    pass


_FIRMAS_ARCHIVO = {".docx": b"PK\x03\x04", ".doc": b"\xd0\xcf\x11\xe0", ".pdf": b"%PDF"}


def _guardar_subida(archivo, usuario, equipo_id, fecha, prioridad, version_de=None, borrador=None, solo_mesa=False):
    """Valida y archiva un archivo subido. Devuelve (nota_id, None) o
    (None, motivo). Se valida el CONTENIDO (no solo la extensión) y el tamaño,
    para no guardar basura ni archivos disfrazados."""
    nombre = Path(archivo.filename or "").name
    sufijo = Path(nombre).suffix.lower()
    if sufijo not in EXTENSIONES_VALIDAS:
        return None, "solo se aceptan archivos .doc, .docx y .pdf"
    if solo_mesa and sufijo == ".doc":
        return None, "en «Solo Mesa de Entradas» se carga el .docx (el número se inserta en el documento); guardalo como .docx"

    # Se guarda primero bajo un nombre temporal para poder crear el registro
    # en la base y obtener el id, con el que recién se arma el nombre
    # definitivo del repositorio histórico.
    temporal = CARPETA_ORIGINALES / f"{uuid.uuid4().hex}{sufijo}"
    try:
        tamano = 0
        with temporal.open("wb") as destino:
            while True:
                bloque = archivo.file.read(1024 * 1024)
                if not bloque:
                    break
                tamano += len(bloque)
                if tamano > MAX_BYTES_SUBIDA:
                    raise _ArchivoRechazado(f"supera el máximo de {MAX_BYTES_SUBIDA // (1024 * 1024)} MB")
                destino.write(bloque)
        if tamano == 0:
            raise _ArchivoRechazado("está vacío")
        with temporal.open("rb") as f:
            if f.read(4) != _FIRMAS_ARCHIVO[sufijo]:
                raise _ArchivoRechazado("el contenido no coincide con la extensión (no es un Word o PDF válido)")
        if sufijo == ".docx":
            try:
                with zipfile.ZipFile(temporal) as z:
                    if "word/document.xml" not in z.namelist():
                        raise _ArchivoRechazado("el .docx está dañado o no es un documento de Word")
            except zipfile.BadZipFile:
                raise _ArchivoRechazado("el .docx está dañado o no es un documento de Word")
    except _ArchivoRechazado as e:
        temporal.unlink(missing_ok=True)
        return None, str(e)
    except Exception:
        temporal.unlink(missing_ok=True)
        raise

    # Lo que sube un Asesor queda "sin enviar" hasta que él lo manda a
    # Coordinación (puede armar bloques y cambiar prioridades antes).
    if solo_mesa:
        borrador = True  # no pasa por Coordinación: no se le avisa (enseguida queda «para remitir»)
    elif borrador is None or sufijo == ".pdf":
        # Asesor y Coordinación preparan antes de mandar a corrección (editar el texto, armar bloques, prioridades).
        # Un PDF nunca entra al circuito de corrección: se archiva (o acompaña un bloque).
        borrador = usuario.get("rol") in ("ASESOR", "COORDINADOR") or sufijo == ".pdf"  # = auth.ROLES_DE_EQUIPO
    nota_id = db.crear_nota(
        nombre, equipo_id, temporal, fecha_nota=fecha, usuario_id=usuario["id"],
        prioridad=prioridad, version_de=version_de, borrador=borrador,
    )
    ruta_definitiva = _ruta_archivo_historico(nota_id, nombre, sufijo)
    temporal.rename(ruta_definitiva)
    db.actualizar_ruta_original(nota_id, ruta_definitiva)
    # Huella del original: cadena de custodia (ver integridad.py).
    try:
        db.fijar_hash(nota_id, "sha256_original", integridad.sha256_archivo(ruta_definitiva))
    except OSError:
        logging.exception(f"No se pudo calcular la huella del original de la nota {nota_id}")

    if sufijo == ".pdf":
        # Nuevo circuito PDF: SUBIDA → aprobación Coordinación → A_CONFIRMAR → confirmación Dirección → ADJUNTO
        db.actualizar_tipo(nota_id, "ADJUNTO")
        db.actualizar_estado(nota_id, "SUBIDA", comentario=f"PDF cargado por {usuario['nombre']}: pendiente de aprobación de Coordinación",
                             desde=("BORRADOR",))
        try:
            from pypdf import PdfReader
            texto_pdf = "\n".join((p.extract_text() or "") for p in PdfReader(str(ruta_definitiva)).pages[:200])
            if texto_pdf.strip():
                db.indexar_texto(nota_id, texto_pdf)
                agentes.registrar_entidades(nota_id, texto_pdf)
                try:
                    import coincidencias as _coinc
                    _coinc.buscar_y_guardar(nota_id)
                except Exception as _ce:
                    logging.warning(f"coincidencias PDF {nota_id}: {_ce}")
        except Exception:
            logging.info(f"El PDF {nota_id} no tiene texto extraíble")
        return nota_id, None

    if solo_mesa:
        # «Solo Mesa de Entradas»: sin control, coordinación, firma ni IA. Mesa le pone número y lo remite.
        db.marcar_solo_mesa(nota_id, usuario["nombre"])
        return nota_id, None

    # Tema y prioridad sugerida por palabras clave: instantáneo y sin IA
    # (los .doc viejos esperan a la corrida nocturna, porque convertirlos
    # necesita Word). El resto del análisis (IA) queda en la cola, que
    # corre solo fuera del horario de oficina.
    try:
        if sufijo == ".docx":
            automatico.clasificar_rapido(nota_id)
        automatico.encolar_analisis(nota_id)
    except Exception:
        logging.exception(f"No se pudo preparar el análisis automático de la nota {nota_id}")
    return nota_id, None


def _fecha_valida(valor):
    try:
        datetime.strptime(valor or "", "%Y-%m-%d")
        return valor
    except ValueError:
        return None


@app.post("/subir")
async def subir(
    request: Request,
    archivos: list[UploadFile] = File(...),
    prioridad: str = Form("NORMAL"),
    agrupar: str = Form(None),
    nombre_bloque: str = Form(""),
    background_tasks: BackgroundTasks = None,
    modalidad: str = Form(""),
):
    """Solo guarda y archiva. NO dispara la corrección: eso pasa recién
    cuando Coordinación aprueba (ver /nota/{id}/aprobar-coordinacion).
    La fecha de la nota ya no se pide acá: se define al firmar.
    `agrupar` arma un bloque con los archivos subidos juntos (si son 2 o
    más). `prioridad`
    (URGENTE / PRIORITARIA / NORMAL) es opcional y también vale para toda la
    tanda; si no viene o no es válida, queda NORMAL.

    El equipo y el asesor ya NO se piden a mano: se toman de la cuenta
    logueada (ver Tarea de permisos/login). Si quien sube no pertenece a
    un Equipo (Dirección General, Mesa de Entrada), se usa el equipo por
    defecto."""
    usuario = usuario_actual(request)
    equipo_id = usuario["equipo_id"] or db.obtener_equipo_por_defecto_id()
    fecha = None
    ids_creados, omitidas, duplicadas = [], [], []
    solo_mesa = modalidad == "SOLO_MESA"
    if solo_mesa:
        _exigir_rol(usuario, *auth.ROLES_MESA)  # el área; el Director General, excepcionalmente (queda registrado)

    for archivo in archivos:
        nota_id, motivo = _guardar_subida(archivo, usuario, equipo_id, fecha, prioridad, solo_mesa=solo_mesa)
        if nota_id:
            ids_creados.append(nota_id)
            try:
                db.guardar_hash_texto(nota_id, nucleo.hash_texto_documento(db.obtener_nota(nota_id)["ruta_original"]))
            except Exception:
                pass  # .doc sin convertir u otro caso: queda la comparación por huella del archivo
            dup = [d for d in db.buscar_duplicados(nota_id) if d["estado"] != "DESCARTADA"]
            if dup:
                db.registrar_evento(nota_id, "Posible duplicado de " + ", ".join(f"#{d['id']}" for d in dup))
                db.marcar_duplicado(nota_id, dup[0]["id"])
                duplicadas.append(f"«{archivo.filename}» se parece a «{dup[0]['nombre_original']}» (#{dup[0]['id']})")
        else:
            logging.warning(f"Archivo omitido ({archivo.filename}): {motivo}")
            omitidas.append({"archivo": archivo.filename, "motivo": motivo})

    if duplicadas:
        _flash(request, "⚠ Posible duplicado: " + "; ".join(duplicadas[:3]) + ". Revisá antes de enviar.")
    bloque_creado = None
    if agrupar and len(ids_creados) >= 2:
        nombre_bloque = (nombre_bloque or "").strip()[:120]
        bloque_creado = db.crear_bloque(nombre_bloque, equipo_id, usuario["id"], ids_creados)
        _reanalizar_bloque(ids_creados)
    # Ola 6 (113): lo que sube la Coordinación del equipo o la Dirección no espera su propia aprobación.
    for nid in ids_creados:
        _coordinacion_implicita(usuario, nid, background_tasks)

    # Cambio 4 (nov. 2026): para PDFs y SOLO_MESA, sugerir incorporar el nro. de expediente al nombre.
    if ids_creados:
        sugerencias = []
        for nid in ids_creados:
            n = db.obtener_nota(nid)
            sufijo = Path(n["nombre_original"]).suffix.lower()
            if sufijo not in (".pdf", ".docx"):
                continue
            texto = ""
            try:
                if sufijo == ".pdf":
                    from pypdf import PdfReader
                    texto = "\n".join((p.extract_text() or "") for p in PdfReader(str(n["ruta_original"])).pages[:5])
                else:
                    from docx import Document as _DocX
                    texto = "\n".join(p.text for p in _DocX(str(n["ruta_original"])).paragraphs)
            except Exception:
                pass
            if texto.strip():
                expte = despacho.proponer_expediente(texto, n["nombre_original"])
                if expte:
                    stem = Path(n["nombre_original"]).stem
                    nombre_sugerido = f"{stem} - Expte. {expte}{sufijo}"
                    sugerencias.append({"nota_id": nid, "nombre_original": n["nombre_original"], "nombre_sugerido": nombre_sugerido})
        if sugerencias:
            request.session["expte_sugerencias"] = sugerencias

    if request.headers.get("hx-request"):
        if not omitidas:
            if ids_creados and not duplicadas:
                _flash(request, f"Se cargaron {len(ids_creados)} documento(s) correctamente."
                       + (" Quedaron «para remitir» (Solo Mesa de Entradas)." if solo_mesa else ""))
            return _recargar_pagina()
        lineas = [f"Se subieron {len(ids_creados)} documento(s)."] if ids_creados else []
        lineas += [f"No se subió «{o['archivo']}»: {o['motivo']}." for o in omitidas]
        cuerpo = "<br>".join(html_lib.escape(l) for l in lineas)
        if ids_creados:
            cuerpo += ' <a href="/">Actualizar listado</a>'
        return HTMLResponse(f'<div class="aviso-error" role="alert">{cuerpo}</div>')
    return {"notas_creadas": ids_creados, "omitidas": omitidas, "bloque_id": bloque_creado}


@app.post("/subir/verificar")
async def verificar_subida(request: Request):
    """Beta 01: antes de terminar la carga, ¿alguno de estos archivos ya está cargado? (no guarda nada)."""
    import hashlib
    import tempfile as _tmp
    usuario_actual(request)
    form = await request.form()
    salida = []
    for archivo in form.getlist("archivos"):
        if not getattr(archivo, "filename", ""):
            continue
        datos = await archivo.read()
        sha = hashlib.sha256(datos).hexdigest()
        h_texto = None
        if Path(archivo.filename).suffix.lower() == ".docx" and datos[:4] == b"PK\x03\x04":
            try:
                with _tmp.NamedTemporaryFile(suffix=".docx", delete=False) as t:
                    t.write(datos)
                h_texto = nucleo.hash_texto_documento(t.name)
                Path(t.name).unlink(missing_ok=True)
            except Exception:
                h_texto = None
        visibles = [d for d in db.duplicados_por_huella(sha, h_texto) if _puede_ver_nota(usuario_actual(request), db.obtener_nota(d["id"]))]
        if visibles:
            salida.append({"archivo": archivo.filename, "existentes": [
                {"id": d["id"], "nombre": d["nombre_original"], "usuario": d["usuario"] or "—", "fecha": fechas.a_hora_local(d["creado_en"]),
                 "estado": d["estado"], "mismo_archivo": bool(d["mismo_archivo"])} for d in visibles]})
    return JSONResponse({"duplicados": salida})


@app.get("/nota/{nota_id}/duplicado", response_class=HTMLResponse)
def comparar_duplicado(request: Request, nota_id: int, otra: int = 0):
    """Beta 01: los dos documentos lado a lado para decidir a mano si es un duplicado."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None or not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    original = db.obtener_nota(otra or nota.get("duplicado_de") or 0)
    if original is None or not _puede_ver_nota(usuario, original):
        raise HTTPException(status_code=404, detail="No hay otro documento para comparar")

    def parrafos(n):
        if db.es_pdf(n):
            return [{"texto": l} for l in (db.texto_indexado(n["id"]) or "").splitlines() if l.strip()]
        try:
            return nucleo.parrafos_de_documento(automatico.ruta_texto_actual(n))
        except Exception:
            return []
    filas = analisis.comparar_versiones(parrafos(original), parrafos(nota))
    resumen = {t: sum(1 for f in filas if f["tipo"] == t) for t in ("cambiado", "agregado", "quitado")}
    db.registrar_acceso(nota_id, "COMPARAR_DUPLICADO", f"con #{original['id']}")
    return templates.TemplateResponse("duplicado.html", {
        "request": request, "usuario": usuario, "nota": nota, "original": original, "filas": filas, "resumen": resumen,
        "mismo_archivo": bool(nota.get("sha256_original") and nota.get("sha256_original") == original.get("sha256_original")),
        "puede_nota": _puede_gestionar_nota(usuario, nota), "puede_original": _puede_gestionar_nota(usuario, original),
        "descartables": db.ESTADOS_DESCARTABLES})


@app.post("/nota/{nota_id}/no-duplicado")
def marcar_no_duplicado(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No podés modificar este documento")
    db.no_es_duplicado(nota_id, usuario["nombre"])
    _flash(request, "Marcado: no es un duplicado.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


MOTIVOS_DESCARTE = ("cargado por error", "duplicado", "ya no corresponde")


@app.post("/nota/{nota_id}/expte-nombre")
async def aplicar_expte_nombre(request: Request, nota_id: int):
    """Cambio 4 nov. 2026: incorpora el expediente sugerido al nombre_original de la nota."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(404)
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(403)
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    if nombre:
        sufijo = Path(nota["nombre_original"]).suffix.lower()
        if not nombre.lower().endswith(sufijo):
            nombre = nombre + sufijo
        nombre = nombre[:200]
        with db._conexion() as con:
            con.execute("UPDATE notas SET nombre_original = ? WHERE id = ?", (nombre, nota_id))
        db.registrar_evento(nota_id, f"Nombre actualizado a «{nombre}» (expediente incorporado al nombre)")
    return _recargar_pagina()


@app.post("/nota/{nota_id}/vincular-expte-adjunto")
async def vincular_expte_adjunto(request: Request, nota_id: int):
    """Cambio 6 nov. 2026: vincula un número de expediente a un PDF adjunto (para poder incorporarlo al legajo)."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404)
    if not db.es_pdf(nota) or nota["estado"] not in ("ADJUNTO", "ARCHIVADA"):
        raise HTTPException(status_code=409, detail="Solo se puede vincular un expediente a un PDF adjunto")
    form = await request.form()
    nro_expte = (form.get("nro_expte") or "").strip()[:50]
    if not nro_expte:
        _flash(request, "⚠ Ingresá un número de expediente.")
        return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)
    with db._conexion() as con:
        con.execute("UPDATE notas SET nro_expte = ? WHERE id = ?", (nro_expte, nota_id))
    db.registrar_evento(nota_id, f"Expediente vinculado: {nro_expte}")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/incorporar-legajo")
async def incorporar_al_legajo(request: Request, nota_id: int):
    """Cambio 6 nov. 2026: incorpora un ADJUNTO o SOLO_MESA al legajo de su expediente (→ DESPACHADA)."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404)
    if nota["estado"] == "DESPACHADA":
        raise HTTPException(status_code=409, detail="Ya está despachado")
    if not nota.get("nro_expte"):
        raise HTTPException(status_code=409, detail="El documento no tiene expediente vinculado")
    if not (db.es_pdf(nota) or nota.get("modalidad") == "SOLO_MESA"):
        raise HTTPException(status_code=409, detail="Solo para PDFs adjuntos o documentos de Mesa de Entradas")
    estados_desde = ("ADJUNTO",) if nota["estado"] == "ADJUNTO" else (nota["estado"],)
    db.actualizar_estado(nota_id, "DESPACHADA",
                         comentario=f"Incorporado al legajo Expte. {nota['nro_expte']} por {usuario['nombre']}",
                         desde=estados_desde)
    db.registrar_evento(nota_id, f"Incorporado al legajo: Expte. {nota['nro_expte']}")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/marcar-procesado-externamente")
async def marcar_procesado_externamente(request: Request, nota_id: int):
    """Mesa marca un PDF ARCHIVADA (sistema anterior) como ya procesado externamente → DESPACHADA.
    No requiere nro_expte. Registra en historial y descargas_mesa para trazabilidad.
    # FUTURO: además de ROLES_MESA, usuarios con permiso "firma_archivo" podrán acceder.
    """
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404)
    if nota["estado"] != "ARCHIVADA" or nota.get("tipo"):
        raise HTTPException(status_code=409, detail="Solo para PDFs archivados del sistema anterior (sin tipo asignado)")
    db.actualizar_estado(
        nota_id, "DESPACHADA",
        comentario=f"Marcado como procesado externamente por {usuario['nombre']} (sin descarga desde el sistema)",
        desde=("ARCHIVADA",),
    )
    import uuid as _uuid
    import reporte_descarga
    try:
        reporte_descarga.generar_excel_descarga(
            archivos=[{
                "nota_id": nota_id,
                "bloque_id": str(nota.get("bloque_id")) if nota.get("bloque_id") else None,
                "archivo_nombre": nota.get("nombre_original") or str(nota_id),
                "lpu": nota.get("nro_nota") or nota.get("lpu"),
                "dni": nota.get("dni"),
                "expediente_ppn": nota.get("nro_expte") or nota.get("nro_expediente_ppn"),
                "tipo_archivo": "ADJUNTO",
            }],
            agente={"id": usuario["id"], "nombre": usuario["nombre"] + " [procesado sin descarga]"},
        )
    except Exception:
        pass  # El registro de trazabilidad es secundario; el cambio de estado ya se hizo
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/descartar")
async def descartar_documento(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No podés descartar este documento")
    try:
        motivo = (await request.form()).get("motivo") or "duplicado"
    except Exception:
        motivo = "duplicado"
    motivo = motivo if motivo in MOTIVOS_DESCARTE else "duplicado"
    try:
        db.descartar_nota(nota_id, usuario["nombre"], motivo)
    except db.ConflictoDeEstado as e:
        raise HTTPException(status_code=409, detail=str(e))
    _flash(request, f"«{nota['nombre_original']}» descartado. Se conserva y se puede recuperar desde «Descartados».")
    return RedirectResponse(url="/", status_code=303)


@app.post("/nota/{nota_id}/recuperar")
def recuperar_documento(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No podés recuperar este documento")
    try:
        db.recuperar_nota(nota_id, usuario["nombre"])
    except db.ConflictoDeEstado as e:
        raise HTTPException(status_code=409, detail=str(e))
    _flash(request, "Documento recuperado.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/nueva-version")
async def subir_nueva_version(request: Request, nota_id: int, archivo: UploadFile = File(...)):
    """Cuando el asesor corrige el documento y lo vuelve a subir: queda como
    una nota nueva ENLAZADA a la anterior (cadena de versiones), con su propio
    circuito. La anterior no se toca."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para subir una versión de esta nota")
    nuevo_id, motivo = _guardar_subida(
        archivo, usuario, nota["equipo_id"], None, nota["prioridad"],
        version_de=nota_id,
    )
    if not nuevo_id:
        raise HTTPException(status_code=422, detail=f"No se pudo subir la versión: {motivo}.")
    db.registrar_evento(nota_id, f"Se subió una versión nueva de este documento (nota #{nuevo_id})")
    _flash(request, "Nueva versión cargada correctamente.")
    return RedirectResponse(url=f"/nota/{nuevo_id}", status_code=303)


@app.get("/nota/{nota_id}/comparar/{otra_id}", response_class=HTMLResponse)
def comparar_versiones_nota(request: Request, nota_id: int, otra_id: int):
    """Qué cambió entre dos versiones (párrafo a párrafo)."""
    usuario = usuario_actual(request)
    a, b = db.obtener_nota(nota_id), db.obtener_nota(otra_id)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not (_puede_ver_nota(usuario, a) and _puede_ver_nota(usuario, b)):
        raise HTTPException(status_code=403, detail="No tenés permiso para ver estas notas")
    try:
        filas = analisis.comparar_versiones(
            nucleo.parrafos_de_documento(automatico.ruta_texto_actual(a)),
            nucleo.parrafos_de_documento(automatico.ruta_texto_actual(b)),
        )
    except Exception as e:
        logging.exception("No se pudo comparar las versiones")
        raise HTTPException(status_code=409, detail=f"No se pudieron comparar los documentos: {e}")
    resumen = {t: sum(1 for f in filas if f["tipo"] == t) for t in ("cambiado", "agregado", "quitado")}
    db.registrar_acceso(nota_id, "COMPARAR", f"con #{otra_id}")
    return templates.TemplateResponse(
        "comparar.html",
        {"request": request, "a": a, "b": b, "filas": filas, "resumen": resumen},
    )


def _fragmento_html(fragmento):
    """El fragmento de búsqueda trae lo encontrado entre \x02 y \x03; se
    escapa todo y recién después se marca."""
    seguro = str(escape(fragmento or ""))
    return Markup(seguro.replace("\x02", "<mark>").replace("\x03", "</mark>"))


@app.get("/buscar", response_class=HTMLResponse)
def buscar(request: Request, q: str = ""):
    """Busca por nombre de archivo y por texto dentro de los documentos, solo
    entre las notas que el usuario puede ver."""
    usuario = usuario_actual(request)
    q = (q or "").strip()[:200]
    resultados = []
    if q:
        notas = {n["id"]: n for n in db.listar_notas(limite=100000)}
        vistos = set()
        qn = q.upper().replace(" ", "")
        if len(qn) >= 4 and ("-" in qn or qn.startswith("INF")):  # número de protocolo (INF-DGPDH-2026-0001) o parte
            for n in notas.values():
                if n.get("protocolo_numero") and qn in n["protocolo_numero"].upper() and _puede_ver_nota(usuario, n):
                    vistos.add(n["id"])
                    resultados.append({"nota": n, "fragmento": None})
        for nota_id, fragmento in db.buscar_texto(q):
            n = notas.get(nota_id)
            if n and _puede_ver_nota(usuario, n):
                vistos.add(nota_id)
                resultados.append({"nota": n, "fragmento": _fragmento_html(fragmento)})
        terminos = db._terminos_busqueda(q)
        for n in notas.values():
            nombre = (n["nombre_original"] or "").lower()
            if n["id"] not in vistos and terminos and all(t in nombre for t in terminos) and _puede_ver_nota(usuario, n):
                resultados.append({"nota": n, "fragmento": None})
        resultados = resultados[:100]
        for r in resultados:
            r["nota"]["etapa"] = flujo.etapa_para_estado(r["nota"]["estado"])
    return templates.TemplateResponse(
        "buscar.html", {"request": request, "q": q, "resultados": resultados}
    )


# =====================================================
# ENDPOINTS — NOTIFICACIONES INTERNAS
# =====================================================
@app.get("/notificaciones", response_class=HTMLResponse)
def ver_notificaciones(request: Request, ver: str = ""):
    """Ola 6 (112): abre en «Nuevos» si hay avisos sin leer; pestañas Nuevos / Leídos / Todos."""
    usuario = usuario_actual(request)
    todas = db.listar_notificaciones(usuario["id"])
    nuevos = sum(1 for n in todas if not n["leida"])
    if ver not in ("nuevos", "leidos", "todos"):
        ver = "nuevos" if nuevos else "todos"
    if ver == "nuevos":
        lista = [n for n in todas if not n["leida"]]
    elif ver == "leidos":
        lista = [n for n in todas if n["leida"]]
    else:
        lista = todas
    return templates.TemplateResponse(
        "notificaciones.html",
        {"request": request, "notificaciones": lista, "ver": ver, "total": len(todas), "nuevos": nuevos, "leidos": len(todas) - nuevos},
    )


@app.post("/notificaciones/{notificacion_id}/no-leido")
def marcar_aviso_no_leido(request: Request, notificacion_id: int):
    usuario = usuario_actual(request)
    n = db.obtener_notificacion(notificacion_id)
    if n is None or n["usuario_id"] != usuario["id"]:
        raise HTTPException(status_code=404, detail="Aviso no encontrado")
    db.marcar_notificacion_no_leida(notificacion_id)
    return RedirectResponse(url="/notificaciones?ver=nuevos", status_code=303)


@app.post("/notificaciones/leer-todas")
def leer_todas_notificaciones(request: Request):
    db.marcar_todas_leidas(usuario_actual(request)["id"])
    return RedirectResponse(url="/notificaciones?ver=todos", status_code=303)


@app.post("/notificaciones/{notificacion_id}/abrir")
def abrir_notificacion(request: Request, notificacion_id: int):
    usuario = usuario_actual(request)
    n = db.obtener_notificacion(notificacion_id)
    if n is None or n["usuario_id"] != usuario["id"]:
        raise HTTPException(status_code=404, detail="Aviso no encontrado")
    db.marcar_notificacion_leida(notificacion_id)
    destino = n.get("url") or "/notificaciones"
    if not destino.startswith("/") or destino.startswith("//"):
        destino = "/notificaciones"
    return RedirectResponse(url=destino, status_code=303)


# =====================================================
# ENDPOINTS — APROBACIÓN / RECHAZO DE COORDINACIÓN
# =====================================================
def _arrancar_correccion(nota, comentario, background_tasks=None, desde=("SUBIDA",)):
    """SUBIDA -> PROCESANDO y lanza la corrección automática.

    El estado se cambia ACÁ MISMO, antes de lanzar la tarea de fondo: así el
    timestamp del historial (usado en /reportes para el "tiempo de aprobación
    de coordinación") es el del click real. Si Coordinación editó el texto
    antes (ver /editar-base), se corrige a partir de esa copia de trabajo.

    Barrera PDF: los PDFs nunca pasan por corrección automática. Van a
    A_CONFIRMAR y esperan confirmación de Dirección antes de llegar a Mesa."""
    nota_id = nota["id"]
    if db.es_pdf(nota):
        if not nota.get("tipo") or nota.get("tipo") == "DESCONOCIDO":
            db.actualizar_tipo(nota_id, "ADJUNTO")
        db.actualizar_estado(nota_id, "A_CONFIRMAR",
                             comentario=comentario + " (PDF: pendiente de confirmación de Dirección)",
                             desde=desde)
        return True
    db.actualizar_estado(nota_id, "PROCESANDO", comentario=comentario, desde=desde)
    try:
        ruta_base = _base_para_corregir(nota)
    except Exception as e:
        db.marcar_error(nota_id, f"Error convirtiendo .doc a .docx: {e}")
        return False
    _encolar_correccion(nota_id, ruta_base, nota["nombre_original"], nota.get("prioridad"))  # una por vez, en orden de prioridad
    return True


def _envio_directo(usuario):
    """Quien envía sin pasar por la aprobación de Coordinación (para los textos de los botones)."""
    return bool(usuario) and (auth.es_admin(usuario) or usuario["rol"] == auth.ROL_COORDINADOR)


def _cumple_coordinacion_por_jerarquia(usuario, nota):
    """Ola 6 (113). Jerarquía del organismo: Asesor → Coordinador → Dirección (Subdirección y Dirección General).
    Si quien sube o envía es la Coordinación del equipo de la nota o la Dirección, no se espera su propia aprobación."""
    if not usuario or not nota:
        return False
    return auth.es_admin(usuario) or (usuario["rol"] == auth.ROL_COORDINADOR and usuario.get("equipo_id") == nota["equipo_id"])


def _coordinacion_implicita(usuario, nota_id, background_tasks=None):
    """Si corresponde, da por cumplida la etapa de Coordinación (queda dicho en el historial) y arranca la corrección."""
    nota = db.obtener_nota(nota_id)
    if not nota or nota["estado"] not in ("SUBIDA", "BORRADOR") or not _cumple_coordinacion_por_jerarquia(usuario, nota):
        return False
    etiqueta = auth.ETIQUETAS_ROL.get(usuario["rol"], usuario["rol"])
    try:
        return _arrancar_correccion(
            nota, f"Etapa de Coordinación considerada cumplida por intervención de {usuario['nombre']} ({etiqueta})", background_tasks,
            desde=(nota["estado"],))
    except db.ConflictoDeEstado:
        return False


# =====================================================
# ENDPOINTS — ENVÍO A COORDINACIÓN (el asesor prepara y recién después envía)
# =====================================================
def _enviar_notas(usuario, ids, background_tasks=None, sin_clasificar=None):
    """Envía los borradores. Reforma 14/11/2026: un documento sin clasificar no se envía (se anota en `sin_clasificar`)."""
    enviadas = 0
    ya = set()
    for nid in ids:
        nota = db.obtener_nota(nid)
        if nota is None:
            continue
        if not _puede_gestionar_nota(usuario, nota):
            raise HTTPException(status_code=403, detail=f"No podés enviar «{nota['nombre_original']}»")
        # Un documento de un bloque viaja con los demás borradores de ese bloque.
        grupo = [nota]
        if nota.get("bloque_id"):
            bloque = db.obtener_bloque(nota["bloque_id"])
            grupo = [n for n in (bloque["notas"] if bloque else []) if n["estado"] == "BORRADOR" and _puede_gestionar_nota(usuario, n)] or [nota]
        for n in grupo:
            if n["id"] in ya or n["estado"] != "BORRADOR":
                continue
            ya.add(n["id"])
            if clasificacion_notas.es_informe(n) and not clasificacion_notas.confirmada(n):
                if sin_clasificar is not None:
                    sin_clasificar.append(n)
                continue
            # Ola 6 (113): si envía la Coordinación del equipo o la Dirección, va directo a corrección.
            if _coordinacion_implicita(usuario, n["id"], background_tasks):
                enviadas += 1
                continue
            try:
                db.enviar_a_coordinacion(n["id"])
                enviadas += 1
                rutas_notas_ia.evaluar_reglas(n["id"], "ENVIO")  # propuesta 2 (solo propone)
            except db.ConflictoDeEstado:
                continue
    return enviadas


@app.post("/enviar-a-coordinacion")
async def enviar_varias_a_coordinacion(request: Request, background_tasks: BackgroundTasks = None):
    usuario = usuario_actual(request)
    form = await request.form()
    ids = []
    for valor in form.getlist("nota_ids"):
        try:
            ids.append(int(valor))
        except ValueError:
            continue
    if not ids:
        raise HTTPException(status_code=422, detail="Marcá al menos un documento para enviar")
    sin_clasificar = []
    enviadas = _enviar_notas(usuario, list(dict.fromkeys(ids)), background_tasks, sin_clasificar)
    aviso = (f" Sin enviar porque falta clasificarlos: {', '.join(n['nombre_original'] for n in sin_clasificar[:5])}"
             + (f" y {len(sin_clasificar) - 5} más" if len(sin_clasificar) > 5 else "") + " (abrí cada uno y confirmá la clasificación).") if sin_clasificar else ""
    if not enviadas:
        if sin_clasificar:
            _flash(request, "⚠ No se envió ninguno." + aviso)
            return RedirectResponse(url="/", status_code=303)
        raise HTTPException(status_code=409, detail="Ninguno de los documentos marcados estaba sin enviar")
    _flash(request, (f"Se enviaron {enviadas} documento(s) a corrección (la etapa de Coordinación se da por cumplida)." if _envio_directo(usuario)
           else f"Se enviaron {enviadas} documento(s) a Coordinación.") + aviso)
    return RedirectResponse(url="/", status_code=303)


@app.post("/nota/{nota_id}/enviar")
def enviar_nota_a_coordinacion(request: Request, nota_id: int, background_tasks: BackgroundTasks = None):
    usuario = usuario_actual(request)
    sin_clasificar = []
    if not _enviar_notas(usuario, [nota_id], background_tasks, sin_clasificar):
        if sin_clasificar:
            _flash(request, "⚠ Antes de enviarlo, confirmá la clasificación del documento (tipo, alcance, confidencialidad, expediente y destinatario).")
            return RedirectResponse(url=f"/nota/{nota_id}#clasificacion", status_code=303)
        raise HTTPException(status_code=409, detail="Esta nota ya fue enviada a Coordinación")
    implicita = (db.obtener_nota(nota_id) or {}).get("estado") not in ("SUBIDA", None)
    _flash(request, "Enviada: la etapa de Coordinación se dio por cumplida y arrancó la corrección." if implicita else "Enviada a Coordinación.")
    return _recargar_pagina()


@app.post("/nota/{nota_id}/aprobar-coordinacion", response_class=HTMLResponse)
def aprobar_coordinacion(request: Request, nota_id: int, background_tasks: BackgroundTasks):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_COORDINADOR)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not auth.es_admin(usuario) and nota["equipo_id"] != usuario["equipo_id"]:
        raise HTTPException(status_code=403, detail="Esta nota no es de tu equipo")
    if nota["estado"] != "SUBIDA":
        raise HTTPException(status_code=409, detail="Esta nota ya fue aprobada o ya no está pendiente")

    _arrancar_correccion(nota, "Aprobada por Coordinación", background_tasks)

    nota = db.obtener_nota(nota_id)
    nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
    return templates.TemplateResponse(
        "_estado_nota.html", {"request": request, "nota": nota, "etapas": flujo.ETAPAS}
    )


@app.post("/nota/{nota_id}/confirmar-pdf", response_class=HTMLResponse)
def confirmar_pdf(request: Request, nota_id: int):
    """Dirección confirma el pase a Mesa de un PDF aprobado por Coordinación (A_CONFIRMAR → ADJUNTO)."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not db.es_pdf(nota):
        raise HTTPException(status_code=409, detail="Esta acción es solo para PDFs")
    if nota["estado"] != "A_CONFIRMAR":
        raise HTTPException(status_code=409, detail="Este PDF no está pendiente de confirmación")
    db.actualizar_estado(nota_id, "ADJUNTO",
                         comentario=f"Adjunto confirmado para Mesa por {usuario['nombre']}",
                         desde=("A_CONFIRMAR",))
    nota = db.obtener_nota(nota_id)
    return _ver_pdf(request, usuario, nota)


@app.post("/nota/{nota_id}/devolver-pdf", response_class=HTMLResponse)
async def devolver_pdf(request: Request, nota_id: int):
    """Dirección devuelve un PDF a Coordinación para re-revisión (A_CONFIRMAR → SUBIDA)."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not db.es_pdf(nota):
        raise HTTPException(status_code=409, detail="Esta acción es solo para PDFs")
    if nota["estado"] != "A_CONFIRMAR":
        raise HTTPException(status_code=409, detail="Este PDF no está pendiente de confirmación")
    form = await request.form()
    motivo = (form.get("motivo") or "").strip()
    comentario = f"PDF devuelto a Coordinación por {usuario['nombre']}"
    if motivo:
        comentario += f": {motivo}"
    db.actualizar_estado(nota_id, "SUBIDA", comentario=comentario, desde=("A_CONFIRMAR",))
    nota = db.obtener_nota(nota_id)
    return _ver_pdf(request, usuario, nota)


@app.post("/bloque/{bloque_id}/confirmar-pdfs")
def confirmar_pdfs_bloque(request: Request, bloque_id: int):
    """Dirección confirma todos los PDFs A_CONFIRMAR de un bloque de una vez."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR)
    bloque = db.obtener_bloque(bloque_id)
    if not bloque:
        raise HTTPException(status_code=404)
    confirmados = 0
    for n in bloque["notas"]:
        if db.es_pdf(n) and n["estado"] == "A_CONFIRMAR":
            db.actualizar_estado(n["id"], "ADJUNTO",
                                 comentario=f"Confirmado por {usuario['nombre']} (lote bloque {bloque_id}): pasa a Mesa de Entradas",
                                 desde=("A_CONFIRMAR",))
            confirmados += 1
    _flash(request, f"Se confirmaron {confirmados} PDF(s) para pasar a Mesa.")
    return RedirectResponse(url=f"/bloque/{bloque_id}", status_code=303)


@app.post("/nota/{nota_id}/rechazar-coordinacion", response_class=HTMLResponse)
async def rechazar_coordinacion(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_COORDINADOR)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not auth.es_admin(usuario) and nota["equipo_id"] != usuario["equipo_id"]:
        raise HTTPException(status_code=403, detail="Esta nota no es de tu equipo")
    if nota["estado"] != "SUBIDA":
        raise HTTPException(status_code=409, detail="Esta nota ya no está pendiente de Coordinación")

    form = await request.form()
    motivo = (form.get("motivo") or "").strip()
    if not motivo:
        raise HTTPException(status_code=422, detail="El motivo del rechazo es obligatorio")

    db.marcar_rechazada(nota_id, motivo)
    nota = db.obtener_nota(nota_id)
    nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
    return templates.TemplateResponse(
        "_estado_nota.html", {"request": request, "nota": nota, "etapas": flujo.ETAPAS}
    )


def _puede_editar_texto(usuario, nota):
    """Quién puede corregir el texto de una nota mientras Coordinación todavía
    no la evaluó: el asesor que la subió, la Coordinación de su equipo y la
    Dirección. Un borrador solo lo edita quien lo subió."""
    if nota["estado"] not in ("BORRADOR", "SUBIDA"):
        return False
    if nota["estado"] == "BORRADOR":
        return nota["usuario_id"] == usuario["id"]
    if auth.es_admin(usuario):
        return True
    if usuario["rol"] == auth.ROL_COORDINADOR:
        return nota["equipo_id"] == usuario["equipo_id"]
    if usuario["rol"] == auth.ROL_ASESOR:
        return nota["usuario_id"] == usuario["id"]
    return False


def _aplicar_ediciones_texto(nota, ediciones, usuario, detalle=None):
    """Guarda ediciones {indice: texto} sobre la copia de trabajo (el original
    archivado nunca se toca), deja constancia y vuelve a chequear. Devuelve
    cuántos párrafos cambiaron de verdad."""
    nota_id = nota["id"]
    ruta_fuente = nota.get("ruta_base") or nota["ruta_original"]
    actuales, _ = nucleo.obtener_vista_previa(ruta_fuente)
    por_indice = {p["indice"]: p["texto"] for p in (actuales or [])}
    cambiadas = {i: t for i, t in ediciones.items() if (t or "").strip() != por_indice.get(i, "").strip()}
    if not cambiadas:
        return 0
    ruta_destino = CARPETA_BASE / f"{nota_id}.docx"
    try:
        nucleo.guardar_texto_editado(ruta_fuente, ruta_destino, cambiadas)
    except Exception as e:
        logging.exception(f"Error guardando edición manual de la nota {nota_id}")
        raise HTTPException(status_code=500, detail=f"No se pudo guardar la edición: {e}")
    db.actualizar_ruta_base(nota_id, ruta_destino)
    db.registrar_evento(nota_id, detalle or f"Texto corregido por {usuario['nombre']}: {len(cambiadas)} párrafo(s)")
    try:
        db.indexar_texto(nota_id, nucleo.texto_de_documento(ruta_destino))
        try:
            import coincidencias as _coinc; _coinc.buscar_y_guardar(nota_id)
        except Exception as _ce:
            logging.warning(f"coincidencias DOCX {nota_id}: {_ce}")
    except Exception:
        logging.exception("No se pudo reindexar el texto editado")
    # El texto cambió: se recalcula la consistencia y se encola el resto del
    # análisis (la corrección precalculada pierde validez sola, por la huella).
    _reanalizar_consistencia(nota_id)
    try:
        automatico.encolar_analisis(nota_id)
    except Exception:
        logging.exception("No se pudo reencolar el análisis")
    # Si la nota ya está en manos de Coordinación y la cambió el asesor, se le avisa.
    if nota["estado"] == "SUBIDA" and usuario["rol"] == auth.ROL_ASESOR:
        db.avisar_a_coordinacion(
            nota_id, f"{usuario['nombre']} corrigió el texto de «{nota['nombre_original']}» mientras esperaba tu evaluación."
        )
    return len(cambiadas)


@app.post("/nota/{nota_id}/editar-base")
async def editar_base(request: Request, nota_id: int):
    """Coordinación edita el texto de la nota ANTES de aprobarla, sin
    descargar el archivo. Nunca toca el original archivado: guarda una
    copia nueva en datos/base/ y la nota usa esa copia como punto de
    partida tanto para la vista previa como para la corrección automática
    (ver aprobar_coordinacion)."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_editar_texto(usuario, nota):
        if nota["estado"] not in ("BORRADOR", "SUBIDA"):
            raise HTTPException(status_code=409, detail="El texto ya no se puede editar acá: la nota pasó a corrección")
        raise HTTPException(status_code=403, detail="No tenés permiso para editar el texto de esta nota")

    form = await request.form()
    ediciones = {}
    for clave, valor in form.items():
        if clave.startswith("texto_"):
            try:
                ediciones[int(clave[len("texto_"):])] = valor
            except ValueError:
                continue
    cambiadas = _aplicar_ediciones_texto(nota, ediciones, usuario)
    _flash(request, f"Texto guardado ({cambiadas} párrafo(s) modificado(s))." if cambiadas else "No había cambios para guardar.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


# =====================================================
# ENDPOINTS — PANTALLA DEL DIRECTOR (dos columnas)
# =====================================================
def _contexto_analisis(usuario, nota, puede_gestionar):
    """Todo lo que la ficha muestra del análisis automático: tema, prioridad
    sugerida, consistencia, mejoras desde el informe y estado de los
    trabajos de IA."""
    activa = nota["estado"] in ESTADOS_ACTIVOS
    sugerida = None
    if (
        puede_gestionar and activa
        and nota.get("prioridad_sugerida_estado") == "PENDIENTE"
        and nota.get("prioridad_sugerida")
        and _rango(nota["prioridad_sugerida"]) < _rango(nota["prioridad"])
    ):
        sugerida = {
            "prioridad": nota["prioridad_sugerida"],
            "motivo": nota.get("prioridad_motivo") or "",
            # El asesor ya indicó una prioridad distinta de Normal: se avisa.
            "pisa_la_del_asesor": nota["prioridad"] != auth.PRIORIDAD_NORMAL,
        }
    referencias = []
    if puede_gestionar and activa:
        referencias = [
            n for n in db.listar_notas()
            if n["equipo_id"] == nota["equipo_id"] and n["id"] != nota["id"]
            and n["estado"] not in automatico.ESTADOS_TERMINALES
            and nucleo.clasificar_documento(n["nombre_original"]) in ("INFORME", "PROVEIDO")
        ]
    trabajos = db.estado_ia_nota(nota["id"])
    ver_mejoras = puede_gestionar or auth.es_admin(usuario) or usuario["rol"] == auth.ROL_COORDINADOR
    return {
        "analisis_sugerida": sugerida,
        "analisis_temas": sorted(analisis.TEMAS.keys()),
        "analisis_referencias": referencias,
        "analisis_ref_actual": db.obtener_nota(nota["ref_nota_id"]) if nota.get("ref_nota_id") else None,
        "analisis_hallazgos": db.listar_hallazgos(nota["id"]),
        "analisis_mejoras": db.listar_mejoras(nota["id"]) if ver_mejoras else [],
        "analisis_trabajos": [
            {"tipo": t, "estado": trabajos[t]["estado"], "error": trabajos[t].get("error")}
            for t in automatico.TIPOS_TRABAJO if t in trabajos
        ],
        "analisis_precalculada": db.tiene_propuestas_previas(nota["id"]),
        "ia_ventana": automatico.descripcion_ventana(),
    }


@app.get("/nota/{nota_id}", response_class=HTMLResponse)
def ver_nota(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para ver esta nota")
    nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
    db.registrar_acceso(nota_id, "VER")
    if db.es_pdf(nota):
        return _ver_pdf(request, usuario, nota)
    if nota["estado"] == "RECHAZADA":
        db.marcar_nota_vista(usuario["id"], nota_id)
    db.marcar_leidas_por_url(usuario["id"], f"/nota/{nota_id}")
    # Si Coordinación ya guardó una edición manual (ruta_base), la vista
    # previa muestra esa versión; el original archivado sigue intacto y
    # siempre descargable aparte.
    ruta_para_previsualizar = nota.get("ruta_base") or nota["ruta_original"]
    parrafos_originales, error_vista_previa = nucleo.obtener_vista_previa(ruta_para_previsualizar, sin_esperar=True)

    # Panel lateral: otros documentos que todavía están en circuito (no
    # despachados ni rechazados), para poder saltar entre ellos sin volver
    # siempre al listado general.
    ESTADOS_PENDIENTES = ("BORRADOR", "SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "FIRMADA", "PARA_REMITIR", "ERROR", "A_CONFIRMAR")
    pendientes = [
        n for n in db.listar_notas()
        if n["id"] != nota_id and n["estado"] in ESTADOS_PENDIENTES and _puede_ver_nota(usuario, n)
    ]
    for n in pendientes:
        n["etapa"] = flujo.etapa_para_estado(n["estado"])
    _anotar_prioridad_efectiva(pendientes)
    pendientes.sort(key=lambda n: _rango(n["prioridad_efectiva"]))

    # Bloque al que pertenece (si lo hay) y bloques del mismo equipo a los
    # que se podría sumar (si no pertenece a ninguno).
    puede_gestionar = _puede_gestionar_nota(usuario, nota) and nota["estado"] in ESTADOS_ACTIVOS
    bloque = None
    bloques_disponibles = []
    if nota.get("bloque_id"):
        bloque = db.obtener_bloque(nota["bloque_id"])
        for bn in bloque["notas"]:
            bn["etapa"] = flujo.etapa_para_estado(bn["estado"])
        bloque["faltan"] = sum(1 for bn in bloque["notas"] if bn["estado"] not in LISTOS_PARA_DESPACHO)
        bloque["hay_firmados"] = any(bn["ruta_firmada"] for bn in bloque["notas"])
        mejor = min((bn["prioridad"] for bn in bloque["notas"]), key=_rango)
        nota["prioridad_efectiva"] = mejor
    else:
        nota["prioridad_efectiva"] = nota["prioridad"]
        if puede_gestionar:
            bloques_disponibles = [
                b for b in db.listar_bloques(nota["equipo_id"])
                if not any(
                    bn["estado"] == "DESPACHADA" for bn in (db.obtener_bloque(b["id"]) or {"notas": []})["notas"]
                )
            ]

    contexto_analisis = _contexto_analisis(usuario, nota, puede_gestionar)
    puede_editar_texto = _puede_editar_texto(usuario, nota)
    for h in contexto_analisis["analisis_hallazgos"]:
        h["aplicable"] = bool(puede_editar_texto and _proponer_aplicacion(nota, h, parrafos_originales))
    versiones = db.cadena_de_versiones(nota_id)
    if len(versiones) < 2:
        versiones = []
    es_dir = auth.es_admin(usuario)

    return templates.TemplateResponse(
        "nota.html",
        {
            "request": request,
            "nota": nota,
            **contexto_analisis,
            "versiones": versiones,
            "puede_subir_version": _puede_gestionar_nota(usuario, nota),
            "integridad": integridad.verificar_nota(nota) if es_dir else [],
            "accesos_nota": db.listar_accesos(500, nota_id) if es_dir else [],
            "etapas": flujo.ETAPAS,
            "parrafos_originales": parrafos_originales,
            "error_vista_previa": error_vista_previa,
            "pendientes": pendientes,
            "puede_gestionar": puede_gestionar,
            "puede_editar_texto": puede_editar_texto,
            "bloque": bloque,
            "bloques_disponibles": bloques_disponibles,
            "puede_gestionar_dup": _puede_gestionar_nota(usuario, nota), "descartables": db.ESTADOS_DESCARTABLES,
        },
    )


@app.get("/nota/{nota_id}/fragmento", response_class=HTMLResponse)
def ver_nota_fragmento(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para ver esta nota")
    nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
    return templates.TemplateResponse(
        "_estado_nota.html", {"request": request, "nota": nota, "etapas": flujo.ETAPAS}
    )


@app.get("/nota/{nota_id}/revisar", response_class=HTMLResponse)
def revisar_nota(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # decisión del Director/Subdirector — ver Tarea de permisos
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] != "EN_REVISION":
        raise HTTPException(status_code=409, detail="Esta nota no tiene propuestas pendientes de revisión")

    # Arma la vista continua: cada párrafo del documento, con su propuesta
    # asociada si la tiene (ver nucleo.generar_propuestas_documento).
    # Beta 01 (157): una propuesta que no cambia nada visible (espacios duros, invisibles) no se muestra ni se aplica.
    invisibles = [p for p in nota["propuestas"] if p["propuesto"] != p["original"] and nucleo.igual_a_la_vista(p["propuesto"], p["original"])]
    for p in invisibles:
        p["propuesto"], p["cambios"] = p["original"], []
    if invisibles:
        db.actualizar_propuestas(nota_id, nota["propuestas"])
    propuestas_por_id = {p["id"]: p for p in nota["propuestas"]}
    filas = []
    for parrafo in nota.get("todos_los_parrafos", []):
        propuesta = propuestas_por_id.get(parrafo["propuesta_id"]) if parrafo["propuesta_id"] is not None else None
        if propuesta is not None:
            propuesta["diff"] = nucleo.calcular_diff_resaltado(propuesta["original"], propuesta["propuesto"])
            propuesta["tramos"] = nucleo.agrupar_cambios(propuesta["diff"])
        filas.append({"parrafo": parrafo, "propuesta": propuesta})

    # Sugerencias de mejora desde el informe que todavía se aplican al texto
    # actual de su párrafo (si el párrafo cambió, ya no se muestran).
    mejoras_por_propuesta = {}
    for m in db.listar_mejoras(nota_id, solo_pendientes=True):
        for p in nota["propuestas"]:
            if p["ubicacion"] == m["ubicacion"] and p["original"] == m["original"]:
                m["diff"] = nucleo.calcular_diff_resaltado(m["original"], m["propuesto"])
                mejoras_por_propuesta.setdefault(p["id"], []).append(m)
                break

    cambios = [f["propuesta"] for f in filas
               if f.get("propuesta") and f["propuesta"]["original"] != f["propuesta"]["propuesto"]]
    cambios_json_data = {
        str(p["id"]): {"texto_original": p["original"], "texto_propuesto": p["propuesto"]}
        for p in cambios
    }
    # Banda de serie: bloque o grupo de relacionadas
    import siguiente_firma as sf
    en_serie = bool(request.query_params.get("serie"))
    grupo_serie = None
    if en_serie:
        bloque_id = nota.get("bloque_id")
        if bloque_id:
            bloque = db.obtener_bloque(bloque_id)
            if bloque:
                docs = sorted(bloque["notas"], key=lambda d: (d.get("creado_en", ""), d["id"]))
                grupo_serie = {
                    "tipo": "bloque", "persona": bloque.get("nombre", ""),
                    "documentos": [{"id": d["id"], "abrev": sf.abrev_nombre(d["nombre_original"]),
                                    "firmado": d["estado"] in ("FIRMADA", "PARA_REMITIR", "DESPACHADA"),
                                    "actual": d["id"] == nota_id} for d in docs],
                }
        else:
            todas = _filtrar_por_visibilidad(db.listar_notas(), usuario)
            pendientes_rev = [n for n in todas if n["estado"] in ("PROCESADA", "EN_REVISION")]
            rel = sf.relacionadas_pendientes(nota, [n for n in pendientes_rev if n["id"] != nota_id])
            if rel:
                grupo = sorted([nota] + rel, key=sf._orden_en_grupo)
                grupo_serie = {
                    "tipo": "relacionadas",
                    "persona": sf.normalizar_persona(nota.get("nombre_original", "")) or "",
                    "documentos": [{"id": d["id"], "abrev": sf.abrev_nombre(d["nombre_original"]),
                                    "firmado": d["estado"] in ("FIRMADA", "PARA_REMITIR", "DESPACHADA"),
                                    "actual": d["id"] == nota_id} for d in grupo],
                }

    return templates.TemplateResponse(
        "revisar.html",
        {
            "request": request, "nota": nota, "filas": filas,
            "cambios": cambios,
            "total_cambios": len(cambios),
            "cambios_aceptados": 0,
            "en_serie": en_serie, "grupo_serie": grupo_serie,
            "cambios_json": json.dumps(cambios_json_data, ensure_ascii=False),
            "mejoras_por_propuesta": mejoras_por_propuesta,
            "analisis_hallazgos": db.listar_hallazgos(nota_id),
        },
    )


@app.post("/nota/{nota_id}/mejora/{mejora_id}/{accion}")
def resolver_mejora_informe(request: Request, nota_id: int, mejora_id: int, accion: str):
    """Aceptar o descartar una sugerencia de mejora desde el informe, sin
    salir de "Revisar correcciones" (la pantalla lo llama con fetch, para no
    perder lo que el Director ya editó en otros cuadros). Aceptar NO guarda
    el documento: solo carga el texto sugerido como propuesta de ese párrafo,
    que el Director revisa y decide como cualquier otra."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # decisión del Director/Subdirector
    if accion not in ("aceptar", "descartar"):
        raise HTTPException(status_code=404, detail="Acción desconocida")
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] != "EN_REVISION":
        raise HTTPException(status_code=409, detail="Esta nota no tiene propuestas pendientes de revisión")
    mejora = db.obtener_mejora(mejora_id)
    if mejora is None or mejora["nota_id"] != nota_id or mejora["estado"] != "PENDIENTE":
        raise HTTPException(status_code=409, detail="Esa sugerencia ya fue resuelta")

    if accion == "descartar":
        db.resolver_mejora(mejora_id, "DESCARTADA")
        return {"ok": True}

    propuestas = nota["propuestas"]
    propuesta = next(
        (p for p in propuestas if p["ubicacion"] == mejora["ubicacion"] and p["original"] == mejora["original"]), None
    )
    if propuesta is None:
        db.resolver_mejora(mejora_id, "OBSOLETA")
        raise HTTPException(status_code=409, detail="El párrafo cambió desde que se hizo la sugerencia")
    anterior = propuesta["propuesto"]
    propuesta["propuesto"] = mejora["propuesto"]
    propuesta["cambios"].append({"fuente": "informe", "original": anterior, "corregido": mejora["propuesto"]})
    db.actualizar_propuestas(nota_id, propuestas)
    db.resolver_mejora(mejora_id, "ACEPTADA")
    db.registrar_evento(nota_id, f"Mejora desde el informe cargada en un párrafo ({mejora['tipo']}) — {usuario['nombre']}")
    return {"ok": True, "propuesta_id": propuesta["id"], "texto": mejora["propuesto"]}


@app.post("/nota/{nota_id}/aplicar-propuestas")
async def aplicar_propuestas(nota_id: int, request: Request):
    """Lee el formulario y hace el trabajo pesado (Word, formato, texto) en otro hilo, para no frenar al resto del servidor."""
    from starlette.concurrency import run_in_threadpool
    _exigir_rol(usuario_actual(request))  # decisión del Director/Subdirector (se vuelve a controlar adentro)
    form = await request.form()
    return await run_in_threadpool(_aplicar_propuestas, nota_id, request, form)


def _aplicar_propuestas(nota_id, request, form):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # decisión del Director/Subdirector
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] != "EN_REVISION":
        raise HTTPException(status_code=409, detail="Esta nota no tiene propuestas pendientes de revisión")

    decisiones = {}
    motivos_rechazo = {}
    sin_cambios = form.get("y_firmar") == "sin_cambios"  # «Firmar sin cambios»: se firma el original, se descartan las propuestas
    for propuesta in nota["propuestas"]:
        pid = propuesta["id"]
        if sin_cambios:
            decisiones[pid] = {"decision": "rechazar", "texto_final": propuesta["original"]}
            continue
        # El texto propuesto ahora se edita directamente en el cuadro: si el
        # Director no lo tocó, lo que llega es igual a "propuesto" (=
        # aceptado tal cual); si lo editó, difiere (= modificado). El
        # checkbox de rechazo es la única acción aparte, para los casos en
        # los que no quiere la propuesta de la IA en absoluto.
        if form.get(f"rechazar_{pid}"):
            decision = "rechazar"
            texto_final = propuesta["original"]
            motivos_rechazo[pid] = form.get(f"motivo_rechazo_{pid}") or None  # propuesta 16 (opcional)
        else:
            texto_final = (form.get(f"texto_{pid}") or "").strip() or propuesta["propuesto"]
            decision = "aceptar" if texto_final.strip() == propuesta["propuesto"].strip() else "modificar"
        decisiones[pid] = {"decision": decision, "texto_final": texto_final}

    # Tiene que partir del MISMO documento que se analizó (la base editada
    # por Coordinación, si la hay), para que las ubicaciones de los párrafos
    # coincidan con las de las propuestas.
    ruta_base_analizada = Path(nota.get("ruta_analizada") or nota.get("ruta_base") or nota["ruta_original"])
    ruta_resultado = CARPETA_PROCESADOS / f"{nota_id}.docx"
    try:
        if ruta_base_analizada.suffix.lower() == ".doc":
            ruta_docx_analizada = Path(str(ruta_base_analizada) + "x")
            if ruta_docx_analizada.exists():
                ruta_base_analizada = ruta_docx_analizada
            else:
                ruta_base_analizada = nucleo.convertir_doc_a_docx(ruta_base_analizada)
        decisiones_completas = nucleo.aplicar_decisiones_director(
            ruta_base_analizada, ruta_resultado, nota["propuestas"], decisiones
        )
        # Formato general institucional (Arial 12, justificado, interlineado
        # 1,5, espaciado 6/0) — solo para documentos clasificados como Nota;
        # el resto conserva el formato que traían, igual que ya pasa con el
        # texto (ver nucleo.es_nota).
        if nota["es_nota"]:
            nucleo.aplicar_formato_institucional(ruta_resultado)
    except Exception as e:
        logging.exception(f"Error aplicando decisiones de la nota {nota_id}")
        raise HTTPException(status_code=500, detail=f"No se pudieron aplicar las decisiones: {e}")

    # Registro para trazabilidad y para el módulo de aprendizaje (solo las
    # "modificado" alimentan la minería de patrones, ver aprendizaje.py).
    # Los párrafos donde no hubo ningún cambio real (ni automático ni
    # manual) no se registran: no aportan nada y solo ensucian el historial
    # y la lista de "cambios aplicados".
    cambios_aplicados = []
    for d in decisiones_completas:
        hubo_cambio = d["propuesto"] != d["original"] or d["texto_final"] != d["original"]
        if not hubo_cambio:
            continue
        db.registrar_decision_director(
            nota_id, d["ubicacion"], d["original"], d["propuesto"], d["texto_final"], d["decision"], d["fuentes"]
        )
        if d["decision"] in ("aceptar", "modificar"):
            cambios_aplicados.append({
                "fuente": "+".join(d["fuentes"]) if d["fuentes"] else "director (manual)",
                "original": d["original"],
                "corregido": d["texto_final"],
            })

    db.guardar_resultado_tras_revision(
        nota_id, ruta_resultado, cambios_aplicados, sha256_resultado=integridad.sha256_si_existe(ruta_resultado)
    )
    import trazabilidad_ia  # Ola 6 (137): qué hizo la persona con lo que propuso la IA
    trazabilidad_ia.registrar_revision(nota_id, decisiones_completas, usuario)
    if not sin_cambios:  # propuesta 16: cuenta por regla de corrección (con «Firmar sin cambios» no se juzgó cada propuesta)
        rutas_notas_ia.registrar_reglas_revision(nota_id, decisiones_completas, motivos_rechazo, usuario)
    try:
        db.indexar_texto(nota_id, nucleo.texto_de_documento(ruta_resultado))
        try:
            import coincidencias as _coinc; _coinc.buscar_y_guardar(nota_id)
        except Exception as _ce:
            logging.warning(f"coincidencias DOCX revisado {nota_id}: {_ce}")
    except Exception:
        logging.exception("No se pudo reindexar el texto de la nota")
    if form.get("y_firmar"):  # «Firmar sin cambios» / «Guardar y firmar»: la ficha abre la firma (PIN y fecha como siempre)
        return RedirectResponse(url=f"/nota/{nota_id}?firmar=1", status_code=303)
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/reabrir-revision", response_class=HTMLResponse)
async def reabrir_revision(request: Request, nota_id: int):
    """Vuelve a mandar la nota a 'Revisar correcciones', para corregir algo
    que se detectó tarde. Dos casos:
    - Desde PROCESADA (antes de firmar): se nota un error al revisarla antes
      de la firma. `motivo` es opcional.
    - Desde FIRMADA (antes de despachar): se nota un error al descargarla
      para el despacho. `motivo` es obligatorio — queda como alerta en el
      historial, porque implica descartar la firma ya insertada.
    En ambos casos parte SIEMPRE del documento procesado SIN firma
    (nota.ruta_resultado), nunca del firmado: si había firma, queda
    descartada y hay que volver a insertarla después de corregir. Se vuelve
    a correr diccionario/LanguageTool/IA igual que la primera vez (si es
    Nota), para que aparezca cualquier cosa que haya quedado pendiente."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # Director/Subdirector
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] not in ("PROCESADA", "FIRMADA"):
        raise HTTPException(status_code=409, detail="Esta nota no está en un estado desde el que se pueda reabrir")

    form = await request.form()
    motivo = (form.get("motivo") or "").strip()
    if nota["estado"] == "FIRMADA" and not motivo:
        raise HTTPException(
            status_code=422,
            detail="Al reabrir una nota ya firmada hace falta describir qué se encontró",
        )

    if not nota.get("ruta_resultado"):
        raise HTTPException(status_code=409, detail="Esta nota no tiene un documento procesado para reabrir")
    ruta_fuente = Path(nota["ruta_resultado"])

    try:
        with nucleo.con_tipo_documental(nota.get("tipo_documental")):  # propuesta 17
            resultado = nucleo.generar_propuestas_documento(ruta_fuente, nota["nombre_original"])
    except Exception as e:
        logging.exception(f"Error reabriendo revisión de la nota {nota_id}")
        raise HTTPException(status_code=500, detail=f"No se pudo reabrir la revisión: {e}")

    prefijo = "Reabierta desde firmada" if nota["estado"] == "FIRMADA" else "Reabierta antes de firmar"
    comentario = f"{prefijo} — {motivo}" if motivo else f"{prefijo} para volver a corregir"

    if resultado["propuestas"]:
        db.guardar_propuestas(
            nota_id,
            tipo=resultado["tipo"],
            observaciones=resultado["observaciones"],
            propuestas=resultado["propuestas"],
            todos_los_parrafos=resultado["todos_los_parrafos"],
            es_nota=resultado["corregido"],
            comentario=comentario,
            alerta=motivo or None,
            desde=("PROCESADA", "FIRMADA"),
            # Las propuestas se calcularon sobre el documento PROCESADO, no
            # sobre el original: al aplicarlas hay que partir del mismo.
            ruta_analizada=ruta_fuente,
        )
    else:
        # Caso borde: el documento procesado quedó sin texto. No hay nada
        # para revisar, así que vuelve directo a PROCESADA con el mismo
        # archivo (sigue listo para firmar como antes).
        db.guardar_resultado_sin_revision(nota_id, tipo=resultado["tipo"], ruta_resultado=ruta_fuente)

    _reanalizar_consistencia(nota_id)
    nota = db.obtener_nota(nota_id)
    nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
    return templates.TemplateResponse(
        "_estado_nota.html", {"request": request, "nota": nota, "etapas": flujo.ETAPAS}
    )


@app.post("/nota/{nota_id}/rechazar-direccion", response_class=HTMLResponse)
async def rechazar_direccion(request: Request, nota_id: int):
    """Rechazo OPERATIVO de la nota completa por parte del Director, no
    una corrección de redacción — por eso no toca las propuestas ni pasa
    por aplicar_decisiones_director."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # Director/Subdirector
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] != "EN_REVISION":
        raise HTTPException(status_code=409, detail="Esta nota no está pendiente de revisión del Director")

    form = await request.form()
    motivo = (form.get("motivo") or "").strip()
    if not motivo:
        raise HTTPException(status_code=422, detail="El motivo del rechazo es obligatorio")

    db.marcar_rechazada(nota_id, motivo)
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


# =====================================================
# ENDPOINTS — FIRMA Y DESPACHO
# =====================================================
@app.post("/nota/{nota_id}/firmar")
def firmar_nota(request: Request, nota_id: int, fecha: str = Form(None)):
    """Firma continua: después de firmar se abre el siguiente documento pendiente de firma (nunca el despacho)."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # solo Director/Subdirector — cada uno usa SU firma
    cola_antes = [n["id"] for n in _cola_firma(usuario)]
    actual = db.obtener_nota(nota_id) or {}
    nombre = actual.get("nombre_original", "")
    if _ya_firmada(actual):
        # Doble toque, «Atrás» del navegador o una página vieja: la nota ya está firmada. No se firma de nuevo ni se
        # muestra un error: se sigue con la próxima de la cola.
        _flash(request, f"«{nombre}» ya estaba firmada.")
        restantes = [i for i in cola_antes if i != nota_id]
        return Response(status_code=200, headers={"HX-Redirect": f"/nota/{restantes[0]}?serie=1" if restantes else "/firma-terminada"})
    _firmar(usuario, nota_id, fecha)
    destino = _despues_de_firmar(request, nota_id, cola_antes, nombre, nota_firmada=actual)
    return Response(status_code=200, headers={"HX-Redirect": destino})


def _ya_firmada(nota):
    """La nota ya pasó por la firma (FIRMADA o una etapa posterior, con documento firmado)."""
    return bool(nota.get("ruta_firmada")) and nota.get("estado") in ("FIRMADA", "PARA_REMITIR", "DESPACHADA")


def _cola_firma(usuario):
    """Notas PROCESADA (listas para firmar directamente). Usada por firma-en-serie."""
    notas = [n for n in _filtrar_por_visibilidad(db.listar_notas(), usuario) if n["estado"] == "PROCESADA"]
    _anotar_prioridad_efectiva(notas)
    return _ordenar_para_firma(notas)


def _cola_completa(usuario):
    """PROCESADA + EN_REVISION sin error: todo lo que el usuario puede revisar y firmar.
    Fuente de verdad para siguiente_para_firmar y firma_terminada."""
    notas = [
        n for n in _filtrar_por_visibilidad(db.listar_notas(), usuario)
        if n["estado"] in ("PROCESADA", "EN_REVISION") and not n.get("error")
    ]
    _anotar_prioridad_efectiva(notas)
    return _ordenar_para_firma(notas)


def _despues_de_firmar(request, nota_id, cola_antes, nombre="", nota_firmada=None):
    """Adónde ir después de una firma: bloque → relacionadas → prioridad → antigüedad.
    Incluye EN_REVISION (abre /revisar) además de PROCESADA (abre modal de firma)."""
    import siguiente_firma as sf
    usuario = usuario_actual(request)
    pendientes = _cola_completa(usuario)  # PROCESADA + EN_REVISION sin error
    salteadas = set(request.session.get("serie_salteadas") or [])
    nota_dict = nota_firmada or {"id": nota_id, "bloque_id": None}
    sig_id, motivo = sf.siguiente_para_firmar(nota_dict, pendientes, salteadas)
    firmadas = int(request.session.get("serie_firmadas") or 0) + 1
    if sig_id is None:
        request.session.pop("serie_firmadas", None)
        request.session["serie_resumen"] = {"firmadas": firmadas, "salteadas": len(request.session.pop("serie_salteadas", None) or [])}
        _flash(request, f"Firmada «{nombre}». No quedan documentos pendientes.")
        return "/firma-terminada"
    sig_nota = next((n for n in pendientes if n["id"] == sig_id), {})
    sig_nombre = sig_nota.get("nombre_original", "")
    _MOTIVOS = {
        "mismo bloque": "mismo trámite (bloque)", "relacionada": "mismo trámite",
        "urgente": "URGENTE", "prioritaria": "prioritaria", "antigüedad": "más antigua",
    }
    request.session["serie_firmadas"] = firmadas
    _flash(request, f"Firmada «{nombre}». Siguiente: «{sig_nombre}» ({_MOTIVOS.get(motivo, motivo)}).")
    # EN_REVISION → va a /revisar; PROCESADA → abre modal de firma
    if sig_nota.get("estado") == "EN_REVISION":
        return f"/nota/{sig_id}/revisar?serie=1"
    return f"/nota/{sig_id}?serie=1"


@app.get("/firma-terminada", response_class=HTMLResponse)
def firma_terminada(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    cola = _cola_completa(usuario)  # PROCESADA + EN_REVISION
    procesadas = [n for n in cola if n["estado"] == "PROCESADA"]
    en_revision = [n for n in cola if n["estado"] == "EN_REVISION"]
    if procesadas:
        return RedirectResponse(url="/firma-en-serie", status_code=303)
    if en_revision:
        # Quedan documentos de control: abrir el primero en /revisar
        return RedirectResponse(url=f"/nota/{en_revision[0]['id']}/revisar?serie=1", status_code=303)
    return templates.TemplateResponse("firma_terminada.html", {"request": request, "usuario": usuario, "control": []})


def _firmar(usuario, nota_id, fecha=None):
    """Inserta la firma de quien está logueado (Director o Subdirector). Si firma en reemplazo de alguien
    ausente (ausencia cargada con esta persona como delegada), queda registrado en el historial."""
    _exigir_rol(usuario)  # solo Director/Subdirector — cada uno usa SU firma
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] not in ("PROCESADA",):
        raise HTTPException(status_code=409, detail="La nota todavía no está lista para firmar")

    if not integridad.resultado_coincide(nota):
        raise HTTPException(
            status_code=409,
            detail="El documento procesado cambió o falta en el servidor desde que se revisó: no se firma. "
                   "Reabrí la revisión para volver a generarlo.",
        )
    origen = Path(nota["ruta_resultado"])
    # Reforma 14/11/2026: el documento se firma clasificado; si llegó sin clasificar (anterior a la reforma), quien firma
    # confirma la propuesta del sistema. Los informes reciben su número de protocolo interno (DGPDH o SGPDH según quién firma).
    hoy, otra = _hoy_local(), _fecha_valida(fecha)
    if clasificacion_notas.es_informe(nota) and not clasificacion_notas.confirmada(nota):
        clasificacion_notas.confirmar_propuesta(nota, usuario)
        nota = db.obtener_nota(nota_id)
    clasif = clasificacion_notas.clasificacion_guardada(nota)
    protocolo = None
    if not clasif.lleva_protocolo and _protocolos_vigentes(nota_id):
        raise HTTPException(status_code=409, detail="Este documento tiene un número de protocolo de informe pero ya no está clasificado como informe. "
                                                    "La Dirección tiene que anular ese número (con motivo) antes de firmarlo.")
    if clasif.lleva_protocolo:
        from dgpdh.dominio import clasificacion as _cl
        from dgpdh.seguridad import auditoria as _au
        from dgpdh.servicios import protocolo as _pr
        try:
            with db._conexion() as con:
                protocolo = _pr.asignar(con, nota_id=nota_id, tipo=clasif.tipo, area=_cl.area_de_firmante(usuario["rol"]),
                                        anio=int(hoy[:4]), actor=_au.Actor.desde(usuario))  # año del servidor, aunque se firme con otra fecha
        except Exception as e:
            logging.exception(f"No se pudo asignar el número de protocolo a la nota {nota_id}")
            raise HTTPException(status_code=500, detail=f"No se pudo asignar el número de protocolo: {getattr(e, 'mensaje', e)}. No se firmó.") from e
    # Nombre definitivo del archivo firmado: el nombre original + "firmado",
    # no el id interno — así Mesa de Entrada lo reconoce de un vistazo en
    # datos/firmados/ (ver Tarea "Nombrar el .docx firmado...").
    nombre_sanitizado = _sanitizar_nombre_archivo(Path(nota["nombre_original"]).stem)
    ruta_firmada = CARPETA_FIRMADOS / f"{nombre_sanitizado} firmado.docx"
    if ruta_firmada.exists():
        # Dos notas distintas con el mismo nombre de archivo original: se
        # agrega el id para no pisar el .docx firmado de la otra.
        ruta_firmada = CARPETA_FIRMADOS / f"{nombre_sanitizado} firmado ({nota_id}).docx"
    try:
        # rol_firmante selecciona automáticamente la firma individual de
        # quien está logueado (Director o Subdirector) — ver
        # nucleo.FIRMAS_POR_ROL. Nadie elige la firma a mano.
        # Si la firma falla después de asignar el número, el número queda reservado para ESTE documento y se reutiliza al
        # reintentar (no se gasta ni pasa a otro).
        nucleo.insertar_firma_en_archivo(origen, ruta_firmada, rol_firmante=usuario["rol"], protocolo=protocolo,
                                         confidencialidad=clasif.confidencialidad, usuario_id=usuario["id"])
        # Ola 6 (120): firma en un paso con la fecha del servidor; «otra fecha» es excepcional y queda registrada.
        db.marcar_firmada(
            nota_id, ruta_firmada, sha256_firmado=integridad.sha256_si_existe(ruta_firmada),
            fecha_nota=otra or hoy,
        )
        rutas_notas_ia.evaluar_reglas(nota_id, "FIRMA")  # propuesta 2 (solo propone)
        if protocolo:
            db.registrar_evento(nota_id, f"Protocolizada: Informe N° {protocolo}")
        if otra and otra != hoy:
            db.registrar_evento(nota_id, f"Firmada por {usuario['nombre']} con otra fecha: {otra[8:10]}/{otra[5:7]}/{otra[:4]} "
                                         f"(fecha del servidor: {hoy[8:10]}/{hoy[5:7]}/{hoy[:4]})")
    except Exception as e:
        logging.exception(f"Error insertando firma en nota {nota_id}")
        raise HTTPException(status_code=500, detail=f"No se pudo insertar la firma: {e}")
    for a in db.ausencias_con_firma_delegada():
        if a.get("delegado_id") == usuario["id"]:
            db.registrar_evento(nota_id, f"Firmada por {usuario['nombre']} en reemplazo de {a['ausente']} (ausente hasta el {a['hasta'][8:10]}/{a['hasta'][5:7]})")
            break
    return True


@app.post("/nota/{nota_id}/despachar")
def despachar_nota(request: Request, nota_id: int):
    """La protocolización (número de expediente/nota) y el envío por correo
    siguen siendo tareas manuales de Mesa de Entrada; este botón solo
    registra que ya se hicieron. Si la nota pertenece a un bloque, se
    despacha el bloque completo (ver _despachar_bloque): no se puede
    despachar un documento suelto de un bloque."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] not in db.LISTAS_PARA_REMITIR:
        raise HTTPException(status_code=409, detail="Solo se puede despachar una nota firmada, de «Solo Mesa» o un PDF adjunto confirmado")

    if nota.get("bloque_id"):
        sin_numero = _despachar_bloque(nota["bloque_id"])
    else:
        sin_numero = _exigir_preparado([nota]) or []
        db.marcar_despachada(nota_id)
        if sin_numero:
            db.registrar_evento(nota_id, "Despachada sin número de nota ni de expediente")
    _flash(request, "Despachado correctamente." + (f" {len(sin_numero)} documento(s) sin número de nota (queda registrado en el historial)." if sin_numero else ""))
    return _recargar_pagina()


@app.post("/despacho/despachar")
async def despachar_marcados(request: Request):
    """Despacho del día: marca como despachados todos los documentos y bloques tildados, de una vez (con el PIN,
    como el despacho individual). Así salen del panel una vez enviados o descargados."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    form = await request.form()
    ids = [int(x) for x in form.getlist("nota_id") if str(x).isdigit()]
    bloques = {int(x) for x in form.getlist("bloque_id") if str(x).isdigit()}
    hechos, sin_numero, errores = 0, 0, []
    for nid in ids:
        n = db.obtener_nota(nid)
        if not n or n["estado"] not in db.LISTAS_PARA_REMITIR:
            continue
        if n.get("bloque_id"):
            bloques.add(n["bloque_id"]); continue
        try:
            sn = _exigir_preparado([n]) or []
        except HTTPException as e:
            errores.append(f"«{n['nombre_original']}»: {e.detail}"); continue
        db.marcar_despachada(nid)
        if sn:
            db.registrar_evento(nid, "Despachada sin número de nota ni de expediente"); sin_numero += 1
        db.registrar_evento(nid, "Despachada desde «Despacho del día» junto con otros documentos")
        hechos += 1
    for bid in sorted(bloques):
        try:
            sin_numero += len(_despachar_bloque(bid)); hechos += 1
        except HTTPException as e:
            b = db.obtener_bloque(bid)
            errores.append(f"Bloque «{(b or {}).get('nombre', bid)}»: {e.detail}")
    if not hechos and not errores:
        _flash(request, "⚠ Marcá al menos un documento o bloque firmado.")
    else:
        _flash(request, (f"Despachados: {hechos}." if hechos else "No se despachó nada.")
               + (f" {sin_numero} sin número de nota (queda registrado)." if sin_numero else "")
               + ((" ⚠ " + " ".join(errores[:5])) if errores else ""))
    return RedirectResponse(url="/despacho", status_code=303)


# ---------- Números de nota/expediente y PDF (Mesa de Entrada) ----------
CARPETA_DESPACHO = CARPETA_DATOS / "despacho"


def _datos_despacho(nota, saltear=()):
    """Valores con que se completa el formulario de Mesa: lo ya cargado, o el
    expediente que menciona el texto y el próximo número de nota del año (propuestas, siempre editables)."""
    propuesto = False
    expte = nota.get("nro_expte") or ""
    base = _documento_base(nota)
    if not expte and base and Path(base).is_file():
        try:
            if db.es_pdf(nota):
                from pypdf import PdfReader
                texto_pdf = "\n".join((p.extract_text() or "") for p in PdfReader(base).pages[:200])
                expte = despacho.proponer_expediente(texto_pdf, nota.get("nombre_original") or "")
            else:
                expte = despacho.proponer_expediente(nucleo.texto_de_documento(base), nota.get("nombre_original") or "")
            propuesto = bool(expte)
        except Exception:
            expte = ""
    if not expte:
        expte = despacho.proponer_expediente("", nota.get("nombre_original") or "")
        propuesto = bool(expte)
    nro_propuesto = ""
    lleva_numero = despacho.lleva_numero(nota)
    if not nota.get("nro_nota") and lleva_numero:
        try:
            nro_propuesto = circuito.proponer_texto("NOTA", saltear=saltear)
        except Exception:
            logging.exception("No se pudo proponer el número de nota")
    return {
        "nro_nota": nota.get("nro_nota") or nro_propuesto,
        "nro_propuesto": bool(nro_propuesto),
        "lleva_numero": lleva_numero,
        "nro_expte": expte,
        "expte_propuesto": propuesto,
        "preparado": _despacho_preparado(nota),
        "tiene_pdf": bool(nota.get("ruta_despacho_pdf") and Path(nota["ruta_despacho_pdf"]).is_file()),
    }


templates.env.globals["lleva_numero"] = despacho.lleva_numero


def _documento_base(nota):
    """Sobre qué documento se insertan los números: el firmado; en «Solo Mesa de Entradas» y PDFs adjuntos, el original."""
    if nota.get("ruta_firmada"):
        return nota["ruta_firmada"]
    if nota.get("modalidad") == "SOLO_MESA" or (db.es_pdf(nota) and nota.get("tipo") == "ADJUNTO"):
        return nota.get("ruta_original")
    return None


def _despacho_preparado(nota):
    ruta = nota.get("ruta_despacho_docx")
    if not ((nota.get("nro_nota") or not despacho.lleva_numero(nota)) and ruta and Path(ruta).is_file()):
        return False
    guardada = nota.get("sha256_despacho")
    return not guardada or integridad.sha256_si_existe(ruta) == guardada


templates.env.globals["datos_despacho"] = _datos_despacho


def _exigir_preparado(notas):
    """Antes de despachar. El número de nota y el de expediente son OPCIONALES: se puede despachar sin numerar
    (queda registrado). Lo único que frena es una copia numerada que se generó y después se modificó: hay que regenerarla.
    Devuelve los nombres de los documentos que se despachan sin número."""
    alterados, sin_numero = [], []
    for n in notas:
        if n["estado"] not in db.LISTAS_PARA_REMITIR:
            continue
        ruta = n.get("ruta_despacho_docx")
        if (n.get("nro_nota") or not despacho.lleva_numero(n)) and ruta and Path(ruta).is_file():
            if not _despacho_preparado(n):
                alterados.append(n["nombre_original"])
        elif despacho.lleva_numero(n):
            sin_numero.append(n["nombre_original"])
    if alterados:
        raise HTTPException(
            status_code=409,
            detail="La copia numerada se modificó después de generarse: volvé a generarla antes de despachar. Falta en: "
                   + ", ".join(alterados[:5]) + ("…" if len(alterados) > 5 else ""),
        )
    return sin_numero


def _preparar_despacho(nota, nro_nota, nro_expte):
    """Genera la copia con números (.docx) y el PDF. Devuelve avisos."""
    if db.es_pdf(nota) and nota.get("tipo") == "ADJUNTO":
        if nro_expte:
            with db._conexion() as con:
                con.execute("UPDATE notas SET nro_expte = ? WHERE id = ?", (nro_expte, nota["id"]))
            db.registrar_evento(nota["id"], f"Expediente vinculado (despacho del día): {nro_expte}")
        return []
    solo_mesa = not nota.get("ruta_firmada") and nota.get("modalidad") == "SOLO_MESA"
    ruta_firmada = Path(_documento_base(nota) or "")
    if not ruta_firmada.is_file():
        raise HTTPException(status_code=409, detail="No se encuentra el documento " + ("cargado" if solo_mesa else "firmado") + " en el servidor")
    guardada = nota.get("sha256_original" if solo_mesa else "sha256_firmado")
    if guardada and integridad.sha256_archivo(ruta_firmada) != guardada:
        raise HTTPException(status_code=409, detail="El documento " + ("cargado cambió desde que se cargó" if solo_mesa else "firmado cambió desde que se firmó")
                            + ": no se prepara el despacho.")
    carpeta = CARPETA_DESPACHO / str(nota["id"])
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)  # lo que esté abierto en otro programa queda (no se puede borrar)
    carpeta.mkdir(parents=True, exist_ok=True)
    base = _sanitizar_nombre_archivo(Path(nota["nombre_original"]).stem) + " - despacho"
    ruta_docx = carpeta / f"{base}.docx"
    ruta_pdf = carpeta / f"{base}.pdf"
    if ruta_docx.exists() or ruta_pdf.exists():  # la copia anterior está abierta (en Word u otro programa): va con otro nombre
        marca = datetime.now().strftime("%H%M%S")
        ruta_docx, ruta_pdf = carpeta / f"{base} ({marca}).docx", carpeta / f"{base} ({marca}).pdf"
    # un documento que no es nota no lleva número de nota y no se toca su texto (el expediente queda registrado)
    avisos = despacho.completar_documento(ruta_firmada, ruta_docx, nro_nota, nro_expte if despacho.lleva_numero(nota) else "")
    pdf_ok = None
    try:
        with nucleo._WORD_LOCK:
            if circuito.CONFIG["pdfa"]:
                circuito.convertir_a_pdfa(ruta_docx, ruta_pdf)
            else:
                despacho.convertir_a_pdf(ruta_docx, ruta_pdf)
        pdf_ok = ruta_pdf
    except Exception as e:
        logging.warning(f"PDF de la nota {nota['id']}: {e}")
        avisos.append("El .docx quedó listo, pero no se pudo generar el PDF (se puede reintentar).")
    if pdf_ok:
        rutas_notas_ia.sellar_qr_despacho(nota["id"], nro_nota, pdf_ok, avisos)  # propuesta 51 (optativa)
    db.guardar_despacho_preparado(
        nota["id"], nro_nota, nro_expte, ruta_docx, pdf_ok, integridad.sha256_archivo(ruta_docx),
    )
    return avisos


def _validar_numeros(pares, nombres):
    """pares: [(nota, nro_nota, nro_expte)] → (limpios, errores)."""
    errores, limpios, vistos = [], [], {}
    for nota, nn, ne in pares:
        if not despacho.normalizar_numero(nn):  # sin número de nota (opcional; los informes y proveídos no lo llevan)
            n2 = despacho.normalizar_numero(ne)
            if n2 and not despacho._NUMERO_OK.match(n2):
                errores.append(f"«{nota['nombre_original']}»: el número de expediente tiene caracteres no válidos.")
            limpios.append((nota, "", n2))
            continue
        n1, e1 = despacho.validar_numero(despacho.obtener_numero_nota(nota, nn), "número de nota")
        n2 = despacho.normalizar_numero(ne)
        if n2 and not despacho._NUMERO_OK.match(n2):
            errores.append(f"«{nota['nombre_original']}»: el número de expediente tiene caracteres no válidos.")
        if e1:
            errores.append(f"«{nota['nombre_original']}»: {e1}")
        else:
            otra = db.numero_de_nota_en_uso(n1, excluir_id=nota["id"])
            if otra:
                errores.append(f"«{nota['nombre_original']}»: el número de nota {n1} ya está en uso en la nota #{otra}.")
            if n1.upper() in vistos:
                errores.append(f"«{nota['nombre_original']}»: el número de nota {n1} se repite en este bloque.")
            vistos[n1.upper()] = True
        limpios.append((nota, n1, n2))
    return limpios, errores


def _guardar_numeros(request, notas_pares):
    notas = [n for n, _, _ in notas_pares]
    for n in notas:
        if n["estado"] not in db.LISTAS_PARA_REMITIR:
            raise HTTPException(status_code=409, detail="Solo se cargan números en documentos ya firmados, de «Solo Mesa» o PDFs adjuntos confirmados")
    limpios, errores = _validar_numeros(notas_pares, None)
    if errores:
        _flash(request, "⚠ No se guardó nada. " + " ".join(errores))
        return False
    avisos = []
    for nota, nn, ne in limpios:
        try:
            avisos += _preparar_despacho(nota, nn, ne)
        except PermissionError:
            logging.exception(f"Despacho de la nota {nota['id']}: archivo bloqueado")
            avisos.append(f"«{nota['nombre_original']}»: no se pudo escribir la copia (¿está abierta en Word?). Cerrala y volvé a generarla.")
        try:
            if ne and expedientes.estado()["cantidad"] and not expedientes.existe(ne):
                avisos.append(f"El expediente {ne} no figura en las planillas de expedientes: verificá el número.")
        except Exception:
            pass
    quien = (db.USUARIO_ACTUAL.get() or {}).get("nombre", "")
    for nota, nn, ne in limpios:
        if not nn:
            continue
        try:
            circuito.registrar_uso("NOTA", nn, quien=quien)
        except Exception:
            logging.exception("No se pudo avanzar la numeración")
    _flash(request, "Números cargados y documentos generados." + ((" " + " ".join(sorted(set(avisos)))) if avisos else ""))
    return True


def _propuestas_lote(notas):
    """Números de nota propuestos para varias notas a la vez (correlativos, sin repetir)."""
    salida, reservados = {}, []
    for n in notas:
        if n.get("nro_nota") or not despacho.lleva_numero(n):
            continue
        num = circuito.proponer("NOTA", saltear=reservados)
        if num is None:
            break  # sin base del registro: no se proponen números
        reservados.append(num)
        salida[n["id"]] = circuito.formatear("NOTA", num, date.today().year)
    return salida


templates.env.globals["propuestas_lote"] = _propuestas_lote


# ---------- Plazos, comentarios, lote, atajos, biblioteca, ausencias ----------
templates.env.globals["semaforo"] = lambda n: plazos.semaforo(n)
templates.env.globals["estado_respuesta"] = plazos.estado_respuesta
templates.env.globals["envio_directo"] = lambda u: _envio_directo(u)
import control_cambios  # noqa: E402  (correcciones como control de cambios, con totales por tipo)
templates.env.globals["control_cambios"] = control_cambios.vista
templates.env.globals["clase_tramo"] = lambda q, a: control_cambios.clasificar_tramo(control_cambios._TOKEN.findall(q or ""), control_cambios._TOKEN.findall(a or ""))
templates.env.globals["CLASES_CAMBIO"] = control_cambios.CLASES
templates.env.globals["fines_de_semana_ia"] = automatico.fines_de_semana_activo
import tipos_documentales  # noqa: E402  (Ola 6, 131)
import trazabilidad_ia  # noqa: E402  (Ola 6, 137)
import referencia_ia  # noqa: E402  (documentos firmados de referencia para la IA)
referencia_ia.CARPETA = CARPETA_DATOS / "referencia_ia"
referencia_ia.configurar(_CONFIG)


def _parecidos_de(nota):
    try:
        return referencia_ia.sugerir_carpeta(db.texto_indexado(nota["id"]) or "")
    except Exception:
        logging.exception("No se pudieron buscar documentos de referencia parecidos")
        return None


templates.env.globals["referencia_parecida"] = _parecidos_de
templates.env.globals["tipo_documental_de"] = lambda n: tipos_documentales.de_nota(n, db.texto_indexado(n["id"]))
templates.env.globals["TIPOS_DOCUMENTALES"] = tipos_documentales.TIPOS
templates.env.globals["ia_registro_de"] = trazabilidad_ia.de_nota
templates.env.globals["ia_avisos_contexto"] = trazabilidad_ia.avisos_de_contexto  # propuesta 20
templates.env.globals["ia_cambios_version"] = nucleo.cambios_de_version_modelos  # propuesta 27
templates.env.globals["comentarios_de"] = lambda n: db.listar_comentarios(n["id"])
templates.env.globals["duplicados_de"] = lambda n: db.buscar_duplicados(n["id"])
templates.env.globals["info_deshacer"] = lambda request: request.session.get("deshacer")

# Reforma 28: el historial de la nota como línea de tiempo (quién, qué y cuándo).
ETIQ_ESTADO_NOTA = {
    "BORRADOR": "Borrador guardado", "SUBIDA": "Subida", "PROCESANDO": "En corrección automática",
    "EN_REVISION": "En revisión", "PROCESADA": "Corregida, a la firma", "ERROR": "Con error", "FIRMADA": "Firmada",
    "PARA_REMITIR": "Solo Mesa de Entradas: para remitir",
    "DESPACHADA": "Despachada", "RECHAZADA": "Rechazada", "REGISTRO": "Registro",
}
_TIPO_EVENTO_NOTA = {"FIRMADA": "firma", "DESPACHADA": "ok", "PROCESADA": "ok", "ERROR": "alerta", "RECHAZADA": "alerta"}


def _eventos_nota(nota):
    eventos = []
    for h in nota.get("historial") or []:
        est = h.get("estado")
        texto = ETIQ_ESTADO_NOTA.get(est, est)
        detalle = h.get("comentario") or ""
        if est == "REGISTRO" and detalle:
            texto, detalle = detalle, ""
        eventos.append({"texto": texto, "detalle": detalle, "fecha": h.get("fecha"), "quien": h.get("usuario"),
                        "tipo": _TIPO_EVENTO_NOTA.get(est, "")})
    return eventos


templates.env.globals["eventos_nota"] = _eventos_nota

# Reforma 31: semáforo de antigüedad en la etapa actual (cuántos días lleva la nota sin moverse).
ESTADOS_CON_ANTIGUEDAD = ("SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "ERROR", "FIRMADA", "PARA_REMITIR", "A_CONFIRMAR")


try:
    DIAS_ETAPA_AMBAR = _CONFIG.getint("plazos", "dias_etapa_ambar", fallback=3)
    DIAS_ETAPA_ROJO = _CONFIG.getint("plazos", "dias_etapa_rojo", fallback=6)
except ValueError:
    DIAS_ETAPA_AMBAR, DIAS_ETAPA_ROJO = 3, 6


def _antiguedad_etapa(nota, hoy=None):
    if nota.get("estado") not in ESTADOS_CON_ANTIGUEDAD:
        return None
    desde = nota.get("en_estado_desde") or nota.get("actualizado_en")
    if not desde:
        return None
    try:
        d = datetime.fromisoformat(str(desde).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    dias = ((hoy or datetime.now(timezone.utc)) - d).days
    ambar, rojo = DIAS_ETAPA_AMBAR, DIAS_ETAPA_ROJO
    nivel = "rojo" if dias >= rojo else "ambar" if dias >= ambar else "verde"
    texto = "hoy" if dias <= 0 else ("1 día" if dias == 1 else f"{dias} días")
    return {"dias": max(dias, 0), "nivel": nivel, "texto": texto}


templates.env.globals["antiguedad_etapa"] = _antiguedad_etapa


def _texto_para_glosario(nota):
    for col in ("ruta_base", "ruta_resultado", "ruta_original"):
        ruta = nota.get(col)
        if ruta and Path(ruta).is_file() and str(ruta).lower().endswith(".docx"):
            try:
                return nucleo.texto_de_documento(ruta)
            except Exception:
                return ""
    return ""


def _glosario_en(nota):
    """Términos del glosario que aparecen en el texto (con la forma preferida)."""
    terminos = db.listar_terminos()
    if not terminos:
        return []
    texto = _texto_para_glosario(nota)
    hallados = []
    for t in terminos:
        if re.search(r"(?<!\w)" + re.escape(t["termino"]) + r"(?!\w)", texto, re.IGNORECASE):
            hallados.append(t)
    return hallados


templates.env.globals["glosario_en"] = _glosario_en

def _marcar_abribles(items, id_de):
    """Antecedentes y notas parecidas pueden ser de otro equipo: se muestran igual (sirven de contexto), pero solo con
    enlace las que quien mira puede abrir. Las demás llevan el equipo, para saber a quién pedirlas."""
    usuario = db.USUARIO_ACTUAL.get()
    equipos = {e["id"]: e["nombre"] for e in db.listar_equipos()}
    for it in items:
        otra = db.obtener_nota(id_de(it)) if id_de(it) else None
        it["abrible"] = bool(otra) and (usuario is None or _puede_ver_nota(usuario, otra))
        it["equipo_nombre"] = equipos.get((otra or {}).get("equipo_id")) or ""
    return items


def _asistentes_de(nota):
    """Panel «Asistentes» de la nota: todo lo que proponen los agentes, para que una persona decida."""
    texto = db.texto_indexado(nota["id"]) or _texto_para_glosario(nota)
    ents = [e for e in db.entidades_de(nota["id"]) if e["tipo"] != "NADA"]
    plazo = None
    if nota.get("plazo_respuesta"):
        try:
            plazo = json.loads(nota["plazo_respuesta"])
        except ValueError:
            plazo = None
    return {
        "resumen": [l for l in (nota.get("resumen") or "").split("\n") if l.strip()],
        "resumen_origen": nota.get("resumen_origen"),
        "plazos": db.sugerencias_de(nota["id"], "PLAZO"),
        "plazo": plazo, "respuesta_vence": nota.get("respuesta_vence"),
        "casi_iguales": _marcar_abribles(db.sugerencias_de(nota["id"], "DUPLICADO"), lambda sg: (sg.get("contenido") or {}).get("nota_id")),
        "reiteraciones": db.sugerencias_de(nota["id"], "REITERACION"),
        "antecedentes": _marcar_abribles(db.antecedentes(nota["id"]), lambda a: a["id"]),
        "entidades": ents,
        "destino": agentes.destinatario_sugerido(texto) if texto else None,
        "estilo": agentes.sugerencias_estilo(texto) if texto else [],
    }


templates.env.globals["asistentes_de"] = _asistentes_de
templates.env.globals["AGENTES"] = agentes.AGENTES
templates.env.globals["alertas_abiertas"] = db.alertas_abiertas


@app.post("/sugerencia/{sug_id}/{accion}")
def resolver_sugerencia_ia(request: Request, sug_id: int, accion: str):
    """Aceptar o descartar una sugerencia de un agente. Lo que se acepte recién ahí cambia algo."""
    usuario = usuario_actual(request)
    sug = db.obtener_sugerencia(sug_id)
    if sug is None or accion not in ("aceptar", "descartar"):
        raise HTTPException(status_code=404, detail="Sugerencia no encontrada")
    nota = db.obtener_nota(sug["nota_id"]) if sug.get("nota_id") else None
    if nota is None or not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if sug["agente"] == "PLAZO" and accion == "aceptar":
        if not (_puede_gestionar_nota(usuario, nota) or auth.es_admin(usuario) or usuario["rol"] in auth.ROLES_MESA):
            raise HTTPException(status_code=403, detail="No podés fijar el plazo de esta nota")
        db.fijar_plazo_respuesta(nota["id"], sug["contenido"])
        try:
            dest = despacho.proponer_destinatario(db.texto_indexado(nota["id"]) or "")
            if dest:
                db.recordar_plazo_destinatario(dest, sug["contenido"])
        except Exception:
            logging.exception("No se pudo recordar el plazo del destinatario")
        for otra in db.sugerencias_de(nota["id"], "PLAZO"):
            if otra["id"] != sug_id:
                db.resolver_sugerencia(otra["id"], "DESCARTADA", usuario["nombre"])
        _flash(request, f"Plazo de respuesta fijado: {sug['contenido'].get('texto')}. Se cuenta desde el despacho y se avisa cuando vence.")
    elif sug["agente"] == "DUPLICADO":
        c = sug["contenido"]
        if accion == "aceptar":
            db.registrar_evento(nota["id"], f"Marcada como repetida de «{c.get('nombre')}» (#{c.get('nota_id')}) por {usuario['nombre']}")
            _flash(request, "Quedó registrado que es una nota repetida. Si no corresponde enviarla, podés rechazarla o pasarla a borrador.")
        else:
            _flash(request, "Listo: no es la misma nota.")
    db.resolver_sugerencia(sug_id, "ACEPTADA" if accion == "aceptar" else "DESCARTADA", usuario["nombre"])
    return RedirectResponse(url=f"/nota/{nota['id']}#asistentes", status_code=303)


@app.post("/nota/{nota_id}/plazo-respuesta/quitar")
def quitar_plazo_respuesta(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None or not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not (_puede_gestionar_nota(usuario, nota) or auth.es_admin(usuario) or usuario["rol"] in auth.ROLES_MESA):
        raise HTTPException(status_code=403, detail="No podés cambiar el plazo de esta nota")
    db.fijar_plazo_respuesta(nota_id, None)
    return RedirectResponse(url=f"/nota/{nota_id}#asistentes", status_code=303)


@app.get("/nota/{nota_id}/proveido", response_class=HTMLResponse)
def ver_proveido(request: Request, nota_id: int, medio: str = "CORREO"):
    """Proveído PROPUESTO para dar curso a la nota (por correo o en mano a la persona detenida). Es un borrador:
    se edita, se descarga y se firma por el circuito habitual. La decisión es siempre de una persona."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None or not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    medio = "MANO" if medio == "MANO" else "CORREO"
    prop = agentes.proponer_proveido(nota_id, medio, usar_ia=nucleo.USAR_IA)
    return templates.TemplateResponse("proveido.html", {"request": request, "usuario": usuario, "nota": nota, "prop": prop, "medio": medio,
                                                        "numero": circuito.proponer_texto("PROVEIDO")})


@app.post("/nota/{nota_id}/proveido")
async def usar_proveido(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None or not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    form = await request.form()
    texto = str(form.get("texto") or "").strip()
    origen = "ia" if form.get("origen") == "ia" else "reglas"
    if form.get("accion") == "descartar" or not texto:
        db.registrar_uso_agente(nota_id, "PROVEIDO", "DESCARTADA", usuario["nombre"], {"medio": form.get("medio")}, origen)
        _flash(request, "Propuesta de proveído descartada.")
        return RedirectResponse(url=f"/nota/{nota_id}#asistentes", status_code=303)
    numero = despacho.normalizar_numero(form.get("numero") or "")
    if numero:
        if db.proveido_en_uso(numero):
            _flash(request, f"⚠ El proveído N° {numero} ya se usó. Elegí otro número.")
            return RedirectResponse(url=f"/nota/{nota_id}/proveido?medio={form.get('medio') or 'CORREO'}", status_code=303)
        db.registrar_proveido_numerado(nota_id, numero, form.get("medio"), usuario["nombre"])
        circuito.registrar_uso("PROVEIDO", numero, quien=usuario["nombre"])
        texto = f"PROVEÍDO N° {numero}\n\n" + texto
    import tempfile
    ruta = Path(tempfile.mkdtemp()) / f"Proveido {numero.replace('/', '-') + ' - ' if numero else '- '}{Path(nota['nombre_original']).stem}.docx"
    agentes.proveido_docx(texto, ruta)
    db.registrar_uso_agente(nota_id, "PROVEIDO", "ACEPTADA", usuario["nombre"], {"medio": form.get("medio"), "editado": form.get("editado") == "1"}, origen)
    db.registrar_evento(nota_id, f"Se descargó un proveído propuesto ({'en mano' if form.get('medio') == 'MANO' else 'por correo'}) — {usuario['nombre']}")
    return FileResponse(str(ruta), filename=ruta.name,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")



@app.post("/nota/{nota_id}/vence")
def fijar_vence(request: Request, nota_id: int, fecha: str = Form("")):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para cambiar el plazo de esta nota")
    f = _fecha_valida(fecha.strip()) if fecha.strip() else None
    if fecha.strip() and not f:
        _flash(request, "⚠ La fecha no es válida.")
    else:
        db.fijar_vencimiento(nota_id, f)
        _flash(request, "Plazo guardado." if f else "Plazo restablecido (según la prioridad).")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


_CITA_MENCION = re.compile(r"@([A-Za-z0-9_.\-]{2,40})")


@app.post("/nota/{nota_id}/comentarios")
def agregar_comentario(request: Request, nota_id: int, texto: str = Form("")):
    """Comentarios internos entre quienes ven la nota; @usuario avisa a esa persona."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para comentar esta nota")
    texto = texto.strip()[:1500]
    if not texto:
        _flash(request, "⚠ Escribí el comentario.")
        return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)
    db.agregar_comentario(nota_id, texto)
    db.registrar_evento(nota_id, f"Comentario de {usuario['nombre']}")
    destinos, sin_acceso = set(), []
    for m in set(_CITA_MENCION.findall(texto)):
        u = db.obtener_usuario_por_nombre(m.lower())
        if not u:
            continue
        if _puede_ver_nota(u, nota):
            destinos.add(u["id"])
        else:
            sin_acceso.append(m)
    if nota.get("usuario_id") and nota["usuario_id"] != usuario["id"]:
        destinos.add(nota["usuario_id"])
    if destinos:
        db.notificar_usuarios(list(destinos), f"{usuario['nombre']} comentó en «{nota['nombre_original']}»: {texto[:90]}", f"/nota/{nota_id}")
    _flash(request, "Comentario agregado." + (f" No tienen acceso a esta nota: {', '.join('@' + x for x in sin_acceso)}." if sin_acceso else ""))
    return RedirectResponse(url=f"/nota/{nota_id}#comentarios", status_code=303)


@app.post("/lote/prioridad")
async def lote_prioridad(request: Request):
    """Cambia la prioridad de varios documentos a la vez, con «Deshacer»."""
    usuario = usuario_actual(request)
    form = await request.form()
    prioridad = (form.get("prioridad") or "").strip()
    if prioridad not in auth.PRIORIDADES:
        _flash(request, "⚠ Elegí una prioridad.")
        return RedirectResponse(url="/", status_code=303)
    previas = {}
    for raw in form.getlist("nota_ids"):
        if not str(raw).isdigit():
            continue
        nota = db.obtener_nota(int(raw))
        if nota and _puede_gestionar_nota(usuario, nota) and nota["estado"] in ESTADOS_ACTIVOS and nota["prioridad"] != prioridad:
            previas[str(nota["id"])] = nota["prioridad"]
            db.actualizar_prioridad(nota["id"], prioridad,
                f"Prioridad: {auth.ETIQUETAS_PRIORIDAD[nota['prioridad']]} → {auth.ETIQUETAS_PRIORIDAD[prioridad]} ({usuario['nombre']}, en lote)")
    if previas:
        request.session["deshacer"] = {"texto": f"Prioridad {auth.ETIQUETAS_PRIORIDAD[prioridad]} aplicada a {len(previas)} documento(s)", "previas": previas}
        _flash(request, f"Prioridad cambiada en {len(previas)} documento(s).")
    else:
        _flash(request, "No había nada que cambiar.")
    return RedirectResponse(url="/", status_code=303)


# Reforma 35: filtros guardados como botones (se guardan en las preferencias de la persona).
_CLAVES_FILTRO = ("f_nombre", "f_tipo", "f_etapa", "f_fecha", "f_prioridad", "f_tema", "f_tarea", "vista", "mostrar_despachadas", "agrupar")
MAX_FILTROS_GUARDADOS = 12


def _limpiar_qs_filtro(qs):
    from urllib.parse import parse_qsl, urlencode
    pares = [(k, v) for k, v in parse_qsl(qs or "", keep_blank_values=False) if k in _CLAVES_FILTRO and v and v != "false"]
    return urlencode(pares)


@app.post("/filtros/guardar")
def guardar_filtro(request: Request, nombre: str = Form(""), qs: str = Form("")):
    usuario = usuario_actual(request)
    nombre = " ".join(nombre.split())[:40]
    limpio = _limpiar_qs_filtro(qs)
    if not nombre or not limpio:
        _flash(request, "⚠ Poné un nombre y aplicá al menos un filtro antes de guardarlo.")
        return RedirectResponse(url="/?" + limpio if limpio else "/", status_code=303)
    try:
        pref = json.loads(usuario.get("pref_notas") or "{}") or {}
    except (TypeError, ValueError):
        pref = {}
    filtros = [f for f in (pref.get("filtros") or []) if f.get("nombre") != nombre]
    filtros.append({"nombre": nombre, "qs": limpio})
    pref["filtros"] = filtros[-MAX_FILTROS_GUARDADOS:]
    db.guardar_pref_notas(usuario["id"], pref)
    _flash(request, f"Filtro «{nombre}» guardado.")
    return RedirectResponse(url="/?" + limpio, status_code=303)


@app.post("/filtros/borrar")
def borrar_filtro(request: Request, nombre: str = Form("")):
    usuario = usuario_actual(request)
    try:
        pref = json.loads(usuario.get("pref_notas") or "{}") or {}
    except (TypeError, ValueError):
        pref = {}
    pref["filtros"] = [f for f in (pref.get("filtros") or []) if f.get("nombre") != nombre]
    db.guardar_pref_notas(usuario["id"], pref)
    _flash(request, f"Filtro «{nombre}» quitado.")
    return RedirectResponse(url="/", status_code=303)


@app.post("/deshacer")
def deshacer(request: Request, descartar: str = Form(""), volver: str = Form("")):
    usuario = usuario_actual(request)
    info = request.session.pop("deshacer", None)
    if info and not descartar:
        vueltos = 0
        for nid, previa in info.get("previas", {}).items():
            nota = db.obtener_nota(int(nid))
            if nota and previa in auth.PRIORIDADES and _puede_gestionar_nota(usuario, nota) and nota["estado"] in ESTADOS_ACTIVOS:
                db.actualizar_prioridad(nota["id"], previa, f"Prioridad vuelve a {auth.ETIQUETAS_PRIORIDAD[previa]} (deshacer, {usuario['nombre']})")
                vueltos += 1
        _flash(request, f"Se deshizo el cambio en {vueltos} documento(s).")
    volver = volver if isinstance(volver, str) else ""
    if not volver.startswith("/") or volver.startswith("//") or "\\" in volver:
        volver = "/"
    return RedirectResponse(url=volver, status_code=303)


@app.get("/nota/{nota_id}/siguiente")
def nota_siguiente(request: Request, nota_id: int, sentido: int = 1):
    """Atajos de revisión (J/K): salta a la nota anterior/siguiente en el mismo
    estado, en el orden de urgencia en que se trabajan."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None or not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    todas = db.listar_notas()
    _anotar_prioridad_efectiva(todas)
    cola = [n for n in _filtrar_por_visibilidad(todas, usuario) if n["estado"] == nota["estado"]]
    rango = {"URGENTE": 0, "PRIORITARIA": 1, "NORMAL": 2}
    cola.sort(key=lambda n: (rango.get(n["prioridad_efectiva"], 2), n["id"]))
    ids = [n["id"] for n in cola]
    if nota_id not in ids:
        return RedirectResponse(url="/", status_code=303)
    i = ids.index(nota_id) + (1 if sentido >= 0 else -1)
    if 0 <= i < len(ids):
        return RedirectResponse(url=f"/nota/{ids[i]}", status_code=303)
    _flash(request, "No hay más documentos en este estado." if sentido >= 0 else "Es el primero de la lista.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.get("/biblioteca", response_class=HTMLResponse)
def biblioteca(request: Request):
    usuario = usuario_actual(request)
    return templates.TemplateResponse("biblioteca.html", {
        "request": request, "usuario": usuario, "terminos": db.listar_terminos(), "modelos": db.listar_modelos_nota()})


@app.post("/biblioteca/terminos")
def biblioteca_termino(request: Request, termino: str = Form(""), preferido: str = Form(""), nota: str = Form("")):
    _exigir_rol(usuario_actual(request))
    if termino.strip():
        db.agregar_termino(termino.strip()[:80], preferido.strip()[:80], nota.strip()[:200])
        _flash(request, f"Término «{termino.strip()[:80]}» agregado correctamente.")
    else:
        _flash(request, "⚠ Escribí el término para agregarlo.")
    return RedirectResponse(url="/biblioteca", status_code=303)


@app.post("/biblioteca/terminos/{termino_id}/borrar")
def biblioteca_borrar_termino(request: Request, termino_id: int):
    _exigir_rol(usuario_actual(request))
    db.borrar_termino(termino_id)
    _flash(request, "Término quitado.")
    return RedirectResponse(url="/biblioteca", status_code=303)


CARPETA_MODELOS = CARPETA_DATOS / "modelos"


@app.post("/biblioteca/modelos")
async def biblioteca_subir_modelo(request: Request, nombre: str = Form(""), descripcion: str = Form(""), archivo: UploadFile = File(...)):
    _exigir_rol(usuario_actual(request))
    sufijo = Path(archivo.filename or "").suffix.lower()
    if sufijo not in (".docx", ".doc") or not nombre.strip():
        _flash(request, "⚠ Indicá un nombre y un archivo Word (.doc/.docx).")
        return RedirectResponse(url="/biblioteca", status_code=303)
    CARPETA_MODELOS.mkdir(parents=True, exist_ok=True)
    destino = CARPETA_MODELOS / f"{uuid.uuid4().hex}{sufijo}"
    with destino.open("wb") as f:
        shutil.copyfileobj(archivo.file, f)
    if destino.stat().st_size > MAX_BYTES_SUBIDA:
        destino.unlink(missing_ok=True)
        _flash(request, "⚠ El archivo es demasiado grande.")
    else:
        db.agregar_modelo_nota(nombre.strip()[:120], descripcion.strip()[:300], destino)
        _flash(request, "Modelo agregado.")
    return RedirectResponse(url="/biblioteca", status_code=303)


@app.get("/biblioteca/modelos/{modelo_id}/descargar")
def biblioteca_descargar_modelo(request: Request, modelo_id: int):
    usuario_actual(request)
    m = db.obtener_modelo_nota(modelo_id)
    if not m or not Path(m["archivo"]).is_file():
        raise HTTPException(status_code=404, detail="Modelo no encontrado")
    return FileResponse(m["archivo"], filename=_sanitizar_nombre_archivo(m["nombre"]) + Path(m["archivo"]).suffix)


@app.post("/biblioteca/modelos/{modelo_id}/borrar")
def biblioteca_borrar_modelo(request: Request, modelo_id: int):
    _exigir_rol(usuario_actual(request))
    m = db.obtener_modelo_nota(modelo_id)
    if m:
        Path(m["archivo"]).unlink(missing_ok=True)
        db.borrar_modelo_nota(modelo_id)
    return RedirectResponse(url="/biblioteca", status_code=303)


@app.get("/ausencias", response_class=HTMLResponse)
def pagina_ausencias(request: Request):
    usuario = usuario_actual(request)
    todos = es_dir = auth.es_admin(usuario)
    candidatos = [u for u in db.listar_usuarios() if u["activo"] and u["id"] != usuario["id"]
                  and (es_dir or u["rol"] == usuario["rol"])]
    return templates.TemplateResponse("ausencias.html", {
        "request": request, "usuario": usuario, "candidatos": candidatos, "hoy": _hoy_local(),
        "ausencias": db.listar_ausencias(None if es_dir else usuario["id"]), "es_dir": es_dir,
        "vigente": db.ausencia_vigente(usuario["id"]),
    })


@app.post("/ausencias")
def crear_ausencia(request: Request, desde: str = Form(""), hasta: str = Form(""), delegado_id: int = Form(0)):
    usuario = usuario_actual(request)
    d, h = _fecha_valida(desde), _fecha_valida(hasta)
    deleg = db.obtener_usuario(delegado_id)
    if not d or not h or h < d:
        _flash(request, "⚠ Revisá las fechas.")
    elif not deleg or deleg["id"] == usuario["id"] or not deleg["activo"] or (
            not auth.es_admin(usuario) and deleg["rol"] != usuario["rol"]):
        _flash(request, "⚠ Elegí a alguien de tu mismo rol para que te reemplace.")
    else:
        db.crear_ausencia(usuario["id"], d, h, deleg["id"])
        _flash(request, f"Ausencia registrada: {deleg['nombre']} recibirá tus avisos.")
    return RedirectResponse(url="/ausencias", status_code=303)


@app.post("/ausencias/{ausencia_id}/borrar")
def borrar_ausencia(request: Request, ausencia_id: int):
    usuario = usuario_actual(request)
    a = next((x for x in db.listar_ausencias() if x["id"] == ausencia_id), None)
    if a and (auth.es_admin(usuario) or a["usuario_id"] == usuario["id"]):
        db.borrar_ausencia(ausencia_id)
        _flash(request, "Ausencia quitada.")
    return RedirectResponse(url="/ausencias", status_code=303)


# ---------- Envío (correo / en mano) y agenda de contactos ----------
import re as _re
_CORREO_OK = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _datos_envio(nota):
    """Destinatario propuesto desde el proveído + agenda + envíos ya registrados."""
    contactos = db.listar_contactos()
    propuesto, contacto = "", None
    if nota.get("ruta_firmada") and Path(nota["ruta_firmada"]).is_file():
        try:
            propuesto = despacho.proponer_destinatario(nucleo.texto_de_documento(nota["ruta_firmada"]))
        except Exception:
            propuesto = ""
        contacto = despacho.mejor_contacto(propuesto, contactos) if propuesto else None
    unidad = db.obtener_unidad(nota["unidad_id"]) if nota.get("unidad_id") else None
    if unidad is None and propuesto:
        cand = db.proponer_unidades(propuesto, 1)
        unidad = cand[0] if cand else None
    return {"propuesto": propuesto, "contacto": contacto, "contactos": contactos, "unidad": unidad,
            "unidades": db.listar_unidades(), "envios": db.listar_envios(nota["id"])}


templates.env.globals["datos_envio"] = _datos_envio


@app.get("/nota/{nota_id}/borrador-correo")
def borrador_correo(request: Request, nota_id: int, destinatario: str = "", correo: str = ""):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] not in ("FIRMADA", "PARA_REMITIR", "DESPACHADA"):
        raise HTTPException(status_code=409, detail="La nota todavía no está firmada")
    if correo and not _CORREO_OK.match(correo.strip()):
        raise HTTPException(status_code=422, detail="El correo no es válido")
    adjuntos = []
    for col in ("ruta_despacho_pdf", "ruta_despacho_docx"):
        if nota.get(col) and Path(nota[col]).is_file():
            adjuntos.append((nota[col], Path(nota[col]).name))
            break
    if not adjuntos:
        raise HTTPException(status_code=409, detail="Primero cargá los números y generá el documento")
    asunto = f"Nota N° {nota['nro_nota']}" + (f" - Expte. N° {nota['nro_expte']}" if nota.get("nro_expte") else "")
    cuerpo = f"{('Estimado/a ' + destinatario + ':') if destinatario else 'Buenos días:'}\n\nSe remite adjunta la {asunto.split(' - ')[0].lower()}.\n\nSaludos cordiales."
    datos = despacho.armar_borrador_eml(destinatario.strip(), correo.strip(), asunto, cuerpo, adjuntos)
    db.registrar_acceso(nota_id, "DESCARGA", "borrador de correo")
    nombre = _sanitizar_nombre_archivo(asunto) + ".eml"
    return Response(content=datos, media_type="message/rfc822",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})


@app.post("/nota/{nota_id}/envio")
def registrar_envio(request: Request, nota_id: int, medio: str = Form("CORREO"), destinatario: str = Form(""),
                    correo: str = Form(""), observacion: str = Form("")):
    """Confirmación humana: queda registrado que alguien envió/entregó el documento."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota["estado"] not in ("FIRMADA", "PARA_REMITIR", "DESPACHADA"):
        raise HTTPException(status_code=409, detail="La nota todavía no está firmada")
    medio = "MANO" if medio == "MANO" else "CORREO"
    destinatario, correo = destinatario.strip(), correo.strip()
    if not destinatario:
        _flash(request, "⚠ Indicá el destinatario.")
    elif medio == "CORREO" and not _CORREO_OK.match(correo):
        _flash(request, "⚠ El correo no es válido: no se registró el envío.")
    else:
        db.registrar_envio(nota_id, medio, destinatario, correo if medio == "CORREO" else None, observacion.strip() or None)
        _flash(request, "Envío registrado.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


# ---------- Expedientes (planillas Excel, solo lectura) ----------
ROLES_EXPEDIENTES = (auth.ROL_COORDINADOR, *auth.ROLES_MESA)


@app.get("/actividad", response_class=HTMLResponse)
def pagina_actividad(request: Request, desde: str = None, hasta: str = None):
    """Ingresos, horarios y acciones por usuario y día (solo Dirección)."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    desde, hasta = _fecha_valida(desde), _fecha_valida(hasta)
    datos = actividad.generar(desde, hasta)
    return templates.TemplateResponse("actividad.html", {
        "request": request, "usuario": usuario, **datos, "accesos": db.listar_accesos(30),
    })


@app.get("/actividad.csv")
def actividad_csv(request: Request, desde: str = None, hasta: str = None):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    datos = actividad.generar(_fecha_valida(desde), _fecha_valida(hasta))
    buffer = io.StringIO()
    w = csv.writer(buffer, delimiter=";")
    w.writerow(["Día", "Usuario", "Ingresos", "Primer movimiento", "Último movimiento", "Minutos estimados", "Accesos a documentos", "Acciones"])
    for f in datos["filas"]:
        w.writerow([f["dia"], f["usuario"], f["ingresos"], f["primero"], f["ultimo"], f["minutos"], f["accesos"], f["acciones"]])
    return Response(content="\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=actividad_dgpdh.csv"})


@app.get("/expedientes", response_class=HTMLResponse)
def pagina_expedientes(request: Request, q: str = "", nota: int = None, lpu: str = "", dni: str = "", expediente: str = "", nombre: str = ""):
    """Búsqueda por campos (LPU, DNI, expediente, nombre y apellido), combinables. `q` queda por compatibilidad
    con los enlaces viejos («Ver expediente» desde una nota): se toma como número de expediente."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *ROLES_EXPEDIENTES)
    filtros = {"lpu": lpu, "dni": dni, "expediente": expediente, "nombre": nombre}
    if q.strip() and not any(str(v).strip() for v in filtros.values()):
        filtros["expediente"] = q
    filtros = {k: str(v).strip()[:120] for k, v in filtros.items()}
    resultados, avisos, error = [], [], None
    buscado = any(filtros.values())
    if buscado:
        try:
            resultados, avisos = expedientes.buscar_campos(filtros)
        except Exception as e:
            error = str(e)
        db.registrar_consulta_expediente(expedientes.describir_filtros(filtros), len(resultados), nota_id=nota)
    return _pantalla_expedientes(request, usuario, filtros=filtros, buscado=buscado, resultados=resultados, avisos=avisos,
                                 error=error, nota_id=nota)


def _pantalla_expedientes(request, usuario, filtros=None, buscado=False, resultados=None, avisos=None, error=None, nota_id=None,
                          importacion=None, status_code=200):
    return templates.TemplateResponse("expedientes.html", {
        "request": request, "usuario": usuario, "filtros": filtros or {k: "" for k in expedientes.CAMPOS},
        "campos": expedientes.CAMPOS, "buscado": buscado, "resultados": resultados or [], "avisos": avisos or [], "error": error,
        "estado": expedientes.estado(), "nota_id": nota_id, "busquedas": _busquedas_expedientes(usuario),
        "importacion": importacion, "puede_importar": auth.es_admin(usuario),
    }, status_code=status_code)


@app.post("/expedientes/importar", response_class=HTMLResponse)
async def importar_planilla_expedientes(request: Request, archivo: UploadFile = File(...)):
    """Mejoras nov. 2026: la Dirección importa una planilla (.xlsx/.xls/.csv) para que se pueda consultar en Expedientes.
    Se guarda en datos/expedientes_importados (nunca en la carpeta configurada de solo lectura)."""
    import tempfile
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *ROLES_EXPEDIENTES)
    if not auth.es_admin(usuario):
        raise HTTPException(status_code=403, detail="Solo la Dirección puede importar planillas de expedientes")
    nombre = Path(archivo.filename or "").name
    sufijo = Path(nombre).suffix.lower()
    ruta_tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=sufijo, delete=False) as t:
            ruta_tmp = Path(t.name)
            leidos = 0
            while True:
                bloque = await archivo.read(1024 * 1024)
                if not bloque:
                    break
                leidos += len(bloque)
                if leidos > expedientes.MAX_BYTES_IMPORTACION:
                    raise ValueError(f"El archivo supera los {expedientes.MAX_BYTES_IMPORTACION // (1024 * 1024)} MB.")
                t.write(bloque)
        resultado = expedientes.importar_planilla(ruta_tmp, nombre)
        logging.info(f"Expedientes: {usuario['nombre']} importó «{resultado['archivo']}» ({resultado['registros']} registros)")
        importacion = {"ok": True, **resultado}
        codigo = 200
    except ValueError as e:
        importacion = {"ok": False, "archivo": nombre, "mensaje": str(e)}
        codigo = 400
    finally:
        if ruta_tmp:
            try:
                ruta_tmp.unlink()
            except OSError:
                pass
    return _pantalla_expedientes(request, usuario, importacion=importacion, status_code=codigo)


# ---------- Equipo y asesor responsables, editables desde la nota (con auditoría) ----------
ROLES_RESPONSABLES = (auth.ROL_ASESOR, auth.ROL_COORDINADOR)


def _opciones_asignacion(usuario, nota):
    """Qué puede reasignar cada quien: la Dirección cambia equipo y asesor; la Coordinación, el asesor dentro de su equipo.
    Devuelve None si no puede reasignar."""
    if not usuario or not nota:
        return None
    activos = [u for u in db.listar_usuarios() if u["activo"] and u["rol"] in ROLES_RESPONSABLES]
    if auth.es_admin(usuario):
        return {"equipos": db.listar_equipos(), "asesores": sorted(activos, key=lambda u: u["nombre"].lower()), "cambia_equipo": True}
    if usuario["rol"] == auth.ROL_COORDINADOR and nota["equipo_id"] == usuario.get("equipo_id"):
        return {"equipos": None, "asesores": sorted((u for u in activos if u.get("equipo_id") == nota["equipo_id"]), key=lambda u: u["nombre"].lower()),
                "cambia_equipo": False}
    return None


templates.env.globals["opciones_asignacion"] = _opciones_asignacion


@app.post("/nota/{nota_id}/asignacion")
def reasignar_nota(request: Request, nota_id: int, equipo_id: str = Form(""), usuario_id: str = Form("")):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    op = _opciones_asignacion(usuario, nota)
    if not op:
        raise HTTPException(status_code=403, detail="No podés reasignar esta nota")
    nuevo_eq = int(equipo_id) if op["cambia_equipo"] and str(equipo_id).isdigit() else nota["equipo_id"]
    if op["cambia_equipo"] and nuevo_eq not in {e["id"] for e in op["equipos"]}:
        raise HTTPException(status_code=422, detail="Equipo no válido")
    nuevo_as = int(usuario_id) if str(usuario_id).isdigit() else None
    asesor = next((u for u in op["asesores"] if u["id"] == nuevo_as), None)
    if nuevo_as and not asesor:
        raise HTTPException(status_code=422, detail="Esa persona no puede quedar como responsable de esta nota")
    if asesor and asesor.get("equipo_id") != nuevo_eq:
        _flash(request, f"{asesor['nombre']} no es del equipo elegido: elegí a alguien de ese equipo.")
        return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)
    if nuevo_eq != nota["equipo_id"] and nota.get("bloque_id"):
        _flash(request, "Esta nota está en un bloque: sacala del bloque antes de cambiarla de equipo.")
        return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)
    cambios = db.reasignar_nota(nota_id, nuevo_eq, nuevo_as if nuevo_as else nota["usuario_id"], usuario["nombre"])
    _flash(request, "Responsables actualizados." if cambios else "No había cambios.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


# ---------- Ola 6 (131): tipo documental confirmado por una persona ----------
@app.post("/nota/{nota_id}/clasificar")
async def clasificar_nota(request: Request, nota_id: int):
    """Reforma 14/11/2026: clasificación del documento (el sistema propone, la persona confirma). Hasta la firma."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not ((_puede_gestionar_nota(usuario, nota) or auth.es_admin(usuario)) and _puede_ver_nota(usuario, nota)):
        raise HTTPException(status_code=403, detail="No podés clasificar este documento")
    if nota["estado"] not in ESTADOS_CLASIFICABLES or nota.get("protocolo_numero") or _protocolos_vigentes(nota_id):
        raise HTTPException(status_code=409, detail="El documento ya está firmado o tiene número de protocolo: la clasificación no se cambia")
    form = await request.form()
    from dgpdh.errores import DatoInvalido
    try:
        clasificacion_notas.guardar(nota_id, {k: form.get(k) for k in ("tipo", "subtipo", "alcance", "confidencialidad", "expediente", "destinatario")}, usuario)
    except DatoInvalido as e:
        _flash(request, "⚠ " + e.mensaje)
        return RedirectResponse(url=f"/nota/{nota_id}#clasificacion", status_code=303)
    _flash(request, "Clasificación confirmada.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/protocolo/anular")
async def anular_protocolo(request: Request):
    """Anula un número de protocolo (Dirección), con motivo. No se reutiliza nunca."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    form = await request.form()
    from dgpdh.errores import ErrorDGPDH
    from dgpdh.seguridad import auditoria as _au
    from dgpdh.servicios import protocolo as _pr
    numero = str(form.get("numero") or "").strip().upper()
    try:
        with db._conexion() as con:
            n = _pr.anular(con, numero, form.get("motivo"), _au.Actor.desde(usuario))
    except ErrorDGPDH as e:
        _flash(request, "⚠ " + e.mensaje)
        return RedirectResponse(url=_volver_seguro(form.get("volver")), status_code=303)
    if n.nota_id:
        db.registrar_evento(n.nota_id, f"Número de protocolo {n.numero} anulado por {usuario['nombre']}: {n.motivo_anulacion}")
    _flash(request, f"Número {n.numero} anulado. No se vuelve a usar.")
    return RedirectResponse(url=_volver_seguro(form.get("volver"), f"/nota/{n.nota_id}" if n.nota_id else "/"), status_code=303)


ESTADOS_CLASIFICABLES = ("BORRADOR", "SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "ERROR")


def _protocolos_vigentes(nota_id):
    return [p for p in _protocolos_de(nota_id) if p.estado == "ASIGNADO"]


def _volver_seguro(valor, defecto="/"):
    """Solo rutas internas: «/algo», nunca «//otro-sitio» ni «/\\otro-sitio» (evita redirecciones abiertas)."""
    v = str(valor or "")
    return v if v.startswith("/") and not v.startswith(("//", "/\\")) else defecto


def _protocolos_de(nota_id):
    from dgpdh.servicios import protocolo as _pr
    try:
        with db._conexion() as con:
            return _pr.de_nota(con, nota_id)
    except Exception:
        return []


templates.env.globals["clasificacion_de"] = lambda n: clasificacion_notas.de_nota(n)
templates.env.globals["es_informe"] = clasificacion_notas.es_informe
templates.env.globals["protocolos_de"] = _protocolos_de
templates.env.globals["CONFIDENCIALIDAD"] = list(clasificacion_notas.ETIQUETA_CONFIDENCIALIDAD.items())
templates.env.globals["ALCANCES"] = list(clasificacion_notas.ETIQUETA_ALCANCE.items())
templates.env.globals["SUBTIPOS"] = clasificacion_notas.SUBTIPOS


@app.post("/nota/{nota_id}/tipo-documental")
def confirmar_tipo_documental(request: Request, nota_id: int, tipo: str = Form(""), propuesto: str = Form("")):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No podés cambiar el tipo de esta nota")
    if tipo not in tipos_documentales.ETIQUETA:
        raise HTTPException(status_code=422, detail="Tipo de documento no válido")
    db.fijar_tipo_documental(nota_id, tipo, usuario["nombre"], "CONFIRMADO" if tipo == propuesto or nota.get("tipo_documental") == tipo else "CAMBIADO")
    _flash(request, f"Tipo de documento: {tipos_documentales.ETIQUETA[tipo]}.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


# ---------- Ola 6 (102): búsquedas guardadas de expedientes (por persona, en sus preferencias) ----------
MAX_BUSQUEDAS_EXPEDIENTES = 12


def _pref_usuario(usuario):
    try:
        return json.loads(usuario.get("pref_notas") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def _busquedas_expedientes(usuario):
    from urllib.parse import urlencode
    salida = []
    for b in _pref_usuario(usuario).get("busquedas_expedientes") or []:
        f = {k: v for k, v in (b.get("filtros") or {}).items() if k in expedientes.CAMPOS and v}
        if f:
            salida.append({"nombre": b.get("nombre", ""), "filtros": f, "qs": urlencode(f)})
    return salida


@app.post("/expedientes/busquedas/guardar")
async def guardar_busqueda_expedientes(request: Request):
    from urllib.parse import urlencode
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *ROLES_EXPEDIENTES)
    form = await request.form()
    nombre = " ".join(str(form.get("titulo_busqueda") or "").split())[:40]  # «nombre» es un campo de búsqueda
    filtros = {k: str(form.get(k) or "").strip()[:120] for k in expedientes.CAMPOS}
    filtros = {k: v for k, v in filtros.items() if v}
    if not nombre or not filtros:
        _flash(request, "Poné un nombre y buscá por al menos un campo antes de guardar la búsqueda.")
        return RedirectResponse(url="/expedientes?" + urlencode(filtros), status_code=303)
    pref = _pref_usuario(usuario)
    lista = [b for b in (pref.get("busquedas_expedientes") or []) if b.get("nombre") != nombre]
    lista.append({"nombre": nombre, "filtros": filtros})
    pref["busquedas_expedientes"] = lista[-MAX_BUSQUEDAS_EXPEDIENTES:]
    db.guardar_pref_notas(usuario["id"], pref)
    _flash(request, f"Búsqueda «{nombre}» guardada.")
    return RedirectResponse(url="/expedientes?" + urlencode(filtros), status_code=303)


@app.post("/expedientes/busquedas/borrar")
def borrar_busqueda_expedientes(request: Request, nombre: str = Form("")):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *ROLES_EXPEDIENTES)
    pref = _pref_usuario(usuario)
    pref["busquedas_expedientes"] = [b for b in (pref.get("busquedas_expedientes") or []) if b.get("nombre") != nombre]
    db.guardar_pref_notas(usuario["id"], pref)
    _flash(request, f"Búsqueda «{nombre}» quitada.")
    return RedirectResponse(url="/expedientes", status_code=303)



# ---------- Expedientes: cruce con detenidos (Cambio 7 nov. 2026) ----------

ROLES_CRUCE_DETENIDOS = (auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR, auth.ROL_COORDINADOR)


def _hacer_cruce_detenidos_expedientes():
    """Cruza todos los detenidos activos contra las planillas de expedientes (match por DNI y LPU)."""
    import detenidos as _det
    _exp = expedientes  # already imported

    registros = _exp.cargar()
    # Pre-build indexes: norm_num(value) → [registro] for DNI and LPU columns
    idx_dni, idx_lpu = {}, {}
    rmap = {}  # id(r) → r
    for r in registros:
        rmap[id(r)] = r
        for col in _exp.columnas_de(r, "dni"):
            n = _exp.norm_num(r["datos"].get(col) or "")
            if len(n) >= 3:
                idx_dni.setdefault(n, []).append(id(r))
        for col in _exp.columnas_de(r, "lpu"):
            n = _exp.norm_num(r["datos"].get(col) or "")
            if len(n) >= 3:
                idx_lpu.setdefault(n, []).append(id(r))

    todos = _det.buscar(incluir_bajas=False, limite=10000)

    exactos, parciales, incongruencias, sin_match = [], [], [], []
    expte_a_det_ids = {}  # expte_norm → [det_id] for duplicate detection

    def _fmt(r):
        exptes = _exp.valores_de(r, "expediente")
        nombres = _exp.valores_de(r, "nombre")
        return {"expediente": exptes[0] if exptes else None,
                "nombre": nombres[0] if nombres else None,
                "archivo": r["archivo"], "fila": r["fila"]}

    for d in todos:
        dni_n = _exp.norm_num(d.get("dni") or "")
        lpu_n = _exp.norm_num(d.get("lpu") or "")

        ids_dni = set(idx_dni.get(dni_n, [])) if len(dni_n) >= 3 else set()
        ids_lpu = set(idx_lpu.get(lpu_n, [])) if len(lpu_n) >= 3 else set()
        ids_exact = ids_dni & ids_lpu
        ids_all = ids_dni | ids_lpu

        if not ids_all:
            sin_match.append({"detenido": d})
            continue

        matches_all = [rmap[i] for i in ids_all]
        exptes_match = []
        for r in matches_all:
            exptes_match.extend(_exp.valores_de(r, "expediente"))

        # Incongruencia: tiene expediente pero la planilla dice otro
        if d.get("nro_expediente_ppn") and exptes_match:
            n_actual = _exp.norm_num(d["nro_expediente_ppn"])
            if not any(_exp.norm_num(e) == n_actual for e in exptes_match):
                incongruencias.append({
                    "detenido": d,
                    "expte_ficha": d["nro_expediente_ppn"],
                    "exptes_planilla": sorted(set(exptes_match))[:5],
                    "matches": [_fmt(r) for r in matches_all[:2]],
                })
                continue

        if ids_exact:
            matches_exact = [rmap[i] for i in ids_exact]
            exptes_e = []
            for r in matches_exact:
                exptes_e.extend(_exp.valores_de(r, "expediente"))
            expte = exptes_e[0] if exptes_e else None
            exactos.append({
                "detenido": d,
                "expte": expte,
                "matches": [_fmt(r) for r in matches_exact[:2]],
            })
            if expte:
                expte_a_det_ids.setdefault(_exp.norm_num(expte), []).append(d["id"])
        else:
            matches_parciales = [rmap[i] for i in ids_all]
            parciales.append({
                "detenido": d,
                "matches": [_fmt(r) for r in matches_parciales[:3]],
                "solo_dni": bool(ids_dni - ids_lpu),
                "solo_lpu": bool(ids_lpu - ids_dni),
            })

    duplicados = [{"expediente": expte_n, "detenido_ids": ids}
                  for expte_n, ids in expte_a_det_ids.items() if len(ids) > 1]

    return {"exactos": exactos, "parciales": parciales, "incongruencias": incongruencias,
            "duplicados": duplicados, "sin_match": sin_match}


@app.get("/expedientes/cruce-detenidos", response_class=HTMLResponse)
def expedientes_cruce_detenidos(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *ROLES_CRUCE_DETENIDOS)
    cruce = _hacer_cruce_detenidos_expedientes()
    return templates.TemplateResponse("expedientes_cruce.html", {
        "request": request, "usuario": usuario, "estado_exp": expedientes.estado(), **cruce,
    })


@app.get("/expedientes/cruce-detenidos/exportar")
def expedientes_cruce_exportar(request: Request):
    import csv, io
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *ROLES_CRUCE_DETENIDOS)
    cruce = _hacer_cruce_detenidos_expedientes()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Tipo", "ID", "Nombre", "DNI", "LPU", "Expte_ficha", "Expte_planilla", "Planilla"])
    for item in cruce["exactos"]:
        d = item["detenido"]; m = item["matches"][0] if item["matches"] else {}
        w.writerow(["Exacto", d["id"], d["nombre_completo"], d.get("dni",""), d.get("lpu",""),
                    d.get("nro_expediente_ppn",""), item.get("expte",""), m.get("archivo","")])
    for item in cruce["parciales"]:
        d = item["detenido"]; m = item["matches"][0] if item["matches"] else {}
        w.writerow(["Parcial", d["id"], d["nombre_completo"], d.get("dni",""), d.get("lpu",""),
                    d.get("nro_expediente_ppn",""), m.get("expediente",""), m.get("archivo","")])
    for item in cruce["incongruencias"]:
        d = item["detenido"]; m = item["matches"][0] if item["matches"] else {}
        w.writerow(["Incongruencia", d["id"], d["nombre_completo"], d.get("dni",""), d.get("lpu",""),
                    item["expte_ficha"], "; ".join(item["exptes_planilla"]), m.get("archivo","")])
    for item in cruce["sin_match"]:
        d = item["detenido"]
        w.writerow(["Sin match", d["id"], d["nombre_completo"], d.get("dni",""), d.get("lpu",""), "", "", ""])
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(content=csv_bytes, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=cruce_detenidos_expedientes.csv"})


@app.post("/expedientes/cruce-detenidos/vincular-exactos")
async def expedientes_vincular_exactos(request: Request):
    import detenidos as _det
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *ROLES_CRUCE_DETENIDOS)
    cruce = _hacer_cruce_detenidos_expedientes()
    form = await request.form()
    ids_form = {int(v) for v in form.getlist("ids") if str(v).isdigit()}
    if not ids_form:
        _flash(request, "⚠ No tildaste ningún detenido.")
        return RedirectResponse(url="/expedientes/cruce-detenidos", status_code=303)

    expte_por_id = {item["detenido"]["id"]: item["expte"]
                    for item in cruce["exactos"] if item.get("expte")}
    vinculados = 0
    for det_id in ids_form:
        expte = expte_por_id.get(det_id)
        if not expte:
            continue
        actual = _det.obtener(det_id)
        if not actual or actual.get("nro_expediente_ppn"):
            continue  # ya tiene uno: no tocar
        datos = {c: actual.get(c) for c, _, _ in _det.CAMPOS}
        datos["nro_expediente_ppn"] = expte
        _, errores, _ = _det.guardar(datos, usuario, det_id)
        if not errores:
            vinculados += 1
    _flash(request, f"Se vincularon {vinculados} expediente(s).")
    return RedirectResponse(url="/expedientes/cruce-detenidos", status_code=303)

@app.get("/expedientes/consultas", response_class=HTMLResponse)
def consultas_expedientes(request: Request):
    """Quién consultó qué: solo Dirección."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario)  # solo Dirección (admin)
    return templates.TemplateResponse("expedientes_consultas.html", {
        "request": request, "usuario": usuario, "consultas": db.listar_consultas_expedientes(),
    })


@app.post("/nota/{nota_id}/numeros")
def cargar_numeros_nota(request: Request, nota_id: int, nro_nota: str = Form(""), nro_expte: str = Form("")):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    _guardar_numeros(request, [(nota, nro_nota, nro_expte)])
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.get("/bloque/{bloque_id}/despacho", response_class=HTMLResponse)
def pagina_despacho_bloque(request: Request, bloque_id: int):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    bloque = db.obtener_bloque(bloque_id)
    if bloque is None:
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    return templates.TemplateResponse(
        "despacho_bloque.html", {"request": request, "bloque": bloque, "usuario": usuario}
    )


@app.post("/bloque/{bloque_id}/numeros")
async def cargar_numeros_bloque(request: Request, bloque_id: int):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    bloque = db.obtener_bloque(bloque_id)
    if bloque is None:
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    form = await request.form()
    pares = [
        (n, form.get(f"nro_nota_{n['id']}", ""), form.get(f"nro_expte_{n['id']}", ""))
        for n in bloque["notas"] if n["estado"] in db.LISTAS_PARA_REMITIR
    ]
    # Se puede numerar de a partes: los documentos sin número de nota quedan como estaban (no se generan).
    con_numero = [(n, nn, ne) for n, nn, ne in pares if str(nn or "").strip() or n.get("nro_nota")]
    if not con_numero:
        _flash(request, "⚠ No se cargó ningún número de nota. Para bajar los documentos sin numerar usá «Descargar sin número».")
    elif _guardar_numeros(request, con_numero) and len(con_numero) < len(pares):
        faltan = len(pares) - len(con_numero)
        _flash(request, request.session.get("flash", "") + f" Quedan {faltan} documento(s) sin número: se generan cuando les cargues el número.")
    return RedirectResponse(url=f"/bloque/{bloque_id}/despacho", status_code=303)


@app.get("/nota/{nota_id}/descargar-despacho")
def descargar_despacho(request: Request, nota_id: int, formato: str = "pdf"):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para descargar esta nota")
    ruta = nota.get("ruta_despacho_pdf") if formato == "pdf" else nota.get("ruta_despacho_docx")
    if not ruta or not Path(ruta).is_file():
        raise HTTPException(status_code=409, detail="Todavía no se generó ese archivo")
    db.registrar_acceso(nota_id, "DESCARGA", f"despacho {formato}")
    return FileResponse(ruta, filename=Path(ruta).name)


@app.get("/bloque/{bloque_id}/descargar-despacho")
def descargar_despacho_bloque(request: Request, bloque_id: int, formato: str = "pdf"):
    usuario = usuario_actual(request)
    bloque = db.obtener_bloque(bloque_id)
    if bloque is None:
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    if not any(_puede_ver_nota(usuario, n) for n in bloque["notas"]):
        raise HTTPException(status_code=403, detail="No tenés permiso para descargar este bloque")
    col = "ruta_despacho_pdf" if formato == "pdf" else "ruta_despacho_docx"
    archivos = [n for n in bloque["notas"] if n.get(col) and Path(n[col]).is_file()]
    if not archivos:
        _flash(request, "⚠ Todavía no hay documentos numerados en este bloque. Para bajarlos sin número usá «Descargar sin número».")
        return RedirectResponse(url=f"/bloque/{bloque_id}/despacho", status_code=303)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in archivos:
            zf.write(n[col], arcname=Path(n[col]).name)
            db.registrar_acceso(n["id"], "DESCARGA", f"despacho {formato} bloque {bloque_id}")
    nombre_zip = _sanitizar_nombre_archivo(bloque["nombre"]) + f" - despacho {formato}.zip"
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre_zip)}"},
    )


CARPETA_PDF_SIN_NUMERO = CARPETA_DATOS / "pdf_sin_numero"


def _pdf_sin_numero(nota):
    """PDF del documento firmado tal cual (sin número de nota ni de expediente). Se guarda en una carpeta aparte y se
    reutiliza mientras el firmado no cambie. El firmado no se toca."""
    base = Path(_documento_base(nota) or "")
    if not base.is_file():
        return None
    solo_mesa = not nota.get("ruta_firmada")
    huella = integridad.sha256_archivo(base)
    guardada = nota.get("sha256_original" if solo_mesa else "sha256_firmado")
    if guardada and huella != guardada:
        raise ValueError(f"«{nota['nombre_original']}»: el documento cambió desde que se firmó; no se convierte.")
    carpeta = CARPETA_PDF_SIN_NUMERO / str(nota["id"])
    pdf = carpeta / (_sanitizar_nombre_archivo(Path(nota["nombre_original"]).stem) + ".pdf")
    marca = carpeta / "huella.txt"
    if pdf.is_file() and marca.is_file() and marca.read_text(encoding="utf-8").strip() == huella:
        return pdf
    carpeta.mkdir(parents=True, exist_ok=True)
    if base.suffix.lower() == ".pdf":
        shutil.copyfile(base, pdf)
    else:
        with nucleo._WORD_LOCK:
            despacho.convertir_a_pdf(base, pdf)
    marca.write_text(huella, encoding="utf-8")
    return pdf


def _zip_documentos(request, notas, formato, detalle_acceso, nombre_zip, volver_url):
    """Arma un .zip con varios documentos firmados: «pdf» y «docx» = el firmado tal cual (sin números);
    «despacho» = la copia numerada (Word y PDF) de los que ya la tienen, y el firmado de los que no."""
    archivos, errores = [], []
    for n in notas:
        try:
            if formato == "pdf":
                r = _pdf_sin_numero(n)
                archivos += [(n, r, Path(n["nombre_original"]).stem, ".pdf")] if r else []
            elif formato == "despacho" and _despacho_preparado(n):
                archivos.append((n, Path(n["ruta_despacho_docx"]), Path(n["ruta_despacho_docx"]).stem, ".docx"))
                if n.get("ruta_despacho_pdf") and Path(n["ruta_despacho_pdf"]).is_file():
                    archivos.append((n, Path(n["ruta_despacho_pdf"]), Path(n["ruta_despacho_pdf"]).stem, ".pdf"))
                r = True
            else:
                r = Path(_documento_base(n) or "")
                r = r if r.is_file() else None
                if r:
                    archivos.append((n, r, Path(n["nombre_original"]).stem + (" - FIRMADA" if n.get("ruta_firmada") else ""),
                                     r.suffix or Path(n["nombre_original"]).suffix or ".docx"))
            if not r:
                errores.append(f"«{n['nombre_original']}»: no se encuentra el documento.")
        except Exception as e:
            logging.warning(f"Descarga en lote de la nota {n['id']}: {e}")
            errores.append(str(e) if isinstance(e, ValueError) else f"«{n['nombre_original']}»: no se pudo generar el PDF ({e}).")
    if not archivos:
        _flash(request, "⚠ No hay documentos firmados para descargar." + ((" " + " ".join(errores[:3])) if errores else ""))
        return RedirectResponse(url=volver_url, status_code=303)
    buffer = io.BytesIO()
    usados = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, r, raiz, ext in archivos:
            nombre = _sanitizar_nombre_archivo(raiz) + ext
            if nombre in usados:
                nombre = f"{n['id']} - {nombre}"
            usados.add(nombre)
            zf.write(r, arcname=nombre)
            db.registrar_acceso(n["id"], "DESCARGA", f"{detalle_acceso} ({formato})")
        if errores:
            zf.writestr("LEEME - documentos que faltan.txt", "\r\n".join(errores))
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre_zip)}"},
    )


@app.get("/bloque/{bloque_id}/descargar-sin-numero")
def descargar_bloque_sin_numero(request: Request, bloque_id: int, formato: str = "pdf"):
    """Los documentos firmados del bloque, SIN número de nota ni de expediente, en PDF o en Word (.zip).
    Sirve para imprimir, revisar o enviar antes de numerar."""
    usuario = usuario_actual(request)
    bloque = db.obtener_bloque(bloque_id)
    if bloque is None:
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    if not any(_puede_ver_nota(usuario, n) for n in bloque["notas"]):
        raise HTTPException(status_code=403, detail="No tenés permiso para descargar este bloque")
    notas = [n for n in bloque["notas"] if n["estado"] in db.LISTAS_PARA_REMITIR + ("DESPACHADA", "ADJUNTO") and _documento_base(n)]
    formato = "pdf" if formato == "pdf" else "docx"
    nombre_zip = _sanitizar_nombre_archivo(bloque["nombre"]) + f" - firmados sin número ({'PDF' if formato == 'pdf' else 'Word'}).zip"
    return _zip_documentos(request, notas, formato, f"bloque {bloque_id} sin número", nombre_zip, f"/bloque/{bloque_id}/despacho")


@app.post("/despacho/descargar")
async def descargar_despacho_del_dia(request: Request, formato: str = "docx"):
    """Despacho del día: los documentos marcados, todos juntos en un .zip, como un bloque (Word o PDF firmados sin
    números, o las copias de despacho). No hace falta cargar números para bajarlos."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    form = await request.form()
    ids = [int(x) for x in form.getlist("nota_id") if str(x).isdigit()]
    notas = [n for n in (db.obtener_nota(i) for i in ids) if n and n["estado"] in db.LISTAS_PARA_REMITIR and _documento_base(n)]
    if not notas:
        _flash(request, "⚠ Marcá al menos un documento firmado.")
        return RedirectResponse(url="/despacho", status_code=303)
    formato = formato if formato in ("pdf", "docx", "despacho") else "docx"
    etiqueta = {"pdf": "PDF firmados", "docx": "Word firmados", "despacho": "copias de despacho"}[formato]
    nombre_zip = f"Despacho {date.today().strftime('%d-%m-%Y')} - {etiqueta} ({len(notas)}).zip"
    return _zip_documentos(request, notas, formato, "despacho del día en lote", nombre_zip, "/despacho")


def _recargar_pagina():
    """Respuesta para los botones de htmx que cambian el estado de una nota
    y afectan más de lo que muestra la tarjeta (paneles de bloque, listado):
    htmx recarga la página completa en vez de pegar texto en la tarjeta."""
    return Response(status_code=200, headers={"HX-Refresh": "true"})


# =====================================================
# ENDPOINTS — BLOQUES (documentos que se despachan juntos)
# =====================================================
def _despachar_bloque(bloque_id):
    bloque = db.obtener_bloque(bloque_id)
    if bloque is None:
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    faltan = [n for n in bloque["notas"] if n["estado"] not in LISTOS_PARA_DESPACHO]
    if faltan:
        raise HTTPException(
            status_code=409,
            detail=f"El bloque no se puede despachar: faltan firmar {len(faltan)} documento(s)",
        )
    sin_numero = set(_exigir_preparado(bloque["notas"]) or [])
    for n in bloque["notas"]:
        if n["estado"] in db.LISTAS_PARA_REMITIR:
            db.marcar_despachada(n["id"])
            db.registrar_evento(n["id"], f"Despachada junto con el bloque {bloque['id']}"
                                + (" sin número de nota ni de expediente" if n["nombre_original"] in sin_numero else ""))
        elif n["estado"] == "ADJUNTO":
            db.actualizar_estado(n["id"], "DESPACHADA", comentario=f"Adjunto despachado junto con el bloque {bloque['id']}", desde=("ADJUNTO",))
    return sorted(sin_numero)


@app.post("/bloques")
async def armar_bloque(request: Request):
    """Agrupa documentos ya subidos para que se despachen juntos. Cada uno
    sigue su circuito individual; solo cambia el despacho."""
    usuario = usuario_actual(request)
    form = await request.form()
    ids = []
    for valor in form.getlist("nota_ids"):
        try:
            ids.append(int(valor))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))
    if len(ids) < 2:
        raise HTTPException(status_code=422, detail="Seleccioná al menos 2 documentos para armar un bloque")

    notas = []
    for nid in ids:
        nota = db.obtener_nota(nid)
        if nota is None:
            raise HTTPException(status_code=404, detail=f"Nota {nid} no encontrada")
        if not _puede_gestionar_nota(usuario, nota):
            raise HTTPException(status_code=403, detail=f"No podés agrupar el documento «{nota['nombre_original']}»")
        if nota["estado"] not in ESTADOS_ACTIVOS:
            raise HTTPException(status_code=409, detail=f"«{nota['nombre_original']}» ya no está en circuito")
        if nota.get("bloque_id"):
            raise HTTPException(
                status_code=409,
                detail=f"«{nota['nombre_original']}» ya pertenece a un bloque; quitala primero de ese bloque",
            )
        notas.append(nota)
    if len({n["equipo_id"] for n in notas}) != 1:
        raise HTTPException(status_code=409, detail="Solo se pueden agrupar documentos del mismo equipo")

    nombre = (form.get("nombre") or "").strip()
    db.crear_bloque(nombre, notas[0]["equipo_id"], usuario["id"], ids)
    _reanalizar_bloque(ids)
    _flash(request, f"Bloque armado con {len(ids)} documentos.")
    return RedirectResponse(url="/", status_code=303)


@app.post("/nota/{nota_id}/bloque/agregar")
async def agregar_a_bloque(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para modificar los bloques de esta nota")
    if nota["estado"] not in ESTADOS_ACTIVOS or nota.get("bloque_id"):
        raise HTTPException(status_code=409, detail="Esta nota no se puede agregar a un bloque")

    form = await request.form()
    try:
        bloque_id = int(form.get("bloque_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Falta elegir el bloque")
    bloque = db.obtener_bloque(bloque_id)
    if bloque is None or bloque["equipo_id"] != nota["equipo_id"]:
        raise HTTPException(status_code=409, detail="Ese bloque no es de este equipo")
    if any(n["estado"] == "DESPACHADA" for n in bloque["notas"]):
        raise HTTPException(status_code=409, detail="Ese bloque ya fue despachado")

    db.agregar_nota_a_bloque(nota_id, bloque_id)
    _reanalizar_bloque([n["id"] for n in bloque["notas"]] + [nota_id])
    _flash(request, f"Agregado al bloque «{bloque['nombre']}».")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/bloque/quitar")
def quitar_de_bloque(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para modificar los bloques de esta nota")
    if not nota.get("bloque_id"):
        raise HTTPException(status_code=409, detail="Esta nota no pertenece a ningún bloque")
    if nota["estado"] == "DESPACHADA":
        raise HTTPException(status_code=409, detail="Esta nota ya fue despachada")

    bloque_anterior = db.obtener_bloque(nota["bloque_id"])
    db.quitar_nota_de_bloque(nota_id)
    _reanalizar_bloque([nota_id] + [n["id"] for n in (bloque_anterior or {"notas": []})["notas"] if n["id"] != nota_id])
    _flash(request, "Quitado del bloque.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/bloque/{bloque_id}/despachar")
def despachar_bloque(request: Request, bloque_id: int):
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    sin_numero = _despachar_bloque(bloque_id)
    _flash(request, "Bloque despachado correctamente." + (f" {len(sin_numero)} documento(s) sin número de nota (queda registrado en el historial)." if sin_numero else ""))
    return _recargar_pagina()


@app.get("/bloque/{bloque_id}/descargar")
def descargar_bloque(request: Request, bloque_id: int):
    """Un .zip con los documentos firmados del bloque, para bajarlos juntos.
    Para rol MESA incluye automáticamente el Excel de reporte de descarga.
    """
    usuario = usuario_actual(request)
    bloque = db.obtener_bloque(bloque_id)
    if bloque is None:
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    if not any(_puede_ver_nota(usuario, n) for n in bloque["notas"]):
        raise HTTPException(status_code=403, detail="No tenés permiso para descargar este bloque")

    firmados = [n for n in bloque["notas"] if n["ruta_firmada"] and Path(n["ruta_firmada"]).is_file()]
    adjuntos = [n for n in bloque["notas"] if not n.get("ruta_firmada") and n.get("modalidad") == "SOLO_MESA"
                and n.get("ruta_original") and Path(n["ruta_original"]).is_file()]
    todos = firmados + adjuntos
    if not todos:
        raise HTTPException(status_code=409, detail="Todavía no hay documentos listos en este bloque")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in todos:
            ruta = n.get("ruta_firmada") or n.get("ruta_original")
            tipo = "ADJUNTO" if n.get("modalidad") == "SOLO_MESA" and not n.get("ruta_firmada") else "FIRMADO"
            zf.write(ruta, arcname=Path(ruta).name)
            db.registrar_acceso(n["id"], "DESCARGA", f"bloque {bloque_id}")
        # FUTURO: además de ROLES_MESA, usuarios con permiso "firma_archivo" podrán acceder.
        # Ese permiso se asignará a jefes, directores de línea y delegados desde /admin.
        if usuario.get("rol") in auth.ROLES_MESA:
            import reporte_descarga
            archivos_meta = [{
                "nota_id": n["id"],
                "bloque_id": str(bloque_id),
                "archivo_nombre": Path(n.get("ruta_firmada") or n.get("ruta_original")).name,
                "lpu": n.get("nro_nota") or n.get("lpu"),
                "dni": n.get("dni"),
                "expediente_ppn": n.get("nro_expediente_ppn"),
                "tipo_archivo": "ADJUNTO" if n.get("modalidad") == "SOLO_MESA" and not n.get("ruta_firmada") else "FIRMADO",
            } for n in todos]
            agente = {"id": usuario["id"], "nombre": usuario["nombre"]}
            excel_bytes, excel_nombre = reporte_descarga.generar_excel_descarga(archivos_meta, agente)
            zf.writestr(excel_nombre, excel_bytes)
    buffer.seek(0)
    nombre_zip = _sanitizar_nombre_archivo(bloque["nombre"]) + " - firmados.zip"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre_zip)}"},
    )


# =====================================================
# ENDPOINTS — PRIORIDAD
# =====================================================
@app.post("/nota/{nota_id}/prioridad")
async def cambiar_prioridad(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para cambiar la prioridad de esta nota")
    if nota["estado"] not in ESTADOS_ACTIVOS:
        raise HTTPException(status_code=409, detail="Esta nota ya no está en circuito")

    form = await request.form()
    prioridad = (form.get("prioridad") or "").strip()
    if prioridad not in auth.PRIORIDADES:
        raise HTTPException(status_code=422, detail="Prioridad no válida")
    if prioridad != nota["prioridad"]:
        db.actualizar_prioridad(
            nota_id,
            prioridad,
            f"Prioridad: {auth.ETIQUETAS_PRIORIDAD[nota['prioridad']]} → {auth.ETIQUETAS_PRIORIDAD[prioridad]} ({usuario['nombre']})",
        )
        request.session["deshacer"] = {"texto": f"Prioridad {auth.ETIQUETAS_PRIORIDAD[prioridad]} en «{nota['nombre_original']}»",
                                       "previas": {str(nota_id): nota["prioridad"]}}
    _flash(request, f"Prioridad: {auth.ETIQUETAS_PRIORIDAD[prioridad]}.")
    volver = (form.get("volver") or "").strip()
    if not volver.startswith("/") or volver.startswith("//") or "\\" in volver:
        volver = f"/nota/{nota_id}"
    return RedirectResponse(url=volver, status_code=303)


# =====================================================
# ENDPOINTS — ANÁLISIS AUTOMÁTICO (tema, prioridad sugerida, consistencia)
# =====================================================
def _nota_gestionable(request, nota_id):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para modificar esta nota")
    if nota["estado"] not in ESTADOS_ACTIVOS:
        raise HTTPException(status_code=409, detail="Esta nota ya no está en circuito")
    return usuario, nota


@app.post("/nota/{nota_id}/tema")
async def cambiar_tema(request: Request, nota_id: int):
    usuario, nota = _nota_gestionable(request, nota_id)
    form = await request.form()
    tema = (form.get("tema") or "").strip()
    if tema == "__auto__":
        db.actualizar_tema(nota_id, nota.get("tema"), "auto")
        try:
            automatico.clasificar_rapido(nota_id)
        except Exception:
            logging.exception("No se pudo reclasificar")
        db.registrar_evento(nota_id, f"Tema vuelto a clasificación automática ({usuario['nombre']})")
    elif tema in analisis.TEMAS:
        if tema != nota.get("tema"):
            db.actualizar_tema(nota_id, tema, "manual")
            db.registrar_evento(nota_id, f"Tema: {analisis.etiqueta_tema(tema)} ({usuario['nombre']})")
    else:
        raise HTTPException(status_code=422, detail="Tema no válido")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/prioridad-sugerida/{accion}")
def resolver_prioridad_sugerida(request: Request, nota_id: int, accion: str):
    """Acepta o descarta la prioridad que sugirió el sistema. Aceptar usa el
    mismo cambio de prioridad de siempre (queda en el historial); nunca se
    aplica sola."""
    usuario, nota = _nota_gestionable(request, nota_id)
    if accion not in ("aceptar", "descartar"):
        raise HTTPException(status_code=404, detail="Acción desconocida")
    sugerida = nota.get("prioridad_sugerida")
    if nota.get("prioridad_sugerida_estado") != "PENDIENTE" or sugerida not in auth.PRIORIDADES:
        raise HTTPException(status_code=409, detail="No hay una sugerencia pendiente")
    if accion == "aceptar":
        db.actualizar_prioridad(
            nota_id, sugerida,
            f"Prioridad sugerida aceptada: {auth.ETIQUETAS_PRIORIDAD[nota['prioridad']]} → "
            f"{auth.ETIQUETAS_PRIORIDAD[sugerida]} ({usuario['nombre']})"
            + (f" — {nota['prioridad_motivo']}" if nota.get("prioridad_motivo") else ""),
        )
        db.resolver_prioridad_sugerida(nota_id, "ACEPTADA")
    else:
        db.registrar_evento(nota_id, f"Prioridad sugerida ({auth.ETIQUETAS_PRIORIDAD[sugerida]}) descartada ({usuario['nombre']})")
        db.resolver_prioridad_sugerida(nota_id, "DESCARTADA")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/referencia")
async def elegir_referencia(request: Request, nota_id: int):
    """Documento de referencia (informe/proveído) elegido a mano, para
    chequear consistencia y proponer mejoras aunque no haya un bloque."""
    usuario, nota = _nota_gestionable(request, nota_id)
    form = await request.form()
    valor = (form.get("ref_nota_id") or "").strip()
    ref_id = None
    if valor:
        try:
            ref_id = int(valor)
        except ValueError:
            raise HTTPException(status_code=422, detail="Documento no válido")
        ref = db.obtener_nota(ref_id)
        if ref is None or ref["equipo_id"] != nota["equipo_id"] or ref_id == nota_id:
            raise HTTPException(status_code=409, detail="Ese documento no se puede usar de referencia")
    db.fijar_referencia(nota_id, ref_id)
    db.registrar_evento(nota_id, "Documento de referencia: " + (ref["nombre_original"] if ref_id else "ninguno") + f" ({usuario['nombre']})")
    _reanalizar_consistencia(nota_id)
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/consistencia/recalcular")
def recalcular_consistencia(request: Request, nota_id: int):
    """Chequeo por reglas, al instante (sin IA)."""
    _nota_gestionable(request, nota_id)
    try:
        resultado = automatico.analizar_consistencia(nota_id, usar_ia=False)
    except Exception as e:
        logging.exception(f"Error recalculando consistencia de la nota {nota_id}")
        raise HTTPException(status_code=500, detail=f"No se pudo chequear: {e}")
    if "omitido" in resultado:
        raise HTTPException(status_code=409, detail=f"No hay con qué comparar: {resultado['omitido']}")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


def _proponer_aplicacion(nota, h, parrafos):
    """Si el aviso es una DISCREPANCIA entre esta nota y otro documento sobre
    un dato concreto, y el dato de la nota aparece una sola vez en su texto,
    propone reemplazarlo por el valor del otro documento. Devuelve None si no
    hay una corrección segura."""
    if h.get("tipo") != "DISCREPANCIA" or not parrafos:
        return None
    valores = h.get("valores") or []
    propios = [v for v in valores if v.get("doc_id") == nota["id"]]
    otros = [v for v in valores if v.get("doc_id") != nota["id"]]
    if len(propios) != 1 or not otros or len({v.get("valor") for v in otros}) != 1:
        return None
    mio, nuevo = propios[0].get("valor"), otros[0].get("valor")
    if not mio or not nuevo or mio == nuevo:
        return None
    coincidencias = [p for p in parrafos if mio in p["texto"]]
    if len(coincidencias) != 1 or coincidencias[0]["texto"].count(mio) != 1:
        return None
    p = coincidencias[0]
    return {
        "indice": p["indice"], "antes": p["texto"], "despues": p["texto"].replace(mio, nuevo, 1),
        "desde": mio, "hacia": nuevo, "ref_doc": otros[0].get("doc") or "otro documento",
    }


def _hallazgo_aplicable(request, hallazgo_id):
    usuario = usuario_actual(request)
    hallazgo = db.obtener_hallazgo(hallazgo_id)
    if hallazgo is None:
        raise HTTPException(status_code=404, detail="Ese aviso ya se actualizó: volvé a la nota para ver la lista vigente")
    hallazgo["valores"] = json.loads(hallazgo["valores_json"])
    nota = db.obtener_nota(hallazgo["nota_id"])
    if nota is None or not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso sobre esta nota")
    if not _puede_editar_texto(usuario, nota):
        raise HTTPException(status_code=409, detail="El texto de esta nota ya no se puede editar (pasó a corrección) o no tenés permiso")
    parrafos, _ = nucleo.obtener_vista_previa(nota.get("ruta_base") or nota["ruta_original"])
    propuesta = _proponer_aplicacion(nota, hallazgo, parrafos)
    if propuesta is None:
        raise HTTPException(status_code=409, detail="Este aviso no se puede corregir automáticamente: editá el texto a mano")
    return usuario, nota, hallazgo, propuesta


@app.get("/hallazgo/{hallazgo_id}/aplicar", response_class=HTMLResponse)
def ver_aplicar_hallazgo(request: Request, hallazgo_id: int):
    usuario, nota, hallazgo, propuesta = _hallazgo_aplicable(request, hallazgo_id)
    segmentos = nucleo.calcular_diff_resaltado(propuesta["antes"], propuesta["despues"])
    return templates.TemplateResponse(
        "aplicar_hallazgo.html",
        {"request": request, "nota": nota, "hallazgo": hallazgo, "propuesta": propuesta, "segmentos": segmentos},
    )


@app.post("/hallazgo/{hallazgo_id}/aplicar")
def aplicar_hallazgo(request: Request, hallazgo_id: int):
    usuario, nota, hallazgo, propuesta = _hallazgo_aplicable(request, hallazgo_id)
    _aplicar_ediciones_texto(
        nota, {propuesta["indice"]: propuesta["despues"]}, usuario,
        detalle=f"Aviso aplicado por {usuario['nombre']}: «{propuesta['desde']}» → «{propuesta['hacia']}» (según {propuesta['ref_doc']})",
    )
    db.resolver_hallazgo(hallazgo_id, "REVISADO", usuario["nombre"])
    _flash(request, "Corrección aplicada al texto.")
    return RedirectResponse(url=f"/nota/{nota['id']}", status_code=303)


@app.post("/hallazgo/{hallazgo_id}/resolver")
async def resolver_hallazgo(request: Request, hallazgo_id: int):
    usuario = usuario_actual(request)
    hallazgo = db.obtener_hallazgo(hallazgo_id)
    if hallazgo is None:
        # El análisis se volvió a calcular mientras la pantalla estaba abierta:
        # no es un error de la persona, se vuelve a la nota con la lista actual.
        form = await request.form()
        destino = (form.get("next") or "").strip()
        if not destino.startswith("/nota/") or "//" in destino or ":" in destino:
            destino = "/"
        return RedirectResponse(url=destino, status_code=303)
    nota = db.obtener_nota(hallazgo["nota_id"])
    if nota is None or not _puede_gestionar_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para resolver este aviso")
    form = await request.form()
    estado = (form.get("estado") or "").strip()
    if estado not in ("REVISADO", "IGNORADO", "ABIERTO", "ERRONEO"):
        raise HTTPException(status_code=422, detail="Estado no válido")
    if estado == "ERRONEO":
        # La IA reportó algo que no es cierto: se guarda para que no lo repita.
        db.guardar_feedback_hallazgo(hallazgo, form.get("motivo"), usuario["nombre"])
    db.resolver_hallazgo(hallazgo_id, estado, usuario["nombre"])
    palabra = {"REVISADO": "revisado", "IGNORADO": "ignorado", "ABIERTO": "reabierto", "ERRONEO": "marcado como reporte erróneo de la IA"}[estado]
    db.registrar_evento(nota["id"], f"Consistencia: «{hallazgo['dato']}» {palabra} ({usuario['nombre']})")
    destino = (form.get("next") or "").strip()
    if not destino.startswith("/nota/") or "//" in destino or ":" in destino:
        destino = f"/nota/{nota['id']}"
    return RedirectResponse(url=destino, status_code=303)


# =====================================================
# ENDPOINTS — ADMINISTRACIÓN DE LA IA
# =====================================================
@app.post("/admin/ia/probar/{proceso}")
def admin_probar_modelo(request: Request, proceso: str):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    if proceso not in nucleo.PROCESOS_IA:
        raise HTTPException(status_code=404, detail="Proceso desconocido")
    import time
    nucleo.refrescar_modelos_ollama()
    inicio = time.time()
    salida = nucleo.consultar_ollama(proceso, "Respondé solamente con la palabra: OK", num_predict=10, timeout=120)
    if salida is None:
        mensaje = f"Prueba de «{nucleo.ETIQUETAS_PROCESOS_IA[proceso]}»: el modelo {nucleo.modelo_para(proceso)} no respondió (¿falta descargarlo o Ollama está apagado?)."
    else:
        mensaje = f"Prueba de «{nucleo.ETIQUETAS_PROCESOS_IA[proceso]}»: respondió en {time.time() - inicio:.1f} s con el modelo {nucleo.modelo_para(proceso)}."
    return _volver_admin(request, mensaje)


@app.post("/admin/correo/ajustes")
def admin_correo_ajustes(request: Request, activo: str = Form("0"), inmediatos: str = Form("0")):
    _exigir_rol(usuario_actual(request))
    db.guardar_ajuste("correo_activo", "1" if activo == "1" else "0")
    db.guardar_ajuste("correo_inmediatos", "1" if inmediatos == "1" else "0")
    return _volver_admin(request, "Ajustes de correo guardados.")


@app.post("/admin/correo/usuario/{usuario_id}")
def admin_correo_usuario(request: Request, usuario_id: int, correo: str = Form("")):
    _exigir_rol(usuario_actual(request))
    correo = correo.strip()
    if correo and not _CORREO_OK.match(correo):
        return _volver_admin(request, "⚠ Ese correo no es válido.")
    db.fijar_correo_usuario(usuario_id, correo)
    return _volver_admin(request, "Correo guardado.")


@app.post("/admin/correo/probar")
def admin_correo_probar(request: Request, destino: str = Form("")):
    _exigir_rol(usuario_actual(request))
    if not _CORREO_OK.match(destino.strip()):
        return _volver_admin(request, "⚠ Indicá un correo válido para la prueba.")
    try:
        avisos_correo.enviar(destino.strip(), "Prueba — Notas DGPDH", "Si lo recibís, el envío de correos funciona.")
        return _volver_admin(request, f"Correo de prueba enviado a {destino.strip()}.")
    except Exception as e:
        return _volver_admin(request, f"⚠ No se pudo enviar: {e}")


@app.post("/admin/ia/fines-de-semana")
def admin_ia_fines_de_semana(request: Request, activo: str = Form("1")):
    _exigir_rol(usuario_actual(request))
    db.guardar_ajuste("ia_fines_de_semana", "1" if activo == "1" else "0")
    return _volver_admin(request, "Fines de semana: la IA corre las 24 horas." if activo == "1" else "Fines de semana: la IA respeta el mismo horario que en la semana.")


@app.post("/admin/ia/adaptativa")
def admin_ia_adaptativa(request: Request, activa: str = Form("0")):
    _exigir_rol(usuario_actual(request))
    carga.activar(activa == "1")
    return _volver_admin(request, "IA adaptativa " + ("activada: corre cuando la PC tiene capacidad libre." if activa == "1" else "desactivada: vuelve al horario fijo."))


@app.post("/admin/ia/verificador")
def admin_ia_verificador(request: Request, activo: str = Form("0")):
    _exigir_rol(usuario_actual(request))
    db.guardar_ajuste("verificador", "1" if activo == "1" else "0")
    return _volver_admin(request, "Verificador cruzado " + ("activado." if activo == "1" else "desactivado."))


@app.post("/admin/ia/aprendizaje/{feedback_id}/borrar")
def admin_borrar_aprendizaje(request: Request, feedback_id: int):
    _exigir_rol(usuario_actual(request))
    db.desactivar_feedback(feedback_id)
    return _volver_admin(request, "Se quitó ese aprendizaje: la IA vuelve a poder señalar ese caso.")


@app.post("/admin/ia/ejecutar-ahora")
def admin_ejecutar_ahora(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    ok = automatico.ejecutar_ahora()
    mensaje = ("Se va a ejecutar la cola ahora, sin esperar al horario nocturno. Puede ralentizar el sistema mientras corra."
               if ok else "El proceso automático no está activo.")
    return _volver_admin(request, mensaje)


@app.post("/admin/ia/analizar-pendientes")
def admin_analizar_pendientes(request: Request):
    usuario = usuario_actual(request)
    _exigir_rol(usuario)
    cantidad = automatico.analizar_pendientes()
    mensaje = f"Se encolaron {cantidad} trabajo(s) de análisis para los documentos que siguen en circuito."
    return _volver_admin(request, mensaje)


# =====================================================
# ENDPOINTS — PERFIL Y CAMBIO DE CONTRASEÑA
# =====================================================
@app.get("/perfil", response_class=HTMLResponse)
def ver_perfil(request: Request, obligatorio: str = ""):
    usuario = usuario_actual(request)
    return templates.TemplateResponse(
        "perfil.html",
        {"request": request, "usuario": usuario, "mensaje": None, "error": None,
         "minimo": auth.CLAVE_MINIMO_CARACTERES,
         "obligatorio": bool(usuario.get("debe_cambiar_clave"))},
    )


@app.get("/politica-ia", response_class=HTMLResponse)
def politica_ia(request: Request):
    """Reforma 25: política escrita de uso de la IA en el sistema (qué hace, qué no, con qué datos y quién decide)."""
    usuario = usuario_actual(request)
    try:
        modelos = nucleo.estado_modelos_ia(refrescar=False)
    except Exception:
        modelos = []
    return templates.TemplateResponse("politica_ia.html", {"request": request, "usuario": usuario, "modelos": modelos,
                                                           "usar_ia": nucleo.USAR_IA, "url_ollama": getattr(nucleo, "OLLAMA_URL", "")})


@app.get("/guia-estilo", response_class=HTMLResponse)
def guia_estilo(request: Request):
    """Reforma 34: referencia visual de colores, botones, etiquetas y componentes, para mantener la coherencia."""
    usuario = usuario_actual(request)
    return templates.TemplateResponse("guia_estilo.html", {"request": request, "usuario": usuario})


@app.post("/perfil/clave", response_class=HTMLResponse)
async def cambiar_clave(request: Request):
    usuario = usuario_actual(request)
    form = await request.form()
    actual = form.get("clave_actual") or ""
    nueva = form.get("clave_nueva") or ""
    repetida = form.get("clave_repetida") or ""

    registro = db.obtener_usuario_por_nombre(usuario["usuario"])
    error = None
    if not auth.verificar_clave(actual, registro["clave_hash"], registro["clave_salt"]):
        error = "La clave actual no es correcta."
    elif nueva != repetida:
        error = "La clave nueva y su repetición no coinciden."
    elif nueva == actual:
        error = "La clave nueva tiene que ser distinta de la actual."
    else:
        error = auth.validar_clave_nueva(nueva, usuario["usuario"])

    if error is None:
        hash_clave, salt = auth.generar_hash_clave(nueva)
        db.cambiar_clave_usuario(usuario["id"], hash_clave, salt)
        if usuario.get("debe_cambiar_clave"):
            return RedirectResponse(url="/", status_code=303)
        mensaje = "Listo: tu clave se cambió correctamente."
    else:
        mensaje = None

    return templates.TemplateResponse(
        "perfil.html",
        {"request": request, "usuario": usuario, "mensaje": mensaje, "error": error,
         "minimo": auth.CLAVE_MINIMO_CARACTERES, "obligatorio": bool(usuario.get("debe_cambiar_clave")) and error is not None},
        status_code=422 if error else 200,
    )


@app.get("/perfil/firma")
async def perfil_firma_imagen(request: Request):
    """Sirve la imagen de firma del usuario actual."""
    import firmas_usuarios
    u = usuario_actual(request)
    p = firmas_usuarios.ruta(u["id"])
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@app.post("/perfil/firma")
async def perfil_firma_subir(request: Request, archivo: UploadFile = File(...)):
    """Sube y guarda la imagen de firma del usuario actual."""
    import firmas_usuarios
    u = usuario_actual(request)
    if u["rol"] not in ("DIRECTOR", "SUBDIRECTOR"):
        raise HTTPException(403)
    contenido = await archivo.read()
    if len(contenido) > 2 * 1024 * 1024:
        _flash(request, "⚠ La imagen no puede superar 2 MB.")
        return RedirectResponse("/perfil", status_code=303)
    try:
        firmas_usuarios.guardar(u["id"], contenido)
        with db._conexion() as con:
            con.execute("UPDATE usuarios SET imagen_firma = 1 WHERE id = ?", (u["id"],))
    except Exception as e:
        _flash(request, f"⚠ No se pudo guardar la firma: {e}")
    return RedirectResponse("/perfil", status_code=303)


@app.post("/perfil/firma/borrar")
async def perfil_firma_borrar(request: Request):
    """Elimina la imagen de firma personal del usuario actual."""
    import firmas_usuarios
    u = usuario_actual(request)
    firmas_usuarios.borrar(u["id"])
    with db._conexion() as con:
        con.execute("UPDATE usuarios SET imagen_firma = 0 WHERE id = ?", (u["id"],))
    return RedirectResponse("/perfil", status_code=303)


# =====================================================
# ENDPOINTS — COINCIDENCIAS DOCUMENTO-PERSONA-EXPEDIENTE
# =====================================================

@app.post("/nota/{nota_id}/coincidencia/{cid}/confirmar")
async def _confirmar_coincidencia(request: Request, nota_id: int, cid: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404)
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403)
    coincs = db.listar_coincidencias(nota_id)
    coinc = next((c for c in coincs if c["id"] == cid), None)
    if coinc is None:
        raise HTTPException(status_code=404, detail="Coincidencia no encontrada")
    db.confirmar_coincidencia(cid, usuario["nombre"], usuario["id"])
    if coinc.get("expte_propuesto") and not nota.get("nro_expte"):
        with db._conexion() as con:
            con.execute("UPDATE notas SET nro_expte=? WHERE id=?",
                        (coinc["expte_propuesto"], nota_id))
        db.registrar_evento(nota_id, f"Expediente {coinc['expte_propuesto']} vinculado por coincidencia confirmada por {usuario['nombre']}")
    db.registrar_evento(nota_id, f"Coincidencia confirmada: {coinc.get('nombre_completo','?')} (nivel {coinc['nivel']}, {coinc['confianza']}%) por {usuario['nombre']}")
    _flash(request, "Coincidencia confirmada.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/coincidencia/{cid}/descartar")
async def _descartar_coincidencia(request: Request, nota_id: int, cid: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404)
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403)
    db.descartar_coincidencia(cid, usuario["nombre"], usuario["id"])
    db.registrar_evento(nota_id, f"Coincidencia descartada por {usuario['nombre']}")
    _flash(request, "Coincidencia descartada.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.post("/nota/{nota_id}/coincidencias/actualizar")
async def _actualizar_coincidencias(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404)
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403)
    db.reemplazar_coincidencias_propuestas(nota_id)
    import coincidencias as _coinc
    n = _coinc.buscar_y_guardar(nota_id)
    db.registrar_evento(nota_id, f"Búsqueda de coincidencias actualizada por {usuario['nombre']}: {n} candidato(s)")
    _flash(request, f"Búsqueda actualizada: {n} candidato(s).")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


# =====================================================
# ENDPOINTS — EXPORTACIÓN MESA
# =====================================================

@app.post("/despacho/exportar-mesa")
async def exportar_mesa(request: Request):
    """Exporta seleccionados como Excel para carga en sistema externo (Mesa de Entradas)."""
    import openpyxl
    from io import BytesIO as _BytesIO
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA)
    form = await request.form()
    ids = [int(x) for x in form.getlist("nota_id") if str(x).isdigit()]
    if not ids:
        raise HTTPException(status_code=422, detail="Seleccioná al menos un documento")
    import coincidencias as _coinc
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Exportación Mesa"
    ws.append(["ID interno", "ID evento (externo)", "Archivo",
               "Expediente PPN", "Expediente (fuente)",
               "Persona", "LPU", "DNI", "Tipo",
               "Fecha", "Estado", "Fecha despacho"])
    for nid in ids:
        nota = db.obtener_nota(nid)
        if nota is None or not _puede_ver_nota(usuario, nota):
            continue
        coincs = db.listar_coincidencias(nid)
        confirmada = next((c for c in coincs if c["estado"] == "CONFIRMADO"), None)
        propuesta = next((c for c in coincs if c["estado"] == "PROPUESTO"), None)
        mejor = confirmada or propuesta
        expte = nota.get("nro_expte") or ""
        fuente_expte = ""
        if not expte and mejor and mejor.get("expte_propuesto"):
            expte = mejor["expte_propuesto"] + " (sugerido)"
            fuente_expte = mejor.get("fuente_expte") or ""
        elif expte and mejor:
            fuente_expte = mejor.get("fuente_expte") or ""
        persona = lpu = dni = ""
        if mejor and mejor.get("nombre_completo"):
            persona = mejor["nombre_completo"]
            lpu = str(mejor.get("lpu") or "")
            dni = str(mejor.get("dni") or "")
        id_evento = ""
        if mejor and mejor.get("datos_doc"):
            try:
                dd = json.loads(mejor["datos_doc"])
                id_evento = str(dd.get("id_evento") or "")
            except Exception:
                pass
        if not id_evento:
            id_evento = _coinc._id_evento_del_archivo(nota.get("nombre_original", "")) or ""
        fecha_despacho = ""
        for ev in reversed(nota.get("historial") or []):
            if ev.get("estado") == "DESPACHADA":
                fecha_despacho = (ev.get("fecha") or "")[:10]
                break
        ws.append([
            nid, id_evento, nota.get("nombre_original", ""),
            expte, fuente_expte, persona, lpu, dni,
            nota.get("tipo") or nota.get("modalidad") or "",
            (nota.get("creado_en") or "")[:10],
            nota.get("estado", ""), fecha_despacho,
        ])
    buf = _BytesIO()
    wb.save(buf)
    buf.seek(0)
    nombre = f"exportacion_mesa_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"},
    )


# =====================================================
# ENDPOINTS — DESCARGAS
# =====================================================
# ---------- Beta 01: documentos PDF (archivo directo o adjunto de un bloque) ----------
ETIQUETA_ESTADO_ESPECIAL = {"ARCHIVADA": "Archivado", "ADJUNTO": "Adjunto del bloque", "A_CONFIRMAR": "Pendiente de confirmación (Dirección)",
                            "DESCARTADA": "Descartado (se puede recuperar)", "PARA_REMITIR": "Solo Mesa de Entradas"}
templates.env.globals["ETIQUETA_ESTADO_ESPECIAL"] = ETIQUETA_ESTADO_ESPECIAL


def _ver_pdf(request, usuario, nota):
    db.marcar_leidas_por_url(usuario["id"], f"/nota/{nota['id']}")
    puede = _puede_gestionar_nota(usuario, nota)
    bloque = db.obtener_bloque(nota["bloque_id"]) if nota.get("bloque_id") else None
    disponibles = []
    if puede and not bloque and nota["estado"] in ("ARCHIVADA", "A_CONFIRMAR"):
        disponibles = [b for b in db.listar_bloques(nota["equipo_id"])
                       if not any(bn["estado"] == "DESPACHADA" for bn in (db.obtener_bloque(b["id"]) or {"notas": []})["notas"])]
    candidatos_expte = (db.candidatos_expte_adjunto(nota["id"])
                         if nota["estado"] == "ADJUNTO" and not nota.get("nro_expte") else [])
    tiene_texto = db.tiene_texto_indexado(nota["id"])
    coincs = db.listar_coincidencias(nota["id"])
    return templates.TemplateResponse("nota_pdf.html", {
        "request": request, "usuario": usuario, "nota": nota, "bloque": bloque, "bloques_disponibles": disponibles,
        "notas_para_acompanar": _notas_para_acompanar(usuario, nota) if puede and not bloque and nota["estado"] in ("ARCHIVADA", "A_CONFIRMAR") else [],
        "puede_gestionar": puede, "duplicado": db.obtener_nota(nota["duplicado_de"]) if nota.get("duplicado_de") else None,
        "candidatos_expte": candidatos_expte,
        "tiene_texto": tiene_texto, "coincidencias": coincs,
        "eventos": nota.get("historial") or []})


def _notas_para_acompanar(usuario, pdf):
    """Documentos (no PDF) del mismo equipo a los que este PDF puede acompañar: todavía en circuito, sin despachar."""
    salida = []
    for n in db.listar_notas():
        if (n["id"] == pdf["id"] or n["equipo_id"] != pdf["equipo_id"] or db.es_pdf(n)
                or n["estado"] not in ESTADOS_ACTIVOS or n["estado"] in ("ARCHIVADA", "ADJUNTO")):
            continue
        if _puede_gestionar_nota(usuario, n):
            salida.append(n)
    return sorted(salida, key=lambda n: n["id"], reverse=True)[:300]


@app.post("/nota/{nota_id}/acompanar")
async def acompanar_documento(request: Request, nota_id: int):
    """Beta 01: un PDF acompaña a UN documento (si ese documento ya está en un bloque, se suma a ese bloque;
    si no, se arma un bloque con los dos). Se despacha junto con él, sin firma."""
    usuario = usuario_actual(request)
    pdf = db.obtener_nota(nota_id)
    if pdf is None or not db.es_pdf(pdf):
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    if not _puede_gestionar_nota(usuario, pdf):
        raise HTTPException(status_code=403, detail="No podés modificar este documento")
    if pdf["estado"] not in ("ARCHIVADA", "A_CONFIRMAR") or pdf.get("bloque_id"):
        raise HTTPException(status_code=409, detail="Este PDF ya acompaña a otro documento")
    form = await request.form()
    try:
        destino_id = int(form.get("destino_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Falta elegir el documento")
    destino = next((n for n in _notas_para_acompanar(usuario, pdf) if n["id"] == destino_id), None)
    if destino is None:
        raise HTTPException(status_code=409, detail="No se puede adjuntar a ese documento")
    if destino.get("bloque_id"):
        bloque = db.obtener_bloque(destino["bloque_id"])
        if any(n["estado"] == "DESPACHADA" for n in bloque["notas"]):
            raise HTTPException(status_code=409, detail="Ese bloque ya fue despachado")
        db.agregar_nota_a_bloque(nota_id, destino["bloque_id"])
    else:
        db.crear_bloque(f"{Path(destino['nombre_original']).stem[:100]} + adjunto", destino["equipo_id"], usuario["id"], [destino_id, nota_id])
    _flash(request, f"El PDF acompaña a «{destino['nombre_original']}»: se despacha junto con él.")
    return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)


@app.get("/nota/{nota_id}/pdf")
def ver_pdf_archivo(request: Request, nota_id: int):
    """El PDF tal como se cargó, para verlo en el navegador (no se modifica nunca)."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None or not db.es_pdf(nota):
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para ver este documento")
    ruta = Path(nota["ruta_original"])
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="El archivo ya no existe en el servidor")
    db.registrar_acceso(nota_id, "VER_PDF")
    from urllib.parse import quote
    return FileResponse(ruta, media_type="application/pdf",
                        headers={"Content-Disposition": "inline; filename*=UTF-8''" + quote(nota["nombre_original"])})


@app.get("/nota/{nota_id}/descargar")
def descargar_nota(request: Request, nota_id: int):
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para descargar esta nota")

    if nota["ruta_firmada"]:
        ruta = Path(nota["ruta_firmada"])
        tipo_archivo = "FIRMADO"
    elif nota["ruta_resultado"]:
        ruta = Path(nota["ruta_resultado"])
        tipo_archivo = "FIRMADO"
    elif nota.get("modalidad") == "SOLO_MESA":
        ruta = Path(nota["ruta_original"])
        tipo_archivo = "ADJUNTO"
    elif db.es_pdf(nota) and nota.get("tipo") == "ADJUNTO":
        ruta = Path(nota["ruta_original"])
        tipo_archivo = "ADJUNTO"
    else:
        raise HTTPException(status_code=409, detail="La nota todavía no tiene un resultado para descargar")

    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="El archivo ya no existe en el servidor")

    nombre_descarga = Path(nota["nombre_original"]).stem + (
        " - FIRMADA.docx" if nota["ruta_firmada"] else " - CORREGIDA.docx" if nota["ruta_resultado"] else ruta.suffix
    )
    db.registrar_acceso(nota_id, "DESCARGA", "firmada" if nota["ruta_firmada"] else "corregida" if nota["ruta_resultado"] else "adjunto")

    if tipo_archivo == "ADJUNTO" and usuario.get("rol") in auth.ROLES_MESA and not nota.get("ruta_firmada"):
        import uuid as _uuid
        db.registrar_descarga_mesa(
            paquete_id=str(_uuid.uuid4()),
            archivos=[{"nota_id": nota_id, "bloque_id": str(nota.get("bloque_id")) if nota.get("bloque_id") else None,
                       "archivo_nombre": nombre_descarga, "lpu": nota.get("lpu"), "dni": nota.get("dni"),
                       "expediente_ppn": nota.get("nro_expediente_ppn") or nota.get("nro_expte"), "tipo_archivo": "ADJUNTO"}],
            agente={"id": usuario["id"], "nombre": usuario["nombre"]},
        )

    con_reporte = request.query_params.get("con_reporte", "0") == "1" and usuario.get("rol") in auth.ROLES_MESA
    if con_reporte:
        import reporte_descarga
        archivos_meta = [{
            "nota_id": nota["id"],
            "bloque_id": str(nota.get("bloque_id")) if nota.get("bloque_id") else None,
            "archivo_nombre": nombre_descarga,
            "lpu": nota.get("nro_nota") or nota.get("lpu"),
            "dni": nota.get("dni"),
            "expediente_ppn": nota.get("nro_expediente_ppn"),
            "tipo_archivo": tipo_archivo,
        }]
        agente = {"id": usuario["id"], "nombre": usuario["nombre"]}
        excel_bytes, excel_nombre = reporte_descarga.generar_excel_descarga(archivos_meta, agente)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(ruta), nombre_descarga)
            zf.writestr(excel_nombre, excel_bytes)
        zip_buf.seek(0)
        fecha_nombre = datetime.now().strftime("%Y%m%d")
        return StreamingResponse(
            iter([zip_buf.getvalue()]),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'nota_{nota_id}_{fecha_nombre}.zip')}"},
        )
    return FileResponse(ruta, filename=nombre_descarga)


@app.get("/nota/{nota_id}/descargar-original")
def descargar_original(request: Request, nota_id: int):
    """El repositorio histórico del asesor: siempre disponible, sin
    importar qué haya pasado con las correcciones o si la nota terminó
    rechazada (para quien tenga permiso para ver esa nota)."""
    usuario = usuario_actual(request)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if not _puede_ver_nota(usuario, nota):
        raise HTTPException(status_code=403, detail="No tenés permiso para descargar esta nota")
    ruta = Path(nota["ruta_original"])
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="El archivo original ya no existe en el servidor")
    db.registrar_acceso(nota_id, "DESCARGA_ORIGINAL")
    return FileResponse(ruta, filename=f"{Path(nota['nombre_original']).stem} - ORIGINAL{ruta.suffix}")


# =====================================================
# ENDPOINTS — APRENDIZAJE POR PATRONES
# =====================================================
def _reglas_ya_aprobadas():
    if not RUTA_REGLAS_PERSONALIZADAS.is_file():
        return frozenset()
    import json
    try:
        reglas = json.loads(RUTA_REGLAS_PERSONALIZADAS.read_text(encoding="utf-8"))
        return frozenset((r["quitada"], r["puesta"]) for r in reglas)
    except Exception:
        return frozenset()


def _periodos_informe():
    """Trimestres y años para el informe en borrador (el último cerrado primero)."""
    hoy = date.today()
    t = (hoy.month - 1) // 3 + 1
    salida = []
    a, q = hoy.year, t
    for _ in range(6):
        salida.append((f"{a}-T{q}", f"{['1.er', '2.º', '3.er', '4.º'][q - 1]} trimestre {a}" + (" (en curso)" if (a, q) == (hoy.year, t) else "")))
        q -= 1
        if q == 0:
            a, q = a - 1, 4
    salida += [(str(hoy.year), f"Año {hoy.year} (en curso)"), (str(hoy.year - 1), f"Año {hoy.year - 1}")]
    return salida


def _patrones_pendientes():
    """Reglas que el sistema aprendió de las correcciones del Director y que todavía nadie aprobó (reforma 15)."""
    return aprendizaje.analizar_patrones(db.obtener_modificaciones(), ya_aprobadas=_reglas_ya_aprobadas())


@app.get("/aprendizaje/patrones", response_class=HTMLResponse)
def ver_patrones(request: Request):
    _exigir_rol(usuario_actual(request))  # decisión de Dirección: cambia cómo corrige el sistema
    correcciones = db.obtener_modificaciones()
    patrones = aprendizaje.analizar_patrones(correcciones, ya_aprobadas=_reglas_ya_aprobadas())
    return templates.TemplateResponse(
        "patrones.html", {"request": request, "patrones": patrones, "total_modificaciones": len(correcciones),
                          "notas_existentes": {n["id"] for n in db.listar_notas()}}
    )


@app.post("/aprendizaje/patrones/agregar")
async def agregar_patron(request: Request):
    _exigir_rol(usuario_actual(request))  # una regla nueva se aplica a TODAS las notas
    import json
    form = await request.form()
    quitada = (form.get("quitada") or "").strip()
    puesta = (form.get("puesta") or "").strip()
    if not quitada or not puesta:
        raise HTTPException(status_code=422, detail="Falta la frase original o la nueva")
    db.registrar_uso_agente(None, "APRENDIZAJE", "ACEPTADA", usuario_actual(request)["nombre"], {"quitada": quitada, "puesta": puesta})

    reglas = []
    if RUTA_REGLAS_PERSONALIZADAS.is_file():
        try:
            reglas = json.loads(RUTA_REGLAS_PERSONALIZADAS.read_text(encoding="utf-8"))
        except Exception:
            reglas = []
    reglas.append({"quitada": quitada, "puesta": puesta})
    RUTA_REGLAS_PERSONALIZADAS.write_text(json.dumps(reglas, ensure_ascii=False, indent=2), encoding="utf-8")

    # Se suma también en caliente, sin esperar a reiniciar el servidor.
    nucleo.REEMPLAZOS[quitada] = puesta

    return RedirectResponse(url="/aprendizaje/patrones", status_code=303)


# =====================================================
# ENDPOINTS — ADMIN: PDFs ARCHIVADOS SIN DESCARGA
# =====================================================
@app.get("/admin/archivados-sin-descarga", response_class=HTMLResponse)
def listar_archivados_sin_descarga(request: Request):
    """Lista PDFs archivados automáticamente antes del circuito, pendientes de reasignar."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR)
    archivados = db.obtener_archivados_sin_descarga()
    return templates.TemplateResponse("admin_archivados_sin_descarga.html", {
        "request": request,
        "usuario": usuario,
        "archivados": archivados,
        "ok": request.query_params.get("ok", ""),
    })


@app.post("/admin/resetear-todos-los-archivados")
async def resetear_todos_los_archivados(request: Request):
    """Resetea de una vez todos los PDFs ARCHIVADA-sin-tipo al estado SUBIDA."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR)
    archivados = db.obtener_archivados_sin_descarga()
    for a in archivados:
        db.resetear_archivado_a_subida(a["id"], usuario["nombre"])
    _flash(request, f"✓ {len(archivados)} archivo(s) devueltos al inicio del circuito.")
    return RedirectResponse(url="/admin/sistema/respaldo", status_code=303)


@app.post("/admin/resetear-archivado/{nota_id}")
async def resetear_archivado(nota_id: int, request: Request):
    """Resetea un PDF archivado-sin-descarga al estado SUBIDA para que entre al circuito."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR)
    nota = db.obtener_nota(nota_id)
    if nota is None:
        raise HTTPException(status_code=404)
    if nota["estado"] != "ARCHIVADA" or nota.get("tipo"):
        raise HTTPException(status_code=409, detail="Este archivo no está en el estado esperado")
    db.resetear_archivado_a_subida(nota_id, usuario["nombre"])
    return RedirectResponse(url="/admin/archivados-sin-descarga?ok=1", status_code=303)


# =====================================================
# ENDPOINTS — MESA DE ENTRADAS: HISTORIAL DE DESCARGAS
# =====================================================
@app.get("/mesa/descargas", response_class=HTMLResponse)
def mesa_descargas(request: Request, fecha_desde: str = "", fecha_hasta: str = ""):
    """Historial de descargas realizadas por Mesa de Entradas."""
    usuario = usuario_actual(request)
    _exigir_rol(usuario, *auth.ROLES_MESA, auth.ROL_DIRECTOR, auth.ROL_SUBDIRECTOR)
    filtros = {}
    if fecha_desde:
        filtros["fecha_desde"] = fecha_desde
    if fecha_hasta:
        filtros["fecha_hasta"] = fecha_hasta + "T23:59:59"
    registros = db.listar_descargas_mesa(filtros)
    return templates.TemplateResponse("mesa_descargas.html", {
        "request": request,
        "usuario": usuario,
        "registros": registros,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
    })


# =====================================================
# ENDPOINTS — REPORTE
# =====================================================
def _equipo_del_reporte(usuario):
    """Dirección ve todo; un Coordinador, solo su equipo; el resto no accede."""
    _exigir_rol(usuario, auth.ROL_COORDINADOR)
    return None if auth.es_admin(usuario) else usuario["equipo_id"]


@app.get("/reportes", response_class=HTMLResponse)
def ver_reportes(request: Request, desde: str = None, hasta: str = None):
    usuario = usuario_actual(request)
    equipo_id = _equipo_del_reporte(usuario)
    reporte = reportes.generar_reporte(desde=desde, hasta=hasta, equipo_id=equipo_id)
    return templates.TemplateResponse(
        "reportes.html",
        {"request": request, "reporte": reporte, "desde": desde or "", "hasta": hasta or "",
         "ia": db.estadisticas_ia() if equipo_id is None else None,
         "etiquetas_trabajo": automatico.ETIQUETAS_TRABAJO,
         "solo_equipo": equipo_id is not None,
         "por_unidad": db.estadisticas_entidades("UNIDAD", desde, hasta, equipo_id),
         "tiempos_equipo": circuito.tiempos_por_equipo(desde, hasta, equipo_id),
         "periodos": _periodos_informe(),
         "por_pedido": db.estadisticas_entidades("PEDIDO", desde, hasta, equipo_id)},
    )


@app.get("/reportes.csv")
def descargar_reporte_csv(request: Request, desde: str = None, hasta: str = None):
    equipo_id = _equipo_del_reporte(usuario_actual(request))
    reporte = reportes.generar_reporte(desde=desde, hasta=hasta, equipo_id=equipo_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Métrica", "Valor"])
    for clave, valor in reporte.items():
        if clave == "muestras":
            continue
        writer.writerow([clave, valor])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_dgpdh.csv"},
    )


# ---------- Traslados y catálogo de unidades ----------
import traslados  # noqa: E402
import rutas_traslados  # noqa: E402
rutas_traslados.registrar(app, {
    "templates": templates, "usuario_actual": usuario_actual, "flash": _flash,
    "tiene_notas": _tiene_acceso_notas, "puede_gestionar_nota": _puede_gestionar_nota,
})
import rutas_operacion  # noqa: E402
rutas_operacion.registrar(app, {
    "templates": templates, "usuario_actual": usuario_actual, "flash": _flash, "tiene_notas": _tiene_acceso_notas,
})
import rutas_datos  # noqa: E402  (ola 5: datos, consultas, presentismo mensual, PIN y manual)
rutas_datos.registrar(app, {
    "templates": templates, "usuario_actual": usuario_actual, "flash": _flash, "tiene_notas": _tiene_acceso_notas,
    "puede_ver_nota": _puede_ver_nota,
})
import rutas_detenidos  # noqa: E402  (ABM de personas detenidas)
rutas_detenidos.registrar(app, {
    "templates": templates, "usuario_actual": usuario_actual, "flash": _flash, "tiene_notas": _tiene_acceso_notas,
    "puede_ver_nota": _puede_ver_nota,
})
import rutas_referencia  # noqa: E402  (documentos firmados de referencia para la IA)
rutas_referencia.registrar(app, {"templates": templates, "usuario_actual": usuario_actual, "flash": _flash})
import rutas_metas  # noqa: E402  (metas de gestión por equipo)
rutas_metas.registrar(app, {"templates": templates, "usuario_actual": usuario_actual, "flash": _flash})
import rutas_obstaculizaciones  # noqa: E402  (Registro de Obstaculizaciones, 14/11/2026)
rutas_obstaculizaciones.registrar(app, {"templates": templates, "usuario_actual": usuario_actual, "flash": _flash,
                                        "tiene_notas": _tiene_acceso_notas, "carpeta_datos": CARPETA_DATOS, "max_bytes": MAX_BYTES_SUBIDA})
import rutas_viajes  # noqa: E402  (Autos PPN y Metas: propuestas 54 a 97)
rutas_viajes.registrar(app, {"templates": templates, "usuario_actual": usuario_actual, "flash": _flash, "tiene_notas": _tiene_acceso_notas})
import rutas_circuito  # noqa: E402
rutas_circuito.registrar(app, {
    "templates": templates, "usuario_actual": usuario_actual, "flash": _flash, "tiene_notas": _tiene_acceso_notas,
    "puede_ver_nota": _puede_ver_nota, "puede_gestionar_nota": _puede_gestionar_nota,
    "filtrar_por_visibilidad": _filtrar_por_visibilidad, "anotar_prioridad_efectiva": _anotar_prioridad_efectiva,
    "antiguedad_etapa": _antiguedad_etapa, "exigir_rol": _exigir_rol, "guardar_numeros": _guardar_numeros,
    "guardar_subida": _guardar_subida, "ordenar_para_firma": _ordenar_para_firma, "firmar": _firmar,
    "carpeta_datos": CARPETA_DATOS, "datos_despacho": _datos_despacho,
})
import rutas_notas_ia  # noqa: E402  (área Notas e IA local: propuestas 1–53)
rutas_notas_ia.registrar(app, {"templates": templates, "usuario_actual": usuario_actual, "exigir_rol": _exigir_rol,
                                "clave_auditoria": _clave_auditoria, "puede_ver_nota": _puede_ver_nota, "tiene_notas": _tiene_acceso_notas,
                                "puede_gestionar_nota": _puede_gestionar_nota, "flash": _flash, "carpeta_datos": CARPETA_DATOS})
import rutas_ui  # noqa: E402  (interfaz y experiencia de usuario, propuestas 98–164)
rutas_ui.registrar(app, {"templates": templates, "usuario_actual": usuario_actual, "flash": _flash, "puede_ver_nota": _puede_ver_nota,
                         "exigir_rol": _exigir_rol, "arrancar_correccion": _arrancar_correccion,
                         "anotar_prioridad_efectiva": _anotar_prioridad_efectiva, "filtrar_por_visibilidad": _filtrar_por_visibilidad,
                         "tiene_notas": _tiene_acceso_notas, "tiene_autos": _tiene_acceso_autos, "config": _CONFIG})
