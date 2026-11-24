"""Rutas del área Notas e IA local (propuestas 1–53) que no tienen un módulo propio.

- Propuesta 16: reglas de corrección que la Dirección puede desactivar a partir de los rechazos (Administración → IA → Aprendizaje).
Se registra al final de main.py con `registrar(app, ctx)`, como los demás `rutas_*.py`.
"""
import logging

import database as db
from dgpdh.errores import ErrorDGPDH
from dgpdh.seguridad import auditoria
from dgpdh.servicios import reglas_correccion


def registrar_reglas_revision(nota_id, decisiones_completas, motivos_rechazo, usuario):
    """Propuesta 16: al aplicar la revisión, una fila por regla que intervino en cada párrafo. Nunca frena la revisión."""
    try:
        with db._conexion() as con:
            for d in decisiones_completas:
                reglas = d.get("reglas") or []
                if reglas and d.get("propuesto") != d.get("original"):
                    reglas_correccion.registrar_decision(con, nota_id=nota_id, ubicacion=d.get("ubicacion"), reglas=reglas,
                                                         decision=d.get("decision"), motivo=(motivos_rechazo or {}).get(d.get("id")),
                                                         usuario=(usuario or {}).get("nombre"))
    except Exception:
        logging.exception("No se pudo registrar el uso de las reglas de corrección")


def datos_reglas(limite=30):
    """Para Administración → IA → Aprendizaje."""
    try:
        with db._conexion() as con:
            todas = reglas_correccion.resumen(con)
            return {"propuestas": reglas_correccion.propuestas(con), "desactivadas": [u for u in todas if u.estado == "DESACTIVADA"],
                    "usos": todas[:limite], "motivos": reglas_correccion.MOTIVOS, "minimo": reglas_correccion.MINIMO_RECHAZOS}
    except Exception:
        logging.exception("No se pudo leer el uso de las reglas de corrección")
        return {"propuestas": [], "desactivadas": [], "usos": [], "motivos": reglas_correccion.MOTIVOS, "minimo": reglas_correccion.MINIMO_RECHAZOS}


def sellar_registros(clave, ancla=None):
    """Propuesta 50: sella las filas nuevas de los registros históricos y anota la cabeza de cada cadena en el archivo ancla
    (el mismo de la auditoría, que se copia en cada respaldo)."""
    from datetime import datetime, timezone

    from dgpdh.seguridad import cadenas
    try:
        with db._conexion() as con:
            hechos = cadenas.sellar_todas(con, clave)
        nuevos = {t: x for t, x in hechos.items() if x[0]}
        if nuevos and ancla is not None:
            fecha = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(ancla, "a", encoding="utf-8") as a:
                for t, (_, ultimo, h) in nuevos.items():
                    a.write(f"{fecha} CADENA:{t} {ultimo} {h}\n")
        return hechos
    except Exception:
        logging.exception("No se pudieron sellar los registros históricos")
        return {}


def verificar_registros(clave):
    from dgpdh.seguridad import cadenas
    with db._conexion() as con:
        return cadenas.verificar_todas(con, clave)


def personas_de_nota(nota, usuario):
    """Propuesta 39: fichas vinculadas a la nota y fichas propuestas (solo para quien puede ver la base de personas)."""
    import detenidos
    from dgpdh.servicios import nota_persona
    try:
        if not nota or not detenidos.puede_ver(usuario, bool(usuario) and (usuario.get("acceso_notas") or usuario.get("rol") == "DIRECTOR")):
            return None
        with db._conexion() as con:
            return {"confirmadas": nota_persona.confirmadas_de_nota(con, nota["id"]), "propuestas": nota_persona.proponer(con, nota["id"])}
    except Exception:
        logging.exception("No se pudieron proponer las personas de la nota")
        return None


def ocr_en_segundo_plano(nota_id, ruta_pdf):
    """Propuesta 4: un PDF archivado sin texto pasa por OCR (si está activado) en un hilo aparte; el texto de la copia se
    indexa para la búsqueda y los antecedentes. El original no se toca."""
    from pathlib import Path

    from dgpdh.servicios import herramientas_locales
    if not herramientas_locales.OCR["activo"]:
        return False

    def trabajo():
        try:
            texto = herramientas_locales.texto_pdf(Path(ruta_pdf))
            if texto.strip():
                db.indexar_texto(nota_id, texto)
                db.registrar_evento(nota_id, "Texto del PDF reconocido con OCR (copia aparte; el original no se modificó)")
        except Exception:
            logging.exception("No se pudo hacer el OCR del PDF")
    import threading
    threading.Thread(target=trabajo, name=f"ocr-{nota_id}", daemon=True).start()
    return True


# ---------------------------------------------------------------- Modificación 2: rechazadas
def _plazos_inactividad():
    import configparser
    from pathlib import Path

    from dgpdh.dominio import inactividad
    cp = configparser.ConfigParser()
    try:
        cp.read(Path(__file__).resolve().parent / "config.ini", encoding="utf-8")
        return (max(1, cp.getint("notas", "inactividad_aviso_dias", fallback=inactividad.AVISO_DIAS)),
                max(1, cp.getint("notas", "inactividad_archivo_dias", fallback=inactividad.ARCHIVO_DIAS)))
    except (ValueError, configparser.Error):
        return inactividad.AVISO_DIAS, inactividad.ARCHIVO_DIAS


def revisar_inactividad(hoy=None, forzar=False):
    """Una vez por día (desde el mantenimiento del trabajador de fondo): avisa al agente por cada documento RECHAZADO sin
    movimiento y archiva (recuperable) los que superan el plazo. Devuelve {avisados, archivados}."""
    from datetime import date, datetime

    from dgpdh.dominio import inactividad
    hoy = hoy or date.today()
    if not forzar and (db.obtener_ajuste("inactividad_ultima_corrida") or "") == hoy.isoformat():
        return {"avisados": 0, "archivados": 0, "omitido": True}
    dias_aviso, dias_archivo = _plazos_inactividad()
    with db._conexion() as con:  # una sola consulta para todas (sin consultas por fila)
        filas = [dict(f) for f in con.execute(
            """SELECT n.id, n.nombre_original, n.usuario_id, MAX(MAX(COALESCE(h.fecha, '')), n.actualizado_en) AS ultima, i.ultimo_aviso, i.avisos
               FROM notas n LEFT JOIN historial_estados h ON h.nota_id = n.id LEFT JOIN notas_inactividad i ON i.nota_id = n.id
               WHERE n.estado = 'RECHAZADA' GROUP BY n.id""")]
    avisados = archivados = 0
    for f in filas:
        try:
            ultima = datetime.fromisoformat(str(f["ultima"])[:19]).date()
        except ValueError:
            continue
        aviso = date.fromisoformat(f["ultimo_aviso"][:10]) if f.get("ultimo_aviso") else None
        d = inactividad.evaluar(ultima, aviso, hoy, dias_aviso=dias_aviso, dias_archivo=dias_archivo)
        try:
            if d.accion == "archivar":
                db.descartar_nota(f["id"], "Sistema", motivo=d.motivo)
                with db._conexion() as con:
                    auditoria.registrar(con, accion="Archivo automático por inactividad", entidad="nota", entidad_id=f["id"],
                                        antes={"estado": "RECHAZADA"}, despues={"estado": "DESCARTADA", "dias_sin_actividad": d.dias},
                                        motivo=d.motivo, origen="tarea_diaria")
                if f["usuario_id"]:
                    db.notificar_usuarios([f["usuario_id"]], f"«{f['nombre_original']}» se archivó: {d.motivo.lower()}. Se puede recuperar "
                                                             "desde «Descartados».", f"/nota/{f['id']}")
                archivados += 1
            elif d.accion == "avisar":
                if f["usuario_id"]:
                    db.notificar_usuarios([f["usuario_id"]], f"«{f['nombre_original']}» está rechazada y sin movimiento hace {d.dias} días. "
                                                             f"Si no se hace nada, a los {dias_archivo} días se archiva (se puede recuperar).",
                                          f"/nota/{f['id']}")
                with db._conexion() as con:
                    con.execute("""INSERT INTO notas_inactividad (nota_id, ultimo_aviso, avisos) VALUES (?, ?, 1)
                                   ON CONFLICT(nota_id) DO UPDATE SET ultimo_aviso = excluded.ultimo_aviso, avisos = avisos + 1""",
                                (f["id"], hoy.isoformat()))
                avisados += 1
        except Exception:
            logging.exception("No se pudo revisar la inactividad de la nota %s", f["id"])
    db.guardar_ajuste("inactividad_ultima_corrida", hoy.isoformat())
    return {"avisados": avisados, "archivados": archivados}


def retirar_rechazadas(ids, usuario, puede_gestionar):
    """«Retirar del listado»: baja lógica (DESCARTADA, recuperable) con auditoría. Solo RECHAZADA y lo que la persona gestiona."""
    hechos = []
    for i in ids:
        n = db.obtener_nota(i)
        if not n or n["estado"] != "RECHAZADA" or not puede_gestionar(usuario, n):
            continue
        db.descartar_nota(i, usuario["nombre"], motivo="retirada del listado")
        with db._conexion() as con:
            auditoria.registrar(con, accion="Retirar del listado", entidad="nota", entidad_id=i, antes={"estado": "RECHAZADA"},
                                despues={"estado": "DESCARTADA"}, actor=auditoria.Actor.desde(usuario), origen="listado")
        hechos.append(i)
    return hechos


def duplicar_rechazadas(ids, usuario, puede_gestionar):
    """«Duplicar para reintentar»: una copia del documento como borrador nuevo del agente; la rechazada queda como estaba."""
    import shutil
    import uuid
    from pathlib import Path

    import integridad
    nuevas = []
    for i in ids:
        n = db.obtener_nota(i)
        if not n or n["estado"] != "RECHAZADA" or not puede_gestionar(usuario, n):
            continue
        origen = Path(n.get("ruta_base") or n["ruta_original"])
        if not origen.is_file():
            continue
        destino = origen.with_name(f"{origen.stem}_reintento_{uuid.uuid4().hex[:8]}{origen.suffix}")
        shutil.copy2(origen, destino)
        nid = db.crear_nota(n["nombre_original"], n["equipo_id"], str(destino), usuario_id=usuario["id"], prioridad=n.get("prioridad") or "NORMAL",
                            borrador=True)
        try:
            db.fijar_hash(nid, "sha256_original", integridad.sha256_archivo(destino))
        except OSError:
            logging.exception("No se pudo calcular la huella del duplicado")
        db.registrar_evento(nid, f"Duplicado de la nota #{i} (rechazada) para reintentar — {usuario['nombre']}")
        db.registrar_evento(i, f"Se duplicó para reintentar como #{nid} — {usuario['nombre']}")
        with db._conexion() as con:
            auditoria.registrar(con, accion="Duplicar para reintentar", entidad="nota", entidad_id=i, despues={"nueva": nid},
                                actor=auditoria.Actor.desde(usuario), origen="listado")
        nuevas.append(nid)
    return nuevas


def sellar_qr_despacho(nota_id, nro_nota, pdf, avisos=None):
    """Propuesta 51: QR de verificación en la copia de despacho (si está activo). Nunca frena el despacho."""
    from pathlib import Path

    from dgpdh.servicios import qr_verificacion
    if not qr_verificacion.CONFIG["activo"]:
        return None
    try:
        with db._conexion() as con:
            return qr_verificacion.sellar_despacho(con, nota_id, nro_nota, Path(pdf))
    except Exception:
        logging.exception("No se pudo estampar el QR de verificación")
        if avisos is not None:
            avisos.append("No se pudo agregar el código QR de verificación al PDF (el despacho sigue igual).")
        return None


def vinculos_de_nota(nota, usuario, puede_ver, puede_gestionar):
    """Propuesta 3: vínculos confirmados (solo hacia notas que la persona ve) y propuestos (solo para quien gestiona la nota)."""
    from dgpdh.servicios import vinculos
    if not nota:
        return None
    try:
        with db._conexion() as con:
            confirmados = vinculos.de_nota(con, nota["id"])
            gestiona = puede_gestionar(usuario, nota)
            texto = con.execute("SELECT contenido FROM notas_texto WHERE nota_id = ?", (nota["id"],)).fetchone() if gestiona else None
            propuestas = vinculos.proponer(con, nota["id"], texto[0] if texto else "") if gestiona else []
        visibles = []
        for v in confirmados:
            if v["otra_id"] is None:
                visibles.append(v)
                continue
            otra = db.obtener_nota(v["otra_id"])
            if otra and puede_ver(usuario, otra):
                visibles.append(v)
        nombres = {}
        for pr in propuestas:
            if pr.destino_id and pr.destino_id not in nombres:
                otra = db.obtener_nota(pr.destino_id)
                nombres[pr.destino_id] = otra["nombre_original"] if otra and puede_ver(usuario, otra) else None
        return {"confirmados": visibles, "propuestas": [pr for pr in propuestas if not pr.destino_id or nombres.get(pr.destino_id)],
                "nombres": nombres, "gestiona": gestiona, "tipos": vinculos.TIPOS}
    except Exception:
        logging.exception("No se pudieron leer los vínculos de la nota")
        return None


# ---------------------------------------------------------------- propuesta 2: reglas editables por la Dirección
def evaluar_reglas(nota_id, disparador, texto=None):
    """Corre las reglas activas del disparador sobre la nota; lo que se cumple queda como sugerencia. Nunca frena el circuito."""
    from dgpdh.servicios import reglas_propuesta as srv
    try:
        nota = db.obtener_nota(nota_id)
        if not nota:
            return 0
        with db._conexion() as con:
            if texto is None:
                f = con.execute("SELECT contenido FROM notas_texto WHERE nota_id = ?", (nota_id,)).fetchone()
                texto = f[0] if f else ""
            unidades = [int(str(f[0])[2:]) for f in con.execute("SELECT norm FROM nota_entidades WHERE nota_id = ? AND tipo = 'UNIDAD'", (nota_id,))
                        if str(f[0]).startswith("u:") and str(f[0])[2:].isdigit()]
            unidades += [int(f[0]) for f in con.execute("SELECT valor FROM nota_metadatos WHERE nota_id = ? AND campo = 'unidad'", (nota_id,))
                         if str(f[0]).isdigit()]
            return srv.evaluar(con, nota_id=nota_id, disparador=disparador, texto=texto or "", tipo_documental=nota.get("tipo_documental"),
                               unidades=unidades, equipo_id=nota.get("equipo_id"))
    except Exception:
        logging.exception("No se pudieron evaluar las reglas de la nota %s", nota_id)
        return 0


def aplicar_sugerencia_regla(nota, sug, usuario):
    """La persona aceptó lo que propuso una regla: recién ahora se aplica (prioridad, aviso, plazo o etiqueta)."""
    import json as _json
    from datetime import datetime, timezone

    import auth
    c = _json.loads(sug["contenido_json"] or "{}")
    accion, valor = c.get("accion"), str(c.get("valor") or "")
    quien = usuario["nombre"]
    if accion == "prioridad" and valor in ("URGENTE", "PRIORITARIA", "NORMAL"):
        db.actualizar_prioridad(nota["id"], valor, f"Prioridad {valor.lower()} por la regla «{c.get('nombre')}» (aceptada por {quien})")
    elif accion == "aviso_equipo" and valor.isdigit():
        ids = [x["id"] for x in db.listar_usuarios() if x["activo"] and x.get("equipo_id") == int(valor) and x["rol"] == auth.ROL_COORDINADOR]
        if ids:
            db.notificar_usuarios(ids, f"Por la regla «{c.get('nombre')}»: mirá «{nota['nombre_original']}».", f"/nota/{nota['id']}")
        db.registrar_evento(nota["id"], f"Aviso al equipo #{valor} por la regla «{c.get('nombre')}» (aceptado por {quien})")
    elif accion in ("plazo_dias", "etiqueta"):
        campo = "plazo_dias" if accion == "plazo_dias" else "etiqueta"
        with db._conexion() as con:
            con.execute("""INSERT INTO nota_metadatos (nota_id, campo, valor, origen, quien, fecha) VALUES (?,?,?,?,?,?)
                           ON CONFLICT(nota_id, campo) DO UPDATE SET valor = excluded.valor, origen = excluded.origen, quien = excluded.quien,
                           fecha = excluded.fecha""", (nota["id"], campo, valor, "REGLA", quien, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        db.registrar_evento(nota["id"], f"{'Plazo de ' + valor + ' días' if campo == 'plazo_dias' else 'Etiqueta «' + valor + '»'} por la regla "
                                        f"«{c.get('nombre')}» (aceptado por {quien})")
    with db._conexion() as con:
        auditoria.registrar(con, accion="Aceptar propuesta de regla", entidad="nota", entidad_id=nota["id"], despues=c,
                            actor=auditoria.Actor.desde(usuario), origen="reglas")


def ids_confirmados_para_caso(tipo, norm, ids):
    """Carpeta del caso: a las notas que nombran la entidad se suman las que una persona vinculó (nota↔ficha confirmado, o la
    unidad confirmada en los datos del documento) y se sacan las que alguien descartó para esa persona. Dos consultas fijas."""
    ids = list(dict.fromkeys(ids or []))
    try:
        with db._conexion() as con:
            if tipo == "PERSONA" and str(norm).startswith("dni:"):
                filas = con.execute("""SELECT np.nota_id, np.estado FROM nota_persona np JOIN detenidos d ON d.id = np.detenido_id
                                       WHERE d.dni = ?""", (str(norm)[4:],)).fetchall()
                descartadas = {f[0] for f in filas if f[1] == "DESCARTADO"}
                ids = [i for i in ids if i not in descartadas] + [f[0] for f in filas if f[1] == "CONFIRMADO" and f[0] not in ids]
            elif tipo == "UNIDAD" and str(norm).startswith("u:"):
                ids += [f[0] for f in con.execute("SELECT nota_id FROM nota_metadatos WHERE campo = 'unidad' AND valor = ?", (str(norm)[2:],))
                        if f[0] not in ids]
    except Exception:
        logging.exception("No se pudieron sumar los vínculos confirmados a la carpeta del caso")
    return ids


# ---------------------------------------------------------------- propuesta 1: metadatos por tipo documental
def metadatos_de_nota(nota, usuario, puede_gestionar):
    """Campos del tipo documental de la nota: valor confirmado o propuesto (pocas consultas, nunca una por fila)."""
    from dgpdh.dominio import metadatos as md
    if not nota:
        return None
    try:
        campos = md.campos_de(nota.get("tipo_documental"))
        with db._conexion() as con:
            guardados = {f[0]: f[1] for f in con.execute("SELECT campo, valor FROM nota_metadatos WHERE nota_id = ?", (nota["id"],))}
            ents: dict = {}
            for tipo, valor, norm in con.execute("SELECT tipo, valor, norm FROM nota_entidades WHERE nota_id = ?", (nota["id"],)):
                ents.setdefault(tipo, []).append({"valor": valor, "norm": norm})
            dnis = [e["norm"][4:] for e in ents.get("PERSONA", []) if str(e["norm"]).startswith("dni:")]
            fichas = {f[0]: f[1] for f in con.execute("SELECT dni, id FROM detenidos WHERE dni IN (%s) AND COALESCE(motivo_baja, '') NOT LIKE "
                                                      "'Unificada%%'" % ",".join("?" * len(dnis)), dnis)} if dnis else {}
            texto = con.execute("SELECT contenido FROM notas_texto WHERE nota_id = ?", (nota["id"],)).fetchone()
        texto = texto[0] if texto else ""
        plazo = None
        organismo = None
        if texto:
            import agentes
            import despacho
            pl = agentes.detectar_plazos(texto)
            plazo = pl[0]["cantidad"] if pl else None
            organismo = despacho.proponer_destinatario(texto) or None
        prop = md.propuestas(campos, ents, fichas, plazo, organismo)
        ids_det = [v for c in campos if c.tipo == "detenido" for v in (guardados.get(c.clave), prop.get(c.clave)) if v]
        ids_nota = [v for c in campos if c.tipo == "nota" for v in (guardados.get(c.clave), prop.get(c.clave)) if v]
        with db._conexion() as con:
            nombres_det = {str(f[0]): f[1] for f in con.execute("SELECT id, nombre_completo FROM detenidos WHERE id IN (%s)"
                                                                 % ",".join("?" * len(ids_det)), ids_det)} if ids_det else {}
            nombres_nota = {str(f[0]): f[1] for f in con.execute("SELECT id, nombre_original FROM notas WHERE id IN (%s)"
                                                                  % ",".join("?" * len(ids_nota)), ids_nota)} if ids_nota else {}
        unidades = {str(u["id"]): u["nombre"] for u in db.listar_unidades()}

        def etiqueta(c, v):
            if not v:
                return ""
            return {"detenido": nombres_det, "unidad": unidades, "nota": nombres_nota}.get(c.tipo, {}).get(v, v) if c.tipo in ("detenido", "unidad", "nota") else v
        filas = [{"clave": c.clave, "etiqueta": c.etiqueta, "tipo": c.tipo, "valor": guardados.get(c.clave), "texto": etiqueta(c, guardados.get(c.clave)),
                  "propuesto": prop.get(c.clave) if not guardados.get(c.clave) else None, "texto_propuesto": etiqueta(c, prop.get(c.clave))}
                 for c in campos]
        return {"filas": filas, "gestiona": puede_gestionar(usuario, nota), "unidades": unidades}
    except Exception:
        logging.exception("No se pudieron armar los datos del documento")
        return None


def guardar_metadatos(nota, form, usuario):
    """Valida y guarda lo que confirmó la persona (con auditoría). La persona detenida también queda vinculada a la ficha."""
    from datetime import datetime, timezone

    from dgpdh.dominio import metadatos as md
    from dgpdh.errores import DatoInvalido
    from dgpdh.servicios import nota_persona
    campos = md.campos_de(nota.get("tipo_documental"))
    nuevos = {c.clave: md.normalizar(c, form.get(f"md_{c.clave}")) for c in campos}
    actor = auditoria.Actor.desde(usuario)
    with db._conexion() as con:
        for c in campos:
            v = nuevos[c.clave]
            tabla = {"detenido": "detenidos", "unidad": "unidades", "nota": "notas"}.get(c.tipo)
            if v and tabla and not con.execute(f"SELECT 1 FROM {tabla} WHERE id = ?", (int(v),)).fetchone():
                raise DatoInvalido(f"«{c.etiqueta}»: no existe el número {v}.")
        previos = {f[0]: f[1] for f in con.execute("SELECT campo, valor FROM nota_metadatos WHERE nota_id = ?", (nota["id"],))}
        cambios = {k: v for k, v in nuevos.items() if (previos.get(k) or None) != v}
        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for k, v in cambios.items():
            con.execute("""INSERT INTO nota_metadatos (nota_id, campo, valor, origen, quien, fecha) VALUES (?,?,?,?,?,?)
                           ON CONFLICT(nota_id, campo) DO UPDATE SET valor = excluded.valor, origen = excluded.origen, quien = excluded.quien,
                           fecha = excluded.fecha""", (nota["id"], k, v, "CONFIRMADO", actor.nombre, ahora))
        if cambios:
            auditoria.registrar(con, accion="Datos del documento", entidad="nota", entidad_id=nota["id"],
                                antes={k: previos.get(k) for k in cambios}, despues=cambios, actor=actor, origen="metadatos")
        if cambios.get("persona_detenida"):
            nota_persona.decidir(con, nota_id=nota["id"], detenido_id=int(cambios["persona_detenida"]), accion="confirmar", actor=actor,
                                 motivo="Datos del documento")
    return cambios


def registrar(app, ctx):
    from fastapi import HTTPException, Request
    from fastapi.responses import RedirectResponse
    templates, usuario_actual, exigir_rol = ctx["templates"], ctx["usuario_actual"], ctx["exigir_rol"]
    templates.env.globals["reglas_correccion_datos"] = datos_reglas
    templates.env.globals["motivos_rechazo"] = reglas_correccion.MOTIVOS
    templates.env.globals["personas_de_nota"] = personas_de_nota
    templates.env.globals["sugerencias_regla"] = lambda nota: db.sugerencias_de(nota["id"], "REGLA") if nota else []

    @app.post("/nota/{nota_id}/regla/{sug_id}")
    async def nota_regla(request: Request, nota_id: int, sug_id: int):
        """Propuesta 2: aceptar o descartar lo que propuso una regla (quien gestiona la nota)."""
        u = usuario_actual(request)
        nota = db.obtener_nota(nota_id)
        if nota is None or not ctx["puede_ver_nota"](u, nota):
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        if not ctx["puede_gestionar_nota"](u, nota):
            raise HTTPException(status_code=403, detail="No podés decidir sobre esta nota")
        sug = next((x for x in db.sugerencias_de(nota_id, "REGLA") if x["id"] == sug_id), None)
        if sug is None:
            raise HTTPException(status_code=409, detail="Esa propuesta ya se resolvió")
        aceptar = (await request.form()).get("accion") == "aceptar"
        if aceptar:
            aplicar_sugerencia_regla(nota, sug, u)
        db.resolver_sugerencia(sug_id, "ACEPTADA" if aceptar else "DESCARTADA", u["nombre"])
        return RedirectResponse(url=f"/nota/{nota_id}#asistentes", status_code=303)

    @app.get("/admin/reglas")
    def admin_reglas(request: Request):
        from dgpdh.dominio import reglas_propuesta as rp
        from dgpdh.servicios import reglas_propuesta as srv
        u = usuario_actual(request)
        exigir_rol(u)
        with db._conexion() as con:
            reglas, uso = srv.todas(con), srv.uso(con)
        return templates.TemplateResponse("reglas_propuesta.html", {
            "request": request, "usuario": u, "reglas": reglas, "uso": uso, "disparadores": rp.DISPARADORES, "condiciones": rp.CONDICIONES,
            "acciones": rp.ACCIONES, "equipos": db.listar_equipos(), "unidades": db.listar_unidades(), "mensaje": request.session.pop("flash_admin", None)})

    @app.post("/admin/reglas")
    async def admin_reglas_guardar(request: Request):
        from dgpdh.servicios import reglas_propuesta as srv
        u = usuario_actual(request)
        exigir_rol(u)
        f = await request.form()
        actor = auditoria.Actor.desde(u)
        try:
            with db._conexion() as con:
                rid = int(f["regla_id"]) if str(f.get("regla_id") or "").isdigit() else None
                if f.get("accion_form") in ("activar", "pausar") and rid:
                    srv.activar(con, rid, f.get("accion_form") == "activar", actor=actor)
                    request.session["flash_admin"] = "Regla activada." if f.get("accion_form") == "activar" else "Regla pausada."
                else:
                    srv.guardar(con, dict(f), actor=actor, regla_id=rid)
                    request.session["flash_admin"] = "Regla guardada. Solo propone: nada cambia en las notas hasta que alguien lo acepte."
        except ErrorDGPDH as e:
            request.session["flash_admin"] = e.mensaje
        return RedirectResponse(url="/admin/reglas", status_code=303)
    templates.env.globals["metadatos_de_nota"] = lambda nota, u: metadatos_de_nota(nota, u, ctx["puede_gestionar_nota"])

    @app.post("/nota/{nota_id}/metadatos")
    async def nota_metadatos(request: Request, nota_id: int):
        """Propuesta 1: quien lleva la nota confirma o corrige los datos propios de su tipo documental."""
        u = usuario_actual(request)
        nota = db.obtener_nota(nota_id)
        if nota is None or not ctx["puede_ver_nota"](u, nota):
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        if not ctx["puede_gestionar_nota"](u, nota):
            raise HTTPException(status_code=403, detail="No podés cambiar los datos de esta nota")
        try:
            cambios = guardar_metadatos(nota, await request.form(), u)
            ctx["flash"](request, f"Datos del documento guardados ({len(cambios)} cambio(s))." if cambios else "No había cambios.")
        except ErrorDGPDH as e:
            ctx["flash"](request, e.mensaje)
        return RedirectResponse(url=f"/nota/{nota_id}#metadatos", status_code=303)
    templates.env.globals["vinculos_de_nota"] = lambda nota, u: vinculos_de_nota(nota, u, ctx["puede_ver_nota"], ctx["puede_gestionar_nota"])

    def _retencion_de(nota):
        from dgpdh.servicios import retencion
        try:
            with db._conexion() as con:
                return retencion.de_nota(con, nota["id"]) if nota else None
        except Exception:
            return None
    templates.env.globals["retencion_de"] = _retencion_de

    @app.get("/admin/retencion")
    def admin_retencion(request: Request):
        """Propuesta 5: series documentales y documentos vencidos (solo marca; nada se borra)."""
        import tipos_documentales
        from dgpdh.servicios import retencion
        u = usuario_actual(request)
        exigir_rol(u)
        with db._conexion() as con:
            datos = {"series": retencion.series(con), "vencidos": retencion.listado(con, "VENCIDA"), "retirados": retencion.listado(con, "RETIRADA")}
        return templates.TemplateResponse("retencion.html", {"request": request, "usuario": u, "tipos": tipos_documentales.TIPOS,
                                                             "destinos": retencion.DESTINOS, "mensaje": request.session.pop("flash_admin", None), **datos})

    @app.post("/admin/retencion")
    async def admin_retencion_accion(request: Request):
        from dgpdh.servicios import retencion
        u = usuario_actual(request)
        exigir_rol(u)
        f = await request.form()
        actor = auditoria.Actor.desde(u)
        try:
            with db._conexion() as con:
                if f.get("accion") == "serie":
                    retencion.guardar_serie(con, tipo=str(f.get("tipo") or ""), plazo_anios=int(f.get("plazo") or 0) if str(f.get("plazo") or "").isdigit() else 0,
                                            destino=str(f.get("destino") or ""), actor=actor)
                    request.session["flash_admin"] = "Serie documental guardada."
                elif f.get("accion") == "marcar":
                    n = retencion.marcar_vencidos(con)
                    request.session["flash_admin"] = f"{n} documento(s) marcados como vencidos. No se borró ni se cambió nada más."
                elif f.get("accion") == "retirar" and str(f.get("nota_id") or "").isdigit():
                    retencion.autorizar_retiro(con, int(f["nota_id"]), actor=actor, motivo=str(f.get("motivo") or ""))
                    request.session["flash_admin"] = "Retiro autorizado: el documento queda «retirado» y se conserva."
        except ErrorDGPDH as e:
            request.session["flash_admin"] = e.mensaje
        return RedirectResponse(url="/admin/retencion", status_code=303)

    @app.get("/nota/{nota_id}/control-cambios.docx")
    def nota_control_cambios(request: Request, nota_id: int):
        """Propuesta 10: copia del documento con las correcciones propuestas como control de cambios de Word."""
        import tempfile
        from pathlib import Path

        from fastapi.responses import Response

        import control_cambios_word
        import nucleo
        u = usuario_actual(request)
        nota = db.obtener_nota(nota_id)
        if nota is None or not ctx["puede_ver_nota"](u, nota):
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        if nota["estado"] != "EN_REVISION" or not nota.get("propuestas"):
            raise HTTPException(status_code=409, detail="Esta nota no tiene correcciones propuestas")
        base = Path(nota.get("ruta_analizada") or nota.get("ruta_base") or nota["ruta_original"])
        if base.suffix.lower() == ".doc":
            base = nucleo.convertir_doc_a_docx(base)
        with tempfile.TemporaryDirectory() as tmp:
            salida = Path(tmp) / "control.docx"
            control_cambios_word.generar(base, salida, nota["propuestas"])
            contenido = salida.read_bytes()
        db.registrar_acceso(nota_id, "DESCARGA", "copia con control de cambios")
        nombre = Path(nota["nombre_original"]).stem + " - con control de cambios.docx"
        from urllib.parse import quote
        return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    def _texto_de(nota):
        with db._conexion() as con:
            f = con.execute("SELECT contenido FROM notas_texto WHERE nota_id = ?", (nota["id"],)).fetchone()
        if f and f[0]:
            return f[0]
        import automatico
        import nucleo
        try:
            return nucleo.texto_de_documento(automatico.ruta_texto_actual(nota))
        except Exception:
            return ""

    def _nota_visible(request, nota_id):
        u = usuario_actual(request)
        nota = db.obtener_nota(nota_id)
        if nota is None or not ctx["puede_ver_nota"](u, nota):
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        return u, nota

    @app.get("/nota/{nota_id}/testar")
    def nota_testar(request: Request, nota_id: int):
        """Propuesta 44: preparar una versión testada para compartir (propone qué tachar; la persona decide)."""
        from dgpdh.dominio import anonimizar
        u, nota = _nota_visible(request, nota_id)
        texto = _texto_de(nota)
        with db._conexion() as con:
            nombres = [f[0] for f in con.execute("SELECT DISTINCT d.nombre_completo FROM nota_persona np JOIN detenidos d ON d.id = np.detenido_id "
                                                 "WHERE np.nota_id = ? AND np.estado = 'CONFIRMADO'", (nota_id,))]
        items = anonimizar.a_testar(texto, nombres)
        return templates.TemplateResponse("testar.html", {"request": request, "usuario": u, "nota": nota, "items": items, "hay_texto": bool(texto.strip())})

    @app.post("/nota/{nota_id}/testar")
    async def nota_testar_generar(request: Request, nota_id: int):
        import html
        import io
        from datetime import datetime
        from pathlib import Path
        from urllib.parse import quote

        from fastapi.responses import Response
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        except ImportError:
            raise HTTPException(status_code=503, detail="Para generar la versión testada falta instalar reportlab en el servidor (pip install reportlab).") from None

        from dgpdh.dominio import anonimizar
        u, nota = _nota_visible(request, nota_id)
        form = await request.form()
        tachar = [str(x) for x in (form.getlist("tachar") if hasattr(form, "getlist") else [])]
        tachar += [x.strip() for x in str(form.get("otros") or "").splitlines() if x.strip()]
        texto = anonimizar.testar(_texto_de(nota), tachar)
        estilos = getSampleStyleSheet()
        buf = io.BytesIO()
        cuerpo = [Paragraph(html.escape(f"Versión testada para compartir · {nota['nombre_original']} · generada por {u['nombre']} el "
                                        f"{datetime.now().strftime('%d/%m/%Y %H:%M')}"), estilos["Italic"]), Spacer(1, 12)]
        cuerpo += [Paragraph(html.escape(par), estilos["BodyText"]) if par.strip() else Spacer(1, 6) for par in texto.splitlines()]
        SimpleDocTemplate(buf, pagesize=A4, title="Versión testada").build(cuerpo)
        with db._conexion() as con:
            auditoria.registrar(con, accion="Versión testada", entidad="nota", entidad_id=nota_id, despues={"tachados": len(set(tachar))},
                                actor=auditoria.Actor.desde(u), origen="testado")
        db.registrar_acceso(nota_id, "DESCARGA", f"versión testada ({len(set(tachar))} dato(s) tachados)")
        nombre = Path(nota["nombre_original"]).stem + " - versión testada.pdf"
        return Response(buf.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    @app.post("/nota/{nota_id}/vinculo")
    async def nota_vinculo(request: Request, nota_id: int):
        """Propuesta 3: confirmar, descartar o agregar un vínculo con tipo. Quien gestiona la nota."""
        from dgpdh.servicios import vinculos
        u = usuario_actual(request)
        nota = db.obtener_nota(nota_id)
        if nota is None or not ctx["puede_ver_nota"](u, nota):
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        if not ctx["puede_gestionar_nota"](u, nota):
            raise HTTPException(status_code=403, detail="No podés cambiar los vínculos de esta nota")
        f = await request.form()
        destino = int(f["destino_id"]) if str(f.get("destino_id") or "").isdigit() else None
        if destino is None and str(f.get("nro_nota") or "").strip():  # agregado a mano por número de nota
            with db._conexion() as con:
                fila = con.execute("SELECT id FROM notas WHERE replace(COALESCE(nro_nota, ''), ' ', '') = ? AND id != ? ORDER BY id DESC LIMIT 1",
                                   (str(f.get("nro_nota")).replace(" ", ""), nota_id)).fetchone()
            if not fila:
                ctx["flash"](request, "No hay otra nota con ese número.")
                return RedirectResponse(url=f"/nota/{nota_id}#vinculos", status_code=303)
            destino = int(fila[0])
        if destino is not None:
            otra = db.obtener_nota(destino)
            if not otra or not ctx["puede_ver_nota"](u, otra):
                raise HTTPException(status_code=404, detail="La otra nota no existe")
        try:
            with db._conexion() as con:
                vinculos.decidir(con, origen_id=nota_id, tipo=str(f.get("tipo") or ""), destino_id=destino,
                                 referencia_externa=str(f.get("referencia") or "") or None, confirmar=f.get("accion") != "descartar",
                                 actor=auditoria.Actor.desde(u), origen_dato="PROPUESTA" if f.get("propuesta") else "MANUAL")
        except ErrorDGPDH as e:
            ctx["flash"](request, e.mensaje)
        return RedirectResponse(url=f"/nota/{nota_id}#vinculos", status_code=303)

    async def _ids_marcados(request):
        form = await request.form()
        lista = form.getlist("nota_ids") if hasattr(form, "getlist") else [form.get("nota_ids")]
        return [int(x) for x in lista if str(x or "").isdigit()]

    def _verificacion(request, codigo, resultado=None):
        from dgpdh.servicios import qr_verificacion
        u = usuario_actual(request)
        if not ctx["tiene_notas"](u):
            raise HTTPException(status_code=403, detail="La verificación es para quien tiene acceso a Notas")
        with db._conexion() as con:
            reg = qr_verificacion.buscar(con, codigo)
        nota = db.obtener_nota(reg["nota_id"]) if reg else None
        r = templates.TemplateResponse("verificar_despacho.html", {"request": request, "usuario": u, "codigo": codigo, "registro": reg,
                                                                   "nota": nota, "ve_nota": bool(nota and ctx["puede_ver_nota"](u, nota)),
                                                                   "resultado": resultado})
        return r

    @app.get("/verificar/{codigo}")
    def verificar_despacho(request: Request, codigo: str):
        """Propuesta 51: página interna de verificación del PDF despachado (a la que lleva el QR)."""
        return _verificacion(request, codigo)

    @app.post("/verificar/{codigo}")
    async def verificar_despacho_archivo(request: Request, codigo: str):
        from dgpdh.servicios import qr_verificacion
        form = await request.form()
        archivo = form.get("archivo")
        contenido = await archivo.read() if archivo is not None and hasattr(archivo, "read") else b""
        with db._conexion() as con:
            reg = qr_verificacion.buscar(con, codigo)
        resultado = None if not reg or not contenido else qr_verificacion.coincide(reg, contenido)
        import detenidos
        detenidos.registrar_acceso(usuario_actual(request), "VERIFICAR_DESPACHO", f"{codigo}: {'coincide' if resultado else 'no coincide'}")
        return _verificacion(request, codigo, resultado)

    @app.post("/notas/rechazadas/retirar")
    async def rechazadas_retirar(request: Request):
        """Modificación 2: retirar del listado (baja lógica, recuperable desde «Descartados»)."""
        u = usuario_actual(request)
        hechos = retirar_rechazadas(await _ids_marcados(request), u, ctx["puede_gestionar_nota"])
        ctx["flash"](request, f"{len(hechos)} documento(s) retirado(s) del listado. Se conservan y se pueden recuperar desde «Descartados»."
                     if hechos else "No había documentos rechazados que puedas retirar entre los marcados.")
        return RedirectResponse(url="/", status_code=303)

    @app.post("/notas/rechazadas/duplicar")
    async def rechazadas_duplicar(request: Request):
        """Modificación 2: duplicar para reintentar (borrador nuevo; la rechazada queda como estaba)."""
        u = usuario_actual(request)
        nuevas = duplicar_rechazadas(await _ids_marcados(request), u, ctx["puede_gestionar_nota"])
        ctx["flash"](request, f"{len(nuevas)} borrador(es) nuevo(s) para reintentar." if nuevas
                     else "No había documentos rechazados que puedas duplicar entre los marcados.")
        return RedirectResponse(url=f"/nota/{nuevas[0]}" if len(nuevas) == 1 else "/", status_code=303)

    @app.post("/nota/{nota_id}/persona")
    async def nota_persona_decidir(request: Request, nota_id: int):
        """Propuesta 39: confirmar o descartar que la nota habla de una persona de la base. Quien ve la nota y la base."""
        import detenidos
        from dgpdh.servicios import nota_persona
        u = usuario_actual(request)
        nota = db.obtener_nota(nota_id)
        if nota is None or not ctx["puede_ver_nota"](u, nota) or not detenidos.puede_ver(u, ctx["tiene_notas"](u)):
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        form = await request.form()
        det_id = str(form.get("detenido_id") or "")
        if not det_id.isdigit():
            raise HTTPException(status_code=422, detail="Falta la ficha")
        try:
            with db._conexion() as con:
                nota_persona.decidir(con, nota_id=nota_id, detenido_id=int(det_id), accion=str(form.get("accion") or ""),
                                     actor=auditoria.Actor.desde(u), puntaje=int(form["puntaje"]) if str(form.get("puntaje") or "").isdigit() else None,
                                     motivo=str(form.get("motivo") or "") or None)
        except ErrorDGPDH as e:
            raise HTTPException(status_code=e.codigo_http, detail=e.mensaje) from e
        return RedirectResponse(url=f"/nota/{nota_id}#personas-nota", status_code=303)

    async def _cambiar(request, desactivar):
        u = usuario_actual(request)
        exigir_rol(u)  # solo la Dirección
        form = await request.form()
        regla = str(form.get("regla") or "")
        try:
            with db._conexion() as con:
                reglas_correccion.cambiar_estado(con, regla, desactivar=desactivar, actor=auditoria.Actor.desde(u),
                                                 motivo=str(form.get("motivo") or "") or None)
        except ErrorDGPDH as e:
            request.session["flash_admin"] = e.mensaje
            return RedirectResponse(url="/admin/ia/aprendizaje", status_code=303)
        import nucleo
        nucleo.recargar_reglas_desactivadas()
        request.session["flash_admin"] = (("Regla desactivada: la corrección deja de proponerla." if desactivar else "Regla reactivada.")
                                          + f" ({reglas_correccion.etiqueta(regla)})")
        return RedirectResponse(url="/admin/ia/aprendizaje", status_code=303)

    @app.post("/admin/archivo/paquete")
    async def archivo_paquete(request: Request):
        """Propuesta 6: paquete de archivo por año o por expediente (solo la Dirección; queda en la auditoría)."""
        from datetime import datetime

        from fastapi.responses import FileResponse

        from dgpdh.servicios import paquete_archivo
        u = usuario_actual(request)
        exigir_rol(u)
        form = await request.form()
        anio = int(form["anio"]) if str(form.get("anio") or "").isdigit() else None
        expediente = str(form.get("expediente") or "").strip() or None
        destino = ctx["carpeta_datos"] / "paquetes" / f"paquete_{anio or 'expte'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        try:
            with db._conexion() as con:
                r = paquete_archivo.generar(con, destino, anio=anio, expediente=expediente, actor=auditoria.Actor.desde(u))
        except ErrorDGPDH as e:
            request.session["flash_admin"] = e.mensaje
            return RedirectResponse(url="/admin/sistema/integridad", status_code=303)
        return FileResponse(str(r.ruta), media_type="application/zip", filename=r.ruta.name)

    @app.post("/admin/integridad/registros")
    def integridad_registros(request: Request):
        """Propuesta 50: verifica las cadenas de los registros históricos (solo la Dirección)."""
        exigir_rol(usuario_actual(request))
        clave, ancla = ctx["clave_auditoria"]()
        sellar_registros(clave, ancla)
        resultados = verificar_registros(clave)
        request.session["flash_admin"] = " · ".join(r.texto for r in resultados)
        return RedirectResponse(url="/admin/sistema/integridad", status_code=303)

    @app.post("/admin/ia/reglas/desactivar")
    async def reglas_desactivar(request: Request):
        return await _cambiar(request, True)

    @app.post("/admin/ia/reglas/reactivar")
    async def reglas_reactivar(request: Request):
        return await _cambiar(request, False)
