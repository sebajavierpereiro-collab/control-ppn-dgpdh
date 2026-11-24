"""Viajes frecuentes: plantillas, precarga semanal «a confirmar», confirmación, patrones y pantallas."""
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
import frecuentes as fr
from datetime import date as D, timedelta as TD
hoy = D.today()
lunes = tr.lunes_de(hoy)
amba = next(x for x in db.listar_unidades() if (x.get("zona") or "") == "AMBA")
mid = db.crear_usuario("mila", h, s, "Milagros Médica", "ASESOR", eq); mila = db.obtener_usuario(mid)
eq_b = db.crear_equipo("Equipo B")
ajena = db.obtener_usuario(db.crear_usuario("aje", h, s, "Ajena Otra", "ASESOR", eq_b))
coord_a = db.obtener_usuario(db.crear_usuario("coa", h, s, "Coordinadora A", "COORDINADOR", eq))

# ---- 1. calendario de la plantilla
p = {"desde": "2026-10-05", "hasta": None, "dias": "0", "frecuencia": "SEMANAL"}  # lunes 05/10
assert fr.toca(p, "2026-10-12") and not fr.toca(p, "2026-10-13") and not fr.toca(p, "2026-09-28")
assert fr.toca(dict(p, frecuencia="QUINCENAL"), "2026-10-19") and not fr.toca(dict(p, frecuencia="QUINCENAL"), "2026-10-12")
assert fr.toca(dict(p, frecuencia="MENSUAL"), "2026-11-02") and not fr.toca(dict(p, frecuencia="MENSUAL"), "2026-10-12")
assert not fr.toca(dict(p, hasta="2026-10-20"), "2026-10-26")
assert fr.describir(dict(p, dias="0,3")) == "Lunes y Jueves, todas las semanas"
print("1 calendario OK")

# ---- 2. Milagros va todos los lunes (y los viernes): plantilla y precarga «a confirmar»
fid = fr.crear({"circuito": "AMBA", "unidad_id": amba["id"], "dias": [0, 4], "frecuencia": "SEMANAL", "desde": hoy.isoformat(),
                "hora_salida": "09:00", "hora_regreso": "15:00", "motivo": "Atención médica"},
               [{"usuario_id": mila["id"], "nombre": mila["nombre"], "equipo_nombre": "Equipo A"}], mila)
n = fr.generar(hoy=hoy)
desde_v, hasta_v = fr.ventana(hoy)
esperados = [desde_v + TD(days=i) for i in range((hasta_v - desde_v).days + 1) if (desde_v + TD(days=i)).weekday() in (0, 4)]
assert n == len(esperados) >= 1, (n, esperados)
assert fr.generar(hoy=hoy) == 0, "no duplica"
pre = [v for v in tr.listar_viajes(desde=hoy.isoformat()) if v.get("frecuente_id") == fid]
assert all(v["estado"] == "A_CONFIRMAR" and v["hora_salida"] == "09:00" and v["modalidad"] == "CHOFER" for v in pre)
assert not any(v["frecuente_id"] == fid for v in tr.pendientes_de_chofer()), "la coordinación no lo ve hasta que se confirma"
assert any("para confirmar" in nt["texto"] for nt in db.listar_notificaciones(mila["id"])) if hasattr(db, "listar_notificaciones") else True
print("2 precarga OK")

# ---- 3. quién confirma
v0 = pre[0]
assert fr.puede_confirmar(mila, v0) and fr.puede_confirmar(coord_a, v0) and fr.puede_confirmar(cch, v0)
assert not fr.puede_confirmar(ajena, v0) and not fr.puede_confirmar(ase, v0)
try:
    fr.confirmar(v0["id"], ajena); raise AssertionError("no debía poder")
except tr.ErrorTraslado:
    pass
res = tr.resumen(mila, "AMBA", hoy=hoy)
assert any(x["id"] == v0["id"] for x in res["a_confirmar"]) and any("frecuente" in a["texto"] for a in res["alertas"])
assert fr.confirmar(v0["id"], coord_a, hoy=hoy) == "SOLICITADO"
assert any(x["id"] == v0["id"] for x in tr.pendientes_de_chofer())
print("3 confirmación OK")

# ---- 4. sin confirmar no cuenta como realizado ni en estadísticas
pasado = (hoy - TD(days=3)).isoformat()
with db._conexion() as con:
    con.execute("INSERT INTO viajes (fecha, unidad_id, modalidad, estado, origen, circuito, frecuente_id, creado_en, actualizado_en) VALUES (?,?,'CHOFER','A_CONFIRMAR','FRECUENTE','AMBA',?,'x','x')", (pasado, amba["id"], fid))
    vp = con.execute("SELECT id FROM viajes WHERE fecha = ? AND frecuente_id = ?", (pasado, fid)).fetchone()["id"]
    con.execute("INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, equipo_nombre) VALUES (?,?,?,?)", (vp, mila["id"], mila["nombre"], "Equipo A"))
assert tr.obtener_viaje(vp)["estado_efectivo"] == "NO_CONFIRMADO"
est = tr.estadisticas("2020-01-01", hoy.isoformat(), {"tipo": "todos"})
assert all(x["id"] != vp for x in est["viajes_detalle"])
try:
    fr.confirmar(vp, mila, hoy=hoy); raise AssertionError("no se confirma un viaje pasado")
except tr.ErrorTraslado:
    pass
print("4 no confirmados OK")

# ---- 5. «no viaja», pausar, reactivar, fin
assert len(pre) >= 2
if True:
    fr.no_viaja(pre[1]["id"], mila)
    assert tr.obtener_viaje(pre[1]["id"])["estado"] == "CANCELADO"
    assert fr.generar(hoy=hoy) == 0, "una fecha marcada «no viaja» no se regenera"
fr.cambiar_estado(fid, "pausar", mila)
assert not [v for v in tr.listar_viajes(desde=(hoy + TD(days=1)).isoformat()) if v.get("frecuente_id") == fid and v["estado"] == "A_CONFIRMAR"]
assert fr.generar(hoy=hoy) == 0
fr.cambiar_estado(fid, "activar", mila)
fr.fijar_fin(fid, hoy.isoformat(), mila)
assert fr.generar(hoy=hoy) == 0, "con fin hoy ya no genera"
fr.fijar_fin(fid, None, mila)
print("5 pausar / fin OK")

# ---- 6. desde las pantallas
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": (hoy + TD(days=2)).isoformat(), "hora_salida": "10:00", "unidad_id": str(amba["id"]),
                                             "agentes": [str(ase["id"])], "frecuente": "1", "frecuencia": "SEMANAL", "dias": ["1", "3"]})
assert r.status_code == 303 and "frecuente" in flashes[-1], flashes[-1]
vid = int(r.url.rsplit("/", 1)[1])
v = tr.obtener_viaje(vid); assert v["frecuente_id"]
pl = fr.obtener(v["frecuente_id"]); assert pl["dias_lista"] == [1, 3] and pl["hora_salida"] == "10:00"
det = llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=vid).content
assert "Es parte de un <strong>viaje frecuente</strong>" in det and "Martes y Jueves" in det
gen = [x for x in tr.listar_viajes(desde=hoy.isoformat()) if x.get("frecuente_id") == pl["id"] and x["estado"] == "A_CONFIRMAR"]
if gen:
    d2 = llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=gen[0]["id"]).content
    assert "Confirmar viaje" in d2 and "No se viaja esta semana" in d2 and "Asignar chofer" not in llamar("GET", "/traslados/{viaje_id:int}", cch, viaje_id=gen[0]["id"]).content
    t = llamar("GET", "/traslados", ase).content
    assert 'id="para-confirmar"' in t and "Confirmar los marcados" in t
    assert "viaje--a_confirmar" in llamar("GET", "/traslados", ase, ver="agenda").content
    llamar("POST", "/traslados/confirmar", ase, {"viaje_id": [str(x["id"]) for x in gen]})
    assert f"{len(gen)} viaje(s) confirmado(s)" in flashes[-1], flashes[-1]
# convertir un viaje existente desde el detalle
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": (hoy + TD(days=5)).isoformat(), "unidad_id": str(amba["id"]), "agentes": [str(ase["id"])]})
v3 = int(r.url.rsplit("/", 1)[1])
assert "Marcar como viaje frecuente" in llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=v3).content
llamar("POST", "/traslados/{viaje_id:int}/frecuente", ase, {"frecuencia": "QUINCENAL"}, viaje_id=v3)
assert tr.obtener_viaje(v3)["frecuente_id"] and fr.obtener(tr.obtener_viaje(v3)["frecuente_id"])["frecuencia"] == "QUINCENAL"
assert prohibido("POST", "/traslados/{viaje_id:int}/frecuente", ajena, {"frecuencia": "SEMANAL"}, viaje_id=v3) == 403
# repetir: precarga personas, destino y horario
rep = llamar("GET", "/traslados/nuevo", ase, repetir=vid).content
assert "Copiado del viaje" in rep and 'value="10:00"' in rep and f'value="{amba["id"]}" ' in rep and "selected" in rep
# página
pg = llamar("GET", "/traslados/frecuentes", ase).content
assert "Plantillas activas" in pg and "Martes y Jueves" in pg
assert "Próximos generados" in llamar("GET", "/traslados/frecuentes", ase, ver="proximos").content
assert "Milagros" not in llamar("GET", "/traslados/frecuentes", ajena).content, "cada uno ve los suyos"
print("6 pantallas OK")

# ---- 7. sugerencias por patrones: 3 lunes seguidos al mismo destino
otra = next(x for x in db.listar_unidades() if (x.get("zona") or "") == "AMBA" and x["id"] != amba["id"])
for k in (1, 2, 3):
    f = (lunes - TD(weeks=k)).isoformat()
    with db._conexion() as con:
        i = con.execute("INSERT INTO viajes (fecha, unidad_id, modalidad, estado, origen, circuito, creado_por_id, creado_en, actualizado_en) VALUES (?,?,'PROPIO','CONFIRMADO','SISTEMA','AMBA',?,'x','x')", (f, otra["id"], dl["id"])).lastrowid
        con.execute("INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, equipo_nombre, auto_propio) VALUES (?,?,?,?,1)", (i, dl["id"], dl["nombre"], "Equipo A"))
sg = fr.sugerencias(dl, hoy=hoy)
assert sg and sg[0]["unidad_id"] == otra["id"] and sg[0]["dia"] == 0 and sg[0]["veces"] == 3, sg
assert "se realizó 3 veces" in llamar("GET", "/traslados/frecuentes", dl, ver="sugerencias").content
llamar("POST", "/traslados/frecuentes/sugerencia", dl, {"viaje_id": str(sg[0]["viaje_id"]), "dia": "0"})
assert not fr.sugerencias(dl, hoy=hoy), "una vez registrado deja de sugerirse"
print("7 sugerencias OK")
print("ALL OK")
