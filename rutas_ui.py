"""Interfaz y experiencia de usuario (propuestas 98–164): ayudas para las plantillas (textos claros, tiempos relativos, grupos
de urgencia, preferencias de pantalla) y las rutas propias del área (preferencias, sugerencias, borradores, paleta de comandos,
salidas del chofer, aprobación por lote, accesibilidad). Se registra al final de main.py con `registrar(app, ctx)`."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import database as db
import fechas
from dgpdh.dominio import interfaz as dom
from dgpdh.errores import ErrorDGPDH
from dgpdh.servicios import interfaz as srv

destino_interno = dom.destino_interno  # main.py lo usa al volver del login
DIAS_CORTOS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


# ---------------------------------------------------------------- filtros de plantilla
def fecha_dia(valor):
    """'2026-11-16' -> 'lun 16/11' (propuesta 119). Sin año si es del año en curso."""
    if not valor:
        return "—"
    try:
        d = date.fromisoformat(str(valor)[:10])
    except ValueError:
        return str(valor)
    return f"{DIAS_CORTOS[d.weekday()]} {d:%d/%m}" + ("" if d.year == date.today().year else f"/{d.year}")


def fecha_larga(valor):
    """ISO con hora -> '16/11/2026 10:20' en la hora local del organismo."""
    return fechas.a_hora_local(valor)


def preferencias_de(usuario, request=None):
    """Preferencias efectivas de la persona; con `request` se leen una sola vez por pedido."""
    estado = getattr(request, "state", None)
    guardadas = getattr(estado, "ui_pref", None) if estado is not None else None
    if guardadas is not None:
        return guardadas
    pref = _leer_preferencias(usuario)
    if estado is not None:
        try:
            estado.ui_pref = pref
        except AttributeError:
            pass
    return pref


AYUDA_POR_RUTA = (  # propuesta 144 (U47): la sección del manual que corresponde a cada pantalla
    ("/traslados/nuevo", "solicitar-viaje"), ("/traslados/hoy", "choferes"), ("/traslados/asignar", "asignar-choferes"),
    ("/traslados/flota", "flota"), ("/traslados/frecuentes", "viajes-frecuentes"), ("/traslados/direccion", "tablero-direccion"),
    ("/traslados/presentismo", "presentismo"), ("/traslados/planificar", "planificador"), ("/firma-en-serie", "firma-serie"),
    ("/despacho", "despacho"), ("/nota/", "ficha-nota"), ("/perfil", "mi-perfil"), ("/notificaciones", "avisos"),
    ("/admin", "usuarios-equipos"), ("/expedientes", "expedientes"), ("/detenidos/importar", "detenidos-importar"),
    ("/detenidos", "busqueda-colectiva"), ("/biblioteca", "biblioteca"), ("/consultas", "consultar"), ("/datos", "datos-direccion"),
    ("/inicio", ""), ("/manual", ""),
)


def enlace_ayuda(ruta):
    for prefijo, video in AYUDA_POR_RUTA:
        if (ruta or "").startswith(prefijo):
            return f"/manual?rol=todos#vd-{video}" if video else "/manual"
    return "/manual"


def _leer_preferencias(usuario):
    if not usuario:
        return dom.preferencias_efectivas({}, None)
    try:
        with db._conexion() as con:
            return srv.preferencias(con, usuario)
    except Exception:  # sin la tabla (base vieja sin migrar) la pantalla sigue con los valores por defecto
        logging.exception("No se pudieron leer las preferencias de pantalla")
        return dom.preferencias_efectivas({}, usuario.get("rol"))


def grupos_firma(notas, hoy=None):
    """Propuesta 110 (U13): pendientes de firma separados por plazo (vencido, hoy, esta semana, URGENTES, resto)."""
    import plazos
    filas = []
    for n in notas or []:
        sem = plazos.semaforo(n)
        filas.append(dict(n, _ui_vence=sem["vence"] if sem else None))
    return dom.agrupar_por_urgencia(filas, hoy or date.today(), clave_vence="_ui_vence")


# Propuesta 98 (U1): política de contenido que impide cargar nada de afuera. Permite los scripts en línea que todavía tienen las
# plantillas ('unsafe-inline') y la evaluación de htmx; bloquea cualquier origen que no sea el propio servidor.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; object-src 'self' blob:; worker-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def poner_csp(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    return response


def cache_pedido(request, clave, fn):
    """Calcula una sola vez por pedido (request.state) lo que varias partes de la página necesitan: no suma consultas."""
    estado = getattr(request, "state", None) if request is not None else None
    guardado = getattr(estado, "_ui_cache", None) if estado is not None else None
    if guardado is None and estado is not None:
        guardado = {}
        try:
            estado._ui_cache = guardado
        except AttributeError:
            guardado = None
    if guardado is not None and clave in guardado:
        return guardado[clave]
    valor = fn()
    if guardado is not None:
        guardado[clave] = valor
    return valor


LISTOS_PARA_DESPACHO = ("FIRMADA", "PARA_REMITIR", "DESPACHADA", "ADJUNTO")


def pasos_bloque(bloque):
    """Propuesta 113 (U16): cada bloque de Mesa como lista de tareas (firmas, números, descarga y despacho) y su próximo paso."""
    import despacho
    notas = bloque.get("notas") or []
    a_firmar = [n for n in notas if n.get("estado") != "ADJUNTO"]
    firmadas = [n for n in a_firmar if n.get("estado") in LISTOS_PARA_DESPACHO]
    con_numero = [n for n in a_firmar if despacho.lleva_numero(n)]
    sin_numero = [n for n in con_numero if not n.get("nro_nota")]
    ids = [n["id"] for n in notas]
    descargado = False
    if ids:
        try:
            with db._conexion() as con:
                marcas = ",".join("?" * len(ids))
                descargado = con.execute(f"SELECT 1 FROM accesos_documentos WHERE accion = 'DESCARGA' AND nota_id IN ({marcas}) LIMIT 1",
                                         ids).fetchone() is not None
        except Exception:
            logging.exception("No se pudo ver si el bloque ya se descargó")
    completo = len(firmadas) == len(a_firmar)
    despachado = bool(notas) and all(n.get("estado") in ("DESPACHADA", "ADJUNTO") for n in notas)
    pasos = [
        {"clave": "firmas", "titulo": "Firmas", "hecho": completo, "texto": f"{len(firmadas)} de {len(a_firmar)}" + (" firmadas" if a_firmar else ""),
         "motivo": None},
        {"clave": "numeros", "titulo": "Números de nota", "hecho": completo and not sin_numero,
         "texto": ("No llevan número" if not con_numero else (f"Falta cargar {len(sin_numero)}" if sin_numero else "Cargados")),
         "motivo": None if completo else "Primero tienen que estar todas las firmas."},
        {"clave": "descarga", "titulo": "Descarga de los firmados", "hecho": descargado, "texto": "Hecha" if descargado else "Pendiente",
         "motivo": None if firmadas else "Todavía no hay documentos firmados."},
        {"clave": "despacho", "titulo": "Despacho", "hecho": despachado, "texto": "Hecho" if despachado else "Pendiente",
         "motivo": None if completo else "Se despacha completo, cuando todo esté firmado."},
    ]
    proximo = next((p["clave"] for p in pasos if not p["hecho"] and not p["motivo"]), None)
    if proximo is None and not completo:
        proximo = "firmas"
    return {"pasos": pasos, "proximo": proximo, "completo": completo}


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]
    puede_ver_nota = ctx.get("puede_ver_nota")
    env = templates.env
    env.globals.update(
        ui_pref=preferencias_de, ui_enlace_ayuda=enlace_ayuda, etiqueta_estado=dom.etiqueta_estado, ETIQUETAS_ESTADO=dom.ETIQUETAS_ESTADO, SIGLAS=dom.SIGLAS,
        ui_mensaje_error=dom.mensaje_error, agrupar_por_urgencia=dom.agrupar_por_urgencia, ui_destino=dom.destino_interno,
        nivel_antiguedad=dom.nivel_antiguedad, ui_grupos_firma=grupos_firma, ui_hace=dom.hace, ui_fecha_dia=fecha_dia,
        ui_fecha_larga=fecha_larga, ui_pasos_bloque=pasos_bloque, ui_agrupar_avisos=lambda avisos: dom.agrupar_avisos(avisos, date.today()),
    )
    def serie_info(request):
        """Propuesta 107 (U10): cuántas URGENTES quedan para firmar y cuántas se saltearon en esta serie."""
        notas = [n for n in db.listar_notas() if n["estado"] == "PROCESADA"]
        if ctx.get("anotar_prioridad_efectiva"):
            ctx["anotar_prioridad_efectiva"](notas)
        ses = getattr(request, "session", {}) or {}
        return {"urgentes": sum(1 for n in notas if (n.get("prioridad_efectiva") or n.get("prioridad")) == "URGENTE"),
                "salteadas": len(ses.get("serie_salteadas") or [])}

    def verbos_inicio(usuario, res=None, request=None):
        """Propuesta 102 (U5): «qué tengo que hacer hoy» como una sola lista ordenada por verbo, cada una con su botón."""
        import auth
        filas = []
        if not usuario:
            return filas
        if ctx.get("tiene_notas") and ctx["tiene_notas"](usuario):
            notas, vistas = cache_pedido(request, "notas_visibles", lambda: (ctx["filtrar_por_visibilidad"](db.listar_notas(), usuario),
                                                                              db.ids_notas_vistas(usuario["id"])))
            def cuenta(*estados):
                return sum(1 for n in notas if n["estado"] in estados)
            rol = usuario.get("rol")
            if auth.es_admin(usuario):
                filas.append({"verbo": "Firmar", "n": cuenta("PROCESADA"), "url": "/firma-en-serie", "boton": "Firmar en serie", "peso": 1})
                filas.append({"verbo": "Revisar correcciones", "n": cuenta("EN_REVISION"), "url": "/?f_tarea=revisar", "boton": "Revisar", "peso": 2})
            if rol == auth.ROL_COORDINADOR:
                filas.append({"verbo": "Aprobar", "n": cuenta("SUBIDA"), "url": "/?f_tarea=aprobar", "boton": "Aprobar", "peso": 1})
            if rol in (auth.ROL_COORDINADOR, auth.ROL_ASESOR):
                propias = [n for n in notas if rol == auth.ROL_COORDINADOR or n["usuario_id"] == usuario["id"]]
                filas.append({"verbo": "Volver a corregir", "n": sum(1 for n in propias if n["estado"] == "RECHAZADA" and n["id"] not in vistas),
                              "url": "/?f_tarea=rechazadas", "boton": "Ver devueltas", "peso": 1})
                filas.append({"verbo": "Enviar", "n": sum(1 for n in notas if n["estado"] == "BORRADOR" and n["usuario_id"] == usuario["id"]),
                              "url": "/", "boton": "Ver sin enviar", "peso": 3})
            if rol in auth.ROLES_MESA:
                filas.append({"verbo": "Despachar", "n": cuenta("FIRMADA", "PARA_REMITIR"), "url": "/despacho", "boton": "Ir a Despacho del día", "peso": 2})
            errores = cuenta("ERROR")
            if errores:
                filas.append({"verbo": "Resolver errores", "n": errores, "url": "/?ver=todos", "boton": "Ver", "peso": 1})
        viajes = (res or {}).get("viajes") if isinstance(res, dict) else None
        if viajes and ctx.get("tiene_autos") and ctx["tiene_autos"](usuario):
            filas.append({"verbo": "Confirmar viajes", "n": viajes.get("pendientes") or 0, "url": "/traslados", "boton": "Ver viajes", "peso": 2})
        return sorted(filas, key=lambda f: (f["n"] == 0, f["peso"]))

    env.globals.update(ui_verbos_inicio=verbos_inicio, ui_serie_info=serie_info, ui_tomar_resumen_serie=lambda request: request.session.pop("serie_resumen", None))
    env.filters.update(hace=dom.hace, fecha_dia=fecha_dia, fecha_larga=fecha_larga)

    def _usuario(request):
        u = usuario_actual(request)
        if not u:
            raise HTTPException(status_code=401, detail="Tu sesión se cerró. Volvé a ingresar.")
        return u

    def _responder(request, destino, mensaje=None, datos=None):
        """htmx o fetch reciben JSON; un formulario común vuelve a la pantalla con el aviso."""
        if request.headers.get("hx-request") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse(dict(datos or {}, ok=True, mensaje=mensaje))
        if mensaje:
            flash(request, mensaje)
        return RedirectResponse(url=dom.destino_interno(destino, "/perfil"), status_code=303)

    # ------------------------------------------------------------ 143 (U46) y 146 (U49): preferencias de pantalla
    @app.post("/ui/preferencias")
    async def guardar_preferencias(request: Request):
        u = _usuario(request)
        f = await request.form()
        cambios = {}
        if "atajos" in f:
            cambios["atajos"] = str(f.get("atajos")) in ("si", "1", "true", "on")
        if f.get("tema"):
            cambios["tema"] = str(f.get("tema"))
        if f.get("densidad"):
            cambios["densidad"] = str(f.get("densidad"))
        if "columnas_ocultas" in f:
            cambios["columnas_ocultas"] = [c for c in f.getlist("columnas_ocultas") if c]
        if f.get("visto"):
            cambios["visto"] = {str(f.get("visto")): str(f.get("valor") or date.today().isoformat())}
        try:
            with db._conexion() as con:
                pref = srv.guardar_preferencias(con, u, cambios)
        except ErrorDGPDH as e:
            raise HTTPException(status_code=e.codigo_http, detail=e.mensaje) from e
        mensaje = None if set(cambios) <= {"visto", "tema"} else "Listo: guardamos tu preferencia."
        return _responder(request, str(f.get("volver") or "/perfil"), mensaje, {"preferencias": pref})

    # ------------------------------------------------------------ 108 (U11): vista dividida de la bandeja de firma
    @app.get("/ui/nota/{nota_id:int}/vista", response_class=HTMLResponse)
    def vista_nota(request: Request, nota_id: int):
        import flujo
        import nucleo
        u = _usuario(request)
        nota = db.obtener_nota(nota_id)
        if nota is None:
            raise HTTPException(status_code=404, detail="Esa nota ya no existe.")
        if not puede_ver_nota or not puede_ver_nota(u, nota):
            raise HTTPException(status_code=403, detail="No tenés permiso para ver esta nota.")
        db.registrar_acceso(nota_id, "VER")
        nota["etapa"] = flujo.etapa_para_estado(nota["estado"])
        parrafos, error = [], None
        if not db.es_pdf(nota):
            ruta = nota.get("ruta_resultado") or nota.get("ruta_base") or nota["ruta_original"]
            parrafos, error = nucleo.obtener_vista_previa(ruta, sin_esperar=True)
        return templates.TemplateResponse("_ui_vista_nota.html", {"request": request, "usuario": u, "nota": nota,
                                                                  "parrafos": (parrafos or [])[:60], "error_vista": error})

    # ------------------------------------------------------------ 136 (U39): quitar un filtro guardado sin preguntar, con «Deshacer»
    @app.post("/ui/filtros/quitar")
    async def quitar_filtro(request: Request):
        u = _usuario(request)
        f = await request.form()
        nombre = str(f.get("nombre") or "")
        try:
            pref = json.loads(u.get("pref_notas") or "{}") or {}
        except (TypeError, ValueError):
            pref = {}
        filtros = pref.get("filtros") or []
        quitado = next((x for x in filtros if x.get("nombre") == nombre), None)
        if quitado:
            pref["filtros"] = [x for x in filtros if x.get("nombre") != nombre]
            db.guardar_pref_notas(u["id"], pref)
            request.session["ui_deshacer_filtro"] = {"nombre": quitado.get("nombre"), "qs": quitado.get("qs")}
            flash(request, f"Filtro «{nombre}» quitado.")
        return RedirectResponse(url="/", status_code=303)

    env.globals["ui_tomar_deshacer_filtro"] = lambda request: request.session.pop("ui_deshacer_filtro", None)

    # ------------------------------------------------------------ 114 (U17): aprobación por lote para Coordinación
    @app.post("/ui/lote/aprobar")
    async def aprobar_lote(request: Request):
        """Aprueba y manda a corrección varias notas de una vez. Cada nota pasa por los mismos controles que la aprobación
        individual (rol, equipo y estado); las que no cumplen se informan y no se tocan. La firma sigue siendo de a una."""
        import auth
        u = _usuario(request)
        ctx["exigir_rol"](u, auth.ROL_COORDINADOR)
        f = await request.form()
        ids = sorted({int(x) for x in f.getlist("nota_ids") if str(x).isdigit()})
        if not ids:
            raise HTTPException(status_code=400, detail="Marcá al menos un documento para aprobar.")
        aprobadas, salteadas = [], []
        for nid in ids:
            nota = db.obtener_nota(nid)
            if nota is None or nota["estado"] != "SUBIDA" or (not auth.es_admin(u) and nota["equipo_id"] != u.get("equipo_id")):
                salteadas.append(nota["nombre_original"] if nota else f"#{nid}")
                continue
            if ctx["arrancar_correccion"](nota, f"Aprobada por Coordinación (en lote, {len(ids)} documentos)"):
                aprobadas.append(nota["nombre_original"])
            else:
                salteadas.append(nota["nombre_original"])
        texto = f"Aprobaste y mandaste a corrección {len(aprobadas)} documento(s)."
        if salteadas:
            texto = "⚠ " + texto + f" No se tocaron {len(salteadas)} porque ya no estaban para aprobar o no son de tu equipo: " + ", ".join(salteadas[:5])
        flash(request, texto)
        return RedirectResponse(url=dom.destino_interno(str(f.get("volver") or "/"), "/"), status_code=303)

    # ------------------------------------------------------------ Confirmación en lote de PDFs A_CONFIRMAR (Dirección)
    @app.post("/ui/lote/confirmar-pdfs")
    async def confirmar_pdfs_lote(request: Request):
        """Dirección confirma el pase a Mesa de varios PDFs A_CONFIRMAR de una vez."""
        u = _usuario(request)
        import auth as _auth
        ctx["exigir_rol"](u, _auth.ROL_DIRECTOR, _auth.ROL_SUBDIRECTOR)
        f = await request.form()
        ids = sorted({int(x) for x in f.getlist("nota_ids") if str(x).isdigit()})
        if not ids:
            raise HTTPException(status_code=400, detail="Marcá al menos un PDF para confirmar.")
        confirmados, salteados = [], []
        for nid in ids:
            nota = db.obtener_nota(nid)
            if nota is None or nota["estado"] != "A_CONFIRMAR" or not db.es_pdf(nota):
                salteados.append(nota["nombre_original"] if nota else f"#{nid}")
                continue
            db.actualizar_estado(nid, "ADJUNTO",
                                 comentario=f"Adjunto confirmado para Mesa por {u['nombre']} (en lote, {len(ids)} PDFs)",
                                 desde=("A_CONFIRMAR",))
            confirmados.append(nota["nombre_original"])
        texto = f"Se confirmaron {len(confirmados)} PDF(s) para pasar a Mesa."
        if salteados:
            texto = "⚠ " + texto + f" No se tocaron {len(salteados)}: " + ", ".join(salteados[:5])
        flash(request, texto)
        return RedirectResponse(url=dom.destino_interno(str(f.get("volver") or "/"), "/"), status_code=303)

    # ------------------------------------------------------------ 122 (U25): borradores de formularios en el servidor
    def _clave_borrador(valor):
        clave = str(valor or "")
        if not clave.startswith("/") or clave.startswith("//") or len(clave) > 200:
            raise HTTPException(status_code=400, detail="Clave de borrador no válida.")
        return clave

    @app.get("/ui/borrador")
    def leer_borrador(request: Request, clave: str = ""):
        u = _usuario(request)
        with db._conexion() as con:
            b = srv.leer_borrador(con, u, _clave_borrador(clave))
        return JSONResponse({"borrador": b})

    @app.post("/ui/borrador")
    async def guardar_borrador(request: Request):
        u = _usuario(request)
        f = await request.form()
        clave = _clave_borrador(f.get("clave"))
        try:
            datos = json.loads(str(f.get("datos") or "{}"))
            if not isinstance(datos, dict):
                raise ValueError
        except ValueError as e:
            raise HTTPException(status_code=400, detail="El borrador no tiene un formato válido.") from e
        try:
            with db._conexion() as con:
                cuando = srv.guardar_borrador(con, u, clave, datos)
        except ErrorDGPDH as e:
            raise HTTPException(status_code=e.codigo_http, detail=e.mensaje) from e
        return JSONResponse({"ok": True, "guardado_en": cuando, "hora": fechas.a_hora_local(cuando, "%H:%M")})

    @app.post("/ui/borrador/descartar")
    async def descartar_borrador(request: Request):
        u = _usuario(request)
        f = await request.form()
        with db._conexion() as con:
            srv.descartar_borrador(con, u, _clave_borrador(f.get("clave")))
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ 148 (U51): «Salí» y «Volví» del chofer, con un toque
    def _chofer_de(u):
        with db._conexion() as con:
            f = con.execute("SELECT id FROM choferes WHERE usuario_id = ?", (u["id"],)).fetchone()
        return f["id"] if f else None

    env.globals["ui_chofer_de"] = _chofer_de
    env.globals["ui_manana"] = lambda: date.fromordinal(date.today().toordinal() + 1).isoformat()
    env.globals["ui_unidad"] = lambda uid: db.obtener_unidad(uid) if uid else None

    @app.post("/ui/viaje/{viaje_id:int}/{momento}")
    async def marcar_salida(request: Request, viaje_id: int, momento: str):
        """Guarda la hora real de salida o de vuelta. Lo hacen el chofer asignado al viaje y la coordinación de traslados."""
        import operacion as op
        import traslados as tr
        u = _usuario(request)
        if momento not in ("sali", "volvi"):
            raise HTTPException(status_code=404, detail="Esa acción no existe.")
        v = tr.obtener_viaje(viaje_id)
        if not v:
            raise HTTPException(status_code=404, detail="El viaje no existe.")
        mio = bool(v.get("chofer_id")) and v["chofer_id"] == _chofer_de(u)
        if not (mio or tr.puede_coordinar(u)):
            raise HTTPException(status_code=403, detail="Lo marca el chofer asignado al viaje o la coordinación de traslados.")
        ahora = datetime.now().strftime("%H:%M")
        # Propuesta 150: una marca guardada sin señal llega después con la hora en que se tocó (del mismo día y no futura).
        form = await request.form()
        hora = str(form.get("hora") or "")
        if hora and len(hora) == 5 and hora[2] == ":" and hora.replace(":", "").isdigit() and hora <= ahora \
                and str(form.get("fecha") or date.today().isoformat()) == date.today().isoformat():
            ahora = hora
        salida, regreso = v.get("hora_salida_real"), v.get("hora_regreso_real")
        if momento == "sali":
            salida = ahora
        else:
            regreso = ahora
            if salida and salida > regreso:
                salida = None
        try:
            op.cargar_horas_reales(viaje_id, salida, regreso, u["nombre"])
        except tr.ErrorTraslado as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        flash(request, f"{'Salida' if momento == 'sali' else 'Vuelta'} registrada a las {ahora}: {v.get('destino') or 'viaje'}.")
        return RedirectResponse(url=dom.destino_interno(f"/traslados/hoy?fecha={v['fecha']}", "/traslados/hoy"), status_code=303)

    # ------------------------------------------------------------ 134 (U37) y 135 (U38): paleta de comandos y búsqueda global
    def _acciones_paleta(u):
        import auth
        import traslados as tr
        acc = []
        notas_ok = bool(ctx.get("tiene_notas") and ctx["tiene_notas"](u))
        autos_ok = bool(ctx.get("tiene_autos") and ctx["tiene_autos"](u))
        if autos_ok and u.get("rol") != "CHOFER":
            acc.append(("Solicitar viaje", "/traslados/nuevo"))
        if autos_ok:
            acc.append(("Ir a Autos PPN", "/traslados"))
        if u.get("rol") == "CHOFER" or (autos_ok and tr.puede_coordinar(u)):
            acc.append(("Mis salidas" if u.get("rol") == "CHOFER" else "Tablero del día", "/traslados/hoy"))
        if notas_ok:
            acc.append(("Ir a Notas a la firma", "/"))
            if u.get("rol") in (auth.ROL_ASESOR, auth.ROL_COORDINADOR) or auth.es_admin(u):
                acc.append(("Subir documentos", "/#panel-subir"))
        if auth.es_admin(u):
            acc.append(("Firmar en serie", "/firma-en-serie"))
        if notas_ok and auth.funciones_mesa(u):
            acc.append(("Ir a Despacho del día", "/despacho"))
        acc += [("Ver mis avisos", "/notificaciones"), ("Mi perfil", "/perfil"), ("Ayuda", "/manual")]
        return acc

    @app.get("/ui/paleta", response_class=HTMLResponse)
    def paleta(request: Request, q: str = ""):
        """Resultados agrupados (acciones, notas, viajes, personas detenidas, unidades) entre lo que la persona ya puede ver.
        No se envía nada a terceros."""
        import detenidos
        import traslados as tr
        from datetime import timedelta
        u = _usuario(request)
        q = " ".join((q or "").split())[:80]
        qn = dom.normalizar(q)
        terminos = [t for t in qn.split() if t]
        coincide = lambda texto: bool(terminos) and all(t in dom.normalizar(texto) for t in terminos)  # noqa: E731
        grupos = []
        acciones = [{"texto": t, "url": url} for t, url in _acciones_paleta(u) if not terminos or coincide(t)]
        if acciones:
            grupos.append({"titulo": "Acciones", "items": acciones[:8], "total": len(acciones)})
        notas_ok = bool(ctx.get("tiene_notas") and ctx["tiene_notas"](u))
        if len(qn) >= 2:
            if notas_ok and puede_ver_nota:
                encontradas = [n for n in db.listar_notas(limite=100000)
                               if (coincide(n.get("nombre_original") or "") or (n.get("protocolo_numero") and qn.replace(" ", "") in dom.normalizar(n["protocolo_numero"])))
                               and puede_ver_nota(u, n)]
                if encontradas:
                    grupos.append({"titulo": "Notas", "total": len(encontradas), "ver_todos": "/buscar?q=" + quote(q),
                                   "items": [{"texto": n["nombre_original"], "detalle": dom.etiqueta_estado(n["estado"]), "url": f"/nota/{n['id']}"}
                                             for n in encontradas[:6]]})
            if ctx.get("tiene_autos") and ctx["tiene_autos"](u):
                desde = (date.today() - timedelta(days=90)).isoformat()
                filtro = {} if tr.puede_coordinar(u) else {"usuario_id": u["id"]}
                if u.get("rol") == "CHOFER":
                    cid = _chofer_de(u)
                    viajes = [v for v in tr.listar_viajes(desde=desde, limite=3000) if cid and v.get("chofer_id") == cid]
                else:
                    viajes = tr.listar_viajes(desde=desde, limite=3000, **filtro)
                viajes = [v for v in viajes if v["estado"] != "CANCELADO" and coincide(" ".join(str(v.get(k) or "") for k in ("destino", "unidad_nombre", "motivo")))]
                if viajes:
                    grupos.append({"titulo": "Viajes", "total": len(viajes),
                                   "items": [{"texto": f"{fecha_dia(v['fecha'])} {v.get('hora_salida') or 'sin horario'} · {v.get('destino') or v.get('unidad_nombre') or ''}",
                                              "detalle": v["estado"].replace("_", " ").capitalize(), "url": f"/traslados/{v['id']}"} for v in viajes[-6:][::-1]]})
            if detenidos.puede_ver(u, notas_ok) and (any(c.isdigit() for c in qn) and len(qn.replace(" ", "")) >= 5 or len(terminos) >= 2):
                personas = detenidos.buscar(q, limite=6)
                detenidos.registrar_acceso(u, "BUSQUEDA", f"Búsqueda global: {q}")
                if personas:
                    grupos.append({"titulo": "Personas detenidas", "total": len(personas), "ver_todos": ("/detenidos?dni=" + quote(q.replace(" ", ""))) if q.replace(" ", "").isdigit() else "/detenidos",
                                   "items": [{"texto": p.get("nombre_completo") or p.get("nombre") or "", "detalle": " · ".join(x for x in (f"DNI {p['dni']}" if p.get("dni") else "", p.get("unidad_nombre") or "") if x),
                                              "url": f"/detenidos/{p['id']}"} for p in personas[:6]]})
            if notas_ok or (ctx.get("tiene_autos") and ctx["tiene_autos"](u)):
                unidades = [x for x in db.listar_unidades() if coincide(" ".join(str(x.get(k) or "") for k in ("nombre", "alias", "ciudad", "provincia")))]
                if unidades:
                    grupos.append({"titulo": "Unidades", "total": len(unidades),
                                   "items": [{"texto": x["nombre"], "detalle": ", ".join(y for y in (x.get("ciudad"), x.get("provincia")) if y),
                                              "url": f"/traslados/mapa#unidad-{x['id']}"} for x in unidades[:6]]})
        import auth
        exp_ok = notas_ok and (auth.es_admin(u) or u.get("rol") == auth.ROL_COORDINADOR or u.get("rol") in auth.ROLES_MESA)
        return templates.TemplateResponse("_ui_paleta_resultados.html", {"request": request, "q": q, "grupos": grupos, "notas_ok": notas_ok,
                                                                         "exp_ok": exp_ok})


    # ------------------------------------------------------------ 106 (U9), 112 (U15) y 164 (U67): bandas y pie institucional
    cfg = ctx.get("config")
    def _cfg(clave, defecto):
        try:
            return cfg.get("interfaz", clave, fallback=defecto) if cfg is not None else defecto
        except Exception:
            return defecto
    env.globals["ui_institucional"] = {
        "version": _cfg("version", "Beta 01"), "fecha_version": _cfg("fecha_version", "14/11/2026"),
        "soporte": _cfg("soporte", "Sistemas de la DGPDH"), "banda_beta": _cfg("banda_beta", "si").lower() not in ("no", "0", "false"),
    }

    def firma_hoy(usuario, ruta):
        """Propuesta 112 (U15): en las bandejas de Dirección y Coordinación, quién firma cuando el Director o la Subdirectora
        están de licencia (ausencias cargadas, de hoy a 7 días)."""
        import auth
        if not usuario or ruta not in ("/", "/inicio") or not (auth.es_admin(usuario) or usuario.get("rol") == auth.ROL_COORDINADOR):
            return []
        hoy = date.today()
        hasta = date.fromordinal(hoy.toordinal() + 7).isoformat()
        avisos = []
        try:
            with db._conexion() as con:
                filas = con.execute("""SELECT a.desde, a.hasta, u.nombre AS usuario_nombre, d.nombre AS delegado_nombre FROM ausencias a
                                       JOIN usuarios u ON u.id = a.usuario_id JOIN usuarios d ON d.id = a.delegado_id
                                       WHERE u.rol IN ('DIRECTOR', 'SUBDIRECTOR') AND a.hasta >= ? AND a.desde <= ? ORDER BY a.desde""",
                                    (hoy.isoformat(), hasta)).fetchall()
            for a in filas:
                d, h = a["desde"], a["hasta"]
                cuando = (f"Hasta el {h[8:10]}/{h[5:7]}" if d <= hoy.isoformat() else f"Del {d[8:10]}/{d[5:7]} al {h[8:10]}/{h[5:7]}")
                avisos.append(f"{cuando} firma {a['delegado_nombre']} ({a['usuario_nombre']} está de licencia).")
        except Exception:
            logging.exception("No se pudo armar el aviso de quién firma")
        return avisos

    env.globals["ui_firma_hoy"] = firma_hoy

    @app.get("/ui/sugerencias", response_class=HTMLResponse)
    def sugerencias(request: Request, desde: str = "", ver: str = ""):
        import auth
        u = _usuario(request)
        lista = []
        if auth.es_admin(u):
            with db._conexion() as con:
                lista = srv.listar_sugerencias(con, None if ver == "todas" else "NUEVA")
        return templates.TemplateResponse("ui_sugerencias.html", {"request": request, "usuario": u, "desde": dom.destino_interno(desde, ""),
                                                                  "lista": lista, "ver": ver, "es_admin_ui": auth.es_admin(u)})

    @app.post("/ui/sugerencias")
    async def sugerencias_guardar(request: Request):
        u = _usuario(request)
        f = await request.form()
        desde = dom.destino_interno(str(f.get("desde") or ""), "/inicio")
        try:
            with db._conexion() as con:
                srv.registrar_sugerencia(con, u, str(f.get("texto") or ""), desde)
        except ErrorDGPDH as e:
            raise HTTPException(status_code=e.codigo_http, detail=e.mensaje) from e
        flash(request, "Gracias: tu comentario le llega a la Dirección. Queda solo en el servidor del organismo.")
        return RedirectResponse(url=desde, status_code=303)

    @app.post("/ui/sugerencias/{sid:int}/atendida")
    def sugerencia_atendida(request: Request, sid: int):
        import auth
        u = _usuario(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Las sugerencias las atiende la Dirección.")
        try:
            with db._conexion() as con:
                srv.atender_sugerencia(con, u, sid)
        except ErrorDGPDH as e:
            raise HTTPException(status_code=e.codigo_http, detail=e.mensaje) from e
        flash(request, "Sugerencia marcada como atendida.")
        return RedirectResponse(url="/ui/sugerencias", status_code=303)

    @app.get("/accesibilidad", response_class=HTMLResponse)
    def accesibilidad(request: Request):
        return templates.TemplateResponse("ui_accesibilidad.html", {"request": request, "usuario": _usuario(request)})

    # ------------------------------------------------------------ 159 (U62): cambios sin recargar la página
    @app.get("/ui/aviso-pendiente")
    def aviso_pendiente(request: Request):
        """El aviso que dejó una acción hecha con fetch (para mostrarlo ya y que no reaparezca en la próxima pantalla)."""
        _usuario(request)
        return JSONResponse({"flash": request.session.pop("flash", None), "deshacer": request.session.get("deshacer")})

    # ------------------------------------------------------------ 153 (U56) y 156 (U59): resumen al entrar y novedades de la versión
    from pathlib import Path as _Path
    ruta_novedades = _Path(__file__).resolve().parent / "novedades.md"

    def _versiones():
        try:
            return dom.leer_novedades(ruta_novedades.read_text(encoding="utf-8"))
        except OSError:
            return []

    def novedades(usuario, request=None):
        vs = _versiones()
        if not usuario or not vs:
            return None
        pref = preferencias_de(usuario, request)
        if pref["vistos"].get("novedades") == vs[0]["version"]:
            return None
        autos = bool(ctx.get("tiene_autos") and ctx["tiene_autos"](usuario))
        items = dom.novedades_para(vs, usuario.get("rol"), autos)
        return {"version": vs[0]["version"], "titulo": vs[0]["titulo"], "items": items} if items else None

    def resumen_dia(usuario, request=None):
        if not usuario:
            return None
        hoy = date.today().isoformat()
        pref = preferencias_de(usuario, request)
        if pref["vistos"].get("resumen_dia") == hoy:
            return None
        desde = date.fromordinal(date.today().toordinal() - 1).isoformat()
        nuevos = [t for t, cuando in avisos_no_leidos(usuario["id"], request) if (cuando or "") >= desde]
        actuar = sum(1 for t in nuevos if dom.clasificar_aviso(t)["actuar"])
        partes = []
        if actuar:
            partes.append(f"{actuar} aviso{'s' if actuar != 1 else ''} que te piden actuar")
        if len(nuevos) - actuar:
            n = len(nuevos) - actuar
            partes.append(f"{n} aviso{'s' if n != 1 else ''} para que sepas")
        pendientes = [f"{v['n']} para {v['verbo'].lower()}" for v in verbos_inicio(usuario, None, request) if v["n"]][:3]
        if not partes and not pendientes:
            return None
        return {"desde_ayer": partes, "pendientes": pendientes, "hoy": hoy}

    env.globals.update(ui_novedades=novedades, ui_resumen_dia=resumen_dia)

    @app.get("/ui/novedades", response_class=HTMLResponse)
    def ver_novedades(request: Request):
        u = _usuario(request)
        return templates.TemplateResponse("ui_novedades.html", {"request": request, "usuario": u, "versiones": _versiones()})


    # ------------------------------------------------------------ 152 (U55): la insignia cuenta solo lo que pide acción
    def avisos_no_leidos(usuario_id, request=None):
        """[(texto, creada_en)] de los avisos sin leer: una sola consulta por pedido para la insignia, el título y el resumen."""
        def leer():
            with db._conexion() as con:
                return [(f[0], f[1]) for f in con.execute("SELECT texto, creada_en FROM notificaciones WHERE usuario_id = ? AND leida = 0 "
                                                          "ORDER BY id DESC LIMIT 500", (usuario_id,))]
        return cache_pedido(request, f"avisos_{usuario_id}", leer)

    def contar_accion(usuario_id, request=None):
        try:
            return sum(1 for t, _ in avisos_no_leidos(usuario_id, request) if dom.clasificar_aviso(t)["actuar"])
        except Exception:
            return db.contar_notificaciones(usuario_id)

    env.globals["ui_contar_accion"] = contar_accion


    # ------------------------------------------------------------ 158 (U61): cola de la IA con tiempo estimado
    _promedio = {"cuando": 0.0, "min": None}

    def minutos_por_nota(refrescar=False):
        """Promedio (últimos 7 días) de lo que tardó la corrección automática por nota, en minutos. Una consulta cada 5 minutos."""
        import time
        if not refrescar and time.monotonic() - _promedio["cuando"] < 300:
            return _promedio["min"]
        valor = None
        try:
            with db._conexion() as con:
                f = con.execute("""SELECT AVG((julianday(fin) - julianday(inicio)) * 1440) FROM (
                                       SELECT fecha AS inicio, estado, LEAD(fecha) OVER (PARTITION BY nota_id ORDER BY id) AS fin
                                       FROM historial_estados WHERE fecha >= ?)
                                   WHERE estado = 'PROCESANDO' AND fin IS NOT NULL AND (julianday(fin) - julianday(inicio)) * 1440 BETWEEN 0.05 AND 240""",
                                (date.fromordinal(date.today().toordinal() - 7).isoformat(),)).fetchone()
            valor = round(f[0], 1) if f and f[0] else None
        except Exception:
            logging.exception("No se pudo calcular el promedio de la corrección automática")
        _promedio.update(cuando=time.monotonic(), min=valor)
        return valor

    def espera_cola(lugar):
        m = minutos_por_nota()
        if not m or not lugar:
            return ""
        total = max(1, round(m * lugar))
        return f"unos {total} min (promedio: {m:g} min por nota)".replace(".", ",")

    env.globals.update(ui_minutos_por_nota=minutos_por_nota, ui_espera_cola=espera_cola)


    # ------------------------------------------------------------ 154 (U57): primeros pasos por rol, salteables
    def primeros_pasos(usuario, request=None):
        """Lista corta de primeros pasos según el rol; cada uno se tilda solo al completarse. Se cierra con «No mostrar más»."""
        import auth
        import pin
        if not usuario:
            return None
        pref = preferencias_de(usuario, request)
        vistos = pref["vistos"]
        if vistos.get("primeros_pasos") == "cerrado":
            return None
        rol = usuario.get("rol")
        pasos = []
        if pin.requiere(usuario):
            pasos.append({"texto": "Creá tu PIN de firma", "url": "/perfil#pin", "hecho": pin.tiene_pin(usuario)})
        if rol in (auth.ROL_ASESOR, auth.ROL_COORDINADOR):
            with db._conexion() as con:
                subio = con.execute("SELECT 1 FROM notas WHERE usuario_id = ? LIMIT 1", (usuario["id"],)).fetchone() is not None
            pasos.append({"texto": "Subí tu primer documento", "url": "/#panel-subir", "hecho": subio})
        if auth.es_admin(usuario):
            pasos.append({"texto": "Probá «Firmar en serie»", "url": "/firma-en-serie", "hecho": vistos.get("paso_serie") == "si", "marca": "paso_serie"})
        video = {"CHOFER": "choferes", "MESA_JEFE": "despacho", "MESA_COORDINADOR": "despacho", "MESA_ASESOR": "despacho",
                 "DIRECTOR": "revisar-firmar", "SUBDIRECTOR": "revisar-firmar"}.get(rol or "", "subir-nota")
        pasos.append({"texto": "Mirá el video de 1 minuto", "url": f"/manual?rol=todos#vd-{video}", "hecho": vistos.get("paso_video") == "si", "marca": "paso_video"})
        pasos.append({"texto": "Elegí tu foto o dejá tus iniciales", "url": "/perfil", "hecho": bool(usuario.get("avatar")) or vistos.get("paso_foto") == "si",
                      "marca": "paso_foto"})
        if all(p["hecho"] for p in pasos):
            return None
        return {"pasos": pasos, "hechos": sum(1 for p in pasos if p["hecho"])}

    env.globals["ui_primeros_pasos"] = primeros_pasos

    # ------------------------------------------------------------ 111 (U14): motivos frecuentes al devolver una nota
    MOTIVOS_BASE = ("Falta el LPU", "La fecha está mal", "El destinatario no corresponde", "Falta un adjunto",
                    "Va con la firma de la Subdirectora", "No corresponde a esta área")

    @app.get("/ui/motivos-frecuentes")
    def motivos_frecuentes(request: Request):
        """Motivos de devolución más usados (cada línea «• …» de los rechazos anteriores) más los de siempre. Coordinación ve solo
        los de su equipo; la Dirección, todos."""
        import auth
        from collections import Counter
        u = _usuario(request)
        if not (auth.es_admin(u) or u.get("rol") == auth.ROL_COORDINADOR):
            raise HTTPException(status_code=403, detail="Solo Coordinación y Dirección devuelven notas.")
        sql = ("SELECT h.comentario FROM historial_estados h JOIN notas n ON n.id = h.nota_id WHERE h.estado = 'RECHAZADA' "
               "AND h.comentario IS NOT NULL" + ("" if auth.es_admin(u) else " AND n.equipo_id = ?") + " ORDER BY h.id DESC LIMIT 500")
        with db._conexion() as con:
            textos = [f[0] for f in con.execute(sql, () if auth.es_admin(u) else (u.get("equipo_id"),))]
        cuenta = Counter(l.strip()[1:].strip() for t in textos for l in str(t).splitlines() if l.strip().startswith("•") and 3 <= len(l.strip()) <= 120)
        usados = [m for m, n in cuenta.most_common(8) if n >= 2]
        return JSONResponse({"motivos": usados + [m for m in MOTIVOS_BASE if m not in usados]})

    # ------------------------------------------------------------ 117 (U20): validación en línea de DNI y LPU
    @app.get("/ui/validar/{campo}", response_class=HTMLResponse)
    def validar_campo(request: Request, campo: str, valor: str = "", excluir: str = ""):
        """Al salir del campo DNI o LPU de la ficha de una persona detenida: avisa si ya existe, con enlace a la ficha. Sin JavaScript
        el formulario se valida igual al enviar."""
        import detenidos
        from markupsafe import escape
        u = _usuario(request)
        if campo not in ("dni", "lpu"):
            raise HTTPException(status_code=404, detail="Ese dato no se valida en línea.")
        if not detenidos.puede_ver(u, bool(ctx.get("tiene_notas") and ctx["tiene_notas"](u))):
            raise HTTPException(status_code=403, detail="No tenés acceso a la base de personas detenidas.")
        digitos = "".join(c for c in str(valor) if c.isdigit())
        if len(digitos) < 5:
            return HTMLResponse("")
        filtro = {"dni": digitos} if campo == "dni" else {"lpu": digitos}
        hallados = [p for p in detenidos.buscar_campos(incluir_bajas=True, limite=5, **filtro) if str(p.get("id")) != str(excluir)]
        if not hallados:
            return HTMLResponse('<span class="validacion-ok">No hay otra ficha con ese ' + ("DNI" if campo == "dni" else "LPU") + ".</span>")
        p = hallados[0]
        detenidos.registrar_acceso(u, "BUSQUEDA", f"Validación en línea de {campo.upper()}")
        texto = f"Ya existe: {escape(p.get('nombre_completo') or p.get('nombre') or '')}" + (f", {escape(p.get('unidad_nombre'))}" if p.get("unidad_nombre") else "")
        return HTMLResponse(f'<span class="validacion-aviso" role="alert">{texto}. <a href="/detenidos/{int(p["id"])}">Ver la ficha</a></span>')


    # ------------------------------------------------------------ 150 (U53): app instalable y lectura sin señal (optativa)
    env.globals["ui_institucional"]["pwa"] = _cfg("pwa", "no").lower() in ("si", "sí", "1", "true")

    @app.get("/sw.js")
    def service_worker(request: Request):
        """El service worker se sirve desde la raíz para que pueda guardar «Mis salidas». Solo con [interfaz] pwa = si."""
        from fastapi.responses import Response
        from pathlib import Path as _P
        _usuario(request)
        if not env.globals["ui_institucional"]["pwa"]:
            raise HTTPException(status_code=404, detail="La app instalable no está activada.")
        cuerpo = (_P(__file__).resolve().parent / "static" / "ui_sw.js").read_text(encoding="utf-8")
        return Response(cuerpo, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


    # ------------------------------------------------------------ 98 (U1): Content-Security-Policy (optativa: [interfaz] csp = si)
    if _cfg("csp", "no").lower() in ("si", "sí", "1", "true"):
        @app.middleware("http")
        async def _csp(request, call_next):
            return poner_csp(await call_next(request))
