"""Ola 2: agentes que proponen (plazos, prerrevisión, resumen, proveído, antecedentes, casi iguales, control cruzado, tablero, hoy)."""
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
import asyncio, json, tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"
import main, auth, agentes, automatico, nucleo, analisis
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
nucleo.USAR_IA = False
from docx import Document
def mkdoc(nombre, parrafos):
    d = Document()
    for p in parrafos: d.add_paragraph(p)
    r = tmp / nombre; d.save(r); return str(r)

# ---- reglas puras
lun = date(2026, 10, 5)  # lunes
p = agentes.detectar_plazos("Se solicita informe dentro del plazo de cinco (5) días hábiles y respuesta en el término de 48 horas.", lun)
assert [(x["cantidad"], x["unidad"], x["habiles"]) for x in p] == [(48, "horas", False), (5, "dias", True)], p
assert p[1]["vence"] == "2026-10-12" and p[0]["vence"] == "2026-10-07"
assert agentes.detectar_plazos("en un plazo de 10 días corridos", lun)[0]["vence"] == "2026-10-15"
assert agentes.detectar_plazos("sin plazo alguno") == []
assert agentes.sumar_habiles(date(2026, 10, 9), 1) == date(2026, 10, 12)
assert set(agentes.pedidos_del_texto("solicita atención médica urgente y traslado a otra unidad")) >= {"Atención médica", "Traslado"}
assert agentes.juzgados_del_texto("según lo ordenado por el Juzgado Federal de Lomas de Zamora N° 2, se informa")[0].startswith("Juzgado Federal de Lomas de Zamora")
assert agentes.tipo_por_contenido("Sr. Juan Pérez\nAlojado en el Módulo 3\nLe informamos que…") == "NOTA A PPL"
assert agentes.tipo_por_contenido("Al Sr. Director del Complejo\nS/D\nTengo el agrado…") == "NOTA"
assert agentes.tipo_por_contenido("VISTO la nota… CONSIDERANDO… Cúmplase.") == "PROVEIDO"
r = agentes.resumen_reglas("Al Sr. Director\nS/D\nTengo el agrado de dirigirme a usted, a fin de solicitar que se brinde atención médica al Sr. Pérez alojado en el CPF I. "
                           "Se requiere respuesta en cinco días. Sin otro particular, saludo atentamente.")
assert r and r[0].startswith("A fin de solicitar") and "atención médica" in r[0], r
assert agentes.parecido("uno dos tres cuatro cinco seis siete ocho", "uno dos tres cuatro cinco seis siete ocho") == 1.0
cc = nucleo.agrupar_cambios(nucleo.calcular_diff_resaltado("Los presos piden agua.", "Las personas detenidas piden agua caliente."))
assert [c["tipo"] for c in cc].count("cambio") == 2 and cc[0]["quitado"] == "Los presos" and cc[0]["agregado"] == "Las personas detenidas", cc
assert analisis.prioridad_por_palabras("se encuentra en régimen de aislamiento")[0] == "URGENTE"
print("reglas OK")

# ---- datos
eq = db.crear_equipo("Equipo A")
h, s = auth.generar_hash_clave("Clave1234")
ana = db.obtener_usuario(db.crear_usuario("ana", h, s, "Ana", "ASESOR", eq))
coo = db.obtener_usuario(db.crear_usuario("coo", h, s, "Coord", "COORDINADOR", eq))
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
for u in (ana, coo):
    with db._conexion() as con: con.execute("UPDATE usuarios SET debe_cambiar_clave = 0 WHERE id = ?", (u["id"],))
uid = db.crear_unidad({"nombre": "Complejo Penitenciario Federal I - Ezeiza", "alias": "CPF I", "zona": "AMBA"}) if hasattr(db, "crear_unidad") else None
cuerpo = ["Al Sr. Director del Complejo Penitenciario Federal I", "S/D",
          "Tengo el agrado de dirigirme a usted en el marco del Expediente EP 1234/2025, a fin de solicitar que se brinde atención médica al Sr. PÉREZ, Juan Carlos (DNI 30.123.456), "
          "alojado en el CPF I, quien se encuentra en huelga de hambre desde el 1 de octubre de 2026.",
          "Se solicita informe dentro del plazo de cinco (5) días hábiles.", "Se adjunta copia de la denuncia.", "Sin otro particular, saludo atentamente.",
          "Buenos Aires, 6 de octubre de 2026."]
n1 = db.crear_nota("Nota CPF I salud.docx", eq, mkdoc("n1.docx", cuerpo), usuario_id=ana["id"])
automatico.clasificar_rapido(n1)
nt = db.obtener_nota(n1)
assert nt["tipo"] == "NOTA" and nt["resumen"] and nt["resumen_origen"] == "reglas"
ents = {(e["tipo"], e["valor"]) for e in db.entidades_de(n1)}
assert ("PEDIDO", "Atención médica") in ents and ("PEDIDO", "Huelga de hambre") in ents and any(t == "PERSONA" for t, _ in ents), ents
plz = db.sugerencias_de(n1, "PLAZO"); assert len(plz) == 1 and plz[0]["contenido"]["cantidad"] == 5
hz = db.listar_hallazgos(n1); datos = {x["dato"] for x in hz}
assert "Anexos mencionados" in datos and all(x["origen"] == "PRERREVISION" for x in hz), datos
assert db.alertas_abiertas(n1) == len(hz)
# la consistencia (otro grupo) no borra la prerrevisión
db.reemplazar_hallazgos(n1, None, [])
assert len(db.listar_hallazgos(n1)) == len(hz)
# placeholder y destinatario faltante
n_mal = db.crear_nota("Nota borrador.docx", eq, mkdoc("nm.docx", ["Se informa que el Sr. XXXX fue trasladado el [fecha]. " * 3]), usuario_id=ana["id"])
automatico.clasificar_rapido(n_mal)
dm = {x["dato"] for x in db.listar_hallazgos(n_mal)}
assert "Campo sin completar" in dm and "Destinatario" in dm, dm
print("prerrevisión, plazos, datos y resumen OK")

# ---- antecedentes y casi iguales
n2 = db.crear_nota("Nota CPF I reiteración.docx", eq, mkdoc("n2.docx", cuerpo[:3] + ["Se reitera el pedido."]), usuario_id=ana["id"])
automatico.clasificar_rapido(n2)
ant = db.antecedentes(n2)
assert ant and ant[0]["id"] == n1 and ant[0]["por_persona"], ant
dup = db.sugerencias_de(n2, "DUPLICADO"); assert dup and dup[0]["contenido"]["nota_id"] == n1 and dup[0]["contenido"]["parecido"] >= 60
print("antecedentes y casi iguales OK")

# ---- rutas: aceptar el plazo, seguimiento y recordatorio
class URL: path = "/"; query = ""
def req(u, form=None):
    async def _f(): return form or {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(), session={}, cookies={}, headers={}, form=_f,
                                 client=types.SimpleNamespace(host="1"))
tok = db.USUARIO_ACTUAL.set(coo)
r = main.resolver_sugerencia_ia(req(coo), plz[0]["id"], "aceptar")
assert r.status_code == 303 and json.loads(db.obtener_nota(n1)["plazo_respuesta"])["cantidad"] == 5
assert db.obtener_sugerencia(plz[0]["id"])["estado"] == "ACEPTADA"
main.resolver_sugerencia_ia(req(coo), dup[0]["id"], "descartar")
assert db.obtener_sugerencia(dup[0]["id"])["estado"] == "DESCARTADA"
db.USUARIO_ACTUAL.reset(tok)
for e in ("PROCESANDO", "PROCESADA", "FIRMADA", "DESPACHADA"): db.actualizar_estado(n1, e)
with db._conexion() as con: con.execute("UPDATE historial_estados SET fecha = '2026-10-05T12:00:00+00:00' WHERE nota_id = ? AND estado = 'DESPACHADA'", (n1,))
assert db.seguimiento_respuestas(hoy=date(2026, 10, 6)) == [] and db.obtener_nota(n1)["respuesta_vence"] == "2026-10-12"
assert db.seguimiento_respuestas(hoy=date(2026, 10, 11)) == [n1] and db.seguimiento_respuestas(hoy=date(2026, 10, 12)) == []
assert any("plazo de respuesta" in x["texto"] for x in db.listar_notificaciones(ana["id"], 10))
n3 = db.crear_nota("Nota vence.docx", eq, mkdoc("n3.docx", ["Al Sr. Juez", "texto"]), usuario_id=ana["id"])
db.fijar_vencimiento(n3, date.today().isoformat())
assert n3 in db.recordar_por_vencer() and n3 not in db.recordar_por_vencer()
print("plazo de respuesta y recordatorios OK")

# ---- control cruzado entre documentos del mismo caso
a = {"id": 1, "nombre": "nota.docx", "texto": "Sr. Juan Pérez\nAlojado en el Módulo 2 del CPF I\nLe informamos que se reclamó atención médica."}
b = {"id": 2, "nombre": "proveido.docx", "texto": "VISTO… respecto de GÓMEZ, Carlos, alojado en el CPF I. Cúmplase."}
hs = agentes.cruce_documentos(a, b)
assert any(x["dato"] == "Persona destinataria" for x in hs), hs
print("control cruzado OK")

# ---- proveído propuesto (sin IA), descarga y registro
html = main.ver_proveido(req(coo), n1, "MANO").body
assert "Proveído propuesto" in html and "entrega en mano" in html and "Notifíquese" in html
res = asyncio.run(main.usar_proveido(req(coo, {"texto": "Texto del proveído", "medio": "MANO", "origen": "reglas", "accion": "usar"}), n1))
assert res.status_code == 200 and str(res.path).endswith(".docx")
asyncio.run(main.usar_proveido(req(coo, {"texto": "x", "medio": "CORREO", "origen": "reglas", "accion": "descartar"}), n1))
tab = {t["agente"]: t for t in db.tablero_agentes()}
assert tab["PROVEIDO"]["aceptadas"] == 1 and tab["PROVEIDO"]["descartadas"] == 1 and tab["PLAZO"]["aceptadas"] == 1
assert tab["PRERREVISION"]["propuestas"] >= 2 and "RESUMEN" in tab
print("proveído y tablero OK")

# ---- bug corregido: decisiones «modificar» alimentan el aprendizaje y el reporte
db.registrar_decision_director(n1, "body:0", "a", "b", "c", "modificar", ["diccionario"])
assert len(db.obtener_modificaciones()) == 1
import reportes
assert reportes.generar_reporte()["notas_modificadas"] == 1
print("aprendizaje (modificar) OK")

# ---- hoy, panel de la nota, política e inicio
hoy = agentes.lista_hoy(coo, [dict(db.obtener_nota(n2), prioridad_efectiva="URGENTE", hallazgos_abiertos=1)], {n2},
                        [{"texto": "Viaje a CPF I", "motivo": "Hoy", "url": "/traslados/1"}])
assert hoy[0]["url"] == f"/nota/{n2}" and hoy[0]["motivo"] == "Urgente" and hoy[-1]["tipo"] == "viaje"
asi = main._asistentes_de(db.obtener_nota(n2))
assert asi["antecedentes"] and asi["resumen"] and asi["destino"]["destinatario"].startswith("Al Sr. Director")
html = main.templates.env.get_template("_asistentes.html").render(nota=db.obtener_nota(n2), request=req(coo))
assert "Antecedentes" in html and "Proponer: entrega en mano" in html and "Resumen" in html
assert "La IA propone" not in main.politica_ia(req(coo)).body and "una persona decide" in main.politica_ia(req(coo)).body
agentes.sembrar_glosario(); assert any(t["termino"] == "presos" for t in db.listar_terminos())
assert agentes.sugerencias_estilo("Los presos reclaman.")[0]["preferido"].startswith("personas privadas")
hi = main.pagina_inicio(req(direc)).body
assert "Hoy necesita tu atención" in hi
print("hoy, panel, política e inicio OK")
print("ALL OK")
