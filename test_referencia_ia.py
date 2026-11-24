"""Documentos de referencia de la IA: carpetas y subcarpetas con documentos firmados; términos que la corrección respeta,
documentos parecidos (propuesta de carpeta), ejemplo de estilo y la pantalla de administración."""
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

import io, analisis
import referencia_ia as ref
ref.CARPETA = tmp / "referencia_ia"; ref.CARPETA.mkdir()
ref._invalidar()
def docx_bytes(*parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; b = io.BytesIO(); d.save(b); return b.getvalue()
def error(fn, parte):
    try: fn()
    except ref.ErrorReferencia as e: assert parte in str(e), str(e); return
    raise AssertionError("se esperaba un error")

# ---------------------------------------------------------------- carpetas
assert ref.crear_carpeta("", "Salud") == "Salud"
assert ref.crear_carpeta("Salud", "Atención médica") == "Salud/Atención médica"
assert ref.crear_carpeta("Salud/Atención médica", "2025") == "Salud/Atención médica/2025"
ref.crear_carpeta("", "Traslados")
error(lambda: ref.crear_carpeta("", "salud"), "Ya hay una carpeta")
error(lambda: ref.crear_carpeta("", "Notas: 2025"), "caracteres")
error(lambda: ref.crear_carpeta("", "CON"), "reservado")
error(lambda: ref.crear_carpeta("", "_retirados"), "no se puede usar")
error(lambda: ref.crear_carpeta("../afuera", "x"), "no se puede usar")
error(lambda: ref.crear_carpeta("No existe", "x"), "no existe")
print("carpetas OK")

# ---------------------------------------------------------------- archivos
MED = ["Al Señor Director del Complejo Penitenciario Federal I de Ezeiza", "Se solicita atención médica urgente del interno alojado en el CPF de Ezeiza, "
       "con control odontológico, medicación psiquiátrica y turno con el servicio de traumatología del hospital penitenciario.",
       "La DGPDH reitera el pedido de historia clínica y de los estudios de laboratorio pendientes."]
VEH = ["Al Señor Jefe del Área de Automotores", "Se informa el kilometraje del vehículo oficial, la carga de combustible, el service de la camioneta "
       "y la renovación de la verificación técnica y el seguro de la flota de la Procuración.", "Se adjunta la planilla de choferes y la hoja de ruta."]
ref.guardar_archivo("Salud/Atención médica/2025", "Pedido de atención.docx", docx_bytes(*MED))
ref.guardar_archivo("Salud", "Atención médica/Reiteración historia clínica.docx", docx_bytes(MED[0], MED[2], "Reiteramos al CPF de Ezeiza lo solicitado sobre la medicación psiquiátrica y el turno odontológico."))
r = ref.guardar_archivo("", "Traslados/Flota/Informe de flota.docx", docx_bytes(*VEH))
assert r == "Traslados/Flota/Informe de flota.docx" and (ref.CARPETA / "Traslados" / "Flota").is_dir(), "al subir una carpeta entera se conservan las subcarpetas"
r2 = ref.guardar_archivo("Salud/Atención médica/2025", "Pedido de atención.docx", docx_bytes(*MED, "copia"))
assert r2.endswith("Pedido de atención (2).docx"), "nunca se pisa un archivo"
error(lambda: ref.guardar_archivo("", "planilla.xlsx", b"PK\x03\x04x"), "solo se aceptan")
error(lambda: ref.guardar_archivo("", "falso.pdf", b"MZxx"), "no coincide")
error(lambda: ref.guardar_archivo("", "../../evil.docx", docx_bytes("x")), "no se puede usar")
# copiado a mano con el Explorador: también se toma; uno repetido no se cuenta dos veces
(ref.CARPETA / "Traslados" / "Choferes.docx").write_bytes(docx_bytes(VEH[0], VEH[2], "Planilla de choferes del vehículo oficial y de la camioneta con su kilometraje y combustible."))
(ref.CARPETA / "Traslados" / "copia flota.docx").write_bytes((ref.CARPETA / "Traslados/Flota/Informe de flota.docx").read_bytes())
(ref.CARPETA / "Traslados" / "~$temporal.docx").write_bytes(b"PK\x03\x04basura")
res = ref.escanear("prueba")
assert res["nuevos"] == 6 and res["errores"] == 0, res
with db._conexion() as con:
    est = {f["ruta"]: f["estado"] for f in con.execute("SELECT ruta, estado FROM referencia_docs")}
assert list(est.values()).count("DUPLICADO") == 1 and "Traslados/~$temporal.docx" not in est, est
assert ref.escanear("prueba") == {"nuevos": 0, "actualizados": 0, "ausentes": 0, "errores": 0}, "sin cambios no relee nada"
(ref.CARPETA / "Traslados" / "copia flota.docx").unlink()
assert ref.escanear()["ausentes"] == 1
print("archivos y lectura OK")

# ---------------------------------------------------------------- términos que se respetan
t = ref.terminos_institucionales()
assert {"Ezeiza", "CPF", "DGPDH"} <= t, sorted(t)[:40]
assert "pabellón" not in t and "médica" not in t
assert ref.respetar_terminos("Pedido al CPF de Ezeiza.", "Pedido al CFP de Ezequiel.") == "Pedido al CPF de Ezeiza."
assert ref.respetar_terminos("el interno vera al medico", "el interno verá al médico") == "el interno verá al médico", "lo común se sigue corrigiendo"
assert ref.respetar_terminos("La DGPDH y el CPF vera", "La DGDH y el CPF verá") == "La DGPDH y el CPF verá"
# con el diccionario general (en la PC está pyspellchecker; acá se simula uno chico si no está instalado)
if nucleo.spell is None:
    class _Dic:
        palabras = {"pabellón", "módulo", "señor", "director", "complejo", "otro", "el", "del", "de", "y", "interno", "médico", "verá"}
        word_frequency = type("W", (), {"keys": lambda self: _Dic.palabras})()
        def known(self, ws): return {w for w in ws if w in self.palabras}
    nucleo.spell = _Dic(); ref._DICC["sin_tildes"] = None
if True:
    ref._invalidar()
    ref.guardar_archivo("Salud", "Pabellones.docx", docx_bytes("El pabellon 3 del modulo.", "Otro pabellon y otro modulo del CPF de Ezeiza."))
    ref.escanear(); t = ref.terminos_institucionales()
    assert "pabellon" not in t and "modulo" not in t, "a una palabra común le falta la tilde: se sigue corrigiendo"
    assert {"Ezeiza", "CPF"} <= t and "Señor" not in t and "Complejo" not in t, "las palabras del diccionario no son términos propios"
# dentro de la corrección
_lt, _ia = nucleo._texto_tras_languagetool, nucleo._texto_tras_ia
nucleo._texto_tras_languagetool = lambda x: (x.replace("Ezeiza", "Ezequiel"), [])
nucleo._texto_tras_ia = lambda x: x.replace("vera", "verá")
final, cambios, _ = nucleo.generar_propuesta_parrafo("El interno del CPF de Ezeiza vera al médico de guardia la semana próxima sin falta alguna.")
assert "Ezeiza" in final and "verá" in final and "Ezequiel" not in final, final
nucleo._texto_tras_languagetool, nucleo._texto_tras_ia = _lt, _ia
print("términos OK")

# ---------------------------------------------------------------- parecidos y estilo
s = ref.sugerir_carpeta("Se pide turno con traumatología y la historia clínica del interno, con la medicación psiquiátrica indicada.")
assert s and s["carpeta"].startswith("Salud"), s
s = ref.sugerir_carpeta("Pedimos el service de la camioneta, el seguro del vehículo y la carga de combustible para los choferes.")
assert s and s["carpeta"].startswith("Traslados"), s
assert ref.sugerir_carpeta("") is None
ej = ref.ejemplo_de_estilo("atención médica, historia clínica y medicación psiquiátrica del interno en Ezeiza")
assert ej and "Ezeiza" in ej
capturado = {}
_disp, _cons = nucleo.ia_disponible, nucleo.consultar_ollama
nucleo.ia_disponible = lambda p: True
nucleo.consultar_ollama = lambda proceso, prompt, **k: (capturado.update(prompt=prompt), '{"sugerencias": []}')[1]
analisis.sugerir_mejoras_desde_informe([{"texto": "Se solicita atención médica e historia clínica del interno con medicación psiquiátrica.", "ubicacion": "body:0"}],
                                       "Informe médico: el interno requiere turno de traumatología.")
assert "<<<EJEMPLO DE ESTILO>>>" in capturado["prompt"] and "no copies ningún dato" in capturado["prompt"]
nucleo.ia_disponible, nucleo.consultar_ollama = _disp, _cons
print("parecidos y estilo OK")

# ---------------------------------------------------------------- pantallas
R = main.app.rutas
html = R[("GET", "/ia/referencia")](req(direc, "/ia/referencia"), carpeta="Salud/Atención médica").body
assert "Atención médica" in html and "Reiteración historia clínica.docx" in html and 'href="/ia/referencia?carpeta=Salud/Atenci%C3%B3n%20m%C3%A9dica/2025"' in html.replace("%2F", "/")
assert "Ezeiza" in html and "webkitdirectory" in html
try:
    R[("GET", "/ia/referencia")](req(ana, "/ia/referencia"), carpeta="")
    raise AssertionError("un asesor no entra")
except main.HTTPException as e:
    assert e.status_code == 403
rr = req(direc, "/ia/referencia", form={"padre": "Traslados", "nombre": "Licencias"})
resp = run(R[("POST", "/ia/referencia/carpeta")](rr))
assert rr.session["flash"] == "Carpeta «Licencias» creada correctamente." and "carpeta=Traslados/Licencias" in resp.headers["location"].replace("%2F", "/")
rr = req(direc, "/ia/referencia", form={"padre": "Traslados", "nombre": "licencias"}); run(R[("POST", "/ia/referencia/carpeta")](rr))
assert rr.session["flash"].startswith("⚠ Ya hay una carpeta")
class Arch:
    def __init__(self, nombre, datos): self.filename = nombre; self._d = datos
    async def read(self): return self._d
ref.escanear_en_segundo_plano = lambda u=None: ref.escanear(u)  # en la prueba, sin hilo
rr = req(direc, "/ia/referencia", form={"carpeta": "Traslados", "archivos": [Arch("a.docx", docx_bytes(*VEH, "uno")), Arch("b.docx", docx_bytes(*VEH, "dos")), Arch("x.txt", b"hola")],
                                        "rutas": json.dumps(["Viejos/2019/a.docx", "Viejos/b.docx", "Viejos/x.txt"])})
rr.headers = {"x-requested-with": "fetch"}
r = run(R[("POST", "/ia/referencia/subir")](rr))
assert json.loads(r.body)["guardados"] == 2 and (ref.CARPETA / "Traslados/Viejos/2019/a.docx").is_file()
assert "Se agregaron 2 documento(s) correctamente" in rr.session["flash"] and "solo se aceptan" in rr.session["flash"]
with db._conexion() as con:
    did = con.execute("SELECT id FROM referencia_docs WHERE ruta = 'Traslados/Viejos/b.docx'").fetchone()["id"]
    assert con.execute("SELECT estado FROM referencia_docs WHERE id = ?", (did,)).fetchone()["estado"] == "INDEXADO"
rr = req(direc, "/ia/referencia"); run(R[("POST", "/ia/referencia/doc/{doc_id:int}/retirar")](rr, did))
assert "retirado de la referencia" in rr.session["flash"] and ref.obtener(did)["estado"] == "RETIRADO"
assert (ref.CARPETA / "_retirados/Traslados/Viejos/b.docx").is_file() and not (ref.CARPETA / "Traslados/Viejos/b.docx").exists(), "nada se borra"
assert ref.escanear()["ausentes"] == 0, "lo retirado no se cuenta como desaparecido"
html = main.admin_panel(req(direc, "/admin"), "ia", "referencia").body
assert "Abrir documentos de referencia" in html
# ficha de la nota: a qué carpeta se parece
nid = nota("Pedido médico.docx", ["PROCESANDO", "PROCESADA"])
db.indexar_texto(nid, "Se solicita turno de traumatología, historia clínica y medicación psiquiátrica del interno alojado en Ezeiza.")
html = main.ver_nota(req(direc, f"/nota/{nid}"), nid).body
assert "Se parece a documentos firmados de: Salud" in html, "propuesta visible en Análisis automático"
print("pantallas OK")
print("ALL OK")

# ---------------------------------------------------------------- eliminar, limpiar y reordenar (son copias para la IA)
from reportlab.pdfgen import canvas
b = io.BytesIO(); c = canvas.Canvas(b); c.rect(50, 50, 100, 100); c.save()
(ref.CARPETA / "Escaneos").mkdir(); (ref.CARPETA / "Escaneos" / "escaneado.pdf").write_bytes(b.getvalue())
(ref.CARPETA / "Escaneos" / "roto.docx").write_bytes(b"PK\x03\x04" + b"0" * 200)
(ref.CARPETA / "Vacia" / "Sub" / "Subsub").mkdir(parents=True)
(ref.CARPETA / "Traslados" / "repetido.docx").write_bytes((ref.CARPETA / "Traslados/Flota/Informe de flota.docx").read_bytes())
ref.escanear()
r = ref.resumen()
assert r["ilegibles"] == 2 and r["repetidos"] >= 1 and r["vacias"] >= 3, (r["ilegibles"], r["repetidos"], r["vacias"])
html = R[("GET", "/ia/referencia")](req(direc, "/ia/referencia"), carpeta="", reorden="").body
assert "Ordenar y limpiar" in html and "Eliminar carpetas vacías" in html and "Reordenar todos los documentos por" in html
rr = req(direc, "/x", form={"que": "archivos", "repetidos": "1"}); run(R[("POST", "/ia/referencia/limpiar")](rr))
assert rr.session["flash"].startswith("Se eliminaron") and not (ref.CARPETA / "Escaneos").exists() and not (ref.CARPETA / "Vacia").exists()
assert not (ref.CARPETA / "Traslados" / "repetido.docx").exists() and (ref.CARPETA / "Traslados/Flota/Informe de flota.docx").is_file(), "se borra la copia repetida, no el original"
r = ref.resumen(); assert r["ilegibles"] == 0 and r["repetidos"] == 0 and r["vacias"] == 0
# uno por uno
with db._conexion() as con:
    did = con.execute("SELECT id, ruta FROM referencia_docs WHERE estado = 'INDEXADO' AND ruta LIKE 'Traslados/Choferes%'").fetchone()
rr = req(direc, "/x"); run(R[("POST", "/ia/referencia/doc/{doc_id:int}/eliminar")](rr, did["id"]))
assert "eliminado" in rr.session["flash"] and not (ref.CARPETA / did["ruta"]).exists() and ref.obtener(did["id"])["estado"] == "ELIMINADO"
assert ref.escanear()["ausentes"] == 0
# reordenar por tipo y año
p = ref.propuesta_reorden("tipo_anio")
assert p["se_mueven"] >= 1 and all("/" in d for d, _ in p["destinos"]) and any(d.endswith("/2025") or d.endswith("/2026") for d, _ in p["destinos"]), p["destinos"]
html = R[("GET", "/ia/referencia")](req(direc, "/ia/referencia"), carpeta="", reorden="tipo_anio").body
assert "Así quedaría ordenado" in html and "Reordenar así" in html
usables = ref.resumen()["usables"]
rr = req(direc, "/x", form={"criterio": "tipo_anio"}); run(R[("POST", "/ia/referencia/reordenar")](rr))
assert rr.session["flash"].startswith("Listo:"), rr.session["flash"]
assert ref.propuesta_reorden("tipo_anio")["se_mueven"] == 0, "ya quedó ordenado"
assert ref.resumen()["usables"] == usables and ref.escanear() == {"nuevos": 0, "actualizados": 0, "ausentes": 0, "errores": 0}, "nada se pierde ni se relee"
assert not (ref.CARPETA / "Salud" / "Atención médica").exists(), "las carpetas viejas que quedan vacías se borran"
with db._conexion() as con:
    for f in con.execute("SELECT ruta FROM referencia_docs WHERE estado = 'INDEXADO'"):
        assert (ref.CARPETA / f["ruta"]).is_file(), f["ruta"]
s2 = ref.sugerir_carpeta("Se pide turno con traumatología y la historia clínica del interno, con la medicación psiquiátrica indicada.")
assert s2 and "/" in s2["carpeta"], s2
print("eliminar, limpiar y reordenar OK")
