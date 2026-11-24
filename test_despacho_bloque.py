"""Despacho de un bloque: numerar de a partes y descargar los firmados sin número (PDF o Word)."""
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

main.CARPETA_ORIGINALES = tmp / "originales"; main.CARPETA_ORIGINALES.mkdir()
mesa = U("mesa", "Marta Mesa", "MESA_JEFE")
def falla(fn, codigo):
    try: run(fn()); raise AssertionError(f"esperaba {codigo}")
    except main.HTTPException as e: assert e.status_code == codigo, (e.status_code, e.detail)
import io, zipfile, despacho, integridad
def cuerpo(r):
    it = r.body_iterator
    if hasattr(it, "__aiter__"):
        async def junta(): return b"".join([x async for x in it])
        return asyncio.run(junta())
    return b"".join(it)
main.CARPETA_DESPACHO = tmp / "despacho"; main.CARPETA_PDF_SIN_NUMERO = tmp / "pdf_sin_numero"
convertidos = []
def _pdf_falso(docx_, pdf, lock=None):
    convertidos.append(Path(docx_).name); Path(pdf).write_bytes(b"%PDF-1.4 falso " + Path(docx_).name.encode())
    return "prueba"
despacho.convertir_a_pdf = _pdf_falso
import circuito; circuito.CONFIG["pdfa"] = False
def firmada(nombre):
    nid = nota(nombre, ["FIRMADA"])
    r = docx("firmado " + nombre, ["Nota N°", "Expte. N°", "Al Sr. Director", "Cuerpo de " + nombre])
    with db._conexion() as con:
        con.execute("UPDATE notas SET estado = 'FIRMADA', ruta_firmada = ?, sha256_firmado = ? WHERE id = ?", (str(r), integridad.sha256_archivo(r), nid))
    return nid
a, b, c = firmada("NOTA A.docx"), firmada("NOTA B.docx"), firmada("NOTA C.docx")
bid = db.crear_bloque("Bloque 14", E1, ana["id"], [a, b, c])
R = lambda r: r.headers.get("location")
# la pantalla ya no exige todos los números y ofrece bajar sin número
html = main.pagina_despacho_bloque(req(mesa), bid).body
assert "required" not in html.split('class="form-despacho"')[1].split("</form>")[0], "los números no son obligatorios para enviar el formulario"
assert f'href="/bloque/{bid}/descargar-sin-numero?formato=pdf"' in html and "Word sin número (.zip)" in html
# sin números: zip de PDF sin número con los tres, registrado como descarga
r = main.descargar_bloque_sin_numero(req(mesa), bid, "pdf")
zf = zipfile.ZipFile(io.BytesIO(cuerpo(r)))
assert sorted(zf.namelist()) == ["NOTA A.pdf", "NOTA B.pdf", "NOTA C.pdf"], zf.namelist()
assert "filename*=UTF-8''Bloque%2014%20-%20firmados%20sin%20n%C3%BAmero%20%28PDF%29.zip" in r.headers["content-disposition"]
assert len(convertidos) == 3
main.descargar_bloque_sin_numero(req(mesa), bid, "pdf"); assert len(convertidos) == 3, "se reutiliza el PDF mientras el firmado no cambie"
r = main.descargar_bloque_sin_numero(req(mesa), bid, "docx")
zf = zipfile.ZipFile(io.BytesIO(cuerpo(r)))
assert sorted(zf.namelist()) == ["NOTA A - FIRMADA.docx", "NOTA B - FIRMADA.docx", "NOTA C - FIRMADA.docx"], zf.namelist()
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM accesos_documentos WHERE detalle LIKE ?", (f"bloque {bid} sin número%",)).fetchone()[0] == 9, "cada descarga queda registrada por documento"
# si un firmado cambió, no se convierte y se avisa dentro del zip
Path(db.obtener_nota(c)["ruta_firmada"]).write_bytes(b"cambiado")
r = main.descargar_bloque_sin_numero(req(mesa), bid, "pdf")
zf = zipfile.ZipFile(io.BytesIO(cuerpo(r)))
assert "LEEME - documentos que faltan.txt" in zf.namelist() and "NOTA C.pdf" not in zf.namelist()
assert "cambió desde que se firmó" in zf.read("LEEME - documentos que faltan.txt").decode()
# zip de numerados sin nada numerado: vuelve a la pantalla con aviso (no un error suelto)
rr = req(mesa); r = main.descargar_despacho_bloque(rr, bid, "pdf")
assert R(r) == f"/bloque/{bid}/despacho" and "Descargar sin número" in rr.session["flash"]
# numerar de a partes: solo A; B queda sin número; C sin número tampoco frena
rr = req(mesa, form={f"nro_nota_{a}": "NO-100-2026", f"nro_expte_{a}": "", f"nro_nota_{b}": "", f"nro_nota_{c}": ""})
r = run(main.cargar_numeros_bloque(rr, bid))
assert db.obtener_nota(a)["nro_nota"] == "NO-100-2026" and not db.obtener_nota(b).get("nro_nota"), rr.session.get("flash")
assert "Quedan 2 documento(s) sin número" in rr.session["flash"], rr.session["flash"]
falla(lambda: main.descargar_bloque_sin_numero(req(U("otro", "Otro Equipo", "ASESOR", db.crear_equipo("Otro"))), bid, "pdf"), 403)
print("despacho de bloque OK")
# todo en blanco: avisa y no toca nada
rr = req(mesa, form={f"nro_nota_{b}": "", f"nro_nota_{c}": ""}); run(main.cargar_numeros_bloque(rr, bid))
with db._conexion() as con: con.execute("UPDATE notas SET nro_nota = NULL WHERE id = ?", (a,))
rr = req(mesa, form={f"nro_nota_{a}": "", f"nro_nota_{b}": "", f"nro_nota_{c}": ""}); run(main.cargar_numeros_bloque(rr, bid))
assert rr.session["flash"].startswith("⚠ No se cargó ningún número de nota")
print("en blanco OK")
# despachar sin número: se permite y queda en el historial
Path(db.obtener_nota(c)["ruta_firmada"]).write_bytes(b"x")  # (el cambio del firmado no frena el despacho sin número)
rr = req(mesa); run(main.despachar_bloque(rr, bid))
assert all(db.obtener_nota(x)["estado"] == "DESPACHADA" for x in (a, b, c)), [db.obtener_nota(x)["estado"] for x in (a, b, c)]
assert "3 documento(s) sin número de nota" in rr.session["flash"], rr.session["flash"]
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM historial_estados WHERE comentario LIKE ?", (f"%bloque {bid} sin número de nota%",)).fetchone()[0] == 3
print("despachar sin número OK")

# ---- documentos que no son notas: sin número de nota; expediente (con su tipo) desde el texto o el nombre
inf = firmada("ET 6157 ID 49592 Gramajo Inf. final.docx")
rr_ = docx("firmado informe", ["INFORME FINAL Y CIERRE DE LA INVESTIGACION", "E.T.: 6116/26", "ID: 46450", "Cuerpo del informe."])
with db._conexion() as con:
    con.execute("UPDATE notas SET ruta_firmada = ?, sha256_firmado = ?, es_nota = 0 WHERE id = ?", (str(rr_), integridad.sha256_archivo(rr_), inf))
    con.execute("UPDATE notas SET es_nota = 1 WHERE id IN (?, ?, ?)", (a, b, c))
nt = firmada("Nota suelta.docx")
with db._conexion() as con: con.execute("UPDATE notas SET es_nota = 1 WHERE id = ?", (nt,))
dd = main._datos_despacho(db.obtener_nota(inf))
assert dd["lleva_numero"] is False and dd["nro_nota"] == "" and dd["nro_expte"] == "ET 6116/26" and dd["expte_propuesto"], dd
assert main._datos_despacho(db.obtener_nota(nt))["lleva_numero"] is True
import rutas_circuito  # noqa
html = main.app.rutas[("GET", "/despacho")](req(mesa, "/despacho")).body
assert "No lleva número" in html and f'name="nro_nota_{inf}"' not in html and f'name="nro_nota_{nt}"' in html
assert f'href="/nota/{inf}/descargar"' in html and "Word firmado" in html and "ET 6116/26" in html
r = run(main.app.rutas[("POST", "/despacho/numeros")](req(mesa, "/despacho", form={"nota_id": [str(inf)], f"nro_expte_{inf}": "ET 6116/26"})))
n_ = db.obtener_nota(inf)
assert not n_["nro_nota"] and n_["nro_expte"] == "ET 6116/26" and main._despacho_preparado(n_), "el informe queda listo sin número de nota"
from docx import Document as _D
assert [p.text for p in _D(n_["ruta_despacho_docx"]).paragraphs][:2] == ["INFORME FINAL Y CIERRE DE LA INVESTIGACION", "E.T.: 6116/26"], "no se agrega nada al texto del informe"
assert main._exigir_preparado([n_]) == [], "no figura como «despachado sin número»"
r = main.descargar_nota(req(mesa), nt); assert r.status_code == 200, "el Word firmado se baja sin cargar números"
# una nota sin número también se genera (el número es opcional) y no consume numeración
rr = req(mesa, "/despacho", form={"nota_id": [str(nt)], f"nro_nota_{nt}": "", f"nro_expte_{nt}": "ET 1/26"})
run(main.app.rutas[("POST", "/despacho/numeros")](rr))
assert "No se guardó" not in rr.session.get("flash", "") and not db.obtener_nota(nt)["nro_nota"] and db.obtener_nota(nt)["nro_expte"] == "ET 1/26"
# todos los marcados juntos, como un bloque, sin cargar números
html = main.app.rutas[("GET", "/despacho")](req(mesa, "/despacho")).body
assert 'formaction="/despacho/descargar?formato=docx"' in html and 'formaction="/despacho/descargar?formato=pdf"' in html
r = run(main.descargar_despacho_del_dia(req(mesa, "/x", form={"nota_id": [str(inf), str(nt)]}), "docx"))
zf = zipfile.ZipFile(io.BytesIO(cuerpo(r)))
assert sorted(zf.namelist()) == ["ET 6157 ID 49592 Gramajo Inf. final - FIRMADA.docx", "Nota suelta - FIRMADA.docx"], zf.namelist()
r = run(main.descargar_despacho_del_dia(req(mesa, "/x", form={"nota_id": [str(inf), str(nt)]}), "pdf"))
assert sorted(zipfile.ZipFile(io.BytesIO(cuerpo(r))).namelist()) == ["ET 6157 ID 49592 Gramajo Inf. final.pdf", "Nota suelta.pdf"]
r = run(main.descargar_despacho_del_dia(req(mesa, "/x", form={"nota_id": [str(inf)]}), "despacho"))
assert any(x.endswith("despacho.docx") for x in zipfile.ZipFile(io.BytesIO(cuerpo(r))).namelist())
rr = req(mesa, "/x", form={}); r = run(main.descargar_despacho_del_dia(rr, "docx")); assert r.status_code == 303 and "Marcá" in rr.session["flash"]
try:
    run(main.descargar_despacho_del_dia(req(ana, "/x", form={"nota_id": [str(inf)]}), "docx")); raise SystemExit("solo Mesa")
except main.HTTPException as e:
    assert e.status_code == 403
# marcar varios como despachados de una vez (y los bloques): salen del panel
import pin
assert pin.ruta_protegida("POST", "/despacho/despachar"), "pide el PIN como el despacho individual"
otra = firmada("Nota otra.docx")
b2_ = [firmada("NOTA B2a.docx"), firmada("NOTA B2b.docx")]; bid2 = db.crear_bloque("Bloque 15", E1, ana["id"], b2_)
html = main.app.rutas[("GET", "/despacho")](req(mesa, "/despacho")).body
assert 'formaction="/despacho/despachar"' in html and f'name="bloque_id" value="{bid2}"' in html and "Bloque 15" in html
rr = req(mesa, "/x", form={"nota_id": [str(inf), str(otra)], "bloque_id": [str(bid2)]})
r = run(main.despachar_marcados(rr))
assert r.headers["location"] == "/despacho" and "Despachados: 3." in rr.session["flash"], rr.session["flash"]
assert all(db.obtener_nota(x)["estado"] == "DESPACHADA" for x in [inf, otra] + b2_)
html = main.app.rutas[("GET", "/despacho")](req(mesa, "/despacho")).body
assert "Nota otra.docx" not in html and "Bloque 15" not in html, "lo despachado sale del panel"
try:
    run(main.despachar_marcados(req(ana, "/x", form={"nota_id": [str(nt)]}))); raise SystemExit("solo Mesa")
except main.HTTPException as e:
    assert e.status_code == 403
# copia de despacho abierta en Word: se genera con otro nombre en vez de fallar
n_ = db.obtener_nota(nt); abierta = Path(n_["ruta_despacho_docx"])
import shutil as _sh
_orig = _sh.rmtree; _sh.rmtree = lambda *a, **k: None  # simula que no se pudo borrar (archivo abierto)
try:
    main._preparar_despacho(n_, "", "ET 1/26")
finally:
    _sh.rmtree = _orig
n2_ = db.obtener_nota(nt); assert n2_["ruta_despacho_docx"] != str(abierta) and Path(n2_["ruta_despacho_docx"]).is_file()
print("informes sin número y descarga en lote OK")
