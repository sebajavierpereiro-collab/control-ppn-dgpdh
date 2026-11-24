"""Pantallas de la ola 5 (datos, seguridad y capacitación). Se registran desde main.py con `registrar(app, ctx)`.

  /datos ...............  tablero de la Dirección (81), por unidad (82), mapa de temas (86), uso del sistema (89),
                          datos agregados publicables (88) y reportes programados (83)
  /consultas ...........  preguntas en lenguaje común sobre el archivo (14)
  /traslados/presentismo/mes, /persona, /cruce  ...  presentismo mensual, por persona y cruce con Personal (80)
  /pin/...  ............  PIN de firma (93)
  /manual ..............  manual por rol y videos (100)
"""
from datetime import date, timedelta
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

import auth
import consultas
import database as db
import indicadores as ind
import manual
import pin
import presentismo
import programados
import traslados as tr

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PESTANAS_DATOS = (("/datos", "Tablero"), ("/datos/unidad", "Por unidad"), ("/datos/temas", "Mapa de temas"),
                  ("/datos/uso", "Uso del sistema"), ("/datos/abiertos", "Datos publicables"), ("/datos/programados", "Reportes programados"))


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]
    tiene_notas, puede_ver_nota = ctx["tiene_notas"], ctx["puede_ver_nota"]
    import iconos
    templates.env.globals.setdefault("ico", iconos.svg)
    templates.env.globals["pin_pendiente"] = lambda request, u: bool(u and pin.requiere(u) and not pin.sesion_confirmada(request.session, u))
    templates.env.globals["pin_tiene"] = pin.tiene_pin

    def pagina(request, nombre, **c):
        return templates.TemplateResponse(nombre, dict(c, request=request, usuario=usuario_actual(request)))

    def solo_direccion(request):
        u = usuario_actual(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Esta sección es para la Dirección.")
        return u

    def archivo(contenido, nombre, tipo=XLSX):
        return Response(contenido, media_type=tipo, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})

    def comunes(request, desde, hasta, activa):
        d, h = ind.rango(desde, hasta)
        return {"desde": d, "hasta": h, "pestanas": PESTANAS_DATOS, "activa": activa, "dmy": ind._dmy,
                "periodos": _periodos_rapidos()}

    # ------------------------------------------------------------------ 81) tablero
    @app.get("/datos", response_class=HTMLResponse)
    def datos_tablero(request: Request, desde: str = "", hasta: str = ""):
        solo_direccion(request)
        c = comunes(request, desde, hasta, "/datos")
        return pagina(request, "datos_tablero.html", t=ind.tablero(c["desde"], c["hasta"]), **c)

    @app.get("/datos/tablero.xlsx")
    def datos_tablero_excel(request: Request, desde: str = "", hasta: str = ""):
        solo_direccion(request)
        d, h = ind.rango(desde, hasta)
        return archivo(programados.tablero_excel(ind.tablero(d, h)), f"tablero_{d}_{h}.xlsx")

    # ------------------------------------------------------------------ 82) por unidad
    @app.get("/datos/unidad", response_class=HTMLResponse)
    def datos_unidad(request: Request, unidad_id: str = "", desde: str = "", hasta: str = ""):
        solo_direccion(request)
        c = comunes(request, desde, hasta, "/datos/unidad")
        unidades = ind.unidades_con_actividad(c["desde"], c["hasta"])
        uid = int(unidad_id) if unidad_id.isdigit() else None
        datos = ind.por_unidad(uid, c["desde"], c["hasta"]) if uid else None
        return pagina(request, "datos_unidad.html", unidades=unidades, unidad_id=uid, d=datos, **c)

    @app.get("/datos/unidad.xlsx")
    def datos_unidad_excel(request: Request, unidad_id: int, desde: str = "", hasta: str = ""):
        solo_direccion(request)
        d, h = ind.rango(desde, hasta)
        datos = ind.por_unidad(unidad_id, d, h)
        if not datos:
            raise HTTPException(status_code=404, detail="Unidad no encontrada")
        return archivo(ind.excel_unidad(datos), f"monitoreo_unidad_{unidad_id}_{d}_{h}.xlsx")

    # ------------------------------------------------------------------ 86) mapa de temas
    @app.get("/datos/temas", response_class=HTMLResponse)
    def datos_temas(request: Request, desde: str = "", hasta: str = "", pedido: str = ""):
        solo_direccion(request)
        c = comunes(request, desde, hasta, "/datos/temas")
        return pagina(request, "datos_temas.html", m=ind.mapa_temas(c["desde"], c["hasta"], pedido), **c)

    # ------------------------------------------------------------------ 89) uso del sistema
    @app.get("/datos/uso", response_class=HTMLResponse)
    def datos_uso(request: Request, desde: str = "", hasta: str = ""):
        solo_direccion(request)
        if not desde:
            desde = (date.today() - timedelta(days=30)).isoformat()
        c = comunes(request, desde, hasta, "/datos/uso")
        return pagina(request, "datos_uso.html", uso=ind.uso_sistema(c["desde"], c["hasta"]), **c)

    @app.get("/datos/uso.xlsx")
    def datos_uso_excel(request: Request, desde: str = "", hasta: str = ""):
        solo_direccion(request)
        d, h = ind.rango(desde or (date.today() - timedelta(days=30)).isoformat(), hasta)
        return archivo(programados.uso_excel(ind.uso_sistema(d, h)), f"uso_sistema_{d}_{h}.xlsx")

    # ------------------------------------------------------------------ 88) datos publicables
    @app.get("/datos/abiertos", response_class=HTMLResponse)
    def datos_abiertos(request: Request, desde: str = "", hasta: str = ""):
        solo_direccion(request)
        c = comunes(request, desde, hasta, "/datos/abiertos")
        return pagina(request, "datos_abiertos.html", a=ind.datos_abiertos(c["desde"], c["hasta"]), **c)

    @app.get("/datos/abiertos.{formato}")
    def datos_abiertos_archivo(request: Request, formato: str, desde: str = "", hasta: str = ""):
        solo_direccion(request)
        d, h = ind.rango(desde, hasta)
        a = ind.datos_abiertos(d, h)
        if formato == "csv":
            return archivo(ind.abiertos_csv(a), f"datos_agregados_dgpdh_{d}_{h}.csv", "text/csv; charset=utf-8")
        if formato == "xlsx":
            return archivo(ind.abiertos_excel(a), f"datos_agregados_dgpdh_{d}_{h}.xlsx")
        raise HTTPException(status_code=404, detail="Formato no disponible")

    # ------------------------------------------------------------------ 83) reportes programados
    @app.get("/datos/programados", response_class=HTMLResponse)
    def datos_programados(request: Request):
        solo_direccion(request)
        c = comunes(request, "", "", "/datos/programados")
        return pagina(request, "datos_programados.html", reportes=programados.REPORTES, activos=programados.activos(),
                      generados=programados.listar(), **c)

    @app.post("/datos/programados")
    async def datos_programados_guardar(request: Request):
        solo_direccion(request)
        form = await request.form()
        programados.guardar_activos(set(form.getlist("activo")))
        if form.get("generar_ahora"):
            hechos = programados.tick()
            flash(request, f"Se generaron {len(hechos)} reporte(s)." if hechos else "No había reportes nuevos para generar (ya estaban los del último período).")
        else:
            flash(request, "Reportes programados guardados.")
        return RedirectResponse(url="/datos/programados", status_code=303)

    @app.get("/datos/programados/{rid}/descargar")
    def datos_programado_descargar(request: Request, rid: int):
        u = usuario_actual(request)
        f = programados.obtener(rid)
        if not f or not programados.puede_descargar(u, f):
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        ruta = programados.CARPETA / f["archivo"]
        if not ruta.is_file():
            raise HTTPException(status_code=404, detail="El archivo ya no está en el servidor")
        return FileResponse(ruta, filename=f["archivo"], media_type=XLSX)

    # ------------------------------------------------------------------ 14) consultas
    @app.get("/consultas", response_class=HTMLResponse)
    def consultas_pagina(request: Request, q: str = "", ia: str = "1"):
        u = usuario_actual(request)
        if not tiene_notas(u):
            raise HTTPException(status_code=403, detail="No tenés acceso al archivo de notas")
        filtros = resultado = None
        if q.strip():
            filtros = consultas.interpretar(q.strip()[:300], usar_ia=(ia == "1"))
            resultado = consultas.ejecutar(filtros, puede_ver_nota, u)
        return pagina(request, "consultas.html", q=q, filtros=filtros, r=resultado, usuario_ve_todo=auth.es_admin(u), **_contexto_consulta(filtros))

    @app.post("/consultas", response_class=HTMLResponse)
    async def consultas_corregida(request: Request):
        u = usuario_actual(request)
        if not tiene_notas(u):
            raise HTTPException(status_code=403, detail="No tenés acceso al archivo de notas")
        form = await request.form()
        filtros = consultas.filtros_desde_form(form)
        resultado = consultas.ejecutar(filtros, puede_ver_nota, u)
        return pagina(request, "consultas.html", q=str(form.get("q") or ""), filtros=filtros, r=resultado, usuario_ve_todo=auth.es_admin(u), **_contexto_consulta(filtros))

    def _contexto_consulta(filtros):
        import agentes
        import nucleo
        with db._conexion() as con:
            equipos = [dict(e) for e in con.execute("SELECT id, nombre FROM equipos ORDER BY nombre")]
        return {"ejemplos": consultas.EJEMPLOS, "catalogo": db.listar_unidades(), "pedidos": list(agentes.PEDIDOS),
                "equipos": equipos, "estados": consultas.ESTADOS, "agrupar": consultas.AGRUPAR,
                "descripcion": consultas.describir(filtros) if filtros else "", "ia_disponible": nucleo.ia_disponible("clasificacion"),
                "dmy": ind._dmy, "corto": ind.nombre_corto}

    # ------------------------------------------------------------------ 80) presentismo mensual, por persona y cruce
    def exigir_presentismo(request):
        u = usuario_actual(request)
        if not presentismo.puede_ver(u):
            raise HTTPException(status_code=403, detail="Este reporte es para el área de Personal y la coordinación de traslados.")
        return u

    def _mes_pedido(mes):
        try:
            a, m = (int(x) for x in (mes or "").split("-")[:2])
            date(a, m, 1)
            return a, m
        except (ValueError, TypeError):
            h = date.today()
            return h.year, h.month

    @app.get("/traslados/presentismo/mes", response_class=HTMLResponse)
    def presentismo_mes(request: Request, mes: str = ""):
        exigir_presentismo(request)
        a, m = _mes_pedido(mes)
        anterior = date(a - (m == 1), (m - 2) % 12 + 1, 1)
        siguiente = date(a + (m == 12), m % 12 + 1, 1)
        return pagina(request, "presentismo_mes.html", datos=presentismo.mes(a, m), mes=f"{a:04d}-{m:02d}",
                      anterior=anterior.isoformat()[:7], siguiente=siguiente.isoformat()[:7], vista="mes")

    @app.get("/traslados/presentismo/mes.xlsx")
    def presentismo_mes_excel(request: Request, mes: str = ""):
        u = exigir_presentismo(request)
        a, m = _mes_pedido(mes)
        return archivo(presentismo.excel_mes(presentismo.mes(a, m), generado_por=u["nombre"]), f"presentismo_{a:04d}-{m:02d}.xlsx")

    @app.get("/traslados/presentismo/persona", response_class=HTMLResponse)
    def presentismo_persona(request: Request, usuario_id: str = "", desde: str = "", hasta: str = ""):
        exigir_presentismo(request)
        d, h = ind.rango(desde or (date.today() - timedelta(days=90)).isoformat(), hasta)
        uid = int(usuario_id) if usuario_id.isdigit() else None
        personas = sorted((x for x in db.listar_usuarios() if x["activo"] and x["rol"] != auth.ROL_CHOFER), key=lambda x: x["nombre"].lower())
        datos = presentismo.persona(uid, date.fromisoformat(d), date.fromisoformat(h)) if uid else None
        return pagina(request, "presentismo_persona.html", personas=personas, usuario_id=uid, datos=datos, desde=d, hasta=h,
                      vista="persona", dmy=ind._dmy)

    @app.get("/traslados/presentismo/cruce", response_class=HTMLResponse)
    def presentismo_cruce(request: Request, mes: str = ""):
        exigir_presentismo(request)
        a, m = _mes_pedido(mes)
        ini = date(a, m, 1)
        fin = date(a + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        return pagina(request, "presentismo_cruce.html", c=presentismo.cruce(ini, fin), mes=f"{a:04d}-{m:02d}",
                      importaciones=presentismo.importaciones(), vista="cruce",
                      dmy=ind._dmy, etiquetas=presentismo.ETIQUETA_CLASE)

    @app.post("/traslados/presentismo/cruce")
    async def presentismo_importar(request: Request):
        u = exigir_presentismo(request)
        form = await request.form()
        archivo_subido = form.get("planilla")
        if not archivo_subido or not getattr(archivo_subido, "filename", ""):
            flash(request, "Elegí la planilla de Personal.")
            return RedirectResponse(url="/traslados/presentismo/cruce", status_code=303)
        contenido = await archivo_subido.read()
        a, m = _mes_pedido(str(form.get("mes") or ""))
        try:
            res, avisos = presentismo.importar(archivo_subido.filename, contenido, u, a, m)
        except Exception as e:
            res, avisos = None, [f"No se pudo leer el archivo: {e}"]
        if not res:
            flash(request, " ".join(avisos) or "La planilla no tenía filas para tomar.")
            return RedirectResponse(url=f"/traslados/presentismo/cruce?mes={a:04d}-{m:02d}", status_code=303)
        flash(request, f"Planilla tomada: {res['filas']} registro(s) del {ind._dmy(res['desde'].isoformat())} al {ind._dmy(res['hasta'].isoformat())}"
                       + (f"; {res['sin_vincular']} nombre(s) sin vincular a un usuario" if res["sin_vincular"] else "") + ". " + " ".join(avisos))
        return RedirectResponse(url=f"/traslados/presentismo/cruce?mes={res['desde'].isoformat()[:7]}", status_code=303)

    # ------------------------------------------------------------------ 93) PIN de firma
    @app.post("/pin/verificar")
    async def pin_verificar(request: Request):
        u = usuario_actual(request)
        if not pin.requiere(u):
            return JSONResponse({"ok": True})
        form = await request.form()
        ip = request.client.host if getattr(request, "client", None) else None
        if not pin.tiene_pin(u):
            error = pin.crear(u, str(form.get("pin") or ""), str(form.get("repetir") or ""), ip=ip)
            if error:
                return JSONResponse({"ok": False, "mensaje": error})
            request.session[pin.CLAVE_SESION] = u["id"]
            return JSONResponse({"ok": True, "mensaje": "PIN creado. No se te va a volver a pedir en esta sesión."})
        ok, mensaje = pin.verificar(u, str(form.get("pin") or ""), ip=ip)
        if ok:
            request.session[pin.CLAVE_SESION] = u["id"]
        return JSONResponse({"ok": ok, "mensaje": mensaje})

    @app.post("/perfil/pin", response_class=HTMLResponse)
    async def perfil_cambiar_pin(request: Request):
        u = usuario_actual(request)
        if not pin.requiere(u):
            raise HTTPException(status_code=403, detail="Tu rol no usa PIN de firma.")
        form = await request.form()
        if pin.tiene_pin(u):
            ok, mensaje = pin.verificar(u, str(form.get("actual") or ""))
            if not ok:
                flash(request, "PIN actual: " + mensaje)
                return RedirectResponse(url="/perfil#pin", status_code=303)
        error = pin.crear(u, str(form.get("pin") or ""), str(form.get("repetir") or ""))
        flash(request, error or "PIN de firma guardado.")
        if not error:
            request.session[pin.CLAVE_SESION] = u["id"]
        return RedirectResponse(url="/perfil#pin", status_code=303)

    @app.post("/admin/usuarios/{usuario_id}/pin-borrar")
    def admin_borrar_pin(request: Request, usuario_id: int):
        u = solo_direccion(request)
        otro = db.obtener_usuario(usuario_id)
        if not otro:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        pin.borrar(usuario_id, por=u)
        request.session["flash_admin"] = f"Se borró el PIN de {otro['nombre']}: va a crear uno nuevo la próxima vez que firme o despache."
        return RedirectResponse(url="/admin/seguridad/pin", status_code=303)

    # ------------------------------------------------------------------ 100) manual y videos
    @app.get("/manual", response_class=HTMLResponse)
    def manual_inicio(request: Request, rol: str = ""):
        u = usuario_actual(request)
        clave = rol if rol in manual.ROLES or rol == "todos" else manual.rol_de(u)
        m = manual.TODOS if clave == "todos" else manual.ROLES[clave]
        return pagina(request, "manual.html", roles=manual.ROLES, rol=clave, m=m, videos=manual.videos_de(clave),
                      todos_videos=manual.VIDEOS, generales=manual.GENERALES)


def _periodos_rapidos(hoy=None):
    hoy = hoy or date.today()
    ini_mes = date(hoy.year, hoy.month, 1)
    mes_ant_fin = ini_mes - timedelta(days=1)
    return [
        ("Últimos 30 días", (hoy - timedelta(days=30)).isoformat(), hoy.isoformat()),
        ("Este mes", ini_mes.isoformat(), hoy.isoformat()),
        ("Mes anterior", date(mes_ant_fin.year, mes_ant_fin.month, 1).isoformat(), mes_ant_fin.isoformat()),
        ("Este año", date(hoy.year, 1, 1).isoformat(), hoy.isoformat()),
        ("Últimos 12 meses", date(hoy.year - 1, hoy.month, 1).isoformat(), hoy.isoformat()),
    ]
