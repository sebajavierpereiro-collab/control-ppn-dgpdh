"""ABM de personas detenidas: alta, validación, edición con historial, baja, búsqueda, importación, exportación, API y pantallas."""
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
import main, auth
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import detenidos as det
import types, io, csv, asyncio, json, re
from datetime import date, timedelta
import openpyxl
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1 = db.crear_equipo("Equipo Salud")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, mesa, chofer = U("ana", "Ana Pérez", "ASESOR", E1), U("mesa", "Marta Mesa", "MESA_ASESOR"), U("chof", "Carlos Chofer", "CHOFER")
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])

hoy = date.today()
"""(arriba: el mismo armado de la app que test_detenidos)  Cola única de corrección, recuperación tras un reinicio y firma sin corrección."""
import time as _t
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def req(u, p="/", form=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"]) if u else None), url=URL(p), session={}, cookies={},
                                 headers={}, query_params={}, form=_f, client=None)
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
main.CARPETA_PROCESADOS = tmp / "procesados"; main.CARPETA_PROCESADOS.mkdir(exist_ok=True)
def nota(nombre, prioridad="NORMAL"):
    d = Document(); d.add_paragraph("Al Sr. Director"); d.add_paragraph("Texto de la nota."); r = tmp / f"{nombre}.docx"; d.save(r)
    nid = db.crear_nota(f"Nota {nombre}.docx", E1, str(r), usuario_id=ana["id"], prioridad=prioridad)
    db.actualizar_estado(nid, "PROCESANDO")
    return nid
# 1) recuperación: notas que quedaron «aplicando correcciones» (reinicio) vuelven a la cola, de a una y por prioridad
orden, simultaneas, en_curso = [], [], [0]
def falsa(nota_id, ruta, nombre):
    en_curso[0] += 1; simultaneas.append(en_curso[0]); orden.append(nota_id); _t.sleep(0.05)
    main.nucleo.copiar_sin_cambios(ruta, main.CARPETA_PROCESADOS / f"{nota_id}.docx")
    db.guardar_resultado_sin_revision(nota_id, tipo="NOTA", ruta_resultado=main.CARPETA_PROCESADOS / f"{nota_id}.docx")
    en_curso[0] -= 1
main._tarea_generar_propuestas = falsa
lote = [nota(f"lote{i}") for i in range(6)]
urgente = nota("urgente", "URGENTE")
saltear = nota("saltear")
main._pasar_sin_correccion(db.obtener_nota(saltear), direc)  # se pasa a la firma antes de que la cola llegue
assert db.obtener_nota(saltear)["estado"] == "PROCESADA"
n = main._recuperar_en_correccion()
assert n == 7, n
assert main._recuperar_en_correccion() == 0, "no se encolan dos veces"
for _ in range(200):
    if all(db.obtener_nota(x)["estado"] == "PROCESADA" for x in lote + [urgente]): break
    _t.sleep(0.05)
assert all(db.obtener_nota(x)["estado"] == "PROCESADA" for x in lote + [urgente]), [db.obtener_nota(x)["estado"] for x in lote]
assert max(simultaneas) == 1, "una nota por vez"
assert saltear not in orden, "la que se pasó a la firma no se corrige"
assert orden.index(urgente) <= 1, ("la urgente pasa adelante", orden)
# 2) la Dirección pasa una nota a la firma sin esperar a la IA (y en lote)
main._encolar_correccion = lambda *a, **k: True   # que la cola no la tome durante la prueba
x1, x2, x3 = nota("x1"), nota("x2"), nota("x3")
r = R_ = main.app.rutas
resp = r[("POST", "/nota/{nota_id}/sin-correccion")](req(direc, "/x"), x1)
assert resp.headers["location"] == f"/nota/{x1}?firmar=1" and db.obtener_nota(x1)["estado"] == "PROCESADA"
n1 = db.obtener_nota(x1)
assert any("sin corrección automática" in (h.get("comentario") or "") for h in n1["historial"]) and Path(n1["ruta_resultado"]).is_file()
import integridad; assert integridad.resultado_coincide(n1), "se puede firmar (la huella del documento coincide)"
try: r[("POST", "/nota/{nota_id}/sin-correccion")](req(mesa, "/x"), x2); raise SystemExit("Mesa no cambia el circuito")
except main.HTTPException as e: assert e.status_code == 403
rr = req(direc, "/", form={"nota_ids": [str(x2), str(x3)]})
run(r[("POST", "/notas/sin-correccion")](rr))
assert db.obtener_nota(x2)["estado"] == db.obtener_nota(x3)["estado"] == "PROCESADA" and "2 documento(s)" in rr.session["flash"]
# 3) pantallas: ficha con el botón y bandeja de la Dirección con la acción en lote
x4 = nota("x4")
html = main.templates.env.get_template("_estado_nota.html").render(request=types.SimpleNamespace(state=types.SimpleNamespace(usuario=direc)),
                                                                   nota=db.obtener_nota(x4), etapas=main.flujo.ETAPAS)
assert "Firmar sin esperar la corrección" in html and f"/nota/{x4}/sin-correccion" in html
html = r[("GET", "/")](req(direc, "/")).body
assert "En corrección automática (1)" in html and "Pasar todas a la firma sin corrección (1)" in html
print("cola única de corrección, recuperación tras reinicio y firma sin corrección OK")
