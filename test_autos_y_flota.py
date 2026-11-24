"""Autos PPN: sistemas habilitados por persona (Notas / Autos PPN), vehículos (alta, suspender, eliminar, recuperar), aviso
cuando no quedan vehículos y revisión de la Dirección General, y planificador por ubicación con unificación de viajes."""
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
def rform(u, form, referer="http://x/admin/usuarios/listado"):
    rr = req(u, "/admin", form=form); rr.headers = {"referer": referer}; return rr

# ---------------------------------------------------------------- A) Notas y Autos PPN, por persona
assert main._tiene_acceso_autos(ana) and main._tiene_acceso_notas(ana)
rr = rform(direc, {"sistema": "autos", "valor": "0"}); run(R[("POST", "/admin/usuarios/{usuario_id}/sistemas")](rr, ana["id"]))
assert rr.session["flash_admin"] == "Autos PPN deshabilitado para «Ana Pérez»." and not main._tiene_acceso_autos(db.obtener_usuario(ana["id"]))
assert main._pagina_inicial(db.obtener_usuario(ana["id"])) == "/inicio"
html = main.pagina_inicio(req(ana, "/inicio")).body
assert "+ Solicitar viaje" not in html and 'href="/traslados"' not in html, "sin Autos PPN no aparece en el menú ni en el inicio"
rr = rform(direc, {"sistema": "notas", "valor": "0"}); run(R[("POST", "/admin/usuarios/{usuario_id}/sistemas")](rr, ana["id"]))
r = main.pagina_inicio(req(ana, "/inicio")); assert "no tiene habilitado ningún sistema" in r.body
rr = rform(direc, {"sistema": "autos", "valor": "1"}); run(R[("POST", "/admin/usuarios/{usuario_id}/sistemas")](rr, ana["id"]))
assert main._pagina_inicial(db.obtener_usuario(ana["id"])) == "/traslados" and main.pagina_inicio(req(ana, "/inicio")).headers["location"] == "/traslados"
rr = rform(direc, {"sistema": "notas", "valor": "1"}); run(R[("POST", "/admin/usuarios/{usuario_id}/sistemas")](rr, ana["id"]))
rr = rform(direc, {"sistema": "autos", "valor": "0"}); run(R[("POST", "/admin/usuarios/{usuario_id}/sistemas")](rr, direc["id"]))
assert rr.session["flash_admin"].startswith("⚠ La Dirección General ve siempre"), "la Dirección no se deshabilita"
rr = rform(direc, {"sistema": "notas", "valor": "1"}); run(R[("POST", "/admin/usuarios/{usuario_id}/sistemas")](rr, chofer_u["id"]))
assert rr.session["flash_admin"].startswith("⚠ Los choferes ven solo Autos PPN")
rr = rform(direc, {"sistema": "autos", "valor": "0"}); run(R[("POST", "/admin/usuarios/{usuario_id}/sistemas")](rr, coordtr["id"]))
assert "coordina traslados" in rr.session["flash_admin"] and main._tiene_acceso_autos(db.obtener_usuario(coordtr["id"]))
# desde «Editar» también
rr = rform(direc, {"nombre": "Ana Pérez", "rol": "ASESOR", "equipo_id": str(E1), "acceso_notas_enviado": "1", "acceso_notas": "1", "acceso_autos_enviado": "1"})
run(main.admin_editar_usuario(rr, ana["id"])); assert not db.obtener_usuario(ana["id"])["acceso_traslados"]
LIST = dict(orden="", dir="asc", f_rol="", f_equipo="", f_estado="", agrupar="")
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **LIST).body
assert "chip-sistema--no" in html and "✕ Autos PPN" in html and "✓ Notas" in html and 'name="acceso_autos"' in html
db.actualizar_usuario(ana["id"], "Ana Pérez", "ASESOR", E1, acceso_traslados=True)
src = open(APP + "/main.py", encoding="utf-8").read()
assert "if path.startswith(_RUTAS_AUTOS) and not _tiene_acceso_autos(usuario):" in src, "el middleware corta /traslados sin el permiso"
print("A sistemas OK")

# ---------------------------------------------------------------- B) vehículos
def crear_v(dom, cap=4):
    return tr.guardar_vehiculo(None, {"dominio": dom, "marca": "Toyota", "modelo": "Hilux", "capacidad": str(cap)})
rr = req(coordtr, "/traslados/recursos", form={"dominio": "ab 123 cd", "capacidad": "4", "marca": "Renault"}); run(R[("POST", "/traslados/recursos/vehiculo")](rr))
assert rr.session["flash"] == "Vehículo AB123CD dado de alta correctamente."
va = next(x["id"] for x in tr.listar_vehiculos() if x["dominio"] == "AB123CD")
vb = crear_v("AC456EF", 9)
estado_v = lambda vid: next(x for x in tr.listar_vehiculos(incluir_baja=True) if x["id"] == vid)
rr = req(coordtr, "/traslados/recursos", form={"estado": "FUERA_DE_CIRCULACION", "motivo": "Sin seguro"}); run(R[("POST", "/traslados/recursos/vehiculo/{vid:int}/estado")](rr, va))
assert "AB123CD quedó suspendido" in rr.session["flash"] and estado_v(va)["estado"] == "FUERA_DE_CIRCULACION" and estado_v(va)["estado_motivo"] == "Sin seguro"
rr = req(coordtr, "/traslados/recursos", form={"estado": "BAJA", "motivo": "Vendido", "volver": "/traslados/vehiculos/%d" % va}); r = run(R[("POST", "/traslados/recursos/vehiculo/{vid:int}/estado")](rr, va))
assert "eliminado de la flota" in rr.session["flash"] and r.headers["location"] == f"/traslados/vehiculos/{va}"
assert va not in [x["id"] for x in tr.listar_vehiculos()] and estado_v(va)["estado"] == "BAJA", "no se borra: queda con estado BAJA"
html = R[("GET", "/traslados/recursos")](req(coordtr, "/traslados/recursos")).body
assert "Vehículos eliminados (1)" in html and "+ Dar de alta un vehículo" in html and "Suspender" in html
try: tr.guardar_vehiculo(None, {"dominio": "AB123CD", "capacidad": "4"}); raise AssertionError
except ErrorTraslado as e: assert "recuperalo" in str(e)
rr = req(coordtr, "/traslados/recursos", form={"estado": "DISPONIBLE"}); run(R[("POST", "/traslados/recursos/vehiculo/{vid:int}/estado")](rr, va))
assert "(recuperado)" in rr.session["flash"] and estado_v(va)["estado"] == "DISPONIBLE" and estado_v(va)["estado_motivo"] is None
html = R[("GET", "/traslados/vehiculos/{vid:int}")](req(coordtr, f"/traslados/vehiculos/{va}"), va).body
assert "Suspender AB123CD" in html and "Eliminar AB123CD" in html
falla(lambda: R[("POST", "/traslados/recursos/vehiculo/{vid:int}/estado")](req(ana, "/x", form={"estado": "BAJA"}), va), 403)
# editar todos los datos desde el panel de flota / ficha del vehículo
html = R[("GET", "/traslados/flota")](req(coordtr, "/traslados/flota")).body
assert f'href="/traslados/vehiculos/{vb}?editar=1#editar-datos"' in html and "Editar datos de AC456EF" in html
html = R[("GET", "/traslados/vehiculos/{vid:int}")](req(coordtr, f"/traslados/vehiculos/{vb}?editar=1"), vb, "1").body
assert 'id="editar-datos" open' in html and 'name="dominio" required maxlength="12" size="10" value="AC456EF"' in html
for campo in ("marca", "modelo", "anio", "combustible", "capacidad", "km", "reservado_para", "seguro_vence", "vtv_vence", "service_proximo", "observaciones"):
    assert f'name="{campo}"' in html, campo
form_ed = {"id": str(vb), "volver": f"/traslados/vehiculos/{vb}", "dominio": "ac 456 ef", "marca": "Toyota", "modelo": "Hiace", "anio": "2021",
           "combustible": "Diésel", "capacidad": "12", "km": "45.300", "reservado_para": "", "seguro_vence": "2027-03-01",
           "vtv_vence": "2027-05-10", "service_proximo": "", "observaciones": "Rueda de auxilio nueva"}
rr = req(coordtr, "/x", form=form_ed); r = run(R[("POST", "/traslados/recursos/vehiculo")](rr))
assert r.headers["location"] == f"/traslados/vehiculos/{vb}"
assert rr.session["flash"].startswith("Datos de AC456EF guardados correctamente (modelo, año, combustible, plazas, kilometraje, seguro vence, vtv vence, observaciones")
x = estado_v(vb)
assert (x["modelo"], x["anio"], x["combustible"], x["capacidad"], x["km"], x["seguro_vence"], x["observaciones"]) == ("Hiace", 2021, "Diésel", 12, 45300, "2027-03-01", "Rueda de auxilio nueva")
hist = tr.historial("vehiculo", vb)
assert any(h["accion"] == "Dato modificado: Plazas" and h["anterior"] == "9" and h["nuevo"] == "12" for h in hist), hist[:3]
rr = req(coordtr, "/x", form=form_ed); run(R[("POST", "/traslados/recursos/vehiculo")](rr))
assert rr.session["flash"] == "No había cambios para guardar en AC456EF."
# un formulario parcial no borra los demás datos
tr.guardar_vehiculo(vb, {"marca": "Toyota", "modelo": "Hiace L"})
x = estado_v(vb); assert x["modelo"] == "Hiace L" and x["capacidad"] == 12 and x["km"] == 45300 and x["observaciones"] == "Rueda de auxilio nueva"
# errores: vuelve a la ficha con el formulario abierto
rr = req(coordtr, "/x", form=dict(form_ed, capacidad="0")); r = run(R[("POST", "/traslados/recursos/vehiculo")](rr))
assert r.headers["location"] == f"/traslados/vehiculos/{vb}?editar=1" and "al menos 1" in rr.session["flash"]
rr = req(coordtr, "/x", form=dict(form_ed, dominio="AB123CD")); run(R[("POST", "/traslados/recursos/vehiculo")](rr))
assert "Ya hay un vehículo con ese dominio" in rr.session["flash"]
falla(lambda: R[("POST", "/traslados/recursos/vehiculo")](req(ana, "/x", form=form_ed)), 403)
print("edición de vehículos OK")
print("B vehículos OK")

# ---------------------------------------------------------------- C) sin vehículos libres → revisión de la Dirección General
dia = D.today() + TD(days=10)
while dia.weekday() >= 5: dia += TD(days=1)
F = dia.isoformat()
ch_id = next(c["id"] for c in tr.listar_choferes() if c["usuario_id"] == chofer_u["id"])
ch2 = tr.guardar_chofer(None, {"nombre": "Otro Chofer"})
base = dict(fecha=F, hora_salida="09:00", hora_regreso="13:00", destino_otro="CPF I Ezeiza", motivo="Monitoreo")
v1 = tr.crear_viaje(base, [{"usuario_id": coordtr["id"], "nombre": coordtr["nombre"]}], coordtr)
tr.asignar(v1, ch_id, va, coordtr)
d = tr.disponibilidad_vehiculos(F, "10:00", "12:00"); assert not d["completo"] and d["libres"] == 1, d
v2 = tr.crear_viaje(dict(base, destino_otro="Marcos Paz"), [{"usuario_id": coordtr["id"], "nombre": coordtr["nombre"]}], coordtr)
tr.asignar(v2, ch2, vb, coordtr, forzar=True)
d = tr.disponibilidad_vehiculos(F, "10:00", "12:00")
assert d["completo"] and d["asignados"] == 2 and "ya están en uso todos los vehículos (2)" in d["texto"], d
assert not tr.disponibilidad_vehiculos(F, "15:00", "17:00")["completo"], "a la tarde los autos están libres"
assert tr.disponibilidad_vehiculos(F)["completo"], "sin horario se cuenta todo el día"
r = R[("GET", "/traslados/disponibilidad")](req(ana, "/x"), fecha=F, hora_salida="10:00", hora_regreso="12:00")
assert json.loads(r.body)["completo"] is True
html = R[("GET", "/traslados/nuevo")](req(ana, "/traslados/nuevo")).body if ("GET", "/traslados/nuevo") in R else ""
# al cargar: aviso; si es prioritario, va a la Dirección General
n_dir = len(db.listar_notificaciones(direc["id"])); n_sub = len(db.listar_notificaciones(sub["id"]))
form = {"fecha": F, "hora_salida": "10:00", "hora_regreso": "12:00", "destino_otro": "Hospital Penitenciario", "motivo": "Audiencia",
        "agentes": [str(ana["id"])], "accion": "guardar", "prioritario": "1", "motivo_prioridad": "Audiencia judicial con plazo que vence ese día"}
rr = req(ana, "/traslados/nuevo", form=form); r = run(R[("POST", "/traslados/nuevo")](rr))
v3 = int(r.headers["location"].rsplit("/", 1)[1])
assert "se pidió la revisión de la Dirección General" in rr.session["flash"], rr.session["flash"]
assert tr.obtener_viaje(v3)["revision_direccion"] == "PENDIENTE"
for quien in (direc, sub):
    assert any("pide su revisión" in x["texto"] for x in db.listar_notificaciones(quien["id"])), "Director y Subdirectora"
rr = req(ana, "/traslados/nuevo", form=dict(form, prioritario="", motivo_prioridad="")); run(R[("POST", "/traslados/nuevo")](rr))
assert rr.session["flash"].startswith("⚠ Viaje registrado. Atención: ese día ya están en uso todos los vehículos")
# en el detalle: pedir después, y la Dirección decide
v4 = max(x["id"] for x in tr.listar_viajes(desde=F, hasta=F))
html = R[("GET", "/traslados/{viaje_id:int}")](req(ana, f"/traslados/{v4}"), v4).body
assert "Sin vehículos libres" in html and "Pedir revisión de la Dirección General" in html
rr = req(ana, "/x", form={"motivo_prioridad": "corto"}); run(R[("POST", "/traslados/{viaje_id:int}/prioridad")](rr, v4))
assert "Contá por qué es prioritario" in rr.session["flash"]
html = R[("GET", "/traslados/{viaje_id:int}")](req(sub, f"/traslados/{v3}"), v3).body
assert "Dar prioridad" in html and "No corresponde" in html
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/revision-direccion")](req(coordtr, "/x", form={"decision": "PRIORIDAD"}), v3), 403)
rr = req(sub, "/x", form={"decision": "NO_CORRESPONDE", "comentario": ""}); run(R[("POST", "/traslados/{viaje_id:int}/revision-direccion")](rr, v3))
assert "Indicá el motivo" in rr.session["flash"]
n_coord = len(db.listar_notificaciones(coordtr["id"])); n_ana = len(db.listar_notificaciones(ana["id"]))
rr = req(sub, "/x", form={"decision": "PRIORIDAD", "comentario": "Corresponde"}); run(R[("POST", "/traslados/{viaje_id:int}/revision-direccion")](rr, v3))
v = tr.obtener_viaje(v3); assert v["revision_direccion"] == "PRIORIDAD" and v["revision_por"] == "Sara Subdirectora"
assert len(db.listar_notificaciones(coordtr["id"])) > n_coord and len(db.listar_notificaciones(ana["id"])) == n_ana + 1
assert any("revisión de la Dirección General" in a["texto"] for a in tr.resumen(direc, "AMBA")["alertas"]) is False, "ya resuelta"
print("C disponibilidad y revisión OK")

# ---------------------------------------------------------------- D) planificador por ubicación y unificar
lunes = tr.lunes_de(dia)
unidad = db.obtener_unidad(db.guardar_unidad(None, {"nombre": "CPF I", "zona": "AMBA Sur", "ciudad": "Ezeiza", "provincia": "Buenos Aires", "tipo": "Complejo"})) if hasattr(db, "guardar_unidad") else None
p1 = tr.crear_viaje(dict(base, destino_otro="Unidad 19 Ezeiza", hora_salida="08:00", hora_regreso="12:00"), [{"usuario_id": ana["id"], "nombre": ana["nombre"]}], ana)
p2 = tr.crear_viaje(dict(base, destino_otro="Unidad 19 Ezeiza", hora_salida="10:00", hora_regreso="14:00"), [{"usuario_id": coo["id"], "nombre": coo["nombre"]}, {"usuario_id": ana["id"], "nombre": ana["nombre"]}], coo)
p3 = tr.crear_viaje(dict(base, destino_otro="Unidad 24 Marcos Paz"), [{"usuario_id": coo["id"], "nombre": coo["nombre"]}], coo)
grupos = op.pedidos_por_ubicacion(lunes)
g19 = next(g for g in grupos if "Unidad 19 Ezeiza" in g["titulo"])
assert [v["id"] for v in g19["viajes"]] == [p1, p2] and g19["dias_unificables"] == [F]
html = R[("GET", "/traslados/planificar")](req(coordtr, "/traslados/planificar"), lunes=lunes.isoformat(), vista="").body
assert "Por ubicación del destino" in html and "Unidad 19 Ezeiza" in html and "Unificar los marcados" in html and "se pueden unificar" in html
html = R[("GET", "/traslados/planificar")](req(coordtr, "/traslados/planificar"), lunes=lunes.isoformat(), vista="propuesta").body
assert "salidas propuestas" in html and "Unificar los marcados" not in html
rr = req(coordtr, "/x", form={"lunes": lunes.isoformat(), "viaje_id": [str(p1), str(p2)]}); run(R[("POST", "/traslados/planificar/unificar")](rr))
assert f"en el viaje #{p1}" in rr.session["flash"], rr.session["flash"]
b = tr.obtener_viaje(p1); o = tr.obtener_viaje(p2)
assert o["estado"] == "CANCELADO" and o["motivo_cancelacion"] == f"Unificado con el viaje #{p1}"
nombres = sorted(p["nombre"] for p in b["participantes"])
assert nombres == sorted([ana["nombre"], coo["nombre"]]), nombres
assert b["hora_salida"] == "08:00" and b["hora_regreso"] == "14:00"
rr = req(coordtr, "/x", form={"lunes": lunes.isoformat(), "viaje_id": [str(p1), str(p3)]}); run(R[("POST", "/traslados/planificar/unificar")](rr))
assert "elegí el chofer" in rr.session["flash"]
ch3 = tr.guardar_chofer(None, {"nombre": "Tercer Chofer"}); vc = crear_v("AD789GH", 9)
rr = req(coordtr, "/x", form={"lunes": lunes.isoformat(), "viaje_id": [str(p1), str(p3)], "chofer_id": str(ch3), "vehiculo_id": str(vc)})
run(R[("POST", "/traslados/planificar/unificar")](rr))
assert "una sola salida" in rr.session["flash"] and tr.obtener_viaje(p3)["chofer_id"] == ch3 == tr.obtener_viaje(p1)["chofer_id"]
rr = req(coordtr, "/x", form={"lunes": lunes.isoformat(), "viaje_id": [str(p1)]}); run(R[("POST", "/traslados/planificar/unificar")](rr))
assert "al menos dos" in rr.session["flash"]
print("D planificador OK")
print("ALL OK")
# el formulario de carga avisa en el primer paso y ofrece la revisión en el último
rr = req(ana, "/traslados/nuevo", form={"fecha": F, "hora_salida": "10:00", "hora_regreso": "12:00", "accion": "revisar", "agentes": [str(ana["id"])]})
html = run(R[("POST", "/traslados/nuevo")](rr)).body
assert 'id="aviso-vehiculos"' in html and "ya están en uso todos los vehículos" in html and 'id="bloque-prioridad" class="bloque-prioridad" >' in html
assert "/traslados/disponibilidad?" in html
print("formulario OK")
# por defecto, todo usuario nuevo tiene los dos sistemas (el formulario viene con los dos tildados)
html = main.admin_panel(req(direc, "/admin"), "usuarios", "nuevo", **LIST).body
assert 'name="acceso_notas" value="1" checked' in html and 'name="acceso_autos" value="1" checked' in html
rr = rform(direc, {"usuario": "nuevo1", "clave": "Provisoria123", "nombre": "Nora Nueva", "rol": "ASESOR", "equipo_id": str(E1),
                   "acceso_notas": "1", "acceso_autos_enviado": "1", "acceso_autos": "1"})
run(main.admin_crear_usuario(rr))
n1 = db.obtener_usuario_por_nombre("nuevo1"); assert main._tiene_acceso_notas(n1) and main._tiene_acceso_autos(n1)
n2 = db.obtener_usuario(db.crear_usuario("nuevo2", h, s, "Néstor Importado", "ASESOR", E1))  # importación / portal
assert main._tiene_acceso_notas(n2) and main._tiene_acceso_autos(n2)
print("por defecto, los dos OK")
# detalle del viaje: quién va con chofer y quién por sus propios medios, en dos grupos claros
vm = tr.crear_viaje(dict(base, destino_otro="CPF I", hora_salida="08:00"), [{"usuario_id": ana["id"], "nombre": ana["nombre"]},
                     {"usuario_id": coo["id"], "nombre": coo["nombre"], "auto_propio": True}], ana)
html = R[("GET", "/traslados/{viaje_id:int}")](req(ana, f"/traslados/{vm}"), vm).body
i_ch, i_pm = html.index("en el vehículo institucional · 1"), html.index("(auto propio u otro transporte) · 1")
assert i_ch < html.index("Ana Pérez", i_ch) < i_pm < html.index("Cora Coord", i_pm), "Ana con chofer, Cora por sus propios medios"
assert "Cambiar a «propios medios»" in html and "Cambiar a «con chofer»" in html and "Va por sus propios medios" not in html
print("pasajeros OK")
# agenda: primero con chofer PPN, después por sus propios medios, cada grupo por equipo; arriba horario y plazas
g_ = tr.pasajeros_por_equipo(tr.obtener_viaje(vm)["participantes"])
assert [t for t, _ in g_] == ["Con chofer PPN", "Por sus propios medios"] and g_[0][1][0][1] == ["Ana Pérez"], g_
print("agenda por equipo OK")
# coordinación: primero dónde y quiénes, luego asignar; la pantalla marca la tarea pendiente principal
html_c = R[("GET", "/traslados/{viaje_id:int}")](req(direc, f"/traslados/{vm}"), vm).body
assert 'class="tarea-pendiente" href="#asignar"' in html_c and "asignar chofer y vehículo" in html_c
assert html_c.index("<dt>Dónde</dt>") < html_c.index('id="asignar"') < html_c.index('id="quienes-viajan"') < html_c.index('<h2>Repetir</h2>')
assert f'href="/traslados/asignar#dia-{tr.obtener_viaje(vm)["fecha"]}"' in html_c
assert 'tarea-principal" id="asignar"' in html_c
i_barra = html_c.index('class="barra-acciones-viaje"')
assert i_barra < html_c.index('id="d-sumar"') < html_c.index('id="d-reprogramar"')
assert html_c.index('id="d-sumar"') < html_c.index("Persona sin usuario") < html_c.index('id="d-reprogramar"') < html_c.index("Cancelar viaje")
# quien cargó el viaje (sin coordinar) no ve tarea de asignación
assert "tarea-pendiente" not in html
print("detalle para coordinación OK")

# ---------------------------------------------------------------- E) mesa de asignación
dm = dia + TD(days=7)
while dm.weekday() >= 5: dm += TD(days=1)
FM = dm.isoformat()
chA = tr.guardar_chofer(None, {"nombre": "Aldo Mesa"}); chB = tr.guardar_chofer(None, {"nombre": "Beto Mesa"})
bm = dict(fecha=FM, motivo="Monitoreo")
m1 = tr.crear_viaje(dict(bm, destino_otro="Unidad 31 Ezeiza", hora_salida="09:00", hora_regreso="12:00"), [{"usuario_id": ana["id"], "nombre": ana["nombre"]},
                     {"usuario_id": coo["id"], "nombre": coo["nombre"], "auto_propio": True}], ana)
m2 = tr.crear_viaje(dict(bm, destino_otro="Unidad 31 Ezeiza", hora_salida="10:00", hora_regreso="14:00"), [{"nombre": "Pedro Externo"}], coo)
m3 = tr.crear_viaje(dict(bm, destino_otro="CPF II Marcos Paz", hora_salida="09:30", hora_regreso="13:00"), [{"nombre": "Laura Externa"}], coo)
m4 = tr.crear_viaje(dict(bm, destino_otro="Devoto", hora_salida="09:00", hora_regreso="11:00"), [{"nombre": "Sara Sola"}], coo)
mesa = op.mesa_asignacion()
dmesa = next(d for d in mesa["dias"] if d["fecha"] == FM)
filas = {v["id"]: v for g in dmesa["lugares"] for v in g["viajes"]}
assert set(filas) >= {m1, m2, m3, m4}
assert [p["nombre"] for p in filas[m1]["en_auto"]] == ["Ana Pérez"] and filas[m1]["por_su_cuenta"] == 1, "solo quienes necesitan auto"
assert any(c["id"] == chA for c in dmesa["choferes"]) and any(x["id"] == vb for x in dmesa["vehiculos"])
html = R[("GET", "/traslados/asignar")](req(coordtr, "/traslados/asignar")).body
i = html.index(f'id="dia-{FM}"')
assert "Ana Pérez" in html[i:] and "+ 1 por sus propios medios" in html[i:] and f'name="viaje_id" value="{m3}"' in html[i:]
assert 'data-mesa-dia' in html and "/static/mesa_asignacion.js" in html and "Asignar choferes" in html
falla(lambda: R[("GET", "/traslados/asignar")](req(ana, "/traslados/asignar")), 403)
# asignar 3 pedidos juntos: los 2 al mismo destino se unifican; el otro queda como parada de la misma salida
rr = req(coordtr, "/traslados/asignar", form={"fecha": FM, "viaje_id": [str(m1), str(m2), str(m3)], "chofer_id": str(chA), "vehiculo_id": str(vb)})
r = run(R[("POST", "/traslados/asignar")](rr))
assert r.headers["location"] == f"/traslados/asignar#dia-{FM}"
assert rr.session["flash"].startswith("Listo: Aldo Mesa con el AC456EF lleva 3 personas (3 pedidos, 1 unificado al mismo destino, en una sola salida)"), rr.session["flash"]
a1, a2, a3 = tr.obtener_viaje(m1), tr.obtener_viaje(m2), tr.obtener_viaje(m3)
assert a1["estado"] == "CONFIRMADO" and a1["chofer_id"] == chA and a1["vehiculo_id"] == vb and a1["hora_regreso"] == "14:00"
assert a2["estado"] == "CANCELADO" and a2["unificado_en"] == m1 and a3["estado"] == "CONFIRMADO" and a3["chofer_id"] == chA
assert not any("conflicto" in h["accion"] for h in tr.historial("viaje", m3)), "las paradas de la misma salida no son conflicto"
dmesa = next(d for d in op.mesa_asignacion()["dias"] if d["fecha"] == FM)
quedan = {v["id"] for g in dmesa["lugares"] for v in g["viajes"]}
assert m4 in quedan and not quedan & {m1, m2, m3}, "lo asignado sale de la lista"
ocA = next(c for c in dmesa["choferes"] if c["id"] == chA)["ocupado"]
assert {f["viaje"] for f in ocA} == {m1, m3}, ocA
# avisos: el mismo chofer en el mismo horario → pide aceptar; capacidad; días distintos
rr = req(coordtr, "/x", form={"fecha": FM, "viaje_id": [str(m4)], "chofer_id": str(chA), "vehiculo_id": ""}); run(R[("POST", "/traslados/asignar")](rr))
assert rr.session["flash"].startswith("⚠ Conflicto: el chofer Aldo Mesa ya tiene el viaje") and tr.obtener_viaje(m4)["estado"] == "SOLICITADO"
m5 = tr.crear_viaje(dict(bm, destino_otro="Devoto", hora_salida="15:00", hora_regreso="17:00"), [{"nombre": f"P{i}"} for i in range(5)], coo)
try: op.asignar_seleccion([m4, m5], chB, va, coordtr); raise AssertionError
except ErrorTraslado as e: assert "Juntos son 6 personas" in str(e)
assert tr.obtener_viaje(m4)["estado"] == "SOLICITADO" and tr.obtener_viaje(m5)["estado"] == "SOLICITADO", "no se toca nada si hay error"
try: op.asignar_seleccion([m4, v1], chB, None, coordtr); raise AssertionError
except ErrorTraslado as e: assert "mismo día" in str(e)
rr = req(coordtr, "/x", form={"fecha": FM, "viaje_id": [str(m4)], "chofer_id": str(chA), "forzar": "1"}); run(R[("POST", "/traslados/asignar")](rr))
assert "Se aceptó el aviso" in rr.session["flash"] and tr.obtener_viaje(m4)["chofer_id"] == chA
# sin límite de días: un pedido a más de 30 días también aparece
lejos = D.today() + TD(days=75)
while lejos.weekday() >= 5: lejos += TD(days=1)
ml = tr.crear_viaje(dict(fecha=lejos.isoformat(), motivo="Monitoreo", destino_otro="Unidad 6 Rawson", hora_salida="09:00", hora_regreso="18:00"), [{"nombre": "Lejana Persona"}], coo)
assert any(v["id"] == ml for d in op.mesa_asignacion()["dias"] for g in d["lugares"] for v in g["viajes"]), "todos los pendientes, aunque falten más de 30 días"
print("mesa de asignación OK")

# ---------------------------------------------------------------- F) ficha del vehículo: combustible, vales, novedades y km
import flota
vf = crear_v("AF111ZZ", 5); tr.guardar_vehiculo(vf, {"km": "1000"})
html = R[("GET", "/traslados/flota")](req(coordtr, "/traslados/flota")).body
assert 'class="vista vista--activa" href="/traslados/flota">Panel de flota</a>' in html and html.index("Tablero del día") < html.index('href="/traslados/flota">Panel de flota'), "Panel de flota en la barra, al lado de Tablero del día"
assert f'href="/traslados/vehiculos/{vf}?cargar=combustible#cargar"' in html
Rr = R[("POST", "/traslados/vehiculos/{vid:int}/registro")]
rr = req(coordtr, "/x", form={"tipo": "COMBUSTIBLE", "fecha": D.today().isoformat(), "litros": "42,5", "importe": "51000", "km": "1250", "detalle": "YPF ticket 123"})
r = run(Rr(rr, vf)); assert rr.session["flash"] == "Carga de combustible registrada." and r.headers["location"].endswith("#registros")
assert tr.listar_vehiculos(incluir_baja=True) and next(x for x in tr.listar_vehiculos() if x["id"] == vf)["km"] == 1250, "el km de la carga actualiza el vehículo"
rr = req(coordtr, "/x", form={"tipo": "VALES", "fecha": D.today().isoformat(), "cantidad": "4", "chofer_id": str(ch2)}); run(Rr(rr, vf))
rr = req(coordtr, "/x", form={"tipo": "NOVEDAD", "fecha": D.today().isoformat(), "gravedad": "GRAVE", "detalle": "Pérdida de líquido de frenos"}); run(Rr(rr, vf))
assert "se avisó a la coordinación" in rr.session["flash"]
res = flota.resumen_registros(vf)
assert res["litros_mes"] == 42.5 and res["vales_mes"] == 4 and len(res["novedades_abiertas"]) == 1
# errores: km menor, litros vacíos, fecha futura
rr = req(coordtr, "/x", form={"tipo": "COMBUSTIBLE", "litros": "10", "km": "900"}); r = run(Rr(rr, vf))
assert "no puede ser menor" in rr.session["flash"] and "cargar=combustible" in r.headers["location"]
rr = req(coordtr, "/x", form={"tipo": "COMBUSTIBLE", "litros": ""}); run(Rr(rr, vf)); assert "cuántos litros" in rr.session["flash"]
rr = req(coordtr, "/x", form={"tipo": "NOVEDAD", "fecha": (D.today() + TD(days=2)).isoformat(), "detalle": "x"}); run(Rr(rr, vf)); assert "futura" in rr.session["flash"]
# ficha: la sección Cargar, las novedades abiertas y la tabla
html = R[("GET", "/traslados/vehiculos/{vid:int}")](req(coordtr, f"/traslados/vehiculos/{vf}"), vf).body
for t in ("Carga de combustible", "Vales entregados", "Novedad mecánica", "Actualizar kilometraje", "Novedades mecánicas sin resolver (1)", "Pérdida de líquido de frenos", "YPF ticket 123"):
    assert t in html, t
# estados de la novedad: cargada → reportada → solicitada → en reparación (vehículo a taller) → reparada (vuelve a disponible)
rid = res["novedades_abiertas"][0]["id"]
assert res["novedades_abiertas"][0]["estado_novedad"] == "CARGADA" and "Cambiar estado" in html and "Reportada al área responsable" in html
Re = R[("POST", "/traslados/vehiculos/registro/{rid:int}/estado")]
rr = req(coordtr, "/x", form={"estado": "REPORTADA", "nota": "Mail a Servicios Generales"}); r = run(Re(rr, rid)); assert r.headers["location"].endswith("#novedades")
rr = req(coordtr, "/x", form={"estado": "REPARACION_SOLICITADA", "nota": "Pedido 4512"}); run(Re(rr, rid))
rr = req(coordtr, "/x", form={"estado": "EN_REPARACION", "nota": "Taller Pérez", "vehiculo_estado": "TALLER"}); run(Re(rr, rid))
assert "En taller" in rr.session["flash"] and next(x for x in tr.listar_vehiculos(incluir_baja=True) if x["id"] == vf)["estado"] == "TALLER"
assert flota.panel()[0] is not None and any(x["id"] == vf and x["novedades_en_reparacion"] == 1 for x in flota.panel())
rr = req(coordtr, "/x", form={"estado": "DESESTIMADA"}); run(Re(rr, rid)); assert "por qué" in rr.session["flash"], "no requiere: hay que contar por qué"
falla(lambda: run(Re(req(ana, "/x", form={"estado": "REPARADA"}), rid)), 403)
html = R[("GET", "/traslados/vehiculos/{vid:int}")](req(coordtr, f"/traslados/vehiculos/{vf}"), vf).body
assert 'aria-current="step">En reparación' in html and "Pedido 4512" in html and "Volver el vehículo a «Disponible»" in html
rr = req(coordtr, "/x", form={"estado": "REPARADA", "nota": "Se cambió el flexible", "vehiculo_estado": "DISPONIBLE"}); run(Re(rr, rid))
assert not flota.resumen_registros(vf)["novedades_abiertas"] and next(x for x in tr.listar_vehiculos() if x["id"] == vf)["estado"] == "DISPONIBLE"
assert [h["estado"] for h in flota.historial_novedades(vf)[rid]] == ["CARGADA", "REPORTADA", "REPARACION_SOLICITADA", "EN_REPARACION", "REPARADA"]
assert next(r for r in flota.registros(vf) if r["id"] == rid)["estado_etiqueta"] == "Reparada"
# reabrir (volvió a fallar) y el atajo «resuelta» de antes siguen andando
flota.cambiar_estado_novedad(rid, "REPORTADA", "Volvió a perder líquido", coordtr); assert len(flota.resumen_registros(vf)["novedades_abiertas"]) == 1
# resolver
rr = req(coordtr, "/x", form={"resolucion": "Se cambió el flexible"}); run(R[("POST", "/traslados/vehiculos/registro/{rid:int}/resuelta")](rr, rid))
assert not flota.resumen_registros(vf)["novedades_abiertas"]
# permisos: un asesor no carga; los vales solo la coordinación
falla(lambda: run(Rr(req(ana, "/x", form={"tipo": "COMBUSTIBLE", "litros": "5"}), vf)), 403)
print("ficha del vehículo: cargas OK")

# ---------------------------------------------------------------- G) tablero de Dirección, entrevistas y metas
import tablero_direccion as td
E2 = db.crear_equipo("Equipo Género"); gina = U("gina", "Gina Género", "ASESOR", E2); hugo = U("hugo", "Hugo Género", "ASESOR", E2)
ana_eq = db.obtener_usuario(ana["id"])["equipo_nombre"] if db.obtener_usuario(ana["id"]).get("equipo_nombre") else None
ayer = D.today() - TD(days=1)
while ayer.weekday() >= 5: ayer -= TD(days=1)
uni = db.listar_unidades()[0]
pv = tr.crear_viaje(dict(fecha=ayer.isoformat(), motivo="Monitoreo", unidad_id=uni["id"], hora_salida="09:00", hora_regreso="13:00"),
                    [{"usuario_id": gina["id"], "nombre": gina["nombre"]}, {"usuario_id": hugo["id"], "nombre": hugo["nombre"], "auto_propio": True}], gina)
with db._conexion() as con: con.execute("UPDATE viajes SET estado = 'CONFIRMADO' WHERE id = ?", (pv,))
# quién informa entrevistas: lo marca la Dirección (individual, en lote o por equipo)
rr = req(direc, "/x", form={"entrevistas": "1"}); rr.headers = {"referer": "http://x/admin/equipos/listado"}
run(main.admin_equipo_permisos(rr, E2))
assert db.obtener_usuario(gina["id"])["carga_entrevistas"] and db.obtener_usuario(hugo["id"])["carga_entrevistas"]
# pendientes y aviso
assert [v["id"] for v in td.entrevistas_pendientes(gina["id"])] == [pv]
assert any("audiencias" in a["texto"] for a in tr.resumen(db.obtener_usuario(gina["id"]), "AMBA")["alertas"])
html = R[("GET", "/traslados/entrevistas")](req(gina, "/traslados/entrevistas")).body
assert f'action="/traslados/{pv}/entrevistas"' in html
pg = next(p for p in tr.obtener_viaje(pv)["participantes"] if p["usuario_id"] == gina["id"]); ph = next(p for p in tr.obtener_viaje(pv)["participantes"] if p["usuario_id"] == hugo["id"])
rr = req(gina, "/x", form={f"p_{pg['id']}": "7", "volver": "/traslados/entrevistas"}); r = run(R[("POST", "/traslados/{viaje_id:int}/entrevistas")](rr, pv))
assert rr.session["flash"] == "Audiencias guardadas." and r.headers["location"] == "/traslados/entrevistas"
rr = req(gina, "/x", form={f"p_{ph['id']}": "3"}); run(R[("POST", "/traslados/{viaje_id:int}/entrevistas")](rr, pv))
assert "tus propias" in rr.session["flash"], "cada persona carga lo suyo"
rr = req(gina, "/x", form={f"p_{pg['id']}": "abc"}); run(R[("POST", "/traslados/{viaje_id:int}/entrevistas")](rr, pv)); assert "número" in rr.session["flash"]
assert not td.entrevistas_pendientes(gina["id"]) and td.entrevistas_pendientes(hugo["id"])
html = R[("GET", "/traslados/{viaje_id:int}")](req(hugo, f"/traslados/{pv}"), pv).body
assert f'name="p_{ph["id"]}"' in html and f'name="p_{pg["id"]}"' not in html and "7 audiencia(s)" in html
# metas: mínimo anual repartido en trimestres
assert td.repartir(10) == [3, 3, 2, 2] and td.repartir(0) == [0, 0, 0, 0]
rr = req(direc, "/x", form={"anio": str(D.today().year), f"anual_{E2}": "8"}); run(R[("POST", "/traslados/direccion/metas")](rr))
assert rr.session["flash"].startswith("Metas") and td.metas(D.today().year)[E2] == {1: 2, 2: 2, 3: 2, 4: 2, "anual": 8}
rr = req(direc, "/x", form={"anio": str(D.today().year), f"t1_{E2}": "1", f"t2_{E2}": "x"}); run(R[("POST", "/traslados/direccion/metas")](rr)); assert "enteros" in rr.session["flash"]
falla(lambda: run(R[("POST", "/traslados/direccion/metas")](req(coordtr, "/x", form={"anio": "2026"}))), 403)
av = next(f for f in td.avance_metas(D.today().year) if f["equipo_id"] == E2)
assert av["trimestres"][td.trimestre_de(ayer) - 1]["hecho"] >= 1, av
# tablero
t_ = td.tablero(*td.rango("anio")[:2])
fg = next(f for f in t_["equipos"] if f["equipo"] == "Equipo Género")
assert fg["viajes"] == 1 and fg["personas"] == 2 and fg["entrevistas"] == 7 and fg["pendientes_entrevistas"] == 1 and fg["pct_institucional"] == 50
html = R[("GET", "/traslados/direccion")](req(direc, "/traslados/direccion"), periodo="anio").body
for txt in ("Tablero de Dirección", "Realizados / programados", "Cobertura territorial", "Alertas operativas activas", "Pendientes de asignar",
            "Supervisión por equipo", "Equipo Género", "Territorio", "Metas de viajes", "Fijar metas", "/traslados/direccion.xlsx?periodo=anio"):
    assert txt in html, txt
html = R[("GET", "/traslados/direccion")](req(direc, "/x"), periodo="anio", ver_equipo="Equipo Género").body
assert "Detalle: Equipo Género" in html and "Gina Género" in html and "Hugo Género" in html
html = R[("GET", "/traslados/direccion")](req(coordtr, "/x"), periodo="anio", equipo="Equipo Género").body
assert "Fijar metas" not in html, "la coordinación ve el tablero; las metas las fija la Dirección"
x = R[("GET", "/traslados/direccion.xlsx")](req(direc, "/x"), periodo="anio")
import openpyxl as _ox, io as _io
lb = _ox.load_workbook(_io.BytesIO(x.body)); assert lb.sheetnames == ["Resumen", "Equipos", "Territorio", "Metas", "Detalle"]
assert any(r[0].value == "Equipo Género" and r[4].value == 7 for r in lb["Equipos"].iter_rows(min_row=2))
falla(lambda: R[("GET", "/traslados/direccion")](req(ana, "/x")), 403)
# áreas sin meta ni integrantes habilitados en Autos PPN no se muestran
E_oc = db.crear_equipo("Equipo Oculto"); oc = U("ocul", "Olga Oculta", "ASESOR", E_oc)
with db._conexion() as con: con.execute("UPDATE usuarios SET acceso_traslados = 0 WHERE id = ?", (oc["id"],))
E_meta = db.crear_equipo("Equipo Con Meta"); td.guardar_metas(D.today().year, {E_meta: [1, 1, 1, 1]}, direc)
vis = {e["nombre"] for e in td.equipos_visibles()}
assert "Equipo Oculto" not in vis and "Equipo Con Meta" in vis and "Equipo Género" in vis, vis
with db._conexion() as con:
    con.execute("INSERT INTO viaje_participantes (viaje_id, nombre, equipo_nombre) VALUES (?,?,?)", (pv, "Olga Oculta", "Equipo Oculto"))
t_ = td.tablero(*td.rango("anio")[:2])
assert "Equipo Oculto" not in [f["equipo"] for f in t_["equipos"]] and t_["equipos_ocultos"] == 1
html = R[("GET", "/traslados/direccion")](req(direc, "/x"), periodo="anio").body
assert "<option>Equipo Oculto</option>" not in html and "<option>Equipo Con Meta</option>" in html and "No se muestran 1 área(s)" in html
assert 'aria-label="Mínimo anual de Equipo Oculto"' not in html
with db._conexion() as con: con.execute("DELETE FROM viaje_participantes WHERE nombre = 'Olga Oculta'")
print("tablero de Dirección OK")

# ---- equipos del historial migrado: vincular a equipos del sistema (se conserva el nombre original y se puede deshacer)
with db._conexion() as con:
    vid = con.execute("""INSERT INTO viajes (fecha, unidad_id, modalidad, estado, inmediato, origen, creado_por, creado_en, actualizado_en)
                         VALUES ('2025-06-02', NULL, 'CHOFER', 'CONFIRMADO', 0, 'IMPORTADO', 'Importación', '2025-06-01', '2025-06-01')""").lastrowid
    con.execute("UPDATE viajes SET destino_otro = 'Destino histórico' WHERE id = ?", (vid,))
    for nom, eq in (("Hist Uno", "SALUD"), ("Hist Dos", "SALUD"), ("Hist Tres", "Prog. Marcos Paz")):
        con.execute("INSERT INTO viaje_participantes (viaje_id, nombre, equipo_nombre) VALUES (?,?,?)", (vid, nom, eq))
pend, vinc = tr.equipos_del_historial()
nombres = {p["nombre"]: p for p in pend}
assert "SALUD" in nombres and nombres["SALUD"]["personas"] == 2 and "Equipo Salud" not in nombres, nombres.keys()
assert nombres["SALUD"]["sugerido"] and nombres["SALUD"]["sugerido"]["id"] == E1, "sugiere el equipo parecido"
html = R[("GET", "/traslados/equipos-historial")](req(coordtr, "/x")).body
assert "Equipos del historial de viajes" in html and "Prog. Marcos Paz" in html and "(sugerido)" in html
falla(lambda: R[("GET", "/traslados/equipos-historial")](req(ana, "/x")), 403)
i = list(nombres).index("SALUD")
r = run(R[("POST", "/traslados/equipos-historial")](req(coordtr, "/x", form={"nombre": list(nombres), f"equipo_{i}": str(E1)})))
with db._conexion() as con:
    filas = [dict(f) for f in con.execute("SELECT nombre, equipo_nombre, equipo_original FROM viaje_participantes WHERE viaje_id = ? ORDER BY id", (vid,))]
assert filas[0]["equipo_nombre"] == "Equipo Salud" and filas[0]["equipo_original"] == "SALUD" and filas[2]["equipo_nombre"] == "Prog. Marcos Paz", filas
pend, vinc = tr.equipos_del_historial()
assert "SALUD" not in {p["nombre"] for p in pend} and vinc[0]["alias"] == "SALUD" and vinc[0]["personas"] == 2
assert tr.equipo_por_alias("salud")["id"] == E1, "las próximas importaciones lo reconocen"
run(R[("POST", "/traslados/equipos-historial/deshacer")](req(coordtr, "/x", form={"alias": "SALUD"})))
with db._conexion() as con:
    assert con.execute("SELECT equipo_nombre FROM viaje_participantes WHERE viaje_id = ? ORDER BY id", (vid,)).fetchone()[0] == "SALUD", "deshacer vuelve al original"
assert tr.equipo_por_alias("SALUD") is None
print("equipos del historial OK")
