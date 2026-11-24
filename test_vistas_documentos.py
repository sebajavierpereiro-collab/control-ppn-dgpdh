"""Mis documentos / Documentos del equipo en el listado de notas (main.index), con un FastAPI de mentira sobre starlette."""
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
import tempfile
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)

import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
eq = db.crear_equipo("Equipo A"); eq2 = db.crear_equipo("Equipo B")
h, s = auth.generar_hash_clave("Clave1234")
ana = db.obtener_usuario(db.crear_usuario("ana", h, s, "Ana", "ASESOR", eq))
beto = db.obtener_usuario(db.crear_usuario("beto", h, s, "Beto", "ASESOR", eq))
coord = db.obtener_usuario(db.crear_usuario("coo", h, s, "Coord", "COORDINADOR", eq))
otro = db.obtener_usuario(db.crear_usuario("otro", h, s, "Otro", "ASESOR", eq2))
r = tmp / "x.docx"; r.write_bytes(b"PK")
def nota(nombre, u, estado="SUBIDA", equipo=eq):
    nid = db.crear_nota(nombre, equipo, str(r), usuario_id=u["id"])
    if estado != "SUBIDA": db.actualizar_estado(nid, estado)
    return nid
nota("ana1.docx", ana); nota("ana2.docx", ana, "PROCESADA"); nota("ana_desp.docx", ana, "DESPACHADA")
nota("beto1.docx", beto); nota("beto2.docx", beto, "EN_REVISION"); nota("ana_borr.docx", ana, "BORRADOR")
nota("beto_borr.docx", beto, "BORRADOR"); nota("otro.docx", otro, equipo=eq2)
class URL: path = "/"; query = ""
def req(u): return types.SimpleNamespace(state=types.SimpleNamespace(usuario=u), url=URL(), session={}, cookies={}, headers={})
def ver(u, **kw):
    args = dict(mostrar_despachadas=False, orden="", direccion="asc", f_nombre="", f_tipo="", f_etapa="", f_fecha="", f_prioridad="", f_tema="", f_tarea="", pagina=1, vista="",
                ver="todos", agrupar="ninguno")
    args.update(kw)
    return main.index(req(db.obtener_usuario(u["id"])), **args).body
h1 = ver(ana)
assert "Mis documentos" in h1 and 'pestana pestana--activa" href="/?vista=mias"' in h1, "asesor: Mis documentos por defecto"
assert "ana1.docx" in h1 and "beto1.docx" not in h1 and "otro.docx" not in h1
import re
cont = re.findall(r'<span class="contador">(\d+)</span>', h1); assert cont == ["0", "2", "4"], cont  # 0 pendientes de acción · 2 propias · 4 del equipo
h2 = ver(ana, vista="equipo")
assert "beto1.docx" in h2 and "beto2.docx" in h2 and "ana1.docx" in h2 and "otro.docx" not in h2 and "beto_borr.docx" not in h2
assert "ana_borr.docx" in h2  # su borrador sigue en su panel «Sin enviar»
hc = ver(coord)
assert 'pestana pestana--activa" href="/?vista=equipo"' in hc and "beto1.docx" in hc, "coordinador: equipo por defecto"
assert "beto1.docx" not in ver(coord, vista="mias")
ht = ver(coord, f_tarea="aprobar", vista="mias")
assert "beto1.docx" in ht and "ana1.docx" in ht, "las tareas del coordinador miran al equipo"
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
hd = ver(direc)
assert "Mis documentos" not in hd and "otro.docx" in hd, "Dirección conserva la vista global"
assert 'name="vista" value="equipo"' in h2
print("vistas de documentos OK")

# ---- «Pendientes de mi acción» por perfil, búsqueda y agrupado (se recuerda la elección)
mesa = db.obtener_usuario(db.crear_usuario("mesa", h, s, "Mesa", "MESA_ASESOR", None))
nota("firmada.docx", beto, "FIRMADA")
filas = lambda html: re.findall(r'href="/nota/\d+">Ver</a>', html)
hp = ver(direc, ver="pendientes")
assert "ana2.docx" in hp and "beto2.docx" in hp and "ana1.docx" not in hp and "firmada.docx" not in hp, "Dirección: para revisar y firmar"
assert re.search(r'Pendientes de mi acción <span class="contador">2</span>', hp)
hm = ver(mesa, ver="pendientes")
assert "firmada.docx" in hm and "ana2.docx" not in hm, "Mesa: firmadas para despachar"
hc = ver(coord, ver="pendientes")
assert "ana1.docx" in hc and "beto1.docx" in hc and "ana2.docx" not in hc, "Coordinación: subidas para aprobar"
ha = ver(ana, ver="pendientes")
assert "No tenés documentos pendientes de acción" in ha and "Buscar en todos los documentos" in ha and "panel-filtros" not in ha
# la elección se recuerda: sin parámetros vuelve a «pendientes»
assert 'pestana pestana--activa" href="/?ver=pendientes"' in ver(direc, ver="")
# buscar desde «pendientes» pasa a «todos»
hb = ver(direc, ver="", f_nombre="ana1")
assert 'pestana pestana--activa" href="/?ver=todos"' in hb and "ana1.docx" in hb and "beto1.docx" not in hb
# agrupar por equipo / asesor / prioridad / fecha
he = ver(direc, ver="todos", agrupar="equipo")
assert 'class="fila-grupo"' in he and "Equipo A" in he and "Equipo B" in he and he.index("Equipo A <span") < he.index("Equipo B <span")
hs = ver(direc, ver="", agrupar="asesor")
assert all(n in hs for n in ("Ana <span", "Beto <span", "Otro <span"))
assert 'data-en-grupo="1"' in hs and "paginador" not in hs
hpri = ver(direc, ver="", agrupar="prioridad")
assert "Normal <span" in hpri
hf = ver(direc, ver="", agrupar="fecha")
assert "Hoy <span" in hf
assert '<option value="fecha" selected>' in ver(direc, ver="", agrupar=""), "la agrupación elegida se recuerda"
assert 'class="fila-grupo"' not in ver(direc, ver="", agrupar="ninguno")
print("pendientes y agrupado OK")

# ---- Ola 1 notas: línea de tiempo, antigüedad en la etapa, tarjetas del inicio
from datetime import datetime, timedelta, timezone
ahora = datetime.now(timezone.utc)
vieja = lambda d: (ahora - timedelta(days=d)).isoformat(timespec="seconds")
assert main._antiguedad_etapa({"estado": "SUBIDA", "en_estado_desde": vieja(0)})["nivel"] == "verde"
assert main._antiguedad_etapa({"estado": "SUBIDA", "en_estado_desde": vieja(4)})["nivel"] == "ambar"
a = main._antiguedad_etapa({"estado": "PROCESADA", "en_estado_desde": vieja(9)}); assert a["nivel"] == "rojo" and a["texto"] == "9 días"
assert main._antiguedad_etapa({"estado": "DESPACHADA", "en_estado_desde": vieja(30)}) is None
nq = nota("quieta.docx", beto, "EN_REVISION")
with db._conexion() as con:
    con.execute("UPDATE historial_estados SET fecha = ? WHERE nota_id = ? AND estado = 'EN_REVISION'", (vieja(10), nq))
fila = next(n for n in db.listar_notas() if n["id"] == nq)
assert fila["en_estado_desde"].startswith(vieja(10)[:10])
hq = ver(direc, ver="todos")
assert "antiguedad--rojo" in hq and "10 días en la etapa" in hq
nt = db.obtener_nota(nq)
ev = main._eventos_nota(nt)
assert ev[0]["texto"] == "Enviada a Coordinación" or ev[0]["texto"] == "Registro" or ev, ev
assert any(e["texto"] == "En revisión" for e in ev)
db.registrar_evento(nq, "Prioridad cambiada a Urgente")
ev = main._eventos_nota(db.obtener_nota(nq)); assert ev[-1]["texto"] == "Prioridad cambiada a Urgente" and ev[-1]["detalle"] == ""
ri = main._resumen_inicio(direc)["notas"]
assert ri["pendientes"] >= 3 and ri["quietas"] >= 1 and ri["para_firmar"] == 1, ri
ra = main._resumen_inicio(db.obtener_usuario(ana["id"]))["notas"]; assert ra["pendientes"] == 0 and ra["para_firmar"] is None, ra
print("línea de tiempo, antigüedad e inicio OK")

# ---- Filtros guardados (reforma 35)
r1 = main.guardar_filtro(req(db.obtener_usuario(direc["id"])), "Urgentes de A", "f_prioridad=URGENTE&f_nombre=&evil=1&mostrar_despachadas=false&agrupar=equipo")
assert r1.headers["location"] == "/?f_prioridad=URGENTE&agrupar=equipo", r1.headers["location"]
hfg = ver(direc, ver="todos", f_prioridad="URGENTE")
assert "Mis filtros:" in hfg and 'href="/?f_prioridad=URGENTE&amp;agrupar=equipo">Urgentes de A' in hfg and "Guardar este filtro" in hfg
main.borrar_filtro(req(db.obtener_usuario(direc["id"])), "Urgentes de A")
assert "Urgentes de A" not in ver(direc, ver="todos")
assert main.guardar_filtro(req(db.obtener_usuario(direc["id"])), "vacío", "evil=1").headers["location"] == "/"
print("filtros guardados OK")
print("ALL OK")
