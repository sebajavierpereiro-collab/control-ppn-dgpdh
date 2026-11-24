"""Metas de gestión por equipo: metas anuales (Dirección), carga de lo realizado (coordinación) y lo que sale de los viajes."""
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
def req(u, p="/", form=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session={}, cookies={},
                                 headers={}, query_params={}, form=_f, client=None)
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def falla(fn, codigo):
    try:
        fn(); raise SystemExit(f"esperaba {codigo}")
    except main.HTTPException as e:
        assert e.status_code == codigo, (e.status_code, e.detail)
R = main.app.rutas
from datetime import date as D, timedelta as TD
import traslados as tr, metas, io as _io, openpyxl as _ox

hoy = D.today(); anio = hoy.year
E2 = db.crear_equipo("Equipo CPF II")
coo2 = U("coo2", "Gisela Fernández", "COORDINADOR", E2)
a1, a2, a3 = U("dq", "Daniela Quiroga", "ASESOR", E2), U("of", "Oscar Funes", "ASESOR", E2), U("sd", "Sofía Díaz", "ASESOR", E2)
otro = U("otro", "Otro Asesor", "ASESOR", E1)
# un viaje realizado este año: 3 personas del equipo van a la misma unidad el mismo día
uni = next(u for u in db.listar_unidades() if tr.es_amba(u["id"]))
f1 = hoy - TD(days=2)
while f1.weekday() >= 5: f1 -= TD(days=1)
if f1.year != anio: f1 = D(anio, 1, 5)
P = lambda u: {"usuario_id": u["id"], "nombre": u["nombre"]}
v1 = tr.crear_viaje(dict(fecha=f1.isoformat(), motivo="Monitoreo", unidad_id=uni["id"], hora_salida="09:00", hora_regreso="13:00"), [P(a1), P(a2), P(a3)], a1)
with db._conexion() as con: con.execute("UPDATE viajes SET estado = 'CONFIRMADO' WHERE id = ?", (v1,))
ps = {p["usuario_id"]: p["id"] for p in tr.obtener_viaje(v1)["participantes"]}
import tablero_direccion as td
td.guardar_entrevistas(v1, {ps[a1["id"]]: 7, ps[a2["id"]]: 5, ps[a3["id"]]: 3}, direc)
mes = f1.month

via = metas.desde_viajes(anio)
assert via["equipo"][("equipo cpf ii", mes, "visitas_equipo")] == 1, "una visita del equipo por día y unidad aunque vayan 3"
assert all(via["persona"][(u["id"], mes, "visitas_equipo")] == 1 for u in (a1, a2, a3)), "cada persona suma 1 visita"
assert via["equipo"][("equipo cpf ii", mes, "entrevistas")] == 15, "audiencias del equipo = suma de las individuales"
assert via["persona"][(a1["id"], mes, "entrevistas")] == 7

# metas anuales: solo la Dirección
falla(lambda: R[("GET", "/metas/anuales")](req(coo2, "/metas/anuales"), ""), 403)
html = R[("GET", "/metas/anuales")](req(direc, "/metas/anuales"), str(anio)).body
assert "Equipo CPF II" in html and f'name="m_{E2}_entrevistas"' in html and "Visitas del equipo a unidades" in html
rr = req(direc, "/x", form={"anio": str(anio), f"m_{E2}_visitas_equipo": "40", f"m_{E2}_entrevistas": "600", f"m_{E2}_monitoreos": "4", f"m_{E2}_com_judicial": "abc"})
run(R[("POST", "/metas/anuales")](rr)); assert "3 valor(es)" in rr.session["flash"] and "no eran números" in rr.session["flash"]
assert metas.metas_anuales(anio)[E2] == {"visitas_equipo": 40, "entrevistas": 600, "monitoreos": 4}

# carga de lo realizado por la coordinación (mes): visitas y audiencias se ven, no se cargan
html = R[("GET", "/metas/carga")](req(coo2, "/metas/carga"), str(anio), f"M{mes:02d}", "").body
assert "DQ" in html and f'name="v_{a1["id"]}_monitoreos"' in html and f'name="v_{a1["id"]}_entrevistas"' not in html and "de los viajes" in html
falla(lambda: R[("GET", "/metas/carga")](req(coo2, "/metas/carga"), str(anio), "", str(E1)), 403)
rr = req(coo2, "/x", form={"anio": str(anio), "periodo": f"M{mes:02d}", "equipo_id": str(E2), f"v_{a1['id']}_monitoreos": "1", f"v_{a2['id']}_monitoreos": "1",
                           f"v_{a3['id']}_com_judicial": "6", f"v_{a1['id']}_requerimientos_remotos": "12"})
run(R[("POST", "/metas/carga")](rr)); assert "Guardado" in rr.session["flash"], rr.session["flash"]
t = (mes - 1) // 3 + 1
rr = req(coo2, "/x", form={"anio": str(anio), "periodo": f"T{t}", "equipo_id": str(E2), f"v_{a1['id']}_monitoreos": "3"})
run(R[("POST", "/metas/carga")](rr)); assert "ya se cargó por mes" in rr.session["flash"], "no se carga el mismo trimestre de dos formas"
falla(lambda: run(R[("POST", "/metas/carga")](req(otro, "/x", form={"anio": str(anio), "periodo": "M01", "equipo_id": str(E2)}))), 403)
# cada integrante carga lo suyo (las metas son del equipo entero); no puede tocar la columna de otra persona
html = R[("GET", "/metas/carga")](req(a2, "/metas/carga"), str(anio), f"M{mes:02d}", "").body
assert f'name="v_{a2["id"]}_monitoreos"' in html and f'name="v_{a1["id"]}_monitoreos"' not in html and "vos cargás tu columna" in html
rr = req(a2, "/x", form={"anio": str(anio), "periodo": f"M{mes:02d}", "equipo_id": str(E2), f"v_{a2['id']}_videollamadas": "2", f"v_{a2['id']}_monitoreos": "1", f"v_{a1['id']}_monitoreos": "9"})
run(R[("POST", "/metas/carga")](rr))
cg = {(f["usuario_id"], f["indicador"]): f["cantidad"] for f in metas.cargado(anio, E2)}
assert cg[(a2["id"], "videollamadas")] == 2 and cg[(a1["id"], "monitoreos")] == 1, "lo de otra persona no se cambia"

# seguimiento
sg = metas.seguimiento(anio, [next(e for e in db.listar_equipos() if e["id"] == E2)])["equipos"][0]
fila = {f["clave"]: f for f in sg["filas"]}
assert fila["visitas_equipo"]["hecho"] == 1 and fila["entrevistas"]["hecho"] == 15 and fila["monitoreos"]["hecho"] == 2 and fila["com_judicial"]["hecho"] == 6
assert fila["monitoreos"]["trimestres"][t - 1] == 2 and fila["monitoreos"]["pct"] == 50
pp = {p["usuario"]["id"]: p["valores"] for p in sg["por_persona"]}
assert pp[a1["id"]]["visitas_equipo"] == 1 and pp[a1["id"]]["entrevistas"] == 7 and pp[a1["id"]]["requerimientos_remotos"] == 12
html = R[("GET", "/metas")](req(coo2, "/metas"), str(anio), "").body
assert "Metas " + str(anio) in html and "Equipo CPF II" in html and "Por persona" in html and "Equipo Salud" not in html, "la coordinación ve solo su equipo"
html = R[("GET", "/metas")](req(direc, "/metas"), str(anio), "").body
assert "Avance por equipo" in html and "Equipo CPF II" in html
html = R[("GET", "/metas")](req(otro, "/metas"), "", "").body
assert "Equipo Salud" in html and "Equipo CPF II" not in html, "cada integrante ve su equipo"
mesa_ = U("mesa_m", "Mesa Sin Equipo", "MESA_ASESOR")
falla(lambda: R[("GET", "/metas")](req(mesa_, "/metas"), "", ""), 403)
# todo el equipo cuenta, no solo asesores y coordinación
sub_ = U("otro_rol", "Otra Persona Equipo", "MESA_ASESOR", E2)
assert sub_["id"] in {u["id"] for u in metas.integrantes(E2)}
# planilla con el mismo formato
r = R[("GET", "/metas.xlsx")](req(coo2, "/metas.xlsx"), str(anio), "")
cuerpo = b"".join(r.body_iterator) if hasattr(r, "body_iterator") and not hasattr(r.body_iterator, "__aiter__") else None
if cuerpo is None:
    async def _j(): return b"".join([x async for x in r.body_iterator])
    cuerpo = asyncio.run(_j())
lb = _ox.load_workbook(_io.BytesIO(cuerpo))
assert lb.sheetnames[:2] == ["Resumen mensual", "Metas y avance"] and f"{t}.º trimestre {anio}" in lb.sheetnames
h = lb[f"{t}.º trimestre {anio}"]
cab = [c.value for c in h[2] if c.value]
assert cab.index("TOTAL") == 5 and {"DQ", "GF", "OF", "SD", "OPE"} == set(cab[:5]), cab
print("metas de gestión OK")

# ---------------------------------------------------------------- correcciones de viajes y audiencias; ingreso a pabellones
# ingreso a pabellones: cada persona lo informa con sus audiencias y cuenta para las metas (1 por visita; el equipo 1 por día y unidad)
td.guardar_entrevistas(v1, {}, a1, pabellones={ps[a1["id"]]: True})
td.guardar_entrevistas(v1, {}, a2, pabellones={ps[a2["id"]]: True})
via = metas.desde_viajes(anio)
assert via["persona"][(a1["id"], mes, "ingreso_pabellones")] == 1 and (a3["id"], mes, "ingreso_pabellones") not in via["persona"]
assert via["equipo"][("equipo cpf ii", mes, "ingreso_pabellones")] == 1, "dos personas el mismo día y unidad: una vez para el equipo"
assert metas.POR_CLAVE["ingreso_pabellones"]["fuente"] == "viajes"
# corregir audiencias ya informadas: queda el antes y el después
rr = req(a1, "/x", form={f"p_{ps[a1['id']]}": "9", f"pabf_{ps[a1['id']]}": "1", "volver": "/traslados/entrevistas"})
run(R[("POST", "/traslados/{viaje_id:int}/entrevistas")](rr, v1))
assert rr.session["flash"] == "Audiencias guardadas.", rr.session
p1 = next(p for p in tr.obtener_viaje(v1)["participantes"] if p["usuario_id"] == a1["id"])
assert p1["entrevistas"] == 9 and p1["ingreso_pabellones"] == 0, "casilla sin tildar = no ingresó"
hist = tr.historial("viaje", v1)
assert any("Audiencias corregidas" in (h.get("accion") or "") and "7 audiencia" in (h.get("anterior") or "") for h in hist), hist
assert [x["id"] for x in td.entrevistas_informadas(a1["id"])] == [v1] and td.entrevistas_informadas(a1["id"])[0]["mis_entrevistas"] == 9
# otra persona no corrige lo de a1
rr = req(a2, "/x", form={f"p_{ps[a1['id']]}": "1"}); run(R[("POST", "/traslados/{viaje_id:int}/entrevistas")](rr, v1))
assert "propias" in rr.session["flash"]
# corregir un viaje realizado: fecha y destino, con motivo; no vuelve a pedir chofer
uni2 = next(u for u in db.listar_unidades() if u["id"] != uni["id"])
f0 = f1 - TD(days=1)
rr = req(a3, "/x", form={"fecha": f0.isoformat(), "hora_salida": "09:00", "hora_regreso": "13:00", "unidad_id": str(uni2["id"]), "motivo": ""})
run(R[("POST", "/traslados/{viaje_id:int}/corregir")](rr, v1)); assert "motivo" in rr.session["flash"] or "por qué" in rr.session["flash"]
rr = req(a3, "/x", form={"fecha": hoy.isoformat(), "hora_salida": "09:00", "hora_regreso": "13:00", "unidad_id": str(uni2["id"]), "motivo": "mal cargado"})
run(R[("POST", "/traslados/{viaje_id:int}/corregir")](rr, v1)); assert "anterior a hoy" in rr.session["flash"]
rr = req(a3, "/x", form={"fecha": f0.isoformat(), "hora_salida": "09:00", "hora_regreso": "13:00", "unidad_id": str(uni2["id"]), "motivo": "se cargó con fecha y unidad equivocadas"})
run(R[("POST", "/traslados/{viaje_id:int}/corregir")](rr, v1)); assert rr.session["flash"].startswith("Viaje corregido"), rr.session
vv = tr.obtener_viaje(v1)
assert vv["fecha"] == f0.isoformat() and vv["unidad_id"] == uni2["id"] and vv["estado"] == "CONFIRMADO" and vv["estado_efectivo"] == "REALIZADO"
assert any("Corrección: fecha" in (h.get("accion") or "") for h in tr.historial("viaje", v1))
falla(lambda: run(R[("POST", "/traslados/{viaje_id:int}/corregir")](req(otro, "/x", form={"fecha": f0.isoformat(), "motivo": "x"}), v1)), 403)
# sumar y quitar personas en un viaje realizado, sin controles de plazas y sin volver a pedir chofer
rr = req(a1, "/x", form={"usuario_id": str(coo2["id"])}); run(R[("POST", "/traslados/{viaje_id:int}/participantes")](rr, v1))
assert rr.session["flash"] == "Persona sumada." and any(p["usuario_id"] == coo2["id"] for p in tr.obtener_viaje(v1)["participantes"])
falla(lambda: run(R[("POST", "/traslados/{viaje_id:int}/participantes")](req(otro, "/x", form={"usuario_id": str(otro["id"])}), v1)), 403)
pc = next(p for p in tr.obtener_viaje(v1)["participantes"] if p["usuario_id"] == coo2["id"])
rr = req(a1, "/x"); run(R[("POST", "/traslados/{viaje_id:int}/participantes/{pid:int}/quitar")](rr, v1, pc["id"]))
assert tr.obtener_viaje(v1)["estado"] == "CONFIRMADO" and not any(p["usuario_id"] == coo2["id"] for p in tr.obtener_viaje(v1)["participantes"])
html = R[("GET", "/traslados/{viaje_id:int}")](req(a1, f"/traslados/{v1}"), v1).body
html = html.decode() if isinstance(html, bytes) else html
assert 'action="/traslados/%d/corregir"' % v1 in html and f'name="pab_{ps[a1["id"]]}"' in html
print("correcciones de viajes, audiencias y pabellones OK")

# ---------------------------------------------------------------- planillas de metas anteriores (importación)
import metas_importar as mi, json
anio_p = anio - 1
lb = _ox.Workbook(); h = lb.active; h.title = f"1er Trimestre {anio_p}"
h.append([None, "ENERO", None, None, None, "FEBRERO", None, None, None, "MARZO", None, None, None, "TOTAL TRIMESTRE"])
h.append([None, "DQ", "ZZ", None, "TOTAL", "DQ", "ZZ", None, "TOTAL", "DQ", "ZZ", None, "TOTAL", None])
h.append(["Visitas unidades (titulo)"] + [None] * 13)
h.append(["Visitas equipo", None, None, None, 5, None, None, None, 4, None, None, None, 3, 12])
h.append(["Entrevistas PPL en unidad", 10, 7, None, 17, 8, None, None, 8, 5, 5, None, 10, 35])
h.append(["Monitoreos", 1, None, None, 3, None, None, None, None, None, None, None, None, 3])   # TOTAL > suma: 2 sin asignar
h.append(["Llamadas raras", 2, None, None, 2] + [None] * 9)                                      # no reconocida
h.append([None, None, 1, None, 1] + [None] * 9)                                                     # suelta
r = lb.create_sheet("Hoja 1"); r.append([None, "Enero", "Febrero", "Marzo", "total"]); r.append(["Monitoreos", 3, 0, 0, 3])
bb = _io.BytesIO(); lb.save(bb)
L = mi.leer_planilla(bb.getvalue())
assert len(L["hojas"]) == 1 and L["hojas"][0]["anio"] == anio_p and L["hojas"][0]["trimestre"] == 1, L
hh = L["hojas"][0]
assert hh["siglas"] == ["DQ", "ZZ"] and [f["nombre"] for f in hh["no_reconocidas"]] == ["Llamadas raras"] and len(hh["sueltas"]) == 1
mon = next(f for f in hh["filas"] if f["indicador"] == "monitoreos")
assert mon["valores"][1]["personas"] == {"DQ": 1} and mon["valores"][1]["sin_asignar"] == 2
# vista previa por la pantalla: sube, sugiere DQ = Daniela Quiroga, ZZ sin usuario
class Sub:
    filename = "metas_cpf2.xlsx"
    async def read(self): return bb.getvalue()
falla(lambda: run(R[("POST", "/metas/importar")](req(a1, "/x", form={"equipo_id": str(E2), "planilla": Sub()}))), 403)  # asesor no importa
html = run(R[("POST", "/metas/importar")](req(coo2, "/x", form={"equipo_id": str(E2), "planilla": Sub()}))).body
html = html.decode() if isinstance(html, bytes) else html
assert "Persona anterior (ZZ)" in html and "Llamadas raras" in html, html[:2000]
import re as _re
tok = _re.search(r'name="token" value="([^"]+)"', html).group(1)
assert f'<option value="{a1["id"]}" selected>' in html, "sugiere a quien tiene esas iniciales"
form = {"token": tok, "hoja_0": "1", "anio_0": str(anio_p), "tri_0": "1", "sigla_DQ": str(a1["id"]), "sigla_ZZ": "",
        f"map_{hh['hoja']}|{hh['no_reconocidas'][0]['fila']}": "videollamadas", "modo": "reemplazar", "accion": "confirmar"}
rr = req(coo2, "/x", form=form); r_ = run(R[("POST", "/metas/importar/revisar")](rr))
assert rr.session["flash"].startswith("Planilla importada"), rr.session
cid = mi.cargas()[0]["id"]
tri_, per_ = metas.realizado(anio_p)
assert tri_[E2]["entrevistas"][0] == 35 and tri_[E2]["visitas_equipo"][0] == 12 and tri_[E2]["monitoreos"][0] == 3 and tri_[E2]["videollamadas"][0] == 2
assert per_[E2][(a1["id"], "entrevistas")] == 23 and per_[E2][(("sigla", "ZZ"), "entrevistas")] == 12 and per_[E2][(("equipo",), "monitoreos")] == 2
s_ = metas.seguimiento(anio_p, [db.listar_equipos()[[e["id"] for e in db.listar_equipos()].index(E2)]])["equipos"][0]
assert any(p["usuario"]["nombre"] == "Persona anterior (ZZ)" for p in s_["por_persona"]) and next(f for f in s_["filas"] if f["clave"] == "entrevistas")["segun_planilla"]
# la carga manual no pisa lo importado; el trimestre queda «por mes»
assert metas.modo_del_trimestre(E2, anio_p, 1) == "M"
metas.guardar_carga(E2, anio_p, "M01", {(a1["id"], "monitoreos"): 9, (a1["id"], "com_judicial"): 4}, coo2)
tri_, _ = metas.realizado(anio_p)
assert tri_[E2]["monitoreos"][0] == 3 and tri_[E2]["com_judicial"][0] == 4, "lo de la planilla no se edita a mano; lo demás sí"
g_ = metas.grilla_carga(E2, anio_p, "M01"); assert "monitoreos" in g_["de_planilla"] and g_["planilla"][(a1["id"], "entrevistas")] == 10
# segunda planilla del mismo período en modo «completar»: no toca lo que ya hay
L2 = mi.normalizar(json.loads(json.dumps(L)))
p2 = mi.previa(L2, E2, {hh["hoja"]: (anio_p, 1)})
assert p2["choques"], "avisa que el período ya tiene datos"
cid2, n2 = mi.aplicar(L2, E2, {hh["hoja"]: (anio_p, 1)}, {"DQ": a1["id"]}, "completar", coo2)
assert n2 == 0, "completar: no entra nada que ya estaba"
# vincular la sigla a un usuario después
rr = req(coo2, "/x", form={"sigla_ZZ": str(a2["id"])}); run(R[("POST", "/metas/cargas/{carga_id:int}/vincular")](rr, cid))
_, per_ = metas.realizado(anio_p); assert per_[E2][(a2["id"], "entrevistas")] == 12
# anular la carga: deja de contar
rr = req(coo2, "/x", form={"motivo": "equipo equivocado"}); run(R[("POST", "/metas/cargas/{carga_id:int}/anular")](rr, cid))
tri_, _ = metas.realizado(anio_p); assert tri_[E2]["entrevistas"][0] == 0 and tri_[E2]["monitoreos"][0] == 0
html = R[("GET", "/metas/cargas")](req(coo2, "/metas/cargas"), str(cid)).body
html = html.decode() if isinstance(html, bytes) else html
assert "Anulada" in html
print("importación de planillas de metas OK")
