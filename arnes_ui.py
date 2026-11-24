"""Arnés común de las pruebas de interfaz (propuestas 98–164): reemplaza FastAPI por un registrador de rutas y Jinja2Templates
por un render directo, igual que tests/test_obstaculizaciones_web.py, para llamar a las rutas y mirar el HTML que devuelven.
No es un test (no empieza con test_): lo importan los scripts tests/test_ui_*.py."""
import sys, types
import starlette.responses as sr
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
        r = R(); r.body = self.env.get_template(nombre).render(**ctx); r.status_code = k.get("status_code", 200); r.headers = {}; return r


ft.Jinja2Templates = Jinja2Templates
sys.modules.update({"fastapi": fa, "fastapi.exceptions": fe, "fastapi.responses": fr, "fastapi.staticfiles": fs, "fastapi.templating": ft})
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db  # noqa: E402
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth  # noqa: E402,F401
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import asyncio  # noqa: E402
from docx import Document  # noqa: E402

h, s = auth.generar_hash_clave("Clave1234")
E1, E2 = db.crear_equipo("Equipo Salud"), db.crear_equipo("Equipo CPF I")


def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))


ana, coo, caro = U("ana", "Ana Pérez", "ASESOR", E1), U("coo", "Cora Coord", "COORDINADOR", E1), U("caro", "Caro CPF", "ASESOR", E2)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
R = main.app.rutas


class URL:
    def __init__(self, p, q=""): self.path = p; self.query = q; self.scheme = "http"


class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v not in (None, "") else [])


def req(u, p="/", ses=None, form=None, q=None, headers=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"]) if u else None), url=URL(p),
                                 session=ses if ses is not None else {}, cookies={}, headers=headers or {}, query_params=q or {},
                                 form=_f, client=None, method="POST" if form is not None else "GET")


def run(x): return asyncio.run(x) if asyncio.iscoroutine(x) else x
def body(r): return r.body.decode() if isinstance(r.body, bytes) else r.body


def docx(nombre, parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; r = tmp / nombre; d.save(r); return r


def nota(nombre, estado="PROCESADA", prioridad="NORMAL", eq=None, u=None):
    r = docx(nombre, ["Al Sr. Director", "Cuerpo."])
    nid = db.crear_nota(nombre, eq or E1, str(r), usuario_id=(u or ana)["id"], prioridad=prioridad)
    camino = {"SUBIDA": [], "PROCESANDO": ["PROCESANDO"], "EN_REVISION": ["PROCESANDO", "EN_REVISION"],
              "PROCESADA": ["PROCESANDO", "PROCESADA"], "FIRMADA": ["PROCESANDO", "PROCESADA", "FIRMADA"]}[estado]
    for e in camino:
        db.actualizar_estado(nid, e)
    with db._conexion() as con:
        con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(r), nid))
    return nid


def falla(fn, codigo):
    try:
        run(fn())
    except main.HTTPException as e:
        assert e.status_code == codigo, (e.status_code, e.detail); return
    raise AssertionError(f"se esperaba {codigo}")
