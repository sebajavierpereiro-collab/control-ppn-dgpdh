"""Ola 4: traslados — desplazamientos, horario real, km y combustible, novedades, licencias, reservas, lista de espera, planificador, chofer sugerido, anomalías, regreso estimado, recordatorio, comparación Excel y pantallas."""
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
from datetime import date, datetime, timedelta
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth, automatico, nucleo
import traslados as tr, operacion as op, rutas_operacion as ro, flota
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
nucleo.USAR_IA = False

class URL: path = "/"; query = ""
class Ses(dict): pass
def req(u, form=None):
    class Form(dict):
        def getlist(self, k):
            v = self.get(k); return v if isinstance(v, list) else ([] if v is None else [v])
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(), session=Ses(), cookies={},
                                 headers={}, form=_f, client=types.SimpleNamespace(host="1"), query_params={})
R = main.app.rutas
def llamar(m, p, u, form=None, **kw):
    r = R[(m, p)](req(u, form), **kw)
    return asyncio.run(r) if asyncio.iscoroutine(r) else r
def status(m, p, u, form=None, **kw):
    try:
        llamar(m, p, u, form, **kw); return 200
    except HTTPException as e:
        return e.status_code
HTTPException = sys.modules["fastapi"].HTTPException

# ---- datos
eq = db.crear_equipo("Equipo A")
h_, s_ = auth.generar_hash_clave("Clave1234")
ana = db.obtener_usuario(db.crear_usuario("ana", h_, s_, "Ana", "ASESOR", eq))
beto = db.obtener_usuario(db.crear_usuario("beto", h_, s_, "Beto", "ASESOR", eq))
caro = db.obtener_usuario(db.crear_usuario("caro", h_, s_, "Caro", "ASESOR", eq))
dani = db.obtener_usuario(db.crear_usuario("dani", h_, s_, "Dani", "ASESOR", eq))
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
u_ch1 = db.obtener_usuario(db.crear_usuario("ch1", h_, s_, "Chofer Uno", "CHOFER", None, acceso_notas=False))
u_ch2 = db.obtener_usuario(db.crear_usuario("ch2", h_, s_, "Chofer Dos", "CHOFER", None, acceso_notas=False))
tr.sincronizar_choferes()
CH = {c["usuario_id"]: c["id"] for c in tr.listar_choferes()}
ch1, ch2 = CH[u_ch1["id"]], CH[u_ch2["id"]]
v4 = tr.guardar_vehiculo(None, {"dominio": "AA100AA", "capacidad": "4", "marca": "Fiat"})
v8 = tr.guardar_vehiculo(None, {"dominio": "BB200BB", "capacidad": "8", "marca": "Renault"})
amba = [u for u in db.listar_unidades() if tr.es_amba(u["id"])]
U1, U2, U3 = amba[0]["id"], amba[1]["id"], amba[2]["id"]
with db._conexion() as con:
    con.execute("UPDATE unidades SET lat=-34.85, lng=-58.52 WHERE id=?", (U1,))
    con.execute("UPDATE unidades SET lat=-34.86, lng=-58.53 WHERE id=?", (U2,))
    con.execute("UPDATE unidades SET lat=-34.40, lng=-58.90 WHERE id=?", (U3,))
hoy = date.today()
lunes = tr.lunes_de(hoy) + timedelta(days=14)
mar = (lunes + timedelta(days=1)).isoformat()
def P(u, auto=False): return {"usuario_id": u["id"], "nombre": u["nombre"], "equipo_nombre": "Equipo A", "auto_propio": auto}
def viaje(fecha, unidad, personas, salida="09:00", regreso="13:00", quien=None):
    return tr.crear_viaje(dict(fecha=fecha, hora_salida=salida, hora_regreso=regreso, unidad_id=unidad, modalidad="CHOFER", motivo="Entrevistas"),
                          personas, quien or personas[0] and db.obtener_usuario(personas[0]["usuario_id"]))

# ---- 16) planificador: junta los pedidos del mismo día en unidades cercanas
a1 = viaje(mar, U1, [P(ana), P(beto)])
a2 = viaje(mar, U2, [P(caro)], salida="10:00", regreso="14:00")
a3 = viaje(mar, U3, [P(dani)])
plan = op.planificar(lunes)
grupo = next(p for p in plan["propuesta"] if a1 in [v["id"] for v in p["viajes"]])
assert {v["id"] for v in grupo["viajes"]} == {a1, a2} and grupo["agrupa"] and grupo["personas"] == 3, grupo
assert plan["pedidos"] == 3 and plan["salidas"] == 2 and plan["ahorro"] == 1
assert grupo["vehiculo"]["id"] == v4, "el vehículo más chico que alcanza"
assert grupo["chofer"] and grupo["chofer"]["apto"]
otro = next(p for p in plan["propuesta"] if a3 in [v["id"] for v in p["viajes"]])
assert otro["chofer"]["id"] != grupo["chofer"]["id"] and otro["vehiculo"]["id"] == v8, "no repite chofer ni vehículo el mismo día"
assert all(tr.obtener_viaje(i)["estado"] == "SOLICITADO" for i in (a1, a2, a3)), "el planificador solo propone"
# aplicar: un solo desplazamiento con dos paradas ordenadas
op.aplicar_propuesta([a1, a2], ch1, v4, direc)
va1, va2 = tr.obtener_viaje(a1), tr.obtener_viaje(a2)
assert va1["estado"] == va2["estado"] == "CONFIRMADO" and va1["desplazamiento_id"] and va1["desplazamiento_id"] == va2["desplazamiento_id"]
assert (va1["orden_parada"], va2["orden_parada"]) == (1, 2)
D = va1["desplazamiento_id"]
d = op.obtener_desplazamiento(D)
assert d["dominio"] == "AA100AA" and d["personas"] == 3 and [p["id"] for p in d["paradas"]] == [a1, a2] and d["equipos"] == ["Equipo A"]
print("planificador y desplazamientos OK")

# ---- 17) chofer sugerido: carga semanal pareja, licencia, licencia de conducir
sug = tr.sugerencias(a3)
assert sug["choferes"][0]["id"] == ch2 and sug["choferes"][0].get("sugerido"), [(c["nombre"], c["motivo"]) for c in sug["choferes"]]
assert "1 salida esta semana" in next(c for c in sug["choferes"] if c["id"] == ch1)["motivo"]
af = op.agregar_licencia(ch2, mar, mar, "VACACIONES", "", "Dir")
assert af == []
sug = tr.sugerencias(a3)
c2 = next(c for c in sug["choferes"] if c["id"] == ch2)
assert not c2["apto"] and "vacaciones" in c2["motivo"] and not c2.get("sugerido")
try:
    tr.asignar(a3, ch2, v8, direc); raise AssertionError("debía avisar la licencia")
except tr.ErrorTraslado as e:
    assert "vacaciones" in str(e)
tr.asignar(a3, ch2, v8, direc, forzar=True)  # la coordinación decide
assert any("vacaciones" in a["texto"] for a in op.anomalias(mar, mar)), "anomalía: chofer de licencia con viaje"
assert op.afectados_por_licencia(ch2, mar, mar)[0]["id"] == a3
cal = op.calendario_choferes(lunes, 7)
fila = next(f for f in cal["filas"] if f["chofer"]["id"] == ch2)
assert fila["celdas"][1]["licencia"] == "Vacaciones" and fila["celdas"][1]["viajes"] == 1
op.borrar_licencia(op.licencias(chofer_id=ch2)[0]["id"])
print("chofer sugerido y licencias OK")

# ---- 72/19) reservas y vencimientos
rid, choque = op.agregar_reserva(v8, mar, "08:00", "12:00", "Service", "Intendencia", "Dir")
assert choque and choque[0]["id"] == a3, "avisa el viaje que ya tiene el vehículo"
assert v8 in op.ocupacion_extra(mar, {"hora_salida": "09:00", "hora_regreso": "10:00"})[1]
assert v8 not in op.ocupacion_extra(mar, {"hora_salida": "13:00", "hora_regreso": "15:00"})[1]
assert any("reservado" in a["texto"] for a in op.anomalias(mar, mar))
op.cancelar_reserva(rid, "Dir"); assert not op.reservas(vehiculo_id=v8)
with db._conexion() as con: con.execute("UPDATE vehiculos SET vtv_vence = ? WHERE id = ?", ((hoy + timedelta(days=1)).isoformat(), v8))
assert any("VTV" in a["texto"] for a in op.anomalias(mar, mar))
a4 = viaje(mar, U3, [P(ana)], salida="15:00", regreso="17:00")
try:
    tr.asignar(a4, ch2, v8, direc); raise AssertionError("debía avisar la VTV vencida")
except tr.ErrorTraslado as e:
    assert "VTV" in str(e)
assert next(x for x in tr.sugerencias(a4)["vehiculos"] if x["id"] == v8)["aviso"] == "VTV vencida"
with db._conexion() as con: con.execute("UPDATE vehiculos SET vtv_vence = NULL WHERE id = ?", (v8,))
# horario inusual y regreso antes de la salida
with db._conexion() as con: con.execute("UPDATE viajes SET hora_salida = '05:00' WHERE id = ?", (a4,))
assert any("horario inusual" in a["texto"] for a in op.anomalias(mar, mar))
print("reservas y anomalías OK")

# ---- reprogramar / cancelar actualizan el desplazamiento
tr.cancelar(a2, "Se suspendió", direc)
d = op.obtener_desplazamiento(D); assert [p["id"] for p in d["paradas"]] == [a1] and tr.obtener_viaje(a2)["desplazamiento_id"] is None
tr.reprogramar(a1, (lunes + timedelta(days=2)).isoformat(), "09:00", "13:00", direc)
assert tr.obtener_viaje(a1)["desplazamiento_id"] is None and op.obtener_desplazamiento(D) is None, "salida vacía se borra"
print("reprogramar y cancelar OK")

# ---- 73) lista de espera
b = (hoy + timedelta(days=5)).isoformat()
w = viaje(b, U1, [P(ana), P(beto), P(caro)])
tr.asignar(w, ch1, v4, direc)
tr.agregar_participante(w, {"nombre": "Externa Uno"})
try:
    tr.agregar_participante(w, P(dani)); raise AssertionError("no hay lugar")
except tr.ErrorTraslado as e:
    assert "lista de espera" in str(e)
llamar("POST", "/traslados/{viaje_id:int}/espera", dani, viaje_id=w)
assert op.espera_de(w)[0]["usuario_id"] == dani["id"]
try:
    op.anotar_en_espera(w, dani); raise AssertionError("dos veces no")
except tr.ErrorTraslado: pass
html = llamar("GET", "/traslados/{viaje_id:int}", dani, viaje_id=w).body
assert "Lista de espera" in html and "Salir de la lista de espera" in html
quien = next(p for p in tr.obtener_viaje(w)["participantes"] if p["nombre"] == "Externa Uno")
tr.quitar_participante(w, quien["id"])
assert op.espera_de(w)[0]["estado"] == "AVISADO"
assert any("Se liberó un lugar" in n["texto"] for n in db.listar_notificaciones(dani["id"], 10))
tr.agregar_participante(w, P(dani))
assert op.espera_de(w) == [], "al sumarse sale de la espera"
print("lista de espera OK")

# ---- 69/76/70) horario real, novedades, cierre con km y combustible (viaje de hoy)
t = viaje(hoy.isoformat(), U2, [P(ana)], salida="08:00", regreso="12:00")
tr.asignar(t, ch1, v4, direc)
DT = tr.obtener_viaje(t)["desplazamiento_id"]; assert DT
assert status("POST", "/traslados/{viaje_id:int}/horario-real", beto, {"salida_real": "08:10"}, viaje_id=t) == 403, "solo quien viaja, el chofer o la coordinación"
llamar("POST", "/traslados/{viaje_id:int}/horario-real", u_ch1, {"salida_real": "08:15", "regreso_real": "12:40"}, viaje_id=t)
vt = tr.obtener_viaje(t); assert (vt["hora_salida_real"], vt["hora_regreso_real"], vt["real_cargado_por"]) == ("08:15", "12:40", "Chofer Uno")
try:
    op.cargar_horas_reales(t, "12:00", "08:00", "x"); raise AssertionError
except tr.ErrorTraslado: pass
llamar("POST", "/traslados/{viaje_id:int}/novedad", ana, {"tipo": "DEMORA_INGRESO", "minutos": "45", "detalle": "Esperamos en la guardia"}, viaje_id=t)
llamar("POST", "/traslados/{viaje_id:int}/novedad", ana, {"tipo": "REQUISA"}, viaje_id=t)
assert len(op.novedades(t)) == 2
npu = op.novedades_por_unidad(hoy.isoformat(), hoy.isoformat())
assert npu[0]["total"] == 2 and any(x[0] == "Demora para ingresar" and x[2] == 45 for x in npu[0]["tipos"])
flota.actualizar_km(v4, 10000, "Dir")
try:
    op.cerrar_desplazamiento(DT, {"km_salida": "10000", "km_regreso": "9000"}, "x"); raise AssertionError
except tr.ErrorTraslado as e: assert "menos" in str(e)
assert status("POST", "/traslados/desplazamiento/{desp_id:int}/cerrar", ana, {}, desp_id=DT) == 403
llamar("POST", "/traslados/desplazamiento/{desp_id:int}/cerrar", u_ch1, {"salida_real": "08:15", "regreso_real": "12:40", "km_salida": "10000", "km_regreso": "10085", "litros": "9,5", "importe": "12000"}, desp_id=DT)
dd = op.obtener_desplazamiento(DT); assert dd["km_regreso"] == 10085 and dd["litros"] == 9.5 and dd["cerrado_por"] == "Chofer Uno"
assert tr.listar_vehiculos()[0]["km"] == 10085 or next(x for x in tr.listar_vehiculos() if x["id"] == v4)["km"] == 10085
cons = op.consumo(hoy.isoformat(), hoy.isoformat()); assert cons[0]["dominio"] == "AA100AA" and cons[0]["km"] == 85 and cons[0]["km_por_litro"] == 8.9
print("horario real, novedades y cierre OK")

# ---- 20) hora de regreso estimada (mediana de los viajes anteriores)
for i, (s, r) in enumerate((("09:00", "13:00"), ("09:00", "14:00"), ("10:00", "13:30"))):
    tr.crear_viaje(dict(fecha=(hoy - timedelta(days=7 * (i + 1))).isoformat(), hora_salida=s, hora_regreso=r, unidad_id=U3, modalidad="PROPIO"), [P(ana, True)], ana)
e = op.estimar_regreso(U3, None, "08:00")
assert e and e["base"] >= 3 and e["regreso"] == "12:00" and e["duracion"] == "04:00", e
assert op.estimar_regreso(None, "Lugar sin historial", "09:00") is None
assert llamar("GET", "/traslados/estimar-regreso", ana, unidad_id=str(U3), salida="10:00").body
print("regreso estimado OK")

# ---- 18) recordatorio personalizado (miércoles), una vez por día
mie = tr.lunes_de(hoy) + timedelta(days=2)
for k in range(1, 4):  # Beto va los martes a U1 hace 3 semanas
    tr.crear_viaje(dict(fecha=(tr.lunes_de(mie) - timedelta(days=7 * k) + timedelta(days=1)).isoformat(), hora_salida="09:00", unidad_id=U1, modalidad="PROPIO"), [P(beto, True)], beto)
prox_l = tr.lunes_de(mie) + timedelta(days=7)  # los jueves, el viaje «w» (hoy + 5) cae justo en ese martes: se aparta un momento
with db._conexion() as con:
    apartados = [(r["id"], r["estado"]) for r in con.execute("""SELECT id, estado FROM viajes WHERE unidad_id = ? AND fecha = ? AND id IN
                   (SELECT viaje_id FROM viaje_participantes WHERE usuario_id = ?)""", (U1, (prox_l + timedelta(days=1)).isoformat(), beto["id"]))]
    for vid_, _ in apartados: con.execute("UPDATE viajes SET estado = 'CANCELADO' WHERE id = ?", (vid_,))
n = op.recordatorio_personalizado(mie)
with db._conexion() as con:
    for vid_, est_ in apartados: con.execute("UPDATE viajes SET estado = ? WHERE id = ?", (est_, vid_))
assert n >= 2 and op.recordatorio_personalizado(mie) == 0
avisos_beto = [x["texto"] for x in db.listar_notificaciones(beto["id"], 5)]  # el aviso general puede llegar en el mismo segundo
nb = next((t for t in avisos_beto if "te faltan cargar" in t), "")
assert "martes" in nb, avisos_beto
assert any("vence la carga" in x["texto"] for x in db.listar_notificaciones(caro["id"], 5)), "a quien no le falta nada, el aviso general"
assert op.recordatorio_personalizado(mie + timedelta(days=2)) == 0, "solo miércoles y jueves"
print("recordatorio personalizado OK")

# ---- 79) comparación Excel ↔ sistema
import openpyxl
from datetime import time as _t
lun_x = date(2025, 10, 13)
tr.crear_viaje(dict(fecha="2025-10-13", hora_salida="10:00", destino_otro="Barrio Mitre", modalidad="PROPIO"), [P(ana, True)], ana)
tr.crear_viaje(dict(fecha="2025-10-13", hora_salida="15:00", destino_otro="Hospital", modalidad="PROPIO"), [P(ana, True)], ana)
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "OCTUBRE 13 AL 17"
ws.append([]); ws.append(["Completen"]); ws.append(["Lunes 13"] + [None] * 7 + ["Martes 14"])
ws.append(["Equipo", "Asesor", "Destino", "Detalle", "Observacion", "Horario", "Chofer", "Motivo"] * 2)
ws.append(["Eq", "Ana", "Otros", "Barrio Mitre", "particular", _t(9, 0), None, "visita"])
ws.append(["Eq", "Beto", "Otros", "Plaza Once", "particular", _t(11, 0), None, None])
xl = tmp / "v.xlsx"; wb.save(xl)
cmp = op.comparar_excel(str(xl))
assert len(cmp["diferencias"]) == 1 and "salida 09:00" in cmp["diferencias"][0]["diferencias"][0]
assert [e["destino"] for e in cmp["solo_excel"]] == ["Plaza Once"] and [v["destino"] for v in cmp["solo_sistema"]] == ["Hospital"]
print("comparación Excel OK")

# ---- funciones de presentación
bl, sh = ro.carriles([{"id": 1, "hora_salida": "09:00", "hora_regreso": "12:00"}, {"id": 2, "hora_salida": "10:00", "hora_regreso": "11:00"},
                      {"id": 3, "hora_salida": "13:00", "hora_regreso": "14:00"}, {"id": 4, "hora_salida": None}])
c = {b["id"]: b for b in bl}
assert c[1]["superpuesto"] and c[2]["superpuesto"] and not c[3]["superpuesto"] and c[1]["css"]["ancho"] == 50 and c[2]["css"]["izq"] == 50
assert c[3]["css"]["ancho"] == 100 and [x["id"] for x in sh] == [4]
bl, _ = ro.carriles([{"id": 1, "hora_salida": "09:00", "hora_regreso": "12:00", "chofer_id": 7, "vehiculo_id": 1},
                     {"id": 2, "hora_salida": "10:00", "hora_regreso": "11:00", "chofer_id": 7, "vehiculo_id": 2},
                     {"id": 3, "hora_salida": "10:00", "hora_regreso": "11:00", "chofer_id": 8, "vehiculo_id": 3}])
assert [b["choque"] for b in sorted(bl, key=lambda b: b["id"])] == [True, True, False], "mismo chofer en dos vehículos a la vez"
col = ro.columnas_del_dia([dict(id=1, estado="SOLICITADO", modalidad="CHOFER", fecha="2026-10-09"),
                           dict(id=2, estado="CONFIRMADO", modalidad="CHOFER", fecha="2026-10-09", hora_salida="08:00", hora_regreso="12:00"),
                           dict(id=3, estado="CONFIRMADO", modalidad="CHOFER", fecha="2026-10-09", hora_salida="14:00", hora_regreso="16:00"),
                           dict(id=4, estado="CONFIRMADO", modalidad="CHOFER", fecha="2026-10-09", hora_salida="06:00", hora_regreso="09:00"),
                           dict(id=5, estado="CONFIRMADO", modalidad="PROPIO", fecha="2026-10-09")], ahora=datetime(2026, 10, 9, 10, 0))
assert [[v["id"] for v in col[k]] for k in ("sin_chofer", "asignados", "en_curso", "realizados", "propios")] == [[1], [3], [2], [4], [5]]
g = ro.mes_grilla(2026, 10, [dict(fecha="2026-10-05", estado="SOLICITADO", modalidad="CHOFER"), dict(fecha="2026-10-05", estado="CONFIRMADO", modalidad="PROPIO"),
                             dict(fecha="2026-10-06", estado="CANCELADO", modalidad="CHOFER")])
d5 = next(x for s in g["semanas"] for x in s if x["fecha"] == "2026-10-05")
assert d5["total"] == 2 and d5["sin_chofer"] == 1 and d5["nivel"] == 4 and g["total"] == 2 and g["semanas"][0][0]["fecha"] == "2026-09-28"
print("carriles, columnas y mes OK")

# ---- pantallas y permisos
for p in ("/traslados/hoy", "/traslados/planificar", "/traslados/licencias", "/traslados/reservas"):
    assert status("GET", p, ana) == 403, p
h1 = llamar("GET", "/traslados/hoy", direc).body
assert "Tablero del día" in h1 and "Salidas de vehículos" in h1 and "Hoja de ruta" in h1 and "En curso" in h1
hc = llamar("GET", "/traslados/hoy", u_ch1).body
assert "Mis salidas" in hc and "Hoja de ruta" in hc and "Sin chofer" not in hc
assert status("GET", "/traslados/hoy", u_ch2) == 200
hh = llamar("GET", "/traslados/horas", ana, lunes=hoy.isoformat()).body
assert "Agenda por hora" in hh and "bloque-viaje" in hh
hm = llamar("GET", "/traslados/mes", ana).body
assert "calendario-mes" in hm and "viaje" in hm
hp = llamar("GET", "/traslados/planificar", direc, lunes=lunes.isoformat()).body
assert "Planificador" in hp and "Por ubicación del destino" in hp, "por defecto: solicitudes por lugar de destino"
hp = llamar("GET", "/traslados/planificar", direc, lunes=lunes.isoformat(), vista="propuesta").body
assert "Planificador" in hp and "sugerido" in hp
r = llamar("POST", "/traslados/planificar/aplicar", direc, {"lunes": lunes.isoformat(), "viaje_id": [str(a4)], "chofer_id": str(ch1), "vehiculo_id": str(v4)})
assert r.status_code == 303 and tr.obtener_viaje(a4)["estado"] == "CONFIRMADO"
assert "Cierre de la salida" in llamar("GET", "/traslados/desplazamiento/{desp_id:int}", u_ch1, desp_id=DT).body
assert status("GET", "/traslados/desplazamiento/{desp_id:int}", u_ch2, desp_id=DT) == 403, "otro chofer no"
hr = llamar("GET", "/traslados/desplazamiento/{desp_id:int}/hoja", direc, desp_id=DT).body
assert "Hoja de ruta" in hr and "Firma del chofer" in hr and "Ana" in hr
llamar("POST", "/traslados/licencias", direc, {"chofer_id": str(ch1), "desde": b, "hasta": b, "tipo": "FRANCO"})
hl = llamar("GET", "/traslados/licencias", direc).body
assert "Chofer Uno" in hl and "Franco" in hl
llamar("POST", "/traslados/reservas", direc, {"vehiculo_id": str(v8), "fecha": b, "motivo": "Acto en Ezeiza"})
assert "Acto en Ezeiza" in llamar("GET", "/traslados/reservas", direc).body
hd = llamar("GET", "/traslados/{viaje_id:int}", ana, viaje_id=t).body
assert "Horario real" in hd and "Novedades del viaje" in hd and "Demora para ingresar" in hd
assert "Asignar chofer" in llamar("GET", "/traslados/{viaje_id:int}", direc, viaje_id=w).body
hn = llamar("GET", "/traslados/nuevo", ana).body
assert "pasos-solicitud" in hn and "¿Cuándo?" in hn and "Revisá y confirmá" in hn and "estimar-regreso" in hn
hrep = llamar("GET", "/traslados/reportes", direc, desde=hoy.isoformat(), hasta=hoy.isoformat()).body
assert "Novedades por unidad" in hrep and "Kilometraje y combustible" in hrep
assert "Tablero del día" in llamar("GET", "/traslados", direc).body and "Por hora" in llamar("GET", "/traslados", ana).body
print("pantallas OK")
# el Excel de viajes se cierra al leerlo (en Windows, si queda abierto, no se puede borrar el temporal: error 500)
import openpyxl as _ox, importar_viajes as _iv
_cerrados = []; _orig = _ox.load_workbook
def _lw(*a, **k):
    wb = _orig(*a, **k); c0 = wb.close
    def _close(): _cerrados.append(1); c0()
    wb.close = _close; return wb
_ox.load_workbook = _lw
try:
    _iv.leer_excel(str(xl)); assert _cerrados, "leer_excel cierra el libro"
    op.comparar_excel(str(xl)); assert len(_cerrados) == 2, "comparar (con viajes ya cargados) también"
finally:
    _ox.load_workbook = _orig
print("TODO OK")
