"""Metas de gestión por equipo (sobre la planilla de seguimiento de metas por trimestre de los equipos).

- La Dirección fija las metas ANUALES de cada equipo, indicador por indicador.
- Cada coordinación carga lo REALIZADO por su equipo, persona por persona, por mes o por trimestre.
- Las visitas a unidades, las audiencias en unidad y el ingreso a pabellones NO se cargan: salen solos de los viajes
  realizados y de lo que cada persona informa en su viaje (Autos PPN: audiencias y si ingresó a pabellones / sectores).
- El panel compara lo realizado con la meta y con lo esperado a la fecha (la meta anual repartida en el año).

Nada se borra: guardar en 0 deja el valor en 0 y queda quién y cuándo lo cargó.
"""
import math
from collections import Counter, defaultdict
from datetime import date, timedelta

import database as db

GRUPOS = [
    ("visitas", "Visitas a unidades"),
    ("monitoreos", "Monitoreos"),
    ("informes", "Informes y presentaciones"),
    ("reuniones", "Reuniones de trabajo"),
    ("requerimientos", "Requerimientos al SPF / FFSS"),
    ("comunicaciones", "Comunicaciones"),
]
# fuente «viajes»: se calcula desde Autos PPN; «carga»: la carga la coordinación
INDICADORES = [
    {"clave": "visitas_equipo", "nombre": "Visitas del equipo a unidades", "grupo": "visitas", "fuente": "viajes"},
    {"clave": "entrevistas", "nombre": "Audiencias / entrevistas a PPL en unidad", "grupo": "visitas", "fuente": "viajes"},
    {"clave": "videollamadas", "nombre": "Videollamadas con PPL", "grupo": "visitas", "fuente": "carga"},
    {"clave": "ingreso_pabellones", "nombre": "Ingreso a pabellones / sectores", "grupo": "visitas", "fuente": "viajes"},
    {"clave": "monitoreos", "nombre": "Monitoreos", "grupo": "monitoreos", "fuente": "carga"},
    {"clave": "informes_generales", "nombre": "Informes generales / temáticos", "grupo": "informes", "fuente": "carga"},
    {"clave": "informes_particulares", "nombre": "Informes particulares", "grupo": "informes", "fuente": "carga"},
    {"clave": "presentaciones_judiciales", "nombre": "Presentaciones judiciales", "grupo": "informes", "fuente": "carga"},
    {"clave": "reuniones_grupo", "nombre": "Reuniones al interior del grupo", "grupo": "reuniones", "fuente": "carga"},
    {"clave": "reuniones_externos", "nombre": "Reuniones con organismos externos", "grupo": "reuniones", "fuente": "carga"},
    {"clave": "reuniones_interareas", "nombre": "Reuniones interáreas PPN", "grupo": "reuniones", "fuente": "carga"},
    {"clave": "requerimientos_presenciales", "nombre": "Requerimientos presenciales", "grupo": "requerimientos", "fuente": "carga"},
    {"clave": "requerimientos_remotos", "nombre": "Requerimientos remotos", "grupo": "requerimientos", "fuente": "carga"},
    {"clave": "com_familiares", "nombre": "Comunicaciones con familiares", "grupo": "comunicaciones", "fuente": "carga"},
    {"clave": "com_judicial", "nombre": "Comunicaciones con poder judicial / defensas", "grupo": "comunicaciones", "fuente": "carga"},
    {"clave": "com_otros", "nombre": "Comunicaciones con otros organismos", "grupo": "comunicaciones", "fuente": "carga"},
]
POR_CLAVE = {i["clave"]: i for i in INDICADORES}
# Propuesta 82 (aclaración del usuario): las metas son grupales y se trasladan a cada integrante (en los indicadores por persona).
# Perfiles de indicadores: cada equipo tiene uno (por defecto «general», el de siempre). Los de salud vienen de sus planillas.
from dgpdh.dominio import metas_perfiles as _perfiles  # noqa: E402

PERFILES = _perfiles.PERFILES
for _i in _perfiles.indicadores_extra():
    POR_CLAVE.setdefault(_i["clave"], _i)
INDICADORES_POR_EQUIPO = {}  # {equipo_id: [claves]}: excepción manual (pruebas); lo normal es el perfil del equipo


def perfiles_equipos():
    def leer():
        try:
            with db._conexion() as con:
                return {f[0]: f[1] for f in con.execute("SELECT equipo_id, perfil FROM equipos_perfil_metas")}
        except Exception:
            return {}
    return _memo(("perfiles",), leer)


def perfil_de(equipo):
    eid = equipo["id"] if isinstance(equipo, dict) else equipo
    p = perfiles_equipos().get(eid, "general")
    return p if p in PERFILES else "general"


def guardar_perfil(equipo_id, perfil, usuario):
    """La Dirección elige el perfil de indicadores del equipo (queda en la auditoría)."""
    import auth
    from dgpdh.seguridad import auditoria as au
    if not auth.es_admin(usuario):
        raise PermissionError("El perfil de metas lo elige la Dirección.")
    if perfil not in PERFILES:
        raise ValueError("Perfil desconocido.")
    with db._conexion() as con:
        antes = con.execute("SELECT perfil FROM equipos_perfil_metas WHERE equipo_id = ?", (equipo_id,)).fetchone()
        if (antes["perfil"] if antes else "general") == perfil:
            return False
        con.execute("""INSERT INTO equipos_perfil_metas (equipo_id, perfil, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?)
                       ON CONFLICT(equipo_id) DO UPDATE SET perfil = excluded.perfil, actualizado_por = excluded.actualizado_por,
                       actualizado_en = excluded.actualizado_en""", (equipo_id, perfil, usuario["nombre"], db._ahora()))
        au.registrar(con, accion="Perfil de metas del equipo", entidad="equipo", entidad_id=equipo_id,
                     antes={"perfil": antes["perfil"] if antes else "general"}, despues={"perfil": perfil}, actor=au.Actor.desde(usuario))
    return True


def indicadores_de(equipo):
    """Indicadores que se siguen para ese equipo: los de su perfil (el general, si no tiene otro)."""
    eid = equipo["id"] if isinstance(equipo, dict) else equipo
    claves = INDICADORES_POR_EQUIPO.get(eid)
    if claves:
        return [POR_CLAVE[c] for c in claves if c in POR_CLAVE]
    return PERFILES[perfil_de(eid)]["indicadores"] or INDICADORES


def grupos_de(equipo):
    return PERFILES[perfil_de(equipo)]["grupos"] or GRUPOS


def dimension_de(i):
    return i.get("dimension", "persona")


def estado_individual(meta, hecho, frac):
    """La meta anual del equipo es también la meta de cada integrante: mismo cálculo que el del equipo."""
    esperado = math.floor(meta * frac)
    return {"meta": meta, "hecho": hecho, "esperado": esperado, "estado": estado(meta, hecho, esperado),
            "pct": min(100, round(100 * hecho / meta)) if meta else None}
DE_VIAJES = [i["clave"] for i in INDICADORES if i["fuente"] == "viajes"]
DE_CARGA = [i["clave"] for i in INDICADORES if i["fuente"] == "carga"]
DE_CARGA_PERSONA = set(DE_CARGA) | _perfiles.claves_por_persona_de_carga()
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
ROLES_FUERA = ("CHOFER",)  # todo el equipo cuenta (asesores, coordinación y demás roles con equipo), menos choferes


def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS metas_anuales (
        equipo_id INTEGER NOT NULL REFERENCES equipos(id), anio INTEGER NOT NULL, indicador TEXT NOT NULL,
        meta INTEGER NOT NULL DEFAULT 0, actualizado_por TEXT, actualizado_en TEXT,
        PRIMARY KEY (equipo_id, anio, indicador))""")
    # periodo: «M01»…«M12» (carga mensual) o «T1»…«T4» (carga trimestral); usuario_id = la persona del equipo
    con.execute("""CREATE TABLE IF NOT EXISTS metas_realizado (
        equipo_id INTEGER NOT NULL REFERENCES equipos(id), usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
        anio INTEGER NOT NULL, periodo TEXT NOT NULL, indicador TEXT NOT NULL, cantidad INTEGER NOT NULL DEFAULT 0,
        cargado_por TEXT, cargado_en TEXT,
        PRIMARY KEY (equipo_id, usuario_id, anio, periodo, indicador))""")
    import metas_importar
    metas_importar.inicializar(con)  # planillas de períodos anteriores



# ---------------------------------------------------------------- períodos
def trimestre_de_mes(m):
    return (m - 1) // 3 + 1


def meses_de(periodo):
    if periodo.startswith("M"):
        return [int(periodo[1:])]
    t = int(periodo[1:])
    return [3 * (t - 1) + 1, 3 * (t - 1) + 2, 3 * (t - 1) + 3]


def nombre_periodo(periodo, anio=None):
    sufijo = f" {anio}" if anio else ""
    if periodo.startswith("M"):
        return MESES[int(periodo[1:]) - 1] + sufijo
    return f"{periodo[1:]}.º trimestre" + sufijo


def validar_periodo(periodo):
    p = str(periodo or "").upper()
    if (p.startswith("M") and p[1:].isdigit() and 1 <= int(p[1:]) <= 12) or (p.startswith("T") and p[1:].isdigit() and 1 <= int(p[1:]) <= 4):
        return f"M{int(p[1:]):02d}" if p.startswith("M") else p
    raise ValueError("Elegí un mes o un trimestre.")


def periodo_actual(hoy=None, modo="M"):
    hoy = hoy or date.today()
    return f"M{hoy.month:02d}" if modo == "M" else f"T{trimestre_de_mes(hoy.month)}"


# ---------------------------------------------------------------- caché por pedido (pocas consultas por pantalla)
import contextvars  # noqa: E402
from contextlib import contextmanager  # noqa: E402

_CACHE = contextvars.ContextVar("metas_cache", default=None)


@contextmanager
def cache_de_pedido():
    """Mientras dura, las lecturas repetidas (usuarios, cargas, planillas) se hacen una sola vez."""
    if _CACHE.get() is not None:
        yield
        return
    marca = _CACHE.set({})
    try:
        yield
    finally:
        _CACHE.reset(marca)


def _memo(clave, fn):
    c = _CACHE.get()
    if c is None:
        return fn()
    if clave not in c:
        c[clave] = fn()
    return c[clave]


def _usuarios():
    return _memo(("usuarios",), db.listar_usuarios)


def _equipos():
    return _memo(("equipos",), db.listar_equipos)


def _importado(anio, equipo_id=None):
    import metas_importar
    todo = _memo(("importado", anio), lambda: metas_importar.importado(anio))
    return todo if equipo_id is None else [f for f in todo if f["equipo_id"] == equipo_id]


def _cubiertos(anio):
    def armar():
        out = set()
        for f in _importado(anio):
            for m in meses_de(f["periodo"]):
                out.add((f["equipo_id"], m, f["indicador"]))
        return out
    return _memo(("cubiertos", anio), armar)


# ---------------------------------------------------------------- equipos e integrantes
def integrantes(equipo_id):
    return [u for u in _usuarios() if u.get("equipo_id") == equipo_id and u.get("activo") and u["rol"] not in ROLES_FUERA]


def iniciales(nombre):
    partes = [p for p in (nombre or "").replace(",", " ").split() if p[:1].isalpha()]
    return "".join(p[0].upper() for p in partes[:3]) or "?"


def equipos_con_integrantes():
    ids = {u["equipo_id"] for u in db.listar_usuarios() if u.get("equipo_id") and u.get("activo") and u["rol"] not in ROLES_FUERA}
    return [e for e in db.listar_equipos() if e["id"] in ids]


def puede_ver(usuario):
    """La Dirección ve todos los equipos; cada integrante, el suyo."""
    import auth
    return bool(usuario) and (auth.es_admin(usuario) or (bool(usuario.get("equipo_id")) and usuario["rol"] not in ROLES_FUERA))


def editables(usuario, equipo_id):
    """A quiénes les puede cargar lo realizado: la Dirección y la coordinación, a todo el equipo; cada persona, lo suyo."""
    import auth
    if auth.es_admin(usuario) or (usuario["rol"] == "COORDINADOR" and usuario.get("equipo_id") == equipo_id):
        return {u["id"] for u in integrantes(equipo_id)}
    return {usuario["id"]} if usuario.get("equipo_id") == equipo_id else set()


def equipos_visibles(usuario):
    import auth
    if auth.es_admin(usuario):
        return equipos_con_integrantes()
    return [e for e in db.listar_equipos() if e["id"] == usuario.get("equipo_id")]


# ---------------------------------------------------------------- metas anuales (Dirección)
def metas_anuales(anio):
    """{equipo_id: {indicador: meta}}. Propuesta 82: la meta de visitas es una sola (la de acá); el tablero de Dirección la lee
    de esta tabla y la tabla vieja `metas_viajes` ya se copió (migración 0023) y no se usa."""
    out = defaultdict(dict)
    with db._conexion() as con:
        for f in con.execute("SELECT equipo_id, indicador, meta FROM metas_anuales WHERE anio = ?", (anio,)):
            out[f["equipo_id"]][f["indicador"]] = f["meta"]
    return dict(out)


def guardar_metas_anuales(anio, filas, usuario):
    """filas = {equipo_id: {indicador: meta}} (enteros ≥ 0). Devuelve cuántos valores se guardaron."""
    from dgpdh.seguridad import auditoria as au
    n = 0
    with db._conexion() as con:
        for eid, vals in filas.items():
            for clave, meta in vals.items():
                if clave not in POR_CLAVE:
                    continue
                previa = con.execute("SELECT meta FROM metas_anuales WHERE equipo_id = ? AND anio = ? AND indicador = ?", (eid, anio, clave)).fetchone()
                if previa is None or previa["meta"] != max(0, int(meta)):  # regla 4: todo cambio de una persona va a la auditoría
                    au.registrar(con, accion="Meta anual fijada", entidad="metas_anuales", entidad_id=f"{eid}-{anio}-{clave}",
                                 antes={"meta": previa["meta"]} if previa else None, despues={"meta": max(0, int(meta))}, actor=au.Actor.desde(usuario))
                con.execute("""INSERT INTO metas_anuales (equipo_id, anio, indicador, meta, actualizado_por, actualizado_en) VALUES (?,?,?,?,?,?)
                               ON CONFLICT(equipo_id, anio, indicador) DO UPDATE SET meta = excluded.meta,
                               actualizado_por = excluded.actualizado_por, actualizado_en = excluded.actualizado_en""",
                            (eid, anio, clave, max(0, int(meta)), usuario["nombre"], db._ahora()))
                n += 1
    return n


# ---------------------------------------------------------------- lo que sale de los viajes
def desde_viajes(anio, hoy=None, viajes=None):
    """Por equipo, persona y mes: visitas a unidades, audiencias e ingresos a pabellones, de los viajes ya realizados del año.
    Ingreso a pabellones: cada persona suma 1 por visita (día y unidad) en la que lo informó; el equipo, 1 por día y unidad
    en la que ingresó al menos una persona del equipo (igual que las visitas).
    Devuelve {"persona": {(usuario_id, mes, indicador): n}, "equipo": {(equipo_nombre_lower, mes, indicador): n}}."""
    import traslados as tr
    hoy = hoy or date.today()
    hasta = min(date(anio, 12, 31), hoy).isoformat()
    if hasta < f"{anio}-01-01":
        return {"persona": {}, "equipo": {}, "unidad": {}}
    if viajes is None:
        viajes = tr.listar_viajes(desde=f"{anio}-01-01", hasta=hasta, estado="CONFIRMADO", limite=100000)
    viajes = [v for v in viajes if v["fecha"] <= hasta and v["fecha"] >= f"{anio}-01-01"]
    persona, equipo = Counter(), Counter()
    visitas_persona, pabellones_persona = set(), set()
    for v in viajes:
        mes = int(v["fecha"][5:7])
        unidad = v.get("unidad_id") or (v.get("destino") or "").strip().lower()
        for p in v["participantes"]:
            uid = p.get("usuario_id")
            if uid:
                visitas_persona.add((uid, v["fecha"], unidad, mes))
                if p.get("ingreso_pabellones"):
                    pabellones_persona.add((uid, v["fecha"], unidad, mes))
                if p.get("entrevistas"):
                    persona[(uid, mes, "entrevistas")] += int(p["entrevistas"])
            eq = (p.get("equipo_nombre") or "").strip().lower()
            if eq and p.get("entrevistas"):
                equipo[(eq, mes, "entrevistas")] += int(p["entrevistas"])
    for uid, _, _, mes in visitas_persona:
        persona[(uid, mes, "visitas_equipo")] += 1
    unidad = Counter()
    for fecha, _, destino, eq in tr.viajes_a_unidad(viajes):  # una visita por día y unidad aunque vayan varios del equipo
        equipo[(eq.strip().lower(), int(fecha[5:7]), "visitas_equipo")] += 1
        unidad[(eq.strip().lower(), int(fecha[5:7]), destino, "visitas_equipo")] += 1
    for uid, _, _, mes in pabellones_persona:
        persona[(uid, mes, "ingreso_pabellones")] += 1
    con_ingreso = lambda v: [p for p in v["participantes"] if p.get("ingreso_pabellones")]
    for fecha, _, _, eq in tr.viajes_a_unidad(viajes, personas=con_ingreso):
        if eq != "(sin equipo)":
            equipo[(eq.strip().lower(), int(fecha[5:7]), "ingreso_pabellones")] += 1
    return {"persona": dict(persona), "equipo": dict(equipo), "unidad": dict(unidad)}


# ---------------------------------------------------------------- foto diaria de lo que sale de los viajes
INTERVALO_ACTUALIZAR_SEGUNDOS = 60


def _viajes_del_anio(anio, hoy, equipo=None):
    import traslados as tr
    hasta = min(date(anio, 12, 31), hoy).isoformat()
    if hasta < f"{anio}-01-01":
        return []
    if equipo is None:
        return tr.listar_viajes(desde=f"{anio}-01-01", hasta=hasta, estado="CONFIRMADO", limite=100000)
    vistos = {v["id"]: v for v in tr.listar_viajes(desde=f"{anio}-01-01", hasta=hasta, estado="CONFIRMADO", equipo_nombre=equipo["nombre"], limite=100000)}
    for u in integrantes(equipo["id"]):  # lo que alguien del equipo viajó con otro equipo también es suyo
        for v in tr.listar_viajes(desde=f"{anio}-01-01", hasta=hasta, estado="CONFIRMADO", usuario_id=u["id"], limite=100000):
            vistos.setdefault(v["id"], v)
    return list(vistos.values())


def armar_foto(anio, hoy=None, equipo=None, quien="Sistema"):
    """Calcula lo de los viajes y lo guarda: de todos los equipos (tarea diaria) o de uno solo («Actualizar ahora»)."""
    import json
    hoy = hoy or date.today()
    equipos = [equipo] if equipo else _equipos()
    viajes = _viajes_del_anio(anio, hoy, equipo)
    via = desde_viajes(anio, hoy, viajes=viajes)
    miembros = {u["id"]: u.get("equipo_id") for u in db.listar_usuarios()}
    calidad = calidad_visitas(anio, equipos, hoy, viajes=viajes)
    calidad_dir = calidad_visitas(anio, equipos, hoy, incluir_obstaculizaciones=True, viajes=viajes)
    ahora = db._ahora()
    with db._conexion() as con:
        for e in equipos:
            nombre = e["nombre"].strip().lower()
            datos = {"persona": [[uid, mes, c, n] for (uid, mes, c), n in via["persona"].items() if miembros.get(uid) == e["id"]],
                     "equipo": [[mes, c, n] for (eq, mes, c), n in via["equipo"].items() if eq == nombre],
                     "unidades": [[mes, d, c, n] for (eq, mes, d, c), n in via.get("unidad", {}).items() if eq == nombre],
                     "calidad": calidad.get(e["id"]), "calidad_dir": calidad_dir.get(e["id"])}
            con.execute("""INSERT INTO metas_foto (anio, equipo_id, generado_en, generado_por, datos) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(anio, equipo_id) DO UPDATE SET generado_en = excluded.generado_en, generado_por = excluded.generado_por,
                           datos = excluded.datos""", (anio, e["id"], ahora, quien, json.dumps(datos)))
    c = _CACHE.get()
    if c is not None:
        c.pop(("foto", anio), None)
    return len(equipos)


def _filas_foto(anio):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM metas_foto WHERE anio = ?", (anio,))]


def foto(anio, hoy=None):
    """{equipo_id: {"generado_en", "generado_por", "persona", "equipo", "calidad", "calidad_dir"}}. Si falta o es de otro día,
    se arma en el momento (primer uso del día) para todos los equipos."""
    import json
    hoy = hoy or date.today()

    def leer():
        filas = _filas_foto(anio)
        equipos = {e["id"] for e in _equipos()}
        al_dia = filas and all(_dia_local(f["generado_en"]) >= hoy for f in filas) and equipos <= {f["equipo_id"] for f in filas}
        if not al_dia:
            armar_foto(anio, hoy)
            filas = _filas_foto(anio)
        return {f["equipo_id"]: dict(json.loads(f["datos"]), generado_en=f["generado_en"], generado_por=f["generado_por"]) for f in filas}
    return _memo(("foto", anio), leer)


def _dia_local(iso):
    import fechas
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).astimezone(fechas.zona()).date()
    except (TypeError, ValueError):
        return date.min


def foto_viajes(anio, hoy=None):
    """Lo mismo que desde_viajes(), pero leído de la foto diaria."""
    equipos = {e["id"]: e["nombre"].strip().lower() for e in _equipos()}
    persona, equipo = {}, {}
    for eid, d in foto(anio, hoy).items():
        for uid, mes, c, n in d.get("persona", []):
            persona[(uid, mes, c)] = n
        for mes, c, n in d.get("equipo", []):
            if eid in equipos:
                equipo[(equipos[eid], mes, c)] = n
    return {"persona": persona, "equipo": equipo}


def actualizar_ahora(anio, equipo, usuario, hoy=None):
    """Botón «Actualizar ahora»: recalcula solo ese equipo y no más de una vez por minuto por equipo. Devuelve (hecho, mensaje)."""
    from datetime import datetime, timezone
    with db._conexion() as con:
        f = con.execute("SELECT generado_en FROM metas_foto WHERE anio = ? AND equipo_id = ?", (anio, equipo["id"])).fetchone()
    if f:
        try:
            pasaron = (datetime.now(timezone.utc) - datetime.fromisoformat(f["generado_en"])).total_seconds()
        except ValueError:
            pasaron = INTERVALO_ACTUALIZAR_SEGUNDOS
        if pasaron < INTERVALO_ACTUALIZAR_SEGUNDOS:
            return False, "Se actualizó hace menos de un minuto: esperá un momento y volvé a intentar."
    armar_foto(anio, hoy, equipo=equipo, quien=usuario["nombre"])
    return True, "Metas actualizadas con los viajes de hoy."


# ---------------------------------------------------------------- lo cargado por la coordinación
def cargado(anio, equipo_id=None):
    def leer():
        with db._conexion() as con:
            return [dict(f) for f in con.execute("SELECT * FROM metas_realizado WHERE anio = ? AND reemplazado_por IS NULL", (anio,))]
    if _CACHE.get() is not None:
        todo = _memo(("cargado", anio), leer)
        return todo if not equipo_id else [f for f in todo if f["equipo_id"] == equipo_id]
    sql, args = "SELECT * FROM metas_realizado WHERE anio = ? AND reemplazado_por IS NULL", [anio]
    if equipo_id:
        sql += " AND equipo_id = ?"; args.append(equipo_id)
    with db._conexion() as con:
        return [dict(f) for f in con.execute(sql, args)]


def cargado_dim(anio, equipo_id=None):
    """Lo realizado por equipo o por unidad/complejo (perfiles de salud)."""
    def leer():
        try:
            with db._conexion() as con:
                return [dict(f) for f in con.execute("SELECT * FROM metas_realizado_dim WHERE anio = ?", (anio,))]
        except Exception:
            return []
    todo = _memo(("dim", anio), leer)
    return todo if equipo_id is None else [f for f in todo if f["equipo_id"] == equipo_id]


def clave_unidad(unidad_id=None, texto=None):
    if unidad_id:
        return f"u:{int(unidad_id)}"
    t = " ".join(str(texto or "").split()).strip()
    return f"t:{t.upper()}" if t else ""


def guardar_dim(equipo_id, anio, periodo, valores, usuario, origen="carga"):
    """valores = {(indicador, dimension, etiqueta): cantidad}; dimension '' = número del equipo. Como la carga por persona:
    no se guardan ceros que nadie cargó, se registra el período cargado y todo va a la auditoría."""
    from dgpdh.seguridad import auditoria as au
    periodo = validar_periodo(periodo)
    validas = {i["clave"] for i in indicadores_de(equipo_id) if dimension_de(i) in ("equipo", "unidad") and i["fuente"] != "viajes"}
    n, cambios = 0, {}
    with db._conexion() as con:
        for (clave, dim, etiqueta), cant in valores.items():
            if clave not in validas:
                continue
            cant = max(0, int(cant))
            existe = con.execute("SELECT cantidad FROM metas_realizado_dim WHERE equipo_id=? AND anio=? AND periodo=? AND indicador=? AND dimension=?",
                                 (equipo_id, anio, periodo, clave, dim)).fetchone()
            if existe is None and not cant:
                continue
            if existe is not None and existe["cantidad"] == cant:
                continue
            con.execute("""INSERT INTO metas_realizado_dim (equipo_id, anio, periodo, indicador, dimension, etiqueta, cantidad, origen, cargado_por, cargado_en)
                           VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(equipo_id, anio, periodo, indicador, dimension) DO UPDATE SET
                           cantidad = excluded.cantidad, etiqueta = excluded.etiqueta, origen = excluded.origen,
                           cargado_por = excluded.cargado_por, cargado_en = excluded.cargado_en""",
                        (equipo_id, anio, periodo, clave, dim, etiqueta, cant, origen, usuario["nombre"], db._ahora()))
            cambios[f"{clave}|{etiqueta or 'equipo'}"] = [existe["cantidad"] if existe else None, cant]
            n += 1
        con.execute("""INSERT INTO metas_periodos_cargados (equipo_id, anio, periodo, cargado_por, cargado_en) VALUES (?,?,?,?,?)
                       ON CONFLICT(equipo_id, anio, periodo) DO UPDATE SET cargado_por = excluded.cargado_por, cargado_en = excluded.cargado_en""",
                    (equipo_id, anio, periodo, usuario["nombre"], db._ahora()))
        if cambios:
            au.registrar(con, accion="Metas: carga por equipo o unidad", entidad="metas_realizado_dim", entidad_id=f"{equipo_id}-{anio}-{periodo}",
                         despues=cambios, actor=au.Actor.desde(usuario), origen=origen)
    c = _CACHE.get()
    if c is not None:
        c.pop(("dim", anio), None)
    return n


def propuesta_notas(equipo_id, anio, periodo):
    """Casos trabajados por notas, por unidad: lo propone el sistema con las notas del equipo (alcance colectivo → colectivos;
    el resto, individuales) y la coordinación lo confirma o corrige. {(indicador, dimension): (etiqueta, n)}."""
    import indicadores as ind
    meses = meses_de(validar_periodo(periodo))
    desde, hasta = f"{anio}-{meses[0]:02d}-01", f"{anio}-{meses[-1]:02d}-31"
    with db._conexion() as con:
        try:
            alcance = {f[0]: f[1] for f in con.execute("SELECT id, clasif_alcance FROM notas WHERE equipo_id = ?", (equipo_id,))}
        except Exception:
            alcance = {}
    out = {}
    for n in ind.notas_periodo(desde, hasta):
        if n.get("equipo_id") != equipo_id:
            continue
        clave = "med_casos_colectivos" if alcance.get(n["id"]) == "COLECTIVO" else "med_casos_individuales"
        for uid, nombre in n["unidades"][:1] or []:
            k = (clave, clave_unidad(uid))
            out[k] = (nombre, out.get(k, (nombre, 0))[1] + 1)
    return out


def desglose_unidades(anio, equipo, hoy=None):
    """{indicador: [{unidad, trimestres: [T1..T4], total}]} para los indicadores por unidad del perfil del equipo."""
    lista = [i for i in indicadores_de(equipo) if dimension_de(i) == "unidad"]
    if not lista:
        return {}
    out = {}
    for i in lista:
        filas = defaultdict(lambda: [0, 0, 0, 0])
        if i["fuente"] == "viajes":
            for mes, destino, clave, n in foto(anio, hoy).get(equipo["id"], {}).get("unidades", []):
                if clave == i["clave"]:
                    filas[destino][trimestre_de_mes(mes) - 1] += n
        else:
            for f in cargado_dim(anio, equipo["id"]):
                if f["indicador"] == i["clave"] and f["dimension"]:
                    filas[f["etiqueta"] or f["dimension"]][trimestre_de_mes(meses_de(f["periodo"])[0]) - 1] += f["cantidad"]
        out[i["clave"]] = [{"unidad": k, "trimestres": v, "total": sum(v)} for k, v in sorted(filas.items(), key=lambda kv: (-sum(kv[1]), kv[0]))]
    return out


def modo_del_trimestre(equipo_id, anio, t):
    """«M» si ese trimestre se cargó por mes, «T» si por trimestre, None si todavía no se cargó."""
    meses = {f"M{m:02d}" for m in meses_de(f"T{t}")}
    usados = {f["periodo"] for f in cargado(anio, equipo_id) + _importado(anio, equipo_id)
              if f["periodo"] in meses or f["periodo"] == f"T{t}"}
    if not usados:
        return None
    return "T" if f"T{t}" in usados else "M"


def guardar_carga(equipo_id, anio, periodo, valores, usuario, permitidos=None):
    """valores = {(usuario_id, indicador): cantidad}. Un trimestre se carga por mes o entero, no las dos cosas
    (si no, se sumaría dos veces)."""
    periodo = validar_periodo(periodo)
    t = trimestre_de_mes(meses_de(periodo)[0])
    modo = modo_del_trimestre(equipo_id, anio, t)
    if modo and modo != periodo[0]:
        raise ValueError(f"El {t}.º trimestre ya se cargó {'por mes' if modo == 'M' else 'entero'}: seguí cargándolo de esa forma "
                         "(así no se cuenta dos veces).")
    miembros = {u["id"] for u in integrantes(equipo_id)}
    if permitidos is not None:
        miembros &= set(permitidos)
    cubiertos = _cubiertos(anio)
    n = 0
    with db._conexion() as con:
        for (uid, clave), cant in valores.items():
            if clave not in DE_CARGA_PERSONA or uid not in miembros:
                continue
            if any((equipo_id, m, clave) in cubiertos for m in meses_de(periodo)):
                continue  # ese indicador del período vino de una planilla importada (se corrige anulando o reimportando)
            existe = con.execute("SELECT cantidad FROM metas_realizado WHERE equipo_id=? AND usuario_id=? AND anio=? AND periodo=? AND indicador=?",
                                 (equipo_id, uid, anio, periodo, clave)).fetchone()
            if existe is None and not cant:
                continue  # no se guardan ceros que nadie cargó
            con.execute("""INSERT INTO metas_realizado (equipo_id, usuario_id, anio, periodo, indicador, cantidad, cargado_por, cargado_en)
                           VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(equipo_id, usuario_id, anio, periodo, indicador) DO UPDATE SET
                           cantidad = excluded.cantidad, cargado_por = excluded.cargado_por, cargado_en = excluded.cargado_en""",
                        (equipo_id, uid, anio, periodo, clave, max(0, int(cant)), usuario["nombre"], db._ahora()))
            n += 1
        try:  # propuesta 91: «se cargó» (aunque sea todo en 0) es distinto de «no se cargó»
            con.execute("""INSERT INTO metas_periodos_cargados (equipo_id, anio, periodo, cargado_por, cargado_en) VALUES (?,?,?,?,?)
                           ON CONFLICT(equipo_id, anio, periodo) DO UPDATE SET cargado_por = excluded.cargado_por, cargado_en = excluded.cargado_en""",
                        (equipo_id, anio, periodo, usuario["nombre"], db._ahora()))
        except Exception:
            import logging
            logging.exception("No se pudo registrar el período cargado")
    return n


def grilla_carga(equipo_id, anio, periodo, hoy=None):
    """Para la pantalla de carga: integrantes, valores cargados del período y lo que sale de los viajes."""
    periodo = validar_periodo(periodo)
    gente = integrantes(equipo_id)
    meses = meses_de(periodo)
    vals = {(f["usuario_id"], f["indicador"]): f["cantidad"] for f in cargado(anio, equipo_id) if f["periodo"] == periodo}
    ultimo = max((f["cargado_en"] or "" for f in cargado(anio, equipo_id) if f["periodo"] == periodo), default="")
    quien = next((f["cargado_por"] for f in cargado(anio, equipo_id) if f["periodo"] == periodo and f["cargado_en"] == ultimo), "")
    via = foto_viajes(anio, hoy)
    eq = next((e for e in db.listar_equipos() if e["id"] == equipo_id), {"nombre": ""})
    auto = {}
    for clave in DE_VIAJES:
        for u in gente:
            auto[(u["id"], clave)] = sum(via["persona"].get((u["id"], m, clave), 0) for m in meses)
        auto[("equipo", clave)] = sum(via["equipo"].get((eq["nombre"].lower(), m, clave), 0) for m in meses)
    imp = [f for f in _importado(anio, equipo_id) if any(m in meses for m in meses_de(f["periodo"]))]
    planilla = defaultdict(int)
    for f in imp:
        planilla[(f["usuario_id"], f["indicador"])] += f["cantidad"]
        planilla[("equipo", f["indicador"])] += f["cantidad"]
    de_planilla = {f["indicador"] for f in imp}
    t = trimestre_de_mes(meses[0])
    return {"integrantes": [dict(u, iniciales=iniciales(u["nombre"])) for u in gente], "valores": vals, "auto": auto,
            "planilla": dict(planilla), "de_planilla": de_planilla,
            "cargas_planilla": sorted({f["carga_id"] for f in imp}),
            "periodo": periodo, "nombre_periodo": nombre_periodo(periodo, anio), "modo_trimestre": modo_del_trimestre(equipo_id, anio, t),
            "ultima_carga": ultimo, "cargado_por": quien, "equipo": eq}


# ---------------------------------------------------------------- seguimiento
def _fraccion_anio(anio, hoy):
    if hoy.year < anio:
        return 0.0
    if hoy.year > anio:
        return 1.0
    ini, fin = date(anio, 1, 1), date(anio, 12, 31)
    return ((hoy - ini).days + 1) / ((fin - ini).days + 1)


def realizado(anio, hoy=None):
    """{equipo_id: {indicador: [T1, T2, T3, T4]}} y {equipo_id: {(usuario_id, indicador): total del año}}.
    Lo que sale de los viajes viene de la foto diaria (foto_viajes); lo cargado a mano, de la base en el momento."""
    hoy = hoy or date.today()
    equipos = _equipos()
    por_nombre = {e["nombre"].strip().lower(): e["id"] for e in equipos}
    tri = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    persona = defaultdict(Counter)
    for f in cargado(anio):
        t = trimestre_de_mes(meses_de(f["periodo"])[0])
        tri[f["equipo_id"]][f["indicador"]][t - 1] += f["cantidad"]
        persona[f["equipo_id"]][(f["usuario_id"], f["indicador"])] += f["cantidad"]
    cubiertos = _cubiertos(anio)
    for f in _importado(anio):  # planillas de períodos anteriores
        t = trimestre_de_mes(meses_de(f["periodo"])[0])
        tri[f["equipo_id"]][f["indicador"]][t - 1] += f["cantidad"]
        clave_p = f["usuario_id"] or (("equipo",) if f["sin_asignar"] else ("sigla", f["sigla"]))
        persona[f["equipo_id"]][(clave_p, f["indicador"])] += f["cantidad"]
    via = foto_viajes(anio, hoy)
    for (eqn, mes, clave), n in via["equipo"].items():
        eid = por_nombre.get(eqn)
        if eid and (eid, mes, clave) not in cubiertos:  # en un mes importado manda la planilla
            tri[eid][clave][trimestre_de_mes(mes) - 1] += n
    # perfiles de salud: lo cargado por equipo o por unidad suma al equipo; «viajes_o_carga» toma lo cargado solo en los meses
    # en que los viajes no tienen dato
    por_id = {e["id"]: e["nombre"].strip().lower() for e in equipos}
    for f in cargado_dim(anio):
        i = next((x for x in indicadores_de(f["equipo_id"]) if x["clave"] == f["indicador"]), None)
        if not i or i["fuente"] == "viajes":
            continue
        meses_f = meses_de(f["periodo"])
        if i["fuente"] == "viajes_o_carga" and any(via["equipo"].get((por_id.get(f["equipo_id"]), m, i["clave"]), 0) for m in meses_f):
            continue
        tri[f["equipo_id"]][f["indicador"]][trimestre_de_mes(meses_f[0]) - 1] += f["cantidad"]
    miembros = {u["id"]: u.get("equipo_id") for u in _usuarios()}
    for (uid, mes, clave), n in via["persona"].items():
        if miembros.get(uid) and (miembros[uid], mes, clave) not in cubiertos:
            persona[miembros[uid]][(uid, clave)] += n
    return tri, persona


def estado(meta, hecho, esperado):
    if not meta:
        return "sin_meta"
    if hecho >= meta:
        return "cumplida"
    if hecho >= esperado:
        return "en_camino"
    return "atrasada"


def periodos_cargados(anio, equipo_id):
    """Propuesta 91: períodos que la coordinación cargó (aunque fuera todo en 0) o que vinieron de una planilla."""
    def leer():
        with db._conexion() as con:
            try:
                return [(f[0], f[1]) for f in con.execute("SELECT equipo_id, periodo FROM metas_periodos_cargados WHERE anio = ?", (anio,))]
            except Exception:
                return []
    p = {per for eid, per in _memo(("periodos", anio), leer) if eid == equipo_id}
    p |= {f["periodo"] for f in cargado(anio, equipo_id)}
    p |= {f["periodo"] for f in cargado_dim(anio, equipo_id)}
    p |= {f["periodo"] for f in _importado(anio, equipo_id)}
    return p


def _feriados(anio):
    import traslados as tr
    return tr.feriados_entre(date(anio, 1, 1), date(anio, 12, 31))


def _ausencias(anio):
    """{usuario_id: {fechas}} de licencias y ausencias importadas de la planilla de Personal (presentismo)."""
    def leer():
        out = defaultdict(set)
        try:
            with db._conexion() as con:
                for uid, f in con.execute("""SELECT usuario_id, fecha FROM personal_novedades WHERE usuario_id IS NOT NULL
                                             AND clase IN ('LICENCIA', 'AUSENCIA') AND fecha BETWEEN ? AND ?""", (f"{anio}-01-01", f"{anio}-12-31")):
                    try:
                        out[uid].add(date.fromisoformat(str(f)[:10]))
                    except ValueError:
                        continue
        except Exception:
            return {}
        return dict(out)
    return _memo(("ausencias", anio), leer)


def calidad_foto(anio, equipos, incluir_obstaculizaciones=False, hoy=None):
    """Propuesta 89 leída de la foto diaria."""
    from dgpdh.dominio import metas_calculo as mc
    f = foto(anio, hoy)
    clave = "calidad_dir" if incluir_obstaculizaciones else "calidad"
    return {e["id"]: (f.get(e["id"], {}).get(clave) or mc.calidad([])) for e in equipos}


def calidad_visitas(anio, equipos, hoy=None, incluir_obstaculizaciones=False, viajes=None):
    """Propuesta 89: por equipo, proporciones de calidad de sus visitas del año (sin carga nueva)."""
    import traslados as tr
    from dgpdh.dominio import metas_calculo as mc
    from dgpdh.servicios import ingreso_unidades
    hoy = hoy or date.today()
    hasta = min(date(anio, 12, 31), hoy - timedelta(days=1)).isoformat()
    if hasta < f"{anio}-01-01":
        return {e["id"]: mc.calidad([]) for e in equipos}
    if viajes is None:
        viajes = tr.listar_viajes(desde=f"{anio}-01-01", hasta=hasta, estado="CONFIRMADO", limite=100000)
    viajes = [v for v in viajes if v["fecha"] <= hasta]
    with db._conexion() as con:
        obst = ingreso_unidades.visitas_con_obstaculo(con, f"{anio}-01-01", hasta, hoy, incluir_obstaculizaciones)
    por_eq = defaultdict(dict)  # equipo_lower -> {(fecha, unidad): [pabellones, audiencias, informada, obst]}
    for v in viajes:
        clave = (v["fecha"], v.get("unidad_id") or (v.get("destino") or "").strip().lower())
        for p in v["participantes"]:
            eq = (p.get("equipo_nombre") or "").strip().lower()
            if not eq:
                continue
            d = por_eq[eq].setdefault(clave, [False, 0, False, isinstance(clave[1], int) and (clave[1], v["fecha"]) in obst])
            d[0] = d[0] or bool(p.get("ingreso_pabellones"))
            if p.get("entrevistas") is not None:
                d[1] += int(p["entrevistas"]); d[2] = True
    return {e["id"]: mc.calidad([mc.Visita(*x) for x in por_eq.get(e["nombre"].strip().lower(), {}).values()]) for e in equipos}


def seguimiento(anio, equipos, hoy=None):
    """Por equipo e indicador: meta anual, realizado por trimestre y en el año, % y estado frente a lo esperado a hoy."""
    with cache_de_pedido():
        return _seguimiento(anio, equipos, hoy)


def _seguimiento(anio, equipos, hoy=None):
    from dgpdh.dominio import metas_calculo as mc
    hoy = hoy or date.today()
    metas = metas_anuales(anio)
    tri, persona = realizado(anio, hoy)
    frac = _fraccion_anio(anio, hoy)
    feriados = _feriados(anio)
    salida = []
    for e in equipos:
        filas, resumen = [], Counter()
        completo = mc.completitud(anio, hoy, periodos_cargados(anio, e["id"]))  # propuesta 91
        lista = indicadores_de(e)
        for i in lista:
            meta = metas.get(e["id"], {}).get(i["clave"], 0)
            por_t = tri[e["id"]][i["clave"]]
            hecho = sum(por_t)
            esperado = math.floor(meta * frac)
            est = mc.estado_con_carga(estado(meta, hecho, esperado), i["fuente"], hecho, completo)
            resumen[est] += 1
            pr = mc.proyectar(meta, hecho, anio, hoy, feriados)  # propuesta 83
            filas.append(dict(i, dimension=dimension_de(i), meta=meta, trimestres=list(por_t), hecho=hecho, esperado=esperado, estado=est,
                              pct=min(100, round(100 * hecho / meta)) if meta else None, meta_trimestre=math.ceil(meta / 4) if meta else 0,
                              esperado_habil=pr.esperado_habil, proyeccion=pr.proyeccion, pct_proyeccion=pr.pct_proyeccion, por_mes=pr.por_mes))
        gente = [dict(u, iniciales=iniciales(u["nombre"])) for u in integrantes(e["id"])]
        metas_eq = metas.get(e["id"], {})
        ausencias = _ausencias(anio)  # 93: del cruce con la planilla de Personal (licencias y ausencias)
        por_persona = []
        for u in gente:
            vals = {i["clave"]: persona[e["id"]].get((u["id"], i["clave"]), 0) for i in lista}
            ind = {i["clave"]: estado_individual(metas_eq.get(i["clave"], 0), vals[i["clave"]], frac)
                   for i in lista if dimension_de(i) == "persona"}  # la meta del equipo es la de cada persona, solo en lo que es por persona
            for d in ind.values():
                d["esperado_ajustado"] = mc.esperado_ajustado(d["meta"], anio, hoy, feriados, ausencias.get(u["id"], ()))
            por_persona.append({"usuario": u, "valores": vals, "indicadores": ind, "dias_ausente": len(ausencias.get(u["id"], ())),
                                "resumen": dict(Counter(x["estado"] for x in ind.values() if x["estado"] != "sin_meta"))})
        # de las planillas importadas: personas que ya no están (por sigla), usuarios de otro equipo y lo del equipo sin asignar
        ids_gente = {u["id"] for u in gente}
        otros = sorted({k[0] for k in persona[e["id"]] if k[0] not in ids_gente}, key=str)
        for k in otros:
            if isinstance(k, int):
                uu = db.obtener_usuario(k) or {"nombre": f"Usuario #{k}"}
                etiqueta = {"id": k, "nombre": uu["nombre"] + " (otro equipo hoy)", "iniciales": iniciales(uu["nombre"])}
            elif k == ("equipo",):
                etiqueta = {"id": None, "nombre": "Equipo, sin asignar (planilla)", "iniciales": "EQ"}
            else:
                etiqueta = {"id": None, "nombre": f"Persona anterior ({k[1]})", "iniciales": k[1]}
            vals = {i["clave"]: persona[e["id"]].get((k, i["clave"]), 0) for i in lista}
            if any(vals.values()):  # personas anteriores o «sin asignar» de planillas: sin estado individual
                por_persona.append({"usuario": etiqueta, "valores": vals, "anterior": True, "indicadores": {}, "resumen": {}})
        planilla_eq = {c for (eid, _, c) in _cubiertos(anio) if eid == e["id"]}
        for f in filas:
            f["segun_planilla"] = f["clave"] in planilla_eq
        con_meta = [f for f in filas if f["meta"]]
        desglose = desglose_unidades(anio, e, hoy)
        for f in filas:
            f["unidades"] = desglose.get(f["clave"]) if f["dimension"] == "unidad" else None
        hay_persona = any(f["dimension"] == "persona" for f in filas)
        salida.append({"equipo": e, "filas": filas, "resumen": dict(resumen), "por_persona": por_persona if hay_persona else [],
                       "perfil": perfil_de(e), "perfil_nombre": PERFILES[perfil_de(e)]["nombre"], "grupos": grupos_de(e),
                       "filas_persona": [f for f in filas if f["dimension"] == "persona"],
                       "completitud": {"nivel": completo.nivel, "faltan": [MESES[m - 1] for m in completo.faltan],
                                       "cerrados": len(completo.cerrados), "cargados": len(completo.cargados)},
                       "avance": round(sum(min(100, f["pct"]) for f in con_meta) / len(con_meta)) if con_meta else None,
                       "trimestres_cargados": [modo_del_trimestre(e["id"], anio, t) for t in (1, 2, 3, 4)]})
    return {"anio": anio, "equipos": salida, "fraccion": round(100 * frac), "grupos": GRUPOS}


# ---------------------------------------------------------------- 85) tendencia y 94) comparación normalizada
def serie_mensual(anio, equipo, hoy=None):
    """{indicador: [enero … diciembre]} del equipo. Lo cargado por trimestre entero se ubica en el último mes del trimestre."""
    hoy = hoy or date.today()
    with cache_de_pedido():
        out = defaultdict(lambda: [0] * 12)
        cub = _cubiertos(anio)
        for f in cargado(anio, equipo["id"]) + cargado_dim(anio, equipo["id"]):
            m = meses_de(f["periodo"])[-1]
            if (equipo["id"], m, f["indicador"]) not in cub:
                out[f["indicador"]][m - 1] += f["cantidad"]
        for f in _importado(anio, equipo["id"]):
            out[f["indicador"]][meses_de(f["periodo"])[-1] - 1] += f["cantidad"]
        nombre = equipo["nombre"].strip().lower()
        for (eq, mes, clave), n in foto_viajes(anio, hoy)["equipo"].items():
            if eq == nombre and (equipo["id"], mes, clave) not in cub:
                out[clave][mes - 1] += n
    return dict(out)


def variacion(actual, anterior):
    """% de cambio (None si no hay con qué comparar)."""
    if not anterior:
        return None
    return round(100 * (actual - anterior) / anterior)


def sparkline(valores, ancho=96, alto=24):
    """Puntos de una mini línea SVG (se dibuja en el servidor, sin librerías)."""
    if not valores:
        return ""
    tope = max(valores) or 1
    paso = ancho / max(1, len(valores) - 1)
    return " ".join(f"{round(i * paso, 1)},{round(alto - 2 - (alto - 4) * v / tope, 1)}" for i, v in enumerate(valores))


def tendencias(anio, equipo, filas, hoy=None):
    """Para cada indicador: la mini línea de los meses ya transcurridos, la variación contra el trimestre anterior y contra el
    mismo trimestre del año anterior (si hay datos)."""
    hoy = hoy or date.today()
    serie = serie_mensual(anio, equipo, hoy)
    ult_mes = 12 if anio < hoy.year else (hoy.month if anio == hoy.year else 0)
    t = max(1, trimestre_de_mes(max(1, ult_mes)))
    previo = {f["clave"]: f["trimestres"] for f in seguimiento(anio - 1, [equipo], hoy)["equipos"][0]["filas"]} if ult_mes else {}
    out = {}
    for f in filas:
        valores = serie.get(f["clave"], [0] * 12)[:ult_mes]
        tri = f["trimestres"]
        out[f["clave"]] = {"puntos": sparkline(valores), "valores": valores,
                           "vs_trimestre": variacion(tri[t - 1], tri[t - 2]) if t > 1 else None,
                           "vs_anio": variacion(tri[t - 1], previo.get(f["clave"], [0, 0, 0, 0])[t - 1]), "trimestre": t}
    return out


def comparacion(seg_equipos, minimo=3):
    """Propuesta 94: visitas, audiencias e informes de cada equipo, por integrante y por unidad a cargo. Las celdas con menos
    de `minimo` casos se marcan como poco representativas."""
    with db._conexion() as con:
        a_cargo = Counter(f[0] for f in con.execute("SELECT equipo_id FROM equipo_unidades"))
    out = []
    for x in seg_equipos:
        f = {r["clave"]: r["hecho"] for r in x["filas"]}
        valores = {"Visitas": f.get("visitas_equipo", 0), "Audiencias": f.get("entrevistas", 0),
                   "Informes": f.get("informes_generales", 0) + f.get("informes_particulares", 0)}
        gente = len(integrantes(x["equipo"]["id"]))
        unidades = a_cargo.get(x["equipo"]["id"], 0)
        out.append({"equipo": x["equipo"], "integrantes": gente, "unidades": unidades, "valores": valores,
                    "por_integrante": {k: round(v / gente, 1) if gente else None for k, v in valores.items()},
                    "por_unidad": {k: round(v / unidades, 1) if unidades else None for k, v in valores.items()},
                    "poco": {k: v < minimo for k, v in valores.items()}})
    return out


# ---------------------------------------------------------------- avisos de fondo (propuestas 84 y 91)
def _coordinacion_de(equipo_id):
    return [u["id"] for u in db.listar_usuarios() if u.get("activo") and u["rol"] == "COORDINADOR" and u.get("equipo_id") == equipo_id]


def avisos_de_fondo(hoy=None):
    """Una vez por día, desde el mantenimiento de fondo:
      84) a la coordinación, cuando un indicador de su equipo PASA a «atrasada» (vuelve a avisar solo si se recupera y cae
          otra vez); los lunes, un resumen de la semana a la Dirección;
      91) los primeros 3 días hábiles del mes, a la coordinación que no cargó el mes anterior.
    Devuelve cuántos avisos mandó. Nada sale por correo: son avisos internos."""
    import auth
    from dgpdh.dominio import calendario as cal
    from dgpdh.servicios import metas_avisos
    hoy = hoy or date.today()
    clave = f"metas_avisos_{hoy.isoformat()}"
    if db.obtener_ajuste(clave):
        return 0
    db.guardar_ajuste(clave, "1")
    try:  # foto diaria de lo que sale de los viajes (la primera pasada del día, a la madrugada)
        armar_foto(hoy.year, hoy)
    except Exception:
        import logging
        logging.exception("No se pudo armar la foto diaria de metas")
    enviados = 0
    equipos = equipos_con_integrantes()
    if not equipos:
        return 0
    anio = hoy.year
    seg = seguimiento(anio, equipos, hoy)
    estados = [(x["equipo"]["id"], f["clave"], f["estado"]) for x in seg["equipos"] for f in x["filas"] if f["meta"]]
    with db._conexion() as con:
        cambios = metas_avisos.actualizar_estados(con, anio, estados, hoy)
    por_id = {x["equipo"]["id"]: x for x in seg["equipos"]}
    for eid, clave_i in cambios:
        f = next(f for f in por_id[eid]["filas"] if f["clave"] == clave_i)
        destinos = _coordinacion_de(eid)
        if destinos:
            db.notificar_usuarios(destinos, f"Metas: «{f['nombre']}» de {por_id[eid]['equipo']['nombre']} pasó a atrasada "
                                  f"(llevan {f['hecho']}; lo esperado a hoy es {f['esperado']} de una meta de {f['meta']}).",
                                  f"/metas?anio={anio}&equipo_id={eid}")
            enviados += 1
    if hoy.weekday() == 0:
        with db._conexion() as con:
            semana = metas_avisos.atrasadas_de_la_semana(con, anio, hoy)
        if semana:
            nombres = {e["id"]: e["nombre"] for e in equipos}
            lineas = [f"{nombres.get(e, e)}: {POR_CLAVE.get(i, {}).get('nombre', i)}" for e, i, _ in semana[:8]]
            dire = [u["id"] for u in db.listar_usuarios() if u.get("activo") and auth.es_admin(u)]
            db.notificar_usuarios(dire, f"Resumen semanal de metas: {len(semana)} indicador(es) pasaron a atrasada — " + "; ".join(lineas)
                                  + (" …" if len(semana) > 8 else "") + ".", f"/metas?anio={anio}")
            enviados += 1
    fer = _feriados(anio)
    if hoy in cal.primeros_habiles_del_mes(hoy.year, hoy.month, 3, fer):
        previo = metas_avisos.mes_anterior(hoy)
        metas_previas = metas_anuales(previo.year)
        for e in equipos:
            if not any(metas_previas.get(e["id"], {}).get(k) for k in DE_CARGA):
                continue  # sin metas de carga manual no hace falta recordar
            marca = f"metas_recordatorio_{e['id']}_{previo.strftime('%Y-%m')}"
            if db.obtener_ajuste(marca) or not metas_avisos.falta_cargar_mes(periodos_cargados(previo.year, e["id"]), previo.month):
                continue
            destinos = _coordinacion_de(e["id"])
            if destinos:
                db.guardar_ajuste(marca, "1")
                db.notificar_usuarios(destinos, f"Metas: falta cargar lo realizado por {e['nombre']} en {MESES[previo.month - 1].lower()} "
                                      f"{previo.year}. Si no hubo actividad, cargalo en 0 para que no figure «sin carga».",
                                      f"/metas/carga?anio={previo.year}&periodo=M{previo.month:02d}&equipo_id={e['id']}")
                enviados += 1
    return enviados


# ---------------------------------------------------------------- planilla (mismo formato que la de los equipos)
def exportar_planilla(anio, equipo, hoy=None):
    """Un libro con una hoja por trimestre: indicadores en filas; cada mes con una columna por persona (iniciales) y su
    total; al final el total del trimestre. Más una hoja de resumen mensual y otra con metas y avance."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    hoy = hoy or date.today()
    gente = [dict(u, iniciales=iniciales(u["nombre"])) for u in integrantes(equipo["id"])]
    filas = cargado(anio, equipo["id"])
    via = foto_viajes(anio, hoy)
    imp = _importado(anio, equipo["id"])
    cub = _cubiertos(anio)

    def de_planilla(clave, mes, uid=None, todos=False):
        return sum(f["cantidad"] for f in imp if f["indicador"] == clave and f["periodo"] == f"M{mes:02d}" and (todos or f["usuario_id"] == uid))
    negrita, blanco = Font(bold=True), Font(bold=True, color="FFFFFF")
    relleno, grupo_rel = PatternFill("solid", fgColor="7C1413"), PatternFill("solid", fgColor="F4E4E4")
    centro = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def valor(uid, clave, mes):
        if (equipo["id"], mes, clave) in cub:
            return de_planilla(clave, mes, uid)
        if clave in DE_VIAJES:
            return via["persona"].get((uid, mes, clave), 0)
        return sum(f["cantidad"] for f in filas if f["usuario_id"] == uid and f["indicador"] == clave and f["periodo"] == f"M{mes:02d}")

    def valor_t(uid, clave, t):
        if clave in DE_VIAJES:
            return None
        return sum(f["cantidad"] for f in filas if f["usuario_id"] == uid and f["indicador"] == clave and f["periodo"] == f"T{t}")

    def total_equipo_mes(clave, mes):
        if (equipo["id"], mes, clave) in cub:  # incluye personas anteriores y lo del equipo sin asignar
            return de_planilla(clave, mes, todos=True) + (0 if clave in DE_VIAJES else
                   sum(f["cantidad"] for f in filas if f["indicador"] == clave and f["periodo"] == f"M{mes:02d}"))
        if clave in DE_VIAJES:
            return via["equipo"].get((equipo["nombre"].lower(), mes, clave), 0)
        return sum(valor(u["id"], clave, mes) for u in gente)

    libro = openpyxl.Workbook()
    libro.remove(libro.active)
    for t in (1, 2, 3, 4):
        h = libro.create_sheet(f"{t}.º trimestre {anio}")
        h.cell(row=1, column=1, value=f"{equipo['nombre']} — {anio}").font = negrita
        col = 2
        meses = meses_de(f"T{t}")
        for mes in meses:
            h.cell(row=1, column=col, value=MESES[mes - 1].upper()).font = blanco
            h.cell(row=1, column=col).fill = relleno
            h.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + len(gente))
            for j, u in enumerate(gente):
                h.cell(row=2, column=col + j, value=u["iniciales"]).font = negrita
            h.cell(row=2, column=col + len(gente), value="TOTAL").font = negrita
            col += len(gente) + 1
        h.cell(row=1, column=col, value="TOTAL TRIMESTRE").font = blanco
        h.cell(row=1, column=col).fill = relleno
        fila = 3
        for gclave, gnombre in GRUPOS:
            h.cell(row=fila, column=1, value=gnombre).font = negrita
            h.cell(row=fila, column=1).fill = grupo_rel
            fila += 1
            for i in [x for x in INDICADORES if x["grupo"] == gclave]:
                h.cell(row=fila, column=1, value=i["nombre"] + (" (desde los viajes)" if i["fuente"] == "viajes" else ""))
                c, total_t = 2, 0
                trimestral = {u["id"]: valor_t(u["id"], i["clave"], t) for u in gente}
                for k, mes in enumerate(meses):
                    for j, u in enumerate(gente):
                        v = valor(u["id"], i["clave"], mes)
                        if v:
                            h.cell(row=fila, column=c + j, value=v)
                    tm = total_equipo_mes(i["clave"], mes)
                    if tm:
                        h.cell(row=fila, column=c + len(gente), value=tm).font = negrita
                    total_t += tm
                    c += len(gente) + 1
                total_t += sum(v or 0 for v in trimestral.values())  # lo cargado por trimestre entero
                h.cell(row=fila, column=c, value=total_t).font = negrita
                fila += 1
        h.column_dimensions["A"].width = 46
        for rr in h.iter_rows(min_row=1, max_row=2):
            for cc in rr:
                cc.alignment = centro
    # resumen mensual (como la «Hoja» de la planilla)
    r = libro.create_sheet("Resumen mensual", 0)
    r.append(["Indicador"] + MESES + ["Total"])
    for cc in r[1]:
        cc.font, cc.fill = blanco, relleno
    for i in INDICADORES:
        por_mes = [total_equipo_mes(i["clave"], m) for m in range(1, 13)]
        extra = sum(f["cantidad"] for f in filas if f["indicador"] == i["clave"] and f["periodo"].startswith("T"))
        r.append([i["nombre"]] + [v or None for v in por_mes] + [sum(por_mes) + extra])
    r.column_dimensions["A"].width = 46
    # metas y avance
    s = seguimiento(anio, [equipo], hoy)["equipos"][0]
    m = libro.create_sheet("Metas y avance", 1)
    m.append(["Indicador", "Meta anual", "T1", "T2", "T3", "T4", "Realizado", "Esperado a hoy", "% de la meta", "Estado"])
    for cc in m[1]:
        cc.font, cc.fill = blanco, relleno
    etiquetas = {"cumplida": "Cumplida", "en_camino": "En camino", "atrasada": "Atrasada", "sin_meta": "Sin meta", "sin_carga": "Sin carga"}
    for f in s["filas"]:
        m.append([f["nombre"], f["meta"] or None] + f["trimestres"] + [f["hecho"], f["esperado"] or None, f["pct"], etiquetas[f["estado"]]])
    m.column_dimensions["A"].width = 46
    # propuesta 82: la meta del equipo es también la de cada integrante
    pp = libro.create_sheet("Estado por persona", 2)
    pp.append(["Persona", "Indicador", "Meta anual (del equipo)", "Realizado", "Esperado a hoy", "% de la meta", "Estado"])
    for cc in pp[1]:
        cc.font, cc.fill = blanco, relleno
    nombres_i = {f["clave"]: f["nombre"] for f in s["filas"]}
    for x in s["por_persona"]:
        if x.get("anterior"):
            continue
        for clave, d in x["indicadores"].items():
            if d["meta"]:
                pp.append([x["usuario"]["nombre"], nombres_i.get(clave, clave), d["meta"], d["hecho"], d["esperado"], d["pct"], etiquetas[d["estado"]]])
    pp.column_dimensions["A"].width = 30
    pp.column_dimensions["B"].width = 46
    return libro
