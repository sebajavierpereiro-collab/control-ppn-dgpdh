"""Ola 1 · sistema: respaldo con prueba de restauración, copia externa, cierre por inactividad, revisión trimestral de usuarios."""
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
        r = R(); r.body = self.env.get_template(nombre).render(**ctx); r.status_code = 200; return r
ft.Jinja2Templates = Jinja2Templates
sys.modules.update({"fastapi": fa, "fastapi.exceptions": fe, "fastapi.responses": fr, "fastapi.staticfiles": fs, "fastapi.templating": ft})
import asyncio, tempfile, time
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth, respaldo
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
_d = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
with db._conexion() as con: con.execute("UPDATE usuarios SET debe_cambiar_clave = 0 WHERE id = ?", (_d["id"],))
direc = db.obtener_usuario(_d["id"])

# ---- 90 · respaldo, copia externa y prueba de restauración
respaldo.CONFIG.update(carpeta=tmp / "resp", carpeta_datos=tmp, copia_externa=None, activo=True)
assert respaldo.en_la_misma_pc(), "respaldo dentro de datos sin copia externa = misma PC"
ok, det = respaldo.verificar_ultimo_respaldo(); assert not ok and "No hay" in det
respaldo.CONFIG["copia_externa"] = tmp.parent / ("ext_" + tmp.name)
ok, res = respaldo.hacer_respaldo(); assert ok and "copia externa" in res, res
assert list((respaldo.CONFIG["copia_externa"] / "db").glob("dgpdh_*.db")) and not respaldo.en_la_misma_pc()
assert respaldo.toca_verificacion()
ok, det = respaldo.verificar_ultimo_respaldo(); assert ok and "sana" in det, det
assert not respaldo.toca_verificacion(), "una vez por mes"
e = respaldo.estado(); assert e["verificacion"]["ok"] and e["copia_externa"]
# respaldo dañado: la prueba lo detecta
roto = tmp / "resp" / "db" / "dgpdh_29991231_000000.db"; roto.write_bytes(b"esto no es una base")
ok, det = respaldo.verificar_ultimo_respaldo(); assert not ok, det
roto.unlink()
print("90 respaldo OK")

# ---- 94 · cierre por inactividad
class Sesion(dict): pass
class Req:
    def __init__(s, sesion, path="/", headers=None):
        s.session, s.method, s.headers = sesion, "GET", headers or {}
        s.url = types.SimpleNamespace(path=path, query=""); s.state = types.SimpleNamespace(); s.client = None
async def siguiente(req):
    import starlette.responses as sr
    return sr.Response("ok", media_type="text/plain")
mw = main.AuthMiddleware.__new__(main.AuthMiddleware)
corre = lambda req: asyncio.run(mw.dispatch(req, siguiente))
main.MINUTOS_INACTIVIDAD = 60
s1 = Sesion(usuario_id=direc["id"], ultima_actividad=int(time.time()) - 30 * 60)
r = corre(Req(s1)); assert r.status_code == 200 and s1["ultima_actividad"] >= int(time.time()) - 2
s2 = Sesion(usuario_id=direc["id"], ultima_actividad=int(time.time()) - 61 * 60)
r = corre(Req(s2)); assert r.status_code in (302, 307) and "inactividad" in r.headers["location"] and "usuario_id" not in s2
s3 = Sesion(usuario_id=direc["id"], ultima_actividad=int(time.time()) - 10 * 60)
antes = s3["ultima_actividad"]; corre(Req(s3, headers={"x-sin-actividad": "1"})); assert s3["ultima_actividad"] == antes
main.MINUTOS_INACTIVIDAD = 0
s4 = Sesion(usuario_id=direc["id"], ultima_actividad=1); assert corre(Req(s4)).status_code == 200, "0 = sin límite"
main.MINUTOS_INACTIVIDAD = 60
html = main.templates.env.get_template("login.html").render(request=None, aviso="Se cerró la sesión por 60 minutos sin uso.")
assert "Se cerró la sesión" in html
print("94 inactividad OK")

# ---- 97 · revisión trimestral
h, s = auth.generar_hash_clave("Clave1234")
uid = db.crear_usuario("vieja", h, s, "Cuenta Vieja", "ASESOR", None)
db.registrar_intento_login("vieja", "1.1.1.1", True)
assert db.revision_usuarios_vencida()
assert db.recordar_revision_usuarios() and not db.recordar_revision_usuarios(), "un aviso, no repetido"
d = main._datos_revision_usuarios()
vieja = next(u for u in d["usuarios"] if u["usuario"] == "vieja")
assert "sin equipo" in vieja["alertas"] and vieja["dias"] == 0 and d["vencida"]
db.registrar_revision_usuarios("Director", "todo en orden")
assert not db.revision_usuarios_vencida() and main._datos_revision_usuarios()["revisiones"][0]["observaciones"] == "todo en orden"
class URL: path = "/admin"; query = ""
rq = types.SimpleNamespace(state=types.SimpleNamespace(usuario=direc), url=URL(), session={}, cookies={}, headers={}, query_params={})
hrev = main.admin_panel(rq, pestana="usuarios", sub="revision").body
assert "Revisión trimestral de usuarios" in hrev and "Cuenta Vieja" in hrev and "todo en orden" in hrev
hsis = main.admin_panel(rq, pestana="sistema", sub="estado").body
assert "Probar restaurar el último respaldo" in hsis and "Prueba de restauración" in hsis
print("97 revisión OK")
print("ALL OK")
