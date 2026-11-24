"""Beta 01 · Tanda A: bandejas sin pestañas vacías, cambios sin diferencia visible, reversión, barra fija de firma,
pendientes de control al terminar de firmar e importación de unidades con vista previa."""
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

# ---------------------------------------------------------------- 164) bandejas
s1 = nota("Nota para aprobar.docx", [])          # SUBIDA: pendiente de la coordinación
db.guardar_pref_notas(coo["id"], {"ver": "todos"})  # la última vez eligió «Todos»
html = main.index(req(coo, "/"), **IDX).body
assert 'pestana pestana--activa" href="/?ver=pendientes"' in html and "Nota para aprobar.docx" in html, "al entrar abre «Pendientes de mi acción»"
assert 'data-plegable="documentos" data-abrir="si"' in html, "y la bandeja se muestra abierta aunque se haya retraído"
html = main.index(req(coo, "/?ver=todos"), **dict(IDX, ver="todos")).body
assert 'pestana--activa" href="/?vista=equipo"' in html or 'pestana pestana--activa" href="/?ver=todos"' in html, "la pestaña elegida a mano se respeta"
db.actualizar_estado(s1, "PROCESANDO")
html = main.index(req(coo, "/"), **IDX).body
assert 'pestana pestana--activa" href="/?ver=pendientes"' not in html, "sin tareas: la última elegida"
js = open(APP + "/static/plegable.js").read(); assert 'data-abrir' in js
print("164 OK")

# ---------------------------------------------------------------- 157) cambios sin diferencia visible
assert nucleo.igual_a_la_vista("Buenos Aires, 25 de septiembre de 2026", "Buenos Aires, 25 de septiembre de 2026 ")
assert nucleo.igual_a_la_vista("a​b  c", "ab c") and not nucleo.igual_a_la_vista("pabellon", "pabellón") and not nucleo.igual_a_la_vista("Sr", "Sr.")
_r, _lt, _ia = nucleo.aplicar_reemplazos_texto, nucleo._texto_tras_languagetool, nucleo._texto_tras_ia
nucleo.aplicar_reemplazos_texto = lambda t: t.replace(" ", " ", 1)
nucleo._texto_tras_languagetool = lambda t: (t, [])
nucleo._texto_tras_ia = lambda t: t + " "
txt = "Buenos Aires, 25 de septiembre de 2026"
final, cambios, _ = nucleo.generar_propuesta_parrafo(txt)
assert final == txt and cambios == [], "solo cambios invisibles: no hay propuesta y el texto queda idéntico"
nucleo._texto_tras_ia = lambda t: t.replace("septiembre", "Septiembre")
final, cambios, _ = nucleo.generar_propuesta_parrafo(txt)
assert final != txt and [c["fuente"] for c in cambios] == ["ia_ollama"], "el cambio visible se conserva; el invisible del diccionario se descarta"
nucleo.aplicar_reemplazos_texto, nucleo._texto_tras_languagetool, nucleo._texto_tras_ia = _r, _lt, _ia
src = open(APP + "/main.py").read(); assert "igual_a_la_vista(p[\"propuesto\"], p[\"original\"])" in src, "las propuestas viejas también se limpian al abrir «Revisar»"
print("157 OK")

# ---------------------------------------------------------------- 158) reversión (la interacción se prueba en el navegador: harness/prueba_reversion.py)
rv = open(APP + "/templates/revisar.html").read()
for k in ('id="rv-deshacer"', 'id="rv-restaurar-todo"', 'class="boton-link rv-restaurar"', "rv-propuesta", "data-original="):
    assert k in rv, k
print("158 OK")

# ---------------------------------------------------------------- 163) barra fija de firma
p1 = nota("Nota a la firma.docx", ["PROCESANDO", "PROCESADA"])
html = main.ver_nota(req(direc, f"/nota/{p1}"), p1).body
assert 'class="barra-firma"' in html and ">Firmar</button>" in html and ">Devolver</button>" in html and ">Historial</a>" in html and ">Ver documento</a>" in html
assert '<details class="datos-nota"><summary>Datos de la nota' in html, "equipo, asesor y prioridad, plegados"
html = main.ver_nota(req(direc, f"/nota/{p1}", q={"serie": "1"}), p1).body
assert 'class="barra-firma"' not in html and "banda-serie" in html, "en la firma en serie queda solo su banda"
html = main.ver_nota(req(ana, f"/nota/{p1}"), p1).body
assert 'class="barra-firma"' not in html and "datos-nota" not in html
print("163 OK")

# ---------------------------------------------------------------- 161) sin firmas: pendientes de control
with db._conexion() as con: con.execute("UPDATE notas SET estado = 'FIRMADA' WHERE estado = 'PROCESADA'")
r1, r2 = nota("Revisar normal.docx", ["PROCESANDO", "EN_REVISION"]), nota("Revisar urgente.docx", ["PROCESANDO", "EN_REVISION"])
db.actualizar_prioridad(r2, "URGENTE", "x")
html = main.firma_terminada(req(direc)).body
assert "¿Querés revisar los documentos pendientes de control?" in html and html.index("Revisar urgente.docx") < html.index("Revisar normal.docx")
assert f'href="/nota/{r2}/revisar">Empezar por la primera' in html
print("161 OK")

# ---------------------------------------------------------------- 151) importación de unidades
L = lambda t: unidades_csv.leer(t.encode("utf-8"))
r = L('nombre;ciudad;correo\n"Unidad Prueba\nUno";Ezeiza;a@b.gob.ar\n')
assert r["filas"][0]["errores"][0] == {"columna": "Nombre", "mensaje": "Se detectó un salto de línea dentro de la celda."} and r["filas"][0]["fila"] == 2
r = L("nombre;ciudad\nUnidad Dos;Ezeiza;sobra\n")
assert "columnas" in r["filas"][0]["errores"][0]["mensaje"]
r = L("ciudad;provincia\nEzeiza;Buenos Aires\n"); assert "Falta la columna «nombre»" in r["errores_generales"][0]
assert "Excel" in unidades_csv.leer(b"PK\x03\x04resto")["errores_generales"][0] and "vacío" in unidades_csv.leer(b"")["errores_generales"][0]
r = L('nombre;ciudad\n"Abierta;Ezeiza\nOtra;Lugar\n')   # comillas sin cerrar: se informa, no se rompe
assert r["errores_generales"] or any(f["errores"] for f in r["filas"]), r
CPF1 = next(u for u in db.listar_unidades() if "-CPF I-" in u["nombre"])
r = L(f"Unidad;Localidad;Latitud;email\nUnidad Nueva Test;Rawson;-43,3;ok@ppn.gov.ar\n{CPF1['nombre']};Ezeiza;;\nMala;X;abc;no-es-correo\nUnidad Nueva Test;Rawson;;\n")
acc = [f["accion"] for f in r["filas"]]
assert acc == ["nueva", "actualizar", "error", "error"], acc
assert {e["columna"] for e in r["filas"][2]["errores"]} == {"Latitud", "Correo"} and "se repite" in r["filas"][3]["errores"][0]["mensaje"]
assert unidades_csv.resumen(r) == {"nuevas": 1, "actualizar": 1, "errores": 2}
assert b"Fila;Unidad;Columna;Problema" in unidades_csv.informe_errores(r) and b"nombre;tipo;zona" in unidades_csv.plantilla()
# por pantalla: vista previa → confirmar; nunca un error técnico
class Archivo:
    def __init__(self, contenido, nombre="u.csv"): self.contenido, self.filename = contenido, nombre
    async def read(self): return self.contenido
R = main.app.rutas
html = run(R[("POST", "/unidades/importar")](req(direc, form={"archivo": Archivo("nombre;ciudad\nUnidad Pantalla;Viedma\nRota;\"X\nY\"\n".encode())}))).body
assert "vista previa" in html and "Unidad Pantalla" in html and "salto de línea" in html and "Traceback" not in html
token = re.search(r'name="token" value="([^"]+)"', html).group(1)
r = run(R[("POST", "/unidades/importar/confirmar")](req(direc, form={"token": token})))
assert r.status_code == 303 and any(u["nombre"] == "Unidad Pantalla" for u in db.listar_unidades(solo_activas=False)) and not any(u["nombre"] == "Rota" for u in db.listar_unidades(solo_activas=False))
html = run(R[("POST", "/unidades/importar")](req(direc, form={"archivo": Archivo(b"\xff\xfe\x00basura sin formato")}))).body
assert "No fue posible importar el archivo" in html and "Traceback" not in html
r = R[("GET", "/unidades/plantilla.csv")](req(direc)); assert r.media_type.startswith("text/csv")
print("151 OK")
print("OK beta 01 · tanda A")
