"""Borrar notas de prueba: se van las notas y lo que depende de ellas; se conserva lo aprendido; respaldo antes."""
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
import asyncio, tempfile
from datetime import date
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth, circuito, respaldo, aprendizaje
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
main.CARPETA_DATOS = tmp
respaldo.CONFIG.update(carpeta=tmp / "respaldos", carpeta_datos=tmp, copia_externa=None)
class URL: path = "/"; query = ""
class Ses(dict): pass
def req(u, form=None):
    async def _f(): return form or {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(), session=Ses(), cookies={},
                                 headers={}, form=_f, client=types.SimpleNamespace(host="1"), query_params={})
eq = db.crear_equipo("Equipo A")
h_, s_ = auth.generar_hash_clave("Clave1234")
ana = db.obtener_usuario(db.crear_usuario("ana", h_, s_, "Ana", "ASESOR", eq))
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
anio = date.today().year
docs = tmp / "notas"; docs.mkdir()
ids = []
for i in range(3):
    f = docs / f"n{i}.docx"; f.write_bytes(b"PK test")
    nid = db.crear_nota(f"Nota {i}.docx", eq, str(f), usuario_id=ana["id"]); ids.append(nid)
    db.indexar_texto(nid, f"Texto de la nota {i} sobre el CPF I")
    db.guardar_entidades(nid, [("PERSONA", "Juan Pérez", "juan perez")])
    db.registrar_decision_director(nid, "p1", "orig", "texto propuesto viejo", "texto final nuevo", "modificado", "")
fuera = tmp.parent / f"fuera_{tmp.name}.docx"; fuera.write_bytes(b"x")
with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(fuera), ids[0]))
db.actualizar_estado(ids[0], "PROCESANDO")
db.guardar_feedback_hallazgo({"clave": "k1", "dato": "dato", "nota_id": ids[0]}, "no era contradicción", "Dir")
db.agregar_termino("Sr. Director", "Señor Director", "")
db.recordar_plazo_destinatario("Director del CPF I", {"cantidad": 5})
db.proponer_sugerencia(ids[0], "PLAZO", "a", {"x": 1})
db.proponer_sugerencia(ids[1], "PLAZO", "b", {"x": 2})
s_dec = db.sugerencias_de(ids[1])[0]; db.resolver_sugerencia(s_dec["id"], "ACEPTADA", "Dir")
db.fijar_contador(anio, "NOTA", 140, "Mesa", inicial=True)
assert circuito.proponer("NOTA", anio) == 141
db.notificar_usuarios([ana["id"]], "Nota lista", f"/nota/{ids[0]}")
db.notificar_usuarios([ana["id"]], "Otro aviso", "/traslados")
antes_correcciones = len(db.obtener_modificaciones())

# vista previa: no toca nada
html = main.admin_panel(req(direc), "sistema", "limpieza").body
assert "Borrar notas de prueba" in html and "BORRAR NOTAS" in html and "Correcciones del Director" in html
p = db.previa_limpieza_notas()
assert next(x for x in p["borrar"] if x["tabla"] == "notas")["n"] == 3 and next(x for x in p["conservar"] if x["tabla"] == "correcciones_director")["n"] == 3
assert len(db.listar_notas()) == 3
# sin confirmar: no borra
r = asyncio.run(main.admin_limpieza_notas(req(direc, {"confirmacion": "borrar"})))
assert len(db.listar_notas()) == 3
# solo Dirección
try:
    asyncio.run(main.admin_limpieza_notas(req(ana, {"confirmacion": "BORRAR NOTAS", "entiendo": "1"}))); raise AssertionError("debía dar 403")
except sys.modules["fastapi"].HTTPException as e:
    assert e.status_code == 403
# confirmado
r = asyncio.run(main.admin_limpieza_notas(req(direc, {"confirmacion": "BORRAR NOTAS", "entiendo": "1"})))
assert db.listar_notas() == [] and list((tmp / "respaldos" / "db").glob("dgpdh_*.db")), "respaldo hecho antes"
assert not any((docs / f"n{i}.docx").exists() for i in range(3)) and fuera.exists(), "borra solo archivos dentro de la carpeta de datos"
with db._conexion() as con:
    for t in ("historial_estados", "notas_texto", "nota_entidades", "numeracion", "bloques"):
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    assert con.execute("SELECT COUNT(*) FROM sugerencias_ia").fetchone()[0] == 1 and con.execute("SELECT nota_id FROM sugerencias_ia").fetchone()[0] is None
    assert con.execute("SELECT COUNT(*) FROM notificaciones WHERE url LIKE '/nota/%'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM notificaciones").fetchone()[0] >= 1
assert len(db.obtener_modificaciones()) == antes_correcciones == 3, "lo aprendido se conserva"
assert db.listar_feedback(10) and db.listar_terminos() and db.listar_plazos_destinatario()
assert circuito.proponer("NOTA", anio) is None, "sin base: el número sale del registro de Mesa de Entradas"
assert db.buscar_texto("CPF") == [] if hasattr(db, "buscar_texto") else True
# los patrones siguen y las notas nuevas funcionan (ids nuevos, sin choque con las correcciones viejas)
assert aprendizaje.analizar_patrones(db.obtener_modificaciones(), ya_aprobadas=set())
f = docs / "nueva.docx"; f.write_bytes(b"PK")
nueva = db.crear_nota("Nueva.docx", eq, str(f), usuario_id=ana["id"])
assert nueva > max(ids)
assert "#" in main.ver_patrones(req(direc)).body
db.registrar_decision_director(nueva, "p1", "a", "b", "c", "modificado", "")
# una segunda limpieza también funciona (la tabla de correcciones ya no tiene la clave foránea)
asyncio.run(main.admin_limpieza_notas(req(direc, {"confirmacion": "BORRAR NOTAS", "entiendo": "1"})))
assert db.listar_notas() == [] and len(db.obtener_modificaciones()) == 4
print("limpieza OK")
print("ALL OK")
