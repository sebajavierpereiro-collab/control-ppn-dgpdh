"""Traslados: viajes institucionales y por medios propios, choferes y vehículos.

Reglas (definidas con la institución):
  * Un asesor puede pedir chofer o registrar un viaje por sus medios, y sumar a
    varios agentes. Cualquier asesor puede cargar.
  * Los viajes de rutina de la semana siguiente se cargan hasta el JUEVES
    anterior; lo que se carga después queda marcado como INMEDIATO (con motivo).
  * Un viaje queda CONFIRMADO salvo que se cargue su cancelación o
    reprogramación. Con chofer, queda SOLICITADO hasta que la coordinación
    asigna chofer y vehículo (el auto se elige según la cantidad de personas).
  * Choferes y autos nunca se eliminan: cambian de estado (baja / fuera de
    circulación). Toda modificación queda en una auditoría inalterable.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import database as db

MODALIDADES = ("CHOFER", "PROPIO")
ETIQUETAS_MODALIDAD = {"CHOFER": "Con chofer", "PROPIO": "Por sus propios medios"}
ESTADOS_VIAJE = ("A_CONFIRMAR", "SOLICITADO", "CONFIRMADO", "CANCELADO")  # A_CONFIRMAR = precargado de un viaje frecuente
ETIQUETAS_ESTADO_VIAJE = {"A_CONFIRMAR": "A confirmar (viaje frecuente)", "NO_CONFIRMADO": "No confirmado (no se realizó)",
                          "SOLICITADO": "Pedido de chofer (sin asignar)", "CONFIRMADO": "Confirmado", "CANCELADO": "Cancelado", "REALIZADO": "Realizado"}
ESTADOS_CHOFER = ("DISPONIBLE", "LICENCIA", "VACACIONES", "ENFERMEDAD", "COMISION", "BAJA")
ETIQUETAS_ESTADO_CHOFER = {"DISPONIBLE": "Disponible", "LICENCIA": "Licencia", "VACACIONES": "Vacaciones",
                           "ENFERMEDAD": "Enfermedad", "COMISION": "Comisión de servicio", "BAJA": "Baja"}
ESTADOS_VEHICULO = ("DISPONIBLE", "TALLER", "FUERA_DE_CIRCULACION", "BAJA")  # FUERA_DE_CIRCULACION = suspendido; BAJA = eliminado (se conserva)
ETIQUETAS_ESTADO_VEHICULO = {"DISPONIBLE": "Disponible", "TALLER": "En taller", "FUERA_DE_CIRCULACION": "Suspendido", "BAJA": "Eliminado (dado de baja)"}
VENTANA_POR_DEFECTO_HORAS = 4
_HORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ErrorTraslado(ValueError):
    """Error de validación o de regla de negocio, con mensaje para mostrar."""


# ---------------------------------------------------------------- fechas y plazos
def _fecha(v):
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ErrorTraslado("La fecha no es válida.")


def lunes_de(f):
    f = _fecha(f)
    return f - timedelta(days=f.weekday())


def limite_planificacion(fecha_viaje):
    """Jueves anterior a la semana del viaje: hasta ese día se cargan los viajes de rutina."""
    return lunes_de(fecha_viaje) - timedelta(days=4)


def es_inmediato(fecha_viaje, cargado_el):
    f, c = _fecha(fecha_viaje), _fecha(cargado_el)
    if f < c:
        return False  # registro posterior de un viaje ya hecho: no es «inmediato»
    return c > limite_planificacion(f)


def normalizar_hora(v):
    v = (v or "").strip().replace(".", ":").replace("h", "")
    if not v:
        return None
    if re.fullmatch(r"\d{1,2}", v):
        v += ":00"
    m = _HORA.match(v)
    if not m:
        raise ErrorTraslado(f"La hora «{v}» no es válida (usá HH:MM).")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _ventana(v):
    """(inicio, fin) en minutos del día, para detectar solapamientos."""
    def mins(h):
        return int(h[:2]) * 60 + int(h[3:5])
    ini = mins(v["hora_salida"]) if v.get("hora_salida") else 8 * 60
    fin = mins(v["hora_regreso"]) if v.get("hora_regreso") else ini + VENTANA_POR_DEFECTO_HORAS * 60
    return ini, max(fin, ini + 30)


def _se_pisan(a, b):
    (a1, a2), (b1, b2) = _ventana(a), _ventana(b)
    return a1 < b2 and b1 < a2


# ---------------------------------------------------------------- auditoría
def auditar(entidad, entidad_id, accion, anterior=None, nuevo=None, con=None):
    u = db.USUARIO_ACTUAL.get() or {}
    args = (entidad, entidad_id, accion, None if anterior is None else str(anterior), None if nuevo is None else str(nuevo),
            u.get("nombre") or "Sistema", db._ahora())
    sql = "INSERT INTO traslados_auditoria (entidad, entidad_id, accion, anterior, nuevo, usuario, fecha) VALUES (?,?,?,?,?,?,?)"
    if con is not None:
        con.execute(sql, args)
    else:
        with db._conexion() as c:
            c.execute(sql, args)


MOTIVO_DESCARTADO = "Descartado: el viaje frecuente se pausó o terminó antes de confirmarlo."  # propuesta 80

MOTIVOS_CANCELACION = ("Clima", "La unidad no permite el ingreso", "Vehículo o chofer no disponible", "Se reprograma",
                       "Ya no es necesario", "Superposición con otra actividad", "Otro")


def evento_linea_tiempo(h):
    """Fila de la auditoría → evento de la línea de tiempo."""
    detalle = None
    if h.get("anterior") or h.get("nuevo"):
        detalle = f"{h.get('anterior') or '—'} → {h.get('nuevo') or '—'}" if h.get("anterior") else h.get("nuevo")
    accion = h.get("accion") or ""
    tipo = "alerta" if accion.startswith("Cancel") else ("ok" if accion.startswith(("Confirm", "Asign", "Alta")) else None)
    return {"fecha": h.get("fecha"), "quien": h.get("usuario"), "texto": accion, "detalle": detalle, "tipo": tipo}


def pasajeros_por_equipo(participantes):
    """Para la agenda: [(«Con chofer PPN» | «Por sus propios medios», [(equipo, [nombres])])], primero los que van en el
    vehículo institucional. Dentro de cada grupo, por equipo (los sin equipo al final)."""
    salida = []
    for titulo, propio in (("Con chofer PPN", False), ("Por sus propios medios", True)):
        por_equipo = {}
        for p in participantes or []:
            if bool(p.get("auto_propio")) != propio:
                continue
            equipo = p.get("equipo_nombre") or (p.get("externo_area") if not p.get("usuario_id") else None) or "Sin equipo"
            por_equipo.setdefault(equipo, []).append(p.get("nombre") or "")
        if por_equipo:
            orden = sorted(por_equipo, key=lambda e: (e == "Sin equipo", e.lower()))
            salida.append((titulo, [(e, por_equipo[e]) for e in orden]))
    return salida


def historial(entidad, entidad_id):
    with db._conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM traslados_auditoria WHERE entidad = ? AND entidad_id = ? ORDER BY id DESC", (entidad, entidad_id))]


# ---------------------------------------------------------------- permisos
def puede_coordinar(usuario):
    return bool(usuario) and (usuario["rol"] in ("DIRECTOR", "SUBDIRECTOR") or bool(usuario.get("coordina_traslados")))


def puede_editar_viaje(usuario, viaje):
    if puede_coordinar(usuario) or (viaje.get("creado_por_id") == usuario["id"]):
        return True
    if viaje.get("estado") == "A_CONFIRMAR":  # un precargado lo ajusta quien lo confirma (personas, horario)
        import frecuentes
        return frecuentes.puede_confirmar(usuario, viaje)
    return False


def alcance_estadisticas(usuario):
    """Qué puede ver el usuario: toda la base, su equipo o lo propio."""
    if puede_coordinar(usuario):
        return {"tipo": "todos"}
    if usuario["rol"] == "COORDINADOR" and usuario.get("equipo_id"):
        with db._conexion() as con:
            e = con.execute("SELECT nombre FROM equipos WHERE id = ?", (usuario["equipo_id"],)).fetchone()
        return {"tipo": "equipo", "equipo_nombre": e["nombre"] if e else None, "usuario_id": usuario["id"]}
    return {"tipo": "propio", "usuario_id": usuario["id"]}


def usuarios_coordinadores():
    return [u["id"] for u in db.listar_usuarios() if u["activo"] and (u["rol"] in ("DIRECTOR", "SUBDIRECTOR") or u.get("coordina_traslados"))]


# ---------------------------------------------------------------- lectura
def _cargar_participantes(con, viajes):
    if not viajes:
        return
    ids = [v["id"] for v in viajes]
    por = {}
    for p in con.execute("""SELECT p.*, e.area AS externo_area FROM viaje_participantes p
                            LEFT JOIN participantes_externos e ON e.id = p.externo_id
                            WHERE p.viaje_id IN (%s) ORDER BY p.id""" % ",".join("?" * len(ids)), ids):
        p = dict(p)
        p["externo"] = not p.get("usuario_id")
        por.setdefault(p["viaje_id"], []).append(p)
    for v in viajes:
        v["participantes"] = por.get(v["id"], [])


def obtener_viaje(viaje_id):
    with db._conexion() as con:
        f = con.execute(
            """SELECT v.*, u.nombre AS unidad_nombre, u.ciudad AS unidad_ciudad, u.provincia AS unidad_provincia,
                      u.telefono_guardia AS unidad_telefono, u.horarios_visita AS unidad_horarios, u.contactos AS unidad_contactos,
                      c.nombre AS chofer_nombre, ve.dominio AS vehiculo_dominio, ve.marca AS vehiculo_marca,
                      ve.modelo AS vehiculo_modelo, ve.capacidad AS vehiculo_capacidad
               FROM viajes v LEFT JOIN unidades u ON u.id = v.unidad_id LEFT JOIN choferes c ON c.id = v.chofer_id
               LEFT JOIN vehiculos ve ON ve.id = v.vehiculo_id WHERE v.id = ?""", (viaje_id,)).fetchone()
        if not f:
            return None
        v = dict(f)
        _cargar_participantes(con, [v])
    v = _decorar(v)
    _completar_salidas([v])
    return v


def _decorar(v, hoy=None):
    hoy = hoy or date.today()
    v["destino"] = v.get("unidad_nombre") or v.get("destino_otro") or "—"
    if v["estado"] == "CONFIRMADO" and v["fecha"] < hoy.isoformat():
        v["estado_efectivo"] = "REALIZADO"
    elif v["estado"] == "A_CONFIRMAR" and v["fecha"] < hoy.isoformat():
        v["estado_efectivo"] = "NO_CONFIRMADO"  # nadie lo confirmó: no cuenta como realizado
    else:
        v["estado_efectivo"] = v["estado"]
    cap = v.get("vehiculo_capacidad")
    n = sum(1 for p in v.get("participantes", []) if not p.get("auto_propio"))
    v["con_transporte"] = n
    v["plazas_libres"] = (cap - n) if cap else None
    return v


# ---------------------------------------------------------------- misma salida (un vehículo, varias paradas)
RADIO_CERCANIA_KM = 15


def _norm_lugar(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def destinos_cercanos(a, b):
    """¿Dos viajes van a lugares vecinos (se puede hacer en una sola salida)? Misma unidad o mismo lugar escrito,
    unidades de la misma ciudad (p. ej. CPF I y Unidad 19, ambas en Ezeiza) o a menos de RADIO_CERCANIA_KM."""
    if a.get("unidad_id") and a.get("unidad_id") == b.get("unidad_id"):
        return True
    if not a.get("unidad_id") and not b.get("unidad_id"):
        return bool(_norm_lugar(a.get("destino_otro"))) and _norm_lugar(a.get("destino_otro")) == _norm_lugar(b.get("destino_otro"))
    ua = db.obtener_unidad(a["unidad_id"]) if a.get("unidad_id") else None
    ub = db.obtener_unidad(b["unidad_id"]) if b.get("unidad_id") else None
    if not ua or not ub:
        return False
    if _norm_lugar(ua.get("ciudad")) and _norm_lugar(ua.get("ciudad")) == _norm_lugar(ub.get("ciudad")):
        return True
    try:
        import math
        la1, lo1, la2, lo2 = (float(x) for x in (ua["lat"], ua["lng"], ub["lat"], ub["lng"]))
    except (TypeError, ValueError, KeyError):
        return False
    dlat, dlon = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h)) <= RADIO_CERCANIA_KM


def _completar_salidas(viajes):
    """Viajes del mismo día con el mismo vehículo y horarios que se pisan son UNA salida con varias paradas:
    comparten las plazas. Agrega `misma_salida` (las otras paradas), `con_transporte_salida` y recalcula `plazas_libres`."""
    claves = {(v["fecha"], v["vehiculo_id"]) for v in viajes
              if v.get("vehiculo_id") and v.get("modalidad") == "CHOFER" and v["estado"] != "CANCELADO"}
    if not claves:
        return
    fechas = sorted({f for f, _ in claves})
    with db._conexion() as con:
        otros = [dict(f) for f in con.execute(
            f"""SELECT v.id, v.fecha, v.vehiculo_id, v.chofer_id, v.hora_salida, v.hora_regreso, v.unidad_id, v.destino_otro,
                       u.nombre AS unidad_nombre,
                       (SELECT COUNT(*) FROM viaje_participantes p WHERE p.viaje_id = v.id AND COALESCE(p.auto_propio, 0) = 0) AS n
                FROM viajes v LEFT JOIN unidades u ON u.id = v.unidad_id
                WHERE v.fecha IN ({','.join('?' * len(fechas))}) AND v.vehiculo_id IS NOT NULL AND v.estado != 'CANCELADO'
                  AND v.modalidad = 'CHOFER'""", fechas)]
    por = {}
    for o in otros:
        por.setdefault((o["fecha"], o["vehiculo_id"]), []).append(o)
    for v in viajes:
        clave = (v["fecha"], v.get("vehiculo_id"))
        if clave not in claves:
            continue
        juntos = [o for o in por.get(clave, []) if o["id"] != v["id"] and _se_pisan(v, o)]
        if not juntos:
            continue
        v["misma_salida"] = [{"id": o["id"], "destino": o["unidad_nombre"] or o["destino_otro"] or "—", "hora_salida": o["hora_salida"],
                              "personas": o["n"]} for o in sorted(juntos, key=lambda o: (o["hora_salida"] or "99", o["id"]))]
        v["con_transporte_salida"] = v["con_transporte"] + sum(o["n"] for o in juntos)
        if v.get("vehiculo_capacidad"):
            v["plazas_libres"] = v["vehiculo_capacidad"] - v["con_transporte_salida"]


def listar_viajes(desde=None, hasta=None, unidad_id=None, usuario_id=None, equipo_nombre=None, estado=None, modalidad=None, circuito=None, limite=2000,
                  incluir_descartados=False):
    sql = """SELECT v.*, u.nombre AS unidad_nombre, c.nombre AS chofer_nombre, ve.dominio AS vehiculo_dominio,
                    ve.marca AS vehiculo_marca, ve.modelo AS vehiculo_modelo, ve.capacidad AS vehiculo_capacidad
             FROM viajes v LEFT JOIN unidades u ON u.id = v.unidad_id LEFT JOIN choferes c ON c.id = v.chofer_id
             LEFT JOIN vehiculos ve ON ve.id = v.vehiculo_id WHERE 1=1"""
    args = []
    if not incluir_descartados:  # propuesta 80: los precargados descartados se conservan pero no se listan (como antes, que se borraban)
        sql += " AND (v.estado != 'CANCELADO' OR v.motivo_cancelacion IS NULL OR v.motivo_cancelacion != ?)"; args.append(MOTIVO_DESCARTADO)
    if desde: sql += " AND v.fecha >= ?"; args.append(desde)
    if hasta: sql += " AND v.fecha <= ?"; args.append(hasta)
    if unidad_id: sql += " AND v.unidad_id = ?"; args.append(unidad_id)
    if estado: sql += " AND v.estado = ?"; args.append(estado)
    if modalidad: sql += " AND v.modalidad = ?"; args.append(modalidad)
    if circuito: sql += " AND v.circuito = ?"; args.append(circuito)
    if usuario_id:
        sql += " AND (v.creado_por_id = ? OR EXISTS (SELECT 1 FROM viaje_participantes p WHERE p.viaje_id = v.id AND p.usuario_id = ?))"
        args += [usuario_id, usuario_id]
    if equipo_nombre:
        sql += " AND EXISTS (SELECT 1 FROM viaje_participantes p WHERE p.viaje_id = v.id AND p.equipo_nombre = ? COLLATE NOCASE)"
        args.append(equipo_nombre)
    sql += " ORDER BY v.fecha, COALESCE(v.hora_salida, '99'), v.id LIMIT ?"
    args.append(limite)
    with db._conexion() as con:
        viajes = [dict(f) for f in con.execute(sql, args)]
        _cargar_participantes(con, viajes)
    viajes = [_decorar(v) for v in viajes]
    _completar_salidas(viajes)
    return viajes


def semana(lunes, dias=7, **filtros):
    """{fecha_iso: [viajes]} para la semana que empieza en `lunes`."""
    l = lunes_de(lunes)
    fin = l + timedelta(days=dias - 1)
    por = {(l + timedelta(days=i)).isoformat(): [] for i in range(dias)}
    for v in listar_viajes(desde=l.isoformat(), hasta=fin.isoformat(), **filtros):
        por[v["fecha"]].append(v)
    return por


def pendientes_de_chofer():
    return [v for v in listar_viajes(desde=date.today().isoformat(), estado="SOLICITADO", circuito="AMBA")]


# ---------------------------------------------------------------- escritura de viajes
def _validar_datos(d):
    fecha = _fecha(d.get("fecha")).isoformat()
    salida, regreso = normalizar_hora(d.get("hora_salida")), normalizar_hora(d.get("hora_regreso"))
    if salida and regreso and regreso <= salida:
        raise ErrorTraslado("La hora de regreso tiene que ser posterior a la de salida.")
    unidad_id = int(d["unidad_id"]) if str(d.get("unidad_id") or "").isdigit() else None
    otro = re.sub(r"\s+", " ", str(d.get("destino_otro") or "")).strip() or None
    if unidad_id and not db.obtener_unidad(unidad_id):
        raise ErrorTraslado("La unidad elegida no existe.")
    if not unidad_id and not otro:
        raise ErrorTraslado("Indicá la unidad de destino (o escribí el lugar si no está en el listado).")
    modalidad = d.get("modalidad")
    if modalidad not in MODALIDADES:
        raise ErrorTraslado("Elegí si el viaje es con chofer o por medios propios.")
    return dict(fecha=fecha, hora_salida=salida, hora_regreso=regreso, unidad_id=unidad_id, destino_otro=None if unidad_id else otro,
                motivo=(str(d.get("motivo") or "").strip()[:500] or None), modalidad=modalidad,
                observaciones=(str(d.get("observaciones") or "").strip()[:500] or None))


def _limpiar_participantes(participantes):
    out, vistos = [], set()
    for p in participantes or []:
        nombre = re.sub(r"\s+", " ", str(p.get("nombre") or "")).strip()
        if not nombre:
            continue
        clave = (p.get("usuario_id") or nombre.lower())
        if clave in vistos:
            continue
        vistos.add(clave)
        out.append(dict(usuario_id=p.get("usuario_id") or None, nombre=nombre[:120], equipo_nombre=(str(p.get("equipo_nombre") or "").strip()[:120] or None),
                        auto_propio=1 if p.get("auto_propio") else 0, externo_id=p.get("externo_id") or None,
                        area=(str(p.get("area") or "").strip()[:120] or None), observaciones=(str(p.get("observaciones") or "").strip()[:300] or None)))
    return out


# ---------------------------------------------------------------- personas sin usuario
def _registrar_externo(con, nombre, area=None, observaciones=None, externo_id=None):
    """Devuelve la fila del catálogo para esa persona (la crea si no existe). Completa área/observaciones si vienen."""
    fila = None
    if externo_id:
        fila = con.execute("SELECT * FROM participantes_externos WHERE id = ?", (externo_id,)).fetchone()
    norm = db.normalizar_nombre(nombre)
    if not fila:
        fila = con.execute("SELECT * FROM participantes_externos WHERE nombre_norm = ?", (norm,)).fetchone()
    if not fila:
        u = db.USUARIO_ACTUAL.get() or {}
        nid = con.execute("INSERT INTO participantes_externos (nombre, nombre_norm, area, observaciones, creado_por, creado_en) VALUES (?,?,?,?,?,?)",
                          (nombre, norm, area, observaciones, u.get("nombre") or "Sistema", db._ahora())).lastrowid
        return dict(con.execute("SELECT * FROM participantes_externos WHERE id = ?", (nid,)).fetchone())
    fila = dict(fila)
    cambios = {k: v for k, v in (("area", area), ("observaciones", observaciones)) if v and v != fila.get(k)}
    if cambios:
        con.execute("UPDATE participantes_externos SET %s WHERE id = ?" % ", ".join(f"{k} = ?" for k in cambios), (*cambios.values(), fila["id"]))
        fila.update(cambios)
    if not fila["activo"]:
        con.execute("UPDATE participantes_externos SET activo = 1 WHERE id = ?", (fila["id"],))
    return fila


def _insertar_participante(con, viaje_id, p):
    externo_id = None
    if not p.get("usuario_id"):
        ext = _registrar_externo(con, p["nombre"], p.get("area"), p.get("observaciones"), p.get("externo_id"))
        externo_id = ext["id"]
        if ext.get("usuario_id"):  # ya se le creó usuario: el viaje queda en su historial
            p["usuario_id"] = ext["usuario_id"]
    equipo = p.get("equipo_nombre")
    if not equipo and p.get("usuario_id"):  # el equipo de la persona, para las estadísticas por equipo
        f = con.execute("SELECT e.nombre FROM usuarios u JOIN equipos e ON e.id = u.equipo_id WHERE u.id = ?", (p["usuario_id"],)).fetchone()
        equipo = f["nombre"] if f else None
    con.execute("INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, equipo_nombre, auto_propio, externo_id) VALUES (?,?,?,?,?,?)",
                (viaje_id, p.get("usuario_id"), p["nombre"], equipo, p["auto_propio"], externo_id))


def buscar_externos(texto, limite=10):
    t = db.normalizar_nombre(texto)
    if len(t) < 2:
        return []
    with db._conexion() as con:
        filas = con.execute("""SELECT e.*, (SELECT COUNT(*) FROM viaje_participantes p WHERE p.externo_id = e.id) AS viajes
                               FROM participantes_externos e WHERE e.activo = 1 AND e.usuario_id IS NULL""").fetchall()
    out = [dict(f) for f in filas if all(x in (f["nombre_norm"] + " " + db.normalizar_nombre(f["area"])) for x in t.split())]
    out.sort(key=lambda e: (-e["viajes"], e["nombre_norm"]))
    return out[:limite]


def listar_externos(incluir_bajas=False):
    with db._conexion() as con:
        filas = con.execute("""SELECT e.*, us.nombre AS usuario_nombre,
                                      (SELECT COUNT(*) FROM viaje_participantes p WHERE p.externo_id = e.id) AS viajes,
                                      (SELECT MAX(v.fecha) FROM viaje_participantes p JOIN viajes v ON v.id = p.viaje_id WHERE p.externo_id = e.id) AS ultimo
                               FROM participantes_externos e LEFT JOIN usuarios us ON us.id = e.usuario_id
                               """ + ("" if incluir_bajas else "WHERE e.activo = 1 ") + "ORDER BY e.nombre_norm").fetchall()
    return [dict(f) for f in filas]


def actualizar_externo(externo_id, nombre, area, observaciones):
    nombre = re.sub(r"\s+", " ", str(nombre or "")).strip()[:120]
    if not nombre:
        raise ErrorTraslado("Indicá el nombre.")
    norm = db.normalizar_nombre(nombre)
    with db._conexion() as con:
        ant = con.execute("SELECT * FROM participantes_externos WHERE id = ?", (externo_id,)).fetchone()
        if not ant:
            raise ErrorTraslado("Esa persona no está en el listado.")
        otro = con.execute("SELECT id FROM participantes_externos WHERE nombre_norm = ? AND id != ?", (norm, externo_id)).fetchone()
        if otro:
            raise ErrorTraslado("Ya hay otra persona cargada con ese nombre.")
        con.execute("UPDATE participantes_externos SET nombre = ?, nombre_norm = ?, area = ?, observaciones = ? WHERE id = ?",
                    (nombre, norm, (area or "").strip()[:120] or None, (observaciones or "").strip()[:300] or None, externo_id))
        auditar("externo", externo_id, "Datos", ant["nombre"], nombre, con=con)


def baja_externo(externo_id, activo):
    with db._conexion() as con:
        con.execute("UPDATE participantes_externos SET activo = ? WHERE id = ?", (1 if activo else 0, externo_id))
        auditar("externo", externo_id, "Reactivada" if activo else "Baja del listado", con=con)


def vincular_externo(externo_id, usuario_id):
    """La persona ya tiene usuario: sus viajes anteriores pasan a su historial (no se borra nada)."""
    with db._conexion() as con:
        ext = con.execute("SELECT * FROM participantes_externos WHERE id = ?", (externo_id,)).fetchone()
        us = con.execute("SELECT id, nombre, equipo_id FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not ext or not us:
            raise ErrorTraslado("Elegí la persona y el usuario.")
        eq = con.execute("SELECT nombre FROM equipos WHERE id = ?", (us["equipo_id"],)).fetchone() if us["equipo_id"] else None
        n = con.execute("UPDATE viaje_participantes SET usuario_id = ?, equipo_nombre = COALESCE(equipo_nombre, ?) WHERE externo_id = ? AND usuario_id IS NULL",
                        (us["id"], eq["nombre"] if eq else None, externo_id)).rowcount
        con.execute("UPDATE participantes_externos SET usuario_id = ? WHERE id = ?", (us["id"], externo_id))
        auditar("externo", externo_id, "Vinculada a usuario", ext["nombre"], f"{us['nombre']} ({n} viaje/s)", con=con)
    return n


# ---------------------------------------------------------------- vincular nombres cargados con usuarios
def _palabras_nombre(nombre):
    return [t for t in re.split(r"[^a-z0-9ñ]+", db.normalizar_nombre(nombre)) if t]


def parecido_usuario(nombre, usuario_nombre):
    """Puntaje de 0 a 100 de que el nombre cargado («L.Aguilar», «Aguilar Lucas», «Alfredo») sea esa persona.
    Las palabras largas tienen que estar en el nombre del usuario (completas o como comienzo de 4+ letras);
    las iniciales, coincidir con la inicial de otra palabra. 0 si no hay coincidencia posible."""
    a, b = _palabras_nombre(nombre), _palabras_nombre(usuario_nombre)
    if not a or not b:
        return 0
    if a == b or sorted(a) == sorted(b):
        return 100
    usados, puntos = set(), 0
    largas = [t for t in a if len(t) > 1]
    iniciales = [t for t in a if len(t) == 1]
    for t in largas:
        j = next((j for j, x in enumerate(b) if j not in usados and (x == t or (len(t) >= 4 and x.startswith(t)) or (len(x) >= 4 and t.startswith(x)))), None)
        if j is None:
            return 0
        usados.add(j)
        puntos += 30 if b[j] == t else 20
    for t in iniciales:
        j = next((j for j, x in enumerate(b) if j not in usados and x.startswith(t)), None)
        if j is None:
            return 0
        usados.add(j)
        puntos += 10
    if not largas:
        return 0
    faltan = len(b) - len(usados)
    return max(1, min(95, puntos + (20 if faltan == 0 else 0) - 5 * faltan))


def sugerir_usuarios(nombre, usuarios, limite=3):
    """Usuarios que podrían ser esa persona, del más probable al menos. Solo propone: vincula una persona."""
    cand = [(parecido_usuario(nombre, u["nombre"]), u) for u in usuarios]
    cand = [(p, u) for p, u in cand if p > 0]
    cand.sort(key=lambda x: (-x[0], x[1]["nombre"].lower()))
    return [dict(u, parecido=p) for p, u in cand[:limite]]


def _columnas_chofer(con):
    """Todas las tablas que guardan un chofer_id (viajes, licencias, reservas, operación…)."""
    tablas = [f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return [t for t in tablas if any(c[1] == "chofer_id" for c in con.execute(f"PRAGMA table_info({t})"))]


def vincular_chofer(chofer_id, usuario_id):
    """Vincula la ficha de chofer (p. ej. cargada por nombre al importar viajes) con un usuario. Si ese usuario ya
    tiene su propia ficha, las dos se unifican: viajes, licencias y reservas pasan a la ficha del usuario y la otra queda
    en Baja con la constancia. No se borra nada. Devuelve {'unificada': bool, 'registros': n}."""
    with db._conexion() as con:
        ch = con.execute("SELECT * FROM choferes WHERE id = ?", (chofer_id,)).fetchone()
        us = con.execute("SELECT id, nombre FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not ch or not us:
            raise ErrorTraslado("Elegí el chofer y el usuario.")
        otra = con.execute("SELECT * FROM choferes WHERE usuario_id = ? AND id != ?", (usuario_id, chofer_id)).fetchone()
        if not otra:
            con.execute("UPDATE choferes SET usuario_id = ?, actualizado_en = ? WHERE id = ?", (usuario_id, db._ahora(), chofer_id))
            auditar("chofer", chofer_id, "Vinculado a usuario", ch["nombre"], us["nombre"], con=con)
            return {"unificada": False, "registros": 0}
        n = 0
        for t in _columnas_chofer(con):
            if t == "choferes":
                continue
            n += con.execute(f"UPDATE {t} SET chofer_id = ? WHERE chofer_id = ?", (otra["id"], chofer_id)).rowcount
        obs = ((ch["observaciones"] or "") + f" · Unificada con la ficha de {us['nombre']} el {db._ahora()[:10]}").strip(" ·")
        con.execute("UPDATE choferes SET estado = 'BAJA', observaciones = ?, actualizado_en = ? WHERE id = ?", (obs, db._ahora(), chofer_id))
        auditar("chofer", chofer_id, "Unificada con usuario", ch["nombre"], f"{us['nombre']} ({n} registro/s)", con=con)
        auditar("chofer", otra["id"], "Recibió los registros de", ch["nombre"], f"{n} registro/s", con=con)
        return {"unificada": True, "registros": n}


def vincular_participante(viaje_id, participante_id, usuario_id):
    """Vincula a un pasajero cargado sin usuario. Si figura en el listado de personas sin usuario, se vinculan TODOS sus
    viajes (como en «Personas sin usuario»); si no, solo este registro. Devuelve la cantidad de viajes que cambiaron."""
    with db._conexion() as con:
        p = con.execute("SELECT * FROM viaje_participantes WHERE id = ? AND viaje_id = ?", (participante_id, viaje_id)).fetchone()
        us = con.execute("SELECT id, nombre, equipo_id FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not p or not us:
            raise ErrorTraslado("Elegí la persona y el usuario.")
        if p["usuario_id"]:
            raise ErrorTraslado("Esa persona ya está vinculada a un usuario.")
        ya = con.execute("SELECT 1 FROM viaje_participantes WHERE viaje_id = ? AND usuario_id = ?", (viaje_id, usuario_id)).fetchone()
        if ya:
            raise ErrorTraslado(f"{us['nombre']} ya figura en este viaje: quitá el registro repetido.")
    if p["externo_id"]:
        return vincular_externo(p["externo_id"], usuario_id)
    with db._conexion() as con:
        eq = con.execute("SELECT nombre FROM equipos WHERE id = ?", (us["equipo_id"],)).fetchone() if us["equipo_id"] else None
        con.execute("UPDATE viaje_participantes SET usuario_id = ?, nombre = ?, equipo_nombre = COALESCE(equipo_nombre, ?) WHERE id = ?",
                    (us["id"], us["nombre"], eq["nombre"] if eq else None, participante_id))
        auditar("viaje", viaje_id, "Pasajero vinculado a usuario", p["nombre"], us["nombre"], con=con)
    return 1


CIRCUITOS = ("AMBA", "DELEGACIONES")
ETIQUETAS_CIRCUITO = {"AMBA": "AMBA", "DELEGACIONES": "Delegaciones"}
TIPOS_ACTIVIDAD = ("ACTIVIDAD", "COMISION", "VISITA")
ETIQUETAS_ACTIVIDAD = {"ACTIVIDAD": "Actividad", "COMISION": "Comisión", "VISITA": "Visita"}
MENSAJE_SIN_CHOFER = "Los traslados con chofer institucional sólo se encuentran disponibles para destinos ubicados dentro del AMBA."


def es_amba(unidad_id):
    """True si la unidad está en el AMBA; None si no se sabe (unidad sin zona o lugar libre)."""
    if not unidad_id:
        return None
    u = db.obtener_unidad(unidad_id)
    if not u or not u.get("zona"):
        return None
    return u["zona"].strip().upper() == "AMBA"


def circuito_de(usuario):
    return usuario.get("circuito") or "AMBA"


def circuitos_visibles(usuario):
    """Dirección y coordinación ven los dos circuitos; el resto, el suyo."""
    return list(CIRCUITOS) if puede_coordinar(usuario) else [circuito_de(usuario)]


def _modalidad_de(participantes):
    return "CHOFER" if any(not p["auto_propio"] for p in participantes) else "PROPIO"


def crear_viaje(datos, participantes, usuario, hoy=None, origen="SISTEMA"):
    hoy = hoy or date.today()
    circuito = datos.get("circuito") if (puede_coordinar(usuario) and datos.get("circuito") in CIRCUITOS) else circuito_de(usuario)
    parts = _limpiar_participantes(participantes)
    if not parts:
        raise ErrorTraslado("Sumá al menos una persona al viaje.")
    datos = dict(datos)
    tipo_act = None
    if circuito == "DELEGACIONES":
        # Delegaciones registra actividades, comisiones y visitas: sin chofer, siempre en la delegación asignada
        for p in parts:
            p["auto_propio"] = 1
        deleg = usuario.get("delegacion_id")
        if deleg:
            if str(datos.get("unidad_id") or "") not in ("", str(deleg)):
                raise ErrorTraslado("Los asesores de Delegaciones registran sus actividades en la delegación asignada.")
            datos["unidad_id"] = deleg
        tipo_act = datos.get("tipo_actividad") if datos.get("tipo_actividad") in TIPOS_ACTIVIDAD else "ACTIVIDAD"
    datos["modalidad"] = _modalidad_de(parts)
    d = _validar_datos(datos)
    if d["modalidad"] == "CHOFER" and es_amba(d["unidad_id"]) is False:
        raise ErrorTraslado(MENSAJE_SIN_CHOFER + " Marcá «Por sus propios medios» para las personas que viajan.")
    inmediato = circuito == "AMBA" and es_inmediato(d["fecha"], hoy)
    motivo_inm = (str(datos.get("motivo_inmediato") or "").strip()[:300] or None)  # opcional
    estado = "SOLICITADO" if d["modalidad"] == "CHOFER" else "CONFIRMADO"
    ahora = db._ahora()
    with db._conexion() as con:
        cur = con.execute(
            """INSERT INTO viajes (fecha, hora_salida, hora_regreso, unidad_id, destino_otro, motivo, modalidad, estado, inmediato,
                                   motivo_inmediato, observaciones, origen, circuito, tipo_actividad, creado_por_id, creado_por, creado_en, actualizado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d["fecha"], d["hora_salida"], d["hora_regreso"], d["unidad_id"], d["destino_otro"], d["motivo"], d["modalidad"], estado,
             1 if inmediato else 0, motivo_inm if inmediato else None, d["observaciones"], origen, circuito, tipo_act,
             usuario["id"], usuario["nombre"], ahora, ahora))
        vid = cur.lastrowid
        for p in parts:
            _insertar_participante(con, vid, p)
        auditar("viaje", vid, "Alta", nuevo=f"{d['fecha']} {circuito} {d['modalidad']} {len(parts)} persona(s)" + (" · INMEDIATO" if inmediato else ""), con=con)
    if estado == "SOLICITADO":
        db.notificar_usuarios(usuarios_coordinadores(), f"Nuevo pedido de chofer para el {d['fecha']}" + (" (fuera de plazo)" if inmediato else "") + f": {usuario['nombre']}.", f"/traslados/{vid}")
    return vid


def _viaje_editable(viaje_id):
    v = obtener_viaje(viaje_id)
    if not v:
        raise ErrorTraslado("El viaje no existe.")
    if v["estado"] == "CANCELADO":
        raise ErrorTraslado("El viaje está cancelado.")
    return v


def reprogramar(viaje_id, fecha, hora_salida, hora_regreso, usuario, hoy=None):
    v = _viaje_editable(viaje_id)
    nuevo = _validar_datos(dict(fecha=fecha, hora_salida=hora_salida, hora_regreso=hora_regreso,
                                unidad_id=v["unidad_id"], destino_otro=v["destino_otro"] or "x", modalidad=v["modalidad"]))
    if (nuevo["fecha"], nuevo["hora_salida"], nuevo["hora_regreso"]) == (v["fecha"], v["hora_salida"], v["hora_regreso"]):
        raise ErrorTraslado("No cambió nada.")
    reset = v["modalidad"] == "CHOFER" and (v["chofer_id"] or v["vehiculo_id"])
    with db._conexion() as con:
        con.execute(
            """UPDATE viajes SET fecha = ?, hora_salida = ?, hora_regreso = ?, reprogramaciones = reprogramaciones + 1, actualizado_en = ?
               WHERE id = ?""", (nuevo["fecha"], nuevo["hora_salida"], nuevo["hora_regreso"], db._ahora(), viaje_id))
        if reset:  # el chofer/auto asignados ya no valen para la nueva fecha: la coordinación reasigna
            con.execute("UPDATE viajes SET chofer_id = NULL, vehiculo_id = NULL, estado = 'SOLICITADO' WHERE id = ?", (viaje_id,))
        auditar("viaje", viaje_id, "Reprogramado", f"{v['fecha']} {v['hora_salida'] or ''}", f"{nuevo['fecha']} {nuevo['hora_salida'] or ''}", con=con)
    _vincular(viaje_id)
    if reset:
        db.notificar_usuarios(usuarios_coordinadores(), f"Se reprogramó un viaje con chofer ({v['destino']}, ahora {nuevo['fecha']}): hay que volver a asignar.", f"/traslados/{viaje_id}")
    return reset


def puede_corregir_realizado(usuario, viaje):
    """Un viaje ya realizado lo corrige quien lo cargó, la coordinación o cualquiera de las personas que viajaron."""
    if viaje.get("estado_efectivo") != "REALIZADO":
        return False
    return puede_editar_viaje(usuario, viaje) or any(p.get("usuario_id") == usuario["id"] for p in viaje.get("participantes", []))


def corregir_realizado(viaje_id, datos, motivo, usuario, hoy=None):
    """Corrige fecha, horario o destino de un viaje que ya se hizo (p. ej. se cargó mal). No toca chofer ni vehículo
    ni lo vuelve a pedir: queda en el historial qué cambió, de qué a qué, quién y por qué."""
    hoy = hoy or date.today()
    v = _viaje_editable(viaje_id)
    if v["estado_efectivo"] != "REALIZADO":
        raise ErrorTraslado("Solo se corrigen así los viajes ya realizados; los próximos se reprograman.")
    motivo = re.sub(r"\s+", " ", str(motivo or "")).strip()[:300]
    if not motivo:
        raise ErrorTraslado("Contá brevemente qué se corrige y por qué (queda en el historial).")
    nuevo = _validar_datos(dict(fecha=datos.get("fecha") or v["fecha"], hora_salida=datos.get("hora_salida"), hora_regreso=datos.get("hora_regreso"),
                                unidad_id=datos.get("unidad_id"), destino_otro=datos.get("destino_otro"), modalidad=v["modalidad"]))
    if nuevo["fecha"] >= hoy.isoformat():
        raise ErrorTraslado("Un viaje realizado tiene que tener fecha anterior a hoy.")
    campos = [("fecha", "Fecha"), ("hora_salida", "Salida"), ("hora_regreso", "Regreso"), ("unidad_id", "Unidad"), ("destino_otro", "Destino")]
    cambios = [(c, t) for c, t in campos if (nuevo[c] or None) != (v.get(c) or None)]
    if not cambios:
        raise ErrorTraslado("No cambió nada.")
    nombre_unidad = lambda uid: (db.obtener_unidad(uid) or {}).get("nombre") if uid else None
    with db._conexion() as con:
        con.execute("""UPDATE viajes SET fecha = ?, hora_salida = ?, hora_regreso = ?, unidad_id = ?, destino_otro = ?, actualizado_en = ?
                       WHERE id = ?""", (nuevo["fecha"], nuevo["hora_salida"], nuevo["hora_regreso"], nuevo["unidad_id"], nuevo["destino_otro"],
                                         db._ahora(), viaje_id))
        for c, t in cambios:
            antes, despues = v.get(c), nuevo[c]
            if c == "unidad_id":
                antes, despues = nombre_unidad(antes), nombre_unidad(despues)
            auditar("viaje", viaje_id, f"Corrección: {t.lower()} (viaje realizado)", antes or "—", f"{despues or '—'} · motivo: {motivo}", con=con)
    _vincular(viaje_id)
    return [t for _, t in cambios]


def cancelar(viaje_id, motivo, usuario):
    v = _viaje_editable(viaje_id)
    motivo = (motivo or "").strip()[:300]
    if not motivo:
        raise ErrorTraslado("Contá el motivo de la cancelación.")
    with db._conexion() as con:
        con.execute("UPDATE viajes SET estado = 'CANCELADO', motivo_cancelacion = ?, actualizado_en = ? WHERE id = ?", (motivo, db._ahora(), viaje_id))
        auditar("viaje", viaje_id, "Cancelado", v["estado"], motivo, con=con)
        chofer_user = con.execute("SELECT usuario_id FROM choferes WHERE id = ?", (v["chofer_id"],)).fetchone() if v["chofer_id"] else None
    _vincular(viaje_id)
    if v["estado"] == "A_CONFIRMAR":
        return
    destinos = set(usuarios_coordinadores())
    if chofer_user and chofer_user["usuario_id"]:
        destinos.add(chofer_user["usuario_id"])
    db.notificar_usuarios(list(destinos), f"Se canceló el viaje a {v['destino']} del {v['fecha']}: {motivo}", f"/traslados/{viaje_id}")


def agregar_participante(viaje_id, participante, correccion=False):
    v = _viaje_editable(viaje_id)
    p = _limpiar_participantes([participante])
    if not p:
        raise ErrorTraslado("Indicá el nombre.")
    p = p[0]
    if any((x["usuario_id"] and x["usuario_id"] == p["usuario_id"]) or x["nombre"].lower() == p["nombre"].lower() for x in v["participantes"]):
        raise ErrorTraslado("Esa persona ya está en el viaje.")
    if v.get("circuito") == "DELEGACIONES":
        p["auto_propio"] = 1
    if correccion and v["estado_efectivo"] == "REALIZADO":  # corrección de un viaje que ya se hizo: se registra quién fue
        with db._conexion() as con:
            _insertar_participante(con, viaje_id, p)
            auditar("viaje", viaje_id, "Corrección: persona agregada (viaje realizado)", nuevo=p["nombre"] + (" (por sus propios medios)" if p["auto_propio"] else ""), con=con)
        return
    cap = v.get("vehiculo_capacidad")
    if cap and not p["auto_propio"] and v.get("con_transporte_salida", v["con_transporte"]) + 1 > cap:
        raise ErrorTraslado(f"No hay lugar: el vehículo asignado tiene {cap} plazas" + (" (compartidas con las otras paradas de la misma salida)" if v.get("misma_salida") else "") + ". Podés anotarte en la lista de espera: si se libera un lugar te avisamos.")
    if not p["auto_propio"] and es_amba(v["unidad_id"]) is False:
        raise ErrorTraslado(MENSAJE_SIN_CHOFER)
    with db._conexion() as con:
        _insertar_participante(con, viaje_id, p)
        auditar("viaje", viaje_id, "Persona agregada", nuevo=p["nombre"] + (" (por sus propios medios)" if p["auto_propio"] else ""), con=con)
        _recalcular(con, viaje_id)
    if p.get("usuario_id"):
        import operacion
        operacion.marcar_sumado(viaje_id, p["usuario_id"])
    _vincular(viaje_id)


def _recalcular(con, viaje_id, correccion=False):
    """La modalidad sale de las personas: si todas van en auto propio no hace falta chofer; si alguien necesita
    transporte, el viaje pasa a pedir chofer."""
    v = con.execute("SELECT * FROM viajes WHERE id = ?", (viaje_id,)).fetchone()
    parts = [dict(x) for x in con.execute("SELECT auto_propio FROM viaje_participantes WHERE viaje_id = ?", (viaje_id,))]
    if not parts or v["estado"] == "CANCELADO":
        return
    if correccion:
        return  # corrección de un viaje ya realizado: no vuelve a pedir chofer ni libera el que fue
    mod = _modalidad_de(parts)
    if mod == v["modalidad"]:
        return
    if v["estado"] == "A_CONFIRMAR":  # precargado: cambia la modalidad pero sigue esperando confirmación
        con.execute("UPDATE viajes SET modalidad=?, actualizado_en=? WHERE id=?", (mod, db._ahora(), viaje_id))
    elif mod == "PROPIO":  # se libera el chofer y el auto
        con.execute("UPDATE viajes SET modalidad='PROPIO', estado='CONFIRMADO', chofer_id=NULL, vehiculo_id=NULL, actualizado_en=? WHERE id=?", (db._ahora(), viaje_id))
    else:
        con.execute("UPDATE viajes SET modalidad='CHOFER', estado='SOLICITADO', actualizado_en=? WHERE id=?", (db._ahora(), viaje_id))
    auditar("viaje", viaje_id, "Modalidad", v["modalidad"], mod, con=con)


def cambiar_auto_propio(viaje_id, participante_id, valor, correccion=False):
    v = _viaje_editable(viaje_id)
    p = next((x for x in v["participantes"] if x["id"] == participante_id), None)
    if not p:
        raise ErrorTraslado("Esa persona no está en el viaje.")
    valor = 1 if valor else 0
    if not valor and (v.get("circuito") == "DELEGACIONES" or es_amba(v["unidad_id"]) is False):
        raise ErrorTraslado(MENSAJE_SIN_CHOFER)
    with db._conexion() as con:
        con.execute("UPDATE viaje_participantes SET auto_propio = ? WHERE id = ?", (valor, participante_id))
        auditar("viaje", viaje_id, "Por sus propios medios", "sí" if p["auto_propio"] else "no", "sí" if valor else "no", con=con)
        _recalcular(con, viaje_id, correccion)
    _vincular(viaje_id)
    if valor and not correccion:
        _avisar_espera(viaje_id)


def quitar_participante(viaje_id, participante_id, correccion=False):
    v = _viaje_editable(viaje_id)
    if len(v["participantes"]) <= 1:
        raise ErrorTraslado("El viaje necesita al menos una persona: si no se hace, cancelalo.")
    p = next((x for x in v["participantes"] if x["id"] == participante_id), None)
    if not p:
        raise ErrorTraslado("Esa persona no está en el viaje.")
    realizado = correccion and v["estado_efectivo"] == "REALIZADO"
    with db._conexion() as con:
        db.borrar_con_registro(con, "viaje_participantes", "id = ?", (participante_id,))
        detalle = p["nombre"] + (f" · {p['entrevistas']} audiencia(s)" if p.get("entrevistas") is not None else "")
        auditar("viaje", viaje_id, "Corrección: persona quitada (viaje realizado)" if realizado else "Persona quitada", anterior=detalle, con=con)
        _recalcular(con, viaje_id, realizado)
    if realizado:
        return
    _vincular(viaje_id)
    _avisar_espera(viaje_id)


def _vincular(viaje_id):
    """Ola 4: deja el viaje en su desplazamiento (salida del vehículo). Nunca frena la operación principal."""
    try:
        import operacion
        return operacion.vincular_desplazamiento(viaje_id)
    except Exception:
        import logging
        logging.exception("No se pudo vincular el desplazamiento del viaje %s", viaje_id)


def _avisar_espera(viaje_id):
    try:
        import operacion
        return operacion.avisar_lugar_libre(viaje_id)
    except Exception:
        import logging
        logging.exception("No se pudo avisar a la lista de espera del viaje %s", viaje_id)


# ---------------------------------------------------------------- asignación
def _ocupados(fecha, viaje_ref, excluir_id=None, mismo_desplazamiento=None):
    """Choferes y vehículos con viajes confirmados que se pisan con `viaje_ref` ese día. Los viajes del desplazamiento
    `mismo_desplazamiento` (paradas de la misma salida) no cuentan como choque."""
    ch, ve = {}, {}
    with db._conexion() as con:
        for o in con.execute("SELECT * FROM viajes WHERE fecha = ? AND estado = 'CONFIRMADO' AND id != ?", (fecha, excluir_id or -1)):
            o = dict(o)
            if mismo_desplazamiento and o.get("desplazamiento_id") == mismo_desplazamiento:
                continue
            if _se_pisan(viaje_ref, o):
                if o["chofer_id"]: ch.setdefault(o["chofer_id"], o["id"])
                if o["vehiculo_id"]: ve.setdefault(o["vehiculo_id"], o["id"])
    return ch, ve


def sugerencias(viaje_id):
    v = obtener_viaje(viaje_id)
    n = v["con_transporte"]
    ch_oc, ve_oc = _ocupados(v["fecha"], v, excluir_id=viaje_id)
    with db._conexion() as con:
        choferes = [dict(c) for c in con.execute("SELECT * FROM choferes WHERE estado = 'DISPONIBLE' ORDER BY nombre")]
        vehiculos = [dict(x) for x in con.execute("SELECT * FROM vehiculos WHERE estado = 'DISPONIBLE' ORDER BY capacidad, dominio")]
    import operacion  # ola 4: licencias, reservas, vencimientos y carga semanal pareja
    _, ve_extra = operacion.ocupacion_extra(v["fecha"], v)
    choferes = operacion.ranking_choferes(v, [c for c in choferes if not c["reservado_para"]], ch_oc)
    # chofer y vehículo que ya van a un destino vecino a esa hora: misma salida (no «ocupado»)
    vecinos = [o for o in listar_viajes(desde=v["fecha"], hasta=v["fecha"], estado="CONFIRMADO", modalidad="CHOFER")
               if o["id"] != viaje_id and o.get("chofer_id") and _se_pisan(v, o) and destinos_cercanos(v, o)]
    for o in vecinos:
        if ch_oc.get(o["chofer_id"]) == o["id"]:
            ch_oc.pop(o["chofer_id"])
        if o.get("vehiculo_id") and ve_oc.get(o["vehiculo_id"]) == o["id"]:
            ve_oc.pop(o["vehiculo_id"])
    salida_ch = {o["chofer_id"]: o for o in vecinos}
    salida_ve = {o["vehiculo_id"]: o for o in vecinos if o.get("vehiculo_id")}
    for c in choferes:
        c["ocupado"] = ch_oc.get(c["id"])
    choferes = _ordenar_por_ruta_compartida(v, n, choferes, viaje_id)
    for c in choferes:
        if c["id"] in salida_ch:  # misma salida de v37 (también si está a más de una hora)
            o = salida_ch[c["id"]]
            c["motivo"] = ((c.get("motivo") or "") + " · " if c.get("motivo") else "") + f"misma salida: ya va a {o['destino']}"
            c["ocupado"] = None
    from dgpdh.dominio import habilidades as hab  # propuesta 58: requisitos del viaje
    requisitos = hab.etiquetas(v.get("requisitos"))
    conocidas_ve = frozenset().union(*[hab.etiquetas(x.get("habilidades")) for x in vehiculos]) if vehiculos else frozenset()
    vs = []
    for x in vehiculos:
        if x["reservado_para"] or x["capacidad"] < n:
            continue
        aviso = ve_extra.get(x["id"])
        falta = hab.faltantes(requisitos, hab.etiquetas(x.get("habilidades")), conocidas_ve)
        if falta:
            aviso = (aviso + " · " if aviso else "") + "no cumple: " + hab.texto(falta)
        for campo, nombre in (("vtv_vence", "VTV vencida"), ("seguro_vence", "seguro vencido")):
            if x.get(campo) and x[campo] < v["fecha"]:
                aviso = (aviso + " · " if aviso else "") + nombre
        o = salida_ve.get(x["id"])
        if o:  # ya va a un destino vecino: comparte plazas con esa parada
            libres = x["capacidad"] - o.get("con_transporte_salida", o["con_transporte"])
            if libres < n:
                aviso = (aviso + " · " if aviso else "") + f"misma salida a {o['destino']} sin lugar ({max(libres, 0)} libre(s))"
            else:
                aviso = (aviso + " · " if aviso else "") + f"misma salida: ya va a {o['destino']} ({libres} libre(s))"
        vs.append(dict(x, ocupado=ve_oc.get(x["id"]), aviso=aviso, misma_salida=o["id"] if o else None))
    vs.sort(key=lambda x: (not x["misma_salida"], bool(x["aviso"]) and not x["misma_salida"], bool(x["ocupado"]), x["capacidad"], x["dominio"]))
    return {"personas": n, "choferes": choferes, "vehiculos": vs, "regreso_estimado": operacion.estimar_regreso(v.get("unidad_id"), v.get("destino_otro"), v.get("hora_salida") or "09:00")}


def _ordenar_por_ruta_compartida(v, personas, choferes, viaje_id):
    """Modificación 5: primero quien ya tiene un viaje a ±1 h hacia la misma unidad, complejo o ciudad con lugar; después los
    libres por menos salidas en la semana; después los ocupados en otra zona o sin lugar. Los bloqueos (licencias, requisitos,
    reservas) siguen mandando. La lógica está en dgpdh/dominio/sugerencia_chofer.py."""
    from dgpdh.dominio import sugerencia_chofer as sc

    def mins(h):
        return int(h[:2]) * 60 + int(h[3:5]) if h and len(h) >= 5 else None
    del_dia = [o for o in listar_viajes(desde=v["fecha"], hasta=v["fecha"], estado="CONFIRMADO", modalidad="CHOFER")
               if o["id"] != viaje_id and o.get("chofer_id")]
    viajes = [sc.ViajeDelDia(o["id"], o["chofer_id"], mins(o.get("hora_salida")), destinos_cercanos(v, o),
                             (o["vehiculo_capacidad"] - o.get("con_transporte_salida", o["con_transporte"])) if o.get("vehiculo_capacidad") else None,
                             _se_pisan(v, o)) for o in del_dia]
    entrada = [sc.Chofer(c["id"], c["nombre"], int(c.get("carga") or 0), not c.get("apto", True), c.get("motivo") or "") for c in choferes]
    orden = sc.ordenar(entrada, viajes, personas, mins(v.get("hora_salida")))
    por_id = {c["id"]: c for c in choferes}
    salida = []
    for sg in orden:
        c = dict(por_id[sg.chofer_id])
        c["sugerido"] = sg.preseleccionar
        c["prioridad"] = sg.prioridad
        if sg.prioridad == 1:
            c["motivo"] = sg.etiqueta + (" · " + c["motivo"] if c.get("motivo") else "")
            c["ocupado"] = None  # comparte la salida: no es un choque
            c["comparte_viaje"] = sg.comparte_viaje
        elif sg.prioridad == 3 and "ocupado" not in (c.get("motivo") or ""):
            c["motivo"] = sg.etiqueta
        salida.append(c)
    return salida


def paradas_de_salida(v, chofer_id, vehiculo_id, aceptar_lejanas=False):
    """Otros viajes confirmados del mismo día, con horario que se pisa, que ya tienen ESTE chofer y ESTE vehículo
    (o este chofer y ninguno de los dos con vehículo) y van a un destino vecino. Con `aceptar_lejanas` (la coordinación
    aceptó el aviso) también los de destinos lejanos con el mismo chofer y vehículo."""
    if not chofer_id:
        return []
    out = []
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT id FROM viajes WHERE fecha = ? AND estado = 'CONFIRMADO' AND modalidad = 'CHOFER' "
                                              "AND chofer_id = ? AND id != ?", (v["fecha"], chofer_id, v["id"]))]
    for f in filas:
        o = obtener_viaje(f["id"])
        if not o or (o.get("vehiculo_id") or None) != (vehiculo_id or None) or not _se_pisan(v, o):
            continue
        if aceptar_lejanas or destinos_cercanos(v, o):
            out.append(o)
    return out


def asignar(viaje_id, chofer_id, vehiculo_id, usuario, forzar=False, misma_salida=()):
    """Asigna chofer y vehículo (el vehículo es opcional). Valida estado, capacidad y
    solapamientos; con `forzar` la coordinación puede aceptar un solapamiento."""
    v = _viaje_editable(viaje_id)
    if v["modalidad"] != "CHOFER":
        raise ErrorTraslado("Es un viaje por medios propios: no lleva chofer.")
    with db._conexion() as con:
        ch = con.execute("SELECT * FROM choferes WHERE id = ?", (chofer_id,)).fetchone()
        ve = con.execute("SELECT * FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone() if vehiculo_id else None
    if not ch:
        raise ErrorTraslado("Elegí un chofer.")
    if ch["estado"] != "DISPONIBLE":
        raise ErrorTraslado(f"El chofer {ch['nombre']} figura en estado «{ETIQUETAS_ESTADO_CHOFER[ch['estado']]}».")
    if vehiculo_id and not ve:
        raise ErrorTraslado("El vehículo no existe.")
    if ve:
        if ve["estado"] != "DISPONIBLE":
            raise ErrorTraslado(f"El vehículo {ve['dominio']} está «{ETIQUETAS_ESTADO_VEHICULO[ve['estado']]}».")
        if ve["capacidad"] < v["con_transporte"]:
            raise ErrorTraslado(f"El vehículo {ve['dominio']} tiene {ve['capacidad']} plazas y viajan {v['con_transporte']} en transporte institucional.")
    import operacion
    # Misma salida: otro viaje del día, en horario que se pisa, con ESTE chofer y ESTE vehículo y a un destino vecino
    # (misma unidad, misma ciudad o cerca): es una parada más del mismo recorrido, no un choque.
    juntos = paradas_de_salida(v, chofer_id, ve["id"] if ve else None, aceptar_lejanas=forzar)
    misma_salida = tuple(misma_salida) + tuple(o["id"] for o in juntos)
    if ve and juntos:
        total = v["con_transporte"] + sum(o["con_transporte"] for o in juntos)
        if total > ve["capacidad"]:
            raise ErrorTraslado(f"En la misma salida ({', '.join(o['destino'] for o in juntos)} y {v['destino']}) viajan {total} personas "
                                f"y el {ve['dominio']} tiene {ve['capacidad']} plazas: elegí un vehículo más grande o que alguien vaya por sus propios medios.")
    ch_oc, ve_oc = _ocupados(v["fecha"], v, excluir_id=viaje_id)
    if misma_salida:  # paradas de la misma salida: no son choque
        ch_oc = {k: x for k, x in ch_oc.items() if x not in misma_salida}
        ve_oc = {k: x for k, x in ve_oc.items() if x not in misma_salida}
    ch_extra, ve_extra = operacion.ocupacion_extra(v["fecha"], v)
    choques = []
    if chofer_id in ch_extra: choques.append(f"el chofer {ch['nombre']} está {ch_extra[chofer_id]}")
    if ch["licencia_vence"] and ch["licencia_vence"] < v["fecha"]: choques.append(f"el chofer {ch['nombre']} tiene la licencia de conducir vencida")
    if ve and ve["id"] in ve_extra: choques.append(f"el vehículo {ve['dominio']} está {ve_extra[ve['id']]}")
    if ve:
        for campo, nombre in (("vtv_vence", "la VTV vencida"), ("seguro_vence", "el seguro vencido")):
            if ve[campo] and ve[campo] < v["fecha"]:
                choques.append(f"el vehículo {ve['dominio']} tiene {nombre}")
    if chofer_id in ch_oc: choques.append(f"el chofer {ch['nombre']} ya tiene el viaje #{ch_oc[chofer_id]} en ese horario")
    if ve and ve["id"] in ve_oc: choques.append(f"el vehículo {ve['dominio']} ya está en el viaje #{ve_oc[ve['id']]}")
    if choques and not forzar:
        extra = (" Si es la misma salida con varias paradas, tildá «Aceptar» y asigná de nuevo: quedan como una sola salida."
                 if (chofer_id in ch_oc or (ve and ve["id"] in ve_oc)) else "")
        raise ErrorTraslado("Conflicto: " + " y ".join(choques) + "." + extra)
    with db._conexion() as con:
        con.execute("UPDATE viajes SET chofer_id = ?, vehiculo_id = ?, estado = 'CONFIRMADO', actualizado_en = ? WHERE id = ?",
                    (chofer_id, ve["id"] if ve else None, db._ahora(), viaje_id))
        auditar("viaje", viaje_id, "Asignación" + (" (con conflicto aceptado)" if choques else "")
                + (" — misma salida que " + ", ".join(f"#{o['id']} {o['destino']}" for o in juntos) if juntos else ""),
                f"{v.get('chofer_nombre') or '—'} / {v.get('vehiculo_dominio') or '—'}", f"{ch['nombre']} / {ve['dominio'] if ve else '—'}", con=con)
    _vincular(viaje_id)
    destinos = {v["creado_por_id"]} - {None}
    if ch["usuario_id"]:
        destinos.add(ch["usuario_id"])
    db.notificar_usuarios(list(destinos), f"Viaje a {v['destino']} del {v['fecha']}: chofer {ch['nombre']}" + (f", vehículo {ve['dominio']}" if ve else "") + ".", f"/traslados/{viaje_id}")
    return choques


# ---------------------------------------------------------------- compatibles
def compatibles(fecha, unidad_id=None, destino_otro=None, excluir_id=None, circuito="AMBA"):
    """Viajes del mismo día al mismo destino a los que se podría sumar la persona."""
    out = []
    for v in listar_viajes(desde=fecha, hasta=fecha, circuito=circuito):
        if v["estado"] == "CANCELADO" or v["id"] == excluir_id:
            continue
        mismo = (unidad_id and v["unidad_id"] == int(unidad_id)) or (
            not unidad_id and destino_otro and (v.get("destino_otro") or "").lower() == destino_otro.strip().lower())
        if not mismo:
            continue
        v["puede_sumarse"] = v["modalidad"] == "CHOFER" and (v["plazas_libres"] is None or v["plazas_libres"] > 0)
        out.append(v)
    return out


# ---------------------------------------------------------------- recursos (choferes, vehículos)
def _normalizar(d, campos):
    return {c: (str(d.get(c)).strip() or None) if d.get(c) is not None else None for c in campos}


ETIQUETAS_CAMPO_VEHICULO = {
    "dominio": "Dominio", "marca": "Marca", "modelo": "Modelo", "anio": "Año", "combustible": "Combustible", "capacidad": "Plazas",
    "km": "Kilometraje", "seguro_vence": "Seguro vence", "vtv_vence": "VTV vence", "service_proximo": "Próximo service",
    "reservado_para": "Reservado para", "observaciones": "Observaciones",
}


def guardar_vehiculo(vehiculo_id, d, cambios_hechos=None):
    """Alta o edición de los datos de un vehículo. En la edición solo cambian los campos que vienen en el formulario
    y cada cambio queda en el historial del vehículo («antes → después»)."""
    textos = ("dominio", "marca", "modelo", "combustible", "reservado_para", "seguro_vence", "vtv_vence", "service_proximo", "observaciones")
    numeros = ("capacidad", "anio", "km")
    presentes = [c for c in textos + numeros if c in d] if vehiculo_id else list(textos + numeros)
    v = {c: x for c, x in _normalizar(d, textos).items() if c in presentes}
    if "dominio" in presentes:
        if not v["dominio"]:
            raise ErrorTraslado("Falta el dominio (patente).")
        v["dominio"] = v["dominio"].upper().replace(" ", "")
    try:
        for c in numeros:
            if c in presentes:
                txt = str(d.get(c) or "").strip().replace(".", "")
                v[c] = int(txt) if txt else (0 if c == "capacidad" else None)
    except ValueError:
        raise ErrorTraslado("Plazas, año y kilometraje tienen que ser números.")
    if "capacidad" in v and v["capacidad"] < 1:
        raise ErrorTraslado("La capacidad tiene que ser al menos 1.")
    if v.get("anio") is not None and not 1950 <= v["anio"] <= date.today().year + 1:
        raise ErrorTraslado("El año del vehículo no es válido.")
    if v.get("km") is not None and v["km"] < 0:
        raise ErrorTraslado("El kilometraje no puede ser negativo.")
    for f in ("seguro_vence", "vtv_vence", "service_proximo"):
        if v.get(f):
            v[f] = _fecha(v[f]).isoformat()
    cols = list(v)
    with db._conexion() as con:
        if "dominio" in v:
            rep = con.execute("SELECT id, estado FROM vehiculos WHERE dominio = ? AND id != ?", (v["dominio"], vehiculo_id or -1)).fetchone()
            if rep:
                if rep["estado"] == "BAJA":
                    raise ErrorTraslado(f"El vehículo {v['dominio']} está eliminado (dado de baja): recuperalo desde «Vehículos eliminados» en lugar de cargarlo de nuevo.")
                raise ErrorTraslado("Ya hay un vehículo con ese dominio.")
        if vehiculo_id:
            antes = con.execute("SELECT * FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
            if not antes:
                raise ErrorTraslado("El vehículo no existe.")
            cambios = [c for c in ETIQUETAS_CAMPO_VEHICULO if c in v and (antes[c] if antes[c] != "" else None) != v[c]]
            if not cambios:
                return vehiculo_id
            con.execute("UPDATE vehiculos SET " + ", ".join(f"{c} = ?" for c in cambios) + ", actualizado_en = ? WHERE id = ?",
                        [v[c] for c in cambios] + [db._ahora(), vehiculo_id])
            if cambios_hechos is not None:
                cambios_hechos.extend(ETIQUETAS_CAMPO_VEHICULO[c] for c in cambios)
            for c in cambios:
                auditar("vehiculo", vehiculo_id, f"Dato modificado: {ETIQUETAS_CAMPO_VEHICULO[c]}",
                        anterior="—" if antes[c] is None else antes[c], nuevo="—" if v[c] is None else v[c], con=con)
            return vehiculo_id
        cur = con.execute("INSERT INTO vehiculos (" + ", ".join(cols) + ", creado_en, actualizado_en) VALUES (" + ",".join("?" * (len(cols) + 2)) + ")",
                          [v[c] for c in cols] + [db._ahora(), db._ahora()])
        auditar("vehiculo", cur.lastrowid, "Alta", nuevo=v["dominio"], con=con)
        return cur.lastrowid


def estado_vehiculo(vehiculo_id, estado, motivo=None):
    """Disponible, en taller, suspendido o eliminado. «Eliminar» no borra: lo saca de la flota (no se puede asignar ni
    aparece en las listas) y conserva su historial de viajes; se puede recuperar."""
    if estado not in ESTADOS_VEHICULO:
        raise ErrorTraslado("Estado de vehículo no válido.")
    motivo = (str(motivo or "").strip()[:300]) or None
    with db._conexion() as con:
        ant = con.execute("SELECT estado FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
        if not ant:
            raise ErrorTraslado("El vehículo no existe.")
        con.execute("UPDATE vehiculos SET estado = ?, estado_motivo = ?, actualizado_en = ? WHERE id = ?",
                    (estado, motivo if estado != "DISPONIBLE" else None, db._ahora(), vehiculo_id))
        auditar("vehiculo", vehiculo_id, "Estado", ant["estado"], estado + (f" — {motivo}" if motivo else ""), con=con)
    if estado != "DISPONIBLE":
        _avisar_viajes_afectados("vehiculo_id", vehiculo_id)


def guardar_chofer(chofer_id, d):
    campos = ("nombre", "legajo", "telefono", "email", "licencia_categoria", "licencia_vence", "reservado_para", "observaciones")
    c = _normalizar(d, campos)
    if not c["nombre"]:
        raise ErrorTraslado("Falta el nombre del chofer.")
    if c["licencia_vence"]:
        _fecha(c["licencia_vence"])
    c["usuario_id"] = int(d["usuario_id"]) if str(d.get("usuario_id") or "").isdigit() else None
    c["vehiculo_fijo_id"] = int(d["vehiculo_fijo_id"]) if str(d.get("vehiculo_fijo_id") or "").isdigit() else None
    cols = list(c)
    with db._conexion() as con:
        if chofer_id:
            con.execute("UPDATE choferes SET " + ", ".join(f"{k} = ?" for k in cols) + ", actualizado_en = ? WHERE id = ?",
                        [c[k] for k in cols] + [db._ahora(), chofer_id])
            auditar("chofer", chofer_id, "Modificado", nuevo=c["nombre"], con=con)
            return chofer_id
        cur = con.execute("INSERT INTO choferes (" + ", ".join(cols) + ", creado_en, actualizado_en) VALUES (" + ",".join("?" * (len(cols) + 2)) + ")",
                          [c[k] for k in cols] + [db._ahora(), db._ahora()])
        auditar("chofer", cur.lastrowid, "Alta", nuevo=c["nombre"], con=con)
        return cur.lastrowid


def estado_chofer(chofer_id, estado):
    if estado not in ESTADOS_CHOFER:
        raise ErrorTraslado("Estado de chofer no válido.")
    with db._conexion() as con:
        ant = con.execute("SELECT estado FROM choferes WHERE id = ?", (chofer_id,)).fetchone()
        if not ant:
            raise ErrorTraslado("El chofer no existe.")
        con.execute("UPDATE choferes SET estado = ?, actualizado_en = ? WHERE id = ?", (estado, db._ahora(), chofer_id))
        auditar("chofer", chofer_id, "Estado", ant["estado"], estado, con=con)
    if estado != "DISPONIBLE":
        _avisar_viajes_afectados("chofer_id", chofer_id)


def _avisar_viajes_afectados(columna, recurso_id):
    """Si un chofer/auto deja de estar disponible, la coordinación ve qué viajes futuros quedan afectados."""
    with db._conexion() as con:
        afectados = [r["id"] for r in con.execute(
            f"SELECT id FROM viajes WHERE {columna} = ? AND estado = 'CONFIRMADO' AND fecha >= ?", (recurso_id, date.today().isoformat()))]
    if afectados:
        db.notificar_usuarios(usuarios_coordinadores(), f"Atención: {len(afectados)} viaje(s) futuro(s) tienen asignado un recurso que ya no está disponible (viaje #{afectados[0]}…).", f"/traslados/{afectados[0]}")


def sincronizar_choferes():
    """Todo usuario con rol CHOFER (alta en el panel general de usuarios) tiene su ficha
    de chofer. Si el usuario se desactiva, la ficha pasa a Baja; si se reactiva, a Disponible."""
    with db._conexion() as con:
        for u in con.execute("SELECT id, nombre, activo FROM usuarios WHERE rol = 'CHOFER'").fetchall():
            c = con.execute("SELECT id, estado, nombre FROM choferes WHERE usuario_id = ?", (u["id"],)).fetchone()
            if not c:
                cur = con.execute("INSERT INTO choferes (nombre, usuario_id, estado, creado_en, actualizado_en) VALUES (?,?,?,?,?)",
                                  (u["nombre"], u["id"], "DISPONIBLE" if u["activo"] else "BAJA", db._ahora(), db._ahora()))
                auditar("chofer", cur.lastrowid, "Alta (usuario con rol Chofer)", nuevo=u["nombre"], con=con)
            else:
                if c["nombre"] != u["nombre"]:
                    con.execute("UPDATE choferes SET nombre = ? WHERE id = ?", (u["nombre"], c["id"]))
                if not u["activo"] and c["estado"] != "BAJA":
                    con.execute("UPDATE choferes SET estado = 'BAJA', actualizado_en = ? WHERE id = ?", (db._ahora(), c["id"]))
                    auditar("chofer", c["id"], "Estado", c["estado"], "BAJA", con=con)
                elif u["activo"] and c["estado"] == "BAJA":
                    con.execute("UPDATE choferes SET estado = 'DISPONIBLE', actualizado_en = ? WHERE id = ?", (db._ahora(), c["id"]))
                    auditar("chofer", c["id"], "Estado", "BAJA", "DISPONIBLE", con=con)


def listar_choferes(incluir_baja=True):
    sincronizar_choferes()
    with db._conexion() as con:
        return [dict(f) for f in con.execute(
            """SELECT c.*, u.usuario AS login, v.dominio AS vehiculo_fijo FROM choferes c
               LEFT JOIN usuarios u ON u.id = c.usuario_id LEFT JOIN vehiculos v ON v.id = c.vehiculo_fijo_id
               %s ORDER BY (c.estado = 'BAJA'), c.nombre COLLATE NOCASE""" % ("" if incluir_baja else "WHERE c.estado != 'BAJA'"))]


def listar_vehiculos(incluir_baja=False):
    """Los vehículos de la flota (los eliminados solo con incluir_baja)."""
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM vehiculos %s ORDER BY (estado = 'BAJA'), (estado = 'FUERA_DE_CIRCULACION'), dominio"
                                             % ("" if incluir_baja else "WHERE estado != 'BAJA'"))]


def vencimientos(dias=30, hoy=None):
    """Seguro, VTV, service y licencias que vencen pronto (o ya vencieron)."""
    hoy = hoy or date.today()
    tope = (hoy + timedelta(days=dias)).isoformat()
    out = []
    for v in listar_vehiculos():
        if v["estado"] == "FUERA_DE_CIRCULACION":
            continue
        for campo, etq in (("seguro_vence", "Seguro"), ("vtv_vence", "VTV"), ("service_proximo", "Service")):
            if v[campo] and v[campo] <= tope:
                out.append({"quien": f"{v['dominio']} ({v['marca'] or ''} {v['modelo'] or ''})".strip(), "que": etq, "fecha": v[campo], "vencido": v[campo] < hoy.isoformat()})
    for c in listar_choferes(False):
        if c["licencia_vence"] and c["licencia_vence"] <= tope:
            out.append({"quien": c["nombre"], "que": "Licencia de conducir", "fecha": c["licencia_vence"], "vencido": c["licencia_vence"] < hoy.isoformat()})
    import flota  # matafuego, cubiertas, aceite y demás controles cargados en el panel de flota
    out += flota.vencimientos_controles(dias, hoy)
    return sorted(out, key=lambda x: x["fecha"])


# ---------------------------------------------------------------- recordatorios
def feriados_entre(desde, hasta):
    """Propuesta 69: días no laborables del calendario único (vacío si no se cargó ninguno)."""
    try:
        from dgpdh.servicios import feriados
        with db._conexion() as con:
            return feriados.conjunto(con, _fecha(desde), _fecha(hasta))
    except Exception:
        return frozenset()


def feriados_iso():
    """Todos los días no laborables activos como texto AAAA-MM-DD (lo que esperan los plazos de notas)."""
    try:
        from dgpdh.servicios import feriados
        with db._conexion() as con:
            return {f.isoformat() for f in feriados.conjunto(con)}
    except Exception:
        return set()


DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def dias_de_recordatorio(hoy):
    """(víspera, cierre) de la carga de la semana próxima. Sin feriados: miércoles y jueves. Si el jueves no es hábil, el
    recordatorio se corre al día hábil anterior (propuesta 69). El plazo para marcar «fuera de plazo» no cambia."""
    from dgpdh.dominio import calendario as cal
    jueves = lunes_de(hoy) + timedelta(days=3)
    fer = feriados_entre(jueves - timedelta(days=14), jueves)
    cierre = cal.habil_anterior_o_igual(jueves, fer)
    return cal.habil_anterior(cierre, fer), cierre


def toca_recordatorio(hoy):
    """«Mañana jueves» / «Hoy» si hoy es víspera o día de cierre; None si no corresponde avisar hoy."""
    vispera, cierre = dias_de_recordatorio(hoy)
    if hoy == vispera:
        return f"Mañana {DIAS_SEMANA[cierre.weekday()]}", cierre
    if hoy == cierre:
        return "Hoy", cierre
    return None


def recordatorio_planificacion(hoy=None):
    """Víspera y día del cierre (miércoles y jueves, o antes si hay feriado): avisa a asesores y coordinadores que se
    acerca el cierre de carga de los viajes de rutina de la semana próxima (una vez por día)."""
    hoy = hoy or date.today()
    aviso = toca_recordatorio(hoy)
    if not aviso:
        return 0
    clave = f"traslados_recordatorio_{hoy.isoformat()}"
    if db.obtener_ajuste(clave):
        return 0
    db.guardar_ajuste(clave, "1")
    prox = lunes_de(hoy) + timedelta(days=7)
    cuando = aviso[0]
    destinos = [u["id"] for u in db.listar_usuarios() if u["activo"] and u["rol"] in ("ASESOR", "COORDINADOR") and u.get("acceso_traslados", 1)]
    db.notificar_usuarios(destinos, f"{cuando} vence la carga de los viajes de rutina de la semana del {prox.strftime('%d/%m')} al {(prox + timedelta(days=4)).strftime('%d/%m')}. Después se cargan como inmediatos.", "/traslados")
    return len(destinos)


# ---------------------------------------------------------------- estadísticas
def estadisticas(desde, hasta, alcance, hoy=None, circuito=None):
    """Totales sobre viajes NO cancelados ya realizados (fecha <= hoy) del período, dentro del alcance."""
    hoy = hoy or date.today()
    tope = min(hasta, hoy.isoformat()) if hasta else hoy.isoformat()
    kw = dict(desde=desde, hasta=tope)
    if alcance["tipo"] == "propio":
        kw["usuario_id"] = alcance["usuario_id"]
    elif alcance["tipo"] == "equipo":
        kw["equipo_nombre"] = alcance.get("equipo_nombre") or "?"
    todos = listar_viajes(desde=desde, hasta=hasta, circuito=circuito, **{k: v for k, v in kw.items() if k not in ("desde", "hasta")})
    cancelados = [v for v in todos if v["estado"] == "CANCELADO"]
    hechos = [v for v in todos if v["estado"] == "CONFIRMADO" and v["fecha"] <= hoy.isoformat()]
    sin_asignar = [v for v in todos if v["estado"] == "SOLICITADO"]

    def personas(v):
        if alcance["tipo"] == "equipo":
            return [p for p in v["participantes"] if (p.get("equipo_nombre") or "").lower() == (alcance.get("equipo_nombre") or "").lower()]
        if alcance["tipo"] == "propio":
            return [p for p in v["participantes"] if p.get("usuario_id") == alcance["usuario_id"]] or v["participantes"][:0]
        return v["participantes"]
    n_personas = sum(len(personas(v)) for v in hechos)
    por_unidad, por_equipo, por_mes, por_chofer, por_vehiculo, por_dia = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    for v in hechos:
        por_unidad[v["destino"]] += 1
        por_mes[v["fecha"][:7]] += 1
        por_dia[dias[_fecha(v["fecha"]).weekday()]] += 1
        for p in personas(v):
            por_equipo[p.get("equipo_nombre") or "(sin equipo)"] += 1
        if v["chofer_nombre"]: por_chofer[v["chofer_nombre"]] += 1
        if v["vehiculo_dominio"]: por_vehiculo[v["vehiculo_dominio"]] += 1
    # Criterio del organismo:
    #  * desplazamiento = salida de un vehículo institucional en un día (aunque recorra varias unidades);
    #  * viaje a la unidad = un equipo que va a una unidad un día determinado.
    desplaz = {clave_desplazamiento(v) for v in hechos if v["modalidad"] == "CHOFER"}
    vu = viajes_a_unidad(hechos, personas)
    vu_unidad, vu_equipo = Counter(k[2] for k in vu), Counter(k[3] for k in vu)
    vu_mes, desp_mes = Counter(k[0][:7] for k in vu), Counter(k[0][:7] for k in desplaz)
    # Cumplimiento del plazo del jueves (solo AMBA, donde rige): viajes cargados en plazo / total, por equipo
    plazo = defaultdict(lambda: [0, 0])
    for v in hechos:
        if (v.get("circuito") or "AMBA") != "AMBA" or v.get("origen") == "IMPORTADO":
            continue
        for eq in {(p.get("equipo_nombre") or "").strip() for p in personas(v)} - {""}:
            plazo[eq][0] += 1
            plazo[eq][1] += 0 if v["inmediato"] else 1
    n = len(hechos) or 1
    return {
        "desplazamientos": len(desplaz), "viajes_unidad": len(vu),
        "por_unidad_viajes": vu_unidad.most_common(), "por_equipo_viajes": vu_equipo.most_common(),
        "por_mes_viajes_unidad": sorted(vu_mes.items()), "por_mes_desplazamientos": sorted(desp_mes.items()),
        "cumplimiento_plazo": sorted(({"equipo": eq, "total": t, "en_plazo": ok, "pct": round(100 * ok / t)} for eq, (t, ok) in plazo.items()),
                                     key=lambda x: (x["pct"], -x["total"])),
        "viajes": len(hechos), "personas": n_personas,
        "personas_auto_propio": sum(1 for v in hechos for p in personas(v) if p.get("auto_propio")), "cancelados": len(cancelados), "sin_chofer": len(sin_asignar),
        "con_chofer": sum(1 for v in hechos if v["modalidad"] == "CHOFER"), "propios": sum(1 for v in hechos if v["modalidad"] == "PROPIO"),
        "inmediatos": sum(1 for v in hechos if v["inmediato"]), "reprogramados": sum(1 for v in hechos if v["reprogramaciones"]),
        "pct_propios": round(100 * sum(1 for v in hechos if v["modalidad"] == "PROPIO") / n), "pct_inmediatos": round(100 * sum(1 for v in hechos if v["inmediato"]) / n),
        "ocupacion_media": round(sum(len(v["participantes"]) for v in hechos if v["modalidad"] == "CHOFER") / max(1, sum(1 for v in hechos if v["modalidad"] == "CHOFER")), 1),
        "por_unidad": por_unidad.most_common(), "por_equipo": por_equipo.most_common(), "por_mes": sorted(por_mes.items()),
        "por_chofer": por_chofer.most_common(), "por_vehiculo": por_vehiculo.most_common(), "por_dia": por_dia.most_common(),
        "viajes_detalle": hechos,
    }


def clave_desplazamiento(v):
    """Una salida de vehículo: mismo día y mismo vehículo (o chofer, si todavía no tiene vehículo cargado)."""
    if v.get("vehiculo_id"):
        return (v["fecha"], "vehiculo", v["vehiculo_id"])
    if v.get("chofer_id"):
        return (v["fecha"], "chofer", v["chofer_id"])
    return (v["fecha"], "viaje", v["id"])


def viajes_a_unidad(viajes, personas=None):
    """Conjunto de (fecha, clave de unidad, nombre del destino, equipo): cada equipo que fue a cada unidad cada día.
    Quien no tiene equipo (p. ej. personas sin usuario) solo suma un viaje si nadie de un equipo fue ese día a esa unidad."""
    personas = personas or (lambda v: v["participantes"])
    out, sin_equipo = set(), set()
    for v in viajes:
        unidad = v.get("unidad_id") or (v.get("destino") or "").strip().lower()
        base = (v["fecha"], unidad, v["destino"])
        for p in personas(v):
            eq = (p.get("equipo_nombre") or "").strip()
            if eq:
                out.add(base + (eq,))
            else:
                sin_equipo.add(base)
    con_equipo = {k[:3] for k in out}
    out |= {b + ("(sin equipo)",) for b in sin_equipo if b not in con_equipo}
    return out


# ---------------------------------------------------------------- destinos propuestos por equipo
def unidades_para_equipo(equipo_id=None, equipo_nombre=None, zona=None):
    """Catálogo ordenado para elegir destino: primero las unidades por defecto del equipo, después las que
    más usó (según los viajes registrados) y por último el resto. Cada unidad trae `marca` y `usos`."""
    unidades = db.listar_unidades()
    if zona:
        unidades = [u for u in unidades if (u.get("zona") or "") == zona]
    defecto = {u["id"]: n for n, u in enumerate(db.unidades_de_equipo(equipo_id))} if equipo_id else {}
    usos = {}
    if equipo_nombre:
        with db._conexion() as con:
            for f in con.execute(
                    """SELECT v.unidad_id, COUNT(DISTINCT v.id) AS n FROM viajes v JOIN viaje_participantes p ON p.viaje_id = v.id
                       WHERE v.unidad_id IS NOT NULL AND v.estado != 'CANCELADO' AND p.equipo_nombre = ? COLLATE NOCASE
                       GROUP BY v.unidad_id""", (equipo_nombre,)):
                usos[f["unidad_id"]] = f["n"]
    with db._conexion() as con:  # uso general (todos los equipos), para quien no tiene historial
        usos_todos = {f["unidad_id"]: f["n"] for f in con.execute(
            "SELECT unidad_id, COUNT(*) AS n FROM viajes WHERE unidad_id IS NOT NULL AND estado != 'CANCELADO' GROUP BY unidad_id")}
    for u in unidades:
        u["usos"] = usos.get(u["id"], 0)
        u["usos_todos"] = usos_todos.get(u["id"], 0)
        u["marca"] = "defecto" if u["id"] in defecto else ("frecuente" if u["usos"] >= 3 else "")
    def clave(u):
        if u["id"] in defecto:
            return (0, defecto[u["id"]], 0, u["nombre"].lower())
        if u["usos"]:
            return (1, 0, -u["usos"], u["nombre"].lower())
        if u["usos_todos"]:
            return (2, 0, -u["usos_todos"], u["nombre"].lower())
        return (3, 0, 0, u["nombre"].lower())
    return sorted(unidades, key=clave)


_VACIAS = {"equipo", "de", "del", "la", "el", "los", "las", "y", "e", "area", "unidad", "grupo", "zona", "en", "a"}


def _tokens(texto):
    return {t for t in re.split(r"[^a-z0-9]+", db.normalizar_nombre(texto)) if t and t not in _VACIAS}


def sugerir_unidades_equipo(equipo_nombre, unidades=None, ya=()):
    """Destinos habituales sugeridos para un equipo: las unidades cuyo nombre, siglas o ciudad contienen TODAS
    las palabras del nombre del equipo (así «CPF I» no se confunde con «CPF II» ni «CPF IV»), más las que el
    equipo ya usó en sus viajes. Devuelve [(unidad, motivo)] sin las que ya están marcadas."""
    unidades = unidades if unidades is not None else db.listar_unidades()
    pedido = _tokens(equipo_nombre)
    out, vistos = [], set(ya)
    if pedido:
        for u in unidades:
            if u["id"] in vistos:
                continue
            if pedido <= _tokens(" ".join(str(u.get(k) or "") for k in ("nombre", "alias", "ciudad"))):
                out.append((u, "por el nombre del equipo")); vistos.add(u["id"])
    for u in unidades_para_equipo(None, equipo_nombre):
        if u["usos"] and u["id"] not in vistos:
            out.append((u, f"{u['usos']} viaje(s) del equipo")); vistos.add(u["id"])
    return out[:8]


# ---------------------------------------------------------------- tablero
def resumen(usuario, circuito, hoy=None):
    """Indicadores del tablero de Traslados para el usuario y el circuito que mira."""
    import frecuentes
    frecuentes.generar(hoy)
    hoy = hoy or date.today()
    import flota
    flota.avisar_responsables(hoy)  # propuesta 64: aviso interno al responsable de cada vencimiento (una vez por día)
    h = hoy.isoformat()
    prox = (hoy + timedelta(days=7)).isoformat()
    coord = puede_coordinar(usuario)
    propios = None if coord else usuario["id"]
    viajes = listar_viajes(desde=h, hasta=prox, circuito=circuito, usuario_id=propios)
    vivos = [v for v in viajes if v["estado"] != "CANCELADO"]
    hoy_l = [v for v in vivos if v["fecha"] == h]
    proximos = [v for v in vivos if v["fecha"] > h]
    pendientes = [v for v in listar_viajes(desde=h, circuito=circuito, estado="SOLICITADO", usuario_id=propios)]
    alertas = []
    try:  # entrevistas que la persona tiene que informar de viajes ya hechos (cualquier circuito)
        import tablero_direccion
        pend = tablero_direccion.entrevistas_pendientes(usuario["id"], hoy)
        if pend:
            alertas.append({"nivel": "medio", "texto": f"Informá la cantidad de audiencias de {len(pend)} viaje(s) ya realizado(s)", "url": "/traslados/entrevistas"})
    except Exception:
        pass
    if circuito in ("AMBA", None):  # None = «Todos»
        urgentes = [v for v in pendientes if v["fecha"] <= (hoy + timedelta(days=2)).isoformat()]
        if coord and urgentes:  # la coordinación va directo a la mesa de asignación (todos los pedidos juntos)
            n_pers = sum(v.get("con_transporte") or 0 for v in urgentes)
            alertas.append({"nivel": "alto", "texto": f"{len(urgentes)} pedido(s) de auto sin chofer para hoy y los próximos 2 días ({n_pers} persona(s)): asignar chofer y vehículo",
                            "url": "/traslados/asignar"})
        for v in ([] if coord else urgentes):
            alertas.append({"nivel": "alto", "texto": f"Viaje del {v['fecha'][8:10]}/{v['fecha'][5:7]} a {v['destino']} sin chofer asignado", "url": f"/traslados/{v['id']}"})
        if coord:
            for x in vencimientos(15, hoy):
                alertas.append({"nivel": "alto" if x["vencido"] else "medio", "texto": f"{x['que']} de {x['quien']}: {'vencido' if x['vencido'] else 'vence'} el {x['fecha'][8:10]}/{x['fecha'][5:7]}", "url": "/traslados/recursos"})
            try:  # ola 4: detector de anomalías de los próximos 14 días
                import operacion
                vistos = {x["texto"] for x in alertas}
                alertas += [x for x in operacion.anomalias(h, (hoy + timedelta(days=14)).isoformat()) if x["texto"] not in vistos]
            except Exception:
                import logging
                logging.exception("No se pudieron calcular las anomalías de traslados")
    if usuario["rol"] in ("DIRECTOR", "SUBDIRECTOR"):
        for v in revisiones_pendientes():
            alertas.insert(0, {"nivel": "alto", "texto": f"Viaje prioritario sin vehículos ({v['fecha'][8:10]}/{v['fecha'][5:7]}, {v['destino']}): revisión de la Dirección General",
                               "url": f"/traslados/{v['id']}"})
    a_confirmar = frecuentes.para_confirmar(usuario, hoy=hoy, circuito=circuito)
    if a_confirmar:
        lim = limite_planificacion(a_confirmar[0]["fecha"])
        nivel = "alto" if hoy >= lim else "medio"
        alertas.insert(0, {"nivel": nivel, "texto": f"{len(a_confirmar)} viaje(s) frecuente(s) para confirmar (plazo: jueves {lim.strftime('%d/%m')})", "url": "/traslados#para-confirmar"})
    return {"hoy": hoy_l, "proximos": proximos[:8], "pendientes": pendientes, "alertas": alertas, "a_confirmar": a_confirmar,
            "n_hoy": len(hoy_l), "n_semana": len(vivos), "n_pendientes": len(pendientes), "n_alertas": len(alertas)}


# ---------------------------------------------------------------- mapa
def datos_mapa(circuito=None):
    """Unidades con coordenadas (propias o aproximadas por ciudad/provincia) y cantidad de viajes registrados."""
    import geo_ar
    with db._conexion() as con:
        usos = {f["unidad_id"]: f["n"] for f in con.execute(
            "SELECT unidad_id, COUNT(*) AS n FROM viajes WHERE unidad_id IS NOT NULL AND estado != 'CANCELADO'" + (" AND circuito = ?" if circuito else "") + " GROUP BY unidad_id",
            (circuito,) if circuito else ())}
    puestas, sin_ubicar = [], []
    for u in db.listar_unidades():
        lat, lng, precision = u.get("lat"), u.get("lng"), "exacta"
        if lat is None or lng is None:
            lat, lng, precision = geo_ar.ubicar(u.get("ciudad"), u.get("provincia"))
        item = {"id": u["id"], "nombre": u["nombre"], "tipo": u.get("tipo"), "zona": u.get("zona"), "ciudad": u.get("ciudad"),
                "telefono_guardia": u.get("telefono_guardia"), "horarios_visita": u.get("horarios_visita"), "contactos": u.get("contactos"),
                "provincia": u.get("provincia"), "fuerza": u.get("fuerza"), "correo": u.get("correo"), "direccion": u.get("direccion"),
                "viajes": usos.get(u["id"], 0)}
        if lat is None:
            sin_ubicar.append(item)
        else:
            puestas.append(dict(item, lat=lat, lng=lng, precision=precision))
    return puestas, sin_ubicar


# ---------------------------------------------------------------- disponibilidad de vehículos y revisión de la Dirección
def disponibilidad_vehiculos(fecha, hora_salida=None, hora_regreso=None, excluir_viaje_id=None):
    """¿Quedan vehículos para ese día (y horario, si se indica)? Cuenta los vehículos habilitados (disponibles, no en
    taller, suspendidos ni eliminados), los ya asignados a viajes con chofer que se superponen, los reservados y los
    pedidos de chofer que todavía esperan vehículo. Devuelve un dict con los números y `completo` (no queda ninguno libre)."""
    _fecha(fecha)
    ref = {"hora_salida": hora_salida or None, "hora_regreso": hora_regreso or None}
    habilitados = [v for v in listar_vehiculos() if v["estado"] == "DISPONIBLE"]
    ids = {v["id"] for v in habilitados}
    asignados, sin_vehiculo = set(), 0
    for v in listar_viajes(desde=fecha, hasta=fecha):
        if v["estado"] == "CANCELADO" or v.get("modalidad") != "CHOFER" or v["id"] == excluir_viaje_id:
            continue
        if hora_salida and not _se_pisan(ref, v):  # sin horario se cuenta todo el día
            continue
        if v.get("vehiculo_id"):
            asignados.add(v["vehiculo_id"])
        else:
            sin_vehiculo += 1
    reservados = set()
    try:
        import operacion
        _, ve = operacion.ocupacion_extra(fecha, ref)
        reservados = set(ve) - asignados
    except Exception:
        pass
    ocupados = (asignados | reservados) & ids
    libres = len(ids - ocupados) - sin_vehiculo
    total = len(ids)
    completo = total == 0 or libres <= 0
    partes = []
    if asignados & ids:
        partes.append(f"{len(asignados & ids)} asignado(s)")
    if reservados & ids:
        partes.append(f"{len(reservados & ids)} reservado(s)")
    if sin_vehiculo:
        partes.append(f"{sin_vehiculo} pedido(s) que esperan vehículo")
    dia = f"{fecha[8:10]}/{fecha[5:7]}"
    if total == 0:
        texto = "No hay vehículos disponibles en la flota."
    elif completo:
        texto = f"El {dia} ya están en uso todos los vehículos ({total}): " + ", ".join(partes) + "."
    else:
        texto = f"El {dia} quedan {libres} vehículo(s) libre(s) de {total}."
    return {"fecha": fecha, "total": total, "asignados": len(asignados & ids), "reservados": len(reservados & ids),
            "esperando": sin_vehiculo, "libres": max(libres, 0), "completo": completo, "texto": texto}


def pedir_revision_direccion(viaje_id, motivo, usuario):
    """Viaje prioritario sin vehículos libres: se pide la revisión de la Dirección General (Director y Subdirector/a)."""
    motivo = str(motivo or "").strip()[:500]
    if len(motivo) < 10:
        raise ErrorTraslado("Contá por qué es prioritario (al menos una frase): la Dirección General lo necesita para decidir.")
    v = _viaje_editable(viaje_id)
    if v.get("revision_direccion") == "PENDIENTE":
        raise ErrorTraslado("Este viaje ya está esperando la revisión de la Dirección General.")
    with db._conexion() as con:
        con.execute("""UPDATE viajes SET prioridad_pedida = 1, motivo_prioridad = ?, revision_direccion = 'PENDIENTE',
                       revision_por = NULL, revision_en = NULL, revision_comentario = NULL, actualizado_en = ? WHERE id = ?""",
                    (motivo, db._ahora(), viaje_id))
        auditar("viaje", viaje_id, "Prioridad pedida a la Dirección General", nuevo=motivo, con=con)
    direccion = [u["id"] for u in db.listar_usuarios() if u["activo"] and u["rol"] in ("DIRECTOR", "SUBDIRECTOR")]
    db.notificar_usuarios(direccion, f"Viaje prioritario sin vehículos libres el {v['fecha'][8:10]}/{v['fecha'][5:7]}: {usuario['nombre']} pide su revisión.",
                          f"/traslados/{viaje_id}")


def resolver_revision_direccion(viaje_id, decision, comentario, usuario):
    """La Dirección General decide: PRIORIDAD (la coordinación libera o reasigna un vehículo) o NO_CORRESPONDE."""
    if decision not in ("PRIORIDAD", "NO_CORRESPONDE"):
        raise ErrorTraslado("Decisión no válida.")
    if usuario["rol"] not in ("DIRECTOR", "SUBDIRECTOR"):
        raise ErrorTraslado("La revisión la hace la Dirección General.")
    v = obtener_viaje(viaje_id)
    if not v or v.get("revision_direccion") != "PENDIENTE":
        raise ErrorTraslado("Este viaje no tiene una revisión pendiente.")
    comentario = str(comentario or "").strip()[:500] or None
    if decision == "NO_CORRESPONDE" and not comentario:
        raise ErrorTraslado("Indicá el motivo para quien pidió el viaje.")
    with db._conexion() as con:
        con.execute("UPDATE viajes SET revision_direccion = ?, revision_por = ?, revision_en = ?, revision_comentario = ?, actualizado_en = ? WHERE id = ?",
                    (decision, usuario["nombre"], db._ahora(), comentario, db._ahora(), viaje_id))
        auditar("viaje", viaje_id, "Revisión de la Dirección General", "PENDIENTE", decision + (f" — {comentario}" if comentario else ""), con=con)
    dia = f"{v['fecha'][8:10]}/{v['fecha'][5:7]}"
    if decision == "PRIORIDAD":
        db.notificar_usuarios(usuarios_coordinadores(), f"La Dirección General dio prioridad al viaje del {dia}: liberar o reasignar un vehículo.", f"/traslados/{viaje_id}")
        texto = f"La Dirección General dio prioridad a tu viaje del {dia}: la coordinación le asigna vehículo."
    else:
        texto = f"La Dirección General revisó tu viaje del {dia}: no corresponde darle prioridad. {comentario}"
    if v.get("creado_por_id"):
        db.notificar_usuarios([v["creado_por_id"]], texto, f"/traslados/{viaje_id}")


def revisiones_pendientes():
    with db._conexion() as con:
        ids = [f["id"] for f in con.execute("SELECT id FROM viajes WHERE revision_direccion = 'PENDIENTE' AND estado != 'CANCELADO' ORDER BY fecha")]
    return [obtener_viaje(i) for i in ids]


# ---------------------------------------------------------------- equipos del historial migrado
def _norm_equipo(t):
    t = "".join(c for c in unicodedata.normalize("NFD", str(t or "")) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def equipo_por_alias(nombre, con=None):
    """Equipo del sistema al que se vinculó un nombre de equipo del historial (o None)."""
    k = _norm_equipo(nombre)
    if not k:
        return None
    def q(c):
        f = c.execute("SELECT e.id, e.nombre FROM equipos_alias a JOIN equipos e ON e.id = a.equipo_id WHERE a.alias_norm = ? AND a.anulado_en IS NULL",
                      (k,)).fetchone()
        return dict(f) if f else None
    if con is not None:
        return q(con)
    with db._conexion() as c:
        return q(c)


def equipos_del_historial():
    """Nombres de equipo que figuran en los viajes y no son un equipo del sistema: para vincularlos.
    Devuelve (pendientes, vinculados). Cada pendiente: nombre, personas, viajes, desde, hasta, sugerido."""
    import difflib
    equipos = db.listar_equipos()
    por_norm = {_norm_equipo(e["nombre"]): e for e in equipos}
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(
            """SELECT p.equipo_nombre AS nombre, COUNT(*) AS personas, COUNT(DISTINCT p.viaje_id) AS viajes, MIN(v.fecha) AS desde, MAX(v.fecha) AS hasta
               FROM viaje_participantes p JOIN viajes v ON v.id = p.viaje_id
               WHERE p.equipo_nombre IS NOT NULL AND TRIM(p.equipo_nombre) != ''
               GROUP BY p.equipo_nombre ORDER BY COUNT(*) DESC""")]
        vinculados = [dict(f) for f in con.execute(
            """SELECT a.alias, a.alias_norm, e.nombre AS equipo, a.vinculado_por, a.vinculado_en,
                      (SELECT COUNT(*) FROM viaje_participantes p WHERE p.equipo_original = a.alias) AS personas
               FROM equipos_alias a JOIN equipos e ON e.id = a.equipo_id WHERE a.anulado_en IS NULL ORDER BY a.alias COLLATE NOCASE""")]
    claves = list(por_norm)
    pendientes = []
    for f in filas:
        k = _norm_equipo(f["nombre"])
        if k in por_norm and por_norm[k]["nombre"] == f["nombre"]:
            continue  # ya es exactamente un equipo del sistema
        if k in por_norm:
            f["sugerido"] = por_norm[k]  # mismo nombre con otra escritura (mayúsculas, tildes)
        else:
            cerca = difflib.get_close_matches(k, claves, n=1, cutoff=0.75)
            contiene = [e for kk, e in por_norm.items() if len(k) >= 4 and (k in kk or kk in k)]
            f["sugerido"] = por_norm[cerca[0]] if cerca else (contiene[0] if len(contiene) == 1 else None)
        pendientes.append(f)
    return pendientes, vinculados


def vincular_equipo_historial(nombre, equipo_id, usuario=None):
    """Pasa las participaciones con ese nombre de equipo al equipo del sistema. Guarda el nombre original en
    `equipo_original` (se puede deshacer) y recuerda el vínculo para próximas importaciones. Devuelve cuántas cambió."""
    nombre = str(nombre or "").strip()
    eq = next((e for e in db.listar_equipos() if e["id"] == int(equipo_id)), None) if str(equipo_id).isdigit() else None
    if not nombre or not eq:
        raise ErrorTraslado("Elegí el equipo del sistema.")
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario
    with db._conexion() as con:
        n = con.execute("UPDATE viaje_participantes SET equipo_original = COALESCE(equipo_original, equipo_nombre), equipo_nombre = ? "
                        "WHERE equipo_nombre = ?", (eq["nombre"], nombre)).rowcount
        con.execute("INSERT INTO equipos_alias (alias_norm, alias, equipo_id, vinculado_por, vinculado_en) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(alias_norm) DO UPDATE SET alias = excluded.alias, equipo_id = excluded.equipo_id, "
                    "vinculado_por = excluded.vinculado_por, vinculado_en = excluded.vinculado_en, anulado_en = NULL, anulado_por = NULL",
                    (_norm_equipo(nombre), nombre, eq["id"], quien, db._ahora()))
        auditar("equipo", eq["id"], "Vínculo de equipo del historial", anterior=nombre, nuevo=f"{eq['nombre']} ({n} participación/es)", con=con)
    return n


def deshacer_vinculo_equipo(alias, usuario=None):
    """Vuelve las participaciones a su nombre original y deja el vínculo dado de baja (propuesta 80: no se borra; si se
    vuelve a vincular, se reactiva)."""
    alias = str(alias or "").strip()
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else (usuario or "Sistema")
    with db._conexion() as con:
        n = con.execute("UPDATE viaje_participantes SET equipo_nombre = equipo_original, equipo_original = NULL WHERE equipo_original = ?",
                        (alias,)).rowcount
        con.execute("UPDATE equipos_alias SET anulado_en = ?, anulado_por = ? WHERE alias_norm = ? AND anulado_en IS NULL",
                    (db._ahora(), quien, _norm_equipo(alias)))
        auditar("equipo", 0, "Se deshizo el vínculo de equipo del historial", anterior=alias, nuevo=f"{n} participación/es", con=con)
    return n
