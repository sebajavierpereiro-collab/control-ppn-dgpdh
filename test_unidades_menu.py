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
"""(arriba: el mismo armado de la app que test_detenidos)  Unidades del Menú de la Procuración, fuerza a cargo, alerta de fuerza distinta y archivo de originales."""
import unidades_menu as um, archivo_cargas
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
    def items(self): return list(dict.items(self))
def req(u, p="/", form=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"]) if u else None), url=URL(p), session={}, cookies={},
                                 headers={}, query_params={}, form=_f, client=None)
R = main.app.rutas
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
SPF = next(f for f in det.listar_fuerzas(False) if f["sigla"] == "SPF"); SPB = next(f for f in det.listar_fuerzas(False) if f["sigla"] == "SPB")

# ------------------------------------------------- fuerza a cargo de la unidad (lista)
with db._conexion() as con:
    con.execute("UPDATE unidades SET fuerza = 'S.P.F.' WHERE id = ?", (CPF1["id"],)); um.inicializar(con)
assert db.obtener_unidad(CPF1["id"]).get("fuerza_id") is None, "«S.P.F.» no es exactamente la sigla: no se adivina"
with db._conexion() as con:
    con.execute("UPDATE unidades SET fuerza = 'spf' WHERE id = ?", (CPF1["id"],)); um.inicializar(con)
assert db.obtener_unidad(CPF1["id"]).get("fuerza_id") == SPF["id"], "el texto «spf» ya cargado se vincula a la fuerza de la lista"
assert CPF1["id"] in [u["id"] for u in db.listar_unidades(fuerza_id=SPF["id"])]
assert CPF1["id"] not in [u["id"] for u in db.listar_unidades(fuerza_id=SPB["id"])]
html = R[("GET", "/unidades")](req(direc, "/unidades"), editar=CPF1["id"]).body
assert "Otra fuerza" in html and "Servicio Penitenciario Bonaerense" in html and "Delegación PPN" in html and "formato del Menú" in html

# ------------------------------------------------- archivo del Menú
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["ID", "Código", "Código RNCT", "Nombre", "Director", "Dirección", "Correo electrónico", "Teléfono", "Activo"])
ws.append([5001, "U1", 0, CPF1["nombre"], "Pérez", "Ruta 205", "cpf1@spf.gob.ar", "011-4444", 1])           # mismo nombre: vinculada
ws.append([5002, "", 0, "(DLIT) Unidad Penal N° 11 - Piñero", "s/d", "n/a", "no-es-correo", "", 1])  # alta, delegación DLIT
ws.append([5003, "", "", "Unidad 9 La Plata (SPB)", "", "", "", "", 1])                                          # alta, fuerza SPB
ws.append([5004, "", "", "Comisaría 33 (CABA)", "", "", "", "", 1])                                               # se va a omitir
ws.append([5002, "", "", "repetida", "", "", "", "", 1])                                                          # ID repetido
buf = io.BytesIO(); wb.save(buf); contenido = buf.getvalue()
menu, avisos = um.leer(contenido, "data.xlsx")
assert [m["menu_id"] for m in menu] == [5001, 5002, 5003, 5004] and any("repetidos" in a for a in avisos)
m2 = menu[1]
assert m2["delegacion"] == "DLIT" and m2["nombre"].startswith("Unidad Penal") and m2["director"] is None and m2["correo"] is None, m2
assert menu[2]["fuerza_sigla"] == "SPB" and menu[3]["delegacion"] == "CABA"
p = um.previa(menu)
est = {f["menu_id"]: f["estado"] for f in p["filas"]}
assert est[5001] == "vinculada" and est[5002] == "nueva" and est[5003] == "nueva", ("la Unidad 9 del SPB no es la Unidad 9 del SPF", est)
assert um.clave("Unidad 9 La Plata (SPB)") != um.clave("Unidad 9") and um.clave("U.9 (SPB)")[1] == 9

class Subido:
    filename = "data - 2026-11-12.xlsx"
    async def read(self): return contenido
html = run(R[("POST", "/unidades/menu")](req(direc, "/unidades/menu", {"archivo": Subido()}))).body
assert "Unidades del Menú de la Procuración" in html and "dest_5002" in html and "Unidad Penal N° 11" in html and "ya se cargó" not in html
token = re.search(r'name="token" value="([^"]+)"', html).group(1)
html_csv = run(R[("POST", "/unidades/importar")](req(direc, "/unidades/importar", {"archivo": Subido()}))).body
assert "Unidades del Menú de la Procuración" in html_csv and "se abrió su vista previa" in html_csv, "el Excel del Menú subido por el importador de CSV va a su vista previa"
# una persona detenida alojada en una unidad «de texto» que llega con el Menú
idp, err, _ = det.guardar({"nombre_completo": "Persona Piñero", "dni": "31222333", "centro_otro": "Unidad Penal N° 11 - Piñero"}, direc)
assert idp and not err, err
r = run(R[("POST", "/unidades/menu/confirmar")](req(direc, "/unidades/menu/confirmar", {"token": token, "dest_5001": str(CPF1["id"]), "dest_5002": "nueva",
                                                                                       "dest_5003": "nueva", "dest_5004": "omitir"})))
assert r.status_code == 303
c = um.cargas(5)[0]
cpf = db.obtener_unidad(CPF1["id"])
assert cpf["menu_id"] == 5001 and cpf["director"] == "Pérez" and cpf["nombre"] == CPF1["nombre"], "vincula y completa solo lo vacío"
nueva = next(u for u in db.listar_unidades(solo_activas=False) if u.get("menu_id") == 5002)
assert nueva["delegacion_ppn"] == "DLIT" and nueva["activa"]
u9 = next(u for u in db.listar_unidades(solo_activas=False) if u.get("menu_id") == 5003)
assert u9["fuerza_id"] == SPB["id"] and u9["fuerza"] == SPB["sigla"] or u9["fuerza_id"] == SPB["id"], u9
assert not any(u.get("menu_id") == 5004 for u in db.listar_unidades(solo_activas=False)), "la omitida no entra"
assert det.obtener(idp)["unidad_id"] == nueva["id"], "la persona pasa a la unidad del catálogo"
# el original queda guardado y se descarga igual
reg = archivo_cargas.de_carga("unidades_menu", c["id"])
assert reg and archivo_cargas.leer(reg)[1] == contenido
assert R[("GET", "/unidades/menu/{carga_id:int}/original")](req(direc), c["id"]).body == contenido
html = run(R[("POST", "/unidades/menu")](req(direc, "/unidades/menu", {"archivo": Subido()}))).body
assert "ya se cargó" in html, "avisa que el mismo archivo ya se cargó"
assert est != {} and "Vincular a " + CPF1["nombre"] in html, "la segunda vez la unidad ya queda vinculada por ID"
# exportar en formato del Menú
wbx = openpyxl.load_workbook(io.BytesIO(R[("GET", "/unidades/menu.xlsx")](req(direc)).body))
cab = [c.value for c in wbx.active[1]]
assert "ID" in cab and "Nombre" in cab, cab
assert any(r[cab.index("ID")] == 5002 for r in wbx.active.iter_rows(min_row=2, values_only=True))
# filtro por delegación
assert [u["id"] for u in db.listar_unidades(delegacion="DLIT")] == [nueva["id"]]
# anular: las altas quedan inactivas y lo completado vuelve atrás
try: um.anular(c["id"], "", direc); raise SystemExit("pide motivo")
except ValueError: pass
um.anular(c["id"], "prueba", direc)
assert not db.obtener_unidad(nueva["id"])["activa"] and not db.obtener_unidad(CPF1["id"]).get("director")

# ------------------------------------------------- alerta: fuerza que informa distinta de la fuerza de la unidad
idf, err, _ = det.guardar({"nombre_completo": "Fuerza Distinta", "dni": "32111222", "unidad_id": str(CPF1["id"])}, direc)
with db._conexion() as con: con.execute("UPDATE detenidos SET fuerza_id = ? WHERE id = ?", (SPB["id"], idf))
al = det.alertas()
x = next(a for a in al["fuerza_distinta"] if a["personas"][0]["id"] == idf)
assert "Servicio Penitenciario Bonaerense" in x["motivo"] and "Servicio Penitenciario Federal" in x["motivo"]
html = R[("GET", "/detenidos/revision")](req(direc, "/detenidos/revision"), tipo="fuerza_distinta").body
assert "Fuerza distinta" in html and "Corregir la fuerza de la unidad" in html
det.decidir_alerta(x["clave"], "revisado", direc)
assert not any(a["personas"][0]["id"] == idf for a in det.alertas()["fuerza_distinta"])

# ------------------------------------------------- archivo de originales: nombre sin número de carga
rid = archivo_cargas.guardar("viajes", None, "viajes.xlsx", b"abc", direc)
with db._conexion() as con: ruta = con.execute("SELECT ruta FROM cargas_archivos WHERE id = ?", (rid,)).fetchone()[0]
assert "cargaNone" not in ruta and ruta.endswith("viajes.xlsx")
# ------------------------------------------------- al importar detenidos: vincular lugares solo con unidades de la fuerza del listado
with db._conexion() as con: con.execute("UPDATE unidades SET fuerza_id = ? WHERE id = ?", (SPF["id"], CPF1["id"]))
sc = det.centros_sin_catalogo([{"nombre_completo": "X", "centro_otro": "Complejo Penit. Federal I Ezeiza"}], SPF["id"])
assert sc[0][2] and sc[0][2]["id"] == CPF1["id"], sc
class ArchL:
    filename = "listado_spf.csv"
    async def read(self): return "Nombre completo;DNI;Unidad\nPersona Lugar Raro;31555666;Lugar Inventado 77\n".encode()
html = run(R[("POST", "/detenidos/importar")](req(direc, "/detenidos/importar", {"planilla": ArchL(), "fecha_listado": "2026-11-11", "fuerza_id": str(SPF["id"])}))).body
assert "Lugar Inventado 77" in html and "unidades a cargo de Servicio Penitenciario Federal" in html and "Mostrar las unidades de todas las fuerzas" in html
otra = next(u for u in db.listar_unidades() if u.get("fuerza_id") != SPF["id"])
assert f'value="{otra["id"]}" data-otra-fuerza="1"' in html and f'value="{CPF1["id"]}" data-otra-fuerza' not in html
html = run(R[("POST", "/detenidos/importar")](req(direc, "/detenidos/importar", {"planilla": ArchL(), "fecha_listado": "2026-11-11", "fuerza_id": str(det.crear_fuerza("Servicio Penitenciario de Prueba", "SPP", direc))}))).body
assert "Ninguna unidad del catálogo tiene cargada la fuerza" in html and "data-otra-fuerza" not in html
print("unidades del Menú, fuerza a cargo, alerta de fuerza distinta y archivo de originales OK")
