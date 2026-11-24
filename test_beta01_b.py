"""Beta 01 · Tanda B: archivo directo de PDF, PDF que acompaña a un documento o a un bloque, y duplicados (aviso previo, comparar, descartar y recuperar)."""
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
import io
from reportlab.pdfgen import canvas
mesa = U("mesa", "Marta Mesa", "MESA_JEFE")
otro_eq = db.crear_equipo("Equipo Otro"); beto = U("beto", "Beto Otro", "ASESOR", otro_eq)

def pdf_bytes(texto):
    b = io.BytesIO(); c = canvas.Canvas(b); c.drawString(72, 720, texto); c.save(); return b.getvalue()
class Arch:
    def __init__(self, nombre, datos): self.filename = nombre; self.file = io.BytesIO(datos)
    async def read(self): return self.file.getvalue()
def subir_pdf(nombre, texto, u=None):
    nid, motivo = main._guardar_subida(Arch(nombre, pdf_bytes(texto)), u or ana, E1, None, "NORMAL")
    assert nid and motivo is None, motivo
    return nid
def estado(i): return db.obtener_nota(i)["estado"]
def falla(fn, codigo):
    try: run(fn())
    except main.HTTPException as e: assert e.status_code == codigo, (e.status_code, e.detail); return e.detail
    raise AssertionError(f"se esperaba {codigo}")

# ---------------------------------------------------------------- archivo directo de PDF
p1 = subir_pdf("Certificado médico.pdf", "Constancia de alta medica del interno")
n = db.obtener_nota(p1)
assert n["estado"] == "ARCHIVADA", n["estado"]
assert "constancia de alta" in (db.texto_indexado(p1) or "").lower(), "el texto del PDF queda indexado para buscarlo"
hist = [h["comentario"] or "" for h in n["historial"]]
assert any("Archivo directo (PDF)" in c for c in hist)
assert not [t for t in db.listar_notas() if t["id"] == p1 and t["estado"] in ("SUBIDA", "EN_REVISION", "PROCESADA")], "no genera tareas"
i, m = main._guardar_subida(Arch("falso.pdf", b"PK\x03\x04xx"), ana, E1, None, "NORMAL"); assert i is None and "no coincide" in m
html = main.ver_nota(req(ana, f"/nota/{p1}"), p1).body
assert "Archivo directo: no pasa por corrección, IA ni firma." in html and f'href="/nota/{p1}/pdf"' in html and "Archivado" in html
r = main.ver_pdf_archivo(req(ana), p1)
assert r.media_type == "application/pdf" and r.headers["content-disposition"].startswith("inline;")
falla(lambda: main.ver_pdf_archivo(req(beto), p1), 403)
d1 = nota("Nota principal.docx", ["PROCESANDO", "PROCESADA"])
falla(lambda: main.ver_pdf_archivo(req(ana), d1), 404)
html = main.index(req(ana, "/", q={"ver": "todos"}), **dict(IDX, ver="todos")).body
assert "Certificado médico.pdf" not in html, "lo archivado no ocupa las bandejas (como lo despachado)"
html = main.index(req(ana, "/", q={"mostrar_despachadas": "1"}), **dict(IDX, mostrar_despachadas=True, ver="todos")).body
assert "Certificado médico.pdf" in html
print("archivo directo OK")

# ---------------------------------------------------------------- acompañar a un documento (solo) y en bloque
html = main.ver_nota(req(ana, f"/nota/{p1}"), p1).body
assert 'action="/nota/%d/acompanar"' % p1 in html and "Nota principal.docx" in html
run(main.acompanar_documento(req(ana, form={"destino_id": str(d1)}), p1))
n = db.obtener_nota(p1); assert n["estado"] == "ADJUNTO" and n["bloque_id"] == db.obtener_nota(d1)["bloque_id"]
b1 = n["bloque_id"]; assert db.obtener_bloque(b1)["nombre"] == "Nota principal + adjunto"
falla(lambda: main.acompanar_documento(req(ana, form={"destino_id": str(d1)}), p1), 409)
p2 = subir_pdf("Informe externo.pdf", "Informe del hospital")
falla(lambda: main.acompanar_documento(req(beto, form={"destino_id": str(d1)}), p2), 403)
run(main.acompanar_documento(req(ana, form={"destino_id": str(d1)}), p2))
assert estado(p2) == "ADJUNTO" and db.obtener_nota(p2)["bloque_id"] == b1, "si el documento ya está en un bloque, se suma a ese"
html = main.ver_nota(req(ana, f"/nota/{p2}"), p2).body
assert "Adjunto del bloque" in html and "Sacar del bloque" in html
run(main.quitar_de_bloque(req(ana), p2)); assert estado(p2) == "ARCHIVADA", "fuera del bloque vuelve a archivado"
run(main.agregar_a_bloque(req(ana, form={"bloque_id": str(b1)}), p2)); assert estado(p2) == "ADJUNTO"
# despacho: los adjuntos no bloquean y salen con el bloque
falla(lambda: main.despachar_bloque(req(mesa), b1), 409)
db.actualizar_estado(d1, "FIRMADA")
html = main.index(req(mesa, "/", q={"ver": "todos"}), **dict(IDX, ver="todos")).body
assert "(PDF, sin firma)" in html and "Listo para despachar" in html, "el panel de Mesa: el adjunto no cuenta como «falta firmar»"
main._exigir_preparado = lambda notas: None
run(main.despachar_bloque(req(mesa), b1))
assert estado(d1) == "DESPACHADA" and estado(p1) == "DESPACHADA" and estado(p2) == "DESPACHADA"
print("adjuntos OK")

# ---------------------------------------------------------------- duplicados: aviso previo, comparar, descartar y recuperar
datos = pdf_bytes("Respuesta a oficio 123")
o1, _ = main._guardar_subida(Arch("Oficio 123.pdf", datos), ana, E1, None, "NORMAL")
r = run(main.verificar_subida(req(ana, form={"archivos": [Arch("Oficio 123 (copia).pdf", datos)]})))
j = json.loads(r.body); assert j["duplicados"][0]["existentes"][0]["id"] == o1 and j["duplicados"][0]["existentes"][0]["mismo_archivo"]
assert j["duplicados"][0]["existentes"][0]["usuario"] == "Ana Pérez" and j["duplicados"][0]["existentes"][0]["fecha"]
j = json.loads(run(main.verificar_subida(req(beto, form={"archivos": [Arch("x.pdf", datos)]}))).body)
assert j["duplicados"] == [], "no revela documentos que no puede ver"
j = json.loads(run(main.verificar_subida(req(ana, form={"archivos": [Arch("nuevo.pdf", pdf_bytes("otra cosa"))]}))).body); assert j["duplicados"] == []
# carga igualmente → queda marcado
class _BT:
    def add_task(self, *a, **k): pass
run(main.subir(req(ana, "/subir"), archivos=[Arch("Oficio 123 (copia).pdf", datos)], prioridad="NORMAL", agrupar=None, nombre_bloque="", background_tasks=_BT()))
o2 = max(t["id"] for t in db.listar_notas()); assert db.obtener_nota(o2)["duplicado_de"] == o1
html = main.index(req(ana, "/", q={"f_tarea": "duplicados"}), **dict(IDX, f_tarea="duplicados", mostrar_despachadas=True, ver="todos")).body
assert "Oficio 123 (copia).pdf" in html and "Posible duplicado" in html
html = main.comparar_duplicado(req(ana), o2).body
assert "Oficio 123.pdf" in html and "Oficio 123 (copia).pdf" in html and "Ana Pérez" in html
falla(lambda: main.comparar_duplicado(req(beto), o2), 404)
falla(lambda: main.descartar_documento(req(beto), o2), 403)
run(main.descartar_documento(req(ana), o2)); assert estado(o2) == "DESCARTADA"
assert db.obtener_nota(o2)["ruta_original"] and Path(db.obtener_nota(o2)["ruta_original"]).is_file(), "no se borra nada"
html = main.index(req(ana, "/"), **dict(IDX, mostrar_despachadas=True, ver="todos")).body
assert "Oficio 123 (copia).pdf" not in html and "Descartados (1)" in html
html = main.index(req(ana, "/", q={"f_tarea": "descartadas"}), **dict(IDX, f_tarea="descartadas")).body
assert "Oficio 123 (copia).pdf" in html
j = json.loads(run(main.verificar_subida(req(ana, form={"archivos": [Arch("y.pdf", datos)]}))).body)
assert [e["id"] for e in j["duplicados"][0]["existentes"]] == [o1], "lo descartado no se vuelve a avisar"
run(main.recuperar_documento(req(ana), o2)); assert estado(o2) == "ARCHIVADA"
falla(lambda: main.recuperar_documento(req(ana), o2), 409)
run(main.marcar_no_duplicado(req(ana), o2)); assert db.obtener_nota(o2)["duplicado_de"] is None
# un Word duplicado en borrador también se descarta y vuelve a su estado
w1 = nota("Pedido.docx", ["BORRADOR"]); run(main.descartar_documento(req(ana), w1)); assert estado(w1) == "DESCARTADA"
run(main.recuperar_documento(req(ana), w1)); assert estado(w1) == "BORRADOR"
# lo firmado no se descarta
w2 = nota("Firmada.docx", ["PROCESANDO", "PROCESADA", "FIRMADA"]); falla(lambda: main.descartar_documento(req(direc), w2), 409)
# el adjunto descartado sale del bloque y al recuperarse queda archivado
d3, d4 = nota("Uno.docx", ["PROCESANDO"]), nota("Dos.docx", ["PROCESANDO"])
p3 = subir_pdf("Constancia.pdf", "Constancia")
b3 = db.crear_bloque("B3", E1, ana["id"], [d3, d4, p3]); assert estado(p3) == "ADJUNTO"
run(main.descartar_documento(req(ana), p3)); assert estado(p3) == "DESCARTADA" and not db.obtener_nota(p3)["bloque_id"]
run(main.recuperar_documento(req(ana), p3)); assert estado(p3) == "ARCHIVADA"
src = open(APP + "/templates/index.html").read()
for k in ("/subir/verificar", "Cargar igualmente", "Reemplazar", "Cancelar"):
    assert k in src, k
print("duplicados OK")
print("ALL OK")

# ---------------------------------------------------------------- descartar una nota cargada por error (también la Dirección, la propia)
dd = nota("Nota cargada dos veces.docx", [], u=direc)
with db._conexion() as con: con.execute("UPDATE notas SET usuario_id = ? WHERE id = ?", (direc["id"], dd))
db.actualizar_estado(dd, "SUBIDA")
html = main.ver_nota(req(direc, f"/nota/{dd}"), dd).body
assert "Descartar este documento" in html and f'action="/nota/{dd}/descartar"' in html and "Cargado por error" in html
run(main.descartar_documento(req(direc, form={"motivo": "cargado por error"}), dd))
assert estado(dd) == "DESCARTADA"
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM historial_estados WHERE nota_id = ? AND comentario LIKE ?", (dd, "%cargado por error%Director%")).fetchone()[0] == 1, "el motivo y quién queda en el historial"
html = main.ver_nota(req(direc, f"/nota/{dd}"), dd).body
assert "Documento descartado." in html and "Descartar este documento" not in html
run(main.recuperar_documento(req(direc), dd)); assert estado(dd) == "SUBIDA", "vuelve a donde estaba"
# firmado: no se ofrece descartar y se explica por qué
fz = nota("Firmada por error.docx", ["PROCESANDO", "PROCESADA", "FIRMADA"])
html = main.ver_nota(req(direc, f"/nota/{fz}"), fz).body
assert "Descartar este documento" not in html and "Un documento firmado no se descarta" in html
# quien no gestiona la nota no ve la opción
html = main.ver_nota(req(beto, f"/nota/{dd}"), dd).body if main._puede_ver_nota(db.obtener_usuario(beto["id"]), db.obtener_nota(dd)) else ""
assert "Descartar este documento" not in html
print("descartar cargada por error OK")
# en corrección automática (PROCESANDO, esperando la IA): también se descarta, la cola no la retoma y no «resucita»
pz = nota("En analisis.docx", [], u=direc)
with db._conexion() as con: con.execute("UPDATE notas SET usuario_id = ?, estado = 'PROCESANDO' WHERE id = ?", (direc["id"], pz))
db.encolar_trabajo(pz, "CORRECCION")
html = main.ver_nota(req(direc, f"/nota/{pz}"), pz).body; assert "Descartar este documento" in html
run(main.descartar_documento(req(direc, form={"motivo": "duplicado"}), pz)); assert estado(pz) == "DESCARTADA"
with db._conexion() as con:
    assert con.execute("SELECT estado FROM trabajos_ia WHERE nota_id = ?", (pz,)).fetchone()["estado"] == "OMITIDO"
db.marcar_error(pz, "falló el análisis"); assert estado(pz) == "DESCARTADA", "un análisis que termina después no la vuelve a poner en circuito"
run(main.recuperar_documento(req(direc), pz)); assert estado(pz) == "SUBIDA", "al recuperarla vuelve a pedir el análisis"
print("descartar en corrección OK")

# ---- firmar un documento sin cambios desde «Revisar correcciones»
main.CARPETA_PROCESADOS = tmp / "procesados"; main.CARPETA_PROCESADOS.mkdir(exist_ok=True)
ns_ = nota("Sin cambios.docx", ["PROCESANDO"])
with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = NULL WHERE id = ?", (ns_,))
prop_ = [{"id": 0, "ubicacion": "body:1", "original": "Cuerpo.", "propuesto": "Cuerpo.", "cambios": []}]
db.guardar_propuestas(ns_, "NOTA", [], prop_, [{"ubicacion": "body:0", "texto": "Al Sr. Director", "propuesta_id": None},
                                             {"ubicacion": "body:1", "texto": "Cuerpo.", "propuesta_id": 0}], desde=("PROCESANDO",))
assert estado(ns_) == "EN_REVISION"
html = main.revisar_nota(req(direc, f"/nota/{ns_}/revisar"), ns_).body
assert "No hay cambios propuestos." in html and html.count("Firmar sin cambios") == 2 and 'name="y_firmar"' in html
r = run(main.aplicar_propuestas(ns_, req(direc, f"/nota/{ns_}/aplicar-propuestas", form={"texto_0": "Cuerpo.", "y_firmar": "sin_cambios"})))
assert r.headers["location"] == f"/nota/{ns_}?firmar=1" and estado(ns_) == "PROCESADA", "queda lista y la ficha abre la firma"
html = main.ver_nota(req(direc, f"/nota/{ns_}"), ns_).body
assert 'hx-post="/nota/%d/firmar"' % ns_ in html and 'q.get("firmar") !== "1"' in html
# con cambios: se puede guardar y firmar, o firmar sin cambios (se descartan las propuestas y queda el original)
for modo, esperado in (("1", "Cuerpo corregido."), ("sin_cambios", "Cuerpo.")):
    nc_ = nota(f"Con cambios {modo}.docx", ["PROCESANDO"])
    with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = NULL WHERE id = ?", (nc_,))
    pr_ = [{"id": 0, "ubicacion": "body:1", "original": "Cuerpo.", "propuesto": "Cuerpo corregido.", "cambios": [{"fuente": "diccionario"}]}]
    db.guardar_propuestas(nc_, "NOTA", [], pr_, [{"ubicacion": "body:0", "texto": "Al Sr. Director", "propuesta_id": None},
                                                {"ubicacion": "body:1", "texto": "Cuerpo.", "propuesta_id": 0}], desde=("PROCESANDO",))
    html = main.revisar_nota(req(direc, f"/nota/{nc_}/revisar"), nc_).body
    assert "1 párrafo con cambios propuestos" in html and "Guardar y firmar" in html and html.count('value="sin_cambios"') == 2 and "Se descartan todas las propuestas" in html
    r = run(main.aplicar_propuestas(nc_, req(direc, "/x", form={"texto_0": "Cuerpo corregido.", "y_firmar": modo})))
    assert r.headers["location"] == f"/nota/{nc_}?firmar=1" and estado(nc_) == "PROCESADA"
    textos = [p.text for p in Document(db.obtener_nota(nc_)["ruta_resultado"]).paragraphs]
    assert esperado in textos, (modo, textos)
print("firmar sin cambios OK")
