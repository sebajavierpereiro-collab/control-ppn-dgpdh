"""Autos PPN: dos viajes a unidades vecinas (p. ej. CPF I y Unidad 19, ambas en Ezeiza) con el mismo chofer y vehículo
son una sola salida: se asignan sin aviso y comparten las plazas."""
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
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def docx(nombre, parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; r = tmp / nombre; d.save(r); return r
def nota(nombre, estados, u=None):
    r = docx(nombre, ["Al Sr. Director", "Cuerpo."])
    nid = db.crear_nota(nombre, E1, str(r), usuario_id=(u or ana)["id"])
    for e in estados: db.actualizar_estado(nid, e)
    with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(r), nid))
    return nid
IDX = dict(mostrar_despachadas=False, orden="", direccion="asc", f_nombre="", f_tipo="", f_etapa="", f_fecha="", f_prioridad="", f_tema="",
           f_tarea="", pagina=1, vista="", ver="", agrupar="")

from datetime import date as D, timedelta as TD
import traslados as tr, operacion as op
from traslados import ErrorTraslado
R = main.app.rutas
sub = U("sub", "Sara Subdirectora", "SUBDIRECTOR")
coordtr = U("coordtr", "Carla Coordina", "ASESOR", E1); db.actualizar_usuario(coordtr["id"], coordtr["nombre"], "ASESOR", E1, coordina_traslados=True)
coordtr = db.obtener_usuario(coordtr["id"])
chofer_u = U("chofer1", "Pedro Chofer", "CHOFER")
tr.sincronizar_choferes()
def falla(fn, codigo):
    try: run(fn())
    except main.HTTPException as e: assert e.status_code == codigo, (e.status_code, e.detail); return
    raise AssertionError(f"se esperaba {codigo}")

# ---------------------------------------------------------------- misma salida a unidades vecinas
uni = {u["nombre"]: u for u in db.listar_unidades()}
cpf1 = uni["Complejo Penitenciario Federal I de Ezeiza -CPF I-"]; u19 = uni["Unidad 19 -Colonia Penal de Ezeiza-"]
cpf2 = uni["Complejo Penitenciario Federal II de Marcos Paz -CPF II-"]
assert (cpf1.get("ciudad"), u19.get("ciudad")) == ("Ezeiza", "Ezeiza"), (cpf1.get("ciudad"), u19.get("ciudad"))
dia = D.today() + TD(days=9)
while dia.weekday() >= 5: dia += TD(days=1)
F = dia.isoformat()
ch = next(c["id"] for c in tr.listar_choferes() if c["usuario_id"] == chofer_u["id"])
ch2 = tr.guardar_chofer(None, {"nombre": "Otro Chofer"})
van = tr.guardar_vehiculo(None, {"dominio": "AA111AA", "marca": "Mercedes", "modelo": "Sprinter", "capacidad": "17"})
chico = tr.guardar_vehiculo(None, {"dominio": "BB222BB", "marca": "Fiat", "modelo": "Cronos", "capacidad": "4"})
base = dict(fecha=F, hora_salida="09:00", hora_regreso="13:00", motivo="Monitoreo")
gente = lambda pref, n: [{"nombre": f"{pref} {i}"} for i in range(n)]
a = tr.crear_viaje(dict(base, unidad_id=cpf1["id"]), gente("CPF", 4), coordtr)
b = tr.crear_viaje(dict(base, unidad_id=u19["id"]), gente("U19", 1), coordtr)
assert tr.destinos_cercanos(tr.obtener_viaje(a), tr.obtener_viaje(b))
tr.asignar(a, ch, van, coordtr)
# la sugerencia ya no lo muestra «ocupado»: misma salida
sug = tr.sugerencias(b)
assert not next(c for c in sug["choferes"] if c["id"] == ch)["ocupado"] and "misma salida" in next(c for c in sug["choferes"] if c["id"] == ch)["motivo"]
xv = next(x for x in sug["vehiculos"] if x["id"] == van); assert not xv["ocupado"] and xv["misma_salida"] == a and sug["vehiculos"][0]["id"] == van, sug["vehiculos"]
# se asigna sin «forzar» y sin conflicto
assert tr.asignar(b, ch, van, coordtr) == []
va_, vb_ = tr.obtener_viaje(a), tr.obtener_viaje(b)
assert [o["id"] for o in vb_["misma_salida"]] == [a] and [o["id"] for o in va_["misma_salida"]] == [b]
assert va_["plazas_libres"] == vb_["plazas_libres"] == 12 and vb_["con_transporte_salida"] == 5, (va_["plazas_libres"], vb_["plazas_libres"])
assert any("misma salida que #%d" % a in (h.get("accion") or "") for h in tr.historial("viaje", b))
# en la semana: plazas compartidas y chip
sem = [v for v in tr.listar_viajes(desde=F, hasta=F) if v["id"] in (a, b)]
assert all(v["plazas_libres"] == 12 for v in sem)
html = R[("GET", "/traslados")](req(coordtr, "/traslados"), lunes=F, circuito="AMBA", ver="semana").body
html = html.decode() if isinstance(html, bytes) else html
assert "misma salida con Unidad 19" in html and "12 libres" in html and "16 libres" not in html, "la semana muestra las plazas compartidas"
# por la ruta: mensaje claro
c = tr.crear_viaje(dict(base, unidad_id=u19["id"], hora_salida="10:00"), gente("U19b", 2), coordtr)
rr = req(coordtr, "/x", form={"chofer_id": str(ch), "vehiculo_id": str(van)}); run(R[("POST", "/traslados/{viaje_id:int}/asignar")](rr, c))
assert rr.session["flash"].startswith("Asignado. Va en la misma salida que") and "7 de 17 plazas" in rr.session["flash"], rr.session["flash"]
html = R[("GET", "/traslados/{viaje_id:int}")](req(coordtr, f"/traslados/{c}"), c).body
html = html.decode() if isinstance(html, bytes) else html
assert "Misma salida" in html and "7 de 17 plazas" in html
# capacidad compartida: no entran
d = tr.crear_viaje(dict(base, unidad_id=u19["id"]), gente("Muchos", 11), coordtr)
try: tr.asignar(d, ch, van, coordtr); raise AssertionError("no entran")
except ErrorTraslado as e: assert "18 personas" in str(e) and "17 plazas" in str(e), str(e)
# sumar una persona a una parada respeta las plazas de toda la salida
for i in range(10): tr.agregar_participante(a, {"nombre": f"Extra {i}"})
try: tr.agregar_participante(b, {"nombre": "Una más"}); raise AssertionError("salida llena")
except ErrorTraslado as e: assert "misma salida" in str(e), str(e)
# destino lejano (Marcos Paz) con el mismo chofer: sigue siendo conflicto
e_ = tr.crear_viaje(dict(base, unidad_id=cpf2["id"]), gente("MP", 1), coordtr)
try: tr.asignar(e_, ch, van, coordtr); raise AssertionError("Marcos Paz no es vecina")
except ErrorTraslado as ex: assert "Conflicto" in str(ex), str(ex)
# mismo chofer pero otro vehículo a la misma hora: conflicto (no puede manejar dos)
f_ = tr.crear_viaje(dict(base, unidad_id=u19["id"]), gente("Otro", 1), coordtr)
try: tr.asignar(f_, ch, chico, coordtr); raise AssertionError("chofer en dos vehículos")
except ErrorTraslado as ex: assert "Conflicto" in str(ex)
# a la tarde, misma unidad vecina: salidas distintas (no se pisan)
g = tr.crear_viaje(dict(base, unidad_id=u19["id"], hora_salida="15:00", hora_regreso="18:00"), gente("Tarde", 1), coordtr)
tr.asignar(g, ch, van, coordtr)
assert not tr.obtener_viaje(g).get("misma_salida") and tr.obtener_viaje(g)["plazas_libres"] == 16
print("misma salida a unidades vecinas OK")
