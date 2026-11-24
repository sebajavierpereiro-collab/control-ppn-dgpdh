"""Reporte semanal de presentismo para el área de Personal (pantalla y Excel)."""
import sys, types, tempfile, asyncio, inspect, warnings
from datetime import date, timedelta
from pathlib import Path
warnings.simplefilter("ignore")
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)

# ---- stub de fastapi
class HTTPException(Exception):
    def __init__(self, status_code, detail=""): self.status_code, self.detail = status_code, detail
class Resp:
    def __init__(self, content=None, status_code=200, media_type=None, headers=None, url=None):
        self.content, self.status_code, self.headers, self.url = content, status_code, headers or {}, url
class RedirectResponse(Resp):
    def __init__(self, url, status_code=307): super().__init__(None, status_code, url=url)
fa = types.ModuleType("fastapi"); fa.HTTPException = HTTPException; fa.Request = object
fr = types.ModuleType("fastapi.responses"); fr.HTMLResponse = Resp; fr.JSONResponse = lambda c, status_code=200: Resp(c, status_code); fr.RedirectResponse = RedirectResponse; fr.Response = Resp
sys.modules["fastapi"], sys.modules["fastapi.responses"] = fa, fr

import jinja2
import database as db, auth, fechas
import traslados as tr
import rutas_traslados
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"; db.inicializar_db()

class App:
    def __init__(self): self.rutas = {}
    def _reg(self, metodo):
        def deco(path, **kw):
            def w(fn): self.rutas[(metodo, path)] = fn; return fn
            return w
        return deco
    def __getattr__(self, n):
        if n in ("get", "post"): return self._reg(n.upper())
        raise AttributeError(n)
app = App()

class Tpl:
    def __init__(self):
        self.env = jinja2.Environment(loader=jinja2.FileSystemLoader(APP + "/templates"))
        self.env.globals.update(ROLES_DE_EQUIPO=auth.ROLES_DE_EQUIPO, ROLES_MESA=auth.ROLES_MESA, contar_notificaciones=db.contar_notificaciones,
                                static_v="t", tomar_flash=lambda r: None)
        self.env.filters["hora"] = fechas.a_hora_local
        self.env.globals.update(ETIQUETAS_ROL=auth.ETIQUETAS_ROL, es_admin=auth.es_admin)
    def TemplateResponse(self, nombre, ctx):
        class _S: pass
        r = _S(); r.state = _S(); r.state.usuario = ctx.get("usuario"); ctx = dict(ctx, request=r)
        return Resp(self.env.get_template(nombre).render(**ctx))
flashes = []
class Req:
    def __init__(self, usuario, form=None):
        self.usuario, self._form = usuario, form or {}
    async def form(self): return Form(self._form)
class Form(dict):
    def getlist(self, k):
        v = self.get(k, [])
        return v if isinstance(v, list) else [v]
class Archivo:
    filename = "u.csv"
    def __init__(self, b): self.b = b
    async def read(self): return self.b

H = {"u": None}
rutas_traslados.registrar(app, {"templates": Tpl(), "usuario_actual": lambda r: r.usuario, "flash": lambda r, t: flashes.append(t),
                                "tiene_notas": lambda u: bool(u.get("acceso_notas", 1)), "puede_gestionar_nota": lambda u, n: True})
import traslados as _tr

def llamar(metodo, path, usuario, form=None, **kw):
    fn = app.rutas[(metodo, path)]
    req = Req(usuario, form)
    r = fn(req, **kw)
    if inspect.iscoroutine(r): r = asyncio.run(r)
    return r

def prohibido(*a, **k):
    try: llamar(*a, **k)
    except HTTPException as e: return e.status_code
    raise AssertionError("debía dar 403")

eq = db.crear_equipo("Equipo A")
h, s = auth.generar_hash_clave("Clave1234")
ase = db.obtener_usuario(db.crear_usuario("ase", h, s, "Ana Asesora", "ASESOR", eq))
cid = db.crear_usuario("cch", h, s, "Coord Choferes", "ASESOR", eq); db.actualizar_usuario(cid, "Coord Choferes", "ASESOR", eq, coordina_traslados=True)
cch = db.obtener_usuario(cid)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
dele = next(x for x in db.listar_unidades() if x["zona"] == "Delegaciones PPN")
dl = db.obtener_usuario(db.crear_usuario("dl", h, s, "Dora Delegada", "ASESOR", eq))
db.actualizar_usuario(dl["id"], "Dora Delegada", "ASESOR", eq, circuito="DELEGACIONES", delegacion_id=dele["id"]); dl = db.obtener_usuario(dl["id"])
chof = db.obtener_usuario(db.crear_usuario("chof", h, s, "Pedro Chofer", "CHOFER", None, acceso_notas=False))
UN = db.listar_unidades()[0]["id"]
import io as _io, json as _json
import openpyxl
import presentismo
from datetime import date as D, timedelta as TD
lunes = tr.lunes_de(D.today()) + TD(days=7)  # semana próxima
amba = next(x for x in db.listar_unidades() if (x.get("zona") or "") == "AMBA")
per = db.obtener_usuario(db.crear_usuario("per", h, s, "Paula Personal", "ASESOR", eq)); db.actualizar_usuario(per["id"], per["nombre"], "ASESOR", eq, personal=True)
per = db.obtener_usuario(per["id"])
# viajes de la semana: lunes con chofer (Ana + Coord + alguien sin usuario), miércoles por sus propios medios (Ana), uno cancelado (Coord)
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": lunes.isoformat(), "hora_salida": "09:00", "hora_regreso": "15:00", "unidad_id": str(amba["id"]),
            "agentes": [str(ase["id"]), str(cch["id"])], "externos": [_json.dumps({"k": "x", "nombre": "Visitante Externo"})]})
v1 = int(r.url.rsplit("/", 1)[1])
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": (lunes + TD(days=2)).isoformat(), "unidad_id": str(amba["id"]), "agentes": [str(ase["id"])], f"auto_{ase['id']}": "1"})
r = llamar("POST", "/traslados/nuevo", cch, {"fecha": (lunes + TD(days=4)).isoformat(), "unidad_id": str(amba["id"]), "agentes": [str(cch["id"])]})
llamar("POST", "/traslados/{viaje_id:int}/cancelar", cch, {"motivo": "Suspendido"}, viaje_id=int(r.url.rsplit("/", 1)[1]))

d = presentismo.semana(lunes)
ana = next(p for p in d["personas"] if p["persona"] == "Ana Asesora")
assert ana["n_dias"] == 2 and set(ana["dias"]) == {lunes.isoformat(), (lunes + TD(days=2)).isoformat()}
coord = next(p for p in d["personas"] if p["persona"] == "Coord Choferes")
assert coord["n_dias"] == 1, "el viaje cancelado no cuenta"
assert [f["persona"] for f in d["sin_usuario"]] == ["Visitante Externo"]
assert any(f["modalidad"] == "Por sus propios medios" for f in d["detalle"] if f["persona"] == "Ana Asesora")

# permisos: Personal y coordinación sí; el resto no
assert "Personal en viaje" in llamar("GET", "/traslados/presentismo", per, lunes=lunes.isoformat()).content
assert "Personal en viaje" in llamar("GET", "/traslados/presentismo", direc, lunes=lunes.isoformat()).content
assert prohibido("GET", "/traslados/presentismo", ase) == 403 and prohibido("GET", "/traslados/presentismo.xlsx", ase) == 403
assert "Presentismo (Personal)" in llamar("GET", "/traslados", per).content and "Presentismo (Personal)" not in llamar("GET", "/traslados", ase).content
pag = llamar("GET", "/traslados/presentismo", per, lunes=(lunes + TD(days=3)).isoformat()).content  # cualquier día lleva a su semana
assert lunes.strftime("%d/%m/%Y") in pag and "Ana Asesora" in pag

# Excel
x = llamar("GET", "/traslados/presentismo.xlsx", per, lunes=lunes.isoformat())
assert f"presentismo_semana_{lunes.isoformat()}.xlsx" in x.headers["Content-Disposition"]
wb = openpyxl.load_workbook(_io.BytesIO(x.content))
assert wb.sheetnames == ["Semana", "Detalle", "Sin usuario"]
ws = wb["Semana"]
filas = {r[0]: r for r in ws.iter_rows(min_row=5, values_only=True)}
assert filas["Ana Asesora"][2].startswith(amba["nombre"]) and "09:00–15:00" in filas["Ana Asesora"][2] and filas["Ana Asesora"][-1] == 2
assert filas["Coord Choferes"][-1] == 1 and not filas["Coord Choferes"][6]  # viernes cancelado: vacío
det = list(wb["Detalle"].iter_rows(min_row=2, values_only=True))
assert len(det) == 3 and det[0][0].date() == lunes and wb["Detalle"].auto_filter.ref
assert list(wb["Sin usuario"].iter_rows(min_row=2, values_only=True))[0][1] == "Visitante Externo"
open(Path(tempfile.gettempdir()) / "presentismo_prueba.xlsx", "wb").write(x.content)
print("presentismo OK")
print("ALL OK")
