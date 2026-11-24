"""Interfaz (propuestas 98–164) en pantalla: se llama a las rutas con el arnés y se mira el HTML que devuelven."""
import re
import arnes_ui as a
from arnes_ui import R, req, run, body, falla, direc, ana, coo, caro
import database as db

base = (a.Path(a.APP) / "templates" / "base.html").read_text(encoding="utf-8")

# ---------------------------------------------------------------- 98, 99 y 116: verificación de lo ya hecho
assert '<script src="/static/htmx.min.js' in base and "cdnjs" not in base, "htmx local (propuesta 98)"
assert (a.Path(a.APP) / "pruebas" / "test_sin_recursos_remotos.py").is_file(), "prueba de cero recursos remotos (propuesta 99)"
assert 'if (tipo === "ok") setTimeout' in base and "aviso-flotante__cerrar" in base, "los errores no se van solos (propuesta 116)"
assert base.count('estilos_ui.css') == 1 and "_ui_scripts.html" in base
print("98, 99, 116 OK")

# ---------------------------------------------------------------- 110, 145, 131, 108, 109: bandeja de firma
n_urg = a.nota("Urgente.docx", prioridad="URGENTE")
n_norm = a.nota("Normal.docx")
with db._conexion() as con:
    con.execute("UPDATE notas SET vence = date('now', '-1 day') WHERE id = ?", (n_norm,))
html = body(R[("GET", "/")](req(direc, "/")))
grupos = re.findall(r'<tr class="fila-grupo fila-grupo--(\w+)">', html)
assert grupos[0] == "vencida" and len(grupos) >= 2, grupos  # el plazo vencido va primero
assert f'<a class="fila-enlace" href="/nota/{n_urg}" data-vista="/ui/nota/{n_urg}/vista">Urgente.docx</a>' in html
tabla = html[html.index('id="tabla-firma"'):html.index("</table>", html.index('id="tabla-firma"'))]
assert ">Abrir</a>" not in tabla, "la columna «Abrir» redundante se va (propuesta 145)"
assert re.search(r'<time datetime="[^"]+" title="\d\d/\d\d/\d{4} \d\d:\d\d"', tabla), "tiempo relativo con fecha exacta (propuesta 131)"
# vista dividida: permisos iguales a la ficha de la nota
v = body(R[("GET", "/ui/nota/{nota_id:int}/vista")](req(direc, f"/ui/nota/{n_urg}/vista"), n_urg))
assert "Urgente.docx" in v and 'data-atajo="f"' in v and "Al Sr. Director" in v and "caja-vista-previa--hoja" in v
falla(lambda: R[("GET", "/ui/nota/{nota_id:int}/vista")](req(caro, "/x"), n_urg), 403)
falla(lambda: R[("GET", "/ui/nota/{nota_id:int}/vista")](req(direc, "/x"), 999999), 404)
# bandeja en cero
with db._conexion() as con:
    con.execute("UPDATE notas SET estado = 'FIRMADA' WHERE estado = 'PROCESADA'")
html = body(R[("GET", "/")](req(direc, "/")))
assert "Estás al día" in html and 'href="/?ver=todos"' in html
print("108, 109, 110, 131, 145 OK")

# ---------------------------------------------------------------- 143 (atajos) y preferencias
assert 'data-atajos", "si"' in body(R[("GET", "/inicio")](req(direc, "/inicio")))  # Dirección: activados por defecto
assert 'data-atajos", "no"' in body(R[("GET", "/inicio")](req(ana, "/inicio")))    # resto: apagados
r = run(R[("POST", "/ui/preferencias")](req(ana, "/ui/preferencias", form={"atajos": "si", "volver": "/perfil"})))
assert r.status_code == 303 and r.headers["location"] == "/perfil"
assert 'data-atajos", "si"' in body(R[("GET", "/inicio")](req(ana, "/inicio")))
falla(lambda: R[("POST", "/ui/preferencias")](req(ana, "/x", form={"tema": "fucsia"})), 400)
assert 'getAttribute("data-atajos") === "no"' in base and 'el.dataset.atajo === "f"' in base, "F enfoca y no firma (propuesta 143)"
print("143 OK")

# ---------------------------------------------------------------- 140 y 138: volver después del login; código de soporte
assert "inactividad=1&volver=" in base and 'getElementById("inact-seguir").focus()' in base
fuente = (a.Path(a.APP) / "main.py").read_text(encoding="utf-8")
assert "Ref. {ref}" in fuente and "rutas_ui.destino_interno(volver, destino)" in fuente
print("138, 140 OK")

# ---------------------------------------------------------------- 125 y 136: chips de filtros y quitar un filtro guardado con «Deshacer»
html = body(R[("GET", "/")](req(coo, "/"), False, "", "asc", "Urg", "", "", "", "URGENTE"))
assert "Filtrando por:" in html and "Archivo contiene: <strong>«Urg»</strong>" in html and "Prioridad: <strong>URGENTE</strong>" in html
chips = re.findall(r'class="chip-filtro__quitar" href="([^"]+)"', html)
assert len(chips) == 2 and "f_prioridad" not in chips[1] and "f_nombre=Urg" in chips[1], chips
assert "Quitar todos los filtros</a></p>" not in html, "el segundo enlace duplicado se fue"
db.guardar_pref_notas(coo["id"], {"filtros": [{"nombre": "Urgentes", "qs": "f_prioridad=URGENTE"}]})
html = body(R[("GET", "/")](req(coo, "/")))
assert 'action="/ui/filtros/quitar"' in html and "¿Quitar el filtro" not in html  # reversible: sin confirmación
ses = {}
r = run(R[("POST", "/ui/filtros/quitar")](req(coo, "/ui/filtros/quitar", ses=ses, form={"nombre": "Urgentes"})))
assert r.status_code == 303 and ses["ui_deshacer_filtro"] == {"nombre": "Urgentes", "qs": "f_prioridad=URGENTE"}
assert not (db.obtener_usuario(coo["id"])["pref_notas"] or "").count("Urgentes")
html = body(R[("GET", "/")](req(coo, "/", ses=ses)))
assert 'action="/filtros/guardar"' in html and 'value="f_prioridad=URGENTE"' in html and "Deshacer" in html
assert "ui_deshacer_filtro" not in ses, "el «Deshacer» se muestra una sola vez"
assert "uiTextoConfirmar" in base and '"Cancelar"' in base and "confirmarBoton" in base
print("125, 136 OK")

# ---------------------------------------------------------------- 114: aprobación por lote (Coordinación)
s1, s2 = a.nota("Lote1.docx", estado="SUBIDA"), a.nota("Lote2.docx", estado="SUBIDA")
otro = a.nota("OtroEquipo.docx", estado="SUBIDA", eq=a.E2, u=caro)
a.main._encolar_correccion = lambda *x, **k: None
html = body(R[("GET", "/")](req(coo, "/"), False, "", "asc", "", "", "", "", "", "", "aprobar"))
assert 'formaction="/ui/lote/aprobar"' in html and 'data-ui-todos' in html and 'data-estado="SUBIDA" data-nombre="Lote1.docx"' in html
falla(lambda: R[("POST", "/ui/lote/aprobar")](req(ana, "/x", form={"nota_ids": [str(s1)]})), 403)
falla(lambda: R[("POST", "/ui/lote/aprobar")](req(coo, "/x", form={"nota_ids": []})), 400)
ses = {}
r = run(R[("POST", "/ui/lote/aprobar")](req(coo, "/x", ses=ses, form={"nota_ids": [str(s1), str(s2), str(otro), str(n_urg)]})))
assert r.status_code == 303 and "Aprobaste y mandaste a corrección 2" in ses["flash"] and "OtroEquipo.docx" in ses["flash"], ses
assert db.obtener_nota(s1)["estado"] == "PROCESANDO" and db.obtener_nota(otro)["estado"] == "SUBIDA"
print("114 OK")

# ---------------------------------------------------------------- 113: bloques de Mesa como lista de tareas
mesa = a.U("mesa", "Mara Mesa", "MESA_JEFE")
f1, f2 = a.nota("Nota_b1.docx", estado="FIRMADA"), a.nota("Informe_b2.docx", estado="FIRMADA")
db.crear_bloque("Bloque listo", a.E1, ana["id"], [f1, f2])
html = body(R[("GET", "/")](req(mesa, "/")))
assert "lista-tareas" in html and "2 de 2 firmadas" in html and "Falta cargar 1" in html
assert re.search(r'class="boton boton--primario" href="/bloque/\d+/despacho">Cargar números', html), "el próximo paso es el primario"
assert "Sí, despachar el bloque «Bloque listo»" in html
print("113 OK")

# ---------------------------------------------------------------- 137: lenguaje claro
plantillas = "".join(p.read_text(encoding="utf-8") for p in (a.Path(a.APP) / "templates").glob("*.html"))
assert '"s/h"' not in plantillas and '<abbr title="Legajo Personal Único">LPU</abbr>' in plantillas
assert "Glosario" in body(R[("GET", "/manual")](req(ana, "/manual")))
assert 'name="atajos" value="si"' in body(R[("GET", "/perfil")](req(ana, "/perfil")))
print("137, 143 perfil OK")

# ---------------------------------------------------------------- 107: firma en serie con progreso, «saltear» y resumen
with db._conexion() as con:
    con.execute("UPDATE notas SET estado = 'FIRMADA' WHERE estado = 'PROCESADA'")
p1, p2 = a.nota("Serie1.docx", prioridad="URGENTE"), a.nota("Serie2.docx")
ses = {}
r = R[("GET", "/firma-en-serie")](req(direc, "/firma-en-serie", ses=ses), 0)
primero = int(r.headers["location"].split("/nota/")[1].split("?")[0])
html = body(R[("GET", "/nota/{nota_id}")](req(direc, f"/nota/{primero}", ses=ses, q={"serie": "1"}), primero))
assert "Documento 1 de 2 · Restan 1" in html and "1 URGENTE<" in html and "Saltear por ahora" in html and "Volver a corregir</button>" in html
R[("GET", "/firma-en-serie")](req(direc, "/firma-en-serie", ses=ses), primero)
assert ses["serie_salteadas"] == [primero]
html = body(R[("GET", "/nota/{nota_id}")](req(direc, "/x", ses=ses, q={"serie": "1"}), p2 if primero == p1 else p1))
assert "1 salteada<" in html
ses2 = {"serie_firmadas": 3, "serie_salteadas": [p1]}
destino = a.main._despues_de_firmar(req(direc, "/x", ses=ses2), 999, [999], "X")
assert ses2["serie_resumen"] == {"firmadas": 4, "salteadas": 1}
with db._conexion() as con:
    con.execute("UPDATE notas SET estado = 'FIRMADA' WHERE estado = 'PROCESADA'")
html = body(R[("GET", "/firma-terminada")](req(direc, "/firma-terminada", ses=ses2)))
assert "Terminaste la serie:</strong> firmaste 4 · salteaste 1" in html and "serie_resumen" not in ses2
print("107 OK")

# ---------------------------------------------------------------- 102: Inicio como bandeja única por verbo
a.nota("ParaFirmar.docx"); a.nota("ParaAprobar.docx", estado="SUBIDA")
html = body(R[("GET", "/inicio")](req(direc, "/inicio")))
assert "Qué tenés que hacer hoy" in html and 'href="/firma-en-serie">Firmar en serie' in html and "Despachar (" not in html
assert '<details class="inicio-resumen">' in html and "Hoy necesita tu atención" in html
html = body(R[("GET", "/inicio")](req(coo, "/inicio")))
assert "Aprobar <span" in html and "Firmar <span" not in html
html = body(R[("GET", "/inicio")](req(mesa, "/inicio")))
assert "Despachar <span" in html
print("102 OK")

# ---------------------------------------------------------------- 115, 118, 119, 120: formularios
estatico = lambda n: (a.Path(a.APP) / "static" / n).read_text(encoding="utf-8")  # noqa: E731
html = body(R[("GET", "/inicio")](req(ana, "/inicio")))
for js in ("ui_formularios.js", "ui_combobox.js", "ui_bandeja.js", "ui_base.js"):
    assert f"/static/{js}?v=" in html, js
f = estatico("ui_formularios.js")
assert 'addEventListener("invalid"' in f and "resumen-errores" in f and "aria-describedby" in f and "Próximo lunes" in f
c = estatico("ui_combobox.js")
assert 'setAttribute("role", "combobox")' in c and "aria-activedescendant" in c and 'role", "listbox"' in c and "normalize(\"NFD\")".replace("\\", "") in c.replace("'", '"')
macros = a.main.templates.env.get_template("_ui_macros.html").module
r_ = str(macros.error_resumen([("dni", "Ingresá el DNI sin puntos"), "Elegí la unidad de destino"]))
assert 'href="#dni"' in r_ and "Elegí la unidad de destino" in r_ and 'role="alert"' in r_ and str(macros.error_resumen([])).strip() == ""
assert 'id="dni-error"' in str(macros.error_campo("dni", "Ingresá el DNI sin puntos"))
assert 'href="/"' in str(macros.volver("//afuera.com")) and 'href="/traslados"' in str(macros.volver("/traslados"))
viaje = body(R[("GET", "/traslados/nuevo")](req(ana, "/traslados/nuevo")))
assert 'data-paso="4"' in viaje and "Revisá y confirmá" in viaje, "solicitud de viaje en pasos con revisión (118, ya existente)"
print("115, 118, 119, 120 OK")

# ---------------------------------------------------------------- 122: borradores en el servidor
import json as _json
r = run(R[("POST", "/ui/borrador")](req(ana, "/ui/borrador", form={"clave": "/traslados/nuevo#form-viaje", "datos": _json.dumps({"motivo": ["Monitoreo"]})})))
assert _json.loads(r.body)["ok"] and _json.loads(r.body)["hora"]
r = R[("GET", "/ui/borrador")](req(ana, "/ui/borrador"), "/traslados/nuevo#form-viaje")
assert _json.loads(r.body)["borrador"]["datos"] == {"motivo": ["Monitoreo"]}
assert _json.loads(R[("GET", "/ui/borrador")](req(coo, "/x"), "/traslados/nuevo#form-viaje").body)["borrador"] is None  # cada uno lo suyo
falla(lambda: R[("POST", "/ui/borrador")](req(ana, "/x", form={"clave": "https://afuera", "datos": "{}"})), 400)
falla(lambda: R[("POST", "/ui/borrador")](req(ana, "/x", form={"clave": "/x", "datos": "no es json"})), 400)
run(R[("POST", "/ui/borrador/descartar")](req(ana, "/x", form={"clave": "/traslados/nuevo#form-viaje"})))
assert _json.loads(R[("GET", "/ui/borrador")](req(ana, "/x"), "/traslados/nuevo#form-viaje").body)["borrador"] is None
assert "uiGuardarBorradores" in estatico("ui_formularios.js") and "sendBeacon" in estatico("ui_formularios.js")
print("122 OK")

# ---------------------------------------------------------------- 148, 149: chofer
import traslados as tr
from datetime import date as _date
chofer = a.U("chof", "Pedro Chofer", "CHOFER")
otro_chofer = a.U("chof2", "Otro Chofer", "CHOFER")
tr.sincronizar_choferes()
cpf1 = next(u_ for u_ in db.listar_unidades() if u_["nombre"].startswith("Complejo Penitenciario Federal I de"))
with db._conexion() as con:
    cid = con.execute("SELECT id FROM choferes WHERE usuario_id = ?", (chofer["id"],)).fetchone()[0]
    con.execute("UPDATE unidades SET direccion = 'Ruta 205 km 45,5', telefono_guardia = '011 4295-1234' WHERE id = ?", (cpf1["id"],))
vid = tr.crear_viaje(dict(fecha=_date.today().isoformat(), hora_salida="08:30", hora_regreso="13:00", unidad_id=cpf1["id"], motivo="Monitoreo"),
                     [{"usuario_id": ana["id"], "nombre": ana["nombre"], "auto_propio": 0}], ana)
with db._conexion() as con:
    con.execute("UPDATE viajes SET chofer_id = ?, estado = 'CONFIRMADO' WHERE id = ?", (cid, vid))
html = body(R[("GET", "/traslados/hoy")](req(chofer, "/traslados/hoy"), "", ""))
assert "salida-chofer__hora" in html and 'href="tel:01142951234"' in html and "Ruta 205 km 45,5" in html
assert f'action="/ui/viaje/{vid}/sali"' in html and "chips-dia" in html and 'class="barra-inferior"' in html and "Día anterior" not in html
assert 'class="barra-inferior"' not in body(R[("GET", "/inicio")](req(direc, "/inicio")))
falla(lambda: R[("POST", "/ui/viaje/{viaje_id:int}/{momento}")](req(otro_chofer, "/x", form={}), vid, "sali"), 403)
falla(lambda: R[("POST", "/ui/viaje/{viaje_id:int}/{momento}")](req(chofer, "/x", form={}), vid, "otra"), 404)
ses = {}
r = run(R[("POST", "/ui/viaje/{viaje_id:int}/{momento}")](req(chofer, "/x", ses=ses, form={}), vid, "sali"))
assert r.status_code == 303 and "Salida registrada" in ses["flash"] and tr.obtener_viaje(vid)["hora_salida_real"]
run(R[("POST", "/ui/viaje/{viaje_id:int}/{momento}")](req(chofer, "/x", ses=ses, form={}), vid, "volvi"))
v = tr.obtener_viaje(vid)
assert v["hora_regreso_real"] and v["real_cargado_por"] == "Pedro Chofer"
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM traslados_auditoria WHERE entidad_id = ? AND accion = 'Horario real'", (vid,)).fetchone()[0] >= 2
print("148, 149 OK")

# ---------------------------------------------------------------- 134, 135: paleta de comandos y búsqueda global
with db._conexion() as con:
    con.execute("UPDATE usuarios SET acceso_notas = 0 WHERE id = ?", (chofer["id"],))
pal = lambda u, q: body(R[("GET", "/ui/paleta")](req(u, "/ui/paleta"), q))  # noqa: E731
h = pal(direc, "")
assert "Firmar en serie" in h and "Solicitar viaje" in h and 'role="option"' in h
h = pal(chofer, "")
assert "Mis salidas" in h and "Firmar en serie" not in h and "Notas a la firma" not in h and "Solicitar viaje" not in h
h = pal(direc, "urgente")
assert "Urgente.docx" in h and ">Notas <span" in h
h = pal(caro, "urgente")
assert "Urgente.docx" not in h, "nunca muestra notas que la persona no puede ver"
assert "No encontramos «urgente»" in h and "/expedientes" not in h  # asesora: sin enlace a Expedientes
h = pal(ana, "ezeiza")
assert ">Unidades <span" in h and "Complejo Penitenciario Federal I de Ezeiza" in h and "Monitoreo" not in h
assert ">Viajes <span" in h  # el viaje de Ana a CPF I
h = pal(chofer, "ezeiza")
assert ">Viajes <span" in h
h = pal(direc, "GONZALEZ zzz")
assert "No encontramos" in h
assert 'data-ui-paleta' in body(R[("GET", "/traslados/hoy")](req(chofer, "/traslados/hoy"), "", "")), "la búsqueda global también para quien solo usa Autos"
print("134, 135 OK")

# ---------------------------------------------------------------- 103, 104, 106, 112, 164: marca, título, bandas y pie
html = body(R[("GET", "/traslados/hoy")](req(chofer, "/traslados/hoy"), "", ""))
assert "DGPDH · Autos PPN" in html and "encabezado--autos" in html
html = body(R[("GET", "/inicio")](req(direc, "/inicio")))
assert "DGPDH · Notas a la firma" in html and re.search(r"<title>\(\d+\) ", html), "título con contador de avisos (104)"
assert 'class="banda-fase"' in html and "/ui/sugerencias?desde=" in html and 'class="pie-institucional"' in html and 'href="/accesibilidad"' in html
ses = {}
r = run(R[("POST", "/ui/sugerencias")](req(ana, "/ui/sugerencias", ses=ses, form={"texto": "No encuentro dónde subir", "desde": "/traslados"})))
assert r.status_code == 303 and r.headers["location"] == "/traslados" and "Gracias" in ses["flash"]
falla(lambda: R[("POST", "/ui/sugerencias")](req(ana, "/x", form={"texto": " ", "desde": "/"})), 400)
assert "No encuentro dónde subir" not in body(R[("GET", "/ui/sugerencias")](req(ana, "/ui/sugerencias"), "/traslados", ""))
h = body(R[("GET", "/ui/sugerencias")](req(direc, "/ui/sugerencias"), "", ""))
assert "No encuentro dónde subir" in h and "Marcar atendida" in h
sid = int(re.search(r"/ui/sugerencias/(\d+)/atendida", h).group(1))
falla(lambda: R[("POST", "/ui/sugerencias/{sid:int}/atendida")](req(ana, "/x", form={}), sid), 403)
R[("POST", "/ui/sugerencias/{sid:int}/atendida")](req(direc, "/x", ses={}, form={}), sid)
assert "Atendida por Director" in body(R[("GET", "/ui/sugerencias")](req(direc, "/x"), "", "todas"))
assert "WCAG 2.2" in body(R[("GET", "/accesibilidad")](req(ana, "/accesibilidad")))
sub = a.U("sub", "Sara Subdirectora", "SUBDIRECTOR")
hoy_ = _date.today().isoformat()
db.crear_ausencia(direc["id"], hoy_, (_date.fromordinal(_date.today().toordinal() + 3)).isoformat(), sub["id"])
assert "firma Sara Subdirectora" in body(R[("GET", "/")](req(coo, "/")))
assert "firma Sara Subdirectora" not in body(R[("GET", "/")](req(ana, "/"))), "solo en las bandejas de Dirección y Coordinación"
print("103, 104, 106, 112, 164 OK")

# ---------------------------------------------------------------- 127, 128, 157, 159
html = body(R[("GET", "/")](req(direc, "/"), False, "nombre", "desc", "", "", "", "", "", "", "", 1, "", "todos"))
assert 'scope="col" aria-sort="descending"' in html and "ordenados por nombre de archivo de mayor a menor" in html
css = estatico("estilos_ui.css")
assert "position: sticky; top: 0" in css and "htmx-request::after" in css and "prefers-reduced-motion" in css
assert "window.cambiarPrioridad = function" in estatico("ui_bandeja.js") and "aria-busy" in estatico("ui_base.js")
ses = {"flash": "Prioridad: URGENTE.", "deshacer": {"texto": "x", "previas": {}}}
j = _json.loads(R[("GET", "/ui/aviso-pendiente")](req(ana, "/x", ses=ses)).body)
assert j["flash"] == "Prioridad: URGENTE." and j["deshacer"] and "flash" not in ses and "deshacer" in ses
print("127, 128, 157, 159 OK")

# ---------------------------------------------------------------- 139, 105: avisos agrupados, en la misma pestaña, y «Volver»
db.notificar_usuarios([ana["id"]], "Tenés 2 documentos para firmar", "/firma-en-serie") if hasattr(db, "notificar_usuarios") else None
db.notificar_usuarios([ana["id"]], "Se despachó tu nota «Informe»", "/nota/1")
h = body(R[("GET", "/notificaciones")](req(ana, "/notificaciones", q={"desde": "/traslados"}), "todos"))
assert "Te toca actuar" in h and "Para que sepas" in h and h.index("Te toca actuar") < h.index("Para que sepas")
assert '>Hoy</h3>' in h and 'target="_blank"' not in h and ">Firmar<span" in h and 'href="/traslados" class="volver"' in h
assert 'href="/inicio" class="volver"' in body(R[("GET", "/notificaciones")](req(ana, "/notificaciones", q={"desde": "//afuera"}), "todos"))
print("105, 139 OK")

# ---------------------------------------------------------------- 153, 156: resumen al entrar y novedades
h = body(R[("GET", "/inicio")](req(ana, "/inicio")))
assert "Desde ayer" in h and "que te piden actuar" in h and "Qué cambió en la versión Beta 01" in h
assert "Los formularios largos guardan un borrador" in h and "«Mis salidas»" not in h, "novedades filtradas por rol"
run(R[("POST", "/ui/preferencias")](req(ana, "/x", ses={}, form={"visto": "resumen_dia", "valor": _date.today().isoformat()})))
run(R[("POST", "/ui/preferencias")](req(ana, "/x", ses={}, form={"visto": "novedades", "valor": "Beta 01"})))
h = body(R[("GET", "/inicio")](req(ana, "/inicio")))
assert "Desde ayer" not in h and "Qué cambió en la versión" not in h, "no vuelven hasta mañana o la próxima versión"
assert "Beta 01 (14/11/2026)" in body(R[("GET", "/ui/novedades")](req(ana, "/ui/novedades")))
print("153, 156 OK")

# ---------------------------------------------------------------- 152, 155: insignias y etapas explicadas
db.marcar_todas_leidas(coo["id"])
db.notificar_usuarios([coo["id"]], "Se despachó una nota del equipo", "/nota/1")
h = body(R[("GET", "/inicio")](req(coo, "/inicio")))
assert "insignia--punto" in h and "<title>(" not in h, "lo informativo no suma a la insignia roja"
db.notificar_usuarios([coo["id"]], "Tenés 1 documento para aprobar", "/?f_tarea=aprobar")
h = body(R[("GET", "/inicio")](req(coo, "/inicio")))
assert '<span class="insignia">1</span></a>' in h and "<title>(1) " in h and "1 te piden actuar" in h
h = body(R[("GET", "/")](req(coo, "/"), False, "", "asc", "", "", "", "", "", "", "", 1, "", "todos"))
assert 'class="etapa-ayuda"' in h and "Etapa 1 de 4.</strong>" in h and "Después pasa a «Nota a la firma de DGPDH»" in h
print("152, 155 OK")

# ---------------------------------------------------------------- 124: PIN
b_ = estatico("ui_base.js")
assert "pin-mostrar" in b_ and "¿Te olvidaste el PIN?" in b_ and 'dlg.addEventListener("close"' in b_
import pin as _pin
assert "Te quedan" in (a.Path(a.APP) / "pin.py").read_text(encoding="utf-8")  # el servidor ya informa los intentos que quedan
print("124 OK")

# ---------------------------------------------------------------- 154: primeros pasos
nuevo = a.U("nuevo", "Nina Nueva", "ASESOR", a.E1)
h = body(R[("GET", "/inicio")](req(nuevo, "/inicio")))
assert "Primeros pasos" in h and "Subí tu primer documento</a>" in h and "Creá tu PIN" not in h
a.nota("Primera.docx", u=nuevo)
h = body(R[("GET", "/inicio")](req(nuevo, "/inicio")))
assert "Subí tu primer documento<span class=\"sr-only\"> (hecho)" in h
assert "Creá tu PIN de firma</a>" in body(R[("GET", "/inicio")](req(mesa, "/inicio")))
run(R[("POST", "/ui/preferencias")](req(nuevo, "/x", ses={}, form={"visto": "primeros_pasos", "valor": "cerrado"})))
assert "Primeros pasos" not in body(R[("GET", "/inicio")](req(nuevo, "/inicio")))
print("154 OK")

# ---------------------------------------------------------------- 111: motivos frecuentes al devolver
for txt in ("• Falta el sello\n• La fecha está mal", "• Falta el sello"):
    nn = a.nota("Devuelta.docx", estado="SUBIDA")
    db.marcar_rechazada(nn, txt)
otro_eq = a.nota("DevueltaOtroEquipo.docx", estado="SUBIDA", eq=a.E2, u=caro)
db.marcar_rechazada(otro_eq, "• Motivo de otro equipo\n• Motivo de otro equipo")
db.marcar_rechazada(a.nota("Dev2.docx", estado="SUBIDA", eq=a.E2, u=caro), "• Motivo de otro equipo")
m = _json.loads(R[("GET", "/ui/motivos-frecuentes")](req(coo, "/x")).body)["motivos"]
assert m[0] == "Falta el sello" and "Motivo de otro equipo" not in m and "Falta el LPU" in m, m
assert "Motivo de otro equipo" in _json.loads(R[("GET", "/ui/motivos-frecuentes")](req(direc, "/x")).body)["motivos"]
falla(lambda: R[("GET", "/ui/motivos-frecuentes")](req(ana, "/x")), 403)
print("111 OK")

# ---------------------------------------------------------------- 117: validación en línea
import detenidos as _det
did, _err, _ = _det.guardar({"nombre_completo": "Juan Carlos Gómez", "dni": "30.123.456", "lpu": "123456"}, direc)
assert did, _err
v = lambda u, c, x, e="": body(R[("GET", "/ui/validar/{campo}")](req(u, "/x"), c, x, e))  # noqa: E731
if True:
    assert "Ya existe: Juan Carlos Gómez" in v(ana, "dni", "30.123.456") and f'/detenidos/{did}' in v(ana, "dni", "30123456")
    assert "No hay otra ficha" in v(ana, "dni", "30123456", str(did)), "al editar no se compara con la misma ficha"
assert v(ana, "dni", "12") == ""
falla(lambda: R[("GET", "/ui/validar/{campo}")](req(chofer, "/x"), "dni", "30123456", ""), 403)
falla(lambda: R[("GET", "/ui/validar/{campo}")](req(ana, "/x"), "nombre", "x", ""), 404)
h = body(R[("GET", "/ui/paleta")](req(ana, "/ui/paleta"), "30123456"))
assert ">Personas detenidas <span" in h and "Juan Carlos Gómez" in h and f'href="/detenidos/{did}"' in h
assert "Juan Carlos Gómez" not in body(R[("GET", "/ui/paleta")](req(chofer, "/ui/paleta"), "30123456")), "el chofer no ve la base"
print("117 OK (y la paleta encuentra personas detenidas)")

# ---------------------------------------------------------------- rendimiento: lo del área no suma consultas por fila (152, 153, 102)
import sqlite3 as _sq
_cuenta = {"n": 0}
_orig = _sq.connect
def _con(*x, **k):
    c = _orig(*x, **k); c.set_trace_callback(lambda q: _cuenta.__setitem__("n", _cuenta["n"] + 1) if not q.startswith("PRAGMA") else None); return c
_sq.connect = _con
_env = a.main.templates.env
def _consultas_ui():
    gastado = {"n": 0}
    originales = {k: f for k, f in _env.globals.items() if k.startswith("ui_") and callable(f)}
    for k, f in originales.items():
        def envoltura(*x, _f=f, **kw):
            n0 = _cuenta["n"]; r = _f(*x, **kw); gastado["n"] += _cuenta["n"] - n0; return r
        _env.globals[k] = envoltura
    try:
        body(R[("GET", "/inicio")](req(direc, "/inicio")))
    finally:
        _env.globals.update(originales)
    return gastado["n"]
antes = _consultas_ui()
for i in range(25):
    a.nota(f"Masiva{i}.docx", estado=["PROCESADA", "SUBIDA", "EN_REVISION"][i % 3])
    db.notificar_usuarios([direc["id"]], f"Tenés el documento {i} para firmar", "/")
despues = _consultas_ui()
_sq.connect = _orig
assert despues == antes and antes <= 9, (antes, despues)
h = body(R[("GET", "/guia-estilo")](req(direc, "/guia-estilo")))
assert "Colores con significado" in h and "--color-atencion" in h and "resumen-errores" in h and "lista-tareas" in h
css = estatico("estilos_ui.css")
assert ".alerta-reapertura { background: var(--color-atencion-fondo)" in css and "#7A5518" not in css
print("100, 163 OK")
h = body(R[("GET", "/detenidos")](req(direc, "/detenidos")))
cab = h[h.index('class="cabecera-accion"'):h.index("</div>", h.index('class="menu-acciones"'))]
assert cab.count("boton--primario") == 1 and 'class="menu-acciones"' in cab and "Exportar Excel o CSV" in cab  # integración: la exportación lleva perfil y motivo (propuesta 46)
print("161 OK")
j_ = estatico("ui_bandeja.js")
assert "corrección y firma" in j_ and "htmx:xhr:progress" in j_ and "DataTransfer" in j_
print("121 OK")
assert "data-autofiltrar" in estatico("ui_formularios.js") and "form.form-filtros" in estatico("ui_formularios.js")
print("126 OK")
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
_t0 = _dt.now(_tz.utc) - _td(hours=1)
with db._conexion() as con:
    con.execute("DELETE FROM historial_estados WHERE estado = 'PROCESANDO'")  # base de prueba: solo estas dos correcciones
    for k, mins in ((0, 4), (1, 6)):
        con.execute("INSERT INTO historial_estados (nota_id, estado, fecha) VALUES (?, 'PROCESANDO', ?)", (n_urg, (_t0 + _td(minutes=10 * k)).isoformat()))
        con.execute("INSERT INTO historial_estados (nota_id, estado, fecha) VALUES (?, 'PROCESADA', ?)", (n_urg, (_t0 + _td(minutes=10 * k + mins)).isoformat()))
assert _env.globals["ui_minutos_por_nota"](refrescar=True) == 5.0
assert _env.globals["ui_espera_cola"](3) == "unos 15 min (promedio: 5 min por nota)" and _env.globals["ui_espera_cola"](0) == ""
print("158 OK")
assert "viaje__pastilla" in estatico("ui_base.js") and "@media print" in estatico("estilos_ui.css")
print("147 OK")
assert "contador-datos" in estatico("ui_formularios.js") and "form.form-buscar-detenidos" in estatico("ui_formularios.js")
print("133 OK")
assert "Exportar esta vista" in estatico("ui_bandeja.js") and "\\ufeff" in estatico("ui_bandeja.js")
print("132 OK")
assert "role=\"combobox\"]:not(.combobox__campo)" in estatico("ui_combobox.js"), "no duplica el buscador de destino de la solicitud de viaje"
assert "/traslados/nuevo?repetir=" in (a.Path(a.APP) / "templates" / "viaje_detalle.html").read_text(encoding="utf-8"), "123 ya existente"
h = body(R[("GET", "/nota/{nota_id}")](req(direc, f"/nota/{n_urg}"), n_urg))
assert '<nav class="migas" aria-label="Estás en">' in h and '<a href="/">Notas a la firma</a>' in h and '<span aria-current="page">Urgente.docx</span>' in h
print("105 migas OK")
assert "atencion-mas" in estatico("ui_bandeja.js") and ".chip-plazo--vencida" in estatico("ui_bandeja.js")
print("130 OK")
assert 'role", "tablist"' in estatico("ui_agenda.js") and "/static/ui_agenda.js" in body(R[("GET", "/inicio")](req(ana, "/inicio")))
print("151 OK")
h = body(R[("GET", "/inicio")](req(direc, "/inicio")))
lat = h[h.index('class="lateral__nav"'):h.index("</nav>", h.index('class="lateral__nav"'))]
assert lat.index(">Mi trabajo<") < lat.index('href="/despacho"') < lat.index(">Consultar<") < lat.index('href="/expedientes"') < lat.index(">Gestión<") < lat.index('href="/datos"')
print("101 OK")
# 150: app instalable, optativa
falla(lambda: R[("GET", "/sw.js")](req(chofer, "/sw.js")), 404)
assert 'rel="manifest"' not in body(R[("GET", "/traslados/hoy")](req(chofer, "/traslados/hoy"), "", ""))
_env.globals["ui_institucional"]["pwa"] = True
r = R[("GET", "/sw.js")](req(chofer, "/sw.js"))
assert r.headers["service-worker-allowed"] == "/" and b"mis-salidas" in r.body
h = body(R[("GET", "/traslados/hoy")](req(chofer, "/traslados/hoy"), "", ""))
assert 'rel="manifest"' in h and "/static/ui_pwa.js" in h
assert 'rel="manifest"' not in body(R[("GET", "/inicio")](req(direc, "/inicio"))), "solo para choferes"
_env.globals["ui_institucional"]["pwa"] = False
sw = estatico("ui_sw.js")
assert 'url.pathname === "/logout"' in sw and "Pendiente de enviar" in sw and "http" not in sw.replace("https://", "").split("\n", 3)[3]
# una marca hecha sin señal llega después con su hora (del mismo día y no futura)
with db._conexion() as con:
    con.execute("UPDATE viajes SET hora_salida_real = NULL, hora_regreso_real = NULL WHERE id = ?", (vid,))
run(R[("POST", "/ui/viaje/{viaje_id:int}/{momento}")](req(chofer, "/x", ses={}, form={"hora": "00:01", "fecha": _date.today().isoformat()}), vid, "sali"))
assert tr.obtener_viaje(vid)["hora_salida_real"] == "00:01"
run(R[("POST", "/ui/viaje/{viaje_id:int}/{momento}")](req(chofer, "/x", ses={}, form={"hora": "23:59", "fecha": "2000-01-01"}), vid, "sali"))
assert tr.obtener_viaje(vid)["hora_salida_real"] != "23:59", "de otro día no se acepta: vale la hora del servidor"
print("150 OK")
import rutas_ui as _rui
class _Resp:
    def __init__(self): self.headers = {}
cab = _rui.poner_csp(_Resp()).headers["Content-Security-Policy"]
assert "default-src 'self'" in cab and "http" not in cab and "frame-ancestors 'none'" in cab
print("98 CSP OK")
print(f"rendimiento OK: {antes} consultas del área en /inicio, sin importar cuántas notas o avisos haya")
print("TODO OK")
