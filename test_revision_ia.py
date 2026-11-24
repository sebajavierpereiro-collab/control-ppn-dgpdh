"""ABM de personas detenidas: alta, validación, edición con historial, baja, búsqueda, importación, exportación, API y pantallas."""
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
import main, auth
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import detenidos as det
import types, io, csv, asyncio, json, re
from datetime import date, timedelta
import openpyxl
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1 = db.crear_equipo("Equipo Salud")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, mesa, chofer = U("ana", "Ana Pérez", "ASESOR", E1), U("mesa", "Marta Mesa", "MESA_ASESOR"), U("chof", "Carlos Chofer", "CHOFER")
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
CPF1 = next(u for u in db.listar_unidades() if "-CPF I-" in u["nombre"])
hoy = date.today()
"""(arriba: el mismo armado de la app que test_detenidos)  Revisión de la base de detenidos con IA local: niveles, candidatos,
propuestas, decisiones, permiso individual de mantenimiento y disparadores."""
import revision_ia as ria, nucleo, time
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def req(u, p="/", form=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"]) if u else None), url=URL(p), session={}, cookies={},
                                 headers={}, query_params={}, form=_f, client=None)
R = main.app.rutas
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def alta(**k):
    i, err, _ = det.guardar(k, mesa); assert i and not err, (k, err); return i

# ---------------------------------------------------------------- reglas
assert ria._numeros_parecidos("30123456", "30123465") == "dos dígitos invertidos" and ria._numeros_parecidos("30123456", "30123457") == "un dígito distinto"
assert ria._numeros_parecidos("30123456", "30129999") is None and ria._fechas("1990-03-05", "1990-05-03") == "invertida"

a1 = alta(nombre_completo="Juan Carlos Gómez", dni="30123456", fecha_nacimiento="05/03/1990", unidad_id=str(CPF1["id"]))
h1 = alta(nombre_completo="GOMEZ Juan Carlos", dni="30123465", fecha_nacimiento="03/05/1990")      # histórico: DNI con dígitos invertidos
det.dar_de_baja(h1, "Egreso", mesa)
b1 = alta(nombre_completo="María Pérez", dni="28999111", fecha_nacimiento="02/01/1980")
c1 = alta(nombre_completo="Maria Peres", lpu="777111", fecha_nacimiento="02/01/1980")                 # tipeo, sin DNI
d1 = alta(nombre_completo="Pedro López", dni="20111222", fecha_nacimiento="01/01/1970")
e1 = alta(nombre_completo="Pedro López Ruiz", dni="33444555", fecha_nacimiento="09/09/1999")         # distinta persona
f1 = alta(nombre_completo="Ana Torres", dni="25000111")
g1 = alta(nombre_completo="Ana Torres", lpu="555")                                                     # mismo nombre exacto: ya lo ven las reglas fijas
x1 = alta(nombre_completo="Kevin Ramírez Soto", doc_extranjero="1024538298")
x2 = alta(nombre_completo="Kevin Ramirez", lpu="888222")
with db._conexion() as con: con.execute("UPDATE detenidos SET dni = NULL, doc_extranjero = '1024538298', activo = 0, motivo_baja = 'Egreso' WHERE id = ?", (x2,))
u1 = alta(nombre_completo="Juan Carlos Gomes", dni="30123457")
det.dar_de_baja(u1, "Unificada con #1", mesa)                                                          # unificada: no cuenta

assert ria.nivel(det.obtener(a1)) == "alojado" and ria.nivel(det.obtener(h1)) == "historico"
n = ria.contar_niveles(); assert n["alojados"] >= 8 and n["historico"] >= 2, n

pares, comparadas = ria.candidatos([f for f in ria._fichas() if f["activo"]], ria._fichas())
encontrados = {tuple(sorted((a["id"], b["id"]))) for a, b, p, m in pares}
assert tuple(sorted((a1, h1))) in encontrados, "DNI con dígitos invertidos + fecha invertida, contra el histórico"
assert tuple(sorted((b1, c1))) in encontrados, "error de tipeo en el nombre"
assert tuple(sorted((x1, x2))) in encontrados, "el mismo documento extranjero"
assert tuple(sorted((d1, e1))) not in encontrados, "documentos y fechas distintos: no se propone"
assert tuple(sorted((f1, g1))) not in encontrados, "dos alojados con el mismo nombre exacto ya están en las reglas fijas"
assert not any(u1 in par for par in encontrados), "la ficha unificada no cuenta"

# ---------------------------------------------------------------- IA local simulada (no sale nada a la red)
consultas = []
def falsa(proceso, prompt, **k):
    consultas.append((proceso, prompt))
    assert proceso == ria.PROCESO and k.get("formato_json")
    if "Peres" in prompt:
        return '{"veredicto": "misma", "confianza": 90, "motivo": "Mismo nombre con un error de tipeo y la misma fecha de nacimiento."}'
    if "Kevin" in prompt:
        return 'texto basura'
    return '{"veredicto": "dudosa", "confianza": 55, "motivo": "Los DNI difieren en dos dígitos."}'
nucleo.consultar_ollama = falsa
_disp = nucleo.ia_disponible
nucleo.ia_disponible = lambda p: True
r = ria.revisar("PEDIDO", usuario=mesa)
assert r["propuestas"] == len(pares) >= 3 and r["con_ia"] == r["propuestas"] - 1, r   # la respuesta basura no cuenta como opinión
assert consultas and "Ficha A" in consultas[0][1]
props = {(p["det_a"], p["det_b"]): p for p in ria.pendientes()}
p_bc = props[tuple(sorted((b1, c1)))]
assert p_bc["veredicto"] == "misma" and p_bc["confianza"] == 90 and ria.pendientes()[0]["veredicto"] == "misma", "las «misma persona» primero"
p_x = props[tuple(sorted((x1, x2)))]; assert p_x["veredicto"] == "" and p_x["confianza"] is None
p_ah = props[tuple(sorted((a1, h1)))]; assert p_ah["mantener_id"] in (a1, h1)
# la IA no cambió nada
assert det.obtener(c1)["activo"] == 1 and det.obtener(h1)["activo"] == 0
# una segunda corrida no repite propuestas
assert ria.revisar("PEDIDO", usuario=mesa)["propuestas"] == 0

# ---------------------------------------------------------------- panel
al = det.alertas()["ia"]
assert len(al) == len(props) and al[0]["clave"].startswith("ia:")
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), tipo="ia").body
assert "Propuestas de la IA" in html and "Revisar con IA ahora" in html and "IA: Misma persona · 90%" in html and "Histórico" in html
assert "Mismo nombre con un error de tipeo" in html and "Conservar esta" in html and "alojados" in html
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), tipo="ia", ia="misma").body
assert "Maria Peres" in html and "Kevin" not in html, "filtro por opinión de la IA"
assert det.cantidad_alertas() >= len(props)

# aceptar: unifica conservando la ficha elegida
clave_bc = f"ia:{p_bc['id']}"
r_ = run(R[("POST", "/detenidos/revision")](req(mesa, "/detenidos/revision", {"clave": clave_bc, "tipo": "ia", "decision": "unificar", "mantener": str(b1)})))
assert r_.status_code == 303
assert det.obtener(c1)["activo"] == 0 and det.obtener(c1)["motivo_baja"] == f"Unificada con #{b1}" and det.obtener(b1)["lpu"] == "777111"
assert ria.obtener(p_bc["id"])["estado"] == "ACEPTADA" and ria.obtener(p_bc["id"])["decidido_por"] == mesa["nombre"]
# conservar la ficha del histórico: la persona sigue alojada
r_ = run(R[("POST", "/detenidos/revision")](req(mesa, "/detenidos/revision", {"clave": f"ia:{p_ah['id']}", "tipo": "ia", "decision": "unificar", "mantener": str(h1)})))
assert det.obtener(h1)["activo"] == 1 and det.obtener(a1)["activo"] == 0, "la ficha conservada vuelve a alojados"
assert any(c["campo"] == "estado" and "unificación" in (c["despues"] or "") for c in det.historial(h1))
# rechazar: queda como personas distintas y no se vuelve a proponer
r_ = run(R[("POST", "/detenidos/revision")](req(mesa, "/detenidos/revision", {"claves": [f"ia:{p_x['id']}"], "lote": "1", "tipo": "ia", "decision": "distintas"})))
assert ria.obtener(p_x["id"])["estado"] == "RECHAZADA" and det.obtener(x2)["activo"] == 0
try: det.decidir_alerta(f"ia:{p_x['id']}", "unificar", mesa); raise SystemExit("ya estaba decidida")
except ValueError: pass
with db._conexion() as con: con.execute("DELETE FROM detenidos_ia_propuestas")  # aun borrando las propuestas, lo decidido no vuelve
r = ria.revisar("PEDIDO", usuario=mesa)
nuevos = {(p["det_a"], p["det_b"]) for p in ria.pendientes()}
assert tuple(sorted((x1, x2))) not in nuevos and tuple(sorted((b1, c1))) not in nuevos

# sin IA: las reglas igual proponen, sin opinión
nucleo.ia_disponible = lambda p: False
z1 = alta(nombre_completo="Sofía Benítez", dni="40111222"); z2 = alta(nombre_completo="Sofia Benites", dni="40111223")
r = ria.revisar("CARGA #9", ids=[z2])
pz = next(p for p in ria.pendientes() if {p["det_a"], p["det_b"]} == {z1, z2})
assert pz["veredicto"] == "" and pz["origen"] == "CARGA #9" and r["revisadas"] == 1
html = R[("GET", "/detenidos/revision")](req(mesa, "/detenidos/revision"), tipo="ia").body
assert "no está disponible" in html and "Sin opinión de la IA" in html

# ---------------------------------------------------------------- permiso individual «Mantenimiento de bases»
try: R[("GET", "/detenidos/revision")](req(ana, "/detenidos/revision"), tipo="ia"); raise SystemExit("la asesora sin permiso no entra")
except main.HTTPException as e: assert e.status_code == 403
db.actualizar_usuario(ana["id"], ana["nombre"], ana["rol"], ana["equipo_id"], mantiene_bases=True)
ana = db.obtener_usuario(ana["id"]); assert det.puede_mantener(ana) and not det.puede_administrar(ana)
html = R[("GET", "/detenidos/revision")](req(ana, "/detenidos/revision"), tipo="ia").body
assert "Propuestas de la IA" in html
html = R[("GET", "/detenidos")](req(ana, "/detenidos")).body
assert "Revisión de la base" in html and "Importar planilla" not in html and "Exportar Excel" not in html, "mantiene la base pero no importa ni exporta"
try: R[("GET", "/detenidos/importar")](req(ana, "/detenidos/importar")); raise SystemExit("no importa")
except main.HTTPException as e: assert e.status_code == 403
try: R[("GET", "/detenidos/revision")](req(chofer, "/detenidos/revision")); raise SystemExit("el chofer no entra aunque tenga el permiso")
except main.HTTPException: pass
db.actualizar_usuario(chofer["id"], chofer["nombre"], chofer["rol"], None, mantiene_bases=True)
assert not det.puede_mantener(db.obtener_usuario(chofer["id"]))

# ---------------------------------------------------------------- disparadores
r_ = run(R[("POST", "/detenidos/revision/ia")](req(mesa, "/detenidos/revision/ia")))
assert r_.status_code == 303
for _ in range(100):
    if not ria.en_curso(): break
    time.sleep(0.05)
assert ria.corridas(1)[0]["origen"] == "PEDIDO" and ria.corridas(1)[0]["estado"] == "TERMINADA"
import automatico
automatico.en_ventana = lambda *a, **k: True
assert ria.toca_nocturna()
ria.revisar("NOCHE")
assert not ria.toca_nocturna(), "una sola por noche"
automatico.en_ventana = lambda *a, **k: False
with db._conexion() as con: con.execute("DELETE FROM detenidos_ia_corridas WHERE origen = 'NOCHE'")
assert not ria.toca_nocturna(), "fuera de la ventana no corre"
# búsqueda por nivel
html = R[("GET", "/detenidos")](req(mesa, "/detenidos"), apellido="ramirez", nombre="kevin", nivel="historico").body
assert "Kevin Ramirez" in html and "Kevin Ramírez Soto" not in html and "Histórico" in html
html = R[("GET", "/detenidos")](req(mesa, "/detenidos"), apellido="ramirez", nombre="kevin").body
assert "Kevin Ramírez Soto" in html and "Kevin Ramirez</a>" not in html
html = R[("GET", "/detenidos")](req(mesa, "/detenidos"), apellido="ramirez", nombre="kevin", nivel="todos").body
assert "Kevin Ramírez Soto" in html and "Kevin Ramirez</a>" in html
assert "revision_detenidos" in nucleo.PROCESOS_IA

# ---------------------------------------------------------------- rendimiento con una base grande (sintética)
import random
random.seed(1)
ap = ["Gonzalez", "Rodriguez", "Gomez", "Fernandez", "Lopez", "Diaz", "Martinez", "Perez", "Garcia", "Sanchez", "Romero", "Sosa", "Alvarez",
      "Torres", "Ruiz", "Ramirez", "Flores", "Acosta", "Benitez", "Medina", "Suarez", "Herrera", "Aguirre", "Pereyra", "Gutierrez"]
no = ["Juan", "Carlos", "Jose", "Luis", "Miguel", "Jorge", "Diego", "Pablo", "Marcelo", "Sergio", "Ariel", "Matias", "Lucas", "Nicolas", "Hector"]
grande = [{"id": 10**6 + i, "nombre_norm": det.norm_nombre(f"{random.choice(ap)} {random.choice(ap)} {random.choice(no)} {random.choice(no)}"),
           "dni": str(random.randint(20000000, 45000000)), "lpu_norm": str(random.randint(100000, 999999)), "doc_extranjero": None,
           "fecha_nacimiento": f"19{random.randint(60, 99)}-0{random.randint(1, 9)}-1{random.randint(0, 9)}", "nacionalidad": "argentina",
           "unidad_id": None, "activo": 1 if i % 4 else 0} for i in range(15000)]
gemelo = dict(grande[10], id=2 * 10**6, activo=0, nombre_norm=grande[10]["nombre_norm"].replace("a", "e", 1),
              dni=grande[10]["dni"][:-2] + grande[10]["dni"][-1] + grande[10]["dni"][-2])  # tipeo en el nombre y DNI invertido, en el histórico
grande.append(gemelo)
t0 = time.time()
pares_g, comp_g = ria.candidatos([g for g in grande if g["activo"]], grande)
dur = time.time() - t0
print(f"  base sintética de 15.000 fichas: {comp_g} comparaciones, {len(pares_g)} candidatos, {dur:.1f} s")
assert dur < 120, dur
assert any({a["id"], b["id"]} == {grande[10]["id"], gemelo["id"]} for a, b, p, m in pares_g), "encuentra el duplicado plantado"
assert len(pares_g) <= 3, "sin falsos positivos en masa"
print("revisión de detenidos con IA local: niveles, candidatos, propuestas, decisiones, permiso y disparadores OK")
