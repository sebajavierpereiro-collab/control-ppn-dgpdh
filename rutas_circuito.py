"""Pantallas de la ola 3 (circuito y despacho). Se registran desde main.py con `registrar(app, ctx)`."""
import io
import json
import re
import tempfile
import types
from datetime import date, timedelta
from pathlib import Path

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

import agentes
import auth
import circuito
import database as db
import despacho
import flujo
import nucleo
import traslados as tr

COLUMNAS = [
    ("BORRADOR", "Sin enviar", ("BORRADOR",)),
    ("REVISION", "Revisión inicial", ("SUBIDA", "PROCESANDO", "EN_REVISION")),
    ("ERROR", "Con error", ("ERROR",)),
    ("FIRMA", "A la firma", ("PROCESADA",)),
    ("DESPACHO", "Firmada, a despacho", ("FIRMADA", "PARA_REMITIR")),
    ("DESPACHADA", "Despachadas (15 días)", ("DESPACHADA",)),
]


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]
    import iconos
    templates.env.globals.setdefault("ico", iconos.svg)
    puede_ver, puede_gestionar = ctx["puede_ver_nota"], ctx["puede_gestionar_nota"]
    filtrar, anotar, antiguedad = ctx["filtrar_por_visibilidad"], ctx["anotar_prioridad_efectiva"], ctx["antiguedad_etapa"]
    exigir_rol, guardar_numeros, guardar_subida = ctx["exigir_rol"], ctx["guardar_numeros"], ctx["guardar_subida"]
    ordenar_para_firma, firmar, carpeta_datos = ctx["ordenar_para_firma"], ctx["firmar"], ctx["carpeta_datos"]
    tiene_notas = ctx["tiene_notas"]

    def pagina(request, nombre, **c):
        return templates.TemplateResponse(nombre, dict(c, request=request, usuario=usuario_actual(request)))

    def nota_visible(request, nota_id):
        u = usuario_actual(request)
        n = db.obtener_nota(nota_id)
        if n is None or not puede_ver(u, n):
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        return u, n

    def es_mesa_o_dir(u):
        return auth.es_admin(u) or auth.funciones_mesa(u)

    def puede_cargar_respuesta(u, n):
        return es_mesa_o_dir(u) or puede_gestionar(u, n)

    templates.env.globals["puede_cargar_respuesta"] = puede_cargar_respuesta
    templates.env.globals["respuestas_de"] = db.respuestas_de
    templates.env.globals["envios_de"] = db.envios_de
    templates.env.globals["viajes_de_nota"] = db.viajes_de_nota

    def proximos_viajes(unidad_id, dias=30):
        if not unidad_id:
            return []
        hoy = date.today()
        return [v for v in tr.listar_viajes(desde=hoy.isoformat(), hasta=(hoy + timedelta(days=dias)).isoformat(), unidad_id=unidad_id)
                if v["estado"] in ("SOLICITADO", "CONFIRMADO", "A_CONFIRMAR")][:5]

    templates.env.globals["proximos_viajes"] = proximos_viajes

    # ------------------------------------------------------------- 27) circuito en columnas
    @app.get("/circuito", response_class=HTMLResponse)
    def ver_circuito(request: Request, equipo: str = ""):
        u = usuario_actual(request)
        if not tiene_notas(u):
            return RedirectResponse(url="/traslados")
        notas = filtrar(db.listar_notas(limite=400), u)
        anotar(notas)
        limite = (date.today() - timedelta(days=15)).isoformat()
        equipos = sorted({n.get("equipo_nombre") or "" for n in notas if n.get("equipo_nombre")})
        if equipo:
            notas = [n for n in notas if (n.get("equipo_nombre") or "") == equipo]
        rango = {"URGENTE": 0, "PRIORITARIA": 1, "NORMAL": 2}
        columnas = []
        for clave, titulo, estados in COLUMNAS:
            lista = [n for n in notas if n["estado"] in estados and (clave != "DESPACHADA" or str(n["actualizado_en"])[:10] >= limite)
                     and (clave != "BORRADOR" or n.get("usuario_id") == u["id"])]
            vistos, unicos = set(), []
            for n in lista:
                if n["id"] not in vistos:
                    vistos.add(n["id"]); unicos.append(n)
            for n in unicos:
                n["antiguedad"] = antiguedad(n)
            unicos.sort(key=lambda n: (rango.get(n.get("prioridad_efectiva") or n.get("prioridad"), 2), -((n.get("antiguedad") or {}).get("dias") or 0)))
            if clave == "BORRADOR" and not unicos:
                continue
            columnas.append({"clave": clave, "titulo": titulo, "notas": unicos,
                             "trabadas": sum(1 for n in unicos if (n.get("antiguedad") or {}).get("nivel") == "rojo")})
        return pagina(request, "circuito.html", columnas=columnas, equipos=equipos, equipo=equipo)

    # ------------------------------------------------------------- 11) despacho del día (Mesa)
    @app.get("/despacho", response_class=HTMLResponse)
    def despacho_del_dia(request: Request):
        u = usuario_actual(request)
        exigir_rol(u, *auth.ROLES_MESA)
        firmadas = [n for n in db.listar_notas(limite=400) if n["estado"] in db.LISTAS_PARA_REMITIR]
        sueltas = [n for n in firmadas if not n.get("bloque_id")]
        bloques = sorted({n["bloque_id"] for n in firmadas if n.get("bloque_id")})
        grupos = {}
        contactos = db.listar_contactos()  # una sola vez para todo el lote
        for n in sueltas:
            texto = db.texto_indexado(n["id"])
            d = agentes.destinatario_sugerido(texto, contactos) if texto else {"destinatario": "", "correo": "", "unidades": []}
            clave = (d.get("destinatario") or (d["unidades"][0]["nombre"] if d.get("unidades") else "") or "Sin destinatario detectado").strip()
            n["destino"] = d
            n["entrega_mano"] = (n.get("tipo") == "NOTA A PPL")
            grupos.setdefault(clave, []).append(n)
        lote = {}
        reservados = []
        usados_nota = circuito.usados_en_el_sistema("NOTA", date.today().year)
        for clave in sorted(grupos):
            for n in grupos[clave]:
                if not n.get("nro_nota") and despacho.lleva_numero(n):
                    num = circuito.proponer("NOTA", saltear=reservados, usados_base=usados_nota)
                    if num is None:
                        break  # sin base del registro: no se proponen números
                    reservados.append(num)
                    lote[n["id"]] = circuito.formatear("NOTA", num, date.today().year)
        esperando = db.notas_esperando_entrega()
        for n in esperando:
            n["viajes"] = proximos_viajes(n["unidad_id"])
            un = db.obtener_unidad(n["unidad_id"]) if n.get("unidad_id") else None
            n["unidad_nombre"] = un["nombre"] if un else ""
        return pagina(request, "despacho_dia.html", grupos=sorted(grupos.items()), lote=lote, bloques=[db.obtener_bloque(b) for b in bloques],
                      esperando=esperando, plazos=db.listar_plazos_destinatario(), datos_despacho=ctx["datos_despacho"])

    @app.post("/despacho/numeros")
    async def despacho_numeros(request: Request):
        u = usuario_actual(request)
        exigir_rol(u, *auth.ROLES_MESA)
        form = await request.form()
        pares = []
        for nid in form.getlist("nota_id"):
            if not str(nid).isdigit():
                continue
            n = db.obtener_nota(int(nid))
            if n and n["estado"] in db.LISTAS_PARA_REMITIR:
                pares.append((n, form.get(f"nro_nota_{nid}") or "", form.get(f"nro_expte_{nid}") or ""))
        if not pares:
            flash(request, "⚠ Marcá al menos una nota.")
        else:
            guardar_numeros(request, pares)
        return RedirectResponse(url="/despacho", status_code=303)

    @app.post("/despacho/plazos/borrar")
    def borrar_plazo_dest(request: Request, norm: str = Form("")):
        u = usuario_actual(request)
        exigir_rol(u, *auth.ROLES_MESA)
        db.borrar_plazo_destinatario(norm)
        return RedirectResponse(url="/despacho#plazos", status_code=303)

    # ------------------------------------------------------------- 56) numeración
    @app.get("/numeracion", response_class=HTMLResponse)
    def ver_numeracion(request: Request, anio: int = 0):
        u = usuario_actual(request)
        exigir_rol(u, *auth.ROLES_MESA)
        anio = anio or date.today().year
        datos = []
        for tipo, etiqueta in circuito.TIPOS_NUMERO.items():
            datos.append({"tipo": tipo, "etiqueta": etiqueta, "contador": db.contador_numeracion(anio, tipo),
                          "proximo": circuito.proponer_texto(tipo, anio), "usados": len(circuito.usados_en_el_sistema(tipo, anio)),
                          "salteados": circuito.salteados(tipo, anio)})
        excel = request.session.pop("excel_registro", None) if hasattr(request, "session") else None
        return pagina(request, "numeracion.html", anio=anio, datos=datos, contadores=db.listar_contadores(), excel=excel,
                      ruta_excel=circuito.CONFIG["excel_registro"], formato=circuito.CONFIG["formato_nota"])

    @app.post("/numeracion/fijar")
    def fijar_numeracion(request: Request, anio: int = Form(0), tipo: str = Form("NOTA"), ultimo: str = Form("")):
        u = usuario_actual(request)
        exigir_rol(u, *auth.ROLES_MESA)
        if tipo not in circuito.TIPOS_NUMERO or not str(ultimo).strip().isdigit() or not 2000 <= int(anio or 0) <= 2100:
            flash(request, "⚠ Indicá el año y el último número usado (solo números).")
        else:
            db.fijar_contador(int(anio), tipo, int(ultimo), u["nombre"], inicial=True)
            flash(request, f"Último número de {circuito.TIPOS_NUMERO[tipo].lower()} {anio}: {ultimo}. El próximo propuesto es {circuito.proponer_texto(tipo, int(anio))}.")
        return RedirectResponse(url=f"/numeracion?anio={anio}", status_code=303)

    @app.post("/numeracion/excel")
    async def numeracion_excel(request: Request, archivo: UploadFile = File(None), usar_configurado: str = Form("")):
        u = usuario_actual(request)
        exigir_rol(u, *auth.ROLES_MESA)
        try:
            if usar_configurado and circuito.CONFIG["excel_registro"]:
                maximos = circuito.leer_excel_registro(circuito.CONFIG["excel_registro"])
            elif archivo is not None and archivo.filename:
                maximos = circuito.leer_excel_registro(await archivo.read(), archivo.filename)
            else:
                flash(request, "⚠ Elegí el Excel de registro de notas.")
                return RedirectResponse(url="/numeracion", status_code=303)
        except Exception as e:
            flash(request, f"⚠ No se pudo leer el Excel: {e}")
            return RedirectResponse(url="/numeracion", status_code=303)
        if not maximos:
            flash(request, "⚠ No encontré en el Excel una columna de número de nota con su fecha o año.")
        else:
            request.session["excel_registro"] = [{"anio": a, "tipo": t, "ultimo": n} for (a, t), n in sorted(maximos.items(), reverse=True)]
        return RedirectResponse(url="/numeracion", status_code=303)

    @app.get("/numeracion/registro.xlsx")
    def registro_xlsx(request: Request, anio: int = 0):
        u = usuario_actual(request)
        exigir_rol(u, *auth.ROLES_MESA)
        anio = anio or date.today().year
        return Response(circuito.exportar_registro(anio), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="registro_notas_{anio}.xlsx"'})

    # ------------------------------------------------------------- 12) respuestas recibidas
    @app.post("/nota/{nota_id}/respuesta")
    async def cargar_respuesta(request: Request, nota_id: int, archivo: UploadFile = File(None)):
        u, n = nota_visible(request, nota_id)
        if not puede_cargar_respuesta(u, n):
            raise HTTPException(status_code=403, detail="No podés cargar respuestas de esta nota")
        form = await request.form()
        fecha = str(form.get("fecha") or "")[:10] or date.today().isoformat()
        try:
            date.fromisoformat(fecha)
        except ValueError:
            fecha = date.today().isoformat()
        ruta, nombre = None, None
        if archivo is not None and archivo.filename:
            datos = await archivo.read()
            if len(datos) > 25 * 1024 * 1024:
                flash(request, "⚠ El archivo supera 25 MB.")
                return RedirectResponse(url=f"/nota/{nota_id}#respuestas", status_code=303)
            nombre = Path(archivo.filename).name
            if Path(nombre).suffix.lower() not in (".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".eml", ".msg", ".odt", ".txt"):
                flash(request, "⚠ Tipo de archivo no permitido (PDF, Word, imagen o correo).")
                return RedirectResponse(url=f"/nota/{nota_id}#respuestas", status_code=303)
            carpeta = Path(carpeta_datos) / "respuestas" / str(nota_id)
            carpeta.mkdir(parents=True, exist_ok=True)
            ruta = carpeta / f"{date.today().isoformat()}_{re.sub(r'[^\w.\- ]', '_', nombre)}"
            ruta.write_bytes(datos)
        db.registrar_respuesta(nota_id, fecha, (form.get("medio") or "CORREO")[:20], str(form.get("remitente") or "")[:200].strip(),
                               str(form.get("resumen") or "")[:2000].strip(), str(ruta) if ruta else None, nombre)
        for sg in db.sugerencias_de(nota_id, "REITERACION"):
            db.resolver_sugerencia(sg["id"], "DESCARTADA", u["nombre"])
        flash(request, "Respuesta registrada. Se avisó a quien redactó la nota y a la coordinación del equipo.")
        return RedirectResponse(url=f"/nota/{nota_id}#respuestas", status_code=303)

    @app.get("/respuesta/{resp_id}/archivo")
    def descargar_respuesta(request: Request, resp_id: int):
        r = db.obtener_respuesta(resp_id)
        if not r or not r.get("archivo"):
            raise HTTPException(status_code=404, detail="Archivo no encontrado")
        nota_visible(request, r["nota_id"])
        if not Path(r["archivo"]).is_file():
            raise HTTPException(status_code=404, detail="El archivo ya no está en el servidor")
        return FileResponse(r["archivo"], filename=r.get("nombre_archivo") or Path(r["archivo"]).name)

    # ------------------------------------------------------------- 58/59) entrega en mano y viajes
    @app.post("/nota/{nota_id}/entrega")
    async def registrar_entrega(request: Request, nota_id: int):
        u, n = nota_visible(request, nota_id)
        if not (es_mesa_o_dir(u) or puede_gestionar(u, n) or tr.puede_coordinar(u)):
            raise HTTPException(status_code=403, detail="No podés registrar entregas de esta nota")
        form = await request.form()
        fecha = str(form.get("fecha_entrega") or "")[:10] or date.today().isoformat()
        viaje = str(form.get("viaje_id") or "")
        db.registrar_entrega_en_mano(nota_id, str(form.get("destinatario") or "").strip()[:200], str(form.get("entregado_por") or u["nombre"]).strip()[:120],
                                     fecha, int(viaje) if viaje.isdigit() else None, str(form.get("recibido_por") or "").strip()[:200] or None,
                                     str(form.get("observacion") or "").strip()[:500] or None)
        if viaje.isdigit():
            for d in db.documentos_de_viaje(int(viaje)):
                if d["nota_id"] == nota_id and d["estado"] == "PENDIENTE":
                    db.resolver_documento_viaje(d["id"], "ENTREGADO", u["nombre"])
        flash(request, "Entrega en mano registrada.")
        return RedirectResponse(url=f"/nota/{nota_id}#entregas", status_code=303)

    @app.post("/nota/{nota_id}/viaje")
    def asignar_viaje(request: Request, nota_id: int, viaje_id: int = Form(0)):
        u, n = nota_visible(request, nota_id)
        if not (es_mesa_o_dir(u) or puede_gestionar(u, n) or tr.puede_coordinar(u)):
            raise HTTPException(status_code=403, detail="No podés asignar esta nota a un viaje")
        v = tr.obtener_viaje(viaje_id)
        if not v or v["estado"] == "CANCELADO":
            raise HTTPException(status_code=404, detail="Viaje no encontrado")
        db.asignar_nota_a_viaje(viaje_id, nota_id, u["nombre"])
        quienes = [p["usuario_id"] for p in v.get("participantes", []) if p.get("usuario_id")]
        db.notificar_usuarios(quienes, f"Llevás para entregar en mano la nota «{n['nombre_original']}» en el viaje del "
                                       f"{v['fecha'][8:10]}/{v['fecha'][5:7]} a {v['destino']}.", f"/traslados/{viaje_id}")
        flash(request, f"La nota va en el viaje del {v['fecha'][8:10]}/{v['fecha'][5:7]} a {v['destino']}. Se avisó a quienes viajan.")
        return RedirectResponse(url=request.headers.get("referer") or f"/nota/{nota_id}", status_code=303)

    @app.post("/viaje-documento/{doc_id}/{accion}")
    def resolver_doc_viaje(request: Request, doc_id: int, accion: str, observacion: str = Form("")):
        u = usuario_actual(request)
        with db._conexion() as con:
            f = con.execute("SELECT * FROM viaje_documentos WHERE id = ?", (doc_id,)).fetchone()
        if not f or accion not in ("entregado", "no-entregado"):
            raise HTTPException(status_code=404, detail="No encontrado")
        v = tr.obtener_viaje(f["viaje_id"])
        viaja = any(p.get("usuario_id") == u["id"] for p in (v or {}).get("participantes", []))
        if not (viaja or tr.puede_coordinar(u) or es_mesa_o_dir(u)):
            raise HTTPException(status_code=403, detail="Solo quienes viajan o la coordinación")
        if accion == "entregado":
            db.resolver_documento_viaje(doc_id, "ENTREGADO", u["nombre"], observacion or None)
            texto = db.texto_indexado(f["nota_id"])
            dest = agentes.destinatario_sugerido(texto).get("destinatario") if texto else ""
            db.registrar_entrega_en_mano(f["nota_id"], dest, u["nombre"], date.today().isoformat(), f["viaje_id"], None, observacion or None)
            flash(request, "Entrega registrada en la nota.")
        else:
            db.resolver_documento_viaje(doc_id, "NO_ENTREGADO", u["nombre"], observacion or "No se pudo entregar")
            n = db.obtener_nota(f["nota_id"])
            db.notificar_usuarios([x["id"] for x in db.listar_usuarios() if x["activo"] and x["rol"] in auth.ROLES_MESA],
                                  f"No se pudo entregar en mano la nota «{n['nombre_original']}» en el viaje #{f['viaje_id']}: {observacion or 'sin detalle'}.",
                                  f"/nota/{f['nota_id']}")
            flash(request, "Quedó registrado que no se entregó; se avisó a Mesa de Entrada.")
        return RedirectResponse(url=f"/traslados/{f['viaje_id']}", status_code=303)

    templates.env.globals["documentos_de_viaje"] = db.documentos_de_viaje
    templates.env.globals["notas_esperando_entrega"] = db.notas_esperando_entrega

    # ------------------------------------------------------------- 61) plantillas con campos
    @app.get("/biblioteca/modelos/{modelo_id}/usar", response_class=HTMLResponse)
    def usar_modelo(request: Request, modelo_id: int):
        u = usuario_actual(request)
        m = db.obtener_modelo_nota(modelo_id)
        if not m or not tiene_notas(u):
            raise HTTPException(status_code=404, detail="Modelo no encontrado")
        try:
            campos = circuito.campos_de_plantilla(m["archivo"])
        except Exception:
            campos = []
        return pagina(request, "plantilla_usar.html", modelo=m, campos=campos, puede_borrador=u["rol"] in auth.ROLES_DE_EQUIPO and bool(u.get("equipo_id")))

    @app.post("/biblioteca/modelos/{modelo_id}/usar")
    async def completar_modelo(request: Request, modelo_id: int):
        u = usuario_actual(request)
        m = db.obtener_modelo_nota(modelo_id)
        if not m or not tiene_notas(u):
            raise HTTPException(status_code=404, detail="Modelo no encontrado")
        form = await request.form()
        campos = circuito.campos_de_plantilla(m["archivo"])
        valores = {c: str(form.get(f"campo_{i}") or "").strip() for i, c in enumerate(campos) if str(form.get(f"campo_{i}") or "").strip()}
        nombre = re.sub(r"[^\w\- ]", "", str(form.get("nombre") or m["nombre"]))[:80].strip() or "Nota"
        destino = Path(tempfile.mkdtemp()) / f"{nombre}.docx"
        circuito.completar_plantilla(m["archivo"], valores, destino)
        if form.get("accion") == "borrador" and u["rol"] in auth.ROLES_DE_EQUIPO and u.get("equipo_id"):
            archivo = types.SimpleNamespace(filename=destino.name, file=io.BytesIO(destino.read_bytes()))
            nota_id, motivo = guardar_subida(archivo, u, u["equipo_id"], None, "NORMAL", borrador=True)
            if not nota_id:
                flash(request, f"⚠ No se pudo crear el borrador: {motivo}")
                return RedirectResponse(url=f"/biblioteca/modelos/{modelo_id}/usar", status_code=303)
            db.registrar_evento(nota_id, f"Creada desde la plantilla «{m['nombre']}»")
            flash(request, "Borrador creado desde la plantilla. Revisalo y completá lo que falte antes de enviarlo.")
            return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)
        return FileResponse(str(destino), filename=destino.name,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # ------------------------------------------------------------- 63) carpeta por caso
    @app.get("/caso", response_class=HTMLResponse)
    def carpeta_caso(request: Request, tipo: str = "PERSONA", norm: str = "", valor: str = ""):
        u = usuario_actual(request)
        if not tiene_notas(u) or tipo not in ("PERSONA", "UNIDAD", "EXPEDIENTE", "GDE") or not norm:
            raise HTTPException(status_code=404, detail="Caso no encontrado")
        ids = db.notas_con_entidad(tipo, norm)
        import rutas_notas_ia  # propuestas 1 y 39: también lo que una persona confirmó (vínculo con la ficha, unidad del documento)
        ids = rutas_notas_ia.ids_confirmados_para_caso(tipo, norm, ids)
        notas = [n for n in (db.obtener_nota(i) for i in ids) if n and puede_ver(u, n)]
        notas.sort(key=lambda n: n["creado_en"])
        for n in notas:
            n["etapa"] = flujo.etapa_para_estado(n["estado"])
            n["respuestas"] = db.respuestas_de(n["id"])
            n["envios"] = db.envios_de(n["id"])
            n["viajes"] = db.viajes_de_nota(n["id"])
            n["proveidos"] = db.proveidos_de(n["id"])
        return pagina(request, "caso.html", tipo=tipo, valor=valor or norm, notas=notas)

    # ------------------------------------------------------------- 64) reiteración sugerida
    @app.get("/nota/{nota_id}/reiteracion", response_class=HTMLResponse)
    def ver_reiteracion(request: Request, nota_id: int):
        u, n = nota_visible(request, nota_id)
        texto = db.texto_indexado(nota_id)
        return pagina(request, "borrador_documento.html", nota=n, titulo="Nota de reiteración propuesta",
                      texto=agentes.reiteracion_reglas(n, texto), accion=f"/nota/{nota_id}/reiteracion", agente="REITERACION")

    @app.post("/nota/{nota_id}/reiteracion")
    async def usar_reiteracion(request: Request, nota_id: int):
        u, n = nota_visible(request, nota_id)
        form = await request.form()
        sugs = db.sugerencias_de(nota_id, "REITERACION")
        if form.get("accion") == "descartar":
            for sg in sugs:
                db.resolver_sugerencia(sg["id"], "DESCARTADA", u["nombre"])
            if not sugs:
                db.registrar_uso_agente(nota_id, "REITERACION", "DESCARTADA", u["nombre"])
            flash(request, "Reiteración descartada.")
            return RedirectResponse(url=f"/nota/{nota_id}#asistentes", status_code=303)
        texto = str(form.get("texto") or "").strip() or "—"
        ruta = Path(tempfile.mkdtemp()) / f"Reiteracion - {Path(n['nombre_original']).stem}.docx"
        agentes.proveido_docx(texto, ruta)
        for sg in sugs:
            db.resolver_sugerencia(sg["id"], "ACEPTADA", u["nombre"])
        if not sugs:
            db.registrar_uso_agente(nota_id, "REITERACION", "ACEPTADA", u["nombre"])
        db.registrar_evento(nota_id, f"Se descargó una nota de reiteración propuesta — {u['nombre']}")
        return FileResponse(str(ruta), filename=ruta.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # ------------------------------------------------------------- 29) vista con formato
    @app.get("/nota/{nota_id}/vista", response_class=HTMLResponse)
    def vista_documento(request: Request, nota_id: int, doc: str = "actual"):
        u, n = nota_visible(request, nota_id)
        import automatico
        rutas = {"original": n.get("ruta_base") or n.get("ruta_original"), "corregido": n.get("ruta_resultado") or n.get("ruta_analizada"),
                 "firmado": n.get("ruta_firmada"), "despacho": n.get("ruta_despacho_docx"), "actual": automatico.ruta_texto_actual(n)}
        ruta = rutas.get(doc) or rutas["actual"]
        if not ruta or not Path(ruta).is_file() or not str(ruta).lower().endswith(".docx"):
            return HTMLResponse('<p class="texto-ayuda">No hay una versión .docx para mostrar.</p>')
        try:
            cuerpo = circuito.docx_a_html(ruta)
        except Exception as e:
            return HTMLResponse(f'<p class="texto-ayuda">No se pudo mostrar el documento ({type(e).__name__}).</p>')
        return HTMLResponse(f'<div class="hoja-documento">{cuerpo}</div>')

    # ------------------------------------------------------------- 37) firma en serie
    def cola_firma(u):
        notas = [n for n in filtrar(db.listar_notas(), u) if n["estado"] == "PROCESADA"]
        anotar(notas)
        return ordenar_para_firma(notas)

    templates.env.globals["cola_firma_info"] = lambda u, nota_id: _posicion(cola_firma(u), nota_id)

    def _posicion(cola, nota_id):
        ids = [n["id"] for n in cola]
        return {"total": len(ids), "pos": (ids.index(nota_id) + 1) if nota_id in ids else None,
                "siguiente": next((i for i in ids if i != nota_id and (nota_id not in ids or ids.index(i) > ids.index(nota_id))), None)}

    @app.get("/firma-en-serie")
    def firma_en_serie(request: Request, despues: int = 0):
        u = usuario_actual(request)
        exigir_rol(u)
        cola = cola_firma(u)
        ids = [n["id"] for n in cola]
        if despues in ids:
            ids = ids[ids.index(despues) + 1:] or []
            request.session["serie_salteadas"] = sorted(set(request.session.get("serie_salteadas") or []) | {despues})  # propuesta 107
        if not ids:
            return RedirectResponse(url="/firma-terminada", status_code=303)
        if not despues:
            request.session.pop("serie_firmadas", None)  # arranca una tanda nueva
            request.session.pop("serie_salteadas", None)
        return RedirectResponse(url=f"/nota/{ids[0]}?serie=1", status_code=303)

    @app.post("/nota/{nota_id}/firmar-y-seguir")
    def firmar_y_seguir(request: Request, nota_id: int, fecha: str = Form(None)):
        u = usuario_actual(request)
        cola_antes = [n["id"] for n in cola_firma(u)]
        actual = db.obtener_nota(nota_id) or {}
        nombre = actual.get("nombre_original", "")
        import main as _main  # misma lógica que «Firmar»: siguiente pendiente o pantalla de fin
        if _main._ya_firmada(actual):  # «Atrás» o doble envío: no se firma de nuevo ni se muestra un error
            _main._flash(request, f"«{nombre}» ya estaba firmada.")
            restantes = [i for i in cola_antes if i != nota_id]
            return RedirectResponse(url=f"/nota/{restantes[0]}?serie=1" if restantes else "/firma-terminada", status_code=303)
        firmar(u, nota_id, fecha)
        return RedirectResponse(url=_main._despues_de_firmar(request, nota_id, cola_antes, nombre, nota_firmada=actual), status_code=303)

    # ------------------------------------------------------------- 66) PDF/A
    @app.get("/nota/{nota_id}/pdfa")
    def pdfa_nota(request: Request, nota_id: int):
        u, n = nota_visible(request, nota_id)
        origen = n.get("ruta_despacho_docx") if n.get("ruta_despacho_docx") and Path(n["ruta_despacho_docx"]).is_file() else n.get("ruta_firmada")
        if not origen or not Path(origen).is_file():
            raise HTTPException(status_code=409, detail="La nota todavía no tiene documento firmado")
        destino = Path(carpeta_datos) / "archivo" / f"{nota_id}.pdf"
        if not destino.is_file() or destino.stat().st_mtime < Path(origen).stat().st_mtime:
            try:
                circuito.convertir_a_pdfa(origen, destino)
            except RuntimeError as e:
                raise HTTPException(status_code=500, detail=str(e))
            db.registrar_evento(nota_id, "Se generó el PDF/A para el archivo")
        nombre = re.sub(r"[^\w\- ]", "_", Path(n["nombre_original"]).stem) + " (archivo PDF-A).pdf"
        return FileResponse(str(destino), filename=nombre, media_type="application/pdf")

    # ------------------------------------------------------------- 87) informe del período
    @app.get("/reportes/informe.docx")
    def informe_periodo(request: Request, periodo: str = ""):
        u = usuario_actual(request)
        exigir_rol(u, auth.ROL_COORDINADOR)
        rango = circuito.periodo(periodo)
        if not rango:
            raise HTTPException(status_code=422, detail="Período no válido (ej. 2026-T3 o 2026)")
        desde, hasta, _ = rango
        equipo_id = None if auth.es_admin(u) else u.get("equipo_id")
        import reportes
        datos = {"reporte": reportes.generar_reporte(desde=desde, hasta=hasta + "T23:59:59", equipo_id=equipo_id),
                 "tiempos": circuito.tiempos_por_equipo(desde, hasta, equipo_id),
                 "por_pedido": db.estadisticas_entidades("PEDIDO", desde, hasta + "T23:59:59", equipo_id),
                 "por_unidad": db.estadisticas_entidades("UNIDAD", desde, hasta + "T23:59:59", equipo_id),
                 "respuestas": db.resumen_respuestas(desde, hasta)}
        if auth.es_admin(u):
            try:
                est = tr.estadisticas(desde, hasta, tr.alcance_estadisticas(u))
                datos["traslados"] = {"desplazamientos": est.get("desplazamientos", 0), "viajes_unidad": est.get("viajes_unidad", 0),
                                      "por_unidad": est.get("por_unidad", [])}
            except Exception:
                datos["traslados"] = None
        contenido = circuito.informe_docx(periodo, datos)
        return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="Informe DGPDH {periodo}.docx"'})
