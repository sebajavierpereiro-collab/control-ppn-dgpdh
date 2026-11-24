"""Tablero de Dirección de Autos PPN: vista gerencial de los viajes a unidades (AMBA y resto del país).

  * Indicadores: realizados vs. programados, cobertura territorial, alertas activas, pendientes de asignar.
  * Por equipo: viajes a la unidad, personas, entrevistas informadas, vehículo institucional vs. propios medios,
    fuera de plazo, cancelados, carga respecto del promedio y avance de la meta del trimestre.
  * Territorio: AMBA agrupado por localidad (Ezeiza, Marcos Paz, CABA…) y el resto del país por provincia.
  * Metas: viajes mínimos por equipo al año, repartidos en trimestres.
  * Entrevistas: cada persona que viaja y tiene marcado «carga entrevistas» informa cuántas hizo.

Todo se calcula en el servidor con los datos propios: no usa servicios externos ni librerías de internet.
"""
import math
from collections import Counter, defaultdict
from datetime import date, timedelta

import database as db
import traslados as tr

PERIODOS = {"semana": "Esta semana", "mes": "Este mes", "trimestre": "Este trimestre", "anio": "Este año", "hoy": "Hoy",
            "personalizado": "Personalizado"}


def trimestre_de(f):
    return (f.month - 1) // 3 + 1


def limites_trimestre(anio, t):
    ini = date(anio, 3 * (t - 1) + 1, 1)
    fin = (date(anio + 1, 1, 1) if t == 4 else date(anio, 3 * t + 1, 1)) - timedelta(days=1)
    return ini, fin


def rango(periodo, desde="", hasta="", hoy=None):
    """(desde, hasta, etiqueta) del período elegido."""
    hoy = hoy or date.today()
    if periodo == "hoy":
        d, h = hoy, hoy
    elif periodo == "semana":
        d = hoy - timedelta(days=hoy.weekday()); h = d + timedelta(days=6)
    elif periodo == "trimestre":
        d, h = limites_trimestre(hoy.year, trimestre_de(hoy))
    elif periodo == "anio":
        d, h = date(hoy.year, 1, 1), date(hoy.year, 12, 31)
    elif periodo == "personalizado" and desde and hasta:
        d, h = tr._fecha(desde), tr._fecha(hasta)
        if h < d:
            d, h = h, d
    else:
        periodo = "mes"
        d = hoy.replace(day=1)
        h = (d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)) - timedelta(days=1)
    return d.isoformat(), h.isoformat(), periodo


# ---------------------------------------------------------------- entrevistas
def debe_cargar_entrevistas(usuario_id, cache=None):
    if not usuario_id:
        return False
    if cache is not None:
        return usuario_id in cache
    u = db.obtener_usuario(usuario_id)
    return bool(u and u.get("carga_entrevistas"))


def usuarios_que_cargan():
    return {u["id"] for u in db.listar_usuarios() if u.get("carga_entrevistas") and u["activo"]}


def entrevistas_pendientes(usuario_id, hoy=None):
    """Viajes ya realizados en los que la persona viajó, debe informar entrevistas y todavía no lo hizo."""
    hoy = hoy or date.today()
    if not debe_cargar_entrevistas(usuario_id):
        return []
    out = []
    with db._conexion() as con:
        filas = con.execute("""SELECT p.id AS participante_id, v.id AS viaje_id FROM viaje_participantes p JOIN viajes v ON v.id = p.viaje_id
                               WHERE p.usuario_id = ? AND p.entrevistas IS NULL AND v.estado = 'CONFIRMADO' AND v.fecha < ?
                               ORDER BY v.fecha DESC LIMIT 60""", (usuario_id, hoy.isoformat())).fetchall()
    for f in filas:
        v = tr.obtener_viaje(f["viaje_id"])
        if v:
            out.append(dict(v, participante_id=f["participante_id"]))
    return out


def entrevistas_informadas(usuario_id, dias=120, hoy=None):
    """Viajes realizados de los últimos `dias` en los que la persona ya informó sus audiencias: para corregirlas."""
    hoy = hoy or date.today()
    desde = (hoy - timedelta(days=dias)).isoformat()
    out = []
    with db._conexion() as con:
        filas = con.execute("""SELECT p.id AS participante_id, v.id AS viaje_id FROM viaje_participantes p JOIN viajes v ON v.id = p.viaje_id
                               WHERE p.usuario_id = ? AND p.entrevistas IS NOT NULL AND v.estado = 'CONFIRMADO' AND v.fecha < ? AND v.fecha >= ?
                               ORDER BY v.fecha DESC LIMIT 120""", (usuario_id, hoy.isoformat(), desde)).fetchall()
    for f in filas:
        v = tr.obtener_viaje(f["viaje_id"])
        if v:
            p = next((x for x in v["participantes"] if x["id"] == f["participante_id"]), {})
            out.append(dict(v, participante_id=f["participante_id"], mis_entrevistas=p.get("entrevistas"),
                            mi_ingreso=p.get("ingreso_pabellones"), informado_por=p.get("entrevistas_por"), informado_en=p.get("entrevistas_en")))
    return out


def guardar_entrevistas(viaje_id, valores, usuario, pabellones=None):
    """valores = {participante_id: cantidad}; pabellones = {participante_id: True/False} (ingresó a pabellones o sectores).
    Cada persona carga lo suyo; la coordinación y la Dirección, las de todos. Se puede corregir después:
    el historial del viaje guarda el valor anterior y el nuevo."""
    pabellones = pabellones or {}
    v = tr.obtener_viaje(viaje_id)
    if not v:
        raise tr.ErrorTraslado("El viaje no existe.")
    if v["estado_efectivo"] != "REALIZADO":
        raise tr.ErrorTraslado("Las audiencias se cargan cuando el viaje ya se realizó.")
    puede_todo = tr.puede_coordinar(usuario)
    por_id = {p["id"]: p for p in v["participantes"]}
    propios = {p["id"] for p in v["participantes"] if p.get("usuario_id") == usuario["id"]}
    guardados, cambios = 0, []
    with db._conexion() as con:
        for pid in sorted(set(valores) | set(pabellones)):
            if pid not in por_id:
                continue
            if not puede_todo and pid not in propios:
                raise tr.ErrorTraslado("Solo podés cargar tus propias audiencias.")
            p = por_id[pid]
            txt = str(valores.get(pid) if valores.get(pid) is not None else "").strip()
            cant = p.get("entrevistas")
            if txt != "":
                if not txt.isdigit() or int(txt) > 500:
                    raise tr.ErrorTraslado("La cantidad de audiencias tiene que ser un número entre 0 y 500.")
                cant = int(txt)
            ingreso = p.get("ingreso_pabellones")
            if pid in pabellones:
                ingreso = 1 if pabellones[pid] else 0
            if cant is None:
                continue  # sin cantidad no se guarda nada (el ingreso a pabellones va junto con las audiencias)
            if (cant, ingreso) == (p.get("entrevistas"), p.get("ingreso_pabellones")):
                continue
            con.execute("""UPDATE viaje_participantes SET entrevistas = ?, ingreso_pabellones = ?, entrevistas_por = ?, entrevistas_en = ?
                           WHERE id = ?""", (cant, ingreso, usuario["nombre"], db._ahora(), pid))
            guardados += 1
            if p.get("entrevistas") is not None:  # corrección: queda el antes y el después
                antes = f"{p['entrevistas']} audiencia(s){' · ingresó a pabellones' if p.get('ingreso_pabellones') else ''}"
                despues = f"{cant} audiencia(s){' · ingresó a pabellones' if ingreso else ''}"
                cambios.append((p["nombre"], antes, despues))
        for nombre, antes, despues in cambios:
            tr.auditar("viaje", viaje_id, f"Audiencias corregidas ({nombre})", antes, despues, con=con)
        nuevos = guardados - len(cambios)
        if nuevos:
            tr.auditar("viaje", viaje_id, "Audiencias cargadas", None, f"{nuevos} persona(s)", con=con)
        if guardados:  # propuesta 73: quien viajó (o la coordinación) deja constancia de que el viaje se hizo
            from dgpdh.seguridad import auditoria as au
            from dgpdh.servicios import viajes_campo
            viajes_campo.marcar_constancia(con, au.Actor.desde(usuario), viaje_id, "VIAJERO" if propios else "COORDINACION")
    return guardados


# ---------------------------------------------------------------- metas por equipo
def metas(anio):
    """{equipo_id: {1: minimo, 2: …, 'anual': suma}}. Propuesta 82: es la meta de visitas de Metas (una sola fuente)."""
    from dgpdh.servicios import metas_visitas
    with db._conexion() as con:
        return metas_visitas.metas(con, anio)


def repartir(anual):
    """Mínimo anual → cuatro trimestres lo más parejos posible (el resto, en los primeros)."""
    from dgpdh.servicios import metas_visitas
    return metas_visitas.repartir(anual)


def guardar_metas(anio, filas, usuario):
    """filas = {equipo_id: [t1, t2, t3, t4]} (enteros ≥ 0). Propuesta 82: se guarda como la meta de visitas de Metas."""
    import auth
    from dgpdh.seguridad import auditoria as au
    from dgpdh.servicios import metas_visitas
    with db._conexion() as con:
        return metas_visitas.fijar(con, au.Actor.desde(usuario), auth.es_admin(usuario), anio, filas)


def diferencias_metas(anio=None):
    """Propuesta 82: equipos cuyas dos metas viejas no coincidían (para que la Dirección elija)."""
    from dgpdh.servicios import metas_visitas
    with db._conexion() as con:
        return metas_visitas.diferencias(con, anio)


def avance_metas(anio, hoy=None, viajes_anio=None):
    """Por equipo y trimestre: mínimo, realizados y estado (cumplida | en camino | atrasada | sin meta)."""
    hoy = hoy or date.today()
    equipos = {e["id"]: e["nombre"] for e in db.listar_equipos()}
    m = metas(anio)
    if viajes_anio is None:
        hechos = [v for v in tr.listar_viajes(desde=f"{anio}-01-01", hasta=f"{anio}-12-31", estado="CONFIRMADO", limite=100000)
                  if v["fecha"] < hoy.isoformat()]
    else:
        hechos = viajes_anio
    vu = tr.viajes_a_unidad(hechos)
    por = Counter()
    for fecha, _, _, eq in vu:
        por[(eq.lower(), trimestre_de(date.fromisoformat(fecha)))] += 1
    filas = []
    for eid, nombre in sorted(equipos.items(), key=lambda x: x[1].lower()):
        meta = m.get(eid, {})
        tris = []
        for t in (1, 2, 3, 4):
            ini, fin = limites_trimestre(anio, t)
            minimo, hecho = meta.get(t, 0), por[(nombre.lower(), t)]
            if hoy < ini:
                transcurrido = 0.0
            elif hoy > fin:
                transcurrido = 1.0
            else:
                transcurrido = ((hoy - ini).days + 1) / ((fin - ini).days + 1)
            esperado = math.floor(minimo * transcurrido)
            if not minimo:
                estado = "sin_meta"
            elif hecho >= minimo:
                estado = "cumplida"
            elif hoy > fin:
                estado = "no_cumplida"
            elif hoy < ini:
                estado = "futuro"
            elif hecho >= esperado:
                estado = "en_camino"
            else:
                estado = "atrasada"
            tris.append({"t": t, "minimo": minimo, "hecho": hecho, "esperado": esperado, "estado": estado,
                         "pct": min(100, round(100 * hecho / minimo)) if minimo else None})
        anual = sum(x["minimo"] for x in tris)
        filas.append({"equipo_id": eid, "equipo": nombre, "anual": anual, "hecho_anual": sum(x["hecho"] for x in tris), "trimestres": tris})
    return filas


def equipos_visibles(anio=None):
    """Áreas que se muestran en el Tablero de Dirección: las habilitadas para viajes (alguien activo del equipo ve Autos PPN)
    o las que tienen meta de viajes en el año. Las demás no se listan."""
    anio = anio or date.today().year
    con_meta = {eid for eid, d in metas(anio).items() if d.get("anual")}
    with db._conexion() as con:
        habilitados = {f[0] for f in con.execute(
            """SELECT DISTINCT equipo_id FROM usuarios WHERE equipo_id IS NOT NULL AND activo = 1
               AND (COALESCE(acceso_traslados, 1) = 1 OR COALESCE(coordina_traslados, 0) = 1 OR rol = 'CHOFER')""")}
    return [e for e in db.listar_equipos() if e["id"] in habilitados or e["id"] in con_meta]


# ---------------------------------------------------------------- tablero
def _region(unidad):
    if not unidad:
        return "Sin unidad del catálogo"
    return "AMBA" if (unidad.get("zona") or "").strip().upper() == "AMBA" else "Resto del país"


def _localidad(unidad):
    return (unidad.get("ciudad") or "").strip() or unidad["nombre"]


def tablero(desde, hasta, circuito=None, equipo=None, hoy=None):
    hoy = hoy or date.today()
    h = hoy.isoformat()
    todos = tr.listar_viajes(desde=desde, hasta=hasta, circuito=circuito or None, limite=100000)
    eq_l = (equipo or "").strip().lower()

    def personas(v):
        ps = v["participantes"]
        return [p for p in ps if (p.get("equipo_nombre") or "").strip().lower() == eq_l] if eq_l else ps

    if eq_l:
        todos = [v for v in todos if personas(v)]
    vivos = [v for v in todos if v["estado"] != "CANCELADO"]
    hechos = [v for v in vivos if v["estado"] == "CONFIRMADO" and v["fecha"] < h]
    programados_a_hoy = [v for v in vivos if v["fecha"] < h]
    cancelados = [v for v in todos if v["estado"] == "CANCELADO"]
    unidades = {u["id"]: u for u in db.listar_unidades(solo_activas=False)}
    activas = [u for u in unidades.values() if u.get("activa", 1)]
    vu = tr.viajes_a_unidad(hechos, personas)

    # 1) realizados vs. programados (hasta hoy)
    k_ejec = {"hechos": len(hechos), "programados": len(programados_a_hoy),
              "pct": round(100 * len(hechos) / len(programados_a_hoy)) if programados_a_hoy else None,
              "cancelados": len(cancelados), "futuros": sum(1 for v in vivos if v["fecha"] >= h)}
    # 2) cobertura territorial: unidades visitadas sobre las del catálogo, por región
    visitadas = {v["unidad_id"] for v in hechos if v.get("unidad_id")}
    cobertura = {}
    for region in ("AMBA", "Resto del país"):
        total = [u for u in activas if _region(u) == region and (u.get("zona") or "") != "Delegaciones PPN"]
        vis = [u for u in total if u["id"] in visitadas]
        n_vu = sum(1 for k in vu if isinstance(k[1], int) and _region(unidades.get(k[1])) == region)
        cobertura[region] = {"visitadas": len(vis), "total": len(total), "viajes": n_vu,
                             "pct": round(100 * len(vis) / len(total)) if total else None}
    n_vu_total = len(vu) or 1
    for region in cobertura:
        cobertura[region]["pct_viajes"] = round(100 * cobertura[region]["viajes"] / n_vu_total)
    # 3) alertas activas
    alertas = []
    sin_chofer = [v for v in tr.listar_viajes(desde=h, estado="SOLICITADO", limite=100000) if v["modalidad"] == "CHOFER"]
    urgentes = [v for v in sin_chofer if v["fecha"] <= (hoy + timedelta(days=2)).isoformat()]
    if urgentes:
        alertas.append({"nivel": "alto", "texto": f"{len(urgentes)} pedido(s) de auto sin chofer para hoy y los próximos 2 días", "url": "/traslados/asignar"})
    try:
        for x in tr.vencimientos(15, hoy):
            alertas.append({"nivel": "alto" if x["vencido"] else "medio", "texto": f"{x['que']} de {x['quien']}: {'vencido' if x['vencido'] else 'vence'} el {x['fecha'][8:10]}/{x['fecha'][5:7]}", "url": "/traslados/recursos"})
    except Exception:
        pass
    try:
        import flota
        for vid, (n, graves, _en_rep) in flota.novedades_abiertas_por_vehiculo().items():
            if graves:
                alertas.append({"nivel": "alto", "texto": f"{graves} novedad(es) mecánica(s) grave(s) sin resolver", "url": f"/traslados/vehiculos/{vid}"})
    except Exception:
        pass
    try:
        rev = tr.revisiones_pendientes()
        if rev:
            alertas.append({"nivel": "medio", "texto": f"{len(rev)} viaje(s) prioritario(s) esperan la decisión de la Dirección", "url": "/traslados?ver=agenda"})
    except Exception:
        pass
    # 4) pendientes y unidades sin cobertura (habituales de algún equipo sin visita en 90 días)
    hace90 = (hoy - timedelta(days=90)).isoformat()
    vistas90 = {v["unidad_id"] for v in tr.listar_viajes(desde=hace90, hasta=h, estado="CONFIRMADO", limite=100000) if v.get("unidad_id")}
    habituales = set()
    with db._conexion() as con:
        try:
            habituales = {f["unidad_id"] for f in con.execute("SELECT DISTINCT unidad_id FROM equipo_unidades")}
        except Exception:
            habituales = set()
    sin_cobertura = sorted((unidades[u] for u in habituales - vistas90 if u in unidades and unidades[u].get("activa", 1)), key=lambda u: u["nombre"])
    k_pend = {"sin_chofer": len(sin_chofer), "urgentes": len(urgentes), "sin_cobertura": sin_cobertura}

    # por equipo
    carga_entrev = usuarios_que_cargan()
    eq = defaultdict(lambda: {"viajes": 0, "personas": 0, "institucional": 0, "propios": 0, "entrevistas": 0, "deben": 0,
                              "informadas": 0, "inmediatos": 0, "cancelados": 0, "total_viajes": 0, "unidades": set()})
    for k in vu:
        eq[k[3]]["viajes"] += 1
        eq[k[3]]["unidades"].add(k[1])
    for v in hechos:
        equipos_v = set()
        for p in personas(v):
            nombre = (p.get("equipo_nombre") or "").strip() or "(sin equipo)"
            e = eq[nombre]
            e["personas"] += 1
            e["propios" if p.get("auto_propio") else "institucional"] += 1
            if p.get("usuario_id") in carga_entrev or p.get("entrevistas") is not None:
                e["deben"] += 1
                if p.get("entrevistas") is not None:
                    e["informadas"] += 1
                    e["entrevistas"] += p["entrevistas"]
            equipos_v.add(nombre)
        for nombre in equipos_v:
            eq[nombre]["total_viajes"] += 1
            if v.get("inmediato"):
                eq[nombre]["inmediatos"] += 1
    for v in cancelados:
        for nombre in {(p.get("equipo_nombre") or "").strip() or "(sin equipo)" for p in personas(v)}:
            eq[nombre]["cancelados"] += 1
    anio = hoy.year
    avance = {f["equipo"].lower(): f for f in avance_metas(anio, hoy)}
    tri = trimestre_de(hoy)
    filas = []
    promedio = (sum(e["viajes"] for e in eq.values()) / len(eq)) if eq else 0
    for nombre, e in eq.items():
        meta = avance.get(nombre.lower())
        mt = meta["trimestres"][tri - 1] if meta else None
        pend_entrev = e["deben"] - e["informadas"]
        ratio = (e["viajes"] / promedio) if promedio else 0
        if pend_entrev >= 3 or (e["deben"] and e["informadas"] / e["deben"] < 0.5):
            carga, carga_txt = "alto", f"{pend_entrev} entrevista(s) sin informar"
        elif ratio >= 1.5:
            carga, carga_txt = "medio", f"{round(ratio, 1)}× el promedio de viajes"
        elif e["total_viajes"] and e["inmediatos"] / e["total_viajes"] >= 0.4:
            carga, carga_txt = "medio", "muchos viajes cargados fuera de plazo"
        else:
            carga, carga_txt = "ok", "normal"
        filas.append({
            "equipo": nombre, "viajes": e["viajes"], "unidades": len(e["unidades"]), "personas": e["personas"],
            "entrevistas": e["entrevistas"], "deben": e["deben"], "informadas": e["informadas"], "pendientes_entrevistas": pend_entrev,
            "pct_institucional": round(100 * e["institucional"] / e["personas"]) if e["personas"] else None,
            "institucional": e["institucional"], "propios": e["propios"],
            "pct_inmediatos": round(100 * e["inmediatos"] / e["total_viajes"]) if e["total_viajes"] else None,
            "cancelados": e["cancelados"], "carga": carga, "carga_txt": carga_txt, "ratio": round(ratio, 2),
            "meta_trimestre": mt,
        })
    # solo las áreas habilitadas para viajes o con meta (y quienes viajaron sin equipo)
    visibles = {e["nombre"].lower() for e in equipos_visibles(anio)}
    ocultas = [f for f in filas if f["equipo"].lower() not in visibles and f["equipo"] != "(sin equipo)"]
    filas = [f for f in filas if f not in ocultas]
    filas.sort(key=lambda f: (-f["viajes"], f["equipo"].lower()))

    # territorio
    amba, interior = defaultdict(lambda: {"viajes": 0, "unidades": Counter(), "ultima": ""}), defaultdict(lambda: {"viajes": 0, "unidades": Counter(), "ultima": ""})
    for fecha, uk, destino, _ in vu:
        u = unidades.get(uk) if isinstance(uk, int) else None
        if u and _region(u) == "AMBA":
            g = amba[_localidad(u)]
        else:
            g = interior[(u.get("provincia") or u.get("ciudad") or "Sin provincia cargada") if u else "Destinos fuera del catálogo"]
        g["viajes"] += 1
        g["unidades"][destino] += 1
        g["ultima"] = max(g["ultima"], fecha)

    def ordenar(d):
        return [{"nombre": k, "viajes": x["viajes"], "unidades": x["unidades"].most_common(), "ultima": x["ultima"]}
                for k, x in sorted(d.items(), key=lambda kv: (-kv[1]["viajes"], kv[0]))]

    # propuesta 73: confirmados ya pasados que nadie confirmó (ni quien viajó ni el chofer): se muestran aparte
    sin_constancia, dias_constancia = [], None
    try:
        from dgpdh.servicios import opciones_viajes, viajes_campo
        dias_constancia = opciones_viajes.leer().dias_constancia
        with db._conexion() as con:
            ids = set(viajes_campo.sin_constancia(con, hoy, dias_constancia, desde, hasta))
        sin_constancia = sorted((v for v in hechos if v["id"] in ids), key=lambda v: v["fecha"])
    except Exception:
        import logging
        logging.exception("No se pudieron calcular los viajes sin constancia")
    return {"desde": desde, "hasta": hasta, "ejecucion": k_ejec, "sin_constancia": sin_constancia, "dias_constancia": dias_constancia, "cobertura": cobertura, "alertas": alertas, "pendientes": k_pend,
            "equipos": filas, "equipos_ocultos": len(ocultas), "amba": ordenar(amba), "interior": ordenar(interior), "max_viajes_equipo": max([f["viajes"] for f in filas] or [1]),
            "viajes_unidad": len(vu), "trimestre": tri, "anio": anio, "hechos": hechos}


def detalle(hechos, equipo=None, localidad=None, unidad=None):
    """Drill-down: los viajes realizados de un equipo, de una localidad o de una unidad, con quiénes viajaron y sus entrevistas."""
    unidades = {u["id"]: u for u in db.listar_unidades(solo_activas=False)}
    out = []
    for v in hechos:
        u = unidades.get(v.get("unidad_id"))
        if unidad and v["destino"] != unidad:
            continue
        if localidad and not (u and (_localidad(u) == localidad or (u.get("provincia") or u.get("ciudad")) == localidad)):
            if not (localidad == "Destinos fuera del catálogo" and not u):
                continue
        ps = v["participantes"]
        if equipo:
            ps = [p for p in ps if ((p.get("equipo_nombre") or "").strip() or "(sin equipo)").lower() == equipo.lower()]
            if not ps:
                continue
        out.append(dict(v, personas_detalle=ps))
    return sorted(out, key=lambda v: (v["fecha"], v.get("hora_salida") or ""), reverse=True)
