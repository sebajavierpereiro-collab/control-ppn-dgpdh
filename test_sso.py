"""Ingreso con el portal PPN (SSO): sesión, vínculo por correo, pedidos pendientes y pestaña de Administración, con un portal de mentira."""
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
import asyncio, io, json, tempfile, urllib.error
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth, sso
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()

# ---- portal de mentira
PERSONAS = {
    "tok-ana": {"id": 501, "firstname": "Ana", "lastname": "Pérez", "email": "Ana.Perez@ppn.gov.ar", "isEnabled": True,
                "area": {"id": 17, "name": "Equipo Complejo Penitenciario Federal N° I Ezeiza"}},
    "tok-nuevo": {"id": 777, "firstname": "Lucía", "lastname": "Gómez", "email": "lgomez@ppn.gov.ar", "isEnabled": True,
                  "area": {"id": 18, "name": "Equipo Complejo Penitenciario Federal N° II  Marcos Paz"}},
}
CERRADAS = set(); SIN_RED = [False]; LLAMADAS = []
class Resp:
    def __init__(s, status, datos): s.status = status; s._b = json.dumps(datos).encode()
    def read(s): return s._b
    def __enter__(s): return s
    def __exit__(s, *a): return False
def portal(pedido, timeout=None):
    LLAMADAS.append((pedido.get_method(), pedido.full_url))
    if SIN_RED[0]: raise urllib.error.URLError("sin red")
    assert pedido.get_header("Application-id") == "uuid-app"
    tok = pedido.get_header("Authorization")
    ruta = pedido.full_url.replace("https://sso.test", "")
    def no(): raise urllib.error.HTTPError(pedido.full_url, 401, "no", {}, io.BytesIO(b"false"))
    if ruta.endswith("/get-session"):
        if tok not in PERSONAS or tok in CERRADAS: no()
        u = PERSONAS[tok]; return Resp(200, {"user": dict(u, role={"name": "Asesor"}), "area": u["area"], "role": {"name": "Asesor"}, "permissions": []})
    if ruta.endswith("/check-session"):
        if tok not in PERSONAS or tok in CERRADAS: no()
        return Resp(200, True)
    if ruta.endswith("/logout"):
        CERRADAS.add(tok); return Resp(200, True)
    if ruta == "/api/sync/users":
        return Resp(200, {"users": [dict(u, role={"id": 1, "name": "Asesor"}) for u in PERSONAS.values()]})
    if ruta == "/api/sync/areas":
        return Resp(200, {"areas": [u["area"] for u in PERSONAS.values()]})
    raise AssertionError(ruta)
sso._abrir = portal

import configparser
cfg = configparser.ConfigParser(); cfg.read_string("[sso]\nurl = https://sso.test/\naplicacion_id = uuid-app\nportal_url = https://portal.test\nverificar_cada_minutos = 1\n")
sso.configurar(cfg)
assert sso.activo() and sso.URL == "https://sso.test" and sso.VERIFICAR_CADA == 60

eq1 = db.crear_equipo("Equipo CPF I Ezeiza"); eq2 = db.crear_equipo("Equipo CPF II Marcos Paz"); db.crear_equipo("Equipo CABA")
h, s = auth.generar_hash_clave("Clave1234")
ana_id = db.crear_usuario("ana", h, s, "Ana Pérez", "ASESOR", eq1)
with db._conexion() as con: con.execute("UPDATE usuarios SET correo = 'ana.perez@ppn.gov.ar' WHERE id = ?", (ana_id,))
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])

class Cliente: host = "10.0.0.1"
def req(u=None, form=None):
    async def _form(): return form or {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=u), session={}, client=Cliente(), headers={},
                                 url=types.SimpleNamespace(path="/"), form=_form, cookies={})

# 1) sugerencia de equipo por área (solo sugerencia)
eqs = db.listar_equipos()
assert sso.equipo_para_area("Equipo Complejo Penitenciario Federal N° I Ezeiza", eqs)["id"] == eq1
assert sso.equipo_para_area("Equipo Complejo Penitenciario Federal N° II  Marcos Paz", eqs)["id"] == eq2
assert sso.equipo_para_area("Salud Mental", eqs) is None
print("sugerencia de equipo OK")

# 2) ingreso de quien tiene el mismo correo: entra y queda vinculado
r = req(); res = main.ingreso_portal(r, "tok-ana")
assert res.status_code == 303 and r.session["usuario_id"] == ana_id and r.session["sso_token"] == "tok-ana"
assert db.obtener_usuario(ana_id)["sso_id"] == 501 and db.usuario_por_sso_id(501)["id"] == ana_id
# la segunda vez lo encuentra por el vínculo aunque cambie el correo en el portal
PERSONAS["tok-ana"]["email"] = "otro@ppn.gov.ar"
r = req(); assert main.ingreso_portal(r, "tok-ana").status_code == 303 and r.session["usuario_id"] == ana_id
print("ingreso por correo y vínculo OK")

# 3) sin usuario local: no entra, queda el pedido
r = req(); res = main.ingreso_portal(r, "tok-nuevo")
assert res.status_code == 403 and "usuario_id" not in r.session and "todavía no está habilitado" in res.body
pend = db.listar_sso_pendientes(); assert len(pend) == 1 and pend[0]["sso_id"] == 777 and pend[0]["correo"] == "lgomez@ppn.gov.ar"
main.ingreso_portal(req(), "tok-nuevo"); assert db.listar_sso_pendientes()[0]["intentos"] == 2
# 4) token inválido y portal caído
res = main.ingreso_portal(req(), "tok-falso"); assert res.status_code == 401 and "venció" in res.body
SIN_RED[0] = True; res = main.ingreso_portal(req(), "tok-ana"); assert res.status_code == 503; SIN_RED[0] = False
assert sso.sesion_viva("tok-ana") is True and sso.sesion_viva("tok-falso") is False
SIN_RED[0] = True; assert sso.sesion_viva("tok-ana") is None; SIN_RED[0] = False
print("pendientes, token inválido y portal caído OK")

# 5) usuario desactivado no entra aunque el portal lo reconozca
with db._conexion() as con: con.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (ana_id,))
assert main.ingreso_portal(req(), "tok-ana").status_code == 403
with db._conexion() as con: con.execute("UPDATE usuarios SET activo = 1 WHERE id = ?", (ana_id,))

# 6) Administración: pestaña, cruce y crear+vincular
d = main._datos_portal(traer=True)
assert d["activo"] and len(d["pendientes"]) == 1 and d["pendientes"][0]["equipo_sugerido"]["id"] == eq2
cr = {p["sso_id"]: p for p in d["cruce"]}
assert cr[501]["usuario"]["id"] == ana_id and cr[501]["como"] == "vinculado" and cr[777]["usuario"] is None
html = main.admin_panel(req(direc), "usuarios", "portal", "", "", 1).body
assert "Pedidos de acceso" in html and "Lucía Gómez" in html and "Crear y vincular" in html and "Personas habilitadas" in html
rr = req(direc, {"sso_id": "777", "nombre": "Lucía Gómez", "correo": "lgomez@ppn.gov.ar", "rol": "ASESOR", "equipo_id": str(eq2)})
asyncio.run(main.admin_portal_crear(rr))
nueva = db.usuario_por_sso_id(777)
assert nueva and nueva["usuario"] == "lgomez" and nueva["equipo_id"] == eq2 and nueva["rol"] == "ASESOR" and not db.listar_sso_pendientes()
r = req(); assert main.ingreso_portal(r, "tok-nuevo").status_code == 303 and r.session["usuario_id"] == nueva["id"]
# asesor sin equipo: no se crea
rr = req(direc, {"sso_id": "999", "nombre": "X", "rol": "ASESOR"}); asyncio.run(main.admin_portal_crear(rr))
assert db.usuario_por_sso_id(999) is None and "equipo" in rr.session["flash_admin"]
# desvincular
asyncio.run(main.admin_portal_desvincular(req(direc, {"usuario_id": str(ana_id)})))
assert db.obtener_usuario(ana_id)["sso_id"] is None
asyncio.run(main.admin_portal_vincular(req(direc, {"usuario_id": str(ana_id), "sso_id": "501"})))
assert db.obtener_usuario(ana_id)["sso_id"] == 501
print("administración del portal OK")

# 7) salir: avisa al portal y vuelve al portal
r = req(); r.session.update({"usuario_id": ana_id, "sso_token": "tok-ana"})
res = main.logout(r); assert res.headers["location"] == "https://portal.test" and "tok-ana" in CERRADAS and not r.session
assert sso.sesion_viva("tok-ana") is False
# 8) desactivado (sin configurar): la ruta avisa y no consulta nada
sso.configurar(configparser.ConfigParser()); n = len(LLAMADAS)
assert main.ingreso_portal(req(), "tok-ana").status_code == 404 and len(LLAMADAS) == n
print("salida y desactivado OK")
# 9) preparación sin conexión: catálogo de áreas, faltantes, asignación de áreas y planilla
areas = sso.areas_locales(); assert len(areas) > 50 and any(a["nombre"].startswith("Dirección General de Protección") for a in areas)
pr = sso.preparacion(db.listar_usuarios(), db.listar_equipos())
assert any(u["usuario"] == "director" for u in pr["sin_correo"]) and pr["porcentaje"] < 100
sug = {f["equipo"]["nombre"]: f["sugerida"] for f in pr["equipos"]}
assert sug["Equipo CPF I Ezeiza"]["id"] == 17 and sug["Equipo CPF II Marcos Paz"]["id"] == 18, sug
asyncio.run(main.admin_portal_areas(req(direc, {f"area_{eq1}": "17", f"area_{eq2}": "18"})))
eqd = {e["id"]: e for e in db.listar_equipos()}; assert eqd[eq1]["sso_area_id"] == 17 and eqd[eq2]["sso_area_id"] == 18
asyncio.run(main.admin_portal_areas(req(direc, {f"area_{eq1}": "99999"}))); assert db.listar_equipos()[0]["sso_area_id"] in (None, 17, 18)
assert {e["id"]: e for e in db.listar_equipos()}[eq1]["sso_area_id"] is None, "un área que no existe no se guarda"
csvt = main.admin_portal_planilla(req(direc)).body.decode("utf-8-sig")
assert "Usuario;Nombre;Correo" in csvt and "lgomez@ppn.gov.ar" in csvt and "Marcos Paz" in csvt
html = main.admin_panel(req(direc), "usuarios", "portal", "", "", 0).body
assert "No está vinculado, a propósito" in html and "Pedidos de acceso" not in html and "Guardar áreas" in html
print("preparación sin conexión OK")
print("ALL OK")
