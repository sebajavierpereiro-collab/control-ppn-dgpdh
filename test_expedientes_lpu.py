"""Expedientes: búsqueda por LPU, DNI, expediente y nombre (combinables) y verificación del LPU de notas y proveídos."""
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
import main, auth, agentes, expedientes
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import openpyxl
carp = tmp / "exp"; carp.mkdir()
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Planilla"]); ws.append(["N° Expediente", "LPU", "Apellido y Nombre", "DNI", "Unidad"])
ws.append(["EP 1234/2025", "123.456", "PÉREZ, Juan Carlos", 30123456, "CPF I"])
ws.append(["EP 999/2024", "222.333", "GÓMEZ, Ana María", 28999000, "CPF IV"])
ws.append(["EP 555/2026", "777.888", "PÉREZ, Pedro", 31000111, "CPF II"])
wb.save(carp / "a.xlsx")
wb2 = openpyxl.Workbook(); w2 = wb2.active; w2.append(["Expediente", "Interno", "Documento"]); w2.append(["EP 42/2026", "LÓPEZ, Rosa", "33444555"]); wb2.save(carp / "b.xlsx")
expedientes.CONFIG["carpeta"] = str(carp)
B = expedientes.buscar_campos
assert [r["datos"]["DNI"] for r in B({"lpu": "123456"})[0]] == ["30123456"]
assert [r["datos"]["DNI"] for r in B({"dni": "30.123.456"})[0]] == ["30123456"]
assert len(B({"nombre": "perez"})[0]) == 2 and len(B({"nombre": "pérez", "lpu": "777888"})[0]) == 1, "se combinan"
assert B({"nombre": "perez", "dni": "28999000"})[0] == [], "combinados que no coinciden"
assert B({"expediente": "EP-1234-2025"})[0][0]["datos"]["LPU"] == "123.456"
r, av = B({"dni": "33444555"}); assert r[0]["datos"]["Interno"] == "LÓPEZ, Rosa"
r, av = B({"lpu": "42"}); assert any("b.xlsx" in a and "LPU" in a for a in av), av
assert B({"lpu": "123"})[0] == [], "parcial solo desde 4 caracteres"
assert B({}) == ([], [])
print("búsqueda por campos OK")

# ---- pantalla
eq = db.crear_equipo("Equipo A"); h_, s_ = auth.generar_hash_clave("Clave1234")
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
class URL: path = "/"; query = ""
def req(u): return types.SimpleNamespace(state=types.SimpleNamespace(usuario=u), url=URL(), session={}, cookies={}, headers={}, query_params={})
html = main.pagina_expedientes(req(direc)).body
for t in ("Ingrese LPU", "Ingrese DNI", "Ingrese número de expediente", "Ingrese nombre o apellido", 'name="lpu"', 'name="dni"', 'name="expediente"', 'name="nombre"'):
    assert t in html, t
form = html[html.index('class="busqueda-expedientes"'):html.index("</form>", html.index('class="busqueda-expedientes"'))]
assert form.count("Buscar</button>") == 1 and 'name="q"' not in form and form.count("<input") >= 4
html = main.pagina_expedientes(req(direc), lpu="123456", nombre="juan").body
assert "1 resultado(s)" in html and "PÉREZ, Juan Carlos" in html
assert "LPU: 123456 · Nombre y Apellido: juan" in db.listar_consultas_expedientes()[0]["consulta"]
html = main.pagina_expedientes(req(direc), q="EP 999/2024").body
assert "GÓMEZ, Ana María" in html and 'value="EP 999/2024"' in html, "enlace viejo «Ver expediente» sigue andando"
html = main.pagina_expedientes(req(direc), dni="1").body
assert "No hay expedientes que coincidan con DNI «1»" in html
print("pantalla OK")

# ---- verificación del LPU en notas y proveídos
nota = {"id": 1, "nombre_original": "Nota CPF I.docx", "tipo": "NOTA"}
prov = {"id": 2, "nombre_original": "Proveido.docx", "tipo": "PROVEIDO"}
ok = "Al Sr. Director del CPF I\nSe solicita atención médica para PÉREZ, Juan Carlos, LPU N° 123.456, DNI 30.123.456, Expte. EP 1234/2025."
assert agentes.verificar_lpu(nota, ok) == []
mal_dni = ok.replace("30.123.456", "30.999.999")
h = agentes.verificar_lpu(nota, mal_dni); assert h and h[0]["tipo"] == "DISCREPANCIA" and "PÉREZ, Juan Carlos" in h[0]["detalle"], h
mal_nombre = "VISTO… el pedido de RODRÍGUEZ, Luis, L.P.U. 222.333."
h = agentes.verificar_lpu(prov, mal_nombre); assert any("GÓMEZ, Ana María" in x["detalle"] and "no aparece" in x["detalle"] for x in h), h
h = agentes.verificar_lpu(nota, "Interno PÉREZ, Pedro, LPU 777.888, Expte. EP 1234/2025.")
assert any(x["dato"] == "LPU 777888 y expediente" for x in h)
h = agentes.verificar_lpu(nota, "LPU N° 999.000 de alguien")
assert h and "no figura en la base" in h[0]["detalle"]
assert agentes.verificar_lpu(nota, "sin lpu en el texto") == []
# integrado en la prerrevisión de notas y proveídos
assert any("LPU" in x["dato"] for x in agentes.prerrevision(prov, mal_nombre))
# sin base de expedientes no se verifica
expedientes.CONFIG["carpeta"] = ""
assert agentes.verificar_lpu(nota, mal_dni) == []
print("verificación de LPU OK")
print("ALL OK")
