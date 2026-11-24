"""Reforma 14/11/2026: clasificación obligatoria al mandar a la firma y protocolo interno de informes (INF-DGPDH / INF-SGPDH)."""
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
import main, auth, nucleo, soberania, respaldo, automatico, control_cambios as cc
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import types, asyncio, json, shutil, socket
from datetime import date, datetime, timedelta, timezone
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1, E2 = db.crear_equipo("Equipo Salud"), db.crear_equipo("Equipo CPF I")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, beto, coo, caro = U("ana", "Ana Pérez", "ASESOR", E1), U("beto", "Beto Ruiz", "ASESOR", E1), U("coo", "Cora Coord", "COORDINADOR", E1), U("caro", "Caro CPF", "ASESOR", E2)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
def req(u, p="/", ses=None):
    async def _f(): return {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session=ses if ses is not None else {},
                                 cookies={}, headers={}, query_params={}, form=_f, client=None)
def docx(nombre, parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; r = tmp / nombre; d.save(r); return r
def procesada(nombre, prioridad="NORMAL", eq=E1, u=None):
    r = docx(nombre, ["Al Sr. Director", "Cuerpo."])
    nid = db.crear_nota(nombre, eq, str(r), usuario_id=(u or ana)["id"], prioridad=prioridad)
    for e in ("PROCESANDO", "PROCESADA"): db.actualizar_estado(nid, e)
    with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(r), nid))
    return nid

import clasificacion_notas as cla
from PIL import Image
R = main.app.rutas
sub = U("sub", "Sara Subdirectora", "SUBDIRECTOR")
png = tmp / "firma.png"; Image.new("RGB", (60, 20), "white").save(png)
nucleo.FIRMAS_POR_ROL = {"DIRECTOR": png, "SUBDIRECTOR": png}
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def reqf(u, form, p="/x"):
    r = req(u, p)
    async def _f(): return Form(form)
    r.form = _f; return r
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def texto_docx(ruta): return "\n".join(p.text for p in Document(str(ruta)).paragraphs)
def falla(fn, codigo):
    try: run(fn())
    except main.HTTPException as e: assert e.status_code == codigo, (e.status_code, e.detail); return
    raise AssertionError(f"se esperaba {codigo}")

# ---------------------------------------------------------------- enviar sin clasificar: no se envía
r1 = docx("Informe visita CPF I.docx", ["Al Sr. Juez Federal", "Informe colectivo sobre la visita. DNI 30.111.222 y DNI 31.222.333."])
n1 = db.crear_nota("Informe visita CPF I.docx", E1, str(r1), usuario_id=ana["id"], borrador=True)
rr = req(ana, f"/nota/{n1}"); r = main.enviar_nota_a_coordinacion(rr, n1)
assert r.headers["location"] == f"/nota/{n1}#clasificacion" and "confirmá la clasificación" in rr.session["flash"] and db.obtener_nota(n1)["estado"] == "BORRADOR"
# la ficha muestra la propuesta (informe, colectivo, interno, destinatario y expediente leídos del texto)
html = main.ver_nota(req(ana, f"/nota/{n1}"), n1).body
assert 'id="clasificacion"' in html and "propuesta del sistema" in html and '<option value="INFORME" selected>' in html
p = cla.proponer(db.obtener_nota(n1), texto_docx(r1))
assert p["tipo"] == "INFORME" and p["alcance"] == "COLECTIVO" and p["confidencialidad"] == "INTERNO" and "Juez" in p["destinatario"], p
# validación y permisos
rr = reqf(ana, {"tipo": "INFORME", "confidencialidad": "RESERVADO", "destinatario": "Sr. Juez"}); run(main.clasificar_nota(rr, n1))
assert "individual o colectivo" in rr.session["flash"] and not db.obtener_nota(n1)["clasificado_en"]
falla(lambda: main.clasificar_nota(reqf(caro, {"tipo": "NOTA"}), n1), 403)
rr = reqf(ana, {"tipo": "INFORME", "alcance": "COLECTIVO", "confidencialidad": "RESERVADO", "destinatario": "Sr. Juez Federal", "expediente": "ET 6116/26"})
run(main.clasificar_nota(rr, n1)); assert rr.session["flash"] == "Clasificación confirmada."
n = db.obtener_nota(n1)
assert (n["tipo_documental"], n["clasif_subtipo"], n["clasif_alcance"], n["confidencialidad"], n["clasif_expediente"], n["clasificado_por"]) == \
       ("INFORME", "GENERAL", "COLECTIVO", "RESERVADO", "ET 6116/26", "Ana Pérez")
from dgpdh.seguridad import auditoria as au
with db._conexion() as con:
    assert au.historial(con, "nota", n1)[-1]["accion"] == "Clasificación"
assert main._enviar_notas(ana, [n1], None) == 1 and db.obtener_nota(n1)["estado"] == "SUBIDA"
# envío en lote: los sin clasificar quedan y se avisa
r2 = docx("Nota sin clasificar.docx", ["Al Sr. Director", "Texto"])
n2 = db.crear_nota("Nota sin clasificar.docx", E1, str(r2), usuario_id=ana["id"], borrador=True)
rr = reqf(ana, {"nota_ids": [str(n2)]}); run(main.enviar_varias_a_coordinacion(rr))
assert "falta clasificarlos" in rr.session["flash"] and "Nota sin clasificar.docx" in rr.session["flash"]
print("clasificación al enviar OK")

# ---------------------------------------------------------------- firma: número de protocolo según quién firma
for e in ("PROCESANDO", "PROCESADA"): db.actualizar_estado(n1, e)
with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(r1), n1))
anio = date.today().year
main._firmar(direc, n1)
n = db.obtener_nota(n1)
assert n["estado"] == "FIRMADA" and n["protocolo_numero"] == f"INF-DGPDH-{anio}-0001", n["protocolo_numero"]
t = texto_docx(n["ruta_firmada"]).lstrip("\n"); assert t.startswith(f"RESERVADO\nINFORME N° INF-DGPDH-{anio}-0001"), repr(t[:200])
assert any("Protocolizada: Informe N° INF-DGPDH" in (h_["comentario"] or "") for h_ in n["historial"])
falla(lambda: main.clasificar_nota(reqf(ana, {"tipo": "NOTA", "confidencialidad": "PUBLICO", "destinatario": "x"}), n1), 409)
# la Subdirección numera en su propia serie
i2 = procesada("Informe individual.docx")
cla.guardar(i2, {"tipo": "INFORME", "alcance": "INDIVIDUAL", "confidencialidad": "PUBLICO", "destinatario": "Archivo"}, ana)
main._firmar(sub, i2)
n = db.obtener_nota(i2); assert n["protocolo_numero"] == f"INF-SGPDH-{anio}-0001"
assert texto_docx(n["ruta_firmada"]).lstrip("\n").startswith(f"INFORME N° INF-SGPDH-{anio}-0001"), "público: sin leyenda"
# una nota no lleva protocolo (sigue con el número de Mesa de Entradas)
nn = procesada("Nota a la Unidad.docx")
cla.guardar(nn, {"tipo": "NOTA", "confidencialidad": "PUBLICO", "destinatario": "Director de la Unidad"}, ana)
main._firmar(direc, nn); n = db.obtener_nota(nn)
assert n["estado"] == "FIRMADA" and not n["protocolo_numero"] and "INFORME N°" not in texto_docx(n["ruta_firmada"])
# un informe que llegó sin clasificar (anterior a la reforma): quien firma confirma la propuesta y queda registrado
viejo = procesada("Informe anterior a la reforma.docx")
main._firmar(direc, viejo); n = db.obtener_nota(viejo)
assert n["clasificado_por"] == "Director General" and n["protocolo_numero"] == f"INF-DGPDH-{anio}-0002"
assert any("confirmada al firmar" in (h_["comentario"] or "") for h_ in n["historial"])
print("protocolo al firmar OK")

# ---------------------------------------------------------------- volver a firmar conserva el número; anular no lo reutiliza
db.actualizar_estado(i2, "PROCESADA", desde=("FIRMADA",))
main._firmar(sub, i2); assert db.obtener_nota(i2)["protocolo_numero"] == f"INF-SGPDH-{anio}-0001"
falla(lambda: main.anular_protocolo(reqf(ana, {"numero": f"INF-DGPDH-{anio}-0002", "motivo": "x"})), 403)
rr = reqf(direc, {"numero": f"INF-DGPDH-{anio}-0002", "motivo": "se firmó el documento equivocado"}); run(main.anular_protocolo(rr))
assert "anulado" in rr.session["flash"] and not db.obtener_nota(viejo)["protocolo_numero"]
rr = reqf(direc, {"numero": f"INF-DGPDH-{anio}-0002", "motivo": "otra vez"}); run(main.anular_protocolo(rr)); assert "ya está anulado" in rr.session["flash"]
db.actualizar_estado(viejo, "PROCESADA", desde=("FIRMADA",))
main._firmar(direc, viejo); assert db.obtener_nota(viejo)["protocolo_numero"] == f"INF-DGPDH-{anio}-0003", "el anulado no se reutiliza"
html = main.ver_nota(req(direc, f"/nota/{viejo}"), viejo).body
assert f"INF-DGPDH-{anio}-0002" in html and "se firmó el documento equivocado" in html and f"INF-DGPDH-{anio}-0003" in html
print("anulación OK")

# ---------------------------------------------------------------- búsqueda, listados y carpeta del caso
html = main.buscar(req(ana, "/buscar"), f"INF-DGPDH-{anio}-0001").body
assert "Informe visita CPF I.docx" in html and "chip-protocolo" in html
html = main.buscar(req(caro, "/buscar"), f"INF-DGPDH-{anio}-0001").body
assert "Informe visita CPF I.docx" not in html, "otro equipo no lo ve por buscar el número"
print("búsqueda OK")
