"""
database.py — Persistencia en SQLite para la app web.

Se usa sqlite3 de la librería estándar (sin dependencias extra) porque el
volumen esperado es bajo (una institución, un puñado de usuarios). Si más
adelante hace falta más concurrencia, migrar a PostgreSQL implica cambiar
solo este archivo.

El modelo ya incluye "equipo" desde esta primera etapa, aunque todavía no
haya login: todas las notas quedan asociadas al equipo por defecto hasta
que se implemente el login y la asignación real de usuarios a equipos.
"""
import logging
import re
import contextvars
import sqlite3
import time
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUTA_DB = Path(__file__).resolve().parent / "datos" / "dgpdh.db"
FTS_DISPONIBLE = False

ESTADOS_VALIDOS = (
    "BORRADOR",
    "SUBIDA",
    "PROCESANDO",
    "EN_REVISION",
    "PROCESADA",
    "ERROR",
    "FIRMADA",
    "DESPACHADA",
    "RECHAZADA",
    # Beta 01: PDF y duplicados
    "ARCHIVADA",   # PDF suelto: archivo directo (sin corrección, IA ni firma)
    "ADJUNTO",     # PDF dentro de un bloque: acompaña el despacho del bloque
    "A_CONFIRMAR", # PDF aprobado por Coordinación, pendiente de confirmación de Dirección para pasar a Mesa
    "DESCARTADA",  # descartado como duplicado (se conserva y se puede recuperar)
    "PARA_REMITIR",  # «Solo Mesa de Entradas»: sin control, coordinación ni firma; Mesa le pone número y lo remite
)
LISTAS_PARA_REMITIR = ("FIRMADA", "PARA_REMITIR", "ADJUNTO")  # lo que Mesa numera, prepara y despacha

# RECHAZADA es un rechazo OPERATIVO de la nota completa (el coordinador o el
# director deciden que no corresponde seguir adelante), no una corrección de
# redacción. Por eso queda, igual que ERROR, fuera de las 4 etapas visibles
# del flujo normal (ver flujo.py) y no se usa como señal para el módulo de
# aprendizaje de patrones (ver aprendizaje.py).

EQUIPO_POR_DEFECTO = "General"


# Usuario que está haciendo la acción en curso (lo fija AuthMiddleware en
# cada request). Así cada fila del historial y de auditoría queda con QUIÉN
# la hizo sin tener que pasarlo por todas las funciones. Fuera de un request
# (por ejemplo el trabajador nocturno) vale None y se registra "Sistema".
USUARIO_ACTUAL = contextvars.ContextVar("usuario_actual", default=None)
# Diagnóstico de lentitud: cuántas conexiones a la base abre un pedido y cuánto tiempo pasa adentro (lo anota «Pedido lento»).
MEDICION_PEDIDO = contextvars.ContextVar("medicion_pedido", default=None)


# Se marca cuando el Director General ejerce una función de Mesa de Entradas (excepcional): así cada registro
# del historial que deja esa acción lo dice, sin tener que acordarse en cada pantalla.
FUNCION_MESA_EXCEPCIONAL = contextvars.ContextVar("funcion_mesa_excepcional", default=False)


def _quien():
    u = USUARIO_ACTUAL.get()
    nombre = (u or {}).get("nombre") or "Sistema"
    if u and FUNCION_MESA_EXCEPCIONAL.get():
        nombre += " (excepcional: función de Mesa de Entradas)"
    return nombre


class ConflictoDeEstado(Exception):
    """La nota ya no está en el estado desde el que se pretendía cambiarla
    (otro usuario o un doble clic se adelantó)."""


def normalizar_nombre(nombre):
    """Clave para comparar nombres de personas: sin acentos, sin mayúsculas y con espacios simples."""
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", str(nombre or "")) if unicodedata.category(c) != "Mn")
    return " ".join(t.lower().split())


@contextmanager
def _conexion():
    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(RUTA_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # La app escribe desde varios hilos a la vez (requests, tareas de fondo y
    # el análisis nocturno). WAL permite leer mientras otro escribe, y
    # busy_timeout hace esperar en vez de fallar con "database is locked".
    con.execute("PRAGMA busy_timeout = 30000")
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        pass  # p. ej. una base en una unidad de red que no soporta WAL
    try:
        con.execute("PRAGMA cache_size = -16000")   # ~16 MB de caché por conexión
        con.execute("PRAGMA temp_store = MEMORY")
    except sqlite3.DatabaseError:
        pass
    inicio = time.monotonic()
    try:
        yield con
        con.commit()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            MEDIDOR["bloqueos"] += 1
        raise
    finally:
        con.close()
        ms = (time.monotonic() - inicio) * 1000
        medicion = MEDICION_PEDIDO.get()
        if medicion is not None:
            medicion["n"] += 1
            medicion["ms"] += ms
        MEDIDOR["conexiones"] += 1
        if ms > 1000:
            MEDIDOR["lentas"] += 1
        MEDIDOR["max_ms"] = max(MEDIDOR["max_ms"], round(ms))


# Ola 5 (reforma 98): cuánto le cuesta a SQLite la carga actual. Se muestra en Administración → Base de datos
# y sirve para decidir con datos si hace falta pasar a PostgreSQL (desde que arrancó la app).
MEDIDOR = {"conexiones": 0, "lentas": 0, "bloqueos": 0, "max_ms": 0}


def inicializar_db(antes_de_migrar=None):
    _crear_esquema_historico()
    # Migraciones numeradas (ADR-0003): auditoría única y todo cambio de esquema nuevo. Si una falla, la app arranca igual
    # con el esquema anterior y el error queda en el registro.
    try:
        from dgpdh.persistencia import migraciones
        hechas = migraciones.aplicar_pendientes(_conexion, antes_de_migrar=antes_de_migrar)
        if hechas:
            logging.info("Esquema actualizado: " + ", ".join(f"{m.numero:04d}_{m.nombre}" for m in hechas))
    except Exception:
        logging.exception("No se pudieron aplicar las migraciones del esquema")


def _crear_esquema_historico():
    with _conexion() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS equipos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT UNIQUE NOT NULL
            )
        """)
        # Login y permisos (ver auth.py para los roles). equipo_id es NULL
        # para Dirección General y Mesa de Entrada, que no pertenecen a un
        # Equipo sino a su propia unidad.
        con.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT UNIQUE NOT NULL,
                clave_hash TEXT NOT NULL,
                clave_salt TEXT NOT NULL,
                nombre TEXT NOT NULL,
                rol TEXT NOT NULL,
                equipo_id INTEGER REFERENCES equipos(id),
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT NOT NULL
            )
        """)
        # Bloques: grupos de documentos que deben despacharse juntos (por
        # ejemplo varias notas que comparten un informe y/o un proveído).
        # Cada documento sigue su circuito individual; el bloque solo
        # afecta al despacho (ver notas.bloque_id).
        con.execute("""
            CREATE TABLE IF NOT EXISTS bloques (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                equipo_id INTEGER NOT NULL REFERENCES equipos(id),
                creado_por INTEGER REFERENCES usuarios(id),
                creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre_original TEXT NOT NULL,
                equipo_id INTEGER NOT NULL REFERENCES equipos(id),
                tipo TEXT,
                es_nota INTEGER NOT NULL DEFAULT 0,
                estado TEXT NOT NULL DEFAULT 'SUBIDA',
                observaciones TEXT,
                cambios_json TEXT,
                propuestas_json TEXT,
                parrafos_json TEXT,
                ruta_original TEXT NOT NULL,
                ruta_resultado TEXT,
                ruta_firmada TEXT,
                fecha_nota TEXT,
                alerta TEXT,
                error TEXT,
                creado_en TEXT NOT NULL,
                actualizado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS historial_estados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL REFERENCES notas(id),
                estado TEXT NOT NULL,
                comentario TEXT,
                fecha TEXT NOT NULL
            )
        """)
        # Cada decisión del Director sobre un párrafo propuesto (aceptar,
        # modificar o rechazar). Es la materia prima del módulo de
        # aprendizaje por patrones (aprendizaje.py) — se guarda SIEMPRE,
        # independientemente de qué decisión haya sido, para tener el
        # registro completo; el análisis de patrones solo usa las
        # "modificado" (ver aprendizaje.py para el motivo).
        con.execute("""
            CREATE TABLE IF NOT EXISTS correcciones_director (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL REFERENCES notas(id),
                ubicacion TEXT NOT NULL,
                texto_original TEXT NOT NULL,
                texto_propuesto TEXT NOT NULL,
                texto_final TEXT NOT NULL,
                decision TEXT NOT NULL,
                fuentes TEXT,
                fecha TEXT NOT NULL
            )
        """)
        con.execute(
            "INSERT OR IGNORE INTO equipos (nombre) VALUES (?)",
            (EQUIPO_POR_DEFECTO,),
        )
        # Migración aditiva: en una base ya existente, CREATE TABLE IF NOT
        # EXISTS no agrega columnas nuevas a una tabla que ya estaba creada.
        # ruta_base es la copia de trabajo que arma el Coordinador al editar
        # el texto antes de aprobar (ver /nota/{id}/editar-base); el
        # original en ruta_original nunca se toca.
        try:
            con.execute("ALTER TABLE notas ADD COLUMN ruta_base TEXT")
        except sqlite3.OperationalError:
            pass  # ya existe (instalación no nueva)
        try:
            con.execute("ALTER TABLE notas ADD COLUMN fecha_nota TEXT")
        except sqlite3.OperationalError:
            pass  # ya existe (instalación no nueva)
        try:
            con.execute("ALTER TABLE notas ADD COLUMN alerta TEXT")
        except sqlite3.OperationalError:
            pass  # ya existe (instalación no nueva)
        # usuario_id: quién subió la nota (si se subió logueado). Las notas
        # cargadas antes de existir el login quedan con usuario_id NULL.
        try:
            con.execute("ALTER TABLE notas ADD COLUMN usuario_id INTEGER REFERENCES usuarios(id)")
        except sqlite3.OperationalError:
            pass  # ya existe (instalación no nueva)

        # prioridad: URGENTE / PRIORITARIA / NORMAL (ver auth.PRIORIDADES).
        # Las notas ya existentes quedan NORMAL.
        try:
            con.execute("ALTER TABLE notas ADD COLUMN prioridad TEXT NOT NULL DEFAULT 'NORMAL'")
        except sqlite3.OperationalError:
            pass  # ya existe (instalación no nueva)
        try:
            con.execute("ALTER TABLE notas ADD COLUMN bloque_id INTEGER REFERENCES bloques(id)")
        except sqlite3.OperationalError:
            pass  # ya existe (instalación no nueva)


        # --- Reformas 11–14: clasificación, consistencia, mejoras y análisis
        # automático. Todas son columnas/tablas nuevas, sin tocar lo existente.
        for columna in (
            "tema TEXT",                       # tema de la nota (auto o manual)
            "tema_origen TEXT",                # 'auto' | 'ia' | 'manual'
            "prioridad_sugerida TEXT",         # sugerencia del sistema (nunca pisa la del asesor)
            "prioridad_motivo TEXT",
            "prioridad_sugerida_estado TEXT",  # PENDIENTE | ACEPTADA | DESCARTADA
            "ref_nota_id INTEGER",             # documento de referencia elegido a mano
            "propuestas_previas_json TEXT",    # corrección precalculada de noche
            "propuestas_previas_hash TEXT",    # huella del texto con que se calculó
            "nro_nota TEXT",                   # número de nota (lo carga Mesa de Entrada)
            "nro_expte TEXT",                  # número de expediente
            "ruta_despacho_docx TEXT",         # copia del firmado con los números
            "ruta_despacho_pdf TEXT",
            "sha256_despacho TEXT",            # huella de la copia con números
            "despacho_preparado_en TEXT",
        ):
            try:
                con.execute(f"ALTER TABLE notas ADD COLUMN {columna}")
            except sqlite3.OperationalError:
                pass  # ya existe

        con.execute("""
            CREATE TABLE IF NOT EXISTS consistencia_hallazgos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL REFERENCES notas(id),
                bloque_id INTEGER,
                tipo TEXT NOT NULL,            -- DISCREPANCIA | FALTA | AVISO
                severidad TEXT NOT NULL,
                dato TEXT NOT NULL,
                detalle TEXT,
                valores_json TEXT NOT NULL,
                clave TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'ABIERTO',   -- ABIERTO | REVISADO | IGNORADO
                resuelto_por TEXT,
                resuelto_en TEXT,
                creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS mejoras_informe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL REFERENCES notas(id),
                ref_nota_id INTEGER,
                ubicacion TEXT NOT NULL,
                tipo TEXT NOT NULL,            -- COMPLETAR | PRECISAR | REDACCION
                original TEXT NOT NULL,
                propuesto TEXT NOT NULL,
                cita TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE',  -- PENDIENTE | ACEPTADA | DESCARTADA | OBSOLETA
                creado_en TEXT NOT NULL,
                resuelto_en TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS trabajos_ia (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL REFERENCES notas(id),
                tipo TEXT NOT NULL,            -- CLASIFICAR | CONSISTENCIA | MEJORAS | CORRECCION
                modelo TEXT,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE',  -- PENDIENTE | EN_CURSO | HECHO | ERROR | OMITIDO
                intentos INTEGER NOT NULL DEFAULT 0,
                creado_en TEXT NOT NULL,
                iniciado_en TEXT,
                terminado_en TEXT,
                resultado_json TEXT,
                error TEXT
            )
        """)


        # --- Auditoría y endurecimiento (mejoras de la auditoría 1–31)
        for tabla, columna in (
            ("usuarios", "debe_cambiar_clave INTEGER NOT NULL DEFAULT 0"),
            ("usuarios", "acceso_notas INTEGER NOT NULL DEFAULT 1"),       # ve el sistema de notas
            ("usuarios", "acceso_traslados INTEGER NOT NULL DEFAULT 1"),   # ve el sistema de traslados
            ("usuarios", "coordina_traslados INTEGER NOT NULL DEFAULT 0"),  # asigna choferes, gestiona flota, reportes
            ("usuarios", "personal INTEGER NOT NULL DEFAULT 0"),            # área de Personal: reporte semanal de presentismo
            ("usuarios", "pref_notas TEXT"),                                # cómo prefiere ver el listado de notas (JSON)
            ("usuarios", "circuito TEXT NOT NULL DEFAULT 'AMBA'"),           # AMBA | DELEGACIONES
            ("usuarios", "delegacion_id INTEGER"),                          # unidad donde presta servicio (circuito Delegaciones)
            ("usuarios", "avatar INTEGER NOT NULL DEFAULT 0"),              # 1 si subió imagen de perfil
            ("usuarios", "imagen_firma INTEGER NOT NULL DEFAULT 0"),        # 1 si subió imagen de firma personal
            ("historial_estados", "usuario TEXT"),
            ("notas", "ruta_analizada TEXT"),   # documento sobre el que se calcularon las propuestas en curso
            ("notas", "sha256_original TEXT"),
            ("notas", "sha256_resultado TEXT"),
            ("notas", "sha256_firmado TEXT"),
            ("notas", "version_de INTEGER"),    # id de la versión anterior de este documento
        ):
            try:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna}")
            except sqlite3.OperationalError:
                pass  # ya existe

        con.execute("""
            CREATE TABLE IF NOT EXISTS ajustes (
                clave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)
        for columna in ("verificacion TEXT", "origen TEXT", "modelo TEXT"):
            try:
                con.execute(f"ALTER TABLE consistencia_hallazgos ADD COLUMN {columna}")
            except sqlite3.OperationalError:
                pass  # ya existe
        for tabla, columna in (("notas", "vence TEXT"), ("notas", "escalada_en TEXT"), ("notas", "hash_texto TEXT"),
                               ("usuarios", "correo TEXT"), ("usuarios", "sso_id INTEGER"), ("equipos", "sso_area_id INTEGER"), ("notas", "resumen TEXT"), ("notas", "resumen_origen TEXT"), ("notas", "plazo_respuesta TEXT"), ("notas", "respuesta_recibida_en TEXT"), ("notas", "respuesta_vence TEXT"), ("notas", "respuesta_avisada TEXT"), ("notas", "recordada_en TEXT")):
            try:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna}")
            except sqlite3.OperationalError:
                pass  # ya existe
        # Personas del portal PPN que intentaron entrar sin usuario vinculado en esta app (Administración decide).
        con.execute("""
            CREATE TABLE IF NOT EXISTS sso_pendientes (
                sso_id INTEGER PRIMARY KEY,
                nombre TEXT, correo TEXT, area TEXT, rol_portal TEXT,
                intentos INTEGER NOT NULL DEFAULT 1,
                ultimo_intento TEXT NOT NULL
            )
        """)
        # Ola 2: datos extraídos del texto, resumen y sugerencias de los agentes (siempre las decide una persona).
        con.execute("""
            CREATE TABLE IF NOT EXISTS nota_entidades (
                nota_id INTEGER NOT NULL, tipo TEXT NOT NULL, valor TEXT NOT NULL, norm TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS ix_entidades_norm ON nota_entidades(tipo, norm)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_entidades_nota ON nota_entidades(nota_id)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sugerencias_ia (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER, agente TEXT NOT NULL, clave TEXT, contenido_json TEXT,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE', origen TEXT,
                creado_en TEXT NOT NULL, resuelto_por TEXT, resuelto_en TEXT
            )
        """)
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_sugerencias_clave ON sugerencias_ia(nota_id, agente, clave)")
        # Ola 3: numeración anual, respuestas recibidas, notas para entregar en un viaje y plazos por destinatario.
        con.execute("""
            CREATE TABLE IF NOT EXISTS numeracion (
                anio INTEGER NOT NULL, tipo TEXT NOT NULL, ultimo INTEGER NOT NULL DEFAULT 0, inicial INTEGER,
                actualizado_en TEXT, actualizado_por TEXT, PRIMARY KEY (anio, tipo)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS proveidos_numerados (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nota_id INTEGER, numero TEXT NOT NULL, medio TEXT, usuario TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS respuestas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nota_id INTEGER NOT NULL, fecha_recepcion TEXT NOT NULL,
                medio TEXT, remitente TEXT, resumen TEXT, archivo TEXT, nombre_archivo TEXT, cargado_por TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS viaje_documentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, viaje_id INTEGER NOT NULL, nota_id INTEGER NOT NULL,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE', asignado_por TEXT, creado_en TEXT NOT NULL,
                resuelto_por TEXT, resuelto_en TEXT, observacion TEXT, UNIQUE (viaje_id, nota_id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS plazos_destinatario (
                norm TEXT PRIMARY KEY, nombre TEXT, cantidad INTEGER NOT NULL, unidad TEXT NOT NULL DEFAULT 'dias',
                habiles INTEGER NOT NULL DEFAULT 1, usos INTEGER NOT NULL DEFAULT 1, actualizado_en TEXT
            )
        """)
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_sso ON usuarios(sso_id) WHERE sso_id IS NOT NULL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS comentarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL,
                usuario_id INTEGER,
                usuario TEXT,
                texto TEXT NOT NULL,
                creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS ausencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                desde TEXT NOT NULL,
                hasta TEXT NOT NULL,
                delegado_id INTEGER NOT NULL,
                creada_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS terminos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                termino TEXT NOT NULL,
                preferido TEXT,
                nota TEXT,
                activo INTEGER NOT NULL DEFAULT 1
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS modelos_nota (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                descripcion TEXT,
                archivo TEXT NOT NULL,
                creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS cola_correo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                asunto TEXT NOT NULL,
                cuerpo TEXT NOT NULL,
                url TEXT,
                creada_en TEXT NOT NULL,
                enviado INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS unidades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
                tipo TEXT,
                zona TEXT,
                ciudad TEXT,
                provincia TEXT,
                fuerza TEXT,
                correo TEXT,
                direccion TEXT,
                alias TEXT,
                activa INTEGER NOT NULL DEFAULT 1,
                creada_en TEXT NOT NULL,
                actualizada_en TEXT NOT NULL
            )
        """)
        try:
            con.execute("ALTER TABLE notas ADD COLUMN unidad_id INTEGER REFERENCES unidades(id)")
        except sqlite3.OperationalError:
            pass  # ya existe
        con.execute("""
            CREATE TABLE IF NOT EXISTS vehiculos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dominio TEXT NOT NULL UNIQUE COLLATE NOCASE,
                marca TEXT, modelo TEXT, anio INTEGER, combustible TEXT,
                capacidad INTEGER NOT NULL DEFAULT 4,
                estado TEXT NOT NULL DEFAULT 'DISPONIBLE',   -- DISPONIBLE | TALLER | FUERA_DE_CIRCULACION
                reservado_para TEXT,                          -- uso exclusivo (ej. «Procurador»): no se sugiere
                seguro_vence TEXT, vtv_vence TEXT, service_proximo TEXT, km INTEGER,
                observaciones TEXT,
                creado_en TEXT NOT NULL, actualizado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS choferes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                usuario_id INTEGER REFERENCES usuarios(id),
                legajo TEXT, telefono TEXT, email TEXT,
                licencia_categoria TEXT, licencia_vence TEXT,
                estado TEXT NOT NULL DEFAULT 'DISPONIBLE',   -- DISPONIBLE | LICENCIA | VACACIONES | ENFERMEDAD | COMISION | BAJA
                vehiculo_fijo_id INTEGER REFERENCES vehiculos(id),
                reservado_para TEXT,
                observaciones TEXT,
                creado_en TEXT NOT NULL, actualizado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS viajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                hora_salida TEXT, hora_regreso TEXT,
                unidad_id INTEGER REFERENCES unidades(id),
                destino_otro TEXT,
                motivo TEXT,
                modalidad TEXT NOT NULL,                      -- CHOFER | PROPIO
                estado TEXT NOT NULL,                         -- SOLICITADO | CONFIRMADO | CANCELADO
                inmediato INTEGER NOT NULL DEFAULT 0,
                motivo_inmediato TEXT,
                chofer_id INTEGER REFERENCES choferes(id),
                vehiculo_id INTEGER REFERENCES vehiculos(id),
                observaciones TEXT,
                reprogramaciones INTEGER NOT NULL DEFAULT 0,
                motivo_cancelacion TEXT,
                origen TEXT NOT NULL DEFAULT 'SISTEMA',       -- SISTEMA | IMPORTADO
                creado_por_id INTEGER, creado_por TEXT,
                creado_en TEXT NOT NULL, actualizado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS viaje_participantes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                viaje_id INTEGER NOT NULL REFERENCES viajes(id),
                usuario_id INTEGER REFERENCES usuarios(id),
                nombre TEXT NOT NULL,
                equipo_nombre TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS participantes_externos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                nombre_norm TEXT NOT NULL UNIQUE,             -- para no duplicar (sin acentos ni mayúsculas)
                area TEXT, observaciones TEXT,
                usuario_id INTEGER REFERENCES usuarios(id),   -- si después se le creó usuario (conserva el historial)
                activo INTEGER NOT NULL DEFAULT 1,
                creado_por TEXT, creado_en TEXT NOT NULL
            )
        """)
        for tabla, columna in (("viaje_participantes", "auto_propio INTEGER NOT NULL DEFAULT 0"),
                               ("viaje_participantes", "externo_id INTEGER REFERENCES participantes_externos(id)"),
                               ("viajes", "frecuente_id INTEGER"),          # viaje precargado desde un viaje frecuente
                               ("viajes", "circuito TEXT NOT NULL DEFAULT 'AMBA'"),
                               ("viajes", "tipo_actividad TEXT"),   # Delegaciones: ACTIVIDAD | COMISION | VISITA
                               ("unidades", "lat REAL"), ("unidades", "lng REAL"),
                               ("unidades", "telefono_guardia TEXT"), ("unidades", "horarios_visita TEXT"), ("unidades", "contactos TEXT"),
                               # fuerza a cargo elegida de la lista de fuerzas y datos de la base de unidades del Menú de la Procuración
                               ("unidades", "fuerza_id INTEGER"), ("unidades", "menu_id INTEGER"), ("unidades", "menu_codigo TEXT"),
                               ("unidades", "menu_rnct TEXT"), ("unidades", "director TEXT"), ("unidades", "delegacion_ppn TEXT"),
                               ("unidades", "a_cargo_dgpdh INTEGER"), ("unidades", "nombre_menu TEXT"),
                               # Ola 4: desplazamiento (salida del vehículo con varias paradas), horas reales y orden de parada
                               ("viajes", "desplazamiento_id INTEGER"), ("viajes", "orden_parada INTEGER"),
                               ("viajes", "hora_salida_real TEXT"), ("viajes", "hora_regreso_real TEXT"), ("viajes", "real_cargado_por TEXT")):
            try:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna}")
            except sqlite3.OperationalError:
                pass  # ya existe
        # Flota: cada control de mantenimiento (service, aceite, cubiertas, matafuego, VTV, seguro…) queda registrado.
        con.execute("""
            CREATE TABLE IF NOT EXISTS vehiculo_controles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehiculo_id INTEGER NOT NULL REFERENCES vehiculos(id),
                tipo TEXT NOT NULL,
                fecha TEXT NOT NULL,                 -- cuándo se hizo
                km INTEGER,                          -- kilometraje al hacerlo
                vence_fecha TEXT, vence_km INTEGER,  -- cuándo toca el próximo
                detalle TEXT, registrado_por TEXT, creado_en TEXT NOT NULL
            )
        """)
        # Flota: cargas de combustible, vales entregados y novedades mecánicas de cada vehículo.
        con.execute("""
            CREATE TABLE IF NOT EXISTS vehiculo_registros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehiculo_id INTEGER NOT NULL REFERENCES vehiculos(id),
                tipo TEXT NOT NULL,                  -- COMBUSTIBLE | VALES | NOVEDAD
                fecha TEXT NOT NULL, km INTEGER,
                litros REAL, importe REAL, cantidad INTEGER, chofer_id INTEGER REFERENCES choferes(id),
                gravedad TEXT, detalle TEXT,
                resuelta INTEGER NOT NULL DEFAULT 0, resuelta_en TEXT, resuelta_por TEXT, resolucion TEXT,
                registrado_por TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS ix_vehiculo_registros ON vehiculo_registros(vehiculo_id, fecha)")
        # Novedades mecánicas: estado (cargada → reportada → reparación solicitada → en reparación → reparada / no requiere) e historial.
        try:
            con.execute("ALTER TABLE vehiculo_registros ADD COLUMN estado_novedad TEXT")
        except sqlite3.OperationalError:
            pass  # ya existe
        con.execute("UPDATE vehiculo_registros SET estado_novedad = CASE resuelta WHEN 1 THEN 'REPARADA' ELSE 'CARGADA' END "
                    "WHERE tipo = 'NOVEDAD' AND estado_novedad IS NULL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS vehiculo_novedad_estados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                registro_id INTEGER NOT NULL REFERENCES vehiculo_registros(id),
                estado TEXT NOT NULL, nota TEXT, usuario TEXT, fecha TEXT NOT NULL
            )
        """)
        # Tablero de Dirección: entrevistas que informa cada persona que viajó (si debe cargarlas) y metas de viajes por equipo.
        for tabla, columna in (("viaje_participantes", "entrevistas INTEGER"), ("viaje_participantes", "entrevistas_por TEXT"),
                               ("viaje_participantes", "entrevistas_en TEXT"), ("usuarios", "carga_entrevistas INTEGER NOT NULL DEFAULT 0"),
                               ("viaje_participantes", "equipo_original TEXT"),
                               ("viaje_participantes", "ingreso_pabellones INTEGER")):  # 1 = ingresó a pabellones / sectores
            try:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna}")
            except sqlite3.OperationalError:
                pass  # ya existe
        # Equipos del historial (Excel de viajes migrado) vinculados a los equipos del sistema; el nombre original se conserva.
        con.execute("""
            CREATE TABLE IF NOT EXISTS equipos_alias (
                alias_norm TEXT PRIMARY KEY, alias TEXT NOT NULL,
                equipo_id INTEGER NOT NULL REFERENCES equipos(id),
                vinculado_por TEXT, vinculado_en TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS metas_viajes (
                equipo_id INTEGER NOT NULL REFERENCES equipos(id),
                anio INTEGER NOT NULL, trimestre INTEGER NOT NULL,   -- 1 a 4
                minimo INTEGER NOT NULL DEFAULT 0,
                actualizado_por TEXT, actualizado_en TEXT,
                PRIMARY KEY (equipo_id, anio, trimestre)
            )
        """)
        # Viajes frecuentes: plantilla (quiénes, adónde, qué días) que precarga los viajes «a confirmar».
        con.execute("""
            CREATE TABLE IF NOT EXISTS viajes_frecuentes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                circuito TEXT NOT NULL DEFAULT 'AMBA',
                unidad_id INTEGER REFERENCES unidades(id), destino_otro TEXT,
                dias TEXT NOT NULL,                           -- días de la semana, 0=lunes … 6=domingo, separados por coma
                frecuencia TEXT NOT NULL DEFAULT 'SEMANAL',   -- SEMANAL | QUINCENAL | MENSUAL
                desde TEXT NOT NULL, hasta TEXT,
                hora_salida TEXT, hora_regreso TEXT, motivo TEXT, observaciones TEXT,
                equipo_nombre TEXT,
                activo INTEGER NOT NULL DEFAULT 1,            -- 1 activo · 0 pausado · -1 eliminado
                creado_por_id INTEGER, creado_por TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS viaje_frecuente_participantes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frecuente_id INTEGER NOT NULL REFERENCES viajes_frecuentes(id),
                usuario_id INTEGER REFERENCES usuarios(id), externo_id INTEGER REFERENCES participantes_externos(id),
                nombre TEXT NOT NULL, equipo_nombre TEXT, auto_propio INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_viajes_frecuente_fecha ON viajes(frecuente_id, fecha) WHERE frecuente_id IS NOT NULL")
        # Personas sin usuario cargadas antes del catálogo (o importadas del Excel): se suman al catálogo una sola vez.
        for f in con.execute("SELECT id, nombre FROM viaje_participantes WHERE usuario_id IS NULL AND externo_id IS NULL").fetchall():
            norm = normalizar_nombre(f["nombre"])
            if not norm:
                continue
            ext = con.execute("SELECT id FROM participantes_externos WHERE nombre_norm = ?", (norm,)).fetchone()
            if ext:
                ext_id = ext["id"]
            else:
                ext_id = con.execute("INSERT INTO participantes_externos (nombre, nombre_norm, creado_por, creado_en) VALUES (?,?,?,?)",
                                     (f["nombre"].strip(), norm, "Sistema", _ahora())).lastrowid
            con.execute("UPDATE viaje_participantes SET externo_id = ? WHERE id = ?", (ext_id, f["id"]))
        con.execute("""
            CREATE TABLE IF NOT EXISTS equipo_unidades (
                equipo_id INTEGER NOT NULL REFERENCES equipos(id),
                unidad_id INTEGER NOT NULL REFERENCES unidades(id),
                orden INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (equipo_id, unidad_id)
            )
        """)
        # Ola 4: operación de traslados
        con.execute("""
            CREATE TABLE IF NOT EXISTS desplazamientos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, vehiculo_id INTEGER, chofer_id INTEGER,
                salida_real TEXT, regreso_real TEXT, km_salida INTEGER, km_regreso INTEGER, litros REAL, importe REAL,
                observaciones TEXT, cerrado_por TEXT, cerrado_en TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS ix_desplazamientos_fecha ON desplazamientos(fecha)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS viaje_novedades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, viaje_id INTEGER NOT NULL, tipo TEXT NOT NULL, detalle TEXT,
                minutos INTEGER, registrado_por TEXT, fecha TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS chofer_licencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chofer_id INTEGER NOT NULL, desde TEXT NOT NULL, hasta TEXT NOT NULL,
                tipo TEXT NOT NULL, observacion TEXT, cargado_por TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS vehiculo_reservas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, vehiculo_id INTEGER NOT NULL, fecha TEXT NOT NULL, desde_hora TEXT, hasta_hora TEXT,
                motivo TEXT NOT NULL, solicitante TEXT, chofer_id INTEGER, estado TEXT NOT NULL DEFAULT 'ACTIVA', cargado_por TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS viaje_espera (
                id INTEGER PRIMARY KEY AUTOINCREMENT, viaje_id INTEGER NOT NULL, usuario_id INTEGER, nombre TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'ESPERANDO', avisado_en TEXT, creado_en TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS traslados_auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entidad TEXT NOT NULL,                        -- viaje | chofer | vehiculo
                entidad_id INTEGER NOT NULL,
                accion TEXT NOT NULL,
                anterior TEXT, nuevo TEXT,
                usuario TEXT, fecha TEXT NOT NULL
            )
        """)
        # La auditoría de traslados no se puede modificar ni borrar (ni desde la app).
        for evento in ("UPDATE", "DELETE"):
            con.execute(f"""CREATE TRIGGER IF NOT EXISTS traslados_auditoria_{evento.lower()}
                            BEFORE {evento} ON traslados_auditoria
                            BEGIN SELECT RAISE(ABORT, 'La auditoría de traslados es inalterable'); END""")
        for indice in ("CREATE INDEX IF NOT EXISTS ix_viajes_fecha ON viajes(fecha)",
                       "CREATE INDEX IF NOT EXISTS ix_vp_viaje ON viaje_participantes(viaje_id)",
                       "CREATE INDEX IF NOT EXISTS ix_vp_usuario ON viaje_participantes(usuario_id)"):
            con.execute(indice)
        con.execute("""
            CREATE TABLE IF NOT EXISTS contactos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                correo TEXT,
                usos INTEGER NOT NULL DEFAULT 0,
                ultimo_uso TEXT,
                UNIQUE(nombre, correo)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS envios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL,
                medio TEXT NOT NULL,           -- CORREO | MANO
                destinatario TEXT,
                correo TEXT,
                observacion TEXT,
                usuario TEXT,
                fecha TEXT NOT NULL
            )
        """)
        # Ola 3 (van después de crear envios y ausencias, para que existan también en una base nueva)
        for tabla, columna in (("envios", "entregado_por TEXT"), ("envios", "viaje_id INTEGER"), ("envios", "fecha_entrega TEXT"),
                               ("envios", "recibido_por TEXT"), ("ausencias", "aviso_firma_en TEXT")):
            try:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna}")
            except sqlite3.OperationalError:
                pass
        con.execute("""
            CREATE TABLE IF NOT EXISTS consultas_expedientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                usuario TEXT,
                consulta TEXT NOT NULL,
                resultados INTEGER NOT NULL DEFAULT 0,
                nota_id INTEGER,
                fecha TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS revisiones_usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL, revisor TEXT, observaciones TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS intentos_login (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                ip TEXT,
                exito INTEGER NOT NULL,
                fecha TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS accesos_documentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER,
                usuario_id INTEGER,
                usuario TEXT,
                accion TEXT NOT NULL,      -- VISTA | DESCARGA | DESCARGA_ORIGINAL | DESCARGA_BLOQUE
                detalle TEXT,
                fecha TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS notificaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                texto TEXT NOT NULL,
                url TEXT,
                leida INTEGER NOT NULL DEFAULT 0,
                creada_en TEXT NOT NULL
            )
        """)
        # Falsos reportes de la IA marcados por personas (aprendizaje).
        con.execute("""
            CREATE TABLE IF NOT EXISTS feedback_ia (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                clave TEXT NOT NULL,
                dato TEXT,
                detalle TEXT,
                valores_json TEXT,
                motivo TEXT,
                usuario TEXT,
                nota_id INTEGER,
                creado_en TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1
            )
        """)
        # Avisos viejos que decían "no hay contradicción": se cierran solos.
        con.execute(
            """UPDATE consistencia_hallazgos SET estado = 'REVISADO', resuelto_por = 'Sistema (sin diferencias)', resuelto_en = ?
               WHERE estado = 'ABIERTO' AND tipo = 'AVISO'
                 AND (lower(detalle) LIKE '%no hay contradicci%' OR lower(detalle) LIKE '%sin contradicci%'
                      OR lower(detalle) LIKE '%no existe contradicci%' OR lower(detalle) LIKE '%no se encontr% contradicci%')""",
            (_ahora(),),
        )
        # Rechazos que cada persona ya abrió (para el contador de tareas).
        con.execute("""
            CREATE TABLE IF NOT EXISTS notas_vistas (
                usuario_id INTEGER NOT NULL,
                nota_id INTEGER NOT NULL,
                vista_en TEXT NOT NULL,
                PRIMARY KEY (usuario_id, nota_id)
            )
        """)
        # Texto de cada documento para la búsqueda. notas_texto siempre
        # existe (búsqueda simple); notas_fts (FTS5) se crea si la
        # instalación de SQLite la trae.
        con.execute("""
            CREATE TABLE IF NOT EXISTS notas_texto (
                nota_id INTEGER PRIMARY KEY REFERENCES notas(id),
                contenido TEXT NOT NULL
            )
        """)
        global FTS_DISPONIBLE
        try:
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notas_fts USING fts5("
                "contenido, tokenize='unicode61 remove_diacritics 2')"
            )
            FTS_DISPONIBLE = True
        except sqlite3.OperationalError:
            FTS_DISPONIBLE = False

        # Índices de las consultas frecuentes.
        for indice in (
            "CREATE INDEX IF NOT EXISTS ix_notas_estado ON notas(estado)",
            "CREATE INDEX IF NOT EXISTS ix_notas_equipo ON notas(equipo_id)",
            "CREATE INDEX IF NOT EXISTS ix_notas_bloque ON notas(bloque_id)",
            "CREATE INDEX IF NOT EXISTS ix_notas_usuario ON notas(usuario_id)",
            "CREATE INDEX IF NOT EXISTS ix_historial_nota ON historial_estados(nota_id)",
            "CREATE INDEX IF NOT EXISTS ix_hallazgos_nota ON consistencia_hallazgos(nota_id)",
            "CREATE INDEX IF NOT EXISTS ix_mejoras_nota ON mejoras_informe(nota_id)",
            "CREATE INDEX IF NOT EXISTS ix_trabajos_estado ON trabajos_ia(estado)",
            "CREATE INDEX IF NOT EXISTS ix_trabajos_nota ON trabajos_ia(nota_id)",
            "CREATE INDEX IF NOT EXISTS ix_correcciones_nota ON correcciones_director(nota_id)",
            "CREATE INDEX IF NOT EXISTS ix_intentos_usuario ON intentos_login(usuario, fecha)",
            "CREATE INDEX IF NOT EXISTS ix_notif_usuario ON notificaciones(usuario_id, leida)",
            "CREATE INDEX IF NOT EXISTS ix_accesos_fecha ON accesos_documentos(fecha)",
        ):
            con.execute(indice)

        # Primer arranque: el catálogo de unidades se carga con los nombres de la
        # planilla DATOS PPN (el resto de los campos se completa desde la pantalla
        # Unidades o importando un CSV).
        _sembrar_unidades(con)

        # Primer arranque: si no hay ningún usuario todavía, se crea un
        # Director General por defecto para poder entrar la primera vez y
        # dar de alta al resto desde /admin. La clave es intencionalmente
        # simple porque hay que cambiarla apenas se entra por primera vez.
        tiene_usuarios = con.execute("SELECT COUNT(*) AS c FROM usuarios").fetchone()["c"]
        if not tiene_usuarios:
            import auth
            hash_clave, salt = auth.generar_hash_clave("director123")
            con.execute(
                """INSERT INTO usuarios (usuario, clave_hash, clave_salt, nombre, rol, equipo_id, creado_en,
                                         debe_cambiar_clave)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, 1)""",
                ("director", hash_clave, salt, "Director General", auth.ROL_DIRECTOR, _ahora()),
            )
        else:
            # Una instalación anterior que todavía tiene la clave de fábrica
            # del Director: se le exige cambiarla en el próximo ingreso.
            import auth
            fila = con.execute(
                "SELECT id, clave_hash, clave_salt, debe_cambiar_clave FROM usuarios WHERE usuario = 'director'"
            ).fetchone()
            if fila and not fila["debe_cambiar_clave"] and auth.verificar_clave("director123", fila["clave_hash"], fila["clave_salt"]):
                con.execute("UPDATE usuarios SET debe_cambiar_clave = 1 WHERE id = ?", (fila["id"],))
        _inicializar_ola5(con)


def borrar_con_registro(con, tabla, donde, parametros=(), motivo=None):
    """Reemplazo de DELETE para datos cargados por personas: la fila completa queda en la auditoría antes de quitarla
    (regla «nada se borra físicamente»; ADR-0008). Usa a la persona de la sesión actual."""
    from dgpdh.seguridad import auditoria
    return auditoria.borrar_con_registro(con, tabla, donde, parametros, actor=auditoria.Actor.desde(USUARIO_ACTUAL.get()), motivo=motivo)


def _inicializar_ola5(con):
    """Ola 5 (datos, seguridad y capacitación): todo aditivo, sin tocar lo existente."""
    for columna in ("pin_hash TEXT", "pin_salt TEXT", "pin_creado_en TEXT"):
        try:
            con.execute(f"ALTER TABLE usuarios ADD COLUMN {columna}")
        except sqlite3.OperationalError:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS seguridad_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER, usuario TEXT, tipo TEXT NOT NULL, detalle TEXT, ip TEXT, fecha TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS personal_importaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, archivo TEXT, desde TEXT, hasta TEXT, filas INTEGER, sin_vincular INTEGER,
        usuario TEXT, creado_en TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS personal_novedades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, importacion_id INTEGER NOT NULL, persona TEXT NOT NULL, usuario_id INTEGER,
        fecha TEXT NOT NULL, novedad TEXT, clase TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS reportes_generados (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, periodo TEXT NOT NULL, archivo TEXT NOT NULL,
        creado_en TEXT NOT NULL, UNIQUE (tipo, periodo))""")
    con.execute("""CREATE TABLE IF NOT EXISTS salud_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chequeo TEXT NOT NULL, estado TEXT NOT NULL, detalle TEXT, fecha TEXT NOT NULL)""")
    for indice in (
        "CREATE INDEX IF NOT EXISTS ix_personal_nov_fecha ON personal_novedades(fecha, usuario_id)",
        "CREATE INDEX IF NOT EXISTS ix_seg_eventos ON seguridad_eventos(usuario_id, tipo, fecha)",
        "CREATE INDEX IF NOT EXISTS ix_salud_eventos ON salud_eventos(chequeo, fecha)",
        "CREATE INDEX IF NOT EXISTS ix_viajes_fecha ON viajes(fecha)",
        "CREATE INDEX IF NOT EXISTS ix_historial_fecha ON historial_estados(fecha)",
        "CREATE INDEX IF NOT EXISTS ix_notas_creado ON notas(creado_en)",
    ):
        con.execute(indice)
    import detenidos  # ABM de personas detenidas (tablas propias, aditivas)
    detenidos.inicializar(con)
    import metas  # metas de gestión por equipo (tablas propias, aditivas)
    metas.inicializar(con)
    import archivo_cargas  # archivo original de cada base cargada (sin modificar, con su huella)
    archivo_cargas.inicializar(con)
    import unidades_menu  # unidades compatibles con el Menú de la Procuración y fuerza a cargo de la lista
    unidades_menu.inicializar(con)
    import revision_ia  # revisión de la base de detenidos con IA local (solo propone)
    revision_ia.inicializar(con)
    try:  # permiso individual: mantenimiento de bases (Revisión de la base de detenidos)
        con.execute("ALTER TABLE usuarios ADD COLUMN mantiene_bases INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    _inicializar_ola6(con)


def _inicializar_ola6(con):
    """Ola 6: trazabilidad de la IA (137) y tipo documental confirmado por una persona (131). Aditivo."""
    import trazabilidad_ia
    trazabilidad_ia.inicializar(con)
    import soberania
    soberania.inicializar(con)
    import referencia_ia  # documentos firmados de referencia para la IA (tablas propias, aditivas)
    referencia_ia.inicializar(con)
    # Autos PPN: motivo de suspensión/baja del vehículo y pedido de revisión de la Dirección General cuando no hay vehículos.
    for tabla, columna in (("vehiculos", "estado_motivo TEXT"), ("viajes", "prioridad_pedida INTEGER NOT NULL DEFAULT 0"),
                           ("viajes", "motivo_prioridad TEXT"), ("viajes", "revision_direccion TEXT"), ("viajes", "revision_por TEXT"),
                           ("viajes", "revision_en TEXT"), ("viajes", "revision_comentario TEXT"), ("viajes", "unificado_en INTEGER")):
        try:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna}")
        except sqlite3.OperationalError:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS notas_reasignaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nota_id INTEGER NOT NULL, campo TEXT NOT NULL, antes TEXT, despues TEXT,
        usuario TEXT, fecha TEXT NOT NULL)""")
    for columna in ("tipo_documental TEXT", "tipo_documental_origen TEXT", "tipo_documental_por TEXT",
                    "duplicado_de INTEGER", "estado_previo TEXT", "modalidad TEXT"):
        try:
            con.execute(f"ALTER TABLE notas ADD COLUMN {columna}")
        except sqlite3.OperationalError:
            pass
    _inicializar_ola7(con)


def _inicializar_ola7(con):
    """Ola 7: registro de descargas de Mesa de Entradas. Aditivo."""
    con.execute("""CREATE TABLE IF NOT EXISTS descargas_mesa (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paquete_id TEXT NOT NULL,
        nota_id INTEGER,
        bloque_id TEXT,
        archivo_nombre TEXT NOT NULL,
        lpu TEXT,
        dni TEXT,
        expediente_ppn TEXT,
        agente_id INTEGER NOT NULL,
        agente_nombre TEXT NOT NULL,
        fecha_descarga TEXT NOT NULL,
        tipo_archivo TEXT NOT NULL,
        creado_en TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (agente_id) REFERENCES usuarios(id)
    )""")
    for indice in (
        "CREATE INDEX IF NOT EXISTS idx_descargas_mesa_paquete ON descargas_mesa(paquete_id)",
        "CREATE INDEX IF NOT EXISTS idx_descargas_mesa_fecha ON descargas_mesa(fecha_descarga)",
        "CREATE INDEX IF NOT EXISTS idx_descargas_mesa_nota ON descargas_mesa(nota_id)",
    ):
        con.execute(indice)
    _inicializar_ola8(con)


def _inicializar_ola8(con):
    """Ola 8: motor de coincidencias documento-detenido-expediente. Aditivo."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS nota_coincidencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nota_id INTEGER NOT NULL REFERENCES notas(id),
            detenido_id INTEGER REFERENCES detenidos(id),
            expte_propuesto TEXT,
            fuente_expte TEXT,
            nivel INTEGER NOT NULL,
            confianza INTEGER NOT NULL,
            motivo TEXT,
            datos_doc TEXT,
            algoritmo TEXT,
            estado TEXT NOT NULL DEFAULT 'PROPUESTO',
            usuario_decision TEXT,
            usuario_decision_id INTEGER,
            fecha TEXT NOT NULL,
            fecha_decision TEXT,
            UNIQUE(nota_id, COALESCE(detenido_id, -1))
        )
    """)
    for idx in (
        "CREATE INDEX IF NOT EXISTS ix_nc_nota ON nota_coincidencias(nota_id)",
        "CREATE INDEX IF NOT EXISTS ix_nc_detenido ON nota_coincidencias(detenido_id)",
        "CREATE INDEX IF NOT EXISTS ix_nc_estado ON nota_coincidencias(nota_id, estado)",
    ):
        con.execute(idx)


def tiene_texto_indexado(nota_id: int) -> bool:
    with _conexion() as con:
        row = con.execute(
            "SELECT 1 FROM notas_texto WHERE nota_id=? AND contenido IS NOT NULL AND length(trim(contenido))>50",
            (nota_id,),
        ).fetchone()
    return row is not None


def listar_coincidencias(nota_id: int) -> list:
    with _conexion() as con:
        rows = con.execute("""
            SELECT nc.*, d.nombre_completo, d.lpu, d.dni, d.nro_expediente_ppn,
                   u.nombre AS unidad_nombre
            FROM nota_coincidencias nc
            LEFT JOIN detenidos d ON d.id = nc.detenido_id
            LEFT JOIN unidades u ON u.id = d.unidad_id
            WHERE nc.nota_id = ? AND nc.estado != 'REEMPLAZADA'
            ORDER BY nc.confianza DESC, nc.nivel ASC
        """, (nota_id,)).fetchall()
    return [dict(r) for r in rows]


def guardar_coincidencia(nota_id: int, detenido_id, expte_propuesto, fuente_expte,
                          nivel: int, confianza: int, motivo: str, datos_doc: str,
                          algoritmo: str = "v1") -> None:
    from datetime import datetime as _dt
    with _conexion() as con:
        con.execute("""
            INSERT INTO nota_coincidencias
                (nota_id, detenido_id, expte_propuesto, fuente_expte, nivel, confianza,
                 motivo, datos_doc, algoritmo, estado, fecha)
            VALUES (?,?,?,?,?,?,?,?,?,'PROPUESTO',?)
            ON CONFLICT(nota_id, COALESCE(detenido_id, -1)) DO UPDATE SET
                expte_propuesto=excluded.expte_propuesto, fuente_expte=excluded.fuente_expte,
                nivel=excluded.nivel, confianza=excluded.confianza, motivo=excluded.motivo,
                datos_doc=excluded.datos_doc, algoritmo=excluded.algoritmo, fecha=excluded.fecha,
                estado=CASE WHEN nota_coincidencias.estado IN ('CONFIRMADO','DESCARTADO')
                            THEN nota_coincidencias.estado ELSE 'PROPUESTO' END
        """, (nota_id, detenido_id, expte_propuesto, fuente_expte, nivel, confianza,
              motivo, datos_doc, algoritmo, _dt.now().isoformat()))


def confirmar_coincidencia(coincidencia_id: int, usuario_nombre: str, usuario_id: int) -> None:
    from datetime import datetime as _dt
    with _conexion() as con:
        con.execute("""UPDATE nota_coincidencias SET estado='CONFIRMADO',
            usuario_decision=?, usuario_decision_id=?, fecha_decision=? WHERE id=?""",
            (usuario_nombre, usuario_id, _dt.now().isoformat(), coincidencia_id))


def descartar_coincidencia(coincidencia_id: int, usuario_nombre: str, usuario_id: int) -> None:
    from datetime import datetime as _dt
    with _conexion() as con:
        con.execute("""UPDATE nota_coincidencias SET estado='DESCARTADO',
            usuario_decision=?, usuario_decision_id=?, fecha_decision=? WHERE id=?""",
            (usuario_nombre, usuario_id, _dt.now().isoformat(), coincidencia_id))


def reemplazar_coincidencias_propuestas(nota_id: int) -> None:
    with _conexion() as con:
        con.execute("""UPDATE nota_coincidencias SET estado='REEMPLAZADA'
            WHERE nota_id=? AND estado='PROPUESTO'""", (nota_id,))


def aprendizaje_coincidencias(detenido_id: int) -> int:
    with _conexion() as con:
        row = con.execute(
            "SELECT COUNT(*) n FROM nota_coincidencias WHERE detenido_id=? AND estado='CONFIRMADO'",
            (detenido_id,),
        ).fetchone()
    return row["n"] if row else 0


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def obtener_equipo_por_defecto_id():
    with _conexion() as con:
        fila = con.execute(
            "SELECT id FROM equipos WHERE nombre = ?", (EQUIPO_POR_DEFECTO,)
        ).fetchone()
        return fila["id"]


def crear_nota(nombre_original, equipo_id, ruta_original, fecha_nota=None, usuario_id=None, prioridad="NORMAL", version_de=None, borrador=False):
    """`fecha_nota` es la fecha administrativa de la nota (la que va a
    figurar en el documento/expediente), no necesariamente la del momento en
    que se sube el archivo. Si no se indica, se usa la fecha de hoy.
    `usuario_id` es quién la subió (logueado); puede ser None en notas
    subidas antes de que existiera el login. `prioridad` es opcional: si no
    se indica (o no es válida), la nota queda NORMAL. Con `borrador=True` la
    nota queda "sin enviar": el asesor puede armar bloques, cambiar
    prioridades y corregir antes de mandarla a Coordinación (ver
    enviar_a_coordinacion); Coordinación no la ve ni recibe aviso hasta entonces."""
    ahora = _ahora()
    estado_inicial = "BORRADOR" if borrador else "SUBIDA"
    # La fecha de la nota se define al firmar (ver /nota/{id}/firmar): al
    # subir el archivo queda vacía.
    if prioridad not in ("URGENTE", "PRIORITARIA", "NORMAL"):
        prioridad = "NORMAL"
    with _conexion() as con:
        cur = con.execute(
            """INSERT INTO notas
               (nombre_original, equipo_id, estado, ruta_original, fecha_nota, usuario_id, prioridad, version_de, creado_en, actualizado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nombre_original, equipo_id, estado_inicial, str(ruta_original), fecha_nota, usuario_id, prioridad, version_de, ahora, ahora),
        )
        nota_id = cur.lastrowid
        con.execute(
            "INSERT INTO historial_estados (nota_id, estado, comentario, fecha, usuario) VALUES (?, ?, ?, ?, ?)",
            (nota_id, estado_inicial, ("Archivo subido (sin enviar a Coordinación)" if borrador else "Archivo subido") if not version_de
             else f"Nueva versión del documento {version_de}", ahora, _quien()),
        )
        if not borrador:
            _notificar_por_estado(con, nota_id, "SUBIDA")
        return nota_id


def enviar_a_coordinacion(nota_id):
    """BORRADOR -> SUBIDA. Recién acá Coordinación la ve y recibe el aviso."""
    actualizar_estado(nota_id, "SUBIDA", comentario="Enviada a Coordinación", desde=("BORRADOR",))


def avisar_a_coordinacion(nota_id, texto):
    """Aviso a los Coordinadores del equipo de la nota (p. ej. cuando el
    asesor corrige el texto mientras espera la evaluación)."""
    import auth
    with _conexion() as con:
        nota = con.execute("SELECT equipo_id FROM notas WHERE id = ?", (nota_id,)).fetchone()
        if nota is None:
            return
        ids = [f["id"] for f in con.execute(
            "SELECT id FROM usuarios WHERE activo = 1 AND rol = ? AND equipo_id = ?", (auth.ROL_COORDINADOR, nota["equipo_id"])
        ).fetchall()]
        _notificar(con, ids, texto, f"/nota/{nota_id}")


# ---------------------------------------------------------------------
# USUARIOS Y EQUIPOS (login y permisos — ver auth.py)
# ---------------------------------------------------------------------
def crear_equipo(nombre):
    with _conexion() as con:
        cur = con.execute("INSERT INTO equipos (nombre) VALUES (?)", (nombre,))
        return cur.lastrowid


def listar_equipos():
    with _conexion() as con:
        filas = con.execute("SELECT * FROM equipos ORDER BY nombre").fetchall()
        return [dict(f) for f in filas]


def crear_usuario(usuario, clave_hash, clave_salt, nombre, rol, equipo_id=None, debe_cambiar_clave=True, acceso_notas=True):
    """Las cuentas nuevas nacen con clave provisoria: la persona tiene que
    cambiarla en su primer ingreso."""
    with _conexion() as con:
        cur = con.execute(
            """INSERT INTO usuarios (usuario, clave_hash, clave_salt, nombre, rol, equipo_id, creado_en, debe_cambiar_clave, acceso_notas)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (usuario, clave_hash, clave_salt, nombre, rol, equipo_id, _ahora(), 1 if debe_cambiar_clave else 0, 1 if acceso_notas else 0),
        )
        return cur.lastrowid


def obtener_usuario(usuario_id):
    with _conexion() as con:
        fila = con.execute(
            """SELECT u.*, e.nombre AS equipo_nombre FROM usuarios u
               LEFT JOIN equipos e ON e.id = u.equipo_id WHERE u.id = ?""",
            (usuario_id,),
        ).fetchone()
        return dict(fila) if fila else None


def obtener_usuario_por_nombre(usuario):
    with _conexion() as con:
        fila = con.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        return dict(fila) if fila else None


def usuario_por_sso_id(sso_id):
    with _conexion() as con:
        fila = con.execute("SELECT id FROM usuarios WHERE sso_id = ?", (sso_id,)).fetchone()
    return obtener_usuario(fila["id"]) if fila else None


def usuario_por_correo(correo):
    """Usuario con ese correo (sin distinguir mayúsculas). Si hubiera dos, no se elige ninguno."""
    with _conexion() as con:
        filas = con.execute("SELECT id FROM usuarios WHERE lower(trim(correo)) = lower(trim(?))", (correo or "",)).fetchall()
    return obtener_usuario(filas[0]["id"]) if len(filas) == 1 and correo else None


def vincular_sso(usuario_id, sso_id, correo=None):
    """Vincula un usuario de esta app con la persona del portal PPN (sso_id=None lo desvincula)."""
    with _conexion() as con:
        if sso_id is not None:
            con.execute("UPDATE usuarios SET sso_id = NULL WHERE sso_id = ? AND id != ?", (sso_id, usuario_id))
        con.execute("UPDATE usuarios SET sso_id = ? WHERE id = ?", (sso_id, usuario_id))
        if correo:
            con.execute("UPDATE usuarios SET correo = ? WHERE id = ? AND (correo IS NULL OR trim(correo) = '')", (correo, usuario_id))
        if sso_id is not None:
            con.execute("DELETE FROM sso_pendientes WHERE sso_id = ?", (sso_id,))


def fijar_area_portal(equipo_id, area_id):
    """Área del portal PPN que corresponde a un equipo (preparación para vincular; None = sin asignar)."""
    with _conexion() as con:
        con.execute("UPDATE equipos SET sso_area_id = ? WHERE id = ?", (area_id, equipo_id))


def numeros_expediente_cargados():
    """Números de expediente distintos que figuran en las notas (para ver su compatibilidad con Mesa de Entradas)."""
    with _conexion() as con:
        return [f[0] for f in con.execute(
            "SELECT DISTINCT trim(nro_expte) FROM notas WHERE nro_expte IS NOT NULL AND trim(nro_expte) != ''").fetchall()]


def registrar_sso_pendiente(p):
    with _conexion() as con:
        con.execute(
            """INSERT INTO sso_pendientes (sso_id, nombre, correo, area, rol_portal, ultimo_intento) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(sso_id) DO UPDATE SET nombre=excluded.nombre, correo=excluded.correo, area=excluded.area,
                   rol_portal=excluded.rol_portal, intentos=intentos+1, ultimo_intento=excluded.ultimo_intento""",
            (p["sso_id"], p.get("nombre"), p.get("correo"), p.get("area"), p.get("rol_portal"), _ahora()))


def listar_sso_pendientes():
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM sso_pendientes ORDER BY ultimo_intento DESC").fetchall()]


def descartar_sso_pendiente(sso_id):
    with _conexion() as con:
        con.execute("DELETE FROM sso_pendientes WHERE sso_id = ?", (sso_id,))


def listar_usuarios():
    with _conexion() as con:
        filas = con.execute(
            """SELECT u.*, e.nombre AS equipo_nombre FROM usuarios u
               LEFT JOIN equipos e ON e.id = u.equipo_id ORDER BY u.rol, u.nombre"""
        ).fetchall()
        return [dict(f) for f in filas]


def desactivar_usuario(usuario_id):
    with _conexion() as con:
        con.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario_id,))


def cambiar_clave_usuario(usuario_id, clave_hash, clave_salt, debe_cambiar=False):
    """`debe_cambiar=True` se usa cuando un administrador asigna una clave
    provisoria: la persona tiene que cambiarla en su próximo ingreso."""
    with _conexion() as con:
        con.execute(
            "UPDATE usuarios SET clave_hash = ?, clave_salt = ?, debe_cambiar_clave = ? WHERE id = ?",
            (clave_hash, clave_salt, 1 if debe_cambiar else 0, usuario_id),
        )


def reactivar_usuario(usuario_id):
    with _conexion() as con:
        con.execute("UPDATE usuarios SET activo = 1 WHERE id = ?", (usuario_id,))


def guardar_pref_notas(usuario_id, pref):
    """Recuerda cómo quiere ver cada persona el listado de notas (qué ver y cómo agrupar)."""
    import json as _json
    with _conexion() as con:
        con.execute("UPDATE usuarios SET pref_notas = ? WHERE id = ?", (_json.dumps(pref), usuario_id))


def actualizar_usuario(usuario_id, nombre, rol, equipo_id, acceso_notas=None, coordina_traslados=None, circuito=None, delegacion_id=-1, personal=None,
                       acceso_traslados=None, carga_entrevistas=None, mantiene_bases=None):
    with _conexion() as con:
        con.execute(
            "UPDATE usuarios SET nombre = ?, rol = ?, equipo_id = ? WHERE id = ?",
            (nombre, rol, equipo_id, usuario_id),
        )
        if acceso_notas is not None:
            con.execute("UPDATE usuarios SET acceso_notas = ? WHERE id = ?", (1 if acceso_notas else 0, usuario_id))
        if coordina_traslados is not None:
            con.execute("UPDATE usuarios SET coordina_traslados = ? WHERE id = ?", (1 if coordina_traslados else 0, usuario_id))
        if personal is not None:
            con.execute("UPDATE usuarios SET personal = ? WHERE id = ?", (1 if personal else 0, usuario_id))
        if acceso_traslados is not None:
            con.execute("UPDATE usuarios SET acceso_traslados = ? WHERE id = ?", (1 if acceso_traslados else 0, usuario_id))
        if carga_entrevistas is not None:
            con.execute("UPDATE usuarios SET carga_entrevistas = ? WHERE id = ?", (1 if carga_entrevistas else 0, usuario_id))
        if mantiene_bases is not None:
            con.execute("UPDATE usuarios SET mantiene_bases = ? WHERE id = ?", (1 if mantiene_bases else 0, usuario_id))
        if circuito in ("AMBA", "DELEGACIONES"):
            con.execute("UPDATE usuarios SET circuito = ? WHERE id = ?", (circuito, usuario_id))
        if delegacion_id != -1:  # None = sin delegación
            con.execute("UPDATE usuarios SET delegacion_id = ? WHERE id = ?", (delegacion_id, usuario_id))


# ---------------------------------------------------------------------
# REGISTROS EN EL HISTORIAL QUE NO SON CAMBIOS DE ESTADO
# ---------------------------------------------------------------------
def registrar_evento(nota_id, comentario):
    """Deja una línea en el historial (cambio de prioridad, armado de
    bloque, etc.) SIN repetir el estado de la nota: se guarda con el estado
    especial "REGISTRO". reportes.py calcula los tiempos a partir de los
    estados reales (SUBIDA, PROCESADA, FIRMADA...), y repetir uno de esos
    estados acá desfasaría esos tiempos."""
    with _conexion() as con:
        con.execute(
            "INSERT INTO historial_estados (nota_id, estado, comentario, fecha, usuario) VALUES (?, ?, ?, ?, ?)",
            (nota_id, "REGISTRO", comentario, _ahora(), _quien()),
        )


def actualizar_prioridad(nota_id, prioridad, comentario):
    with _conexion() as con:
        con.execute("UPDATE notas SET prioridad = ? WHERE id = ?", (prioridad, nota_id))
    registrar_evento(nota_id, comentario)


# ---------------------------------------------------------------------
# BLOQUES (documentos que se despachan juntos)
# ---------------------------------------------------------------------
def crear_bloque(nombre, equipo_id, creado_por, nota_ids):
    """Crea un bloque y le asigna las notas indicadas. Si `nombre` viene
    vacío se arma "Bloque N" con el id recién creado."""
    with _conexion() as con:
        cur = con.execute(
            "INSERT INTO bloques (nombre, equipo_id, creado_por, creado_en) VALUES (?, ?, ?, ?)",
            (nombre or "pendiente", equipo_id, creado_por, _ahora()),
        )
        bloque_id = cur.lastrowid
        if not nombre:
            con.execute("UPDATE bloques SET nombre = ? WHERE id = ?", (f"Bloque {bloque_id}", bloque_id))
        for nid in nota_ids:
            con.execute("UPDATE notas SET bloque_id = ? WHERE id = ?", (bloque_id, nid))
    for nid in nota_ids:
        registrar_evento(nid, f"Agregada al bloque {bloque_id}")
        ajustar_estado_pdf(nid)
    return bloque_id


def obtener_bloque(bloque_id):
    with _conexion() as con:
        fila = con.execute("SELECT * FROM bloques WHERE id = ?", (bloque_id,)).fetchone()
        if fila is None:
            return None
        bloque = dict(fila)
        notas = con.execute(
            """SELECT n.*, u.nombre AS asesor_nombre FROM notas n
               LEFT JOIN usuarios u ON u.id = n.usuario_id
               WHERE n.bloque_id = ? ORDER BY n.id""",
            (bloque_id,),
        ).fetchall()
        bloque["notas"] = [dict(n) for n in notas]
        return bloque


def listar_bloques(equipo_id=None):
    with _conexion() as con:
        if equipo_id is None:
            filas = con.execute("SELECT * FROM bloques ORDER BY id DESC").fetchall()
        else:
            filas = con.execute(
                "SELECT * FROM bloques WHERE equipo_id = ? ORDER BY id DESC", (equipo_id,)
            ).fetchall()
        return [dict(f) for f in filas]


def agregar_nota_a_bloque(nota_id, bloque_id):
    with _conexion() as con:
        con.execute("UPDATE notas SET bloque_id = ? WHERE id = ?", (bloque_id, nota_id))
    registrar_evento(nota_id, f"Agregada al bloque {bloque_id}")
    ajustar_estado_pdf(nota_id)


def quitar_nota_de_bloque(nota_id):
    """Saca la nota de su bloque. Si el bloque queda con menos de 2
    documentos, deja de tener sentido y se disuelve (el documento que
    quedaba también se libera)."""
    with _conexion() as con:
        fila = con.execute("SELECT bloque_id FROM notas WHERE id = ?", (nota_id,)).fetchone()
        bloque_id = fila["bloque_id"] if fila else None
        if bloque_id is None:
            return
        con.execute("UPDATE notas SET bloque_id = NULL WHERE id = ?", (nota_id,))
        restantes = [r["id"] for r in con.execute("SELECT id FROM notas WHERE bloque_id = ?", (bloque_id,))]
        if len(restantes) < 2:
            con.execute("UPDATE notas SET bloque_id = NULL WHERE bloque_id = ?", (bloque_id,))
            borrar_con_registro(con, "bloques", "id = ?", (bloque_id,))
    registrar_evento(nota_id, f"Quitada del bloque {bloque_id}")
    ajustar_estado_pdf(nota_id)
    if len(restantes) < 2:
        for rid in restantes:
            registrar_evento(rid, f"Bloque {bloque_id} disuelto (quedaba un solo documento)")
            ajustar_estado_pdf(rid)


def actualizar_estado(nota_id, estado, comentario=None, desde=None, **campos):
    """Cambia el estado de la nota y lo deja en el historial (con quién lo
    hizo). Si se indica `desde` (tupla de estados), el cambio solo se hace
    si la nota SIGUE en alguno de esos estados: la verificación y el cambio
    son una sola operación, así un doble clic o dos usuarios a la vez no
    pueden aplicar la misma transición dos veces (levanta ConflictoDeEstado)."""
    if estado not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado inválido: {estado}")
    ahora = _ahora()
    campos["estado"] = estado
    campos["actualizado_en"] = ahora
    columnas = ", ".join(f"{k} = ?" for k in campos)
    valores = list(campos.values()) + [nota_id]
    sql = f"UPDATE notas SET {columnas} WHERE id = ?"
    if desde:
        sql += " AND estado IN (" + ", ".join("?" for _ in desde) + ")"
        valores += list(desde)
    elif estado != "DESCARTADA":
        # Una nota descartada solo sale de ahí al recuperarla (con desde=("DESCARTADA",)): así un análisis que
        # termina después de descartarla no la vuelve a poner en circuito.
        sql += " AND estado != 'DESCARTADA'"
    with _conexion() as con:
        cur = con.execute(sql, valores)
        if cur.rowcount == 0:
            if desde:
                raise ConflictoDeEstado(
                    "Otra persona ya cambió el estado de esta nota. Recargá la página para ver cómo quedó."
                )
            return
        con.execute(
            "INSERT INTO historial_estados (nota_id, estado, comentario, fecha, usuario) VALUES (?, ?, ?, ?, ?)",
            (nota_id, estado, comentario, ahora, _quien()),
        )
        _notificar_por_estado(con, nota_id, estado)


# ---------------------------------------------------------------------
# NOTIFICACIONES INTERNAS
# ---------------------------------------------------------------------
def _notificar(con, usuarios_ids, texto, url):
    actor = (USUARIO_ACTUAL.get() or {}).get("id")
    for uid in set(usuarios_ids):
        if uid == actor:
            continue  # nadie necesita un aviso de lo que acaba de hacer él mismo
        existe = con.execute(
            "SELECT 1 FROM notificaciones WHERE usuario_id = ? AND url = ? AND texto = ? AND leida = 0",
            (uid, url, texto),
        ).fetchone()
        if not existe:
            con.execute(
                "INSERT INTO notificaciones (usuario_id, texto, url, creada_en) VALUES (?, ?, ?, ?)",
                (uid, texto, url, _ahora()),
            )
            _encolar_correo(con, uid, texto, url)
            # Si la persona está ausente, el aviso también le llega a quien delegó.
            aus = _ausencia_vigente(con, uid)
            if aus and aus["delegado_id"] != actor:
                con.execute(
                    "INSERT INTO notificaciones (usuario_id, texto, url, creada_en) VALUES (?, ?, ?, ?)",
                    (aus["delegado_id"], f"(Delegado de {aus['nombre']}) {texto}", url, _ahora()),
                )
                _encolar_correo(con, aus["delegado_id"], f"(Delegado de {aus['nombre']}) {texto}", url)


def _notificar_por_estado(con, nota_id, estado):
    """Quién tiene que enterarse cuando una nota llega a cierto estado."""
    import auth
    nota = con.execute(
        "SELECT nombre_original, equipo_id, usuario_id FROM notas WHERE id = ?", (nota_id,)
    ).fetchone()
    if nota is None:
        return
    url = f"/nota/{nota_id}"
    nombre = nota["nombre_original"]
    actor = (USUARIO_ACTUAL.get() or {}).get("nombre") or "El sistema"

    def con_rol(roles, equipo_id=None):
        marcas = ", ".join("?" for _ in roles)
        sql = f"SELECT id FROM usuarios WHERE activo = 1 AND rol IN ({marcas})"
        params = list(roles)
        if equipo_id is not None:
            sql += " AND equipo_id = ?"
            params.append(equipo_id)
        return [f["id"] for f in con.execute(sql, params).fetchall()]

    if estado == "SUBIDA":
        _notificar(con, con_rol([auth.ROL_COORDINADOR], nota["equipo_id"]), f"{actor} subió el documento «{nombre}»: necesita su aprobación.", url)
    elif estado == "EN_REVISION":
        _notificar(con, con_rol(auth.ROLES_DIRECCION), f"{actor} dejó corregido el documento «{nombre}»: necesita su revisión de correcciones.", url)
    elif estado == "PROCESADA":
        _notificar(con, con_rol(auth.ROLES_DIRECCION), f"{actor} revisó las correcciones del documento «{nombre}»: necesita su firma.", url)
    elif estado == "FIRMADA":
        _notificar(con, con_rol(auth.ROLES_MESA), f"{actor} firmó el documento «{nombre}»: necesita su despacho.", url)
    elif estado == "PARA_REMITIR":
        _notificar(con, con_rol(auth.ROLES_MESA), f"{actor} cargó «{nombre}» como «Solo Mesa de Entradas»: para numerar y remitir.", url)
    elif estado in ("DESPACHADA", "RECHAZADA") and nota["usuario_id"]:
        if estado == "DESPACHADA":
            texto = f"{actor} despachó tu documento «{nombre}»."
        else:
            texto = f"{actor} rechazó tu documento «{nombre}»: revisá el motivo."
        _notificar(con, [nota["usuario_id"]], texto, url)


def marcar_leidas_por_url(usuario_id, url):
    """Al abrir una nota, sus avisos pendientes pasan a leídos."""
    with _conexion() as con:
        con.execute("UPDATE notificaciones SET leida = 1 WHERE usuario_id = ? AND url = ? AND leida = 0", (usuario_id, url))


def marcar_nota_vista(usuario_id, nota_id):
    with _conexion() as con:
        con.execute(
            "INSERT OR IGNORE INTO notas_vistas (usuario_id, nota_id, vista_en) VALUES (?, ?, ?)",
            (usuario_id, nota_id, _ahora()),
        )


def ids_notas_vistas(usuario_id):
    with _conexion() as con:
        return {f["nota_id"] for f in con.execute(
            "SELECT nota_id FROM notas_vistas WHERE usuario_id = ?", (usuario_id,)
        ).fetchall()}


def contar_notificaciones(usuario_id):
    with _conexion() as con:
        return con.execute(
            "SELECT COUNT(*) AS c FROM notificaciones WHERE usuario_id = ? AND leida = 0", (usuario_id,)
        ).fetchone()["c"]


def listar_notificaciones(usuario_id, limite=60):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM notificaciones WHERE usuario_id = ? ORDER BY id DESC LIMIT ?", (usuario_id, limite)
        ).fetchall()]


def obtener_notificacion(notificacion_id):
    with _conexion() as con:
        f = con.execute("SELECT * FROM notificaciones WHERE id = ?", (notificacion_id,)).fetchone()
        return dict(f) if f else None


def marcar_notificacion_leida(notificacion_id):
    with _conexion() as con:
        con.execute("UPDATE notificaciones SET leida = 1 WHERE id = ?", (notificacion_id,))


def marcar_notificacion_no_leida(notificacion_id):
    with _conexion() as con:
        con.execute("UPDATE notificaciones SET leida = 0 WHERE id = ?", (notificacion_id,))


def marcar_todas_leidas(usuario_id):
    with _conexion() as con:
        con.execute("UPDATE notificaciones SET leida = 1 WHERE usuario_id = ?", (usuario_id,))


def guardar_propuestas(nota_id, tipo, observaciones, propuestas, todos_los_parrafos=None, es_nota=True, comentario=None, alerta=None, desde=None, ruta_analizada=None):
    """Guarda las propuestas de corrección generadas (sin aplicar todavía)
    y deja la nota en EN_REVISION, a la espera de que el Director acepte,
    modifique o rechace cada una desde /nota/{id}/revisar.
    `todos_los_parrafos` es la lista completa del documento (con y sin
    cambios), para poder armar la vista continua de dos columnas.
    `es_nota` distingue si se le aplicó corrección automática o si solo se
    habilitó para edición manual (documentos que no son Nota también pasan
    por acá, para que el Director pueda editarlos a mano aunque la IA y el
    corrector no se les hayan aplicado).
    `comentario` permite personalizar la entrada de historial (por ejemplo,
    al reabrir una nota ya procesada o firmada para volver a corregirla —
    ver /nota/{id}/reabrir-revision). `alerta` es el motivo de esa
    reapertura, mostrado como aviso destacado en pantalla (no solo en el
    historial) hasta que el Director vuelva a guardar decisiones; en el
    flujo normal (primera vez que se generan propuestas) va en None, lo que
    limpia cualquier alerta vieja."""
    actualizar_estado(
        nota_id,
        "EN_REVISION",
        comentario=comentario or f"{len(propuestas)} párrafo(s) para revisar",
        desde=desde,
        ruta_analizada=str(ruta_analizada) if ruta_analizada else None,
        tipo=tipo,
        es_nota=1 if es_nota else 0,
        observaciones="; ".join(observaciones) if observaciones else None,
        propuestas_json=json.dumps(propuestas, ensure_ascii=False),
        parrafos_json=json.dumps(todos_los_parrafos or [], ensure_ascii=False),
        alerta=alerta,
    )


def actualizar_ruta_base(nota_id, ruta_base):
    """Guarda la copia de trabajo editada por el Coordinador (ver
    /nota/{id}/editar-base). El original archivado en ruta_original nunca
    se modifica ni se reemplaza."""
    with _conexion() as con:
        con.execute(
            "UPDATE notas SET ruta_base = ?, actualizado_en = ? WHERE id = ?",
            (str(ruta_base), _ahora(), nota_id),
        )


def guardar_resultado_sin_revision(nota_id, tipo, ruta_resultado, sha256_resultado=None):
    """Para documentos que no son Nota: no hay nada que revisar, van
    directo a PROCESADA (listos para firmar)."""
    actualizar_estado(
        nota_id,
        "PROCESADA",
        comentario="Sin correcciones (no es Nota)",
        tipo=tipo,
        es_nota=0,
        ruta_resultado=str(ruta_resultado),
        sha256_resultado=sha256_resultado,
        ruta_analizada=None,
        alerta=None,
    )


def guardar_resultado_tras_revision(nota_id, ruta_resultado, cambios_aplicados, sha256_resultado=None):
    """Aplica el resultado de que el usuario haya aceptado/rechazado cada
    propuesta: queda PROCESADA (lista para firmar), con el log de los
    cambios efectivamente aplicados (no todos los propuestos)."""
    actualizar_estado(
        nota_id,
        "PROCESADA",
        comentario=f"Revisión aplicada: {len(cambios_aplicados)} cambio(s) aceptado(s)",
        desde=("EN_REVISION",),
        sha256_resultado=sha256_resultado,
        ruta_analizada=None,
        ruta_resultado=str(ruta_resultado),
        cambios_json=json.dumps(cambios_aplicados, ensure_ascii=False),
        alerta=None,  # se resolvió lo que haya motivado una reapertura, si la hubo
    )


def marcar_error(nota_id, mensaje):
    actualizar_estado(nota_id, "ERROR", comentario=mensaje, error=mensaje)


def marcar_firmada(nota_id, ruta_firmada, sha256_firmado=None, fecha_nota=None):
    campos = {}
    if fecha_nota:
        campos["fecha_nota"] = fecha_nota
    actualizar_estado(
        nota_id, "FIRMADA", comentario="Firma insertada" + (f" (fecha de la nota: {fecha_nota})" if fecha_nota else ""),
        desde=("PROCESADA",),
        ruta_firmada=str(ruta_firmada), sha256_firmado=sha256_firmado,
        ruta_despacho_docx=None, ruta_despacho_pdf=None, sha256_despacho=None, despacho_preparado_en=None, **campos,
    )


def numero_de_nota_en_uso(nro_nota, excluir_id=None):
    """Id de OTRA nota que ya tiene ese número de nota (comparación sin
    distinguir mayúsculas), o None."""
    with _conexion() as con:
        fila = con.execute(
            "SELECT id FROM notas WHERE UPPER(nro_nota) = UPPER(?) AND id != ? LIMIT 1",
            (nro_nota, excluir_id or -1),
        ).fetchone()
    return fila["id"] if fila else None


def guardar_despacho_preparado(nota_id, nro_nota, nro_expte, ruta_docx, ruta_pdf, sha256):
    with _conexion() as con:
        con.execute(
            """UPDATE notas SET nro_nota=?, nro_expte=?, ruta_despacho_docx=?, ruta_despacho_pdf=?,
                   sha256_despacho=?, despacho_preparado_en=?, actualizado_en=? WHERE id=? AND estado IN ('FIRMADA', 'PARA_REMITIR')""",
            (nro_nota, nro_expte, str(ruta_docx), str(ruta_pdf) if ruta_pdf else None, sha256,
             _ahora(), _ahora(), nota_id),
        )
    registrar_evento(nota_id, f"Despacho preparado: {('Nota N° ' + nro_nota) if nro_nota else 'sin número de nota (no es una nota)'}, Expte. N° {nro_expte or '—'}")


def marcar_despachada(nota_id):
    actualizar_estado(nota_id, "DESPACHADA", comentario="Nota despachada", desde=LISTAS_PARA_REMITIR)


def es_pdf(nota):
    return str((nota or {}).get("ruta_original") or "").lower().endswith(".pdf")


def ajustar_estado_pdf(nota_id):
    """Un PDF suelto queda «Archivado»; dentro de un bloque, «Adjunto» (viaja con el despacho del bloque).
    Solo actúa sobre PDFs legacy en ARCHIVADA sin tipo. Los PDFs A_CONFIRMAR permanecen A_CONFIRMAR
    hasta que Dirección los confirme; los que tienen tipo ya están en el nuevo circuito."""
    n = obtener_nota(nota_id)
    if not n or not es_pdf(n) or n["estado"] not in ("ARCHIVADA", "ADJUNTO"):
        return
    if n.get("tipo"):  # solo legacy ARCHIVADA sin tipo; con tipo están en el nuevo circuito
        return
    destino = "ADJUNTO" if n.get("bloque_id") else "ARCHIVADA"
    if n["estado"] != destino:
        actualizar_estado(nota_id, destino, comentario="Adjunto del bloque: se despacha junto con él" if destino == "ADJUNTO"
                          else "Sale del bloque: queda archivado", desde=(n["estado"],))


def actualizar_tipo(nota_id, tipo):
    """Fija el tipo documental de una nota (ej: 'ADJUNTO' para PDFs del nuevo circuito)."""
    with _conexion() as con:
        con.execute("UPDATE notas SET tipo = ? WHERE id = ?", (tipo, nota_id))


def marcar_rechazada(nota_id, motivo):
    """Rechazo operativo de la nota completa (no de redacción). `motivo` es
    obligatorio: queda en el historial como justificación."""
    actualizar_estado(nota_id, "RECHAZADA", comentario=motivo, desde=("SUBIDA", "EN_REVISION"))


def actualizar_ruta_original(nota_id, ruta_original):
    """Se usa una sola vez, justo después de subir el archivo, para
    reubicarlo en el repositorio histórico por fecha (ver _ruta_archivo_
    historico en main.py). No se vuelve a tocar después: el archivo
    original nunca se sobrescribe ni se borra."""
    with _conexion() as con:
        con.execute(
            "UPDATE notas SET ruta_original = ? WHERE id = ?",
            (str(ruta_original), nota_id),
        )


def registrar_decision_director(nota_id, ubicacion, texto_original, texto_propuesto, texto_final, decision, fuentes):
    with _conexion() as con:
        con.execute(
            """INSERT INTO correcciones_director
               (nota_id, ubicacion, texto_original, texto_propuesto, texto_final, decision, fuentes, fecha)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (nota_id, ubicacion, texto_original, texto_propuesto, texto_final, decision,
             json.dumps(fuentes, ensure_ascii=False), _ahora()),
        )


def obtener_modificaciones():
    """Todas las correcciones donde el Director eligió "modificar" (no
    "aceptar" ni "rechazar"). Es el insumo de aprendizaje.analizar_patrones."""
    with _conexion() as con:
        filas = con.execute(
            "SELECT * FROM correcciones_director WHERE decision IN ('modificado', 'modificar') ORDER BY id"
        ).fetchall()
        return [dict(f) for f in filas]


def conexion():
    """Punto de acceso público a la conexión, para módulos de solo lectura
    como reportes.py (evita que tengan que importar el helper "privado")."""
    return _conexion()


def obtener_nota(nota_id):
    with _conexion() as con:
        fila = con.execute(
            """SELECT n.*, e.nombre AS equipo_nombre, u.nombre AS asesor_nombre,
                      b.nombre AS bloque_nombre
               FROM notas n
               JOIN equipos e ON e.id = n.equipo_id
               LEFT JOIN usuarios u ON u.id = n.usuario_id
               LEFT JOIN bloques b ON b.id = n.bloque_id
               WHERE n.id = ?""",
            (nota_id,),
        ).fetchone()
        if fila is None:
            return None
        nota = dict(fila)
        nota["cambios"] = json.loads(nota["cambios_json"]) if nota["cambios_json"] else []
        nota["propuestas"] = json.loads(nota["propuestas_json"]) if nota["propuestas_json"] else []
        nota["todos_los_parrafos"] = json.loads(nota["parrafos_json"]) if nota["parrafos_json"] else []
        historial = con.execute(
            "SELECT estado, comentario, fecha, usuario FROM historial_estados WHERE nota_id = ? ORDER BY id",
            (nota_id,),
        ).fetchall()
        nota["historial"] = [dict(h) for h in historial]
        return nota


def listar_notas(limite=300):
    """Todas las notas EN CIRCUITO (nunca se omite una activa, por vieja que
    sea) más las `limite` terminales más recientes (despachadas/rechazadas)."""
    with _conexion() as con:
        filas = con.execute(
            """SELECT n.*, e.nombre AS equipo_nombre, u.nombre AS asesor_nombre,
                      b.nombre AS bloque_nombre,
                      (SELECT COUNT(*) FROM consistencia_hallazgos h
                        WHERE h.nota_id = n.id AND h.estado = 'ABIERTO') AS hallazgos_abiertos,
                      (SELECT MAX(he.fecha) FROM historial_estados he
                        WHERE he.nota_id = n.id AND he.estado = n.estado) AS en_estado_desde
               FROM notas n
               JOIN equipos e ON e.id = n.equipo_id
               LEFT JOIN usuarios u ON u.id = n.usuario_id
               LEFT JOIN bloques b ON b.id = n.bloque_id
               WHERE n.estado NOT IN ('DESPACHADA', 'RECHAZADA')
                  OR n.id IN (SELECT id FROM notas WHERE estado IN ('DESPACHADA', 'RECHAZADA')
                              ORDER BY id DESC LIMIT ?)
               ORDER BY n.id DESC""",
            (limite,),
        ).fetchall()
        return [dict(f) for f in filas]


# ---------------------------------------------------------------------
# CLASIFICACIÓN (tema y prioridad sugerida) — reforma 11
# ---------------------------------------------------------------------
def guardar_clasificacion(nota_id, tema, tema_origen, prioridad_sugerida, motivo):
    """Guarda el tema (salvo que ya lo haya fijado una persona: lo manual no
    se pisa) y la prioridad sugerida. La sugerencia vuelve a quedar
    PENDIENTE solo si cambió respecto de la anterior."""
    with _conexion() as con:
        fila = con.execute(
            "SELECT tema_origen, prioridad_sugerida, prioridad_sugerida_estado FROM notas WHERE id = ?", (nota_id,)
        ).fetchone()
        if fila is None:
            return
        # Lo fijado a mano no se pisa, y un resultado de IA no se pisa con
        # uno de palabras clave.
        if fila["tema_origen"] != "manual" and not (fila["tema_origen"] == "ia" and tema_origen == "auto"):
            con.execute("UPDATE notas SET tema = ?, tema_origen = ? WHERE id = ?", (tema, tema_origen, nota_id))
        if fila["prioridad_sugerida"] != prioridad_sugerida or not fila["prioridad_sugerida_estado"]:
            estado = "PENDIENTE" if prioridad_sugerida and prioridad_sugerida != "NORMAL" else None
            con.execute(
                "UPDATE notas SET prioridad_sugerida = ?, prioridad_motivo = ?, prioridad_sugerida_estado = ? WHERE id = ?",
                (prioridad_sugerida, motivo, estado, nota_id),
            )
        else:
            con.execute("UPDATE notas SET prioridad_motivo = ? WHERE id = ?", (motivo, nota_id))


def actualizar_tema(nota_id, tema, origen="manual"):
    with _conexion() as con:
        con.execute("UPDATE notas SET tema = ?, tema_origen = ? WHERE id = ?", (tema, origen, nota_id))


def resolver_prioridad_sugerida(nota_id, estado):
    with _conexion() as con:
        con.execute("UPDATE notas SET prioridad_sugerida_estado = ? WHERE id = ?", (estado, nota_id))


def fijar_referencia(nota_id, ref_nota_id):
    with _conexion() as con:
        con.execute("UPDATE notas SET ref_nota_id = ? WHERE id = ?", (ref_nota_id, nota_id))


# ---------------------------------------------------------------------
# CONSISTENCIA — reforma 12
# ---------------------------------------------------------------------
def reemplazar_hallazgos(nota_id, bloque_id, hallazgos, grupo="consistencia"):
    """Reemplaza los hallazgos de la nota por los recién calculados, pero
    conserva el estado (REVISADO / IGNORADO) de los que siguen siendo los
    mismos (misma clave): así lo que una persona ya revisó no reaparece."""
    ahora = _ahora()
    with _conexion() as con:
        # Dos grupos que se recalculan por separado: la prerrevisión (un documento solo, origen PRERREVISION)
        # y la consistencia entre documentos (el resto). Uno nunca borra los hallazgos del otro.
        filtro = "origen = 'PRERREVISION'" if grupo == "prerrevision" else "COALESCE(origen, '') != 'PRERREVISION'"
        previos = {
            f["clave"]: dict(f) for f in con.execute(
                f"SELECT * FROM consistencia_hallazgos WHERE nota_id = ? AND {filtro}", (nota_id,)
            ).fetchall()
        }
        # Los que siguen siendo los mismos se ACTUALIZAN en el lugar (conservan su
        # id: un botón de una pantalla abierta sigue funcionando); los que ya no
        # aparecen se borran y los nuevos se agregan.
        erroneos = {f["clave"] for f in con.execute("SELECT clave FROM feedback_ia WHERE activo = 1 AND tipo = 'CONSISTENCIA'").fetchall()}
        hallazgos = [h for h in hallazgos if h["clave"] in previos or h["clave"] not in erroneos]
        claves_nuevas = {h["clave"] for h in hallazgos}
        nuevos_accionables = [h for h in hallazgos if h["clave"] not in previos and h.get("verificacion") != "DUDOSO"]
        for clave, previo in previos.items():
            if clave not in claves_nuevas:
                con.execute("DELETE FROM consistencia_hallazgos WHERE id = ?", (previo["id"],))
        for h in hallazgos:
            previo = previos.get(h["clave"])
            if previo:
                con.execute(
                    """UPDATE consistencia_hallazgos SET bloque_id = ?, tipo = ?, severidad = ?, dato = ?,
                       detalle = ?, valores_json = ? WHERE id = ?""",
                    (bloque_id, h["tipo"], h["severidad"], h["dato"], h.get("detalle"),
                     json.dumps(h["valores"], ensure_ascii=False), previo["id"]),
                )
                continue
            con.execute(
                """INSERT INTO consistencia_hallazgos
                   (nota_id, bloque_id, tipo, severidad, dato, detalle, valores_json, clave, estado,
                    resuelto_por, resuelto_en, creado_en, verificacion, origen, modelo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (nota_id, bloque_id, h["tipo"], h["severidad"], h["dato"], h.get("detalle"),
                 json.dumps(h["valores"], ensure_ascii=False), h["clave"],
                 "IGNORADO" if h.get("verificacion") == "DUDOSO" else "ABIERTO",
                 "Verificador IA" if h.get("verificacion") == "DUDOSO" else None,
                 ahora if h.get("verificacion") == "DUDOSO" else None, ahora,
                 h.get("verificacion"), h.get("origen"), h.get("modelo")),
            )
        # Alertas nuevas: se le avisa al asesor mientras la nota espera a Coordinación.
        if nuevos_accionables:
            nota = con.execute("SELECT nombre_original, usuario_id, estado FROM notas WHERE id = ?", (nota_id,)).fetchone()
            if nota and nota["usuario_id"] and nota["estado"] in ("BORRADOR", "SUBIDA"):
                n = len(nuevos_accionables)
                _notificar(
                    con, [nota["usuario_id"]],
                    f"El sistema detectó {n} alerta{'s' if n != 1 else ''} {'en la prerrevisión de' if grupo == 'prerrevision' else 'de consistencia en'} tu documento «{nota['nombre_original']}»: revisala antes de que Coordinación la evalúe.",
                    f"/nota/{nota_id}",
                )


def guardar_feedback_hallazgo(hallazgo, motivo, usuario):
    """Una persona marcó este reporte de la IA como erróneo: se guarda para
    no repetirlo (misma clave) y como ejemplo negativo para el modelo."""
    with _conexion() as con:
        con.execute(
            """INSERT INTO feedback_ia (tipo, clave, dato, detalle, valores_json, motivo, usuario, nota_id, creado_en)
               VALUES ('CONSISTENCIA', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (hallazgo["clave"], hallazgo["dato"], hallazgo.get("detalle"), hallazgo.get("valores_json"),
             (motivo or "").strip()[:300], usuario, hallazgo["nota_id"], _ahora()),
        )


def ejemplos_erroneos(limite=4):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT dato, motivo FROM feedback_ia WHERE activo = 1 AND tipo = 'CONSISTENCIA' ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()]


def listar_feedback(limite=100):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM feedback_ia ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()]


def desactivar_feedback(feedback_id):
    """La Dirección puede borrar un aprendizaje equivocado."""
    with _conexion() as con:
        con.execute("UPDATE feedback_ia SET activo = 0 WHERE id = ?", (feedback_id,))


def listar_hallazgos(nota_id):
    with _conexion() as con:
        filas = con.execute(
            """SELECT * FROM consistencia_hallazgos WHERE nota_id = ?
               ORDER BY CASE tipo WHEN 'DISCREPANCIA' THEN 0 WHEN 'FALTA' THEN 1 ELSE 2 END, id""",
            (nota_id,),
        ).fetchall()
    resultado = []
    for f in filas:
        h = dict(f)
        h["valores"] = json.loads(h["valores_json"])
        resultado.append(h)
    return resultado


def obtener_hallazgo(hallazgo_id):
    with _conexion() as con:
        f = con.execute("SELECT * FROM consistencia_hallazgos WHERE id = ?", (hallazgo_id,)).fetchone()
        return dict(f) if f else None


def resolver_hallazgo(hallazgo_id, estado, quien):
    with _conexion() as con:
        con.execute(
            "UPDATE consistencia_hallazgos SET estado = ?, resuelto_por = ?, resuelto_en = ? WHERE id = ?",
            (estado, quien, _ahora(), hallazgo_id),
        )


def hay_analisis_consistencia(nota_id):
    with _conexion() as con:
        return con.execute(
            "SELECT 1 FROM trabajos_ia WHERE nota_id = ? AND tipo = 'CONSISTENCIA' AND estado = 'HECHO' LIMIT 1",
            (nota_id,),
        ).fetchone() is not None


# ---------------------------------------------------------------------
# MEJORAS DESDE EL INFORME — reforma 13
# ---------------------------------------------------------------------
def reemplazar_mejoras(nota_id, ref_nota_id, mejoras):
    """Reemplaza las sugerencias PENDIENTES por las nuevas; las ya resueltas
    (aceptadas/descartadas) se conservan y no se vuelven a proponer."""
    ahora = _ahora()
    with _conexion() as con:
        resueltas = {
            (f["ubicacion"], f["propuesto"]) for f in con.execute(
                "SELECT ubicacion, propuesto FROM mejoras_informe WHERE nota_id = ? AND estado IN ('ACEPTADA','DESCARTADA')",
                (nota_id,),
            ).fetchall()
        }
        con.execute("DELETE FROM mejoras_informe WHERE nota_id = ? AND estado IN ('PENDIENTE','OBSOLETA')", (nota_id,))
        for m in mejoras:
            if (m["ubicacion"], m["propuesto"]) in resueltas:
                continue
            con.execute(
                """INSERT INTO mejoras_informe (nota_id, ref_nota_id, ubicacion, tipo, original, propuesto, cita, creado_en)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (nota_id, ref_nota_id, m["ubicacion"], m["tipo"], m["original"], m["propuesto"], m["cita"], ahora),
            )


def listar_mejoras(nota_id, solo_pendientes=False):
    with _conexion() as con:
        sql = "SELECT * FROM mejoras_informe WHERE nota_id = ?"
        if solo_pendientes:
            sql += " AND estado = 'PENDIENTE'"
        return [dict(f) for f in con.execute(sql + " ORDER BY id", (nota_id,)).fetchall()]


def obtener_mejora(mejora_id):
    with _conexion() as con:
        f = con.execute("SELECT * FROM mejoras_informe WHERE id = ?", (mejora_id,)).fetchone()
        return dict(f) if f else None


def resolver_mejora(mejora_id, estado):
    with _conexion() as con:
        con.execute("UPDATE mejoras_informe SET estado = ?, resuelto_en = ? WHERE id = ?", (estado, _ahora(), mejora_id))


def actualizar_propuestas(nota_id, propuestas):
    """Reemplaza propuestas_json (por ejemplo al aceptar una mejora del
    informe sobre un párrafo en revisión)."""
    with _conexion() as con:
        con.execute("UPDATE notas SET propuestas_json = ? WHERE id = ?",
                    (json.dumps(propuestas, ensure_ascii=False), nota_id))


# ---------------------------------------------------------------------
# CORRECCIÓN PRECALCULADA — reforma 14
# ---------------------------------------------------------------------
def guardar_propuestas_previas(nota_id, huella, resultado):
    with _conexion() as con:
        con.execute(
            "UPDATE notas SET propuestas_previas_json = ?, propuestas_previas_hash = ? WHERE id = ?",
            (json.dumps(resultado, ensure_ascii=False), huella, nota_id),
        )


def obtener_propuestas_previas(nota_id, huella):
    """Devuelve la corrección precalculada SOLO si se calculó sobre el mismo
    texto (misma huella); si el texto cambió, devuelve None."""
    with _conexion() as con:
        f = con.execute(
            "SELECT propuestas_previas_json, propuestas_previas_hash FROM notas WHERE id = ?", (nota_id,)
        ).fetchone()
    if f and f["propuestas_previas_json"] and f["propuestas_previas_hash"] == huella:
        return json.loads(f["propuestas_previas_json"])
    return None


def tiene_propuestas_previas(nota_id):
    with _conexion() as con:
        f = con.execute("SELECT propuestas_previas_hash FROM notas WHERE id = ?", (nota_id,)).fetchone()
    return bool(f and f["propuestas_previas_hash"])


# ---------------------------------------------------------------------
# COLA DE TRABAJOS DE IA — reforma 14
# ---------------------------------------------------------------------
def encolar_trabajo(nota_id, tipo, modelo=None):
    """Agrega un trabajo a la cola, salvo que ya haya uno igual esperando o
    en curso para esa nota (evita duplicados). Devuelve el id o None."""
    with _conexion() as con:
        existe = con.execute(
            "SELECT id FROM trabajos_ia WHERE nota_id = ? AND tipo = ? AND estado IN ('PENDIENTE','EN_CURSO')",
            (nota_id, tipo),
        ).fetchone()
        if existe:
            return None
        cur = con.execute(
            "INSERT INTO trabajos_ia (nota_id, tipo, modelo, creado_en) VALUES (?, ?, ?, ?)",
            (nota_id, tipo, modelo, _ahora()),
        )
        return cur.lastrowid


def trabajos_pendientes():
    """Trabajos PENDIENTE con los datos de su nota (prioridad, bloque, estado)
    para que el worker los ordene."""
    with _conexion() as con:
        filas = con.execute(
            """SELECT t.*, n.prioridad, n.bloque_id, n.estado AS nota_estado, n.nombre_original
               FROM trabajos_ia t JOIN notas n ON n.id = t.nota_id
               WHERE t.estado = 'PENDIENTE' ORDER BY t.id"""
        ).fetchall()
        return [dict(f) for f in filas]


def prioridades_por_bloque():
    """{bloque_id: prioridad más alta} para calcular la prioridad efectiva."""
    rango = {"URGENTE": 0, "PRIORITARIA": 1, "NORMAL": 2}
    mejor = {}
    with _conexion() as con:
        for f in con.execute("SELECT bloque_id, prioridad FROM notas WHERE bloque_id IS NOT NULL").fetchall():
            actual = mejor.get(f["bloque_id"])
            if actual is None or rango.get(f["prioridad"], 2) < rango.get(actual, 2):
                mejor[f["bloque_id"]] = f["prioridad"]
    return mejor


def iniciar_trabajo(trabajo_id, modelo=None):
    with _conexion() as con:
        con.execute(
            "UPDATE trabajos_ia SET estado = 'EN_CURSO', iniciado_en = ?, modelo = COALESCE(?, modelo) WHERE id = ?",
            (_ahora(), modelo, trabajo_id),
        )


def terminar_trabajo(trabajo_id, estado, resultado=None, error=None, sumar_intento=False):
    with _conexion() as con:
        con.execute(
            """UPDATE trabajos_ia SET estado = ?, terminado_en = ?, resultado_json = ?, error = ?,
               intentos = intentos + ? WHERE id = ?""",
            (estado, _ahora() if estado in ("HECHO", "ERROR", "OMITIDO") else None,
             json.dumps(resultado, ensure_ascii=False) if resultado is not None else None,
             error, 1 if sumar_intento else 0, trabajo_id),
        )


def reencolar_trabajos_huerfanos():
    """Al arrancar la app: un trabajo que quedó EN_CURSO (se cortó la luz o
    se cerró la app) vuelve a PENDIENTE."""
    with _conexion() as con:
        cur = con.execute("UPDATE trabajos_ia SET estado = 'PENDIENTE' WHERE estado = 'EN_CURSO'")
        return cur.rowcount


def estado_ia_nota(nota_id):
    """{tipo: estado} del último trabajo de cada tipo para la nota."""
    with _conexion() as con:
        filas = con.execute(
            "SELECT tipo, estado, error, terminado_en FROM trabajos_ia WHERE nota_id = ? ORDER BY id", (nota_id,)
        ).fetchall()
    ultimo = {}
    for f in filas:
        ultimo[f["tipo"]] = dict(f)
    return ultimo


def listar_trabajos(limite=40):
    with _conexion() as con:
        filas = con.execute(
            """SELECT t.*, n.nombre_original FROM trabajos_ia t JOIN notas n ON n.id = t.nota_id
               ORDER BY t.id DESC LIMIT ?""", (limite,)
        ).fetchall()
        return [dict(f) for f in filas]


def resumen_trabajos():
    with _conexion() as con:
        filas = con.execute("SELECT estado, COUNT(*) AS c FROM trabajos_ia GROUP BY estado").fetchall()
    return {f["estado"]: f["c"] for f in filas}


def estadisticas_ia():
    """Datos para el reporte: tiempos de los trabajos y tasa de aceptación
    de las sugerencias (prioridad y mejoras del informe) y de los hallazgos."""
    from datetime import datetime
    with _conexion() as con:
        trabajos = con.execute(
            "SELECT tipo, iniciado_en, terminado_en FROM trabajos_ia WHERE estado = 'HECHO' AND iniciado_en IS NOT NULL"
        ).fetchall()
        prio = {f["e"]: f["c"] for f in con.execute(
            "SELECT prioridad_sugerida_estado AS e, COUNT(*) AS c FROM notas "
            "WHERE prioridad_sugerida_estado IS NOT NULL GROUP BY e").fetchall()}
        mej = {f["estado"]: f["c"] for f in con.execute(
            "SELECT estado, COUNT(*) AS c FROM mejoras_informe GROUP BY estado").fetchall()}
        hall = {f["estado"]: f["c"] for f in con.execute(
            "SELECT estado, COUNT(*) AS c FROM consistencia_hallazgos GROUP BY estado").fetchall()}
        temas = {(f["tema"] or "Sin clasificar"): f["c"] for f in con.execute(
            "SELECT tema, COUNT(*) AS c FROM notas GROUP BY tema ORDER BY c DESC").fetchall()}
    por_tipo = {}
    for t in trabajos:
        try:
            seg = (datetime.fromisoformat(t["terminado_en"]) - datetime.fromisoformat(t["iniciado_en"])).total_seconds()
        except Exception:
            continue
        por_tipo.setdefault(t["tipo"], []).append(seg)
    tiempos = {tipo: {"cantidad": len(v), "promedio_seg": round(sum(v) / len(v), 1)} for tipo, v in por_tipo.items()}

    def tasa(d, ok):
        total = sum(d.get(k, 0) for k in (ok, "DESCARTADA"))
        return round(100 * d.get(ok, 0) / total) if total else None

    return {
        "tiempos": tiempos,
        "prioridad": {"aceptadas": prio.get("ACEPTADA", 0), "descartadas": prio.get("DESCARTADA", 0),
                      "pendientes": prio.get("PENDIENTE", 0), "tasa_aceptacion": tasa(prio, "ACEPTADA")},
        "mejoras": {"aceptadas": mej.get("ACEPTADA", 0), "descartadas": mej.get("DESCARTADA", 0),
                    "pendientes": mej.get("PENDIENTE", 0), "tasa_aceptacion": tasa(mej, "ACEPTADA")},
        "hallazgos": {"abiertos": hall.get("ABIERTO", 0), "revisados": hall.get("REVISADO", 0),
                      "ignorados": hall.get("IGNORADO", 0)},
        "temas": temas,
    }


# ---------------------------------------------------------------------
# SEGURIDAD: INTENTOS DE LOGIN Y AUDITORÍA DE ACCESOS
# ---------------------------------------------------------------------
def ultimos_ingresos():
    """{usuario (en minúsculas): fecha del último ingreso correcto}."""
    with _conexion() as con:
        return {f["usuario"]: f["ultimo"] for f in con.execute(
            "SELECT usuario, MAX(fecha) AS ultimo FROM intentos_login WHERE exito = 1 GROUP BY usuario")}


def registrar_revision_usuarios(revisor, observaciones=None):
    with _conexion() as con:
        con.execute("INSERT INTO revisiones_usuarios (fecha, revisor, observaciones) VALUES (?, ?, ?)",
                    (_ahora(), revisor, (observaciones or "").strip()[:500] or None))


def listar_revisiones_usuarios(limite=12):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM revisiones_usuarios ORDER BY id DESC LIMIT ?", (limite,))]


DIAS_REVISION_USUARIOS = 90
_aviso_revision = {"dia": None}


def revision_usuarios_vencida(hoy=None):
    """True si pasaron más de 90 días desde la última revisión de usuarios y permisos (o nunca se hizo)."""
    hoy = hoy or datetime.now(timezone.utc)
    r = listar_revisiones_usuarios(1)
    if not r:
        return True
    try:
        ultima = datetime.fromisoformat(r[0]["fecha"])
    except ValueError:
        return True
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=timezone.utc)
    return (hoy - ultima).days > DIAS_REVISION_USUARIOS


def recordar_revision_usuarios():
    """Una vez por día, si la revisión trimestral está vencida, avisa a la Dirección."""
    dia = datetime.now().date()
    if _aviso_revision["dia"] == dia or not revision_usuarios_vencida():
        return False
    _aviso_revision["dia"] = dia
    with _conexion() as con:
        ya = con.execute("SELECT 1 FROM notificaciones WHERE url = '/admin/usuarios/revision' AND creada_en >= ?",
                         ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),)).fetchone()
    if ya:
        return False  # ya se avisó en la última semana
    notificar_usuarios([u["id"] for u in listar_usuarios() if u["activo"] and u["rol"] in ("DIRECTOR", "SUBDIRECTOR")],
                       "Toca la revisión trimestral de usuarios y permisos.", "/admin/usuarios/revision")
    return True


def registrar_intento_login(usuario, ip, exito):
    with _conexion() as con:
        con.execute(
            "INSERT INTO intentos_login (usuario, ip, exito, fecha) VALUES (?, ?, ?, ?)",
            ((usuario or "").strip().lower()[:80], (ip or "")[:64], 1 if exito else 0, _ahora()),
        )


def login_bloqueado(usuario, ip, max_fallos=5, minutos=15, max_fallos_ip=30):
    """True si ese usuario acumuló `max_fallos` intentos fallidos en los
    últimos `minutos` (contando desde su último ingreso exitoso), o si esa
    IP acumuló demasiados fallos contra cualquier usuario."""
    from datetime import timedelta
    desde = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat(timespec="seconds")
    usuario = (usuario or "").strip().lower()[:80]
    with _conexion() as con:
        ultimo_ok = con.execute(
            "SELECT MAX(fecha) AS f FROM intentos_login WHERE usuario = ? AND exito = 1", (usuario,)
        ).fetchone()["f"]
        corte = max(desde, ultimo_ok) if ultimo_ok else desde
        fallos = con.execute(
            "SELECT COUNT(*) AS c FROM intentos_login WHERE usuario = ? AND exito = 0 AND fecha > ?",
            (usuario, corte),
        ).fetchone()["c"]
        if fallos >= max_fallos:
            return True
        if ip:
            fallos_ip = con.execute(
                "SELECT COUNT(*) AS c FROM intentos_login WHERE ip = ? AND exito = 0 AND fecha > ?", (ip, desde)
            ).fetchone()["c"]
            return fallos_ip >= max_fallos_ip
    return False


def listar_intentos_fallidos(limite=30):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM intentos_login WHERE exito = 0 ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()]


def registrar_acceso(nota_id, accion, detalle=None):
    """Quién vio o descargó qué documento (usa el usuario del request en curso)."""
    u = USUARIO_ACTUAL.get() or {}
    with _conexion() as con:
        con.execute(
            "INSERT INTO accesos_documentos (nota_id, usuario_id, usuario, accion, detalle, fecha) VALUES (?, ?, ?, ?, ?, ?)",
            (nota_id, u.get("id"), u.get("nombre") or "Sistema", accion, detalle, _ahora()),
        )


def listar_accesos(limite=50, nota_id=None):
    with _conexion() as con:
        sql = """SELECT a.*, n.nombre_original FROM accesos_documentos a
                 LEFT JOIN notas n ON n.id = a.nota_id"""
        params = []
        if nota_id is not None:
            sql += " WHERE a.nota_id = ?"
            params.append(nota_id)
        sql += " ORDER BY a.id DESC LIMIT ?"
        params.append(limite)
        return [dict(f) for f in con.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------
# INTEGRIDAD (huellas SHA-256)
# ---------------------------------------------------------------------
_COLUMNAS_HASH = ("sha256_original", "sha256_resultado", "sha256_firmado")


def fijar_hash(nota_id, columna, valor):
    if columna not in _COLUMNAS_HASH:
        raise ValueError("Columna de huella no válida")
    with _conexion() as con:
        con.execute(f"UPDATE notas SET {columna} = ? WHERE id = ?", (valor, nota_id))


# ---------------------------------------------------------------------
# BÚSQUEDA DE TEXTO
# ---------------------------------------------------------------------
def indexar_texto(nota_id, texto):
    """Guarda (o reemplaza) el texto de la nota para poder buscar adentro."""
    texto = texto or ""
    with _conexion() as con:
        con.execute(
            "INSERT INTO notas_texto (nota_id, contenido) VALUES (?, ?) "
            "ON CONFLICT(nota_id) DO UPDATE SET contenido = excluded.contenido",
            (nota_id, texto),
        )
        if FTS_DISPONIBLE:
            con.execute("DELETE FROM notas_fts WHERE rowid = ?", (nota_id,))
            con.execute("INSERT INTO notas_fts (rowid, contenido) VALUES (?, ?)", (nota_id, texto))
            try:  # propuesta 11: también por raíces («traslado» encuentra «trasladaron»)
                from dgpdh.dominio.raices import raices
                con.execute("DELETE FROM notas_raices WHERE rowid = ?", (nota_id,))
                con.execute("INSERT INTO notas_raices (rowid, contenido) VALUES (?, ?)", (nota_id, raices(texto)))
            except sqlite3.OperationalError:
                pass  # base sin la migración 0018


def _terminos_busqueda(consulta):
    import re
    return [t for t in re.findall(r"[\w\-]{2,}", (consulta or "").lower())][:8]


def _buscar_por_raices(con, terminos, ya, limite):
    """Propuesta 11: lo que se encuentra solo por la raíz de las palabras, con un fragmento armado del texto."""
    import re
    from dgpdh.dominio.raices import raiz, raices
    raices_busca = [raiz(t) for t in terminos]
    try:
        ids = [f[0] for f in con.execute("SELECT rowid FROM notas_raices WHERE notas_raices MATCH ? LIMIT ?",
                                         (" ".join('"' + r.replace('"', '') + '"*' for r in raices_busca), limite + len(ya)))]
    except sqlite3.OperationalError:
        return []
    salida = []
    nuevos = [x for x in ids if x not in ya][:limite]
    textos = {f["nota_id"]: f["contenido"] for f in con.execute(
        "SELECT nota_id, contenido FROM notas_texto WHERE nota_id IN (%s)" % ",".join("?" * len(nuevos)), nuevos)} if nuevos else {}
    for i in nuevos:
        texto = textos.get(i, "")
        m = next((m for m in re.finditer(r"\w+", texto) if raices(m.group(0)).startswith(raices_busca[0])), None)
        pos = m.start() if m else 0
        ini = max(0, pos - 60)
        salida.append((i, ("…" if ini else "") + texto[ini:pos].replace("\n", " ") + ("\x02" + m.group(0) + "\x03" if m else "")
                       + texto[m.end() if m else pos:pos + 100].replace("\n", " ")))
    return salida


def buscar_texto(consulta, limite=100):
    """Devuelve [(nota_id, fragmento)] con las notas que contienen TODAS las
    palabras buscadas (las últimas, por prefijo). El fragmento marca lo
    encontrado con \x02 ... \x03. El filtro de permisos lo aplica quien llama."""
    terminos = _terminos_busqueda(consulta)
    if not terminos:
        return []
    with _conexion() as con:
        if FTS_DISPONIBLE:
            expresion = " ".join('"' + t.replace('"', '') + '"*' for t in terminos)
            try:
                filas = con.execute(
                    "SELECT rowid AS id, snippet(notas_fts, 0, char(2), char(3), '…', 14) AS frag "
                    "FROM notas_fts WHERE notas_fts MATCH ? ORDER BY rank LIMIT ?",
                    (expresion, limite),
                ).fetchall()
                salida = [(f["id"], f["frag"]) for f in filas]
                if len(salida) < limite:
                    salida += _buscar_por_raices(con, terminos, {i for i, _ in salida}, limite - len(salida))
                return salida
            except sqlite3.OperationalError:
                pass  # expresión inválida: cae a la búsqueda simple
        condiciones = " AND ".join("lower(contenido) LIKE ?" for _ in terminos)
        filas = con.execute(
            f"SELECT nota_id, contenido FROM notas_texto WHERE {condiciones} LIMIT ?",
            [f"%{t}%" for t in terminos] + [limite],
        ).fetchall()
    resultado = []
    for f in filas:
        texto = f["contenido"]
        pos = texto.lower().find(terminos[0])
        ini = max(0, pos - 60)
        frag = texto[ini:pos + 100].replace("\n", " ")
        resultado.append((f["nota_id"], ("…" if ini else "") + frag))
    return resultado


# ---------------------------------------------------------------------
# VERSIONES DE UN DOCUMENTO
# ---------------------------------------------------------------------
def cadena_de_versiones(nota_id):
    """Todas las versiones de un documento (la raíz y sus reemplazos), de la
    más vieja a la más nueva: [{"id", "nombre_original", "estado", "creado_en", "numero"}]."""
    with _conexion() as con:
        filas = {f["id"]: dict(f) for f in con.execute(
            "SELECT id, nombre_original, estado, creado_en, version_de FROM notas"
        ).fetchall()}
    if nota_id not in filas:
        return []
    raiz = nota_id
    while filas[raiz]["version_de"] and filas[raiz]["version_de"] in filas:
        raiz = filas[raiz]["version_de"]
    cadena, pendientes = [], [raiz]
    while pendientes:
        actual = pendientes.pop(0)
        cadena.append(filas[actual])
        pendientes.extend(sorted(i for i, f in filas.items() if f["version_de"] == actual))
    for numero, f in enumerate(cadena, start=1):
        f["numero"] = numero
    return cadena


# ---------------------------------------------------------------------
# RUTAS: REUBICACIÓN AL MOVER LA CARPETA DE LA APP
# ---------------------------------------------------------------------
_COLUMNAS_RUTA = ("ruta_original", "ruta_base", "ruta_resultado", "ruta_firmada", "ruta_analizada")


_OTRAS_RUTAS = (("notas", "ruta_despacho_docx"), ("notas", "ruta_despacho_pdf"), ("respuestas", "archivo"),
                ("modelos_nota", "archivo"), ("reportes_generados", "archivo"))


def reubicar_rutas(carpeta_datos):
    """Si la carpeta de la app se movió (otro disco, otra PC o un servidor Linux tras una
    restauración), las rutas guardadas dejan de existir. Para cada ruta que
    ya no existe, busca la parte que sigue a "datos" dentro de la carpeta de
    datos actual y, si el archivo está ahí, actualiza la ruta. Entiende rutas de
    Windows (C:\\...\\datos\\...) guardadas antes del cambio. Devuelve cuántas corrigió."""
    carpeta_datos = Path(carpeta_datos)
    corregidas = 0

    def nueva(valor):
        if not valor or Path(valor).exists():
            return None
        partes = Path(str(valor).replace("\\", "/")).parts
        if "datos" not in partes:
            return None
        idx = len(partes) - 1 - list(reversed(partes)).index("datos")
        candidata = carpeta_datos.joinpath(*partes[idx + 1:])
        return str(candidata) if candidata.exists() else None

    columnas = [("notas", c) for c in _COLUMNAS_RUTA] + list(_OTRAS_RUTAS)
    with _conexion() as con:
        tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for tabla, col in columnas:
            if tabla not in tablas or col not in {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}:
                continue
            for fila in con.execute(f"SELECT rowid AS rid, {col} AS v FROM {tabla} WHERE {col} IS NOT NULL").fetchall():
                ruta = nueva(fila["v"])
                if ruta:
                    con.execute(f"UPDATE {tabla} SET {col} = ? WHERE rowid = ?", (ruta, fila["rid"]))
                    corregidas += 1
    return corregidas


# ---------- Contactos y envíos (despacho) ----------
def guardar_contacto(nombre, correo=None):
    """Alta o actualización de la agenda; cada uso suma para ordenar las propuestas."""
    nombre = (nombre or "").strip()
    correo = (correo or "").strip().lower() or None
    if not nombre:
        return
    with _conexion() as con:
        fila = con.execute(
            "SELECT id FROM contactos WHERE nombre = ? COLLATE NOCASE AND IFNULL(correo,'') = IFNULL(?,'')",
            (nombre, correo),
        ).fetchone()
        if fila:
            con.execute("UPDATE contactos SET usos = usos + 1, ultimo_uso = ? WHERE id = ?", (_ahora(), fila["id"]))
        else:
            con.execute("INSERT INTO contactos (nombre, correo, usos, ultimo_uso) VALUES (?, ?, 1, ?)",
                        (nombre, correo, _ahora()))


def listar_contactos(limite=500):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM contactos ORDER BY usos DESC, nombre LIMIT ?", (limite,))]


def registrar_envio(nota_id, medio, destinatario, correo, observacion=None):
    u = USUARIO_ACTUAL.get() or {}
    with _conexion() as con:
        con.execute(
            "INSERT INTO envios (nota_id, medio, destinatario, correo, observacion, usuario, fecha) VALUES (?,?,?,?,?,?,?)",
            (nota_id, medio, destinatario, correo, observacion, u.get("nombre") or "Sistema", _ahora()),
        )
    guardar_contacto(destinatario, correo)
    registrar_evento(nota_id, f"Envío registrado ({'por correo' if medio == 'CORREO' else 'entrega en mano'}) a {destinatario or '—'}")


def listar_envios(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM envios WHERE nota_id = ? ORDER BY id DESC", (nota_id,))]


def registrar_consulta_expediente(consulta, resultados, nota_id=None):
    u = USUARIO_ACTUAL.get() or {}
    with _conexion() as con:
        con.execute(
            "INSERT INTO consultas_expedientes (usuario_id, usuario, consulta, resultados, nota_id, fecha) VALUES (?,?,?,?,?,?)",
            (u.get("id"), u.get("nombre") or "Sistema", consulta, resultados, nota_id, _ahora()),
        )


def listar_consultas_expedientes(limite=200):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM consultas_expedientes ORDER BY id DESC LIMIT ?", (limite,))]


# ---------- Ajustes simples (clave/valor) ----------
def obtener_ajuste(clave, defecto=None):
    with _conexion() as con:
        f = con.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return f["valor"] if f else defecto


def guardar_ajuste(clave, valor):
    with _conexion() as con:
        con.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                    (clave, str(valor)))


# ---------- Tablero de calidad de la IA ----------
def calidad_ia():
    """Resumen por proceso/modelo para Administración → IA → Calidad."""
    with _conexion() as con:
        trabajos = [dict(f) for f in con.execute(
            """SELECT tipo, COALESCE(modelo, '—') AS modelo, COUNT(*) AS total,
                      SUM(estado = 'HECHO') AS hechos, SUM(estado = 'ERROR') AS errores,
                      SUM(estado = 'OMITIDO') AS omitidos,
                      AVG(CASE WHEN iniciado_en IS NOT NULL AND terminado_en IS NOT NULL
                          THEN (julianday(terminado_en) - julianday(iniciado_en)) * 86400 END) AS segundos
               FROM trabajos_ia GROUP BY tipo, modelo ORDER BY tipo, modelo""")]
        hallazgos = [dict(f) for f in con.execute(
            """SELECT COALESCE(modelo, '(reglas / sin dato)') AS modelo,
                      COUNT(*) AS total,
                      SUM(estado = 'ABIERTO') AS abiertos, SUM(estado = 'REVISADO') AS revisados,
                      SUM(estado = 'IGNORADO' AND IFNULL(verificacion,'') != 'DUDOSO') AS ignorados,
                      SUM(estado = 'ERRONEO') AS erroneos,
                      SUM(verificacion = 'DUDOSO') AS dudosos_verificador,
                      SUM(verificacion = 'OK') AS confirmados_verificador
               FROM consistencia_hallazgos WHERE origen = 'IA' GROUP BY modelo""")]
        mejoras = {f["estado"]: f["c"] for f in con.execute("SELECT estado, COUNT(*) AS c FROM mejoras_informe GROUP BY estado")}
        erroneos_activos = con.execute("SELECT COUNT(*) AS c FROM feedback_ia WHERE activo = 1").fetchone()["c"]
    for h in hallazgos:
        resueltos = (h["revisados"] or 0) + (h["ignorados"] or 0) + (h["erroneos"] or 0)
        h["util_pct"] = round(100 * (h["revisados"] or 0) / resueltos) if resueltos else None
        h["erroneo_pct"] = round(100 * (h["erroneos"] or 0) / resueltos) if resueltos else None
    dec = (mejoras.get("ACEPTADA", 0), mejoras.get("DESCARTADA", 0))
    return {"trabajos": trabajos, "hallazgos": hallazgos, "mejoras": mejoras,
            "mejoras_aceptacion_pct": round(100 * dec[0] / sum(dec)) if sum(dec) else None,
            "erroneos_activos": erroneos_activos}


# ---------- Plazos y escalamiento ----------
def fijar_vencimiento(nota_id, fecha):
    with _conexion() as con:
        con.execute("UPDATE notas SET vence = ?, escalada_en = NULL WHERE id = ?", (fecha or None, nota_id))
    registrar_evento(nota_id, f"Plazo: {fecha or 'vuelve al plazo por prioridad'}")


def escalar_vencidas(hoy=None):
    """Avisa UNA vez (Coordinación del equipo y Dirección) de cada nota que superó su plazo."""
    import auth, plazos
    from datetime import date
    hoy = hoy or date.today()
    avisadas = []
    with _conexion() as con:
        for n in con.execute(
            "SELECT * FROM notas WHERE escalada_en IS NULL AND estado IN (%s)" % ",".join("?" * len(plazos.ESTADOS_CON_PLAZO)),
            plazos.ESTADOS_CON_PLAZO,
        ).fetchall():
            n = dict(n)
            sem = plazos.semaforo(n, hoy)
            if not sem or sem["nivel"] != "vencida":
                continue
            destinos = [u["id"] for u in con.execute(
                "SELECT id, rol, equipo_id FROM usuarios WHERE activo = 1").fetchall()
                if u["rol"] in auth.ROLES_DIRECCION or (u["rol"] == auth.ROL_COORDINADOR and u["equipo_id"] == n["equipo_id"])]
            _notificar(con, destinos, f"La nota «{n['nombre_original']}» superó su plazo ({sem['texto'].lower()}).", f"/nota/{n['id']}")
            con.execute("UPDATE notas SET escalada_en = ? WHERE id = ?", (_ahora(), n["id"]))
            avisadas.append(n["id"])
    return avisadas


def recordar_por_vencer(hoy=None):
    """Recordatorio UNA vez, el día anterior o el mismo día del vencimiento, a quien tiene que actuar según la etapa."""
    import auth, plazos
    from datetime import date
    hoy = hoy or date.today()
    hechos = []
    with _conexion() as con:
        usuarios = [dict(u) for u in con.execute("SELECT id, rol, equipo_id FROM usuarios WHERE activo = 1")]
        for n in con.execute(
            "SELECT * FROM notas WHERE recordada_en IS NULL AND estado IN (%s)" % ",".join("?" * len(plazos.ESTADOS_CON_PLAZO)),
            plazos.ESTADOS_CON_PLAZO,
        ).fetchall():
            n = dict(n)
            sem = plazos.semaforo(n, hoy)
            if not sem or sem["nivel"] != "rojo":
                continue
            if n["estado"] in ("SUBIDA", "ERROR"):
                destinos = [u["id"] for u in usuarios if u["rol"] == auth.ROL_COORDINADOR and u["equipo_id"] == n["equipo_id"]] + ([n["usuario_id"]] if n.get("usuario_id") else [])
            elif n["estado"] in LISTAS_PARA_REMITIR:
                destinos = [u["id"] for u in usuarios if u["rol"] in auth.ROLES_MESA]
            else:
                destinos = [u["id"] for u in usuarios if u["rol"] in auth.ROLES_DIRECCION]
            _notificar(con, destinos, f"Recordatorio: «{n['nombre_original']}» {sem['texto'].lower()}.", f"/nota/{n['id']}")
            con.execute("UPDATE notas SET recordada_en = ? WHERE id = ?", (_ahora(), n["id"]))
            hechos.append(n["id"])
    return hechos


def fijar_plazo_respuesta(nota_id, plazo):
    """Plazo que la nota le da al destinatario para responder (se cuenta desde el despacho). None lo quita."""
    with _conexion() as con:
        con.execute("UPDATE notas SET plazo_respuesta = ?, respuesta_vence = NULL, respuesta_avisada = NULL WHERE id = ?",
                    (json.dumps(plazo, ensure_ascii=False) if plazo else None, nota_id))
    registrar_evento(nota_id, f"Plazo de respuesta: {plazo['texto']}" if plazo else "Se quitó el plazo de respuesta")


def _calcular_vence_respuesta(con, n, feriados=()):
    """Vencimiento del plazo de respuesta (desde el despacho); lo guarda y lo devuelve, o None si todavía no se despachó."""
    from datetime import date
    import agentes
    if not n.get("plazo_respuesta"):
        return None
    f = con.execute("SELECT MIN(fecha) FROM historial_estados WHERE nota_id = ? AND estado = 'DESPACHADA'", (n["id"],)).fetchone()[0]
    if not f:
        return None
    p = json.loads(n["plazo_respuesta"])
    base = date.fromisoformat(f[:10])
    if p.get("unidad") == "horas":
        v = base + timedelta(days=max(1, -(-int(p["cantidad"]) // 24)))
    elif p.get("habiles", True):
        v = agentes.sumar_habiles(base, int(p["cantidad"]), feriados)
    else:
        v = base + timedelta(days=int(p["cantidad"]))
    con.execute("UPDATE notas SET respuesta_vence = ? WHERE id = ?", (v.isoformat(), n["id"]))
    return v.isoformat()


def seguimiento_respuestas(hoy=None, feriados=()):
    """Para las notas despachadas con plazo de respuesta: calcula el vencimiento (desde el despacho) y avisa una vez
    al autor y a la coordinación del equipo cuando vence (o el día anterior)."""
    import auth
    from datetime import date
    import agentes
    hoy = hoy or date.today()
    avisos = []
    with _conexion() as con:
        for n in con.execute("SELECT * FROM notas WHERE plazo_respuesta IS NOT NULL AND estado = 'DESPACHADA' AND respuesta_avisada IS NULL").fetchall():
            n = dict(n)
            vence = n.get("respuesta_vence") or _calcular_vence_respuesta(con, n, feriados)
            if not vence:
                continue
            if date.fromisoformat(vence) - timedelta(days=1) <= hoy:
                destinos = [u["id"] for u in con.execute("SELECT id, rol, equipo_id FROM usuarios WHERE activo = 1").fetchall()
                            if u["rol"] == auth.ROL_COORDINADOR and u["equipo_id"] == n["equipo_id"]] + ([n["usuario_id"]] if n.get("usuario_id") else [])
                _notificar(con, destinos, f"Vence el plazo de respuesta de la nota «{n['nombre_original']}» ({vence[8:10]}/{vence[5:7]}). ¿Llegó la respuesta?", f"/nota/{n['id']}")
                con.execute("UPDATE notas SET respuesta_avisada = ? WHERE id = ?", (_ahora(), n["id"]))
                avisos.append(n["id"])
    return avisos


def respuestas_por_vencer(dias=7):
    from datetime import date
    hasta = (date.today() + timedelta(days=dias)).isoformat()
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT id, nombre_original, equipo_id, usuario_id, respuesta_vence FROM notas WHERE estado = 'DESPACHADA' AND respuesta_vence IS NOT NULL AND respuesta_vence <= ? ORDER BY respuesta_vence",
            (hasta,))]


# ---------- Ola 3: numeración ----------
def numeros_cargados(tipo):
    with _conexion() as con:
        if tipo == "PROVEIDO":
            return [(f["numero"], f["creado_en"]) for f in con.execute("SELECT numero, creado_en FROM proveidos_numerados")]
        return [(f["nro_nota"], f["fecha_nota"] or f["despacho_preparado_en"] or f["creado_en"]) for f in con.execute(
            "SELECT nro_nota, fecha_nota, despacho_preparado_en, creado_en FROM notas WHERE nro_nota IS NOT NULL AND trim(nro_nota) != ''")]


def contador_numeracion(anio, tipo):
    with _conexion() as con:
        f = con.execute("SELECT ultimo FROM numeracion WHERE anio = ? AND tipo = ?", (anio, tipo)).fetchone()
    return f["ultimo"] if f else None


def contador_inicial(anio, tipo):
    with _conexion() as con:
        f = con.execute("SELECT inicial FROM numeracion WHERE anio = ? AND tipo = ?", (anio, tipo)).fetchone()
    return f["inicial"] if f else None


def avanzar_contador(anio, tipo, numero, quien=""):
    with _conexion() as con:
        con.execute("""INSERT INTO numeracion (anio, tipo, ultimo, actualizado_en, actualizado_por) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(anio, tipo) DO UPDATE SET ultimo = MAX(ultimo, excluded.ultimo),
                       actualizado_en = excluded.actualizado_en, actualizado_por = excluded.actualizado_por""",
                    (anio, tipo, numero, _ahora(), quien))


def fijar_contador(anio, tipo, ultimo, quien="", inicial=False):
    """Fija a mano el último número usado (Mesa / Dirección), p. ej. al tomarlo del Excel de registro."""
    with _conexion() as con:
        con.execute("""INSERT INTO numeracion (anio, tipo, ultimo, inicial, actualizado_en, actualizado_por) VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(anio, tipo) DO UPDATE SET ultimo = excluded.ultimo,
                       inicial = COALESCE(excluded.inicial, inicial), actualizado_en = excluded.actualizado_en,
                       actualizado_por = excluded.actualizado_por""",
                    (anio, tipo, ultimo, ultimo if inicial else None, _ahora(), quien))


def listar_contadores():
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM numeracion ORDER BY anio DESC, tipo")]


def registrar_proveido_numerado(nota_id, numero, medio, usuario):
    with _conexion() as con:
        con.execute("INSERT INTO proveidos_numerados (nota_id, numero, medio, usuario, creado_en) VALUES (?,?,?,?,?)",
                    (nota_id, numero, medio, usuario, _ahora()))


def proveido_en_uso(numero):
    with _conexion() as con:
        return con.execute("SELECT nota_id FROM proveidos_numerados WHERE UPPER(numero) = UPPER(?) LIMIT 1", (numero,)).fetchone() is not None


def notas_con_numero():
    with _conexion() as con:
        filas = con.execute(
            """SELECT n.*, e.nombre AS equipo_nombre, u.nombre AS asesor_nombre,
                      (SELECT destinatario FROM envios v WHERE v.nota_id = n.id ORDER BY v.id DESC LIMIT 1) AS destinatario,
                      (SELECT CASE medio WHEN 'MANO' THEN 'En mano' ELSE 'Correo' END FROM envios v WHERE v.nota_id = n.id ORDER BY v.id DESC LIMIT 1) AS envio
               FROM notas n JOIN equipos e ON e.id = n.equipo_id LEFT JOIN usuarios u ON u.id = n.usuario_id
               WHERE n.nro_nota IS NOT NULL AND trim(n.nro_nota) != ''""").fetchall()
    return [dict(f) for f in filas]


# ---------- Ola 3: respuestas recibidas ----------
def registrar_respuesta(nota_id, fecha_recepcion, medio, remitente, resumen, archivo=None, nombre_archivo=None):
    """Carga manual de una respuesta: corta el seguimiento del plazo y avisa al autor y a la coordinación del equipo."""
    import auth
    u = USUARIO_ACTUAL.get() or {}
    with _conexion() as con:
        con.execute("""INSERT INTO respuestas (nota_id, fecha_recepcion, medio, remitente, resumen, archivo, nombre_archivo, cargado_por, creado_en)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (nota_id, fecha_recepcion, medio, remitente, resumen, archivo, nombre_archivo, u.get("nombre") or "Sistema", _ahora()))
        actual = dict(con.execute("SELECT * FROM notas WHERE id = ?", (nota_id,)).fetchone())
        if actual.get("plazo_respuesta") and not actual.get("respuesta_vence"):
            _calcular_vence_respuesta(con, actual)  # para poder decir si llegó en término (Ola 6, 109)
        con.execute("UPDATE notas SET respuesta_recibida_en = COALESCE(respuesta_recibida_en, ?), respuesta_avisada = COALESCE(respuesta_avisada, ?) WHERE id = ?",
                    (fecha_recepcion, _ahora(), nota_id))
        n = con.execute("SELECT nombre_original, usuario_id, equipo_id FROM notas WHERE id = ?", (nota_id,)).fetchone()
        destinos = [x["id"] for x in con.execute("SELECT id, rol, equipo_id FROM usuarios WHERE activo = 1").fetchall()
                    if x["rol"] == auth.ROL_COORDINADOR and x["equipo_id"] == n["equipo_id"]] + ([n["usuario_id"]] if n["usuario_id"] else [])
        _notificar(con, destinos, f"Llegó respuesta a la nota «{n['nombre_original']}»" + (f" de {remitente}" if remitente else "") + ".",
                   f"/nota/{nota_id}#respuestas")
    registrar_evento(nota_id, f"Respuesta recibida el {fecha_recepcion[8:10]}/{fecha_recepcion[5:7]}/{fecha_recepcion[:4]}"
                              + (f" de {remitente}" if remitente else "") + f" (cargada por {u.get('nombre') or 'Sistema'})")


def respuestas_de(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM respuestas WHERE nota_id = ? ORDER BY fecha_recepcion, id", (nota_id,))]


def obtener_respuesta(resp_id):
    with _conexion() as con:
        f = con.execute("SELECT * FROM respuestas WHERE id = ?", (resp_id,)).fetchone()
    return dict(f) if f else None


def resumen_respuestas(desde=None, hasta=None):
    from datetime import date
    hoy = date.today().isoformat()
    sql = "SELECT respuesta_vence, respuesta_recibida_en FROM notas WHERE plazo_respuesta IS NOT NULL AND estado = 'DESPACHADA'"
    params = []
    if desde:
        sql += " AND creado_en >= ?"; params.append(desde)
    if hasta:
        sql += " AND creado_en <= ?"; params.append(hasta + "T23:59:59")
    with _conexion() as con:
        filas = con.execute(sql, params).fetchall()
        sin_plazo = con.execute("SELECT COUNT(*) FROM notas WHERE plazo_respuesta IS NULL AND estado = 'DESPACHADA'"
                                + sql[sql.index("'DESPACHADA'") + len("'DESPACHADA'"):], params).fetchone()[0]
    import plazos
    from collections import Counter
    clases = Counter(plazos.estado_respuesta(dict(f, estado="DESPACHADA", plazo_respuesta="x"), hoy=date.fromisoformat(hoy))["clave"] for f in filas)
    return {"con_plazo": len(filas), "respondidas": sum(1 for f in filas if f["respuesta_recibida_en"]),
            "vencidas": sum(1 for f in filas if not f["respuesta_recibida_en"] and f["respuesta_vence"] and f["respuesta_vence"] < hoy),
            # Ola 6 (109): etiquetas explícitas
            "en_termino": clases.get("en_termino", 0) + clases.get("pendiente", 0), "respondidas_en_termino": clases.get("en_termino", 0),
            "fuera_de_termino": clases.get("fuera_de_termino", 0), "vencido": clases.get("vencido", 0), "sin_plazo": sin_plazo}


def respuestas_vencidas():
    """Notas despachadas con plazo de respuesta vencido y sin respuesta cargada (para sugerir una reiteración)."""
    from datetime import date
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            """SELECT * FROM notas WHERE estado = 'DESPACHADA' AND respuesta_vence IS NOT NULL AND respuesta_vence < ?
               AND respuesta_recibida_en IS NULL""", (date.today().isoformat(),))]


# ---------- Ola 3: plazos de respuesta por destinatario ----------
def recordar_plazo_destinatario(nombre, plazo):
    import agentes
    norm = agentes._norm(nombre)
    if not norm:
        return
    with _conexion() as con:
        con.execute("""INSERT INTO plazos_destinatario (norm, nombre, cantidad, unidad, habiles, actualizado_en) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(norm) DO UPDATE SET cantidad = excluded.cantidad, unidad = excluded.unidad, habiles = excluded.habiles,
                       usos = usos + 1, actualizado_en = excluded.actualizado_en""",
                    (norm, nombre, int(plazo["cantidad"]), plazo.get("unidad", "dias"), 1 if plazo.get("habiles", True) else 0, _ahora()))


def plazo_de_destinatario(nombre):
    import agentes
    norm = agentes._norm(nombre)
    if not norm:
        return None
    with _conexion() as con:
        f = con.execute("SELECT * FROM plazos_destinatario WHERE norm = ?", (norm,)).fetchone()
    return dict(f) if f else None


def listar_plazos_destinatario():
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM plazos_destinatario ORDER BY usos DESC, nombre")]


def borrar_plazo_destinatario(norm):
    with _conexion() as con:
        borrar_con_registro(con, "plazos_destinatario", "norm = ?", (norm,))


# ---------- Ola 3: notas para entregar en mano en un viaje ----------
def asignar_nota_a_viaje(viaje_id, nota_id, quien):
    with _conexion() as con:
        con.execute("""INSERT OR IGNORE INTO viaje_documentos (viaje_id, nota_id, asignado_por, creado_en) VALUES (?,?,?,?)""",
                    (viaje_id, nota_id, quien, _ahora()))
    registrar_evento(nota_id, f"Para entregar en mano en el viaje #{viaje_id} ({quien})")


def documentos_de_viaje(viaje_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            """SELECT d.*, n.nombre_original, n.nro_nota, n.unidad_id FROM viaje_documentos d JOIN notas n ON n.id = d.nota_id
               WHERE d.viaje_id = ? ORDER BY d.id""", (viaje_id,))]


def viajes_de_nota(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            """SELECT d.*, v.fecha, COALESCE(u.nombre, v.destino_otro, '—') AS destino, v.estado AS estado_viaje
               FROM viaje_documentos d JOIN viajes v ON v.id = d.viaje_id LEFT JOIN unidades u ON u.id = v.unidad_id
               WHERE d.nota_id = ? ORDER BY v.fecha""", (nota_id,))]


def resolver_documento_viaje(doc_id, estado, quien, observacion=None):
    with _conexion() as con:
        con.execute("UPDATE viaje_documentos SET estado = ?, resuelto_por = ?, resuelto_en = ?, observacion = ? WHERE id = ?",
                    (estado, quien, _ahora(), observacion, doc_id))
        f = con.execute("SELECT * FROM viaje_documentos WHERE id = ?", (doc_id,)).fetchone()
    return dict(f) if f else None


def notas_esperando_entrega(unidad_id=None):
    """Notas firmadas o despachadas con unidad, que van en mano y todavía no se entregaron ni tienen viaje asignado pendiente."""
    sql = """SELECT n.id, n.nombre_original, n.nro_nota, n.unidad_id, n.estado FROM notas n
             WHERE n.estado IN ('FIRMADA', 'PARA_REMITIR', 'DESPACHADA') AND n.unidad_id IS NOT NULL
               AND (n.tipo = 'NOTA A PPL' OR EXISTS (SELECT 1 FROM sugerencias_ia s WHERE s.nota_id = n.id AND s.agente = 'PROVEIDO'
                                                     AND s.contenido_json LIKE '%"MANO"%'))
               AND NOT EXISTS (SELECT 1 FROM envios e WHERE e.nota_id = n.id AND e.medio = 'MANO')
               AND NOT EXISTS (SELECT 1 FROM viaje_documentos d WHERE d.nota_id = n.id AND d.estado = 'PENDIENTE')"""
    params = []
    if unidad_id:
        sql += " AND n.unidad_id = ?"; params.append(unidad_id)
    with _conexion() as con:
        return [dict(f) for f in con.execute(sql + " ORDER BY n.id", params)]


def registrar_entrega_en_mano(nota_id, destinatario, entregado_por, fecha_entrega, viaje_id=None, recibido_por=None, observacion=None):
    u = USUARIO_ACTUAL.get() or {}
    with _conexion() as con:
        con.execute("""INSERT INTO envios (nota_id, medio, destinatario, correo, observacion, usuario, fecha, entregado_por, viaje_id, fecha_entrega, recibido_por)
                       VALUES (?, 'MANO', ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
                    (nota_id, destinatario, observacion, u.get("nombre") or "Sistema", _ahora(), entregado_por, viaje_id, fecha_entrega, recibido_por))
    registrar_evento(nota_id, f"Entregada en mano a {destinatario or '—'} por {entregado_por or '—'} el {fecha_entrega[8:10]}/{fecha_entrega[5:7]}"
                              + (f" (viaje #{viaje_id})" if viaje_id else ""))


def envios_de(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM envios WHERE nota_id = ? ORDER BY id", (nota_id,))]


def notas_con_entidad(tipo, norm):
    """Ids de las notas que mencionan esa persona / unidad / expediente (también por el N° de expediente cargado)."""
    with _conexion() as con:
        ids = {f[0] for f in con.execute("SELECT DISTINCT nota_id FROM nota_entidades WHERE tipo = ? AND norm = ?", (tipo, norm))}
        if tipo == "EXPEDIENTE":
            ids |= {f[0] for f in con.execute("SELECT id FROM notas WHERE nro_expte IS NOT NULL AND lower(replace(nro_expte, ' ', '')) = lower(replace(?, ' ', ''))", (norm,))}
    return sorted(ids)


def proveidos_de(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM proveidos_numerados WHERE nota_id = ? ORDER BY id", (nota_id,))]


# ---------- Ola 3: tiempos ----------
def historial_para_tiempos(desde=None, hasta=None, equipo_id=None):
    sql = "SELECT n.id, e.nombre AS equipo FROM notas n JOIN equipos e ON e.id = n.equipo_id WHERE n.estado != 'BORRADOR'"
    params = []
    if desde:
        sql += " AND n.creado_en >= ?"; params.append(desde)
    if hasta:
        sql += " AND n.creado_en <= ?"; params.append(hasta + "T23:59:59" if len(hasta) == 10 else hasta)
    if equipo_id is not None:
        sql += " AND n.equipo_id = ?"; params.append(equipo_id)
    with _conexion() as con:
        notas = {f["id"]: {"equipo": f["equipo"], "eventos": {}} for f in con.execute(sql, params)}
        if notas:
            for h in con.execute("SELECT nota_id, estado, fecha FROM historial_estados WHERE estado != 'REGISTRO' ORDER BY id"):
                if h["nota_id"] in notas:
                    try:
                        notas[h["nota_id"]]["eventos"].setdefault(h["estado"], []).append(datetime.fromisoformat(h["fecha"]))
                    except ValueError:
                        pass
    return list(notas.values())


# ---------- Ola 3: delegación de firma ----------
def ausencias_con_firma_delegada():
    """Ausencias vigentes de quien firma (Dirección) con una persona delegada que también firma."""
    import auth
    from datetime import datetime as _dt
    import fechas
    hoy = _dt.now(fechas.zona()).date().isoformat()
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            """SELECT a.*, u.nombre AS ausente, u.rol AS rol_ausente, d.nombre AS delegado, d.rol AS rol_delegado
               FROM ausencias a JOIN usuarios u ON u.id = a.usuario_id LEFT JOIN usuarios d ON d.id = a.delegado_id
               WHERE a.desde <= ? AND a.hasta >= ?""", (hoy, hoy)).fetchall()
            if f["rol_ausente"] in auth.ROLES_CON_FIRMA_PROPIA]


def marcar_aviso_firma(ausencia_id):
    with _conexion() as con:
        con.execute("UPDATE ausencias SET aviso_firma_en = ? WHERE id = ?", (_ahora(), ausencia_id))


# ---------- Comentarios internos ----------
def agregar_comentario(nota_id, texto):
    u = USUARIO_ACTUAL.get() or {}
    with _conexion() as con:
        cur = con.execute(
            "INSERT INTO comentarios (nota_id, usuario_id, usuario, texto, creado_en) VALUES (?,?,?,?,?)",
            (nota_id, u.get("id"), u.get("nombre") or "Sistema", texto, _ahora()),
        )
        return cur.lastrowid


def listar_comentarios(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM comentarios WHERE nota_id = ? ORDER BY id", (nota_id,))]


def notificar_usuarios(usuarios_ids, texto, url):
    with _conexion() as con:
        _notificar(con, usuarios_ids, texto, url)


# ---------- Ausencias y delegación ----------
def _ausencia_vigente(con, usuario_id):
    from datetime import datetime
    import fechas
    hoy = datetime.now(fechas.zona()).date().isoformat()
    f = con.execute(
        """SELECT a.*, u.nombre FROM ausencias a JOIN usuarios u ON u.id = a.usuario_id
           WHERE a.usuario_id = ? AND a.desde <= ? AND a.hasta >= ? ORDER BY a.id DESC LIMIT 1""",
        (usuario_id, hoy, hoy),
    ).fetchone()
    return dict(f) if f else None


def ausencia_vigente(usuario_id):
    with _conexion() as con:
        return _ausencia_vigente(con, usuario_id)


def crear_ausencia(usuario_id, desde, hasta, delegado_id):
    with _conexion() as con:
        con.execute("INSERT INTO ausencias (usuario_id, desde, hasta, delegado_id, creada_en) VALUES (?,?,?,?,?)",
                    (usuario_id, desde, hasta, delegado_id, _ahora()))


def borrar_ausencia(ausencia_id):
    with _conexion() as con:
        borrar_con_registro(con, "ausencias", "id = ?", (ausencia_id,))


def listar_ausencias(usuario_id=None):
    with _conexion() as con:
        sql = """SELECT a.*, u.nombre AS usuario_nombre, d.nombre AS delegado_nombre FROM ausencias a
                 JOIN usuarios u ON u.id = a.usuario_id JOIN usuarios d ON d.id = a.delegado_id"""
        args = ()
        if usuario_id:
            sql += " WHERE a.usuario_id = ?"; args = (usuario_id,)
        return [dict(f) for f in con.execute(sql + " ORDER BY a.desde DESC", args)]


# ---------- Duplicados ----------
# ---------- Beta 01: duplicados (comparar y decidir; descartar se puede deshacer) ----------
ESTADOS_DESCARTABLES = ("BORRADOR", "SUBIDA", "PROCESANDO", "ERROR", "RECHAZADA", "EN_REVISION", "PROCESADA", "ARCHIVADA", "ADJUNTO", "PARA_REMITIR")


def marcar_solo_mesa(nota_id, quien):
    """«Solo Mesa de Entradas»: el documento no pasa por control, coordinación ni firma; queda para que Mesa le
    ponga número y lo remita."""
    with _conexion() as con:
        con.execute("UPDATE notas SET modalidad = 'SOLO_MESA' WHERE id = ?", (nota_id,))
    n = obtener_nota(nota_id)
    actualizar_estado(nota_id, "PARA_REMITIR", desde=(n["estado"],),
                      comentario=f"Solo Mesa de Entradas (cargado por {quien}): sin control, coordinación ni firma; remisión directa")


def marcar_duplicado(nota_id, original_id):
    with _conexion() as con:
        con.execute("UPDATE notas SET duplicado_de = ? WHERE id = ?", (original_id, nota_id))


def no_es_duplicado(nota_id, quien):
    with _conexion() as con:
        con.execute("UPDATE notas SET duplicado_de = NULL WHERE id = ?", (nota_id,))
    registrar_evento(nota_id, f"Revisado: no es un duplicado ({quien})")


def descartar_nota(nota_id, quien, motivo="duplicado"):
    """Saca la nota de las bandejas sin borrarla (queda el archivo, el historial y se puede recuperar)."""
    n = obtener_nota(nota_id)
    if not n or n["estado"] not in ESTADOS_DESCARTABLES:
        raise ConflictoDeEstado("Este documento ya no se puede descartar (está firmado o despachado).")
    if n.get("bloque_id"):
        quitar_nota_de_bloque(nota_id)
        n = obtener_nota(nota_id)
    previo = "SUBIDA" if n["estado"] == "PROCESANDO" else n["estado"]  # al recuperarla vuelve a pedir el análisis
    actualizar_estado(nota_id, "DESCARTADA", comentario=f"Descartado ({motivo}) por {quien}; se puede recuperar",
                      desde=(n["estado"],), estado_previo=previo)
    with _conexion() as con:  # lo que la IA tenía pendiente para esta nota ya no corre
        con.execute("UPDATE trabajos_ia SET estado = 'OMITIDO', error = 'Nota descartada' WHERE nota_id = ? AND estado = 'PENDIENTE'", (nota_id,))


def recuperar_nota(nota_id, quien):
    n = obtener_nota(nota_id)
    if not n or n["estado"] != "DESCARTADA":
        raise ConflictoDeEstado("Este documento no está descartado.")
    previo = n.get("estado_previo") or ("ARCHIVADA" if es_pdf(n) else "BORRADOR")
    if previo == "ADJUNTO":
        previo = "ARCHIVADA"  # ya no está en el bloque
    actualizar_estado(nota_id, previo, comentario=f"Recuperado por {quien}", desde=("DESCARTADA",), estado_previo=None)


def duplicados_por_huella(sha256, hash_texto=None, limite=5):
    """Para avisar ANTES de cargar: documentos ya cargados con el mismo archivo o el mismo texto."""
    with _conexion() as con:
        filas = con.execute(
            """SELECT n.id, n.nombre_original, n.estado, n.creado_en, u.nombre AS usuario,
                      (n.sha256_original = ?) AS mismo_archivo
               FROM notas n LEFT JOIN usuarios u ON u.id = n.usuario_id
               WHERE n.estado != 'DESCARTADA' AND (n.sha256_original = ? OR (? IS NOT NULL AND n.hash_texto = ?))
               ORDER BY n.id DESC LIMIT ?""", (sha256, sha256, hash_texto, hash_texto, limite)).fetchall()
    return [dict(f) for f in filas]


def guardar_hash_texto(nota_id, h):
    with _conexion() as con:
        con.execute("UPDATE notas SET hash_texto = ? WHERE id = ?", (h, nota_id))


def buscar_duplicados(nota_id):
    """Otras notas con el mismo archivo (huella) o el mismo texto, sin contar
    las versiones de la misma cadena."""
    with _conexion() as con:
        n = con.execute("SELECT sha256_original, hash_texto, version_de FROM notas WHERE id = ?", (nota_id,)).fetchone()
        if not n:
            return []
        filas = con.execute(
            """SELECT id, nombre_original, estado, creado_en, version_de,
                      (sha256_original IS NOT NULL AND sha256_original = ?) AS mismo_archivo
               FROM notas WHERE id != ? AND ((sha256_original IS NOT NULL AND sha256_original = ?)
                                             OR (hash_texto IS NOT NULL AND hash_texto = ?))
               ORDER BY id DESC LIMIT 5""",
            (n["sha256_original"], nota_id, n["sha256_original"], n["hash_texto"]),
        ).fetchall()
    return [dict(f) for f in filas if f["id"] != n["version_de"] and f["version_de"] != nota_id]


# ---------- Glosario y modelos ----------
def guardar_entidades(nota_id, filas):
    """filas = [(tipo, valor, norm)]: reemplaza los datos extraídos del texto de la nota."""
    with _conexion() as con:
        con.execute("DELETE FROM nota_entidades WHERE nota_id = ?", (nota_id,))
        con.executemany("INSERT INTO nota_entidades (nota_id, tipo, valor, norm) VALUES (?,?,?,?)",
                        [(nota_id, t, v, n) for t, v, n in filas if n])


def entidades_de(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT tipo, valor, norm FROM nota_entidades WHERE nota_id = ? ORDER BY tipo, valor", (nota_id,))]


def candidatos_expte_adjunto(nota_id: int) -> list:
    """Expedientes candidatos para vincular a un PDF adjunto (desde nota_entidades y detenidos)."""
    with _conexion() as con:
        entidades = con.execute(
            "SELECT tipo, valor, norm FROM nota_entidades WHERE nota_id = ?", (nota_id,)
        ).fetchall()
        candidatos = [e["valor"] for e in entidades if e["tipo"] in ("EXPEDIENTE", "GDE")]
        lpus = [e["norm"] for e in entidades if e["tipo"] == "LPU"]
        for lpu in lpus[:3]:
            row = con.execute(
                "SELECT nro_expediente_ppn FROM detenidos WHERE lpu = ? AND nro_expediente_ppn IS NOT NULL LIMIT 1",
                (lpu,)
            ).fetchone()
            if row and row["nro_expediente_ppn"]:
                candidatos.append(row["nro_expediente_ppn"])
    return list(dict.fromkeys(candidatos))


def ids_de_cadena(nota_id):
    """Ids de la misma cadena de versiones (la nota, sus anteriores y sus posteriores)."""
    with _conexion() as con:
        filas = {f["id"]: f["version_de"] for f in con.execute("SELECT id, version_de FROM notas")}
    raiz = nota_id
    while filas.get(raiz):
        raiz = filas[raiz]
    cadena, cambio = {raiz}, True
    while cambio:
        cambio = False
        for i, v in filas.items():
            if v in cadena and i not in cadena:
                cadena.add(i); cambio = True
    return cadena | {nota_id}


def textos_recientes(dias=365):
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")
    with _conexion() as con:
        return [tuple(f) for f in con.execute(
            """SELECT t.nota_id, t.contenido, n.nombre_original, n.estado, n.creado_en FROM notas_texto t
               JOIN notas n ON n.id = t.nota_id WHERE n.creado_en >= ? AND n.estado != 'BORRADOR'""", (desde,))]


def antecedentes(nota_id, limite=8):
    """Otras notas que comparten persona (nombre o DNI), unidad o expediente con esta, las más recientes primero."""
    with _conexion() as con:
        filas = con.execute(
            """SELECT n.id, n.nombre_original, n.estado, n.creado_en, n.fecha_nota, n.nro_nota,
                      GROUP_CONCAT(DISTINCT e2.tipo || ':' || e2.valor) AS comunes
               FROM nota_entidades e1
               JOIN nota_entidades e2 ON e2.tipo = e1.tipo AND e2.norm = e1.norm AND e2.nota_id != e1.nota_id
               JOIN notas n ON n.id = e2.nota_id
               WHERE e1.nota_id = ? AND e1.tipo IN ('PERSONA', 'UNIDAD', 'EXPEDIENTE', 'GDE')
               GROUP BY n.id ORDER BY n.id DESC LIMIT ?""", (nota_id, limite * 3)).fetchall()
    cadena = ids_de_cadena(nota_id)
    salida = []
    for f in filas:
        if f["id"] in cadena:
            continue
        d = dict(f)
        d["comunes"] = sorted({c.split(":", 1)[1] for c in (f["comunes"] or "").split(",") if ":" in c})
        d["por_persona"] = any(c.startswith("PERSONA:") for c in (f["comunes"] or "").split(","))
        salida.append(d)
    salida.sort(key=lambda d: (not d["por_persona"], -d["id"]))
    return salida[:limite]


def estadisticas_entidades(tipo, desde=None, hasta=None, equipo_id=None, limite=15):
    sql = """SELECT e.valor, COUNT(DISTINCT e.nota_id) AS n FROM nota_entidades e JOIN notas n ON n.id = e.nota_id
             WHERE e.tipo = ? AND n.estado != 'BORRADOR'"""
    params = [tipo]
    if desde:
        sql += " AND n.creado_en >= ?"; params.append(desde)
    if hasta:
        sql += " AND n.creado_en <= ?"; params.append(hasta)
    if equipo_id is not None:
        sql += " AND n.equipo_id = ?"; params.append(equipo_id)
    sql += " GROUP BY e.norm ORDER BY n DESC, e.valor LIMIT ?"
    params.append(limite)
    with _conexion() as con:
        return [(f["valor"], f["n"]) for f in con.execute(sql, params)]


def notas_sin_entidades(limite=5000):
    with _conexion() as con:
        return [(f[0], f[1]) for f in con.execute(
            """SELECT t.nota_id, t.contenido FROM notas_texto t
               WHERE NOT EXISTS (SELECT 1 FROM nota_entidades e WHERE e.nota_id = t.nota_id) LIMIT ?""", (limite,))]


def fijar_tipo_documental(nota_id, clave, quien, origen="CONFIRMADO"):
    """Ola 6 (131): la persona confirma o cambia el tipo documental propuesto."""
    import tipos_documentales
    with _conexion() as con:
        con.execute("UPDATE notas SET tipo_documental = ?, tipo_documental_origen = ?, tipo_documental_por = ? WHERE id = ?",
                    (clave, origen, quien, nota_id))
    registrar_evento(nota_id, f"Tipo de documento: {tipos_documentales.ETIQUETA.get(clave, clave)} ({'confirmado' if origen == 'CONFIRMADO' else 'cambiado'} por {quien})")


def reasignar_nota(nota_id, equipo_id, usuario_id, quien):
    """Cambia el equipo y/o el asesor responsable de una nota. Deja cada cambio registrado (quién, antes, después, cuándo)
    en notas_reasignaciones y en el historial, y avisa a la persona asignada. Devuelve la lista de cambios."""
    with _conexion() as con:
        n = con.execute("SELECT n.equipo_id, n.usuario_id, n.nombre_original, e.nombre AS equipo, u.nombre AS asesor FROM notas n "
                        "LEFT JOIN equipos e ON e.id = n.equipo_id LEFT JOIN usuarios u ON u.id = n.usuario_id WHERE n.id = ?", (nota_id,)).fetchone()
        nuevo_eq = con.execute("SELECT nombre FROM equipos WHERE id = ?", (equipo_id,)).fetchone()
        nuevo_as = con.execute("SELECT nombre FROM usuarios WHERE id = ?", (usuario_id,)).fetchone() if usuario_id else None
        cambios = []
        if n["equipo_id"] != equipo_id:
            cambios.append(("equipo", n["equipo"], nuevo_eq["nombre"] if nuevo_eq else None))
        if n["usuario_id"] != usuario_id:
            cambios.append(("asesor", n["asesor"], nuevo_as["nombre"] if nuevo_as else None))
        if not cambios:
            return []
        con.execute("UPDATE notas SET equipo_id = ?, usuario_id = ? WHERE id = ?", (equipo_id, usuario_id, nota_id))
        ahora = _ahora()
        con.executemany("INSERT INTO notas_reasignaciones (nota_id, campo, antes, despues, usuario, fecha) VALUES (?,?,?,?,?,?)",
                        [(nota_id, c, a, d, quien, ahora) for c, a, d in cambios])
        if usuario_id and n["usuario_id"] != usuario_id:
            _notificar(con, [usuario_id], f"Te asignaron la nota «{n['nombre_original']}» ({quien}).", f"/nota/{nota_id}")
    registrar_evento(nota_id, "Reasignada por " + quien + ": " + "; ".join(
        f"{'Equipo' if c == 'equipo' else 'Asesor'}: {a or '—'} → {d or '—'}" for c, a, d in cambios))
    return cambios


def reasignaciones_de(nota_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM notas_reasignaciones WHERE nota_id = ? ORDER BY id DESC", (nota_id,))]


def texto_indexado(nota_id):
    with _conexion() as con:
        f = con.execute("SELECT contenido FROM notas_texto WHERE nota_id = ?", (nota_id,)).fetchone()
    return f["contenido"] if f else ""


def alertas_abiertas(nota_id):
    with _conexion() as con:
        return con.execute("SELECT COUNT(*) FROM consistencia_hallazgos WHERE nota_id = ? AND estado = 'ABIERTO'", (nota_id,)).fetchone()[0]


def fijar_tipo_si_falta(nota_id, tipo):
    with _conexion() as con:
        con.execute("UPDATE notas SET tipo = ? WHERE id = ? AND (tipo IS NULL OR tipo = '')", (tipo, nota_id))


def tablero_agentes():
    """Reforma 24: por agente, cuántas propuestas hizo, cuántas aceptó o descartó una persona y el tiempo de la IA."""
    filas = {}

    def fila(agente):
        return filas.setdefault(agente, {"agente": agente, "propuestas": 0, "aceptadas": 0, "descartadas": 0, "pendientes": 0,
                                         "segundos": None, "ia": 0})
    with _conexion() as con:
        for f in con.execute("""SELECT CASE WHEN origen = 'PRERREVISION' THEN 'PRERREVISION' ELSE 'CONSISTENCIA' END AS a, estado, COUNT(*) AS n
                                FROM consistencia_hallazgos GROUP BY a, estado"""):
            d = fila(f["a"]); d["propuestas"] += f["n"]
            if f["estado"] == "REVISADO": d["aceptadas"] += f["n"]
            elif f["estado"] in ("IGNORADO", "ERRONEO"): d["descartadas"] += f["n"]
            else: d["pendientes"] += f["n"]
        for f in con.execute("SELECT prioridad_sugerida_estado AS e, COUNT(*) AS n FROM notas WHERE prioridad_sugerida_estado IS NOT NULL GROUP BY e"):
            d = fila("CLASIFICACION"); d["propuestas"] += f["n"]
            d["aceptadas" if f["e"] == "ACEPTADA" else "descartadas" if f["e"] == "DESCARTADA" else "pendientes"] += f["n"]
        for f in con.execute("SELECT decision, COUNT(*) AS n FROM correcciones_director GROUP BY decision"):
            d = fila("CORRECCION"); d["propuestas"] += f["n"]
            d["aceptadas" if f["decision"] in ("aceptar", "modificar", "modificado") else "descartadas"] += f["n"]
        for f in con.execute("SELECT estado, COUNT(*) AS n FROM mejoras_informe GROUP BY estado"):
            d = fila("MEJORAS"); d["propuestas"] += f["n"]
            d["aceptadas" if f["estado"] == "ACEPTADA" else "descartadas" if f["estado"] == "DESCARTADA" else "pendientes"] += f["n"]
        for f in con.execute("SELECT agente, estado, COUNT(*) AS n, SUM(origen = 'ia') AS ia FROM sugerencias_ia GROUP BY agente, estado"):
            d = fila(f["agente"]); d["propuestas"] += f["n"]; d["ia"] += f["ia"] or 0
            d["aceptadas" if f["estado"] == "ACEPTADA" else "descartadas" if f["estado"] == "DESCARTADA" else "pendientes"] += f["n"]
        for f in con.execute("SELECT resumen_origen AS o, COUNT(*) AS n FROM notas WHERE resumen IS NOT NULL GROUP BY o"):
            d = fila("RESUMEN"); d["propuestas"] += f["n"]
            if f["o"] == "ia": d["ia"] += f["n"]
        por_tipo = {"CLASIFICAR": "CLASIFICACION", "CONSISTENCIA": "CONSISTENCIA", "MEJORAS": "MEJORAS", "CORRECCION": "CORRECCION", "RESUMEN": "RESUMEN"}
        for f in con.execute("""SELECT tipo, AVG((julianday(terminado_en) - julianday(iniciado_en)) * 86400) AS seg FROM trabajos_ia
                                WHERE estado = 'HECHO' AND iniciado_en IS NOT NULL AND terminado_en IS NOT NULL GROUP BY tipo"""):
            if f["tipo"] in por_tipo and f["seg"] is not None:
                fila(por_tipo[f["tipo"]])["segundos"] = round(f["seg"], 1)
    for d in filas.values():
        resueltas = d["aceptadas"] + d["descartadas"]
        d["tasa"] = round(100 * d["aceptadas"] / resueltas) if resueltas else None
        d["consejo"] = ("Pocos datos todavía" if resueltas < 10 else "Conviene mantenerlo" if d["tasa"] >= 50
                        else "Revisar: se descarta más de lo que se usa" if d["tasa"] >= 25 else "Evaluar si conviene apagarlo")
        if d["agente"] == "RESUMEN":
            d["consejo"] = "Se usa al firmar (no tiene aceptar o descartar)"
    return sorted(filas.values(), key=lambda d: -d["propuestas"])


def guardar_resumen(nota_id, texto, origen):
    with _conexion() as con:
        con.execute("UPDATE notas SET resumen = ?, resumen_origen = ? WHERE id = ?", (texto, origen, nota_id))


# ---------- Sugerencias de los agentes (aceptar / descartar) ----------
def proponer_sugerencia(nota_id, agente, clave, contenido, origen="reglas"):
    """Guarda una sugerencia nueva. Si ya existe la misma (misma clave), no la repite ni revive una descartada."""
    with _conexion() as con:
        con.execute(
            """INSERT OR IGNORE INTO sugerencias_ia (nota_id, agente, clave, contenido_json, origen, creado_en)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (nota_id, agente, clave, json.dumps(contenido, ensure_ascii=False), origen, _ahora()))
        f = con.execute("SELECT * FROM sugerencias_ia WHERE nota_id IS ? AND agente = ? AND clave = ?", (nota_id, agente, clave)).fetchone()
    return _sug(f)


def _sug(f):
    if not f:
        return None
    d = dict(f)
    try:
        d["contenido"] = json.loads(d.get("contenido_json") or "{}")
    except ValueError:
        d["contenido"] = {}
    return d


def sugerencias_de(nota_id, agente=None, estado="PENDIENTE"):
    sql, params = "SELECT * FROM sugerencias_ia WHERE nota_id = ?", [nota_id]
    if agente:
        sql += " AND agente = ?"; params.append(agente)
    if estado:
        sql += " AND estado = ?"; params.append(estado)
    with _conexion() as con:
        return [_sug(f) for f in con.execute(sql + " ORDER BY id", params)]


def obtener_sugerencia(sug_id):
    with _conexion() as con:
        return _sug(con.execute("SELECT * FROM sugerencias_ia WHERE id = ?", (sug_id,)).fetchone())


def resolver_sugerencia(sug_id, estado, quien):
    with _conexion() as con:
        con.execute("UPDATE sugerencias_ia SET estado = ?, resuelto_por = ?, resuelto_en = ? WHERE id = ? AND estado = 'PENDIENTE'",
                    (estado, quien, _ahora(), sug_id))


def registrar_uso_agente(nota_id, agente, estado, quien, contenido=None, origen="reglas"):
    """Para agentes que se usan a pedido (p. ej. el proveído): deja constancia de si se usó o se descartó."""
    with _conexion() as con:
        con.execute(
            """INSERT INTO sugerencias_ia (nota_id, agente, clave, contenido_json, estado, origen, creado_en, resuelto_por, resuelto_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nota_id, agente, f"uso-{uuid.uuid4().hex[:10]}", json.dumps(contenido or {}, ensure_ascii=False), estado, origen,
             _ahora(), quien, _ahora()))


def tablero_sugerencias():
    """Por agente: propuestas, aceptadas, descartadas y pendientes; tiempo medio hasta decidir (horas)."""
    with _conexion() as con:
        filas = con.execute(
            """SELECT agente, estado, COUNT(*) AS n,
                      AVG(CASE WHEN resuelto_en IS NOT NULL THEN (julianday(resuelto_en) - julianday(creado_en)) * 24 END) AS horas
               FROM sugerencias_ia GROUP BY agente, estado""").fetchall()
    salida = {}
    for f in filas:
        d = salida.setdefault(f["agente"], {"PENDIENTE": 0, "ACEPTADA": 0, "DESCARTADA": 0, "horas": []})
        d[f["estado"]] = d.get(f["estado"], 0) + f["n"]
        if f["horas"] is not None:
            d["horas"].append((f["horas"], f["n"]))
    for d in salida.values():
        tot = sum(n for _, n in d["horas"])
        d["horas_promedio"] = round(sum(h * n for h, n in d["horas"]) / tot, 1) if tot else None
        resueltas = d["ACEPTADA"] + d["DESCARTADA"]
        d["tasa"] = round(100 * d["ACEPTADA"] / resueltas) if resueltas else None
    return salida


def listar_terminos(solo_activos=True):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM terminos %s ORDER BY termino COLLATE NOCASE" % ("WHERE activo = 1" if solo_activos else ""))]


def agregar_termino(termino, preferido, nota):
    with _conexion() as con:
        con.execute("INSERT INTO terminos (termino, preferido, nota) VALUES (?,?,?)", (termino, preferido or None, nota or None))


def borrar_termino(termino_id):
    with _conexion() as con:
        borrar_con_registro(con, "terminos", "id = ?", (termino_id,))


def listar_modelos_nota():
    with _conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM modelos_nota ORDER BY nombre COLLATE NOCASE")]


def agregar_modelo_nota(nombre, descripcion, archivo):
    with _conexion() as con:
        con.execute("INSERT INTO modelos_nota (nombre, descripcion, archivo, creado_en) VALUES (?,?,?,?)",
                    (nombre, descripcion or None, str(archivo), _ahora()))


def obtener_modelo_nota(modelo_id):
    with _conexion() as con:
        f = con.execute("SELECT * FROM modelos_nota WHERE id = ?", (modelo_id,)).fetchone()
    return dict(f) if f else None


def borrar_modelo_nota(modelo_id):
    with _conexion() as con:
        borrar_con_registro(con, "modelos_nota", "id = ?", (modelo_id,))


# ---------- Correo: cola de avisos ----------
def _encolar_correo(con, usuario_id, texto, url):
    f = con.execute("SELECT valor FROM ajustes WHERE clave = 'correo_activo'").fetchone()
    if not f or f["valor"] != "1":
        return
    i = con.execute("SELECT valor FROM ajustes WHERE clave = 'correo_inmediatos'").fetchone()
    if not i or i["valor"] != "1":
        return
    u = con.execute("SELECT correo FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    if u and u["correo"]:
        con.execute("INSERT INTO cola_correo (usuario_id, asunto, cuerpo, url, creada_en) VALUES (?,?,?,?,?)",
                    (usuario_id, "Aviso — Notas DGPDH", texto, url, _ahora()))


def correos_pendientes(limite=200):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            """SELECT c.*, u.correo, u.nombre FROM cola_correo c JOIN usuarios u ON u.id = c.usuario_id
               WHERE c.enviado = 0 ORDER BY c.id LIMIT ?""", (limite,))]


def marcar_correos_enviados(ids):
    if ids:
        with _conexion() as con:
            con.execute("UPDATE cola_correo SET enviado = 1 WHERE id IN (%s)" % ",".join("?" * len(ids)), list(ids))


def fijar_correo_usuario(usuario_id, correo):
    with _conexion() as con:
        con.execute("UPDATE usuarios SET correo = ? WHERE id = ?", ((correo or "").strip().lower() or None, usuario_id))


def resumen_pendientes_por_usuario():
    """{usuario_id: [(texto, url)]} de avisos sin leer (para el resumen diario)."""
    with _conexion() as con:
        r = {}
        for f in con.execute("SELECT usuario_id, texto, url FROM notificaciones WHERE leida = 0 ORDER BY id"):
            r.setdefault(f["usuario_id"], []).append((f["texto"], f["url"]))
    return r


# ---------- Catálogo de unidades (compartido por Notas y, a futuro, Traslados) ----------
CAMPOS_UNIDAD = ("nombre", "tipo", "zona", "ciudad", "provincia", "fuerza", "correo", "direccion", "alias", "lat", "lng",
                 "telefono_guardia", "horarios_visita", "contactos")


def _completar_ubicaciones(con):
    """Ciudad y provincia de las unidades cuyo lugar es conocido; solo completa lo que está vacío."""
    ruta = Path(__file__).parent / "datos_semilla" / "ubicaciones.json"
    if not ruta.is_file():
        return
    try:
        for nombre, (ciudad, provincia) in json.loads(ruta.read_text(encoding="utf-8")).items():
            con.execute("UPDATE unidades SET ciudad = COALESCE(NULLIF(ciudad, ''), ?), provincia = COALESCE(NULLIF(provincia, ''), ?) "
                        "WHERE nombre = ? COLLATE NOCASE", (ciudad, provincia, nombre))
    except Exception:
        logging.exception("No se pudieron completar las ubicaciones de las unidades")


def _sembrar_unidades(con):
    _sembrar_unidades_base(con)
    _completar_ubicaciones(con)


def _sembrar_unidades_base(con):
    if con.execute("SELECT COUNT(*) AS c FROM unidades").fetchone()["c"]:
        return
    ruta = Path(__file__).parent / "datos_semilla" / "unidades.json"
    if not ruta.is_file():
        return
    try:
        for u in json.loads(ruta.read_text(encoding="utf-8")):
            con.execute(
                "INSERT OR IGNORE INTO unidades (nombre, tipo, zona, fuerza, creada_en, actualizada_en) VALUES (?,?,?,?,?,?)",
                (u["nombre"], u.get("tipo"), u.get("zona"), u.get("fuerza"), _ahora(), _ahora()),
            )
    except Exception:
        logging.exception("No se pudo cargar el catálogo inicial de unidades")


def _norm(s):
    import unicodedata
    return re.sub(r"\s+", " ", "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")).strip()


def listar_unidades(q=None, tipo=None, provincia=None, solo_activas=True, fuerza_id=None, delegacion=None):
    with _conexion() as con:
        filas = [dict(f) for f in con.execute(
            "SELECT * FROM unidades %s ORDER BY nombre COLLATE NOCASE" % ("WHERE activa = 1" if solo_activas else ""))]
    if q:
        palabras = _norm(q).split()
        filas = [u for u in filas if all(p in _norm(" ".join(str(u.get(c) or "") for c in CAMPOS_UNIDAD)) for p in palabras)]
    if tipo:
        filas = [u for u in filas if u["tipo"] == tipo]
    if provincia:
        filas = [u for u in filas if _norm(u["provincia"]) == _norm(provincia)]
    if fuerza_id:
        filas = [u for u in filas if (u.get("fuerza_id") or 0) == int(fuerza_id) or (int(fuerza_id) == -1 and not u.get("fuerza_id"))]
    if delegacion:
        filas = [u for u in filas if (u.get("delegacion_ppn") or "") == delegacion]
    return filas


def obtener_unidad(unidad_id):
    if not unidad_id:
        return None
    with _conexion() as con:
        f = con.execute("SELECT * FROM unidades WHERE id = ?", (unidad_id,)).fetchone()
    return dict(f) if f else None


EXTRA_UNIDAD = ("fuerza_id", "director", "delegacion_ppn")


def fuerza_por_texto(texto, con=None):
    """El id de la fuerza que coincide por nombre o sigla (sin tildes ni mayúsculas), o None."""
    t = _norm(texto)
    if not t:
        return None
    def buscar(c):
        try:
            for f in c.execute("SELECT id, nombre, sigla FROM fuerzas"):
                if t in (_norm(f["nombre"]), _norm(f["sigla"] or "")):
                    return f["id"]
        except sqlite3.OperationalError:
            return None
        return None
    if con is not None:
        return buscar(con)
    with _conexion() as c:
        return buscar(c)


def guardar_unidad(unidad_id, datos):
    """Alta (unidad_id=None) o modificación. Levanta ValueError si falta el
    nombre, si el correo no parece válido o si el nombre ya existe."""
    uid = _guardar_unidad_base(unidad_id, datos)
    extras = {}
    if "fuerza_id" in datos:
        v = str(datos.get("fuerza_id") or "")
        extras["fuerza_id"] = int(v) if v.isdigit() else None
    elif str(datos.get("fuerza") or "").strip():  # CSV o pantallas viejas: la fuerza por nombre o sigla
        extras["fuerza_id"] = fuerza_por_texto(datos.get("fuerza"))
    for c in ("director", "delegacion_ppn"):
        if c in datos:
            extras[c] = re.sub(r"\s+", " ", str(datos.get(c) or "")).strip() or None
    if extras:
        with _conexion() as con:
            if extras.get("fuerza_id"):
                f = con.execute("SELECT nombre FROM fuerzas WHERE id = ?", (extras["fuerza_id"],)).fetchone()
                if f:  # el texto de «fuerza» acompaña a la elegida (exportaciones y pantallas viejas)
                    con.execute("UPDATE unidades SET fuerza = ? WHERE id = ?", (f["nombre"], uid))
            con.execute("UPDATE unidades SET " + ", ".join(f"{c} = ?" for c in extras) + " WHERE id = ?", list(extras.values()) + [uid])
    return uid


def _guardar_unidad_base(unidad_id, datos):
    d = {c: re.sub(r"\s+", " ", str(datos.get(c) or "")).strip() or None for c in CAMPOS_UNIDAD}
    if not d["nombre"]:
        raise ValueError("Falta el nombre de la unidad.")
    for c, tope in (("lat", 90), ("lng", 180)):
        if d[c] is not None:
            try:
                d[c] = float(d[c].replace(",", "."))
            except ValueError:
                raise ValueError("La latitud y la longitud tienen que ser números (ej.: -34.60).")
            if abs(d[c]) > tope:
                raise ValueError("La latitud o la longitud están fuera de rango.")
    if d["correo"] and not all(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", x.strip()) for x in re.split(r"[;,]", d["correo"]) if x.strip()):
        raise ValueError("El correo no es válido (podés poner varios separados por «;»).")
    with _conexion() as con:
        repetida = con.execute("SELECT id FROM unidades WHERE nombre = ? COLLATE NOCASE AND id != ?", (d["nombre"], unidad_id or -1)).fetchone()
        if repetida:
            raise ValueError("Ya existe una unidad con ese nombre.")
        if unidad_id:
            con.execute(
                "UPDATE unidades SET " + ", ".join(f"{c} = ?" for c in CAMPOS_UNIDAD) + ", actualizada_en = ? WHERE id = ?",
                [d[c] for c in CAMPOS_UNIDAD] + [_ahora(), unidad_id])
            return unidad_id
        cur = con.execute(
            "INSERT INTO unidades (" + ", ".join(CAMPOS_UNIDAD) + ", creada_en, actualizada_en) VALUES (" + ",".join("?" * (len(CAMPOS_UNIDAD) + 2)) + ")",
            [d[c] for c in CAMPOS_UNIDAD] + [_ahora(), _ahora()])
        return cur.lastrowid


def cambiar_unidad_activa(unidad_id, activa):
    with _conexion() as con:
        con.execute("UPDATE unidades SET activa = ?, actualizada_en = ? WHERE id = ?", (1 if activa else 0, _ahora(), unidad_id))


def importar_unidades(filas):
    """filas: lista de dicts con las claves de CAMPOS_UNIDAD (+ 'activa' opcional).
    Crea las nuevas y completa las existentes (por nombre) SOLO con los campos que
    vienen con valor. Devuelve (creadas, actualizadas, errores)."""
    creadas = actualizadas = 0
    errores = []
    for i, f in enumerate(filas, start=2):
        nombre = re.sub(r"\s+", " ", str(f.get("nombre") or "")).strip()
        if not nombre:
            continue
        try:
            with _conexion() as con:
                ex = con.execute("SELECT * FROM unidades WHERE nombre = ? COLLATE NOCASE", (nombre,)).fetchone()
            if ex:
                combinado = dict(ex)
                combinado.update({c: f[c] for c in CAMPOS_UNIDAD if str(f.get(c) or "").strip()})
                guardar_unidad(ex["id"], combinado)
                if str(f.get("activa") or "").strip().lower() in ("0", "no", "false"):
                    cambiar_unidad_activa(ex["id"], False)
                actualizadas += 1
            else:
                guardar_unidad(None, f)
                creadas += 1
        except ValueError as e:
            errores.append(f"Fila {i} ({nombre}): {e}")
    return creadas, actualizadas, errores


_PATRONES_UNIDADES = {"firma": None, "lista": []}


def _patrones_unidades():
    """Expresiones de búsqueda de cada unidad, armadas una sola vez mientras el catálogo no cambie (antes se recompilaban
    para cada documento: con un lote de 80 notas el panel de despacho tardaba 20 segundos)."""
    unidades = listar_unidades()
    firma = tuple((u["id"], u["nombre"], u["alias"]) for u in unidades)
    if firma == _PATRONES_UNIDADES["firma"]:
        return _PATRONES_UNIDADES["lista"]
    lista = []
    for u in unidades:
        nombres = {u["nombre"]}
        nombres.update(x.strip() for x in re.split(r"[;|]", u["alias"] or "") if x.strip())
        nombres.update(m for m in re.findall(r"-\s*([^-]{2,20}?)\s*-", u["nombre"]))
        pats = []
        for n in nombres:
            nn = _norm(n.strip(" .-"))
            if len(nn) >= 3:
                pats.append((len(nn), re.compile(r"(?<![a-z0-9])" + re.escape(nn) + r"(?![a-z0-9])")))
        lista.append((u, pats))
    _PATRONES_UNIDADES.update(firma=firma, lista=lista)
    return lista


def proponer_unidades(texto, limite=3):
    """Unidades del catálogo que se nombran en el texto (por nombre, alias o la
    sigla entre guiones, ej. «-CPF I-»). Primero las de nombre más largo."""
    t = " " + _norm(texto) + " "
    halladas = []
    for u, pats in _patrones_unidades():
        mejor = max((largo for largo, p in pats if p.search(t)), default=0)
        if mejor:
            halladas.append((mejor, u))
    halladas.sort(key=lambda x: (-x[0], x[1]["nombre"]))
    return [u for _, u in halladas[:limite]]


def fijar_unidad_nota(nota_id, unidad_id):
    with _conexion() as con:
        con.execute("UPDATE notas SET unidad_id = ? WHERE id = ?", (unidad_id or None, nota_id))
    u = obtener_unidad(unidad_id)
    registrar_evento(nota_id, f"Unidad: {u['nombre']}" if u else "Unidad: se quitó")


# ---------- Unidades por defecto de cada equipo ----------
def unidades_de_equipo(equipo_id):
    with _conexion() as con:
        return [dict(f) for f in con.execute(
            """SELECT u.*, eu.orden FROM equipo_unidades eu JOIN unidades u ON u.id = eu.unidad_id
               WHERE eu.equipo_id = ? AND u.activa = 1 ORDER BY eu.orden, u.nombre COLLATE NOCASE""", (equipo_id,))]


def fijar_unidades_de_equipo(equipo_id, unidad_ids):
    """Reemplaza las unidades por defecto del equipo (el orden es el de la lista)."""
    vistos, ids = set(), []
    for i in unidad_ids or []:
        if str(i).isdigit() and int(i) not in vistos and obtener_unidad(int(i)):
            vistos.add(int(i))
            ids.append(int(i))
    with _conexion() as con:
        borrar_con_registro(con, "equipo_unidades", "equipo_id = ?", (equipo_id,))
        for n, i in enumerate(ids):
            con.execute("INSERT INTO equipo_unidades (equipo_id, unidad_id, orden) VALUES (?,?,?)", (equipo_id, i, n))
    return ids


def marcar_avatar(usuario_id, tiene):
    with _conexion() as con:
        con.execute("UPDATE usuarios SET avatar = ? WHERE id = ?", (1 if tiene else 0, usuario_id))


# =====================================================================
# Limpieza de notas de prueba (Administración → Estado del sistema)
# =====================================================================
# Se borra: las notas y todo lo que cuelga de ellas (historial, análisis, avisos, respuestas, envíos, entregas en mano,
# vistas, texto indexado y datos extraídos que sirven de antecedente) y la numeración guardada por el sistema
# (el próximo número tiene que salir del registro de Mesa de Entradas).
# Se conserva lo aprendido: correcciones del Director (patrones), reportes marcados como erróneos, glosario,
# plazos por destinatario, modelos y plantillas, y las decisiones ya tomadas sobre sugerencias de los asistentes.
_LIMPIEZA_BORRAR = (  # (tabla, condición, etiqueta)
    ("historial_estados", "1=1", "Historial de estados"),
    ("consistencia_hallazgos", "1=1", "Avisos de consistencia y prerrevisión"),
    ("mejoras_informe", "1=1", "Informes de mejoras"),
    ("trabajos_ia", "nota_id IS NOT NULL", "Trabajos de análisis en cola"),
    ("nota_entidades", "1=1", "Datos extraídos (personas, unidades, expedientes) — antecedentes"),
    ("sugerencias_ia", "estado = 'PENDIENTE'", "Sugerencias de asistentes sin decidir"),
    ("proveidos_numerados", "1=1", "Proveídos numerados"),
    ("respuestas", "1=1", "Respuestas cargadas"),
    ("viaje_documentos", "1=1", "Documentos para entregar en viajes"),
    ("comentarios", "1=1", "Comentarios"),
    ("envios", "1=1", "Envíos"),
    ("consultas_expedientes", "nota_id IS NOT NULL", "Consultas de expediente de las notas"),
    ("accesos_documentos", "nota_id IS NOT NULL", "Registro de accesos a los documentos"),
    ("notas_vistas", "1=1", "Marcas de «vista»"),
    ("notas_texto", "1=1", "Texto indexado para búsqueda y antecedentes"),
    ("notificaciones", "url LIKE '/nota/%' OR url LIKE '/despacho%' OR url LIKE '/circuito%' OR url LIKE '/firma%'", "Avisos que apuntan a notas"),
    ("cola_correo", "enviado = 0 AND (url LIKE '/nota/%' OR url LIKE '/despacho%')", "Correos pendientes sobre notas"),
    ("numeracion", "1=1", "Numeración guardada por el sistema (notas y proveídos)"),
)
_LIMPIEZA_CONSERVAR = (
    ("correcciones_director", "Correcciones del Director (de acá salen los patrones aprendidos)"),
    ("feedback_ia", "Reportes de la IA marcados como erróneos"),
    ("terminos", "Glosario institucional"),
    ("plazos_destinatario", "Plazos recordados por destinatario"),
    ("modelos_nota", "Modelos y plantillas de la biblioteca"),
)
_COLUMNAS_ARCHIVO_NOTA = ("ruta_original", "ruta_resultado", "ruta_firmada", "ruta_base", "ruta_despacho_docx", "ruta_despacho_pdf", "ruta_analizada")


def _existe_tabla(con, tabla):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabla,)).fetchone())


def _columnas(con, tabla):
    return {f[1] for f in con.execute(f"PRAGMA table_info({tabla})")}


def previa_limpieza_notas():
    """Qué se borraría y qué se conserva (cantidades), sin tocar nada."""
    with _conexion() as con:
        borrar = [{"tabla": "notas", "etiqueta": "Notas", "n": con.execute("SELECT COUNT(*) FROM notas").fetchone()[0]}]
        for t, cond, etq in _LIMPIEZA_BORRAR:
            if _existe_tabla(con, t):
                borrar.append({"tabla": t, "etiqueta": etq, "n": con.execute(f"SELECT COUNT(*) FROM {t} WHERE {cond}").fetchone()[0]})
        conservar = [{"tabla": t, "etiqueta": etq, "n": con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]}
                     for t, etq in _LIMPIEZA_CONSERVAR if _existe_tabla(con, t)]
        if _existe_tabla(con, "sugerencias_ia"):
            total = con.execute("SELECT COUNT(*) FROM sugerencias_ia").fetchone()[0]
            pend = next((b["n"] for b in borrar if b["tabla"] == "sugerencias_ia"), 0)
            conservar.append({"tabla": "sugerencias_ia", "etiqueta": "Decisiones sobre sugerencias de los asistentes (estadísticas)", "n": total - pend})
        archivos = 0
        for f in con.execute("SELECT * FROM notas"):
            archivos += sum(1 for c in _COLUMNAS_ARCHIVO_NOTA if c in f.keys() and f[c])
    borrar.append({"tabla": "archivos", "etiqueta": "Archivos Word/PDF de las notas", "n": archivos})
    return {"borrar": borrar, "conservar": conservar}


def _quitar_fk_correcciones(con):
    """correcciones_director apunta a notas con clave foránea: se rehace sin ella para poder borrar las notas
    y conservar las correcciones (lo aprendido)."""
    sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='correcciones_director'").fetchone()
    if not sql or "REFERENCES notas" not in sql[0]:
        return
    cols = [f[1] for f in con.execute("PRAGMA table_info(correcciones_director)")]
    nuevo = sql[0].replace("REFERENCES notas(id)", "").replace("CREATE TABLE correcciones_director", "CREATE TABLE correcciones_director_nueva", 1)
    nuevo = nuevo.replace("CREATE TABLE IF NOT EXISTS correcciones_director", "CREATE TABLE correcciones_director_nueva", 1)
    con.execute(nuevo)
    lista = ", ".join(cols)
    con.execute(f"INSERT INTO correcciones_director_nueva ({lista}) SELECT {lista} FROM correcciones_director")
    con.execute("DROP TABLE correcciones_director")
    con.execute("ALTER TABLE correcciones_director_nueva RENAME TO correcciones_director")


def limpiar_notas(carpeta_datos, quien=""):
    """Borra todas las notas y lo que depende de ellas; conserva lo aprendido. Los archivos se borran solo si están
    dentro de la carpeta de datos. Devuelve {borrados: {tabla: n}, archivos: n}. Hacer un respaldo ANTES (lo hace la pantalla)."""
    carpeta = Path(carpeta_datos).resolve()
    rutas = []
    borrados = {}
    with _conexion() as con:
        for f in con.execute("SELECT * FROM notas"):
            rutas += [f[c] for c in _COLUMNAS_ARCHIVO_NOTA if c in f.keys() and f[c]]
        if _existe_tabla(con, "respuestas") and "archivo" in _columnas(con, "respuestas"):
            rutas += [f[0] for f in con.execute("SELECT archivo FROM respuestas WHERE archivo IS NOT NULL")]
        _quitar_fk_correcciones(con)
        if _existe_tabla(con, "sugerencias_ia"):
            borrados["sugerencias_ia"] = con.execute("DELETE FROM sugerencias_ia WHERE estado = 'PENDIENTE'").rowcount
            con.execute("UPDATE sugerencias_ia SET nota_id = NULL")  # las decisiones quedan para las estadísticas
        for t, cond, _ in _LIMPIEZA_BORRAR:
            if t == "sugerencias_ia" or not _existe_tabla(con, t):
                continue
            borrados[t] = con.execute(f"DELETE FROM {t} WHERE {cond}").rowcount
        if _existe_tabla(con, "notas_fts"):
            con.execute("DELETE FROM notas_fts")  # índice de búsqueda de texto completo
        if _existe_tabla(con, "notas_raices"):
            con.execute("DELETE FROM notas_raices")  # índice por raíces (propuesta 11), también derivado
        if "ref_nota_id" in _columnas(con, "notas"):
            con.execute("UPDATE notas SET ref_nota_id = NULL")
        borrados["notas"] = con.execute("DELETE FROM notas").rowcount
        borrados["bloques"] = con.execute("DELETE FROM bloques").rowcount
        from dgpdh.seguridad import cadenas  # propuesta 50: el historial borrado a propósito reinicia su cadena
        cadenas.reiniciar(con, "historial_estados", f"Limpiar notas · {quien} · {borrados['notas']} nota(s)")
        con.execute("INSERT INTO ajustes (clave, valor) VALUES ('limpieza_notas_ultima', ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                    (f"{_ahora()} · {quien} · {borrados['notas']} nota(s)",))
    archivos = 0
    for r in set(rutas):
        try:
            p = Path(r)
            p = (p if p.is_absolute() else carpeta / p).resolve()
            if carpeta in p.parents and p.is_file():
                p.unlink()
                archivos += 1
        except OSError:
            pass
    return {"borrados": borrados, "archivos": archivos}


def registrar_descarga_mesa(paquete_id: str, archivos: list, agente: dict) -> None:
    """Registra en descargas_mesa cada archivo de un paquete descargado por Mesa de Entradas.

    archivos: lista de dicts con claves nota_id, bloque_id, archivo_nombre, lpu, dni,
              expediente_ppn y tipo_archivo ("ADJUNTO" o "FIRMADO").
    agente: dict con claves id (int) y nombre (str).
    """
    fecha = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _conexion() as con:
        for archivo in archivos:
            con.execute(
                """INSERT INTO descargas_mesa
                   (paquete_id, nota_id, bloque_id, archivo_nombre, lpu, dni,
                    expediente_ppn, agente_id, agente_nombre, fecha_descarga, tipo_archivo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    paquete_id,
                    archivo.get("nota_id"),
                    archivo.get("bloque_id"),
                    archivo["archivo_nombre"],
                    archivo.get("lpu"),
                    archivo.get("dni"),
                    archivo.get("expediente_ppn"),
                    agente["id"],
                    agente["nombre"],
                    fecha,
                    archivo["tipo_archivo"],
                ),
            )


def listar_descargas_mesa(filtros: dict | None = None) -> list:
    """Devuelve registros de descargas_mesa ordenados por fecha desc.

    filtros opcionales: paquete_id (str), nota_id (int), fecha_desde (str ISO), fecha_hasta (str ISO).
    """
    clausulas = []
    params = []
    if filtros:
        if filtros.get("paquete_id"):
            clausulas.append("paquete_id = ?")
            params.append(filtros["paquete_id"])
        if filtros.get("nota_id"):
            clausulas.append("nota_id = ?")
            params.append(filtros["nota_id"])
        if filtros.get("fecha_desde"):
            clausulas.append("fecha_descarga >= ?")
            params.append(filtros["fecha_desde"])
        if filtros.get("fecha_hasta"):
            clausulas.append("fecha_descarga <= ?")
            params.append(filtros["fecha_hasta"])
    where = ("WHERE " + " AND ".join(clausulas)) if clausulas else ""
    with _conexion() as con:
        rows = con.execute(
            f"SELECT * FROM descargas_mesa {where} ORDER BY fecha_descarga DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def obtener_archivados_sin_descarga() -> list:
    """PDFs en estado ARCHIVADA con tipo NULL que nunca fueron descargados por Mesa."""
    with _conexion() as con:
        rows = con.execute("""
            SELECT n.*, e.nombre AS equipo_nombre,
                   u.nombre AS asesor_nombre
            FROM notas n
            LEFT JOIN equipos e ON n.equipo_id = e.id
            LEFT JOIN usuarios u ON n.usuario_id = u.id
            WHERE n.estado = 'ARCHIVADA'
              AND (n.tipo IS NULL OR n.tipo = '')
              AND n.id NOT IN (
                  SELECT nota_id FROM descargas_mesa WHERE nota_id IS NOT NULL
              )
            ORDER BY n.creado_en
        """).fetchall()
    return [dict(r) for r in rows]


def resetear_archivado_a_subida(nota_id: int, usuario_nombre: str) -> None:
    """Resetea un PDF ARCHIVADA-sin-descarga al estado SUBIDA para que entre al circuito.
    No borra nada. No mueve archivos. Registra en historial_estados.
    """
    with _conexion() as con:
        con.execute(
            """INSERT INTO historial_estados (nota_id, estado, comentario, fecha, usuario)
               VALUES (?, 'SUBIDA', ?, datetime('now'), ?)""",
            (nota_id,
             "Reseteo manual: PDF archivado automáticamente antes del circuito, devuelto al inicio por administración",
             usuario_nombre),
        )
        con.execute(
            """UPDATE notas SET estado = 'SUBIDA', tipo = 'ADJUNTO', modalidad = 'SOLO_MESA'
               WHERE id = ? AND estado = 'ARCHIVADA' AND (tipo IS NULL OR tipo = '')""",
            (nota_id,),
        )
