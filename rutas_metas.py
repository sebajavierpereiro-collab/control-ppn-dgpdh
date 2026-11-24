"""Pantallas de metas de gestión por equipo.

  /metas ...................... seguimiento (Dirección: todos los equipos; coordinación: el suyo) + Excel con la planilla
  /metas/anuales .............. la Dirección fija las metas anuales de cada equipo
  /metas/carga ................ la coordinación carga lo realizado por mes o por trimestre, persona por persona
  /metas/importar ............. planillas de metas de períodos anteriores: subir → vista previa → confirmar
  /metas/cargas ............... cargas de planillas: vincular siglas a usuarios o anular una carga completa
"""
import io
import json
import secrets
from pathlib import Path
from datetime import date
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

import auth
import database as db
import metas
import metas_importar as mi


def _carpeta():
    c = Path(db.RUTA_DB).parent / "importaciones_metas"
    c.mkdir(parents=True, exist_ok=True)
    return c


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]

    def pagina(request, nombre, **c):
        u = usuario_actual(request)
        return templates.TemplateResponse(nombre, dict(c, request=request, usuario=u, M=metas, es_direccion=auth.es_admin(u),
                                                       puede_planillas=bool(_equipos_importables(u))))

    def _equipos_importables(u):
        return [e for e in metas.equipos_visibles(u) if mi.puede_importar(u, e["id"])] if u and metas.puede_ver(u) else []

    def exigir_ver(request):
        u = usuario_actual(request)
        if not metas.puede_ver(u):
            raise HTTPException(status_code=403, detail="Las metas las ven la Dirección y los integrantes de cada equipo.")
        return u

    def anio_de(valor):
        hoy = date.today().year
        return int(valor) if str(valor).isdigit() and hoy - 5 <= int(valor) <= hoy + 2 else hoy

    def equipo_elegido(u, valor):
        visibles = metas.equipos_visibles(u)
        if not visibles:
            raise HTTPException(status_code=403, detail="No tenés un equipo asignado.")
        if str(valor).isdigit():
            e = next((x for x in visibles if x["id"] == int(valor)), None)
            if not e:
                raise HTTPException(status_code=403, detail="Solo podés ver las metas de tu equipo.")
            return e, visibles
        return None, visibles

    @app.get("/metas", response_class=HTMLResponse)
    def metas_panel(request: Request, anio: str = "", equipo_id: str = ""):
        u = exigir_ver(request)
        a = anio_de(anio)
        e, visibles = equipo_elegido(u, equipo_id)
        equipos = [e] if e else visibles
        with metas.cache_de_pedido():  # lo de los viajes sale de la foto diaria: pocas consultas por pantalla
            datos = metas.seguimiento(a, equipos)
            try:  # propuesta 89: calidad de las visitas (el Registro de Obstaculizaciones solo cuenta para la Dirección, que lo ve todo)
                calidad = metas.calidad_foto(a, equipos, incluir_obstaculizaciones=auth.es_admin(u))
            except Exception:
                import logging
                logging.exception("No se pudo leer la calidad de las visitas")
                calidad = {}
            fotos = metas.foto(a)
            try:  # 85: tendencia por indicador (vista de un equipo) · 94: comparación normalizada (vista de todos)
                tend = {x["equipo"]["id"]: metas.tendencias(a, x["equipo"], x["filas"]) for x in datos["equipos"]} if e or len(datos["equipos"]) == 1 else {}
                comp = metas.comparacion(datos["equipos"]) if not e and len(datos["equipos"]) > 1 else []
            except Exception:
                import logging
                logging.exception("No se pudo calcular la tendencia o la comparación")
                tend, comp = {}, []
            actualizado = min((fotos[x["id"]]["generado_en"] for x in equipos if x["id"] in fotos), default=None)
        return pagina(request, "metas_panel.html", d=datos, anio=a, equipo=e, visibles=visibles, hoy=date.today(), calidad=calidad,
                      actualizado=actualizado, tendencias=tend, comparacion=comp)

    @app.post("/metas/actualizar")
    async def metas_actualizar(request: Request):
        """«Actualizar ahora»: recalcula lo de los viajes del equipo (como mucho una vez por minuto por equipo)."""
        u = exigir_ver(request)
        f = await request.form()
        a = anio_de(f.get("anio"))
        e, visibles = equipo_elegido(u, f.get("equipo_id"))
        objetivo = [e] if e else visibles
        mensajes = [metas.actualizar_ahora(a, x, u)[1] for x in objetivo]
        flash(request, mensajes[0] if len(set(mensajes)) == 1 else "Metas actualizadas (algunos equipos se habían actualizado hace menos de un minuto).")
        return RedirectResponse(url=f"/metas?anio={a}" + (f"&equipo_id={e['id']}" if e else ""), status_code=303)

    @app.get("/metas.xlsx")
    def metas_excel(request: Request, anio: str = "", equipo_id: str = ""):
        u = exigir_ver(request)
        a = anio_de(anio)
        e, visibles = equipo_elegido(u, equipo_id)
        e = e or visibles[0]
        libro = metas.exportar_planilla(a, e)
        buf = io.BytesIO(); libro.save(buf)
        nombre = f"Metas {e['nombre']} {a}.xlsx"
        return StreamingResponse(iter([buf.getvalue()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    # ------------------------------------------------------------ metas anuales (Dirección)
    @app.get("/metas/anuales", response_class=HTMLResponse)
    def metas_anuales(request: Request, anio: str = ""):
        u = exigir_ver(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Las metas anuales las fija la Dirección.")
        a = anio_de(anio)
        return pagina(request, "metas_anuales.html", anio=a, equipos=metas.equipos_con_integrantes(), valores=metas.metas_anuales(a))

    @app.post("/metas/anuales")
    async def metas_anuales_guardar(request: Request):
        u = exigir_ver(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Las metas anuales las fija la Dirección.")
        f = await request.form()
        a = anio_de(f.get("anio"))
        filas, malos = {}, 0
        for e in metas.equipos_con_integrantes():
            for i in metas.INDICADORES:
                v = str(f.get(f"m_{e['id']}_{i['clave']}") or "").strip()
                if v == "":
                    continue
                if not v.isdigit():
                    malos += 1; continue
                filas.setdefault(e["id"], {})[i["clave"]] = int(v)
        n = metas.guardar_metas_anuales(a, filas, u)
        flash(request, f"Metas {a} guardadas ({n} valor(es))." + (f" ⚠ {malos} valor(es) no eran números y no se guardaron." if malos else ""))
        return RedirectResponse(url=f"/metas/anuales?anio={a}", status_code=303)

    # ------------------------------------------------------------ carga de lo realizado (coordinación)
    @app.get("/metas/carga", response_class=HTMLResponse)
    def metas_carga(request: Request, anio: str = "", periodo: str = "", equipo_id: str = ""):
        u = exigir_ver(request)
        a = anio_de(anio)
        e, visibles = equipo_elegido(u, equipo_id)
        e = e or visibles[0]
        try:
            p = metas.validar_periodo(periodo) if periodo else metas.periodo_actual()
        except ValueError:
            p = metas.periodo_actual()
        g = metas.grilla_carga(e["id"], a, p)
        persona = [i for i in metas.indicadores_de(e) if metas.dimension_de(i) == "persona"]  # perfil del equipo
        return pagina(request, "metas_carga.html", g=g, anio=a, equipo=e, visibles=visibles, editables=metas.editables(u, e["id"]),
                      indicadores_carga=persona, grupos_carga=[gr for gr in metas.grupos_de(e) if any(i["grupo"] == gr[0] for i in persona)],
                      perfil=metas.perfil_de(e))

    @app.post("/metas/carga")
    async def metas_carga_guardar(request: Request):
        u = exigir_ver(request)
        f = await request.form()
        e, _ = equipo_elegido(u, f.get("equipo_id"))
        if not e:
            raise HTTPException(status_code=400, detail="Falta el equipo.")
        a = anio_de(f.get("anio"))
        valores, malos = {}, 0
        permitidos = metas.editables(u, e["id"])
        for persona in [x for x in metas.integrantes(e["id"]) if x["id"] in permitidos]:
            for clave in [i["clave"] for i in metas.indicadores_de(e) if metas.dimension_de(i) == "persona" and i["fuente"] == "carga"]:
                v = str(f.get(f"v_{persona['id']}_{clave}") or "").strip()
                if v == "":
                    v = "0"
                if not v.isdigit():
                    malos += 1; continue
                valores[(persona["id"], clave)] = int(v)
        destino = f"/metas/carga?anio={a}&periodo={f.get('periodo')}&equipo_id={e['id']}"
        try:
            n = metas.guardar_carga(e["id"], a, f.get("periodo"), valores, u, permitidos)
        except ValueError as ex:
            flash(request, f"⚠ {ex}")
            return RedirectResponse(url=destino, status_code=303)
        flash(request, f"Guardado: {metas.nombre_periodo(metas.validar_periodo(f.get('periodo')), a)} de {e['nombre']}."
                       + (f" ⚠ {malos} valor(es) no eran números y no se guardaron." if malos else ""))
        return RedirectResponse(url=destino, status_code=303)

    # ------------------------------------------------------------ planillas de períodos anteriores
    def exigir_importar(request, equipo_id=None):
        u = exigir_ver(request)
        equipos = _equipos_importables(u)
        if not equipos:
            raise HTTPException(status_code=403, detail="Las planillas anteriores las importa la Dirección o la coordinación del equipo.")
        if equipo_id is not None and not any(e["id"] == equipo_id for e in equipos):
            raise HTTPException(status_code=403, detail="Solo podés importar planillas de tu equipo.")
        return u, equipos

    def _elegidas(form, lectura):
        """Hojas tildadas con su año y trimestre (editables en la vista previa)."""
        out = {}
        for k, h in enumerate(lectura["hojas"]):
            if form.get(f"hoja_{k}") != "1":
                continue
            a, t = str(form.get(f"anio_{k}") or ""), str(form.get(f"tri_{k}") or "")
            out[h["hoja"]] = (int(a) if a.isdigit() else None, int(t) if t.isdigit() else None)
        return out

    def _vista_previa(request, u, token, datos, form=None, avisos=None):
        lectura = datos["lectura"]
        if form is None:  # primera vez: todas las hojas con período reconocido, con el período del nombre
            elegidas = {h["hoja"]: (h["anio"], h["trimestre"]) for h in lectura["hojas"] if h["anio"] and h["trimestre"] and h["filas"]}
            vinculos, asign, modo = {}, {}, "reemplazar"
        else:
            elegidas = _elegidas(form, lectura)
            vinculos = {s: form.get(f"sigla_{s}") for s in {x for h in lectura["hojas"] for x in h["siglas"]}}
            asign = {k[4:]: form.get(k) for k in form.keys() if k.startswith("map_")}
            modo = form.get("modo") or "reemplazar"
        elegidas_ok = {k: v for k, v in elegidas.items() if v[0] and v[1]}
        p = mi.previa(lectura, datos["equipo_id"], elegidas_ok, asign)
        equipo = next((e for e in db.listar_equipos() if e["id"] == datos["equipo_id"]), {"nombre": ""})
        return pagina(request, "metas_importar.html", paso="previa", token=token, lectura=lectura, p=p, elegidas=elegidas, vinculos=vinculos,
                      asign=asign, modo=modo, equipo=equipo, archivo=datos.get("archivo"), avisos=(avisos or []) + lectura["avisos"],
                      usuarios=[x for x in db.listar_usuarios() if x["activo"] and x["rol"] != "CHOFER"], anio=date.today().year)

    def _leer_token(token, u):
        token = str(token or "")
        ruta = _carpeta() / f"{token}.json"
        if not token.replace("-", "").replace("_", "").isalnum() or not ruta.is_file():
            return None, None
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("usuario_id") != u["id"]:
            raise HTTPException(status_code=403, detail="Esta vista previa la generó otra persona.")
        mi.normalizar(datos["lectura"])
        return ruta, datos

    @app.get("/metas/importar", response_class=HTMLResponse)
    def metas_importar_form(request: Request, equipo_id: str = ""):
        u, equipos = exigir_importar(request)
        elegido = int(equipo_id) if str(equipo_id).isdigit() else (u.get("equipo_id") if any(e["id"] == u.get("equipo_id") for e in equipos) else None)
        return pagina(request, "metas_importar.html", paso="subir", equipos=equipos, elegido=elegido, anio=date.today().year, equipo=None,
                      cargas=mi.cargas({e["id"] for e in equipos}, 10))

    @app.post("/metas/importar", response_class=HTMLResponse)
    async def metas_importar_subir(request: Request):
        f = await request.form()
        eid = int(f["equipo_id"]) if str(f.get("equipo_id") or "").isdigit() else None
        if not eid:
            exigir_importar(request)
            flash(request, "⚠ Elegí el equipo al que pertenece la planilla.")
            return RedirectResponse(url="/metas/importar", status_code=303)
        u, _ = exigir_importar(request, eid)
        subido = f.get("planilla")
        if not subido or not getattr(subido, "filename", ""):
            flash(request, "⚠ Elegí la planilla para importar.")
            return RedirectResponse(url=f"/metas/importar?equipo_id={eid}", status_code=303)
        if not subido.filename.lower().endswith((".xlsx", ".xlsm")):
            flash(request, "⚠ La planilla tiene que ser de Excel (.xlsx).")
            return RedirectResponse(url=f"/metas/importar?equipo_id={eid}", status_code=303)
        contenido = await subido.read()
        try:
            lectura = mi.leer_planilla(contenido)
        except Exception as e:
            flash(request, f"⚠ No se pudo leer la planilla: {e}")
            return RedirectResponse(url=f"/metas/importar?equipo_id={eid}", status_code=303)
        if not lectura["hojas"]:
            flash(request, "⚠ " + " ".join(lectura["avisos"]))
            return RedirectResponse(url=f"/metas/importar?equipo_id={eid}", status_code=303)
        token = secrets.token_urlsafe(16)
        datos = {"usuario_id": u["id"], "equipo_id": eid, "archivo": subido.filename, "lectura": lectura}
        (_carpeta() / f"{token}.json").write_text(json.dumps(datos, ensure_ascii=False, default=str), encoding="utf-8")
        (_carpeta() / f"{token}.orig").write_bytes(contenido)
        import archivo_cargas
        previo = archivo_cargas.ya_cargado(contenido, "metas")
        avisos = [f"⚠ Esta misma planilla ya se importó el {previo['fecha'][8:10]}/{previo['fecha'][5:7]}/{previo['fecha'][:4]} (carga #{previo['carga_id']})."] if previo else None
        return _vista_previa(request, u, token, datos, avisos=avisos)

    @app.post("/metas/importar/revisar", response_class=HTMLResponse)
    async def metas_importar_revisar(request: Request):
        f = await request.form()
        u, _ = exigir_importar(request)
        ruta, datos = _leer_token(f.get("token"), u)
        if not datos:
            flash(request, "La vista previa venció: volvé a subir la planilla.")
            return RedirectResponse(url="/metas/importar", status_code=303)
        exigir_importar(request, datos["equipo_id"])
        if f.get("accion") != "confirmar":
            return _vista_previa(request, u, f.get("token"), datos, f)
        lectura = datos["lectura"]
        elegidas = _elegidas(f, lectura)
        faltan = [h for h, (a, t) in elegidas.items() if not a or not t]
        if faltan:
            return _vista_previa(request, u, f.get("token"), datos, f, [f"⚠ Indicá año y trimestre de «{h}»." for h in faltan])
        vinculos = {s: f.get(f"sigla_{s}") for s in {x for h in lectura["hojas"] for x in h["siglas"]}}
        asign = {k[4:]: f.get(k) for k in f.keys() if k.startswith("map_")}
        try:
            cid, n = mi.aplicar(lectura, datos["equipo_id"], elegidas, vinculos, f.get("modo"), u, datos.get("archivo"), asign)
        except ValueError as e:
            return _vista_previa(request, u, f.get("token"), datos, f, [f"⚠ {e}"])
        orig = ruta.with_suffix(".orig")
        if orig.is_file():
            import archivo_cargas
            archivo_cargas.guardar("metas", cid, datos.get("archivo"), orig.read_bytes(), u)
            orig.unlink(missing_ok=True)
        ruta.unlink(missing_ok=True)
        flash(request, f"Planilla importada: {n} valor(es) de {len(elegidas)} trimestre(s). Quedó como la carga #{cid}; si hubo un error, se anula entera.")
        return RedirectResponse(url=f"/metas/cargas?ver={cid}", status_code=303)

    @app.get("/metas/cargas", response_class=HTMLResponse)
    def metas_cargas(request: Request, ver: str = ""):
        u, equipos = exigir_importar(request)
        lista = mi.cargas({e["id"] for e in equipos})
        elegida = next((c for c in lista if str(c["id"]) == ver), None)
        if elegida:
            import archivo_cargas
            elegida = dict(elegida, original=archivo_cargas.de_carga("metas", elegida["id"]))
        return pagina(request, "metas_cargas.html", lista=lista, elegida=elegida, anio=date.today().year, equipo=None,
                      usuarios=[x for x in db.listar_usuarios() if x["activo"] and x["rol"] != "CHOFER"], nombre_periodos=mi.nombre_periodos)

    def _carga_propia(request, carga_id):
        u, equipos = exigir_importar(request)
        c = next((x for x in mi.cargas({e["id"] for e in equipos}) if x["id"] == carga_id), None)
        if not c:
            raise HTTPException(status_code=404, detail="La carga no existe.")
        return u, c

    @app.get("/metas/cargas/{carga_id:int}/original")
    def metas_carga_original(request: Request, carga_id: int):
        _carga_propia(request, carga_id)
        import archivo_cargas
        reg = archivo_cargas.de_carga("metas", carga_id)
        if not reg:
            raise HTTPException(status_code=404, detail="Esta carga no tiene la planilla original guardada (es anterior a esta versión).")
        try:
            nombre, contenido = archivo_cargas.leer(reg)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        return Response(contenido, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    @app.post("/metas/cargas/{carga_id:int}/anular")
    async def metas_carga_anular(request: Request, carga_id: int):
        u, c = _carga_propia(request, carga_id)
        f = await request.form()
        try:
            r = mi.anular(carga_id, f.get("motivo"), u)
        except ValueError as e:
            flash(request, f"⚠ {e}")
            return RedirectResponse(url=f"/metas/cargas?ver={carga_id}#anular", status_code=303)
        flash(request, f"Carga #{carga_id} anulada: sus valores dejan de contar." + (f" Volvieron a contar {r['restituidos']} valor(es) que había reemplazado." if r["restituidos"] else ""))
        return RedirectResponse(url="/metas/cargas", status_code=303)

    @app.post("/metas/cargas/{carga_id:int}/vincular")
    async def metas_carga_vincular(request: Request, carga_id: int):
        u, c = _carga_propia(request, carga_id)
        f = await request.form()
        n = 0
        for s in c["sin_usuario"]:
            v = str(f.get(f"sigla_{s}") or "")
            if v.isdigit():
                try:
                    n += mi.vincular_sigla(carga_id, s, int(v))
                except ValueError:
                    pass
        flash(request, f"Vinculado: {n} valor(es) ahora cuentan para la persona elegida." if n else "No elegiste a nadie para vincular.")
        return RedirectResponse(url=f"/metas/cargas?ver={carga_id}", status_code=303)
