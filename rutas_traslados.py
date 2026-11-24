"""Pantallas de Traslados y del catálogo de unidades.

Se registran desde main.py con `registrar(app, ctx)` (ctx trae templates,
usuario_actual y _flash) para no importar main desde acá.
"""
import csv
import io
import json
import logging
from datetime import date, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import auth
import avatares
import database as db
import flota
import frecuentes as fr
import presentismo
import tablero_direccion as td
import traslados as tr
from traslados import ErrorTraslado

COLUMNAS_UNIDADES = ("nombre", "tipo", "zona", "ciudad", "provincia", "fuerza", "correo", "direccion", "alias", "lat", "lng",
                     "telefono_guardia", "horarios_visita", "contactos")


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]
    import iconos
    templates.env.globals.setdefault("ico", iconos.svg)
    tiene_notas = ctx["tiene_notas"]
    templates.env.globals["ETIQ_CIRCUITO"] = tr.ETIQUETAS_CIRCUITO
    templates.env.globals["ETIQ_ACTIVIDAD"] = tr.ETIQUETAS_ACTIVIDAD
    templates.env.globals["ETIQ_MODALIDAD"] = tr.ETIQUETAS_MODALIDAD
    templates.env.globals.update(FRECUENCIAS=fr.FRECUENCIAS, DIAS_SEMANA=fr.DIAS, ETIQ_ESTADO=tr.ETIQUETAS_ESTADO_VIAJE,
                                 MOTIVOS_CANCELACION=tr.MOTIVOS_CANCELACION)
    templates.env.filters["tl_traslados"] = tr.evento_linea_tiempo
    templates.env.globals["pasajeros_por_equipo"] = tr.pasajeros_por_equipo
    templates.env.globals["ETIQ_ESTADO_CHOFER"] = tr.ETIQUETAS_ESTADO_CHOFER
    templates.env.globals["ETIQ_ESTADO_VEHICULO"] = tr.ETIQUETAS_ESTADO_VEHICULO
    templates.env.globals["documentos_de_viaje"] = db.documentos_de_viaje
    templates.env.globals["sugerir_usuarios"] = tr.sugerir_usuarios
    templates.env.globals["notas_esperando_entrega"] = db.notas_esperando_entrega

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

    def exigir_admin(u):
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Solo Administración (Dirección General) puede hacer esto.")

    def circuito_elegido(u, pedido):
        visibles = tr.circuitos_visibles(u)
        return pedido if pedido in visibles else visibles[0]

    def circuito_con_todos(u, pedido):
        """(valor para la pantalla, filtro para la base): «TODOS» solo para quien ve los dos circuitos."""
        if pedido == "TODOS" and len(tr.circuitos_visibles(u)) > 1:
            return "TODOS", None
        c = circuito_elegido(u, pedido)
        return c, c

    def exigir_no_chofer(u):
        if u["rol"] == "CHOFER":
            raise HTTPException(status_code=403, detail="Los choferes solo consultan el catálogo.")

    def iso(f):
        return f.isoformat()

    # ------------------------------------------------------------ tablero y agenda
    @app.get("/traslados", response_class=HTMLResponse)
    def agenda(request: Request, lunes: str = None, mis: int = 0, unidad_id: str = "", modalidad: str = "", estado: str = "", circuito: str = "", frecuentes: int = 0, ver: str = ""):
        u = usuario_actual(request)
        # Evaluación de interfaz (propuesta 23): quien solo carga viajes entra a «Mis viajes», no al tablero de coordinación.
        if (u["rol"] != "CHOFER" and not tr.puede_coordinar(u) and ver != "agenda" and not lunes and not mis and not unidad_id
                and not modalidad and not estado and not frecuentes and not circuito):
            circ = tr.circuitos_visibles(u)[0]
            res = tr.resumen(u, circ)
            mios = [v for v in tr.listar_viajes(desde=iso(date.today()), hasta=iso(date.today() + timedelta(days=30)), usuario_id=u["id"])
                    if v["estado"] != "CANCELADO"]
            return pagina(request, "traslados_inicio.html", res=res, mios=mios[:12], circuito=circ, circuitos=tr.circuitos_visibles(u),
                          limite=iso(tr.limite_planificacion(tr.lunes_de(date.today()) + timedelta(days=7))), hoy=iso(date.today()))
        # «Todos» (solo quien ve los dos circuitos): suma AMBA y Delegaciones
        todos = circuito == "TODOS" and len(tr.circuitos_visibles(u)) > 1
        circ = "TODOS" if todos else circuito_elegido(u, circuito)
        try:
            base = tr.lunes_de(lunes) if lunes else tr.lunes_de(date.today())
        except ErrorTraslado:
            base = tr.lunes_de(date.today())
        filtros = {"circuito": None if todos else circ}
        if mis: filtros["usuario_id"] = u["id"]
        if unidad_id.isdigit(): filtros["unidad_id"] = int(unidad_id)
        if modalidad in tr.MODALIDADES: filtros["modalidad"] = modalidad
        if estado in tr.ESTADOS_VIAJE: filtros["estado"] = estado
        res = tr.resumen(u, None if todos else circ)  # también precarga los viajes frecuentes de la semana próxima
        sem = tr.semana(base, 7, **filtros)
        if frecuentes:
            sem = {f: [v for v in vs if v.get("frecuente_id")] for f, vs in sem.items()}
        dias = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
        grilla = [{"fecha": f, "nombre": dias[i], "viajes": v} for i, (f, v) in enumerate(sem.items())]
        coord = tr.puede_coordinar(u)
        return pagina(request, "traslados.html", grilla=grilla, lunes=iso(base), anterior=iso(base - timedelta(days=7)),
                      siguiente=iso(base + timedelta(days=7)), hoy=iso(date.today()), mis=mis, unidad_id=unidad_id,
                      modalidad=modalidad, estado=estado, unidades=db.listar_unidades(), circuito=circ,
                      circuitos=tr.circuitos_visibles(u), res=res, frecuentes=frecuentes,
                      limite=iso(tr.limite_planificacion(base + timedelta(days=7))), vencen=[])

    # ------------------------------------------------------------ nuevo viaje
    def _persona(x):
        return {"id": x["id"], "nombre": x["nombre"], "equipo": x.get("equipo_nombre"), "correo": x.get("correo"),
                "avatar": x.get("avatar", 0), "delegacion": x.get("delegacion_nombre")}

    def _externos_del_form(form):
        """Personas sin usuario agregadas en el formulario (cada una viaja como JSON en un campo «externos»)."""
        out = []
        if form is None:
            return out
        for crudo in form.getlist("externos"):
            try:
                e = json.loads(crudo)
            except (TypeError, ValueError):
                continue
            if not isinstance(e, dict) or not str(e.get("nombre") or "").strip():
                continue
            k = str(e.get("k") or "")
            out.append({"k": k, "externo_id": int(e["id"]) if str(e.get("id") or "").isdigit() else None,
                        "nombre": str(e["nombre"]).strip()[:120], "area": str(e.get("area") or "").strip()[:120],
                        "observaciones": str(e.get("observaciones") or "").strip()[:300],
                        "auto_propio": bool(form.get(f"auto_ext_{k}")), "crudo": crudo})
        return out

    def _disponibilidad_segura(fecha, form=None, excluir=None):
        try:
            return tr.disponibilidad_vehiculos(fecha, (form.get("hora_salida") if form else None) or None,
                                               (form.get("hora_regreso") if form else None) or None, excluir)
        except Exception:
            return None

    @app.get("/traslados/disponibilidad")
    def disponibilidad(request: Request, fecha: str = "", hora_salida: str = "", hora_regreso: str = ""):
        usuario_actual(request)
        try:
            return JSONResponse(tr.disponibilidad_vehiculos(fecha, hora_salida or None, hora_regreso or None))
        except Exception:
            return JSONResponse({"completo": False, "texto": ""})

    def _form_nuevo(request, form, error=None, unidad_pre="", circuito_pre="", aviso=None):
        u = usuario_actual(request)
        circ = tr.circuito_de(u)
        if form and puede_circ(u, form.get("circuito")):
            circ = form.get("circuito")
        elif not form and puede_circ(u, circuito_pre):
            circ = circuito_pre
        fecha = (form.get("fecha") if form else None) or iso(date.today() + timedelta(days=1))
        recurrente = {"frecuencia": (form.get("frecuencia") if form else None) or "SEMANAL",
                      "dias": [int(x) for x in (form.getlist("dias") if form is not None else []) if str(x).isdigit()]}
        compat = []
        if form and form.get("fecha"):
            try:
                compat = tr.compatibles(form.get("fecha"), form.get("unidad_id") or None, form.get("destino_otro"), circuito=circ)
            except Exception:
                compat = []
        todos = [x for x in db.listar_usuarios() if x["activo"] and x["rol"] != "CHOFER" and x.get("acceso_traslados", 1)]
        deleg = db.obtener_unidad(u["delegacion_id"]) if u.get("delegacion_id") else None
        if circ == "DELEGACIONES":
            unidades = [deleg] if deleg else [x for x in db.listar_unidades() if (x.get("zona") or "") == "Delegaciones PPN"]
            equipo_def = []
        else:
            unidades = tr.unidades_para_equipo(u.get("equipo_id"), u.get("equipo_nombre"))
            equipo_def = [x for x in unidades if x["marca"] == "defecto"]
        elegida = (form.get("unidad_id") if form else None) or (unidad_pre if str(unidad_pre).isdigit() else None) or (str(equipo_def[0]["id"]) if equipo_def else (str(deleg["id"]) if deleg else ""))
        # integrantes del equipo precargados; el resto se agrega con el buscador
        if form is not None and form.getlist("agentes"):
            marcados = [int(x) for x in form.getlist("agentes") if str(x).isdigit()]
        elif u.get("equipo_id") and circ == "AMBA":
            marcados = [x["id"] for x in todos if x.get("equipo_id") == u["equipo_id"]] or [u["id"]]
        else:
            marcados = [u["id"]]
        if u["id"] not in marcados:
            marcados.insert(0, u["id"])
        mapa = {x["id"]: x for x in todos}
        elegidos = [dict(_persona(mapa[i]), auto=bool(form and form.get(f"auto_{i}"))) for i in marcados if i in mapa]
        zonas = {str(x["id"]): (x.get("zona") or "") for x in unidades}
        return pagina(request, "viaje_nuevo.html", form=form or {}, error=error, fecha=fecha, compat=compat,
                      unidades=unidades, elegida=elegida, elegidos=elegidos, externos=_externos_del_form(form), circ=circ, zonas=zonas, deleg=deleg,
                      puede_circuitos=tr.circuitos_visibles(u), tipos_actividad=tr.TIPOS_ACTIVIDAD,
                      limite=iso(tr.limite_planificacion(fecha)) if fecha else "", hoy=iso(date.today()),
                      fuera_de_plazo=bool(fecha) and circ == "AMBA" and tr.es_inmediato(fecha, date.today()),
                      mensaje_sin_chofer=tr.MENSAJE_SIN_CHOFER, aviso=aviso, recurrente=recurrente,
                      disponibilidad=_disponibilidad_segura(fecha, form) if circ == "AMBA" else None,
                      FRECUENCIAS=fr.FRECUENCIAS, DIAS_SEMANA=fr.DIAS)

    def puede_circ(u, c):
        return c in tr.circuitos_visibles(u)

    @app.get("/traslados/nuevo", response_class=HTMLResponse)
    def viaje_nuevo(request: Request, unidad: str = "", circuito: str = "", repetir: int = 0, fecha: str = ""):
        exigir_no_chofer(usuario_actual(request))
        if repetir:
            v = tr.obtener_viaje(repetir)
            if v:
                f = _form_desde_viaje(v)
                if fecha:
                    f["fecha"] = fecha
                return _form_nuevo(request, f, aviso=f"Copiado del viaje del {v['fecha'][8:10]}/{v['fecha'][5:7]} a {v['destino']}" +
                                   (": revisá y confirmá." if fecha else ": elegí la nueva fecha."))
        return _form_nuevo(request, None, unidad_pre=unidad, circuito_pre=circuito)

    @app.get("/traslados/mi-semana", response_class=HTMLResponse)
    def mi_semana(request: Request):
        u = usuario_actual(request)
        exigir_no_chofer(u)
        return pagina(request, "traslados_mi_semana.html", d=fr.mi_semana(u))

    class _FormPre(dict):
        """Formulario precargado (para «Repetir este viaje»)."""
        def getlist(self, k):
            v = self.get(k, [])
            return v if isinstance(v, list) else [v]

    def _form_desde_viaje(v):
        f = _FormPre(circuito=v.get("circuito") or "AMBA", unidad_id=str(v["unidad_id"] or ""), destino_otro=v["destino_otro"] or "",
                     hora_salida=v["hora_salida"] or "", hora_regreso=v["hora_regreso"] or "", motivo=v["motivo"] or "",
                     observaciones=v["observaciones"] or "", agentes=[], externos=[])
        for i, p in enumerate(v["participantes"]):
            if p.get("usuario_id"):
                f["agentes"].append(str(p["usuario_id"]))
                if p["auto_propio"]:
                    f[f"auto_{p['usuario_id']}"] = "1"
            else:
                k = f"r{i}"
                f["externos"].append(json.dumps({"k": k, "id": p.get("externo_id"), "nombre": p["nombre"], "area": p.get("externo_area") or ""}))
                if p["auto_propio"]:
                    f[f"auto_ext_{k}"] = "1"
        return f

    @app.get("/traslados/personas")
    def buscar_personas(request: Request, q: str = ""):
        """Buscador global de personas para sumar a un viaje: nombre, apellido, correo, equipo o delegación."""
        usuario_actual(request)
        t = " ".join(q.lower().split())
        if len(t) < 2:
            return JSONResponse([])
        unidades = {x["id"]: x["nombre"] for x in db.listar_unidades(solo_activas=False)}
        out = []
        for x in db.listar_usuarios():
            if not x["activo"] or x["rol"] == "CHOFER" or not x.get("acceso_traslados", 1):
                continue
            x = dict(x, delegacion_nombre=unidades.get(x.get("delegacion_id")))
            hay = " ".join(str(x.get(k) or "") for k in ("nombre", "usuario", "correo", "equipo_nombre", "delegacion_nombre")).lower()
            if all(p in hay for p in t.split()):
                out.append(_persona(x))
        out = out[:12]
        for e in tr.buscar_externos(q, limite=15 - len(out)):
            out.append({"id": f"e{e['id']}", "externo_id": e["id"], "externo": True, "nombre": e["nombre"],
                        "equipo": e.get("area"), "correo": None, "avatar": 0, "delegacion": None})
        return JSONResponse(out)

    @app.post("/traslados/nuevo")
    async def viaje_crear(request: Request):
        u = usuario_actual(request)
        exigir_no_chofer(u)
        form = await request.form()
        if form.get("accion") == "revisar":
            return _form_nuevo(request, form)
        mapa = {x["id"]: x for x in db.listar_usuarios()}
        parts = []
        for x in form.getlist("agentes"):
            if str(x).isdigit() and int(x) in mapa:
                m = mapa[int(x)]
                parts.append({"usuario_id": m["id"], "nombre": m["nombre"], "equipo_nombre": m.get("equipo_nombre"),
                              "auto_propio": bool(form.get(f"auto_{m['id']}"))})
        for e in _externos_del_form(form):
            parts.append({"nombre": e["nombre"], "equipo_nombre": None, "auto_propio": e["auto_propio"], "externo_id": e["externo_id"],
                          "area": e["area"], "observaciones": e["observaciones"]})
        for linea in (form.get("otros") or "").splitlines():  # formato anterior (una por línea)
            if linea.strip():
                parts.append({"nombre": linea.strip(), "equipo_nombre": None, "auto_propio": bool(form.get("auto_otros"))})
        try:
            vid = tr.crear_viaje(dict(form), parts, u)
        except ErrorTraslado as e:
            return _form_nuevo(request, form, error=str(e))
        aviso_auto = ""
        nuevo = tr.obtener_viaje(vid)
        if nuevo and nuevo.get("modalidad") == "CHOFER":
            disp = _disponibilidad_segura(nuevo["fecha"], form, excluir=vid)
            if disp and disp["completo"]:
                if form.get("prioritario"):
                    try:
                        tr.pedir_revision_direccion(vid, form.get("motivo_prioridad"), u)
                        aviso_auto = " Ese día no quedan vehículos libres: por ser prioritario, se pidió la revisión de la Dirección General."
                    except ErrorTraslado as e:
                        aviso_auto = f" Ese día no quedan vehículos libres y no se pudo pedir la revisión: {e} Podés pedirla desde el viaje."
                else:
                    aviso_auto = (" Atención: ese día ya están en uso todos los vehículos; la coordinación te avisa si se puede. "
                                  "Si es prioritario, pedí la revisión de la Dirección General desde el viaje.")
        try:  # propuesta 57: aviso (no bloquea) si el viaje cae fuera del horario de ingreso de la unidad
            from dgpdh.servicios import ventanas as _vent
            with db._conexion() as _con:
                _av = _vent.aviso_para_viaje(_con, nuevo.get("unidad_id"), nuevo["fecha"], nuevo.get("hora_salida")) if nuevo else None
            if _av:
                aviso_auto += " Atención: " + _av
        except Exception:
            logging.exception("No se pudo revisar el horario de ingreso")
        if form.get("frecuente"):
            try:
                dias = [int(x) for x in form.getlist("dias") if str(x).isdigit()]
                fr.crear_desde_viaje(vid, form.get("frecuencia"), dias, form.get("frec_hasta") or None, u)
                fr.generar(forzar=True)
                return volver(request, f"/traslados/{vid}", "Viaje registrado y guardado como frecuente: los próximos se precargan para confirmar cada semana." + aviso_auto)
            except ErrorTraslado as e:
                return volver(request, f"/traslados/{vid}", f"Viaje registrado, pero no se pudo guardar como frecuente: {e}")
        return volver(request, f"/traslados/{vid}", ("⚠ " if aviso_auto and "Atención" in aviso_auto else "") + "Viaje registrado." + aviso_auto)

    # ------------------------------------------------------------ viajes frecuentes
    @app.post("/traslados/{viaje_id:int}/confirmar")
    async def viaje_confirmar(request: Request, viaje_id: int):
        u = usuario_actual(request)
        try:
            estado = fr.confirmar(viaje_id, u)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))
        return volver(request, f"/traslados/{viaje_id}", "Confirmado: " + ("pasó a la coordinación para asignar chofer." if estado == "SOLICITADO" else "queda registrado."))

    @app.post("/traslados/confirmar")
    async def viajes_confirmar(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        ok, errores = 0, []
        for x in f.getlist("viaje_id"):
            if str(x).isdigit():
                try:
                    fr.confirmar(int(x), u); ok += 1
                except ErrorTraslado as e:
                    errores.append(str(e))
        return volver(request, "/traslados", f"{ok} viaje(s) confirmado(s)." + (f" {len(errores)} no se pudieron confirmar." if errores else ""))

    @app.post("/traslados/{viaje_id:int}/no-viaja")
    async def viaje_no_viaja(request: Request, viaje_id: int):
        u = usuario_actual(request)
        try:
            fr.no_viaja(viaje_id, u)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))
        return volver(request, "/traslados", "Listo: ese viaje no se realiza esta semana.")

    @app.post("/traslados/{viaje_id:int}/frecuente")
    async def viaje_a_frecuente(request: Request, viaje_id: int):
        u = usuario_actual(request)
        exigir_no_chofer(u)
        f = await request.form()
        v = tr.obtener_viaje(viaje_id)
        if not v or not (tr.puede_editar_viaje(u, v) or fr.puede_confirmar(u, v)):
            raise HTTPException(status_code=403, detail="No podés convertir ese viaje.")
        try:
            fr.crear_desde_viaje(viaje_id, f.get("frecuencia"), [int(x) for x in f.getlist("dias") if str(x).isdigit()], f.get("hasta") or None, u)
            fr.generar(forzar=True)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))
        return volver(request, "/traslados/frecuentes", "Guardado como viaje frecuente. Los próximos se precargan para confirmar cada semana.")

    @app.get("/traslados/frecuentes", response_class=HTMLResponse)
    def frecuentes_pagina(request: Request, ver: str = "activas"):
        u = usuario_actual(request)
        exigir_no_chofer(u)
        fr.generar()
        plantillas = fr.listar(u)
        desde, hasta = fr.ventana()
        proximos = [v for v in tr.listar_viajes(desde=iso(date.today()), hasta=iso(hasta + timedelta(days=21))) if v.get("frecuente_id")
                    and any(p["id"] == v["frecuente_id"] for p in plantillas)]
        return pagina(request, "traslados_frecuentes.html", ver=ver if ver in ("activas", "proximos", "sugerencias", "todas") else "activas",
                      plantillas=plantillas, proximos=proximos, sugerencias=fr.sugerencias(u), FRECUENCIAS=fr.FRECUENCIAS, DIAS_SEMANA=fr.DIAS,
                      ETIQ_ESTADO=tr.ETIQUETAS_ESTADO_VIAJE, puede_confirmar=lambda v: fr.puede_confirmar(u, v))

    @app.post("/traslados/frecuentes/{fid:int}/estado")
    async def frecuentes_estado(request: Request, fid: int):
        u = usuario_actual(request)
        f = await request.form()
        try:
            fr.cambiar_estado(fid, f.get("accion"), u)
            if f.get("accion") == "activar":
                fr.generar(forzar=True)
        except ErrorTraslado as e:
            return volver(request, "/traslados/frecuentes", str(e))
        return volver(request, "/traslados/frecuentes", {"pausar": "Pausado: no se precargan más viajes hasta reactivarlo.",
                                                         "activar": "Reactivado.", "eliminar": "Eliminado."}.get(f.get("accion"), "Listo."))

    @app.post("/traslados/frecuentes/{fid:int}/fin")
    async def frecuentes_fin(request: Request, fid: int):
        u = usuario_actual(request)
        f = await request.form()
        try:
            fr.fijar_fin(fid, f.get("hasta") or None, u)
        except ErrorTraslado as e:
            return volver(request, "/traslados/frecuentes", str(e))
        return volver(request, "/traslados/frecuentes", "Fecha de finalización guardada.")

    @app.post("/traslados/frecuentes/sugerencia")
    async def frecuentes_desde_sugerencia(request: Request):
        u = usuario_actual(request)
        exigir_no_chofer(u)
        f = await request.form()
        vid, dia = str(f.get("viaje_id") or ""), str(f.get("dia") or "")
        v = tr.obtener_viaje(int(vid)) if vid.isdigit() else None
        if not v or not (tr.puede_editar_viaje(u, v) or fr.puede_confirmar(u, v)):
            return volver(request, "/traslados/frecuentes?ver=sugerencias", "Ese viaje ya no está disponible.")
        try:
            fr.crear_desde_viaje(v["id"], "SEMANAL", [int(dia)] if dia.isdigit() else None, None, u)
            fr.generar(forzar=True)
        except ErrorTraslado as e:
            return volver(request, "/traslados/frecuentes?ver=sugerencias", str(e))
        return volver(request, "/traslados/frecuentes", "Registrado como viaje frecuente.")

    # ------------------------------------------------------------ detalle
    # ------------------------------------------------------------ tablero de Dirección y entrevistas
    def exigir_tablero(u):
        if not (u["rol"] in ("DIRECTOR", "SUBDIRECTOR") or tr.puede_coordinar(u)):
            raise HTTPException(status_code=403, detail="El tablero de Dirección es para la Dirección General y la coordinación de traslados.")

    def datos_tablero(u, periodo, desde, hasta, circuito, equipo):
        d, h, periodo = td.rango(periodo, desde, hasta)
        circ = circuito if circuito in ("AMBA", "DELEGACIONES") else None
        return td.tablero(d, h, circ, equipo or None), periodo, circ

    def _metas_visibles(anio):
        visibles = {e["id"] for e in td.equipos_visibles(anio)}
        return [f for f in td.avance_metas(anio) if f["equipo_id"] in visibles]

    @app.get("/traslados/direccion", response_class=HTMLResponse)
    def tablero_direccion(request: Request, periodo: str = "mes", desde: str = "", hasta: str = "", circuito: str = "", equipo: str = "",
                          ver_equipo: str = "", ver_lugar: str = "", ver_unidad: str = ""):
        u = usuario_actual(request)
        exigir_tablero(u)
        try:
            t, periodo, circ = datos_tablero(u, periodo, desde, hasta, circuito, equipo)
        except ErrorTraslado as e:
            return volver(request, "/traslados/direccion", "⚠ " + str(e))
        det = None
        if ver_equipo or ver_lugar or ver_unidad:
            det = {"titulo": ver_equipo or ver_lugar or ver_unidad, "viajes": td.detalle(t["hechos"], ver_equipo or None, ver_lugar or None, ver_unidad or None)}
        anio = date.today().year
        return pagina(request, "traslados_direccion.html", t=t, periodo=periodo, PERIODOS=td.PERIODOS, circuito=circ or "", equipo=equipo,
                      equipos=td.equipos_visibles(anio), det=det, metas=_metas_visibles(anio), anio=anio, activa="direccion",
                      puede_metas=u["rol"] in ("DIRECTOR", "SUBDIRECTOR"))

    @app.get("/traslados/direccion.xlsx")
    def tablero_direccion_excel(request: Request, periodo: str = "mes", desde: str = "", hasta: str = "", circuito: str = "", equipo: str = ""):
        u = usuario_actual(request)
        exigir_tablero(u)
        import openpyxl
        from openpyxl.styles import Font
        t, periodo, circ = datos_tablero(u, periodo, desde, hasta, circuito, equipo)
        libro = openpyxl.Workbook()
        h = libro.active; h.title = "Resumen"
        negrita = Font(bold=True)
        h.append(["Tablero de Dirección — Autos PPN"]); h["A1"].font = Font(bold=True, size=14)
        h.append([f"Período: {t['desde']} a {t['hasta']}", f"Circuito: {circ or 'todos'}", f"Equipo: {equipo or 'todos'}"])
        h.append([])
        e = t["ejecucion"]
        for fila in (["Viajes realizados", e["hechos"]], ["Viajes programados (hasta hoy)", e["programados"]], ["% realizados", e["pct"]],
                     ["Viajes a la unidad", t["viajes_unidad"]], ["Cancelados", e["cancelados"]],
                     ["Cobertura AMBA (unidades visitadas / total)", f"{t['cobertura']['AMBA']['visitadas']} / {t['cobertura']['AMBA']['total']}"],
                     ["Cobertura resto del país", f"{t['cobertura']['Resto del país']['visitadas']} / {t['cobertura']['Resto del país']['total']}"],
                     ["Pedidos sin chofer", t["pendientes"]["sin_chofer"]], ["Unidades habituales sin visita en 90 días", len(t["pendientes"]["sin_cobertura"])],
                     ["Alertas activas", len(t["alertas"])]):
            h.append(fila)
        h2 = libro.create_sheet("Equipos")
        h2.append(["Equipo", "Viajes a la unidad", "Unidades distintas", "Personas", "Entrevistas", "Entrevistas sin informar",
                   "% vehículo institucional", "% fuera de plazo", "Cancelados", f"Meta T{t['trimestre']}", f"Realizado T{t['trimestre']}", "Carga"])
        for f in t["equipos"]:
            mt = f["meta_trimestre"] or {}
            h2.append([f["equipo"], f["viajes"], f["unidades"], f["personas"], f["entrevistas"], f["pendientes_entrevistas"],
                       f["pct_institucional"], f["pct_inmediatos"], f["cancelados"], mt.get("minimo") or None, mt.get("hecho"), f["carga_txt"]])
        h3 = libro.create_sheet("Territorio")
        h3.append(["Región", "Localidad / provincia", "Viajes a la unidad", "Última visita", "Unidades"])
        for region, lista in (("AMBA", t["amba"]), ("Resto del país", t["interior"])):
            for g in lista:
                h3.append([region, g["nombre"], g["viajes"], g["ultima"], ", ".join(f"{n} ({c})" for n, c in g["unidades"])])
        h4 = libro.create_sheet("Metas")
        h4.append(["Equipo", "Mínimo anual", "Realizado en el año"] + [f"T{i} mínimo" for i in range(1, 5)] + [f"T{i} realizado" for i in range(1, 5)])
        for f in _metas_visibles(date.today().year):
            h4.append([f["equipo"], f["anual"], f["hecho_anual"]] + [x["minimo"] for x in f["trimestres"]] + [x["hecho"] for x in f["trimestres"]])
        h5 = libro.create_sheet("Detalle")
        h5.append(["Fecha", "Destino", "Equipo", "Persona", "Medio", "Audiencias", "Ingresó a pabellones"])
        for v in sorted(t["hechos"], key=lambda v: v["fecha"]):
            for p in v["participantes"]:
                if equipo and (p.get("equipo_nombre") or "").lower() != equipo.lower():
                    continue
                h5.append([v["fecha"], v["destino"], p.get("equipo_nombre") or "", p["nombre"], "Propios medios" if p.get("auto_propio") else "Vehículo PPN", p.get("entrevistas"),
                               {1: "Sí", 0: "No"}.get(p.get("ingreso_pabellones"), "")])
        for hoja in libro.worksheets:
            for c in hoja[1 if hoja.title != "Resumen" else 4]:
                c.font = negrita
            for col in hoja.columns:
                hoja.column_dimensions[col[0].column_letter].width = min(48, max(10, max(len(str(c.value or "")) for c in col) + 2))
        buf = io.BytesIO(); libro.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=tablero_direccion_{t['desde']}_{t['hasta']}.xlsx"})

    @app.post("/traslados/direccion/metas")
    async def tablero_metas(request: Request):
        u = usuario_actual(request)
        if u["rol"] not in ("DIRECTOR", "SUBDIRECTOR"):
            raise HTTPException(status_code=403, detail="Las metas las fija la Dirección General.")
        f = await request.form()
        anio = int(f.get("anio") or date.today().year)
        filas = {}
        try:
            for e in db.listar_equipos():
                tri = [str(f.get(f"t{i}_{e['id']}") or "").strip() for i in range(1, 5)]
                anual = str(f.get(f"anual_{e['id']}") or "").strip()
                if any(tri):
                    if not all(x.isdigit() or x == "" for x in tri):
                        raise ErrorTraslado(f"Las metas de «{e['nombre']}» tienen que ser números enteros.")
                    filas[e["id"]] = [int(x or 0) for x in tri]
                elif anual:
                    if not anual.isdigit():
                        raise ErrorTraslado(f"La meta anual de «{e['nombre']}» tiene que ser un número entero.")
                    filas[e["id"]] = td.repartir(int(anual))
        except ErrorTraslado as e:
            return volver(request, "/traslados/direccion#metas", "⚠ " + str(e))
        td.guardar_metas(anio, filas, u)
        return volver(request, "/traslados/direccion#metas", f"Metas {anio} guardadas para {len(filas)} equipo(s).")

    @app.get("/traslados/entrevistas", response_class=HTMLResponse)
    def entrevistas_pendientes(request: Request):
        u = usuario_actual(request)
        return pagina(request, "traslados_entrevistas.html", pendientes=td.entrevistas_pendientes(u["id"]),
                      informadas=td.entrevistas_informadas(u["id"]), debe=td.debe_cargar_entrevistas(u["id"]))

    @app.post("/traslados/{viaje_id:int}/entrevistas")
    async def cargar_entrevistas(request: Request, viaje_id: int):
        u = usuario_actual(request)
        f = await request.form()
        valores = {int(k[2:]): v for k, v in f.items() if k.startswith("p_") and k[2:].isdigit()}
        # «pabf_<id>» marca que la casilla de pabellones estaba en el formulario (una casilla sin tildar no se envía)
        pabellones = {int(k[5:]): bool(f.get(f"pab_{k[5:]}")) for k in f.keys() if k.startswith("pabf_") and k[5:].isdigit()}
        destino = f.get("volver") if str(f.get("volver") or "").startswith("/traslados") else f"/traslados/{viaje_id}"
        try:
            n = td.guardar_entrevistas(viaje_id, valores, u, pabellones=pabellones)
        except ErrorTraslado as e:
            return volver(request, destino, "⚠ " + str(e))
        return volver(request, destino, "Audiencias guardadas." if n else "No cambió nada.")

    @app.get("/traslados/{viaje_id:int}", response_class=HTMLResponse)
    def viaje_detalle(request: Request, viaje_id: int):
        u = usuario_actual(request)
        v = tr.obtener_viaje(viaje_id)
        if not v:
            raise HTTPException(status_code=404, detail="El viaje no existe.")
        coord = tr.puede_coordinar(u)
        sug = tr.sugerencias(viaje_id) if coord and v["modalidad"] == "CHOFER" and v["estado"] in ("SOLICITADO", "CONFIRMADO") else None
        usuarios = [x for x in db.listar_usuarios() if x["activo"] and x["rol"] != "CHOFER"]
        disp = None
        if v["modalidad"] == "CHOFER" and v["estado"] in ("SOLICITADO", "CONFIRMADO", "A_CONFIRMAR") and not v.get("vehiculo_id") and v["fecha"] >= iso(date.today()):
            disp = _disponibilidad_segura(v["fecha"], {"hora_salida": v.get("hora_salida"), "hora_regreso": v.get("hora_regreso")}, excluir=viaje_id)
        es_dir = u["rol"] in ("DIRECTOR", "SUBDIRECTOR")
        tarea = _tarea_principal(v, sug, es_dir, fr.puede_confirmar(u, v))
        puede_corregir = tr.puede_corregir_realizado(u, v)
        unidades_corr = db.listar_unidades() if puede_corregir else []
        return pagina(request, "viaje_detalle.html", v=v, tarea=tarea, cargan_entrevistas=td.usuarios_que_cargan(), puede_editar=tr.puede_editar_viaje(u, v) and v["estado"] != "CANCELADO",
                      puede_corregir=puede_corregir, unidades_corr=unidades_corr,
                      disponibilidad=disp, es_direccion=u["rol"] in ("DIRECTOR", "SUBDIRECTOR"),
                      sug=sug, historial=tr.historial("viaje", viaje_id), usuarios=usuarios,
                      externos=[e for e in tr.listar_externos() if not e["usuario_id"]],
                      yo_participo=any(p.get("usuario_id") == u["id"] for p in v["participantes"]),
                      puede_confirmar=fr.puede_confirmar(u, v), plantilla=fr.obtener(v["frecuente_id"]) if v.get("frecuente_id") else None,
                      ETIQ_ESTADO=tr.ETIQUETAS_ESTADO_VIAJE, FRECUENCIAS=fr.FRECUENCIAS, DIAS_SEMANA=fr.DIAS)

    def _tarea_principal(v, sug, es_dir, puede_conf):
        """La tarea que le toca a quien mira el viaje: la pantalla lo lleva ahí y la resalta."""
        if v["estado"] == "CANCELADO" or v.get("estado_efectivo") in ("REALIZADO", "NO_CONFIRMADO"):
            return None
        if es_dir and v.get("revision_direccion") == "PENDIENTE":
            return {"ancla": "revision", "texto": "decidir si el viaje tiene prioridad"}
        if v["estado"] == "A_CONFIRMAR" and puede_conf:
            return {"ancla": "confirmar", "texto": "confirmar si el viaje se hace"}
        if sug and not v.get("chofer_id"):
            return {"ancla": "asignar", "texto": "asignar chofer y vehículo"}
        if sug and not v.get("vehiculo_id"):
            return {"ancla": "asignar", "texto": "asignar el vehículo"}
        return None

    def _accion(request, viaje_id, fn, ok):
        u = usuario_actual(request)
        v = tr.obtener_viaje(viaje_id)
        if not v:
            raise HTTPException(status_code=404, detail="El viaje no existe.")
        if not (tr.puede_editar_viaje(u, v) or tr.puede_corregir_realizado(u, v)):
            raise HTTPException(status_code=403, detail="Solo quien lo cargó o la coordinación puede modificarlo.")
        try:
            fn(u, v)
            return volver(request, f"/traslados/{viaje_id}", ok)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))

    @app.post("/traslados/{viaje_id:int}/prioridad")
    async def viaje_pedir_prioridad(request: Request, viaje_id: int):
        """Sin vehículos libres ese día: quien cargó el viaje (o la coordinación) pide la revisión de la Dirección General."""
        f = await request.form()
        return _accion(request, viaje_id, lambda u, v: tr.pedir_revision_direccion(viaje_id, f.get("motivo_prioridad"), u),
                       "Se pidió la revisión de la Dirección General (Director y Subdirector/a). Te llega un aviso con la decisión.")

    @app.post("/traslados/{viaje_id:int}/revision-direccion")
    async def viaje_revision_direccion(request: Request, viaje_id: int):
        u = usuario_actual(request)
        if u["rol"] not in ("DIRECTOR", "SUBDIRECTOR"):
            raise HTTPException(status_code=403, detail="La revisión la hace la Dirección General.")
        f = await request.form()
        decision = f.get("decision")
        try:
            tr.resolver_revision_direccion(viaje_id, decision, f.get("comentario"), u)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))
        return volver(request, f"/traslados/{viaje_id}", "Prioridad otorgada: se avisó a la coordinación para liberar o reasignar un vehículo."
                      if decision == "PRIORIDAD" else "Revisión registrada: se avisó a quien pidió el viaje.")

    @app.post("/traslados/{viaje_id:int}/reprogramar")
    async def viaje_reprogramar(request: Request, viaje_id: int):
        f = await request.form()
        return _accion(request, viaje_id, lambda u, v: tr.reprogramar(viaje_id, f.get("fecha"), f.get("hora_salida"), f.get("hora_regreso"), u), "Viaje reprogramado.")

    @app.post("/traslados/{viaje_id:int}/corregir")
    async def viaje_corregir(request: Request, viaje_id: int):
        """Corrección de un viaje ya realizado (fecha, horario o destino mal cargados)."""
        f = await request.form()
        datos = {k: f.get(k) for k in ("fecha", "hora_salida", "hora_regreso", "unidad_id", "destino_otro")}
        hechos = {}
        def corregir(u, v):
            hechos["campos"] = tr.corregir_realizado(viaje_id, datos, f.get("motivo"), u)
        r = _accion(request, viaje_id, corregir, "")
        if hechos.get("campos"):
            return volver(request, f"/traslados/{viaje_id}", "Viaje corregido: " + ", ".join(hechos["campos"]).lower() + ". Quedó en el historial.")
        return r

    @app.post("/traslados/{viaje_id:int}/cancelar")
    async def viaje_cancelar(request: Request, viaje_id: int):
        f = await request.form()
        tipo, detalle = str(f.get("motivo_tipo") or "").strip(), str(f.get("motivo") or "").strip()
        if tipo and tipo not in tr.MOTIVOS_CANCELACION:
            tipo = "Otro"
        if tipo == "Otro" and not detalle:
            return volver(request, f"/traslados/{viaje_id}", "Contá el motivo de la cancelación.")
        motivo = (f"{tipo}: {detalle}" if detalle else tipo) if tipo else detalle
        return _accion(request, viaje_id, lambda u, v: tr.cancelar(viaje_id, motivo, u), "Viaje cancelado.")

    @app.post("/traslados/{viaje_id:int}/participantes")
    async def viaje_sumar(request: Request, viaje_id: int):
        f = await request.form()
        u = usuario_actual(request)
        uid = str(f.get("usuario_id") or "")
        nombre = str(f.get("nombre") or "").strip()
        if f.get("yo"):
            uid, nombre = str(u["id"]), ""
        if uid.isdigit() and nombre:
            return volver(request, f"/traslados/{viaje_id}", "Elegí una persona de la lista o escribí el nombre de alguien sin usuario, no las dos cosas.")
        if uid.isdigit():
            m = next((x for x in db.listar_usuarios() if x["id"] == int(uid)), None)
            p = {"usuario_id": m["id"], "nombre": m["nombre"], "equipo_nombre": m.get("equipo_nombre")} if m else {"nombre": ""}
        else:
            p = {"nombre": nombre, "area": f.get("area"), "observaciones": f.get("observaciones")}
        p["auto_propio"] = bool(f.get("auto_propio"))
        # cualquier asesor puede sumarse (o sumar) a un viaje con chofer que tenga plazas
        v = tr.obtener_viaje(viaje_id)
        if not v:
            raise HTTPException(status_code=404, detail="El viaje no existe.")
        if v["estado_efectivo"] == "REALIZADO" and not tr.puede_corregir_realizado(u, v):
            raise HTTPException(status_code=403, detail="Un viaje realizado lo corrige quien lo cargó, la coordinación o quien viajó.")
        try:
            tr.agregar_participante(viaje_id, p, correccion=v["estado_efectivo"] == "REALIZADO")
            return volver(request, f"/traslados/{viaje_id}", "Persona sumada.")
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))

    @app.post("/traslados/{viaje_id:int}/participantes/{pid:int}/quitar")
    async def viaje_quitar(request: Request, viaje_id: int, pid: int):
        return _accion(request, viaje_id, lambda u, v: tr.quitar_participante(viaje_id, pid, correccion=v["estado_efectivo"] == "REALIZADO"), "Persona quitada.")

    @app.post("/traslados/{viaje_id:int}/participantes/{pid:int}/vincular")
    async def viaje_vincular_pasajero(request: Request, viaje_id: int, pid: int):
        """Pasajero cargado sin usuario (o con el nombre escrito distinto): se vincula al usuario correcto."""
        exigir_coord(usuario_actual(request))
        f = await request.form()
        uid = str(f.get("usuario_id") or "")
        if not uid.isdigit():
            return volver(request, f"/traslados/{viaje_id}", "Elegí el usuario.")
        try:
            n = tr.vincular_participante(viaje_id, pid, int(uid))
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))
        return volver(request, f"/traslados/{viaje_id}", "Vinculado." + (f" {n} viaje(s) de esa persona pasaron al historial del usuario." if n > 1 else ""))

    @app.post("/traslados/{viaje_id:int}/participantes/{pid:int}/auto")
    async def viaje_auto(request: Request, viaje_id: int, pid: int):
        f = await request.form()
        return _accion(request, viaje_id, lambda u, v: tr.cambiar_auto_propio(viaje_id, pid, bool(f.get("auto_propio")), correccion=v["estado_efectivo"] == "REALIZADO"), "Actualizado.")

    @app.post("/traslados/{viaje_id:int}/asignar")
    async def viaje_asignar(request: Request, viaje_id: int):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        try:
            ch = int(f.get("chofer_id") or 0)
            ve = int(f["vehiculo_id"]) if str(f.get("vehiculo_id") or "").isdigit() else None
            choques = tr.asignar(viaje_id, ch, ve, u, forzar=bool(f.get("forzar")))
            v = tr.obtener_viaje(viaje_id)
            salida = ""
            if v and v.get("misma_salida"):
                salida = (" Va en la misma salida que " + ", ".join(f"#{o['id']} {o['destino']}" for o in v["misma_salida"])
                          + (f": {v['con_transporte_salida']} de {v['vehiculo_capacidad']} plazas ocupadas." if v.get("vehiculo_capacidad") else "."))
            return volver(request, f"/traslados/{viaje_id}", "Asignado." + salida
                          + (" Se aceptó un aviso (" + "; ".join(choques) + "): queda en el historial." if choques else ""))
        except ErrorTraslado as e:
            return volver(request, f"/traslados/{viaje_id}", str(e))

    # ------------------------------------------------------------ flota y fichas
    def puede_ver_flota(u):
        return tr.puede_coordinar(u) or u["rol"] == "CHOFER"

    @app.get("/traslados/flota", response_class=HTMLResponse)
    def flota_panel(request: Request):
        u = usuario_actual(request)
        if not puede_ver_flota(u):
            raise HTTPException(status_code=403, detail="El panel de flota es para la coordinación de traslados y los choferes.")
        return pagina(request, "traslados_flota.html", vehiculos=flota.panel(), TIPOS=flota.TIPOS, ETIQ_ESTADO_VEHICULO=tr.ETIQUETAS_ESTADO_VEHICULO)

    @app.get("/traslados/vehiculos/{vid:int}", response_class=HTMLResponse)
    def vehiculo_ficha(request: Request, vid: int, editar: str = "", cargar: str = ""):
        u = usuario_actual(request)
        if not puede_ver_flota(u):
            raise HTTPException(status_code=403, detail="La ficha del vehículo es para la coordinación de traslados y los choferes.")
        v = next((x for x in tr.listar_vehiculos(incluir_baja=True) if x["id"] == vid), None)
        if not v:
            raise HTTPException(status_code=404, detail="El vehículo no existe.")
        hoy = date.today()
        proximos = [x for x in tr.listar_viajes(desde=iso(hoy), hasta=iso(hoy + timedelta(days=14))) if x.get("vehiculo_id") == vid and x["estado"] != "CANCELADO"]
        return pagina(request, "traslados_vehiculo.html", v=v, estado=flota.estado_vehiculo(v), controles=flota.controles(vid),
                      uso=flota.ocupacion(vid), proximos=proximos, TIPOS=flota.TIPOS, hoy=iso(hoy),
                      historial=tr.historial("vehiculo", vid), ETIQ_ESTADO_VEHICULO=tr.ETIQUETAS_ESTADO_VEHICULO,
                      abrir_edicion=bool(editar) and tr.puede_coordinar(u),
                      registros=flota.registros(vid), resumen_reg=flota.resumen_registros(vid), GRAVEDADES=flota.GRAVEDADES, ESTADOS_NOVEDAD=flota.ESTADOS_NOVEDAD, CLASE_NOVEDAD=flota.CLASE_ESTADO_NOVEDAD, hist_nov=flota.historial_novedades(vid),
                      choferes_vales=[c for c in tr.listar_choferes() if c.get("estado") != "BAJA"] if tr.puede_coordinar(u) else [],
                      abrir_carga=cargar)

    @app.post("/traslados/vehiculos/{vid:int}/control")
    async def vehiculo_control(request: Request, vid: int):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        try:
            flota.registrar_control(vid, dict(f), u)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/vehiculos/{vid}", str(e))
        return volver(request, f"/traslados/vehiculos/{vid}", "Control registrado.")

    @app.post("/traslados/vehiculos/{vid:int}/registro")
    async def vehiculo_registro(request: Request, vid: int):
        """Carga de combustible, vales entregados o novedad mecánica. Los vales los registra la coordinación."""
        u = usuario_actual(request)
        if not puede_ver_flota(u):
            raise HTTPException(status_code=403, detail="No podés cargar datos de este vehículo.")
        f = await request.form()
        tipo = str(f.get("tipo") or "")
        if tipo == "VALES" and not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Los vales entregados los registra la coordinación de traslados.")
        destino = f"/traslados/vehiculos/{vid}"
        try:
            flota.registrar(vid, tipo, dict(f), u)
        except ErrorTraslado as e:
            return volver(request, destino + f"?cargar={tipo.lower()}#cargar", "⚠ " + str(e))
        mensajes = {"COMBUSTIBLE": "Carga de combustible registrada.", "VALES": "Vales entregados registrados.",
                    "NOVEDAD": "Novedad mecánica registrada" + (": se avisó a la coordinación." if f.get("gravedad") in ("MODERADA", "GRAVE") else ".")}
        return volver(request, destino + "#registros", mensajes.get(tipo, "Registrado."))

    @app.post("/traslados/vehiculos/registro/{rid:int}/resuelta")
    async def vehiculo_novedad_resuelta(request: Request, rid: int):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        try:
            vid = flota.resolver_novedad(rid, f.get("resolucion"), u)
        except ErrorTraslado as e:
            return volver(request, "/traslados/flota", "⚠ " + str(e))
        return volver(request, f"/traslados/vehiculos/{vid}#registros", "Novedad marcada como resuelta.")

    @app.post("/traslados/vehiculos/registro/{rid:int}/estado")
    async def vehiculo_novedad_estado(request: Request, rid: int):
        """Estado de una novedad mecánica: lo lleva la coordinación de traslados."""
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        try:
            vid = flota.cambiar_estado_novedad(rid, str(f.get("estado") or ""), f.get("nota"), u,
                                               estado_vehiculo_nuevo=f.get("vehiculo_estado") or None)
        except ErrorTraslado as e:
            with db._conexion() as con:
                r = con.execute("SELECT vehiculo_id FROM vehiculo_registros WHERE id = ?", (rid,)).fetchone()
            return volver(request, f"/traslados/vehiculos/{r['vehiculo_id']}#novedades" if r else "/traslados/flota", "⚠ " + str(e))
        return volver(request, f"/traslados/vehiculos/{vid}#novedades",
                      f"Novedad: {flota.ESTADOS_NOVEDAD[f.get('estado')].lower()}."
                      + {"TALLER": " El vehículo quedó «En taller».", "DISPONIBLE": " El vehículo quedó disponible."}.get(f.get("vehiculo_estado") or "", ""))

    @app.post("/traslados/vehiculos/{vid:int}/km")
    async def vehiculo_km(request: Request, vid: int):
        u = usuario_actual(request)
        if not puede_ver_flota(u):
            raise HTTPException(status_code=403, detail="No podés cambiar el kilometraje.")
        f = await request.form()
        try:
            flota.actualizar_km(vid, f.get("km"), u)
        except ErrorTraslado as e:
            return volver(request, f"/traslados/vehiculos/{vid}?cargar=km#cargar", "⚠ " + str(e))
        return volver(request, f"/traslados/vehiculos/{vid}", "Kilometraje actualizado.")

    @app.get("/traslados/choferes/{cid:int}", response_class=HTMLResponse)
    def chofer_ficha(request: Request, cid: int):
        u = usuario_actual(request)
        datos = flota.ficha_chofer(cid)
        if not datos:
            raise HTTPException(status_code=404, detail="El chofer no existe.")
        if not (tr.puede_coordinar(u) or datos["chofer"].get("usuario_id") == u["id"]):
            raise HTTPException(status_code=403, detail="La ficha del chofer es para la coordinación de traslados y el propio chofer.")
        return pagina(request, "traslados_chofer.html", d=datos, ETIQ_ESTADO_CHOFER=tr.ETIQUETAS_ESTADO_CHOFER)

    # ------------------------------------------------------------ recursos
    @app.get("/traslados/recursos", response_class=HTMLResponse)
    def recursos(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        todos_v = tr.listar_vehiculos(incluir_baja=True)
        return pagina(request, "traslados_recursos.html", choferes=tr.listar_choferes(), vehiculos=[x for x in todos_v if x["estado"] != "BAJA"],
                      vehiculos_baja=[x for x in todos_v if x["estado"] == "BAJA"],
                      vencen=tr.vencimientos(60), usuarios=[x for x in db.listar_usuarios() if x["activo"]],
                      ESTADOS_CHOFER=tr.ESTADOS_CHOFER, ESTADOS_VEHICULO=tr.ESTADOS_VEHICULO)

    @app.post("/traslados/recursos/vehiculo")
    async def vehiculo_guardar(request: Request):
        exigir_coord(usuario_actual(request))
        f = await request.form()
        vid = int(f["id"]) if str(f.get("id") or "").isdigit() else None
        destino = f.get("volver") if str(f.get("volver") or "").startswith("/traslados/") else "/traslados/recursos"
        datos = {k: x for k, x in f.items() if k not in ("id", "volver")}
        cambios = []
        try:
            tr.guardar_vehiculo(vid, datos, cambios)
        except ErrorTraslado as e:
            return volver(request, destino + ("&" if "?" in destino else "?") + "editar=1" if vid else destino, str(e))
        dominio = str(f.get("dominio") or "").upper().replace(" ", "")
        if not vid:
            return volver(request, destino, f"Vehículo {dominio} dado de alta correctamente.")
        if not cambios:
            return volver(request, destino, f"No había cambios para guardar en {dominio}.")
        return volver(request, destino, f"Datos de {dominio} guardados correctamente ({', '.join(c.lower() for c in cambios)}).")

    @app.post("/traslados/recursos/vehiculo/{vid:int}/estado")
    async def vehiculo_estado(request: Request, vid: int):
        exigir_coord(usuario_actual(request))
        f = await request.form()
        destino = f.get("volver") if str(f.get("volver") or "").startswith("/traslados/") else "/traslados/recursos"
        estado = f.get("estado")
        try:
            v = next((x for x in tr.listar_vehiculos(incluir_baja=True) if x["id"] == vid), None)
            tr.estado_vehiculo(vid, estado, f.get("motivo"))
            dominio = v["dominio"] if v else "El vehículo"
            mensajes = {"DISPONIBLE": f"{dominio} quedó disponible" + (" (recuperado)" if v and v["estado"] == "BAJA" else "") + ".",
                        "TALLER": f"{dominio} quedó en taller.",
                        "FUERA_DE_CIRCULACION": f"{dominio} quedó suspendido: no se puede asignar hasta reactivarlo.",
                        "BAJA": f"{dominio} eliminado de la flota. Su historial se conserva y se puede recuperar desde «Vehículos eliminados»."}
            return volver(request, destino, mensajes.get(estado, "Estado del vehículo actualizado."))
        except ErrorTraslado as e:
            return volver(request, destino, str(e))

    @app.post("/traslados/recursos/chofer/{cid:int}/vincular")
    async def chofer_vincular(request: Request, cid: int):
        """Ficha de chofer cargada por nombre (p. ej. al importar viajes) → usuario. Si el usuario ya tenía ficha, se unifican."""
        exigir_coord(usuario_actual(request))
        f = await request.form()
        uid = str(f.get("usuario_id") or "")
        if not uid.isdigit():
            return volver(request, "/traslados/recursos", "Elegí el usuario.")
        try:
            r = tr.vincular_chofer(cid, int(uid))
        except ErrorTraslado as e:
            return volver(request, "/traslados/recursos", str(e))
        return volver(request, "/traslados/recursos", f"Fichas unificadas: {r['registros']} registro(s) (viajes, licencias, reservas) pasaron a la ficha del usuario."
                      if r["unificada"] else "Chofer vinculado al usuario.")

    @app.post("/traslados/recursos/chofer")
    async def chofer_guardar(request: Request):
        exigir_coord(usuario_actual(request))
        f = await request.form()
        cid = int(f["id"]) if str(f.get("id") or "").isdigit() else None
        try:
            tr.guardar_chofer(cid, dict(f))
            return volver(request, "/traslados/recursos", "Chofer guardado.")
        except ErrorTraslado as e:
            return volver(request, "/traslados/recursos", str(e))

    @app.post("/traslados/recursos/chofer/{cid:int}/estado")
    async def chofer_estado(request: Request, cid: int):
        exigir_coord(usuario_actual(request))
        f = await request.form()
        try:
            tr.estado_chofer(cid, f.get("estado"))
            return volver(request, "/traslados/recursos", "Estado del chofer actualizado.")
        except ErrorTraslado as e:
            return volver(request, "/traslados/recursos", str(e))

    # ------------------------------------------------------------ presentismo (área de Personal)
    def _semana_pedida(lunes):
        try:
            return tr.lunes_de(lunes) if lunes else tr.lunes_de(date.today())
        except ErrorTraslado:
            return tr.lunes_de(date.today())

    @app.get("/traslados/presentismo", response_class=HTMLResponse)
    def presentismo_pagina(request: Request, lunes: str = ""):
        u = usuario_actual(request)
        if not presentismo.puede_ver(u):
            raise HTTPException(status_code=403, detail="Este reporte es para el área de Personal y la coordinación de traslados.")
        l = _semana_pedida(lunes)
        return pagina(request, "traslados_presentismo.html", datos=presentismo.semana(l), dias=presentismo.DIAS,
                      anterior=iso(l - timedelta(days=7)), siguiente=iso(l + timedelta(days=7)), lunes=iso(l))

    @app.get("/traslados/presentismo.xlsx")
    def presentismo_excel(request: Request, lunes: str = ""):
        u = usuario_actual(request)
        if not presentismo.puede_ver(u):
            raise HTTPException(status_code=403, detail="Este reporte es para el área de Personal y la coordinación de traslados.")
        l = _semana_pedida(lunes)
        contenido = presentismo.excel(presentismo.semana(l), generado_por=u["nombre"])
        return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="presentismo_semana_{iso(l)}.xlsx"'})

    # ------------------------------------------------------------ personas sin usuario
    @app.get("/traslados/externos", response_class=HTMLResponse)
    def externos_pagina(request: Request, bajas: int = 0, editar: int = 0):
        exigir_coord(usuario_actual(request))
        lista = tr.listar_externos(incluir_bajas=bool(bajas))
        usuarios = sorted((x for x in db.listar_usuarios() if x["activo"] and x["rol"] != "CHOFER"), key=lambda x: x["nombre"].lower())
        for e in lista:
            sug = tr.sugerir_usuarios(e["nombre"], usuarios, 1) if not e["usuario_id"] else []
            e["sugerido"] = sug[0]["id"] if sug and sug[0]["parecido"] >= 50 else None
            e["parecido"] = sug[0]["parecido"] if e["sugerido"] else None
        return pagina(request, "traslados_externos.html", lista=lista, usuarios=usuarios, bajas=bajas,
                      editando=next((e for e in lista if e["id"] == editar), None))

    @app.post("/traslados/externos/{eid:int}")
    async def externos_guardar(request: Request, eid: int):
        exigir_coord(usuario_actual(request))
        f = await request.form()
        try:
            tr.actualizar_externo(eid, f.get("nombre"), f.get("area"), f.get("observaciones"))
        except ErrorTraslado as e:
            return volver(request, f"/traslados/externos?editar={eid}", str(e))
        return volver(request, "/traslados/externos", "Datos guardados.")

    @app.post("/traslados/externos/{eid:int}/vincular")
    async def externos_vincular(request: Request, eid: int):
        exigir_coord(usuario_actual(request))
        f = await request.form()
        uid = str(f.get("usuario_id") or "")
        if not uid.isdigit():
            return volver(request, "/traslados/externos", "Elegí el usuario.")
        try:
            n = tr.vincular_externo(eid, int(uid))
        except ErrorTraslado as e:
            return volver(request, "/traslados/externos", str(e))
        return volver(request, "/traslados/externos", f"Vinculado: {n} viaje(s) pasaron al historial del usuario.")

    @app.post("/traslados/externos/{eid:int}/activo")
    async def externos_activo(request: Request, eid: int):
        exigir_coord(usuario_actual(request))
        f = await request.form()
        tr.baja_externo(eid, f.get("activo") == "1")
        return volver(request, "/traslados/externos", "Listado actualizado.")

    # ------------------------------------------------------------ importar Excel histórico
    @app.get("/traslados/importar", response_class=HTMLResponse)
    def importar_pagina(request: Request):
        exigir_coord(usuario_actual(request))
        return pagina(request, "traslados_importar.html", informe=None)

    @app.post("/traslados/importar", response_class=HTMLResponse)
    async def importar_excel(request: Request):
        import tempfile
        from pathlib import Path
        import importar_viajes
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        archivo = f.get("archivo")
        if not archivo or not getattr(archivo, "filename", "").lower().endswith(".xlsx"):
            return volver(request, "/traslados/importar", "Elegí el Excel de viajes (.xlsx).")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows: no falla si el archivo quedó tomado
            ruta = Path(tmp) / "viajes.xlsx"
            contenido = await archivo.read()
            ruta.write_bytes(contenido)
            try:
                informe = importar_viajes.importar(str(ruta), aplicar=bool(f.get("aplicar")), usuario=u)
            except Exception as e:
                return volver(request, "/traslados/importar", f"No pude leer el Excel: {e}")
        if f.get("aplicar"):  # el original queda guardado en la carpeta de bases cargadas
            import archivo_cargas
            archivo_cargas.guardar("viajes", None, archivo.filename, contenido, u)
        return pagina(request, "traslados_importar.html", informe=informe, aplicado=bool(f.get("aplicar")))

    # ------------------------------------------------------------ equipos del historial migrado
    @app.get("/traslados/equipos-historial", response_class=HTMLResponse)
    def equipos_historial(request: Request):
        exigir_coord(usuario_actual(request))
        pendientes, vinculados = tr.equipos_del_historial()
        return pagina(request, "traslados_equipos_historial.html", pendientes=pendientes, vinculados=vinculados, equipos=db.listar_equipos())

    @app.post("/traslados/equipos-historial")
    async def equipos_historial_vincular(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        nombres = f.getlist("nombre")
        hechos, personas = 0, 0
        for i, nombre in enumerate(nombres):
            destino = str(f.get(f"equipo_{i}") or "")
            if destino.isdigit():
                personas += tr.vincular_equipo_historial(nombre, int(destino), u)
                hechos += 1
        if not hechos:
            return volver(request, "/traslados/equipos-historial", "No elegiste ningún equipo para vincular.")
        return volver(request, "/traslados/equipos-historial", f"Listo: {hechos} nombre(s) vinculado(s), {personas} participación(es) pasaron al equipo del sistema.")

    @app.post("/traslados/equipos-historial/deshacer")
    async def equipos_historial_deshacer(request: Request):
        u = usuario_actual(request)
        exigir_coord(u)
        f = await request.form()
        n = tr.deshacer_vinculo_equipo(f.get("alias"), u)
        return volver(request, "/traslados/equipos-historial", f"Vínculo deshecho: {n} participación(es) volvieron a su nombre original.")

    # ------------------------------------------------------------ reportes
    @app.get("/traslados/reportes", response_class=HTMLResponse)
    def reportes(request: Request, desde: str = "", hasta: str = "", circuito: str = ""):
        u = usuario_actual(request)
        hoy = date.today()
        desde = desde or iso(hoy.replace(day=1))
        hasta = hasta or iso(hoy)
        alc = tr.alcance_estadisticas(u)
        try:
            circ, filtro = circuito_con_todos(u, circuito)
            est = tr.estadisticas(desde, hasta, alc, circuito=filtro)
        except ErrorTraslado as e:
            return volver(request, "/traslados/reportes", str(e))
        return pagina(request, "traslados_reportes.html", est=est, alcance=alc, desde=desde, hasta=hasta,
                      circuito=circ, circuitos=tr.circuitos_visibles(u))

    @app.get("/traslados/reportes.csv")
    def reportes_csv(request: Request, desde: str = "", hasta: str = "", circuito: str = ""):
        u = usuario_actual(request)
        hoy = date.today()
        desde = desde or iso(hoy.replace(day=1))
        hasta = hasta or iso(hoy)
        est = tr.estadisticas(desde, hasta, tr.alcance_estadisticas(u), circuito=circuito_con_todos(u, circuito)[1])
        s = io.StringIO()
        w = csv.writer(s, delimiter=";")
        w.writerow(["Fecha", "Salida", "Regreso", "Destino", "Modalidad", "Desplazamiento", "Chofer", "Vehículo", "Personas", "Equipos (viajes a la unidad)",
                    "Participantes (PM = por sus propios medios)", "Fuera de plazo", "Motivo"])
        for v in est["viajes_detalle"]:
            if v["modalidad"] == "CHOFER":
                desp = f"{v['fecha']} · " + (v["vehiculo_dominio"] or v["chofer_nombre"] or f"viaje {v['id']}")
            else:
                desp = ""
            equipos = sorted({(p.get("equipo_nombre") or "").strip() for p in v["participantes"]} - {""})
            w.writerow([v["fecha"], v["hora_salida"] or "", v["hora_regreso"] or "", v["destino"], tr.ETIQUETAS_MODALIDAD[v["modalidad"]],
                        desp, v["chofer_nombre"] or "", v["vehiculo_dominio"] or "", len(v["participantes"]), ", ".join(equipos),
                        ", ".join(p["nombre"] + (" (sin usuario)" if p.get("externo") else "") + (" (PM)" if p.get("auto_propio") else "") for p in v["participantes"]), "Sí" if v["inmediato"] else "", v["motivo"] or ""])
        return Response("﻿" + s.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="viajes_{desde}_{hasta}.csv"'})

    # ------------------------------------------------------------ unidades
    @app.get("/unidades", response_class=HTMLResponse)
    def unidades(request: Request, q: str = "", tipo: str = "", provincia: str = "", inactivas: int = 0, editar: int = 0,
                 fuerza_id: str = "", delegacion: str = "", pag: str = "1"):
        return _pagina_unidades(request, q, tipo, provincia, inactivas, editar, fuerza_id, delegacion, pag)

    @app.get("/unidades/{uid:int}/editar", response_class=HTMLResponse)
    def unidad_editar(request: Request, uid: int, q: str = "", tipo: str = "", provincia: str = "", inactivas: int = 0,
                      fuerza_id: str = "", delegacion: str = "", pag: str = "1"):
        """Modificación 3B: la edición tiene su propia dirección y conserva los filtros del listado."""
        exigir_admin(usuario_actual(request))
        if not db.obtener_unidad(uid):
            raise HTTPException(status_code=404, detail="La unidad no existe.")
        return _pagina_unidades(request, q, tipo, provincia, inactivas, uid, fuerza_id, delegacion, pag)

    def _pagina_unidades(request, q="", tipo="", provincia="", inactivas=0, editar=0, fuerza_id="", delegacion="", pag="1",
                         previo=None, error=None):
        """Listado y ABM de unidades. `previo` (lo cargado) y `error` se usan cuando el alta o la edición no pasan la validación:
        el formulario queda abierto y con los datos (Modificación 3)."""
        exigir_admin(usuario_actual(request))
        q, tipo, provincia, delegacion = (str(x or "") for x in (q, tipo, provincia, delegacion))
        inactivas = 1 if str(inactivas) in ("1", "true", "on") else 0
        editar = int(editar) if str(editar or "").isdigit() else 0
        import detenidos as det_
        import unidades_menu
        fid = int(fuerza_id) if str(fuerza_id).lstrip("-").isdigit() else None
        lista = db.listar_unidades(q or None, tipo or None, provincia or None, solo_activas=not inactivas, fuerza_id=fid, delegacion=delegacion or None)
        todas = db.listar_unidades(solo_activas=False)
        n_pag = int(pag) if str(pag).isdigit() and int(pag) > 0 else 1
        por = 100
        return pagina(request, "unidades.html", lista=lista[(n_pag - 1) * por: n_pag * por], total=len(lista), pag=n_pag, paginas=max(1, -(-len(lista) // por)),
                      q=q, tipo=tipo, provincia=provincia, inactivas=inactivas, fuerza_id=fid, delegacion=delegacion,
                      tipos=sorted({x["tipo"] for x in todas if x["tipo"]}), provincias=sorted({x["provincia"] for x in todas if x["provincia"]}),
                      delegaciones=sorted({x["delegacion_ppn"] for x in todas if x.get("delegacion_ppn")}), nombres_deleg=unidades_menu.DELEGACIONES,
                      fuerzas=det_.listar_fuerzas(False), cargas_menu=unidades_menu.cargas(10),
                      editando=db.obtener_unidad(editar) if editar else None, previo=previo, error=error,
                      incompletas=sum(1 for x in todas if x["activa"] and not (x["ciudad"] and x["provincia"] and x["correo"])))

    @app.post("/unidades")
    async def unidad_guardar(request: Request):
        exigir_admin(usuario_actual(request))
        f = await request.form()
        uid = int(f["id"]) if str(f.get("id") or "").isdigit() else None
        datos = dict(f)
        try:
            if datos.get("fuerza_id") == "nueva":  # «+ Otra fuerza»: se crea y se elige
                import detenidos as det_
                datos["fuerza_id"] = str(det_.crear_fuerza(datos.get("fuerza_nueva"), datos.get("fuerza_sigla"), usuario_actual(request)))
            db.guardar_unidad(uid, datos)
            filtros = str(f.get("filtros") or "")
            return volver(request, "/unidades" + ("?" + filtros if filtros and "\n" not in filtros and "//" not in filtros else ""), "Unidad guardada.")
        except ValueError as e:
            # Modificación 3: el formulario vuelve abierto, con lo que se cargó y el error a la vista (también como aviso).
            flash(request, "⚠ " + str(e))
            return _pagina_unidades(request, editar=uid or 0, previo={k: v for k, v in datos.items() if k not in ("id", "filtros")}, error=str(e))

    @app.post("/unidades/{uid:int}/activa")
    async def unidad_activa(request: Request, uid: int):
        exigir_admin(usuario_actual(request))
        f = await request.form()
        db.cambiar_unidad_activa(uid, bool(int(f.get("activa") or 0)))
        return volver(request, "/unidades", "Unidad actualizada.")

    @app.get("/unidades.csv")
    def unidades_csv(request: Request):
        exigir_admin(usuario_actual(request))
        s = io.StringIO()
        w = csv.writer(s, delimiter=";")
        w.writerow(COLUMNAS_UNIDADES)
        for x in db.listar_unidades(solo_activas=False):
            w.writerow([x[c] or "" for c in COLUMNAS_UNIDADES])
        return Response("﻿" + s.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="unidades.csv"'})

    # Beta 01 (151): importación de unidades con vista previa y mensajes claros (nunca un error técnico).
    def _carpeta_imp():
        from pathlib import Path as _P
        c = _P(db.RUTA_DB).parent / "importaciones_unidades"
        c.mkdir(parents=True, exist_ok=True)
        return c

    def _leer_previa(token):
        import json as _json
        if not token or not token.replace("-", "").replace("_", "").isalnum():
            return None
        ruta = _carpeta_imp() / f"{token}.json"
        return _json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else None

    @app.post("/unidades/importar", response_class=HTMLResponse)
    async def unidades_importar(request: Request):
        import json as _json
        import secrets
        import unidades_csv
        exigir_admin(usuario_actual(request))
        f = await request.form()
        archivo = f.get("archivo")
        if not archivo or not getattr(archivo, "filename", ""):
            return volver(request, "/unidades", "Elegí un archivo CSV.")
        _contenido = await archivo.read()
        if _contenido[:4] == b"PK\x03\x04":  # un Excel: si es la base del Menú de la Procuración, va a su vista previa
            menu, avisos = _leer_menu(_contenido, archivo.filename)
            if menu:
                u = usuario_actual(request)
                return _previa_menu(request, u, _contenido, archivo.filename, menu,
                                    ["Este archivo es la base de unidades del Menú de la Procuración: se abrió su vista previa."] + avisos)
        try:
            res = unidades_csv.leer(_contenido)
        except Exception:
            logging.exception("Error inesperado leyendo el CSV de unidades")
            res = {"filas": [], "errores_generales": ["No fue posible importar el archivo: no se pudo leer como CSV. Probá guardarlo de nuevo como «CSV (delimitado por punto y coma)»."],
                   "columnas": [], "ignoradas": []}
        token = secrets.token_urlsafe(12)
        try:
            (_carpeta_imp() / f"{token}.orig").write_bytes(_contenido)
        except NameError:
            pass
        (_carpeta_imp() / f"{token}.json").write_text(_json.dumps(dict(res, _archivo=archivo.filename), ensure_ascii=False), encoding="utf-8")
        return pagina(request, "unidades_importar.html", res=res, resumen=unidades_csv.resumen(res), token=token, archivo=archivo.filename)

    @app.post("/unidades/importar/confirmar")
    async def unidades_importar_confirmar(request: Request):
        import unidades_csv
        exigir_admin(usuario_actual(request))
        f = await request.form()
        res = _leer_previa(str(f.get("token") or ""))
        if not res:
            return volver(request, "/unidades", "La vista previa venció: volvé a subir el archivo.")
        try:
            creadas, actualizadas, errores = unidades_csv.aplicar(res)
        except Exception:
            logging.exception("Error inesperado importando unidades")
            return volver(request, "/unidades", "No fue posible importar el archivo. No se guardó nada; avisá a Sistemas si se repite.")
        (_carpeta_imp() / f"{f.get('token')}.json").unlink(missing_ok=True)
        orig = _carpeta_imp() / f"{f.get('token')}.orig"
        if orig.is_file():
            import archivo_cargas
            archivo_cargas.guardar("unidades_csv", None, res.get("_archivo"), orig.read_bytes(), usuario_actual(request))
            orig.unlink(missing_ok=True)
        omitidas = unidades_csv.resumen(res)["errores"] + len(errores)
        return volver(request, "/unidades", f"Importación: {creadas} nuevas, {actualizadas} actualizadas" + (f"; {omitidas} fila(s) con error no se tomaron." if omitidas else "."))

    # ------------------------------------------------------------ base de unidades del Menú de la Procuración
    def _carpeta_menu():
        from pathlib import Path as _P
        c = _P(db.RUTA_DB).parent / "importaciones_unidades"
        c.mkdir(parents=True, exist_ok=True)
        return c

    def _previa_menu(request, u, contenido, nombre_archivo, menu, avisos):
        import json as _json
        import secrets
        import archivo_cargas
        import detenidos as det_
        import unidades_menu
        archivo = type("A", (), {"filename": nombre_archivo})()
        token = secrets.token_urlsafe(12)
        (_carpeta_menu() / f"menu_{token}.json").write_text(_json.dumps({"usuario_id": u["id"], "archivo": archivo.filename, "menu": menu,
                                                                        "sha256": archivo_cargas.huella(contenido)}, ensure_ascii=False), encoding="utf-8")
        (_carpeta_menu() / f"menu_{token}.orig").write_bytes(contenido)
        ya = archivo_cargas.ya_cargado(contenido, "unidades_menu")
        p = unidades_menu.previa(menu)
        return pagina(request, "unidades_menu.html", p=p, token=token, archivo=archivo.filename, avisos=avisos, ya_cargado=ya,
                      unidades=db.listar_unidades(solo_activas=False), nombres_deleg=unidades_menu.DELEGACIONES,
                      fuerzas={(x["sigla"] or ""): x["nombre"] for x in det_.listar_fuerzas(False)})

    def _leer_menu(contenido, nombre):
        import unidades_menu
        try:
            return unidades_menu.leer(contenido, nombre)
        except Exception:
            logging.exception("No se pudo leer el archivo del Menú")
            return [], ["No se pudo leer el archivo. Tiene que ser el Excel (.xlsx) que exporta el Menú de la Procuración."]

    @app.post("/unidades/menu", response_class=HTMLResponse)
    async def unidades_menu_subir(request: Request):
        u = usuario_actual(request)
        exigir_admin(u)
        f = await request.form()
        archivo = f.get("archivo")
        if not archivo or not getattr(archivo, "filename", ""):
            return volver(request, "/unidades", "Elegí el Excel de unidades exportado del Menú de la Procuración.")
        contenido = await archivo.read()
        menu, avisos = _leer_menu(contenido, archivo.filename)
        if not menu:
            return volver(request, "/unidades", "⚠ " + " ".join(avisos))
        return _previa_menu(request, u, contenido, archivo.filename, menu, avisos)

    @app.post("/unidades/menu/confirmar")
    async def unidades_menu_confirmar(request: Request):
        import json as _json
        import archivo_cargas
        import unidades_menu
        u = usuario_actual(request)
        exigir_admin(u)
        f = await request.form()
        token = str(f.get("token") or "")
        ruta = _carpeta_menu() / f"menu_{token}.json"
        if not token.replace("-", "").replace("_", "").isalnum() or not ruta.is_file():
            return volver(request, "/unidades", "La vista previa venció: volvé a subir el archivo.")
        datos = _json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("usuario_id") != u["id"]:
            raise HTTPException(status_code=403, detail="Esta vista previa la generó otra persona.")
        decisiones = {k[5:]: v for k, v in f.items() if k.startswith("dest_")}
        r = unidades_menu.aplicar(datos["menu"], decisiones, u, datos.get("archivo"))
        orig = _carpeta_menu() / f"menu_{token}.orig"
        if orig.is_file():
            archivo_cargas.guardar("unidades_menu", r["carga_id"], datos.get("archivo"), orig.read_bytes(), u)
            orig.unlink(missing_ok=True)
        ruta.unlink(missing_ok=True)
        return volver(request, "/unidades", f"Unidades del Menú: {r['vinculadas']} vinculada(s) a unidades existentes, {r['altas']} alta(s) nueva(s)"
                      + (f", {r['omitidas']} omitida(s)" if r["omitidas"] else "")
                      + (f"; {r['detenidos']} persona(s) detenida(s) pasaron a su unidad del catálogo" if r["detenidos"] else "") + f". Carga #{r['carga_id']}.")

    @app.post("/unidades/menu/{carga_id:int}/anular")
    async def unidades_menu_anular(request: Request, carga_id: int):
        import unidades_menu
        u = usuario_actual(request)
        exigir_admin(u)
        f = await request.form()
        try:
            r = unidades_menu.anular(carga_id, f.get("motivo"), u)
        except ValueError as e:
            return volver(request, "/unidades", "⚠ " + str(e))
        return volver(request, "/unidades", f"Carga #{carga_id} anulada: {r['bajas']} unidad(es) que había dado de alta quedaron inactivas y {r['revertidos']} dato(s) volvieron a su valor anterior.")

    @app.get("/unidades/menu/{carga_id:int}/original")
    def unidades_menu_original(request: Request, carga_id: int):
        import archivo_cargas
        from urllib.parse import quote as _q
        exigir_admin(usuario_actual(request))
        reg = archivo_cargas.de_carga("unidades_menu", carga_id)
        if not reg:
            raise HTTPException(status_code=404, detail="Esta carga no tiene el archivo original guardado.")
        nombre, contenido = archivo_cargas.leer(reg)
        return Response(contenido, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_q(nombre)}"})

    @app.get("/unidades/menu.xlsx")
    def unidades_menu_exportar(request: Request):
        import unidades_menu
        exigir_admin(usuario_actual(request))
        return Response(unidades_menu.exportar(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="unidades_formato_menu.xlsx"'})

    @app.get("/unidades/plantilla.csv")
    def unidades_plantilla(request: Request):
        import unidades_csv
        exigir_admin(usuario_actual(request))
        return Response(unidades_csv.plantilla(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="plantilla_unidades.csv"'})

    @app.get("/unidades/importar/errores.csv")
    def unidades_errores(request: Request, token: str = ""):
        import unidades_csv
        exigir_admin(usuario_actual(request))
        res = _leer_previa(token)
        if not res:
            return volver(request, "/unidades", "El informe venció: volvé a subir el archivo.")
        return Response(unidades_csv.informe_errores(res), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="errores_importacion_unidades.csv"'})

    @app.post("/nota/{nota_id:int}/unidad")
    async def nota_unidad(request: Request, nota_id: int):
        u = usuario_actual(request)
        nota = db.obtener_nota(nota_id)
        mesa_ok = nota and u["rol"] in auth.ROLES_MESA and nota["estado"] in ("FIRMADA", "DESPACHADA")
        if not nota or not (ctx["puede_gestionar_nota"](u, nota) or mesa_ok):
            raise HTTPException(status_code=403, detail="No tenés permiso sobre esta nota.")
        f = await request.form()
        db.fijar_unidad_nota(nota_id, int(f["unidad_id"]) if str(f.get("unidad_id") or "").isdigit() else None)
        return volver(request, f"/nota/{nota_id}", "Unidad de destino actualizada.")

    # ------------------------------------------------------------ mapa de unidades
    @app.get("/traslados/mapa", response_class=HTMLResponse)
    def mapa(request: Request, circuito: str = ""):
        u = usuario_actual(request)
        circ = circuito_elegido(u, circuito) if circuito else None
        puestas, sin_ubicar = tr.datos_mapa(circ)
        return pagina(request, "mapa.html", puestas=puestas, sin_ubicar=sin_ubicar, circuito=circ or "",
                      circuitos=tr.circuitos_visibles(u), soy_admin=auth.es_admin(u))

    # ------------------------------------------------------------ equipos: unidades por defecto
    @app.post("/admin/equipos/{equipo_id:int}/unidades")
    async def equipo_unidades(request: Request, equipo_id: int):
        exigir_admin(usuario_actual(request))
        f = await request.form()
        if not any(e["id"] == equipo_id for e in db.listar_equipos()):
            raise HTTPException(status_code=404, detail="El equipo no existe.")
        n = len(db.fijar_unidades_de_equipo(equipo_id, f.getlist("unidad_id")))
        return volver(request, "/admin/equipos/listado", f"Unidades por defecto guardadas ({n}).")

    # ------------------------------------------------------------ avatar de perfil
    @app.get("/avatar/{usuario_id:int}")
    def ver_avatar(request: Request, usuario_id: int):
        usuario_actual(request)
        ruta = avatares.ruta(usuario_id)
        if not ruta.is_file():
            raise HTTPException(status_code=404, detail="Sin imagen.")
        return Response(ruta.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})

    @app.post("/perfil/avatar")
    async def subir_avatar(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        archivo = f.get("imagen")
        if not archivo or not getattr(archivo, "filename", ""):
            return volver(request, "/perfil", "Elegí una imagen.")
        try:
            avatares.guardar(u["id"], await archivo.read())
            db.marcar_avatar(u["id"], True)
        except ValueError as e:
            return volver(request, "/perfil", str(e))
        return volver(request, "/perfil", "Imagen de perfil actualizada.")

    @app.post("/perfil/avatar/quitar")
    async def quitar_avatar(request: Request):
        u = usuario_actual(request)
        avatares.borrar(u["id"])
        db.marcar_avatar(u["id"], False)
        return volver(request, "/perfil", "Imagen quitada: se usan tus iniciales.")
