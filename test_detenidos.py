"""ABM de personas detenidas: alta, validación, edición con historial, baja, búsqueda, importación, exportación, API y pantallas."""
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
import main, auth
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import detenidos as det
import types, io, csv, asyncio, json, re
from datetime import date, timedelta
import openpyxl
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1 = db.crear_equipo("Equipo Salud")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, mesa, chofer = U("ana", "Ana Pérez", "ASESOR", E1), U("mesa", "Marta Mesa", "MESA_ASESOR"), U("chof", "Carlos Chofer", "CHOFER")
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
CPF1 = next(u for u in db.listar_unidades() if "-CPF I-" in u["nombre"])
hoy = date.today()

# ---------------------------------------------------------------- reglas de datos
assert det.fecha("05/03/1990") == "1990-03-05" and det.fecha("1990-03-05") == "1990-03-05" and det.fecha("31/02/1990") is None and det.fecha("") == ""
assert det.edad(date(hoy.year - 30, 1, 1).isoformat()) in (29, 30)
assert det.es_adulto({"fecha_nacimiento": (hoy - timedelta(days=365 * 17)).isoformat()}) is False
assert det.es_adulto({"fecha_nacimiento": None, "es_adulto_manual": 1}) is True and det.es_adulto({}) is None

i1, err, av = det.guardar({"nombre_completo": "  Juan   Carlos Gómez ", "dni": "30.123.456", "lpu": "123456", "alias": "Pocho",
                           "fecha_nacimiento": "10/05/1985", "nro_expediente_ppn": "EX-2026-1", "codigo": "C-1", "unidad_id": str(CPF1["id"]),
                           "modulo": "III", "pabellon": "5", "juzgado": "Juzgado Federal N.º 2 de Morón"}, ana)
assert i1 and not err, err
d = det.obtener(i1)
assert d["nombre_completo"] == "Juan Carlos Gómez" and d["dni"] == "30123456" and d["edad"] >= 40 and d["es_adulto"] is True and d["centro"] == CPF1["nombre"]
# DNI y LPU no se repiten; fecha futura y DNI mal formado no pasan
_, err, _ = det.guardar({"nombre_completo": "Otro", "dni": "30123456"}, ana); assert any("DNI" in e for e in err), err
_, err, _ = det.guardar({"nombre_completo": "Otro", "lpu": "123-456"}, ana); assert any("LPU" in e for e in err), err
_, err, _ = det.guardar({"nombre_completo": "Otro", "fecha_nacimiento": (hoy + timedelta(days=3)).isoformat()}, ana); assert any("futura" in e for e in err)
_, err, _ = det.guardar({"nombre_completo": "Otro", "dni": "12ab"}, ana); assert err
_, err, _ = det.guardar({"nombre_completo": ""}, ana); assert "Falta el nombre completo." in err
# menor sin fecha, con «es adulto» manual
i2, err, av = det.guardar({"nombre_completo": "Lucas Menor", "lpu": "999", "es_adulto_manual": "0", "centro_otro": "Instituto San Martín"}, ana)
assert i2 and det.obtener(i2)["es_adulto"] is False and det.obtener(i2)["centro"] == "Instituto San Martín"
assert any("Sin DNI" in a for a in det.validar({"nombre_completo": "X"})[2])

# edición: historial campo por campo
det.guardar(dict(det.obtener(i1), pabellon="7", celda="12"), mesa, i1)
hist = det.historial(i1)
assert {(x["campo"], x["antes"], x["despues"]) for x in hist} >= {("pabellon", "5", "7"), ("celda", None, "12")} and hist[-1]["campo"] == "alta"

# búsqueda
assert [x["id"] for x in det.buscar("gomez")] == [i1] and [x["id"] for x in det.buscar("30123456")] == [i1]
assert [x["id"] for x in det.buscar("pocho")] == [i1] and [x["id"] for x in det.buscar("EX-2026")] == [i1]
assert [x["id"] for x in det.buscar(adultos=False)] == [i2] and [x["id"] for x in det.buscar(unidad_id=CPF1["id"])] == [i1]

# baja y reactivación: nunca se borra
det.dar_de_baja(i2, "Egreso", mesa)
assert det.buscar("lucas") == [] and det.buscar("lucas", incluir_bajas=True)[0]["motivo_baja"] == "Egreso"
assert det.contar() == {"activos": 1, "total": 2}
det.reactivar(i2, mesa); assert det.obtener(i2)["activo"] == 1

# notas que la nombran
doc = Document(); doc.add_paragraph("Al Sr. Director del -CPF I-"); doc.add_paragraph("Se entrevistó a GÓMEZ, Juan Carlos, DNI 30.123.456, alojado en el pabellón 7."); r = tmp / "n.docx"; doc.save(r)
nid = db.crear_nota("Nota Gómez.docx", E1, str(r), usuario_id=ana["id"])
db.actualizar_estado(nid, "PROCESANDO"); db.actualizar_estado(nid, "PROCESADA")
import automatico; automatico.clasificar_rapido(nid)
rel = det.notas_relacionadas(det.obtener(i1))
assert nid in [x["id"] for x in rel], ("la nota la nombra", rel, [dict(x) for x in db._conexion().__enter__().execute("SELECT * FROM nota_entidades")])

# ---------------------------------------------------------------- importación
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Listado de internos"])
ws.append(["Apellido y Nombre", "D.N.I.", "LPU", "Fecha de nacimiento", "Unidad", "Módulo", "Pabellón", "Juzgado", "Expediente PPN"])
ws.append(["Gómez, Juan Carlos", 30123456, "123456", "", "CPF I", "III", "8", "", ""])          # existente: actualiza pabellón
ws.append(["Pérez, María", "28.999.111", "", date(1980, 1, 2), "Complejo Penitenciario Federal II", "", "", "TOF 3", "EX-9"])  # nueva
ws.append(["Sin Documento", "", "", "", "", "", "", "", ""])                                   # sin DNI/LPU: alta con aviso
ws.append(["Fecha Mala", "11222333", "", "45/13/2000", "", "", "", "", ""])                     # error
ws.append(["Repetida", "28999111", "", "", "", "", "", "", ""])                                  # DNI repetido en la planilla
buf = io.BytesIO(); wb.save(buf)
regs, avisos = det.leer_archivo("internos.xlsx", buf.getvalue())
assert len(regs) == 5 and "Columnas reconocidas" in avisos[0], (regs, avisos)
assert regs[0]["unidad_id"] == CPF1["id"]
p = det.previa(regs)
assert len(p["nuevas"]) == 2 and len(p["actualizar"]) == 1 and len(p["errores"]) == 2, p
assert p["actualizar"][0][0] == i1 and p["actualizar"][0][2] == ["pabellon"], p["actualizar"]
res = det.aplicar_importacion(regs, mesa)
assert {k: res[k] for k in ("altas", "actualizadas", "errores")} == {"altas": 2, "actualizadas": 1, "errores": 2}, res
assert det.obtener(i1)["pabellon"] == "8" and det.obtener(i1)["juzgado"], "lo vacío en la planilla no pisa"
assert det.historial(i1)[0]["origen"] == "IMPORTADO"
maria = det.buscar("maria perez")[0]; assert maria["fecha_nacimiento"] == "1980-01-02" and maria["juzgado"] == "TOF 3" and maria["nro_expediente_ppn"] == "EX-9", maria
# CSV
csv_txt = "Nombre completo;DNI;Alias\nRoberto Díaz;20111222;Tito\n".encode("utf-8")
regs, _ = det.leer_archivo("x.csv", csv_txt); assert regs and regs[0]["alias"] == "Tito"
# planilla sin encabezados reconocibles
assert det.leer_archivo("x.csv", b"a;b\n1;2\n")[0] == []

# ---------------------------------------------------------------- exportación
lista = det.buscar(incluir_bajas=True, limite=1000)
wb = openpyxl.load_workbook(io.BytesIO(det.exportar_excel(lista)))
fila1 = [c.value for c in wb.active[1]]
for k in ("Nombre completo", "DNI", "LPU", "Alias", "Fecha de nacimiento", "Es adulto", "Edad", "Número de expediente PPN", "Código",
          "Centro de detención", "Módulo de detención", "Pabellón", "Juzgado"):
    assert k in fila1, k
assert wb.active.max_row == len(lista) + 1
filas = list(csv.reader(io.StringIO(det.exportar_csv(lista).decode("utf-8-sig")), delimiter=";"))
assert len(filas) == len(lista) + 1 and filas[0][1] == "Nombre completo"
j = det.a_json(det.obtener(i1)); assert j["dni"] == "30123456" and j["centro_detencion"] == CPF1["nombre"] and "nombre_norm" not in j
assert det.consultar_sistema_procuracion("1") is None, "sin configurar no sale a la red"

# ---------------------------------------------------------------- permisos
assert det.puede_ver(ana) and not det.puede_ver(chofer) and det.puede_administrar(mesa) and det.puede_administrar(direc) and not det.puede_administrar(ana)

# ---------------------------------------------------------------- pantallas
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def req(u, p="/", form=None, headers=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"]) if u else None), url=URL(p), session={}, cookies={},
                                 headers=headers or {}, query_params={}, form=_f, client=None)
R = main.app.rutas
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
html = R[("GET", "/detenidos")](req(ana, "/detenidos"), apellido="gomez").body
assert "Juan Carlos Gómez" not in html and "al menos dos" in html, "con un solo dato (sin DNI ni LPU) no busca"
html = R[("GET", "/detenidos")](req(ana, "/detenidos"), apellido="gomez", nombre="juan").body
assert "Juan Carlos Gómez" in html and "Nueva persona" in html and "Exportar" not in html, "el asesor no exporta"
html = R[("GET", "/detenidos")](req(mesa, "/detenidos")).body
assert "Exportar Excel" in html and "Importar planilla" in html and "Juan Carlos Gómez" not in html, "al abrir no trae la base"
assert "Revisión de la base" in html
html = R[("GET", "/detenidos")](req(mesa, "/detenidos"), edad="menores", bajas="", unidad_id="").body
assert "al menos dos" in html
html = R[("GET", "/detenidos")](req(mesa, "/detenidos"), dni=det.obtener(i1)["dni"]).body
assert "Juan Carlos Gómez" in html, "con el DNI solo alcanza"
try:
    R[("GET", "/detenidos")](req(chofer, "/detenidos")); raise SystemExit("el chofer no entra")
except main.HTTPException as e:
    assert e.status_code == 403
try:
    R[("GET", "/detenidos/exportar.{formato}")](req(ana), "xlsx"); raise SystemExit("el asesor no exporta")
except main.HTTPException as e:
    assert e.status_code == 403
html = R[("GET", "/detenidos/nueva")](req(ana, "/detenidos/nueva")).body
for k in ("Nombre completo", "DNI", "LPU", "Alias", "Fecha de nacimiento", "Es adulto", "Número de expediente PPN", "Código", "Centro de detención",
          "Módulo de detención", "Pabellón", "Juzgado"):
    assert k in html, k
r = run(R[("POST", "/detenidos/nueva")](req(ana, form={"nombre_completo": "Nuevo Pérez", "dni": "33444555", "fecha_nacimiento": "2001-02-03"})))
assert r.status_code == 303 and r.headers["location"].startswith("/detenidos/")
nuevo = int(r.headers["location"].rsplit("/", 1)[1])
r = run(R[("POST", "/detenidos/nueva")](req(ana, form={"nombre_completo": "Dup", "dni": "33444555"})))
assert "Ya existe una persona con ese DNI" in r.body and 'value="Dup"' in r.body, "vuelve al formulario con lo cargado"
html = R[("GET", "/detenidos/{det_id}")](req(mesa, f"/detenidos/{i1}"), i1).body
assert "Historial de cambios" in html and "Nota Gómez.docx" in html and "Dar de baja" in html and "Pabellón" in html
html = R[("GET", "/detenidos/{det_id}")](req(ana, f"/detenidos/{i1}"), i1).body
assert "Dar de baja" not in html
assert det.accesos(i1) and det.accesos(i1)[0]["usuario"] == "Ana Pérez", "queda registro de quién miró la ficha"
r = run(R[("POST", "/detenidos/{det_id}/editar")](req(ana, form=dict({k: (v if v is not None else "") for k, v in det.obtener(nuevo).items() if k in det.ETIQUETA}, alias="Nuevito")), nuevo))
assert r.status_code == 303 and det.obtener(nuevo)["alias"] == "Nuevito"
r = run(R[("POST", "/detenidos/{det_id}/baja")](req(mesa, form={"motivo": ""}), nuevo)); assert det.obtener(nuevo)["activo"] == 1, "sin motivo no hay baja"
r = run(R[("POST", "/detenidos/{det_id}/baja")](req(mesa, form={"motivo": "Egreso"}), nuevo)); assert det.obtener(nuevo)["activo"] == 0
# importación por pantalla: previa → confirmar
class Archivo:
    filename = "p.csv"
    async def read(self): return "Nombre completo;DNI\nAna Torres;27111000\n".encode()
r = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": Archivo()})))
assert r.status_code == 303, "sin fecha ni fuerza no hay vista previa"
r = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": Archivo(), "fecha_listado": "2999-01-01", "fuerza_id": "1"})))
assert r.status_code == 303, "la fecha del listado no puede ser futura"
r = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": Archivo(), "fecha_listado": "2026-10-01", "fuerza_id": ""})))
assert r.status_code == 303, "hay que elegir la fuerza"
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": Archivo(), "fecha_listado": "2026-10-01", "fuerza_id": "1"}))).body
assert "Confirmar importación" in html and "Fecha del listado: <strong>01/10/2026" in html and "Ana Torres" not in html, "las secciones se cargan al desplegarlas"
token = re.search(r'name="token" value="([^"]+)"', html).group(1)
sec = R[("GET", "/detenidos/importar/seccion")](req(mesa, "/x"), token, "nuevas", "", "", "1").body
assert "Ana Torres" in sec
try:
    run(R[("POST", "/detenidos/importar/confirmar")](req(direc, form={"token": token}))); raise SystemExit("otra persona no confirma")
except main.HTTPException as e:
    assert e.status_code == 403
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": Archivo(), "fecha_listado": "2026-10-01", "fuerza_id": "1"}))).body
token = re.search(r'name="token" value="([^"]+)"', html).group(1)
r = run(R[("POST", "/detenidos/importar/confirmar")](req(mesa, form={"token": token})))
assert r.status_code == 303 and det.buscar("27111000")
r = run(R[("POST", "/detenidos/importar/confirmar")](req(mesa, form={"token": token}))); assert r.headers["location"] == "/detenidos/importar", "el token se usa una sola vez"
r = R[("GET", "/detenidos/exportar.{formato}")](req(mesa), "xlsx", bajas="1"); assert r.media_type.startswith("application/vnd.openxml")
# API con token
try:
    R[("GET", "/api/detenidos")](req(None, headers={"authorization": "Bearer x"})); raise SystemExit("sin api_token configurado está apagada")
except main.HTTPException as e:
    assert e.status_code == 401
det.EXTERNO["api_token"] = "secreto-largo"
r = R[("GET", "/api/detenidos")](req(None, headers={"authorization": "Bearer secreto-largo"}), dni="30.123.456")
j = json.loads(r.body); assert j["total"] == 1 and j["personas"][0]["lpu"] == "123456"
try:
    R[("GET", "/api/detenidos")](req(None, headers={"authorization": "Bearer otro"})); raise SystemExit("token incorrecto")
except main.HTTPException as e:
    assert e.status_code == 401
html = R[("GET", "/detenidos/importar")](req(direc)).body; assert "planillas de expedientes de Mesa" in html
print("OK detenidos")

# ---- fuerza y fecha del listado: el alojamiento solo cambia con un listado más nuevo
fz = det.crear_fuerza("Policía de Mendoza", "PM", mesa); assert det.crear_fuerza("policia de mendoza") == fz, "no duplica fuerzas"
try:
    det.crear_fuerza("x"); raise SystemExit("nombre de fuerza muy corto")
except ValueError:
    pass
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": Archivo(), "fecha_listado": "2026-10-02", "fuerza_id": "nueva",
                                                          "fuerza_nueva": "Alcaidía Provincial Test", "fuerza_sigla": "APT"}))).body
assert "Alcaidía Provincial Test" in html and any(f["nombre"] == "Alcaidía Provincial Test" for f in det.listar_fuerzas()), "crea la fuerza al importar"
uni = db.listar_unidades(solo_activas=False)
u1, u2 = uni[0], uni[1]
base = [{"nombre_completo": "Listado Fecha Uno", "dni": "94111222", "unidad_id": u1["id"], "pabellon": "A", "nacionalidad": "Paraguaya", "juzgado": "Juzgado Federal 3"}]
r = det.aplicar_importacion(base, mesa, fecha_listado="2026-10-10", fuerza_id=1, archivo="spf_10.xlsx"); assert r["altas"] == 1
pid = det.buscar("94111222")[0]["id"]; d = det.obtener(pid)
assert d["fecha_listado"] == "2026-10-10" and d["fuerza"] and d["vigencia"] in ("al_dia", "desactualizado")
viejo = [dict(base[0], unidad_id=u2["id"], pabellon="Z")]
p = det.previa(viejo, "2026-10-05"); assert len(p["conservado"]) == 1 and p["aloj_actualiza"] == 0, p
r = det.aplicar_importacion(viejo, mesa, fecha_listado="2026-10-05", fuerza_id=1, archivo="spf_05.xlsx")
d = det.obtener(pid); assert d["unidad_id"] == u1["id"] and d["pabellon"] == "A" and d["fecha_listado"] == "2026-10-10", "listado anterior conserva el alojamiento"
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM detenidos_cambios WHERE detenido_id = ? AND campo = 'alojamiento'", (pid,)).fetchone()[0] >= 1, "queda auditado"
r = det.aplicar_importacion(viejo, mesa, fecha_listado="2026-10-20", fuerza_id=fz, archivo="spf_20.xlsx")
d = det.obtener(pid); assert d["unidad_id"] == u2["id"] and d["pabellon"] == "Z" and d["fecha_listado"] == "2026-10-20" and d["fuerza_id"] == fz, "listado más nuevo actualiza"
assert det.listados(5)[0]["archivo"] == "spf_20.xlsx"
assert det.vigencia({"fecha_listado": "2000-01-01"}) == "desactualizado" and det.vigencia({}) == "sin_fecha"

# ---- búsqueda colectiva
lista, res = det.busqueda_colectiva(nacionalidades=["paraguaya"])
assert any(x["id"] == pid for x in lista) and all((x["nacionalidad"] or "").lower() == "paraguaya" for x in lista)
assert det.busqueda_colectiva(nacionalidades=["paraguaya"], fuerza_id=1)[0] == [] or all(x["fuerza_id"] == 1 for x in det.busqueda_colectiva(fuerza_id=1)[0])
assert [x["id"] for x in det.busqueda_colectiva(juzgado="federal 3")[0]] == [pid]
assert det.busqueda_colectiva(juzgado="federal 3", tipo_unidad="NoExiste")[0] == []
assert res["total"] == len(lista) and res["nacionalidades"][0][0] == "Paraguaya"
class QP(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
    def multi_items(self): return [(k, x) for k, v in self.items() for x in (v if isinstance(v, list) else [v])]
def reqq(u, q, form=None):
    r = req(u, "/detenidos/busqueda", form); r.query_params = QP(q); r.method = "POST" if form is not None else "GET"; return r
html = R[("GET", "/detenidos/busqueda")](reqq(ana, {"juzgado": "federal 3"})).body
assert "Listado Fecha Uno" in html and "Gráficos del grupo" in html and "Exportar todo" not in html, "el asesor busca pero no exporta"
html = R[("GET", "/detenidos/busqueda")](reqq(mesa, {"nacionalidad": ["Paraguaya"]})).body
assert "Exportar todo a Excel" in html and "nacionalidad=Paraguaya" in html
try:
    R[("GET", "/detenidos/busqueda")](reqq(chofer, {})); raise SystemExit("el chofer no entra a la búsqueda")
except main.HTTPException as e:
    assert e.status_code in (401, 403)
try:
    run(R[("GET", "/detenidos/busqueda/exportar.{formato}")](reqq(ana, {}), "csv")); raise SystemExit("el asesor no exporta")
except main.HTTPException as e:
    assert e.status_code == 403
r = run(R[("GET", "/detenidos/busqueda/exportar.{formato}")](reqq(mesa, {"juzgado": "federal 3"}), "csv"))
assert "Listado Fecha Uno" in r.body.decode("utf-8-sig") and "fuerza" in r.body.decode("utf-8-sig").lower()
r = run(R[("GET", "/detenidos/busqueda/exportar.{formato}")](reqq(mesa, {}, form={"id": [str(pid)]}), "csv"))
filas = [l for l in r.body.decode("utf-8-sig").splitlines() if l.strip()]; assert len(filas) == 2 and "Listado Fecha Uno" in filas[1], "exporta solo las marcadas"
r = run(R[("GET", "/detenidos/busqueda/exportar.{formato}")](reqq(mesa, {}), "xlsx")); assert r.media_type.startswith("application/vnd.openxml")
html = R[("GET", "/detenidos/importar")](req(mesa)).body; assert "Fecha del listado" in html and "spf_20.xlsx" in html and "Planilla del listado" in html and html.count("fechaDelNombre(f.name)") == 1
print("OK fuerza, fecha del listado y búsqueda colectiva")

# ---- lugar que no está en el catálogo: vincular a una unidad existente o dejar el nombre como figura
destino = db.listar_unidades()[2]
class Arch2:
    filename = "listado_05-11-2026.csv"
    async def read(self): return ("Nombre completo;DNI;Unidad\nVinc Uno;41000001;Penal Inventado Norte\nVinc Dos;41000002;Penal Inventado Norte\n"
                                  "Queda Igual;41000003;Comisaría Lejana 7\n").encode()
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": Arch2(), "fecha_listado": "2026-11-05", "fuerza_id": "1"}))).body
assert "Lugares de alojamiento que no están cargados" in html and "Penal Inventado Norte" in html and "Comisaría Lejana 7" in html
i_pen = 0 if html.index('Penal Inventado Norte') < html.index('Comisaría Lejana 7') else 1
token = re.search(r'name="token" value="([^"]+)"', html).group(1)
r = run(R[("POST", "/detenidos/importar/confirmar")](req(mesa, form={"token": token, f"vinculo_{i_pen}": str(destino["id"]), "recordar_vinculos": "1"})))
a, b, c = (det.obtener(det.buscar(x)[0]["id"]) for x in ("41000001", "41000002", "41000003"))
assert a["unidad_id"] == b["unidad_id"] == destino["id"] and not a["centro_otro"], "vinculadas a la unidad elegida"
assert c["unidad_id"] is None and c["centro_otro"] == "Comisaría Lejana 7" and c["centro"] == "Comisaría Lejana 7", "sin vincular queda como figura"
assert "Penal Inventado Norte" in (db.obtener_unidad(destino["id"])["alias"] or ""), "se recuerda como alias"
regs, _ = det.leer_archivo("x.csv", "Nombre completo;DNI;Unidad\nOtra Vez;41000009;Penal Inventado Norte\n".encode())
assert regs[0]["unidad_id"] == destino["id"] and not det.centros_sin_catalogo(regs), "el próximo listado la reconoce solo"
assert det.vincular_centros([{"centro_otro": "X"}], {"X": "999999"}) == 0, "no vincula a unidades inexistentes"
print("OK vincular lugares al catálogo")

# ---- planillas grandes: la vista previa no se cuelga (antes consultaba la base por fila y comparaba cada DNI con todos)
import time as _t
lib = openpyxl.Workbook(); hh = lib.active
hh.append(["Apellido y nombre", "DNI", "LPU", "Unidad"])
for i in range(6000): hh.append([f"Masiva Persona {i}", 30000000 + i, f"9{i:06d}", "Complejo Penitenciario Federal I"])
hh.append(["Repetida Por DNI", 30000001, "", ""])
bb = io.BytesIO(); lib.save(bb)
t0 = _t.time(); regs, _ = det.leer_archivo("grande.xlsx", bb.getvalue()); p = det.previa(regs); dt = _t.time() - t0
assert len(p["nuevas"]) == 6000 and len(p["errores"]) == 1 and p["errores"][0]["tipo"] == "repetido" and p["errores"][0]["ref"] == 2, p["errores"][:2]
assert dt < 20, f"vista previa demasiado lenta: {dt:.1f}s"
t0 = _t.time(); r = det.aplicar_importacion(regs, {"id": 1, "nombre": "Prueba"}); dt = _t.time() - t0
assert r["altas"] == 6000 and r["errores"] == 1 and dt < 30, (r, dt)
regs[0]["juzgado"] = "Juzgado Federal 2"
p = det.previa(regs[:10]); assert len(p["actualizar"]) == 1 and not p["nuevas"], "la segunda vez actualiza, no duplica"
print("OK planillas grandes")

# ---- fuerza en el listado y anulación de una carga completa
html = R[("GET", "/detenidos")](req(mesa, "/detenidos"), dni="94111222").body
assert "<th scope=\"col\">Fuerza</th>" in html and "Policía de Mendoza" in html, "el listado muestra la fuerza"
antes_ = det.obtener(pid)
base_ = [{"nombre_completo": "Carga Erronea Uno", "dni": "42000001", "nacionalidad": "Argentina"},
         {"nombre_completo": "Carga Erronea Dos", "dni": "42000002"},
         {"nombre_completo": antes_["nombre_completo"], "dni": antes_["dni"], "juzgado": "Juzgado Equivocado", "pabellon": "ZZ"}]
r_ = det.aplicar_importacion(base_, mesa, fecha_listado="2026-11-01", fuerza_id=2, archivo="error.xlsx")
assert r_["altas"] == 2 and r_["actualizadas"] == 1
lid_ = det.listados(1)[0]["id"]
d_ = det.obtener(pid); assert d_["juzgado"] == "Juzgado Equivocado" and d_["listado_id"] == lid_
# alguien corrige a mano después de la carga: se respeta
det.guardar(dict({k: (v if v is not None else "") for k, v in det.obtener(pid).items() if k in det.ETIQUETA}, pabellon="Corregido a mano"), mesa, pid)
html = R[("GET", "/detenidos/listados")](req(mesa, "/detenidos/listados"), ver=str(lid_)).body
assert "error.xlsx" in html and "Anular la carga completa" in html and "2</strong> persona(s) que dio de alta" in html
try:
    R[("GET", "/detenidos/listados")](req(ana, "/detenidos/listados"), ver=""); raise SystemExit("el asesor no ve las cargas")
except main.HTTPException as e:
    assert e.status_code == 403
rr = req(mesa, "/x", form={"motivo": ""}); run(R[("POST", "/detenidos/listados/{listado_id:int}/anular")](rr, lid_))
assert "por qué" in rr.session["flash"], "pide motivo"
rr = req(mesa, "/x", form={"motivo": "Se subió el listado equivocado"}); r = run(R[("POST", "/detenidos/listados/{listado_id:int}/anular")](rr, lid_))
assert "2 persona(s)" in rr.session["flash"] and "se respetaron" in rr.session["flash"], rr.session["flash"]
for dni_ in ("42000001", "42000002"):
    x_ = det.buscar(dni_, incluir_bajas=True)[0]; assert not x_["activo"] and x_["motivo_baja"].startswith(det.ANULADA)
assert not det.buscar("42000001"), "no aparecen entre los activos"
d_ = det.obtener(pid)
assert d_["juzgado"] == antes_["juzgado"] and d_["pabellon"] == "Corregido a mano", "vuelve lo anterior; lo corregido a mano se respeta"
assert d_["listado_id"] == antes_["listado_id"] and d_["fecha_listado"] == antes_["fecha_listado"] and d_["fuerza_id"] == antes_["fuerza_id"]
l_ = det.listados(1)[0]; assert l_["anulado_en"] and l_["motivo_anulacion"] == "Se subió el listado equivocado"
try:
    det.anular_listado(lid_, "otra vez", mesa); raise SystemExit("no se anula dos veces")
except ValueError:
    pass
# si después el listado correcto trae a una de esas personas, vuelve a estar activa
det.aplicar_importacion([{"nombre_completo": "Carga Erronea Uno", "dni": "42000001"}], mesa, fecha_listado="2026-11-02", fuerza_id=1, archivo="bueno.xlsx")
assert det.buscar("42000001")[0]["activo"]
print("anular carga completa OK")
# cargas hechas antes de esta versión (sin marca en el historial): se reconocen por hora y usuario
r_ = det.aplicar_importacion([{"nombre_completo": "Legado Uno", "dni": "43000001"}], mesa, fecha_listado="2026-11-03", fuerza_id=1, archivo="viejo.xlsx")
lid2 = det.listados(1)[0]["id"]
with db._conexion() as con:  # (en la prueba todo pasa en el mismo segundo: se separa la hora de esta carga)
    con.execute("UPDATE detenidos_cambios SET listado_id = NULL, fecha = '2030-01-01T00:00:01+00:00' WHERE listado_id = ?", (lid2,))
    con.execute("UPDATE detenidos_listados SET creado_en = '2030-01-01T00:00:00+00:00' WHERE id = ?", (lid2,))
assert det.resumen_anulacion(lid2)["exacto"] is False and len(det.resumen_anulacion(lid2)["altas"]) == 1
r_ = det.anular_listado(lid2, "prueba", mesa); assert r_["bajas"] == 1 and not r_["exacto"]
print("anular carga anterior a esta versión OK")

# ---------------------------------------------------------------- limpieza, condena, alojamiento parcial, egresos del SPF e informe
assert det.solo_digitos("DNI: 30.123.456") == "30123456" and det.solo_digitos(30123456.0) == "30123456" and det.solo_digitos("LPU 12-34/5") == "12345"
assert det.separar_nombre("GÓMEZ PÉREZ Juan") == ("GÓMEZ PÉREZ", "Juan") and det.separar_nombre("Paz, Ana") == ("Paz", "Ana") and det.separar_nombre("Ana Paz") == (None, None)
assert det.leer_condena("5 años y 3 meses") == (5, 3, 0) and det.leer_condena("") == (None, None, None)
# planilla con apellido y nombres separados, DNI y LPU sucios, condena en texto y fecha de cumplimiento
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Apellido", "Nombres", "Documento", "LPU", "Unidad", "Módulo", "Pabellón", "Situación procesal", "Condena", "Fecha de cumplimiento"])
ws.append(["SPFUNO", "Ana", "DNI: 44.000.001", "LPU 900-001", u1["nombre"], "I", "1", "Condenada", "5 años 3 meses", "10/05/2030"])
ws.append(["SPFDOS", "Beto", "44.000.002", "", u1["nombre"], "II", "2", "Procesado", "", ""])
ws.append(["SPFTRES", "Caro", "44000003", "", u2["nombre"], "III", "3", "", "", ""])
ws.append(["SINDNI", "Dora", "", "", u2["nombre"], "", "", "", "", ""])
buf = io.BytesIO(); wb.save(buf)
regs, av = det.leer_archivo("spf_a.xlsx", buf.getvalue())
assert regs[0]["nombre_completo"] == "SPFUNO, Ana" and regs[0]["condena_anios"] == 5 and regs[0]["condena_meses"] == 3, regs[0]
assert "columnas separadas" in av[0]
spf = next(f["id"] for f in det.listar_fuerzas(False) if f["sigla"] == "SPF")
r = det.aplicar_importacion(regs, mesa, fecha_listado="2026-11-01", fuerza_id=spf, archivo="spf_a.xlsx", egresos="todos")
assert r["altas"] == 4, r
ana_ = det.buscar("44000001")[0]
assert ana_["dni"] == "44000001" and ana_["lpu"] == "900001" and ana_["apellido"] == "SPFUNO" and ana_["nombres"] == "Ana"
assert ana_["fecha_cumplimiento"] == "2030-05-10" and ana_["condenado"] and ana_["estado_actual"] == "Activo / alojado"
assert det.buscar("SINDNI")[0]["dni"] is None, "sin DNI se toma igual"
inf = det.informe_de(r["listado_id"])["informe"]
assert inf["unidades_detectadas"] == 2 and len(inf["altas"]) == 4 and inf["condenados"] == 1 and inf["procesados"] == 1, inf
# listado posterior del SPF: misma unidad sin módulo/pabellón conserva; otra unidad sin detalle no arrastra el viejo; SPFTRES no figura
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Apellido y Nombre", "DNI", "Unidad", "Módulo", "Pabellón"])
ws.append(["SPFUNO, Ana", "44000001", u1["nombre"], "", ""])
ws.append(["SPFDOS, Beto", "44000002", u2["nombre"], "", ""])
ws.append(["SINDNI, Dora", "", u2["nombre"], "", ""])
buf = io.BytesIO(); wb.save(buf)
regs2, _ = det.leer_archivo("spf_b.xlsx", buf.getvalue())
p = det.previa(regs2, "2026-11-08", spf)
nombres_egr = {e["nombre"] for e in p["egresos"]}
assert "SPFTRES, Caro" in nombres_egr and "SPFUNO, Ana" not in nombres_egr, p["egresos"]
assert {m["nombre"] for m in p["mudanzas"]} == {"SPFDOS, Beto"}, p["mudanzas"]
# otra fuerza no da egresos
assert det.previa(regs2, "2026-11-08", fz)["egresos"] == []
r2 = det.aplicar_importacion(regs2, mesa, fecha_listado="2026-11-08", fuerza_id=spf, archivo="spf_b.xlsx", egresos="todos")
a_, b_ = det.buscar("44000001")[0], det.buscar("44000002")[0]
assert a_["modulo"] == "I" and a_["pabellon"] == "1", "misma unidad sin detalle: se conserva"
assert b_["unidad_id"] == u2["id"] and b_["modulo"] is None and b_["pabellon"] is None, "otra unidad: el detalle viejo no se arrastra"
c_ = det.buscar("44000003", incluir_bajas=True)[0]
assert not c_["activo"] and c_["motivo_baja"].startswith(det.EGRESO_SPF) and c_["estado_actual"] == "Egresado"
assert any(h["campo"] == "egreso" for h in det.historial(c_["id"])), "egreso en el historial"
inf2 = det.informe_de(r2["listado_id"])["informe"]
assert [e["nombre"] for e in inf2["egresos"]] == [e["nombre"] for e in p["egresos"]] and len(inf2["mudanzas"]) == 1 and [x["nombre"] for x in inf2["altas"]] == ["SINDNI, Dora"], "sin DNI: alta nueva hasta que se decida"
html = R[("GET", "/detenidos/listados/{listado_id:int}")](req(mesa, f"/detenidos/listados/{r2['listado_id']}"), r2["listado_id"]).body
html = html.decode() if isinstance(html, bytes) else html
assert "SPFTRES, Caro" not in html and "Egresadas del listado del SPF" in html, "las listas se cargan al desplegar"
sec = R[("GET", "/detenidos/listados/{listado_id:int}/seccion")](req(mesa, "/x"), r2["listado_id"], "egresos", "", "1").body
sec = sec.decode() if isinstance(sec, bytes) else sec
assert "SPFTRES, Caro" in sec
# anular la carga revierte el egreso
det.anular_listado(r2["listado_id"], "prueba de egresos", mesa)
assert det.buscar("44000003")[0]["activo"], "anular la carga deshace el egreso"
# si vuelve a figurar en un listado posterior, reingresa
det.aplicar_importacion(regs2, mesa, fecha_listado="2026-11-09", fuerza_id=spf, archivo="spf_c.xlsx", egresos="todos")
assert not det.buscar("44000003", incluir_bajas=True)[0]["activo"]
r4 = det.aplicar_importacion([{"nombre_completo": "SPFTRES, Caro", "dni": "44000003", "unidad_id": u2["id"]}], mesa, fecha_listado="2026-11-09",
                             fuerza_id=spf, archivo="spf_d.xlsx")
assert det.buscar("44000003")[0]["activo"] and r4["reingresos"] == 1
# la edición manual no borra datos que el formulario no trae (p. ej. la condena)
ana_id = det.buscar("44000001")[0]["id"]
rr = req(mesa, "/x", form={"nombre_completo": "SPFUNO, Ana", "dni": "44000001"}); run(R[("POST", "/detenidos/{det_id}/editar")](rr, ana_id))
assert det.obtener(ana_id)["condena_anios"] == 5
print("limpieza, condena, alojamiento parcial, egresos SPF e informe OK")

# ---------------------------------------------------------------- coincidencias solo por nombre y revisión de la base
regs_n = [{"nombre_completo": "PEREZ SINDOC, Juana", "unidad_id": u1["id"]}]
det.aplicar_importacion(regs_n, mesa, fecha_listado="2026-11-01", fuerza_id=fz, archivo="n1.xlsx")
pj = det.buscar_campos(apellido="perez sindoc", nombre="juana")
assert len(pj) == 1 and pj[0]["apellido"] == "PEREZ SINDOC"
p_ = det.previa(regs_n, "2026-11-02", fz)
assert len(p_["posibles"]) == 1 and len(p_["nuevas"]) == 1 and not p_["actualizar"], "sin DNI no se une sola: muestra las dos opciones"
assert len(det.previa(regs_n, "2026-11-02", fz, {"1": "misma"})["actualizar"]) == 1, "si se decide «misma», actualiza"
det.aplicar_importacion(regs_n, mesa, fecha_listado="2026-11-02", fuerza_id=fz, archivo="n2.xlsx")  # decidir después: alta nueva + alerta
dos = det.buscar_campos(apellido="perez sindoc", nombre="juana")
assert len(dos) == 2
al = det.alertas()
clave = det.clave_duplicado(dos[0]["id"], dos[1]["id"])
assert any(a["clave"] == clave for a in al["duplicado"]), "aparece en Revisión de la base"
assert any(a["clave"] == f"sindoc:{dos[0]['id']}" for a in al["sin_documento"])
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), "duplicado", "").body
assert "PEREZ SINDOC, Juana" in html and "unificar" in html
try:
    R[("GET", "/detenidos/revision")](req(ana, "/detenidos/revision"), "", ""); raise SystemExit("solo administra quien puede")
except main.HTTPException as e:
    assert e.status_code == 403
rr = req(mesa, "/x", form={"clave": clave, "tipo": "duplicado", "decision": "unificar", "mantener": str(dos[0]["id"])})
run(R[("POST", "/detenidos/revision")](rr))
assert "unificada" in rr.session["flash"], rr.session
assert len(det.buscar_campos(apellido="perez sindoc", nombre="juana")) == 1
b_ = det.obtener(dos[1]["id"]); assert not b_["activo"] and b_["motivo_baja"].startswith("Unificada con")
assert not any(a["clave"] == clave for a in det.alertas()["duplicado"])
# otra persona al importar: queda decidido y no alerta
det.aplicar_importacion(regs_n, mesa, fecha_listado="2026-11-03", fuerza_id=fz, archivo="n3.xlsx", decisiones={"1": "otra"})
assert not det.alertas()["duplicado"] or all("PEREZ SINDOC" not in a["personas"][0]["nombre_completo"] for a in det.alertas()["duplicado"])
# pena cumplida
with db._conexion() as con:
    con.execute("UPDATE detenidos SET fecha_cumplimiento = '2020-01-01' WHERE id = ?", (dos[0]["id"],))
assert any(a["clave"] == f"pena:{dos[0]['id']}" for a in det.alertas()["pena_cumplida"])
det.decidir_alerta(f"pena:{dos[0]['id']}", "baja", mesa)
assert det.obtener(dos[0]["id"])["motivo_baja"] == "Egreso: pena cumplida" and det.obtener(dos[0]["id"])["estado_actual"] == "Egresado"
print("búsqueda por campos, coincidencias por nombre y revisión de la base OK")

# ---------------------------------------------------------------- documentos extranjeros, conflictos, filas para revisar, pendientes y original
cd_ = det.clasificar_documento
assert cd_("D.N.I. EXTRANJERO 1000587878")[:2] == (None, "D.N.I. EXTRANJERO 1000587878")
assert cd_("C.I. 1024538298")[0] is None and cd_("D.N.I. 1026270060")[0] is None, "más de 8 dígitos no es DNI argentino"
assert cd_("D.N.I. EXTRANJERO 94123456")[0] == "94123456", "DNI argentino para extranjeros (90 millones) sí"
assert cd_("D.N.I. 0") == (None, None, None) and cd_("NO TIENE 0") == (None, None, None)
assert cd_("30.123.456")[0] == "30123456" and cd_("30123456", extranjero=True)[0] is None, "extranjero con número bajo: no es DNI"
assert cd_("123456789")[0] is None
# fichas existentes: una con DNI y otra con LPU (la misma persona cargada dos veces)
fa = det.guardar({"nombre_completo": "CONFLICTO, Persona", "dni": "33333333"}, mesa)[0]
fb = det.guardar({"nombre_completo": "CONFLICTO, Persona", "lpu": "777777"}, mesa)[0]
solo_lpu = det.guardar({"nombre_completo": "SOLO LPU, Ramona", "lpu": "888001"}, mesa)[0]
regs_c = [{"nombre_completo": "CONFLICTO, Persona", "dni": "33333333", "lpu": "777777", "unidad_id": u1["id"]},     # 1 DNI→fa, LPU→fb
          {"nombre_completo": "Extranjera Uno", "dni": "D.N.I. EXTRANJERO 1010175408", "lpu": "888002", "nacionalidad": "Colombiana"},  # 2
          {"nombre_completo": "SOLO LPU, Ramona", "dni": "25111222"},                                               # 3 mismo nombre, la ficha no tiene DNI
          {"nombre_completo": "Repetida Uno", "dni": "28000001"}, {"nombre_completo": "Repetida Dos", "dni": "28000001"},  # 4 y 5
          {"nombre_completo": "Fecha Rota", "dni": "29000001", "fecha_nacimiento": "45/13/2000"}]               # 6
p = det.previa(regs_c, "2026-11-10", fz)
tipos_ = {r["n"]: r["tipo"] for r in p["revisar"]}
assert tipos_ == {1: "conflicto", 5: "repetido", 6: "fecha"}, tipos_
assert any(a[0] == fb for a in p["actualizar"]), "el conflicto actualiza la ficha del LPU"
assert p["documentos"] and p["documentos"][0]["n"] == 2 and any(x.get("doc_extranjero") == "D.N.I. EXTRANJERO 1010175408" for x in p["nuevas"])
assert [x["n"] for x in p["posibles"]] == [3], "mismo nombre que alguien sin DNI: se ofrece, no se une solo"
assert len(p["errores"]) == 2, "sin resolver: repetido y fecha (el conflicto se carga igual)"
# decisiones por pantalla: vista previa → sección → decidir (en lote y una corrección)
class ArchC:
    filename = "conflictos.csv"
    async def read(self):
        return ("Nombre completo;DNI;LPU;Fecha de nacimiento;Nacionalidad\n" + "\n".join(
            ";".join(str(r.get(k) or "") for k in ("nombre_completo", "dni", "lpu", "fecha_nacimiento", "nacionalidad")) for r in regs_c)).encode()
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": ArchC(), "fecha_listado": "2026-11-10", "fuerza_id": str(fz)}))).body
assert "filas para revisar" in html and 'data-seccion="revisar"' in html
tk = re.search(r'name="token" value="([^"]+)"', html).group(1)
sec = R[("GET", "/detenidos/importar/seccion")](req(mesa, "/x"), tk, "revisar", "fecha", "", "1").body
assert "Fecha Rota" in sec and "Repetida Dos" not in sec, "filtro por tipo de problema"
rr = req(mesa, "/x", form={"token": tk, "s": "revisar", "tipo": "", "q": "", "accion": "corregir:6", "fecha_nacimiento_6": "01/02/1990", "dni_6": "29000001",
                          "nombre_completo_6": "Fecha Rota", "lpu_6": ""})
run(R[("POST", "/detenidos/importar/decidir")](rr)); assert "corregida" in rr.session["flash"]
rr = req(mesa, "/x", form={"token": tk, "s": "revisar", "tipo": "conflicto", "q": "", "accion": "misma", "todas": "1"})
run(R[("POST", "/detenidos/importar/decidir")](rr)); assert "1 fila(s) marcada(s) como la misma" in rr.session["flash"], rr.session
rr = req(mesa, "/x", form={"token": tk, "s": "posibles", "accion": "misma", "fila": "3"}); run(R[("POST", "/detenidos/importar/decidir")](rr))
html = R[("GET", "/detenidos/importar/previa")](req(mesa, "/x"), tk).body
assert "1</span><span class=\"kpi__t\">filas para revisar sin resolver" in html.replace("\n", "") or "filas para revisar sin resolver" in html
r = run(R[("POST", "/detenidos/importar/confirmar")](req(mesa, form={"token": tk})))
lid_c = int(r.headers["location"].rsplit("/", 1)[1])
assert not det.obtener(fa)["activo"] and det.obtener(fa)["motivo_baja"] == f"Unificada con #{fb}", "«misma persona» unifica las dos fichas"
assert det.obtener(fb)["dni"] == "33333333" and det.obtener(fb)["lpu"] == "777777"
assert det.obtener(solo_lpu)["dni"] == "25111222", "«misma persona» por nombre completa el DNI"
assert det.buscar_campos(dni="29000001")[0]["fecha_nacimiento"] == "1990-02-01", "la corrección se cargó"
ext = det.buscar_campos(lpu="888002")[0]; assert ext["dni"] is None and ext["doc_extranjero"] == "D.N.I. EXTRANJERO 1010175408"
pend = det.alertas()["pendiente"]
pend = [x for x in pend if x["pendiente"]["datos"].get("nombre_completo") == "Repetida Dos"]
assert len(pend) == 1, "lo no resuelto queda pendiente, no se pierde"
assert any(x["pendiente"]["datos"].get("nombre_completo") == "Fecha Mala" for x in det.alertas()["pendiente"]), "también las filas con error de cargas anteriores"
# resolver el pendiente: cargarlo sin DNI
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), "pendiente", "", "", "", "1", "").body
assert "Repetida Dos" in html and "Cargar sin DNI" in html
rr = req(mesa, "/x", form={"clave": pend[0]["clave"], "tipo": "pendiente", "decision": "sin_dni", "campo_nombre_completo": "Repetida Dos", "campo_dni": "28000001"})
run(R[("POST", "/detenidos/revision")](rr)); assert "1 alerta(s)" in rr.session["flash"], rr.session
rd = det.buscar_campos(apellido="repetida", nombre="dos")[0]; assert rd["dni"] is None and rd["doc_extranjero"] == "28000001"
assert not any(x["pendiente"]["datos"].get("nombre_completo") == "Repetida Dos" for x in det.alertas()["pendiente"])
# archivo original: guardado sin modificar, con su huella; descarga registrada; aviso si se vuelve a subir
import archivo_cargas
orig = archivo_cargas.de_carga("detenidos", lid_c)
assert orig and orig["sha256"] == archivo_cargas.huella(asyncio.run(ArchC().read()))
r = R[("GET", "/detenidos/listados/{listado_id:int}/original")](req(mesa, "/x"), lid_c)
assert r.body == asyncio.run(ArchC().read())
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": ArchC(), "fecha_listado": "2026-11-11", "fuerza_id": str(fz)}))).body
assert "Este mismo archivo ya se cargó" in html
# «Último listado» en los resultados de búsqueda
html = R[("GET", "/detenidos")](req(mesa, "/detenidos"), lpu="777777").body
assert "Último listado" in html and "10/11/2026" in html and "cargado el" in html
# revisión en lote
for nombre_ in ("LOTE UNO", "LOTE DOS"):
    det.guardar({"nombre_completo": "PAR, " + nombre_}, mesa); det.guardar({"nombre_completo": "PAR, " + nombre_}, mesa)
claves_ = [a["clave"] for a in det.alertas()["duplicado"] if "PAR," in a["personas"][0]["nombre_completo"]]
rr = req(mesa, "/x", form={"lote": "1", "tipo": "duplicado", "decision": "distintas", "claves": claves_})
run(R[("POST", "/detenidos/revision")](rr)); assert f"{len(claves_)} alerta(s)" in rr.session["flash"] and len(claves_) == 2
print("documentos extranjeros, conflictos, filas para revisar, pendientes y archivo original OK")

# ---- filas repetidas en la planilla: qué pasó, qué hacer, y lo que no hace falta preguntar
regs_r = [{"nombre_completo": "GALINDEZ, Nicolas Braian", "dni": "41387028", "lpu": "447201", "centro_otro": "CPF CABA", "pabellon": "3"},     # 1
          {"nombre_completo": "Otro Cualquiera", "dni": "20999888"},                                                                         # 2
          {"nombre_completo": "GALINDEZ, Nicolas Braian", "dni": "41387028", "lpu": "447201", "centro_otro": "CPF CABA", "pabellon": "3"},   # 3 idéntica a la 1
          {"nombre_completo": "RIVEROS, Mauricio", "dni": "34395023", "lpu": "362296", "centro_otro": "U-7"},                                 # 4
          {"nombre_completo": "RIVEROS, Mauricio Sebastian", "dni": "34395023", "lpu": "362296", "centro_otro": "U-6"},                       # 5 misma persona, otro lugar
          {"nombre_completo": "PEREZ, Ana", "dni": "35111222"},                                                                             # 6
          {"nombre_completo": "SOSA, Jorge", "dni": "35111222"}]                                                                            # 7 otra persona, mismo DNI
p = det.previa(regs_r, "2026-11-11", fz)
rv = {r["n"]: r for r in p["revisar"]}
assert set(rv) == {5, 7}, rv.keys()
assert any(d["n"] == 3 and d.get("auto") and "se carga una sola vez" in d["motivo"] for d in p["descartadas"]), "idéntica: sin preguntar"
assert rv[5]["caso"] == "misma" and rv[5]["otras"][0]["n"] == 4 and "U-7" in rv[5]["otras"][0]["lugar"] and "Elegí cuál" in rv[5]["que_hacer"]
assert rv[7]["caso"] == "distintas" and "los nombres son distintos" in rv[7]["mensaje"] and "DNI 35111222" in rv[7]["mensaje"] and rv[7]["otras"][0]["nombre"] == "PEREZ, Ana"
# «vale esta fila»: la 5 en lugar de la 4; la 7 tiene el documento → la 6 se carga sin él
p = det.previa(regs_r, "2026-11-11", fz, {"5": {"accion": "vale"}, "7": {"accion": "vale"}})
assert not p["revisar"], p["revisar"]
assert any(d["n"] == 4 and "fila 5" in d["motivo"] for d in p["descartadas"])
nuevas_ = {x["nombre_completo"]: x for x in p["nuevas"]}
assert nuevas_["RIVEROS, Mauricio Sebastian"]["centro_otro"] == "U-6" and "RIVEROS, Mauricio" not in nuevas_
assert nuevas_["SOSA, Jorge"]["dni"] == "35111222" and not nuevas_["PEREZ, Ana"]["dni"] and "fila 7" in nuevas_["PEREZ, Ana"]["observaciones"]
# «vale la otra» y «cargar sin el documento»
p = det.previa(regs_r, "2026-11-11", fz, {"5": {"accion": "misma"}, "7": {"accion": "otra"}})
assert not p["revisar"] and any(d["n"] == 5 for d in p["descartadas"])
nuevas_ = {x["nombre_completo"]: x for x in p["nuevas"]}
assert nuevas_["PEREZ, Ana"]["dni"] == "35111222" and not nuevas_["SOSA, Jorge"]["dni"]
# en pantalla: la otra fila a la vista, «Qué hacer» y botones por fila
class ArchR:
    filename = "repetidas.csv"
    async def read(self):
        return ("Nombre completo;DNI;LPU;Unidad;Pabellón\n" + "\n".join(";".join(str(r.get(k) or "") for k in ("nombre_completo", "dni", "lpu", "centro_otro", "pabellon"))
                                                                     for r in regs_r)).encode()
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": ArchR(), "fecha_listado": "2026-11-11", "fuerza_id": str(fz)}))).body
tk = re.search(r'name="token" value="([^"]+)"', html).group(1)
sec = R[("GET", "/detenidos/importar/seccion")](req(mesa, "/x"), tk, "revisar", "", "", "1").body
assert "En el archivo que estás subiendo" in sec and "En el sistema hoy:" in sec and "En el archivo, fila 4:" in sec and "En el archivo, fila 5 (esta):" in sec
assert sec.count("Cargar sin DNI") >= 2, "cargar sin DNI en los dos casos"
assert "Qué hacer:" in sec and "Vale esta fila" in sec and "Vale la fila 4" in sec and "El documento es de esta persona" in sec
rr = req(mesa, "/x", form={"token": tk, "s": "revisar", "tipo": "", "q": "", "accion": "vale:5"})
run(R[("POST", "/detenidos/importar/decidir")](rr)); assert "1 fila(s) marcada(s) como la fila que vale" in rr.session["flash"], rr.session
sec = R[("GET", "/detenidos/importar/seccion")](req(mesa, "/x"), tk, "revisar", "", "", "1").body
assert "RIVEROS" not in sec and "SOSA" in sec
# «cargar sin DNI» en una repetida de la misma persona: entra como otra persona, el número queda aparte
p = det.previa(regs_r, "2026-11-11", fz, {"5": {"accion": "sin_dni"}, "7": {"accion": "sin_dni"}})
assert not p["revisar"], p["revisar"]
nuevas_ = {x["nombre_completo"]: x for x in p["nuevas"]}
assert nuevas_["RIVEROS, Mauricio Sebastian"]["dni"] == "" and nuevas_["RIVEROS, Mauricio Sebastian"]["doc_extranjero"] == "34395023"
assert not nuevas_["RIVEROS, Mauricio Sebastian"]["lpu"] and "fila 4" in nuevas_["RIVEROS, Mauricio Sebastian"]["observaciones"]
assert nuevas_["RIVEROS, Mauricio"]["dni"] == "34395023" and nuevas_["SOSA, Jorge"]["dni"] == "" and nuevas_["PEREZ, Ana"]["dni"] == "35111222"
print("filas repetidas en la planilla: diagnóstico claro, botones por fila y repetidas idénticas sin preguntar OK")

# ---- «Posible misma persona»: no se muestran pares que no pueden ser la misma persona
hoy_ = date.today()
rec = (hoy_ - timedelta(days=5)).isoformat(); rec2 = (hoy_ - timedelta(days=20)).isoformat()
base_ = {"nombre_norm": "perez marcelo antonio"}
a_ = dict(base_, id=1, dni="18228242", fecha_nacimiento="1967-03-01", fecha_listado=rec2, listado_id=1, unidad_id=10)
b_ = dict(base_, id=2, dni=None, fecha_nacimiento="1985-06-01", fecha_listado=rec, listado_id=2, unidad_id=20)
assert det.descarte_duplicado(a_, b_) == "edades distintas"
b_["fecha_nacimiento"] = None
assert det.descarte_duplicado(a_, b_) == "las dos figuran en listados recientes en lugares distintos"
b_["fecha_listado"] = "2025-01-01"
assert det.descarte_duplicado(a_, b_) is None, "uno de los listados es viejo: puede ser la misma persona (traslado)"
m1 = {"id": 3, "nombre_norm": "miguel angel", "unidad_id": 1}; m2 = {"id": 4, "nombre_norm": "miguel angel", "unidad_id": 2}
assert det.descarte_duplicado(m1, m2) == "nombre incompleto o muy común sin otro dato que coincida"
m2["unidad_id"] = 1; assert det.descarte_duplicado(m1, m2) is None, "dos palabras pero misma unidad: se muestra"
print("posible misma persona sin falsos (edades, listados recientes en lugares distintos, nombres incompletos) OK")

# ---- informe de inconsistencias para devolver a la fuerza
_u1, _u2 = CPF1["nombre"], next(u["nombre"] for u in db.listar_unidades() if u["id"] != CPF1["id"])
regs_i = [{"nombre_completo": "ALFA, Uno", "dni": "33111222", "lpu": "700001", "centro_otro": _u1},
          {"nombre_completo": "BETA, Dos", "dni": "33111222", "lpu": "700002", "centro_otro": _u1},             # documento repetido (otra persona)
          {"nombre_completo": "GAMMA, Tres", "dni": "D.N.I. EXTRANJERO 1024538298", "lpu": "700003", "centro_otro": _u1},
          {"nombre_completo": "DELTA, Cuatro", "dni": "0", "lpu": "700004", "centro_otro": "Lugar Raro 99"},
          {"nombre_completo": "EPSILON, Cinco", "dni": "34555666", "lpu": "700005", "centro_otro": _u1, "fecha_nacimiento": "45/13/1990"},
          {"nombre_completo": "ZETA, Seis", "dni": "35666777", "lpu": "700006", "centro_otro": _u1},
          {"nombre_completo": "ZETA, Seis", "dni": "35666777", "lpu": "700006", "centro_otro": _u1},              # fila duplicada idéntica
          {"nombre_completo": "ETA, Siete", "dni": "36777888", "lpu": "700007", "centro_otro": _u1},
          {"nombre_completo": "ETA, Siete", "dni": "36777888", "lpu": "700007", "centro_otro": _u2}]            # dos lugares
csv_i = ("Nombre completo;DNI;LPU;Unidad;Fecha de nacimiento\n" + "\n".join(";".join(str(r.get(k) or "") for k in ("nombre_completo", "dni", "lpu", "centro_otro", "fecha_nacimiento")) for r in regs_i)).encode()
inc = det.inconsistencias(det.leer_archivo("x.csv", csv_i)[0], "2026-11-12", fz)
tipos_i = {(i["fila"], i["tipo"]) for i in inc["items"]}
assert {(2, "doc_repetido"), (3, "doc_extranjero"), (4, "sin_dni"), (5, "fecha"), (7, "fila_duplicada"), (9, "dos_lugares")} <= tipos_i, tipos_i
assert ("Lugar Raro 99", 1) in inc["lugares"] and inc["por_tipo"]["lugar"] == 1 and inc["filas_con_problemas"] == 6
assert not any("#" in i["detalle"] for i in inc["items"]), "no revela fichas internas de la PPN"
dest = tmp / "inc.docx"; det.reporte_inconsistencias(inc, {"fuerza": "Servicio Penitenciario Federal", "fecha_listado": "2026-11-12", "archivo": "x.csv"}, str(dest))
txt = "\n".join(p.text for p in Document(dest).paragraphs) + "\n".join(c.text for t in Document(dest).tables for r in t.rows for c in r.cells)
assert "Inconsistencias detectadas" in txt and "Documento repetido en el listado" in txt and "Lugar Raro 99" in txt and "BETA, Dos" in txt and "Qué se solicita" in txt
# por pantalla: desde la vista previa y desde la carga confirmada (lo calculado al cargar queda guardado)
class ArchI:
    filename = "inconsistente.csv"
    async def read(self):
        return ("Nombre completo;DNI;LPU;Unidad;Fecha de nacimiento\n" + "\n".join(";".join(str(r.get(k) or "") for k in ("nombre_completo", "dni", "lpu", "centro_otro", "fecha_nacimiento")) for r in regs_i)).encode()
html = run(R[("POST", "/detenidos/importar")](req(mesa, form={"planilla": ArchI(), "fecha_listado": "2026-11-12", "fuerza_id": str(fz)}))).body
assert "Devolución a la fuerza" in html and "Temas a incluir en el informe" in html and 'name="tema" value="doc_repetido"' in html
assert 'name="tema" value="nombre"' not in html, "solo los temas que aparecen en el listado"
assert "DNI duplicado" in html and "DNI extranjero" in html and "Doble alojamiento" in html
tk = re.search(r'name="token" value="([^"]+)"', html).group(1)
r = R[("GET", "/detenidos/importar/inconsistencias.{formato}")](req(mesa, "/x"), "docx", tk)
assert r.media_type.endswith("wordprocessingml.document") and len(r.body) > 5000
class QPt(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
rq = req(mesa, "/x"); rq.query_params = QPt(tema=["doc_extranjero", "dos_lugares"])
r = R[("GET", "/detenidos/importar/inconsistencias.{formato}")](rq, "docx", tk)
txt_f = "\n".join(p.text for p in Document(io.BytesIO(r.body)).paragraphs) + "\n".join(c.text for t in Document(io.BytesIO(r.body)).tables for row in t.rows for c in row.cells)
assert "GAMMA, Tres" in txt_f and "ETA, Siete" in txt_f and "BETA, Dos" not in txt_f and "Lugar Raro 99" not in txt_f, "solo los temas elegidos"
f_ = det.filtrar_inconsistencias(inc, ["doc_extranjero", "inventado"]); assert f_["filas_con_problemas"] == 1 and f_["por_tipo"] == {"doc_extranjero": 1}
assert det.filtrar_inconsistencias(inc, [])["filas_con_problemas"] == inc["filas_con_problemas"], "sin temas: todo"
r = R[("GET", "/detenidos/importar/inconsistencias.{formato}")](req(mesa, "/x"), "pdf", tk)
assert r.media_type in ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
try: R[("GET", "/detenidos/importar/inconsistencias.{formato}")](req(ana, "/x"), "pdf", tk); raise SystemExit("la asesora no lo descarga")
except main.HTTPException: pass
r = run(R[("POST", "/detenidos/importar/confirmar")](req(mesa, form={"token": tk})))
lid_i = int(r.headers["location"].rsplit("/", 1)[1])
assert det.informe_de(lid_i)["informe"]["inconsistencias"]["filas_con_problemas"] == 6, "queda guardado como vino el listado"
r = R[("GET", "/detenidos/listados/{listado_id:int}/inconsistencias.{formato}")](req(mesa, "/x"), lid_i, "docx")
assert "DELTA, Cuatro" in "\n".join(c.text for t in Document(io.BytesIO(r.body)).tables for row in t.rows for c in row.cells)
html = R[("GET", "/detenidos/listados/{listado_id:int}")](req(mesa, "/x"), lid_i).body
assert f"/detenidos/listados/{lid_i}/inconsistencias.pdf" in html and "Temas a incluir en el informe" in html and 'value="sin_dni"' in html
print("informe de inconsistencias para devolver a la fuerza OK")
