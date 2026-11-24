"""Modificación 2: rechazadas en el listado (retirar, duplicar) y avisos o archivo por inactividad desde la última actividad."""
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


import rutas_notas_ia
from datetime import date, timedelta
R = main.app.rutas
def rech(nombre, u=None):
    nid = nota(nombre, ["SUBIDA"], u)
    db.rechazar_nota(nid, "No corresponde", "Cora Coord") if hasattr(db, "rechazar_nota") else db.actualizar_estado(nid, "RECHAZADA", comentario="No corresponde")
    assert estado(nid) == "RECHAZADA"; return nid
a, b, c = rech("Rechazada A.docx"), rech("Rechazada B.docx"), rech("Rechazada C.docx", beto)
html = main.index(req(ana, "/", q={"ver": "todos"}), **dict(IDX, ver="todos")).body
assert f'value="{a}" form="form-bloques" data-rechazada="1"' in html and "Retirar del listado" in html and "Duplicar para reintentar" in html
assert "eliminar" not in html.lower().split("form-bloques")[-1][:3000], "en la pantalla no se dice «eliminar»"
# retirar: baja lógica y recuperable; solo lo que la persona gestiona
r = run(R[("POST", "/notas/rechazadas/retirar")](req(ana, "/", form={"nota_ids": [str(a), str(c)]})))
assert r.status_code == 303 and estado(a) == "DESCARTADA" and estado(c) == "RECHAZADA", "lo de otro equipo no se toca"
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion = 'Retirar del listado' AND entidad_id = ?", (str(a),)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM notas WHERE id = ?", (a,)).fetchone()[0] == 1, "nunca se borra"
html = main.index(req(ana, "/", q={"f_tarea": "descartadas"}), **dict(IDX, f_tarea="descartadas", ver="todos")).body
assert "Rechazada A.docx" in html
main.recuperar_documento(req(ana, f"/nota/{a}/recuperar"), a); assert estado(a) == "RECHAZADA"
# duplicar: borrador nuevo del agente con una copia del archivo; la rechazada queda igual
r = run(R[("POST", "/notas/rechazadas/duplicar")](req(ana, "/", form={"nota_ids": [str(b)]})))
nueva = int(r.headers["location"].rsplit("/", 1)[1])
n2 = db.obtener_nota(nueva)
assert n2["estado"] == "BORRADOR" and n2["nombre_original"] == "Rechazada B.docx" and n2["ruta_original"] != db.obtener_nota(b)["ruta_original"]
assert Path(n2["ruta_original"]).is_file() and estado(b) == "RECHAZADA" and n2["usuario_id"] == ana["id"]
assert run(R[("POST", "/notas/rechazadas/duplicar")](req(ana, "/", form={"nota_ids": [str(nueva)]}))).headers["location"] == "/", "solo rechazadas"

# inactividad: desde la ÚLTIMA actividad. a: actividad hoy (recién recuperada); b: hace 7 días; c: hace 15 días
def envejecer(nid, dias):
    f = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")
    with db._conexion() as con:
        con.execute("UPDATE historial_estados SET fecha = ? WHERE nota_id = ?", (f, nid))
        con.execute("UPDATE notas SET actualizado_en = ? WHERE id = ?", (f, nid))
from datetime import datetime, timezone
envejecer(b, 7); envejecer(c, 15)
with db._conexion() as con: con.execute("DELETE FROM notificaciones")
r = rutas_notas_ia.revisar_inactividad()
assert r == {"avisados": 1, "archivados": 1}, r
assert estado(b) == "RECHAZADA" and estado(c) == "DESCARTADA" and estado(a) == "RECHAZADA"
with db._conexion() as con:
    avisos = [dict(x) for x in con.execute("SELECT usuario_id, texto FROM notificaciones")]
    aud = con.execute("SELECT usuario, motivo FROM auditoria WHERE accion = 'Archivo automático por inactividad'").fetchone()
assert any(x["usuario_id"] == ana["id"] and "sin movimiento hace 7 días" in x["texto"] for x in avisos)
assert any(x["usuario_id"] == beto["id"] and "se archivó" in x["texto"] for x in avisos)
assert aud["usuario"] == "Sistema" and aud["motivo"] == "Vencimiento por inactividad (15 días)"
assert rutas_notas_ia.revisar_inactividad().get("omitido"), "una vez por día"
assert rutas_notas_ia.revisar_inactividad(forzar=True) == {"avisados": 0, "archivados": 0}, "el aviso no se repite antes de 7 días"
assert rutas_notas_ia.revisar_inactividad(hoy=date.today() + timedelta(days=7), forzar=True)["avisados"] >= 1
main.recuperar_documento(req(beto, f"/nota/{c}/recuperar"), c)
assert estado(c) == "RECHAZADA" and rutas_notas_ia.revisar_inactividad(forzar=True)["archivados"] == 0, "reactivada: la cuenta reinicia"
# ---------------------------------------------------------------- propuesta 51: QR de verificación (optativo)
from dgpdh.servicios import qr_verificacion as qv
pdf = tmp / "desp.pdf"; pdf.write_bytes(pdf_bytes("Nota despachada"))
assert rutas_notas_ia.sellar_qr_despacho(b, "123/26", pdf) is None, "apagado no cambia nada"
qv.CONFIG.update(activo=True, url="https://dgpdh.interno")
antes = pdf.read_bytes()
codigo = rutas_notas_ia.sellar_qr_despacho(b, "123/26", pdf)
assert codigo and codigo.startswith(f"{b}-") and pdf.read_bytes() != antes
from pypdf import PdfReader
assert "Nota N° 123/26" in PdfReader(str(pdf)).pages[-1].extract_text()
html = R[("GET", "/verificar/{codigo}")](req(ana, f"/verificar/{codigo}"), codigo).body
assert "Rechazada B.docx" in html and "Comparar" in html
class Sub:
    def __init__(self, b_): self.b = b_
    async def read(self): return self.b
html = run(R[("POST", "/verificar/{codigo}")](req(ana, "/", form={"archivo": Sub(pdf.read_bytes())}), codigo)).body
assert "Coincide:" in html
html = run(R[("POST", "/verificar/{codigo}")](req(ana, "/", form={"archivo": Sub(antes)}), codigo)).body
assert "No coincide:" in html
html = R[("GET", "/verificar/{codigo}")](req(beto, f"/verificar/{codigo}"), codigo).body
assert "Comparar" in html and "Rechazada B.docx" not in html, "quien no ve la nota no ve su nombre"
assert "no corresponde" in R[("GET", "/verificar/{codigo}")](req(ana, "/verificar/X"), "X").body
qv.CONFIG.update(activo=False)
# ---------------------------------------------------------------- propuesta 3: vínculos con tipo entre documentos
v1 = nota("Pedido original.docx", ["SUBIDA"]); v2 = nota("Reiteración.docx", ["SUBIDA"])
with db._conexion() as con: con.execute("UPDATE notas SET nro_nota = '123/26' WHERE id = ?", (v1,))
db.indexar_texto(v2, "Se reitera lo pedido en la nota N° 123/26 y en respuesta a la NO-2026-00001111-APN-DD#MJ.")
from dgpdh.servicios import vinculos as vs
with db._conexion() as con:
    props = vs.proponer(con, v2, "Se reitera lo pedido en la nota N° 123/26 y en respuesta a la NO-2026-00001111-APN-DD#MJ.")
assert {(x.tipo, x.destino_id, x.referencia_externa) for x in props} == {("reitera_a", v1, None), ("responde_a", None, "NO-2026-00001111")}
html = main.ver_nota(req(ana, f"/nota/{v2}"), v2).body
assert "Vínculos con otros documentos" in html and "Propuesta: <strong>Reitera a</strong> Pedido original.docx" in html
r = run(R[("POST", "/nota/{nota_id}/vinculo")](req(ana, "/", form={"tipo": "reitera_a", "destino_id": str(v1), "accion": "confirmar", "propuesta": "1"}), v2))
assert r.status_code == 303
html = main.ver_nota(req(ana, f"/nota/{v1}"), v1).body
assert "Reiterada por" in html and "Reiteración.docx" in html, "se ve desde la otra nota"
html = main.ver_nota(req(ana, f"/nota/{v2}"), v2).body
assert "Propuesta: <strong>Reitera a</strong>" not in html and "Reitera a</strong>" in html
run(R[("POST", "/nota/{nota_id}/vinculo")](req(ana, "/", form={"tipo": "anexo_de", "nro_nota": "123/26", "accion": "confirmar"}), v2))
with db._conexion() as con:
    assert [x["tipo"] for x in vs.de_nota(con, v2)] == ["reitera_a", "anexo_de"]
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion = 'Vincular documentos'").fetchone()[0] == 2
falla(lambda: R[("POST", "/nota/{nota_id}/vinculo")](req(beto, "/", form={"tipo": "reitera_a", "destino_id": str(v1)}), v2), 404)
# ---------------------------------------------------------------- propuesta 5: plan de retención (pantalla y permisos)
html = R[("GET", "/admin/retencion")](req(direc, "/admin/retencion")).body
assert "Plan de retención" in html and "Marcar lo vencido" in html and "no se borra" in html
run(R[("POST", "/admin/retencion")](req(direc, "/", form={"accion": "serie", "tipo": "NOTA", "plazo": "1", "destino": "BAJA"})))
with db._conexion() as con:
    con.execute("UPDATE notas SET tipo_documental = 'NOTA', estado = 'DESPACHADA', fecha_nota = '2020-01-01' WHERE id = ?", (v1,))
run(R[("POST", "/admin/retencion")](req(direc, "/", form={"accion": "marcar"})))
assert "Pedido original.docx" in R[("GET", "/admin/retencion")](req(direc, "/admin/retencion")).body
assert "Plazo de guarda vencido" in main.ver_nota(req(direc, f"/nota/{v1}"), v1).body
run(R[("POST", "/admin/retencion")](req(direc, "/", form={"accion": "retirar", "nota_id": str(v1), "motivo": "Resolución 1/2026"})))
assert "Retirado (retención)" in main.ver_nota(req(direc, f"/nota/{v1}"), v1).body and estado(v1) == "DESPACHADA"
falla(lambda: R[("GET", "/admin/retencion")](req(ana, "/admin/retencion")), 403)
# ---------------------------------------------------------------- propuesta 44: versión testada
db.indexar_texto(v1, "Me dirijo por el Sr. Juan Gómez, DNI 30.123.456, con tratamiento psiquiátrico, en el Expte. EP 1234/2025.")
html = R[("GET", "/nota/{nota_id}/testar")](req(ana, f"/nota/{v1}/testar"), v1).body
assert 'value="30.123.456" checked' in html and 'value="Juan Gómez"' in html and "tratamiento psiquiátrico" in html
r = run(R[("POST", "/nota/{nota_id}/testar")](req(ana, "/", form={"tachar": ["30.123.456", "Juan Gómez"], "otros": "EP 1234/2025"}), v1))
from pypdf import PdfReader
txt = PdfReader(io.BytesIO(r.body)).pages[0].extract_text()
assert "30.123.456" not in txt and "Gómez" not in txt and "1234/2025" not in txt and "[TESTADO]" in txt and "tratamiento" in txt
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion = 'Versión testada' AND entidad_id = ?", (str(v1),)).fetchone()[0] == 1
falla(lambda: R[("GET", "/nota/{nota_id}/testar")](req(beto, "/"), v1), 404)
# ---------------------------------------------------------------- propuesta 1: datos propios del tipo documental
import detenidos as det_
fid, _, _ = det_.guardar({"nombre_completo": "Juan Gómez", "dni": "30123456"}, {"nombre": "Mesa"})
m1 = nota("Pedido de informe.docx", ["SUBIDA"])
db.fijar_tipo_documental(m1, "PEDIDO_INFORME", "Ana Pérez")
uni = db.listar_unidades()[0]
texto_m1 = f"Al Director de {uni['nombre']}. Solicito informe sobre Juan Gómez, DNI 30.123.456, en el plazo de 5 días hábiles."
db.indexar_texto(m1, texto_m1)
import agentes; agentes.registrar_entidades(m1, texto_m1)
html = main.ver_nota(req(ana, f"/nota/{m1}"), m1).body
assert "Datos del documento" in html and "Organismo requerido" in html and 'name="md_plazo_dias" value="5"' in html
assert f'name="md_persona_detenida" value="{fid}"' in html and "Juan Gómez" in html and f'<option value="{uni["id"]}" selected>' in html
r = run(R[("POST", "/nota/{nota_id}/metadatos")](req(ana, "/", form={"md_persona_detenida": str(fid), "md_plazo_dias": "5", "md_unidad": str(uni["id"]),
                                                                     "md_fecha_limite": "2026-12-01", "md_organismo_requerido": "Dirección de la Unidad"}), m1))
assert r.status_code == 303
with db._conexion() as con:
    guardado = {f[0]: f[1] for f in con.execute("SELECT campo, valor FROM nota_metadatos WHERE nota_id = ?", (m1,))}
    assert guardado["plazo_dias"] == "5" and guardado["fecha_limite"] == "2026-12-01" and guardado["persona_detenida"] == str(fid)
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion = 'Datos del documento' AND entidad_id = ?", (str(m1),)).fetchone()[0] == 1
assert any(n["id"] == m1 and n["confirmada"] for n in det_.notas_relacionadas(det_.obtener(fid))), "la ficha la lee"
run(R[("POST", "/nota/{nota_id}/metadatos")](req(ana, "/", form={"md_plazo_dias": "cinco"}), m1))
with db._conexion() as con:
    assert con.execute("SELECT valor FROM nota_metadatos WHERE nota_id = ? AND campo = 'plazo_dias'", (m1,)).fetchone()[0] == "5", "lo inválido no se guarda"
run(R[("POST", "/nota/{nota_id}/metadatos")](req(ana, "/", form={"md_persona_detenida": "999999"}), m1))
falla(lambda: R[("POST", "/nota/{nota_id}/metadatos")](req(beto, "/", form={}), m1), 404)
# propuesta 39: la carpeta del caso lee el vínculo confirmado y la unidad confirmada, aunque el texto no los nombre igual
m2 = nota("Sin DNI en el texto.docx", ["SUBIDA"])
from dgpdh.servicios import nota_persona as npx
from dgpdh.seguridad import auditoria as aud_
with db._conexion() as con:
    npx.decidir(con, nota_id=m2, detenido_id=fid, accion="confirmar", actor=aud_.Actor(ana["id"], ana["nombre"]))
    con.execute("DELETE FROM nota_entidades WHERE nota_id = ?", (m1,))
html = R[("GET", "/caso")](req(ana, "/caso"), tipo="PERSONA", norm="dni:30123456", valor="DNI 30123456").body
assert "Sin DNI en el texto.docx" in html and "Pedido de informe.docx" in html
html = R[("GET", "/caso")](req(ana, "/caso"), tipo="UNIDAD", norm=f"u:{uni['id']}", valor=uni["nombre"]).body
assert "Pedido de informe.docx" in html, "unidad confirmada en los datos del documento"
with db._conexion() as con:
    npx.decidir(con, nota_id=m2, detenido_id=fid, accion="descartar", actor=aud_.Actor(ana["id"], ana["nombre"]))
assert "Sin DNI en el texto.docx" not in R[("GET", "/caso")](req(ana, "/caso"), tipo="PERSONA", norm="dni:30123456", valor="x").body
# ---------------------------------------------------------------- propuesta 2: reglas de la Dirección (solo proponen)
html = R[("GET", "/admin/reglas")](req(direc, "/admin/reglas")).body
assert "Crear regla" in html
run(R[("POST", "/admin/reglas")](req(direc, "/", form={"nombre": "Huelga de hambre", "disparador": "SUBIDA", "condicion": "texto_contiene",
                                                     "condicion_valor": "huelga de hambre", "accion": "prioridad", "accion_valor": "urgente"})))
run(R[("POST", "/admin/reglas")](req(direc, "/", form={"nombre": "Mal", "disparador": "SUBIDA", "condicion": "texto_contiene",
                                                     "condicion_valor": "x", "accion": "prioridad", "accion_valor": "ya"})))
from dgpdh.servicios import reglas_propuesta as rps
with db._conexion() as con: reglas_ = rps.todas(con)
assert [r_.nombre for r_ in reglas_] == ["Huelga de hambre"], "la regla inválida no se guarda"
h1 = nota("Huelga.docx", ["SUBIDA"])
assert rutas_notas_ia.evaluar_reglas(h1, "SUBIDA", "La persona inició una HUELGA DE HAMBRE el lunes.") == 1
assert rutas_notas_ia.evaluar_reglas(h1, "SUBIDA", "huelga de hambre") == 0, "una sola vez por nota y regla"
assert rutas_notas_ia.evaluar_reglas(h1, "FIRMA", "huelga de hambre") == 0, "otro disparador"
assert db.obtener_nota(h1)["prioridad"] == "NORMAL", "la regla no cambia la nota sola"
html = main.ver_nota(req(ana, f"/nota/{h1}"), h1).body
assert "Propuestas de las reglas de la Dirección" in html and "Huelga de hambre" in html
sug = db.sugerencias_de(h1, "REGLA")[0]
falla(lambda: R[("POST", "/nota/{nota_id}/regla/{sug_id}")](req(beto, "/", form={"accion": "aceptar"}), h1, sug["id"]), 404)
run(R[("POST", "/nota/{nota_id}/regla/{sug_id}")](req(ana, "/", form={"accion": "aceptar"}), h1, sug["id"]))
assert db.obtener_nota(h1)["prioridad"] == "URGENTE" and not db.sugerencias_de(h1, "REGLA")
with db._conexion() as con:
    assert rps.uso(con)[reglas_[0].id]["ACEPTADA"] == 1
html = R[("GET", "/admin/reglas")](req(direc, "/admin/reglas")).body
assert "1 aceptadas" in html
run(R[("POST", "/admin/reglas")](req(direc, "/", form={"regla_id": str(reglas_[0].id), "accion_form": "pausar"})))
h2 = nota("Otra huelga.docx", ["SUBIDA"])
assert rutas_notas_ia.evaluar_reglas(h2, "SUBIDA", "huelga de hambre") == 0, "pausada no propone"
falla(lambda: R[("GET", "/admin/reglas")](req(ana, "/admin/reglas")), 403)
print("rechazadas: retirar, duplicar, avisos y archivo por inactividad OK")
