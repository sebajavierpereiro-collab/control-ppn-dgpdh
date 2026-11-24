"""Ola 6: avisos, aprobación por jerarquía, firma en un paso, plazo de respuesta explícito, leyenda, búsquedas guardadas,
tipos documentales y trazabilidad de la IA."""
import sys, types
import starlette.responses as sr, starlette.templating as st, starlette.staticfiles as ss, starlette.exceptions as se
fa = types.ModuleType("fastapi")
class FastAPI:
    def __init__(self, *a, **k): self.rutas = {}; self.state = types.SimpleNamespace()
    def _reg(self, m):
        def deco(path, **kw):
            def w(fn): self.rutas[(m, path)] = fn; return fn
            return w
        return deco
    def get(self, p, **k): return self._reg("GET")(p)
    def post(self, p, **k): return self._reg("POST")(p)
    def middleware(self, *a, **k): return lambda f: f
    def add_middleware(self, *a, **k): pass
    def exception_handler(self, *a, **k): return lambda f: f
    def mount(self, *a, **k): pass
    def on_event(self, *a, **k): return lambda f: f
def _param(default=None, *a, **k): return default
class UploadFile: pass
class BackgroundTasks:
    def add_task(self, *a, **k): pass
class HTTPException(Exception):
    def __init__(self, status_code=500, detail=""): self.status_code, self.detail = status_code, detail
fa.FastAPI, fa.File, fa.Form, fa.UploadFile, fa.BackgroundTasks, fa.Request, fa.HTTPException = FastAPI, _param, _param, UploadFile, BackgroundTasks, object, HTTPException
fe = types.ModuleType("fastapi.exceptions"); fe.RequestValidationError = type("RVE", (Exception,), {})
fr = types.ModuleType("fastapi.responses")
for n in ("FileResponse", "HTMLResponse", "JSONResponse", "RedirectResponse", "Response", "StreamingResponse"): setattr(fr, n, getattr(sr, n))
fs = types.ModuleType("fastapi.staticfiles"); fs.StaticFiles = lambda *a, **k: None
ft = types.ModuleType("fastapi.templating")
class Jinja2Templates:
    def __init__(self, directory): 
        import jinja2
        self.env = jinja2.Environment(loader=jinja2.FileSystemLoader(directory), autoescape=True)
    def TemplateResponse(self, nombre, ctx, *a, **k):
        class R: pass
        r = R(); r.body = self.env.get_template(nombre).render(**ctx); r.status_code = k.get("status_code", 200); return r
ft.Jinja2Templates = Jinja2Templates
sys.modules.update({"fastapi": fa, "fastapi.exceptions": fe, "fastapi.responses": fr, "fastapi.staticfiles": fs, "fastapi.templating": ft})
import tempfile
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"

db.RUTA_DB = tmp / "t.db"
import main, auth, plazos, tipos_documentales as td, trazabilidad_ia as tz, nucleo
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import types, asyncio, json, re, shutil
from datetime import date, datetime, timedelta, timezone
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1, E2 = db.crear_equipo("Equipo Salud"), db.crear_equipo("Equipo CPF I")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, coo, coo2 = U("ana", "Ana Pérez", "ASESOR", E1), U("coo", "Cora Coordinadora", "COORDINADOR", E1), U("coo2", "Otro Coord", "COORDINADOR", E2)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def req(u, p="/", form=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session={}, cookies={},
                                 headers={}, query_params={}, form=_f, client=None)
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
class BG:
    def __init__(self): self.tareas = []
    def add_task(self, f, *a, **k): self.tareas.append((f, a))
def docx(nombre, parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; r = tmp / nombre; d.save(r); return r

# ---------------------------------------------------------------- 112) avisos
db.notificar_usuarios([ana["id"]], "Aviso uno", "/")
db.notificar_usuarios([ana["id"]], "Aviso dos", "/")
html = main.ver_notificaciones(req(ana, "/notificaciones")).body
assert 'pestana pestana--activa" aria-current="page">Nuevos' in html, "abre en Nuevos si hay sin leer"
assert html.index(">Nuevos") < html.index(">Leídos") < html.index(">Todos")
n1 = db.listar_notificaciones(ana["id"])[0]
main.abrir_notificacion(req(ana), n1["id"]); assert db.contar_notificaciones(ana["id"]) == 1
html = main.ver_notificaciones(req(ana), ver="leidos").body
assert "Marcar como no leído" in html
main.marcar_aviso_no_leido(req(ana), n1["id"]); assert db.contar_notificaciones(ana["id"]) == 2
try:
    main.marcar_aviso_no_leido(req(coo), n1["id"]); raise SystemExit("ajeno")
except main.HTTPException as e:
    assert e.status_code == 404
db.marcar_todas_leidas(ana["id"])
assert "pestana--activa\" aria-current=\"page\">Todos" in main.ver_notificaciones(req(ana)).body, "sin nuevos abre en Todos"
print("112 OK")

# ---------------------------------------------------------------- 113) jerarquía
def nueva(u, borrador):
    return db.crear_nota("Nota prueba.docx", u["equipo_id"] or E1, str(docx(f"n{datetime.now().timestamp()}.docx", ["Al Sr. Director", "Texto."])),
                         usuario_id=u["id"], borrador=borrador)
na = nueva(ana, True)
# reforma 14/11/2026: sin clasificar no se envía; clasificada, sigue el circuito de siempre
import clasificacion_notas
assert main._enviar_notas(ana, [na], None) == 0 and db.obtener_nota(na)["estado"] == "BORRADOR"
_CLA = {"tipo": "NOTA", "confidencialidad": "PUBLICO", "destinatario": "Sr. Director de la Unidad"}
clasificacion_notas.guardar(na, _CLA, ana)
encoladas = []  # la corrección ya no va en BackgroundTasks: entra a la cola única de corrección
_encolar_real = main._encolar_correccion
main._encolar_correccion = lambda nid, *a, **k: encoladas.append(nid) or True
bg = BG(); assert main._enviar_notas(ana, [na], bg) == 1
assert db.obtener_nota(na)["estado"] == "SUBIDA" and not bg.tareas, "lo que envía el asesor espera a Coordinación"
# la coordinadora sube: queda sin enviar (puede corregir, agrupar, priorizar) y al enviar va directo a corrección
assert "in (\"ASESOR\", \"COORDINADOR\")" in open(APP + "/main.py").read()
nb = nueva(coo, True); clasificacion_notas.guardar(nb, _CLA, coo); avisos_antes = db.contar_notificaciones(coo["id"])
bg = BG(); assert main._enviar_notas(coo, [nb], bg) == 1
n = db.obtener_nota(nb); assert n["estado"] == "PROCESANDO" and nb in encoladas and not any(h_["estado"] == "SUBIDA" for h_ in n["historial"]), n["historial"]
assert db.contar_notificaciones(coo["id"]) == avisos_antes, "no se avisa a sí misma"
nc = nueva(coo, False)   # algo ya enviado (SUBIDA) en lo que interviene la coordinadora
bg = BG(); assert main._coordinacion_implicita(coo, nc, bg)
n = db.obtener_nota(nc); assert n["estado"] == "PROCESANDO" and nc in encoladas
main._encolar_correccion = _encolar_real
assert any("considerada cumplida por intervención de Cora Coordinadora (Coordinador)" in (h_.get("comentario") or "") for h_ in n["historial"]), n["historial"]
nd = db.crear_nota("Nota dir.docx", E1, str(docx("nd.docx", ["Texto"])), usuario_id=direc["id"])
assert main._coordinacion_implicita(direc, nd, BG()) and db.obtener_nota(nd)["estado"] == "PROCESANDO", "la Dirección tampoco espera"
assert not main._coordinacion_implicita(coo2, na, BG()) and db.obtener_nota(na)["estado"] == "SUBIDA", "otra coordinación no cumple la etapa"
assert not main._coordinacion_implicita(ana, na, BG()), "el asesor no"
assert main._coordinacion_implicita(coo, na, BG()), "la coordinadora del equipo, sí, al intervenir"
print("113 OK")

# ---------------------------------------------------------------- 120) firma en un paso
def para_firmar(nombre):
    r = docx(nombre, ["Al Sr. Director", "Cuerpo de la nota."])
    nid = db.crear_nota(nombre, E1, str(r), usuario_id=ana["id"])
    for e in ("PROCESANDO", "PROCESADA"): db.actualizar_estado(nid, e)
    with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(r), nid))
    return nid
nucleo.insertar_firma_en_archivo = lambda o, d, rol_firmante=None, **k: shutil.copy(o, d)
f1 = para_firmar("Nota firma 1.docx"); main._firmar(direc, f1)
n = db.obtener_nota(f1); assert n["estado"] == "FIRMADA" and n["fecha_nota"] == main._hoy_local()
assert not any("otra fecha" in (h_.get("comentario") or "") for h_ in n["historial"])
f2 = para_firmar("Nota firma 2.docx"); otra = (date.fromisoformat(main._hoy_local()) + timedelta(days=3)).isoformat()
main._firmar(direc, f2, otra)
n = db.obtener_nota(f2); assert n["fecha_nota"] == otra
assert any("con otra fecha" in (h_.get("comentario") or "") and "fecha del servidor" in (h_.get("comentario") or "") for h_ in n["historial"]), "queda registrado"
f3 = para_firmar("Nota firma 3.docx")
nota3 = db.obtener_nota(f3); nota3["etapa"] = main.flujo.etapa_para_estado(nota3["estado"])
html = main.templates.env.get_template("_estado_nota.html").render(request=req(direc), nota=nota3, etapas=main.flujo.ETAPAS, usuario=direc)
assert "Firmar" in html and "Otra fecha" in html and 'value=""' in html and "con la fecha indicada" not in html and "¿Firmar este documento?" not in html
print("120 OK")

# ---------------------------------------------------------------- 109) plazo de respuesta explícito
base = {"estado": "DESPACHADA", "plazo_respuesta": "{}"}
H = date(2026, 10, 10)
assert plazos.estado_respuesta({"estado": "DESPACHADA"}, hoy=H)["clave"] == "sin_plazo"
assert plazos.estado_respuesta(dict(base, respuesta_vence="2026-10-15"), hoy=H)["texto"] == "En término"
assert plazos.estado_respuesta(dict(base, respuesta_vence="2026-10-05"), hoy=H)["clave"] == "vencido"
assert plazos.estado_respuesta(dict(base, respuesta_vence="2026-10-05", respuesta_recibida_en="2026-10-04"), hoy=H)["clave"] == "en_termino"
assert plazos.estado_respuesta(dict(base, respuesta_vence="2026-10-05", respuesta_recibida_en="2026-10-08"), hoy=H)["clave"] == "fuera_de_termino"
assert plazos.estado_respuesta({"estado": "FIRMADA"}, hoy=H) is None
# por respuesta puntual, con su fecha
assert plazos.estado_respuesta(dict(base, respuesta_vence="2026-10-05"), "2026-10-06", hoy=H)["texto"] == "Fuera de término"
# extremo a extremo: despachada con plazo, respuesta tarde → vencimiento calculado al cargarla
r = docx("resp.docx", ["Texto"])
nr = db.crear_nota("Nota con plazo.docx", E1, str(r), usuario_id=ana["id"])
for e in ("PROCESANDO", "PROCESADA", "FIRMADA", "DESPACHADA"): db.actualizar_estado(nr, e)
db.fijar_plazo_respuesta(nr, {"cantidad": 5, "unidad": "dias", "habiles": False, "texto": "cinco (5) días"})
hace = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(timespec="seconds")
with db._conexion() as con: con.execute("UPDATE historial_estados SET fecha = ? WHERE nota_id = ? AND estado = 'DESPACHADA'", (hace, nr))
db.registrar_respuesta(nr, date.today().isoformat(), "CORREO", "Dirección del CPF I", "Responden.")
n = db.obtener_nota(nr); assert n["respuesta_vence"] and plazos.estado_respuesta(n)["clave"] == "fuera_de_termino", n["respuesta_vence"]
res = db.resumen_respuestas()
assert res["fuera_de_termino"] == 1 and "sin_plazo" in res and res["vencido"] == 0, res
print("109 OK")

# ---------------------------------------------------------------- 114) leyenda y título limpio en «Revisar»
src = open(APP + "/templates/revisar.html").read()
assert "resumen_cambios" in src and src.index("<script") > src.index("{% block contenido %}"), "sin script dentro del título"

# ---------------------------------------------------------------- 102) búsquedas guardadas de expedientes
mesa = U("mesa", "Marta Mesa", "MESA_ASESOR")
r = run(main.guardar_busqueda_expedientes(req(mesa, form={"titulo_busqueda": "CPF I", "lpu": "123", "nombre": ""})))
assert r.status_code == 303 and "lpu=123" in r.headers["location"]
b = main._busquedas_expedientes(db.obtener_usuario(mesa["id"])); assert b == [{"nombre": "CPF I", "filtros": {"lpu": "123"}, "qs": "lpu=123"}], b
run(main.guardar_busqueda_expedientes(req(mesa, form={"titulo_busqueda": "Sin campos"})))
assert len(main._busquedas_expedientes(db.obtener_usuario(mesa["id"]))) == 1, "sin filtros no se guarda"
html = main.pagina_expedientes(req(mesa), lpu="123").body
assert "Mis búsquedas" in html and "Guardar esta búsqueda" in html
main.borrar_busqueda_expedientes(req(mesa), "CPF I"); assert main._busquedas_expedientes(db.obtener_usuario(mesa["id"])) == []
print("102 OK")

# ---------------------------------------------------------------- 131) tipos documentales
P = lambda t, n="Nota.docx", tc=None: td.proponer(t, n, tc)[0]
assert P("Al Sr. Director del Complejo Penitenciario Federal I. Tengo el agrado de dirigirme a Ud. a fin de solicitarle tenga a bien informar sobre la atención médica") == "PEDIDO_INFORME"
assert P("Al Sr. Juez a cargo del Juzgado Federal N.º 2. Vengo a poner en conocimiento de V.S. la situación") == "PRESENTACION_JUDICIAL"
assert P("Al Sr. Director de la Unidad 6. En respuesta a su nota del 3/10 informamos") == "RESPUESTA_ORGANISMO"
assert P("Se intima al Servicio Penitenciario a que en 48 horas") == "REQUERIMIENTO"
assert P("OFICIO N.º 12. Al Sr. Director") == "OFICIO" and P("x", "Oficio CPF.docx") == "OFICIO"
assert P("A la Coordinación de Salud de la Procuración Penitenciaria. Por la presente") == "COMUNICACION_INTERNA"
assert P("Al Sr. Director del Complejo Penitenciario Federal II. Ponemos en su conocimiento la visita realizada") == "COMUNICACION_UNIDAD"
assert P("Texto", "Proveído 1.docx") == "PROVEIDO" and P("x", tc="NOTA A PPL") == "NOTA_PPL" and P("Hola") == "NOTA"
d = td.de_nota({"id": 1, "nombre_original": "Nota.docx", "tipo": "NOTA"}, "Al Sr. Director del Complejo Penitenciario Federal I. Se solicita informe sobre")
assert d["clave"] == "PEDIDO_INFORME" and not d["confirmado"] and d["motivo"]
r = main.confirmar_tipo_documental(req(ana), na, "OFICIO", "PEDIDO_INFORME")
n = db.obtener_nota(na); assert n["tipo_documental"] == "OFICIO" and n["tipo_documental_origen"] == "CAMBIADO" and n["tipo_documental_por"] == "Ana Pérez"
assert td.de_nota(n)["confirmado"] and td.de_nota(n)["etiqueta"] == "Oficio"
try:
    main.confirmar_tipo_documental(req(coo2), na, "NOTA", ""); raise SystemExit("otro equipo no")
except main.HTTPException as e:
    assert e.status_code == 403
html = main.ver_nota(req(ana, f"/nota/{na}"), na).body
assert ('<option value="OFICIO" selected>' in html or "<dd>Oficio</dd>" in html) and 'id="clasificacion"' in html and "Lo que propuso la IA" in html
print("131 OK")

# ---------------------------------------------------------------- 137) trazabilidad de la IA
class Resp:
    status_code = 200
    def json(self): return {"response": '{"tema": "Salud"}', "done_reason": "stop"}
_post, _disp = nucleo.requests.post, nucleo.ia_disponible
nucleo.requests.post = lambda *a, **k: Resp(); nucleo.ia_disponible = lambda p: True
with tz.para(na, "CLASIFICAR"):
    assert nucleo.consultar_ollama("clasificacion", "PROMPT con datos personales DNI 30123456") == '{"tema": "Salud"}'
class Mal:
    status_code = 500
    def json(self): return {}
nucleo.requests.post = lambda *a, **k: Mal()
with tz.para(na, "RESUMEN"):
    assert nucleo.consultar_ollama("redaccion", "otro") is None
nucleo.requests.post, nucleo.ia_disponible = _post, _disp
regs = tz.de_nota(na)
assert [r_["tarea"] for r_ in regs] == ["RESUMEN", "CLASIFICAR"] and regs[1]["ok"] == 1 and regs[0]["ok"] == 0
assert regs[1]["pedido_chars"] == len("PROMPT con datos personales DNI 30123456") and "30123456" not in json.dumps(regs), "el pedido no se guarda"
c = tz.registrar_revision(na, [{"fuentes": ["diccionario", "ia_ollama"], "decision": "aceptar"}, {"fuentes": ["ia_ollama"], "decision": "modificar"},
                               {"fuentes": ["languagetool"], "decision": "rechazar"}], coo)
assert c == {"aceptar": 1, "modificar": 1, "rechazar": 0}
assert tz.de_nota(na)[0]["tarea"] == "REVISION_HUMANA" and "editados por la persona" in tz.de_nota(na)[0]["salida"]
html = main.ver_nota(req(ana, f"/nota/{na}"), na).body
assert "Clasificación" in html and "No se usó" in html and "Decisión de la persona" in html
assert any(x["tarea"] == "CLASIFICAR" for x in tz.resumen())
print("137 OK")
print("OK ola 6")
