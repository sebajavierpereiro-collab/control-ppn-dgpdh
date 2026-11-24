"""Personas detenidas: alta, baja y modificación (ABM), con importación y exportación de la base.

Decisión del organismo (devolución de las nuevas especificaciones, fila 126): los datos de la persona detenida salen de
un ABM propio que se puede cargar a mano o importar, con bases exportables, y queda PREPARADO para intercambiar datos
con el sistema del menú de la Procuración (sin conectar todavía, ver `configurar`).

  * Nunca se borra a una persona: se da de baja (con motivo, por ejemplo «egreso») y se puede reactivar.
  * Cada cambio queda registrado campo por campo (quién, cuándo, valor anterior y nuevo, y si vino de una importación).
  * Edad y «es adulto» se calculan con la fecha de nacimiento; sin fecha, «es adulto» se carga a mano.
  * DNI y LPU no se repiten: al importar se usan para actualizar a la persona existente.
"""
import csv
import io
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timezone

import database as db

CAMPOS = [  # (clave, etiqueta, obligatorio)
    ("nombre_completo", "Nombre completo", True),
    ("dni", "DNI", False),
    ("lpu", "LPU", False),
    ("doc_extranjero", "Documento extranjero / otro", False),
    ("alias", "Alias", False),
    ("fecha_nacimiento", "Fecha de nacimiento", False),
    ("es_adulto_manual", "Es adulto", False),
    ("nro_expediente_ppn", "Número de expediente PPN", False),
    ("codigo", "Código", False),
    ("unidad_id", "Centro de detención", False),
    ("centro_otro", "Centro de detención (otro)", False),
    ("modulo", "Módulo de detención", False),
    ("pabellon", "Pabellón", False),
    ("celda", "Celda", False),
    ("juzgado", "Juzgado", False),
    ("tribunal", "Tribunal", False),
    ("defensor", "Defensor/a", False),
    ("situacion_procesal", "Situación procesal", False),
    ("condena_anios", "Años de condena", False),
    ("condena_meses", "Meses de condena", False),
    ("condena_dias", "Días de condena", False),
    ("fecha_cumplimiento", "Fecha de cumplimiento de pena", False),
    ("nacionalidad", "Nacionalidad", False),
    ("observaciones", "Observaciones", False),
]
ETIQUETA = {c: e for c, e, _ in CAMPOS}
SITUACIONES = ("Procesado/a", "Condenado/a", "Con prisión preventiva", "Arresto domiciliario", "Otra")
# Encabezados que se reconocen al importar (sin acentos, minúsculas, por inclusión)
ENCABEZADOS = {
    "nombre_completo": ("nombre completo", "apellido y nombre", "nombre y apellido", "apellido, nombre", "interno", "detenido", "ppl", "persona", "nombre"),
    "dni": ("dni", "documento", "d.n.i", "nro doc", "n doc"),
    "lpu": ("lpu", "l.p.u", "legajo"),
    "doc_extranjero": ("documento extranjero", "doc. extranjero", "pasaporte", "cedula"),
    "alias": ("alias", "apodo"),
    "fecha_nacimiento": ("fecha de nacimiento", "fecha nac", "nacimiento", "f. nac", "fnac"),
    "es_adulto_manual": ("es adulto", "adulto", "mayor de edad"),
    "nro_expediente_ppn": ("expediente ppn", "numero de expediente", "nro expediente", "expediente", "expte"),
    "codigo": ("codigo", "cod."),
    "centro": ("centro de detencion", "centro", "unidad", "complejo", "establecimiento"),
    "modulo": ("modulo",),
    "pabellon": ("pabellon",),
    "celda": ("celda",),
    "juzgado": ("juzgado",),
    "tribunal": ("tribunal", "tof"),
    "defensor": ("defensor", "defensoria"),
    "situacion_procesal": ("situacion procesal", "situacion legal", "situacion"),
    "fecha_cumplimiento": ("fecha de cumplimiento", "cumplimiento de pena", "fecha cumplimiento", "vencimiento de pena", "agotamiento", "vto pena",
                           "vencimiento"),
    "condena_anios": ("anos de condena", "condena anos", "condena (anos)", "anos"),
    "condena_meses": ("meses de condena", "condena meses", "condena (meses)", "meses"),
    "condena_dias": ("dias de condena", "condena dias", "condena (dias)", "dias"),
    "condena": ("monto de condena", "monto de pena", "pena impuesta", "condena", "pena"),
    "nacionalidad": ("nacionalidad",),
    "observaciones": ("observaciones", "observacion", "notas"),
}
EXTERNO = {"activo": False, "url": "", "token": "", "api_token": ""}
# Fuerzas que remiten listados (la lista se amplía desde la pantalla de importación)
FUERZAS_INICIALES = (("Servicio Penitenciario Federal", "SPF"), ("Servicio Penitenciario Bonaerense", "SPB"),
                     ("Policía de la Ciudad", "PC"), ("Policía de la Provincia de Buenos Aires", "PBA"),
                     ("Policía Federal Argentina", "PFA"), ("Gendarmería Nacional Argentina", "GNA"),
                     ("Prefectura Naval Argentina", "PNA"), ("Policía de Seguridad Aeroportuaria", "PSA"))
# Datos del lugar de alojamiento: solo los cambia un listado MÁS NUEVO que el último que los informó
ALOJAMIENTO = ("unidad_id", "centro_otro", "modulo", "pabellon", "celda")
DETALLE_ALOJAMIENTO = ("modulo", "pabellon", "celda")  # dependen de la unidad: si la unidad cambia y no vienen, no se arrastran
EGRESO_SPF = "Egreso: no figura en el listado del SPF"  # baja automática (solo listados del Servicio Penitenciario Federal)
DIAS_VIGENCIA = 30


def configurar(config):
    """[detenidos] en config.ini. api_token habilita /api/detenidos (lectura) para que otro sistema del organismo
    consulte esta base; url/token quedan listos para pedir datos al sistema del menú de la Procuración."""
    try:
        EXTERNO["url"] = config.get("detenidos", "url_sistema_procuracion", fallback="").strip()
        EXTERNO["token"] = config.get("detenidos", "token_sistema_procuracion", fallback="").strip()
        EXTERNO["api_token"] = config.get("detenidos", "api_token", fallback="").strip()
        # propuesta 37: además de la casilla de la pantalla, el servidor rechaza la confirmación sin ella
        EXTERNO["exigir_confirmar_alertas"] = config.getboolean("detenidos", "exigir_confirmar_alertas", fallback=False)
        # propuesta 46: motivo obligatorio para la exportación completa, aviso por volumen y campos de la API
        EXTERNO["exigir_motivo_exportacion"] = config.getboolean("detenidos", "exigir_motivo_exportacion", fallback=False)
        EXTERNO["aviso_exportacion_fichas"] = config.getint("detenidos", "aviso_exportacion_fichas", fallback=2000)
        EXTERNO["ocultar_sensibles"] = config.getboolean("detenidos", "ocultar_sensibles", fallback=False)  # propuesta 45
        EXTERNO["api_campos"] = [c.strip() for c in config.get("detenidos", "api_campos", fallback="").split(",") if c.strip()]
        EXTERNO["activo"] = bool(EXTERNO["url"] and EXTERNO["token"])
    except Exception:
        pass


# ---------------------------------------------------------------- normalización
def _sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn")


def norm_nombre(s):
    return " ".join(_sin_acentos(s).lower().replace(",", " ").split())


def norm_num(s):
    return re.sub(r"[^0-9a-z]", "", _sin_acentos(s).lower())


def solo_digitos(v):
    """DNI y LPU: se queda solo con los números («DNI: 30.123.456» → «30123456»; «LPU 123-456/7» → «1234567»).
    Un número leído del Excel como decimal («30123456.0») no suma el cero del final."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = re.sub(r"(?<=\d)\.0+$", "", str(v).strip())
    return re.sub(r"\D", "", t)


_BASURA_NOMBRE = re.compile(r"^(?:interno|interna|ppl|detenido|detenida|apellido y nombres?|nombre y apellido)\s*[:\-]\s*", re.I)


def limpiar_nombre(v):
    """Espacios, signos sueltos y rótulos («Interno: …»); «Gómez ,Juan» → «Gómez, Juan»."""
    t = _BASURA_NOMBRE.sub("", re.sub(r"\s+", " ", str(v or "")).strip())
    t = re.sub(r"\s*,\s*", ", ", t).strip(" ,;.-")
    return t


def separar_nombre(nombre):
    """(apellido, nombres) a partir del nombre completo.
    «GÓMEZ, Juan Carlos» → coma: antes es el apellido. «GÓMEZ PÉREZ Juan» → apellido en mayúsculas y nombres no.
    Sin pistas («Juan Gómez») → (None, None): no se adivina."""
    t = limpiar_nombre(nombre)
    if not t:
        return None, None
    if "," in t:
        ape, nom = [x.strip() for x in t.split(",", 1)]
        return (ape or None), (nom or None)
    palabras = t.split()
    es_mayus = [p.isupper() and len(re.sub(r"[^A-Za-zÁÉÍÓÚÑÜáéíóúñü]", "", p)) > 1 for p in palabras]
    if any(es_mayus) and not all(es_mayus):
        k = 0
        while k < len(palabras) and es_mayus[k]:
            k += 1
        if k and all(not m for m in es_mayus[k:]):
            return " ".join(palabras[:k]), " ".join(palabras[k:])
        k = len(palabras)
        while k and es_mayus[k - 1]:
            k -= 1
        if k < len(palabras) and all(not m for m in es_mayus[:k]):  # «Juan Carlos GÓMEZ»
            return " ".join(palabras[k:]), " ".join(palabras[:k])
    return None, None


def leer_condena(texto):
    """«5 años 3 meses», «5a 3m 10d», «4 años y 6 meses», «perpetua» → (años, meses, días) o (None, None, None)."""
    t = norm_nombre(texto)
    if not t:
        return None, None, None
    if "perpetu" in t:
        return 99, 0, 0
    m = {k: re.search(rf"(\d+)\s*(?:{p})", t) for k, p in (("a", r"anos?\b|a\b"), ("m", r"mes(?:es)?\b|m\b"), ("d", r"dias?\b|d\b"))}
    if not any(m.values()):
        return None, None, None
    return tuple(int(m[k].group(1)) if m[k] else 0 for k in "amd")


def es_condenado(d):
    sit = norm_nombre(d.get("situacion_procesal"))
    if "conden" in sit:
        return True
    if "proces" in sit or "preventiv" in sit:
        return False
    return any(d.get(k) for k in ("condena_anios", "condena_meses", "condena_dias", "fecha_cumplimiento"))


def es_procesado(d):
    sit = norm_nombre(d.get("situacion_procesal"))
    return ("proces" in sit or "preventiv" in sit) and not es_condenado(d)


def es_spf(fuerza_id):
    if not fuerza_id:
        return False
    with db._conexion() as con:
        f = con.execute("SELECT nombre, sigla FROM fuerzas WHERE id = ?", (fuerza_id,)).fetchone()
    return bool(f) and (norm_nombre(f["sigla"]) == "spf" or norm_nombre(f["nombre"]) == "servicio penitenciario federal")


def fecha(v):
    """Acepta date/datetime, AAAA-MM-DD y DD/MM/AAAA. Devuelve ISO o ''."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    t = str(v or "").strip()
    if not t:
        return ""
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", t)
    try:
        if m:
            d_, m_, a_ = (int(x) for x in m.groups())
            return date(a_ + (1900 if a_ > 30 else 2000) if a_ < 100 else a_, m_, d_).isoformat()
        return date.fromisoformat(t[:10]).isoformat()
    except ValueError:
        return None  # inválida


def edad(fecha_nac, hoy=None):
    if not fecha_nac:
        return None
    hoy = hoy or date.today()
    n = date.fromisoformat(fecha_nac)
    return hoy.year - n.year - ((hoy.month, hoy.day) < (n.month, n.day))


def es_adulto(d, hoy=None):
    e = edad(d.get("fecha_nacimiento"), hoy)
    if e is not None:
        return e >= 18
    return None if d.get("es_adulto_manual") is None else bool(d["es_adulto_manual"])


def listar_fuerzas(solo_activas=True):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM fuerzas %s ORDER BY nombre COLLATE NOCASE" % ("WHERE activa = 1" if solo_activas else ""))]


def crear_fuerza(nombre, sigla=None, usuario=None):
    nombre = re.sub(r"\s+", " ", str(nombre or "")).strip()[:120]
    if len(nombre) < 3:
        raise ValueError("Escribí el nombre de la fuerza (al menos 3 letras).")
    with db._conexion() as con:
        clave = norm_nombre(nombre)  # sin tildes ni mayúsculas: «Policía» = «policia»; también coincide con la sigla
        for f in con.execute("SELECT id, nombre, sigla FROM fuerzas"):
            if clave in (norm_nombre(f["nombre"]), norm_nombre(f["sigla"] or "")):
                return f["id"]
        return con.execute("INSERT INTO fuerzas (nombre, sigla, creado_por, creado_en) VALUES (?,?,?,?)",
                           (nombre, (str(sigla or "").strip()[:20] or None), (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario, _ahora())).lastrowid


def vigencia(d, hoy=None):
    """al_dia | desactualizado | sin_fecha, según la fecha del último listado que informó el alojamiento."""
    f = d.get("fecha_listado")
    if not f:
        return "sin_fecha"
    hoy = hoy or date.today()
    return "al_dia" if (hoy - date.fromisoformat(f[:10])).days <= DIAS_VIGENCIA else "desactualizado"


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- base
def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS detenidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_completo TEXT NOT NULL, nombre_norm TEXT NOT NULL,
        dni TEXT, lpu TEXT, lpu_norm TEXT, alias TEXT, fecha_nacimiento TEXT, es_adulto_manual INTEGER,
        nro_expediente_ppn TEXT, codigo TEXT, unidad_id INTEGER REFERENCES unidades(id), centro_otro TEXT,
        modulo TEXT, pabellon TEXT, celda TEXT, juzgado TEXT, tribunal TEXT, defensor TEXT, situacion_procesal TEXT,
        nacionalidad TEXT, observaciones TEXT,
        activo INTEGER NOT NULL DEFAULT 1, motivo_baja TEXT, origen TEXT NOT NULL DEFAULT 'MANUAL', externo_id TEXT,
        creado_por TEXT, creado_en TEXT NOT NULL, actualizado_por TEXT, actualizado_en TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS detenidos_cambios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, detenido_id INTEGER NOT NULL, campo TEXT NOT NULL, antes TEXT, despues TEXT,
        usuario TEXT, origen TEXT, fecha TEXT NOT NULL)""")
    for columna in ("fuerza_id INTEGER", "fecha_listado TEXT", "listado_id INTEGER", "condena_anios INTEGER", "condena_meses INTEGER",
                    "condena_dias INTEGER", "fecha_cumplimiento TEXT", "apellido TEXT", "nombres TEXT", "doc_extranjero TEXT"):
        try:
            con.execute(f"ALTER TABLE detenidos ADD COLUMN {columna}")
        except Exception:
            pass  # ya existe
    con.execute("""CREATE TABLE IF NOT EXISTS fuerzas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE COLLATE NOCASE, sigla TEXT,
        activa INTEGER NOT NULL DEFAULT 1, creado_por TEXT, creado_en TEXT NOT NULL)""")
    if not con.execute("SELECT 1 FROM fuerzas LIMIT 1").fetchone():
        for nombre, sigla in FUERZAS_INICIALES:
            con.execute("INSERT INTO fuerzas (nombre, sigla, creado_por, creado_en) VALUES (?,?,?,?)", (nombre, sigla, "Sistema", _ahora()))
    con.execute("""CREATE TABLE IF NOT EXISTS detenidos_listados (
        id INTEGER PRIMARY KEY AUTOINCREMENT, archivo TEXT, fuerza_id INTEGER REFERENCES fuerzas(id), fecha_listado TEXT,
        filas INTEGER, altas INTEGER, actualizadas INTEGER, alojamiento_actualizado INTEGER, alojamiento_conservado INTEGER,
        usuario TEXT, creado_en TEXT NOT NULL)""")
    # anulación de una carga completa (nada se borra: se revierte y queda registrado)
    for tabla, columna in (("detenidos_cambios", "listado_id INTEGER"), ("detenidos_listados", "anulado_en TEXT"),
                           ("detenidos_listados", "anulado_por TEXT"), ("detenidos_listados", "motivo_anulacion TEXT"),
                           ("detenidos_listados", "origen TEXT"), ("detenidos_listados", "egresos INTEGER"),
                           ("detenidos_listados", "informe TEXT")):
        try:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna}")
        except Exception:
            pass  # ya existe
    for i in ("CREATE INDEX IF NOT EXISTS ix_det_dni ON detenidos(dni)", "CREATE INDEX IF NOT EXISTS ix_det_lpu ON detenidos(lpu_norm)",
              "CREATE INDEX IF NOT EXISTS ix_det_nombre ON detenidos(nombre_norm)", "CREATE INDEX IF NOT EXISTS ix_det_cambios ON detenidos_cambios(detenido_id)"):
        con.execute(i)
    inicializar_revision(con)
    completar_apellidos(con)
    # un DNI argentino tiene hasta 8 dígitos: lo que se cargó antes con 9 o más es un documento extranjero
    for f in con.execute("SELECT id, dni FROM detenidos WHERE length(dni) > 8").fetchall():
        con.execute("UPDATE detenidos SET doc_extranjero = COALESCE(doc_extranjero, ?), dni = NULL WHERE id = ?", (f["dni"], f["id"]))
        con.execute("INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha) VALUES (?,?,?,?,?,?,?)",
                    (f["id"], "dni", f["dni"], "pasa a «Documento extranjero» (más de 8 dígitos)", "Sistema", "MIGRACION", _ahora()))
    # LPU y DNI se comparan solo por sus números (cargas anteriores podían tener letras o rótulos)
    for f in con.execute("SELECT id, dni, lpu, lpu_norm FROM detenidos WHERE lpu_norm GLOB '*[^0-9]*' OR dni GLOB '*[^0-9]*'").fetchall():
        con.execute("UPDATE detenidos SET lpu_norm = ?, dni = ? WHERE id = ?", (solo_digitos(f["lpu"]) or None, solo_digitos(f["dni"]) or None, f["id"]))


def _cargas_por_id():
    with db._conexion() as con:
        return {f["id"]: dict(f) for f in con.execute("SELECT id, creado_en, origen, archivo FROM detenidos_listados")}


def _decorar(d, unidades=None, fuerzas=None, cargas=None):
    d = dict(d)
    if d.get("listado_id"):
        c = (cargas if cargas is not None else _cargas_por_id()).get(d["listado_id"])
        d["carga_fecha"] = c["creado_en"][:10] if c else None
    d["edad"] = edad(d.get("fecha_nacimiento"))
    motivo = str(d.get("motivo_baja") or "")
    d["estado_actual"] = "Activo / alojado" if d.get("activo", 1) else ("Egresado" if "egreso" in norm_nombre(motivo) else "De baja")
    d["condenado"] = es_condenado(d)
    d["es_adulto"] = es_adulto(d)
    d["vigencia"] = vigencia(d)
    if d.get("fuerza_id"):
        if fuerzas is None:
            with db._conexion() as con:
                f = con.execute("SELECT nombre FROM fuerzas WHERE id = ?", (d["fuerza_id"],)).fetchone()
            d["fuerza"] = f["nombre"] if f else ""
        else:
            d["fuerza"] = fuerzas.get(d["fuerza_id"], "")
    else:
        d["fuerza"] = ""
    if unidades is None and d.get("unidad_id"):
        u = db.obtener_unidad(d["unidad_id"])
        d["centro"] = u["nombre"] if u else (d.get("centro_otro") or "")
    else:
        d["centro"] = (unidades or {}).get(d.get("unidad_id")) or d.get("centro_otro") or ""
    return d


def obtener(det_id):
    with db._conexion() as con:
        f = con.execute("SELECT * FROM detenidos WHERE id = ?", (det_id,)).fetchone()
    return _decorar(f) if f else None


def buscar(q="", unidad_id=None, adultos=None, incluir_bajas=False, limite=200):
    """q busca en nombre, alias, DNI, LPU, expediente y código (todas las palabras)."""
    sql, params = "SELECT * FROM detenidos WHERE 1=1", []
    if not incluir_bajas:
        sql += " AND activo = 1"
    if unidad_id:
        sql += " AND unidad_id = ?"
        params.append(unidad_id)
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(sql + " ORDER BY nombre_norm", params)]
    unidades = {u["id"]: u["nombre"] for u in db.listar_unidades(solo_activas=False)}
    fuerzas = {f["id"]: f["nombre"] for f in listar_fuerzas(False)}; cargas = _cargas_por_id()
    palabras = norm_nombre(q).split()
    salida = []
    for f in filas:
        if palabras:
            texto = " ".join([f["nombre_norm"], norm_nombre(f.get("alias")), norm_num(f.get("dni")), f.get("lpu_norm") or "",
                              norm_num(f.get("nro_expediente_ppn")), norm_num(f.get("codigo"))])
            if not all((norm_num(p) and norm_num(p) in texto) or p in texto for p in palabras):
                continue
        d = _decorar(f, unidades, fuerzas, cargas)
        if adultos is not None and d["es_adulto"] is not adultos:
            continue
        salida.append(d)
        if len(salida) >= limite:
            break
    return salida


RANGOS_EDAD = ((18, 25, "18 a 25"), (26, 35, "26 a 35"), (36, 45, "36 a 45"), (46, 60, "46 a 60"), (61, 200, "61 o más"))


def opciones_busqueda():
    """Valores para los filtros: nacionalidades y juzgados cargados, fuerzas, tipos de unidad y unidades con personas."""
    with db._conexion() as con:
        nac = [r[0] for r in con.execute("SELECT nacionalidad FROM detenidos WHERE activo = 1 AND nacionalidad IS NOT NULL AND nacionalidad != '' "
                                         "GROUP BY nacionalidad COLLATE NOCASE ORDER BY COUNT(*) DESC")]
        juz = [r[0] for r in con.execute("SELECT juzgado FROM detenidos WHERE activo = 1 AND juzgado IS NOT NULL AND juzgado != '' "
                                         "GROUP BY juzgado COLLATE NOCASE ORDER BY COUNT(*) DESC LIMIT 300")]
        con_personas = {r[0] for r in con.execute("SELECT DISTINCT unidad_id FROM detenidos WHERE unidad_id IS NOT NULL")}
    unidades = [u for u in db.listar_unidades(solo_activas=False) if u["id"] in con_personas]
    tipos = sorted({(u.get("tipo") or "Otro") for u in unidades})
    return {"nacionalidades": nac, "juzgados": juz, "fuerzas": listar_fuerzas(False), "tipos": tipos, "unidades": unidades}


def busqueda_colectiva(q="", nacionalidades=(), fuerza_id=None, tipo_unidad="", unidad_id=None, juzgado="", edad_min=None, edad_max=None,
                       vigencia_f="", incluir_bajas=False):
    """Filtros combinables sobre toda la base. Devuelve la lista (decorada) y el resumen para los gráficos."""
    lista = buscar(q=q, unidad_id=unidad_id, incluir_bajas=incluir_bajas, limite=1_000_000)
    nac = {norm_nombre(n) for n in nacionalidades if n}
    tipos_u = {u["id"]: (u.get("tipo") or "Otro") for u in db.listar_unidades(solo_activas=False)}
    juz = norm_nombre(juzgado)
    out = []
    for d in lista:
        if nac and norm_nombre(d.get("nacionalidad")) not in nac:
            continue
        if fuerza_id and d.get("fuerza_id") != fuerza_id:
            continue
        if tipo_unidad and tipos_u.get(d.get("unidad_id")) != tipo_unidad:
            continue
        if juz and juz not in norm_nombre(" ".join(filter(None, (d.get("juzgado"), d.get("tribunal"))))):
            continue
        if edad_min is not None and (d["edad"] is None or d["edad"] < edad_min):
            continue
        if edad_max is not None and (d["edad"] is None or d["edad"] > edad_max):
            continue
        if vigencia_f and d["vigencia"] != vigencia_f:
            continue
        out.append(d)
    return out, resumen_grupo(out)


def resumen_grupo(lista):
    from collections import Counter
    n = len(lista) or 1
    edades = Counter()
    for d in lista:
        e = d.get("edad")
        etiqueta = "Sin fecha de nacimiento" if e is None else ("Menor de 18" if e < 18 else next(t for a, b, t in RANGOS_EDAD if a <= e <= b))
        edades[etiqueta] += 1
    orden_edad = ["Menor de 18"] + [t for _, _, t in RANGOS_EDAD] + ["Sin fecha de nacimiento"]
    nac = Counter((d.get("nacionalidad") or "Sin dato").strip().capitalize() for d in lista)
    juz = Counter((d.get("juzgado") or "Sin dato").strip() for d in lista)
    fue = Counter(d.get("fuerza") or "Sin dato" for d in lista)
    cen = Counter(d.get("centro") or "Sin dato" for d in lista)
    vig = Counter(d["vigencia"] for d in lista)
    pct = lambda c: [(k, v, round(100 * v / n)) for k, v in c]
    return {"total": len(lista), "edades": [(t, edades[t], round(100 * edades[t] / n)) for t in orden_edad if edades[t]],
            "nacionalidades": pct(nac.most_common(8)), "juzgados": pct(juz.most_common(8)), "fuerzas": pct(fue.most_common()),
            "centros": pct(cen.most_common(8)), "vigencia": dict(vig)}


def contar():
    with db._conexion() as con:
        f = con.execute("SELECT SUM(activo) AS activos, COUNT(*) AS total FROM detenidos").fetchone()
    return {"activos": f["activos"] or 0, "total": f["total"] or 0}


def _indices():
    """Una sola lectura de la base para las importaciones grandes: DNI, LPU y nombre → personas."""
    idx = {"dni": {}, "lpu": {}, "nombre": {}, "filas": {}, "doc": {}}
    with db._conexion() as con:
        # las fichas activas primero: un documento repetido en una ficha unificada (de baja) no le gana a la activa
        for f in con.execute("SELECT * FROM detenidos ORDER BY activo DESC, id"):
            f = dict(f)
            idx["filas"][f["id"]] = f
            if f.get("dni"): idx["dni"].setdefault(f["dni"], f)
            if f.get("lpu_norm"): idx["lpu"].setdefault(f["lpu_norm"], f)
            if f.get("nombre_norm"): idx["nombre"].setdefault(f["nombre_norm"], f)
            if f.get("doc_extranjero"): idx["doc"].setdefault(solo_digitos(f["doc_extranjero"]) or norm_num(f["doc_extranjero"]), f)
    return idx


def validar(datos, det_id=None, indices=None):
    """Devuelve (datos_limpios, errores, avisos). Con `indices` (de _indices) no consulta la base."""
    d, errores, avisos = {}, [], []
    for clave, _, _ in CAMPOS:
        v = datos.get(clave)
        d[clave] = str(v).strip() if v is not None and not isinstance(v, (int, date)) else v
    d["nombre_completo"] = limpiar_nombre(d.get("nombre_completo"))
    if not d["nombre_completo"]:
        errores.append("Falta el nombre completo.")
    dni = solo_digitos(datos.get("dni"))
    from dgpdh.dominio import datos_personales as dp
    if dni and not re.fullmatch(r"\d{6,8}", dni):
        cuil = dp.analizar_cuil(dni)
        errores.append("El DNI tiene que tener entre 6 y 8 números (si es un documento extranjero, cargalo en «Documento extranjero»)."
                       + (f" Si es el CUIL, el DNI es {cuil.dni}." if cuil and cuil.valido else ""))
    d["dni"] = dni or None
    avisos.extend(dp.avisos_cuil(datos.get("doc_extranjero"), dni if re.fullmatch(r"\d{6,8}", dni or "") else None))  # propuesta 40
    d["lpu"] = solo_digitos(datos.get("lpu")) or None
    for clave, tope, nombre in (("condena_anios", 99, "años"), ("condena_meses", 11, "meses"), ("condena_dias", 31, "días")):
        v = solo_digitos(datos.get(clave))
        if v and int(v) > tope:
            errores.append(f"Los {nombre} de condena tienen que ser un número entre 0 y {tope}.")
        d[clave] = int(v) if v else None
    fc = fecha(d.get("fecha_cumplimiento"))
    if fc is None:
        errores.append("La fecha de cumplimiento de pena no es válida (usá DD/MM/AAAA).")
    d["fecha_cumplimiento"] = fc or None
    f = fecha(d.get("fecha_nacimiento"))
    if f is None:
        errores.append("La fecha de nacimiento no es válida (usá DD/MM/AAAA).")
    elif f and f > date.today().isoformat():
        errores.append("La fecha de nacimiento no puede ser futura.")
    elif f and edad(f) > 110:
        errores.append("La fecha de nacimiento da más de 110 años.")
    d["fecha_nacimiento"] = f or None
    m = d.get("es_adulto_manual")
    d["es_adulto_manual"] = None if m in (None, "", "sin_dato") else (1 if str(m).lower() in ("1", "si", "sí", "true", "s", "x") else 0)
    try:
        d["unidad_id"] = int(d["unidad_id"]) if d.get("unidad_id") not in (None, "") else None
    except (TypeError, ValueError):
        d["unidad_id"] = None
    for clave in ("doc_extranjero", "alias", "nro_expediente_ppn", "codigo", "centro_otro", "modulo", "pabellon", "celda", "juzgado", "tribunal",
                  "defensor", "situacion_procesal", "nacionalidad", "observaciones"):
        d[clave] = (d.get(clave) or "").strip()[:500] or None
    # duplicados
    if indices is not None:
        otro = indices["dni"].get(d["dni"]) if d["dni"] else None
        if otro and otro["id"] != det_id:
            errores.append(f"Ya existe una persona con ese DNI: {otro['nombre_completo']} (#{otro['id']}).")
        otro = indices["lpu"].get(norm_num(d["lpu"])) if d["lpu"] else None
        if otro and otro["id"] != det_id:
            errores.append(f"Ya existe una persona con ese LPU: {otro['nombre_completo']} (#{otro['id']}).")
        if d["nombre_completo"] and not d["dni"] and not d["lpu"]:
            otro = indices["nombre"].get(norm_nombre(d["nombre_completo"]))
            if otro and otro["id"] != det_id:
                avisos.append("Hay otra persona con el mismo nombre: revisá que no sea la misma (cargá DNI o LPU para distinguirlas).")
        if not d["dni"] and not d["lpu"]:
            avisos.append("Sin DNI ni LPU es difícil identificar a la persona con certeza.")
        return d, errores, avisos
    with db._conexion() as con:
        if d["dni"]:
            otro = con.execute("SELECT id, nombre_completo FROM detenidos WHERE dni = ? AND id != ? AND COALESCE(motivo_baja, '') NOT LIKE 'Unificada con%'", (d["dni"], det_id or -1)).fetchone()
            if otro:
                errores.append(f"Ya existe una persona con ese DNI: {otro['nombre_completo']} (#{otro['id']}).")
        if d["lpu"]:
            otro = con.execute("SELECT id, nombre_completo FROM detenidos WHERE lpu_norm = ? AND id != ? AND COALESCE(motivo_baja, '') NOT LIKE 'Unificada con%'", (norm_num(d["lpu"]), det_id or -1)).fetchone()
            if otro:
                errores.append(f"Ya existe una persona con ese LPU: {otro['nombre_completo']} (#{otro['id']}).")
        if d["nombre_completo"] and not d["dni"] and not d["lpu"]:
            parecido = con.execute("SELECT id FROM detenidos WHERE nombre_norm = ? AND id != ?", (norm_nombre(d["nombre_completo"]), det_id or -1)).fetchone()
            if parecido:
                avisos.append("Hay otra persona con el mismo nombre: revisá que no sea la misma (cargá DNI o LPU para distinguirlas).")
    if not d["dni"] and not d["lpu"]:
        avisos.append("Sin DNI ni LPU es difícil identificar a la persona con certeza.")
    return d, errores, avisos


def _escribir(con, d, quien, det_id, origen, listado_id=None):
    columnas = [c for c, _, _ in CAMPOS]
    valores = [d[c] for c in columnas]
    if det_id:
        antes = dict(con.execute("SELECT * FROM detenidos WHERE id = ?", (det_id,)).fetchone())
        con.execute(f"UPDATE detenidos SET {', '.join(c + ' = ?' for c in columnas)}, nombre_norm = ?, lpu_norm = ?, "
                    "apellido = ?, nombres = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                    valores + [norm_nombre(d["nombre_completo"]), norm_num(d["lpu"]) or None, *separar_nombre(d["nombre_completo"]), quien, _ahora(), det_id])
        cambios = [(c, antes.get(c), d[c]) for c in columnas if str(antes.get(c) or "") != str(d[c] or "")]
    else:
        cur = con.execute(f"INSERT INTO detenidos ({', '.join(columnas)}, nombre_norm, lpu_norm, apellido, nombres, origen, creado_por, creado_en, "
                          f"actualizado_por, actualizado_en) VALUES ({', '.join('?' * (len(columnas) + 9))})",
                          valores + [norm_nombre(d["nombre_completo"]), norm_num(d["lpu"]) or None, *separar_nombre(d["nombre_completo"]),
                                     origen, quien, _ahora(), quien, _ahora()])
        det_id = cur.lastrowid
        cambios = [("alta", None, d["nombre_completo"])]
    con.executemany("INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha, listado_id) VALUES (?,?,?,?,?,?,?,?)",
                    [(det_id, c, None if a is None else str(a), None if b is None else str(b), quien, origen, _ahora(), listado_id)
                     for c, a, b in cambios])
    return det_id


def _quien(usuario):
    return (usuario or {}).get("nombre") if isinstance(usuario, dict) else (usuario or "Sistema")


def guardar(datos, usuario=None, det_id=None, origen="MANUAL"):
    """Alta (det_id None) o modificación. Devuelve (id, errores, avisos)."""
    d, errores, avisos = validar(datos, det_id)
    if errores:
        return None, errores, avisos
    with db._conexion() as con:
        det_id = _escribir(con, d, _quien(usuario), det_id, origen)
    return det_id, [], avisos


def dar_de_baja(det_id, motivo, usuario):
    _cambiar_estado(det_id, 0, (motivo or "").strip()[:300] or "Sin motivo", usuario)


def reactivar(det_id, usuario):
    _cambiar_estado(det_id, 1, None, usuario)


def _cambiar_estado(det_id, activo, motivo, usuario):
    quien = (usuario or {}).get("nombre", "Sistema")
    with db._conexion() as con:
        con.execute("UPDATE detenidos SET activo = ?, motivo_baja = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                    (activo, motivo, quien, _ahora(), det_id))
        con.execute("INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha) VALUES (?,?,?,?,?,?,?)",
                    (det_id, "baja" if not activo else "reactivación", None, motivo, quien, "MANUAL", _ahora()))


def historial(det_id):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM detenidos_cambios WHERE detenido_id = ? ORDER BY id DESC", (det_id,))]


def notas_relacionadas(d, limite=30):
    """Notas que nombran a la persona: por DNI leído del texto o por el nombre (todas sus palabras). Propuesta 39: más las
    que una persona vinculó a la ficha, y sin las que alguien descartó (marcadas con «confirmada»)."""
    from dgpdh.servicios import nota_persona
    with db._conexion() as con:
        confirmadas = nota_persona.notas_confirmadas(con, d["id"]) if d.get("id") else set()
        descartadas = nota_persona.descartadas(con, d["id"]) if d.get("id") else set()
    salida = _notas_relacionadas(d, limite, confirmadas, descartadas)
    for n in salida:
        n["confirmada"] = n["id"] in confirmadas
    return salida


def _notas_relacionadas(d, limite, confirmadas, descartadas):
    ids = set(confirmadas)
    with db._conexion() as con:
        if d.get("dni"):
            ids |= {f[0] for f in con.execute("SELECT DISTINCT nota_id FROM nota_entidades WHERE tipo = 'PERSONA' AND norm = ?", ("dni:" + d["dni"],))}
        palabras = [p for p in d["nombre_norm"].split() if len(p) > 2]
        if len(palabras) >= 2:
            for f in con.execute("SELECT DISTINCT nota_id, norm FROM nota_entidades WHERE tipo = 'PERSONA'"):
                if all(p in (f["norm"] or "") for p in palabras):
                    ids.add(f["nota_id"])
        ids -= set(descartadas)
        if not ids:
            return []
        filas = con.execute("SELECT id, nombre_original, estado, nro_nota, creado_en FROM notas WHERE id IN (%s) AND estado != 'BORRADOR' "
                            "ORDER BY creado_en DESC LIMIT ?" % ",".join("?" * len(ids)), list(ids) + [limite]).fetchall()
    return [dict(f) for f in filas]


# ---------------------------------------------------------------- importación
def _mapear_encabezados(fila):
    enc = [norm_nombre(c) for c in fila]
    mapa = {}
    for clave, claves in ENCABEZADOS.items():
        for k in claves:
            j = next((j for j, h in enumerate(enc) if h and (h == k or h.startswith(k) or k in h) and j not in mapa.values()), None)
            if j is not None:
                mapa[clave] = j
                break
    return mapa


def leer_archivo(nombre, contenido):
    """Filas de un .xlsx/.xls/.csv → (registros [dict con las claves de CAMPOS], avisos)."""
    import presentismo
    filas = [r for r in presentismo._filas_de_archivo(nombre, contenido) if r and any(str(c or "").strip() for c in r)]
    for i, fila in enumerate(filas[:15]):
        mapa = _mapear_encabezados(fila)
        if "nombre_completo" in mapa and ("dni" in mapa or "lpu" in mapa):
            break
    else:
        return [], ["No encontré los encabezados: la planilla necesita al menos una columna de nombre y otra de DNI o LPU."]
    unidades = db.listar_unidades(solo_activas=False)
    cache_centro = {}
    registros = []
    # apellido y nombres en columnas separadas: se juntan como «APELLIDO, Nombres»
    enc = [norm_nombre(c) for c in filas[i]]
    col_ape = next((j for j, h in enumerate(enc) if h in ("apellido", "apellidos", "apellido/s", "apellido s")), None)
    col_nom = next((j for j, h in enumerate(enc) if h in ("nombre", "nombres", "nombre/s", "nombre s")), None)
    separados = col_ape is not None and col_nom is not None
    for r in filas[i + 1:]:
        reg = {}
        for clave, j in mapa.items():
            v = r[j] if j < len(r) else None
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            reg[clave] = v if isinstance(v, (date, datetime)) else ("" if v is None else str(v).strip())
        if separados:
            ape = str(r[col_ape] if col_ape < len(r) and r[col_ape] is not None else "").strip()
            nom = str(r[col_nom] if col_nom < len(r) and r[col_nom] is not None else "").strip()
            reg["nombre_completo"] = f"{ape}, {nom}" if ape and nom else (ape or nom)
        if not str(reg.get("nombre_completo") or "").strip():
            continue
        texto_condena = reg.pop("condena", "")
        if texto_condena and not any(reg.get(k) not in (None, "") for k in ("condena_anios", "condena_meses", "condena_dias")):
            a_, m_, d_ = leer_condena(texto_condena)
            if a_ is not None:
                reg.update(condena_anios=a_, condena_meses=m_, condena_dias=d_)
        centro = str(reg.pop("centro", "") or "").strip()
        if centro:
            if centro not in cache_centro:  # suele repetirse en miles de filas: se busca una sola vez
                u = db.proponer_unidades(centro, 1) or [x for x in unidades if norm_nombre(x["nombre"]) == norm_nombre(centro)]
                cache_centro[centro] = u[0]["id"] if u else None
            reg["unidad_id"], reg["centro_otro"] = (cache_centro[centro], None) if cache_centro[centro] else (None, centro)
        registros.append(reg)
    nombres_col = [ETIQUETA.get(k, {"centro": "Centro de detención", "condena": "Condena (texto)"}.get(k, k)) for k in mapa]
    if separados:
        nombres_col = ["Apellido y Nombres (columnas separadas)"] + [x for x in nombres_col if x != ETIQUETA["nombre_completo"]]
    return registros, [f"Columnas reconocidas: {', '.join(nombres_col)}."]


def centros_sin_catalogo(registros, fuerza_id=None):
    """Lugares de alojamiento del listado que no coinciden con ninguna unidad del catálogo.
    Devuelve [(nombre tal como figura, cantidad de personas, unidad sugerida o None)]; la sugerencia es solo una ayuda.
    Con `fuerza_id` (la fuerza que envía el listado) la sugerencia sale primero de las unidades a cargo de esa fuerza."""
    import difflib
    from collections import Counter
    cuenta = Counter(str(r.get("centro_otro") or "").strip() for r in registros if not r.get("unidad_id") and str(r.get("centro_otro") or "").strip())
    if not cuenta:
        return []
    def armar(unidades):
        claves = []  # (nombre normalizado, unidad) por nombre y por cada alias
        for u in unidades:
            for nom in [u["nombre"]] + [x for x in re.split(r"[;|]", u.get("alias") or "") if x.strip()]:
                claves.append((norm_nombre(nom), u))
        return claves
    todas = db.listar_unidades()
    propias = armar([u for u in todas if fuerza_id and u.get("fuerza_id") == fuerza_id])
    claves = armar(todas)
    out = []
    for nombre, n in cuenta.most_common():
        sug = None
        for grupo, corte in ((propias, 0.7), (claves, 0.8)):  # primero las de la fuerza (con un corte algo más amplio)
            cerca = difflib.get_close_matches(norm_nombre(nombre), [k for k, _ in grupo], n=1, cutoff=corte)
            sug = next((u for k, u in grupo if cerca and k == cerca[0]), None)
            if sug:
                break
        out.append((nombre, n, sug))
    return out


def vincular_centros(registros, vinculos, usuario=None, recordar=True):
    """Aplica lo que decidió la persona: `vinculos` = {nombre como figura en el listado: unidad_id}.
    Los que no se vinculan quedan con el nombre tal como figura (centro_otro). Con `recordar`, el nombre se agrega como
    alias de la unidad para que el próximo listado la reconozca solo. Devuelve cuántas filas quedaron vinculadas."""
    validas = {u["id"]: u for u in db.listar_unidades(solo_activas=False)}
    vinculos = {k: int(v) for k, v in (vinculos or {}).items() if str(v).isdigit() and int(v) in validas}
    n = 0
    for r in registros:
        uid = vinculos.get(str(r.get("centro_otro") or "").strip()) if not r.get("unidad_id") else None
        if uid:
            r["unidad_id"], r["centro_otro"] = uid, None
            n += 1
    if recordar and vinculos:
        with db._conexion() as con:
            for nombre, uid in vinculos.items():
                actual = [x.strip() for x in re.split(r"[;|]", validas[uid].get("alias") or "") if x.strip()]
                if norm_nombre(nombre) not in {norm_nombre(x) for x in actual + [validas[uid]["nombre"]]}:
                    actual.append(nombre[:120])
                    con.execute("UPDATE unidades SET alias = ? WHERE id = ?", ("; ".join(actual), uid))
                    validas[uid]["alias"] = "; ".join(actual)
    return n


def existente(reg):
    with db._conexion() as con:
        dni = solo_digitos(reg.get("dni"))
        if dni:
            f = con.execute("SELECT id FROM detenidos WHERE dni = ? ORDER BY activo DESC, id", (dni,)).fetchone()
            if f:
                return f["id"]
        lpu = solo_digitos(reg.get("lpu"))
        if lpu:
            f = con.execute("SELECT id FROM detenidos WHERE lpu_norm = ? ORDER BY activo DESC, id", (lpu,)).fetchone()
            if f:
                return f["id"]
    return None


def _texto_alojamiento(d, unidades):
    centro = unidades.get(d.get("unidad_id")) if d.get("unidad_id") else (d.get("centro_otro") or "")
    partes = [centro or "sin unidad"] + [f"{e} {d[k]}" for k, e in (("modulo", "mód."), ("pabellon", "pab."), ("celda", "celda")) if d.get(k)]
    return " · ".join(partes)


TIPOS_REVISAR = {
    "conflicto": "DNI y LPU en fichas distintas",
    "repetido": "Repetido en la planilla",
    "fecha": "Fecha inválida",
    "nombre": "Sin nombre",
    "otro": "Otros problemas",
}
_TEXTO_EXTRANJERO = re.compile(r"extranj|\bc\s*i\b|cedula|pasaporte|\bpas\b|\bdni\s*ext", re.I)


def es_extranjero(reg):
    nac = norm_nombre(reg.get("nacionalidad"))
    return bool(nac) and not nac.startswith("argentin")


def clasificar_documento(texto, extranjero=False):
    """(dni, doc_extranjero, aviso) a partir de lo que trae la columna DNI.
    - «0», «no tiene», vacío → sin DNI.
    - Documento extranjero («D.N.I. EXTRANJERO …», «C.I. …», pasaporte) o persona extranjera → no se toma como DNI,
      salvo que sea el DNI argentino para extranjeros (8 dígitos que empiezan con 9: 90 millones o más).
    - Más de 8 dígitos no es un DNI argentino → documento extranjero. Menos de 6 → número inválido (se conserva igual)."""
    crudo = re.sub(r"\s+", " ", str(texto if texto is not None else "")).strip()
    if isinstance(texto, float) and texto.is_integer():
        crudo = str(int(texto))
    dig = solo_digitos(texto)
    if not dig or int(dig) == 0:
        return None, None, None
    dni_arg_extranjeros = len(dig) == 8 and dig.startswith("9")
    rotulo_ext = bool(_TEXTO_EXTRANJERO.search(norm_nombre(crudo).replace(".", " ")))
    if (rotulo_ext or extranjero) and not dni_arg_extranjeros:
        return None, crudo, "documento extranjero (no se carga como DNI)"
    if len(dig) > 8:
        from dgpdh.dominio import datos_personales as dp  # propuesta 40: solo avisa, no cambia cómo se guarda
        c = dp.analizar_cuil(dig)
        if c is not None:
            return None, crudo, (f"parece un CUIL que contiene el DNI {c.dni} (se guarda como documento extranjero: revisalo)" if c.valido
                                 else f"parece un CUIL, pero {c.motivo} (se guarda como documento extranjero)")
        return None, crudo, "más de 8 dígitos: no es un DNI argentino (se guarda como documento extranjero)"
    if len(dig) < 6:
        return None, crudo, "número de documento demasiado corto (se guarda aparte, sin DNI)"
    return dig, None, None


def _decision(decisiones, n):
    d = (decisiones or {}).get(str(n))
    if isinstance(d, dict):
        return d.get("accion") or "", d.get("campos") or {}
    return (d or ""), {}


def preparar_fila(reg, decisiones=None, n=None):
    """Aplica la corrección elegida para la fila y clasifica el documento. Devuelve (reg, avisos, accion)."""
    accion, campos = _decision(decisiones, n)
    r = dict(reg)
    r.update({k: v for k, v in campos.items() if k in ("dni", "lpu", "nombre_completo", "fecha_nacimiento", "fecha_cumplimiento", "unidad_id")})
    avisos = []
    if "dni" in r:
        dni, doc, aviso = clasificar_documento(r.get("dni"), es_extranjero(r))
        if doc and not r.get("doc_extranjero"):
            r["doc_extranjero"] = doc
        r["dni"] = dni or ""
        if aviso:
            avisos.append(aviso)
    if accion == "sin_dni" and r.get("dni"):
        r["doc_extranjero"] = r.get("doc_extranjero") or r["dni"]
        r["dni"] = ""
    if accion == "sin_fecha":
        for k, etq in (("fecha_nacimiento", "Fecha de nacimiento"), ("fecha_cumplimiento", "Fecha de cumplimiento")):
            if r.get(k) not in (None, "") and fecha(r.get(k)) is None:
                r["observaciones"] = "; ".join(x for x in (r.get("observaciones"), f"{etq} informada: «{r[k]}»") if x)
                r[k] = ""
    return r, avisos, accion


def _tipo_error(errs):
    t = " ".join(errs).lower()
    if "fecha" in t:
        return "fecha"
    if "nombre" in t:
        return "nombre"
    return "otro"


def _mismo_nombre(a, b):
    na, nb = norm_nombre(limpiar_nombre(a)), norm_nombre(limpiar_nombre(b))
    if not na or not nb:
        return False
    sa, sb = set(na.split()), set(nb.split())
    if sa == sb or (len(sa & sb) >= 2 and (sa <= sb or sb <= sa)):
        return True
    import difflib
    return difflib.SequenceMatcher(None, " ".join(sorted(sa)), " ".join(sorted(sb))).ratio() >= 0.88


def _alojamiento_fila(r):
    return (str(r.get("unidad_id") or ""), norm_nombre(r.get("centro_otro")),
            *(str(r.get(k) or "").strip().lower() for k in ("modulo", "pabellon", "celda")))


def _resumen_fila(n, r, unidades):
    lugar = unidades.get(r.get("unidad_id")) if r.get("unidad_id") else (r.get("centro_otro") or "")
    detalle = " · ".join(f"{e} {r[k]}" for k, e in (("modulo", "mód."), ("pabellon", "pab."), ("celda", "celda")) if r.get(k))
    return {"n": n, "nombre": r.get("nombre_completo"), "dni": r.get("dni") or "", "lpu": r.get("lpu") or "",
            "lugar": " · ".join(x for x in (lugar, detalle) if x) or "sin lugar", "nac": r.get("fecha_nacimiento") or ""}


def repetidas_en_planilla(registros, decisiones=None, idx=None):
    """Agrupa las filas de la planilla que son la misma persona o comparten DNI/LPU (por el documento o porque llevan a la
    misma ficha de la base). Para cada fila de un grupo: {grupo, misma, identicas, elegida}.
    - misma persona y mismo alojamiento («identicas»): se carga una sola vez, sin preguntar;
    - misma persona con distinto alojamiento: hay que elegir qué fila vale;
    - personas distintas con el mismo documento: hay que decir de quién es el documento."""
    idx = idx or _indices()
    pre = [preparar_fila(r, decisiones, n) for n, r in enumerate(registros, 1)]
    padre = {}

    def raiz(x):
        while padre.get(x, x) != x:
            x = padre[x]
        return x
    por_clave, fichas = {}, {}
    for n, (r, _, acc) in enumerate(pre, 1):
        if acc == "descartar":
            continue
        d, l = solo_digitos(r.get("dni")), solo_digitos(r.get("lpu"))
        ficha = (idx["lpu"].get(l) if l else None) or (idx["dni"].get(d) if d else None)
        if ficha:
            fichas[n] = ficha
        for c in (("dni", d), ("lpu", l), ("ficha", ficha["id"] if ficha else "")):
            if c[1]:
                por_clave.setdefault(c, []).append(n)
    for ns in por_clave.values():
        for x in ns[1:]:
            a, b = raiz(ns[0]), raiz(x)
            if a != b:
                padre[max(a, b)] = min(a, b)
    grupos = {}
    for ns in por_clave.values():
        for x in ns:
            grupos.setdefault(raiz(x), set()).add(x)
    info = {}
    for g in grupos.values():
        if len(g) < 2:
            continue
        g = sorted(g)
        regs = {x: pre[x - 1][0] for x in g}
        dnis = {solo_digitos(regs[x].get("dni")) for x in g} - {""}
        lpus = {solo_digitos(regs[x].get("lpu")) for x in g} - {""}
        misma = len(dnis) <= 1 and len(lpus) <= 1 and all(_mismo_nombre(regs[g[0]].get("nombre_completo"), regs[x].get("nombre_completo")) for x in g[1:])
        identicas = misma and len({_alojamiento_fila(regs[x]) for x in g}) == 1
        elegida = next((x for x in g if pre[x - 1][2] == "vale"), None)
        en_base = list({fichas[x]["id"]: fichas[x] for x in g if x in fichas}.values())
        comp = [("DNI", d) for d in dnis if sum(1 for x in g if solo_digitos(regs[x].get("dni")) == d) > 1] + \
               [("LPU", l) for l in lpus if sum(1 for x in g if solo_digitos(regs[x].get("lpu")) == l) > 1]
        for x in g:
            info[x] = {"grupo": g, "misma": misma, "identicas": identicas, "elegida": elegida, "regs": regs, "en_base": en_base,
                       "compartido": " y ".join(f"{t} {v}" for t, v in comp) or "la misma persona de la base"}
    return info


def previa(registros, fecha_listado=None, fuerza_id=None, decisiones=None):
    """Qué pasaría al importar. Lee la base una sola vez. Ninguna fila se pierde: lo que tiene problemas queda «para revisar»
    con su acción (corregir, cargar sin DNI, cargar sin fecha, misma persona, otra persona, descartar).
    - Alojamiento: solo lo cambia un listado MÁS NUEVO que el último que lo informó; módulo/pabellón/celda vacíos no borran
      lo que había si la unidad es la misma; si cambió de unidad, el detalle viejo no se arrastra.
    - Documento: ver clasificar_documento (extranjeros, «0», más de 8 dígitos).
    - Cruce: DNI, LPU o documento extranjero. Si el DNI y el LPU llevan a fichas distintas se actualiza la del LPU y se
      ofrece unificarlas. Si no hay coincidencia pero el nombre es igual al de alguien que no tiene ese documento, se
      muestran las dos opciones (misma persona / otra persona) en lugar de unirlas solas.
    - Egresos: solo listados del SPF contra listados anteriores del SPF."""
    idx = _indices()
    unidades = {u["id"]: u["nombre"] for u in db.listar_unidades(solo_activas=False)}
    nuevas, actualizar, conservado, mudanzas, reingresos, posibles = [], [], [], [], [], []
    revisar, descartadas, documentos, unificar = [], [], [], []
    vistos, docs_planilla = {}, {}
    repetidas = repetidas_en_planilla(registros, decisiones, idx)

    def en_base_txt(rep):
        """Qué hay hoy en el sistema con ese documento (para decidir con todo a la vista)."""
        return [dict(f, alojamiento=_texto_alojamiento(f, unidades)) for f in rep["en_base"]]
    for n, reg0 in enumerate(registros, 1):
        reg, avisos_doc, accion = preparar_fila(reg0, decisiones, n)
        if accion == "descartar":
            descartadas.append({"n": n, "reg": reg0})
            continue
        if avisos_doc:
            documentos.append({"n": n, "nombre": reg.get("nombre_completo"), "original": reg0.get("dni"), "aviso": avisos_doc[0],
                               "doc": reg.get("doc_extranjero")})
        dni_r, lpu_r = solo_digitos(reg.get("dni")), solo_digitos(reg.get("lpu"))
        doc_r = solo_digitos(reg.get("doc_extranjero")) or norm_num(reg.get("doc_extranjero"))
        # repetidas dentro de la misma planilla (ver repetidas_en_planilla)
        rep = repetidas.get(n)
        if rep:
            g, primera, elegida = rep["grupo"], rep["grupo"][0], rep["elegida"]
            vale = elegida or primera
            otras = [_resumen_fila(x, rep["regs"][x], unidades) for x in g if x != n]
            if rep["identicas"] and n != primera:
                descartadas.append({"n": n, "reg": reg0, "auto": True,
                                    "motivo": f"fila repetida igual a la fila {primera} (misma persona y mismo lugar): se carga una sola vez"})
                continue
            if not rep["identicas"] and n != vale:
                if accion == "misma" or (elegida and rep["misma"] and accion not in ("otra", "sin_dni")):
                    descartadas.append({"n": n, "reg": reg0, "motivo": f"la persona figura también en la fila {vale}, que es la que vale"})
                    continue
                if accion in ("otra", "sin_dni") or elegida:
                    # se carga sin el documento repetido (queda en observaciones y, el DNI, como documento aparte)
                    comp_dni = solo_digitos(rep["regs"][vale].get("dni"))
                    comp_lpu = solo_digitos(rep["regs"][vale].get("lpu"))
                    quita = [x for x, v in (("dni", comp_dni), ("lpu", comp_lpu)) if v and solo_digitos(reg.get(x)) == v]
                    if accion == "sin_dni" and "dni" not in quita and solo_digitos(reg0.get("dni")) == comp_dni:
                        quita.append("dni")  # preparar_fila ya lo sacó del DNI
                    extra = {"doc_extranjero": reg.get("doc_extranjero") or reg.get("dni")} if "dni" in quita and reg.get("dni") else {}
                    reg = dict(reg, **extra, **{x: "" for x in quita},
                               observaciones="; ".join(x for x in (reg.get("observaciones"),
                                                                    f"Documento repetido en el listado (fila {vale}): " + ", ".join(f"{x.upper()} {reg0.get(x)}" for x in quita)) if x))
                    dni_r, lpu_r = solo_digitos(reg.get("dni")), solo_digitos(reg.get("lpu"))
                elif rep["misma"]:
                    revisar.append({"n": n, "reg": reg0, "tipo": "repetido", "ref": primera, "otras": otras, "caso": "misma", "en_base": en_base_txt(rep),
                                    "mensaje": f"En el archivo que estás subiendo, esta persona aparece dos veces ({rep['compartido']}): en la fila {primera} y en esta, "
                                               f"con distinto lugar de alojamiento.",
                                    "que_hacer": f"Elegí cuál de las dos filas tiene el alojamiento correcto, o «Cargar sin DNI» si en realidad es otra persona. "
                                                 f"Mientras no elijas, se carga la fila {primera} y esta no."})
                    continue
                else:
                    revisar.append({"n": n, "reg": reg0, "tipo": "repetido", "ref": primera, "otras": otras, "caso": "distintas", "en_base": en_base_txt(rep),
                                    "mensaje": f"En el archivo que estás subiendo, esta fila y la fila {primera} tienen el mismo {rep['compartido']}, "
                                               f"pero los nombres son distintos. Un documento puede ser de una sola persona.",
                                    "que_hacer": f"Decí de quién es el documento: «El documento es de esta persona», o «Cargar sin DNI» para cargar a esta persona sin ese número. "
                                                 f"Si el número está mal tipeado, usá «Corregir». Mientras no decidas, el documento queda para la fila {primera} y esta no se carga."})
                    continue
        fila_dni = idx["dni"].get(dni_r) if dni_r else None
        fila_lpu = idx["lpu"].get(lpu_r) if lpu_r else None
        fila = fila_lpu or fila_dni or (idx["doc"].get(doc_r) if doc_r else None)
        if fila_dni and fila_lpu and fila_dni["id"] != fila_lpu["id"]:
            # el DNI está en una ficha y el LPU en otra: se actualiza la del LPU (sin pisar el DNI) y se ofrece unificarlas
            reg = dict(reg, dni="")
            item = {"n": n, "reg": reg0, "tipo": "conflicto", "se_carga": True, "a": fila_dni["id"], "b": fila_lpu["id"],
                    "fichas": [dict(fila_lpu, alojamiento=_texto_alojamiento(fila_lpu, unidades)),
                               dict(fila_dni, alojamiento=_texto_alojamiento(fila_dni, unidades))],
                    "mensaje": f"El DNI es de la ficha #{fila_dni['id']} y el LPU de la #{fila_lpu['id']}: se actualiza la del LPU."}
            if accion == "misma":
                unificar.append((fila_lpu["id"], fila_dni["id"]))
            elif accion == "otra":
                unificar.append((None, (fila_lpu["id"], fila_dni["id"])))
            else:
                revisar.append(item)
        posible = None
        if not fila:
            # mismo nombre que alguien que no tiene el documento que trae la fila (o la fila no trae ninguno)
            cand = idx["nombre"].get(norm_nombre(limpiar_nombre(reg.get("nombre_completo"))))
            if cand and cand["id"] not in vistos:
                compatible = (not dni_r or not cand.get("dni")) and (not lpu_r or not cand.get("lpu_norm"))
                if compatible:
                    if accion == "misma":
                        fila = cand
                    else:
                        posible = cand
                        if accion != "otra":
                            posibles.append({"n": n, "reg": reg, "candidato": dict(cand, alojamiento=_texto_alojamiento(cand, unidades))})
        if fila and fila["id"] in vistos and n != (repetidas.get(n) or {}).get("elegida"):
            if accion == "misma":
                descartadas.append({"n": n, "reg": reg0, "motivo": f"la persona figura también en la fila {vistos[fila['id']]}"})
                continue
            revisar.append({"n": n, "reg": reg0, "tipo": "repetido", "ref": vistos[fila["id"]], "caso": "misma",
                            "otras": [_resumen_fila(vistos[fila["id"]], registros[vistos[fila["id"]] - 1], unidades)],
                            "en_base": [dict(fila, alojamiento=_texto_alojamiento(fila, unidades))],
                            "mensaje": f"En el archivo que estás subiendo, esta fila y la fila {vistos[fila['id']]} son la misma persona que ya está en el sistema "
                                       f"(ficha #{fila['id']}), con datos distintos.",
                            "que_hacer": f"Elegí cuál de las dos filas vale. Mientras no elijas, se carga la fila {vistos[fila['id']]} y esta no."})
            continue
        if fila:
            det_id, actual = fila["id"], fila
            vistos[det_id] = n
            combinado = {c: actual.get(c) for c, _, _ in CAMPOS}
            mas_nuevo = not fecha_listado or not actual.get("fecha_listado") or fecha_listado > actual["fecha_listado"][:10]
            entrantes = {k: v for k, v in reg.items() if v not in (None, "") and not str(k).startswith("_")}
            if mas_nuevo:
                combinado.update(entrantes)
                trae_unidad = reg.get("unidad_id") or str(reg.get("centro_otro") or "").strip()
                if trae_unidad:
                    if reg.get("unidad_id"):
                        combinado["centro_otro"] = None
                    else:
                        combinado["unidad_id"] = None
                    otra_unidad = (combinado.get("unidad_id"), norm_nombre(combinado.get("centro_otro"))) != \
                                  (actual.get("unidad_id"), norm_nombre(actual.get("centro_otro")))
                    if otra_unidad:  # cambió de unidad: el módulo/pabellón/celda anteriores eran de la otra
                        for k in DETALLE_ALOJAMIENTO:
                            if reg.get(k) in (None, ""):
                                combinado[k] = None
            else:  # listado anterior: no pisa nada, solo completa lo vacío
                combinado.update({k: v for k, v in entrantes.items() if combinado.get(k) in (None, "")})
                if any(reg.get(k) not in (None, "") and str(reg.get(k)) != str(actual.get(k) or "") for k in ALOJAMIENTO if actual.get(k) not in (None, "")):
                    conservado.append((det_id, actual["nombre_completo"], actual["fecha_listado"][:10]))
            if set(norm_nombre(reg.get("nombre_completo")).split()) == set((actual.get("nombre_norm") or "").split()):
                combinado["nombre_completo"] = actual["nombre_completo"]  # «Gómez, Juan» = «Juan Gómez»: no se reescribe
            d, errs, _ = validar(combinado, det_id, indices=idx)
            if errs:
                revisar.append({"n": n, "reg": reg0, "tipo": _tipo_error(errs), "mensaje": " ".join(errs), "ficha": det_id})
                continue
            cambios = [c for c, _, _ in CAMPOS if str(actual.get(c) or "") != str(d.get(c) or "")]
            if cambios or (fecha_listado and mas_nuevo):
                actualizar.append((det_id, combinado, cambios, mas_nuevo))
            if any(c in ALOJAMIENTO for c in cambios) and any(actual.get(k) not in (None, "") for k in ALOJAMIENTO):
                mudanzas.append({"id": det_id, "nombre": actual["nombre_completo"], "dni": actual.get("dni"),
                                 "antes": _texto_alojamiento(actual, unidades), "despues": _texto_alojamiento(d, unidades),
                                 "unidad": any(c in ("unidad_id", "centro_otro") for c in cambios)})
            if not actual.get("activo") and str(actual.get("motivo_baja") or "").startswith((EGRESO_SPF, ANULADA)):
                reingresos.append({"id": det_id, "nombre": actual["nombre_completo"], "dni": actual.get("dni"), "motivo": actual.get("motivo_baja")})
        else:
            d, errs, _ = validar(reg, indices=idx)
            if errs:
                revisar.append({"n": n, "reg": reg0, "tipo": _tipo_error(errs), "mensaje": " ".join(errs)})
                continue
            nuevas.append(dict(reg, _posible_de=posible["id"], _decision=accion) if posible else reg)
    # egresos: solo con un listado del SPF con fecha, y solo contra listados anteriores del SPF
    egresos, egresos_unidades = [], []
    if fuerza_id and fecha_listado and es_spf(fuerza_id):
        unidades_listado = {r.get("unidad_id") for r in registros if r.get("unidad_id")} | \
                           {("otro", norm_nombre(r.get("centro_otro"))) for r in registros if not r.get("unidad_id") and r.get("centro_otro")}
        for f in idx["filas"].values():
            if not f.get("activo") or f["id"] in vistos or f.get("fuerza_id") != fuerza_id:
                continue
            if not f.get("fecha_listado") or f["fecha_listado"][:10] >= fecha_listado:
                continue  # lo informó un listado igual o más nuevo que este
            e = {"id": f["id"], "nombre": f["nombre_completo"], "dni": f.get("dni"), "lpu": f.get("lpu"),
                 "alojamiento": _texto_alojamiento(f, unidades), "ultimo_listado": f["fecha_listado"][:10]}
            egresos.append(e)
            clave_u = f.get("unidad_id") or ("otro", norm_nombre(f.get("centro_otro")))
            if clave_u in unidades_listado:
                egresos_unidades.append(e)
        egresos.sort(key=lambda e: norm_nombre(e["nombre"]))
    sin_resolver = [r for r in revisar if not r.get("se_carga")]
    return {"nuevas": nuevas, "actualizar": actualizar, "errores": sin_resolver, "revisar": revisar, "descartadas": descartadas,
            "documentos": documentos, "unificar": unificar, "conservado": conservado,
            "aloj_actualiza": sum(1 for x in actualizar if x[3] and any(c in ALOJAMIENTO for c in x[2])),
            "mudanzas": mudanzas, "reingresos": reingresos, "egresos": egresos, "posibles": posibles,
            "egresos_unidades": [e for e in egresos if e in egresos_unidades], "es_spf": bool(fuerza_id and es_spf(fuerza_id)),
            "por_tipo": {t: sum(1 for r in revisar if r["tipo"] == t) for t in TIPOS_REVISAR}}


def controlar_esquema(registros, fuerza_id=None, archivo_esquemas=None):
    """Propuesta 36: el esquema de la fuerza (datos/esquemas_listados.json, optativo; si no, el general) corrido completo sobre
    el listado: todos los problemas con fila y columna."""
    from pathlib import Path

    from dgpdh.dominio import esquema_listados as el
    ruta = Path(archivo_esquemas) if archivo_esquemas else db.RUTA_DB.parent / "esquemas_listados.json"
    propio = None
    if fuerza_id and ruta.is_file():
        try:
            propio = (json.loads(ruta.read_text(encoding="utf-8")) or {}).get(str(fuerza_id))
        except ValueError:
            logging.warning("esquemas_listados.json no es un JSON válido: se usa el esquema general")
    esquema = el.esquema_desde(propio)
    fallas = el.controlar(registros, esquema)
    return {"esquema": esquema.nombre, "fallas": fallas, "por_columna": el.resumen(fallas), "columnas": esquema.columnas}


def alertas_listado(registros, fuerza_id=None, fecha_listado=None):
    """Propuesta 37: lo raro de este listado comparado con el anterior de la misma fuerza (unidades que bajan mucho o faltan,
    más filas sin DNI, columnas que dejaron de venir, fecha anterior a la última cargada). Solo avisa: la carga se confirma
    aparte. Devuelve una lista de textos."""
    from dgpdh.dominio import listados as dl
    if not fuerza_id:
        return []
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT id, fecha_listado, filas, informe FROM detenidos_listados WHERE fuerza_id = ? "
                                              "AND anulado_en IS NULL ORDER BY COALESCE(fecha_listado, ''), id", (fuerza_id,))]
    if not filas:
        return []
    ultima = max((f["fecha_listado"] or "")[:10] for f in filas) or None
    previas = [f for f in filas if not fecha_listado or not f["fecha_listado"] or f["fecha_listado"][:10] <= fecha_listado]
    anterior_fila = (previas or filas)[-1]
    try:
        anterior = (json.loads(anterior_fila["informe"] or "{}") or {}).get("perfil") or {"filas": anterior_fila["filas"] or 0}
    except ValueError:
        anterior = {"filas": anterior_fila["filas"] or 0}
    unidades = {f"u:{u['id']}": u["nombre"] for u in db.listar_unidades(solo_activas=False)}
    return [a.mensaje for a in dl.comparar(dl.perfil(registros), anterior, fecha_nuevo=fecha_listado,
                                           fecha_anterior=(anterior_fila["fecha_listado"] or "")[:10] or None, fecha_ultima=ultima,
                                           nombres=unidades, etiquetas=ETIQUETA)]


def aplicar_importacion(registros, usuario, origen="IMPORTADO", fecha_listado=None, fuerza_id=None, archivo=None, egresos="ninguno",
                        decisiones=None):
    """Graba la importación en UNA sola transacción. Con fecha y fuerza del listado: queda registrado el listado, cada
    persona cuyo alojamiento confirma o cambia guarda la fecha y la fuerza, y los listados anteriores no pisan datos.
    `egresos` (solo SPF): «todos» = listado completo, quien no figura egresa; «unidades» = listado parcial, solo egresa
    quien estaba en las unidades que trae; «ninguno» = no se registran egresos. Al terminar queda el informe de la carga."""
    try:  # lo que está mal en el listado tal como vino (antes de grabar), para la devolución a la fuerza
        incons = inconsistencias(registros, fecha_listado, fuerza_id)
    except Exception:
        logging.exception("No se pudieron calcular las inconsistencias del listado")
        incons = None
    p = previa(registros, fecha_listado, fuerza_id, decisiones)
    idx = _indices()
    quien = _quien(usuario)
    altas = mods = 0
    lista_egresos = []
    with db._conexion() as con:
        listado_id = con.execute("""INSERT INTO detenidos_listados (archivo, fuerza_id, fecha_listado, filas, usuario, creado_en, origen)
                                    VALUES (?,?,?,?,?,?,?)""", (archivo, fuerza_id, fecha_listado, len(registros), quien, _ahora(), origen)).lastrowid

        def marcar(det_id):
            """Fecha y fuerza del listado en la persona; lo anterior queda guardado para poder anular la carga."""
            previo = con.execute("SELECT fecha_listado, fuerza_id, listado_id FROM detenidos WHERE id = ?", (det_id,)).fetchone()
            con.execute("INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha, listado_id) VALUES (?,?,?,?,?,?,?,?)",
                        (det_id, "_listado", json.dumps(dict(previo) if previo else {}), fecha_listado, quien, origen, _ahora(), listado_id))
            con.execute("UPDATE detenidos SET fecha_listado = ?, fuerza_id = COALESCE(?, fuerza_id), listado_id = ? WHERE id = ?",
                        (fecha_listado, fuerza_id, listado_id, det_id))

        altas_ids = []
        for reg in p["nuevas"]:
            d, errs, _ = validar(reg, indices=idx)
            if not errs:
                nid = _escribir(con, d, quien, None, origen, listado_id); altas += 1
                altas_ids.append(nid)
                if reg.get("_posible_de") and reg.get("_decision") == "otra":  # se decidió que es otra persona: no vuelve a alertar
                    _decidir(con, clave_duplicado(reg["_posible_de"], nid), "duplicado", "distintas", "Decidido al importar", quien)
                if fecha_listado:
                    marcar(nid)
        for det_id, comb, cambios, mas_nuevo in p["actualizar"]:
            d, errs, _ = validar(comb, det_id, indices=idx)
            if errs:
                continue
            if cambios:
                _escribir(con, d, quien, det_id, origen, listado_id); mods += 1
            if fecha_listado and mas_nuevo:
                marcar(det_id)
            # quien había quedado de baja por una carga anulada o por un egreso del SPF vuelve a estar activo si un listado lo trae de nuevo
            previo = con.execute("SELECT activo, motivo_baja FROM detenidos WHERE id = ?", (det_id,)).fetchone()
            if previo and not previo["activo"] and str(previo["motivo_baja"] or "").startswith((ANULADA, EGRESO_SPF)):
                con.execute("UPDATE detenidos SET activo = 1, motivo_baja = NULL WHERE id = ?", (det_id,))
                con.execute("INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha, listado_id) VALUES (?,?,?,?,?,?,?,?)",
                            (det_id, "reingreso", previo["motivo_baja"], f"vuelve a figurar en el listado del {fecha_listado or 'día'}", quien, origen, _ahora(), listado_id))
        for det_id, _, fecha_actual in p["conservado"]:
            con.execute("INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha, listado_id) VALUES (?,?,?,?,?,?,?,?)",
                        (det_id, "alojamiento", f"listado del {fecha_actual}", f"listado del {fecha_listado} (anterior): se conservó el alojamiento",
                         quien, origen, _ahora(), listado_id))
        # lo que quedó sin resolver no se pierde: pasa a Revisión de la base («Pendientes de las cargas»)
        for r in p["revisar"]:
            limpio = {k: v for k, v in r["reg"].items() if not str(k).startswith("_")}
            con.execute("""INSERT INTO detenidos_pendientes (listado_id, fila, tipo, mensaje, datos, det_a, det_b, creado_en)
                           VALUES (?,?,?,?,?,?,?,?)""", (listado_id, r["n"], r["tipo"], r["mensaje"], json.dumps(limpio, ensure_ascii=False, default=str),
                                                         r.get("a"), r.get("b"), _ahora()))
        for keep, absorber in p["unificar"]:
            if keep is None:  # se decidió que son personas distintas
                _decidir(con, clave_duplicado(*absorber), "duplicado", "distintas", "Decidido al importar", quien)
        lista_egresos = {"todos": p["egresos"], "unidades": p["egresos_unidades"]}.get(egresos, [])
        if lista_egresos:
            motivo = f"{EGRESO_SPF} del {fecha_listado[8:10]}/{fecha_listado[5:7]}/{fecha_listado[:4]} (carga #{listado_id})"
            for e in lista_egresos:
                con.execute("UPDATE detenidos SET activo = 0, motivo_baja = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ? AND activo = 1",
                            (motivo, quien, _ahora(), e["id"]))
                con.execute("INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha, listado_id) VALUES (?,?,?,?,?,?,?,?)",
                            (e["id"], "egreso", "Activo / alojado", motivo, quien, origen, _ahora(), listado_id))
        if listado_id:
            informe = _informe(con, registros, p, altas_ids, lista_egresos, egresos, fecha_listado, fuerza_id)
            informe["inconsistencias"] = incons
            try:  # propuesta 37: perfil del listado, para comparar el próximo de la misma fuerza
                from dgpdh.dominio import listados as dl
                informe["perfil"] = dl.perfil(registros)
            except Exception:
                logging.exception("No se pudo armar el perfil del listado")
            con.execute("""UPDATE detenidos_listados SET altas = ?, actualizadas = ?, alojamiento_actualizado = ?, alojamiento_conservado = ?,
                           egresos = ?, informe = ? WHERE id = ?""",
                        (altas, mods, p["aloj_actualiza"], len(p["conservado"]), len(lista_egresos), json.dumps(informe, ensure_ascii=False, default=str), listado_id))
    unificadas = 0
    for keep, absorber in p["unificar"]:  # fuera de la transacción: unificar abre la suya
        if keep:
            try:
                unificar(keep, absorber, usuario); unificadas += 1
                with db._conexion() as con:
                    _decidir(con, clave_duplicado(keep, absorber), "duplicado", "unificar", "Decidido al importar", quien)
            except ValueError:
                pass
    return {"altas": altas, "actualizadas": mods, "errores": len(p["errores"]), "conservado": len(p["conservado"]),
            "aloj_actualiza": p["aloj_actualiza"], "egresos": len(lista_egresos), "reingresos": len(p["reingresos"]), "listado_id": listado_id,
            "pendientes": len(p["errores"]), "descartadas": len(p["descartadas"]), "unificadas": unificadas,
            "documentos": len(p["documentos"])}


def _informe(con, registros, p, altas_ids, egresados, alcance_egresos, fecha_listado, fuerza_id):
    """Informe de la carga: unidades, alojados, condenados/procesados, altas, egresos y cambios de alojamiento.
    Se calcula al grabar y queda guardado con la carga (no cambia si la base se modifica después)."""
    from collections import Counter
    unidades = {u["id"]: u["nombre"] for u in db.listar_unidades(solo_activas=False)}
    centro = lambda r: unidades.get(r.get("unidad_id")) if r.get("unidad_id") else (str(r.get("centro_otro") or "").strip() or "Sin unidad")
    en_listado = Counter(centro(r) for r in registros)
    # situación de quienes vinieron en el listado, ya consolidados (lo que trae el listado completa lo que había)
    ids = [x[0] for x in p["actualizar"]] + list(altas_ids)
    filas = []
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        filas += [dict(f) for f in con.execute(f"SELECT * FROM detenidos WHERE id IN ({','.join('?' * len(lote))})", lote)]
    condenados = sum(1 for f in filas if es_condenado(f))
    procesados = sum(1 for f in filas if es_procesado(f))
    activos = con.execute("SELECT COUNT(*) FROM detenidos WHERE activo = 1").fetchone()[0]
    activos_fuerza = con.execute("SELECT COUNT(*) FROM detenidos WHERE activo = 1 AND fuerza_id = ?", (fuerza_id,)).fetchone()[0] if fuerza_id else None
    por_unidad = Counter()
    for f in con.execute("SELECT unidad_id, centro_otro FROM detenidos WHERE activo = 1"):
        por_unidad[centro(dict(f))] += 1
    altas = []
    for i in range(0, len(altas_ids), 500):
        lote = list(altas_ids)[i:i + 500]
        altas += [{"id": f["id"], "nombre": f["nombre_completo"], "dni": f["dni"], "alojamiento": _texto_alojamiento(dict(f), unidades)}
                  for f in con.execute(f"SELECT * FROM detenidos WHERE id IN ({','.join('?' * len(lote))})", lote)]
    altas.sort(key=lambda a: norm_nombre(a["nombre"]))
    return {"fecha_listado": fecha_listado, "filas": len(registros), "errores": len(p["errores"]),
            "unidades_detectadas": len(en_listado), "personas_por_unidad_listado": en_listado.most_common(),
            "alojados_base": activos, "alojados_fuerza": activos_fuerza,
            "alojados_por_unidad": [(u, por_unidad.get(u, 0)) for u, _ in en_listado.most_common()],
            "condenados": condenados, "procesados": procesados, "sin_situacion": len(filas) - condenados - procesados,
            "altas": altas, "egresos": egresados, "alcance_egresos": alcance_egresos if p["es_spf"] else None,
            "egresos_sin_registrar": len(p["egresos"]) - len(egresados) if p["es_spf"] else 0,
            "mudanzas": p["mudanzas"], "cambian_unidad": sum(1 for m in p["mudanzas"] if m["unidad"]),
            "reingresos": p["reingresos"], "actualizadas": len(p["actualizar"]), "conservado": len(p["conservado"]),
            "descartadas": [{"n": d["n"], "nombre": d["reg"].get("nombre_completo"), "dni": d["reg"].get("dni"), "motivo": d.get("motivo", "descartada a mano")}
                            for d in p["descartadas"]],
            "documentos": p["documentos"], "pendientes": len(p["errores"])}



# ---------------------------------------------------------------- devolución a la fuerza: inconsistencias del listado
TIPOS_INCONSISTENCIA = {
    "doc_repetido": ("Documento repetido en el listado", "Un mismo DNI o LPU figura en filas de personas distintas.",
                     "Indicar a qué persona corresponde el documento y corregir el de la otra."),
    "dos_lugares": ("Persona informada dos veces con distinto alojamiento", "La misma persona figura en más de una fila con lugares distintos.",
                    "Informar el alojamiento actual (una sola fila por persona)."),
    "fila_duplicada": ("Fila duplicada", "La misma persona figura dos veces con los mismos datos.", "Eliminar la fila repetida."),
    "doc_extranjero": ("Documento extranjero en la columna DNI", "El número informado como DNI no es un DNI argentino.",
                       "Informar el documento extranjero en una columna aparte y el DNI argentino, si lo tiene."),
    "doc_invalido": ("Número de documento inválido", "El DNI tiene una cantidad de dígitos que no corresponde a un DNI argentino.",
                     "Verificar y corregir el número."),
    "sin_dni": ("DNI informado en cero o «no tiene»", "La columna DNI trae 0, «no tiene» o un texto sin número.",
                "Informar el DNI si la persona lo tiene, o dejar la celda vacía."),
    "doc_cruzado": ("DNI y LPU que no se corresponden", "En los registros de la PPN, el DNI y el LPU informados pertenecen a fichas distintas.",
                    "Verificar el DNI y el LPU de la persona."),
    "fecha": ("Fecha inválida", "Una fecha (nacimiento, detención o cumplimiento) no es una fecha válida.", "Corregir la fecha (DD/MM/AAAA)."),
    "nombre": ("Fila sin nombre", "La fila no trae apellido ni nombre.", "Completar el apellido y nombre o eliminar la fila."),
    "otro": ("Otros datos con error", "Un dato de la fila no tiene un formato válido.", "Verificar el dato indicado."),
    "lugar": ("Lugar de alojamiento no identificado", "El nombre del lugar no coincide con ninguna unidad del catálogo de la PPN.",
              "Informar el nombre oficial de la unidad (y módulo/pabellón en columnas aparte)."),
}


# nombre corto para elegir qué temas van en el informe
TEMAS_INCONSISTENCIA = {"doc_repetido": "DNI duplicado", "dos_lugares": "Doble alojamiento", "fila_duplicada": "Fila duplicada",
                        "doc_extranjero": "DNI extranjero", "doc_invalido": "DNI inválido", "sin_dni": "DNI en cero / «no tiene»",
                        "doc_cruzado": "DNI y LPU cruzados", "fecha": "Fecha inválida", "nombre": "Sin nombre", "otro": "Otros errores",
                        "lugar": "Lugar no identificado"}


def filtrar_inconsistencias(datos, temas):
    """Solo los temas elegidos (claves de TIPOS_INCONSISTENCIA). Sin temas válidos, devuelve todo."""
    temas = {t for t in (temas or []) if t in TIPOS_INCONSISTENCIA}
    if not temas:
        return datos
    items = [i for i in datos["items"] if i["tipo"] in temas]
    lugares = datos["lugares"] if "lugar" in temas else []
    por_tipo = {t: n for t, n in datos["por_tipo"].items() if t in temas}
    return dict(datos, items=items, lugares=lugares, por_tipo=por_tipo, filas_con_problemas=len({i["fila"] for i in items}),
                temas=sorted(temas, key=list(TIPOS_INCONSISTENCIA).index))


def inconsistencias(registros, fecha_listado=None, fuerza_id=None):
    """Lo que está mal en el listado TAL COMO VINO (sin las decisiones tomadas al cargarlo), para devolvérselo a la fuerza
    que lo envió. Devuelve {"items": [{fila, nombre, dni, lpu, lugar, tipo, detalle}], "lugares": [(lugar, personas)],
    "por_tipo": {tipo: n}, "filas": total}. No incluye datos de otras personas de la base de la PPN."""
    from collections import Counter
    p = previa(registros, fecha_listado, fuerza_id, None)
    unidades = {u["id"]: u["nombre"] for u in db.listar_unidades(solo_activas=False)}
    def fila(n, tipo, detalle):
        r = registros[n - 1]
        return {"fila": n, "nombre": r.get("nombre_completo") or "", "dni": str(r.get("dni") or ""), "lpu": str(r.get("lpu") or ""),
                "lugar": unidades.get(r.get("unidad_id")) if r.get("unidad_id") else (r.get("centro_otro") or ""), "tipo": tipo, "detalle": detalle}
    items, vistos = [], set()
    def agregar(it):
        clave = (it["fila"], it["tipo"])
        if clave not in vistos:
            vistos.add(clave); items.append(it)
    for d in p["documentos"]:
        aviso = (d.get("aviso") or "").lower()
        tipo = "doc_extranjero" if "extranjero" in aviso and "más de 8" not in aviso else "doc_invalido"
        agregar(fila(d["n"], tipo, f"Informado: «{d.get('original') or ''}»"))
    for n, r in enumerate(registros, 1):
        crudo = str(r.get("dni") if r.get("dni") is not None else "").strip()
        if crudo and not (solo_digitos(crudo) or "").strip("0") and not any(x["fila"] == n for x in items):
            agregar(fila(n, "sin_dni", f"Informado: «{crudo}»"))
    for x in p["revisar"]:
        n = x["n"]
        if x["tipo"] == "repetido":
            otras = ", ".join(str(o["n"]) for o in x.get("otras") or []) or str(x.get("ref") or "")
            if x.get("caso") == "distintas":
                agregar(fila(n, "doc_repetido", f"Mismo documento que la fila {otras}, de otra persona"))
            else:
                agregar(fila(n, "dos_lugares", f"También figura en la fila {otras}, con otro alojamiento"))
        elif x["tipo"] == "conflicto":
            agregar(fila(n, "doc_cruzado", "El DNI y el LPU corresponden a personas distintas en los registros de la PPN"))
        elif x["tipo"] in ("fecha", "nombre"):
            agregar(fila(n, x["tipo"], re.sub(r"\s*\(.*?\)", "", x.get("mensaje") or "").strip()))
        else:
            agregar(fila(n, "otro", re.sub(r"\s*\((si es|usá|cargalo).*?\)", "", x.get("mensaje") or "").strip()))
    for d in p["descartadas"]:
        if d.get("auto"):
            ref = re.search(r"fila (\d+)", d.get("motivo") or "")
            agregar(fila(d["n"], "fila_duplicada", f"Igual a la fila {ref.group(1)}" if ref else "Fila repetida"))
    items.sort(key=lambda i: (list(TIPOS_INCONSISTENCIA).index(i["tipo"]), i["fila"]))
    lugares = [(nombre, cant) for nombre, cant, _ in centros_sin_catalogo(registros, fuerza_id)]
    por_tipo = Counter(i["tipo"] for i in items)
    if lugares:
        por_tipo["lugar"] = sum(c for _, c in lugares)
    return {"items": items, "lugares": lugares, "por_tipo": dict(por_tipo), "filas": len(registros),
            "filas_con_problemas": len({i["fila"] for i in items})}


def reporte_inconsistencias(datos, meta, destino):
    """Arma el informe de devolución (.docx). `meta`: fuerza, fecha_listado, archivo, fecha_carga, carga (id o None), usuario."""
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Cm(29.7), Cm(21.0)
    for m in ("left_margin", "right_margin"):
        setattr(sec, m, Cm(1.6))
    sec.top_margin = sec.bottom_margin = Cm(1.5)
    est = doc.styles["Normal"]; est.font.name = "Arial"; est.font.size = Pt(9.5)
    rojo = RGBColor(0xA3, 0x26, 0x2E)
    def parrafo(texto, tam=9.5, negrita=False, color=None, alinear=None, despues=4, junto=True):
        q = doc.add_paragraph(); r = q.add_run(texto); r.font.size = Pt(tam); r.bold = negrita
        if color: r.font.color.rgb = color
        if alinear: q.alignment = alinear
        q.paragraph_format.space_after = Pt(despues)
        q.paragraph_format.keep_with_next = junto
        return q
    def anchos(tabla, cms, encabezado=True):
        """Anchos fijos (Word y LibreOffice), filas que no se parten y encabezado repetido en cada página."""
        tabla.autofit = False
        grid = tabla._tbl.tblGrid
        for gc, w in zip(grid.findall(qn("w:gridCol")), cms):
            gc.set(qn("w:w"), str(int(w * 567)))
        for i, fila in enumerate(tabla.rows):
            trPr = fila._tr.get_or_add_trPr()
            cs = OxmlElement("w:cantSplit"); trPr.append(cs)
            if encabezado and i == 0:
                th = OxmlElement("w:tblHeader"); trPr.append(th)
            for celda, w in zip(fila.cells, cms):
                celda.width = Cm(w)
    def sombrear(celda, color):
        tc = celda._tc.get_or_add_tcPr(); sh = OxmlElement("w:shd")
        sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), color); tc.append(sh)
    parrafo("PROCURACIÓN PENITENCIARIA DE LA NACIÓN", 9, True, rojo, despues=0)
    parrafo("Dirección General de Protección de Derechos Humanos", 9, despues=10)
    parrafo("Inconsistencias detectadas en el listado de personas alojadas", 16, True, despues=6)
    fl = meta.get("fecha_listado")
    fl = f"{fl[8:10]}/{fl[5:7]}/{fl[:4]}" if fl else "sin fecha"
    renglones = [("Fuerza / organismo que informa", meta.get("fuerza") or "—"), ("Fecha del listado", fl),
                 ("Archivo recibido", meta.get("archivo") or "—"),
                 ("Fecha de revisión", meta.get("fecha_carga") or datetime.now().strftime("%d/%m/%Y")),
                 ("Filas del listado", str(datos["filas"])),
                 ("Filas con observaciones", f"{datos['filas_con_problemas']} ({round(100 * datos['filas_con_problemas'] / max(1, datos['filas']))} %)")]
    t = doc.add_table(rows=0, cols=2); t.style = "Table Grid"
    for k, v in renglones:
        c = t.add_row().cells; c[0].text = k; c[1].text = v; sombrear(c[0], "F3EEEA")
    anchos(t, (6.5, 12), encabezado=False)
    parrafo("", despues=2)
    parrafo("Al procesar el listado se detectaron las observaciones que siguen. Se solicita revisarlas y, de corresponder, "
            "remitir el listado corregido en los próximos envíos. Las filas observadas se registraron igual, con las salvedades indicadas, "
            "para no perder información.", despues=8)
    if not datos["items"] and not datos["lugares"]:
        parrafo("No se detectaron inconsistencias en el listado.", 11, True, despues=6)
    # resumen
    parrafo("Resumen", 12, True, rojo, despues=4)
    r = doc.add_table(rows=1, cols=3); r.style = "Table Grid"
    for c, txt in zip(r.rows[0].cells, ("Observación", "Cantidad", "Qué se solicita")):
        c.text = txt; c.paragraphs[0].runs[0].bold = True; sombrear(c, "F3EEEA")
    for tipo, (nombre, _, pedido) in TIPOS_INCONSISTENCIA.items():
        n = datos["por_tipo"].get(tipo)
        if n:
            c = r.add_row().cells; c[0].text = nombre; c[1].text = str(n); c[2].text = pedido
    anchos(r, (10, 2.5, 13.9))
    # detalle por tipo
    for tipo, (nombre, explicacion, pedido) in TIPOS_INCONSISTENCIA.items():
        if tipo == "lugar":
            continue
        grupo = [i for i in datos["items"] if i["tipo"] == tipo]
        if not grupo:
            continue
        parrafo("", despues=2)
        parrafo(f"{nombre} ({len(grupo)})", 12, True, rojo, despues=2)
        parrafo(f"{explicacion} {pedido}", 9, despues=4)
        tb = doc.add_table(rows=1, cols=6); tb.style = "Table Grid"
        for c, txt in zip(tb.rows[0].cells, ("Fila", "Apellido y nombre", "DNI informado", "LPU", "Alojamiento informado", "Observación")):
            c.text = txt; c.paragraphs[0].runs[0].bold = True; sombrear(c, "F3EEEA")
        for i in grupo:
            c = tb.add_row().cells
            for celda, v in zip(c, (str(i["fila"]), i["nombre"], i["dni"], i["lpu"], i["lugar"], i["detalle"])):
                celda.text = v or "—"
        anchos(tb, (1.3, 6.2, 3.4, 2.2, 6.2, 7.1))
    if datos["lugares"]:
        nombre, explicacion, pedido = TIPOS_INCONSISTENCIA["lugar"]
        parrafo("", despues=2)
        parrafo(f"{nombre} ({len(datos['lugares'])})", 12, True, rojo, despues=2)
        parrafo(f"{explicacion} {pedido}", 9, despues=4)
        tb = doc.add_table(rows=1, cols=2); tb.style = "Table Grid"
        for c, txt in zip(tb.rows[0].cells, ("Lugar como figura en el listado", "Personas")):
            c.text = txt; c.paragraphs[0].runs[0].bold = True; sombrear(c, "F3EEEA")
        for lugar, cant in datos["lugares"]:
            c = tb.add_row().cells; c[0].text = lugar; c[1].text = str(cant)
        anchos(tb, (20, 4))
    # pie de cada página: reserva de los datos y número de página
    pie = sec.footer.paragraphs[0]; pie.alignment = WD_ALIGN_PARAGRAPH.LEFT
    nota = pie.add_run("Sistema de Gestión DGPDH · Contiene datos personales: tratarlo con la reserva correspondiente (Ley 25.326) · ")
    nota.font.size = Pt(7.5); nota.font.color.rgb = RGBColor(0x80, 0x78, 0x72)
    run = pie.add_run("Página "); run.font.size = Pt(7.5); run.font.color.rgb = RGBColor(0x80, 0x78, 0x72)
    for codigo in ("PAGE",):
        f1 = OxmlElement("w:fldSimple"); f1.set(qn("w:instr"), codigo); rr = OxmlElement("w:r"); tt = OxmlElement("w:t"); tt.text = "1"
        rr.append(tt); f1.append(rr); pie._p.append(f1)
    doc.save(destino)
    return destino


def informe_de(listado_id):
    with db._conexion() as con:
        l = con.execute("SELECT l.*, f.nombre AS fuerza FROM detenidos_listados l LEFT JOIN fuerzas f ON f.id = l.fuerza_id WHERE l.id = ?",
                        (listado_id,)).fetchone()
    if not l:
        return None
    l = dict(l)
    try:
        l["informe"] = json.loads(l["informe"]) if l.get("informe") else None
    except ValueError:
        l["informe"] = None
    return l


def listados(limite=20):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("""SELECT l.*, f.nombre AS fuerza FROM detenidos_listados l LEFT JOIN fuerzas f ON f.id = l.fuerza_id
                                                ORDER BY l.id DESC LIMIT ?""", (limite,))]


ANULADA = "Carga de listado anulada"


def _cambios_de_listado(con, l):
    """Los cambios que hizo una carga. Las cargas nuevas los tienen marcados; para las anteriores a esta versión se
    reconocen por quién y cuándo se grabaron (la carga se graba toda junta, en segundos)."""
    filas = [dict(f) for f in con.execute("SELECT * FROM detenidos_cambios WHERE listado_id = ? ORDER BY id", (l["id"],))]
    if filas:
        return filas, True
    from datetime import timedelta as _td
    ini = datetime.fromisoformat(l["creado_en"])
    fin = (ini + _td(minutes=5)).isoformat(timespec="seconds")
    sig = con.execute("SELECT MIN(creado_en) FROM detenidos_listados WHERE id > ?", (l["id"],)).fetchone()[0]
    if sig and sig < fin:
        fin = sig
    filas = [dict(f) for f in con.execute(
        "SELECT * FROM detenidos_cambios WHERE usuario IS ? AND fecha >= ? AND fecha < ? AND origen NOT IN ('MANUAL', 'ANULACION') ORDER BY id",
        (l["usuario"], l["creado_en"], fin))]
    return filas, False


def resumen_anulacion(listado_id):
    """Qué haría anular la carga: personas que se dan de baja (las que dio de alta) y datos que vuelven atrás."""
    with db._conexion() as con:
        l = con.execute("SELECT * FROM detenidos_listados WHERE id = ?", (listado_id,)).fetchone()
        if not l:
            return None
        l = dict(l)
        cambios, exacto = _cambios_de_listado(con, l)
    altas = sorted({c["detenido_id"] for c in cambios if c["campo"] == "alta"})
    datos = [c for c in cambios if c["campo"] not in ("alta", "alojamiento", "_listado", "egreso", "reingreso")]
    return {"listado": l, "altas": altas, "personas_actualizadas": len({c["detenido_id"] for c in datos} - set(altas)),
            "egresos": len({c["detenido_id"] for c in cambios if c["campo"] == "egreso"}),
            "campos": len([c for c in datos if c["detenido_id"] not in altas]), "exacto": exacto}


def anular_listado(listado_id, motivo, usuario):
    """Anula una carga completa hecha por error. No se borra nada: quienes entraron con esa carga quedan de baja
    («Carga de listado anulada») y los datos que la carga cambió vuelven a su valor anterior, salvo que alguien los haya
    modificado después (eso se respeta y se informa). Todo queda en el historial de cada persona."""
    motivo = (motivo or "").strip()[:300]
    if not motivo:
        raise ValueError("Contá por qué se anula la carga.")
    quien = _quien(usuario)
    ahora = _ahora()
    with db._conexion() as con:
        l = con.execute("SELECT * FROM detenidos_listados WHERE id = ?", (listado_id,)).fetchone()
        if not l:
            raise ValueError("La carga no existe.")
        l = dict(l)
        if l.get("anulado_en"):
            raise ValueError("Esa carga ya estaba anulada.")
        cambios, exacto = _cambios_de_listado(con, l)
        altas = {c["detenido_id"] for c in cambios if c["campo"] == "alta"}
        etiqueta = f"{ANULADA} (#{listado_id}{' · ' + l['archivo'] if l.get('archivo') else ''})"
        bajas = revertidos = respetados = 0
        log = "INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha) VALUES (?,?,?,?,?,?,?)"
        for det_id in sorted(altas):
            f = con.execute("SELECT activo FROM detenidos WHERE id = ?", (det_id,)).fetchone()
            if f and f["activo"]:
                con.execute("UPDATE detenidos SET activo = 0, motivo_baja = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                            (etiqueta, quien, ahora, det_id))
                con.execute(log, (det_id, "activo", "1", "0", quien, "ANULACION", ahora))
                bajas += 1
        columnas = {c for c, _, _ in CAMPOS}
        for c in reversed(cambios):  # del último al primero
            det_id = c["detenido_id"]
            if det_id in altas:
                continue
            if c["campo"] in columnas:
                actual = con.execute(f"SELECT {c['campo']} AS v FROM detenidos WHERE id = ?", (det_id,)).fetchone()
                if actual is None:
                    continue
                if str(actual["v"] if actual["v"] is not None else "") != str(c["despues"] or ""):
                    respetados += 1  # se modificó después de la carga: no se toca
                    continue
                valor = c["antes"]
                if c["campo"] == "unidad_id":
                    valor = int(valor) if valor and str(valor).isdigit() else None
                con.execute(f"UPDATE detenidos SET {c['campo']} = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                            (valor, quien, ahora, det_id))
                if c["campo"] == "nombre_completo":
                    con.execute("UPDATE detenidos SET nombre_norm = ? WHERE id = ?", (norm_nombre(valor), det_id))
                if c["campo"] == "lpu":
                    con.execute("UPDATE detenidos SET lpu_norm = ? WHERE id = ?", (norm_num(valor) or None, det_id))
                con.execute(log, (det_id, c["campo"], c["despues"], c["antes"], quien, "ANULACION", ahora))
                revertidos += 1
            elif c["campo"] == "egreso":  # el egreso que registró la carga se deshace (si nadie lo tocó después)
                f = con.execute("SELECT activo, motivo_baja FROM detenidos WHERE id = ?", (det_id,)).fetchone()
                if f and not f["activo"] and f["motivo_baja"] == c["despues"]:
                    con.execute("UPDATE detenidos SET activo = 1, motivo_baja = NULL, actualizado_por = ?, actualizado_en = ? WHERE id = ?", (quien, ahora, det_id))
                    con.execute(log, (det_id, "egreso", c["despues"], "Activo / alojado (se anuló la carga)", quien, "ANULACION", ahora))
                    revertidos += 1
                else:
                    respetados += 1
            elif c["campo"] == "reingreso":
                f = con.execute("SELECT activo FROM detenidos WHERE id = ?", (det_id,)).fetchone()
                if f and f["activo"]:
                    con.execute("UPDATE detenidos SET activo = 0, motivo_baja = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?", (c["antes"], quien, ahora, det_id))
                    con.execute(log, (det_id, "baja", None, c["antes"], quien, "ANULACION", ahora))
                    revertidos += 1
            elif c["campo"] == "_listado":
                try:
                    previo = json.loads(c["antes"] or "{}")
                except ValueError:
                    previo = {}
                con.execute("""UPDATE detenidos SET fecha_listado = ?, fuerza_id = ?, listado_id = ? WHERE id = ? AND listado_id = ?""",
                            (previo.get("fecha_listado"), previo.get("fuerza_id"), previo.get("listado_id"), det_id, listado_id))
        con.execute("UPDATE detenidos_listados SET anulado_en = ?, anulado_por = ?, motivo_anulacion = ? WHERE id = ?",
                    (ahora, quien, motivo, listado_id))
    return {"bajas": bajas, "revertidos": revertidos, "respetados": respetados, "exacto": exacto}


def desde_planillas_expedientes():
    """Registros tomados de las planillas de expedientes de Mesa (solo lectura): nombre, DNI, LPU, expediente y unidad."""
    import expedientes
    registros = []
    for r in expedientes.cargar():
        reg = {}
        for enc, valor in r["datos"].items():
            h = norm_nombre(enc)
            for clave in ("lpu", "dni", "nro_expediente_ppn", "nombre_completo", "centro", "fecha_nacimiento", "juzgado"):
                if clave not in reg and any(k == h or k in h for k in ENCABEZADOS[clave]):
                    reg[clave] = valor
                    break
        if reg.get("nombre_completo") and (reg.get("dni") or reg.get("lpu")):
            centro = str(reg.pop("centro", "") or "")
            if centro:
                u = db.proponer_unidades(centro, 1)
                reg["unidad_id"], reg["centro_otro"] = (u[0]["id"], None) if u else (None, centro)
            registros.append(reg)
    return registros


# ---------------------------------------------------------------- exportación e intercambio
COLUMNAS_EXPORTACION = [("id", "ID"), ("nombre_completo", "Nombre completo"), ("dni", "DNI"), ("lpu", "LPU"), ("alias", "Alias"),
                        ("fecha_nacimiento", "Fecha de nacimiento"), ("es_adulto", "Es adulto"), ("edad", "Edad"),
                        ("nro_expediente_ppn", "Número de expediente PPN"), ("codigo", "Código"), ("centro", "Centro de detención"),
                        ("modulo", "Módulo de detención"), ("pabellon", "Pabellón"), ("celda", "Celda"), ("juzgado", "Juzgado"),
                        ("tribunal", "Tribunal"), ("defensor", "Defensor/a"), ("situacion_procesal", "Situación procesal"),
                        ("condena_anios", "Años de condena"), ("condena_meses", "Meses de condena"), ("condena_dias", "Días de condena"),
                        ("fecha_cumplimiento", "Fecha de cumplimiento de pena"), ("apellido", "Apellido"), ("nombres", "Nombres"),
                        ("nacionalidad", "Nacionalidad"), ("fuerza", "Fuerza que informó"), ("fecha_listado", "Fecha del listado"), ("carga_fecha", "Fecha de carga"), ("vigencia", "Vigencia del dato"), ("doc_extranjero", "Documento extranjero"),
                        ("observaciones", "Observaciones"), ("activo", "Activo"),
                        ("motivo_baja", "Motivo de baja"), ("actualizado_en", "Última actualización")]


def _valor_export(d, k):
    v = d.get(k)
    if k in ("es_adulto",):
        return "" if v is None else ("Sí" if v else "No")
    if k == "activo":
        return "Sí" if v else "No"
    return "" if v is None else v


def exportar_excel(lista, columnas=None, constancia=None):
    """`columnas` (propuesta 46): las del perfil elegido; por defecto, todas. `constancia`: filas para la hoja «Constancia»."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    cols = columnas or COLUMNAS_EXPORTACION
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Personas detenidas"
    ws.append([e for _, e in cols])
    for j in range(1, len(cols) + 1):
        c = ws.cell(row=1, column=j)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="7C1413")
    col_fn = next((j for j, (k, _) in enumerate(cols, 1) if k == "fecha_nacimiento"), None)
    for d in lista:
        ws.append([_valor_export(d, k) for k, _ in cols])
        if d.get("fecha_nacimiento") and col_fn:
            ws.cell(row=ws.max_row, column=col_fn).value = date.fromisoformat(d["fecha_nacimiento"])
            ws.cell(row=ws.max_row, column=col_fn).number_format = "DD/MM/YYYY"
    anchos = dict(zip([k for k, _ in COLUMNAS_EXPORTACION],
                      [6, 30, 12, 12, 16, 14, 9, 7, 18, 10, 34, 12, 10, 8, 30, 24, 24, 20, 8, 8, 8, 14, 22, 22, 14, 30, 12, 12, 14, 18, 30, 8, 20, 20], strict=False))
    for j, (k, _) in enumerate(cols, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = anchos.get(k, 14)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    if constancia:
        hoja = wb.create_sheet("Constancia")
        for fila in constancia:
            hoja.append(list(fila))
        hoja.column_dimensions["A"].width, hoja.column_dimensions["B"].width = 14, 90
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def exportar_csv(lista, columnas=None):
    cols = columnas or COLUMNAS_EXPORTACION
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([e for _, e in cols])
    for d in lista:
        w.writerow([_valor_export(d, k) for k, _ in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def preparar_exportacion(u, perfil, motivo, cantidad, ip=None, formato="xlsx", origen=""):
    """Propuesta 46: columnas del perfil, constancia y registro (con aviso a la Dirección si se pasa del volumen del día).
    Devuelve (columnas, constancia, error). `error` es un texto para la persona si falta el motivo."""
    from dgpdh.dominio import exportaciones as ex
    perfil = perfil if perfil in ex.PERFILES else "completa"
    motivo = (motivo or "").strip()[:300]
    if perfil == "completa" and EXTERNO.get("exigir_motivo_exportacion") and len(motivo) < 5:
        return None, None, "Para exportar la base completa escribí el motivo (o elegí la exportación mínima)."
    ident = ex.identificador()
    quien = (u or {}).get("nombre", "Sistema")
    registrar_acceso(u, "DETENIDOS_EXPORTAR", f"{origen}{formato}: {cantidad} persona(s) · {ident} · {perfil}" + (f" · {motivo}" if motivo else ""), ip)
    try:
        hoy = date.today().isoformat()
        with db._conexion() as con:
            detalles = [f[0] for f in con.execute("SELECT detalle FROM seguridad_eventos WHERE tipo = 'DETENIDOS_EXPORTAR' AND usuario = ? "
                                                  "AND fecha >= ?", (quien, hoy))]
        total, umbral = ex.cantidad_exportada(detalles), int(EXTERNO.get("aviso_exportacion_fichas") or ex.UMBRAL_AVISO)
        if umbral and total > umbral and total - cantidad <= umbral:  # se avisa una vez, al pasar el umbral
            import auth
            ids = [x["id"] for x in db.listar_usuarios() if x["activo"] and auth.es_admin(x)]
            if ids:
                db.notificar_usuarios(ids, f"{quien} exportó hoy {total} fichas de personas detenidas (más de {umbral}).", "/admin/seguridad/accesos")
    except Exception:
        logging.exception("No se pudo controlar el volumen de exportaciones")
    constancia = ex.constancia(usuario=quien, fecha=_ahora(), ident=ident, perfil=perfil, motivo=motivo, cantidad=cantidad)
    return ex.columnas(perfil, COLUMNAS_EXPORTACION), constancia, None


def a_json(d):
    """Formato de intercambio con otros sistemas del organismo (claves estables, sin datos internos de la app)."""
    return {"id": d["id"], "nombre_completo": d["nombre_completo"], "dni": d.get("dni"), "lpu": d.get("lpu"), "alias": d.get("alias"),
            "fecha_nacimiento": d.get("fecha_nacimiento"), "es_adulto": d.get("es_adulto"), "edad": d.get("edad"),
            "nro_expediente_ppn": d.get("nro_expediente_ppn"), "codigo": d.get("codigo"), "centro_detencion": d.get("centro"),
            "modulo": d.get("modulo"), "pabellon": d.get("pabellon"), "celda": d.get("celda"), "juzgado": d.get("juzgado"),
            "tribunal": d.get("tribunal"), "defensor": d.get("defensor"), "situacion_procesal": d.get("situacion_procesal"),
            "condena_anios": d.get("condena_anios"), "condena_meses": d.get("condena_meses"), "condena_dias": d.get("condena_dias"),
            "fecha_cumplimiento": d.get("fecha_cumplimiento"), "apellido": d.get("apellido"), "nombres": d.get("nombres"),
            "nacionalidad": d.get("nacionalidad"), "activo": bool(d.get("activo")), "actualizado_en": d.get("actualizado_en")}


def consultar_sistema_procuracion(dni=None, lpu=None):
    """PREPARADO, SIN CONECTAR: cuando el sistema del menú de la Procuración exponga su servicio, acá se pide la persona
    por DNI o LPU y se devuelve en el mismo formato de `a_json` para precargar el formulario (siempre editable)."""
    if not EXTERNO["activo"]:
        return None
    import requests
    try:
        r = requests.get(EXTERNO["url"].rstrip("/") + "/internos", params={"dni": dni or "", "lpu": lpu or ""},
                         headers={"Authorization": f"Bearer {EXTERNO['token']}"}, timeout=8)
        if r.status_code != 200:
            return None
        datos = r.json()
        return datos[0] if isinstance(datos, list) and datos else (datos or None)
    except Exception:
        return None


# ---------------------------------------------------------------- permisos y registro de accesos
def puede_ver(u, tiene_notas=True):
    """Ver y cargar: quien tiene acceso a Notas, salvo choferes."""
    import auth
    return bool(u) and tiene_notas and u["rol"] != auth.ROL_CHOFER


def puede_administrar(u):
    """Baja, reactivación, importación y exportación de la base: Dirección y Mesa de Entradas."""
    import auth
    return bool(u) and (auth.es_admin(u) or u["rol"] in auth.ROLES_MESA)


def puede_mantener(u):
    """Mantenimiento de la base (Revisión de la base, propuestas de la IA, unificar fichas): quien la administra y los
    usuarios a los que se les dio el permiso individual «Mantenimiento de bases»."""
    return puede_administrar(u) or (puede_ver(u) and bool((u or {}).get("mantiene_bases")))


def registrar_acceso(u, tipo, detalle="", ip=None):
    """Datos personales sensibles: queda constancia de quién miró la ficha, exportó o importó."""
    try:
        with db._conexion() as con:
            con.execute("INSERT INTO seguridad_eventos (usuario_id, usuario, tipo, detalle, ip, fecha) VALUES (?,?,?,?,?,?)",
                        ((u or {}).get("id"), (u or {}).get("nombre", "API"), tipo, str(detalle)[:300], ip, _ahora()))
    except Exception:
        pass


def accesos(det_id, limite=30):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT usuario, tipo, fecha FROM seguridad_eventos WHERE tipo LIKE 'DETENIDO%' AND detalle = ? "
                                             "ORDER BY id DESC LIMIT ?", (f"#{det_id}", limite))]


# ---------------------------------------------------------------- búsqueda por campos (pantalla principal)
CAMPOS_BUSQUEDA = (("apellido", "Apellido"), ("nombre", "Nombre"), ("dni", "DNI"), ("lpu", "LPU"),
                   ("expediente", "Expediente PPN"), ("codigo", "Código"))


def datos_de_busqueda(f):
    """Cuántos datos trae la búsqueda. Con DNI o LPU alcanza uno; si no, hacen falta al menos dos
    (apellido, nombre, expediente, código, centro o edad)."""
    if solo_digitos(f.get("dni")) or solo_digitos(f.get("lpu")):
        return True, ""
    n = sum(1 for k in ("apellido", "nombre", "expediente", "codigo") if str(f.get(k) or "").strip())
    n += 1 if f.get("unidad_id") else 0
    n += 1 if f.get("adultos") is not None else 0
    if n >= 2:
        return True, ""
    return False, ("Completá al menos dos datos (por ejemplo apellido y nombre, o apellido y centro de detención). "
                   "Con el DNI o el LPU alcanza uno solo.")


def buscar_campos(apellido="", nombre="", dni="", lpu="", expediente="", codigo="", unidad_id=None, adultos=None,
                  incluir_bajas=False, limite=300):
    """Búsqueda con campos separados. DNI y LPU por sus números exactos; apellido y nombre por palabras (si la persona
    tiene el apellido separado se busca ahí; si no, en el nombre completo)."""
    sql, params = "SELECT * FROM detenidos WHERE 1=1", []
    if not incluir_bajas:
        sql += " AND activo = 1"
    if solo_digitos(dni):
        sql += " AND dni = ?"; params.append(solo_digitos(dni))
    if solo_digitos(lpu):
        sql += " AND lpu_norm = ?"; params.append(solo_digitos(lpu))
    if unidad_id:
        sql += " AND unidad_id = ?"; params.append(unidad_id)
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(sql + " ORDER BY nombre_norm", params)]
    pal_ape, pal_nom = norm_nombre(apellido).split(), norm_nombre(nombre).split()
    exp, cod = norm_num(expediente), norm_num(codigo)
    unidades = {u["id"]: u["nombre"] for u in db.listar_unidades(solo_activas=False)}
    fuerzas = {f["id"]: f["nombre"] for f in listar_fuerzas(False)}; cargas = _cargas_por_id()
    salida = []
    for f in filas:
        palabras = set(f["nombre_norm"].split())
        en_ape = set(norm_nombre(f.get("apellido")).split()) or palabras
        en_nom = set(norm_nombre(f.get("nombres")).split()) or palabras
        if pal_ape and not all(any(w.startswith(p) for w in en_ape) for p in pal_ape):
            continue
        if pal_nom and not all(any(w.startswith(p) for w in en_nom) for p in pal_nom):
            continue
        if exp and exp not in norm_num(f.get("nro_expediente_ppn")):
            continue
        if cod and cod not in norm_num(f.get("codigo")):
            continue
        d = _decorar(f, unidades, fuerzas, cargas)
        if adultos is not None and d["es_adulto"] is not adultos:
            continue
        salida.append(d)
        if len(salida) >= limite:
            break
    return salida


def completar_apellidos(con):
    """Para cargas anteriores: separa apellido y nombres donde se puede (coma o apellido en mayúsculas)."""
    for f in con.execute("SELECT id, nombre_completo FROM detenidos WHERE apellido IS NULL AND nombres IS NULL").fetchall():
        a, n = separar_nombre(f["nombre_completo"])
        if a or n:
            con.execute("UPDATE detenidos SET apellido = ?, nombres = ? WHERE id = ?", (a, n, f["id"]))


# ---------------------------------------------------------------- revisión de la base (panel del administrador)
# Reglas fijas (sin IA): el sistema muestra las alertas y decide una persona. Lo decidido no vuelve a aparecer.
TIPOS_ALERTA = {
    "ia": ("Propuestas de la IA", "La IA local constató los alojados contra toda la base (alojados e histórico) y encontró posibles fichas "
           "duplicadas que las reglas exactas no ven. La IA no cambia nada: deja la unificación preparada y decidís vos."),
    "pendiente": ("Filas pendientes de las cargas", "Filas de un listado que no se pudieron cargar como venían: corregilas, cargalas sin el dato que falla o descartalas."),
    "duplicado": ("Posible misma persona", "Mismo nombre y al menos una de las fichas sin DNI ni LPU, o el DNI en una ficha y el LPU en otra. "
                  "No se muestran los pares que no pueden ser la misma persona: edades distintas, o las dos informadas en listados recientes en "
                  "lugares distintos; ni nombres de dos palabras sin otro dato que coincida."),
    "mismo_nombre": ("Mismo nombre con distinto documento", "Puede ser un error de carga del DNI o personas distintas."),
    "pena_cumplida": ("Pena cumplida y sigue activa", "La fecha de cumplimiento de pena ya pasó."),
    "menor": ("Menor de edad", "Según la fecha de nacimiento (o lo cargado a mano) es menor de 18 años."),
    "sin_documento": ("Sin DNI ni LPU", "Es difícil identificar a la persona con certeza."),
    "fuerza_distinta": ("Fuerza distinta a la de la unidad", "La fuerza que informó a la persona no es la fuerza a cargo de la unidad donde figura alojada: "
                        "puede ser un traslado no informado, un error de carga o la unidad mal asignada."),
}


def descarte_duplicado(a, b, hoy=None):
    """Por qué dos fichas con el mismo nombre NO son la misma persona (o None si puede serlo). Evita llenar la revisión
    de falsos «posible misma persona»:
      - fechas de nacimiento cargadas en las dos y más de un año de diferencia;
      - las dos figuran en listados recientes (al día), de cargas distintas y en lugares distintos: nadie está en dos
        lugares a la vez;
      - nombre de dos palabras o menos (por ejemplo, solo los nombres de pila) sin otro dato que coincida (misma fecha de
        nacimiento o misma unidad)."""
    hoy = hoy or date.today()
    fa, fb = (a.get("fecha_nacimiento") or "")[:10], (b.get("fecha_nacimiento") or "")[:10]
    if fa and fb:
        try:
            if abs((date.fromisoformat(fa) - date.fromisoformat(fb)).days) > 400:
                return "edades distintas"
        except ValueError:
            pass
    lugar = lambda x: (x.get("unidad_id"), norm_nombre(x.get("centro_otro")) if not x.get("unidad_id") else "")
    vig = lambda x: bool(x.get("fecha_listado")) and (hoy - date.fromisoformat(x["fecha_listado"][:10])).days <= DIAS_VIGENCIA
    if vig(a) and vig(b) and a.get("listado_id") != b.get("listado_id") and lugar(a) != lugar(b) and any(lugar(a)) and any(lugar(b)):
        return "las dos figuran en listados recientes en lugares distintos"
    if len((a.get("nombre_norm") or "").split()) <= 2:
        misma_fecha = fa and fa == fb
        misma_unidad = any(lugar(a)) and lugar(a) == lugar(b)
        if not (misma_fecha or misma_unidad):
            return "nombre incompleto o muy común sin otro dato que coincida"
    return None


def clave_duplicado(a, b):
    a, b = sorted((int(a), int(b)))
    return f"dup:{a}:{b}"


def _decidir(con, clave, tipo, decision, nota, quien):
    con.execute("""INSERT INTO detenidos_revision (clave, tipo, decision, nota, usuario, fecha) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(clave) DO UPDATE SET decision = excluded.decision, nota = excluded.nota, usuario = excluded.usuario,
                   fecha = excluded.fecha""", (clave, tipo, decision, (nota or "")[:300] or None, quien, _ahora()))


def inicializar_revision(con):
    con.execute("""CREATE TABLE IF NOT EXISTS detenidos_revision (
        clave TEXT PRIMARY KEY, tipo TEXT NOT NULL, decision TEXT NOT NULL, nota TEXT, usuario TEXT, fecha TEXT NOT NULL)""")
    # filas de una carga que quedaron sin resolver (no se perdieron) y fichas en conflicto (DNI en una, LPU en otra)
    con.execute("""CREATE TABLE IF NOT EXISTS detenidos_pendientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, listado_id INTEGER, fila INTEGER, tipo TEXT NOT NULL, mensaje TEXT, datos TEXT,
        det_a INTEGER, det_b INTEGER, estado TEXT NOT NULL DEFAULT 'PENDIENTE', resuelto_por TEXT, resuelto_en TEXT, resolucion TEXT,
        creado_en TEXT NOT NULL)""")


def alertas(hoy=None):
    """{tipo: [alerta]} con las alertas pendientes (sin decisión). Lee la base una sola vez."""
    hoy = hoy or date.today()
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT * FROM detenidos WHERE activo = 1")]
        decididas = {r[0] for r in con.execute("SELECT clave FROM detenidos_revision")}
    todas_u = db.listar_unidades(solo_activas=False)
    unidades = {u["id"]: u["nombre"] for u in todas_u}
    fuerza_de_unidad = {u["id"]: u.get("fuerza_id") for u in todas_u if u.get("fuerza_id")}
    fuerzas = {f["id"]: f["nombre"] for f in listar_fuerzas(False)}; cargas = _cargas_por_id()
    out = {t: [] for t in TIPOS_ALERTA}
    por_nombre = {}
    for f in filas:
        por_nombre.setdefault(f["nombre_norm"], []).append(f)
    for nombre, grupo in por_nombre.items():
        if len(grupo) < 2 or not nombre:
            continue
        grupo.sort(key=lambda x: x["id"])
        for i, a in enumerate(grupo):
            for b in grupo[i + 1:]:
                clave = clave_duplicado(a["id"], b["id"])
                if clave in decididas:
                    continue
                if descarte_duplicado(a, b, hoy):
                    continue
                sin_doc = lambda x: not x.get("dni") and not x.get("lpu_norm")
                if sin_doc(a) or sin_doc(b):
                    tipo = "duplicado"
                elif a.get("dni") != b.get("dni") or a.get("lpu_norm") != b.get("lpu_norm"):
                    tipo = "mismo_nombre"
                else:
                    continue
                out[tipo].append({"clave": clave, "personas": [_decorar(a, unidades, fuerzas, cargas), _decorar(b, unidades, fuerzas, cargas)]})
    with db._conexion() as con:
        pend = [dict(x) for x in con.execute("SELECT * FROM detenidos_pendientes WHERE estado = 'PENDIENTE' ORDER BY id")]
    por_id = {f["id"]: f for f in filas}
    for x in pend:
        if x["tipo"] == "conflicto":
            clave = clave_duplicado(x["det_a"], x["det_b"])
            if clave in decididas or x["det_a"] not in por_id or x["det_b"] not in por_id or any(a["clave"] == clave for a in out["duplicado"]):
                continue
            out["duplicado"].append({"clave": clave, "personas": [_decorar(por_id[x["det_b"]], unidades, fuerzas, cargas), _decorar(por_id[x["det_a"]], unidades, fuerzas, cargas)],
                                     "motivo": x["mensaje"]})
        else:
            datos = json.loads(x["datos"] or "{}")
            out["pendiente"].append({"clave": f"pend:{x['id']}", "pendiente": dict(x, datos=datos), "personas": []})
    import revision_ia
    props = revision_ia.pendientes()
    if props:
        faltan = {i for x in props for i in (x["det_a"], x["det_b"]) if i not in por_id}
        if faltan:
            with db._conexion() as con:
                for r in con.execute("SELECT * FROM detenidos WHERE id IN (%s)" % ",".join("?" * len(faltan)), list(faltan)):
                    por_id[r["id"]] = dict(r)
        for x in props:
            if x["det_a"] in por_id and x["det_b"] in por_id:
                pa, pb = _decorar(por_id[x["det_a"]], unidades, fuerzas, cargas), _decorar(por_id[x["det_b"]], unidades, fuerzas, cargas)
                for d_ in (pa, pb):
                    d_["nivel"] = revision_ia.nivel(d_)
                personas = [pa, pb] if x["mantener_id"] != x["det_b"] else [pb, pa]
                out["ia"].append({"clave": f"ia:{x['id']}", "personas": personas, "propuesta": dict(x, reglas=json.loads(x["reglas"] or "[]"))})
    for f in filas:
        d = None
        if f.get("fecha_cumplimiento") and f["fecha_cumplimiento"] < hoy.isoformat() and f"pena:{f['id']}" not in decididas:
            d = _decorar(f, unidades, fuerzas, cargas)
            out["pena_cumplida"].append({"clave": f"pena:{f['id']}", "personas": [d]})
        adulto = es_adulto(f, hoy)
        if adulto is False and f"menor:{f['id']}" not in decididas:
            out["menor"].append({"clave": f"menor:{f['id']}", "personas": [d or _decorar(f, unidades, fuerzas, cargas)]})
        if not f.get("dni") and not f.get("lpu_norm") and f"sindoc:{f['id']}" not in decididas:
            out["sin_documento"].append({"clave": f"sindoc:{f['id']}", "personas": [d or _decorar(f, unidades, fuerzas, cargas)]})
        fu = fuerza_de_unidad.get(f.get("unidad_id"))
        if fu and f.get("fuerza_id") and fu != f["fuerza_id"] and f"fuerza:{f['id']}" not in decididas:
            out["fuerza_distinta"].append({"clave": f"fuerza:{f['id']}", "personas": [d or _decorar(f, unidades, fuerzas, cargas)],
                                           "motivo": f"Informó {fuerzas.get(f['fuerza_id'], '?')}; la unidad está a cargo de {fuerzas.get(fu, '?')}."})
    return out


def decidir_alerta(clave, decision, usuario, nota="", mantener_id=None, campos=None):
    """Aplica lo que decidió el administrador. Decisiones:
       duplicado/mismo_nombre: «unificar» (se conserva `mantener_id`) o «distintas»;
       pena_cumplida: «baja» (egreso por pena cumplida) o «sigue» (sigue alojada: por ejemplo otra causa);
       menor / sin_documento: «revisado»."""
    quien = _quien(usuario)
    tipo = {"dup": "duplicado", "pena": "pena_cumplida", "menor": "menor", "sindoc": "sin_documento", "pend": "pendiente", "fuerza": "fuerza_distinta", "ia": "ia"}.get(clave.split(":")[0])
    if not tipo:
        raise ValueError("Alerta desconocida.")
    if tipo == "pendiente":
        return resolver_pendiente(int(clave.split(":")[1]), decision, usuario, campos or {})
    if tipo == "ia":  # propuesta de la IA: se decide como un posible duplicado y se cierra la propuesta
        import revision_ia
        prop = revision_ia.obtener(int(clave.split(":")[1]))
        if not prop or prop["estado"] not in revision_ia.ESTADOS_ABIERTOS:
            raise ValueError("La propuesta ya fue decidida.")
        if decision == "en_revision":  # Modificación 7: queda pendiente, con estado propio, hasta que alguien decida
            revision_ia.dejar_en_revision(prop["id"], usuario, nota)
            return None
        if decision not in ("unificar", "distintas"):
            raise ValueError("Elegí si son la misma persona o personas distintas.")
        mantener = mantener_id if mantener_id in (prop["det_a"], prop["det_b"]) else prop["mantener_id"]
        decidir_alerta(prop["clave"], decision, usuario, ((nota or "") + " (propuesta de la IA)").strip(), mantener)
        revision_ia.cerrar(prop["id"], decision, usuario)
        return None
    ids = [int(x) for x in clave.split(":")[1:]]
    if tipo == "duplicado":
        if decision == "unificar":
            if mantener_id not in ids:
                raise ValueError("Elegí cuál ficha se conserva.")
            otra = next(i for i in ids if i != mantener_id)
            unificar(mantener_id, otra, usuario)
        elif decision != "distintas":
            raise ValueError("Elegí si son la misma persona o personas distintas.")
    elif tipo == "pena_cumplida":
        if decision == "baja":
            _cambiar_estado(ids[0], 0, "Egreso: pena cumplida", usuario if isinstance(usuario, dict) else {"nombre": quien})
        elif decision != "sigue":
            raise ValueError("Elegí si se da de baja o sigue alojada.")
    elif decision != "revisado":
        raise ValueError("Decisión no válida.")
    with db._conexion() as con:
        _decidir(con, clave, tipo, decision, nota, quien)
        if tipo == "duplicado":  # los conflictos de carga de ese par quedan resueltos
            a, b = ids
            con.execute("""UPDATE detenidos_pendientes SET estado = 'RESUELTO', resuelto_por = ?, resuelto_en = ?, resolucion = ?
                           WHERE estado = 'PENDIENTE' AND tipo = 'conflicto' AND ((det_a = ? AND det_b = ?) OR (det_a = ? AND det_b = ?))""",
                        (quien, _ahora(), decision, a, b, b, a))


def resolver_pendiente(pid, accion, usuario, campos=None):
    """Resuelve una fila pendiente de una carga: «cargar» (con las correcciones), «sin_dni», «sin_fecha» o «descartar».
    Se carga como una carga chica vinculada a la original; si sigue con problemas, avisa y no guarda nada."""
    quien = _quien(usuario)
    with db._conexion() as con:
        x = con.execute("SELECT * FROM detenidos_pendientes WHERE id = ?", (pid,)).fetchone()
        l = con.execute("SELECT * FROM detenidos_listados WHERE id = ?", (x["listado_id"],)).fetchone() if x else None
    if not x or x["estado"] != "PENDIENTE":
        raise ValueError("Esa fila ya se resolvió.")
    if accion not in ("cargar", "sin_dni", "sin_fecha", "descartar"):
        raise ValueError("Elegí qué hacer con la fila.")
    if accion != "descartar":
        reg = json.loads(x["datos"] or "{}")
        dec = {"1": {"accion": "otra" if accion == "cargar" else accion, "campos": {k: v for k, v in (campos or {}).items() if v not in (None,)}}}
        fecha_l = l["fecha_listado"] if l else None
        p = previa([reg], fecha_l, l["fuerza_id"] if l else None, dec)
        if p["errores"]:
            raise ValueError(p["errores"][0]["mensaje"])
        aplicar_importacion([reg], usuario, origen="PENDIENTE", fecha_listado=fecha_l, fuerza_id=l["fuerza_id"] if l else None,
                            archivo=f"Fila {x['fila']} pendiente de la carga #{x['listado_id']}", decisiones=dec)
    with db._conexion() as con:
        con.execute("UPDATE detenidos_pendientes SET estado = 'RESUELTO', resuelto_por = ?, resuelto_en = ?, resolucion = ? WHERE id = ?",
                    (quien, _ahora(), accion, pid))


def unificar(mantener_id, absorber_id, usuario):
    """Dos fichas de la misma persona: la que se conserva completa los datos que le faltan con la otra (y toma el
    alojamiento si la otra viene de un listado más nuevo); la otra queda de baja «Unificada con #…». No se borra nada."""
    quien = _quien(usuario)
    a, b = obtener(mantener_id), obtener(absorber_id)
    if not a or not b:
        raise ValueError("La ficha no existe.")
    datos = {c: a.get(c) for c, _, _ in CAMPOS}
    for c, _, _ in CAMPOS:
        if datos.get(c) in (None, "") and b.get(c) not in (None, ""):
            datos[c] = b[c]
    b_mas_nuevo = (b.get("fecha_listado") or "") > (a.get("fecha_listado") or "")
    if b_mas_nuevo:
        for c in ALOJAMIENTO:
            datos[c] = b.get(c)
    d, errores, _ = validar(datos, mantener_id)
    errores = [e for e in errores if "Ya existe" not in e]  # el documento repetido es justamente el de la otra ficha
    if errores:
        raise ValueError(" ".join(errores))
    with db._conexion() as con:
        foto_a = dict(con.execute("SELECT * FROM detenidos WHERE id = ?", (mantener_id,)).fetchone())  # propuesta 33: para poder separarlas
        foto_b = dict(con.execute("SELECT * FROM detenidos WHERE id = ?", (absorber_id,)).fetchone())
        _escribir(con, d, quien, mantener_id, "UNIFICACION")
        if b_mas_nuevo:
            con.execute("UPDATE detenidos SET fecha_listado = ?, fuerza_id = ?, listado_id = ? WHERE id = ?",
                        (b.get("fecha_listado"), b.get("fuerza_id"), b.get("listado_id"), mantener_id))
        motivo = f"Unificada con #{mantener_id}"
        con.execute("UPDATE detenidos SET activo = 0, motivo_baja = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                    (motivo, quien, _ahora(), absorber_id))
        log = "INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha) VALUES (?,?,?,?,?,?,?)"
        if not a.get("activo") and b.get("activo"):  # se conserva la ficha del histórico pero la persona sigue alojada
            con.execute("UPDATE detenidos SET activo = 1, motivo_baja = NULL WHERE id = ?", (mantener_id,))
            con.execute(log, (mantener_id, "estado", a.get("motivo_baja") or "Baja", "Alojada (por la unificación)", quien, "UNIFICACION", _ahora()))
        con.execute(log, (absorber_id, "unificación", None, motivo, quien, "UNIFICACION", _ahora()))
        con.execute(log, (mantener_id, "unificación", None, f"Se le unió la ficha #{absorber_id}", quien, "UNIFICACION", _ahora()))
        from dgpdh.seguridad import auditoria
        from dgpdh.servicios import unificaciones
        uid = unificaciones.registrar(con, mantener_id=mantener_id, absorbida_id=absorber_id, antes_mantener=foto_a, antes_absorbida=foto_b,
                                      usuario=quien)
        auditoria.registrar(con, accion="Unificar fichas", entidad="detenido", entidad_id=mantener_id, antes={"absorbida": absorber_id},
                            despues={"unificacion_id": uid}, actor=auditoria.Actor.desde(usuario if isinstance(usuario, dict) else None),
                            origen="detenidos")


def _derivados(fila):
    return {"nombre_norm": norm_nombre(fila.get("nombre_completo")), "lpu_norm": norm_num(fila.get("lpu")) or None,
            **dict(zip(("apellido", "nombres"), separar_nombre(fila.get("nombre_completo") or ""), strict=True))}


def historial_alojamiento(det_id):
    """Propuesta 38: tramos de alojamiento (unidad, desde, hasta), del más nuevo al más viejo."""
    from dgpdh.dominio import alojamiento
    d = obtener(det_id)
    if not d:
        return []
    with db._conexion() as con:
        cambios = [dict(f) for f in con.execute("SELECT id, campo, antes, despues, fecha, listado_id FROM detenidos_cambios "
                                                "WHERE detenido_id = ? AND campo IN ('unidad_id', 'centro_otro')", (det_id,))]
        fechas = {f[0]: (f[1] or "")[:10] for f in con.execute("SELECT id, fecha_listado FROM detenidos_listados") if f[1]}
    return alojamiento.tramos(d, cambios, fechas)


def alojamiento_segun_base(texto):
    """Propuesta 38: para la prerrevisión. Por cada DNI de la nota que está en la base: (DNI, ficha, unidad del último listado)."""
    salida = []
    for dni in dict.fromkeys(m.group(1).replace(".", "") for m in re.finditer(r"\b(\d{1,2}\.?\d{3}\.?\d{3})\b", texto or "")):
        with db._conexion() as con:
            f = con.execute("SELECT id, nombre_completo, unidad_id, centro_otro, fecha_listado FROM detenidos WHERE dni = ? AND "
                            "COALESCE(motivo_baja, '') NOT LIKE 'Unificada con%' ORDER BY activo DESC, id DESC LIMIT 1", (dni,)).fetchone()
        if f and f["unidad_id"]:
            salida.append((dni, dict(f)))
    return salida


def calidad_por_fuerza():
    """Propuesta 42: tablero de calidad de los listados (por fuerza y trimestre), desde los informes de las cargas."""
    from dgpdh.dominio import calidad_listados
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("""SELECT l.fecha_listado, l.informe, f.nombre AS fuerza FROM detenidos_listados l
                                                 LEFT JOIN fuerzas f ON f.id = l.fuerza_id WHERE l.anulado_en IS NULL AND l.informe IS NOT NULL""")]
    cargas = []
    for f in filas:
        try:
            inc = (json.loads(f["informe"] or "{}") or {}).get("inconsistencias")
        except ValueError:
            inc = None
        cargas.append({"fuerza": f["fuerza"], "fecha_listado": f["fecha_listado"], "inconsistencias": inc})
    return calidad_listados.tablero(cargas)


def calidad_csv(periodos):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    tipos = list(TEMAS_INCONSISTENCIA)
    w.writerow(["Fuerza", "Trimestre", "Listados", "Filas", "% con problemas", "Tendencia (puntos)"] + [f"% {TEMAS_INCONSISTENCIA[t]}" for t in tipos])
    for p in periodos:
        w.writerow([p.fuerza, p.trimestre, p.listados, p.filas, p.pct, "" if p.tendencia is None else p.tendencia] + [p.pct_tipo(t) for t in tipos])
    w.writerow([])
    w.writerow(["Datos agregados de calidad de los listados recibidos. No contiene datos de personas."])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def historiales_de(ids, por_persona=8):
    """Modificación 7: los últimos cambios de varias fichas en UNA consulta (para «Ver datos completos» en la revisión)."""
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    salida = {}
    with db._conexion() as con:
        for f in con.execute("SELECT detenido_id, campo, antes, despues, usuario, origen, fecha FROM detenidos_cambios WHERE detenido_id IN (%s) "
                             "AND campo != '_listado' ORDER BY id DESC" % ",".join("?" * len(ids)), ids):
            lista = salida.setdefault(f["detenido_id"], [])
            if len(lista) < por_persona:
                lista.append(dict(f))
    return salida


def unificaciones_de(det_id):
    """Propuesta 33: las fichas que se unieron a esta (para «Separar fichas»)."""
    from dgpdh.servicios import unificaciones
    with db._conexion() as con:
        return unificaciones.de_ficha(con, det_id, [c for c, _, _ in CAMPOS])


def separar(unificacion_id, usuario, motivo, ip=None):
    """Propuesta 33: deshace una unificación. Devuelve el resultado (campos devueltos y campos que se conservaron)."""
    from dgpdh.seguridad import auditoria
    from dgpdh.servicios import unificaciones
    with db._conexion() as con:
        f = con.execute("SELECT mantener_id, absorbida_id FROM detenidos_unificaciones WHERE id = ?", (unificacion_id,)).fetchone()
        clave = clave_duplicado(f["mantener_id"], f["absorbida_id"]) if f else ""
        return unificaciones.separar(con, unificacion_id, campos=[c for c, _, _ in CAMPOS], derivados=_derivados,
                                     actor=auditoria.Actor.desde(usuario if isinstance(usuario, dict) else None, ip), motivo=motivo,
                                     clave_par=clave)


def decisiones_recientes(limite=50):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM detenidos_revision ORDER BY fecha DESC LIMIT ?", (limite,))]


def cantidad_pendientes():
    with db._conexion() as con:
        return con.execute("SELECT COUNT(*) FROM detenidos_pendientes WHERE estado = 'PENDIENTE'").fetchone()[0]


def cantidad_alertas(hoy=None):
    """Cuántas alertas pendientes hay (sin «sin DNI ni LPU», que es solo informativa). Liviana: no arma las fichas."""
    hoy = (hoy or date.today()).isoformat()
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT id, nombre_norm, dni, lpu_norm, fecha_cumplimiento, fecha_nacimiento, es_adulto_manual, "
                                              "fecha_listado, listado_id, unidad_id, centro_otro FROM detenidos WHERE activo = 1")]
        decididas = {r[0] for r in con.execute("SELECT clave FROM detenidos_revision")}
    n, grupos = 0, {}
    for f in filas:
        grupos.setdefault(f["nombre_norm"], []).append(f)
        if f.get("fecha_cumplimiento") and f["fecha_cumplimiento"] < hoy and f"pena:{f['id']}" not in decididas:
            n += 1
        if es_adulto(f) is False and f"menor:{f['id']}" not in decididas:
            n += 1
    for nombre, g in grupos.items():
        if len(g) > 1 and nombre:
            g.sort(key=lambda x: x["id"])
            for i, a in enumerate(g):
                for b in g[i + 1:]:
                    if clave_duplicado(a["id"], b["id"]) not in decididas and (a.get("dni") != b.get("dni") or a.get("lpu_norm") != b.get("lpu_norm")
                                                                               or not (a.get("dni") or a.get("lpu_norm"))) \
                            and not descarte_duplicado(a, b, date.fromisoformat(hoy)):
                        n += 1
    import revision_ia
    return n + cantidad_pendientes() + len(revision_ia.pendientes())


def fuerza_sugerida(registros):
    """La fuerza a cargo de la mayoría de las unidades que trae el listado (si las unidades tienen su fuerza cargada)."""
    from collections import Counter
    por_unidad = {u["id"]: u.get("fuerza_id") for u in db.listar_unidades(solo_activas=False)}
    cuenta = Counter(por_unidad.get(r.get("unidad_id")) for r in registros if r.get("unidad_id") and por_unidad.get(r.get("unidad_id")))
    if not cuenta:
        return None
    fid, n = cuenta.most_common(1)[0]
    con_unidad = sum(cuenta.values())
    if n * 2 < con_unidad:
        return None
    nombre = next((f["nombre"] for f in listar_fuerzas(False) if f["id"] == fid), "")
    return {"id": fid, "nombre": nombre, "n": n}


def ficha_a_conservar(ids):
    """Al unificar en lote: se conserva la ficha con LPU y el listado más nuevo (y si empatan, la más antigua)."""
    fichas = [obtener(i) for i in ids]
    fichas = [f for f in fichas if f]
    if not fichas:
        raise ValueError("Las fichas no existen.")
    fichas.sort(key=lambda f: (0 if f.get("lpu") else 1, -(int((f.get("fecha_listado") or "0000-00-00")[:10].replace("-", "")) ), f["id"]))
    return fichas[0]["id"]
