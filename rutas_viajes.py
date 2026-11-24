"""Pantallas nuevas del área Autos PPN y Metas (propuestas 54 a 97). Se registran desde main.py con `registrar(app, ctx)`.

La lógica vive en dgpdh/ (dominio y servicios) o en los módulos históricos del área; acá solo se controla el permiso, se lee
el formulario y se muestra. Cada pantalla está marcada con el número de propuesta.
"""
import csv
import io
from datetime import date, datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import auth
import database as db
import fechas
import traslados as tr
from dgpdh.dominio import calendario as cal
from dgpdh.errores import ErrorDGPDH
from dgpdh.seguridad import auditoria as au
from dgpdh.servicios import feriados as srv_feriados
from dgpdh.servicios import ingreso_unidades
from dgpdh.servicios import metas_visitas
from dgpdh.servicios import opciones_viajes
from dgpdh.servicios import ventanas as srv_ventanas
from dgpdh.dominio import ventanas as dom_ventanas
from dgpdh.servicios import viajes_campo


def actor(u):
    return au.Actor.desde(u)


def ahora_local():
    """Hora del servidor en Buenos Aires (la que se guarda al tocar «Llegamos», «Ingresamos» o «Salimos»)."""
    return datetime.now(fechas.zona())


def chofer_de(u):
    with db._conexion() as con:
        f = con.execute("SELECT id FROM choferes WHERE usuario_id = ?", (u["id"],)).fetchone()
    return f["id"] if f else None


def permiso_en_viaje(u, v):
    """Propuestas 72 y 73: los permisos de siempre (coordinación, chofer del viaje, quienes viajan)."""
    mi_chofer = chofer_de(u)
    return viajes_campo.Permiso(coordina=tr.puede_coordinar(u), es_chofer_del_viaje=bool(v.get("chofer_id")) and v.get("chofer_id") == mi_chofer,
                                viaja=any(p.get("usuario_id") == u["id"] for p in v.get("participantes", [])))


def _destino_seguro(texto, defecto):
    t = str(texto or "")
    return t if t.startswith("/traslados") and "//" not in t and "\\" not in t else defecto


def ve_obstaculizaciones(u, tiene_notas):
    """Las cifras del Registro de Obstaculizaciones solo entran si la persona puede ver todo ese registro (ADR-0010)."""
    try:
        import rutas_obstaculizaciones
        return rutas_obstaculizaciones.persona(u, tiene_notas).ve_todo
    except Exception:
        return False


def puede_tablero(u):
    return bool(u) and (u["rol"] in ("DIRECTOR", "SUBDIRECTOR") or tr.puede_coordinar(u))


def neutralizar(v):
    """Un texto que empieza con = + - @ se abriría como fórmula en Excel: se le antepone un apóstrofo."""
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


def puede_feriados(u):
    """Propuesta 69: el calendario de feriados lo administran la Dirección y la coordinación de traslados."""
    return bool(u) and (auth.es_admin(u) or tr.puede_coordinar(u))


async def _leer_archivo(archivo, maximo=2 * 1024 * 1024):
    if archivo is None or isinstance(archivo, str):
        return (archivo or "").encode("utf-8")
    contenido = await archivo.read(maximo + 1)
    if len(contenido) > maximo:
        raise tr.ErrorTraslado("El archivo es demasiado grande para ser un calendario de feriados.")
    return contenido


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]
    templates.env.globals["viajes_puede_feriados"] = puede_feriados
    from dgpdh.dominio import espera as _espera
    templates.env.globals["viajes_motivos_espera"] = _espera.MOTIVOS

    def _perfil_de(eid):
        import metas
        return metas.perfil_de(eid)

    templates.env.globals["metas_perfil_de"] = _perfil_de
    from dgpdh.dominio import metas_fichas as _fichas
    templates.env.globals["metas_ficha"] = _fichas.ficha
    from dgpdh.dominio import metas_perfiles as _mp
    templates.env.globals["metas_perfiles"] = _mp.PERFILES
    templates.env.globals["viajes_eventos_parada"] = viajes_campo.EVENTOS
    templates.env.globals["viajes_origenes_constancia"] = viajes_campo.ORIGENES
    templates.env.globals["viajes_permiso"] = permiso_en_viaje
    templates.env.globals["viajes_hoy_local"] = lambda: ahora_local().date().isoformat()
    tiene_notas = ctx["tiene_notas"]

    def ingreso_de_unidad(usuario, unidad_id, desde, hasta):
        """Propuesta 79, para «Datos → Por unidad» y el tablero de Dirección."""
        try:
            with db._conexion() as con:
                return ingreso_unidades.indicador(con, desde, hasta, date.today(), unidad_id=unidad_id,
                                                  incluir_obstaculizaciones=ve_obstaculizaciones(usuario, tiene_notas))
        except Exception:
            import logging
            logging.exception("No se pudo calcular el indicador de ingreso")
            return []

    def ingreso_resumen(usuario, desde, hasta):
        try:
            with db._conexion() as con:
                return ingreso_unidades.resumen_por_unidad(con, desde, hasta, date.today(),
                                                           incluir_obstaculizaciones=ve_obstaculizaciones(usuario, tiene_notas))
        except Exception:
            import logging
            logging.exception("No se pudo calcular el indicador de ingreso")
            return []

    def diferencias_metas(anio=None):
        with db._conexion() as con:
            return metas_visitas.diferencias(con, anio)

    templates.env.globals["viajes_diferencias_metas"] = diferencias_metas
    templates.env.globals["viajes_ingreso_unidad"] = ingreso_de_unidad
    templates.env.globals["viajes_ingreso_resumen"] = ingreso_resumen

    def pagina(request, nombre, **c):
        u = usuario_actual(request)
        base = {"request": request, "usuario": u, "coordina": tr.puede_coordinar(u)}
        base.update(c)
        return templates.TemplateResponse(nombre, base)

    def volver(request, destino, mensaje=None):
        if mensaje:
            flash(request, mensaje)
        return RedirectResponse(url=destino, status_code=303)

    # ------------------------------------------------------------ propuesta 64: proveedores, responsables y días de aviso
    from dgpdh.servicios import proveedores as srv_prov
    from dgpdh.dominio import avisos_flota as dom_af

    def proveedores_activos():
        try:
            with db._conexion() as con:
                return srv_prov.listar(con)
        except Exception:
            return []

    def responsables_posibles():
        return [x for x in db.listar_usuarios() if x["activo"]]

    templates.env.globals["viajes_proveedores"] = proveedores_activos
    templates.env.globals["viajes_responsables"] = responsables_posibles
    templates.env.globals["viajes_tipos_proveedor"] = dom_af.TIPOS_PROVEEDOR

    def _exigir_coord(u):
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")

    @app.get("/traslados/proveedores", response_class=HTMLResponse)
    def proveedores(request: Request, bajas: str = "", editar: int = 0):
        u = usuario_actual(request)
        _exigir_coord(u)
        import flota
        with db._conexion() as con:
            lista = srv_prov.listar(con, incluir_baja=bool(bajas))
            conf = srv_prov.dias_configurados(con)
        dias = [{"tipo": t, "nombre": n, "dias": dom_af.dias_aviso(t, conf), "configurado": t in conf,
                 "defecto": dom_af.dias_aviso(t)} for t, n in flota.TIPOS.items()]
        return pagina(request, "viajes_proveedores.html", lista=lista, bajas=bool(bajas), dias=dias, TIPOS=dom_af.TIPOS_PROVEEDOR,
                      editar=next((p for p in lista if p["id"] == editar and p["activo"]), None))

    @app.post("/traslados/proveedores")
    async def proveedor_guardar(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        pid = str(f.get("id") or "")
        try:
            with db._conexion() as con:
                srv_prov.guardar(con, actor(u), tr.puede_coordinar(u), dict(f), int(pid) if pid.isdigit() else None)
        except ErrorDGPDH as e:
            if e.codigo_http == 403:
                raise HTTPException(status_code=403, detail=e.mensaje) from e
            return volver(request, "/traslados/proveedores", "⚠ " + e.mensaje)
        return volver(request, "/traslados/proveedores", "Proveedor guardado.")

    @app.post("/traslados/proveedores/{pid:int}/baja")
    async def proveedor_baja(request: Request, pid: int):
        u = usuario_actual(request)
        f = await request.form()
        try:
            with db._conexion() as con:
                srv_prov.dar_de_baja(con, actor(u), tr.puede_coordinar(u), pid, str(f.get("motivo") or "") or None)
        except ErrorDGPDH as e:
            if e.codigo_http == 403:
                raise HTTPException(status_code=403, detail=e.mensaje) from e
            return volver(request, "/traslados/proveedores", "⚠ " + e.mensaje)
        return volver(request, "/traslados/proveedores", "Proveedor dado de baja (los controles que lo citan lo siguen mostrando).")

    @app.post("/traslados/proveedores/dias-aviso")
    async def proveedores_dias(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        import flota
        try:
            with db._conexion() as con:
                n = srv_prov.guardar_dias(con, actor(u), tr.puede_coordinar(u), list(flota.TIPOS), {t: f.get(f"dias_{t}") for t in flota.TIPOS})
        except ErrorDGPDH as e:
            if e.codigo_http == 403:
                raise HTTPException(status_code=403, detail=e.mensaje) from e
            return volver(request, "/traslados/proveedores#dias", "⚠ " + e.mensaje)
        return volver(request, "/traslados/proveedores#dias", f"Días de aviso guardados ({n} cambio(s))." if n else "No hubo cambios.")

    # ------------------------------------------------------------ propuesta 65: órdenes de trabajo de taller
    from dgpdh.servicios import ordenes_taller as srv_ot

    def ordenes_de_vehiculo(vid):
        try:
            with db._conexion() as con:
                return srv_ot.del_vehiculo(con, int(vid))
        except Exception:
            import logging
            logging.exception("No se pudieron leer las órdenes de trabajo")
            return []

    templates.env.globals["viajes_ordenes_trabajo"] = ordenes_de_vehiculo

    def _error_ot(request, e, destino):
        if e.codigo_http == 403:
            raise HTTPException(status_code=403, detail=e.mensaje) from e
        return volver(request, destino, "⚠ " + e.mensaje)

    @app.post("/traslados/vehiculos/{vid:int}/ordenes")
    async def orden_nueva(request: Request, vid: int):
        u = usuario_actual(request)
        f = await request.form()
        destino = f"/traslados/vehiculos/{vid}#ordenes"
        nov = [int(x) for x in f.getlist("novedad") if str(x).isdigit()]
        try:
            with db._conexion() as con:
                oid = srv_ot.crear(con, actor(u), tr.puede_coordinar(u), vid, dict(f), nov)
        except ErrorDGPDH as e:
            return _error_ot(request, e, destino)
        return volver(request, destino, f"Orden de trabajo #{oid} creada.")

    @app.post("/traslados/ordenes/{oid:int}/datos")
    async def orden_datos(request: Request, oid: int):
        u = usuario_actual(request)
        f = await request.form()
        destino = f"/traslados/vehiculos/{str(f.get('vehiculo_id') or '')}#ordenes" if str(f.get("vehiculo_id") or "").isdigit() else "/traslados/flota"
        try:
            with db._conexion() as con:
                srv_ot.editar(con, actor(u), tr.puede_coordinar(u), oid, dict(f))
        except ErrorDGPDH as e:
            return _error_ot(request, e, destino)
        return volver(request, destino, f"Orden de trabajo #{oid} actualizada.")

    @app.post("/traslados/ordenes/{oid:int}/etapa")
    async def orden_etapa(request: Request, oid: int):
        u = usuario_actual(request)
        f = await request.form()
        destino = f"/traslados/vehiculos/{str(f.get('vehiculo_id') or '')}#ordenes" if str(f.get("vehiculo_id") or "").isdigit() else "/traslados/flota"
        try:
            with db._conexion() as con:
                r = srv_ot.cambiar_etapa(con, actor(u), tr.puede_coordinar(u), oid, str(f.get("etapa") or ""), str(f.get("nota") or ""),
                                         f.get("costo_final"))
        except ErrorDGPDH as e:
            return _error_ot(request, e, destino)
        destino = f"/traslados/vehiculos/{r['vehiculo_id']}#ordenes"
        msg = f"Orden de trabajo #{oid}: etapa actualizada."
        if r["poner_en_taller"]:
            tr.estado_vehiculo(r["vehiculo_id"], "TALLER", f"Orden de trabajo #{oid}: {r['descripcion']}"[:300])
            msg += " El vehículo quedó en taller."
        if r["ofrecer_disponible"]:
            if f.get("volver_disponible") == "1":
                tr.estado_vehiculo(r["vehiculo_id"], "DISPONIBLE")
                msg += " El vehículo volvió a estar disponible."
            else:
                msg += " El vehículo sigue en taller: cambiá su estado cuando pueda volver a circular."
        return volver(request, destino, msg)

    # ------------------------------------------------------------ propuesta 69: feriados
    @app.get("/traslados/feriados", response_class=HTMLResponse)
    def feriados(request: Request, anio: int = 0, bajas: str = ""):
        u = usuario_actual(request)
        if not puede_feriados(u):
            raise HTTPException(status_code=403, detail="El calendario de feriados lo administran la Dirección y la coordinación de traslados.")
        anio = anio or date.today().year
        with db._conexion() as con:
            lista = srv_feriados.listar(con, anio, incluir_bajas=bool(bajas))
        return pagina(request, "viajes_feriados.html", lista=lista, anio=anio, bajas=bool(bajas), TIPOS=cal.TIPOS_FERIADO)

    @app.post("/traslados/feriados")
    async def feriado_nuevo(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        destino = f"/traslados/feriados?anio={str(f.get('fecha') or '')[:4] or date.today().year}"
        try:
            with db._conexion() as con:
                srv_feriados.guardar(con, actor(u), puede_feriados(u), str(f.get("fecha") or ""), str(f.get("motivo") or ""),
                                     str(f.get("tipo") or "NACIONAL"))
        except ErrorDGPDH as e:
            if e.codigo_http == 403:
                raise HTTPException(status_code=403, detail=e.mensaje) from e
            return volver(request, destino, "⚠ " + e.mensaje)
        return volver(request, destino, "Día no laborable guardado.")

    @app.post("/traslados/feriados/{fid:int}/baja")
    async def feriado_baja(request: Request, fid: int):
        u = usuario_actual(request)
        f = await request.form()
        try:
            with db._conexion() as con:
                srv_feriados.dar_de_baja(con, actor(u), puede_feriados(u), fid, str(f.get("motivo") or ""))
        except ErrorDGPDH as e:
            if e.codigo_http == 403:
                raise HTTPException(status_code=403, detail=e.mensaje) from e
            return volver(request, "/traslados/feriados", "⚠ " + e.mensaje)
        return volver(request, "/traslados/feriados", "Feriado dado de baja (queda en el historial y se puede volver a cargar).")

    @app.post("/traslados/feriados/importar")
    async def feriados_importar(request: Request):
        u = usuario_actual(request)
        if not puede_feriados(u):
            raise HTTPException(status_code=403, detail="El calendario de feriados lo administran la Dirección y la coordinación de traslados.")
        f = await request.form()
        try:
            texto = (await _leer_archivo(f.get("archivo"))).decode("utf-8", errors="replace")
            with db._conexion() as con:
                r = srv_feriados.importar_ics(con, actor(u), True, texto, str(f.get("tipo") or "NACIONAL"))
        except (ErrorDGPDH, tr.ErrorTraslado) as e:
            return volver(request, "/traslados/feriados", "⚠ " + getattr(e, "mensaje", str(e)))
        return volver(request, "/traslados/feriados", f"Calendario importado: {r['nuevos']} día(s) nuevo(s), {r['ya_estaban']} ya estaban.")

    @app.get("/traslados/feriados.ics")
    def feriados_ics(request: Request, anio: int = 0):
        """Descarga del calendario cargado (para revisarlo o pasarlo a otra PC). No sale nada solo."""
        u = usuario_actual(request)
        if not puede_feriados(u):
            raise HTTPException(status_code=403, detail="El calendario de feriados lo administran la Dirección y la coordinación de traslados.")
        with db._conexion() as con:
            lista = srv_feriados.listar(con, anio or None)
        buf = io.StringIO()
        buf.write("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//PPN DGPDH//Feriados//ES\r\n")
        for x in lista:
            d = str(x["fecha"]).replace("-", "")
            buf.write(f"BEGIN:VEVENT\r\nUID:feriado-{x['id']}@dgpdh\r\nDTSTAMP:{d}T000000Z\r\nDTSTART;VALUE=DATE:{d}\r\n"
                      f"SUMMARY:{str(x['motivo']).replace(',', chr(92) + ',')}\r\nEND:VEVENT\r\n")
        buf.write("END:VCALENDAR\r\n")
        return Response(buf.getvalue(), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=feriados_{anio or 'todos'}.ics"})

    # ------------------------------------------------------------ propuestas 72 y 73: en la unidad y constancia
    def _viaje(viaje_id):
        v = tr.obtener_viaje(viaje_id)
        if not v:
            raise HTTPException(status_code=404, detail="El viaje no existe.")
        return v

    def _http(e):
        if e.codigo_http in (403, 404):
            raise HTTPException(status_code=e.codigo_http, detail=e.mensaje) from e

    @app.post("/traslados/{viaje_id:int}/parada/{evento}")
    async def parada_evento(request: Request, viaje_id: int, evento: str):
        u = usuario_actual(request)
        v = _viaje(viaje_id)
        f = await request.form()
        destino = _destino_seguro(f.get("volver"), f"/traslados/{viaje_id}#en-la-unidad")
        permiso = permiso_en_viaje(u, v)
        ahora = ahora_local()
        correccion = bool(f.get("correccion")) and permiso.coordina
        hora = str(f.get("hora") or "").strip() if permiso.coordina and f.get("hora") else ahora.strftime("%H:%M")
        try:
            with db._conexion() as con:
                h = viajes_campo.registrar_evento(con, actor(u), permiso, viaje_id, evento, hora, ahora.date(), correccion=correccion)
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, destino, "⚠ " + e.mensaje)
        return volver(request, destino, f"{viajes_campo.EVENTOS[evento][1]}: {h}.")

    @app.post("/traslados/{viaje_id:int}/constancia")
    async def constancia(request: Request, viaje_id: int):
        u = usuario_actual(request)
        v = _viaje(viaje_id)
        f = await request.form()
        destino = _destino_seguro(f.get("volver"), f"/traslados/{viaje_id}#en-la-unidad")
        try:
            with db._conexion() as con:
                nueva = viajes_campo.confirmar_realizado(con, actor(u), permiso_en_viaje(u, v), viaje_id, ahora_local().date())
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, destino, "⚠ " + e.mensaje)
        return volver(request, destino, "Gracias: quedó constancia de que el viaje se hizo." if nueva else "El viaje ya tenía constancia.")

    # ------------------------------------------------------------ propuesta 79: demora para ingresar
    @app.get("/traslados/ingreso-unidades.csv")
    def ingreso_csv(request: Request, desde: str = "", hasta: str = "", unidad_id: int = 0):
        u = usuario_actual(request)
        if not (puede_tablero(u) or auth.es_admin(u)):
            raise HTTPException(status_code=403, detail="Este indicador es para la Dirección y la coordinación de traslados.")
        hoy = date.today()
        d = desde or (hoy - timedelta(days=365)).isoformat()
        h = hasta or hoy.isoformat()
        try:
            date.fromisoformat(d), date.fromisoformat(h)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Las fechas no son válidas.") from e
        with db._conexion() as con:
            filas = ingreso_unidades.indicador(con, d, h, hoy, unidad_id=unidad_id or None,
                                               incluir_obstaculizaciones=ve_obstaculizaciones(u, tiene_notas))
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        for fila in ingreso_unidades.csv_filas(filas):
            w.writerow([neutralizar(x) for x in fila])
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=ingreso_unidades_{d}_{h}.csv"})

    # ------------------------------------------------------------ propuesta 82: una sola meta de visitas
    @app.post("/traslados/direccion/metas/unificar")
    async def metas_unificar(request: Request):
        u = usuario_actual(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Las metas las fija la Dirección General.")
        f = await request.form()
        try:
            eid, anio = int(str(f.get("equipo_id") or "0")), int(str(f.get("anio") or "0"))
            with db._conexion() as con:
                metas_visitas.resolver(con, actor(u), True, eid, anio, str(f.get("eleccion") or ""))
        except ValueError:
            return volver(request, "/traslados/direccion#metas", "⚠ Faltan datos.")
        except ErrorDGPDH as e:
            return volver(request, "/traslados/direccion#metas", "⚠ " + e.mensaje)
        return volver(request, "/traslados/direccion#metas", "Listo: queda una sola meta de visitas para ese equipo.")

    # ------------------------------------------------------------ propuesta 57: horarios de ingreso por unidad
    def _ventanas_de(unidad_id):
        with db._conexion() as con:
            return srv_ventanas.listar(con, unidad_id)

    templates.env.globals["viajes_ventanas_unidad"] = _ventanas_de

    def _rendimiento(vid):  # propuesta 63
        import operacion as op
        try:
            return list(reversed(op.rendimiento_combustible(vid)))[:12]
        except Exception:
            return []

    templates.env.globals["viajes_rendimiento"] = _rendimiento

    @app.get("/unidades/{uid:int}/ventanas", response_class=HTMLResponse)
    def ventanas_unidad(request: Request, uid: int, bajas: str = ""):
        u = usuario_actual(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Los horarios de ingreso los carga la Administración (Dirección General).")
        unidad = db.obtener_unidad(uid)
        if not unidad:
            raise HTTPException(status_code=404, detail="La unidad no existe.")
        with db._conexion() as con:
            lista = srv_ventanas.listar(con, uid, incluir_bajas=bool(bajas))
        return pagina(request, "viajes_ventanas.html", unidad=unidad, lista=lista, bajas=bool(bajas), TIPOS=dom_ventanas.TIPOS,
                      DIAS=dom_ventanas.DIAS, hoy=date.today().isoformat())

    @app.post("/unidades/{uid:int}/ventanas")
    async def ventana_nueva(request: Request, uid: int):
        u = usuario_actual(request)
        f = await request.form()
        dias = [int(x) for x in (f.getlist("dias") if hasattr(f, "getlist") else []) if str(x).isdigit()]
        try:
            with db._conexion() as con:
                srv_ventanas.guardar(con, actor(u), auth.es_admin(u), uid, dias, str(f.get("desde") or ""), str(f.get("hasta") or ""),
                                     str(f.get("tipo") or "AUDIENCIAS"), str(f.get("vigente_desde") or ""), str(f.get("observacion") or ""))
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, f"/unidades/{uid}/ventanas", "⚠ " + e.mensaje)
        return volver(request, f"/unidades/{uid}/ventanas", "Horario de ingreso guardado.")

    @app.post("/unidades/ventanas/{vid:int}/baja")
    async def ventana_baja(request: Request, vid: int):
        u = usuario_actual(request)
        f = await request.form()
        try:
            with db._conexion() as con:
                uid = srv_ventanas.dar_de_baja(con, actor(u), auth.es_admin(u), vid, str(f.get("motivo") or ""))
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, "/unidades", "⚠ " + e.mensaje)
        return volver(request, f"/unidades/{uid}/ventanas", "Horario dado de baja (queda en el historial).")

    # ------------------------------------------------------------ propuesta 58: requisitos y habilidades
    def _guardar_etiquetas(request, tabla, columna, rid, valor, entidad):
        from dgpdh.dominio import habilidades as hab
        u = usuario_actual(request)
        nuevo = hab.texto(hab.etiquetas(valor))[:300] or None
        with db._conexion() as con:
            f = con.execute(f"SELECT {columna} FROM {tabla} WHERE id = ?", (rid,)).fetchone()
            if not f:
                raise HTTPException(status_code=404, detail="No existe.")
            con.execute(f"UPDATE {tabla} SET {columna} = ? WHERE id = ?", (nuevo, rid))
            au.registrar(con, accion=f"Cambio de {columna}", entidad=entidad, entidad_id=rid, antes={columna: f[0]}, despues={columna: nuevo},
                         actor=actor(u))
            if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'traslados_auditoria'").fetchone():
                con.execute("INSERT INTO traslados_auditoria (entidad, entidad_id, accion, anterior, nuevo, usuario, fecha) VALUES (?,?,?,?,?,?,?)",
                            (entidad, rid, columna.capitalize(), f[0], nuevo, u["nombre"], db._ahora()))
        return nuevo

    @app.post("/traslados/{viaje_id:int}/requisitos")
    async def viaje_requisitos(request: Request, viaje_id: int):
        u = usuario_actual(request)
        v = _viaje(viaje_id)
        if not tr.puede_editar_viaje(u, v):
            raise HTTPException(status_code=403, detail="Los requisitos los cargan quien pidió el viaje o la coordinación.")
        f = await request.form()
        _guardar_etiquetas(request, "viajes", "requisitos", viaje_id, str(f.get("requisitos") or ""), "viaje")
        return volver(request, f"/traslados/{viaje_id}", "Requisitos guardados: el chofer y el vehículo sugeridos los tienen en cuenta.")

    @app.post("/traslados/vehiculos/{vid:int}/habilidades")
    async def vehiculo_habilidades(request: Request, vid: int):
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")
        f = await request.form()
        _guardar_etiquetas(request, "vehiculos", "habilidades", vid, str(f.get("habilidades") or ""), "vehiculo")
        return volver(request, f"/traslados/vehiculos/{vid}", "Características del vehículo guardadas.")

    @app.post("/traslados/choferes/{cid:int}/habilidades")
    async def chofer_habilidades(request: Request, cid: int):
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")
        f = await request.form()
        _guardar_etiquetas(request, "choferes", "habilidades", cid, str(f.get("habilidades") or ""), "chofer")
        return volver(request, f"/traslados/choferes/{cid}", "Habilitaciones del chofer guardadas.")

    # ------------------------------------------------------------ propuesta 67: mis viajes en el calendario (.ics)
    @app.get("/traslados/mis-viajes.ics")
    def mis_viajes_ics(request: Request):
        from dgpdh.dominio import ics
        u = usuario_actual(request)
        hoy = date.today()
        desde, hasta = (hoy - timedelta(days=14)).isoformat(), (hoy + timedelta(days=90)).isoformat()
        mi_chofer = chofer_de(u) if u["rol"] == "CHOFER" else None
        if mi_chofer:
            viajes = [v for v in tr.listar_viajes(desde=desde, hasta=hasta) if v.get("chofer_id") == mi_chofer]
        else:
            viajes = tr.listar_viajes(desde=desde, hasta=hasta, usuario_id=u["id"])
        unidades = {x["id"]: x for x in db.listar_unidades(solo_activas=False)}
        eventos = []
        for v in viajes:
            un = unidades.get(v.get("unidad_id")) or {}
            lugar = ", ".join(x for x in (v["destino"], un.get("direccion"), un.get("ciudad")) if x)
            detalle = (f"Chofer: {v['chofer_nombre']}" if v.get("chofer_nombre") else "") + (f" · vehículo {v['vehiculo_dominio']}" if v.get("vehiculo_dominio") else "")
            eventos.append(ics.Evento(v["id"], v["fecha"], v.get("hora_salida"), v.get("hora_regreso"), v["destino"], lugar, v["estado"],
                                      int(v.get("reprogramaciones") or 0), tuple(p["nombre"] for p in v["participantes"]), detalle))
        texto = ics.calendario(eventos, "Mis viajes — Autos PPN")
        return Response(texto, media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": "attachment; filename=mis_viajes.ics"})

    # ------------------------------------------------------------ propuesta 70: vista por recurso (vehículos y choferes)
    @app.get("/traslados/recursos-dia", response_class=HTMLResponse)
    def recursos_dia(request: Request, fecha: str = "", ver: str = "vehiculo"):
        import operacion as op
        from dgpdh.dominio import recursos_dia as rd
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")
        try:
            f = date.fromisoformat(fecha) if fecha else date.today()
        except ValueError:
            f = date.today()
        fi = f.isoformat()
        ver = "chofer" if ver == "chofer" else "vehiculo"
        viajes = [v for v in tr.listar_viajes(desde=fi, hasta=fi) if v["estado"] in ("CONFIRMADO", "SOLICITADO") and v["modalidad"] == "CHOFER"]
        filas = []
        with db._conexion() as con:
            if ver == "vehiculo":
                recursos = [dict(x) for x in con.execute("SELECT * FROM vehiculos WHERE estado != 'BAJA' ORDER BY dominio")]
            else:
                recursos = [dict(x) for x in con.execute("SELECT * FROM choferes WHERE estado != 'BAJA' ORDER BY nombre")]
        reservas = op.reservas(fi, fi)
        for r in recursos:
            if ver == "vehiculo":
                fila = rd.Fila(r["id"], r["dominio"], f"{r.get('marca') or ''} {r.get('modelo') or ''} · {r['capacidad']} plazas".strip(),
                               f"/traslados/vehiculos/{r['id']}")
                if r["estado"] != "DISPONIBLE":
                    fila.bloques.append(rd.todo_el_dia("taller", tr.ETIQUETAS_ESTADO_VEHICULO.get(r["estado"], r["estado"]), fila.url))
                for x in reservas:
                    if x["vehiculo_id"] == r["id"]:
                        fila.bloques.append(rd.bloque(x.get("desde_hora") or "00:00", x.get("hasta_hora") or "23:59", "reserva", f"Reserva: {x['motivo']}"))
                propios = [v for v in viajes if v.get("vehiculo_id") == r["id"]]
            else:
                fila = rd.Fila(r["id"], r["nombre"], tr.ETIQUETAS_ESTADO_CHOFER.get(r["estado"], r["estado"]), f"/traslados/choferes/{r['id']}")
                lic = op.licencia_en(r["id"], fi)
                if lic or r["estado"] != "DISPONIBLE":
                    fila.bloques.append(rd.todo_el_dia("licencia", op.TIPOS_LICENCIA.get(lic["tipo"], "Licencia") if lic else
                                                       tr.ETIQUETAS_ESTADO_CHOFER.get(r["estado"], r["estado"])))
                for x in reservas:
                    if x.get("chofer_id") == r["id"]:
                        fila.bloques.append(rd.bloque(x.get("desde_hora") or "00:00", x.get("hasta_hora") or "23:59", "reserva", f"Reserva: {x['motivo']}"))
                propios = [v for v in viajes if v.get("chofer_id") == r["id"]]
            for v in propios:
                otro = (v.get("chofer_nombre") if ver == "vehiculo" else v.get("vehiculo_dominio")) or "—"
                fila.bloques.append(rd.bloque(v.get("hora_salida_real") or v.get("hora_salida"), v.get("hora_regreso_real") or v.get("hora_regreso"),
                                              "salida", f"{v['destino']} · {otro} · {v['con_transporte']} persona(s)", f"/traslados/{v['id']}"))
            filas.append(fila)
        pedidos = [v for v in viajes if v["estado"] == "SOLICITADO" and not v.get("chofer_id")]
        return pagina(request, "viajes_recursos_dia.html", filas=filas, ver=ver, fecha=fi, horas=rd.horas(), pedidos=pedidos,
                      anterior=(f - timedelta(days=1)).isoformat(), siguiente=(f + timedelta(days=1)).isoformat(), activa="recursos")

    # ------------------------------------------------------------ propuesta 60: control del vehículo antes de salir
    from dgpdh.servicios import checklist as srv_check

    def _control_de(desp_id):
        with db._conexion() as con:
            return {"items": srv_check.items(con), "ultimo": {c["item_id"]: c for c in srv_check.ultimo(con, desp_id)}}

    templates.env.globals["viajes_control_salida"] = _control_de
    templates.env.globals["viajes_resultados_control"] = srv_check.RESULTADOS

    @app.post("/traslados/desplazamiento/{desp_id:int}/control")
    async def control_salida(request: Request, desp_id: int):
        import flota
        u = usuario_actual(request)
        with db._conexion() as con:
            d = con.execute("SELECT chofer_id, vehiculo_id FROM desplazamientos WHERE id = ?", (desp_id,)).fetchone()
        if not d:
            raise HTTPException(status_code=404, detail="La salida no existe.")
        puede = tr.puede_coordinar(u) or (d["chofer_id"] is not None and d["chofer_id"] == chofer_de(u))
        if not puede:
            raise HTTPException(status_code=403, detail="El control lo cargan el chofer de la salida o la coordinación.")
        f = await request.form()
        destino = f"/traslados/desplazamiento/{desp_id}#control"
        with db._conexion() as con:
            ids = [int(str(i["id"])) for i in srv_check.items(con)]
        resp = [srv_check.Respuesta(i, str(f.get(f"r_{i}")), str(f.get(f"o_{i}") or "")) for i in ids if f.get(f"r_{i}")]
        try:
            with db._conexion() as con:
                no_aptos = srv_check.registrar(con, actor(u), True, desp_id, resp)
        except ErrorDGPDH as e:
            return volver(request, destino, "⚠ " + e.mensaje)
        for n in no_aptos:
            if not n["vehiculo_id"]:
                continue
            try:
                rid = flota.registrar(n["vehiculo_id"], "NOVEDAD", {"gravedad": "GRAVE", "detalle": f"Control antes de salir: {n['item']} no apto. {n['observacion']}"}, u)
                with db._conexion() as con:
                    srv_check.vincular_novedad(con, int(n["control_id"]), rid)
            except tr.ErrorTraslado:
                import logging
                logging.exception("No se pudo crear la novedad del control")
        if no_aptos:
            return volver(request, destino, f"⚠ Control guardado. {len(no_aptos)} ítem(s) no apto(s): se cargó la novedad mecánica y se avisó a la coordinación.")
        return volver(request, destino, "Control guardado.")

    @app.get("/traslados/checklist", response_class=HTMLResponse)
    def checklist_items(request: Request):
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")
        with db._conexion() as con:
            lista = srv_check.items(con, incluir_bajas=True)
        return pagina(request, "viajes_checklist.html", lista=lista)

    @app.post("/traslados/checklist")
    async def checklist_item_nuevo(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        try:
            with db._conexion() as con:
                if f.get("baja"):
                    srv_check.baja_item(con, actor(u), tr.puede_coordinar(u), int(str(f.get("baja"))))
                else:
                    srv_check.agregar_item(con, actor(u), tr.puede_coordinar(u), str(f.get("texto") or ""))
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, "/traslados/checklist", "⚠ " + e.mensaje)
        return volver(request, "/traslados/checklist", "Control actualizado.")

    # ------------------------------------------------------------ propuestas 86 y 88: balance trimestral y frecuencia por unidad
    from dgpdh.servicios import frecuencia_unidades as srv_frec
    from dgpdh.servicios import revision_trimestral as srv_rev

    def _equipo_metas(u, equipo_id):
        import metas
        if not metas.puede_ver(u):
            raise HTTPException(status_code=403, detail="No tenés acceso a Metas.")
        visibles = metas.equipos_visibles(u)
        if str(equipo_id or "").strip():
            e = next((x for x in visibles if str(x["id"]) == str(equipo_id)), None)
        else:
            e = visibles[0] if len(visibles) == 1 else None
        if not e:
            raise HTTPException(status_code=403, detail="No podés ver ese equipo.")
        return e

    def _coordina_equipo(u, e):
        return auth.es_admin(u) or (u["rol"] == "COORDINADOR" and u.get("equipo_id") == e["id"])

    def _anio_tri(anio, trimestre):
        hoy = date.today()
        a = int(anio) if str(anio).isdigit() else hoy.year
        t = int(trimestre) if str(trimestre) in ("1", "2", "3", "4") else max(1, (hoy.month - 1) // 3)
        return a, t

    @app.get("/metas/revision", response_class=HTMLResponse)
    def metas_revision(request: Request, equipo_id: str = "", anio: str = "", trimestre: str = ""):
        import metas
        u = usuario_actual(request)
        e = _equipo_metas(u, equipo_id)
        a, t = _anio_tri(anio, trimestre)
        seg = metas.seguimiento(a, [e])["equipos"][0]
        filas = [f for f in seg["filas"] if f["meta"]]
        with db._conexion() as con:
            vers, resp = srv_rev.versiones(con, e["id"], a, t), srv_rev.respuestas(con, e["id"], a, t)
        return pagina(request, "viajes_revision.html", e=e, anio=a, t=t, filas=filas, sugerido=srv_rev.sugerir([f["estado"] for f in filas]),
                      versiones=vers, respuestas=resp, COLORES=srv_rev.COLORES, puede=_coordina_equipo(u, e), es_dir=auth.es_admin(u))

    @app.post("/metas/revision")
    async def metas_revision_guardar(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        e = _equipo_metas(u, f.get("equipo_id"))
        a, t = _anio_tri(f.get("anio"), f.get("trimestre"))
        destino = f"/metas/revision?equipo_id={e['id']}&anio={a}&trimestre={t}"
        try:
            with db._conexion() as con:
                if f.get("respuesta") is not None:
                    srv_rev.responder(con, actor(u), auth.es_admin(u), e["id"], a, t, str(f.get("respuesta") or ""))
                else:
                    srv_rev.guardar(con, actor(u), _coordina_equipo(u, e), e["id"], a, t, str(f.get("color") or ""), str(f.get("sugerido") or ""),
                                    str(f.get("comentario") or ""), str(f.get("obstaculos") or ""), str(f.get("acciones") or ""))
        except ErrorDGPDH as ex:
            _http(ex)
            return volver(request, destino, "⚠ " + ex.mensaje)
        if f.get("respuesta") is None:
            destinos = [x["id"] for x in db.listar_usuarios() if x.get("activo") and auth.es_admin(x) and x["id"] != u["id"]]
            db.notificar_usuarios(destinos, f"Balance del {t}.º trimestre de {e['nombre']} cargado por {u['nombre']}.", destino)
        return volver(request, destino, "Guardado.")

    @app.get("/metas/frecuencias", response_class=HTMLResponse)
    def metas_frecuencias(request: Request, equipo_id: str = ""):
        u = usuario_actual(request)
        e = _equipo_metas(u, equipo_id)
        hoy = date.today()
        with db._conexion() as con:
            obst = ingreso_unidades.visitas_con_obstaculo(con, (hoy - timedelta(days=90)).isoformat(), hoy.isoformat(), hoy,
                                                          ve_obstaculizaciones(u, tiene_notas))
            cuenta = {}
            for uid, _ in obst:
                cuenta[uid] = cuenta.get(uid, 0) + 1
            est = srv_frec.estado(con, e["id"], e["nombre"], hoy, cuenta)
        return pagina(request, "viajes_frecuencias.html", e=e, est=est, FRECUENCIAS=srv_frec.FRECUENCIAS, RIESGOS=srv_frec.RIESGOS,
                      puede=_coordina_equipo(u, e))

    @app.post("/metas/frecuencias")
    async def metas_frecuencias_guardar(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        e = _equipo_metas(u, f.get("equipo_id"))
        valores = {}
        with db._conexion() as con:
            ids = [int(x[0]) for x in con.execute("SELECT unidad_id FROM equipo_unidades WHERE equipo_id = ?", (e["id"],))]
        for uid in ids:
            d, r = str(f.get(f"f_{uid}") or ""), str(f.get(f"r_{uid}") or "")
            valores[uid] = (int(d) if d.isdigit() else None, r or None)
        try:
            with db._conexion() as con:
                n = srv_frec.guardar(con, actor(u), _coordina_equipo(u, e), e["id"], valores)
        except ErrorDGPDH as ex:
            _http(ex)
            return volver(request, f"/metas/frecuencias?equipo_id={e['id']}", "⚠ " + ex.mensaje)
        return volver(request, f"/metas/frecuencias?equipo_id={e['id']}", f"Guardado ({n} cambio/s).")

    # ------------------------------------------------------------ Modificación 4: pasajeros externos
    from dgpdh.servicios import pasajeros_externos as srv_ext

    def puede_externos(u):
        return bool(u) and (auth.es_admin(u) or tr.puede_coordinar(u))

    def _externo(pid):
        with db._conexion() as con:
            return srv_ext.obtener(con, pid)

    def _opciones_externos():
        with db._conexion() as con:
            return [srv_ext.etiqueta(x) for x in srv_ext.listar(con)]

    templates.env.globals["viajes_externo"] = _externo
    templates.env.globals["viajes_externos_opciones"] = _opciones_externos
    templates.env.globals["viajes_puede_externos"] = puede_externos

    @app.get("/traslados/pasajeros-externos", response_class=HTMLResponse)
    def externos_lista(request: Request, q: str = "", bajas: str = "", editar: int = 0):
        u = usuario_actual(request)
        if not puede_externos(u):
            raise HTTPException(status_code=403, detail="Los pasajeros externos los administran la Dirección y la coordinación de traslados.")
        with db._conexion() as con:
            return pagina(request, "viajes_externos.html", lista=srv_ext.listar(con, bool(bajas), q), q=q, bajas=bool(bajas),
                          categorias=srv_ext.categorias(con), categorias_todas=srv_ext.categorias(con, True),
                          editando=srv_ext.obtener(con, editar) if editar else None)

    @app.post("/traslados/pasajeros-externos")
    async def externo_guardar(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        pid = int(str(f.get("id"))) if str(f.get("id") or "").isdigit() else None
        try:
            with db._conexion() as con:
                srv_ext.guardar(con, actor(u), puede_externos(u), {k: str(f.get(k) or "") for k in ("nombre", "apellido", "documento", "categoria_id", "institucion")}, pid)
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, "/traslados/pasajeros-externos" + (f"?editar={pid}" if pid else ""), "⚠ " + e.mensaje)
        return volver(request, "/traslados/pasajeros-externos", "Pasajero externo guardado.")

    @app.post("/traslados/pasajeros-externos/{pid:int}/activo")
    async def externo_activo(request: Request, pid: int):
        u = usuario_actual(request)
        f = await request.form()
        try:
            with db._conexion() as con:
                srv_ext.cambiar_activo(con, actor(u), puede_externos(u), pid, f.get("activo") == "1", str(f.get("motivo") or ""))
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, "/traslados/pasajeros-externos?bajas=1", "⚠ " + e.mensaje)
        return volver(request, "/traslados/pasajeros-externos?bajas=1", "Listo.")

    @app.post("/traslados/pasajeros-externos/categorias")
    async def externo_categoria(request: Request):
        u = usuario_actual(request)
        f = await request.form()
        try:
            with db._conexion() as con:
                if f.get("baja"):
                    srv_ext.baja_categoria(con, actor(u), puede_externos(u), int(str(f.get("baja"))))
                else:
                    srv_ext.guardar_categoria(con, actor(u), puede_externos(u), str(f.get("nombre") or ""))
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, "/traslados/pasajeros-externos#categorias", "⚠ " + e.mensaje)
        return volver(request, "/traslados/pasajeros-externos#categorias", "Categorías actualizadas.")

    @app.post("/traslados/{viaje_id:int}/participantes/{pid:int}/externo")
    async def participante_externo(request: Request, viaje_id: int, pid: int):
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Lo vincula la coordinación de traslados.")
        f = await request.form()
        try:
            with db._conexion() as con:
                ext = srv_ext.desde_etiqueta(con, str(f.get("externo") or ""))
                if not ext:
                    return volver(request, f"/traslados/{viaje_id}", "⚠ Elegí un pasajero externo de la lista (o escribí su DNI). Si no está, registralo en «Pasajeros externos».")
                nombre = srv_ext.vincular(con, actor(u), True, viaje_id, pid, int(str(ext["id"])))
        except ErrorDGPDH as e:
            _http(e)
            return volver(request, f"/traslados/{viaje_id}", "⚠ " + e.mensaje)
        return volver(request, f"/traslados/{viaje_id}", f"Vinculado: {nombre} ({ext['categoria']}). Ocupa lugar en el vehículo y no cuenta para las metas.")

    # ------------------------------------------------------------ Modificación 6: viajes unificados por unidad de destino
    from dgpdh.dominio import salidas as dom_salidas

    def _pedidos_unificados(viaje_id):
        with db._conexion() as con:
            return [f[0] for f in con.execute("SELECT id FROM viajes WHERE unificado_en = ? ORDER BY id", (viaje_id,))]

    def _salida_detalle(v):
        """Para la ficha: todas las paradas de la salida (o el viaje con pedidos unidos), una por unidad de destino."""
        otros = [tr.obtener_viaje(o["id"]) for o in v.get("misma_salida") or []]
        viajes = [v] + [o for o in otros if o]
        pars = dom_salidas.paradas(viajes, _pedidos_unificados)
        return pars if (len(pars) > 1 or any(len(p["pedidos"]) > 1 for p in pars)) else []

    templates.env.globals["viajes_agrupar_dia"] = dom_salidas.agrupar_dia
    templates.env.globals["viajes_salida_detalle"] = _salida_detalle

    # ------------------------------------------------------------ propuesta 74: tiempo de respuesta de la coordinación
    from dgpdh.dominio import respuesta as dom_resp

    def _dt(iso):
        try:
            d = datetime.fromisoformat(str(iso))
        except (TypeError, ValueError):
            return None
        return d.astimezone(fechas.zona()) if d.tzinfo else d.replace(tzinfo=fechas.zona())

    def semaforo_pedido(v):
        ahora = ahora_local()
        creado = _dt(v.get("creado_en"))
        if not creado:
            return None
        fer = tr.feriados_entre(creado.date() - timedelta(days=1), ahora.date())
        espera = dom_resp.horas_habiles(creado, ahora, fer)
        salida = v.get("hora_salida") or "08:00"
        viaje_dt = _dt(f"{v['fecha']}T{salida}:00")
        hasta = (viaje_dt - ahora).total_seconds() / 3600 if viaje_dt else None
        nivel, texto = dom_resp.semaforo(espera, hasta)
        return {"nivel": nivel, "texto": texto}

    def tiempos_asignacion(desde, hasta):
        """Mediana de horas hábiles entre el pedido y la primera asignación, por mes (para la Dirección)."""
        with db._conexion() as con:
            filas = con.execute("""SELECT v.creado_en, MIN(a.fecha) AS asignado FROM viajes v
                                   JOIN traslados_auditoria a ON a.entidad = 'viaje' AND a.entidad_id = v.id AND a.accion LIKE 'Asignación%'
                                   WHERE v.modalidad = 'CHOFER' AND COALESCE(v.origen, '') != 'IMPORTADO' GROUP BY v.id
                                   HAVING asignado >= ? AND asignado <= ?""", (desde, hasta + "T23:59:59")).fetchall()
        fer = tr.feriados_entre(date.fromisoformat(desde) - timedelta(days=60), date.fromisoformat(hasta))
        pares = []
        for f in filas:
            a, b = _dt(f["creado_en"]), _dt(f["asignado"])
            if a and b:
                pares.append((b.strftime("%Y-%m"), dom_resp.horas_habiles(a, b, fer)))
        return dom_resp.mediana_por_mes(pares)

    templates.env.globals["viajes_semaforo_pedido"] = semaforo_pedido
    templates.env.globals["viajes_tiempos_asignacion"] = tiempos_asignacion

    # ------------------------------------------------------------ propuesta 81: salidas con lugar en los próximos días
    def salidas_con_lugar(u, dias=7):
        if not u or u.get("rol") == "CHOFER":
            return []
        hoy = date.today()
        out, vistos = [], set()
        for v in tr.listar_viajes(desde=hoy.isoformat(), hasta=(hoy + timedelta(days=dias)).isoformat(), estado="CONFIRMADO", modalidad="CHOFER", circuito="AMBA"):
            libres = v.get("plazas_libres")
            if not v.get("vehiculo_id") or libres is None or libres <= 0 or v["id"] in vistos:
                continue
            if any(p.get("usuario_id") == u["id"] for p in v["participantes"]):
                continue
            vistos.update([v["id"]] + [o["id"] for o in v.get("misma_salida") or []])
            out.append(v)
        return out[:8]

    templates.env.globals["viajes_salidas_con_lugar"] = salidas_con_lugar

    @app.post("/traslados/{viaje_id:int}/sumarme")
    async def sumarme(request: Request, viaje_id: int):
        import operacion as op
        u = usuario_actual(request)
        if u["rol"] == "CHOFER":
            raise HTTPException(status_code=403, detail="Los choferes no se anotan en viajes.")
        v = _viaje(viaje_id)
        if v["estado"] != "CONFIRMADO" or not v.get("plazas_libres") or v["plazas_libres"] <= 0:
            return volver(request, "/traslados", "⚠ Esa salida ya no tiene lugar.")
        try:
            op.anotar_en_espera(viaje_id, u)
        except tr.ErrorTraslado as e:
            return volver(request, "/traslados", "⚠ " + str(e))
        db.notificar_usuarios(tr.usuarios_coordinadores(), f"{u['nombre']} quiere sumarse a la salida del {v['fecha'][8:10]}/{v['fecha'][5:7]} a {v['destino']} "
                              f"({v['plazas_libres']} lugar/es libre/s).", f"/traslados/{viaje_id}")
        return volver(request, "/traslados", "Listo: la coordinación confirma si te sumás (quedaste en la lista de ese viaje).")

    # ------------------------------------------------------------ perfiles de metas: carga por equipo y por unidad/complejo
    def _dims_de(e):
        import metas
        lista = [i for i in metas.indicadores_de(e) if metas.dimension_de(i) in ("equipo", "unidad") and i["fuente"] != "viajes"]
        return [i for i in lista if metas.dimension_de(i) == "equipo"], [i for i in lista if metas.dimension_de(i) == "unidad"]

    @app.get("/metas/carga-equipo", response_class=HTMLResponse)
    def metas_carga_equipo(request: Request, equipo_id: str = "", anio: str = "", periodo: str = "", usar_propuesta: str = ""):
        import metas
        u = usuario_actual(request)
        e = _equipo_metas(u, equipo_id)
        a = int(anio) if str(anio).isdigit() else date.today().year
        try:
            p = metas.validar_periodo(periodo) if periodo else metas.periodo_actual()
        except ValueError:
            p = metas.periodo_actual()
        de_equipo, de_unidad = _dims_de(e)
        cargados = [f for f in metas.cargado_dim(a, e["id"]) if f["periodo"] == p]
        valores = {(f["indicador"], f["dimension"]): f["cantidad"] for f in cargados}
        propuesta = metas.propuesta_notas(e["id"], a, p) if any(i.get("propuesta") for i in de_unidad) else {}
        filas = {}
        with db._conexion() as con:
            for uid, nombre in con.execute("""SELECT u.id, u.nombre FROM equipo_unidades eu JOIN unidades u ON u.id = eu.unidad_id
                                              WHERE eu.equipo_id = ? ORDER BY eu.orden, u.nombre""", (e["id"],)):
                filas[metas.clave_unidad(uid)] = nombre
        for f in cargados:
            if f["dimension"]:
                filas.setdefault(f["dimension"], f["etiqueta"] or f["dimension"])
        for (_, dim), (nombre, _) in propuesta.items():
            filas.setdefault(dim, nombre)
        if usar_propuesta:  # la persona pidió completar con lo propuesto: lo ve en el formulario y decide si lo guarda
            for k, (_, n) in propuesta.items():
                valores.setdefault(k, n)
        return pagina(request, "viajes_metas_dim.html", e=e, anio=a, periodo=p, nombre_periodo=metas.nombre_periodo(p, a), de_equipo=de_equipo,
                      de_unidad=de_unidad, valores=valores, propuesta=propuesta, filas=list(filas.items()), usar_propuesta=bool(usar_propuesta),
                      puede=_coordina_equipo(u, e), MESES=metas.MESES)

    @app.post("/metas/carga-equipo")
    async def metas_carga_equipo_guardar(request: Request):
        import metas
        u = usuario_actual(request)
        f = await request.form()
        e = _equipo_metas(u, f.get("equipo_id"))
        if not _coordina_equipo(u, e):
            raise HTTPException(status_code=403, detail="Lo carga la coordinación del equipo (o la Dirección).")
        a = int(str(f.get("anio"))) if str(f.get("anio") or "").isdigit() else date.today().year
        p = str(f.get("periodo") or "")
        de_equipo, de_unidad = _dims_de(e)
        valores, malos = {}, 0

        def num(v):
            t = str(v or "").strip()
            return None if t == "" else (int(t) if t.isdigit() else -1)
        for i in de_equipo:
            n = num(f.get(f"e_{i['clave']}"))
            if n is not None:
                valores[(i["clave"], "", None)] = n
        dims = [str(x) for x in (f.getlist("dim") if hasattr(f, "getlist") else [])]
        for k in range(3):  # filas nuevas: unidad escrita a mano
            texto = str(f.get(f"nueva_{k}") or "").strip()
            if texto:
                dims.append(metas.clave_unidad(texto=texto))
        for dim in dims:
            etiqueta = str(f.get(f"etq_{dim}") or "") or (dim[2:] if dim.startswith("t:") else dim)
            for i in de_unidad:
                n = num(f.get(f"u_{i['clave']}_{dim}"))
                if n is None and dim.startswith("t:"):
                    k = next((k for k in range(3) if metas.clave_unidad(texto=str(f.get(f"nueva_{k}") or "")) == dim), None)
                    n = num(f.get(f"nu_{i['clave']}_{k}")) if k is not None else None
                if n is not None:
                    valores[(i["clave"], dim, etiqueta)] = n
        malos = sum(1 for v in valores.values() if v < 0)
        valores = {k: v for k, v in valores.items() if v >= 0}
        destino = f"/metas/carga-equipo?equipo_id={e['id']}&anio={a}&periodo={p}"
        try:
            n = metas.guardar_dim(e["id"], a, p, valores, u)
        except ValueError as ex:
            return volver(request, destino, "⚠ " + str(ex))
        return volver(request, destino, f"Guardado ({n} valor/es)." + (f" ⚠ {malos} valor(es) no eran números y no se guardaron." if malos else ""))

    @app.post("/admin/equipos/{equipo_id:int}/perfil-metas")
    async def equipo_perfil_metas(request: Request, equipo_id: int):
        import metas
        u = usuario_actual(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="El perfil de metas lo elige la Dirección.")
        f = await request.form()
        try:
            cambio = metas.guardar_perfil(equipo_id, str(f.get("perfil") or ""), u)
        except ValueError as e:
            return volver(request, f"/admin/equipos/listado#equipo-{equipo_id}", "⚠ " + str(e))
        return volver(request, f"/admin/equipos/listado#equipo-{equipo_id}", "Perfil de metas guardado." if cambio else "El equipo ya tenía ese perfil.")

    # ------------------------------------------------------------ importación de las planillas de los equipos de salud
    def _unidad_por_nombre(texto):
        """«CPF I» → la unidad del catálogo cuyo nombre o sigla entre guiones coincide; si no, None (queda el texto)."""
        from dgpdh.dominio import planillas_salud as ps
        t = ps.norm(texto)
        if not t:
            return None
        for un in db.listar_unidades(solo_activas=False):
            n = ps.norm(un["nombre"])
            sigla = ps.norm(un["nombre"].rsplit("-", 2)[1]) if un["nombre"].count("-") >= 2 else ""
            if t in (n, sigla):
                return un
        return None

    def _gente_por_planilla(e, nombres):
        import metas
        from dgpdh.dominio import planillas_salud as ps
        gente = metas.integrantes(e["id"])
        out = {}
        for n in nombres:
            k = ps.norm(n)
            cand = [x for x in gente if k and k in ps.norm(x["nombre"]).split()] or [x for x in gente if k and k in ps.norm(x["nombre"])]
            out[n] = cand[0] if len(cand) == 1 else None
        return out

    def _previa_salud(e, lec):
        import metas
        validas = {i["clave"]: i for i in metas.indicadores_de(e)}
        personas = _gente_por_planilla(e, sorted({p[0] for p in lec.persona}))
        equipo = [x for x in lec.equipo if x[1] in validas and validas[x[1]]["fuente"] != "viajes"]
        control = list(lec.control) + [f"{validas[x[1]]['nombre']} ({x[0]}): la planilla dice {x[2]} (en el sistema sale de los viajes)"
                                       for x in lec.equipo if x[1] in validas and validas[x[1]]["fuente"] == "viajes"]
        fuera = sorted({x[1] for x in lec.equipo + [(u[0], u[1]) for u in lec.unidad]} - set(validas))
        unidades = {}
        for _, _, nombre, _ in lec.unidad:
            un = _unidad_por_nombre(nombre)
            unidades[nombre] = {"clave": metas.clave_unidad(un["id"]) if un else metas.clave_unidad(texto=nombre), "catalogo": un["nombre"] if un else None}
        return {"personas": personas, "equipo": equipo, "unidad": [x for x in lec.unidad if x[1] in validas], "control": control,
                "fuera_de_perfil": fuera, "unidades": unidades}

    @app.get("/metas/importar-salud", response_class=HTMLResponse)
    def metas_importar_salud(request: Request, equipo_id: str = ""):
        import metas
        u = usuario_actual(request)
        if not metas.puede_ver(u):
            raise HTTPException(status_code=403, detail="No tenés acceso a Metas.")
        equipos = [x for x in metas.equipos_visibles(u) if metas.perfil_de(x) != "general" and _coordina_equipo(u, x)]
        return pagina(request, "viajes_importar_salud.html", equipos=equipos, equipo_id=equipo_id, previa=None, anio=date.today().year)

    @app.post("/metas/importar-salud", response_class=HTMLResponse)
    async def metas_importar_salud_post(request: Request):
        import json

        import metas
        import openpyxl
        from dgpdh.dominio import planillas_salud as ps
        u = usuario_actual(request)
        f = await request.form()
        e = _equipo_metas(u, f.get("equipo_id"))
        if not _coordina_equipo(u, e):
            raise HTTPException(status_code=403, detail="Lo importa la coordinación del equipo (o la Dirección).")
        anio = int(str(f.get("anio"))) if str(f.get("anio") or "").isdigit() else date.today().year
        perfil = metas.perfil_de(e)
        if f.get("datos"):  # confirmación: se aplica lo que la persona vio en la vista previa
            try:
                d = json.loads(str(f.get("datos")))
            except ValueError:
                return volver(request, "/metas/importar-salud", "⚠ No se pudo leer la vista previa: volvé a subir la planilla.")
            origen = "planilla " + str(d.get("archivo") or "")[:80]
            n = 0
            por_periodo = {}
            for nombre, periodo, clave, cant in d.get("persona", []):
                uid = (d.get("vinculos") or {}).get(nombre)
                if uid:
                    por_periodo.setdefault(periodo, {})[(int(uid), clave)] = int(cant)
            for periodo, vals in por_periodo.items():
                try:
                    n += metas.guardar_carga(e["id"], d["anio"], periodo, vals, u)
                except ValueError as ex:
                    return volver(request, f"/metas?anio={d['anio']}&equipo_id={e['id']}", f"⚠ {ex}")
            dims = {}
            for periodo, clave, cant in d.get("equipo", []):
                dims.setdefault(periodo, {})[(clave, "", None)] = int(cant)
            for periodo, clave, nombre, cant in d.get("unidad", []):
                k = d["unidades"][nombre]["clave"]
                dims.setdefault(periodo, {})[(clave, k, d["unidades"][nombre]["catalogo"] or nombre)] = int(cant)
            for periodo, vals in dims.items():
                n += metas.guardar_dim(e["id"], d["anio"], periodo, vals, u, origen=origen)
            return volver(request, f"/metas?anio={d['anio']}&equipo_id={e['id']}", f"Planilla importada: {n} valor(es) cargado(s) o actualizado(s).")
        archivo = f.get("archivo")
        contenido = await _leer_archivo(archivo, 5 * 1024 * 1024)
        try:
            libro = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True)
            filas = [tuple(r) for r in libro.worksheets[0].iter_rows(values_only=True)]
        except Exception:
            return volver(request, f"/metas/importar-salud?equipo_id={e['id']}", "⚠ No pude abrir el archivo: tiene que ser una planilla de Excel (.xlsx).")
        lec = ps.leer(filas, anio)
        if lec.formato != perfil:
            return volver(request, f"/metas/importar-salud?equipo_id={e['id']}",
                          f"⚠ La planilla es de «{lec.formato.replace('_', ' ')}» y el equipo usa el perfil «{metas.PERFILES[perfil]['nombre']}».")
        previa = _previa_salud(e, lec)
        datos = {"anio": lec.anio or anio, "archivo": getattr(archivo, "filename", ""), "persona": lec.persona, "equipo": previa["equipo"],
                 "unidad": previa["unidad"], "unidades": previa["unidades"],
                 "vinculos": {k: v["id"] for k, v in previa["personas"].items() if v}}
        return pagina(request, "viajes_importar_salud.html", equipos=[e], equipo_id=str(e["id"]), previa=previa, lec=lec, e=e,
                      datos=json.dumps(datos, ensure_ascii=False), anio=lec.anio or anio)

    # ------------------------------------------------------------ propuestas 95 y 97: publicar metas y calidad de datos
    @app.get("/metas/publicar.xlsx")
    def metas_publicar(request: Request, anio: str = ""):
        """Propuesta 95: metas por equipo y trimestre para el informe anual, sin personas y con el mínimo publicable de 3."""
        import indicadores as ind
        import metas
        import openpyxl
        from openpyxl.styles import Font
        u = usuario_actual(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="La exportación para publicar es de la Dirección.")
        a = int(anio) if str(anio).isdigit() else date.today().year
        seg = metas.seguimiento(a, metas.equipos_con_integrantes())
        libro = openpyxl.Workbook()
        h = libro.active
        h.title = f"Metas {a}"
        h.append([f"Metas de gestión por equipo — {a} (para publicar)"])
        h.append([f"Sin datos de personas. Las celdas con 1 o 2 casos se muestran como «menos de {ind.MINIMO_PUBLICABLE}» (secreto estadístico)."])
        h.append(["Equipo", "Indicador", "Meta anual", "T1", "T2", "T3", "T4", "Total"])
        for c in h[3]:
            c.font = Font(bold=True)
        for x in seg["equipos"]:
            for f in x["filas"]:
                if not f["meta"] and not f["hecho"]:
                    continue
                h.append([neutralizar(x["equipo"]["nombre"]), f["nombre"], f["meta"] or None] + [ind._publicable(v) for v in f["trimestres"]]
                         + [ind._publicable(f["hecho"])])
        h.column_dimensions["A"].width, h.column_dimensions["B"].width = 34, 50
        buf = io.BytesIO()
        libro.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=metas_{a}_para_publicar.xlsx"})

    @app.get("/metas/calidad", response_class=HTMLResponse)
    def metas_calidad(request: Request, anio: str = ""):
        """Propuesta 97: Administración → Calidad de datos de metas."""
        from dgpdh.servicios import metas_calidad as mcal
        u = usuario_actual(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Esta sección es para la Dirección.")
        a = int(anio) if str(anio).isdigit() else date.today().year
        with db._conexion() as con:
            ctrl = mcal.controles(con, a, date.today())
        return pagina(request, "viajes_metas_calidad.html", controles=ctrl, anio=a)

    # ------------------------------------------------------------ propuesta 66: disponibilidad de la flota
    @app.get("/traslados/flota/disponibilidad", response_class=HTMLResponse)
    def flota_disponibilidad(request: Request, anio: str = ""):
        from dgpdh.dominio import disponibilidad as dd
        u = usuario_actual(request)
        if not puede_tablero(u):
            raise HTTPException(status_code=403, detail="Es para la coordinación de traslados y la Dirección.")
        hoy = date.today()
        a = int(anio) if str(anio).isdigit() else hoy.year
        desde, hasta = date(a, 1, 1), min(date(a, 12, 31), hoy)
        filas, total = [], {}
        with db._conexion() as con:
            vehiculos = [dict(x) for x in con.execute("SELECT id, dominio, estado, creado_en FROM vehiculos ORDER BY dominio")]
            historial = {}
            for vid, anterior, nuevo, fecha in con.execute(
                    "SELECT entidad_id, anterior, nuevo, fecha FROM traslados_auditoria WHERE entidad = 'vehiculo' AND accion = 'Estado' ORDER BY id"):
                historial.setdefault(int(vid), []).append((anterior, nuevo, fecha))
            sin_vehiculo = {}
            for fecha, n in con.execute("""SELECT substr(fecha, 1, 7), COUNT(*) FROM viajes WHERE modalidad = 'CHOFER' AND fecha BETWEEN ? AND ?
                                           AND ((estado = 'SOLICITADO' AND fecha < ?) OR (estado = 'CANCELADO' AND motivo_cancelacion LIKE 'Vehículo o chofer no disponible%'))
                                           GROUP BY substr(fecha, 1, 7)""", (desde.isoformat(), hasta.isoformat(), hoy.isoformat())):
                sin_vehiculo[fecha] = n
        meses = [f"{a}-{m:02d}" for m in range(1, (hasta.month if a == hoy.year else 12) + 1)] if hasta >= desde else []
        for v in vehiculos:
            alta = _dt(v["creado_en"]).date() if _dt(v["creado_en"]) else desde
            h = historial.get(v["id"], [])
            inicial = (dd.estado_de_texto(h[0][0]) if h else None) or v["estado"]
            cambios = [(_dt(f).date(), e) for _, n, f in h if _dt(f) and (e := dd.estado_de_texto(n))]
            if hasta < desde or alta > hasta:
                continue
            dias = dd.dias_por_mes(alta, inicial, cambios, desde, hasta)
            for m, x in dias.items():
                t = total.setdefault(m, {})
                for e, n in x.items():
                    t[e] = t.get(e, 0) + n
            filas.append({"vehiculo": v, "meses": {m: dd.pct_disponible(x) for m, x in dias.items()}, "dias": dias,
                          "anual": dd.pct_disponible({e: sum(x.get(e, 0) for x in dias.values()) for e in dd.ESTADOS})})
        return pagina(request, "viajes_disponibilidad.html", filas=filas, meses=meses, total={m: dd.pct_disponible(x) for m, x in total.items()},
                      dias_total=total, sin_vehiculo=sin_vehiculo, anio=a, activa="flota")

    # ------------------------------------------------------------ propuesta 62: costo por unidad, equipo y trimestre
    def _rango(desde, hasta, dias=90):
        hoy = date.today()
        try:
            d = date.fromisoformat(desde) if desde else hoy - timedelta(days=dias)
            h = date.fromisoformat(hasta) if hasta else hoy
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Las fechas no son válidas.") from e
        return (d, h) if d <= h else (h, d)

    def _costos(desde, hasta):
        from dgpdh.dominio import costos as dc
        from dgpdh.dominio import flota_control as fc
        o = opciones_viajes.leer()
        salidas = []
        with db._conexion() as con:
            ds = con.execute("SELECT id, fecha, importe FROM desplazamientos WHERE importe > 0 AND fecha BETWEEN ? AND ?", (desde, hasta)).fetchall()
            for d in ds:
                paradas = []
                for vid, nombre, lat, lng, destino in con.execute(
                        """SELECT v.id, u.nombre, u.lat, u.lng, v.destino_otro FROM viajes v LEFT JOIN unidades u ON u.id = v.unidad_id
                           WHERE v.desplazamiento_id = ? AND v.estado = 'CONFIRMADO' ORDER BY v.orden_parada, v.id""", (d["id"],)):
                    eqs = tuple(sorted({(f[0] or "").strip() for f in con.execute("SELECT equipo_nombre FROM viaje_participantes WHERE viaje_id = ?", (vid,))} - {""}))
                    km = fc.haversine_km(o.sede_lat, o.sede_lng, float(lat), float(lng)) if None not in (o.sede_lat, o.sede_lng, lat, lng) else None
                    paradas.append(dc.Parada(nombre or destino or "—", eqs, km))
                salidas.append((d["fecha"], float(d["importe"]), paradas))
        return dc.acumular(salidas), len(salidas)

    @app.get("/traslados/costos", response_class=HTMLResponse)
    def costos(request: Request, desde: str = "", hasta: str = ""):
        u = usuario_actual(request)
        if not puede_tablero(u):
            raise HTTPException(status_code=403, detail="Es para la coordinación de traslados y la Dirección.")
        d, h = _rango(desde, hasta, 365)
        c, n = _costos(d.isoformat(), h.isoformat())
        return pagina(request, "viajes_costos.html", c=c, n=n, desde=d.isoformat(), hasta=h.isoformat(), activa="flota")

    @app.get("/traslados/costos.xlsx")
    def costos_excel(request: Request, desde: str = "", hasta: str = ""):
        import openpyxl
        u = usuario_actual(request)
        if not puede_tablero(u):
            raise HTTPException(status_code=403, detail="Es para la coordinación de traslados y la Dirección.")
        d, h = _rango(desde, hasta, 365)
        c, _ = _costos(d.isoformat(), h.isoformat())
        libro = openpyxl.Workbook()
        libro.remove(libro.active)
        for clave, titulo in (("unidad", "Por unidad"), ("equipo", "Por equipo"), ("trimestre", "Por trimestre")):
            hj = libro.create_sheet(titulo)
            hj.append([titulo[4:].capitalize(), "Importe ($)"])
            for k, v in sorted(c[clave].items(), key=lambda kv: -kv[1] if clave != "trimestre" else 0):
                hj.append([neutralizar(k), v])
        buf = io.BytesIO()
        libro.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=costos_{d}_{h}.xlsx"})

    # ------------------------------------------------------------ propuesta 78: informe de recorridos por vehículo o chofer
    def _recorridos(vehiculo_id, chofer_id, desde, hasta):
        import operacion as op
        from dgpdh.dominio import habilidades as hab
        filtro, args = ("vehiculo_id = ?", [vehiculo_id]) if vehiculo_id else ("chofer_id = ?", [chofer_id])
        with db._conexion() as con:
            ids = [f[0] for f in con.execute(f"SELECT id FROM desplazamientos WHERE {filtro} AND fecha BETWEEN ? AND ? ORDER BY fecha, id",
                                             args + [desde, hasta])]
        filas, tot = [], {"salidas": 0, "km": 0, "ruta_min": 0, "unidad_min": 0, "novedades": 0}
        for i in ids:
            d = op.obtener_desplazamiento(i)
            if not d or not d["paradas"]:
                continue
            km = (d["km_regreso"] - d["km_salida"]) if d.get("km_regreso") is not None and d.get("km_salida") is not None else None
            s, r = hab.minutos(d.get("salida_real")), hab.minutos(d.get("regreso_real"))
            ruta = (r - s) if s is not None and r is not None and r > s else None
            paradas = []
            for p in d["paradas"]:
                a, z = hab.minutos(p.get("ingreso_unidad")), hab.minutos(p.get("salida_unidad"))
                en_unidad = (z - a) if a is not None and z is not None and z >= a else None
                paradas.append({"viaje": p, "en_unidad": en_unidad, "novedades": [n["etiqueta"] for n in p["novedades"]]})
                tot["unidad_min"] += en_unidad or 0
                tot["novedades"] += len(p["novedades"])
            tot["salidas"] += 1
            tot["km"] += km or 0
            tot["ruta_min"] += ruta or 0
            filas.append({"d": d, "km": km, "ruta": ruta, "paradas": paradas})
        return filas, tot

    @app.get("/traslados/planificar/comparacion", response_class=HTMLResponse)
    def propuesto_vs_asignado(request: Request, lunes: str = ""):
        """Propuesta 77: al cerrar la semana, lo que propuso el planificador frente a lo que se asignó."""
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")
        from dgpdh.servicios import propuesto_asignado
        hoy = date.today()
        try:
            base = tr.lunes_de(date.fromisoformat(lunes)) if lunes else tr.lunes_de(hoy - timedelta(days=7))
        except ValueError:
            base = tr.lunes_de(hoy - timedelta(days=7))
        with db._conexion() as con:
            r = propuesto_asignado.cuadro(con, base)
        import operacion as op
        return pagina(request, "viajes_propuesto_asignado.html", r=r, lunes=base.isoformat(), radio=op.RADIO_KM_AGRUPAR,
                      anterior=(base - timedelta(days=7)).isoformat(), siguiente=(base + timedelta(days=7)).isoformat())

    @app.get("/traslados/recorridos", response_class=HTMLResponse)
    def recorridos(request: Request, vehiculo_id: str = "", chofer_id: str = "", desde: str = "", hasta: str = "", formato: str = ""):
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")
        d, h = _rango(desde, hasta, 31)
        vid = int(vehiculo_id) if vehiculo_id.isdigit() else None
        cid = int(chofer_id) if chofer_id.isdigit() else None
        filas, tot = _recorridos(vid, cid, d.isoformat(), h.isoformat()) if (vid or cid) else ([], None)
        if formato == "xlsx" and (vid or cid):
            import openpyxl
            libro = openpyxl.Workbook()
            hj = libro.active
            hj.append(["Fecha", "Vehículo", "Chofer", "Parada", "Llegada", "Ingreso", "Salida de la unidad", "Min. en la unidad", "Km de la salida",
                       "Min. en ruta", "Novedades"])
            for f in filas:
                for k, p in enumerate(f["paradas"]):
                    v = p["viaje"]
                    hj.append([f["d"]["fecha"], f["d"].get("dominio"), f["d"].get("chofer_nombre"), neutralizar(v["destino"]), v.get("llegada_unidad"),
                               v.get("ingreso_unidad"), v.get("salida_unidad"), p["en_unidad"], f["km"] if k == 0 else None, f["ruta"] if k == 0 else None,
                               ", ".join(p["novedades"])])
            hj.append([])
            hj.append(["Totales", None, None, f"{tot['salidas']} salida(s)", None, None, None, tot["unidad_min"], tot["km"], tot["ruta_min"], tot["novedades"]])
            buf = io.BytesIO()
            libro.save(buf)
            return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f"attachment; filename=recorridos_{d}_{h}.xlsx"})
        return pagina(request, "viajes_recorridos.html", filas=filas, tot=tot, desde=d.isoformat(), hasta=h.isoformat(), vehiculo_id=vid, chofer_id=cid,
                      vehiculos=tr.listar_vehiculos(True), choferes=tr.listar_choferes(), activa="flota")

    # ------------------------------------------------------------ propuestas 55 y 54: orden sugerido de paradas (OSRM local optativo)
    def orden_sugerido(d):
        from dgpdh.dominio import ruteo
        from dgpdh.servicios import distancias
        o = opciones_viajes.leer()
        paradas = [p for p in d.get("paradas") or [] if p.get("estado") == "CONFIRMADO"]
        if len(paradas) < 2:
            return None
        coords = {}
        with db._conexion() as con:
            for p in paradas:
                f = con.execute("SELECT lat, lng FROM unidades WHERE id = ?", (p.get("unidad_id"),)).fetchone() if p.get("unidad_id") else None
                if not f or f["lat"] is None or f["lng"] is None:
                    return {"faltan": True}
                coords[p["id"]] = (float(f["lat"]), float(f["lng"]))
        origen = 0 if o.sede_lat is not None and o.sede_lng is not None else None
        if origen is not None:
            coords[0] = (float(o.sede_lat), float(o.sede_lng))
        def dist(a, b):
            return distancias.ruta([coords[a], coords[b]])[0]
        actual = [p["id"] for p in paradas]
        sugerido = ruteo.sugerir(actual, dist, origen)
        fuente = distancias.ruta([coords[actual[0]], coords[actual[1]]])[2]
        nombres = {p["id"]: p["destino"] for p in paradas}
        return {"actual": [(i, nombres[i]) for i in actual], "sugerido": [(i, nombres[i]) for i in sugerido],
                "km_actual": round(ruteo.largo(actual, dist, origen), 1), "km_sugerido": round(ruteo.largo(sugerido, dist, origen), 1),
                "fuente": fuente, "con_sede": origen is not None, "distinto": sugerido != actual}

    templates.env.globals["viajes_orden_sugerido"] = orden_sugerido

    @app.post("/traslados/desplazamiento/{desp_id:int}/orden")
    async def aplicar_orden(request: Request, desp_id: int):
        u = usuario_actual(request)
        if not tr.puede_coordinar(u):
            raise HTTPException(status_code=403, detail="Solo la coordinación de traslados puede hacer esto.")
        f = await request.form()
        ids = [int(x) for x in str(f.get("orden") or "").split(",") if x.strip().isdigit()]
        with db._conexion() as con:
            actuales = [r[0] for r in con.execute("SELECT id FROM viajes WHERE desplazamiento_id = ? ORDER BY orden_parada, id", (desp_id,))]
            if sorted(ids) != sorted(actuales) or not ids:
                return volver(request, f"/traslados/desplazamiento/{desp_id}", "⚠ El recorrido cambió: volvé a pedir la sugerencia.")
            for k, vid in enumerate(ids, 1):
                con.execute("UPDATE viajes SET orden_parada = ? WHERE id = ?", (k, vid))
            au.registrar(con, accion="Orden de paradas cambiado", entidad="desplazamiento", entidad_id=desp_id, antes={"orden": actuales},
                         despues={"orden": ids}, actor=actor(u), origen="sugerencia")
            con.execute("INSERT INTO traslados_auditoria (entidad, entidad_id, accion, anterior, nuevo, usuario, fecha) VALUES ('desplazamiento', ?, ?, ?, ?, ?, ?)",
                        (desp_id, "Orden de paradas (sugerido)", ", ".join(f"#{i}" for i in actuales), ", ".join(f"#{i}" for i in ids), u["nombre"], db._ahora()))
        return volver(request, f"/traslados/desplazamiento/{desp_id}", "Orden de paradas actualizado.")

    # ------------------------------------------------------------ propuesta 76: datos para decidir cuando faltan vehículos
    def puntaje_revision(v):
        """Para cada pedido con chofer que compite ese día: urgencia declarada, días desde la última visita del equipo a la
        unidad, si el equipo está atrasado en su meta de visitas y si es inmediato. Es solo información: decide la Dirección."""
        import metas
        hoy = date.today()
        compiten = [o for o in tr.listar_viajes(desde=v["fecha"], hasta=v["fecha"], modalidad="CHOFER")
                    if o["estado"] == "SOLICITADO" or o["id"] == v["id"]]
        equipos = {e["nombre"].strip().lower(): e for e in db.listar_equipos()}
        estado_visitas = {}
        try:
            with metas.cache_de_pedido():
                for x in metas.seguimiento(hoy.year, list(equipos.values()))["equipos"]:
                    f = next((r for r in x["filas"] if r["clave"] == "visitas_equipo"), None)
                    if f:
                        estado_visitas[x["equipo"]["nombre"].strip().lower()] = f["estado"]
        except Exception:
            import logging
            logging.exception("No se pudo leer el estado de las metas")
        out = []
        with db._conexion() as con:
            for o in compiten:
                eqs = sorted({(p.get("equipo_nombre") or "").strip() for p in o["participantes"]} - {""})
                ultima = None
                if o.get("unidad_id") and eqs:
                    ultima = con.execute(f"""SELECT MAX(v.fecha) FROM viajes v WHERE v.unidad_id = ? AND v.estado = 'CONFIRMADO' AND v.fecha < ?
                                             AND EXISTS (SELECT 1 FROM viaje_participantes p WHERE p.viaje_id = v.id AND p.equipo_nombre IN ({",".join("?" * len(eqs))}))""",
                                         [o["unidad_id"], hoy.isoformat()] + eqs).fetchone()[0]
                out.append({"viaje": o, "urgencia": o.get("motivo_prioridad") if o.get("prioridad_pedida") or o.get("revision_direccion") else None,
                            "dias_sin_visita": (hoy - date.fromisoformat(ultima)).days if ultima else None, "equipos": eqs,
                            "atrasados": [e for e in eqs if estado_visitas.get(e.lower()) == "atrasada"], "inmediato": bool(o.get("inmediato"))})
        return out

    templates.env.globals["viajes_puntaje_revision"] = puntaje_revision

    # ------------------------------------------------------------ propuesta 96: borrador de lectura del trimestre (IA local)
    @app.post("/metas/lectura", response_class=HTMLResponse)
    async def metas_lectura(request: Request):
        import metas
        import nucleo
        from dgpdh.dominio import lectura_metas as lm
        u = usuario_actual(request)
        f = await request.form()
        e = _equipo_metas(u, f.get("equipo_id"))
        a, t = _anio_tri(f.get("anio"), f.get("trimestre"))
        seg = metas.seguimiento(a, [e])["equipos"][0]
        lista = lm.hechos(e["nombre"], seg["filas"], t)
        respuesta = nucleo.consultar_ollama("redaccion", lm.prompt(e["nombre"], t, a, lista), num_predict=500, timeout=120) if lista else None
        texto, con_ia = lm.armar(respuesta, e["nombre"], t, a, lista)
        return pagina(request, "viajes_lectura_metas.html", e=e, anio=a, t=t, texto=texto, con_ia=con_ia, hechos=lista)
