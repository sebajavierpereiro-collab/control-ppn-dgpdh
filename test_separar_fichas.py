"""Propuesta 33: separar fichas de personas detenidas unificadas por error (reactiva, devuelve campos, registra y no se vuelve a proponer)."""
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
import revision_ia as ria
from dgpdh.errores import Conflicto, DatoInvalido

# ---------------------------------------------------------------- unificación con foto y separación completa
a = alta(nombre_completo="Juan Carlos Gómez", dni="30123456", fecha_nacimiento="05/03/1990")
b = alta(nombre_completo="Juan Carlos Gomez", lpu="777111", alias="Juancito", unidad_id=str(CPF1["id"]), modulo="IV")
det.dar_de_baja(a, "Egreso", mesa)
with db._conexion() as con: con.execute("UPDATE detenidos SET fecha_listado = '2026-11-01' WHERE id = ?", (b,))
det.decidir_alerta(det.clave_duplicado(a, b), "unificar", mesa, "", a)   # se conserva la del histórico; b estaba alojada
A, B = det.obtener(a), det.obtener(b)
assert A["activo"] == 1 and A["lpu"] == "777111" and A["alias"] == "Juancito" and A["unidad_id"] == CPF1["id"] and B["motivo_baja"] == f"Unificada con #{a}"
us = det.unificaciones_de(a)
assert len(us) == 1 and us[0].absorbida_id == b and us[0].origen == "SISTEMA" and {"lpu", "alias", "unidad_id", "modulo"} <= set(us[0].campos_tomados)
# después de unificar, alguien corrige el alias: eso no se pisa al separar
i, err, _ = det.guardar(dict(det.obtener(a), alias="El Juan"), mesa, a); assert not err, err

html = R[("GET", "/detenidos/{det_id}")](req(mesa, f"/detenidos/{a}"), a).body
assert "Fichas unificadas con esta" in html and "Separar fichas" in html and f"Ficha #{b}" in html
html = R[("GET", "/detenidos/{det_id}")](req(ana, f"/detenidos/{a}"), a).body
assert "Separar fichas" not in html, "sin el permiso de mantenimiento no se ofrece"
try:
    run(R[("POST", "/detenidos/{det_id}/separar/{unificacion_id}")](req(ana, "/", {"motivo": "son distintas"}), a, us[0].id)); raise SystemExit("ana no puede")
except main.HTTPException as e: assert e.status_code == 403
try:
    det.separar(us[0].id, mesa, "no"); raise SystemExit("motivo obligatorio")
except DatoInvalido: pass
r_ = run(R[("POST", "/detenidos/{det_id}/separar/{unificacion_id}")](req(mesa, "/", {"motivo": "Son hermanos: la unidad confirmó DNI distinto"}), a, us[0].id))
assert r_.status_code == 303
A, B = det.obtener(a), det.obtener(b)
assert B["activo"] == 1 and B["motivo_baja"] is None, "la absorbida vuelve a alojados (estaba alojada)"
assert A["activo"] == 0 and A["motivo_baja"] == "Egreso", "la conservada vuelve al histórico, como estaba"
assert not A["lpu"] and A["lpu_norm"] is None and not A["unidad_id"] and not A["modulo"], "devuelve los campos que había tomado"
assert A["alias"] == "El Juan", "lo que se cambió después de unificar se conserva"
assert A["dni"] == "30123456" and B["lpu"] == "777111"
assert any(h["campo"] == "separación" and "hermanos" in (h["despues"] or "") for h in det.historial(b))
assert any(h["origen"] == "SEPARACION" and h["campo"] == "lpu" for h in det.historial(a))
with db._conexion() as con:
    au = con.execute("SELECT * FROM auditoria WHERE accion = 'Separar fichas'").fetchone()
    assert au and au["entidad_id"] == str(a) and "hermanos" in au["motivo"] and au["usuario"] == mesa["nombre"]
    assert con.execute("SELECT decision FROM detenidos_revision WHERE clave = ?", (det.clave_duplicado(a, b),)).fetchone()[0] == "distintas"
try:
    det.separar(us[0].id, mesa, "otra vez lo mismo"); raise SystemExit("no se separa dos veces")
except Conflicto: pass
assert det.unificaciones_de(a)[0].separada_en and det.unificaciones_de(a)[0].motivo_separacion.startswith("Son hermanos")
# la revisión no vuelve a proponer el par
nucleo_ia = __import__("nucleo"); nucleo_ia.ia_disponible = lambda p: False
ria.revisar("PEDIDO", usuario=mesa)
assert not any({p["det_a"], p["det_b"]} == {a, b} for p in ria.pendientes())
html = R[("GET", "/detenidos/{det_id}")](req(mesa, f"/detenidos/{a}"), a).body
assert "Separada el" in html and "hermanos" in html

# ---------------------------------------------------------------- unificación vieja (sin foto): se reconstruye desde el historial
c = alta(nombre_completo="María Pérez", dni="28999111")
d_ = alta(nombre_completo="María Peres", lpu="555222", fecha_nacimiento="02/01/1980")
det.unificar(c, d_, mesa)
with db._conexion() as con:
    con.execute("DELETE FROM detenidos_unificaciones WHERE mantener_id = ?", (c,))   # simula una unificación anterior al cambio
us = det.unificaciones_de(c)
assert len(us) == 1 and us[0].origen == "RECONSTRUIDA" and set(us[0].campos_tomados) == {"lpu", "fecha_nacimiento"}
assert len(det.unificaciones_de(c)) == 1, "no se reconstruye dos veces"
r = det.separar(us[0].id, mesa, "Distinta fecha de nacimiento confirmada")
C, D = det.obtener(c), det.obtener(d_)
assert not C["lpu"] and not C["fecha_nacimiento"] and set(r.restaurados) == {"lpu", "fecha_nacimiento"}
assert D["activo"] == 0 and "revisar si sigue alojada" in D["motivo_baja"] and not D["motivo_baja"].startswith("Unificada"), \
    "sin foto no se sabe si estaba alojada: queda en el histórico para revisar"
# la ficha absorbida que alguien ya reactivó a mano no se toca
e1 = alta(nombre_completo="Pedro Ruiz", dni="20111222"); e2 = alta(nombre_completo="Pedro Ruis", lpu="999")
det.unificar(e1, e2, mesa); det.reactivar(e2, mesa)
try:
    det.separar(det.unificaciones_de(e1)[0].id, mesa, "son personas distintas"); raise SystemExit("la otra ficha ya no figura como unificada")
except Conflicto: pass
# propuesta 45: datos sensibles tapados (optativo) para quien no mantiene la base
s1 = alta(nombre_completo="Rosa Sensible", dni="27111222", situacion_procesal="Procesada", observaciones="Tratamiento psiquiátrico")
html = R[("GET", "/detenidos/{det_id}")](req(ana, f"/detenidos/{s1}"), s1).body
assert "Procesada" in html and "Tratamiento" in html, "sin la opción se ve como hoy"
det.EXTERNO["ocultar_sensibles"] = True
html = R[("GET", "/detenidos/{det_id}")](req(ana, f"/detenidos/{s1}"), s1).body
assert "Procesada" not in html and "Tratamiento" not in html and "(protegido)" in html and "Ver los datos protegidos" in html
assert R[("GET", "/detenidos/{det_id}/editar")](req(ana, "/"), s1).status_code == 303, "no se edita lo que no se ve"
rq = req(ana, "/", {"motivo": "no"}); run(R[("POST", "/detenidos/{det_id}/ver-sensibles")](rq, s1)); assert not any(k.startswith("sensibles_") for k in rq.session), "motivo obligatorio"
rq = req(ana, "/", {"motivo": "Redacción de la nota del expediente"})
assert run(R[("POST", "/detenidos/{det_id}/ver-sensibles")](rq, s1)).status_code == 303
rg = req(ana, f"/detenidos/{s1}"); rg.session = rq.session
html = R[("GET", "/detenidos/{det_id}")](rg, s1).body
assert "Procesada" in html and "Tratamiento" in html and "(protegido)" not in html
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) FROM seguridad_eventos WHERE tipo = 'DETENIDO_VER_SENSIBLE' AND detalle LIKE ?", (f"#{s1}: Redacción%",)).fetchone()[0] == 1
assert "Procesada" in R[("GET", "/detenidos/{det_id}")](req(mesa, f"/detenidos/{s1}"), s1).body, "quien mantiene la base ve todo"
det.EXTERNO["ocultar_sensibles"] = False

# propuesta 42: el tablero de calidad por fuerza se muestra (solo para quien administra la base)
html = R[("GET", "/detenidos/calidad")](req(mesa, "/detenidos/calidad")).body
assert "Calidad de los listados por fuerza" in html and "Descargar anexo" in html
try: R[("GET", "/detenidos/calidad")](req(ana, "/detenidos/calidad")); raise SystemExit("ana no administra")
except main.HTTPException as e: assert e.status_code == 403
assert R[("GET", "/detenidos/calidad.csv")](req(mesa, "/detenidos/calidad.csv")).media_type.startswith("text/csv")
print("separar fichas unificadas: foto, separación, conservación de cambios posteriores, permiso, auditoría y reconstrucción OK")
