"""Pantallas del Registro de Obstaculizaciones (carga, consulta con filtros, ficha, adjuntos, exportación, informe a la firma y ABM
de tipos). La lógica vive en dgpdh/ (dominio y servicios); acá solo se arma la persona, se lee el formulario y se muestra."""
import csv
import io
import logging
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

import auth
import database as db
from dgpdh.dominio import obstaculizaciones as dom
from dgpdh.errores import ErrorDGPDH
from dgpdh.servicios import obstaculizaciones as srv

PERIODO_DEFECTO_DIAS = 365
MAX_ADJUNTOS_POR_ENVIO = 10


def persona(u, tiene_notas):
    return dom.Persona(id=u["id"], nombre=u["nombre"], rol=u["rol"], equipo_id=u.get("equipo_id"), es_direccion=auth.es_admin(u),
                       consulta=bool(u.get("consulta_obstaculizaciones")), tiene_notas=bool(tiene_notas(u)))


def puede_usar(u, tiene_notas):
    p = persona(u, tiene_notas)
    return p.puede_cargar or p.ve_todo


def _int(v):
    s = str(v or "").strip()
    return int(s) if s.isdigit() else None


def _filtros(q):
    sub = {"SI": True, "NO": False}.get(str(q.get("subsanado") or "").upper())
    return srv.Filtros(desde=q.get("desde") or None, hasta=q.get("hasta") or None, unidad_id=_int(q.get("unidad_id")), tipo_id=_int(q.get("tipo_id")),
                       equipo_id=_int(q.get("equipo_id")), fuerza_id=_int(q.get("fuerza_id")), subsanado=sub,
                       vinculo=(q.get("vinculo") or None), estado={"ANULADO": "ANULADO", "TODOS": None}.get(q.get("estado") or "", "REGISTRADO"),
                       texto=(q.get("q") or None))


def registrar(app, ctx):
    templates, usuario_actual, flash, tiene_notas = ctx["templates"], ctx["usuario_actual"], ctx["flash"], ctx["tiene_notas"]
    carpeta = Path(ctx["carpeta_datos"]) / "obstaculizaciones"
    max_bytes = ctx["max_bytes"]
    templates.env.globals["obst_puede_usar"] = lambda u: bool(u) and puede_usar(u, tiene_notas)

    def _cuenta_mes(u, desde):  # estado del panel del menú
        if not puede_usar(u, tiene_notas):
            return None
        with db._conexion() as con:
            return len(srv.listar(con, persona(u, tiene_notas), srv.Filtros(desde=desde), limite=1000))
    import menu_paneles
    menu_paneles.OBSTACULIZACIONES = _cuenta_mes

    def exigir(request):
        u = usuario_actual(request)
        if not puede_usar(u, tiene_notas):
            raise HTTPException(status_code=403, detail="No tenés acceso al Registro de Obstaculizaciones.")
        return u, persona(u, tiene_notas)

    def error(e):
        if isinstance(e, ErrorDGPDH):
            raise HTTPException(status_code=e.codigo_http, detail=e.mensaje) from e
        raise e

    def catalogos(con, p):
        return {"tipos": srv.listar_tipos(con), "tipos_todos": srv.listar_tipos(con, False), "unidades": db.listar_unidades(),
                "equipos": db.listar_equipos(), "fuerzas": [dict(f) for f in con.execute("SELECT id, nombre, sigla FROM fuerzas WHERE activa = 1 ORDER BY nombre")],
                "usuarios": [x for x in db.listar_usuarios() if x["activo"] and x["rol"] != "CHOFER"]}

    def pagina(request, nombre, **c):
        u = usuario_actual(request)
        return templates.TemplateResponse(nombre, dict(c, request=request, usuario=u, CLASES_VINCULO=dom.CLASES_VINCULO))

    # ------------------------------------------------------------ consulta
    @app.get("/obstaculizaciones", response_class=HTMLResponse)
    def obst_lista(request: Request):
        u, p = exigir(request)
        q = dict(request.query_params)
        if "desde" not in q:
            q["desde"] = date.fromordinal(date.today().toordinal() - PERIODO_DEFECTO_DIAS).isoformat()
        with db._conexion() as con:
            filas = srv.listar(con, p, _filtros(q))
            cat = catalogos(con, p)
        return pagina(request, "obstaculizaciones.html", filas=filas[:500], total=len(filas), resumen=srv.resumen(filas), q=q,
                      qs=urlencode({k: v for k, v in q.items() if v}), p=p, **cat)

    @app.get("/obstaculizaciones/exportar.{formato}")
    def obst_exportar(request: Request, formato: str):
        u, p = exigir(request)
        if formato not in ("csv", "xlsx", "pdf"):
            raise HTTPException(status_code=404, detail="Formato no disponible (csv, xlsx o pdf).")
        with db._conexion() as con:
            filas = srv.listar(con, p, _filtros(dict(request.query_params)))
        columnas = [("numero", "Número"), ("fecha_hecho", "Fecha"), ("hora_hecho", "Hora"), ("lugar", "Establecimiento / dependencia"),
                    ("sector", "Pabellón / sector"), ("tipo_nombre", "Tipo"), ("tipos_extra", "Otros tipos"), ("equipo_nombre", "Equipo"),
                    ("agentes", "Intervinientes"), ("fuerza_nombre", "Fuerza"), ("autoridad", "Autoridad responsable"),
                    ("subsanado", "¿Se subsanó en el lugar?"), ("como_se_subsano", "Cómo"), ("demora_minutos", "Demora (min)"),
                    ("descripcion", "Descripción"), ("vinculos", "Vínculos"), ("adjuntos", "Adjuntos"), ("cargado_por", "Cargó"), ("estado", "Estado")]

        def valor(f, k):
            v = f.get(k)
            if k == "subsanado":
                return "Sí" if v else "No"
            if k in ("agentes", "tipos_extra"):
                return ", ".join(v or [])
            if k == "vinculos":
                v = "; ".join(f"{dom.CLASES_VINCULO.get(x['clase'], x['clase'])} {x['ref_id'] or x['ref_texto'] or ''}".strip() for x in v or [])
            if v is None:
                return ""
            if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + v  # un texto no puede convertirse en fórmula al abrirlo en Excel (inyección de fórmulas)
            return v
        nombre = f"obstaculizaciones_{date.today().isoformat()}"
        from dgpdh.seguridad import auditoria as au
        with db._conexion() as con:
            au.registrar(con, accion=f"Exportación ({formato})", entidad="obstaculizaciones", despues={"registros": len(filas),
                         "filtros": dict(request.query_params)}, actor=au.Actor(u["id"], u["nombre"]))
        if formato == "csv":
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow([t for _, t in columnas])
            for f in filas:
                w.writerow([valor(f, k) for k, _ in columnas])
            return Response(("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f"attachment; filename={nombre}.csv"})
        if formato == "xlsx":
            import openpyxl
            from openpyxl.styles import Font
            libro = openpyxl.Workbook()
            h = libro.active
            h.title = "Obstaculizaciones"
            h.append([t for _, t in columnas])
            for c in h[1]:
                c.font = Font(bold=True)
            for f in filas:
                h.append([valor(f, k) for k, _ in columnas])
            h.freeze_panes = "A2"
            h.auto_filter.ref = h.dimensions
            r = srv.resumen(filas)
            h2 = libro.create_sheet("Resumen")
            h2.append(["Total", r["total"]])
            h2.append(["Subsanadas en el lugar", r["subsanadas"]])
            h2.append(["Demora media (min)", r["demora_media"] or ""])
            h2.append([])
            h2.append(["Por tipo", "Cantidad"])
            for t, n in r["por_tipo"]:
                h2.append([t, n])
            h2.append([])
            h2.append(["Por establecimiento", "Cantidad"])
            for t, n in r["por_unidad"]:
                h2.append([t, n])
            buf = io.BytesIO()
            libro.save(buf)
            return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f"attachment; filename={nombre}.xlsx"})
        # PDF: tabla en Word apaisado y conversión (Word o LibreOffice); sin conversor, se entrega el .docx
        import despacho
        from docx import Document
        from docx.enum.section import WD_ORIENT
        from docx.shared import Pt
        doc = Document()
        s = doc.sections[0]
        s.orientation, s.page_width, s.page_height = WD_ORIENT.LANDSCAPE, s.page_height, s.page_width
        doc.add_heading("Registro de Obstaculizaciones", level=1)
        doc.add_paragraph(f"{len(filas)} registro(s) · generado el {date.today():%d/%m/%Y} por {u['nombre']}. Documento de uso interno.")
        cols = [("numero", "Número"), ("fecha_hecho", "Fecha"), ("lugar", "Establecimiento"), ("tipo_nombre", "Tipo"), ("equipo_nombre", "Equipo"),
                ("fuerza_nombre", "Fuerza"), ("subsanado", "Subsanado"), ("descripcion", "Descripción")]
        t = doc.add_table(rows=1, cols=len(cols))
        t.style = "Table Grid"
        for i, (_, titulo) in enumerate(cols):
            t.rows[0].cells[i].text = titulo
        for f in filas:
            celdas = t.add_row().cells
            for i, (k, _) in enumerate(cols):
                celdas[i].text = str(valor(f, k))[:600]
        for fila in t.rows:
            for c in fila.cells:
                for par in c.paragraphs:
                    for run in par.runs:
                        run.font.size = Pt(8)
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / f"{nombre}.docx"
            doc.save(ruta)
            nombre_f, contenido, tipo = f"{nombre}.docx", ruta.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            try:
                despacho.convertir_a_pdf(str(ruta), str(Path(tmp) / f"{nombre}.pdf"))
                nombre_f, contenido, tipo = f"{nombre}.pdf", (Path(tmp) / f"{nombre}.pdf").read_bytes(), "application/pdf"
            except Exception as e:
                logging.warning(f"Obstaculizaciones: no se pudo pasar a PDF, se entrega el .docx ({e})")
        return Response(contenido, media_type=tipo, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre_f)}"})

    # ------------------------------------------------------------ carga y edición
    def leer_formulario(form):
        agentes = []
        for uid in form.getlist("agentes_ids"):
            if str(uid).isdigit():
                x = db.obtener_usuario(int(uid))
                if x:
                    agentes.append((x["id"], x["nombre"]))
        for nombre in re.split(r"[,;\n]+", str(form.get("agentes_otros") or "")):
            if nombre.strip():
                agentes.append((None, nombre.strip()))
        vinculos = []
        if str(form.get("viaje_id") or "").strip().isdigit():
            vinculos.append(("VIAJE", int(form.get("viaje_id")), None))
        if str(form.get("nota_id") or "").strip().isdigit():
            vinculos.append(("NOTA", int(form.get("nota_id")), None))
        if str(form.get("expediente") or "").strip():
            vinculos.append(("EXPEDIENTE", None, str(form.get("expediente")).strip()))
        d = {k: form.get(k) for k in ("fecha_hecho", "hora_hecho", "unidad_id", "lugar_otro", "sector", "tipo_id", "equipo_id", "fuerza_id",
                                      "autoridad", "descripcion", "subsanado", "como_se_subsano", "demora_minutos")}
        d.update(tipos_extra=form.getlist("tipos_extra"), agentes=agentes, vinculos=vinculos)
        return d

    def valores_de(f):
        return {"fecha_hecho": f["fecha_hecho"], "hora_hecho": f["hora_hecho"], "unidad_id": f.get("unidad_id"), "lugar_otro": f.get("lugar_otro") or "",
                "sector": f.get("sector") or "", "tipo_id": f["tipo_id"], "tipos_extra": f.get("tipos_extra_ids", []), "equipo_id": f.get("equipo_id"),
                "fuerza_id": f.get("fuerza_id"), "autoridad": f.get("autoridad") or "", "descripcion": f["descripcion"],
                "subsanado": "SI" if f["subsanado"] else "NO", "como_se_subsano": f.get("como_se_subsano") or "", "demora_minutos": f.get("demora_minutos") or "",
                "agentes_ids": [a["usuario_id"] for a in f.get("agentes_detalle", []) if a["usuario_id"]],
                "agentes_otros": ", ".join(a["nombre"] for a in f.get("agentes_detalle", []) if not a["usuario_id"]),
                "viaje_id": next((v["ref_id"] for v in f["vinculos"] if v["clase"] == "VIAJE"), ""),
                "nota_id": next((v["ref_id"] for v in f["vinculos"] if v["clase"] == "NOTA"), ""),
                "expediente": next((v["ref_texto"] for v in f["vinculos"] if v["clase"] == "EXPEDIENTE"), "")}

    def desde_viaje(viaje_id, u):
        """Precarga desde un viaje realizado: fecha, unidad, equipo, personas y vínculo."""
        import traslados as tr
        v = tr.obtener_viaje(viaje_id)
        if not v:
            return {}
        ids = [x["usuario_id"] for x in v["participantes"] if x.get("usuario_id")]
        otros = [x["nombre"] for x in v["participantes"] if not x.get("usuario_id")]
        fuerza = None
        if v.get("unidad_id"):
            un = db.obtener_unidad(v["unidad_id"]) or {}
            with db._conexion() as con:
                f = con.execute("SELECT id FROM fuerzas WHERE ? != '' AND (lower(nombre) = lower(?) OR lower(sigla) = lower(?))",
                                (un.get("fuerza") or "", un.get("fuerza") or "", un.get("fuerza") or "")).fetchone()
            fuerza = f[0] if f else None
        return {"fecha_hecho": v["fecha"], "hora_hecho": v.get("hora_salida") or "", "unidad_id": v.get("unidad_id"), "lugar_otro": v.get("destino_otro") or "",
                "equipo_id": u.get("equipo_id"), "agentes_ids": ids, "agentes_otros": ", ".join(otros), "viaje_id": viaje_id, "fuerza_id": fuerza}

    @app.get("/obstaculizaciones/nueva", response_class=HTMLResponse)
    def obst_nueva(request: Request, viaje_id: str = ""):
        u, p = exigir(request)
        if not p.puede_cargar:
            raise HTTPException(status_code=403, detail="No tenés habilitada la carga.")
        valores = {"fecha_hecho": date.today().isoformat(), "hora_hecho": datetime.now().strftime("%H:%M"), "equipo_id": u.get("equipo_id"),
                   "agentes_ids": [u["id"]], "subsanado": ""}
        if viaje_id.isdigit():
            valores.update({k: v for k, v in desde_viaje(int(viaje_id), u).items() if v not in (None, "")})
        with db._conexion() as con:
            cat = catalogos(con, p)
        return pagina(request, "obstaculizacion_form.html", v=valores, accion="/obstaculizaciones/nueva", titulo="Registrar obstaculización", **cat)

    @app.post("/obstaculizaciones/nueva", response_class=HTMLResponse)
    async def obst_crear(request: Request):
        u, p = exigir(request)
        form = await request.form()
        d = leer_formulario(form)
        try:
            with db._conexion() as con:
                c = dom.validar(d, tipos_activos=srv.ids_tipos_activos(con))
                oid, numero = srv.crear(con, p, c, circuito=u.get("circuito"))
            errores_adj = await guardar_adjuntos(form, p, oid)
        except ErrorDGPDH as e:
            with db._conexion() as con:
                cat = catalogos(con, p)
            v = dict(d, agentes_ids=[a for a, _ in d["agentes"] if a], agentes_otros=form.get("agentes_otros") or "",
                     viaje_id=form.get("viaje_id") or "", nota_id=form.get("nota_id") or "", expediente=form.get("expediente") or "")
            return pagina(request, "obstaculizacion_form.html", v=v, error=e.mensaje, accion="/obstaculizaciones/nueva",
                          titulo="Registrar obstaculización", **cat)
        flash(request, f"Obstaculización registrada: {numero}." + (" " + " ".join(errores_adj) if errores_adj else "")
              + " Cuando esté completa, generá el informe para la firma.")
        return RedirectResponse(url=f"/obstaculizaciones/{oid}", status_code=303)

    async def guardar_adjuntos(form, p, oid):
        errores = []
        for n_archivo, archivo in enumerate(form.getlist("adjuntos")):
            if n_archivo >= MAX_ADJUNTOS_POR_ENVIO:
                errores.append(f"⚠ Se adjuntan hasta {MAX_ADJUNTOS_POR_ENVIO} archivos por vez: el resto no se tomó.")
                break
            if not getattr(archivo, "filename", None):
                continue
            if len(errores) + 1 > MAX_ADJUNTOS_POR_ENVIO:
                errores.append(f"⚠ Se adjuntan hasta {MAX_ADJUNTOS_POR_ENVIO} archivos por vez.")
                break
            contenido = await archivo.read(max_bytes + 1)  # nunca más del máximo en memoria
            if len(contenido) > max_bytes:
                errores.append(f"⚠ «{archivo.filename}» no se adjuntó: supera el máximo de {max_bytes // (1024 * 1024)} MB.")
                continue
            try:
                with db._conexion() as con:
                    srv.agregar_adjunto(con, p, oid, archivo.filename, contenido, carpeta, max_bytes)
            except ErrorDGPDH as e:
                errores.append(f"⚠ «{archivo.filename}» no se adjuntó: {e.mensaje}")
        return errores

    @app.get("/obstaculizaciones/{oid:int}", response_class=HTMLResponse)
    def obst_ficha(request: Request, oid: int):
        u, p = exigir(request)
        from dgpdh.seguridad import auditoria as au
        try:
            with db._conexion() as con:
                f = srv.obtener(con, p, oid)
                reg = srv.registro_de(con, oid)
                hist = au.historial(con, "obstaculizacion", oid)
        except ErrorDGPDH as e:
            error(e)
        ahora = datetime.now(timezone.utc)
        nota = db.obtener_nota(f["informe_nota_id"]) if f.get("informe_nota_id") else None
        return pagina(request, "obstaculizacion_ficha.html", f=f, historial=hist, nota=nota,
                      puede_editar=dom.puede_editar(p, reg, ahora), puede_anular=dom.puede_anular(p, reg, ahora),
                      puede_informe=dom.puede_generar_informe(p, reg))

    @app.get("/obstaculizaciones/{oid:int}/editar", response_class=HTMLResponse)
    def obst_editar_form(request: Request, oid: int):
        u, p = exigir(request)
        try:
            with db._conexion() as con:
                f = srv.obtener(con, p, oid)
                if not dom.puede_editar(p, srv.registro_de(con, oid), datetime.now(timezone.utc)):
                    raise HTTPException(status_code=403, detail="Ya no podés modificar este registro.")
                cat = catalogos(con, p)
        except ErrorDGPDH as e:
            error(e)
        return pagina(request, "obstaculizacion_form.html", v=valores_de(f), accion=f"/obstaculizaciones/{oid}/editar",
                      titulo=f"Modificar {f['numero']}", f=f, **cat)

    @app.post("/obstaculizaciones/{oid:int}/editar", response_class=HTMLResponse)
    async def obst_editar(request: Request, oid: int):
        u, p = exigir(request)
        form = await request.form()
        d = leer_formulario(form)
        try:
            with db._conexion() as con:
                tipos = srv.ids_tipos_activos(con) | _tipos_actuales(con, oid)
                srv.editar(con, p, oid, dom.validar(d, tipos_activos=tipos))
            errores_adj = await guardar_adjuntos(form, p, oid)
        except ErrorDGPDH as e:
            if e.codigo_http == 403:
                error(e)
            with db._conexion() as con:
                cat = catalogos(con, p)
                f = srv.obtener(con, p, oid)
            v = dict(d, agentes_ids=[a for a, _ in d["agentes"] if a], agentes_otros=form.get("agentes_otros") or "",
                     viaje_id=form.get("viaje_id") or "", nota_id=form.get("nota_id") or "", expediente=form.get("expediente") or "")
            return pagina(request, "obstaculizacion_form.html", v=v, error=e.mensaje, accion=f"/obstaculizaciones/{oid}/editar",
                          titulo=f"Modificar {f['numero']}", f=f, **cat)
        flash(request, "Cambios guardados (quedan en el historial)." + (" " + " ".join(errores_adj) if errores_adj else ""))
        return RedirectResponse(url=f"/obstaculizaciones/{oid}", status_code=303)

    def _tipos_actuales(con, oid):
        """Un registro viejo puede tener tipos ya dados de baja: se los deja conservar al editar."""
        ids = {int(r[0]) for r in con.execute("SELECT tipo_id FROM obstaculizaciones WHERE id = ?", (oid,))}
        return ids | {int(r[0]) for r in con.execute("SELECT tipo_id FROM obstaculizacion_tipos_extra WHERE obstaculizacion_id = ?", (oid,))}

    @app.post("/obstaculizaciones/{oid:int}/anular")
    async def obst_anular(request: Request, oid: int):
        u, p = exigir(request)
        form = await request.form()
        try:
            with db._conexion() as con:
                srv.anular(con, p, oid, str(form.get("motivo") or ""))
        except ErrorDGPDH as e:
            flash(request, "⚠ " + e.mensaje)
            return RedirectResponse(url=f"/obstaculizaciones/{oid}", status_code=303)
        flash(request, "Registro anulado. Sigue visible en la consulta con el filtro «Anulados».")
        return RedirectResponse(url=f"/obstaculizaciones/{oid}", status_code=303)

    @app.post("/obstaculizaciones/{oid:int}/adjuntos")
    async def obst_adjuntar(request: Request, oid: int):
        u, p = exigir(request)
        form = await request.form()
        try:
            with db._conexion() as con:
                srv.obtener(con, p, oid)
        except ErrorDGPDH as e:
            error(e)
        errores = await guardar_adjuntos(form, p, oid)
        flash(request, " ".join(errores) if errores else "Adjunto(s) agregado(s).")
        return RedirectResponse(url=f"/obstaculizaciones/{oid}#adjuntos", status_code=303)

    @app.get("/obstaculizaciones/{oid:int}/adjuntos/{aid:int}")
    def obst_descargar(request: Request, oid: int, aid: int):
        u, p = exigir(request)
        try:
            with db._conexion() as con:
                a = srv.adjunto(con, p, oid, aid)
        except ErrorDGPDH as e:
            error(e)
        if not Path(a["ruta"]).is_file():
            raise HTTPException(status_code=404, detail="El archivo no está en el servidor.")
        return FileResponse(a["ruta"], media_type=a["tipo_mime"] or "application/octet-stream", filename=a["nombre_original"])

    @app.post("/obstaculizaciones/{oid:int}/adjuntos/{aid:int}/quitar")
    def obst_quitar_adjunto(request: Request, oid: int, aid: int):
        u, p = exigir(request)
        try:
            with db._conexion() as con:
                srv.desactivar_adjunto(con, p, oid, aid)
        except ErrorDGPDH as e:
            flash(request, "⚠ " + e.mensaje)
            return RedirectResponse(url=f"/obstaculizaciones/{oid}#adjuntos", status_code=303)
        flash(request, "Adjunto quitado del registro (el archivo se conserva y queda en el historial).")
        return RedirectResponse(url=f"/obstaculizaciones/{oid}#adjuntos", status_code=303)

    # ------------------------------------------------------------ informe a la firma
    @app.post("/obstaculizaciones/{oid:int}/informe")
    def obst_informe(request: Request, oid: int):
        """Arma el informe (.docx) y lo crea como documento en borrador de quien lo pide, ya clasificado como
        «Informe de obstaculización»: desde ahí sigue el circuito de Notas (coordinación → firma → protocolo INF-…)."""
        u, p = exigir(request)
        import clasificacion_notas
        try:
            with db._conexion() as con:
                f = srv.obtener(con, p, oid)
                if not dom.puede_generar_informe(p, srv.registro_de(con, oid)):
                    raise HTTPException(status_code=403, detail="No podés generar el informe de este registro (ya tiene informe o está anulado).")
        except ErrorDGPDH as e:
            error(e)
        equipo_id = f.get("equipo_id") or u.get("equipo_id")
        if not equipo_id:
            flash(request, "⚠ Para generar el informe el registro tiene que tener un equipo: editalo y elegí el equipo.")
            return RedirectResponse(url=f"/obstaculizaciones/{oid}", status_code=303)
        with db._conexion() as con:  # un solo informe por hecho, aunque lleguen dos pedidos a la vez
            if not srv.reservar_informe(con, oid):
                flash(request, "⚠ Este registro ya tiene un informe (o se está generando).")
                return RedirectResponse(url=f"/obstaculizaciones/{oid}", status_code=303)
        try:
            destino = carpeta / "informes" / f"Informe de obstaculización {f['numero']}.docx"
            destino.parent.mkdir(parents=True, exist_ok=True)
            armar_informe(f, destino)
            nota_id = db.crear_nota(destino.name, equipo_id, str(destino), usuario_id=u["id"], borrador=True)
            expediente = next((v["ref_texto"] for v in f["vinculos"] if v["clase"] == "EXPEDIENTE"), None)
            clasificacion_notas.guardar(nota_id, {"tipo": "INFORME", "subtipo": "OBSTACULIZACION", "alcance": "COLECTIVO", "confidencialidad": "INTERNO",
                                                  "destinatario": "Dirección General de Protección de Derechos Humanos", "expediente": expediente}, u)
            db.registrar_evento(nota_id, f"Informe generado desde el Registro de Obstaculizaciones ({f['numero']})")
            with db._conexion() as con:
                srv.marcar_informe(con, p, oid, nota_id)
        except Exception:
            with db._conexion() as con:
                srv.liberar_informe(con, oid)
            raise
        flash(request, f"Informe de {f['numero']} creado como borrador y ya clasificado. Revisalo y envialo a la firma.")
        return RedirectResponse(url=f"/nota/{nota_id}", status_code=303)

    # ------------------------------------------------------------ ABM de tipos
    @app.get("/obstaculizaciones/tipos", response_class=HTMLResponse)
    def obst_tipos(request: Request):
        u, p = exigir(request)
        if not p.puede_administrar_tipos:
            raise HTTPException(status_code=403, detail="Solo la Dirección administra los tipos.")
        with db._conexion() as con:
            tipos = srv.listar_tipos(con, False)
        return pagina(request, "obstaculizaciones_tipos.html", tipos=tipos)

    @app.post("/obstaculizaciones/tipos")
    async def obst_tipos_guardar(request: Request):
        u, p = exigir(request)
        form = await request.form()
        try:
            with db._conexion() as con:
                accion = form.get("accion")
                tid = _int(form.get("tipo_id"))
                if accion in ("baja", "reactivar") and tid:
                    srv.cambiar_estado_tipo(con, p, tid, accion == "reactivar")
                else:
                    srv.guardar_tipo(con, p, str(form.get("nombre") or ""), str(form.get("descripcion") or ""), tid, _int(form.get("orden")))
        except ErrorDGPDH as e:
            if e.codigo_http == 403:
                error(e)
            flash(request, "⚠ " + e.mensaje)
            return RedirectResponse(url="/obstaculizaciones/tipos", status_code=303)
        flash(request, "Tipos actualizados (los registros anteriores conservan su tipo).")
        return RedirectResponse(url="/obstaculizaciones/tipos", status_code=303)


def armar_informe(f, destino):
    """Informe institucional del hecho (uno por registro)."""
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    estilo = doc.styles["Normal"]
    estilo.font.size = Pt(11)
    doc.add_heading("Informe de obstaculización", level=1)
    doc.add_paragraph(f"Registro {f['numero']} · Procuración Penitenciaria de la Nación · Dirección General de Protección de Derechos Humanos")
    datos = [("Fecha y hora del hecho", f"{f['fecha_hecho'][8:10]}/{f['fecha_hecho'][5:7]}/{f['fecha_hecho'][:4]} a las {f['hora_hecho']} h"),
             ("Establecimiento / dependencia", f["lugar"]), ("Pabellón o sector", f.get("sector") or "—"),
             ("Tipo de obstaculización", f["tipo_nombre"] + (f" (además: {', '.join(f['tipos_extra'])})" if f.get("tipos_extra") else "")),
             ("Equipo interviniente", f.get("equipo_nombre") or "—"), ("Agentes intervinientes", ", ".join(f.get("agentes") or []) or "—"),
             ("Fuerza o autoridad responsable", " · ".join(x for x in (f.get("fuerza_nombre"), f.get("autoridad")) if x) or "—"),
             ("¿Se pudo subsanar en el lugar?", ("Sí. " + (f.get("como_se_subsano") or "")) if f["subsanado"] else "No"),
             ("Demora", f"{f['demora_minutos']} minutos" if f.get("demora_minutos") is not None else "—"),
             ("Vínculos", "; ".join(f"{dom.CLASES_VINCULO.get(v['clase'], v['clase'])} {v['ref_id'] or v['ref_texto'] or ''}".strip() for v in f.get("vinculos") or []) or "—")]
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for k, v in datos:
        celdas = t.add_row().cells
        celdas[0].text, celdas[1].text = k, str(v)
    doc.add_heading("Descripción del hecho", level=2)
    for parrafo in str(f["descripcion"]).splitlines():
        if parrafo.strip():
            doc.add_paragraph(parrafo.strip())
    activos = [a for a in f.get("lista_adjuntos") or [] if a["activo"]]
    if activos:
        doc.add_heading("Evidencia adjunta", level=2)
        for a in activos:
            doc.add_paragraph(f"{a['nombre_original']} — huella SHA-256: {a['sha256']}", style="List Bullet")
    doc.add_paragraph(f"Cargado por {f['cargado_por']} el {f['creado_en'][8:10]}/{f['creado_en'][5:7]}/{f['creado_en'][:4]}.")
    doc.save(str(destino))
