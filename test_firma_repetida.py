"""v41: tocar «Firmar» de nuevo (Atrás del navegador, doble toque) en una nota ya firmada no da error ni la firma otra vez."""
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
import types, asyncio, json, shutil, socket, io, re
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
R = main.app.rutas
from PIL import Image
png = tmp / "firma.png"; Image.new("RGB", (60, 20), "white").save(png); nucleo.FIRMAS_POR_ROL = {"DIRECTOR": png, "SUBDIRECTOR": png}
informe = procesada  # la firma confirma sola la clasificación propuesta
a, b = informe("a.docx"), informe("b.docx")
ses = {}
r = R[("POST", "/nota/{nota_id}/firmar")](req(direc, f"/nota/{a}/firmar", ses), a, None)
assert r.status_code == 200 and r.headers["HX-Redirect"] == f"/nota/{b}?serie=1", r.headers
assert db.obtener_nota(a)["estado"] == "FIRMADA"
firmado = db.obtener_nota(a)["ruta_firmada"]; antes = Path(firmado).stat().st_mtime_ns
eventos = len(db.obtener_nota(a).get("historial") or [])
r = R[("POST", "/nota/{nota_id}/firmar")](req(direc, f"/nota/{a}/firmar", ses), a, None)
assert r.status_code == 200 and r.headers["HX-Redirect"] == f"/nota/{b}?serie=1", "segunda vez: sigue con la próxima, sin error"
assert Path(firmado).stat().st_mtime_ns == antes, "no se vuelve a firmar"
# lo mismo por «Firmar y seguir»
r = R[("POST", "/nota/{nota_id}/firmar-y-seguir")](req(direc, f"/nota/{a}/firmar-y-seguir", ses), a, None)
assert r.status_code == 303 and r.headers["location"] == f"/nota/{b}?serie=1", r.headers
# una nota que no está lista sigue dando 409
c = db.crear_nota("c.docx", E1, str(docx("c.docx", ["x"])), usuario_id=ana["id"])
db.actualizar_estado(c, "PROCESANDO")
try:
    R[("POST", "/nota/{nota_id}/firmar")](req(direc, f"/nota/{c}/firmar", ses), c, None); raise SystemExit("se esperaba 409")
except main.HTTPException as e:
    assert e.status_code == 409
print("firma repetida OK")
