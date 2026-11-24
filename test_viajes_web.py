"""Autos PPN y Metas, propuestas 54 a 97 (pantallas): permisos por rol, formularios y descargas de las mejoras del área."""
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
import main, auth, nucleo, soberania, respaldo, automatico, control_cambios as cc
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import types, asyncio, json, shutil, socket, io, re
from datetime import date, datetime, timedelta, timezone

h, s = auth.generar_hash_clave("Clave1234")
E1, E2 = db.crear_equipo("Equipo Salud"), db.crear_equipo("Equipo CPF I")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, beto, coo, caro = U("ana", "Ana Pérez", "ASESOR", E1), U("beto", "Beto Ruiz", "ASESOR", E1), U("coo", "Cora Coord", "COORDINADOR", E1), U("caro", "Caro CPF", "ASESOR", E2)
cid = db.crear_usuario("cch", h, s, "Coord Choferes", "ASESOR", E2); db.actualizar_usuario(cid, "Coord Choferes", "ASESOR", E2, coordina_traslados=True)
cch = db.obtener_usuario(cid)
chofer = U("chof", "Pedro Chofer", "CHOFER")
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
def req(u, p="/", ses=None):
    async def _f(): return {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session=ses if ses is not None else {},
                                 cookies={}, headers={}, query_params={}, form=_f, client=None)
R = main.app.rutas
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v not in (None, "") else [])
class Archivo:
    def __init__(self, nombre, contenido): self.filename, self._c = nombre, contenido
    async def read(self, n=-1): return self._c if n is None or n < 0 else self._c[:n]
def reqf(u, form, p="/x", q=None):
    r = req(u, p)
    async def _f(): return Form(form)
    r.form = _f; r.query_params = q or {}; return r
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def body(r): return r.body.decode() if isinstance(r.body, bytes) else r.body
def falla(fn, codigo):
    try: run(fn())
    except main.HTTPException as e: assert e.status_code == codigo, (e.status_code, e.detail); return
    raise AssertionError(f"se esperaba {codigo}")
def post(u, ruta, form, *a):
    rr = reqf(u, form); r = run(R[("POST", ruta)](rr, *a)); return rr.session.get("flash"), r
hoy = date.today()
import traslados as tr, operacion as op, metas, tablero_direccion as td, flota

# ---------------------------------------------------------------- propuesta 69: feriados
falla(lambda: R[("GET", "/traslados/feriados")](req(ana)), 403)
falla(lambda: R[("POST", "/traslados/feriados")](reqf(ana, {"fecha": "2026-07-09", "motivo": "Independencia"})), 403)
fl, r = post(cch, "/traslados/feriados", {"fecha": "2026-07-09", "motivo": "Día de la Independencia", "tipo": "NACIONAL"})
assert fl == "Día no laborable guardado." and r.headers["location"] == "/traslados/feriados?anio=2026"
fl, _ = post(direc, "/traslados/feriados", {"fecha": "2026-07-10", "motivo": "x"}); assert fl.startswith("⚠") and "motivo" in fl
html = body(R[("GET", "/traslados/feriados")](req(direc), anio=2026))
assert "Día de la Independencia" in html and "Feriado nacional" in html and 'action="/traslados/feriados/importar"' in html
ics = b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;VALUE=DATE:20261208\nSUMMARY:Inmaculada Concepci\xc3\xb3n\nEND:VEVENT\nEND:VCALENDAR"
fl, _ = post(cch, "/traslados/feriados/importar", {"archivo": Archivo("f.ics", ics), "tipo": "NACIONAL"})
assert fl == "Calendario importado: 1 día(s) nuevo(s), 0 ya estaban.", fl
falla(lambda: R[("POST", "/traslados/feriados/importar")](reqf(ana, {"archivo": Archivo("f.ics", ics)})), 403)
x = R[("GET", "/traslados/feriados.ics")](req(direc), anio=2026)
assert b"DTSTART;VALUE=DATE:20261208" in x.body and "text/calendar" in x.media_type
from dgpdh.servicios import feriados as srv_fer
with db._conexion() as con: fid = next(f["id"] for f in srv_fer.listar(con, 2026) if f["fecha"] == "2026-12-08")
fl, _ = post(cch, "/traslados/feriados/{fid:int}/baja", {"motivo": ""}, fid); assert fl.startswith("⚠")
fl, _ = post(cch, "/traslados/feriados/{fid:int}/baja", {"motivo": "Cargado por error"}, fid); assert fl.startswith("Feriado dado de baja")
assert "Inmaculada" not in body(R[("GET", "/traslados/feriados")](req(direc), anio=2026))
assert "Cargado por error" in body(R[("GET", "/traslados/feriados")](req(direc), anio=2026, bajas="1"))
assert tr.feriados_iso() == {"2026-07-09"}
print("69 feriados OK")

# ---------------------------------------------------------------- propuestas 72 y 73: en la unidad y constancia
import rutas_viajes
tr.sincronizar_choferes()
CH = {c["usuario_id"]: c["id"] for c in tr.listar_choferes()}
ch_id = CH[chofer["id"]]
veh = tr.guardar_vehiculo(None, {"dominio": "AB123CD", "capacidad": "6", "marca": "Renault"})
amba = [u_ for u_ in db.listar_unidades() if tr.es_amba(u_["id"])]
hoy_l = rutas_viajes.ahora_local().date()
def P(u, auto=0): return {"usuario_id": u["id"], "nombre": u["nombre"], "equipo_nombre": db.obtener_usuario(u["id"]).get("equipo_nombre") or "Equipo Salud", "auto_propio": auto}
vch = tr.crear_viaje({"fecha": hoy_l.isoformat(), "hora_salida": "08:00", "hora_regreso": "14:00", "unidad_id": amba[0]["id"]}, [P(ana)], ana,
                     hoy=hoy_l - timedelta(days=10))
tr.asignar(vch, ch_id, veh, cch, forzar=True)
assert tr.obtener_viaje(vch)["estado"] == "CONFIRMADO"
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/parada/{evento}")](reqf(beto, {}), vch, "llegada"), 403)
fl, r = post(chofer, "/traslados/{viaje_id:int}/parada/{evento}", {"volver": "/traslados/hoy"}, vch, "llegada")
assert fl.startswith("Llegamos a la unidad: ") and r.headers["location"] == "/traslados/hoy", fl
fl, _ = post(chofer, "/traslados/{viaje_id:int}/parada/{evento}", {"hora": "03:00"}, vch, "llegada")
assert fl.startswith("⚠") and "ya se marcó" in fl, "solo la coordinación cambia una hora ya marcada"
fl, r = post(ana, "/traslados/{viaje_id:int}/parada/{evento}", {"volver": "https://otro.sitio/"}, vch, "ingreso")
assert r.headers["location"] == f"/traslados/{vch}#en-la-unidad", "no redirige afuera"
fl, _ = post(cch, "/traslados/{viaje_id:int}/parada/{evento}", {"hora": "00:01", "correccion": "1"}, vch, "llegada"); assert fl == "Llegamos a la unidad: 00:01.", fl
v_ = tr.obtener_viaje(vch); assert v_["llegada_unidad"] == "00:01" and v_["ingreso_unidad"]
html = body(R[("GET", "/traslados/hoy")](req(chofer)))
assert 'action="/traslados/%d/parada/salida"' % vch in html and 'action="/traslados/%d/parada/llegada"' % vch not in html
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(ana, f"/traslados/{vch}"), vch))
assert 'id="en-la-unidad"' in html and "Confirmo que el viaje se hizo" in html and "Corregir una hora" not in html
assert "Corregir una hora" in body(R[("GET", "/traslados/{viaje_id:int}")](req(cch, f"/traslados/{vch}"), vch))
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/constancia")](reqf(beto, {}), vch), 403)
fl, _ = post(ana, "/traslados/{viaje_id:int}/constancia", {}, vch); assert fl.startswith("Gracias"), fl
fl, _ = post(ana, "/traslados/{viaje_id:int}/constancia", {}, vch); assert fl == "El viaje ya tenía constancia."
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(ana, f"/traslados/{vch}"), vch))
assert "lo confirmó quien viajó" in html and "Confirmo que el viaje se hizo" not in html
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/parada/{evento}")](reqf(ana, {}), 999999, "llegada"), 404)
print("72-73 en la unidad y constancia OK")

# ---------------------------------------------------------------- propuesta 79: demora para ingresar
pasado = hoy_l - timedelta(days=5)
v79 = tr.crear_viaje({"fecha": pasado.isoformat(), "unidad_id": amba[1]["id"]}, [P(ana, 1)], ana, hoy=pasado - timedelta(days=10))
op.agregar_novedad(v79, "DEMORA_INGRESO", "", "70", "Ana")
with db._conexion() as con:
    con.execute("""INSERT INTO obstaculizaciones (numero, anio, correlativo, fecha_hecho, hora_hecho, unidad_id, tipo_id, descripcion, subsanado,
                   demora_minutos, cargado_por_id, cargado_por, creado_en) VALUES ('O-9', 2026, 9, ?, '10:00', ?, 1, 'x', 1, 200, 1, 'x', 'x')""",
                (pasado.isoformat(), amba[1]["id"]))
falla(lambda: R[("GET", "/traslados/ingreso-unidades.csv")](req(ana)), 403)
x = R[("GET", "/traslados/ingreso-unidades.csv")](req(cch), unidad_id=amba[1]["id"])
txt = x.body.decode("utf-8")
assert txt.startswith("﻿Unidad;Mes;Visitas") and ";70;1" in txt, txt  # la coordinación no ve el Registro: solo la novedad
x = R[("GET", "/traslados/ingreso-unidades.csv")](req(direc), unidad_id=amba[1]["id"])
assert ";200;1" in x.body.decode("utf-8"), x.body  # la Dirección ve también el Registro de Obstaculizaciones
falla(lambda: R[("GET", "/traslados/ingreso-unidades.csv")](req(direc), desde="ayer"), 400)
html = body(R[("GET", "/datos/unidad")](req(direc), unidad_id=str(amba[1]["id"])))
assert "Ingreso a la unidad: obstáculos y demoras" in html and "Descargar (CSV)" in html
html = body(R[("GET", "/traslados/direccion")](req(cch, "/traslados/direccion"), periodo="personalizado",
                                               desde=(pasado - timedelta(days=1)).isoformat(), hasta=hoy_l.isoformat()))
assert "Ingreso a las unidades: obstáculos al monitoreo" in html and amba[1]["nombre"] in html
print("79 demora de ingreso OK")

# ---------------------------------------------------------------- propuesta 80: no se borra nada (licencias)
op.agregar_licencia(ch_id, (hoy_l + timedelta(days=30)).isoformat(), (hoy_l + timedelta(days=31)).isoformat(), "FRANCO", "", "Coord")
lic = op.licencias(chofer_id=ch_id)[0]
falla(lambda: R[("POST", "/traslados/licencias/{lic_id:int}/borrar")](reqf(ana, {}), lic["id"]), 403)
fl, _ = post(cch, "/traslados/licencias/{lic_id:int}/borrar", {"motivo": "Error de carga"}, lic["id"]); assert fl.startswith("Licencia quitada (queda"), fl
html = body(R[("GET", "/traslados/licencias")](req(cch)))
assert "Licencias quitadas (1)" in html and "Error de carga" in html and f'/traslados/licencias/{lic["id"]}/recuperar' in html
fl, _ = post(cch, "/traslados/licencias/{lic_id:int}/recuperar", {}, lic["id"]); assert fl == "Licencia recuperada.", fl
fl, _ = post(cch, "/traslados/licencias/{lic_id:int}/recuperar", {}, lic["id"]); assert "no está entre las quitadas" in fl
print("80 no se borra nada OK")

# ---------------------------------------------------------------- propuesta 82: una sola meta de visitas
from dgpdh.persistencia import migraciones as mg
anio = hoy_l.year
with db._conexion() as con:
    for t_, m_ in enumerate((2, 2, 2, 2), 1):
        con.execute("INSERT INTO metas_viajes (equipo_id, anio, trimestre, minimo) VALUES (?, ?, ?, ?)", (E2, anio, t_, m_))
    con.execute("INSERT INTO metas_anuales (equipo_id, anio, indicador, meta) VALUES (?, ?, 'visitas_equipo', 20)", (E2, anio))
    next(m_ for m_ in mg.disponibles() if m_.numero == 23).aplicar(con)
html = body(R[("GET", "/traslados/direccion")](req(direc, "/traslados/direccion"), periodo="anio"))
assert "Antes había dos metas de visitas" in html and "Queda 8 (tablero)" in html and "Queda 20 (Metas)" in html
assert "Antes había dos metas" not in body(R[("GET", "/traslados/direccion")](req(cch, "/traslados/direccion"), periodo="anio")), "solo la Dirección elige"
falla(lambda: R[("POST", "/traslados/direccion/metas/unificar")](reqf(cch, {"equipo_id": str(E2), "anio": str(anio), "eleccion": "TABLERO"})), 403)
fl, _ = post(direc, "/traslados/direccion/metas/unificar", {"equipo_id": str(E2), "anio": str(anio), "eleccion": "ANUAL"}); assert fl.startswith("Listo"), fl
fl, _ = post(direc, "/traslados/direccion/metas/unificar", {"equipo_id": str(E2), "anio": str(anio), "eleccion": "ANUAL"}); assert "ya se resolvió" in fl
assert td.metas(anio)[E2]["anual"] == 20 and metas.metas_anuales(anio)[E2]["visitas_equipo"] == 20
fl, _ = post(direc, "/traslados/direccion/metas", {"anio": str(anio), f"anual_{E2}": "12"}); assert fl.startswith("Metas")
assert metas.metas_anuales(anio)[E2]["visitas_equipo"] == 12, "lo que se fija en el tablero es la meta de Metas"
print("82 una sola meta de visitas OK")

# ---------------------------------------------------------------- propuestas 83, 89 y 91: panel de metas
metas.guardar_metas_anuales(anio, {E1: {"monitoreos": 24, "visitas_equipo": 10}}, direc)
with db._conexion() as con:  # la foto del día ya estaba armada: se la envejece para que se recalcule con los viajes de la prueba
    con.execute("UPDATE metas_foto SET generado_en = '2000-01-01T00:00:00+00:00'")
html = body(R[("GET", "/metas")](req(coo, "/metas"), anio=str(anio), equipo_id=str(E1)))
for txt in ("Al ritmo actual", "Para llegar", "Carga de lo realizado:") + (("sin carga", "Calidad de las visitas") if hoy_l.month > 1 else ()):
    assert txt in html, txt
html = body(R[("GET", "/metas")](req(direc, "/metas"), anio=str(anio)))
assert "Completitud" in html
print("83-89-91 panel de metas OK")

# ---------------------------------------------------------------- propuesta 57: horarios de ingreso
U57 = amba[2]["id"]
falla(lambda: R[("GET", "/unidades/{uid:int}/ventanas")](req(cch), U57), 403)
falla(lambda: R[("POST", "/unidades/{uid:int}/ventanas")](reqf(cch, {"dias": ["1"], "desde": "09:00", "hasta": "12:00"}), U57), 403)
fl, _ = post(direc, "/unidades/{uid:int}/ventanas", {"dias": [str(d) for d in range(7) if d != (hoy_l + timedelta(days=20)).weekday()],
                                                    "desde": "09:00", "hasta": "12:00", "tipo": "AUDIENCIAS", "vigente_desde": "2020-01-01"}, U57)
assert fl == "Horario de ingreso guardado.", fl
fl, _ = post(direc, "/unidades/{uid:int}/ventanas", {"dias": [], "desde": "09:00", "hasta": "12:00"}, U57); assert fl.startswith("⚠")
html = body(R[("GET", "/unidades/{uid:int}/ventanas")](req(direc), U57)); assert "09:00–12:00" in html and "Dar de baja" in html
f57 = (hoy_l + timedelta(days=20)).isoformat()
rr = reqf(ana, Form({"fecha": f57, "unidad_id": str(U57), "hora_salida": "08:00", "hora_regreso": "13:00", "agentes": [str(ana["id"])], f"auto_{ana['id']}": "1"}))
r = run(R[("POST", "/traslados/nuevo")](rr))
assert "Atención: La unidad no tiene horario de ingreso" in rr.session["flash"], rr.session.get("flash")
v57 = int(r.headers["location"].rsplit("/", 1)[-1])
assert tr.obtener_viaje(v57)["estado"] == "CONFIRMADO", "el aviso no bloquea"
assert "Horarios de ingreso de la unidad:" in body(R[("GET", "/traslados/{viaje_id:int}")](req(ana, f"/traslados/{v57}"), v57))
print("57 horarios de ingreso OK")

# ---------------------------------------------------------------- propuestas 58 y 59: requisitos, habilidades y descanso
falla(lambda: R[("POST", "/traslados/vehiculos/{vid:int}/habilidades")](reqf(ana, {"habilidades": "x"}), veh), 403)
fl, _ = post(cch, "/traslados/vehiculos/{vid:int}/habilidades", {"habilidades": "Ruta larga, 7 plazas"}, veh); assert fl.startswith("Características"), fl
fl, _ = post(cch, "/traslados/choferes/{cid:int}/habilidades", {"habilidades": "licencia D1"}, ch_id); assert fl.startswith("Habilitaciones")
assert "7 plazas" in body(R[("GET", "/traslados/vehiculos/{vid:int}")](req(cch), veh))
assert "licencia d1" in body(R[("GET", "/traslados/choferes/{cid:int}")](req(cch), ch_id))
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/requisitos")](reqf(beto, {"requisitos": "x"}), vch), 403)
fl, _ = post(cch, "/traslados/{viaje_id:int}/requisitos", {"requisitos": "licencia d1"}, vch); assert fl.startswith("Requisitos guardados")
assert "Requisitos del viaje" in body(R[("GET", "/traslados/{viaje_id:int}")](req(cch, f"/traslados/{vch}"), vch))
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion IN ('Cambio de habilidades', 'Cambio de requisitos')").fetchone()[0] == 3
print("58-59 requisitos y descanso OK")

# ---------------------------------------------------------------- propuestas 61 y 63: odómetro y combustible
for i in range(5):
    flota.registrar(veh, "COMBUSTIBLE", {"fecha": (hoy_l - timedelta(days=40 - i)).isoformat(), "km": str(5000 + 300 * i), "litros": "30"}, cch)
html = body(R[("GET", "/traslados/vehiculos/{vid:int}")](req(cch), veh))
assert "Rendimiento de combustible" in html and "10.0" in html
with db._conexion() as con:
    d61 = con.execute("INSERT INTO desplazamientos (fecha, vehiculo_id, creado_en) VALUES (?, ?, 'x')", ((hoy_l - timedelta(days=2)).isoformat(), veh)).lastrowid
fl, _ = post(cch, "/traslados/desplazamiento/{desp_id:int}/cerrar", {"km_salida": "100", "km_regreso": "150"}, d61)
assert fl.startswith("⚠ Salida cerrada") and "Revisá: los km de salida (100)" in fl, fl
print("61-63 odómetro y combustible OK")

# ---------------------------------------------------------------- propuesta 67: .ics de mis viajes
x = R[("GET", "/traslados/mis-viajes.ics")](req(ana))
assert f"UID:viaje-{vch}@dgpdh".encode() in x.body and b"STATUS:CONFIRMED" in x.body and "text/calendar" in x.media_type
assert f"UID:viaje-{vch}@dgpdh".encode() not in R[("GET", "/traslados/mis-viajes.ics")](req(beto)).body, "solo los propios"
assert f"UID:viaje-{vch}@dgpdh".encode() in R[("GET", "/traslados/mis-viajes.ics")](req(chofer)).body, "el chofer, los asignados"
assert "mis-viajes.ics" in body(R[("GET", "/traslados/mi-semana")](req(ana)))
print("67 calendario .ics OK")

# ---------------------------------------------------------------- propuesta 70: vista por recurso
falla(lambda: R[("GET", "/traslados/recursos-dia")](req(ana)), 403)
html = body(R[("GET", "/traslados/recursos-dia")](req(cch), fecha=hoy_l.isoformat()))
assert "AB123CD" in html and f'href="/traslados/{vch}"' in html and "viajes-bloque--salida" in html and "Por chofer" in html
html = body(R[("GET", "/traslados/recursos-dia")](req(cch), fecha=hoy_l.isoformat(), ver="chofer"))
assert "Pedro Chofer" in html and f'href="/traslados/{vch}"' in html
print("70 vista por recurso OK")

# ---------------------------------------------------------------- propuesta 60: control antes de salir
with db._conexion() as con:
    d60 = con.execute("SELECT desplazamiento_id FROM viajes WHERE id = ?", (vch,)).fetchone()[0]
    items60 = [r["id"] for r in con.execute("SELECT id FROM checklist_items WHERE activo = 1 ORDER BY orden")]
falla(lambda: R[("POST", "/traslados/desplazamiento/{desp_id:int}/control")](reqf(ana, {f"r_{items60[0]}": "OK"}), d60), 403)
html = body(R[("GET", "/traslados/desplazamiento/{desp_id:int}")](req(chofer), d60))
assert "Control antes de salir" in html and f'name="r_{items60[0]}"' in html
fl, _ = post(chofer, "/traslados/desplazamiento/{desp_id:int}/control", {f"r_{items60[0]}": "OK", f"r_{items60[2]}": "NO_APTO", f"o_{items60[2]}": "Matafuego vencido"}, d60)
assert fl.startswith("⚠ Control guardado. 1 ítem(s) no apto(s)"), fl
with db._conexion() as con:
    nov = con.execute("SELECT detalle, gravedad FROM vehiculo_registros WHERE vehiculo_id = ? AND tipo = 'NOVEDAD' ORDER BY id DESC", (veh,)).fetchone()
assert nov["gravedad"] == "GRAVE" and "Matafuego" in nov["detalle"]
assert "Control antes de salir" in body(R[("GET", "/traslados/desplazamiento/{desp_id:int}/hoja")](req(chofer), d60))
falla(lambda: R[("GET", "/traslados/checklist")](req(chofer)), 403)
fl, _ = post(cch, "/traslados/checklist", {"texto": "Gato y llave"}); assert fl == "Control actualizado."
print("60 control antes de salir OK")

# ---------------------------------------------------------------- propuestas 86 y 88: balance trimestral y frecuencias
html = body(R[("GET", "/metas/revision")](req(coo), equipo_id=str(E1), anio=str(anio), trimestre="2"))
assert "Balance del 2.º trimestre" in html and 'name="comentario"' in html
assert 'name="comentario"' not in body(R[("GET", "/metas/revision")](req(ana), equipo_id=str(E1), anio=str(anio), trimestre="2")), "el balance lo carga la coordinación"
falla(lambda: R[("GET", "/metas/revision")](req(caro), equipo_id=str(E1)), 403)
falla(lambda: R[("POST", "/metas/revision")](reqf(ana, {"equipo_id": str(E1), "anio": str(anio), "trimestre": "2", "color": "VERDE",
                                                       "comentario": "Todo bien en el trimestre"})), 403)
fl, _ = post(coo, "/metas/revision", {"equipo_id": str(E1), "anio": str(anio), "trimestre": "2", "color": "AMBAR", "comentario": "Hubo licencias en mayo."}); assert fl == "Guardado."
fl, _ = post(direc, "/metas/revision", {"equipo_id": str(E1), "anio": str(anio), "trimestre": "2", "respuesta": "Gracias, lo vemos."}); assert fl == "Guardado."
html = body(R[("GET", "/metas/revision")](req(ana), equipo_id=str(E1), anio=str(anio), trimestre="2"))
assert "Hubo licencias en mayo." in html and "Gracias, lo vemos." in html
html = body(R[("GET", "/metas/frecuencias")](req(coo), equipo_id=str(E1)))
assert "Frecuencia de visita por unidad" in html
falla(lambda: R[("GET", "/metas/frecuencias")](req(caro), equipo_id=str(E1)), 403)
print("86-88 balance y frecuencias OK")

# ---------------------------------------------------------------- propuesta 82: meta del equipo trasladada a cada integrante
html = body(R[("GET", "/metas")](req(coo, "/metas"), anio=str(anio), equipo_id=str(E1)))
assert "la meta del equipo es también la meta de cada integrante" in html and "cumplida(s)" in html and "atrasada(s)" in html
assert 'class="semaforo-dir semaforo-dir--alto"' in html and ">atrasada</span>" in html, "estado con etiqueta y texto, no solo color"
print("82 meta por integrante OK")

# ---------------------------------------------------------------- Metas: foto diaria y «Actualizar ahora»
assert "Actualizado: " in html and "Actualizar ahora" in html
fl, _ = post(coo, "/metas/actualizar", {"anio": str(anio), "equipo_id": str(E1)})
assert "menos de un minuto" in fl, fl
with db._conexion() as con:
    con.execute("UPDATE metas_foto SET generado_en = '2000-01-01T00:00:00+00:00' WHERE equipo_id = ?", (E1,))
fl, _ = post(coo, "/metas/actualizar", {"anio": str(anio), "equipo_id": str(E1)}); assert fl == "Metas actualizadas con los viajes de hoy.", fl
with db._conexion() as con:
    assert con.execute("SELECT generado_por FROM metas_foto WHERE equipo_id = ?", (E1,)).fetchone()[0] == coo["nombre"]
falla(lambda: R[("POST", "/metas/actualizar")](reqf(caro, {"anio": str(anio), "equipo_id": str(E1)})), 403)
print("metas foto diaria OK")

# ---------------------------------------------------------------- Modificación 4: pasajeros externos
falla(lambda: R[("GET", "/traslados/pasajeros-externos")](req(ana)), 403)
from dgpdh.servicios import pasajeros_externos as px
with db._conexion() as con: cat_ = px.categorias(con)[3]["id"]
fl, _ = post(cch, "/traslados/pasajeros-externos", {"nombre": "Juan", "apellido": "Invitado", "documento": "28999888", "categoria_id": str(cat_)}); assert fl == "Pasajero externo guardado."
fl, _ = post(cch, "/traslados/pasajeros-externos", {"nombre": "Juan", "apellido": "Otro", "documento": "28999888", "categoria_id": str(cat_)}); assert "documento" in fl
assert "Invitado, Juan" in body(R[("GET", "/traslados/pasajeros-externos")](req(direc)))
tr.agregar_participante(vch, {"nombre": "Juan visitante", "auto_propio": 0})
pext = next(x for x in tr.obtener_viaje(vch)["participantes"] if x["nombre"] == "Juan visitante")
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(cch, f"/traslados/{vch}"), vch))
assert "o es el externo" in html and "Invitado, Juan · DNI 28999888 · Invitado" in html
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/participantes/{pid:int}/externo")](reqf(ana, {"externo": "28999888"}), vch, pext["id"]), 403)
fl, _ = post(cch, "/traslados/{viaje_id:int}/participantes/{pid:int}/externo", {"externo": "nadie"}, vch, pext["id"]); assert fl.startswith("⚠ Elegí")
fl, _ = post(cch, "/traslados/{viaje_id:int}/participantes/{pid:int}/externo", {"externo": "Invitado, Juan · DNI 28999888 · Invitado"}, vch, pext["id"])
assert fl.startswith("Vinculado: Juan Invitado (Invitado)"), fl
html = body(R[("GET", "/traslados/desplazamiento/{desp_id:int}/hoja")](req(cch), d60))
assert "Juan Invitado" in html and "externo: Invitado, DNI 28999888" in html
assert "externo · Invitado" in body(R[("GET", "/traslados/{viaje_id:int}")](req(cch, f"/traslados/{vch}"), vch))
print("Mod 4 pasajeros externos OK")

# ---------------------------------------------------------------- Modificación 5: chofer sugerido por ruta compartida
with db._conexion() as con:
    con.execute("UPDATE viajes SET requisitos = NULL WHERE id = ?", (vch,))
    con.execute("UPDATE choferes SET habilidades = NULL WHERE id = ?", (ch_id,))
v5 = tr.crear_viaje({"fecha": hoy_l.isoformat(), "hora_salida": "08:30", "hora_regreso": "13:00", "unidad_id": amba[0]["id"]}, [P(beto)], beto,
                    hoy=hoy_l - timedelta(days=10))
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(cch, f"/traslados/{v5}"), v5))
import re as _re
opt = _re.search(r'<option value="%d" selected>([^<]*)</option>' % ch_id, html)
assert opt and "(sugerido)" in opt.group(1) and f"comparte viaje #{vch}" in opt.group(1), opt and opt.group(1)
print("Mod 5 chofer por ruta compartida OK")

# ---------------------------------------------------------------- Modificación 6: viajes unificados por unidad
f6 = (hoy_l + timedelta(days=40))
f6 = f6 + timedelta(days=(7 - f6.weekday()) % 7)  # un lunes
a6 = tr.crear_viaje({"fecha": f6.isoformat(), "hora_salida": "09:00", "hora_regreso": "13:00", "unidad_id": amba[0]["id"]}, [P(ana)], ana)
b6 = tr.crear_viaje({"fecha": f6.isoformat(), "hora_salida": "09:30", "hora_regreso": "13:00", "unidad_id": amba[3]["id"]}, [P(caro)], caro)
op.unificar_viajes([a6, b6], cch, chofer_id=ch_id, vehiculo_id=veh)
html = body(R[("GET", "/traslados")](req(cch, "/traslados"), ver="agenda", lunes=f6.isoformat()))
i_ = html.index("viaje--salida")
tarjeta = html[i_: html.index("</div>", i_)]
assert amba[0]["nombre"] in tarjeta and amba[3]["nombre"] in tarjeta and tarjeta.count('class="viaje__parada"') == 2 and "AB123CD" in tarjeta
paradas_ = tarjeta[tarjeta.index('class="viaje__parada"'):]
assert paradas_.index("Ana Pérez") < paradas_.index(amba[3]["nombre"]) < paradas_.index("Caro CPF"), "cada pasajero bajo su unidad"
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(cch, f"/traslados/{a6}"), a6))
assert "Misma salida: varias paradas" in html and html.count('class="viajes-parada-bloque"') == 2
print("Mod 6 viajes unificados OK")

# ---------------------------------------------------------------- propuesta 74: tiempo de respuesta
p74 = tr.crear_viaje({"fecha": (hoy_l + timedelta(days=1)).isoformat(), "hora_salida": "09:00", "unidad_id": amba[0]["id"]}, [P(beto)], beto)
html = body(R[("GET", "/traslados/asignar")](req(cch)))
assert "el viaje es en menos de 48 h" in html and "semaforo-dir--alto" in html
html = body(R[("GET", "/traslados/direccion")](req(cch, "/traslados/direccion"), periodo="personalizado", desde=(hoy_l - timedelta(days=60)).isoformat(), hasta=hoy_l.isoformat()))
assert "Tiempo de respuesta de la coordinación" in html
print("74 tiempo de respuesta OK")

# ---------------------------------------------------------------- propuesta 81: salidas con lugar
html = body(R[("GET", "/traslados")](req(caro, "/traslados")))
assert "Salidas con lugar" in html and f'action="/traslados/{vch}/sumarme"' in html
assert f'action="/traslados/{vch}/sumarme"' not in body(R[("GET", "/traslados")](req(ana, "/traslados"))), "no a quien ya viaja"
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/sumarme")](reqf(chofer, {}), vch), 403)
fl, _ = post(caro, "/traslados/{viaje_id:int}/sumarme", {}, vch); assert fl.startswith("Listo: la coordinación confirma"), fl
fl, _ = post(caro, "/traslados/{viaje_id:int}/sumarme", {}, vch); assert "lista de espera" in fl
assert any(e["usuario_id"] == caro["id"] for e in op.espera_de(vch))
print("81 salidas con lugar OK")

# ---------------------------------------------------------------- perfiles de metas (equipos de salud)
falla(lambda: R[("POST", "/admin/equipos/{equipo_id:int}/perfil-metas")](reqf(coo, {"perfil": "salud_medica"}), E1), 403)
E_sm = db.crear_equipo("Equipo Salud Mental"); coo_sm = U("coosm", "Coord SM", "COORDINADOR", E_sm); ase_sm = U("asesm", "Ase SM", "ASESOR", E_sm)
fl, _ = post(direc, "/admin/equipos/{equipo_id:int}/perfil-metas", {"perfil": "salud_mental"}, E_sm); assert fl == "Perfil de metas guardado."
html = body(run(R[("GET", "/admin/{pestana}/{sub}")](req(direc, "/admin/equipos/listado"), pestana="equipos", sub="listado")))
assert f'action="/admin/equipos/{E_sm}/perfil-metas"' in html and '<option value="salud_mental" selected>' in html
html = body(R[("GET", "/metas/carga-equipo")](req(coo_sm), equipo_id=str(E_sm), anio=str(anio), periodo="M07"))
assert "Recorridas" in html and 'name="e_sm_recorridas"' in html
assert 'name="e_sm_recorridas"' not in body(R[("GET", "/metas/carga-equipo")](req(ase_sm), equipo_id=str(E_sm), anio=str(anio), periodo="M07"))
falla(lambda: R[("POST", "/metas/carga-equipo")](reqf(ase_sm, {"equipo_id": str(E_sm), "anio": str(anio), "periodo": "M07", "e_sm_recorridas": "3"})), 403)
fl, _ = post(coo_sm, "/metas/carga-equipo", {"equipo_id": str(E_sm), "anio": str(anio), "periodo": "M07", "e_sm_recorridas": "3", "e_sm_reuniones": "x"})
assert fl.startswith("Guardado (1 valor/es).") and "no eran números" in fl, fl
html = body(R[("GET", "/metas")](req(coo_sm, "/metas"), anio=str(anio), equipo_id=str(E_sm)))
assert "Perfil de indicadores: Salud mental" in html and "Recorridas" in html and "Monitoreos" not in html
falla(lambda: R[("GET", "/metas/carga-equipo")](req(caro), equipo_id=str(E_sm)), 403)
print("perfiles de metas OK")

# ---------------------------------------------------------------- importación de la planilla de salud médica (datos sintéticos)
import openpyxl as _ox2
E_med = db.crear_equipo("Equipo Salud Médica"); coo_med = U("coomd", "Coord Médica", "COORDINADOR", E_med)
med1 = U("medx1", "Laura Ficticia", "ASESOR", E_med); med2 = U("medx2", "Mario Inventado", "ASESOR", E_med)
fl, _ = post(direc, "/admin/equipos/{equipo_id:int}/perfil-metas", {"perfil": "salud_medica"}, E_med)
lb = _ox2.Workbook(); hj = lb.active
for fila in [(None, "MEDICOS", "Trimestre", "MES", "Entrevistas", "Relevamientos de historias clínicas", "Intervenciones", "Recomendaciones", "TOTAL"),
             (None, "FICTICIA", 3, "JULIO", 5, 1, 2, 3, 11), (None, None, None, "AGOSTO", 6, 0, 0, 1, 7),
             (None, "DESCONOCIDA", 3, "JULIO", 1, 1, 1, 1, 4), (None, "TOTAL", None, None, 12, 2, 3, 5, 22), (None,),
             (None, "VISITAS A LAS UNIDADES", None, None, "INFORMES GOLPEADOS "), (None, "UNIDAD/COMPLEJO /HTAL", "3º TRIMESTRE ", None, "UNIDAD O COMPLEJO ", "3º TRIMESTRE "),
             (None, "CPF I", 7, None, "CPF I", 2), (None, "TOTAL", 7, None, "TOTAL", 2), (None,),
             (None, "Casos individuales trabajados por nota"), (None, "COMPLEJOS", "Julio", "Agosto", "Septiembre", "Total Individual", "Total Colectiva", "Julio", "Agosto", "Septiembre", "Total Respuesta"),
             (None, "Hospital de prueba", 3, 4, 5, 12, 2, 1, 0, 1, 2), (None, "Total general", 3, 4, 5, 12, 2, 1, 0, 1, 2), (None,),
             (None, "REUNIONES DE EQUIPO Y/O CON OTROS EQUIPOS", None, "10")]:
    hj.append(list(fila))
_b = io.BytesIO(); lb.save(_b)
falla(lambda: R[("POST", "/metas/importar-salud")](reqf(med1, {"equipo_id": str(E_med), "anio": "2026", "archivo": Archivo("p.xlsx", _b.getvalue())})), 403)
assert '<option value="%d"' % E_med in body(R[("GET", "/metas/importar-salud")](req(coo_med)))
rr = reqf(coo_med, {"equipo_id": str(E_med), "anio": "2026", "archivo": Archivo("p.xlsx", _b.getvalue())})
html = body(run(R[("POST", "/metas/importar-salud")](rr)))
assert "Vista previa" in html and "FICTICIA → <strong>Laura Ficticia</strong>" in html and "DESCONOCIDA → <span" in html
assert "Visitas a CPF I: la planilla dice 7" in html and "Hospital de prueba → queda como texto" in html and "Confirmar e importar" in html
with db._conexion() as con: assert not con.execute("SELECT 1 FROM metas_realizado_dim WHERE equipo_id = ?", (E_med,)).fetchone(), "la vista previa no guarda"
datos = _re.search(r'<textarea name="datos" hidden>(.*?)</textarea>', html, _re.S).group(1)
import html as _h
fl, r = post(coo_med, "/metas/importar-salud", {"equipo_id": str(E_med), "anio": "2026", "datos": _h.unescape(datos)})
assert fl.startswith("Planilla importada:"), fl
x = metas.seguimiento(2026, [{"id": E_med, "nombre": "Equipo Salud Médica"}], date(2026, 9, 30))["equipos"][0]
f_ = {r_["clave"]: r_ for r_ in x["filas"]}
assert f_["med_entrevistas"]["hecho"] == 11 and f_["med_casos_individuales"]["hecho"] == 12 and f_["med_casos_colectivos"]["hecho"] == 2
assert f_["med_reuniones"]["hecho"] == 10 and f_["med_informes_golpeados"]["unidades"] == [{"unidad": next(x["nombre"] for x in db.listar_unidades() if x["nombre"].endswith("-CPF I-")), "trimestres": [0, 0, 2, 0], "total": 2}]
rr = reqf(coo_sm, {"equipo_id": str(E_sm), "anio": "2026", "archivo": Archivo("p.xlsx", _b.getvalue())}); run(R[("POST", "/metas/importar-salud")](rr))
assert "salud medica" in rr.session["flash"] and "Salud mental" in rr.session["flash"], rr.session["flash"]
print("importación de planillas de salud OK")

# ---------------------------------------------------------------- propuestas 92, 95 y 97
html = body(R[("GET", "/metas")](req(coo, "/metas"), anio=str(anio), equipo_id=str(E1)))
assert "¿Cómo se calcula?" in html and "Un día + una unidad + un equipo = una visita" in html and "Exportar para publicar" not in html
falla(lambda: R[("GET", "/metas/publicar.xlsx")](req(coo)), 403)
x = R[("GET", "/metas/publicar.xlsx")](req(direc), anio=str(anio))
lb = _ox2.load_workbook(io.BytesIO(x.body)); filas_ = [[c.value for c in r] for r in lb.active.iter_rows(min_row=4)]
assert filas_ and not any(isinstance(v, int) and 0 < v < 3 for r in filas_ for v in r[3:]), "nada entre 1 y 2 a la vista"
assert not any("Pérez" in str(v) or "Ruiz" in str(v) for r in filas_ for v in r), "sin personas"
falla(lambda: R[("GET", "/metas/calidad")](req(coo)), 403)
assert "Audiencias informadas en viajes que no se realizaron" in body(R[("GET", "/metas/calidad")](req(direc), anio=str(anio)))
print("92-95-97 ficha, publicar y calidad OK")

# ---------------------------------------------------------------- propuesta 66: disponibilidad de la flota
tr.estado_vehiculo(veh, "TALLER", "Frenos"); tr.estado_vehiculo(veh, "DISPONIBLE")
falla(lambda: R[("GET", "/traslados/flota/disponibilidad")](req(ana)), 403)
html = body(R[("GET", "/traslados/flota/disponibilidad")](req(cch), anio=str(hoy_l.year)))
assert "AB123CD" in html and "Toda la flota" in html and "Viajes que quedaron sin vehículo" in html
print("66 disponibilidad OK")

# ---------------------------------------------------------------- propuestas 62 y 78: costos y recorridos
with db._conexion() as con:
    con.execute("UPDATE desplazamientos SET importe = 5000, km_salida = 1000, km_regreso = 1080, salida_real = '08:00', regreso_real = '14:00' WHERE id = ?", (d60,))
falla(lambda: R[("GET", "/traslados/costos")](req(ana)), 403)
html = body(R[("GET", "/traslados/costos")](req(cch), desde=(hoy_l - timedelta(days=5)).isoformat(), hasta=hoy_l.isoformat()))
assert amba[0]["nombre"] in html and "Equipo Salud" in html and "5,000.00" in html
x = R[("GET", "/traslados/costos.xlsx")](req(cch)); assert _ox2.load_workbook(io.BytesIO(x.body)).sheetnames == ["Por unidad", "Por equipo", "Por trimestre"]
falla(lambda: R[("GET", "/traslados/recorridos")](req(ana)), 403)
html = body(R[("GET", "/traslados/recorridos")](req(cch), vehiculo_id=str(veh), desde=(hoy_l - timedelta(days=5)).isoformat(), hasta=hoy_l.isoformat()))
assert "Informe de recorridos" in html and amba[0]["nombre"] in html and "360 min" in html and ">80<" in html
x = R[("GET", "/traslados/recorridos")](req(cch), vehiculo_id=str(veh), formato="xlsx")
assert _ox2.load_workbook(io.BytesIO(x.body)).active["A1"].value == "Fecha"
print("62-78 costos y recorridos OK")

# ---------------------------------------------------------------- propuesta 55: orden sugerido de paradas
with db._conexion() as con:
    for k_, (la, lo) in enumerate(((-34.60, -58.40), (-34.90, -58.60), (-34.62, -58.41))):
        con.execute("UPDATE unidades SET lat = ?, lng = ? WHERE id = ?", (la, lo, amba[k_ + 5]["id"]))
fz = (hoy_l + timedelta(days=50)).isoformat()
vs55 = [tr.crear_viaje({"fecha": fz, "hora_salida": h_, "hora_regreso": "14:00", "unidad_id": amba[k_ + 5]["id"]}, [P(ana)] if k_ == 0 else [P(beto)] if k_ == 1 else [P(caro)],
                       ana if k_ == 0 else beto if k_ == 1 else caro) for k_, h_ in enumerate(("08:00", "08:10", "08:20"))]
for v_ in vs55: tr.asignar(v_, ch_id, veh, cch, forzar=True)
d55 = tr.obtener_viaje(vs55[0])["desplazamiento_id"]
html = body(R[("GET", "/traslados/desplazamiento/{desp_id:int}")](req(cch), d55))
assert "Sugerir orden de las paradas" in html and "Usar el orden sugerido" in html and "línea recta" in html
orden_ = _re.search(r'name="orden" value="([0-9,]+)"', html).group(1)
assert orden_.split(",")[:2] == [str(vs55[0]), str(vs55[2])], orden_
falla(lambda: R[("POST", "/traslados/desplazamiento/{desp_id:int}/orden")](reqf(chofer, {"orden": orden_}), d55), 403)
fl, _ = post(cch, "/traslados/desplazamiento/{desp_id:int}/orden", {"orden": "1,2"}, d55); assert fl.startswith("⚠")
fl, _ = post(cch, "/traslados/desplazamiento/{desp_id:int}/orden", {"orden": orden_}, d55); assert fl == "Orden de paradas actualizado."
assert [p["id"] for p in op.obtener_desplazamiento(d55)["paradas"]] == [int(x) for x in orden_.split(",")]
print("55 orden sugerido OK")

# ---------------------------------------------------------------- propuestas 76, 85 y 94
html = body(R[("GET", "/metas")](req(coo, "/metas"), anio=str(anio), equipo_id=str(E1)))
assert "Tendencia" in html and "viajes-spark" in html
rqq = req(direc, "/metas"); rqq.query_params = {"normalizar": "unidad"}
html = body(R[("GET", "/metas")](rqq, anio=str(anio)))
assert "Comparación entre equipos" in html and "Unidades a cargo" in html
f76 = (hoy_l + timedelta(days=60)).isoformat()
r1 = tr.crear_viaje({"fecha": f76, "hora_salida": "09:00", "unidad_id": amba[0]["id"]}, [P(ana)], ana)
r2 = tr.crear_viaje({"fecha": f76, "hora_salida": "10:00", "unidad_id": amba[1]["id"]}, [P(caro)], caro)
tr.pedir_revision_direccion(r1, "Medida de fuerza en el pabellón 3 desde ayer", ana)
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(direc, f"/traslados/{r1}"), r1))
assert "Pedidos con chofer de ese día" in html and f'href="/traslados/{r2}">#{r2}</a>' in html and "Medida de fuerza en el pabellón 3" in html
print("76-85-94 revisión, tendencia y comparación OK")

# ---------------------------------------------------------------- propuesta 96: borrador de lectura (sin IA en las pruebas)
nucleo.consultar_ollama = lambda *a, **k: "Las visitas vienen [H1] y conviene revisarlas."
html = body(run(R[("POST", "/metas/lectura")](reqf(coo, {"equipo_id": str(E1), "anio": str(anio), "trimestre": "3"}))))
assert "Borrador para revisar." in html and "Lo redactó la IA local" in html and "[H1]" not in html.split("Datos usados")[0]
nucleo.consultar_ollama = lambda *a, **k: None
assert "La IA no estaba disponible" in body(run(R[("POST", "/metas/lectura")](reqf(coo, {"equipo_id": str(E1), "anio": str(anio), "trimestre": "3"}))))
falla(lambda: R[("POST", "/metas/lectura")](reqf(caro, {"equipo_id": str(E1)})), 403)
print("96 borrador de lectura OK")

# ---------------------------------------------------------------- propuesta 56 (parte sin software externo): restricciones en el planificador
f56 = hoy_l + timedelta(days=21); f56 = f56 - timedelta(days=f56.weekday()) + timedelta(days=1)  # un martes
v56 = tr.crear_viaje({"fecha": f56.isoformat(), "hora_salida": "16:00", "hora_regreso": "18:00", "unidad_id": U57}, [P(beto)], beto)
plan = op.planificar(tr.lunes_de(f56))
pr = next(p_ for p_ in plan["propuesta"] if any(x["id"] == v56 for x in p_["viajes"]))
assert any("horario de ingreso" in a for a in pr["avisos"]), pr["avisos"]
assert "horario de ingreso de ese día termina" in body(R[("GET", "/traslados/planificar")](req(cch), lunes=tr.lunes_de(f56).isoformat(), vista="propuesta"))
print("56 restricciones del planificador OK")

# ---------------------------------------------------------------- propuesta 75: lista de espera con prioridad
fl, _ = post(beto, "/traslados/{viaje_id:int}/espera", {"motivo": "HUELGA_HAMBRE", "motivo_detalle": "Pabellón 2"}, vch)
assert "lista de espera" in fl, fl
le = op.espera_de(vch); assert le[0]["usuario_id"] == beto["id"] and le[0]["urgente"], le
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(cch), vch))
assert "Huelga de hambre" in html and f'action="/traslados/{vch}/espera/orden"' in html
falla(lambda: R[("POST", "/traslados/{viaje_id:int}/espera/orden")](reqf(caro, {"espera_id": str(le[0]["id"]), "hacia": "bajar"}), vch), 403)
fl, _ = post(cch, "/traslados/{viaje_id:int}/espera/orden", {"espera_id": str(le[0]["id"]), "hacia": "bajar"}, vch)
assert fl == "Orden de la lista de espera actualizado." and op.espera_de(vch)[1]["usuario_id"] == beto["id"]
print("75 lista de espera con prioridad OK")

# ---------------------------------------------------------------- propuesta 77: propuesto vs. asignado
html = body(R[("GET", "/traslados/planificar")](req(cch), lunes=tr.lunes_de(f56).isoformat(), vista="propuesta"))
assert "Propuesto vs. asignado" in html
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM planificador_propuesto WHERE viaje_id = ?", (v56,)).fetchone()[0] == 1
html = body(R[("GET", "/traslados/planificar/comparacion")](req(cch), lunes=tr.lunes_de(f56).isoformat()))
assert "Propuesto vs. asignado" in html and "Pedidos sin cubrir" in html and "todavía no terminó" in html
falla(lambda: R[("GET", "/traslados/planificar/comparacion")](req(caro), lunes=""), 403)
print("77 propuesto vs. asignado OK")

# ---------------------------------------------------------------- propuesta 64: proveedores, responsable y días de aviso
falla(lambda: R[("GET", "/traslados/proveedores")](req(caro)), 403)
falla(lambda: R[("POST", "/traslados/proveedores")](reqf(caro, {"nombre": "Taller X"})), 403)
fl, _ = post(cch, "/traslados/proveedores", {"nombre": "Taller Norte", "tipo": "TALLER"}); assert fl == "Proveedor guardado.", fl
fl, _ = post(cch, "/traslados/proveedores/dias-aviso", {"dias_VTV": "999"}); assert fl.startswith("⚠"), fl
fl, _ = post(cch, "/traslados/proveedores/dias-aviso", {"dias_VTV": "60"}); assert "1 cambio" in fl, fl
html = body(R[("GET", "/traslados/proveedores")](req(cch)))
assert "Taller Norte" in html and 'name="dias_VTV"' in html and 'value="60"' in html
with db._conexion() as con:
    pid64 = con.execute("SELECT id FROM proveedores WHERE nombre = 'Taller Norte'").fetchone()[0]
vid64 = veh if isinstance(veh, int) else veh["id"]
html = body(R[("GET", "/traslados/vehiculos/{vid:int}")](req(cch), vid64))
assert 'name="responsable_id"' in html and "Taller Norte" in html
fl, _ = post(cch, "/traslados/vehiculos/{vid:int}/control", {"tipo": "FRENOS", "fecha": hoy_l.isoformat(), "proveedor_id": str(pid64), "referencia": "OT-9", "responsable_id": str(ana["id"])}, vid64)
assert fl == "Control registrado.", fl
assert "ref. OT-9" in body(R[("GET", "/traslados/vehiculos/{vid:int}")](req(cch), vid64))
fl, _ = post(cch, "/traslados/proveedores/{pid:int}/baja", {}, pid64); assert "dado de baja" in fl
print("64 proveedores y avisos OK")

# ---------------------------------------------------------------- propuesta 65: órdenes de trabajo de taller
v65 = tr.guardar_vehiculo(None, {"dominio": "AC065AC", "capacidad": "4"}); v65 = v65 if isinstance(v65, int) else v65["id"]
n65 = flota.registrar(v65, "NOVEDAD", {"detalle": "Pierde aceite", "gravedad": "MODERADA"}, cch)
falla(lambda: R[("POST", "/traslados/vehiculos/{vid:int}/ordenes")](reqf(caro, {"descripcion": "x"}), v65), 403)
fl, _ = post(cch, "/traslados/vehiculos/{vid:int}/ordenes", {"descripcion": "Junta de cárter", "taller": "Taller Oeste", "novedad": str(n65)}, v65)
assert fl.startswith("Orden de trabajo #"), fl
with db._conexion() as con:
    oid65 = con.execute("SELECT id FROM ordenes_trabajo WHERE vehiculo_id = ?", (v65,)).fetchone()[0]
html = body(R[("GET", "/traslados/vehiculos/{vid:int}")](req(cch), v65))
assert "Órdenes de trabajo de taller" in html and "Junta de cárter" in html and "Empezar (pasa a En curso)" in html
fl, _ = post(cch, "/traslados/ordenes/{oid:int}/etapa", {"etapa": "EN_CURSO", "vehiculo_id": str(v65)}, oid65)
assert "quedó en taller" in fl, fl
assert next(x for x in tr.listar_vehiculos() if x["id"] == v65)["estado"] == "TALLER"
fl, _ = post(cch, "/traslados/ordenes/{oid:int}/etapa", {"etapa": "HECHA", "vehiculo_id": str(v65), "costo_final": "18.000"}, oid65)
assert "sigue en taller" in fl and next(x for x in tr.listar_vehiculos() if x["id"] == v65)["estado"] == "TALLER", fl
fl, _ = post(cch, "/traslados/vehiculos/{vid:int}/ordenes", {"descripcion": "Revisión final"}, v65)
with db._conexion() as con:
    oid65b = con.execute("SELECT MAX(id) FROM ordenes_trabajo WHERE vehiculo_id = ?", (v65,)).fetchone()[0]
post(cch, "/traslados/ordenes/{oid:int}/etapa", {"etapa": "EN_CURSO", "vehiculo_id": str(v65)}, oid65b)
fl, _ = post(cch, "/traslados/ordenes/{oid:int}/etapa", {"etapa": "HECHA", "vehiculo_id": str(v65), "volver_disponible": "1"}, oid65b)
assert "volvió a estar disponible" in fl and next(x for x in tr.listar_vehiculos() if x["id"] == v65)["estado"] == "DISPONIBLE", fl
fl, _ = post(cch, "/traslados/ordenes/{oid:int}/etapa", {"etapa": "EN_CURSO", "vehiculo_id": str(v65)}, oid65b); assert fl.startswith("⚠"), fl
print("65 órdenes de trabajo OK")
