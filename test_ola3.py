"""Ola 3: numeración anual, despacho del día, respuestas, entregas en mano y viajes, plantillas, caso, reiteración, vista, firma en serie, PDF/A, tiempos e informe."""
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
import asyncio, io, json, tempfile
from datetime import date, timedelta
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth, agentes, automatico, nucleo, circuito
import traslados as tr
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
nucleo.USAR_IA = False
main.CARPETA_DATOS = tmp
from docx import Document
def mkdoc(nombre, parrafos):
    d = Document()
    for p in parrafos: d.add_paragraph(p)
    r = tmp / nombre; d.save(r); return str(r)
anio = date.today().year

# ---- numeración (reglas)
assert circuito.leer_numero("123/2026") == (123, 2026) and circuito.leer_numero("45-26") == (45, 2026)
assert circuito.leer_numero("NO-2026-77") == (77, 2026) and circuito.leer_numero("12", 2025) == (12, 2025) and circuito.leer_numero("") == (None, None)
assert circuito.formatear("NOTA", 5, 2026) == "5/2026"
assert circuito.proponer("NOTA", anio) is None and circuito.proponer_texto("NOTA", anio) == "", "sin base del registro no propone"
db.fijar_contador(anio, "NOTA", 140, "Mesa", inicial=True)
assert circuito.proponer("NOTA", anio) == 141 and circuito.proponer("NOTA", anio, saltear=[141]) == 142
circuito.registrar_uso("NOTA", f"150/{anio}")
assert db.contador_numeracion(anio, "NOTA") == 150
circuito.registrar_uso("NOTA", f"145/{anio}")
assert db.contador_numeracion(anio, "NOTA") == 150, "nunca retrocede"
# Excel de registro
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Registro de notas DGPDH"]); ws.append([])
ws.append(["N° Nota", "Fecha", "Destinatario", "Proveído N°"])
ws.append([310, date(2025, 12, 20), "Juzgado", "40/2025"])
ws.append(["311/2025", date(2025, 12, 28), "SPF", None])
ws.append([12, date(anio, 2, 3), "CPF I", "3"])
buf = io.BytesIO(); wb.save(buf)
mx = circuito.leer_excel_registro(buf.getvalue(), "registro.xlsx")
assert mx[(2025, "NOTA")] == 311 and mx[(2025, "PROVEIDO")] == 40 and mx[(anio, "NOTA")] == 12 and mx[(anio, "PROVEIDO")] == 3, mx
print("numeración OK")

# ---- vista con formato y plantillas
d = Document(); p = d.add_paragraph(); r = p.add_run("Hola "); r2 = p.add_run("<script>alert(1)</script>"); r2.bold = True
t = d.add_table(rows=1, cols=2); t.rows[0].cells[0].text = "A"; ruta_html = tmp / "h.docx"; d.save(ruta_html)
h = circuito.docx_a_html(ruta_html)
assert "&lt;script&gt;" in h and "<script>" not in h and "<strong>" in h and "doc-tabla" in h
db.agregar_modelo_nota("x", "", mkdoc("modelo.docx", ["Al Sr. «Destinatario»", "Persona: [[Persona]] en «Unidad»."])) if False else None
n_pl = circuito.crear_plantillas_base(tmp / "modelos")
assert n_pl == 3 and len(db.listar_modelos_nota()) == 3 and circuito.crear_plantillas_base(tmp / "modelos") == 0
m = db.listar_modelos_nota()[0]
campos = circuito.campos_de_plantilla(m["archivo"])
assert "Destinatario" in campos and "Unidad" in campos and "Persona" in campos, campos
circuito.completar_plantilla(m["archivo"], {"Destinatario": "Director del CPF I", "Persona": "Juan Pérez"}, tmp / "lleno.docx")
txt = nucleo.texto_de_documento(str(tmp / "lleno.docx"))
assert "Director del CPF I" in txt and "Juan Pérez" in txt and "«Unidad»" in txt
print("vista y plantillas OK")

# ---- datos de prueba
eq = db.crear_equipo("Equipo A")
h_, s_ = auth.generar_hash_clave("Clave1234")
ana = db.obtener_usuario(db.crear_usuario("ana", h_, s_, "Ana", "ASESOR", eq))
coo = db.obtener_usuario(db.crear_usuario("coo", h_, s_, "Coord", "COORDINADOR", eq))
mesa = db.obtener_usuario(db.crear_usuario("mesa", h_, s_, "Mesa", "MESA_ASESOR", None))
sub = db.obtener_usuario(db.crear_usuario("sub", h_, s_, "Subdirectora", "SUBDIRECTOR", None))
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
unidad = db.proponer_unidades("Complejo Penitenciario Federal I de Ezeiza", 1) or db.listar_unidades()[:1]
UN = unidad[0]["id"]
class URL: path = "/"; query = ""
class Ses(dict): pass
def req(u, form=None, query=None):
    async def _f(): return form or {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(), session=Ses(), cookies={},
                                 headers={}, form=_f, client=types.SimpleNamespace(host="1"), query_params=query or {})
R = main.app.rutas
def ruta(m, p): return R[(m, p)]
cuerpo = ["Al Sr. Director del Complejo Penitenciario Federal I", "S/D",
          "Tengo el agrado de dirigirme a usted a fin de solicitar atención médica para PÉREZ, Juan (DNI 30.123.456). "
          "Se solicita informe dentro del plazo de cinco (5) días hábiles.", "Sin otro particular, saludo atentamente."]
def nota(nombre, estado="FIRMADA", u=ana):
    nid = db.crear_nota(nombre, eq, mkdoc(nombre, cuerpo), usuario_id=u["id"])
    automatico.clasificar_rapido(nid)
    for e in {"PROCESADA": ["PROCESANDO", "PROCESADA"], "FIRMADA": ["PROCESANDO", "PROCESADA", "FIRMADA"],
              "DESPACHADA": ["PROCESANDO", "PROCESADA", "FIRMADA", "DESPACHADA"]}.get(estado, []):
        db.actualizar_estado(nid, e)
    return nid
f1, f2 = nota("Nota uno.docx"), nota("Nota dos.docx")

# ---- despacho del día: agrupado y números correlativos propuestos
html = ruta("GET", "/despacho")(req(mesa)).body
assert "Despacho del día" in html and "Director del Complejo Penitenciario Federal I" in html
assert f'value="151/{anio}"' in html and f'value="152/{anio}"' in html, "lote correlativo sin repetir"
dd = main._datos_despacho(db.obtener_nota(f1)); assert dd["nro_nota"] == f"151/{anio}" and dd["nro_propuesto"]
print("despacho del día OK")

# ---- Mesa ve las despachadas; respuestas; entregas; viajes
d1 = nota("Nota despachada.docx", "DESPACHADA")
assert main._puede_ver_nota(db.obtener_nota(mesa["id"]) if False else mesa, db.obtener_nota(d1))
tok = db.USUARIO_ACTUAL.set(mesa)
r = asyncio.run(ruta("POST", "/nota/{nota_id}/respuesta")(req(mesa, {"fecha": "2026-10-05", "medio": "CORREO", "remitente": "CPF I", "resumen": "Se otorgó turno médico."}), d1, None))
assert r.status_code == 303 and db.respuestas_de(d1)[0]["remitente"] == "CPF I" and db.obtener_nota(d1)["respuesta_recibida_en"] == "2026-10-05"
assert any("Llegó respuesta" in x["texto"] for x in db.listar_notificaciones(ana["id"], 10)) and any("Llegó respuesta" in x["texto"] for x in db.listar_notificaciones(coo["id"], 10))
db.USUARIO_ACTUAL.reset(tok)
with db._conexion() as con: con.execute("UPDATE notas SET unidad_id = ?, tipo = 'NOTA A PPL' WHERE id = ?", (UN, d1))
assert any(x["id"] == d1 for x in db.notas_esperando_entrega(UN))
vid = tr.crear_viaje(dict(fecha=(date.today() + timedelta(days=3)).isoformat(), hora_salida="09:00", hora_regreso="13:00", unidad_id=UN, modalidad="CHOFER", motivo="Entrevistas"),
                     [{"usuario_id": coo["id"], "nombre": coo["nombre"], "equipo_nombre": "Equipo A"}], coo)
ruta("POST", "/nota/{nota_id}/viaje")(req(mesa), d1, vid)
docs = db.documentos_de_viaje(vid); assert docs and docs[0]["estado"] == "PENDIENTE"
assert not any(x["id"] == d1 for x in db.notas_esperando_entrega(UN)), "ya tiene viaje"
assert any("Llevás para entregar" in x["texto"] for x in db.listar_notificaciones(coo["id"], 10))
tok = db.USUARIO_ACTUAL.set(coo)
ruta("POST", "/viaje-documento/{doc_id}/{accion}")(req(coo), docs[0]["id"], "entregado", "Firmó la constancia")
db.USUARIO_ACTUAL.reset(tok)
env = [e for e in db.envios_de(d1) if e["medio"] == "MANO"][0]
assert env["viaje_id"] == vid and env["entregado_por"] == "Coord" and db.documentos_de_viaje(vid)[0]["estado"] == "ENTREGADO"
print("respuestas, entregas y viajes OK")

# ---- reiteración y plazos por destinatario
d2 = nota("Nota sin respuesta.docx", "DESPACHADA")
db.fijar_plazo_respuesta(d2, {"cantidad": 5, "unidad": "dias", "habiles": True, "texto": "5 días hábiles"})
with db._conexion() as con: con.execute("UPDATE notas SET respuesta_vence = ? WHERE id = ?", ((date.today() - timedelta(days=2)).isoformat(), d2))
assert agentes.sugerir_reiteraciones() >= 1 and db.sugerencias_de(d2, "REITERACION")
html = ruta("GET", "/nota/{nota_id}/reiteracion")(req(coo), d2).body
assert "reiterar el pedido" in html
res = asyncio.run(ruta("POST", "/nota/{nota_id}/reiteracion")(req(coo, {"texto": "Reitero", "accion": "usar"}), d2))
assert str(res.path).endswith(".docx") and db.sugerencias_de(d2, "REITERACION") == []
db.recordar_plazo_destinatario("Al Sr. Director del Complejo Penitenciario Federal I", {"cantidad": 10, "unidad": "dias", "habiles": True})
d3 = nota("Nota otra.docx", "DESPACHADA")
with db._conexion() as con: con.execute("DELETE FROM sugerencias_ia WHERE nota_id = ? AND agente = 'PLAZO'", (d3,))
agentes.sugerir_plazos_por_destinatario()
sp = db.sugerencias_de(d3, "PLAZO"); assert sp and sp[0]["contenido"]["cantidad"] == 10 and sp[0]["origen"] == "destinatario"
print("reiteración y plazos por destinatario OK")

# ---- caso, circuito, vista, informe, numeración (pantallas)
ent = next(e for e in db.entidades_de(d1) if e["tipo"] == "PERSONA")
html = ruta("GET", "/caso")(req(coo), "PERSONA", ent["norm"], ent["valor"]).body
assert "Carpeta del caso" in html and "Nota despachada.docx" in html and "Se otorgó turno médico" in html and "Entregada en mano" in html
html = ruta("GET", "/circuito")(req(coo), "").body
assert "Firmada, a despacho" in html and "Nota uno.docx" in html
h = ruta("GET", "/nota/{nota_id}/vista")(req(coo), f1, "actual").body.decode()
assert "hoja-documento" in h and "atención médica" in h
assert "Numeración de notas" in ruta("GET", "/numeracion")(req(mesa), anio).body
ruta("POST", "/numeracion/fijar")(req(mesa), anio, "PROVEIDO", "20")
assert circuito.proponer_texto("PROVEIDO", anio) == f"21/{anio}"
x = ruta("GET", "/numeracion/registro.xlsx")(req(mesa), anio); assert x.body[:2] == b"PK"
inf = ruta("GET", "/reportes/informe.docx")(req(direc), f"{anio}")
doc = Document(io.BytesIO(inf.body)); textos = "\n".join(p.text for p in doc.paragraphs)
assert "Informe del año" in textos and "Notas" in textos
assert circuito.periodo("2026-T3")[:2] == ("2026-07-01", "2026-09-30") and circuito.periodo("2026-T4")[1] == "2026-12-31"
assert any(t["equipo"] == "Equipo A" for t in circuito.tiempos_por_equipo())
print("caso, circuito, vista, numeración e informe OK")

# ---- plantillas: crear borrador
r = asyncio.run(ruta("POST", "/biblioteca/modelos/{modelo_id}/usar")(req(ana, {"campo_0": "Director del CPF I", "accion": "borrador", "nombre": "Pedido colchón"}), m["id"]))
assert r.status_code == 303 and "/nota/" in r.headers["location"]
nb = db.obtener_nota(int(r.headers["location"].rsplit("/", 1)[1])); assert nb["estado"] == "BORRADOR" and nb["usuario_id"] == ana["id"]
print("plantillas con borrador OK")

# ---- firma en serie y delegación
p1, p2 = nota("Para firmar 1.docx", "PROCESADA"), nota("Para firmar 2.docx", "PROCESADA")
r = ruta("GET", "/firma-en-serie")(req(direc), 0); assert r.headers["location"].endswith("?serie=1")
primero = int(r.headers["location"].split("/nota/")[1].split("?")[0])
r2 = ruta("GET", "/firma-en-serie")(req(direc), primero); assert r2.headers["location"] != r.headers["location"]
info = main.templates.env.globals["cola_firma_info"](direc, primero); assert info["pos"] == 1 and info["total"] >= 2
import fechas, datetime as _dtm; _hoy_local = _dtm.datetime.now(fechas.zona()).date()  # la vigencia se mide en hora argentina
db.crear_ausencia(direc["id"], _hoy_local.isoformat(), (_hoy_local + timedelta(days=5)).isoformat(), sub["id"])
assert agentes.avisar_firma_delegada() == 1 and agentes.avisar_firma_delegada() == 0
assert any("la firma queda a tu cargo" in x["texto"] for x in db.listar_notificaciones(sub["id"], 10))
print("firma en serie y delegación OK")

# ---- proveído numerado
tok = db.USUARIO_ACTUAL.set(coo)
res = asyncio.run(main.usar_proveido(req(coo, {"texto": "VISTO…", "medio": "CORREO", "origen": "reglas", "accion": "usar", "numero": f"21/{anio}"}), f1))
assert str(res.path).endswith(".docx") and db.proveido_en_uso(f"21/{anio}") and db.contador_numeracion(anio, "PROVEIDO") == 21
assert "PROVEÍDO N°" in nucleo.texto_de_documento(str(res.path))
r = asyncio.run(main.usar_proveido(req(coo, {"texto": "x", "medio": "CORREO", "accion": "usar", "numero": f"21/{anio}"}), f1))
assert r.status_code == 303, "número repetido"
db.USUARIO_ACTUAL.reset(tok)
print("proveído numerado OK")

# ---- PDF/A
pdf = tmp / "a.pdf"
try:
    motor = circuito.convertir_a_pdfa(mkdoc("pa.docx", ["Texto de prueba"]), pdf)
    assert pdf.is_file() and circuito.es_pdfa(pdf), motor
    print("PDF/A OK")
except RuntimeError as e:
    print("PDF/A omitido (sin LibreOffice):", e)
print("ALL OK")
