"""Especificación de octubre 2026: firma continua, equipo y asesor editables, Modo Seguro Institucional,
correcciones como control de cambios y IA las 24 horas los fines de semana."""
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
import types, asyncio, json, shutil, socket
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

# ---------------------------------------------------------------- 5) firma continua
nucleo.insertar_firma_en_archivo = lambda o, d, rol_firmante=None, **k: shutil.copy(o, d)
a, b, c = procesada("Nota A.docx"), procesada("Nota B urgente.docx", "URGENTE"), procesada("Nota C.docx")
cola = [n["id"] for n in main._cola_firma(direc)]
assert cola[0] == b and set(cola) == {a, b, c}, "primero lo urgente, después por antigüedad"
ses = {}
r = main.firmar_nota(req(direc, f"/nota/{b}", ses), b)
assert r.headers["HX-Redirect"] == f"/nota/{cola[1]}?serie=1", r.headers
assert ses.get("serie_firmadas") == 1 and "Restan 2" in ses.get("flash", "") or "Restan 2" in json.dumps(ses), ses
html = main.ver_nota(types.SimpleNamespace(**{**vars(req(direc, f"/nota/{cola[1]}", ses)), "query_params": {"serie": "1"}}), cola[1]).body
assert "Documento 2 de 3" in html and "Restan 1" in html, "contador de avance"
r = main.firmar_nota(req(direc, "/", ses), cola[1]); assert r.headers["HX-Redirect"] == f"/nota/{cola[2]}?serie=1"
r = main.firmar_nota(req(direc, "/", ses), cola[2]); assert r.headers["HX-Redirect"] == "/firma-terminada" and "serie_firmadas" not in ses
html = main.firma_terminada(req(direc)).body
assert "No existen más documentos pendientes de firma" in html and 'href="/despacho"' in html and "Bandeja de firmas" in html and 'href="/inicio"' in html
assert all(db.obtener_nota(i)["estado"] == "FIRMADA" for i in (a, b, c)), "ninguna redirección al despacho; quedan firmadas"
print("5 firma continua OK")

# ---------------------------------------------------------------- 2) equipo y asesor editables
n = procesada("Nota reasignar.docx")
op = main._opciones_asignacion(direc, db.obtener_nota(n)); assert op["cambia_equipo"] and {u["id"] for u in op["asesores"]} >= {ana["id"], beto["id"], caro["id"], coo["id"]}
op = main._opciones_asignacion(coo, db.obtener_nota(n)); assert not op["cambia_equipo"] and {u["id"] for u in op["asesores"]} == {ana["id"], beto["id"], coo["id"]}
assert main._opciones_asignacion(ana, db.obtener_nota(n)) is None
r = main.reasignar_nota(req(coo), n, str(E2), str(beto["id"]))
x = db.obtener_nota(n); assert x["usuario_id"] == beto["id"] and x["equipo_id"] == E1, "la coordinación cambia el asesor, no el equipo"
assert any("Te asignaron" in m["texto"] for m in db.listar_notificaciones(beto["id"]))
try:
    main.reasignar_nota(req(coo), n, "", str(caro["id"])); raise SystemExit("asesor de otro equipo")
except main.HTTPException as e:
    assert e.status_code == 422
main.reasignar_nota(req(direc), n, str(E2), str(beto["id"]))
assert db.obtener_nota(n)["equipo_id"] == E1, "asesor que no es del equipo elegido: no cambia nada"
main.reasignar_nota(req(direc), n, str(E2), str(caro["id"]))
x = db.obtener_nota(n); assert x["equipo_id"] == E2 and x["usuario_id"] == caro["id"]
aud = db.reasignaciones_de(n)
assert [(r_["campo"], r_["antes"], r_["despues"], r_["usuario"]) for r_ in aud][:2] == [("equipo", "Equipo Salud", "Equipo CPF I", "Director General"), ("asesor", "Beto Ruiz", "Caro CPF", "Director General")] or \
       {(r_["campo"], r_["antes"], r_["despues"]) for r_ in aud} >= {("equipo", "Equipo Salud", "Equipo CPF I"), ("asesor", "Beto Ruiz", "Caro CPF"), ("asesor", "Ana Pérez", "Beto Ruiz")}, aud
assert all(r_["fecha"] for r_ in aud)
assert any("Reasignada por Director General" in (h_.get("comentario") or "") for h_ in db.obtener_nota(n)["historial"])
try:
    main.reasignar_nota(req(ana), n, "", str(ana["id"])); raise SystemExit("el asesor no reasigna")
except main.HTTPException as e:
    assert e.status_code == 403
# en bloque no se cambia de equipo
n1, n2 = procesada("Bloque 1.docx"), procesada("Bloque 2.docx")
db.crear_bloque("B", E1, ana["id"], [n1, n2])
main.reasignar_nota(req(direc), n1, str(E2), str(caro["id"])); assert db.obtener_nota(n1)["equipo_id"] == E1
html = main.ver_nota(req(direc, f"/nota/{n}"), n).body
assert 'action="/nota/%d/asignacion"' % n in html and 'name="equipo_id"' in html and "Reasignar" in html
html = main.ver_nota(req(caro, f"/nota/{n}"), n).body
assert "/asignacion" not in html and "Equipo: Equipo CPF I" in html
print("2 reasignación OK")

# ---------------------------------------------------------------- 4) Modo Seguro Institucional
for host, esp in (("localhost", True), ("127.0.0.1", True), ("10.2.3.4", True), ("192.168.1.10", True), ("SRV-DGPDH", True),
                  ("8.8.8.8", False), ("api.openai.com", False), ("smtp.office365.com", False)):
    assert soberania.es_interno(host) is esp, host
assert soberania.CONFIG["instalado"], "main lo instala al arrancar"
antes = len(soberania.listar())
try:
    sk = socket.socket(); sk.settimeout(0.5); sk.connect(("8.8.8.8", 53)); raise SystemExit("debía bloquear")
except soberania.SalidaBloqueada:
    pass
finally:
    sk.close()
reg = soberania.listar()[0]; assert len(soberania.listar()) == antes + 1 and reg["resultado"] == "BLOQUEADA" and "8.8.8.8" in reg["destino"] and reg["fecha"] and reg["usuario"]
import requests
try:
    ses_r = requests.Session(); ses_r.trust_env = False   # sin el proxy del entorno de pruebas
    ses_r.get("http://203.0.113.9/", timeout=1); raise SystemExit("requests también")
except Exception as e:
    assert "Modo Seguro" in repr(e) or isinstance(e, soberania.SalidaBloqueada) or "SalidaBloqueada" in repr(e), repr(e)
sk = socket.socket(); sk.settimeout(0.5)
try:
    sk.connect(("127.0.0.1", 9))   # interno: lo deja intentar (rechazo normal del sistema, no del modo seguro)
except soberania.SalidaBloqueada:
    raise SystemExit("lo interno no se bloquea")
except OSError:
    pass
finally:
    sk.close()
# destino autorizado: pasa y queda registrado
soberania.CONFIG["destinos_autorizados"] = ["203.0.113.7"]
sk = socket.socket(); sk.settimeout(0.3)
try:
    sk.connect(("203.0.113.7", 25))
except soberania.SalidaBloqueada:
    raise SystemExit("autorizado")
except OSError:
    pass
finally:
    sk.close()
assert soberania.listar()[0]["resultado"].startswith("PERMITIDA (destino autorizado)")
soberania.CONFIG["destinos_autorizados"] = []
# los sistemas propios configurados (portal, Mesa…) quedan autorizados; la IA y el correo externos, no
soberania.autorizar_sistemas_del_organismo(["https://portal.ppn.gov.ar/api", "", "http://10.0.0.5/mesa"])
assert soberania.clasificar("https://portal.ppn.gov.ar") == "autorizado" and soberania.clasificar("http://api.openai.com") == "externo"
soberania.autorizar_sistemas_del_organismo([])
# confirmación expresa (para funciones futuras): registra persona, datos y motivo
with soberania.salida_confirmada(direc, "203.0.113.8", "IA", "texto anonimizado de una nota", "prueba de servicio externo"):
    sk = socket.socket(); sk.settimeout(0.3)
    try: sk.connect(("203.0.113.8", 443))
    except soberania.SalidaBloqueada: raise SystemExit("confirmada")
    except OSError: pass
    finally: sk.close()
x = soberania.listar()[0]; assert x["resultado"] == "PERMITIDA (confirmada)" and x["usuario"] == "Director General" and x["motivo"] == "prueba de servicio externo"
try:
    with soberania.salida_confirmada(direc, "x", "IA", "", "sin datos"): pass
    raise SystemExit("sin datos no hay confirmación")
except soberania.SalidaBloqueada:
    pass
# respaldos en nube pública
assert soberania.ruta_en_nube(r"C:\Users\usuario\OneDrive - PPN\respaldos") and soberania.ruta_en_nube("/home/x/Google Drive/r") and not soberania.ruta_en_nube(r"\\servidor\respaldos")
respaldo.CONFIG["carpeta"] = tmp / "OneDrive" / "respaldos"; respaldo.CONFIG["carpeta_datos"] = tmp
ok, msg = respaldo.hacer_respaldo(); assert not ok and "nube pública" in msg and not (tmp / "OneDrive" / "respaldos" / "db").exists()
respaldo.CONFIG["carpeta"] = tmp / "respaldos_red"
ok, msg = respaldo.hacer_respaldo(); assert ok, msg
# panel
html = main.admin_panel(req(direc, "/admin/seguridad/modo-seguro"), "seguridad", "modo-seguro").body
assert "Modo Seguro Institucional" in html and "Activo" in html and "Registro de salidas al exterior" in html and "8.8.8.8" in html and "Bloqueada" in html
print("4 modo seguro OK")

# ---------------------------------------------------------------- 1) correcciones como control de cambios
v = cc.vista([{"fuente": "diccionario+languagetool", "original": "El Sr director del pabellon", "corregido": "El Sr. Director del pabellón"},
              {"fuente": "ia_ollama", "original": "a la brevedad posible", "corregido": "en el plazo de 5 días hábiles"},
              {"fuente": "languagetool", "original": "Se solicita, que informe", "corregido": "Se solicita que informe"}])
assert v["conteo"] == {"ortografia": 2, "gramatica": 1, "reformulacion": 1} and v["total"] == 4, v["conteo"]
assert v["filas"][0]["fuente"] == "Diccionario + Corrector ortográfico y gramatical" and v["filas"][1]["fuente"] == "IA"
nc = procesada("Nota con cambios.docx")
with db._conexion() as con:
    con.execute("UPDATE notas SET cambios_json = ?, es_nota = 1 WHERE id = ?", (json.dumps([{"fuente": "ia_ollama", "original": "a la brevedad", "corregido": "en el plazo de 5 días"}]), nc))
nota = db.obtener_nota(nc); nota["etapa"] = main.flujo.etapa_para_estado(nota["estado"])
html = main.templates.env.get_template("_estado_nota.html").render(request=req(direc), nota=nota, etapas=main.flujo.ETAPAS, usuario=direc)
assert nota.get("cambios")
if True:
    assert '<del class="diff-quitado tramo tramo--reformulacion"' in html and '<ins class="diff-agregado tramo tramo--reformulacion"' in html and "reformulación / estilo" in html
print("1 control de cambios OK")

# ---------------------------------------------------------------- 3) IA 24 horas los fines de semana
sab = datetime(2026, 10, 10, 12, 0, tzinfo=automatico._zona()); lun = datetime(2026, 10, 12, 12, 0, tzinfo=automatico._zona())
assert automatico.fines_de_semana_activo() and automatico.en_ventana(sab) and not automatico.en_ventana(lun), "activo por defecto; lunes a viernes igual que antes"
main.admin_ia_fines_de_semana(req(direc), "0")
assert not automatico.fines_de_semana_activo() and not automatico.en_ventana(sab)
main.admin_ia_fines_de_semana(req(direc), "1"); assert automatico.en_ventana(sab) and "las 24 horas" in automatico.descripcion_ventana()
print("3 fines de semana OK")
print("OK especificación octubre")
