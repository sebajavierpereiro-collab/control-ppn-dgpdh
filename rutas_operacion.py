"""Pantallas de la ola 4 (traslados: planificación y operación). Se registran desde main.py con `registrar(app, ctx)`.

Tablero del día, agenda por hora, vista mensual, planificador semanal, desplazamientos con hoja de ruta y cierre
(km y combustible), horario real, novedades, licencias de choferes, reservas de vehículos, lista de espera,
hora de regreso estimada y comparación con el Excel anterior. Los asistentes solo PROPONEN: decide la coordinación.
"""
import calendar
from datetime import date, datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import database as db
import operacion as op
import traslados as tr
from traslados import ErrorTraslado

HORA_INICIO, HORA_FIN = 6, 22  # rango visible de la agenda por hora
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")


def _mins(h):
    return op._mins(h)


def carriles(viajes):
    """Agenda por hora: ubica cada viaje con horario en un carril para que los que se pisan queden lado a lado."""
    con_hora, sin_hora = [], []
    for v in viajes:
        s = _mins(v.get("hora_salida"))
        if s is None:
            sin_hora.append(v)
            continue
        r = _mins(v.get("hora_regreso"))
        if r is None or r <= s:
            r = s + 60 * tr.VENTANA_POR_DEFECTO_HORAS
        con_hora.append(dict(v, _s=s, _r=r))
    con_hora.sort(key=lambda v: (v["_s"], v["_r"]))
    grupos, grupo, fin_grupo = [], [], -1
    for v in con_hora:  # grupos de viajes que se encadenan solapándose
        if grupo and v["_s"] >= fin_grupo:
            grupos.append(grupo); grupo = []
        grupo.append(v); fin_grupo = max(fin_grupo, v["_r"]) if grupo[:-1] else v["_r"]
    if grupo:
        grupos.append(grupo)
    salida = []
    base, alto = HORA_INICIO * 60, (HORA_FIN - HORA_INICIO) * 60
    for g in grupos:
        fines = []
        for v in g:
            k = next((i for i, f in enumerate(fines) if f <= v["_s"]), None)
            if k is None:
                fines.append(v["_r"]); k = len(fines) - 1
            else:
                fines[k] = v["_r"]
            v["_carril"] = k
        n = len(fines)
        for v in g:
            top = max(0, v["_s"] - base)
            bottom = min(alto, v["_r"] - base)
            v["css"] = {"top": round(100 * top / alto, 3), "alto": round(100 * max(bottom - top, 20) / alto, 3),
                        "izq": round(100 * v["_carril"] / n, 3), "ancho": round(100 / n, 3)}
            v["superpuesto"] = n > 1
            # choque real: el mismo chofer en dos vehículos a la vez (el mismo vehículo con varias paradas es una sola salida)
            v["choque"] = any(w is not v and w.get("chofer_id") and w["chofer_id"] == v.get("chofer_id") and w.get("vehiculo_id") != v.get("vehiculo_id")
                              and w["_s"] < v["_r"] and v["_s"] < w["_r"] for w in g)
            salida.append(v)
    return salida, sin_hora


def columnas_del_dia(viajes, ahora=None):
    """Tablero del día: sin chofer, asignados (por salir), en curso y realizados (más los de medios propios aparte)."""
    ahora = ahora or datetime.now()
    hoy, m = ahora.date().isoformat(), ahora.hour * 60 + ahora.minute
    col = {"sin_chofer": [], "asignados": [], "en_curso": [], "realizados": [], "propios": []}
    for v in viajes:
        if v["estado"] == "CANCELADO" or v["estado"] == "A_CONFIRMAR":
            continue
        if v["modalidad"] == "PROPIO":
            col["propios"].append(v); continue
        if v["estado"] == "SOLICITADO":
            col["sin_chofer"].append(v); continue
        s, r = _mins(v.get("hora_salida")), _mins(v.get("hora_regreso"))
        if v.get("hora_regreso_real") or v["fecha"] < hoy or (v["fecha"] == hoy and r is not None and m >= r and not v.get("hora_salida_real")):
            col["realizados"].append(v)
        elif v.get("hora_salida_real") or (v["fecha"] == hoy and s is not None and m >= s):
            col["en_curso"].append(v)
        else:
            col["asignados"].append(v)
    return col


def mes_grilla(anio, mes, viajes):
    """Semanas del mes (lunes a domingo) con la cantidad de viajes por día."""
    por = {}
    for v in viajes:
        if v["estado"] == "CANCELADO":
            continue
        d = por.setdefault(v["fecha"], {"total": 0, "sin_chofer": 0, "propios": 0, "a_confirmar": 0})
        d["total"] += 1
        if v["estado"] == "SOLICITADO":
            d["sin_chofer"] += 1
        if v["modalidad"] == "PROPIO":
            d["propios"] += 1
        if v["estado"] == "A_CONFIRMAR":
            d["a_confirmar"] += 1
    maximo = max([d["total"] for d in por.values()] or [0])
    semanas = []
    for sem in calendar.Calendar(0).monthdatescalendar(anio, mes):
        fila = []
        for d in sem:
            datos = por.get(d.isoformat(), {"total": 0, "sin_chofer": 0, "propios": 0, "a_confirmar": 0})
            nivel = 0 if not datos["total"] else 1 + min(3, int(3 * datos["total"] / maximo)) if maximo else 0
            fila.append(dict(datos, fecha=d.isoformat(), dia=d.day, del_mes=d.month == mes, finde=d.weekday() >= 5, nivel=nivel))
        semanas.append(fila)
    return {"semanas": semanas, "maximo": maximo, "total": sum(d["total"] for d in por.values()),
            "sin_chofer": sum(d["sin_chofer"] for d in por.values()), "dias_con_viajes": len(por)}


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]
    import iconos
    templates.env.globals.setdefault("ico", iconos.svg)
    tiene_notas = ctx["tiene_notas"]
    templates.env.globals.update(TIPOS_NOVEDAD=op.TIPOS_NOVEDAD, TIPOS_LICENCIA=op.TIPOS_LICENCIA)

    def operacion_de_viaje(viaje_id):
        v = tr.obtener_viaje(viaje_id)
        d = op.obtener_desplazamiento(v["desplazamiento_id"]) if v and v.get("desplazamiento_id") else None
        with db._conexion() as con:
            ch = con.execute("SELECT usuario_id FROM choferes WHERE id = ?", (v["chofer_id"],)).fetchone() if v and v.get("chofer_id") else None
        return {"novedades": op.novedades(viaje_id), "espera": op.espera_de(viaje_id),
                "desplazamiento": d, "paradas": len(d["paradas"]) if d else 0, "chofer_usuario_id": ch["usuario_id"] if ch else None}

    templates.env.globals["operacion_de_viaje"] = operacion_de_viaje
    templates.env.globals["fecha_hoy"] = lambda: date.today().isoformat()
    templates.env.globals["consumo_flota"] = op.consumo
    templates.env.globals["novedades_por_unidad"] = op.novedades_por_unidad

    def pagina(request, nombre, **ctxt):
        u = usuario_actual(request)
        base = {"request": request, "usuario": u, "coordina": tr.puede_coordinar(u), "tiene_notas": tiene_notas(u)}
        base.update(ctxt)
        return templates.TemplateResponse(nombre, base)

    def volver(request, destino, mensaje=None):
        if mensaje:
            flash(request, mensaje)
        return RedirectResponse(url=destino, status_code=303)

    def exigir_coord(u):
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")

    def chofer_de_usuario(u):
        with db._conexion() as con:
            f = con.execute("SELECT id FROM choferes WHERE usuario_id = ?", (u["id"],)).fetchone()
        return f["id"] if f else None

    def puede_operar_viaje(u, v):
        """Cargan horario real y novedades: la coordinación, el chofer del viaje y quienes viajan."""
        if tr.puede_coordinar(u) or any(p.get("usuario_id") == u["id"] for p in v["participantes"]):
            return True
        return bool(v.get("chofer_id")) and v["chofer_id"] == chofer_de_usuario(u)

    def puede_ver_desplazamiento(u, d):
        return tr.puede_coordinar(u) or (d.get("chofer_id") and d["chofer_id"] == chofer_de_usuario(u))

    def fecha_param(txt, defecto=None):
        try:
            return date.fromisoformat(txt) if txt else (defecto or date.today())
        except ValueError:
            return defecto or date.today()

    def circuito_de(u, pedido):
        visibles = tr.circuitos_visibles(u)
        if pedido == "TODOS" and len(visibles) > 1:
            return "TODOS", None
        c = pedido if pedido in visibles else visibles[0]
        return c, c

    # ------------------------------------------------------------ 44) tablero del día
    @app.get("/traslados/hoy", response_class=HTMLResponse)
    def tablero_dia(request: Request, fecha: str = "", circuito: str = ""):
        u = usuario_actual(request)
        mi_chofer = chofer_de_usuario(u) if u["rol"] == "CHOFER" else None
        if not tr.puede_coordinar(u) and not mi_chofer:
            raise HTTPException(status_code=403, detail="El tablero del día es de la coordinación de traslados y de los choferes.")
        f = fecha_param(fecha)
        circ, filtro = circuito_de(u, circuito or "AMBA")
        viajes = tr.listar_viajes(desde=f.isoformat(), hasta=f.isoformat(), circuito=filtro)
        if mi_chofer:
            viajes = [v for v in viajes if v.get("chofer_id") == mi_chofer]
        col = columnas_del_dia(viajes)
        desps = [op.obtener_desplazamiento(i) for i in op.desplazamientos_del_dia(f.isoformat())]
        desps = [d for d in desps if d and d["paradas"] and (not mi_chofer or d["chofer_id"] == mi_chofer)]
        anom = [] if mi_chofer else [a for a in op.anomalias(f.isoformat(), f.isoformat())]
        novedades = [n for v in viajes for n in op.novedades(v["id"])]
        return pagina(request, "traslados_hoy.html", f=f.isoformat(), col=col, desps=desps, anomalias=anom, novedades=novedades,
                      anterior=(f - timedelta(days=1)).isoformat(), siguiente=(f + timedelta(days=1)).isoformat(),
                      es_hoy=f == date.today(), dia=DIAS[f.weekday()], circuito=circ, circuitos=tr.circuitos_visibles(u),
                      ahora=datetime.now().strftime("%H:%M"), soy_chofer=bool(mi_chofer))

    # ------------------------------------------------------------ 41) agenda por hora
    @app.get("/traslados/horas", response_class=HTMLResponse)
    def agenda_horas(request: Request, lunes: str = "", circuito: str = "", vista: str = "semana", fecha: str = ""):
        u = usuario_actual(request)
        circ, filtro = circuito_de(u, circuito)
        base = tr.lunes_de(fecha_param(lunes))
        sem = tr.semana(base, 7, circuito=filtro)
        dias = []
        for i, (fi, vs) in enumerate(sem.items()):
            vivos = [v for v in vs if v["estado"] != "CANCELADO"]
            bloques, sin_hora = carriles(vivos)
            dias.append({"fecha": fi, "nombre": DIAS[i], "bloques": bloques, "sin_hora": sin_hora,
                         "superpuestos": sum(1 for b in bloques if b["superpuesto"]), "choques": sum(1 for b in bloques if b["choque"])})
        horas = list(range(HORA_INICIO, HORA_FIN + 1))
        return pagina(request, "traslados_horas.html", dias=dias, horas=horas, max_sinhora=max([len(d["sin_hora"]) for d in dias] + [1]), lunes=base.isoformat(),
                      anterior=(base - timedelta(days=7)).isoformat(), siguiente=(base + timedelta(days=7)).isoformat(),
                      hoy=date.today().isoformat(), circuito=circ, circuitos=tr.circuitos_visibles(u),
                      ahora_pct=round(100 * ((datetime.now().hour * 60 + datetime.now().minute) - HORA_INICIO * 60) / ((HORA_FIN - HORA_INICIO) * 60), 2))

    # ------------------------------------------------------------ 42) vista mensual
    @app.get("/traslados/mes", response_class=HTMLResponse)
    def vista_mes(request: Request, mes: str = "", circuito: str = ""):
        u = usuario_actual(request)
        circ, filtro = circuito_de(u, circuito)
        try:
            anio, m = (int(x) for x in mes.split("-")) if mes else (date.today().year, date.today().month)
            primero = date(anio, m, 1)
        except ValueError:
            primero = date.today().replace(day=1)
        ultimo = primero.replace(day=calendar.monthrange(primero.year, primero.month)[1])
        viajes = tr.listar_viajes(desde=primero.isoformat(), hasta=ultimo.isoformat(), circuito=filtro, limite=10000)
        g = mes_grilla(primero.year, primero.month, viajes)
        ant = (primero - timedelta(days=1)).replace(day=1)
        sig = ultimo + timedelta(days=1)
        return pagina(request, "traslados_mes.html", g=g, titulo=f"{MESES[primero.month - 1].capitalize()} {primero.year}",
                      anterior=ant.strftime("%Y-%m"), siguiente=sig.strftime("%Y-%m"), hoy=date.today().isoformat(),
                      circuito=circ, circuitos=tr.circuitos_visibles(u), mes=primero.strftime("%Y-%m"))

    # ------------------------------------------------------------ mesa de asignación (chofer y vehículo)
    @app.get("/traslados/asignar", response_class=HTMLResponse)
    def mesa_asignacion(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        mesa = op.mesa_asignacion()
        for d in mesa["dias"]:
            f = date.fromisoformat(d["fecha"])
            d["titulo"] = f"{DIAS[f.weekday()]} {f.strftime('%d/%m')}"
            d["es_hoy"] = f == date.today()
            d["manana"] = f == date.today() + timedelta(days=1)
        return pagina(request, "traslados_asignar.html", mesa=mesa, activa="asignar")

    @app.post("/traslados/asignar")
    async def mesa_asignacion_guardar(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        ids = [x for x in f.getlist("viaje_id") if str(x).isdigit()]
        fecha = str(f.get("fecha") or "")
        destino = "/traslados/asignar" + (f"#dia-{fecha}" if fecha else "")
        try:
            ch = int(f["chofer_id"]) if str(f.get("chofer_id") or "").isdigit() else None
            ve = int(f["vehiculo_id"]) if str(f.get("vehiculo_id") or "").isdigit() else None
            r = op.asignar_seleccion(ids, ch, ve, u, forzar=f.get("forzar") == "1")
        except ErrorTraslado as e:
            return volver(request, destino, "⚠ " + str(e))
        n = len(ids)
        msg = (f"Listo: {r['chofer']}" + (f" con el {r['vehiculo']}" if r["vehiculo"] else " (sin vehículo todavía)") +
               f" lleva {r['personas']} persona{'s' if r['personas'] != 1 else ''} ({n} pedido{'s' if n != 1 else ''}" +
               (f", {r['unificados']} unificado{'s' if r['unificados'] != 1 else ''} al mismo destino" if r["unificados"] else "") +
               (", en una sola salida" if len(r["viajes"]) > 1 else "") + ").")
        if r["choques"]:
            msg += " Se aceptó el aviso: queda en el historial."
        return volver(request, destino, msg)

    # ------------------------------------------------------------ 16/17/19) planificador semanal
    @app.get("/traslados/planificar", response_class=HTMLResponse)
    def planificador(request: Request, lunes: str = "", vista: str = ""):
        u = usuario_actual(request)
        exigir_coord(u)
        hoy = date.today()
        base = tr.lunes_de(fecha_param(lunes, hoy + timedelta(days=7 - hoy.weekday())))
        plan = op.planificar(base, "AMBA")
        try:  # propuesta 77: se guarda lo propuesto para compararlo al cerrar la semana
            from dgpdh.servicios import propuesto_asignado
            with db._conexion() as con:
                propuesto_asignado.guardar(con, base, plan["propuesta"])
        except Exception:
            import logging
            logging.exception("No se pudo guardar la propuesta del planificador")
        vista = "ubicacion" if vista != "propuesta" else "propuesta"  # por defecto: las solicitudes por lugar de destino
        ubicaciones = op.pedidos_por_ubicacion(base, "AMBA") if vista == "ubicacion" else []
        for g in ubicaciones:
            for v in g["viajes"]:
                v["dia"] = DIAS[date.fromisoformat(v["fecha"]).weekday()]
        for p in plan["propuesta"]:
            p["dia"] = DIAS[date.fromisoformat(p["fecha"]).weekday()]
        with db._conexion() as con:
            choferes = [dict(c) for c in con.execute("SELECT id, nombre FROM choferes WHERE estado = 'DISPONIBLE' ORDER BY nombre")]
            vehiculos = [dict(x) for x in con.execute("SELECT id, dominio, capacidad FROM vehiculos WHERE estado = 'DISPONIBLE' ORDER BY capacidad, dominio")]
        return pagina(request, "traslados_planificar.html", plan=plan, lunes=base.isoformat(), choferes=choferes, vehiculos=vehiculos,
                      vista=vista, ubicaciones=ubicaciones,
                      anterior=(base - timedelta(days=7)).isoformat(), siguiente=(base + timedelta(days=7)).isoformat(),
                      anomalias=op.anomalias(base.isoformat(), (base + timedelta(days=6)).isoformat()),
                      carga=op.carga_semanal(base.isoformat()), DIAS=DIAS)

    @app.post("/traslados/planificar/aplicar")
    async def planificador_aplicar(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        ids = [int(x) for x in f.getlist("viaje_id") if str(x).isdigit()]
        lunes = str(f.get("lunes") or "")
        try:
            ch = int(f.get("chofer_id") or 0)
            ve = int(f["vehiculo_id"]) if str(f.get("vehiculo_id") or "").isdigit() else None
            if not ids:
                raise ErrorTraslado("No hay viajes para asignar.")
            choques = op.aplicar_propuesta(ids, ch, ve, u)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/planificar?lunes={lunes}", str(e))
        n = len(ids)
        return volver(request, f"/traslados/planificar?lunes={lunes}",
                      f"Asignado{'s' if n > 1 else ''} {n} viaje{'s' if n > 1 else ''}" + (" en una sola salida." if n > 1 else ".") +
                      (" Se aceptaron solapamientos: quedan en el historial." if choques else ""))

    @app.post("/traslados/planificar/unificar")
    async def planificador_unificar(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        lunes = str(f.get("lunes") or "")
        destino = f"/traslados/planificar?lunes={lunes}&vista=ubicacion"
        try:
            ch = int(f["chofer_id"]) if str(f.get("chofer_id") or "").isdigit() else None
            ve = int(f["vehiculo_id"]) if str(f.get("vehiculo_id") or "").isdigit() else None
            r = op.unificar_viajes([x for x in f.getlist("viaje_id") if str(x).isdigit()], u, ch, ve)
        except ErrorTraslado as e:
            return volver(request, destino, str(e))
        if r["modo"] == "un_viaje":
            msg = f"Se unificaron {r['n']} pedidos al mismo destino en el viaje #{r['viaje_id']}; los demás quedan como «Unificado con el viaje #{r['viaje_id']}»."
        else:
            msg = f"Los {r['n']} pedidos quedan en una sola salida, con el mismo chofer y vehículo."
        return volver(request, destino, msg + (" Chofer asignado." if ch and r["modo"] == "un_viaje" else ""))

    # ------------------------------------------------------------ 20) hora de regreso estimada
    @app.get("/traslados/estimar-regreso")
    def estimar_regreso(request: Request, unidad_id: str = "", destino_otro: str = "", salida: str = ""):
        usuario_actual(request)
        try:
            e = op.estimar_regreso(int(unidad_id) if unidad_id.isdigit() else None, destino_otro or None, salida or "09:00")
        except ErrorTraslado:
            e = None
        return JSONResponse(e or {})

    # ------------------------------------------------------------ 68/50/70) desplazamientos, hoja de ruta y cierre
    def _desp(request, desp_id):
        u = usuario_actual(request)
        d = op.obtener_desplazamiento(desp_id)
        if not d:
            raise HTTPException(status_code=404, detail="La salida no existe.")
        if not puede_ver_desplazamiento(u, d):
            raise HTTPException(status_code=403, detail="Solo la coordinación y el chofer de la salida pueden verla.")
        return u, d

    @app.get("/traslados/desplazamiento/{desp_id:int}", response_class=HTMLResponse)
    def desplazamiento(request: Request, desp_id: int):
        u, d = _desp(request, desp_id)
        return pagina(request, "traslados_desplazamiento.html", d=d, historial=tr.historial("desplazamiento", desp_id))

    @app.get("/traslados/desplazamiento/{desp_id:int}/hoja", response_class=HTMLResponse)
    def hoja_de_ruta(request: Request, desp_id: int):
        u, d = _desp(request, desp_id)
        return pagina(request, "traslados_hoja_ruta.html", d=d, impreso=datetime.now().strftime("%d/%m/%Y %H:%M"),
                      dia=DIAS[date.fromisoformat(d["fecha"]).weekday()])

    @app.post("/traslados/desplazamiento/{desp_id:int}/cerrar")
    async def cerrar_desplazamiento(request: Request, desp_id: int):
        u, d = _desp(request, desp_id)
        f = await request.form()
        try:
            avisos = op.cerrar_desplazamiento(desp_id, dict(f), u["nombre"]) or []
        except ErrorTraslado as e:
            return volver(request, f"/traslados/desplazamiento/{desp_id}", str(e))
        return volver(request, f"/traslados/desplazamiento/{desp_id}", ("⚠ " if avisos else "") + "Salida cerrada: horario, km y combustible guardados."
                      + "".join(f" Revisá: {a}." for a in avisos))

    # ------------------------------------------------------------ 69/76) horario real y novedades del viaje
    def _viaje_operable(request, viaje_id):
        u = usuario_actual(request)
        v = tr.obtener_viaje(viaje_id)
        if not v:
            raise HTTPException(status_code=404, detail="El viaje no existe.")
        if not puede_operar_viaje(u, v):
            raise HTTPException(status_code=403, detail="Lo cargan la coordinación, el chofer o quienes viajan.")
        return u, v

    @app.post("/traslados/{viaje_id:int}/horario-real")
    async def horario_real(request: Request, viaje_id: int):
        u, v = _viaje_operable(request, viaje_id)
        f = await request.form()
        try:
            op.cargar_horas_reales(viaje_id, f.get("salida_real"), f.get("regreso_real"), u["nombre"])
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))
        return volver(request, f"/traslados/{viaje_id}", "Horario real guardado.")

    @app.post("/traslados/{viaje_id:int}/novedad")
    async def novedad(request: Request, viaje_id: int):
        u, v = _viaje_operable(request, viaje_id)
        f = await request.form()
        try:
            op.agregar_novedad(viaje_id, str(f.get("tipo") or ""), f.get("detalle"), f.get("minutos"), u["nombre"])
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}#novedades", str(e))
        return volver(request, f"/traslados/{viaje_id}#novedades", "Novedad registrada.")

    # ------------------------------------------------------------ 73) lista de espera
    @app.post("/traslados/{viaje_id:int}/espera")
    async def anotarse_espera(request: Request, viaje_id: int):
        u = usuario_actual(request)
        if u["rol"] == "CHOFER":
            raise HTTPException(status_code=403, detail="Los choferes no se anotan en viajes.")
        try:
            f = await request.form()
            op.anotar_en_espera(viaje_id, u, str(f.get("motivo") or "RUTINA"), f.get("motivo_detalle"))  # propuesta 75
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))
        return volver(request, f"/traslados/{viaje_id}", "Quedaste en la lista de espera: si se libera un lugar te llega un aviso.")

    @app.post("/traslados/{viaje_id:int}/espera/orden")
    async def ordenar_espera(request: Request, viaje_id: int):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        eid = str(f.get("espera_id") or "")
        cambio = eid.isdigit() and op.reordenar_espera(viaje_id, int(eid), str(f.get("hacia") or ""), u)
        return volver(request, f"/traslados/{viaje_id}#espera", "Orden de la lista de espera actualizado." if cambio else "El orden no cambió.")

    @app.post("/traslados/{viaje_id:int}/espera/salir")
    async def salir_espera(request: Request, viaje_id: int):
        u = usuario_actual(request)
        op.salir_de_espera(viaje_id, u["id"])
        return volver(request, f"/traslados/{viaje_id}", "Saliste de la lista de espera.")

    # ------------------------------------------------------------ 71) licencias de choferes
    @app.get("/traslados/licencias", response_class=HTMLResponse)
    def licencias(request: Request, desde: str = ""):
        u = usuario_actual(request)
        exigir_coord(u)
        d0 = tr.lunes_de(fecha_param(desde))
        cal = op.calendario_choferes(d0, 28)
        return pagina(request, "traslados_licencias.html", cal=cal, desde=d0.isoformat(),
                      anterior=(d0 - timedelta(days=28)).isoformat(), siguiente=(d0 + timedelta(days=28)).isoformat(),
                      lista=op.licencias(desde=date.today().isoformat()), choferes=tr.listar_choferes(False), hoy=date.today().isoformat(),
                      anuladas=op.licencias(desde=(date.today() - timedelta(days=90)).isoformat(), anuladas=True))

    @app.post("/traslados/licencias")
    async def licencia_nueva(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        try:
            cid = int(f.get("chofer_id") or 0)
            if not cid:
                raise ErrorTraslado("Elegí el chofer.")
            af = op.agregar_licencia(cid, f.get("desde"), f.get("hasta"), str(f.get("tipo") or ""), f.get("observacion"), u["nombre"])
        except ErrorTraslado as e:
            return volver(request, "/traslados/licencias", str(e))
        msg = "Licencia cargada."
        if af:
            msg += f" Atención: tiene {len(af)} viaje(s) asignado(s) en esas fechas (el primero, el {af[0]['fecha'][8:10]}/{af[0]['fecha'][5:7]}); hay que reasignarlos."
        return volver(request, "/traslados/licencias", msg)

    @app.post("/traslados/licencias/{lic_id:int}/borrar")
    async def licencia_borrar(request: Request, lic_id: int):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        try:
            op.anular_licencia(lic_id, u["nombre"], f.get("motivo"))  # propuesta 80: baja lógica, se puede recuperar
        except ErrorTraslado as e:
            return volver(request, "/traslados/licencias", str(e))
        return volver(request, "/traslados/licencias", "Licencia quitada (queda en «Licencias quitadas» y se puede recuperar).")

    @app.post("/traslados/licencias/{lic_id:int}/recuperar")
    async def licencia_recuperar(request: Request, lic_id: int):
        u = usuario_actual(request)
        exigir_coord(u)
        try:
            af = op.recuperar_licencia(lic_id, u["nombre"])
        except ErrorTraslado as e:
            return volver(request, "/traslados/licencias", str(e))
        msg = "Licencia recuperada."
        if af:
            msg += f" Atención: el chofer tiene {len(af)} viaje(s) asignado(s) en esas fechas; hay que reasignarlos."
        return volver(request, "/traslados/licencias", msg)

    # ------------------------------------------------------------ 72) reservas de vehículos
    @app.get("/traslados/reservas", response_class=HTMLResponse)
    def reservas(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        return pagina(request, "traslados_reservas.html", lista=op.reservas(desde=date.today().isoformat()),
                      vehiculos=[x for x in tr.listar_vehiculos() if x["estado"] != "FUERA_DE_CIRCULACION"],
                      choferes=tr.listar_choferes(False), hoy=date.today().isoformat())

    @app.post("/traslados/reservas")
    async def reserva_nueva(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        try:
            vid = int(f.get("vehiculo_id") or 0)
            if not vid:
                raise ErrorTraslado("Elegí el vehículo.")
            cid = int(f["chofer_id"]) if str(f.get("chofer_id") or "").isdigit() else None
            _, choque = op.agregar_reserva(vid, f.get("fecha"), f.get("desde_hora"), f.get("hasta_hora"), f.get("motivo"), f.get("solicitante"), u["nombre"], cid)
        except ErrorTraslado as e:
            return volver(request, "/traslados/reservas", str(e))
        msg = "Reserva cargada."
        if choque:
            msg += f" Atención: el vehículo ya está asignado al viaje #{choque[0]['id']} en ese horario."
        return volver(request, "/traslados/reservas", msg)

    @app.post("/traslados/reservas/{res_id:int}/cancelar")
    async def reserva_cancelar(request: Request, res_id: int):
        u = usuario_actual(request)
        exigir_coord(u)
        op.cancelar_reserva(res_id, u["nombre"])
        return volver(request, "/traslados/reservas", "Reserva cancelada.")

    # ------------------------------------------------------------ 79) comparación Excel anterior ↔ sistema
    @app.post("/traslados/importar/comparar", response_class=HTMLResponse)
    async def comparar_excel(request: Request):
        import tempfile
        from pathlib import Path
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        archivo = f.get("archivo")
        if not archivo or not getattr(archivo, "filename", "").lower().endswith(".xlsx"):
            return volver(request, "/traslados/importar", "Elegí el Excel de viajes (.xlsx).")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows: no falla si el archivo quedó tomado
            ruta = Path(tmp) / "viajes.xlsx"
            ruta.write_bytes(await archivo.read())
            try:
                cmp = op.comparar_excel(str(ruta))
            except Exception as e:
                return volver(request, "/traslados/importar", f"No pude leer el Excel: {e}")
        return pagina(request, "traslados_comparar.html", cmp=cmp, archivo=archivo.filename)
