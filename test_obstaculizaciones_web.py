"""Registro de Obstaculizaciones (pantallas): carga, permisos por rol, adjuntos, filtros, exportación, informe a la firma y tipos."""
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
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1, E2 = db.crear_equipo("Equipo Salud"), db.crear_equipo("Equipo CPF I")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, beto, coo, caro = U("ana", "Ana Pérez", "ASESOR", E1), U("beto", "Beto Ruiz", "ASESOR", E1), U("coo", "Cora Coord", "COORDINADOR", E1), U("caro", "Caro CPF", "ASESOR", E2)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
def req(u, p="/", ses=None):
    async def _f(): return {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session=ses if ses is not None else {},
                                 cookies={}, headers={}, query_params={}, form=_f, client=None)
def docx(nombre, parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; r = tmp / nombre; d.save(r); return r
def procesada(nombre, prioridad="NORMAL", eq=E1, u=None):
    r = docx(nombre, ["Al Sr. Director", "Cuerpo."])
    nid = db.crear_nota(nombre, eq, str(r), usuario_id=(u or ana)["id"], prioridad=prioridad)
    for e in ("PROCESANDO", "PROCESADA"): db.actualizar_estado(nid, e)
    with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(r), nid))
    return nid

R = main.app.rutas
import clasificacion_notas as cla, traslados as tr
from PIL import Image
R = main.app.rutas
sub = U("sub", "Sara Subdirectora", "SUBDIRECTOR")
chofer = U("chof", "Pedro Chofer", "CHOFER")
legal = U("legal", "Lía Legales", "ASESOR", E2)
coo2 = U("coo2", "Coord CPF", "COORDINADOR", E2)
png = tmp / "firma.png"; Image.new("RGB", (60, 20), "white").save(png); nucleo.FIRMAS_POR_ROL = {"DIRECTOR": png, "SUBDIRECTOR": png}
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v not in (None, "") else [])
class Archivo:
    def __init__(self, nombre, contenido): self.filename, self._c = nombre, contenido
    async def read(self, n=-1): return self._c if n is None or n < 0 else self._c[:n]
def reqf(u, form, p="/x", q=None):
    r = req(u, p)
    async def _f(): return Form(form)
    r.form = _f; r.query_params = q or {}; return r
def reqq(u, q=None, p="/x"):
    r = req(u, p); r.query_params = q or {}; return r
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def body(r): return r.body.decode() if isinstance(r.body, bytes) else r.body
def falla(fn, codigo):
    try: run(fn())
    except main.HTTPException as e: assert e.status_code == codigo, (e.status_code, e.detail); return
    raise AssertionError(f"se esperaba {codigo}")
uni = {u_["nombre"]: u_ for u_ in db.listar_unidades()}
cpf1 = uni["Complejo Penitenciario Federal I de Ezeiza -CPF I-"]
spf = db._conexion().__enter__().execute("SELECT id FROM fuerzas WHERE sigla = 'SPF'").fetchone()[0] if False else None
with db._conexion() as con: spf = con.execute("SELECT id FROM fuerzas WHERE sigla = 'SPF'").fetchone()[0]
hoy = date.today()

# ---------------------------------------------------------------- menú y acceso
html = main.pagina_inicio(req(ana, "/inicio")).body
assert 'href="/obstaculizaciones"' in html
assert "lateral__panel--obstaculizaciones" in html, "Obstaculizaciones va en Paneles, como Autos, Metas y Personas detenidas"
import menu_paneles
assert menu_paneles._obstaculizaciones(ana)["texto"] in ("Sin registros este mes",) or "este mes" in menu_paneles._obstaculizaciones(ana)["texto"]
assert menu_paneles._obstaculizaciones(chofer) is None, "el chofer no ve el panel"
falla(lambda: R[("GET", "/obstaculizaciones")](reqq(chofer)), 403)
# precarga desde un viaje realizado
ayer = (hoy - timedelta(days=1)).isoformat()
vid = tr.crear_viaje(dict(fecha=ayer, hora_salida="09:00", hora_regreso="13:00", unidad_id=cpf1["id"], motivo="Monitoreo"),
                     [{"usuario_id": ana["id"], "nombre": ana["nombre"], "auto_propio": 1}, {"nombre": "Persona Externa", "auto_propio": 1}], ana)
html = body(R[("GET", "/obstaculizaciones/nueva")](req(ana, "/obstaculizaciones/nueva"), str(vid)))
assert f'value="{ayer}"' in html and f'<option value="{cpf1["id"]}" selected>' in html and "Persona Externa" in html and f'name="viaje_id" min="1" value="{vid}"' in html
html = body(R[("GET", "/traslados/{viaje_id:int}")](req(ana, f"/traslados/{vid}"), vid))
assert f"/obstaculizaciones/nueva?viaje_id={vid}" in html, "botón en el viaje realizado"
print("acceso y precarga OK")

# ---------------------------------------------------------------- carga: validación y alta con adjuntos
base = {"fecha_hecho": ayer, "hora_hecho": "09:40", "unidad_id": str(cpf1["id"]), "sector": "Módulo III", "tipo_id": "1", "equipo_id": str(E1),
        "agentes_ids": [str(ana["id"])], "agentes_otros": "Persona Externa", "fuerza_id": str(spf), "autoridad": "Jefe de turno",
        "descripcion": "El ingreso se demoró dos horas sin explicación por parte de la guardia del complejo.", "subsanado": "NO",
        "demora_minutos": "120", "viaje_id": str(vid), "expediente": "ET 6116/26"}
r = run(R[("POST", "/obstaculizaciones/nueva")](reqf(ana, dict(base, subsanado=""))))
assert "subsanar" in body(r) and 'value="Jefe de turno"' in body(r), "el error vuelve al formulario sin perder lo cargado"
rr = reqf(ana, dict(base, adjuntos=[Archivo("Acta.pdf", b"%PDF-1.4 acta"), Archivo("malo.exe", b"MZ")]))
r = run(R[("POST", "/obstaculizaciones/nueva")](rr))
oid = int(r.headers["location"].rsplit("/", 1)[1])
assert f"OB-{int(ayer[:4])}-0001" in rr.session["flash"] and "malo.exe" in rr.session["flash"], rr.session["flash"]
html = body(R[("GET", "/obstaculizaciones/{oid:int}")](req(ana), oid))
assert "Acta.pdf" in html and "Viaje #" in html and "ET 6116/26" in html and "Generar informe para la firma" in html and "Persona Externa" in html
aid = int(re.search(rf"/obstaculizaciones/{oid}/adjuntos/(\d+)", html).group(1))
r = R[("GET", "/obstaculizaciones/{oid:int}/adjuntos/{aid:int}")](req(ana), oid, aid); assert Path(r.path).read_bytes() == b"%PDF-1.4 acta"
# visibilidad: otra asesora no; coordinación del equipo sí; Legales con el permiso, sí
falla(lambda: R[("GET", "/obstaculizaciones/{oid:int}")](req(caro), oid), 403)
falla(lambda: R[("GET", "/obstaculizaciones/{oid:int}/adjuntos/{aid:int}")](req(caro), oid, aid), 403)
assert "OB-" in body(R[("GET", "/obstaculizaciones/{oid:int}")](req(coo), oid))
falla(lambda: R[("GET", "/obstaculizaciones/{oid:int}")](req(legal), oid), 403)
rr = req(direc, "/admin"); rr.headers = {"referer": "http://x/admin/usuarios/listado"}
async def _fl(): return Form({"nombre": "Lía Legales", "rol": "ASESOR", "equipo_id": str(E2), "coordina_enviado": "1", "consulta_obstaculizaciones": "1"})
rr.form = _fl; run(main.admin_editar_usuario(rr, legal["id"]))
assert db.obtener_usuario(legal["id"])["consulta_obstaculizaciones"] == 1
assert "OB-" in body(R[("GET", "/obstaculizaciones/{oid:int}")](req(legal), oid)), "con el permiso individual ve todo"
assert f">OB-{int(ayer[:4])}-0001<" in body(R[("GET", "/obstaculizaciones")](reqq(legal)))
assert "No hay registros" in body(R[("GET", "/obstaculizaciones")](reqq(caro)))
print("carga, adjuntos y visibilidad OK")

# ---------------------------------------------------------------- edición, filtros y exportación
rr = reqf(ana, dict(base, subsanado="SI", como_se_subsano="Intervino el director del complejo"))
run(R[("POST", "/obstaculizaciones/{oid:int}/editar")](rr, oid)); assert "guardados" in rr.session["flash"]
html = body(R[("GET", "/obstaculizaciones")](reqq(direc, {"subsanado": "SI", "unidad_id": str(cpf1["id"])})))
assert f">OB-{int(ayer[:4])}-0001<" in html
assert "No hay registros" in body(R[("GET", "/obstaculizaciones")](reqq(direc, {"subsanado": "NO"})))
r = R[("GET", "/obstaculizaciones/exportar.{formato}")](reqq(direc, {"desde": ayer}), "csv")
t = r.body.decode("utf-8-sig"); assert "Número;Fecha" in t and "Jefe de turno" in t and "Viaje" in t
r = R[("GET", "/obstaculizaciones/exportar.{formato}")](reqq(direc), "xlsx")
import openpyxl; libro = openpyxl.load_workbook(io.BytesIO(r.body)); assert libro.sheetnames == ["Obstaculizaciones", "Resumen"] and libro.active.max_row == 2
r = R[("GET", "/obstaculizaciones/exportar.{formato}")](reqq(direc), "pdf"); assert r.headers["content-disposition"].endswith((".pdf", ".docx"))
r = R[("GET", "/obstaculizaciones/exportar.{formato}")](reqq(caro), "csv"); assert r.body.decode("utf-8-sig").count("\n") == 1, "cada uno exporta solo lo que ve"
print("edición, filtros y exportación OK")

# ---------------------------------------------------------------- informe a la firma: clasificado y protocolizado
rr = reqf(ana, {}); r = R[("POST", "/obstaculizaciones/{oid:int}/informe")](rr, oid)
nid = int(r.headers["location"].rsplit("/", 1)[1])
n = db.obtener_nota(nid)
assert n["estado"] == "BORRADOR" and n["tipo_documental"] == "INFORME" and n["clasif_subtipo"] == "OBSTACULIZACION" and n["clasif_expediente"] == "ET 6116/26"
assert cla.confirmada(n), "llega a la firma ya clasificado"
texto = "\n".join(p_.text for p_ in Document(n["ruta_original"]).paragraphs)
assert "Informe de obstaculización" in texto and "dos horas" in texto and "SHA-256" in texto
falla(lambda: R[("POST", "/obstaculizaciones/{oid:int}/informe")](reqf(ana, {}), oid), 403)
rr = reqf(ana, dict(base)); falla(lambda: R[("POST", "/obstaculizaciones/{oid:int}/editar")](rr, oid), 403)
assert main._enviar_notas(ana, [nid], None) == 1
for e in ("PROCESANDO", "PROCESADA"): db.actualizar_estado(nid, e)
with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ruta_original WHERE id = ?", (nid,))
main._firmar(direc, nid)
assert db.obtener_nota(nid)["protocolo_numero"] == f"INF-DGPDH-{hoy.year}-0001"
assert f"INF-DGPDH-{hoy.year}-0001" in body(R[("GET", "/obstaculizaciones/{oid:int}")](req(ana), oid))
print("informe a la firma OK")

# ---------------------------------------------------------------- tipos (ABM) y anulación
falla(lambda: R[("GET", "/obstaculizaciones/tipos")](req(ana)), 403)
rr = reqf(direc, {"nombre": "Impedimento de acceso a historias clínicas"}); run(R[("POST", "/obstaculizaciones/tipos")](rr))
assert "actualizados" in rr.session["flash"] and "historias clínicas" in body(R[("GET", "/obstaculizaciones/tipos")](req(direc)))
rr = reqf(direc, {"tipo_id": "1", "accion": "baja"}); run(R[("POST", "/obstaculizaciones/tipos")](rr))
html = body(R[("GET", "/obstaculizaciones/nueva")](req(ana, "/x"), ""))
assert "Demora injustificada" not in html.split('name="tipo_id"')[1].split("</select>")[0], "el tipo de baja no se ofrece"
assert "Demora injustificada" in body(R[("GET", "/obstaculizaciones/{oid:int}")](req(ana), oid)), "el registro lo sigue mostrando"
rr = reqf(ana, {"motivo": "prueba"}); run(R[("POST", "/obstaculizaciones/{oid:int}/anular")](rr, oid)); assert "No podés" in rr.session["flash"]
rr = reqf(direc, {"motivo": "duplicado"}); run(R[("POST", "/obstaculizaciones/{oid:int}/anular")](rr, oid)); assert "anulado" in rr.session["flash"]
assert "No hay registros" in body(R[("GET", "/obstaculizaciones")](reqq(direc)))
assert "duplicado" in body(R[("GET", "/obstaculizaciones/{oid:int}")](req(direc), oid))
print("tipos y anulación OK")
