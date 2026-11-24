"""Modificación 1: «Revisar correcciones» a dos columnas (documento + inspector), sin perder ninguna acción."""
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
import main, auth, nucleo, unidades_csv
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import types, asyncio, json, shutil, re
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1 = db.crear_equipo("Equipo Salud")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, coo = U("ana", "Ana Pérez", "ASESOR", E1), U("coo", "Cora Coord", "COORDINADOR", E1)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def req(u, p="/", form=None, q=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session={}, cookies={},
                                 headers={}, query_params=q or {}, form=_f, client=None)
d = Document(); [d.add_paragraph(p) for p in ["Al Sr. Director", "La visita del 12 de mayo de 2025 al pabellón 4."]]; r = tmp / "Nota.docx"; d.save(r)
nid = db.crear_nota("Nota.docx", E1, str(r), usuario_id=ana["id"])
for e in ("PROCESANDO",): db.actualizar_estado(nid, e)
prop = [{"id": 0, "ubicacion": "body:1", "original": "La visita del 12 de mayo de 2025 al pabellón 4.",
         "propuesto": "La visita del 12 de mayo de 2025 al pabellón IV.", "cambios": [{"fuente": "diccionario"}]}]
db.guardar_propuestas(nid, "NOTA", [], prop, [{"ubicacion": "body:0", "texto": "Al Sr. Director", "propuesta_id": None},
                                             {"ubicacion": "body:1", "texto": prop[0]["original"], "propuesta_id": 0}], desde=("PROCESANDO",))
import agentes
h_ = agentes._h("AVISO", "Fecha", "Revisá la fecha.", "La visita del 12 de mayo de 2025 al pabellón 4", db.obtener_nota(nid))
db.reemplazar_hallazgos(nid, None, [h_], grupo="prerrevision")
html = main.revisar_nota(req(direc, f"/nota/{nid}/revisar"), nid).body
# dos columnas: documento a la izquierda, inspector a la derecha (con la revisión automática y las acciones)
assert html.index('class="notas-revision__principal"') < html.index('id="form-revision"') < html.index('id="inspector-revision"')
insp = html[html.index('id="inspector-revision"'):]
for k in ('id="ver-original"', 'id="rv-deshacer"', 'id="rv-restaurar-todo"', 'id="rv-propuesta-todo"', "Más opciones", "Atajos:",
          "Ver el documento con formato", "Rechazar esta nota completa", "Revisión automática", 'id="inspector-plegar"', "Guardar y firmar"):
    assert k in insp, f"falta en el inspector: {k}"
cabecera = html[html.index('class="notas-revision__cabecera"'):html.index('id="notas-revision"')]
assert "Revisar correcciones" in cabecera and "1</strong> párrafo con cambios" in cabecera and "1</strong> aviso para revisar" in cabecera
assert "rv-restaurar-todo" not in cabecera and "ver-original" not in cabecera, "la cabecera queda mínima"
assert 'data-fragmentos="La visita del 12 de mayo de 2025 al pabellón 4"' in insp, "la tarjeta sabe a qué párrafo lleva"
assert '/static/notas_revision.js' in html and html.count('value="sin_cambios"') == 2 and "1 párrafo con cambios propuestos" in html
js = open(APP + "/static/notas_revision.js", encoding="utf-8").read()
assert "scrollIntoView" in js and "notas-resaltado" in js and "localStorage" in js
css = open(APP + "/static/estilos_notas.css", encoding="utf-8").read()
assert "380px" in css and "max-width: 1100px" in css
# propuesta 10: descarga con control de cambios de Word
assert f'/nota/{nid}/control-cambios.docx' in insp
import zipfile, io as _io
r_ = main.app.rutas[("GET", "/nota/{nota_id}/control-cambios.docx")](req(direc, "/"), nid)
xml = zipfile.ZipFile(_io.BytesIO(r_.body)).read("word/document.xml").decode()
assert "<w:del " in xml and "<w:ins " in xml and "IV" in xml
print("revisión a dos columnas OK")
