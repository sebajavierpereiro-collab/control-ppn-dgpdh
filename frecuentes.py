"""Viajes frecuentes (recurrentes).

Reglas acordadas con la Dirección:
  * La plantilla guarda QUIÉNES viajan y ADÓNDE, qué días y en qué horario. No guarda chofer ni vehículo:
    eso lo asigna la coordinación en cada viaje.
  * Cada semana el sistema PRECARGA los viajes de la semana siguiente en estado «A_CONFIRMAR».
    La persona (o su coordinadora) los confirma la semana anterior, hasta el jueves, como cualquier viaje
    de rutina. Recién al confirmarse pasan a «pedido de chofer» (o confirmados, si van por sus propios medios).
  * Un precargado que nadie confirma NUNCA cuenta como realizado ni entra en las estadísticas.
  * Solo evita volver a cargar: se puede quitar o sumar gente, cambiar horarios o marcar «no viaja».
"""
import time
from collections import defaultdict
from datetime import date, timedelta

import database as db
import traslados as tr
from traslados import ErrorTraslado

FRECUENCIAS = {"SEMANAL": "Todas las semanas", "QUINCENAL": "Cada 15 días", "MENSUAL": "Una vez por mes"}
DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
DIAS_CORTOS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MOTIVO_NO_VIAJA = "No se realiza esta semana (viaje frecuente)."
# Propuesta 80: los precargados que nadie confirmó no se borran al pausar o terminar el frecuente: quedan descartados
# (cancelados con este motivo, fuera de las agendas y estadísticas) y vuelven solos si el frecuente se reactiva.
MOTIVO_DESCARTADO = tr.MOTIVO_DESCARTADO

_ULTIMA_GENERACION = {"t": 0.0}


# ---------------------------------------------------------------- utilidades
def _fecha(f):
    return tr._fecha(f)


def _dias(texto):
    return sorted({int(x) for x in str(texto or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6})


def describir(p):
    """«Lunes y jueves, todas las semanas»."""
    ds = [DIAS[d] for d in _dias(p["dias"])]
    txt = ds[0] if len(ds) == 1 else ", ".join(ds[:-1]) + " y " + ds[-1] if ds else "—"
    if p["frecuencia"] == "MENSUAL":
        n = (_fecha(p["desde"]).day - 1) // 7 + 1
        return f"{txt}, una vez por mes ({n}.ª semana)"
    return f"{txt}, {FRECUENCIAS.get(p['frecuencia'], '').lower()}"


def toca(p, f):
    """¿La plantilla tiene viaje la fecha f?"""
    f = _fecha(f)
    desde = _fecha(p["desde"])
    if f < desde or (p.get("hasta") and f > _fecha(p["hasta"])):
        return False
    if f.weekday() not in _dias(p["dias"]):
        return False
    if p["frecuencia"] == "QUINCENAL":
        return ((tr.lunes_de(f) - tr.lunes_de(desde)).days // 7) % 2 == 0
    if p["frecuencia"] == "MENSUAL":
        return (f.day - 1) // 7 == (desde.day - 1) // 7
    return True


def ventana(hoy=None):
    """Desde mañana hasta el domingo de la semana próxima: lo que se confirma esta semana."""
    hoy = hoy or date.today()
    return hoy + timedelta(days=1), tr.lunes_de(hoy) + timedelta(days=13)


# ---------------------------------------------------------------- plantillas
def _participantes(con, ids):
    por = defaultdict(list)
    if ids:
        for f in con.execute("SELECT * FROM viaje_frecuente_participantes WHERE frecuente_id IN (%s) ORDER BY id" % ",".join("?" * len(ids)), list(ids)):
            por[f["frecuente_id"]].append(dict(f))
    return por


def obtener(fid):
    with db._conexion() as con:
        f = con.execute("""SELECT f.*, u.nombre AS unidad_nombre FROM viajes_frecuentes f LEFT JOIN unidades u ON u.id = f.unidad_id
                           WHERE f.id = ?""", (fid,)).fetchone()
        if not f:
            return None
        p = dict(f)
        p["participantes"] = _participantes(con, [fid])[fid]
    return _decorar(p)


def _decorar(p):
    p["destino"] = p.get("unidad_nombre") or p.get("destino_otro") or "—"
    p["descripcion"] = describir(p)
    p["dias_lista"] = _dias(p["dias"])
    return p


def crear(datos, participantes, usuario):
    """datos: circuito, unidad_id/destino_otro, dias (lista o texto), frecuencia, desde, hasta, horarios, motivo, observaciones."""
    dias = _dias(",".join(str(x) for x in datos.get("dias") or []) if isinstance(datos.get("dias"), (list, tuple)) else datos.get("dias"))
    if not dias:
        raise ErrorTraslado("Elegí al menos un día de la semana para el viaje frecuente.")
    frecuencia = datos.get("frecuencia") if datos.get("frecuencia") in FRECUENCIAS else "SEMANAL"
    parts = tr._limpiar_participantes(participantes)
    if not parts:
        raise ErrorTraslado("Sumá al menos una persona al viaje frecuente.")
    d = tr._validar_datos(dict(datos, fecha=datos.get("desde") or date.today().isoformat(), modalidad=tr._modalidad_de(parts)))
    hasta = None
    if datos.get("hasta"):
        hasta = _fecha(datos["hasta"]).isoformat()
        if hasta < d["fecha"]:
            raise ErrorTraslado("La fecha de finalización tiene que ser posterior al inicio.")
    circuito = datos.get("circuito") if datos.get("circuito") in tr.CIRCUITOS else tr.circuito_de(usuario)
    with db._conexion() as con:
        fid = con.execute(
            """INSERT INTO viajes_frecuentes (circuito, unidad_id, destino_otro, dias, frecuencia, desde, hasta, hora_salida, hora_regreso,
                                              motivo, observaciones, equipo_nombre, creado_por_id, creado_por, creado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (circuito, d["unidad_id"], d["destino_otro"], ",".join(map(str, dias)), frecuencia, d["fecha"], hasta, d["hora_salida"], d["hora_regreso"],
             d["motivo"], d["observaciones"], usuario.get("equipo_nombre"), usuario["id"], usuario["nombre"], db._ahora())).lastrowid
        for p in parts:
            externo_id = p.get("externo_id")
            if not p.get("usuario_id"):
                externo_id = tr._registrar_externo(con, p["nombre"], p.get("area"), p.get("observaciones"), externo_id)["id"]
            con.execute("""INSERT INTO viaje_frecuente_participantes (frecuente_id, usuario_id, externo_id, nombre, equipo_nombre, auto_propio)
                           VALUES (?,?,?,?,?,?)""", (fid, p.get("usuario_id"), externo_id, p["nombre"], p.get("equipo_nombre"), p["auto_propio"]))
        tr.auditar("frecuente", fid, "Alta", nuevo=f"{d['fecha']} · {','.join(DIAS_CORTOS[x] for x in dias)} · {frecuencia} · {len(parts)} persona(s)", con=con)
    return fid


def crear_desde_viaje(viaje_id, frecuencia, dias, hasta, usuario):
    v = tr.obtener_viaje(viaje_id)
    if not v:
        raise ErrorTraslado("El viaje no existe.")
    dias = dias or [_fecha(v["fecha"]).weekday()]
    parts = [dict(usuario_id=p["usuario_id"], externo_id=p.get("externo_id"), nombre=p["nombre"], equipo_nombre=p["equipo_nombre"],
                  auto_propio=p["auto_propio"]) for p in v["participantes"]]
    fid = crear(dict(circuito=v.get("circuito"), unidad_id=v["unidad_id"], destino_otro=v["destino_otro"], dias=dias, frecuencia=frecuencia,
                     desde=v["fecha"], hasta=hasta, hora_salida=v["hora_salida"], hora_regreso=v["hora_regreso"], motivo=v["motivo"],
                     observaciones=v["observaciones"]), parts, usuario)
    with db._conexion() as con:  # el viaje de origen queda como la primera vuelta de la serie
        con.execute("UPDATE viajes SET frecuente_id = ? WHERE id = ? AND frecuente_id IS NULL", (fid, viaje_id))
    return fid


def puede_ver(usuario, p):
    if tr.puede_coordinar(usuario) or p["creado_por_id"] == usuario["id"]:
        return True
    if any(x.get("usuario_id") == usuario["id"] for x in p["participantes"]):
        return True
    return usuario["rol"] == "COORDINADOR" and bool(usuario.get("equipo_nombre")) and (
        (p.get("equipo_nombre") or "").lower() == usuario["equipo_nombre"].lower()
        or any((x.get("equipo_nombre") or "").lower() == usuario["equipo_nombre"].lower() for x in p["participantes"]))


def listar(usuario, incluir_eliminadas=False):
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(
            """SELECT f.*, u.nombre AS unidad_nombre FROM viajes_frecuentes f LEFT JOIN unidades u ON u.id = f.unidad_id
               WHERE f.activo >= ? ORDER BY f.activo DESC, f.id DESC""", (-1 if incluir_eliminadas else 0,))]
        por = _participantes(con, [f["id"] for f in filas])
    out = []
    for f in filas:
        f["participantes"] = por[f["id"]]
        if puede_ver(usuario, f):
            out.append(_decorar(f))
    return out


def cambiar_estado(fid, accion, usuario):
    p = obtener(fid)
    if not p or not puede_ver(usuario, p):
        raise ErrorTraslado("Ese viaje frecuente no existe.")
    if not (tr.puede_coordinar(usuario) or p["creado_por_id"] == usuario["id"] or usuario["rol"] == "COORDINADOR"):
        raise ErrorTraslado("Solo quien lo creó, su coordinación o la coordinación de traslados pueden cambiarlo.")
    valor = {"pausar": 0, "activar": 1, "eliminar": -1}.get(accion)
    if valor is None:
        raise ErrorTraslado("Acción desconocida.")
    with db._conexion() as con:
        con.execute("UPDATE viajes_frecuentes SET activo = ? WHERE id = ?", (valor, fid))
        if valor != 1:  # los precargados futuros que nadie confirmó se retiran (no se tocan los confirmados)
            _descartar(con, fid, date.today().isoformat(), "Pausado" if valor == 0 else "Eliminado")
        tr.auditar("frecuente", fid, {"pausar": "Pausado", "activar": "Reactivado", "eliminar": "Eliminado"}[accion], con=con)


def _descartar(con, fid, despues_de, por):
    ids = [f[0] for f in con.execute("SELECT id FROM viajes WHERE frecuente_id = ? AND estado = 'A_CONFIRMAR' AND fecha > ?", (fid, despues_de))]
    for vid in ids:
        con.execute("UPDATE viajes SET estado = 'CANCELADO', motivo_cancelacion = ?, actualizado_en = ? WHERE id = ?", (MOTIVO_DESCARTADO, db._ahora(), vid))
        tr.auditar("viaje", vid, "Descartado (viaje frecuente)", "A_CONFIRMAR", f"{por}: el precargado no se confirmó", con=con)
    return len(ids)


def fijar_fin(fid, hasta, usuario):
    p = obtener(fid)
    if not p or not puede_ver(usuario, p):
        raise ErrorTraslado("Ese viaje frecuente no existe.")
    hasta = _fecha(hasta).isoformat() if hasta else None
    with db._conexion() as con:
        con.execute("UPDATE viajes_frecuentes SET hasta = ? WHERE id = ?", (hasta, fid))
        if hasta:
            _descartar(con, fid, hasta, "Finaliza")
        tr.auditar("frecuente", fid, "Finaliza", p.get("hasta") or "sin fecha", hasta or "sin fecha", con=con)


# ---------------------------------------------------------------- precarga semanal
def _feriados(con, desde, hasta):
    try:
        from dgpdh.servicios import feriados
        return feriados.conjunto(con, desde, hasta)
    except Exception:
        return frozenset()


def generar(hoy=None, forzar=False):
    """Crea los viajes «a confirmar» de la ventana (desde mañana hasta el domingo de la semana próxima).
    Es idempotente: una fecha ya generada (aunque después se haya marcado «no viaja») no se vuelve a crear."""
    if hoy is None and not forzar and time.time() - _ULTIMA_GENERACION["t"] < 300:
        return 0
    _ULTIMA_GENERACION["t"] = time.time()
    desde, hasta = ventana(hoy)
    creados, avisar = 0, defaultdict(int)
    with db._conexion() as con:
        no_laborables = _feriados(con, desde, hasta)  # propuesta 69: en un feriado no se precarga
        plantillas = [dict(f) for f in con.execute("SELECT * FROM viajes_frecuentes WHERE activo = 1")]
        por = _participantes(con, [p["id"] for p in plantillas])
        for p in plantillas:
            parts = por[p["id"]]
            if not parts:
                continue
            f = desde
            while f <= hasta:
                if toca(p, f) and f not in no_laborables:  # propuesta 80: un precargado descartado vuelve al reactivar
                    rec = con.execute("""UPDATE viajes SET estado = 'A_CONFIRMAR', motivo_cancelacion = NULL, actualizado_en = ?
                                         WHERE frecuente_id = ? AND fecha = ? AND estado = 'CANCELADO' AND motivo_cancelacion = ?""",
                                      (db._ahora(), p["id"], f.isoformat(), MOTIVO_DESCARTADO))
                    if rec.rowcount:
                        vid = con.execute("SELECT id FROM viajes WHERE frecuente_id = ? AND fecha = ?", (p["id"], f.isoformat())).fetchone()[0]
                        tr.auditar("viaje", vid, "Recuperado (viaje frecuente reactivado)", "Descartado", "A_CONFIRMAR", con=con)
                        creados += 1
                        for uid in {x["usuario_id"] for x in parts if x["usuario_id"]} | {p["creado_por_id"]}:
                            if uid:
                                avisar[uid] += 1
                if toca(p, f) and f not in no_laborables and not con.execute("SELECT 1 FROM viajes WHERE frecuente_id = ? AND fecha = ?", (p["id"], f.isoformat())).fetchone():
                    fuera_amba = p["circuito"] == "AMBA" and tr.es_amba(p["unidad_id"]) is False
                    lista = [dict(x, auto_propio=1 if (fuera_amba or p["circuito"] == "DELEGACIONES") else x["auto_propio"]) for x in parts]
                    modalidad = tr._modalidad_de(lista)
                    ahora = db._ahora()
                    cur = con.execute(
                        """INSERT OR IGNORE INTO viajes (fecha, hora_salida, hora_regreso, unidad_id, destino_otro, motivo, modalidad, estado, inmediato,
                                               observaciones, origen, circuito, frecuente_id, creado_por_id, creado_por, creado_en, actualizado_en)
                           VALUES (?,?,?,?,?,?,?,'A_CONFIRMAR',0,?,'FRECUENTE',?,?,?,?,?,?)""",
                        (f.isoformat(), p["hora_salida"], p["hora_regreso"], p["unidad_id"], p["destino_otro"], p["motivo"], modalidad,
                         p["observaciones"], p["circuito"], p["id"], p["creado_por_id"], p["creado_por"], ahora, ahora))
                    if cur.rowcount:
                        vid = cur.lastrowid
                        for x in lista:
                            con.execute("""INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, equipo_nombre, auto_propio, externo_id)
                                           VALUES (?,?,?,?,?,?)""", (vid, x["usuario_id"], x["nombre"], x["equipo_nombre"], x["auto_propio"], x["externo_id"]))
                        tr.auditar("viaje", vid, "Precargado", nuevo=f"viaje frecuente #{p['id']}", con=con)
                        creados += 1
                        for uid in {x["usuario_id"] for x in parts if x["usuario_id"]} | {p["creado_por_id"]}:
                            if uid:
                                avisar[uid] += 1
                f += timedelta(days=1)
    for uid, n in avisar.items():
        db.notificar_usuarios([uid], f"Hay {n} viaje(s) frecuente(s) precargado(s) para confirmar hasta el jueves.", "/traslados#para-confirmar")
    return creados


# ---------------------------------------------------------------- confirmación
def puede_confirmar(usuario, v):
    if tr.puede_coordinar(usuario) or v.get("creado_por_id") == usuario["id"]:
        return True
    if any(p.get("usuario_id") == usuario["id"] for p in v["participantes"]):
        return True
    eq = (usuario.get("equipo_nombre") or "").lower()
    return usuario["rol"] == "COORDINADOR" and bool(eq) and any((p.get("equipo_nombre") or "").lower() == eq for p in v["participantes"])


def para_confirmar(usuario, hoy=None, circuito=None):
    hoy = hoy or date.today()
    viajes = tr.listar_viajes(desde=hoy.isoformat(), estado="A_CONFIRMAR", circuito=circuito)
    return [v for v in viajes if puede_confirmar(usuario, v)]


def confirmar(viaje_id, usuario, hoy=None):
    hoy = hoy or date.today()
    v = tr.obtener_viaje(viaje_id)
    if not v:
        raise ErrorTraslado("El viaje no existe.")
    if v["estado"] != "A_CONFIRMAR":
        raise ErrorTraslado("Ese viaje ya estaba confirmado o cancelado.")
    if v["fecha"] < hoy.isoformat():
        raise ErrorTraslado("Ese viaje ya pasó: no se puede confirmar.")
    if not puede_confirmar(usuario, v):
        raise ErrorTraslado("Solo pueden confirmarlo quienes viajan, su coordinación o la coordinación de traslados.")
    estado = "SOLICITADO" if v["modalidad"] == "CHOFER" else "CONFIRMADO"
    inmediato = v.get("circuito") == "AMBA" and tr.es_inmediato(v["fecha"], hoy)
    with db._conexion() as con:
        con.execute("UPDATE viajes SET estado = ?, inmediato = ?, actualizado_en = ? WHERE id = ? AND estado = 'A_CONFIRMAR'",
                    (estado, 1 if inmediato else 0, db._ahora(), viaje_id))
        tr.auditar("viaje", viaje_id, "Confirmado (viaje frecuente)", "A_CONFIRMAR", estado + (" · fuera de plazo" if inmediato else ""), con=con)
    if estado == "SOLICITADO":
        db.notificar_usuarios(tr.usuarios_coordinadores(), f"Nuevo pedido de chofer para el {v['fecha']} (viaje frecuente)" + (" (fuera de plazo)" if inmediato else "") +
                              f": {v['destino']}.", f"/traslados/{viaje_id}")
    return estado


def no_viaja(viaje_id, usuario):
    v = tr.obtener_viaje(viaje_id)
    if not v or not puede_confirmar(usuario, v):
        raise ErrorTraslado("No podés cambiar ese viaje.")
    tr.cancelar(viaje_id, MOTIVO_NO_VIAJA, usuario)


# ---------------------------------------------------------------- detección de patrones
def sugerencias(usuario, hoy=None, semanas=8, minimo=3):
    """Viajes que se repiten (mismo destino y mismo día de la semana, en al menos `minimo` semanas distintas de las
    últimas `semanas`) y que todavía no son frecuentes. Mira los viajes de la persona (o de su equipo, si coordina)."""
    hoy = hoy or date.today()
    desde = (hoy - timedelta(weeks=semanas)).isoformat()
    viajes = [v for v in tr.listar_viajes(desde=desde, hasta=hoy.isoformat()) if v["estado"] != "CANCELADO" and not v.get("frecuente_id")]
    eq = (usuario.get("equipo_nombre") or "").lower()
    def mio(v):
        if v.get("creado_por_id") == usuario["id"] or any(p.get("usuario_id") == usuario["id"] for p in v["participantes"]):
            return True
        return usuario["rol"] == "COORDINADOR" and eq and any((p.get("equipo_nombre") or "").lower() == eq for p in v["participantes"])
    grupos = defaultdict(list)
    for v in viajes:
        if v.get("unidad_id") and mio(v):
            grupos[(v["unidad_id"], _fecha(v["fecha"]).weekday())].append(v)
    activas = [p for p in listar(usuario) if p["activo"] == 1]
    out = []
    for (unidad, dia), vs in grupos.items():
        if len({tr.lunes_de(v["fecha"]) for v in vs}) < minimo:
            continue
        if any(p["unidad_id"] == unidad and dia in p["dias_lista"] for p in activas):
            continue
        ultimo = max(vs, key=lambda v: v["fecha"])
        out.append({"unidad_id": unidad, "destino": ultimo["destino"], "dia": dia, "dia_nombre": DIAS[dia], "veces": len(vs), "viaje_id": ultimo["id"],
                    "personas": [p["nombre"] for p in ultimo["participantes"]]})
    return sorted(out, key=lambda x: -x["veces"])


# ---------------------------------------------------------------- «Mi semana»: lo que suele cargar y todavía no cargó
def faltan_cargar(usuario, hoy=None, semanas=8, minimo=3):
    """Viajes que la persona hace habitualmente (mismo destino y día, en al menos `minimo` de las últimas `semanas`)
    y que todavía no están cargados para la semana próxima (ni como frecuentes precargados)."""
    hoy = hoy or date.today()
    lunes_prox = tr.lunes_de(hoy) + timedelta(days=7)
    proxima = [v for v in tr.listar_viajes(desde=lunes_prox.isoformat(), hasta=(lunes_prox + timedelta(days=6)).isoformat())
               if v["estado"] != "CANCELADO" and (v.get("creado_por_id") == usuario["id"] or any(p.get("usuario_id") == usuario["id"] for p in v["participantes"]))]
    ya = {(v.get("unidad_id"), _fecha(v["fecha"]).weekday()) for v in proxima}
    desde = (tr.lunes_de(hoy) - timedelta(weeks=semanas)).isoformat()
    hechos = [v for v in tr.listar_viajes(desde=desde, hasta=(tr.lunes_de(hoy) - timedelta(days=1)).isoformat())
              if v["estado"] != "CANCELADO" and v.get("unidad_id")
              and (v.get("creado_por_id") == usuario["id"] or any(p.get("usuario_id") == usuario["id"] for p in v["participantes"]))]
    grupos = defaultdict(list)
    for v in hechos:
        grupos[(v["unidad_id"], _fecha(v["fecha"]).weekday())].append(v)
    out = []
    for (unidad, dia), vs in grupos.items():
        if len({tr.lunes_de(v["fecha"]) for v in vs}) < minimo or (unidad, dia) in ya:
            continue
        ultimo = max(vs, key=lambda v: v["fecha"])
        out.append({"unidad_id": unidad, "destino": ultimo["destino"], "dia": dia, "dia_nombre": DIAS[dia], "veces": len(vs),
                    "fecha": (lunes_prox + timedelta(days=dia)).isoformat(), "viaje_id": ultimo["id"]})
    return sorted(out, key=lambda x: x["fecha"])


def mi_semana(usuario, hoy=None):
    hoy = hoy or date.today()
    lunes = tr.lunes_de(hoy)
    mios = [v for v in tr.listar_viajes(desde=lunes.isoformat(), hasta=(lunes + timedelta(days=13)).isoformat())
            if v.get("creado_por_id") == usuario["id"] or any(p.get("usuario_id") == usuario["id"] for p in v["participantes"])]
    semanas = []
    for k in (0, 1):
        l = lunes + timedelta(days=7 * k)
        dias = []
        for i in range(7):
            f = (l + timedelta(days=i)).isoformat()
            vs = [v for v in mios if v["fecha"] == f]
            if vs or i < 5:
                dias.append({"fecha": f, "nombre": DIAS[i], "viajes": vs})
        semanas.append({"titulo": "Esta semana" if k == 0 else "Semana próxima", "lunes": l.isoformat(), "dias": dias})
    return {"semanas": semanas, "a_confirmar": para_confirmar(usuario, hoy=hoy), "faltan": faltan_cargar(usuario, hoy=hoy),
            "limite": tr.limite_planificacion(lunes + timedelta(days=7)).isoformat(), "hoy": hoy.isoformat()}
