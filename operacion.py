"""Operación de traslados (ola 4 del plan de reformas).

  * Desplazamiento: una salida del vehículo en un día, con una o varias paradas (los viajes) y los equipos de cada una.
    Es el mismo criterio de cómputo de las estadísticas (fecha + vehículo, o fecha + chofer), ahora guardado en la carga.
  * Horas reales, kilometraje y combustible, novedades del viaje (ingreso denegado, demoras, requisas…).
  * Licencias de choferes con fechas, reservas de vehículos para otros usos y lista de espera.
  * Asistentes que PROPONEN (la coordinación decide): planificador semanal, chofer sugerido, hora de regreso estimada,
    anomalías y recordatorio personalizado.
"""
import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import database as db

TIPOS_NOVEDAD = {
    "INGRESO_DENEGADO": "Ingreso denegado",
    "DEMORA_INGRESO": "Demora para ingresar",
    "REQUISA": "Requisa / revisión al equipo",
    "ENTREVISTA_NO_REALIZADA": "No se pudo entrevistar a alguien",
    "SIN_ESPACIO": "Sin espacio adecuado para entrevistar",
    "DEMORA_TRANSITO": "Demora en el traslado",
    "OTRA": "Otra",
}
TIPOS_LICENCIA = {"VACACIONES": "Vacaciones", "LICENCIA": "Licencia", "ENFERMEDAD": "Enfermedad", "COMISION": "Comisión", "FRANCO": "Franco", "OTRA": "Otra"}

MAX_DESPLAZAMIENTOS_SEMANA = 8
MAX_DESPLAZAMIENTOS_DIA = 2
MAX_HORAS_DIA = 10
HORA_TEMPRANA, HORA_TARDE = "06:00", "21:00"
RADIO_KM_AGRUPAR = 25


def _hoy():
    return date.today()


def _mins(h):
    return int(h[:2]) * 60 + int(h[3:5]) if h and len(h) >= 5 else None


def _hhmm(m):
    m = max(0, min(int(round(m)), 23 * 60 + 59))
    return f"{m // 60:02d}:{m % 60:02d}"


# =====================================================================
# 68) DESPLAZAMIENTOS
# =====================================================================
def desplazamiento_para(con, viaje):
    """Desplazamiento del viaje (fecha + vehículo, o fecha + chofer). Lo crea si no existe."""
    if viaje.get("modalidad") != "CHOFER" or not (viaje.get("vehiculo_id") or viaje.get("chofer_id")):
        return None
    if viaje.get("vehiculo_id"):
        f = con.execute("SELECT id FROM desplazamientos WHERE fecha = ? AND vehiculo_id = ? ORDER BY id LIMIT 1",
                        (viaje["fecha"], viaje["vehiculo_id"])).fetchone()
    else:
        f = con.execute("SELECT id FROM desplazamientos WHERE fecha = ? AND vehiculo_id IS NULL AND chofer_id = ? ORDER BY id LIMIT 1",
                        (viaje["fecha"], viaje["chofer_id"])).fetchone()
    if f:
        con.execute("UPDATE desplazamientos SET chofer_id = COALESCE(chofer_id, ?) WHERE id = ?", (viaje.get("chofer_id"), f[0]))
        return f[0]
    cur = con.execute("INSERT INTO desplazamientos (fecha, vehiculo_id, chofer_id, creado_en) VALUES (?, ?, ?, ?)",
                      (viaje["fecha"], viaje.get("vehiculo_id"), viaje.get("chofer_id"), db._ahora()))
    return cur.lastrowid


def vincular_desplazamiento(viaje_id):
    """Después de asignar / reprogramar / cancelar: deja el viaje en el desplazamiento que le corresponde y ordena las paradas."""
    with db._conexion() as con:
        v = con.execute("SELECT * FROM viajes WHERE id = ?", (viaje_id,)).fetchone()
        if not v:
            return None
        v = dict(v)
        anterior = v.get("desplazamiento_id")
        nuevo = desplazamiento_para(con, v) if v["estado"] == "CONFIRMADO" else None
        con.execute("UPDATE viajes SET desplazamiento_id = ? WHERE id = ?", (nuevo, viaje_id))
        for d in {anterior, nuevo} - {None}:
            _ordenar_paradas(con, d)
            if not con.execute("SELECT 1 FROM viajes WHERE desplazamiento_id = ?", (d,)).fetchone():
                db.borrar_con_registro(con, "desplazamientos", "id = ? AND salida_real IS NULL AND km_salida IS NULL", (d,))
    return nuevo


def _ordenar_paradas(con, desplazamiento_id):
    filas = con.execute("SELECT id, hora_salida FROM viajes WHERE desplazamiento_id = ? ORDER BY COALESCE(hora_salida, '99:99'), id",
                        (desplazamiento_id,)).fetchall()
    for i, f in enumerate(filas, 1):
        con.execute("UPDATE viajes SET orden_parada = ? WHERE id = ?", (i, f["id"]))


def relevar_desplazamientos():
    """Arma los desplazamientos de los viajes ya asignados (una sola vez, liviano)."""
    with db._conexion() as con:
        ids = [f[0] for f in con.execute("""SELECT id FROM viajes WHERE desplazamiento_id IS NULL AND estado = 'CONFIRMADO'
                                            AND modalidad = 'CHOFER' AND (vehiculo_id IS NOT NULL OR chofer_id IS NOT NULL)""")]
    for i in ids:
        vincular_desplazamiento(i)
    return len(ids)


def obtener_desplazamiento(desp_id):
    import traslados as tr
    with db._conexion() as con:
        d = con.execute(
            """SELECT d.*, ve.dominio, ve.marca, ve.modelo, ve.capacidad, ve.km AS km_vehiculo, c.nombre AS chofer_nombre, c.telefono AS chofer_telefono
               FROM desplazamientos d LEFT JOIN vehiculos ve ON ve.id = d.vehiculo_id LEFT JOIN choferes c ON c.id = d.chofer_id
               WHERE d.id = ?""", (desp_id,)).fetchone()
        if not d:
            return None
        d = dict(d)
        ids = [f[0] for f in con.execute("SELECT id FROM viajes WHERE desplazamiento_id = ? ORDER BY orden_parada, id", (desp_id,))]
    d["paradas"] = [tr.obtener_viaje(i) for i in ids]
    for p in d["paradas"]:
        with db._conexion() as con:
            u = con.execute("SELECT direccion FROM unidades WHERE id = ?", (p.get("unidad_id"),)).fetchone() if p.get("unidad_id") else None
        p["unidad_direccion"] = u["direccion"] if u else ""
        p["documentos"] = db.documentos_de_viaje(p["id"])
        p["novedades"] = novedades(p["id"])
    d["personas"] = sum(p["con_transporte"] for p in d["paradas"])
    d["equipos"] = sorted({x.get("equipo_nombre") for p in d["paradas"] for x in p["participantes"] if x.get("equipo_nombre")})
    return d


def desplazamientos_del_dia(fecha):
    with db._conexion() as con:
        return [f[0] for f in con.execute("SELECT id FROM desplazamientos WHERE fecha = ? ORDER BY id", (fecha,))]


# =====================================================================
# 69/70) HORAS REALES, KM Y COMBUSTIBLE
# =====================================================================
def cargar_horas_reales(viaje_id, salida, regreso, quien):
    import traslados as tr
    salida = tr.normalizar_hora(salida) if salida else None
    regreso = tr.normalizar_hora(regreso) if regreso else None
    if salida and regreso and _mins(regreso) < _mins(salida):
        raise tr.ErrorTraslado("La hora real de regreso no puede ser anterior a la de salida.")
    with db._conexion() as con:
        v = con.execute("SELECT hora_salida_real, hora_regreso_real FROM viajes WHERE id = ?", (viaje_id,)).fetchone()
        con.execute("UPDATE viajes SET hora_salida_real = ?, hora_regreso_real = ?, real_cargado_por = ?, actualizado_en = ? WHERE id = ?",
                    (salida, regreso, quien, db._ahora(), viaje_id))
        tr.auditar("viaje", viaje_id, "Horario real", f"{v['hora_salida_real'] or '—'}–{v['hora_regreso_real'] or '—'}",
                   f"{salida or '—'}–{regreso or '—'}", con=con)


def cerrar_desplazamiento(desp_id, datos, quien):
    """Carga la salida y el regreso reales, los km y el combustible de la salida. Actualiza los km del vehículo."""
    import flota
    import traslados as tr
    def num(k, tipo=int):
        v = str(datos.get(k) or "").replace(",", ".").strip()
        if not v:
            return None
        try:
            x = tipo(float(v)) if tipo is int else float(v)
        except ValueError:
            raise tr.ErrorTraslado(f"«{v}» no es un número válido.")
        if x < 0:
            raise tr.ErrorTraslado("Los valores no pueden ser negativos.")
        return x
    km_s, km_r = num("km_salida"), num("km_regreso")
    if km_s is not None and km_r is not None and km_r < km_s:
        raise tr.ErrorTraslado("Los km al regreso no pueden ser menos que los km a la salida.")
    salida = tr.normalizar_hora(datos.get("salida_real")) if datos.get("salida_real") else None
    regreso = tr.normalizar_hora(datos.get("regreso_real")) if datos.get("regreso_real") else None
    with db._conexion() as con:
        previo_d = con.execute("SELECT * FROM desplazamientos WHERE id = ?", (desp_id,)).fetchone()
        km_previo = None
        if previo_d and previo_d["vehiculo_id"]:
            km_previo = _km_previo(con, previo_d)
            if km_previo is None:
                vk = con.execute("SELECT km FROM vehiculos WHERE id = ?", (previo_d["vehiculo_id"],)).fetchone()
                km_previo = vk["km"] if vk and vk["km"] and not previo_d["km_regreso"] else None
        con.execute("""UPDATE desplazamientos SET salida_real = ?, regreso_real = ?, km_salida = ?, km_regreso = ?, litros = ?, importe = ?,
                       observaciones = ?, cerrado_por = ?, cerrado_en = ? WHERE id = ?""",
                    (salida, regreso, km_s, km_r, num("litros", float), num("importe", float),
                     (datos.get("observaciones") or "").strip()[:500] or None, quien, db._ahora(), desp_id))
        d = con.execute("SELECT vehiculo_id FROM desplazamientos WHERE id = ?", (desp_id,)).fetchone()
        # las paradas sin horario real toman el de la salida
        if salida or regreso:
            con.execute("""UPDATE viajes SET hora_salida_real = COALESCE(hora_salida_real, ?), hora_regreso_real = COALESCE(hora_regreso_real, ?)
                           WHERE desplazamiento_id = ?""", (salida, regreso, desp_id))
        if salida or regreso or km_r is not None:  # propuesta 73: cerrar la salida deja constancia de que las paradas se hicieron
            from dgpdh.seguridad import auditoria as au
            from dgpdh.servicios import viajes_campo
            actor = au.Actor.desde(db.USUARIO_ACTUAL.get()) if db.USUARIO_ACTUAL.get() else au.Actor(None, quien or "Sistema")
            for (vid,) in con.execute("SELECT id FROM viajes WHERE desplazamiento_id = ? AND estado = 'CONFIRMADO'", (desp_id,)).fetchall():
                viajes_campo.marcar_constancia(con, actor, vid, "CIERRE")
    if d and d["vehiculo_id"] and km_r:
        try:
            flota.actualizar_km(d["vehiculo_id"], km_r, quien)
        except Exception:
            pass
    tr.auditar("desplazamiento", desp_id, "Cierre", None, f"{salida or '—'}–{regreso or '—'} · km {km_s or '—'}→{km_r or '—'} · {num('litros', float) or '—'} l")
    avisos = []  # propuestas 61 y 63: controles que avisan sin bloquear
    try:
        avisos = avisos_odometro({"id": desp_id, "km_salida": km_s, "km_regreso": km_r}, km_previo)
    except Exception:
        import logging
        logging.exception("No se pudo controlar el odómetro")
    if d and d["vehiculo_id"] and num("litros", float) and km_r:
        t = avisar_combustible(d["vehiculo_id"], f"D{desp_id}")
        if t:
            avisos.append(t)
    return avisos


# ---------------------------------------------------------------- 61/63) controles de odómetro y combustible
def _opciones():
    from dgpdh.servicios import opciones_viajes
    return opciones_viajes.leer()


def distancia_estimada(desp_id, con=None):
    """Recorrido estimado de la salida: sede → paradas en orden → sede (haversine × factor). None sin sede o sin coordenadas."""
    from dgpdh.dominio import flota_control as fc
    o = _opciones()
    if o.sede_lat is None or o.sede_lng is None:
        return None
    def q(c):
        return c.execute("""SELECT u.lat, u.lng FROM viajes v JOIN unidades u ON u.id = v.unidad_id WHERE v.desplazamiento_id = ?
                            AND v.estado = 'CONFIRMADO' ORDER BY v.orden_parada, v.id""", (desp_id,)).fetchall()
    if con is not None:
        filas = q(con)
    else:
        with db._conexion() as c:
            filas = q(c)
    puntos = [(float(f[0]), float(f[1])) for f in filas if f[0] is not None and f[1] is not None]
    if not puntos or len(puntos) < len(filas):
        return None
    try:
        from dgpdh.servicios import distancias
        with db._conexion() as c:
            km = distancias.recorrido_km(c, [(o.sede_lat, o.sede_lng)] + puntos)
        if km is not None:
            return km
    except Exception:
        pass
    return fc.recorrido_km([(o.sede_lat, o.sede_lng)] + puntos, o.factor_ruta)


def avisos_odometro(d, km_previo):
    from dgpdh.dominio import flota_control as fc
    return fc.avisos_odometro(d.get("km_salida"), d.get("km_regreso"), km_previo, distancia_estimada(d["id"]), _opciones().desvio_odometro_pct)


def _km_previo(con, d):
    f = con.execute("""SELECT km_regreso FROM desplazamientos WHERE vehiculo_id = ? AND km_regreso IS NOT NULL AND (fecha < ? OR (fecha = ? AND id < ?))
                       ORDER BY fecha DESC, id DESC LIMIT 1""", (d["vehiculo_id"], d["fecha"], d["fecha"], d["id"])).fetchone()
    r = con.execute("SELECT MAX(km) FROM vehiculo_registros WHERE vehiculo_id = ? AND km IS NOT NULL AND fecha < ?", (d["vehiculo_id"], d["fecha"])).fetchone()
    candidatos = [x for x in ((f[0] if f else None), (r[0] if r else None)) if x is not None]
    return max(candidatos) if candidatos else None


def cargas_combustible(vehiculo_id):
    """Cargas con km: las del panel de flota y las del cierre de cada salida."""
    from dgpdh.dominio import flota_control as fc
    with db._conexion() as con:
        filas = [fc.Carga(f"R{f['id']}", f["fecha"], int(f["km"]), float(f["litros"])) for f in con.execute(
            "SELECT id, fecha, km, litros FROM vehiculo_registros WHERE vehiculo_id = ? AND tipo = 'COMBUSTIBLE' AND km IS NOT NULL AND litros > 0", (vehiculo_id,))]
        filas += [fc.Carga(f"D{f['id']}", f["fecha"], int(f["km_regreso"]), float(f["litros"])) for f in con.execute(
            "SELECT id, fecha, km_regreso, litros FROM desplazamientos WHERE vehiculo_id = ? AND km_regreso IS NOT NULL AND litros > 0", (vehiculo_id,))]
    return filas


def rendimiento_combustible(vehiculo_id):
    from dgpdh.dominio import flota_control as fc
    return fc.rendimientos(cargas_combustible(vehiculo_id), _opciones().desvio_combustible_pct)


def avisar_combustible(vehiculo_id, carga_id):
    """Propuesta 63: si la carga recién registrada se aparta del rendimiento habitual, aviso interno a la coordinación."""
    import traslados as tr
    try:
        r = next((x for x in rendimiento_combustible(vehiculo_id) if x.carga.id == carga_id), None)
        if not r or not r.fuera:
            return None
        with db._conexion() as con:
            dom = con.execute("SELECT dominio FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
        texto = (f"Revisar la carga de combustible de {dom['dominio'] if dom else 'un vehículo'} del {r.carga.fecha[8:10]}/{r.carga.fecha[5:7]}: "
                 f"rinde {r.km_por_litro} km/l y lo habitual es {r.mediana} km/l ({r.desvio_pct:+d} %).")
        db.notificar_usuarios([u["id"] for u in db.listar_usuarios() if u["activo"] and tr.puede_coordinar(u)], texto, f"/traslados/vehiculos/{vehiculo_id}#combustible")
        return texto
    except Exception:
        import logging
        logging.exception("No se pudo revisar el rendimiento de combustible")
        return None


def consumo(desde=None, hasta=None):
    """Km, litros e importe por vehículo en el período (desplazamientos cerrados)."""
    sql = """SELECT ve.dominio, COUNT(*) AS salidas, SUM(d.km_regreso - d.km_salida) AS km, SUM(d.litros) AS litros, SUM(d.importe) AS importe
             FROM desplazamientos d JOIN vehiculos ve ON ve.id = d.vehiculo_id
             WHERE d.km_salida IS NOT NULL AND d.km_regreso IS NOT NULL"""
    p = []
    if desde:
        sql += " AND d.fecha >= ?"; p.append(desde)
    if hasta:
        sql += " AND d.fecha <= ?"; p.append(hasta)
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(sql + " GROUP BY ve.id ORDER BY km DESC", p)]
    for f in filas:
        f["km_por_litro"] = round(f["km"] / f["litros"], 1) if f.get("litros") and f.get("km") else None
    return filas


# =====================================================================
# 76) NOVEDADES DEL VIAJE
# =====================================================================
def agregar_novedad(viaje_id, tipo, detalle, minutos, quien):
    import traslados as tr
    if tipo not in TIPOS_NOVEDAD:
        raise tr.ErrorTraslado("Elegí el tipo de novedad.")
    m = None
    if str(minutos or "").strip():
        try:
            m = max(0, int(float(str(minutos).replace(",", "."))))
        except ValueError:
            raise tr.ErrorTraslado("Los minutos tienen que ser un número.")
    with db._conexion() as con:
        con.execute("INSERT INTO viaje_novedades (viaje_id, tipo, detalle, minutos, registrado_por, fecha) VALUES (?,?,?,?,?,?)",
                    (viaje_id, tipo, (detalle or "").strip()[:1000] or None, m, quien, db._ahora()))
        tr.auditar("viaje", viaje_id, "Novedad", None, TIPOS_NOVEDAD[tipo] + (f": {detalle}" if detalle else ""), con=con)


def novedades(viaje_id):
    with db._conexion() as con:
        return [dict(f, etiqueta=TIPOS_NOVEDAD.get(f["tipo"], f["tipo"])) for f in
                con.execute("SELECT * FROM viaje_novedades WHERE viaje_id = ? ORDER BY id", (viaje_id,))]


def novedades_por_unidad(desde=None, hasta=None):
    sql = """SELECT COALESCE(u.nombre, v.destino_otro, '—') AS unidad, n.tipo, COUNT(*) AS n, AVG(n.minutos) AS minutos
             FROM viaje_novedades n JOIN viajes v ON v.id = n.viaje_id LEFT JOIN unidades u ON u.id = v.unidad_id WHERE 1=1"""
    p = []
    if desde:
        sql += " AND v.fecha >= ?"; p.append(desde)
    if hasta:
        sql += " AND v.fecha <= ?"; p.append(hasta)
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(sql + " GROUP BY unidad, n.tipo ORDER BY n DESC", p)]
    por = defaultdict(lambda: {"total": 0, "tipos": []})
    for f in filas:
        por[f["unidad"]]["total"] += f["n"]
        por[f["unidad"]]["tipos"].append((TIPOS_NOVEDAD.get(f["tipo"], f["tipo"]), f["n"], round(f["minutos"]) if f["minutos"] else None))
    return sorted(({"unidad": k, **v} for k, v in por.items()), key=lambda x: -x["total"])


# =====================================================================
# 71/72) LICENCIAS DE CHOFERES Y RESERVAS DE VEHÍCULOS
# =====================================================================
def agregar_licencia(chofer_id, desde, hasta, tipo, observacion, quien):
    import traslados as tr
    if tipo not in TIPOS_LICENCIA:
        raise tr.ErrorTraslado("Elegí el tipo de licencia.")
    try:
        d1, d2 = date.fromisoformat(desde), date.fromisoformat(hasta)
    except (TypeError, ValueError):
        raise tr.ErrorTraslado("Indicá desde y hasta.")
    if d2 < d1:
        raise tr.ErrorTraslado("La fecha «hasta» es anterior a «desde».")
    with db._conexion() as con:
        con.execute("INSERT INTO chofer_licencias (chofer_id, desde, hasta, tipo, observacion, cargado_por, creado_en) VALUES (?,?,?,?,?,?,?)",
                    (chofer_id, desde, hasta, tipo, (observacion or "").strip()[:300] or None, quien, db._ahora()))
    tr.auditar("chofer", chofer_id, "Licencia", None, f"{TIPOS_LICENCIA[tipo]} del {desde} al {hasta}")
    return afectados_por_licencia(chofer_id, desde, hasta)


def anular_licencia(lic_id, quien, motivo=None):
    """Propuesta 80: quitar una licencia es una baja lógica (queda quién, cuándo y por qué) y se puede recuperar."""
    import traslados as tr
    motivo = (motivo or "").strip()[:300] or "Quitada desde la pantalla de licencias"
    with db._conexion() as con:
        f = con.execute("SELECT * FROM chofer_licencias WHERE id = ? AND anulada_en IS NULL", (lic_id,)).fetchone()
        if not f:
            raise tr.ErrorTraslado("Esa licencia no existe o ya estaba quitada.")
        con.execute("UPDATE chofer_licencias SET anulada_en = ?, anulada_por = ?, motivo_anulacion = ? WHERE id = ?", (db._ahora(), quien, motivo, lic_id))
        tr.auditar("chofer", f["chofer_id"], "Licencia quitada", f"{TIPOS_LICENCIA.get(f['tipo'], f['tipo'])} del {f['desde']} al {f['hasta']}", motivo, con=con)
        from dgpdh.seguridad import auditoria as au
        au.registrar(con, accion="Licencia de chofer anulada", entidad="chofer_licencias", entidad_id=lic_id, antes=dict(f), motivo=motivo,
                     actor=au.Actor.desde(db.USUARIO_ACTUAL.get()))


def recuperar_licencia(lic_id, quien):
    import traslados as tr
    with db._conexion() as con:
        f = con.execute("SELECT * FROM chofer_licencias WHERE id = ? AND anulada_en IS NOT NULL", (lic_id,)).fetchone()
        if not f:
            raise tr.ErrorTraslado("Esa licencia no está entre las quitadas.")
        con.execute("UPDATE chofer_licencias SET anulada_en = NULL, anulada_por = NULL, motivo_anulacion = NULL WHERE id = ?", (lic_id,))
        tr.auditar("chofer", f["chofer_id"], "Licencia recuperada", None, f"{TIPOS_LICENCIA.get(f['tipo'], f['tipo'])} del {f['desde']} al {f['hasta']}", con=con)
        from dgpdh.seguridad import auditoria as au
        au.registrar(con, accion="Licencia de chofer recuperada", entidad="chofer_licencias", entidad_id=lic_id, despues=dict(f, anulada_en=None),
                     actor=au.Actor.desde(db.USUARIO_ACTUAL.get()))
    return afectados_por_licencia(f["chofer_id"], f["desde"], f["hasta"])


def borrar_licencia(lic_id, quien="Sistema", motivo=None):
    """Nombre histórico: ya no borra, anula (propuesta 80)."""
    anular_licencia(lic_id, quien, motivo)


def licencia_en(chofer_id, fecha):
    with db._conexion() as con:
        f = con.execute("""SELECT * FROM chofer_licencias WHERE chofer_id = ? AND desde <= ? AND hasta >= ? AND anulada_en IS NULL
                           ORDER BY id DESC LIMIT 1""", (chofer_id, fecha, fecha)).fetchone()
    return dict(f) if f else None


def licencias(desde=None, hasta=None, chofer_id=None, anuladas=False):
    sql = "SELECT l.*, c.nombre AS chofer FROM chofer_licencias l JOIN choferes c ON c.id = l.chofer_id WHERE "
    sql += "l.anulada_en IS NOT NULL" if anuladas else "l.anulada_en IS NULL"
    p = []
    if desde:
        sql += " AND l.hasta >= ?"; p.append(desde)
    if hasta:
        sql += " AND l.desde <= ?"; p.append(hasta)
    if chofer_id:
        sql += " AND l.chofer_id = ?"; p.append(chofer_id)
    with db._conexion() as con:
        return [dict(f, etiqueta=TIPOS_LICENCIA.get(f["tipo"], f["tipo"])) for f in con.execute(sql + " ORDER BY l.desde", p)]


def afectados_por_licencia(chofer_id, desde, hasta):
    """Viajes ya asignados a ese chofer dentro de la licencia (para reasignar)."""
    with db._conexion() as con:
        return [dict(f) for f in con.execute(
            "SELECT id, fecha FROM viajes WHERE chofer_id = ? AND estado = 'CONFIRMADO' AND fecha BETWEEN ? AND ?", (chofer_id, desde, hasta))]


def calendario_choferes(desde, dias=28):
    """Grilla choferes × días: licencia (tipo), viajes asignados o libre."""
    import traslados as tr
    fechas = [desde + timedelta(days=i) for i in range(dias)]
    with db._conexion() as con:
        choferes = [dict(c) for c in con.execute("SELECT * FROM choferes WHERE estado != 'BAJA' ORDER BY nombre")]
        asignados = Counter((f["chofer_id"], f["fecha"]) for f in con.execute(
            "SELECT chofer_id, fecha FROM viajes WHERE estado = 'CONFIRMADO' AND chofer_id IS NOT NULL AND fecha BETWEEN ? AND ?",
            (fechas[0].isoformat(), fechas[-1].isoformat())))
    lic = licencias(fechas[0].isoformat(), fechas[-1].isoformat())
    filas = []
    for c in choferes:
        celdas = []
        for f in fechas:
            fi = f.isoformat()
            l = next((x for x in lic if x["chofer_id"] == c["id"] and x["desde"] <= fi <= x["hasta"]), None)
            celdas.append({"fecha": fi, "licencia": l["etiqueta"] if l else None, "viajes": asignados.get((c["id"], fi), 0),
                           "finde": f.weekday() >= 5})
        filas.append({"chofer": c, "celdas": celdas, "estado": tr.ETIQUETAS_ESTADO_CHOFER.get(c["estado"], c["estado"])})
    return {"fechas": fechas, "filas": filas}


def agregar_reserva(vehiculo_id, fecha, desde_hora, hasta_hora, motivo, solicitante, quien, chofer_id=None):
    import traslados as tr
    if not (motivo or "").strip():
        raise tr.ErrorTraslado("Indicá para qué se reserva el vehículo.")
    try:
        date.fromisoformat(fecha)
    except (TypeError, ValueError):
        raise tr.ErrorTraslado("Indicá la fecha.")
    dh = tr.normalizar_hora(desde_hora) if desde_hora else None
    hh = tr.normalizar_hora(hasta_hora) if hasta_hora else None
    choque = [v for v in tr.listar_viajes(desde=fecha, hasta=fecha) if v["vehiculo_id"] == vehiculo_id and v["estado"] == "CONFIRMADO"
              and tr._se_pisan(v, {"hora_salida": dh or "00:00", "hora_regreso": hh or "23:59"})]
    with db._conexion() as con:
        cur = con.execute("""INSERT INTO vehiculo_reservas (vehiculo_id, fecha, desde_hora, hasta_hora, motivo, solicitante, chofer_id, cargado_por, creado_en)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                          (vehiculo_id, fecha, dh, hh, motivo.strip()[:300], (solicitante or "").strip()[:120] or None, chofer_id, quien, db._ahora()))
    tr.auditar("vehiculo", vehiculo_id, "Reserva", None, f"{fecha} {dh or ''}–{hh or ''}: {motivo}")
    return cur.lastrowid, choque


def cancelar_reserva(res_id, quien):
    with db._conexion() as con:
        con.execute("UPDATE vehiculo_reservas SET estado = 'CANCELADA' WHERE id = ?", (res_id,))


def reservas(desde=None, hasta=None, vehiculo_id=None):
    sql = """SELECT r.*, ve.dominio, c.nombre AS chofer FROM vehiculo_reservas r JOIN vehiculos ve ON ve.id = r.vehiculo_id
             LEFT JOIN choferes c ON c.id = r.chofer_id WHERE r.estado = 'ACTIVA'"""
    p = []
    if desde:
        sql += " AND r.fecha >= ?"; p.append(desde)
    if hasta:
        sql += " AND r.fecha <= ?"; p.append(hasta)
    if vehiculo_id:
        sql += " AND r.vehiculo_id = ?"; p.append(vehiculo_id)
    with db._conexion() as con:
        return [dict(f) for f in con.execute(sql + " ORDER BY r.fecha, r.desde_hora", p)]


def ocupacion_extra(fecha, viaje_ref):
    """Choferes con licencia y vehículos reservados que chocan con el viaje: ({chofer_id: motivo}, {vehiculo_id: motivo})."""
    import traslados as tr
    ch, ve = {}, {}
    for l in licencias(fecha, fecha):
        ch[l["chofer_id"]] = f"{l['etiqueta'].lower()} hasta el {l['hasta'][8:10]}/{l['hasta'][5:7]}"
    for r in reservas(fecha, fecha):
        if tr._se_pisan(viaje_ref, {"hora_salida": r["desde_hora"] or "00:00", "hora_regreso": r["hasta_hora"] or "23:59"}):
            ve[r["vehiculo_id"]] = f"reservado: {r['motivo']}"
            if r.get("chofer_id"):
                ch.setdefault(r["chofer_id"], f"con el vehículo reservado: {r['motivo']}")
    return ch, ve


# =====================================================================
# 73) LISTA DE ESPERA
# =====================================================================
def anotar_en_espera(viaje_id, usuario, motivo="RUTINA", detalle=None):
    import traslados as tr
    from dgpdh.dominio import espera as de
    motivo = motivo if motivo in de.MOTIVOS else "RUTINA"
    v = tr.obtener_viaje(viaje_id)
    if not v or v["estado"] == "CANCELADO":
        raise tr.ErrorTraslado("El viaje no existe o está cancelado.")
    if any(p.get("usuario_id") == usuario["id"] for p in v["participantes"]):
        raise tr.ErrorTraslado("Ya estás en el viaje.")
    with db._conexion() as con:
        if con.execute("SELECT 1 FROM viaje_espera WHERE viaje_id = ? AND usuario_id = ? AND estado = 'ESPERANDO'", (viaje_id, usuario["id"])).fetchone():
            raise tr.ErrorTraslado("Ya estás en la lista de espera de este viaje.")
        con.execute("INSERT INTO viaje_espera (viaje_id, usuario_id, nombre, creado_en, motivo, motivo_detalle) VALUES (?,?,?,?,?,?)",
                    (viaje_id, usuario["id"], usuario["nombre"], db._ahora(), motivo, (str(detalle or "").strip()[:300] or None)))
        tr.auditar("viaje", viaje_id, "Lista de espera", None, f"{usuario['nombre']} · {de.MOTIVOS[motivo].lower()}", con=con)


def espera_de(viaje_id):
    """Propuesta 75: en el orden sugerido (urgencias primero, después antigüedad), o en el que fijó la coordinación."""
    from dgpdh.dominio import espera as de
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT * FROM viaje_espera WHERE viaje_id = ? AND estado IN ('ESPERANDO', 'AVISADO')", (viaje_id,))]
    lista = de.ordenar(filas)
    for f in lista:
        f["motivo_etiqueta"] = de.MOTIVOS.get(f.get("motivo") or "RUTINA", "Rutina")
        f["urgente"] = de.es_urgente(f.get("motivo"))
    return lista


def reordenar_espera(viaje_id, espera_id, hacia, usuario):
    """La coordinación sube o baja a alguien en la lista: queda el orden fijado, con quién y cuándo."""
    import traslados as tr
    from dgpdh.dominio import espera as de
    actual = [e["id"] for e in espera_de(viaje_id)]
    nuevo = de.mover(actual, int(espera_id), hacia)
    if nuevo == actual:
        return False
    with db._conexion() as con:
        for k, eid in enumerate(nuevo, 1):
            con.execute("UPDATE viaje_espera SET orden_manual = ?, orden_por = ?, orden_en = ? WHERE id = ?", (k, usuario["nombre"], db._ahora(), eid))
        nombres = {e["id"]: e["nombre"] for e in espera_de(viaje_id)}
        tr.auditar("viaje", viaje_id, "Orden de la lista de espera", ", ".join(nombres[i] for i in actual), ", ".join(nombres[i] for i in nuevo), con=con)
    return True


def salir_de_espera(viaje_id, usuario_id):
    with db._conexion() as con:
        con.execute("UPDATE viaje_espera SET estado = 'RETIRADO' WHERE viaje_id = ? AND usuario_id = ? AND estado IN ('ESPERANDO', 'AVISADO')",
                    (viaje_id, usuario_id))


def avisar_lugar_libre(viaje_id):
    """Si se liberó lugar en el vehículo, avisa a la primera persona de la lista de espera (y a la coordinación)."""
    import traslados as tr
    v = tr.obtener_viaje(viaje_id)
    if not v or v["estado"] == "CANCELADO":
        return None
    libres = v.get("plazas_libres")
    if libres is not None and libres <= 0:
        return None
    with db._conexion() as con:
        f = next((e for e in espera_de(viaje_id) if e["estado"] == "ESPERANDO"), None)  # propuesta 75: el primero del orden sugerido
        if not f:
            return None
        con.execute("UPDATE viaje_espera SET estado = 'AVISADO', avisado_en = ? WHERE id = ?", (db._ahora(), f["id"]))
    texto = f"Se liberó un lugar en el viaje del {v['fecha'][8:10]}/{v['fecha'][5:7]} a {v['destino']}: si todavía querés ir, sumate."
    destinos = [f["usuario_id"]] if f["usuario_id"] else []
    destinos += [u["id"] for u in db.listar_usuarios() if u["activo"] and tr.puede_coordinar(u)]
    db.notificar_usuarios(destinos, texto, f"/traslados/{viaje_id}")
    return f["usuario_id"]


def marcar_sumado(viaje_id, usuario_id):
    with db._conexion() as con:
        con.execute("UPDATE viaje_espera SET estado = 'SUMADO' WHERE viaje_id = ? AND usuario_id = ? AND estado IN ('ESPERANDO', 'AVISADO')",
                    (viaje_id, usuario_id))


# =====================================================================
# 17) CHOFER SUGERIDO
# =====================================================================
def carga_semanal(fecha):
    """Desplazamientos (salidas) de cada chofer en la semana de `fecha`."""
    import traslados as tr
    lunes = tr.lunes_de(date.fromisoformat(fecha) if isinstance(fecha, str) else fecha)
    domingo = lunes + timedelta(days=6)
    vistos = defaultdict(set)
    for v in tr.listar_viajes(desde=lunes.isoformat(), hasta=domingo.isoformat()):
        if v["estado"] == "CONFIRMADO" and v.get("chofer_id") and v["modalidad"] == "CHOFER":
            vistos[v["chofer_id"]].add(tr.clave_desplazamiento(v))
    return {k: len(s) for k, s in vistos.items()}


def ranking_choferes(viaje, choferes, ocupados, extra_carga=None):
    """Ordena los choferes disponibles: primero los libres, sin licencia ni licencia de conducir vencida, con menos salidas
    en la semana y, a igualdad, el que tiene como vehículo fijo uno libre. Devuelve [{..., motivo, apto}]."""
    from dgpdh.dominio import habilidades as hab
    carga = carga_semanal(viaje["fecha"])
    for k, n in (extra_carga or {}).items():  # salidas ya propuestas por el planificador en esta misma semana
        carga[k] = carga.get(k, 0) + n
    ch_extra, _ = ocupacion_extra(viaje["fecha"], viaje)
    requisitos = hab.etiquetas(viaje.get("requisitos"))  # propuesta 58
    conocidas = frozenset().union(*[hab.etiquetas(c.get("habilidades")) for c in choferes]) if choferes else frozenset()
    jornadas = jornadas_choferes([c["id"] for c in choferes], viaje["fecha"]) if choferes else {}
    salida = []
    for c in choferes:
        motivos, apto = [], True
        if c.get("licencia_vence") and c["licencia_vence"] < viaje["fecha"]:
            motivos.append("licencia de conducir vencida"); apto = False
        if c["id"] in ch_extra:
            motivos.append(ch_extra[c["id"]]); apto = False
        falta = hab.faltantes(requisitos, hab.etiquetas(c.get("habilidades")), conocidas)
        if falta:
            motivos.append("no cumple: " + hab.texto(falta)); apto = False
        corto = descanso_corto(jornadas, c["id"], viaje)  # propuesta 59
        if corto:
            motivos.append(corto)
        if ocupados.get(c["id"]):
            motivos.append(f"ocupado en el viaje #{ocupados[c['id']]}")
        n = carga.get(c["id"], 0)
        motivos.append(f"{n} salida{'s' if n != 1 else ''} esta semana")
        salida.append(dict(c, apto=apto, carga=n, motivo=" · ".join(motivos), descanso_corto=bool(corto)))
    salida.sort(key=lambda c: (not c["apto"], bool(ocupados.get(c["id"])), c["descanso_corto"], c["carga"], c["nombre"]))
    if salida and salida[0]["apto"] and not ocupados.get(salida[0]["id"]):
        salida[0]["sugerido"] = True
    return salida


def descanso_minimo_horas():
    try:
        from dgpdh.servicios import opciones_viajes
        return opciones_viajes.leer().descanso_minimo_horas
    except Exception:
        return 10


def jornadas_choferes(chofer_ids, fecha):
    """{(chofer_id, fecha): (primera salida, último regreso)} del día anterior, el del viaje y el siguiente (horas reales si están)."""
    import traslados as tr
    f = date.fromisoformat(fecha) if isinstance(fecha, str) else fecha
    out = {}
    for v in tr.listar_viajes(desde=(f - timedelta(days=1)).isoformat(), hasta=(f + timedelta(days=1)).isoformat(), estado="CONFIRMADO"):
        if v.get("chofer_id") not in chofer_ids or v["modalidad"] != "CHOFER":
            continue
        s, r = v.get("hora_salida_real") or v.get("hora_salida"), v.get("hora_regreso_real") or v.get("hora_regreso")
        k = (v["chofer_id"], v["fecha"])
        a, b = out.get(k, (None, None))
        out[k] = (min(x for x in (a, s) if x) if (a or s) else None, max(x for x in (b, r) if x) if (b or r) else None)
    return out


def descanso_corto(jornadas, chofer_id, viaje):
    """Propuesta 59: texto si asignarle el viaje deja menos del descanso mínimo entre jornadas (avisa, no descarta)."""
    from dgpdh.dominio import habilidades as hab
    minimo = descanso_minimo_horas()
    if not minimo:
        return None
    f = date.fromisoformat(viaje["fecha"])
    antes, despues = (f - timedelta(days=1)).isoformat(), (f + timedelta(days=1)).isoformat()
    previo = jornadas.get((chofer_id, antes), (None, None))[1]
    h = hab.descanso_horas(previo, viaje.get("hora_salida"))
    if h is not None and h < minimo:
        return f"descanso de {h:g} h desde el regreso del {antes[8:10]}/{antes[5:7]} (mínimo {minimo} h)"
    siguiente = jornadas.get((chofer_id, despues), (None, None))[0]
    h = hab.descanso_horas(viaje.get("hora_regreso"), siguiente)
    if h is not None and h < minimo:
        return f"descanso de {h:g} h hasta la salida del {despues[8:10]}/{despues[5:7]} (mínimo {minimo} h)"
    return None


# =====================================================================
# 20) HORA DE REGRESO ESTIMADA
# =====================================================================
def estimar_regreso(unidad_id=None, destino_otro=None, salida="09:00"):
    """Duración típica (mediana) de los viajes anteriores al mismo destino: usa la hora real si está, si no la planificada."""
    import traslados as tr
    if not unidad_id and not destino_otro:
        return None
    hasta = date.today().isoformat()
    desde = (date.today() - timedelta(days=365)).isoformat()
    duraciones = []
    for v in tr.listar_viajes(desde=desde, hasta=hasta, unidad_id=int(unidad_id) if unidad_id else None):
        if v["estado"] == "CANCELADO":
            continue
        if not unidad_id and (v.get("destino_otro") or "").lower() != (destino_otro or "").strip().lower():
            continue
        s = _mins(v.get("hora_salida_real") or v.get("hora_salida"))
        r = _mins(v.get("hora_regreso_real") or v.get("hora_regreso"))
        if s is not None and r is not None and r > s:
            duraciones.append(r - s)
    if len(duraciones) < 3:
        return None
    med = statistics.median(duraciones)
    s0 = _mins(tr.normalizar_hora(salida)) if salida else 9 * 60
    return {"regreso": _hhmm(s0 + med), "duracion": _hhmm(med), "base": len(duraciones)}


# =====================================================================
# 16) PLANIFICADOR SEMANAL
# =====================================================================
def _distancia_km(a, b):
    if None in (a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng")):
        return None
    r = 6371
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _cerca(u1, u2):
    if not u1 or not u2:
        return False
    if u1["id"] == u2["id"]:
        return True
    d = _distancia_km(u1, u2)
    if d is not None:
        return d <= RADIO_KM_AGRUPAR
    return bool(u1.get("ciudad")) and (u1.get("ciudad") or "").lower() == (u2.get("ciudad") or "").lower()


def planificar(lunes, circuito="AMBA"):
    """Propuesta para la semana: agrupa los pedidos sin chofer del mismo día en unidades cercanas en un solo
    desplazamiento (si entran en un vehículo) y sugiere vehículo y chofer. No asigna nada: la coordinación decide."""
    import traslados as tr
    dias = [lunes + timedelta(days=i) for i in range(7)]
    unidades = {u["id"]: u for u in db.listar_unidades()}
    with db._conexion() as con:
        vehiculos = [dict(x) for x in con.execute("SELECT * FROM vehiculos WHERE estado = 'DISPONIBLE' AND (reservado_para IS NULL OR reservado_para = '') ORDER BY capacidad DESC, dominio")]
        choferes = [dict(c) for c in con.execute("SELECT * FROM choferes WHERE estado = 'DISPONIBLE' AND (reservado_para IS NULL OR reservado_para = '') ORDER BY nombre")]
    cap_max = max([x["capacidad"] for x in vehiculos] or [0])
    propuesta = []
    usados_ch, usados_ve = defaultdict(set), defaultdict(set)
    propuestas = Counter()
    for d in dias:
        fi = d.isoformat()
        pedidos = [v for v in tr.listar_viajes(desde=fi, hasta=fi, circuito=circuito) if v["estado"] == "SOLICITADO" and v["modalidad"] == "CHOFER"]
        pedidos.sort(key=lambda v: (v.get("hora_salida") or "99:99", v["id"]))
        grupos = []
        for v in pedidos:
            u = unidades.get(v.get("unidad_id"))
            puesto = False
            for g in grupos:
                if g["personas"] + v["con_transporte"] > (cap_max or 99):
                    continue
                if all(_cerca(u, unidades.get(o.get("unidad_id"))) for o in g["viajes"]):
                    g["viajes"].append(v); g["personas"] += v["con_transporte"]; puesto = True
                    break
            if not puesto:
                grupos.append({"viajes": [v], "personas": v["con_transporte"]})
        for g in grupos:
            ref = {"fecha": fi, "hora_salida": min((x.get("hora_salida") or "08:00") for x in g["viajes"]),
                   "hora_regreso": max((x.get("hora_regreso") or "13:00") for x in g["viajes"])}
            ch_oc, ve_oc = tr._ocupados(fi, ref)
            ch_extra, ve_extra = ocupacion_extra(fi, ref)
            veh = next((x for x in sorted(vehiculos, key=lambda x: (x["capacidad"], x["dominio"]))
                        if x["capacidad"] >= g["personas"] and x["id"] not in ve_oc and x["id"] not in ve_extra and x["id"] not in usados_ve[fi]
                        and not (x.get("vtv_vence") and x["vtv_vence"] < fi) and not (x.get("seguro_vence") and x["seguro_vence"] < fi)), None)
            libres = [c for c in choferes if c["id"] not in ch_oc and c["id"] not in ch_extra and c["id"] not in usados_ch[fi]]
            rk = ranking_choferes(dict(ref), libres, {}, propuestas) if libres else []
            cho = next((c for c in rk if c["apto"]), None)
            if veh:
                usados_ve[fi].add(veh["id"])
            if cho:
                usados_ch[fi].add(cho["id"])
                propuestas[cho["id"]] += 1
            propuesta.append({"fecha": fi, "viajes": g["viajes"], "personas": g["personas"], "vehiculo": veh, "chofer": cho,
                              "agrupa": len(g["viajes"]) > 1, "clave": "-".join(str(x["id"]) for x in g["viajes"]),
                              "avisos": restricciones_salida(g["viajes"], ref)})
    ahorro = sum(len(p["viajes"]) - 1 for p in propuesta)
    return {"dias": dias, "propuesta": propuesta, "pedidos": sum(len(p["viajes"]) for p in propuesta),
            "salidas": len(propuesta), "ahorro": ahorro}


def restricciones_salida(viajes, ref):
    """Propuesta 56 (parte sin software externo): controla la salida propuesta contra el horario de ingreso de cada unidad
    (propuesta 57) y la jornada máxima del chofer. Solo avisa; el armado sigue siendo el de siempre."""
    avisos = []
    try:
        from dgpdh.servicios import ventanas
        with db._conexion() as con:
            for v in viajes:
                a = ventanas.aviso_para_viaje(con, v.get("unidad_id"), v["fecha"], v.get("hora_salida"))
                if a:
                    avisos.append(f"{v['destino']}: {a}")
    except Exception:
        import logging
        logging.exception("No se pudieron controlar los horarios de ingreso")
    s, r = _mins(ref.get("hora_salida")), _mins(ref.get("hora_regreso"))
    if s is not None and r is not None and r - s > MAX_HORAS_DIA * 60:
        avisos.append(f"La salida dura más de {MAX_HORAS_DIA} h: supera la jornada del chofer.")
    return avisos


def aplicar_propuesta(viaje_ids, chofer_id, vehiculo_id, usuario):
    """Asigna el mismo chofer y vehículo a los viajes del grupo (un solo desplazamiento)."""
    import traslados as tr
    choques = []
    for i, vid in enumerate(viaje_ids):
        choques += tr.asignar(vid, chofer_id, vehiculo_id, usuario, forzar=i > 0) or []
    return choques


# =====================================================================
# 19) ANOMALÍAS
# =====================================================================
def anomalias(desde=None, hasta=None):
    """Lo que conviene revisar antes de que pase: chofer sobrecargado o con licencia, vehículo vencido o reservado,
    licencia de conducir vencida y horarios inusuales. Devuelve [{nivel, texto, url}]."""
    import traslados as tr
    desde = desde or date.today().isoformat()
    hasta = hasta or (date.today() + timedelta(days=14)).isoformat()
    viajes = [v for v in tr.listar_viajes(desde=desde, hasta=hasta) if v["estado"] in ("CONFIRMADO", "SOLICITADO")]
    with db._conexion() as con:
        choferes = {c["id"]: dict(c) for c in con.execute("SELECT * FROM choferes")}
        vehiculos = {x["id"]: dict(x) for x in con.execute("SELECT * FROM vehiculos")}
    out = []
    por_dia, por_semana, horas_dia = defaultdict(set), defaultdict(set), defaultdict(int)
    for v in viajes:
        f = v["fecha"]
        url = f"/traslados/{v['id']}"
        etq = f"{f[8:10]}/{f[5:7]} a {v['destino']}"
        s, r = _mins(v.get("hora_salida")), _mins(v.get("hora_regreso"))
        if s is not None and r is not None and r <= s:
            out.append({"nivel": "alto", "texto": f"Viaje del {etq}: el regreso ({v['hora_regreso']}) es anterior a la salida.", "url": url})
        elif (v.get("hora_salida") and v["hora_salida"] < HORA_TEMPRANA) or (v.get("hora_regreso") and v["hora_regreso"] > HORA_TARDE):
            out.append({"nivel": "medio", "texto": f"Viaje del {etq}: horario inusual ({v.get('hora_salida') or '—'}–{v.get('hora_regreso') or '—'}).", "url": url})
        if v["estado"] != "CONFIRMADO":
            continue
        c = choferes.get(v.get("chofer_id"))
        x = vehiculos.get(v.get("vehiculo_id"))
        if c:
            if c.get("licencia_vence") and c["licencia_vence"] < f:
                out.append({"nivel": "alto", "texto": f"{c['nombre']} tiene la licencia de conducir vencida y está asignado al viaje del {etq}.", "url": url})
            l = licencia_en(c["id"], f)
            if l:
                out.append({"nivel": "alto", "texto": f"{c['nombre']} está de {TIPOS_LICENCIA.get(l['tipo'], l['tipo']).lower()} y tiene asignado el viaje del {etq}.", "url": url})
            clave = tr.clave_desplazamiento(v)
            por_dia[(c["id"], f)].add(clave)
            por_semana[(c["id"], tr.lunes_de(date.fromisoformat(f)).isoformat())].add(clave)
            if s is not None and r is not None and r > s:
                horas_dia[(c["id"], f)] += r - s
        if x:
            for campo, nombre in (("vtv_vence", "la VTV vencida"), ("seguro_vence", "el seguro vencido")):
                if x.get(campo) and x[campo] < f:
                    out.append({"nivel": "alto", "texto": f"El vehículo {x['dominio']} tiene {nombre} y está asignado al viaje del {etq}.", "url": url})
            if x.get("estado") != "DISPONIBLE":
                out.append({"nivel": "alto", "texto": f"El vehículo {x['dominio']} figura «{x['estado'].lower().replace('_', ' ')}» y está asignado al viaje del {etq}.", "url": url})
            _, ve_extra = ocupacion_extra(f, v)
            if x["id"] in ve_extra:
                out.append({"nivel": "alto", "texto": f"El vehículo {x['dominio']} está {ve_extra[x['id']]} y también asignado al viaje del {etq}.", "url": url})
    for (cid, f), s in por_dia.items():
        if len(s) > MAX_DESPLAZAMIENTOS_DIA:
            out.append({"nivel": "medio", "texto": f"{choferes[cid]['nombre']} tiene {len(s)} salidas el {f[8:10]}/{f[5:7]}.", "url": f"/traslados/choferes/{cid}"})
    for (cid, f), m in horas_dia.items():
        if m > MAX_HORAS_DIA * 60:
            out.append({"nivel": "medio", "texto": f"{choferes[cid]['nombre']} suma {m // 60} h de viaje el {f[8:10]}/{f[5:7]}.", "url": f"/traslados/choferes/{cid}"})
    minimo = descanso_minimo_horas()  # propuesta 59: descanso entre el regreso de un día y la salida del siguiente
    if minimo:
        from dgpdh.dominio import habilidades as hab
        dias_ch = defaultdict(lambda: [None, None])
        for v in viajes:
            if v["estado"] == "CONFIRMADO" and v.get("chofer_id") and v["modalidad"] == "CHOFER":
                d = dias_ch[(v["chofer_id"], v["fecha"])]
                if v.get("hora_salida"):
                    d[0] = min(d[0] or "99:99", v["hora_salida"])
                if v.get("hora_regreso"):
                    d[1] = max(d[1] or "", v["hora_regreso"])
        for (cid, f), (_, regreso) in dias_ch.items():
            sig = (date.fromisoformat(f) + timedelta(days=1)).isoformat()
            h = hab.descanso_horas(regreso, dias_ch.get((cid, sig), (None, None))[0])
            if h is not None and h < minimo and cid in choferes:
                out.append({"nivel": "medio", "texto": f"{choferes[cid]['nombre']} descansa {h:g} h entre el {f[8:10]}/{f[5:7]} y el {sig[8:10]}/{sig[5:7]} "
                            f"(mínimo {minimo} h).", "url": f"/traslados/choferes/{cid}"})
    for (cid, l), s in por_semana.items():
        if len(s) > MAX_DESPLAZAMIENTOS_SEMANA:
            out.append({"nivel": "medio", "texto": f"{choferes[cid]['nombre']} tiene {len(s)} salidas la semana del {l[8:10]}/{l[5:7]} (más de {MAX_DESPLAZAMIENTOS_SEMANA}).", "url": f"/traslados/choferes/{cid}"})
    try:  # propuesta 61: salidas ya cerradas del rango con kilometraje inconsistente
        with db._conexion() as con:
            cerradas = [dict(f) for f in con.execute("""SELECT d.*, ve.dominio FROM desplazamientos d JOIN vehiculos ve ON ve.id = d.vehiculo_id
                                                        WHERE d.fecha BETWEEN ? AND ? AND d.km_salida IS NOT NULL""", (desde, hasta))]
            previos = {d["id"]: _km_previo(con, d) for d in cerradas}
        for d in cerradas:
            for t in avisos_odometro(d, previos[d["id"]]):
                out.append({"nivel": "medio", "texto": f"Salida del {d['fecha'][8:10]}/{d['fecha'][5:7]} de {d['dominio']}: {t}.",
                            "url": f"/traslados/desplazamiento/{d['id']}"})
    except Exception:
        import logging
        logging.exception("No se pudo controlar el odómetro de las salidas")
    vistos, unicos = set(), []
    for a in sorted(out, key=lambda a: a["nivel"] != "alto"):
        if a["texto"] not in vistos:
            vistos.add(a["texto"]); unicos.append(a)
    return unicos


# =====================================================================
# 18) RECORDATORIO DEL JUEVES PERSONALIZADO
# =====================================================================
def recordatorio_personalizado(hoy=None):
    """Miércoles y jueves: a cada asesor/a le dice qué viajes habituales le faltan cargar y cuáles frecuentes le faltan
    confirmar para la semana próxima. Una vez por día; a quien no le falta nada no le llega nada."""
    import auth
    import frecuentes as fr
    import traslados as tr
    hoy = hoy or date.today()
    aviso = tr.toca_recordatorio(hoy)  # propuesta 69: si el jueves es feriado, se corre al día hábil anterior
    if not aviso:
        return 0
    clave = f"traslados_recordatorio_personal_{hoy.isoformat()}"
    if db.obtener_ajuste(clave):
        return 0
    db.guardar_ajuste(clave, "1")
    prox = tr.lunes_de(hoy) + timedelta(days=7)
    lim = aviso[1]
    n, general = 0, []
    for u in db.listar_usuarios():
        if not u["activo"] or u["rol"] not in (auth.ROL_ASESOR, auth.ROL_COORDINADOR) or not u.get("acceso_traslados", 1):
            continue
        try:
            faltan = fr.faltan_cargar(u, hoy=hoy)
            confirmar = [v for v in fr.para_confirmar(u, hoy=hoy) if any(p.get("usuario_id") == u["id"] for p in v["participantes"])]
        except Exception:
            continue
        if not faltan and not confirmar:  # sin pendientes: el aviso general del plazo
            general.append(u["id"])
            continue
        partes = []
        if faltan:
            partes.append("te faltan cargar: " + ", ".join(f"{x['destino']} ({x['dia_nombre'].lower()})" for x in faltan[:4]))
        if confirmar:
            partes.append(f"tenés {len(confirmar)} viaje(s) frecuente(s) para confirmar")
        db.notificar_usuarios([u["id"]], f"Planificación de la semana próxima (plazo: {tr.DIAS_SEMANA[lim.weekday()]} {lim.strftime('%d/%m')}): " + "; ".join(partes) + ".",
                              "/traslados/mi-semana")
        n += 1
    if general:
        cuando = aviso[0]
        db.notificar_usuarios(general, f"{cuando} vence la carga de los viajes de rutina de la semana del {prox.strftime('%d/%m')} al "
                              f"{(prox + timedelta(days=4)).strftime('%d/%m')}. Después se cargan como inmediatos.", "/traslados")
    return n + len(general)


# =====================================================================
# 79) COMPARACIÓN EXCEL ANTERIOR ↔ SISTEMA
# =====================================================================
def comparar_excel(ruta):
    """Compara los viajes del Excel de la planilla anterior con los del sistema en el mismo período, sin importar nada.
    Clave: fecha + destino; se informan diferencias de horario y de chofer."""
    import importar_viajes as iv
    import traslados as tr
    filas, sin_fecha = iv.leer_excel(ruta)
    del_excel, _ = iv.armar_viajes(filas)
    if not del_excel:
        return {"desde": None, "hasta": None, "ambos": [], "solo_excel": [], "solo_sistema": [], "diferencias": [], "hojas_sin_fecha": sin_fecha}
    desde = min(v["fecha"] for v in del_excel)
    hasta = max(v["fecha"] for v in del_excel)
    sistema = [v for v in tr.listar_viajes(desde=desde, hasta=hasta) if v["estado"] != "CANCELADO"]

    def destino_de(v):
        return ("u", v.get("unidad_id")) if v.get("unidad_id") else ("o", (v.get("destino_otro") or "").strip().lower())

    libres = defaultdict(list)
    for v in sistema:
        libres[(v["fecha"], destino_de(v))].append(v)
    ambos, solo_excel, diferencias = [], [], []
    for e in del_excel:
        if e.get("cancelado"):
            continue
        clave = (e["fecha"], destino_de(e))
        candidatos = libres.get(clave) or []
        if not candidatos:
            solo_excel.append(e)
            continue
        s = min(candidatos, key=lambda v: abs((_mins(v.get("hora_salida")) or 0) - (_mins(e.get("hora_salida")) or 0)))
        candidatos.remove(s)
        dif = []
        if e.get("hora_salida") and s.get("hora_salida") and e["hora_salida"] != s["hora_salida"]:
            dif.append(f"salida {e['hora_salida']} (Excel) / {s['hora_salida']} (sistema)")
        ch_e = (e.get("chofer") or "").strip().lower()
        ch_s = (s.get("chofer_nombre") or "").strip().lower()
        if ch_e and ch_s and ch_e != ch_s:
            dif.append(f"chofer {e['chofer']} / {s['chofer_nombre']}")
        par = {"excel": e, "sistema": s, "diferencias": dif}
        (diferencias if dif else ambos).append(par)
    nombres = {u["id"]: u["nombre"] for u in db.listar_unidades(solo_activas=False)}
    for e in solo_excel + [x["excel"] for x in ambos + diferencias]:
        e["destino"] = nombres.get(e.get("unidad_id")) or e.get("destino_otro") or "—"
    solo_sistema = [v for lista in libres.values() for v in lista]
    return {"desde": desde, "hasta": hasta, "ambos": ambos, "solo_excel": solo_excel, "solo_sistema": sorted(solo_sistema, key=lambda v: (v["fecha"], v["id"])),
            "diferencias": diferencias, "hojas_sin_fecha": sin_fecha}


# =====================================================================
# Planificador por ubicación del destino y unificación de viajes
# =====================================================================
def _clave_ubicacion(v, unidades):
    """(orden, título) del lugar de destino: zona y ciudad de la unidad; para destinos escritos a mano, el texto."""
    u = unidades.get(v.get("unidad_id"))
    if u:
        zona = (u.get("zona") or "").strip()
        ciudad = (u.get("ciudad") or "").strip()
        prov = (u.get("provincia") or "").strip()
        lugar = ciudad or prov or u["nombre"]
        titulo = " · ".join(x for x in (zona, lugar + (f" ({prov})" if prov and prov != lugar else "")) if x)
        return ((zona or "~").lower(), lugar.lower()), titulo or u["nombre"]
    otro = (v.get("destino_otro") or "Otro destino").strip()
    return ("~~", otro.lower()), f"Otros destinos · {otro}"


def pedidos_por_ubicacion(lunes, circuito="AMBA"):
    """Pedidos con chofer sin asignar de la semana, agrupados por lugar de destino (zona y ciudad). Dentro de cada lugar,
    por día y hora; marca los días con más de un pedido (se pueden unificar)."""
    import traslados as tr
    desde, hasta = lunes.isoformat(), (lunes + timedelta(days=6)).isoformat()
    unidades = {u["id"]: u for u in db.listar_unidades()}
    grupos = {}
    for v in tr.listar_viajes(desde=desde, hasta=hasta, circuito=circuito):
        if v["estado"] != "SOLICITADO" or v["modalidad"] != "CHOFER":
            continue
        clave, titulo = _clave_ubicacion(v, unidades)
        g = grupos.setdefault(clave, {"titulo": titulo, "viajes": [], "personas": 0})
        g["viajes"].append(v)
        g["personas"] += v.get("con_transporte") or 0
    salida = []
    for clave in sorted(grupos):
        g = grupos[clave]
        g["viajes"].sort(key=lambda v: (v["fecha"], v.get("hora_salida") or "99:99", v["id"]))
        por_dia = Counter(v["fecha"] for v in g["viajes"])
        g["dias_unificables"] = sorted(f for f, n in por_dia.items() if n > 1)
        for v in g["viajes"]:
            v["unificable"] = por_dia[v["fecha"]] > 1
        salida.append(g)
    return salida


def _mismo_destino(a, b):
    if a.get("unidad_id") or b.get("unidad_id"):
        return a.get("unidad_id") == b.get("unidad_id")
    return (a.get("destino_otro") or "").strip().lower() == (b.get("destino_otro") or "").strip().lower()


def unificar_viajes(viaje_ids, usuario, chofer_id=None, vehiculo_id=None):
    """Une pedidos del MISMO día. Si van al mismo destino, quedan en un solo viaje (se pasan las personas al primero y
    los demás quedan cancelados como «Unificado con el viaje #N», sin borrarse). Si van a destinos distintos, se arma
    una sola salida: el mismo chofer y vehículo para todos (hace falta elegirlos). Devuelve {"modo", "viaje_id", "n"}."""
    import traslados as tr
    ids = list(dict.fromkeys(int(x) for x in viaje_ids))
    if len(ids) < 2:
        raise tr.ErrorTraslado("Marcá al menos dos pedidos para unificar.")
    viajes = [tr.obtener_viaje(i) for i in ids]
    if any(v is None for v in viajes):
        raise tr.ErrorTraslado("Alguno de los viajes ya no existe.")
    if any(v["estado"] not in ("SOLICITADO", "CONFIRMADO") or v["modalidad"] != "CHOFER" for v in viajes):
        raise tr.ErrorTraslado("Solo se unifican pedidos con chofer que no estén cancelados.")
    if len({v["fecha"] for v in viajes}) > 1:
        raise tr.ErrorTraslado("Solo se unifican pedidos del mismo día.")
    viajes.sort(key=lambda v: (v.get("hora_salida") or "99:99", v["id"]))
    base = viajes[0]
    personas = sum(v.get("con_transporte") or 0 for v in viajes)
    with db._conexion() as con:
        cap = con.execute("SELECT MAX(capacidad) AS c FROM vehiculos WHERE estado = 'DISPONIBLE'").fetchone()["c"] or 0
    if cap and personas > cap:
        raise tr.ErrorTraslado(f"Juntos son {personas} personas y el vehículo más grande tiene {cap} plazas: no entran en una sola salida.")
    if all(_mismo_destino(base, v) for v in viajes[1:]):
        salida = min((v.get("hora_salida") for v in viajes if v.get("hora_salida")), default=base.get("hora_salida"))
        regreso = max((v.get("hora_regreso") for v in viajes if v.get("hora_regreso")), default=base.get("hora_regreso"))
        with db._conexion() as con:
            ya = {r["usuario_id"] for r in con.execute("SELECT usuario_id FROM viaje_participantes WHERE viaje_id = ? AND usuario_id IS NOT NULL", (base["id"],))}
            for v in viajes[1:]:
                for p in con.execute("SELECT id, usuario_id FROM viaje_participantes WHERE viaje_id = ?", (v["id"],)).fetchall():
                    if p["usuario_id"] and p["usuario_id"] in ya:
                        continue
                    con.execute("UPDATE viaje_participantes SET viaje_id = ? WHERE id = ?", (base["id"], p["id"]))
                    if p["usuario_id"]:
                        ya.add(p["usuario_id"])
                con.execute("""UPDATE viajes SET estado = 'CANCELADO', motivo_cancelacion = ?, unificado_en = ?, actualizado_en = ? WHERE id = ?""",
                            (f"Unificado con el viaje #{base['id']}", base["id"], db._ahora(), v["id"]))
                tr.auditar("viaje", v["id"], "Unificado", v["estado"], f"con el viaje #{base['id']}", con=con)
            con.execute("UPDATE viajes SET hora_salida = ?, hora_regreso = ?, actualizado_en = ? WHERE id = ?", (salida, regreso, db._ahora(), base["id"]))
            tr.auditar("viaje", base["id"], "Unificación", None, "se sumaron los viajes " + ", ".join(f"#{v['id']}" for v in viajes[1:]), con=con)
        avisar = {v["creado_por_id"] for v in viajes[1:] if v.get("creado_por_id") and v["creado_por_id"] != usuario["id"]}
        if avisar:
            db.notificar_usuarios(list(avisar), f"Tu pedido de viaje del {base['fecha'][8:10]}/{base['fecha'][5:7]} a {base['destino']} se unificó con otro al mismo destino.",
                                  f"/traslados/{base['id']}")
        if chofer_id:
            tr.asignar(base["id"], chofer_id, vehiculo_id, usuario)
        return {"modo": "un_viaje", "viaje_id": base["id"], "n": len(viajes)}
    if not chofer_id:
        raise tr.ErrorTraslado("Van a destinos distintos: para hacerlos en una sola salida elegí el chofer (y el vehículo).")
    aplicar_propuesta([v["id"] for v in viajes], chofer_id, vehiculo_id, usuario)
    return {"modo": "una_salida", "viaje_id": base["id"], "n": len(viajes)}


# =====================================================================
# MESA DE ASIGNACIÓN: pedidos de auto pendientes → chofer y vehículo
# =====================================================================
DIAS_MESA = 30


def mesa_asignacion(hoy=None, dias=None):
    """TODOS los pedidos con chofer sin asignar desde hoy (sin límite de días; `dias` solo para acotar si se pide), por día. Por cada día: los pedidos (destino, hora y SOLO las personas
    que van en el vehículo institucional), agrupados por lugar, y los choferes y vehículos con lo que ya tienen ese día,
    para que la pantalla avise solapamientos y capacidad mientras se marcan pedidos."""
    import traslados as tr
    hoy = hoy or date.today()
    desde, hasta = hoy.isoformat(), ((hoy + timedelta(days=dias)).isoformat() if dias else None)
    unidades = {u["id"]: u for u in db.listar_unidades()}
    todos = tr.listar_viajes(desde=desde, hasta=hasta, limite=50000)
    pendientes = [v for v in todos if v["estado"] == "SOLICITADO" and v["modalidad"] == "CHOFER" and not v.get("chofer_id")]
    with db._conexion() as con:
        choferes = [dict(c) for c in con.execute("SELECT * FROM choferes WHERE estado = 'DISPONIBLE' ORDER BY nombre")]
        vehiculos = [dict(x) for x in con.execute("SELECT * FROM vehiculos WHERE estado = 'DISPONIBLE' ORDER BY capacidad, dominio")]
    salida = []
    for fecha in sorted({v["fecha"] for v in pendientes}):
        del_dia = [v for v in pendientes if v["fecha"] == fecha]
        for v in del_dia:
            v["en_auto"] = [p for p in v["participantes"] if not p.get("auto_propio")]
            v["por_su_cuenta"] = len(v["participantes"]) - len(v["en_auto"])
            v["clave_lugar"], v["lugar"] = _clave_ubicacion(v, unidades)
            if v["lugar"].startswith("Otros destinos · "):  # destino escrito a mano: alcanza con el nombre
                v["lugar"] = v["lugar"][len("Otros destinos · "):]
        del_dia.sort(key=lambda v: (v["clave_lugar"], v.get("hora_salida") or "99:99", v["id"]))
        lugares = []
        for v in del_dia:
            if not lugares or lugares[-1]["clave"] != v["clave_lugar"]:
                lugares.append({"clave": v["clave_lugar"], "titulo": v["lugar"], "viajes": []})
            lugares[-1]["viajes"].append(v)
        for g in lugares:
            g["personas"] = sum(v["con_transporte"] or 0 for v in g["viajes"])
        # lo que ya tiene cada chofer y vehículo ese día (para avisar solapamientos al marcar)
        ocup_ch, ocup_ve = defaultdict(list), defaultdict(list)
        for o in todos:
            if o["fecha"] == fecha and o["estado"] == "CONFIRMADO":
                franja = {"desde": o.get("hora_salida") or "00:00", "hasta": o.get("hora_regreso") or "23:59", "viaje": o["id"], "destino": o["destino"]}
                if o.get("chofer_id"): ocup_ch[o["chofer_id"]].append(franja)
                if o.get("vehiculo_id"): ocup_ve[o["vehiculo_id"]].append(franja)
        dia_entero = {"fecha": fecha, "hora_salida": "00:00", "hora_regreso": "23:59"}
        ch_extra, ve_extra = ocupacion_extra(fecha, dia_entero)
        carga = carga_semanal(fecha)
        chs = []
        for c in choferes:
            if c.get("reservado_para"):
                continue
            bloqueo = ch_extra.get(c["id"]) or ("licencia de conducir vencida" if c.get("licencia_vence") and c["licencia_vence"] < fecha else None)
            chs.append({"id": c["id"], "nombre": c["nombre"], "ocupado": ocup_ch.get(c["id"], []), "bloqueo": bloqueo,
                        "carga": carga.get(c["id"], 0), "vehiculo_fijo_id": c.get("vehiculo_fijo_id")})
        chs.sort(key=lambda c: (bool(c["bloqueo"]), len(c["ocupado"]), c["carga"], c["nombre"]))
        ves = []
        for x in vehiculos:
            if x.get("reservado_para"):
                continue
            aviso = ve_extra.get(x["id"])
            for campo, nombre in (("vtv_vence", "VTV vencida"), ("seguro_vence", "seguro vencido")):
                if x.get(campo) and x[campo] < fecha:
                    aviso = (aviso + " · " if aviso else "") + nombre
            ves.append({"id": x["id"], "dominio": x["dominio"], "marca": x.get("marca"), "modelo": x.get("modelo"),
                        "capacidad": x["capacidad"], "ocupado": ocup_ve.get(x["id"], []), "aviso": aviso})
        salida.append({"fecha": fecha, "lugares": lugares, "pedidos": len(del_dia),
                       "personas": sum(v["con_transporte"] or 0 for v in del_dia), "choferes": chs, "vehiculos": ves})
    return {"dias": salida, "pedidos": len(pendientes), "personas": sum(v["con_transporte"] or 0 for v in pendientes),
            "max_plazas": max([x["capacidad"] for x in vehiculos if not x.get("reservado_para")] or [0])}


def asignar_seleccion(viaje_ids, chofer_id, vehiculo_id, usuario, forzar=False):
    """Asigna un chofer (y vehículo) a los pedidos marcados del mismo día, en una sola salida. Los pedidos al MISMO
    destino se unifican en un solo viaje (los demás quedan «Unificado con el viaje #N», sin borrarse); los de destinos
    distintos quedan como paradas de la misma salida. Antes de tocar nada controla capacidad, solapamientos, licencias
    y vencimientos (con `forzar` la coordinación acepta el aviso). Devuelve {"viajes": [ids finales], "unificados": n, "choques": [...]}."""
    import traslados as tr
    ids = list(dict.fromkeys(int(x) for x in viaje_ids))
    if not ids:
        raise tr.ErrorTraslado("Marcá al menos un pedido.")
    if not chofer_id:
        raise tr.ErrorTraslado("Elegí el chofer.")
    viajes = [tr.obtener_viaje(i) for i in ids]
    if any(v is None for v in viajes):
        raise tr.ErrorTraslado("Alguno de los pedidos ya no existe.")
    if any(v["estado"] not in ("SOLICITADO", "CONFIRMADO") or v["modalidad"] != "CHOFER" for v in viajes):
        raise tr.ErrorTraslado("Solo se asignan pedidos con chofer que no estén cancelados.")
    if len({v["fecha"] for v in viajes}) > 1:
        raise tr.ErrorTraslado("Una salida es de un solo día: marcá pedidos del mismo día.")
    fecha = viajes[0]["fecha"]
    personas = sum(v.get("con_transporte") or 0 for v in viajes)
    with db._conexion() as con:
        ch = con.execute("SELECT * FROM choferes WHERE id = ?", (chofer_id,)).fetchone()
        ve = con.execute("SELECT * FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone() if vehiculo_id else None
    if not ch:
        raise tr.ErrorTraslado("Elegí el chofer.")
    if ch["estado"] != "DISPONIBLE":
        raise tr.ErrorTraslado(f"El chofer {ch['nombre']} figura en estado «{tr.ETIQUETAS_ESTADO_CHOFER[ch['estado']]}».")
    if vehiculo_id and not ve:
        raise tr.ErrorTraslado("El vehículo no existe.")
    if ve:
        if ve["estado"] != "DISPONIBLE":
            raise tr.ErrorTraslado(f"El vehículo {ve['dominio']} está «{tr.ETIQUETAS_ESTADO_VEHICULO[ve['estado']]}».")
        if ve["capacidad"] < personas:
            raise tr.ErrorTraslado(f"Juntos son {personas} personas y el {ve['dominio']} tiene {ve['capacidad']} plazas: elegí otro vehículo o marcá menos pedidos.")
    ref = {"fecha": fecha, "hora_salida": min((v.get("hora_salida") or "08:00") for v in viajes),
           "hora_regreso": max((v.get("hora_regreso") or "13:00") for v in viajes)}
    choques = []
    ch_oc, ve_oc = {}, {}
    with db._conexion() as con:
        for o in con.execute("SELECT * FROM viajes WHERE fecha = ? AND estado = 'CONFIRMADO'", (fecha,)):
            o = dict(o)
            if o["id"] in ids or not tr._se_pisan(ref, o):
                continue
            if o["chofer_id"]: ch_oc.setdefault(o["chofer_id"], o["id"])
            if o["vehiculo_id"]: ve_oc.setdefault(o["vehiculo_id"], o["id"])
    ch_extra, ve_extra = ocupacion_extra(fecha, ref)
    if chofer_id in ch_oc: choques.append(f"el chofer {ch['nombre']} ya tiene el viaje #{ch_oc[chofer_id]} en ese horario")
    if chofer_id in ch_extra: choques.append(f"el chofer {ch['nombre']} está {ch_extra[chofer_id]}")
    if ch["licencia_vence"] and ch["licencia_vence"] < fecha: choques.append(f"el chofer {ch['nombre']} tiene la licencia de conducir vencida")
    if ve:
        if ve["id"] in ve_oc: choques.append(f"el vehículo {ve['dominio']} ya está en el viaje #{ve_oc[ve['id']]}")
        if ve["id"] in ve_extra: choques.append(f"el vehículo {ve['dominio']} está {ve_extra[ve['id']]}")
        for campo, nombre in (("vtv_vence", "la VTV vencida"), ("seguro_vence", "el seguro vencido")):
            if ve[campo] and ve[campo] < fecha:
                choques.append(f"el vehículo {ve['dominio']} tiene {nombre}")
    if choques and not forzar:
        raise tr.ErrorTraslado("Conflicto: " + " y ".join(choques) + ". Si igual querés asignarlo, marcá «Aceptar el aviso».")
    # 1) mismo destino → un solo viaje
    por_destino = []
    for v in sorted(viajes, key=lambda v: (v.get("hora_salida") or "99:99", v["id"])):
        g = next((g for g in por_destino if _mismo_destino(g[0], v)), None)
        (g.append(v) if g is not None else por_destino.append([v]))
    finales, unificados = [], 0
    for g in por_destino:
        if len(g) > 1:
            r = unificar_viajes([v["id"] for v in g], usuario)
            finales.append(r["viaje_id"]); unificados += len(g) - 1
        else:
            finales.append(g[0]["id"])
    # 2) mismo chofer y vehículo para todos: una sola salida (los avisos ya se aceptaron arriba)
    for vid in finales:
        tr.asignar(vid, chofer_id, vehiculo_id, usuario, forzar=bool(choques), misma_salida=tuple(finales))
    return {"viajes": finales, "unificados": unificados, "choques": choques, "personas": personas,
            "chofer": ch["nombre"], "vehiculo": ve["dominio"] if ve else None}
