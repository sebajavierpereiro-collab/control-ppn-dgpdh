"""Modificación 7: avisos de la IA sobre fichas de detenidos: «Ver datos completos» y «Dejar en revisión»."""
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
CPF1 = next(u for u in db.listar_unidades() if "-CPF I-" in u["nombre"])
hoy = date.today()
import revision_ia as ria, nucleo
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def req(u, p="/", form=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"]) if u else None), url=URL(p), session={}, cookies={},
                                 headers={}, query_params={}, form=_f, client=None)
R = main.app.rutas
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def alta(**k):
    i, err, _ = det.guardar(k, mesa); assert i and not err, (k, err); return i
nucleo.ia_disponible = lambda p: False
a1 = alta(nombre_completo="María Pérez", dni="28999111", fecha_nacimiento="02/01/1980", unidad_id=str(CPF1["id"]), modulo="IV", pabellon="B")
c1 = alta(nombre_completo="Maria Peres", lpu="777111", fecha_nacimiento="02/01/1980")
det.guardar(dict(det.obtener(c1), pabellon="C"), mesa, c1)                       # deja un cambio en el historial
ria.revisar("PEDIDO", usuario=mesa)
p = next(x for x in ria.pendientes() if {x["det_a"], x["det_b"]} == {a1, c1})
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), tipo="ia").body
assert "Ver datos completos" in html and "LPU (legajo)" in html and "777111" in html and "Pabellón" in html and CPF1["nombre"] in html
assert "Últimos cambios de la ficha" in html and "pabellon" in html and "Dejar en revisión" in html
# dejar en revisión: sigue pendiente, con estado propio, auditado y con filtro
r = run(R[("POST", "/detenidos/revision")](req(mesa, "/detenidos/revision", {"clave": f"ia:{p['id']}", "tipo": "ia", "decision": "en_revision",
                                                                            "nota": "Se consulta a la unidad"})))
assert r.status_code == 303
q = ria.obtener(p["id"])
assert q["estado"] == "EN_REVISION" and q["en_revision_por"] == mesa["nombre"] and q["en_revision_nota"] == "Se consulta a la unidad"
assert any(x["id"] == p["id"] for x in ria.pendientes()), "no se cierra"
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion = 'Dejar en revisión' AND entidad_id = ?", (str(p["id"]),)).fetchone()[0] == 1
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), tipo="ia", ia="en_revision").body
assert "Maria Peres" in html and "En revisión</span>" in html and "Se consulta a la unidad" in html
assert html.count('value="en_revision" type="submit"') == 0, "ya está en revisión: no se ofrece de nuevo"
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), tipo="ia", ia="misma").body
assert "Maria Peres" not in html
assert ria.revisar("PEDIDO", usuario=mesa)["propuestas"] == 0, "no se vuelve a proponer mientras está en revisión"
# después se decide normalmente
run(R[("POST", "/detenidos/revision")](req(mesa, "/detenidos/revision", {"clave": f"ia:{p['id']}", "tipo": "ia", "decision": "distintas"})))
assert ria.obtener(p["id"])["estado"] == "RECHAZADA" and not any(x["id"] == p["id"] for x in ria.pendientes())
try: det.decidir_alerta(f"ia:{p['id']}", "en_revision", mesa); raise SystemExit("ya decidida")
except ValueError: pass
# permisos: solo quien mantiene la base (que además pasa detenidos.puede_ver)
try: R[("GET", "/detenidos/revision")](req(ana, "/detenidos/revision"), tipo="ia"); raise SystemExit("sin permiso no entra")
except main.HTTPException as e: assert e.status_code == 403
try: run(R[("POST", "/detenidos/revision")](req(chofer, "/detenidos/revision", {"clave": f"ia:{p['id']}", "tipo": "ia", "decision": "en_revision"}))); raise SystemExit("chofer")
except main.HTTPException as e: assert e.status_code == 403
print("avisos de la IA: datos completos y «Dejar en revisión» OK")

# ---------------------------------------------------------------- propuesta 31: grupos de la misma persona
g1 = alta(nombre_completo="Walter Vázquez", dni="30111222", fecha_nacimiento="05/05/1985")
g2 = alta(nombre_completo="Walter Vazquez", dni="30111223", fecha_nacimiento="05/05/1985")
g3 = alta(nombre_completo="Walter Basquez", dni="30111224", fecha_nacimiento="05/05/1985")
ria.revisar("PEDIDO", usuario=mesa)
html = R[("GET", "/detenidos/revision/grupos")](req(mesa, "/detenidos/revision/grupos")).body
assert "Grupo de 3 fichas" in html and "Unificar el grupo" in html and "Sacar del grupo" in html
r_ = run(R[("POST", "/detenidos/revision/grupos")](req(mesa, "/", {"ficha": [str(g1), str(g2), str(g3)], "accion": f"sacar:{g3}"})))
assert not [p for p in ria.pendientes() if g3 in (p["det_a"], p["det_b"]) and {p["det_a"], p["det_b"]} <= {g1, g2, g3}]
assert "Grupo de 3 fichas" not in R[("GET", "/detenidos/revision/grupos")](req(mesa, "/detenidos/revision/grupos")).body
# ahora uno de cuatro, unificado de una vez
g4 = alta(nombre_completo="Walter Vásques", dni="30111225", fecha_nacimiento="05/05/1985")
ria.revisar("PEDIDO", usuario=mesa)
grupos_ = ria.grupos_pendientes()
assert grupos_ and {g1, g2, g4} <= {d["id"] for d in grupos_[0]["fichas"]}
ids_ = [d["id"] for d in grupos_[0]["fichas"]]
run(R[("POST", "/detenidos/revision/grupos")](req(mesa, "/", {"ficha": [str(i) for i in ids_], "accion": "unificar", "mantener": str(g1)})))
assert all(det.obtener(i)["motivo_baja"] == f"Unificada con #{g1}" for i in ids_ if i != g1) and det.obtener(g1)["activo"] == 1
assert not [p for p in ria.pendientes() if {p["det_a"], p["det_b"]} <= set(ids_)]
try: R[("GET", "/detenidos/revision/grupos")](req(ana, "/detenidos/revision/grupos")); raise SystemExit("sin permiso")
except main.HTTPException as e: assert e.status_code == 403
print("grupos de la misma persona OK")
