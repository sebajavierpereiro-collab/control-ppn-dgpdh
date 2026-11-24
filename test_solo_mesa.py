"""«Solo Mesa de Entradas» (sin control, coordinación ni firma; remisión directa) y funciones de Mesa de Entradas:
las ejerce esa área y, excepcionalmente, el Director General (y los perfiles superiores que se creen), con registro."""
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
import main, auth, nucleo, unidades_csv, integridad
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
main.CARPETA_DESPACHO = tmp / "despacho"
import io
mesa = U("mesa", "Marta Mesa", "MESA_JEFE"); mesa2 = U("mesa2", "Mario Mesa", "MESA_ASESOR")
sub = U("sub", "Sara Subdirectora", "SUBDIRECTOR")
class Arch:
    def __init__(self, nombre, datos): self.filename = nombre; self.file = io.BytesIO(datos)
    async def read(self): return self.file.getvalue()
def docx_bytes(*parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; b = io.BytesIO(); d.save(b); return b.getvalue()
class _BT:
    def add_task(self, *a, **k): pass
def subir(u, nombre, datos, modalidad="SOLO_MESA"):
    db.FUNCION_MESA_EXCEPCIONAL.set(False)
    tok = db.USUARIO_ACTUAL.set(db.obtener_usuario(u["id"]))
    try:
        return run(main.subir(req(u, "/subir"), archivos=[Arch(nombre, datos)], prioridad="NORMAL", agrupar=None, nombre_bloque="",
                              background_tasks=_BT(), modalidad=modalidad))
    finally:
        db.USUARIO_ACTUAL.reset(tok)
def como(u, fn):
    db.FUNCION_MESA_EXCEPCIONAL.set(False)
    tok = db.USUARIO_ACTUAL.set(db.obtener_usuario(u["id"]))
    try: return run(fn())
    finally: db.USUARIO_ACTUAL.reset(tok); db.FUNCION_MESA_EXCEPCIONAL.set(False)
def falla(fn, codigo, u=None):
    try: como(u or ana, fn) if u else run(fn())
    except main.HTTPException as e: assert e.status_code == codigo, (e.status_code, e.detail); return e.detail
    raise AssertionError(f"se esperaba {codigo}")
ultimo = lambda: max(t["id"] for t in db.listar_notas())
estado = lambda i: db.obtener_nota(i)["estado"]
avisos = lambda u: [x["texto"] for x in db.listar_notificaciones(u["id"])] if hasattr(db, "listar_notificaciones") else []

# ---------------------------------------------------------------- quién puede
assert auth.funciones_mesa(mesa) and auth.funciones_mesa(direc) and auth.funciones_mesa(sub) and not auth.funciones_mesa(coo)
assert auth.mesa_excepcional(sub)
assert auth.mesa_excepcional(direc) and not auth.mesa_excepcional(mesa)
for u in (ana, coo):
    falla(lambda u=u: main.subir(req(u, "/subir"), archivos=[Arch("x.docx", docx_bytes("x"))], prioridad="NORMAL", agrupar=None,
                                  nombre_bloque="", background_tasks=_BT(), modalidad="SOLO_MESA"), 403)
print("permisos OK")

# ---------------------------------------------------------------- carga por Mesa de Entradas
n_coo = len(db.listar_notificaciones(coo["id"])) if hasattr(db, "listar_notificaciones") else 0
subir(mesa, "Pase a Unidad 4.docx", docx_bytes("Al Sr. Director de la Unidad 4", "Se remite el expediente de referencia."))
m1 = ultimo(); n = db.obtener_nota(m1)
assert n["estado"] == "PARA_REMITIR" and n["modalidad"] == "SOLO_MESA"
hist = n["historial"]
assert any("Solo Mesa de Entradas (cargado por Marta Mesa)" in (h["comentario"] or "") for h in hist)
assert not any(h["estado"] in ("SUBIDA", "PROCESANDO") for h in hist), "no pasa por coordinación ni corrección"
assert not any("excepcional" in (h["usuario"] or "") for h in hist), "Mesa lo hace como función propia"
if hasattr(db, "listar_notificaciones"):
    assert len(db.listar_notificaciones(coo["id"])) == n_coo, "Coordinación no recibe aviso"
    assert any("Solo Mesa de Entradas" in x["texto"] for x in db.listar_notificaciones(mesa2["id"])), "el resto del área sí"
r = subir(mesa, "Viejo.doc", b"\xd0\xcf\x11\xe0" + b"0" * 100)
assert "guardalo como .docx" in json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else True
# Mesa lo ve, lo ve como tarea y con su etiqueta; el asesor y la coordinación no
html = main.index(req(mesa, "/"), **IDX).body
assert "Pase a Unidad 4.docx" in html and "etiqueta-estado--para_remitir\">Solo Mesa de Entradas" in html and "para despachar" in html
assert main._puede_ver_nota(db.obtener_usuario(mesa2["id"]), n) and not main._puede_ver_nota(db.obtener_usuario(ana["id"]), n)
html = main.ver_nota(req(mesa, f"/nota/{m1}"), m1).body
assert "Sin control, coordinación ni firma: remisión directa." in html and 'action="/nota/%d/numeros"' % m1 in html
assert "copia del documento cargado" in html and "excepcional" not in html
html_form = main.index(req(mesa, "/"), **IDX).body
assert 'value="SOLO_MESA" checked' in html_form, "para Mesa, «Solo Mesa de Entradas» viene marcado"
# números, documento y despacho (el número se inserta sobre el original, que queda intacto)
sha = db.obtener_nota(m1)["sha256_original"]
como(mesa, lambda: main.cargar_numeros_nota(req(mesa), m1, nro_nota="NO-2026-00123-PPN", nro_expte="EX-2026-1-PPN"))
n = db.obtener_nota(m1)
assert n["nro_nota"] == "NO-2026-00123-PPN" and Path(n["ruta_despacho_docx"]).is_file()
assert integridad.sha256_archivo(n["ruta_original"]) == sha
assert "NO-2026-00123-PPN" in nucleo.texto_de_documento(n["ruta_despacho_docx"])
como(mesa, lambda: main.despachar_nota(req(mesa), m1)); assert estado(m1) == "DESPACHADA"
r = main.descargar_nota(req(mesa), m1); assert str(r.path) == n["ruta_original"], r.path
print("Solo Mesa de Entradas OK")

# ---------------------------------------------------------------- el Director, excepcionalmente
subir(direc, "Remito urgente.docx", docx_bytes("Se remite documentación."))
m2 = ultimo(); n = db.obtener_nota(m2)
assert n["estado"] == "PARA_REMITIR"
assert any("(excepcional: función de Mesa de Entradas)" in (h["usuario"] or "") for h in n["historial"]), "queda registrado como excepcional"
html = main.ver_nota(req(direc, f"/nota/{m2}"), m2).body
assert "queda registrada como excepcional" in html
html = main.index(req(direc, "/"), **dict(IDX, ver="todos")).body
assert "«Solo Mesa de Entradas» es una función de esa área" in html and 'value="" checked' in html, "al Director se le propone el circuito normal"
# despacha una nota firmada: el historial lo marca
f1 = nota("Nota firmada.docx", ["PROCESANDO", "PROCESADA", "FIRMADA"])
with db._conexion() as con: con.execute("UPDATE notas SET ruta_firmada = ruta_resultado WHERE id = ?", (f1,))
como(direc, lambda: main.cargar_numeros_nota(req(direc), f1, nro_nota="NO-2026-00124-PPN", nro_expte=""))
como(direc, lambda: main.despachar_nota(req(direc), f1))
h = [x for x in db.obtener_nota(f1)["historial"] if x["estado"] == "DESPACHADA"][0]
assert h["usuario"].endswith("(excepcional: función de Mesa de Entradas)"), h["usuario"]
# la Subdirección, igual que el Director: puede, y queda marcado como excepcional
f2 = nota("Otra firmada.docx", ["PROCESANDO", "PROCESADA", "FIRMADA"])
with db._conexion() as con: con.execute("UPDATE notas SET ruta_firmada = ruta_resultado WHERE id = ?", (f2,))
como(sub, lambda: main.cargar_numeros_nota(req(sub), f2, nro_nota="NO-2026-00126-PPN", nro_expte=""))
como(sub, lambda: main.despachar_nota(req(sub), f2))
h = [x for x in db.obtener_nota(f2)["historial"] if x["estado"] == "DESPACHADA"][0]
assert h["usuario"] == "Sara Subdirectora (excepcional: función de Mesa de Entradas)", h["usuario"]
html = main.index(req(sub, "/"), **IDX).body
assert 'href="/despacho"' in html and 'name="modalidad"' in html
html = main.index(req(direc, "/"), **IDX).body; assert 'href="/despacho"' in html
# Mesa no queda marcada como excepcional al despachar
f3 = nota("Tercera firmada.docx", ["PROCESANDO", "PROCESADA", "FIRMADA"])
with db._conexion() as con: con.execute("UPDATE notas SET ruta_firmada = ruta_resultado WHERE id = ?", (f3,))
como(mesa, lambda: main.cargar_numeros_nota(req(mesa), f3, nro_nota="NO-2026-00125-PPN", nro_expte=""))
como(mesa, lambda: main.despachar_nota(req(mesa), f3))
assert not any("excepcional" in (x["usuario"] or "") for x in db.obtener_nota(f3)["historial"])
print("excepcional OK")
print("ALL OK")
