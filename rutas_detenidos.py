"""Pantallas del ABM de personas detenidas. Se registran desde main.py con `registrar(app, ctx)`.

  /detenidos ......................  lista y búsqueda (nombre, alias, DNI, LPU, expediente, código), filtros por centro,
                                     mayores/menores de edad y bajas
  /detenidos/nueva, /{id}/editar ..  alta y modificación
  /detenidos/{id} .................  ficha con edad calculada, historial de cambios, notas que la nombran y accesos
  /detenidos/{id}/baja, /reactivar   baja con motivo (nunca se borra) y reactivación
  /detenidos/importar .............  planilla .xlsx/.csv → vista previa → confirmar; también desde las planillas de Mesa
  /detenidos/exportar.xlsx / .csv .  base exportable (respeta los filtros de la lista)
  /api/detenidos ..................  lectura en JSON para otros sistemas del organismo, con token ([detenidos] api_token)
"""
import hmac
import json
import logging
import re
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import database as db
import detenidos as det

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _carpeta_importaciones():
    c = Path(db.RUTA_DB).parent / "importaciones_detenidos"
    c.mkdir(parents=True, exist_ok=True)
    return c


def registrar(app, ctx):
    templates, usuario_actual, flash, tiene_notas = ctx["templates"], ctx["usuario_actual"], ctx["flash"], ctx["tiene_notas"]

    def pagina(request, nombre, **c):
        u = usuario_actual(request)
        return templates.TemplateResponse(nombre, dict(c, request=request, usuario=u, puede_administrar=det.puede_administrar(u),
                                                              puede_mantener=det.puede_mantener(u), temas_incons=det.TEMAS_INCONSISTENCIA))

    def ip(request):
        return request.client.host if getattr(request, "client", None) else None

    def exigir_ver(request):
        u = usuario_actual(request)
        if not det.puede_ver(u, tiene_notas(u)):
            raise HTTPException(status_code=403, detail="No tenés acceso a la base de personas detenidas.")
        return u

    def exigir_admin(request):
        u = exigir_ver(request)
        if not det.puede_administrar(u):
            raise HTTPException(status_code=403, detail="Esta acción es para la Dirección y Mesa de Entradas.")
        return u

    def exigir_mantener(request):
        u = exigir_ver(request)
        if not det.puede_mantener(u):
            raise HTTPException(status_code=403, detail="Esta acción es para quien mantiene la base (Dirección, Mesa de Entradas o con el permiso «Mantenimiento de bases»).")
        return u

    def obtener_o_404(det_id):
        d = det.obtener(det_id)
        if not d:
            raise HTTPException(status_code=404, detail="Persona no encontrada")
        return d

    def filtros(q, unidad_id, edad, bajas):
        uid = int(unidad_id) if str(unidad_id).isdigit() else None
        adultos = {"mayores": True, "menores": False}.get(edad)
        return dict(q=q.strip()[:120], unidad_id=uid, adultos=adultos, incluir_bajas=(bajas == "1"))

    def contexto_form(d=None, errores=None, avisos=None):
        return {"d": d or {}, "errores": errores or [], "avisos": avisos or [], "campos": det.ETIQUETA,
                "situaciones": det.SITUACIONES, "catalogo": db.listar_unidades(), "externo": det.EXTERNO["activo"]}

    # ------------------------------------------------------------------ lista
    @app.get("/detenidos", response_class=HTMLResponse)
    def detenidos_lista(request: Request, apellido: str = "", nombre: str = "", dni: str = "", lpu: str = "", expediente: str = "",
                        codigo: str = "", unidad_id: str = "", edad: str = "", bajas: str = "", nivel: str = ""):
        """Abre con el buscador vacío: la base no se muestra hasta buscar. Con DNI o LPU alcanza un dato; si no, al menos dos."""
        u = exigir_ver(request)
        f = {"apellido": apellido.strip()[:80], "nombre": nombre.strip()[:80], "dni": dni.strip()[:20], "lpu": lpu.strip()[:30],
             "expediente": expediente.strip()[:60], "codigo": codigo.strip()[:60],
             "unidad_id": int(unidad_id) if str(unidad_id).isdigit() else None, "adultos": {"mayores": True, "menores": False}.get(edad)}
        buscado = any(v not in (None, "") for v in f.values())
        lista, aviso = [], ""
        if buscado:
            ok, aviso = det.datos_de_busqueda(f)
            if ok:
                nivel = nivel if nivel in ("alojados", "historico", "todos") else ("todos" if bajas == "1" else "alojados")
                lista = det.buscar_campos(**f, incluir_bajas=nivel != "alojados", limite=301 if nivel != "historico" else 5000)
                if nivel == "historico":
                    lista = [x for x in lista if not x.get("activo")][:301]
                det.registrar_acceso(u, "DETENIDOS_BUSCAR", f"{len(lista)} resultado(s)", ip(request))
        return pagina(request, "detenidos.html", lista=lista[:300], hay_mas=len(lista) > 300, f=f, buscado=buscado and not aviso, aviso=aviso,
                      edad=edad, bajas=bajas, nivel=nivel if nivel in ("alojados", "historico", "todos") else ("todos" if bajas == "1" else "alojados"),
                      catalogo=db.listar_unidades(), cuenta=det.contar(),
                      alertas=det.cantidad_alertas() if det.puede_mantener(u) else 0)

    # ------------------------------------------------------------------ revisión de la base (administrador)
    @app.get("/detenidos/revision", response_class=HTMLResponse)
    def detenidos_revision(request: Request, tipo: str = "", q: str = "", unidad_id: str = "", fuerza_id: str = "", pag: str = "1", todas: str = "",
                           ia: str = "", orden: str = ""):
        exigir_mantener(request)
        import revision_ia
        a = det.alertas()
        tipo = tipo if tipo in det.TIPOS_ALERTA else next((t for t in det.TIPOS_ALERTA if a[t]), "duplicado")
        lista = a[tipo]
        uid = int(unidad_id) if str(unidad_id).isdigit() else None
        fid = int(fuerza_id) if str(fuerza_id).isdigit() else None
        palabras = det.norm_nombre(q).split()

        def pasa(al):
            personas = al["personas"] or []
            texto = " ".join([str(x.get("nombre_completo") or "") + " " + str(x.get("dni") or "") + " " + str(x.get("lpu") or "") for x in personas]
                             + [json.dumps(al.get("pendiente", {}).get("datos", {}), ensure_ascii=False)])
            if palabras and not all(w in det.norm_nombre(texto) for w in palabras):
                return False
            if uid and not any(x.get("unidad_id") == uid for x in personas) and al.get("pendiente", {}).get("datos", {}).get("unidad_id") != uid:
                return False
            if fid and not any(x.get("fuerza_id") == fid for x in personas):
                return False
            if tipo == "ia" and ia == "en_revision":  # Modificación 7
                return al["propuesta"].get("estado") == "EN_REVISION"
            if tipo == "ia" and ia and (al["propuesta"].get("veredicto") or "") != ("" if ia == "sin" else ia):
                return False
            return True
        filtrada = [al for al in lista if pasa(al)]
        if tipo == "ia" and orden == "primero":  # propuesta 41
            filtrada = revision_ia.ordenar_para_decidir_primero(filtrada)
        n_pag = int(pag) if str(pag).isdigit() and int(pag) > 0 else 1
        por = 100
        pagina_lista = filtrada[(n_pag - 1) * por: n_pag * por]
        historiales = (det.historiales_de([x["id"] for al in pagina_lista for x in (al.get("personas") or []) if x])
                       if tipo == "ia" else {})  # Modificación 7: una sola consulta para toda la página
        return pagina(request, "detenidos_revision.html", alertas=a, tipo=tipo, tipos=det.TIPOS_ALERTA, lista=pagina_lista, historiales=historiales,
                      total=len(filtrada), pag=n_pag, paginas=max(1, -(-len(filtrada) // por)), f={"q": q, "unidad_id": uid, "fuerza_id": fid, "ia": ia, "orden": orden},
                      calibracion=revision_ia.calibracion() if tipo == "ia" else None,
                      catalogo=db.listar_unidades(solo_activas=False), fuerzas=det.listar_fuerzas(False), recientes=det.decisiones_recientes(),
                      corridas=revision_ia.corridas(5) if tipo == "ia" else [], ia_en_curso=revision_ia.en_curso(),
                      niveles=revision_ia.contar_niveles() if tipo == "ia" else {}, veredictos=revision_ia.VEREDICTOS,
                      ia_ok=__import__("nucleo").ia_disponible(revision_ia.PROCESO))

    @app.get("/detenidos/revision/grupos", response_class=HTMLResponse)
    def detenidos_revision_grupos(request: Request):
        """Propuesta 31: posibles grupos de la misma persona (tres fichas o más conectadas por propuestas)."""
        exigir_mantener(request)
        import revision_ia
        return pagina(request, "detenidos_grupos.html", grupos=revision_ia.grupos_pendientes())

    @app.post("/detenidos/revision/grupos")
    async def detenidos_revision_grupos_decidir(request: Request):
        u = exigir_mantener(request)
        import revision_ia
        f = await request.form()
        ids = [int(x) for x in f.getlist("ficha") if str(x).isdigit()]
        try:
            if f.get("accion") == "unificar":
                n = revision_ia.unificar_grupo(ids, int(f.get("mantener") or 0), u)
                flash(request, f"Grupo unificado: {n} ficha(s) quedaron unidas a la #{f.get('mantener')}. Se pueden separar desde la ficha.")
            elif str(f.get("accion") or "").startswith("sacar:") and str(f.get("accion"))[6:].isdigit():
                sacar = int(str(f.get("accion"))[6:])
                n = revision_ia.sacar_del_grupo(ids, sacar, u)
                flash(request, f"Ficha #{sacar} sacada del grupo ({n} par(es) marcados como personas distintas).")
            else:
                flash(request, "Elegí qué hacer con el grupo.")
        except ValueError as e:
            flash(request, f"⚠ {e}")
        det.registrar_acceso(u, "DETENIDOS_REVISION", f"grupo {ids}: {f.get('accion')}", ip(request))
        return RedirectResponse(url="/detenidos/revision/grupos", status_code=303)

    @app.post("/detenidos/revision/ia")
    async def detenidos_revision_ia(request: Request):
        """«Revisar con IA ahora»: corre en segundo plano; las propuestas aparecen en la pestaña a medida que termina."""
        u = exigir_mantener(request)
        import revision_ia
        if revision_ia.en_segundo_plano("PEDIDO", usuario=u):
            det.registrar_acceso(u, "DETENIDOS_REVISION_IA", "revisión con IA a pedido", ip(request))
            flash(request, "La revisión con IA empezó. Puede tardar varios minutos; las propuestas aparecen en esta pestaña (actualizá la página).")
        else:
            flash(request, "Ya hay una revisión con IA en curso: esperá a que termine.")
        return RedirectResponse(url="/detenidos/revision?tipo=ia", status_code=303)

    @app.post("/detenidos/revision")
    async def detenidos_revision_decidir(request: Request):
        """Una alerta (botones de la tarjeta) o varias tildadas a la vez (barra de selección)."""
        u = exigir_mantener(request)
        f = await request.form()
        tipo = str(f.get("tipo") or "")
        decision = str(f.get("decision") or "")
        claves = [c for c in f.getlist("claves") if c] if f.get("lote") else [str(f.get("clave") or "")]
        volver_a = (f"/detenidos/revision?tipo={tipo}&q={quote(str(f.get('q') or ''))}&unidad_id={f.get('unidad_id') or ''}&fuerza_id={f.get('fuerza_id') or ''}"
                    + (f"&ia={quote(str(f.get('ia')))}" if f.get("ia") else ""))
        if not claves or not claves[0]:
            flash(request, "⚠ Tildá al menos una alerta.")
            return RedirectResponse(url=volver_a, status_code=303)
        hechas, errores = 0, []
        for clave in claves:
            mantener = int(f["mantener"]) if not f.get("lote") and str(f.get("mantener") or "").isdigit() else None
            if f.get("lote") and decision == "unificar" and not clave.startswith("ia:"):  # en las de la IA va la ficha que sugirió
                ids = [int(x) for x in clave.split(":")[1:]]
                mantener = det.ficha_a_conservar(ids)
            campos = {k[6:]: str(f.get(k) or "").strip() for k in f.keys() if k.startswith("campo_")} if not f.get("lote") else {}
            try:
                det.decidir_alerta(clave, decision, u, f.get("nota"), mantener, campos)
                hechas += 1
            except (ValueError, StopIteration) as e:
                errores.append(f"{clave}: {e}")
        det.registrar_acceso(u, "DETENIDOS_REVISION", f"{hechas} alerta(s): {decision}", ip(request))
        textos = {"unificar": "unificada(s)", "distintas": "marcadas como personas distintas", "baja": "dadas de baja por pena cumplida",
                  "sigue": "marcadas «sigue alojada»", "revisado": "marcadas como revisadas", "cargar": "cargada(s)", "sin_dni": "cargada(s) sin DNI",
                  "sin_fecha": "cargada(s) sin la fecha", "descartar": "descartada(s)", "en_revision": "dejada(s) en revisión"}
        flash(request, (f"{hechas} alerta(s) {textos.get(decision, 'resuelta(s)')}." if hechas else "")
                       + (f" ⚠ {len(errores)} no se pudieron resolver: " + "; ".join(errores[:5]) if errores else ""))
        return RedirectResponse(url=volver_a, status_code=303)

    # ------------------------------------------------------------------ alta
    @app.get("/detenidos/nueva", response_class=HTMLResponse)
    def detenidos_nueva(request: Request, dni: str = "", lpu: str = ""):
        exigir_ver(request)
        d = {"dni": dni, "lpu": lpu}
        externo = det.consultar_sistema_procuracion(dni, lpu) if (dni or lpu) else None
        if externo:  # precarga desde el sistema de la Procuración: siempre editable antes de guardar
            d.update({k: v for k, v in externo.items() if v not in (None, "") and k in det.ETIQUETA})
        return pagina(request, "detenido_form.html", titulo="Nueva persona detenida", accion="/detenidos/nueva",
                      precargado=bool(externo), **contexto_form(d))

    @app.post("/detenidos/nueva", response_class=HTMLResponse)
    async def detenidos_crear(request: Request):
        u = exigir_ver(request)
        datos = dict(await request.form())
        det_id, errores, avisos = det.guardar(datos, u)
        if errores:
            return pagina(request, "detenido_form.html", titulo="Nueva persona detenida", accion="/detenidos/nueva",
                          precargado=False, **contexto_form(datos, errores, avisos))
        flash(request, "Persona cargada." + (" " + " ".join(avisos) if avisos else ""))
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    # ------------------------------------------------------------------ importación (antes de /{id})
    @app.get("/detenidos/importar", response_class=HTMLResponse)
    def detenidos_importar(request: Request):
        exigir_admin(request)
        return pagina(request, "detenidos_importar.html", previa=None, token="", avisos=[], etiquetas=det.ETIQUETA,
                      fuerzas=det.listar_fuerzas(), listados=det.listados(10), hoy=date.today().isoformat())

    # ------------------------------------------------------------------ cargas de listados: panel y anulación completa
    @app.get("/detenidos/listados", response_class=HTMLResponse)
    def detenidos_listados(request: Request, ver: str = ""):
        exigir_admin(request)
        lista = det.listados(200)
        resumen = det.resumen_anulacion(int(ver)) if str(ver).isdigit() else None
        import archivo_cargas
        return pagina(request, "detenidos_listados.html", lista=lista, resumen=resumen, originales=archivo_cargas.de_cargas("detenidos"))

    @app.get("/detenidos/calidad", response_class=HTMLResponse)
    def detenidos_calidad(request: Request):
        """Propuesta 42: calidad de lo que manda cada fuerza, por trimestre."""
        exigir_admin(request)
        return pagina(request, "detenidos_calidad.html", periodos=det.calidad_por_fuerza(), temas=det.TEMAS_INCONSISTENCIA)

    @app.get("/detenidos/calidad.csv")
    def detenidos_calidad_csv(request: Request):
        u = exigir_admin(request)
        det.registrar_acceso(u, "DETENIDOS_CALIDAD_EXPORTAR", "tablero de calidad por fuerza", ip(request))
        return Response(det.calidad_csv(det.calidad_por_fuerza()), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="calidad_listados_por_fuerza.csv"'})

    @app.get("/detenidos/listados/{listado_id:int}", response_class=HTMLResponse)
    def detenidos_listado_informe(request: Request, listado_id: int, todo: str = ""):
        """Informe de la carga: unidades, alojados, condenados y procesados, altas, egresos del SPF y cambios de alojamiento."""
        exigir_admin(request)
        l = det.informe_de(listado_id)
        if not l:
            raise HTTPException(status_code=404, detail="La carga no existe.")
        import archivo_cargas
        return pagina(request, "detenidos_informe.html", l=l, inf=l.get("informe"), tope=None if todo == "1" else 300,
                      original=archivo_cargas.de_carga("detenidos", listado_id))

    @app.get("/detenidos/listados/{listado_id:int}/seccion", response_class=HTMLResponse)
    def detenidos_listado_seccion(request: Request, listado_id: int, s: str = "", q: str = "", pag: str = "1"):
        """Una sección del informe de la carga (se pide al desplegarla)."""
        u = exigir_admin(request)
        l = det.informe_de(listado_id)
        inf = (l or {}).get("informe") or {}
        columnas = {"altas": [("nombre", "Nombre"), ("dni", "DNI"), ("alojamiento", "Alojamiento")],
                    "egresos": [("nombre", "Nombre"), ("dni", "DNI"), ("alojamiento", "Último alojamiento"), ("ultimo_listado", "Último listado")],
                    "mudanzas": [("nombre", "Nombre"), ("dni", "DNI"), ("antes", "Antes"), ("despues", "Ahora")],
                    "reingresos": [("nombre", "Nombre"), ("dni", "DNI"), ("motivo", "Estaba de baja por")],
                    "documentos": [("nombre", "Nombre"), ("original", "Como vino en «DNI»"), ("aviso", "Qué se hizo")],
                    "descartadas": [("nombre", "Nombre"), ("dni", "DNI"), ("motivo", "Motivo")]}.get(s)
        if not columnas:
            raise HTTPException(status_code=404, detail="Sección desconocida.")
        filas = list(inf.get(s) or [])
        if q:
            palabras = det.norm_nombre(q).split()
            filas = [x for x in filas if all(w in det.norm_nombre(" ".join(str(x.get(k) or "") for k, _ in columnas)) for w in palabras)]
        n_pag = int(pag) if str(pag).isdigit() and int(pag) > 0 else 1
        return templates.TemplateResponse("_informe_seccion.html", dict(request=request, usuario=u, columnas=columnas, total=len(filas), q=q,
                                                                         filas=filas[(n_pag - 1) * 100: n_pag * 100], pag=n_pag, por_pag=100, enlace=s != "documentos" and s != "descartadas"))

    @app.post("/detenidos/listados/{listado_id:int}/anular")
    async def detenidos_listado_anular(request: Request, listado_id: int):
        u = exigir_admin(request)
        form = await request.form()
        try:
            r = det.anular_listado(listado_id, form.get("motivo"), u)
        except ValueError as e:
            flash(request, f"⚠ {e}")
            return RedirectResponse(url=f"/detenidos/listados?ver={listado_id}#anular", status_code=303)
        det.registrar_acceso(u, "DETENIDOS_ANULAR_CARGA", f"carga #{listado_id}: {r['bajas']} baja(s), {r['revertidos']} dato(s) revertido(s)", ip(request))
        flash(request, f"Carga anulada: {r['bajas']} persona(s) que había dado de alta quedaron de baja y {r['revertidos']} dato(s) volvieron a su valor anterior."
                       + (f" {r['respetados']} dato(s) se modificaron después de la carga y se respetaron." if r["respetados"] else "")
                       + ("" if r["exacto"] else " (Carga anterior a esta versión: se reconocieron sus cambios por fecha y usuario.)"))
        return RedirectResponse(url="/detenidos/listados", status_code=303)

    # ------------------------------------------------------------------ importación: vista previa por partes
    _cache_previa = {}

    def _leer_token(token, u):
        token = str(token or "")
        ruta = _carpeta_importaciones() / f"{token}.json"
        if not token or not token.replace("-", "").replace("_", "").isalnum() or not ruta.is_file():
            return None, None
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("usuario_id") != u["id"]:
            raise HTTPException(status_code=403, detail="Esta vista previa la generó otra persona.")
        return ruta, datos

    def _previa_de(token, datos):
        """La vista previa se calcula una vez por cada estado de las decisiones (desplegar o paginar no recalcula)."""
        clave = (token, json.dumps(datos.get("decisiones") or {}, sort_keys=True))
        if clave not in _cache_previa:
            if len(_cache_previa) > 6:
                _cache_previa.clear()
            _cache_previa[clave] = det.previa(datos["registros"], datos.get("fecha_listado"), datos.get("fuerza_id"), datos.get("decisiones"))
        return _cache_previa[clave]

    _cache_incons = {}

    def _incons_de(token, datos):
        """Inconsistencias del listado tal como vino (no dependen de las decisiones): una vez por vista previa."""
        if token not in _cache_incons:
            if len(_cache_incons) > 6:
                _cache_incons.clear()
            try:
                _cache_incons[token] = det.inconsistencias(datos["registros"], datos.get("fecha_listado"), datos.get("fuerza_id"))
            except Exception:
                logging.exception("No se pudieron calcular las inconsistencias")
                return None
        return _cache_incons[token]

    _cache_alertas = {}

    def _alertas_de(token, datos):
        """Propuesta 37: comparación con el listado anterior de la misma fuerza (una vez por vista previa)."""
        if token not in _cache_alertas:
            if len(_cache_alertas) > 6:
                _cache_alertas.clear()
            try:
                _cache_alertas[token] = det.alertas_listado(datos["registros"], datos.get("fuerza_id"), datos.get("fecha_listado"))
            except Exception:
                logging.exception("No se pudo comparar con el listado anterior")
                _cache_alertas[token] = []
        return _cache_alertas[token]

    _cache_esquema = {}

    def _esquema_de(token, datos):
        """Propuesta 36: control completo con el esquema de la fuerza (una vez por vista previa)."""
        if token not in _cache_esquema:
            if len(_cache_esquema) > 6:
                _cache_esquema.clear()
            try:
                _cache_esquema[token] = det.controlar_esquema(datos["registros"], datos.get("fuerza_id"))
            except Exception:
                logging.exception("No se pudo controlar el esquema del listado")
                _cache_esquema[token] = None
        return _cache_esquema[token]

    def _pagina_previa(request, token, datos, avisos=None):
        p = _previa_de(token, datos)
        fuerza = next((f for f in det.listar_fuerzas(False) if f["id"] == datos.get("fuerza_id")), None)
        ya = None
        if datos.get("sha256"):
            with db._conexion() as con:
                f = con.execute("SELECT * FROM cargas_archivos WHERE sha256 = ? AND tipo = 'detenidos' ORDER BY id LIMIT 1", (datos["sha256"],)).fetchone()
            ya = dict(f) if f else None
        return pagina(request, "detenidos_importar.html", previa=p, token=token, avisos=(avisos or []) + (datos.get("avisos") or []),
                      sin_catalogo=det.centros_sin_catalogo(datos["registros"], datos.get("fuerza_id")), unidades_catalogo=db.listar_unidades(),
                      fuerza_listado=datos.get("fuerza_id"), incons=_incons_de(token, datos), alertas_listado=_alertas_de(token, datos), esquema=_esquema_de(token, datos),
                      etiquetas=det.ETIQUETA, tipos_revisar=det.TIPOS_REVISAR, ya_cargado=ya,
                      meta=dict(fecha_listado=datos.get("fecha_listado"), archivo=datos.get("archivo"), fuerza=fuerza["nombre"] if fuerza else "",
                                filas=len(datos["registros"]), fuerza_sugerida=datos.get("fuerza_sugerida")),
                      unidades={x["id"]: x["nombre"] for x in db.listar_unidades(solo_activas=False)})

    @app.post("/detenidos/importar", response_class=HTMLResponse)
    async def detenidos_importar_previa(request: Request):
        u = exigir_admin(request)
        form = await request.form()
        avisos = []
        meta = {}
        contenido = None
        if form.get("desde_mesa"):
            registros, origen = det.desde_planillas_expedientes(), "PLANILLAS_MESA"
            avisos.append(f"Se leyeron {len(registros)} persona(s) con nombre y DNI o LPU de las planillas de expedientes de Mesa.")
        else:
            subido = form.get("planilla")
            if not subido or not getattr(subido, "filename", ""):
                flash(request, "Elegí la planilla para importar.")
                return RedirectResponse(url="/detenidos/importar", status_code=303)
            # fuerza y fecha del listado: se indican a mano al importar (la fuerza se elige o se crea)
            fecha_listado = str(form.get("fecha_listado") or "").strip()
            try:
                fecha_listado = date.fromisoformat(fecha_listado).isoformat()
            except ValueError:
                flash(request, "⚠ Indicá la fecha del listado.")
                return RedirectResponse(url="/detenidos/importar", status_code=303)
            if fecha_listado > date.today().isoformat():
                flash(request, "⚠ La fecha del listado no puede ser futura.")
                return RedirectResponse(url="/detenidos/importar", status_code=303)
            try:
                if form.get("fuerza_id") == "nueva":
                    fuerza_id = det.crear_fuerza(form.get("fuerza_nueva"), form.get("fuerza_sigla"), u)
                elif str(form.get("fuerza_id") or "").isdigit():
                    fuerza_id = int(form["fuerza_id"])
                else:
                    raise ValueError("Elegí la fuerza que mandó el listado (o creala si no está).")
            except ValueError as e:
                flash(request, f"⚠ {e}")
                return RedirectResponse(url="/detenidos/importar", status_code=303)
            meta = {"fecha_listado": fecha_listado, "fuerza_id": fuerza_id, "archivo": subido.filename}
            contenido = await subido.read()
            try:
                registros, avisos = det.leer_archivo(subido.filename, contenido)
            except Exception as e:
                registros, avisos = [], [f"No se pudo leer el archivo: {e}"]
            origen = "IMPORTADO"
        if not registros:
            flash(request, " ".join(avisos) or "La planilla no tenía filas para tomar.")
            return RedirectResponse(url="/detenidos/importar", status_code=303)
        token = secrets.token_urlsafe(16)
        sugerida = det.fuerza_sugerida(registros)
        if sugerida and meta.get("fuerza_id") and sugerida["id"] != meta["fuerza_id"]:
            avisos.append(f"⚠ Según las unidades que trae, el listado parece de {sugerida['nombre']} ({sugerida['n']} de {len(registros)} filas en unidades a su cargo). Revisá la fuerza elegida.")
        datos = {"usuario_id": u["id"], "origen": origen, "registros": registros, "sin_catalogo": [c for c, _, _ in det.centros_sin_catalogo(registros)],
                 "decisiones": {}, "avisos": avisos, "fuerza_sugerida": sugerida, **meta}
        if contenido is not None:
            import archivo_cargas
            (_carpeta_importaciones() / f"{token}.orig").write_bytes(contenido)  # el original se archiva al confirmar
            datos["sha256"] = archivo_cargas.huella(contenido)
        (_carpeta_importaciones() / f"{token}.json").write_text(json.dumps(datos, default=str, ensure_ascii=False), encoding="utf-8")
        return _pagina_previa(request, token, datos)

    @app.get("/detenidos/importar/previa", response_class=HTMLResponse)
    def detenidos_importar_ver(request: Request, token: str = ""):
        u = exigir_admin(request)
        _, datos = _leer_token(token, u)
        if not datos:
            flash(request, "La vista previa venció: volvé a subir la planilla.")
            return RedirectResponse(url="/detenidos/importar", status_code=303)
        return _pagina_previa(request, token, datos)

    POR_PAG = 100

    def _filas_seccion(p, s, tipo="", q="", unidades=None):
        """Las filas de una sección de la vista previa, con sus filtros."""
        unidades = unidades or {}
        if s == "revisar":
            filas = [dict(r, nombre=r["reg"].get("nombre_completo"), doc=r["reg"].get("dni"), lugar=r["reg"].get("centro_otro") or unidades.get(r["reg"].get("unidad_id"), ""))
                     for r in p["revisar"] if not tipo or r["tipo"] == tipo]
        elif s == "posibles":
            filas = [dict(x, nombre=x["reg"].get("nombre_completo"), doc=x["reg"].get("dni") or x["reg"].get("lpu"), lugar=x["reg"].get("centro_otro") or unidades.get(x["reg"].get("unidad_id"), "")) for x in p["posibles"]]
        elif s == "nuevas":
            filas = [{"n": None, "nombre": r.get("nombre_completo"), "doc": r.get("dni") or "", "lpu": r.get("lpu") or "", "expte": r.get("nro_expediente_ppn") or "",
                      "lugar": r.get("centro_otro") or unidades.get(r.get("unidad_id"), "")} for r in p["nuevas"]]
        elif s == "actualizar":
            filas = [{"id": det_id, "nombre": comb.get("nombre_completo"), "doc": comb.get("dni") or "", "cambios": cambios} for det_id, comb, cambios, _ in p["actualizar"]]
        elif s == "mudanzas":
            filas = list(p["mudanzas"])
        elif s == "egresos":
            filas = list(p["egresos"])
        elif s == "documentos":
            filas = list(p["documentos"])
        elif s == "conservado":
            filas = [{"id": i, "nombre": nom, "fecha": f} for i, nom, f in p["conservado"]]
        elif s == "descartadas":
            filas = [{"n": d["n"], "nombre": d["reg"].get("nombre_completo"), "doc": d["reg"].get("dni"), "motivo": d.get("motivo", "descartada a mano")} for d in p["descartadas"]]
        else:
            filas = []
        if q:
            palabras = det.norm_nombre(q).split()
            filas = [f for f in filas if all(w in det.norm_nombre(" ".join(str(f.get(k) or "") for k in ("nombre", "doc", "lpu", "lugar", "dni", "original"))) for w in palabras)]
        return filas

    @app.get("/detenidos/importar/seccion", response_class=HTMLResponse)
    def detenidos_importar_seccion(request: Request, token: str = "", s: str = "", tipo: str = "", q: str = "", pag: str = "1"):
        """Una sección de la vista previa: se pide recién al desplegarla (la página abre con el resumen)."""
        u = exigir_admin(request)
        _, datos = _leer_token(token, u)
        if not datos:
            return HTMLResponse('<p class="aviso aviso--atencion">La vista previa venció: volvé a subir la planilla.</p>')
        p = _previa_de(token, datos)
        unidades = {x["id"]: x["nombre"] for x in db.listar_unidades(solo_activas=False)}
        filas = _filas_seccion(p, s, tipo, q, unidades)
        n_pag = int(pag) if str(pag).isdigit() and int(pag) > 0 else 1
        total = len(filas)
        decisiones = datos.get("decisiones") or {}
        return templates.TemplateResponse("_detenidos_seccion.html", dict(
            request=request, usuario=u, s=s, tipo=tipo, q=q, pag=n_pag, total=total, por_pag=POR_PAG, token=token,
            filas=filas[(n_pag - 1) * POR_PAG: n_pag * POR_PAG], tipos_revisar=det.TIPOS_REVISAR, decisiones=decisiones,
            por_tipo=p["por_tipo"], etiquetas=det.ETIQUETA))

    @app.post("/detenidos/importar/decidir")
    async def detenidos_importar_decidir(request: Request):
        """Acción sobre filas de la vista previa: una, varias tildadas o todas las de un filtro."""
        u = exigir_admin(request)
        f = await request.form()
        token = str(f.get("token") or "")
        ruta, datos = _leer_token(token, u)
        if not datos:
            flash(request, "La vista previa venció: volvé a subir la planilla.")
            return RedirectResponse(url="/detenidos/importar", status_code=303)
        accion = str(f.get("accion") or "")
        solo = None
        if ":" in accion and accion.split(":", 1)[1].isdigit():  # botón de una sola fila («corregir:12», «vale:12»…)
            accion, solo = accion.split(":", 1)[0], int(accion.split(":", 1)[1])
        if accion not in ("corregir", "sin_dni", "sin_fecha", "misma", "otra", "vale", "descartar", "deshacer"):
            flash(request, "⚠ Elegí qué hacer con las filas.")
            return RedirectResponse(url=f"/detenidos/importar/previa?token={token}", status_code=303)
        s, tipo, q = str(f.get("s") or ""), str(f.get("tipo") or ""), str(f.get("q") or "")
        if solo:
            filas = [solo]
        elif f.get("todas") == "1":
            p = _previa_de(token, datos)
            filas = [x["n"] for x in _filas_seccion(p, s, tipo, q) if x.get("n")]
        else:
            filas = [int(x) for x in f.getlist("fila") if str(x).isdigit()]
        if not filas:
            flash(request, "⚠ Tildá al menos una fila.")
            return RedirectResponse(url=f"/detenidos/importar/previa?token={token}#sec-{s}", status_code=303)
        dec = datos.setdefault("decisiones", {})
        for n in filas:
            if accion == "deshacer":
                dec.pop(str(n), None)
            elif accion == "corregir":
                campos = {k: str(f.get(f"{k}_{n}") or "").strip() for k in ("dni", "lpu", "nombre_completo", "fecha_nacimiento") if f.get(f"{k}_{n}") is not None}
                dec[str(n)] = {"accion": "corregir", "campos": campos}
            else:
                dec[str(n)] = {"accion": accion, "campos": (dec.get(str(n)) or {}).get("campos", {}) if isinstance(dec.get(str(n)), dict) else {}}
        ruta.write_text(json.dumps(datos, default=str, ensure_ascii=False), encoding="utf-8")
        nombres = {"vale": "marcada(s) como la fila que vale", "corregir": "corregida(s)", "sin_dni": "a cargar sin DNI", "sin_fecha": "a cargar sin la fecha", "misma": "marcada(s) como la misma persona",
                   "otra": "marcada(s) como otra persona", "descartar": "descartada(s)", "deshacer": "vuelta(s) a «sin resolver»"}
        flash(request, f"{len(filas)} fila(s) {nombres[accion]}. La vista previa se actualizó.")
        return RedirectResponse(url=f"/detenidos/importar/previa?token={token}#sec-{s}", status_code=303)

    @app.post("/detenidos/importar/confirmar")
    async def detenidos_importar_confirmar(request: Request):
        u = exigir_admin(request)
        form = await request.form()
        token = str(form.get("token") or "")
        ruta, datos = _leer_token(token, u)
        if not datos:
            flash(request, "La vista previa venció: volvé a subir la planilla.")
            return RedirectResponse(url="/detenidos/importar", status_code=303)
        alertas = _alertas_de(token, datos)  # propuesta 37: la pantalla pide marcar que se revisaron (casilla obligatoria)
        if alertas and det.EXTERNO.get("exigir_confirmar_alertas") and form.get("confirmo_alertas") != "1":
            flash(request, "Este listado tiene diferencias grandes con el anterior de la misma fuerza: revisalas y marcá que las viste para confirmar.")
            return RedirectResponse(url=f"/detenidos/importar/previa?token={token}#alertas-listado", status_code=303)
        if alertas:
            det.registrar_acceso(u, "DETENIDOS_IMPORTAR_CON_ALERTAS", " | ".join(alertas)[:300], ip(request))
        ruta.unlink(missing_ok=True)
        # lugares que no estaban en el catálogo: la persona eligió vincularlos a una unidad o dejarlos como figuran en el listado
        centros = datos.get("sin_catalogo") or [c for c, _, _ in det.centros_sin_catalogo(datos["registros"])]
        vinculos = {c: form.get(f"vinculo_{i}") for i, c in enumerate(centros) if str(form.get(f"vinculo_{i}") or "").isdigit()}
        vinculadas = det.vincular_centros(datos["registros"], vinculos, u, recordar=form.get("recordar_vinculos") == "1") if vinculos else 0
        if vinculos:
            det.registrar_acceso(u, "DETENIDOS_VINCULAR_UNIDAD", "; ".join(f"«{k}» → unidad {v}" for k, v in vinculos.items())[:900], ip(request))
        alcance = form.get("egresos") if form.get("egresos") in ("todos", "unidades", "ninguno") else "ninguno"
        decisiones = dict(datos.get("decisiones") or {})
        decisiones.update({k[6:]: {"accion": form.get(k)} for k in form.keys() if k.startswith("misma_") and form.get(k) in ("misma", "otra")})
        r = det.aplicar_importacion(datos["registros"], u, origen=datos.get("origen", "IMPORTADO"), fecha_listado=datos.get("fecha_listado"),
                                    fuerza_id=datos.get("fuerza_id"), archivo=datos.get("archivo"), egresos=alcance, decisiones=decisiones)
        orig = _carpeta_importaciones() / f"{token}.orig"
        if orig.is_file():
            import archivo_cargas
            archivo_cargas.guardar("detenidos", r["listado_id"], datos.get("archivo"), orig.read_bytes(), u)
            orig.unlink(missing_ok=True)
        det.registrar_acceso(u, "DETENIDOS_IMPORTAR", f"{r['altas']} altas, {r['actualizadas']} actualizadas, {r['egresos']} egreso(s)", ip(request))
        try:  # la IA constata contra toda la base a las personas de esta carga (en segundo plano; solo propone)
            import revision_ia
            with db._conexion() as con:
                ids_carga = [x[0] for x in con.execute("SELECT id FROM detenidos WHERE listado_id = ? AND activo = 1", (r["listado_id"],))]
            if ids_carga:
                revision_ia.en_segundo_plano(f"CARGA #{r['listado_id']}", ids=ids_carga, usuario=u)
        except Exception:
            logging.exception("No se pudo iniciar la revisión con IA de la carga")
        flash(request, f"Importación hecha: {r['altas']} alta(s), {r['actualizadas']} actualizada(s)"
                       + (f", alojamiento actualizado en {r['aloj_actualiza']}" if r.get("aloj_actualiza") else "")
                       + (f", {r['conservado']} con alojamiento conservado (el listado era anterior; quedó registrado)" if r.get("conservado") else "")
                       + (f", {r['documentos']} documento(s) extranjero(s) guardado(s) aparte" if r.get("documentos") else "")
                       + (f", {r['pendientes']} fila(s) pendiente(s) en Revisión de la base" if r.get("pendientes") else "")
                       + (f", {r['descartadas']} descartada(s)" if r.get("descartadas") else "")
                       + (f", {r['unificadas']} ficha(s) unificada(s)" if r.get("unificadas") else "")
                       + (f", {r['egresos']} egreso(s) del SPF" if r.get("egresos") else "")
                       + (f", {r['reingresos']} reingreso(s)" if r.get("reingresos") else "")
                       + (f", {len(vinculos)} lugar(es) vinculado(s) a unidades del catálogo ({vinculadas} persona(s))" if vinculos else "") + ".")
        return RedirectResponse(url=f"/detenidos/listados/{r['listado_id']}", status_code=303)

    def _temas(request):
        q = getattr(request, "query_params", {}) or {}
        v = q.getlist("tema") if hasattr(q, "getlist") else q.get("tema")
        return [v] if isinstance(v, str) else list(v or [])

    def _entregar_reporte(u, datos_inc, meta, formato, request, detalle):
        """Informe de inconsistencias para devolver a la fuerza: PDF (Word o LibreOffice) o .docx."""
        import tempfile
        import despacho
        if formato not in ("pdf", "docx"):
            raise HTTPException(status_code=404, detail="Formato no disponible (pdf o docx).")
        base = re.sub(r"[^\w\-]+", "_", f"inconsistencias_{meta.get('fuerza') or 'listado'}_{meta.get('fecha_listado') or ''}").strip("_")
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / f"{base}.docx"
            det.reporte_inconsistencias(datos_inc, meta, str(docx))
            nombre, contenido, tipo = f"{base}.docx", docx.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if formato == "pdf":
                pdf = Path(tmp) / f"{base}.pdf"
                try:
                    despacho.convertir_a_pdf(str(docx), str(pdf))
                    nombre, contenido, tipo = f"{base}.pdf", pdf.read_bytes(), "application/pdf"
                except Exception as e:  # sin Word ni LibreOffice: se entrega el .docx igual
                    logging.warning(f"No se pudo pasar a PDF el informe de inconsistencias: {e}")
        det.registrar_acceso(u, "DETENIDOS_REPORTE_INCONSISTENCIAS", detalle, ip(request))
        return Response(contenido, media_type=tipo, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    @app.get("/detenidos/importar/inconsistencias.{formato}")
    def detenidos_importar_inconsistencias(request: Request, formato: str, token: str = ""):
        """Desde la vista previa (antes de confirmar)."""
        u = exigir_admin(request)
        ruta, datos = _leer_token(token, u)
        if not datos:
            raise HTTPException(status_code=404, detail="La vista previa venció: volvé a subir la planilla.")
        fuerza = next((f["nombre"] for f in det.listar_fuerzas(False) if f["id"] == datos.get("fuerza_id")), "")
        inc = det.inconsistencias(datos["registros"], datos.get("fecha_listado"), datos.get("fuerza_id"))
        meta = {"fuerza": fuerza, "fecha_listado": datos.get("fecha_listado"), "archivo": datos.get("archivo")}
        inc = det.filtrar_inconsistencias(inc, _temas(request))
        return _entregar_reporte(u, inc, meta, formato, request, f"vista previa: {datos.get('archivo')} · temas: {', '.join(inc.get('temas') or ['todos'])}")

    @app.get("/detenidos/listados/{listado_id:int}/inconsistencias.{formato}")
    def detenidos_listado_inconsistencias(request: Request, listado_id: int, formato: str):
        """De una carga ya hecha: lo que se calculó al cargarla (o, si es anterior a esta versión, desde el archivo original)."""
        u = exigir_admin(request)
        l = det.informe_de(listado_id)
        if not l:
            raise HTTPException(status_code=404, detail="La carga no existe.")
        inc = (l.get("informe") or {}).get("inconsistencias")
        if inc is None:
            import archivo_cargas
            reg = archivo_cargas.de_carga("detenidos", listado_id)
            if not reg:
                raise HTTPException(status_code=404, detail="Esta carga es anterior a esta versión y no tiene el archivo original guardado.")
            nombre, contenido = archivo_cargas.leer(reg)
            registros, _ = det.leer_archivo(nombre, contenido)
            inc = det.inconsistencias(registros, l.get("fecha_listado"), l.get("fuerza_id"))
        meta = {"fuerza": l.get("fuerza"), "fecha_listado": l.get("fecha_listado"), "archivo": l.get("archivo"),
                "fecha_carga": f"{l['creado_en'][8:10]}/{l['creado_en'][5:7]}/{l['creado_en'][:4]}" if l.get("creado_en") else None}
        inc = det.filtrar_inconsistencias(inc, _temas(request))
        return _entregar_reporte(u, inc, meta, formato, request, f"carga #{listado_id} · temas: {', '.join(inc.get('temas') or ['todos'])}")

    @app.get("/detenidos/listados/{listado_id:int}/original")
    def detenidos_listado_original(request: Request, listado_id: int):
        u = exigir_admin(request)
        import archivo_cargas
        reg = archivo_cargas.de_carga("detenidos", listado_id)
        if not reg:
            raise HTTPException(status_code=404, detail="Esta carga no tiene el archivo original guardado (es anterior a esta versión).")
        try:
            nombre, contenido = archivo_cargas.leer(reg)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        det.registrar_acceso(u, "DETENIDOS_DESCARGA_ORIGINAL", f"carga #{listado_id}: {nombre}", ip(request))
        return Response(contenido, media_type="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    # ------------------------------------------------------------------ exportación
    @app.get("/detenidos/exportar.{formato}")
    def detenidos_exportar(request: Request, formato: str, q: str = "", unidad_id: str = "", edad: str = "", bajas: str = "",
                           perfil: str = "", motivo: str = ""):
        u = exigir_admin(request)
        if formato not in ("xlsx", "csv"):
            raise HTTPException(status_code=404, detail="Formato no disponible")
        lista = det.buscar(**filtros(q, unidad_id, edad, bajas), limite=100000)
        cols, constancia, error = det.preparar_exportacion(u, perfil, motivo, len(lista), ip(request), formato)  # propuesta 46
        if error:
            flash(request, error)
            return RedirectResponse(url="/detenidos#exportar", status_code=303)
        if formato == "xlsx":
            return Response(det.exportar_excel(lista, cols, constancia), media_type=XLSX,
                            headers={"Content-Disposition": 'attachment; filename="personas_detenidas.xlsx"'})
        if formato == "csv":
            return Response(det.exportar_csv(lista, cols), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="personas_detenidas.csv"'})
        raise HTTPException(status_code=404, detail="Formato no disponible")

    # ------------------------------------------------------------------ búsqueda colectiva
    def filtros_colectivos(params):
        def entero(k):
            v = str(params.get(k) or "").strip()
            return int(v) if v.isdigit() else None
        return dict(q=str(params.get("q") or "").strip()[:120], nacionalidades=[n for n in params.getlist("nacionalidad") if n][:30],
                    fuerza_id=entero("fuerza_id"), tipo_unidad=str(params.get("tipo") or "")[:60], unidad_id=entero("unidad_id"),
                    juzgado=str(params.get("juzgado") or "").strip()[:120], edad_min=entero("edad_min"), edad_max=entero("edad_max"),
                    vigencia_f=params.get("vigencia") if params.get("vigencia") in ("al_dia", "desactualizado", "sin_fecha") else "",
                    incluir_bajas=params.get("bajas") == "1")

    POR_PAGINA = 50

    @app.get("/detenidos/busqueda", response_class=HTMLResponse)
    def detenidos_busqueda(request: Request):
        u = exigir_ver(request)
        params = request.query_params
        f = filtros_colectivos(params)
        lista, res = det.busqueda_colectiva(**f)
        pagina_n = max(1, int(params.get("pagina")) if str(params.get("pagina") or "").isdigit() else 1)
        paginas = max(1, -(-len(lista) // POR_PAGINA))
        pagina_n = min(pagina_n, paginas)
        det.registrar_acceso(u, "DETENIDOS_BUSQUEDA", f"{len(lista)} resultado(s)", ip(request))
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.multi_items() if k != "pagina")
        return pagina(request, "detenidos_busqueda.html", lista=lista[(pagina_n - 1) * POR_PAGINA: pagina_n * POR_PAGINA], res=res, f=f,
                      op=det.opciones_busqueda(), pagina=pagina_n, paginas=paginas, qs=qs, dias_vigencia=det.DIAS_VIGENCIA)

    @app.get("/detenidos/busqueda/exportar.{formato}")
    @app.post("/detenidos/busqueda/exportar.{formato}")
    async def detenidos_busqueda_exportar(request: Request, formato: str):
        u = exigir_admin(request)
        params = request.query_params
        lista, _ = det.busqueda_colectiva(**filtros_colectivos(params))
        if request.method == "POST":  # solo los marcados
            form = await request.form()
            ids = {int(x) for x in form.getlist("id") if str(x).isdigit()}
            if ids:
                lista = [d for d in lista if d["id"] in ids]
        cols, constancia, error = det.preparar_exportacion(u, params.get("perfil", ""), params.get("motivo", ""), len(lista), ip(request),
                                                           formato, "búsqueda colectiva ")  # propuesta 46
        if error:
            flash(request, error)
            return RedirectResponse(url="/detenidos/busqueda", status_code=303)
        if formato == "xlsx":
            return Response(det.exportar_excel(lista, cols, constancia), media_type=XLSX,
                            headers={"Content-Disposition": 'attachment; filename="busqueda_personas_detenidas.xlsx"'})
        if formato == "csv":
            return Response(det.exportar_csv(lista, cols), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="busqueda_personas_detenidas.csv"'})
        raise HTTPException(status_code=404, detail="Formato no disponible")

    # ------------------------------------------------------------------ ficha, edición, baja
    def sensibles_tapados(request, u, d):
        """Propuesta 45: ¿hay que tapar los datos sensibles de esta ficha para esta persona ahora?"""
        import time as _time

        from dgpdh.dominio import sensibles
        if not det.EXTERNO.get("ocultar_sensibles") or det.puede_mantener(u) or not sensibles.hay_sensibles(d):
            return False
        hasta = (getattr(request, "session", None) or {}).get(f"sensibles_{d['id']}")
        return not (hasta and float(hasta) > _time.time())

    @app.post("/detenidos/{det_id}/ver-sensibles")
    async def detenidos_ver_sensibles(request: Request, det_id: int):
        import time as _time

        from dgpdh.dominio import sensibles
        u = exigir_ver(request)
        obtener_o_404(det_id)
        motivo = str((await request.form()).get("motivo") or "").strip()
        if len(motivo) < 5:
            flash(request, "Para ver los datos protegidos escribí el motivo (queda registrado).")
            return RedirectResponse(url=f"/detenidos/{det_id}#sensibles", status_code=303)
        det.registrar_acceso(u, "DETENIDO_VER_SENSIBLE", f"#{det_id}: {motivo}"[:300], ip(request))
        request.session[f"sensibles_{det_id}"] = str(_time.time() + sensibles.MINUTOS_VISIBLE * 60)
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    @app.get("/detenidos/{det_id}", response_class=HTMLResponse)
    def detenidos_ficha(request: Request, det_id: int):
        from dgpdh.dominio import sensibles
        import expedientes as _exp
        u = exigir_ver(request)
        d = obtener_o_404(det_id)
        det.registrar_acceso(u, "DETENIDO_VER", f"#{det_id}", ip(request))
        tapados = sensibles_tapados(request, u, d)
        historial = det.historial(det_id)
        if tapados:
            d, historial = sensibles.tapar(d), sensibles.tapar_historial(historial)
        sugerencias_expte = []
        if not tapados and (d.get("dni") or d.get("lpu")):
            filtros = {k: d[k] for k in ("dni", "lpu") if d.get(k)}
            try:
                resultados, _ = _exp.buscar_campos(filtros)
                for r in resultados:
                    exptes = _exp.valores_de(r, "expediente")
                    nombres = _exp.valores_de(r, "nombre")
                    sugerencias_expte.append({
                        "expediente": exptes[0] if exptes else None,
                        "nombre": nombres[0] if nombres else None,
                        "archivo": r["archivo"],
                        "fila": r["fila"],
                    })
            except Exception:
                pass
        return pagina(request, "detenido_ficha.html", d=d, campos=det.ETIQUETA, historial=historial, sensibles_tapados=tapados,
                      notas=det.notas_relacionadas(d), accesos=det.accesos(det_id) if det.puede_administrar(u) else [],
                      unificaciones=det.unificaciones_de(det_id) if det.puede_mantener(u) else [],
                      tramos=det.historial_alojamiento(det_id), nombres_unidades={x["id"]: x["nombre"] for x in db.listar_unidades(solo_activas=False)},
                      sugerencias_expte=sugerencias_expte,
                      puede_administrar=det.puede_administrar(u))

    @app.post("/detenidos/{det_id}/vincular-expediente")
    async def detenidos_vincular_expediente(request: Request, det_id: int):
        """Cambio 5 nov. 2026: vincula el nro_expediente_ppn con un número encontrado en las planillas."""
        u = exigir_ver(request)
        actual = obtener_o_404(det_id)
        form = await request.form()
        expediente = (form.get("expediente") or "").strip()[:100]
        if not expediente:
            flash(request, "⚠ No se recibió un número de expediente.")
            return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)
        datos = {c: actual.get(c) for c, _, _ in det.CAMPOS}
        datos["nro_expediente_ppn"] = expediente
        _, errores, _ = det.guardar(datos, u, det_id)
        if errores:
            flash(request, "⚠ Error al guardar: " + "; ".join(errores))
        else:
            flash(request, f"Expediente PPN vinculado: {expediente}")
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    @app.post("/detenidos/{det_id}/desvincular-expediente")
    async def detenidos_desvincular_expediente(request: Request, det_id: int):
        """Cambio 8 nov. 2026: quita el nro_expediente_ppn de la ficha (con auditoría)."""
        u = exigir_admin(request)
        actual = obtener_o_404(det_id)
        anterior = actual.get("nro_expediente_ppn")
        if not anterior:
            flash(request, "La ficha no tiene expediente vinculado.")
            return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)
        datos = {c: actual.get(c) for c, _, _ in det.CAMPOS}
        datos["nro_expediente_ppn"] = None
        _, errores, _ = det.guardar(datos, u, det_id)
        if errores:
            flash(request, "⚠ Error al desvincular: " + "; ".join(errores))
        else:
            flash(request, f"Expediente desvinculado (era: {anterior}).")
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    @app.post("/detenidos/{det_id}/separar/{unificacion_id}")
    async def detenidos_separar(request: Request, det_id: int, unificacion_id: int):
        """Propuesta 33: deshacer una unificación («Separar fichas»). Quien mantiene la base, con motivo."""
        from dgpdh.errores import ErrorDGPDH
        u = exigir_mantener(request)
        obtener_o_404(det_id)
        if not any(x.id == unificacion_id for x in det.unificaciones_de(det_id)):
            raise HTTPException(status_code=404, detail="Esa unificación no es de esta ficha")
        motivo = str((await request.form()).get("motivo") or "")
        try:
            r = det.separar(unificacion_id, u, motivo, ip(request))
        except ErrorDGPDH as e:
            flash(request, e.mensaje)
            return RedirectResponse(url=f"/detenidos/{det_id}#unificaciones", status_code=303)
        texto = "Fichas separadas. La otra ficha " + ("volvió a alojados." if r.absorbida_activa else "quedó en el histórico: revisá si sigue alojada.")
        if r.conservados:
            texto += " Se conservaron sin cambios los datos que alguien modificó después de unificar: " + ", ".join(
                det.ETIQUETA.get(c, c) for c in r.conservados) + "."
        flash(request, texto)
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    @app.get("/detenidos/{det_id}/editar", response_class=HTMLResponse)
    def detenidos_editar(request: Request, det_id: int):
        u = exigir_ver(request)
        d = obtener_o_404(det_id)
        if sensibles_tapados(request, u, d):  # propuesta 45: no se edita lo que no se ve
            flash(request, "Para editar esta ficha primero mostrá los datos protegidos (queda registrado el motivo).")
            return RedirectResponse(url=f"/detenidos/{det_id}#sensibles", status_code=303)
        return pagina(request, "detenido_form.html", titulo=f"Editar · {d['nombre_completo']}", accion=f"/detenidos/{det_id}/editar",
                      precargado=False, det_id=det_id, **contexto_form(d))

    @app.post("/detenidos/{det_id}/editar", response_class=HTMLResponse)
    async def detenidos_actualizar(request: Request, det_id: int):
        u = exigir_ver(request)
        actual = obtener_o_404(det_id)
        datos = dict(await request.form())
        for c, _, _ in det.CAMPOS:  # un campo que el formulario no trae se conserva (no se borra por omisión)
            datos.setdefault(c, actual.get(c))
        _, errores, avisos = det.guardar(datos, u, det_id)
        if errores:
            return pagina(request, "detenido_form.html", titulo="Editar persona detenida", accion=f"/detenidos/{det_id}/editar",
                          precargado=False, det_id=det_id, **contexto_form(datos, errores, avisos))
        flash(request, "Cambios guardados.")
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    @app.post("/detenidos/{det_id}/baja")
    async def detenidos_baja(request: Request, det_id: int):
        u = exigir_admin(request)
        obtener_o_404(det_id)
        motivo = str((await request.form()).get("motivo") or "").strip()
        if not motivo:
            flash(request, "Indicá el motivo de la baja (por ejemplo: egreso, traslado a otra jurisdicción, carga duplicada).")
            return RedirectResponse(url=f"/detenidos/{det_id}#baja", status_code=303)
        det.dar_de_baja(det_id, motivo, u)
        flash(request, "Persona dada de baja. Sigue en la base y se puede reactivar.")
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    @app.post("/detenidos/{det_id}/reactivar")
    def detenidos_reactivar(request: Request, det_id: int):
        u = exigir_admin(request)
        obtener_o_404(det_id)
        det.reactivar(det_id, u)
        flash(request, "Persona reactivada.")
        return RedirectResponse(url=f"/detenidos/{det_id}", status_code=303)

    # ------------------------------------------------------------------ API de lectura para otros sistemas
    def exigir_token(request):
        esperado = det.EXTERNO.get("api_token") or ""
        recibido = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if not esperado or not hmac.compare_digest(recibido.encode(), esperado.encode()):
            raise HTTPException(status_code=401, detail="Token inválido o API desactivada ([detenidos] api_token en config.ini).")

    @app.get("/api/detenidos")
    def api_detenidos(request: Request, dni: str = "", lpu: str = "", q: str = "", bajas: str = ""):
        exigir_token(request)
        if dni or lpu:
            det_id = det.existente({"dni": dni, "lpu": lpu})
            lista = [det.obtener(det_id)] if det_id else []
        else:
            lista = det.buscar(q=q[:120], incluir_bajas=(bajas == "1"), limite=5000)
        det.registrar_acceso(None, "DETENIDOS_API", f"{len(lista)} resultado(s)", ip(request))
        from dgpdh.dominio import exportaciones  # propuesta 46: el token puede limitarse a ciertos campos
        return JSONResponse({"total": len(lista), "personas": [exportaciones.campos_api(det.a_json(d), det.EXTERNO.get("api_campos")) for d in lista]})
