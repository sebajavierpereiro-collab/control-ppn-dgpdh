"""Flota: estado de cada vehículo (service, aceite, cubiertas, matafuego, VTV, seguro y demás controles con
vencimiento) y las fichas de vehículos y choferes.

Cada control queda registrado con la fecha y el kilometraje en que se hizo y cuándo toca el próximo (por fecha,
por kilómetros o por ambos). VTV, seguro y service además actualizan los campos del vehículo que ya usa el
aviso de vencimientos de Traslados, así no hay dos fechas distintas para lo mismo.
"""
from datetime import date, timedelta

import database as db
import traslados as tr
from traslados import ErrorTraslado

TIPOS = {
    "SERVICE": "Service", "ACEITE": "Aceite y filtros", "CUBIERTAS": "Cubiertas", "FRENOS": "Frenos",
    "MATAFUEGO": "Matafuego", "BATERIA": "Batería", "VTV": "VTV", "SEGURO": "Seguro", "BOTIQUIN": "Botiquín y balizas", "OTRO": "Otro",
}
# Controles que se esperan en todo vehículo (los demás se muestran si se cargaron alguna vez)
BASICOS = ("SERVICE", "ACEITE", "CUBIERTAS", "MATAFUEGO", "VTV", "SEGURO")
_CAMPO_VEHICULO = {"VTV": "vtv_vence", "SEGURO": "seguro_vence", "SERVICE": "service_proximo"}
AVISO_DIAS, AVISO_KM = 30, 1000


def _int(v):
    v = str(v or "").strip().replace(".", "")
    return int(v) if v.isdigit() else None


def registrar_control(vehiculo_id, d, usuario):
    tipo = d.get("tipo") if d.get("tipo") in TIPOS else None
    if not tipo:
        raise ErrorTraslado("Elegí qué control se hizo.")
    fecha = tr._fecha(d.get("fecha") or date.today().isoformat()).isoformat()
    vence = tr._fecha(d["vence_fecha"]).isoformat() if d.get("vence_fecha") else None
    km, vence_km = _int(d.get("km")), _int(d.get("vence_km"))
    if vence and vence < fecha:
        raise ErrorTraslado("El próximo vencimiento tiene que ser posterior a la fecha del control.")
    if km is not None and vence_km is not None and vence_km <= km:
        raise ErrorTraslado("El kilometraje del próximo control tiene que ser mayor al actual.")
    detalle = (str(d.get("detalle") or "").strip()[:300]) or None
    referencia = (str(d.get("referencia") or "").strip()[:120]) or None  # propuesta 64: póliza, n.º de orden…
    prov_id, resp_id = _int(d.get("proveedor_id")), _int(d.get("responsable_id"))
    with db._conexion() as con:
        v = con.execute("SELECT * FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
        if not v:
            raise ErrorTraslado("El vehículo no existe.")
        cid = con.execute("""INSERT INTO vehiculo_controles (vehiculo_id, tipo, fecha, km, vence_fecha, vence_km, detalle, registrado_por, creado_en)
                             VALUES (?,?,?,?,?,?,?,?,?)""", (vehiculo_id, tipo, fecha, km, vence, vence_km, detalle, usuario["nombre"], db._ahora())).lastrowid
        if prov_id or resp_id or referencia:  # propuesta 64
            if prov_id and not con.execute("SELECT 1 FROM proveedores WHERE id = ? AND activo = 1", (prov_id,)).fetchone():
                raise ErrorTraslado("El proveedor no existe o está dado de baja.")
            if resp_id and not con.execute("SELECT 1 FROM usuarios WHERE id = ? AND activo = 1", (resp_id,)).fetchone():
                raise ErrorTraslado("La persona responsable no existe o no está activa.")
            con.execute("UPDATE vehiculo_controles SET proveedor_id = ?, referencia = ?, responsable_id = ? WHERE id = ?", (prov_id, referencia, resp_id, cid))
        if km is not None and (v["km"] or 0) < km:
            con.execute("UPDATE vehiculos SET km = ?, actualizado_en = ? WHERE id = ?", (km, db._ahora(), vehiculo_id))
        if tipo in _CAMPO_VEHICULO and vence:
            con.execute(f"UPDATE vehiculos SET {_CAMPO_VEHICULO[tipo]} = ?, actualizado_en = ? WHERE id = ?", (vence, db._ahora(), vehiculo_id))
        tr.auditar("vehiculo", vehiculo_id, f"Control: {TIPOS[tipo]}", nuevo=f"{fecha}" + (f" · {km} km" if km else "") + (f" · próximo {vence}" if vence else "") + (f" / {vence_km} km" if vence_km else "") + (f" · ref. {referencia}" if referencia else "") + (" · con responsable" if resp_id else ""), con=con)
    return cid


def actualizar_km(vehiculo_id, km, usuario):
    km = _int(km)
    if km is None:
        raise ErrorTraslado("Indicá el kilometraje.")
    with db._conexion() as con:
        v = con.execute("SELECT km FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
        if not v:
            raise ErrorTraslado("El vehículo no existe.")
        if v["km"] and km < v["km"]:
            raise ErrorTraslado(f"El kilometraje no puede bajar (último registrado: {v['km']} km).")
        con.execute("UPDATE vehiculos SET km = ?, actualizado_en = ? WHERE id = ?", (km, db._ahora(), vehiculo_id))
        tr.auditar("vehiculo", vehiculo_id, "Kilometraje", v["km"], km, con=con)


# ---------------------------------------------------------------- combustible, vales y novedades mecánicas
GRAVEDADES = {"LEVE": "Leve (puede seguir circulando)", "MODERADA": "Moderada (revisar pronto)", "GRAVE": "Grave (no debe circular)"}
ETIQ_REGISTRO = {"COMBUSTIBLE": "Carga de combustible", "VALES": "Vales entregados", "NOVEDAD": "Novedad mecánica"}
# Recorrido de una novedad mecánica. Las dos últimas la cierran (dejan de contar como «sin resolver»).
ESTADOS_NOVEDAD = {
    "CARGADA": "Cargada",
    "REPORTADA": "Reportada al área responsable",
    "REPARACION_SOLICITADA": "Reparación solicitada",
    "EN_REPARACION": "En reparación",
    "REPARADA": "Reparada",
    "DESESTIMADA": "No requiere reparación",
}
NOVEDAD_CERRADA = ("REPARADA", "DESESTIMADA")
CLASE_ESTADO_NOVEDAD = {"CARGADA": "aviso", "REPORTADA": "info", "REPARACION_SOLICITADA": "info", "EN_REPARACION": "frecuente",
                        "REPARADA": "ok", "DESESTIMADA": "neutro"}


def _num(v, tipo=float, nombre="valor"):
    txt = str(v or "").strip().replace(".", "").replace(",", ".") if tipo is int else str(v or "").strip().replace(",", ".")
    if not txt:
        return None
    try:
        x = int(float(txt)) if tipo is int else round(float(txt), 2)
    except ValueError:
        raise ErrorTraslado(f"«{v}» no es un número válido ({nombre}).")
    if x < 0:
        raise ErrorTraslado(f"El {nombre} no puede ser negativo.")
    return x


def _quien(usuario):
    return usuario.get("nombre") if isinstance(usuario, dict) else (usuario or "Sistema")


def _km_registro(vehiculo_id, km, usuario):
    """Si se informó el kilometraje y es mayor al registrado, se actualiza el del vehículo."""
    if km is None:
        return
    with db._conexion() as con:
        v = con.execute("SELECT km FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
    if v and v["km"] and km < v["km"]:
        raise ErrorTraslado(f"El kilometraje no puede ser menor al último registrado ({v['km']:,} km).".replace(",", "."))
    if not v or km != v["km"]:
        actualizar_km(vehiculo_id, km, usuario)


def registrar(vehiculo_id, tipo, d, usuario):
    """Carga de combustible, vales entregados o novedad mecánica. Devuelve el id del registro."""
    if tipo not in ETIQ_REGISTRO:
        raise ErrorTraslado("Tipo de registro no válido.")
    with db._conexion() as con:
        v = con.execute("SELECT * FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
    if not v:
        raise ErrorTraslado("El vehículo no existe.")
    fecha = tr._fecha(d.get("fecha") or date.today().isoformat()).isoformat()
    if fecha > date.today().isoformat():
        raise ErrorTraslado("La fecha no puede ser futura.")
    km = _num(d.get("km"), int, "kilometraje")
    litros = importe = cantidad = chofer_id = gravedad = None
    detalle = (str(d.get("detalle") or "").strip()[:500]) or None
    if tipo == "COMBUSTIBLE":
        litros, importe = _num(d.get("litros"), float, "litros"), _num(d.get("importe"), float, "importe")
        if not litros:
            raise ErrorTraslado("Indicá cuántos litros se cargaron.")
        if litros > 300:
            raise ErrorTraslado("Más de 300 litros en una carga: revisá el número.")
    elif tipo == "VALES":
        cantidad = _num(d.get("cantidad"), int, "cantidad de vales")
        if not cantidad:
            raise ErrorTraslado("Indicá cuántos vales se entregaron.")
        importe = _num(d.get("importe"), float, "importe")
        chofer_id = int(d["chofer_id"]) if str(d.get("chofer_id") or "").isdigit() else None
    else:
        gravedad = d.get("gravedad") if d.get("gravedad") in GRAVEDADES else "LEVE"
        if not detalle:
            raise ErrorTraslado("Contá cuál es la novedad mecánica.")
    _km_registro(vehiculo_id, km, usuario)
    with db._conexion() as con:
        rid = con.execute("""INSERT INTO vehiculo_registros (vehiculo_id, tipo, fecha, km, litros, importe, cantidad, chofer_id, gravedad, detalle,
                             registrado_por, creado_en, estado_novedad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (vehiculo_id, tipo, fecha, km, litros, importe, cantidad, chofer_id, gravedad, detalle, _quien(usuario), db._ahora(),
                           "CARGADA" if tipo == "NOVEDAD" else None)).lastrowid
        if tipo == "NOVEDAD":
            con.execute("INSERT INTO vehiculo_novedad_estados (registro_id, estado, nota, usuario, fecha) VALUES (?,?,?,?,?)",
                        (rid, "CARGADA", None, _quien(usuario), db._ahora()))
        if tipo == "COMBUSTIBLE":
            resumen = f"{litros} l" + (f" · ${importe:,.2f}" if importe else "")
        elif tipo == "VALES":
            resumen = f"{cantidad} vale(s)"
        else:
            resumen = f"{GRAVEDADES[gravedad].split(' (')[0]}: {detalle}"
        tr.auditar("vehiculo", vehiculo_id, ETIQ_REGISTRO[tipo], None, f"{fecha} · {resumen}", con=con)
    if tipo == "COMBUSTIBLE" and km:  # propuesta 63: aviso si la carga se aparta del rendimiento habitual
        import operacion
        operacion.avisar_combustible(vehiculo_id, f"R{rid}")
    if tipo == "NOVEDAD" and gravedad in ("MODERADA", "GRAVE"):
        destinos = [u["id"] for u in db.listar_usuarios() if u["activo"] and tr.puede_coordinar(u)]
        db.notificar_usuarios(destinos, f"Novedad mecánica {GRAVEDADES[gravedad].split(' (')[0].lower()} en {v['dominio']}: {detalle}",
                              f"/traslados/vehiculos/{vehiculo_id}")
    return rid


def cambiar_estado_novedad(registro_id, estado, nota, usuario, estado_vehiculo_nuevo=None):
    """Avanza (o corrige) el estado de una novedad mecánica y lo deja en el historial. Al cerrarla (reparada / no requiere)
    deja de contar como «sin resolver»; si se reabre, vuelve a contar. `estado_vehiculo_nuevo` (TALLER o DISPONIBLE) cambia
    además el estado del vehículo, solo si la persona lo eligió."""
    if estado not in ESTADOS_NOVEDAD:
        raise ErrorTraslado("Estado de la novedad no válido.")
    nota = (str(nota or "").strip()[:300]) or None
    if estado == "DESESTIMADA" and not nota:
        raise ErrorTraslado("Contá por qué no requiere reparación.")
    with db._conexion() as con:
        r = con.execute("SELECT * FROM vehiculo_registros WHERE id = ? AND tipo = 'NOVEDAD'", (registro_id,)).fetchone()
        if not r:
            raise ErrorTraslado("La novedad no existe.")
        anterior = r["estado_novedad"] or ("REPARADA" if r["resuelta"] else "CARGADA")
        if anterior == estado and not nota:
            raise ErrorTraslado("La novedad ya está en ese estado.")
        cerrada = estado in NOVEDAD_CERRADA
        con.execute("UPDATE vehiculo_registros SET estado_novedad = ?, resuelta = ?, resuelta_en = ?, resuelta_por = ?, resolucion = ? WHERE id = ?",
                    (estado, int(cerrada), db._ahora() if cerrada else None, _quien(usuario) if cerrada else None,
                     nota if cerrada else None, registro_id))
        con.execute("INSERT INTO vehiculo_novedad_estados (registro_id, estado, nota, usuario, fecha) VALUES (?,?,?,?,?)",
                    (registro_id, estado, nota, _quien(usuario), db._ahora()))
        tr.auditar("vehiculo", r["vehiculo_id"], "Novedad mecánica: estado", f"{ESTADOS_NOVEDAD[anterior]} · {r['detalle']}",
                   ESTADOS_NOVEDAD[estado] + (f" — {nota}" if nota else ""), con=con)
    if estado_vehiculo_nuevo in ("TALLER", "DISPONIBLE"):
        tr.estado_vehiculo(r["vehiculo_id"], estado_vehiculo_nuevo, f"Novedad mecánica: {r['detalle']}"[:300] if estado_vehiculo_nuevo == "TALLER" else None)
    return r["vehiculo_id"]


def resolver_novedad(registro_id, resolucion, usuario):
    return cambiar_estado_novedad(registro_id, "REPARADA", resolucion, usuario)


def historial_novedades(vehiculo_id):
    """{registro_id: [pasos]} de las novedades del vehículo."""
    out = {}
    with db._conexion() as con:
        for f in con.execute("""SELECT h.* FROM vehiculo_novedad_estados h JOIN vehiculo_registros r ON r.id = h.registro_id
                                WHERE r.vehiculo_id = ? ORDER BY h.fecha, h.id""", (vehiculo_id,)):
            d = dict(f)
            d["etiqueta"] = ESTADOS_NOVEDAD.get(d["estado"], d["estado"])
            out.setdefault(d["registro_id"], []).append(d)
    return out


def registros(vehiculo_id, limite=40):
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(
            """SELECT r.*, c.nombre AS chofer_nombre FROM vehiculo_registros r LEFT JOIN choferes c ON c.id = r.chofer_id
               WHERE r.vehiculo_id = ? ORDER BY r.fecha DESC, r.id DESC LIMIT ?""", (vehiculo_id, limite))]
    for f in filas:
        f["etiqueta"] = ETIQ_REGISTRO[f["tipo"]]
        if f["tipo"] == "NOVEDAD":
            f["estado_novedad"] = f.get("estado_novedad") or ("REPARADA" if f["resuelta"] else "CARGADA")
            f["estado_etiqueta"] = ESTADOS_NOVEDAD[f["estado_novedad"]]
    return filas


def resumen_registros(vehiculo_id, hoy=None):
    """Combustible y vales del mes en curso, y novedades mecánicas sin resolver."""
    hoy = hoy or date.today()
    desde = hoy.replace(day=1).isoformat()
    with db._conexion() as con:
        c = con.execute("""SELECT COALESCE(SUM(litros), 0) AS litros, COALESCE(SUM(importe), 0) AS importe, COUNT(*) AS cargas
                           FROM vehiculo_registros WHERE vehiculo_id = ? AND tipo = 'COMBUSTIBLE' AND fecha >= ?""", (vehiculo_id, desde)).fetchone()
        va = con.execute("""SELECT COALESCE(SUM(cantidad), 0) AS vales FROM vehiculo_registros
                            WHERE vehiculo_id = ? AND tipo = 'VALES' AND fecha >= ?""", (vehiculo_id, desde)).fetchone()
        abiertas = [dict(f) for f in con.execute("""SELECT * FROM vehiculo_registros WHERE vehiculo_id = ? AND tipo = 'NOVEDAD' AND resuelta = 0
                                                    ORDER BY CASE gravedad WHEN 'GRAVE' THEN 0 WHEN 'MODERADA' THEN 1 ELSE 2 END, fecha DESC""", (vehiculo_id,))]
    for a in abiertas:
        a["estado_novedad"] = a.get("estado_novedad") or "CARGADA"
        a["estado_etiqueta"] = ESTADOS_NOVEDAD[a["estado_novedad"]]
    return {"litros_mes": round(c["litros"], 1), "importe_mes": round(c["importe"], 2), "cargas_mes": c["cargas"], "vales_mes": va["vales"],
            "novedades_abiertas": abiertas}


def novedades_abiertas_por_vehiculo():
    with db._conexion() as con:
        return {f["vehiculo_id"]: (f["n"], f["graves"], f["en_reparacion"]) for f in con.execute(
            """SELECT vehiculo_id, COUNT(*) AS n, SUM(gravedad = 'GRAVE') AS graves, SUM(estado_novedad = 'EN_REPARACION') AS en_reparacion
               FROM vehiculo_registros
               WHERE tipo = 'NOVEDAD' AND resuelta = 0 GROUP BY vehiculo_id""")}


def controles(vehiculo_id):
    with db._conexion() as con:
        try:  # propuesta 64: proveedor y responsable de cada control
            return [dict(f) for f in con.execute("""SELECT c.*, p.nombre AS proveedor_nombre, p.activo AS proveedor_activo, u.nombre AS responsable_nombre
                                                    FROM vehiculo_controles c LEFT JOIN proveedores p ON p.id = c.proveedor_id
                                                    LEFT JOIN usuarios u ON u.id = c.responsable_id
                                                    WHERE c.vehiculo_id = ? ORDER BY c.fecha DESC, c.id DESC""", (vehiculo_id,))]
        except Exception:  # base sin la migración 0053
            return [dict(f) for f in con.execute("SELECT * FROM vehiculo_controles WHERE vehiculo_id = ? ORDER BY fecha DESC, id DESC", (vehiculo_id,))]


def dias_aviso():
    """Propuesta 64: días de aviso por tipo de control (VTV 45, seguro 30, matafuego 15, el resto 30, o lo configurado)."""
    from dgpdh.dominio import avisos_flota as af
    try:
        from dgpdh.servicios import proveedores
        with db._conexion() as con:
            conf = proveedores.dias_configurados(con)
    except Exception:
        conf = {}
    return {t: af.dias_aviso(t, conf) for t in TIPOS}


def avisar_responsables(hoy=None, forzar=False):
    """Propuesta 64: avisos internos a la persona responsable de cada vencimiento (una vez por día como mucho)."""
    hoy = hoy or date.today()
    try:
        from dgpdh.servicios import proveedores
        with db._conexion() as con:
            f = con.execute("SELECT valor FROM ajustes WHERE clave = 'flota_avisos_revisado'").fetchone()
            if f and f[0] == hoy.isoformat() and not forzar:
                return 0
            con.execute("INSERT INTO ajustes (clave, valor) VALUES ('flota_avisos_revisado', ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (hoy.isoformat(),))
            return proveedores.avisar_responsables(con, hoy, TIPOS)
    except Exception:
        import logging
        logging.exception("No se pudieron mandar los avisos de vencimientos a los responsables")
        return 0


def _nivel(vence_fecha, vence_km, km_actual, hoy, aviso_dias=None):
    """vencido | pronto | ok | sin_dato, mirando la fecha y los km (gana el más urgente)."""
    niveles = []
    if vence_fecha:
        dias = (tr._fecha(vence_fecha) - hoy).days
        niveles.append("vencido" if dias < 0 else ("pronto" if dias <= (AVISO_DIAS if aviso_dias is None else aviso_dias) else "ok"))
    if vence_km and km_actual is not None:
        falta = vence_km - km_actual
        niveles.append("vencido" if falta <= 0 else ("pronto" if falta <= AVISO_KM else "ok"))
    for n in ("vencido", "pronto", "ok"):
        if n in niveles:
            return n
    return "sin_dato"


def estado_vehiculo(v, hoy=None):
    """Para cada control: el último registro, el próximo vencimiento y su semáforo."""
    hoy = hoy or date.today()
    ultimos = {}
    for c in controles(v["id"]):
        ultimos.setdefault(c["tipo"], c)
    filas = []
    avisos = dias_aviso()
    for tipo in list(BASICOS) + [t for t in TIPOS if t not in BASICOS and t in ultimos]:
        c = ultimos.get(tipo)
        vence_f = (c or {}).get("vence_fecha") or (v.get(_CAMPO_VEHICULO[tipo]) if tipo in _CAMPO_VEHICULO else None)
        vence_km = (c or {}).get("vence_km")
        nivel = _nivel(vence_f, vence_km, v.get("km"), hoy, avisos.get(tipo))
        filas.append({"tipo": tipo, "nombre": TIPOS[tipo], "ultimo": c, "vence_fecha": vence_f, "vence_km": vence_km, "nivel": nivel, "dias_aviso": avisos.get(tipo),
                      "faltan_km": (vence_km - v["km"]) if (vence_km and v.get("km") is not None) else None,
                      "faltan_dias": (tr._fecha(vence_f) - hoy).days if vence_f else None})
    orden = {"vencido": 0, "pronto": 1, "sin_dato": 2, "ok": 3}
    peor = min((f["nivel"] for f in filas), key=lambda n: orden[n]) if filas else "sin_dato"
    return {"controles": filas, "peor": peor, "n_alertas": sum(1 for f in filas if f["nivel"] in ("vencido", "pronto"))}


def ocupacion(vehiculo_id, dias=60, hoy=None):
    """Uso en los últimos días: salidas, personas promedio y ocupación respecto de la capacidad."""
    hoy = hoy or date.today()
    desde = (hoy - timedelta(days=dias)).isoformat()
    with db._conexion() as con:
        v = con.execute("SELECT capacidad FROM vehiculos WHERE id = ?", (vehiculo_id,)).fetchone()
    viajes = [x for x in tr.listar_viajes(desde=desde, hasta=hoy.isoformat()) if x.get("vehiculo_id") == vehiculo_id and x["estado"] == "CONFIRMADO"]
    salidas = {(x["fecha"]) for x in viajes}
    personas = [sum(1 for p in x["participantes"] if not p.get("auto_propio")) for x in viajes]
    prom = round(sum(personas) / len(personas), 1) if personas else 0
    cap = (v["capacidad"] if v else 0) or 0
    return {"dias": dias, "viajes": len(viajes), "salidas": len(salidas), "personas_promedio": prom,
            "ocupacion_pct": round(100 * prom / cap) if cap and personas else None}


def panel(hoy=None):
    """Todos los vehículos (menos los fuera de circulación al final) con su semáforo de mantenimiento."""
    out = []
    nov = novedades_abiertas_por_vehiculo()
    for v in tr.listar_vehiculos():
        e = estado_vehiculo(v, hoy)
        n, graves, en_rep = nov.get(v["id"], (0, 0, 0))
        out.append(dict(v, estado_flota=e, novedades=n, novedades_graves=graves or 0, novedades_en_reparacion=en_rep or 0))
    return out


def vencimientos_controles(dias=30, hoy=None):
    """Para el aviso general de vencimientos: controles de mantenimiento (no VTV/seguro/service, que ya avisa
    traslados.vencimientos) vencidos o próximos por fecha o por kilómetros."""
    hoy = hoy or date.today()
    out = []
    for v in tr.listar_vehiculos():
        if v["estado"] == "FUERA_DE_CIRCULACION":
            continue
        for f in estado_vehiculo(v, hoy)["controles"]:
            if f["tipo"] in _CAMPO_VEHICULO or f["nivel"] not in ("vencido", "pronto"):
                continue
            quien = f"{v['dominio']} ({v['marca'] or ''} {v['modelo'] or ''})".strip()
            if f["vence_fecha"] and f["faltan_dias"] is not None and f["faltan_dias"] <= dias:
                out.append({"quien": quien, "que": f["nombre"], "fecha": f["vence_fecha"], "vencido": f["faltan_dias"] < 0})
            elif f["faltan_km"] is not None:
                out.append({"quien": quien, "que": f"{f['nombre']} (faltan {max(0, f['faltan_km'])} km)", "fecha": hoy.isoformat(), "vencido": f["faltan_km"] <= 0})
    return out


def ficha_chofer(chofer_id, hoy=None):
    hoy = hoy or date.today()
    c = next((x for x in tr.listar_choferes() if x["id"] == chofer_id), None)
    if not c:
        return None
    lunes = tr.lunes_de(hoy)
    agenda = [x for x in tr.listar_viajes(desde=lunes.isoformat(), hasta=(lunes + timedelta(days=13)).isoformat())
              if x.get("chofer_id") == chofer_id and x["estado"] != "CANCELADO"]
    hechos = [x for x in tr.listar_viajes(desde=(hoy - timedelta(days=30)).isoformat(), hasta=hoy.isoformat())
              if x.get("chofer_id") == chofer_id and x["estado"] == "CONFIRMADO"]
    usuario = db.obtener_usuario(c["usuario_id"]) if c.get("usuario_id") else None
    licencia_dias = (tr._fecha(c["licencia_vence"]) - hoy).days if c.get("licencia_vence") else None
    return {"chofer": c, "usuario": usuario, "agenda": agenda, "salidas_30": len({x["fecha"] for x in hechos}), "viajes_30": len(hechos),
            "licencia_dias": licencia_dias, "lunes": lunes}
